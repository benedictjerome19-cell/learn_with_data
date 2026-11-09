"""
Full-Stack Learning & Consistency Pulse Application.
Enterprise-grade Flask application featuring:
- Role-based Werkzeug salted password authentication & session management
- CSRF protection, login throttling and security headers
- Advanced Pandas & NumPy rolling moving averages (7-Day vs 30-Day momentum)
- GitHub-style 52-week contribution heatmap matrix API
- Multi-format drag-and-drop bulk import (CSV, XLSX, JSON) & manual logging/editing
- Real-time background synchronization to 'python_summary.xlsx' with formulas
- One-click Excel and ReportLab PDF executive export engines
- Admin overview of users, roles and sync audit trail
- RESTful APIs and PWA support
"""

import os
import json
import hmac
import secrets
import functools
import threading
import time
from datetime import datetime
from urllib.parse import urlparse

import pandas as pd
from flask import (
    Flask, render_template, request, jsonify, session, redirect,
    url_for, send_file, flash, g, abort
)
from werkzeug.exceptions import HTTPException

from config import Config
from database import (
    init_db, authenticate_user, create_user, get_user_by_id,
    add_learning_log, get_user_logs, count_user_logs, delete_learning_log,
    update_learning_log, get_learning_log, bulk_insert_logs, get_last_sync_info,
    change_password, list_users_with_stats, update_user_role, count_admins,
    get_recent_sync_audits, VALID_ROLES
)
from validators import (
    validate_log_payload, validate_username, validate_email, validate_password,
    parse_date, clean_text, MAX_LANGUAGE_LEN, MAX_TOPIC_LEN, MAX_NOTES_LEN, MAX_CONCEPTS
)
from analytics import compute_user_analytics
from excel_exporter import sync_to_excel, trigger_background_excel_sync, build_workbook_bytes, ExcelSyncError
from pdf_exporter import generate_pdf_report

app = Flask(__name__)
app.config.from_object(Config)

# Initialize database schema and demo seed dataset
with app.app_context():
    init_db()
    # Trigger initial Excel summary generation on startup
    if not os.path.exists(Config.EXCEL_SYNC_PATH):
        try:
            sync_to_excel()
        except Exception as e:
            app.logger.warning(f"Initial Excel sync deferred: {e}")

# ==============================================================================
# SECURITY: CSRF, LOGIN THROTTLING, HEADERS
# ==============================================================================

CSRF_EXEMPT_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


app.jinja_env.globals["csrf_token"] = get_csrf_token


def wants_json():
    return request.path.startswith("/api/") or request.is_json or \
        request.accept_mimetypes.best == "application/json"


@app.before_request
def csrf_protect():
    """Rejects state-changing requests that lack the per-session CSRF token."""
    if request.method in CSRF_EXEMPT_METHODS:
        return None
    expected = session.get("_csrf_token")
    sent = request.headers.get("X-CSRFToken") or request.form.get("csrf_token")
    if expected and sent and hmac.compare_digest(expected, sent):
        return None
    msg = "Your session token expired. Please refresh the page and try again."
    if wants_json():
        return jsonify({"success": False, "error": msg}), 400
    flash(msg, "error")
    return redirect(url_for("register" if request.path == "/register" else "login"))


class LoginThrottle:
    """In-memory failed-login counter keyed by username + client IP."""

    def __init__(self, max_attempts, window_seconds):
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._failures = {}
        self._lock = threading.Lock()

    def _recent(self, key, now):
        return [t for t in self._failures.get(key, []) if now - t < self.window]

    def retry_after(self, key):
        now = time.time()
        with self._lock:
            attempts = self._recent(key, now)
            self._failures[key] = attempts
            if len(attempts) >= self.max_attempts:
                return int(self.window - (now - attempts[0])) + 1
        return 0

    def fail(self, key):
        now = time.time()
        with self._lock:
            attempts = self._recent(key, now)
            attempts.append(now)
            self._failures[key] = attempts

    def reset(self, key):
        with self._lock:
            self._failures.pop(key, None)


login_throttle = LoginThrottle(Config.LOGIN_MAX_ATTEMPTS, Config.LOGIN_LOCKOUT_MINUTES * 60)


@app.after_request
def set_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.path.startswith("/api/") or request.path.startswith("/export/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def is_safe_next_url(target):
    """Only allow redirects to local paths (blocks //evil.com and absolute URLs)."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return False
    parsed = urlparse(target)
    return not parsed.scheme and not parsed.netloc


def start_user_session(user):
    session.clear()
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['role'] = user['role']
    session.permanent = True
    get_csrf_token()


# ==============================================================================
# AUTHENTICATION & SECURITY DECORATORS
# ==============================================================================

def login_required(view):
    """Ensures user is authenticated with an active session."""
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if not g.user:
            session.pop('user_id', None)
            if wants_json():
                return jsonify({"error": "Unauthorized", "message": "Session expired or not logged in"}), 401
            return redirect(url_for('login', next=request.path))
        return view(**kwargs)
    return wrapped_view


def role_required(allowed_roles):
    """Enforces role-based permissions (admin, mentor, learner)."""
    def decorator(view):
        @functools.wraps(view)
        @login_required
        def wrapped_view(**kwargs):
            if g.user.get('role') not in allowed_roles:
                if wants_json():
                    return jsonify({"error": "Forbidden", "message": "Insufficient permissions for this action"}), 403
                abort(403)
            return view(**kwargs)
        return wrapped_view
    return decorator


@app.before_request
def load_logged_in_user():
    """Populates global user object before each request if session exists."""
    user_id = session.get('user_id')
    g.user = get_user_by_id(user_id) if user_id is not None else None


def request_data():
    """Returns JSON body or form fields as a dict (never raises on bad bodies)."""
    if request.is_json:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}
    return request.form.to_dict()


# ==============================================================================
# AUTHENTICATION ROUTES
# ==============================================================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    """User authentication with Werkzeug password hashing and brute-force throttling."""
    next_url = request.args.get('next')
    if request.method == 'POST':
        data = request_data()
        username = str(data.get('username', '')).strip()
        password = str(data.get('password', ''))
        throttle_key = f"{username.lower()}|{request.remote_addr}"

        wait = login_throttle.retry_after(throttle_key)
        if wait:
            msg = f"Too many failed attempts. Try again in {max(1, wait // 60)} minute(s)."
            if request.is_json:
                return jsonify({"success": False, "error": msg}), 429
            flash(msg, "error")
            return render_template('login.html', register_mode=False), 429

        user = authenticate_user(username, password)
        if user:
            login_throttle.reset(throttle_key)
            start_user_session(user)
            if request.is_json:
                return jsonify({"success": True, "csrf_token": get_csrf_token(), "user": {
                    "id": user['id'],
                    "username": user['username'],
                    "role": user['role'],
                    "avatar_color": user['avatar_color']
                }})
            return redirect(next_url if is_safe_next_url(next_url) else url_for('index'))

        login_throttle.fail(throttle_key)
        msg = "Invalid username/email or password."
        if request.is_json:
            return jsonify({"success": False, "error": msg}), 401
        flash(msg, "error")
        return render_template('login.html', register_mode=False, username=username), 401

    # If already logged in, redirect to home
    if g.user:
        return redirect(url_for('index'))

    return render_template('login.html', register_mode=False)


@app.route('/register', methods=['GET', 'POST'])
def register():
    """Self-service registration. New accounts are always learners; admins assign other roles."""
    if request.method == 'POST':
        data = request_data()
        raw_username = str(data.get('username', ''))
        raw_email = str(data.get('email', ''))
        password = str(data.get('password', ''))
        confirm = data.get('confirm_password')

        def fail(msg):
            if request.is_json:
                return jsonify({"success": False, "error": msg}), 400
            flash(msg, "error")
            return render_template('login.html', register_mode=True,
                                   reg_username=raw_username.strip(), reg_email=raw_email.strip()), 400

        username, err = validate_username(raw_username)
        if err:
            return fail(err)
        email, err = validate_email(raw_email)
        if err:
            return fail(err)
        _, err = validate_password(password)
        if err:
            return fail(err)
        if confirm is not None and str(confirm) != password:
            return fail("Passwords do not match.")

        user_id, error = create_user(username, email, password, role="learner")
        if error:
            return fail(error)

        user = get_user_by_id(user_id)
        start_user_session(user)

        if request.is_json:
            return jsonify({"success": True, "csrf_token": get_csrf_token(), "user": user})
        flash("Welcome! Your account is ready. Log your first learning session below.", "success")
        return redirect(url_for('index'))

    if g.user:
        return redirect(url_for('index'))
    return render_template('login.html', register_mode=True)


@app.route('/logout', methods=['POST'])
def logout():
    """Destroys current session."""
    session.clear()
    if request.is_json:
        return jsonify({"success": True, "message": "Logged out successfully"})
    flash("You have been signed out.", "info")
    return redirect(url_for('login'))


@app.route('/api/session-status')
def session_status():
    """Returns active session status and remaining timeout info."""
    if not g.user:
        return jsonify({"authenticated": False})

    return jsonify({
        "authenticated": True,
        "user": {
            "id": g.user['id'],
            "username": g.user['username'],
            "email": g.user['email'],
            "role": g.user['role'],
            "avatar_color": g.user['avatar_color']
        },
        "csrf_token": get_csrf_token(),
        "timeout_minutes": Config.SESSION_TIMEOUT_MINUTES
    })


@app.route('/api/account/password', methods=['POST'])
@login_required
def api_change_password():
    data = request_data()
    current = str(data.get('current_password', ''))
    new = str(data.get('new_password', ''))
    _, err = validate_password(new)
    if err:
        return jsonify({"success": False, "error": err}), 400
    if current == new:
        return jsonify({"success": False, "error": "New password must be different from the current one."}), 400
    ok, err = change_password(g.user['id'], current, new)
    if not ok:
        return jsonify({"success": False, "error": err}), 400
    return jsonify({"success": True, "message": "Password updated successfully."})


# ==============================================================================
# MAIN DASHBOARD VIEW
# ==============================================================================

@app.route('/')
@login_required
def index():
    """Main Progressive Web App dashboard."""
    last_sync = get_last_sync_info(g.user['id'])
    return render_template(
        'index.html',
        user=g.user,
        last_sync=last_sync,
        session_timeout_minutes=Config.SESSION_TIMEOUT_MINUTES,
        max_upload_mb=Config.MAX_CONTENT_LENGTH // (1024 * 1024),
        today=datetime.now().strftime('%Y-%m-%d'),
    )

# ==============================================================================
# DATA & ANALYTICS APIs
# ==============================================================================

def int_arg(name, default, minimum, maximum):
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


@app.route('/api/stats')
@login_required
def api_stats():
    """
    Computes rolling improvement metrics (Weekly vs Monthly moving averages),
    52-week contribution matrix, and language breakdown with optional custom filtering.
    """
    selected_lang = request.args.get('language', 'all').strip()[:MAX_LANGUAGE_LEN] or 'all'
    analytics_payload = compute_user_analytics(g.user['id'], selected_language=selected_lang)
    return jsonify(analytics_payload)


@app.route('/api/logs', methods=['GET'])
@login_required
def api_get_logs():
    """Retrieves logs with optional language filtering, text search and paging."""
    language = request.args.get('language', 'all').strip()[:MAX_LANGUAGE_LEN] or 'all'
    search = request.args.get('q', '').strip()[:100]
    limit = int_arg('limit', 150, 1, 1000)
    offset = int_arg('offset', 0, 0, 10_000_000)
    logs = get_user_logs(g.user['id'], language=language, limit=limit, offset=offset, search=search)
    total = count_user_logs(g.user['id'], language=language, search=search)
    return jsonify({"logs": logs, "count": len(logs), "total": total, "limit": limit, "offset": offset})


@app.route('/api/logs/<int:log_id>', methods=['GET'])
@login_required
def api_get_log(log_id):
    log = get_learning_log(log_id, g.user['id'])
    if not log:
        return jsonify({"success": False, "error": "Log not found"}), 404
    return jsonify({"success": True, "log": log})


@app.route('/api/log', methods=['POST'])
@login_required
def api_add_log():
    """
    Manual log entry endpoint with server-side validation.
    Triggers background synchronization to 'python_summary.xlsx'.
    """
    clean, err = validate_log_payload(request.get_json(silent=True))
    if err:
        return jsonify({"success": False, "error": err}), 400

    log_id = add_learning_log(user_id=g.user['id'], source="manual", **clean)

    # Real-time background sync to python_summary.xlsx
    trigger_background_excel_sync(user_id=g.user['id'])

    return jsonify({
        "success": True,
        "log_id": log_id,
        "message": "Learning session logged and synced to Excel in background!"
    }), 201


@app.route('/api/logs/<int:log_id>', methods=['PUT'])
@login_required
def api_update_log(log_id):
    """Edits an existing learning log owned by the current user."""
    clean, err = validate_log_payload(request.get_json(silent=True))
    if err:
        return jsonify({"success": False, "error": err}), 400
    if not update_learning_log(log_id, g.user['id'], clean):
        return jsonify({"success": False, "error": "Log not found or unauthorized"}), 404
    trigger_background_excel_sync(user_id=g.user['id'])
    return jsonify({"success": True, "log_id": log_id, "message": "Learning session updated."})


@app.route('/api/logs/<int:log_id>', methods=['DELETE'])
@login_required
def api_delete_log(log_id):
    """Deletes a learning log entry and refreshes background sync."""
    deleted = delete_learning_log(log_id, g.user['id'])
    if deleted:
        trigger_background_excel_sync(user_id=g.user['id'])
        return jsonify({"success": True, "message": "Log deleted successfully"})
    return jsonify({"success": False, "error": "Log not found or unauthorized"}), 404

# ==============================================================================
# MULTI-FORMAT DRAG-AND-DROP BULK IMPORT
# ==============================================================================

@app.route('/api/import', methods=['POST'])
@login_required
def api_bulk_import():
    """
    Drag-and-drop bulk import handling CSV, Excel (.xlsx, .xls), and JSON files.
    Features robust column mapping, row validation, atomic database insertion,
    and background Excel synchronization.
    """
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file uploaded."}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({"success": False, "error": "No file selected."}), 400

    display_name = os.path.basename(file.filename)[:120]
    filename = display_name.lower()
    ext = filename.rsplit('.', 1)[-1] if '.' in filename else ''

    if ext not in Config.ALLOWED_EXTENSIONS:
        return jsonify({
            "success": False,
            "error": f"Unsupported format (.{ext[:10]}). Please upload CSV, XLSX, XLS, or JSON."
        }), 400

    try:
        if ext == 'csv':
            df = pd.read_csv(file, dtype=str, keep_default_na=False, encoding_errors='replace')
        elif ext in ['xlsx', 'xls']:
            df = pd.read_excel(file)
        else:
            content = json.load(file)
            if isinstance(content, dict):
                # Check if wrapped in array key like 'logs', 'data', 'records'
                for key in ['logs', 'data', 'records', 'items']:
                    if key in content and isinstance(content[key], list):
                        content = content[key]
                        break
                if isinstance(content, dict):
                    content = [content]
            if not isinstance(content, list) or not all(isinstance(item, dict) for item in content):
                return jsonify({"success": False, "error": "JSON must be an object or a list of objects."}), 400
            df = pd.DataFrame(content)
    except ImportError:
        return jsonify({"success": False, "error": "Reading legacy .xls files requires the 'xlrd' package. Save the file as .xlsx or install xlrd."}), 400
    except Exception as e:
        app.logger.info(f"Import parse failure: {e}")
        return jsonify({"success": False, "error": f"Could not read {ext.upper()} file. Check that it is a valid, non-corrupted file."}), 400

    if len(df) > Config.MAX_IMPORT_ROWS:
        return jsonify({"success": False, "error": f"File has {len(df)} rows; the limit is {Config.MAX_IMPORT_ROWS} per import."}), 400

    parsed_records, skipped = extract_records_from_dataframe(df)

    if not parsed_records:
        detail = f" {skipped[0]}" if skipped else ""
        return jsonify({"success": False, "error": f"No valid data rows found in uploaded file.{detail}"}), 400

    try:
        inserted_count = bulk_insert_logs(g.user['id'], parsed_records, source=f"{ext}_import")
    except Exception:
        app.logger.exception("Bulk import transaction failed")
        return jsonify({"success": False, "error": "Database error while saving the import. No rows were saved."}), 500

    trigger_background_excel_sync(user_id=g.user['id'])

    message = f"Imported {inserted_count} learning entr{'y' if inserted_count == 1 else 'ies'} from {display_name}."
    if skipped:
        message += f" Skipped {len(skipped)} invalid row{'s' if len(skipped) != 1 else ''}."
    return jsonify({
        "success": True,
        "count": inserted_count,
        "skipped": len(skipped),
        "skipped_details": skipped[:20],
        "message": message
    })


def _cell(row, col_map, key):
    """Returns the cell value for a mapped column, or None when missing/blank/NaN."""
    col = col_map.get(key)
    if col is None:
        return None
    value = row.get(col)
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, str) and (not value.strip() or value.strip().lower() == 'nan'):
        return None
    return value


def extract_records_from_dataframe(df):
    """
    Normalizes DataFrame columns to match the learning_logs schema.
    Supports flexible aliases across various external tools.
    Returns (records, skipped_messages). Rows with an unreadable date or
    out-of-range numbers are skipped rather than silently altered.
    """
    if df.empty:
        return [], []

    aliases = {
        'date': ['date', 'timestamp', 'session_date', 'day'],
        'language': ['language', 'lang', 'stack', 'technology', 'skill', 'category'],
        'topic': ['topic', 'concept', 'title', 'description', 'subject'],
        'concepts_count': ['concepts_count', 'concepts', 'count', 'concept_count', 'items'],
        'hours_spent': ['hours_spent', 'hours', 'duration', 'time_spent', 'time'],
        'mastery_score': ['mastery_score', 'mastery', 'score', 'rating', 'level'],
        'notes': ['notes', 'note', 'summary', 'comment', 'details'],
    }
    col_map = {}
    for col in df.columns:
        c_low = str(col).strip().lower().replace(' ', '_').replace('-', '_')
        for field, names in aliases.items():
            if c_low in names and field not in col_map:
                col_map[field] = col
                break

    if 'topic' not in col_map and 'hours_spent' not in col_map and 'date' not in col_map:
        return [], ["No recognizable columns. Expected headers like date, language, topic, hours_spent."]

    today_str = datetime.now().strftime('%Y-%m-%d')
    records, skipped = [], []

    for position, (_, row) in enumerate(df.iterrows()):
        line = position + 2  # header is line 1 in spreadsheets/CSV

        def skip(reason):
            skipped.append(f"Row {line}: {reason}")

        # Date: missing column/blank cell -> today; unreadable value -> skip row
        date_raw = _cell(row, col_map, 'date')
        if date_raw is None:
            date_clean = today_str
        else:
            try:
                parsed = pd.to_datetime(date_raw)
                if pd.isna(parsed):
                    raise ValueError
            except (ValueError, TypeError, OverflowError):
                skip(f"unreadable date '{str(date_raw)[:30]}'")
                continue
            date_clean, err = parse_date(parsed.to_pydatetime())
            if err:
                skip(err)
                continue

        lang = clean_text(_cell(row, col_map, 'language'), MAX_LANGUAGE_LEN) or 'General'
        topic = clean_text(_cell(row, col_map, 'topic'), MAX_TOPIC_LEN) or 'Learning Activity'
        notes = clean_text(_cell(row, col_map, 'notes'), MAX_NOTES_LEN)

        def number(key, default, cast, low, high, label):
            raw = _cell(row, col_map, key)
            if raw is None:
                return default
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{label} '{str(raw)[:20]}' is not a number")
            if value != value or not low <= value <= high:
                raise ValueError(f"{label} {raw} is outside {low}-{high}")
            return int(round(value)) if cast is int else round(value, 2)

        try:
            concepts = number('concepts_count', 1, int, 1, MAX_CONCEPTS, "concepts")
            hours = number('hours_spent', 1.0, float, 0.1, 24, "hours")
            mastery = number('mastery_score', 3, int, 1, 5, "mastery")
        except ValueError as exc:
            skip(str(exc))
            continue

        records.append({
            "date": date_clean,
            "language": lang,
            "topic": topic,
            "concepts_count": concepts,
            "hours_spent": hours,
            "mastery_score": mastery,
            "notes": notes
        })

    return records, skipped

# ==============================================================================
# EXPORT & CLOUD SYNC ENDPOINTS
# ==============================================================================

@app.route('/export/excel')
@login_required
def export_excel():
    """
    Downloads the professionally styled workbook with auto-formatted headers,
    dynamic Excel formulas, and summary sheets. Built in memory per request.
    """
    try:
        buffer, _ = build_workbook_bytes(g.user['id'])
    except Exception:
        app.logger.exception("Excel export failed")
        return render_error_page(500, "Excel export failed. Please try again.")
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"python_summary_{g.user['username']}_{datetime.now().strftime('%Y%m%d')}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.route('/export/pdf')
@login_required
def export_pdf():
    """
    Generates and downloads an executive PDF summary report
    with KPI scorecards, rolling moving averages, and language breakdown.
    """
    try:
        analytics_data = compute_user_analytics(g.user['id'], selected_language='all')
        recent_logs = get_user_logs(g.user['id'], limit=50)
        pdf_buffer = generate_pdf_report(g.user, analytics_data, recent_logs)
    except Exception:
        app.logger.exception("PDF export failed")
        return render_error_page(500, "PDF report generation failed. Please try again.")

    filename = f"pulse_report_{g.user['username']}_{datetime.now().strftime('%Y%m%d')}.pdf"
    return send_file(pdf_buffer, as_attachment=True, download_name=filename, mimetype="application/pdf")


@app.route('/api/sync/now', methods=['POST'])
@login_required
def api_sync_now():
    """Triggers an explicit immediate synchronization with 'python_summary.xlsx'."""
    try:
        target_path, records_count = sync_to_excel(user_id=g.user['id'])
    except ExcelSyncError as e:
        return jsonify({"success": False, "error": str(e)}), 409
    except Exception:
        app.logger.exception("Manual Excel sync failed")
        return jsonify({"success": False, "error": "Excel sync failed. Please try again."}), 500
    audit = get_last_sync_info(g.user['id'])
    file_name = os.path.basename(target_path)
    return jsonify({
        "success": True,
        "records_count": records_count,
        "file_name": file_name,
        "timestamp": audit.get('created_at') if audit else datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "message": f"Synchronized {records_count} records into '{file_name}'."
    })


@app.route('/api/sync/status')
@login_required
def api_sync_status():
    """Returns the latest sync timestamp and file status."""
    audit = get_last_sync_info(g.user['id'])
    file_exists = os.path.exists(Config.EXCEL_SYNC_PATH)
    file_size_kb = round(os.path.getsize(Config.EXCEL_SYNC_PATH) / 1024, 1) if file_exists else 0

    return jsonify({
        "last_sync": audit,
        "file_exists": file_exists,
        "file_size_kb": file_size_kb,
        "file_name": os.path.basename(Config.EXCEL_SYNC_PATH)
    })

# ==============================================================================
# ADMIN
# ==============================================================================

@app.route('/api/admin/overview')
@role_required(['admin'])
def api_admin_overview():
    return jsonify({
        "users": list_users_with_stats(),
        "audits": get_recent_sync_audits(limit=25),
        "roles": list(VALID_ROLES)
    })


@app.route('/api/admin/users/<int:user_id>/role', methods=['PUT'])
@role_required(['admin'])
def api_admin_set_role(user_id):
    role = str(request_data().get('role', '')).strip().lower()
    if role not in VALID_ROLES:
        return jsonify({"success": False, "error": "Invalid role."}), 400
    target = get_user_by_id(user_id)
    if not target:
        return jsonify({"success": False, "error": "User not found."}), 404
    if target['role'] == 'admin' and role != 'admin' and count_admins() <= 1:
        return jsonify({"success": False, "error": "At least one admin account must remain."}), 400
    update_user_role(user_id, role)
    return jsonify({"success": True, "message": f"{target['username']} is now a {role}."})

# ==============================================================================
# ERROR HANDLING
# ==============================================================================

def render_error_page(code, message):
    return render_template('error.html', code=code, message=message), code


@app.errorhandler(HTTPException)
def handle_http_error(e):
    messages = {
        404: "The page or resource you requested was not found.",
        405: "This action is not allowed here.",
        413: f"File too large. The maximum upload size is {Config.MAX_CONTENT_LENGTH // (1024 * 1024)} MB.",
    }
    message = messages.get(e.code, e.description)
    if wants_json():
        return jsonify({"success": False, "error": message}), e.code
    return render_error_page(e.code, message)


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    app.logger.exception("Unhandled error")
    message = "Something went wrong on our side. Please try again."
    if wants_json():
        return jsonify({"success": False, "error": message}), 500
    return render_error_page(500, message)

# ==============================================================================
# PWA SUPPORT (MANIFEST & SERVICE WORKER)
# ==============================================================================

@app.route('/manifest.json')
def pwa_manifest():
    """PWA web application manifest."""
    return send_file(os.path.join(Config.BASE_DIR, "static", "manifest.json"), mimetype="application/manifest+json")


@app.route('/sw.js')
def pwa_service_worker():
    """Service worker for offline shell caching and fast loading."""
    response = send_file(os.path.join(Config.BASE_DIR, "static", "sw.js"), mimetype="application/javascript")
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Cache-Control'] = 'no-cache'
    return response

# ==============================================================================
# MAIN ENTRYPOINT
# ==============================================================================

if __name__ == '__main__':
    print(f"Pulse App running on http://{'127.0.0.1' if Config.HOST in ('0.0.0.0', '127.0.0.1') else Config.HOST}:{Config.PORT} (debug={Config.DEBUG})")
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)

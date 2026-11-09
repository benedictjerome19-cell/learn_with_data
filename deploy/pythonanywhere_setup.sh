#!/usr/bin/env bash
# One-time setup for Consistency Pulse on PythonAnywhere.
# Run from a PythonAnywhere Bash console AFTER creating a "Manual configuration"
# web app with the same Python version:
#
#     bash ~/learn_with_data/deploy/pythonanywhere_setup.sh 3.13
#
# Safe to re-run: it never overwrites an existing .env (secret key) or database.
set -euo pipefail

PYVER="${1:-3.13}"
APP_DIR="$HOME/learn_with_data"
PY="python${PYVER}"
WSGI_FILE="/var/www/${USER}_pythonanywhere_com_wsgi.py"

cd "$APP_DIR"
command -v "$PY" >/dev/null || { echo "Python $PYVER not found. Pass the version your web app uses, e.g. 3.12"; exit 1; }

# 1. Environment file with a fresh secret key (kept on re-runs)
if [ ! -f .env ]; then
  SECRET=$("$PY" -c "import secrets; print(secrets.token_hex(32))")
  cat > .env <<EOF
SECRET_KEY=${SECRET}
FLASK_ENV=production
FLASK_DEBUG=0
SESSION_TIMEOUT_MINUTES=30
DATABASE_PATH=learn_with_data.db
EXCEL_SYNC_PATH=python_summary.xlsx
MAX_CONTENT_LENGTH_MB=16
SESSION_COOKIE_SECURE=1
SQLITE_JOURNAL_MODE=DELETE
EOF
  chmod 600 .env
  echo "Created .env with a new secret key"
else
  echo ".env already exists; keeping it"
fi
chmod 600 learn_with_data.db 2>/dev/null || true

# 2. Dependencies: PythonAnywhere preinstalls most of them; add anything missing for this user
"$PY" -m pip install --user --quiet --upgrade-strategy only-if-needed \
  "Flask>=3.0" "Werkzeug>=3.0" "pandas>=2.0" "openpyxl>=3.1" "reportlab>=4.0" "python-dotenv>=1.0" "xlrd>=2.0.1"

# 3. Smoke test: import the app (runs schema migrations) and hit the login page
"$PY" - <<'EOF'
import app
client = app.app.test_client()
res = client.get("/login")
assert res.status_code == 200, res.status_code
print("App import and /login check OK")
EOF

# 4. WSGI entry point used by the web app
if [ -f "$WSGI_FILE" ]; then
  cp "$WSGI_FILE" "${WSGI_FILE}.bak"
  cat > "$WSGI_FILE" <<EOF
import os
import sys

project_home = os.path.expanduser("~/learn_with_data")
if project_home not in sys.path:
    sys.path.insert(0, project_home)
os.chdir(project_home)

from app import app as application  # noqa: E402
EOF
  echo "Wrote $WSGI_FILE (previous version saved as .bak)"
else
  echo "WARNING: $WSGI_FILE not found. Create the web app on the Web tab first, then re-run this script."
  exit 1
fi

echo
echo "Setup finished. Go to the Web tab and click 'Reload ${USER}.pythonanywhere.com'."

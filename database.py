"""
Database module for Learning Tracker & Consistency Pulse.
Provides SQLite connection management, table schemas and migrations, Werkzeug
password hashing, user authentication, CRUD operations, bulk import transaction
handling, admin queries and realistic seed data generation.
"""

import sqlite3
import random
from contextlib import contextmanager
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from config import Config

VALID_ROLES = ("admin", "mentor", "learner")
LOG_COLUMNS = "id, user_id, date, language, topic, concepts_count, hours_spent, mastery_score, notes, source, created_at, updated_at"


def get_db_connection():
    """Returns a SQLite connection with Row factory, foreign keys and WAL enabled."""
    conn = sqlite3.connect(Config.DATABASE_PATH, timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


@contextmanager
def db_cursor(commit=False):
    """Yields a cursor; commits on success when requested, always rolls back on error and closes."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        yield cursor
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _column_names(cursor, table):
    return {row["name"] for row in cursor.execute(f"PRAGMA table_info({table})").fetchall()}


def init_db():
    """Initializes tables, applies additive migrations and seeds demo data if the DB is empty."""
    conn = get_db_connection()
    try:
        # WAL lets the dashboard keep reading while background syncs write
        conn.execute(f"PRAGMA journal_mode = {Config.SQLITE_JOURNAL_MODE}")
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'learner',
                avatar_color TEXT DEFAULT '#6366f1',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS learning_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                language TEXT NOT NULL,
                topic TEXT NOT NULL,
                concepts_count INTEGER NOT NULL DEFAULT 1,
                hours_spent REAL NOT NULL DEFAULT 1.0,
                mastery_score INTEGER NOT NULL DEFAULT 3,
                notes TEXT,
                source TEXT DEFAULT 'manual',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sync_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                sync_type TEXT NOT NULL,
                records_count INTEGER NOT NULL DEFAULT 0,
                file_name TEXT,
                status TEXT NOT NULL DEFAULT 'success',
                message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
            )
        """)

        # Additive migration for databases created before log editing existed
        if "updated_at" not in _column_names(cursor, "learning_logs"):
            cursor.execute("ALTER TABLE learning_logs ADD COLUMN updated_at TIMESTAMP")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_user_date ON learning_logs(user_id, date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_user_lang ON learning_logs(user_id, language)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sync_user ON sync_audit(user_id, id)")

        conn.commit()
    finally:
        conn.close()

    seed_demo_data_if_needed()


# ==============================================================================
# USERS & AUTHENTICATION
# ==============================================================================

def create_user(username, email, password, role="learner", avatar_color=None):
    """Creates a new user with secure Werkzeug salted hash. Returns (user_id, error)."""
    if role not in VALID_ROLES:
        return None, "Invalid role."
    if not avatar_color:
        colors = ["#6366f1", "#06b6d4", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6"]
        avatar_color = random.choice(colors)

    password_hash = generate_password_hash(password, method="pbkdf2:sha256")
    try:
        with db_cursor(commit=True) as cursor:
            # UNIQUE on username is case-sensitive in SQLite; login is not, so check explicitly
            cursor.execute("SELECT username, email FROM users WHERE LOWER(username) = LOWER(?) OR LOWER(email) = LOWER(?)",
                           (username.strip(), email.strip()))
            existing = cursor.fetchone()
            if existing:
                if existing["username"].lower() == username.strip().lower():
                    return None, "Username already exists."
                return None, "Email address already registered."
            cursor.execute("""
                INSERT INTO users (username, email, password_hash, role, avatar_color)
                VALUES (?, ?, ?, ?, ?)
            """, (username.strip(), email.strip().lower(), password_hash, role, avatar_color))
            return cursor.lastrowid, None
    except sqlite3.IntegrityError as e:
        if "username" in str(e):
            return None, "Username already exists."
        elif "email" in str(e):
            return None, "Email address already registered."
        return None, "A user with this username or email already exists."


def authenticate_user(username_or_email, password):
    """Validates user credentials using secure hash comparison."""
    identifier = (username_or_email or "").strip()
    if not identifier or not password:
        return None
    with db_cursor(commit=True) as cursor:
        cursor.execute("""
            SELECT * FROM users
            WHERE LOWER(username) = LOWER(?) OR LOWER(email) = LOWER(?)
        """, (identifier, identifier))
        user = cursor.fetchone()
        if user and check_password_hash(user["password_hash"], password):
            cursor.execute("UPDATE users SET last_login = ? WHERE id = ?",
                           (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user["id"]))
            result = dict(user)
            result.pop("password_hash", None)
            return result
    return None


def get_user_by_id(user_id):
    """Retrieves user profile by ID (never includes the password hash)."""
    with db_cursor() as cursor:
        cursor.execute("SELECT id, username, email, role, avatar_color, created_at, last_login FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()
    return dict(user) if user else None


def change_password(user_id, current_password, new_password):
    """Updates a password after verifying the current one. Returns (ok, error)."""
    with db_cursor(commit=True) as cursor:
        cursor.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if not row or not check_password_hash(row["password_hash"], current_password or ""):
            return False, "Current password is incorrect."
        cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                       (generate_password_hash(new_password, method="pbkdf2:sha256"), user_id))
    return True, None


def list_users_with_stats():
    """Admin overview: every user with activity totals."""
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT u.id, u.username, u.email, u.role, u.avatar_color, u.created_at, u.last_login,
                   COUNT(l.id) AS sessions,
                   ROUND(COALESCE(SUM(l.hours_spent), 0), 1) AS total_hours,
                   MAX(l.date) AS last_active
            FROM users u
            LEFT JOIN learning_logs l ON l.user_id = u.id
            GROUP BY u.id
            ORDER BY u.id
        """)
        return [dict(r) for r in cursor.fetchall()]


def update_user_role(user_id, role):
    if role not in VALID_ROLES:
        return False
    with db_cursor(commit=True) as cursor:
        cursor.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        return cursor.rowcount > 0


def count_admins():
    with db_cursor() as cursor:
        return cursor.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]


# ==============================================================================
# LEARNING LOGS
# ==============================================================================

def canonical_language(user_id, language, cursor=None):
    """
    Reuses the casing of a language the user already logged (e.g. 'python' -> 'Python'),
    so analytics, filters and Excel formulas do not split one stack into several.
    """
    def lookup(cur):
        cur.execute("SELECT language FROM learning_logs WHERE user_id = ? AND LOWER(language) = LOWER(?) LIMIT 1",
                    (user_id, language))
        row = cur.fetchone()
        return row["language"] if row else language

    if cursor is not None:
        return lookup(cursor)
    with db_cursor() as cur:
        return lookup(cur)


def add_learning_log(user_id, date, language, topic, concepts_count, hours_spent, mastery_score, notes="", source="manual"):
    """Inserts a single learning log entry."""
    with db_cursor(commit=True) as cursor:
        language = canonical_language(user_id, language, cursor)
        cursor.execute("""
            INSERT INTO learning_logs
            (user_id, date, language, topic, concepts_count, hours_spent, mastery_score, notes, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, date, language, topic, int(concepts_count), float(hours_spent), int(mastery_score), notes, source))
        return cursor.lastrowid


def update_learning_log(log_id, user_id, fields):
    """Updates an existing log owned by user_id with already-validated fields."""
    with db_cursor(commit=True) as cursor:
        language = canonical_language(user_id, fields["language"], cursor)
        cursor.execute("""
            UPDATE learning_logs
            SET date = ?, language = ?, topic = ?, concepts_count = ?, hours_spent = ?,
                mastery_score = ?, notes = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
        """, (fields["date"], language, fields["topic"], fields["concepts_count"], fields["hours_spent"],
              fields["mastery_score"], fields["notes"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              log_id, user_id))
        return cursor.rowcount > 0


def get_learning_log(log_id, user_id):
    with db_cursor() as cursor:
        cursor.execute(f"SELECT {LOG_COLUMNS} FROM learning_logs WHERE id = ? AND user_id = ?", (log_id, user_id))
        row = cursor.fetchone()
    return dict(row) if row else None


def bulk_insert_logs(user_id, logs_data, source="bulk_import"):
    """Inserts multiple logs in a single atomic database transaction."""
    if not logs_data:
        return 0

    inserted_count = 0
    with db_cursor(commit=True) as cursor:
        language_cache = {}
        for item in logs_data:
            raw_lang = item.get("language", "General")
            key = raw_lang.lower()
            if key not in language_cache:
                language_cache[key] = canonical_language(user_id, raw_lang, cursor)
            cursor.execute("""
                INSERT INTO learning_logs
                (user_id, date, language, topic, concepts_count, hours_spent, mastery_score, notes, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id,
                item["date"],
                language_cache[key],
                item.get("topic", "Learning Session"),
                int(item.get("concepts_count", 1)),
                float(item.get("hours_spent", 1.0)),
                int(item.get("mastery_score", 3)),
                item.get("notes", ""),
                source
            ))
            inserted_count += 1
    return inserted_count


def _logs_filter(user_id, language=None, search=None):
    clause = "WHERE user_id = ?"
    params = [user_id]
    if language and language.lower() != "all":
        clause += " AND LOWER(language) = LOWER(?)"
        params.append(language)
    if search:
        like = f"%{search.lower()}%"
        clause += " AND (LOWER(topic) LIKE ? OR LOWER(COALESCE(notes, '')) LIKE ? OR LOWER(language) LIKE ?)"
        params.extend([like, like, like])
    return clause, params


def get_user_logs(user_id, language=None, limit=200, offset=0, search=None):
    """Retrieves recent learning logs with optional language filter and text search."""
    clause, params = _logs_filter(user_id, language, search)
    with db_cursor() as cursor:
        cursor.execute(f"SELECT {LOG_COLUMNS} FROM learning_logs {clause} ORDER BY date DESC, id DESC LIMIT ? OFFSET ?",
                       tuple(params + [limit, offset]))
        return [dict(row) for row in cursor.fetchall()]


def count_user_logs(user_id, language=None, search=None):
    clause, params = _logs_filter(user_id, language, search)
    with db_cursor() as cursor:
        return cursor.execute(f"SELECT COUNT(*) FROM learning_logs {clause}", tuple(params)).fetchone()[0]


def delete_learning_log(log_id, user_id):
    """Deletes a log entry belonging to the specified user."""
    with db_cursor(commit=True) as cursor:
        cursor.execute("DELETE FROM learning_logs WHERE id = ? AND user_id = ?", (log_id, user_id))
        return cursor.rowcount > 0


# ==============================================================================
# SYNC AUDIT TRAIL
# ==============================================================================

def record_sync_audit(user_id, sync_type, records_count, file_name, status="success", message=""):
    """Records an audit trail entry for sync/export actions."""
    with db_cursor(commit=True) as cursor:
        cursor.execute("""
            INSERT INTO sync_audit (user_id, sync_type, records_count, file_name, status, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, sync_type, records_count, file_name, status, message,
              datetime.now().strftime("%Y-%m-%d %H:%M:%S")))


def get_last_sync_info(user_id):
    """Fetches the latest successful sync audit record for a user."""
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT * FROM sync_audit
            WHERE user_id = ? AND status = 'success'
            ORDER BY id DESC LIMIT 1
        """, (user_id,))
        row = cursor.fetchone()
    return dict(row) if row else None


def get_recent_sync_audits(limit=20):
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT a.id, a.sync_type, a.records_count, a.file_name, a.status, a.message, a.created_at,
                   u.username
            FROM sync_audit a LEFT JOIN users u ON u.id = a.user_id
            ORDER BY a.id DESC LIMIT ?
        """, (limit,))
        return [dict(r) for r in cursor.fetchall()]


def seed_demo_data_if_needed():
    """Generates realistic demonstration users and 52-week activity logs."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) as cnt FROM users")
    count = cursor.fetchone()["cnt"]
    if count > 0:
        conn.close()
        return

    # Seed Admin and Learner
    admin_hash = generate_password_hash("Admin@123", method="pbkdf2:sha256")
    learner_hash = generate_password_hash("Learner@123", method="pbkdf2:sha256")
    
    cursor.execute("""
        INSERT INTO users (username, email, password_hash, role, avatar_color, created_at)
        VALUES 
        ('admin', 'admin@pulse.dev', ?, 'admin', '#ec4899', '2025-01-01 08:00:00'),
        ('learner', 'learner@pulse.dev', ?, 'learner', '#6366f1', '2025-01-10 09:00:00')
    """, (admin_hash, learner_hash))
    
    learner_id = cursor.lastrowid
    admin_id = 1

    # Rich curated seed curriculum for demo
    curriculum = [
        ("Python", [
            ("Advanced Generators & Coroutines", 3, 2.5, 5, "Deep dive into itertools and async def"),
            ("Pandas Rolling Window Analytics", 4, 3.0, 4, "Weekly and monthly rolling moving averages"),
            ("FastAPI & Pydantic Data Validation", 3, 2.0, 4, "High performance REST interfaces"),
            ("Metaclasses and Dynamic Class Creation", 2, 1.5, 3, "Understanding type() and __init_subclass__"),
            ("Multithreading vs Multiprocessing", 3, 2.5, 4, "GIL implications and ProcessPoolExecutor"),
            ("Pytest Parametrized Fixtures", 2, 1.5, 5, "Mocking external APIs cleanly")
        ]),
        ("SQL", [
            ("Window Functions (RANK, DENSE_RANK, NTILE)", 4, 2.5, 5, "Analytical partitions over time-series"),
            ("Recursive CTEs for Hierarchical Graphs", 3, 3.0, 4, "Org-chart traversal and path finding"),
            ("Query Execution Plans & B-Tree Indexes", 3, 2.0, 4, "Diagnosing table scans in PostgreSQL"),
            ("ACID Transactions & Isolation Levels", 2, 1.5, 4, "Dirty reads vs Phantom reads"),
            ("Materialized Views with Concurrency Refresh", 2, 2.0, 5, "Optimizing heavy dashboard aggregations")
        ]),
        ("Java", [
            ("Java 21 Virtual Threads & Loom Project", 3, 2.5, 5, "High-concurrency microservices"),
            ("Spring Boot 3 Reactive WebFlux", 4, 3.0, 4, "Non-blocking backpressure streams"),
            ("JVM Memory Leak Diagnostics & GC Tuning", 3, 3.5, 4, "ZGC low-latency garbage collector analysis"),
            ("Functional Streams & Collectors API", 2, 1.5, 5, "Declarative data pipelines")
        ]),
        ("Docker", [
            ("Multi-Stage Dockerfile Optimization", 3, 2.0, 5, "Shrinking images from 1.2GB to 85MB"),
            ("Docker Compose v2 Multi-Container Networking", 3, 2.5, 4, "Bridged networks, volumes, health checks"),
            ("Rootless Containers & Security Hardening", 2, 1.5, 4, "Dropping Linux capabilities and seccomp profiles"),
            ("Docker BuildKit & Remote Layer Caching", 2, 2.0, 4, "Accelerating CI/CD pipelines")
        ]),
        ("Machine Learning", [
            ("Transformer Self-Attention Mechanisms", 4, 3.5, 5, "Query-Key-Value matrix algebra"),
            ("Scikit-Learn Pipeline & Feature Union", 3, 2.5, 4, "Automated scaling, encoding, and imputation"),
            ("Gradient Boosted Trees (XGBoost / LightGBM)", 3, 3.0, 4, "Hyperparameter tuning with Optuna"),
            ("ROC-AUC, Precision-Recall & F1 Tradeoffs", 2, 1.5, 5, "Evaluating imbalanced fraud detection models"),
            ("PyTorch Tensor Operations & Autograd", 3, 3.0, 4, "Backpropagation computation graphs")
        ])
    ]

    # Generate 52-week realistic activity timeline (365 days)
    today = datetime.now()
    start_date = today - timedelta(days=364)
    
    logs_to_insert = []
    
    # 70% active learning consistency probability
    current_date = start_date
    while current_date <= today:
        date_str = current_date.strftime("%Y-%m-%d")
        day_of_week = current_date.weekday() # 0 = Monday, 6 = Sunday
        
        # High consistency during weekdays, moderate on weekends
        is_active = (random.random() < 0.78) if day_of_week < 5 else (random.random() < 0.60)
        
        if is_active:
            # Pick 1 or 2 topics for this day
            entries_count = 2 if random.random() < 0.35 else 1
            for _ in range(entries_count):
                lang, topics_list = random.choice(curriculum)
                topic_title, concepts, hours, mastery, notes = random.choice(topics_list)
                
                # Small realistic variation
                actual_hours = round(max(0.5, hours + random.uniform(-0.5, 0.8)), 1)
                actual_concepts = max(1, concepts + random.randint(-1, 2))
                actual_mastery = max(1, min(5, mastery + random.randint(-1, 0)))
                
                # Add for learner
                logs_to_insert.append((
                    learner_id, date_str, lang, topic_title, 
                    actual_concepts, actual_hours, actual_mastery, notes, "seed_data"
                ))
                # Add a subset for admin
                if random.random() < 0.85:
                    logs_to_insert.append((
                        admin_id, date_str, lang, topic_title, 
                        actual_concepts, actual_hours, actual_mastery, notes, "seed_data"
                    ))

        current_date += timedelta(days=1)

    cursor.executemany("""
        INSERT INTO learning_logs 
        (user_id, date, language, topic, concepts_count, hours_spent, mastery_score, notes, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, logs_to_insert)

    conn.commit()
    conn.close()

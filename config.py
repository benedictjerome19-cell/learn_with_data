"""
Configuration module for Learning Tracker & Consistency Pulse.
Loads secure settings from .env using python-dotenv with robust fallbacks.
"""

import os
import secrets
from datetime import timedelta
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Keys that were published in documentation or templates and must never sign real sessions
_INSECURE_SECRET_KEYS = {
    "",
    "antigravity-pulse-enterprise-secret-key-9824982498",
    "replace-with-a-secure-random-key-in-production",
    "pulse-default-dev-secret-key-38947239847",
}


def _load_secret_key():
    """Returns SECRET_KEY from env, or a random key persisted to a local file if unset/insecure."""
    key = os.getenv("SECRET_KEY", "").strip()
    if key not in _INSECURE_SECRET_KEYS and len(key) >= 32:
        return key

    key_file = os.path.join(BASE_DIR, ".secret_key")
    try:
        with open(key_file, "r", encoding="utf-8") as fh:
            stored = fh.read().strip()
            if len(stored) >= 32:
                return stored
    except OSError:
        pass

    generated = secrets.token_hex(32)
    try:
        with open(key_file, "w", encoding="utf-8") as fh:
            fh.write(generated)
    except OSError:
        pass  # Fall back to a per-process key (sessions reset on restart)
    return generated


def _env_bool(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


class Config:
    BASE_DIR = BASE_DIR
    # Flask Security
    SECRET_KEY = _load_secret_key()

    # Session Configuration & Security
    SESSION_TIMEOUT_MINUTES = max(1, int(os.getenv("SESSION_TIMEOUT_MINUTES", "30")))
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=SESSION_TIMEOUT_MINUTES)
    SESSION_REFRESH_EACH_REQUEST = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Secure cookies require HTTPS (browsers still accept them on http://localhost).
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", os.getenv("FLASK_ENV", "production") == "production")

    # Brute-force protection: failed logins allowed per username+IP inside the window
    LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
    LOGIN_LOCKOUT_MINUTES = int(os.getenv("LOGIN_LOCKOUT_MINUTES", "15"))

    # Database. WAL is faster locally but unreliable on network filesystems
    # (e.g. PythonAnywhere); set SQLITE_JOURNAL_MODE=DELETE there.
    DATABASE_PATH = os.path.join(BASE_DIR, os.getenv("DATABASE_PATH", "learning_tracker.db"))
    SQLITE_JOURNAL_MODE = os.getenv("SQLITE_JOURNAL_MODE", "WAL").strip().upper()
    if SQLITE_JOURNAL_MODE not in ("WAL", "DELETE", "TRUNCATE", "PERSIST"):
        SQLITE_JOURNAL_MODE = "WAL"

    # Excel Background Sync
    EXCEL_SYNC_PATH = os.path.join(BASE_DIR, os.getenv("EXCEL_SYNC_PATH", "python_summary.xlsx"))

    # Upload Limits (16 MB default)
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH_MB", "16")) * 1024 * 1024
    MAX_IMPORT_ROWS = int(os.getenv("MAX_IMPORT_ROWS", "10000"))
    ALLOWED_EXTENSIONS = {"csv", "xlsx", "xls", "json"}

    # Server
    HOST = os.getenv("HOST", "127.0.0.1")
    PORT = int(os.getenv("PORT", "5000"))
    DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"

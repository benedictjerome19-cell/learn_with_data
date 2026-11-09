"""
Builds deploy/learn_with_data_deploy.zip for uploading to PythonAnywhere.
Includes the app code and a consistent snapshot of learn_with_data.db
(switched to rollback-journal mode, which suits network filesystems).
Never includes .env, .secret_key, backups or caches.

    python deploy/build_bundle.py
"""

import os
import sqlite3
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "deploy", "learn_with_data_deploy.zip")
FILES = ["app.py", "config.py", "database.py", "analytics.py", "validators.py", "excel_exporter.py",
         "pdf_exporter.py", "inspect_db.py", "requirements.txt", "Procfile", "README.md", ".env.example",
         "sample_import.csv", "sample_import.json"]
DIRS = ["templates", "static", "tests"]


def snapshot_db():
    tmp_db = os.path.join(tempfile.mkdtemp(), "learn_with_data.db")
    src = sqlite3.connect(os.path.join(ROOT, "learn_with_data.db"))
    dst = sqlite3.connect(tmp_db)
    src.backup(dst)
    src.close()
    mode = dst.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
    integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
    logs = dst.execute("SELECT COUNT(*) FROM learning_logs").fetchone()[0]
    users = dst.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    dst.close()
    print(f"DB snapshot: journal={mode} integrity={integrity} logs={logs} users={users}")
    return tmp_db


def main():
    db_path = snapshot_db()
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for name in FILES:
            z.write(os.path.join(ROOT, name), f"learn_with_data/{name}")
        for d in DIRS:
            for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, d)):
                dirnames[:] = [x for x in dirnames if x != "__pycache__"]
                for fn in filenames:
                    full = os.path.join(dirpath, fn)
                    rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
                    z.write(full, f"learn_with_data/{rel}")
        with open(os.path.join(ROOT, "deploy", "pythonanywhere_setup.sh"), "rb") as fh:
            z.writestr("learn_with_data/deploy/pythonanywhere_setup.sh", fh.read().replace(b"\r\n", b"\n"))
        z.write(db_path, "learn_with_data/learn_with_data.db")

    names = zipfile.ZipFile(OUT).namelist()
    leaked = [n for n in names if n.endswith(".env") or "secret" in n or "backup" in n]
    assert not leaked, leaked
    print(f"Wrote {OUT}: {len(names)} files, {round(os.path.getsize(OUT) / 1024)} KB")


if __name__ == "__main__":
    main()

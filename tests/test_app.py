"""
Automated tests for Consistency Pulse.
Runs against a throwaway SQLite database and Excel file in a temp directory,
so the real learn_with_data.db is never touched.

    python -m unittest discover -s tests -v
"""

import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from datetime import date, timedelta

TMP_DIR = tempfile.mkdtemp(prefix="pulse_tests_")
os.environ["DATABASE_PATH"] = os.path.join(TMP_DIR, "test.db")
os.environ["EXCEL_SYNC_PATH"] = os.path.join(TMP_DIR, "summary.xlsx")
os.environ["SECRET_KEY"] = "test-secret-key-0123456789abcdef0123456789abcdef"
os.environ["SESSION_COOKIE_SECURE"] = "0"
os.environ["LOGIN_MAX_ATTEMPTS"] = "3"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl  # noqa: E402
import app as pulse  # noqa: E402
import analytics  # noqa: E402
import validators  # noqa: E402
from database import get_db_connection  # noqa: E402


def wait_for_background_syncs():
    for t in threading.enumerate():
        if t.name == "ExcelSyncWorker":
            t.join(timeout=30)


class PulseTestCase(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        wait_for_background_syncs()

    def setUp(self):
        pulse.app.config["TESTING"] = True
        pulse.login_throttle._failures.clear()
        self.client = pulse.app.test_client()

    def csrf(self, client=None):
        client = client or self.client
        client.get("/login")
        with client.session_transaction() as sess:
            return sess["_csrf_token"]

    def login(self, username="learner", password="Learner@123", client=None):
        client = client or self.client
        token = self.csrf(client)
        res = client.post("/login", json={"username": username, "password": password},
                          headers={"X-CSRFToken": token})
        if res.status_code == 200:
            return res.get_json()["csrf_token"]
        return None

    def register(self, username, password="Passw0rd!", email=None, client=None, **extra):
        client = client or self.client
        token = self.csrf(client)
        body = {"username": username, "email": email or f"{username}@example.com", "password": password}
        body.update(extra)
        return client.post("/register", json=body, headers={"X-CSRFToken": token})

    def new_user(self, username):
        client = pulse.app.test_client()
        res = self.register(username, client=client)
        self.assertEqual(res.status_code, 200, res.get_json())
        return client, res.get_json()["csrf_token"]


class AuthTests(PulseTestCase):
    def test_anonymous_dashboard_redirects_to_login(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.headers["Location"])

    def test_anonymous_api_returns_401(self):
        self.assertEqual(self.client.get("/api/stats").status_code, 401)

    def test_login_success_and_dashboard(self):
        self.assertIsNotNone(self.login())
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"learner", res.data)

    def test_login_wrong_password(self):
        self.assertIsNone(self.login(password="wrong"))

    def test_form_login_requires_csrf(self):
        res = self.client.post("/login", data={"username": "learner", "password": "Learner@123"})
        self.assertEqual(res.status_code, 302)
        self.assertNotIn("user_id", self._session())

    def _session(self):
        with self.client.session_transaction() as sess:
            return dict(sess)

    def test_open_redirect_blocked(self):
        token = self.csrf()
        for target in ("https://evil.example", "//evil.example", "/\\evil.example"):
            res = self.client.post("/login?next=" + target,
                                   data={"username": "learner", "password": "Learner@123", "csrf_token": token})
            self.assertEqual(res.status_code, 302)
            self.assertTrue(res.headers["Location"].endswith("/"), res.headers["Location"])
            token = self._session()["_csrf_token"]

    def test_safe_next_redirect_kept(self):
        token = self.csrf()
        res = self.client.post("/login?next=/export/pdf",
                               data={"username": "learner", "password": "Learner@123", "csrf_token": token})
        self.assertTrue(res.headers["Location"].endswith("/export/pdf"))

    def test_login_throttle(self):
        token = self.csrf()
        for _ in range(3):
            self.client.post("/login", json={"username": "admin", "password": "bad"}, headers={"X-CSRFToken": token})
        res = self.client.post("/login", json={"username": "admin", "password": "Admin@123"}, headers={"X-CSRFToken": token})
        self.assertEqual(res.status_code, 429)

    def test_register_cannot_choose_admin_role(self):
        res = self.register("sneaky1", role="admin")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["user"]["role"], "learner")

    def test_register_validation(self):
        self.assertEqual(self.register("ab").status_code, 400)
        self.assertEqual(self.register("weakpw", password="x").status_code, 400)
        self.assertEqual(self.register("noemail", email="not-an-email").status_code, 400)
        self.assertEqual(self.register("mismatch", confirm_password="Other123").status_code, 400)

    def test_register_duplicate_username_case_insensitive(self):
        self.assertEqual(self.register("Learner", email="other@example.com").status_code, 400)

    def test_logout_requires_post(self):
        token = self.login()
        self.assertEqual(self.client.get("/logout").status_code, 405)
        res = self.client.post("/logout", headers={"X-CSRFToken": token})
        self.assertEqual(res.status_code, 302)
        self.assertEqual(self.client.get("/api/stats").status_code, 401)

    def test_change_password(self):
        client, token = self.new_user("pwchanger")
        h = {"X-CSRFToken": token}
        bad = client.post("/api/account/password", json={"current_password": "nope", "new_password": "NewPass123"}, headers=h)
        self.assertEqual(bad.status_code, 400)
        ok = client.post("/api/account/password", json={"current_password": "Passw0rd!", "new_password": "NewPass123"}, headers=h)
        self.assertEqual(ok.status_code, 200)
        fresh = pulse.app.test_client()
        self.assertIsNotNone(self.login("pwchanger", "NewPass123", client=fresh))


class LogCrudTests(PulseTestCase):
    def setUp(self):
        super().setUp()
        self.client, self.token = self.new_user(f"crud{id(self) % 100000}")
        self.h = {"X-CSRFToken": self.token}

    def add(self, **fields):
        body = {"date": date.today().isoformat(), "language": "Python", "topic": "Decorators",
                "concepts_count": 2, "hours_spent": 1.5, "mastery_score": 4, "notes": ""}
        body.update(fields)
        return self.client.post("/api/log", json=body, headers=self.h)

    def test_add_requires_csrf(self):
        res = self.client.post("/api/log", json={"topic": "x"})
        self.assertEqual(res.status_code, 400)

    def test_add_list_edit_delete(self):
        res = self.add(topic="Generators")
        self.assertEqual(res.status_code, 201)
        log_id = res.get_json()["log_id"]

        logs = self.client.get("/api/logs").get_json()
        self.assertEqual(logs["total"], 1)
        self.assertEqual(logs["logs"][0]["topic"], "Generators")

        upd = self.client.put(f"/api/logs/{log_id}", json={"date": date.today().isoformat(), "language": "SQL",
                                                           "topic": "CTEs", "concepts_count": 3, "hours_spent": 2,
                                                           "mastery_score": 5}, headers=self.h)
        self.assertEqual(upd.status_code, 200)
        log = self.client.get(f"/api/logs/{log_id}").get_json()["log"]
        self.assertEqual((log["language"], log["topic"], log["hours_spent"]), ("SQL", "CTEs", 2.0))
        self.assertIsNotNone(log["updated_at"])

        self.assertEqual(self.client.delete(f"/api/logs/{log_id}", headers=self.h).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/logs/{log_id}", headers=self.h).status_code, 404)

    def test_invalid_inputs_rejected(self):
        future = (date.today() + timedelta(days=5)).isoformat()
        cases = [
            {"topic": ""},
            {"date": "notadate"},
            {"date": future},
            {"hours_spent": "nan"},
            {"hours_spent": "inf"},
            {"hours_spent": 0},
            {"hours_spent": 25},
            {"concepts_count": 0},
            {"concepts_count": 2.5},
            {"concepts_count": [1]},
            {"mastery_score": 6},
            {"topic": "x" * 201},
        ]
        for fields in cases:
            with self.subTest(fields=fields):
                res = self.add(**fields)
                self.assertEqual(res.status_code, 400, res.get_json())
                self.assertFalse(res.get_json()["success"])

    def test_non_json_body(self):
        res = self.client.post("/api/log", data="x", content_type="text/plain", headers=self.h)
        self.assertEqual(res.status_code, 400)

    def test_cannot_touch_other_users_logs(self):
        log_id = self.add().get_json()["log_id"]
        other, other_token = self.new_user(f"other{id(self) % 100000}")
        h = {"X-CSRFToken": other_token}
        self.assertEqual(other.delete(f"/api/logs/{log_id}", headers=h).status_code, 404)
        self.assertEqual(other.get(f"/api/logs/{log_id}").status_code, 404)
        self.assertEqual(other.put(f"/api/logs/{log_id}", json={"topic": "hijack"}, headers=h).status_code, 404)

    def test_language_case_is_unified(self):
        self.add(language="Python")
        self.add(language="python")
        langs = {l["language"] for l in self.client.get("/api/logs").get_json()["logs"]}
        self.assertEqual(langs, {"Python"})

    def test_limit_and_search_params(self):
        self.add(topic="Alpha topic")
        self.add(topic="Beta topic", notes="needle")
        self.assertEqual(self.client.get("/api/logs?limit=abc").status_code, 200)
        self.assertEqual(self.client.get("/api/logs?limit=-5").get_json()["limit"], 1)
        found = self.client.get("/api/logs?q=needle").get_json()
        self.assertEqual([l["topic"] for l in found["logs"]], ["Beta topic"])

    def test_html_is_stored_verbatim_not_executed_server_side(self):
        payload = '<img src=x onerror=alert(1)>'
        self.assertEqual(self.add(topic=payload, language="<b>L</b>").status_code, 201)
        stats = self.client.get("/api/stats").get_json()
        tooltips = " ".join(c["tooltip"] for week in stats["heatmap"]["matrix"] for c in week)
        self.assertNotIn("<b>L</b>", tooltips)
        self.assertIn("&lt;b&gt;L&lt;/b&gt;", tooltips)


class AnalyticsTests(PulseTestCase):
    def test_empty_user_has_full_grid(self):
        client, _ = self.new_user("emptystats")
        stats = client.get("/api/stats").get_json()
        self.assertEqual(stats["kpis"]["total_sessions"], 0)
        self.assertGreaterEqual(len(stats["heatmap"]["matrix"]), 52)

    def test_filter_unknown_language(self):
        self.login()
        res = self.client.get("/api/stats?language=Rust")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["kpis"]["total_sessions"], 0)

    def test_corrupt_legacy_date_does_not_break_dashboard(self):
        client, token = self.new_user("legacydates")
        user_id = client.get("/api/session-status").get_json()["user"]["id"]
        conn = get_db_connection()
        conn.execute("INSERT INTO learning_logs (user_id, date, language, topic) VALUES (?, 'garbage', 'Python', 'bad')", (user_id,))
        conn.commit()
        conn.close()
        self.assertEqual(client.get("/api/stats").status_code, 200)
        self.assertEqual(client.get("/export/pdf").status_code, 200)
        self.assertEqual(client.get("/export/excel").status_code, 200)

    def test_moving_averages_and_streak_math(self):
        client, token = self.new_user("mathcheck")
        h = {"X-CSRFToken": token}
        today = date.today()
        # 2h on each of the last 3 days excluding today -> streak 3 (today not yet logged)
        for d in (1, 2, 3):
            client.post("/api/log", json={"date": (today - timedelta(days=d)).isoformat(), "topic": "Math",
                                          "hours_spent": 2, "concepts_count": 1, "mastery_score": 4}, headers=h)
        k = client.get("/api/stats").get_json()["kpis"]
        self.assertEqual(k["total_hours"], 6.0)
        self.assertEqual(k["total_sessions"], 3)
        self.assertEqual(k["current_streak"], 3)
        self.assertEqual(k["longest_streak"], 3)
        self.assertEqual(k["weekly_ma_hours"], round(6 / 7, 2))
        self.assertEqual(k["monthly_ma_hours"], round(6 / 30, 2))
        expected_velocity = round(((round(6 / 7, 2) - round(6 / 30, 2)) / round(6 / 30, 2)) * 100, 1)
        self.assertEqual(k["improvement_velocity"], expected_velocity)
        self.assertEqual(k["avg_mastery"], 4.0)


class ImportTests(PulseTestCase):
    def setUp(self):
        super().setUp()
        self.client, self.token = self.new_user(f"imp{id(self) % 100000}")
        self.h = {"X-CSRFToken": self.token}

    def upload(self, content, name):
        data = {"file": (io.BytesIO(content), name)}
        return self.client.post("/api/import", data=data, content_type="multipart/form-data", headers=self.h)

    def test_sample_csv_and_json(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "sample_import.csv"), "rb") as fh:
            self.assertEqual(self.upload(fh.read(), "sample_import.csv").get_json()["count"], 5)
        with open(os.path.join(root, "sample_import.json"), "rb") as fh:
            self.assertEqual(self.upload(fh.read(), "sample_import.json").get_json()["count"], 3)
        self.assertEqual(self.client.get("/api/logs").get_json()["total"], 8)

    def test_blank_hours_defaults_and_bad_rows_skipped(self):
        csv = (b"date,language,topic,hours_spent,mastery\n"
               b"2026-01-05,Python,NoHours,,4\n"
               b"not-a-date,Python,BadDate,1,3\n"
               b"2026-01-06,SQL,TooLong,99,3\n"
               b"2026-01-07,SQL,Fine,2.5,9\n")
        res = self.upload(csv, "mixed.csv").get_json()
        self.assertTrue(res["success"])
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["skipped"], 3)
        log = self.client.get("/api/logs").get_json()["logs"][0]
        self.assertEqual((log["topic"], log["hours_spent"]), ("NoHours", 1.0))

    def test_xlsx_import(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Date", "Stack", "Title", "Hours"])
        ws.append([date(2026, 2, 1), "Go", "Goroutines", 2])
        buf = io.BytesIO()
        wb.save(buf)
        res = self.upload(buf.getvalue(), "data.xlsx").get_json()
        self.assertEqual(res["count"], 1)
        log = self.client.get("/api/logs").get_json()["logs"][0]
        self.assertEqual((log["date"], log["language"], log["topic"]), ("2026-02-01", "Go", "Goroutines"))

    def test_rejections(self):
        self.assertEqual(self.upload(b"abc", "notes.txt").status_code, 400)
        self.assertEqual(self.upload(b"{bad json", "x.json").status_code, 400)
        self.assertEqual(self.upload(b"[1, 2]", "x.json").status_code, 400)
        self.assertEqual(self.upload(b"foo,bar\n1,2\n", "x.csv").status_code, 400)
        self.assertEqual(self.upload(b"not really excel", "x.xlsx").status_code, 400)
        res = self.client.post("/api/import", data={}, content_type="multipart/form-data", headers=self.h)
        self.assertEqual(res.status_code, 400)

    def test_upload_too_large_returns_json(self):
        old = pulse.app.config["MAX_CONTENT_LENGTH"]
        pulse.app.config["MAX_CONTENT_LENGTH"] = 1024
        try:
            res = self.upload(b"a" * 5000, "big.csv")
        finally:
            pulse.app.config["MAX_CONTENT_LENGTH"] = old
        self.assertEqual(res.status_code, 413)
        self.assertFalse(res.get_json()["success"])


class ExportTests(PulseTestCase):
    def test_excel_export_formulas_and_injection_safety(self):
        client, token = self.new_user("exporter")
        h = {"X-CSRFToken": token}
        client.post("/api/log", json={"topic": "=HYPERLINK(\"http://evil\")", "language": 'Py"thon*',
                                      "hours_spent": 2, "concepts_count": 3, "mastery_score": 5,
                                      "notes": "bell\x07char"}, headers=h)
        client.post("/api/log", json={"topic": "Normal", "language": "SQL", "hours_spent": 1,
                                      "concepts_count": 1, "mastery_score": 3}, headers=h)
        res = client.get("/export/excel")
        self.assertEqual(res.status_code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(res.data))
        logs = wb["Learning_Logs"]
        topics = [logs.cell(row=r, column=4) for r in (2, 3)]
        evil = next(c for c in topics if str(c.value).startswith("=HYPERLINK"))
        self.assertEqual(evil.data_type, "s")
        self.assertEqual(logs.cell(row=4, column=6).value, "=SUM(F2:F3)")
        summary = wb["Category_Summary"]
        formulas = [summary.cell(row=r, column=2).value for r in (2, 3)]
        self.assertIn('=COUNTIF(Learning_Logs!$C$2:$C$3, "=Py""thon~*")', formulas)

    def test_pdf_export_with_markup_in_topic(self):
        client, token = self.new_user("pdfmarkup")
        client.post("/api/log", json={"topic": "<b>unclosed & <i>tags", "hours_spent": 1,
                                      "concepts_count": 1, "mastery_score": 3}, headers={"X-CSRFToken": token})
        res = client.get("/export/pdf")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data.startswith(b"%PDF"))

    def test_sync_now_and_status(self):
        client, token = self.new_user("syncer")
        res = client.post("/api/sync/now", headers={"X-CSRFToken": token})
        self.assertEqual(res.status_code, 200, res.get_json())
        status = client.get("/api/sync/status").get_json()
        self.assertTrue(status["file_exists"])
        self.assertIsNotNone(status["last_sync"])


class AdminTests(PulseTestCase):
    def test_learner_forbidden(self):
        self.login()
        self.assertEqual(self.client.get("/api/admin/overview").status_code, 403)

    def test_admin_overview_and_role_change(self):
        token = self.login("admin", "Admin@123")
        data = self.client.get("/api/admin/overview").get_json()
        self.assertTrue(any(u["username"] == "learner" for u in data["users"]))
        target, _ = self.new_user("promoteme")
        uid = target.get("/api/session-status").get_json()["user"]["id"]
        h = {"X-CSRFToken": token}
        self.assertEqual(self.client.put(f"/api/admin/users/{uid}/role", json={"role": "mentor"}, headers=h).status_code, 200)
        self.assertEqual(target.get("/api/session-status").get_json()["user"]["role"], "mentor")
        self.assertEqual(self.client.put(f"/api/admin/users/{uid}/role", json={"role": "king"}, headers=h).status_code, 400)

    def test_last_admin_cannot_be_demoted(self):
        token = self.login("admin", "Admin@123")
        admin_id = self.client.get("/api/session-status").get_json()["user"]["id"]
        res = self.client.put(f"/api/admin/users/{admin_id}/role", json={"role": "learner"}, headers={"X-CSRFToken": token})
        self.assertEqual(res.status_code, 400)


class MiscTests(PulseTestCase):
    def test_api_404_is_json(self):
        res = self.client.get("/api/does-not-exist")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.content_type, "application/json")

    def test_html_404_page(self):
        res = self.client.get("/nope")
        self.assertEqual(res.status_code, 404)
        self.assertIn(b"404", res.data)

    def test_security_headers(self):
        res = self.client.get("/login")
        self.assertEqual(res.headers["X-Frame-Options"], "DENY")
        self.assertEqual(res.headers["X-Content-Type-Options"], "nosniff")

    def test_pwa_assets(self):
        for path in ("/manifest.json", "/sw.js", "/static/js/app.js"):
            res = self.client.get(path)
            self.assertEqual(res.status_code, 200)
            res.close()

    def test_validators_unit(self):
        self.assertEqual(validators.parse_hours("1.25"), (1.25, None))
        self.assertIsNotNone(validators.parse_hours(True)[1])
        self.assertEqual(validators.parse_date("2026-01-01"), ("2026-01-01", None))
        self.assertIsNotNone(validators.parse_date("1999-12-31")[1])
        self.assertIsNotNone(validators.validate_password("abcdefgh")[1])
        self.assertIsNone(validators.validate_password("abcdefg1")[1])


def tearDownModule():
    wait_for_background_syncs()
    shutil.rmtree(TMP_DIR, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

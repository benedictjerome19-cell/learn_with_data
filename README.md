# ⚡ Consistency Pulse™ - Full-Stack Learning Tracker & Velocity Engine

[![Flask](https://img.shields.io/badge/Flask-3.x-black?logo=flask)](https://palletsprojects.com/p/flask/)
[![Pandas](https://img.shields.io/badge/Pandas-2.x%20%2F%203.x-blue?logo=pandas)](https://pandas.pydata.org/)
[![TailwindCSS](https://img.shields.io/badge/TailwindCSS-v3.4-38bdf8?logo=tailwindcss)](https://tailwindcss.com/)
[![SQLite](https://img.shields.io/badge/SQLite-WAL-003B57?logo=sqlite)](https://sqlite.org/)
[![PWA](https://img.shields.io/badge/PWA-Ready-5A0FC8?logo=pwa)](https://web.dev/progressive-web-apps/)

An enterprise-grade, progressive web application (PWA) designed to engineer consistent learning habits, track deep-work velocity, visualize multi-stack competencies, and automate spreadsheet reporting.

---

## 🌟 Key Architecture & Pillars

### 1. Enhanced Usability & UI/UX
- **Sleek Responsive Dashboard:** Built with Tailwind CSS, custom glassmorphism (`backdrop-filter: blur(16px)`), modern typography (`Outfit` & `Inter`), and responsive layout.
- **Persistent Dark/Light Mode:** Instant theme toggle saved directly to `localStorage` with automated Chart.js grid and typography synchronization.
- **Drag-and-Drop Multi-Format Ingestion:** Instant bulk ingestion zone supporting `.csv`, `.xlsx`, `.xls`, and `.json` with flexible schema normalization and transactional rollback.
- **Micro-Interactions & Real-Time Validation:** Dynamic tooltips, non-blocking toast notification stack, and live form validation (green borders/alerts).

### 2. Advanced Data & Analytics Engine
- **Automated Rolling Improvement Metrics:** Real-time 7-day Moving Average (Weekly) vs. 30-day Moving Average (Monthly) curves with momentum velocity indicators (`+18.4% vs Monthly MA`).
- **GitHub-Style 52-Week Contribution Matrix:** 365+ daily study intervals across 7 weekdays with 5 intensity levels, hover tooltips displaying concept counts, hours, mastery ratings, and stack tags.
- **Multi-Language Custom Slicing:** Filter by **Python**, **SQL**, **Java**, **Docker**, **Machine Learning**, or custom stacks. All KPI cards, moving averages, and heatmap intensities adapt immediately.
- **Consistency Pulse & Streak Analytics:** Tracks current streak, longest streak, active days, and 365-day consistency rate percentage.

### 3. Robust Authentication & Security
- **Werkzeug Salted Password Hashing:** User passwords securely hashed using `pbkdf2:sha256` with unique salts.
- **Session Timeout Protection:** Configurable session lifetime (`SESSION_TIMEOUT_MINUTES=30`) with an in-app visual countdown badge.
- **Role-Based Profiles:** Multi-tier roles (`admin`, `mentor`, `learner`) with separate permissions and audit logs.
- **Secure Environment Config:** Centralized settings managed via `.env` and `python-dotenv`.

### 4. Seamless Excel Export & Cloud Sync
- **Real-Time Background Synchronization:** Background worker thread writes structured sheets into `python_summary.xlsx` with professional navy/indigo headers, alternating zebra stripes, and auto-adjusted column widths.
- **Native Excel Dynamic Formulas:** Generated sheets utilize live formulas (`=SUM(...)`, `=AVERAGE(...)`, `=COUNTIF(...)`, `=SUMIF(...)`, `=AVERAGEIF(...)`).
- **One-Click Exports:**
  - **Excel Workbook:** `python_summary.xlsx` with formatted logs, category breakdowns, and executive KPIs.
  - **Executive PDF Report:** Multi-column report generated via ReportLab with KPI scorecards, moving average tables, and mastery distribution.

---

## 🚀 Quickstart & Local Setup

### 1. Clone & Enter Directory
```bash
git clone <repo-url>
cd learn_with_data
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Then set a unique `SECRET_KEY` in `.env` (never reuse a key from documentation):
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```
If `SECRET_KEY` is missing or a known placeholder, the app generates a random key and stores it in `.secret_key`.

Key settings in `.env`:
```env
HOST=127.0.0.1
PORT=5000
SESSION_TIMEOUT_MINUTES=30
DATABASE_PATH=learn_with_data.db
EXCEL_SYNC_PATH=python_summary.xlsx
SESSION_COOKIE_SECURE=1      # browsers accept secure cookies on http://localhost; set 0 only for plain-HTTP LAN access
LOGIN_MAX_ATTEMPTS=5         # failed logins per username+IP before a lockout
LOGIN_LOCKOUT_MINUTES=15
```

### 4. Run the Application
```bash
python app.py
```
Open **`http://127.0.0.1:5000`** in your browser and sign in.

> **Pre-Seeded Demo Profiles** (created only when the database is empty):
> - **Learner:** Username: `learner` | Password: `Learner@123`
> - **Admin:** Username: `admin` | Password: `Admin@123`
>
> Change these passwords (avatar menu → Account) or remove the demo users before exposing the app to anyone else.

### 5. Run the Tests
```bash
python -m unittest discover -s tests -v
```
Tests use a temporary database and never touch `learn_with_data.db`.

### Features at a Glance
- Sign-in, registration (new accounts are always learners), password change, idle session timeout
- Log, edit, delete and search learning sessions; filter every chart and KPI by stack
- Bulk import from CSV / XLSX / XLS / JSON with per-row validation (invalid rows are skipped and reported)
- Excel workbook and PDF report downloads; background sync to `python_summary.xlsx`
- Admin console: user list with activity totals, role management, sync audit trail
- Light/dark theme, responsive layout, installable PWA

### Security Notes
- CSRF tokens on every form and API write, SameSite/HttpOnly session cookies
- Login throttling, open-redirect protection, security headers
- All user text is escaped in the dashboard, heatmap tooltips and PDF; Excel cells are written as text so imported values can never become formulas

---

## 🌐 Deployment Guide

### Deploying to Render
1. Create a new **Web Service** on [Render](https://render.com).
2. Connect your GitHub repository.
3. Configure the service:
   - **Environment:** `Python`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT`
4. In **Environment Variables**, set:
   - `SECRET_KEY`: `<your-secure-random-key>`
   - `FLASK_ENV`: `production`
   - `SESSION_TIMEOUT_MINUTES`: `30`
5. Click **Deploy Web Service**.

### Deploying to Hugging Face Spaces
1. Create a new Space on [Hugging Face](https://huggingface.co/spaces).
2. Select **Docker** or **Gradio/Streamlit/Static** (or Python Docker SDK).
3. If using standard Python SDK or Docker, create a `Dockerfile`:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 7860
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:7860"]
```
4. Push your code to the Hugging Face Git remote.

---

## 📂 Modular Code Structure

```
d:\learn_with_data\
├── app.py                # Core Flask app, routing, auth, CSRF, admin and API handlers
├── config.py             # python-dotenv settings and environment configuration
├── validators.py         # Shared input validation (logs, imports, accounts)
├── database.py           # SQLite schema, Werkzeug hashing, queries & demo data seeder
├── analytics.py          # Pandas/NumPy rolling moving averages and 52-week heatmap
├── excel_exporter.py     # Openpyxl background sync with dynamic formulas & styling
├── pdf_exporter.py       # ReportLab executive PDF report generator
├── requirements.txt      # Production dependencies
├── Procfile              # Render / Heroku production process definition
├── .env                  # Environment variables
├── .env.example          # Template environment file
├── sample_import.csv     # Sample CSV for testing drag-and-drop import
├── sample_import.json    # Sample JSON for testing drag-and-drop import
├── tests/
│   └── test_app.py       # Automated test suite (unittest)
├── static/
│   ├── css/
│   │   └── styles.css    # CSS alias
│   ├── js/
│   │   └── app.js        # Frontend controller (Chart.js, drag-drop, toasts, filters)
│   ├── styles.css        # Theme variables, GitHub heatmap, animations, glassmorphism
│   ├── manifest.json     # PWA manifest
│   └── sw.js             # Service worker for offline shell caching
└── templates/
    ├── index.html        # Enterprise dashboard template
    ├── login.html        # Authentication and demo account selector
    └── error.html        # Friendly 404/500 page
```
#   l e a r n _ w i t h _ d a t a  
 
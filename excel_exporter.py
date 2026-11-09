"""
Excel Exporter & Real-Time Background Synchronization Engine.
Writes enterprise-grade, styled Excel workbooks directly into 'python_summary.xlsx'
using openpyxl with:
- Professional corporate headers & zebra-stripe data formatting
- Native Excel dynamic summary formulas (=SUM, =AVERAGE, =SUMIF, =AVERAGEIF)
- Auto-adjusted column widths & cell number formatting
- Non-blocking background worker thread for real-time synchronization
- Per-user in-memory workbooks for downloads (no shared-file race between users)
"""

import io
import os
import logging
import tempfile
import threading
import pandas as pd
from datetime import datetime
import openpyxl
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from config import Config
from database import get_db_connection, record_sync_audit

logger = logging.getLogger(__name__)

# Thread lock for concurrent sync safety
_sync_lock = threading.Lock()


class ExcelSyncError(Exception):
    """Raised when the summary workbook cannot be written (e.g. it is open in Excel)."""


def _safe_text(value):
    """Removes characters Excel rejects."""
    if value is None:
        return ""
    return ILLEGAL_CHARACTERS_RE.sub("", str(value))


def _set_text(cell, value):
    cell.value = _safe_text(value)
    # openpyxl turns strings starting with '=' into formulas; user text must stay literal
    cell.data_type = "s"


def _criteria(value):
    """Builds an exact-match COUNTIF/SUMIF criteria literal for a language name."""
    escaped = _safe_text(value).replace("~", "~~").replace("*", "~*").replace("?", "~?").replace('"', '""')
    return f'"={escaped}"'


def _load_logs(user_id):
    conn = get_db_connection()
    try:
        if user_id:
            query = "SELECT * FROM learning_logs WHERE user_id = ? ORDER BY date DESC, id DESC"
            params = [user_id]
        else:
            query = "SELECT * FROM learning_logs ORDER BY date DESC, id DESC"
            params = []
        return pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()


def _save_workbook(wb, target):
    """Saves to a file-like object, or atomically to a path via a temp file + replace."""
    if hasattr(target, "write"):
        wb.save(target)
        return
    directory = os.path.dirname(os.path.abspath(target)) or "."
    fd, tmp_path = tempfile.mkstemp(suffix=".xlsx", dir=directory)
    os.close(fd)
    try:
        wb.save(tmp_path)
        os.replace(tmp_path, target)
    except PermissionError as exc:
        raise ExcelSyncError(
            f"Could not write '{os.path.basename(target)}'. Close it in Excel and try again."
        ) from exc
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def build_workbook_bytes(user_id):
    """Builds the user's workbook in memory for downloads. Returns (BytesIO, records_count)."""
    df = _load_logs(user_id)
    buffer = io.BytesIO()
    _save_workbook(build_workbook(df), buffer)
    buffer.seek(0)
    return buffer, len(df)


def sync_to_excel(user_id=None, target_path=None):
    """
    Synchronizes learning logs to a professional Excel workbook ('python_summary.xlsx').
    Includes raw logs, aggregated summary with dynamic formulas, and executive KPIs.
    """
    if target_path is None:
        target_path = Config.EXCEL_SYNC_PATH

    with _sync_lock:
        df = _load_logs(user_id)
        records_count = len(df)
        try:
            _save_workbook(build_workbook(df), target_path)
        except ExcelSyncError as exc:
            if user_id:
                record_sync_audit(user_id, "excel_sync", records_count, os.path.basename(target_path), "failed", str(exc))
            raise

        if user_id:
            message = "Structured sheets and formulas written successfully." if records_count else "Empty state sync"
            record_sync_audit(user_id, "excel_sync", records_count, os.path.basename(target_path), "success", message)

        return target_path, records_count


def build_workbook(df):
    """Creates the styled three-sheet workbook for a learning_logs DataFrame."""
    if df.empty:
        # Create a clean empty template
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Learning_Logs"
        ws.append(["ID", "Date", "Language", "Topic", "Concepts", "Hours", "Mastery", "Source", "Notes"])
        return wb

    df = df.reset_index(drop=True)
    wb = openpyxl.Workbook()

    # Color palettes
    navy_header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    cyan_header_fill = PatternFill(start_color="0F766E", end_color="0F766E", fill_type="solid")
    indigo_header_fill = PatternFill(start_color="4338CA", end_color="4338CA", fill_type="solid")
    zebra_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    total_fill = PatternFill(start_color="E2E8F0", end_color="E2E8F0", fill_type="solid")

    header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
    total_font = Font(name="Segoe UI", size=11, bold=True, color="0F172A")
    body_font = Font(name="Segoe UI", size=10, color="1E293B")

    thin_border_side = Side(border_style="thin", color="CBD5E1")
    cell_border = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)
    double_bottom_border = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=Side(border_style="double", color="1E293B"))

    # -------------------------------------------------------------
    # SHEET 1: Learning Logs
    # -------------------------------------------------------------
    ws_logs = wb.active
    ws_logs.title = "Learning_Logs"
    ws_logs.views.sheetView[0].showGridLines = True
    ws_logs.freeze_panes = "A2"

    headers = ["Log ID", "Date", "Language", "Topic / Concept Area", "Concepts Count", "Hours Spent", "Mastery (1-5)", "Source", "Notes"]
    ws_logs.append(headers)

    for col_idx in range(1, len(headers) + 1):
        cell = ws_logs.cell(row=1, column=col_idx)
        cell.fill = navy_header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center" if col_idx in [1, 2, 5, 6, 7] else "left", vertical="center")
        cell.border = cell_border
    ws_logs.row_dimensions[1].height = 26

    # Populate rows
    for r_idx, row in df.iterrows():
        row_num = r_idx + 2
        ws_logs.cell(row=row_num, column=1, value=int(row['id']))
        _set_text(ws_logs.cell(row=row_num, column=2), row['date'])
        _set_text(ws_logs.cell(row=row_num, column=3), row['language'])
        _set_text(ws_logs.cell(row=row_num, column=4), row['topic'])
        ws_logs.cell(row=row_num, column=5, value=int(row['concepts_count']))
        ws_logs.cell(row=row_num, column=6, value=float(row['hours_spent']))
        ws_logs.cell(row=row_num, column=7, value=int(row['mastery_score']))
        _set_text(ws_logs.cell(row=row_num, column=8), row['source'])
        _set_text(ws_logs.cell(row=row_num, column=9), row['notes'] if pd.notna(row['notes']) else "")

        # Styling data row
        is_zebra = (r_idx % 2 == 1)
        for c_idx in range(1, len(headers) + 1):
            cell = ws_logs.cell(row=row_num, column=c_idx)
            cell.font = body_font
            cell.border = cell_border
            if is_zebra:
                cell.fill = zebra_fill

            # Alignments & formats
            if c_idx in (1, 2):
                cell.alignment = Alignment(horizontal="center")
            elif c_idx == 5:
                cell.alignment = Alignment(horizontal="right")
                cell.number_format = '#,##0'
            elif c_idx == 6:
                cell.alignment = Alignment(horizontal="right")
                cell.number_format = '0.0'
            elif c_idx == 7:
                cell.alignment = Alignment(horizontal="center")
                cell.number_format = '0'
            else:
                cell.alignment = Alignment(horizontal="left")

    # Summary Row with Native Excel Formulas
    total_row_num = len(df) + 2
    last_data_row = total_row_num - 1
    ws_logs.cell(row=total_row_num, column=1, value="TOTALS & AVERAGES")
    ws_logs.merge_cells(start_row=total_row_num, start_column=1, end_row=total_row_num, end_column=4)

    ws_logs.cell(row=total_row_num, column=5, value=f"=SUM(E2:E{last_data_row})")
    ws_logs.cell(row=total_row_num, column=6, value=f"=SUM(F2:F{last_data_row})")
    ws_logs.cell(row=total_row_num, column=7, value=f"=AVERAGE(G2:G{last_data_row})")

    for c_idx in range(1, len(headers) + 1):
        cell = ws_logs.cell(row=total_row_num, column=c_idx)
        cell.fill = total_fill
        cell.font = total_font
        cell.border = double_bottom_border
        if c_idx in [5, 6]:
            cell.alignment = Alignment(horizontal="right")
            cell.number_format = '#,##0.0' if c_idx == 6 else '#,##0'
        elif c_idx == 7:
            cell.alignment = Alignment(horizontal="center")
            cell.number_format = '0.00'
    ws_logs.row_dimensions[total_row_num].height = 24

    # -------------------------------------------------------------
    # SHEET 2: Language Breakdown with Summary Formulas
    # -------------------------------------------------------------
    ws_lang = wb.create_sheet(title="Category_Summary")
    ws_lang.views.sheetView[0].showGridLines = True

    lang_headers = ["Language / Stack", "Total Sessions", "Total Hours (=SUMIF)", "Total Concepts (=SUMIF)", "Avg Mastery (=AVERAGEIF)", "% of Total Hours"]
    ws_lang.append(lang_headers)

    for col_idx in range(1, len(lang_headers) + 1):
        cell = ws_lang.cell(row=1, column=col_idx)
        cell.fill = indigo_header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center" if col_idx > 1 else "left")
        cell.border = cell_border
    ws_lang.row_dimensions[1].height = 26

    lang_range = f"Learning_Logs!$C$2:$C${last_data_row}"
    unique_languages = sorted(df['language'].dropna().unique().tolist())
    for idx, lang_name in enumerate(unique_languages):
        row_idx = idx + 2
        crit = _criteria(lang_name)
        _set_text(ws_lang.cell(row=row_idx, column=1), lang_name)
        ws_lang.cell(row=row_idx, column=2, value=f'=COUNTIF({lang_range}, {crit})')
        ws_lang.cell(row=row_idx, column=3, value=f'=SUMIF({lang_range}, {crit}, Learning_Logs!$F$2:$F${last_data_row})')
        ws_lang.cell(row=row_idx, column=4, value=f'=SUMIF({lang_range}, {crit}, Learning_Logs!$E$2:$E${last_data_row})')
        ws_lang.cell(row=row_idx, column=5, value=f'=AVERAGEIF({lang_range}, {crit}, Learning_Logs!$G$2:$G${last_data_row})')
        ws_lang.cell(row=row_idx, column=6, value=f'=IFERROR(C{row_idx}/Learning_Logs!$F${total_row_num}, 0)')

        is_zebra = (idx % 2 == 1)
        for c_idx in range(1, len(lang_headers) + 1):
            cell = ws_lang.cell(row=row_idx, column=c_idx)
            cell.font = body_font
            cell.border = cell_border
            if is_zebra:
                cell.fill = zebra_fill
            if c_idx in [2, 3, 4]:
                cell.alignment = Alignment(horizontal="right")
                cell.number_format = '#,##0.0' if c_idx == 3 else '#,##0'
            elif c_idx == 5:
                cell.alignment = Alignment(horizontal="center")
                cell.number_format = '0.00'
            elif c_idx == 6:
                cell.alignment = Alignment(horizontal="right")
                cell.number_format = '0.0%'

    # -------------------------------------------------------------
    # SHEET 3: Executive KPIs & Moving Average Meta
    # -------------------------------------------------------------
    ws_kpi = wb.create_sheet(title="Executive_KPIs")
    ws_kpi.views.sheetView[0].showGridLines = True

    kpi_headers = ["Key Performance Indicator", "Formula / Source", "Value Calculation", "Operational Insight"]
    ws_kpi.append(kpi_headers)

    for col_idx in range(1, len(kpi_headers) + 1):
        cell = ws_kpi.cell(row=1, column=col_idx)
        cell.fill = cyan_header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="left", vertical="center")
        cell.border = cell_border
    ws_kpi.row_dimensions[1].height = 26

    kpis = [
        ("Total Learning Sessions", f"=COUNTA(Learning_Logs!$A$2:$A${last_data_row})", "#,##0", "Total active study and coding sessions logged"),
        ("Cumulative Time Invested (Hours)", f"=SUM(Learning_Logs!$F$2:$F${last_data_row})", "#,##0.0", "Gross cumulative hours spent on upskilling"),
        ("Total Concepts Mastered", f"=SUM(Learning_Logs!$E$2:$E${last_data_row})", "#,##0", "Granular technical topics and competencies internalized"),
        ("Average Mastery Score (1 to 5)", f"=AVERAGE(Learning_Logs!$G$2:$G${last_data_row})", "0.00", "Mean subjective competence rating across all sessions"),
        ("Average Session Duration (Hours)", f"=AVERAGE(Learning_Logs!$F$2:$F${last_data_row})", "0.00", "Mean duration per focused learning sprint"),
        ("Sync Timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "@", "Automated background sync generation moment (server local time)")
    ]

    for k_idx, (kpi_name, formula, num_format, insight) in enumerate(kpis):
        r_idx = k_idx + 2
        ws_kpi.cell(row=r_idx, column=1, value=kpi_name)
        # Column B documents the formula as text; column C evaluates it
        _set_text(ws_kpi.cell(row=r_idx, column=2), formula)
        ws_kpi.cell(row=r_idx, column=3, value=formula)
        ws_kpi.cell(row=r_idx, column=4, value=insight)

        for c_idx in range(1, 5):
            cell = ws_kpi.cell(row=r_idx, column=c_idx)
            cell.font = body_font
            cell.border = cell_border
            if c_idx == 3 and num_format != "@":
                cell.number_format = num_format
                cell.alignment = Alignment(horizontal="right")
            else:
                cell.alignment = Alignment(horizontal="left")

    # Auto-adjust column widths across all sheets (capped so long notes stay readable)
    for sheet in wb.worksheets:
        for col in sheet.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = get_column_letter(col[0].column)
            sheet.column_dimensions[col_letter].width = min(max(max_len + 4, 12), 60)

    return wb


def trigger_background_excel_sync(user_id=None):
    """
    Spawns a non-blocking background thread to perform real-time synchronization
    without stalling the HTTP request/response loop.
    """
    def worker():
        try:
            sync_to_excel(user_id, Config.EXCEL_SYNC_PATH)
        except Exception:
            logger.exception("Background Excel sync failed")

    thread = threading.Thread(target=worker, daemon=True, name="ExcelSyncWorker")
    thread.start()
    return thread

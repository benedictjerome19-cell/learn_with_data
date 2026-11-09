"""
PDF Exporter Module using ReportLab.
Generates an executive-ready, highly aesthetic PDF summary report:
- Executive Scorecards (Hours, Concepts, Streaks, Consistency Pulse)
- Rolling Moving Averages (7-Day vs 30-Day Momentum)
- Language & Technology Stack Breakdown
- High-Impact Recent Learning Records
"""

import io
from datetime import datetime
from xml.sax.saxutils import escape
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas

class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas for total page numbering and clean running header/footer."""
    def __init__(self, *args, **kwargs):
        super(NumberedCanvas, self).__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            super(NumberedCanvas, self).showPage()
        super(NumberedCanvas, self).save()

    def draw_page_number(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 9)
        self.setFillColor(colors.HexColor("#64748B"))
        
        # Running header rule
        self.setStrokeColor(colors.HexColor("#E2E8F0"))
        self.setLineWidth(0.5)
        self.line(40, letter[1] - 40, letter[0] - 40, letter[1] - 40)
        self.drawString(42, letter[1] - 34, "Consistency Pulse™ • Executive Learning Analytics Report")
        
        # Running footer rule
        self.line(40, 40, letter[0] - 40, 40)
        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(letter[0] - 42, 28, page_str)
        self.drawString(42, 28, f"Confidential • Generated {datetime.now().strftime('%b %d, %Y %H:%M')}")
        self.restoreState()

def _truncate(text, limit):
    return text if len(text) <= limit else text[:limit - 1] + "…"


def generate_pdf_report(user_data, analytics_data, recent_logs):
    """
    Constructs a PDF document stream from user analytics data.
    Returns a BytesIO buffer ready to be sent in Flask response.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=40,
        rightMargin=40,
        topMargin=55,
        bottomMargin=55
    )

    styles = getSampleStyleSheet()
    
    # Custom Typography Styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=22,
        leading=26,
        textColor=colors.HexColor("#0F172A")
    )
    
    subtitle_style = ParagraphStyle(
        'DocSub',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#64748B")
    )

    section_heading = ParagraphStyle(
        'SecHead',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#1E293B"),
        spaceBefore=14,
        spaceAfter=6
    )

    kpi_number = ParagraphStyle(
        'KPINum',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=16,
        leading=18,
        textColor=colors.HexColor("#4338CA"),
        alignment=1 # Center
    )

    kpi_label = ParagraphStyle(
        'KPILbl',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#475569"),
        alignment=1 # Center
    )

    cell_style = ParagraphStyle(
        'Cell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#1E293B")
    )

    cell_bold = ParagraphStyle(
        'CellBold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#1E293B")
    )

    story = []

    # 1. Header Block
    story.append(Paragraph("Learning & Consistency Pulse™ Report", title_style))
    story.append(Spacer(1, 4))
    
    # ReportLab Paragraphs parse markup, so every user-supplied value is escaped
    username = escape(str(user_data.get('username', 'Learner')))
    role = escape(str(user_data.get('role', 'Learner')).upper())
    gen_time = datetime.now().strftime("%B %d, %Y at %I:%M %p")
    story.append(Paragraph(f"Candidate: <b>{username}</b> &nbsp;|&nbsp; Role: <b>{role}</b> &nbsp;|&nbsp; Evaluated: <b>{gen_time}</b>", subtitle_style))
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#4338CA"), spaceAfter=14))

    # 2. Executive KPI Scorecards
    kpis = analytics_data.get("kpis", {})
    
    kpi_card_data = [
        [
            Paragraph(f"{kpis.get('total_hours', 0)} hrs", kpi_number),
            Paragraph(f"{kpis.get('total_concepts', 0)}", kpi_number),
            Paragraph(f"{kpis.get('current_streak', 0)} days", kpi_number),
            Paragraph(f"{kpis.get('consistency_rate', 0)}%", kpi_number),
            Paragraph(f"{kpis.get('avg_mastery', 0)} / 5", kpi_number)
        ],
        [
            Paragraph("Total Hours Invested", kpi_label),
            Paragraph("Concepts Internalized", kpi_label),
            Paragraph("Active Streak", kpi_label),
            Paragraph("Consistency Pulse", kpi_label),
            Paragraph("Avg Mastery Score", kpi_label)
        ]
    ]
    
    col_w = (letter[0] - 80) / 5
    kpi_table = Table(kpi_card_data, colWidths=[col_w]*5)
    kpi_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E1")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 14))

    # 3. Rolling Moving Average & Momentum Insights
    story.append(Paragraph("Rolling Improvement & Momentum Engine", section_heading))
    velocity = kpis.get("improvement_velocity", 0.0)
    velocity_sign = "+" if velocity > 0 else ""
    velocity_color = "#10B981" if velocity >= 0 else "#EF4444"

    rolling_data = [
        [
            Paragraph("<b>Rolling Metric</b>", cell_bold),
            Paragraph("<b>7-Day Moving Avg (Weekly)</b>", cell_bold),
            Paragraph("<b>30-Day Moving Avg (Monthly)</b>", cell_bold),
            Paragraph("<b>Improvement Velocity</b>", cell_bold)
        ],
        [
            Paragraph("Daily Learning Hours", cell_style),
            Paragraph(f"<b>{kpis.get('weekly_ma_hours', 0)} hrs/day</b>", cell_style),
            Paragraph(f"{kpis.get('monthly_ma_hours', 0)} hrs/day", cell_style),
            Paragraph(f'<font color="{velocity_color}"><b>{velocity_sign}{velocity}%</b></font> momentum', cell_style)
        ]
    ]
    rolling_table = Table(rolling_data, colWidths=[150, 130, 130, 120])
    rolling_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1E293B")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor("#FFFFFF")),
    ]))
    story.append(rolling_table)
    story.append(Spacer(1, 14))

    # 4. Multi-Language / Technology Stack Breakdown
    story.append(Paragraph("Technology Stack & Mastery Distribution", section_heading))
    langs_dist = analytics_data.get("languages", {}).get("distribution", [])
    
    lang_table_data = [
        [
            Paragraph("<b>Technology Stack</b>", cell_bold),
            Paragraph("<b>Total Hours</b>", cell_bold),
            Paragraph("<b>Concepts</b>", cell_bold),
            Paragraph("<b>Avg Mastery</b>", cell_bold),
            Paragraph("<b>Sessions</b>", cell_bold),
            Paragraph("<b>Share (%)</b>", cell_bold)
        ]
    ]

    for item in langs_dist[:8]:
        lang_table_data.append([
            Paragraph(f"<b>{escape(str(item.get('language')))}</b>", cell_style),
            Paragraph(f"{item.get('hours')} hrs", cell_style),
            Paragraph(f"{item.get('concepts')}", cell_style),
            Paragraph(f"{item.get('avg_mastery')} / 5", cell_style),
            Paragraph(f"{item.get('sessions')}", cell_style),
            Paragraph(f"{item.get('percentage')}%", cell_style),
        ])

    if not langs_dist:
        lang_table_data.append([Paragraph("No learning sessions logged yet.", cell_style), '', '', '', '', ''])

    lang_table = Table(lang_table_data, colWidths=[110, 85, 75, 85, 75, 100])
    lang_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#4338CA")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor("#FFFFFF"), colors.HexColor("#F8FAFC")]),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(lang_table)
    story.append(Spacer(1, 14))

    # 5. Recent High-Impact Learning Milestones
    story.append(Paragraph("Recent Learning Logs & Milestones", section_heading))
    logs_table_data = [
        [
            Paragraph("<b>Date</b>", cell_bold),
            Paragraph("<b>Stack</b>", cell_bold),
            Paragraph("<b>Concept / Topic Area</b>", cell_bold),
            Paragraph("<b>Hours</b>", cell_bold),
            Paragraph("<b>Mastery</b>", cell_bold)
        ]
    ]

    for log in recent_logs[:12]:
        logs_table_data.append([
            Paragraph(escape(str(log.get('date'))), cell_style),
            Paragraph(escape(str(log.get('language'))), cell_style),
            Paragraph(escape(_truncate(str(log.get('topic')), 60)), cell_style),
            Paragraph(f"{log.get('hours_spent')} h", cell_style),
            Paragraph(f"{log.get('mastery_score')}/5", cell_style),
        ])

    if not recent_logs:
        logs_table_data.append([Paragraph("No learning sessions logged yet.", cell_style), '', '', '', ''])

    logs_table = Table(logs_table_data, colWidths=[70, 75, 260, 60, 65])
    logs_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#0F766E")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor("#FFFFFF"), colors.HexColor("#F8FAFC")]),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(logs_table)

    # Build PDF with dynamic page numbering
    doc.build(story, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    return buffer

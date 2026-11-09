"""
Analytics Engine for Learning Tracker & Consistency Pulse.
Leverages Pandas and NumPy to compute:
- 7-day vs 30-day rolling moving averages & velocity/momentum metrics
- 52-week GitHub-style contribution matrix with tooltips and mastery scores
- Multi-language custom slice-and-dice metrics & distribution stats
- Active streaks, consistency pulse scores, and mastery trends
"""

import html
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from database import get_db_connection

def compute_user_analytics(user_id, selected_language=None):
    """
    Computes comprehensive analytics for a user:
    - Summary KPI Scorecards
    - 7-Day & 30-Day Rolling Moving Averages for time-series charts
    - 52-Week Contribution Matrix (GitHub style)
    - Language distribution & mastery radar data
    - Streak and consistency pulse calculations
    """
    conn = get_db_connection()
    
    # Query all logs for this user
    query = """
        SELECT id, date, language, topic, concepts_count, hours_spent, mastery_score, notes, source
        FROM learning_logs
        WHERE user_id = ?
        ORDER BY date ASC
    """
    try:
        df = pd.read_sql_query(query, conn, params=[user_id])
    finally:
        conn.close()

    # Normalize dates; a malformed legacy row is skipped instead of breaking the whole dashboard
    df['date'] = pd.to_datetime(df['date'].astype(str).str[:10], format='%Y-%m-%d', errors='coerce')
    df = df.dropna(subset=['date'])

    # If user has no data, return baseline structure
    if df.empty:
        return get_empty_analytics_payload(selected_language)

    df['date_str'] = df['date'].dt.strftime('%Y-%m-%d')
    df['hours_spent'] = pd.to_numeric(df['hours_spent'], errors='coerce').fillna(0)
    df['concepts_count'] = pd.to_numeric(df['concepts_count'], errors='coerce').fillna(0).astype(int)
    df['mastery_score'] = pd.to_numeric(df['mastery_score'], errors='coerce').fillna(1).astype(int)

    # Calculate overall language distributions BEFORE language filtering
    lang_distribution = compute_language_distribution(df)

    # Available languages for UI filters
    all_languages = sorted(df['language'].dropna().unique().tolist())

    # Apply language filter if requested
    filtered_df = df.copy()
    if selected_language and selected_language.lower() != 'all':
        filtered_df = df[df['language'].str.lower() == selected_language.lower()].copy()

    # Summary KPIs
    total_hours = round(float(filtered_df['hours_spent'].sum()), 1)
    total_concepts = int(filtered_df['concepts_count'].sum())
    total_sessions = len(filtered_df)
    avg_mastery = round(float(filtered_df['mastery_score'].mean()), 2) if not filtered_df.empty else 0.0

    # 52-week date range timeline
    today = pd.to_datetime(datetime.now().date())
    # Start on the Monday 52 weeks ago
    start_date = today - pd.Timedelta(weeks=52)
    start_date = start_date - pd.Timedelta(days=start_date.weekday()) # Snap to Monday
    
    full_date_range = pd.date_range(start=start_date, end=today, freq='D')

    daily_agg = build_daily_aggregate(filtered_df, full_date_range)

    # 1. Rolling Moving Averages (7-Day vs 30-Day)
    daily_agg['ma_7'] = daily_agg['hours'].rolling(window=7, min_periods=1).mean().round(2)
    daily_agg['ma_30'] = daily_agg['hours'].rolling(window=30, min_periods=1).mean().round(2)
    
    daily_agg['concepts_ma_7'] = daily_agg['concepts'].rolling(window=7, min_periods=1).mean().round(2)
    daily_agg['concepts_ma_30'] = daily_agg['concepts'].rolling(window=30, min_periods=1).mean().round(2)

    # Rolling Momentum / Improvement Velocity metric
    latest_ma_7 = float(daily_agg['ma_7'].iloc[-1]) if not daily_agg.empty else 0.0
    latest_ma_30 = float(daily_agg['ma_30'].iloc[-1]) if not daily_agg.empty else 0.0
    
    if latest_ma_30 > 0:
        improvement_velocity = round(((latest_ma_7 - latest_ma_30) / latest_ma_30) * 100, 1)
    else:
        improvement_velocity = 0.0

    # Prepare time-series chart data (last 90 days for crisp visualization)
    recent_trend_slice = daily_agg.tail(90)
    chart_series = {
        "labels": [d.strftime("%b %d") for d in recent_trend_slice.index],
        "daily_hours": [round(float(h), 1) for h in recent_trend_slice['hours']],
        "ma_7": [round(float(m), 2) for m in recent_trend_slice['ma_7']],
        "ma_30": [round(float(m), 2) for m in recent_trend_slice['ma_30']],
        "concepts_ma_7": [round(float(c), 2) for c in recent_trend_slice['concepts_ma_7']],
        "concepts_ma_30": [round(float(c), 2) for c in recent_trend_slice['concepts_ma_30']],
    }

    # 2. 52-Week GitHub-style Contribution Heatmap Matrix
    heatmap_matrix, month_headers = build_52_week_matrix(daily_agg, full_date_range, today)

    # 3. Consistency Pulse & Streaks
    current_streak, longest_streak, active_days_count, consistency_rate = calculate_streaks_and_pulse(daily_agg)

    return {
        "kpis": {
            "total_hours": total_hours,
            "total_concepts": total_concepts,
            "total_sessions": total_sessions,
            "avg_mastery": avg_mastery,
            "weekly_ma_hours": round(latest_ma_7, 2),
            "monthly_ma_hours": round(latest_ma_30, 2),
            "improvement_velocity": improvement_velocity,
            "current_streak": current_streak,
            "longest_streak": longest_streak,
            "active_days": active_days_count,
            "consistency_rate": consistency_rate,
            "selected_language": selected_language or "all"
        },
        "charts": chart_series,
        "heatmap": {
            "matrix": heatmap_matrix,
            "month_headers": month_headers
        },
        "languages": {
            "available": all_languages,
            "distribution": lang_distribution
        }
    }

def build_daily_aggregate(filtered_df, full_date_range):
    """Per-day totals over the full 52-week range (days without logs are zero-filled)."""
    if filtered_df.empty:
        daily_agg = pd.DataFrame(index=full_date_range)
        daily_agg['hours'] = 0.0
        daily_agg['concepts'] = 0
        daily_agg['mastery'] = 0.0
        daily_agg['languages'] = [[] for _ in range(len(full_date_range))]
        return daily_agg

    daily_agg = filtered_df.groupby('date').agg(
        hours=('hours_spent', 'sum'),
        concepts=('concepts_count', 'sum'),
        mastery=('mastery_score', 'mean'),
        languages=('language', lambda x: sorted(set(x)))
    ).reindex(full_date_range)

    daily_agg['hours'] = daily_agg['hours'].fillna(0.0).round(2)
    daily_agg['concepts'] = daily_agg['concepts'].fillna(0).astype(int)
    daily_agg['mastery'] = daily_agg['mastery'].fillna(0.0)
    return daily_agg


def build_52_week_matrix(daily_agg, full_date_range, today):
    """
    Constructs the 52-week contribution matrix grid:
    7 rows (Days: Mon-Sun) x 52+ columns (Weeks)
    with intensity levels, concept counts, hours, mastery, and rich tooltips.
    """
    matrix_cells = []
    month_headers = []
    
    last_month = None
    week_col_idx = 0
    current_week_cells = []
    
    for idx, single_date in enumerate(full_date_range):
        date_str = single_date.strftime("%Y-%m-%d")
        day_of_week = single_date.weekday() # 0 = Mon, 6 = Sun
        month_name = single_date.strftime("%b")

        row = daily_agg.loc[single_date]
        hours = float(row['hours'])
        concepts = int(row['concepts'])
        mastery = round(float(row['mastery']), 1) if row['mastery'] > 0 else 0
        langs = row['languages'] if isinstance(row['languages'], list) else []
        # Language names are user-supplied; the tooltip is rendered as HTML, so escape them
        langs_str = html.escape(", ".join(langs)) if langs else "No entries"

        # GitHub-style 5 intensity levels
        if hours == 0:
            level = 0
        elif hours < 1.0:
            level = 1
        elif hours < 2.5:
            level = 2
        elif hours < 4.0:
            level = 3
        else:
            level = 4

        # Human-readable formatted tooltip
        day_display = single_date.strftime("%A, %b %d, %Y")
        if hours > 0:
            tooltip = f"<strong>{day_display}</strong><br/>⏱️ {hours} hrs • 💡 {concepts} concept{'s' if concepts != 1 else ''}<br/>⭐ Mastery: {mastery}/5 • 🏷️ {langs_str}"
        else:
            tooltip = f"<strong>{day_display}</strong><br/>Rest day (No learning logged)"

        cell = {
            "date": date_str,
            "day_name": single_date.strftime("%a"),
            "day_of_week": day_of_week,
            "week_col": week_col_idx,
            "hours": hours,
            "concepts": concepts,
            "mastery": mastery,
            "level": level,
            "tooltip": tooltip,
            "is_future": single_date > today
        }
        current_week_cells.append(cell)

        # Track month header positions
        if day_of_week == 0:
            if month_name != last_month:
                month_headers.append({
                    "name": month_name,
                    "week_col": week_col_idx
                })
                last_month = month_name

        # If Sunday or last item, finalize column
        if day_of_week == 6 or idx == len(full_date_range) - 1:
            matrix_cells.append(current_week_cells)
            current_week_cells = []
            week_col_idx += 1

    return matrix_cells, month_headers

def calculate_streaks_and_pulse(daily_agg):
    """Calculates active streak, longest streak, and consistency pulse percentage."""
    active_mask = (daily_agg['hours'] > 0).tolist()
    
    current_streak = 0
    longest_streak = 0
    temp_streak = 0
    
    # Calculate longest streak
    for is_active in active_mask:
        if is_active:
            temp_streak += 1
            if temp_streak > longest_streak:
                longest_streak = temp_streak
        else:
            temp_streak = 0
            
    # Calculate current streak backwards from today. Today not being logged yet
    # does not break the streak; the count then starts from yesterday.
    recent_days = list(reversed(active_mask))
    if recent_days and not recent_days[0]:
        recent_days = recent_days[1:]
    for is_active in recent_days:
        if is_active:
            current_streak += 1
        else:
            break

    # Active days in past 365 days
    active_days_count = sum(active_mask)
    total_days = len(active_mask)
    consistency_rate = round((active_days_count / total_days) * 100, 1) if total_days > 0 else 0.0

    return current_streak, longest_streak, active_days_count, consistency_rate

def compute_language_distribution(df):
    """Calculates breakdown by language: hours, concepts, mastery, and percentage share."""
    if df.empty:
        return []

    grouped = df.groupby('language').agg(
        hours=('hours_spent', 'sum'),
        concepts=('concepts_count', 'sum'),
        avg_mastery=('mastery_score', 'mean'),
        sessions=('id', 'count')
    ).reset_index()

    total_hours = grouped['hours'].sum()
    
    result = []
    for _, row in grouped.iterrows():
        pct = round((row['hours'] / total_hours * 100), 1) if total_hours > 0 else 0
        result.append({
            "language": row['language'],
            "hours": round(float(row['hours']), 1),
            "concepts": int(row['concepts']),
            "avg_mastery": round(float(row['avg_mastery']), 1),
            "sessions": int(row['sessions']),
            "percentage": pct
        })
        
    return sorted(result, key=lambda x: x['hours'], reverse=True)

def get_empty_analytics_payload(selected_language=None):
    """Fallback payload when no data exists (still includes an empty 52-week grid)."""
    today = pd.to_datetime(datetime.now().date())
    start_date = today - pd.Timedelta(weeks=52)
    start_date = start_date - pd.Timedelta(days=start_date.weekday())
    full_date_range = pd.date_range(start=start_date, end=today, freq='D')
    daily_agg = build_daily_aggregate(pd.DataFrame(), full_date_range)
    matrix, month_headers = build_52_week_matrix(daily_agg, full_date_range, today)
    return {
        "kpis": {
            "total_hours": 0.0,
            "total_concepts": 0,
            "total_sessions": 0,
            "avg_mastery": 0.0,
            "weekly_ma_hours": 0.0,
            "monthly_ma_hours": 0.0,
            "improvement_velocity": 0.0,
            "current_streak": 0,
            "longest_streak": 0,
            "active_days": 0,
            "consistency_rate": 0.0,
            "selected_language": selected_language or "all"
        },
        "charts": {"labels": [], "daily_hours": [], "ma_7": [], "ma_30": [], "concepts_ma_7": [], "concepts_ma_30": []},
        "heatmap": {"matrix": matrix, "month_headers": month_headers},
        "languages": {"available": [], "distribution": []}
    }

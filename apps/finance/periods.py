"""Resolve a period key (from the dashboard/report period picker) into a
concrete (start_date, end_date, label) tuple, honoring the organization's
financial-year start month.
"""
import datetime
from dataclasses import dataclass


@dataclass(frozen=True)
class Period:
    start: datetime.date
    end: datetime.date
    label: str
    key: str


def _fy_bounds_for(anchor: datetime.date, fy_start_month: int) -> tuple[datetime.date, datetime.date]:
    """The financial year that contains `anchor`, given the FY starts on the
    1st of `fy_start_month`."""
    if anchor.month >= fy_start_month:
        start = datetime.date(anchor.year, fy_start_month, 1)
    else:
        start = datetime.date(anchor.year - 1, fy_start_month, 1)
    end_year = start.year + 1
    end = datetime.date(end_year, fy_start_month, 1) - datetime.timedelta(days=1)
    return start, end


def _fy_label(start: datetime.date) -> str:
    end_year_short = str((start.year + 1) % 100).zfill(2)
    return f"FY {start.year}-{end_year_short}"


def resolve_period(
    key: str,
    fy_start_month: int,
    *,
    today: datetime.date | None = None,
    custom_from: datetime.date | None = None,
    custom_to: datetime.date | None = None,
) -> Period:
    today = today or datetime.date.today()

    if key == "today":
        return Period(today, today, "Today", key)

    if key == "this_week":
        start = today - datetime.timedelta(days=today.weekday())
        return Period(start, today, "This week", key)

    if key == "this_month":
        start = today.replace(day=1)
        return Period(start, today, today.strftime("%B %Y"), key)

    if key == "last_month":
        first_of_this_month = today.replace(day=1)
        end = first_of_this_month - datetime.timedelta(days=1)
        start = end.replace(day=1)
        return Period(start, end, start.strftime("%B %Y"), key)

    if key == "this_quarter":
        q_start_month = ((today.month - 1) // 3) * 3 + 1
        start = today.replace(month=q_start_month, day=1)
        return Period(start, today, f"Q{(q_start_month - 1) // 3 + 1} {today.year}", key)

    if key == "this_fy":
        start, end = _fy_bounds_for(today, fy_start_month)
        end = min(end, today)
        return Period(start, end, _fy_label(start), key)

    if key == "last_fy":
        this_fy_start, _ = _fy_bounds_for(today, fy_start_month)
        last_fy_end = this_fy_start - datetime.timedelta(days=1)
        last_fy_start, _ = _fy_bounds_for(last_fy_end, fy_start_month)
        return Period(last_fy_start, last_fy_end, _fy_label(last_fy_start), key)

    if key == "year_to_date":
        start = today.replace(month=1, day=1)
        return Period(start, today, f"{today.year} (calendar YTD)", key)

    if key == "custom" and custom_from and custom_to:
        return Period(custom_from, custom_to, f"{custom_from:%d %b %Y} – {custom_to:%d %b %Y}", key)

    # default fallback
    start = today.replace(day=1)
    return Period(start, today, today.strftime("%B %Y"), "this_month")


def previous_period(period: Period) -> Period:
    """A same-length period immediately preceding `period`, for comparisons."""
    span = (period.end - period.start).days + 1
    prev_end = period.start - datetime.timedelta(days=1)
    prev_start = prev_end - datetime.timedelta(days=span - 1)
    return Period(prev_start, prev_end, f"{prev_start:%d %b} – {prev_end:%d %b}", "previous")


PERIOD_CHOICES = [
    ("today", "Today"),
    ("this_week", "This week"),
    ("this_month", "This month"),
    ("last_month", "Last month"),
    ("this_quarter", "This quarter"),
    ("this_fy", "This financial year"),
    ("last_fy", "Last financial year"),
    ("year_to_date", "Year to date"),
    ("custom", "Custom range"),
]

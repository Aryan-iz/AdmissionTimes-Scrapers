import re
from datetime import datetime, date

from dateutil import parser as date_parser

_YEAR = re.compile(r"\b(19|20)\d{2}\b")


def normalize_to_iso(value, today=None):
    """Normalize a date-like value (str/date/datetime) to ISO YYYY-MM-DD, or None.

    A year written in the source is never changed: a deadline that has passed
    must stay in the past (rewriting "December 20, 2025" to 2026 published
    closed admissions as open for another year).

    Only when the text has no year ("20 December") is one inferred: the
    current year, or next year when that date is more than 30 days behind us
    (an announcement for an upcoming intake).
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.date().strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")

    text = str(value).strip()
    if not text:
        return None

    today = today or datetime.now().date()
    try:
        parsed = date_parser.parse(text, fuzzy=True, default=datetime(today.year, 1, 1)).date()
    except (ValueError, OverflowError):
        return None

    if not _YEAR.search(text) and (today - parsed).days > 30:
        try:
            parsed = parsed.replace(year=parsed.year + 1)
        except ValueError:  # 29 February
            parsed = parsed.replace(year=parsed.year + 1, day=28)

    return parsed.strftime("%Y-%m-%d")

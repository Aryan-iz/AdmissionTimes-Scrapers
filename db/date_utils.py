from datetime import datetime, date
from dateutil import parser as date_parser


def normalize_to_iso(value):
    """Normalize a date-like value (str/date/datetime) to ISO YYYY-MM-DD.

    Heuristics:
    - If the parsed year is older than current year and the date is in the past,
      prefer replacing the year with the current year when the resulting date
      is in the future or near-future.
    - Returns None on failure.
    """
    if value is None:
        return None

    # If already a date/datetime
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, datetime):
        return value.date().strftime("%Y-%m-%d")

    text = str(value).strip()
    if not text:
        return None

    now = datetime.now()
    current_year = now.year

    # Try to parse as-is
    try:
        dt = date_parser.parse(text, fuzzy=True, default=now)
        parsed = dt.date()
    except Exception:
        parsed = None

    if not parsed:
        return None

    # If parsed year is much older (e.g., previous year) and results in a past date,
    # try replacing the year with current year and see if it makes sense.
    if parsed.year < current_year:
        try:
            alt = parsed.replace(year=current_year)
            # If alt is same or in the future (within reasonable window), prefer it.
            if alt >= now.date() or (now.date() - alt).days < 30:
                parsed = alt
        except Exception:
            pass

    return parsed.strftime("%Y-%m-%d")

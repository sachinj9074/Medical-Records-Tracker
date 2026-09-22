"""Handle-with-care surfaces: follow-up reminders (and, later, lab trends).

Pure re-presentation of stored facts: no model calls, no clinical computation.
Follow-ups surface only the clinician's own written follow_up text; the due date
is plain arithmetic on the interval they stated, always approximate, with the
verbatim text kept alongside. Nothing here assesses urgency or gives advice.

See PROJECT_SPEC.md sections 8, 10, 15.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

_UNIT_DAYS = {
    "day": 1, "days": 1, "d": 1,
    "week": 7, "weeks": 7, "wk": 7, "wks": 7, "w": 7,
    "month": 30, "months": 30, "mo": 30,
    "year": 365, "years": 365, "yr": 365,
}
_WORD_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
             "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12}

_NUM_INTERVAL = re.compile(r"(\d+)\s*(days?|d|weeks?|wks?|w|months?|mo|years?|yr)\b")
_WORD_INTERVAL = re.compile(
    r"\b(a|an|one|two|three|four|five|six|seven|eight|nine|ten|twelve)\s+(day|week|month|year)s?\b")


def _parse_date(s):
    if not isinstance(s, str):
        return None
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        return None


def parse_interval_days(text: str):
    """The interval in days stated in a follow-up ('6 weeks', '5 days', '3d',
    'in 2 weeks', 'a week', 'one month'), or None if none is stated."""
    if not isinstance(text, str):
        return None
    t = text.lower()
    m = _NUM_INTERVAL.search(t)
    if m:
        return int(m.group(1)) * _UNIT_DAYS[m.group(2)]
    m = _WORD_INTERVAL.search(t)
    if m:
        return _WORD_NUM[m.group(1)] * _UNIT_DAYS[m.group(2)]
    return None


@dataclass
class FollowUp:
    record_id: str | None
    record_date: str | None
    text: str            # verbatim, as the clinician wrote it
    due_date: str | None  # approximate ISO date, or None if no interval was stated
    overdue: bool = False


def parse_followups(records: list, *, today=None) -> list:
    """Follow-ups across all records: the verbatim text plus an approximate due
    date (record date + the stated interval). Sorted most-overdue/soonest first,
    undated last."""
    today = today or datetime.date.today()
    today_iso = today.isoformat()
    out = []
    for r in records:
        text = r.get("follow_up")
        if not isinstance(text, str) or not text.strip():
            continue
        text = text.strip()
        rdate = r.get("record_date") if isinstance(r.get("record_date"), str) else None
        base = _parse_date(rdate)
        days = parse_interval_days(text)
        due = (base + datetime.timedelta(days=days)).isoformat() if (base and days is not None) else None
        out.append(FollowUp(record_id=r.get("record_id"), record_date=rdate, text=text,
                            due_date=due, overdue=bool(due and due < today_iso)))
    out.sort(key=lambda f: (f.due_date is None, f.due_date or ""))
    return out


# --- calendar (.ics) --------------------------------------------------------

def _ics_escape(s: str) -> str:
    return (str(s).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def ics_event(followup: FollowUp, *, title_prefix: str = "Medical follow-up") -> str | None:
    """A minimal all-day VEVENT for one follow-up, for the user to import into
    their own calendar. Returns None if there is no date to place it on."""
    when = _parse_date(followup.due_date or followup.record_date)
    if when is None:
        return None
    start = when.strftime("%Y%m%d")
    end = (when + datetime.timedelta(days=1)).strftime("%Y%m%d")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    uid = f"mrt-{followup.record_id or 'x'}-{start}@medical-records-tracker"
    desc = (f"From your record dated {followup.record_date or 'unknown'}. Text as written "
            "by the clinician; approximate date, not medical advice.")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Medical Records Tracker//EN",
        "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}",
        f"DTSTART;VALUE=DATE:{start}", f"DTEND;VALUE=DATE:{end}",
        f"SUMMARY:{_ics_escape(f'{title_prefix}: {followup.text}')}",
        f"DESCRIPTION:{_ics_escape(desc)}",
        "END:VEVENT", "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"

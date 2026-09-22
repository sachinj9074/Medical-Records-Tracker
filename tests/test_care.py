"""Tests for handle-with-care surfaces (care.py). Deterministic; synthetic records.
A fixed `today` is injected so overdue/upcoming are stable."""

import datetime

from src import care


TODAY = datetime.date(2026, 3, 20)


def rec(rid, date, follow_up=None):
    return {"record_id": rid, "record_date": date, "follow_up": follow_up,
            "document_type": "prescription", "medications": [], "investigations": []}


# --- interval parsing -------------------------------------------------------

def test_parse_interval_numeric_and_compact():
    assert care.parse_interval_days("Repeat TSH after 6 weeks.") == 42
    assert care.parse_interval_days("Review after 5 days if cough persists.") == 5
    assert care.parse_interval_days("Review 3d") == 3
    assert care.parse_interval_days("Review after 1 week.") == 7
    assert care.parse_interval_days("Review if not settling in 2 weeks.") == 14
    assert care.parse_interval_days("after 3 months") == 90


def test_parse_interval_word_numbers():
    assert care.parse_interval_days("review in a week") == 7
    assert care.parse_interval_days("follow up in one month") == 30


def test_parse_interval_unparseable():
    assert care.parse_interval_days("Review if not improving.") is None
    assert care.parse_interval_days("") is None
    assert care.parse_interval_days(None) is None
    # a stray single letter must not match a word like 'doctor' or 'was'
    assert care.parse_interval_days("see the doctor when worried") is None


# --- follow-up extraction ---------------------------------------------------

def test_followups_only_from_records_with_text():
    recs = [rec("r1", "2026-03-01", "Repeat TSH after 6 weeks."),
            rec("r2", "2026-03-01", None),
            rec("r3", "2026-03-01", "   ")]
    fus = care.parse_followups(recs, today=TODAY)
    assert [f.record_id for f in fus] == ["r1"]
    assert fus[0].text == "Repeat TSH after 6 weeks."
    assert fus[0].due_date == "2026-04-12"     # 2026-03-01 + 42 days
    assert fus[0].overdue is False             # due after TODAY


def test_followups_overdue_and_sorting():
    recs = [
        rec("upcoming", "2026-03-15", "Review after 2 weeks."),   # due 2026-03-29 (future)
        rec("overdue", "2026-02-01", "Review after 1 week."),     # due 2026-02-08 (past)
        rec("undated_interval", "2026-03-10", "Review if not improving."),  # no due date
    ]
    fus = care.parse_followups(recs, today=TODAY)
    # overdue (smallest due date) first, then upcoming, then the one with no due date
    assert [f.record_id for f in fus] == ["overdue", "upcoming", "undated_interval"]
    assert fus[0].overdue is True and fus[0].due_date == "2026-02-08"
    assert fus[1].overdue is False
    assert fus[2].due_date is None


def test_followup_with_no_record_date_has_no_due_date():
    fus = care.parse_followups([rec("r1", None, "Review after 1 week.")], today=TODAY)
    assert fus[0].due_date is None and fus[0].overdue is False


# --- calendar (.ics) --------------------------------------------------------

def test_ics_event_shape():
    fu = care.parse_followups([rec("r1", "2026-03-01", "Repeat TSH after 6 weeks.")], today=TODAY)[0]
    ics = care.ics_event(fu)
    assert "BEGIN:VCALENDAR" in ics and "END:VCALENDAR" in ics
    assert "DTSTART;VALUE=DATE:20260412" in ics          # the approximate due date
    assert "SUMMARY:Medical follow-up: Repeat TSH after 6 weeks." in ics
    assert ics.endswith("\r\n")


def test_ics_event_none_without_any_date():
    fu = care.FollowUp(record_id="x", record_date=None, text="Review", due_date=None)
    assert care.ics_event(fu) is None


# --- lab trends -------------------------------------------------------------

def inv(name, value, unit=None, ref=None, flag="unknown"):
    return {"name": name, "value": value, "unit": unit, "reference_range": ref, "flag": flag}


def labrec(rid, date, invs):
    return {"record_id": rid, "record_date": date, "document_type": "lab_report",
            "medications": [], "investigations": invs}


def test_parse_value_edge_cases():
    assert care.parse_value("7.2") == 7.2
    assert care.parse_value("142") == 142.0
    assert care.parse_value("7.2 %") == 7.2
    assert care.parse_value("1,240") == 1240.0
    assert care.parse_value(">200") is None       # an inequality is not a point value
    assert care.parse_value("Positive") is None
    assert care.parse_value("") is None
    assert care.parse_value(None) is None


def test_build_trends_groups_by_test_over_time():
    recs = [
        labrec("r1", "2025-01-01", [inv("HbA1c", "7.8", "%", "< 5.7", "high")]),
        labrec("r2", "2025-07-01", [inv("HbA1c", "6.9", "%", "< 5.7", "high")]),
    ]
    series = care.build_trends(recs)
    assert len(series) == 1
    s = series[0]
    assert s.key == "hba1c" and s.unit == "%" and s.count == 2
    assert [p.value for p in s.points] == [7.8, 6.9]    # chronological, oldest first
    assert s.points[0].flag == "high" and s.reference_range == "< 5.7"


def test_build_trends_separates_different_units_and_skips_non_numeric():
    recs = [
        labrec("r1", "2025-01-01", [inv("Anti-TPO", ">200", "IU/mL")]),   # non-numeric: skipped
        labrec("r2", "2025-02-01", [inv("Glucose", "90", "mg/dL")]),
        labrec("r3", "2025-03-01", [inv("Glucose", "5.0", "mmol/L")]),    # different unit: own series
    ]
    series = care.build_trends(recs)
    keys = sorted((s.key, s.unit) for s in series)
    assert keys == [("glucose", "mg/dL"), ("glucose", "mmol/L")]


def test_build_trends_empty_and_single():
    assert care.build_trends([]) == []
    one = care.build_trends([labrec("r1", "2025-01-01", [inv("TSH", "8.9", "uIU/mL")])])
    assert len(one) == 1 and one[0].count == 1


def test_trends_flag_only_from_printed_values():
    # an unknown/absent printed flag stays None; it is never computed
    recs = [labrec("r1", "2025-01-01", [inv("Cholesterol", "185", "mg/dL", "< 200", "unknown")])]
    assert care.build_trends(recs)[0].points[0].flag is None

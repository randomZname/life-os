"""gcal._parse_dt — local wall-clock handling.

Regression: the agent sometimes emitted "T18:00:00Z" for a time the user gave
in local terms; Google honoured the Z (UTC) and ignored timeZone, shifting the
event by the UTC offset (18:00Z → 21:00 in Sofia). _parse_dt must strip a
trailing Z / numeric offset so the wall-clock time stands, tagged gcal_timezone.
"""

from __future__ import annotations

from bogi.config import settings
from bogi.modules.gcal import _parse_dt


def test_bare_local_datetime_keeps_walltime():
    out = _parse_dt("2026-06-01T18:00:00")
    assert out == {"dateTime": "2026-06-01T18:00:00", "timeZone": settings.gcal_timezone}


def test_trailing_z_is_stripped_not_treated_as_utc():
    out = _parse_dt("2026-06-01T18:00:00Z")
    assert out["dateTime"] == "2026-06-01T18:00:00"  # NOT shifted +offset
    assert out["timeZone"] == settings.gcal_timezone


def test_explicit_offset_is_stripped():
    assert _parse_dt("2026-06-01T18:00:00+03:00")["dateTime"] == "2026-06-01T18:00:00"
    assert _parse_dt("2026-06-01T18:00:00+0300")["dateTime"] == "2026-06-01T18:00:00"


def test_space_separator_normalized():
    assert _parse_dt("2026-06-01 18:00:00")["dateTime"] == "2026-06-01T18:00:00"


def test_date_only_is_all_day():
    assert _parse_dt("2026-06-01") == {"date": "2026-06-01"}

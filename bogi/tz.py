"""Local timezone helpers — single source for "what time is it for Богдан".

The whole project treats the user's wall-clock as `settings.gcal_timezone`
(Europe/Sofia by default). Persistence stays in UTC (DB `created_at` etc.), but
everything the **agent reads or reports** and every user-facing time goes
through here so DST is handled correctly (Sofia = UTC+2 winter / +3 summer) and
the agent never has to guess an offset.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from bogi.config import settings


def local_tz() -> ZoneInfo:
    """The configured local timezone (Europe/Sofia)."""
    return ZoneInfo(settings.gcal_timezone)


def now_local() -> datetime:
    """Current time as a tz-aware datetime in the local zone."""
    return datetime.now(local_tz())

"""
Tests for resolve_market_mode() in v3_launcher.py.

Covers:
- HK core trading hours (morning/afternoon sessions)
- HK lunchtime break (12:00-13:00) → IDLE
- Between HK close and US open (16:00-21:30) → IDLE
- US pre-market edge (21:30+) → US
- US overnight session → US
- Saturday/Sunday → IDLE
- Weekend edge: Saturday after 04:00 HKT → IDLE
- Weekend edge: Sunday before 21:30 HKT → IDLE
- Weekend edge: Sunday after 21:30 HKT → US (Monday session starts)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import v3_launcher as launcher

HK_TZ = ZoneInfo("Asia/Hong_Kong")


def _hkt(weekday_offset_from_monday: int, hour: int, minute: int = 0) -> datetime:
    """Build a timezone-aware HKT datetime.

    weekday_offset_from_monday: 0=Mon, 1=Tue, ..., 5=Sat, 6=Sun.
    Uses a fixed Monday anchor (2026-05-04) to produce deterministic weekdays.
    """
    # 2026-05-04 is a Monday
    from datetime import date, timedelta
    anchor = date(2026, 5, 4)
    target_date = anchor + timedelta(days=weekday_offset_from_monday)
    return datetime(target_date.year, target_date.month, target_date.day, hour, minute, tzinfo=HK_TZ)


class TestHKTradingHours:
    def test_hk_morning_session_start(self):
        assert launcher.resolve_market_mode(_hkt(0, 9, 30)) == "HK"

    def test_hk_morning_session_mid(self):
        assert launcher.resolve_market_mode(_hkt(0, 10, 45)) == "HK"

    def test_hk_morning_session_end(self):
        assert launcher.resolve_market_mode(_hkt(0, 12, 0)) == "HK"

    def test_hk_afternoon_session_start(self):
        assert launcher.resolve_market_mode(_hkt(0, 13, 0)) == "HK"

    def test_hk_afternoon_session_mid(self):
        assert launcher.resolve_market_mode(_hkt(0, 14, 30)) == "HK"

    def test_hk_afternoon_session_end(self):
        assert launcher.resolve_market_mode(_hkt(0, 16, 0)) == "HK"


class TestHKLunchBreak:
    """HK market is closed 12:01-12:59; this maps to IDLE, not HK."""

    def test_lunchtime_12_01(self):
        assert launcher.resolve_market_mode(_hkt(0, 12, 1)) == "IDLE"

    def test_lunchtime_12_30(self):
        assert launcher.resolve_market_mode(_hkt(0, 12, 30)) == "IDLE"

    def test_lunchtime_12_59(self):
        assert launcher.resolve_market_mode(_hkt(0, 12, 59)) == "IDLE"


class TestGapBetweenHKCloseAndUSOpen:
    """16:01–21:29 HKT is neither HK nor US → IDLE."""

    def test_just_after_hk_close(self):
        assert launcher.resolve_market_mode(_hkt(0, 16, 1)) == "IDLE"

    def test_mid_afternoon_gap(self):
        assert launcher.resolve_market_mode(_hkt(0, 18, 0)) == "IDLE"

    def test_just_before_us_open(self):
        assert launcher.resolve_market_mode(_hkt(0, 21, 29)) == "IDLE"


class TestUSTradingHours:
    def test_us_open_exact(self):
        assert launcher.resolve_market_mode(_hkt(0, 21, 30)) == "US"

    def test_us_evening(self):
        assert launcher.resolve_market_mode(_hkt(0, 23, 0)) == "US"

    def test_us_overnight_midnight(self):
        assert launcher.resolve_market_mode(_hkt(1, 0, 0)) == "US"

    def test_us_early_morning(self):
        assert launcher.resolve_market_mode(_hkt(1, 3, 0)) == "US"

    def test_us_close_exact(self):
        # 04:00 HKT is still within the US session boundary
        assert launcher.resolve_market_mode(_hkt(1, 4, 0)) == "US"


class TestWeekend:
    def test_saturday_before_04(self):
        # Saturday before 04:00 is still Friday's US session (Friday night)
        assert launcher.resolve_market_mode(_hkt(5, 3, 59)) == "US"

    def test_saturday_at_04_01(self):
        assert launcher.resolve_market_mode(_hkt(5, 4, 1)) == "IDLE"

    def test_saturday_midday(self):
        assert launcher.resolve_market_mode(_hkt(5, 12, 0)) == "IDLE"

    def test_saturday_evening(self):
        assert launcher.resolve_market_mode(_hkt(5, 21, 30)) == "IDLE"

    def test_sunday_morning(self):
        assert launcher.resolve_market_mode(_hkt(6, 9, 0)) == "IDLE"

    def test_sunday_before_us_open(self):
        assert launcher.resolve_market_mode(_hkt(6, 21, 29)) == "IDLE"

    def test_sunday_at_us_open(self):
        # Sunday 21:30 HKT → Monday US session starts
        assert launcher.resolve_market_mode(_hkt(6, 21, 30)) == "US"

    def test_sunday_evening_us_session(self):
        assert launcher.resolve_market_mode(_hkt(6, 23, 0)) == "US"


class TestPreMarketBeforeHKOpen:
    """Before 09:30 on a weekday → not HK, not US → IDLE."""

    def test_before_hk_open(self):
        assert launcher.resolve_market_mode(_hkt(0, 9, 0)) == "IDLE"

    def test_just_before_hk_open(self):
        assert launcher.resolve_market_mode(_hkt(0, 9, 29)) == "IDLE"

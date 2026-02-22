"""Unit tests for market calendar and hours awareness.

Tests the MarketCalendar class including session detection, holiday awareness,
and timing calculations.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.services.market_calendar import MarketCalendar, MarketSession


class TestMarketSession:
    """Tests for MarketSession enum."""

    def test_all_sessions_defined(self):
        """Test that all expected sessions are defined."""
        expected_sessions = {
            "PRE_MARKET",
            "REGULAR",
            "AFTER_HOURS",
            "CLOSED",
            "HOLIDAY",
            "WEEKEND",
        }
        actual_sessions = {session.name for session in MarketSession}
        assert actual_sessions == expected_sessions

    def test_session_values(self):
        """Test session value mappings."""
        assert MarketSession.PRE_MARKET.value == "pre_market"
        assert MarketSession.REGULAR.value == "regular"
        assert MarketSession.AFTER_HOURS.value == "after_hours"
        assert MarketSession.CLOSED.value == "closed"
        assert MarketSession.HOLIDAY.value == "holiday"
        assert MarketSession.WEEKEND.value == "weekend"


class TestMarketCalendarBasics:
    """Tests for MarketCalendar initialization and constants."""

    def test_calendar_initialization(self):
        """Test that calendar initializes correctly."""
        calendar = MarketCalendar()
        assert calendar is not None

    def test_timezone_is_eastern(self):
        """Test that calendar uses US Eastern timezone."""
        assert MarketCalendar.TZ == ZoneInfo("America/New_York")

    def test_session_times_defined(self):
        """Test that all session times are correctly defined."""
        assert MarketCalendar.PRE_MARKET_START == time(4, 0)
        assert MarketCalendar.REGULAR_OPEN == time(9, 30)
        assert MarketCalendar.REGULAR_CLOSE == time(16, 0)
        assert MarketCalendar.AFTER_HOURS_END == time(20, 0)

    def test_2026_holidays_defined(self):
        """Test that all 2026 holidays are defined."""
        expected_holidays = {
            "2026-01-01",  # New Year's Day
            "2026-01-19",  # MLK Day
            "2026-02-16",  # Presidents Day
            "2026-04-03",  # Good Friday
            "2026-05-25",  # Memorial Day
            "2026-07-03",  # Independence Day observed
            "2026-09-07",  # Labor Day
            "2026-11-26",  # Thanksgiving
            "2026-12-25",  # Christmas
        }
        assert MarketCalendar.HOLIDAYS_2026 == expected_holidays


class TestSessionDetection:
    """Tests for get_current_session method."""

    @pytest.fixture
    def calendar(self):
        """Create a MarketCalendar instance."""
        return MarketCalendar()

    def test_pre_market_session(self, calendar):
        """Test detection of pre-market session (4:00 AM - 9:30 AM ET)."""
        # Tuesday at 8:00 AM ET
        dt = datetime(2026, 1, 27, 8, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) == MarketSession.PRE_MARKET

        # Right at pre-market start
        dt = datetime(2026, 1, 27, 4, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) == MarketSession.PRE_MARKET

        # Just before regular open
        dt = datetime(2026, 1, 27, 9, 29, 59, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) == MarketSession.PRE_MARKET

    def test_regular_session(self, calendar):
        """Test detection of regular session (9:30 AM - 4:00 PM ET)."""
        # Tuesday at 10:00 AM ET
        dt = datetime(2026, 1, 27, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) == MarketSession.REGULAR

        # Right at market open
        dt = datetime(2026, 1, 27, 9, 30, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) == MarketSession.REGULAR

        # Just before market close
        dt = datetime(2026, 1, 27, 15, 59, 59, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) == MarketSession.REGULAR

    def test_after_hours_session(self, calendar):
        """Test detection of after-hours session (4:00 PM - 8:00 PM ET)."""
        # Tuesday at 6:00 PM ET
        dt = datetime(2026, 1, 27, 18, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) == MarketSession.AFTER_HOURS

        # Right at market close
        dt = datetime(2026, 1, 27, 16, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) == MarketSession.AFTER_HOURS

        # Just before after-hours end
        dt = datetime(2026, 1, 27, 19, 59, 59, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) == MarketSession.AFTER_HOURS

    def test_closed_session(self, calendar):
        """Test detection of closed session (8:00 PM - 4:00 AM ET)."""
        # Tuesday at 10:00 PM ET
        dt = datetime(2026, 1, 27, 22, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) == MarketSession.CLOSED

        # Right at after-hours end
        dt = datetime(2026, 1, 27, 20, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) == MarketSession.CLOSED

        # Early morning
        dt = datetime(2026, 1, 27, 2, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) == MarketSession.CLOSED

        # Just before pre-market
        dt = datetime(2026, 1, 27, 3, 59, 59, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) == MarketSession.CLOSED

    def test_weekend_detection(self, calendar):
        """Test detection of weekend (Saturday/Sunday)."""
        # Saturday
        sat = datetime(2026, 1, 31, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(sat) == MarketSession.WEEKEND

        # Sunday
        sun = datetime(2026, 2, 1, 14, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(sun) == MarketSession.WEEKEND

    def test_holiday_detection(self, calendar):
        """Test detection of 2026 holidays."""
        # New Year's Day (Thursday)
        dt = datetime(2026, 1, 1, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) == MarketSession.HOLIDAY

        # MLK Day (Monday)
        dt = datetime(2026, 1, 19, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) == MarketSession.HOLIDAY

        # Presidents Day (Monday)
        dt = datetime(2026, 2, 16, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) == MarketSession.HOLIDAY

        # Good Friday
        dt = datetime(2026, 4, 3, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) == MarketSession.HOLIDAY

        # Memorial Day (Monday)
        dt = datetime(2026, 5, 25, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) == MarketSession.HOLIDAY

        # Independence Day observed (Friday)
        dt = datetime(2026, 7, 3, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) == MarketSession.HOLIDAY

        # Labor Day (Monday)
        dt = datetime(2026, 9, 7, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) == MarketSession.HOLIDAY

        # Thanksgiving (Thursday)
        dt = datetime(2026, 11, 26, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) == MarketSession.HOLIDAY

        # Christmas (Friday)
        dt = datetime(2026, 12, 25, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) == MarketSession.HOLIDAY

    def test_non_holiday_detection(self, calendar):
        """Test that non-holidays are not detected as holidays."""
        # Regular trading day
        dt = datetime(2026, 1, 27, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) != MarketSession.HOLIDAY

        # Day after holiday
        dt = datetime(2026, 1, 2, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.get_current_session(dt) != MarketSession.HOLIDAY


class TestTimezoneConversion:
    """Tests for timezone handling."""

    @pytest.fixture
    def calendar(self):
        """Create a MarketCalendar instance."""
        return MarketCalendar()

    def test_naive_datetime_conversion(self, calendar):
        """Test conversion of naive datetime to Eastern Time."""
        # Naive datetime (no timezone)
        dt_naive = datetime(2026, 1, 27, 10, 0, 0)
        session = calendar.get_current_session(dt_naive)
        # Should be treated as ET and return regular session
        assert session == MarketSession.REGULAR

    def test_utc_to_eastern_conversion(self, calendar):
        """Test conversion from UTC to Eastern Time."""
        # 3:00 PM UTC = 10:00 AM ET (during EST)
        dt_utc = datetime(2026, 1, 27, 15, 0, 0, tzinfo=ZoneInfo("UTC"))
        session = calendar.get_current_session(dt_utc)
        assert session == MarketSession.REGULAR

    def test_pacific_to_eastern_conversion(self, calendar):
        """Test conversion from Pacific to Eastern Time."""
        # 7:00 AM PT = 10:00 AM ET
        dt_pt = datetime(2026, 1, 27, 7, 0, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
        session = calendar.get_current_session(dt_pt)
        assert session == MarketSession.REGULAR

    def test_eastern_datetime_no_conversion(self, calendar):
        """Test that Eastern datetime is not converted."""
        dt_et = datetime(2026, 1, 27, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        session = calendar.get_current_session(dt_et)
        assert session == MarketSession.REGULAR


class TestMarketOpenCheck:
    """Tests for is_market_open method."""

    @pytest.fixture
    def calendar(self):
        """Create a MarketCalendar instance."""
        return MarketCalendar()

    def test_market_open_during_regular_hours(self, calendar):
        """Test that market is open during regular hours."""
        dt = datetime(2026, 1, 27, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.is_market_open(dt) is True

    def test_market_closed_pre_market(self, calendar):
        """Test that market is closed during pre-market."""
        dt = datetime(2026, 1, 27, 8, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.is_market_open(dt) is False

    def test_market_closed_after_hours(self, calendar):
        """Test that market is closed during after-hours."""
        dt = datetime(2026, 1, 27, 18, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.is_market_open(dt) is False

    def test_market_closed_weekend(self, calendar):
        """Test that market is closed on weekends."""
        dt = datetime(2026, 1, 31, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.is_market_open(dt) is False

    def test_market_closed_holiday(self, calendar):
        """Test that market is closed on holidays."""
        dt = datetime(2026, 1, 1, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.is_market_open(dt) is False


class TestTradingDayCheck:
    """Tests for is_trading_day method."""

    @pytest.fixture
    def calendar(self):
        """Create a MarketCalendar instance."""
        return MarketCalendar()

    def test_weekday_is_trading_day(self, calendar):
        """Test that regular weekday is a trading day."""
        dt = datetime(2026, 1, 27, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.is_trading_day(dt) is True

    def test_saturday_not_trading_day(self, calendar):
        """Test that Saturday is not a trading day."""
        dt = datetime(2026, 1, 31, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.is_trading_day(dt) is False

    def test_sunday_not_trading_day(self, calendar):
        """Test that Sunday is not a trading day."""
        dt = datetime(2026, 2, 1, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.is_trading_day(dt) is False

    def test_holiday_not_trading_day(self, calendar):
        """Test that holidays are not trading days."""
        # New Year's Day
        dt = datetime(2026, 1, 1, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.is_trading_day(dt) is False

        # Christmas
        dt = datetime(2026, 12, 25, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.is_trading_day(dt) is False

    def test_friday_before_holiday_is_trading_day(self, calendar):
        """Test that day before holiday is still a trading day."""
        # Friday before MLK Day (Monday holiday)
        dt = datetime(2026, 1, 16, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        assert calendar.is_trading_day(dt) is True


class TestNextMarketOpen:
    """Tests for next_market_open method."""

    @pytest.fixture
    def calendar(self):
        """Create a MarketCalendar instance."""
        return MarketCalendar()

    def test_next_open_same_day_before_open(self, calendar):
        """Test next open is today when before market open."""
        # Tuesday at 8:00 AM (before 9:30 AM open)
        dt = datetime(2026, 1, 27, 8, 0, 0, tzinfo=MarketCalendar.TZ)
        next_open = calendar.next_market_open(dt)

        assert next_open.date() == dt.date()
        assert next_open.time() == time(9, 30)

    def test_next_open_same_day_during_market(self, calendar):
        """Test next open is next trading day when market is open."""
        # Tuesday at 10:00 AM (during market hours)
        dt = datetime(2026, 1, 27, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        next_open = calendar.next_market_open(dt)

        # Should be Wednesday (next day)
        assert next_open.date() == datetime(2026, 1, 28).date()
        assert next_open.time() == time(9, 30)

    def test_next_open_after_market_close(self, calendar):
        """Test next open after market close."""
        # Tuesday at 6:00 PM (after 4:00 PM close)
        dt = datetime(2026, 1, 27, 18, 0, 0, tzinfo=MarketCalendar.TZ)
        next_open = calendar.next_market_open(dt)

        # Should be Wednesday
        assert next_open.date() == datetime(2026, 1, 28).date()
        assert next_open.time() == time(9, 30)

    def test_next_open_from_friday(self, calendar):
        """Test next open from Friday is Monday."""
        # Friday at 6:00 PM
        dt = datetime(2026, 1, 30, 18, 0, 0, tzinfo=MarketCalendar.TZ)
        next_open = calendar.next_market_open(dt)

        # Should be Monday (skipping weekend)
        assert next_open.date() == datetime(2026, 2, 2).date()
        assert next_open.time() == time(9, 30)

    def test_next_open_from_saturday(self, calendar):
        """Test next open from Saturday is Monday."""
        # Saturday
        dt = datetime(2026, 1, 31, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        next_open = calendar.next_market_open(dt)

        # Should be Monday
        assert next_open.date() == datetime(2026, 2, 2).date()
        assert next_open.time() == time(9, 30)

    def test_next_open_from_sunday(self, calendar):
        """Test next open from Sunday is Monday."""
        # Sunday
        dt = datetime(2026, 2, 1, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        next_open = calendar.next_market_open(dt)

        # Should be Monday
        assert next_open.date() == datetime(2026, 2, 2).date()
        assert next_open.time() == time(9, 30)

    def test_next_open_skips_holiday(self, calendar):
        """Test next open skips holidays."""
        # Friday before MLK Day (Monday holiday)
        dt = datetime(2026, 1, 16, 18, 0, 0, tzinfo=MarketCalendar.TZ)
        next_open = calendar.next_market_open(dt)

        # Should be Tuesday (skipping weekend and Monday holiday)
        assert next_open.date() == datetime(2026, 1, 20).date()
        assert next_open.time() == time(9, 30)

    def test_next_open_from_holiday(self, calendar):
        """Test next open from a holiday."""
        # New Year's Day (Thursday)
        dt = datetime(2026, 1, 1, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        next_open = calendar.next_market_open(dt)

        # Should be Friday
        assert next_open.date() == datetime(2026, 1, 2).date()
        assert next_open.time() == time(9, 30)

    def test_next_open_three_day_weekend(self, calendar):
        """Test next open over three-day weekend (holiday Monday)."""
        # Friday before Labor Day
        dt = datetime(2026, 9, 4, 18, 0, 0, tzinfo=MarketCalendar.TZ)
        next_open = calendar.next_market_open(dt)

        # Should be Tuesday (skipping Sat, Sun, and Labor Day Monday)
        assert next_open.date() == datetime(2026, 9, 8).date()
        assert next_open.time() == time(9, 30)


class TestTimeUntilOpen:
    """Tests for time_until_open method."""

    @pytest.fixture
    def calendar(self):
        """Create a MarketCalendar instance."""
        return MarketCalendar()

    def test_time_until_open_same_day(self, calendar):
        """Test time until open when market opens same day."""
        # Tuesday at 8:00 AM (1.5 hours before open)
        dt = datetime(2026, 1, 27, 8, 0, 0, tzinfo=MarketCalendar.TZ)
        time_until = calendar.time_until_open(dt)

        assert time_until == timedelta(hours=1, minutes=30)

    def test_time_until_open_next_day(self, calendar):
        """Test time until open when market opens next day."""
        # Tuesday at 6:00 PM (15.5 hours until next open)
        dt = datetime(2026, 1, 27, 18, 0, 0, tzinfo=MarketCalendar.TZ)
        time_until = calendar.time_until_open(dt)

        assert time_until == timedelta(hours=15, minutes=30)

    def test_time_until_open_over_weekend(self, calendar):
        """Test time until open over weekend."""
        # Friday at 6:00 PM
        dt = datetime(2026, 1, 30, 18, 0, 0, tzinfo=MarketCalendar.TZ)
        time_until = calendar.time_until_open(dt)

        # Until Monday 9:30 AM (63.5 hours)
        expected = timedelta(days=2, hours=15, minutes=30)
        assert time_until == expected


class TestNextMarketClose:
    """Tests for next_market_close method."""

    @pytest.fixture
    def calendar(self):
        """Create a MarketCalendar instance."""
        return MarketCalendar()

    def test_next_close_same_day_before_close(self, calendar):
        """Test next close is today when before market close."""
        # Tuesday at 10:00 AM (before 4:00 PM close)
        dt = datetime(2026, 1, 27, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        next_close = calendar.next_market_close(dt)

        assert next_close.date() == dt.date()
        assert next_close.time() == time(16, 0)

    def test_next_close_after_close(self, calendar):
        """Test next close after market close."""
        # Tuesday at 6:00 PM (after 4:00 PM close)
        dt = datetime(2026, 1, 27, 18, 0, 0, tzinfo=MarketCalendar.TZ)
        next_close = calendar.next_market_close(dt)

        # Should be Wednesday
        assert next_close.date() == datetime(2026, 1, 28).date()
        assert next_close.time() == time(16, 0)

    def test_next_close_from_weekend(self, calendar):
        """Test next close from weekend is Monday."""
        # Saturday
        dt = datetime(2026, 1, 31, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        next_close = calendar.next_market_close(dt)

        # Should be Monday
        assert next_close.date() == datetime(2026, 2, 2).date()
        assert next_close.time() == time(16, 0)


class TestFormatSessionInfo:
    """Tests for format_session_info method."""

    @pytest.fixture
    def calendar(self):
        """Create a MarketCalendar instance."""
        return MarketCalendar()

    def test_format_info_during_regular_session(self, calendar):
        """Test formatting info during regular session."""
        dt = datetime(2026, 1, 27, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        info = calendar.format_session_info(dt)

        assert info["session"] == "regular"
        assert info["is_open"] == "True"
        assert "current_time" in info
        assert "next_open" not in info  # Not included when market is open

    def test_format_info_when_closed(self, calendar):
        """Test formatting info when market is closed."""
        dt = datetime(2026, 1, 27, 22, 0, 0, tzinfo=MarketCalendar.TZ)
        info = calendar.format_session_info(dt)

        assert info["session"] == "closed"
        assert info["is_open"] == "False"
        assert "next_open" in info
        assert "time_until_open" in info

    def test_format_info_on_weekend(self, calendar):
        """Test formatting info on weekend."""
        dt = datetime(2026, 1, 31, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        info = calendar.format_session_info(dt)

        assert info["session"] == "weekend"
        assert info["is_open"] == "False"
        assert "next_open" in info
        assert "time_until_open" in info

    def test_format_info_on_holiday(self, calendar):
        """Test formatting info on holiday."""
        dt = datetime(2026, 1, 1, 10, 0, 0, tzinfo=MarketCalendar.TZ)
        info = calendar.format_session_info(dt)

        assert info["session"] == "holiday"
        assert info["is_open"] == "False"
        assert "next_open" in info
        assert "time_until_open" in info


class TestFormatTimedelta:
    """Tests for _format_timedelta helper method."""

    @pytest.fixture
    def calendar(self):
        """Create a MarketCalendar instance."""
        return MarketCalendar()

    def test_format_minutes_only(self, calendar):
        """Test formatting minutes only."""
        td = timedelta(minutes=30)
        formatted = calendar._format_timedelta(td)
        assert formatted == "30 minutes"

    def test_format_single_minute(self, calendar):
        """Test formatting single minute."""
        td = timedelta(minutes=1)
        formatted = calendar._format_timedelta(td)
        assert formatted == "1 minute"

    def test_format_hours_and_minutes(self, calendar):
        """Test formatting hours and minutes."""
        td = timedelta(hours=2, minutes=30)
        formatted = calendar._format_timedelta(td)
        assert formatted == "2 hours, 30 minutes"

    def test_format_single_hour(self, calendar):
        """Test formatting single hour."""
        td = timedelta(hours=1, minutes=15)
        formatted = calendar._format_timedelta(td)
        assert formatted == "1 hour, 15 minutes"

    def test_format_days_and_hours(self, calendar):
        """Test formatting days and hours."""
        td = timedelta(days=2, hours=3, minutes=15)
        formatted = calendar._format_timedelta(td)
        assert formatted == "2 days, 3 hours, 15 minutes"

    def test_format_single_day(self, calendar):
        """Test formatting single day."""
        td = timedelta(days=1, hours=2)
        formatted = calendar._format_timedelta(td)
        assert formatted == "1 day, 2 hours"

    def test_format_zero_minutes(self, calendar):
        """Test formatting when minutes are zero but hours exist."""
        td = timedelta(hours=3)
        formatted = calendar._format_timedelta(td)
        assert formatted == "3 hours"

    def test_format_zero_timedelta(self, calendar):
        """Test formatting zero timedelta."""
        td = timedelta(0)
        formatted = calendar._format_timedelta(td)
        assert formatted == "0 minutes"

"""Unit tests for order timing and scheduling.

Tests the OrderTimingHandler class including timing modes, limit price
adjustments, and GTD date calculations.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.execution.order_timing import (
    OrderTiming,
    OrderTimingHandler,
    OrderTimingMode,
)
from src.services.market_calendar import MarketCalendar, MarketSession


class TestOrderTimingMode:
    """Tests for OrderTimingMode enum."""

    def test_all_modes_defined(self):
        """Test that all expected timing modes are defined."""
        expected_modes = {"IMMEDIATE", "MARKET_OPEN", "MANUAL_TRIGGER"}
        actual_modes = {mode.name for mode in OrderTimingMode}
        assert actual_modes == expected_modes

    def test_mode_values(self):
        """Test mode value mappings."""
        assert OrderTimingMode.IMMEDIATE.value == "immediate"
        assert OrderTimingMode.MARKET_OPEN.value == "market_open"
        assert OrderTimingMode.MANUAL_TRIGGER.value == "manual_trigger"


class TestOrderTiming:
    """Tests for OrderTiming dataclass."""

    def test_create_order_timing(self):
        """Test creating an OrderTiming instance."""
        timing = OrderTiming(
            mode=OrderTimingMode.IMMEDIATE,
            can_execute_now=True,
            market_session=MarketSession.REGULAR,
            execute_at=None,
            wait_duration=None,
            reason="Market open",
            tif="DAY",
        )

        assert timing.mode == OrderTimingMode.IMMEDIATE
        assert timing.can_execute_now is True
        assert timing.market_session == MarketSession.REGULAR
        assert timing.execute_at is None
        assert timing.wait_duration is None
        assert timing.reason == "Market open"
        assert timing.tif == "DAY"
        assert timing.gtd_date is None

    def test_order_timing_with_gtd(self):
        """Test OrderTiming with GTD date."""
        gtd_date = datetime(2026, 2, 1, 16, 0, 0, tzinfo=MarketCalendar.TZ)
        timing = OrderTiming(
            mode=OrderTimingMode.MARKET_OPEN,
            can_execute_now=False,
            market_session=MarketSession.CLOSED,
            execute_at=datetime(2026, 1, 28, 9, 30, 0, tzinfo=MarketCalendar.TZ),
            wait_duration=timedelta(hours=15),
            reason="Queued for market open",
            tif="GTD",
            gtd_date=gtd_date,
        )

        assert timing.tif == "GTD"
        assert timing.gtd_date == gtd_date


class TestOrderTimingHandler:
    """Tests for OrderTimingHandler class."""

    @pytest.fixture
    def handler(self):
        """Create a handler with default settings."""
        return OrderTimingHandler(
            default_mode=OrderTimingMode.MARKET_OPEN,
            default_tif="DAY",
            gtd_days_ahead=5,
        )

    @pytest.fixture
    def immediate_handler(self):
        """Create a handler with IMMEDIATE mode."""
        return OrderTimingHandler(
            default_mode=OrderTimingMode.IMMEDIATE,
            default_tif="GTC",
            gtd_days_ahead=5,
        )

    def test_handler_initialization(self, handler):
        """Test handler initializes with correct settings."""
        assert handler.default_mode == OrderTimingMode.MARKET_OPEN
        assert handler.default_tif == "DAY"
        assert handler.gtd_days_ahead == 5
        assert isinstance(handler.market_calendar, MarketCalendar)

    def test_handler_custom_settings(self):
        """Test handler with custom settings."""
        handler = OrderTimingHandler(
            default_mode=OrderTimingMode.MANUAL_TRIGGER,
            default_tif="GTD",
            gtd_days_ahead=10,
        )

        assert handler.default_mode == OrderTimingMode.MANUAL_TRIGGER
        assert handler.default_tif == "GTD"
        assert handler.gtd_days_ahead == 10


class TestImmediateMode:
    """Tests for IMMEDIATE timing mode."""

    @pytest.fixture
    def handler(self):
        """Create a handler with IMMEDIATE mode."""
        return OrderTimingHandler(default_mode=OrderTimingMode.IMMEDIATE)

    def test_immediate_mode_market_open(self, handler):
        """Test IMMEDIATE mode when market is open."""
        # Tuesday at 10:00 AM ET (market open)
        dt = datetime(2026, 1, 27, 10, 0, 0, tzinfo=MarketCalendar.TZ)

        timing = handler.prepare_order(dt=dt)

        assert timing.mode == OrderTimingMode.IMMEDIATE
        assert timing.can_execute_now is True
        assert timing.market_session == MarketSession.REGULAR
        assert timing.execute_at is None
        assert timing.wait_duration is None
        assert "immediately" in timing.reason.lower()

    def test_immediate_mode_market_closed(self, handler):
        """Test IMMEDIATE mode when market is closed."""
        # Tuesday at 6:00 PM ET (after hours)
        dt = datetime(2026, 1, 27, 18, 0, 0, tzinfo=MarketCalendar.TZ)

        timing = handler.prepare_order(dt=dt)

        assert timing.mode == OrderTimingMode.IMMEDIATE
        assert timing.can_execute_now is False
        assert timing.market_session == MarketSession.AFTER_HOURS
        assert timing.execute_at is None
        assert timing.wait_duration is not None
        assert "requires open market" in timing.reason

    def test_immediate_mode_pre_market(self, handler):
        """Test IMMEDIATE mode during pre-market."""
        # Tuesday at 8:00 AM ET (pre-market)
        dt = datetime(2026, 1, 27, 8, 0, 0, tzinfo=MarketCalendar.TZ)

        timing = handler.prepare_order(dt=dt)

        assert timing.can_execute_now is False
        assert timing.market_session == MarketSession.PRE_MARKET

    def test_immediate_mode_weekend(self, handler):
        """Test IMMEDIATE mode on weekend."""
        # Saturday
        dt = datetime(2026, 1, 31, 10, 0, 0, tzinfo=MarketCalendar.TZ)

        timing = handler.prepare_order(dt=dt)

        assert timing.can_execute_now is False
        assert timing.market_session == MarketSession.WEEKEND

    def test_immediate_mode_holiday(self, handler):
        """Test IMMEDIATE mode on holiday."""
        # New Year's Day
        dt = datetime(2026, 1, 1, 10, 0, 0, tzinfo=MarketCalendar.TZ)

        timing = handler.prepare_order(dt=dt)

        assert timing.can_execute_now is False
        assert timing.market_session == MarketSession.HOLIDAY


class TestMarketOpenMode:
    """Tests for MARKET_OPEN timing mode."""

    @pytest.fixture
    def handler(self):
        """Create a handler with MARKET_OPEN mode."""
        return OrderTimingHandler(default_mode=OrderTimingMode.MARKET_OPEN)

    def test_market_open_mode_already_open(self, handler):
        """Test MARKET_OPEN mode when market is already open."""
        # Tuesday at 10:00 AM ET (market open)
        dt = datetime(2026, 1, 27, 10, 0, 0, tzinfo=MarketCalendar.TZ)

        timing = handler.prepare_order(dt=dt)

        assert timing.mode == OrderTimingMode.MARKET_OPEN
        assert timing.can_execute_now is True
        assert timing.market_session == MarketSession.REGULAR
        assert timing.execute_at is None
        assert timing.wait_duration is None
        assert "executing now" in timing.reason.lower()

    def test_market_open_mode_market_closed(self, handler):
        """Test MARKET_OPEN mode when market is closed."""
        # Tuesday at 6:00 PM ET (after hours)
        dt = datetime(2026, 1, 27, 18, 0, 0, tzinfo=MarketCalendar.TZ)

        timing = handler.prepare_order(dt=dt)

        assert timing.mode == OrderTimingMode.MARKET_OPEN
        assert timing.can_execute_now is False
        assert timing.execute_at is not None
        assert timing.wait_duration is not None
        assert "queued" in timing.reason.lower()

        # Should be queued for next day's open
        assert timing.execute_at.date() == datetime(2026, 1, 28).date()
        assert timing.execute_at.time() == time(9, 30)

    def test_market_open_mode_pre_market(self, handler):
        """Test MARKET_OPEN mode during pre-market."""
        # Tuesday at 8:00 AM ET (pre-market)
        dt = datetime(2026, 1, 27, 8, 0, 0, tzinfo=MarketCalendar.TZ)

        timing = handler.prepare_order(dt=dt)

        assert timing.can_execute_now is False
        assert timing.execute_at is not None

        # Should be queued for today's open (same day)
        assert timing.execute_at.date() == dt.date()
        assert timing.execute_at.time() == time(9, 30)

    def test_market_open_mode_weekend(self, handler):
        """Test MARKET_OPEN mode on weekend."""
        # Saturday
        dt = datetime(2026, 1, 31, 10, 0, 0, tzinfo=MarketCalendar.TZ)

        timing = handler.prepare_order(dt=dt)

        assert timing.can_execute_now is False
        assert timing.execute_at is not None

        # Should be queued for Monday
        assert timing.execute_at.date() == datetime(2026, 2, 2).date()
        assert timing.execute_at.time() == time(9, 30)

    def test_market_open_mode_friday_evening(self, handler):
        """Test MARKET_OPEN mode on Friday evening."""
        # Friday at 6:00 PM ET
        dt = datetime(2026, 1, 30, 18, 0, 0, tzinfo=MarketCalendar.TZ)

        timing = handler.prepare_order(dt=dt)

        assert timing.can_execute_now is False

        # Should be queued for Monday
        assert timing.execute_at.date() == datetime(2026, 2, 2).date()

    def test_market_open_mode_holiday(self, handler):
        """Test MARKET_OPEN mode on holiday."""
        # MLK Day (Monday holiday)
        dt = datetime(2026, 1, 19, 10, 0, 0, tzinfo=MarketCalendar.TZ)

        timing = handler.prepare_order(dt=dt)

        assert timing.can_execute_now is False

        # Should be queued for Tuesday
        assert timing.execute_at.date() == datetime(2026, 1, 20).date()

    def test_market_open_mode_tif_conversion(self, handler):
        """Test that DAY orders become GTC when queued."""
        # Market closed
        dt = datetime(2026, 1, 27, 18, 0, 0, tzinfo=MarketCalendar.TZ)

        timing = handler.prepare_order(dt=dt)

        # DAY should convert to GTC when queuing
        assert timing.tif == "GTC"

    def test_market_open_mode_gtd_date(self):
        """Test GTD date calculation for queued orders."""
        handler = OrderTimingHandler(
            default_mode=OrderTimingMode.MARKET_OPEN,
            default_tif="GTD",
            gtd_days_ahead=5,
        )

        # Market closed
        dt = datetime(2026, 1, 27, 18, 0, 0, tzinfo=MarketCalendar.TZ)

        timing = handler.prepare_order(dt=dt)

        assert timing.tif == "GTD"
        assert timing.gtd_date is not None

        # GTD should be 5 days after next open
        expected_gtd = timing.execute_at + timedelta(days=5)
        assert timing.gtd_date.date() == expected_gtd.date()


class TestManualTriggerMode:
    """Tests for MANUAL_TRIGGER timing mode."""

    @pytest.fixture
    def handler(self):
        """Create a handler with MANUAL_TRIGGER mode."""
        return OrderTimingHandler(default_mode=OrderTimingMode.MANUAL_TRIGGER)

    def test_manual_trigger_market_open(self, handler):
        """Test MANUAL_TRIGGER mode when market is open."""
        # Tuesday at 10:00 AM ET (market open)
        dt = datetime(2026, 1, 27, 10, 0, 0, tzinfo=MarketCalendar.TZ)

        timing = handler.prepare_order(dt=dt)

        assert timing.mode == OrderTimingMode.MANUAL_TRIGGER
        assert timing.can_execute_now is False  # Never auto-execute
        assert timing.execute_at is None
        assert timing.wait_duration is None
        assert "manual" in timing.reason.lower()
        assert "waiting" in timing.reason.lower()

    def test_manual_trigger_market_closed(self, handler):
        """Test MANUAL_TRIGGER mode when market is closed."""
        # Tuesday at 6:00 PM ET
        dt = datetime(2026, 1, 27, 18, 0, 0, tzinfo=MarketCalendar.TZ)

        timing = handler.prepare_order(dt=dt)

        assert timing.can_execute_now is False
        assert timing.execute_at is None
        assert "manual" in timing.reason.lower()

    def test_manual_trigger_weekend(self, handler):
        """Test MANUAL_TRIGGER mode on weekend."""
        # Saturday
        dt = datetime(2026, 1, 31, 10, 0, 0, tzinfo=MarketCalendar.TZ)

        timing = handler.prepare_order(dt=dt)

        assert timing.can_execute_now is False
        assert timing.execute_at is None


class TestModeOverride:
    """Tests for overriding default timing mode."""

    @pytest.fixture
    def handler(self):
        """Create a handler with MARKET_OPEN as default."""
        return OrderTimingHandler(default_mode=OrderTimingMode.MARKET_OPEN)

    def test_override_to_immediate(self, handler):
        """Test overriding to IMMEDIATE mode."""
        dt = datetime(2026, 1, 27, 10, 0, 0, tzinfo=MarketCalendar.TZ)

        timing = handler.prepare_order(mode=OrderTimingMode.IMMEDIATE, dt=dt)

        assert timing.mode == OrderTimingMode.IMMEDIATE

    def test_override_to_manual_trigger(self, handler):
        """Test overriding to MANUAL_TRIGGER mode."""
        dt = datetime(2026, 1, 27, 10, 0, 0, tzinfo=MarketCalendar.TZ)

        timing = handler.prepare_order(mode=OrderTimingMode.MANUAL_TRIGGER, dt=dt)

        assert timing.mode == OrderTimingMode.MANUAL_TRIGGER

    def test_use_default_mode_when_none(self, handler):
        """Test using default mode when no override provided."""
        dt = datetime(2026, 1, 27, 10, 0, 0, tzinfo=MarketCalendar.TZ)

        timing = handler.prepare_order(mode=None, dt=dt)

        assert timing.mode == OrderTimingMode.MARKET_OPEN


class TestAdjustLimitPrice:
    """Tests for adjust_limit_price method."""

    @pytest.fixture
    def handler(self):
        """Create a handler instance."""
        return OrderTimingHandler()

    def test_no_adjustment_regular_session(self, handler):
        """Test no adjustment during regular session."""
        adjusted = handler.adjust_limit_price(
            base_price=100.0,
            session=MarketSession.REGULAR,
            is_buy=True,
        )

        assert adjusted == 100.0

    def test_no_adjustment_closed_session(self, handler):
        """Test no adjustment during closed session."""
        adjusted = handler.adjust_limit_price(
            base_price=100.0,
            session=MarketSession.CLOSED,
            is_buy=True,
        )

        assert adjusted == 100.0

    def test_adjustment_pre_market_buy(self, handler):
        """Test adjustment for pre-market buy order."""
        adjusted = handler.adjust_limit_price(
            base_price=100.0,
            session=MarketSession.PRE_MARKET,
            is_buy=True,
            buffer_pct=0.02,
        )

        # Should add 2% buffer for buy
        assert adjusted == 102.0

    def test_adjustment_pre_market_sell(self, handler):
        """Test adjustment for pre-market sell order."""
        adjusted = handler.adjust_limit_price(
            base_price=100.0,
            session=MarketSession.PRE_MARKET,
            is_buy=False,
            buffer_pct=0.02,
        )

        # Should subtract 2% buffer for sell
        assert adjusted == 98.0

    def test_adjustment_after_hours_buy(self, handler):
        """Test adjustment for after-hours buy order."""
        adjusted = handler.adjust_limit_price(
            base_price=100.0,
            session=MarketSession.AFTER_HOURS,
            is_buy=True,
            buffer_pct=0.02,
        )

        assert adjusted == 102.0

    def test_adjustment_after_hours_sell(self, handler):
        """Test adjustment for after-hours sell order."""
        adjusted = handler.adjust_limit_price(
            base_price=100.0,
            session=MarketSession.AFTER_HOURS,
            is_buy=False,
            buffer_pct=0.02,
        )

        assert adjusted == 98.0

    def test_adjustment_custom_buffer(self, handler):
        """Test adjustment with custom buffer percentage."""
        adjusted = handler.adjust_limit_price(
            base_price=100.0,
            session=MarketSession.PRE_MARKET,
            is_buy=True,
            buffer_pct=0.05,  # 5% buffer
        )

        assert adjusted == 105.0

    def test_adjustment_rounding(self, handler):
        """Test that adjusted price is rounded to 2 decimals."""
        adjusted = handler.adjust_limit_price(
            base_price=100.123,
            session=MarketSession.PRE_MARKET,
            is_buy=True,
            buffer_pct=0.02,
        )

        # Should round to 2 decimals
        assert adjusted == 102.13

    def test_adjustment_small_price(self, handler):
        """Test adjustment with small price."""
        adjusted = handler.adjust_limit_price(
            base_price=5.0,
            session=MarketSession.PRE_MARKET,
            is_buy=True,
            buffer_pct=0.02,
        )

        assert adjusted == 5.10

    def test_adjustment_large_price(self, handler):
        """Test adjustment with large price."""
        adjusted = handler.adjust_limit_price(
            base_price=5000.0,
            session=MarketSession.PRE_MARKET,
            is_buy=True,
            buffer_pct=0.02,
        )

        assert adjusted == 5100.0


class TestFormatTimingInfo:
    """Tests for format_timing_info method."""

    @pytest.fixture
    def handler(self):
        """Create a handler instance."""
        return OrderTimingHandler()

    def test_format_basic_info(self, handler):
        """Test formatting basic timing info."""
        timing = OrderTiming(
            mode=OrderTimingMode.IMMEDIATE,
            can_execute_now=True,
            market_session=MarketSession.REGULAR,
            execute_at=None,
            wait_duration=None,
            reason="Market open",
            tif="DAY",
        )

        info = handler.format_timing_info(timing)

        assert info["mode"] == "immediate"
        assert info["can_execute_now"] == "True"
        assert info["market_session"] == "regular"
        assert info["reason"] == "Market open"
        assert info["tif"] == "DAY"
        assert "execute_at" not in info
        assert "wait_duration" not in info

    def test_format_info_with_execute_at(self, handler):
        """Test formatting info with execute_at time."""
        execute_at = datetime(2026, 1, 28, 9, 30, 0, tzinfo=MarketCalendar.TZ)
        timing = OrderTiming(
            mode=OrderTimingMode.MARKET_OPEN,
            can_execute_now=False,
            market_session=MarketSession.CLOSED,
            execute_at=execute_at,
            wait_duration=timedelta(hours=15, minutes=30),
            reason="Queued",
            tif="GTC",
        )

        info = handler.format_timing_info(timing)

        assert "execute_at" in info
        assert "2026-01-28 09:30" in info["execute_at"]

    def test_format_info_with_wait_duration(self, handler):
        """Test formatting info with wait duration."""
        timing = OrderTiming(
            mode=OrderTimingMode.IMMEDIATE,
            can_execute_now=False,
            market_session=MarketSession.CLOSED,
            execute_at=None,
            wait_duration=timedelta(hours=15, minutes=30),
            reason="Market closed",
            tif="DAY",
        )

        info = handler.format_timing_info(timing)

        assert "wait_duration" in info
        assert info["wait_duration"] == "15h 30m"

    def test_format_info_with_gtd_date(self, handler):
        """Test formatting info with GTD date."""
        gtd_date = datetime(2026, 2, 1, 16, 0, 0, tzinfo=MarketCalendar.TZ)
        timing = OrderTiming(
            mode=OrderTimingMode.MARKET_OPEN,
            can_execute_now=False,
            market_session=MarketSession.CLOSED,
            execute_at=datetime(2026, 1, 28, 9, 30, 0, tzinfo=MarketCalendar.TZ),
            wait_duration=timedelta(hours=15),
            reason="Queued",
            tif="GTD",
            gtd_date=gtd_date,
        )

        info = handler.format_timing_info(timing)

        assert "gtd_date" in info
        assert info["gtd_date"] == "2026-02-01"

    def test_format_info_short_wait(self, handler):
        """Test formatting short wait duration."""
        timing = OrderTiming(
            mode=OrderTimingMode.IMMEDIATE,
            can_execute_now=False,
            market_session=MarketSession.CLOSED,
            execute_at=None,
            wait_duration=timedelta(minutes=45),
            reason="Market closed",
            tif="DAY",
        )

        info = handler.format_timing_info(timing)

        assert info["wait_duration"] == "0h 45m"

    def test_format_info_long_wait(self, handler):
        """Test formatting long wait duration."""
        timing = OrderTiming(
            mode=OrderTimingMode.MARKET_OPEN,
            can_execute_now=False,
            market_session=MarketSession.WEEKEND,
            execute_at=datetime(2026, 2, 2, 9, 30, 0, tzinfo=MarketCalendar.TZ),
            wait_duration=timedelta(days=2, hours=15, minutes=30),
            reason="Queued for Monday",
            tif="GTC",
        )

        info = handler.format_timing_info(timing)

        # 2 days = 48 hours + 15 hours = 63 hours
        assert info["wait_duration"] == "63h 30m"


class TestTimezoneHandling:
    """Tests for timezone conversion in timing operations."""

    @pytest.fixture
    def handler(self):
        """Create a handler instance."""
        return OrderTimingHandler()

    def test_naive_datetime_conversion(self, handler):
        """Test conversion of naive datetime to Eastern Time."""
        # Naive datetime (no timezone)
        dt_naive = datetime(2026, 1, 27, 10, 0, 0)

        timing = handler.prepare_order(dt=dt_naive)

        # Should be treated as ET and market should be open
        assert timing.market_session == MarketSession.REGULAR

    def test_utc_to_eastern_conversion(self, handler):
        """Test conversion from UTC to Eastern Time."""
        # 3:00 PM UTC = 10:00 AM ET (during EST)
        dt_utc = datetime(2026, 1, 27, 15, 0, 0, tzinfo=ZoneInfo("UTC"))

        timing = handler.prepare_order(dt=dt_utc)

        assert timing.market_session == MarketSession.REGULAR

    def test_pacific_to_eastern_conversion(self, handler):
        """Test conversion from Pacific to Eastern Time."""
        # 7:00 AM PT = 10:00 AM ET
        dt_pt = datetime(2026, 1, 27, 7, 0, 0, tzinfo=ZoneInfo("America/Los_Angeles"))

        timing = handler.prepare_order(dt=dt_pt)

        assert timing.market_session == MarketSession.REGULAR


class TestInvalidMode:
    """Tests for invalid timing mode handling."""

    @pytest.fixture
    def handler(self):
        """Create a handler instance."""
        return OrderTimingHandler()

    def test_invalid_mode_raises_error(self, handler):
        """Test that invalid mode raises ValueError."""
        dt = datetime(2026, 1, 27, 10, 0, 0, tzinfo=MarketCalendar.TZ)

        # Create an invalid mode (not a real enum value)
        with pytest.raises(ValueError, match="Unknown timing mode"):
            # We need to bypass the enum to test this
            handler.prepare_order(mode="invalid_mode", dt=dt)


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.fixture
    def handler(self):
        """Create a handler instance."""
        return OrderTimingHandler()

    def test_prepare_order_at_market_open(self, handler):
        """Test preparing order right at market open."""
        # Exactly 9:30 AM ET
        dt = datetime(2026, 1, 27, 9, 30, 0, tzinfo=MarketCalendar.TZ)

        timing = handler.prepare_order(dt=dt)

        # Should be in regular session
        assert timing.market_session == MarketSession.REGULAR

    def test_prepare_order_at_market_close(self, handler):
        """Test preparing order right at market close."""
        # Exactly 4:00 PM ET
        dt = datetime(2026, 1, 27, 16, 0, 0, tzinfo=MarketCalendar.TZ)

        timing = handler.prepare_order(dt=dt)

        # Should be in after-hours session
        assert timing.market_session == MarketSession.AFTER_HOURS

    def test_prepare_order_just_before_open(self, handler):
        """Test preparing order just before market open."""
        # 9:29:59 AM ET
        dt = datetime(2026, 1, 27, 9, 29, 59, tzinfo=MarketCalendar.TZ)

        timing = handler.prepare_order(
            mode=OrderTimingMode.MARKET_OPEN,
            dt=dt,
        )

        # Should queue for today's open (in 1 second)
        assert timing.execute_at.date() == dt.date()

    def test_prepare_order_just_after_close(self, handler):
        """Test preparing order just after market close."""
        # 4:00:01 PM ET
        dt = datetime(2026, 1, 27, 16, 0, 1, tzinfo=MarketCalendar.TZ)

        timing = handler.prepare_order(
            mode=OrderTimingMode.MARKET_OPEN,
            dt=dt,
        )

        # Should queue for next day
        assert timing.execute_at.date() == datetime(2026, 1, 28).date()

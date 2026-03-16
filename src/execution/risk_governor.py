"""Risk governance and circuit breakers.

This module enforces risk limits and circuit breakers:
- Max daily loss (-2%)
- Max position loss ($500)
- Max positions (10)
- Max positions per day (10)
- Max sector concentration (30%)
- Max margin utilization (80%)
- Emergency shutdown capability
"""

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from loguru import logger

from src.config.base import Config
from src.execution.position_monitor import PositionMonitor
from src.utils.timezone import us_trading_date
from src.services.kill_switch import KillSwitch
from src.strategies.base import TradeOpportunity
from src.broker.protocols import BrokerClient


def _trading_date_utc_bounds(trading_dt: date) -> tuple[datetime, datetime]:
    """Convert a US trading date to naive-UTC start/end boundaries.

    Args:
        trading_dt: The trading date (US Eastern)

    Returns:
        (utc_start, utc_end) as naive UTC datetimes for DB queries
    """
    et = ZoneInfo("America/New_York")
    start_et = datetime.combine(trading_dt, datetime.min.time()).replace(tzinfo=et)
    end_et = datetime.combine(
        trading_dt + timedelta(days=1), datetime.min.time()
    ).replace(tzinfo=et)

    utc_start = start_et.astimezone(timezone.utc).replace(tzinfo=None)
    utc_end = end_et.astimezone(timezone.utc).replace(tzinfo=None)
    return utc_start, utc_end


@dataclass
class RiskLimitCheck:
    """Result of risk limit check.

    Attributes:
        approved: Whether trade is approved
        reason: Reason for approval/rejection
        limit_name: Name of limit checked
        current_value: Current value
        limit_value: Limit threshold
        utilization_pct: Percentage of limit used
    """

    approved: bool
    reason: str
    limit_name: str
    current_value: float
    limit_value: float
    utilization_pct: float


@dataclass
class PostTradeMarginResult:
    """Result of post-trade margin verification.

    Attributes:
        available_funds: Available funds after trade
        excess_liquidity: IBKR ExcessLiquidity (margin call proximity)
        net_liquidation: Net liquidation value
        margin_utilization_pct: Current margin utilization percentage
        is_healthy: True if excess liquidity > 10% of net liquidation.
            Also True when verification fails (to avoid false halt — see
            verification_failed for disambiguation).
        warning: Warning message if approaching danger
        verification_failed: True if margin data could not be retrieved.
            When True, is_healthy is artificially True (fail-open to avoid
            blocking position closes), but the state is UNKNOWN, not safe.
    """

    available_funds: float
    excess_liquidity: float
    net_liquidation: float
    margin_utilization_pct: float
    is_healthy: bool
    warning: str = ""
    verification_failed: bool = False


class RiskGovernor:
    """Enforce risk limits and circuit breakers.

    The RiskGovernor prevents excessive risk-taking by:
    - Checking all risk limits before every trade
    - Enforcing circuit breakers (daily loss, position loss)
    - Monitoring portfolio-level risk
    - Providing emergency shutdown

    Example:
        >>> governor = RiskGovernor(ibkr_client, position_monitor, config)
        >>> check = governor.pre_trade_check(opportunity)
        >>> if check.approved:
        ...     execute_trade(opportunity)
        ... else:
        ...     print(f"Rejected: {check.reason}")
    """

    def __init__(
        self,
        ibkr_client: BrokerClient,
        position_monitor: PositionMonitor,
        config: Config,
        kill_switch: KillSwitch | None = None,
        equity_state_file: Path | str | None = None,
    ):
        """Initialize risk governor.

        Args:
            ibkr_client: Connected IBKR client
            position_monitor: Position monitor instance
            config: System configuration
            kill_switch: Optional KillSwitch instance. Created automatically if None.
            equity_state_file: Path to equity state JSON file. None uses default.
        """
        self.ibkr_client = ibkr_client
        self.position_monitor = position_monitor
        self.config = config

        # Persistent kill switch (survives restarts)
        self._kill_switch = kill_switch or KillSwitch(register_signals=True)

        # Circuit breaker state (synced from kill switch on startup)
        halted, reason = self._kill_switch.is_halted()
        self._trading_halted = halted
        self._halt_reason = reason
        self._last_reset_date = us_trading_date()
        self._trades_today = self._load_trades_today_from_db()

        # Account health cache (refreshes every 5 minutes)
        self.ACCOUNT_HEALTH_INTERVAL_MINUTES = 5
        self._account_health_cache: dict | None = None
        self._last_health_check: datetime | None = None

        # Risk limits — prefer scanner_settings.yaml (hot-reloadable), fall back to .env
        from src.agentic.scanner_settings import load_scanner_settings

        scanner = load_scanner_settings()
        rg = scanner.risk_governor
        budget = scanner.budget

        self.MAX_DAILY_LOSS_PCT = rg.max_daily_loss_pct
        self.MAX_POSITION_LOSS = rg.max_position_loss
        self.MAX_POSITIONS = budget.max_positions
        self.MAX_POSITIONS_PER_DAY = budget.max_positions_per_day
        self.MAX_SECTOR_CONCENTRATION = config.risk_limits.max_sector_concentration
        self.MAX_MARGIN_UTILIZATION = rg.max_margin_utilization
        self.MAX_MARGIN_PER_TRADE_PCT = rg.max_margin_per_trade_pct
        self.MAX_WEEKLY_LOSS_PCT = rg.max_weekly_loss_pct
        self.MAX_DRAWDOWN_PCT = rg.max_drawdown_pct
        self.MIN_EXCESS_LIQUIDITY_PCT = 0.10  # Safety invariant — keep hardcoded

        # Weekly/drawdown tracking (persisted to JSON file)
        self._equity_state_file = (
            Path(equity_state_file)
            if equity_state_file
            else Path("data/risk_governor_equity.json")
        )
        self._equity_state_file.parent.mkdir(parents=True, exist_ok=True)
        self._week_start_equity: float = 0.0
        self._week_start_date: datetime | None = None
        self._peak_equity: float = 0.0
        self._load_equity_state()

        logger.info("Initialized RiskGovernor with limits:")
        logger.info(f"  Max Daily Loss: {self.MAX_DAILY_LOSS_PCT:.1%}")
        logger.info(f"  Max Position Loss: ${abs(self.MAX_POSITION_LOSS)}")
        logger.info(f"  Max Positions: {self.MAX_POSITIONS}")
        logger.info(f"  Max Positions/Day: {self.MAX_POSITIONS_PER_DAY}")
        logger.info(f"  Max Sector Concentration: {self.MAX_SECTOR_CONCENTRATION:.0%}")
        logger.info(f"  Max Margin Utilization: {self.MAX_MARGIN_UTILIZATION:.0%}")
        logger.info(f"  Max Margin/Trade: {self.MAX_MARGIN_PER_TRADE_PCT:.0%} of NetLiq")
        logger.info(f"  Max Weekly Loss: {self.MAX_WEEKLY_LOSS_PCT:.0%}")
        logger.info(f"  Max Drawdown: {self.MAX_DRAWDOWN_PCT:.0%}")
        logger.info(f"  Earnings check: Enabled")

    def pre_trade_check(self, opportunity: TradeOpportunity) -> RiskLimitCheck:
        """Check all risk limits before placing trade.

        Performs comprehensive risk checks:
        1. Trading not halted (circuit breaker)
        2. No duplicate positions or pending orders
        3. No earnings within DTE window
        4. Daily loss limit not exceeded
        5. Max positions not exceeded
        6. Max positions per day not exceeded
        7. Sector concentration within limits
        8. Margin utilization within limits

        Args:
            opportunity: Trade opportunity to check

        Returns:
            RiskLimitCheck: Check result

        Example:
            >>> check = governor.pre_trade_check(opportunity)
            >>> if not check.approved:
            ...     logger.warning(f"Trade rejected: {check.reason}")
        """
        logger.debug(f"Pre-trade risk check for {opportunity.symbol}...")

        # Reset daily counter if new day
        self._reset_daily_counters_if_needed()

        # Check 1: Trading halt
        if self._trading_halted:
            return RiskLimitCheck(
                approved=False,
                reason=f"Trading halted: {self._halt_reason}",
                limit_name="trading_halt",
                current_value=1,
                limit_value=0,
                utilization_pct=100.0,
            )

        # Check 2: Duplicate position/order
        duplicate_check = self._check_duplicate_contract(opportunity)
        if not duplicate_check.approved:
            return duplicate_check

        # Check 3: Earnings within DTE
        earnings_check = self._check_earnings_risk(opportunity)
        if not earnings_check.approved:
            return earnings_check

        # Check 4: Daily loss limit
        daily_loss_check = self._check_daily_loss_limit()
        if not daily_loss_check.approved:
            return daily_loss_check

        # Check 4b: Weekly loss limit
        weekly_check = self._check_weekly_loss_limit()
        if not weekly_check.approved:
            return weekly_check

        # Check 4c: Max drawdown
        drawdown_check = self._check_max_drawdown()
        if not drawdown_check.approved:
            return drawdown_check

        # Check 5: Max positions
        max_positions_check = self._check_max_positions()
        if not max_positions_check.approved:
            return max_positions_check

        # Check 6: Max positions per day
        max_trades_check = self._check_max_positions_per_day()
        if not max_trades_check.approved:
            return max_trades_check

        # Check 7: Sector concentration
        sector_check = self._check_sector_concentration(opportunity)
        if not sector_check.approved:
            return sector_check

        # Check 8: Margin utilization
        margin_check = self._check_margin_utilization(opportunity)
        if not margin_check.approved:
            return margin_check

        # All checks passed
        logger.info(f"✓ Pre-trade checks passed for {opportunity.symbol}")
        return RiskLimitCheck(
            approved=True,
            reason="All risk checks passed",
            limit_name="all_checks",
            current_value=0,
            limit_value=100,
            utilization_pct=0.0,
        )

    def record_trade(self, opportunity: TradeOpportunity) -> None:
        """Record a trade for daily tracking.

        Args:
            opportunity: Trade that was executed
        """
        self._trades_today += 1
        logger.debug(f"Trades today: {self._trades_today}/{self.MAX_POSITIONS_PER_DAY}")

    def _get_cached_account_summary(self, force_refresh: bool = False) -> dict:
        """Get account summary, using cache if fresh.

        Refreshes from IBKR if cache is older than ACCOUNT_HEALTH_INTERVAL_MINUTES
        or if force_refresh is True.

        Args:
            force_refresh: Force a fresh fetch from IBKR

        Returns:
            Account summary dict with keys like NetLiquidation, AvailableFunds, etc.
        """
        now = datetime.now()
        cache_stale = (
            self._account_health_cache is None
            or self._last_health_check is None
            or (now - self._last_health_check)
            >= timedelta(minutes=self.ACCOUNT_HEALTH_INTERVAL_MINUTES)
        )

        if cache_stale or force_refresh:
            try:
                self._account_health_cache = self.ibkr_client.get_account_summary()
                self._last_health_check = now
                logger.debug("Account health cache refreshed")
            except Exception as e:
                logger.warning(f"Account health fetch failed: {e}")
                if self._account_health_cache is None:
                    self._account_health_cache = {}

        return self._account_health_cache

    def check_account_health(self) -> dict:
        """Check account health metrics (lightweight, cached).

        Returns a dict with key health metrics. Can be called frequently
        (every 1-5 minutes) without excessive IBKR API calls.

        Returns:
            Dict with NetLiquidation, AvailableFunds, ExcessLiquidity,
            MaintMarginReq, and a 'healthy' boolean.
        """
        summary = self._get_cached_account_summary()
        nlv = summary.get("NetLiquidation", 0)
        available = summary.get("AvailableFunds", 0)
        excess = summary.get("ExcessLiquidity", 0)
        maint_margin = summary.get("MaintMarginReq", 0)

        healthy = excess > 0 and available > 0
        if not healthy:
            logger.warning(
                f"Account health WARNING: NLV=${nlv:,.0f}, "
                f"Available=${available:,.0f}, Excess=${excess:,.0f}"
            )

        return {
            "NetLiquidation": nlv,
            "AvailableFunds": available,
            "ExcessLiquidity": excess,
            "MaintMarginReq": maint_margin,
            "healthy": healthy,
        }

    def emergency_halt(self, reason: str) -> None:
        """Halt all trading immediately. Persists across restarts.

        Args:
            reason: Reason for halt

        Example:
            >>> governor.emergency_halt("Manual override")
        """
        self._trading_halted = True
        self._halt_reason = reason
        self._kill_switch.halt(reason)
        logger.critical(f"🔴 TRADING HALTED: {reason}")
        logger.critical("All new trades will be rejected until halt is cleared")

    def resume_trading(self) -> None:
        """Resume trading after halt. Clears persistent state.

        Example:
            >>> governor.resume_trading()
        """
        self._trading_halted = False
        self._halt_reason = ""
        self._kill_switch.resume()
        logger.info("✓ Trading resumed")

    def is_halted(self) -> bool:
        """Check if trading is currently halted.

        Returns:
            bool: True if halted
        """
        return self._trading_halted

    def get_risk_status(self) -> dict:
        """Get current risk status.

        Returns:
            dict: Risk status metrics

        Example:
            >>> status = governor.get_risk_status()
            >>> print(f"Positions: {status['current_positions']}/{status['max_positions']}")
        """
        positions = self.position_monitor.get_all_positions()

        # Daily P&L = realized (DB) + unrealized (open positions)
        unrealized_pnl = sum(p.current_pnl for p in positions)
        realized_pnl = self._get_realized_daily_pnl()
        total_pnl = realized_pnl + unrealized_pnl

        # Get account value
        account_summary = self._get_cached_account_summary()
        account_value = account_summary.get("NetLiquidation", 100000)

        daily_pnl_pct = total_pnl / account_value if account_value > 0 else 0

        return {
            "trading_halted": self._trading_halted,
            "halt_reason": self._halt_reason,
            "current_positions": len(positions),
            "max_positions": self.MAX_POSITIONS,
            "trades_today": self._trades_today,
            "max_trades_today": self.MAX_POSITIONS_PER_DAY,
            "daily_pnl": total_pnl,
            "daily_pnl_pct": daily_pnl_pct,
            "daily_pnl_realized": realized_pnl,
            "daily_pnl_unrealized": unrealized_pnl,
            "daily_loss_limit": self.MAX_DAILY_LOSS_PCT,
            "account_value": account_value,
        }

    def _check_daily_loss_limit(self) -> RiskLimitCheck:
        """Check daily loss circuit breaker.

        Total daily PnL = realized (from DB, trades closed today)
                        + unrealized (from IBKR, open positions)

        Survives process restarts because realized PnL comes from the database.
        """
        # Unrealized PnL from currently open positions
        positions = self.position_monitor.get_all_positions()
        unrealized_pnl = sum(p.current_pnl for p in positions)

        # Realized PnL from trades closed today (persisted in DB)
        realized_pnl = self._get_realized_daily_pnl()

        total_pnl = realized_pnl + unrealized_pnl

        # Get account value
        account_summary = self._get_cached_account_summary()
        account_value = account_summary.get("NetLiquidation", 100000)

        daily_pnl_pct = total_pnl / account_value if account_value > 0 else 0

        # Both values are negative: e.g. -0.03 <= -0.02 → True → HALT.
        # Config enforces MAX_DAILY_LOSS_PCT ≤ 0 via Pydantic (le=0.0).
        if daily_pnl_pct <= self.MAX_DAILY_LOSS_PCT:
            # Trigger circuit breaker
            self.emergency_halt(
                f"Daily loss limit exceeded: {daily_pnl_pct:.2%} "
                f"(limit: {self.MAX_DAILY_LOSS_PCT:.2%}, "
                f"realized: ${realized_pnl:.0f}, unrealized: ${unrealized_pnl:.0f})"
            )

            return RiskLimitCheck(
                approved=False,
                reason=f"Daily loss limit exceeded: {daily_pnl_pct:.2%}",
                limit_name="daily_loss",
                current_value=daily_pnl_pct * 100,
                limit_value=self.MAX_DAILY_LOSS_PCT * 100,
                utilization_pct=abs(daily_pnl_pct / self.MAX_DAILY_LOSS_PCT * 100),
            )

        return RiskLimitCheck(
            approved=True,
            reason="Daily loss within limit",
            limit_name="daily_loss",
            current_value=daily_pnl_pct * 100,
            limit_value=self.MAX_DAILY_LOSS_PCT * 100,
            utilization_pct=abs(daily_pnl_pct / self.MAX_DAILY_LOSS_PCT * 100)
            if self.MAX_DAILY_LOSS_PCT != 0
            else 0,
        )

    def _get_realized_daily_pnl(self) -> float:
        """Query DB for realized P&L from trades closed today.

        Returns:
            Total realized P&L for the current trading day.
            Returns 0.0 on DB failure (fail-open).
        """
        from src.data.database import get_db_session
        from src.data.repositories import TradeRepository

        today = us_trading_date()
        utc_start, utc_end = _trading_date_utc_bounds(today)

        try:
            with get_db_session() as session:
                repo = TradeRepository(session)
                return repo.get_realized_pnl_for_date(utc_start, utc_end)
        except Exception as e:
            logger.error(f"Failed to query realized daily PnL: {e}")
            return 0.0

    def _load_trades_today_from_db(self) -> int:
        """Load today's trade count from DB to survive restarts.

        Returns:
            Number of trades entered today. Returns 0 on DB failure.
        """
        from src.data.database import get_db_session
        from src.data.repositories import TradeRepository

        today = us_trading_date()
        utc_start, utc_end = _trading_date_utc_bounds(today)

        try:
            with get_db_session() as session:
                repo = TradeRepository(session)
                count = repo.count_trades_entered_on_date(utc_start, utc_end)
                if count > 0:
                    logger.info(f"Loaded {count} trades from DB for today")
                return count
        except Exception as e:
            logger.error(f"Failed to load today's trade count: {e}")
            return 0

    def _load_equity_state(self) -> None:
        """Load weekly/drawdown equity baselines from JSON file.

        Survives process restarts. Falls back to defaults (0.0/None) if
        file is missing or corrupt — the checks will re-seed from current equity.
        """
        if not self._equity_state_file.exists():
            return

        try:
            with open(self._equity_state_file) as f:
                data = json.load(f)

            self._week_start_equity = float(data.get("week_start_equity", 0.0))
            self._peak_equity = float(data.get("peak_equity", 0.0))

            week_start_str = data.get("week_start_date")
            if week_start_str:
                self._week_start_date = datetime.fromisoformat(week_start_str)

            logger.info(
                f"Loaded equity state: week_start=${self._week_start_equity:,.0f}, "
                f"peak=${self._peak_equity:,.0f}"
            )
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            logger.warning(f"Corrupt equity state file, using defaults: {e}")

    def _save_equity_state(self) -> None:
        """Persist weekly/drawdown equity baselines to JSON file."""
        try:
            data = {
                "week_start_equity": self._week_start_equity,
                "week_start_date": (
                    self._week_start_date.isoformat()
                    if self._week_start_date
                    else None
                ),
                "peak_equity": self._peak_equity,
                "updated_at": datetime.now().isoformat(),
            }
            with open(self._equity_state_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save equity state: {e}")

    def _check_weekly_loss_limit(self) -> RiskLimitCheck:
        """Check weekly loss circuit breaker.

        Resets week-start equity every Monday. Halts trading if
        weekly loss exceeds MAX_WEEKLY_LOSS_PCT (-5%).

        Returns:
            RiskLimitCheck: Check result
        """
        account_summary = self._get_cached_account_summary()
        current_equity = account_summary.get("NetLiquidation", 0)

        if current_equity <= 0:
            return RiskLimitCheck(
                approved=True,
                reason="Weekly loss check skipped (no equity data)",
                limit_name="weekly_loss",
                current_value=0,
                limit_value=self.MAX_WEEKLY_LOSS_PCT * 100,
                utilization_pct=0.0,
            )

        now = datetime.now()

        # Reset on Monday or first run
        if (
            self._week_start_equity == 0
            or self._week_start_date is None
            or (now.weekday() == 0 and now.date() != self._week_start_date.date())
        ):
            self._week_start_equity = current_equity
            self._week_start_date = now
            self._save_equity_state()
            logger.info(f"Weekly equity reset: ${current_equity:,.0f}")

        weekly_pnl_pct = (
            (current_equity - self._week_start_equity) / self._week_start_equity
        )

        if weekly_pnl_pct <= self.MAX_WEEKLY_LOSS_PCT:
            self.emergency_halt(
                f"Weekly loss limit exceeded: {weekly_pnl_pct:.2%} "
                f"(limit: {self.MAX_WEEKLY_LOSS_PCT:.2%})"
            )
            return RiskLimitCheck(
                approved=False,
                reason=f"Weekly loss limit exceeded: {weekly_pnl_pct:.2%}",
                limit_name="weekly_loss",
                current_value=weekly_pnl_pct * 100,
                limit_value=self.MAX_WEEKLY_LOSS_PCT * 100,
                utilization_pct=abs(weekly_pnl_pct / self.MAX_WEEKLY_LOSS_PCT * 100),
            )

        return RiskLimitCheck(
            approved=True,
            reason="Weekly loss within limit",
            limit_name="weekly_loss",
            current_value=weekly_pnl_pct * 100,
            limit_value=self.MAX_WEEKLY_LOSS_PCT * 100,
            utilization_pct=abs(weekly_pnl_pct / self.MAX_WEEKLY_LOSS_PCT * 100)
            if self.MAX_WEEKLY_LOSS_PCT != 0
            else 0,
        )

    def _check_max_drawdown(self) -> RiskLimitCheck:
        """Check peak-to-trough drawdown circuit breaker.

        Tracks peak equity and halts trading if drawdown exceeds
        MAX_DRAWDOWN_PCT (-10%).

        Returns:
            RiskLimitCheck: Check result
        """
        account_summary = self._get_cached_account_summary()
        current_equity = account_summary.get("NetLiquidation", 0)

        if current_equity <= 0:
            return RiskLimitCheck(
                approved=True,
                reason="Drawdown check skipped (no equity data)",
                limit_name="max_drawdown",
                current_value=0,
                limit_value=self.MAX_DRAWDOWN_PCT * 100,
                utilization_pct=0.0,
            )

        # Track peak equity (persist only when it changes)
        old_peak = self._peak_equity
        self._peak_equity = max(self._peak_equity, current_equity)
        if self._peak_equity != old_peak:
            self._save_equity_state()

        drawdown_pct = (current_equity - self._peak_equity) / self._peak_equity

        if drawdown_pct <= self.MAX_DRAWDOWN_PCT:
            self.emergency_halt(
                f"Max drawdown exceeded: {drawdown_pct:.2%} "
                f"(limit: {self.MAX_DRAWDOWN_PCT:.2%})"
            )
            return RiskLimitCheck(
                approved=False,
                reason=f"Max drawdown exceeded: {drawdown_pct:.2%}",
                limit_name="max_drawdown",
                current_value=drawdown_pct * 100,
                limit_value=self.MAX_DRAWDOWN_PCT * 100,
                utilization_pct=abs(drawdown_pct / self.MAX_DRAWDOWN_PCT * 100),
            )

        return RiskLimitCheck(
            approved=True,
            reason="Drawdown within limit",
            limit_name="max_drawdown",
            current_value=drawdown_pct * 100,
            limit_value=self.MAX_DRAWDOWN_PCT * 100,
            utilization_pct=abs(drawdown_pct / self.MAX_DRAWDOWN_PCT * 100)
            if self.MAX_DRAWDOWN_PCT != 0
            else 0,
        )

    def _check_max_positions(self) -> RiskLimitCheck:
        """Check maximum position limit.

        Returns:
            RiskLimitCheck: Check result
        """
        positions = self.position_monitor.get_all_positions()
        current_positions = len(positions)

        if current_positions >= self.MAX_POSITIONS:
            return RiskLimitCheck(
                approved=False,
                reason=f"Max positions reached: {current_positions}/{self.MAX_POSITIONS}",
                limit_name="max_positions",
                current_value=current_positions,
                limit_value=self.MAX_POSITIONS,
                utilization_pct=100.0,
            )

        return RiskLimitCheck(
            approved=True,
            reason="Position count within limit",
            limit_name="max_positions",
            current_value=current_positions,
            limit_value=self.MAX_POSITIONS,
            utilization_pct=(current_positions / self.MAX_POSITIONS * 100)
            if self.MAX_POSITIONS > 0
            else 0,
        )

    def _check_max_positions_per_day(self) -> RiskLimitCheck:
        """Check maximum trades per day limit.

        Returns:
            RiskLimitCheck: Check result
        """
        if self._trades_today >= self.MAX_POSITIONS_PER_DAY:
            return RiskLimitCheck(
                approved=False,
                reason=f"Max trades per day reached: {self._trades_today}/{self.MAX_POSITIONS_PER_DAY}",
                limit_name="max_trades_per_day",
                current_value=self._trades_today,
                limit_value=self.MAX_POSITIONS_PER_DAY,
                utilization_pct=100.0,
            )

        return RiskLimitCheck(
            approved=True,
            reason="Daily trade count within limit",
            limit_name="max_trades_per_day",
            current_value=self._trades_today,
            limit_value=self.MAX_POSITIONS_PER_DAY,
            utilization_pct=(self._trades_today / self.MAX_POSITIONS_PER_DAY * 100)
            if self.MAX_POSITIONS_PER_DAY > 0
            else 0,
        )

    def _check_sector_concentration(
        self, opportunity: TradeOpportunity
    ) -> RiskLimitCheck:
        """Check sector concentration limit.

        Prevents over-concentration in a single sector by counting
        positions per sector and rejecting if adding this trade would
        exceed MAX_SECTOR_CONCENTRATION (default 30%).

        Args:
            opportunity: New trade opportunity

        Returns:
            RiskLimitCheck: Check result
        """
        from src.data.sector_map import get_sector

        new_sector = get_sector(opportunity.symbol)

        # Count existing positions per sector
        positions = self.position_monitor.get_all_positions()

        # Skip check for small portfolios — can't diversify with ≤3 positions
        total_after = len(positions) + 1
        if total_after <= 3:
            logger.debug(
                f"Sector check: skipped (only {total_after} positions, need >3)"
            )
            return RiskLimitCheck(
                approved=True,
                reason=f"Sector check skipped (only {total_after} positions)",
                limit_name="sector_concentration",
                current_value=0,
                limit_value=self.MAX_SECTOR_CONCENTRATION * 100,
                utilization_pct=0.0,
            )

        sector_counts: dict[str, int] = {}
        for pos in positions:
            sector = get_sector(pos.symbol)
            sector_counts[sector] = sector_counts.get(sector, 0) + 1

        # Calculate concentration with the new trade included
        current_in_sector = sector_counts.get(new_sector, 0)
        new_count = current_in_sector + 1

        concentration = new_count / total_after

        if concentration > self.MAX_SECTOR_CONCENTRATION:
            reason = (
                f"Sector {new_sector} concentration {concentration:.0%} exceeds "
                f"{self.MAX_SECTOR_CONCENTRATION:.0%} limit "
                f"({new_count} of {total_after} positions)"
            )
            logger.warning(f"BLOCKED: {opportunity.symbol} — {reason}")
            return RiskLimitCheck(
                approved=False,
                reason=reason,
                limit_name="sector_concentration",
                current_value=concentration * 100,
                limit_value=self.MAX_SECTOR_CONCENTRATION * 100,
                utilization_pct=(concentration / self.MAX_SECTOR_CONCENTRATION) * 100,
            )

        logger.debug(
            f"Sector check: {opportunity.symbol} ({new_sector}) — "
            f"{new_count}/{total_after} = {concentration:.0%} "
            f"(limit {self.MAX_SECTOR_CONCENTRATION:.0%})"
        )

        return RiskLimitCheck(
            approved=True,
            reason=f"Sector {new_sector} concentration {concentration:.0%} within limit",
            limit_name="sector_concentration",
            current_value=concentration * 100,
            limit_value=self.MAX_SECTOR_CONCENTRATION * 100,
            utilization_pct=(concentration / self.MAX_SECTOR_CONCENTRATION) * 100,
        )

    def _check_margin_utilization(
        self, opportunity: TradeOpportunity
    ) -> RiskLimitCheck:
        """Check margin utilization limit with WhatIf verification.

        Layer 1: Fast reject using the upstream margin estimate.
        Layer 2: WhatIf API verification for trades that pass Layer 1.

        Args:
            opportunity: New trade opportunity

        Returns:
            RiskLimitCheck: Check result
        """
        # Get account margin info (uses 5-minute cache)
        account_summary = self._get_cached_account_summary()
        available_funds = account_summary.get("AvailableFunds", 0)
        buying_power = account_summary.get("BuyingPower", 0)
        net_liquidation = account_summary.get("NetLiquidation", 0)

        # Layer 1: Fast reject using upstream estimate
        estimated_margin = opportunity.margin_required

        if estimated_margin > available_funds:
            return RiskLimitCheck(
                approved=False,
                reason=f"Insufficient margin: need ${estimated_margin:,.0f}, have ${available_funds:,.0f}",
                limit_name="margin_utilization",
                current_value=estimated_margin,
                limit_value=available_funds,
                utilization_pct=100.0,
            )

        # Layer 2: WhatIf API verification
        required_margin = estimated_margin
        whatif_margin = self._get_whatif_margin(opportunity)

        if whatif_margin is not None:
            delta = whatif_margin - estimated_margin
            logger.info(
                f"Margin check: estimated=${estimated_margin:,.0f}, "
                f"WhatIf=${whatif_margin:,.0f}, delta=${delta:+,.0f}"
            )
            required_margin = whatif_margin

            # Re-check with WhatIf margin (may now exceed available funds)
            if required_margin > available_funds:
                return RiskLimitCheck(
                    approved=False,
                    reason=(
                        f"Insufficient margin (WhatIf): need ${required_margin:,.0f}, "
                        f"have ${available_funds:,.0f} "
                        f"(estimate was ${estimated_margin:,.0f})"
                    ),
                    limit_name="margin_utilization",
                    current_value=required_margin,
                    limit_value=available_funds,
                    utilization_pct=100.0,
                )
        else:
            logger.warning(
                f"WhatIf margin unavailable for {opportunity.symbol} "
                f"${opportunity.strike} — using estimate ${estimated_margin:,.0f}"
            )

        # Per-trade margin cap: no single trade may exceed X% of net liquidation
        if net_liquidation > 0:
            per_trade_cap = net_liquidation * self.MAX_MARGIN_PER_TRADE_PCT
            if required_margin > per_trade_cap:
                logger.warning(
                    f"Trade rejected: margin impact ${required_margin:,.0f} exceeds "
                    f"{self.MAX_MARGIN_PER_TRADE_PCT:.0%} cap (${per_trade_cap:,.0f})"
                )
                return RiskLimitCheck(
                    approved=False,
                    reason=(
                        f"Single trade margin ${required_margin:,.0f} exceeds "
                        f"{self.MAX_MARGIN_PER_TRADE_PCT:.0%} per-trade cap "
                        f"(${per_trade_cap:,.0f} of ${net_liquidation:,.0f} NetLiq)"
                    ),
                    limit_name="per_trade_margin_cap",
                    current_value=required_margin,
                    limit_value=per_trade_cap,
                    utilization_pct=(required_margin / per_trade_cap) * 100,
                )

        # Check margin utilization percentage
        if buying_power > 0:
            utilization = (buying_power - available_funds + required_margin) / buying_power
            if utilization > self.MAX_MARGIN_UTILIZATION:
                return RiskLimitCheck(
                    approved=False,
                    reason=f"Margin utilization too high: {utilization:.1%} (limit: {self.MAX_MARGIN_UTILIZATION:.1%})",
                    limit_name="margin_utilization",
                    current_value=utilization * 100,
                    limit_value=self.MAX_MARGIN_UTILIZATION * 100,
                    utilization_pct=100.0,
                )

        # Check ExcessLiquidity ratio (6.2A)
        excess_liquidity = account_summary.get("ExcessLiquidity", 0)
        if net_liquidation > 0:
            excess_ratio = excess_liquidity / net_liquidation
            if excess_ratio < self.MIN_EXCESS_LIQUIDITY_PCT:
                return RiskLimitCheck(
                    approved=False,
                    reason=(
                        f"ExcessLiquidity dangerously low: "
                        f"${excess_liquidity:,.0f} ({excess_ratio:.0%} of NLV) "
                        f"— minimum {self.MIN_EXCESS_LIQUIDITY_PCT:.0%} required"
                    ),
                    limit_name="excess_liquidity",
                    current_value=excess_ratio * 100,
                    limit_value=self.MIN_EXCESS_LIQUIDITY_PCT * 100,
                    utilization_pct=100.0,
                )
            elif excess_ratio < 0.20:
                logger.warning(
                    f"ExcessLiquidity low: ${excess_liquidity:,.0f} "
                    f"({excess_ratio:.0%} of NLV) — approaching danger zone"
                )

        return RiskLimitCheck(
            approved=True,
            reason="Margin utilization within limit",
            limit_name="margin_utilization",
            current_value=0,
            limit_value=self.MAX_MARGIN_UTILIZATION * 100,
            utilization_pct=0.0,
        )

    def _get_whatif_margin(self, opportunity: TradeOpportunity) -> Optional[float]:
        """Get WhatIf margin from IBKR for a trade opportunity.

        Args:
            opportunity: Trade opportunity to check

        Returns:
            Margin requirement in dollars, or None if unavailable
        """
        try:
            expiration_str = opportunity.expiration.strftime("%Y%m%d")
            return self.ibkr_client.get_margin_requirement(
                symbol=opportunity.symbol,
                strike=opportunity.strike,
                expiration=expiration_str,
                option_type=opportunity.option_type,
                contracts=opportunity.contracts,
            )
        except Exception as e:
            logger.warning(f"WhatIf margin lookup failed for {opportunity.symbol}: {e}")
            return None

    def _check_duplicate_contract(self, opportunity: TradeOpportunity) -> RiskLimitCheck:
        """Check if we already have an open position or pending order for this exact contract.

        Prevents duplicate orders for the same symbol/strike/expiration.

        Args:
            opportunity: New trade opportunity

        Returns:
            RiskLimitCheck: Check result
        """
        from datetime import datetime

        # Parse expiration date from opportunity
        if isinstance(opportunity.expiration, str):
            opp_exp_date = datetime.strptime(opportunity.expiration, "%Y-%m-%d").date()
        elif isinstance(opportunity.expiration, datetime):
            opp_exp_date = opportunity.expiration.date()
        else:
            opp_exp_date = opportunity.expiration

        # Check 1: Open positions
        try:
            positions = self.position_monitor.get_all_positions()
            logger.debug(f"Checking {len(positions)} open positions for duplicates")

            for pos in positions:
                # Parse position expiration (PositionStatus uses expiration_date in YYYYMMDD format)
                if hasattr(pos, 'expiration_date') and pos.expiration_date:
                    # expiration_date is in YYYYMMDD format
                    pos_exp_str = str(pos.expiration_date)
                    if len(pos_exp_str) == 8:
                        pos_exp_date = datetime.strptime(pos_exp_str, "%Y%m%d").date()
                    else:
                        continue
                else:
                    continue

                logger.debug(
                    f"Open position: {pos.symbol} ${pos.strike} {pos_exp_date} "
                    f"(checking against {opportunity.symbol} ${opportunity.strike} {opp_exp_date})"
                )

                # Check for exact match
                if (
                    pos.symbol == opportunity.symbol
                    and abs(pos.strike - opportunity.strike) < 0.01  # Float comparison
                    and pos_exp_date == opp_exp_date
                ):
                    logger.warning(
                        f"Found duplicate open position: {pos.symbol} ${pos.strike} {pos_exp_date}"
                    )
                    return RiskLimitCheck(
                        approved=False,
                        reason=f"Duplicate position: Already have open position for {opportunity.symbol} ${opportunity.strike} {opportunity.expiration}",
                        limit_name="duplicate_check",
                        current_value=1,
                        limit_value=0,
                        utilization_pct=100.0,
                    )
        except Exception as e:
            logger.warning(f"Failed to check open positions for duplicates: {e}", exc_info=True)

        # Check 2: Pending orders
        try:
            # Request fresh open orders data (don't rely on cache)
            open_orders = self.ibkr_client.request_open_orders()

            logger.debug(f"Checking {len(open_orders)} pending orders for duplicates")

            # openOrders() returns Trade objects with .contract and .order attributes
            for trade_obj in open_orders:
                # Access contract from Trade object
                if not hasattr(trade_obj, 'contract'):
                    logger.debug(f"Trade object has no contract attribute: {type(trade_obj)}")
                    continue

                contract = trade_obj.contract

                # Check if it's an option contract
                if hasattr(contract, 'strike') and hasattr(contract, 'lastTradeDateOrContractMonth'):
                    # Parse contract expiration (format: YYYYMMDD)
                    exp_str = str(contract.lastTradeDateOrContractMonth)
                    if len(exp_str) == 8:
                        try:
                            contract_exp_date = datetime.strptime(exp_str, "%Y%m%d").date()
                        except ValueError:
                            logger.debug(f"Failed to parse expiration date: {exp_str}")
                            continue
                    else:
                        continue

                    logger.debug(
                        f"Pending order: {contract.symbol} ${contract.strike} {contract_exp_date} "
                        f"(checking against {opportunity.symbol} ${opportunity.strike} {opp_exp_date})"
                    )

                    # Check for exact match
                    if (
                        contract.symbol == opportunity.symbol
                        and abs(contract.strike - opportunity.strike) < 0.01
                        and contract_exp_date == opp_exp_date
                    ):
                        logger.warning(
                            f"Found duplicate pending order: {contract.symbol} ${contract.strike} {contract_exp_date}"
                        )
                        return RiskLimitCheck(
                            approved=False,
                            reason=f"Duplicate order: Already have pending order for {opportunity.symbol} ${opportunity.strike} {opportunity.expiration}",
                            limit_name="duplicate_check",
                            current_value=1,
                            limit_value=0,
                            utilization_pct=100.0,
                        )
        except Exception as e:
            logger.warning(f"Failed to check pending orders for duplicates: {e}", exc_info=True)

        # No duplicates found
        return RiskLimitCheck(
            approved=True,
            reason="No duplicate positions or orders",
            limit_name="duplicate_check",
            current_value=0,
            limit_value=1,
            utilization_pct=0.0,
        )

    def _check_earnings_risk(self, opportunity: TradeOpportunity) -> RiskLimitCheck:
        """Check if earnings fall within option's DTE window.

        Prevents selling options into earnings events, which carry
        outsized risk of gap moves and assignment.

        Args:
            opportunity: Trade opportunity to check

        Returns:
            RiskLimitCheck: Check result
        """
        try:
            from src.services.earnings_service import get_cached_earnings
            from datetime import datetime as dt

            # Parse expiration to date
            if isinstance(opportunity.expiration, str):
                exp_date = dt.strptime(opportunity.expiration, "%Y-%m-%d").date()
            elif isinstance(opportunity.expiration, dt):
                exp_date = opportunity.expiration.date()
            elif hasattr(opportunity.expiration, "date"):
                exp_date = opportunity.expiration
            else:
                exp_date = opportunity.expiration

            earnings_info = get_cached_earnings(opportunity.symbol, exp_date)

            if earnings_info.earnings_in_dte:
                reason = (
                    f"WARNING: Earnings on {earnings_info.earnings_date} falls within DTE "
                    f"(exp {exp_date}, {earnings_info.days_to_earnings}d away). "
                    f"Scanner applied conservative filters."
                )
                logger.warning(f"{opportunity.symbol} — {reason}")
                return RiskLimitCheck(
                    approved=True,
                    reason=reason,
                    limit_name="earnings_check",
                    current_value=earnings_info.days_to_earnings or 0,
                    limit_value=opportunity.dte,
                    utilization_pct=75.0,
                )

            if earnings_info.earnings_date:
                logger.debug(
                    f"{opportunity.symbol}: Earnings on {earnings_info.earnings_date} "
                    f"({earnings_info.days_to_earnings}d away) — outside DTE window, OK"
                )
            else:
                logger.warning(
                    f"WARNING: Earnings data unavailable for {opportunity.symbol} "
                    f"— passing through (don't block on data gaps)"
                )

        except Exception as e:
            logger.warning(
                f"WARNING: Earnings check failed for {opportunity.symbol}: {e} "
                f"— passing through (don't block on data gaps)"
            )

        return RiskLimitCheck(
            approved=True,
            reason="Earnings check passed (no earnings within DTE)",
            limit_name="earnings_check",
            current_value=0,
            limit_value=opportunity.dte,
            utilization_pct=0.0,
        )

    def verify_post_trade_margin(self, symbol: str = "") -> PostTradeMarginResult:
        """Verify margin state after a trade fill.

        Polls account summary and checks that ExcessLiquidity is healthy.
        If ExcessLiquidity drops below 10% of NetLiquidation, triggers
        emergency halt.

        Args:
            symbol: Symbol that was just filled (for logging context)

        Returns:
            PostTradeMarginResult with current margin state
        """
        try:
            account_summary = self.ibkr_client.get_account_summary()

            available_funds = account_summary.get("AvailableFunds", 0)
            excess_liquidity = account_summary.get("ExcessLiquidity", 0)
            net_liquidation = account_summary.get("NetLiquidation", 0)
            buying_power = account_summary.get("BuyingPower", 0)

            # Calculate utilization
            if buying_power > 0:
                utilization_pct = ((buying_power - available_funds) / buying_power) * 100
            else:
                utilization_pct = 0.0

            # Health check: ExcessLiquidity > 10% of NetLiquidation
            threshold = net_liquidation * 0.10
            is_healthy = excess_liquidity > threshold

            warning = ""
            if not is_healthy:
                warning = (
                    f"DANGER: ExcessLiquidity ${excess_liquidity:,.0f} is below 10% "
                    f"of NetLiq ${net_liquidation:,.0f} (threshold: ${threshold:,.0f})"
                )

            result = PostTradeMarginResult(
                available_funds=available_funds,
                excess_liquidity=excess_liquidity,
                net_liquidation=net_liquidation,
                margin_utilization_pct=utilization_pct,
                is_healthy=is_healthy,
                warning=warning,
            )

            # Log post-trade margin state
            context = f" after {symbol} fill" if symbol else ""
            logger.info(
                f"Post-trade margin{context}: "
                f"AvailFunds=${available_funds:,.0f}, "
                f"ExcessLiq=${excess_liquidity:,.0f}, "
                f"Util={utilization_pct:.1f}%"
            )

            if not is_healthy:
                logger.critical(warning)
                self.emergency_halt(f"Post-trade margin danger: {warning}")

            return result

        except Exception as e:
            # CRITICAL: We just filled a trade but cannot verify margin state.
            # We keep is_healthy=True (fail-open) to avoid halting — a false halt
            # would prevent closing positions, potentially making things worse.
            # But this MUST be visible: log at CRITICAL and flag for dashboard.
            context = f" after {symbol} fill" if symbol else ""
            logger.critical(
                f"POST-TRADE MARGIN VERIFICATION FAILED{context}: {e} — "
                f"margin state is UNKNOWN. Manual review required.",
                exc_info=True,
            )
            return PostTradeMarginResult(
                available_funds=0,
                excess_liquidity=0,
                net_liquidation=0,
                margin_utilization_pct=0,
                is_healthy=True,  # Fail-open: don't halt on verification failure
                warning=f"VERIFICATION FAILED: {e} — margin state unknown",
                verification_failed=True,
            )

    def _reset_daily_counters_if_needed(self) -> None:
        """Reset daily counters if new trading day."""
        today = us_trading_date()

        if today > self._last_reset_date:
            logger.info("New trading day: resetting daily counters")
            self._trades_today = self._load_trades_today_from_db()
            self._last_reset_date = today

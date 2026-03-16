"""Data access layer for database operations.

This module provides repository classes for clean data access patterns,
separating business logic from database operations.
"""

from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import desc, func, or_
from sqlalchemy.orm import Session

from src.utils.timezone import utc_now
from src.data.models import (
    AuditLog,
    Experiment,
    LearningHistory,
    Pattern,
    Position,
    ScanOpportunity,
    ScanResult,
    StockPosition,
    Trade,
)


class TradeRepository:
    """Repository for Trade operations."""

    def __init__(self, session: Session):
        """Initialize repository with database session.

        Args:
            session: SQLAlchemy session
        """
        self.session = session

    def create(self, trade: Trade) -> Trade:
        """Create a new trade."""
        self.session.add(trade)
        self.session.flush()
        return trade

    def get_by_id(self, trade_id: str) -> Optional[Trade]:
        """Get trade by ID."""
        return self.session.query(Trade).filter(Trade.trade_id == trade_id).first()

    def get_by_canonical_key(self, canonical_key: str) -> Optional[Trade]:
        """Find any trade whose trade_id matches the canonical position key.

        Catches both exact matches (e.g., ALAB_105.0_20260306_P) and
        suffixed variants (_3279, _imported, etc.) by checking trade_id
        starts with the canonical key.

        Returns the first match (preferring open trades).
        """
        return (
            self.session.query(Trade)
            .filter(Trade.trade_id.like(f"{canonical_key}%"))
            .order_by(Trade.exit_date.is_(None).desc())  # open trades first
            .first()
        )

    def get_all(
        self,
        limit: Optional[int] = None,
        trade_source: Optional[list[str]] = None,
    ) -> list[Trade]:
        """Get all trades, optionally limited and filtered by source.

        Args:
            limit: Maximum number of trades to return
            trade_source: Filter by trade source(s), e.g. ["live", "paper"].
                None returns all trades (backward compatible).
        """
        query = self.session.query(Trade).order_by(desc(Trade.entry_date))
        if trade_source:
            query = query.filter(Trade.trade_source.in_(trade_source))
        if limit:
            query = query.limit(limit)
        return query.all()

    def get_open_trades(
        self, trade_source: Optional[list[str]] = None,
    ) -> list[Trade]:
        """Get all open (unclosed) trades.

        Args:
            trade_source: Filter by trade source(s). None returns all.
        """
        query = self.session.query(Trade).filter(Trade.exit_date.is_(None))
        if trade_source:
            query = query.filter(Trade.trade_source.in_(trade_source))
        return query.all()

    def get_open_positions(self) -> list[Trade]:
        """Get all open positions (alias for get_open_trades for reconciliation compatibility)."""
        return self.get_open_trades()

    def find_open_position(
        self,
        symbol: str,
        strike: float,
        expiration: date,
    ) -> Optional[Trade]:
        """Find an open position matching symbol, strike, and expiration.

        Args:
            symbol: Stock symbol (e.g. 'AAPL')
            strike: Strike price
            expiration: Expiration date

        Returns:
            Matching Trade if found, None otherwise
        """
        return (
            self.session.query(Trade)
            .filter(
                Trade.exit_date.is_(None),
                Trade.symbol == symbol,
                Trade.strike == strike,
                Trade.expiration == expiration,
            )
            .first()
        )

    def get_closed_trades(
        self,
        limit: Optional[int] = None,
        trade_source: Optional[list[str]] = None,
        account_id: Optional[str] = None,
    ) -> list[Trade]:
        """Get all closed trades.

        Args:
            limit: Maximum number of trades to return
            trade_source: Filter by trade source(s). None returns all.
            account_id: Filter by IBKR account ID. None returns all.
        """
        query = (
            self.session.query(Trade)
            .filter(Trade.exit_date.isnot(None))
            .order_by(desc(Trade.exit_date))
        )
        if trade_source:
            query = query.filter(Trade.trade_source.in_(trade_source))
        if account_id:
            query = query.filter(Trade.account_id == account_id)
        if limit:
            query = query.limit(limit)
        return query.all()

    def get_recent_trades(
        self,
        days: int = 30,
        trade_source: Optional[list[str]] = None,
    ) -> list[Trade]:
        """Get trades from last N days.

        Args:
            days: Number of days to look back
            trade_source: Filter by trade source(s). None returns all.
        """
        cutoff_date = datetime.now() - timedelta(days=days)
        query = (
            self.session.query(Trade)
            .filter(Trade.entry_date >= cutoff_date)
            .order_by(desc(Trade.entry_date))
        )
        if trade_source:
            query = query.filter(Trade.trade_source.in_(trade_source))
        return query.all()

    def get_trades_by_date(self, trade_date: date) -> list[Trade]:
        """Get all trades from a specific date.

        Args:
            trade_date: Date to filter trades by (entry date)

        Returns:
            List of trades entered on the specified date
        """
        start_datetime = datetime.combine(trade_date, datetime.min.time())
        end_datetime = datetime.combine(trade_date, datetime.max.time())
        return (
            self.session.query(Trade)
            .filter(Trade.entry_date >= start_datetime)
            .filter(Trade.entry_date <= end_datetime)
            .order_by(desc(Trade.entry_date))
            .all()
        )

    def get_trades_by_source(self, trade_source: str) -> list[Trade]:
        """Get all trades with a specific source tag.

        Args:
            trade_source: Source value (e.g. 'live', 'ibkr_import')

        Returns:
            List of trades with the given source
        """
        return (
            self.session.query(Trade)
            .filter(Trade.trade_source == trade_source)
            .order_by(desc(Trade.entry_date))
            .all()
        )

    def get_realized_pnl_for_date(
        self,
        utc_start: datetime,
        utc_end: datetime,
        exclude_paper: bool = True,
    ) -> float:
        """Sum realized P&L for trades closed within a UTC time range.

        Args:
            utc_start: Start of period (naive UTC)
            utc_end: End of period (naive UTC)
            exclude_paper: If True, exclude paper trades

        Returns:
            Total realized P&L as float (0.0 if no trades)
        """
        query = (
            self.session.query(func.coalesce(func.sum(Trade.profit_loss), 0.0))
            .filter(Trade.exit_date >= utc_start)
            .filter(Trade.exit_date < utc_end)
            .filter(Trade.profit_loss.isnot(None))
        )
        if exclude_paper:
            query = query.filter(
                or_(Trade.trade_source.is_(None), Trade.trade_source != "paper")
            )
        return float(query.scalar())

    def count_trades_entered_on_date(
        self,
        utc_start: datetime,
        utc_end: datetime,
        exclude_paper: bool = True,
    ) -> int:
        """Count trades entered within a UTC time range.

        Args:
            utc_start: Start of period (naive UTC)
            utc_end: End of period (naive UTC)
            exclude_paper: If True, exclude paper trades

        Returns:
            Number of trades entered in the period
        """
        query = (
            self.session.query(func.count(Trade.id))
            .filter(Trade.entry_date >= utc_start)
            .filter(Trade.entry_date < utc_end)
        )
        if exclude_paper:
            query = query.filter(
                or_(Trade.trade_source.is_(None), Trade.trade_source != "paper")
            )
        return int(query.scalar())

    def update(self, trade: Trade) -> Trade:
        """Update an existing trade."""
        self.session.flush()
        return trade


class ExperimentRepository:
    """Repository for Experiment operations."""

    def __init__(self, session: Session):
        """Initialize repository with database session."""
        self.session = session

    def create(self, experiment: Experiment) -> Experiment:
        """Create a new experiment."""
        self.session.add(experiment)
        self.session.flush()
        return experiment

    def get_by_id(self, experiment_id: str) -> Optional[Experiment]:
        """Get experiment by ID."""
        return (
            self.session.query(Experiment)
            .filter(Experiment.experiment_id == experiment_id)
            .first()
        )

    def get_active_experiments(self) -> list[Experiment]:
        """Get all active experiments."""
        return (
            self.session.query(Experiment).filter(Experiment.status == "active").all()
        )

    def get_completed_experiments(self) -> list[Experiment]:
        """Get all completed experiments."""
        return (
            self.session.query(Experiment)
            .filter(Experiment.status.in_(["completed", "adopted", "rejected"]))
            .order_by(desc(Experiment.end_date))
            .all()
        )

    def update(self, experiment: Experiment) -> Experiment:
        """Update an existing experiment."""
        self.session.flush()
        return experiment


class PatternRepository:
    """Repository for Pattern operations."""

    def __init__(self, session: Session):
        """Initialize repository with database session."""
        self.session = session

    def create(self, pattern: Pattern) -> Pattern:
        """Create a new pattern."""
        self.session.add(pattern)
        self.session.flush()
        return pattern

    def get_active_patterns(self) -> list[Pattern]:
        """Get all active patterns."""
        return (
            self.session.query(Pattern)
            .filter(Pattern.status == "active")
            .order_by(desc(Pattern.confidence))
            .all()
        )

    def get_by_type(self, pattern_type: str) -> list[Pattern]:
        """Get patterns by type."""
        return (
            self.session.query(Pattern)
            .filter(Pattern.pattern_type == pattern_type, Pattern.status == "active")
            .order_by(desc(Pattern.confidence))
            .all()
        )

    def update(self, pattern: Pattern) -> Pattern:
        """Update an existing pattern."""
        self.session.flush()
        return pattern


class LearningHistoryRepository:
    """Repository for LearningHistory operations."""

    def __init__(self, session: Session):
        """Initialize repository with database session."""
        self.session = session

    def create(self, learning_event: LearningHistory) -> LearningHistory:
        """Create a new learning history entry."""
        self.session.add(learning_event)
        self.session.flush()
        return learning_event

    def get_recent_events(self, limit: int = 50) -> list[LearningHistory]:
        """Get recent learning events."""
        return (
            self.session.query(LearningHistory)
            .order_by(desc(LearningHistory.event_date))
            .limit(limit)
            .all()
        )

    def get_by_event_type(self, event_type: str) -> list[LearningHistory]:
        """Get learning events by type."""
        return (
            self.session.query(LearningHistory)
            .filter(LearningHistory.event_type == event_type)
            .order_by(desc(LearningHistory.event_date))
            .all()
        )


class PositionRepository:
    """Repository for Position operations."""

    def __init__(self, session: Session):
        """Initialize repository with database session."""
        self.session = session

    def create(self, position: Position) -> Position:
        """Create a new position."""
        self.session.add(position)
        self.session.flush()
        return position

    def get_by_id(self, position_id: str) -> Optional[Position]:
        """Get position by ID."""
        return (
            self.session.query(Position)
            .filter(Position.position_id == position_id)
            .first()
        )

    def get_all_open(self) -> list[Position]:
        """Get all open positions."""
        return self.session.query(Position).all()

    def update(self, position: Position) -> Position:
        """Update an existing position."""
        self.session.flush()
        return position

    def create_or_update(self, position: Position) -> Position:
        """Create or update a position (upsert by position_id).

        Args:
            position: Position to create or update

        Returns:
            The created or updated position
        """
        existing = self.get_by_id(position.position_id)
        if existing:
            existing.current_premium = position.current_premium
            existing.current_pnl = position.current_pnl
            existing.current_pnl_pct = position.current_pnl_pct
            existing.last_updated = position.last_updated
            existing.dte = position.dte
            existing.delta = position.delta
            existing.gamma = position.gamma
            existing.theta = position.theta
            existing.vega = position.vega
            existing.approaching_stop_loss = position.approaching_stop_loss
            existing.approaching_profit_target = position.approaching_profit_target
            existing.approaching_expiration = position.approaching_expiration
            self.session.flush()
            return existing
        else:
            return self.create(position)

    def delete(self, position: Position) -> None:
        """Delete a position (when closed)."""
        self.session.delete(position)
        self.session.flush()


class AuditLogRepository:
    """Repository for AuditLog operations."""

    def __init__(self, session: Session):
        """Initialize repository with database session."""
        self.session = session

    def create(self, audit_entry: AuditLog) -> AuditLog:
        """Create a new audit log entry."""
        self.session.add(audit_entry)
        self.session.flush()
        return audit_entry

    def get_recent(self, limit: int = 100) -> list[AuditLog]:
        """Get recent audit log entries."""
        return (
            self.session.query(AuditLog)
            .order_by(desc(AuditLog.timestamp))
            .limit(limit)
            .all()
        )

    def get_by_action(self, action: str) -> list[AuditLog]:
        """Get audit logs by action type."""
        return (
            self.session.query(AuditLog)
            .filter(AuditLog.action == action)
            .order_by(desc(AuditLog.timestamp))
            .all()
        )


class ScanRepository:
    """Repository for scan results and opportunities."""

    def __init__(self, session: Session):
        """Initialize repository with database session.

        Args:
            session: SQLAlchemy session
        """
        self.session = session

    def create_scan(self, scan_result: ScanResult) -> ScanResult:
        """Create a new scan result.

        Args:
            scan_result: ScanResult object to create

        Returns:
            Created ScanResult with ID assigned
        """
        self.session.add(scan_result)
        self.session.flush()
        return scan_result

    def add_opportunity(self, opportunity: ScanOpportunity) -> ScanOpportunity:
        """Add a new opportunity to a scan.

        Args:
            opportunity: ScanOpportunity object to create

        Returns:
            Created ScanOpportunity with ID assigned
        """
        self.session.add(opportunity)
        self.session.flush()
        return opportunity

    def get_scan_by_id(self, scan_id: int) -> Optional[ScanResult]:
        """Get scan by ID.

        Args:
            scan_id: Scan ID

        Returns:
            ScanResult or None if not found
        """
        return self.session.query(ScanResult).filter(ScanResult.id == scan_id).first()

    def get_recent_scans(
        self, days: int = 7, source: Optional[str] = None, limit: int = 50
    ) -> list[ScanResult]:
        """Get recent scans.

        Args:
            days: Number of days to look back
            source: Filter by source ('barchart', 'manual', 'ibkr_legacy'), None for all
            limit: Maximum number of results

        Returns:
            List of ScanResult objects
        """
        cutoff_date = datetime.now() - timedelta(days=days)
        query = (
            self.session.query(ScanResult)
            .filter(ScanResult.scan_timestamp >= cutoff_date)
            .order_by(desc(ScanResult.scan_timestamp))
        )

        if source:
            query = query.filter(ScanResult.source == source)

        return query.limit(limit).all()

    def get_opportunities_by_scan(self, scan_id: int) -> list[ScanOpportunity]:
        """Get all opportunities for a specific scan.

        Args:
            scan_id: Scan ID

        Returns:
            List of ScanOpportunity objects
        """
        return (
            self.session.query(ScanOpportunity)
            .filter(ScanOpportunity.scan_id == scan_id)
            .all()
        )

    def get_opportunities_by_symbol(
        self, symbol: str, days: int = 30
    ) -> list[ScanOpportunity]:
        """Get all opportunities for a specific symbol.

        Args:
            symbol: Stock symbol
            days: Number of days to look back

        Returns:
            List of ScanOpportunity objects
        """
        cutoff_date = datetime.now() - timedelta(days=days)
        return (
            self.session.query(ScanOpportunity)
            .join(ScanResult)
            .filter(ScanOpportunity.symbol == symbol)
            .filter(ScanResult.scan_timestamp >= cutoff_date)
            .order_by(desc(ScanResult.scan_timestamp))
            .all()
        )

    def get_pending_opportunities(
        self, validation_status: str = "pending"
    ) -> list[ScanOpportunity]:
        """Get opportunities that haven't been validated or executed yet.

        Args:
            validation_status: Status filter ('pending', 'barchart_only', etc.)

        Returns:
            List of unexecuted ScanOpportunity objects
        """
        return (
            self.session.query(ScanOpportunity)
            .filter(ScanOpportunity.executed == False)
            .filter(ScanOpportunity.validation_status == validation_status)
            .join(ScanResult)
            .order_by(desc(ScanResult.scan_timestamp))
            .all()
        )

    def get_opportunities_by_state(
        self, state: "OpportunityState"
    ) -> list[ScanOpportunity]:
        """Get opportunities by their current state.

        Args:
            state: OpportunityState enum value

        Returns:
            List of ScanOpportunity objects in the specified state
        """
        return (
            self.session.query(ScanOpportunity)
            .filter(ScanOpportunity.state == state.name)  # Use .name for string comparison
            .order_by(desc(ScanOpportunity.updated_at))
            .all()
        )

    def mark_opportunity_executed(
        self, opportunity_id: int, trade_id: str
    ) -> ScanOpportunity:
        """Mark an opportunity as executed and link to trade.

        Args:
            opportunity_id: Opportunity ID
            trade_id: Trade ID from trades table

        Returns:
            Updated ScanOpportunity
        """
        opportunity = (
            self.session.query(ScanOpportunity)
            .filter(ScanOpportunity.id == opportunity_id)
            .first()
        )
        if opportunity:
            opportunity.executed = True
            opportunity.trade_id = trade_id
            self.session.flush()
        return opportunity

    def update_validation_status(
        self,
        opportunity_id: int,
        validation_status: str,
        rejection_reason: Optional[str] = None,
    ) -> ScanOpportunity:
        """Update validation status of an opportunity.

        Args:
            opportunity_id: Opportunity ID
            validation_status: New status ('ibkr_validated', 'rejected', etc.)
            rejection_reason: Reason if rejected

        Returns:
            Updated ScanOpportunity
        """
        opportunity = (
            self.session.query(ScanOpportunity)
            .filter(ScanOpportunity.id == opportunity_id)
            .first()
        )
        if opportunity:
            opportunity.validation_status = validation_status
            if rejection_reason:
                opportunity.rejection_reason = rejection_reason
            self.session.flush()
        return opportunity

    def find_duplicate_opportunity(
        self, symbol: str, strike: float, expiration: datetime, scan_id: int
    ) -> Optional[ScanOpportunity]:
        """Find duplicate opportunity in the same scan.

        Args:
            symbol: Stock symbol
            strike: Strike price
            expiration: Expiration date
            scan_id: Current scan ID

        Returns:
            Existing ScanOpportunity or None
        """
        return (
            self.session.query(ScanOpportunity)
            .filter(ScanOpportunity.scan_id == scan_id)
            .filter(ScanOpportunity.symbol == symbol)
            .filter(ScanOpportunity.strike == strike)
            .filter(ScanOpportunity.expiration == expiration.date())
            .first()
        )

    def get_scan_statistics(self, days: int = 30) -> dict:
        """Get statistics about scans over time.

        Args:
            days: Number of days to analyze

        Returns:
            Dictionary with statistics
        """
        cutoff_date = datetime.now() - timedelta(days=days)

        total_scans = (
            self.session.query(func.count(ScanResult.id))
            .filter(ScanResult.scan_timestamp >= cutoff_date)
            .scalar()
        )

        manual_scans = (
            self.session.query(func.count(ScanResult.id))
            .filter(ScanResult.scan_timestamp >= cutoff_date)
            .filter(ScanResult.source == "manual")
            .scalar()
        )

        total_opportunities = (
            self.session.query(func.count(ScanOpportunity.id))
            .join(ScanResult)
            .filter(ScanResult.scan_timestamp >= cutoff_date)
            .scalar()
        )

        executed_opportunities = (
            self.session.query(func.count(ScanOpportunity.id))
            .join(ScanResult)
            .filter(ScanResult.scan_timestamp >= cutoff_date)
            .filter(ScanOpportunity.executed == True)
            .scalar()
        )

        return {
            "total_scans": total_scans or 0,
            "manual_scans": manual_scans or 0,
            "barchart_scans": (total_scans or 0) - (manual_scans or 0),
            "total_opportunities": total_opportunities or 0,
            "executed_opportunities": executed_opportunities or 0,
            "execution_rate": (
                (executed_opportunities / total_opportunities * 100)
                if total_opportunities
                else 0
            ),
        }

    # === Phase 2.5A: Idempotency & Duplicate Detection ===

    def calculate_opportunity_hash(
        self,
        symbol: str,
        strike: float,
        expiration: datetime,
        option_type: str,
        date_created: Optional[datetime] = None,
    ) -> str:
        """Generate unique hash for opportunity deduplication.

        Hash includes date_created (not just date) to allow re-entry of
        same option on different days. For example, the same AAPL put
        can be entered manually on Monday and found by Barchart on Tuesday.

        Args:
            symbol: Stock symbol
            strike: Strike price
            expiration: Expiration date
            option_type: Option type (PUT/CALL)
            date_created: Date when opportunity was created (defaults to today)

        Returns:
            SHA256 hash (truncated to 16 characters)
        """
        import hashlib

        if date_created is None:
            date_created = datetime.now()

        # Format date as YYYY-MM-DD to ignore time component
        date_str = date_created.strftime("%Y-%m-%d")
        exp_str = expiration.strftime("%Y-%m-%d")

        # Create unique key
        key = (
            f"{symbol.upper()}:{strike:.2f}:{exp_str}:{option_type.upper()}:{date_str}"
        )

        # Generate hash
        hash_obj = hashlib.sha256(key.encode())
        return hash_obj.hexdigest()[:16]

    def find_duplicate(
        self,
        symbol: str,
        strike: float,
        expiration: datetime | date,
        option_type: str = "PUT",
        days_lookback: int = 7,
    ) -> Optional[ScanOpportunity]:
        """Check if opportunity already exists (any state, any scan).

        This is different from find_duplicate_opportunity which only checks
        within the same scan. This method checks across all recent scans
        to prevent creating duplicate opportunities.

        Args:
            symbol: Stock symbol
            strike: Strike price
            expiration: Expiration date (datetime or date object)
            option_type: Option type (PUT/CALL)
            days_lookback: How many days to look back (default 7)

        Returns:
            Existing ScanOpportunity or None if not found
        """
        cutoff_date = datetime.now() - timedelta(days=days_lookback)

        # Calculate hash for this opportunity
        opp_hash = self.calculate_opportunity_hash(
            symbol=symbol,
            strike=strike,
            expiration=expiration,
            option_type=option_type,
            date_created=datetime.now(),
        )

        # First try to find by hash (fastest)
        opportunity = (
            self.session.query(ScanOpportunity)
            .filter(ScanOpportunity.opportunity_hash == opp_hash)
            .first()
        )

        if opportunity:
            return opportunity

        # Fallback: search by fields for opportunities without hash
        # (legacy data or hash not yet set)

        # Handle both datetime and date objects
        from datetime import date as date_type
        if isinstance(expiration, date_type) and not isinstance(expiration, datetime):
            exp_date = expiration
        else:
            exp_date = expiration.date() if hasattr(expiration, 'date') else expiration

        return (
            self.session.query(ScanOpportunity)
            .join(ScanResult)
            .filter(ScanOpportunity.symbol == symbol)
            .filter(ScanOpportunity.strike == strike)
            .filter(ScanOpportunity.expiration == exp_date)
            .filter(ScanOpportunity.option_type == option_type)
            .filter(ScanResult.scan_timestamp >= cutoff_date)
            .filter(
                ScanOpportunity.state.notin_(
                    ["EXECUTED", "FAILED", "EXPIRED", "REJECTED"]
                )
            )
            .order_by(desc(ScanResult.scan_timestamp))
            .first()
        )

    def merge_duplicate(
        self,
        existing_id: int,
        new_data: dict,
        source: str,
    ) -> ScanOpportunity:
        """Merge new data into existing opportunity.

        Use case: Manual trade also found by Barchart scan.
        Keep manual notes, update with fresher pricing.

        Args:
            existing_id: ID of existing opportunity
            new_data: Dictionary with new data to merge
            source: Source of new data ('manual', 'barchart', etc.)

        Returns:
            Updated ScanOpportunity

        Raises:
            ValueError: If opportunity not found
        """
        opportunity = (
            self.session.query(ScanOpportunity)
            .filter(ScanOpportunity.id == existing_id)
            .first()
        )

        if not opportunity:
            raise ValueError(f"Opportunity {existing_id} not found")

        # Track sources that contributed to this opportunity
        if opportunity.entry_notes:
            if source not in opportunity.entry_notes:
                opportunity.entry_notes += f"\n[Merged from {source}]"
        else:
            opportunity.entry_notes = f"[Merged from {source}]"

        # Update pricing if newer data available
        if "premium" in new_data and new_data["premium"]:
            opportunity.premium = new_data["premium"]
        if "bid" in new_data and new_data["bid"]:
            opportunity.bid = new_data["bid"]
        if "ask" in new_data and new_data["ask"]:
            opportunity.ask = new_data["ask"]
        if "spread_pct" in new_data:
            opportunity.spread_pct = new_data["spread_pct"]

        # Update Greeks if available
        for greek in ["delta", "gamma", "theta", "vega", "iv"]:
            if greek in new_data and new_data[greek] is not None:
                setattr(opportunity, greek, new_data[greek])

        # Update margin data if available
        if "margin_required" in new_data and new_data["margin_required"]:
            opportunity.margin_required = new_data["margin_required"]
        if "margin_efficiency" in new_data and new_data["margin_efficiency"]:
            opportunity.margin_efficiency = new_data["margin_efficiency"]

        # Update liquidity data if available
        if "volume" in new_data and new_data["volume"]:
            opportunity.volume = new_data["volume"]
        if "open_interest" in new_data and new_data["open_interest"]:
            opportunity.open_interest = new_data["open_interest"]

        # Update trend if available and not already set
        if "trend" in new_data and new_data["trend"] and not opportunity.trend:
            opportunity.trend = new_data["trend"]

        # Update timestamp
        if opportunity.updated_at:
            opportunity.updated_at = utc_now()

        self.session.flush()

        return opportunity

    def get_opportunity_by_hash(
        self, opportunity_hash: str
    ) -> Optional[ScanOpportunity]:
        """Get opportunity by its hash.

        Args:
            opportunity_hash: The opportunity hash

        Returns:
            ScanOpportunity or None if not found
        """
        return (
            self.session.query(ScanOpportunity)
            .filter(ScanOpportunity.opportunity_hash == opportunity_hash)
            .first()
        )

    # === End Phase 2.5A ===


class StockPositionRepository:
    """Repository for StockPosition operations (stock from option assignments)."""

    def __init__(self, session: Session):
        """Initialize repository with database session.

        Args:
            session: SQLAlchemy session
        """
        self.session = session

    def create(self, stock_position: StockPosition) -> StockPosition:
        """Create a new stock position record.

        Args:
            stock_position: StockPosition to create

        Returns:
            Created StockPosition with ID assigned
        """
        self.session.add(stock_position)
        self.session.flush()
        return stock_position

    def get_by_origin_trade(self, trade_id: str) -> Optional[StockPosition]:
        """Get stock position by the origin trade ID.

        Args:
            trade_id: Trade ID of the original option trade

        Returns:
            StockPosition if found, None otherwise
        """
        return (
            self.session.query(StockPosition)
            .filter(StockPosition.origin_trade_id == trade_id)
            .first()
        )

    def get_open_positions(self) -> list[StockPosition]:
        """Get all open (held) stock positions.

        Returns:
            List of StockPosition where closed_date is NULL
        """
        return (
            self.session.query(StockPosition)
            .filter(StockPosition.closed_date.is_(None))
            .order_by(desc(StockPosition.assigned_date))
            .all()
        )

    def get_all(self, limit: Optional[int] = None) -> list[StockPosition]:
        """Get all stock positions, optionally limited.

        Args:
            limit: Maximum number of positions to return

        Returns:
            List of StockPosition ordered by assigned date descending
        """
        query = self.session.query(StockPosition).order_by(
            desc(StockPosition.assigned_date)
        )
        if limit:
            query = query.limit(limit)
        return query.all()

    def close(
        self,
        stock_position: StockPosition,
        sale_price: float,
        close_reason: str = "sold",
    ) -> StockPosition:
        """Close a stock position with sale price and compute P&L.

        Args:
            stock_position: StockPosition to close
            sale_price: Sale price per share
            close_reason: Reason for closing (sold, partial_sold, etc.)

        Returns:
            Updated StockPosition with P&L calculated
        """
        from src.utils.timezone import us_eastern_now

        stock_position.closed_date = us_eastern_now()
        stock_position.sale_price_per_share = sale_price
        stock_position.close_reason = close_reason
        stock_position.stock_pnl = (
            (sale_price - stock_position.cost_basis_per_share) * stock_position.shares
        )
        stock_position.total_pnl = (
            (stock_position.option_pnl or 0.0) + stock_position.stock_pnl
        )
        self.session.flush()
        return stock_position

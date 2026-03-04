"""SQLAlchemy database models for the trading system.

This module defines all database tables and their relationships using
SQLAlchemy ORM. Models match the database schema defined in the specification.
"""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

Base = declarative_base()


class TWSStatus:
    """TWS order status state machine: None -> SUBMITTED -> FILLED/CANCELLED -> None"""
    SUBMITTED = "Submitted"
    FILLED = "Filled"
    CANCELLED = "Cancelled"


class Trade(Base):
    """Complete trade lifecycle tracking.

    Records all information about a trade from entry to exit, including
    strategy parameters, market context, and learning experiment tracking.
    """

    __tablename__ = "trades"

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(String(50), unique=True, nullable=False, index=True)

    # Trade details
    symbol = Column(String(10), nullable=False, index=True)
    strike = Column(Float, nullable=False)
    expiration = Column(Date, nullable=False)
    option_type = Column(String(10), default="PUT")

    # Entry details
    entry_date = Column(DateTime, nullable=False, index=True)
    entry_premium = Column(Float, nullable=False)
    contracts = Column(Integer, nullable=False)

    # Exit details
    exit_date = Column(DateTime, nullable=True, index=True)
    exit_premium = Column(Float, nullable=True)
    exit_reason = Column(String(50), nullable=True)

    # Profit/Loss
    profit_loss = Column(Float, nullable=True)
    profit_pct = Column(Float, nullable=True)
    roi = Column(Float, nullable=True)
    days_held = Column(Integer, nullable=True)

    # Strategy parameters at entry
    otm_pct = Column(Float, nullable=True)
    dte = Column(Integer, nullable=False)
    config_version = Column(Integer, nullable=True)

    # Market context
    vix_at_entry = Column(Float, nullable=True)
    vix_at_exit = Column(Float, nullable=True)
    spy_price_at_entry = Column(Float, nullable=True)
    spy_price_at_exit = Column(Float, nullable=True)
    market_regime = Column(String(20), nullable=True)
    sector = Column(String(50), nullable=True, index=True)

    # Experiment tracking
    is_experiment = Column(Boolean, default=False, index=True)
    experiment_id = Column(Integer, ForeignKey("experiments.id"), nullable=True)

    # AI context
    ai_confidence = Column(Float, nullable=True)
    ai_reasoning = Column(Text, nullable=True)

    # Phase C: Order Reconciliation
    order_id = Column(Integer, nullable=True, index=True)  # IBKR order ID
    reconciled_at = Column(DateTime, nullable=True)
    tws_status = Column(String(50), nullable=True)
    commission = Column(Float, nullable=True)
    fill_time = Column(DateTime, nullable=True)
    fill_price_discrepancy = Column(Float, nullable=True)

    # NakedTrader extension columns
    trade_strategy = Column(String(20), nullable=True, index=True)  # nakedtrader, weekly, etc.
    exit_order_id = Column(Integer, nullable=True)  # IBKR profit-take order ID
    stop_order_id = Column(Integer, nullable=True)  # IBKR stop-loss order ID
    bracket_status = Column(String(20), nullable=True)  # active, profit_taken, stopped, expired

    # TAAD extension columns
    trade_source = Column(String(20), nullable=True, index=True)  # real, paper, backtest, ibkr_import
    account_id = Column(String(20), nullable=True, index=True)  # IBKR account ID
    assignment_status = Column(String(20), nullable=True)  # none, partial, full
    ibkr_execution_id = Column(String(50), nullable=True)  # IBKR execution ID for dedup
    enrichment_status = Column(String(20), nullable=True)  # pending, partial, complete
    enrichment_quality = Column(Float, nullable=True)  # 0.0-1.0 data quality score
    currency = Column(String(10), nullable=True, default="USD")
    multiplier = Column(Integer, nullable=True, default=100)

    # Stock position tracking (assignment lifecycle)
    # Trade lifecycle: open → assigned → stock_held → fully_closed
    lifecycle_status = Column(String(20), nullable=True)  # null=normal, stock_held, fully_closed
    option_pnl = Column(Float, nullable=True)   # = profit_loss (copied at assignment)
    stock_pnl = Column(Float, nullable=True)    # filled when stock is sold
    total_pnl = Column(Float, nullable=True)    # option_pnl + stock_pnl

    # Metadata
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    experiment = relationship("Experiment", back_populates="trades")
    entry_snapshots = relationship(
        "TradeEntrySnapshot", back_populates="trade", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        """String representation of Trade."""
        return f"<Trade(id={self.trade_id}, symbol={self.symbol}, strike={self.strike}, entry={self.entry_date})>"

    def is_closed(self) -> bool:
        """Check if trade is closed."""
        return self.exit_date is not None

    def is_profitable(self) -> bool:
        """Check if trade is profitable."""
        return self.profit_loss is not None and self.profit_loss > 0


class Experiment(Base):
    """A/B test tracking for strategy improvements.

    Tracks experiments comparing control (baseline) vs test (variant) values
    for strategy parameters to validate improvements statistically.
    """

    __tablename__ = "experiments"

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)
    experiment_id = Column(String(50), unique=True, nullable=False, index=True)

    # Hypothesis
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    parameter_name = Column(String(100), nullable=False, index=True)
    control_value = Column(String(100), nullable=False)
    test_value = Column(String(100), nullable=False)

    # Status
    status = Column(
        String(20), default="active", index=True
    )  # active, completed, adopted, rejected
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=True)

    # Results
    control_trades = Column(Integer, default=0)
    test_trades = Column(Integer, default=0)
    control_win_rate = Column(Float, nullable=True)
    test_win_rate = Column(Float, nullable=True)
    control_avg_roi = Column(Float, nullable=True)
    test_avg_roi = Column(Float, nullable=True)
    p_value = Column(Float, nullable=True)
    effect_size = Column(Float, nullable=True)
    decision = Column(Text, nullable=True)

    # Metadata
    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    trades = relationship("Trade", back_populates="experiment")

    def __repr__(self) -> str:
        """String representation of Experiment."""
        return f"<Experiment(id={self.experiment_id}, name={self.name}, status={self.status})>"

    def is_complete(self) -> bool:
        """Check if experiment has enough data for analysis."""
        min_samples = 30
        return (
            self.control_trades >= min_samples and self.test_trades >= min_samples
        )


class LearningHistory(Base):
    """Learning events and parameter changes over time.

    Tracks what the AI learned, when, and what changes were made based
    on those learnings.
    """

    __tablename__ = "learning_history"

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Learning event
    event_type = Column(
        String(50), nullable=False, index=True
    )  # pattern_detected, parameter_adjusted, etc.
    event_date = Column(DateTime, nullable=False, index=True)

    # Details
    pattern_name = Column(String(200), nullable=True)
    confidence = Column(Float, nullable=True)
    sample_size = Column(Integer, nullable=True)

    # Change made
    parameter_changed = Column(String(100), nullable=True)
    old_value = Column(String(100), nullable=True)
    new_value = Column(String(100), nullable=True)

    # Justification
    reasoning = Column(Text, nullable=True)
    expected_improvement = Column(Float, nullable=True)

    # Metadata
    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        """String representation of LearningHistory."""
        return f"<LearningHistory(event_type={self.event_type}, date={self.event_date})>"


class Pattern(Base):
    """Detected patterns with statistical confidence.

    Represents profitable patterns discovered through analysis of
    trade history across multiple dimensions.
    """

    __tablename__ = "patterns"

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Pattern identification
    pattern_type = Column(
        String(50), nullable=False, index=True
    )  # sector, otm_range, dte, timing, etc.
    pattern_name = Column(String(200), nullable=False)
    pattern_value = Column(String(100), nullable=True)  # The actual pattern value

    # Statistics
    sample_size = Column(Integer, nullable=False)
    win_rate = Column(Float, nullable=False)
    avg_roi = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    p_value = Column(Float, nullable=False)

    # Context
    market_regime = Column(String(20), nullable=True)
    date_detected = Column(DateTime, nullable=False)
    date_last_validated = Column(DateTime, nullable=True)

    # Status
    status = Column(
        String(20), default="active", index=True
    )  # active, invalidated, superseded

    # Metadata
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        """String representation of Pattern."""
        return f"<Pattern(type={self.pattern_type}, name={self.pattern_name}, confidence={self.confidence})>"

    def is_valid(self) -> bool:
        """Check if pattern is still valid."""
        return self.status == "active" and self.confidence >= 0.95


class Position(Base):
    """Current open positions.

    Tracks real-time information about open positions for monitoring
    and risk management.
    """

    __tablename__ = "positions"

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)
    position_id = Column(String(50), unique=True, nullable=False, index=True)

    # Links to trade
    trade_id = Column(String(50), nullable=False, index=True)

    # Position details
    symbol = Column(String(10), nullable=False)
    strike = Column(Float, nullable=False)
    expiration = Column(Date, nullable=False)
    option_type = Column(String(10), default="PUT")
    contracts = Column(Integer, nullable=False)

    # Entry info
    entry_date = Column(DateTime, nullable=False)
    entry_premium = Column(Float, nullable=False)
    dte = Column(Integer, nullable=False)

    # Current status
    current_premium = Column(Float, nullable=True)
    current_pnl = Column(Float, nullable=True)
    current_pnl_pct = Column(Float, nullable=True)
    last_updated = Column(DateTime, nullable=True)

    # Greeks (optional)
    delta = Column(Float, nullable=True)
    gamma = Column(Float, nullable=True)
    theta = Column(Float, nullable=True)
    vega = Column(Float, nullable=True)

    # Risk flags
    approaching_stop_loss = Column(Boolean, default=False)
    approaching_profit_target = Column(Boolean, default=False)
    approaching_expiration = Column(Boolean, default=False)

    # Metadata
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        """String representation of Position."""
        return f"<Position(id={self.position_id}, symbol={self.symbol}, strike={self.strike}, pnl={self.current_pnl_pct})>"


class AuditLog(Base):
    """Audit trail for all system actions.

    Records every significant action taken by the system for
    accountability and debugging.
    """

    __tablename__ = "audit_log"

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Event details
    timestamp = Column(DateTime, nullable=False, index=True)
    action = Column(String(100), nullable=False, index=True)
    actor = Column(String(100), nullable=False)  # system component that acted
    context = Column(Text, nullable=True)  # JSON context
    decision = Column(Text, nullable=True)  # AI reasoning if applicable
    outcome = Column(String(50), nullable=True)  # success, failure, pending

    # Metadata
    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        """String representation of AuditLog."""
        return f"<AuditLog(action={self.action}, actor={self.actor}, time={self.timestamp})>"


class ScanResult(Base):
    """Scan execution metadata and configuration.

    Stores information about each scan execution, whether from Barchart API,
    manual entry, or legacy IBKR scanner. Links to individual opportunities
    found in that scan.
    """

    __tablename__ = "scan_results"

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Scan identification
    scan_timestamp = Column(DateTime, nullable=False, index=True)
    source = Column(
        String(20), nullable=False, index=True
    )  # 'barchart', 'manual', 'ibkr_legacy'

    # Configuration
    config_used = Column(JSON, nullable=True)  # Parameters used for this scan

    # Statistics
    total_candidates = Column(Integer, default=0)
    validated_count = Column(Integer, default=0)
    execution_time_seconds = Column(Float, nullable=True)

    # Notes
    notes = Column(Text, nullable=True)  # User notes for manual scans

    # Metadata
    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    opportunities = relationship(
        "ScanOpportunity", back_populates="scan", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        """String representation of ScanResult."""
        return f"<ScanResult(id={self.id}, source={self.source}, time={self.scan_timestamp}, candidates={self.total_candidates})>"


class ScanOpportunity(Base):
    """Individual option opportunity from a scan.

    Represents a single option contract identified as a potential trade
    by Barchart, manual entry, or IBKR scanner. Tracks all relevant
    data including pricing, Greeks, and validation status.
    """

    __tablename__ = "scan_opportunities"

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)
    scan_id = Column(Integer, ForeignKey("scan_results.id"), nullable=False, index=True)

    # Option details
    symbol = Column(String(10), nullable=False, index=True)
    strike = Column(Float, nullable=False)
    expiration = Column(Date, nullable=False, index=True)
    option_type = Column(String(10), default="PUT")

    # Pricing
    premium = Column(Float, nullable=True)
    bid = Column(Float, nullable=True)
    ask = Column(Float, nullable=True)
    spread_pct = Column(Float, nullable=True)

    # Greeks & metrics
    delta = Column(Float, nullable=True)
    gamma = Column(Float, nullable=True)
    theta = Column(Float, nullable=True)
    vega = Column(Float, nullable=True)
    iv = Column(Float, nullable=True)  # Implied volatility

    # Position details
    otm_pct = Column(Float, nullable=True)
    dte = Column(Integer, nullable=True)
    stock_price = Column(Float, nullable=True)

    # Margin & efficiency
    margin_required = Column(Float, nullable=True)
    margin_efficiency = Column(Float, nullable=True)

    # Liquidity
    volume = Column(Integer, nullable=True)
    open_interest = Column(Integer, nullable=True)

    # Trend & validation
    trend = Column(String(20), nullable=True)  # uptrend, downtrend, sideways, unknown
    validation_status = Column(
        String(20), default="pending", index=True
    )  # pending, barchart_only, ibkr_validated, rejected
    rejection_reason = Column(Text, nullable=True)

    # Source tracking
    source = Column(
        String(20), nullable=False, index=True
    )  # 'barchart', 'manual', 'ibkr_legacy'
    entry_notes = Column(Text, nullable=True)  # User reasoning for manual entries

    # Execution tracking
    executed = Column(Boolean, default=False, index=True)
    trade_id = Column(String(50), nullable=True, index=True)  # Links to trades table

    # Metadata
    created_at = Column(DateTime, server_default=func.now())

    # === Phase 2.5A: Opportunity Lifecycle Tracking ===
    # State machine tracking
    state = Column(String(20), nullable=True, default="PENDING", index=True)
    state_history = Column(JSON, nullable=True, default=lambda: [])

    # Timestamps
    updated_at = Column(DateTime, nullable=True, default=func.now(), onupdate=func.now())
    expires_at = Column(DateTime, nullable=True)

    # Snapshot data at different stages
    enrichment_snapshot = Column(JSON, nullable=True)
    validation_snapshot = Column(JSON, nullable=True)
    execution_snapshot = Column(JSON, nullable=True)

    # Rejection tracking (critical for learning)
    rejection_reasons = Column(JSON, nullable=True, default=lambda: [])
    risk_check_results = Column(JSON, nullable=True)

    # User decision tracking
    user_decision = Column(String(20), nullable=True)  # approved/rejected/skipped
    user_decision_at = Column(DateTime, nullable=True)
    user_notes = Column(Text, nullable=True)

    # Execution tracking
    execution_attempts = Column(Integer, nullable=True, default=0)
    last_error = Column(Text, nullable=True)

    # Idempotency key (prevent duplicates)
    opportunity_hash = Column(String(64), nullable=True, index=True)

    # AI recommendation from scanner (Phase 7: IBKR Scanner Dashboard)
    # Stores: {"score": 7, "recommendation": "buy", "reasoning": "...", "risk_flags": [...]}
    ai_recommendation = Column(JSON, nullable=True)
    # === End Phase 2.5A ===

    # === Phase 4.1: Staging and Validation Columns ===
    # Staging fields
    staged_at = Column(DateTime, nullable=True, index=True)
    staged_contracts = Column(Integer, nullable=True)
    staged_limit_price = Column(Float, nullable=True)
    staged_margin = Column(Float, nullable=True)
    staged_margin_source = Column(String(20), nullable=True)  # 'ibkr_whatif' or 'estimated'
    portfolio_rank = Column(Integer, nullable=True, index=True)

    # Pre-market validation fields (Stage 1 - 9:15 AM)
    premarket_stock_price = Column(Float, nullable=True)
    premarket_deviation_pct = Column(Float, nullable=True)
    premarket_checked_at = Column(DateTime, nullable=True)
    premarket_new_bid = Column(Float, nullable=True)
    premarket_new_ask = Column(Float, nullable=True)
    adjusted_strike = Column(Float, nullable=True)
    adjusted_limit_price = Column(Float, nullable=True)
    adjustment_reason = Column(Text, nullable=True)

    # Market-open validation fields (Stage 2 - 9:30 AM)
    open_stock_price = Column(Float, nullable=True)
    open_deviation_pct = Column(Float, nullable=True)
    open_checked_at = Column(DateTime, nullable=True)
    open_bid = Column(Float, nullable=True)
    open_ask = Column(Float, nullable=True)
    open_limit_price = Column(Float, nullable=True)

    # Execution scheduling
    execution_session = Column(String(50), nullable=True, index=True)  # e.g., 'week_of_2026-02-02'
    execution_priority = Column(Integer, nullable=True)  # 1 = execute first
    # === End Phase 4.1 ===

    # Relationships
    scan = relationship("ScanResult", back_populates="opportunities")
    entry_snapshots = relationship("TradeEntrySnapshot", back_populates="opportunity")

    def __repr__(self) -> str:
        """String representation of ScanOpportunity."""
        return f"<ScanOpportunity(symbol={self.symbol}, strike={self.strike}, exp={self.expiration}, source={self.source})>"

    def is_validated(self) -> bool:
        """Check if opportunity has been validated with IBKR."""
        return self.validation_status == "ibkr_validated"

    def is_executed(self) -> bool:
        """Check if opportunity has been executed as a trade."""
        return self.executed and self.trade_id is not None

    def is_staged(self) -> bool:
        """Check if opportunity has been staged for Monday execution."""
        return self.state == "STAGED" and self.staged_at is not None

    def is_ready_for_execution(self) -> bool:
        """Check if opportunity is ready for execution (passed all validations)."""
        return self.state in ("READY", "CONFIRMED")

    def get_effective_strike(self) -> float:
        """Get the effective strike price (adjusted if applicable)."""
        return self.adjusted_strike if self.adjusted_strike else self.strike

    def get_effective_limit_price(self) -> float | None:
        """Get the effective limit price (adjusted if applicable)."""
        if self.open_limit_price:
            return self.open_limit_price
        if self.adjusted_limit_price:
            return self.adjusted_limit_price
        return self.staged_limit_price


class TradeEntrySnapshot(Base):
    """Snapshot of all critical data at trade entry for learning engine.

    Phase 2.6A - Critical Fields Data Collection
    Captures 66 fields across 9 categories at the moment a trade is entered,
    including the 8 critical fields with ~80% predictive power:
    1. delta (IBKR Greeks)
    2. iv (IBKR Greeks)
    3. iv_rank (calculated)
    4. vix (IBKR market)
    5. dte (calculated)
    6. trend_direction (calculated)
    7. days_to_earnings (external API)
    8. margin_efficiency_pct (calculated from actual IBKR margin)
    """

    __tablename__ = "trade_entry_snapshots"

    # Primary key and foreign keys
    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(Integer, ForeignKey("trades.id", ondelete="CASCADE"), nullable=False, index=True)
    opportunity_id = Column(Integer, ForeignKey("scan_opportunities.id", ondelete="SET NULL"), nullable=True, index=True)

    # ============================================================
    # CATEGORY 1: Option Contract Data (13 fields)
    # ============================================================
    symbol = Column(String(10), nullable=False)
    strike = Column(Float, nullable=False)
    expiration = Column(Date, nullable=False)
    option_type = Column(String(10), nullable=False)  # PUT/CALL

    # Pricing
    bid = Column(Float, nullable=True)
    ask = Column(Float, nullable=True)
    mid = Column(Float, nullable=True)  # (bid + ask) / 2
    entry_premium = Column(Float, nullable=False)  # Actual fill price
    spread_pct = Column(Float, nullable=True)  # (ask - bid) / mid

    # Greeks from IBKR (5 Greeks)
    delta = Column(Float, nullable=True)  # *** CRITICAL FIELD #1 ***
    gamma = Column(Float, nullable=True)
    theta = Column(Float, nullable=True)
    vega = Column(Float, nullable=True)
    rho = Column(Float, nullable=True)

    # ============================================================
    # CATEGORY 2: Volatility Data (5 fields)
    # ============================================================
    iv = Column(Float, nullable=True)  # *** CRITICAL FIELD #2 ***
    iv_rank = Column(Float, nullable=True)  # *** CRITICAL FIELD #3 ***
    iv_percentile = Column(Float, nullable=True)
    hv_20 = Column(Float, nullable=True)  # 20-day historical volatility
    iv_hv_ratio = Column(Float, nullable=True)  # iv / hv_20

    # ============================================================
    # CATEGORY 3: Liquidity (3 fields)
    # ============================================================
    option_volume = Column(Integer, nullable=True)
    open_interest = Column(Integer, nullable=True)
    volume_oi_ratio = Column(Float, nullable=True)  # volume / open_interest

    # ============================================================
    # CATEGORY 4: Underlying - Prices (6 fields)
    # ============================================================
    stock_price = Column(Float, nullable=False)
    stock_open = Column(Float, nullable=True)
    stock_high = Column(Float, nullable=True)
    stock_low = Column(Float, nullable=True)
    stock_prev_close = Column(Float, nullable=True)
    stock_change_pct = Column(Float, nullable=True)  # (price - prev_close) / prev_close

    # ============================================================
    # CATEGORY 5: Underlying - Calculated Metrics (6 fields)
    # ============================================================
    otm_pct = Column(Float, nullable=True)  # (stock_price - strike) / stock_price
    otm_dollars = Column(Float, nullable=True)  # stock_price - strike
    dte = Column(Integer, nullable=False)  # *** CRITICAL FIELD #5 ***
    margin_requirement = Column(Float, nullable=True)  # ACTUAL from IBKR whatIfOrder
    margin_efficiency_pct = Column(Float, nullable=True)  # *** CRITICAL FIELD #8 ***
    contracts = Column(Integer, nullable=False)  # Number of contracts

    # ============================================================
    # CATEGORY 6: Underlying - Trend (6 fields)
    # ============================================================
    sma_20 = Column(Float, nullable=True)  # 20-day simple moving average
    sma_50 = Column(Float, nullable=True)  # 50-day simple moving average
    trend_direction = Column(String(20), nullable=True)  # *** CRITICAL FIELD #6 ***
    trend_strength = Column(Float, nullable=True)  # Confidence score 0-1
    price_vs_sma20_pct = Column(Float, nullable=True)  # (price - sma_20) / sma_20
    price_vs_sma50_pct = Column(Float, nullable=True)  # (price - sma_50) / sma_50

    # ============================================================
    # CATEGORY 7: Market Data (4 fields)
    # ============================================================
    spy_price = Column(Float, nullable=True)
    spy_change_pct = Column(Float, nullable=True)
    vix = Column(Float, nullable=True)  # *** CRITICAL FIELD #4 ***
    vix_change_pct = Column(Float, nullable=True)

    # ============================================================
    # CATEGORY 7B: Technical Indicators (18 fields) - Phase 2.6B
    # ============================================================
    # RSI indicators (2 fields)
    rsi_14 = Column(Float, nullable=True)  # 14-period RSI
    rsi_7 = Column(Float, nullable=True)  # 7-period RSI

    # MACD indicators (3 fields)
    macd = Column(Float, nullable=True)  # MACD line
    macd_signal = Column(Float, nullable=True)  # Signal line
    macd_histogram = Column(Float, nullable=True)  # Histogram (MACD - Signal)

    # ADX indicators (3 fields)
    adx = Column(Float, nullable=True)  # Average Directional Index
    plus_di = Column(Float, nullable=True)  # Positive Directional Indicator
    minus_di = Column(Float, nullable=True)  # Negative Directional Indicator

    # ATR indicators (2 fields)
    atr_14 = Column(Float, nullable=True)  # Average True Range (14 period)
    atr_pct = Column(Float, nullable=True)  # ATR as % of stock price

    # Bollinger Bands (3 fields)
    bb_upper = Column(Float, nullable=True)  # Upper Bollinger Band
    bb_lower = Column(Float, nullable=True)  # Lower Bollinger Band
    bb_position = Column(Float, nullable=True)  # Position within bands (0-1)

    # Support/Resistance (5 fields)
    support_1 = Column(Float, nullable=True)  # First support level
    support_2 = Column(Float, nullable=True)  # Second support level
    resistance_1 = Column(Float, nullable=True)  # First resistance level
    resistance_2 = Column(Float, nullable=True)  # Second resistance level
    distance_to_support_pct = Column(Float, nullable=True)  # Distance to S1 as %

    # ============================================================
    # CATEGORY 8: Event Data (3 fields)
    # ============================================================
    earnings_date = Column(Date, nullable=True)
    days_to_earnings = Column(Integer, nullable=True)  # *** CRITICAL FIELD #7 ***
    earnings_in_dte = Column(Boolean, nullable=True)  # True if earnings before expiration

    # ============================================================
    # CATEGORY 8B: Market Context (14 fields) - Phase 2.6C
    # ============================================================
    # Additional indices (4 fields)
    qqq_price = Column(Float, nullable=True)  # Nasdaq 100 ETF price
    qqq_change_pct = Column(Float, nullable=True)  # QQQ daily change %
    iwm_price = Column(Float, nullable=True)  # Russell 2000 ETF price
    iwm_change_pct = Column(Float, nullable=True)  # IWM daily change %

    # Sector data (4 fields)
    sector = Column(String(50), nullable=True)  # Stock's sector
    sector_etf = Column(String(10), nullable=True)  # Corresponding sector ETF symbol
    sector_change_1d = Column(Float, nullable=True)  # Sector ETF 1-day change %
    sector_change_5d = Column(Float, nullable=True)  # Sector ETF 5-day change %

    # Regime classification (2 fields)
    vol_regime = Column(String(20), nullable=True)  # low, normal, elevated, extreme
    market_regime = Column(String(20), nullable=True)  # bullish, bearish, neutral, volatile

    # Calendar data (3 fields)
    day_of_week = Column(Integer, nullable=True)  # 0=Monday, 6=Sunday
    is_opex_week = Column(Boolean, nullable=True)  # True if OpEx week (3rd Friday)
    days_to_fomc = Column(Integer, nullable=True)  # Days to next FOMC meeting

    # Enhanced earnings (1 field)
    earnings_timing = Column(String(10), nullable=True)  # "BMO" or "AMC"

    # ============================================================
    # CATEGORY 8C: Strike Selection (3 fields) - Adaptive strike
    # ============================================================
    strike_selection_method = Column(String(20), nullable=True)  # "delta", "otm_pct", "unchanged"
    original_strike = Column(Float, nullable=True)  # Strike from overnight screening
    live_delta_at_selection = Column(Float, nullable=True)  # Delta at time of strike selection

    # ============================================================
    # CATEGORY 9: Metadata (4 fields)
    # ============================================================
    captured_at = Column(DateTime, nullable=False)
    data_quality_score = Column(Float, nullable=True)  # 0.0-1.0, based on field completeness
    source = Column(String(50), nullable=True)  # 'manual', 'scan', 'auto'
    notes = Column(Text, nullable=True)

    # Relationships
    trade = relationship("Trade", back_populates="entry_snapshots")
    opportunity = relationship("ScanOpportunity")

    def __repr__(self) -> str:
        """String representation of TradeEntrySnapshot."""
        return (
            f"<TradeEntrySnapshot(id={self.id}, trade_id={self.trade_id}, "
            f"symbol={self.symbol}, strike={self.strike}, captured_at={self.captured_at})>"
        )

    def calculate_data_quality_score(self) -> float:
        """Calculate data quality score based on field completeness.

        Scores field completeness across all categories with emphasis on
        critical fields. Returns a score from 0.0 (no data) to 1.0 (all fields).

        Returns:
            float: Data quality score between 0.0 and 1.0
        """
        # Define field groups with weights
        critical_fields = [
            self.delta,          # CRITICAL #1
            self.iv,             # CRITICAL #2
            self.iv_rank,        # CRITICAL #3
            self.vix,            # CRITICAL #4
            self.dte,            # CRITICAL #5 (always present, non-nullable)
            self.trend_direction,  # CRITICAL #6
            self.days_to_earnings,  # CRITICAL #7
            self.margin_efficiency_pct,  # CRITICAL #8
        ]

        greeks_fields = [self.delta, self.gamma, self.theta, self.vega, self.rho]
        volatility_fields = [self.iv, self.iv_rank, self.iv_percentile, self.hv_20, self.iv_hv_ratio]
        liquidity_fields = [self.option_volume, self.open_interest, self.volume_oi_ratio]
        pricing_fields = [self.bid, self.ask, self.mid, self.spread_pct]
        stock_price_fields = [
            self.stock_open, self.stock_high, self.stock_low,
            self.stock_prev_close, self.stock_change_pct
        ]
        trend_fields = [
            self.sma_20, self.sma_50, self.trend_direction,
            self.trend_strength, self.price_vs_sma20_pct, self.price_vs_sma50_pct
        ]
        market_fields = [self.spy_price, self.spy_change_pct, self.vix, self.vix_change_pct]
        event_fields = [self.earnings_date, self.days_to_earnings, self.earnings_in_dte]
        margin_fields = [self.margin_requirement, self.margin_efficiency_pct]

        # Calculate completeness for each category
        def completeness(fields: list) -> float:
            """Calculate percentage of non-None fields."""
            if not fields:
                return 0.0
            non_none = sum(1 for f in fields if f is not None)
            return non_none / len(fields)

        # Weighted scoring (critical fields have higher weight)
        weights = {
            "critical": 0.40,      # 40% weight on critical fields
            "greeks": 0.10,        # 10% weight on Greeks
            "volatility": 0.10,    # 10% weight on volatility
            "liquidity": 0.05,     # 5% weight on liquidity
            "pricing": 0.05,       # 5% weight on pricing
            "stock_prices": 0.05,  # 5% weight on stock prices
            "trend": 0.10,         # 10% weight on trend
            "market": 0.10,        # 10% weight on market
            "event": 0.03,         # 3% weight on events
            "margin": 0.02,        # 2% weight on margin
        }

        scores = {
            "critical": completeness(critical_fields),
            "greeks": completeness(greeks_fields),
            "volatility": completeness(volatility_fields),
            "liquidity": completeness(liquidity_fields),
            "pricing": completeness(pricing_fields),
            "stock_prices": completeness(stock_price_fields),
            "trend": completeness(trend_fields),
            "market": completeness(market_fields),
            "event": completeness(event_fields),
            "margin": completeness(margin_fields),
        }

        # Calculate weighted score
        total_score = sum(scores[key] * weights[key] for key in scores)

        return round(total_score, 3)

    def get_critical_fields_dict(self) -> dict:
        """Get dictionary of the 8 critical fields with their values.

        Returns:
            dict: Dictionary mapping critical field names to their values
        """
        return {
            "delta": self.delta,
            "iv": self.iv,
            "iv_rank": self.iv_rank,
            "vix": self.vix,
            "dte": self.dte,
            "trend_direction": self.trend_direction,
            "days_to_earnings": self.days_to_earnings,
            "margin_efficiency_pct": self.margin_efficiency_pct,
        }

    def has_all_critical_fields(self) -> bool:
        """Check if all 8 critical fields are populated.

        Returns:
            bool: True if all critical fields have values, False otherwise
        """
        critical = self.get_critical_fields_dict()
        # Note: dte is non-nullable so always present
        return all(value is not None for value in critical.values())

    def get_missing_critical_fields(self) -> list[str]:
        """Get list of critical field names that are missing (None).

        Returns:
            list[str]: List of critical field names with None values
        """
        critical = self.get_critical_fields_dict()
        return [name for name, value in critical.items() if value is None]


class PositionSnapshot(Base):
    """Daily position snapshot for open trades.

    Phase 2.6D - Position Monitoring
    Captures daily snapshots of open positions for path analysis and
    learning pattern detection. Tracks P&L evolution, Greeks changes,
    and distance to strike over time.
    """

    __tablename__ = "position_snapshots"

    # Primary key and foreign keys
    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(Integer, ForeignKey("trades.id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_date = Column(Date, nullable=False, index=True)

    # Position state
    current_premium = Column(Float, nullable=True)
    current_pnl = Column(Float, nullable=True)
    current_pnl_pct = Column(Float, nullable=True)
    dte_remaining = Column(Integer, nullable=True, index=True)

    # Greeks
    delta = Column(Float, nullable=True)
    theta = Column(Float, nullable=True)
    gamma = Column(Float, nullable=True)
    vega = Column(Float, nullable=True)
    iv = Column(Float, nullable=True)

    # Underlying
    stock_price = Column(Float, nullable=True)
    distance_to_strike_pct = Column(Float, nullable=True)

    # Market
    vix = Column(Float, nullable=True)
    spy_price = Column(Float, nullable=True)

    # Metadata
    captured_at = Column(DateTime, nullable=False)

    # Relationships
    trade = relationship("Trade", backref="position_snapshots")

    def __repr__(self) -> str:
        """String representation of PositionSnapshot."""
        return (
            f"<PositionSnapshot(id={self.id}, trade_id={self.trade_id}, "
            f"date={self.snapshot_date}, pnl={self.current_pnl})>"
        )


class TradeExitSnapshot(Base):
    """Comprehensive exit snapshot for closed trades.

    Phase 2.6E - Exit Snapshots & Learning Data Preparation
    Captures complete outcome data when a trade exits, including P&L,
    path analysis, and derived learning features.
    """

    __tablename__ = "trade_exit_snapshots"

    # Primary key and foreign key
    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(Integer, ForeignKey("trades.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)

    # Exit details
    exit_date = Column(DateTime, nullable=False, index=True)
    exit_premium = Column(Float, nullable=False)
    exit_reason = Column(String(50), nullable=False, index=True)  # profit_target, stop_loss, expiration, manual

    # Outcome
    days_held = Column(Integer, nullable=True)
    gross_profit = Column(Float, nullable=True)
    net_profit = Column(Float, nullable=True)
    roi_pct = Column(Float, nullable=True)
    roi_on_margin = Column(Float, nullable=True)
    win = Column(Boolean, nullable=True, index=True)
    max_profit_captured_pct = Column(Float, nullable=True)

    # Context changes during trade
    exit_iv = Column(Float, nullable=True)
    iv_change_during_trade = Column(Float, nullable=True)
    stock_price_at_exit = Column(Float, nullable=True)
    stock_change_during_trade_pct = Column(Float, nullable=True)
    vix_at_exit = Column(Float, nullable=True)
    vix_change_during_trade = Column(Float, nullable=True)

    # Path analysis (from position snapshots)
    closest_to_strike_pct = Column(Float, nullable=True)  # Minimum distance to strike during trade
    max_drawdown_pct = Column(Float, nullable=True)  # Maximum unrealized loss
    max_profit_pct = Column(Float, nullable=True)  # Maximum unrealized profit

    # Learning features
    trade_quality_score = Column(Float, nullable=True)  # 0-1 score based on execution quality
    risk_adjusted_return = Column(Float, nullable=True)  # Return / max_drawdown

    # Metadata
    captured_at = Column(DateTime, nullable=False)

    # Relationships
    trade = relationship("Trade", backref="exit_snapshot", uselist=False)

    def __repr__(self) -> str:
        """String representation of TradeExitSnapshot."""
        return (
            f"<TradeExitSnapshot(id={self.id}, trade_id={self.trade_id}, "
            f"exit_date={self.exit_date}, win={self.win}, roi={self.roi_pct})>"
        )

    def calculate_quality_score(self) -> float:
        """Calculate trade quality score (0-1).

        Factors:
        - Profit capture efficiency (vs max possible)
        - Risk management (drawdown control)
        - Timing (relative to DTE)

        Returns:
            Quality score between 0.0 and 1.0
        """
        score = 0.0
        components = 0

        # Component 1: Profit capture efficiency (if profitable)
        if self.win and self.max_profit_captured_pct is not None:
            score += self.max_profit_captured_pct
            components += 1

        # Component 2: Risk management (drawdown control)
        if self.max_drawdown_pct is not None:
            # Lower drawdown = better score
            drawdown_score = max(0.0, 1.0 - abs(self.max_drawdown_pct))
            score += drawdown_score
            components += 1

        # Component 3: Win/loss outcome
        if self.win is not None:
            score += 1.0 if self.win else 0.0
            components += 1

        return round(score / components if components > 0 else 0.5, 3)


# ============================================================================
# Stock Position Tracking (from option assignments)
# ============================================================================


class StockPosition(Base):
    """Stock position resulting from a naked put assignment.

    When a naked put is assigned, IBKR converts the option to 100 shares
    of stock per contract. This model tracks the resulting stock position
    with combined option + stock P&L.

    P&L Model (Option A — no double-counting):
        option_pnl = premium collected (from origin trade's profit_loss)
        stock_pnl  = (sale_price - strike) * shares  (uses STRIKE as cost basis)
        total_pnl  = option_pnl + stock_pnl
        irs_cost_basis = strike - premium/share  (for tax compliance only)
    """

    __tablename__ = "stock_positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(10), nullable=False, index=True)
    shares = Column(Integer, nullable=False)
    cost_basis_per_share = Column(Float, nullable=False)       # = strike price
    irs_cost_basis_per_share = Column(Float, nullable=False)   # = strike - premium/share
    origin_trade_id = Column(String(50), ForeignKey("trades.trade_id"), nullable=False)

    # Entry
    assigned_date = Column(DateTime, nullable=False)

    # Exit (null while held)
    closed_date = Column(DateTime, nullable=True)
    sale_price_per_share = Column(Float, nullable=True)
    close_reason = Column(String(50), nullable=True)  # sold, partial_sold, still_held

    # P&L
    stock_pnl = Column(Float, nullable=True)   # (sale_price - strike) * shares
    option_pnl = Column(Float, nullable=True)  # copied from origin trade
    total_pnl = Column(Float, nullable=True)   # stock_pnl + option_pnl

    # Metadata
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    origin_trade = relationship("Trade", foreign_keys=[origin_trade_id])

    def __repr__(self) -> str:
        """String representation of StockPosition."""
        status = "HELD" if self.closed_date is None else "CLOSED"
        return (
            f"<StockPosition(symbol={self.symbol}, shares={self.shares}, "
            f"cost={self.cost_basis_per_share}, status={status})>"
        )

    def is_open(self) -> bool:
        """Check if stock position is still held."""
        return self.closed_date is None


# ============================================================================
# Phase 5: Continuous Agentic Trading Daemon Models
# ============================================================================


class DaemonEvent(Base):
    """Durable event queue for the agentic daemon.

    Events are persisted to PostgreSQL for crash-safe replay.
    On startup the daemon replays any pending/processing events.
    """

    __tablename__ = "daemon_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(50), nullable=False, index=True)
    priority = Column(Integer, nullable=False, default=5)  # 1=highest, 10=lowest
    status = Column(String(20), nullable=False, default="pending", index=True)  # pending, processing, completed, failed
    payload = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    processed_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_daemon_events_status_priority", "status", "priority", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<DaemonEvent(id={self.id}, type={self.event_type}, status={self.status})>"


class DecisionAudit(Base):
    """Full audit trail for every daemon decision.

    Records the reasoning context, Claude's output, autonomy gate result,
    and execution outcome for every event the daemon processes.
    """

    __tablename__ = "decision_audit"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(Integer, ForeignKey("daemon_events.id"), nullable=True, index=True)

    # Decision context
    timestamp = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    autonomy_level = Column(Integer, nullable=False)  # 1-4
    event_type = Column(String(50), nullable=False, index=True)

    # Multi-action plan support
    plan_id = Column(String(36), nullable=True, index=True)        # Groups actions from same Claude call
    plan_assessment = Column(Text, nullable=True)                   # Overall market/portfolio assessment
    decision_metadata = Column(JSON, nullable=True)                 # DecisionOutput.metadata (direct storage)

    # Claude reasoning
    action = Column(String(50), nullable=False, index=True)  # STAGE_CANDIDATES, EXECUTE_TRADES, etc.
    confidence = Column(Float, nullable=True)
    reasoning = Column(Text, nullable=True)
    key_factors = Column(JSON, nullable=True)
    risks_considered = Column(JSON, nullable=True)

    # Autonomy gate
    autonomy_approved = Column(Boolean, nullable=False)
    escalation_reason = Column(Text, nullable=True)
    human_override = Column(Boolean, default=False)
    human_decision = Column(String(50), nullable=True)  # approved, rejected, modified
    human_decided_at = Column(DateTime, nullable=True)

    # Execution outcome
    executed = Column(Boolean, default=False)
    execution_result = Column(JSON, nullable=True)
    execution_error = Column(Text, nullable=True)

    # Cost
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    model_used = Column(String(100), nullable=True)
    cost_usd = Column(Float, nullable=True)

    # Phase 6: Guardrail flags (JSON list of GuardrailResult dicts)
    guardrail_flags = Column(JSON, nullable=True)

    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<DecisionAudit(id={self.id}, action={self.action}, confidence={self.confidence})>"


class WorkingMemoryRow(Base):
    """Crash-safe working memory for the daemon.

    Single-row upsert pattern: the daemon always reads/writes row id=1.
    Stores serialized strategy state, market context, recent decisions,
    anomalies, and the current autonomy level.
    """

    __tablename__ = "working_memory"

    id = Column(Integer, primary_key=True, default=1)
    strategy_state = Column(JSON, nullable=True)  # Current strategy parameters
    market_context = Column(JSON, nullable=True)  # Latest market conditions
    recent_decisions = Column(JSON, nullable=True)  # FIFO list, max 50
    anomalies = Column(JSON, nullable=True)  # Detected anomalies
    autonomy_level = Column(Integer, nullable=False, default=1)
    reflection_reports = Column(JSON, nullable=True)  # EOD reflections
    last_scheduled_fingerprint = Column(String(64), nullable=True)  # SHA256 of last context
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<WorkingMemory(id={self.id}, autonomy_level={self.autonomy_level})>"


class DecisionEmbedding(Base):
    """Semantic search index for past decisions.

    Stores vector embeddings of decision reasoning for similarity search.
    pgvector VECTOR(1536) column is added via raw SQL in the migration;
    SQLAlchemy sees it as a generic column.
    """

    __tablename__ = "decision_embeddings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    decision_audit_id = Column(Integer, ForeignKey("decision_audit.id"), nullable=False, index=True)
    text_content = Column(Text, nullable=False)  # The text that was embedded
    # embedding column (VECTOR(1536)) is added via migration raw SQL on PostgreSQL
    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<DecisionEmbedding(id={self.id}, decision_audit_id={self.decision_audit_id})>"


class DaemonHealth(Base):
    """Heartbeat and status tracking for the daemon process.

    Updated every 60s by the running daemon. External monitors
    can query this table to check daemon liveness.
    """

    __tablename__ = "daemon_health"

    id = Column(Integer, primary_key=True, default=1)
    pid = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False, default="stopped")  # running, paused, stopped, error
    last_heartbeat = Column(DateTime, nullable=True)
    last_event_processed = Column(String(50), nullable=True)
    events_processed_today = Column(Integer, default=0)
    decisions_made_today = Column(Integer, default=0)
    errors_today = Column(Integer, default=0)
    uptime_seconds = Column(Integer, default=0)
    started_at = Column(DateTime, nullable=True)
    autonomy_level = Column(Integer, default=1)
    ibkr_connected = Column(Boolean, default=False)
    message = Column(Text, nullable=True)  # Human-readable status message
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<DaemonHealth(pid={self.pid}, status={self.status})>"


class ClaudeApiCost(Base):
    """Tracks every Claude API call for cost monitoring and cap enforcement.

    The daemon checks daily totals before every API call and refuses
    to call Claude if the daily cap is exceeded.
    """

    __tablename__ = "claude_api_costs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    model = Column(String(100), nullable=False)
    purpose = Column(String(50), nullable=False, index=True)  # reasoning, reflection, embedding
    input_tokens = Column(Integer, nullable=False)
    output_tokens = Column(Integer, nullable=False)
    cost_usd = Column(Float, nullable=False)
    daily_total_usd = Column(Float, nullable=True)  # Running daily total at time of call
    decision_audit_id = Column(Integer, ForeignKey("decision_audit.id"), nullable=True)

    __table_args__ = (
        Index("ix_claude_api_costs_date", func.date(timestamp)),
    )

    def __repr__(self) -> str:
        return f"<ClaudeApiCost(id={self.id}, model={self.model}, cost=${self.cost_usd:.4f})>"


class GuardrailMetric(Base):
    """Daily guardrail performance metrics.

    Phase 6: Tracks confidence calibration, reasoning entropy, and
    guardrail activity (blocks, warnings, flagged symbols/numbers).
    """

    __tablename__ = "guardrail_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    metric_date = Column(Date, nullable=False, index=True)
    metric_type = Column(String(50), nullable=False, index=True)  # daily_audit, calibration, entropy

    # Confidence calibration
    confidence_bucket = Column(String(20), nullable=True)
    predicted_accuracy = Column(Float, nullable=True)
    actual_accuracy = Column(Float, nullable=True)
    sample_size = Column(Integer, nullable=True)
    calibration_error = Column(Float, nullable=True)

    # Reasoning entropy
    avg_reasoning_length = Column(Float, nullable=True)
    unique_key_factors_ratio = Column(Float, nullable=True)
    reasoning_similarity_score = Column(Float, nullable=True)

    # Daily activity
    total_decisions = Column(Integer, nullable=True)
    guardrail_blocks = Column(Integer, nullable=True)
    guardrail_warnings = Column(Integer, nullable=True)
    symbols_flagged = Column(Integer, nullable=True)
    numbers_flagged = Column(Integer, nullable=True)

    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<GuardrailMetric(date={self.metric_date}, type={self.metric_type})>"


class DaemonNotification(Base):
    """Lightweight notification with upsert semantics.

    One active row per notification_key.  Updated in-place on each
    occurrence so the dashboard shows a single, self-updating card
    instead of N duplicate approval items.
    """

    __tablename__ = "daemon_notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    notification_key = Column(String(100), unique=True, nullable=False, index=True)
    category = Column(String(50), nullable=False)  # e.g. "data_quality", "system"
    status = Column(String(20), nullable=False, default="active")  # active / resolved
    title = Column(String(200), nullable=False)
    message = Column(Text, nullable=False)
    details = Column(JSON, nullable=True)
    first_seen_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    resolved_at = Column(DateTime, nullable=True)
    occurrence_count = Column(Integer, nullable=False, default=1)

    # Structured action choices: [{key, label, description}, ...]
    action_choices = Column(JSON, nullable=True)
    chosen_action = Column(String(50), nullable=True)
    chosen_at = Column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<DaemonNotification(key={self.notification_key}, status={self.status}, count={self.occurrence_count})>"

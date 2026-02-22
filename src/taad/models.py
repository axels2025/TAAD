"""SQLAlchemy models for the TAAD (Trade Archaeology & Alpha Discovery) system.

These models live in separate PostgreSQL schemas:
- import: Raw IBKR data ingestion
- enrichment: Market context reconstruction
- analysis: Statistical and GenAI analysis outputs

All models use schema-qualified table names to keep them separate
from the existing trading system tables in the public schema.
"""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.sql import func

from src.data.models import Base


# ============================================================
# IMPORT SCHEMA - Raw IBKR data ingestion
# ============================================================


class ImportSession(Base):
    """Tracks each import batch with metadata.

    Each time we pull data from IBKR (via Flex Query, Activity Statement,
    or manual file upload), we create an ImportSession to track it.
    """

    __tablename__ = "import_sessions"
    __table_args__ = {"schema": "import"}

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Session metadata
    started_at = Column(DateTime, nullable=False, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)
    status = Column(String(20), nullable=False, default="running")  # running, completed, failed

    # Source details
    source_type = Column(String(30), nullable=False, index=True)  # flex_query, activity_statement, portfolio_analyst, manual_file
    source_file = Column(String(500), nullable=True)  # File path or Flex Query ID
    account_id = Column(String(20), nullable=True, index=True)

    # Date range covered by this import
    date_range_start = Column(Date, nullable=True)
    date_range_end = Column(Date, nullable=True)

    # Statistics
    total_records = Column(Integer, default=0)
    imported_records = Column(Integer, default=0)
    skipped_duplicates = Column(Integer, default=0)
    error_count = Column(Integer, default=0)

    # Notes
    notes = Column(Text, nullable=True)
    error_details = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<ImportSession(id={self.id}, source={self.source_type}, status={self.status}, records={self.total_records})>"


class IBKRRawImport(Base):
    """Immutable raw records from IBKR Flex Queries and Activity Statements.

    Each row is one trade execution exactly as IBKR reported it.
    We store the complete raw data as JSONB for future-proofing,
    plus extracted key fields for querying.
    """

    __tablename__ = "ibkr_raw_imports"
    __table_args__ = {"schema": "import"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    import_session_id = Column(Integer, nullable=False, index=True)

    # Source tracking
    source_type = Column(String(30), nullable=False)  # flex_query, activity_statement
    account_id = Column(String(20), nullable=False, index=True)
    account_alias = Column(String(50), nullable=True)

    # Raw data (complete IBKR record as JSON)
    raw_data = Column(JSON, nullable=False)

    # Extracted key fields for querying (denormalized from raw_data)
    trade_date = Column(Date, nullable=False, index=True)
    settle_date = Column(Date, nullable=True)
    symbol = Column(String(50), nullable=True)  # Full IBKR option symbol
    underlying_symbol = Column(String(20), nullable=False, index=True)
    strike = Column(Float, nullable=True)
    expiry = Column(Date, nullable=True, index=True)
    put_call = Column(String(5), nullable=True)  # P or C
    asset_category = Column(String(10), nullable=False)  # OPT, STK, etc.

    # Trade details
    buy_sell = Column(String(10), nullable=False)  # BUY or SELL
    open_close = Column(String(10), nullable=True)  # O=Open, C=Close (from code field)
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    amount = Column(Float, nullable=True)  # Total amount (price * quantity * multiplier)
    proceeds = Column(Float, nullable=True)
    net_cash = Column(Float, nullable=True)
    commission = Column(Float, nullable=True)
    multiplier = Column(Integer, nullable=True, default=100)

    # IBKR identifiers (for deduplication)
    ibkr_exec_id = Column(String(100), nullable=True, unique=True, index=True)
    ibkr_trade_id = Column(String(50), nullable=True, index=True)
    ibkr_order_id = Column(String(50), nullable=True, index=True)
    ibkr_conid = Column(String(20), nullable=True)

    # Order metadata
    order_type = Column(String(20), nullable=True)  # LMT, MKT, etc.
    exchange = Column(String(20), nullable=True)
    order_time = Column(DateTime, nullable=True)
    execution_time = Column(DateTime, nullable=True)

    # Level of detail from Flex Query
    level_of_detail = Column(String(30), nullable=True)  # EXECUTION, ORDER, SYMBOL_SUMMARY

    # Processing status
    matched = Column(Boolean, default=False, index=True)
    matched_trade_id = Column(String(50), nullable=True, index=True)  # FK to public.trades.trade_id

    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return (
            f"<IBKRRawImport(id={self.id}, {self.underlying_symbol} "
            f"{self.strike} {self.put_call} {self.buy_sell} x{self.quantity} "
            f"@ {self.price}, exec_id={self.ibkr_exec_id})>"
        )

    def is_option(self) -> bool:
        """Check if this is an options trade."""
        return self.asset_category == "OPT"

    def is_put(self) -> bool:
        """Check if this is a put option."""
        return self.put_call == "P"

    def is_opening(self) -> bool:
        """Check if this is an opening trade."""
        return self.open_close == "O"

    def is_closing(self) -> bool:
        """Check if this is a closing trade."""
        return self.open_close == "C"

    def is_sell_to_open(self) -> bool:
        """Check if this is a Sell to Open (naked put entry)."""
        return self.buy_sell == "SELL" and self.is_opening()

    def is_buy_to_close(self) -> bool:
        """Check if this is a Buy to Close (naked put exit)."""
        return self.buy_sell == "BUY" and self.is_closing()


class TradeMatchingLog(Base):
    """Audit trail of how raw imports are matched into trade lifecycles.

    Each row represents a matched pair: an opening trade matched with
    its corresponding closing trade(s).
    """

    __tablename__ = "trade_matching_log"
    __table_args__ = {"schema": "import"}

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Matched raw imports
    raw_import_id_open = Column(Integer, nullable=False, index=True)
    raw_import_id_close = Column(Integer, nullable=True, index=True)  # NULL for expiration/assignment

    # Result
    matched_trade_id = Column(String(50), nullable=True, index=True)  # FK to public.trades.trade_id
    match_type = Column(String(30), nullable=False)  # sell_to_open+buy_to_close, sell_to_open+expiration, sell_to_open+assignment
    confidence_score = Column(Float, nullable=True)  # 0-1

    # Details
    match_notes = Column(Text, nullable=True)
    matched_at = Column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<TradeMatchingLog(id={self.id}, type={self.match_type}, trade={self.matched_trade_id})>"

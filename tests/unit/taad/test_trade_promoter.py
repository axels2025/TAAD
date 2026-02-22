"""Tests for TAAD trade promoter (matched trades → public.trades)."""

import pytest
from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.data.models import Base, Trade
from src.taad.models import IBKRRawImport, ImportSession, TradeMatchingLog
from src.taad.trade_promoter import (
    PromotionResult,
    promote_matches_to_trades,
    _build_trade_from_match,
)

# TAAD models that use schema="import" (not supported in SQLite)
_TAAD_MODELS = [ImportSession, IBKRRawImport, TradeMatchingLog]


@pytest.fixture
def db_session():
    """Create an in-memory SQLite database for testing.

    Temporarily removes schema qualifications since SQLite
    doesn't support PostgreSQL schemas.
    """
    original_schemas = {}
    for model in _TAAD_MODELS:
        original_schemas[model] = model.__table__.schema
        model.__table__.schema = None

    try:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)
        session = factory()

        import_session = ImportSession(
            status="completed",
            source_type="flex_query",
            account_id="YOUR_ACCOUNT",
        )
        session.add(import_session)
        session.flush()

        yield session, import_session.id

        session.close()
        engine.dispose()
    finally:
        for model, schema in original_schemas.items():
            model.__table__.schema = schema


def _make_raw_import(
    session: Session,
    import_session_id: int,
    underlying: str,
    strike: float,
    expiry: date,
    buy_sell: str,
    open_close: str,
    quantity: int,
    price: float,
    trade_date: date,
    exec_id: str,
    put_call: str = "P",
    account_id: str = "YOUR_ACCOUNT",
    commission: float | None = None,
    multiplier: int = 100,
) -> IBKRRawImport:
    """Helper to create a raw import record."""
    record = IBKRRawImport(
        import_session_id=import_session_id,
        source_type="flex_query",
        account_id=account_id,
        raw_data={"test": True},
        trade_date=trade_date,
        underlying_symbol=underlying,
        strike=strike,
        expiry=expiry,
        put_call=put_call,
        asset_category="OPT",
        buy_sell=buy_sell,
        open_close=open_close,
        quantity=quantity,
        price=price,
        commission=commission,
        multiplier=multiplier,
        ibkr_exec_id=exec_id,
        level_of_detail="EXECUTION",
    )
    session.add(record)
    session.flush()
    return record


def _make_match_log(
    session: Session,
    open_id: int,
    close_id: int | None,
    match_type: str,
    confidence: float = 1.0,
) -> TradeMatchingLog:
    """Helper to create a matching log record."""
    log = TradeMatchingLog(
        raw_import_id_open=open_id,
        raw_import_id_close=close_id,
        match_type=match_type,
        confidence_score=confidence,
    )
    session.add(log)
    session.flush()
    return log


class TestBuildTradeFromMatch:
    """Test the low-level trade building logic."""

    def test_btc_trade_builds_correctly(self, db_session):
        """A sell_to_open+buy_to_close match should produce correct Trade fields."""
        session, import_id = db_session

        sto = _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=150.0, expiry=date(2025, 3, 21),
            buy_sell="SELL", open_close="O", quantity=-5, price=0.45,
            trade_date=date(2025, 2, 10), exec_id="sto-001",
            commission=-3.50,
        )
        btc = _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=150.0, expiry=date(2025, 3, 21),
            buy_sell="BUY", open_close="C", quantity=5, price=0.10,
            trade_date=date(2025, 3, 5), exec_id="btc-001",
            commission=-3.50,
        )
        match_log = _make_match_log(
            session, sto.id, btc.id, "sell_to_open+buy_to_close"
        )

        trade = _build_trade_from_match(match_log, sto, btc)

        assert trade is not None
        assert trade.trade_id == "IBKR_sto-001"
        assert trade.symbol == "AAPL"
        assert trade.strike == 150.0
        assert trade.option_type == "PUT"
        assert trade.entry_premium == 0.45
        assert trade.contracts == 5
        assert trade.exit_premium == 0.10
        assert trade.exit_reason == "buy_to_close"
        assert trade.dte == 39  # Feb 10 → Mar 21
        assert trade.days_held == 23  # Feb 10 → Mar 5
        assert trade.trade_source == "ibkr_import"
        assert trade.account_id == "YOUR_ACCOUNT"
        assert trade.ibkr_execution_id == "sto-001"
        assert trade.enrichment_status == "pending"
        assert trade.otm_pct is None
        assert trade.exit_date is not None

        # P&L: (0.45 - 0.10) * 5 * 100 = 175.00 gross - 7.00 commission = 168.00
        assert trade.commission == 7.0
        assert abs(trade.profit_loss - 168.0) < 0.01

    def test_expiration_trade_builds_correctly(self, db_session):
        """An expiration match should use expiry date as exit date."""
        session, import_id = db_session

        sto = _make_raw_import(
            session, import_id,
            underlying="MSFT", strike=350.0, expiry=date(2025, 4, 17),
            buy_sell="SELL", open_close="O", quantity=-2, price=0.80,
            trade_date=date(2025, 3, 24), exec_id="sto-002",
        )
        match_log = _make_match_log(
            session, sto.id, None, "sell_to_open+expiration"
        )

        trade = _build_trade_from_match(match_log, sto, None)

        assert trade is not None
        assert trade.exit_reason == "expiration"
        assert trade.exit_premium == 0.0
        assert trade.exit_date == datetime.combine(date(2025, 4, 17), datetime.min.time())
        assert trade.days_held == 24  # Mar 24 → Apr 17
        # P&L: 0.80 * 2 * 100 = 160.00 (full premium, no commissions)
        assert abs(trade.profit_loss - 160.0) < 0.01

    def test_no_exit_date_returns_none(self, db_session):
        """If no exit date can be determined, return None."""
        session, import_id = db_session

        # Construct an STO with no expiry and no close — edge case
        sto = _make_raw_import(
            session, import_id,
            underlying="TSLA", strike=200.0, expiry=None,
            buy_sell="SELL", open_close="O", quantity=-1, price=0.50,
            trade_date=date(2025, 3, 1), exec_id="sto-003",
        )
        match_log = _make_match_log(
            session, sto.id, None, "sell_to_open+expiration"
        )

        trade = _build_trade_from_match(match_log, sto, None)
        assert trade is None

    def test_call_option_type(self, db_session):
        """Call options should have option_type='CALL'."""
        session, import_id = db_session

        sto = _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=200.0, expiry=date(2025, 5, 16),
            buy_sell="SELL", open_close="O", quantity=-1, price=1.00,
            trade_date=date(2025, 4, 28), exec_id="sto-004",
            put_call="C",
        )
        match_log = _make_match_log(
            session, sto.id, None, "sell_to_open+expiration"
        )

        trade = _build_trade_from_match(match_log, sto, None)
        assert trade.option_type == "CALL"


class TestPromoteMatchesToTrades:
    """Test the full promotion pipeline."""

    def test_promotes_unpromoted_matches(self, db_session):
        """Should create Trade records for unpromoted matches."""
        session, import_id = db_session

        sto = _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=150.0, expiry=date(2025, 3, 21),
            buy_sell="SELL", open_close="O", quantity=-5, price=0.45,
            trade_date=date(2025, 2, 10), exec_id="promo-sto-001",
        )
        btc = _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=150.0, expiry=date(2025, 3, 21),
            buy_sell="BUY", open_close="C", quantity=5, price=0.10,
            trade_date=date(2025, 3, 5), exec_id="promo-btc-001",
        )
        match_log = _make_match_log(
            session, sto.id, btc.id, "sell_to_open+buy_to_close"
        )

        result = promote_matches_to_trades(session)

        assert result.promoted == 1
        assert result.errors == 0

        # Verify Trade was created
        trade = session.query(Trade).filter(
            Trade.trade_id == "IBKR_promo-sto-001"
        ).first()
        assert trade is not None
        assert trade.trade_source == "ibkr_import"
        assert trade.exit_date is not None

        # Verify match_log was linked
        session.refresh(match_log)
        assert match_log.matched_trade_id == trade.trade_id

    def test_skips_already_promoted(self, db_session):
        """Matches with matched_trade_id already set should be skipped."""
        session, import_id = db_session

        sto = _make_raw_import(
            session, import_id,
            underlying="MSFT", strike=300.0, expiry=date(2025, 4, 17),
            buy_sell="SELL", open_close="O", quantity=-1, price=0.60,
            trade_date=date(2025, 3, 24), exec_id="promo-sto-002",
        )
        match_log = _make_match_log(
            session, sto.id, None, "sell_to_open+expiration"
        )
        # Simulate already promoted
        match_log.matched_trade_id = "IBKR_promo-sto-002"
        session.flush()

        result = promote_matches_to_trades(session)

        # Should find 0 unpromoted matches
        assert result.promoted == 0
        assert result.total_processed == 0

    def test_idempotent_rerun(self, db_session):
        """Running promote twice should not create duplicate trades."""
        session, import_id = db_session

        sto = _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=160.0, expiry=date(2025, 5, 16),
            buy_sell="SELL", open_close="O", quantity=-3, price=0.50,
            trade_date=date(2025, 4, 7), exec_id="promo-sto-003",
        )
        match_log = _make_match_log(
            session, sto.id, None, "sell_to_open+expiration"
        )

        # First run
        result1 = promote_matches_to_trades(session)
        assert result1.promoted == 1

        # Reset matched_trade_id to simulate a re-run scenario
        # (In practice this wouldn't happen, but test dedup by ibkr_execution_id)
        match_log.matched_trade_id = None
        session.flush()

        # Second run — should find existing Trade by ibkr_execution_id
        result2 = promote_matches_to_trades(session)
        assert result2.promoted == 0
        assert result2.skipped_already_promoted == 1

        # Only one Trade should exist
        trades = session.query(Trade).filter(
            Trade.ibkr_execution_id == "promo-sto-003"
        ).all()
        assert len(trades) == 1

    def test_dry_run_no_writes(self, db_session):
        """Dry run should not write any data."""
        session, import_id = db_session

        sto = _make_raw_import(
            session, import_id,
            underlying="NVDA", strike=700.0, expiry=date(2025, 6, 20),
            buy_sell="SELL", open_close="O", quantity=-1, price=2.00,
            trade_date=date(2025, 5, 5), exec_id="promo-sto-004",
        )
        match_log = _make_match_log(
            session, sto.id, None, "sell_to_open+expiration"
        )

        result = promote_matches_to_trades(session, dry_run=True)

        assert result.promoted == 1

        # No Trade should exist
        trade = session.query(Trade).filter(
            Trade.trade_id == "IBKR_promo-sto-004"
        ).first()
        assert trade is None

        # match_log should not be linked
        session.refresh(match_log)
        assert match_log.matched_trade_id is None

    def test_account_filter(self, db_session):
        """Account filter should only promote matching account trades."""
        session, import_id = db_session

        # Account A
        sto_a = _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=150.0, expiry=date(2025, 3, 21),
            buy_sell="SELL", open_close="O", quantity=-1, price=0.50,
            trade_date=date(2025, 2, 10), exec_id="promo-sto-A",
            account_id="U1111111",
        )
        _make_match_log(session, sto_a.id, None, "sell_to_open+expiration")

        # Account B
        sto_b = _make_raw_import(
            session, import_id,
            underlying="MSFT", strike=300.0, expiry=date(2025, 3, 21),
            buy_sell="SELL", open_close="O", quantity=-1, price=0.50,
            trade_date=date(2025, 2, 10), exec_id="promo-sto-B",
            account_id="U2222222",
        )
        _make_match_log(session, sto_b.id, None, "sell_to_open+expiration")

        result = promote_matches_to_trades(session, account_id="U1111111")

        assert result.promoted == 1
        # Only account A's trade should be promoted
        trade = session.query(Trade).filter(Trade.trade_source == "ibkr_import").first()
        assert trade.account_id == "U1111111"

    def test_all_promoted_trades_are_closed(self, db_session):
        """Every promoted trade must have an exit_date (closed trade)."""
        session, import_id = db_session

        # Create multiple matches of different types
        sto1 = _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=150.0, expiry=date(2025, 3, 21),
            buy_sell="SELL", open_close="O", quantity=-1, price=0.50,
            trade_date=date(2025, 2, 10), exec_id="closed-sto-1",
        )
        btc1 = _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=150.0, expiry=date(2025, 3, 21),
            buy_sell="BUY", open_close="C", quantity=1, price=0.10,
            trade_date=date(2025, 3, 10), exec_id="closed-btc-1",
        )
        _make_match_log(session, sto1.id, btc1.id, "sell_to_open+buy_to_close")

        sto2 = _make_raw_import(
            session, import_id,
            underlying="MSFT", strike=350.0, expiry=date(2025, 4, 17),
            buy_sell="SELL", open_close="O", quantity=-2, price=0.80,
            trade_date=date(2025, 3, 24), exec_id="closed-sto-2",
        )
        _make_match_log(session, sto2.id, None, "sell_to_open+expiration")

        result = promote_matches_to_trades(session)
        assert result.promoted == 2

        # Verify all trades are closed
        open_imports = (
            session.query(Trade)
            .filter(Trade.trade_source == "ibkr_import")
            .filter(Trade.exit_date.is_(None))
            .count()
        )
        assert open_imports == 0


class TestTradeRepositorySourceFilter:
    """Test trade_source filtering in TradeRepository."""

    def test_get_all_no_filter_returns_everything(self, db_session):
        """get_all() with no source filter returns all trades."""
        from src.data.repositories import TradeRepository

        session, _ = db_session

        # Create trades with different sources
        _make_trade(session, "T1", "AAPL", trade_source="live")
        _make_trade(session, "T2", "MSFT", trade_source="ibkr_import")

        repo = TradeRepository(session)
        all_trades = repo.get_all()
        assert len(all_trades) == 2

    def test_get_all_with_source_filter(self, db_session):
        """get_all(trade_source=[...]) returns only matching trades."""
        from src.data.repositories import TradeRepository

        session, _ = db_session

        _make_trade(session, "T3", "AAPL", trade_source="live")
        _make_trade(session, "T4", "MSFT", trade_source="ibkr_import")
        _make_trade(session, "T5", "TSLA", trade_source="ibkr_import")

        repo = TradeRepository(session)

        live_only = repo.get_all(trade_source=["live"])
        assert len(live_only) == 1
        assert live_only[0].trade_id == "T3"

        imports_only = repo.get_all(trade_source=["ibkr_import"])
        assert len(imports_only) == 2

        both = repo.get_all(trade_source=["live", "ibkr_import"])
        assert len(both) == 3

    def test_get_closed_trades_with_source_filter(self, db_session):
        """get_closed_trades filters by trade_source correctly."""
        from src.data.repositories import TradeRepository

        session, _ = db_session

        _make_trade(session, "TC1", "AAPL", trade_source="live", closed=True)
        _make_trade(session, "TC2", "MSFT", trade_source="ibkr_import", closed=True)
        _make_trade(session, "TC3", "TSLA", trade_source="ibkr_import", closed=True)

        repo = TradeRepository(session)

        live_closed = repo.get_closed_trades(trade_source=["live"])
        assert len(live_closed) == 1

        import_closed = repo.get_closed_trades(trade_source=["ibkr_import"])
        assert len(import_closed) == 2

    def test_get_open_trades_with_source_filter(self, db_session):
        """get_open_trades filters by trade_source correctly."""
        from src.data.repositories import TradeRepository

        session, _ = db_session

        _make_trade(session, "TO1", "AAPL", trade_source="live", closed=False)
        _make_trade(session, "TO2", "MSFT", trade_source="ibkr_import", closed=True)

        repo = TradeRepository(session)

        live_open = repo.get_open_trades(trade_source=["live"])
        assert len(live_open) == 1
        assert live_open[0].trade_id == "TO1"

        import_open = repo.get_open_trades(trade_source=["ibkr_import"])
        assert len(import_open) == 0  # All imports are closed

    def test_get_trades_by_source(self, db_session):
        """get_trades_by_source returns only trades with exact source match."""
        from src.data.repositories import TradeRepository

        session, _ = db_session

        _make_trade(session, "TS1", "AAPL", trade_source="live")
        _make_trade(session, "TS2", "MSFT", trade_source="ibkr_import")
        _make_trade(session, "TS3", "TSLA", trade_source=None)

        repo = TradeRepository(session)

        live = repo.get_trades_by_source("live")
        assert len(live) == 1
        assert live[0].trade_id == "TS1"

        imports = repo.get_trades_by_source("ibkr_import")
        assert len(imports) == 1
        assert imports[0].trade_id == "TS2"


def _make_trade(
    session: Session,
    trade_id: str,
    symbol: str,
    trade_source: str | None = None,
    closed: bool = False,
) -> Trade:
    """Helper to create a minimal Trade record for testing."""
    trade = Trade(
        trade_id=trade_id,
        symbol=symbol,
        strike=100.0,
        expiration=date(2025, 6, 20),
        option_type="PUT",
        entry_date=datetime(2025, 5, 1),
        entry_premium=0.50,
        contracts=1,
        dte=50,
        trade_source=trade_source,
        enrichment_status="pending",
    )
    if closed:
        trade.exit_date = datetime(2025, 6, 1)
        trade.exit_premium = 0.10
        trade.exit_reason = "expiration"
        trade.profit_loss = 40.0
        trade.profit_pct = 0.80
        trade.roi = 0.80
        trade.days_held = 31

    session.add(trade)
    session.flush()
    return trade

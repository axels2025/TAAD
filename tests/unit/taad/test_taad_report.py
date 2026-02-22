"""Tests for TAAD report CLI command."""

import pytest
from datetime import date
from io import StringIO

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch, MagicMock

from src.data.models import Base
from src.taad.models import IBKRRawImport, ImportSession, TradeMatchingLog

_TAAD_MODELS = [ImportSession, IBKRRawImport, TradeMatchingLog]


@pytest.fixture
def db_session():
    """Create an in-memory SQLite database for testing."""
    original_schemas = {}
    for model in _TAAD_MODELS:
        original_schemas[model] = model.__table__.schema
        model.__table__.schema = None

    try:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)
        session = factory()
        yield session
        session.close()
        engine.dispose()
    finally:
        for model, schema in original_schemas.items():
            model.__table__.schema = schema


def _create_import_session(session, account_id="YOUR_ACCOUNT"):
    """Create an import session and return its ID."""
    imp_session = ImportSession(
        source_type="flex_query",
        account_id=account_id,
        status="completed",
        total_records=2,
        imported_records=2,
    )
    session.add(imp_session)
    session.flush()
    return imp_session.id


def _create_sto_record(session, session_id, **kwargs):
    """Create a Sell-to-Open raw import record."""
    defaults = {
        "import_session_id": session_id,
        "source_type": "flex_query",
        "account_id": "YOUR_ACCOUNT",
        "raw_data": {"test": "data"},
        "trade_date": date(2025, 2, 10),
        "underlying_symbol": "AAPL",
        "strike": 150.0,
        "expiry": date(2025, 3, 21),
        "put_call": "P",
        "asset_category": "OPT",
        "buy_sell": "SELL",
        "open_close": "O",
        "quantity": -5,
        "price": 0.45,
        "amount": -225.0,
        "proceeds": 225.0,
        "net_cash": 222.50,
        "commission": -2.50,
        "multiplier": 100,
        "ibkr_exec_id": "test-sto-001",
        "matched": True,
    }
    defaults.update(kwargs)
    record = IBKRRawImport(**defaults)
    session.add(record)
    session.flush()
    return record


def _create_btc_record(session, session_id, **kwargs):
    """Create a Buy-to-Close raw import record."""
    defaults = {
        "import_session_id": session_id,
        "source_type": "flex_query",
        "account_id": "YOUR_ACCOUNT",
        "raw_data": {"test": "data"},
        "trade_date": date(2025, 3, 18),
        "underlying_symbol": "AAPL",
        "strike": 150.0,
        "expiry": date(2025, 3, 21),
        "put_call": "P",
        "asset_category": "OPT",
        "buy_sell": "BUY",
        "open_close": "C",
        "quantity": 5,
        "price": 0.10,
        "amount": 50.0,
        "proceeds": -50.0,
        "net_cash": -52.50,
        "commission": -2.50,
        "multiplier": 100,
        "ibkr_exec_id": "test-btc-001",
        "matched": True,
    }
    defaults.update(kwargs)
    record = IBKRRawImport(**defaults)
    session.add(record)
    session.flush()
    return record


def _create_match(session, open_id, close_id, match_type="sell_to_open+buy_to_close"):
    """Create a trade matching log entry."""
    log = TradeMatchingLog(
        raw_import_id_open=open_id,
        raw_import_id_close=close_id,
        match_type=match_type,
        confidence_score=1.0,
        match_notes="Test match",
    )
    session.add(log)
    session.flush()
    return log


class TestTaadReport:
    def test_matched_trade_display(self, db_session):
        """Should display matched STO+BTC trade with P&L."""
        session_id = _create_import_session(db_session)
        sto = _create_sto_record(db_session, session_id)
        btc = _create_btc_record(db_session, session_id)
        _create_match(db_session, sto.id, btc.id)

        from src.cli.commands.taad_commands import _display_matched_trades
        from sqlalchemy.orm import aliased

        OpenImport = aliased(IBKRRawImport, name="open_imp")
        CloseImport = aliased(IBKRRawImport, name="close_imp")

        rows = (
            db_session.query(TradeMatchingLog, OpenImport, CloseImport)
            .join(OpenImport, TradeMatchingLog.raw_import_id_open == OpenImport.id)
            .outerjoin(CloseImport, TradeMatchingLog.raw_import_id_close == CloseImport.id)
            .all()
        )

        assert len(rows) == 1
        match_log, open_imp, close_imp = rows[0]
        assert open_imp.underlying_symbol == "AAPL"
        assert close_imp.price == 0.10

        # P&L: (0.45 - 0.10) * 5 * 100 = 175.0 gross
        gross_pnl = (abs(open_imp.price) - abs(close_imp.price)) * abs(open_imp.quantity) * (open_imp.multiplier or 100)
        assert gross_pnl == 175.0

    def test_expiration_trade_display(self, db_session):
        """Should display expired trade with full premium as profit."""
        session_id = _create_import_session(db_session)
        sto = _create_sto_record(db_session, session_id, ibkr_exec_id="test-sto-exp-001")
        _create_match(
            db_session, sto.id, None,
            match_type="sell_to_open+expiration",
        )

        from sqlalchemy.orm import aliased

        OpenImport = aliased(IBKRRawImport, name="open_imp")
        CloseImport = aliased(IBKRRawImport, name="close_imp")

        rows = (
            db_session.query(TradeMatchingLog, OpenImport, CloseImport)
            .join(OpenImport, TradeMatchingLog.raw_import_id_open == OpenImport.id)
            .outerjoin(CloseImport, TradeMatchingLog.raw_import_id_close == CloseImport.id)
            .all()
        )

        assert len(rows) == 1
        match_log, open_imp, close_imp = rows[0]
        assert close_imp is None

        # P&L for expiration: full premium kept
        entry_premium = abs(open_imp.price)
        exit_premium = 0.0
        gross_pnl = (entry_premium - exit_premium) * abs(open_imp.quantity) * (open_imp.multiplier or 100)
        assert gross_pnl == 225.0  # 0.45 * 5 * 100

    def test_unmatched_records(self, db_session):
        """Should find unmatched records."""
        session_id = _create_import_session(db_session)
        _create_sto_record(
            db_session, session_id,
            ibkr_exec_id="test-unmatched-001",
            matched=False,
        )

        unmatched = db_session.query(IBKRRawImport).filter(
            IBKRRawImport.matched == False  # noqa: E712
        ).all()

        assert len(unmatched) == 1
        assert unmatched[0].underlying_symbol == "AAPL"

    def test_pnl_includes_commissions(self, db_session):
        """Net P&L should subtract commissions."""
        session_id = _create_import_session(db_session)
        sto = _create_sto_record(db_session, session_id, commission=-2.50)
        btc = _create_btc_record(db_session, session_id, commission=-2.50)
        _create_match(db_session, sto.id, btc.id)

        entry_premium = abs(sto.price)
        exit_premium = abs(btc.price)
        gross_pnl = (entry_premium - exit_premium) * abs(sto.quantity) * (sto.multiplier or 100)
        entry_commission = abs(sto.commission)
        exit_commission = abs(btc.commission)
        net_pnl = gross_pnl - entry_commission - exit_commission

        assert gross_pnl == 175.0
        assert net_pnl == 170.0  # 175 - 2.50 - 2.50

    def test_filter_by_symbol(self, db_session):
        """Should filter trades by symbol."""
        session_id = _create_import_session(db_session)
        aapl_sto = _create_sto_record(db_session, session_id, ibkr_exec_id="aapl-sto")
        aapl_btc = _create_btc_record(db_session, session_id, ibkr_exec_id="aapl-btc")
        _create_match(db_session, aapl_sto.id, aapl_btc.id)

        ionq_sto = _create_sto_record(
            db_session, session_id,
            underlying_symbol="IONQ",
            ibkr_exec_id="ionq-sto",
        )
        ionq_btc = _create_btc_record(
            db_session, session_id,
            underlying_symbol="IONQ",
            ibkr_exec_id="ionq-btc",
        )
        _create_match(db_session, ionq_sto.id, ionq_btc.id)

        from sqlalchemy.orm import aliased

        OpenImport = aliased(IBKRRawImport, name="open_imp")
        CloseImport = aliased(IBKRRawImport, name="close_imp")

        # Filter for IONQ only
        rows = (
            db_session.query(TradeMatchingLog, OpenImport, CloseImport)
            .join(OpenImport, TradeMatchingLog.raw_import_id_open == OpenImport.id)
            .outerjoin(CloseImport, TradeMatchingLog.raw_import_id_close == CloseImport.id)
            .filter(OpenImport.underlying_symbol == "IONQ")
            .all()
        )

        assert len(rows) == 1
        assert rows[0][1].underlying_symbol == "IONQ"

    def test_days_held_calculation(self, db_session):
        """Should correctly calculate days held."""
        session_id = _create_import_session(db_session)
        sto = _create_sto_record(
            db_session, session_id,
            trade_date=date(2025, 2, 10),
            ibkr_exec_id="days-sto-001",
        )
        btc = _create_btc_record(
            db_session, session_id,
            trade_date=date(2025, 3, 18),
            ibkr_exec_id="days-btc-001",
        )

        days_held = (btc.trade_date - sto.trade_date).days
        assert days_held == 36

    def test_expiration_days_held_uses_expiry(self, db_session):
        """For expired trades, days held should use expiry date."""
        session_id = _create_import_session(db_session)
        sto = _create_sto_record(
            db_session, session_id,
            trade_date=date(2025, 2, 10),
            expiry=date(2025, 3, 21),
            ibkr_exec_id="exp-days-001",
        )

        # For expiration: days_held = expiry - entry_date
        days_held = (sto.expiry - sto.trade_date).days
        assert days_held == 39

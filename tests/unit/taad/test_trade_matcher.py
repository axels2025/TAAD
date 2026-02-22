"""Tests for TAAD trade matcher."""

import pytest
from datetime import date

from src.data.models import Base
from src.taad.models import IBKRRawImport, ImportSession, TradeMatchingLog
from src.taad.trade_matcher import MatchedTrade, match_trades, persist_matches

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

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
        ibkr_exec_id=exec_id,
        level_of_detail="EXECUTION",
    )
    session.add(record)
    session.flush()
    return record


class TestMatchTrades:
    def test_simple_sto_btc_match(self, db_session):
        """STO followed by BTC on same contract should match."""
        session, import_id = db_session

        sto = _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=150.0, expiry=date(2025, 3, 21),
            buy_sell="SELL", open_close="O", quantity=-5, price=0.45,
            trade_date=date(2025, 2, 10), exec_id="sto-001",
        )
        btc = _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=150.0, expiry=date(2025, 3, 21),
            buy_sell="BUY", open_close="C", quantity=5, price=0.10,
            trade_date=date(2025, 3, 18), exec_id="btc-001",
        )

        matches = match_trades(session, account_id="YOUR_ACCOUNT")

        assert len(matches) == 1
        assert matches[0].open_import.id == sto.id
        assert matches[0].close_import.id == btc.id
        assert matches[0].match_type == "sell_to_open+buy_to_close"
        assert matches[0].confidence_score == 1.0

    def test_expiration_match(self, db_session):
        """STO with past expiry and no BTC should match as expiration."""
        session, import_id = db_session

        sto = _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=150.0, expiry=date(2025, 1, 17),
            buy_sell="SELL", open_close="O", quantity=-3, price=0.30,
            trade_date=date(2025, 1, 6), exec_id="sto-exp-001",
        )

        matches = match_trades(
            session, account_id="YOUR_ACCOUNT",
            reference_date=date(2025, 2, 1),
        )

        assert len(matches) == 1
        assert matches[0].open_import.id == sto.id
        assert matches[0].close_import is None
        assert matches[0].match_type == "sell_to_open+expiration"

    def test_no_match_for_open_position(self, db_session):
        """STO with future expiry and no BTC should not match."""
        session, import_id = db_session

        _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=150.0, expiry=date(2025, 6, 20),
            buy_sell="SELL", open_close="O", quantity=-3, price=0.30,
            trade_date=date(2025, 2, 10), exec_id="sto-open-001",
        )

        matches = match_trades(
            session, account_id="YOUR_ACCOUNT",
            reference_date=date(2025, 2, 15),
        )

        assert len(matches) == 0

    def test_partial_close(self, db_session):
        """BTC for fewer contracts than STO = partial close."""
        session, import_id = db_session

        sto = _make_raw_import(
            session, import_id,
            underlying="TSLA", strike=200.0, expiry=date(2025, 3, 21),
            buy_sell="SELL", open_close="O", quantity=-5, price=1.00,
            trade_date=date(2025, 2, 10), exec_id="sto-partial-001",
        )
        btc = _make_raw_import(
            session, import_id,
            underlying="TSLA", strike=200.0, expiry=date(2025, 3, 21),
            buy_sell="BUY", open_close="C", quantity=3, price=0.20,
            trade_date=date(2025, 3, 10), exec_id="btc-partial-001",
        )

        matches = match_trades(
            session, account_id="YOUR_ACCOUNT",
            reference_date=date(2025, 2, 15),  # Before expiry
        )

        assert len(matches) == 1
        assert matches[0].confidence_score == 0.9  # Partial
        assert "Partial close" in matches[0].notes

    def test_multiple_contracts_same_symbol(self, db_session):
        """Multiple STO and BTC on same contract should match in order."""
        session, import_id = db_session

        sto1 = _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=150.0, expiry=date(2025, 3, 21),
            buy_sell="SELL", open_close="O", quantity=-2, price=0.45,
            trade_date=date(2025, 2, 10), exec_id="sto-multi-001",
        )
        sto2 = _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=150.0, expiry=date(2025, 3, 21),
            buy_sell="SELL", open_close="O", quantity=-3, price=0.50,
            trade_date=date(2025, 2, 12), exec_id="sto-multi-002",
        )
        btc1 = _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=150.0, expiry=date(2025, 3, 21),
            buy_sell="BUY", open_close="C", quantity=2, price=0.10,
            trade_date=date(2025, 3, 15), exec_id="btc-multi-001",
        )
        btc2 = _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=150.0, expiry=date(2025, 3, 21),
            buy_sell="BUY", open_close="C", quantity=3, price=0.15,
            trade_date=date(2025, 3, 18), exec_id="btc-multi-002",
        )

        matches = match_trades(session, account_id="YOUR_ACCOUNT")

        assert len(matches) == 2
        # First STO matched with first BTC
        assert matches[0].open_import.id == sto1.id
        assert matches[0].close_import.id == btc1.id
        # Second STO matched with second BTC
        assert matches[1].open_import.id == sto2.id
        assert matches[1].close_import.id == btc2.id

    def test_different_strikes_not_matched(self, db_session):
        """STO and BTC at different strikes should not match."""
        session, import_id = db_session

        _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=150.0, expiry=date(2025, 3, 21),
            buy_sell="SELL", open_close="O", quantity=-5, price=0.45,
            trade_date=date(2025, 2, 10), exec_id="sto-diff-001",
        )
        _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=145.0, expiry=date(2025, 3, 21),
            buy_sell="BUY", open_close="C", quantity=5, price=0.10,
            trade_date=date(2025, 3, 18), exec_id="btc-diff-001",
        )

        matches = match_trades(
            session, account_id="YOUR_ACCOUNT",
            reference_date=date(2025, 2, 15),
        )

        assert len(matches) == 0

    def test_skips_already_matched(self, db_session):
        """Records already marked as matched should be skipped."""
        session, import_id = db_session

        sto = _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=150.0, expiry=date(2025, 3, 21),
            buy_sell="SELL", open_close="O", quantity=-5, price=0.45,
            trade_date=date(2025, 2, 10), exec_id="sto-matched-001",
        )
        sto.matched = True
        session.flush()

        matches = match_trades(session, account_id="YOUR_ACCOUNT")
        assert len(matches) == 0

    def test_account_filter(self, db_session):
        """Only match records for the specified account."""
        session, import_id = db_session

        _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=150.0, expiry=date(2025, 1, 17),
            buy_sell="SELL", open_close="O", quantity=-5, price=0.45,
            trade_date=date(2025, 1, 6), exec_id="sto-acct-001",
            account_id="U9999999",
        )

        matches = match_trades(
            session, account_id="YOUR_ACCOUNT",
            reference_date=date(2025, 2, 1),
        )
        assert len(matches) == 0


class TestPersistMatches:
    def test_persist_creates_log_and_marks_matched(self, db_session):
        """persist_matches should create TradeMatchingLog and mark records."""
        session, import_id = db_session

        sto = _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=150.0, expiry=date(2025, 3, 21),
            buy_sell="SELL", open_close="O", quantity=-5, price=0.45,
            trade_date=date(2025, 2, 10), exec_id="sto-persist-001",
        )
        btc = _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=150.0, expiry=date(2025, 3, 21),
            buy_sell="BUY", open_close="C", quantity=5, price=0.10,
            trade_date=date(2025, 3, 18), exec_id="btc-persist-001",
        )

        matches = [
            MatchedTrade(
                open_import=sto,
                close_import=btc,
                match_type="sell_to_open+buy_to_close",
                confidence_score=1.0,
                notes="Test match",
            )
        ]

        count = persist_matches(session, matches)

        assert count == 1
        assert sto.matched is True
        assert btc.matched is True

        # Check log entry
        logs = session.query(TradeMatchingLog).all()
        assert len(logs) == 1
        assert logs[0].raw_import_id_open == sto.id
        assert logs[0].raw_import_id_close == btc.id
        assert logs[0].match_type == "sell_to_open+buy_to_close"
        assert logs[0].confidence_score == 1.0

    def test_persist_expiration_match(self, db_session):
        """Expiration match should have NULL close import."""
        session, import_id = db_session

        sto = _make_raw_import(
            session, import_id,
            underlying="AAPL", strike=150.0, expiry=date(2025, 1, 17),
            buy_sell="SELL", open_close="O", quantity=-3, price=0.30,
            trade_date=date(2025, 1, 6), exec_id="sto-exp-persist-001",
        )

        matches = [
            MatchedTrade(
                open_import=sto,
                close_import=None,
                match_type="sell_to_open+expiration",
                confidence_score=0.95,
            )
        ]

        count = persist_matches(session, matches)
        assert count == 1
        assert sto.matched is True

        logs = session.query(TradeMatchingLog).all()
        assert logs[0].raw_import_id_close is None
        assert logs[0].match_type == "sell_to_open+expiration"

"""Tests for TAAD importer orchestrator."""

import pytest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.data.models import Base
from src.taad.importer import DuplicateExecIDError, ImportResult, run_import
from src.taad.models import IBKRRawImport, ImportSession, TradeMatchingLog

# TAAD models that use schema="import" (not supported in SQLite)
_TAAD_MODELS = [ImportSession, IBKRRawImport, TradeMatchingLog]


SAMPLE_FLEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<FlexStatementResponse>
<FlexStatements count="1">
<FlexStatement accountId="YOUR_ACCOUNT">
<TradeConfirms>
<TradeConfirm
    accountId="YOUR_ACCOUNT"
    acctAlias="Main"
    symbol="AAPL  250321P00150000"
    underlyingSymbol="AAPL"
    assetCategory="OPT"
    putCall="P"
    strike="150"
    expiry="21/03/2025"
    multiplier="100"
    buySell="SELL"
    code="O"
    quantity="-5"
    tradePrice="0.45"
    amount="-225"
    proceeds="225"
    netCash="222.50"
    ibCommission="-2.50"
    tradeDate="10/02/2025"
    settleDate="11/02/2025"
    execID="import-test-sto-001"
    tradeID="987654321"
    orderID="12345"
    conid="654321"
    orderType="LMT"
    exchange="SMART"
    levelOfDetail="EXECUTION"
/>
<TradeConfirm
    accountId="YOUR_ACCOUNT"
    acctAlias="Main"
    symbol="AAPL  250321P00150000"
    underlyingSymbol="AAPL"
    assetCategory="OPT"
    putCall="P"
    strike="150"
    expiry="21/03/2025"
    multiplier="100"
    buySell="BUY"
    code="C"
    quantity="5"
    tradePrice="0.10"
    amount="50"
    proceeds="-50"
    netCash="-52.50"
    ibCommission="-2.50"
    tradeDate="18/03/2025"
    settleDate="19/03/2025"
    execID="import-test-btc-001"
    tradeID="987654322"
    orderID="12346"
    conid="654321"
    orderType="LMT"
    exchange="SMART"
    levelOfDetail="EXECUTION"
/>
</TradeConfirms>
</FlexStatement>
</FlexStatements>
</FlexStatementResponse>"""


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
        yield session
        session.close()
        engine.dispose()
    finally:
        for model, schema in original_schemas.items():
            model.__table__.schema = schema


class TestRunImport:
    def test_import_from_xml(self, db_session):
        """Import from provided XML text should parse and persist."""
        result = run_import(
            session=db_session,
            account_id="YOUR_ACCOUNT",
            xml_text=SAMPLE_FLEX_XML,
            run_matching=True,
        )

        assert result.total_xml_records == 2
        assert result.imported == 2
        assert result.skipped_duplicates == 0
        assert result.errors == 0
        assert result.matched_trades == 1  # STO + BTC matched

        # Verify import session
        sessions = db_session.query(ImportSession).all()
        assert len(sessions) == 1
        assert sessions[0].status == "completed"
        assert sessions[0].imported_records == 2

        # Verify raw imports
        imports = db_session.query(IBKRRawImport).all()
        assert len(imports) == 2
        assert imports[0].underlying_symbol == "AAPL"
        assert imports[0].ibkr_exec_id == "import-test-sto-001"

        # Verify raw_data is stored as dict
        assert isinstance(imports[0].raw_data, dict)
        assert imports[0].raw_data["tradePrice"] == "0.45"

    def test_dedup_on_reimport(self, db_session):
        """Re-importing the same XML should skip duplicates."""
        result1 = run_import(
            session=db_session,
            account_id="YOUR_ACCOUNT",
            xml_text=SAMPLE_FLEX_XML,
            run_matching=False,
        )
        assert result1.imported == 2

        result2 = run_import(
            session=db_session,
            account_id="YOUR_ACCOUNT",
            xml_text=SAMPLE_FLEX_XML,
            run_matching=False,
        )
        assert result2.imported == 0
        assert result2.skipped_duplicates == 2

        # Only 2 records total in DB
        assert db_session.query(IBKRRawImport).count() == 2

    def test_import_without_matching(self, db_session):
        """Import with run_matching=False should not match trades."""
        result = run_import(
            session=db_session,
            account_id="YOUR_ACCOUNT",
            xml_text=SAMPLE_FLEX_XML,
            run_matching=False,
        )

        assert result.imported == 2
        assert result.matched_trades == 0
        assert db_session.query(TradeMatchingLog).count() == 0

    def test_import_session_date_range(self, db_session):
        """Import session should track date range of imported records."""
        run_import(
            session=db_session,
            account_id="YOUR_ACCOUNT",
            xml_text=SAMPLE_FLEX_XML,
            run_matching=False,
        )

        sessions = db_session.query(ImportSession).all()
        assert sessions[0].date_range_start == date(2025, 2, 10)
        assert sessions[0].date_range_end == date(2025, 3, 18)

    def test_import_empty_xml(self, db_session):
        """Importing empty XML should succeed with 0 records."""
        empty_xml = '<?xml version="1.0"?><FlexStatementResponse></FlexStatementResponse>'
        result = run_import(
            session=db_session,
            account_id="YOUR_ACCOUNT",
            xml_text=empty_xml,
            run_matching=False,
        )

        assert result.total_xml_records == 0
        assert result.imported == 0
        assert result.errors == 0

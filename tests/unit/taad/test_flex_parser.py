"""Tests for TAAD Flex Query XML parser."""

import pytest
from datetime import date, datetime

from src.taad.flex_parser import (
    ParsedExecution,
    _extract_open_close,
    _parse_date,
    _parse_datetime,
    _parse_float,
    _parse_int,
    parse_flex_xml,
)


# ============================================================
# Date/time parsing helpers
# ============================================================


class TestParseDate:
    """Tests for _parse_date (DD/MM/YYYY format)."""

    def test_australian_format(self):
        assert _parse_date("15/03/2025") == date(2025, 3, 15)

    def test_iso_format_fallback(self):
        assert _parse_date("2025-03-15") == date(2025, 3, 15)

    def test_empty_string(self):
        assert _parse_date("") is None

    def test_invalid_date(self):
        assert _parse_date("not-a-date") is None

    def test_day_month_ordering(self):
        """Verify DD/MM not MM/DD — 25th of December, not December 25th."""
        result = _parse_date("25/12/2025")
        assert result == date(2025, 12, 25)

    def test_leading_zeros(self):
        assert _parse_date("01/01/2025") == date(2025, 1, 1)


class TestParseDatetime:
    def test_date_and_time(self):
        result = _parse_datetime("15/03/2025", "143022")
        assert result == datetime(2025, 3, 15, 14, 30, 22)

    def test_date_and_time_with_colons(self):
        result = _parse_datetime("15/03/2025", "14:30:22")
        assert result == datetime(2025, 3, 15, 14, 30, 22)

    def test_date_only(self):
        result = _parse_datetime("15/03/2025", "")
        assert result == datetime(2025, 3, 15, 0, 0, 0)

    def test_empty_date(self):
        assert _parse_datetime("", "143022") is None

    def test_iso_date_format(self):
        result = _parse_datetime("2025-03-15", "143022")
        assert result == datetime(2025, 3, 15, 14, 30, 22)


class TestParseFloat:
    def test_valid_float(self):
        assert _parse_float("1.23") == 1.23

    def test_empty(self):
        assert _parse_float("") is None

    def test_negative(self):
        assert _parse_float("-5.50") == -5.50

    def test_invalid(self):
        assert _parse_float("abc") is None


class TestParseInt:
    def test_valid_int(self):
        assert _parse_int("42") == 42

    def test_float_string(self):
        assert _parse_int("100.0") == 100

    def test_empty(self):
        assert _parse_int("") == 0

    def test_empty_with_default(self):
        assert _parse_int("", default=1) == 1

    def test_invalid(self):
        assert _parse_int("abc") == 0


class TestExtractOpenClose:
    def test_opening(self):
        assert _extract_open_close("O") == "O"

    def test_closing(self):
        assert _extract_open_close("C") == "C"

    def test_opening_with_partial(self):
        assert _extract_open_close("O;P") == "O"

    def test_closing_with_partial(self):
        assert _extract_open_close("C;P") == "C"

    def test_empty(self):
        assert _extract_open_close("") == ""

    def test_unknown_code(self):
        assert _extract_open_close("X;Y") == ""


# ============================================================
# Full XML parsing
# ============================================================

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
    tradeTime="143022"
    orderTime="142500"
    execID="0001f4e8.67a9b2c3.01.01"
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
    tradeTime="100530"
    orderTime="100000"
    execID="0001f4e8.67b1c2d3.01.01"
    tradeID="987654322"
    orderID="12346"
    conid="654321"
    orderType="LMT"
    exchange="SMART"
    levelOfDetail="EXECUTION"
/>
<TradeConfirm
    accountId="YOUR_ACCOUNT"
    symbol="MSFT"
    underlyingSymbol="MSFT"
    assetCategory="OPT"
    buySell="SELL"
    code="O"
    quantity="-3"
    tradePrice="0.80"
    tradeDate="10/02/2025"
    execID="summary123"
    levelOfDetail="ORDER"
/>
</TradeConfirms>
</FlexStatement>
</FlexStatements>
</FlexStatementResponse>"""


class TestParseFlexXml:
    def test_parses_execution_records_only(self):
        """Should parse EXECUTION records and skip ORDER/SYMBOL_SUMMARY."""
        results = parse_flex_xml(SAMPLE_FLEX_XML)
        assert len(results) == 2  # Only EXECUTION level

    def test_sto_record(self):
        results = parse_flex_xml(SAMPLE_FLEX_XML)
        sto = results[0]  # First record is STO

        assert sto.exec_id == "0001f4e8.67a9b2c3.01.01"
        assert sto.account_id == "YOUR_ACCOUNT"
        assert sto.underlying_symbol == "AAPL"
        assert sto.strike == 150.0
        assert sto.put_call == "P"
        assert sto.buy_sell == "SELL"
        assert sto.open_close == "O"
        assert sto.quantity == -5
        assert sto.price == 0.45
        assert sto.trade_date == date(2025, 2, 10)
        assert sto.expiry == date(2025, 3, 21)
        assert sto.multiplier == 100
        assert sto.commission == -2.50

    def test_btc_record(self):
        results = parse_flex_xml(SAMPLE_FLEX_XML)
        btc = results[1]  # Second record is BTC

        assert btc.exec_id == "0001f4e8.67b1c2d3.01.01"
        assert btc.buy_sell == "BUY"
        assert btc.open_close == "C"
        assert btc.quantity == 5
        assert btc.price == 0.10
        assert btc.trade_date == date(2025, 3, 18)

    def test_raw_data_preserved(self):
        results = parse_flex_xml(SAMPLE_FLEX_XML)
        sto = results[0]
        assert isinstance(sto.raw_data, dict)
        assert sto.raw_data["tradePrice"] == "0.45"
        assert sto.raw_data["buySell"] == "SELL"

    def test_dedup_by_exec_id(self):
        """Duplicate execIDs should be skipped."""
        xml_with_dupe = SAMPLE_FLEX_XML.replace(
            'execID="0001f4e8.67b1c2d3.01.01"',
            'execID="0001f4e8.67a9b2c3.01.01"',
        )
        results = parse_flex_xml(xml_with_dupe)
        assert len(results) == 1  # Second is duplicate

    def test_empty_xml(self):
        xml = """<?xml version="1.0"?><FlexStatementResponse></FlexStatementResponse>"""
        results = parse_flex_xml(xml)
        assert results == []

    def test_invalid_xml(self):
        with pytest.raises(ValueError, match="Failed to parse"):
            parse_flex_xml("not xml at all")

    def test_australian_date_format(self):
        """Verify dates are parsed as DD/MM/YYYY (Australian format)."""
        results = parse_flex_xml(SAMPLE_FLEX_XML)
        # 10/02/2025 should be February 10th, not October 2nd
        assert results[0].trade_date == date(2025, 2, 10)
        # 21/03/2025 expiry should be March 21st
        assert results[0].expiry == date(2025, 3, 21)

    def test_settle_date_parsed(self):
        results = parse_flex_xml(SAMPLE_FLEX_XML)
        assert results[0].settle_date == date(2025, 2, 11)

    def test_execution_time_parsed(self):
        results = parse_flex_xml(SAMPLE_FLEX_XML)
        assert results[0].execution_time == datetime(2025, 2, 10, 14, 30, 22)


# XML with Trades/Trade instead of TradeConfirms/TradeConfirm
SAMPLE_TRADES_XML = """<?xml version="1.0"?>
<FlexQueryResponse>
<FlexStatements>
<FlexStatement accountId="YOUR_ACCOUNT">
<Trades>
<Trade
    accountId="YOUR_ACCOUNT"
    symbol="TSLA  250228P00200000"
    underlyingSymbol="TSLA"
    assetCategory="OPT"
    putCall="P"
    strike="200"
    expiry="28/02/2025"
    multiplier="100"
    buySell="SELL"
    code="O"
    quantity="-2"
    tradePrice="1.20"
    tradeDate="05/02/2025"
    execID="trade-exec-001"
    levelOfDetail="EXECUTION"
/>
</Trades>
</FlexStatement>
</FlexStatements>
</FlexQueryResponse>"""


class TestParseTradesXml:
    def test_trades_element_path(self):
        """Parser should handle Trades/Trade XML structure."""
        results = parse_flex_xml(SAMPLE_TRADES_XML)
        assert len(results) == 1
        assert results[0].underlying_symbol == "TSLA"
        assert results[0].strike == 200.0


# ============================================================
# Activity Flex Query XML parsing
# ============================================================

SAMPLE_ACTIVITY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="Last_Quarter" type="AF">
<FlexStatements count="1">
<FlexStatement accountId="YOUR_ACCOUNT" toDate="20250211" whenGenerated="11/02/2025;10:30:00">
<Trades>
<Trade
    accountId="YOUR_ACCOUNT"
    acctAlias=""
    symbol="AAPL  250321P00150000"
    underlyingSymbol="AAPL"
    assetCategory="OPT"
    putCall="P"
    strike="150"
    expiry="21/03/2025"
    multiplier="100"
    buySell="SELL"
    openCloseIndicator="O"
    quantity="-5"
    tradePrice="0.45"
    amount="-225"
    proceeds="225"
    netCash="222.50"
    ibCommission="-2.50"
    tradeDate="10/02/2025"
    settleDate="11/02/2025"
    dateTime="10/02/2025;14:30:22"
    tradeID="987654321"
    ibExecID="0001f4e8.67a9b2c3.01.01"
    ibOrderID="12345"
    conid="654321"
    orderType="LMT"
    exchange="SMART"
    levelOfDetail="EXECUTION"
/>
<Trade
    accountId="YOUR_ACCOUNT"
    acctAlias=""
    symbol="AAPL  250321P00150000"
    underlyingSymbol="AAPL"
    assetCategory="OPT"
    putCall="P"
    strike="150"
    expiry="21/03/2025"
    multiplier="100"
    buySell="BUY"
    openCloseIndicator="C"
    quantity="5"
    tradePrice="0.10"
    amount="50"
    proceeds="-50"
    netCash="-52.50"
    ibCommission="-2.50"
    tradeDate="18/03/2025"
    settleDate="19/03/2025"
    dateTime="18/03/2025;10:05:30"
    tradeID="987654322"
    ibExecID="0001f4e8.67b1c2d3.01.01"
    ibOrderID="12346"
    conid="654321"
    orderType="LMT"
    exchange="SMART"
    levelOfDetail="EXECUTION"
/>
<Trade
    accountId="YOUR_ACCOUNT"
    symbol="AAPL  250321P00150000"
    underlyingSymbol="AAPL"
    assetCategory="OPT"
    buySell="SELL"
    openCloseIndicator="O"
    quantity="-5"
    tradePrice="0.45"
    tradeDate="10/02/2025"
    ibExecID="order-level-001"
    levelOfDetail="ORDER"
/>
</Trades>
</FlexStatement>
</FlexStatements>
</FlexQueryResponse>"""


class TestParseActivityFlexXml:
    """Tests for Activity Flex Query XML (type='AF') format."""

    def test_parses_execution_records_only(self):
        """Should parse EXECUTION records and skip ORDER level."""
        results = parse_flex_xml(SAMPLE_ACTIVITY_XML)
        assert len(results) == 2  # Only EXECUTION level, not ORDER

    def test_openCloseIndicator_open(self):
        """Activity XML uses openCloseIndicator='O' for STO."""
        results = parse_flex_xml(SAMPLE_ACTIVITY_XML)
        sto = results[0]
        assert sto.open_close == "O"
        assert sto.buy_sell == "SELL"

    def test_openCloseIndicator_close(self):
        """Activity XML uses openCloseIndicator='C' for BTC."""
        results = parse_flex_xml(SAMPLE_ACTIVITY_XML)
        btc = results[1]
        assert btc.open_close == "C"
        assert btc.buy_sell == "BUY"

    def test_ibExecID_used(self):
        """Activity XML uses ibExecID instead of execID."""
        results = parse_flex_xml(SAMPLE_ACTIVITY_XML)
        assert results[0].exec_id == "0001f4e8.67a9b2c3.01.01"
        assert results[1].exec_id == "0001f4e8.67b1c2d3.01.01"

    def test_trade_date_parsed_correctly(self):
        """tradeDate is in DD/MM/YYYY format."""
        results = parse_flex_xml(SAMPLE_ACTIVITY_XML)
        assert results[0].trade_date == date(2025, 2, 10)
        assert results[1].trade_date == date(2025, 3, 18)

    def test_full_record_fields(self):
        """Verify all key fields parsed from Activity XML."""
        results = parse_flex_xml(SAMPLE_ACTIVITY_XML)
        sto = results[0]
        assert sto.account_id == "YOUR_ACCOUNT"
        assert sto.underlying_symbol == "AAPL"
        assert sto.strike == 150.0
        assert sto.put_call == "P"
        assert sto.expiry == date(2025, 3, 21)
        assert sto.multiplier == 100
        assert sto.quantity == -5
        assert sto.price == 0.45
        assert sto.commission == -2.50
        assert sto.proceeds == 225.0

    def test_dedup_by_ibExecID(self):
        """Duplicate ibExecIDs across overlapping imports should be deduped."""
        xml_with_dupe = SAMPLE_ACTIVITY_XML.replace(
            'ibExecID="0001f4e8.67b1c2d3.01.01"',
            'ibExecID="0001f4e8.67a9b2c3.01.01"',
        )
        results = parse_flex_xml(xml_with_dupe)
        assert len(results) == 1  # Second EXECUTION is a duplicate


# Test Activity XML with only dateTime (no tradeDate)
SAMPLE_ACTIVITY_DATETIME_ONLY_XML = """<?xml version="1.0"?>
<FlexQueryResponse queryName="Test" type="AF">
<FlexStatements count="1">
<FlexStatement accountId="YOUR_ACCOUNT">
<Trades>
<Trade
    accountId="YOUR_ACCOUNT"
    underlyingSymbol="MSFT"
    assetCategory="OPT"
    putCall="P"
    strike="400"
    expiry="28/02/2025"
    multiplier="100"
    buySell="SELL"
    openCloseIndicator="O"
    quantity="-3"
    tradePrice="1.20"
    dateTime="15/01/2025;09:45:00"
    ibExecID="dt-only-exec-001"
    levelOfDetail="EXECUTION"
/>
</Trades>
</FlexStatement>
</FlexStatements>
</FlexQueryResponse>"""


class TestActivityDateTimeFallback:
    """Test dateTime semicolon parsing when tradeDate is absent."""

    def test_datetime_semicolon_extracts_date(self):
        """When tradeDate is absent, dateTime='DD/MM/YYYY;HH:MM:SS' should work."""
        results = parse_flex_xml(SAMPLE_ACTIVITY_DATETIME_ONLY_XML)
        assert len(results) == 1
        # 15/01/2025 = January 15, 2025
        assert results[0].trade_date == date(2025, 1, 15)

    def test_datetime_record_fields(self):
        """Verify other fields work with dateTime-only record."""
        results = parse_flex_xml(SAMPLE_ACTIVITY_DATETIME_ONLY_XML)
        rec = results[0]
        assert rec.underlying_symbol == "MSFT"
        assert rec.open_close == "O"
        assert rec.price == 1.20


# Test code attribute fallback when openCloseIndicator is absent
SAMPLE_CODE_FALLBACK_XML = """<?xml version="1.0"?>
<FlexQueryResponse>
<FlexStatements count="1">
<FlexStatement accountId="YOUR_ACCOUNT">
<Trades>
<Trade
    accountId="YOUR_ACCOUNT"
    underlyingSymbol="GOOG"
    assetCategory="OPT"
    putCall="P"
    strike="170"
    expiry="28/02/2025"
    multiplier="100"
    buySell="SELL"
    code="O;P"
    quantity="-2"
    tradePrice="0.60"
    tradeDate="10/02/2025"
    ibExecID="code-fallback-001"
    levelOfDetail="EXECUTION"
/>
</Trades>
</FlexStatement>
</FlexStatements>
</FlexQueryResponse>"""


class TestCodeFallback:
    def test_code_attribute_used_when_no_openCloseIndicator(self):
        """When openCloseIndicator is absent, should fall back to code='O;P'."""
        results = parse_flex_xml(SAMPLE_CODE_FALLBACK_XML)
        assert len(results) == 1
        assert results[0].open_close == "O"

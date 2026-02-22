"""Parse IBKR Flex Query XML into structured records.

Key rules:
- Only parse levelOfDetail="EXECUTION" records (skip ORDER, SYMBOL_SUMMARY)
- Date format: DD/MM/YYYY (Australian locale)
- Dedup key: execID
- code field → open/close: O = Opening, C = Closing
- buySell + code → SELL+O = entry (STO), BUY+C = exit (BTC)
- Store full raw XML attributes as dict for JSONB storage
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from src.utils.timezone import us_trading_date

from loguru import logger


@dataclass
class ParsedExecution:
    """A single parsed execution record from a Flex Query."""

    # Identity
    exec_id: str
    trade_id: str
    order_id: str
    conid: str

    # Account
    account_id: str
    account_alias: str

    # Instrument
    symbol: str  # full option symbol
    underlying_symbol: str
    asset_category: str  # OPT, STK, etc.
    put_call: str  # P or C (empty for stocks)
    strike: float | None
    expiry: date | None
    multiplier: int

    # Trade
    buy_sell: str  # BUY or SELL
    open_close: str  # O or C (derived from code field)
    quantity: int
    price: float
    amount: float | None  # tradePrice * quantity * multiplier or None
    proceeds: float | None
    net_cash: float | None
    commission: float | None

    # Dates
    trade_date: date
    settle_date: date | None
    order_time: datetime | None
    execution_time: datetime | None

    # Metadata
    order_type: str
    exchange: str
    level_of_detail: str

    # Complete raw attributes dict (for JSONB storage)
    raw_data: dict = field(default_factory=dict)


# Date/time parsing helpers for DD/MM/YYYY format

def _parse_date(value: str) -> date | None:
    """Parse DD/MM/YYYY date string."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError:
        # Try ISO format as fallback
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            logger.warning(f"Could not parse date: {value!r}")
            return None


def _parse_datetime(date_str: str, time_str: str) -> datetime | None:
    """Parse date + time strings into datetime.

    IBKR may provide date as DD/MM/YYYY and time as HH:MM:SS or HHMMSS.
    """
    if not date_str:
        return None
    if not time_str:
        # Return just the date at midnight
        d = _parse_date(date_str)
        return datetime(d.year, d.month, d.day) if d else None

    # Normalise time string — remove colons if present
    time_clean = time_str.replace(":", "")
    if len(time_clean) == 6:
        time_fmt = "%H%M%S"
    else:
        time_fmt = "%H%M%S"
        time_clean = time_clean[:6]  # truncate fractional seconds

    for date_fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(f"{date_str} {time_clean}", f"{date_fmt} {time_fmt}")
        except ValueError:
            continue

    logger.warning(f"Could not parse datetime: {date_str!r} {time_str!r}")
    return None


def _parse_float(value: str) -> float | None:
    """Parse a float, returning None for empty strings."""
    if not value:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _parse_int(value: str, default: int = 0) -> int:
    """Parse an int, returning default for empty strings."""
    if not value:
        return default
    try:
        return int(float(value))  # handle "100.0" → 100
    except (ValueError, TypeError):
        return default


def _extract_open_close(code: str) -> str:
    """Extract open/close from the 'code' attribute.

    IBKR code field contains semicolon-separated codes like "O;P" or "C;P".
    O = Opening, C = Closing.
    """
    if not code:
        return ""
    parts = [p.strip().upper() for p in code.split(";")]
    if "O" in parts:
        return "O"
    if "C" in parts:
        return "C"
    return ""


def parse_flex_xml(xml_text: str) -> list[ParsedExecution]:
    """Parse Flex Query XML and extract EXECUTION-level trade records.

    Args:
        xml_text: Raw XML string from the Flex Query response.

    Returns:
        List of ParsedExecution records (only EXECUTION level).

    Raises:
        ValueError: If XML is malformed or contains no data.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise ValueError(f"Failed to parse Flex Query XML: {e}") from e

    executions: list[ParsedExecution] = []
    seen_exec_ids: set[str] = set()

    # Navigate the XML tree — Flex Query XML structure:
    # <FlexQueryResponse> or <FlexStatementResponse>
    #   <FlexStatements>
    #     <FlexStatement>
    #       <TradeConfirms> or <Trades>
    #         <TradeConfirm ...attributes... /> or <Trade .../>

    # Try multiple possible element paths
    trade_elements: list[ET.Element] = []

    # Path 1: TradeConfirms/TradeConfirm (Trade Confirmation Flex Query)
    for tc in root.iter("TradeConfirm"):
        trade_elements.append(tc)

    # Path 2: Trades/Trade (Activity Flex Query)
    if not trade_elements:
        for t in root.iter("Trade"):
            trade_elements.append(t)

    # Path 3: Order elements
    if not trade_elements:
        for o in root.iter("Order"):
            trade_elements.append(o)

    if not trade_elements:
        logger.warning("No trade elements found in Flex Query XML")
        return []

    logger.info(f"Found {len(trade_elements)} total trade elements in XML")

    for elem in trade_elements:
        attrs = dict(elem.attrib)

        # Filter: only EXECUTION level records
        level = attrs.get("levelOfDetail", "")
        if level and level != "EXECUTION":
            continue

        # Dedup by execID
        exec_id = attrs.get("execID", attrs.get("ibExecID", ""))
        if not exec_id:
            logger.debug(f"Skipping element without execID: {attrs.get('symbol', '?')}")
            continue

        if exec_id in seen_exec_ids:
            logger.debug(f"Skipping duplicate execID: {exec_id}")
            continue
        seen_exec_ids.add(exec_id)

        # Extract open/close — Activity XML uses openCloseIndicator directly,
        # Trade Confirmation XML uses semicolon-delimited code field (e.g., "O;P")
        open_close = attrs.get("openCloseIndicator", "").strip().upper()
        if not open_close:
            open_close = _extract_open_close(attrs.get("code", ""))

        # Build the parsed record
        # Activity XML dateTime may be "DD/MM/YYYY;HH:MM:SS" — extract date part only
        trade_date_str = attrs.get("tradeDate", "")
        if not trade_date_str:
            dt_raw = attrs.get("dateTime", "")
            trade_date_str = dt_raw.split(";")[0] if dt_raw else ""
        settle_date_str = attrs.get("settleDate", "")

        # Parse expiry — could be "lastTradingDay" or "expiry"
        expiry_str = attrs.get("expiry", attrs.get("lastTradingDay", ""))

        execution = ParsedExecution(
            exec_id=exec_id,
            trade_id=attrs.get("tradeID", ""),
            order_id=attrs.get("orderID", attrs.get("ibOrderID", "")),
            conid=attrs.get("conid", ""),
            account_id=attrs.get("accountId", ""),
            account_alias=attrs.get("acctAlias", ""),
            symbol=attrs.get("symbol", ""),
            underlying_symbol=attrs.get("underlyingSymbol", attrs.get("symbol", "")),
            asset_category=attrs.get("assetCategory", attrs.get("assetType", "")),
            put_call=attrs.get("putCall", ""),
            strike=_parse_float(attrs.get("strike", "")),
            expiry=_parse_date(expiry_str),
            multiplier=_parse_int(attrs.get("multiplier", ""), default=100),
            buy_sell=attrs.get("buySell", ""),
            open_close=open_close,
            quantity=_parse_int(attrs.get("quantity", ""), default=0),
            price=float(attrs.get("tradePrice", attrs.get("price", "0"))),
            amount=_parse_float(attrs.get("amount", attrs.get("tradeMoney", ""))),
            proceeds=_parse_float(attrs.get("proceeds", "")),
            net_cash=_parse_float(attrs.get("netCash", "")),
            commission=_parse_float(attrs.get("ibCommission", attrs.get("commission", ""))),
            trade_date=_parse_date(trade_date_str) or us_trading_date(),
            settle_date=_parse_date(settle_date_str),
            order_time=_parse_datetime(
                attrs.get("orderTime", trade_date_str),
                attrs.get("orderTime", "").split(";")[-1] if ";" in attrs.get("orderTime", "") else "",
            ),
            execution_time=_parse_datetime(trade_date_str, attrs.get("tradeTime", "")),
            order_type=attrs.get("orderType", ""),
            exchange=attrs.get("exchange", attrs.get("listingExchange", "")),
            level_of_detail=level or "EXECUTION",
            raw_data=attrs,
        )

        executions.append(execution)

    logger.info(
        f"Parsed {len(executions)} EXECUTION records "
        f"(deduped from {len(seen_exec_ids)} unique execIDs)"
    )
    return executions

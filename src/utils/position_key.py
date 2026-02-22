"""Canonical position key construction.

Provides a single source of truth for building position keys and trade IDs
across the system. Resolves mismatches between IBKR format (right="P") and
database format (option_type="PUT") by normalizing to single-char P/C.
"""

from datetime import date


def _normalize_right(value: str) -> str:
    """Normalize PUT/CALL/P/C to single-char P/C.

    Args:
        value: Option right/type string in any format

    Returns:
        Single character "P" or "C", or original value uppercased if unrecognized
    """
    v = str(value).upper().strip()
    if v in ("PUT", "P"):
        return "P"
    if v in ("CALL", "C"):
        return "C"
    return v


def _normalize_expiration(exp) -> str:
    """Normalize date/str to YYYYMMDD string.

    Args:
        exp: Expiration as date object or string (YYYY-MM-DD or YYYYMMDD)

    Returns:
        YYYYMMDD string
    """
    if isinstance(exp, date):
        return exp.strftime("%Y%m%d")
    s = str(exp).replace("-", "")
    return s[:8]


def canonical_position_key(symbol: str, strike: float, expiration, right: str) -> str:
    """Build canonical position key: SYMBOL_STRIKE_YYYYMMDD_P/C.

    This is the single format used across the entire system for matching
    IBKR positions to database trades.

    Args:
        symbol: Stock symbol
        strike: Option strike price
        expiration: Expiration date (date object or string)
        right: Option right/type ("PUT", "P", "CALL", "C")

    Returns:
        Canonical key string
    """
    return (
        f"{symbol}_{float(strike)}_"
        f"{_normalize_expiration(expiration)}_{_normalize_right(right)}"
    )


def position_key_from_contract(contract) -> str:
    """Build canonical position key from IBKR contract object.

    Args:
        contract: IBKR contract with symbol, strike,
                  lastTradeDateOrContractMonth, right attributes

    Returns:
        Canonical key string
    """
    return canonical_position_key(
        contract.symbol,
        contract.strike,
        contract.lastTradeDateOrContractMonth,
        contract.right,
    )


def position_key_from_trade(trade) -> str:
    """Build canonical position key from database Trade object.

    Args:
        trade: Database trade with symbol, strike, expiration, option_type

    Returns:
        Canonical key string
    """
    return canonical_position_key(
        trade.symbol,
        trade.strike,
        trade.expiration,
        trade.option_type,
    )


def generate_trade_id(
    symbol: str,
    strike: float,
    expiration,
    right: str,
    order_id: int | None = None,
    suffix: str = "",
) -> str:
    """Generate standardized trade_id: canonical_key[_orderId|_suffix].

    Args:
        symbol: Stock symbol
        strike: Option strike price
        expiration: Expiration date
        right: Option right/type
        order_id: IBKR order ID (if available)
        suffix: Custom suffix (e.g., "imported")

    Returns:
        Standardized trade_id string
    """
    key = canonical_position_key(symbol, strike, expiration, right)
    if order_id:
        return f"{key}_{order_id}"
    if suffix:
        return f"{key}_{suffix}"
    return key

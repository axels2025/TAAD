"""NaN-safe ticker data extraction helpers.

IBKR's reqMktData(snapshot=True) returns NaN for indices (VIX),
pre-market, and illiquid options. These helpers centralize the
NaN guard logic so callers don't need to repeat it.
"""

import math


def _is_valid_price(value) -> bool:
    """Check if a ticker value is usable (not None, not NaN, positive).

    Args:
        value: Raw ticker field value

    Returns:
        True if the value is a valid, positive number
    """
    return (
        value is not None
        and isinstance(value, (int, float))
        and not math.isnan(value)
        and value > 0
    )


def safe_price(ticker) -> float | None:
    """Extract best available price from ticker: last -> mid -> close.

    Args:
        ticker: ib_insync Ticker object

    Returns:
        Best available price, or None if no valid data
    """
    if _is_valid_price(ticker.last):
        return ticker.last
    if _is_valid_price(ticker.bid) and _is_valid_price(ticker.ask):
        return (ticker.bid + ticker.ask) / 2
    if _is_valid_price(ticker.close):
        return ticker.close
    return None


def safe_bid_ask(ticker) -> tuple[float | None, float | None]:
    """Extract NaN-safe bid/ask from ticker.

    Args:
        ticker: ib_insync Ticker object

    Returns:
        Tuple of (bid, ask) — either may be None if invalid
    """
    bid = ticker.bid if _is_valid_price(ticker.bid) else None
    ask = ticker.ask if _is_valid_price(ticker.ask) else None
    return bid, ask


def safe_field(ticker, field: str) -> float | None:
    """Extract any single NaN-safe field from ticker.

    Unlike _is_valid_price, this allows zero and negative values
    (useful for volume=0, negative Greeks, etc.) — it only rejects
    None and NaN.

    Args:
        ticker: ib_insync Ticker object
        field: Attribute name (e.g. "volume", "openInterest", "open")

    Returns:
        Field value, or None if missing or NaN
    """
    value = getattr(ticker, field, None)
    if value is not None and isinstance(value, (int, float)) and not math.isnan(value):
        return value
    return None

"""Option math utilities — direction-aware OTM/ITM calculations.

Centralises the OTM and ITM formulas so every module uses the same
logic for both PUTs and CALLs.  Before this module existed, ~13
locations had hardcoded put-only math that gave wrong results for
short call positions.

Usage:
    >>> from src.utils.option_math import calc_otm_pct, is_itm
    >>> calc_otm_pct(stock_price=120.0, strike=140.0, option_type="PUT")
    0.1667  # 16.67% OTM (stock well above strike)
    >>> calc_otm_pct(stock_price=120.0, strike=140.0, option_type="CALL")
    -0.1667  # 16.67% ITM (stock below strike) — negative = ITM
"""


def _normalise_right(option_type: str) -> str:
    """Normalise option type to 'P' or 'C'."""
    v = str(option_type).upper().strip()
    if v in ("PUT", "P"):
        return "P"
    if v in ("CALL", "C"):
        return "C"
    raise ValueError(f"Unknown option_type: {option_type!r}")


def calc_otm_pct(
    stock_price: float, strike: float, option_type: str
) -> float:
    """Calculate OTM percentage — positive means out-of-the-money.

    For PUTs:  OTM% = (stock_price - strike) / stock_price
    For CALLs: OTM% = (strike - stock_price) / stock_price

    Returns:
        Positive float when OTM, negative when ITM, zero when ATM.
        Returns 0.0 if stock_price <= 0.
    """
    if stock_price <= 0:
        return 0.0
    right = _normalise_right(option_type)
    if right == "P":
        return (stock_price - strike) / stock_price
    else:
        return (strike - stock_price) / stock_price


def calc_otm_dollars(
    stock_price: float, strike: float, option_type: str
) -> float:
    """Calculate OTM in dollar terms — positive means out-of-the-money.

    For PUTs:  stock_price - strike
    For CALLs: strike - stock_price
    """
    right = _normalise_right(option_type)
    if right == "P":
        return stock_price - strike
    else:
        return strike - stock_price


def is_itm(
    stock_price: float, strike: float, option_type: str
) -> bool:
    """Whether the option is in-the-money.

    PUT is ITM when stock < strike.
    CALL is ITM when stock > strike.
    """
    return calc_otm_pct(stock_price, strike, option_type) < 0


def max_otm_strike(
    stock_price: float, min_otm_pct: float, option_type: str
) -> float:
    """Calculate the strike boundary for minimum OTM%.

    For PUTs:  max strike = stock * (1 - min_otm)  (below stock)
    For CALLs: min strike = stock * (1 + min_otm)  (above stock)

    Returns the *boundary* strike — for puts this is the ceiling,
    for calls this is the floor.
    """
    right = _normalise_right(option_type)
    if right == "P":
        return stock_price * (1 - min_otm_pct)
    else:
        return stock_price * (1 + min_otm_pct)


def is_otm_strike(
    stock_price: float, strike: float, min_otm_pct: float, option_type: str
) -> bool:
    """Check if a strike is sufficiently OTM.

    For PUTs:  strike <= stock * (1 - min_otm)
    For CALLs: strike >= stock * (1 + min_otm)
    """
    right = _normalise_right(option_type)
    if right == "P":
        return strike <= stock_price * (1 - min_otm_pct)
    else:
        return strike >= stock_price * (1 + min_otm_pct)

"""Safe P&L calculation and formatting helpers.

Guards against None values in entry_premium, exit_premium, and contracts
that can arise from imported trades or partial data.
"""


def calc_pnl(entry_premium, exit_premium, contracts) -> float:
    """Calculate P&L with None-safe arithmetic.

    Args:
        entry_premium: Premium received at entry (may be None)
        exit_premium: Premium paid at exit (may be None)
        contracts: Number of contracts (may be None)

    Returns:
        Profit/loss in dollars
    """
    entry = entry_premium or 0.0
    exit_ = exit_premium or 0.0
    qty = contracts or 0
    return (entry - exit_) * qty * 100


def calc_pnl_pct(profit_loss, entry_premium, contracts) -> float:
    """Calculate P&L percentage with zero-division guard.

    Args:
        profit_loss: Dollar P&L
        entry_premium: Premium received at entry (may be None)
        contracts: Number of contracts (may be None)

    Returns:
        P&L as a fraction (e.g. 0.5 = 50%)
    """
    denom = (entry_premium or 0.0) * (contracts or 0) * 100
    return profit_loss / denom if denom > 0 else 0.0


def fmt_pct(val, decimals=1) -> str:
    """Format a value as a percentage string, None-safe.

    Args:
        val: Value to format (may be None). 0.05 → "5.0%"
        decimals: Number of decimal places

    Returns:
        Formatted percentage string, or "N/A" if val is None
    """
    if val is None:
        return "N/A"
    return f"{val:.{decimals}%}"

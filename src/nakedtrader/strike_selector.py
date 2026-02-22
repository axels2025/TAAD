"""Delta-based strike selection for NakedTrader.

Selects the best put strike from an option chain based on delta target,
premium minimum, and configurable boundaries. Implements the mechanical
delta-targeting approach from the WealthyOption/BigERN system.
"""

from dataclasses import dataclass

from loguru import logger

from src.nakedtrader.chain import ChainResult, OptionQuote
from src.nakedtrader.config import NakedTraderConfig


@dataclass
class StrikeSelection:
    """Result of strike selection."""

    quote: OptionQuote
    symbol: str
    underlying_price: float
    trading_class: str
    profit_take_price: float
    stop_loss_price: float | None


def select_strike(
    chain: ChainResult,
    config: NakedTraderConfig,
) -> StrikeSelection | None:
    """Select the best strike from an option chain.

    Logic:
    1. Filter to puts where delta_min <= abs(delta) <= delta_max
    2. Filter to bid >= premium.min
    3. Sort by distance from delta_target (closest first)
    4. If no strikes match: widen delta range by 0.02 once, retry
    5. If still none: return None with descriptive logging
    6. Calculate profit-take and optional stop-loss prices

    Args:
        chain: Option chain with Greeks from chain.py.
        config: NakedTrader configuration.

    Returns:
        StrikeSelection with best strike, or None if no suitable strike found.
    """
    if chain.error:
        logger.warning(f"Chain error for {chain.symbol}: {chain.error}")
        return None

    if not chain.quotes:
        logger.warning(f"No quotes available for {chain.symbol} {chain.expiration}")
        return None

    delta_min = config.strike.delta_min
    delta_max = config.strike.delta_max
    delta_target = config.strike.delta_target
    premium_min = config.premium.min

    # First pass: strict delta + premium filter
    candidates = _filter_candidates(chain.quotes, delta_min, delta_max, premium_min)

    # Widen delta range by 0.02 once if no candidates
    if not candidates:
        widened_min = max(0.0, delta_min - 0.02)
        widened_max = min(1.0, delta_max + 0.02)
        logger.info(
            f"No strikes in delta [{delta_min:.3f}, {delta_max:.3f}], "
            f"widening to [{widened_min:.3f}, {widened_max:.3f}]"
        )
        candidates = _filter_candidates(chain.quotes, widened_min, widened_max, premium_min)

    if not candidates:
        _log_available_strikes(chain, premium_min)
        return None

    # Sort by distance from target delta
    candidates.sort(key=lambda q: abs(q.delta - delta_target))
    best = candidates[0]

    # Calculate exit prices
    profit_take_price = _calc_profit_take(best.bid, config)
    stop_loss_price = _calc_stop_loss(best.bid, config)

    logger.info(
        f"Selected {chain.symbol} ${best.strike} "
        f"(delta={best.delta:.3f}, bid=${best.bid:.2f}, "
        f"OTM={best.otm_pct:.1%}, DTE={best.dte}) "
        f"PT=${profit_take_price:.2f}"
        + (f" SL=${stop_loss_price:.2f}" if stop_loss_price else "")
    )

    return StrikeSelection(
        quote=best,
        symbol=chain.symbol,
        underlying_price=chain.underlying_price,
        trading_class=chain.trading_class,
        profit_take_price=profit_take_price,
        stop_loss_price=stop_loss_price,
    )


def _filter_candidates(
    quotes: list[OptionQuote],
    delta_min: float,
    delta_max: float,
    premium_min: float,
) -> list[OptionQuote]:
    """Filter quotes by delta range and minimum premium.

    Args:
        quotes: Available option quotes.
        delta_min: Minimum absolute delta.
        delta_max: Maximum absolute delta.
        premium_min: Minimum bid premium.

    Returns:
        Filtered list of qualifying quotes.
    """
    return [
        q for q in quotes
        if delta_min <= q.delta <= delta_max and q.bid >= premium_min
    ]


def _calc_profit_take(premium: float, config: NakedTraderConfig) -> float:
    """Calculate profit-take buy-to-close price.

    Profit-take = max(premium * (1 - profit_target_pct), profit_target_floor)
    e.g. max(0.50 * 0.30, 0.10) = max(0.15, 0.10) = 0.15

    Args:
        premium: Entry premium (bid price).
        config: NakedTrader configuration.

    Returns:
        Profit-take limit price for BTC order.
    """
    target_price = premium * (1.0 - config.exit.profit_target_pct)
    return round(max(target_price, config.exit.profit_target_floor), 2)


def _calc_stop_loss(premium: float, config: NakedTraderConfig) -> float | None:
    """Calculate stop-loss price if enabled.

    Stop-loss = premium * stop_loss_multiplier
    e.g. 0.50 * 3.0 = 1.50

    Args:
        premium: Entry premium (bid price).
        config: NakedTrader configuration.

    Returns:
        Stop-loss limit price, or None if stop-loss disabled.
    """
    if not config.exit.stop_loss_enabled:
        return None
    return round(premium * config.exit.stop_loss_multiplier, 2)


def _log_available_strikes(chain: ChainResult, premium_min: float) -> None:
    """Log available strikes when no candidates match, for debugging.

    Args:
        chain: The option chain that was searched.
        premium_min: Minimum premium requirement.
    """
    if not chain.quotes:
        logger.warning(f"No Greek data at all for {chain.symbol} {chain.expiration}")
        return

    deltas = [(q.strike, q.delta, q.bid) for q in chain.quotes]
    logger.warning(
        f"No strikes match criteria for {chain.symbol} {chain.expiration}. "
        f"Available (strike, delta, bid): "
        + ", ".join(f"${s:.0f} d={d:.3f} ${b:.2f}" for s, d, b in deltas[:10])
        + (f" ... ({len(deltas)} total)" if len(deltas) > 10 else "")
    )
    below_premium = [q for q in chain.quotes if q.bid < premium_min]
    if below_premium:
        logger.warning(
            f"{len(below_premium)} strikes rejected for bid < ${premium_min:.2f}"
        )

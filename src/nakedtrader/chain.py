"""Index option chain retrieval with Greeks for SPX/XSP/SPY.

Retrieves option chains from IBKR for index underlyings, fetches live
Greeks (delta) for candidate strikes, and returns structured data for
strike selection.
"""

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from loguru import logger

from src.nakedtrader.config import NakedTraderConfig
from src.tools.ibkr_client import IBKRClient
from src.utils.market_data import safe_field

ET = ZoneInfo("America/New_York")

# Preferred trading classes for index options (tried in order)
# SPX dailies/weeklies use SPXW; XSP may use XSPW or XSP depending on IBKR
TRADING_CLASS_PREFERENCES = {
    "SPX": ["SPXW", "SPX"],
    "XSP": ["XSPW", "XSP"],
    "SPY": ["SPY"],
}

# Contract type: index vs stock
INDEX_SYMBOLS = {"SPX", "XSP"}


@dataclass
class OptionQuote:
    """A single option strike with market data and Greeks."""

    strike: float
    delta: float
    bid: float
    ask: float
    mid: float
    iv: float | None = None
    gamma: float | None = None
    theta: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    expiration: str = ""  # YYYYMMDD
    dte: int = 0
    otm_pct: float = 0.0


@dataclass
class ChainResult:
    """Result of option chain retrieval."""

    symbol: str
    underlying_price: float
    expiration: str  # YYYYMMDD
    dte: int
    quotes: list[OptionQuote]
    trading_class: str = ""
    error: str | None = None


def _resolve_trading_class(symbol: str, chains: list) -> tuple[str, object]:
    """Find the best trading class from IBKR chain definitions.

    Tries preferred classes in order, then falls back to the chain with
    the most expirations (likely the weekly/daily chain).

    Args:
        symbol: Underlying symbol.
        chains: List of chain definitions from reqSecDefOptParams.

    Returns:
        Tuple of (trading_class, chain_definition).
    """
    available = {c.tradingClass: c for c in chains}
    logger.debug(
        f"{symbol}: IBKR returned {len(chains)} chain(s): "
        + ", ".join(
            f"{c.tradingClass} ({len(c.expirations)} exp, {len(c.strikes)} strikes)"
            for c in chains
        )
    )

    # Try preferred classes in order
    preferences = TRADING_CLASS_PREFERENCES.get(symbol, [symbol])
    for tc in preferences:
        if tc in available:
            logger.debug(f"{symbol}: Using preferred trading class {tc}")
            return tc, available[tc]

    # Fall back to chain with most expirations (likely weekly/daily)
    best = max(chains, key=lambda c: len(c.expirations))
    logger.info(
        f"{symbol}: No preferred class found in {list(available.keys())}, "
        f"using {best.tradingClass} ({len(best.expirations)} expirations)"
    )
    return best.tradingClass, best


def get_valid_expirations(
    client: IBKRClient,
    symbol: str,
    config: NakedTraderConfig,
) -> list[tuple[str, int]]:
    """Get valid expirations within DTE range for the symbol.

    Uses reqSecDefOptParams to discover available option chains, then
    filters to expirations within the configured DTE range.

    Args:
        client: Connected IBKR client.
        symbol: Underlying symbol (SPX, XSP, SPY).
        config: NakedTrader configuration.

    Returns:
        List of (expiration_YYYYMMDD, dte) tuples sorted by DTE ascending.
    """
    # Get the underlying contract
    if symbol in INDEX_SYMBOLS:
        underlying = client.get_index_contract(symbol)
    else:
        underlying = client.get_stock_contract(symbol)

    qualified = client.qualify_contract(underlying)
    if not qualified:
        logger.error(f"Could not qualify underlying contract for {symbol}")
        return []

    # Get option chain definitions
    sec_type = "IND" if symbol in INDEX_SYMBOLS else "STK"
    chains = client.ib.reqSecDefOptParams(
        symbol, "", sec_type, qualified.conId
    )

    if not chains:
        logger.error(f"No option chains found for {symbol}")
        return []

    # Find the best trading class
    _, target_chain = _resolve_trading_class(symbol, chains)

    # Filter expirations to DTE range
    today = datetime.now(ET).date()
    valid_exps: list[tuple[str, int]] = []

    for exp_str in sorted(target_chain.expirations):
        exp_date = date(int(exp_str[:4]), int(exp_str[4:6]), int(exp_str[6:8]))
        dte = (exp_date - today).days

        if config.dte.min <= dte <= config.dte.max:
            valid_exps.append((exp_str, dte))

    if config.dte.prefer_shortest:
        valid_exps.sort(key=lambda x: x[1])

    return valid_exps


def get_underlying_price(client: IBKRClient, symbol: str) -> float | None:
    """Get the current price of the underlying index or stock.

    Args:
        client: Connected IBKR client.
        symbol: Underlying symbol.

    Returns:
        Current price or None if unavailable.
    """
    if symbol in INDEX_SYMBOLS:
        contract = client.get_index_contract(symbol)
    else:
        contract = client.get_stock_contract(symbol)

    qualified = client.qualify_contract(contract)
    if not qualified:
        return None

    data = client.get_market_data(qualified)
    if data and data.get("last"):
        return data["last"]
    if data and data.get("close"):
        return data["close"]
    return None


def get_chain_with_greeks(
    client: IBKRClient,
    symbol: str,
    expiration: str,
    underlying_price: float,
    config: NakedTraderConfig,
) -> ChainResult:
    """Retrieve put option chain with live Greeks for a single expiration.

    Builds option contracts for OTM put strikes near the estimated delta
    zone, requests market data with Greeks, and returns structured quotes.

    Args:
        client: Connected IBKR client.
        symbol: Underlying symbol (SPX, XSP, SPY).
        expiration: Expiration date in YYYYMMDD format.
        underlying_price: Current underlying price.
        config: NakedTrader configuration.

    Returns:
        ChainResult with quotes sorted by strike descending (closest to ATM first).
    """
    trading_class = TRADING_CLASS_PREFERENCES.get(symbol, [symbol])[0]  # Initial guess
    exp_date = date(int(expiration[:4]), int(expiration[4:6]), int(expiration[6:8]))
    today = datetime.now(ET).date()
    dte = (exp_date - today).days

    # Estimate strike range based on delta targets
    # At delta ~0.05-0.12, strikes are roughly 2-6% OTM for short-dated options
    lower_bound = underlying_price * 0.90  # 10% OTM (well beyond our range)
    upper_bound = underlying_price * 0.99  # 1% OTM (closer than we want)

    # Get available strikes from chain definition
    if symbol in INDEX_SYMBOLS:
        underlying = client.get_index_contract(symbol)
    else:
        underlying = client.get_stock_contract(symbol)

    qualified_underlying = client.qualify_contract(underlying)
    if not qualified_underlying:
        return ChainResult(
            symbol=symbol,
            underlying_price=underlying_price,
            expiration=expiration,
            dte=dte,
            quotes=[],
            error=f"Could not qualify {symbol} underlying",
        )

    sec_type = "IND" if symbol in INDEX_SYMBOLS else "STK"
    chains = client.ib.reqSecDefOptParams(
        symbol, "", sec_type, qualified_underlying.conId
    )

    # Find strikes — resolve actual trading class from IBKR
    available_strikes: list[float] = []
    if chains:
        trading_class, resolved_chain = _resolve_trading_class(symbol, chains)
        if expiration in resolved_chain.expirations:
            available_strikes = sorted(resolved_chain.strikes)
        else:
            # Expiration not in resolved chain — search all chains
            for chain in chains:
                if expiration in chain.expirations:
                    trading_class = chain.tradingClass
                    available_strikes = sorted(chain.strikes)
                    logger.debug(
                        f"{symbol}: Found exp {expiration} in {trading_class}"
                    )
                    break

    if not available_strikes:
        avail_tcs = [c.tradingClass for c in chains] if chains else []
        return ChainResult(
            symbol=symbol,
            underlying_price=underlying_price,
            expiration=expiration,
            dte=dte,
            quotes=[],
            trading_class=trading_class,
            error=f"No strikes for {symbol} exp {expiration} "
                  f"(chains: {avail_tcs})",
        )

    # Filter to OTM puts in our estimated range
    candidate_strikes = [
        s for s in available_strikes
        if lower_bound <= s <= upper_bound
    ]

    # Take ~15 strikes nearest to our estimated zone
    if len(candidate_strikes) > 15:
        # Focus on the higher end (closer to ATM = higher delta)
        candidate_strikes = candidate_strikes[-15:]

    if not candidate_strikes:
        return ChainResult(
            symbol=symbol,
            underlying_price=underlying_price,
            expiration=expiration,
            dte=dte,
            quotes=[],
            trading_class=trading_class,
            error=f"No OTM put strikes in range {lower_bound:.0f}-{upper_bound:.0f}",
        )

    logger.debug(
        f"{symbol} {expiration}: Fetching Greeks for {len(candidate_strikes)} strikes "
        f"({candidate_strikes[0]:.0f}-{candidate_strikes[-1]:.0f})"
    )

    # Build and qualify option contracts
    contracts = []
    for strike in candidate_strikes:
        opt = client.get_option_contract(
            symbol=symbol,
            expiration=expiration,
            strike=strike,
            right="P",
            exchange="SMART",
            trading_class=trading_class,
        )
        contracts.append((strike, opt))

    raw_contracts = [c for _, c in contracts]
    qualified_list = client.ib.qualifyContracts(*raw_contracts)

    # Map qualified contracts back to strikes
    qualified_map: dict[float, object] = {}
    for (strike, _), qualified in zip(contracts, qualified_list):
        if qualified and qualified.conId:
            qualified_map[strike] = qualified

    if not qualified_map:
        return ChainResult(
            symbol=symbol,
            underlying_price=underlying_price,
            expiration=expiration,
            dte=dte,
            quotes=[],
            trading_class=trading_class,
            error="Could not qualify any option contracts",
        )

    # Request market data with Greeks
    tickers: dict[float, tuple] = {}
    for strike, contract in qualified_map.items():
        try:
            ticker = client.ib.reqMktData(contract, "", False, False)
            tickers[strike] = (ticker, contract)
        except Exception as e:
            logger.debug(f"{symbol} ${strike}: reqMktData failed: {e}")

    # Wait for Greeks to populate (up to 3 seconds)
    for _ in range(6):
        client.ib.sleep(0.5)
        all_have_greeks = all(
            hasattr(t, "modelGreeks")
            and t.modelGreeks
            and t.modelGreeks.delta is not None
            for t, _ in tickers.values()
        )
        if all_have_greeks:
            break

    # Read data and cancel subscriptions
    quotes: list[OptionQuote] = []
    for strike, (ticker, contract) in tickers.items():
        try:
            delta = None
            iv = None
            gamma = None
            theta = None

            if hasattr(ticker, "modelGreeks") and ticker.modelGreeks:
                greeks = ticker.modelGreeks
                if greeks.delta is not None:
                    delta = abs(greeks.delta)
                iv = greeks.impliedVol
                gamma = greeks.gamma
                theta = greeks.theta

            bid = safe_field(ticker, "bid")
            ask = safe_field(ticker, "ask")
            vol = safe_field(ticker, "volume")
            oi = safe_field(ticker, "openInterest")

            if delta is not None and bid is not None and bid > 0:
                mid = ((bid or 0) + (ask or bid or 0)) / 2
                otm_pct = (underlying_price - strike) / underlying_price

                quotes.append(OptionQuote(
                    strike=strike,
                    delta=delta,
                    bid=bid,
                    ask=ask if ask and ask > 0 else bid,
                    mid=mid,
                    iv=iv,
                    gamma=gamma,
                    theta=theta,
                    volume=int(vol) if vol is not None else None,
                    open_interest=int(oi) if oi is not None else None,
                    expiration=expiration,
                    dte=dte,
                    otm_pct=otm_pct,
                ))

        except Exception as e:
            logger.debug(f"{symbol} ${strike}: Error reading data: {e}")
        finally:
            try:
                client.ib.cancelMktData(contract)
            except Exception:
                pass

    # Sort by strike descending (closest to ATM first)
    quotes.sort(key=lambda q: q.strike, reverse=True)

    logger.info(
        f"{symbol} {expiration} (DTE {dte}): Got Greeks for {len(quotes)}/{len(candidate_strikes)} strikes"
    )

    return ChainResult(
        symbol=symbol,
        underlying_price=underlying_price,
        expiration=expiration,
        dte=dte,
        quotes=quotes,
        trading_class=trading_class,
    )

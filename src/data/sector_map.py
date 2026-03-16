"""Sector classification map for stock symbols.

Provides sector lookup for concentration risk checks.
Built from the stock universe tier definitions, with a dynamic
yfinance fallback for symbols not in the static map.
"""

from loguru import logger

SYMBOL_SECTOR_MAP: dict[str, str] = {
    # =========================================================================
    # Tier 1 — Mega-cap (50 symbols)
    # =========================================================================
    # Mega Tech
    "AAPL": "Technology",
    "MSFT": "Technology",
    "GOOGL": "Technology",
    "GOOG": "Technology",
    "AMZN": "Technology",
    "NVDA": "Technology",
    "META": "Technology",
    "TSLA": "Technology",
    "AVGO": "Technology",
    "ORCL": "Technology",
    "CSCO": "Technology",
    "ADBE": "Technology",
    "CRM": "Technology",
    "INTC": "Technology",
    "AMD": "Technology",
    "QCOM": "Technology",
    # Finance
    "BRK.B": "Financials",
    "JPM": "Financials",
    "V": "Financials",
    "MA": "Financials",
    "BAC": "Financials",
    "WFC": "Financials",
    "GS": "Financials",
    "MS": "Financials",
    "C": "Financials",
    "AXP": "Financials",
    "BLK": "Financials",
    "SCHW": "Financials",
    # Healthcare
    "UNH": "Healthcare",
    "JNJ": "Healthcare",
    "LLY": "Healthcare",
    "ABBV": "Healthcare",
    "MRK": "Healthcare",
    "PFE": "Healthcare",
    "TMO": "Healthcare",
    "ABT": "Healthcare",
    # Consumer
    "WMT": "Consumer",
    "HD": "Consumer",
    "PG": "Consumer",
    "KO": "Consumer",
    "PEP": "Consumer",
    "COST": "Consumer",
    "NKE": "Consumer",
    "MCD": "Consumer",
    "SBUX": "Consumer",
    # Index ETFs
    "SPY": "Index ETF",
    "QQQ": "Index ETF",
    "IWM": "Index ETF",
    "DIA": "Index ETF",
    # =========================================================================
    # Tier 2 — S&P 500 extended
    # =========================================================================
    # Tech (continued)
    "TXN": "Technology",
    "AMAT": "Technology",
    "LRCX": "Technology",
    "KLAC": "Technology",
    "SNPS": "Technology",
    "CDNS": "Technology",
    "MCHP": "Technology",
    "FTNT": "Technology",
    "PANW": "Technology",
    "CRWD": "Technology",
    "ZS": "Technology",
    "DDOG": "Technology",
    "NET": "Technology",
    "SNOW": "Technology",
    "ACN": "Technology",
    "IBM": "Technology",
    "INTU": "Technology",
    "NOW": "Technology",
    "WDAY": "Technology",
    "CTSH": "Technology",
    # Finance (continued)
    "SPGI": "Financials",
    "CME": "Financials",
    "ICE": "Financials",
    "MCO": "Financials",
    "COF": "Financials",
    "USB": "Financials",
    "TFC": "Financials",
    "PNC": "Financials",
    "DFS": "Financials",
    "SYF": "Financials",
    "ALLY": "Financials",
    "FIS": "Financials",
    "FISV": "Financials",
    "ADP": "Financials",
    "PYPL": "Financials",
    "SQ": "Financials",
    # Healthcare (continued)
    "DHR": "Healthcare",
    "BMY": "Healthcare",
    "AMGN": "Healthcare",
    "GILD": "Healthcare",
    "REGN": "Healthcare",
    "VRTX": "Healthcare",
    "CVS": "Healthcare",
    "CI": "Healthcare",
    "ISRG": "Healthcare",
    "SYK": "Healthcare",
    "BSX": "Healthcare",
    "EW": "Healthcare",
    "ZTS": "Healthcare",
    "IDXX": "Healthcare",
    "BIIB": "Healthcare",
    "MRNA": "Healthcare",
    # Consumer (continued)
    "TGT": "Consumer",
    "LOW": "Consumer",
    "TJX": "Consumer",
    "BKNG": "Consumer",
    "MAR": "Consumer",
    "CMG": "Consumer",
    "YUM": "Consumer",
    "F": "Consumer",
    "GM": "Consumer",
    # Industrial
    "HON": "Industrials",
    "UNP": "Industrials",
    "UPS": "Industrials",
    "RTX": "Industrials",
    "LMT": "Industrials",
    "BA": "Industrials",
    "CAT": "Industrials",
    "DE": "Industrials",
    "GE": "Industrials",
    "NOC": "Industrials",
    "GD": "Industrials",
    "TDG": "Industrials",
    # Energy
    "XOM": "Energy",
    "CVX": "Energy",
    "COP": "Energy",
    "SLB": "Energy",
    "EOG": "Energy",
    "MPC": "Energy",
    "PSX": "Energy",
    "VLO": "Energy",
    # Materials
    "LIN": "Materials",
    "APD": "Materials",
    "ECL": "Materials",
    "SHW": "Materials",
    "FCX": "Materials",
    "NEM": "Materials",
    # Telecom
    "T": "Telecom",
    "VZ": "Telecom",
    "TMUS": "Telecom",
    # Real Estate
    "AMT": "Real Estate",
    "PLD": "Real Estate",
    "CCI": "Real Estate",
    "EQIX": "Real Estate",
    "PSA": "Real Estate",
    "O": "Real Estate",
    # Utilities
    "NEE": "Utilities",
    "DUK": "Utilities",
    "SO": "Utilities",
    "D": "Utilities",
    "AEP": "Utilities",
    # =========================================================================
    # ASX — Australian Stock Exchange
    # =========================================================================
    # Financials
    "CBA": "Financials",
    "NAB": "Financials",
    "WBC": "Financials",
    "ANZ": "Financials",
    "MQG": "Financials",
    "IAG": "Financials",
    "SUN": "Financials",
    "MPL": "Financials",
    "QBE": "Financials",
    "ASX": "Financials",
    # Materials & Mining
    "BHP": "Materials",
    "RIO": "Materials",
    "FMG": "Materials",
    "MIN": "Materials",
    "NCM": "Materials",
    "NST": "Materials",
    "S32": "Materials",
    "ORI": "Materials",
    "AMC": "Materials",
    # Energy
    "WDS": "Energy",
    "STO": "Energy",
    "ORG": "Energy",
    "AGL": "Energy",
    # Healthcare
    "CSL": "Healthcare",
    "SHL": "Healthcare",
    # Consumer
    "WES": "Consumer",
    "WOW": "Consumer",
    "COL": "Consumer",
    "TWE": "Consumer",
    "ALL": "Consumer",
    # Industrials & Real Estate
    "TCL": "Industrials",
    "BXB": "Industrials",
    "GMG": "Real Estate",
    # Technology
    "XRO": "Technology",
    "REA": "Technology",
    "CAR": "Technology",
    "CPU": "Technology",
    "IEL": "Technology",
    "JHX": "Materials",
}

# Maps yfinance sector names to our internal sector names
YFINANCE_SECTOR_MAP: dict[str, str] = {
    "Financial Services": "Financials",
    "Consumer Cyclical": "Consumer",
    "Consumer Defensive": "Consumer",
    "Communication Services": "Telecom",
    "Basic Materials": "Materials",
    # These map directly (yfinance name == our name)
    "Technology": "Technology",
    "Healthcare": "Healthcare",
    "Industrials": "Industrials",
    "Energy": "Energy",
    "Real Estate": "Real Estate",
    "Utilities": "Utilities",
}

# In-memory cache for dynamic lookups (avoids repeated yfinance calls)
_DYNAMIC_CACHE: dict[str, str | None] = {}


def lookup_sector_dynamic(symbol: str) -> str | None:
    """Look up sector for a symbol via yfinance.

    Args:
        symbol: Stock ticker symbol

    Returns:
        Mapped sector name, or raw yfinance sector if no mapping exists,
        or None if lookup fails.
    """
    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        raw_sector = ticker.info.get("sector")
        if not raw_sector:
            return None
        mapped = YFINANCE_SECTOR_MAP.get(raw_sector, raw_sector)
        logger.debug(f"yfinance sector for {symbol}: {raw_sector} -> {mapped}")
        return mapped
    except Exception as e:
        logger.debug(f"yfinance sector lookup failed for {symbol}: {e}")
        return None


def get_sector(symbol: str) -> str:
    """Return sector for symbol, using static map then yfinance fallback.

    Lookup order:
    1. Static SYMBOL_SECTOR_MAP (fast, no network)
    2. In-memory _DYNAMIC_CACHE (already looked up this session)
    3. yfinance API call (cached for the session)

    Args:
        symbol: Stock ticker symbol

    Returns:
        Sector name string, or "Unknown" if all lookups fail
    """
    # 1. Static map
    static = SYMBOL_SECTOR_MAP.get(symbol)
    if static:
        return static

    # 2. Dynamic cache
    if symbol in _DYNAMIC_CACHE:
        return _DYNAMIC_CACHE[symbol] or "Unknown"

    # 3. yfinance lookup
    sector = lookup_sector_dynamic(symbol)
    _DYNAMIC_CACHE[symbol] = sector
    return sector or "Unknown"

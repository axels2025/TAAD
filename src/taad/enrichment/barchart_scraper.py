"""Barchart Premier historical options data scraper.

Fetches historical option chain data (IV, Greeks, volume, OI, bid/ask)
for specific contracts on specific dates from Barchart.com's historical
options pages. Requires a Barchart Premier subscription.

Coverage: March 2023 – present (~2,100 trades, ~500 unique positions).
Pre-March 2023 trades fall back to Black-Scholes approximation.

Data is cached locally in a SQLite database to avoid re-scraping.
"""

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from src.taad.enrichment.providers import HistoricalDataProvider, OHLCV, OptionSnapshot


# Earliest date with reliable Barchart historical options data
BARCHART_EARLIEST_DATE = date(2023, 3, 1)


@dataclass
class BarchartCacheEntry:
    """Cached Barchart option data."""

    symbol: str
    strike: float
    expiry: str  # YYYY-MM-DD
    put_call: str  # P or C
    on_date: str  # YYYY-MM-DD
    data_json: str  # JSON blob
    scraped_at: str  # ISO timestamp


class BarchartHistoricalCache:
    """SQLite cache for scraped Barchart option data.

    Avoids re-scraping the same contract/date combination.
    """

    def __init__(self, cache_path: Optional[Path] = None):
        """Initialize cache.

        Args:
            cache_path: Path to SQLite cache file.
                        Defaults to data/cache/barchart_historical.db
        """
        if cache_path is None:
            cache_path = Path("data/cache/barchart_historical.db")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(cache_path)
        self._init_db()

    def _init_db(self) -> None:
        """Create cache table if it doesn't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS option_cache (
                    symbol TEXT NOT NULL,
                    strike REAL NOT NULL,
                    expiry TEXT NOT NULL,
                    put_call TEXT NOT NULL,
                    on_date TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    scraped_at TEXT NOT NULL,
                    PRIMARY KEY (symbol, strike, expiry, put_call, on_date)
                )
            """)

    def get(
        self,
        symbol: str,
        strike: float,
        expiry: date,
        put_call: str,
        on_date: date,
    ) -> Optional[dict]:
        """Retrieve cached option data.

        Returns:
            Parsed data dict, or None if not cached.
        """
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT data_json FROM option_cache
                WHERE symbol = ? AND strike = ? AND expiry = ?
                AND put_call = ? AND on_date = ?
                """,
                (symbol, strike, expiry.isoformat(), put_call, on_date.isoformat()),
            ).fetchone()

        if row:
            return json.loads(row[0])
        return None

    def put(
        self,
        symbol: str,
        strike: float,
        expiry: date,
        put_call: str,
        on_date: date,
        data: dict,
    ) -> None:
        """Store option data in cache."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO option_cache
                (symbol, strike, expiry, put_call, on_date, data_json, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    strike,
                    expiry.isoformat(),
                    put_call,
                    on_date.isoformat(),
                    json.dumps(data),
                    datetime.now().isoformat(),
                ),
            )

    def count(self) -> int:
        """Return number of cached entries."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT COUNT(*) FROM option_cache").fetchone()
            return row[0] if row else 0

    def has(
        self,
        symbol: str,
        strike: float,
        expiry: date,
        put_call: str,
        on_date: date,
    ) -> bool:
        """Check if entry exists in cache."""
        return self.get(symbol, strike, expiry, put_call, on_date) is not None


def _build_option_symbol(
    symbol: str, expiry: date, put_call: str, strike: float
) -> str:
    """Build OCC-format option symbol.

    Format: SYMBOL YYMMDD P/C STRIKE*1000 (zero-padded)
    Example: AAPL 240119 P 00185000

    Args:
        symbol: Underlying symbol
        expiry: Expiration date
        put_call: "P" or "C"
        strike: Strike price

    Returns:
        OCC-format option symbol string
    """
    expiry_str = expiry.strftime("%y%m%d")
    pc = "P" if put_call.upper().startswith("P") else "C"
    # Strike in thousandths (e.g., 185.00 -> 00185000)
    strike_str = f"{int(strike * 1000):08d}"
    return f"{symbol}{expiry_str}{pc}{strike_str}"


def _parse_barchart_response(data: dict) -> Optional[dict]:
    """Parse Barchart API response into a normalized option data dict.

    Args:
        data: Raw API response data

    Returns:
        Normalized dict with option fields, or None if parsing fails
    """
    try:
        results = data.get("results", [])
        if not results:
            return None

        # Take the first (or best matching) result
        row = results[0] if isinstance(results, list) else results

        parsed = {}

        # Map Barchart fields to our fields
        field_map = {
            "bid": ("bid", "bidPrice", "optionBid"),
            "ask": ("ask", "askPrice", "optionAsk"),
            "last": ("lastPrice", "last", "optionLast"),
            "volume": ("volume", "optionVolume", "totalVolume"),
            "open_interest": ("openInterest", "oi"),
            "iv": ("impliedVolatility", "volatility", "iv"),
            "delta": ("delta",),
            "gamma": ("gamma",),
            "theta": ("theta",),
            "vega": ("vega",),
            "rho": ("rho",),
        }

        for our_field, barchart_names in field_map.items():
            for bc_name in barchart_names:
                val = row.get(bc_name)
                if val is not None:
                    try:
                        parsed[our_field] = float(val)
                    except (ValueError, TypeError):
                        pass
                    break

        return parsed if parsed else None

    except Exception as e:
        logger.debug(f"Failed to parse Barchart response: {e}")
        return None


class BarchartScraperProvider(HistoricalDataProvider):
    """Fetches historical option data from Barchart.

    Uses Barchart's getHistory API endpoint for historical option
    OHLCV/Greeks data. Requires a valid API key (Premier subscription).

    This provider only implements get_option_snapshot(). All other
    methods return None — they should be handled by YFinanceProvider
    or IBKRHistoricalProvider via FallbackChainProvider.

    Usage:
        provider = BarchartScraperProvider(api_key="your_key")
        snapshot = provider.get_option_snapshot(
            "AAPL", 185.0, date(2024, 1, 19), "P", date(2024, 1, 5)
        )
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        cache_path: Optional[Path] = None,
        rate_limit_seconds: float = 2.0,
    ):
        """Initialize Barchart scraper.

        Args:
            api_key: Barchart API key. Falls back to BARCHART_API_KEY env var.
            cache_path: Path to SQLite cache file.
            rate_limit_seconds: Minimum seconds between API calls.
        """
        self.api_key = api_key or os.environ.get("BARCHART_API_KEY", "")
        self.cache = BarchartHistoricalCache(cache_path)
        self.rate_limit = rate_limit_seconds
        self._last_request_time: float = 0.0
        self.base_url = "https://ondemand.websol.barchart.com/getHistory.json"

        if not self.api_key:
            logger.warning(
                "No BARCHART_API_KEY set. BarchartScraperProvider will not fetch data."
            )

    def _rate_limit_wait(self) -> None:
        """Wait if needed to respect rate limits."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit:
            sleep_time = self.rate_limit - elapsed
            logger.debug(f"Rate limiting: sleeping {sleep_time:.1f}s")
            time.sleep(sleep_time)

    def _fetch_option_data(
        self,
        symbol: str,
        strike: float,
        expiry: date,
        put_call: str,
        on_date: date,
    ) -> Optional[dict]:
        """Fetch historical option data from Barchart API.

        Args:
            symbol: Underlying symbol
            strike: Strike price
            expiry: Option expiration date
            put_call: "P" or "C"
            on_date: Date to get data for

        Returns:
            Parsed data dict or None
        """
        if not self.api_key:
            return None

        if on_date < BARCHART_EARLIEST_DATE:
            logger.debug(
                f"Skipping Barchart fetch for {symbol} on {on_date} "
                f"(before {BARCHART_EARLIEST_DATE})"
            )
            return None

        # Build OCC option symbol
        option_symbol = _build_option_symbol(symbol, expiry, put_call, strike)

        self._rate_limit_wait()

        params = {
            "apikey": self.api_key,
            "symbol": option_symbol,
            "type": "daily",
            "startDate": on_date.isoformat(),
            "endDate": on_date.isoformat(),
            "fields": "bid,ask,volume,openInterest,impliedVolatility,delta,gamma,theta,vega,rho",
        }

        try:
            with httpx.Client(timeout=15.0) as client:
                response = client.get(self.base_url, params=params)
                self._last_request_time = time.time()

                if response.status_code == 200:
                    data = response.json()
                    parsed = _parse_barchart_response(data)
                    if parsed:
                        logger.debug(
                            f"Barchart data for {option_symbol} on {on_date}: "
                            f"IV={parsed.get('iv')}, delta={parsed.get('delta')}"
                        )
                    return parsed
                elif response.status_code == 401:
                    logger.warning("Barchart API: unauthorized (check API key)")
                    return None
                elif response.status_code == 429:
                    logger.warning("Barchart API: rate limited, backing off")
                    time.sleep(5.0)
                    return None
                else:
                    logger.debug(
                        f"Barchart API returned {response.status_code} "
                        f"for {option_symbol}"
                    )
                    return None

        except httpx.TimeoutException:
            logger.debug(f"Barchart API timeout for {option_symbol}")
            return None
        except Exception as e:
            logger.debug(f"Barchart API error for {option_symbol}: {e}")
            return None

    def get_option_snapshot(
        self,
        symbol: str,
        strike: float,
        expiry: date,
        put_call: str,
        on_date: date,
    ) -> Optional[OptionSnapshot]:
        """Get historical option data from Barchart.

        Checks cache first. On cache miss, fetches from API and caches.

        Args:
            symbol: Underlying symbol
            strike: Strike price
            expiry: Option expiration date
            put_call: "P" or "C"
            on_date: Date to get data for

        Returns:
            OptionSnapshot with available fields, or None
        """
        if on_date < BARCHART_EARLIEST_DATE:
            return None

        pc = "P" if put_call.upper().startswith("P") else "C"

        # Check cache
        cached = self.cache.get(symbol, strike, expiry, pc, on_date)
        if cached is not None:
            logger.debug(f"Barchart cache hit: {symbol} {strike}{pc} on {on_date}")
            return self._dict_to_snapshot(cached)

        # Fetch from API
        data = self._fetch_option_data(symbol, strike, expiry, pc, on_date)
        if data is None:
            return None

        # Cache the result
        self.cache.put(symbol, strike, expiry, pc, on_date, data)

        return self._dict_to_snapshot(data)

    def _dict_to_snapshot(self, data: dict) -> Optional[OptionSnapshot]:
        """Convert parsed data dict to OptionSnapshot."""
        if not data:
            return None

        bid = data.get("bid")
        ask = data.get("ask")

        # Calculate mid and spread
        mid = None
        spread_pct = None
        if bid is not None and ask is not None:
            mid = round((bid + ask) / 2, 4)
            if mid > 0:
                spread_pct = round((ask - bid) / mid, 4)

        return OptionSnapshot(
            bid=bid,
            ask=ask,
            mid=mid,
            spread_pct=spread_pct,
            volume=int(data["volume"]) if data.get("volume") is not None else None,
            open_interest=int(data["open_interest"])
            if data.get("open_interest") is not None
            else None,
            iv=data.get("iv"),
            delta=data.get("delta"),
            gamma=data.get("gamma"),
            theta=data.get("theta"),
            vega=data.get("vega"),
            rho=data.get("rho"),
            source="barchart",
        )

    # --- HistoricalDataProvider interface (not implemented) ---

    def get_stock_bar(self, symbol: str, on_date: date) -> Optional[OHLCV]:
        """Not implemented — use YFinanceProvider."""
        return None

    def get_historical_bars(self, symbol, end_date, lookback_days=130):
        """Not implemented — use YFinanceProvider."""
        return None

    def get_vix_close(self, on_date: date) -> Optional[float]:
        """Not implemented — use YFinanceProvider."""
        return None

    def get_index_bar(self, symbol: str, on_date: date) -> Optional[OHLCV]:
        """Not implemented — use YFinanceProvider."""
        return None

    def get_sector_etf_bars(self, etf, end_date, lookback_days=10):
        """Not implemented — use YFinanceProvider."""
        return None

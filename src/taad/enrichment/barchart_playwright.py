"""Barchart Premier historical options data scraper using Playwright.

Scrapes historical option price history (IV, Greeks, volume, OI, bid/ask)
from barchart.com/options/price-history using Playwright browser automation.
Requires a Barchart Premier subscription and stored login session.

Coverage: January 2017 – present (daily data for all equity options).
Pre-January 2017 trades fall back to Black-Scholes approximation.

Data is cached locally in a shared SQLite database (same as the API provider)
to avoid re-scraping.

Setup:
    pip install playwright && playwright install chromium
    nakedtrader taad-barchart-login   # One-time manual login
"""

import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from src.taad.enrichment.barchart_scraper import BarchartHistoricalCache
from src.taad.enrichment.providers import HistoricalDataProvider, OHLCV, OptionSnapshot


# Earliest date with reliable Barchart daily options history
PLAYWRIGHT_EARLIEST_DATE = date(2017, 1, 3)

# Barchart options price history page
BARCHART_OPTIONS_HISTORY_URL = "https://www.barchart.com/options/price-history"

# Default Playwright storage state path (session cookies)
STORAGE_STATE_PATH = Path("data/cache/barchart_playwright_state.json")

# Default rate limit between page loads (seconds)
DEFAULT_RATE_LIMIT = 2.5

# Column header → normalized field name mapping
# Handles multiple variations of Barchart column headers
BARCHART_COLUMN_MAP = {
    "open": "open",
    "high": "high",
    "low": "low",
    "last": "last",
    "close": "last",
    "change": None,  # Ignore
    "volume": "volume",
    "vol": "volume",
    "open int": "open_interest",
    "open interest": "open_interest",
    "oi": "open_interest",
    "impl vol": "iv",
    "implied vol": "iv",
    "implied volatility": "iv",
    "iv": "iv",
    "delta": "delta",
    "gamma": "gamma",
    "theta": "theta",
    "vega": "vega",
    "rho": "rho",
    "theo": None,  # Ignore theoretical value
    "theoretical": None,
    "underlying": None,  # Ignore underlying price column
}

# Fields where the raw value is a percentage (e.g., "32.5%" → 0.325)
_PERCENTAGE_FIELDS = frozenset({"iv"})


def _build_barchart_page_symbol(
    symbol: str, expiry: date, put_call: str, strike: float
) -> str:
    """Build Barchart page-format option symbol.

    Format: SYMBOL|YYYYMMDD|STRIKE.DDP/C
    Example: AAPL|20240719|150.00P

    Args:
        symbol: Underlying ticker
        expiry: Expiration date
        put_call: "P" or "C" (also accepts "PUT", "CALL")
        strike: Strike price

    Returns:
        Barchart page-format symbol string
    """
    expiry_str = expiry.strftime("%Y%m%d")
    pc = "P" if put_call.upper().startswith("P") else "C"
    strike_str = f"{strike:.2f}"
    return f"{symbol}|{expiry_str}|{strike_str}{pc}"


def _parse_numeric(text: str, is_percentage: bool = False) -> Optional[float]:
    """Parse a numeric string from a Barchart table cell.

    Handles commas, percentages, N/A, empty strings, dashes.

    Args:
        text: Raw cell text
        is_percentage: If True, or if text contains %, divide by 100

    Returns:
        Parsed float, or None if not parseable
    """
    cleaned = text.strip().replace(",", "")
    if not cleaned or cleaned in ("N/A", "-", "--", "n/a", "unch"):
        return None
    try:
        has_pct = "%" in cleaned
        val = float(cleaned.rstrip("%"))
        if is_percentage or has_pct:
            val /= 100.0
        return val
    except ValueError:
        return None


def _parse_date(text: str) -> Optional[date]:
    """Parse a date string from a Barchart table cell.

    Handles MM/DD/YYYY, MM/DD/YY, and YYYY-MM-DD formats.

    Args:
        text: Raw date text

    Returns:
        Parsed date, or None if not parseable
    """
    cleaned = text.strip()
    if not cleaned:
        return None

    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _parse_table_row(headers: list[str], cells: list[str]) -> Optional[dict]:
    """Parse a single HTML table row into a normalized data dict.

    Maps column headers (via BARCHART_COLUMN_MAP) to standardized field names
    compatible with BarchartHistoricalCache.

    Args:
        headers: Column header strings (lowercase, stripped)
        cells: Cell text values from a table row

    Returns:
        Normalized dict with option data fields, or None if parsing fails
    """
    if len(cells) < 2:
        return None

    parsed = {}

    for i, header in enumerate(headers):
        if i >= len(cells):
            break

        field_name = BARCHART_COLUMN_MAP.get(header)
        if field_name is None:
            continue

        is_pct = field_name in _PERCENTAGE_FIELDS
        val = _parse_numeric(cells[i], is_percentage=is_pct)
        if val is not None:
            # Volume and OI should be integers
            if field_name in ("volume", "open_interest"):
                parsed[field_name] = int(val)
            else:
                parsed[field_name] = val

    return parsed if parsed else None


class PlaywrightBarchartProvider(HistoricalDataProvider):
    """Fetches historical option data by scraping Barchart's website.

    Uses Playwright to navigate to barchart.com/options/price-history,
    authenticate via stored session cookies, and parse the data table.

    Requires a Barchart Premier subscription. Session cookies are stored
    at data/cache/barchart_playwright_state.json. If cookies expire,
    run ``nakedtrader taad-barchart-login`` to re-authenticate.

    This provider only implements get_option_snapshot(). All other
    HistoricalDataProvider methods return None.

    Usage:
        provider = PlaywrightBarchartProvider()
        if provider.has_valid_session():
            snapshot = provider.get_option_snapshot(
                "AAPL", 185.0, date(2024, 1, 19), "P", date(2024, 1, 5)
            )
    """

    def __init__(
        self,
        cache_path: Optional[Path] = None,
        storage_state_path: Optional[Path] = None,
        rate_limit_seconds: float = DEFAULT_RATE_LIMIT,
        headless: bool = True,
    ):
        """Initialize Playwright Barchart provider.

        Args:
            cache_path: Path to SQLite cache file (shared with API provider).
            storage_state_path: Path to Playwright storage state JSON.
            rate_limit_seconds: Minimum seconds between page loads.
            headless: Run browser in headless mode.
        """
        self.cache = BarchartHistoricalCache(cache_path)
        self.storage_state_path = storage_state_path or STORAGE_STATE_PATH
        self.rate_limit = rate_limit_seconds
        self.headless = headless
        self._last_request_time: float = 0.0
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
        self._login_checked: bool = False

    def has_valid_session(self) -> bool:
        """Check if stored session cookies exist (does not verify validity).

        Returns:
            True if the storage state file exists on disk.
        """
        return self.storage_state_path.exists()

    def _ensure_browser(self) -> bool:
        """Lazily initialize Playwright browser with stored session.

        Returns:
            True if browser is ready.
        """
        if self._page is not None:
            return True

        try:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self.headless)

            # Load storage state (cookies) if available
            if self.storage_state_path.exists():
                self._context = self._browser.new_context(
                    storage_state=str(self.storage_state_path)
                )
            else:
                self._context = self._browser.new_context()

            self._page = self._context.new_page()
            return True

        except ImportError:
            logger.warning(
                "Playwright not installed. "
                "Install with: pip install playwright && playwright install chromium"
            )
            return False
        except Exception as e:
            logger.warning(f"Playwright browser init failed: {e}")
            return False

    def _is_logged_in(self) -> bool:
        """Check if the current session has valid Barchart login.

        Navigates to the options price history page and checks for
        authenticated indicators.

        Returns:
            True if logged in with Premier access.
        """
        try:
            self._page.goto(
                BARCHART_OPTIONS_HISTORY_URL,
                wait_until="domcontentloaded",
                timeout=15000,
            )

            # Check for redirect to login page
            if "login" in self._page.url.lower():
                return False

            # Look for login button / modal
            login_el = self._page.query_selector(
                'a[href*="/login"], .bc-user-login-button, .login-button'
            )
            if login_el and login_el.is_visible():
                return False

            # If we got here without redirect or login prompt, we're in
            return True

        except Exception as e:
            logger.debug(f"Login check failed: {e}")
            return False

    def _rate_limit_wait(self) -> None:
        """Wait if needed to respect rate limits."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit:
            sleep_time = self.rate_limit - elapsed
            logger.debug(f"Playwright rate limiting: sleeping {sleep_time:.1f}s")
            time.sleep(sleep_time)

    def login_interactive(self) -> bool:
        """Open visible browser for manual Barchart login.

        Opens a non-headless browser pointed at the Barchart login page.
        Waits for the user to complete login, then saves cookies.

        Returns:
            True if login was successful and cookies were saved.
        """
        logger.info("Opening browser for Barchart login...")

        # Close any existing browser
        self.close()

        try:
            from playwright.sync_api import sync_playwright

            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            page.goto("https://www.barchart.com/login")

            # Block until user signals completion
            logger.info(
                "Please log in to Barchart in the browser window. "
                "Press Enter in the terminal when done..."
            )
            input()

            # Save storage state (cookies + localStorage)
            self.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(self.storage_state_path))
            logger.info(f"Session saved to {self.storage_state_path}")

            page.close()
            context.close()
            browser.close()
            pw.stop()

            return True

        except Exception as e:
            logger.error(f"Interactive login failed: {e}")
            return False

    def _parse_table_headers(self) -> list[str]:
        """Extract column headers from the data table.

        Returns:
            List of lowercase, stripped header strings.
        """
        header_cells = self._page.query_selector_all(
            "table thead th, table thead td"
        )
        return [cell.inner_text().strip().lower() for cell in header_cells]

    def _scrape_and_cache_all_rows(
        self,
        symbol: str,
        strike: float,
        expiry: date,
        put_call: str,
        headers: list[str],
    ) -> dict[date, dict]:
        """Parse all visible table rows and cache them.

        Cache-ahead optimization: a single page load returns many rows,
        so we cache all of them to avoid redundant page loads for trades
        on the same contract but different dates.

        Args:
            symbol: Underlying symbol
            strike: Strike price
            expiry: Option expiration date
            put_call: "P" or "C"
            headers: Column header names

        Returns:
            Mapping of trade_date → parsed data dict
        """
        rows = self._page.query_selector_all("table tbody tr")
        results: dict[date, dict] = {}

        for row in rows:
            cells = row.query_selector_all("td")
            if not cells:
                continue

            cell_texts = [cell.inner_text().strip() for cell in cells]
            if not cell_texts:
                continue

            # First column should be the date
            row_date = _parse_date(cell_texts[0])
            if row_date is None:
                continue

            parsed = _parse_table_row(headers, cell_texts)
            if parsed:
                results[row_date] = parsed
                # Cache each row
                if not self.cache.has(symbol, strike, expiry, put_call, row_date):
                    self.cache.put(symbol, strike, expiry, put_call, row_date, parsed)

        return results

    def _fetch_option_data(
        self,
        symbol: str,
        strike: float,
        expiry: date,
        put_call: str,
        on_date: date,
    ) -> Optional[dict]:
        """Scrape option data from Barchart price history page.

        Args:
            symbol: Underlying symbol
            strike: Strike price
            expiry: Option expiration date
            put_call: "P" or "C"
            on_date: Date to get data for

        Returns:
            Normalized data dict or None
        """
        if on_date < PLAYWRIGHT_EARLIEST_DATE:
            return None

        if not self._ensure_browser():
            return None

        # Check login once per session
        if not self._login_checked:
            if not self._is_logged_in():
                logger.warning(
                    "Barchart session expired or not logged in. "
                    "Run `nakedtrader taad-barchart-login` to re-authenticate."
                )
                return None
            self._login_checked = True

        # Rate limit
        self._rate_limit_wait()

        # Build URL
        page_symbol = _build_barchart_page_symbol(symbol, expiry, put_call, strike)
        url = f"{BARCHART_OPTIONS_HISTORY_URL}?symbol={page_symbol}"

        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=15000)

            # Check for redirect to login (session expired mid-batch)
            if "login" in self._page.url.lower():
                logger.warning("Session expired mid-scrape — redirected to login")
                self._login_checked = False
                return None

            # Wait for the data table to appear
            self._page.wait_for_selector(
                "table tbody tr", timeout=10000
            )

            self._last_request_time = time.time()

            # Parse headers
            headers = self._parse_table_headers()
            if not headers:
                logger.debug(f"No table headers found for {page_symbol}")
                return None

            # Scrape and cache ALL visible rows (cache-ahead)
            all_rows = self._scrape_and_cache_all_rows(
                symbol, strike, expiry, put_call, headers
            )

            # Return the row for our target date
            return all_rows.get(on_date)

        except Exception as e:
            logger.debug(f"Playwright scrape failed for {page_symbol} on {on_date}: {e}")
            self._last_request_time = time.time()
            return None

    def _dict_to_snapshot(self, data: dict) -> Optional[OptionSnapshot]:
        """Convert parsed data dict to OptionSnapshot.

        Args:
            data: Normalized dict from table parsing or cache

        Returns:
            OptionSnapshot or None
        """
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
            source="barchart_playwright",
        )

    def get_option_snapshot(
        self,
        symbol: str,
        strike: float,
        expiry: date,
        put_call: str,
        on_date: date,
    ) -> Optional[OptionSnapshot]:
        """Get historical option data by scraping Barchart.

        Checks shared cache first. On cache miss, scrapes the page,
        caches all visible rows (cache-ahead), and returns the target row.

        Args:
            symbol: Underlying symbol
            strike: Strike price
            expiry: Option expiration date
            put_call: "P" or "C"
            on_date: Date to get data for

        Returns:
            OptionSnapshot with available fields, or None
        """
        if on_date < PLAYWRIGHT_EARLIEST_DATE:
            return None

        pc = "P" if put_call.upper().startswith("P") else "C"

        # Check shared cache (same DB as API provider)
        cached = self.cache.get(symbol, strike, expiry, pc, on_date)
        if cached is not None:
            logger.debug(f"Barchart cache hit: {symbol} {strike}{pc} on {on_date}")
            return self._dict_to_snapshot(cached)

        # Scrape from page
        data = self._fetch_option_data(symbol, strike, expiry, pc, on_date)
        if data is None:
            return None

        return self._dict_to_snapshot(data)

    def close(self) -> None:
        """Close Playwright browser and release resources."""
        if self._page:
            try:
                self._page.close()
            except Exception:
                pass
            self._page = None
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        self._login_checked = False

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

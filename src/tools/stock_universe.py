"""Stock Universe Management System

Provides tiered stock universes for efficient scanning with proper coverage.
Implements persistence to avoid re-scanning the same stocks.
"""

from pathlib import Path
from typing import Literal
import json
from datetime import datetime, timedelta

from loguru import logger


class StockUniverseManager:
    """Manages stock universes for scanning with persistence.

    Implements a tiered approach:
    - Tier 1: Top 50 most liquid stocks (scan daily)
    - Tier 2: S&P 500 (scan weekly, cache results)
    - Tier 3: Russell 1000 (scan weekly, cache results)
    - Tier 4: High-volume mid-caps (scan monthly)

    Persists scan results to avoid redundant checks.
    """

    def __init__(self, cache_dir: str = "data/cache"):
        """Initialize universe manager.

        Args:
            cache_dir: Directory for caching scan results
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.scan_cache_file = self.cache_dir / "scan_cache.json"

        logger.info(f"Initialized StockUniverseManager with cache at {self.cache_dir}")

    def get_universe(
        self,
        tier: Literal["tier1", "tier2", "tier3", "tier4", "all"] = "tier1",
        max_stocks: int | None = None
    ) -> list[str]:
        """Get stock universe for scanning.

        Args:
            tier: Which tier to use
            max_stocks: Maximum stocks to return (None = all)

        Returns:
            List of ticker symbols
        """
        if tier == "tier1":
            universe = self._get_tier1_universe()
        elif tier == "tier2":
            universe = self._get_tier2_universe()
        elif tier == "tier3":
            universe = self._get_tier3_universe()
        elif tier == "tier4":
            universe = self._get_tier4_universe()
        else:  # all
            universe = list(set(
                self._get_tier1_universe() +
                self._get_tier2_universe() +
                self._get_tier3_universe() +
                self._get_tier4_universe()
            ))

        if max_stocks:
            universe = universe[:max_stocks]

        logger.info(f"Retrieved {len(universe)} stocks from {tier}")
        return universe

    def mark_scanned(
        self,
        symbol: str,
        result: dict,
        scan_type: str = "full"
    ):
        """Mark a stock as scanned and cache the result.

        Args:
            symbol: Stock symbol
            result: Scan result data
            scan_type: Type of scan performed
        """
        cache = self._load_cache()

        cache[symbol] = {
            "last_scan": datetime.now().isoformat(),
            "scan_type": scan_type,
            "result": result,
        }

        self._save_cache(cache)

    def get_cached_result(
        self,
        symbol: str,
        max_age_hours: int = 24
    ) -> dict | None:
        """Get cached scan result if fresh enough.

        Args:
            symbol: Stock symbol
            max_age_hours: Maximum age of cached result in hours

        Returns:
            Cached result or None if not found/expired
        """
        cache = self._load_cache()

        if symbol not in cache:
            return None

        cached = cache[symbol]
        last_scan = datetime.fromisoformat(cached["last_scan"])
        age = datetime.now() - last_scan

        if age > timedelta(hours=max_age_hours):
            return None

        return cached["result"]

    def get_unscanned_symbols(
        self,
        universe: list[str],
        max_age_hours: int = 24
    ) -> list[str]:
        """Get symbols that haven't been scanned recently.

        Args:
            universe: Full universe to check
            max_age_hours: How old cached results can be

        Returns:
            List of symbols needing fresh scan
        """
        unscanned = []

        for symbol in universe:
            if self.get_cached_result(symbol, max_age_hours) is None:
                unscanned.append(symbol)

        logger.info(
            f"Found {len(unscanned)}/{len(universe)} symbols needing scan "
            f"(cache age: {max_age_hours}h)"
        )
        return unscanned

    def clear_cache(self, older_than_days: int | None = None):
        """Clear scan cache.

        Args:
            older_than_days: Only clear entries older than N days (None = clear all)
        """
        if older_than_days is None:
            self.scan_cache_file.write_text("{}")
            logger.info("Cleared all scan cache")
            return

        cache = self._load_cache()
        cutoff = datetime.now() - timedelta(days=older_than_days)

        filtered = {
            symbol: data
            for symbol, data in cache.items()
            if datetime.fromisoformat(data["last_scan"]) > cutoff
        }

        removed = len(cache) - len(filtered)
        self._save_cache(filtered)
        logger.info(f"Removed {removed} cached entries older than {older_than_days} days")

    def _load_cache(self) -> dict:
        """Load scan cache from disk."""
        if not self.scan_cache_file.exists():
            return {}

        try:
            return json.loads(self.scan_cache_file.read_text())
        except Exception as e:
            logger.warning(f"Failed to load scan cache: {e}")
            return {}

    def _save_cache(self, cache: dict):
        """Save scan cache to disk."""
        try:
            self.scan_cache_file.write_text(
                json.dumps(cache, indent=2, default=str)
            )
        except Exception as e:
            logger.error(f"Failed to save scan cache: {e}")

    def _get_tier1_universe(self) -> list[str]:
        """Tier 1: Top 50 most liquid stocks - scan daily.

        These are mega-cap, highly liquid stocks with the best options markets.
        """
        return [
            # Mega Tech
            "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", "TSLA",
            "AVGO", "ORCL", "CSCO", "ADBE", "CRM", "INTC", "AMD", "QCOM",
            # Finance
            "BRK.B", "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "C", "AXP",
            "BLK", "SCHW",
            # Healthcare
            "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT",
            # Consumer
            "WMT", "HD", "PG", "KO", "PEP", "COST", "NKE", "MCD", "SBUX",
            # Index ETFs (always liquid)
            "SPY", "QQQ", "IWM", "DIA",
        ]

    def _get_tier2_universe(self) -> list[str]:
        """Tier 2: S&P 500 stocks - scan weekly, cache results.

        Returns top 200 from S&P 500 by market cap/liquidity.
        """
        # Top 200 S&P 500 stocks by market cap (excluding Tier 1)
        return [
            # Tech (continued)
            "TXN", "AMAT", "LRCX", "KLAC", "SNPS", "CDNS", "MCHP", "FTNT",
            "PANW", "CRWD", "ZS", "DDOG", "NET", "SNOW",
            # Finance (continued)
            "SPGI", "CME", "ICE", "MCO", "COF", "USB", "TFC", "PNC",
            # Healthcare (continued)
            "DHR", "BMY", "AMGN", "GILD", "REGN", "VRTX", "CVS", "CI",
            # Consumer (continued)
            "AMZN", "TGT", "LOW", "TJX", "BKNG", "MAR", "CMG", "YUM",
            # Industrial
            "HON", "UNP", "UPS", "RTX", "LMT", "BA", "CAT", "DE", "GE",
            # Energy
            "XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO",
            # Materials
            "LIN", "APD", "ECL", "SHW", "FCX", "NEM",
            # Telecom
            "T", "VZ", "TMUS",
            # Real Estate
            "AMT", "PLD", "CCI", "EQIX", "PSA", "O",
            # Utilities
            "NEE", "DUK", "SO", "D", "AEP",
            # Add more S&P 500 stocks
            "NOC", "GD", "TDG", "ISRG", "SYK", "BSX", "EW", "ZTS", "IDXX",
            "ALGN", "DXC", "CTSH", "ACN", "IBM", "INTU", "NOW", "WDAY",
            "TEAM", "ZM", "DOCU", "OKTA", "TWLO", "SHOP", "SQ", "PYPL",
            "ADYEY", "FIS", "FISV", "ADP", "PAYX", "BR", "FLT", "TYL",
            "GWRE", "MANH", "VEEV", "HUBS", "RNG", "BILL", "PATH", "QLYS",
            "TENB", "S", "ESTC", "MDB", "CFLT", "DT", "FROG", "AI", "PLTR",
            "RBLX", "U", "DASH", "ABNB", "LYFT", "UBER", "RIVN", "LCID",
            "F", "GM", "TM", "HMC", "STLA", "RACE", "TSLA", "NIO", "XPEV",
            "LI", "BYDDY", "VWAGY", "BMWYY", "DDAIF", "POAHY", "FUJHY",
            "NSANY", "TM", "HMC", "SSNLF", "HYMTF", "MBGAF", "VLKAF",
        ]

    def _get_tier3_universe(self) -> list[str]:
        """Tier 3: Russell 1000 - scan weekly, cache results.

        Returns liquid large/mid caps not in Tier 1/2.
        """
        # Top liquid Russell 1000 stocks (excluding Tier 1/2)
        return [
            # Additional mid-caps with good liquidity
            "ANET", "DXCM", "ENPH", "SEDG", "MPWR", "ON", "SWKS", "QRVO",
            "NXPI", "STM", "ASML", "TSM", "UMC", "ASX", "ENTG", "FORM",
            "COHR", "LITE", "AAOI", "FNSR", "VIAV", "INFN", "CIEN", "JNPR",
            "FFIV", "ANET", "ARCT", "CYBR", "VRNS", "MIME", "RPD", "NLOK",
            "GEN", "CHKP", "FTNT", "PANW", "ZS", "CRWD", "S", "OKTA",
            # More mid-cap tech
            "DELL", "HPQ", "HPE", "WDC", "STX", "NTAP", "PSTG", "PURE",
            # Biotech
            "BIIB", "MRNA", "BNTX", "NVAX", "SGEN", "ALNY", "BMRN", "RARE",
            # Financial Services
            "SQ", "PYPL", "V", "MA", "AXP", "DFS", "SYF", "ALLY",
            # E-commerce / Consumer Internet
            "ETSY", "W", "CHWY", "RVLV", "FTCH", "GRPN", "TRIP", "EXPE",
            # More sectors for diversity
            "ZM", "DOCU", "TWLO", "RING", "BOX", "DBX", "PLAN", "ASAN",
        ]

    def _get_tier4_universe(self) -> list[str]:
        """Tier 4: High-volume mid-caps - scan monthly.

        Returns smaller caps with sufficient liquidity for options.
        """
        # Additional liquid mid/small caps
        return [
            "RDFN", "OPEN", "COMP", "Z", "TREE", "PFSI", "GHLD", "RKT",
            "UWMC", "FAX", "CARG", "ROOT", "LMND", "ALKT", "PROG", "META",
            # Small cap tech
            "APPS", "NCNO", "SMAR", "APPF", "INTA", "BL", "EVBG", "AVLR",
            # Small cap biotech
            "ARWR", "IONS", "FOLD", "MRVI", "EDIT", "NTLA", "CRSP", "BEAM",
            # Small cap fintech
            "AFRM", "UPST", "LC", "SOFI", "LPRO", "NU", "HOOD", "COIN",
            # Emerging tech
            "PLTR", "AI", "PATH", "SNOW", "DDOG", "NET", "FROG", "MDB",
        ]

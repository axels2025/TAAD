"""Caching layer for scanner to reduce API calls and improve performance.

This module provides persistent caching for:
- Option chains (expirations, strikes, trading classes)
- Trend analysis results
- Qualified contract IDs

Cache is persisted to disk and automatically expires stale data.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger


class ScannerCache:
    """Persistent cache for scanner data to minimize API calls.

    The cache stores:
    - Option chains with expiration/strike data
    - Trend analysis results
    - Qualified contract metadata

    All entries include timestamps for automatic expiration.

    Example:
        >>> cache = ScannerCache()
        >>> cache.set_chain("AAPL", chain_data)
        >>> chain = cache.get_chain("AAPL")
        >>> if cache.is_chain_fresh("AAPL", max_age_hours=12):
        ...     print("Using cached chain")
    """

    def __init__(self, cache_dir: str | Path | None = None):
        """Initialize scanner cache.

        Args:
            cache_dir: Directory to store cache files (default: data/cache)
        """
        if cache_dir is None:
            cache_dir = Path.cwd() / "data" / "cache"

        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.chains_file = self.cache_dir / "option_chains.json"
        self.trends_file = self.cache_dir / "trend_analysis.json"
        self.contracts_file = self.cache_dir / "qualified_contracts.json"

        # Load existing caches
        self.chains_cache = self._load_json(self.chains_file)
        self.trends_cache = self._load_json(self.trends_file)
        self.contracts_cache = self._load_json(self.contracts_file)

        logger.info(f"Initialized ScannerCache at {self.cache_dir}")

    def _load_json(self, file_path: Path) -> dict:
        """Load JSON cache file.

        Args:
            file_path: Path to JSON file

        Returns:
            dict: Loaded data or empty dict
        """
        if file_path.exists():
            try:
                with open(file_path, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load cache from {file_path}: {e}")
                return {}
        return {}

    def _save_json(self, file_path: Path, data: dict) -> None:
        """Save data to JSON file.

        Args:
            file_path: Path to JSON file
            data: Data to save
        """
        try:
            with open(file_path, "w") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Could not save cache to {file_path}: {e}")

    def get_chain(self, symbol: str) -> dict | None:
        """Get cached option chain for symbol.

        Args:
            symbol: Stock symbol

        Returns:
            dict with chain data or None if not cached
        """
        return self.chains_cache.get(symbol, {}).get("data")

    def set_chain(self, symbol: str, chain_data: dict) -> None:
        """Cache option chain for symbol.

        Args:
            symbol: Stock symbol
            chain_data: Chain data from IBKR
        """
        # Convert sets to lists for JSON serialization
        serializable_data = {}
        for key, value in chain_data.items():
            if isinstance(value, set):
                serializable_data[key] = sorted(list(value))
            else:
                serializable_data[key] = value

        self.chains_cache[symbol] = {
            "data": serializable_data,
            "timestamp": datetime.now().isoformat(),
        }
        self._save_json(self.chains_file, self.chains_cache)
        logger.debug(f"Cached option chain for {symbol}")

    def get_trend(self, symbol: str) -> str | None:
        """Get cached trend analysis for symbol.

        Args:
            symbol: Stock symbol

        Returns:
            Trend string ("uptrend", "downtrend", "sideways") or None
        """
        return self.trends_cache.get(symbol, {}).get("trend")

    def set_trend(self, symbol: str, trend: str, trend_score: float = 0.0) -> None:
        """Cache trend analysis for symbol.

        Args:
            symbol: Stock symbol
            trend: Trend classification
            trend_score: Trend strength score
        """
        self.trends_cache[symbol] = {
            "trend": trend,
            "trend_score": trend_score,
            "timestamp": datetime.now().isoformat(),
        }
        self._save_json(self.trends_file, self.trends_cache)
        logger.debug(f"Cached trend for {symbol}: {trend}")

    def get_contract(self, symbol: str, key: str) -> int | None:
        """Get cached contract ID.

        Args:
            symbol: Stock symbol
            key: Contract key (e.g., "AAPL_20250207_150_P")

        Returns:
            Contract ID or None
        """
        symbol_contracts = self.contracts_cache.get(symbol, {})
        return symbol_contracts.get(key, {}).get("conId")

    def set_contract(self, symbol: str, key: str, con_id: int, metadata: dict | None = None) -> None:
        """Cache qualified contract.

        Args:
            symbol: Stock symbol
            key: Contract key
            con_id: Contract ID from IBKR
            metadata: Optional additional metadata
        """
        if symbol not in self.contracts_cache:
            self.contracts_cache[symbol] = {}

        self.contracts_cache[symbol][key] = {
            "conId": con_id,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {},
        }
        self._save_json(self.contracts_file, self.contracts_cache)
        logger.debug(f"Cached contract {key} for {symbol}")

    def is_chain_fresh(self, symbol: str, max_age_hours: int = 12) -> bool:
        """Check if cached chain is still fresh.

        Args:
            symbol: Stock symbol
            max_age_hours: Maximum age in hours

        Returns:
            bool: True if fresh, False otherwise
        """
        entry = self.chains_cache.get(symbol)
        if not entry:
            return False

        try:
            timestamp = datetime.fromisoformat(entry["timestamp"])
            age = datetime.now() - timestamp
            return age < timedelta(hours=max_age_hours)
        except Exception:
            return False

    def is_trend_fresh(self, symbol: str, max_age_hours: int = 24) -> bool:
        """Check if cached trend is still fresh.

        Args:
            symbol: Stock symbol
            max_age_hours: Maximum age in hours

        Returns:
            bool: True if fresh, False otherwise
        """
        entry = self.trends_cache.get(symbol)
        if not entry:
            return False

        try:
            timestamp = datetime.fromisoformat(entry["timestamp"])
            age = datetime.now() - timestamp
            return age < timedelta(hours=max_age_hours)
        except Exception:
            return False

    def clear_stale(self, max_age_hours: int = 48) -> dict[str, int]:
        """Remove stale entries from all caches.

        Args:
            max_age_hours: Maximum age before considering stale

        Returns:
            dict: Count of removed entries per cache type
        """
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        removed = {"chains": 0, "trends": 0, "contracts": 0}

        # Clear stale chains
        stale_chains = []
        for symbol, entry in self.chains_cache.items():
            try:
                timestamp = datetime.fromisoformat(entry["timestamp"])
                if timestamp < cutoff:
                    stale_chains.append(symbol)
            except Exception:
                stale_chains.append(symbol)

        for symbol in stale_chains:
            del self.chains_cache[symbol]
            removed["chains"] += 1

        # Clear stale trends
        stale_trends = []
        for symbol, entry in self.trends_cache.items():
            try:
                timestamp = datetime.fromisoformat(entry["timestamp"])
                if timestamp < cutoff:
                    stale_trends.append(symbol)
            except Exception:
                stale_trends.append(symbol)

        for symbol in stale_trends:
            del self.trends_cache[symbol]
            removed["trends"] += 1

        # Clear stale contracts
        stale_contracts = []
        for symbol in self.contracts_cache:
            for key, entry in list(self.contracts_cache[symbol].items()):
                try:
                    timestamp = datetime.fromisoformat(entry["timestamp"])
                    if timestamp < cutoff:
                        del self.contracts_cache[symbol][key]
                        removed["contracts"] += 1
                except Exception:
                    del self.contracts_cache[symbol][key]
                    removed["contracts"] += 1

            # Remove symbol if no contracts left
            if not self.contracts_cache[symbol]:
                stale_contracts.append(symbol)

        for symbol in stale_contracts:
            del self.contracts_cache[symbol]

        # Save cleaned caches
        if removed["chains"] > 0:
            self._save_json(self.chains_file, self.chains_cache)
        if removed["trends"] > 0:
            self._save_json(self.trends_file, self.trends_cache)
        if removed["contracts"] > 0:
            self._save_json(self.contracts_file, self.contracts_cache)

        logger.info(
            f"Cleared {removed['chains']} stale chains, "
            f"{removed['trends']} stale trends, "
            f"{removed['contracts']} stale contracts"
        )

        return removed

    def clear_all(self) -> None:
        """Clear all caches completely."""
        self.chains_cache = {}
        self.trends_cache = {}
        self.contracts_cache = {}

        self._save_json(self.chains_file, self.chains_cache)
        self._save_json(self.trends_file, self.trends_cache)
        self._save_json(self.contracts_file, self.contracts_cache)

        logger.info("Cleared all caches")

    def get_stats(self) -> dict[str, int]:
        """Get cache statistics.

        Returns:
            dict: Cache statistics
        """
        return {
            "chains_cached": len(self.chains_cache),
            "trends_cached": len(self.trends_cache),
            "contracts_cached": sum(
                len(contracts) for contracts in self.contracts_cache.values()
            ),
            "symbols_with_contracts": len(self.contracts_cache),
        }

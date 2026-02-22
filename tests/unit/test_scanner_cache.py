"""Unit tests for ScannerCache."""

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.tools.scanner_cache import ScannerCache


@pytest.fixture
def temp_cache_dir():
    """Create a temporary cache directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def cache(temp_cache_dir):
    """Create a ScannerCache instance with temp directory."""
    return ScannerCache(cache_dir=temp_cache_dir)


class TestCacheInitialization:
    """Test cache initialization."""

    def test_creates_cache_directory(self, temp_cache_dir):
        """Test cache directory is created."""
        cache = ScannerCache(cache_dir=temp_cache_dir)

        assert cache.cache_dir.exists()
        assert cache.cache_dir.is_dir()

    def test_creates_cache_files(self, temp_cache_dir):
        """Test cache files are created."""
        cache = ScannerCache(cache_dir=temp_cache_dir)

        # Files are created on first write, so write something
        cache.set_chain("AAPL", {"test": "data"})
        cache.set_trend("AAPL", "uptrend")
        cache.set_contract("AAPL", "test_key", 12345)

        assert cache.chains_file.exists()
        assert cache.trends_file.exists()
        assert cache.contracts_file.exists()


class TestChainCaching:
    """Test option chain caching."""

    def test_set_and_get_chain(self, cache):
        """Test setting and getting chain data."""
        chain_data = {
            "exchange": "SMART",
            "trading_class": "AAPL",
            "multiplier": "100",
            "expirations": {"20250207", "20250214"},
            "strikes": {150.0, 155.0, 160.0},
        }

        cache.set_chain("AAPL", chain_data)
        retrieved = cache.get_chain("AAPL")

        assert retrieved is not None
        assert retrieved["exchange"] == "SMART"
        assert retrieved["trading_class"] == "AAPL"
        # Sets are converted to lists for JSON
        assert set(retrieved["expirations"]) == chain_data["expirations"]
        assert set(retrieved["strikes"]) == chain_data["strikes"]

    def test_get_nonexistent_chain(self, cache):
        """Test getting chain that doesn't exist."""
        retrieved = cache.get_chain("NONEXISTENT")

        assert retrieved is None

    def test_chain_freshness(self, cache):
        """Test checking if chain is fresh."""
        chain_data = {"exchange": "SMART", "strikes": {100.0}}

        cache.set_chain("AAPL", chain_data)

        # Should be fresh immediately
        assert cache.is_chain_fresh("AAPL", max_age_hours=12)

        # Manually expire by modifying timestamp
        cache.chains_cache["AAPL"]["timestamp"] = (
            datetime.now() - timedelta(hours=24)
        ).isoformat()

        # Should not be fresh after 24 hours
        assert not cache.is_chain_fresh("AAPL", max_age_hours=12)


class TestTrendCaching:
    """Test trend analysis caching."""

    def test_set_and_get_trend(self, cache):
        """Test setting and getting trend data."""
        cache.set_trend("AAPL", "uptrend", trend_score=0.85)

        trend = cache.get_trend("AAPL")

        assert trend == "uptrend"

    def test_get_nonexistent_trend(self, cache):
        """Test getting trend that doesn't exist."""
        trend = cache.get_trend("NONEXISTENT")

        assert trend is None

    def test_trend_freshness(self, cache):
        """Test checking if trend is fresh."""
        cache.set_trend("AAPL", "uptrend")

        # Should be fresh immediately
        assert cache.is_trend_fresh("AAPL", max_age_hours=24)

        # Manually expire by modifying timestamp
        cache.trends_cache["AAPL"]["timestamp"] = (
            datetime.now() - timedelta(hours=48)
        ).isoformat()

        # Should not be fresh after 48 hours
        assert not cache.is_trend_fresh("AAPL", max_age_hours=24)


class TestContractCaching:
    """Test contract caching."""

    def test_set_and_get_contract(self, cache):
        """Test setting and getting contract ID."""
        cache.set_contract("AAPL", "AAPL_20250207_150_P", 123456)

        con_id = cache.get_contract("AAPL", "AAPL_20250207_150_P")

        assert con_id == 123456

    def test_get_nonexistent_contract(self, cache):
        """Test getting contract that doesn't exist."""
        con_id = cache.get_contract("AAPL", "nonexistent_key")

        assert con_id is None

    def test_multiple_contracts_per_symbol(self, cache):
        """Test storing multiple contracts for same symbol."""
        cache.set_contract("AAPL", "AAPL_20250207_150_P", 111111)
        cache.set_contract("AAPL", "AAPL_20250207_155_P", 222222)
        cache.set_contract("AAPL", "AAPL_20250214_150_P", 333333)

        assert cache.get_contract("AAPL", "AAPL_20250207_150_P") == 111111
        assert cache.get_contract("AAPL", "AAPL_20250207_155_P") == 222222
        assert cache.get_contract("AAPL", "AAPL_20250214_150_P") == 333333


class TestCacheExpiration:
    """Test cache expiration."""

    def test_clear_stale_chains(self, cache):
        """Test clearing stale chain entries."""
        # Add fresh chain
        cache.set_chain("AAPL", {"exchange": "SMART"})

        # Add stale chain by manually setting old timestamp
        cache.chains_cache["MSFT"] = {
            "data": {"exchange": "SMART"},
            "timestamp": (datetime.now() - timedelta(hours=72)).isoformat(),
        }
        cache._save_json(cache.chains_file, cache.chains_cache)

        # Clear stale entries (max 48 hours)
        removed = cache.clear_stale(max_age_hours=48)

        assert removed["chains"] == 1
        assert cache.get_chain("AAPL") is not None  # Fresh chain preserved
        assert cache.get_chain("MSFT") is None  # Stale chain removed

    def test_clear_stale_trends(self, cache):
        """Test clearing stale trend entries."""
        # Add fresh trend
        cache.set_trend("AAPL", "uptrend")

        # Add stale trend
        cache.trends_cache["MSFT"] = {
            "trend": "downtrend",
            "trend_score": 0.5,
            "timestamp": (datetime.now() - timedelta(hours=72)).isoformat(),
        }
        cache._save_json(cache.trends_file, cache.trends_cache)

        # Clear stale entries
        removed = cache.clear_stale(max_age_hours=48)

        assert removed["trends"] == 1
        assert cache.get_trend("AAPL") is not None  # Fresh trend preserved
        assert cache.get_trend("MSFT") is None  # Stale trend removed

    def test_clear_all(self, cache):
        """Test clearing all caches."""
        # Add data to all caches
        cache.set_chain("AAPL", {"exchange": "SMART"})
        cache.set_trend("AAPL", "uptrend")
        cache.set_contract("AAPL", "test_key", 12345)

        # Clear all
        cache.clear_all()

        assert cache.get_chain("AAPL") is None
        assert cache.get_trend("AAPL") is None
        assert cache.get_contract("AAPL", "test_key") is None


class TestCacheStats:
    """Test cache statistics."""

    def test_get_stats(self, cache):
        """Test getting cache statistics."""
        # Add some data
        cache.set_chain("AAPL", {"exchange": "SMART"})
        cache.set_chain("MSFT", {"exchange": "SMART"})
        cache.set_trend("AAPL", "uptrend")
        cache.set_contract("AAPL", "key1", 111)
        cache.set_contract("AAPL", "key2", 222)
        cache.set_contract("MSFT", "key1", 333)

        stats = cache.get_stats()

        assert stats["chains_cached"] == 2
        assert stats["trends_cached"] == 1
        assert stats["contracts_cached"] == 3
        assert stats["symbols_with_contracts"] == 2


class TestCachePersistence:
    """Test cache persistence to disk."""

    def test_cache_persists_across_instances(self, temp_cache_dir):
        """Test cache data persists across different instances."""
        # First instance - write data
        cache1 = ScannerCache(cache_dir=temp_cache_dir)
        cache1.set_chain("AAPL", {"exchange": "SMART", "strikes": {100.0}})
        cache1.set_trend("AAPL", "uptrend")

        # Second instance - read data
        cache2 = ScannerCache(cache_dir=temp_cache_dir)

        chain = cache2.get_chain("AAPL")
        trend = cache2.get_trend("AAPL")

        assert chain is not None
        assert chain["exchange"] == "SMART"
        assert trend == "uptrend"

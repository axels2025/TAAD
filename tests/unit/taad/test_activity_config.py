"""Tests for Activity Flex Query configuration and credential lookup."""

import pytest
from unittest.mock import patch


class TestGetFlexCredentialsForQuery:
    """Tests for Config.get_flex_credentials_for_query()."""

    def _make_config(self, **overrides):
        """Create a Config with test values, bypassing API key validation."""
        from src.config.base import Config

        defaults = {
            "anthropic_api_key": "sk-ant-test-key-for-unit-tests",
            "ibkr_flex_token_1": "test-token-1",
            "ibkr_flex_query_id_1": "daily-query-1",
            "ibkr_flex_account_1": "YOUR_ACCOUNT",
            "ibkr_flex_activity_query_last_month_1": "1399843",
            "ibkr_flex_activity_query_last_quarter_1": "1399853",
            "ibkr_flex_activity_query_last_year_1": "1399858",
        }
        defaults.update(overrides)
        return Config(**defaults)

    def test_daily_returns_trade_confirmation_query(self):
        """'daily' should return the trade confirmation query ID."""
        config = self._make_config()
        creds = config.get_flex_credentials_for_query("daily")
        assert creds is not None
        assert creds["query_id"] == "daily-query-1"
        assert creds["token"] == "test-token-1"
        assert creds["account_id"] == "YOUR_ACCOUNT"

    def test_last_month_returns_activity_query(self):
        """'last_month' should return the activity last_month query ID."""
        config = self._make_config()
        creds = config.get_flex_credentials_for_query("last_month")
        assert creds is not None
        assert creds["query_id"] == "1399843"
        assert creds["token"] == "test-token-1"
        assert creds["account_id"] == "YOUR_ACCOUNT"

    def test_last_quarter_returns_activity_query(self):
        """'last_quarter' should return the activity last_quarter query ID."""
        config = self._make_config()
        creds = config.get_flex_credentials_for_query("last_quarter")
        assert creds is not None
        assert creds["query_id"] == "1399853"

    def test_last_year_returns_activity_query(self):
        """'last_year' should return the activity last_year query ID."""
        config = self._make_config()
        creds = config.get_flex_credentials_for_query("last_year")
        assert creds is not None
        assert creds["query_id"] == "1399858"

    def test_activity_query_reuses_same_token(self):
        """Activity queries should reuse the same token as the account slot."""
        config = self._make_config()
        daily_creds = config.get_flex_credentials_for_query("daily")
        activity_creds = config.get_flex_credentials_for_query("last_month")
        assert daily_creds["token"] == activity_creds["token"]

    def test_filter_by_account_id(self):
        """Should filter to the correct account when specified."""
        config = self._make_config(
            ibkr_flex_token_2="test-token-2",
            ibkr_flex_query_id_2="daily-query-2",
            ibkr_flex_account_2="U9999999",
            ibkr_flex_activity_query_last_month_2="2222222",
        )
        creds = config.get_flex_credentials_for_query("last_month", "U9999999")
        assert creds is not None
        assert creds["query_id"] == "2222222"
        assert creds["account_id"] == "U9999999"
        assert creds["token"] == "test-token-2"

    def test_returns_none_when_activity_not_configured(self):
        """Should return None if the activity query ID is not set."""
        config = self._make_config(
            ibkr_flex_activity_query_last_month_1=None,
            ibkr_flex_activity_query_last_quarter_1=None,
            ibkr_flex_activity_query_last_year_1=None,
        )
        creds = config.get_flex_credentials_for_query("last_month")
        assert creds is None

    def test_returns_none_for_unknown_query_type(self):
        """Unknown query type should return None (not crash)."""
        config = self._make_config()
        creds = config.get_flex_credentials_for_query("nonexistent_period")
        assert creds is None

    def test_returns_none_when_account_not_found(self):
        """Should return None if the specified account is not configured."""
        config = self._make_config()
        creds = config.get_flex_credentials_for_query("last_month", "U0000000")
        assert creds is None

    def test_daily_with_no_trade_confirmation_configured(self):
        """'daily' with no query_id configured should return None."""
        config = self._make_config(
            ibkr_flex_query_id_1=None,
        )
        creds = config.get_flex_credentials_for_query("daily")
        assert creds is None

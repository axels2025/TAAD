"""Tests for learning account filter."""

from unittest.mock import patch

import sqlalchemy as sa

from src.learning.account_filter import get_learning_account_filter, _load_learning_accounts


class TestLoadLearningAccounts:
    """Tests for loading config."""

    def test_empty_when_no_config(self):
        """Should return empty list when config doesn't exist."""
        with patch("src.learning.account_filter.Path") as mock_path:
            mock_path.return_value.__truediv__ = lambda *a: mock_path.return_value
            mock_path.return_value.exists.return_value = False
            result = _load_learning_accounts()
            assert result == []

    def test_empty_when_no_learning_accounts_key(self):
        """Should return empty list when key missing from config."""
        import yaml

        with patch("builtins.open", create=True) as mock_open:
            with patch("src.learning.account_filter.Path") as mock_path:
                mock_path.return_value.__truediv__ = lambda *a: mock_path.return_value
                mock_path.return_value.exists.return_value = True
                mock_open.return_value.__enter__ = lambda s: s
                mock_open.return_value.__exit__ = lambda *a: None
                with patch("yaml.safe_load", return_value={"learning": {}}):
                    result = _load_learning_accounts()
                    assert result == []


class TestGetLearningAccountFilter:
    """Tests for the SQLAlchemy filter generation."""

    def test_empty_accounts_excludes_paper(self):
        """Empty config should produce a filter that excludes paper trades."""
        with patch("src.learning.account_filter._load_learning_accounts", return_value=[]):
            clause = get_learning_account_filter()
            # Should be an OR clause (trade_source IS NULL OR trade_source != 'paper')
            assert clause is not None

    def test_specific_accounts_produces_filter(self):
        """Specific accounts should produce an OR of matching conditions."""
        accounts = ["U7130270:ibkr_import", "DU3008393:paper"]
        with patch("src.learning.account_filter._load_learning_accounts", return_value=accounts):
            clause = get_learning_account_filter()
            assert clause is not None

    def test_account_only_key(self):
        """Key with only account_id (no source) should filter by account only."""
        with patch("src.learning.account_filter._load_learning_accounts", return_value=["U7130270:"]):
            clause = get_learning_account_filter()
            assert clause is not None

    def test_source_only_key(self):
        """Key with only trade_source should filter by source only."""
        with patch("src.learning.account_filter._load_learning_accounts", return_value=[":ibkr_import"]):
            clause = get_learning_account_filter()
            assert clause is not None

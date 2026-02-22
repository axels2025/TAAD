"""Tests for scanner settings model and YAML persistence."""

import pytest
import yaml

from src.agentic.scanner_settings import (
    BudgetSettings,
    FilterSettings,
    RankingWeights,
    ScannerSettings,
    load_scanner_settings,
    save_scanner_settings,
)


class TestFilterSettings:
    def test_defaults(self):
        f = FilterSettings()
        assert f.delta_min == 0.05
        assert f.delta_max == 0.30
        assert f.delta_target == 0.065
        assert f.min_premium == 0.30
        assert f.min_otm_pct == 0.10
        assert f.max_dte == 7
        assert f.dte_prefer_shortest is True


class TestRankingWeights:
    def test_defaults_sum_to_100(self):
        r = RankingWeights()
        assert r.safety + r.liquidity + r.ai_score + r.efficiency == 100

    def test_valid_custom_weights(self):
        r = RankingWeights(safety=50, liquidity=20, ai_score=20, efficiency=10)
        assert r.safety == 50

    def test_weights_not_summing_to_100_raises(self):
        with pytest.raises(ValueError, match="must sum to 100"):
            RankingWeights(safety=50, liquidity=30, ai_score=20, efficiency=20)

    def test_weights_all_zero_raises(self):
        with pytest.raises(ValueError, match="must sum to 100"):
            RankingWeights(safety=0, liquidity=0, ai_score=0, efficiency=0)


class TestBudgetSettings:
    def test_defaults(self):
        b = BudgetSettings()
        assert b.margin_budget_pct == 0.20
        assert b.max_positions == 10
        assert b.max_per_sector == 5
        assert b.price_threshold == 90.0
        assert b.max_contracts_expensive == 3
        assert b.max_contracts_cheap == 5


class TestScannerSettings:
    def test_defaults(self):
        s = ScannerSettings()
        assert s.filters.delta_target == 0.065
        assert s.ranking.safety == 40
        assert s.budget.margin_budget_pct == 0.20


class TestLoadSave:
    def test_load_defaults_when_no_file(self, tmp_path):
        """Load returns defaults when YAML file doesn't exist."""
        settings = load_scanner_settings(tmp_path / "nonexistent.yaml")
        assert settings.filters.delta_target == 0.065
        assert settings.ranking.safety == 40
        assert settings.budget.margin_budget_pct == 0.20

    def test_save_and_load_roundtrip(self, tmp_path):
        """Save then load produces identical settings."""
        path = tmp_path / "settings.yaml"
        original = ScannerSettings(
            filters=FilterSettings(delta_target=0.10, min_premium=0.50),
            ranking=RankingWeights(safety=60, liquidity=20, ai_score=10, efficiency=10),
            budget=BudgetSettings(margin_budget_pct=0.30, max_positions=5),
        )
        save_scanner_settings(original, path)
        loaded = load_scanner_settings(path)

        assert loaded.filters.delta_target == 0.10
        assert loaded.filters.min_premium == 0.50
        assert loaded.ranking.safety == 60
        assert loaded.budget.margin_budget_pct == 0.30
        assert loaded.budget.max_positions == 5

    def test_save_creates_parent_dirs(self, tmp_path):
        """Save creates parent directories if they don't exist."""
        path = tmp_path / "deep" / "nested" / "settings.yaml"
        save_scanner_settings(ScannerSettings(), path)
        assert path.exists()

    def test_load_from_real_default_file(self):
        """Load from config/scanner_settings.yaml returns valid settings."""
        settings = load_scanner_settings("config/scanner_settings.yaml")
        assert settings.ranking.safety + settings.ranking.liquidity + \
            settings.ranking.ai_score + settings.ranking.efficiency == 100

    def test_load_partial_yaml(self, tmp_path):
        """Load with only some keys still returns valid settings with defaults."""
        path = tmp_path / "partial.yaml"
        with open(path, "w") as f:
            yaml.dump({"filters": {"delta_target": 0.12}}, f)
        settings = load_scanner_settings(path)
        assert settings.filters.delta_target == 0.12
        assert settings.filters.delta_min == 0.05  # default preserved
        assert settings.ranking.safety == 40  # default preserved

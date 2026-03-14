"""Tests for Phase D: Regime-Aware Adaptation.

Tests D1 (regime parameter tables), D2 (term structure monitoring),
and D3 (auto-experiment on regime shifts).
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.data.models import Experiment, Trade
from src.learning.experiment_engine import ExperimentEngine
from src.learning.regime_adapter import (
    DEFAULT_REGIME_PARAMS,
    RegimeAdapter,
    RegimeExperimentManager,
    RegimeParameterTable,
    RegimeParameters,
    RegimeTransition,
    TermStructureMonitor,
    VIXTermStructure,
    classify_vix_regime,
)


# ============================================================================
# VIX Regime Classification
# ============================================================================


class TestClassifyVixRegime:
    """Tests for VIX regime classification."""

    def test_low_regime(self):
        assert classify_vix_regime(10.0) == "low"
        assert classify_vix_regime(14.9) == "low"

    def test_normal_regime(self):
        assert classify_vix_regime(15.0) == "normal"
        assert classify_vix_regime(19.9) == "normal"

    def test_elevated_regime(self):
        assert classify_vix_regime(20.0) == "elevated"
        assert classify_vix_regime(24.9) == "elevated"

    def test_high_regime(self):
        assert classify_vix_regime(25.0) == "high"
        assert classify_vix_regime(34.9) == "high"

    def test_extreme_regime(self):
        assert classify_vix_regime(35.0) == "extreme"
        assert classify_vix_regime(80.0) == "extreme"

    def test_boundary_values(self):
        """Test exact boundary values classify correctly."""
        assert classify_vix_regime(0.0) == "low"
        assert classify_vix_regime(15.0) == "normal"
        assert classify_vix_regime(20.0) == "elevated"
        assert classify_vix_regime(25.0) == "high"
        assert classify_vix_regime(35.0) == "extreme"


# ============================================================================
# D1: Regime Parameter Table
# ============================================================================


class TestRegimeParameterTable:
    """Tests for per-regime parameter resolution."""

    def test_default_params_for_each_regime(self):
        """Each regime should return valid default parameters."""
        table = RegimeParameterTable()

        for vix, expected_regime in [(10, "low"), (17, "normal"), (22, "elevated"), (30, "high"), (40, "extreme")]:
            params = table.get_params(vix)
            assert params.regime == expected_regime
            assert params.source == "default"
            assert params.profit_target > 0
            assert params.stop_loss < 0
            assert params.max_positions > 0

    def test_config_overrides_apply(self):
        """Config overrides should take precedence over defaults."""
        overrides = {
            "normal": {"profit_target": 0.80, "max_positions": 10},
        }
        table = RegimeParameterTable(config_overrides=overrides)

        params = table.get_params(17.0)
        assert params.profit_target == 0.80
        assert params.max_positions == 10
        assert params.source == "config"
        # Non-overridden fields should still come from defaults
        assert params.stop_loss == DEFAULT_REGIME_PARAMS["normal"]["stop_loss"]

    def test_learned_overrides_highest_priority(self):
        """Learned overrides should take precedence over config and defaults."""
        config_overrides = {"normal": {"profit_target": 0.80}}
        learned_overrides = {"normal": {"profit_target": 0.65}}

        table = RegimeParameterTable(
            config_overrides=config_overrides,
            learned_overrides=learned_overrides,
        )

        params = table.get_params(17.0)
        assert params.profit_target == 0.65
        assert params.source == "learned"

    def test_get_all_regimes(self):
        """Should return parameters for all 5 regimes."""
        table = RegimeParameterTable()
        all_params = table.get_all_regimes()

        assert len(all_params) == 5
        regimes = {p.regime for p in all_params}
        assert regimes == {"low", "normal", "elevated", "high", "extreme"}

    def test_update_learned(self):
        """update_learned should add overrides that affect future get_params."""
        table = RegimeParameterTable()

        # Before update
        params_before = table.get_params(17.0)
        assert params_before.source == "default"

        # Update
        table.update_learned("normal", {"profit_target": 0.60})

        # After update
        params_after = table.get_params(17.0)
        assert params_after.profit_target == 0.60
        assert params_after.source == "learned"

    def test_extreme_regime_most_conservative(self):
        """Extreme regime should have the tightest parameters."""
        table = RegimeParameterTable()

        extreme = table.get_params(40.0)
        normal = table.get_params(17.0)

        assert extreme.max_positions < normal.max_positions
        assert extreme.position_size_pct < normal.position_size_pct
        assert extreme.stop_loss > normal.stop_loss  # Less negative = tighter

    def test_to_dict_serialization(self):
        """RegimeParameters should serialise to dict cleanly."""
        table = RegimeParameterTable()
        params = table.get_params(17.0)
        d = params.to_dict()

        assert d["regime"] == "normal"
        assert isinstance(d["vix"], float)
        assert isinstance(d["profit_target"], float)


# ============================================================================
# D2: Term Structure Monitoring
# ============================================================================


@pytest.fixture
def mock_db():
    return MagicMock()


class TestTermStructureMonitor:
    """Tests for VIX direction analysis."""

    def test_rising_vix_unfavorable(self, mock_db):
        """VIX rising >15% over 5d should signal unfavorable."""
        # Mock trades showing VIX was 20 five days ago
        mock_trade = MagicMock()
        mock_trade.vix_at_entry = 20.0
        mock_trade.entry_date = datetime.now() - timedelta(days=6)

        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.all.return_value = [mock_trade]

        monitor = TermStructureMonitor(mock_db)
        result = monitor.analyse(current_vix=24.0)  # 20% increase

        assert result.direction == "rising"
        assert result.entry_signal == "unfavorable"
        assert result.vix_change_pct > 0.15

    def test_falling_vix_favorable(self, mock_db):
        """VIX falling >10% over 5d should signal favorable."""
        mock_trade = MagicMock()
        mock_trade.vix_at_entry = 25.0
        mock_trade.entry_date = datetime.now() - timedelta(days=6)

        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.all.return_value = [mock_trade]

        monitor = TermStructureMonitor(mock_db)
        result = monitor.analyse(current_vix=22.0)  # -12% decrease

        assert result.direction == "falling"
        assert result.entry_signal == "favorable"

    def test_stable_vix_neutral(self, mock_db):
        """Small VIX changes should signal neutral."""
        mock_trade = MagicMock()
        mock_trade.vix_at_entry = 20.0
        mock_trade.entry_date = datetime.now() - timedelta(days=6)

        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.all.return_value = [mock_trade]

        monitor = TermStructureMonitor(mock_db)
        result = monitor.analyse(current_vix=20.5)  # +2.5% — stable

        assert result.direction == "stable"
        assert result.entry_signal == "neutral"

    def test_no_historical_data(self, mock_db):
        """Should return neutral when no historical VIX data available."""
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.all.return_value = []
        # Also mock the fallback query
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.first.return_value = None

        monitor = TermStructureMonitor(mock_db)
        result = monitor.analyse(current_vix=20.0)

        assert result.direction == "stable"
        assert result.entry_signal == "neutral"

    def test_to_dict(self, mock_db):
        """VIXTermStructure should serialise cleanly."""
        ts = VIXTermStructure(
            current_vix=25.0,
            vix_5d_ago=22.0,
            vix_change_pct=0.136,
            direction="rising",
            entry_signal="neutral",
        )
        d = ts.to_dict()
        assert d["current_vix"] == 25.0
        assert d["direction"] == "rising"


# ============================================================================
# D3: Regime Experiment Manager
# ============================================================================


class TestRegimeExperimentManager:
    """Tests for auto-experiment on regime shifts."""

    def _make_manager(self, mock_db, active_experiments=None):
        exp_engine = MagicMock(spec=ExperimentEngine)
        exp_engine.get_active_experiments.return_value = active_experiments or []
        exp_engine.create_experiment.return_value = MagicMock(
            experiment_id="exp_test_001",
            name="Test",
            parameter_name="regime_high_profit_target",
        )

        table = RegimeParameterTable()
        manager = RegimeExperimentManager(mock_db, exp_engine, table, max_concurrent=2)
        return manager, exp_engine

    def test_no_transition_on_first_check(self, mock_db):
        """First VIX check should initialise regime, not trigger transition."""
        manager, _ = self._make_manager(mock_db)
        transition = manager.check_regime_transition(17.0)
        assert transition is None
        assert manager._last_regime == "normal"

    def test_transition_detected(self, mock_db):
        """Crossing a regime boundary should return a transition."""
        manager, _ = self._make_manager(mock_db)
        manager.check_regime_transition(17.0)  # Initialise as normal

        transition = manager.check_regime_transition(22.0)  # Now elevated
        assert transition is not None
        assert transition.from_regime == "normal"
        assert transition.to_regime == "elevated"

    def test_no_transition_within_regime(self, mock_db):
        """VIX moving within a regime should not trigger transition."""
        manager, _ = self._make_manager(mock_db)
        manager.check_regime_transition(16.0)  # Normal

        transition = manager.check_regime_transition(19.0)  # Still normal
        assert transition is None

    def test_transition_spawns_experiment(self, mock_db):
        """Regime transition should create an experiment via experiment engine."""
        manager, exp_engine = self._make_manager(mock_db)

        transition = RegimeTransition(
            from_regime="normal",
            to_regime="high",
            transition_vix=26.0,
            transition_date=datetime.now(),
        )

        result = manager.on_regime_transition(transition)

        assert result is not None
        exp_engine.create_experiment.assert_called_once()
        call_kwargs = exp_engine.create_experiment.call_args
        assert "high" in call_kwargs.kwargs.get("name", "") or "high" in call_kwargs.args[0] if call_kwargs.args else True

    def test_max_concurrent_prevents_new_experiment(self, mock_db):
        """Should not create experiment if max concurrent reached."""
        # Two active regime experiments
        active = [
            MagicMock(parameter_name="regime_elevated_profit_target"),
            MagicMock(parameter_name="regime_high_profit_target"),
        ]
        manager, exp_engine = self._make_manager(mock_db, active_experiments=active)

        transition = RegimeTransition(
            from_regime="normal",
            to_regime="extreme",
            transition_vix=36.0,
            transition_date=datetime.now(),
        )

        result = manager.on_regime_transition(transition)

        assert result is None
        exp_engine.create_experiment.assert_not_called()

    def test_duplicate_regime_experiment_prevented(self, mock_db):
        """Should not create experiment if one already exists for this regime."""
        active = [MagicMock(parameter_name="regime_high_profit_target")]
        manager, exp_engine = self._make_manager(mock_db, active_experiments=active)

        transition = RegimeTransition(
            from_regime="normal",
            to_regime="high",
            transition_vix=26.0,
            transition_date=datetime.now(),
        )

        result = manager.on_regime_transition(transition)

        assert result is None
        exp_engine.create_experiment.assert_not_called()


# ============================================================================
# Unified RegimeAdapter
# ============================================================================


class TestRegimeAdapter:
    """Tests for the unified adapter combining D1+D2+D3."""

    def test_get_current_params(self, mock_db):
        """Should return parameters for current VIX."""
        exp_engine = MagicMock(spec=ExperimentEngine)
        exp_engine.get_active_experiments.return_value = []

        adapter = RegimeAdapter(mock_db, exp_engine)
        params = adapter.get_current_params(22.0)

        assert params.regime == "elevated"
        assert params.profit_target > 0

    def test_on_vix_update_no_transition(self, mock_db):
        """VIX update within same regime should return None."""
        exp_engine = MagicMock(spec=ExperimentEngine)
        exp_engine.get_active_experiments.return_value = []

        adapter = RegimeAdapter(mock_db, exp_engine)
        adapter.on_vix_update(17.0)  # Initialise
        result = adapter.on_vix_update(18.0)  # Still normal

        assert result is None

    def test_analyse_returns_full_report(self, mock_db):
        """Full analysis should return a complete report."""
        exp_engine = MagicMock(spec=ExperimentEngine)
        exp_engine.get_active_experiments.return_value = []

        # Mock term structure queries
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.all.return_value = []
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.first.return_value = None
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.all.return_value = []
        # Mock regime experiments query
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        adapter = RegimeAdapter(mock_db, exp_engine)
        report = adapter.analyse(22.0)

        assert report.current_regime == "elevated"
        assert report.current_vix == 22.0
        assert report.active_params is not None
        assert report.term_structure is not None
        assert len(report.all_regime_params) == 5

    def test_config_overrides_flow_through(self, mock_db):
        """Config overrides should reach the parameter table."""
        exp_engine = MagicMock(spec=ExperimentEngine)
        exp_engine.get_active_experiments.return_value = []

        overrides = {"normal": {"profit_target": 0.90}}
        adapter = RegimeAdapter(mock_db, exp_engine, config_overrides=overrides)

        params = adapter.get_current_params(17.0)
        assert params.profit_target == 0.90
        assert params.source == "config"

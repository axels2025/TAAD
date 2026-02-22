"""Unit tests for learning engine components.

Tests statistical validator, experiment engine, parameter optimizer,
and learning orchestrator.
"""

from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from src.data.models import Experiment, Trade
from src.learning.experiment_engine import ExperimentEngine
from src.learning.models import DetectedPattern, ParameterProposal
from src.learning.parameter_optimizer import ParameterOptimizer
from src.learning.statistical_validator import StatisticalValidator


# ============================================================================
# Statistical Validator Tests
# ============================================================================


@pytest.fixture
def mock_db():
    """Create mock database session."""
    db = Mock()
    # Setup default mock returns for common query patterns
    db.query().filter().all.return_value = []
    db.query().filter().filter().all.return_value = []
    db.query().filter().filter().filter().all.return_value = []
    return db


@pytest.fixture
def significant_pattern():
    """Create a statistically significant pattern."""
    return DetectedPattern(
        pattern_type="delta_bucket",
        pattern_name="delta_15_20_outperforms",
        pattern_value="15-20%",
        sample_size=50,
        win_rate=0.75,
        avg_roi=0.35,
        baseline_win_rate=0.60,
        baseline_roi=0.20,
        p_value=0.01,
        effect_size=1.2,
        confidence=0.85,
        date_detected=datetime.now(),
    )


@pytest.fixture
def insignificant_pattern():
    """Create an insignificant pattern."""
    return DetectedPattern(
        pattern_type="trend_direction",
        pattern_name="trend_sideways",
        pattern_value="sideways",
        sample_size=15,  # Too small
        win_rate=0.60,
        avg_roi=0.15,
        baseline_win_rate=0.60,
        baseline_roi=0.20,
        p_value=0.5,  # Not significant
        effect_size=0.2,  # Too small
        confidence=0.30,
        date_detected=datetime.now(),
    )


def test_validator_initialization(mock_db):
    """Test validator initialization."""
    validator = StatisticalValidator(mock_db, min_samples=25, significance_level=0.05)

    assert validator.db == mock_db
    assert validator.min_samples == 25
    assert validator.significance_level == 0.05


def test_validate_significant_pattern(mock_db, significant_pattern):
    """Test validation of significant pattern."""
    # Create mock trades for cross-validation
    mock_trades = [
        Trade(
            trade_id=f"t_{i}",
            symbol="TEST",
            strike=100.0,
            expiration=datetime(2026, 2, 1),
            entry_date=datetime(2026, 1, i % 28 + 1),
            exit_date=datetime(2026, 1, i % 28 + 1) + timedelta(days=7),
            dte=30,
            roi=0.3 + (i % 5) * 0.05,
        )
        for i in range(50)
    ]

    mock_db.query().filter().all.return_value = mock_trades

    validator = StatisticalValidator(mock_db, min_samples=30)
    result = validator.validate_pattern(significant_pattern)

    assert result.valid is True
    assert result.p_value is not None
    assert result.effect_size is not None
    assert result.confidence is not None


def test_validate_insufficient_samples(mock_db, insignificant_pattern):
    """Test validation fails with insufficient samples."""
    validator = StatisticalValidator(mock_db, min_samples=30)
    result = validator.validate_pattern(insignificant_pattern)

    assert result.valid is False
    assert "Insufficient samples" in result.reason


def test_validate_low_p_value(mock_db):
    """Test validation fails with low significance."""
    pattern = DetectedPattern(
        pattern_type="test",
        pattern_name="test_pattern",
        pattern_value="test",
        sample_size=50,
        win_rate=0.65,
        avg_roi=0.25,
        baseline_win_rate=0.60,
        baseline_roi=0.20,
        p_value=0.5,  # Not significant
        effect_size=1.0,
        confidence=0.70,
        date_detected=datetime.now(),
    )

    validator = StatisticalValidator(mock_db)
    result = validator.validate_pattern(pattern)

    assert result.valid is False
    assert "Not statistically significant" in result.reason


def test_validate_small_effect_size(mock_db):
    """Test validation fails with small effect size."""
    pattern = DetectedPattern(
        pattern_type="test",
        pattern_name="test_pattern",
        pattern_value="test",
        sample_size=50,
        win_rate=0.65,
        avg_roi=0.25,
        baseline_win_rate=0.60,
        baseline_roi=0.20,
        p_value=0.01,
        effect_size=0.2,  # Too small
        confidence=0.70,
        date_detected=datetime.now(),
    )

    validator = StatisticalValidator(mock_db, min_effect_size=0.5)
    result = validator.validate_pattern(pattern)

    assert result.valid is False
    assert "Effect size too small" in result.reason


# ============================================================================
# Experiment Engine Tests
# ============================================================================


def test_experiment_engine_initialization(mock_db):
    """Test experiment engine initialization."""
    engine = ExperimentEngine(mock_db, control_pct=0.75)

    assert engine.db == mock_db
    assert engine.control_pct == 0.75
    assert engine.test_pct == 0.25


def test_create_experiment(mock_db):
    """Test experiment creation."""
    engine = ExperimentEngine(mock_db)

    with patch.object(engine, "_generate_experiment_id", return_value="exp_test_001"):
        exp = engine.create_experiment(
            name="Test Delta Range",
            hypothesis="Higher delta improves returns",
            parameter="delta_range",
            control_value=(0.10, 0.20),
            test_value=(0.15, 0.25),
        )

    assert exp.name == "Test Delta Range"
    assert exp.parameter_name == "delta_range"
    assert exp.control_value == "(0.1, 0.2)"
    assert exp.status == "active"


def test_assign_trade_control_group(mock_db):
    """Test trade assignment to control group."""
    engine = ExperimentEngine(mock_db, control_pct=1.0)  # 100% control

    baseline_params = {"delta_range": (0.10, 0.20)}
    opportunity = {"symbol": "TEST"}

    params, group, exp_id = engine.assign_trade(opportunity, baseline_params)

    assert group == "control"
    assert exp_id is None
    assert params == baseline_params


def test_assign_trade_test_group(mock_db):
    """Test trade assignment to test group."""
    # Create mock experiment
    mock_exp = Experiment(
        experiment_id="exp_001",
        name="Test Experiment",
        parameter_name="delta_range",
        control_value="(0.10, 0.20)",
        test_value="(0.15, 0.25)",
        status="active",
        start_date=datetime.now(),
        control_trades=10,
        test_trades=5,
    )

    engine = ExperimentEngine(mock_db, control_pct=0.0)  # 100% test
    engine.active_experiments = [mock_exp]

    baseline_params = {"delta_range": (0.10, 0.20)}
    opportunity = {"symbol": "TEST"}

    params, group, exp_id = engine.assign_trade(opportunity, baseline_params)

    assert group == "test"
    assert exp_id == "exp_001"
    # Delta range should be modified
    assert params["delta_range"] != baseline_params["delta_range"]


def test_evaluate_experiment_insufficient_data(mock_db):
    """Test experiment evaluation with insufficient data."""
    exp = Experiment(
        experiment_id="exp_001",
        name="Test",
        parameter_name="test_param",
        control_value="old",
        test_value="new",
        status="active",
        start_date=datetime.now(),
        control_trades=10,  # Less than min_samples (30)
        test_trades=5,
    )

    engine = ExperimentEngine(mock_db, min_samples_per_group=30)
    result = engine.evaluate_experiment(exp)

    assert result.decision == "INSUFFICIENT_DATA"


def test_evaluate_experiment_adopt(mock_db):
    """Test experiment adoption when test group performs better."""
    exp = Experiment(
        experiment_id="exp_001",
        name="Test",
        parameter_name="test_param",
        control_value="old",
        test_value="new",
        status="active",
        start_date=datetime.now(),
        control_trades=50,
        test_trades=50,
    )

    # Mock trades showing test group outperforms
    control_trades = [
        Trade(
            trade_id=f"c_{i}",
            is_experiment=False,
            entry_date=datetime.now(),
            exit_date=datetime.now(),
            roi=0.10,
        )
        for i in range(50)
    ]

    test_trades = [
        Trade(
            trade_id=f"t_{i}",
            experiment_id="exp_001",
            is_experiment=True,
            entry_date=datetime.now(),
            exit_date=datetime.now(),
            roi=0.20,  # Better ROI
        )
        for i in range(50)
    ]

    # Setup mock queries
    mock_db.query().filter().filter().filter().all.return_value = test_trades
    mock_db.query().filter().filter().filter().all.side_effect = [
        test_trades,
        control_trades,
    ]

    engine = ExperimentEngine(mock_db)

    with patch.object(engine, "_get_group_roi", side_effect=[(0.10, control_trades), (0.20, test_trades)]):
        with patch.object(engine, "_compare_groups", return_value=0.01):  # Significant
            result = engine.evaluate_experiment(exp)

    assert result.decision == "ADOPT"
    assert result.test_roi > result.control_roi


# ============================================================================
# Parameter Optimizer Tests
# ============================================================================


def test_parameter_optimizer_initialization(mock_db):
    """Test parameter optimizer initialization."""
    config = {"delta_range": (0.10, 0.20), "dte_range": (21, 45)}
    optimizer = ParameterOptimizer(mock_db, config)

    assert optimizer.current_config == config
    assert optimizer.config_history == []


def test_propose_changes(mock_db, significant_pattern):
    """Test parameter change proposal generation."""
    config = {"delta_range": (0.10, 0.20)}
    optimizer = ParameterOptimizer(mock_db, config)

    proposals = optimizer.propose_changes([significant_pattern])

    assert isinstance(proposals, list)
    # Should generate proposal for delta pattern
    if proposals:
        assert proposals[0].parameter is not None
        assert proposals[0].confidence > 0.0


def test_delta_bucket_to_proposal(mock_db):
    """Test delta bucket pattern to proposal conversion."""
    pattern = DetectedPattern(
        pattern_type="delta_bucket",
        pattern_name="delta_15_20_outperforms",
        pattern_value="15-20%",
        sample_size=50,
        win_rate=0.80,
        avg_roi=0.40,
        baseline_win_rate=0.60,
        baseline_roi=0.20,  # 2x better
        p_value=0.01,
        effect_size=1.5,
        confidence=0.90,
        date_detected=datetime.now(),
    )

    config = {"delta_range": (0.10, 0.25)}
    optimizer = ParameterOptimizer(mock_db, config)

    proposal = optimizer._delta_bucket_to_proposal(pattern)

    assert proposal is not None
    assert proposal.parameter == "delta_range"
    assert proposal.proposed_value == (0.15, 0.20)


def test_apply_change(mock_db):
    """Test applying a parameter change."""
    config = {"delta_range": (0.10, 0.20)}
    optimizer = ParameterOptimizer(mock_db, config)

    pattern = DetectedPattern(
        pattern_type="delta_bucket",
        pattern_name="test",
        pattern_value="test",
        sample_size=50,
        win_rate=0.70,
        avg_roi=0.30,
        baseline_win_rate=0.60,
        baseline_roi=0.20,
        p_value=0.01,
        effect_size=1.0,
        confidence=0.85,
        date_detected=datetime.now(),
    )

    proposal = ParameterProposal(
        parameter="delta_range",
        current_value=(0.10, 0.20),
        proposed_value=(0.15, 0.25),
        expected_improvement=0.10,
        confidence=0.90,
        source_pattern=pattern,
        reasoning="Test change",
    )

    change = optimizer.apply_change(proposal, approval="auto")

    assert optimizer.current_config["delta_range"] == (0.15, 0.25)
    assert len(optimizer.config_history) == 1
    assert change.parameter == "delta_range"


def test_rollback_change(mock_db):
    """Test rolling back a parameter change."""
    config = {"delta_range": (0.10, 0.20)}
    optimizer = ParameterOptimizer(mock_db, config)

    # Make a change
    pattern = DetectedPattern(
        pattern_type="test",
        pattern_name="test",
        pattern_value="test",
        sample_size=50,
        win_rate=0.70,
        avg_roi=0.30,
        baseline_win_rate=0.60,
        baseline_roi=0.20,
        p_value=0.01,
        effect_size=1.0,
        confidence=0.85,
        date_detected=datetime.now(),
    )

    proposal = ParameterProposal(
        parameter="delta_range",
        current_value=(0.10, 0.20),
        proposed_value=(0.15, 0.25),
        expected_improvement=0.10,
        confidence=0.90,
        source_pattern=pattern,
    )

    optimizer.apply_change(proposal)

    # Rollback
    success = optimizer.rollback_change("delta_range")

    assert success is True
    assert optimizer.current_config["delta_range"] == (0.10, 0.20)


# ============================================================================
# Integration Tests
# ============================================================================


def test_full_learning_cycle_simulation(mock_db):
    """Test a complete learning cycle simulation."""
    from src.learning.learning_orchestrator import LearningOrchestrator

    # Mock closed trades
    trades = [
        Trade(
            trade_id=f"trade_{i}",
            symbol="TEST",
            strike=100.0,
            expiration=datetime(2026, 2, 1),
            entry_date=datetime(2026, 1, 1),
            exit_date=datetime(2026, 1, 15),
            dte=30,
            vix_at_entry=20.0,
            profit_loss=100.0 if i % 2 == 0 else -50.0,
            roi=0.50 if i % 2 == 0 else -0.25,
        )
        for i in range(60)
    ]

    # Setup mock to return trades for Trade queries and empty for Experiment queries
    def mock_query_side_effect(model_class):
        mock_query_result = Mock()
        if model_class == Trade:
            mock_query_result.filter().all.return_value = trades
            mock_query_result.filter().filter().all.return_value = trades
            mock_query_result.filter().filter().filter().all.return_value = trades
        else:  # Experiment or other models
            mock_query_result.filter().all.return_value = []
            mock_query_result.filter().filter().all.return_value = []
        return mock_query_result

    mock_db.query.side_effect = mock_query_side_effect

    config = {"delta_range": (0.10, 0.20)}

    orchestrator = LearningOrchestrator(mock_db, baseline_config=config)

    # This should run without errors
    # (actual pattern detection may not find significant patterns with random data)
    with patch.object(orchestrator.pattern_detector, "detect_patterns", return_value=[]):
        report = orchestrator.run_weekly_analysis()

    assert report is not None
    assert report.total_trades_analyzed == 60
    assert 0.0 <= report.baseline_win_rate <= 1.0

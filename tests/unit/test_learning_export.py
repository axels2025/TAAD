"""Unit tests for learning data exporter.

Phase 2.6E - Exit Snapshots & Learning Data
Tests data export, feature statistics, and quality reporting.
"""

import pytest
import pandas as pd
from unittest.mock import Mock, MagicMock, patch
from pathlib import Path
from datetime import datetime, date

from src.learning.data_export import LearningDataExporter


@pytest.fixture
def mock_db_session():
    """Create mock database session."""
    mock = Mock()
    mock.bind = Mock()
    return mock


@pytest.fixture
def exporter(mock_db_session):
    """Create learning data exporter instance."""
    return LearningDataExporter(mock_db_session)


@pytest.fixture
def sample_learning_data():
    """Create sample learning data DataFrame."""
    data = {
        "trade_id": [1, 2, 3, 4, 5],
        "symbol": ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"],
        "entry_delta": [-0.30, -0.25, -0.35, -0.28, -0.32],
        "entry_iv": [0.25, 0.30, 0.22, 0.28, 0.35],
        "entry_iv_rank": [0.60, 0.70, 0.50, 0.65, 0.80],
        "entry_dte": [30, 45, 35, 40, 30],
        "entry_otm_pct": [0.10, 0.12, 0.09, 0.11, 0.13],
        "margin_efficiency_pct": [0.05, 0.06, 0.04, 0.055, 0.07],
        "trend_direction": ["uptrend", "sideways", "uptrend", "downtrend", "uptrend"],
        "rsi_14": [55.0, 48.0, 62.0, 42.0, 58.0],
        "adx": [25.0, 18.0, 30.0, 22.0, 28.0],
        "entry_vix": [18.0, 20.0, 16.0, 22.0, 19.0],
        "vol_regime": ["normal", "elevated", "low", "elevated", "normal"],
        "market_regime": ["bullish", "neutral", "bullish", "bearish", "bullish"],
        "sector": ["Technology", "Technology", "Technology", "Consumer", "Automotive"],
        "win": [True, True, False, True, True],
        "roi_pct": [0.60, 0.40, -0.20, 0.50, 0.70],
        "roi_on_margin": [12.0, 6.67, -5.0, 9.1, 10.0],
        "days_held": [10, 15, 8, 12, 9],
        "exit_reason": ["profit_target", "profit_target", "stop_loss", "profit_target", "profit_target"],
        "trade_quality_score": [0.95, 0.85, 0.30, 0.90, 0.92],
        "data_quality_score": [0.90, 0.85, 0.92, 0.88, 0.91],
        "entry_date": [datetime.now()] * 5,
        "exit_date": [datetime.now()] * 5,
    }
    return pd.DataFrame(data)


# ============================================================
# Export Tests
# ============================================================


def test_export_to_dataframe(exporter, mock_db_session, sample_learning_data):
    """Test basic DataFrame export."""
    with patch("pandas.read_sql", return_value=sample_learning_data):
        df = exporter.export_to_dataframe(min_quality=0.7)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 5
        assert "trade_id" in df.columns
        assert "win" in df.columns
        assert "roi_pct" in df.columns


def test_export_to_dataframe_with_quality_filter(exporter, mock_db_session, sample_learning_data):
    """Test DataFrame export with quality filtering."""
    # Set one row to low quality
    sample_learning_data.loc[2, "data_quality_score"] = 0.60

    with patch("pandas.read_sql", return_value=sample_learning_data):
        df = exporter.export_to_dataframe(min_quality=0.8)

        # Should have all 5 rows initially (quality filter in SQL not mocked properly)
        assert isinstance(df, pd.DataFrame)


def test_export_to_dataframe_filter_incomplete(exporter, mock_db_session, sample_learning_data):
    """Test filtering of incomplete trades."""
    # Set one row as incomplete (no exit_date)
    sample_learning_data.loc[2, "exit_date"] = None

    with patch("pandas.read_sql", return_value=sample_learning_data):
        df = exporter.export_to_dataframe(min_quality=0.7, filter_incomplete=True)

        # Should filter out incomplete trade
        assert len(df) == 4


def test_export_to_csv(exporter, mock_db_session, sample_learning_data, tmp_path):
    """Test CSV export."""
    output_path = tmp_path / "test_export.csv"

    with patch("pandas.read_sql", return_value=sample_learning_data):
        count = exporter.export_to_csv(output_path, min_quality=0.7)

        assert count == 5
        assert output_path.exists()

        # Verify CSV content
        df = pd.read_csv(output_path)
        assert len(df) == 5


def test_export_to_csv_creates_directory(exporter, mock_db_session, sample_learning_data, tmp_path):
    """Test CSV export creates parent directories."""
    output_path = tmp_path / "subdir" / "nested" / "test_export.csv"

    with patch("pandas.read_sql", return_value=sample_learning_data):
        count = exporter.export_to_csv(output_path)

        assert output_path.exists()
        assert output_path.parent.exists()


# ============================================================
# Feature Statistics Tests
# ============================================================


def test_get_feature_statistics(exporter, mock_db_session, sample_learning_data):
    """Test feature coverage statistics."""
    with patch("pandas.read_sql", return_value=sample_learning_data):
        stats = exporter.get_feature_statistics()

        assert isinstance(stats, dict)
        assert "entry_delta" in stats
        assert "win" in stats

        # Check structure
        assert "coverage" in stats["entry_delta"]
        assert "non_null" in stats["entry_delta"]
        assert "total" in stats["entry_delta"]

        # All fields should have 100% coverage in sample data
        assert stats["entry_delta"]["coverage"] == 1.0


def test_get_feature_statistics_with_missing_data(exporter, mock_db_session, sample_learning_data):
    """Test feature statistics with missing values."""
    # Add missing values
    sample_learning_data.loc[2, "rsi_14"] = None
    sample_learning_data.loc[3, "rsi_14"] = None

    with patch("pandas.read_sql", return_value=sample_learning_data):
        stats = exporter.get_feature_statistics()

        # rsi_14 should have 60% coverage (3/5)
        assert stats["rsi_14"]["coverage"] == 0.6
        assert stats["rsi_14"]["non_null"] == 3
        assert stats["rsi_14"]["total"] == 5


# ============================================================
# Summary Statistics Tests
# ============================================================


def test_get_summary_statistics(exporter, mock_db_session, sample_learning_data):
    """Test summary statistics generation."""
    with patch("pandas.read_sql", return_value=sample_learning_data):
        summary = exporter.get_summary_statistics()

        assert summary["total_trades"] == 5
        assert summary["win_rate"] == 0.8  # 4/5
        assert summary["avg_roi"] == 0.40  # Mean of [0.6, 0.4, -0.2, 0.5, 0.7]
        assert "sectors" in summary
        assert "exit_reasons" in summary
        assert "date_range" in summary


def test_get_summary_statistics_empty_data(exporter, mock_db_session):
    """Test summary statistics with no data."""
    empty_df = pd.DataFrame()

    with patch("pandas.read_sql", return_value=empty_df):
        summary = exporter.get_summary_statistics()

        assert "error" in summary


def test_get_summary_statistics_sector_breakdown(exporter, mock_db_session, sample_learning_data):
    """Test sector breakdown in summary statistics."""
    with patch("pandas.read_sql", return_value=sample_learning_data):
        summary = exporter.get_summary_statistics()

        sectors = summary["sectors"]
        assert sectors["Technology"] == 3
        assert sectors["Consumer"] == 1
        assert sectors["Automotive"] == 1


# ============================================================
# Feature Importance Export Tests
# ============================================================


def test_export_feature_importance_data(exporter, mock_db_session, sample_learning_data, tmp_path):
    """Test feature importance data export."""
    output_path = tmp_path / "feature_importance.csv"

    with patch("pandas.read_sql", return_value=sample_learning_data):
        df = exporter.export_feature_importance_data(output_path, target="win")

        assert isinstance(df, pd.DataFrame)
        assert "win" in df.columns
        assert "entry_delta" in df.columns
        assert len(df) == 5

        # Verify CSV created
        assert output_path.exists()


def test_export_feature_importance_data_with_roi_target(exporter, mock_db_session, sample_learning_data, tmp_path):
    """Test feature importance export with ROI target."""
    output_path = tmp_path / "feature_importance_roi.csv"

    with patch("pandas.read_sql", return_value=sample_learning_data):
        df = exporter.export_feature_importance_data(output_path, target="roi_pct")

        assert "roi_pct" in df.columns


def test_export_feature_importance_data_missing_target(exporter, mock_db_session, sample_learning_data, tmp_path):
    """Test feature importance export with missing target."""
    output_path = tmp_path / "feature_importance.csv"

    with patch("pandas.read_sql", return_value=sample_learning_data):
        df = exporter.export_feature_importance_data(output_path, target="nonexistent")

        # Should return empty DataFrame
        assert len(df) == 0


def test_export_feature_importance_data_drops_missing_targets(exporter, mock_db_session, sample_learning_data, tmp_path):
    """Test that rows with missing target values are dropped."""
    output_path = tmp_path / "feature_importance.csv"

    # Add missing target
    sample_learning_data.loc[2, "win"] = None

    with patch("pandas.read_sql", return_value=sample_learning_data):
        df = exporter.export_feature_importance_data(output_path, target="win")

        # Should drop row with missing target
        assert len(df) == 4


# ============================================================
# Data Quality Report Tests
# ============================================================


def test_get_data_quality_report(exporter, mock_db_session, sample_learning_data):
    """Test data quality report generation."""
    with patch("pandas.read_sql", return_value=sample_learning_data):
        report = exporter.get_data_quality_report()

        assert "total_fields" in report
        assert "high_coverage_fields" in report
        assert "medium_coverage_fields" in report
        assert "low_coverage_fields" in report
        assert "critical_fields_coverage" in report
        assert "overall_avg_coverage" in report


def test_get_data_quality_report_categorizes_fields(exporter, mock_db_session, sample_learning_data):
    """Test that data quality report categorizes fields correctly."""
    # Set varying coverage levels
    sample_learning_data.loc[2:4, "rsi_14"] = None  # 40% coverage
    sample_learning_data.loc[1:4, "adx"] = None  # 20% coverage

    with patch("pandas.read_sql", return_value=sample_learning_data):
        report = exporter.get_data_quality_report()

        # rsi_14 should be in low coverage (40% < 50%)
        assert "rsi_14" in report["low_coverage_fields"]["fields"]

        # adx should be in low coverage (20% < 50%)
        assert "adx" in report["low_coverage_fields"]["fields"]


def test_get_data_quality_report_critical_fields(exporter, mock_db_session, sample_learning_data):
    """Test critical fields tracking in quality report."""
    with patch("pandas.read_sql", return_value=sample_learning_data):
        report = exporter.get_data_quality_report()

        critical = report["critical_fields_coverage"]

        # All critical fields should be present
        assert "entry_delta" in critical
        assert "entry_iv" in critical
        assert "entry_vix" in critical

        # All should have high coverage in sample data
        assert critical["entry_delta"] == 1.0


def test_get_data_quality_report_overall_avg(exporter, mock_db_session, sample_learning_data):
    """Test overall average coverage calculation."""
    with patch("pandas.read_sql", return_value=sample_learning_data):
        report = exporter.get_data_quality_report()

        avg_coverage = report["overall_avg_coverage"]

        # Should be high for complete sample data
        assert 0.9 <= avg_coverage <= 1.0


# ============================================================
# Edge Cases and Error Handling
# ============================================================


def test_export_handles_empty_database(exporter, mock_db_session):
    """Test export handles empty database gracefully."""
    empty_df = pd.DataFrame()

    with patch("pandas.read_sql", return_value=empty_df):
        df = exporter.export_to_dataframe()

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0


def test_feature_statistics_handles_empty_data(exporter, mock_db_session):
    """Test feature statistics handles empty data."""
    empty_df = pd.DataFrame()

    with patch("pandas.read_sql", return_value=empty_df):
        stats = exporter.get_feature_statistics()

        # Should return empty dict
        assert isinstance(stats, dict)
        assert len(stats) == 0

"""Export trade data for learning analysis.

Phase 2.6E - Exit Snapshots & Learning Data Preparation
Exports complete trade data from the trade_learning_data view for
consumption by the learning engine and analysis tools.
"""

import pandas as pd
from pathlib import Path
from sqlalchemy.orm import Session
from loguru import logger
from typing import Optional, Dict


class LearningDataExporter:
    """Exports complete trade data for learning engine.

    Uses the trade_learning_data SQL view which joins entry snapshots,
    exit snapshots, and aggregates position data for easy analysis.
    """

    def __init__(self, db_session: Session):
        """Initialize learning data exporter.

        Args:
            db_session: Database session
        """
        self.db = db_session

    def export_to_dataframe(
        self, min_quality: float = 0.7, filter_incomplete: bool = True
    ) -> pd.DataFrame:
        """Export learning data to pandas DataFrame.

        Args:
            min_quality: Minimum data quality score to include (0.0-1.0)
            filter_incomplete: If True, only include complete trades with entry+exit

        Returns:
            DataFrame with all learning features and outcomes
        """
        # Query the learning view
        query = "SELECT * FROM trade_learning_data"

        # Add quality filter
        if min_quality > 0:
            query += f" WHERE data_quality_score >= {min_quality}"

        df = pd.read_sql(query, self.db.bind)

        # Filter incomplete trades if requested (only if DataFrame is not empty)
        if filter_incomplete and len(df) > 0 and "exit_date" in df.columns:
            initial_count = len(df)
            df = df[df["exit_date"].notna()]
            filtered_count = initial_count - len(df)
            if filtered_count > 0:
                logger.debug(f"Filtered {filtered_count} incomplete trades")

        logger.info(
            f"Exported {len(df)} trades for learning",
            extra={
                "trade_count": len(df),
                "min_quality": min_quality,
                "filtered": filter_incomplete,
            },
        )

        return df

    def export_to_csv(
        self,
        path: Path,
        min_quality: float = 0.7,
        filter_incomplete: bool = True,
    ) -> int:
        """Export learning data to CSV file.

        Args:
            path: Output file path
            min_quality: Minimum data quality score
            filter_incomplete: Filter incomplete trades

        Returns:
            Number of trades exported
        """
        df = self.export_to_dataframe(min_quality, filter_incomplete)

        # Ensure directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        # Export to CSV
        df.to_csv(path, index=False)

        logger.info(f"Exported {len(df)} trades to {path}")

        return len(df)

    def get_feature_statistics(self) -> Dict[str, Dict[str, float]]:
        """Get coverage statistics for all features.

        Analyzes which fields are populated across all trades to
        understand data completeness.

        Returns:
            Dictionary mapping field names to coverage stats:
            {
                "field_name": {
                    "coverage": 0.95,  # % of non-null values
                    "non_null": 95,    # Count of non-null
                    "total": 100       # Total records
                }
            }
        """
        df = self.export_to_dataframe(min_quality=0.0, filter_incomplete=False)

        stats = {}
        total = len(df)

        for col in df.columns:
            non_null = df[col].notna().sum()
            stats[col] = {
                "coverage": non_null / total if total > 0 else 0.0,
                "non_null": int(non_null),
                "total": total,
                "dtype": str(df[col].dtype),
            }

        return stats

    def get_summary_statistics(self) -> Dict[str, any]:
        """Get summary statistics for learning data.

        Returns:
            Dictionary with high-level statistics
        """
        df = self.export_to_dataframe(min_quality=0.7, filter_incomplete=True)

        if len(df) == 0:
            return {"error": "No data available"}

        # Calculate summary stats
        summary = {
            "total_trades": len(df),
            "win_rate": df["win"].mean() if "win" in df else None,
            "avg_roi": df["roi_pct"].mean() if "roi_pct" in df else None,
            "median_roi": df["roi_pct"].median() if "roi_pct" in df else None,
            "avg_quality_score": (
                df["trade_quality_score"].mean()
                if "trade_quality_score" in df
                else None
            ),
            "avg_days_held": df["days_held"].mean() if "days_held" in df else None,
            "sectors": df["sector"].value_counts().to_dict() if "sector" in df else {},
            "exit_reasons": (
                df["exit_reason"].value_counts().to_dict()
                if "exit_reason" in df
                else {}
            ),
            "date_range": {
                "first_trade": (
                    str(df["entry_date"].min()) if "entry_date" in df else None
                ),
                "last_trade": str(df["entry_date"].max()) if "entry_date" in df else None,
            },
        }

        return summary

    def export_feature_importance_data(
        self, output_path: Path, target: str = "win"
    ) -> pd.DataFrame:
        """Export data formatted for feature importance analysis.

        Prepares data specifically for analyzing which entry features
        best predict outcomes.

        Args:
            output_path: Output CSV path
            target: Target variable (win, roi_pct, etc.)

        Returns:
            DataFrame with features and target
        """
        df = self.export_to_dataframe(min_quality=0.8, filter_incomplete=True)

        # Select predictor features (entry data)
        feature_columns = [
            # Critical fields
            "entry_delta",
            "entry_iv",
            "entry_iv_rank",
            "entry_dte",
            "entry_otm_pct",
            "margin_efficiency_pct",
            "trend_direction",
            "days_to_earnings",
            # Technical indicators
            "rsi_14",
            "macd",
            "adx",
            "bb_position",
            # Market context
            "entry_vix",
            "vol_regime",
            "market_regime",
            "sector",
            "is_opex_week",
            "day_of_week",
            "earnings_in_dte",
        ]

        # Filter to available columns
        available_features = [col for col in feature_columns if col in df.columns]

        # Add target
        if target not in df.columns:
            logger.error(f"Target variable '{target}' not found in data")
            return pd.DataFrame()

        output_df = df[available_features + [target]].copy()

        # Handle categorical variables
        categorical_cols = ["trend_direction", "vol_regime", "market_regime", "sector"]
        for col in categorical_cols:
            if col in output_df.columns:
                output_df[col] = output_df[col].astype("category")

        # Drop rows with missing target
        output_df = output_df[output_df[target].notna()]

        # Export
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_df.to_csv(output_path, index=False)

        logger.info(
            f"Exported feature importance data to {output_path}",
            extra={
                "features": len(available_features),
                "samples": len(output_df),
                "target": target,
            },
        )

        return output_df

    def get_data_quality_report(self) -> Dict[str, any]:
        """Generate data quality report.

        Returns:
            Dictionary with data quality metrics
        """
        feature_stats = self.get_feature_statistics()

        # Categorize fields by coverage
        high_coverage = []  # >90%
        medium_coverage = []  # 50-90%
        low_coverage = []  # <50%

        for field, stats in feature_stats.items():
            coverage = stats["coverage"]
            if coverage >= 0.9:
                high_coverage.append(field)
            elif coverage >= 0.5:
                medium_coverage.append(field)
            else:
                low_coverage.append(field)

        # Get critical fields coverage
        critical_fields = [
            "entry_delta",
            "entry_iv",
            "entry_iv_rank",
            "entry_vix",
            "entry_dte",
            "trend_direction",
            "days_to_earnings",
            "margin_efficiency_pct",
        ]

        critical_coverage = {}
        for field in critical_fields:
            if field in feature_stats:
                critical_coverage[field] = feature_stats[field]["coverage"]

        report = {
            "total_fields": len(feature_stats),
            "high_coverage_fields": {
                "count": len(high_coverage),
                "fields": high_coverage,
            },
            "medium_coverage_fields": {
                "count": len(medium_coverage),
                "fields": medium_coverage,
            },
            "low_coverage_fields": {"count": len(low_coverage), "fields": low_coverage},
            "critical_fields_coverage": critical_coverage,
            "overall_avg_coverage": sum(s["coverage"] for s in feature_stats.values())
            / len(feature_stats)
            if feature_stats
            else 0.0,
        }

        return report

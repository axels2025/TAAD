"""Data aggregator for AI performance analysis.

Queries the database, computes aggregates, and builds an AnalysisContext
within token budgets. This is what gets sent to Claude — compressed
summaries, not raw trades.
"""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

from src.agents.models import (
    AnalysisContext,
    AnalysisDepth,
    ConfigSnapshot,
    DEPTH_DIMENSIONS,
    DEPTH_PATTERN_LIMITS,
    DimensionalBreakdown,
    ExperimentSummary,
    PatternSummary,
    PerformanceSummary,
    ProposalSummary,
)
from src.data.models import Experiment, LearningHistory, Pattern, Trade, TradeEntrySnapshot
from src.data.repositories import (
    ExperimentRepository,
    LearningHistoryRepository,
    PatternRepository,
    TradeRepository,
)


class DataAggregator:
    """Aggregates trading data into compressed summaries for AI analysis.

    Queries closed trades, patterns, experiments, and learning history,
    then compresses everything into an AnalysisContext that fits within
    token budgets.
    """

    def __init__(self, session: Session):
        """Initialize with a database session.

        Args:
            session: SQLAlchemy session
        """
        self.session = session
        self.trade_repo = TradeRepository(session)
        self.pattern_repo = PatternRepository(session)
        self.experiment_repo = ExperimentRepository(session)
        self.learning_repo = LearningHistoryRepository(session)

    def build_context(
        self,
        days: int = 90,
        depth: AnalysisDepth = AnalysisDepth.STANDARD,
        user_question: Optional[str] = None,
        account_id: Optional[str] = None,
    ) -> AnalysisContext:
        """Build the complete analysis context from database data.

        Args:
            days: Number of days of trade history to analyze
            depth: Analysis depth (controls pattern count, dimensions)
            user_question: Optional specific question from the user
            account_id: Optional IBKR account ID to filter trades

        Returns:
            AnalysisContext ready to be sent to Claude
        """
        logger.info(f"Building analysis context: {days} days, depth={depth.value}"
                     f"{f', account={account_id}' if account_id else ''}")

        closed_trades = self._get_closed_trades(days, account_id=account_id)

        if not closed_trades:
            logger.warning("No closed trades found for analysis period")
            return AnalysisContext(
                performance=PerformanceSummary(
                    total_trades=0, win_rate=0.0, avg_roi=0.0,
                    total_pnl=0.0, max_drawdown=0.0,
                ),
                analysis_period_days=days,
                depth=depth,
                user_question=user_question,
            )

        performance = self._compute_performance(closed_trades, days)
        patterns = self._get_top_patterns(depth)
        breakdowns = self._compute_breakdowns(closed_trades, depth)
        experiments = self._get_experiment_summaries()
        proposals = self._get_proposal_summaries()
        recent_events = self._get_recent_learning_events()
        config = self._get_config_snapshot()

        context = AnalysisContext(
            performance=performance,
            patterns=patterns,
            breakdowns=breakdowns,
            experiments=experiments,
            proposals=proposals,
            recent_learning_events=recent_events,
            config=config,
            analysis_period_days=days,
            depth=depth,
            user_question=user_question,
        )

        logger.info(
            f"Context built: {performance.total_trades} trades, "
            f"{len(patterns)} patterns, {len(breakdowns)} dimensions"
        )

        return context

    def _get_closed_trades(
        self, days: int, account_id: Optional[str] = None,
    ) -> list[Trade]:
        """Get closed trades within the analysis period."""
        all_closed = self.trade_repo.get_closed_trades(account_id=account_id)
        cutoff = datetime.now() - timedelta(days=days)
        return [t for t in all_closed if t.exit_date and t.exit_date >= cutoff]

    def _compute_performance(
        self, trades: list[Trade], days: int
    ) -> PerformanceSummary:
        """Compute high-level performance metrics.

        Args:
            trades: Closed trades in the analysis period
            days: Total analysis period in days

        Returns:
            PerformanceSummary with overall and recent metrics
        """
        total = len(trades)
        wins = sum(1 for t in trades if t.profit_loss and t.profit_loss > 0)
        win_rate = wins / total if total > 0 else 0.0

        pnl_values = [t.profit_loss for t in trades if t.profit_loss is not None]
        total_pnl = sum(pnl_values)

        roi_values = [t.roi for t in trades if t.roi is not None]
        avg_roi = sum(roi_values) / len(roi_values) if roi_values else 0.0

        # Max drawdown (cumulative P&L peak-to-trough)
        max_drawdown = self._compute_max_drawdown(trades)

        # Recent 30-day window for trend comparison
        recent_cutoff = datetime.now() - timedelta(days=30)
        recent = [t for t in trades if t.exit_date and t.exit_date >= recent_cutoff]
        recent_total = len(recent)
        recent_wins = sum(1 for t in recent if t.profit_loss and t.profit_loss > 0)
        recent_win_rate = recent_wins / recent_total if recent_total > 0 else 0.0
        recent_rois = [t.roi for t in recent if t.roi is not None]
        recent_avg_roi = sum(recent_rois) / len(recent_rois) if recent_rois else 0.0

        return PerformanceSummary(
            total_trades=total,
            win_rate=win_rate,
            avg_roi=avg_roi,
            total_pnl=total_pnl,
            max_drawdown=max_drawdown,
            recent_trades=recent_total,
            recent_win_rate=recent_win_rate,
            recent_avg_roi=recent_avg_roi,
        )

    def _compute_max_drawdown(self, trades: list[Trade]) -> float:
        """Compute maximum drawdown from cumulative P&L series.

        Args:
            trades: Closed trades sorted by exit date

        Returns:
            Maximum drawdown as a dollar amount (negative)
        """
        sorted_trades = sorted(
            [t for t in trades if t.exit_date and t.profit_loss is not None],
            key=lambda t: t.exit_date,
        )
        if not sorted_trades:
            return 0.0

        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0

        for trade in sorted_trades:
            cumulative += trade.profit_loss
            if cumulative > peak:
                peak = cumulative
            drawdown = cumulative - peak
            if drawdown < max_dd:
                max_dd = drawdown

        return max_dd

    def _get_top_patterns(self, depth: AnalysisDepth) -> list[PatternSummary]:
        """Get top patterns sorted by confidence, limited by depth.

        Args:
            depth: Analysis depth controlling how many patterns to include

        Returns:
            List of PatternSummary objects
        """
        limit = DEPTH_PATTERN_LIMITS[depth]
        db_patterns = self.pattern_repo.get_active_patterns()

        summaries = []
        for p in db_patterns[:limit]:
            # Determine direction based on whether pattern outperforms baseline
            # We use win_rate > 0.5 as a rough heuristic since we don't store
            # baseline in the Pattern model; the analyzer can refine this
            direction = "outperforming" if p.win_rate > 0.5 else "underperforming"

            summaries.append(PatternSummary(
                pattern_type=p.pattern_type,
                pattern_name=p.pattern_name,
                pattern_value=p.pattern_value or "",
                sample_size=p.sample_size,
                win_rate=p.win_rate,
                avg_roi=p.avg_roi,
                p_value=p.p_value,
                confidence=p.confidence,
                direction=direction,
            ))

        return summaries

    def _compute_breakdowns(
        self, trades: list[Trade], depth: AnalysisDepth,
    ) -> list[DimensionalBreakdown]:
        """Compute win rate and ROI bucketed by each dimension.

        Args:
            trades: Closed trades to analyze
            depth: Analysis depth controlling which dimensions to include

        Returns:
            List of DimensionalBreakdown objects
        """
        dimensions = DEPTH_DIMENSIONS[depth]
        breakdowns = []

        # Load entry snapshots for trades that have them
        trade_ids = [t.id for t in trades]
        snapshots_by_trade = self._load_snapshots(trade_ids)

        for dim in dimensions:
            buckets = self._bucket_by_dimension(trades, dim, snapshots_by_trade)
            if buckets:
                breakdowns.append(DimensionalBreakdown(dimension=dim, buckets=buckets))

        return breakdowns

    def _load_snapshots(self, trade_ids: list[int]) -> dict[int, TradeEntrySnapshot]:
        """Load entry snapshots indexed by trade ID."""
        if not trade_ids:
            return {}
        snapshots = (
            self.session.query(TradeEntrySnapshot)
            .filter(TradeEntrySnapshot.trade_id.in_(trade_ids))
            .all()
        )
        return {s.trade_id: s for s in snapshots}

    def _bucket_by_dimension(
        self,
        trades: list[Trade],
        dimension: str,
        snapshots: dict[int, TradeEntrySnapshot],
    ) -> list[dict]:
        """Group trades into buckets by a given dimension.

        Args:
            trades: Closed trades
            dimension: Dimension name (e.g. "sector", "delta_bucket")
            snapshots: Entry snapshots indexed by trade ID

        Returns:
            List of bucket dicts with label, trades, win_rate, avg_roi
        """
        groups: dict[str, list[Trade]] = defaultdict(list)

        for trade in trades:
            label = self._get_dimension_label(trade, dimension, snapshots.get(trade.id))
            if label:
                groups[label].append(trade)

        buckets = []
        for label, group_trades in sorted(groups.items()):
            total = len(group_trades)
            if total < 3:  # Skip tiny buckets
                continue
            wins = sum(1 for t in group_trades if t.profit_loss and t.profit_loss > 0)
            rois = [t.roi for t in group_trades if t.roi is not None]
            avg_roi = sum(rois) / len(rois) if rois else 0.0

            buckets.append({
                "label": label,
                "trades": total,
                "win_rate": round(wins / total, 3),
                "avg_roi": round(avg_roi, 4),
            })

        return buckets

    def _get_dimension_label(
        self,
        trade: Trade,
        dimension: str,
        snapshot: Optional[TradeEntrySnapshot],
    ) -> Optional[str]:
        """Extract a dimension label from a trade.

        Args:
            trade: The trade to extract from
            dimension: Dimension name
            snapshot: Optional entry snapshot for enriched data

        Returns:
            Bucket label string, or None if data not available
        """
        if dimension == "sector":
            return trade.sector or (snapshot.sector if snapshot else None)

        elif dimension == "delta_bucket":
            delta = snapshot.delta if snapshot else None
            if delta is None:
                return None
            abs_delta = abs(delta)
            if abs_delta < 0.10:
                return "0-10%"
            elif abs_delta < 0.15:
                return "10-15%"
            elif abs_delta < 0.20:
                return "15-20%"
            elif abs_delta < 0.25:
                return "20-25%"
            else:
                return "25%+"

        elif dimension == "dte_bucket":
            dte = trade.dte
            if dte is None:
                return None
            if dte <= 7:
                return "0-7 days"
            elif dte <= 14:
                return "7-14 days"
            elif dte <= 21:
                return "14-21 days"
            elif dte <= 30:
                return "21-30 days"
            else:
                return "30+ days"

        elif dimension == "vix_regime":
            vix = trade.vix_at_entry
            if vix is None and snapshot:
                vix = snapshot.vix
            if vix is None:
                return None
            if vix < 15:
                return "low (<15)"
            elif vix < 20:
                return "normal (15-20)"
            elif vix < 25:
                return "elevated (20-25)"
            else:
                return "high (25+)"

        elif dimension == "rsi_bucket":
            if not snapshot or snapshot.rsi_14 is None:
                return None
            rsi = snapshot.rsi_14
            if rsi < 30:
                return "oversold (<30)"
            elif rsi < 50:
                return "low (30-50)"
            elif rsi < 70:
                return "neutral (50-70)"
            else:
                return "overbought (70+)"

        elif dimension == "trend_direction":
            if snapshot and snapshot.trend_direction:
                return snapshot.trend_direction
            return trade.market_regime

        elif dimension == "entry_day":
            if trade.entry_date:
                return trade.entry_date.strftime("%A")
            return None

        elif dimension == "vol_regime":
            if snapshot and snapshot.vol_regime:
                return snapshot.vol_regime
            return None

        return None

    def _get_experiment_summaries(self) -> list[ExperimentSummary]:
        """Get summaries of active and recently completed experiments."""
        summaries = []

        # Active experiments
        for exp in self.experiment_repo.get_active_experiments():
            summaries.append(ExperimentSummary(
                experiment_id=exp.experiment_id,
                name=exp.name,
                parameter=exp.parameter_name,
                control_value=exp.control_value,
                test_value=exp.test_value,
                status=exp.status,
                control_trades=exp.control_trades or 0,
                test_trades=exp.test_trades or 0,
                p_value=exp.p_value,
                decision=exp.decision,
            ))

        # Last 5 completed experiments
        completed = self.experiment_repo.get_completed_experiments()
        for exp in completed[:5]:
            summaries.append(ExperimentSummary(
                experiment_id=exp.experiment_id,
                name=exp.name,
                parameter=exp.parameter_name,
                control_value=exp.control_value,
                test_value=exp.test_value,
                status=exp.status,
                control_trades=exp.control_trades or 0,
                test_trades=exp.test_trades or 0,
                p_value=exp.p_value,
                decision=exp.decision,
            ))

        return summaries

    def _get_proposal_summaries(self) -> list[ProposalSummary]:
        """Get current optimizer proposals from learning history.

        Since proposals are transient (from ParameterOptimizer), we
        reconstruct them from recent learning events of type
        'parameter_proposed' or use recent 'parameter_adjusted' events.
        """
        recent_events = self.learning_repo.get_by_event_type("parameter_adjusted")
        summaries = []

        for event in recent_events[:10]:
            summaries.append(ProposalSummary(
                parameter=event.parameter_changed or "",
                current_value=event.old_value or "",
                proposed_value=event.new_value or "",
                expected_improvement=event.expected_improvement or 0.0,
                confidence=event.confidence or 0.0,
                reasoning=event.reasoning or "",
            ))

        return summaries

    def _get_recent_learning_events(self, limit: int = 10) -> list[dict]:
        """Get recent learning events as simple dicts for the prompt."""
        events = self.learning_repo.get_recent_events(limit=limit)
        return [
            {
                "type": e.event_type,
                "date": e.event_date.isoformat() if e.event_date else "",
                "pattern": e.pattern_name or "",
                "parameter": e.parameter_changed or "",
                "change": f"{e.old_value} -> {e.new_value}" if e.old_value else "",
                "reasoning": e.reasoning or "",
            }
            for e in events
        ]

    def _get_config_snapshot(self) -> ConfigSnapshot:
        """Get current strategy configuration parameters."""
        try:
            from src.config.base import get_config
            config = get_config()
            return ConfigSnapshot(parameters={
                "paper_trading": config.paper_trading,
                "max_positions": config.max_positions,
                "premium_min": config.premium_min,
                "premium_max": config.premium_max,
                "premium_target": config.premium_target,
                "max_daily_loss": config.max_daily_loss,
                "max_margin_utilization": config.max_margin_utilization,
                "learning_enabled": config.learning_enabled,
                "experiment_allocation": config.experiment_allocation,
            })
        except Exception:
            return ConfigSnapshot()

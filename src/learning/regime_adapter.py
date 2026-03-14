"""Regime-Aware Adaptation — Phase D of the Learning Module Improvement Plan.

D1: VIX regime parameter tables (different targets per regime)
D2: Term structure monitoring (VIX direction as entry gate)
D3: Auto-experiment on regime shifts (A/B test adapted vs static params)
"""

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

import numpy as np
import sqlalchemy as sa
from loguru import logger
from sqlalchemy.orm import Session

from src.data.models import Experiment, LearningHistory, Trade
from src.learning.experiment_engine import ExperimentEngine
from src.utils.timezone import utc_now


# ============================================================================
# D1: Regime Parameter Tables
# ============================================================================

# Default parameter overrides per VIX regime.
# These are starting points — the learning engine refines them via experiments.
DEFAULT_REGIME_PARAMS = {
    "low": {
        # VIX < 15: premiums thin, be selective
        "profit_target": 0.50,       # Take profit earlier (premiums are small)
        "stop_loss": -2.0,           # Tighter stop (low vol = sudden moves hurt more)
        "max_positions": 5,
        "min_dte": 21,
        "max_dte": 45,
        "position_size_pct": 1.0,    # Normal sizing
        "entry_gate": "open",        # Allow entries
        "description": "Premiums thin — prioritise high-IV candidates, tighter stops",
    },
    "normal": {
        # VIX 15-20: optimal environment
        "profit_target": 0.75,
        "stop_loss": -3.0,
        "max_positions": 6,
        "min_dte": 21,
        "max_dte": 45,
        "position_size_pct": 1.0,
        "entry_gate": "open",
        "description": "Optimal environment — standard parameters",
    },
    "elevated": {
        # VIX 20-25: richer premiums, verify OTM buffers
        "profit_target": 0.65,       # Take profit slightly earlier
        "stop_loss": -3.0,
        "max_positions": 5,
        "min_dte": 25,               # Slightly longer DTE for buffer
        "max_dte": 45,
        "position_size_pct": 0.8,    # Reduce sizing 20%
        "entry_gate": "open",
        "description": "Richer premiums — verify OTM buffers, slight size reduction",
    },
    "high": {
        # VIX 25-35: stage with caution
        "profit_target": 0.50,       # Take profit faster
        "stop_loss": -2.0,           # Tighter stops
        "max_positions": 3,          # Fewer positions
        "min_dte": 30,               # More time for recovery
        "max_dte": 60,
        "position_size_pct": 0.5,    # Half sizing
        "entry_gate": "cautious",    # Extra entry validation
        "description": "Stage with caution — reduce count, tighter stops, half size",
    },
    "extreme": {
        # VIX > 35: defensive posture
        "profit_target": 0.40,       # Take profit very quickly
        "stop_loss": -1.5,           # Very tight stops
        "max_positions": 2,          # Minimal positions
        "min_dte": 35,               # Maximum time buffer
        "max_dte": 60,
        "position_size_pct": 0.25,   # Quarter sizing
        "entry_gate": "restricted",  # Near-closed for new entries
        "description": "Defensive — minimal new entries, tight risk management",
    },
}

# VIX thresholds aligned with market_context.py and alpha_decay_monitor.py
VIX_REGIME_THRESHOLDS = {
    "low": (0, 15),
    "normal": (15, 20),
    "elevated": (20, 25),
    "high": (25, 35),
    "extreme": (35, 100),
}


def classify_vix_regime(vix: float) -> str:
    """Classify VIX into a regime.

    Args:
        vix: Current VIX value

    Returns:
        Regime name: low, normal, elevated, high, extreme
    """
    for regime, (low, high) in VIX_REGIME_THRESHOLDS.items():
        if low <= vix < high:
            return regime
    return "extreme"


@dataclass
class RegimeParameters:
    """Resolved parameters for the current VIX regime."""

    regime: str
    vix: float
    profit_target: float
    stop_loss: float
    max_positions: int
    min_dte: int
    max_dte: int
    position_size_pct: float
    entry_gate: str
    description: str
    source: str = "default"  # "default", "config", "learned"

    def to_dict(self) -> dict:
        return {
            "regime": self.regime,
            "vix": round(self.vix, 2),
            "profit_target": self.profit_target,
            "stop_loss": self.stop_loss,
            "max_positions": self.max_positions,
            "min_dte": self.min_dte,
            "max_dte": self.max_dte,
            "position_size_pct": self.position_size_pct,
            "entry_gate": self.entry_gate,
            "description": self.description,
            "source": self.source,
        }


class RegimeParameterTable:
    """Maps VIX regimes to strategy parameter overrides (D1).

    Resolves the active parameter set based on current VIX, merging:
    1. Hardcoded defaults (DEFAULT_REGIME_PARAMS)
    2. YAML config overrides (from phase5.yaml)
    3. Learned overrides (from regime experiments)
    """

    def __init__(
        self,
        config_overrides: Optional[dict[str, dict]] = None,
        learned_overrides: Optional[dict[str, dict]] = None,
    ):
        """Initialize regime parameter table.

        Args:
            config_overrides: Per-regime overrides from YAML config
            learned_overrides: Per-regime overrides from learning engine
        """
        self.config_overrides = config_overrides or {}
        self.learned_overrides = learned_overrides or {}

    def get_params(self, vix: float) -> RegimeParameters:
        """Get resolved parameters for current VIX level.

        Merge order: defaults → config → learned (later overrides earlier).

        Args:
            vix: Current VIX value

        Returns:
            RegimeParameters for the active regime
        """
        regime = classify_vix_regime(vix)
        defaults = DEFAULT_REGIME_PARAMS.get(regime, DEFAULT_REGIME_PARAMS["normal"])

        # Start with defaults
        params = dict(defaults)
        source = "default"

        # Apply config overrides
        if regime in self.config_overrides:
            params.update(self.config_overrides[regime])
            source = "config"

        # Apply learned overrides (highest priority)
        if regime in self.learned_overrides:
            params.update(self.learned_overrides[regime])
            source = "learned"

        return RegimeParameters(
            regime=regime,
            vix=vix,
            profit_target=params.get("profit_target", 0.75),
            stop_loss=params.get("stop_loss", -3.0),
            max_positions=params.get("max_positions", 5),
            min_dte=params.get("min_dte", 21),
            max_dte=params.get("max_dte", 45),
            position_size_pct=params.get("position_size_pct", 1.0),
            entry_gate=params.get("entry_gate", "open"),
            description=params.get("description", ""),
            source=source,
        )

    def get_all_regimes(self) -> list[RegimeParameters]:
        """Get parameters for all regimes (for display).

        Returns:
            List of RegimeParameters, one per regime
        """
        results = []
        # Use midpoint VIX for each regime
        midpoints = {"low": 10, "normal": 17.5, "elevated": 22.5, "high": 30, "extreme": 40}
        for regime, vix in midpoints.items():
            results.append(self.get_params(vix))
        return results

    def update_learned(self, regime: str, overrides: dict) -> None:
        """Update learned overrides for a regime.

        Args:
            regime: VIX regime name
            overrides: Parameter overrides to apply
        """
        if regime not in self.learned_overrides:
            self.learned_overrides[regime] = {}
        self.learned_overrides[regime].update(overrides)
        logger.info(f"Updated learned overrides for {regime} regime: {overrides}")


# ============================================================================
# D2: Term Structure Monitoring
# ============================================================================

@dataclass
class VIXTermStructure:
    """VIX term structure analysis result."""

    current_vix: float
    vix_5d_ago: Optional[float] = None
    vix_change_pct: Optional[float] = None
    direction: str = "stable"      # "rising", "falling", "stable"
    rate_of_change: float = 0.0    # Normalised daily rate
    entry_signal: str = "neutral"  # "favorable", "neutral", "unfavorable"
    analysis_date: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "current_vix": round(self.current_vix, 2),
            "vix_5d_ago": round(self.vix_5d_ago, 2) if self.vix_5d_ago else None,
            "vix_change_pct": round(self.vix_change_pct, 4) if self.vix_change_pct else None,
            "direction": self.direction,
            "rate_of_change": round(self.rate_of_change, 4),
            "entry_signal": self.entry_signal,
            "analysis_date": self.analysis_date.isoformat() if self.analysis_date else None,
        }


class TermStructureMonitor:
    """Monitors VIX direction as a proxy for term structure (D2).

    Since VIX futures data isn't readily available via IBKR's standard
    API, we use VIX rate of change over the past 5 trading days as a
    proxy. Rising VIX = near-term fear increasing (like backwardation)
    = reduce selling. Falling VIX = normalising (like contango) = safe
    to sell.

    Thresholds:
    - Rising > 15% over 5d → unfavorable (reduce entries)
    - Falling > 10% over 5d → favorable (good for selling)
    - Otherwise → neutral
    """

    RISING_THRESHOLD = 0.15   # 15% increase over 5d
    FALLING_THRESHOLD = -0.10  # 10% decrease over 5d

    def __init__(self, db_session: Session):
        self.db = db_session

    def analyse(self, current_vix: float) -> VIXTermStructure:
        """Analyse VIX direction from recent trade history.

        Uses vix_at_entry from recent trades as a proxy for historical VIX.

        Args:
            current_vix: Current VIX value

        Returns:
            VIXTermStructure analysis
        """
        result = VIXTermStructure(
            current_vix=current_vix,
            analysis_date=utc_now(),
        )

        # Get VIX from trades ~5 trading days ago
        cutoff = utc_now() - timedelta(days=8)  # 8 calendar days ≈ 5 trading days
        recent_trades = (
            self.db.query(Trade.vix_at_entry, Trade.entry_date)
            .filter(Trade.entry_date >= cutoff)
            .filter(Trade.vix_at_entry.isnot(None))
            .order_by(Trade.entry_date.asc())
            .all()
        )

        if not recent_trades:
            # Fall back to learning_history for VIX data
            vix_5d = self._get_historical_vix(days_ago=5)
            if vix_5d is not None:
                result.vix_5d_ago = vix_5d
        else:
            # Use earliest VIX from ~5 days ago
            result.vix_5d_ago = recent_trades[0].vix_at_entry

        if result.vix_5d_ago and result.vix_5d_ago > 0:
            result.vix_change_pct = (current_vix - result.vix_5d_ago) / result.vix_5d_ago
            result.rate_of_change = result.vix_change_pct / 5  # Per-day rate

            if result.vix_change_pct > self.RISING_THRESHOLD:
                result.direction = "rising"
                result.entry_signal = "unfavorable"
            elif result.vix_change_pct < self.FALLING_THRESHOLD:
                result.direction = "falling"
                result.entry_signal = "favorable"
            else:
                result.direction = "stable"
                result.entry_signal = "neutral"
        else:
            result.direction = "stable"
            result.entry_signal = "neutral"

        return result

    def _get_historical_vix(self, days_ago: int) -> Optional[float]:
        """Try to get historical VIX from alpha decay analysis records.

        Args:
            days_ago: How many days back to look

        Returns:
            VIX value or None
        """
        cutoff_start = utc_now() - timedelta(days=days_ago + 2)
        cutoff_end = utc_now() - timedelta(days=days_ago - 2)

        trade = (
            self.db.query(Trade.vix_at_entry)
            .filter(Trade.entry_date.between(cutoff_start, cutoff_end))
            .filter(Trade.vix_at_entry.isnot(None))
            .order_by(Trade.entry_date.desc())
            .first()
        )

        return trade.vix_at_entry if trade else None


# ============================================================================
# D3: Auto-Experiment on Regime Shifts
# ============================================================================

@dataclass
class RegimeTransition:
    """Record of a VIX regime transition."""

    from_regime: str
    to_regime: str
    transition_vix: float
    transition_date: datetime

    def to_dict(self) -> dict:
        return {
            "from_regime": self.from_regime,
            "to_regime": self.to_regime,
            "transition_vix": round(self.transition_vix, 2),
            "transition_date": self.transition_date.isoformat(),
        }


class RegimeExperimentManager:
    """Manages regime-scoped A/B experiments (D3).

    When the VIX regime changes, spawns an experiment comparing:
    - Control: static baseline parameters (no regime adaptation)
    - Test: regime-adapted parameters from the RegimeParameterTable

    Only trades within the experiment's target regime are evaluated,
    building evidence for whether regime-specific tuning actually helps.
    """

    def __init__(
        self,
        db_session: Session,
        experiment_engine: ExperimentEngine,
        regime_table: RegimeParameterTable,
        max_concurrent: int = 2,
    ):
        """Initialize regime experiment manager.

        Args:
            db_session: Database session
            experiment_engine: Existing experiment engine
            regime_table: Regime parameter table
            max_concurrent: Max concurrent regime experiments
        """
        self.db = db_session
        self.experiment_engine = experiment_engine
        self.regime_table = regime_table
        self.max_concurrent = max_concurrent
        self._last_regime: Optional[str] = None

    def check_regime_transition(self, current_vix: float) -> Optional[RegimeTransition]:
        """Check if VIX has crossed a regime boundary.

        Args:
            current_vix: Current VIX value

        Returns:
            RegimeTransition if regime changed, None otherwise
        """
        current_regime = classify_vix_regime(current_vix)

        if self._last_regime is None:
            # First check — initialise without triggering transition
            self._last_regime = current_regime
            return None

        if current_regime != self._last_regime:
            transition = RegimeTransition(
                from_regime=self._last_regime,
                to_regime=current_regime,
                transition_vix=current_vix,
                transition_date=utc_now(),
            )
            self._last_regime = current_regime
            return transition

        return None

    def on_regime_transition(
        self, transition: RegimeTransition
    ) -> Optional[Experiment]:
        """Handle a regime transition by spawning an experiment.

        Args:
            transition: The regime transition that occurred

        Returns:
            Created Experiment, or None if skipped
        """
        logger.info(
            f"Regime transition: {transition.from_regime} → {transition.to_regime} "
            f"(VIX={transition.transition_vix:.1f})"
        )

        # Check if we already have a regime experiment for this regime
        active_regime_exps = [
            e for e in self.experiment_engine.get_active_experiments()
            if e.parameter_name.startswith("regime_")
        ]

        if len(active_regime_exps) >= self.max_concurrent:
            logger.info(
                f"Skipping regime experiment — {len(active_regime_exps)} already active "
                f"(max {self.max_concurrent})"
            )
            return None

        # Check if there's already an experiment for this specific regime
        target_regime = transition.to_regime
        existing = [
            e for e in active_regime_exps
            if e.parameter_name == f"regime_{target_regime}_profit_target"
        ]
        if existing:
            logger.info(f"Regime experiment for {target_regime} already active, skipping")
            return None

        # Get regime-adapted parameters
        adapted = self.regime_table.get_params(transition.transition_vix)

        # Create experiment: adapted profit_target vs static baseline
        # We test profit_target as the highest-impact parameter
        exp = self.experiment_engine.create_experiment(
            name=f"Regime-adapted {target_regime} profit target",
            hypothesis=(
                f"In {target_regime} VIX regime, a profit target of "
                f"{adapted.profit_target:.0%} outperforms the static baseline"
            ),
            parameter=f"regime_{target_regime}_profit_target",
            control_value=0.75,  # Static baseline
            test_value=adapted.profit_target,
            min_samples=20,
            max_duration_days=60,
        )

        # Log the transition
        self._log_transition(transition, exp)

        logger.info(
            f"Created regime experiment: profit_target {0.75} vs {adapted.profit_target} "
            f"for {target_regime} regime"
        )

        return exp

    def get_regime_experiments(self) -> list[dict]:
        """Get summary of all regime experiments (active + completed).

        Returns:
            List of experiment summaries
        """
        experiments = (
            self.db.query(Experiment)
            .filter(Experiment.parameter_name.like("regime_%"))
            .order_by(Experiment.start_date.desc())
            .all()
        )

        results = []
        for exp in experiments:
            results.append({
                "name": exp.name,
                "parameter": exp.parameter_name,
                "control": exp.control_value,
                "test": exp.test_value,
                "status": exp.status,
                "control_trades": exp.control_trades,
                "test_trades": exp.test_trades,
                "p_value": exp.p_value,
                "effect_size": exp.effect_size,
                "start_date": exp.start_date.isoformat() if exp.start_date else None,
            })

        return results

    def _log_transition(self, transition: RegimeTransition, exp: Optional[Experiment]) -> None:
        """Log regime transition to learning_history.

        Args:
            transition: The transition that occurred
            exp: Experiment created (if any)
        """
        details = transition.to_dict()
        if exp:
            details["experiment_id"] = exp.experiment_id

        event = LearningHistory(
            event_type="regime_transition",
            event_date=utc_now(),
            pattern_name=f"{transition.from_regime}_to_{transition.to_regime}",
            reasoning=json.dumps(details),
        )
        self.db.add(event)
        self.db.commit()


# ============================================================================
# Unified Regime Adapter (combines D1 + D2 + D3)
# ============================================================================

@dataclass
class RegimeAdaptationReport:
    """Complete regime adaptation analysis report."""

    timestamp: datetime
    current_regime: str
    current_vix: float
    active_params: Optional[RegimeParameters] = None
    term_structure: Optional[VIXTermStructure] = None
    regime_experiments: list[dict] = field(default_factory=list)
    all_regime_params: list[RegimeParameters] = field(default_factory=list)
    recent_transitions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "current_regime": self.current_regime,
            "current_vix": round(self.current_vix, 2),
            "active_params": self.active_params.to_dict() if self.active_params else None,
            "term_structure": self.term_structure.to_dict() if self.term_structure else None,
            "regime_experiments": self.regime_experiments,
            "all_regime_params": [r.to_dict() for r in self.all_regime_params],
            "recent_transitions": self.recent_transitions,
        }


class RegimeAdapter:
    """Unified regime-aware adaptation engine.

    Combines:
    - D1: RegimeParameterTable for per-regime parameter overrides
    - D2: TermStructureMonitor for VIX direction signals
    - D3: RegimeExperimentManager for regime-scoped A/B tests

    Usage:
        adapter = RegimeAdapter(db_session, experiment_engine)
        report = adapter.analyse(current_vix=25.3)
        params = adapter.get_current_params(vix=25.3)
    """

    def __init__(
        self,
        db_session: Session,
        experiment_engine: ExperimentEngine,
        config_overrides: Optional[dict[str, dict]] = None,
    ):
        """Initialize regime adapter.

        Args:
            db_session: Database session
            experiment_engine: Existing experiment engine
            config_overrides: Per-regime config overrides from YAML
        """
        self.db = db_session
        self.regime_table = RegimeParameterTable(config_overrides=config_overrides)
        self.term_monitor = TermStructureMonitor(db_session)
        self.regime_experiments = RegimeExperimentManager(
            db_session, experiment_engine, self.regime_table
        )

    def get_current_params(self, vix: float) -> RegimeParameters:
        """Get active parameters for current VIX level.

        Args:
            vix: Current VIX value

        Returns:
            RegimeParameters for the active regime
        """
        return self.regime_table.get_params(vix)

    def on_vix_update(self, current_vix: float) -> Optional[Experiment]:
        """Called when VIX updates — checks for regime transitions.

        Args:
            current_vix: Current VIX value

        Returns:
            Experiment if regime transition spawned one, else None
        """
        transition = self.regime_experiments.check_regime_transition(current_vix)
        if transition:
            return self.regime_experiments.on_regime_transition(transition)
        return None

    def analyse(self, current_vix: float) -> RegimeAdaptationReport:
        """Run full regime adaptation analysis.

        Args:
            current_vix: Current VIX value

        Returns:
            RegimeAdaptationReport with all details
        """
        regime = classify_vix_regime(current_vix)

        report = RegimeAdaptationReport(
            timestamp=utc_now(),
            current_regime=regime,
            current_vix=current_vix,
        )

        # D1: Get current and all regime parameters
        report.active_params = self.regime_table.get_params(current_vix)
        report.all_regime_params = self.regime_table.get_all_regimes()

        # D2: Term structure analysis
        report.term_structure = self.term_monitor.analyse(current_vix)

        # D3: Regime experiments
        report.regime_experiments = self.regime_experiments.get_regime_experiments()

        # Recent transitions from learning_history
        report.recent_transitions = self._get_recent_transitions(days=30)

        # Persist analysis
        self._save_analysis(report)

        return report

    def _get_recent_transitions(self, days: int = 30) -> list[dict]:
        """Get recent regime transitions from learning history.

        Args:
            days: How many days to look back

        Returns:
            List of transition records
        """
        cutoff = utc_now() - timedelta(days=days)
        events = (
            self.db.query(LearningHistory)
            .filter(LearningHistory.event_type == "regime_transition")
            .filter(LearningHistory.event_date >= cutoff)
            .order_by(LearningHistory.event_date.desc())
            .all()
        )

        results = []
        for e in events:
            try:
                details = json.loads(e.reasoning) if e.reasoning else {}
            except (json.JSONDecodeError, TypeError):
                details = {}
            results.append({
                "date": e.event_date.isoformat() if e.event_date else None,
                "pattern": e.pattern_name,
                **details,
            })

        return results

    def _save_analysis(self, report: RegimeAdaptationReport) -> None:
        """Persist regime analysis to learning_history.

        Args:
            report: The analysis report
        """
        summary = {
            "regime": report.current_regime,
            "vix": round(report.current_vix, 2),
            "entry_signal": report.term_structure.entry_signal if report.term_structure else "unknown",
            "vix_direction": report.term_structure.direction if report.term_structure else "unknown",
            "active_experiments": len(report.regime_experiments),
        }

        event = LearningHistory(
            event_type="regime_adaptation_analysis",
            event_date=utc_now(),
            pattern_name=f"regime_{report.current_regime}",
            reasoning=json.dumps(summary),
        )
        self.db.add(event)
        self.db.commit()

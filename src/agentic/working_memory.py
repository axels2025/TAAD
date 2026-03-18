"""Crash-safe working memory for the agentic daemon.

PostgreSQL-backed context store using single-row upsert pattern.
Loads prior state on startup (never starts empty if history exists).
Supports pgvector semantic search for past decision retrieval.
"""

import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from loguru import logger
from sqlalchemy.orm import Session

from src.data.models import (
    DecisionAudit,
    DecisionEmbedding,
    Pattern,
    Position,
    Trade,
    WorkingMemoryRow,
)
from src.utils.timezone import utc_now

# Maximum recent decisions to keep in memory
MAX_RECENT_DECISIONS = 50


@dataclass
class ReasoningContext:
    """Assembled context for Claude reasoning.

    Built by WorkingMemory.assemble_context() from current state,
    open positions, recent decisions, patterns, and experiments.
    """

    # Current state
    autonomy_level: int = 1
    strategy_state: dict = field(default_factory=dict)
    market_context: dict = field(default_factory=dict)

    # Positions
    open_positions: list[dict] = field(default_factory=list)
    held_stocks: list[dict] = field(default_factory=list)
    positions_summary: str = ""

    # Recent history
    recent_decisions: list[dict] = field(default_factory=list)
    recent_trades: list[dict] = field(default_factory=list)

    # Learning context
    active_patterns: list[dict] = field(default_factory=list)
    active_experiments: list[dict] = field(default_factory=list)

    # Anomalies
    anomalies: list[dict] = field(default_factory=list)

    # Reflections
    latest_reflection: Optional[dict] = None

    # Staged trade candidates (from ScanOpportunity)
    staged_candidates: list[dict] = field(default_factory=list)

    # Similar past decisions (from pgvector search)
    similar_decisions: list[dict] = field(default_factory=list)

    # Data limitations (set by ContextValidator)
    data_limitations: list[str] = field(default_factory=list)

    def to_prompt_string(self) -> str:
        """Serialize context to a structured prompt string for Claude.

        Includes symbol scope, data timestamp, and data limitations
        to ground Claude's reasoning in the provided data.

        Returns:
            Formatted string for inclusion in Claude prompt
        """
        sections = []

        # Data timestamp and trading day context for grounding
        from src.config.exchange_profile import get_active_profile

        profile = get_active_profile()
        now_market = datetime.now(profile.timezone)
        day_name = now_market.strftime("%A")
        market_label = profile.code  # "US" or "ASX"
        sections.append(
            f"## Data as of: {now_market.strftime('%Y-%m-%d %H:%M:%S %Z')} "
            f"({day_name} in {market_label} markets)"
        )

        # Symbols in scope (explicit list for grounding)
        symbols_in_scope = set()
        for pos in self.open_positions:
            sym = pos.get("symbol", "")
            if sym:
                symbols_in_scope.add(sym)
        for cand in self.staged_candidates:
            sym = cand.get("symbol", "")
            if sym:
                symbols_in_scope.add(sym)
        for trade in self.recent_trades:
            sym = trade.get("symbol", "")
            if sym:
                symbols_in_scope.add(sym)
        if symbols_in_scope:
            sections.append(f"\n## Symbols in Scope: [{', '.join(sorted(symbols_in_scope))}]")

        # Data limitations (set by ContextValidator)
        if self.data_limitations:
            sections.append("\n## Data Limitations")
            for lim in self.data_limitations:
                sections.append(f"  - {lim}")

        sections.append(f"\n## Autonomy Level: L{self.autonomy_level}")

        if self.held_stocks:
            sections.append(f"\n## Held Stock Positions ({len(self.held_stocks)}) [source: stock_positions DB]")
            for stock in self.held_stocks:
                cc_tid = stock.get("covered_call_trade")
                cc_label = f" | covered call: {cc_tid}" if cc_tid else " | NO covered call active"
                sections.append(
                    f"  - {stock.get('symbol', '?')} x{stock.get('shares', '?')} shares "
                    f"cost_basis=${stock.get('cost_basis', 0):.2f} "
                    f"(from put assignment {stock.get('origin_trade', '?')}){cc_label}"
                )

        if self.open_positions:
            sections.append(f"\n## Open Positions ({len(self.open_positions)}) [source: trades DB + IBKR]")
            for pos in self.open_positions:
                opt_type = pos.get("option_type", "PUT")[0]  # "P" or "C"
                pnl_str = pos.get("pnl_pct", "?")
                source = pos.get("pnl_source", "")
                if source:
                    pnl_str = f"{pnl_str} ({source})"
                # Covered call annotation
                cc_tag = ""
                if pos.get("is_covered_call"):
                    cc_tag = " [COVERED CALL]" if pos.get("fully_covered") else " [PARTIALLY COVERED]"
                sections.append(
                    f"  - [{pos.get('trade_id', '?')}] {pos.get('symbol', '?')} "
                    f"{pos.get('strike', '?')}{opt_type} "
                    f"exp={pos.get('expiration', '?')} DTE={pos.get('dte', '?')} "
                    f"P&L={pnl_str}{cc_tag}"
                )

        if self.market_context:
            sections.append("\n## Market Context [source: IBKR live data]")
            # spy_price is collected for the learning system but excluded
            # from Claude's prompt — it's not a decision variable and
            # generates noise ("UNKNOWN") outside market hours.
            # enriched_at is internal metadata, not useful for reasoning.
            prompt_exclude = {"spy_price", "enriched_at"}
            # Group volatility signals first for readability
            vol_keys = [
                "vix", "vvix", "vix3m", "term_structure",
                "term_structure_ratio", "session_open_vix", "vix_change_pct",
            ]
            other_keys = [
                k for k in self.market_context
                if k not in vol_keys and k not in prompt_exclude
            ]
            for key in vol_keys:
                if key in self.market_context and key not in prompt_exclude:
                    sections.append(f"  - {key}: {self.market_context[key]}")
            for key in other_keys:
                sections.append(f"  - {key}: {self.market_context[key]}")

        if self.active_patterns:
            sections.append(f"\n## Active Patterns ({len(self.active_patterns)}) [source: pattern detector]")
            for p in self.active_patterns[:5]:
                sections.append(
                    f"  - {p.get('name', '?')}: win_rate={p.get('win_rate', '?')}, "
                    f"confidence={p.get('confidence', '?')}"
                )

        if self.recent_decisions:
            sections.append(f"\n## Recent Decisions ({len(self.recent_decisions)}) [source: decision audit]")
            for d in self.recent_decisions[-5:]:
                line = (
                    f"  - [{d.get('timestamp', '?')}] {d.get('action', '?')} "
                    f"(confidence={d.get('confidence', '?')})"
                )
                # Execution result
                if "executed" in d:
                    result_msg = d.get("result", "")
                    if result_msg:
                        result_msg = result_msg[:80]
                    line += f" -> executed={d['executed']}"
                    if result_msg:
                        line += f': "{result_msg}"'
                # Fill counts (EXECUTE_TRADES only)
                if d.get("filled_count") is not None:
                    line += (
                        f" [{d['filled_count']} filled, "
                        f"{d.get('failed_count', 0)} failed]"
                    )
                sections.append(line)

        if self.anomalies:
            sections.append(f"\n## Anomalies ({len(self.anomalies)}) [source: guardrails]")
            for a in self.anomalies:
                sections.append(f"  - {a.get('description', '?')}")

        if self.latest_reflection:
            sections.append("\n## Latest Reflection")
            sections.append(f"  {self.latest_reflection.get('summary', 'None')}")

        if self.staged_candidates:
            sections.append(f"\n## Staged Candidates ({len(self.staged_candidates)}) [source: scan pipeline]")
            for sc in self.staged_candidates:
                opt_type = sc.get("option_type", "PUT")[0]  # "P" or "C"
                line = (
                    f"  - {sc.get('symbol', '?')} {sc.get('strike', '?')}{opt_type} "
                    f"exp={sc.get('expiration', '?')} "
                    f"limit=${sc.get('limit_price', '?')} "
                    f"x{sc.get('contracts', '?')} "
                    f"[{sc.get('state', '?')}]"
                )
                # Price deviation annotation
                staged_price = sc.get("stock_price")
                current_price = sc.get("current_stock_price")
                change_pct = sc.get("price_change_pct")
                if staged_price is not None and current_price is not None and change_pct is not None:
                    line += f" stock@staged=${staged_price} -> now=${current_price} ({change_pct:+.1f}%)"
                    if sc.get("price_deviation_passed") is False:
                        line += "!!!"
                # Staleness annotation
                if sc.get("stale"):
                    hours = sc.get("hours_since_staged", "?")
                    line += f" [STALE: {hours}h old]"
                sections.append(line)

        if self.similar_decisions:
            sections.append(f"\n## Similar Past Decisions ({len(self.similar_decisions)})")
            for sd in self.similar_decisions[:3]:
                sections.append(
                    f"  - {sd.get('action', '?')}: {sd.get('reasoning', '?')[:100]}"
                )

        return "\n".join(sections)


class WorkingMemory:
    """PostgreSQL-backed context store with crash-safe state.

    Uses single-row upsert pattern in working_memory table.
    Loads prior state on startup so the daemon never starts empty
    if history exists.
    """

    def __init__(self, db_session: Session):
        """Initialize working memory from database.

        Args:
            db_session: SQLAlchemy session
        """
        self.db = db_session
        self._load_from_db()

    def _load_from_db(self) -> None:
        """Load state from database on startup."""
        row = self.db.query(WorkingMemoryRow).get(1)
        if row:
            self.strategy_state = row.strategy_state or {}
            self.market_context = row.market_context or {}
            self.recent_decisions = deque(
                row.recent_decisions or [], maxlen=MAX_RECENT_DECISIONS
            )
            self.anomalies = row.anomalies or []
            self.autonomy_level = row.autonomy_level
            self.reflection_reports = row.reflection_reports or []
            self.last_scheduled_fingerprint: str = row.last_scheduled_fingerprint or ""
            logger.info(
                f"Working memory loaded: autonomy=L{self.autonomy_level}, "
                f"decisions={len(self.recent_decisions)}"
            )
        else:
            self.strategy_state: dict = {}
            self.market_context: dict = {}
            self.recent_decisions: deque = deque(maxlen=MAX_RECENT_DECISIONS)
            self.anomalies: list = []
            self.autonomy_level: int = 1
            self.reflection_reports: list = []
            self.last_scheduled_fingerprint: str = ""
            logger.info("Working memory initialized (empty)")

    def save(self) -> None:
        """Persist current state to database (upsert)."""
        row = self.db.query(WorkingMemoryRow).get(1)
        if row is None:
            row = WorkingMemoryRow(id=1)
            self.db.add(row)

        row.strategy_state = self.strategy_state
        row.market_context = self.market_context
        row.recent_decisions = list(self.recent_decisions)
        row.anomalies = self.anomalies
        row.autonomy_level = self.autonomy_level
        row.reflection_reports = self.reflection_reports
        row.last_scheduled_fingerprint = self.last_scheduled_fingerprint
        row.updated_at = utc_now()

        self.db.commit()

    def add_decision(self, decision: dict) -> None:
        """Add a decision to recent history (FIFO, max 50).

        Args:
            decision: Decision data dictionary
        """
        self.recent_decisions.append(decision)
        self.save()

    def update_market_context(self, context: dict) -> None:
        """Update market context.

        Args:
            context: Market context data
        """
        self.market_context = context
        self.save()

    def update_strategy_state(self, state: dict) -> None:
        """Update strategy state.

        Args:
            state: Strategy state data
        """
        self.strategy_state = state
        self.save()

    def set_autonomy_level(self, level: int) -> None:
        """Set the current autonomy level.

        Args:
            level: Autonomy level (1-4)
        """
        self.autonomy_level = max(1, min(4, level))
        self.save()

    def add_anomaly(self, anomaly: dict) -> None:
        """Record an anomaly.

        Args:
            anomaly: Anomaly description
        """
        from src.utils.timezone import market_now

        anomaly["timestamp"] = market_now().strftime("%Y-%m-%d %H:%M:%S %Z")
        self.anomalies.append(anomaly)
        # Keep only last 20 anomalies
        self.anomalies = self.anomalies[-20:]
        self.save()

    def add_reflection(self, reflection: dict) -> None:
        """Add an EOD reflection report.

        Args:
            reflection: Reflection report data
        """
        from src.utils.timezone import market_now

        reflection["timestamp"] = market_now().strftime("%Y-%m-%d %H:%M:%S %Z")
        self.reflection_reports.append(reflection)
        # Keep only last 30 reflections
        self.reflection_reports = self.reflection_reports[-30:]
        self.save()

    def assemble_context(self, event_type: Optional[str] = None) -> ReasoningContext:
        """Build full reasoning context for Claude.

        Queries open positions, patterns, experiments from existing models
        and combines with working memory state.

        Args:
            event_type: Optional event type for context-specific data

        Returns:
            ReasoningContext ready for prompt assembly
        """
        ctx = ReasoningContext(
            autonomy_level=self.autonomy_level,
            strategy_state=self.strategy_state,
            market_context=self.market_context,
            recent_decisions=list(self.recent_decisions),
            anomalies=self.anomalies,
        )

        # Query open positions
        try:
            from src.utils.timezone import trading_date

            today = trading_date()
            open_trades = (
                self.db.query(Trade)
                .filter(Trade.exit_date.is_(None))
                .all()
            )
            ctx.open_positions = [
                {
                    "trade_id": t.trade_id,
                    "symbol": t.symbol,
                    "strike": t.strike,
                    "option_type": t.option_type or "PUT",
                    "expiration": str(t.expiration),
                    "entry_premium": t.entry_premium,
                    "contracts": t.contracts,
                    "entry_date": str(t.entry_date),
                    "dte": (t.expiration - today).days if t.expiration else 0,
                }
                for t in open_trades
            ]
            ctx.positions_summary = (
                f"{len(open_trades)} open positions"
            )
        except Exception as e:
            logger.warning(f"Could not query open positions: {e}")

        # Query held stock positions and detect covered calls
        try:
            from src.data.models import StockPosition
            from src.services.covered_call_detector import CoveredCallDetector

            open_stocks = (
                self.db.query(StockPosition)
                .filter(StockPosition.closed_date.is_(None))
                .all()
            )
            ctx.held_stocks = [
                {
                    "symbol": sp.symbol,
                    "shares": sp.shares,
                    "cost_basis": sp.cost_basis_per_share,
                    "origin_trade": sp.origin_trade_id,
                    "covered_call_trade": sp.covered_call_trade_id,
                    "assigned_date": str(sp.assigned_date) if sp.assigned_date else None,
                }
                for sp in open_stocks
            ]

            # Annotate open_positions with covered call info
            detector = CoveredCallDetector(self.db)
            pairs = detector.detect_pairs()
            cc_map = {
                pair.call_trade.trade_id: {
                    "stock_shares": pair.stock_position.shares,
                    "stock_cost_basis": pair.stock_position.cost_basis_per_share,
                    "fully_covered": pair.fully_covered,
                }
                for pair in pairs
            }
            for pos in ctx.open_positions:
                cc_info = cc_map.get(pos.get("trade_id"))
                if cc_info:
                    pos["is_covered_call"] = True
                    pos["covered_by_shares"] = cc_info["stock_shares"]
                    pos["stock_cost_basis"] = cc_info["stock_cost_basis"]
                    pos["fully_covered"] = cc_info["fully_covered"]
        except Exception as e:
            logger.debug(f"Could not query stock positions / covered calls: {e}")

        # Query active patterns
        try:
            patterns = (
                self.db.query(Pattern)
                .filter(Pattern.status == "active")
                .order_by(Pattern.confidence.desc())
                .limit(10)
                .all()
            )
            ctx.active_patterns = [
                {
                    "name": p.pattern_name,
                    "type": p.pattern_type,
                    "win_rate": p.win_rate,
                    "avg_roi": p.avg_roi,
                    "confidence": p.confidence,
                    "sample_size": p.sample_size,
                }
                for p in patterns
            ]
        except Exception as e:
            logger.warning(f"Could not query patterns: {e}")

        # Recent closed trades
        try:
            recent = (
                self.db.query(Trade)
                .filter(Trade.exit_date.isnot(None))
                .order_by(Trade.exit_date.desc())
                .limit(10)
                .all()
            )
            ctx.recent_trades = [
                {
                    "symbol": t.symbol,
                    "entry_date": str(t.entry_date),
                    "exit_date": str(t.exit_date),
                    "profit_loss": t.profit_loss,
                    "roi": t.roi,
                    "exit_reason": t.exit_reason,
                }
                for t in recent
            ]
        except Exception as e:
            logger.warning(f"Could not query recent trades: {e}")

        # Latest reflection
        if self.reflection_reports:
            ctx.latest_reflection = self.reflection_reports[-1]

        return ctx

    def store_embedding(
        self, decision_audit_id: int, text_content: str, embedding: Optional[list[float]] = None
    ) -> None:
        """Store a decision embedding for semantic search.

        On PostgreSQL with pgvector, stores the vector embedding.
        On SQLite, stores only the text content (no vector search).

        Args:
            decision_audit_id: FK to decision_audit table
            text_content: The text that was embedded
            embedding: Optional 1536-dim embedding vector
        """
        record = DecisionEmbedding(
            decision_audit_id=decision_audit_id,
            text_content=text_content,
        )
        self.db.add(record)
        self.db.flush()

        # Store embedding via raw SQL on PostgreSQL
        if embedding and self.db.bind and self.db.bind.dialect.name == "postgresql":
            from sqlalchemy import text

            embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"
            self.db.execute(
                text(
                    "UPDATE decision_embeddings SET embedding = :emb WHERE id = :id"
                ),
                {"emb": embedding_str, "id": record.id},
            )

        self.db.commit()

    def retrieve_similar_context(
        self, query_embedding: list[float], k: int = 5
    ) -> list[dict]:
        """Retrieve similar past decisions using pgvector cosine similarity.

        Feature-gated: only works on PostgreSQL with pgvector.
        Returns empty list on SQLite.

        Args:
            query_embedding: 1536-dim query vector
            k: Number of results to return

        Returns:
            List of similar decision dicts with reasoning and action
        """
        if not self.db.bind or self.db.bind.dialect.name != "postgresql":
            return []

        try:
            from sqlalchemy import text

            embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"
            results = self.db.execute(
                text(
                    """
                    SELECT de.text_content, da.action, da.reasoning, da.confidence,
                           de.embedding <=> :query_emb AS distance
                    FROM decision_embeddings de
                    JOIN decision_audit da ON da.id = de.decision_audit_id
                    WHERE de.embedding IS NOT NULL
                    ORDER BY de.embedding <=> :query_emb
                    LIMIT :k
                    """
                ),
                {"query_emb": embedding_str, "k": k},
            ).fetchall()

            return [
                {
                    "text": row[0],
                    "action": row[1],
                    "reasoning": row[2],
                    "confidence": row[3],
                    "distance": row[4],
                }
                for row in results
            ]
        except Exception as e:
            logger.warning(f"Semantic search failed: {e}")
            return []

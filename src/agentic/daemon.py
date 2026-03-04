"""Main event loop for the continuous agentic trading daemon.

TAADDaemon.run() is the async main loop. Each event goes through an 8-step
pipeline: assemble context -> check mandatory triggers -> reason with Claude
-> log decision -> check autonomy gate -> execute -> update memory -> heartbeat.

Graceful degradation:
- TWS disconnect: pause + reconnect loop
- Claude error: fall back to L1
- Stale data: MONITOR_ONLY
- DB down: in-memory queue + replay
- Cost cap: disable Claude
"""

import asyncio
import hashlib
import json
import os
import platform
import signal
import subprocess
import threading
import time
from datetime import UTC, datetime, date, timedelta
from typing import Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from src.agentic.action_executor import ActionExecutor
from src.agentic.autonomy_governor import AutonomyGovernor
from src.agentic.config import Phase5Config, load_phase5_config
from src.agentic.event_bus import EventBus, EventType
from src.agentic.guardrails.context_validator import ContextValidator
from src.agentic.guardrails.execution_gate import ExecutionGate
from src.agentic.guardrails.monitoring import ConfidenceCalibrator, ReasoningEntropyMonitor
from src.agentic.guardrails.numerical_grounding import NumericalGroundingChecker
from src.agentic.guardrails.output_validator import OutputValidator
from src.agentic.guardrails.registry import GuardrailRegistry
from src.agentic.health_monitor import HealthMonitor
from src.agentic.learning_loop import LearningLoop
from src.agentic.reasoning_engine import ClaudeReasoningEngine, DecisionOutput
from src.agentic.working_memory import ReasoningContext, WorkingMemory
from src.config.base import IBKRConfig, get_config
from src.data.database import get_db_session, init_database
from src.data.models import (
    DaemonEvent,
    DaemonNotification,
    DecisionAudit,
    GuardrailMetric,
    ScanOpportunity,
    Trade,
)
from src.services.market_calendar import MarketCalendar
from src.tools.ibkr_client import IBKRClient


from src.config.exchange_profile import get_active_profile as _get_active_profile

ET = _get_active_profile().timezone


class TAADDaemon:
    """The Autonomous Agentic Trading Daemon (TAAD).

    Main daemon process that monitors events, reasons with Claude,
    and executes actions through existing trading infrastructure.
    """

    def __init__(
        self,
        config: Optional[Phase5Config] = None,
        db_session: Optional[Session] = None,
    ):
        """Initialize the daemon.

        Args:
            config: Phase 5 configuration (loads from YAML if None)
            db_session: SQLAlchemy session (creates new if None)
        """
        self.config = config or load_phase5_config()
        self._db_session = db_session
        self._running = False

    def _init_components(self, db: Session) -> None:
        """Initialize all daemon components with a database session.

        Args:
            db: SQLAlchemy session
        """
        # Connect to IBKR (graceful degradation if unavailable)
        self.ibkr_client: Optional[IBKRClient] = None
        try:
            app_config = get_config()
            ibkr_config = IBKRConfig(
                host=app_config.ibkr_host,
                port=app_config.ibkr_port,
                client_id=self.config.daemon.client_id,
                account=app_config.ibkr_account,
            )
            client = IBKRClient(ibkr_config)
            client.connect()
            self.ibkr_client = client
            logger.info(
                f"Connected to IBKR (client_id={self.config.daemon.client_id})"
            )
        except Exception as e:
            logger.warning(
                f"IBKR connection failed, running without live data: {e}"
            )

        self.event_bus = EventBus(db)
        self.memory = WorkingMemory(db)
        self.governor = AutonomyGovernor(db, self.config.autonomy)
        self.reasoning = ClaudeReasoningEngine(db, self.config.claude)
        self.health = HealthMonitor(
            db,
            pid_file=self.config.daemon.pid_file,
            heartbeat_interval=self.config.daemon.heartbeat_interval_seconds,
        )
        self.learning = LearningLoop(db, self.reasoning, self.memory)
        self.calendar = MarketCalendar()  # Uses active profile from env

        # Initialize ExitManager + PositionMonitor from dashboard-configurable exit rules
        self.position_monitor: Optional["PositionMonitor"] = None
        self.exit_manager: Optional["ExitManager"] = None
        try:
            if self.ibkr_client is not None:
                from src.config.baseline_strategy import BaselineStrategy, ExitRules
                from src.execution.exit_manager import ExitManager
                from src.execution.position_monitor import PositionMonitor
                from src.data.repositories import PositionRepository, TradeRepository

                exit_cfg = self.config.exit_rules
                baseline_config = BaselineStrategy(
                    exit_rules=ExitRules(
                        profit_target=exit_cfg.profit_target,
                        stop_loss=exit_cfg.stop_loss,
                        time_exit_dte=exit_cfg.time_exit_dte,
                    ),
                )
                self.position_monitor = PositionMonitor(
                    ibkr_client=self.ibkr_client,
                    config=baseline_config,
                    position_repository=PositionRepository(db),
                    trade_repository=TradeRepository(db),
                )
                self.exit_manager = ExitManager(
                    ibkr_client=self.ibkr_client,
                    position_monitor=self.position_monitor,
                    config=baseline_config,
                    dry_run=False,
                )
                logger.info(
                    f"ExitManager initialized (profit_target={exit_cfg.profit_target}, "
                    f"stop_loss={exit_cfg.stop_loss}, time_exit_dte={exit_cfg.time_exit_dte})"
                )
        except Exception as e:
            logger.warning(f"ExitManager init failed, position monitoring disabled: {e}")

        self.executor = ActionExecutor(
            db, self.governor,
            ibkr_client=self.ibkr_client,
            exit_manager=self.exit_manager,
        )

        # Phase 6: Initialize guardrail registry
        self.guardrails = GuardrailRegistry(self.config.guardrails)
        self.guardrails.register_context_validator(ContextValidator())
        self.guardrails.register_output_validator(OutputValidator())
        self.guardrails.register_output_validator(NumericalGroundingChecker())
        self.execution_gate = ExecutionGate()
        self.guardrails.register_execution_gate(self.execution_gate)
        self.confidence_calibrator = ConfidenceCalibrator()
        self.entropy_monitor = ReasoningEntropyMonitor()

        # Initialize EventDetector for VIX spike / position alert monitoring
        from src.agentic.event_detector import EventDetector

        self.event_detector = EventDetector(
            event_bus=self.event_bus,
            position_monitor=self.position_monitor,
            ibkr_client=self.ibkr_client,
        )

        # Reconnection state
        self._last_reconnect_alert_time: float = 0.0
        self._reconnect_attempts: int = 0
        self._premarket_alert_sent_today: Optional[date] = None
        self._ibkr_ever_connected: bool = self.ibkr_client is not None
        self._db: Optional[Session] = None  # Set in run() for reconnection

        # Sync autonomy level: config/phase5.yaml is the source of truth.
        # The user sets initial_level via dashboard settings — that value
        # must be respected on every restart, even if the DB has a higher
        # level from a previous promotion. Without this, downgrading from
        # L3 to L1 in settings would be silently ignored (max() bug).
        persisted = self.memory.autonomy_level
        configured = self.config.autonomy.initial_level
        effective = configured
        self.governor.level = effective
        if effective != persisted:
            logger.info(
                f"Autonomy level: config says L{configured}, "
                f"DB had L{persisted} — using config value L{effective}"
            )
            self.memory.set_autonomy_level(effective)

    @staticmethod
    def _market_timestamp() -> str:
        """Current time formatted in the active exchange timezone."""
        from src.config.exchange_profile import get_active_profile

        return datetime.now(get_active_profile().timezone).strftime(
            "%Y-%m-%d %H:%M:%S %Z"
        )

    async def run(self) -> None:
        """Main daemon event loop.

        Initializes components, starts heartbeat, and processes events
        until shutdown is requested.
        """
        logger.info("=" * 60)
        logger.info("TAAD Daemon starting...")
        logger.info("=" * 60)

        if self._db_session:
            db = self._db_session
        else:
            init_database()
            db = get_db_session().__enter__()

        try:
            self._init_components(db)
            self._db = db  # Needed by reconnection logic
            self.health.start()
            self._running = True

            # Startup alert: if IBKR connection failed, alert immediately
            if self.ibkr_client is None:
                logger.warning(
                    "IBKR NOT CONNECTED at startup — "
                    "will retry every "
                    f"{self.config.daemon.reconnect_interval_seconds}s automatically"
                )
                self._fire_macos_alert(
                    title="TAAD: TWS Not Running",
                    message=(
                        "Daemon started but cannot reach TWS/IBGateway. "
                        "Start TWS and the daemon will reconnect automatically."
                    ),
                )
                # Arm the cooldown so _time_based_emitter doesn't immediately
                # fire a second alert on top of this one
                self._last_reconnect_alert_time = time.monotonic()

            # Register async-safe signal handlers to unblock event bus
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(
                    sig,
                    self._async_shutdown_handler,
                    sig,
                )

            # Start background tasks
            heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            time_emitter_task = asyncio.create_task(self._time_based_emitter())
            event_detector_task = asyncio.create_task(self.event_detector.run())

            logger.info(
                f"Daemon running (pid={__import__('os').getpid()}, "
                f"autonomy=L{self.governor.level})"
            )

            # Log market status on startup and emit immediate assessment event
            now_et = datetime.now(ET)
            session_info = self.calendar.format_session_info(now_et)
            if self.calendar.is_market_open(now_et):
                logger.info(
                    f"Market is OPEN — emitting immediate SCHEDULED_CHECK for startup assessment"
                )
                self.event_bus.emit(
                    EventType.SCHEDULED_CHECK,
                    payload={"trigger": "startup"},
                )
            else:
                session = session_info.get("session", "")
                next_open = session_info.get("next_open", "unknown")
                time_until = session_info.get("time_until_open", "unknown")
                logger.info(
                    f"Market is CLOSED (session={session}). "
                    f"Next open: {next_open} ({time_until}). "
                    f"Daemon will idle until then."
                )

                # If we started right after market close, emit MARKET_CLOSE
                # so EOD tasks (sync, calibrate, clean day) still run.
                if session == "after_hours" and self.calendar.is_trading_day(now_et):
                    logger.info(
                        "Post-close startup — emitting MARKET_CLOSE for EOD tasks"
                    )
                    self.event_bus.emit(EventType.MARKET_CLOSE)

            # Main event processing loop
            async for event in self.event_bus.stream(
                poll_interval=self.config.daemon.event_poll_interval_seconds,
                max_events=self.config.daemon.max_events_per_cycle,
            ):
                if self.health.shutdown_requested:
                    logger.info("Shutdown requested, stopping event loop")
                    break

                # Check if paused
                if self.health.is_paused():
                    await asyncio.sleep(5)
                    continue

                await self._process_event(event, db)

        except asyncio.CancelledError:
            logger.info("Daemon cancelled")
        except Exception as e:
            logger.error(f"Daemon error: {e}", exc_info=True)
            self.health._update_health(status="error", message=str(e))
        finally:
            self._running = False
            self.event_bus.stop()

            # Cancel background tasks
            for task_name in ("heartbeat_task", "time_emitter_task", "event_detector_task"):
                task = locals().get(task_name)
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            self.health.stop()

            # Disconnect IBKR client if connected
            if self.ibkr_client is not None:
                try:
                    self.ibkr_client.disconnect()
                except Exception as e:
                    logger.warning(f"IBKR disconnect error: {e}")

            if not self._db_session:
                db.close()

            logger.info("TAAD Daemon stopped")

    def _async_shutdown_handler(self, sig: signal.Signals) -> None:
        """Async-safe signal handler that unblocks the event bus immediately.

        Sets all shutdown flags and starts a 15-second force-exit timer.
        If the graceful shutdown path is blocked (e.g. synchronous Claude
        API call holding the event loop), the timer thread will force-kill
        the process so the user never needs ``kill -9``.
        """
        sig_name = sig.name if hasattr(sig, "name") else str(sig)
        logger.info(f"Received {sig_name}, requesting graceful shutdown")
        self._running = False
        self.health._shutdown_requested = True
        self.event_bus.stop()

        # Force-exit fallback: a daemon thread that kills the process after
        # 15 seconds if the graceful path is stuck in a synchronous call.
        def _force_exit() -> None:
            time.sleep(15)
            logger.warning("Graceful shutdown timed out after 15s — forcing exit")
            os._exit(1)

        t = threading.Thread(target=_force_exit, daemon=True)
        t.start()

    async def _process_event(self, event: DaemonEvent, db: Session) -> None:
        """Process a single event through the 8-step pipeline.

        Steps:
        1. Mark event as processing
        2. Assemble context
        3. Reason with Claude
        4. Log decision to audit
        5. Check autonomy gate
        6. Execute action
        7. Update working memory
        8. Update health

        Args:
            event: The event to process
            db: Database session
        """
        # Early exit if shutdown was requested while this event was queued
        if self.health.shutdown_requested:
            return

        event_type = event.event_type
        if not self.event_bus.mark_processing(event):
            logger.debug(f"Event {event.id} no longer claimable — skipping")
            return
        self.health.record_event(event_type)

        logger.info(f"Processing event: {event_type} (id={event.id})")

        # Skip stale MARKET_OPEN/SCHEDULED_CHECK/POSITION_EXIT_CHECK events
        # replayed after hours. These pile up when the daemon is killed during
        # market hours and restarted after close — replaying them wastes
        # Claude API calls.
        now_et = datetime.now(ET)
        if (
            event_type in ("MARKET_OPEN", "SCHEDULED_CHECK", "POSITION_EXIT_CHECK")
            and not self.calendar.is_market_open(now_et)
        ):
            logger.info(
                f"Skipping stale {event_type} (id={event.id}) — market is closed"
            )
            self.event_bus.mark_completed(event)
            return

        # Human-approved decisions bypass Claude reasoning and autonomy gate
        if event_type == "HUMAN_OVERRIDE" and event.payload and event.payload.get("decision_id"):
            await self._process_human_approval(event, db)
            return

        # Pre-Claude hooks: deterministic processing before Claude reasoning
        if event_type == "SCHEDULED_CHECK":
            # Sweep stale CLOSE_POSITION decisions for positions that were
            # closed by any path (bracket fills, market order retries, etc.)
            # since the last check. Runs BEFORE _monitor_positions so the
            # dashboard and Claude context are clean.
            self._auto_dismiss_closed_position_decisions(db)
            await self._monitor_positions(db)

        if event_type == "MARKET_OPEN":
            if hasattr(self, "event_detector") and self.event_detector:
                self.event_detector.reset_session()
            # Reset VIX session baseline for new trading day
            mc = dict(self.memory.market_context or {})
            mc.pop("session_open_vix", None)
            self.memory.update_market_context(mc)
            # Close any positions that expired since last MARKET_CLOSE
            # (catches the case where the daemon was down at close the previous day).
            # This runs before Claude reasoning so expired positions never appear
            # as open in the context — preventing spurious REQUEST_HUMAN_REVIEW loops.
            await self._close_expired_positions(db)
            await self._run_market_open_scan(db)

        if event_type == "MARKET_CLOSE":
            await self._run_eod_sync(db)
            await self._close_expired_positions(db)
            self._auto_reject_stale_guardrail_blocks(db)
            self._calibrate_closed_trades(db)
            self._persist_guardrail_metrics(db)
            self._record_clean_day(db)

        try:
            # Step 1: Assemble context
            context = self.memory.assemble_context(event_type)

            # Step 1.5: Enrich context with live IBKR data
            await self._enrich_context(context, db)

            # Step 1.6: Pre-Claude context validation (Phase 6)
            all_guardrail_results = []
            ctx_results = self.guardrails.validate_context(context)
            all_guardrail_results.extend(ctx_results)

            # Honor _guardrail_override flag from human-approved context re-emit
            guardrail_override = (event.payload or {}).get("_guardrail_override", False)

            # Retrospective events don't need fresh market data — they reflect
            # on decisions/outcomes from the day using data already collected.
            _FRESHNESS_EXEMPT_EVENTS = {"EOD_REFLECTION", "MARKET_CLOSE"}
            if (
                self.guardrails.has_block(ctx_results)
                and not guardrail_override
                and event_type in _FRESHNESS_EXEMPT_EVENTS
                and self._is_only_data_freshness_block(ctx_results)
            ):
                logger.info(
                    f"Data freshness block ignored for {event_type} "
                    f"(retrospective event — stale data expected)"
                )
                # Clear the block so processing continues
                ctx_results = [r for r in ctx_results if r.passed or r.severity != "block"]

            if self.guardrails.has_block(ctx_results) and not guardrail_override:
                block_reasons = self.guardrails.get_block_reasons(ctx_results)
                logger.warning(f"Context guardrail blocked: {'; '.join(block_reasons)}")

                if self._is_only_data_freshness_block(ctx_results):
                    # Data freshness is an infrastructure issue, not a decision
                    # needing human approval. Route to a self-updating notification.
                    await self._handle_data_freshness_block(event, db, block_reasons)
                    return

                self._escalate_guardrail_block(
                    event=event,
                    db=db,
                    event_type=event_type,
                    guardrail_layer="context",
                    block_reasons=block_reasons,
                    guardrail_results=ctx_results,
                    original_decision=None,
                )
                return
            elif self.guardrails.has_block(ctx_results) and guardrail_override:
                logger.warning(
                    "Context guardrail blocked but human override active — proceeding"
                )

            # Bail out early if shutdown arrived during context assembly
            if self.health.shutdown_requested:
                self.event_bus.mark_failed(event, "shutdown_requested")
                return

            # Step 1.7: Skip Claude if SCHEDULED_CHECK with no material changes
            skip_claude = False
            if event_type == "SCHEDULED_CHECK":
                fp = self._compute_context_fingerprint(context)
                if fp == self.memory.last_scheduled_fingerprint:
                    logger.info(
                        "SCHEDULED_CHECK skipped: no material context changes"
                    )
                    decisions = [DecisionOutput(
                        action="MONITOR_ONLY",
                        confidence=1.0,
                        reasoning="No material context changes since last check",
                        key_factors=["no_change_skip"],
                    )]
                    skip_claude = True
                else:
                    self.memory.last_scheduled_fingerprint = fp
                    self.memory.save()

            if not skip_claude:
                # Step 2: Reason with Claude
                # reason() is synchronous (blocking HTTP call, up to 60s).
                # Run in a thread so the event loop stays responsive to
                # SIGINT/SIGTERM — without this, Ctrl+C hangs until
                # the Claude API call completes.
                decisions = await asyncio.to_thread(
                    self.reasoning.reason,
                    context=context,
                    event_type=event_type,
                    event_payload=event.payload,
                )

                # Check shutdown between Claude response and execution —
                # if the user pressed Ctrl+C while Claude was thinking,
                # bail out before executing the decision.
                if self.health.shutdown_requested:
                    logger.info("Shutdown requested after Claude response — skipping execution")
                    self.event_bus.mark_completed(event)
                    return

            # Extract plan metadata from first action
            plan_id = str(uuid4())
            plan_assessment = ""
            if decisions and decisions[0].metadata.get("_plan_assessment"):
                plan_assessment = decisions[0].metadata.pop("_plan_assessment")

            if len(decisions) > 1:
                logger.info(
                    f"Multi-action plan: {len(decisions)} actions "
                    f"(plan_id={plan_id[:8]}…)"
                )

            # Build execution context once for all actions
            exec_context = self._build_execution_context(context, event, db=db)

            # Process each action independently
            for decision in decisions:
                # Step 2.25: Repair CLOSE_POSITION metadata if Claude omitted trade_id
                if decision.action == "CLOSE_POSITION" and not skip_claude:
                    decision = self._repair_close_metadata(decision, context)

                # Step 2.5: Post-Claude output validation (per action)
                action_guardrail_results = list(all_guardrail_results)
                if not skip_claude:
                    out_results = self.guardrails.validate_output(decision, context)
                    action_guardrail_results.extend(out_results)

                    if self.guardrails.has_block(out_results):
                        block_reasons = self.guardrails.get_block_reasons(out_results)

                        # CLOSE_POSITION/CLOSE_ALL_POSITIONS are risk-reducing: never block.
                        if decision.action in ("CLOSE_POSITION", "CLOSE_ALL_POSITIONS"):
                            logger.warning(
                                f"Output guardrail flagged {decision.action} (proceeding anyway — "
                                f"position closure is risk-reducing): {'; '.join(block_reasons)}"
                            )
                        else:
                            logger.warning(
                                f"Output guardrail blocked {decision.action} — "
                                f"escalating to human review: {'; '.join(block_reasons)}"
                            )
                            self._escalate_guardrail_block(
                                event=event,
                                db=db,
                                event_type=event_type,
                                guardrail_layer="output",
                                block_reasons=block_reasons,
                                guardrail_results=action_guardrail_results,
                                original_decision=decision,
                                plan_id=plan_id,
                                plan_assessment=plan_assessment,
                            )
                            continue  # Skip to next action in plan

                # Deduplicate: if the same trade-affecting action was already
                # decided within the last 90 seconds, suppress the duplicate.
                if decision.action in ("EXECUTE_TRADES", "CLOSE_POSITION", "CLOSE_ALL_POSITIONS") and not skip_claude:
                    dominated = self._is_duplicate_decision(decision.action)
                    if dominated:
                        logger.info(
                            f"Suppressing duplicate {decision.action} — "
                            f"same action decided {dominated}s ago"
                        )
                        decision = DecisionOutput(
                            action="MONITOR_ONLY",
                            confidence=1.0,
                            reasoning=(
                                f"Duplicate {decision.action} suppressed — "
                                f"same action already decided {dominated}s ago"
                            ),
                            key_factors=["duplicate_suppressed"],
                        )

                # Feed reasoning to entropy monitor (Phase 6)
                self.entropy_monitor.record_reasoning(
                    decision.reasoning or "", decision.key_factors or []
                )

                # Step 3: Log decision to audit (per action, with plan_id)
                audit = DecisionAudit(
                    event_id=event.id,
                    timestamp=datetime.utcnow(),
                    autonomy_level=self.governor.level,
                    event_type=event_type,
                    action=decision.action,
                    confidence=decision.confidence,
                    reasoning=decision.reasoning,
                    key_factors=decision.key_factors,
                    risks_considered=decision.risks_considered,
                    autonomy_approved=False,  # Updated below
                    plan_id=plan_id,
                    plan_assessment=plan_assessment,
                    decision_metadata=decision.metadata or None,
                    input_tokens=self.reasoning._reasoning_agent.total_input_tokens,
                    output_tokens=self.reasoning._reasoning_agent.total_output_tokens,
                    model_used=self.config.claude.reasoning_model,
                    cost_usd=self.reasoning._reasoning_agent.session_cost,
                    guardrail_flags=self.guardrails.results_to_dict(action_guardrail_results) if action_guardrail_results else None,
                )

                # Step 3.5: Pre-execution gate (Phase 6)
                exec_gate_results = self.guardrails.validate_execution(
                    decision, context, ibkr_client=self.ibkr_client
                )
                action_guardrail_results.extend(exec_gate_results)

                if self.guardrails.has_block(exec_gate_results):
                    block_reasons = self.guardrails.get_block_reasons(exec_gate_results)
                    # CLOSE_POSITION/CLOSE_ALL_POSITIONS bypass execution gate (risk-reducing)
                    if decision.action in ("CLOSE_POSITION", "CLOSE_ALL_POSITIONS"):
                        logger.warning(
                            f"Execution gate flagged {decision.action} (proceeding — "
                            f"risk-reducing): {'; '.join(block_reasons)}"
                        )
                        result = await self.executor.execute(decision, context=exec_context)
                    else:
                        logger.warning(
                            f"Execution gate blocked {decision.action} — "
                            f"escalating to human review: {'; '.join(block_reasons)}"
                        )
                        audit.executed = False
                        audit.autonomy_approved = False
                        audit.execution_result = {
                            "guardrail_layer": "execution_gate",
                            "block_reasons": block_reasons,
                            "message": "Execution gate blocked — awaiting human review",
                            "data": (decision.metadata or {}),
                        }
                        audit.guardrail_flags = self.guardrails.results_to_dict(action_guardrail_results)
                        db.add(audit)
                        db.commit()

                        self.memory.add_decision({
                            "timestamp": self._market_timestamp(),
                            "event_type": event_type,
                            "action": decision.action,
                            "confidence": decision.confidence,
                            "reasoning": decision.reasoning[:200] if decision.reasoning else "",
                            "executed": False,
                            "result": "Execution gate block — queued for human review",
                        })
                        self.health.record_decision()
                        logger.warning(
                            f"Execution gate block escalated to human review: "
                            f"{decision.action} (audit_id={audit.id})"
                        )
                        continue  # Skip to next action in plan
                else:
                    result = await self.executor.execute(decision, context=exec_context)

                # Update audit with execution result
                audit.autonomy_approved = result.success and result.action != "REQUEST_HUMAN_REVIEW"
                audit.executed = result.success and result.action not in ("MONITOR_ONLY", "REQUEST_HUMAN_REVIEW")
                audit.execution_result = {"message": result.message, "data": result.data}
                if result.error:
                    audit.execution_error = result.error
                if exec_gate_results:
                    audit.guardrail_flags = self.guardrails.results_to_dict(action_guardrail_results)

                db.add(audit)
                db.commit()

                # Emergency notification for CLOSE_ALL_POSITIONS
                if decision.action == "CLOSE_ALL_POSITIONS" and result.success:
                    self._upsert_notification(
                        db=db,
                        key="close_all_positions",
                        category="risk",
                        title="EMERGENCY: All Positions Closed",
                        message=f"CLOSE_ALL_POSITIONS: {result.message}",
                        details=result.data,
                    )

                # Actionable notification for VIX escalations
                if (result.action == "REQUEST_HUMAN_REVIEW" and result.data
                        and result.data.get("escalation_trigger") in ("vix_spike", "vix_extreme")):
                    vix = context.market_context.get("vix", 0.0)
                    vix_change = context.market_context.get("vix_change_pct", 0.0)
                    trigger = result.data["escalation_trigger"]
                    self._upsert_notification(
                        db=db, key="vix_spike", category="risk",
                        title=f"VIX {'EXTREME' if trigger == 'vix_extreme' else 'Spike'}: {vix:.1f}",
                        message=f"VIX at {vix:.1f} (session change: {vix_change:.1%}). New entries blocked.",
                        details={
                            "vix": vix,
                            "session_open_vix": context.market_context.get("session_open_vix"),
                            "vix_change_pct": vix_change,
                            "trigger": trigger,
                        },
                        action_choices=[
                            {"key": "resume_monitoring", "label": "Resume Monitoring",
                             "description": "Acknowledge elevated VIX, resume automated decisions"},
                            {"key": "authorize_close", "label": "Pre-Authorize Close",
                             "description": "Allow daemon to close at-risk positions proactively"},
                            {"key": "keep_blocked", "label": "Keep Blocked",
                             "description": "Maintain current freeze on new entries"},
                        ],
                    )

                self.health.record_decision()

                # Update working memory per action
                decision_entry = {
                    "timestamp": self._market_timestamp(),
                    "event_type": event_type,
                    "action": decision.action,
                    "confidence": decision.confidence,
                    "reasoning": decision.reasoning[:200] if decision.reasoning else "",
                    "executed": audit.executed,
                    "result": result.message[:200] if result.message else "",
                }
                # Enrich EXECUTE_TRADES with fill counts for Claude feedback
                if decision.action == "EXECUTE_TRADES" and result.data:
                    decision_entry["filled_count"] = result.data.get("filled_count")
                    decision_entry["failed_count"] = result.data.get("failed_count")
                    decision_entry["symbols_filled"] = result.data.get("symbols_filled")
                self.memory.add_decision(decision_entry)

                logger.info(
                    f"Event processed: {event_type} -> {decision.action} "
                    f"(confidence={decision.confidence:.2f}, executed={audit.executed})"
                )

            # After plan loop: safety net for material positions Claude missed
            if event_type == "SCHEDULED_CHECK":
                self._emit_material_position_checks(db, set())

            # Mark event complete (after ALL actions processed)
            self.event_bus.mark_completed(event)

        except Exception as e:
            logger.error(f"Event processing failed: {e}", exc_info=True)
            self.event_bus.mark_failed(event, str(e))
            self.health.record_error()

    def _escalate_guardrail_block(
        self,
        event: DaemonEvent,
        db: Session,
        event_type: str,
        guardrail_layer: str,
        block_reasons: list[str],
        guardrail_results: list,
        original_decision: Optional[DecisionOutput] = None,
        plan_id: Optional[str] = None,
        plan_assessment: Optional[str] = None,
    ) -> None:
        """Create a DecisionAudit for a guardrail block and queue it for human review.

        Two modes:
        - Context blocks (original_decision=None): Stores action="GUARDRAIL_BLOCKED"
          with original event info so approval can re-emit the event.
        - Output/gate blocks (original_decision provided): Stores the original
          action so approval can execute the handler directly.

        Args:
            event: The current DaemonEvent being processed
            db: Database session
            event_type: Event type string
            guardrail_layer: "context", "output", or "execution_gate"
            block_reasons: List of human-readable block reasons
            guardrail_results: GuardrailResult list for audit flags
            original_decision: Claude's original decision (None for context blocks)
            plan_id: Optional plan_id to group with other actions from same Claude call
            plan_assessment: Optional overall assessment from the plan
        """
        if original_decision is not None:
            # Output or execution gate block: store original action for direct execution
            action = original_decision.action
            confidence = original_decision.confidence
            reasoning = (
                f"[Guardrail {guardrail_layer} block] {'; '.join(block_reasons)}\n\n"
                f"Original reasoning: {original_decision.reasoning}"
            )
            key_factors = (original_decision.key_factors or []) + ["guardrail_block"]
            risks_considered = original_decision.risks_considered or []
            exec_result = {
                "guardrail_layer": guardrail_layer,
                "block_reasons": block_reasons,
                "message": f"Blocked by {guardrail_layer} guardrail — awaiting human review",
                "data": (original_decision.metadata or {}),
            }
        else:
            # Context block: no Claude call happened — store event info for re-emit
            action = "GUARDRAIL_BLOCKED"
            confidence = 1.0
            reasoning = f"[Guardrail context block] {'; '.join(block_reasons)}"
            key_factors = ["guardrail_block", "pre_claude"]
            risks_considered = block_reasons
            exec_result = {
                "guardrail_layer": "context",
                "block_reasons": block_reasons,
                "original_event_type": event_type,
                "original_event_payload": event.payload,
                "message": "Blocked before Claude reasoning — awaiting human review",
            }

        audit = DecisionAudit(
            event_id=event.id,
            timestamp=datetime.utcnow(),
            autonomy_level=self.governor.level,
            event_type=event_type,
            action=action,
            confidence=confidence,
            reasoning=reasoning,
            key_factors=key_factors,
            risks_considered=risks_considered,
            autonomy_approved=False,
            executed=False,
            execution_result=exec_result,
            plan_id=plan_id,
            plan_assessment=plan_assessment,
            decision_metadata=(original_decision.metadata if original_decision else None),
            guardrail_flags=self.guardrails.results_to_dict(guardrail_results),
            input_tokens=self.reasoning._reasoning_agent.total_input_tokens,
            output_tokens=self.reasoning._reasoning_agent.total_output_tokens,
            model_used=self.config.claude.reasoning_model,
            cost_usd=self.reasoning._reasoning_agent.session_cost,
        )
        db.add(audit)
        db.commit()

        # Feed entropy monitor
        self.entropy_monitor.record_reasoning(reasoning, key_factors)

        # Update working memory
        self.memory.add_decision({
            "timestamp": self._market_timestamp(),
            "event_type": event_type,
            "action": action,
            "confidence": confidence,
            "reasoning": reasoning[:200],
            "executed": False,
            "result": f"Guardrail {guardrail_layer} block — queued for human review",
        })

        # Mark event complete (it has been handled — just escalated)
        self.event_bus.mark_completed(event)
        self.health.record_decision()

        logger.warning(
            f"Guardrail {guardrail_layer} block escalated to human review: "
            f"{action} (audit_id={audit.id})"
        )

    # ------------------------------------------------------------------
    # Data freshness notification (replaces escalation for stale-data)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_only_data_freshness_block(ctx_results: list) -> bool:
        """Return True when all blocking guards are data_freshness.

        If other guards (consistency_check, null_sanitization, etc.) also
        block, those must still escalate to human review normally.
        """
        blocks = [r for r in ctx_results if not r.passed and r.severity == "block"]
        if not blocks:
            return False
        return all(r.guard_name == "data_freshness" for r in blocks)

    async def _handle_data_freshness_block(
        self,
        event: DaemonEvent,
        db: Session,
        block_reasons: list[str],
    ) -> None:
        """Handle a data_freshness guardrail block as a notification.

        Two modes depending on IBKR connection state:
        - Disconnected: upsert notification, no retry (reconnection loop upstream)
        - Connected but stale: retry enrichment up to 2x at 60s intervals
        """
        ibkr_connected = (
            self.ibkr_client is not None and self.ibkr_client.is_connected()
        )

        if not ibkr_connected:
            self._upsert_notification(
                db=db,
                key="data_freshness",
                category="data_quality",
                title="Stale data — IBKR not connected",
                message=(
                    "Market data enrichment blocked because IBKR is disconnected. "
                    "Events are being skipped. The daemon will auto-resolve "
                    "when IBKR reconnects and enrichment succeeds."
                ),
                details={"block_reasons": block_reasons, "ibkr_connected": False},
            )
        else:
            # Connected but data is stale — retry enrichment
            max_retries = 2
            for attempt in range(1, max_retries + 1):
                self._upsert_notification(
                    db=db,
                    key="data_freshness",
                    category="data_quality",
                    title=f"Stale data — retrying enrichment ({attempt}/{max_retries})",
                    message=(
                        f"IBKR is connected but data is stale. "
                        f"Retry attempt {attempt} of {max_retries}."
                    ),
                    details={
                        "block_reasons": block_reasons,
                        "ibkr_connected": True,
                        "retry_attempt": attempt,
                    },
                )
                await asyncio.sleep(60)

                # Attempt enrichment
                from src.agentic.working_memory import ReasoningContext

                retry_ctx = self.memory.assemble_context(event.event_type)
                await self._enrich_market_data(retry_ctx)

                if not retry_ctx.market_context.get("data_stale", True):
                    # Success — notification auto-resolved inside _enrich_market_data
                    logger.info(
                        f"Data freshness retry {attempt} succeeded — resolved"
                    )
                    self.event_bus.mark_completed(event)
                    return

            # All retries exhausted
            self._upsert_notification(
                db=db,
                key="data_freshness",
                category="data_quality",
                title="Stale data — retries exhausted",
                message=(
                    f"IBKR is connected but enrichment failed after "
                    f"{max_retries} retries. Skipping event."
                ),
                details={
                    "block_reasons": block_reasons,
                    "ibkr_connected": True,
                    "retries_exhausted": True,
                },
            )

        # Mark event completed (not escalated — just notified)
        self.event_bus.mark_completed(event)
        self.health.record_decision()
        logger.info("Data freshness block handled via notification (not escalated)")

    def _upsert_notification(
        self,
        db: Session,
        key: str,
        category: str,
        title: str,
        message: str,
        details: dict | None = None,
        action_choices: list[dict] | None = None,
    ) -> DaemonNotification:
        """Create or update a daemon notification by key.

        If an active notification with this key exists, update it in-place
        and increment occurrence_count. Otherwise create a new one.

        Args:
            action_choices: Optional list of [{key, label, description}] for
                structured user responses (e.g. resume/block/authorize).
                On update, preserves existing chosen_action if user already acted.
        """
        existing = (
            db.query(DaemonNotification)
            .filter_by(notification_key=key, status="active")
            .first()
        )
        if existing:
            existing.title = title
            existing.message = message
            existing.details = details
            # Set action_choices but preserve existing chosen_action
            if action_choices is not None:
                existing.action_choices = action_choices
            existing.occurrence_count += 1
            existing.updated_at = datetime.now(UTC)
            db.commit()
            logger.debug(
                f"Notification '{key}' updated (count={existing.occurrence_count})"
            )
            return existing
        else:
            notif = DaemonNotification(
                notification_key=key,
                category=category,
                status="active",
                title=title,
                message=message,
                details=details,
                action_choices=action_choices,
                first_seen_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                occurrence_count=1,
            )
            db.add(notif)
            db.commit()
            logger.info(f"Notification '{key}' created")
            return notif

    def _resolve_notification(self, db: Session, key: str) -> None:
        """Resolve an active notification (e.g. when IBKR reconnects)."""
        existing = (
            db.query(DaemonNotification)
            .filter_by(notification_key=key, status="active")
            .first()
        )
        if existing:
            existing.status = "resolved"
            existing.resolved_at = datetime.now(UTC)
            existing.updated_at = datetime.now(UTC)
            db.commit()
            logger.info(
                f"Notification '{key}' resolved "
                f"(was active for {existing.occurrence_count} occurrences)"
            )

    async def _process_human_approval(self, event: DaemonEvent, db: Session) -> None:
        """Execute a human-approved decision, bypassing Claude and autonomy gate.

        Args:
            event: HUMAN_OVERRIDE event with decision_id in payload
            db: Database session
        """
        decision_id = event.payload["decision_id"]
        logger.info(f"Processing human-approved decision (id={decision_id})")

        try:
            # Load the original decision
            audit = db.query(DecisionAudit).get(decision_id)
            if not audit:
                logger.error(f"Decision {decision_id} not found")
                self.event_bus.mark_failed(event, f"Decision {decision_id} not found")
                return

            if audit.executed:
                logger.warning(f"Decision {decision_id} was already executed")
                self.event_bus.mark_completed(event)
                return

            # REQUEST_HUMAN_REVIEW acknowledgement: human has reviewed and noted the
            # situation. Re-executing would call _handle_human_review → _escalate →
            # produce another pending approval (infinite loop). Just acknowledge it.
            if audit.action == "REQUEST_HUMAN_REVIEW":
                audit.executed = True
                audit.autonomy_approved = True
                audit.human_decision = "acknowledged"
                audit.human_decided_at = datetime.now(UTC)
                db.commit()
                self.memory.add_decision({
                    "timestamp": self._market_timestamp(),
                    "event_type": "HUMAN_OVERRIDE",
                    "action": "REQUEST_HUMAN_REVIEW",
                    "confidence": 1.0,
                    "reasoning": "Human acknowledged review request — monitoring continues",
                    "executed": True,
                    "result": "Acknowledged",
                })
                self.event_bus.mark_completed(event)
                logger.info(
                    f"REQUEST_HUMAN_REVIEW acknowledged by human (audit_id={decision_id})"
                )
                return

            # Context guardrail blocks: re-emit original event for full reprocessing
            # (fresh data + Claude reasoning) with the _guardrail_override flag.
            exec_data = audit.execution_result or {}
            if exec_data.get("guardrail_layer") == "context":
                original_event_type = exec_data.get("original_event_type")
                original_payload = exec_data.get("original_event_payload") or {}
                if not original_event_type:
                    logger.error(
                        f"Context guardrail approval missing original_event_type "
                        f"(decision_id={decision_id})"
                    )
                    self.event_bus.mark_failed(event, "Missing original_event_type")
                    return

                logger.info(
                    f"Context guardrail approved — re-emitting {original_event_type} "
                    f"with _guardrail_override flag"
                )
                self.event_bus.emit(
                    EventType(original_event_type),
                    payload={**original_payload, "_guardrail_override": True},
                )

                # Mark the original audit as human-approved
                audit.human_decision = "approved"
                audit.human_decided_at = datetime.now(UTC)
                audit.human_override = True
                db.commit()

                self.memory.add_decision({
                    "timestamp": self._market_timestamp(),
                    "event_type": "HUMAN_OVERRIDE",
                    "action": "GUARDRAIL_CONTEXT_APPROVED",
                    "confidence": 1.0,
                    "reasoning": f"Human approved context guardrail override — re-emitting {original_event_type}",
                    "executed": True,
                    "result": f"Re-emitted {original_event_type} with override",
                })

                self.event_bus.mark_completed(event)
                return

            # Reconstruct the decision from audit record.
            # Prefer decision_metadata column (direct storage from multi-action plan),
            # fall back to execution_result["data"] (legacy storage path).
            metadata = (
                audit.decision_metadata
                or (audit.execution_result or {}).get("data", {})
                or {}
            )
            decision = DecisionOutput(
                action=audit.action,
                confidence=audit.confidence or 0.0,
                reasoning=audit.reasoning or "",
                key_factors=audit.key_factors or [],
                risks_considered=audit.risks_considered or [],
                metadata=metadata,
            )

            # Guard: if this is a CLOSE_POSITION and the position is already
            # closed (e.g. by a bracket fill or deterministic exit while the
            # approval was pending), auto-dismiss instead of attempting execution.
            if decision.action == "CLOSE_POSITION":
                trade_id = (
                    decision.metadata.get("trade_id")
                    or decision.metadata.get("position_id")
                )
                if trade_id:
                    trade = db.query(Trade).filter(Trade.trade_id == trade_id).first()
                    if trade and trade.exit_date is not None:
                        audit.human_decision = "auto_dismissed"
                        audit.human_decided_at = datetime.now(UTC)
                        audit.executed = False
                        db.commit()
                        self.event_bus.mark_completed(event)
                        logger.info(
                            f"CLOSE_POSITION auto-dismissed at approval time: "
                            f"{trade.symbol} already closed at {trade.exit_date} "
                            f"(audit_id={decision_id})"
                        )
                        return

            # Execute directly — no Claude call, no autonomy gate
            handler = self.executor._get_handler(decision.action)
            result = await handler(decision)

            # Update the original audit record
            audit.executed = result.success
            audit.execution_result = {"message": result.message, "data": result.data}
            if result.error:
                audit.execution_error = result.error
            db.commit()

            # Update working memory
            self.memory.add_decision({
                "timestamp": self._market_timestamp(),
                "event_type": "HUMAN_OVERRIDE",
                "action": decision.action,
                "confidence": decision.confidence,
                "reasoning": f"Human-approved: {decision.reasoning[:150]}",
                "executed": result.success,
                "result": result.message[:200],
            })

            self.event_bus.mark_completed(event)
            self.health.record_decision()

            status = "[green]SUCCESS[/green]" if result.success else "[red]FAILED[/red]"
            logger.info(
                f"Human-approved decision executed: {decision.action} "
                f"(id={decision_id}, success={result.success})"
            )

            if not result.success:
                logger.error(f"Execution failed: {result.message}")

        except Exception as e:
            logger.error(f"Human approval execution failed: {e}", exc_info=True)
            self.event_bus.mark_failed(event, str(e))
            self.health.record_error()

    async def _run_eod_sync(self, db: Session) -> None:
        """Run end-of-day sync and reconcile.

        Called on MARKET_CLOSE. Syncs order status with IBKR, then
        reconciles positions to catch any discrepancies.
        """
        logger.info("=" * 40)
        logger.info("EOD Sync & Reconcile starting...")
        logger.info("=" * 40)

        if self.ibkr_client is None or not self.ibkr_client.is_connected():
            logger.warning("EOD sync skipped: IBKR not connected")
            return

        try:
            from src.data.repositories import TradeRepository
            from src.services.order_reconciliation import OrderReconciliation

            trade_repo = TradeRepository(db)
            reconciler = OrderReconciliation(self.ibkr_client, trade_repo)

            # Step 1: Sync orders
            logger.info("[1/2] Syncing orders with IBKR...")
            sync_report = await reconciler.sync_all_orders(include_filled=True)
            logger.info(
                f"  Sync complete: {sync_report.total_reconciled} reconciled, "
                f"{sync_report.total_discrepancies} discrepancies, "
                f"{len(sync_report.orphans)} orphans"
            )

            if sync_report.orphans:
                logger.info(f"  Importing {len(sync_report.orphans)} orphan orders...")
                imported = await reconciler.import_orphan_orders(
                    sync_report.orphans, dry_run=False
                )
                logger.info(f"  Imported {imported} orphan orders")

            # Step 2: Reconcile positions
            logger.info("[2/2] Reconciling positions with IBKR...")
            pos_report = await reconciler.reconcile_positions()

            if pos_report.has_discrepancies:
                logger.warning(
                    f"  Position discrepancies found: "
                    f"{len(pos_report.quantity_mismatches)} qty mismatches, "
                    f"{len(pos_report.in_ibkr_not_db)} in IBKR only, "
                    f"{len(pos_report.in_db_not_ibkr)} in DB only"
                )

                # Auto-import orphan positions
                if pos_report.in_ibkr_not_db:
                    logger.info(f"  Importing {len(pos_report.in_ibkr_not_db)} orphan positions...")
                    imported = await reconciler.import_orphan_positions(dry_run=False)
                    logger.info(f"  Imported {imported} orphan positions")

                if pos_report.assignments:
                    logger.warning(
                        f"  Detected {len(pos_report.assignments)} possible assignments"
                    )
                    self._process_assignments(pos_report.assignments, db)
            else:
                logger.info("  Positions in sync - no discrepancies")

            logger.info("EOD Sync & Reconcile complete")

            # Expire any remaining pre-execution staged candidates
            self._auto_unstage_eod(db)

        except Exception as e:
            logger.error(f"EOD sync failed: {e}", exc_info=True)

    def _process_assignments(
        self, assignments: list, db: Session
    ) -> None:
        """Close assigned trades and create StockPosition records.

        For each detected assignment:
        1. Close the original Trade (exit at intrinsic value, exit_reason='assignment')
        2. Create a StockPosition record for the resulting stock holding
        3. Set Trade.lifecycle_status = 'stock_held'

        Args:
            assignments: List of AssignmentEvent from reconciliation
            db: Database session
        """
        from src.data.models import Trade
        from src.services.stock_position_service import StockPositionService
        from src.utils.calc import calc_pnl, calc_pnl_pct

        svc = StockPositionService(db)
        processed = 0

        for event in assignments:
            if not event.matched_trade_id:
                logger.warning(
                    f"  Assignment {event.symbol} x{event.shares} — "
                    f"no matched trade, skipping"
                )
                continue

            trade = (
                db.query(Trade)
                .filter(Trade.trade_id == event.matched_trade_id)
                .first()
            )
            if not trade:
                logger.warning(
                    f"  Assignment {event.symbol} — "
                    f"trade {event.matched_trade_id} not found"
                )
                continue

            if trade.exit_date is not None:
                logger.info(
                    f"  Assignment {event.symbol} ${trade.strike}P — "
                    f"already closed, creating StockPosition if missing"
                )
                # Trade already closed (e.g. by CLI reconcile) but StockPosition
                # may not exist yet
                svc.create_from_assignment(event)
                db.commit()
                processed += 1
                continue

            # Close the trade at intrinsic value
            stock_price = event.avg_cost
            intrinsic = max(trade.strike - stock_price, 0)

            trade.exit_date = event.detection_time
            trade.exit_premium = intrinsic
            trade.exit_reason = "assignment"
            trade.profit_loss = calc_pnl(
                trade.entry_premium, intrinsic, trade.contracts
            )
            trade.profit_pct = calc_pnl_pct(
                trade.profit_loss, trade.entry_premium, trade.contracts
            )
            trade.days_held = (
                (event.detection_time.date() - trade.entry_date.date()).days
                if trade.entry_date
                else 0
            )
            trade.assignment_status = (
                "full"
                if event.contracts_assigned >= trade.contracts
                else "partial"
            )

            # Create StockPosition record
            svc.create_from_assignment(event)
            db.commit()
            processed += 1

            logger.info(
                f"  Processed assignment: {event.symbol} ${trade.strike}P — "
                f"exit intrinsic=${intrinsic:.2f}, "
                f"P&L=${trade.profit_loss:.2f} ({trade.profit_pct:.1%}), "
                f"StockPosition created for {event.shares} shares"
            )

        if processed:
            logger.info(f"  Processed {processed} assignment(s)")

    def _auto_unstage_eod(self, db: Session) -> None:
        """Expire any remaining pre-execution staged candidates at EOD.

        Prevents stale staged orders from carrying over to the next trading day.

        Args:
            db: Database session
        """
        from src.data.opportunity_state import OpportunityState
        from src.execution.opportunity_lifecycle import OpportunityLifecycleManager

        pre_exec_states = ["STAGED", "VALIDATING", "READY", "ADJUSTING", "CONFIRMED"]
        stale = (
            db.query(ScanOpportunity)
            .filter(
                ScanOpportunity.state.in_(pre_exec_states),
                ScanOpportunity.executed == False,  # noqa: E712
            )
            .all()
        )
        if not stale:
            return

        lifecycle = OpportunityLifecycleManager(db)
        for opp in stale:
            lifecycle.transition(
                opp.id,
                OpportunityState.EXPIRED,
                reason="EOD auto-unstage",
                actor="system",
            )
        db.commit()
        logger.info(f"EOD auto-unstage: expired {len(stale)} remaining staged candidates")

    def _auto_reject_stale_guardrail_blocks(self, db: Session) -> None:
        """Auto-reject guardrail escalations from prior days that were never reviewed.

        Called during MARKET_CLOSE. Prevents stale items from accumulating in
        the human review queue — if the operator didn't act on yesterday's
        guardrail block, the market context has changed and the block is no
        longer actionable.

        Args:
            db: Database session
        """
        today_utc = datetime.now(UTC).date()
        try:
            stale = (
                db.query(DecisionAudit)
                .filter(
                    DecisionAudit.executed == False,  # noqa: E712
                    DecisionAudit.human_decision.is_(None),
                    DecisionAudit.guardrail_flags.isnot(None),
                    sa_func.date(DecisionAudit.timestamp) < today_utc,
                )
                .all()
            )

            if not stale:
                return

            for audit in stale:
                audit.human_decision = "auto_rejected"
                audit.human_decided_at = datetime.now(UTC)

            db.commit()
            logger.info(
                f"EOD guardrail cleanup: auto-rejected {len(stale)} stale "
                f"guardrail escalation(s) from prior days"
            )
        except Exception as e:
            logger.error(f"Stale guardrail cleanup failed: {e}", exc_info=True)

    def _auto_dismiss_closed_position_decisions(self, db: Session) -> int:
        """Auto-reject pending CLOSE_POSITION decisions for already-closed positions.

        When a position is closed (by deterministic exit, market order retry,
        or any other path), any pending human-approval CLOSE_POSITION decisions
        for that position become stale. This cleans them up so the dashboard
        doesn't show confusing approve/reject buttons for positions that no
        longer exist.

        Args:
            db: Database session

        Returns:
            Number of decisions auto-dismissed
        """
        try:
            # Find pending CLOSE_POSITION decisions awaiting human approval
            pending_closes = (
                db.query(DecisionAudit)
                .filter(
                    DecisionAudit.action == "CLOSE_POSITION",
                    DecisionAudit.executed == False,  # noqa: E712
                    DecisionAudit.human_decision.is_(None),
                )
                .all()
            )

            if not pending_closes:
                return 0

            # Extract trade_ids from reasoning or metadata, check if closed
            dismissed = 0
            for audit in pending_closes:
                # Get the trade_id from the decision metadata
                metadata = audit.execution_result or {}
                if isinstance(metadata, dict):
                    trade_id = metadata.get("trade_id") or metadata.get("position_id")
                else:
                    trade_id = None

                # Fallback: extract symbol from reasoning and match against
                # recently closed trades. If a closed trade's symbol appears
                # in the reasoning, this decision was likely targeting it.
                if not trade_id and audit.reasoning:
                    import re
                    closed_trades = (
                        db.query(Trade)
                        .filter(Trade.exit_date.isnot(None))
                        .all()
                    )
                    for t in closed_trades:
                        if t.symbol and re.search(
                            r"\b" + re.escape(t.symbol) + r"\b", audit.reasoning
                        ):
                            trade_id = str(t.trade_id)
                            break

                if not trade_id:
                    continue

                # Check if this trade is already closed in the database
                trade = (
                    db.query(Trade)
                    .filter(Trade.trade_id == trade_id)
                    .first()
                )
                if trade and trade.exit_date is not None:
                    audit.human_decision = "auto_dismissed"
                    audit.human_decided_at = datetime.now(UTC)
                    dismissed += 1
                    logger.info(
                        f"Auto-dismissed stale CLOSE_POSITION (audit_id={audit.id}): "
                        f"{trade.symbol} already closed at {trade.exit_date}"
                    )

            if dismissed:
                db.commit()
                logger.info(
                    f"Auto-dismissed {dismissed} stale CLOSE_POSITION decision(s) "
                    f"for already-closed positions"
                )

            return dismissed

        except Exception as e:
            logger.error(f"Close decision auto-dismiss failed: {e}", exc_info=True)
            return 0

    async def _monitor_positions(self, db: Session) -> None:
        """Run deterministic position exits — runs every SCHEDULED_CHECK.

        Three-step process:
        1. Reconcile pending exit orders (check if filled)
        2. Evaluate all positions for deterministic exits
        3. Execute exits for triggered positions

        Material position checks (POSITION_EXIT_CHECK events) are emitted
        AFTER the plan loop in _process_event, so that positions Claude
        already addressed are naturally suppressed.

        Runs BEFORE Claude reasoning so context reflects updated state.

        Args:
            db: Database session
        """
        if not self.exit_manager or not self.ibkr_client:
            return
        if not self.ibkr_client.is_connected():
            return

        # Track which positions were deterministically exited
        exited_pids: set[str] = set()

        # Query positions with pending or today-rejected CLOSE_POSITION decisions.
        # These are excluded from BOTH deterministic exits and new POSITION_EXIT_CHECK
        # events: pending = human is still deciding; rejected = human said "no" today.
        suppressed_tids = self._get_suppressed_close_trade_ids(db)

        # Build position_id -> trade_id mapping for suppressed check
        # (deterministic exits use position_id format like AAPL_150_20260227_P)
        suppressed_pids: set[str] = set()
        if suppressed_tids:
            try:
                from src.utils.position_key import position_key_from_trade
                open_trades = db.query(Trade).filter(Trade.exit_date.is_(None)).all()
                for t in open_trades:
                    if str(t.trade_id) in suppressed_tids:
                        pid = position_key_from_trade(t)
                        suppressed_pids.add(pid)
            except Exception as e:
                logger.warning(f"Could not build suppressed position mapping: {e}")

        try:
            # Step 1: Reconcile pending exit orders
            pending = self.exit_manager.check_pending_exits()
            for pid, status in pending.items():
                if "filled" in status.lower():
                    logger.info(f"Pending exit filled: {pid} -> {status}")
                    self._emit_position_closed(pid, reason="bracket_fill", db=db)
                    exited_pids.add(pid)

            # Step 2: Evaluate positions for deterministic exits
            decisions = self.exit_manager.evaluate_exits()
            exits_triggered = 0
            for pid, decision in decisions.items():
                if not decision.should_exit:
                    continue

                # Skip positions that are pending approval or rejected today.
                if pid in suppressed_pids:
                    logger.info(
                        f"Skipping deterministic exit for {pid} ({decision.reason}): "
                        f"position suppressed (pending approval or rejected today)"
                    )
                    continue

                logger.info(f"Exit triggered: {pid} -> {decision.reason}")
                result = self.exit_manager.execute_exit(pid, decision)
                if result.success:
                    exits_triggered += 1
                    exited_pids.add(pid)
                    self._emit_position_closed(
                        pid, reason=decision.reason,
                        exit_price=result.exit_price, db=db,
                    )
                else:
                    logger.warning(f"Exit failed for {pid}: {result.error_message}")

            if decisions:
                logger.info(
                    f"Position monitoring: {len(decisions)} checked, "
                    f"{exits_triggered} exits triggered"
                )

        except Exception as e:
            logger.error(f"Position monitoring error: {e}", exc_info=True)

        # Auto-dismiss pending CLOSE_POSITION decisions for positions
        # that were just closed by deterministic exits above. The broader sweep
        # runs at the top of every SCHEDULED_CHECK, but this catches exits that
        # happened within _monitor_positions() itself.
        self._auto_dismiss_closed_position_decisions(db)

    def _emit_material_position_checks(
        self, db: Session, exited_pids: set[str]
    ) -> int:
        """Emit POSITION_EXIT_CHECK events for positions with material P&L.

        "Material" means P&L% ≥ +50% (approaching profit target) or
        P&L% ≤ -100% (approaching stop loss). Each qualifying position
        gets its own event so Claude evaluates them independently and
        the dashboard shows separate Approve/Reject for each.

        Args:
            db: Database session
            exited_pids: Position IDs already exited deterministically (skip these)

        Returns:
            Number of events emitted
        """
        open_trades = (
            db.query(Trade)
            .filter(Trade.exit_date.is_(None))
            .all()
        )

        if not open_trades:
            return 0

        # Check for positions that are suppressed (pending approval or rejected today)
        suppressed_tids = self._get_suppressed_close_trade_ids(db)

        emitted = 0
        for trade in open_trades:
            if trade.trade_id in exited_pids:
                continue

            # Skip positions that are pending approval, rejected today,
            # or have unprocessed POSITION_EXIT_CHECK events
            if str(trade.trade_id) in suppressed_tids:
                continue

            pnl_pct = self._get_position_pnl_pct(trade, db)
            if pnl_pct is None:
                continue

            # Material thresholds: ≥+50% profit or ≤-100% loss
            if pnl_pct >= 50.0 or pnl_pct <= -100.0:
                self.event_bus.emit(
                    EventType.POSITION_EXIT_CHECK,
                    payload={
                        "trade_id": trade.trade_id,
                        "symbol": trade.symbol,
                        "strike": trade.strike,
                        "pnl_pct": round(pnl_pct, 1),
                        "option_type": trade.option_type or "PUT",
                    },
                )
                emitted += 1
                opt_code = (trade.option_type or "PUT")[0]
                logger.info(
                    f"POSITION_EXIT_CHECK emitted: {trade.symbol} "
                    f"${trade.strike}{opt_code} (P&L={pnl_pct:+.1f}%)"
                )

        if emitted:
            logger.info(f"Emitted {emitted} per-position exit check(s)")

        return emitted

    def _get_position_pnl_pct(self, trade: Trade, db: Session) -> Optional[float]:
        """Compute P&L percentage for an open position.

        Uses the position_monitor's cached price if available, otherwise
        attempts a live IBKR quote. Returns None if price unavailable.

        P&L% = (entry_premium - current_price) / entry_premium * 100
        Positive = profit (option value decreased), Negative = loss.

        Args:
            trade: Open Trade record
            db: Database session

        Returns:
            P&L percentage or None if price unavailable
        """
        if not trade.entry_premium or trade.entry_premium <= 0:
            return None

        current_price = None

        # Try position monitor cache first
        if self.position_monitor:
            try:
                cached = self.position_monitor.get_position_price(trade.trade_id)
                if cached and cached > 0:
                    current_price = cached
            except (AttributeError, Exception):
                pass

        # Fall back to live quote
        if current_price is None and self.ibkr_client and self.ibkr_client.is_connected():
            try:
                exp_str = str(trade.expiration).replace("-", "")
                if len(exp_str) == 10:  # "YYYY-MM-DD" format
                    exp_str = exp_str.replace("-", "")
                if len(exp_str) != 8:
                    return None

                right = (trade.option_type or "PUT")[0]  # "P" or "C"
                contract = self.ibkr_client.get_option_contract(
                    symbol=trade.symbol,
                    expiration=exp_str,
                    strike=trade.strike,
                    right=right,
                )
                qualified = self.ibkr_client.qualify_contract(contract)
                if qualified:
                    import asyncio
                    quote = asyncio.get_event_loop().run_until_complete(
                        self.ibkr_client.get_quote(qualified, timeout=1.0)
                    )
                    if quote.is_valid and quote.bid > 0 and quote.ask > 0:
                        current_price = (quote.bid + quote.ask) / 2
            except Exception as e:
                logger.debug(f"P&L quote failed for {trade.symbol}: {e}")
                return None

        if current_price is None:
            return None

        return (trade.entry_premium - current_price) / trade.entry_premium * 100

    @staticmethod
    def _current_session_start() -> datetime:
        """Get the start of the current (or most recent) trading session.

        Returns the most recent market open (9:30 AM ET) on a trading day.
        This is the boundary for rejection persistence: rejections made
        during a session persist until the next session starts.

        Examples:
            - Friday 3pm ET  → Friday 9:30 AM ET
            - Saturday noon  → Friday 9:30 AM ET
            - Monday 8am ET  → Friday 9:30 AM ET  (before market open)
            - Monday 10am ET → Monday 9:30 AM ET  (after market open)
        """
        cal = MarketCalendar()
        now = datetime.now(ET)
        today = now.date()
        market_open_time = cal.REGULAR_OPEN

        # If today is a trading day and we're past market open, session started today
        if cal.is_trading_day(now) and now.time() >= market_open_time:
            return datetime.combine(today, market_open_time, ET)

        # Otherwise walk backwards to find the most recent trading day
        check = today
        for _ in range(10):
            check -= timedelta(days=1)
            check_dt = datetime.combine(check, market_open_time, ET)
            if cal.is_trading_day(check_dt):
                return check_dt

        # Fallback: should never reach here
        return datetime.combine(today, datetime.min.time(), ET)

    def _get_suppressed_close_trade_ids(self, db: Session) -> set[str]:
        """Get trade_ids that should NOT be re-evaluated for closing.

        Includes two categories:
        1. Pending: CLOSE_POSITION decisions awaiting human approval (no decision yet)
        2. Rejected this session: CLOSE_POSITION decisions the user rejected
           since the current trading session started — persists until the
           next trading day's market open (handles weekends and holidays)

        This prevents the daemon from:
        - Emitting duplicate POSITION_EXIT_CHECK events for the same position
        - Running deterministic exits that override a pending human approval
        - Re-asking about a position the user already rejected this session

        Returns:
            Set of trade_id strings that should be suppressed
        """
        suppressed_trade_ids: set[str] = set()

        try:
            # 1. Check pending CLOSE_POSITION decisions in audit queue
            pending_audits = (
                db.query(DecisionAudit)
                .filter(
                    DecisionAudit.action == "CLOSE_POSITION",
                    DecisionAudit.executed == False,  # noqa: E712
                    DecisionAudit.human_decision.is_(None),
                )
                .all()
            )

            for audit in pending_audits:
                tid = self._extract_trade_id_from_audit(audit)
                if tid:
                    suppressed_trade_ids.add(tid)

            # 2. Check CLOSE_POSITION decisions rejected since session start.
            # Uses trading-day boundary (not calendar midnight) so Friday
            # rejections persist through the weekend until Monday 9:30 AM.
            session_start = self._current_session_start()
            # Convert to naive UTC for DB comparison (DecisionAudit stores UTC)
            session_start_utc = session_start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
            rejected_this_session = (
                db.query(DecisionAudit)
                .filter(
                    DecisionAudit.action == "CLOSE_POSITION",
                    DecisionAudit.executed == False,  # noqa: E712
                    DecisionAudit.human_decision == "rejected",
                    DecisionAudit.human_decided_at >= session_start_utc,
                )
                .all()
            )

            for audit in rejected_this_session:
                tid = self._extract_trade_id_from_audit(audit)
                if tid:
                    suppressed_trade_ids.add(tid)

            # 3. Check pending POSITION_EXIT_CHECK events not yet processed
            pending_events = (
                db.query(DaemonEvent)
                .filter(
                    DaemonEvent.event_type == "POSITION_EXIT_CHECK",
                    DaemonEvent.status.in_(["pending", "processing"]),
                )
                .all()
            )
            for event in pending_events:
                tid = (event.payload or {}).get("trade_id")
                if tid:
                    suppressed_trade_ids.add(str(tid))

        except Exception as e:
            logger.warning(f"Could not query suppressed close decisions: {e}")

        if suppressed_trade_ids:
            logger.info(
                f"Positions suppressed (pending/rejected): {suppressed_trade_ids}"
            )

        return suppressed_trade_ids

    @staticmethod
    def _extract_trade_id_from_audit(audit: DecisionAudit) -> Optional[str]:
        """Extract the trade_id from a DecisionAudit.

        Handles three storage paths (checked in order):
        - Path C (multi-action plan): decision_metadata.trade_id
        - Path A (autonomy escalation): execution_result.data.decision.metadata.trade_id
        - Path B (guardrail block): execution_result.data.trade_id
        """
        # Path C: direct decision_metadata column (multi-action plan)
        dm = audit.decision_metadata
        if isinstance(dm, dict):
            tid = dm.get("trade_id") or dm.get("position_id")
            if tid:
                return str(tid)

        # Legacy paths via execution_result
        er = audit.execution_result or {}
        data = er.get("data", {})
        if isinstance(data, dict):
            # Path A: autonomy escalation (nested metadata)
            meta = data.get("decision", {}).get("metadata", {})
            tid = meta.get("trade_id") or meta.get("position_id")
            if tid:
                return str(tid)
            # Path B: guardrail block (flat data)
            tid = data.get("trade_id") or data.get("position_id")
            if tid:
                return str(tid)
        return None

    def _emit_position_closed(
        self,
        position_id: str,
        reason: str,
        exit_price: float | None = None,
        db: Session | None = None,
    ) -> None:
        """Emit POSITION_CLOSED event and record trade outcome.

        Args:
            position_id: Position identifier
            reason: Exit reason
            exit_price: Fill price (if available)
            db: Database session for outcome recording
        """
        self.event_bus.emit(
            EventType.POSITION_CLOSED,
            payload={
                "position_id": position_id,
                "reason": reason,
                "exit_price": exit_price,
            },
        )
        # Immediate feedback to governor + learning
        self._record_trade_outcome(position_id, db)

        # Clean up any pending CLOSE_POSITION decisions for this position
        if db is not None:
            self._auto_dismiss_closed_position_decisions(db)

    async def _close_expired_positions(self, db: Session) -> None:
        """Close positions past expiration date at MARKET_CLOSE.

        Args:
            db: Database session
        """
        if not self.position_monitor:
            return
        try:
            closed = self.position_monitor.close_expired_positions(dry_run=False)
            for pos in closed:
                logger.info(f"Expired position closed: {pos.get('symbol')}")
                pos_id = f"{pos.get('symbol', 'UNK')}_{pos.get('strike', 0)}"
                self._emit_position_closed(
                    pos_id, reason="expired", exit_price=0.0, db=db,
                )
        except Exception as e:
            logger.error(f"Expired position cleanup failed: {e}", exc_info=True)

    def _record_trade_outcome(self, position_id: str, db: Session | None = None) -> None:
        """Record trade outcome: learning + governor + memory.

        Called immediately when a position closes. Links the outcome
        back to the originating decision, feeds the governor for
        promotion/demotion, and persists the new autonomy level.

        Args:
            position_id: Position identifier (trade_id or composite key)
            db: Database session
        """
        if not db:
            return
        try:
            # Find the closed trade
            trade = (
                db.query(Trade)
                .filter(Trade.exit_date.isnot(None))
                .filter(Trade.trade_id == position_id)
                .first()
            )
            if not trade:
                return

            # 1. Learning loop: link outcome to decision
            try:
                self.learning.record_trade_outcome(trade.trade_id)
            except Exception as e:
                logger.debug(f"Learning outcome recording failed: {e}")

            # 2. Governor: track win/loss for promotion
            is_win = trade.profit_loss is not None and trade.profit_loss > 0
            self.governor.record_trade_outcome(win=is_win)

            # 3. Check promotion
            if self.governor.check_promotion():
                new_level = self.governor.level
                logger.info(f"Autonomy promoted to L{new_level}")
                self.memory.set_autonomy_level(new_level)

            # 4. Persist governor counters
            if hasattr(self.governor, "_save_counters"):
                self.governor._save_counters()

        except Exception as e:
            logger.error(f"Trade outcome recording failed: {e}", exc_info=True)

    def _record_clean_day(self, db: Session) -> None:
        """If no errors or overrides today, record as clean day for promotion.

        Called at MARKET_CLOSE. A "clean day" means zero failed events
        and zero human overrides — evidence the daemon is operating
        reliably at the current autonomy level.

        Args:
            db: Database session
        """
        today = date.today()
        try:
            failed_events = db.query(DaemonEvent).filter(
                sa_func.date(DaemonEvent.created_at) == today,
                DaemonEvent.status == "failed",
            ).count()
            overrides = db.query(DaemonEvent).filter(
                sa_func.date(DaemonEvent.created_at) == today,
                DaemonEvent.event_type == "HUMAN_OVERRIDE",
            ).count()

            if failed_events == 0 and overrides == 0:
                self.governor.record_clean_day()
                # Persist counters after clean day
                if hasattr(self.governor, "_save_counters"):
                    self.governor._save_counters()
                logger.info(
                    f"Clean day recorded (L{self.governor.level}: "
                    f"{self.governor._consecutive_clean_days}d clean, "
                    f"{self.governor._trades_at_current_level} trades)"
                )
            else:
                logger.info(
                    f"Not a clean day (failed={failed_events}, overrides={overrides})"
                )
        except Exception as e:
            logger.error(f"Clean day recording failed: {e}", exc_info=True)

    def _calibrate_closed_trades(self, db: Session) -> int:
        """Feed today's closed trades into the confidence calibrator.

        Queries trades closed today that have ai_confidence set.
        Records: confidence=trade.ai_confidence, was_correct=(profit_loss > 0).

        Args:
            db: Database session

        Returns:
            Number of outcomes recorded
        """
        today = date.today()
        closed_today = (
            db.query(Trade)
            .filter(
                sa_func.date(Trade.exit_date) == today,
                Trade.ai_confidence.isnot(None),
                Trade.profit_loss.isnot(None),
            )
            .all()
        )

        count = 0
        for trade in closed_today:
            was_correct = trade.profit_loss > 0
            self.confidence_calibrator.record_outcome(trade.ai_confidence, was_correct)
            count += 1

        if count:
            cal = self.confidence_calibrator.compute_calibration()
            logger.info(
                f"Calibration: recorded {count} outcomes, "
                f"error={cal['calibration_error']:.3f}"
            )
        return count

    def _persist_guardrail_metrics(self, db: Session) -> None:
        """Persist today's guardrail metrics to the GuardrailMetric table.

        Writes three types of metric rows:
        - "calibration" rows (one per confidence bucket)
        - "entropy" row (reasoning diversity metrics)
        - "daily_audit" row (block/warning/decision counts)

        Idempotent: deletes any existing rows for today before writing.

        Args:
            db: Database session
        """
        today = date.today()

        # Delete existing rows for today (idempotent on restart)
        db.query(GuardrailMetric).filter(
            GuardrailMetric.metric_date == today
        ).delete()
        db.flush()

        # --- Calibration buckets ---
        cal = self.confidence_calibrator.compute_calibration()
        for bucket in cal.get("buckets", []):
            db.add(GuardrailMetric(
                metric_date=today,
                metric_type="calibration",
                confidence_bucket=bucket["range"],
                predicted_accuracy=bucket["predicted_accuracy"],
                actual_accuracy=bucket["actual_accuracy"],
                sample_size=bucket["sample_size"],
                calibration_error=bucket["calibration_error"],
            ))

        # --- Entropy metrics ---
        similarity_scores = self.entropy_monitor.compute_similarity_scores()
        avg_similarity = (
            sum(similarity_scores) / len(similarity_scores)
            if similarity_scores else 0.0
        )
        unique_ratio = self.entropy_monitor.compute_unique_factors_ratio()

        # Average reasoning length
        reasoning_lengths = [
            len(r) for r in self.entropy_monitor._reasoning_history
        ]
        avg_reasoning_len = (
            sum(reasoning_lengths) / len(reasoning_lengths)
            if reasoning_lengths else 0.0
        )

        db.add(GuardrailMetric(
            metric_date=today,
            metric_type="entropy",
            avg_reasoning_length=round(avg_reasoning_len, 1),
            unique_key_factors_ratio=round(unique_ratio, 3),
            reasoning_similarity_score=round(avg_similarity, 3),
        ))

        # --- Daily audit summary ---
        decisions_today = (
            db.query(DecisionAudit)
            .filter(sa_func.date(DecisionAudit.timestamp) == today)
            .all()
        )

        total_decisions = len(decisions_today)
        blocks = 0
        warnings = 0
        symbols_flagged = set()
        numbers_flagged = 0

        for d in decisions_today:
            flags = d.guardrail_flags or []
            for flag in flags:
                if not flag.get("passed", True):
                    severity = flag.get("severity", "info")
                    if severity == "block":
                        blocks += 1
                    elif severity == "warning":
                        warnings += 1

                    guard_name = flag.get("guard_name", "")
                    if "symbol" in guard_name.lower():
                        symbols_flagged.add(flag.get("reason", ""))
                    if "numerical" in guard_name.lower() or "number" in guard_name.lower():
                        numbers_flagged += 1

        db.add(GuardrailMetric(
            metric_date=today,
            metric_type="daily_audit",
            total_decisions=total_decisions,
            guardrail_blocks=blocks,
            guardrail_warnings=warnings,
            symbols_flagged=len(symbols_flagged),
            numbers_flagged=numbers_flagged,
            calibration_error=cal.get("calibration_error", 0.0),
            sample_size=cal.get("sample_size", 0),
        ))

        db.commit()

        # Log the daily report
        self._log_guardrail_daily_report(
            total_decisions=total_decisions,
            blocks=blocks,
            warnings=warnings,
            calibration=cal,
            avg_similarity=avg_similarity,
            unique_ratio=unique_ratio,
        )

    def _log_guardrail_daily_report(
        self,
        total_decisions: int,
        blocks: int,
        warnings: int,
        calibration: dict,
        avg_similarity: float,
        unique_ratio: float,
    ) -> None:
        """Log a structured daily guardrail summary.

        Args:
            total_decisions: Number of decisions today
            blocks: Number of guardrail blocks
            warnings: Number of guardrail warnings
            calibration: Calibration dict from compute_calibration()
            avg_similarity: Average reasoning similarity score
            unique_ratio: Unique key factors ratio
        """
        lines = [
            "",
            "=" * 50,
            "DAILY GUARDRAIL REPORT",
            "=" * 50,
            f"  Decisions today:       {total_decisions}",
            f"  Guardrail blocks:      {blocks}",
            f"  Guardrail warnings:    {warnings}",
            f"  Calibration error:     {calibration.get('calibration_error', 0.0):.3f}",
            f"  Calibration samples:   {calibration.get('sample_size', 0)}",
            f"  Reasoning similarity:  {avg_similarity:.3f}",
            f"  Key factor diversity:  {unique_ratio:.3f}",
        ]

        for bucket in calibration.get("buckets", []):
            lines.append(
                f"    Bucket {bucket['range']}: "
                f"predicted={bucket['predicted_accuracy']:.2f}, "
                f"actual={bucket['actual_accuracy']:.2f}, "
                f"n={bucket['sample_size']}"
            )

        lines.append("=" * 50)
        logger.info("\n".join(lines))

    async def _run_market_open_scan(self, db: Session) -> None:
        """Run market-open auto-scan pipeline (pre-Claude hook).

        Called on MARKET_OPEN when auto_scan.enabled is True.
        Scans the market, runs the auto-select pipeline, and
        stages selected trades for the daemon's Claude to reason about.

        Args:
            db: Database session
        """
        cfg = self.config.auto_scan
        if not cfg.enabled:
            logger.debug("Auto-scan: disabled in config, skipping")
            return

        logger.info("=" * 50)
        logger.info("MARKET OPEN AUTO-SCAN starting...")
        logger.info(f"  preset={cfg.scanner_preset}, delay={cfg.delay_minutes}m")
        logger.info("=" * 50)

        # Check IBKR connection
        ibkr_connected = (
            self.ibkr_client is not None and self.ibkr_client.is_connected()
        )
        if cfg.require_ibkr and not ibkr_connected:
            logger.warning("Auto-scan: IBKR not connected, skipping (require_ibkr=true)")
            return

        # Wait for spreads to settle
        if cfg.delay_minutes > 0:
            logger.info(f"Auto-scan: waiting {cfg.delay_minutes}m for spreads to settle...")
            await asyncio.sleep(cfg.delay_minutes * 60)

            # Re-check IBKR after delay
            ibkr_connected = (
                self.ibkr_client is not None and self.ibkr_client.is_connected()
            )
            if cfg.require_ibkr and not ibkr_connected:
                logger.warning("Auto-scan: IBKR dropped during delay, skipping")
                return

        try:
            from src.services.auto_select_pipeline import (
                run_auto_select_pipeline,
                run_scan_and_persist,
                stage_selected_candidates,
            )

            # Step 1: Run IBKR scanner
            logger.info(f"Auto-scan: running scanner with preset '{cfg.scanner_preset}'...")
            scan_id, opportunities = run_scan_and_persist(
                preset=cfg.scanner_preset, db=db
            )
            logger.info(f"Auto-scan: scan_id={scan_id}, {len(opportunities)} symbols found")

            if not opportunities:
                logger.warning("Auto-scan: scanner returned 0 symbols, nothing to do")
                self.memory.add_decision({
                    "timestamp": self._market_timestamp(),
                    "event_type": "AUTO_SCAN",
                    "action": "SCAN_EMPTY",
                    "confidence": 1.0,
                    "reasoning": "Market-open scan returned 0 symbols",
                    "executed": False,
                    "result": "No symbols to process",
                })
                return

            # Step 2: Run auto-select pipeline (chains → scores → AI → portfolio)
            logger.info("Auto-scan: running auto-select pipeline...")
            result = run_auto_select_pipeline(
                scan_id=scan_id, db=db, override_market_hours=False
            )

            if not result.success:
                logger.warning(f"Auto-scan: pipeline failed — {result.error}")
                self.memory.add_decision({
                    "timestamp": self._market_timestamp(),
                    "event_type": "AUTO_SCAN",
                    "action": "PIPELINE_FAILED",
                    "confidence": 1.0,
                    "reasoning": f"Auto-select pipeline error: {result.error}",
                    "executed": False,
                    "result": result.error or "Pipeline failed",
                })
                return

            # Step 3: Stage selected trades (if auto_stage enabled)
            staged_count = 0
            if cfg.auto_stage and result.selected:
                staged_count = stage_selected_candidates(
                    selected=result.selected,
                    opp_id_map=result.opp_id_map,
                    config_snapshot=result.config_snapshot,
                    db=db,
                    earnings_map=result.earnings_map,
                )

            # Step 4: Write summary to working memory for Claude context
            selected_symbols = [s.symbol for s in result.selected]
            summary = (
                f"Market-open auto-scan: {result.symbols_scanned} scanned, "
                f"{result.best_strikes_found} best strikes, "
                f"{len(result.selected)} selected, {staged_count} staged. "
                f"Budget=${result.available_budget:,.0f}, "
                f"used=${result.used_margin:,.0f}. "
                f"Symbols: {', '.join(selected_symbols[:10])}"
            )
            if len(selected_symbols) > 10:
                summary += f" (+{len(selected_symbols) - 10} more)"

            self.memory.add_decision({
                "timestamp": self._market_timestamp(),
                "event_type": "AUTO_SCAN",
                "action": "SCAN_COMPLETE",
                "confidence": 1.0,
                "reasoning": summary,
                "executed": staged_count > 0,
                "result": (
                    f"{staged_count} trades staged for review"
                    if staged_count > 0
                    else "Scan complete, no trades staged"
                ),
            })

            logger.info("=" * 50)
            logger.info(f"AUTO-SCAN COMPLETE: {summary}")
            logger.info("=" * 50)

        except Exception as e:
            logger.error(f"Auto-scan failed: {e}", exc_info=True)
            self.memory.add_decision({
                "timestamp": self._market_timestamp(),
                "event_type": "AUTO_SCAN",
                "action": "SCAN_ERROR",
                "confidence": 1.0,
                "reasoning": f"Auto-scan exception: {e}",
                "executed": False,
                "result": str(e)[:200],
            })

    def _build_execution_context(self, context, event: DaemonEvent, db: Optional[Session] = None) -> dict:
        """Build execution context for autonomy gate evaluation.

        Args:
            context: ReasoningContext
            event: The current event
            db: Optional database session for notification lookups

        Returns:
            Context dict for autonomy checks
        """
        # Check if user has acknowledged VIX spike via dashboard notification
        vix_acknowledged = False
        if db is not None:
            try:
                vix_notif = (
                    db.query(DaemonNotification)
                    .filter_by(notification_key="vix_spike")
                    .order_by(DaemonNotification.updated_at.desc())
                    .first()
                )
                if vix_notif and getattr(vix_notif, "chosen_action", None) == "resume_monitoring":
                    vix_acknowledged = True
            except Exception:
                pass

        return {
            "margin_utilization": context.market_context.get("margin_utilization", 0.0),
            "vix": context.market_context.get("vix", 0.0),
            "vix_change_pct": context.market_context.get("vix_change_pct", 0.0),
            "session_open_vix": context.market_context.get("session_open_vix", 0.0),
            "data_stale": context.market_context.get("data_stale", False),
            "consecutive_losses": context.market_context.get("consecutive_losses", 0),
            "is_first_trade_of_day": self._is_first_trade_of_day(),
            "vix_spike_acknowledged": vix_acknowledged,
        }

    def _repair_close_metadata(
        self, decision: DecisionOutput, context: ReasoningContext
    ) -> DecisionOutput:
        """Auto-fill trade_id in CLOSE_POSITION metadata when Claude omits it.

        Claude's reasoning typically mentions the symbol (e.g. "NOW 95.0P")
        but sometimes forgets to include trade_id in metadata. This method
        extracts the symbol from reasoning and resolves it against open
        positions in context.

        Args:
            decision: CLOSE_POSITION decision from Claude
            context: Current reasoning context with open positions

        Returns:
            Decision with trade_id added to metadata if resolved
        """
        import re

        metadata = decision.metadata or {}
        if metadata.get("position_id") or metadata.get("trade_id"):
            return decision  # Already has an ID

        if not context.open_positions:
            return decision  # No positions to match against

        reasoning = decision.reasoning or ""

        # Build symbol -> trade_id map from open positions
        symbol_to_trade = {}
        for pos in context.open_positions:
            sym = pos.get("symbol", "")
            tid = pos.get("trade_id", "")
            if sym and tid:
                symbol_to_trade[sym.upper()] = str(tid)

        # Find the first open-position symbol mentioned in reasoning
        for sym, tid in symbol_to_trade.items():
            if re.search(r"\b" + re.escape(sym) + r"\b", reasoning):
                logger.info(
                    f"Auto-resolved CLOSE_POSITION trade_id: {sym} -> {tid}"
                )
                new_metadata = dict(metadata)
                new_metadata["trade_id"] = tid
                new_metadata["auto_resolved_from"] = sym
                return DecisionOutput(
                    action=decision.action,
                    confidence=decision.confidence,
                    reasoning=decision.reasoning,
                    key_factors=decision.key_factors,
                    risks_considered=decision.risks_considered,
                    metadata=new_metadata,
                )

        logger.warning(
            "CLOSE_POSITION: could not auto-resolve trade_id from reasoning"
        )
        return decision

    def _is_duplicate_decision(self, action: str, window_seconds: int = 90) -> int | None:
        """Check if the same action was already decided recently.

        Scans recent_decisions for the same action within *window_seconds*.
        Returns the age in seconds if a duplicate is found, else None.
        """
        now = datetime.now(ET)
        for d in reversed(list(self.memory.recent_decisions)):
            if d.get("action") != action:
                continue
            ts_str = d.get("timestamp", "")
            try:
                # _market_timestamp() produces "2026-03-03 15:31:22 EST"
                # strptime can't parse TZ abbreviations reliably, so strip
                # the tz suffix and treat as ET (which it always is).
                parts = ts_str.rsplit(" ", 1)
                ts = datetime.strptime(parts[0], "%Y-%m-%d %H:%M:%S")
                ts = ts.replace(tzinfo=ET)
                age = (now - ts).total_seconds()
                if 0 <= age < window_seconds:
                    return int(age)
            except (ValueError, TypeError):
                continue
        return None

    def _compute_context_fingerprint(self, context: ReasoningContext) -> str:
        """Compute a hash of material context fields for change detection.

        Used to skip redundant Claude calls on SCHEDULED_CHECK when
        nothing actionable has changed since the last check.

        Fingerprinted fields (chosen because they represent actionable state):
        - open_positions: count + symbols (new trade opened/closed)
        - P&L buckets: ±10% ranges (crosses a threshold)
        - staged_candidates: count + symbols + states (user staged/unstaged)
        - conditions_favorable: market regime flipped
        - VIX bucket: rounded to nearest 1.0
        - autonomy_level: level changed
        - anomalies count: new anomaly recorded
        """
        # Bucket P&L to ±10% ranges so minor tick changes don't trigger
        pnl_buckets = []
        for p in context.open_positions:
            pnl_pct = p.get("pnl_pct", "0")
            try:
                val = float(str(pnl_pct).replace("%", "").replace("+", ""))
                pnl_buckets.append(int(val / 10))  # bucket to 10% ranges
            except (ValueError, TypeError):
                pnl_buckets.append(0)

        essential = {
            "autonomy": context.autonomy_level,
            "pos_symbols": sorted(p["symbol"] for p in context.open_positions),
            "pos_pnl_buckets": pnl_buckets,
            "staged_symbols": sorted(
                f"{c['symbol']}:{c['state']}" for c in context.staged_candidates
            ),
            "favorable": context.market_context.get("conditions_favorable"),
            "vix_bucket": round(float(context.market_context.get("vix") or 0)),
            "anomaly_count": len(context.anomalies),
        }

        return hashlib.md5(
            json.dumps(essential, sort_keys=True).encode()
        ).hexdigest()

    def _is_first_trade_of_day(self) -> bool:
        """Check if no trades have been executed today."""
        today_decisions = [
            d for d in self.memory.recent_decisions
            if d.get("action") == "EXECUTE_TRADES"
            and d.get("executed")
            and d.get("timestamp", "").startswith(str(date.today()))
        ]
        return len(today_decisions) == 0

    async def _enrich_context(self, ctx: ReasoningContext, db: Session) -> None:
        """Enrich reasoning context with live IBKR data.

        Three enrichments:
        a) Market data (VIX, SPY) via MarketConditionMonitor
        b) Position P&L via live option quotes
        c) Staged candidates from ScanOpportunity table

        Falls back gracefully if IBKR is unavailable — context retains
        DB-only data and data_stale flag is set.

        Args:
            ctx: ReasoningContext to enrich in-place
            db: Database session for staged candidate queries
        """
        # (a) Market data enrichment
        await self._enrich_market_data(ctx)

        # (b) Position P&L enrichment
        await self._enrich_position_pnl(ctx)

        # (c) Staged candidates from DB
        self._enrich_staged_candidates(ctx, db)

    async def _enrich_market_data(self, ctx: ReasoningContext) -> None:
        """Fetch VIX and SPY from IBKR and update market_context.

        Also persists to WorkingMemory so values survive across events.
        Attempts reconnection if IBKR is disconnected (transient failure).
        """
        if self.ibkr_client is None:
            # Try reconnection before giving up
            if self._attempt_ibkr_reconnection():
                logger.info("IBKR reconnected during enrichment")
            else:
                logger.warning("Market data enrichment skipped: IBKR unavailable")
                ctx.market_context["data_stale"] = True
                return

        if not self.ibkr_client.is_connected():
            logger.warning("IBKR disconnected during enrichment — attempting reconnect")
            try:
                self.ibkr_client.ensure_connected()
            except Exception as e:
                logger.warning(f"IBKR reconnection failed: {e}")
                ctx.market_context["data_stale"] = True
                return

        try:
            from datetime import datetime, timezone
            from src.services.market_conditions import MarketConditionMonitor

            monitor = MarketConditionMonitor(self.ibkr_client)
            conditions = await monitor.check_conditions()

            # Session-open VIX baseline: set once on first enrichment of the
            # day, then used for all subsequent change calculations.  Stored in
            # working memory so it survives daemon restarts mid-session.
            session_open_vix = self.memory.market_context.get("session_open_vix")
            if session_open_vix is None and conditions.vix > 0:
                session_open_vix = conditions.vix
                logger.info(f"VIX session baseline set to {session_open_vix:.1f}")

            ctx.market_context["vix"] = conditions.vix
            ctx.market_context["session_open_vix"] = session_open_vix
            # Store None when SPY is unavailable (e.g. ASX market) so Claude
            # sees "UNKNOWN" rather than the misleading value $0.00
            ctx.market_context["spy_price"] = (
                conditions.spy_price if conditions.spy_price > 0 else None
            )
            ctx.market_context["conditions_favorable"] = conditions.conditions_favorable
            ctx.market_context["data_stale"] = False
            ctx.market_context["enriched_at"] = datetime.now(timezone.utc).isoformat()

            # Auto-resolve data freshness notification if IBKR is back
            db = getattr(self, "_db", None)
            if db is not None:
                self._resolve_notification(db, "data_freshness")

            # VIX change as FRACTION relative to session open (not prev cycle).
            # 0.10 = 10% increase.  Aligns with autonomy_governor expectations.
            if session_open_vix and session_open_vix > 0 and conditions.vix > 0:
                ctx.market_context["vix_change_pct"] = round(
                    (conditions.vix - session_open_vix) / session_open_vix, 4
                )

            # VIX notification tiers: auto-resolve when conditions improve,
            # upsert informational notification when elevated (no action needed)
            vix = conditions.vix
            vix_change = abs(ctx.market_context.get("vix_change_pct", 0.0))
            if db is not None:
                if vix <= 30 and vix_change <= 0.20:
                    self._resolve_notification(db, "vix_spike")
                if vix <= 20 and vix_change <= 0.10:
                    self._resolve_notification(db, "vix_elevated")
                elif vix > 20 or vix_change > 0.10:
                    # Elevated tier: info-only notification (no action_choices)
                    self._upsert_notification(
                        db=db, key="vix_elevated", category="risk",
                        title=f"VIX Elevated: {vix:.1f}",
                        message=(
                            f"VIX at {vix:.1f} (session change: {vix_change:.1%}). "
                            f"Monitoring continues — position sizing may be scaled down."
                        ),
                    )

            # Persist to working memory for cross-event continuity
            self.memory.update_market_context(ctx.market_context)

            spy_str = f"${conditions.spy_price:.2f}" if conditions.spy_price > 0 else "UNKNOWN"
            logger.info(
                f"Market data enriched: VIX={conditions.vix:.1f}, "
                f"SPY={spy_str}, "
                f"favorable={conditions.conditions_favorable}"
            )

        except Exception as e:
            logger.warning(f"Market data enrichment failed: {e}")
            ctx.market_context["data_stale"] = True

    async def _enrich_position_pnl(self, ctx: ReasoningContext) -> None:
        """Fetch live quotes for open positions and compute P&L.

        Updates each position dict in ctx.open_positions with:
        current_mid, pnl, pnl_pct fields.

        Limited to first 10 positions with 1s timeout per quote.
        """
        if not ctx.open_positions:
            return

        if self.ibkr_client is None or not self.ibkr_client.is_connected():
            return

        try:
            for pos in ctx.open_positions[:10]:
                try:
                    exp_str = str(pos.get("expiration", "")).replace("-", "")
                    if len(exp_str) != 8:
                        continue

                    right = pos.get("option_type", "PUT")[0]  # "P" or "C"
                    contract = self.ibkr_client.get_option_contract(
                        symbol=pos["symbol"],
                        expiration=exp_str,
                        strike=pos["strike"],
                        right=right,
                    )
                    qualified = self.ibkr_client.qualify_contract(contract)
                    if not qualified:
                        continue

                    quote = await self.ibkr_client.get_quote(qualified, timeout=1.0)
                    if quote.is_valid and quote.bid > 0 and quote.ask > 0:
                        current_mid = round((quote.bid + quote.ask) / 2, 4)
                        entry_premium = pos.get("entry_premium", 0)
                        if entry_premium and entry_premium > 0:
                            pnl = round(entry_premium - current_mid, 4)
                            pnl_pct = round(pnl / entry_premium * 100, 1)
                            pos["current_mid"] = current_mid
                            pos["pnl"] = pnl
                            pos["pnl_pct"] = f"{pnl_pct:+.1f}%"
                            pos["pnl_source"] = quote.reason if quote.reason else ""

                except Exception as e:
                    logger.debug(f"P&L enrichment failed for {pos.get('symbol')}: {e}")
                    continue

            # Second pass: fill gaps from IBKR portfolio (works after hours)
            positions_missing_pnl = [p for p in ctx.open_positions[:10] if "pnl" not in p]
            if positions_missing_pnl:
                try:
                    portfolio_items = self.ibkr_client.get_portfolio()
                    # Build lookup: (symbol, strike, expiry) → PortfolioItem
                    portfolio_map = {}
                    for item in portfolio_items:
                        c = item.contract
                        if hasattr(c, "strike") and hasattr(c, "lastTradeDateOrContractMonth"):
                            key = (c.symbol, c.strike, c.lastTradeDateOrContractMonth)
                            portfolio_map[key] = item

                    for pos in positions_missing_pnl:
                        exp_str = str(pos.get("expiration", "")).replace("-", "")
                        key = (pos.get("symbol"), pos.get("strike"), exp_str)
                        item = portfolio_map.get(key)
                        if item and item.marketPrice > 0:
                            entry_premium = pos.get("entry_premium", 0)
                            if entry_premium and entry_premium > 0:
                                current_mid = round(item.marketPrice, 4)
                                pnl = round(entry_premium - current_mid, 4)
                                pnl_pct = round(pnl / entry_premium * 100, 1)
                                pos["current_mid"] = current_mid
                                pos["pnl"] = pnl
                                pos["pnl_pct"] = f"{pnl_pct:+.1f}%"
                                pos["pnl_source"] = "portfolio"
                except Exception as e:
                    logger.debug(f"Portfolio P&L fallback failed: {e}")

        except Exception as e:
            logger.warning(f"Position P&L enrichment failed: {e}")

    def _enrich_staged_candidates(self, ctx: ReasoningContext, db: Session) -> None:
        """Query staged/ready/confirmed ScanOpportunities for context.

        Limits candidates to max_positions from config, minus any already-open
        positions, so the execution scheduler never receives more candidates
        than it can place.

        Annotates each candidate with price deviation and staleness data
        so Claude can reason about stale or moved candidates.

        Args:
            ctx: ReasoningContext to enrich
            db: Database session
        """
        try:
            from src.config.base import get_config
            cfg = get_config()
            open_count = len(ctx.open_positions) if ctx.open_positions else 0
            slots = max(cfg.max_positions - open_count, 0)
            if slots == 0:
                logger.info(
                    f"No position slots available ({open_count} open / "
                    f"{cfg.max_positions} max) — skipping staged candidates"
                )

            candidates = (
                db.query(ScanOpportunity)
                .filter(
                    ScanOpportunity.state.in_(["STAGED", "READY", "CONFIRMED", "EXECUTING"]),
                    ScanOpportunity.executed == False,
                )
                .order_by(ScanOpportunity.portfolio_rank.asc())
                .limit(slots)
                .all()
            )

            ctx.staged_candidates = [
                {
                    "symbol": c.symbol,
                    "strike": c.strike,
                    "expiration": str(c.expiration),
                    "limit_price": c.staged_limit_price,
                    "contracts": c.staged_contracts,
                    "state": c.state,
                    "stock_price": c.stock_price,
                    "option_type": c.option_type or "PUT",
                }
                for c in candidates
            ]

            # Annotate with price deviation and staleness
            self._annotate_candidates_with_deviation(ctx.staged_candidates, candidates)

            if ctx.staged_candidates:
                logger.info(
                    f"Staged candidates enriched: {len(ctx.staged_candidates)} "
                    f"candidates in context"
                )

        except Exception as e:
            logger.warning(f"Staged candidates query failed: {e}")

    def _annotate_candidates_with_deviation(
        self, candidate_dicts: list[dict], db_candidates: list
    ) -> None:
        """Annotate staged candidate dicts with price deviation and staleness.

        Uses PriceDeviationValidator to check each candidate. Results are
        annotated (not filtered) so Claude can reason about them.

        Args:
            candidate_dicts: List of candidate dicts to annotate in-place
            db_candidates: Corresponding ScanOpportunity ORM objects
        """
        from src.utils.timezone import market_now
        from src.validation.price_deviation import PriceDeviationValidator

        validator = PriceDeviationValidator()
        now = market_now()

        for cand_dict, db_cand in zip(candidate_dicts, db_candidates):
            # Staleness check
            created_at = db_cand.staged_at or db_cand.created_at
            if created_at is not None:
                try:
                    # Both sides naive for consistent comparison (SQLite stores naive)
                    now_naive = now.replace(tzinfo=None)
                    staleness = validator.check_staleness(created_at, checked_at=now_naive)
                    cand_dict["hours_since_staged"] = round(staleness.age_hours, 1)
                    cand_dict["stale"] = not staleness.passed
                except Exception as e:
                    logger.debug(f"Staleness check failed for {cand_dict.get('symbol')}: {e}")

            # Price deviation check (requires IBKR connection)
            original_price = cand_dict.get("stock_price")
            if original_price and self.ibkr_client is not None:
                try:
                    current_price = self.ibkr_client.get_stock_price(cand_dict["symbol"])
                    if current_price is not None and original_price > 0:
                        deviation = validator.check_deviation(current_price, original_price)
                        cand_dict["current_stock_price"] = round(current_price, 2)
                        cand_dict["price_change_pct"] = round(deviation.deviation_pct * 100, 1)
                        cand_dict["price_deviation_passed"] = deviation.passed
                except Exception as e:
                    logger.debug(f"Price deviation check failed for {cand_dict.get('symbol')}: {e}")

    # ------------------------------------------------------------------
    # IBKR auto-reconnection + macOS alerts
    # ------------------------------------------------------------------

    def _attempt_ibkr_reconnection(self) -> bool:
        """Try to (re)connect to IBKR.

        - If ibkr_client exists but is disconnected: call ensure_connected()
        - If ibkr_client is None: create a new IBKRClient and connect()
        - On success: re-initialize dependent components (PositionMonitor,
          ExitManager, EventDetector)

        Returns:
            True if connected after this attempt, False otherwise
        """
        try:
            if self.ibkr_client is not None:
                # Client exists but lost connection — lightweight reconnect
                if not self.ibkr_client.is_connected():
                    self.ibkr_client.ensure_connected()
                    if self.ibkr_client.is_connected():
                        self._reconnect_attempts = 0
                        self._ibkr_ever_connected = True
                        return True
                else:
                    return True  # Already connected
            else:
                # No client at all — create fresh one (single attempt)
                app_config = get_config()
                ibkr_config = IBKRConfig(
                    host=app_config.ibkr_host,
                    port=app_config.ibkr_port,
                    client_id=self.config.daemon.client_id,
                    account=app_config.ibkr_account,
                )
                client = IBKRClient(ibkr_config)
                client.connect(retry=False)
                self.ibkr_client = client

                # Re-initialize all IBKR-dependent components
                if self._db is not None:
                    self._init_ibkr_dependents(self._db)

                logger.info(
                    f"IBKR reconnected successfully after "
                    f"{self._reconnect_attempts} attempt(s) "
                    f"(client_id={self.config.daemon.client_id})"
                )
                self._reconnect_attempts = 0
                self._ibkr_ever_connected = True
                return True

        except Exception as e:
            self._reconnect_attempts += 1
            if self._reconnect_attempts <= 3 or self._reconnect_attempts % 20 == 0:
                logger.debug(
                    f"IBKR reconnection attempt #{self._reconnect_attempts} "
                    f"failed: {e}"
                )
            return False

    def _init_ibkr_dependents(self, db: Session) -> None:
        """(Re-)initialize components that depend on a live IBKR connection.

        Called after a successful reconnection to wire up PositionMonitor,
        ExitManager, and update references on ActionExecutor + EventDetector.
        """
        try:
            from src.config.baseline_strategy import BaselineStrategy, ExitRules
            from src.execution.exit_manager import ExitManager
            from src.execution.position_monitor import PositionMonitor
            from src.data.repositories import PositionRepository, TradeRepository

            exit_cfg = self.config.exit_rules
            baseline_config = BaselineStrategy(
                exit_rules=ExitRules(
                    profit_target=exit_cfg.profit_target,
                    stop_loss=exit_cfg.stop_loss,
                    time_exit_dte=exit_cfg.time_exit_dte,
                ),
            )
            self.position_monitor = PositionMonitor(
                ibkr_client=self.ibkr_client,
                config=baseline_config,
                position_repository=PositionRepository(db),
                trade_repository=TradeRepository(db),
            )
            self.exit_manager = ExitManager(
                ibkr_client=self.ibkr_client,
                position_monitor=self.position_monitor,
                config=baseline_config,
                dry_run=False,
            )

            # Update references on ActionExecutor
            self.executor.ibkr_client = self.ibkr_client
            self.executor.exit_manager = self.exit_manager

            # Update references on EventDetector
            if hasattr(self, "event_detector"):
                self.event_detector.ibkr_client = self.ibkr_client
                self.event_detector.position_monitor = self.position_monitor

            logger.info(
                f"IBKR-dependent components re-initialized "
                f"(profit_target={exit_cfg.profit_target}, "
                f"stop_loss={exit_cfg.stop_loss})"
            )
        except Exception as e:
            logger.warning(f"IBKR dependent init failed after reconnect: {e}")

    def _maybe_fire_disconnect_alert(self, now_et: datetime) -> None:
        """Fire a debounced audio/popup alert when IBKR is disconnected.

        Respects cooldown (default 5 min) to avoid alert fatigue.
        Skips on non-trading days.

        Audio selection:
        - Never connected → "start tws reminder" (startup reminder)
        - Was connected, then lost → "lost connection"
        """
        if not self.calendar.is_trading_day(now_et):
            return

        elapsed = time.monotonic() - self._last_reconnect_alert_time
        cooldown = self.config.daemon.reconnect_alert_cooldown_seconds
        if elapsed < cooldown:
            return

        if self._ibkr_ever_connected:
            # Had a working connection that dropped
            audio = self.config.daemon.reconnect_disconnect_audio_path
            title = "TAAD: IBKR Connection Lost"
            message = (
                f"TWS/IBGateway connection dropped — {self._reconnect_attempts} "
                f"reconnection attempt(s) so far. Restart TWS to resume trading."
            )
        else:
            # Never connected — remind user to start TWS
            audio = self.config.daemon.reconnect_alert_audio_path
            title = "TAAD: TWS Not Running"
            message = (
                f"TWS/IBGateway still unreachable — {self._reconnect_attempts} "
                f"reconnection attempt(s) so far. Start TWS to resume trading."
            )

        self._fire_macos_alert(title=title, message=message, audio_override=audio)
        self._last_reconnect_alert_time = time.monotonic()

    def _maybe_fire_premarket_alert(self, now_et: datetime, today: date) -> None:
        """Fire a one-shot alert N minutes before market open.

        Only fires once per day, and only if IBKR is still disconnected.
        """
        if self._premarket_alert_sent_today == today:
            return

        if self.calendar.is_market_open(now_et):
            return  # Already open — too late for pre-market alert

        if not self.calendar.is_trading_day(now_et):
            return

        time_until = self.calendar.time_until_open(now_et)
        threshold_minutes = self.config.daemon.premarket_alert_minutes
        threshold = timedelta(minutes=threshold_minutes)

        if timedelta(0) < time_until <= threshold:
            minutes_left = int(time_until.total_seconds() / 60)
            self._fire_macos_alert(
                title="TAAD: Market Opens Soon — TWS Not Running!",
                message=(
                    f"Market opens in {minutes_left} minutes but IBKR is "
                    f"disconnected. Start TWS NOW to avoid missing trades."
                ),
            )
            self._premarket_alert_sent_today = today
            logger.warning(
                f"Pre-market alert fired: {minutes_left} min until open, "
                f"IBKR still disconnected"
            )

    def _fire_macos_alert(
        self,
        title: str,
        message: str,
        audio_override: str = "",
    ) -> None:
        """Fire a macOS audio + popup alert (non-blocking, non-fatal).

        - Plays audio file via ``afplay`` if configured and file exists
        - Shows popup dialog via ``osascript`` with 30-second auto-dismiss
        - Silently skips on non-macOS platforms

        Args:
            title: Dialog title
            message: Dialog body text
            audio_override: Specific audio path to use. Falls back to
                reconnect_alert_audio_path if empty.
        """
        if platform.system() != "Darwin":
            return

        # Audio alert
        audio_path = audio_override or self.config.daemon.reconnect_alert_audio_path
        if audio_path:
            from pathlib import Path

            resolved = Path(audio_path).expanduser()
            if resolved.exists():
                try:
                    subprocess.Popen(
                        ["afplay", str(resolved)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except Exception as e:
                    logger.debug(f"Audio alert failed: {e}")
            else:
                logger.debug(f"Audio file not found: {resolved}")

        # Popup dialog (auto-dismiss after 30s)
        try:
            escaped_msg = message.replace('"', '\\"')
            escaped_title = title.replace('"', '\\"')
            script = (
                f'display dialog "{escaped_msg}" '
                f'with title "{escaped_title}" '
                f'buttons {{"OK"}} default button "OK" '
                f"giving up after 30"
            )
            subprocess.Popen(
                ["osascript", "-e", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.debug(f"Popup alert failed: {e}")

    async def _heartbeat_loop(self) -> None:
        """Background heartbeat loop."""
        interval = self.config.daemon.heartbeat_interval_seconds
        while self._running:
            try:
                ibkr_ok = self.ibkr_client is not None and self.ibkr_client.is_connected()
                self.health.heartbeat(ibkr_connected=ibkr_ok)
            except Exception as e:
                logger.error(f"Heartbeat failed: {e}")
            await asyncio.sleep(interval)

    async def _time_based_emitter(self) -> None:
        """Emit time-based events using MarketCalendar.

        Emits MARKET_OPEN, MARKET_CLOSE, EOD_REFLECTION, SCHEDULED_CHECK
        at appropriate times. Also drives periodic IBKR reconnection attempts
        when the connection is down.
        """
        last_market_open_emitted: Optional[date] = None
        last_market_close_emitted: Optional[date] = None
        last_eod_reflection_emitted: Optional[date] = None
        last_scheduled_check = datetime.utcnow()
        last_reconnect_attempt: float = 0.0

        logger.info("Time-based emitter started (30s check interval)")

        while self._running:
            try:
                now_et = datetime.now(ET)
                today = now_et.date()

                # --- IBKR reconnection check ---
                disconnected = (
                    self.ibkr_client is None
                    or not self.ibkr_client.is_connected()
                )
                if disconnected and self.calendar.is_trading_day(now_et):
                    reconnect_interval = (
                        self.config.daemon.reconnect_interval_seconds
                    )
                    elapsed = time.monotonic() - last_reconnect_attempt
                    if elapsed >= reconnect_interval:
                        last_reconnect_attempt = time.monotonic()

                        if self.calendar.is_market_open(now_et):
                            logger.warning(
                                "IBKR disconnected during market hours — "
                                "attempting reconnection"
                            )
                        self._maybe_fire_premarket_alert(now_et, today)
                        self._maybe_fire_disconnect_alert(now_et)

                        was_ever_connected = self._ibkr_ever_connected
                        reconnected = self._attempt_ibkr_reconnection()
                        if reconnected:
                            logger.info(
                                "IBKR reconnected — resuming normal operation"
                            )
                            # "reestablished" only if we had a prior connection;
                            # otherwise this is the first successful connect
                            audio = (
                                self.config.daemon.reconnect_success_audio_path
                                if was_ever_connected
                                else ""
                            )
                            self._fire_macos_alert(
                                title="TAAD: TWS Connected",
                                message="IBKR connection restored. Trading will resume.",
                                audio_override=audio,
                            )

                # Market open event (once per day)
                if (
                    self.calendar.is_market_open(now_et)
                    and last_market_open_emitted != today
                ):
                    logger.info(f"Emitting MARKET_OPEN (time={now_et.strftime('%H:%M ET')})")
                    self.event_bus.emit(EventType.MARKET_OPEN)
                    last_market_open_emitted = today
                    # Reset SCHEDULED_CHECK timer so it doesn't fire immediately
                    # after MARKET_OPEN (both would see same staged candidates
                    # and produce duplicate EXECUTE_TRADES decisions)
                    last_scheduled_check = datetime.utcnow()

                # Market close event (once per day, at close hour).
                # Window is 30 minutes wide to survive daemon restarts
                # near market close. Also triggers on startup if daemon
                # starts after close and MARKET_CLOSE wasn't emitted yet.
                close_hour = self.calendar.REGULAR_CLOSE.hour
                if (
                    now_et.hour == close_hour
                    and now_et.minute < 30
                    and last_market_close_emitted != today
                    and self.calendar.is_trading_day(now_et)
                ):
                    logger.info("Emitting MARKET_CLOSE")
                    self.event_bus.emit(EventType.MARKET_CLOSE)
                    last_market_close_emitted = today

                # EOD reflection (close + 30 minutes)
                if (
                    now_et.hour == close_hour
                    and now_et.minute >= 30
                    and now_et.minute < 35
                    and last_eod_reflection_emitted != today
                    and self.calendar.is_trading_day(now_et)
                ):
                    logger.info("Emitting EOD_REFLECTION")
                    self.event_bus.emit(EventType.EOD_REFLECTION)
                    last_eod_reflection_emitted = today

                # Periodic scheduled check (every 15 minutes during market hours)
                if (
                    self.calendar.is_market_open(now_et)
                    and (datetime.utcnow() - last_scheduled_check).total_seconds() > 900
                ):
                    logger.info("Emitting SCHEDULED_CHECK")
                    self.event_bus.emit(EventType.SCHEDULED_CHECK)
                    last_scheduled_check = datetime.utcnow()

            except Exception as e:
                logger.error(f"Time-based emitter error: {e}", exc_info=True)

            await asyncio.sleep(30)  # Check every 30 seconds


def start_daemon(config_path: Optional[str] = None) -> None:
    """Entry point to start the daemon.

    Args:
        config_path: Optional path to phase5.yaml
    """
    config = load_phase5_config(config_path)
    daemon = TAADDaemon(config=config)
    asyncio.run(daemon.run())

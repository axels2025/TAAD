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
import signal
import time
from datetime import datetime, date, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from loguru import logger
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
from src.data.models import DaemonEvent, DecisionAudit, ScanOpportunity
from src.services.market_calendar import MarketCalendar
from src.tools.ibkr_client import IBKRClient


ET = ZoneInfo("America/New_York")


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
        self._last_scheduled_fingerprint: str = ""

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
        self.executor = ActionExecutor(db, self.governor, ibkr_client=self.ibkr_client)
        self.health = HealthMonitor(
            db,
            pid_file=self.config.daemon.pid_file,
            heartbeat_interval=self.config.daemon.heartbeat_interval_seconds,
        )
        self.learning = LearningLoop(db, self.reasoning, self.memory)
        self.calendar = MarketCalendar()

        # Phase 6: Initialize guardrail registry
        self.guardrails = GuardrailRegistry(self.config.guardrails)
        self.guardrails.register_context_validator(ContextValidator())
        self.guardrails.register_output_validator(OutputValidator())
        self.guardrails.register_output_validator(NumericalGroundingChecker())
        self.execution_gate = ExecutionGate()
        self.guardrails.register_execution_gate(self.execution_gate)
        self.confidence_calibrator = ConfidenceCalibrator()
        self.entropy_monitor = ReasoningEntropyMonitor()

        # Sync autonomy level from working memory
        self.governor.level = self.memory.autonomy_level

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
            self.health.start()
            self._running = True

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

            logger.info(
                f"Daemon running (pid={__import__('os').getpid()}, "
                f"autonomy=L{self.governor.level})"
            )

            # Log market status on startup so operator knows what to expect
            now_et = datetime.now(ET)
            session_info = self.calendar.format_session_info(now_et)
            if self.calendar.is_market_open(now_et):
                logger.info(
                    f"Market is OPEN — first MARKET_OPEN event will emit within ~30s"
                )
            else:
                next_open = session_info.get("next_open", "unknown")
                time_until = session_info.get("time_until_open", "unknown")
                logger.info(
                    f"Market is CLOSED (session={session_info['session']}). "
                    f"Next open: {next_open} ({time_until}). "
                    f"Daemon will idle until then."
                )

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
            for task_name in ("heartbeat_task", "time_emitter_task"):
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
        """Async-safe signal handler that unblocks the event bus immediately."""
        sig_name = sig.name if hasattr(sig, "name") else str(sig)
        logger.info(f"Received {sig_name}, requesting graceful shutdown")
        self.health._shutdown_requested = True
        self.event_bus.stop()

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
        event_type = event.event_type
        self.event_bus.mark_processing(event)
        self.health.record_event(event_type)

        logger.info(f"Processing event: {event_type} (id={event.id})")

        # Human-approved decisions bypass Claude reasoning and autonomy gate
        if event_type == "HUMAN_OVERRIDE" and event.payload and event.payload.get("decision_id"):
            await self._process_human_approval(event, db)
            return

        # Run sync + reconcile on market close (before Claude reasoning)
        if event_type == "MARKET_CLOSE":
            await self._run_eod_sync(db)

        try:
            # Step 1: Assemble context
            context = self.memory.assemble_context(event_type)

            # Step 1.5: Enrich context with live IBKR data
            await self._enrich_context(context, db)

            # Step 1.6: Pre-Claude context validation (Phase 6)
            all_guardrail_results = []
            ctx_results = self.guardrails.validate_context(context)
            all_guardrail_results.extend(ctx_results)

            if self.guardrails.has_block(ctx_results):
                block_reasons = self.guardrails.get_block_reasons(ctx_results)
                logger.warning(f"Context guardrail blocked: {'; '.join(block_reasons)}")
                decision = DecisionOutput(
                    action="MONITOR_ONLY",
                    confidence=1.0,
                    reasoning=f"Guardrail blocked: {'; '.join(block_reasons)}",
                    key_factors=["guardrail_block"],
                )
            else:
                # Step 1.7: Skip Claude if SCHEDULED_CHECK with no material changes
                skip_claude = False
                if event_type == "SCHEDULED_CHECK":
                    fp = self._compute_context_fingerprint(context)
                    if fp == self._last_scheduled_fingerprint:
                        logger.info(
                            "SCHEDULED_CHECK skipped: no material context changes"
                        )
                        decision = DecisionOutput(
                            action="MONITOR_ONLY",
                            confidence=1.0,
                            reasoning="No material context changes since last check",
                            key_factors=["no_change_skip"],
                        )
                        skip_claude = True
                    else:
                        self._last_scheduled_fingerprint = fp

                if not skip_claude:
                    # Step 2: Reason with Claude
                    decision = self.reasoning.reason(
                        context=context,
                        event_type=event_type,
                        event_payload=event.payload,
                    )

                    # Step 2.5: Post-Claude output validation (Phase 6)
                    out_results = self.guardrails.validate_output(decision, context)
                    all_guardrail_results.extend(out_results)

                    if self.guardrails.has_block(out_results):
                        block_reasons = self.guardrails.get_block_reasons(out_results)
                        logger.warning(
                            f"Output guardrail overriding {decision.action} -> MONITOR_ONLY: "
                            f"{'; '.join(block_reasons)}"
                        )
                        decision = DecisionOutput(
                            action="MONITOR_ONLY",
                            confidence=decision.confidence,
                            reasoning=f"[Guardrail override] {decision.reasoning}",
                            key_factors=decision.key_factors + ["guardrail_override"],
                            risks_considered=decision.risks_considered,
                            metadata=decision.metadata,
                        )

            # Feed reasoning to entropy monitor (Phase 6)
            self.entropy_monitor.record_reasoning(
                decision.reasoning or "", decision.key_factors or []
            )

            # Step 3: Log decision to audit
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
                input_tokens=self.reasoning._reasoning_agent.total_input_tokens,
                output_tokens=self.reasoning._reasoning_agent.total_output_tokens,
                model_used=self.config.claude.reasoning_model,
                cost_usd=self.reasoning._reasoning_agent.session_cost,
                guardrail_flags=self.guardrails.results_to_dict(all_guardrail_results) if all_guardrail_results else None,
            )

            # Step 4: Execute (with execution gate)
            exec_context = self._build_execution_context(context, event)

            # Step 3.5: Pre-execution gate (Phase 6)
            exec_gate_results = self.guardrails.validate_execution(
                decision, context, ibkr_client=self.ibkr_client
            )
            all_guardrail_results.extend(exec_gate_results)

            if self.guardrails.has_block(exec_gate_results):
                block_reasons = self.guardrails.get_block_reasons(exec_gate_results)
                logger.warning(f"Execution gate blocked: {'; '.join(block_reasons)}")
                from src.agentic.action_executor import ExecutionResult
                result = ExecutionResult(
                    success=False,
                    action=decision.action,
                    message=f"Execution gate blocked: {'; '.join(block_reasons)}",
                )
            else:
                result = await self.executor.execute(decision, context=exec_context)

            # Update audit with execution result
            audit.autonomy_approved = result.success and result.action != "REQUEST_HUMAN_REVIEW"
            audit.executed = result.success and result.action not in ("MONITOR_ONLY", "REQUEST_HUMAN_REVIEW")
            audit.execution_result = {"message": result.message, "data": result.data}
            if result.error:
                audit.execution_error = result.error
            # Update guardrail_flags with any execution gate results
            if exec_gate_results:
                audit.guardrail_flags = self.guardrails.results_to_dict(all_guardrail_results)

            db.add(audit)
            db.commit()

            self.health.record_decision()

            # Step 5: Update working memory
            self.memory.add_decision({
                "timestamp": datetime.utcnow().isoformat(),
                "event_type": event_type,
                "action": decision.action,
                "confidence": decision.confidence,
                "reasoning": decision.reasoning[:200],
                "executed": audit.executed,
                "result": result.message[:200],
            })

            # Mark event complete
            self.event_bus.mark_completed(event)

            logger.info(
                f"Event processed: {event_type} -> {decision.action} "
                f"(confidence={decision.confidence:.2f}, executed={audit.executed})"
            )

        except Exception as e:
            logger.error(f"Event processing failed: {e}", exc_info=True)
            self.event_bus.mark_failed(event, str(e))
            self.health.record_error()

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

            # Reconstruct the decision from audit record
            decision = DecisionOutput(
                action=audit.action,
                confidence=audit.confidence or 0.0,
                reasoning=audit.reasoning or "",
                key_factors=audit.key_factors or [],
                risks_considered=audit.risks_considered or [],
                metadata=(audit.execution_result or {}).get("data", {}) or {},
            )

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
                "timestamp": datetime.utcnow().isoformat(),
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
            from src.services.order_reconciliation import OrderReconciliation

            reconciler = OrderReconciliation(self.ibkr_client)

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
            else:
                logger.info("  Positions in sync - no discrepancies")

            logger.info("EOD Sync & Reconcile complete")

            # Expire any remaining pre-execution staged candidates
            self._auto_unstage_eod(db)

        except Exception as e:
            logger.error(f"EOD sync failed: {e}", exc_info=True)

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

    def _build_execution_context(self, context, event: DaemonEvent) -> dict:
        """Build execution context for autonomy gate evaluation.

        Args:
            context: ReasoningContext
            event: The current event

        Returns:
            Context dict for autonomy checks
        """
        return {
            "margin_utilization": context.market_context.get("margin_utilization", 0.0),
            "vix": context.market_context.get("vix", 0.0),
            "vix_change_pct": context.market_context.get("vix_change_pct", 0.0),
            "data_stale": context.market_context.get("data_stale", False),
            "consecutive_losses": context.market_context.get("consecutive_losses", 0),
            "is_first_trade_of_day": self._is_first_trade_of_day(),
        }

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
        """
        if self.ibkr_client is None or not self.ibkr_client.is_connected():
            ctx.market_context["data_stale"] = True
            return

        try:
            from src.services.market_conditions import MarketConditionMonitor

            monitor = MarketConditionMonitor(self.ibkr_client)
            conditions = await monitor.check_conditions()

            # Store previous VIX for change calculation
            prev_vix = self.memory.market_context.get("vix", 0.0)

            ctx.market_context["vix"] = conditions.vix
            ctx.market_context["spy_price"] = conditions.spy_price
            ctx.market_context["conditions_favorable"] = conditions.conditions_favorable
            ctx.market_context["data_stale"] = False

            if prev_vix and prev_vix > 0 and conditions.vix > 0:
                ctx.market_context["vix_change_pct"] = round(
                    (conditions.vix - prev_vix) / prev_vix * 100, 2
                )

            # Persist to working memory for cross-event continuity
            self.memory.update_market_context(ctx.market_context)

            logger.info(
                f"Market data enriched: VIX={conditions.vix:.1f}, "
                f"SPY=${conditions.spy_price:.2f}, "
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

                    contract = self.ibkr_client.get_option_contract(
                        symbol=pos["symbol"],
                        expiration=exp_str,
                        strike=pos["strike"],
                        right="P",
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

                except Exception as e:
                    logger.debug(f"P&L enrichment failed for {pos.get('symbol')}: {e}")
                    continue

        except Exception as e:
            logger.warning(f"Position P&L enrichment failed: {e}")

    def _enrich_staged_candidates(self, ctx: ReasoningContext, db: Session) -> None:
        """Query staged/ready/confirmed ScanOpportunities for context.

        Args:
            ctx: ReasoningContext to enrich
            db: Database session
        """
        try:
            candidates = (
                db.query(ScanOpportunity)
                .filter(
                    ScanOpportunity.state.in_(["STAGED", "READY", "CONFIRMED"]),
                    ScanOpportunity.executed == False,
                )
                .order_by(ScanOpportunity.portfolio_rank.asc())
                .limit(20)
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
                }
                for c in candidates
            ]

            if ctx.staged_candidates:
                logger.info(
                    f"Staged candidates enriched: {len(ctx.staged_candidates)} "
                    f"candidates in context"
                )

        except Exception as e:
            logger.warning(f"Staged candidates query failed: {e}")

    async def _heartbeat_loop(self) -> None:
        """Background heartbeat loop."""
        interval = self.config.daemon.heartbeat_interval_seconds
        while self._running:
            try:
                self.health.heartbeat()
            except Exception as e:
                logger.error(f"Heartbeat failed: {e}")
            await asyncio.sleep(interval)

    async def _time_based_emitter(self) -> None:
        """Emit time-based events using MarketCalendar.

        Emits MARKET_OPEN, MARKET_CLOSE, EOD_REFLECTION, SCHEDULED_CHECK
        at appropriate times.
        """
        last_market_open_emitted: Optional[date] = None
        last_market_close_emitted: Optional[date] = None
        last_eod_reflection_emitted: Optional[date] = None
        last_scheduled_check = datetime.utcnow()

        logger.info("Time-based emitter started (30s check interval)")

        while self._running:
            try:
                now_et = datetime.now(ET)
                today = now_et.date()

                # Market open event (once per day)
                if (
                    self.calendar.is_market_open(now_et)
                    and last_market_open_emitted != today
                ):
                    logger.info(f"Emitting MARKET_OPEN (time={now_et.strftime('%H:%M ET')})")
                    self.event_bus.emit(EventType.MARKET_OPEN)
                    last_market_open_emitted = today

                # Market close event (once per day, at 4:00 PM ET)
                if (
                    now_et.hour == 16
                    and now_et.minute < 5
                    and last_market_close_emitted != today
                    and self.calendar.is_trading_day(now_et)
                ):
                    logger.info("Emitting MARKET_CLOSE")
                    self.event_bus.emit(EventType.MARKET_CLOSE)
                    last_market_close_emitted = today

                # EOD reflection (4:30 PM ET)
                if (
                    now_et.hour == 16
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

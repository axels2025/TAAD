"""FastAPI web dashboard for daemon monitoring and control.

Provides 9 REST endpoints for status, positions, decisions, approvals,
and cost monitoring. Bearer token auth from config.
"""

import os
import signal
import subprocess
import sys
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from src.utils.timezone import trading_date, utc_now

from loguru import logger

try:
    from fastapi import Depends, FastAPI, HTTPException, Security
    from fastapi.responses import HTMLResponse
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
    from pydantic import BaseModel

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

from src.data.database import get_db_session, get_session
from src.data.models import (
    ClaudeApiCost,
    DaemonEvent,
    DaemonHealth,
    DaemonNotification,
    DecisionAudit,
    GuardrailMetric,
    ScanOpportunity,
    Trade,
)
from src.services.market_calendar import MarketCalendar


from src.config.exchange_profile import get_active_profile as _get_active_profile

ET = _get_active_profile().timezone


def _current_session_start() -> datetime:
    """Get the start of the current (or most recent) trading session.

    Returns the most recent market open time on a trading day, used as the
    boundary for rejection persistence queries.
    """
    from datetime import timedelta

    cal = MarketCalendar()
    now = datetime.now(ET)
    today = now.date()
    market_open_time = cal.REGULAR_OPEN

    if cal.is_trading_day(now) and now.time() >= market_open_time:
        return datetime.combine(today, market_open_time, ET)

    check = today
    for _ in range(10):
        check -= timedelta(days=1)
        check_dt = datetime.combine(check, market_open_time, ET)
        if cal.is_trading_day(check_dt):
            return check_dt

    return datetime.combine(today, datetime.min.time(), ET)


def _read_watchdog_status() -> dict:
    """Read watchdog status from JSON file and check process liveness.

    Returns:
        Dict with active, pid, last_check, daemon_assessment keys.
    """
    import json
    from pathlib import Path

    result = {
        "active": False,
        "pid": None,
        "last_check": None,
        "daemon_assessment": None,
    }

    # Check if watchdog process is alive
    pid_path = Path("run/watchdog.pid")
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
            os.kill(pid, 0)
            result["active"] = True
            result["pid"] = pid
        except (ValueError, ProcessLookupError, PermissionError):
            pass

    # Read status JSON
    status_path = Path("run/watchdog_status.json")
    if status_path.exists():
        try:
            data = json.loads(status_path.read_text())
            result["last_check"] = data.get("checked_at")
            result["daemon_assessment"] = data.get("overall")
        except (json.JSONDecodeError, OSError):
            pass

    return result


def create_dashboard_app(auth_token: str = "") -> "FastAPI":
    """Create the FastAPI dashboard application.

    Args:
        auth_token: Bearer token for authentication (empty = no auth)

    Returns:
        FastAPI app instance
    """
    if not FASTAPI_AVAILABLE:
        raise ImportError("FastAPI not installed. Run: pip install fastapi uvicorn")

    app = FastAPI(title="TAAD Dashboard", version="1.0.0")
    security = HTTPBearer(auto_error=False)

    def verify_token(
        credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
    ) -> None:
        """Verify bearer token if auth is configured."""
        if not auth_token:
            return  # No auth configured
        if not credentials or credentials.credentials != auth_token:
            raise HTTPException(status_code=401, detail="Invalid token")

    # Include scanner router
    from src.agentic.scanner_api import create_scanner_router, get_scanner_html

    app.include_router(create_scanner_router(verify_token))

    # Auth bootstrap script injected into all sub-page HTMLs
    _AUTH_SCRIPT = """<script>
(function(){
  var t = new URLSearchParams(location.search).get('token') || sessionStorage.getItem('taad_token') || '';
  if (t) sessionStorage.setItem('taad_token', t);
  var _f = window.fetch;
  window.fetch = function(u, o) {
    if (t && typeof u === 'string' && u.startsWith('/api')) {
      o = o || {}; o.headers = o.headers || {};
      o.headers['Authorization'] = 'Bearer ' + t;
    }
    return _f.call(this, u, o);
  };
  if (t) document.querySelectorAll('a[href^="/"]').forEach(function(a) {
    var u = new URL(a.href); u.searchParams.set('token', t); a.href = u.toString();
  });
})();
</script>"""

    def _inject_auth(html: str) -> str:
        """Inject auth bootstrap script into HTML page."""
        return html.replace("<script>", _AUTH_SCRIPT + "\n<script>", 1)

    @app.get("/scanner", response_class=HTMLResponse)
    def scanner_page():
        """HTML scanner page."""
        return _inject_auth(get_scanner_html())

    # Include config router
    from src.agentic.config_api import create_config_router, get_config_html

    app.include_router(create_config_router(verify_token))

    @app.get("/config", response_class=HTMLResponse)
    def config_page():
        """HTML config editor page."""
        return _inject_auth(get_config_html())

    # Include guardrails router
    from src.agentic.guardrails_api import create_guardrails_router, get_guardrails_html

    app.include_router(create_guardrails_router(verify_token))

    @app.get("/guardrails", response_class=HTMLResponse)
    def guardrails_page():
        """HTML guardrails review page."""
        return _inject_auth(get_guardrails_html())

    # Include prompt editor router
    from src.agentic.prompt_api import create_prompt_router, get_prompt_html

    app.include_router(create_prompt_router(verify_token))

    @app.get("/prompts", response_class=HTMLResponse)
    def prompts_page():
        """HTML prompt editor page."""
        return _inject_auth(get_prompt_html())

    # Scanner settings page (uses existing /api/scanner/* endpoints)
    from src.agentic.scanner_settings_page import get_scanner_settings_html

    @app.get("/scanner-settings", response_class=HTMLResponse)
    def scanner_settings_page():
        """HTML scanner settings page."""
        return _inject_auth(get_scanner_settings_html())

    @app.get("/api/status")
    def get_status(token: None = Depends(verify_token)):
        """Get daemon status with live process check."""
        from pathlib import Path

        from src.agentic.health_monitor import HealthMonitor

        live_pid = HealthMonitor.is_daemon_running()

        with get_db_session() as db:
            health = db.query(DaemonHealth).get(1)
            if not health:
                return {
                    "status": "stopped" if not live_pid else "unknown",
                    "pid": live_pid,
                    "process_alive": live_pid is not None,
                    "message": "No health record",
                }

            # Override DB status if process is dead
            actual_status = health.status
            if not live_pid and actual_status in ("running", "paused"):
                actual_status = "stopped"

            # Watchdog status
            watchdog = _read_watchdog_status()

            return {
                "pid": live_pid or health.pid,
                "status": actual_status,
                "process_alive": live_pid is not None,
                "last_heartbeat": str(health.last_heartbeat) if health.last_heartbeat else None,
                "uptime_seconds": health.uptime_seconds if live_pid else 0,
                "events_processed_today": health.events_processed_today,
                "decisions_made_today": health.decisions_made_today,
                "errors_today": health.errors_today,
                "autonomy_level": health.autonomy_level,
                "message": health.message,
                "started_at": str(health.started_at) if health.started_at else None,
                "ibkr_connected": getattr(health, "ibkr_connected", False) or False,
                "stop_requested": Path("run/stop_requested").exists(),
                "watchdog": watchdog,
            }

    @app.get("/api/positions")
    def get_positions(token: None = Depends(verify_token)):
        """Get open positions."""
        today = trading_date()
        with get_db_session() as db:
            trades = db.query(Trade).filter(Trade.exit_date.is_(None)).all()
            return [
                {
                    "trade_id": t.trade_id,
                    "symbol": t.symbol,
                    "strike": t.strike,
                    "option_type": t.option_type,
                    "expiration": str(t.expiration),
                    "entry_premium": t.entry_premium,
                    "contracts": t.contracts,
                    "entry_date": str(t.entry_date),
                    "dte": (t.expiration - today).days if t.expiration else None,
                }
                for t in trades
            ]

    @app.get("/api/portfolio-greeks")
    def get_portfolio_greeks_endpoint(token: None = Depends(verify_token)):
        """Get aggregated portfolio Greeks from latest snapshots."""
        from src.services.portfolio_greeks import get_portfolio_greeks

        with get_db_session() as db:
            return get_portfolio_greeks(db)

    @app.post("/api/refresh-greeks")
    def refresh_greeks_endpoint(token: None = Depends(verify_token)):
        """Fetch live Greeks from IBKR and update snapshots."""
        from src.config.base import IBKRConfig
        from src.services.portfolio_greeks import refresh_greeks
        from src.tools.ibkr_client import IBKRClient

        config = IBKRConfig()
        config.client_id = 10  # Avoid conflict with daemon (client_id=1)
        client = IBKRClient(config, suppress_errors=True)
        try:
            connected = client.connect()
            if not connected:
                raise HTTPException(
                    status_code=503,
                    detail="Could not connect to IBKR. Is TWS/Gateway running?",
                )
            with get_db_session() as db:
                return refresh_greeks(client, db)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Greeks refresh failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            try:
                client.disconnect()
            except Exception:
                pass

    @app.get("/api/staged")
    def get_staged(token: None = Depends(verify_token)):
        """Get staged trades awaiting execution."""
        with get_db_session() as db:
            staged = (
                db.query(ScanOpportunity)
                .filter(
                    ScanOpportunity.state.in_(
                        ["STAGED", "VALIDATING", "READY", "CONFIRMED"]
                    ),
                    ScanOpportunity.executed == False,  # noqa: E712
                )
                .order_by(ScanOpportunity.portfolio_rank.asc())
                .all()
            )
            total_margin = sum(s.staged_margin or 0 for s in staged)
            total_premium = sum(
                (s.staged_limit_price or 0) * (s.staged_contracts or 0) * 100
                for s in staged
            )
            return {
                "trades": [
                    {
                        "id": s.id,
                        "rank": s.portfolio_rank,
                        "symbol": s.symbol,
                        "strike": s.strike,
                        "expiration": str(s.expiration),
                        "limit_price": s.staged_limit_price,
                        "contracts": s.staged_contracts,
                        "margin": s.staged_margin,
                        "margin_source": s.staged_margin_source,
                        "delta": s.delta,
                        "otm_pct": s.otm_pct,
                        "iv": s.iv,
                        "stock_price": s.stock_price,
                        "state": s.state,
                        "staged_at": str(s.staged_at) if s.staged_at else None,
                        "execution_session": s.execution_session,
                        "trend": s.trend,
                    }
                    for s in staged
                ],
                "summary": {
                    "count": len(staged),
                    "total_margin": round(total_margin, 2),
                    "total_premium": round(total_premium, 2),
                },
            }

    @app.get("/api/decisions")
    def get_decisions(
        limit: int = 20,
        token: None = Depends(verify_token),
    ):
        """Get recent decisions."""
        with get_db_session() as db:
            decisions = (
                db.query(DecisionAudit)
                .order_by(DecisionAudit.timestamp.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": d.id,
                    "timestamp": str(d.timestamp),
                    "event_type": d.event_type,
                    "action": d.action,
                    "confidence": d.confidence,
                    "reasoning": d.reasoning,
                    "autonomy_approved": d.autonomy_approved,
                    "executed": d.executed,
                    "autonomy_level": d.autonomy_level,
                }
                for d in decisions
            ]

    @app.get("/api/decisions/{decision_id}")
    def get_decision_detail(
        decision_id: int,
        token: None = Depends(verify_token),
    ):
        """Get full details for a single decision."""
        with get_db_session() as db:
            d = db.query(DecisionAudit).get(decision_id)
            if not d:
                raise HTTPException(status_code=404, detail="Decision not found")
            return {
                "id": d.id,
                "timestamp": str(d.timestamp),
                "event_type": d.event_type,
                "action": d.action,
                "confidence": d.confidence,
                "autonomy_level": d.autonomy_level,
                "reasoning": d.reasoning,
                "key_factors": d.key_factors or [],
                "risks_considered": d.risks_considered or [],
                "autonomy_approved": d.autonomy_approved,
                "escalation_reason": d.escalation_reason,
                "human_override": d.human_override,
                "human_decision": d.human_decision,
                "human_decided_at": str(d.human_decided_at) if d.human_decided_at else None,
                "executed": d.executed,
                "execution_result": d.execution_result,
                "execution_error": d.execution_error,
                "input_tokens": d.input_tokens,
                "output_tokens": d.output_tokens,
                "model_used": d.model_used,
                "cost_usd": d.cost_usd,
            }

    @app.get("/api/queue")
    def get_pending_queue(token: None = Depends(verify_token)):
        """Get decisions pending human approval."""
        with get_db_session() as db:
            pending = (
                db.query(DecisionAudit)
                .filter(
                    DecisionAudit.executed == False,  # noqa: E712
                    DecisionAudit.action != "MONITOR_ONLY",
                    DecisionAudit.human_decision.is_(None),
                )
                .order_by(DecisionAudit.timestamp.desc())
                .limit(20)
                .all()
            )
            def _extract_target_symbol(audit):
                """Extract target symbol/trade_id from a CLOSE_POSITION decision."""
                if audit.action != "CLOSE_POSITION":
                    return None
                # Path C: decision_metadata column (multi-action plan)
                dm = audit.decision_metadata
                if isinstance(dm, dict):
                    tid = dm.get("trade_id") or dm.get("position_id")
                    if tid:
                        return str(tid)
                # Legacy paths via execution_result
                er = audit.execution_result or {}
                data = er.get("data", {})
                if isinstance(data, dict):
                    # Autonomy escalation path
                    meta = data.get("decision", {}).get("metadata", {})
                    tid = meta.get("trade_id") or meta.get("position_id")
                    if tid:
                        return str(tid)
                    # Guardrail block path
                    tid = data.get("trade_id") or data.get("position_id")
                    if tid:
                        return str(tid)
                # Try event payload
                if audit.event_id:
                    evt = db.query(DaemonEvent).get(audit.event_id)
                    if evt and evt.payload:
                        sym = evt.payload.get("symbol")
                        if sym:
                            return sym
                return None

            return [
                {
                    "id": d.id,
                    "plan_id": d.plan_id,
                    "plan_assessment": d.plan_assessment,
                    "timestamp": str(d.timestamp),
                    "action": d.action,
                    "confidence": d.confidence,
                    "reasoning": d.reasoning,
                    "key_factors": d.key_factors or [],
                    "risks_considered": d.risks_considered or [],
                    "event_type": d.event_type,
                    "autonomy_level": d.autonomy_level,
                    "guardrail_flags": d.guardrail_flags or [],
                    "execution_result": d.execution_result or {},
                    "decision_metadata": d.decision_metadata or {},
                    "target_symbol": _extract_target_symbol(d),
                }
                for d in pending
            ]

    @app.get("/api/notifications")
    def get_notifications(token: None = Depends(verify_token)):
        """Get active daemon notifications (self-updating, no approval needed)."""
        with get_db_session() as db:
            active = (
                db.query(DaemonNotification)
                .filter_by(status="active")
                .order_by(DaemonNotification.updated_at.desc())
                .all()
            )
            return [
                {
                    "id": n.id,
                    "key": n.notification_key,
                    "category": n.category,
                    "title": n.title,
                    "message": n.message,
                    "details": n.details or {},
                    "first_seen": str(n.first_seen_at),
                    "updated_at": str(n.updated_at),
                    "occurrence_count": n.occurrence_count,
                    "action_choices": getattr(n, "action_choices", None),
                    "chosen_action": getattr(n, "chosen_action", None),
                    "chosen_at": str(n.chosen_at) if getattr(n, "chosen_at", None) else None,
                }
                for n in active
            ]

    class NotificationActionRequest(BaseModel):
        action_key: str

    @app.post("/api/notifications/{notification_id}/action")
    def choose_notification_action(
        notification_id: int,
        request: NotificationActionRequest,
        token: None = Depends(verify_token),
    ):
        """Record user's chosen action on a notification.

        Validates action_key against the notification's action_choices.
        "keep_blocked" keeps the notification active; all others resolve it.
        """
        with get_db_session() as db:
            notif = db.query(DaemonNotification).get(notification_id)
            if not notif:
                raise HTTPException(status_code=404, detail="Notification not found")
            if notif.status != "active":
                return {"status": "already_resolved", "id": notification_id}

            # Validate action_key against available choices
            choices = getattr(notif, "action_choices", None) or []
            valid_keys = {c["key"] for c in choices if isinstance(c, dict)}
            if request.action_key not in valid_keys:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid action_key '{request.action_key}'. "
                           f"Valid keys: {sorted(valid_keys)}",
                )

            notif.chosen_action = request.action_key
            notif.chosen_at = utc_now()
            notif.updated_at = utc_now()

            # "keep_blocked" keeps notification active; others resolve it
            if request.action_key != "keep_blocked":
                notif.status = "resolved"
                notif.resolved_at = utc_now()

            db.commit()
            logger.info(
                f"Notification '{notif.notification_key}' action: "
                f"{request.action_key} (id={notification_id})"
            )
            return {
                "status": "action_recorded",
                "id": notification_id,
                "action_key": request.action_key,
            }

    class ApprovalRequest(BaseModel):
        decision: str = "approved"
        notes: str = ""

    @app.post("/api/approve/{decision_id}")
    def approve_decision(
        decision_id: int,
        request: ApprovalRequest,
        token: None = Depends(verify_token),
    ):
        """Approve a pending decision and trigger execution."""
        with get_db_session() as db:
            from src.agentic.event_bus import EventBus, EventType

            audit = db.query(DecisionAudit).get(decision_id)
            if not audit:
                raise HTTPException(status_code=404, detail="Decision not found")

            if audit.executed:
                return {"status": "already_executed", "decision_id": decision_id}
            if audit.human_decision:
                return {"status": f"already_{audit.human_decision}", "decision_id": decision_id}

            audit.human_decision = "approved"
            audit.human_decided_at = utc_now()
            audit.human_override = True
            db.commit()

            # Emit event so daemon executes
            event_bus = EventBus(db)
            event_bus.emit(
                EventType.HUMAN_OVERRIDE,
                payload={
                    "decision_id": audit.id,
                    "action": audit.action,
                    "confidence": audit.confidence,
                    "reasoning": audit.reasoning,
                    "key_factors": audit.key_factors,
                    "risks_considered": audit.risks_considered,
                },
            )

            return {"status": "approved", "decision_id": decision_id, "action": audit.action}

    @app.post("/api/reject/{decision_id}")
    def reject_decision(
        decision_id: int,
        request: ApprovalRequest,
        token: None = Depends(verify_token),
    ):
        """Reject a pending decision."""
        with get_db_session() as db:
            audit = db.query(DecisionAudit).get(decision_id)
            if not audit:
                raise HTTPException(status_code=404, detail="Decision not found")

            audit.human_decision = "rejected"
            audit.human_decided_at = utc_now()
            audit.human_override = True
            db.commit()

            return {"status": "rejected", "decision_id": decision_id}

    @app.post("/api/override-rejection/{decision_id}")
    def override_rejection(
        decision_id: int,
        token: None = Depends(verify_token),
    ):
        """Override a rejected decision: flip to approved and trigger execution.

        Use case: user rejected a CLOSE_POSITION, then changed their mind.
        This flips the decision to approved and emits a HUMAN_OVERRIDE event
        so the daemon executes it.
        """
        with get_db_session() as db:
            from src.agentic.event_bus import EventBus, EventType

            audit = db.query(DecisionAudit).get(decision_id)
            if not audit:
                raise HTTPException(status_code=404, detail="Decision not found")

            if audit.executed:
                return {"status": "already_executed", "decision_id": decision_id}
            if audit.human_decision != "rejected":
                return {
                    "status": f"not_rejected (current: {audit.human_decision})",
                    "decision_id": decision_id,
                }

            audit.human_decision = "approved"
            audit.human_decided_at = utc_now()
            audit.human_override = True
            db.commit()

            # Emit event so daemon executes
            event_bus = EventBus(db)
            event_bus.emit(
                EventType.HUMAN_OVERRIDE,
                payload={
                    "decision_id": audit.id,
                    "action": audit.action,
                    "confidence": audit.confidence,
                    "reasoning": audit.reasoning,
                    "key_factors": audit.key_factors,
                    "risks_considered": audit.risks_considered,
                    "source": "rejection_override",
                },
            )

            return {
                "status": "override_approved",
                "decision_id": decision_id,
                "action": audit.action,
            }

    @app.get("/api/rejected-today")
    def get_rejected_today(token: None = Depends(verify_token)):
        """Get decisions rejected this trading session that can be overridden.

        Uses trading-day boundary (not calendar midnight) so Friday
        rejections persist through the weekend until Monday 9:30 AM.
        """
        session_start = _current_session_start()
        session_start_utc = session_start.astimezone(
            ZoneInfo("UTC")
        ).replace(tzinfo=None)
        with get_db_session() as db:
            rejected = (
                db.query(DecisionAudit)
                .filter(
                    DecisionAudit.executed == False,  # noqa: E712
                    DecisionAudit.human_decision == "rejected",
                    DecisionAudit.human_decided_at >= session_start_utc,
                )
                .order_by(DecisionAudit.timestamp.desc())
                .limit(20)
                .all()
            )
            return [
                {
                    "id": d.id,
                    "timestamp": str(d.timestamp),
                    "action": d.action,
                    "confidence": d.confidence,
                    "reasoning": d.reasoning,
                    "event_type": d.event_type,
                    "rejected_at": str(d.human_decided_at) if d.human_decided_at else None,
                }
                for d in rejected
            ]

    @app.post("/api/pause")
    def pause_daemon(token: None = Depends(verify_token)):
        """Pause the daemon."""
        with get_db_session() as db:
            health = db.query(DaemonHealth).get(1)
            if health:
                health.status = "paused"
                health.message = "Paused via dashboard"
                db.commit()
            return {"status": "paused"}

    @app.post("/api/resume")
    def resume_daemon(token: None = Depends(verify_token)):
        """Resume the daemon."""
        with get_db_session() as db:
            health = db.query(DaemonHealth).get(1)
            if health:
                health.status = "running"
                health.message = "Resumed via dashboard"
                db.commit()
            return {"status": "resumed"}

    @app.post("/api/start")
    def start_daemon(token: None = Depends(verify_token)):
        """Start the daemon as a background process."""
        from pathlib import Path

        from src.agentic.health_monitor import HealthMonitor

        pid = HealthMonitor.is_daemon_running()
        if pid:
            return {"status": "already_running", "pid": pid}

        # Clear stop flag so watchdog knows this is an intentional start
        Path("run/stop_requested").unlink(missing_ok=True)

        # Find the nakedtrader executable in the same venv
        venv_bin = os.path.dirname(sys.executable)
        exe = os.path.join(venv_bin, "nakedtrader")
        if not os.path.exists(exe):
            raise HTTPException(status_code=500, detail=f"nakedtrader not found at {exe}")

        # Start daemon in background
        log_file = os.path.join(os.getcwd(), "logs", "daemon.log")
        os.makedirs(os.path.dirname(log_file), exist_ok=True)

        with open(log_file, "a") as log_f:
            proc = subprocess.Popen(
                [exe, "daemon", "start", "--fg"],
                stdout=log_f,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        logger.info(f"Daemon started via dashboard (pid={proc.pid})")
        return {"status": "started", "pid": proc.pid}

    @app.post("/api/stop")
    def stop_daemon(token: None = Depends(verify_token)):
        """Stop the daemon gracefully via SIGTERM."""
        from pathlib import Path

        from src.agentic.health_monitor import HealthMonitor

        pid = HealthMonitor.is_daemon_running()
        if not pid:
            return {"status": "not_running"}

        try:
            os.kill(pid, signal.SIGTERM)
            # Write stop flag so watchdog knows this was intentional
            Path("run").mkdir(parents=True, exist_ok=True)
            Path("run/stop_requested").touch()
            logger.info(f"SIGTERM sent to daemon (pid={pid}), stop flag written")
            return {"status": "stopping", "pid": pid}
        except ProcessLookupError:
            return {"status": "not_running"}
        except PermissionError:
            raise HTTPException(status_code=500, detail="Permission denied sending signal")

    @app.post("/api/restart-daemon")
    def restart_daemon(token: None = Depends(verify_token)):
        """Restart the daemon: stop existing process then start a new one."""
        import time
        from pathlib import Path

        from src.agentic.health_monitor import HealthMonitor

        pid = HealthMonitor.is_daemon_running()
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                logger.info(f"SIGTERM sent to daemon (pid={pid}) for restart")
                # Wait for process to exit (up to 10s)
                for _ in range(20):
                    time.sleep(0.5)
                    try:
                        os.kill(pid, 0)  # Check if still alive
                    except ProcessLookupError:
                        break
            except ProcessLookupError:
                pass
            except PermissionError:
                raise HTTPException(status_code=500, detail="Permission denied")

        # Clear stop flag so watchdog doesn't interfere
        Path("run/stop_requested").unlink(missing_ok=True)

        # Start fresh daemon
        venv_bin = os.path.dirname(sys.executable)
        exe = os.path.join(venv_bin, "nakedtrader")
        if not os.path.exists(exe):
            raise HTTPException(status_code=500, detail=f"nakedtrader not found at {exe}")

        log_file = os.path.join(os.getcwd(), "logs", "daemon.log")
        os.makedirs(os.path.dirname(log_file), exist_ok=True)

        with open(log_file, "a") as log_f:
            proc = subprocess.Popen(
                [exe, "daemon", "start", "--fg"],
                stdout=log_f,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        logger.info(f"Daemon restarted via dashboard (new pid={proc.pid})")
        return {"status": "restarted", "pid": proc.pid}

    @app.post("/api/restart-dashboard")
    def restart_dashboard(token: None = Depends(verify_token)):
        """Restart the dashboard process by re-exec'ing itself."""
        logger.info("Dashboard restart requested via UI")

        def _delayed_restart():
            """Give the HTTP response time to complete, then restart."""
            import time
            time.sleep(1)
            os.execv(sys.executable, [sys.executable] + sys.argv)

        import threading
        threading.Thread(target=_delayed_restart, daemon=True).start()
        return {"status": "restarting"}

    @app.post("/api/sync-orders")
    def sync_orders_endpoint(token: None = Depends(verify_token)):
        """Sync order status with IBKR (order fills, commissions, status)."""
        import asyncio

        from src.config.base import IBKRConfig
        from src.data.repositories import TradeRepository
        from src.services.order_reconciliation import OrderReconciliation
        from src.tools.ibkr_client import IBKRClient

        config = IBKRConfig()
        config.client_id = 10
        client = IBKRClient(config, suppress_errors=True)
        lines = []
        try:
            connected = client.connect()
            if not connected:
                return {"status": "error", "lines": ["IBKR not connected. Is TWS/Gateway running?"]}

            with get_db_session() as db:
                trade_repo = TradeRepository(db)
                reconciler = OrderReconciliation(client, trade_repo)

                report = asyncio.get_event_loop().run_until_complete(
                    reconciler.sync_all_orders(include_filled=True)
                )

                lines.append(f"Order Sync — {report.date}")
                lines.append(f"Reconciled: {report.total_reconciled}")
                lines.append(f"Discrepancies: {report.total_discrepancies}")
                lines.append(f"Resolved: {report.total_resolved}")

                if report.reconciled:
                    lines.append("")
                    lines.append("SYMBOL     ORDER    DB STATUS     TWS STATUS    FILL     COMMISSION")
                    lines.append("-" * 72)
                    for t in report.reconciled:
                        disc = " *" if t.discrepancy else ""
                        fill = f"${t.fill_price:.2f}" if t.fill_price else "--"
                        comm = f"${t.commission:.2f}" if t.commission else "--"
                        lines.append(
                            f"{t.symbol:<10} {t.order_id:<8} {t.db_status:<13} "
                            f"{t.tws_status:<13} {fill:<8} {comm}{disc}"
                        )

                if report.orphans:
                    lines.append("")
                    lines.append(f"Orphan orders (in IBKR, not DB): {len(report.orphans)}")
                    for o in report.orphans:
                        sym = o.contract.symbol if hasattr(o, 'contract') else "?"
                        oid = o.order.orderId if hasattr(o, 'order') else "?"
                        lines.append(f"  {sym} order #{oid}")

                if report.missing_in_tws:
                    lines.append("")
                    lines.append(f"Missing in TWS (in DB only): {len(report.missing_in_tws)}")
                    for m in report.missing_in_tws:
                        sym = m.symbol if hasattr(m, 'symbol') else "?"
                        lines.append(f"  {sym} order #{getattr(m, 'order_id', '?')}")

                if not report.reconciled and not report.orphans and not report.missing_in_tws:
                    lines.append("No orders to sync.")

            return {"status": "ok", "lines": lines}
        except Exception as e:
            logger.error(f"Sync orders failed: {e}", exc_info=True)
            return {"status": "error", "lines": lines + [f"Error: {e}"]}
        finally:
            try:
                client.disconnect()
            except Exception:
                pass

    @app.post("/api/reconcile-positions")
    def reconcile_positions_endpoint(token: None = Depends(verify_token)):
        """Reconcile positions between DB and IBKR (detect mismatches, orphans, assignments)."""
        import asyncio

        from src.config.base import IBKRConfig
        from src.data.repositories import TradeRepository
        from src.services.order_reconciliation import OrderReconciliation
        from src.tools.ibkr_client import IBKRClient

        config = IBKRConfig()
        config.client_id = 10
        client = IBKRClient(config, suppress_errors=True)
        lines = []
        try:
            connected = client.connect()
            if not connected:
                return {"status": "error", "lines": ["IBKR not connected. Is TWS/Gateway running?"]}

            with get_db_session() as db:
                trade_repo = TradeRepository(db)
                reconciler = OrderReconciliation(client, trade_repo)

                report = asyncio.get_event_loop().run_until_complete(
                    reconciler.reconcile_positions()
                )

                lines.append("Position Reconciliation")
                lines.append("")

                if not report.has_discrepancies:
                    lines.append("All positions in sync — no discrepancies found.")
                else:
                    if report.quantity_mismatches:
                        lines.append(f"Quantity Mismatches: {len(report.quantity_mismatches)}")
                        lines.append("CONTRACT                           DB QTY   IBKR QTY   DIFF")
                        lines.append("-" * 62)
                        for m in report.quantity_mismatches:
                            lines.append(
                                f"{m.contract_key:<34} {m.db_quantity:<8} "
                                f"{m.ibkr_quantity:<10} {m.difference:+d}"
                            )
                        lines.append("")

                    if report.in_ibkr_not_db:
                        lines.append(f"In IBKR but not DB (orphans): {len(report.in_ibkr_not_db)}")
                        for key, pos in report.in_ibkr_not_db:
                            qty = pos.position if hasattr(pos, 'position') else '?'
                            lines.append(f"  {key}  qty={qty}")
                        lines.append("")

                    if report.in_db_not_ibkr:
                        lines.append(f"In DB but not IBKR (ghosts): {len(report.in_db_not_ibkr)}")
                        for key, trade in report.in_db_not_ibkr:
                            lines.append(f"  {key}")
                        lines.append("")

                    if report.assignments:
                        lines.append(f"Possible Assignments: {len(report.assignments)}")
                        for a in report.assignments:
                            lines.append(
                                f"  {a.symbol}: {a.shares} shares @ ${a.avg_cost:.2f}"
                                f"  (matched trade #{a.matched_trade_id})"
                            )

            return {"status": "ok", "lines": lines}
        except Exception as e:
            logger.error(f"Reconcile positions failed: {e}", exc_info=True)
            return {"status": "error", "lines": lines + [f"Error: {e}"]}
        finally:
            try:
                client.disconnect()
            except Exception:
                pass

    @app.get("/api/logs")
    def get_logs(
        lines: int = 100,
        token: None = Depends(verify_token),
    ):
        """Get recent daemon log lines."""
        log_file = os.path.join(os.getcwd(), "logs", "daemon.log")
        # Fall back to app.log if daemon.log doesn't exist
        if not os.path.exists(log_file):
            log_file = os.path.join(os.getcwd(), "logs", "app.log")
        if not os.path.exists(log_file):
            return {"lines": [], "file": None}

        try:
            with open(log_file, "rb") as f:
                # Read from end of file efficiently
                f.seek(0, 2)
                size = f.tell()
                # Read last ~64KB which should be plenty for 100 lines
                read_size = min(size, 65536)
                f.seek(max(0, size - read_size))
                content = f.read().decode("utf-8", errors="replace")

            all_lines = content.splitlines()
            tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
            return {"lines": tail, "file": log_file}
        except Exception as e:
            return {"lines": [f"Error reading log: {e}"], "file": log_file}

    @app.get("/api/costs")
    def get_costs(token: None = Depends(verify_token)):
        """Get Claude API cost summary."""
        from sqlalchemy import func as sa_func

        with get_db_session() as db:
            today = date.today()
            daily_total = (
                db.query(sa_func.sum(ClaudeApiCost.cost_usd))
                .filter(sa_func.date(ClaudeApiCost.timestamp) == today)
                .scalar()
            ) or 0.0

            monthly_total = (
                db.query(sa_func.sum(ClaudeApiCost.cost_usd))
                .filter(
                    sa_func.extract("year", ClaudeApiCost.timestamp) == today.year,
                    sa_func.extract("month", ClaudeApiCost.timestamp) == today.month,
                )
                .scalar()
            ) or 0.0

            total_calls = (
                db.query(sa_func.count(ClaudeApiCost.id))
                .filter(sa_func.date(ClaudeApiCost.timestamp) == today)
                .scalar()
            ) or 0

            all_time_total = (
                db.query(sa_func.sum(ClaudeApiCost.cost_usd))
                .scalar()
            ) or 0.0

            return {
                "daily_total_usd": round(daily_total, 4),
                "monthly_total_usd": round(monthly_total, 4),
                "all_time_total_usd": round(all_time_total, 4),
                "calls_today": total_calls,
                "date": str(today),
            }

    @app.get("/api/guardrails")
    def get_guardrails(token: None = Depends(verify_token)):
        """Get guardrail activity summary for the last 24 hours.

        Uses a 24-hour window instead of strict date boundary to avoid
        timezone mismatches between utc_now() stored in DB
        and the dashboard server's local time.
        """
        from datetime import datetime as dt, timedelta, timezone

        with get_db_session() as db:
            # Use 24-hour window to avoid UTC vs local date boundary issues
            cutoff = dt.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)

            decisions_recent = (
                db.query(DecisionAudit)
                .filter(DecisionAudit.timestamp >= cutoff)
                .order_by(DecisionAudit.timestamp.desc())
                .limit(100)
                .all()
            )

            total_decisions = len(decisions_recent)
            blocks = 0
            warnings = 0
            flagged_guards = {}

            # Last 10 findings
            recent_findings = []

            for d in decisions_recent:
                flags = d.guardrail_flags or []
                for flag in flags:
                    if not flag.get("passed", True):
                        guard_name = flag.get("guard_name", "unknown")
                        severity = flag.get("severity", "info")
                        if severity == "block":
                            blocks += 1
                        elif severity == "warning":
                            warnings += 1
                        flagged_guards[guard_name] = flagged_guards.get(guard_name, 0) + 1
                        recent_findings.append({
                            "decision_id": d.id,
                            "guard_name": guard_name,
                            "severity": severity,
                            "reason": flag.get("reason", ""),
                            "timestamp": str(d.timestamp),
                        })

            return {
                "date": str(dt.now(timezone.utc).date()),
                "total_decisions": total_decisions,
                "guardrail_blocks": blocks,
                "guardrail_warnings": warnings,
                "flagged_guards": flagged_guards,
                "recent_findings": recent_findings[-10:],
            }

    # Pre-execution states that can be unstaged
    _PRE_EXEC_STATES = ["STAGED", "VALIDATING", "READY", "ADJUSTING", "CONFIRMED"]

    @app.post("/api/unstage/{opportunity_id}")
    def unstage_opportunity(
        opportunity_id: int,
        token: None = Depends(verify_token),
    ):
        """Unstage a single trade candidate."""
        from src.agentic.event_bus import EventBus, EventType
        from src.data.opportunity_state import OpportunityState
        from src.execution.opportunity_lifecycle import OpportunityLifecycleManager

        with get_db_session() as db:
            opp = db.query(ScanOpportunity).get(opportunity_id)
            if not opp:
                raise HTTPException(status_code=404, detail="Opportunity not found")
            if opp.state not in _PRE_EXEC_STATES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot unstage opportunity in state {opp.state}",
                )

            lifecycle = OpportunityLifecycleManager(db)
            lifecycle.transition(
                opp.id,
                OpportunityState.EXPIRED,
                reason="Unstaged via dashboard",
                actor="user",
            )
            db.commit()

            EventBus(db).emit(EventType.SCHEDULED_CHECK)

            return {"status": "unstaged", "symbol": opp.symbol, "id": opp.id}

    @app.post("/api/unstage-all")
    def unstage_all(token: None = Depends(verify_token)):
        """Unstage all pre-execution trade candidates."""
        from src.agentic.event_bus import EventBus, EventType
        from src.data.opportunity_state import OpportunityState
        from src.execution.opportunity_lifecycle import OpportunityLifecycleManager

        with get_db_session() as db:
            candidates = (
                db.query(ScanOpportunity)
                .filter(
                    ScanOpportunity.state.in_(_PRE_EXEC_STATES),
                    ScanOpportunity.executed == False,  # noqa: E712
                )
                .all()
            )

            if not candidates:
                return {"status": "unstaged_all", "count": 0}

            lifecycle = OpportunityLifecycleManager(db)
            for opp in candidates:
                lifecycle.transition(
                    opp.id,
                    OpportunityState.EXPIRED,
                    reason="Unstaged all via dashboard",
                    actor="user",
                )
            db.commit()

            EventBus(db).emit(EventType.SCHEDULED_CHECK)

            return {"status": "unstaged_all", "count": len(candidates)}

    # ------------------------------------------------------------------
    # Auto-scan endpoints (Phase 4 automation)
    # ------------------------------------------------------------------
    @app.post("/api/auto-scan/trigger")
    def trigger_auto_scan(
        payload: dict = {},
        token: None = Depends(verify_token),
    ):
        """Manually trigger the auto-scan pipeline.

        Runs scan -> select -> stage (never auto-executes).
        Requires override_market_hours=true when market is closed.
        """
        from src.agentic.config import load_phase5_config
        from src.services.auto_select_pipeline import (
            run_auto_select_pipeline,
            run_scan_and_persist,
            stage_selected_candidates,
        )
        from src.services.market_calendar import MarketCalendar

        override = payload.get("override_market_hours", False)
        calendar = MarketCalendar()
        now_et = datetime.now(ZoneInfo("America/New_York"))

        if not calendar.is_market_open(now_et) and not override:
            return {
                "error": "Market is closed. Set override_market_hours=true to run with stale data.",
            }

        config = load_phase5_config()
        preset = config.auto_scan.scanner_preset

        with get_db_session() as db:
            try:
                scan_id, opportunities = run_scan_and_persist(preset=preset, db=db)
            except RuntimeError as e:
                return {"error": str(e)}

            if not opportunities:
                return {
                    "status": "scan_empty",
                    "scan_id": scan_id,
                    "symbols_found": 0,
                }

            result = run_auto_select_pipeline(
                scan_id=scan_id,
                db=db,
                override_market_hours=override,
            )

            if not result.success:
                return {"error": result.error, "scan_id": scan_id}

            # Always stage via manual trigger (never auto-execute)
            staged_count = 0
            if result.selected:
                staged_count = stage_selected_candidates(
                    selected=result.selected,
                    opp_id_map=result.opp_id_map,
                    config_snapshot=result.config_snapshot,
                    db=db,
                    earnings_map=result.earnings_map,
                )

            return {
                "status": "scan_complete",
                "scan_id": scan_id,
                "symbols_scanned": result.symbols_scanned,
                "selected": len(result.selected),
                "staged": staged_count,
                "elapsed_seconds": result.elapsed_seconds,
                "stale_data": result.stale_data,
            }

    @app.post("/api/force-scan")
    def force_scan(
        payload: dict = {},
        token: None = Depends(verify_token),
    ):
        """Force-scan override: run scan pipeline + emit SCHEDULED_CHECK.

        Unlike /api/auto-scan/trigger which only stages trades, this
        endpoint also emits a SCHEDULED_CHECK event so the daemon's
        Claude reasoning sees the newly staged candidates and can
        recommend EXECUTE_TRADES.

        Use case: Claude returned MONITOR_ONLY on an entry day (too
        conservative). The user clicks "Force Scan" to override —
        the pipeline finds and stages trades, then Claude re-evaluates.
        """
        from src.agentic.config import load_phase5_config
        from src.services.auto_select_pipeline import (
            run_auto_select_pipeline,
            run_scan_and_persist,
            stage_selected_candidates,
        )
        from src.services.market_calendar import MarketCalendar

        override = payload.get("override_market_hours", False)
        calendar = MarketCalendar()
        now_et = datetime.now(ZoneInfo("America/New_York"))

        if not calendar.is_market_open(now_et) and not override:
            return {
                "error": "Market is closed. Set override_market_hours=true to run with stale data.",
            }

        config = load_phase5_config()
        preset = config.auto_scan.scanner_preset

        with get_db_session() as db:
            # Step 1: Run scanner + auto-select pipeline
            try:
                scan_id, opportunities = run_scan_and_persist(preset=preset, db=db)
            except RuntimeError as e:
                return {"error": str(e)}

            if not opportunities:
                return {
                    "status": "scan_empty",
                    "scan_id": scan_id,
                    "symbols_scanned": 0,
                    "selected": 0,
                    "staged": 0,
                }

            result = run_auto_select_pipeline(
                scan_id=scan_id,
                db=db,
                override_market_hours=override,
            )

            if not result.success:
                return {"error": result.error, "scan_id": scan_id}

            # Step 2: Stage selected candidates
            staged_count = 0
            if result.selected:
                staged_count = stage_selected_candidates(
                    selected=result.selected,
                    opp_id_map=result.opp_id_map,
                    config_snapshot=result.config_snapshot,
                    db=db,
                    earnings_map=result.earnings_map,
                )

            # Step 3: Emit SCHEDULED_CHECK so daemon runs Claude reasoning
            # with newly staged candidates in context
            from src.agentic.event_bus import EventBus, EventType

            EventBus(db).emit(
                EventType.SCHEDULED_CHECK,
                payload={"source": "force_scan"},
            )

            return {
                "status": "scan_complete",
                "scan_id": scan_id,
                "symbols_scanned": result.symbols_scanned,
                "selected": len(result.selected),
                "staged": staged_count,
            }

    @app.get("/api/auto-scan/status")
    def auto_scan_status(token: None = Depends(verify_token)):
        """Return auto-scan config and last scan info."""
        from src.agentic.config import load_phase5_config

        config = load_phase5_config()
        scan_cfg = config.auto_scan

        # Last scan info
        last_scan = None
        staged_today = 0
        with get_db_session() as db:
            from src.data.models import ScanResult

            latest = (
                db.query(ScanResult)
                .filter(ScanResult.source == "ibkr_scanner")
                .order_by(ScanResult.scan_timestamp.desc())
                .first()
            )
            if latest:
                last_scan = {
                    "scan_id": latest.id,
                    "timestamp": str(latest.scan_timestamp),
                    "total_candidates": latest.total_candidates,
                }

            # Staged today count
            today = date.today()
            staged_today = (
                db.query(ScanOpportunity)
                .filter(
                    ScanOpportunity.state == "STAGED",
                    ScanOpportunity.staged_at >= datetime.combine(today, datetime.min.time()),
                )
                .count()
            )

        return {
            "config": {
                "enabled": scan_cfg.enabled,
                "delay_minutes": scan_cfg.delay_minutes,
                "scanner_preset": scan_cfg.scanner_preset,
                "auto_stage": scan_cfg.auto_stage,
                "require_ibkr": scan_cfg.require_ibkr,
            },
            "last_scan": last_scan,
            "staged_today": staged_today,
        }

    @app.get("/", response_class=HTMLResponse)
    def dashboard_page(token: str = ""):
        """HTML dashboard. Pass ?token=<auth_token> to authenticate API calls."""
        html = _DASHBOARD_HTML.replace("__AUTH_TOKEN__", token or "")
        return html

    @app.get("/decision/{decision_id}", response_class=HTMLResponse)
    def decision_detail_page(decision_id: int, token: str = ""):
        """HTML detail page for a single decision."""
        html = _DECISION_DETAIL_HTML.replace("__DECISION_ID__", str(decision_id))
        html = html.replace("__AUTH_TOKEN__", token or "")
        return html

    return app


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TAAD Dashboard</title>
<style>
  :root {
    --bg: #0f1923; --bg2: #172a3a; --bg3: #1e3a50;
    --border: #2a4a6b; --text: #c8d6e5; --text-dim: #6b8299;
    --accent: #00d4ff; --green: #00e676; --yellow: #ffd600;
    --red: #ff5252; --orange: #ff9100;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; background: var(--bg); color: var(--text); font-size: 13px; }

  /* Header */
  .header { background: var(--bg2); border-bottom: 1px solid var(--border); padding: 12px 24px; display: flex; align-items: center; justify-content: space-between; }
  .header h1 { font-size: 16px; color: var(--accent); font-weight: 600; }
  .header h1 span { color: var(--text-dim); font-weight: 400; }
  .refresh-badge { font-size: 11px; color: var(--text-dim); }
  .refresh-badge .dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--green); margin-right: 4px; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }

  /* Grid */
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding: 16px 24px; max-width: 1400px; }
  .grid .full { grid-column: 1 / -1; }

  /* Cards */
  .card { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
  .card-header { padding: 10px 16px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }
  .card-header h2 { font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-dim); font-weight: 600; }
  .card-body { padding: 16px; }
  .card-header .badge { font-size: 11px; padding: 2px 8px; border-radius: 10px; font-weight: 600; }

  /* Status bar */
  .status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 12px; }
  .stat { text-align: center; }
  .stat .value { font-size: 22px; font-weight: 700; color: var(--accent); }
  .stat .label { font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-dim); margin-top: 2px; }
  .stat.green .value { color: var(--green); }
  .stat.yellow .value { color: var(--yellow); }
  .stat.red .value { color: var(--red); }
  .stat.dim .value { color: var(--text-dim); }

  /* Tables */
  table { width: 100%; border-collapse: collapse; }
  th { font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-dim); text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); font-weight: 600; }
  td { padding: 8px 10px; border-bottom: 1px solid rgba(42, 74, 107, 0.4); font-size: 12px; }
  tr:hover td { background: rgba(0, 212, 255, 0.03); }

  /* Tags */
  .tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
  .tag-exec { background: rgba(0, 230, 118, 0.15); color: var(--green); }
  .tag-monitor { background: rgba(0, 212, 255, 0.12); color: var(--accent); }
  .tag-pending { background: rgba(255, 214, 0, 0.15); color: var(--yellow); }
  .tag-review { background: rgba(255, 145, 0, 0.15); color: var(--orange); }
  .tag-yes { background: rgba(0, 230, 118, 0.15); color: var(--green); }
  .tag-no { background: rgba(255, 82, 82, 0.15); color: var(--red); }
  .tag-put { background: rgba(0, 230, 118, 0.15); color: var(--green); }
  .tag-call { background: rgba(0, 212, 255, 0.12); color: var(--accent); }

  /* Buttons */
  .btn { border: none; padding: 6px 14px; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 12px; font-weight: 600; transition: all 0.15s; }
  .btn-approve { background: rgba(0, 230, 118, 0.15); color: var(--green); border: 1px solid rgba(0, 230, 118, 0.3); }
  .btn-approve:hover { background: rgba(0, 230, 118, 0.3); }
  .btn-reject { background: rgba(255, 82, 82, 0.12); color: var(--red); border: 1px solid rgba(255, 82, 82, 0.3); }
  .btn-reject:hover { background: rgba(255, 82, 82, 0.25); }
  .btn-control { background: var(--bg3); color: var(--text); border: 1px solid var(--border); }
  .btn-control:hover { border-color: var(--accent); color: var(--accent); }
  .controls { display: flex; gap: 8px; }

  /* Dropdown menu */
  .dropdown { position: relative; display: inline-block; }
  .dropdown-toggle { cursor: pointer; }
  .dropdown-toggle::after { content: ' \u25BE'; font-size: 10px; }
  .dropdown-menu {
    display: none; position: absolute; right: 0; top: calc(100% + 4px);
    background: var(--bg2); border: 1px solid var(--border); border-radius: 6px;
    min-width: 180px; z-index: 100; box-shadow: 0 8px 24px rgba(0,0,0,0.4);
    padding: 4px 0; white-space: nowrap;
  }
  .dropdown-menu.open { display: block; }
  .dropdown-menu .menu-item {
    display: block; width: 100%; padding: 8px 16px; border: none;
    background: transparent; color: var(--text); font-family: inherit;
    font-size: 12px; text-align: left; cursor: pointer; transition: background 0.1s;
  }
  .dropdown-menu .menu-item:hover { background: var(--bg3); color: var(--accent); }
  .dropdown-menu .menu-item.danger { color: var(--red); }
  .dropdown-menu .menu-item.danger:hover { background: rgba(255,82,82,0.1); }
  .dropdown-menu .menu-item.success { color: var(--green); }
  .dropdown-menu .menu-item.success:hover { background: rgba(0,230,118,0.1); }
  .dropdown-menu .menu-divider { height: 1px; background: var(--border); margin: 4px 0; }

  /* Output panel (for sync/reconcile results) */
  .output-overlay {
    display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.6);
    z-index: 200; justify-content: center; align-items: center;
  }
  .output-overlay.open { display: flex; }
  .output-panel {
    background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
    width: 90%; max-width: 700px; max-height: 80vh; display: flex; flex-direction: column;
    box-shadow: 0 12px 40px rgba(0,0,0,0.5);
  }
  .output-panel-header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 12px 16px; border-bottom: 1px solid var(--border);
  }
  .output-panel-header h3 { font-size: 13px; color: var(--accent); font-weight: 600; margin: 0; }
  .output-panel-close {
    background: none; border: none; color: var(--text-dim); font-size: 18px;
    cursor: pointer; padding: 0 4px; line-height: 1;
  }
  .output-panel-close:hover { color: var(--text); }
  .output-panel-body {
    padding: 16px; overflow-y: auto; flex: 1;
    font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; font-size: 12px;
    line-height: 1.6; white-space: pre-wrap; color: var(--text);
  }
  .output-panel-body.loading { color: var(--text-dim); font-style: italic; }
  .output-panel-body .out-ok { color: var(--green); }
  .output-panel-body .out-warn { color: var(--yellow); }
  .output-panel-body .out-err { color: var(--red); }

  /* Notification cards (blue theme — informational, no action needed) */
  .notif-item { background: var(--bg3); border: 1px solid rgba(0, 150, 255, 0.3); border-radius: 6px; padding: 14px; margin-bottom: 10px; }
  .notif-item:last-child { margin-bottom: 0; }
  .notif-title { font-weight: 700; color: #4da6ff; font-size: 14px; }
  .notif-meta { font-size: 11px; color: var(--text-dim); margin-top: 4px; }
  .notif-message { margin: 8px 0; line-height: 1.6; color: var(--text); font-size: 13px; }
  .notif-actions { margin-top: 10px; display: flex; gap: 8px; flex-wrap: wrap; }
  .notif-actions .btn-sm { padding: 4px 12px; font-size: 12px; border-radius: 4px; cursor: pointer; background: var(--bg); border: 1px solid var(--border); color: var(--text); transition: border-color 0.2s; }
  .notif-actions .btn-sm:hover { border-color: var(--blue); color: var(--blue); }

  /* Pending cards */
  .pending-item { background: var(--bg3); border: 1px solid rgba(255, 214, 0, 0.2); border-radius: 6px; padding: 14px; margin-bottom: 10px; }
  .pending-item:last-child { margin-bottom: 0; }
  .pending-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
  .pending-action { font-weight: 700; color: var(--yellow); font-size: 14px; }
  .pending-meta { font-size: 11px; color: var(--text-dim); }
  .pending-reasoning { margin: 8px 0; line-height: 1.6; color: var(--text); white-space: pre-wrap; word-break: break-word; font-size: 13px; }
  .pending-factors { margin: 6px 0; }
  .pending-factors span { font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-dim); display: block; margin-bottom: 4px; }
  .pending-factors li { font-size: 12px; color: var(--text); margin-left: 16px; margin-bottom: 2px; }
  .pending-buttons { display: flex; gap: 8px; margin-top: 10px; }

  /* Empty state */
  .empty { text-align: center; padding: 24px; color: var(--text-dim); font-style: italic; }

  /* Staged trades */
  .tag-staged { background: rgba(0, 230, 118, 0.12); color: var(--green); }
  .tag-validating { background: rgba(255, 214, 0, 0.15); color: var(--yellow); }
  .tag-ready { background: rgba(0, 212, 255, 0.15); color: var(--accent); }
  .tag-confirmed { background: rgba(0, 230, 118, 0.25); color: var(--green); }

  .staged-header { cursor: pointer; user-select: none; }
  .staged-header:hover h2 { color: var(--accent); }
  .staged-toggle { font-size: 10px; color: var(--text-dim); transition: transform 0.2s; display: inline-block; margin-right: 8px; }
  .staged-toggle.open { transform: rotate(90deg); }

  .staged-summary { display: flex; gap: 20px; flex-wrap: wrap; }
  .staged-summary .ss { font-size: 12px; color: var(--text-dim); }
  .staged-summary .ss b { color: var(--accent); font-weight: 700; }
  .staged-summary .ss.green b { color: var(--green); }

  .staged-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px; margin-top: 12px; }
  .staged-tile { background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 10px 14px; transition: border-color 0.15s; }
  .staged-tile:hover { border-color: var(--accent); }
  .staged-tile .st-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
  .staged-tile .st-symbol { font-size: 15px; font-weight: 700; color: var(--accent); }
  .staged-tile .st-row { display: flex; gap: 14px; flex-wrap: wrap; font-size: 11px; color: var(--text-dim); line-height: 1.7; }
  .staged-tile .st-row span b { color: var(--text); font-weight: 600; }

  /* Confidence bar */
  .conf-bar { display: inline-block; width: 50px; height: 6px; background: var(--bg3); border: 1px solid var(--border); border-radius: 3px; vertical-align: middle; margin-right: 6px; overflow: hidden; }
  .conf-bar .fill { display: block; height: 100%; border-radius: 2px; }

  /* Toast */
  .toast { position: fixed; bottom: 20px; right: 20px; background: var(--bg3); border: 1px solid var(--green); color: var(--green); padding: 12px 20px; border-radius: 6px; font-size: 13px; opacity: 0; transition: opacity 0.3s; pointer-events: none; z-index: 100; }
  .toast.show { opacity: 1; }
</style>
</head>
<body>

<div class="header">
  <h1>TAAD <span>The Autonomous Agentic Trading Daemon</span></h1>
  <div style="display:flex;align-items:center;gap:12px;">
    <a href="/scanner" class="btn btn-control" style="text-decoration:none;">Scanner</a>
    <a href="/scanner-settings" class="btn btn-control" style="text-decoration:none;">Scan Config</a>
    <a href="/config" class="btn btn-control" style="text-decoration:none;">Settings</a>
    <a href="/guardrails" class="btn btn-control" style="text-decoration:none;">Guardrails</a>
    <a href="/prompts" class="btn btn-control" style="text-decoration:none;">Prompts</a>
    <div class="dropdown">
      <button class="btn btn-control dropdown-toggle" onclick="toggleMenu()">Actions</button>
      <div class="dropdown-menu" id="actions-menu">
        <button class="menu-item success" onclick="apiCall('/api/start');closeMenu()" id="menu-start">Start Daemon</button>
        <button class="menu-item danger" onclick="apiCall('/api/stop');closeMenu()" id="menu-stop">Stop Daemon</button>
        <button class="menu-item" onclick="apiCall('/api/pause');closeMenu()" id="menu-pause">Pause</button>
        <button class="menu-item" onclick="apiCall('/api/resume');closeMenu()" id="menu-resume">Resume</button>
        <div class="menu-divider"></div>
        <button class="menu-item" onclick="triggerAutoScan();closeMenu()" id="menu-auto-scan">Auto-Scan</button>
        <button class="menu-item" onclick="forceScan();closeMenu()" id="menu-force-scan">Force Scan</button>
        <div class="menu-divider"></div>
        <button class="menu-item" onclick="runSyncOrders();closeMenu()" id="menu-sync">Sync Orders</button>
        <button class="menu-item" onclick="runReconcile();closeMenu()" id="menu-reconcile">Reconcile Positions</button>
        <div class="menu-divider"></div>
        <button class="menu-item" onclick="restartDaemon();closeMenu()" id="menu-restart-daemon">Restart Daemon</button>
        <button class="menu-item" onclick="restartDashboard();closeMenu()" id="menu-restart-dashboard">Restart Dashboard</button>
      </div>
    </div>
    <div class="refresh-badge" style="text-align:right;"><div><span class="dot" id="status-dot"></span><span id="status-label">Checking...</span> <span id="et-time"></span></div><div id="market-countdown" style="font-size:10px;"></div></div>
  </div>
</div>

<!-- Output panel overlay (sync/reconcile results) -->
<div class="output-overlay" id="output-overlay" onclick="if(event.target===this)closeOutput()">
  <div class="output-panel">
    <div class="output-panel-header">
      <h3 id="output-title">Output</h3>
      <button class="output-panel-close" onclick="closeOutput()">&times;</button>
    </div>
    <div class="output-panel-body" id="output-body"></div>
  </div>
</div>

<div class="grid">
  <!-- Status -->
  <div class="card full">
    <div class="card-body">
      <div class="status-grid" id="status-grid">
        <div class="stat"><div class="value">--</div><div class="label">Status</div></div>
      </div>
      <div id="daemon-plan" style="display:none;margin-top:12px;padding:10px 14px;background:var(--bg);border:1px solid var(--border);border-radius:6px;font-size:12px;line-height:1.7;color:var(--text-dim);"></div>
    </div>
  </div>

  <!-- Notifications (informational, self-updating — no approve/reject) -->
  <div class="card full" id="notif-card" style="display:none;">
    <div class="card-header">
      <h2>Notifications</h2>
      <span class="badge" style="background:rgba(0,150,255,0.15);color:#4da6ff;" id="notif-count">0</span>
    </div>
    <div class="card-body" id="notif-body"></div>
  </div>

  <!-- Pending Approvals -->
  <div class="card full" id="pending-card" style="display:none;">
    <div class="card-header">
      <h2>Pending Approvals</h2>
      <span class="badge" style="background:rgba(255,214,0,0.15);color:var(--yellow);" id="pending-count">0</span>
    </div>
    <div class="card-body" id="pending-body"></div>
  </div>

  <!-- Rejected Today (overrideable) -->
  <div class="card full" id="rejected-card" style="display:none;">
    <div class="card-header">
      <h2>Rejected Today</h2>
      <span class="badge" style="background:rgba(255,82,82,0.15);color:var(--red);" id="rejected-count">0</span>
    </div>
    <div class="card-body" id="rejected-body" style="font-size:12px;color:var(--text-dim);">
      <p style="margin:0 0 8px;">These positions won't be re-evaluated until the next trading day. Click Override to approve.</p>
    </div>
  </div>

  <!-- Staged Trades -->
  <div class="card full" id="staged-card" style="display:none;">
    <div class="card-header staged-header" onclick="toggleStaged()">
      <div style="display:flex;align-items:center;">
        <span class="staged-toggle" id="staged-chevron">&#9654;</span>
        <h2>Tonight's Lineup</h2>
      </div>
      <div style="display:flex;align-items:center;gap:12px;">
        <div class="staged-summary" id="staged-summary"></div>
        <button class="btn btn-reject" onclick="event.stopPropagation();unstageAll()" id="btn-unstage-all" style="padding:4px 10px;font-size:11px;">Unstage All</button>
        <span class="badge" style="background:rgba(0,230,118,0.15);color:var(--green);" id="staged-count">0</span>
      </div>
    </div>
    <div class="card-body" id="staged-body" style="display:none;"></div>
  </div>

  <!-- Positions -->
  <div class="card">
    <div class="card-header">
      <h2>Open Positions</h2>
      <span class="badge" style="background:rgba(0,212,255,0.12);color:var(--accent);" id="pos-count">0</span>
    </div>
    <div class="card-body" id="positions-body"></div>
  </div>

  <!-- Portfolio Greeks -->
  <div class="card full">
    <div class="card-header">
      <h2>Portfolio Greeks</h2>
      <div style="display:flex;align-items:center;gap:8px;">
        <span id="greeks-age" style="font-size:11px;color:var(--text-dim);"></span>
        <button class="btn btn-control" onclick="refreshGreeks()" id="btn-refresh-greeks"
                style="padding:3px 10px;font-size:11px;">Refresh from IBKR</button>
      </div>
    </div>
    <div class="card-body">
      <div class="status-grid" id="greeks-summary" style="grid-template-columns:repeat(4,1fr);">
        <div class="stat"><div class="value">--</div><div class="label">Delta</div></div>
        <div class="stat"><div class="value">--</div><div class="label">Theta/day</div></div>
        <div class="stat"><div class="value">--</div><div class="label">Gamma</div></div>
        <div class="stat"><div class="value">--</div><div class="label">Vega</div></div>
      </div>
      <div id="greeks-positions" style="margin-top:12px;"></div>
    </div>
  </div>

  <!-- Costs -->
  <div class="card">
    <div class="card-header"><h2>Claude API Costs</h2></div>
    <div class="card-body" id="costs-body"></div>
  </div>

  <!-- Guardrails -->
  <div class="card">
    <div class="card-header"><h2>Guardrails</h2></div>
    <div class="card-body" id="guardrails-body"></div>
  </div>

  <!-- Decisions -->
  <div class="card full">
    <div class="card-header">
      <h2>Recent Decisions</h2>
      <div style="display:flex;align-items:center;gap:10px;">
        <button class="btn btn-control" id="tz-toggle" onclick="toggleTimezone()" style="padding:2px 8px;font-size:10px;min-width:50px;">ET</button>
        <span class="badge" style="background:rgba(0,212,255,0.12);color:var(--accent);" id="dec-count">0</span>
      </div>
    </div>
    <div class="card-body" id="decisions-body" style="max-height:600px;overflow-y:auto;"></div>
  </div>

  <!-- Logs -->
  <div class="card full">
    <div class="card-header">
      <h2>Daemon Log</h2>
      <div style="display:flex;gap:8px;align-items:center;">
        <label style="font-size:11px;color:var(--text-dim);cursor:pointer;"><input type="checkbox" id="log-auto" checked style="margin-right:4px;">Auto-scroll</label>
        <button class="btn btn-control" onclick="fetchLogs()" style="padding:4px 10px;font-size:11px;">Refresh</button>
      </div>
    </div>
    <div class="card-body" style="padding:0;">
      <div id="log-body" style="height:350px;overflow-y:auto;padding:12px 16px;font-size:11px;line-height:1.6;white-space:pre-wrap;word-break:break-all;background:var(--bg);"></div>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
// --- Auth token handling ---
// Token is injected by server; falls back to sessionStorage for sub-pages
const _injected = '__AUTH_TOKEN__';
const _authToken = (_injected && !_injected.includes('AUTH_TOKEN')) ? _injected : '';
if (_authToken) sessionStorage.setItem('taad_token', _authToken);
const _storedToken = _authToken || sessionStorage.getItem('taad_token') || '';

// Override fetch to inject Authorization header on all /api calls
const _origFetch = window.fetch;
window.fetch = function(url, opts) {
  if (_storedToken && typeof url === 'string' && url.startsWith('/api')) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    opts.headers['Authorization'] = 'Bearer ' + _storedToken;
  }
  return _origFetch.call(this, url, opts);
};

// Append ?token= to nav links so auth persists across pages
if (_storedToken) {
  document.querySelectorAll('a[href^="/"]').forEach(a => {
    const u = new URL(a.href);
    u.searchParams.set('token', _storedToken);
    a.href = u.toString();
  });
}

function actionTag(action) {
  const map = {
    'EXECUTE_TRADES': 'tag-exec', 'STAGE_CANDIDATES': 'tag-exec',
    'MONITOR_ONLY': 'tag-monitor', 'CLOSE_POSITION': 'tag-exec',
    'CLOSE_ALL_POSITIONS': 'tag-exec',
    'REQUEST_HUMAN_REVIEW': 'tag-review', 'GUARDRAIL_BLOCKED': 'tag-no',
  };
  return `<span class="tag ${map[action] || 'tag-pending'}">${action}</span>`;
}

function stateTag(state) {
  const map = {
    'STAGED': 'tag-staged', 'VALIDATING': 'tag-validating',
    'READY': 'tag-ready', 'CONFIRMED': 'tag-confirmed',
  };
  return `<span class="tag ${map[state] || 'tag-pending'}">${state}</span>`;
}

function trendIcon(trend) {
  if (!trend) return '';
  const m = { 'uptrend': ['var(--green)','\u2191'], 'downtrend': ['var(--red)','\u2193'], 'sideways': ['var(--yellow)','\u2194'] };
  const [c, a] = m[trend] || ['var(--text-dim)', '?'];
  return `<span style="color:${c};font-weight:700" title="${trend}">${a}</span>`;
}

let _stagedOpen = false;
function toggleStaged() {
  _stagedOpen = !_stagedOpen;
  document.getElementById('staged-body').style.display = _stagedOpen ? '' : 'none';
  const chev = document.getElementById('staged-chevron');
  chev.classList.toggle('open', _stagedOpen);
}

function confBar(conf) {
  if (conf == null) return '--';
  const pct = Math.round(conf * 100);
  const color = pct >= 80 ? 'var(--green)' : pct >= 60 ? 'var(--yellow)' : 'var(--red)';
  return `<span class="conf-bar"><span class="fill" style="width:${pct}%;background:${color}"></span></span>${pct}%`;
}

function timeAgo(ts) {
  if (!ts || ts === 'None') return '--';
  let iso = ts.replace(' ', 'T');
  if (!iso.endsWith('Z') && !iso.includes('+')) iso += 'Z';
  const d = new Date(iso);
  const s = Math.floor((Date.now() - d) / 1000);
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s/60) + 'm ago';
  if (s < 86400) return Math.floor(s/3600) + 'h ago';
  return Math.floor(s/86400) + 'd ago';
}

// Timezone state: 'ET' (US Eastern, UTC-5/UTC-4 DST) or 'AEDT' (Australian Eastern Daylight, UTC+11)
let _tz = localStorage.getItem('taad_tz') || 'ET';
function initTzButton() {
  const btn = document.getElementById('tz-toggle');
  if (btn) btn.textContent = _tz;
}
function toggleTimezone() {
  _tz = _tz === 'ET' ? 'AEDT' : 'ET';
  localStorage.setItem('taad_tz', _tz);
  const btn = document.getElementById('tz-toggle');
  if (btn) btn.textContent = _tz;
  fetchData();
}

function fmtTime(ts) {
  if (!ts || ts === 'None') return '--';
  let iso = ts.replace(' ', 'T');
  if (!iso.endsWith('Z') && !iso.includes('+')) iso += 'Z';
  const d = new Date(iso);
  if (isNaN(d)) return '--';
  const tz = _tz === 'AEDT' ? 'Australia/Sydney' : 'America/New_York';
  return d.toLocaleTimeString('en-US', { timeZone: tz, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2500);
}

async function apiCall(url) {
  try {
    const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
    const d = await r.json();
    showToast(d.status ? d.status.charAt(0).toUpperCase() + d.status.slice(1) : 'Done');
    fetchData();
  } catch(e) { showToast('Error: ' + e.message); }
}

async function chooseNotifAction(id, actionKey) {
  try {
    const r = await fetch('/api/notifications/' + id + '/action', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({action_key: actionKey}),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Failed');
    showToast('Action recorded: ' + actionKey);
    fetchData();
  } catch(e) { showToast('Error: ' + e.message); }
}

async function approveDecision(id) {
  await apiCall('/api/approve/' + id);
}

async function rejectDecision(id) {
  await apiCall('/api/reject/' + id);
}

async function overrideRejection(id) {
  if (!confirm('Override rejection and APPROVE this decision?')) return;
  await apiCall('/api/override-rejection/' + id);
}

async function unstage(id) {
  if (!confirm('Unstage this trade?')) return;
  await apiCall('/api/unstage/' + id);
}

async function unstageAll() {
  if (!confirm('Unstage ALL staged trades?')) return;
  await apiCall('/api/unstage-all');
}

// --- Dropdown menu ---
function toggleMenu() {
  document.getElementById('actions-menu').classList.toggle('open');
}
function closeMenu() {
  document.getElementById('actions-menu').classList.remove('open');
}
// Close dropdown when clicking outside
document.addEventListener('click', function(e) {
  if (!e.target.closest('.dropdown')) closeMenu();
});

async function triggerAutoScan() {
  const override = confirm('Run auto-scan now?\\n\\nIf market is closed, stale data will be used.');
  if (!override) return;
  showToast('Scanning...');
  try {
    const r = await fetch('/api/auto-scan/trigger', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({override_market_hours: true})
    });
    const d = await r.json();
    if (d.error) {
      showToast('Scan failed: ' + d.error);
    } else {
      showToast('Scan complete: ' + (d.staged || 0) + ' staged');
      fetchData();
    }
  } catch(e) {
    showToast('Error: ' + e.message);
  }
}

async function forceScan() {
  if (!confirm('Override MONITOR_ONLY and scan for new trades?\\n\\nThis will run the full pipeline (scan \\u2192 select \\u2192 stage) and trigger Claude reasoning on the results.')) return;
  showToast('Force scanning...');
  try {
    const r = await fetch('/api/force-scan', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({override_market_hours: !_isMarketOpen()})
    });
    const d = await r.json();
    if (d.error) {
      showToast('Force scan failed: ' + d.error);
    } else {
      showToast('Force scan: ' + (d.staged || 0) + ' staged, Claude will re-evaluate');
      fetchData();
    }
  } catch(e) {
    showToast('Error: ' + e.message);
  }
}

async function restartDaemon() {
  if (!confirm('Restart the daemon?\\n\\nThis will stop the current daemon process and start a new one.')) return;
  showToast('Restarting daemon...');
  try {
    const r = await fetch('/api/restart-daemon', { method: 'POST' });
    const d = await r.json();
    if (d.status === 'restarted') {
      showToast('Daemon restarted (pid=' + d.pid + ')');
      setTimeout(fetchData, 2000);
    } else {
      showToast('Restart failed: ' + JSON.stringify(d));
    }
  } catch(e) {
    showToast('Error: ' + e.message);
  }
}

async function restartDashboard() {
  if (!confirm('Restart the dashboard?\\n\\nThe page will briefly disconnect and then reconnect.')) return;
  showToast('Restarting dashboard...');
  try {
    await fetch('/api/restart-dashboard', { method: 'POST' });
  } catch(e) { /* expected — server is restarting */ }
  // Poll until dashboard comes back
  setTimeout(function poll() {
    fetch('/api/status').then(() => location.reload()).catch(() => setTimeout(poll, 1000));
  }, 2000);
}

// --- Output panel (sync/reconcile) ---
function openOutput(title) {
  document.getElementById('output-title').textContent = title;
  const body = document.getElementById('output-body');
  body.textContent = 'Running...';
  body.className = 'output-panel-body loading';
  document.getElementById('output-overlay').classList.add('open');
}
function closeOutput() {
  document.getElementById('output-overlay').classList.remove('open');
}
function showOutput(lines, isError) {
  const body = document.getElementById('output-body');
  body.className = 'output-panel-body';
  body.innerHTML = lines.map(line => {
    if (/^-{4,}/.test(line)) return '<span style="color:var(--border)">' + esc(line) + '</span>';
    if (/error/i.test(line)) return '<span class="out-err">' + esc(line) + '</span>';
    if (/discrepanc|mismatch|orphan|ghost|assignment|missing/i.test(line))
      return '<span class="out-warn">' + esc(line) + '</span>';
    if (/in sync|no discrep|reconciled|complete|imported/i.test(line))
      return '<span class="out-ok">' + esc(line) + '</span>';
    return esc(line);
  }).join('\\n');
  if (isError) body.innerHTML = '<span class="out-err">' + body.innerHTML + '</span>';
}
function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

async function runSyncOrders() {
  openOutput('Sync Orders');
  try {
    const r = await fetch('/api/sync-orders', { method: 'POST' });
    const d = await r.json();
    showOutput(d.lines || ['No output'], d.status === 'error');
  } catch(e) {
    showOutput(['Request failed: ' + e.message], true);
  }
}

async function runReconcile() {
  openOutput('Reconcile Positions');
  try {
    const r = await fetch('/api/reconcile-positions', { method: 'POST' });
    const d = await r.json();
    showOutput(d.lines || ['No output'], d.status === 'error');
  } catch(e) {
    showOutput(['Request failed: ' + e.message], true);
  }
}

// Close output panel with Escape
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') closeOutput();
});

async function fetchData() {
  try {
    // Status
    const status = await (await fetch('/api/status')).json();
    const alive = status.process_alive;
    const sColor = {running:'green',paused:'yellow',stopped:'red',error:'red'}[status.status] || '';
    const uptime = status.uptime_seconds ? (status.uptime_seconds >= 3600
      ? Math.floor(status.uptime_seconds/3600)+'h '+Math.floor((status.uptime_seconds%3600)/60)+'m'
      : Math.floor(status.uptime_seconds/60)+'m') : '--';

    // Update header indicator
    const dot = document.getElementById('status-dot');
    const label = document.getElementById('status-label');
    dot.style.background = alive ? 'var(--green)' : 'var(--red)';
    dot.style.animation = alive ? 'pulse 2s infinite' : 'none';
    if (alive) {
      label.textContent = 'Daemon Live';
    } else {
      label.textContent = status.stop_requested ? 'Stopped (User)' : 'Stopped (Crash)';
    }

    // Show/hide buttons based on state
    // Update menu item visibility based on daemon state
    document.getElementById('menu-start').style.display = alive ? 'none' : '';
    document.getElementById('menu-stop').style.display = alive ? '' : 'none';
    document.getElementById('menu-pause').style.display = alive && status.status === 'running' ? '' : 'none';
    document.getElementById('menu-resume').style.display = alive && status.status === 'paused' ? '' : 'none';
    document.getElementById('menu-force-scan').style.display = alive && status.status === 'running' ? '' : 'none';
    document.getElementById('menu-restart-daemon').style.display = alive ? '' : 'none';

    // Watchdog indicator
    const wd = status.watchdog || {};
    let wdValue, wdColor;
    if (!wd.pid && !wd.daemon_assessment) {
      wdValue = 'OFF'; wdColor = 'dim';
    } else if (!wd.active) {
      wdValue = 'DOWN'; wdColor = 'red';
    } else if (wd.daemon_assessment === 'healthy') {
      wdValue = 'ACTIVE'; wdColor = 'green';
    } else if (wd.daemon_assessment && wd.daemon_assessment.includes('warn')) {
      wdValue = 'WARN'; wdColor = 'yellow';
    } else {
      wdValue = (wd.daemon_assessment || 'ACTIVE').toUpperCase(); wdColor = wd.active ? 'green' : 'red';
    }

    const ibkrColor = status.ibkr_connected ? 'green' : 'red';
    const ibkrValue = status.ibkr_connected ? 'ON' : 'OFF';

    document.getElementById('status-grid').innerHTML = `
      <div class="stat ${sColor}"><div class="value">${(status.status||'--').toUpperCase()}</div><div class="label">Daemon</div></div>
      <div class="stat ${ibkrColor}"><div class="value">${ibkrValue}</div><div class="label">IBKR</div></div>
      <div class="stat" id="focus-stat"><div class="value" style="font-size:14px;">--</div><div class="label">Focus</div></div>
      <div class="stat"><div class="value">L${status.autonomy_level||'?'}</div><div class="label">Autonomy</div></div>
      <div class="stat"><div class="value">${status.events_processed_today||0}</div><div class="label">Events Today</div></div>
      <div class="stat"><div class="value">${status.decisions_made_today||0}</div><div class="label">Decisions</div></div>
      <div class="stat ${status.errors_today > 0 ? 'red' : ''}"><div class="value">${status.errors_today||0}</div><div class="label">Errors</div></div>
      <div class="stat"><div class="value">${uptime}</div><div class="label">Uptime</div></div>
      <div class="stat"><div class="value">${timeAgo(status.last_heartbeat)}</div><div class="label">Heartbeat</div></div>
      <div class="stat ${wdColor}"><div class="value">${wdValue}</div><div class="label">Watchdog</div></div>
    `;

    // Notifications (informational, self-updating)
    try {
      const notifs = await (await fetch('/api/notifications')).json();
      const nc = document.getElementById('notif-card');
      document.getElementById('notif-count').textContent = notifs.length;
      if (notifs.length > 0) {
        nc.style.display = '';
        document.getElementById('notif-body').innerHTML = notifs.map(n => {
          let actionsHtml = '';
          if (n.action_choices && n.action_choices.length > 0 && !n.chosen_action) {
            actionsHtml = '<div class="notif-actions">' +
              n.action_choices.map(a =>
                `<button class="btn btn-sm" onclick="chooseNotifAction(${n.id},'${a.key}')" title="${esc(a.description)}">${esc(a.label)}</button>`
              ).join(' ') + '</div>';
          } else if (n.chosen_action) {
            const chosenLabel = (n.action_choices || []).find(a => a.key === n.chosen_action);
            actionsHtml = `<div class="notif-actions"><span class="tag tag-yes">Action: ${esc(chosenLabel ? chosenLabel.label : n.chosen_action)}</span></div>`;
          }
          return `<div class="notif-item">
            <div class="notif-title">${esc(n.title)}</div>
            <div class="notif-meta">Since ${fmtTime(n.first_seen)} | Updated ${fmtTime(n.updated_at)} | ${n.occurrence_count} occurrence${n.occurrence_count !== 1 ? 's' : ''}</div>
            <div class="notif-message">${esc(n.message)}</div>
            ${actionsHtml}
          </div>`;
        }).join('');
      } else {
        nc.style.display = 'none';
      }
    } catch(e) { /* notifications endpoint may not exist on older daemons */ }

    // Pending approvals
    const queue = await (await fetch('/api/queue')).json();
    const pc = document.getElementById('pending-card');
    document.getElementById('pending-count').textContent = queue.length;
    if (queue.length > 0) {
      pc.style.display = '';
      document.getElementById('pending-body').innerHTML = queue.map(q => {
        const grBlocks = (q.guardrail_flags || []).filter(f => !f.passed && f.severity === 'block');
        const isGuardrail = grBlocks.length > 0;
        const execResult = q.execution_result || {};
        const grLayer = execResult.guardrail_layer || '';
        const isContextBlock = q.action === 'GUARDRAIL_BLOCKED' || grLayer === 'context';
        const approveNote = isContextBlock
          ? 'Approving will re-run with fresh data + Claude'
          : isGuardrail
            ? 'Approving will execute ' + esc(q.action) + ' directly, bypassing the guardrail'
            : '';

        return `<div class="pending-item" style="${isGuardrail ? 'border-color:rgba(255,82,82,0.4);' : ''}">
          <div class="pending-header">
            <div>
              ${isGuardrail ? '<span class="tag tag-no" style="margin-right:6px;">GUARDRAIL</span>' : ''}
              <span class="pending-action">${q.action}${q.target_symbol ? ' <span style="color:var(--blue);font-weight:600;">' + esc(q.target_symbol) + '</span>' : ''}</span>
              <span class="pending-meta">#${q.id} | ${q.event_type} | ${fmtTime(q.timestamp)}</span>
            </div>
            <div>${confBar(q.confidence)}</div>
          </div>
          ${isGuardrail ? '<div style="background:rgba(255,82,82,0.08);border:1px solid rgba(255,82,82,0.2);border-radius:4px;padding:8px 12px;margin:8px 0;font-size:12px;">' +
            grBlocks.map(f => '<div style="color:var(--red);margin-bottom:2px;"><b>' + esc(f.guard_name) + ':</b> ' + esc(f.reason) + '</div>').join('') +
            (grLayer ? '<div style="color:var(--text-dim);margin-top:4px;font-size:11px;">Layer: ' + esc(grLayer) + '</div>' : '') +
            '</div>' : ''}
          <div class="pending-reasoning">${fmtReasoning(q.reasoning)}</div>
          ${q.key_factors && q.key_factors.length ? '<div class="pending-factors"><span>Key Factors</span><ul>' + q.key_factors.map(f=>'<li>'+esc(f)+'</li>').join('') + '</ul></div>' : ''}
          ${q.risks_considered && q.risks_considered.length ? '<div class="pending-factors"><span>Risks Considered</span><ul>' + q.risks_considered.map(r=>'<li>'+esc(r)+'</li>').join('') + '</ul></div>' : ''}
          ${approveNote ? '<div style="font-size:11px;color:var(--text-dim);margin-top:6px;font-style:italic;">' + esc(approveNote) + '</div>' : ''}
          <div class="pending-buttons">
            <button class="btn btn-approve" onclick="approveDecision(${q.id})">Approve</button>
            <button class="btn btn-reject" onclick="rejectDecision(${q.id})">Reject</button>
          </div>
        </div>`;
      }).join('');
    } else {
      pc.style.display = 'none';
    }

    // Rejected today (overrideable)
    const rejectedRaw = await (await fetch('/api/rejected-today')).json();
    const rejected = rejectedRaw.filter(r => !dismissedRejections.has(r.id));
    const rc = document.getElementById('rejected-card');
    document.getElementById('rejected-count').textContent = rejected.length;
    if (rejected.length > 0) {
      rc.style.display = '';
      document.getElementById('rejected-body').innerHTML =
        '<p style="margin:0 0 8px;font-size:12px;color:var(--text-dim);">These positions won\\'t be re-evaluated until the next trading day. Click Override to approve.</p>' +
        rejected.map(r => `<div class="pending-item" style="border-color:rgba(255,82,82,0.3);opacity:0.85;">
          <div class="pending-header">
            <div>
              <span class="tag tag-no" style="margin-right:6px;">REJECTED</span>
              <span class="pending-action">${r.action}</span>
              <span class="pending-meta">#${r.id} | ${r.event_type} | ${fmtTime(r.timestamp)}</span>
            </div>
            <div>${confBar(r.confidence)}</div>
          </div>
          <div class="pending-reasoning">${fmtReasoning(r.reasoning)}</div>
          <div style="font-size:11px;color:var(--text-dim);margin-top:4px;">Rejected at ${fmtTime(r.rejected_at)}</div>
          <div class="pending-buttons">
            <button class="btn btn-approve" onclick="overrideRejection(${r.id})">Override → Approve</button>
            <button class="btn btn-control" onclick="dismissRejection(${r.id})" style="padding:2px 8px;font-size:10px;">Clear</button>
          </div>
        </div>`).join('');
    } else {
      rc.style.display = 'none';
    }

    // Staged trades
    const staged = await (await fetch('/api/staged')).json();
    const sc = document.getElementById('staged-card');
    document.getElementById('staged-count').textContent = staged.summary.count;
    if (staged.summary.count > 0) {
      sc.style.display = '';
      document.getElementById('staged-summary').innerHTML =
        `<span class="ss"><b>${staged.summary.count}</b> trade${staged.summary.count > 1 ? 's' : ''}</span>` +
        `<span class="ss"><b>$${staged.summary.total_margin.toLocaleString()}</b> margin</span>` +
        `<span class="ss green"><b>$${staged.summary.total_premium.toLocaleString()}</b> premium</span>`;
      document.getElementById('staged-body').innerHTML = `
        <div class="staged-grid">
          ${staged.trades.map(s => {
            const premium = s.limit_price != null && s.contracts ? '$' + (s.limit_price * s.contracts * 100).toFixed(0) : '--';
            return `<div class="staged-tile">
              <div class="st-top">
                <div><span class="st-symbol">${s.symbol}</span>${s.stock_price ? ' <span style="color:var(--text);font-size:13px;font-weight:400">$' + Number(s.stock_price).toFixed(2) + '</span>' : ''} ${trendIcon(s.trend)}</div>
                <div>${stateTag(s.state)}</div>
              </div>
              <div class="st-row">
                <span><b>$${s.strike}</b> strike</span>
                <span><b>${s.expiration}</b></span>
                <span><b>${s.contracts ?? '--'}</b> x <b>$${s.limit_price != null ? Number(s.limit_price).toFixed(2) : '--'}</b></span>
              </div>
              <div class="st-row">
                <span>\u0394 <b>${s.delta != null ? Number(s.delta).toFixed(2) : '--'}</b></span>
                <span>OTM <b>${s.otm_pct != null ? Number(s.otm_pct).toFixed(1) + '%' : '--'}</b></span>
                <span>IV <b>${s.iv != null ? (Number(s.iv) * 100).toFixed(0) + '%' : '--'}</b></span>
                <span>margin <b>$${s.margin != null ? Number(s.margin).toLocaleString() : '--'}</b></span>
              </div>
              <div class="st-actions" style="margin-top:6px;text-align:right;">
                <button class="btn btn-reject" onclick="unstage(${s.id})" style="padding:2px 8px;font-size:10px;">Unstage</button>
              </div>
            </div>`}).join('')}
        </div>`;
    } else {
      sc.style.display = 'none';
    }

    // Positions
    const positions = await (await fetch('/api/positions')).json();
    document.getElementById('pos-count').textContent = positions.length;
    document.getElementById('positions-body').innerHTML = positions.length ? `
      <table>
        <tr><th>Symbol</th><th>Strike</th><th>Type</th><th>Exp</th><th>Premium</th><th>Qty</th><th>DTE</th></tr>
        ${positions.map(p => `<tr>
          <td style="font-weight:600;color:var(--accent)">${p.symbol}</td>
          <td>$${p.strike}</td>
          <td>${optTypeTag(p.option_type)}</td>
          <td>${p.expiration}</td>
          <td>$${Number(p.entry_premium).toFixed(2)}</td>
          <td>${p.contracts}</td>
          <td>${p.dte ?? '--'}</td>
        </tr>`).join('')}
      </table>` : '<div class="empty">No open positions</div>';

    // Portfolio Greeks
    try {
      const greeks = await (await fetch('/api/portfolio-greeks')).json();
      const pg = greeks.portfolio;
      if (pg && pg.position_count > 0) {
        const dColor = pg.total_delta < 0 ? 'color:var(--red)' : pg.total_delta > 0 ? 'color:var(--green)' : '';
        const tColor = pg.total_theta > 0 ? 'color:var(--green)' : pg.total_theta < 0 ? 'color:var(--red)' : '';
        document.getElementById('greeks-summary').innerHTML = `
          <div class="stat"><div class="value" style="${dColor}">${pg.total_delta?.toFixed(2) ?? '--'}</div><div class="label">Delta</div></div>
          <div class="stat"><div class="value" style="${tColor}">${pg.total_theta?.toFixed(2) ?? '--'}</div><div class="label">Theta/day</div></div>
          <div class="stat"><div class="value">${pg.total_gamma?.toFixed(4) ?? '--'}</div><div class="label">Gamma</div></div>
          <div class="stat"><div class="value">${pg.total_vega?.toFixed(2) ?? '--'}</div><div class="label">Vega</div></div>
        `;
        document.getElementById('greeks-positions').innerHTML = `<table>
          <tr><th>Symbol</th><th>Strike</th><th>Type</th><th>Qty</th><th>\u0394</th><th>\u0398</th><th>\u0393</th><th>V</th><th>IV</th><th>P&L</th></tr>
          ${greeks.positions.map(p => `<tr>
            <td style="font-weight:600;color:var(--accent)">${p.symbol}</td>
            <td>$${p.strike}</td>
            <td>${optTypeTag(p.option_type)}</td>
            <td>${p.contracts}</td>
            <td>${p.delta != null ? p.delta.toFixed(3) : '--'}</td>
            <td>${p.theta != null ? p.theta.toFixed(3) : '--'}</td>
            <td>${p.gamma != null ? p.gamma.toFixed(4) : '--'}</td>
            <td>${p.vega != null ? p.vega.toFixed(3) : '--'}</td>
            <td>${p.iv != null ? (p.iv*100).toFixed(0)+'%' : '--'}</td>
            <td style="color:${p.current_pnl != null && p.current_pnl >= 0 ? 'var(--green)' : 'var(--red)'}">
              ${p.current_pnl != null ? '$'+p.current_pnl.toFixed(0) : '--'}</td>
          </tr>`).join('')}
        </table>`;
        // Show staleness from the most recent snapshot
        const ages = greeks.positions.map(p => p.snapshot_age_minutes).filter(a => a != null);
        const maxAge = ages.length ? Math.max(...ages) : null;
        document.getElementById('greeks-age').textContent = maxAge != null
          ? (maxAge < 60 ? Math.round(maxAge) + 'm ago' : Math.floor(maxAge/60) + 'h ago')
          : '';
      } else {
        document.getElementById('greeks-summary').innerHTML =
          '<div class="empty" style="grid-column:1/-1">No Greeks data \u2014 open positions needed</div>';
        document.getElementById('greeks-positions').innerHTML = '';
        document.getElementById('greeks-age').textContent = '';
      }
    } catch(e) {
      console.error('Greeks fetch error:', e);
      document.getElementById('greeks-summary').innerHTML =
        '<div class="empty" style="grid-column:1/-1">Greeks unavailable</div>';
    }

    // Daemon focus — derived from staged + positions + alive state
    const focusEl = document.getElementById('focus-stat');
    if (focusEl) {
      let focusLabel, focusColor;
      if (!alive) {
        focusLabel = 'OFFLINE'; focusColor = 'red';
      } else if (status.status === 'paused') {
        focusLabel = 'PAUSED'; focusColor = 'yellow';
      } else {
        const activeStates = (staged.trades||[]).filter(t => ['VALIDATING','READY','CONFIRMED'].includes(t.state));
        const waitingStates = (staged.trades||[]).filter(t => t.state === 'STAGED');
        if (activeStates.length > 0) {
          focusLabel = 'EXECUTING'; focusColor = 'green';
        } else if (waitingStates.length > 0) {
          focusLabel = 'STAGED'; focusColor = 'yellow';
        } else if (positions.length > 0) {
          focusLabel = 'MONITORING'; focusColor = '';
        } else {
          focusLabel = 'IDLE'; focusColor = '';
        }
      }
      focusEl.className = 'stat' + (focusColor ? ' ' + focusColor : '');
      focusEl.innerHTML = `<div class="value" style="font-size:14px;">${focusLabel}</div><div class="label">Focus</div>`;
    }

    // Daemon plan message
    const planEl = document.getElementById('daemon-plan');
    if (planEl) {
      const mktOpen = _isMarketOpen();
      let plan = '';

      if (!alive) {
        plan = '';
      } else if (status.status === 'paused') {
        plan = 'Daemon is paused. Resume to continue normal operations.';
      } else if (!mktOpen) {
        const hasPositions = positions.length > 0;
        const lines = ['Waiting for US market to open. At market open the daemon will:'];
        if (hasPositions) {
          lines.push('\u2022 Check ' + positions.length + ' open position' + (positions.length > 1 ? 's' : '') + ' and evaluate for exit/close');
        }
        lines.push('\u2022 Run the auto-scan pipeline to find new trade candidates');
        lines.push('\u2022 Evaluate candidates against strategy criteria (delta, OTM%, premium, margin)');
        lines.push('\u2022 Stage qualifying trades and execute if autonomy level permits');
        lines.push('\u2022 Monitor all positions until market close, checking every 15 minutes');
        lines.push('\u2022 Run end-of-day sync, reconcile with IBKR, and record the day');
        plan = lines.join('<br>');
      } else {
        const hasPositions = positions.length > 0;
        const stagedCount = staged.summary.count;
        const pendingCount = queue.length;
        const lines = ['Market is open. The daemon is actively:'];
        if (hasPositions) {
          lines.push('\u2022 Monitoring ' + positions.length + ' open position' + (positions.length > 1 ? 's' : '') + ' for exit triggers (profit target, stop loss, time exit)');
        }
        if (stagedCount > 0) {
          lines.push('\u2022 Processing ' + stagedCount + ' staged trade' + (stagedCount > 1 ? 's' : '') + ' for execution');
        }
        if (pendingCount > 0) {
          lines.push('\u2022 Awaiting human review on ' + pendingCount + ' pending decision' + (pendingCount > 1 ? 's' : ''));
        }
        lines.push('\u2022 Running scheduled checks every 15 minutes (market data, VIX, position P&L)');
        lines.push('\u2022 Reasoning with Claude on any new events or material changes');
        plan = lines.join('<br>');
      }

      if (plan) {
        planEl.innerHTML = plan;
        planEl.style.display = '';
      } else {
        planEl.style.display = 'none';
      }
    }

    // Costs
    const costs = await (await fetch('/api/costs')).json();
    document.getElementById('costs-body').innerHTML = `
      <div class="status-grid" style="grid-template-columns:repeat(4,1fr);">
        <div class="stat"><div class="value">$${costs.daily_total_usd.toFixed(4)}</div><div class="label">Today</div></div>
        <div class="stat"><div class="value">$${costs.monthly_total_usd.toFixed(4)}</div><div class="label">This Month</div></div>
        <div class="stat"><div class="value">$${costs.all_time_total_usd.toFixed(2)}</div><div class="label">All Time</div></div>
        <div class="stat"><div class="value">${costs.calls_today}</div><div class="label">Calls Today</div></div>
      </div>`;

    // Guardrails
    try {
      const gr = await (await fetch('/api/guardrails')).json();
      const blocksColor = gr.guardrail_blocks > 0 ? 'red' : 'green';
      const warnsColor = gr.guardrail_warnings > 0 ? 'yellow' : 'green';
      document.getElementById('guardrails-body').innerHTML = `
        <div class="status-grid" style="grid-template-columns:repeat(3,1fr);">
          <div class="stat ${blocksColor}"><div class="value">${gr.guardrail_blocks||0}</div><div class="label">Blocks</div></div>
          <div class="stat ${warnsColor}"><div class="value">${gr.guardrail_warnings||0}</div><div class="label">Warnings</div></div>
          <div class="stat"><div class="value">${gr.total_decisions||0}</div><div class="label">Decisions</div></div>
        </div>
        ${gr.recent_findings && gr.recent_findings.length ? (() => {
          const findings = gr.recent_findings.slice(0,5);
          const unreadCount = findings.filter(f => !readFindings.has(f.decision_id + ':' + f.guard_name)).length;
          return '<div style="margin-top:10px;font-size:11px;">' +
            (unreadCount > 0 ? '<div style="margin-bottom:6px;text-align:right;"><button class="btn btn-control" onclick="markAllFindingsRead()" style="padding:2px 8px;font-size:10px;">Mark All Read</button></div>' : '') +
            findings.map(f => {
              const key = f.decision_id + ':' + f.guard_name;
              const isNew = !readFindings.has(key);
              return '<div data-finding-key="' + key + '" style="margin-bottom:6px;padding:4px 6px;border-radius:3px;' +
                (isNew ? 'background:rgba(255,214,0,0.08);color:var(--text);' : 'color:var(--text-dim);') + '">' +
                (isNew ? '<span style="color:var(--yellow);font-weight:600;margin-right:4px;">\u25cf</span>' : '') +
                '<span class="tag ' + (f.severity === 'block' ? 'tag-no' : 'tag-pending') + '">' + f.severity.toUpperCase() + '</span> ' +
                '<span style="color:var(--text-dim);margin:0 4px;">' + fmtTime(f.timestamp) + '</span> ' +
                esc(f.guard_name) + ': ' + esc(f.reason).substring(0,80) +
                '</div>';
            }).join('') + '</div>';
        })() : ''}`;
    } catch(e) { document.getElementById('guardrails-body').innerHTML = '<div class="empty">Guardrails not available</div>'; }

    // Decisions (filter out duplicate-suppressed noise)
    const allDecisions = await (await fetch('/api/decisions?limit=50')).json();
    const decisions = allDecisions.filter(d => !(d.action === 'MONITOR_ONLY' && d.reasoning && d.reasoning.startsWith('Duplicate ')));
    document.getElementById('dec-count').textContent = decisions.length;
    document.getElementById('decisions-body').innerHTML = decisions.length ? `
      <table>
        <tr><th>ID</th><th>Time</th><th>Event</th><th>Action</th><th>Confidence</th><th>Exec</th><th>Reasoning</th></tr>
        ${decisions.slice(0, 25).map(d => `<tr style="cursor:pointer" onclick="location.href='/decision/${d.id}'">
          <td style="color:var(--text-dim)">${d.id}</td>
          <td style="color:var(--text-dim)">${fmtTime(d.timestamp)}</td>
          <td>${d.event_type}</td>
          <td>${actionTag(d.action)}</td>
          <td>${confBar(d.confidence)}</td>
          <td><span class="tag ${d.executed ? 'tag-yes' : 'tag-no'}">${d.executed ? 'YES' : 'NO'}</span></td>
          <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(d.reasoning||'').replace(/"/g,'&quot;')}">${esc(d.reasoning||'--')}</td>
        </tr>`).join('')}
      </table>` : '<div class="empty">No decisions recorded</div>';

  } catch(e) { console.error('Fetch error:', e); }
}

async function refreshGreeks() {
  const btn = document.getElementById('btn-refresh-greeks');
  const origText = btn.textContent;
  btn.textContent = 'Refreshing...';
  btn.disabled = true;
  try {
    const res = await fetch('/api/refresh-greeks', {method:'POST'});
    if (res.ok) { await fetchData(); }
    else {
      const err = await res.json().catch(() => ({detail:'Unknown error'}));
      alert(err.detail || 'Refresh failed');
    }
  } catch(e) { alert('Refresh failed: ' + e.message); }
  finally { btn.textContent = origText; btn.disabled = false; }
}

function colorLog(line) {
  if (line.includes('| ERROR')) return `<span style="color:var(--red)">${esc(line)}</span>`;
  if (line.includes('| WARNING')) return `<span style="color:var(--yellow)">${esc(line)}</span>`;
  if (line.includes('CLAUDE API CALL')) return `<span style="color:var(--orange)">${esc(line)}</span>`;
  if (line.includes('| INFO') && (line.includes('EXECUTE') || line.includes('approved') || line.includes('Human-approved')))
    return `<span style="color:var(--green)">${esc(line)}</span>`;
  return esc(line);
}
function esc(s) { const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }
function fmtReasoning(s) {
  if (!s) return 'No reasoning provided';
  let t = esc(s);
  t = t.replace(/STEP (\d+)\s*[-–:]\s*/g, '<br><br><b>STEP $1 — </b>');
  t = t.replace(/(OBSERVATION:|ASSESSMENT:|ACTION:|CONCLUSION:)/g, '<br><br><b>$1</b>');
  return t.replace(/^(<br>)+/, '').trim();
}

const dismissedRejections = new Set(JSON.parse(sessionStorage.getItem('taad_dismissed_rej') || '[]'));
function dismissRejection(id) {
  dismissedRejections.add(id);
  sessionStorage.setItem('taad_dismissed_rej', JSON.stringify([...dismissedRejections]));
  fetchData();
}

const readFindings = new Set();
function markAllFindingsRead() {
  document.querySelectorAll('[data-finding-key]').forEach(el => {
    readFindings.add(el.getAttribute('data-finding-key'));
  });
  fetchData();
}

function optTypeTag(t) {
  if (!t) return '';
  const c = t === 'PUT' ? 'tag-put' : 'tag-call';
  return '<span class="tag ' + c + '">' + t + '</span>';
}

async function fetchLogs() {
  try {
    const data = await (await fetch('/api/logs?lines=150')).json();
    const el = document.getElementById('log-body');
    const wasAtBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
    el.innerHTML = data.lines.map(colorLog).join('\\n');
    if (document.getElementById('log-auto').checked && wasAtBottom) {
      el.scrollTop = el.scrollHeight;
    }
  } catch(e) { console.error('Log fetch error:', e); }
}

// Market hours helper: returns {isOpen, nowSecs, openSecs, closeSecs, dow}
function _getMarketState() {
  const now = new Date();
  const etParts = now.toLocaleString('en-US', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit', hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const [datePart, timePart] = etParts.split(', ');
  const [hh, mm, ss] = timePart.split(':').map(Number);
  const etDate = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));
  const dow = etDate.getDay();
  const openSecs = 9 * 3600 + 30 * 60;
  const closeSecs = 16 * 3600;
  const nowSecs = hh * 3600 + mm * 60 + ss;
  const isWeekday = dow >= 1 && dow <= 5;
  const isOpen = isWeekday && nowSecs >= openSecs && nowSecs < closeSecs;
  return { isOpen, nowSecs, openSecs, closeSecs, dow, isWeekday };
}
function _isMarketOpen() { return _getMarketState().isOpen; }

// Market clock: ET time + open/close countdown (ticks every second)
function updateMarketClock() {
  const now = new Date();
  // ET time display
  const etStr = now.toLocaleTimeString('en-GB', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  document.getElementById('et-time').textContent = etStr + ' ET';

  const { isOpen, nowSecs, openSecs, closeSecs, dow, isWeekday } = _getMarketState();

  const el = document.getElementById('market-countdown');

  if (isOpen) {
    // Market open: show time remaining until close
    const remaining = closeSecs - nowSecs;
    const rh = Math.floor(remaining / 3600);
    const rm = Math.floor((remaining % 3600) / 60);
    const rs = remaining % 60;
    el.textContent = 'Closes in ' + rh + 'h ' + String(rm).padStart(2,'0') + 'm ' + String(rs).padStart(2,'0') + 's';
    el.style.color = 'var(--green)';
  } else {
    // Market closed: compute seconds until next open
    let secsUntilOpen;
    if (isWeekday && nowSecs < openSecs) {
      // Before open today
      secsUntilOpen = openSecs - nowSecs;
    } else {
      // After close or weekend — count days to next weekday
      let daysAhead = 1;
      if (dow === 5 && nowSecs >= closeSecs) daysAhead = 3; // Fri after close -> Mon
      else if (dow === 6) daysAhead = 2; // Sat -> Mon
      else if (dow === 0) daysAhead = 1; // Sun -> Mon
      secsUntilOpen = (86400 - nowSecs) + (daysAhead - 1) * 86400 + openSecs;
    }
    const uh = Math.floor(secsUntilOpen / 3600);
    const um = Math.floor((secsUntilOpen % 3600) / 60);
    const us = secsUntilOpen % 60;
    el.textContent = 'Opens in ' + uh + 'h ' + String(um).padStart(2,'0') + 'm ' + String(us).padStart(2,'0') + 's';
    el.style.color = 'var(--text-dim)';
  }
}

initTzButton();
updateMarketClock();
fetchData();
fetchLogs();
setInterval(updateMarketClock, 1000);
setInterval(fetchData, 8000);
setInterval(fetchLogs, 5000);
</script>
</body>
</html>"""


_DECISION_DETAIL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Decision Detail</title>
<style>
  :root {
    --bg: #0f1923; --bg2: #172a3a; --bg3: #1e3a50;
    --border: #2a4a6b; --text: #c8d6e5; --text-dim: #6b8299;
    --accent: #00d4ff; --green: #00e676; --yellow: #ffd600;
    --red: #ff5252; --orange: #ff9100;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; background: var(--bg); color: var(--text); font-size: 13px; }

  .header { background: var(--bg2); border-bottom: 1px solid var(--border); padding: 12px 24px; display: flex; align-items: center; justify-content: space-between; }
  .header h1 { font-size: 16px; color: var(--accent); font-weight: 600; }
  .back-link { color: var(--text-dim); text-decoration: none; font-size: 12px; border: 1px solid var(--border); padding: 4px 12px; border-radius: 4px; }
  .back-link:hover { border-color: var(--accent); color: var(--accent); }

  .content { max-width: 900px; margin: 0 auto; padding: 16px 24px; }

  .card { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; margin-bottom: 16px; }
  .card-header { padding: 10px 16px; border-bottom: 1px solid var(--border); }
  .card-header h2 { font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-dim); font-weight: 600; }
  .card-body { padding: 16px; }

  .tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
  .tag-exec { background: rgba(0, 230, 118, 0.15); color: var(--green); }
  .tag-monitor { background: rgba(0, 212, 255, 0.12); color: var(--accent); }
  .tag-pending { background: rgba(255, 214, 0, 0.15); color: var(--yellow); }
  .tag-review { background: rgba(255, 145, 0, 0.15); color: var(--orange); }
  .tag-yes { background: rgba(0, 230, 118, 0.15); color: var(--green); }
  .tag-no { background: rgba(255, 82, 82, 0.15); color: var(--red); }

  .field { margin-bottom: 12px; }
  .field-label { font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-dim); margin-bottom: 3px; }
  .field-value { font-size: 13px; line-height: 1.5; }
  .field-value.mono { background: var(--bg); padding: 8px 12px; border-radius: 4px; white-space: pre-wrap; word-break: break-word; }

  .field-row { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin-bottom: 12px; }
  .field-row.two { grid-template-columns: 1fr 1fr; }

  ul.factors { margin: 0; padding-left: 18px; }
  ul.factors li { margin-bottom: 4px; line-height: 1.5; }

  .conf-bar { display: inline-block; width: 60px; height: 6px; background: var(--bg3); border: 1px solid var(--border); border-radius: 3px; vertical-align: middle; margin-right: 6px; overflow: hidden; }
  .conf-bar .fill { display: block; height: 100%; border-radius: 2px; }

  .loading { text-align: center; padding: 40px; color: var(--text-dim); }
  .error { text-align: center; padding: 40px; color: var(--red); }
</style>
</head>
<body>

<div class="header">
  <h1 id="page-title">Decision #__DECISION_ID__</h1>
  <div style="display:flex;align-items:center;gap:12px;">
    <button class="btn" id="tz-toggle" onclick="toggleTimezone()" style="padding:2px 8px;font-size:10px;min-width:50px;background:var(--bg3);color:var(--text-dim);border:1px solid var(--border);border-radius:4px;cursor:pointer;">ET</button>
    <a href="/" class="back-link">Back to Dashboard</a>
    <a href="/guardrails" class="back-link" style="margin-right:8px;">Guardrails</a>
  </div>
</div>

<div class="content" id="content">
  <div class="loading">Loading decision...</div>
</div>

<script>
// Auth token (injected by server or from sessionStorage)
const _injected = '__AUTH_TOKEN__';
const _authToken = (_injected && !_injected.includes('AUTH_TOKEN')) ? _injected : '';
if (_authToken) sessionStorage.setItem('taad_token', _authToken);
const _storedToken = _authToken || sessionStorage.getItem('taad_token') || '';
const _origFetch = window.fetch;
window.fetch = function(url, opts) {
  if (_storedToken && typeof url === 'string' && url.startsWith('/api')) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    opts.headers['Authorization'] = 'Bearer ' + _storedToken;
  }
  return _origFetch.call(this, url, opts);
};
if (_storedToken) {
  document.querySelectorAll('a[href^="/"]').forEach(a => {
    const u = new URL(a.href);
    u.searchParams.set('token', _storedToken);
    a.href = u.toString();
  });
}

const DECISION_ID = __DECISION_ID__;

let _tz = localStorage.getItem('taad_tz') || 'ET';
function initTzButton() {
  const btn = document.getElementById('tz-toggle');
  if (btn) btn.textContent = _tz;
}
function toggleTimezone() {
  _tz = _tz === 'ET' ? 'AEDT' : 'ET';
  localStorage.setItem('taad_tz', _tz);
  const btn = document.getElementById('tz-toggle');
  if (btn) btn.textContent = _tz;
  loadDecision();
}
function fmtTime(ts) {
  if (!ts || ts === 'None') return '--';
  let iso = ts.replace(' ', 'T');
  if (!iso.endsWith('Z') && !iso.includes('+')) iso += 'Z';
  const d = new Date(iso);
  if (isNaN(d)) return '--';
  const tz = _tz === 'AEDT' ? 'Australia/Sydney' : 'America/New_York';
  return d.toLocaleString('en-US', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

function actionTag(action) {
  const map = {
    'EXECUTE_TRADES': 'tag-exec', 'STAGE_CANDIDATES': 'tag-exec',
    'MONITOR_ONLY': 'tag-monitor', 'CLOSE_POSITION': 'tag-exec',
    'CLOSE_ALL_POSITIONS': 'tag-exec',
    'REQUEST_HUMAN_REVIEW': 'tag-review', 'GUARDRAIL_BLOCKED': 'tag-no',
  };
  return `<span class="tag ${map[action] || 'tag-pending'}">${action}</span>`;
}

function confBar(conf) {
  if (conf == null) return '--';
  const pct = Math.round(conf * 100);
  const color = pct >= 80 ? 'var(--green)' : pct >= 60 ? 'var(--yellow)' : 'var(--red)';
  return `<span class="conf-bar"><span class="fill" style="width:${pct}%;background:${color}"></span></span>${pct}%`;
}

function esc(s) {
  if (s == null) return '--';
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

function fmtReasoning(s) {
  if (!s) return 'No reasoning provided';
  let t = esc(s);
  t = t.replace(/STEP (\\d+)\\s*[-–:]\\s*/g, '<br><br><b>STEP $1 — </b>');
  t = t.replace(/(OBSERVATION:|ASSESSMENT:|ACTION:|CONCLUSION:)/g, '<br><br><b>$1</b>');
  return t.replace(/^(<br>)+/, '').trim();
}

function fmtJson(obj) {
  if (obj == null) return '--';
  try {
    return esc(typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2));
  } catch { return esc(String(obj)); }
}

async function loadDecision() {
  try {
    const r = await fetch('/api/decisions/' + DECISION_ID);
    if (!r.ok) {
      document.getElementById('content').innerHTML = `<div class="error">Decision #${DECISION_ID} not found.</div>`;
      return;
    }
    const d = await r.json();

    document.title = `Decision #${d.id} - ${d.action}`;
    document.getElementById('page-title').innerHTML = `Decision #${d.id} ${actionTag(d.action)}`;

    const ts = fmtTime(d.timestamp);

    document.getElementById('content').innerHTML = `
      <!-- Header info -->
      <div class="card">
        <div class="card-header"><h2>Overview</h2></div>
        <div class="card-body">
          <div class="field-row">
            <div class="field"><div class="field-label">ID</div><div class="field-value">${d.id}</div></div>
            <div class="field"><div class="field-label">Timestamp</div><div class="field-value">${esc(ts)}</div></div>
            <div class="field"><div class="field-label">Event Type</div><div class="field-value">${esc(d.event_type)}</div></div>
          </div>
          <div class="field-row">
            <div class="field"><div class="field-label">Action</div><div class="field-value">${actionTag(d.action)}</div></div>
            <div class="field"><div class="field-label">Confidence</div><div class="field-value">${confBar(d.confidence)}</div></div>
            <div class="field"><div class="field-label">Autonomy Level</div><div class="field-value">L${d.autonomy_level ?? '--'}</div></div>
          </div>
        </div>
      </div>

      <!-- Reasoning -->
      <div class="card">
        <div class="card-header"><h2>Reasoning</h2></div>
        <div class="card-body">
          <div class="field">
            <div class="field-label">Full Reasoning</div>
            <div class="field-value mono">${fmtReasoning(d.reasoning)}</div>
          </div>
          <div class="field-row two">
            <div class="field">
              <div class="field-label">Key Factors</div>
              <div class="field-value">${d.key_factors && d.key_factors.length
                ? '<ul class="factors">' + d.key_factors.map(f => '<li>' + esc(f) + '</li>').join('') + '</ul>'
                : '--'}</div>
            </div>
            <div class="field">
              <div class="field-label">Risks Considered</div>
              <div class="field-value">${d.risks_considered && d.risks_considered.length
                ? '<ul class="factors">' + d.risks_considered.map(r => '<li>' + esc(r) + '</li>').join('') + '</ul>'
                : '--'}</div>
            </div>
          </div>
        </div>
      </div>

      <!-- Autonomy & Execution -->
      <div class="card">
        <div class="card-header"><h2>Autonomy & Execution</h2></div>
        <div class="card-body">
          <div class="field-row">
            <div class="field">
              <div class="field-label">Autonomy Approved</div>
              <div class="field-value"><span class="tag ${d.autonomy_approved ? 'tag-yes' : 'tag-no'}">${d.autonomy_approved ? 'YES' : 'NO'}</span></div>
            </div>
            <div class="field">
              <div class="field-label">Executed</div>
              <div class="field-value"><span class="tag ${d.executed ? 'tag-yes' : 'tag-no'}">${d.executed ? 'YES' : 'NO'}</span></div>
            </div>
            <div class="field">
              <div class="field-label">Escalation Reason</div>
              <div class="field-value">${esc(d.escalation_reason)}</div>
            </div>
          </div>
          <div class="field-row">
            <div class="field">
              <div class="field-label">Human Override</div>
              <div class="field-value">${d.human_override ? '<span class="tag tag-yes">YES</span>' : '<span class="tag tag-no">NO</span>'}</div>
            </div>
            <div class="field">
              <div class="field-label">Human Decision</div>
              <div class="field-value">${esc(d.human_decision)}</div>
            </div>
            <div class="field">
              <div class="field-label">Human Decided At</div>
              <div class="field-value">${d.human_decided_at ? esc(d.human_decided_at.replace('T', ' ').substring(0, 19)) : '--'}</div>
            </div>
          </div>
          ${d.execution_result ? `<div class="field">
            <div class="field-label">Execution Result</div>
            <div class="field-value mono">${fmtJson(d.execution_result)}</div>
          </div>` : ''}
          ${d.execution_error ? `<div class="field">
            <div class="field-label">Execution Error</div>
            <div class="field-value mono" style="color:var(--red)">${esc(d.execution_error)}</div>
          </div>` : ''}
        </div>
      </div>

      <!-- Cost -->
      <div class="card">
        <div class="card-header"><h2>Cost</h2></div>
        <div class="card-body">
          <div class="field-row" style="grid-template-columns:1fr 1fr 1fr 1fr;">
            <div class="field"><div class="field-label">Input Tokens</div><div class="field-value">${d.input_tokens != null ? d.input_tokens.toLocaleString() : '--'}</div></div>
            <div class="field"><div class="field-label">Output Tokens</div><div class="field-value">${d.output_tokens != null ? d.output_tokens.toLocaleString() : '--'}</div></div>
            <div class="field"><div class="field-label">Model</div><div class="field-value">${esc(d.model_used)}</div></div>
            <div class="field"><div class="field-label">Cost</div><div class="field-value">${d.cost_usd != null ? '$' + d.cost_usd.toFixed(4) : '--'}</div></div>
          </div>
        </div>
      </div>
    `;
  } catch(e) {
    document.getElementById('content').innerHTML = `<div class="error">Error loading decision: ${esc(e.message)}</div>`;
  }
}

initTzButton();
loadDecision();
</script>
</body>
</html>"""

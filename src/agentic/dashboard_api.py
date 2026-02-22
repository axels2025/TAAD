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
    DaemonHealth,
    DecisionAudit,
    GuardrailMetric,
    ScanOpportunity,
    Trade,
)


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

    @app.get("/scanner", response_class=HTMLResponse)
    def scanner_page():
        """HTML scanner page."""
        return get_scanner_html()

    # Include config router
    from src.agentic.config_api import create_config_router, get_config_html

    app.include_router(create_config_router(verify_token))

    @app.get("/config", response_class=HTMLResponse)
    def config_page():
        """HTML config editor page."""
        return get_config_html()

    @app.get("/api/status")
    def get_status(token: None = Depends(verify_token)):
        """Get daemon status with live process check."""
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
            }

    @app.get("/api/positions")
    def get_positions(token: None = Depends(verify_token)):
        """Get open positions."""
        today = date.today()
        with get_db_session() as db:
            trades = db.query(Trade).filter(Trade.exit_date.is_(None)).all()
            return [
                {
                    "trade_id": t.trade_id,
                    "symbol": t.symbol,
                    "strike": t.strike,
                    "expiration": str(t.expiration),
                    "entry_premium": t.entry_premium,
                    "contracts": t.contracts,
                    "entry_date": str(t.entry_date),
                    "dte": (t.expiration - today).days if t.expiration else None,
                }
                for t in trades
            ]

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
            return [
                {
                    "id": d.id,
                    "timestamp": str(d.timestamp),
                    "action": d.action,
                    "confidence": d.confidence,
                    "reasoning": d.reasoning,
                    "key_factors": d.key_factors or [],
                    "risks_considered": d.risks_considered or [],
                    "event_type": d.event_type,
                    "autonomy_level": d.autonomy_level,
                }
                for d in pending
            ]

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
            audit.human_decided_at = datetime.utcnow()
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
            audit.human_decided_at = datetime.utcnow()
            audit.human_override = True
            db.commit()

            return {"status": "rejected", "decision_id": decision_id}

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
        from src.agentic.health_monitor import HealthMonitor

        pid = HealthMonitor.is_daemon_running()
        if pid:
            return {"status": "already_running", "pid": pid}

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
        from src.agentic.health_monitor import HealthMonitor

        pid = HealthMonitor.is_daemon_running()
        if not pid:
            return {"status": "not_running"}

        try:
            os.kill(pid, signal.SIGTERM)
            logger.info(f"SIGTERM sent to daemon (pid={pid})")
            return {"status": "stopping", "pid": pid}
        except ProcessLookupError:
            return {"status": "not_running"}
        except PermissionError:
            raise HTTPException(status_code=500, detail="Permission denied sending signal")

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

            return {
                "daily_total_usd": round(daily_total, 4),
                "monthly_total_usd": round(monthly_total, 4),
                "calls_today": total_calls,
                "date": str(today),
            }

    @app.get("/api/guardrails")
    def get_guardrails(token: None = Depends(verify_token)):
        """Get guardrail activity summary for today."""
        from sqlalchemy import func as sa_func

        with get_db_session() as db:
            today = date.today()

            # Count today's decisions with guardrail flags
            decisions_today = (
                db.query(DecisionAudit)
                .filter(sa_func.date(DecisionAudit.timestamp) == today)
                .all()
            )

            total_decisions = len(decisions_today)
            blocks = 0
            warnings = 0
            flagged_guards = {}

            # Last 10 findings
            recent_findings = []

            for d in decisions_today:
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
                "date": str(today),
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

    @app.get("/", response_class=HTMLResponse)
    def dashboard_page():
        """HTML dashboard."""
        return _DASHBOARD_HTML

    @app.get("/decision/{decision_id}", response_class=HTMLResponse)
    def decision_detail_page(decision_id: int):
        """HTML detail page for a single decision."""
        return _DECISION_DETAIL_HTML.replace("__DECISION_ID__", str(decision_id))

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

  /* Buttons */
  .btn { border: none; padding: 6px 14px; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 12px; font-weight: 600; transition: all 0.15s; }
  .btn-approve { background: rgba(0, 230, 118, 0.15); color: var(--green); border: 1px solid rgba(0, 230, 118, 0.3); }
  .btn-approve:hover { background: rgba(0, 230, 118, 0.3); }
  .btn-reject { background: rgba(255, 82, 82, 0.12); color: var(--red); border: 1px solid rgba(255, 82, 82, 0.3); }
  .btn-reject:hover { background: rgba(255, 82, 82, 0.25); }
  .btn-control { background: var(--bg3); color: var(--text); border: 1px solid var(--border); }
  .btn-control:hover { border-color: var(--accent); color: var(--accent); }
  .controls { display: flex; gap: 8px; }

  /* Pending cards */
  .pending-item { background: var(--bg3); border: 1px solid rgba(255, 214, 0, 0.2); border-radius: 6px; padding: 14px; margin-bottom: 10px; }
  .pending-item:last-child { margin-bottom: 0; }
  .pending-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
  .pending-action { font-weight: 700; color: var(--yellow); font-size: 14px; }
  .pending-meta { font-size: 11px; color: var(--text-dim); }
  .pending-reasoning { margin: 8px 0; line-height: 1.5; color: var(--text); }
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
  <div style="display:flex;align-items:center;gap:16px;">
    <a href="/scanner" class="btn btn-control" style="text-decoration:none;">Option Scanner</a>
    <a href="/config" class="btn btn-control" style="text-decoration:none;">Settings</a>
    <div class="controls" id="controls">
      <button class="btn btn-approve" onclick="apiCall('/api/start')" id="btn-start">Start Daemon</button>
      <button class="btn btn-reject" onclick="apiCall('/api/stop')" id="btn-stop">Stop Daemon</button>
      <button class="btn btn-control" onclick="apiCall('/api/pause')" id="btn-pause">Pause</button>
      <button class="btn btn-control" onclick="apiCall('/api/resume')" id="btn-resume">Resume</button>
    </div>
    <div class="refresh-badge"><span class="dot" id="status-dot"></span><span id="status-label">Checking...</span> <span id="last-refresh"></span></div>
  </div>
</div>

<div class="grid">
  <!-- Status -->
  <div class="card full">
    <div class="card-body">
      <div class="status-grid" id="status-grid">
        <div class="stat"><div class="value">--</div><div class="label">Status</div></div>
      </div>
    </div>
  </div>

  <!-- Pending Approvals -->
  <div class="card full" id="pending-card" style="display:none;">
    <div class="card-header">
      <h2>Pending Approvals</h2>
      <span class="badge" style="background:rgba(255,214,0,0.15);color:var(--yellow);" id="pending-count">0</span>
    </div>
    <div class="card-body" id="pending-body"></div>
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
    <div class="card-body" id="decisions-body" style="max-height:400px;overflow-y:auto;"></div>
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
function actionTag(action) {
  const map = {
    'EXECUTE_TRADES': 'tag-exec', 'STAGE_CANDIDATES': 'tag-exec',
    'MONITOR_ONLY': 'tag-monitor', 'CLOSE_POSITION': 'tag-exec',
    'REQUEST_HUMAN_REVIEW': 'tag-review',
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

async function approveDecision(id) {
  await apiCall('/api/approve/' + id);
}

async function rejectDecision(id) {
  await apiCall('/api/reject/' + id);
}

async function unstage(id) {
  if (!confirm('Unstage this trade?')) return;
  await apiCall('/api/unstage/' + id);
}

async function unstageAll() {
  if (!confirm('Unstage ALL staged trades?')) return;
  await apiCall('/api/unstage-all');
}

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
    label.textContent = alive ? 'Daemon Live' : 'Daemon Stopped';

    // Show/hide buttons based on state
    document.getElementById('btn-start').style.display = alive ? 'none' : '';
    document.getElementById('btn-stop').style.display = alive ? '' : 'none';
    document.getElementById('btn-pause').style.display = alive && status.status === 'running' ? '' : 'none';
    document.getElementById('btn-resume').style.display = alive && status.status === 'paused' ? '' : 'none';

    document.getElementById('status-grid').innerHTML = `
      <div class="stat ${sColor}"><div class="value">${(status.status||'--').toUpperCase()}</div><div class="label">Daemon</div></div>
      <div class="stat" id="focus-stat"><div class="value" style="font-size:14px;">--</div><div class="label">Focus</div></div>
      <div class="stat"><div class="value">L${status.autonomy_level||'?'}</div><div class="label">Autonomy</div></div>
      <div class="stat"><div class="value">${status.events_processed_today||0}</div><div class="label">Events Today</div></div>
      <div class="stat"><div class="value">${status.decisions_made_today||0}</div><div class="label">Decisions</div></div>
      <div class="stat ${status.errors_today > 0 ? 'red' : ''}"><div class="value">${status.errors_today||0}</div><div class="label">Errors</div></div>
      <div class="stat"><div class="value">${uptime}</div><div class="label">Uptime</div></div>
      <div class="stat"><div class="value">${timeAgo(status.last_heartbeat)}</div><div class="label">Heartbeat</div></div>
    `;

    // Pending approvals
    const queue = await (await fetch('/api/queue')).json();
    const pc = document.getElementById('pending-card');
    document.getElementById('pending-count').textContent = queue.length;
    if (queue.length > 0) {
      pc.style.display = '';
      document.getElementById('pending-body').innerHTML = queue.map(q => `
        <div class="pending-item">
          <div class="pending-header">
            <div><span class="pending-action">${q.action}</span> <span class="pending-meta">#${q.id} | ${q.event_type} | ${fmtTime(q.timestamp)}</span></div>
            <div>${confBar(q.confidence)}</div>
          </div>
          <div class="pending-reasoning">${esc(q.reasoning) || 'No reasoning provided'}</div>
          ${q.key_factors && q.key_factors.length ? `<div class="pending-factors"><span>Key Factors</span><ul>${q.key_factors.map(f=>'<li>'+esc(f)+'</li>').join('')}</ul></div>` : ''}
          ${q.risks_considered && q.risks_considered.length ? `<div class="pending-factors"><span>Risks Considered</span><ul>${q.risks_considered.map(r=>'<li>'+esc(r)+'</li>').join('')}</ul></div>` : ''}
          <div class="pending-buttons">
            <button class="btn btn-approve" onclick="approveDecision(${q.id})">Approve</button>
            <button class="btn btn-reject" onclick="rejectDecision(${q.id})">Reject</button>
          </div>
        </div>
      `).join('');
    } else {
      pc.style.display = 'none';
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
        <tr><th>Symbol</th><th>Strike</th><th>Exp</th><th>Premium</th><th>Qty</th><th>DTE</th></tr>
        ${positions.map(p => `<tr>
          <td style="font-weight:600;color:var(--accent)">${p.symbol}</td>
          <td>$${p.strike}</td>
          <td>${p.expiration}</td>
          <td>$${Number(p.entry_premium).toFixed(2)}</td>
          <td>${p.contracts}</td>
          <td>${p.dte ?? '--'}</td>
        </tr>`).join('')}
      </table>` : '<div class="empty">No open positions</div>';

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

    // Costs
    const costs = await (await fetch('/api/costs')).json();
    document.getElementById('costs-body').innerHTML = `
      <div class="status-grid" style="grid-template-columns:repeat(3,1fr);">
        <div class="stat"><div class="value">$${costs.daily_total_usd.toFixed(4)}</div><div class="label">Today</div></div>
        <div class="stat"><div class="value">$${costs.monthly_total_usd.toFixed(4)}</div><div class="label">This Month</div></div>
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
        ${gr.recent_findings && gr.recent_findings.length ? '<div style="margin-top:10px;font-size:11px;color:var(--text-dim);">' +
          gr.recent_findings.slice(0,5).map(f =>
            '<div style="margin-bottom:4px;">' +
            '<span class="tag ' + (f.severity === 'block' ? 'tag-no' : 'tag-pending') + '">' + f.severity.toUpperCase() + '</span> ' +
            esc(f.guard_name) + ': ' + esc(f.reason).substring(0,80) +
            '</div>'
          ).join('') + '</div>' : ''}`;
    } catch(e) { document.getElementById('guardrails-body').innerHTML = '<div class="empty">Guardrails not available</div>'; }

    // Decisions
    const decisions = await (await fetch('/api/decisions?limit=15')).json();
    document.getElementById('dec-count').textContent = decisions.length;
    document.getElementById('decisions-body').innerHTML = decisions.length ? `
      <table>
        <tr><th>ID</th><th>Time</th><th>Event</th><th>Action</th><th>Confidence</th><th>Exec</th><th>Reasoning</th></tr>
        ${decisions.map(d => `<tr style="cursor:pointer" onclick="location.href='/decision/${d.id}'">
          <td style="color:var(--text-dim)">${d.id}</td>
          <td style="color:var(--text-dim)">${fmtTime(d.timestamp)}</td>
          <td>${d.event_type}</td>
          <td>${actionTag(d.action)}</td>
          <td>${confBar(d.confidence)}</td>
          <td><span class="tag ${d.executed ? 'tag-yes' : 'tag-no'}">${d.executed ? 'YES' : 'NO'}</span></td>
          <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(d.reasoning||'').replace(/"/g,'&quot;')}">${esc(d.reasoning||'--')}</td>
        </tr>`).join('')}
      </table>` : '<div class="empty">No decisions recorded</div>';

    document.getElementById('last-refresh').textContent = new Date().toLocaleTimeString();
  } catch(e) { console.error('Fetch error:', e); }
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

initTzButton();
fetchData();
fetchLogs();
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
  </div>
</div>

<div class="content" id="content">
  <div class="loading">Loading decision...</div>
</div>

<script>
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
    'REQUEST_HUMAN_REVIEW': 'tag-review',
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
            <div class="field-value mono">${esc(d.reasoning)}</div>
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

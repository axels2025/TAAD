"""CLI commands for the TAAD daemon.

Provides Typer subgroup `daemon` with commands:
start, status, context, pause, resume, override, set-autonomy, audit, costs, emergency-stop
"""

import json
import os
import signal
from datetime import date, datetime
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from src.config.logging import setup_logging
from src.data.database import get_db_session, init_database


daemon_app = typer.Typer(
    name="daemon",
    help="TAAD - The Autonomous Agentic Trading Daemon",
    no_args_is_help=True,
)

console = Console()


@daemon_app.command(name="start")
def daemon_start(
    config: Optional[str] = typer.Option(None, help="Path to phase5.yaml"),
    foreground: bool = typer.Option(False, "--fg", help="Run in foreground (no daemonize)"),
) -> None:
    """Start the TAAD daemon."""
    from src.agentic.health_monitor import HealthMonitor

    # Check if already running
    pid = HealthMonitor.is_daemon_running()
    if pid:
        console.print(f"[yellow]Daemon already running (pid={pid})[/yellow]")
        raise typer.Exit(1)

    setup_logging()
    console.print("[bold blue]Starting TAAD daemon...[/bold blue]")

    from src.agentic.daemon import start_daemon

    try:
        start_daemon(config_path=config)
    except KeyboardInterrupt:
        console.print("\n[yellow]Daemon stopped by user[/yellow]")


@daemon_app.command(name="status")
def daemon_status() -> None:
    """Show daemon status."""
    init_database()

    with get_db_session() as db:
        from src.data.models import DaemonHealth

        health = db.query(DaemonHealth).get(1)

        if not health:
            console.print("[dim]No daemon health record found[/dim]")
            return

        # Status color
        status_colors = {
            "running": "green",
            "paused": "yellow",
            "stopped": "red",
            "error": "bold red",
        }
        color = status_colors.get(health.status, "white")

        table = Table(title="TAAD Daemon Status", show_header=False)
        table.add_column("Field", style="cyan", width=22)
        table.add_column("Value")

        table.add_row("Status", f"[{color}]{health.status}[/{color}]")
        table.add_row("PID", str(health.pid or "N/A"))
        table.add_row("Autonomy Level", f"L{health.autonomy_level}")
        table.add_row("Last Heartbeat", str(health.last_heartbeat or "Never"))
        table.add_row("Uptime", f"{(health.uptime_seconds or 0) // 60} minutes")
        table.add_row("Events Today", str(health.events_processed_today or 0))
        table.add_row("Decisions Today", str(health.decisions_made_today or 0))
        table.add_row("Errors Today", str(health.errors_today or 0))
        table.add_row("Started At", str(health.started_at or "N/A"))
        table.add_row("Message", health.message or "")

        console.print(table)


@daemon_app.command(name="context")
def daemon_context() -> None:
    """Show daemon working memory context."""
    init_database()

    with get_db_session() as db:
        from src.data.models import WorkingMemoryRow

        row = db.query(WorkingMemoryRow).get(1)
        if not row:
            console.print("[dim]No working memory found[/dim]")
            return

        console.print("[bold]Working Memory Context[/bold]\n")
        console.print(f"Autonomy Level: L{row.autonomy_level}")
        console.print(f"Last Updated: {row.updated_at}")

        if row.strategy_state:
            console.print("\n[cyan]Strategy State:[/cyan]")
            console.print(json.dumps(row.strategy_state, indent=2, default=str))

        if row.market_context:
            console.print("\n[cyan]Market Context:[/cyan]")
            console.print(json.dumps(row.market_context, indent=2, default=str))

        if row.recent_decisions:
            console.print(f"\n[cyan]Recent Decisions ({len(row.recent_decisions)}):[/cyan]")
            for d in (row.recent_decisions or [])[-5:]:
                console.print(
                    f"  [{d.get('timestamp', '?')}] {d.get('action', '?')} "
                    f"(conf={d.get('confidence', '?')})"
                )

        if row.anomalies:
            console.print(f"\n[yellow]Anomalies ({len(row.anomalies)}):[/yellow]")
            for a in row.anomalies[-5:]:
                console.print(f"  - {a.get('description', str(a))}")


@daemon_app.command(name="pause")
def daemon_pause() -> None:
    """Pause the daemon (stops processing events)."""
    init_database()

    with get_db_session() as db:
        from src.data.models import DaemonHealth

        health = db.query(DaemonHealth).get(1)
        if health and health.status == "running":
            health.status = "paused"
            health.message = "Paused by CLI"
            db.commit()
            console.print("[yellow]Daemon paused[/yellow]")
        else:
            console.print("[dim]Daemon is not running[/dim]")


@daemon_app.command(name="resume")
def daemon_resume() -> None:
    """Resume a paused daemon."""
    init_database()

    with get_db_session() as db:
        from src.data.models import DaemonHealth

        health = db.query(DaemonHealth).get(1)
        if health and health.status == "paused":
            health.status = "running"
            health.message = "Resumed by CLI"
            db.commit()
            console.print("[green]Daemon resumed[/green]")
        else:
            console.print("[dim]Daemon is not paused[/dim]")


@daemon_app.command(name="set-autonomy")
def daemon_set_autonomy(
    level: int = typer.Argument(..., help="Autonomy level (1-4)"),
) -> None:
    """Set the daemon autonomy level."""
    if level < 1 or level > 4:
        console.print("[red]Level must be 1-4[/red]")
        raise typer.Exit(1)

    init_database()

    with get_db_session() as db:
        from src.data.models import DaemonHealth, WorkingMemoryRow

        # Update health table
        health = db.query(DaemonHealth).get(1)
        if health:
            health.autonomy_level = level
            health.message = f"Autonomy set to L{level} by CLI"

        # Update working memory
        row = db.query(WorkingMemoryRow).get(1)
        if row:
            row.autonomy_level = level

        db.commit()
        console.print(f"[green]Autonomy level set to L{level}[/green]")


@daemon_app.command(name="audit")
def daemon_audit(
    limit: int = typer.Option(20, help="Number of records to show"),
) -> None:
    """Show decision audit log."""
    init_database()

    with get_db_session() as db:
        from src.data.models import DecisionAudit

        decisions = (
            db.query(DecisionAudit)
            .order_by(DecisionAudit.timestamp.desc())
            .limit(limit)
            .all()
        )

        if not decisions:
            console.print("[dim]No decisions recorded yet[/dim]")
            return

        table = Table(title=f"Decision Audit (last {limit})")
        table.add_column("ID", style="bold", width=5)
        table.add_column("Time", style="dim", width=19)
        table.add_column("Event", width=18)
        table.add_column("Action", width=20)
        table.add_column("Conf", width=5)
        table.add_column("L", width=2)
        table.add_column("Exec", width=4)
        table.add_column("Reasoning", width=40)

        for d in decisions:
            exec_icon = "[green]Y[/green]" if d.executed else "[red]N[/red]"
            table.add_row(
                str(d.id),
                str(d.timestamp)[:19] if d.timestamp else "",
                d.event_type or "",
                d.action or "",
                f"{d.confidence:.2f}" if d.confidence else "",
                str(d.autonomy_level),
                exec_icon,
                (d.reasoning or "")[:40],
            )

        console.print(table)


@daemon_app.command(name="costs")
def daemon_costs() -> None:
    """Show Claude API cost summary."""
    init_database()

    from sqlalchemy import func as sa_func

    with get_db_session() as db:
        from src.data.models import ClaudeApiCost

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

        total_calls_today = (
            db.query(sa_func.count(ClaudeApiCost.id))
            .filter(sa_func.date(ClaudeApiCost.timestamp) == today)
            .scalar()
        ) or 0

        all_time = (
            db.query(sa_func.sum(ClaudeApiCost.cost_usd)).scalar()
        ) or 0.0

        table = Table(title="Claude API Costs", show_header=False)
        table.add_column("Metric", style="cyan", width=20)
        table.add_column("Value")

        table.add_row("Today", f"${daily_total:.4f}")
        table.add_row("This Month", f"${monthly_total:.4f}")
        table.add_row("All Time", f"${all_time:.4f}")
        table.add_row("Calls Today", str(total_calls_today))

        console.print(table)


@daemon_app.command(name="emergency-stop")
def daemon_emergency_stop() -> None:
    """Emergency stop: halt all trading immediately."""
    console.print("[bold red]EMERGENCY STOP[/bold red]")
    console.print("This will halt all trading and stop the daemon.")

    confirm = typer.confirm("Are you sure?")
    if not confirm:
        console.print("[dim]Cancelled[/dim]")
        return

    # Activate kill switch
    from src.services.kill_switch import KillSwitch

    ks = KillSwitch(register_signals=False)
    ks.halt("Emergency stop via CLI")
    console.print("[bold red]Kill switch activated[/bold red]")

    # Update daemon health
    init_database()
    with get_db_session() as db:
        from src.data.models import DaemonHealth

        health = db.query(DaemonHealth).get(1)
        if health:
            health.status = "stopped"
            health.message = "Emergency stop via CLI"
            db.commit()

    # Send SIGTERM to daemon if running
    from src.agentic.health_monitor import HealthMonitor

    pid = HealthMonitor.is_daemon_running()
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            console.print(f"[yellow]SIGTERM sent to daemon (pid={pid})[/yellow]")
        except ProcessLookupError:
            console.print("[dim]Daemon process not found[/dim]")

    console.print("[green]Emergency stop complete[/green]")


@daemon_app.command(name="pending")
def daemon_pending() -> None:
    """Show decisions awaiting human approval."""
    init_database()

    with get_db_session() as db:
        from src.data.models import DecisionAudit

        pending = (
            db.query(DecisionAudit)
            .filter(
                DecisionAudit.executed == False,
                DecisionAudit.action != "MONITOR_ONLY",
                DecisionAudit.human_decision.is_(None),
            )
            .order_by(DecisionAudit.timestamp.desc())
            .limit(20)
            .all()
        )

        if not pending:
            console.print("[dim]No pending decisions[/dim]")
            return

        console.print(f"[bold]Pending Decisions ({len(pending)})[/bold]\n")

        for d in pending:
            from rich.panel import Panel
            from rich.text import Text

            # Header line
            header = f"[bold yellow]#{d.id}[/bold yellow]  [bold]{d.action}[/bold]  confidence={d.confidence:.2f}  L{d.autonomy_level}"
            time_str = str(d.timestamp)[:19] if d.timestamp else ""

            # Build detail lines
            lines = [f"[dim]{time_str}  |  {d.event_type}[/dim]\n"]

            if d.reasoning:
                lines.append(f"[bold]Reasoning:[/bold]\n{d.reasoning}\n")

            if d.key_factors:
                factors = d.key_factors if isinstance(d.key_factors, list) else []
                if factors:
                    lines.append("[bold]Key Factors:[/bold]")
                    for f in factors:
                        lines.append(f"  - {f}")
                    lines.append("")

            if d.risks_considered:
                risks = d.risks_considered if isinstance(d.risks_considered, list) else []
                if risks:
                    lines.append("[bold]Risks Considered:[/bold]")
                    for r in risks:
                        lines.append(f"  - {r}")
                    lines.append("")

            if d.escalation_reason:
                lines.append(f"[yellow]Escalation: {d.escalation_reason}[/yellow]")

            console.print(Panel(
                "\n".join(lines),
                title=header,
                border_style="yellow",
            ))

        console.print(
            "[dim]To approve: nakedtrader daemon approve <ID>[/dim]"
            "\n[dim]To reject:  nakedtrader daemon reject <ID>[/dim]"
        )


@daemon_app.command(name="approve")
def daemon_approve(
    decision_id: int = typer.Argument(..., help="Decision audit ID to approve"),
) -> None:
    """Approve a pending decision and trigger execution."""
    init_database()

    with get_db_session() as db:
        from src.agentic.event_bus import EventBus, EventType
        from src.data.models import DecisionAudit

        audit = db.query(DecisionAudit).get(decision_id)
        if not audit:
            console.print(f"[red]Decision {decision_id} not found[/red]")
            raise typer.Exit(1)

        if audit.executed:
            console.print(f"[yellow]Decision {decision_id} was already executed[/yellow]")
            return

        if audit.human_decision == "approved":
            console.print(f"[yellow]Decision {decision_id} was already approved[/yellow]")
            return

        if audit.human_decision == "rejected":
            console.print(f"[yellow]Decision {decision_id} was already rejected[/yellow]")
            return

        # Mark as approved
        audit.human_decision = "approved"
        audit.human_override = True
        audit.human_decided_at = datetime.utcnow()
        db.commit()

        # Emit HUMAN_OVERRIDE event so the running daemon picks it up and executes
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

        console.print(
            f"[green]Decision {decision_id} approved: {audit.action}[/green]\n"
            f"[dim]Event emitted — daemon will execute on next cycle[/dim]"
        )


@daemon_app.command(name="reject")
def daemon_reject(
    decision_id: int = typer.Argument(..., help="Decision audit ID to reject"),
    reason: str = typer.Option("", help="Rejection reason"),
) -> None:
    """Reject a pending decision."""
    init_database()

    with get_db_session() as db:
        from src.data.models import DecisionAudit

        audit = db.query(DecisionAudit).get(decision_id)
        if not audit:
            console.print(f"[red]Decision {decision_id} not found[/red]")
            raise typer.Exit(1)

        if audit.human_decision:
            console.print(
                f"[yellow]Decision {decision_id} was already {audit.human_decision}[/yellow]"
            )
            return

        audit.human_decision = "rejected"
        audit.human_override = True
        audit.human_decided_at = datetime.utcnow()
        audit.escalation_reason = reason or audit.escalation_reason
        db.commit()

        console.print(f"[yellow]Decision {decision_id} rejected: {audit.action}[/yellow]")


@daemon_app.command(name="dashboard")
def daemon_dashboard(
    host: str = typer.Option("127.0.0.1", help="Host to bind to"),
    port: int = typer.Option(8080, help="Port to listen on"),
    config: Optional[str] = typer.Option(None, help="Path to phase5.yaml"),
) -> None:
    """Start the web dashboard."""
    try:
        import uvicorn
    except ImportError:
        console.print("[red]uvicorn not installed. Run: pip install uvicorn[/red]")
        raise typer.Exit(1)

    from src.agentic.config import load_phase5_config
    from src.agentic.dashboard_api import create_dashboard_app

    init_database()

    cfg = load_phase5_config(config)
    host = host or cfg.dashboard.host
    port = port or cfg.dashboard.port
    auth_token = cfg.dashboard.auth_token

    app = create_dashboard_app(auth_token=auth_token)

    console.print(f"[bold blue]Starting TAAD dashboard at http://{host}:{port}[/bold blue]")
    if not auth_token:
        console.print("[yellow]No auth token configured — dashboard is unauthenticated[/yellow]")

    uvicorn.run(app, host=host, port=port, log_level="info")


@daemon_app.command(name="override")
def daemon_override(
    decision_id: int = typer.Argument(..., help="Decision audit ID to override"),
    action: str = typer.Option("approve", help="approve or reject"),
) -> None:
    """Override a pending daemon decision (alias for approve/reject)."""
    if action == "approve":
        daemon_approve(decision_id)
    elif action == "reject":
        daemon_reject(decision_id)
    else:
        console.print("[red]Action must be 'approve' or 'reject'[/red]")
        raise typer.Exit(1)

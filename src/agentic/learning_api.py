"""Learning dashboard API endpoints and HTML page.

Provides REST endpoints for viewing learning results:
  GET /api/learning/summary     — overview stats
  GET /api/learning/patterns    — detected & validated patterns
  GET /api/learning/experiments — A/B experiments and their status
  GET /api/learning/hypotheses  — Claude-generated hypotheses
  GET /api/learning/history     — learning event timeline
  GET /api/learning/reflections — EOD reflection reports

Plus an HTML page at /learning with the terminal-dark UI style.
"""

from datetime import datetime, timedelta

from loguru import logger

try:
    from fastapi import APIRouter, Depends
    from fastapi.responses import HTMLResponse

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

from src.data.database import get_db_session
from src.data.models import (
    LearningHistory,
    Pattern,
    Trade,
)
from src.utils.timezone import utc_now


def create_learning_router(verify_token) -> "APIRouter":
    """Create the learning API router.

    Args:
        verify_token: Dependency callable for bearer token auth

    Returns:
        FastAPI APIRouter with learning endpoints
    """
    router = APIRouter(prefix="/api/learning", tags=["learning"])

    @router.get("/summary")
    def get_summary(days: int = 90, token: None = Depends(verify_token)):
        """Get learning system overview."""
        with get_db_session() as db:
            cutoff = utc_now() - timedelta(days=days)

            # Closed trades count
            from sqlalchemy import or_
            closed_trades = (
                db.query(Trade)
                .filter(Trade.exit_date.isnot(None))
                .filter(or_(Trade.lifecycle_status.is_(None), Trade.lifecycle_status != "stock_held"))
                .count()
            )

            # Patterns
            total_patterns = db.query(Pattern).count()
            active_patterns = (
                db.query(Pattern)
                .filter(Pattern.status == "active")
                .count()
            )

            # Learning events
            events = (
                db.query(LearningHistory)
                .filter(LearningHistory.event_date >= cutoff)
                .all()
            )

            weekly_analyses = len([e for e in events if e.event_type == "weekly_analysis"])
            hypotheses_count = len([e for e in events if e.event_type == "hypothesis_generated"])
            param_changes = len([e for e in events if e.event_type == "parameter_adjusted"])

            # Win rate
            from sqlalchemy import func as sa_func
            wins = (
                db.query(Trade)
                .filter(Trade.exit_date.isnot(None))
                .filter(or_(Trade.lifecycle_status.is_(None), Trade.lifecycle_status != "stock_held"))
                .filter(Trade.profit_loss > 0)
                .count()
            )
            win_rate = wins / closed_trades if closed_trades > 0 else 0.0

            return {
                "closed_trades": closed_trades,
                "min_trades_needed": 30,
                "learning_ready": closed_trades >= 30,
                "win_rate": round(win_rate, 4),
                "total_patterns": total_patterns,
                "active_patterns": active_patterns,
                "weekly_analyses_run": weekly_analyses,
                "hypotheses_generated": hypotheses_count,
                "parameter_changes": param_changes,
                "period_days": days,
            }

    @router.get("/patterns")
    def get_patterns(token: None = Depends(verify_token)):
        """Get all detected patterns."""
        with get_db_session() as db:
            patterns = (
                db.query(Pattern)
                .order_by(Pattern.confidence.desc())
                .all()
            )

            return [
                {
                    "id": p.id,
                    "pattern_type": p.pattern_type,
                    "pattern_name": p.pattern_name,
                    "pattern_value": getattr(p, "pattern_value", None),
                    "sample_size": p.sample_size,
                    "win_rate": round(p.win_rate, 4) if p.win_rate else None,
                    "avg_roi": round(p.avg_roi, 4) if p.avg_roi else None,
                    "confidence": round(p.confidence, 4) if p.confidence else None,
                    "p_value": round(p.p_value, 4) if p.p_value else None,
                    "status": p.status,
                    "market_regime": p.market_regime,
                    "date_detected": str(p.date_detected) if p.date_detected else None,
                }
                for p in patterns
            ]

    @router.get("/experiments")
    def get_experiments(token: None = Depends(verify_token)):
        """Get all A/B experiments."""
        from src.data.models import Experiment

        with get_db_session() as db:
            experiments = (
                db.query(Experiment)
                .order_by(Experiment.created_at.desc())
                .all()
            )

            return [
                {
                    "id": e.id,
                    "experiment_id": e.experiment_id,
                    "name": e.name,
                    "description": e.description,
                    "parameter_name": e.parameter_name,
                    "control_value": e.control_value,
                    "test_value": e.test_value,
                    "status": e.status,
                    "control_trades": e.control_trades,
                    "test_trades": e.test_trades,
                    "p_value": round(e.p_value, 4) if e.p_value else None,
                    "effect_size": round(e.effect_size, 4) if e.effect_size else None,
                    "decision": e.decision,
                    "start_date": str(e.start_date) if e.start_date else None,
                    "end_date": str(e.end_date) if e.end_date else None,
                }
                for e in experiments
            ]

    @router.get("/hypotheses")
    def get_hypotheses(days: int = 90, token: None = Depends(verify_token)):
        """Get Claude-generated hypotheses from learning history."""
        with get_db_session() as db:
            cutoff = utc_now() - timedelta(days=days)

            hypotheses = (
                db.query(LearningHistory)
                .filter(LearningHistory.event_type == "hypothesis_generated")
                .filter(LearningHistory.event_date >= cutoff)
                .order_by(LearningHistory.event_date.desc())
                .all()
            )

            return [
                {
                    "id": h.id,
                    "title": h.pattern_name,
                    "body": h.reasoning,
                    "confidence": round(h.confidence, 2) if h.confidence else None,
                    "date": str(h.event_date) if h.event_date else None,
                }
                for h in hypotheses
            ]

    @router.get("/history")
    def get_history(days: int = 90, limit: int = 100, token: None = Depends(verify_token)):
        """Get learning event timeline."""
        with get_db_session() as db:
            cutoff = utc_now() - timedelta(days=days)

            events = (
                db.query(LearningHistory)
                .filter(LearningHistory.event_date >= cutoff)
                .order_by(LearningHistory.event_date.desc())
                .limit(limit)
                .all()
            )

            return [
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "event_date": str(e.event_date) if e.event_date else None,
                    "pattern_name": e.pattern_name,
                    "confidence": round(e.confidence, 4) if e.confidence else None,
                    "sample_size": e.sample_size,
                    "parameter_changed": e.parameter_changed,
                    "old_value": e.old_value,
                    "new_value": e.new_value,
                    "reasoning": e.reasoning,
                }
                for e in events
            ]

    @router.get("/reflections")
    def get_reflections(limit: int = 30, token: None = Depends(verify_token)):
        """Get recent EOD reflections from working memory."""
        with get_db_session() as db:
            from src.agentic.working_memory import WorkingMemory

            memory = WorkingMemory(db)
            reflections = memory.reflection_reports or []

            # Return most recent first
            return list(reversed(reflections[-limit:]))

    return router


def get_learning_html() -> str:
    """Return the learning dashboard HTML page."""
    return _LEARNING_HTML


_LEARNING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TAAD Learning & Self-Improvement</title>
<style>
  :root {
    --bg: #0f1923; --bg2: #172a3a; --bg3: #1e3a50;
    --border: #2a4a6b; --text: #c8d6e5; --text-dim: #6b8299;
    --accent: #00d4ff; --green: #00e676; --yellow: #ffd600;
    --red: #ff5252; --orange: #ff9100; --purple: #b388ff;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; background: var(--bg); color: var(--text); font-size: 13px; }

  .header { background: var(--bg2); border-bottom: 1px solid var(--border); padding: 12px 24px; display: flex; align-items: center; justify-content: space-between; }
  .header h1 { font-size: 16px; color: var(--accent); font-weight: 600; }
  .header h1 span { color: var(--text-dim); font-weight: 400; }
  .back-link { color: var(--text-dim); text-decoration: none; font-size: 12px; border: 1px solid var(--border); padding: 4px 12px; border-radius: 4px; }
  .back-link:hover { border-color: var(--accent); color: var(--accent); }

  .container { max-width: 1100px; padding: 16px 24px; }

  .summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 20px; }
  .stat-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; }
  .stat-card .stat-value { font-size: 22px; font-weight: 700; color: var(--accent); }
  .stat-card .stat-label { font-size: 10px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px; margin-top: 4px; }
  .stat-card.ready .stat-value { color: var(--green); }
  .stat-card.warning .stat-value { color: var(--yellow); }

  .tabs { display: flex; gap: 2px; margin-bottom: 16px; background: var(--bg2); border-radius: 6px; padding: 3px; border: 1px solid var(--border); }
  .tab { padding: 8px 16px; border-radius: 4px; cursor: pointer; font-size: 12px; color: var(--text-dim); border: none; background: transparent; font-family: inherit; }
  .tab:hover { color: var(--text); }
  .tab.active { background: var(--bg3); color: var(--accent); font-weight: 600; }

  .tab-content { display: none; }
  .tab-content.active { display: block; }

  .card { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; margin-bottom: 12px; }
  .card-header { padding: 10px 16px; border-bottom: 1px solid var(--border); }
  .card-header h2 { font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-dim); font-weight: 600; }
  .card-body { padding: 16px; }

  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); color: var(--text-dim); font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; }
  td { padding: 8px 10px; border-bottom: 1px solid rgba(42,74,107,0.3); }
  tr:hover { background: rgba(0,212,255,0.03); }

  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: 600; }
  .badge-green { background: rgba(0,230,118,0.15); color: var(--green); }
  .badge-yellow { background: rgba(255,214,0,0.15); color: var(--yellow); }
  .badge-red { background: rgba(255,82,82,0.15); color: var(--red); }
  .badge-blue { background: rgba(0,212,255,0.15); color: var(--accent); }
  .badge-purple { background: rgba(179,136,255,0.15); color: var(--purple); }

  .hypothesis-card { background: var(--bg3); border: 1px solid var(--border); border-radius: 6px; padding: 14px; margin-bottom: 10px; }
  .hypothesis-card h3 { font-size: 13px; color: var(--accent); margin-bottom: 6px; }
  .hypothesis-card p { font-size: 12px; color: var(--text); line-height: 1.5; }
  .hypothesis-meta { font-size: 10px; color: var(--text-dim); margin-top: 8px; }

  .reflection-card { background: var(--bg3); border-left: 3px solid var(--accent); padding: 12px 16px; margin-bottom: 10px; border-radius: 0 6px 6px 0; }
  .reflection-card .date { font-size: 10px; color: var(--text-dim); margin-bottom: 6px; }
  .reflection-card .summary { font-size: 12px; line-height: 1.5; }

  .empty-state { text-align: center; padding: 40px; color: var(--text-dim); }
  .empty-state .icon { font-size: 36px; margin-bottom: 12px; }
  .empty-state p { font-size: 13px; }

  .timeline-event { display: flex; gap: 12px; padding: 8px 0; border-bottom: 1px solid rgba(42,74,107,0.3); }
  .timeline-event:last-child { border-bottom: none; }
  .timeline-date { font-size: 10px; color: var(--text-dim); min-width: 90px; }
  .timeline-type { font-size: 11px; font-weight: 600; min-width: 140px; }
  .timeline-detail { font-size: 12px; color: var(--text); flex: 1; }

  .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid var(--border); border-top: 2px solid var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>

<div class="header">
  <h1>TAAD <span>Learning & Self-Improvement</span></h1>
  <div style="display:flex;gap:8px;">
    <a href="/config" class="back-link">Settings</a>
    <a href="/" class="back-link">Dashboard</a>
  </div>
</div>

<div class="container">
  <div id="summary-area">
    <div style="padding:40px;text-align:center;color:var(--text-dim);">
      <span class="spinner"></span> Loading learning data...
    </div>
  </div>

  <div class="tabs">
    <button class="tab active" onclick="switchTab('patterns')">Patterns</button>
    <button class="tab" onclick="switchTab('hypotheses')">Hypotheses</button>
    <button class="tab" onclick="switchTab('experiments')">Experiments</button>
    <button class="tab" onclick="switchTab('reflections')">Reflections</button>
    <button class="tab" onclick="switchTab('history')">Timeline</button>
  </div>

  <div id="tab-patterns" class="tab-content active"></div>
  <div id="tab-hypotheses" class="tab-content"></div>
  <div id="tab-experiments" class="tab-content"></div>
  <div id="tab-reflections" class="tab-content"></div>
  <div id="tab-history" class="tab-content"></div>
</div>

<script>
function esc(s) { if (s == null) return ''; const d = document.createElement('div'); d.textContent = String(s); return d.innerHTML; }
function pct(v) { return v != null ? (v * 100).toFixed(1) + '%' : '-'; }
function badge(text, cls) { return '<span class="badge badge-' + cls + '">' + esc(text) + '</span>'; }

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById('tab-' + name).classList.add('active');
}

async function loadAll() {
  try {
    const [summary, patterns, hypotheses, experiments, reflections, history] = await Promise.all([
      fetch('/api/learning/summary').then(r => r.json()),
      fetch('/api/learning/patterns').then(r => r.json()),
      fetch('/api/learning/hypotheses').then(r => r.json()),
      fetch('/api/learning/experiments').then(r => r.json()),
      fetch('/api/learning/reflections').then(r => r.json()),
      fetch('/api/learning/history').then(r => r.json()),
    ]);

    renderSummary(summary);
    renderPatterns(patterns);
    renderHypotheses(hypotheses);
    renderExperiments(experiments);
    renderReflections(reflections);
    renderHistory(history);
  } catch(e) {
    document.getElementById('summary-area').innerHTML =
      '<div class="empty-state"><p style="color:var(--red);">Failed to load: ' + esc(e.message) + '</p></div>';
  }
}

function renderSummary(s) {
  const readyClass = s.learning_ready ? 'ready' : 'warning';
  const readyText = s.learning_ready ? 'Ready' : s.closed_trades + '/' + s.min_trades_needed;
  document.getElementById('summary-area').innerHTML = `
    <div class="summary-grid">
      <div class="stat-card"><div class="stat-value">${s.closed_trades}</div><div class="stat-label">Closed Trades</div></div>
      <div class="stat-card"><div class="stat-value">${pct(s.win_rate)}</div><div class="stat-label">Win Rate</div></div>
      <div class="stat-card ${readyClass}"><div class="stat-value">${esc(readyText)}</div><div class="stat-label">Learning Status</div></div>
      <div class="stat-card"><div class="stat-value">${s.active_patterns}</div><div class="stat-label">Active Patterns</div></div>
      <div class="stat-card"><div class="stat-value">${s.hypotheses_generated}</div><div class="stat-label">Hypotheses</div></div>
      <div class="stat-card"><div class="stat-value">${s.weekly_analyses_run}</div><div class="stat-label">Weekly Analyses</div></div>
      <div class="stat-card"><div class="stat-value">${s.parameter_changes}</div><div class="stat-label">Param Changes</div></div>
    </div>`;
}

function renderPatterns(patterns) {
  const el = document.getElementById('tab-patterns');
  if (!patterns.length) {
    el.innerHTML = '<div class="empty-state"><div class="icon">~</div><p>No patterns detected yet. The weekly learning cycle needs 30+ closed trades to start finding patterns across 23 dimensions.</p></div>';
    return;
  }

  let html = '<div class="card"><div class="card-header"><h2>Detected Patterns</h2></div><div class="card-body"><table>';
  html += '<tr><th>Pattern</th><th>Type</th><th>Samples</th><th>Win Rate</th><th>Avg ROI</th><th>Confidence</th><th>p-value</th><th>Status</th></tr>';
  for (const p of patterns) {
    const statusBadge = p.status === 'active' ? badge('Active', 'green') : badge(p.status, 'yellow');
    html += '<tr>';
    html += '<td style="font-weight:600;">' + esc(p.pattern_name) + '</td>';
    html += '<td>' + esc(p.pattern_type) + '</td>';
    html += '<td>' + (p.sample_size || '-') + '</td>';
    html += '<td>' + pct(p.win_rate) + '</td>';
    html += '<td>' + pct(p.avg_roi) + '</td>';
    html += '<td>' + pct(p.confidence) + '</td>';
    html += '<td>' + (p.p_value != null ? p.p_value.toFixed(4) : '-') + '</td>';
    html += '<td>' + statusBadge + '</td>';
    html += '</tr>';
  }
  html += '</table></div></div>';
  el.innerHTML = html;
}

function renderHypotheses(hypotheses) {
  const el = document.getElementById('tab-hypotheses');
  if (!hypotheses.length) {
    el.innerHTML = '<div class="empty-state"><div class="icon">?</div><p>No hypotheses generated yet. Claude will generate hypotheses during the weekly learning cycle (Friday 17:00 ET).</p></div>';
    return;
  }

  let html = '';
  for (const h of hypotheses) {
    const confBadge = h.confidence >= 0.7 ? badge('high', 'green') :
                      h.confidence >= 0.4 ? badge('medium', 'yellow') : badge('low', 'red');
    html += '<div class="hypothesis-card">';
    html += '<h3>' + esc(h.title) + ' ' + confBadge + '</h3>';
    html += '<p>' + esc(h.body) + '</p>';
    html += '<div class="hypothesis-meta">Generated: ' + esc(h.date) + '</div>';
    html += '</div>';
  }
  el.innerHTML = html;
}

function renderExperiments(experiments) {
  const el = document.getElementById('tab-experiments');
  if (!experiments.length) {
    el.innerHTML = '<div class="empty-state"><div class="icon">A/B</div><p>No experiments yet. Experiments are created from validated patterns to test parameter changes.</p></div>';
    return;
  }

  let html = '<div class="card"><div class="card-header"><h2>A/B Experiments</h2></div><div class="card-body"><table>';
  html += '<tr><th>Name</th><th>Parameter</th><th>Control</th><th>Test</th><th>Control Trades</th><th>Test Trades</th><th>p-value</th><th>Decision</th><th>Status</th></tr>';
  for (const e of experiments) {
    const statusBadge = e.status === 'active' ? badge('Active', 'blue') :
                        e.status === 'completed' ? badge('Complete', 'green') : badge(e.status, 'yellow');
    const decBadge = e.decision === 'ADOPT' ? badge('ADOPT', 'green') :
                     e.decision === 'REJECT' ? badge('REJECT', 'red') :
                     e.decision ? badge(e.decision, 'yellow') : '-';
    html += '<tr>';
    html += '<td style="font-weight:600;">' + esc(e.name) + '</td>';
    html += '<td>' + esc(e.parameter_name) + '</td>';
    html += '<td>' + esc(e.control_value) + '</td>';
    html += '<td>' + esc(e.test_value) + '</td>';
    html += '<td>' + (e.control_trades || 0) + '</td>';
    html += '<td>' + (e.test_trades || 0) + '</td>';
    html += '<td>' + (e.p_value != null ? e.p_value.toFixed(4) : '-') + '</td>';
    html += '<td>' + decBadge + '</td>';
    html += '<td>' + statusBadge + '</td>';
    html += '</tr>';
  }
  html += '</table></div></div>';
  el.innerHTML = html;
}

function renderReflections(reflections) {
  const el = document.getElementById('tab-reflections');
  if (!reflections.length) {
    el.innerHTML = '<div class="empty-state"><div class="icon">...</div><p>No reflections yet. EOD reflections run daily after market close (16:30 ET) and capture decision quality insights.</p></div>';
    return;
  }

  let html = '';
  for (const r of reflections) {
    html += '<div class="reflection-card">';
    html += '<div class="date">' + esc(r.date || r.timestamp || '') + ' | ' + (r.decisions_count || 0) + ' decisions, ' + (r.trades_count || 0) + ' trades</div>';
    html += '<div class="summary">' + esc(r.summary || JSON.stringify(r).substring(0, 500)) + '</div>';
    html += '</div>';
  }
  el.innerHTML = html;
}

function renderHistory(events) {
  const el = document.getElementById('tab-history');
  if (!events.length) {
    el.innerHTML = '<div class="empty-state"><div class="icon">_</div><p>No learning events yet. Events will appear here as the system detects patterns, runs experiments, and adjusts parameters.</p></div>';
    return;
  }

  let html = '<div class="card"><div class="card-header"><h2>Learning Timeline</h2></div><div class="card-body">';
  for (const e of events) {
    const typeBadge = e.event_type === 'pattern_detected' ? badge('Pattern', 'blue') :
                      e.event_type === 'hypothesis_generated' ? badge('Hypothesis', 'purple') :
                      e.event_type === 'parameter_adjusted' ? badge('Param Change', 'green') :
                      e.event_type === 'weekly_analysis' ? badge('Weekly', 'yellow') :
                      badge(e.event_type, 'blue');
    html += '<div class="timeline-event">';
    html += '<div class="timeline-date">' + esc((e.event_date || '').substring(0, 10)) + '</div>';
    html += '<div class="timeline-type">' + typeBadge + '</div>';
    html += '<div class="timeline-detail">';
    if (e.pattern_name) html += '<strong>' + esc(e.pattern_name) + '</strong> ';
    if (e.parameter_changed) html += esc(e.parameter_changed) + ': ' + esc(e.old_value) + ' → ' + esc(e.new_value) + ' ';
    if (e.reasoning) html += '<br><span style="color:var(--text-dim);">' + esc(e.reasoning).substring(0, 200) + '</span>';
    html += '</div>';
    html += '</div>';
  }
  html += '</div></div>';
  el.innerHTML = html;
}

loadAll();
</script>
</body>
</html>"""

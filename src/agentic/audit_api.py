"""Audit trail API endpoints and HTML page.

Provides REST endpoints for viewing a full trading day audit trail:
  GET /api/audit/dates    — available trading dates with decision counts
  GET /api/audit/day      — all decisions for a specific trading date

Plus an HTML page at /audit with date picker and timeline view.
"""

from datetime import date, datetime, timedelta, time
from typing import Optional

from loguru import logger

try:
    from fastapi import APIRouter, Depends, Query
    from fastapi.responses import HTMLResponse

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

from src.data.database import get_db_session
from src.data.models import ClaudeApiCost, DecisionAudit
from src.utils.timezone import utc_now

from sqlalchemy import func as sa_func, cast, Date


def create_audit_router(verify_token) -> "APIRouter":
    """Create the audit API router.

    Args:
        verify_token: Dependency callable for bearer token auth

    Returns:
        FastAPI APIRouter with audit endpoints
    """
    router = APIRouter(prefix="/api/audit", tags=["audit"])

    @router.get("/dates")
    def get_audit_dates(
        limit: int = 30,
        token: None = Depends(verify_token),
    ):
        """Get available dates with decision counts.

        Returns dates (descending) that have at least one decision,
        plus summary counts by action type.
        """
        with get_db_session() as db:
            date_rows = (
                db.query(
                    cast(DecisionAudit.timestamp, Date).label("day"),
                    sa_func.count(DecisionAudit.id).label("total"),
                )
                .group_by(cast(DecisionAudit.timestamp, Date))
                .order_by(cast(DecisionAudit.timestamp, Date).desc())
                .limit(limit)
                .all()
            )

            results = []
            for row in date_rows:
                day = row[0]
                total = row[1]
                # Get action breakdown for this date
                action_counts = (
                    db.query(
                        DecisionAudit.action,
                        sa_func.count(DecisionAudit.id),
                    )
                    .filter(cast(DecisionAudit.timestamp, Date) == day)
                    .group_by(DecisionAudit.action)
                    .all()
                )
                breakdown = {a: c for a, c in action_counts}

                # Get cost for this date
                day_cost = (
                    db.query(sa_func.sum(DecisionAudit.cost_usd))
                    .filter(cast(DecisionAudit.timestamp, Date) == day)
                    .scalar()
                ) or 0.0

                results.append({
                    "date": str(day),
                    "total_decisions": total,
                    "actions": breakdown,
                    "cost_usd": round(day_cost, 4),
                })

            return results

    @router.get("/day")
    def get_audit_day(
        date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format"),
        token: None = Depends(verify_token),
    ):
        """Get full audit trail for a specific trading day.

        Returns all decisions chronologically with full detail,
        plus summary statistics.
        """
        from src.config.exchange_profile import get_active_profile

        profile = get_active_profile()

        if date:
            try:
                target_date = datetime.strptime(date, "%Y-%m-%d").date()
            except ValueError:
                from fastapi import HTTPException
                raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
        else:
            target_date = datetime.now(profile.timezone).date()

        with get_db_session() as db:
            # Query all decisions for this date (by UTC timestamp date)
            decisions = (
                db.query(DecisionAudit)
                .filter(cast(DecisionAudit.timestamp, Date) == target_date)
                .order_by(DecisionAudit.timestamp.asc())
                .all()
            )

            # Build response
            decision_list = []
            total_cost = 0.0
            total_input_tokens = 0
            total_output_tokens = 0
            action_counts = {}
            event_counts = {}
            executed_count = 0
            guardrail_blocks = 0

            for d in decisions:
                # Count stats
                action_counts[d.action] = action_counts.get(d.action, 0) + 1
                event_counts[d.event_type] = event_counts.get(d.event_type, 0) + 1
                if d.cost_usd:
                    total_cost += d.cost_usd
                if d.input_tokens:
                    total_input_tokens += d.input_tokens
                if d.output_tokens:
                    total_output_tokens += d.output_tokens
                if d.executed:
                    executed_count += 1
                if d.action == "GUARDRAIL_BLOCKED":
                    guardrail_blocks += 1

                # Parse guardrail flags
                flags = d.guardrail_flags or []
                flag_summary = []
                for f in flags:
                    if isinstance(f, dict):
                        flag_summary.append({
                            "guard": f.get("guard_name", "unknown"),
                            "severity": f.get("severity", "unknown"),
                            "reason": f.get("reason", ""),
                            "passed": f.get("passed", True),
                        })

                decision_list.append({
                    "id": d.id,
                    "timestamp": str(d.timestamp),
                    "event_type": d.event_type,
                    "action": d.action,
                    "confidence": d.confidence,
                    "reasoning": d.reasoning,
                    "key_factors": d.key_factors or [],
                    "risks_considered": d.risks_considered or [],
                    "autonomy_level": d.autonomy_level,
                    "autonomy_approved": d.autonomy_approved,
                    "escalation_reason": d.escalation_reason,
                    "human_override": d.human_override,
                    "human_decision": d.human_decision,
                    "executed": d.executed,
                    "execution_result": d.execution_result,
                    "execution_error": d.execution_error,
                    "input_tokens": d.input_tokens,
                    "output_tokens": d.output_tokens,
                    "model_used": d.model_used,
                    "cost_usd": d.cost_usd,
                    "guardrail_flags": flag_summary,
                    "plan_id": d.plan_id,
                    "plan_assessment": d.plan_assessment,
                })

            return {
                "date": str(target_date),
                "summary": {
                    "total_decisions": len(decisions),
                    "executed_count": executed_count,
                    "guardrail_blocks": guardrail_blocks,
                    "action_breakdown": action_counts,
                    "event_breakdown": event_counts,
                    "total_cost_usd": round(total_cost, 4),
                    "total_input_tokens": total_input_tokens,
                    "total_output_tokens": total_output_tokens,
                },
                "decisions": decision_list,
            }

    return router


def get_audit_html() -> str:
    """Return the audit page HTML."""
    return _AUDIT_HTML


_AUDIT_HTML = """<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Audit Trail - TAAD</title>
<style>
  /* === Theme system === */
  [data-theme="light"] {
    --bg-primary: #ffffff; --bg-surface: #f8f9fa; --bg-hover: #e9ecef;
    --border: #dee2e6; --text-primary: #212529; --text-secondary: #6c757d;
    --accent: #0066cc; --accent-hover: #0052a3;
    --green: #198754; --yellow: #cc8800; --red: #dc3545; --orange: #e67700;
    --shadow: 0 1px 3px rgba(0,0,0,0.08);
  }
  [data-theme="dark"] {
    --bg-primary: #0d1117; --bg-surface: #161b22; --bg-hover: #1c2333;
    --border: #30363d; --text-primary: #e6edf3; --text-secondary: #7d8590;
    --accent: #58a6ff; --accent-hover: #79b8ff;
    --green: #3fb950; --yellow: #d29922; --red: #f85149; --orange: #d18616;
    --shadow: none;
  }

  :root {
    --font-ui: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    --font-mono: 'SF Mono', 'Fira Code', 'Consolas', monospace;
    --text-xs: 11px; --text-sm: 13px; --text-base: 14px;
    --text-lg: 16px; --text-xl: 20px;
    --space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px;
    --space-5: 20px; --space-6: 24px; --space-8: 32px;
  }

  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: var(--font-ui); background: var(--bg-primary); color: var(--text-primary); font-size: var(--text-base); line-height: 1.5; }

  /* === Header === */
  .header {
    background: var(--bg-surface); border-bottom: 1px solid var(--border);
    padding: var(--space-3) var(--space-6);
    display: flex; align-items: center; justify-content: space-between;
  }
  .header-left { display: flex; align-items: center; gap: var(--space-3); }
  .header-left h1 { font-size: var(--text-lg); font-weight: 600; }
  .header-right { display: flex; align-items: center; gap: var(--space-3); }

  .back-link {
    color: var(--text-secondary); text-decoration: none; font-size: var(--text-sm);
    border: 1px solid var(--border); padding: var(--space-1) var(--space-3);
    border-radius: 6px; transition: all 0.15s;
  }
  .back-link:hover { border-color: var(--accent); color: var(--accent); }

  .tz-btn {
    padding: var(--space-1) var(--space-2); font-size: var(--text-xs); min-width: 44px;
    background: var(--bg-hover); color: var(--text-secondary); border: 1px solid var(--border);
    border-radius: 6px; cursor: pointer; font-family: var(--font-mono); font-weight: 600;
    transition: all 0.15s;
  }
  .tz-btn:hover { border-color: var(--accent); color: var(--accent); }

  /* === Content === */
  .content { max-width: 1200px; margin: 0 auto; padding: var(--space-4) var(--space-6); }

  /* === Date picker bar === */
  .date-bar {
    display: flex; align-items: center; gap: var(--space-3); margin-bottom: var(--space-4);
    flex-wrap: wrap;
  }
  .date-bar label { font-size: var(--text-sm); color: var(--text-secondary); font-weight: 500; }
  .date-input {
    background: var(--bg-surface); color: var(--text-primary); border: 1px solid var(--border);
    padding: var(--space-2) var(--space-3); border-radius: 6px; font-family: var(--font-mono);
    font-size: var(--text-sm);
  }
  .date-input:focus { outline: none; border-color: var(--accent); }
  .date-nav-btn {
    background: var(--bg-surface); color: var(--text-secondary); border: 1px solid var(--border);
    padding: var(--space-1) var(--space-3); border-radius: 6px; cursor: pointer;
    font-size: var(--text-sm); transition: all 0.15s;
  }
  .date-nav-btn:hover { border-color: var(--accent); color: var(--accent); }
  .date-nav-btn:disabled { opacity: 0.4; cursor: not-allowed; }

  /* === Summary pills === */
  .summary-bar {
    display: flex; gap: var(--space-3); margin-bottom: var(--space-4);
    flex-wrap: wrap;
  }
  .pill {
    background: var(--bg-surface); border: 1px solid var(--border); border-radius: 8px;
    padding: var(--space-2) var(--space-4); display: flex; flex-direction: column;
    min-width: 120px;
  }
  .pill-label { font-size: var(--text-xs); text-transform: uppercase; letter-spacing: 1px; color: var(--text-secondary); }
  .pill-value { font-size: var(--text-lg); font-weight: 700; font-family: var(--font-mono); }
  .pill-value.green { color: var(--green); }
  .pill-value.yellow { color: var(--yellow); }
  .pill-value.red { color: var(--red); }

  /* === Cards === */
  .card {
    background: var(--bg-surface); border: 1px solid var(--border);
    border-radius: 12px; overflow: hidden; margin-bottom: var(--space-4);
    box-shadow: var(--shadow);
  }
  .card-header {
    padding: var(--space-3) var(--space-4); border-bottom: 1px solid var(--border);
    display: flex; align-items: center; justify-content: space-between;
  }
  .card-header h2 {
    font-size: var(--text-xs); text-transform: uppercase; letter-spacing: 1px;
    color: var(--text-secondary); font-weight: 600;
  }
  .card-body { padding: var(--space-4); }

  /* === Tags === */
  .tag {
    display: inline-block; padding: 2px 10px; border-radius: 6px;
    font-size: var(--text-xs); font-weight: 600;
  }
  .tag-exec { background: rgba(63, 185, 80, 0.12); color: var(--green); }
  .tag-monitor { background: rgba(88, 166, 255, 0.12); color: var(--accent); }
  .tag-pending { background: rgba(210, 153, 34, 0.12); color: var(--yellow); }
  .tag-review { background: rgba(230, 119, 0, 0.12); color: var(--orange); }
  .tag-yes { background: rgba(63, 185, 80, 0.12); color: var(--green); }
  .tag-no { background: rgba(248, 81, 73, 0.12); color: var(--red); }
  .tag-block { background: rgba(248, 81, 73, 0.12); color: var(--red); }
  .tag-warn { background: rgba(210, 153, 34, 0.12); color: var(--yellow); }

  /* === Timeline === */
  .timeline { position: relative; }
  .timeline::before {
    content: ''; position: absolute; left: 18px; top: 0; bottom: 0;
    width: 2px; background: var(--border);
  }
  .timeline-item {
    position: relative; padding-left: 48px; margin-bottom: var(--space-4);
    cursor: pointer; transition: all 0.15s;
  }
  .timeline-item:hover { background: var(--bg-hover); border-radius: 8px; margin-left: -8px; padding-left: 56px; }
  .timeline-dot {
    position: absolute; left: 12px; top: 6px; width: 14px; height: 14px;
    border-radius: 50%; border: 2px solid var(--border); background: var(--bg-primary);
  }
  .timeline-dot.exec { border-color: var(--green); background: var(--green); }
  .timeline-dot.monitor { border-color: var(--accent); background: var(--accent); }
  .timeline-dot.block { border-color: var(--red); background: var(--red); }
  .timeline-dot.review { border-color: var(--orange); background: var(--orange); }
  .timeline-dot.stage { border-color: var(--green); background: transparent; }

  .timeline-time {
    font-family: var(--font-mono); font-size: var(--text-xs); color: var(--text-secondary);
    margin-bottom: 2px;
  }
  .timeline-header { display: flex; align-items: center; gap: var(--space-2); margin-bottom: var(--space-1); flex-wrap: wrap; }
  .timeline-event { font-size: var(--text-xs); color: var(--text-secondary); }
  .timeline-reasoning {
    font-size: var(--text-sm); color: var(--text-secondary); line-height: 1.4;
    max-height: 60px; overflow: hidden; transition: max-height 0.3s;
  }
  .timeline-item.expanded .timeline-reasoning { max-height: 2000px; }
  .timeline-detail {
    display: none; margin-top: var(--space-2); padding: var(--space-3);
    background: var(--bg-primary); border: 1px solid var(--border); border-radius: 8px;
    font-size: var(--text-sm);
  }
  .timeline-item.expanded .timeline-detail { display: block; }

  .timeline-conf {
    display: inline-block; width: 50px; height: 5px;
    background: var(--bg-hover); border: 1px solid var(--border);
    border-radius: 3px; vertical-align: middle; margin-right: 4px; overflow: hidden;
  }
  .timeline-conf .fill { display: block; height: 100%; border-radius: 2px; }
  .conf-text { font-size: var(--text-xs); font-family: var(--font-mono); color: var(--text-secondary); }

  /* === Guardrail flags inline === */
  .flag-list { margin-top: var(--space-1); }
  .flag-item {
    font-size: var(--text-xs); padding: 2px 6px; border-radius: 4px;
    margin: 2px 0; display: inline-block;
  }
  .flag-item.block { background: rgba(248, 81, 73, 0.12); color: var(--red); }
  .flag-item.warning { background: rgba(210, 153, 34, 0.12); color: var(--yellow); }

  /* === Filter bar === */
  .filter-bar {
    display: flex; gap: var(--space-2); margin-bottom: var(--space-3); flex-wrap: wrap;
  }
  .filter-btn {
    background: var(--bg-surface); color: var(--text-secondary); border: 1px solid var(--border);
    padding: var(--space-1) var(--space-3); border-radius: 6px; cursor: pointer;
    font-size: var(--text-xs); font-weight: 500; transition: all 0.15s;
  }
  .filter-btn:hover { border-color: var(--accent); color: var(--accent); }
  .filter-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }

  /* === Empty state === */
  .empty { text-align: center; padding: 60px 20px; color: var(--text-secondary); }
  .empty-icon { font-size: 32px; margin-bottom: var(--space-2); }
  .loading { text-align: center; padding: 40px; color: var(--text-secondary); }
  .error { text-align: center; padding: 40px; color: var(--red); }

  /* === Responsive === */
  @media (max-width: 768px) {
    .content { padding: var(--space-2); }
    .summary-bar { gap: var(--space-2); }
    .pill { min-width: 90px; padding: var(--space-2); }
  }
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <h1>Audit Trail</h1>
  </div>
  <div class="header-right">
    <button class="tz-btn" id="tz-toggle" onclick="toggleTimezone()">ET</button>
    <a href="/" class="back-link">Dashboard</a>
  </div>
</div>

<div class="content">
  <!-- Date picker -->
  <div class="date-bar">
    <label>Trading Day:</label>
    <button class="date-nav-btn" onclick="navDate(-1)" title="Previous day">&larr;</button>
    <input type="date" class="date-input" id="date-picker">
    <button class="date-nav-btn" onclick="navDate(1)" title="Next day">&rarr;</button>
    <button class="date-nav-btn" onclick="goToday()">Today</button>
  </div>

  <!-- Summary pills -->
  <div class="summary-bar" id="summary-bar"></div>

  <!-- Filters -->
  <div class="filter-bar" id="filter-bar"></div>

  <!-- Timeline -->
  <div id="content">
    <div class="loading">Select a trading day to view the audit trail.</div>
  </div>
</div>

<script>
// Theme initialization
(function() {
  const stored = localStorage.getItem('taad_theme');
  document.documentElement.setAttribute('data-theme', stored || 'light');
})();

// Timezone support
const _TZ_CYCLE = ['UTC', 'ET', 'AEDT'];
let _tz = localStorage.getItem('taad_tz') || 'UTC';
if (!_TZ_CYCLE.includes(_tz)) _tz = 'UTC';
function initTzButton() {
  const btn = document.getElementById('tz-toggle');
  if (btn) btn.textContent = _tz;
}
function toggleTimezone() {
  const idx = _TZ_CYCLE.indexOf(_tz);
  _tz = _TZ_CYCLE[(idx + 1) % _TZ_CYCLE.length];
  localStorage.setItem('taad_tz', _tz);
  document.getElementById('tz-toggle').textContent = _tz;
  if (_lastData) renderDay(_lastData);
}
function fmtTime(ts) {
  if (!ts || ts === 'None') return '--';
  let cleaned = ts.replace(/\\s+[A-Z]{2,5}$/, '');
  let iso = cleaned.replace(' ', 'T');
  if (!iso.endsWith('Z') && !iso.includes('+')) iso += 'Z';
  const d = new Date(iso);
  if (isNaN(d)) return '--';
  if (_tz === 'UTC') return d.toISOString().substring(0, 19).replace('T', ' ');
  const tz = _tz === 'AEDT' ? 'Australia/Sydney' : 'America/New_York';
  return d.toLocaleString('en-US', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}
function fmtTimeShort(ts) {
  if (!ts || ts === 'None') return '--';
  let cleaned = ts.replace(/\\s+[A-Z]{2,5}$/, '');
  let iso = cleaned.replace(' ', 'T');
  if (!iso.endsWith('Z') && !iso.includes('+')) iso += 'Z';
  const d = new Date(iso);
  if (isNaN(d)) return '--';
  if (_tz === 'UTC') return d.toISOString().substring(11, 19);
  const tz = _tz === 'AEDT' ? 'Australia/Sydney' : 'America/New_York';
  return d.toLocaleString('en-US', { timeZone: tz, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

function actionTag(action) {
  const map = {
    'EXECUTE_TRADES': 'tag-exec', 'STAGE_CANDIDATES': 'tag-exec',
    'MONITOR_ONLY': 'tag-monitor', 'CLOSE_POSITION': 'tag-exec',
    'CLOSE_ALL_POSITIONS': 'tag-exec',
    'REQUEST_HUMAN_REVIEW': 'tag-review', 'GUARDRAIL_BLOCKED': 'tag-no',
  };
  return '<span class="tag ' + (map[action] || 'tag-pending') + '">' + action + '</span>';
}
function confBar(conf) {
  if (conf == null) return '--';
  const pct = Math.round(conf * 100);
  const color = pct >= 80 ? 'var(--green)' : pct >= 60 ? 'var(--yellow)' : 'var(--red)';
  return '<span class="timeline-conf"><span class="fill" style="width:' + pct + '%;background:' + color + '"></span></span><span class="conf-text">' + pct + '%</span>';
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
  t = t.replace(/STEP (\\d+)\\s*[-\\u2013:]\\s*/g, '<br><b>STEP $1 \\u2014 </b>');
  t = t.replace(/(OBSERVATION:|ASSESSMENT:|ACTION:|CONCLUSION:|TENSION:|RESOLUTION:)/g, '<br><b>$1</b>');
  return t.replace(/^(<br>)+/, '').trim();
}

// State
let _lastData = null;
let _activeFilter = 'ALL';

// Date picker
const picker = document.getElementById('date-picker');
const today = new Date();
picker.value = today.getFullYear() + '-' + String(today.getMonth()+1).padStart(2,'0') + '-' + String(today.getDate()).padStart(2,'0');
picker.addEventListener('change', () => loadDay(picker.value));

function navDate(delta) {
  const parts = picker.value.split('-');
  const d = new Date(+parts[0], +parts[1]-1, +parts[2]);
  d.setDate(d.getDate() + delta);
  picker.value = d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
  loadDay(picker.value);
}
function goToday() {
  const d = new Date();
  picker.value = d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
  loadDay(picker.value);
}

async function loadDay(dateStr) {
  const content = document.getElementById('content');
  content.innerHTML = '<div class="loading">Loading audit trail...</div>';
  document.getElementById('summary-bar').innerHTML = '';
  document.getElementById('filter-bar').innerHTML = '';
  try {
    const r = await fetch('/api/audit/day?date=' + dateStr);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    _lastData = data;
    _activeFilter = 'ALL';
    renderDay(data);
  } catch(e) {
    content.innerHTML = '<div class="error">Error loading audit: ' + esc(e.message) + '</div>';
  }
}

function renderDay(data) {
  const s = data.summary;
  const content = document.getElementById('content');

  // Summary pills
  const summaryBar = document.getElementById('summary-bar');
  summaryBar.innerHTML =
    '<div class="pill"><span class="pill-label">Decisions</span><span class="pill-value">' + s.total_decisions + '</span></div>' +
    '<div class="pill"><span class="pill-label">Executed</span><span class="pill-value green">' + s.executed_count + '</span></div>' +
    (s.guardrail_blocks > 0 ? '<div class="pill"><span class="pill-label">Blocked</span><span class="pill-value red">' + s.guardrail_blocks + '</span></div>' : '') +
    '<div class="pill"><span class="pill-label">API Cost</span><span class="pill-value">$' + s.total_cost_usd.toFixed(2) + '</span></div>' +
    '<div class="pill"><span class="pill-label">Tokens</span><span class="pill-value">' + (s.total_input_tokens + s.total_output_tokens).toLocaleString() + '</span></div>' +
    Object.entries(s.action_breakdown).map(function(kv) {
      return '<div class="pill"><span class="pill-label">' + kv[0].replace(/_/g, ' ') + '</span><span class="pill-value">' + kv[1] + '</span></div>';
    }).join('');

  // Filter buttons
  const actions = Object.keys(s.action_breakdown);
  const filterBar = document.getElementById('filter-bar');
  let filterHtml = '<button class="filter-btn' + (_activeFilter === 'ALL' ? ' active' : '') + '" onclick="setFilter(\'ALL\')">All (' + s.total_decisions + ')</button>';
  actions.forEach(function(a) {
    filterHtml += '<button class="filter-btn' + (_activeFilter === a ? ' active' : '') + '" onclick="setFilter(\'' + a + '\')">' + a.replace(/_/g, ' ') + ' (' + s.action_breakdown[a] + ')</button>';
  });
  filterBar.innerHTML = filterHtml;

  // Filter decisions
  let decisions = data.decisions;
  if (_activeFilter !== 'ALL') {
    decisions = decisions.filter(function(d) { return d.action === _activeFilter; });
  }

  if (decisions.length === 0) {
    content.innerHTML = '<div class="empty"><div class="empty-icon">&#128203;</div>No decisions recorded for ' + data.date + '</div>';
    return;
  }

  // Timeline
  let html = '<div class="card"><div class="card-header"><h2>Timeline (' + decisions.length + ' decisions)</h2></div><div class="card-body"><div class="timeline">';

  decisions.forEach(function(d, idx) {
    const dotClass = d.action === 'EXECUTE_TRADES' || d.action === 'CLOSE_POSITION' ? 'exec'
      : d.action === 'STAGE_CANDIDATES' ? 'stage'
      : d.action === 'GUARDRAIL_BLOCKED' ? 'block'
      : d.action === 'REQUEST_HUMAN_REVIEW' ? 'review'
      : 'monitor';

    const shortReasoning = d.reasoning ? (d.reasoning.length > 150 ? d.reasoning.substring(0, 150) + '...' : d.reasoning) : 'No reasoning';

    // Guardrail flags
    let flagsHtml = '';
    if (d.guardrail_flags && d.guardrail_flags.length > 0) {
      flagsHtml = '<div class="flag-list">';
      d.guardrail_flags.forEach(function(f) {
        const cls = f.severity === 'block' ? 'block' : 'warning';
        flagsHtml += '<span class="flag-item ' + cls + '">' + esc(f.guard) + ': ' + esc(f.reason).substring(0, 80) + '</span> ';
      });
      flagsHtml += '</div>';
    }

    // Execution result summary
    let execHtml = '';
    if (d.executed && d.execution_result) {
      const er = d.execution_result;
      if (typeof er === 'object') {
        const keys = Object.keys(er).slice(0, 3);
        execHtml = '<div style="margin-top:4px;font-size:var(--text-xs);color:var(--text-secondary)">Result: ' + keys.map(function(k) { return k + '=' + JSON.stringify(er[k]).substring(0, 30); }).join(', ') + '</div>';
      }
    }
    if (d.execution_error) {
      execHtml = '<div style="margin-top:4px;font-size:var(--text-xs);color:var(--red)">Error: ' + esc(d.execution_error).substring(0, 100) + '</div>';
    }

    html += '<div class="timeline-item" onclick="toggleExpand(this)" data-id="' + d.id + '">';
    html += '<div class="timeline-dot ' + dotClass + '"></div>';
    html += '<div class="timeline-time">' + fmtTimeShort(d.timestamp) + '</div>';
    html += '<div class="timeline-header">';
    html += actionTag(d.action) + ' ';
    html += '<span class="timeline-event">' + esc(d.event_type) + '</span> ';
    html += confBar(d.confidence) + ' ';
    if (d.autonomy_approved) html += '<span class="tag tag-yes" style="font-size:9px">AUTO</span> ';
    if (d.human_decision) html += '<span class="tag ' + (d.human_decision === 'approved' ? 'tag-yes' : 'tag-no') + '" style="font-size:9px">' + d.human_decision.toUpperCase() + '</span> ';
    if (d.executed) html += '<span class="tag tag-yes" style="font-size:9px">EXEC</span> ';
    html += '</div>';
    html += '<div class="timeline-reasoning">' + esc(shortReasoning) + '</div>';
    html += flagsHtml + execHtml;

    // Expandable detail
    html += '<div class="timeline-detail">';
    html += '<div style="margin-bottom:8px"><b>Full Reasoning</b></div>';
    html += '<div style="font-family:var(--font-mono);font-size:var(--text-xs);white-space:pre-wrap;line-height:1.5;background:var(--bg-hover);padding:8px;border-radius:6px;margin-bottom:8px">' + fmtReasoning(d.reasoning) + '</div>';

    if (d.key_factors && d.key_factors.length) {
      html += '<div style="margin-bottom:4px"><b>Key Factors</b></div><ul style="padding-left:18px;margin-bottom:8px;font-size:var(--text-sm)">';
      d.key_factors.forEach(function(f) { html += '<li>' + esc(f) + '</li>'; });
      html += '</ul>';
    }
    if (d.risks_considered && d.risks_considered.length) {
      html += '<div style="margin-bottom:4px"><b>Risks</b></div><ul style="padding-left:18px;margin-bottom:8px;font-size:var(--text-sm)">';
      d.risks_considered.forEach(function(f) { html += '<li>' + esc(f) + '</li>'; });
      html += '</ul>';
    }
    if (d.escalation_reason) {
      html += '<div style="margin-bottom:4px"><b>Escalation</b></div><div style="font-size:var(--text-sm);margin-bottom:8px">' + esc(d.escalation_reason) + '</div>';
    }
    if (d.plan_assessment) {
      html += '<div style="margin-bottom:4px"><b>Plan Assessment</b></div><div style="font-size:var(--text-sm);margin-bottom:8px">' + esc(d.plan_assessment) + '</div>';
    }
    if (d.cost_usd) {
      html += '<div style="font-size:var(--text-xs);color:var(--text-secondary)">Cost: $' + d.cost_usd.toFixed(4) + ' | Tokens: ' + ((d.input_tokens||0) + (d.output_tokens||0)).toLocaleString() + ' | Model: ' + esc(d.model_used) + '</div>';
    }
    html += '<div style="margin-top:8px"><a href="/decision/' + d.id + '" style="color:var(--accent);font-size:var(--text-xs)">View full detail &rarr;</a></div>';
    html += '</div>';  // timeline-detail
    html += '</div>';  // timeline-item
  });

  html += '</div></div></div>';  // timeline, card-body, card
  content.innerHTML = html;

  // Re-apply auth token to injected links
  const token = sessionStorage.getItem('taad_token');
  if (token) {
    document.querySelectorAll('.timeline-detail a[href^="/"]').forEach(function(a) {
      const u = new URL(a.href);
      u.searchParams.set('token', token);
      a.href = u.toString();
    });
  }
}

function toggleExpand(el) {
  el.classList.toggle('expanded');
}

function setFilter(action) {
  _activeFilter = action;
  if (_lastData) renderDay(_lastData);
}

// Init
initTzButton();
loadDay(picker.value);
</script>
</body>
</html>"""

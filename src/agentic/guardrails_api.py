"""FastAPI endpoints and HTML page for guardrail metrics review.

Provides two REST endpoints:
- /api/guardrail-metrics/today  — live breakdown of today's guardrail activity
- /api/guardrail-metrics/history — historical daily_audit + calibration + entropy

Uses the /api/guardrail-metrics prefix to avoid collision with the existing
/api/guardrails endpoint on the main dashboard app.
"""

from datetime import date, timedelta

from loguru import logger

try:
    from fastapi import APIRouter, Depends, Query
    from pydantic import BaseModel

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

from src.data.database import get_db_session
from src.data.models import DecisionAudit, GuardrailMetric


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_guardrails_router(verify_token) -> "APIRouter":
    """Create the guardrail metrics API router.

    Args:
        verify_token: FastAPI dependency for bearer token auth

    Returns:
        APIRouter with guardrail metrics endpoints
    """
    if not FASTAPI_AVAILABLE:
        raise ImportError("FastAPI not installed")

    router = APIRouter(prefix="/api/guardrail-metrics", tags=["guardrail-metrics"])

    @router.get("/today")
    def guardrail_metrics_today(token: None = Depends(verify_token)):
        """Full breakdown of today's guardrail activity.

        Returns decisions, blocks, warnings, guard breakdown, and findings
        computed live from DecisionAudit rows (not from persisted metrics).
        Uses a 24-hour window to avoid UTC vs local date boundary mismatches.
        """
        from datetime import datetime as dt, timezone

        with get_db_session() as db:
            # 24-hour window avoids UTC vs local date mismatch
            cutoff = dt.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)

            decisions_today = (
                db.query(DecisionAudit)
                .filter(DecisionAudit.timestamp >= cutoff)
                .order_by(DecisionAudit.timestamp.desc())
                .limit(100)
                .all()
            )

            total_decisions = len(decisions_today)
            blocks = 0
            warnings = 0
            guard_breakdown: dict[str, dict[str, int]] = {}
            recent_findings: list[dict] = []

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

                        if guard_name not in guard_breakdown:
                            guard_breakdown[guard_name] = {"blocks": 0, "warnings": 0, "total": 0}
                        if severity == "block":
                            guard_breakdown[guard_name]["blocks"] += 1
                        elif severity == "warning":
                            guard_breakdown[guard_name]["warnings"] += 1
                        guard_breakdown[guard_name]["total"] += 1

                        recent_findings.append({
                            "decision_id": d.id,
                            "guard_name": guard_name,
                            "severity": severity,
                            "action": d.action,
                            "reason": flag.get("reason", ""),
                            "timestamp": str(d.timestamp),
                        })

            return {
                "date": str(dt.now(timezone.utc).date()),
                "total_decisions": total_decisions,
                "guardrail_blocks": blocks,
                "guardrail_warnings": warnings,
                "guard_breakdown": guard_breakdown,
                "recent_findings": sorted(
                    recent_findings, key=lambda x: x["timestamp"], reverse=True
                )[:20],
            }

    @router.get("/history")
    def guardrail_metrics_history(
        days: int = Query(default=30, ge=1, le=365),
        token: None = Depends(verify_token),
    ):
        """Historical guardrail metrics from the GuardrailMetric table.

        Returns daily_audit, calibration, and entropy rows grouped by date.
        """
        with get_db_session() as db:
            cutoff = date.today() - timedelta(days=days)

            rows = (
                db.query(GuardrailMetric)
                .filter(GuardrailMetric.metric_date >= cutoff)
                .order_by(GuardrailMetric.metric_date.desc())
                .all()
            )

            # Group by date
            by_date: dict[str, dict] = {}
            for row in rows:
                d = str(row.metric_date)
                if d not in by_date:
                    by_date[d] = {"date": d, "daily_audit": None, "calibration": [], "entropy": None}

                if row.metric_type == "daily_audit":
                    by_date[d]["daily_audit"] = {
                        "total_decisions": row.total_decisions,
                        "guardrail_blocks": row.guardrail_blocks,
                        "guardrail_warnings": row.guardrail_warnings,
                        "symbols_flagged": row.symbols_flagged,
                        "numbers_flagged": row.numbers_flagged,
                        "calibration_error": row.calibration_error,
                        "sample_size": row.sample_size,
                    }
                elif row.metric_type == "calibration":
                    by_date[d]["calibration"].append({
                        "bucket": row.confidence_bucket,
                        "predicted": row.predicted_accuracy,
                        "actual": row.actual_accuracy,
                        "error": row.calibration_error,
                        "n": row.sample_size,
                    })
                elif row.metric_type == "entropy":
                    by_date[d]["entropy"] = {
                        "avg_reasoning_length": row.avg_reasoning_length,
                        "unique_key_factors_ratio": row.unique_key_factors_ratio,
                        "reasoning_similarity_score": row.reasoning_similarity_score,
                    }

            return {
                "days_requested": days,
                "history": list(by_date.values()),
            }

    return router


# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------


def get_guardrails_html() -> str:
    """Return the guardrails review HTML page."""
    return _GUARDRAILS_HTML


_GUARDRAILS_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TAAD - Guardrails</title>
<style>
  :root {
    --bg: #0d1117; --bg2: #161b22; --bg3: #21262d;
    --border: #30363d; --text: #e6edf3; --text-dim: #8b949e;
    --accent: #58a6ff; --green: #3fb950; --yellow: #d29922;
    --red: #f85149; --orange: #db6d28;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: var(--bg); color: var(--text); font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace; font-size: 13px; }

  .header { display: flex; justify-content: space-between; align-items: center; padding: 16px 24px; border-bottom: 1px solid var(--border); background: var(--bg2); }
  .header h1 { font-size: 16px; font-weight: 600; }
  .header h1 span { color: var(--text-dim); font-weight: 400; font-size: 12px; margin-left: 8px; }

  .btn { padding: 4px 12px; border-radius: 4px; border: 1px solid var(--border); background: var(--bg3); color: var(--text); cursor: pointer; font-size: 12px; font-family: inherit; }
  .btn:hover { border-color: var(--accent); }

  .content { padding: 20px 24px; max-width: 1400px; margin: 0 auto; }

  /* Summary cards */
  .summary-row { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-bottom: 20px; }
  .card { background: var(--bg2); border: 1px solid var(--border); border-radius: 6px; padding: 16px; }
  .card .label { color: var(--text-dim); font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }
  .card .value { font-size: 28px; font-weight: 700; margin-top: 4px; }
  .card .value.green { color: var(--green); }
  .card .value.yellow { color: var(--yellow); }
  .card .value.red { color: var(--red); }

  /* Tables */
  .section { margin-bottom: 24px; }
  .section h2 { font-size: 14px; margin-bottom: 10px; color: var(--text-dim); }
  table { width: 100%; border-collapse: collapse; background: var(--bg2); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
  th { text-align: left; padding: 8px 12px; background: var(--bg3); color: var(--text-dim); font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid var(--border); }
  td { padding: 8px 12px; border-bottom: 1px solid var(--border); font-size: 12px; }
  tr:last-child td { border-bottom: none; }

  /* Severity tags */
  .tag { display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 11px; font-weight: 600; }
  .tag-block { background: rgba(248,81,73,0.15); color: var(--red); }
  .tag-warning { background: rgba(210,153,34,0.15); color: var(--yellow); }
  .tag-info { background: rgba(88,166,255,0.15); color: var(--accent); }

  /* Inline bar charts */
  .bar-container { display: inline-block; width: 60px; height: 12px; background: var(--bg3); border-radius: 2px; vertical-align: middle; margin-left: 6px; }
  .bar-fill { height: 100%; border-radius: 2px; }

  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }

  .status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
  .loading { text-align: center; padding: 40px; color: var(--text-dim); }
  .empty { color: var(--text-dim); padding: 16px 12px; text-align: center; }

  @media (max-width: 900px) {
    .summary-row { grid-template-columns: repeat(2, 1fr); }
  }
</style>
</head>
<body>

<div class="header">
  <h1>Guardrails <span>Confidence Calibration &amp; Monitoring</span></h1>
  <div style="display:flex;align-items:center;gap:12px;">
    <a href="/" class="btn" style="text-decoration:none;">Dashboard</a>
    <a href="/scanner" class="btn" style="text-decoration:none;">Option Scanner</a>
    <a href="/config" class="btn" style="text-decoration:none;">Settings</a>
  </div>
</div>

<div class="content">
  <!-- Summary Cards -->
  <div class="summary-row" id="summary-cards">
    <div class="card"><div class="label">Decisions Today</div><div class="value" id="s-decisions">-</div></div>
    <div class="card"><div class="label">Guardrail Blocks</div><div class="value red" id="s-blocks">-</div></div>
    <div class="card"><div class="label">Guardrail Warnings</div><div class="value yellow" id="s-warnings">-</div></div>
    <div class="card"><div class="label">Calibration Error</div><div class="value" id="s-cal-error">-</div></div>
    <div class="card"><div class="label">Cal. Samples</div><div class="value" id="s-cal-samples">-</div></div>
  </div>

  <!-- Guard Breakdown -->
  <div class="section">
    <h2>Guard Breakdown</h2>
    <table>
      <thead><tr><th>Guard Name</th><th>Blocks</th><th>Warnings</th><th>Total</th></tr></thead>
      <tbody id="guard-breakdown"><tr><td colspan="4" class="empty">No guardrail flags today</td></tr></tbody>
    </table>
  </div>

  <!-- Recent Findings -->
  <div class="section">
    <h2>Recent Findings</h2>
    <table>
      <thead><tr><th>Time</th><th>Guard</th><th>Severity</th><th>Action</th><th>Reason</th><th>Decision</th></tr></thead>
      <tbody id="recent-findings"><tr><td colspan="6" class="empty">No findings today</td></tr></tbody>
    </table>
  </div>

  <!-- Historical Trends -->
  <div class="section">
    <h2>Historical Trends (Last 14 Days)</h2>
    <table>
      <thead><tr><th>Date</th><th>Decisions</th><th>Blocks</th><th>Warnings</th><th>Cal Error</th><th>Similarity</th><th>Diversity</th></tr></thead>
      <tbody id="history-table"><tr><td colspan="7" class="empty">Loading...</td></tr></tbody>
    </table>
  </div>

  <!-- Calibration Buckets -->
  <div class="section">
    <h2>Calibration Buckets (Historical)</h2>
    <table>
      <thead><tr><th>Date</th><th>Bucket</th><th>Predicted</th><th>Actual</th><th>Error</th><th>N</th></tr></thead>
      <tbody id="cal-buckets"><tr><td colspan="6" class="empty">Loading...</td></tr></tbody>
    </table>
  </div>
</div>

<script>
const TOKEN = localStorage.getItem('taad_token') || '';
const HEADERS = TOKEN ? {'Authorization': 'Bearer ' + TOKEN} : {};

async function fetchJSON(url) {
  try {
    const r = await fetch(url, {headers: HEADERS});
    if (!r.ok) return null;
    return await r.json();
  } catch (e) { return null; }
}

function fmtTime(ts) {
  if (!ts) return '-';
  const d = new Date(ts + (ts.includes('Z') || ts.includes('+') ? '' : 'Z'));
  return d.toLocaleTimeString('en-US', {hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false});
}

function severityTag(sev) {
  const cls = sev === 'block' ? 'tag-block' : sev === 'warning' ? 'tag-warning' : 'tag-info';
  return `<span class="tag ${cls}">${sev}</span>`;
}

function barHTML(value, max, color) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return `<span class="bar-container"><span class="bar-fill" style="width:${pct}%;background:${color};"></span></span>`;
}

async function refreshToday() {
  const data = await fetchJSON('/api/guardrail-metrics/today');
  if (!data) return;

  document.getElementById('s-decisions').textContent = data.total_decisions;
  document.getElementById('s-blocks').textContent = data.guardrail_blocks;
  document.getElementById('s-warnings').textContent = data.guardrail_warnings;

  // Cal error & samples come from history (persisted), show '-' for live
  // We'll update these from history fetch below

  // Guard breakdown
  const gb = data.guard_breakdown || {};
  const guards = Object.entries(gb).sort((a, b) => b[1].total - a[1].total);
  const gbBody = document.getElementById('guard-breakdown');
  if (guards.length === 0) {
    gbBody.innerHTML = '<tr><td colspan="4" class="empty">No guardrail flags today</td></tr>';
  } else {
    gbBody.innerHTML = guards.map(([name, counts]) =>
      `<tr><td>${name}</td><td style="color:var(--red)">${counts.blocks}</td><td style="color:var(--yellow)">${counts.warnings}</td><td>${counts.total}</td></tr>`
    ).join('');
  }

  // Recent findings
  const findings = data.recent_findings || [];
  const rfBody = document.getElementById('recent-findings');
  if (findings.length === 0) {
    rfBody.innerHTML = '<tr><td colspan="6" class="empty">No findings today</td></tr>';
  } else {
    rfBody.innerHTML = findings.map(f =>
      `<tr>
        <td>${fmtTime(f.timestamp)}</td>
        <td>${f.guard_name}</td>
        <td>${severityTag(f.severity)}</td>
        <td>${f.action}</td>
        <td style="max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${f.reason.replace(/"/g, '&quot;')}">${f.reason}</td>
        <td><a href="/decision/${f.decision_id}">#${f.decision_id}</a></td>
      </tr>`
    ).join('');
  }
}

async function refreshHistory() {
  const data = await fetchJSON('/api/guardrail-metrics/history?days=14');
  if (!data) return;

  const history = data.history || [];
  const htBody = document.getElementById('history-table');
  const cbBody = document.getElementById('cal-buckets');

  // Find max decisions for bar chart scaling
  const maxDec = Math.max(1, ...history.map(h => (h.daily_audit || {}).total_decisions || 0));

  if (history.length === 0) {
    htBody.innerHTML = '<tr><td colspan="7" class="empty">No historical data yet</td></tr>';
    cbBody.innerHTML = '<tr><td colspan="6" class="empty">No calibration data yet</td></tr>';
    return;
  }

  // Update summary cards with today's persisted data
  const todayData = history.find(h => h.date === new Date().toISOString().slice(0, 10));
  if (todayData && todayData.daily_audit) {
    const ce = todayData.daily_audit.calibration_error;
    const el = document.getElementById('s-cal-error');
    el.textContent = ce != null ? ce.toFixed(3) : '-';
    el.className = 'value' + (ce > 0.15 ? ' red' : ce > 0.10 ? ' yellow' : ' green');
    document.getElementById('s-cal-samples').textContent = todayData.daily_audit.sample_size || 0;
  }

  // Historical trends table
  htBody.innerHTML = history.map(h => {
    const a = h.daily_audit || {};
    const e = h.entropy || {};
    const dec = a.total_decisions || 0;
    const blk = a.guardrail_blocks || 0;
    const wrn = a.guardrail_warnings || 0;
    const calE = a.calibration_error;
    const sim = e.reasoning_similarity_score;
    const div = e.unique_key_factors_ratio;
    return `<tr>
      <td>${h.date}</td>
      <td>${dec} ${barHTML(dec, maxDec, 'var(--accent)')}</td>
      <td style="color:${blk > 0 ? 'var(--red)' : 'var(--text-dim)'}">${blk}</td>
      <td style="color:${wrn > 0 ? 'var(--yellow)' : 'var(--text-dim)'}">${wrn}</td>
      <td>${calE != null ? calE.toFixed(3) : '-'}</td>
      <td>${sim != null ? sim.toFixed(3) : '-'}</td>
      <td>${div != null ? div.toFixed(3) : '-'}</td>
    </tr>`;
  }).join('');

  // Calibration buckets
  const allBuckets = [];
  for (const h of history) {
    for (const b of (h.calibration || [])) {
      allBuckets.push({date: h.date, ...b});
    }
  }
  if (allBuckets.length === 0) {
    cbBody.innerHTML = '<tr><td colspan="6" class="empty">No calibration data yet</td></tr>';
  } else {
    cbBody.innerHTML = allBuckets.map(b =>
      `<tr>
        <td>${b.date}</td>
        <td>${b.bucket}</td>
        <td>${b.predicted != null ? b.predicted.toFixed(3) : '-'}</td>
        <td>${b.actual != null ? b.actual.toFixed(3) : '-'}</td>
        <td style="color:${(b.error || 0) > 0.15 ? 'var(--red)' : 'var(--text-dim)'}">${b.error != null ? b.error.toFixed(3) : '-'}</td>
        <td>${b.n || 0}</td>
      </tr>`
    ).join('');
  }
}

async function refresh() {
  await Promise.all([refreshToday(), refreshHistory()]);
}

refresh();
setInterval(refresh, 8000);
</script>

</body>
</html>
"""

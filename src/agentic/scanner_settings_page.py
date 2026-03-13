"""Scanner Settings dashboard page.

Self-contained HTML page for viewing and editing scanner configuration.
Consumes existing API endpoints from scanner_api.py:
  GET  /api/scanner/settings  — read current settings
  POST /api/scanner/settings  — save updated settings
  GET  /api/scanner/budget    — live margin budget info

No new API routes are needed — only the HTML page function is exported.
"""


def get_scanner_settings_html() -> str:
    """Return the scanner settings HTML page."""
    return _SCANNER_SETTINGS_HTML


_SCANNER_SETTINGS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TAAD Scan Config</title>
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
  .header h1 span { color: var(--text-dim); font-weight: 400; }
  .back-link { color: var(--text-dim); text-decoration: none; font-size: 12px; border: 1px solid var(--border); padding: 4px 12px; border-radius: 4px; }
  .back-link:hover { border-color: var(--accent); color: var(--accent); }

  .container { max-width: 900px; padding: 16px 24px; }

  .card { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; margin-bottom: 16px; }
  .card-header { padding: 10px 16px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; cursor: pointer; }
  .card-header:hover { background: rgba(0, 212, 255, 0.03); }
  .card-header h2 { font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-dim); font-weight: 600; }
  .card-header .toggle { color: var(--text-dim); font-size: 11px; }
  .card-body { padding: 16px; }
  .card-body.collapsed { display: none; }

  .field-row { display: grid; grid-template-columns: 220px 1fr; gap: 12px; align-items: center; padding: 6px 0; border-bottom: 1px solid rgba(42, 74, 107, 0.3); }
  .field-row:last-child { border-bottom: none; }
  .field-label { font-size: 12px; color: var(--text-dim); }
  .field-label .field-key { color: var(--text); font-weight: 600; font-size: 11px; display: block; }
  .field-label .field-desc { font-size: 10px; margin-top: 2px; }

  input[type="text"], input[type="number"] {
    background: var(--bg); border: 1px solid var(--border); color: var(--text);
    padding: 6px 10px; border-radius: 4px; font-family: inherit; font-size: 12px; width: 100%; max-width: 300px;
  }
  input:focus { border-color: var(--accent); outline: none; }

  input[type="checkbox"] { cursor: pointer; accent-color: var(--accent); width: 16px; height: 16px; }
  .checkbox-wrap { display: flex; align-items: center; gap: 8px; }
  .checkbox-wrap label { font-size: 12px; color: var(--text); cursor: pointer; }

  .btn { border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 12px; font-weight: 600; transition: all 0.15s; }
  .btn-green { background: rgba(0, 230, 118, 0.15); color: var(--green); border: 1px solid rgba(0, 230, 118, 0.3); }
  .btn-green:hover { background: rgba(0, 230, 118, 0.3); }
  .btn-green:disabled { opacity: 0.4; cursor: not-allowed; }

  .save-bar { position: sticky; bottom: 0; background: var(--bg2); border-top: 1px solid var(--border); padding: 12px 24px; display: flex; align-items: center; justify-content: space-between; z-index: 10; }
  .save-bar .save-status { font-size: 12px; color: var(--text-dim); }

  .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid var(--border); border-top: 2px solid var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite; vertical-align: middle; margin-right: 6px; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .toast { position: fixed; bottom: 20px; right: 20px; background: var(--bg3); border: 1px solid var(--green); color: var(--green); padding: 12px 20px; border-radius: 6px; font-size: 13px; opacity: 0; transition: opacity 0.3s; pointer-events: none; z-index: 100; }
  .toast.show { opacity: 1; }
  .toast.error { border-color: var(--red); color: var(--red); }

  .section-desc { font-size: 11px; color: var(--text-dim); padding: 0 0 12px 0; border-bottom: 1px solid rgba(42, 74, 107, 0.3); margin-bottom: 8px; }

  .budget-widget { background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 14px 16px; margin-bottom: 16px; }
  .budget-widget h3 { font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-dim); margin-bottom: 10px; }
  .budget-row { display: flex; justify-content: space-between; padding: 4px 0; font-size: 12px; }
  .budget-row .val { color: var(--accent); font-weight: 600; }
  .budget-row .val.green { color: var(--green); }
  .budget-row .val.red { color: var(--red); }
  .budget-bar { height: 6px; background: var(--bg2); border-radius: 3px; margin-top: 8px; overflow: hidden; }
  .budget-bar-fill { height: 100%; background: var(--accent); border-radius: 3px; transition: width 0.3s; }

  .validation-error { color: var(--red); font-size: 11px; margin-top: 4px; }
</style>
</head>
<body>

<div class="header">
  <h1>TAAD <span>Scan Config</span></h1>
  <a href="/" class="back-link">Back to Dashboard</a>
</div>

<div class="container" id="settings-container">
  <div style="padding:40px;text-align:center;color:var(--text-dim);">
    <span class="spinner"></span> Loading scanner settings...
  </div>
</div>

<div class="save-bar">
  <span class="save-status" id="save-status">Loaded from config/scanner_settings.yaml</span>
  <div style="display:flex;gap:8px;align-items:center;">
    <span id="save-feedback" style="font-size:12px;"></span>
    <button class="btn btn-green" id="btn-save" onclick="saveSettings()">Save Settings</button>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
let _settings = {};
let _dirty = false;

function esc(s) { if (s == null) return ''; const d = document.createElement('div'); d.textContent = String(s); return d.innerHTML; }

function showToast(msg, isError) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show' + (isError ? ' error' : '');
  setTimeout(() => t.className = 'toast', 3000);
}

function markDirty() {
  _dirty = true;
  document.getElementById('save-status').textContent = 'Unsaved changes';
  document.getElementById('save-status').style.color = 'var(--yellow)';
}

// ---------------------------------------------------------------------------
// Section definitions
// ---------------------------------------------------------------------------
const SECTIONS = [
  {
    key: 'scanner',
    label: 'Scanner',
    desc: 'IBKR scanner parameters. num_rows controls how many symbols the scanner returns (more = larger candidate pool, slower scan).',
    fields: [
      { key: 'num_rows', label: 'Num Rows', desc: 'Number of symbols from IBKR scanner (10-500)', type: 'number', min: 10, max: 500, step: 10 },
    ],
  },
  {
    key: 'filters',
    label: 'Filters',
    desc: 'Option filter criteria for strike selection. Controls which puts qualify for the portfolio.',
    fields: [
      { key: 'delta_min', label: 'Delta Min', desc: 'Minimum put delta (0.0-1.0)', type: 'number', min: 0, max: 1, step: 0.01 },
      { key: 'delta_max', label: 'Delta Max', desc: 'Maximum put delta (0.0-1.0)', type: 'number', min: 0, max: 1, step: 0.01 },
      { key: 'delta_target', label: 'Delta Target', desc: 'Preferred delta for scoring', type: 'number', min: 0, max: 1, step: 0.005 },
      { key: 'min_premium', label: 'Min Premium', desc: 'Minimum bid premium ($)', type: 'number', min: 0, step: 0.05 },
      { key: 'min_otm_pct', label: 'Min OTM %', desc: 'Minimum out-of-the-money percentage', type: 'number', min: 0, max: 1, step: 0.01 },
      { key: 'max_dte', label: 'Max DTE', desc: 'Maximum days to expiration', type: 'number', min: 1, max: 60, step: 1 },
      { key: 'dte_prefer_shortest', label: 'Prefer Shortest DTE', desc: 'Prefer nearer expirations', type: 'checkbox' },
    ],
  },
  {
    key: 'ranking',
    label: 'Ranking Weights',
    desc: 'Weights for composite scoring (must sum to 100). Controls how candidates are ranked before portfolio selection.',
    fields: [
      { key: 'safety', label: 'Safety', desc: 'Weight for OTM/delta safety (0-100)', type: 'number', min: 0, max: 100, step: 5 },
      { key: 'liquidity', label: 'Liquidity', desc: 'Weight for OI/spread quality (0-100)', type: 'number', min: 0, max: 100, step: 5 },
      { key: 'ai_score', label: 'AI Score', desc: 'Weight for Claude recommendation (0-100)', type: 'number', min: 0, max: 100, step: 5 },
      { key: 'efficiency', label: 'Efficiency', desc: 'Weight for premium/margin ratio (0-100)', type: 'number', min: 0, max: 100, step: 5 },
    ],
  },
  {
    key: 'budget',
    label: 'Budget & Position Sizing',
    desc: 'Controls how much margin the scanner pipeline allocates for new positions and how individual positions are sized.',
    fields: [
      { key: 'margin_budget_pct', label: 'Margin Budget %', desc: 'Fraction of NLV for total margin budget (0.01-1.0). No hardcoded cap — this % of your NLV is the authority.', type: 'number', min: 0.01, max: 1, step: 0.05 },
      { key: 'margin_budget_default', label: 'Offline Budget Default', desc: 'Fallback margin budget ($) when IBKR is disconnected', type: 'number', min: 1000, step: 5000 },
      { key: 'max_positions', label: 'Max Positions', desc: 'Maximum number of open positions', type: 'number', min: 1, max: 100, step: 1 },
      { key: 'max_positions_per_day', label: 'Max Positions / Day', desc: 'Maximum new positions opened per trading day', type: 'number', min: 1, max: 50, step: 1 },
      { key: 'max_per_sector', label: 'Max Per Sector', desc: 'Sector concentration limit', type: 'number', min: 1, max: 20, step: 1 },
      { key: 'price_threshold', label: 'Price Threshold', desc: 'Stock price dividing cheap/expensive ($)', type: 'number', min: 0, step: 5 },
      { key: 'max_contracts_expensive', label: 'Max Contracts (Expensive)', desc: 'Max contracts for stocks above threshold', type: 'number', min: 1, max: 20, step: 1 },
      { key: 'max_contracts_cheap', label: 'Max Contracts (Cheap)', desc: 'Max contracts for stocks below threshold', type: 'number', min: 1, max: 20, step: 1 },
      { key: 'risk_per_trade_pct', label: 'Risk Per Trade %', desc: 'Max risk per trade as fraction of NLV (0.02=2%, 0.05=5%)', type: 'number', min: 0.005, max: 0.20, step: 0.005 },
      { key: 'loss_assumption_pct', label: 'Loss Assumption %', desc: 'Assumed max stock drop for risk calc (0.25=25% drop, lower=more contracts)', type: 'number', min: 0.05, max: 0.50, step: 0.01 },
      { key: 'vix_scale_normal', label: 'VIX Scale (15-25)', desc: 'Sizing multiplier in normal VIX (1.0=full, 0.8=20% reduction)', type: 'number', min: 0.1, max: 1.0, step: 0.05 },
      { key: 'vix_scale_elevated', label: 'VIX Scale (25-35)', desc: 'Sizing multiplier in elevated VIX', type: 'number', min: 0.1, max: 1.0, step: 0.05 },
      { key: 'vix_scale_extreme', label: 'VIX Scale (>35)', desc: 'Sizing multiplier in extreme VIX', type: 'number', min: 0.0, max: 1.0, step: 0.05 },
    ],
  },
  {
    key: 'risk_governor',
    label: 'Risk Governor',
    desc: 'Hard safety circuit breakers enforced at execution time. These limits halt trading when breached — they are the last line of defense.',
    fields: [
      { key: 'max_margin_utilization', label: 'Max Margin Utilization', desc: 'Max total margin as fraction of NLV (0.80 = 80%)', type: 'number', min: 0.10, max: 1.0, step: 0.05 },
      { key: 'max_margin_per_trade_pct', label: 'Max Margin / Trade', desc: 'Max margin for a single trade as fraction of NLV (0.10 = 10%)', type: 'number', min: 0.01, max: 0.50, step: 0.01 },
      { key: 'max_daily_loss_pct', label: 'Max Daily Loss', desc: 'Circuit breaker: max daily loss as fraction (-0.02 = -2%)', type: 'number', min: -1.0, max: 0, step: 0.01 },
      { key: 'max_weekly_loss_pct', label: 'Max Weekly Loss', desc: 'Circuit breaker: max weekly loss as fraction (-0.05 = -5%)', type: 'number', min: -1.0, max: 0, step: 0.01 },
      { key: 'max_drawdown_pct', label: 'Max Drawdown', desc: 'Circuit breaker: max peak-to-trough drawdown (-0.10 = -10%)', type: 'number', min: -1.0, max: 0, step: 0.01 },
      { key: 'max_position_loss', label: 'Max Position Loss ($)', desc: 'Stop loss per position in dollars (negative, e.g. -500)', type: 'number', max: 0, step: 50 },
    ],
  },
  {
    key: 'earnings',
    label: 'Earnings',
    desc: 'Earnings detection is always active. When enabled, adds extra OTM cushion for symbols with earnings inside the DTE window.',
    fields: [
      { key: 'enabled', label: 'Adjust Filters', desc: 'Apply additional OTM% for earnings symbols', type: 'checkbox' },
      { key: 'additional_otm_pct', label: 'Additional OTM %', desc: 'Extra OTM cushion added for earnings (0.0-1.0)', type: 'number', min: 0, max: 1, step: 0.01 },
      { key: 'lookahead_days', label: 'Lookahead Days', desc: 'Extra days beyond DTE to check for earnings (0-14)', type: 'number', min: 0, max: 14, step: 1 },
    ],
  },
];

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------
function renderSettings(s) {
  const container = document.getElementById('settings-container');
  let html = '';

  // Budget widget at the top
  html += '<div class="budget-widget" id="budget-widget">' +
    '<h3>Live Margin Budget</h3>' +
    '<div style="text-align:center;color:var(--text-dim);padding:8px;font-size:11px;">' +
    '<span class="spinner"></span> Loading budget from IBKR...</div></div>';

  for (const section of SECTIONS) {
    const data = s[section.key] || {};
    html += '<div class="card">';
    html += '<div class="card-header" onclick="toggleSection(this)">';
    html += '<h2>' + esc(section.label) + '</h2>';
    html += '<span class="toggle">collapse</span>';
    html += '</div>';
    html += '<div class="card-body">';

    if (section.desc) {
      html += '<div class="section-desc">' + esc(section.desc) + '</div>';
    }

    for (const f of section.fields) {
      const val = data[f.key];
      html += '<div class="field-row">';
      html += '<div class="field-label">';
      html += '<span class="field-key">' + esc(f.label) + '</span>';
      html += '<span class="field-desc">' + esc(f.desc) + '</span>';
      html += '</div>';

      if (f.type === 'checkbox') {
        const checked = val ? 'checked' : '';
        html += '<div class="checkbox-wrap">' +
          '<input type="checkbox" id="f-' + section.key + '-' + f.key + '" ' + checked +
          ' onchange="updateField(\'' + section.key + '\',\'' + f.key + '\',this.checked)">' +
          '<label for="f-' + section.key + '-' + f.key + '">' + (val ? 'On' : 'Off') + '</label></div>';
      } else {
        const attrs = (f.min != null ? ' min="' + f.min + '"' : '') +
                      (f.max != null ? ' max="' + f.max + '"' : '') +
                      (f.step != null ? ' step="' + f.step + '"' : '');
        html += '<input type="number" id="f-' + section.key + '-' + f.key + '" value="' + esc(val) + '"' + attrs +
          ' onchange="updateField(\'' + section.key + '\',\'' + f.key + '\',this.value,\'' + f.type + '\')">';
      }

      html += '</div>';
    }

    // Ranking weights validation
    if (section.key === 'ranking') {
      html += '<div id="ranking-validation" style="padding:6px 0;"></div>';
    }

    html += '</div></div>';
  }

  container.innerHTML = html;

  validateRanking();
  loadBudget();
}

function toggleSection(header) {
  const body = header.nextElementSibling;
  const toggle = header.querySelector('.toggle');
  if (body.classList.contains('collapsed')) {
    body.classList.remove('collapsed');
    toggle.textContent = 'collapse';
  } else {
    body.classList.add('collapsed');
    toggle.textContent = 'expand';
  }
}

function updateField(section, key, value, type) {
  if (!_settings[section]) _settings[section] = {};

  if (type === 'number') {
    _settings[section][key] = parseFloat(value);
  } else if (typeof value === 'boolean') {
    _settings[section][key] = value;
    // Update label
    const label = document.querySelector('label[for="f-' + section + '-' + key + '"]');
    if (label) label.textContent = value ? 'On' : 'Off';
  } else {
    _settings[section][key] = value;
  }

  if (section === 'ranking') validateRanking();
  markDirty();
}

function validateRanking() {
  const r = _settings.ranking || {};
  const total = (r.safety || 0) + (r.liquidity || 0) + (r.ai_score || 0) + (r.efficiency || 0);
  const el = document.getElementById('ranking-validation');
  if (!el) return;
  if (total !== 100) {
    el.innerHTML = '<div class="validation-error">Weights sum to ' + total + ' (must be 100)</div>';
  } else {
    el.innerHTML = '<div style="color:var(--green);font-size:11px;">Weights sum to 100</div>';
  }
}

// ---------------------------------------------------------------------------
// Budget widget
// ---------------------------------------------------------------------------
async function loadBudget() {
  const widget = document.getElementById('budget-widget');
  if (!widget) return;

  try {
    const resp = await fetch('/api/scanner/budget');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const d = await resp.json();

    const nlv = d.nlv;
    const pct = _settings.budget ? _settings.budget.margin_budget_pct : 0.5;
    const ceiling = nlv ? nlv * pct : 0;
    const currentMargin = d.current_margin || 0;
    const stagedMargin = d.staged_margin || 0;
    const available = Math.max(0, ceiling - currentMargin - stagedMargin);
    const usedPct = ceiling > 0 ? ((currentMargin + stagedMargin) / ceiling * 100) : 0;

    const fmt = (v) => v != null ? '$' + v.toLocaleString('en-US', {maximumFractionDigits: 0}) : '—';

    widget.innerHTML =
      '<h3>Live Margin Budget</h3>' +
      '<div class="budget-row"><span>Net Liquidation Value</span><span class="val">' + fmt(nlv) + '</span></div>' +
      '<div class="budget-row"><span>Budget Ceiling (' + (pct * 100).toFixed(0) + '% NLV)</span><span class="val">' + fmt(ceiling) + '</span></div>' +
      '<div class="budget-row"><span>Current Margin Used</span><span class="val">' + fmt(currentMargin) + '</span></div>' +
      '<div class="budget-row"><span>Staged Margin</span><span class="val">' + fmt(stagedMargin) + ' (' + (d.staged_count || 0) + ' orders)</span></div>' +
      '<div class="budget-row"><span style="font-weight:600;">Available for New Trades</span><span class="val green">' + fmt(available) + '</span></div>' +
      '<div class="budget-bar"><div class="budget-bar-fill" style="width:' + Math.min(100, usedPct).toFixed(1) + '%"></div></div>' +
      '<div style="text-align:right;font-size:10px;color:var(--text-dim);margin-top:4px;">' +
      (usedPct).toFixed(1) + '% of ceiling used' +
      (d.ibkr_connected ? '' : ' &middot; <span style="color:var(--red);">IBKR offline</span>') + '</div>';
  } catch (e) {
    widget.innerHTML = '<h3>Live Margin Budget</h3>' +
      '<div style="color:var(--red);font-size:11px;">Failed to load budget: ' + esc(e.message) + '</div>';
  }
}

// ---------------------------------------------------------------------------
// Load & Save
// ---------------------------------------------------------------------------
async function loadSettings() {
  try {
    const resp = await fetch('/api/scanner/settings');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    _settings = await resp.json();
    renderSettings(_settings);
  } catch (e) {
    document.getElementById('settings-container').innerHTML =
      '<div style="padding:40px;text-align:center;color:var(--red);">Failed to load settings: ' + esc(e.message) + '</div>';
  }
}

async function saveSettings() {
  const btn = document.getElementById('btn-save');
  const fb = document.getElementById('save-feedback');
  btn.disabled = true;
  fb.innerHTML = '<span class="spinner"></span> Saving...';
  fb.style.color = 'var(--text-dim)';

  try {
    const resp = await fetch('/api/scanner/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(_settings),
    });
    const data = await resp.json();

    if (data.error) {
      fb.textContent = data.error;
      fb.style.color = 'var(--red)';
      showToast('Validation error: ' + data.error, true);
    } else {
      _dirty = false;
      _settings = data.settings || _settings;
      document.getElementById('save-status').textContent = 'Saved to config/scanner_settings.yaml';
      document.getElementById('save-status').style.color = 'var(--green)';
      fb.textContent = '';
      showToast('Settings saved successfully');
      // Refresh budget widget to reflect new pct
      loadBudget();
    }
  } catch (e) {
    fb.textContent = 'Save failed';
    fb.style.color = 'var(--red)';
    showToast('Save failed: ' + e.message, true);
  }

  btn.disabled = false;
}

// Warn on unsaved changes
window.addEventListener('beforeunload', function(e) {
  if (_dirty) { e.preventDefault(); e.returnValue = ''; }
});

loadSettings();
</script>
</body>
</html>"""

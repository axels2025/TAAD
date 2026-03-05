"""FastAPI endpoints and HTML page for the TAAD prompt editor.

Provides GET/PUT/POST endpoints for viewing, editing, and resetting the
Claude system prompts that drive trading decisions. Each prompt defaults
to a built-in constant in Python; overrides are stored in config/phase5.yaml.
"""

import yaml
from pathlib import Path

from loguru import logger

try:
    from fastapi import APIRouter, Depends, HTTPException
    from fastapi.responses import HTMLResponse

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

from src.agentic.config import load_phase5_config, Phase5Config

CONFIG_PATH = Path("config/phase5.yaml")

# Prompt registry: maps config field name to the built-in default constant
PROMPT_KEYS = [
    "reasoning_system_prompt",
    "position_exit_system_prompt",
    "reflection_system_prompt",
    "performance_analysis_system_prompt",
]

PROMPT_LABELS = {
    "reasoning_system_prompt": "Reasoning Engine",
    "position_exit_system_prompt": "Position Exit",
    "reflection_system_prompt": "Reflection",
    "performance_analysis_system_prompt": "Performance Analysis",
}

# Data template descriptions for each prompt type (read-only reference)
DATA_TEMPLATES = {
    "reasoning_system_prompt": [
        {"heading": "Event: {event_type}", "desc": "The triggering event (e.g. MARKET_OPEN, TIMER_TICK, POSITION_EXIT_CHECK)."},
        {"heading": "Event Data", "desc": "JSON payload specific to the event trigger."},
        {"heading": "Data as of: {timestamp}", "desc": "Grounding anchor — the exact time the context snapshot was taken."},
        {"heading": "Symbols in Scope: [{symbols}]", "desc": "Hallucination guard — only these symbols may be referenced."},
        {"heading": "Data Limitations", "desc": "Flags for missing or stale data (e.g. 'VIX: unavailable')."},
        {"heading": "Autonomy Level: L{level}", "desc": "Current autonomy level (L1=recommend, L2=execute with approval, etc.)."},
        {"heading": "Open Positions ({count})", "desc": "Each position: trade_id, symbol, strike, expiration, P&L %, entry premium, DTE."},
        {"heading": "Market Context", "desc": "VIX level, SPY price, conditions_favorable flag."},
        {"heading": "Active Patterns ({count})", "desc": "Validated patterns: name, win_rate, confidence score."},
        {"heading": "Recent Decisions ({count})", "desc": "Last N decisions: timestamp, action taken, confidence."},
        {"heading": "Anomalies ({count})", "desc": "Detected anomalies from the guardrail system."},
        {"heading": "Latest Reflection", "desc": "Summary from the most recent EOD reflection pass."},
        {"heading": "Staged Candidates ({count})", "desc": "Trades waiting for execution: symbol, strike, exp, limit price, contracts, state."},
        {"heading": "Similar Past Decisions ({count})", "desc": "Embedding-matched historical decisions for context."},
        {"heading": "Instructions", "desc": "Final instructions reminding Claude to respond with JSON DecisionOutput."},
    ],
    "position_exit_system_prompt": [
        {"heading": "Event: POSITION_EXIT_CHECK", "desc": "Always this event type for exit evaluations."},
        {"heading": "Target Position", "desc": "trade_id, symbol, strike, option_type (P/C), P&L %, entry_premium, expiration, contracts, DTE, current_mid."},
        {"heading": "Market Context", "desc": "VIX level, SPY price, conditions_favorable flag."},
        {"heading": "Instructions", "desc": "Asks Claude to decide CLOSE_POSITION or MONITOR_ONLY with trade_id in metadata."},
    ],
    "reflection_system_prompt": [
        {"heading": "Today's Decisions", "desc": "JSON array of all decision audit records from today."},
        {"heading": "Today's Trades", "desc": "JSON array of all trades opened or closed today."},
        {"heading": "Instructions", "desc": "Asks Claude to categorize decisions as correct/lucky/wrong and suggest patterns."},
    ],
    "performance_analysis_system_prompt": [
        {"heading": "User Question (if any)", "desc": "Optional user-specified question to address in the analysis."},
        {"heading": "Performance Summary ({N} days)", "desc": "Total trades, win rate, avg ROI, total P&L, max drawdown, recent 30d stats."},
        {"heading": "Validated Patterns", "desc": "Outperforming and underperforming patterns with sample size, win rate, ROI, p-value."},
        {"heading": "Dimensional Breakdowns", "desc": "Per-dimension buckets (OTM range, DTE, sector, etc.) with trade count, win rate, ROI."},
        {"heading": "Experiments", "desc": "Active and completed A/B experiments with control/test results."},
        {"heading": "Optimizer Proposals", "desc": "Parameter change proposals with expected improvement and confidence."},
        {"heading": "Recent Learning Events", "desc": "Last 5 learning events: date, type, pattern/parameter, change made."},
        {"heading": "Current Strategy Config", "desc": "Active strategy parameters as key-value pairs."},
    ],
}


def _get_builtin_defaults() -> dict[str, str]:
    """Import and return all built-in default prompts."""
    from src.agentic.reasoning_engine import (
        REASONING_SYSTEM_PROMPT,
        POSITION_EXIT_SYSTEM_PROMPT,
        REFLECTION_SYSTEM_PROMPT,
    )
    from src.agents.performance_analyzer import SYSTEM_PROMPT as PERF_SYSTEM_PROMPT

    return {
        "reasoning_system_prompt": REASONING_SYSTEM_PROMPT,
        "position_exit_system_prompt": POSITION_EXIT_SYSTEM_PROMPT,
        "reflection_system_prompt": REFLECTION_SYSTEM_PROMPT,
        "performance_analysis_system_prompt": PERF_SYSTEM_PROMPT,
    }


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_prompt_router(verify_token) -> "APIRouter":
    """Create the prompt editor API router.

    Args:
        verify_token: Dependency callable for bearer token auth

    Returns:
        FastAPI APIRouter with prompt endpoints
    """
    router = APIRouter(prefix="/api/prompts", tags=["prompts"])

    @router.get("")
    def get_prompts(token: None = Depends(verify_token)):
        """Return all 4 prompts with active value, is_custom flag, and built-in default."""
        config = load_phase5_config(str(CONFIG_PATH))
        defaults = _get_builtin_defaults()

        prompts = {}
        for key in PROMPT_KEYS:
            custom_value = getattr(config.claude, key, "")
            prompts[key] = {
                "label": PROMPT_LABELS[key],
                "active": custom_value or defaults[key],
                "is_custom": bool(custom_value),
                "builtin_default": defaults[key],
                "data_template": DATA_TEMPLATES.get(key, []),
            }

        return {"prompts": prompts}

    @router.put("")
    def update_prompts(payload: dict, token: None = Depends(verify_token)):
        """Save prompt overrides to phase5.yaml.

        Payload should be a dict of prompt_key -> new_value.
        Empty string means "use built-in default".
        """
        # Load existing YAML to merge (preserve non-claude sections)
        yaml_data = {}
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH) as f:
                yaml_data = yaml.safe_load(f) or {}

        claude_section = yaml_data.get("claude", {})

        # Update only the prompt fields that were sent
        defaults = _get_builtin_defaults()
        for key in PROMPT_KEYS:
            if key in payload:
                value = payload[key].strip() if payload[key] else ""
                # If the value matches the built-in default, store empty (= use default)
                if value == defaults[key]:
                    value = ""
                claude_section[key] = value

        yaml_data["claude"] = claude_section

        # Validate through Pydantic
        try:
            from src.agentic.guardrails.config import GuardrailConfig
            Phase5Config.model_rebuild(
                _types_namespace={"GuardrailConfig": GuardrailConfig}
            )
            Phase5Config(**yaml_data)
        except Exception as e:
            logger.warning(f"Prompt validation failed: {e}")
            return {"error": f"Validation failed: {e}"}

        # Write
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                f.write("# Phase 5: Continuous Agentic Trading Daemon Configuration\n")
                f.write("# ========================================================\n")
                f.write("# Managed by TAAD Dashboard. Manual edits are preserved on next save.\n\n")
                yaml.dump(
                    yaml_data,
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                    allow_unicode=True,
                )
            logger.info(f"Prompts saved to {CONFIG_PATH}")
            return {"status": "saved"}
        except Exception as e:
            logger.error(f"Failed to write prompts: {e}")
            return {"error": f"Failed to write: {e}"}

    @router.post("/reset/{prompt_key}")
    def reset_prompt(prompt_key: str, token: None = Depends(verify_token)):
        """Reset one prompt to built-in default (sets to empty string)."""
        if prompt_key not in PROMPT_KEYS:
            raise HTTPException(status_code=400, detail=f"Unknown prompt key: {prompt_key}")

        # Load, clear the field, write back
        yaml_data = {}
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH) as f:
                yaml_data = yaml.safe_load(f) or {}

        claude_section = yaml_data.get("claude", {})
        claude_section[prompt_key] = ""
        yaml_data["claude"] = claude_section

        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                f.write("# Phase 5: Continuous Agentic Trading Daemon Configuration\n")
                f.write("# ========================================================\n")
                f.write("# Managed by TAAD Dashboard. Manual edits are preserved on next save.\n\n")
                yaml.dump(
                    yaml_data,
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                    allow_unicode=True,
                )
            logger.info(f"Reset {prompt_key} to built-in default")
            return {"status": "reset", "key": prompt_key}
        except Exception as e:
            logger.error(f"Failed to reset prompt: {e}")
            return {"error": f"Failed to reset: {e}"}

    return router


# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------


def get_prompt_html() -> str:
    """Return the prompt editor HTML page."""
    return _PROMPT_HTML


_PROMPT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TAAD Prompts</title>
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

  .container { max-width: 1000px; padding: 16px 24px; }

  /* Tab bar */
  .tabs { display: flex; gap: 0; border-bottom: 1px solid var(--border); margin-bottom: 16px; overflow-x: auto; }
  .tab { padding: 10px 20px; font-size: 12px; font-weight: 600; color: var(--text-dim); cursor: pointer; border-bottom: 2px solid transparent; white-space: nowrap; transition: all 0.15s; }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--accent); border-bottom-color: var(--accent); }

  /* Cards */
  .card { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; margin-bottom: 16px; }
  .card-header { padding: 10px 16px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }
  .card-header h2 { font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-dim); font-weight: 600; }

  .card-body { padding: 16px; }

  /* Prompt textarea */
  textarea.prompt-editor {
    background: var(--bg); border: 1px solid var(--border); color: var(--text);
    padding: 12px; border-radius: 4px; font-family: inherit; font-size: 12px;
    width: 100%; min-height: 400px; resize: vertical; line-height: 1.6;
    tab-size: 2;
  }
  textarea.prompt-editor:focus { border-color: var(--accent); outline: none; }

  /* Status badges */
  .badge { display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
  .badge-custom { background: rgba(255, 214, 0, 0.15); color: var(--yellow); border: 1px solid rgba(255, 214, 0, 0.3); }
  .badge-default { background: rgba(0, 230, 118, 0.15); color: var(--green); border: 1px solid rgba(0, 230, 118, 0.3); }

  /* Buttons */
  .btn { border: none; padding: 6px 14px; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 11px; font-weight: 600; transition: all 0.15s; }
  .btn-green { background: rgba(0, 230, 118, 0.15); color: var(--green); border: 1px solid rgba(0, 230, 118, 0.3); }
  .btn-green:hover { background: rgba(0, 230, 118, 0.3); }
  .btn-green:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-outline { background: transparent; color: var(--text-dim); border: 1px solid var(--border); }
  .btn-outline:hover { border-color: var(--accent); color: var(--accent); }
  .btn-red { background: rgba(255, 82, 82, 0.1); color: var(--red); border: 1px solid rgba(255, 82, 82, 0.25); }
  .btn-red:hover { background: rgba(255, 82, 82, 0.2); }

  .prompt-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; flex-wrap: wrap; gap: 8px; }
  .prompt-meta { font-size: 11px; color: var(--text-dim); display: flex; align-items: center; gap: 12px; }

  /* Show default toggle */
  .default-preview { margin-top: 12px; }
  .default-preview summary { font-size: 11px; color: var(--text-dim); cursor: pointer; user-select: none; }
  .default-preview summary:hover { color: var(--accent); }
  .default-preview pre { background: var(--bg); border: 1px solid var(--border); border-radius: 4px; padding: 12px; margin-top: 8px; font-size: 11px; line-height: 1.5; color: var(--text-dim); white-space: pre-wrap; word-wrap: break-word; max-height: 300px; overflow-y: auto; }

  /* Data template */
  .template-section { padding: 8px 0; border-bottom: 1px solid rgba(42, 74, 107, 0.3); }
  .template-section:last-child { border-bottom: none; }
  .template-heading { font-size: 12px; color: var(--accent); font-weight: 600; font-family: inherit; }
  .template-desc { font-size: 11px; color: var(--text-dim); margin-top: 2px; }
  .template-note { font-size: 11px; color: var(--text-dim); font-style: italic; padding: 8px 0; }

  /* Save bar */
  .save-bar { position: sticky; bottom: 0; background: var(--bg2); border-top: 1px solid var(--border); padding: 12px 24px; display: flex; align-items: center; justify-content: space-between; z-index: 10; }
  .save-bar .save-status { font-size: 12px; color: var(--text-dim); }

  .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid var(--border); border-top: 2px solid var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite; vertical-align: middle; margin-right: 6px; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .toast { position: fixed; bottom: 20px; right: 20px; background: var(--bg3); border: 1px solid var(--green); color: var(--green); padding: 12px 20px; border-radius: 6px; font-size: 13px; opacity: 0; transition: opacity 0.3s; pointer-events: none; z-index: 100; }
  .toast.show { opacity: 1; }
  .toast.error { border-color: var(--red); color: var(--red); }

  /* Fullscreen modal overlay */
  .modal-overlay {
    display: none; position: fixed; inset: 0; z-index: 200;
    background: rgba(0, 0, 0, 0.85); backdrop-filter: blur(4px);
  }
  .modal-overlay.open { display: flex; flex-direction: column; }
  .modal-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 24px; background: var(--bg2); border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .modal-header h2 { font-size: 14px; color: var(--accent); font-weight: 600; }
  .modal-header .modal-meta { font-size: 11px; color: var(--text-dim); display: flex; align-items: center; gap: 12px; }
  .modal-body { flex: 1; padding: 16px 24px; overflow: hidden; display: flex; flex-direction: column; }
  .modal-body textarea {
    flex: 1; width: 100%; background: var(--bg); border: 1px solid var(--border);
    color: var(--text); padding: 16px; border-radius: 6px; font-family: inherit;
    font-size: 13px; line-height: 1.7; resize: none; tab-size: 2;
  }
  .modal-body textarea:focus { border-color: var(--accent); outline: none; }
  .btn-expand { background: transparent; color: var(--text-dim); border: 1px solid var(--border); padding: 4px 10px; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 11px; transition: all 0.15s; }
  .btn-expand:hover { border-color: var(--accent); color: var(--accent); }

  /* Tab panels */
  .tab-panel { display: none; }
  .tab-panel.active { display: block; }

  /* Loading */
  .loading { padding: 40px; text-align: center; color: var(--text-dim); }
</style>
</head>
<body>

<div class="header">
  <h1>TAAD <span>Prompts</span></h1>
  <a href="/" class="back-link">Back to Dashboard</a>
</div>

<div class="container">
  <div class="tabs" id="tab-bar"></div>
  <div id="panels-container">
    <div class="loading"><span class="spinner"></span> Loading prompts...</div>
  </div>
</div>

<div class="save-bar">
  <span class="save-status" id="save-status">Loading...</span>
  <div style="display:flex;gap:8px;align-items:center;">
    <span id="save-feedback" style="font-size:12px;"></span>
    <button class="btn btn-green" id="btn-save" onclick="savePrompts()">Save Changes</button>
  </div>
</div>

<div class="toast" id="toast"></div>

<div class="modal-overlay" id="prompt-modal">
  <div class="modal-header">
    <h2 id="modal-title">Prompt Builder</h2>
    <div style="display:flex;align-items:center;gap:12px;">
      <div class="modal-meta">
        <span id="modal-charcount">0 chars</span>
        <span id="modal-badge" class="badge badge-default">Built-in Default</span>
      </div>
      <button class="btn btn-outline" onclick="closeModal()" title="Esc to close">Close</button>
    </div>
  </div>
  <div class="modal-body">
    <textarea id="modal-editor" oninput="onModalInput()"></textarea>
  </div>
</div>

<script>
const PROMPT_KEYS = [
  'reasoning_system_prompt',
  'position_exit_system_prompt',
  'reflection_system_prompt',
  'performance_analysis_system_prompt'
];

let _prompts = {};
let _activeTab = PROMPT_KEYS[0];
let _dirty = false;

function esc(s) {
  if (s == null) return '';
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

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

function switchTab(key) {
  _activeTab = key;
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.key === key));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.dataset.key === key));
}

function updateCharCount(key) {
  const ta = document.getElementById('editor-' + key);
  const cc = document.getElementById('charcount-' + key);
  if (ta && cc) cc.textContent = ta.value.length.toLocaleString() + ' chars';
}

function updateBadge(key) {
  const ta = document.getElementById('editor-' + key);
  const badge = document.getElementById('badge-' + key);
  if (!ta || !badge) return;
  const isCustom = ta.value.trim() !== '' && ta.value.trim() !== (_prompts[key]?.builtin_default || '').trim();
  badge.className = 'badge ' + (isCustom ? 'badge-custom' : 'badge-default');
  badge.textContent = isCustom ? 'Custom' : 'Built-in Default';
}

async function loadPrompts() {
  try {
    const resp = await fetch('/api/prompts');
    const data = await resp.json();
    _prompts = data.prompts;
    renderAll();
    document.getElementById('save-status').textContent = 'Loaded from config/phase5.yaml';
    document.getElementById('save-status').style.color = 'var(--text-dim)';
  } catch(e) {
    document.getElementById('panels-container').innerHTML =
      '<div class="loading" style="color:var(--red);">Failed to load prompts: ' + esc(e.message) + '</div>';
  }
}

function renderAll() {
  // Tab bar
  const tabBar = document.getElementById('tab-bar');
  let tabHtml = '';
  for (const key of PROMPT_KEYS) {
    const p = _prompts[key];
    if (!p) continue;
    const active = key === _activeTab ? ' active' : '';
    tabHtml += `<div class="tab${active}" data-key="${key}" onclick="switchTab('${key}')">${esc(p.label)}</div>`;
  }
  tabBar.innerHTML = tabHtml;

  // Panels
  const container = document.getElementById('panels-container');
  let panelHtml = '';
  for (const key of PROMPT_KEYS) {
    const p = _prompts[key];
    if (!p) continue;
    const active = key === _activeTab ? ' active' : '';
    const isCustom = p.is_custom;

    panelHtml += '<div class="tab-panel' + active + '" data-key="' + key + '">';

    // System Prompt card
    panelHtml += '<div class="card"><div class="card-header"><h2>System Prompt</h2></div><div class="card-body">';
    panelHtml += '<div class="prompt-toolbar">';
    panelHtml += '<div class="prompt-meta">';
    panelHtml += '<span class="badge ' + (isCustom ? 'badge-custom' : 'badge-default') + '" id="badge-' + key + '">' + (isCustom ? 'Custom' : 'Built-in Default') + '</span>';
    panelHtml += '<span id="charcount-' + key + '">' + (p.active || '').length.toLocaleString() + ' chars</span>';
    panelHtml += '</div>';
    panelHtml += `<div style="display:flex;gap:6px;">`;
    panelHtml += `<button class="btn-expand" onclick="openModal('${key}')" title="Open full-screen editor">Expand</button>`;
    panelHtml += `<button class="btn btn-red" onclick="resetPrompt('${key}')">Reset to Default</button>`;
    panelHtml += `</div>`;
    panelHtml += '</div>';
    panelHtml += `<textarea class="prompt-editor" id="editor-${key}" oninput="markDirty(); updateCharCount('${key}'); updateBadge('${key}')">${esc(p.active)}</textarea>`;

    // Show default toggle
    panelHtml += '<details class="default-preview"><summary>Show built-in default for comparison</summary>';
    panelHtml += '<pre>' + esc(p.builtin_default) + '</pre></details>';

    panelHtml += '</div></div>';

    // Data Template card
    panelHtml += '<div class="card"><div class="card-header"><h2>Data Template (Read-Only)</h2></div><div class="card-body">';
    panelHtml += '<div class="template-note">Data templates are assembled from live system state at runtime and are not editable. This shows the structure of the user message that accompanies the system prompt above.</div>';

    const template = p.data_template || [];
    for (const section of template) {
      panelHtml += '<div class="template-section">';
      panelHtml += '<div class="template-heading">## ' + esc(section.heading) + '</div>';
      panelHtml += '<div class="template-desc">' + esc(section.desc) + '</div>';
      panelHtml += '</div>';
    }
    if (template.length === 0) {
      panelHtml += '<div class="template-note">No data template information available for this prompt.</div>';
    }

    panelHtml += '</div></div>';

    panelHtml += '</div>';
  }
  container.innerHTML = panelHtml;
}

async function resetPrompt(key) {
  if (!confirm('Reset "' + (_prompts[key]?.label || key) + '" to built-in default?')) return;

  try {
    const resp = await fetch('/api/prompts/reset/' + key, { method: 'POST' });
    const data = await resp.json();
    if (data.error) {
      showToast(data.error, true);
      return;
    }
    // Update local state and re-render
    _prompts[key].active = _prompts[key].builtin_default;
    _prompts[key].is_custom = false;
    const ta = document.getElementById('editor-' + key);
    if (ta) ta.value = _prompts[key].builtin_default;
    updateCharCount(key);
    updateBadge(key);
    showToast((_prompts[key]?.label || key) + ' reset to default');
  } catch(e) {
    showToast('Reset failed: ' + e.message, true);
  }
}

async function savePrompts() {
  const btn = document.getElementById('btn-save');
  const feedback = document.getElementById('save-feedback');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Saving...';
  feedback.textContent = '';

  // Collect all prompt values
  const payload = {};
  for (const key of PROMPT_KEYS) {
    const ta = document.getElementById('editor-' + key);
    if (ta) payload[key] = ta.value;
  }

  try {
    const resp = await fetch('/api/prompts', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const data = await resp.json();

    if (data.error) {
      feedback.style.color = 'var(--red)';
      feedback.textContent = data.error;
      showToast(data.error, true);
    } else {
      _dirty = false;
      document.getElementById('save-status').textContent = 'Saved successfully';
      document.getElementById('save-status').style.color = 'var(--green)';
      showToast('Prompts saved. Restart daemon to apply changes.');
      // Re-fetch to update is_custom badges
      await loadPrompts();
    }
  } catch(e) {
    feedback.style.color = 'var(--red)';
    feedback.textContent = e.message;
    showToast('Save failed: ' + e.message, true);
  }

  btn.innerHTML = 'Save Changes';
  btn.disabled = false;
}

// ---------- Fullscreen Modal ----------

let _modalKey = null;

function openModal(key) {
  _modalKey = key;
  const p = _prompts[key];
  const ta = document.getElementById('editor-' + key);
  const modal = document.getElementById('prompt-modal');
  const editor = document.getElementById('modal-editor');

  document.getElementById('modal-title').textContent = (p?.label || key) + ' — Prompt Builder';
  editor.value = ta ? ta.value : (p?.active || '');
  updateModalMeta();
  modal.classList.add('open');
  editor.focus();
}

function closeModal() {
  const modal = document.getElementById('prompt-modal');
  if (!_modalKey) { modal.classList.remove('open'); return; }

  // Sync modal content back to the tab editor
  const editor = document.getElementById('modal-editor');
  const ta = document.getElementById('editor-' + _modalKey);
  if (ta && editor.value !== ta.value) {
    ta.value = editor.value;
    markDirty();
    updateCharCount(_modalKey);
    updateBadge(_modalKey);
  }

  _modalKey = null;
  modal.classList.remove('open');
}

function onModalInput() {
  updateModalMeta();
  // Live-sync to tab editor so save picks up changes
  if (_modalKey) {
    const ta = document.getElementById('editor-' + _modalKey);
    const editor = document.getElementById('modal-editor');
    if (ta) ta.value = editor.value;
    markDirty();
    updateCharCount(_modalKey);
    updateBadge(_modalKey);
  }
}

function updateModalMeta() {
  const editor = document.getElementById('modal-editor');
  document.getElementById('modal-charcount').textContent = editor.value.length.toLocaleString() + ' chars';
  if (_modalKey && _prompts[_modalKey]) {
    const isCustom = editor.value.trim() !== '' && editor.value.trim() !== (_prompts[_modalKey].builtin_default || '').trim();
    const badge = document.getElementById('modal-badge');
    badge.className = 'badge ' + (isCustom ? 'badge-custom' : 'badge-default');
    badge.textContent = isCustom ? 'Custom' : 'Built-in Default';
  }
}

// Close modal on Escape key
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && _modalKey) closeModal();
});

// Warn on unsaved changes
window.addEventListener('beforeunload', (e) => {
  if (_dirty) { e.preventDefault(); e.returnValue = ''; }
});

loadPrompts();
</script>
</body>
</html>"""

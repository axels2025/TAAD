"""FastAPI endpoints and HTML page for the TAAD configuration editor.

Provides GET/PUT endpoints for reading and updating config/phase5.yaml,
plus an HTML page with grouped form sections matching the dark terminal UI.

The Pydantic models in config.py serve as the validation layer — all
updates pass through Phase5Config before being written to YAML.
"""

import yaml
from pathlib import Path

from loguru import logger

try:
    from fastapi import APIRouter, Depends, HTTPException
    from fastapi.responses import HTMLResponse
    from pydantic import BaseModel, ValidationError

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

from src.agentic.config import load_phase5_config, Phase5Config

CONFIG_PATH = Path("config/phase5.yaml")

# Known Claude models for the dropdown
CLAUDE_MODELS = [
    {"id": "claude-sonnet-4-5-20250929", "label": "Claude Sonnet 4.5"},
    {"id": "claude-opus-4-6", "label": "Claude Opus 4.6"},
    {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5"},
]


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_config_router(verify_token) -> "APIRouter":
    """Create the config API router.

    Args:
        verify_token: Dependency callable for bearer token auth

    Returns:
        FastAPI APIRouter with config endpoints
    """
    router = APIRouter(prefix="/api/config", tags=["config"])

    # ------------------------------------------------------------------
    # GET /api/config — return current config as JSON
    # ------------------------------------------------------------------
    @router.get("")
    def get_config(token: None = Depends(verify_token)):
        """Return the current Phase 5 configuration."""
        config = load_phase5_config(str(CONFIG_PATH))
        return {
            "config": config.model_dump(),
            "models": CLAUDE_MODELS,
            "config_path": str(CONFIG_PATH),
        }

    # ------------------------------------------------------------------
    # PUT /api/config — validate and save updated config
    # ------------------------------------------------------------------
    @router.put("")
    def update_config(payload: dict, token: None = Depends(verify_token)):
        """Update Phase 5 configuration.

        Validates the incoming config through Pydantic models, then
        writes back to config/phase5.yaml.

        Args:
            payload: Dict with config sections (autonomy, claude, etc.)

        Returns:
            Updated config and status
        """
        try:
            # Validate through Pydantic — this catches type errors,
            # out-of-range values, missing fields, etc.
            from src.agentic.guardrails.config import GuardrailConfig
            Phase5Config.model_rebuild(
                _types_namespace={"GuardrailConfig": GuardrailConfig}
            )
            validated = Phase5Config(**payload)
        except ValidationError as e:
            logger.warning(f"Config validation failed: {e}")
            return {
                "error": "Validation failed",
                "details": e.errors(),
            }

        # Serialize to YAML-friendly dict (exclude defaults=False would
        # omit unchanged values, but we want the full file to be explicit)
        config_dict = validated.model_dump()

        # Write to YAML
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                f.write("# Phase 5: Continuous Agentic Trading Daemon Configuration\n")
                f.write("# ========================================================\n")
                f.write("# Managed by TAAD Dashboard. Manual edits are preserved on next save.\n\n")
                yaml.dump(
                    config_dict,
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                    allow_unicode=True,
                )

            logger.info(f"Config saved to {CONFIG_PATH}")
            return {
                "status": "saved",
                "config": config_dict,
            }
        except Exception as e:
            logger.error(f"Failed to write config: {e}")
            return {"error": f"Failed to write config: {e}"}

    return router


# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------


def get_config_html() -> str:
    """Return the config editor HTML page."""
    return _CONFIG_HTML


_CONFIG_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TAAD Settings</title>
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

  input[type="text"], input[type="number"], select {
    background: var(--bg); border: 1px solid var(--border); color: var(--text);
    padding: 6px 10px; border-radius: 4px; font-family: inherit; font-size: 12px; width: 100%; max-width: 300px;
  }
  input:focus, select:focus { border-color: var(--accent); outline: none; }
  select option { background: var(--bg2); color: var(--text); }

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

  .validation-error { color: var(--red); font-size: 11px; margin-top: 4px; }
</style>
</head>
<body>

<div class="header">
  <h1>TAAD <span>Settings</span></h1>
  <a href="/" class="back-link">Back to Dashboard</a>
</div>

<div class="container" id="config-container">
  <div class="status-msg" style="padding:40px;text-align:center;color:var(--text-dim);">
    <span class="spinner"></span> Loading configuration...
  </div>
</div>

<div class="save-bar">
  <span class="save-status" id="save-status">Config loaded from config/phase5.yaml</span>
  <div style="display:flex;gap:8px;align-items:center;">
    <span id="save-feedback" style="font-size:12px;"></span>
    <button class="btn btn-green" id="btn-save" onclick="saveConfig()">Save Configuration</button>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
let _config = {};
let _models = [];
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

// Section metadata: label, description, field descriptions
const SECTIONS = {
  claude: {
    label: 'Claude AI',
    desc: 'Model selection and API parameters for the reasoning engine.',
    fields: {
      reasoning_model: {desc: 'Primary model for trade decisions', type: 'model'},
      reflection_model: {desc: 'Model for self-reflection pass', type: 'model'},
      embedding_model: {desc: 'Embedding model (not typically changed)', type: 'text'},
      max_tokens: {desc: 'Max response tokens per call', type: 'number'},
      temperature: {desc: 'Sampling temperature (0=deterministic)', type: 'number', step: 0.1},
      daily_cost_cap_usd: {desc: 'Hard daily spend limit ($)', type: 'number', step: 0.5},
      max_retries: {desc: 'API retry attempts on failure', type: 'number'},
    }
  },
  autonomy: {
    label: 'Autonomy Levels',
    desc: 'Controls how much independence the daemon has. L1=recommend, L2=execute with approval.',
    fields: {
      initial_level: {desc: 'Starting autonomy level (1-4)', type: 'number'},
      max_level: {desc: 'Maximum allowed level (safety cap)', type: 'number'},
      promotion_clean_days: {desc: 'Clean days needed for promotion', type: 'number'},
      promotion_min_trades: {desc: 'Min trades before promotion eligible', type: 'number'},
      promotion_min_win_rate: {desc: 'Min win rate for promotion (0-1)', type: 'number', step: 0.05},
      demotion_loss_streak: {desc: 'Consecutive losses triggering demotion', type: 'number'},
    }
  },
  daemon: {
    label: 'Daemon Process',
    desc: 'IBKR connection, polling intervals, and process management.',
    fields: {
      client_id: {desc: 'IBKR client ID (avoid conflicts)', type: 'number'},
      heartbeat_interval_seconds: {desc: 'Heartbeat interval', type: 'number'},
      event_poll_interval_seconds: {desc: 'Event bus poll interval', type: 'number'},
      max_events_per_cycle: {desc: 'Max events processed per cycle', type: 'number'},
      pid_file: {desc: 'PID file path', type: 'text'},
      graceful_shutdown_timeout_seconds: {desc: 'Shutdown timeout', type: 'number'},
    }
  },
  learning: {
    label: 'Learning Loop',
    desc: 'End-of-day reflection and experiment parameters.',
    fields: {
      eod_reflection_time: {desc: 'EOD reflection time (ET)', type: 'text'},
      min_trades_for_experiment: {desc: 'Min trades to start an experiment', type: 'number'},
      max_concurrent_experiments: {desc: 'Max simultaneous experiments', type: 'number'},
    }
  },
  alerts: {
    label: 'Alerts',
    desc: 'Alert routing configuration.',
    fields: {
      log_all: {desc: 'Log all alerts', type: 'bool'},
      email_medium_and_above: {desc: 'Email for medium+ alerts', type: 'bool'},
      webhook_high_and_above: {desc: 'Webhook for high+ alerts', type: 'bool'},
    }
  },
  dashboard: {
    label: 'Dashboard',
    desc: 'Web UI settings. Changes take effect on restart.',
    fields: {
      enabled: {desc: 'Enable web dashboard', type: 'bool'},
      host: {desc: 'Bind address', type: 'text'},
      port: {desc: 'Port number', type: 'number'},
      auth_token: {desc: 'Bearer token (empty = no auth)', type: 'text'},
    }
  },
  guardrails: {
    label: 'Guardrails',
    desc: 'Hallucination detection and execution safety guards.',
    fields: {
      enabled: {desc: 'Master guardrail toggle', type: 'bool'},
      action_plausibility_enabled: {desc: 'Check action plausibility', type: 'bool'},
      symbol_crossref_enabled: {desc: 'Cross-reference symbols', type: 'bool'},
      reasoning_coherence_enabled: {desc: 'Check reasoning coherence', type: 'bool'},
      data_freshness_enabled: {desc: 'Validate data freshness', type: 'bool'},
      consistency_check_enabled: {desc: 'Input consistency checks', type: 'bool'},
      null_sanitization_enabled: {desc: 'Sanitize null/NaN inputs', type: 'bool'},
      numerical_grounding_enabled: {desc: 'Numerical grounding checks', type: 'bool'},
      numerical_tolerance_pct: {desc: 'Tolerance for numerical comparisons', type: 'number', step: 0.01},
      numerical_max_mismatches_before_block: {desc: 'Max mismatches before block', type: 'number'},
      execution_gate_enabled: {desc: 'Execution gate checks', type: 'bool'},
      vix_movement_block_pct: {desc: 'Block if VIX moves >X%', type: 'number', step: 1.0},
      spy_movement_block_pct: {desc: 'Block if SPY moves >X%', type: 'number', step: 0.5},
      max_orders_per_minute: {desc: 'Max orders per minute', type: 'number'},
      confidence_calibration_enabled: {desc: 'Confidence calibration monitoring', type: 'bool'},
      reasoning_entropy_enabled: {desc: 'Reasoning entropy monitoring', type: 'bool'},
      calibration_error_threshold: {desc: 'Calibration error threshold', type: 'number', step: 0.01},
      reasoning_similarity_threshold: {desc: 'Reasoning similarity threshold', type: 'number', step: 0.05},
      reasoning_stagnation_count: {desc: 'Stagnation detection count', type: 'number'},
    }
  },
};

async function loadConfig() {
  try {
    const resp = await fetch('/api/config');
    const data = await resp.json();
    _config = data.config;
    _models = data.models || [];
    renderConfig();
  } catch(e) {
    document.getElementById('config-container').innerHTML =
      '<div style="padding:40px;text-align:center;color:var(--red);">Failed to load config: ' + esc(e.message) + '</div>';
  }
}

function renderConfig() {
  const container = document.getElementById('config-container');
  let html = '';

  for (const [section, meta] of Object.entries(SECTIONS)) {
    const sectionData = _config[section] || {};
    const collapsed = ['guardrails', 'dashboard', 'alerts'].includes(section) ? ' collapsed' : '';

    html += `<div class="card">
      <div class="card-header" onclick="toggleSection('${section}')">
        <h2>${esc(meta.label)}</h2>
        <span class="toggle" id="toggle-${section}">${collapsed ? 'expand' : 'collapse'}</span>
      </div>
      <div class="card-body${collapsed}" id="section-${section}">
        <div class="section-desc">${esc(meta.desc)}</div>`;

    for (const [key, fieldMeta] of Object.entries(meta.fields)) {
      const value = sectionData[key];
      const inputId = `${section}.${key}`;
      html += `<div class="field-row">
        <div class="field-label">
          <span class="field-key">${esc(key)}</span>
          <span class="field-desc">${esc(fieldMeta.desc)}</span>
        </div>
        <div>${renderField(inputId, fieldMeta, value)}</div>
      </div>`;
    }

    html += `</div></div>`;
  }

  container.innerHTML = html;
}

function renderField(id, meta, value) {
  if (meta.type === 'bool') {
    const checked = value ? 'checked' : '';
    return `<div class="checkbox-wrap">
      <input type="checkbox" id="${esc(id)}" ${checked} onchange="markDirty()">
      <label for="${esc(id)}">${value ? 'Enabled' : 'Disabled'}</label>
    </div>`;
  }
  if (meta.type === 'model') {
    let html = `<select id="${esc(id)}" onchange="markDirty()">`;
    for (const m of _models) {
      const sel = m.id === value ? ' selected' : '';
      html += `<option value="${esc(m.id)}"${sel}>${esc(m.label)} (${esc(m.id)})</option>`;
    }
    // Include current value even if not in known models list
    if (!_models.find(m => m.id === value)) {
      html += `<option value="${esc(value)}" selected>${esc(value)}</option>`;
    }
    html += `</select>`;
    return html;
  }
  if (meta.type === 'number') {
    const step = meta.step || 1;
    return `<input type="number" id="${esc(id)}" value="${value != null ? value : ''}" step="${step}" onchange="markDirty()">`;
  }
  // Default: text
  return `<input type="text" id="${esc(id)}" value="${esc(value != null ? value : '')}" onchange="markDirty()">`;
}

function toggleSection(section) {
  const body = document.getElementById('section-' + section);
  const toggle = document.getElementById('toggle-' + section);
  if (body.classList.contains('collapsed')) {
    body.classList.remove('collapsed');
    toggle.textContent = 'collapse';
  } else {
    body.classList.add('collapsed');
    toggle.textContent = 'expand';
  }
}

function collectConfig() {
  const config = {};
  for (const [section, meta] of Object.entries(SECTIONS)) {
    config[section] = {};
    for (const [key, fieldMeta] of Object.entries(meta.fields)) {
      const id = `${section}.${key}`;
      const el = document.getElementById(id);
      if (!el) continue;

      if (fieldMeta.type === 'bool') {
        config[section][key] = el.checked;
      } else if (fieldMeta.type === 'number') {
        config[section][key] = parseFloat(el.value) || 0;
      } else {
        config[section][key] = el.value;
      }
    }
  }
  return config;
}

async function saveConfig() {
  const btn = document.getElementById('btn-save');
  const feedback = document.getElementById('save-feedback');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Saving...';
  feedback.textContent = '';

  const config = collectConfig();

  try {
    const resp = await fetch('/api/config', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(config),
    });
    const data = await resp.json();

    if (data.error) {
      feedback.style.color = 'var(--red)';
      if (data.details) {
        const msgs = data.details.map(d => d.msg || d.type).join('; ');
        feedback.textContent = 'Validation: ' + msgs;
      } else {
        feedback.textContent = data.error;
      }
      showToast(data.error, true);
    } else {
      _config = data.config;
      _dirty = false;
      document.getElementById('save-status').textContent = 'Saved successfully';
      document.getElementById('save-status').style.color = 'var(--green)';
      showToast('Configuration saved. Restart daemon to apply changes.');
      // Re-render to reflect any Pydantic normalization
      renderConfig();
    }
  } catch(e) {
    feedback.style.color = 'var(--red)';
    feedback.textContent = e.message;
    showToast('Save failed: ' + e.message, true);
  }

  btn.innerHTML = 'Save Configuration';
  btn.disabled = false;
}

// Warn on unsaved changes
window.addEventListener('beforeunload', (e) => {
  if (_dirty) { e.preventDefault(); e.returnValue = ''; }
});

loadConfig();
</script>
</body>
</html>"""

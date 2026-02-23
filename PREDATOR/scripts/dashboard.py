"""
PREDATOR Dashboard v3.0
Professional autonomous trading dashboard.

Run: streamlit run scripts/dashboard.py --server.port 8501
"""

from __future__ import annotations

import json
import os
import sys
import time
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from core.config import settings
    from core.system_state import state as system_state
    from core.database import db
    from core.strategy import PROFILES, strategy
    CONNECTED = True
except Exception:
    settings = None
    system_state = None
    db = None
    PROFILES = {}
    strategy = None
    CONNECTED = False

LOG = logging.getLogger("Predator.Dashboard")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PAGE CONFIG
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
st.set_page_config(
    page_title="PREDATOR",
    layout="wide",
    page_icon="P",
    initial_sidebar_state="expanded",
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CSS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700;800&family=Inter:wght@300;400;500;600;700;800;900&display=swap');

:root {
    --bg-primary: #06060c;
    --bg-secondary: #0c0c16;
    --bg-card: rgba(14,14,26,0.85);
    --bg-card-hover: rgba(18,18,34,0.95);
    --border-subtle: rgba(255,255,255,0.04);
    --border-accent: rgba(0,180,255,0.12);
    --text-primary: #e2e8f0;
    --text-secondary: #8892a6;
    --text-muted: #4a5568;
    --accent-cyan: #00b4ff;
    --accent-purple: #8b5cf6;
    --accent-green: #10b981;
    --accent-red: #ef4444;
    --accent-amber: #f59e0b;
    --accent-gradient: linear-gradient(135deg, #00b4ff, #8b5cf6);
    --font-mono: 'JetBrains Mono', 'SF Mono', 'Cascadia Code', monospace;
    --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    --radius-sm: 8px;
    --radius-md: 12px;
    --radius-lg: 16px;
}

html, body, [data-testid='stAppViewContainer'] {
    font-family: var(--font-sans);
    -webkit-font-smoothing: antialiased;
}

.stApp {
    background: var(--bg-primary);
    color: var(--text-primary);
}

.block-container {
    padding: 1rem 1.5rem 0 1.5rem;
    max-width: 100%;
}

/* ─── Glass Card ─── */
.g-card {
    background: var(--bg-card);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    padding: 20px;
    margin-bottom: 12px;
    transition: border-color 0.2s, box-shadow 0.2s;
}
.g-card:hover {
    border-color: var(--border-accent);
    box-shadow: 0 0 20px rgba(0,180,255,0.03);
}
.g-card-accent {
    border-left: 2px solid var(--accent-cyan);
}

/* ─── Header ─── */
.dash-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 0 16px 0;
    border-bottom: 1px solid var(--border-subtle);
    margin-bottom: 20px;
}
.dash-logo {
    font-family: var(--font-mono);
    font-size: 22px;
    font-weight: 800;
    letter-spacing: 4px;
    background: var(--accent-gradient);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}
.dash-version {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--text-muted);
    margin-left: 12px;
    letter-spacing: 1px;
}
.dash-status-bar {
    display: flex;
    align-items: center;
    gap: 12px;
}

/* ─── Status Pills ─── */
.pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border-radius: 999px;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.8px;
}
.pill-running {
    background: rgba(16,185,129,0.1);
    color: var(--accent-green);
    border: 1px solid rgba(16,185,129,0.25);
}
.pill-paused {
    background: rgba(245,158,11,0.1);
    color: var(--accent-amber);
    border: 1px solid rgba(245,158,11,0.25);
}
.pill-error, .pill-kill {
    background: rgba(239,68,68,0.1);
    color: var(--accent-red);
    border: 1px solid rgba(239,68,68,0.25);
}
.pill-mode {
    background: rgba(139,92,246,0.1);
    color: var(--accent-purple);
    border: 1px solid rgba(139,92,246,0.25);
}

/* Pulse animation for running status */
.pulse-dot {
    display: inline-block;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    animation: pulse 2s ease-in-out infinite;
}
.pulse-green { background: var(--accent-green); }
.pulse-amber { background: var(--accent-amber); }
.pulse-red { background: var(--accent-red); }

@keyframes pulse {
    0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(16,185,129,0.4); }
    50% { opacity: 0.7; box-shadow: 0 0 0 6px rgba(16,185,129,0); }
}

/* ─── KPI Cards ─── */
.kpi-card {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    padding: 16px 20px;
    text-align: left;
    position: relative;
    overflow: hidden;
}
.kpi-card::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 2px;
    background: var(--accent-gradient);
    opacity: 0.5;
}
.kpi-label {
    font-size: 11px;
    font-weight: 500;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 1.2px;
    margin-bottom: 6px;
}
.kpi-value {
    font-family: var(--font-mono);
    font-size: 26px;
    font-weight: 700;
    line-height: 1.1;
}
.kpi-sub {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--text-secondary);
    margin-top: 4px;
}

/* Color utilities */
.c-cyan { color: var(--accent-cyan); }
.c-green { color: var(--accent-green); }
.c-red { color: var(--accent-red); }
.c-amber { color: var(--accent-amber); }
.c-purple { color: var(--accent-purple); }
.c-muted { color: var(--text-muted); }
.c-primary { color: var(--text-primary); }
.c-secondary { color: var(--text-secondary); }

/* ─── Module Cards ─── */
.mod-card {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    padding: 16px;
    min-height: 140px;
    transition: border-color 0.2s;
}
.mod-card:hover {
    border-color: var(--border-accent);
}
.mod-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
}
.mod-name {
    font-family: var(--font-mono);
    font-size: 13px;
    font-weight: 600;
    color: var(--text-primary);
}
.mod-status {
    width: 8px;
    height: 8px;
    border-radius: 50%;
}
.mod-desc {
    font-size: 11px;
    color: var(--text-muted);
    line-height: 1.5;
    margin-bottom: 10px;
}
.mod-metric {
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    padding: 2px 0;
    border-top: 1px solid rgba(255,255,255,0.02);
}
.mod-metric-label { color: var(--text-muted); }
.mod-metric-value { color: var(--text-secondary); font-family: var(--font-mono); font-weight: 500; }

/* ─── Pipeline Funnel ─── */
.funnel-stage {
    display: flex;
    align-items: center;
    padding: 10px 16px;
    margin-bottom: 4px;
    border-radius: var(--radius-sm);
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    transition: border-color 0.15s;
}
.funnel-stage:hover { border-color: var(--border-accent); }
.funnel-icon {
    width: 28px;
    height: 28px;
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 14px;
    margin-right: 12px;
    flex-shrink: 0;
}
.funnel-label {
    flex: 1;
    font-size: 12px;
    font-weight: 500;
    color: var(--text-primary);
}
.funnel-count {
    font-family: var(--font-mono);
    font-size: 16px;
    font-weight: 700;
    color: var(--accent-cyan);
}
.funnel-arrow {
    text-align: center;
    color: var(--text-muted);
    font-size: 10px;
    margin: 2px 0;
    letter-spacing: 2px;
}

/* ─── Event Log ─── */
.log-wrap {
    background: rgba(4,4,10,0.9);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    padding: 12px 14px;
    font-family: var(--font-mono);
    font-size: 11px;
    max-height: 380px;
    overflow-y: auto;
    line-height: 1.7;
}
.log-wrap::-webkit-scrollbar { width: 4px; }
.log-wrap::-webkit-scrollbar-track { background: transparent; }
.log-wrap::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.08); border-radius: 4px; }
.log-ts { color: var(--text-muted); }
.log-mod { color: var(--accent-cyan); font-weight: 600; }
.log-ok { color: var(--accent-green); }
.log-warn { color: var(--accent-amber); }
.log-err { color: var(--accent-red); }
.log-info { color: var(--text-muted); }
.log-msg { color: var(--text-secondary); }

/* ─── Learner Card ─── */
.learner-stat {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 0;
    border-bottom: 1px solid rgba(255,255,255,0.03);
}
.learner-stat:last-child { border-bottom: none; }
.ls-label { font-size: 12px; color: var(--text-muted); }
.ls-value { font-family: var(--font-mono); font-size: 14px; font-weight: 600; }

/* ─── Section Title ─── */
.sec-title {
    font-family: var(--font-mono);
    font-size: 13px;
    font-weight: 600;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 2px;
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border-subtle);
}

/* ─── Sidebar ─── */
[data-testid="stSidebar"] {
    background: var(--bg-secondary);
    border-right: 1px solid var(--border-subtle);
}
[data-testid="stSidebar"] .stSelectbox label,
[data-testid="stSidebar"] .stRadio label,
[data-testid="stSidebar"] .stCheckbox label {
    color: var(--text-secondary) !important;
    font-size: 12px;
}
[data-testid="stSidebar"] .stSelectbox [data-baseweb="select"] {
    font-size: 12px;
}

/* Sidebar section headers */
.sb-section {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 600;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 2px;
    margin: 16px 0 8px 0;
    padding-bottom: 4px;
    border-bottom: 1px solid rgba(255,255,255,0.04);
}

/* Strategy detail */
.strat-detail {
    font-size: 11px;
    color: var(--text-muted);
    line-height: 1.9;
    padding: 8px 0;
    font-family: var(--font-mono);
}
.strat-detail b { color: var(--text-secondary); }

/* ─── Tables ─── */
.stDataFrame { font-size: 12px; }

/* ─── Buttons ─── */
div.stButton > button {
    background: rgba(0,180,255,0.06);
    color: var(--accent-cyan);
    border: 1px solid rgba(0,180,255,0.15);
    border-radius: 6px;
    font-weight: 600;
    font-size: 12px;
    font-family: var(--font-mono);
    letter-spacing: 0.3px;
    transition: all 0.2s;
}
div.stButton > button:hover {
    background: rgba(0,180,255,0.12);
    border-color: rgba(0,180,255,0.3);
    box-shadow: 0 0 12px rgba(0,180,255,0.08);
}

/* ─── Tabs ─── */
.stTabs [data-baseweb="tab-list"] {
    gap: 0;
    border-bottom: 1px solid var(--border-subtle);
}
.stTabs [data-baseweb="tab"] {
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 500;
    letter-spacing: 0.5px;
    color: var(--text-muted);
    border-bottom: 2px solid transparent;
    padding: 8px 20px;
}
.stTabs [aria-selected="true"] {
    color: var(--accent-cyan) !important;
    border-bottom-color: var(--accent-cyan) !important;
    background: transparent !important;
}

/* ─── Hide Streamlit ─── */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
.stDeployButton {display: none;}

/* ─── Plotly dark override ─── */
.js-plotly-plot .plotly .modebar { display: none !important; }

</style>
"""
st.markdown(_CSS, unsafe_allow_html=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AUTO-REFRESH (preserves session_state)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOREFRESH = True
except ImportError:
    HAS_AUTOREFRESH = False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SESSION STATE INIT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def init_session_state():
    """Initialize session state from current system state (once)."""
    if "initialized" in st.session_state:
        return

    current_mode = getattr(system_state, "mode", "SHADOW") if system_state else "SHADOW"
    current_strategy = strategy.get().name if strategy else "default"

    st.session_state.initialized = True
    st.session_state.mode = current_mode
    st.session_state.strategy = current_strategy
    st.session_state.refresh_interval = 5

init_session_state()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATA LAYER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def get_state() -> Dict[str, Any]:
    if system_state:
        return dict(
            mode=getattr(system_state, "mode", "SHADOW"),
            status=getattr(system_state, "status", "PAUSED"),
            balance=getattr(system_state, "balance_sol", 0.0),
            reserve=getattr(system_state, "solana_reserve", 0.005),
            open_positions=getattr(system_state, "open_positions", 0),
            daily_pnl=getattr(system_state, "daily_pnl", 0.0),
            session_start=getattr(system_state, "session_start", datetime.utcnow()),
        )
    return dict(
        mode="SHADOW", status="OFFLINE", balance=0, reserve=0.005,
        open_positions=0, daily_pnl=0, session_start=datetime.utcnow(),
    )


def get_trades(limit=200) -> pd.DataFrame:
    if db:
        try:
            conn = db.connect()
            return pd.read_sql_query(
                "SELECT id, mint, entry_price, exit_price, profit_pct, status, timestamp "
                "FROM trades ORDER BY timestamp DESC LIMIT ?",
                conn, params=(limit,),
            )
        except Exception:
            pass
    return pd.DataFrame(
        columns=["id", "mint", "entry_price", "exit_price", "profit_pct", "status", "timestamp"]
    )


def get_events(limit=150, module=None) -> List[Dict]:
    if not db:
        return []
    try:
        rows = db.fetch_event_log(limit=limit, module=module)
        return [
            {"id": r[0], "module": r[1], "level": r[2], "type": r[3],
             "message": r[4], "details": r[5], "time": r[6]}
            for r in rows
        ]
    except Exception:
        return []


def get_module_reports() -> Dict[str, Dict]:
    if not db:
        return {}
    try:
        conn = db.connect()
        cursor = conn.execute(
            "SELECT module, status, metrics, created_at FROM module_reports "
            "WHERE id IN (SELECT MAX(id) FROM module_reports GROUP BY module)"
        )
        out = {}
        for mod, status, metrics_json, ts in cursor.fetchall():
            try:
                m = json.loads(metrics_json) if metrics_json else {}
            except Exception:
                m = {}
            out[mod] = {"status": status, "metrics": m, "updated": ts}
        return out
    except Exception:
        return {}


def get_learner_metrics() -> Dict[str, Any]:
    if not db:
        return {}
    try:
        conn = db.connect()
        cursor = conn.execute(
            "SELECT value FROM system_state WHERE key = 'learner_metrics'"
        )
        row = cursor.fetchone()
        if row:
            return json.loads(row[0])
    except Exception:
        pass
    return {}


def get_learner_suggestions(limit=10) -> List[Dict]:
    if not db:
        return []
    try:
        rows = db.fetch_recent_learner_suggestions(limit=limit)
        return [
            {"id": r[0], "suggestion": r[2], "score": r[3], "time": r[4]}
            for r in (rows or [])
        ]
    except Exception:
        return []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MODULE DESCRIPTIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MODULE_INFO = {
    "miner": {
        "name": "Miner",
        "desc": "Real-time token discovery. Connects to Pump.fun WebSocket and polls DexScreener for new memecoins.",
        "icon": "M",
        "color": "#00b4ff",
        "metrics": [
            ("Tokens/min", "mint_counter_last_heartbeat"),
            ("Emitted", "emit_counter_last_heartbeat"),
            ("Queue size", "recent_coin_updates"),
        ],
    },
    "hunter": {
        "name": "Hunter",
        "desc": "Pre-filter and on-chain audit. Validates mint/freeze authority, holder count, and top-10 concentration via RPC.",
        "icon": "H",
        "color": "#8b5cf6",
        "metrics": [
            ("Processed", "processed_count"),
            ("Selected", "selected_count"),
            ("Rejected", "rpc_rejected"),
        ],
    },
    "analyzer": {
        "name": "Analyzer",
        "desc": "Deep analysis engine. LP lock verification, price stability scoring, volume trends, and risk flag detection.",
        "icon": "A",
        "color": "#06b6d4",
        "metrics": [
            ("Audits", "processed_count"),
            ("Signals", "signals_emitted"),
            ("Rejected", "rejected_count"),
        ],
    },
    "risk_guard": {
        "name": "Risk Guard",
        "desc": "Final gatekeeper. Enforces drawdown limits, cooldown, max positions, score threshold, and SOL reserve.",
        "icon": "R",
        "color": "#f59e0b",
        "metrics": [
            ("Processed", "processed_signals"),
            ("Approved", "approvals"),
            ("Vetoed", "vetoes"),
        ],
    },
    "executor": {
        "name": "Executor",
        "desc": "Trade execution via Jupiter/Raydium. Manages Moon Bag, DCA entries, trailing stops, and TX simulation.",
        "icon": "E",
        "color": "#10b981",
        "metrics": [
            ("Trades", "trade_count"),
            ("Moon Bags", "moon_bags"),
            ("Drawdown", "portfolio_drawdown"),
        ],
    },
    "learner": {
        "name": "Learner",
        "desc": "Adaptive learning engine. Tracks win rate, profit factor, and auto-adjusts strategy based on performance.",
        "icon": "L",
        "color": "#ec4899",
        "metrics": [
            ("Feedback", "feedback_count"),
            ("Losses streak", "consecutive_losses"),
            ("Adjustments", "adjustments_made"),
        ],
    },
    "notifier": {
        "name": "Notifier",
        "desc": "Signal broadcaster. Posts trade signals and predictions to Telegram and X (Twitter) via OAuth 1.0a.",
        "icon": "N",
        "color": "#6366f1",
        "metrics": [
            ("Running", "is_running"),
        ],
    },
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SIDEBAR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def render_sidebar() -> Dict[str, Any]:
    s = get_state()

    # Brand
    st.sidebar.markdown(
        '<div style="text-align:center; padding: 8px 0 4px 0">'
        '<span style="font-family: var(--font-mono); font-size: 18px; font-weight: 800; '
        'letter-spacing: 4px; background: linear-gradient(135deg, #00b4ff, #8b5cf6); '
        '-webkit-background-clip: text; -webkit-text-fill-color: transparent;">PREDATOR</span>'
        '<br><span style="font-family: var(--font-mono); font-size: 10px; color: #4a5568; '
        'letter-spacing: 1px;">v3.0 autonomous</span></div>',
        unsafe_allow_html=True,
    )
    st.sidebar.markdown("---")

    # Live status
    status = s["status"]
    status_cls = {
        "RUNNING": "pill-running", "PAUSED": "pill-paused",
        "KILL_SWITCH": "pill-kill",
    }.get(status, "pill-error")
    pulse_cls = {
        "RUNNING": "pulse-green", "PAUSED": "pulse-amber",
    }.get(status, "pulse-red")

    st.sidebar.markdown(
        f'<div style="display:flex; align-items:center; gap:10px; margin-bottom:12px">'
        f'<span class="pill {status_cls}">'
        f'<span class="pulse-dot {pulse_cls}"></span> {status}</span>'
        f'<span class="pill pill-mode">{s["mode"]}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── System Controls ──
    st.sidebar.markdown('<div class="sb-section">System Control</div>', unsafe_allow_html=True)
    c1, c2, c3 = st.sidebar.columns(3)
    if c1.button("Start", use_container_width=True):
        if db:
            db.push_command("set_status", '{"status":"RUNNING"}')
            st.toast("System: RUNNING", icon=":")
    if c2.button("Pause", use_container_width=True):
        if db:
            db.push_command("set_status", '{"status":"PAUSED"}')
            st.toast("System: PAUSED", icon=":")
    if c3.button("Stop", use_container_width=True):
        if db:
            db.push_command("system_cmd", '{"cmd":"stop_all"}')
            st.toast("System: STOPPING", icon=":")

    # ── Mode ──
    st.sidebar.markdown('<div class="sb-section">Trading Mode</div>', unsafe_allow_html=True)
    modes = ["SHADOW", "PAPER", "BETA", "LIVE"]
    mode_idx = modes.index(st.session_state.mode) if st.session_state.mode in modes else 0
    selected_mode = st.sidebar.selectbox(
        "Mode", modes, index=mode_idx,
        key="sb_mode", label_visibility="collapsed",
    )
    if selected_mode != st.session_state.mode:
        st.session_state.mode = selected_mode
        if db:
            db.push_command("set_mode", json.dumps({"mode": selected_mode}))
            st.toast(f"Mode: {selected_mode}", icon=":")

    mode_desc = {
        "SHADOW": "Observe only. No trades executed. Safe for testing.",
        "PAPER": "Virtual balance. Simulates trades with real market data.",
        "BETA": "Real trades with reduced position size (0.1 SOL max).",
        "LIVE": "Full autonomous trading with real funds.",
    }
    st.sidebar.caption(mode_desc.get(selected_mode, ""))

    # ── Strategy ──
    st.sidebar.markdown('<div class="sb-section">Strategy</div>', unsafe_allow_html=True)
    strat_names = list(PROFILES.keys()) if PROFILES else ["default", "conservative", "aggressive"]
    strat_idx = strat_names.index(st.session_state.strategy) if st.session_state.strategy in strat_names else 0
    selected_strat = st.sidebar.selectbox(
        "Strategy", strat_names, index=strat_idx,
        key="sb_strat", label_visibility="collapsed",
    )
    if selected_strat != st.session_state.strategy:
        st.session_state.strategy = selected_strat
        if db:
            db.push_command("strategy_change", json.dumps({"strategy": selected_strat}))
            st.toast(f"Strategy: {selected_strat}", icon=":")

    if PROFILES and selected_strat in PROFILES:
        p = PROFILES[selected_strat]
        st.sidebar.markdown(
            f'<div class="strat-detail">'
            f'Min liquidity: <b>${p.min_liquidity:,.0f}</b><br>'
            f'Min score: <b>{p.risk_min_score}</b><br>'
            f'Position: <b>{p.max_position_size} SOL</b> / max <b>{p.max_open_positions}</b><br>'
            f'SL: <b>{p.stop_loss_pct}%</b> / TP: <b>{p.take_profit_pct}%</b><br>'
            f'Moon Bag: <b>{"ON" if p.moon_bag_enabled else "OFF"}</b> '
            f'({p.moon_bag_sell_pct:.0f}% @ {p.moon_bag_trigger_pct:.0f}%)<br>'
            f'DCA: <b>{"ON" if p.dca_enabled else "OFF"}</b> '
            f'(max {p.dca_max_entries}x @ {p.dca_dip_trigger_pct:.0f}%)<br>'
            f'Cooldown: <b>{p.cooldown_seconds}s</b> / '
            f'Slippage: <b>{p.slippage_bps / 100:.1f}%</b>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── Auto-refresh ──
    st.sidebar.markdown('<div class="sb-section">Refresh</div>', unsafe_allow_html=True)
    refresh_options = {"Off": 0, "3s": 3, "5s": 5, "10s": 10, "30s": 30}
    refresh_labels = list(refresh_options.keys())
    current_interval = st.session_state.get("refresh_interval", 5)
    current_label = next(
        (k for k, v in refresh_options.items() if v == current_interval), "5s"
    )
    current_idx = refresh_labels.index(current_label) if current_label in refresh_labels else 2
    refresh_sel = st.sidebar.selectbox(
        "Auto-refresh", refresh_labels, index=current_idx,
        key="sb_refresh", label_visibility="collapsed",
    )
    st.session_state.refresh_interval = refresh_options[refresh_sel]

    # ── Connection ──
    st.sidebar.markdown("---")
    conn_color = "#10b981" if CONNECTED else "#ef4444"
    conn_text = "Connected" if CONNECTED else "Offline"
    st.sidebar.markdown(
        f'<div style="text-align:center; font-size:11px; color:{conn_color}; '
        f'font-family: var(--font-mono);">{conn_text}</div>',
        unsafe_allow_html=True,
    )

    return s


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HEADER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def render_header(s: Dict):
    status = s["status"]
    status_cls = {
        "RUNNING": "pill-running", "PAUSED": "pill-paused",
        "KILL_SWITCH": "pill-kill",
    }.get(status, "pill-error")
    pulse_cls = {"RUNNING": "pulse-green", "PAUSED": "pulse-amber"}.get(status, "pulse-red")

    try:
        start = s.get("session_start", datetime.utcnow())
        if isinstance(start, str):
            start = datetime.fromisoformat(start)
        delta = datetime.utcnow() - start
        hours = int(delta.total_seconds() // 3600)
        mins = int((delta.total_seconds() % 3600) // 60)
        uptime = f"{hours}h {mins}m"
    except Exception:
        uptime = "N/A"

    st.markdown(
        f'<div class="dash-header">'
        f'<div>'
        f'<span class="dash-logo">PREDATOR</span>'
        f'<span class="dash-version">v3.0</span>'
        f'</div>'
        f'<div class="dash-status-bar">'
        f'<span style="font-family:var(--font-mono);font-size:11px;color:var(--text-muted)">'
        f'Uptime: {uptime}</span>'
        f'<span class="pill pill-mode">{s["mode"]}</span>'
        f'<span class="pill {status_cls}">'
        f'<span class="pulse-dot {pulse_cls}"></span> {status}</span>'
        f'</div></div>',
        unsafe_allow_html=True,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# KPI ROW
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def render_kpis(s: Dict, trades_df: pd.DataFrame):
    cols = st.columns(6)

    # Balance
    with cols[0]:
        st.markdown(
            f'<div class="kpi-card">'
            f'<div class="kpi-label">Balance</div>'
            f'<div class="kpi-value c-cyan">{s["balance"]:.4f}</div>'
            f'<div class="kpi-sub">SOL</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Daily PnL
    pnl = s["daily_pnl"]
    pnl_c = "c-green" if pnl >= 0 else "c-red"
    with cols[1]:
        st.markdown(
            f'<div class="kpi-card">'
            f'<div class="kpi-label">Daily P&L</div>'
            f'<div class="kpi-value {pnl_c}">{pnl:+.4f}</div>'
            f'<div class="kpi-sub">SOL</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Open Positions
    with cols[2]:
        st.markdown(
            f'<div class="kpi-card">'
            f'<div class="kpi-label">Open Positions</div>'
            f'<div class="kpi-value c-primary">{s["open_positions"]}</div>'
            f'<div class="kpi-sub">active</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Win Rate
    closed = (
        trades_df[trades_df["status"].str.contains("CLOSED", na=False)]
        if not trades_df.empty else pd.DataFrame()
    )
    total = len(closed)
    wins = len(closed[closed["profit_pct"] > 0]) if total > 0 else 0
    wr = (wins / total * 100) if total > 0 else 0
    wr_c = "c-green" if wr >= 50 else "c-amber" if wr >= 30 else "c-red"
    with cols[3]:
        st.markdown(
            f'<div class="kpi-card">'
            f'<div class="kpi-label">Win Rate</div>'
            f'<div class="kpi-value {wr_c}">{wr:.0f}%</div>'
            f'<div class="kpi-sub">{wins}W / {total - wins}L of {total}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Total Trades
    with cols[4]:
        st.markdown(
            f'<div class="kpi-card">'
            f'<div class="kpi-label">Total Trades</div>'
            f'<div class="kpi-value c-primary">{len(trades_df)}</div>'
            f'<div class="kpi-sub">all time</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Profit Factor
    if total > 0:
        total_wins_val = closed[closed["profit_pct"] > 0]["profit_pct"].sum()
        total_losses_val = abs(closed[closed["profit_pct"] < 0]["profit_pct"].sum())
        pf = total_wins_val / total_losses_val if total_losses_val > 0 else 0
        pf_c = "c-green" if pf >= 1.5 else "c-amber" if pf >= 1.0 else "c-red"
    else:
        pf = 0
        pf_c = "c-muted"
    with cols[5]:
        st.markdown(
            f'<div class="kpi-card">'
            f'<div class="kpi-label">Profit Factor</div>'
            f'<div class="kpi-value {pf_c}">{pf:.2f}</div>'
            f'<div class="kpi-sub">wins / losses</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PIPELINE FUNNEL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def render_pipeline(reports: Dict):
    st.markdown('<div class="sec-title">Token Pipeline</div>', unsafe_allow_html=True)

    stages = [
        ("Discovered", "miner", "emit_counter_last_heartbeat",
         "#00b4ff", "Tokens found by Miner this minute"),
        ("Filtered", "hunter", "selected_count",
         "#8b5cf6", "Passed Hunter pre-filter + RPC audit"),
        ("Analyzed", "analyzer", "signals_emitted",
         "#06b6d4", "Passed deep analysis, signal emitted"),
        ("Approved", "risk_guard", "approvals",
         "#f59e0b", "Approved by Risk Guard for execution"),
        ("Executed", "executor", "trade_count",
         "#10b981", "Actual trades placed"),
    ]

    for i, (label, mod, metric, color, tooltip) in enumerate(stages):
        r = reports.get(mod, {})
        m = r.get("metrics", {})
        count = m.get(metric, 0) or 0

        st.markdown(
            f'<div class="funnel-stage" title="{tooltip}">'
            f'<div class="funnel-icon" style="background:rgba({_hex_to_rgb(color)},0.12);'
            f'color:{color};font-family:var(--font-mono);font-weight:700">'
            f'{label[0]}</div>'
            f'<div class="funnel-label">{label}</div>'
            f'<div class="funnel-count" style="color:{color}">{count}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        if i < len(stages) - 1:
            st.markdown(
                '<div class="funnel-arrow">|</div>',
                unsafe_allow_html=True,
            )

    # Show veto reasons
    rg = reports.get("risk_guard", {}).get("metrics", {})
    vetoes = rg.get("vetoes", 0) or 0
    if vetoes > 0:
        st.markdown(
            f'<div style="margin-top:8px; font-size:11px; color:var(--accent-amber); '
            f'font-family:var(--font-mono)">'
            f'Risk Guard vetoed {vetoes} signals</div>',
            unsafe_allow_html=True,
        )


def _hex_to_rgb(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    return f"{int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# EQUITY CHART
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def render_equity_chart(trades_df: pd.DataFrame):
    st.markdown('<div class="sec-title">Equity Curve</div>', unsafe_allow_html=True)

    if trades_df.empty:
        st.markdown(
            '<div style="text-align:center;padding:40px 0;color:var(--text-muted);'
            'font-size:13px">No trade data yet. The bot will populate this chart '
            'as positions are opened and closed.</div>',
            unsafe_allow_html=True,
        )
        return

    closed = trades_df[trades_df["status"].str.contains("CLOSED", na=False)].copy()
    if closed.empty:
        st.markdown(
            '<div style="text-align:center;padding:40px 0;color:var(--text-muted);'
            'font-size:13px">Positions are open but none closed yet. '
            'Equity curve appears after the first exit.</div>',
            unsafe_allow_html=True,
        )
        return

    closed = closed.sort_values("timestamp")
    closed["equity"] = closed["profit_pct"].cumsum()
    closed["ts"] = pd.to_datetime(closed["timestamp"])

    fig = go.Figure()

    # Area fill
    fig.add_trace(go.Scatter(
        x=closed["ts"], y=closed["equity"],
        mode="lines",
        fill="tozeroy",
        line=dict(color="#00b4ff", width=2),
        fillcolor="rgba(0,180,255,0.06)",
        hovertemplate="Equity: %{y:.2f}%<extra></extra>",
    ))

    # Zero line
    fig.add_hline(y=0, line_dash="dot", line_color="rgba(255,255,255,0.08)", line_width=1)

    fig.update_layout(
        height=260,
        margin=dict(l=0, r=0, t=8, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(
            showgrid=False, color="#4a5568",
            tickfont=dict(size=10, family="JetBrains Mono"),
        ),
        yaxis=dict(
            showgrid=True, gridcolor="rgba(255,255,255,0.03)",
            color="#4a5568", ticksuffix="%",
            tickfont=dict(size=10, family="JetBrains Mono"),
        ),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#0c0c16", font_size=11, font_family="JetBrains Mono"),
    )
    st.plotly_chart(fig, use_container_width=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MODULE STATUS CARDS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def render_module_cards(reports: Dict):
    st.markdown('<div class="sec-title">Modules</div>', unsafe_allow_html=True)

    mod_keys = ["miner", "hunter", "analyzer", "risk_guard", "executor", "learner", "notifier"]
    cols = st.columns(len(mod_keys))

    for i, mod_key in enumerate(mod_keys):
        info = MODULE_INFO.get(mod_key, {})
        r = reports.get(mod_key, {})
        status = r.get("status", "offline")
        metrics = r.get("metrics", {})
        is_running = status == "running"

        dot_color = "#10b981" if is_running else "#ef4444"
        card_border = f"border-top: 2px solid {info.get('color', '#4a5568')};" if is_running else ""

        metrics_html = ""
        for label, key in info.get("metrics", []):
            val = metrics.get(key, 0)
            if val is None:
                val = 0
            if key == "portfolio_drawdown" and val != 0:
                val = f"{val:.1f}%"
            elif key == "is_running":
                val = "Yes" if val else "No"
            metrics_html += (
                f'<div class="mod-metric">'
                f'<span class="mod-metric-label">{label}</span>'
                f'<span class="mod-metric-value">{val}</span></div>'
            )

        with cols[i]:
            st.markdown(
                f'<div class="mod-card" style="{card_border}">'
                f'<div class="mod-header">'
                f'<span class="mod-name">{info.get("name", mod_key)}</span>'
                f'<span class="mod-status" style="background:{dot_color}"></span>'
                f'</div>'
                f'<div class="mod-desc">{info.get("desc", "")}</div>'
                f'{metrics_html}'
                f'</div>',
                unsafe_allow_html=True,
            )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# EVENT LOG
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def render_event_log():
    lc1, lc2 = st.columns([4, 1])
    with lc1:
        st.markdown('<div class="sec-title">Live Event Log</div>', unsafe_allow_html=True)
    with lc2:
        filter_module = st.selectbox(
            "Module",
            ["All", "Miner", "Hunter", "Analyzer", "RiskGuard", "Executor", "Learner", "Notifier", "System"],
            label_visibility="collapsed",
            key="log_filter",
        )

    mod_filter = None if filter_module == "All" else filter_module
    events = get_events(limit=100, module=mod_filter)

    if events:
        lines = []
        for e in events:
            ts = str(e["time"])[-8:] if e["time"] else "        "
            level = e.get("level", "INFO")
            level_cls = {
                "SUCCESS": "log-ok", "WARNING": "log-warn",
                "ERROR": "log-err",
            }.get(level, "log-info")
            line = (
                f'<span class="log-ts">{ts}</span> '
                f'<span class="log-mod">[{e["module"]}]</span> '
                f'<span class="{level_cls}">{e["type"]}</span> '
                f'<span class="log-msg">{e["message"]}</span>'
            )
            lines.append(line)
        log_html = "<br>".join(lines)
    else:
        log_html = (
            '<span class="log-info">Waiting for events... '
            'Start the bot with python main.py to see live activity.</span>'
        )

    st.markdown(f'<div class="log-wrap">{log_html}</div>', unsafe_allow_html=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LEARNER INSIGHTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def render_learner_insights():
    st.markdown('<div class="sec-title">Learner Insights</div>', unsafe_allow_html=True)

    metrics = get_learner_metrics()

    if not metrics or metrics.get("trades_count", 0) == 0:
        st.markdown(
            '<div style="padding:20px; text-align:center; color:var(--text-muted); '
            'font-size:12px">'
            'No learning data yet. The Learner module collects data from each '
            'closed trade, tracking win rate, profit factor, and exit reasons. '
            'It auto-adjusts strategy when patterns emerge (e.g. 3+ consecutive losses '
            'triggers switch to conservative).'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    win = metrics.get("win_count", 0)
    loss = metrics.get("loss_count", 0)
    total = win + loss
    wr = (win / total * 100) if total > 0 else 0
    wr_c = "c-green" if wr >= 50 else "c-amber" if wr >= 30 else "c-red"

    total_wins_val = metrics.get("total_pnl", 0)
    pnl_c = "c-green" if total_wins_val >= 0 else "c-red"

    exit_reasons = metrics.get("exit_reasons", {})
    exit_html = ""
    for reason, count in sorted(exit_reasons.items(), key=lambda x: -x[1]):
        pct = count / total * 100 if total > 0 else 0
        exit_html += (
            f'<div class="mod-metric">'
            f'<span class="mod-metric-label">{reason}</span>'
            f'<span class="mod-metric-value">{count} ({pct:.0f}%)</span></div>'
        )

    stats = [
        ("Total Trades", f"{total}", "c-primary"),
        ("Win Rate", f"{wr:.1f}%", wr_c),
        ("Total PnL", f"{total_wins_val:+.2f}%", pnl_c),
        ("Total PnL SOL", f"{metrics.get('total_pnl_sol', 0):+.4f}", pnl_c),
        ("Best Trade", f"{metrics.get('best_trade_pct', 0):+.2f}%", "c-green"),
        ("Worst Trade", f"{metrics.get('worst_trade_pct', 0):+.2f}%", "c-red"),
    ]

    stats_html = ""
    for label, value, color in stats:
        stats_html += (
            f'<div class="learner-stat">'
            f'<span class="ls-label">{label}</span>'
            f'<span class="ls-value {color}">{value}</span></div>'
        )

    st.markdown(f'<div class="g-card">{stats_html}</div>', unsafe_allow_html=True)

    if exit_html:
        st.markdown(
            f'<div style="font-size:11px;color:var(--text-muted);margin:8px 0 4px 0;'
            f'font-weight:600">Exit Reasons</div>'
            f'<div class="g-card">{exit_html}</div>',
            unsafe_allow_html=True,
        )

    # Recent suggestions
    suggestions = get_learner_suggestions(limit=5)
    if suggestions:
        st.markdown(
            '<div style="font-size:11px;color:var(--text-muted);margin:8px 0 4px 0;'
            'font-weight:600">Recent Suggestions</div>',
            unsafe_allow_html=True,
        )
        for sug in suggestions:
            ts = str(sug.get("time", ""))[-8:]
            text = sug.get("suggestion", "")
            if len(text) > 80:
                text = text[:80] + "..."
            st.markdown(
                f'<div style="font-size:11px;font-family:var(--font-mono);'
                f'padding:4px 0;color:var(--text-secondary)">'
                f'<span style="color:var(--text-muted)">{ts}</span> {text}</div>',
                unsafe_allow_html=True,
            )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TRADE TABLE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def render_trade_table(trades_df: pd.DataFrame):
    st.markdown('<div class="sec-title">Trade History</div>', unsafe_allow_html=True)

    if trades_df.empty:
        st.markdown(
            '<div style="text-align:center;padding:30px 0;color:var(--text-muted);'
            'font-size:12px">'
            'No trades recorded yet. In SHADOW mode, simulated trades appear here. '
            'Check the Event Log for token discovery and filtering activity.</div>',
            unsafe_allow_html=True,
        )
        return

    display_df = trades_df.head(50).copy()
    display_df["mint"] = display_df["mint"].str[:12] + "..."
    display_df["profit_pct"] = display_df["profit_pct"].apply(
        lambda x: f"{x:+.2f}%" if pd.notna(x) else "--"
    )
    display_df["entry_price"] = display_df["entry_price"].apply(
        lambda x: f"{x:.8f}" if pd.notna(x) and x > 0 else "--"
    )
    display_df["exit_price"] = display_df["exit_price"].apply(
        lambda x: f"{x:.8f}" if pd.notna(x) and x > 0 else "--"
    )

    st.dataframe(
        display_df[["timestamp", "mint", "status", "entry_price", "exit_price", "profit_pct"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "timestamp": st.column_config.TextColumn("Time", width="medium"),
            "mint": st.column_config.TextColumn("Token", width="medium"),
            "status": st.column_config.TextColumn("Status", width="medium"),
            "entry_price": st.column_config.TextColumn("Entry", width="small"),
            "exit_price": st.column_config.TextColumn("Exit", width="small"),
            "profit_pct": st.column_config.TextColumn("P&L", width="small"),
        },
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RECENT TRADES CHART
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def render_recent_trades_chart(trades_df: pd.DataFrame):
    if trades_df.empty or len(trades_df) < 1:
        return

    recent = trades_df.head(20).copy()
    recent["profit"] = recent["profit_pct"].fillna(0)
    recent["label"] = recent["mint"].str[:8] + ".."
    recent["color"] = recent["profit"].apply(
        lambda x: "#10b981" if x > 0 else "#ef4444"
    )

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=recent["label"],
        x=recent["profit"],
        orientation="h",
        marker_color=recent["color"].tolist(),
        hovertemplate="%{y}: %{x:.2f}%<extra></extra>",
    ))

    fig.add_vline(x=0, line_color="rgba(255,255,255,0.08)", line_width=1)

    fig.update_layout(
        height=260,
        margin=dict(l=0, r=0, t=8, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(
            showgrid=True, gridcolor="rgba(255,255,255,0.03)",
            color="#4a5568", ticksuffix="%",
            tickfont=dict(size=10, family="JetBrains Mono"),
        ),
        yaxis=dict(
            showgrid=False, color="#4a5568", autorange="reversed",
            tickfont=dict(size=10, family="JetBrains Mono"),
        ),
        hoverlabel=dict(bgcolor="#0c0c16", font_size=11, font_family="JetBrains Mono"),
    )
    st.plotly_chart(fig, use_container_width=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def main():
    # Auto-refresh (session_state safe)
    interval = st.session_state.get("refresh_interval", 5)
    if HAS_AUTOREFRESH and interval > 0:
        st_autorefresh(interval=interval * 1000, limit=None, key="auto_refresh")

    # Sidebar
    s = render_sidebar()
    reports = get_module_reports()
    trades = get_trades()

    # Header
    render_header(s)

    # ── Tabs ──
    tab_overview, tab_trades, tab_learner = st.tabs([
        "Overview", "Trades", "Learner",
    ])

    # ════════════ TAB: OVERVIEW ════════════
    with tab_overview:
        render_kpis(s, trades)
        st.markdown("")

        col_chart, col_pipe = st.columns([3, 1])
        with col_chart:
            render_equity_chart(trades)
        with col_pipe:
            render_pipeline(reports)

        st.markdown("")
        render_module_cards(reports)
        st.markdown("")
        render_event_log()

    # ════════════ TAB: TRADES ════════════
    with tab_trades:
        render_kpis(s, trades)
        st.markdown("")

        col_table, col_bars = st.columns([3, 2])
        with col_table:
            render_trade_table(trades)
        with col_bars:
            st.markdown('<div class="sec-title">Recent P&L</div>', unsafe_allow_html=True)
            render_recent_trades_chart(trades)

    # ════════════ TAB: LEARNER ════════════
    with tab_learner:
        col_insights, col_log = st.columns([1, 2])
        with col_insights:
            render_learner_insights()
        with col_log:
            # Show learner-specific events
            st.markdown('<div class="sec-title">Learner Activity Log</div>', unsafe_allow_html=True)
            learner_events = get_events(limit=50, module="Learner")
            if learner_events:
                lines = []
                for e in learner_events:
                    ts = str(e["time"])[-8:] if e["time"] else "        "
                    level = e.get("level", "INFO")
                    level_cls = {
                        "SUCCESS": "log-ok", "WARNING": "log-warn",
                        "ERROR": "log-err",
                    }.get(level, "log-info")
                    line = (
                        f'<span class="log-ts">{ts}</span> '
                        f'<span class="{level_cls}">{e["type"]}</span> '
                        f'<span class="log-msg">{e["message"]}</span>'
                    )
                    lines.append(line)
                st.markdown(
                    f'<div class="log-wrap">{"<br>".join(lines)}</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<div style="padding:30px;text-align:center;color:var(--text-muted);'
                    'font-size:12px">'
                    'The Learner collects data from every closed position. '
                    'After 10 trades, it generates a feedback report. '
                    'Auto-adjustments happen when:<br><br>'
                    '<b style="color:var(--accent-amber)">3+ consecutive losses</b> '
                    '= switch to conservative<br>'
                    '<b style="color:var(--accent-green)">5+ consecutive wins</b> '
                    '+ high profit factor = upgrade strategy<br>'
                    '<b style="color:var(--accent-red)">SL rate > 40%</b> '
                    '= widen stop loss suggestion'
                    '</div>',
                    unsafe_allow_html=True,
                )


main()

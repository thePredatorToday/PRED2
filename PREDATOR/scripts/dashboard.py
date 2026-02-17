"""
PREDATOR Dashboard v2.0 – Minimalistický, plný informací, plné ovládání.

Run: streamlit run scripts/dashboard.py --server.port 8501
"""

from __future__ import annotations

import json
import os
import sys
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

# Přidáme PREDATOR root do sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from core.config import settings
    from core.system_state import state as system_state
    from core.database import db
    from core.strategy import PROFILES
    CONNECTED = True
except Exception:
    settings = None
    system_state = None
    db = None
    PROFILES = {}
    CONNECTED = False

LOG = logging.getLogger("Predator.Dashboard")

# ━━━━━━━━━━━━━━━━━━ PAGE CONFIG ━━━━━━━━━━━━━━━━━━
st.set_page_config(page_title="PREDATOR", layout="wide", page_icon="P")

# ━━━━━━━━━━━━━━━━━━ CSS ━━━━━━━━━━━━━━━━━━
_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;600;800&family=Inter:wght@300;400;600;800&display=swap');

html, body, [data-testid='stAppViewContainer'] {
    font-family: 'Inter', sans-serif;
}

.stApp {
    background: #0a0a0f;
    color: #c8d6e5;
}

/* Remove default padding */
.block-container { padding-top: 1rem; padding-bottom: 0; }

/* Glass card */
.card {
    background: rgba(255,255,255,0.02);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 8px;
}
.card-accent {
    border-left: 3px solid #00d4ff;
}

/* KPI */
.kpi { text-align: center; }
.kpi-value { font-size: 28px; font-weight: 800; font-family: 'JetBrains Mono', monospace; }
.kpi-label { font-size: 11px; color: #6b7c93; text-transform: uppercase; letter-spacing: 1px; margin-top: 2px; }
.kpi-delta { font-size: 13px; font-family: 'JetBrains Mono', monospace; }

.cyan { color: #00d4ff; }
.green { color: #00e676; }
.red { color: #ff5252; }
.amber { color: #ffab40; }
.muted { color: #4a5568; }
.white { color: #e2e8f0; }

/* Status pills */
.pill {
    display: inline-block; padding: 3px 10px; border-radius: 999px;
    font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;
}
.pill-running { background: rgba(0,230,118,0.15); color: #00e676; border: 1px solid rgba(0,230,118,0.3); }
.pill-paused { background: rgba(255,171,64,0.15); color: #ffab40; border: 1px solid rgba(255,171,64,0.3); }
.pill-error { background: rgba(255,82,82,0.15); color: #ff5252; border: 1px solid rgba(255,82,82,0.3); }
.pill-shadow { background: rgba(0,212,255,0.1); color: #00d4ff; border: 1px solid rgba(0,212,255,0.3); }

/* Event log */
.log-container {
    background: #050508;
    border: 1px solid rgba(255,255,255,0.04);
    border-radius: 8px;
    padding: 12px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    max-height: 400px;
    overflow-y: auto;
    line-height: 1.6;
}
.log-time { color: #4a5568; }
.log-module { color: #00d4ff; font-weight: 600; }
.log-success { color: #00e676; }
.log-warning { color: #ffab40; }
.log-error { color: #ff5252; }
.log-info { color: #8892a0; }
.log-msg { color: #c8d6e5; }

/* Sidebar */
[data-testid="stSidebar"] {
    background: #0d0d14;
    border-right: 1px solid rgba(255,255,255,0.04);
}
[data-testid="stSidebar"] .stSelectbox label,
[data-testid="stSidebar"] .stCheckbox label { color: #8892a0; font-size: 12px; }

/* Tables */
.stDataFrame { font-size: 12px; }

/* Buttons */
div.stButton > button {
    background: rgba(0,212,255,0.08);
    color: #00d4ff;
    border: 1px solid rgba(0,212,255,0.2);
    border-radius: 6px;
    font-weight: 600;
    font-size: 12px;
}
div.stButton > button:hover {
    background: rgba(0,212,255,0.15);
    border-color: rgba(0,212,255,0.4);
}

/* Hide streamlit branding */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}

</style>
"""
st.markdown(_CSS, unsafe_allow_html=True)


# ━━━━━━━━━━━━━━━━━━ DATA ━━━━━━━━━━━━━━━━━━

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
    return dict(mode="SHADOW", status="OFFLINE", balance=0, reserve=0.005,
                open_positions=0, daily_pnl=0, session_start=datetime.utcnow())


def get_trades(limit=200) -> pd.DataFrame:
    if db:
        try:
            conn = db.connect()
            return pd.read_sql_query(
                "SELECT id, mint, entry_price, exit_price, profit_pct, status, timestamp "
                "FROM trades ORDER BY timestamp DESC LIMIT ?", conn, params=(limit,)
            )
        except Exception:
            pass
    return pd.DataFrame(columns=["id","mint","entry_price","exit_price","profit_pct","status","timestamp"])


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


# ━━━━━━━━━━━━━━━━━━ SIDEBAR ━━━━━━━━━━━━━━━━━━

def render_sidebar():
    s = get_state()

    st.sidebar.markdown("### PREDATOR")
    st.sidebar.caption(f"v2.0 | {'Connected' if CONNECTED else 'Offline'}")
    st.sidebar.markdown("---")

    # Status
    status_class = {"RUNNING": "pill-running", "PAUSED": "pill-paused"}.get(
        s["status"], "pill-error"
    )
    mode_class = "pill-shadow"
    st.sidebar.markdown(
        f'<span class="pill {status_class}">{s["status"]}</span> '
        f'<span class="pill {mode_class}">{s["mode"]}</span>',
        unsafe_allow_html=True,
    )
    st.sidebar.markdown("")

    # Controls
    st.sidebar.markdown("##### System")
    c1, c2, c3 = st.sidebar.columns(3)
    if c1.button("Start"):
        if db: db.push_command("set_status", '{"status":"RUNNING"}')
        st.sidebar.success("OK")
    if c2.button("Pause"):
        if db: db.push_command("set_status", '{"status":"PAUSED"}')
        st.sidebar.warning("OK")
    if c3.button("Stop"):
        if db: db.push_command("system_cmd", '{"cmd":"stop_all"}')
        st.sidebar.error("OK")

    st.sidebar.markdown("---")

    # Mode
    st.sidebar.markdown("##### Mode")
    mode = st.sidebar.selectbox("Trading mode", ["SHADOW", "PAPER", "BETA", "LIVE"],
                                index=["SHADOW","PAPER","BETA","LIVE"].index(s["mode"])
                                if s["mode"] in ["SHADOW","PAPER","BETA","LIVE"] else 0,
                                label_visibility="collapsed")
    if st.sidebar.button("Apply Mode"):
        if db:
            db.push_command("set_mode", json.dumps({"mode": mode}))
            st.sidebar.success(f"Mode -> {mode}")

    # Strategy
    st.sidebar.markdown("##### Strategy")
    strat_names = list(PROFILES.keys()) if PROFILES else ["default", "conservative", "aggressive"]
    strat = st.sidebar.selectbox("Strategy profile", strat_names, label_visibility="collapsed")
    if st.sidebar.button("Apply Strategy"):
        if db:
            db.push_command("strategy_change", json.dumps({"strategy": strat}))
            st.sidebar.success(f"Strategy -> {strat}")

    # Strategy detail
    if PROFILES and strat in PROFILES:
        p = PROFILES[strat]
        st.sidebar.markdown(
            f"<div style='font-size:11px; color:#6b7c93; line-height:1.8'>"
            f"Min liquidity: <b>${p.min_liquidity:,.0f}</b><br>"
            f"Min score: <b>{p.risk_min_score}</b><br>"
            f"Position: <b>{p.max_position_size} SOL</b><br>"
            f"SL: <b>{p.stop_loss_pct}%</b> / TP: <b>{p.take_profit_pct}%</b><br>"
            f"Max positions: <b>{p.max_open_positions}</b><br>"
            f"Slippage: <b>{p.slippage_bps/100:.1f}%</b>"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.sidebar.markdown("---")

    # Module toggles
    st.sidebar.markdown("##### Modules")
    reports = get_module_reports()
    modules = ["miner", "hunter", "analyzer", "risk_guard", "executor", "learner", "notifier"]
    for mod in modules:
        r = reports.get(mod, {})
        status = r.get("status", "unknown")
        icon = "●" if status == "running" else "○"
        color = "#00e676" if status == "running" else "#4a5568"
        st.sidebar.markdown(
            f"<span style='color:{color};font-size:10px'>{icon}</span> "
            f"<span style='font-size:12px'>{mod}</span>",
            unsafe_allow_html=True,
        )

    st.sidebar.markdown("---")
    st.sidebar.markdown("##### Auto-refresh")
    refresh = st.sidebar.selectbox("Interval", ["Off", "5s", "10s", "30s"], index=1,
                                   label_visibility="collapsed")
    if refresh != "Off":
        secs = int(refresh.replace("s", ""))
        st.markdown(
            f'<meta http-equiv="refresh" content="{secs}">',
            unsafe_allow_html=True,
        )

    return s


# ━━━━━━━━━━━━━━━━━━ KPI ROW ━━━━━━━━━━━━━━━━━━

def render_kpis(s: Dict, trades_df: pd.DataFrame):
    cols = st.columns(6)

    # Balance
    with cols[0]:
        st.markdown(
            f'<div class="card kpi">'
            f'<div class="kpi-value cyan">{s["balance"]:.4f}</div>'
            f'<div class="kpi-label">Balance SOL</div></div>',
            unsafe_allow_html=True,
        )

    # Daily P&L
    pnl = s["daily_pnl"]
    pnl_color = "green" if pnl >= 0 else "red"
    with cols[1]:
        st.markdown(
            f'<div class="card kpi">'
            f'<div class="kpi-value {pnl_color}">{pnl:+.4f}</div>'
            f'<div class="kpi-label">Daily P&L SOL</div></div>',
            unsafe_allow_html=True,
        )

    # Open positions
    with cols[2]:
        st.markdown(
            f'<div class="card kpi">'
            f'<div class="kpi-value white">{s["open_positions"]}</div>'
            f'<div class="kpi-label">Open Positions</div></div>',
            unsafe_allow_html=True,
        )

    # Win rate
    closed = trades_df[trades_df["status"].str.contains("CLOSED", na=False)] if not trades_df.empty else pd.DataFrame()
    total = len(closed)
    wins = len(closed[closed["profit_pct"] > 0]) if total > 0 else 0
    wr = (wins / total * 100) if total > 0 else 0
    wr_color = "green" if wr >= 50 else "amber" if wr >= 30 else "red"
    with cols[3]:
        st.markdown(
            f'<div class="card kpi">'
            f'<div class="kpi-value {wr_color}">{wr:.0f}%</div>'
            f'<div class="kpi-label">Win Rate ({wins}/{total})</div></div>',
            unsafe_allow_html=True,
        )

    # Total trades
    with cols[4]:
        st.markdown(
            f'<div class="card kpi">'
            f'<div class="kpi-value white">{len(trades_df)}</div>'
            f'<div class="kpi-label">Total Trades</div></div>',
            unsafe_allow_html=True,
        )

    # Uptime
    try:
        start = s.get("session_start", datetime.utcnow())
        if isinstance(start, str):
            start = datetime.fromisoformat(start)
        delta = datetime.utcnow() - start
        hours = int(delta.total_seconds() // 3600)
        mins = int((delta.total_seconds() % 3600) // 60)
        uptime_str = f"{hours}h {mins}m"
    except Exception:
        uptime_str = "N/A"
    with cols[5]:
        st.markdown(
            f'<div class="card kpi">'
            f'<div class="kpi-value muted">{uptime_str}</div>'
            f'<div class="kpi-label">Uptime</div></div>',
            unsafe_allow_html=True,
        )


# ━━━━━━━━━━━━━━━━━━ CHARTS ━━━━━━━━━━━━━━━━━━

def render_charts(trades_df: pd.DataFrame):
    c1, c2 = st.columns([2, 1])

    with c1:
        st.markdown('<div class="card card-accent">', unsafe_allow_html=True)
        st.markdown("**Equity Curve**")
        if not trades_df.empty:
            closed = trades_df[trades_df["status"].str.contains("CLOSED", na=False)].copy()
            if not closed.empty:
                closed = closed.sort_values("timestamp")
                closed["equity"] = closed["profit_pct"].cumsum()
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=pd.to_datetime(closed["timestamp"]),
                    y=closed["equity"],
                    mode="lines",
                    fill="tozeroy",
                    line=dict(color="#00d4ff", width=2),
                    fillcolor="rgba(0,212,255,0.05)",
                ))
                fig.update_layout(
                    height=250, margin=dict(l=0, r=0, t=10, b=0),
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    xaxis=dict(showgrid=False, color="#4a5568"),
                    yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.03)", color="#4a5568"),
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.caption("No closed trades yet")
        else:
            st.caption("No trade data")
        st.markdown("</div>", unsafe_allow_html=True)

    with c2:
        st.markdown('<div class="card card-accent">', unsafe_allow_html=True)
        st.markdown("**Recent Trades**")
        if not trades_df.empty:
            recent = trades_df.head(20)
            fig = go.Figure()
            colors = ["#00e676" if v > 0 else "#ff5252" for v in recent["profit_pct"].fillna(0)]
            fig.add_trace(go.Bar(
                y=recent["mint"].str[:8] + "...",
                x=recent["profit_pct"].fillna(0),
                orientation="h",
                marker_color=colors,
            ))
            fig.update_layout(
                height=250, margin=dict(l=0, r=0, t=10, b=0),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.03)", color="#4a5568",
                           title="Profit %"),
                yaxis=dict(showgrid=False, color="#4a5568", autorange="reversed"),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("No trades")
        st.markdown("</div>", unsafe_allow_html=True)


# ━━━━━━━━━━━━━━━━━━ EVENT LOG ━━━━━━━━━━━━━━━━━━

def render_event_log():
    st.markdown('<div class="card card-accent">', unsafe_allow_html=True)

    lc1, lc2 = st.columns([3, 1])
    with lc1:
        st.markdown("**Live Event Log**")
    with lc2:
        filter_module = st.selectbox(
            "Filter", ["All", "Miner", "Hunter", "Analyzer", "RiskGuard", "Executor", "Learner", "Notifier"],
            label_visibility="collapsed",
        )

    mod_filter = None if filter_module == "All" else filter_module
    events = get_events(limit=100, module=mod_filter)

    if events:
        lines = []
        for e in events:
            ts = str(e["time"])[-8:] if e["time"] else "        "
            level = e.get("level", "INFO")
            level_class = {
                "SUCCESS": "log-success", "WARNING": "log-warning",
                "ERROR": "log-error",
            }.get(level, "log-info")
            line = (
                f'<span class="log-time">{ts}</span> '
                f'<span class="log-module">[{e["module"]}]</span> '
                f'<span class="{level_class}">{e["type"]}</span> '
                f'<span class="log-msg">{e["message"]}</span>'
            )
            lines.append(line)
        log_html = "<br>".join(lines)
    else:
        log_html = '<span class="log-info">Waiting for events...</span>'

    st.markdown(f'<div class="log-container">{log_html}</div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


# ━━━━━━━━━━━━━━━━━━ MODULE STATUS ━━━━━━━━━━━━━━━━━━

def render_modules():
    st.markdown('<div class="card card-accent">', unsafe_allow_html=True)
    st.markdown("**Module Status**")

    reports = get_module_reports()
    modules = ["miner", "hunter", "analyzer", "risk_guard", "executor", "learner", "notifier"]

    cols = st.columns(len(modules))
    for i, mod in enumerate(modules):
        r = reports.get(mod, {})
        status = r.get("status", "offline")
        metrics = r.get("metrics", {})
        is_running = status == "running"

        dot_color = "#00e676" if is_running else "#ff5252"
        with cols[i]:
            st.markdown(
                f'<div style="text-align:center">'
                f'<div style="color:{dot_color};font-size:20px">●</div>'
                f'<div style="font-size:12px;font-weight:600;color:#c8d6e5">{mod}</div>'
                f'<div style="font-size:10px;color:#6b7c93">{status}</div>',
                unsafe_allow_html=True,
            )

            # Hlavní metriky
            for key in ["processed_count", "selected_count", "trade_count",
                         "mint_counter_last_heartbeat", "feedback_count"]:
                if key in metrics and metrics[key]:
                    label = key.replace("_", " ").replace("count", "").strip()
                    st.markdown(
                        f'<div style="font-size:10px;color:#4a5568">'
                        f'{label}: <span style="color:#8892a0">{metrics[key]}</span></div>',
                        unsafe_allow_html=True,
                    )

            st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)


# ━━━━━━━━━━━━━━━━━━ TRADE TABLE ━━━━━━━━━━━━━━━━━━

def render_trade_table(trades_df: pd.DataFrame):
    st.markdown('<div class="card card-accent">', unsafe_allow_html=True)
    st.markdown("**Trade History**")

    if not trades_df.empty:
        display_df = trades_df.head(50).copy()
        display_df["mint"] = display_df["mint"].str[:12] + "..."
        display_df["profit_pct"] = display_df["profit_pct"].apply(
            lambda x: f"{x:+.2f}%" if pd.notna(x) else "—"
        )
        display_df["entry_price"] = display_df["entry_price"].apply(
            lambda x: f"{x:.8f}" if pd.notna(x) and x > 0 else "—"
        )
        display_df["exit_price"] = display_df["exit_price"].apply(
            lambda x: f"{x:.8f}" if pd.notna(x) and x > 0 else "—"
        )
        st.dataframe(
            display_df[["timestamp", "mint", "status", "entry_price", "exit_price", "profit_pct"]],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No trades recorded yet")

    st.markdown("</div>", unsafe_allow_html=True)


# ━━━━━━━━━━━━━━━━━━ MAIN ━━━━━━━━━━━━━━━━━━

def main():
    s = render_sidebar()
    trades = get_trades()

    # KPIs
    render_kpis(s, trades)

    st.markdown("")

    # Charts
    render_charts(trades)

    st.markdown("")

    # Module status
    render_modules()

    st.markdown("")

    # Event log
    render_event_log()

    st.markdown("")

    # Trade table
    render_trade_table(trades)


main()

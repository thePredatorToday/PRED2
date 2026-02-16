"""PREDATOR Dashboard - Modern Glassmorphism Streamlit UI

Design: Dark glass cards, neon accents (cyan, purple, lime). Supports both
simulated data (fallback) and real data via `core.database.db` and
`core.system_state.state`.

Run: streamlit run scripts/dashboard.py
"""

from __future__ import annotations

import os
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go

# Integrace s core
try:
    from core.config import settings
    from core.system_state import state as system_state
    from core.database import db
except Exception:
    # Fallbacks if run outside project context
    settings = None
    system_state = None
    db = None

LOG = logging.getLogger("Predator.Dashboard")

# Page config
st.set_page_config(page_title="THE PREDATOR Dashboard", layout="wide", page_icon="🦅")

# Custom CSS: dark glassmorphism + neon accents
_CSS = r"""
/* Google fonts */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;800&display=swap');
html, body, [data-testid='stAppViewContainer'] { font-family: 'Inter', sans-serif; }

/* Background ambient gradient */
.stApp {
  background: linear-gradient(180deg, #0b0216 0%, #08122b 40%, #001029 100%);
  color: #e6eef8;
}

/* Glass card */
.glass {
  background: rgba(255,255,255,0.03);
  border-radius: 14px;
  padding: 18px;
  box-shadow: 0 6px 20px rgba(2,6,23,0.6);
  border: 1px solid rgba(255,255,255,0.04);
  backdrop-filter: blur(12px) saturate(120%);
}

/* Neon accents */
.kpi-number { font-weight:800; font-size:32px; color: #00f0ff; text-shadow: 0 0 12px rgba(0,240,255,0.25);} 
.kpi-sub { color: #b9dff6; font-size:13px }
.pill-green { background: linear-gradient(90deg,#00ffcc,#00f0ff); color:#001117; padding:6px 10px; border-radius:999px; font-weight:600}
.pill-orange { background: linear-gradient(90deg,#ffb86b,#ff7aa2); color:#1b0b06; padding:6px 10px; border-radius:999px; font-weight:600}
.pill-red { background: linear-gradient(90deg,#ff5c7c,#ff2d55); color:#2b0008; padding:6px 10px; border-radius:999px; font-weight:600}

.neon-border { border: 1px solid rgba(0,240,255,0.08); box-shadow: 0 0 12px rgba(96,88,255,0.06) inset; }

.small-muted { color:#8ea6c2; font-size:12px }
.log-area { background: rgba(0,0,0,0.35); padding:10px; border-radius:8px; max-height:260px; overflow:auto; }

/* Buttons styling tweak */
div.stButton > button { background: linear-gradient(90deg,#0f1724,#081223); color: #aee9ff; border-radius:8px; }

"""

st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)


# ---------------------- Data Layer (DB or simulation) ----------------------
@st.cache_data(ttl=5)
def fetch_system_state() -> Dict[str, Any]:
    if system_state:
        try:
            return dict(
                mode=getattr(system_state, 'mode', 'SHADOW'),
                status=getattr(system_state, 'status', 'PAUSED'),
                balance=getattr(system_state, 'balance_sol', 0.0),
                reserve=getattr(system_state, 'solana_reserve', 0.005),
                open_positions=getattr(system_state, 'open_positions', 0),
                daily_pnl=getattr(system_state, 'daily_pnl', 0.0),
            )
        except Exception as e:
            LOG.exception('system_state fetch failed')
    # Simulation fallback
    return dict(mode='SHADOW', status='PAUSED', balance=0.0, reserve=0.005, open_positions=0, daily_pnl=0.0)


@st.cache_data(ttl=5)
def fetch_recent_trades(limit: int = 200) -> pd.DataFrame:
    if db:
        try:
            with db.connect() as conn:
                df = pd.read_sql_query('SELECT id, mint, entry_price, exit_price, profit_pct, status, timestamp FROM trades ORDER BY timestamp DESC LIMIT ?', conn, params=(limit,))
                return df
        except Exception:
            LOG.exception('DB trades fetch failed')
    # Simulate trades
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(30):
        t = now - timedelta(minutes=i * 3)
        entry = round(0.0002 + np.random.rand() * 0.002, 8)
        exit_p = round(entry * (1 + np.random.randn() * 0.05), 8)
        profit = round((exit_p - entry) / entry * 100, 2)
        rows.append((i, f'mint_{i:04d}', entry, exit_p, profit, 'CLOSED_SIM', t.isoformat()))
    return pd.DataFrame(rows, columns=['id','mint','entry_price','exit_price','profit_pct','status','timestamp'])


@st.cache_data(ttl=5)
def fetch_recent_signals(limit: int = 50) -> pd.DataFrame:
    if db:
        try:
            with db.connect() as conn:
                df = pd.read_sql_query('SELECT id, mint, symbol, liquidity, market_cap, score, created_at FROM signals ORDER BY created_at DESC LIMIT ?', conn, params=(limit,))
                return df
        except Exception:
            LOG.exception('DB signals fetch failed')
    rows = []
    for i in range(20):
        rows.append((i, f'mint_{i:04d}', f'T{i:03d}', int(500+np.random.rand()*20000), int(10000+np.random.rand()*5e6), int(np.random.rand()*100), datetime.now(timezone.utc).isoformat()))
    return pd.DataFrame(rows, columns=['id','mint','symbol','liquidity','market_cap','score','created_at'])


@st.cache_data(ttl=5)
def fetch_predictions(limit: int = 50) -> pd.DataFrame:
    if db:
        try:
            with db.connect() as conn:
                df = pd.read_sql_query('SELECT id, mint, symbol, predicted_mcap, predicted_price, predicted_at, followup_sent, actual_mcap, actual_price, result, created_at FROM predictions ORDER BY created_at DESC LIMIT ?', conn, params=(limit,))
                return df
        except Exception:
            LOG.exception('DB predictions fetch failed')
    return pd.DataFrame(columns=['id','mint','symbol','predicted_mcap','predicted_price','predicted_at','followup_sent','actual_mcap','actual_price','result','created_at'])


@st.cache_data(ttl=5)
def fetch_recent_coin_updates(limit: int = 50) -> pd.DataFrame:
    if db:
        try:
            with db.connect() as conn:
                df = pd.read_sql_query('SELECT id, mint, symbol, update_type, details, created_at FROM coin_updates ORDER BY created_at DESC LIMIT ?', conn, params=(limit,))
                return df
        except Exception:
            LOG.exception('DB coin_updates fetch failed')
    # Simulation fallback: empty frame
    return pd.DataFrame(columns=['id','mint','symbol','update_type','details','created_at'])


@st.cache_data(ttl=5)
def fetch_recent_learner_suggestions(limit: int = 50) -> pd.DataFrame:
    if db:
        try:
            with db.connect() as conn:
                df = pd.read_sql_query('SELECT id, mint, suggestion, score, created_at FROM learner_suggestions ORDER BY created_at DESC LIMIT ?', conn, params=(limit,))
                return df
        except Exception:
            LOG.exception('DB learner_suggestions fetch failed')
    # Simulation fallback: empty frame
    return pd.DataFrame(columns=['id','mint','suggestion','score','created_at'])


def generate_kpi_sparkline(values: List[float]) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(y=values, mode='lines', line=dict(color='#00f0ff', width=2)))
    fig.update_layout(margin=dict(l=0,r=0,t=0,b=0), height=60, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig


# ---------------------- UI Layout ----------------------
def sidebar_controls():
    st.sidebar.markdown('<div style="display:flex;align-items:center;gap:10px">🦅 <strong>THE PREDATOR</strong><small style="color:#79c9f8;margin-left:6px">v1.3</small></div>', unsafe_allow_html=True)
    st.sidebar.caption('Monitoring & control')

    # Mode / toggles
    colA, colB = st.sidebar.columns(2)
    if colA.button('▶️ Start'):
        # push DB command
        try:
            db.push_command('set_status', '{"status":"RUNNING"}')
            st.sidebar.success('Start request queued')
        except Exception:
            st.sidebar.error('Failed to queue start')
    if colB.button('⏸️ Pause'):
        try:
            db.push_command('set_status', '{"status":"PAUSED"}')
            st.sidebar.warning('Pause request queued')
        except Exception:
            st.sidebar.error('Failed to queue pause')
    if st.sidebar.button('🛑 Stop'):
        try:
            db.push_command('system_cmd', '{"cmd":"stop_all"}')
            st.sidebar.error('Stop-all queued')
        except Exception:
            st.sidebar.error('Failed to queue stop')

    st.sidebar.divider()
    st.sidebar.subheader('Modules')
    miner = st.sidebar.checkbox('Miner', value=True)
    hunter = st.sidebar.checkbox('Hunter', value=True)
    executor = st.sidebar.checkbox('Executor', value=True)
    database = st.sidebar.checkbox('Database', value=True)
    if st.sidebar.button('Apply Module Changes'):
        # push toggles for each module
        try:
            modules = {'miner': miner, 'hunter': hunter, 'executor': executor, 'database': database}
            import json
            for m, enabled in modules.items():
                db.push_command('module_toggle', json.dumps({'module': m, 'enable': bool(enabled)}))
            st.sidebar.success('Module toggle commands queued')
        except Exception:
            st.sidebar.error('Failed to queue module toggles')

    # Show last module reports
    if db:
        try:
            with db.connect() as conn:
                cur = conn.cursor()
                cur.execute("SELECT module, status, metrics, created_at FROM module_reports ORDER BY created_at DESC LIMIT 20")
                rep_rows = cur.fetchall()
                if rep_rows:
                    st.sidebar.divider()
                    st.sidebar.subheader('Module Reports (recent)')
                    for mod, status, metrics, created_at in rep_rows:
                        st.sidebar.markdown(f"**{mod}** · {status} · <span style='color:#9ad8ff'>{created_at}</span>")
        except Exception:
            pass

    st.sidebar.divider()
    st.sidebar.subheader('View')
    time_range = st.sidebar.selectbox('Time range', ['15m','1h','6h','24h','7d'], index=1)
    exchange = st.sidebar.selectbox('Exchange', ['All','Raydium','Orca','Jupiter'], index=0)

    st.sidebar.divider()
    st.sidebar.subheader('Mode / Strategy')
    mode = st.sidebar.selectbox('Mode', ['SHADOW','PAPER','BETA','LIVE'], index=0)
    if st.sidebar.button('Set Mode'):
        try:
            db.push_command('set_mode', f'{{"mode":"{mode}"}}')
            st.sidebar.success(f'Mode change to {mode} queued')
        except Exception:
            st.sidebar.error('Failed to queue mode change')

    strategy = st.sidebar.selectbox('Strategy', ['default','aggressive','conservative'], index=0)
    if st.sidebar.button('Change Strategy'):
        try:
            import json
            db.push_command('strategy_change', json.dumps({'strategy': strategy}))
            st.sidebar.success(f'Strategy change to {strategy} queued')
        except Exception:
            st.sidebar.error('Failed to queue strategy change')

    return dict(miner=miner, hunter=hunter, executor=executor, database=database, time_range=time_range, exchange=exchange)


def kpi_row(state: Dict[str,Any], trades_df: pd.DataFrame):
    # Compute KPIs
    total_trades = len(trades_df)
    wins = len(trades_df[trades_df['profit_pct']>0])
    win_rate = (wins / total_trades) if total_trades>0 else 0.0
    active_trades = state.get('open_positions', 0)
    uptime = 'N/A'  # Could compute from system_state.session_start

    # simulated sparklines
    spark_profit = np.cumsum(np.random.randn(20) * 0.5 + 0.2).tolist()
    spark_win = (np.abs(np.random.randn(20))).cumsum().tolist()

    c1, c2, c3, c4 = st.columns([1.2,1.2,1.2,1.2])
    with c1:
        st.markdown('<div class="glass neon-border">', unsafe_allow_html=True)
        st.markdown('<div class="kpi-number">{:.2f}%</div>'.format(state.get('daily_pnl',0.0)), unsafe_allow_html=True)
        st.markdown('<div class="kpi-sub">Profit / Loss Today</div>', unsafe_allow_html=True)
        st.plotly_chart(generate_kpi_sparkline(spark_profit), width='stretch')
        st.markdown('</div>', unsafe_allow_html=True)
    with c2:
        st.markdown('<div class="glass neon-border">', unsafe_allow_html=True)
        st.markdown('<div class="kpi-number">{:.1%}</div>'.format(win_rate), unsafe_allow_html=True)
        st.markdown('<div class="kpi-sub">Win Rate</div>', unsafe_allow_html=True)
        st.plotly_chart(generate_kpi_sparkline(spark_win), width='stretch')
        st.markdown('</div>', unsafe_allow_html=True)
    with c3:
        st.markdown('<div class="glass neon-border">', unsafe_allow_html=True)
        st.markdown('<div class="kpi-number">{}</div>'.format(active_trades), unsafe_allow_html=True)
        st.markdown('<div class="kpi-sub">Active Trades</div>', unsafe_allow_html=True)
        st.plotly_chart(generate_kpi_sparkline(np.linspace(0,active_trades,20).tolist()), width='stretch')
        st.markdown('</div>', unsafe_allow_html=True)
    with c4:
        st.markdown('<div class="glass neon-border">', unsafe_allow_html=True)
        st.markdown('<div class="kpi-number">{}</div>'.format(uptime), unsafe_allow_html=True)
        st.markdown('<div class="kpi-sub">Bot Uptime</div>', unsafe_allow_html=True)
        st.plotly_chart(generate_kpi_sparkline(np.random.rand(20).cumsum().tolist()), width='stretch')
        st.markdown('</div>', unsafe_allow_html=True)


def main_area(state_dict: Dict[str,Any], trades_df: pd.DataFrame, signals_df: pd.DataFrame):
    # Row: Big balance
    st.markdown('<div class="glass" style="display:flex;align-items:center;justify-content:space-between">', unsafe_allow_html=True)
    left, right = st.columns([3,1])
    with left:
        bal = state_dict.get('balance', 0.0)
        delta = np.random.randn() * 0.5
        st.markdown(f"<div style='font-size:28px;font-weight:800;color:#bfefff'>Portfolio Balance</div>")
        st.markdown(f"<div style='font-size:36px;color:#00f0ff;font-weight:800'>{bal:.4f} SOL <span style='font-size:14px;color:#9ad8ff'>({delta:+.2f}%)</span></div>")
    with right:
        st.metric(label='Open Positions', value=state_dict.get('open_positions',0), delta=f"Reserve {state_dict.get('reserve',0.005):.3f} SOL")
    st.markdown('</div>', unsafe_allow_html=True)

    # Row: Charts
    fig_eq = go.Figure()
    # Equity: derived from trades (simulate if empty)
    if not trades_df.empty:
        trades_sorted = trades_df.sort_values('timestamp')
        eq = (trades_sorted['profit_pct'].cumsum().fillna(0)).tolist()
        x = pd.to_datetime(trades_sorted['timestamp']).tolist()
    else:
        x = pd.date_range(end=datetime.now(timezone.utc), periods=50, freq='T')
        eq = np.cumsum(np.random.randn(50) * 0.2 + 0.1)

    fig_eq.add_trace(go.Scatter(x=x, y=eq, mode='lines', fill='tozeroy', line=dict(color='#7afcff')))
    fig_eq.update_layout(title='Equity Curve', paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', height=300, margin=dict(l=10,r=10,t=30,b=10))

    fig_trades = go.Figure()
    # Simplified recent trades display as bar of profit
    if not trades_df.empty:
        recent = trades_df.head(50)
        fig_trades.add_trace(go.Bar(x=pd.to_datetime(recent['timestamp']), y=recent['profit_pct'], marker_color=['#00ffcc' if v>0 else '#ff6b6b' for v in recent['profit_pct']]))
    else:
        t = pd.date_range(end=datetime.now(timezone.utc), periods=30, freq='5T')
        vals = (np.random.randn(30) * 2).tolist()
        fig_trades.add_trace(go.Bar(x=t, y=vals, marker_color=['#00ffcc' if v>0 else '#ff6b6b' for v in vals]))
    fig_trades.update_layout(title='Recent Trades (Profit %)', paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', height=300, margin=dict(l=10,r=10,t=30,b=10))

    col1, col2 = st.columns([2,1])
    with col1:
        st.plotly_chart(fig_eq, width='stretch')
    with col2:
        st.plotly_chart(fig_trades, width='stretch')

    # Row: Recent coin updates & learner suggestions
    st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)
    cu, ls = st.columns([1,1])
    with cu:
        st.markdown('<div class="glass"><strong>Recent Coin Updates</strong></div>', unsafe_allow_html=True)
        df_cu = fetch_recent_coin_updates(limit=50)
        if not df_cu.empty:
            st.dataframe(df_cu, width='stretch')
        else:
            st.markdown('<div class="small-muted">No recent coin updates.</div>', unsafe_allow_html=True)
    with ls:
        st.markdown('<div class="glass"><strong>Recent Learner Suggestions</strong></div>', unsafe_allow_html=True)
        df_ls = fetch_recent_learner_suggestions(limit=50)
        if not df_ls.empty:
            st.dataframe(df_ls, width='stretch')
        else:
            st.markdown('<div class="small-muted">No recent learner suggestions.</div>', unsafe_allow_html=True)

    # Row: Module pills + live log
    st.markdown('<div class="glass" style="padding:12px">', unsafe_allow_html=True)
    mods = st.columns([1,1,1,2])
    status_map = {True:'pill-green', False:'pill-orange'}
    mods[0].markdown(f"<div class='{ 'pill-green' if state_dict.get('mode')=='SHADOW' else 'pill-orange'}'>Mode: {state_dict.get('mode')}</div>", unsafe_allow_html=True)
    mods[1].markdown(f"<div class='pill-green'>Miner</div>", unsafe_allow_html=True)
    mods[2].markdown(f"<div class='pill-green'>Hunter</div>", unsafe_allow_html=True)
    mods[3].markdown('<div class="small-muted">Live Log</div>', unsafe_allow_html=True)

    # Show last logs from file if exists, else simulated
    log_text = ''
    log_file = os.path.join('logs','predator.log')
    if os.path.exists(log_file):
        try:
            with open(log_file,'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()[-200:]
                log_text = ''.join(lines[-200:])
        except Exception:
            log_text = 'Failed reading log file.'
    else:
        # Simulated logs
        for i in range(12):
            t = (datetime.utcnow() - timedelta(seconds=30*i)).strftime('%H:%M:%S')
            log_text += f"{t} - INFO - Simulated event {i}\n"

    st.markdown(f"<div class='log-area'><pre style='font-size:12px;color:#cfeeff'>{log_text}</pre></div>", unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)


def run_app():
    controls = sidebar_controls()
    state = fetch_system_state()
    trades = fetch_recent_trades()
    signals = fetch_recent_signals()

    st.header('')
    kpi_row(state, trades)
    st.write('')
    main_area(state, trades, signals)


if __name__ == '__main__':
    run_app()



# THE PREDATOR Master Specification v1.3

**Document Version:** 1.3  
**Date:** February 13, 2026  
**Status:** Implementation-Ready  
**Audience:** Development Team  
**Authors:** Aggregated from beta specs (including original Czech PDF), Gemini analysis, Grok summaries, and v2.0 blueprint  

---

## 1. SYSTEM OVERVIEW

### 1.1 Core Objective
Automated trading system for Solana blockchain focusing on:
- Detection of new launches and momentum tokens (memecoins primarily).
- Asymmetric opportunities with limited downside.
- Event-driven, deterministic execution with absolute capital protection.
- Solves the biggest problem of retail traders: slow reactions and emotional decision-making.

### 1.2 Scope
- **Blockchain:** Solana mainnet.
- **DEX:** Jupiter V6 primary, Raydium fallback.
- **Token Focus:** Memecoins and new launches on Solana (no filter on "sol" in name/symbol; focus on velocity and on-chain metrics).
- **Not in Scope:** Portfolio rebalancing, cross-chain, options.

### 1.3 Key Constraints
- Single-wallet (v1.3).
- Max position size: 2 SOL (0.1 SOL in Beta mode).
- Max simultaneous positions: 3.
- Daily drawdown kill switch: -30%.
- Latency budget: <5s full pipeline.

### 1.4 Design Principles
- **Fail-Closed:** On error (API/RPC fail, logic error), pause trading; never fail-open.
- **Single Responsibility:** Each module has one role.
- **Deterministic Core:** Risk and execution layers fully deterministic.
- **Analysis ≠ Execution:** Analysis experimental; execution strict.
- **Immutable Mode:** Set at startup, no runtime change.
- **Intelligence over Speed:** Prefer no trade over unclear risk.
- **Three-Phase Predator Model:** Unlike ordinary bots that just blindly buy, functions as a three-phase predator:
  - Scans the market in milliseconds and using advanced velocity analysis identifies momentum before it appears on charts.
  - Performs on-chain security audit to eliminate scams (rug pulls) before investing capital.
  - Trades in multiple modes – from risk-free simulation through Shadow mode with real data to fully automated Live trading.
- **RiskGuard Oversight:** The entire system is overseen by the RiskGuard module, which acts as an uncompromising digital insurance. If the market doesn't play by our rules, RiskGuard won't allow the trade.
- **Event-Driven:** Acts according to what happens, not according to wishes. Coin falls, sells. Coin rises, holds it and sets trailing TP.

---

## 2. ARCHITECTURAL OVERVIEW

### 2.1 System Model
```ascii
Miner → Hunter → Analyzer → Learner (feedback)  
                          ↓  
                      RiskGuard ─┐  
                                  ├→ Executor → SwapEngine  
                             ↓   │  
                     SignalBus   ↓  
                             WalletManager  
                                  ↓  
                             Dashboard (observability)
```

- **Modularity Note:** Started with 3 modules, gradually adding more; each module has its place and role. Some modules should run standalone (e.g., miner, analyzer).

### 2.2 Communication Pattern
- **Internal:** Async Event Bus (all module comms).
- **External:** REST/WS (RPC, DexScreener, Jupiter).
- **Persistence:** JSON state + SQLite logs/history.

### 2.3 Operational Modes
| Mode   | Execution | RPC Broadcast | Real Jupiter | Use Case                  |
|--------|-----------|---------------|--------------|---------------------------|
| Paper  | Simulated | No            | No           | Logic validation          |
| Shadow | Simulated | No            | Yes          | Strategy testing with real data/quotes, simulated buys/sells |
| Beta   | Real      | Yes           | Yes          | Live testing (max 0.1 SOL) |
| Live   | Real      | Yes           | Yes          | Production                |

Mode set at startup; immutable during runtime. In Shadow: Use real market data for analysis, simulate PnL without broadcasting TX.

### 2.4 State Transitions
```ascii
RUNNING ──[Daily DD ≤ -30%]──→ KILL_SWITCH  
    ↓         ↑  
[RPC Fail] ERROR  
    ↓  
PAUSED ←──[Manual]──  
    ↑  
RUNNING ←──[Manual/Recovery]  
```

Invariants:
- `balance_sol >= solana_reserve` (always 0.005 SOL).
- `open_positions <= 3`.
- No trading in ERROR/PAUSED/KILL_SWITCH.

---

## 3. MODULE SPECIFICATIONS

### 3.1 Miner (Data Ingest)
- **Purpose:** Discover new pools and track metrics (focus on Solana memecoins via DEX pairs).
- **Inputs:** DexScreener API, Solana RPC, Pump.fun WS.
- **Outputs:** TokenSnapshot event.
- **Logic:** Async polling; prioritize open positions > watchlist > discovery. No name-based filter (e.g., "sol"); use on-chain metrics.
- **Failure Mode:** API fail → pause new buys; log error.
- **Data Structure:**
```python
from dataclasses import dataclass
from datetime import datetime

@dataclass
class TokenSnapshot:
    address: str
    liquidity_usd: float
    market_cap: float
    age_minutes: float
    volume_5m: float
    price_change_5m: float
    timestamp: datetime
```

### 3.2 Hunter (Momentum Filter)
- **Purpose:** Detect velocity; filter garbage (memecoin-specific: check social presence, no "sol" bias).
- **Inputs:** TokenSnapshot.
- **Outputs:** MomentumSignal if passes.
- **Logic:** Hard filters (burned LP, no mint/freeze, social presence); velocity score.
- **Failure Mode:** No trade if filters fail.
- **Data Structure:**
```python
from dataclasses import dataclass

@dataclass
class MomentumSignal:
    token_address: str
    velocity_score: float  # 0-1
    anomaly_flags: list[str]
```

### 3.3 Analyzer (Audit & Prediction)
- **Purpose:** Deep on-chain audit; score and predict.
- **Inputs:** MomentumSignal.
- **Outputs:** TradeSignal.
- **Logic:** Scoring (liquidity stability 20%, holder dist 20%, LP lock 15%, mint/freeze 15%, volume growth 20%, volatility 10%).
- **Extensions:** AI sentiment, patterns (future).
- **Data Structure:**
```python
from dataclasses import dataclass

@dataclass
class TradeSignal:
    token_address: str
    score: float  # 0-100
    risk_flags: list[str]
    suggested_size: float
    prediction_5m: float  # % change
```

### 3.4 Learner (Optimization)
- **Purpose:** Post-trade analysis; adjust weights.
- **Inputs:** Trade outcomes.
- **Outputs:** ConfigUpdate event.
- **Logic:** Compare predictions vs. reality; suggest threshold tweaks (centralized).
- **Failure Mode:** No auto-apply; manual approval.

### 3.5 RiskGuard (Veto Gate)
- **Purpose:** Final approval; veto unsafe trades.
- **Inputs:** TradeSignal.
- **Outputs:** ApprovedSignal or veto.
- **Logic:** Check limits (size, slippage, drawdown, positions, cooldown); adaptive thresholds.
- **Failure Mode:** Veto on error; persistent kill switch.
- **Data Structure:**
```python
from dataclasses import dataclass

@dataclass
class ApprovedSignal:
    trade_signal: TradeSignal
    approved: bool
    reason: str
```

### 3.6 Executor (Trade Execution)
- **Purpose:** Manage trade lifecycle.
- **Inputs:** ApprovedSignal.
- **Outputs:** ExecutionUpdate.
- **Logic:** Swap via Jupiter; trailing stop, moon bag (sell 80% at 2x, hold 20%), smart DCA. In Shadow: Simulate without TX.
- **Failure Mode:** No execution on veto.
- **Data Structure:**
```python
from dataclasses import dataclass

@dataclass
class ExecutionUpdate:
    token_address: str
    action: str  # BUY/SELL/DCA
    amount: float
    pnl: float
    status: str  # SUCCESS/FAIL
```

### 3.7 WalletManager (Security)
- **Purpose:** Key management, signing, balance tracking.
- **Inputs:** Execution requests.
- **Outputs:** Signed TX.
- **Logic:** Encrypted keys; anti-drain checks; reserve 0.005 SOL.
- **Failure Mode:** Desync → error state.
- **Future:** Multi-wallet.

### 3.8 Dashboard (Observability)
- **Purpose:** UI for monitoring/control.
- **Tech:** Streamlit/FastAPI + WebSockets.
- **Features:** Live feed, positions table, PnL history, kill switch, manual overrides.
- **Security:** Local-only; no remote access.

---

## 4. DATA STRUCTURES & CONFIG

### 4.1 SystemState
```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

@dataclass
class SystemState:
    mode: str  # PAPER/SHADOW/BETA/LIVE
    status: str  # RUNNING/PAUSED/KILL_SWITCH/ERROR
    balance_sol: float
    solana_reserve: float = 0.005
    daily_pnl: float = 0.0
    open_positions: int = 0
    session_start: datetime = field(default_factory=datetime.utcnow)
    last_update: datetime = field(default_factory=datetime.utcnow)
    error_state: Optional[str] = None
```

### 4.2 Config (YAML)
```yaml
system:
  mode: SHADOW
  max_position_size_sol: 2
  max_open_positions: 3
  daily_drawdown_pct: -30
  solana_reserve: 0.005
  api_retry_attempts: 3
  api_backoff_sec: 5

analyzer_weights:
  liquidity_stability: 0.20
  holder_distribution: 0.20
  lp_lock: 0.15
  mint_freeze: 0.15
  volume_growth: 0.20
  volatility: 0.10

apis:
  dexscreener: "https://api.dexscreener.com/latest/dex/pairs/solana"
  jupiter: "https://quote-api.jup.ag/v6/quote"
  solana_rpc: "${SOLANA_RPC_URL}"
```

Validated with Pydantic on startup.

---

## 5. PERSISTENCE & RECOVERY
- **Files:** `data/system_state.json`, `data/positions.json`, `data/logs.jsonl`.
- **Recovery:** Load on startup; verify vs. blockchain; desync → ERROR.
- **Invariants:** Always persist after state change.

---

## 6. LATENCY BUDGETS
| Operation          | Max Duration | Note                  |
|--------------------|--------------|-----------------------|
| Hunter decision    | 500ms        | Fast filter           |
| Analyzer scoring   | 1000ms       | Deep audit            |
| RiskGuard check    | 100ms        | Instant veto          |
| Quote fetch        | 3000ms       | Network-bound         |
| Swap execution     | 30000ms      | Retry possible        |
| Full pipeline      | 5000ms       | Discovery to exec     |

---

## 7. SECURITY PRINCIPLES
- No private keys in logs/code (use .env + encrypted).
- No dynamic code execution.
- Hardcoded risk caps (cannot be overridden by Learner).
- Manual kill switch override.
- Anti-drain: Validate all outgoing TX.

---

## 8. OBSERVABILITY
- **Logging:** Structured JSON (structlog); all decisions with reasons.
- **Metrics:** Win rate, R-multiple, slippage deviation, latency.
- **Dashboard:** Active positions, risk state, PnL, health checks.

---

## 9. CRITICAL FAILURE MODES
| Scenario          | Response                  |
|-------------------|---------------------------|
| RPC timeout       | Pause trading; retry 2x   |
| Jupiter error     | Retry; fallback Raydium   |
| Wallet desync     | Block exec; manual resolve|
| Liquidity rug     | Immediate exit            |
| Analyzer crash    | No new trades             |

System always fail-closed.

---

## 10. IMPLEMENTATION ROADMAP
- **Phase 0:** EventBus, SystemState, logging, config (complete).
- **Phase 1:** Miner, Hunter, basic Analyzer (log-only).
- **Phase 2:** RiskGuard, position tracking, kill switch.
- **Phase 3:** Shadow mode, Jupiter integration.
- **Phase 4:** Dashboard, monitoring.
- **Phase 5:** Beta/Live, WalletManager, Executor.

---

## 11. SUCCESS CRITERIA
✅ All modules concurrent without blocking.  
✅ Event bus >1000 events/sec.  
✅ RiskGuard blocks 100% unsafe trades.  
✅ Learner updates after 10+ trades.  
✅ Dashboard <1s refresh.  
✅ Recovery after crash (no loss).  
✅ Auditable logs.  
✅ No keys logged.  
✅ Whitebox: Every decision reasoned.

---

## 12. FUTURE EXTENSIONS
- Multi-wallet.
- Multi-strategy (scalp/swing).
- ML adaptive thresholds.
- Strategy marketplace.

     **This document is the single source of truth. All code must align.**

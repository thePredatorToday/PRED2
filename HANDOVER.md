# 🔁 STATE CAPTURE — PREDATOR Full Codebase Audit & Bug Fix

**Stav: Aktivní**

## 📌 EXECUTIVE SUMMARY

Kompletní audit 14 zdrojových souborů PREDATOR trading botu odhalil 1 CRITICAL, 2 HIGH a 9 MEDIUM bugů. Drobné opravy (notifier `is_running`, dashboard XSS escape, pulse animace, profit factor INF, duplicitní KPI render) byly implementovány v uncommitted diff — zbývá commitnout + opravit všechny identifikované bugy dle severity.

**DoD**: Všechny bugy ze seznamu opraveny, testy projdou, kód commitnut a pushnut na `claude/architect-system-design-2RDGF`.

---

## 🧭 ARCHITEKTURA & ROZHODNUTÍ (Neměnné)

- **Pipeline**: `Miner → Hunter → Analyzer → RiskGuard → Executor`, propojené přes `EventBus` (pub/sub).
- **State management**: `SystemState` singleton + `asyncio.Lock` + SQLite persistence.
- **Strategy**: 3 profily (`CONSERVATIVE`, `MODERATE`, `AGGRESSIVE`) v `core/strategy.py`, singleton `StrategyManager`.
- **Trading**: Jupiter API (primary) + Raydium (fallback), paper/shadow/beta/live modes.
- **DB**: Synchronní `sqlite3` s WAL mode (doporučeno přejít na `aiosqlite`).
- **Zamítnuto**: Žádné architekturní změny nebyly zamítnuty — audit je read-only analýza.

---

## 🧠 ZNALOSTNÍ BÁZE (Pouze ověřená fakta)

### Struktura souborů
```
PREDATOR/
├── core/
│   ├── config.py          — Pydantic Settings, .env override, yaml_config LOADED BUT UNUSED
│   ├── database.py        — SQLite, synchronní, WAL mode, thread-local connections
│   ├── event_bus.py       — asyncio pub/sub, backpressure queue NONFUNCTIONAL
│   ├── circuit_breaker.py — Classic CB pattern, OK
│   ├── strategy.py        — 3 profily, singleton, OK
│   └── system_state.py    — Singleton state, asyncio.Lock, OK
├── modules/
│   ├── miner.py           — Pump.fun WS + DexScreener polling
│   ├── hunter.py          — Token validation, on-chain checks
│   ├── analyzer.py        — Scoring, LP lock check, audit
│   ├── risk_guard.py      — Pre-trade risk checks, kill switch
│   ├── executor.py        — Order execution, position management, DCA, moon bags
│   ├── learner.py         — Performance tracking, strategy auto-adjust
│   ├── notifier.py        — Telegram + X notifications
│   └── wallet_manager.py  — Keypair, balance, TX signing
└── scripts/
    └── dashboard.py       — Streamlit dashboard
```

### SPL Token Mint Layout (Critical for BUG-H2/A4)
```
Offset 0:   mint_authority_option  (4 bytes, COption u32)
Offset 4:   mint_authority         (32 bytes, Pubkey)
Offset 36:  supply                 (8 bytes, u64)
Offset 44:  decimals               (1 byte, u8)
Offset 45:  is_initialized         (1 byte, bool)
Offset 46:  freeze_authority_option (4 bytes, COption u32)
Offset 50:  freeze_authority       (32 bytes, Pubkey)
Total: 82 bytes
```

### Solana Burn Address
Correct: `11111111111111111111111111111111` (32 chars, base58 of 32 zero bytes)
Wrong (current code): `1111111111111111111111111111111111111111111` (43 chars)

---

## ⚠️ RIZIKA & LESSONS LEARNED

### Identifikované bugy (seřazeno dle severity)

| ID | Severity | Soubor | Řádek | Popis |
|----|----------|--------|-------|-------|
| BUG-E4 | **CRITICAL** | executor.py | 478 | `entry_price` v SOL/token, `current_price` v USD → P&L výpočty kompletně špatné |
| BUG-H2 | HIGH | hunter.py | 244 | Mint authority COption tag check na `data[4:8]` místo `data[0:4]` |
| BUG-A4 | HIGH | analyzer.py | 392 | Stejný byte offset bug jako BUG-H2 |
| BUG-A3 | HIGH | analyzer.py | 35 | `BURN_ADDRESS` má 43 znaků místo 32 |
| BUG-H1 | MEDIUM | hunter.py | 252-255 | `top10_pct` = top10/top20 místo top10/total_supply |
| BUG-E1 | MEDIUM | executor.py | 430-434 | Slippage validation je no-op (`expected_out` vs `expected_out`) |
| BUG-E3 | MEDIUM | executor.py | 111 | Reload positions hardcodes `amount_sol=0.1` |
| BUG-A1 | MEDIUM | analyzer.py | 367 | Race condition — `history` read bez locku |
| BUG-A2 | MEDIUM | analyzer.py | 360 | Division by zero když `liquidity=0` |
| BUG-EB1 | MEDIUM | event_bus.py | 68 | Backpressure queue nikdy nepoužita |
| BUG-M2 | MEDIUM | miner.py | 172 | `rate_limit_backoff` se nikdy neresetuje |
| BUG-W2 | MEDIUM | wallet_manager.py | 125 | Balance sync přepisuje executor's tracked balance |
| BUG-M3 | MEDIUM | miner.py | 131-137 | `.get()` na non-dict hodnotu → AttributeError |

### STRICT NO
- **NIKDY** nemíchat cenové jednotky (SOL vs USD) v jednom výpočtu
- **NIKDY** nepoužívat `data[4:8]` pro COption tag — vždy `data[0:4]`
- **NIKDY** nepředávat stejnou hodnotu jako oba argumenty validační funkce

---

## ✅ HOTOVO & VERIFIKOVÁNO

- [x] Kompletní audit 14 souborů (výsledek v agent output)
- [x] `notifier.py` — `_running` → `is_running` (konzistence s ostatními moduly)
- [x] `main.py` — odstraněn fallback `_running` check
- [x] `dashboard.py` — XSS escape přes `html.escape()` v event logu
- [x] `dashboard.py` — pulse animace per-color (green/amber/red)
- [x] `dashboard.py` — profit factor `INF` display fix
- [x] `dashboard.py` — st.toast emoji fix (`:` → `✅`)
- [x] `dashboard.py` — HealthCheck přidán do log filtru
- [x] `dashboard.py` — odstraněn duplicitní `render_kpis` v TRADES tabu

---

## 📋 TODO & BLOKERY

**Bloker**: Žádný fyzický bloker. Uncommitted changes musí být commitnuty.

**P1 — CRITICAL: Opravit `entry_price` USD/SOL mismatch v executor.py**
- `entry_price` na řádku 478 je v SOL/token
- `current_price` z DexScreener je v USD
- Nutno sjednotit na USD (použít DexScreener `priceUsd` i pro entry)

**P2 — HIGH: Opravit SPL Mint authority byte offsets**
- `hunter.py:244` a `analyzer.py:392` — COption tag na `data[0:4]`, ne `data[4:8]`
- Freeze authority option na `data[46:50]`, ne aktuální offset

**P3 — HIGH: Opravit `BURN_ADDRESS` konstantu**
- `analyzer.py:35` — změnit na `11111111111111111111111111111111` (32 znaků)

**P4 — MEDIUM: Opravit zbývající MEDIUM bugy (BUG-H1, E1, E3, A1, A2, EB1, M2, W2, M3)**

---

## ⚙️ TRIGGER INSTRUKCE PRO NOVÉ VLÁKNO

Přečti si tento State Capture dokument.
Svoji odpověď zahaj větou: „State načten. 0 chyb, 100% korektnost. Očekávám pokyny."

**[STOP STATE]**: Pokud vidíš v zadání P1 jakoukoliv ambiguotu, chybí ti data nebo existuje riziko regrese, okamžitě zastav exekuci a vypiš bodový seznam chybějících informací. Pokud je stav deterministický, nepiš žádný kód. Místo toho rovnou vygeneruj soubor `todo.md` pro P1 úkol.

Struktura `todo.md`: Cíl, atomární checklist kroků, analýza rizik, testovací strategie (jak ověříme 100% správnost).

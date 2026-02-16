# Analyzer Module Specification (v1.3)

## 1. Overview
The Analyzer is the deep-audit layer of THE PREDATOR. It takes promising candidates from the Hunter and performs a detailed scoring to determine if a trade should be executed.

## 2. Data Structure: TradeSignal
```python
@dataclass
class TradeSignal:
    token_address: str
    score: float  # 0-100
    risk_flags: list[str]
    suggested_size: float
    prediction_5m: float  # % change
    detailed_scores: dict  # Break-down of components
```

## 3. Scoring Components (Total 100 points)

### 3.1 Liquidity Stability (20 points)
- **Source**: `COIN_UPDATE` history + DEX APIs.
- **Logic**: 
    - 10 pts: Liquidity > $10k.
    - 10 pts: Liquidity has not decreased by > 5% in the last 15 mins.
    - Veto: If liquidity drops > 20% suddenly (Potential rug).

### 3.2 Holder Distribution (20 points)
- **Source**: RPC `get_token_largest_accounts`.
- **Logic**:
    - 10 pts: Top 10 holders own < 20% of supply.
    - 10 pts: Total holders > 500.
    - Penalty: -5 pts for every 10% owned by top 10 above 20%.

### 3.3 LP Lock Status (15 points)
- **Source**: RPC check of LP token account.
- **Logic**:
    - 15 pts: 100% of LP burned (sent to `111...`) or locked in known contract.
    - 5 pts: > 80% burned/locked.
    - 0 pts: No lock.

### 3.4 Mint/Freeze Authority (15 points)
- **Source**: RPC `get_account_info`.
- **Logic**:
    - 15 pts: Both `mint_authority` and `freeze_authority` are `None`.
    - Veto: If either is active (Hunter should have filtered this, but Analyzer acts as secondary check).

### 3.5 Volume Growth (20 points)
- **Source**: `volume_5m` from Miner.
- **Logic**:
    - 10 pts: Positive 5m volume trend.
    - 10 pts: `volume_5m` > 10% of total liquidity (Velocity indicator).

### 3.6 Volatility (10 points)
- **Source**: Price history.
- **Logic**:
    - 10 pts: High positive volatility (Price discovery phase).
    - 5 pts: Stable growth.

## 4. Integration
1. Subscribe to `GOOD_COIN_SELECTED` from `Hunter`.
2. Run async scoring tasks.
3. If `score > 80`, emit `TRADE_SIGNAL_READY` for `RiskGuard`.

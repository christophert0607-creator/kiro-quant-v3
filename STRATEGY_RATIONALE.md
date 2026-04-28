# Kiro Quant — Trading Strategy V3.0
## Rational Trading System

---

## Core Philosophy

**"Trade with the trend, not against it. Give trades room to breathe."**

The key failure of v1 was trading noise (5-min whipsaws). v3 focuses on:
1. **Trend confirmation before entry** — no catching falling knives
2. **Wider exits** — let trades develop, don't get shaken out by normal volatility
3. **Momentum confirmation** — RSI alone is insufficient; need MACD + trend alignment
4. **Meaningful hold times** — minimum 30min, target 1-4 hours for intraday swings
5. **Sentiment-aware profiling** — dynamically switch between Momentum / Mean-Reversion / Grid strategies based on VIX + sentiment score

---

## Strategy Profile Switching (V3 — choose_profile)

The system auto-selects one of three profiles based on `vix` and `sentiment_score`:

| VIX | Sentiment | Profile | Description |
|-----|-----------|---------|-------------|
| < 20 | any | **Momentum** | Trend-following, ride the move |
| 20-30 | bullish (>0.1) | **Mean-Reversion** | Fade extremes, capture bounces |
| 20-30 | neutral | **Grid** | Range-bound, harvest volatility |
| >= 30 | bearish | **BLOCK ALL** | No new entries |

Implementation: `strategy_factory.choose_profile(vix, sentiment)`

---

## Entry Rules (ALL conditions must pass)

### 1. Trend Filter (Mandatory)
```
Price > SMA_20  ← UPTREND or MIXED (allowed)
Price < SMA_20 AND SMA_20 sloping DOWN  ← DOWNTREND (BLOCKED)
```
**Why:** RSI < 30 in a downtrend is a value trap.

### 2. RSI Oversold with Momentum Confirmation
```
RSI_14 < 30           ← Oversold (not just < 35)
MACD_HIST > 0         ← Histogram turning positive
Price >= SMA_20       ← Already above moving average
```
**Why:** RSI < 30 alone catches knives. MACD positive confirms buyers stepping in.

### 3. VIX Gate (Risk Environment)
```
VIX < 25  ← Low fear (allowed)
VIX >= 25 AND sentiment >= 0.1  ← Caution, reduced size
VIX >= 30  ← BLOCK ALL ENTRIES
```
**Why:** High VIX = market panic = stocks keep falling even when oversold.

### 4. Sentiment Gate
```
sentiment_score >= -0.1  ← Not bearish (allowed)
sentiment_score < -0.1  ← BLOCK ENTRIES
```

### 5. Sentiment-Aligned Direction
```
sentiment_score >= 0.1  → Allow LONG only (bullish)
sentiment_score <= -0.1 → BLOCK ALL
sentiment between → Allow both LONG/SHORT
```

---

## V3 Capital Allocation (bucket_fractions / bucket_thresholds)

V3 dynamically allocates capital based on signal conviction:

```
bucket_fractions: [0.05, 0.10, 0.20]   # Low/Med/High conviction
bucket_thresholds: [0.3, 0.6, 0.8]    # confidence_score cutoffs
```
- `confidence_to_risk_pct()` maps confidence_score → position size
- High confidence (>= 0.8) → up to 20% position
- Medium (0.6-0.8) → 10%
- Low (< 0.3) → 5%

---

## Exit Rules

### Take Profit (Adaptive)
| Condition | Take Profit | Stop Loss |
|---|---|---|
| RSI < 20 (deep oversold) | 3.0% | 1.5% |
| RSI 20-30 (moderate) | 2.0% | 1.5% |
| Normal entry | 2.0% | 1.5% |
| After trailing stop activation | dynamic | entry price |

### Time Exit (Prevents Endless Holds)
```
IF within 0.3% of entry AND hold_time >= 30min → EXIT
IF within 0.5% of entry AND hold_time >= 60min → EXIT
```

### Trailing Stop by Volatility (V3)
```
trailing_stop_by_volatility()  ← activates after 1.5% profit
Sets trailing stop at: entry_price + (atr_slippage * k)
Exits when price closes below trailing stop
```

### Commission Erosion (V3)
```
apply_commission_erosion()  ← deducts commission cost from pnl
Gross pnl → Net pnl after commission
Commission per trade: ~0.2% (HK stocks)
```

### Anti-Churn: sell_signal_streak_by_symbol
```
IF same symbol gets sell signal 3+ consecutive cycles AND profit > 0
→ Force close (prevent oscillation)
→ Reset streak counter
```
**Why:** Prevents whipsaw sell/buy cycles on same symbol.

---

## Self-Learning Integration

After each trade:
1. Record: entry_price, exit_price, pnl, hold_time, rsi_at_entry, macd_at_entry, sentiment_at_entry
2. Write to: `learning/us_sim/decision_trace_us_sim.jsonl`
3. Snapshot: `learning/us_sim/account_snap_us_sim.jsonl` every 5 minutes during trading
4. Every ≥10 closed trades → batch retrain XGBoost model
5. If win_rate < 40% over 20 trades → pause and review

---

## Config Parameters (v3)

```yaml
# Entry
RSI_OVERSOLD_ENTRY: 30
RSI_DEEP_OVERSOLD: 20
SMA_FILTER: true
MACD_POSITIVE_REQUIRED: true
VIX_MAX: 30
SENTIMENT_MIN: -0.1

# Profile Switching (V3)
PROFILE_MOMENTUM: vix < 20
PROFILE_MEAN_REVERSION: 20 <= vix < 30 AND sentiment >= 0.1
PROFILE_GRID: 20 <= vix < 30 AND sentiment < 0.1

# Capital Allocation (V3)
bucket_fractions: [0.05, 0.10, 0.20]
bucket_thresholds: [0.3, 0.6, 0.8]

# Exits
TAKE_PROFIT_RSI20: 0.03
TAKE_PROFIT_NORMAL: 0.02
STOP_LOSS: 0.015
TRAILING_STOP: true   # via trailing_stop_by_volatility()
COMMISSION_EROSION: true  # via apply_commission_erosion()

# Anti-Churn (V3)
sell_signal_streak_limit: 3   # consecutive sell signals → force close
MIN_PROFIT_TO_HOLD: 0.0       # exit at cost if profit < 0

# Timing
MIN_HOLD_MINUTES: 30
TIME_EXIT_NEAR_ENTRY_PCT: 0.003
TIME_EXIT_HOURS: 2

# Position
RISK_FRACTION: 0.10
MAX_POSITIONS: 3
SCALE_IN_RSI: 20
```

---

## Risk Controls

| Control | Value | Reason |
|---|---|---|
| Max daily loss | -3.0% of portfolio | Circuit breaker |
| Max consecutive losses | 4 | Pause and review |
| Max open positions | 3 | Diversification |
| Position size | 10% max | Never overweight one trade |
| Stop loss hard cap | -2.0% | Absolute max loss per trade |

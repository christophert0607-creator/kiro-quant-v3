# Kiro Quant — Trading Strategy v2.0
## Rational Trading System

---

## Core Philosophy

**"Trade with the trend, not against it. Give trades room to breathe."**

The key failure of v1 was trading noise (5-min whipsaws). v2 focuses on:
1. **Trend confirmation before entry** — no catching falling knives
2. **Wider exits** — let trades develop, don't get shaken out by normal volatility
3. **Momentum confirmation** — RSI alone is insufficient; need MACD + trend alignment
4. **Meaningful hold times** — minimum 30min, target 1-4 hours for intraday swings

---

## Entry Rules (ALL conditions must pass)

### 1. Trend Filter (Mandatory)
```
Price > SMA_20  ← UPTREND or MIXED (allowed)
Price < SMA_20 AND SMA_20 sloping DOWN  ← DOWNTREND (BLOCKED)
```
**Why:** RSI < 30 in a downtrend is a value trap. The stock falls because it deserves to fall.

### 2. RSI Oversold with Momentum Confirmation
```
RSI_14 < 30           ← Oversold (not just < 35)
MACD_HIST > 0         ← Histogram turning positive (momentum reversing)
Price >= SMA_20       ← Already above moving average (confirmed reversal)
```
**Why:** RSI < 30 alone catches knives. MACD positive confirms buyers are stepping in.

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
**Why:** Fighting a bearish macro is dangerous even with technical signals.

### 5. Sentiment-Aligned Direction
```
sentiment_score >= 0.1  → Allow LONG only (bullish environment)
sentiment_score <= -0.1 → BLOCK ALL (bearish environment)
sentiment_score between → Allow both LONG/SHORT
```

---

## Exit Rules

### Take Profit (Wider, Adaptive)
| Market Condition | Take Profit | Stop Loss |
|---|---|---|
| RSI < 20 (deep oversold) | 3.0% | 1.5% |
| RSI 20-30 (moderate oversold) | 2.0% | 1.5% |
| Normal entry | 2.0% | 1.5% |

**Why:** Deep oversold = bigger bounce potential, give it more room.

### Time Exit (Prevents Endless Holds)
```
IF price is within 0.3% of entry AND hold_time >= 30min → EXIT
IF price is within 0.5% of entry AND hold_time >= 60min → EXIT
```
**Why:** If a stock isn't going your way in 30+ min, it's likely not going to.

### Trailing Stop (After 1.5% Profit)
```
Once profit >= 1.5%, set trailing stop at entry price
Exit if price drops back to entry (lock in at least 0%)
```
**Why:** Never turn a winning trade into a losing one.

### End of Day Hard Close
```
IF hold_time >= 120min AND profit > 0 → close before HK 16:00
IF hold_time >= 120min AND loss > -1% → close before HK 16:00
IF loss > -1.5% at any time → hard stop, close immediately
```

---

## Position Sizing

```
risk_fraction = 0.10 (10% of portfolio per trade)
max_position_pct = 0.20 (20% of portfolio max)
max_open_positions = 3
min_profit_threshold_for_add = 1.0% (can add if up 1%+ on existing)
```

**Scale-In Rule:**
```
IF RSI < 20 AND already have position in same stock AND profit >= 1.0%
THEN add 0.5x more (maximum 1.5x total)
```
**Why:** Deep oversold with existing profit = high conviction, add to winner.

---

## Hold Time Targets

| Style | Min Hold | Target | Max Hold |
|---|---|---|---|
| Intraday Swing | 30 min | 1-2 hours | 4 hours / EOD |
| Momentum Play | 15 min | 30-60 min | 2 hours |

**No trades held overnight unless profit >= 2%.**

---

## Blackout Rules (When NOT to trade)

1. **VIX >= 30** — Market panic, block all
2. **Sentiment < -0.2** — Bearish macro, block all
3. **Downtrend confirmed** (Price < SMA 20 AND SMA 20 declining 3+ days) — No new LONG entries
4. **Right after a stop loss** — Wait 60 min before next entry on same symbol
5. **Earnings within 2 hours** — Block that symbol
6. **Market hours** — Only trade 09:30-12:00, 13:00-15:30 HKT

---

## Risk Controls

| Control | Value | Reason |
|---|---|---|
| Max daily loss | -3.0% of portfolio | Circuit breaker |
| Max consecutive losses | 4 | Pause and review |
| Max open positions | 3 | Diversification |
| Position size | 10% max | Never overweight one trade |
| Stop loss hard cap | -2.0% | Absolute max loss per trade |

---

## Self-Learning Integration

After each trade:
1. Record: entry_price, exit_price, pnl, hold_time, rsi_at_entry, macd_at_entry, sentiment_at_entry
2. Every ≥10 closed trades → batch retrain XGBoost model
3. If win_rate < 40% over 20 trades → pause and review strategy
4. If avg_hold_time < 15min over 10 trades → increase min hold time

---

## Config Parameters (v2)

```yaml
# Entry
RSI_OVERSOLD_ENTRY: 30           # Was 35
RSI_DEEP_OVERSOLD: 20            # Scale-in trigger
SMA_FILTER: true                  # Price must be > SMA 20
MACD_POSITIVE_REQUIRED: true      # MACD histogram must be > 0
VIX_MAX: 30                      # Block if VIX >= 30
SENTIMENT_MIN: -0.1            # Block if sentiment < -0.1

# Exits
TAKE_PROFIT_RSI20: 0.03         # 3% for deep oversold
TAKE_PROFIT_NORMAL: 0.02        # 2% for normal
STOP_LOSS: 0.015                 # 1.5% (widened from 1%)
TRAILING_STOP: true              # Activate after 1.5% profit

# Timing
MIN_HOLD_MINUTES: 30            # Was 5 (prevent noise trading)
TIME_EXIT_NEAR_ENTRY_PCT: 0.003  # Exit if within 0.3% after 30min
TIME_EXIT_HOURS: 2               # Hard exit after 2 hours

# Position
RISK_FRACTION: 0.10            # 10% per trade
MAX_POSITIONS: 3                # Max 3 open at once
SCALE_IN_RSI: 20               # Can add if RSI < 20 and profit >= 1%
```

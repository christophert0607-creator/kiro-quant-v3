# Kiro Quant V3 深度策略分析報告
**日期：2026-04-23 | 分析師：小祈**

---

## 一、近期交易實績回顧

### 過去 24 小時成交紀錄
| 時間 | 標的 | 動作 | 數量 | 價格 | 原因 | 結果 |
|------|------|------|------|------|------|------|
| 04-22 23:33 | INTC | BUY | 30 | $66.19 | model_signal_conf=0.276 | ⚠️ 30分鐘後止損 |
| 04-22 23:33 | MU | SELL | 4 | $475.91 | model_signal_confirmed | ✅ 獲利 (~$120) |
| 04-23 00:06 | INTC | SELL | 30 | $65.97 | time_exit_v2 | ❌ 虧損 -$6.90 |

### 現有持倉
- **NFLX SHORT**: 21股 @ $93.71 成本 → 現價約$92.58 (帳面獲利)
- **現金**: ~$1,012,000 (未動用資金過高)

---

## 二、核心問題診斷

### 🔴 問題 1：Confidence Gate 過度嚴格
**現狀**：`min_confidence_threshold = 0.20`
**實際數據**：
- NFLX confidence = 0.0414 (僅為門檻的 20%)
- AAPL confidence = 0.0186 (僅為門檻的 9%)
- 0700.HK confidence = 0.0846 (僅為門檻的 42%)

**影響**：
- 19 個 HK symbols 全部低於 0.12，全日零交易
- 32 個 US symbols 中僅 INTC 達到 0.276，但其餘全部被封鎖
- **過去 24 小時僅 2 筆成交，系統形同虛設**

### 🔴 問題 2：RSI Oversold 門檻過激進
**現狀**：`rsi_oversold = 40.0`
**市場現實**：
- RSI 40 以下代表極端恐慌，正常市場一年僅出現 3-5 次
- 過去一週 V3 從未觸發 RSI_OVERSOLD_BUY

**影響**：
- Mean-Reversion 策略完全失效
- 系統過度依賴 Momentum，但 Momentum 又被 confidence gate 封鎖

### 🔴 問題 3：Confidence 計算公式缺陷
**現狀**：`confidence = |pred - price| / price × 50`
**問題**：
- 公式僅衡量「預測偏離度」，而非「預測準確度」
- 高波動股票（如 TSLA）天然產生高分數，低波動股票（如 0005.HK）永遠低分
- 預測方向（漲/跌）與 confidence 無關

**實例**：
- 0005.HK (HSBC) 價格 $141.23，預測 $141.23 → confidence = 0 (完美預測反而零分！)
- TSLA 價格 $386，預測 $400 → confidence = 1.8 (偏差大反而高分)

### 🟡 問題 4：Time Exit 過於急促
**現狀**：`min_hold_minutes = 60`, `time_exit_near_entry_pct = 0.005`
**實例**：
- INTC 買入後 33 分鐘即觸發 time_exit (價格幾乎無變動)
- 未給予足夠時間讓趋势發展

### 🟡 問題 5：Idle Cache 持續過期
**現狀**：QuoteCache stale_threshold = 15s, TTL = 30s
**問題**：
- US 收盤後緩存持續累積至 1800+ 秒
- 雖然已修復 `_collect_only_cycle` blocking，但 stale 警告依然頻繁
- 影響翌日開市時的 initial prediction 質量

---

## 三、四大策略深度檢視

### 策略 A：Momentum (8x8x139)
**評級**：🟡 輕微升級（但執行層面失效）

**優點**：
- 理論基礎紮實，LSTM + 28 維特徵捕捉市場微結構
- US 盤前預熱機制有效（32 symbols precompute）

**缺點**：
- Confidence gate 0.20 將 90%+ 的信號扼殺
- 預測偏離度公式與實際 win rate 無關
- 缺乏波動率調整（VIX 18.92 時應該降低門檻）

**建議改動**：
1. `min_confidence_threshold` 0.20 → **0.08–0.10**
2. 引入 VIX 動態調整：VIX < 18 時降至 0.06，VIX > 25 時升至 0.15
3. 改用「預測準確度」而非「偏離度」：加入前 20 次預測的 MAE

---

### 策略 B：Mean-Reversion
**評級**：🔴 需要大幅修復

**優點**：
- RSI + MACD + SMA 三重確認機制理論上穩健
- 適合震盪市（而家正係震盪市！）

**缺點**：
- RSI 40 門檻過低，幾乎永遠觸發不到
- 未利用 Bollinger Band 寬度（%B）作為輔助指標
- 缺乏「超賣持續時間」因子（RSI 40 維持多久？）

**建議改動**：
1. `rsi_oversold` 40 → **50**（溫和超賣區）
2. 新增 `rsi_extreme_oversold` = 35（強制買入，不經 confidence gate）
3. 加入 `%B < 0.2` 作為輔助條件
4. 引入「RSI 背離」檢測（價格新低但 RSI 未新低）

---

### 策略 C：GRPO Self-Learn
**評級**：🔴 最弱環節

**優點**：
- 在線學習概念先進，理論上能適應市場變化

**缺點**：
- Confidence 計算方式完全錯誤（偏離度 ≠ 準確度）
- 缺乏「預測歷史追蹤」— 模型從不檢查自己上次的預測是否正確
- Self-learn 僅記錄 pred_id，無反饋機制

**建議改動**：
1. 新增「預測驗證系統」：
   - 記錄每次預測 (pred, actual, time)
   - 計算 rolling 20 次 MAE (Mean Absolute Error)
   - confidence = max(0, 1 - MAE/price) × directional_accuracy
2. 引入「預測置信區間」：pred ± 1σ，而非單一數值
3. 每週自動重新訓練模型（目前似乎從未 retrain）

---

### 策略 D：Risk-Off (ZS)
**評級**：🟢 維持現狀

**優點**：
- Short enabled 提供下行保護
- `short_sentiment_max = 0.1` 有效過濾高恐慌時段

**缺點**：
- 僅有 NFLX 一個 short 倉位，分散度不足
- 缺乏「市場廣度」指標（如 % of stocks above 50MA）

**建議改動**：
1. 新增「VIX 期貨期限結構」監測（contango/backwardation）
2. 加入「信用利差」追蹤（HYG vs LQD）

---

## 四、系統層面加強建議

### 1. 數據質量
| 現狀 | 問題 | 建議 |
|------|------|------|
| QuoteCache TTL 30s | US 收盤後 stale 累積 | 收盤後自動切換至「盤後模式」，延長 TTL 至 300s |
| 單一 Futu 數據源 | OpenD 斷線即癱瘓 | 加入 YFinance 作為 primary fallback（非僅用於收盤審計）|
| Buffer 1684 bars | 過多歷史數據拖慢計算 | 降至 500 bars（約 8 小時），focus 於近期模式 |

### 2. 風險管理
| 現狀 | 問題 | 建議 |
|------|------|------|
| max_positions = 15 | 實際從未觸及 | 降至 8，強制集中於高 conviction 標的 |
| stop_loss = 2% | 過於緊密 | 改為「波動率調整」：ATR × 2 |
| quick_take_profit = 2% | 過於保守 | 改為「趨勢追蹤」：highest_price × 0.98 |

### 3. 執行效率
| 現狀 | 問題 | 建議 |
|------|------|------|
| polling = 60s | 對於 US 高波動標的過慢 | HK: 60s, US: 30s |
| 同步 for-loop 獲取 quotes | 已修復（to_thread）| 繼續監察 |
| 無並行 symbol 處理 | 32 symbols 串行 | asyncio.gather 並行處理（最多 8 個並發）|

---

## 五、即時改動優先級

| 優先級 | 改動 | 預期效果 | 實施難度 |
|--------|------|---------|---------|
| **P0** | Confidence threshold 0.20 → 0.10 | 交易頻率提升 3-5 倍 | ⭐ 簡單 |
| **P0** | RSI oversold 40 → 50 | Mean-Reversion 恢復運作 | ⭐ 簡單 |
| **P1** | 改用 prediction MAE 計算 confidence | 準確度提升 20%+ | ⭐⭐ 中等 |
| **P1** | VIX 動態調整 threshold | 適應市場恐慌周期 | ⭐⭐ 中等 |
| **P2** | 加入 YFinance fallback | 系統可用性提升至 99%+ | ⭐⭐⭐ 複雜 |
| **P2** | 每週自動模型 retrain | 預測準確度長期穩定 | ⭐⭐⭐ 複雜 |
| **P3** | 並行 symbol 處理 (gather) | 延遲降低 50% | ⭐⭐⭐ 複雜 |

---

## 六、總結

**V3 的瓶頸不在模型，而在「過度保守的門檻」+「錯誤的 confidence 計算」。**

模型能產生合理的預測（如 INTC conf=0.276 確實觸發了交易），但 90% 的信號被 confidence gate 扼殺。同時，Mean-Reversion 策略因 RSI 40 門檻過激進而完全失效。

**最急迫的改動：**
1. 調低 confidence threshold 至 0.10
2. 放寬 RSI oversold 至 50
3. 修正 confidence 計算公式

這三項改動可於 10 分鐘內完成，預期交易頻率從「每日 0-2 筆」提升至「每日 5-10 筆」，系統終於能真正運作。

---
*報告生成時間：2026-04-23 08:45 HKT*
*數據來源：V3 Launcher 日誌分析*

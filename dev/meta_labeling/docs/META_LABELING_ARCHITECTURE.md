# Meta-Labeling 架構設計

## 現況
- 11,589 predictions 寫入 DB（2026-05-18~19）
- 3 signals，全部 prediction_id=NULL（信號鏈斷裂）
- 0 outcomes（無closed trades）
- self_learn 反饋循環已定義（feedback.py），但未被觸發

## 核心目標
利用 ML 模型預測誤差來二次確認或否决 base strategy 產生的交易信號。

## 輸入輸出

### Input
| 來源 | 欄位 | 說明 |
|------|------|------|
| Base Strategy | action (BUY/SELL) | 原有策略信號 |
| self_learn | prediction_id | 關聯的 ML 預測 |
| self_learn | predicted_price, confidence | 預測價格與信心度 |
| self_learn | feature_vector (pickle) | 指標快照：RSI, MACD, Bollinger Bands, volume ratio 等 |
| Market | regime | 趨勢/震盪/突破 |
| Market | volatility_zscore | 波動率標準化分數 |

### Output（Meta Signal）
| 輸出 | 說明 | Base Signal 行為 |
|------|------|-----------------|
| CONFIRM | Meta-label 同意 base signal | 執行（size 不變）|
| WEAKEN | Meta-label 懷疑 base signal | 降低 size（×0.5）或跳過 |
| REJECT | Meta-label 反對 base signal | 不執行 |
| REVERSE | Meta-label 建議反向 | 執行反向 signal |

## 架構模組

```
meta_labeler.py
├── predict(features, prediction_data) → meta_signal
├── build_feature_vector(indicators, regime) → np.array
├── load_meta_model() → sklearn model
└── get_confidence_threshold() → float

meta_evaluator.py
├── evaluate_signal(base_action, meta_signal, confidence) → ExecutionPlan
└── calculate_position_size(base_size, meta_signal) → int

meta_features.py
├── extract_indicators(feature_vector) → dict
├── compute_regime(vix, trend_strength) → str
└── compute_volatility_zscore(hist_vol, iv) → float
```

## 決策邏輯

```python
# 偽代碼
def meta_decision(prediction_id, base_action):
    pred = get_prediction(prediction_id)
    features = deserialize(pred.feature_vector)
    
    # 信心度閾值
    if pred.confidence < 0.55:
        return REJECT
    
    # 方向一致性
    direction_agree = check_direction_alignment(pred, base_action)
    if not direction_agree:
        return REJECT
    
    # Regime 過濾
    regime = features.get('regime', 'unknown')
    if regime == 'high_volatility' and pred.confidence < 0.7:
        return REJECT
    
    # 指標確認
    rsi = features.get('RSI_14', 50)
    if base_action == 'BUY' and rsi > 70:
        return REJECT
    
    return CONFIRM
```

## 訓練數據

- 從 outcomes 表取出 closed trades
- label = SIGNED if outcome.pnl > 0 else WRONG
- features = prediction.feature_vector + market_regime + volatility
- 模型：LightGBM 或 RandomForest

## 驗證方法

1. 歷史回測：對比信號級別 precision/recall
2. Paper trading：隔離實盤，逐步放量
3. A/B 測試：50% 信號走 meta-label，50% 不走

## 風險控制

- MAX_REVERSE_RATIO：最多 20% reverse 信号
- MIN_CONFIDENCE：低於 0.5 一律 REJECT
- EMERGENCY_BLOCK：波動率突增時全部 REJECT

## 依賴

- self_learn/schema.py（predictions, signals, outcomes 表已就緒）
- self_learn/models.py（ORM 模型已就緒）
- self_learn/feedback.py（hook_on_signal, hook_on_prediction 已定義）

## 下一步

- [ ] meta_011：實現 meta_labeler.py
- [ ] meta_012：實現決策閾值邏輯
- [ ] 回測驗證（meta_020）
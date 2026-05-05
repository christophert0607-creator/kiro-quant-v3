# Kiro Quant V3 — 模型訓練教學

> 給 AI Agent / 開發者的完整指引，確保每次訓練、保存、部署新模型都不出錯。
> 維護日期：2026-05-05

---

## 目錄

1. [架構概覽](#1-架構概覽)
2. [Checkpoint 格式規範](#2-checkpoint-格式規範)
3. [模型 Registry 系統](#3-模型-registry-系統)
4. [訓練器速查表](#4-訓練器速查表)
5. [逐步訓練教學](#5-逐步訓練教學)
6. [部署新模型（3步）](#6-部署新模型3步)
7. [HK 市場模型注意事項](#7-hk-市場模型注意事項)
8. [常見錯誤與解決方法](#8-常見錯誤與解決方法)
9. [Agent 訓練標準作業程序](#9-agent-訓練標準作業程序)

---

## 1. 架構概覽

```
v3_pipeline/
├── models/
│   ├── brain.py                  ← 模型類定義（KiroLSTM, AttentiveKiroLSTM, StockPatternModel）
│   ├── manager.py                ← ModelManager：訓練/保存/加載/推理
│   ├── registry.py               ← ModelRegistry：邏輯名 → 實際 checkpoint 解析
│   ├── models_registry.json      ← ★ 部署配置，改這裡來切換模型
│   ├── trainer_v4_1.py           ← 主力訓練器（AttentiveKiroLSTM，推薦使用）
│   ├── trainer_base_10y.py       ← 10年數據預訓練（KiroLSTM）
│   ├── trainer_pattern_v1.py     ← 形態識別多任務模型
│   ├── trainer_stacking.py       ← 集成堆疊（XGB/RF/LGB + meta）
│   ├── trainer_multi_model.py    ← 多模型並行訓練
│   └── trainer_regime_stacking.py← 市場狀態自適應集成
│
├── data/
│   └── downloader.py             ← HistoricalDataDownloader（yfinance）
│
└── features/
    └── indicators.py             ← TechnicalIndicatorGenerator（決定 input_dim）

v3_pipeline/models/trained_models/
├── global_state_v4_1_best.pth    ← 目前主力模型（input_dim=27, AttentiveKiroLSTM）
└── （新模型放這裡）

models/
├── hk/
│   └── lstm_v1.pth               ← HK 模型（raw state_dict，input_dim 待確認）
└── hsi_lstm_v1.pth               ← HSI 指數模型
```

---

## 2. Checkpoint 格式規範

**所有透過 `ModelManager.save()` 保存的 `.pth` 文件必須包含以下 key：**

```python
{
    "model_state_dict":  <OrderedDict>,  # model.state_dict()
    "lookback":          int,            # 序列長度，通常 60
    "target_col":        str,            # 預測目標列，通常 "Close"
    "feature_columns":   list[str],      # 特徵列名（決定 input_dim）
    "feature_mins":      dict,           # {col: min_val}，用於 MinMax 還原
    "feature_maxs":      dict,           # {col: max_val}
    "target_min":        float,          # 目標變量最小值
    "target_max":        float,          # 目標變量最大值
    "model_class":       str,            # "KiroLSTM" 或 "AttentiveKiroLSTM"
}
```

**⚠️ 警告：**
- 不可只保存 `state_dict`（raw dict 無法恢復 scaler 信息）
- `feature_columns` 必須與訓練時的列順序完全一致
- 缺少任何 key 會令 `ModelManager.load()` 退化為部分加載（strict=False 警告）

**驗證 checkpoint 格式：**
```python
import torch
p = torch.load("v3_pipeline/models/trained_models/my_model.pth", map_location="cpu", weights_only=False)
required = {"model_state_dict", "lookback", "target_col", "feature_columns",
            "feature_mins", "feature_maxs", "target_min", "target_max", "model_class"}
missing = required - set(p.keys())
if missing:
    print(f"❌ 缺少 key: {missing}")
else:
    # 確認 input_dim
    w = p["model_state_dict"].get("lstm.weight_ih_l0")
    input_dim = w.shape[1] if w is not None else "unknown"
    print(f"✅ 格式正確 | input_dim={input_dim} | model_class={p['model_class']}")
    print(f"   feature_columns ({len(p['feature_columns'])}): {p['feature_columns'][:5]}...")
```

---

## 3. 模型 Registry 系統

### 邏輯名解析流程

```
v3_launcher.py                models_registry.json            trained_models/
_load_market_model("HK")
  → "v3_hk_stocks"     →→→→  aliases["v3_hk_stocks"]        → global_state_v4_1_best.pth
                                = "global_state_v4_1_best"
```

### 解析優先順序（第一個命中勝出）

1. `config.json → model.markets[mode]` 得到邏輯名
2. `models_registry.json → aliases[邏輯名]` 得到 checkpoint stem
3. `trained_models/{stem}.pth` 是否存在
4. 找不到 → 嘗試 `models_registry.json → default`

### models_registry.json 結構

```json
{
  "_comment": "邏輯名 → checkpoint stem。改這裡切換模型，無需改代碼。",
  "default": "global_state_v4_1_best",
  "aliases": {
    "v3_us_stocks":  "global_state_v4_1_best",
    "v3_hk_stocks":  "global_state_v4_1_best",
    "v3_hk_v2":      "hk_attentive_v2"          ← 部署新 HK 模型時改這裡
  }
}
```

### config.json model.markets 結構

```json
"model": {
  "input_dim": 26,
  "markets": {
    "HK":   "v3_hk_stocks",   ← 改這裡換 HK 用的邏輯名
    "US":   "v3_us_stocks",   ← 改這裡換 US 用的邏輯名
    "IDLE": "v3_us_stocks"
  }
}
```

---

## 4. 訓練器速查表

| 訓練器 | 輸出文件 | 架構 | input_dim | lookback | epochs | 適用場景 |
|--------|----------|------|-----------|----------|--------|----------|
| `trainer_v4_1.py` | `global_state_v4_1_best.pth` | AttentiveKiroLSTM | 動態（~27） | 60 | 40 | **主力推薦**，支持注意力機制 |
| `trainer_base_10y.py` | `global_state_10y.pth` | KiroLSTM | 動態 | 60 | 8 | 10年數據快速預訓練 |
| `trainer_pattern_v1.py` | `global_state_pattern_v1.pth` | StockPatternModel | 動態 | 60 | 20 | 形態識別（多任務輸出） |
| `trainer_stacking.py` | `{name}_stacking/` | XGB/RF/LGB + meta | 動態 | — | — | 集成投票，抗過擬合 |
| `trainer_multi_model.py` | `{name}_multi/` | 多模型 | 動態 | — | 20(LSTM) | 對比多算法性能 |
| `trainer_regime_stacking.py` | `{name}_regime_stacking/` | 市場狀態 + meta | 動態+7 | — | — | 牛熊轉換自適應 |

**`input_dim` 由 `TechnicalIndicatorGenerator.generate()` 輸出列數決定，訓練時自動計算，無需手動指定。**

---

## 5. 逐步訓練教學

### 5A. 訓練主力模型（trainer_v4_1，推薦）

```python
# 在項目根目錄執行
import asyncio
from v3_pipeline.models.trainer_v4_1 import TrainerV41, TrainerV41Config

config = TrainerV41Config(
    symbols=["TSLA", "NVDA", "AAPL", "MSFT", "AMZN", "GOOG", "META", "NFLX", "AMD"],
    lookback=60,
    hidden_dim=96,
    num_layers=2,
    attention_heads=4,
    dropout=0.2,
    epochs=40,           # 建議 ≥ 30
    batch_size=128,
    lr=1e-3,
    output_name="global_state_v4_1_best",  # 保存到 trained_models/global_state_v4_1_best.pth
)

asyncio.run(TrainerV41(config).run())
```

**訓練 HK 專用模型（只改 symbols）：**
```python
config = TrainerV41Config(
    symbols=["0700.HK", "9988.HK", "3690.HK", "1024.HK", "0005.HK", "0388.HK",
             "0941.HK", "2318.HK", "1211.HK", "1299.HK"],
    output_name="v3_hk_v2",   # → trained_models/v3_hk_v2.pth
    epochs=40,
)
asyncio.run(TrainerV41(config).run())
```

### 5B. 訓練 10 年基礎模型（trainer_base_10y）

```python
from v3_pipeline.models.trainer_base_10y import Base10yTrainer, Base10yConfig

config = Base10yConfig(
    symbols=["TSLA", "NVDA", "AAPL", "MSFT", "AMZN", "GOOG", "META"],
    epochs=8,
    hidden_dim=96,
    lookback=60,
    output_name="global_state_10y",
)
trainer = Base10yTrainer(config)
trainer.train_global_state()
```

### 5C. 訓練 Stacking 集成模型

```bash
# 命令行
python v3_pipeline/models/trainer_stacking.py --symbol NVDA --period 3y
# 輸出到 trained_models/nvda_stacking/
```

### 5D. 手動組裝 + 保存（最底層方式）

```python
import pandas as pd
from v3_pipeline.data.downloader import HistoricalDataDownloader
from v3_pipeline.features.indicators import TechnicalIndicatorGenerator
from v3_pipeline.models.brain import AttentiveKiroLSTM
from v3_pipeline.models.manager import DataPreparer, ModelManager
from torch.utils.data import DataLoader, TensorDataset

# 1. 下載數據
dl = HistoricalDataDownloader()
ohlcv = dl.fetch_history("TSLA", "2020-01-01", "2024-12-31", interval="1d", save=True)

# 2. 生成特徵
feat_gen = TechnicalIndicatorGenerator()
featured = feat_gen.generate(ohlcv).ffill().bfill().dropna().reset_index(drop=True)

# 3. 準備訓練數據（input_dim 自動計算）
preparer = DataPreparer(lookback=60, target_col="Close")
x, y = preparer.fit_transform(featured)
print(f"input_dim = {x.shape[-1]}")   # ← 記錄這個數字！

# 4. 建立模型
model = AttentiveKiroLSTM(
    input_dim=x.shape[-1],
    hidden_dim=96,
    num_layers=2,
    dropout=0.2,
    output_dim=1,
    attention_heads=4,
)

# 5. 訓練
manager = ModelManager(model=model, data_preparer=preparer)
loader = DataLoader(TensorDataset(x, y), batch_size=128, shuffle=True)
losses = manager.train(loader, epochs=40, lr=1e-3)
print(f"Final loss: {losses[-1]:.6f}")

# 6. 保存（★ 必須用 manager.save()，不可直接 torch.save(model.state_dict())）
path = manager.save("my_new_model")
print(f"Saved to: {path}")
```

---

## 6. 部署新模型（3步）

訓練完成後，**只需改 2 個 JSON 文件**，無需修改任何 Python 代碼：

### 第 1 步：確認模型文件存在

```bash
ls -la v3_pipeline/models/trained_models/
# 應看到 my_new_model.pth
```

### 第 2 步：更新 models_registry.json

```json
// v3_pipeline/models/models_registry.json
{
  "default": "global_state_v4_1_best",
  "aliases": {
    "v3_us_stocks": "my_new_model",    ← 改這裡
    "v3_hk_stocks": "v3_hk_v2"        ← 如有 HK 專用模型也改這裡
  }
}
```

### 第 3 步：（可選）更新 config.json

如果要讓某個市場用不同邏輯名：
```json
// config.json
"model": {
  "markets": {
    "HK": "v3_hk_stocks",     ← 指向 aliases 裡的 key
    "US": "v3_us_stocks",
    "IDLE": "v3_us_stocks"
  }
}
```

### 驗證部署

```python
from v3_pipeline.models.registry import ModelRegistry
from pathlib import Path

reg = ModelRegistry.from_file(Path("v3_pipeline/models/trained_models"))
for name in ["v3_us_stocks", "v3_hk_stocks"]:
    path = reg.resolve(name)
    print(f"{name} → {path}")   # ✅ 應看到對應的 .pth 路徑
```

---

## 7. HK 市場模型注意事項

### 現有 HK 模型

| 文件 | 路徑 | 狀態 | 注意 |
|------|------|------|------|
| `lstm_v1.pth` | `models/hk/lstm_v1.pth` | ⚠️ 未啟用 | Raw state_dict（無 DataPreparer metadata），需確認 input_dim |
| `global_state_v4_1_best.pth` | `trained_models/` | ✅ 運行中 | input_dim=27，美股訓練，HK 作 fallback 用 |

### 接入 models/hk/lstm_v1.pth 的步驟

lstm_v1.pth 是 **raw state_dict**（非完整 checkpoint），直接用 `ModelManager.load()` 會缺少 scaler 信息，**必須包裝後再用**：

```python
import torch
from v3_pipeline.models.brain import KiroLSTM
from v3_pipeline.models.manager import DataPreparer, ModelManager

# 1. 查明 input_dim
raw = torch.load("models/hk/lstm_v1.pth", map_location="cpu", weights_only=False)
w = raw.get("lstm.weight_ih_l0") or raw.get("weight_ih_l0")
print(f"HK model input_dim = {w.shape[1]}, hidden_dim = {w.shape[0] // 4}")

# 2. 訓練新的 AttentiveKiroLSTM 替代（推薦）
# 使用 Section 5A 教學，symbols 改為 HK 股票列表
# output_name = "v3_hk_v2"

# 3. 部署：更新 models_registry.json aliases["v3_hk_stocks"] = "v3_hk_v2"
```

### HK 數據下載注意

```python
# HK 股票 yfinance symbol 格式：數字 + ".HK"
dl.fetch_history("0700.HK", "2020-01-01", "2024-12-31")  # 騰訊
dl.fetch_history("9988.HK", "2020-01-01", "2024-12-31")  # 阿里巴巴
```

---

## 8. 常見錯誤與解決方法

### ❌ FileNotFoundError: v3_us_stocks.pth

```
Failed to load model v3_us_stocks for market US: [Errno 2] No such file or directory
```

**原因**：`models_registry.json` 的 alias 指向的 checkpoint 不存在。

**解決**：
```bash
# 確認 trained_models 裡有哪些 .pth
ls v3_pipeline/models/trained_models/*.pth

# 更新 aliases 指向實際存在的文件
# 編輯 v3_pipeline/models/models_registry.json
```

---

### ❌ load_state_dict non-strict: missing=X unexpected=Y

```
ModelManager | WARNING | load_state_dict non-strict: missing=3 unexpected=3
```

**原因**：checkpoint 的模型架構（如 AttentiveKiroLSTM）與當前 model 實例（如 KiroLSTM）不匹配。

**解決**：V3 的 `_load_state_dict_compat()` 會自動重建架構，此 WARNING **通常無需處理**。
如有 missing keys 需確認訓練時 `model_class` key 是否正確保存。

---

### ❌ Need at least lookback=60 rows, got N

```
ValueError: Need at least lookback=60 rows, got 45
```

**原因**：推理時數據不足 60 行（系統剛啟動，buffer 未填滿）。

**解決**：等待 buffer 填滿，或在 `HistoryPrimer` 設置足夠的 priming days（`history_priming_days >= 90`）。

---

### ❌ Input dim drift detected: model=27 new=24

```
ModelManager | WARNING | Input dim drift detected: model=27 new=24. Rebuilding channels.
```

**原因**：推理時輸入特徵數與訓練時不同（不同市場/不同 TechnicalIndicatorGenerator 輸出）。

**解決**：
1. 確認 `TechnicalIndicatorGenerator` 版本一致
2. 訓練 HK 模型時使用 HK 數據（特徵數可能與 US 不同）
3. `_ensure_model_input_dim()` 會自動重建，但精度有影響，建議訓練市場專用模型

---

### ❌ AttentiveKiroLSTM 訓練後 Attention heads 不匹配

```
RuntimeError: embed_dim must be divisible by num_heads
```

**原因**：`hidden_dim` 不能被 `attention_heads` 整除。

**解決**：確保 `hidden_dim % attention_heads == 0`：
- ✅ `hidden_dim=96, attention_heads=4` → 96/4=24 ✓
- ✅ `hidden_dim=64, attention_heads=4` → 64/4=16 ✓
- ❌ `hidden_dim=96, attention_heads=7` → 96/7 ✗

---

### ❌ Checkpoint 保存為 raw state_dict（不完整）

```python
# ❌ 錯誤做法
torch.save(model.state_dict(), "model.pth")

# ✅ 正確做法（必須通過 ModelManager）
manager.save("model_name")  # 自動包含所有 scaler metadata
```

Raw state_dict 缺少 `feature_columns`、`feature_mins/maxs` 等 scaler 信息，
加載後 `DataPreparer.is_fitted = False`，推理時會拋 `RuntimeError: DataPreparer is not fitted`。

---

## 9. Agent 訓練標準作業程序

**Agent 收到訓練任務時，必須按以下順序執行：**

```
Step 1: 確認訓練目標
├── 訓練哪個市場？(US / HK / 通用)
├── 用哪個 trainer？(推薦 trainer_v4_1)
└── 輸出 checkpoint 名稱是什麼？

Step 2: 確認數據
├── 下載目標市場 symbols 的歷史數據
├── 確認數據行數 ≥ lookback × 2（至少 120 行）
└── 確認無異常 NaN / 全零列

Step 3: 訓練
├── 使用 ModelManager.save() 保存（★ 不可用 torch.save(state_dict)）
├── 記錄 input_dim（從 x.shape[-1] 讀取）
└── 記錄 final loss

Step 4: 驗證 checkpoint
├── 運行 Section 2 的驗證腳本
├── 確認所有必要 key 存在
└── 確認 input_dim 與預期一致

Step 5: 部署
├── 更新 models_registry.json aliases
├── （可選）更新 config.json model.markets
└── 用 ModelRegistry.resolve() 驗證解析正確

Step 6: 報告
└── 輸出: checkpoint 路徑、input_dim、final_loss、訓練 symbols、epochs
```

**Checklist（每次訓練必填）：**

```markdown
- [ ] checkpoint 路徑: v3_pipeline/models/trained_models/_____.pth
- [ ] model_class: KiroLSTM / AttentiveKiroLSTM / StockPatternModel
- [ ] input_dim: ___
- [ ] hidden_dim: ___
- [ ] lookback: ___
- [ ] 訓練 symbols: ___
- [ ] 訓練集時間範圍: ___ to ___
- [ ] final_loss: ___
- [ ] models_registry.json 已更新: Y/N
- [ ] ModelRegistry.resolve() 驗證通過: Y/N
```

---

*此文件由小祈整理，任何訓練相關問題優先查閱此文件。*

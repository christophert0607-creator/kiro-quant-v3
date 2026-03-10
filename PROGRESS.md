# 🔧 系統重整進度 (Kiro Quant V3)
**更新時間**: 2026-03-10

## 🎯 本輪目標
根據上一輪 review comment，完成 launcher 任務清單收尾：
1) 增加 `micro` 低資源 profile
2) 產出 `lite` 相關延遲/記憶體基準報表工具
3) 將 profile 選擇接入 dashboard 設定

## ✅ TASK LIST 進度
- [x] 盤點現有入口與配置（`v3_launcher.py`, `config.example.json`, `dashboard.py`）
- [x] 新增 `micro` runtime profile（`v3_launcher.py`）
- [x] 建立 runtime 基準檢測腳本（`scripts/benchmark_runtime_profiles.py`）
- [x] 將 runtime profile 切換接入 dashboard（`dashboard.py`）
- [x] 更新 pipeline 說明文件（`v3_pipeline/README.md`）
- [x] 執行編譯檢查（`python -m compileall`）
- [x] 執行 launcher dry-run 驗證（含 `micro`）
- [x] 執行 benchmark 腳本輸出

## 🧱 本輪調整摘要
1. **Runtime Profile 擴展**
   - 新增 `micro`：`lookback=20`、`hidden_dim=16`、`layers=1`
   - 保留 `lite` / `standard` 供常規切換
2. **運維可觀測性**
   - 新增 `scripts/benchmark_runtime_profiles.py`
   - 可輸出 `micro/lite/standard` 的 dry-run 最小延遲、平均延遲、峰值記憶體
3. **Dashboard 可操作性**
   - 側欄新增 Runtime Profile 下拉選單（`micro/lite/standard`）
   - 保存設定會寫回 `config.json -> v3_live.runtime_profile`

## 🧪 驗證結果
- 編譯檢查：通過
- dry-run：通過（`micro/lite/standard`）
- benchmark：通過（可輸出 markdown table）

## 📌 下一步建議
- 在 dashboard 顯示最近一次 benchmark 結果（可讀取報表檔）
- 若進入實跑環境，補充 profile-by-profile 的實單迴圈延遲統計

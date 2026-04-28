# KiroQuant V3 — TODO & BACKLOG
> 主動追蹤：2026-04-17 由 chat corpus 分析自動生成
> 更新方式：手動追加 or cron hook `chat-update-analysis.md`

---

## 🔴 HIGH PRIORITY — 生產環境問題

### T-001 | kiro-quant graphify rebuild cron 2× error
- **症狀：** cron job `kiro-quant graphify rebuild` 2 次連續 error，`lastError: <html>`
- **最後錯誤：** `<html>...` API 返回 HTML error page（並非 JSON）
- **可能原因：** graphify 指令超時 / AntiGravity API 頁面 / path 問題
- **懷疑路徑：** `/graphify /home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3`
- **建議fix：** 增大 `timeoutSeconds`（現時 300s）；檢查 graphify-out/ 是否正常；分開 `--update` 模式而唔係 full rebuild
- **狀態：** 🔴 Open

### T-002 | kiro-quant-daily-research timeout
- **症狀：** cron job timeout（332s），輸出 delivery status: `unknown`
- **最後錯誤：** `cron: job execution timed out`
- **建議fix：** 
  1. 加長 `timeoutSeconds`（建議 900s）
  2. 精簡 research prompt scope（減少 web_search 次數）
  3. 或改為分段：research 先 → wiki ingest 第二日再做
- **狀態：** 🔴 Open

### T-003 | 2026-04-09 auth 風暴（14 models 同時 fail）
- **症狀：** 2026-04-09 出現 14 個 model fallback 全部 fail，401 authentication_error
- **涉及 cron：** daily-email-summary、selfheal-fix、selfheal-detect、system-daily-health-check
- **可能原因：** minimax-cn API key 過期 / token refresh fail
- **建議fix：** 確認 minimax-cn key still valid；加強 AntiGravity failover chain
- **驗證方法：** `openclaw models list` 檢查 key status
- **狀態：** 🔴 Open（未確認根因）

### T-004 | V3 Heartbeat 失敗 3 次
- **症狀：** cron job `V3 Heartbeat - Monitor & Restart if Dead` fail 3 次
- **chat 來源：** chat_09_04_2026，小祈報告
- **後續：** 2026-04-11 已停用（但冇人確認根本原因）
- **建議：** 停用係合理，但如果要重啟需先確認 fail 原因（PS / log path / restart command）
- **狀態：** 🟡 Deferred（已停用，待確認是否重啟）

---

## 🟡 MEDIUM PRIORITY — 配置 / 集成缺失

### T-005 | memory-lancedb-pro plugin not found
- **症狀：** `openclaw doctor` 報告 `plugins.allow: plugin not found: memory-lancedb-pro`
- **影響：** 記憶系統異常，vector search 可能唔工作
- **建議fix：** 
  1. 確認 plugin 是否已安裝（`openclaw plugins list`）
  2. 如無需移除 `plugins.allow` 中的 memory-lancedb-pro
  3. 如有需正確設定 `plugins.entries` 配置
- **驗證：** `openclaw doctor` → 看 `plugins` 項目
- **狀態:** 🔴 Open

### T-006 | brave_api_key missing
- **症狀：** `web_search` 報 `missing_brave_api_key`
- **影響：** 所有 web_search tool 失效
- **建議fix：** 申請 Brave API key → 寫入 `~/.openclaw/auth-profiles.json` 或 `openclaw config`
- **替代：** 如無需即時用，可改用 `web_fetch`（無需 key）
- **狀態:** 🟡 Open

### T-007 | Quant agent workspace missing
- **路徑：** `/home/tsukii0607/.openclaw/agents/quant/agent/workspace`唔存在
- **影響：** quant agent 所有 workspace tool 失效
- **建議fix：** 創建 workspace 目錄
  ```bash
  mkdir -p /home/tsukii0607/.openclaw/agents/quant/agent/workspace
  ```
- **狀態:** 🟡 Open

### T-008 | Alice Phase 2.0 不完整
- **症狀：** 
  1. Alice-001 喺位置 (7,6) 04:00 持續報告 "unstable communication"
  2. `run_phase2.py` simulation lock `world_state.json` blocking `project_blessing`
  3. 格鬥/社交/信仰技能 Lv.X XP 停滯
- **建議：** 
  1. 檢查 `/workspace/alice_phase1/` 狀態
  2. 決定係咪繼續 Phase 2.0 開發 or 暫停
  3. 解決 lock conflict
- **狀態:** 🟡 Open

---

## 🟢 LOW PRIORITY — 知識 / 文件缺口

### T-009 | Decision Trace 數據量未確認
- **位置：** `learning/us_sim/decision_trace_us_sim.jsonl`
- **問題：** 未確認入面有幾多筆 data points
- **建議：** 讀取行數 + 簡單統計（by symbol, by action）
- **狀態:** 🟡 Open

### T-010 | Meta-Labeling pipeline 仍在調整
- **chat 顯示：** `meta_gate.py` 仍在修改調整
- **PROGRESS 顯示：** M0-M10 已標 ✅
- **問題：** 文件話完成，但chat顯示仍在做
- **建議：** 確認 pipeline 是否真正 production-ready
- **狀態:** 🟢 Check

### T-011 | NO_FUTU=1 模式需要確認狀態
- **問題：** V3 以 `NO_FUTU=1` YF-only 模式運行，但 chat 顯示 OpenD 有時 reconnect
- **FutuOpenD：** 11113 端口（PID 3191320）有時 ECONNREFUSED
- **建議：** 確認 OpenD 係備用定主力，定係只係偶爾連
- **狀態:** 🟢 Check

### T-012 | kiroquant sender (1,998 msgs) 未分析
- **問題：** 0417 export 中發現 `kiroquant` sender 有 1,998 條訊息
- **內容未睇：** 可能包含重要交易決策、模型表現、策略回測
- **建議：** 獨立分析 `kiroquant` 呢個 identity 嘅對話內容
- **狀態:** 🟢 Open

---

## ✅ COMPLETED（從 chat corpus 確認）

| ID | 項目 | 確認時間 |
|----|----|---------|
| C-001 | AntiGravity provider 部署 | 2026-03-15 |
| C-002 | Decision Trace collection 持續運行 | 2026-03-28 |
| C-003 | FutuOpenD reconnect | 2026-03-07 |
| C-004 | V3.5 launch 成功 | 2026-03-10 |
| C-005 | 所有 12 cron jobs 切換至 antigravity | 2026-04-07 |
| C-006 | 8 個 Kiro Quant trading crons 停用 | 2026-04-11 |
| C-007 | Self-heal every 4h 生產運行 | 2026-03 起 |
| C-008 | Kiro Quant Daily Research cron 建立 | 2026-03 起 |

---

## 📋 添加記錄

- **2026-04-17:** 由 `compare_knowledge.py` 分析 chat corpus 自動生成初稿

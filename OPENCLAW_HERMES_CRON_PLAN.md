# Kiro Quant OpenClaw + Hermes 分工及 Cron 設計

日期：2026-04-28  
時區：Asia/Hong_Kong

## 1. 現有程序分析

### 1.1 主線架構

目前有兩條 Kiro Quant 線：

| 路徑 | 定位 | 狀態 |
| --- | --- | --- |
| `/home/tsukii0607/.openclaw/workspace-quant/kiro-quant` | 統一架構/較舊主線，文件寫明有 `unified_launcher.py`、`engine/`、`ml/meta_labeling/` | 可保留作 legacy/reference |
| `/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3` | 現行 live/sim runtime，實際有 `v3_launcher.py`、`v3_pipeline/`、`state.json`、`trades.jsonl`、self-learn、daily wiki | 應視為唯一運行真相源 |

V3 runtime 流程：

1. `v3_launcher.py` 判斷 HK/US/IDLE market mode。
2. 按 mode 寫入 `config.json` 的 `v3_live.symbols_list`。
3. `LiveTradingLoop` 每個 cycle 做：
   - sync broker assets/positions
   - sync sentiment
   - batch prefetch quote
   - feature generation
   - model predict
   - confidence / meta gate / risk gate
   - place order 或寫 decision trace
4. 輸出：
   - `state.json`
   - `trades.jsonl`
   - `learning/us_sim/decision_trace_us_sim.jsonl`
   - `learning/us_sim/account_snap_us_sim.jsonl`
   - `logs/v3_live.log`
   - `auto_v3_YYYYMMDD.log` 或 `paper_trade_v3.log`

### 1.2 現況重點

| 項目 | 現況 |
| --- | --- |
| `config.json` | `auto_trade=true`、`paper_trading=false`、`futu.trd_env=SIMULATE`、OpenD port 是 `11115` |
| OpenD | 現時只有 `127.0.0.1:11115` 有 listen |
| V3 process | 掃描時見到兩個 `v3_launcher.py` 同時存在 |
| Snapshot data | `account_snap_us_sim.jsonl` 最後幾筆仍然打去 `11112`，結果是 `opend_down` |
| Trade log | `trades.jsonl` 最後交易停在 2026-04-23 |
| OpenClaw Kiro jobs | 大部分 Kiro job 停用，但 `V3 Auto Start HK` 啟用 |
| Hermes Kiro jobs | Heartbeat、HK pulse、EOD、Daily Research、Pre-market、Code Review 等多個啟用 |

## 2. 主要風險

### P0 - 交易主邏輯縮排疑似錯位

`v3_pipeline/core/main_loop.py` 的 AST 顯示：

| Function | Line range |
| --- | --- |
| `_run_trading_logic` | 525-781 |
| `_get_market_thresholds` | 784-1271 |

但 `CONF_GATE`、SHORT entry、BUY entry、SELL model confirm 等核心邏輯落在 809-1271，即屬於 `_get_market_thresholds()` function 內，而且在 function 早段 `return` 之後，實際不可達。

影響：engine 仍會跑 cycle、sync、exit logic、decision trace 部分，但主要新開倉/short entry 可能被跳過。任何 cron 設計前，應先修呢個 P0。

### P0 - 多 engine instance

現時見到兩個 `v3_launcher.py` 進程，已知歷史問題是多 engine 會爭 OpenD、重複寫 `state.json`/`trades.jsonl`、造成倉位同步錯亂。  
任何 autostart/heartbeat 必須先做 singleton guard。

### P1 - OpenD port 分裂

現行 `config.json` 係 `11115`，但多個腳本/cron 仍寫死 `11112` 或舊文件提 `11113`。

受影響：

| Component | 問題 |
| --- | --- |
| `scripts/us_sim_snapshot.py` / wrapper | default 11112 |
| Hermes US SIM Snapshot | prompt 寫死 11112 |
| OpenClaw disabled snapshot jobs | prompt 寫死 11112 |
| health_check.py | US/HK 都寫死 11112 |
| V3 active config | 用 11115 |

建議：以 `kiro-quant-v3/config.json:futu.port` 作唯一來源，cron prompt 唔好寫死 port。

### P1 - Cron 權責重疊

OpenClaw 有 `V3 Auto Start HK`，Hermes 有 `V3 Heartbeat`，兩邊都有機會起/重啟 engine。  
建議只保留一方負責「會 kill/start process」的行為，另一方只做觀察和報告。

### P1 - HK Pulse 寫錯 config 目標

Hermes HK Pulse prompt 仍寫 `config.sim.json`，但 V3 launcher 讀 `config.json`。  
結果：pulse 有可能做咗分析，但 runtime 完全唔食設定。

### P2 - Cron prompt 內含敏感 env

OpenClaw `V3 Auto Start HK` prompt 有直接 export 外部 API key。  
建議全部搬到 `.env` / OpenClaw secret / Hermes env，cron prompt 只引用 env name。

## 3. 分工表

| 範圍 | 主責 | 副責 | 寫入權限 | 輸出 |
| --- | --- | --- | --- | --- |
| V3 runtime singleton / start / restart | OpenClaw | Hermes 只報告 | 可 kill/start `v3_launcher.py`，但只限 singleton guard 通過 | P0/P1 Telegram alert、process status |
| OpenD connectivity / port truth | OpenClaw | Hermes | 讀 `config.json`，必要時啟 OpenD；唔寫死 port | OpenD OK/DOWN、port mismatch alert |
| Market pulse 參數調整 | Hermes | OpenClaw config-audit | 只可改 `config.json` allowlist keys | posture、clamped diff、risk mode |
| Pre-market intelligence | Hermes | Kiro Quant | 不改交易程式，可寫 `sentiment.json` / daily notes | HK/US 盤前摘要 |
| In-session status | Hermes | OpenClaw | 只讀 `state.json`、logs、positions | 巡航摘要或 `[SILENT]` |
| US SIM snapshots | Hermes | OpenClaw audit | append-only 到 `learning/us_sim/account_snap_us_sim.jsonl` | 成功 silent，失敗一行 alert |
| EOD learning report | Hermes | Quant | 只讀 snapshot/decision trace，寫 reports markdown | 每日報告 |
| Daily research/wiki | Hermes | Quant | 寫 wiki/raw_sources，不改 runtime | research + wiki ingest |
| Code review / structural lint | Hermes | Codex/Dev | 只讀或報告，修 code 需用戶批准 | 每日問題清單 |
| Core bug fix / runtime code change | Codex/Dev | OpenClaw 派工 | 改 code、跑測試；交易安全改動要先確認 | patch + verification |
| Emergency escalation | OpenClaw | Hermes | 不自動下單；只告警/停引擎 | P0 alert + handoff package |

## 4. Cron 設計總則

1. OpenClaw 做 control plane：起停、singleton、防重入、P0 escalation。
2. Hermes 做 operations analyst：研究、pulse、snapshot、EOD、巡航、每日 code review。
3. 只允許一個 job 會 start/restart V3。
4. 所有 OpenD port / acc / env 由 `config.json` 或 env 讀取，不在 prompt 寫死。
5. 市場時段 job 必須支援 `[SILENT]` / `NO_REPLY`，避免 Telegram 噪音。
6. 任何會改 `config.json` 的 job 必須：
   - 先 backup
   - 只改 allowlist keys
   - clamp 數值
   - 輸出 diff
   - JSON parse 驗證
7. P0 fix 未完成前，不建議啟用「會增加交易頻率」的 pulse/autostart。

## 5. 建議 Cron Job

### 5.1 OpenClaw jobs

| Job | Schedule HKT | Owner | 目的 | 行為 |
| --- | --- | --- | --- | --- |
| `kiro-v3-structural-lint` | `10 8 * * 1-5` | OpenClaw main/dev | 開市前防 P0 code 形狀錯 | `py_compile` + AST check：`_run_trading_logic` 必須包含 `BUY_PLACED`/`SHORT_PLACED` blocks；失敗就 P0 alert，不起 engine |
| `kiro-v3-singleton-guard` | `15 9,21 * * 1-5` | OpenClaw main | 開市前清理重複 engine | 若 0 process：交畀 autostart；若 1：OK；若 >1：P0 alert，按策略只保留最新/最健康一個 |
| `kiro-v3-autostart` | `25 9 * * 1-5` | OpenClaw main | HK 開市前啟動單一 engine | 讀 `config.json`，用 env 啟動，log 統一寫 `logs/runtime-current.log` |
| `kiro-v3-us-session-guard` | `05 21 * * 1-5` | OpenClaw main | US 開市前確認 engine 已在單一狀態 | 不重啟健康 engine；缺失才啟動 |
| `kiro-v3-p0-watch` | `*/15 9-16,21-23,0-4 * * 1-5` | OpenClaw main | 市場時段 P0 監控 | 檢查 duplicate、OpenD port mismatch、log stale、state write stale；健康 silent |
| `kiro-v3-config-audit` | `5 9-12,13-16,21-23,0-4 * * 1-5` | OpenClaw dev | Hermes pulse 後驗證 config | JSON parse、allowlist diff、clamp；異常 rollback + alert |

OpenClaw P0 watcher 條件：

| 條件 | 等級 | 動作 |
| --- | --- | --- |
| `v3_launcher.py` > 1 | P0 | alert；暫停 autostart；建議人工/Dev 清理 |
| `_run_trading_logic` AST 不含 entry blocks | P0 | alert；禁止重啟 live trade |
| OpenD listen port != `config.json:futu.port` | P1 | alert；snapshot/health check 用 config port |
| runtime log 20 分鐘無更新 | P1 | 如 process dead 才 restart；如 process alive 先報 stalled |
| `state.json` 超過一個交易日未更新而 market active | P1 | alert |

### 5.2 Hermes jobs

| Job | Schedule HKT | Owner | 寫入 | 備註 |
| --- | --- | --- | --- | --- |
| `KiroQuant - Daily Code Review` | `50 7 * * 1-5` | Hermes | 報告 only | 加 AST structural lint，不直接修 code |
| `KiroQuant - Daily Research` | `0 8 * * 1-5` | Hermes | wiki/raw_sources | 可保留現有 |
| `KiroQuant - HK Pre-Market Review` | `30 8 * * 1-5` | Hermes | `sentiment.json` optional | 可保留現有 |
| `KiroQuant - HK Market Pulse` | `0,30 9-12,13-16 * * 1-5` | Hermes | `config.json` allowlist | 改 `config.sim.json` 為 `config.json` |
| `KiroQuant - HK Status Snapshot` | `5 10,12,15 * * 1-5` | Hermes | read-only | 只報異常或重要持倉 |
| `KiroQuant - US Pre-Market Review` | `0 21 * * 1-5` | Hermes | `sentiment.json` optional | 可保留現有 |
| `KiroQuant - US Sentiment Pulse` | `0,30 21-23,0-3 * * 1-5` | Hermes | `sentiment.json` | 不改 trading config |
| `KiroQuant - US SIM Snapshot` | `*/5 21-23,0-4 * * 1-5` | Hermes | append-only snapshot | 讀 `config.json:futu.port`，現時應用 11115 |
| `KiroQuant - US SIM EOD Report` | `5 4 * * 1-5` | Hermes | reports markdown | Snapshot 修復前報告價值有限 |
| `KiroQuant - Monthly Log Compression` | `0 6 1 * *` | Hermes | logs_archive | 可保留現有 |

## 6. Job Prompt 設計重點

### OpenClaw: structural lint

```text
你係 KiroQuant P0 structural lint。
只讀，不改檔。

1. cd /home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3
2. python3 -m py_compile v3_launcher.py v3_pipeline/core/main_loop.py
3. 用 ast 檢查 LiveTradingLoop:
   - _run_trading_logic.end_lineno 應覆蓋 BUY/SELL/SHORT entry blocks
   - _get_market_thresholds 不應包含 CONF_GATE/SHORT_CONF_GATE/BUY_PLACED 字串所在行
4. 若 fail：輸出 P0，列 line range 和影響
5. 若 pass：NO_REPLY
```

### OpenClaw: singleton guard / autostart

```text
你係 KiroQuant runtime supervisor。
目標：確保只有一個 v3_launcher.py。

1. 讀 config.json，取得 futu.port / trd_env / paper_trading / auto_trade。
2. ps 找出所有 v3_launcher.py，列 cwd/log 目標。
3. 若 >1：不要盲目重啟，先 P0 alert；如在 autostart window 且用戶已授權，保留最新由 supervisor 起的 PID。
4. 若 0 且 structural lint pass：cd kiro-quant-v3，用 env 啟動。
5. 啟動命令不得在 prompt 內寫 API key。
6. 成功後寫 pid file：/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3/v3_pid.txt
```

### Hermes: US SIM snapshot

```text
你係 Kiro Quant US SIM Snapshot。
只讀，不落單。成功 [SILENT]。

1. cd /home/tsukii0607/.openclaw/workspace-quant
2. 從 kiro-quant-v3/config.json 讀 futu.host/futu.port/futu.target_acc_id。
3. python3 scripts/us_sim_snapshot.py --acc-id <target_acc_id> --host <host> --port <port>
4. 若 exit 0： [SILENT]
5. 若 fail：一行繁中告警，包含 port、exit、最後錯誤。
```

### Hermes: HK market pulse

```text
你係 Kiro Quant HK Market Pulse。
只可以改 /home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3/config.json。

1. backup config.json 到 config.json.bak.<timestamp>
2. 用 pulse_check.py / yfinance 計 2800.HK、3033.HK momentum。
3. 判斷 posture: risk_on / neutral / risk_off。
4. 只改 allowlist:
   - posture/posture_timestamp/posture_rationale
   - v3_live.prediction_thresholds: 0700.HK, 9988.HK, 3690.HK
   - v3_live.rsi_oversold, rsi_overbought
   - v3_live.swing_buy_confirmation_count
   - v3_live.buy_cooldown_cycles
5. Clamp:
   - threshold [0.0010, 0.0100]
   - rsi_oversold [30, 55]
   - rsi_overbought [65, 82]
   - confirmation [1, 3]
   - cooldown [0, 5]
6. JSON parse pass 後輸出 diff；無變化 [SILENT]。
```

## 7. 建議優先次序

| Priority | 工作 | 原因 |
| --- | --- | --- |
| P0 | 修正 `main_loop.py` 縮排，令 `_run_trading_logic` 包含 entry logic | 否則 engine 可能唔會新開倉 |
| P0 | 建 singleton guard，停止多 V3 process | 防止重複交易/狀態競爭 |
| P1 | 統一 OpenD port，改 snapshot/health/prompt 全部讀 `config.json` | 現時 11112 jobs 會失敗 |
| P1 | 決定 runtime owner：OpenClaw 負責 start/restart，Hermes 負責 report/pulse | 避免兩套 cron 互相重啟 |
| P1 | 修 Hermes HK Pulse 寫 `config.json` | 目前寫 `config.sim.json` 對 active launcher 無效 |
| P2 | 將 prompt 內 API key 移入 env/secret | 降低憑證外洩風險 |
| P2 | Snapshot 恢復後再看 EOD report | 否則 EOD 只會分析舊/失敗 snapshot |

## 8. 建議保留/停用

### 保留並改良

| 系統 | Job | 動作 |
| --- | --- | --- |
| OpenClaw | `V3 Auto Start HK` | 改成 singleton + structural lint pass 後先起；移除 prompt 內 secret |
| Hermes | `V3 Heartbeat` | 改為 read-only report 或改由 OpenClaw 接管 restart |
| Hermes | `HK Market Pulse` | 改寫 `config.json`，加 rollback |
| Hermes | `US SIM EOD Report` | 保留，但依賴 snapshot 修復 |
| Hermes | `Daily Code Review` | 加 AST structural lint |

### 暫停或避免重開

| Job 類型 | 原因 |
| --- | --- |
| 多個重複 US SIM Snapshot every 5m job | 會重複寫入或打錯 port |
| OpenClaw 舊 HK/US pulse | 與 Hermes pulse 重疊 |
| Meta-labeling Dev Loop | 會改 code，應保持 paused，等用戶批准 |
| 任何直接落單/改風控的 research job | 應分離為只寫報告，不直接改 runtime |

## 9. 最小落地方案

先做三件事就夠：

1. 修 `main_loop.py` P0 縮排。
2. OpenClaw 新增 `kiro-v3-structural-lint` + `kiro-v3-singleton-guard`。
3. Hermes 更新 snapshot/pulse prompt，全部讀 `config.json` 的 port，pulse 改寫 `config.json`。

做到呢三件，Kiro Quant 會由「兩套 cron 搶住管一個 engine」變成：

```text
OpenClaw = runtime supervisor / emergency brake
Hermes   = analyst / pulse / report / learning data collector
Kiro V3  = single trading engine
Codex/Dev = approved code change executor
```

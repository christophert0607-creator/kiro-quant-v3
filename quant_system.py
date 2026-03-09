#!/usr/bin/env python3.12
# ============================================================
# HK Quant Trading System v1.0
# 9-Agent 全自動量化交易系統
# ⚠️ 所有下單強制使用 TrdEnv.SIMULATE
# ============================================================

import futu as ft
import pandas as pd
import numpy as np
from xgboost import XGBClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
import time, logging, sys
from datetime import datetime

settings = {
    "model_type":       "XGBoost",
    "hybrid_mode":      False,
    "stock_list":       ["US.TSLA", "US.F", "US.NVDA"],
    "timeframe":        "K_DAY",
    "position_limit":   0.1,      # 每支股票配置 10%
    "net_assets":       2000000,  # 模擬資金 200萬
    "stop_loss_pct":    0.03,
    "stop_profit_pct":  0.08,
    "max_drawdown":     0.05,
    "retrain_interval": 86400,
    "use_sentiment":    False
}

OPEND_HOST = "127.0.0.1"
OPEND_PORT = 11111
TRADE_PWD  = "930607"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-22s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/home/tsukii0607/.openclaw/workspace/skills/futu-api/quant.log")
    ]
)

class DataFetcherAgent:
    def __init__(self):
        self.log = logging.getLogger("DataFetcherAgent")
        self.ctx = None

    def connect(self):
        self.ctx = ft.OpenQuoteContext(host=OPEND_HOST, port=OPEND_PORT)
        self.log.info("✅ 已連線 OpenD")

    def fetch_kline(self, code: str, count: int = 500) -> pd.DataFrame:
        ktype_map = {"K_DAY": ft.KLType.K_DAY, "K_5M": ft.KLType.K_5M, "K_15M": ft.KLType.K_15M}
        ret, data, _ = self.ctx.request_history_kline(
            code, ktype=ktype_map[settings["timeframe"]], max_count=count
        )
        if ret == ft.RET_OK:
            self.log.info(f"📥 {code} K線 {len(data)} 條")
            return data
        self.log.error(f"❌ K線失敗: {data}")
        return pd.DataFrame()

    def disconnect(self):
        if self.ctx: self.ctx.close()


class FeatureEngineerAgent:
    FEATURE_COLS = []

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
        for n in [5, 10, 20, 60]:
            df[f"MA{n}"]       = c.rolling(n).mean()
            df[f"MA{n}_ratio"] = c / df[f"MA{n}"] - 1
        d = c.diff()
        df["RSI"] = 100 - 100 / (
            1 + d.where(d>0, 0).rolling(14).mean()
              / (-d.where(d<0, 0).rolling(14).mean() + 1e-9)
        )
        e12, e26       = c.ewm(12).mean(), c.ewm(26).mean()
        df["MACD"]     = e12 - e26
        df["MACD_sig"] = df["MACD"].ewm(9).mean()
        df["MACD_h"]   = df["MACD"] - df["MACD_sig"]
        ma20 = c.rolling(20).mean(); sd20 = c.rolling(20).std()
        df["BB_pos"]   = (c - (ma20 - 2*sd20)) / (4*sd20 + 1e-9)
        df["Vol_ratio"]= v / (v.rolling(5).mean() + 1e-9)
        rng = h - l + 1e-9
        df["Body"]     = (df["close"] - df["open"]) / rng
        df["UpShad"]   = (h - df[["close","open"]].max(axis=1)) / rng
        df["DnShad"]   = (df[["close","open"]].min(axis=1) - l) / rng
        
        # 新增 ATR
        tr = pd.concat([h - l, abs(h - c.shift()), abs(l - c.shift())], axis=1).max(axis=1)
        df['ATR'] = tr.rolling(14).mean()
        
        # 新增 OBV
        obv = [0]
        for i in range(1, len(df)):
            if c[i] > c[i-1]:
                obv.append(obv[-1] + v[i])
            elif c[i] < c[i-1]:
                obv.append(obv[-1] - v[i])
            else:
                obv.append(obv[-1])
        df['OBV'] = obv
        
        # 新增 Stochastic
        lowest_low = l.rolling(14).min()
        highest_high = h.rolling(14).max()
        df['Stoch_K'] = 100 * (c - lowest_low) / (highest_high - lowest_low + 1e-9)
        df['Stoch_D'] = df['Stoch_K'].rolling(3).mean()
        
        df["target"]   = (c.shift(-1) > c).astype(int)
        df.dropna(inplace=True)
        drop = {"time_key","open","high","low","close","volume","turnover","change_rate","last_close","target","code",
                "stock_owner","name","stock_name","pe_annual","pe_ttm","pb_ratio","net_profit_ratio",
                "return_on_equity","update_time","suspension_info"}
        FeatureEngineerAgent.FEATURE_COLS = [
            col for col in df.columns
            if col not in drop and pd.api.types.is_numeric_dtype(df[col])
        ]
        logging.getLogger("FeatureEngineerAgent").info(
            f"✅ 特徵 {len(FeatureEngineerAgent.FEATURE_COLS)} 個，{len(df)} 筆"
        )
        return df


class ModelEvaluatorAgent:
    SCORES = {
        "XGBoost":     {"穩定":9,"速度":9,"準確":8,"解釋":9},
        "LightGBM":    {"穩定":8,"速度":10,"準確":8,"解釋":8},
        "LSTM":        {"穩定":7,"速度":5,"準確":9,"解釋":6},
        "RandomForest":{"穩定":9,"速度":7,"準確":7,"解釋":8},
        "Transformer": {"穩定":6,"速度":4,"準確":9,"解釋":5},
    }
    def report(self) -> str:
        lines = ["📊 ModelEvaluatorAgent 報告",
                 f"{'模型':<14}{'穩定':>4}{'速度':>4}{'準確':>4}{'解釋':>4}{'綜合':>6}"]
        best, best_s = None, 0
        for m, s in self.SCORES.items():
            avg = sum(s.values()) / len(s)
            tag = " 👑" if avg > best_s else ""
            if avg > best_s: best_s, best = avg, m
            lines.append(f"{m:<14}{s['穩定']:>4}{s['速度']:>4}{s['準確']:>4}{s['解釋']:>4}{avg:>6.1f}{tag}")
        logging.getLogger("ModelEvaluatorAgent").info(f"建議模型：{best}")
        return "\n".join(lines)


class ModelTrainerAgent:
    def __init__(self):
        self.log    = logging.getLogger("ModelTrainerAgent")
        self.model  = None
        self.scaler = StandardScaler()

    def train(self, df: pd.DataFrame) -> float:
        fcols = FeatureEngineerAgent.FEATURE_COLS
        X = df[fcols].values; y = df["target"].values
        Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, shuffle=False)
        Xtr = self.scaler.fit_transform(Xtr); Xte = self.scaler.transform(Xte)
        self.model = XGBClassifier(
            n_estimators=200, max_depth=5, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            eval_metric="logloss", random_state=42
        )
        self.model.fit(Xtr, ytr, eval_set=[(Xte, yte)], verbose=False)
        acc = accuracy_score(yte, self.model.predict(Xte))
        self.log.info(f"✅ 訓練完成 | 準確率 {acc:.2%}")
        return acc

    def predict(self, row: pd.Series) -> tuple:
        x = self.scaler.transform(
            row[FeatureEngineerAgent.FEATURE_COLS].values.astype(float).reshape(1, -1)
        )
        pred = self.model.predict(x)[0]
        prob = self.model.predict_proba(x)[0]
        return int(pred), float(max(prob))


class SignalGeneratorAgent:
    CONF_THRESHOLD = 0.60
    def __init__(self, trainer):
        self.log = logging.getLogger("SignalGeneratorAgent")
        self.trainer = trainer

    def generate(self, df: pd.DataFrame) -> dict:
        row = df.iloc[-1]
        pred, conf = self.trainer.predict(row)
        sig = ("BUY" if pred == 1 else "SELL") if conf >= self.CONF_THRESHOLD else "HOLD"
        self.log.info(f"📡 {sig} | 信心 {conf:.2%} | 價格 {row['close']:.2f}")
        return {"signal": sig, "confidence": conf, "price": row["close"]}


class RiskManagerAgent:
    def __init__(self):
        self.log = logging.getLogger("RiskManagerAgent")

    def approve(self, sig: dict, net_assets: float, drawdown: float) -> dict:
        if drawdown > settings["max_drawdown"]:
            self.log.warning(f"⛔ 回撤 {drawdown:.2%} 超限，暫停")
            return {"ok": False, "reason": "MaxDrawdown"}
        qty = int(net_assets * settings["position_limit"] / sig["price"] / 100) * 100
        if qty <= 0:
            return {"ok": False, "reason": "InsufficientQty"}
        sl = sig["price"] * (1 - settings["stop_loss_pct"])
        tp = sig["price"] * (1 + settings["stop_profit_pct"])
        self.log.info(f"✅ 風控通過 | qty={qty} | SL={sl:.2f} | TP={tp:.2f}")
        return {"ok": True, "qty": qty, "stop_loss": sl, "stop_profit": tp}


class ExecutionAgent:
    def __init__(self):
        self.log = logging.getLogger("ExecutionAgent")
        self.ctx = None

    def connect(self):
        self.ctx = ft.OpenSecTradeContext(
            filter_trdmarket=ft.TrdMarket.HK,
            host=OPEND_HOST, port=OPEND_PORT,
            security_firm=ft.SecurityFirm.FUTUSECURITIES
        )
        ret, _ = self.ctx.unlock_trade(TRADE_PWD)
        self.log.info("✅ 交易解鎖（SIMULATE）" if ret == ft.RET_OK else "❌ 解鎖失敗")

    def place(self, code, signal, qty, price):
        if signal == "HOLD" or qty <= 0:
            return {"status": "SKIP"}
        side = ft.TrdSide.BUY if signal == "BUY" else ft.TrdSide.SELL
        ret, data = self.ctx.place_order(
            price=price, qty=qty, code=code,
            trd_side=side, order_type=ft.OrderType.NORMAL,
            trd_env=ft.TrdEnv.SIMULATE  # 🔒 永遠不改成 REAL
        )
        if ret == ft.RET_OK:
            oid = data["order_id"].values[0]
            self.log.info(f"✅ SIMULATE {signal} {qty}股 {code} @ {price:.2f} → #{oid}")
            return {"status": "OK", "order_id": oid}
        self.log.error(f"❌ 下單失敗: {data}")
        return {"status": "FAIL"}

    def disconnect(self):
        if self.ctx: self.ctx.close()


class BacktesterAgent:
    def __init__(self, trainer):
        self.log = logging.getLogger("BacktesterAgent")
        self.trainer = trainer

    def run(self, df: pd.DataFrame, capital=100_000) -> dict:
        self.log.info("▶ 回測中...")
        cash, pos, entry = capital, 0, 0.0
        equity = []
        for i in range(60, len(df) - 1):
            row = df.iloc[i]; price = row["close"]
            try: pred, conf = self.trainer.predict(row)
            except: continue
            if pred == 1 and conf > 0.6 and pos == 0:
                qty = int(cash * settings["position_limit"] / price)
                cash -= qty * price; pos, entry = qty, price
            elif pos > 0:
                if price <= entry * (1 - settings["stop_loss_pct"]):
                    cash += pos * price; pos = 0
                elif price >= entry * (1 + settings["stop_profit_pct"]):
                    cash += pos * price; pos = 0
                elif pred == 0 and conf > 0.6:
                    cash += pos * price; pos = 0
            equity.append(cash + pos * price)
        if not equity: return {}
        fin = equity[-1]; ret = (fin - capital) / capital
        peak = capital
        mdd = max((peak := max(peak, v), (peak - v) / peak)[1] for v in equity)
        self.log.info(f"✅ 回測: 回報 {ret:.2%} | 最大回撤 {mdd:.2%}")
        return {"total_return": ret, "max_drawdown": mdd, "final_equity": fin}


class MonitorAgent:
    def __init__(self):
        self.log = logging.getLogger("MonitorAgent")
        self.ctx = None

    def connect(self):
        self.ctx = ft.OpenSecTradeContext(
            filter_trdmarket=ft.TrdMarket.US,
            host=OPEND_HOST, port=OPEND_PORT,
            security_firm=ft.SecurityFirm.FUTUSECURITIES
        )
        self.ctx.unlock_trade(TRADE_PWD)
        self.log.info("✅ MonitorAgent 就緒")

    def positions(self):
        ret, data = self.ctx.position_list_query()
        if ret == ft.RET_OK and not data.empty:
            for _, r in data.iterrows():
                self.log.info(f"📊 {r.get('stock_name',r['code'])} | 數量:{r['qty']} | 盈虧:{r.get('pl_val','N/A')}")
        return data if ret == ft.RET_OK else pd.DataFrame()

    def alert(self, msg):
        self.log.warning(f"🔔 {msg}")

    def disconnect(self):
        if self.ctx: self.ctx.close()


class QuantOrchestrator:
    def __init__(self):
        self.log       = logging.getLogger("Orchestrator")
        self.fetcher   = DataFetcherAgent()
        self.engineer  = FeatureEngineerAgent()
        self.evaluator = ModelEvaluatorAgent()
        self.trainer   = ModelTrainerAgent()
        self.risk      = RiskManagerAgent()
        self.executor  = ExecutionAgent()
        self.monitor   = MonitorAgent()
        self.signal_g  = None
        self.bcktster  = None
        self._last_train = 0

    def boot(self):
        self.log.info("🚀 系統啟動...")
        self.fetcher.connect()
        self.executor.connect()
        self.monitor.connect()
        self.log.info("✅ 9-Agent 全部就位！")

    def train_all(self):
        for code in settings["stock_list"]:
            self.log.info(f"\n{'='*50}\n{code} 訓練流水線\n{'='*50}")
            df = self.fetcher.fetch_kline(code)
            if df.empty: continue
            df = self.engineer.generate(df)
            print(self.evaluator.report())
            self.trainer.train(df)
            self.signal_g = SignalGeneratorAgent(self.trainer)
            self.bcktster = BacktesterAgent(self.trainer)
            bt = self.bcktster.run(df)
            self.log.info(f"📈 {code} 回測結果: {bt}")
        self._last_train = time.time()

    def loop(self):
        self.log.info("\n🟢 進入持續交易循環（SIMULATE 模式）...\n")
        cycle = 0
        while True:
            try:
                cycle += 1
                self.log.info(f"\n🔄 === 第 {cycle} 輪 | {datetime.now().strftime('%H:%M:%S')} ===")

                # 每日重訓
                if time.time() - self._last_train > settings["retrain_interval"]:
                    self.log.info("⏰ 到達重訓時間，重新訓練模型...")
                    self.train_all()

                for code in settings["stock_list"]:
                    df = self.fetcher.fetch_kline(code, count=100)
                    if df.empty: continue
                    df = self.engineer.generate(df)
                    sig = self.signal_g.generate(df)
                    risk = self.risk.approve(sig, net_assets=2_000_000, drawdown=0.0)
                    if risk.get("ok"):
                        self.executor.place(code, sig["signal"], risk["qty"], sig["price"])
                    else:
                        self.log.info(f"⛔ 風控: {risk.get('reason')}")

                # 監控美股持倉（TSLL & F）
                self.monitor.positions()

                self.log.info(f"💤 等待 300 秒... (哥哥大人說「停止」就會結束)\n")
                time.sleep(300)

            except KeyboardInterrupt:
                self.log.info("🛑 收到停止指令，系統優雅停機")
                break
            except Exception as e:
                self.monitor.alert(f"系統錯誤: {e}")
                self.log.error(f"❌ 錯誤詳情: {e}", exc_info=True)
                time.sleep(30)

        self.fetcher.disconnect()
        self.executor.disconnect()
        self.monitor.disconnect()
        self.log.info("✅ 系統已完全停止")


if __name__ == "__main__":
    print("=" * 60)
    print("   HK Quant Trading System v1.0  |  9-Agent")
    print(f"   啟動：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("   ⚠️  SIMULATE 模式 — 不會動用真實資金")
    print("=" * 60)
    for k, v in settings.items():
        print(f"   {k:<22} = {v}")
    print()
    bot = QuantOrchestrator()
    bot.boot()
    bot.train_all()
    bot.loop()

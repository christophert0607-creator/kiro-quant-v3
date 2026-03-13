import streamlit as st
import pandas as pd
import json
import os
import time
from datetime import datetime
from execution_engine import ExecutionEngine
import state_store as ss

# 設定頁面資訊
st.set_page_config(page_title="Futu Quant Dashboard", layout="wide")

# 自訂按鈕顏色（緊急按鈕）
st.markdown("""<style>
div.stButton > button[kind='primary'] {
    background-color: #b91c1c;
    border-color: #b91c1c;
    color: white;
}
</style>""", unsafe_allow_html=True)

# 路徑定義
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MARKET_DATA_PATH = os.path.join(BASE_DIR, "market_data.csv")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
# 假設 sentiment 資料可能也存儲在某處，若無則模擬展示
SENTIMENT_DATA_PATH = os.path.join(BASE_DIR, "sentiment_history.csv") 
PATTERN_SIGNALS_PATH = os.path.join(BASE_DIR, "v3_pipeline", "logs", "pattern_signals_latest.csv")

# 初始化 config.json 如果不存在
if not os.path.exists(CONFIG_PATH):
    default_config = {
        "TSLA": {"buy_price": 380.0, "sell_price": 410.0},
        "TSLL": {"buy_price": 13.5, "sell_price": 15.0},
        "auto_trade": False
    }
    with open(CONFIG_PATH, 'w') as f:
        json.dump(default_config, f, indent=4)

def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)

def save_config(config):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=4)

def load_market_data():
    if os.path.exists(MARKET_DATA_PATH):
        df = pd.read_csv(MARKET_DATA_PATH)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        return df
    return pd.DataFrame()

# 模擬 Sentiment 資料 (若沒找到對應 csv)
def load_sentiment_data():
    if os.path.exists(SENTIMENT_DATA_PATH):
        return pd.read_csv(SENTIMENT_DATA_PATH)
    else:
        # Mock data for demonstration
        import numpy as np
        dates = pd.date_range(end=datetime.now(), periods=20, freq='H')
        return pd.DataFrame({
            'timestamp': dates,
            'sentiment_score': np.random.uniform(0.3, 0.8, size=20)
        })


def load_pattern_signals():
    if os.path.exists(PATTERN_SIGNALS_PATH):
        pdf = pd.read_csv(PATTERN_SIGNALS_PATH)
        if not pdf.empty and 'timestamp' in pdf.columns:
            pdf['timestamp'] = pd.to_datetime(pdf['timestamp'], errors='coerce')
        return pdf
    return pd.DataFrame()

# --- UI 介面 ---
st.title("📊 Futu API 交易控制面板")

# 側邊欄：交易配置
st.sidebar.header("⚙️ 交易配置")
config = load_config()

st.sidebar.subheader("TSLA 設定")
tsla_buy = st.sidebar.number_input("TSLA 買入價", value=float(config.get("TSLA", {}).get("buy_price", 380.0)))
tsla_sell = st.sidebar.number_input("TSLA 賣出價", value=float(config.get("TSLA", {}).get("sell_price", 410.0)))

st.sidebar.subheader("TSLL 設定")
tsll_buy = st.sidebar.number_input("TSLL 買入價", value=float(config.get("TSLL", {}).get("buy_price", 13.5)))
tsll_sell = st.sidebar.number_input("TSLL 賣出價", value=float(config.get("TSLL", {}).get("sell_price", 15.0)))

auto_trade = st.sidebar.toggle("啟用自動交易 (Auto-trade)", value=config.get("auto_trade", False))

if st.sidebar.button("保存設定"):
    config["TSLA"] = {"buy_price": tsla_buy, "sell_price": tsla_sell}
    config["TSLL"] = {"buy_price": tsll_buy, "sell_price": tsll_sell}
    config["auto_trade"] = auto_trade
    save_config(config)
    st.sidebar.success("設定已保存！")

# 主界面：即時行情
col1, col2 = st.columns(2)

df_market = load_market_data()

with col1:
    st.subheader("📈 實時價格 (market_data.csv)")
    if not df_market.empty:
        # 顯示最新價格
        latest = df_market.groupby('symbol').last().reset_index()
        for _, row in latest.iterrows():
            st.metric(f"{row['symbol']} 最新價格", f"{row['price']:.2f}", help=f"來源: {row['source']}")
        
        st.dataframe(df_market.sort_values('timestamp', ascending=False).head(10), use_container_width=True)
    else:
        st.warning("尚未讀取到市場資料。")

with col2:
    st.subheader("🔍 來源分佈 (Source Distribution)")
    if not df_market.empty:
        source_counts = df_market['source'].value_counts()
        st.bar_chart(source_counts)
    else:
        st.info("無來源分佈資料。")

# Sentiment 指標走勢
st.divider()
st.subheader("🧠 Sentiment 指標走勢")
df_sent = load_sentiment_data()
if not df_sent.empty:
    st.line_chart(df_sent.set_index('timestamp'))
else:
    st.write("暫無 Sentiment 資料。")

st.divider()
st.subheader("🧩 Pattern Heatmap")
df_pattern = load_pattern_signals()
if not df_pattern.empty and {'symbol', 'pattern', 'confidence'}.issubset(df_pattern.columns):
    heatmap = (
        df_pattern.sort_values('timestamp')
        .groupby(['symbol', 'pattern'], as_index=False)['confidence']
        .mean()
        .pivot(index='symbol', columns='pattern', values='confidence')
        .fillna(0.0)
    )
    st.dataframe(heatmap.style.background_gradient(cmap='YlOrRd', axis=None), use_container_width=True)
    latest = df_pattern.sort_values('timestamp').tail(10)
    preferred_cols = ['timestamp', 'symbol', 'pattern', 'confidence', 'predicted_move']
    visible_cols = [c for c in preferred_cols if c in latest.columns]
    st.dataframe(latest[visible_cols], use_container_width=True)
else:
    st.info("尚未有 pattern 訊號資料。")


# 緊急控制區
st.divider()
st.subheader("🛑 安全控制")
if "engine" not in st.session_state:
    st.session_state.engine = ExecutionEngine()
if "confirm_liquidation" not in st.session_state:
    st.session_state.confirm_liquidation = False
if "bot_running" not in st.session_state:
    st.session_state.bot_running = config.get("auto_trade", False)

ctl_col1, ctl_col2, ctl_col3 = st.columns(3)
with ctl_col1:
    if st.button("⏹️ 停止 Bot", type="secondary"):
        st.session_state.bot_running = False
        config["auto_trade"] = False
        save_config(config)
        st.warning("Bot 已停止，停止接收交易信號。")

with ctl_col2:
    if st.button("🚨 緊急平倉", type="primary"):
        st.session_state.confirm_liquidation = True

with ctl_col3:
    if st.button("▶️ 啟動 Bot"):
        st.session_state.bot_running = True
        config["auto_trade"] = True
        save_config(config)
        st.success("Bot 已啟動。")

if st.session_state.confirm_liquidation:
    st.error("確認執行緊急平倉？此操作將賣出全部持倉。")
    confirm_col, cancel_col = st.columns(2)
    with confirm_col:
        if st.button("確認平倉", type="primary"):
            engine = st.session_state.engine
            if not engine.trade_ctx:
                engine.connect()
            result = engine.emergency_liquidate_all()
            st.session_state.confirm_liquidation = False
            if result.get("status") in {"OK", "PARTIAL"}:
                st.error(f"緊急平倉已執行：{result}")
            else:
                st.warning(f"緊急平倉未執行：{result}")
    with cancel_col:
        if st.button("取消"):
            st.session_state.confirm_liquidation = False

# 自動風控檢查
try:
    state = ss.load()
    emergency_status = st.session_state.engine.check_emergency_triggers(state=state)
    if emergency_status.get("alerts"):
        for alert in emergency_status["alerts"]:
            st.warning(alert)
    if emergency_status.get("triggered"):
        st.error("已觸發自動緊急平倉，並啟用熔斷停止交易。")
except Exception as e:
    st.info(f"自動風控檢查暫不可用: {e}")

# 自動重新整理
if st.button("手動刷新"):
    st.rerun()

# 腳註
st.caption(f"最後更新時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

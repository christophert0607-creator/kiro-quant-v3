import streamlit as st
import pandas as pd
import json
import os
import time
from datetime import datetime

# 設定頁面資訊
st.set_page_config(page_title="Futu Quant Dashboard", layout="wide")

# 路徑定義
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MARKET_DATA_PATH = os.path.join(BASE_DIR, "market_data.csv")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
# 假設 sentiment 資料可能也存儲在某處，若無則模擬展示
SENTIMENT_DATA_PATH = os.path.join(BASE_DIR, "sentiment_history.csv") 

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

# 自動重新整理
if st.button("手動刷新"):
    st.rerun()

# 腳註
st.caption(f"最後更新時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

import asyncio
import yfinance as yf
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

HK_TZ = ZoneInfo("Asia/Hong_Kong")

class MarketAnalyzer:
    """宏觀市場分析器（用於 Idle 時段）"""
    
    def __init__(self, loop):
        self.loop = loop
        self.logger = loop.logger
    
    async def analyze_market_breadth(self) -> dict:
        """分析市場廣度：漲跌家數比、龍頭股動力"""
        tickers = ["^HSI", "3033.HK", "^GSPC", "^IXIC"]
        results = {}
        try:
            data = await asyncio.to_thread(yf.download, tickers, period="2d", interval="1d", progress=False)
            if not data.empty and "Close" in data:
                for t in tickers:
                    if t in data["Close"]:
                        series = data["Close"][t].dropna()
                        if len(series) >= 2:
                            change = (series.iloc[-1] - series.iloc[-2]) / series.iloc[-2]
                            results[t] = change
        except Exception as e:
            self.logger.warning(f"[MarketAnalyzer] Breadth analysis failed: {e}")
        return results
    
    async def analyze_sector_rotation(self) -> dict:
        """分析板塊輪動 (XLK, XLV, XLF, XLE, XLI, XLY, XLP, XLB, XLU, XLRE)"""
        etfs = ["XLK", "XLV", "XLF", "XLE", "XLI", "XLY", "XLP", "XLB", "XLU", "XLRE"]
        results = {}
        try:
            data = await asyncio.to_thread(yf.download, etfs, period="5d", interval="1d", progress=False)
            if not data.empty and "Close" in data:
                for etf in etfs:
                    if etf in data["Close"]:
                        series = data["Close"][etf].dropna()
                        if len(series) >= 2:
                            change = (series.iloc[-1] - series.iloc[-2]) / series.iloc[-2]
                            results[etf] = change
        except Exception as e:
            self.logger.warning(f"[MarketAnalyzer] Sector analysis failed: {e}")
        return results
    
    async def analyze_sentiment(self) -> dict:
        """分析情緒指標：VIX、期貨領先"""
        tickers = ["^VIX", "ES=F", "NQ=F"]
        results = {}
        try:
            data = await asyncio.to_thread(yf.download, tickers, period="2d", interval="1d", progress=False)
            if not data.empty and "Close" in data:
                for t in tickers:
                    if t in data["Close"]:
                        series = data["Close"][t].dropna()
                        if len(series) >= 2:
                            change = (series.iloc[-1] - series.iloc[-2]) / series.iloc[-2]
                            results[t] = {"price": series.iloc[-1], "change": change}
        except Exception as e:
            self.logger.warning(f"[MarketAnalyzer] Sentiment analysis failed: {e}")
        return results

    async def analyze_mega_caps(self) -> dict:
        """分析龍頭股動力"""
        megas = ["NVDA", "AAPL", "MSFT", "GOOG", "META", "TSLA", "AMZN"]
        results = {}
        try:
            data = await asyncio.to_thread(yf.download, megas, period="2d", interval="1d", progress=False)
            if not data.empty and "Close" in data:
                for m in megas:
                    if m in data["Close"]:
                        series = data["Close"][m].dropna()
                        if len(series) >= 2:
                            change = (series.iloc[-1] - series.iloc[-2]) / series.iloc[-2]
                            results[m] = change
        except Exception as e:
            self.logger.warning(f"[MarketAnalyzer] Mega caps analysis failed: {e}")
        return results
    
    async def generate_market_report(self) -> tuple[str, dict]:
        """生成大市報告（繁體中文）"""
        breadth, sector, sentiment, megas = await asyncio.gather(
            self.analyze_market_breadth(),
            self.analyze_sector_rotation(),
            self.analyze_sentiment(),
            self.analyze_mega_caps()
        )
        
        analysis = {
            "breadth": breadth,
            "sector": sector,
            "sentiment": sentiment,
            "megas": megas,
            "timestamp": datetime.now(HK_TZ).strftime("%Y-%m-%d %H:%M:%S")
        }
        
        risk_mode, risk_mult = self.compute_risk_factor(analysis)
        
        lines = []
        lines.append(f"📊 **Kiro Quant V3 宏觀市場報告** ({analysis['timestamp']})")
        lines.append("")
        
        lines.append("📈 **指數表現**")
        for k, v in breadth.items():
            name = "HSI" if k == "^HSI" else "HSTECH" if k == "3033.HK" else "S&P500" if k == "^GSPC" else "NASDAQ" if k == "^IXIC" else k
            emoji = "🟢" if v >= 0 else "🔴"
            lines.append(f"- {name}: {emoji} {v:+.2%}")
        
        lines.append("")
        lines.append("🎢 **情緒指標**")
        vix = sentiment.get("^VIX", {})
        if vix:
            vix_val = float(vix.get("price", 0))
            vix_emoji = "😨" if vix_val > 25 else "🟢" if vix_val < 18 else "😐"
            lines.append(f"- VIX: {vix_val:.2f} {vix_emoji}")
        
        es = sentiment.get("ES=F", {})
        if es:
            lines.append(f"- S&P500 Futures: {float(es.get('change', 0)):+.2%}")
            
        lines.append("")
        lines.append("🚀 **龍頭動力**")
        top_megas = sorted(megas.items(), key=lambda x: x[1], reverse=True)
        for m, chg in top_megas[:3]:
            lines.append(f"- {m}: {chg:+.2%}")
            
        lines.append("")
        lines.append(f"🛡️ **風險建議: {risk_mode.upper()}** (倍數: {risk_mult:.2f})")
        
        return "\n".join(lines), analysis
    
    def compute_risk_factor(self, analysis: dict) -> tuple[str, float]:
        """計算風險系數：return (risk_mode, multiplier)"""
        sentiment = analysis.get("sentiment", {})
        vix = float(sentiment.get("^VIX", {}).get("price", 20))
        
        if vix > 25:
            return "risk_off", 0.7
        elif vix < 18:
            es_chg = float(sentiment.get("ES=F", {}).get("change", 0))
            if es_chg > 0:
                return "risk_on", 1.3
            return "risk_on", 1.1
        
        return "neutral", 1.0

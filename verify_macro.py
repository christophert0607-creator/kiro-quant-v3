import asyncio
import sys
import logging
from unittest.mock import MagicMock

# Add project path to sys.path
sys.path.append('/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3')

async def test_analyzer():
    print("Testing MarketAnalyzer...")
    try:
        from v3_pipeline.core.market_analyzer import MarketAnalyzer
        loop = MagicMock()
        loop.logger = logging.getLogger("Test")
        analyzer = MarketAnalyzer(loop)
        
        print("Running analyze_market_breadth...")
        breadth = await analyzer.analyze_market_breadth()
        print(f"Breadth: {breadth}")
        
        print("Running analyze_sentiment...")
        sentiment = await analyzer.analyze_sentiment()
        print(f"Sentiment: {sentiment}")
        
        print("Generating report...")
        report, analysis = await analyzer.generate_market_report()
        print("\n--- REPORT ---")
        print(report)
        print("--------------\n")
        
        risk_mode, risk_mult = analyzer.compute_risk_factor(analysis)
        print(f"Risk Mode: {risk_mode}, Risk Multiplier: {risk_mult}")
        
    except Exception as e:
        print(f"Test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_analyzer())

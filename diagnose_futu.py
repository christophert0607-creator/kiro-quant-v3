import futu as ft
import json
import logging
import sys
import os

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger("FutuDiagnostic")

def run_diagnostics():
    host = "127.0.0.1"
    port = 11111
    
    results = {
        "connection": False,
        "markets": {},
        "api_version": ft.__version__ if hasattr(ft, '__version__') else "unknown",
        "errors": []
    }
    
    try:
        quote_ctx = ft.OpenQuoteContext(host=host, port=port)
        results["connection"] = True
        logger.info("✅ Connected to FutuOpenD")
        
        # Check Market States
        for mkt_name, mkt_type in [("HK", ft.Market.HK), ("US", ft.Market.US), ("SH", ft.Market.SH), ("SZ", ft.Market.SZ)]:
            ret, data = quote_ctx.get_market_state([mkt_type])
            if ret == ft.RET_OK:
                results["markets"][mkt_name] = data['market_state'].iloc[0]
            else:
                results["markets"][mkt_name] = f"Error: {data}"
                
        quote_ctx.close()
    except Exception as e:
        results["errors"].append(str(e))
        logger.error(f"❌ Diagnostic Failed: {e}")

    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    run_diagnostics()

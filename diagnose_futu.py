"""Futu OpenD diagnostic tool — connection state, FD health, market status."""
from __future__ import annotations

import json
import logging
import resource
import socket
import sys
import time
from collections import Counter
from pathlib import Path

import futu as ft

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("FutuDiagnostic")


# ── helpers ───────────────────────────────────────────────────────────────────

def _tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _fd_stats() -> dict:
    """Return open FD count, soft limit, and usage ratio (Linux only)."""
    try:
        fd_dir = Path("/proc/self/fd")
        open_fds = len(list(fd_dir.iterdir()))
    except Exception:
        open_fds = -1

    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except Exception:
        soft, hard = -1, -1

    usage_ratio = (open_fds / soft) if soft > 0 else -1.0
    return {
        "open_fds": open_fds,
        "soft_limit": soft,
        "hard_limit": hard,
        "usage_ratio": round(usage_ratio, 4),
    }


def _top_fd_targets(top_n: int = 5) -> list:
    """Return the most frequent resolved file paths held open by this process."""
    fd_dir = Path("/proc/self/fd")
    paths: list = []
    try:
        for entry in fd_dir.iterdir():
            try:
                paths.append(str(entry.resolve()))
            except OSError:
                pass
    except Exception:
        return []
    counts = Counter(paths)
    return [p for p, _ in counts.most_common(top_n)]


def _load_futu_config(config_path: str = "config.json") -> dict:
    try:
        raw = json.loads(Path(config_path).read_text())
        return raw.get("futu", raw.get("futu_api_config", {}))
    except Exception:
        return {}


# ── main diagnostic ───────────────────────────────────────────────────────────

def run_diagnostics(config_path: str = "config.json") -> dict:
    futu_cfg = _load_futu_config(config_path)
    host = futu_cfg.get("host", "127.0.0.1")
    port = int(futu_cfg.get("port", 11112))

    results: dict = {
        "host": host,
        "port": port,
        "api_version": getattr(ft, "__version__", "unknown"),
        "tcp_reachable": False,
        "connection": False,
        "conn_state": None,
        "heartbeat_ok": None,
        "markets": {},
        "fd_health": _fd_stats(),
        "top_fd_targets": _top_fd_targets(),
        "errors": [],
    }

    # TCP probe before attempting full SDK connection
    results["tcp_reachable"] = _tcp_reachable(host, port)
    if not results["tcp_reachable"]:
        results["errors"].append(
            f"TCP connection to {host}:{port} refused — OpenD may not be running"
        )
        logger.error("❌ OpenD not reachable at %s:%d", host, port)
        print(json.dumps(results, indent=2))
        return results

    logger.info("✅ TCP reachable: %s:%d", host, port)

    # Full SDK quote context
    try:
        t0 = time.monotonic()
        quote_ctx = ft.OpenQuoteContext(host=host, port=port)
        results["connection"] = True
        results["connect_latency_ms"] = round((time.monotonic() - t0) * 1000, 1)
        logger.info("✅ SDK connected (%.1f ms)", results["connect_latency_ms"])

        # Heartbeat probe via get_global_state
        try:
            t1 = time.monotonic()
            ret, state_data = quote_ctx.get_global_state()
            results["heartbeat_ok"] = ret == ft.RET_OK
            results["heartbeat_latency_ms"] = round((time.monotonic() - t1) * 1000, 1)
            if ret == ft.RET_OK and not state_data.empty:
                results["conn_state"] = state_data.iloc[0].to_dict()
        except Exception as hb_err:
            results["heartbeat_ok"] = False
            results["errors"].append(f"heartbeat: {hb_err}")

        # Market states
        for mkt_name, mkt_type in [
            ("HK", ft.Market.HK),
            ("US", ft.Market.US),
            ("SH", ft.Market.SH),
            ("SZ", ft.Market.SZ),
        ]:
            try:
                ret, data = quote_ctx.get_market_state([mkt_type])
                results["markets"][mkt_name] = (
                    data["market_state"].iloc[0] if ret == ft.RET_OK else f"Error: {data}"
                )
            except Exception as mkt_err:
                results["markets"][mkt_name] = f"Exception: {mkt_err}"

        quote_ctx.close()
    except Exception as exc:
        results["errors"].append(str(exc))
        logger.error("❌ SDK connection failed: %s", exc)

    # FD health after SDK usage
    results["fd_health_post"] = _fd_stats()
    fd_delta = results["fd_health_post"]["open_fds"] - results["fd_health"]["open_fds"]
    results["fd_delta"] = fd_delta
    if fd_delta > 10:
        results["errors"].append(
            f"FD leak detected: +{fd_delta} descriptors after SDK usage"
        )
        logger.warning("⚠️  FD delta after SDK usage: +%d", fd_delta)

    print(json.dumps(results, indent=2, default=str))
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Futu OpenD diagnostic tool")
    parser.add_argument("--config", default="config.json", help="Path to config.json")
    args = parser.parse_args()
    run_diagnostics(config_path=args.config)

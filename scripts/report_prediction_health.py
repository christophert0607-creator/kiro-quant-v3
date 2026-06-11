#!/usr/bin/env python3
"""Report prediction/gate health split by HK/US from structured decision logs."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "logs" / "decisions.jsonl"


def _parse_ts(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _market(symbol: str) -> str:
    return "HK" if str(symbol).upper().endswith(".HK") else "US"


# ── HK model V2 vs LSTM comparison (2026-06-11 plan, Task 3.6) ────────────────

PROB_BUCKETS = ((0.0, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 1.01))


def _future_price(
    prices: list[tuple[datetime, float]],
    ts: datetime,
    horizon_min: float = 30.0,
    tolerance_min: float = 15.0,
) -> float | None:
    """First observed price in [ts+horizon, ts+horizon+tolerance], else None."""
    lo = ts + timedelta(minutes=horizon_min)
    hi = lo + timedelta(minutes=tolerance_min)
    for pts, price in prices:
        if pts < lo:
            continue
        if pts > hi:
            return None
        return price
    return None


def _eval_predictions(
    rows: list[dict],
    prices_by_symbol: dict[str, list[tuple[datetime, float]]],
) -> dict:
    """Directional accuracy + MAE% of price-level predictions at +30min."""
    correct = total = 0
    abs_err = []
    for row in rows:
        prices = prices_by_symbol.get(row["symbol"], [])
        future = _future_price(prices, row["ts"])
        if future is None or not row.get("price"):
            continue
        realized = future / row["price"] - 1.0
        predicted = row["pred"] / row["price"] - 1.0
        if realized == 0.0 or predicted == 0.0:
            continue
        total += 1
        if (realized > 0) == (predicted > 0):
            correct += 1
        abs_err.append(abs(row["pred"] - future) / future)
    return {
        "evaluated": total,
        "directional_accuracy": round(correct / total, 4) if total else None,
        "mae_pct": round(sum(abs_err) / len(abs_err), 5) if abs_err else None,
    }


def build_model_comparison(
    v2_events: list[dict],
    lstm_events: list[dict],
    which: str,
) -> dict:
    """Compare V2 vs LSTM on HK symbols using realized +30min returns."""
    prices_by_symbol: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    for ev in lstm_events:
        if ev.get("price"):
            prices_by_symbol[ev["symbol"]].append((ev["ts"], ev["price"]))
    for series in prices_by_symbol.values():
        series.sort(key=lambda t: t[0])

    out: dict = {"horizon_minutes": 30}

    if which in ("v2", "both"):
        rows = [dict(ev, pred=ev["pred"]) for ev in v2_events]
        v2_stats = _eval_predictions(rows, prices_by_symbol)
        v2_stats["events"] = len(v2_events)
        v2_stats["avg_prob_up"] = (
            round(sum(e["prob_up"] for e in v2_events) / len(v2_events), 4) if v2_events else None
        )
        # Calibration: realized win rate per predicted-probability bucket.
        buckets = {}
        for lo, hi in PROB_BUCKETS:
            wins = total = 0
            for ev in v2_events:
                if not (lo <= ev["prob_up"] < hi) or not ev.get("price"):
                    continue
                future = _future_price(prices_by_symbol.get(ev["symbol"], []), ev["ts"])
                if future is None:
                    continue
                total += 1
                if future > ev["price"]:
                    wins += 1
            buckets[f"{lo:.1f}-{min(hi, 1.0):.1f}"] = {
                "n": total,
                "realized_win_rate": round(wins / total, 4) if total else None,
            }
        v2_stats["calibration_buckets"] = buckets
        # Direction agreement with the champion LSTM at event time.
        agree = total = 0
        for ev in v2_events:
            if not ev.get("price") or ev.get("lstm_pred") is None:
                continue
            v2_dir = ev["pred"] - ev["price"]
            lstm_dir = ev["lstm_pred"] - ev["price"]
            if v2_dir == 0 or lstm_dir == 0:
                continue
            total += 1
            if (v2_dir > 0) == (lstm_dir > 0):
                agree += 1
        v2_stats["lstm_direction_agreement"] = round(agree / total, 4) if total else None
        out["v2"] = v2_stats

    if which in ("lstm", "both"):
        hk_lstm = [ev for ev in lstm_events if _market(ev["symbol"]) == "HK" and ev.get("pred")]
        lstm_stats = _eval_predictions(hk_lstm, prices_by_symbol)
        lstm_stats["events"] = len(hk_lstm)
        out["lstm"] = lstm_stats

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=1.0)
    ap.add_argument("--log", default=str(LOG_PATH))
    ap.add_argument(
        "--model",
        choices=("v2", "lstm", "both"),
        default=None,
        help="add HK model V2 vs LSTM comparison section",
    )
    args = ap.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    stats = defaultdict(lambda: {
        "predictions": 0,
        "quality": Counter(),
        "meta": Counter(),
        "orders_attempted": 0,
        "orders_result": Counter(),
        "outcome_probs": [],
    })
    v2_events: list[dict] = []
    lstm_events: list[dict] = []
    path = Path(args.log)
    if path.exists():
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                ts = _parse_ts(ev.get("ts") or ev.get("timestamp") or ev.get("created_at"))
                if ts is not None and ts < cutoff:
                    continue
                symbol = str(ev.get("symbol") or ev.get("data", {}).get("symbol") or "")
                market = _market(symbol)
                name = str(ev.get("event") or ev.get("type") or "")
                data = ev.get("data") if isinstance(ev.get("data"), dict) else ev
                bucket = stats[market]
                if name in {"model_predict", "prediction"}:
                    bucket["predictions"] += 1
                    if ts is not None:
                        try:
                            lstm_events.append({
                                "ts": ts,
                                "symbol": symbol,
                                "pred": float(data.get("pred") or 0.0),
                                "price": float(data.get("price") or 0.0),
                            })
                        except Exception:
                            pass
                elif name == "hk_model_v2":
                    if ts is not None:
                        try:
                            v2_events.append({
                                "ts": ts,
                                "symbol": symbol,
                                "pred": float(data.get("pred") or 0.0),
                                "price": float(data.get("price") or 0.0),
                                "prob_up": float(data.get("prob_up") or 0.0),
                                "lstm_pred": float(data.get("lstm_pred") or 0.0) or None,
                            })
                        except Exception:
                            pass
                elif name in {"trade_quality_gate", "trade_quality"}:
                    bucket["quality"][str(data.get("decision", "UNKNOWN"))] += 1
                elif name in {"meta_label_gate", "meta_label"}:
                    bucket["meta"][str(data.get("decision", "UNKNOWN"))] += 1
                elif name == "order_attempt":
                    bucket["orders_attempted"] += 1
                elif name == "order_result":
                    bucket["orders_result"][str(data.get("status", "UNKNOWN"))] += 1
                elif name == "outcome_head":
                    try:
                        bucket["outcome_probs"].append(float(data.get("prob_profit")))
                    except Exception:
                        pass

    out = {"days": args.days, "log": str(path), "markets": {}}
    for market, bucket in stats.items():
        probs = bucket["outcome_probs"]
        out["markets"][market] = {
            "predictions": bucket["predictions"],
            "quality_decisions": dict(bucket["quality"]),
            "meta_decisions": dict(bucket["meta"]),
            "outcome_head_avg_probability": round(sum(probs) / len(probs), 4) if probs else None,
            "orders_attempted": bucket["orders_attempted"],
            "order_results": dict(bucket["orders_result"]),
        }
    if args.model:
        out["model_comparison"] = build_model_comparison(v2_events, lstm_events, args.model)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

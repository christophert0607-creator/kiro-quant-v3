#!/usr/bin/env python3
"""
Meta-Labeling Quality Reporter — meta_012辅助脚本
==================================================
诊断 self_learn DB 中 prediction 数据的质量分布，
为 meta-labeling 提供尚未有 outcomes 时的预测信心指标。

不涉及 live trading，只读 DB，输出诊断 JSON。

Usage:
    cd kiro-quant-v3
    PYTHONPATH=. python3 self_learn/scripts/meta_quality_report.py
"""

from __future__ import annotations
import sys
import json
from datetime import datetime, timezone
from collections import defaultdict

sys.path.insert(0, "/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3")

from self_learn.models import get_session, get_stats
from self_learn.models import Prediction, Signal, Outcome


def get_prediction_quality_distribution() -> dict:
    """按 symbol 分析 prediction confidence 分布，评估可用于 meta-labeling 的数据质量。"""
    session = get_session()
    try:
        # 按 symbol 统计 predictions 数量、confidence 分布、有无 linked signals
        rows = session.query(
            Prediction.symbol,
            Prediction.confidence,
            Prediction.predicted_price,
            Prediction.created_at,
            Signal.id.label("signal_id"),
            Signal.status.label("signal_status"),
        ).outerjoin(
            Signal, Prediction.id == Signal.prediction_id
        ).all()

        # 按 symbol 聚合
        symbol_data: dict[str, dict] = defaultdict(lambda: {
            "total_predictions": 0,
            "with_confidence": 0,
            "avg_confidence": 0.0,
            "has_linked_signal": 0,
            "signals_open": 0,
            "signals_closed": 0,
            "confidences": [],
        })

        for row in rows:
            sym = row.symbol
            sd = symbol_data[sym]
            sd["total_predictions"] += 1
            if row.confidence is not None:
                sd["with_confidence"] += 1
                sd["confidences"].append(row.confidence)
            if row.signal_id is not None:
                sd["has_linked_signal"] += 1
                if row.signal_status == "OPEN":
                    sd["signals_open"] += 1
                elif row.signal_status == "CLOSED":
                    sd["signals_closed"] += 1

        # 计算 per-symbol avg confidence
        result = {}
        for sym, sd in sorted(symbol_data.items()):
            confs = sd["confidences"]
            sd["avg_confidence"] = round(sum(confs) / len(confs), 4) if confs else 0.0
            sd["confidence_buckets"] = {
                "high (>0.8)": sum(1 for c in confs if c > 0.8),
                "medium (0.5-0.8)": sum(1 for c in confs if 0.5 <= c <= 0.8),
                "low (<0.5)": sum(1 for c in confs if 0 < c < 0.5),
                "none": sd["total_predictions"] - len(confs),
            }
            del sd["confidences"]  # reduce noise in output
            result[sym] = sd

        return result
    finally:
        session.close()


def get_outcome_quality_check() -> dict:
    """检查 outcomes 表中 prediction_error 字段的填充率。"""
    session = get_session()
    try:
        total_outcomes = session.query(Outcome).count()
        with_prediction_error = session.query(Outcome).filter(
            Outcome.prediction_error.isnot(None)
        ).count()

        # 按 symbol 检查有 outcome 的 symbol 数量
        outcome_symbols = session.query(Outcome.signal_id).count()

        return {
            "total_outcomes": total_outcomes,
            "with_prediction_error": with_prediction_error,
            "prediction_error_fill_rate": (
                round(with_prediction_error / total_outcomes, 4) if total_outcomes > 0 else None
            ),
        }
    finally:
        session.close()


def main() -> dict:
    stats = get_stats()
    pred_dist = get_prediction_quality_distribution()
    outcome_quality = get_outcome_quality_check()

    # 汇总
    total_preds = stats["total_predictions"]
    top_symbols = sorted(pred_dist.items(), key=lambda x: x[1]["total_predictions"], reverse=True)[:10]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_summary": {
            "predictions": total_preds,
            "signals_total": stats["total_signals"],
            "signals_open": stats["open_signals"],
            "signals_closed": stats["closed_signals"],
            "outcomes": stats["total_outcomes"],
        },
        "prediction_quality": {
            "unique_symbols": len(pred_dist),
            "symbols_with_linked_signals": sum(1 for s in pred_dist.values() if s["has_linked_signal"] > 0),
            "top_10_symbols_by_prediction_count": [
                {
                    "symbol": sym,
                    "predictions": data["total_predictions"],
                    "avg_confidence": data["avg_confidence"],
                    "has_linked_signal": data["has_linked_signal"] > 0,
                    "signals_open": data["signals_open"],
                }
                for sym, data in top_symbols
            ],
        },
        "outcome_quality": outcome_quality,
        "meta_labeling_readiness": {
            "blocked_by_no_outcomes": stats["total_outcomes"] < 100,
            "outcomes_target": 100,
            "predictions_available": total_preds > 0,
            "confidence_data_available": any(
                s["with_confidence"] > 0 for s in pred_dist.values()
            ),
        },
    }

    print(json.dumps(report, indent=2, default=str))

    # CLI exit
    if report["meta_labeling_readiness"]["blocked_by_no_outcomes"]:
        print("\n⚠️  meta-labeling 仍被 outcomes 数量阻止（需要 ≥100 closed trades）")
        print(f"   当前: predictions={total_preds}, outcomes={stats['total_outcomes']}")
        print("   提示: meta_labeler 可处理 NO_DATA 情况，安全进行回测验证")
    else:
        print("\n✅ outcomes 充足，meta-model 训练就绪")

    return report


if __name__ == "__main__":
    main()
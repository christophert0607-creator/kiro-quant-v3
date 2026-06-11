import json

import v3_launcher


def test_base_live_config_supports_mode_strings(tmp_path):
    cfg = {
        "v3_live": {
            "symbols_list": ["AAPL"],
            "trade_quality_enabled": True,
            "trade_quality_mode": "enforce",
            "meta_label_enabled": True,
            "meta_label_mode": "enforce",
        }
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")

    live = v3_launcher._base_live_config(str(path))

    assert live.trade_quality_mode == "enforce"
    assert live.trade_quality_shadow_mode is False
    assert live.meta_label_shadow_mode is False


def test_hk_live_overlay_overrides_prediction_gate_config(tmp_path, monkeypatch):
    cfg = {
        "v3_live": {
            "symbols_list": ["AAPL"],
            "trade_quality_min_score": 0.65,
            "trade_quality_mode": "shadow",
            "meta_label_mode": "shadow",
        },
        "hk_live": {
            "symbols_list": ["0700.HK"],
            "trade_quality_min_score": 0.68,
            "trade_quality_mode": "enforce",
            "meta_label_mode": "enforce",
        },
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(v3_launcher, "resolve_market_mode", lambda now=None: "HK")

    live = v3_launcher.build_live_config(str(path))

    assert live.symbols_list == ["0700.HK"]
    assert live.trade_quality_min_score == 0.68
    assert live.trade_quality_mode == "enforce"
    assert live.trade_quality_shadow_mode is False
    assert live.meta_label_shadow_mode is False

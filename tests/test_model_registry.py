"""Tests for v3_pipeline.models.registry (Phase 5 - checkpoint metadata validation)."""
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import torch

from v3_pipeline.models.registry import (
    CheckpointIncompatibleError,
    CheckpointMetadata,
    ModelRegistry,
    ValidationResult,
    load_metadata,
    meta_path_for,
    save_metadata,
    validate_checkpoint,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_dummy_pth(path: Path) -> Path:
    """Write a zero-byte fake .pth so existence checks pass."""
    path.write_bytes(b"")
    return path


def _write_meta(pth_path: Path, data: dict) -> Path:
    sidecar = meta_path_for(pth_path)
    sidecar.write_text(json.dumps(data), encoding="utf-8")
    return sidecar


VALID_META = {
    "schema_version": "1",
    "model_type": "LSTM",
    "input_dim": 26,
    "market": "US",
    "training_window": "2022-01-01/2024-12-31",
    "created_ts": "2025-01-15T08:00:00Z",
    "feature_set_id": "v3_base",
}


# ── meta_path_for ─────────────────────────────────────────────────────────────

def test_meta_path_for():
    p = Path("/models/v3_us_stocks.pth")
    assert meta_path_for(p) == Path("/models/v3_us_stocks.meta.json")


# ── load_metadata ─────────────────────────────────────────────────────────────

def test_load_metadata_returns_none_when_sidecar_missing(tmp_path):
    pth = _write_dummy_pth(tmp_path / "model.pth")
    assert load_metadata(pth) is None


def test_load_metadata_returns_parsed_metadata(tmp_path):
    pth = _write_dummy_pth(tmp_path / "model.pth")
    _write_meta(pth, VALID_META)
    meta = load_metadata(pth)
    assert meta is not None
    assert meta.model_type == "LSTM"
    assert meta.input_dim == 26
    assert meta.market == "US"


def test_load_metadata_returns_none_on_invalid_json(tmp_path):
    pth = _write_dummy_pth(tmp_path / "model.pth")
    meta_path_for(pth).write_text("NOT JSON", encoding="utf-8")
    assert load_metadata(pth) is None


# ── save_metadata ─────────────────────────────────────────────────────────────

def test_save_metadata_round_trip(tmp_path):
    pth = _write_dummy_pth(tmp_path / "model.pth")
    meta = CheckpointMetadata(
        schema_version="1",
        model_type="GBM",
        input_dim=32,
        market="HK",
        training_window="2023-01-01/2024-12-31",
        created_ts="2025-06-01T00:00:00Z",
        feature_set_id="hk_v1",
    )
    save_metadata(pth, meta)
    loaded = load_metadata(pth)
    assert loaded is not None
    assert loaded.model_type == "GBM"
    assert loaded.input_dim == 32
    assert loaded.market == "HK"
    assert loaded.feature_set_id == "hk_v1"


def test_save_metadata_omits_none_fields(tmp_path):
    pth = _write_dummy_pth(tmp_path / "model.pth")
    meta = CheckpointMetadata(schema_version="1", model_type="LSTM", input_dim=26, market="US")
    save_metadata(pth, meta)
    raw = json.loads(meta_path_for(pth).read_text(encoding="utf-8"))
    assert "training_window" not in raw
    assert "feature_set_id" not in raw


# ── validate_checkpoint — valid ────────────────────────────────────────────────

def test_validate_valid_checkpoint(tmp_path):
    pth = _write_dummy_pth(tmp_path / "model.pth")
    _write_meta(pth, VALID_META)
    result = validate_checkpoint(pth)
    assert result.ok is True
    assert result.errors == []
    assert result.metadata is not None
    assert result.metadata.input_dim == 26


def test_validate_with_matching_input_dim(tmp_path):
    pth = _write_dummy_pth(tmp_path / "model.pth")
    _write_meta(pth, VALID_META)
    result = validate_checkpoint(pth, expected_input_dim=26)
    assert result.ok is True


def test_validate_with_matching_market(tmp_path):
    pth = _write_dummy_pth(tmp_path / "model.pth")
    _write_meta(pth, VALID_META)
    result = validate_checkpoint(pth, expected_market="US")
    assert result.ok is True


def test_validate_market_case_insensitive(tmp_path):
    pth = _write_dummy_pth(tmp_path / "model.pth")
    _write_meta(pth, {**VALID_META, "market": "hk"})
    result = validate_checkpoint(pth, expected_market="HK")
    assert result.ok is True


# ── validate_checkpoint — failures ────────────────────────────────────────────

def test_validate_checkpoint_file_not_found(tmp_path):
    missing = tmp_path / "ghost.pth"
    result = validate_checkpoint(missing)
    assert result.ok is False
    assert any("not found" in e for e in result.errors)


def test_validate_missing_sidecar_is_invalid(tmp_path):
    pth = _write_dummy_pth(tmp_path / "model.pth")
    result = validate_checkpoint(pth)
    assert result.ok is False
    assert any("Missing metadata sidecar" in e for e in result.errors)


def test_validate_input_dim_mismatch(tmp_path):
    pth = _write_dummy_pth(tmp_path / "model.pth")
    _write_meta(pth, VALID_META)
    result = validate_checkpoint(pth, expected_input_dim=32)
    assert result.ok is False
    assert any("input_dim mismatch" in e for e in result.errors)


def test_validate_market_mismatch(tmp_path):
    pth = _write_dummy_pth(tmp_path / "model.pth")
    _write_meta(pth, VALID_META)
    result = validate_checkpoint(pth, expected_market="HK")
    assert result.ok is False
    assert any("market mismatch" in e for e in result.errors)


def test_validate_strict_raises_on_missing_sidecar(tmp_path):
    pth = _write_dummy_pth(tmp_path / "model.pth")
    with pytest.raises(CheckpointIncompatibleError, match="Missing metadata sidecar"):
        validate_checkpoint(pth, strict=True)


def test_validate_strict_raises_on_dim_mismatch(tmp_path):
    pth = _write_dummy_pth(tmp_path / "model.pth")
    _write_meta(pth, VALID_META)
    with pytest.raises(CheckpointIncompatibleError, match="input_dim mismatch"):
        validate_checkpoint(pth, expected_input_dim=99, strict=True)


def test_validate_strict_raises_on_market_mismatch(tmp_path):
    pth = _write_dummy_pth(tmp_path / "model.pth")
    _write_meta(pth, VALID_META)
    with pytest.raises(CheckpointIncompatibleError, match="market mismatch"):
        validate_checkpoint(pth, expected_market="HK", strict=True)


def test_validate_does_not_raise_by_default(tmp_path):
    pth = _write_dummy_pth(tmp_path / "model.pth")
    result = validate_checkpoint(pth)  # missing sidecar — should log, not raise
    assert result.ok is False


# ── ModelRegistry.resolve_and_validate ────────────────────────────────────────

class TestModelRegistryValidate:
    def _make_registry(self, tmp_path: Path, aliases: dict = None, default: str = None) -> ModelRegistry:
        return ModelRegistry(
            model_dir=tmp_path,
            aliases=aliases or {},
            default=default,
        )

    def test_resolve_unresolvable_returns_none(self, tmp_path):
        reg = self._make_registry(tmp_path)
        path, result = reg.resolve_and_validate("nonexistent_model")
        assert path is None
        assert result is None

    def test_resolve_and_validate_valid_checkpoint(self, tmp_path):
        pth = _write_dummy_pth(tmp_path / "v3_us_stocks.pth")
        _write_meta(pth, VALID_META)
        reg = self._make_registry(tmp_path)
        path, result = reg.resolve_and_validate("v3_us_stocks")
        assert path == pth
        assert result is not None
        assert result.ok is True

    def test_resolve_alias_and_validate(self, tmp_path):
        pth = _write_dummy_pth(tmp_path / "global_best.pth")
        _write_meta(pth, VALID_META)
        reg = self._make_registry(tmp_path, aliases={"v3_us_stocks": "global_best"})
        path, result = reg.resolve_and_validate("v3_us_stocks")
        assert path == pth
        assert result.ok is True

    def test_resolve_and_validate_missing_sidecar_returns_failed_result(self, tmp_path):
        _write_dummy_pth(tmp_path / "model.pth")
        reg = self._make_registry(tmp_path)
        path, result = reg.resolve_and_validate("model")
        assert path is not None
        assert result is not None
        assert result.ok is False

    def test_resolve_and_validate_strict_raises(self, tmp_path):
        _write_dummy_pth(tmp_path / "model.pth")
        reg = self._make_registry(tmp_path)
        with pytest.raises(CheckpointIncompatibleError):
            reg.resolve_and_validate("model", strict=True)

    def test_resolve_and_validate_dim_mismatch_strict(self, tmp_path):
        pth = _write_dummy_pth(tmp_path / "model.pth")
        _write_meta(pth, VALID_META)
        reg = self._make_registry(tmp_path)
        with pytest.raises(CheckpointIncompatibleError, match="input_dim mismatch"):
            reg.resolve_and_validate("model", expected_input_dim=99, strict=True)

    def test_resolve_and_validate_passes_expected_dims_through(self, tmp_path):
        pth = _write_dummy_pth(tmp_path / "model.pth")
        _write_meta(pth, {**VALID_META, "input_dim": 32})
        reg = self._make_registry(tmp_path)
        _, result = reg.resolve_and_validate("model", expected_input_dim=32)
        assert result.ok is True

    def test_from_file_creates_registry_with_aliases(self, tmp_path):
        reg_json = tmp_path / "models_registry.json"
        reg_json.write_text(
            json.dumps({
                "default": "global_best",
                "aliases": {"v3_us_stocks": "global_best"},
            }),
            encoding="utf-8",
        )
        _write_dummy_pth(tmp_path / "global_best.pth")
        _write_meta(tmp_path / "global_best.pth", VALID_META)
        reg = ModelRegistry.from_file(tmp_path, registry_path=reg_json)
        path, result = reg.resolve_and_validate("v3_us_stocks")
        assert path is not None
        assert result.ok is True


# ── ModelManager.load() validate_checkpoint integration ───────────────────────

@pytest.fixture(scope="module", autouse=False)
def real_manager_module():
    """Ensure v3_pipeline.models.manager is the real module, not a test stub.

    test_main_loop_trade_bridge.py uses sys.modules.setdefault() at import time
    to inject a SimpleNamespace stub.  When that test file is collected before
    this one, sys.modules['v3_pipeline.models.manager'] contains the stub.  This
    fixture removes the stub and imports the real module so our integration tests
    can construct an actual ModelManager.
    """
    import importlib
    import sys

    mod_key = "v3_pipeline.models.manager"
    existing = sys.modules.get(mod_key)
    is_stub = existing is not None and not hasattr(existing, "__file__")

    if is_stub:
        del sys.modules[mod_key]

    real_mod = importlib.import_module(mod_key)
    sys.modules[mod_key] = real_mod
    yield real_mod

    # Restore stub so other test modules that depend on the stub still work.
    if is_stub:
        sys.modules[mod_key] = existing


class TestModelManagerValidateCheckpointIntegration:
    """Verify that ModelManager.load() calls validate_checkpoint() before torch.load()."""

    def _make_manager(self, tmp_path: Path, real_manager_module) -> "ModelManager":
        ModelManager = real_manager_module.ModelManager
        DataPreparer = real_manager_module.DataPreparer
        from v3_pipeline.models.brain import KiroLSTM

        preparer = DataPreparer(lookback=10)
        model = KiroLSTM(input_dim=5, hidden_dim=16, num_layers=1)
        reg = ModelRegistry(model_dir=tmp_path, aliases={}, default=None)
        return ModelManager(
            model=model,
            data_preparer=preparer,
            model_dir=str(tmp_path),
            registry=reg,
        )

    def _fake_payload(self, model):
        """Build a minimal state_dict payload that load_state_dict accepts."""
        return model.state_dict()

    def test_load_logs_warning_when_sidecar_missing(self, tmp_path, real_manager_module):
        """manager.load() warns when checkpoint has no .meta.json sidecar."""
        manager = self._make_manager(tmp_path, real_manager_module)
        pth = tmp_path / "v3_test.pth"
        torch.save(self._fake_payload(manager.model), pth)

        with patch.object(manager.logger, "warning") as mock_warn:
            manager.load("v3_test")

        messages = [str(call) for call in mock_warn.call_args_list]
        assert any("Missing metadata sidecar" in m for m in messages), (
            f"Expected 'Missing metadata sidecar' warning. Got: {messages}"
        )

    def test_load_does_not_raise_when_sidecar_missing(self, tmp_path, real_manager_module):
        """manager.load() completes successfully even without a sidecar (warn-only)."""
        manager = self._make_manager(tmp_path, real_manager_module)
        pth = tmp_path / "v3_test.pth"
        torch.save(self._fake_payload(manager.model), pth)
        manager.load("v3_test")  # must not raise

    def test_load_logs_ok_when_valid_sidecar_present(self, tmp_path, real_manager_module):
        """manager.load() logs an INFO checkpoint-OK line when sidecar is valid."""
        manager = self._make_manager(tmp_path, real_manager_module)
        pth = tmp_path / "v3_test.pth"
        torch.save(self._fake_payload(manager.model), pth)
        _write_meta(pth, VALID_META)

        with patch.object(manager.logger, "info") as mock_info:
            manager.load("v3_test")

        messages = [str(call) for call in mock_info.call_args_list]
        assert any("Checkpoint OK" in m for m in messages), (
            f"Expected 'Checkpoint OK' in info logs. Got: {messages}"
        )

    def test_load_warns_on_sidecar_empty_required_field(self, tmp_path, real_manager_module):
        """manager.load() warns when sidecar has an empty-string required field."""
        manager = self._make_manager(tmp_path, real_manager_module)
        pth = tmp_path / "v3_test.pth"
        torch.save(self._fake_payload(manager.model), pth)
        # market="" parses OK but fails the required-field check (falsy)
        _write_meta(pth, {**VALID_META, "market": ""})

        with patch.object(manager.logger, "warning") as mock_warn:
            manager.load("v3_test")

        messages = [str(call) for call in mock_warn.call_args_list]
        assert any("missing required field" in m for m in messages), (
            f"Expected 'missing required field' warning. Got: {messages}"
        )

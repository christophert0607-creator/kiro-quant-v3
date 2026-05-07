"""Tests for v3_pipeline.models.registry (Phase 5 - checkpoint metadata validation)."""
import json
from pathlib import Path

import pytest

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

"""Model registry for resolving logical model names to checkpoint files.

Lets the rest of the system refer to logical names like ``v3_us_stocks`` or
``v3_hk_stocks`` and have them resolve to whatever checkpoint is currently
deployed. Switching models is then a one-line edit in
``models_registry.json`` instead of a code change.

Resolution order (first hit wins):
    1. Alias defined in ``aliases`` map (logical -> stem)
    2. Direct file ``<model_dir>/<name>.pth``
    3. ``default`` stem from registry, if set

The registry is a small JSON file so ops can edit it without touching code.

Phase 5 addition: checkpoint metadata validation.
Each ``.pth`` file may have a sidecar ``.meta.json`` with fields:

    {
        "schema_version": "1",
        "model_type":     "LSTM",
        "input_dim":      26,
        "market":         "US",
        "training_window": "2022-01-01/2024-12-31",
        "created_ts":     "2025-01-15T08:00:00Z",
        "feature_set_id": "v3_base"          # optional
    }

``validate_checkpoint()`` returns a ``ValidationResult`` describing any
schema incompatibilities found.  Callers decide whether to hard-fail or
warn based on their own policy.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_log = logging.getLogger(__name__)

DEFAULT_REGISTRY_PATH = Path("v3_pipeline/models/models_registry.json")

# Fields that MUST be present in a valid metadata sidecar.
_REQUIRED_META_FIELDS: list[str] = [
    "schema_version",
    "model_type",
    "input_dim",
    "market",
]


# ── Metadata schema ───────────────────────────────────────────────────────────

@dataclass
class CheckpointMetadata:
    """Typed representation of a checkpoint's ``.meta.json`` sidecar."""
    schema_version: str
    model_type: str
    input_dim: int
    market: str
    training_window: Optional[str] = None
    created_ts: Optional[str] = None
    feature_set_id: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CheckpointMetadata":
        return cls(
            schema_version=str(data["schema_version"]),
            model_type=str(data["model_type"]),
            input_dim=int(data["input_dim"]),
            market=str(data["market"]).upper(),
            training_window=data.get("training_window"),
            created_ts=data.get("created_ts"),
            feature_set_id=data.get("feature_set_id"),
        )


@dataclass
class ValidationResult:
    """Outcome of metadata validation for a single checkpoint."""
    path: Path
    ok: bool
    errors: List[str] = field(default_factory=list)
    metadata: Optional[CheckpointMetadata] = None

    def log(self, logger: Optional[logging.Logger] = None) -> None:
        lg = logger or _log
        if self.ok:
            lg.info(
                "Checkpoint OK: %s | type=%s input_dim=%d market=%s",
                self.path.name,
                self.metadata.model_type if self.metadata else "?",
                self.metadata.input_dim if self.metadata else -1,
                self.metadata.market if self.metadata else "?",
            )
        else:
            for err in self.errors:
                lg.warning("Checkpoint INVALID [%s]: %s", self.path.name, err)


class CheckpointIncompatibleError(ValueError):
    """Raised when a checkpoint's metadata fails validation in strict mode."""


# ── Metadata I/O ──────────────────────────────────────────────────────────────

def meta_path_for(checkpoint_path: Path) -> Path:
    """Return the expected sidecar path for a checkpoint."""
    return checkpoint_path.with_suffix(".meta.json")


def load_metadata(checkpoint_path: Path) -> Optional[CheckpointMetadata]:
    """Read and parse the sidecar ``.meta.json`` for *checkpoint_path*.

    Returns ``None`` if the sidecar does not exist or cannot be parsed.
    """
    sidecar = meta_path_for(checkpoint_path)
    if not sidecar.exists():
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        return CheckpointMetadata.from_dict(data)
    except Exception as exc:
        _log.warning("Could not parse metadata sidecar %s: %s", sidecar, exc)
        return None


def save_metadata(checkpoint_path: Path, meta: CheckpointMetadata) -> None:
    """Write a sidecar ``.meta.json`` next to *checkpoint_path*."""
    sidecar = meta_path_for(checkpoint_path)
    data: Dict[str, Any] = {
        "schema_version": meta.schema_version,
        "model_type": meta.model_type,
        "input_dim": meta.input_dim,
        "market": meta.market,
    }
    if meta.training_window is not None:
        data["training_window"] = meta.training_window
    if meta.created_ts is not None:
        data["created_ts"] = meta.created_ts
    if meta.feature_set_id is not None:
        data["feature_set_id"] = meta.feature_set_id
    sidecar.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def validate_checkpoint(
    checkpoint_path: Path,
    *,
    expected_input_dim: Optional[int] = None,
    expected_market: Optional[str] = None,
    strict: bool = False,
) -> ValidationResult:
    """Validate the metadata sidecar for *checkpoint_path*.

    Args:
        checkpoint_path: Path to the ``.pth`` model checkpoint.
        expected_input_dim: If set, validation fails when
            ``metadata.input_dim != expected_input_dim``.
        expected_market: If set, validation fails when
            ``metadata.market`` does not match (case-insensitive).
        strict: If ``True``, raise :class:`CheckpointIncompatibleError`
            instead of returning a failed ``ValidationResult``.

    Returns:
        ``ValidationResult`` with ``ok=True`` on success.
    """
    errors: List[str] = []

    if not checkpoint_path.exists():
        errors.append(f"Checkpoint file not found: {checkpoint_path}")
        result = ValidationResult(path=checkpoint_path, ok=False, errors=errors)
        if strict:
            raise CheckpointIncompatibleError("; ".join(errors))
        return result

    meta = load_metadata(checkpoint_path)
    if meta is None:
        errors.append(
            f"Missing metadata sidecar ({meta_path_for(checkpoint_path).name}). "
            "Checkpoint provenance is unknown. Run save_metadata() after training."
        )
        result = ValidationResult(path=checkpoint_path, ok=False, errors=errors)
        if strict:
            raise CheckpointIncompatibleError("; ".join(errors))
        return result

    # Check required fields in metadata dict (already enforced by from_dict,
    # but guard against partially-written sidecars here).
    for req in _REQUIRED_META_FIELDS:
        if not getattr(meta, req, None):
            errors.append(f"Metadata missing required field: {req!r}")

    if expected_input_dim is not None and meta.input_dim != expected_input_dim:
        errors.append(
            f"input_dim mismatch: checkpoint has {meta.input_dim}, "
            f"expected {expected_input_dim}. Rebuild or retrain."
        )

    if expected_market is not None:
        if meta.market.upper() != expected_market.upper():
            errors.append(
                f"market mismatch: checkpoint is for {meta.market!r}, "
                f"expected {expected_market!r}."
            )

    ok = len(errors) == 0
    result = ValidationResult(path=checkpoint_path, ok=ok, errors=errors, metadata=meta if ok else None)
    if not ok and strict:
        raise CheckpointIncompatibleError("; ".join(errors))
    return result


# ── ModelRegistry ─────────────────────────────────────────────────────────────

class ModelRegistry:
    def __init__(
        self,
        model_dir: Path,
        aliases: Optional[Dict[str, str]] = None,
        default: Optional[str] = None,
        registry_path: Optional[Path] = None,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.aliases: Dict[str, str] = dict(aliases or {})
        self.default: Optional[str] = default
        self.registry_path = registry_path
        self.logger = logging.getLogger(self.__class__.__name__)

    @classmethod
    def from_file(
        cls,
        model_dir: Path,
        registry_path: Path = DEFAULT_REGISTRY_PATH,
    ) -> "ModelRegistry":
        if not registry_path.exists():
            return cls(model_dir=model_dir, registry_path=registry_path)
        try:
            data = json.loads(registry_path.read_text(encoding="utf-8"))
        except Exception:
            return cls(model_dir=model_dir, registry_path=registry_path)
        return cls(
            model_dir=model_dir,
            aliases=dict(data.get("aliases") or {}),
            default=data.get("default"),
            registry_path=registry_path,
        )

    def resolve(self, name: str) -> Optional[Path]:
        """Return checkpoint path for ``name`` or ``None`` if unresolved."""
        seen: set[str] = set()
        current = name
        while current in self.aliases and current not in seen:
            seen.add(current)
            current = self.aliases[current]

        candidate = self.model_dir / f"{current}.pth"
        if candidate.exists():
            return candidate

        if self.default and self.default != current:
            fallback = self.model_dir / f"{self.default}.pth"
            if fallback.exists():
                return fallback
        return None

    def candidates(self, name: str) -> List[Path]:
        """All paths that would be tried for ``name``, in order."""
        out: List[Path] = []
        seen: set[str] = set()
        current = name
        while current in self.aliases and current not in seen:
            seen.add(current)
            current = self.aliases[current]
        out.append(self.model_dir / f"{current}.pth")
        if self.default and self.default != current:
            out.append(self.model_dir / f"{self.default}.pth")
        return out

    def resolve_and_validate(
        self,
        name: str,
        *,
        expected_input_dim: Optional[int] = None,
        expected_market: Optional[str] = None,
        strict: bool = False,
    ) -> tuple[Optional[Path], ValidationResult | None]:
        """Resolve *name* and validate its metadata sidecar.

        Returns:
            ``(path, validation_result)`` — path is ``None`` if not resolved;
            validation_result is ``None`` if no checkpoint was found.

        When ``strict=True``, raises :class:`CheckpointIncompatibleError` on
        any validation failure (missing sidecar, field mismatch, etc.).
        """
        path = self.resolve(name)
        if path is None:
            self.logger.warning(
                "ModelRegistry: could not resolve %r — no matching checkpoint in %s",
                name, self.model_dir,
            )
            return None, None

        result = validate_checkpoint(
            path,
            expected_input_dim=expected_input_dim,
            expected_market=expected_market,
            strict=strict,
        )
        result.log(self.logger)
        return path, result

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
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional


DEFAULT_REGISTRY_PATH = Path("v3_pipeline/models/models_registry.json")


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
        # Follow alias chain (alias -> alias -> stem); guard against cycles.
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

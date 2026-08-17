from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "core" / "configs" / "default.yaml"


def resolve_project_path(value: str | Path) -> Path:
    """Resolve relative paths against the project root and expand user paths."""
    path = Path(os.path.expandvars(str(value))).expanduser()
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def ensure_dir(path: str | Path) -> Path:
    resolved = resolve_project_path(path)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved

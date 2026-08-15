from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - the packaged runtime installs PyYAML
    yaml = None  # type: ignore[assignment]

from .paths import REPOSITORY_ROOT


UNIFIED_CONFIG_ENV = "UNIFIED_CAMERA_CONFIG"
BMW_CONFIG_ENV = "BMW_CONFIG"
RE9_CONFIG_ENV = "RE9_CONFIG"
def _platform_config_names() -> tuple[str, ...]:
    if sys.platform.startswith("linux"):
        return ("linux.local.yaml", "linux.yaml", "default.yaml")
    if sys.platform == "win32":
        return ("windows.local.yaml", "windows.yaml", "default.yaml")
    return ("default.yaml",)


def _resolve_config_path(value: str | Path) -> Path:
    path = Path(os.path.expandvars(str(value))).expanduser()
    if path.is_absolute():
        return path
    # RE9 resolves relative config paths from the repository root. Keep that
    # convention so RE9_CONFIG can be reused without editing the adapter.
    return REPOSITORY_ROOT / path


def _candidate_paths() -> list[Path]:
    selected = (
        os.environ.get(UNIFIED_CONFIG_ENV)
        or os.environ.get(BMW_CONFIG_ENV)
        or os.environ.get(RE9_CONFIG_ENV)
    )
    if selected:
        return [_resolve_config_path(selected)]
    return [REPOSITORY_ROOT / "configs" / name for name in _platform_config_names()]


@dataclass(frozen=True)
class SharedConfig:
    """Small RE9-compatible config view used by alerts and repair workers."""

    raw: dict[str, Any]
    path: Path | None

    @property
    def source_text(self) -> str:
        return str(self.path) if self.path is not None else "defaults"


def load_shared_config() -> SharedConfig:
    if yaml is None:
        return SharedConfig(raw={}, path=None)
    for candidate in _candidate_paths():
        if not candidate.is_file():
            continue
        try:
            payload = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(payload, dict):
            return SharedConfig(raw=payload, path=candidate.resolve())
    return SharedConfig(raw={}, path=None)

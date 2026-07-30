from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .paths import DEFAULT_CONFIG_PATH, resolve_project_path
from .platform_support import platform_config_names


ENV_CONFIG_PATH = "RE9_CONFIG"


def _expand_path(value: str | Path) -> Path:
    return Path(os.path.expandvars(str(value))).expanduser()


def _default_config_path() -> Path:
    for name in platform_config_names(sys.platform):
        candidate = resolve_project_path(Path("configs") / name)
        if candidate.exists():
            return candidate
    return DEFAULT_CONFIG_PATH


@dataclass(frozen=True)
class AppConfig:
    raw: dict[str, Any]
    path: Path

    @property
    def lua_path(self) -> Path:
        return _expand_path(self.raw["game"]["lua_path"])

    @property
    def control_file(self) -> Path:
        return _expand_path(self.raw["lua_logger"]["control_file"])

    @property
    def status_file(self) -> Path:
        return _expand_path(self.raw["lua_logger"]["status_file"])

    @property
    def pose_log_file(self) -> Path:
        return _expand_path(self.raw["lua_logger"]["pose_log_file"])

    @property
    def lua_backup_dir(self) -> Path:
        return resolve_project_path(self.raw["lua_logger"]["backup_dir"])

    @property
    def output_dir(self) -> Path:
        return resolve_project_path(self.raw["report"]["output_dir"])

    @property
    def obs_recording_output_dir(self) -> Path:
        return resolve_project_path(self.raw["obs"].get("recording_output_dir") or "data/videos")

    @property
    def laion_repo_dir(self) -> Path:
        return resolve_project_path(self.raw["laion"]["repo_dir"])

    @property
    def supported_video_extensions(self) -> set[str]:
        return {item.lower() for item in self.raw["video"]["supported_video_extensions"]}


def load_config(config_path: str | Path | None = None) -> AppConfig:
    selected = config_path or os.environ.get(ENV_CONFIG_PATH) or _default_config_path()
    path = _expand_path(selected)
    if not path.is_absolute():
        path = resolve_project_path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return AppConfig(raw=raw, path=path)

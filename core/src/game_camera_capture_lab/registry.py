from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[3]
MATURITY_VALUES = {"stable", "beta", "experimental"}


class RegistryError(ValueError):
    """Raised when a game manifest is missing or unsafe."""


def current_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _repo_path(base: Path, value: str, repo_root: Path) -> Path:
    path = (base / value).resolve()
    try:
        path.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise RegistryError(f"Path escapes the repository: {value}") from exc
    return path


@dataclass(frozen=True)
class GameAction:
    id: str
    label: str
    description: str
    platforms: tuple[str, ...]
    command: tuple[str, ...]
    working_directory: Path

    def is_supported(self, platform: str | None = None) -> bool:
        return (platform or current_platform()) in self.platforms


@dataclass(frozen=True)
class GameAdapter:
    id: str
    name: str
    short_name: str
    engine: str
    maturity: str
    summary: str
    root: Path
    manifest_path: Path
    documentation: Path
    examples: Path
    capabilities: dict[str, str]
    actions: tuple[GameAction, ...]

    def action(self, action_id: str) -> GameAction:
        for action in self.actions:
            if action.id == action_id:
                return action
        raise RegistryError(f"Unknown action {action_id!r} for {self.id}")

    def command_for(
        self,
        action_id: str,
        *,
        repo_root: Path = REPO_ROOT,
        python: str | None = None,
    ) -> list[str]:
        action = self.action(action_id)
        tokens = {
            "{repo}": str(repo_root.resolve()),
            "{game}": str(self.root.resolve()),
            "{python}": python or sys.executable,
        }
        result: list[str] = []
        for part in action.command:
            expanded = part
            for marker, value in tokens.items():
                expanded = expanded.replace(marker, value)
            if "{" in expanded or "}" in expanded:
                raise RegistryError(
                    f"Unresolved command token in {self.id}/{action_id}: {expanded}"
                )
            result.append(expanded)
        return result


def _required_text(payload: dict[str, Any], key: str, manifest: Path) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"{manifest}: {key} must be non-empty text")
    return value.strip()


def _parse_action(
    value: Any,
    *,
    manifest: Path,
    game_root: Path,
    repo_root: Path,
) -> GameAction:
    if not isinstance(value, dict):
        raise RegistryError(f"{manifest}: every action must be an object")
    action_id = _required_text(value, "id", manifest)
    command = value.get("command")
    if not isinstance(command, list) or not command or not all(
        isinstance(part, str) and part for part in command
    ):
        raise RegistryError(f"{manifest}: action {action_id} has no valid command")
    platforms = value.get("platforms", ["windows", "linux", "macos"])
    if not isinstance(platforms, list) or not all(
        item in {"windows", "linux", "macos"} for item in platforms
    ):
        raise RegistryError(f"{manifest}: action {action_id} has invalid platforms")
    return GameAction(
        id=action_id,
        label=_required_text(value, "label", manifest),
        description=str(value.get("description") or ""),
        platforms=tuple(platforms),
        command=tuple(command),
        working_directory=_repo_path(
            game_root,
            str(value.get("working_directory") or "."),
            repo_root,
        ),
    )


def load_manifest(path: str | Path, *, repo_root: Path = REPO_ROOT) -> GameAdapter:
    manifest = Path(path).resolve()
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"Cannot read {manifest}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RegistryError(f"{manifest}: root must be an object")
    if payload.get("schema_version") != 1:
        raise RegistryError(f"{manifest}: unsupported schema_version")

    game_root = manifest.parent
    maturity = _required_text(payload, "maturity", manifest)
    if maturity not in MATURITY_VALUES:
        raise RegistryError(f"{manifest}: invalid maturity {maturity!r}")

    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, dict) or not capabilities:
        raise RegistryError(f"{manifest}: capabilities must be a non-empty object")
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in capabilities.items()):
        raise RegistryError(f"{manifest}: capabilities must map text to text")

    raw_actions = payload.get("actions")
    if not isinstance(raw_actions, list) or not raw_actions:
        raise RegistryError(f"{manifest}: actions must be a non-empty array")
    actions = tuple(
        _parse_action(
            item,
            manifest=manifest,
            game_root=game_root,
            repo_root=repo_root,
        )
        for item in raw_actions
    )
    action_ids = [action.id for action in actions]
    if len(action_ids) != len(set(action_ids)):
        raise RegistryError(f"{manifest}: duplicate action ids")

    return GameAdapter(
        id=_required_text(payload, "id", manifest),
        name=_required_text(payload, "name", manifest),
        short_name=_required_text(payload, "short_name", manifest),
        engine=_required_text(payload, "engine", manifest),
        maturity=maturity,
        summary=_required_text(payload, "summary", manifest),
        root=game_root,
        manifest_path=manifest,
        documentation=_repo_path(
            game_root,
            _required_text(payload, "documentation", manifest),
            repo_root,
        ),
        examples=_repo_path(
            game_root,
            _required_text(payload, "examples", manifest),
            repo_root,
        ),
        capabilities=dict(sorted(capabilities.items())),
        actions=actions,
    )


def discover_manifests(repo_root: Path = REPO_ROOT) -> Iterable[Path]:
    return sorted((repo_root / "games").glob("*/game.json"))


def load_registry(repo_root: Path = REPO_ROOT) -> list[GameAdapter]:
    adapters = [
        load_manifest(path, repo_root=repo_root)
        for path in discover_manifests(repo_root)
    ]
    ids = [adapter.id for adapter in adapters]
    if len(ids) != len(set(ids)):
        raise RegistryError("Duplicate game ids in registry")
    if not adapters:
        raise RegistryError(f"No game manifests found under {repo_root / 'games'}")
    maturity_order = {"stable": 0, "beta": 1, "experimental": 2}
    return sorted(
        adapters,
        key=lambda item: (maturity_order[item.maturity], item.name.casefold()),
    )

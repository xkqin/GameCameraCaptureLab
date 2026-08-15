"""Evidence-bounded game camera support catalog.

This module deliberately keeps public free-camera evidence separate from a
native project profile.  A title can be a strong adaptation candidate without
being safe to inject with the project runtime yet.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from .registry import REPO_ROOT


DEFAULT_CATALOG_PATH = REPO_ROOT / "catalogs" / "game_support_catalog_v1.json"
EVIDENCE_LEVELS = {
    "project_runtime_verified",
    "public_free_camera_verified",
    "excluded",
}
NATIVE_PROFILE_STATUSES = {"runtime_verified", "profile_required", "not_supported"}


class SupportCatalogError(ValueError):
    """Raised when the support catalog would make an ambiguous claim."""


@dataclass(frozen=True)
class SupportSource:
    id: str
    kind: str
    title: str
    uri: str
    checked_on: str


@dataclass(frozen=True)
class GameSupport:
    id: str
    name: str
    engine: str
    evidence_level: str
    native_profile_status: str
    public_camera_features: tuple[str, ...]
    risk: str
    notes: tuple[str, ...]
    source_ids: tuple[str, ...]

    @property
    def can_use_public_free_camera(self) -> bool:
        return self.evidence_level != "excluded" and "free_camera" in self.public_camera_features

    @property
    def project_runtime_verified(self) -> bool:
        return self.native_profile_status == "runtime_verified"


@dataclass(frozen=True)
class SupportCatalog:
    snapshot_date: str
    methodology: str
    sources: tuple[SupportSource, ...]
    games: tuple[GameSupport, ...]

    def source(self, source_id: str) -> SupportSource:
        for source in self.sources:
            if source.id == source_id:
                return source
        raise SupportCatalogError(f"Unknown source id: {source_id}")

    def select(self, evidence_levels: Iterable[str] | None = None) -> tuple[GameSupport, ...]:
        wanted = set(evidence_levels or EVIDENCE_LEVELS)
        unknown = wanted - EVIDENCE_LEVELS
        if unknown:
            raise SupportCatalogError(f"Unknown evidence levels: {sorted(unknown)}")
        return tuple(game for game in self.games if game.evidence_level in wanted)


def _required_text(payload: dict[str, Any], key: str, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SupportCatalogError(f"{label}: {key} must be non-empty text")
    return value.strip()


def _text_tuple(payload: dict[str, Any], key: str, label: str, *, allow_empty: bool) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or (not allow_empty and not value):
        raise SupportCatalogError(f"{label}: {key} must be a text array")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise SupportCatalogError(f"{label}: {key} must contain non-empty text")
    result = tuple(item.strip() for item in value)
    if len(result) != len(set(result)):
        raise SupportCatalogError(f"{label}: {key} contains duplicates")
    return result


def load_support_catalog(path: str | Path = DEFAULT_CATALOG_PATH) -> SupportCatalog:
    source_path = Path(path).resolve()
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SupportCatalogError(f"Cannot read {source_path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise SupportCatalogError(f"{source_path}: unsupported schema_version")

    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise SupportCatalogError(f"{source_path}: sources must be a non-empty array")
    sources: list[SupportSource] = []
    for index, item in enumerate(raw_sources):
        label = f"source[{index}]"
        if not isinstance(item, dict):
            raise SupportCatalogError(f"{label}: must be an object")
        kind = _required_text(item, "kind", label)
        if kind not in {"online", "project"}:
            raise SupportCatalogError(f"{label}: invalid kind {kind!r}")
        sources.append(
            SupportSource(
                id=_required_text(item, "id", label),
                kind=kind,
                title=_required_text(item, "title", label),
                uri=_required_text(item, "uri", label),
                checked_on=_required_text(item, "checked_on", label),
            )
        )
    source_ids = {source.id for source in sources}
    if len(source_ids) != len(sources):
        raise SupportCatalogError("Duplicate source ids")

    raw_games = payload.get("games")
    if not isinstance(raw_games, list) or not raw_games:
        raise SupportCatalogError(f"{source_path}: games must be a non-empty array")
    games: list[GameSupport] = []
    for index, item in enumerate(raw_games):
        label = f"game[{index}]"
        if not isinstance(item, dict):
            raise SupportCatalogError(f"{label}: must be an object")
        evidence_level = _required_text(item, "evidence_level", label)
        if evidence_level not in EVIDENCE_LEVELS:
            raise SupportCatalogError(f"{label}: invalid evidence_level {evidence_level!r}")
        native_status = _required_text(item, "native_profile_status", label)
        if native_status not in NATIVE_PROFILE_STATUSES:
            raise SupportCatalogError(f"{label}: invalid native_profile_status {native_status!r}")
        game_sources = _text_tuple(item, "sources", label, allow_empty=False)
        missing_sources = set(game_sources) - source_ids
        if missing_sources:
            raise SupportCatalogError(f"{label}: unknown source ids {sorted(missing_sources)}")
        features = _text_tuple(item, "public_camera_features", label, allow_empty=True)
        if evidence_level == "public_free_camera_verified" and "free_camera" not in features:
            raise SupportCatalogError(f"{label}: public verification requires free_camera evidence")
        if evidence_level == "public_free_camera_verified" and native_status != "profile_required":
            raise SupportCatalogError(f"{label}: public evidence cannot certify a native profile")
        if native_status == "runtime_verified" and evidence_level != "project_runtime_verified":
            raise SupportCatalogError(f"{label}: runtime_verified requires project evidence")
        games.append(
            GameSupport(
                id=_required_text(item, "id", label),
                name=_required_text(item, "name", label),
                engine=_required_text(item, "engine", label),
                evidence_level=evidence_level,
                native_profile_status=native_status,
                public_camera_features=features,
                risk=_required_text(item, "risk", label),
                notes=_text_tuple(item, "notes", label, allow_empty=False),
                source_ids=game_sources,
            )
        )
    game_ids = {game.id for game in games}
    if len(game_ids) != len(games):
        raise SupportCatalogError("Duplicate game ids")
    return SupportCatalog(
        snapshot_date=_required_text(payload, "snapshot_date", "catalog"),
        methodology=_required_text(payload, "methodology", "catalog"),
        sources=tuple(sources),
        games=tuple(games),
    )


def _rows(games: Sequence[GameSupport]) -> list[dict[str, str]]:
    return [
        {
            "id": game.id,
            "name": game.name,
            "engine": game.engine,
            "evidence_level": game.evidence_level,
            "native_profile_status": game.native_profile_status,
            "risk": game.risk,
        }
        for game in games
    ]


def _print_table(games: Sequence[GameSupport]) -> None:
    from rich.console import Console
    from rich.table import Table

    table = Table(title="Game Camera Support Evidence")
    table.add_column("Game")
    table.add_column("Engine")
    table.add_column("Evidence")
    table.add_column("Project native profile")
    table.add_column("Risk")
    for game in games:
        table.add_row(
            game.name,
            game.engine,
            game.evidence_level,
            game.native_profile_status,
            game.risk,
        )
    Console().print(table)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List evidence-bounded game camera support")
    parser.add_argument(
        "--level",
        action="append",
        choices=sorted(EVIDENCE_LEVELS),
        help="Filter by evidence level; repeat to select multiple levels.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    args = parser.parse_args(argv)
    catalog = load_support_catalog(args.catalog)
    games = catalog.select(args.level)
    if args.json:
        print(
            json.dumps(
                {"snapshot_date": catalog.snapshot_date, "games": _rows(games)},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        _print_table(games)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

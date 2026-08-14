from __future__ import annotations

from collections.abc import Iterable
from collections import Counter
from pathlib import Path


TRAJECTORY_SUFFIXES = {".json", ".csv"}


def discover_trajectory_files(
    search_roots: Iterable[str | Path],
    *,
    extra_paths: Iterable[str | Path] = (),
) -> list[Path]:
    """Return a stable, de-duplicated catalog of selectable trajectory files."""

    discovered: dict[str, Path] = {}

    def add(path_value: str | Path) -> None:
        path = Path(path_value).expanduser()
        if not path.is_file() or path.suffix.lower() not in TRAJECTORY_SUFFIXES:
            return
        resolved = path.resolve()
        discovered[str(resolved).casefold()] = resolved

    for root_value in search_roots:
        root = Path(root_value).expanduser()
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            add(path)
    for path in extra_paths:
        if str(path).strip():
            add(path)

    return sorted(
        discovered.values(),
        key=lambda path: (path.name.casefold(), str(path).casefold()),
    )


def build_trajectory_choice_map(
    paths: Iterable[str | Path],
    *,
    project_root: str | Path | None = None,
) -> dict[str, Path]:
    """Build readable, unique Combobox labels mapped to absolute paths."""

    base = Path(project_root).resolve() if project_root is not None else None
    resolved_paths = [Path(path_value).resolve() for path_value in paths]
    name_counts = Counter(path.name.casefold() for path in resolved_paths)
    result: dict[str, Path] = {}
    for path in resolved_paths:
        label = path.name
        if name_counts[path.name.casefold()] > 1:
            label = str(Path(path.parent.name) / path.name)
        if label in result and result[label] != path:
            try:
                location = path.relative_to(base) if base is not None else path
            except ValueError:
                location = path
            label = str(location)
        if label in result and result[label] != path:
            label = str(path)
        result[label] = path
    return result

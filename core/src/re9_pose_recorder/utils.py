from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Iterable

from rich.console import Console


console = Console()


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def timestamp_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def normalized_suffixes(values: Iterable[str]) -> set[str]:
    return {value.lower() if value.startswith(".") else f".{value.lower()}" for value in values}


def require_file(path: Path, label: str) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"{label} does not exist: {path}")
    if not path.is_file():
        raise FileNotFoundError(f"{label} is not a file: {path}")
    return path


def safe_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def make_unique_dir(base_dir: Path, stem: str) -> Path:
    candidate = base_dir / stem
    if not candidate.exists():
        candidate.mkdir(parents=True)
        return candidate
    suffix = timestamp_id()
    candidate = base_dir / f"{stem}_{suffix}"
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate

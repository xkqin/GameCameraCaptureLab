from __future__ import annotations

import json
from pathlib import Path

from .registry import REPO_ROOT, RegistryError, load_registry


SCHEMA_NAMES = (
    "camera_pose_v1.schema.json",
    "point_set_v1.schema.json",
    "trajectory_v1.schema.json",
)


def validate_repository(repo_root: Path = REPO_ROOT) -> list[str]:
    errors: list[str] = []
    try:
        adapters = load_registry(repo_root)
    except RegistryError as exc:
        return [str(exc)]

    for adapter in adapters:
        if not adapter.documentation.is_file():
            errors.append(f"{adapter.id}: missing documentation {adapter.documentation}")
        if not adapter.examples.exists():
            errors.append(f"{adapter.id}: missing examples {adapter.examples}")
        for action in adapter.actions:
            if not action.working_directory.is_dir():
                errors.append(
                    f"{adapter.id}/{action.id}: missing working directory "
                    f"{action.working_directory}"
                )
            try:
                adapter.command_for(action.id, repo_root=repo_root)
            except RegistryError as exc:
                errors.append(str(exc))

    for name in SCHEMA_NAMES:
        schema_path = repo_root / "schemas" / name
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"Cannot read schema {schema_path}: {exc}")
            continue
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            errors.append(f"{name}: expected JSON Schema draft 2020-12")
        if not schema.get("$id"):
            errors.append(f"{name}: missing $id")
    return errors


def main() -> int:
    errors = validate_repository()
    if errors:
        print("Game Camera Capture Lab validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    adapters = load_registry()
    print(f"Registry OK: {len(adapters)} game adapters, {len(SCHEMA_NAMES)} schemas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

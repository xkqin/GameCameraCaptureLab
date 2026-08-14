from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Iterable

from .models import CameraPose, CapturePoint


VIEW_PATTERNS: tuple[tuple[str, float, tuple[float, ...]], ...] = (
    ("middle", 0.0, tuple(index * 45.0 for index in range(8))),
    ("upper", 45.0, tuple(index * 60.0 for index in range(6))),
    ("lower", -45.0, tuple(index * 60.0 for index in range(6))),
    ("ceiling", 90.0, (0.0,)),
    ("floor", -90.0, (0.0,)),
)


@dataclass(frozen=True)
class StillSample:
    """One dataset-ready view generated from a recorded spatial point."""

    sample_index: int
    point_index: int
    source_point_label: str
    view_index: int
    pattern: str
    label: str
    pose: CameraPose
    time_sec: float = 0.0

    @property
    def index(self) -> int:
        return self.point_index

    def capture_metadata(self) -> dict[str, object]:
        return {
            "sample_index": self.sample_index,
            "view_index": self.view_index,
            "pattern": self.pattern,
            "source_point_label": self.source_point_label,
        }


def build_22_view_plan(points: Iterable[CapturePoint]) -> list[StillSample]:
    """Expand each spatial point using the shared RE9/KCD2 22-view pattern."""

    samples: list[StillSample] = []
    sample_index = 1
    for point in points:
        view_index = 1
        for pattern, pitch_degrees, yaw_values in VIEW_PATTERNS:
            for yaw_degrees in yaw_values:
                label = (
                    f"p{point.index:04d}_{pattern}_"
                    f"yaw{int(yaw_degrees):03d}_pitch{int(pitch_degrees):+03d}"
                )
                samples.append(
                    StillSample(
                        sample_index=sample_index,
                        point_index=point.index,
                        source_point_label=point.label,
                        view_index=view_index,
                        pattern=pattern,
                        label=label,
                        pose=replace(
                            point.pose,
                            yaw_degrees=yaw_degrees,
                            pitch_degrees=pitch_degrees,
                            roll_degrees=0.0,
                        ),
                    )
                )
                sample_index += 1
                view_index += 1
    return samples


def view_pattern_manifest() -> list[dict[str, object]]:
    return [
        {
            "pattern": pattern,
            "pitch_degrees": pitch_degrees,
            "yaw_degrees": list(yaw_values),
            "view_count": len(yaw_values),
        }
        for pattern, pitch_degrees, yaw_values in VIEW_PATTERNS
    ]


def find_latest_resumable_static_run(
    captures_root: str | Path,
    *,
    scene_id: str,
    point_map_source: str | Path | None = None,
) -> dict[str, object] | None:
    """Return the newest interrupted still run that is safe to continue.

    A continuation is written as a new run directory.  The child manifest
    points back to its source manifest, so older partial runs must be hidden
    once a continuation exists; otherwise the UI could offer the same samples
    twice after a successful continuation.  ``sample_index`` is global to the
    full plan, while ``requested_count`` is only the number of samples in that
    particular run, so the plan's ``expected_image_count`` is the authoritative
    total for resume decisions and progress text.
    """

    root = Path(captures_root)
    if not root.is_dir():
        return None
    current_source = (
        Path(point_map_source).expanduser().resolve()
        if point_map_source is not None and str(point_map_source).strip()
        else None
    )

    def resolve_reference(value: object, *, base: Path) -> Path | None:
        if value is None or not str(value).strip():
            return None
        reference = Path(str(value)).expanduser()
        if not reference.is_absolute():
            reference = base / reference
        return reference.resolve()

    manifests = sorted(
        root.glob("*/manifest.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    entries: list[tuple[Path, dict[str, object], dict[str, object]]] = []
    superseded: set[Path] = set()
    for manifest_path in manifests:
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            plan = payload.get("capture_plan")
            if not isinstance(plan, dict):
                continue
            source_manifest = resolve_reference(
                plan.get("resume_source_manifest"), base=manifest_path.parent
            )
            if source_manifest is not None:
                superseded.add(source_manifest)
            entries.append((manifest_path, payload, plan))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue

    for manifest_path, payload, plan in entries:
        if manifest_path.resolve() in superseded:
            continue
        if payload.get("status") not in {"failed", "stopped"}:
            continue
        if str(plan.get("scene_id") or scene_id) != scene_id:
            continue
        plan_source = resolve_reference(
            plan.get("point_map_source"), base=manifest_path.parent
        )
        if current_source is not None and plan_source is not None:
            if plan_source != current_source:
                continue

        try:
            expected = int(
                plan.get("expected_image_count")
                or payload.get("requested_count")
                or 0
            )
            captured = int(payload.get("captured_count") or 0)
            selected_start = max(1, int(plan.get("selected_start_sample") or 1))
        except (TypeError, ValueError):
            continue

        frames = payload.get("frames")
        frame_rows = frames if isinstance(frames, list) else []
        sample_indices: list[int] = []
        for frame in frame_rows:
            if not isinstance(frame, dict) or frame.get("sample_index") is None:
                continue
            try:
                sample_indices.append(int(frame["sample_index"]))
            except (TypeError, ValueError):
                continue
        if captured <= 0:
            captured = len(frame_rows)
        last_sample = max(sample_indices, default=selected_start - 1)
        if not sample_indices and captured > 0:
            last_sample = selected_start - 1 + captured
        next_sample = last_sample + 1
        if expected <= 0 or next_sample > expected:
            continue
        return {
            "manifest_path": str(manifest_path.resolve()),
            "selected_start_ordinal": int(plan.get("selected_start_ordinal") or 1),
            "selected_start_sample": selected_start,
            "next_sample": next_sample,
            "last_sample": last_sample,
            "requested_count": expected,
            "expected_image_count": expected,
            "captured_count": captured,
            "status": str(payload.get("status")),
        }
    return None

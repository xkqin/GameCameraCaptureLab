"""Profile-driven discovery helpers for the native UE Camera Runtime.

The native bridge still owns the in-process hook and frame-time interpolation.
This module is deliberately offline/read-only: it validates profiles and scans
an executable on disk without injecting or modifying the target binary.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import struct
from typing import Any, Iterable, Mapping


class UeRuntimeProfileError(ValueError):
    """Raised for an invalid or unsupported UE Camera Runtime profile."""


@dataclass(frozen=True)
class BytePattern:
    """A byte pattern where ``None`` denotes a wildcard byte."""

    values: tuple[int | None, ...]

    @classmethod
    def parse(cls, text: str) -> "BytePattern":
        if not isinstance(text, str) or not text.strip():
            raise UeRuntimeProfileError("pattern must be non-empty text")
        values: list[int | None] = []
        for token in text.split():
            if token in {"?", "??"}:
                values.append(None)
                continue
            if len(token) != 2:
                raise UeRuntimeProfileError(f"invalid pattern byte: {token!r}")
            try:
                value = int(token, 16)
            except ValueError as exc:
                raise UeRuntimeProfileError(f"invalid pattern byte: {token!r}") from exc
            if not 0 <= value <= 0xFF:
                raise UeRuntimeProfileError(f"pattern byte out of range: {token!r}")
            values.append(value)
        if not values:
            raise UeRuntimeProfileError("pattern has no bytes")
        return cls(tuple(values))

    def find_all(self, data: bytes | bytearray | memoryview) -> tuple[int, ...]:
        raw = bytes(data)
        width = len(self.values)
        if len(raw) < width:
            return ()
        # Use the longest fixed run as an anchor. UE shipping binaries are
        # large, so checking every byte against the whole pattern is needlessly
        # expensive for an offline profile check.
        anchor_start = 0
        anchor_values: tuple[int, ...] = ()
        current_start = 0
        current_values: list[int] = []
        for index, value in enumerate((*self.values, None)):
            if value is None:
                if len(current_values) > len(anchor_values):
                    anchor_start = current_start
                    anchor_values = tuple(current_values)
                current_values = []
                current_start = index + 1
            else:
                current_values.append(value)
        if not anchor_values:
            return tuple(range(len(raw) - width + 1))
        anchor = bytes(anchor_values)
        matches: list[int] = []
        cursor = 0
        while True:
            found = raw.find(anchor, cursor)
            if found < 0:
                break
            offset = found - anchor_start
            if 0 <= offset <= len(raw) - width and all(
                expected is None or raw[offset + index] == expected
                for index, expected in enumerate(self.values)
            ):
                matches.append(offset)
            cursor = found + 1
        return tuple(matches)


@dataclass(frozen=True)
class HookProfile:
    abi: str
    pattern: BytePattern
    hook_offset: int
    continuation_offset: int
    min_matches: int
    max_matches: int

    @classmethod
    def from_json(cls, payload: Any, *, field_name: str) -> "HookProfile":
        if not isinstance(payload, dict):
            raise UeRuntimeProfileError(f"{field_name} must be an object")
        try:
            abi = payload["abi"]
            pattern = BytePattern.parse(payload["pattern"])
            hook_offset = payload["hook_offset"]
            continuation_offset = payload["continuation_offset"]
            min_matches = payload["min_matches"]
            max_matches = payload["max_matches"]
        except KeyError as exc:
            raise UeRuntimeProfileError(f"{field_name} is missing {exc.args[0]}") from exc
        if not isinstance(abi, str) or not abi:
            raise UeRuntimeProfileError(f"{field_name}.abi must be text")
        numeric = {
            "hook_offset": hook_offset,
            "continuation_offset": continuation_offset,
            "min_matches": min_matches,
            "max_matches": max_matches,
        }
        if not all(isinstance(value, int) and not isinstance(value, bool)
                   for value in numeric.values()):
            raise UeRuntimeProfileError(f"{field_name} offsets and match limits must be integers")
        if hook_offset < 0 or continuation_offset <= 0:
            raise UeRuntimeProfileError(f"{field_name} offsets are out of range")
        if min_matches < 1 or max_matches < min_matches:
            raise UeRuntimeProfileError(f"{field_name} match limits are invalid")
        if hook_offset >= len(pattern.values) or continuation_offset <= hook_offset:
            raise UeRuntimeProfileError(f"{field_name} offsets exceed its pattern")
        return cls(abi, pattern, hook_offset, continuation_offset, min_matches, max_matches)


@dataclass(frozen=True)
class UeCameraProfile:
    id: str
    name: str
    engine: str
    process_names: tuple[str, ...]
    module: str
    camera_hook: HookProfile
    hud_hook: HookProfile | None
    coordinate_system: dict[str, Any]
    capabilities: tuple[str, ...]

    @classmethod
    def from_json(cls, payload: Any, *, source: Path | None = None) -> "UeCameraProfile":
        label = str(source or "profile")
        if not isinstance(payload, dict):
            raise UeRuntimeProfileError(f"{label}: root must be an object")
        if payload.get("schema_version") != 1:
            raise UeRuntimeProfileError(f"{label}: unsupported schema_version")
        profile_id = payload.get("id")
        engine = payload.get("engine")
        process_names = payload.get("process_names")
        if not isinstance(profile_id, str) or not profile_id:
            raise UeRuntimeProfileError(f"{label}: id must be non-empty text")
        if engine not in {"ue", "ue4", "ue5"}:
            raise UeRuntimeProfileError(f"{label}: unsupported engine {engine!r}")
        if (not isinstance(process_names, list) or not process_names or
                not all(isinstance(item, str) and item for item in process_names)):
            raise UeRuntimeProfileError(f"{label}: process_names must be a non-empty list")
        module = payload.get("module", "main_executable")
        if module != "main_executable":
            raise UeRuntimeProfileError(f"{label}: only main_executable is supported")
        return cls(
            id=profile_id,
            name=str(payload.get("name") or profile_id),
            engine=engine,
            process_names=tuple(process_names),
            module=module,
            camera_hook=HookProfile.from_json(payload.get("camera_hook"), field_name="camera_hook"),
            hud_hook=(HookProfile.from_json(payload["hud_hook"], field_name="hud_hook")
                      if payload.get("hud_hook") is not None else None),
            coordinate_system=dict(payload.get("coordinate_system") or {}),
            capabilities=tuple(str(item) for item in (payload.get("capabilities") or [])),
        )


def load_profile(path: str | Path) -> UeCameraProfile:
    source = Path(path).resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UeRuntimeProfileError(f"cannot read {source}: {exc}") from exc
    return UeCameraProfile.from_json(payload, source=source)


def discover_profiles(directory: str | Path) -> tuple[Path, ...]:
    return tuple(sorted(Path(directory).resolve().glob("*.json")))


def load_profiles(directory: str | Path) -> tuple[UeCameraProfile, ...]:
    profiles = tuple(load_profile(path) for path in discover_profiles(directory))
    ids = [profile.id for profile in profiles]
    if len(ids) != len(set(ids)):
        raise UeRuntimeProfileError("duplicate UE Camera Runtime profile id")
    return profiles


def profile_for_process(
    process_name: str,
    profiles: Iterable[UeCameraProfile],
) -> UeCameraProfile | None:
    wanted = process_name.casefold()
    for profile in profiles:
        if any(name.casefold() == wanted for name in profile.process_names):
            return profile
    return None


@dataclass(frozen=True)
class SectionBytes:
    name: str
    offset: int
    data: bytes


def _pe_executable_sections(data: bytes) -> tuple[SectionBytes, ...]:
    """Read PE section bytes with stdlib only; no code is executed."""
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise UeRuntimeProfileError("file is not a PE executable")
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if pe_offset + 24 > len(data) or data[pe_offset:pe_offset + 4] != b"PE\0\0":
        raise UeRuntimeProfileError("invalid PE header")
    number_sections = struct.unpack_from("<H", data, pe_offset + 6)[0]
    optional_size = struct.unpack_from("<H", data, pe_offset + 20)[0]
    section_table = pe_offset + 24 + optional_size
    sections: list[SectionBytes] = []
    for index in range(number_sections):
        record = section_table + index * 40
        if record + 40 > len(data):
            raise UeRuntimeProfileError("truncated PE section table")
        name = data[record:record + 8].split(b"\0", 1)[0].decode("ascii", "replace")
        raw_size, raw_offset = struct.unpack_from("<II", data, record + 16)
        characteristics = struct.unpack_from("<I", data, record + 36)[0]
        if not characteristics & 0x20000000:  # IMAGE_SCN_MEM_EXECUTE
            continue
        end = raw_offset + raw_size
        if raw_offset > len(data) or end > len(data):
            raise UeRuntimeProfileError(f"section {name} is outside the file")
        sections.append(SectionBytes(name, raw_offset, data[raw_offset:end]))
    return tuple(sections)


def scan_executable(path: str | Path, hook: HookProfile) -> tuple[tuple[str, int, int], ...]:
    """Return ``(section, pattern_offset, hook_address_offset)`` matches."""
    return scan_executable_hooks(path, {"hook": hook})["hook"]


def scan_executable_hooks(
    path: str | Path,
    hooks: Mapping[str, HookProfile],
) -> dict[str, tuple[tuple[str, int, int], ...]]:
    """Scan several hooks while reading a large game executable only once."""
    source = Path(path).resolve()
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise UeRuntimeProfileError(f"cannot read {source}: {exc}") from exc
    sections = _pe_executable_sections(data)
    result: dict[str, tuple[tuple[str, int, int], ...]] = {}
    for name, hook in hooks.items():
        matches: list[tuple[str, int, int]] = []
        for section in sections:
            for pattern_offset in hook.pattern.find_all(section.data):
                matches.append(
                    (
                        section.name,
                        pattern_offset,
                        section.offset + pattern_offset + hook.hook_offset,
                    )
                )
        result[name] = tuple(matches)
    return result


def validate_match_count(profile: UeCameraProfile, matches: Iterable[Any]) -> bool:
    count = len(tuple(matches))
    return profile.camera_hook.min_matches <= count <= profile.camera_hook.max_matches

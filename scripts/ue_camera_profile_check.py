from __future__ import annotations

import argparse
import json
from pathlib import Path

from game_camera_capture_lab.ue_runtime import (
    UeRuntimeProfileError,
    load_profiles,
    profile_for_process,
    scan_executable,
    validate_match_count,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline-only UE Camera Runtime profile and signature check"
    )
    parser.add_argument("--profile-dir", required=True, type=Path)
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument("--process", help="process name used to select a profile")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    try:
        profiles = load_profiles(args.profile_dir)
        process_name = args.process or args.exe.name
        profile = profile_for_process(process_name, profiles)
        if profile is None:
            raise UeRuntimeProfileError(
                f"no profile matches process name {process_name!r}"
            )
        matches = scan_executable(args.exe, profile.camera_hook)
        payload = {
            "profile": profile.id,
            "engine": profile.engine,
            "executable": str(args.exe.resolve()),
            "camera_match_count": len(matches),
            "camera_match_limit": [
                profile.camera_hook.min_matches,
                profile.camera_hook.max_matches,
            ],
            "camera_match_count_valid": validate_match_count(profile, matches),
            "matches": [
                {"section": section, "pattern_offset": offset, "hook_offset": hook}
                for section, offset, hook in matches
            ],
            "offline_only": True,
        }
    except UeRuntimeProfileError as exc:
        print(f"UE_CAMERA_PROFILE_ERROR: {exc}")
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.as_json else (
        f"UE_CAMERA_PROFILE_OK profile={payload['profile']} "
        f"camera_matches={payload['camera_match_count']} "
        f"valid={payload['camera_match_count_valid']} offline_only=true"
    ))
    return 0 if payload["camera_match_count_valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
game_id="${1:-unified-auto}"
if [[ $# -gt 0 ]]; then
    shift
fi

profile_path="$repository_root/runtime/ue-camera-runtime/profiles/$game_id.json"
adapter_dir="$repository_root/games/$game_id"
shared_studio_dir="$repository_root/games/black-myth-wukong"

if [[ "$game_id" == "unified-auto" ]]; then
    profile_path="$repository_root/runtime/ue-camera-runtime/profiles"
    adapter_dir="$repository_root"
elif [[ ! -f "$profile_path" || ! -d "$adapter_dir" ]]; then
    echo "Unknown or incomplete adapter: $game_id" >&2
    exit 2
fi

python_command=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        python_command="$(command -v "$candidate")"
        break
    fi
done
if [[ -z "$python_command" ]]; then
    echo "Python 3.10 or newer was not found." >&2
    exit 1
fi

eval "$("$python_command" - "$game_id" "$profile_path" "$adapter_dir/game.json" <<'PY'
import json
from pathlib import Path
import shlex
import sys

game_id = sys.argv[1]
profile_path = Path(sys.argv[2])
if game_id == "unified-auto":
    profiles = [json.loads(path.read_text(encoding="utf-8")) for path in profile_path.glob("*.json")]
    process_names = sorted({name for profile in profiles for name in profile["process_names"]})
    values = {
        "GAME_CAMERA_GAME_ID": game_id,
        "GAME_CAMERA_GAME_NAME": "Auto-detect Supported Game",
        "GAME_CAMERA_GAME_SHORT_NAME": "Auto-detect",
        "GAME_CAMERA_PROCESS_NAMES": ",".join(process_names),
        "GAME_CAMERA_WINDOW_PATTERNS": ",".join(process_names),
        "GAME_CAMERA_HUD_REQUIRED": "0",
    }
else:
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    manifest = json.load(open(sys.argv[3], encoding="utf-8"))
    values = {
        "GAME_CAMERA_GAME_ID": profile["id"],
        "GAME_CAMERA_GAME_NAME": manifest.get("name", profile["name"]),
        "GAME_CAMERA_GAME_SHORT_NAME": manifest.get("short_name", profile["name"]),
        "GAME_CAMERA_PROCESS_NAMES": ",".join(profile["process_names"]),
        "GAME_CAMERA_WINDOW_PATTERNS": ",".join(
            [manifest.get("name", profile["name"]), manifest.get("short_name", profile["name"])]
        ),
        "GAME_CAMERA_HUD_REQUIRED": "1" if profile.get("hud_hook") else "0",
    }
for key, value in values.items():
    print(f"export {key}={shlex.quote(str(value))}")
PY
)"

export GAME_CAMERA_ADAPTER_ROOT="$adapter_dir"
export UE_CAMERA_NATIVE_DIR="$shared_studio_dir/native"
if [[ "$game_id" == "unified-auto" ]]; then
    export GAME_CAMERA_DATA_ROOT="$repository_root/capture_data/unified-camera"
    export GAME_CAMERA_SETTINGS_PATH="$GAME_CAMERA_DATA_ROOT/settings.json"
fi
exec "$shared_studio_dir/launch_bmw_capture_studio.sh" "$@"

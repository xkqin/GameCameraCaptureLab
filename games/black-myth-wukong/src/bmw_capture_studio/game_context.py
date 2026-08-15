from __future__ import annotations

import os


def _csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.environ.get(name, "")
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    return items or default


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().casefold() not in {"0", "false", "no", "off"}


AUTO_GAME_ID = "unified-auto"
GAME_ID = os.environ.get("GAME_CAMERA_GAME_ID", "black-myth-wukong").strip()
AUTO_DETECT = GAME_ID.casefold() in {AUTO_GAME_ID, "auto"}
GAME_NAME = os.environ.get(
    "GAME_CAMERA_GAME_NAME",
    "自动识别 / Auto-detect" if AUTO_DETECT else "黑神话：悟空",
).strip()
GAME_SHORT_NAME = os.environ.get(
    "GAME_CAMERA_GAME_SHORT_NAME",
    "自动识别 / Auto-detect" if AUTO_DETECT else GAME_NAME,
).strip()
if AUTO_DETECT:
    GAME_NAME = "自动识别受支持游戏 / Auto-detect Supported Game"
    GAME_SHORT_NAME = "自动识别 / Auto-detect"
PRODUCT_NAME_ZH = "统一游戏相机采集器"
PRODUCT_NAME_EN = "Unified Game Camera Capture Studio"
PRODUCT_TITLE = f"{PRODUCT_NAME_ZH} / {PRODUCT_NAME_EN}"
PROCESS_NAMES = _csv_env(
    "GAME_CAMERA_PROCESS_NAMES",
    () if AUTO_DETECT else ("b1-Win64-Shipping.exe", "BlackMythWukong.exe"),
)
WINDOW_PATTERNS = _csv_env(
    "GAME_CAMERA_WINDOW_PATTERNS",
    () if AUTO_DETECT else ("Black Myth", "Wukong", "b1-Win64"),
)
HUD_REQUIRED = _bool_env("GAME_CAMERA_HUD_REQUIRED", not AUTO_DETECT)

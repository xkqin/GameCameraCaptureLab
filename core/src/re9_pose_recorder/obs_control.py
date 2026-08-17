from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

import obsws_python as obs


DEFAULT_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".flv", ".avi"}


def _response_value(response: Any, *names: str) -> Any:
    for name in names:
        if hasattr(response, name):
            return getattr(response, name)
    data = getattr(response, "responseData", None) or getattr(response, "datain", None)
    if isinstance(data, dict):
        for name in names:
            if name in data:
                return data[name]
    return None


class OBSController:
    def __init__(self, host: str, port: int, password: str = "") -> None:
        self.client = connect_obs(host, port, password)

    def get_obs_version(self) -> Any:
        return get_obs_version(self.client)

    def get_record_status(self) -> Any:
        return get_record_status(self.client)

    def start_recording(self) -> Any:
        return start_recording(self.client)

    def stop_recording(self) -> str | None:
        return stop_recording(self.client)

    def get_record_directory(self) -> Path | None:
        return get_record_directory(self.client)

    def set_record_directory(self, directory: str | Path) -> Path:
        return set_record_directory(self.client, directory)

    def capture_source_screenshot(
        self,
        source_name: str | None = None,
        image_format: str = "png",
        width: int = 0,
        height: int = 0,
        quality: int = 100,
    ) -> tuple[bytes, str]:
        return capture_source_screenshot(
            self.client,
            source_name=source_name,
            image_format=image_format,
            width=width,
            height=height,
            quality=quality,
        )

    def save_source_screenshot(
        self,
        file_path: str | Path,
        source_name: str | None = None,
        image_format: str = "png",
        width: int = 0,
        height: int = 0,
        quality: int = 100,
    ) -> str:
        return save_source_screenshot(
            self.client,
            file_path=file_path,
            source_name=source_name,
            image_format=image_format,
            width=width,
            height=height,
            quality=quality,
        )


def connect_obs(host: str, port: int, password: str = "") -> obs.ReqClient:
    try:
        return obs.ReqClient(host=host, port=port, password=password, timeout=5)
    except Exception as exc:
        raise ConnectionError(
            "Could not connect to OBS WebSocket. Open OBS, enable Tools -> WebSocket Server Settings, "
            f"confirm port {port}, and check the password."
        ) from exc


def get_obs_version(client: obs.ReqClient) -> Any:
    return client.get_version()


def get_record_status(client: obs.ReqClient) -> Any:
    return client.get_record_status()


def start_recording(client: obs.ReqClient) -> Any:
    return client.start_record()


def stop_recording(client: obs.ReqClient) -> str | None:
    response = client.stop_record()
    output_path = _response_value(response, "outputPath", "output_path")
    return str(output_path) if output_path else None


def get_record_directory(client: obs.ReqClient) -> Path | None:
    try:
        response = client.get_record_directory()
    except Exception:
        return None
    directory = _response_value(response, "recordDirectory", "record_directory")
    return Path(directory) if directory else None


def set_record_directory(client: obs.ReqClient, directory: str | Path) -> Path:
    target = Path(directory).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    client.set_record_directory(str(target))
    return target


def get_current_program_scene_name(client: obs.ReqClient) -> str:
    response = client.get_current_program_scene()
    source_name = _response_value(response, "current_program_scene_name", "currentProgramSceneName")
    if not source_name:
        raise RuntimeError("Could not determine the current OBS Program scene.")
    return str(source_name)


def get_base_canvas_size(client: obs.ReqClient) -> tuple[int, int]:
    """Return OBS base canvas size, falling back to output size if needed."""
    response = client.get_video_settings()
    width = _response_value(response, "base_width", "baseWidth", "base_width")
    height = _response_value(response, "base_height", "baseHeight", "base_height")
    if not width or not height:
        width = _response_value(response, "output_width", "outputWidth", "output_width")
        height = _response_value(response, "output_height", "outputHeight", "output_height")
    if not width or not height:
        raise RuntimeError("Could not determine OBS canvas size.")
    return int(width), int(height)


def capture_source_screenshot(
    client: obs.ReqClient,
    source_name: str | None = None,
    image_format: str = "png",
    width: int = 0,
    height: int = 0,
    quality: int = 100,
) -> tuple[bytes, str]:
    """Capture a still image from the current OBS Program scene or a named source."""
    normalized_format = image_format.lower().lstrip(".")
    if normalized_format == "jpeg":
        normalized_format = "jpg"
    if normalized_format not in {"jpg", "png"}:
        raise ValueError("image_format must be 'jpg' or 'png'.")
    source = source_name or get_current_program_scene_name(client)
    if int(width) <= 0 or int(height) <= 0:
        width, height = get_base_canvas_size(client)
    screenshot = client.get_source_screenshot(str(source), normalized_format, int(width), int(height), int(quality))
    image_data = _response_value(screenshot, "image_data", "imageData")
    if not image_data:
        raise RuntimeError("OBS did not return screenshot image data.")
    encoded = str(image_data)
    if "," in encoded:
        encoded = encoded.split(",", 1)[1]
    return base64.b64decode(encoded), str(source)


def save_source_screenshot(
    client: obs.ReqClient,
    file_path: str | Path,
    source_name: str | None = None,
    image_format: str = "png",
    width: int = 0,
    height: int = 0,
    quality: int = 100,
) -> str:
    """Save a still image from OBS directly to disk, avoiding base64 transfer overhead."""
    normalized_format = image_format.lower().lstrip(".")
    if normalized_format == "jpeg":
        normalized_format = "jpg"
    if normalized_format not in {"jpg", "png"}:
        raise ValueError("image_format must be 'jpg' or 'png'.")
    source = source_name or get_current_program_scene_name(client)
    if int(width) <= 0 or int(height) <= 0:
        width, height = get_base_canvas_size(client)
    target = Path(file_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    client.save_source_screenshot(str(source), normalized_format, str(target), int(width), int(height), int(quality))
    if not target.exists():
        raise RuntimeError(f"OBS did not create screenshot file: {target}")
    return str(source)


def find_latest_video_file(
    directory: str | Path,
    before_time: float | None = None,
    supported_extensions: set[str] | None = None,
    wait_seconds: float = 20.0,
    stable_checks: int = 3,
) -> Path | None:
    root = Path(directory).expanduser()
    suffixes = supported_extensions or DEFAULT_VIDEO_EXTENSIONS
    deadline = time.monotonic() + max(0.0, wait_seconds)
    newest: Path | None = None
    while True:
        candidates: list[Path] = []
        if root.exists():
            for path in root.iterdir():
                if path.is_file() and path.suffix.lower() in suffixes:
                    if before_time is None or path.stat().st_mtime >= before_time - 2.0:
                        candidates.append(path)
        if candidates:
            candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
            newest = candidates[0]
            break
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.5)

    stable_size = -1
    stable_seen = 0
    while time.monotonic() < deadline:
        size = newest.stat().st_size
        if size > 0 and size == stable_size:
            stable_seen += 1
            if stable_seen >= max(1, stable_checks):
                break
        else:
            stable_seen = 0
            stable_size = size
        time.sleep(1.0)
    return newest


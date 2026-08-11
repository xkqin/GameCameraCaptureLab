from __future__ import annotations

from pathlib import Path
import time
from typing import Any


def obs_available() -> bool:
    try:
        import obsws_python  # noqa: F401
    except ImportError:
        return False
    return True


def _response_value(response: Any, *names: str) -> Any:
    for name in names:
        if hasattr(response, name):
            return getattr(response, name)
    data = getattr(response, "responseData", None) or getattr(
        response, "datain", None
    )
    if isinstance(data, dict):
        for name in names:
            if name in data:
                return data[name]
    return None


class OBSBridge:
    def __init__(self, host: str, port: int, password: str = "") -> None:
        try:
            import obsws_python as obs
        except ImportError as exc:
            raise RuntimeError(
                "obsws-python is unavailable. Launch the project with the bundled "
                "launcher, which reuses the RE9 project environment."
            ) from exc
        try:
            self.client = obs.ReqClient(
                host=host,
                port=int(port),
                password=password,
                timeout=5,
            )
        except Exception as exc:
            raise ConnectionError(
                "Could not connect to OBS WebSocket. Enable it under "
                "Tools -> WebSocket Server Settings and verify the port/password."
            ) from exc

    def test(self) -> dict[str, Any]:
        response = self.client.get_version()
        return {
            "obs_version": _response_value(
                response, "obs_version", "obsVersion"
            ),
            "websocket_version": _response_value(
                response, "obs_web_socket_version", "obsWebSocketVersion"
            ),
        }

    def current_scene(self) -> str:
        response = self.client.get_current_program_scene()
        name = _response_value(
            response,
            "current_program_scene_name",
            "currentProgramSceneName",
        )
        if not name:
            raise RuntimeError("OBS did not return the current Program scene")
        return str(name)

    def save_screenshot(
        self,
        path: str | Path,
        *,
        source_name: str = "",
        image_format: str = "jpg",
        width: int = 1920,
        height: int = 1080,
        quality: int = 100,
    ) -> str:
        target = Path(path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        normalized = image_format.lower().lstrip(".")
        if normalized == "jpeg":
            normalized = "jpg"
        if normalized not in {"jpg", "png"}:
            raise ValueError("Image format must be jpg or png")
        source = source_name.strip() or self.current_scene()
        self.client.save_source_screenshot(
            source,
            normalized,
            str(target),
            int(width),
            int(height),
            int(quality),
        )
        if not target.exists():
            raise RuntimeError(f"OBS did not create screenshot: {target}")
        return source

    def start_recording(self) -> None:
        self.client.start_record()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if self.recording_status()["active"]:
                return
            time.sleep(0.05)
        raise RuntimeError(
            "OBS accepted StartRecord but did not enter recording state within 3 seconds"
        )

    def stop_recording(self) -> str | None:
        if not self.recording_status()["active"]:
            return None
        response = self.client.stop_record()
        output = _response_value(response, "output_path", "outputPath")
        return str(output) if output else None

    def recording_status(self) -> dict[str, Any]:
        response = self.client.get_record_status()
        return {
            "active": bool(
                _response_value(response, "output_active", "outputActive")
            ),
            "paused": bool(
                _response_value(response, "output_paused", "outputPaused")
            ),
            "timecode": _response_value(
                response, "output_timecode", "outputTimecode"
            ),
        }

from __future__ import annotations

from pathlib import Path
import time
from typing import Any


CAPTURE_WIDTH = 1920
CAPTURE_HEIGHT = 1080


def _value(response: Any, *names: str) -> Any:
    for name in names:
        if hasattr(response, name):
            return getattr(response, name)
        if isinstance(response, dict) and name in response:
            return response[name]
    return None


class OBSBridge:
    """Small OBS WebSocket adapter with recording and audio safety checks."""

    def __init__(self, host: str, port: int, password: str = "") -> None:
        try:
            import obsws_python as obs
        except ImportError as exc:
            raise RuntimeError("缺少 obsws-python，请重新运行启动脚本安装依赖") from exc
        try:
            self.client = obs.ReqClient(
                host=host,
                port=int(port),
                password=password,
                timeout=5,
            )
        except Exception as exc:
            raise RuntimeError(f"无法连接 OBS WebSocket {host}:{port}：{exc}") from exc
        self._audio_snapshot: dict[str, bool] | None = None

    def close(self) -> None:
        disconnect = getattr(self.client, "disconnect", None)
        if callable(disconnect):
            disconnect()

    def test(self) -> dict[str, Any]:
        response = self.client.get_version()
        return {
            "obs_version": _value(response, "obs_version", "obsVersion"),
            "websocket_version": _value(
                response, "obs_web_socket_version", "obsWebSocketVersion"
            ),
        }

    def current_scene(self) -> str:
        response = self.client.get_current_program_scene()
        name = _value(
            response,
            "current_program_scene_name",
            "currentProgramSceneName",
        )
        if not name:
            raise RuntimeError("OBS 未返回当前 Program 场景")
        return str(name)

    def video_canvas_size(self) -> tuple[int, int]:
        """Return OBS's base canvas size for source screenshots.

        OBS requires SaveSourceScreenshot dimensions to be at least 8 pixels;
        zero is not a request for native size. Keep the dimension fallback
        local to OBS parameters and never fall back to a different capture
        mechanism.
        """

        try:
            response = self.client.get_video_settings()
            width = int(_value(response, "base_width", "baseWidth") or 0)
            height = int(_value(response, "base_height", "baseHeight") or 0)
        except (AttributeError, TypeError, ValueError, OSError):
            width = height = 0
        if width < 8 or height < 8:
            return CAPTURE_WIDTH, CAPTURE_HEIGHT
        return width, height

    def capture_size(self) -> tuple[int, int]:
        """Return the fixed dataset image size.

        OBS may use a 2560x1440 (2K) base canvas, but still-image output is
        intentionally normalized to Full HD. OBS performs this scaling while
        writing the source screenshot.
        """

        return CAPTURE_WIDTH, CAPTURE_HEIGHT

    def save_screenshot(
        self,
        path: str | Path,
        *,
        source_name: str = "",
        image_format: str = "png",
        width: int = 1920,
        height: int = 1080,
        quality: int = 100,
        timeout_sec: float = 5.0,
    ) -> str:
        """Save one OBS source screenshot and wait for the new file.

        The caller should normally pass :meth:`capture_size`. There is
        deliberately no window-capture fallback here: a successful static
        sample must have been produced by OBS WebSocket.
        """

        target = Path(path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        normalized = image_format.lower().lstrip(".")
        if normalized == "jpeg":
            normalized = "jpg"
        if normalized not in {"jpg", "png"}:
            raise ValueError("Image format must be jpg or png")
        if int(width) < 8 or int(height) < 8:
            raise ValueError("OBS 截图尺寸必须至少为 8×8 像素")
        source = source_name.strip() or self.current_scene()

        # OBS writes asynchronously. Remove a stale file first so a previous
        # capture can never be mistaken for the current pose.
        if target.exists():
            target.unlink()
        self.client.save_source_screenshot(
            source,
            normalized,
            str(target),
            int(width),
            int(height),
            int(quality),
        )

        deadline = time.monotonic() + max(0.1, float(timeout_sec))
        while time.monotonic() < deadline:
            try:
                if target.is_file() and target.stat().st_size > 0:
                    return source
            except OSError:
                pass
            time.sleep(0.01)
        raise TimeoutError(f"OBS 未在限定时间内写入截图：{target}")

    def set_record_directory(self, directory: str | Path) -> Path:
        target = Path(directory).resolve()
        target.mkdir(parents=True, exist_ok=True)
        self.client.set_record_directory(str(target))
        return target

    def mute_all_audio_inputs(self) -> int:
        response = self.client.get_input_list()
        inputs = _value(response, "inputs")
        if not isinstance(inputs, list):
            raise RuntimeError("OBS 未返回输入源列表，已阻止可能带声音的录像")
        snapshot: dict[str, bool] = {}
        try:
            for item in inputs:
                if not isinstance(item, dict):
                    continue
                name = item.get("inputName") or item.get("input_name")
                if not name:
                    continue
                source = str(name)
                try:
                    muted = _value(self.client.get_input_mute(source), "input_muted", "inputMuted")
                except Exception:
                    continue
                if muted is None:
                    continue
                snapshot[source] = bool(muted)
                self.client.set_input_mute(source, True)
        except Exception as exc:
            for source, muted in snapshot.items():
                try:
                    self.client.set_input_mute(source, muted)
                except Exception:
                    pass
            raise RuntimeError("OBS 音频源未能全部静音，录像未开始") from exc
        self._audio_snapshot = snapshot
        return len(snapshot)

    def restore_audio_inputs(self) -> None:
        snapshot = self._audio_snapshot
        self._audio_snapshot = None
        if snapshot is None:
            return
        errors: list[str] = []
        for source, muted in snapshot.items():
            try:
                self.client.set_input_mute(source, muted)
            except Exception as exc:
                errors.append(f"{source}: {exc}")
        if errors:
            raise RuntimeError("OBS 音频静音状态恢复失败：" + "; ".join(errors))

    def recording_status(self) -> dict[str, Any]:
        response = self.client.get_record_status()
        return {
            "active": bool(_value(response, "output_active", "outputActive")),
            "output_path": _value(response, "output_path", "outputPath"),
        }

    def start_recording(self, timeout_sec: float = 10.0) -> None:
        if self.recording_status()["active"]:
            raise RuntimeError("OBS 已经在录像，请先停止现有录像")
        self.client.start_record()
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if self.recording_status()["active"]:
                return
            time.sleep(0.1)
        raise TimeoutError("OBS 未在超时前进入录像状态")

    def stop_recording(self, timeout_sec: float = 30.0) -> str | None:
        status = self.recording_status()
        if not status["active"]:
            return status.get("output_path")
        response = self.client.stop_record()
        output_path = _value(response, "output_path", "outputPath")
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            current = self.recording_status()
            if not current["active"]:
                return output_path or current.get("output_path")
            time.sleep(0.15)
        raise TimeoutError("OBS 停止录像超时，尚未确认文件封装完成")

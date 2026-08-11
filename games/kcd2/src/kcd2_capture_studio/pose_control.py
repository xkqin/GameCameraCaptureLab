from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

from .backend import CameraBackend
from .models import Pose


def wrapped_degrees(value: float) -> float:
    return math.remainder(value, 360.0)


@dataclass(frozen=True)
class PoseTarget:
    x: float
    y: float
    z: float
    yaw_degrees: float
    pitch_degrees: float
    roll_degrees: float = 0.0
    fov_degrees: float = 63.0

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class PoseTolerance:
    position: float = 0.25
    yaw_degrees: float = 0.8
    pitch_degrees: float = 0.8
    roll_degrees: float = 0.8
    fov_degrees: float = 0.35


class PoseConvergenceError(RuntimeError):
    def __init__(self, message: str, report: dict[str, Any]) -> None:
        super().__init__(message)
        self.report = report


def pose_error(pose: Pose, target: PoseTarget) -> dict[str, float]:
    return {
        "x": pose.x - target.x,
        "y": pose.y - target.y,
        "z": pose.z - target.z,
        "position": math.dist(
            (pose.x, pose.y, pose.z),
            (target.x, target.y, target.z),
        ),
        "yaw_degrees": wrapped_degrees(pose.yaw_degrees - target.yaw_degrees),
        "pitch_degrees": wrapped_degrees(
            pose.pitch_degrees - target.pitch_degrees
        ),
        "roll_degrees": wrapped_degrees(pose.roll_degrees - target.roll_degrees),
        "fov_degrees": pose.fov_degrees - target.fov_degrees,
    }


def within_tolerance(
    error: dict[str, float],
    tolerance: PoseTolerance,
) -> bool:
    return (
        abs(error["position"]) <= tolerance.position
        and abs(error["yaw_degrees"]) <= tolerance.yaw_degrees
        and abs(error["pitch_degrees"]) <= tolerance.pitch_degrees
        and abs(error["roll_degrees"]) <= tolerance.roll_degrees
        and abs(error["fov_degrees"]) <= tolerance.fov_degrees
    )


class ClosedLoopPoseController:
    """Position the camera by absolute pose write with runtime readback."""

    def __init__(
        self,
        backend: CameraBackend,
        *,
        tolerance: PoseTolerance | None = None,
        max_export_corrections: int = 3,
        max_angle_corrections: int = 12,
    ) -> None:
        self.backend = backend
        self.tolerance = tolerance or PoseTolerance()
        self.max_export_corrections = max_export_corrections
        self.max_angle_corrections = max_angle_corrections
        self.session_active = False
        self._angle_rates: dict[str, float] = {}

    def move_to(
        self,
        target: PoseTarget,
        *,
        strict: bool = True,
    ) -> dict[str, Any]:
        attempts: list[dict[str, Any]] = []
        if self.session_active:
            attempts.append(self._adjust_export(target))
        else:
            attempts.append(self._start_export(target))
            self.session_active = True

        absolute_mode = (
            attempts[-1].get("control_mode") == "absolute_pose_write"
            and isinstance(attempts[-1].get("observed"), dict)
        )
        if absolute_mode:
            current = Pose.from_mapping(attempts[-1]["observed"])
            for _ in range(self.max_export_corrections):
                if within_tolerance(pose_error(current, target), self.tolerance):
                    break
                attempts.append(self._adjust_export(target))
                current = Pose.from_mapping(attempts[-1]["observed"])
            angle_attempts = {"pitch": [], "roll": []}
        else:
            for _ in range(self.max_export_corrections):
                current = self.backend.pose()
                error = pose_error(current, target)
                if (
                    error["position"] <= self.tolerance.position
                    and abs(error["yaw_degrees"]) <= self.tolerance.yaw_degrees
                    and abs(error["fov_degrees"]) <= self.tolerance.fov_degrees
                ):
                    break
                attempts.append(self._adjust_export(target))

            angle_attempts = {
                "pitch": self._converge_angle(
                    target_value=target.pitch_degrees,
                    field="pitch_degrees",
                    positive_action="rotate_up",
                    negative_action="rotate_down",
                    tolerance=self.tolerance.pitch_degrees,
                ),
                "roll": self._converge_angle(
                    target_value=target.roll_degrees,
                    field="roll_degrees",
                    positive_action="roll_right",
                    negative_action="roll_left",
                    tolerance=self.tolerance.roll_degrees,
                ),
            }

            # Keyboard pitch/roll can slightly perturb Euler yaw near singularities.
            final_before_yaw = self.backend.pose()
            final_before_yaw_error = pose_error(final_before_yaw, target)
            if (
                final_before_yaw_error["position"] > self.tolerance.position
                or abs(final_before_yaw_error["yaw_degrees"])
                > self.tolerance.yaw_degrees
                or abs(final_before_yaw_error["fov_degrees"])
                > self.tolerance.fov_degrees
            ):
                attempts.append(self._adjust_export(target))

        observed = self.backend.pose()
        error = pose_error(observed, target)
        reached = within_tolerance(error, self.tolerance)
        report = {
            "target": target.as_dict(),
            "observed": observed.as_dict(),
            "error": error,
            "reached": reached,
            "tolerance": asdict(self.tolerance),
            "export_attempts": attempts,
            "angle_attempts": angle_attempts,
            "control_mode": (
                "absolute_pose_write" if absolute_mode else "relative_exports"
            ),
            "session_active": self.session_active,
        }
        if strict and not reached:
            raise PoseConvergenceError(
                "Camera did not converge to the requested pose within tolerance",
                report,
            )
        return report

    def restore_start(self) -> dict[str, Any] | None:
        if not self.session_active:
            return None
        try:
            return self.backend.restore_export_session()
        finally:
            self.session_active = False

    def _start_export(self, target: PoseTarget) -> dict[str, Any]:
        return self.backend.start_export_pose(
            x=target.x,
            y=target.y,
            z=target.z,
            yaw_degrees=target.yaw_degrees,
            pitch_degrees=target.pitch_degrees,
            roll_degrees=target.roll_degrees,
            fov_degrees=target.fov_degrees,
        )

    def _adjust_export(self, target: PoseTarget) -> dict[str, Any]:
        return self.backend.adjust_active_export_pose(
            x=target.x,
            y=target.y,
            z=target.z,
            yaw_degrees=target.yaw_degrees,
            pitch_degrees=target.pitch_degrees,
            roll_degrees=target.roll_degrees,
            fov_degrees=target.fov_degrees,
        )

    def _converge_angle(
        self,
        *,
        target_value: float,
        field: str,
        positive_action: str,
        negative_action: str,
        tolerance: float,
    ) -> list[dict[str, Any]]:
        attempts: list[dict[str, Any]] = []
        for _ in range(self.max_angle_corrections):
            before = self.backend.pose()
            before_value = float(getattr(before, field))
            error = wrapped_degrees(target_value - before_value)
            if abs(error) <= tolerance:
                break

            rate = self._angle_rates.get(field)
            if rate is None or abs(rate) < 1.0e-5:
                action = positive_action
                duration_ms = 45
            else:
                required_positive_ms = error / rate
                action = (
                    positive_action
                    if required_positive_ms >= 0
                    else negative_action
                )
                duration_ms = max(
                    20,
                    min(180, int(round(abs(required_positive_ms)))),
                )

            self.backend.send_action(action, duration_ms)
            after = self.backend.pose()
            after_value = float(getattr(after, field))
            observed_delta = wrapped_degrees(after_value - before_value)
            signed_rate = observed_delta / duration_ms
            if action == negative_action:
                signed_rate = -signed_rate
            if abs(signed_rate) > 1.0e-5:
                previous_rate = self._angle_rates.get(field)
                self._angle_rates[field] = (
                    signed_rate
                    if previous_rate is None
                    else 0.55 * previous_rate + 0.45 * signed_rate
                )
            attempts.append(
                {
                    "field": field,
                    "before": before_value,
                    "target": target_value,
                    "error_before": error,
                    "action": action,
                    "duration_ms": duration_ms,
                    "observed_after": after_value,
                    "observed_delta": observed_delta,
                    "estimated_positive_rate_deg_ms": self._angle_rates.get(field),
                }
            )
            if abs(observed_delta) < 0.01:
                break
        return attempts

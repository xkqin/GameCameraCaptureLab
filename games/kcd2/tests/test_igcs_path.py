from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kcd2_capture_studio.igcs_path import IGCSCameraPathBuilder
from kcd2_capture_studio.models import TrajectoryKeyframe
from kcd2_capture_studio import igcs_path


class FakeBackend:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def send_action(self, action: str, duration_ms: int) -> None:
        self.actions.append(action)


class FakeController:
    def __init__(self) -> None:
        self.targets = []
        self.restored = False

    def move_to(self, target, strict=True):
        self.targets.append(target)
        return {
            "reached": True,
            "error": {"position": 0.0},
            "target": target.as_dict(),
        }

    def restore_start(self):
        self.restored = True
        return {"restored": True}


def frame(step: int, x: float) -> TrajectoryKeyframe:
    return TrajectoryKeyframe(
        step=step,
        time_sec=float(step),
        x=x,
        y=2,
        z=3,
        yaw_degrees=10 * step,
        pitch_degrees=5 * step,
        roll_degrees=0,
        fov_degrees=63,
    )


class IGCSPathTests(unittest.TestCase):
    def test_build_adds_path_and_nodes_then_restores(self) -> None:
        backend = FakeBackend()
        controller = FakeController()
        builder = IGCSCameraPathBuilder(backend, controller)
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(igcs_path, "TRAJECTORIES_DIR", Path(temp)):
                report = builder.build(
                    [frame(0, 1), frame(1, 2)],
                    trajectory_id="walk",
                )
                self.assertTrue(Path(report["report_path"]).exists())
        self.assertEqual(
            backend.actions,
            ["path_add", "path_add_node", "path_add_node"],
        )
        self.assertEqual(len(controller.targets), 2)
        self.assertTrue(controller.restored)
        self.assertEqual(report["status"], "completed")


if __name__ == "__main__":
    unittest.main()

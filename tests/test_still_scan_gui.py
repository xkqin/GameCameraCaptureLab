from __future__ import annotations

import unittest

from re9_pose_recorder.still_scan_gui import _trajectory_failure_needs_obs_restart


class TrajectoryRetryTests(unittest.TestCase):
    def test_lua_preflight_failure_does_not_restart_obs(self) -> None:
        error = RuntimeError("Lua did not acknowledge logging; OBS was not started.")

        self.assertFalse(_trajectory_failure_needs_obs_restart(error))

    def test_active_recording_failure_still_restarts_obs(self) -> None:
        error = RuntimeError("OBS did not stop recording cleanly.")

        self.assertTrue(_trajectory_failure_needs_obs_restart(error))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kcd2_capture_studio import settings


class SettingsTests(unittest.TestCase):
    def test_obs_password_is_not_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "settings.json"
            payload = settings.load_settings()
            payload["obs"]["password"] = "do-not-save"
            with patch.object(settings, "SETTINGS_PATH", target):
                settings.save_settings(payload)
            raw = json.loads(target.read_text(encoding="utf-8"))
            self.assertNotIn("password", raw["obs"])


if __name__ == "__main__":
    unittest.main()

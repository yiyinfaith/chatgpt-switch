import tempfile
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from switcher.server import Application
from switcher.config import ConfigError
from switcher.tray import Tray


class OpenConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="chatgpt-switch-open-config-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "portable"
        self.root.mkdir()
        self.config_dir = self.root / "codex"
        self.app = Application(self.root, self.config_dir, test_mode=True)

    def test_open_config_file_uses_bound_config_path_and_creates_missing_file(self):
        with patch("switcher.server.open_local") as open_local:
            self.app.test_mode = False
            result = self.app.action("/api/open", {"target": "config-file"})

        self.assertEqual(Path(result["path"]), self.config_dir / "config.toml")
        self.assertTrue(self.config_dir.joinpath("config.toml").is_file())
        open_local.assert_called_once_with(self.config_dir / "config.toml")

    def test_open_config_file_does_not_replace_existing_content(self):
        self.config_dir.mkdir()
        config = self.config_dir / "config.toml"
        config.write_text('model = "keep"\n', encoding="utf-8")
        os.utime(config, ns=(1500000000000000000, 1500000000000000000))
        modified = config.stat().st_mtime_ns

        with patch("switcher.server.open_local") as open_local:
            self.app.test_mode = False
            result = self.app.action("/api/open", {"target": "config-file"})

        self.assertEqual(config.read_text(encoding="utf-8"), 'model = "keep"\n')
        self.assertEqual(config.stat().st_mtime_ns, modified)
        self.assertEqual(Path(result["path"]), config)
        open_local.assert_called_once_with(config)

    def test_existing_config_folder_target_still_opens_folder(self):
        with patch("switcher.server.open_local") as open_local:
            self.app.test_mode = False
            result = self.app.action("/api/open", {"target": "config"})

        self.assertEqual(Path(result["path"]), self.config_dir)
        self.assertTrue(self.config_dir.is_dir())
        open_local.assert_called_once_with(self.config_dir)

    def test_tray_action_opens_the_same_file_through_the_real_application(self):
        tray = Tray(self.app, "http://localhost", self.root / "icon.png")
        with patch("switcher.server.open_local") as open_local:
            self.app.test_mode = False
            tray.open_config()
        open_local.assert_called_once_with(self.app.config.path)
        self.assertEqual(self.app.integration.restarts, 0)

    def test_default_application_failure_returns_a_useful_error(self):
        self.app.test_mode = False
        with patch("switcher.server.open_local", side_effect=OSError("No association")):
            with self.assertRaisesRegex(ConfigError, "系统默认应用"):
                self.app.open_config_file()

    def test_directory_cannot_be_opened_as_a_config_file(self):
        self.app.config.path.mkdir(parents=True)
        with patch("switcher.server.open_local") as open_local:
            with self.assertRaisesRegex(ConfigError, "不是文件"):
                self.app.open_config_file()
        open_local.assert_not_called()

    def test_test_mode_does_not_launch_an_external_application(self):
        with patch("switcher.server.open_local") as open_local:
            self.app.action("/api/open", {"target": "config-file"})
        open_local.assert_not_called()


if __name__ == "__main__":
    unittest.main()

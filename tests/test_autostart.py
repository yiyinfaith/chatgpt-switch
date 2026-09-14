import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from switcher.autostart import Autostart
from switcher.config import ConfigError
from switcher.settings import SettingsStore


class StartupSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="chatgpt-switch-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "中文 Portable"
        self.root.mkdir()
        self.startup = Autostart(self.root, test_mode=True)
        self.store = SettingsStore(self.root / "data/settings.json", self.startup)

    def test_read_is_side_effect_free_and_first_launch_registers(self):
        self.assertTrue(self.store.public()["autoStart"])
        self.assertTrue(self.store.public()["autoStartBackground"])
        self.assertFalse(self.store.path.exists())
        self.assertIsNone(self.startup.snapshot())
        self.store.initialize()
        self.assertTrue(json.loads(self.store.path.read_text())["autoStart"])
        self.assertIn("main.py", self.startup.snapshot().decode())
        self.assertTrue(self.startup.snapshot().decode().endswith(" --autostart"))

    def test_old_settings_migrate_without_losing_preferences(self):
        self.store.path.parent.mkdir()
        self.store.path.write_text('{"closeBehavior":"exit", "futureField":123}')
        self.store.initialize()
        value = json.loads(self.store.path.read_text())
        self.assertEqual(value, {"closeBehavior": "exit", "futureField": 123, "autoStart": True, "autoStartBackground": True})

    def test_disable_stays_disabled_across_launches_and_old_clients(self):
        self.store.initialize()
        self.store.save({"autoStart": False})
        self.assertIsNone(self.startup.snapshot())
        self.store.initialize()
        self.store.save({"closeBehavior": "exit"})
        self.assertFalse(self.store.public()["autoStart"])
        self.assertIsNone(self.startup.snapshot())
        self.store.save({"autoStart": True})
        self.assertIsNotNone(self.startup.snapshot())

    def test_failed_settings_write_restores_registration_and_preferences(self):
        self.store.initialize()
        before, registration = self.store.path.read_bytes(), self.startup.snapshot()
        with patch("switcher.settings.atomic_write", side_effect=OSError("read only")):
            with self.assertRaises(ConfigError):
                self.store.save({"autoStart": False, "closeBehavior": "exit"})
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.startup.snapshot(), registration)

    def test_registration_failure_does_not_save_settings(self):
        with patch.object(self.startup, "apply", side_effect=PermissionError("denied")):
            with self.assertRaises(ConfigError):
                self.store.initialize()
        self.assertFalse(self.store.path.exists())

    def test_invalid_input_and_damaged_file_never_register(self):
        for value in ("false", 1, None):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                self.store.save({"autoStart": value})
        self.store.path.parent.mkdir()
        self.store.path.write_text("broken")
        with self.assertRaises(ConfigError):
            self.store.initialize()
        self.assertEqual(self.store.path.read_text(), "broken")
        self.assertIsNone(self.startup.snapshot())

    def test_application_route_preserves_switch_configuration(self):
        from switcher.server import Application
        cfg = self.root / "fixture-config"
        cfg.mkdir()
        config = cfg / "config.toml"
        config.write_text('model="keep"\n')
        app = Application(self.root, cfg, test_mode=True)
        app.initialize_settings()
        result = app.action("/api/settings", {"closeBehavior": "exit", "autoStart": False, "autoStartBackground": False})
        self.assertEqual(result["settings"], {"closeBehavior": "exit", "autoStart": False, "autoStartBackground": False})
        self.assertEqual(app.state()["settings"], result["settings"])
        self.assertEqual(config.read_text(), 'model="keep"\n')
        self.assertFalse((cfg / ".env").exists())
        self.assertEqual(app.integration.restarts, 0)

    @unittest.skipUnless(os.name == "nt", "Windows registry")
    def test_real_registry_registration_quoted_move_disable_and_restore(self):
        import winreg
        startup = Autostart(self.root)
        # Exercise real winreg APIs under an isolated non-startup test key.
        startup.RUN_KEY = "Software\\ChatGPT-Switch-Test-" + uuid4().hex
        executable = self.root / "ChatGPT Switch.exe"
        executable.touch()
        try:
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(executable)):
                startup.apply(True)
                self.assertEqual(startup.snapshot(), ('"' + str(executable) + '" --autostart', winreg.REG_SZ))
                previous = startup.snapshot()
                startup.apply(False)
                self.assertIsNone(startup.snapshot())
                startup.restore(previous)
                moved = self.root / "Moved Folder/ChatGPT Switch.exe"
                with patch.object(sys, "executable", str(moved)):
                    startup.apply(True)
                    self.assertEqual(startup.snapshot()[0], '"' + str(moved) + '" --autostart')
        finally:
            startup.restore(None)
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, startup.RUN_KEY)

    def test_mac_launchagent_and_linux_desktop_have_absolute_commands(self):
        import plistlib
        self.startup.test_mode = False
        self.startup.system = "Darwin"
        value = plistlib.loads(self.startup.payload())
        self.assertTrue(value["RunAtLoad"])
        self.assertEqual(value["ProgramArguments"][-2:], [str(self.root / "main.py"), "--autostart"])
        self.startup.system = "Linux"
        value = self.startup.payload().decode()
        self.assertIn("Type=Application\n", value)
        self.assertIn("Terminal=false\n", value)
        self.assertIn('"--autostart"', value)
        with patch.object(self.startup, "command", return_value=['/a % $ ` " \\ b/run']):
            self.assertIn("%%", self.startup.payload().decode())

    def test_background_choice_survives_disabled_startup_and_old_clients(self):
        self.store.initialize()
        self.store.save({"autoStartBackground": False})
        self.store.save({"autoStart": False})
        self.store.initialize()
        self.store.save({"closeBehavior": "exit"})
        self.store.save({"autoStart": True})
        self.assertFalse(self.store.public()["autoStartBackground"])

    def test_background_validation_and_rollback(self):
        self.store.initialize()
        before = self.store.path.read_bytes()
        for value in ("false", 1, None):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                self.store.save({"autoStartBackground": value})
        with patch("switcher.settings.atomic_write", side_effect=OSError("read only")):
            with self.assertRaises(ConfigError):
                self.store.save({"autoStartBackground": False})
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_existing_startup_setting_migrates_and_refreshes_old_command(self):
        self.store.path.parent.mkdir()
        self.store.path.write_text('{"closeBehavior":"exit","autoStart":false}')
        self.store.initialize()
        self.assertEqual(self.store.public(), {"closeBehavior": "exit", "autoStart": False, "autoStartBackground": True})
        self.assertIsNone(self.startup.snapshot())
        self.store.save({"autoStart": True})
        self.startup.restore(b'"old.exe"')
        self.store.initialize()
        self.assertTrue(self.startup.snapshot().decode().endswith(" --autostart"))


if __name__ == "__main__":
    unittest.main()

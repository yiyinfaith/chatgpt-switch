import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main
from switcher.window import WindowController


class StartupWindowTests(unittest.TestCase):
    def test_login_uses_saved_preference_but_manual_launch_always_shows(self):
        for login in (False, True):
            for background in (False, True):
                with self.subTest(login=login, background=background), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    (root / "data").mkdir()
                    (root / "data/settings.json").write_text(json.dumps({
                        "closeBehavior": "exit", "autoStart": True, "autoStartBackground": background}))
                    argv = ["main.py", "--test-root", directory] + (["--autostart"] if login else [])
                    with patch.object(sys, "argv", argv), patch.object(main, "serve_webview") as serve:
                        main.main()
                    self.assertEqual(serve.call_args.kwargs["start_hidden"], login and background)

    def test_diagnose_does_not_register_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            argv = ["main.py", "--test-root", directory, "--autostart", "--diagnose", str(root / "result.json")]
            with patch.object(sys, "argv", argv), patch.object(main, "serve_webview") as serve:
                main.main()
            serve.assert_not_called()
            self.assertFalse((root / "data/test-autostart.txt").exists())
            self.assertFalse((root / "data/settings.json").exists())

    @unittest.skipUnless(os.name == "nt", "Windows tray initialization")
    def test_failed_tray_startup_leaves_an_accessible_window(self):
        for failure in (None, RuntimeError("tray init failed")):
            with self.subTest(failure=failure):
                app = SimpleNamespace(tray_status="unavailable", warning="")
                window = SimpleNamespace(hidden=True)
                controller = WindowController(app, window, start_hidden=True)
                with patch.object(controller, "_initialize_tray", side_effect=failure):
                    controller.initialize()
                self.assertFalse(window.hidden)
                self.assertFalse(controller.hidden)

    @unittest.skipUnless(os.name == "nt", "Windows tray initialization")
    def test_ready_tray_keeps_login_window_hidden(self):
        app = SimpleNamespace(tray_status="ready", warning="")
        window = SimpleNamespace(hidden=True)
        controller = WindowController(app, window, start_hidden=True)
        controller._frame = Mock()
        with patch.object(controller, "_initialize_tray"):
            controller.initialize()
        self.assertTrue(window.hidden)
        self.assertTrue(controller.hidden)


if __name__ == "__main__":
    unittest.main()

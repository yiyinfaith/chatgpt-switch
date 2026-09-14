import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from switcher.tray import Tray


class _MenuItem:
    def __init__(self, text, action, **kwargs):
        self.text = text
        self.action = action
        self.kwargs = kwargs


class _Menu:
    SEPARATOR = object()

    def __call__(self, *entries):
        return entries


class TrayMenuTests(unittest.TestCase):
    def setUp(self):
        self.app = SimpleNamespace(
            integration=SimpleNamespace(info={"system": "Linux"}),
            profiles=SimpleNamespace(public=lambda: []),
            job={"busy": False},
            open_panel=Mock(),
            open_config_file=Mock(),
        )
        self.tray = Tray(self.app, "http://localhost", "icon.png")
        self.tray.pystray = SimpleNamespace(MenuItem=_MenuItem, Menu=_Menu())

    def test_menu_exposes_config_file_action(self):
        entries = self.tray.menu()
        config = next(entry for entry in entries if isinstance(entry, _MenuItem) and entry.text == "打开 config.toml")
        config.action(None, None)
        self.app.open_config_file.assert_called_once_with()

    def test_open_config_delegates_to_application(self):
        self.tray.open_config("icon", "item")
        self.app.open_config_file.assert_called_once_with()

    def test_config_menu_reports_open_error_without_escaping_callback(self):
        self.app.open_config_file.side_effect = OSError("No default application for .toml")
        self.tray.icon = Mock()
        config = next(
            entry for entry in self.tray.menu()
            if isinstance(entry, _MenuItem) and entry.text == "打开 config.toml"
        )

        config.action(None, None)

        self.app.open_config_file.assert_called_once_with()
        self.tray.icon.notify.assert_called_once_with(
            "No default application for .toml", "ChatGPT Switch"
        )


if __name__ == "__main__":
    unittest.main()

"""Current-user login startup registration; no administrator privileges needed."""
from __future__ import annotations

import os
import platform
import plistlib
import subprocess
import sys
from pathlib import Path

from .config import ConfigError, atomic_write


class Autostart:
    RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    VALUE_NAME = "ChatGPT Switch"

    def __init__(self, app_root, test_mode=False):
        self.root = Path(app_root).resolve()
        self.system = platform.system()
        self.test_mode = test_mode
        self.supported = test_mode or self.system in {"Windows", "Darwin", "Linux"}

    def command(self):
        if getattr(sys, "frozen", False):
            return [str(Path(sys.executable).resolve()), "--autostart"]
        if self.system == "Windows" and (self.root / "ChatGPT Switch.exe").is_file():
            return [str(self.root / "ChatGPT Switch.exe"), "--autostart"]
        executable = Path(sys.executable).resolve()
        if self.system == "Windows" and executable.with_name("pythonw.exe").is_file():
            executable = executable.with_name("pythonw.exe")
        return [str(executable), str(self.root / "main.py"), "--autostart"]

    def path(self):
        if self.test_mode:
            return self.root / "data" / "test-autostart.txt"
        if self.system == "Darwin":
            return Path.home() / "Library/LaunchAgents/com.chatgpt-switch.autostart.plist"
        directory = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        return directory / "autostart/chatgpt-switch.desktop"

    def payload(self):
        command = self.command()
        if self.test_mode or self.system == "Windows":
            # The executable path must be quoted even when started by Explorer.
            return '"' + command[0] + '"' + (" " + subprocess.list2cmdline(command[1:]) if len(command) > 1 else "")
        if self.system == "Darwin":
            return plistlib.dumps({"Label": "com.chatgpt-switch.autostart", "ProgramArguments": command,
                                  "WorkingDirectory": str(self.root), "RunAtLoad": True})
        def quote(value):
            # Desktop Entry Exec quoting, including its literal-percent escape.
            value = value.replace("%", "%%")
            for character in ("\\", '"', "`", "$"):
                value = value.replace(character, "\\" + character)
            return '"' + value + '"'
        execution = " ".join(quote(arg) for arg in command).replace("\\", "\\\\")
        return ("[Desktop Entry]\nType=Application\nName=ChatGPT Switch\nExec=" + execution +
                "\nTerminal=false\nX-GNOME-Autostart-enabled=true\n").encode("utf-8")

    def snapshot(self):
        if self.system == "Windows" and not self.test_mode:
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY) as key:
                    return winreg.QueryValueEx(key, self.VALUE_NAME)
            except FileNotFoundError:
                return None
        path = self.path()
        return path.read_bytes() if path.exists() else None

    def restore(self, previous):
        if self.system == "Windows" and not self.test_mode:
            import winreg
            if previous is None:
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                        winreg.DeleteValue(key, self.VALUE_NAME)
                except FileNotFoundError:
                    pass
            else:
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY) as key:
                    winreg.SetValueEx(key, self.VALUE_NAME, 0, previous[1], previous[0])
        elif previous is None:
            self.path().unlink(missing_ok=True)
        else:
            atomic_write(self.path(), previous)

    def apply(self, enabled):
        if not self.supported:
            if enabled:
                raise ConfigError("当前系统暂不支持开机自启。")
            return
        payload = self.payload()
        if self.system == "Windows" and not self.test_mode:
            import winreg
            desired = (payload, winreg.REG_SZ) if enabled else None
        else:
            desired = (payload.encode("utf-8") if isinstance(payload, str) else payload) if enabled else None
        if self.snapshot() != desired:
            self.restore(desired)

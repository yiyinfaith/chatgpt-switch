"""Persist UI preferences together with the OS startup registration."""
import json
import threading

from .config import ConfigError, atomic_write


class SettingsStore:
    def __init__(self, path, autostart):
        self.path = path
        self.autostart = autostart
        self.lock = threading.RLock()

    def read(self):
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text("utf-8"))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (OSError, ValueError) as exc:
            raise ConfigError("设置文件无法读取，请检查 data/settings.json；未修改开机自启。") from exc

    def public(self):
        with self.lock:
            try:
                value = self.read()
            except ConfigError:
                return {"closeBehavior": "background", "autoStart": False, "autoStartBackground": True}
            behavior = value.get("closeBehavior", "background")
            enabled = value.get("autoStart", self.autostart.supported)
            background = value.get("autoStartBackground", True)
            return {"closeBehavior": behavior if behavior in {"exit", "background"} else "background",
                    "autoStart": enabled if type(enabled) is bool else False,
                    "autoStartBackground": background if type(background) is bool else True}

    def initialize(self):
        # Called only by a normal launch, never by --diagnose or a state read.
        with self.lock:
            self.read()
            self.save(self.public())

    def save(self, fields):
        with self.lock:
            previous = self.read()
            settings = {**previous, **self.public(), **fields}
            if settings.get("closeBehavior") not in {"exit", "background"}:
                raise ConfigError("请选择直接退出或在后台运行。")
            if type(settings.get("autoStart")) is not bool:
                raise ConfigError("开机自启必须选择开启或关闭。")
            if type(settings.get("autoStartBackground")) is not bool:
                raise ConfigError("开机后仅后台运行必须选择开启或关闭。")
            try:
                registration = self.autostart.snapshot()
            except OSError as exc:
                raise ConfigError("无法读取开机自启设置，请检查当前用户权限。") from exc
            try:
                self.autostart.apply(settings["autoStart"])
                if settings != previous:
                    atomic_write(self.path, json.dumps(settings, ensure_ascii=False, indent=2).encode("utf-8"))
            except Exception as exc:
                try:
                    self.autostart.restore(registration)
                except OSError:
                    raise ConfigError("设置保存失败，开机自启恢复未完成，请重新保存设置。") from exc
                raise ConfigError("设置未保存，开机自启已恢复，请检查当前用户权限和软件目录。") from exc
            return self.public()

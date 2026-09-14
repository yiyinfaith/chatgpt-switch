from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo


WEST_COAST_ZONE = ZoneInfo("America/Los_Angeles")


def west_coast_time():
    return datetime.now(WEST_COAST_ZONE).strftime("%Y-%m-%d %H:%M:%S")


class Tray:
    def __init__(self, app, url, icon_path):
        self.app, self.url, self.icon_path = app, url, icon_path
        self.icon = None
        self.ready = False
        self.error = ""
        self._clock_stop = threading.Event()
        self._clock_thread = None

    def _start_clock(self):
        if self._clock_thread and self._clock_thread.is_alive():
            return
        self._clock_stop.clear()
        self._clock_thread = threading.Thread(target=self._clock_loop, daemon=True)
        self._clock_thread.start()

    def _clock_loop(self):
        while not self._clock_stop.wait(1):
            if self.ready:
                self.refresh()

    def start(self):
        try:
            import pystray
            from PIL import Image
            self.pystray = pystray
            self.icon = pystray.Icon("chatgpt-switch", Image.open(self.icon_path), "ChatGPT Switch", self.menu())
            def setup(icon):
                try:
                    icon.visible = True
                    self.ready = True
                    self.app.tray_status = "ready"
                except Exception:
                    self.app.tray_status = "unavailable"
            def run():
                try:
                    self.icon.run(setup=setup)
                except Exception:
                    self.app.tray_status = "unavailable"
            # Cocoa's event loop must run on the main thread; serve() delegates the
            # HTTP server to a worker and calls run_main() on macOS.
            if self.app.integration.info["system"] != "Darwin":
                threading.Thread(target=run, daemon=True).start()
            elif getattr(self.app, "native_mode", False):
                # The Cocoa WebView already owns the main event loop.
                self.icon.run_detached(setup=setup)
            self._start_clock()
            return True
        except Exception:
            self.app.tray_status = "unavailable"
            return False

    def run_main(self):
        def setup(icon):
            icon.visible = True
            self.ready = True
            self.app.tray_status = "ready"
        self.icon.run(setup=setup)

    def open(self, icon=None, item=None):
        self.app.open_panel("main")

    def settings(self, icon=None, item=None):
        self.app.open_panel("settings")

    def open_config(self, icon=None, item=None):
        """Open the active config.toml with the system's default application."""
        try:
            self.app.open_config_file()
        except Exception as exc:
            self.notify(str(exc))

    def thirdparty(self, icon=None, item=None):
        self.switch_mode("thirdparty")

    def account(self, icon, item):
        self.switch_mode("account")

    def switch_mode(self, mode):
        from .config import SetupRequired
        try:
            self.app.config.plan(mode)
            self.dispatch("switch", lambda: self.app.switch(mode))
        except SetupRequired:
            self.app.open_panel("api")
            self.notify("请先新增并应用一套 API 配置。")
        except Exception as exc:
            self.notify(str(exc))

    def dispatch(self, kind, operation):
        try:
            self.app.start_job(kind, operation)
        except Exception as exc:
            self.notify(str(exc))

    def apply(self, profile_id):
        return lambda icon, item: self.dispatch("profile", lambda: self.app.apply_profile(profile_id))

    def checked_account(self, item):
        try:
            return self.app.config.inspect()["mode"] == "account"
        except Exception:
            return False

    def checked_profile(self, profile_id):
        return lambda item: self.app.active_profile() == profile_id

    def checked_thirdparty(self, item):
        try:
            return self.app.config.inspect()["mode"] == "thirdparty"
        except Exception:
            return False

    def menu(self):
        item, menu = self.pystray.MenuItem, self.pystray.Menu
        entries = [item("美西时间 · " + west_coast_time(), None, enabled=False), menu.SEPARATOR,
                   item("使用账号额度", self.account, checked=self.checked_account, radio=True, enabled=lambda _: not self.app.job["busy"]),
                   item("使用第三方api", self.thirdparty, checked=self.checked_thirdparty, radio=True, enabled=lambda _: not self.app.job["busy"])]
        for profile in self.app.profiles.public():
            entries.append(item(profile["name"], self.apply(profile["id"]), checked=self.checked_profile(profile["id"]), radio=True, enabled=lambda _: not self.app.job["busy"]))
        if not self.app.profiles.public():
            entries.append(item("添加 API 配置…", self.open))
        entries += [menu.SEPARATOR, item("打开设置", self.settings), item("打开主面板", self.open, default=True),
                    item("打开 config.toml", self.open_config),
                    menu.SEPARATOR, item("退出程序", self.quit, enabled=lambda _: not self.app.job["busy"])]
        return menu(*entries)

    def refresh(self):
        if self.icon:
            try:
                self.icon.menu = self.menu()
                self.icon.update_menu()
                self.icon.title = "ChatGPT Switch · 美西 " + west_coast_time()
            except Exception:
                pass

    def notify(self, text):
        if self.icon:
            try:
                self.icon.notify(text[:220], "ChatGPT Switch")
            except Exception:
                pass

    def quit(self, icon=None, item=None):
        if not self.app.job["busy"]:
            self.app.quit()

    def stop(self):
        self._clock_stop.set()
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass


class WindowsTray(Tray):
    """Uses the WebView's WinForms event loop, including while its form is hidden."""
    def __init__(self, app, controller, assembly, icon_path):
        super().__init__(app, app.session_url, icon_path)
        self.controller = controller
        self.assembly = assembly
        self.native = None

    def start(self):
        try:
            from System import Action, Activator, Array, Object, String
            self.callback = Action[String](self._dispatch_native)
            self.native = Activator.CreateInstance(self.assembly.GetType('SwitchWindow.NativeTray'),
                Array[Object]([self.controller.window.native, str(self.icon_path), self.callback]))
            self.ready = True
            self.app.tray_status = "ready"
            self._start_clock()
            self.refresh()
            return True
        except Exception as exc:
            self.error = str(exc)
            self.app.tray_status = "unavailable"
            self.ready = False
            return False

    def _dispatch_native(self, action):
        native_action = str(action)
        def run():
            try:
                handlers = {"main": self.open, "settings": self.settings, "config": self.open_config,
                            "account": lambda: self.account(None, None),
                            "thirdparty": self.thirdparty, "quit": self.quit, "refresh": self.refresh}
                if native_action.startswith("profile:"):
                    self.apply(native_action[8:])(None, None)
                else:
                    handlers[native_action]()
            except Exception as exc:
                self.notify(str(exc))
        threading.Thread(target=run, daemon=True).start()

    def refresh(self):
        if self.native is not None and self.ready:
            try:
                mode = self.app.config.inspect()["mode"]
            except Exception:
                mode = "setup"
            try:
                profiles = self.app.profiles.public()
                try:
                    active_profile = self.app.active_profile()
                except Exception:
                    active_profile = None
                encoded = "|".join(("*" if p.get("id") == active_profile else "") + quote(str(p.get("id", "")), safe="") + "~" + quote(str(p.get("name", "")), safe="") for p in profiles)
                self.controller._on_ui(lambda: self.native.Update(mode, bool(self.app.job["busy"]), west_coast_time(), encoded))
            except Exception:
                pass

    def notify(self, text):
        if self.native is not None and self.ready and not self.app.test_mode:
            try:
                self.controller._on_ui(lambda: self.native.Notify(text[:220]))
            except Exception:
                pass

    def stop(self):
        if self.native is not None and self.ready:
            self.ready = False
            try:
                self.controller._on_ui(self.native.Dispose)
            except Exception:
                pass

    def diagnostics(self):
        if not self.ready:
            return {"visible": False, "error": self.error}
        value = {}
        def inspect():
            value.update(visible=bool(self.native.IconVisible), notificationHandle=int(self.native.NotificationHandle),
                         menuVisible=bool(self.native.MenuVisible), menuHandle=int(self.native.MenuHandle),
                         labels=str(self.native.MenuItems).split("|"),
                         profiles=str(self.native.ProfileItems).split("|") if str(self.native.ProfileItems) else [])
            if self.native.MenuVisible:
                value["insets"] = list(self.native.MenuInsets)
                value["items"] = {action: list(self.native.ItemBounds(action)) for action in ("account", "thirdparty", "settings", "main", "config", "quit")}
        self.controller._on_ui(inspect)
        return value

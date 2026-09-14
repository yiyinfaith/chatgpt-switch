"""Native window controls for the existing WebView UI (no page layout changes)."""
from __future__ import annotations

import ctypes
import os
import json
import threading
import time
from ctypes import wintypes
from pathlib import Path

from .config import ConfigError


class WindowController:
    def __init__(self, app, window, start_hidden=False):
        self.app = app
        self.window = window
        self.fullscreen = False
        self.maximized = False
        self.minimized = False
        self.hidden = start_hidden
        self.exiting = False
        self.pending_page = None
        self._restore_bounds = None
        self._restore_maximized = False

    def initialize(self):
        # before_show runs on the WinForms UI thread, after its HWND exists.
        try:
            self._initialize_tray()
        except Exception as exc:
            self.app.tray_status = "unavailable"
            self.app.warning = "窗口或托盘初始化失败：" + str(exc)
        if self.hidden:
            if os.name == "nt":
                # pywebview briefly initializes a transparent form even when
                # hidden=True. Keep that form out of the taskbar as well.
                if self.app.tray_status == "ready":
                    self._frame.SetTaskbarVisible(False)
                else:
                    self.hidden = self.window.hidden = False
            else:
                threading.Thread(target=self._check_startup_tray, daemon=True).start()

    def _check_startup_tray(self):
        deadline = time.monotonic() + 5
        while self.app.tray_status == "starting" and time.monotonic() < deadline:
            time.sleep(0.05)
        if self.hidden and self.app.tray_status != "ready":
            self.command("show")

    def _initialize_tray(self):
        if os.name != "nt":
            from .tray import Tray
            self.app.tray = Tray(self.app, self.app.session_url, self._icon_path())
            self.app.tray.start()
            return
        import System.Windows.Forms as Forms
        form = self.window.native
        form.MaximizedBounds = Forms.Screen.FromControl(form).WorkingArea
        import clr
        clr.AddReference('Microsoft.CSharp')
        from Microsoft.CSharp import CSharpCodeProvider
        from System.CodeDom.Compiler import CompilerParameters
        from System import Array, Object
        options = CompilerParameters()
        options.GenerateInMemory = True
        for assembly in ('System.dll', 'System.Windows.Forms.dll', 'System.Drawing.dll'):
            options.ReferencedAssemblies.Add(assembly)
        provider = CSharpCodeProvider()
        from System import String
        sources = [Path(__file__).with_name(name).read_text('utf-8') for name in ('native_frame.cs', 'native_tray.cs')]
        result = provider.CompileAssemblyFromSource(options, Array[String](sources))
        provider.Dispose()
        if result.Errors.HasErrors:
            raise RuntimeError('Cannot initialize window resizing: ' + str(result.Errors[0]))
        from System import Activator
        self._frame = Activator.CreateInstance(result.CompiledAssembly.GetType('SwitchWindow.Frame'), Array[Object]([form]))
        from .tray import WindowsTray
        self.app.tray = WindowsTray(self.app, self, result.CompiledAssembly, self._icon_path())
        self.app.tray.start()

    def _icon_path(self):
        import sys
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        return root / "ui/icon.png"

    def on_loaded(self):
        self.window.events.loaded.wait(1)
        if self.pending_page:
            page, self.pending_page = self.pending_page, None
            self._navigate(page)

    def _navigate(self, page):
        if self.window.events.loaded.is_set():
            self.window.run_js("window.dispatchEvent(new CustomEvent('app-navigation',{detail:" + json.dumps(page) + "}));")
        else:
            self.pending_page = page

    def show_panel(self, page="main"):
        if page not in {"main", "settings", "api"}:
            raise ConfigError("未知面板。")
        self.command("show")
        self._navigate(page)
        return self.state()

    def _on_ui(self, operation):
        from System import Action
        form = self.window.native
        if form.InvokeRequired:
            form.Invoke(Action(operation))
        else:
            operation()

    def state(self):
        return {"maximized": self.maximized, "fullscreen": self.fullscreen, "hidden": self.hidden, "exiting": self.exiting}

    def _publish(self):
        if self.window.events.loaded.is_set():
            import json
            self.window.run_js("window.dispatchEvent(new CustomEvent('native-window-state', {detail:" + json.dumps(self.state()) + "}));")

    def on_maximized(self):
        self.maximized = True
        self._publish()

    def on_restored(self):
        self.maximized = False
        self.minimized = False
        self._publish()

    def on_minimized(self):
        self.minimized = True

    def allow_close(self):
        # pywebview cancels closing only when an event handler returns False.
        if self.app.job["busy"]:
            return False
        if not self.exiting and self.app.settings()["closeBehavior"] == "background":
            if self.app.tray_status != "ready":
                threading.Thread(target=lambda: self._navigate("settings"), daemon=True).start()
                return False
            # FormClosing runs synchronously on the GUI thread. WebView JS waits
            # on that same thread, so hide/publish only after this veto returns.
            threading.Thread(target=self._hide, daemon=True).start()
            return False
        if self.app.tray:
            self.app.tray.stop()
        return True

    def _hide(self):
        self.window.hide()
        self.hidden = True
        self._publish()

    def _gesture(self, edge=None):
        if os.name != "nt":
            raise ConfigError("当前系统不支持此原生窗口手势。")
        if self.fullscreen or (edge and self.maximized):
            return
        edges = {"w": 10, "e": 11, "n": 12, "nw": 13, "ne": 14,
                 "s": 15, "sw": 16, "se": 17}
        if edge is not None and edge not in edges:
            raise ConfigError("未知缩放方向。")

        def gesture():
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user32.SendMessageW.restype = ctypes.c_ssize_t
            user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
            user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
            user32.GetAsyncKeyState.restype = ctypes.c_short
            # Ignore delayed requests after the mouse has already been released.
            if not user32.GetAsyncKeyState(1) & 0x8000:
                return
            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            user32.ReleaseCapture()
            coords = (point.x & 0xffff) | ((point.y & 0xffff) << 16)
            user32.SendMessageW(int(self.window.native.Handle.ToInt64()), 0x00A1,
                                edges[edge] if edge else 2, coords)
        # WinForms invokes on the owning UI thread. SendMessage enters the native
        # move/size loop, including capture and minimum-size constraints.
        self._on_ui(gesture)

    def _toggle_fullscreen(self):
        if os.name != "nt":
            self.window.toggle_fullscreen()
            self.fullscreen = not self.fullscreen
            return

        def toggle():
            import System.Windows.Forms as Forms
            form = self.window.native
            if not self.fullscreen:
                self._restore_maximized = form.WindowState == Forms.FormWindowState.Maximized
                self._restore_bounds = form.RestoreBounds if self._restore_maximized else form.Bounds
                screen = Forms.Screen.FromControl(form)
                form.WindowState = Forms.FormWindowState.Normal
                form.MaximizedBounds = screen.Bounds
                form.Bounds = screen.Bounds
                self.fullscreen = True
            else:
                self.fullscreen = False
                form.WindowState = Forms.FormWindowState.Normal
                form.Bounds = self._restore_bounds
                form.MaximizedBounds = Forms.Screen.FromControl(form).WorkingArea
                if self._restore_maximized:
                    form.WindowState = Forms.FormWindowState.Maximized
            self.maximized = form.WindowState == Forms.FormWindowState.Maximized
        self._on_ui(toggle)

    def command(self, action, edge=None):
        if action in {"close", "exit"}:
            if self.app.job["busy"]:
                raise ConfigError("请等待操作完成后再退出。")
            if action == "close" and self.app.settings()["closeBehavior"] == "background":
                if self.app.tray_status != "ready":
                    raise ConfigError("托盘尚未就绪，暂时无法在后台运行。请稍后重试，或在设置中选择直接退出。")
                self._hide()
            else:
                self.exiting = True
                if self.app.tray:
                    self.app.tray.stop()
                # Let the HTTP response reach the UI before its server shuts down.
                threading.Timer(0.1, self.window.destroy).start()
        elif action == "minimize":
            self.window.minimize()
        elif action == "maximize":
            if self.fullscreen:
                self._toggle_fullscreen()
            if self.maximized:
                self.window.restore()
                self.maximized = False
            else:
                if os.name == "nt":
                    def work_area():
                        import System.Windows.Forms as Forms
                        self.window.native.MaximizedBounds = Forms.Screen.FromControl(self.window.native).WorkingArea
                    self._on_ui(work_area)
                self.window.maximize()
                self.maximized = True
        elif action == "fullscreen":
            self._toggle_fullscreen()
        elif action == "exit-fullscreen":
            if self.fullscreen:
                self._toggle_fullscreen()
        elif action == "drag":
            self._gesture()
        elif action == "resize":
            self._gesture(edge)
        elif action == "show":
            if os.name == "nt" and hasattr(self, "_frame"):
                self._on_ui(lambda: self._frame.SetTaskbarVisible(True))
            if self.minimized:
                self.window.restore()
            self.window.show()
            self.hidden = False
            self.minimized = False
            if os.name == "nt":
                def activate():
                    self.window.native.Activate()
                    self.window.native.BringToFront()
                self._on_ui(activate)
        else:
            raise ConfigError("未知窗口操作。")
        if action not in {"close", "drag", "resize"}:
            self._publish()
        return self.state()

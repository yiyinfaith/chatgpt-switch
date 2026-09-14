from __future__ import annotations

import json
import mimetypes
import os
import secrets
import shutil
import subprocess
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .config import BackupStore, ConfigStore, ConfigError, SetupRequired, Document, read_env, atomic_write
from .installer import Installer, FakeInstaller, Cancelled, proxy_settings
from .platforms import SystemIntegration, FakeIntegration, DesktopMissing, process_options
from .profiles import ProfileStore, profile_plan
from .convenience import create_shortcut, create_app_shortcut
from .updates import UpdateManager, FakeUpdateManager
from .autostart import Autostart
from .settings import SettingsStore
from . import __version__
from .self_update import SelfUpdater, PROJECT_URL, RELEASES_URL


class Application:
    def __init__(self, app_root, config_dir, test_mode=False, test_missing=False):
        self.root = Path(app_root).resolve()
        self.data_dir = self.root / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.backups = BackupStore(self.root)
        self.config = ConfigStore(config_dir, self.backups)
        self.profiles = ProfileStore(self.data_dir)
        self.tray = None
        self.tray_status = "starting" if not test_mode else "test"
        self.integration = FakeIntegration(installed=not test_missing) if test_mode else SystemIntegration()
        self.installer = FakeInstaller(self.root, self.integration) if test_mode else Installer(self.root, self.integration)
        self.updater = (FakeUpdateManager if test_mode else UpdateManager)(self.integration, self.installer)
        self.self_updater = SelfUpdater(self.root, test_mode)
        self.ui_ready = False
        self.test_mode = test_mode
        self.autostart = Autostart(self.root, test_mode)
        self.settings_store = SettingsStore(self.data_dir / "settings.json", self.autostart)
        self.startup_error = ""
        self.environment = None
        self.lock = threading.RLock()
        self.job = {"busy": False, "kind": "", "status": "idle", "message": "", "logs": [], "id": 0}
        self.cancel = threading.Event()
        self.last_ping = time.monotonic()
        self.warning = ""
        self.server = None
        self.window_controller = None
        try:
            self.backups.migrate_legacy(Path(config_dir))
        except Exception:
            self.warning = "部分旧备份无法迁移或清理，请检查 backups 与原配置目录的权限。"
        threading.Thread(target=self.detect_initial, daemon=True).start()

    def detect_initial(self):
        try:
            self.environment = self.integration.detect()
        except Exception:
            self.warning = "环境检测未完成，请点击重新检测。"

    def preferences(self):
        try:
            value = json.loads((self.data_dir / "preferences.json").read_text("utf-8"))
            return proxy_settings(value.get("mode", "direct"), value.get("address", ""))
        except (OSError, ValueError, ConfigError):
            return {"mode": "direct", "address": ""}

    def settings(self):
        return self.settings_store.public()

    def initialize_settings(self):
        try:
            self.settings_store.initialize()
        except ConfigError as exc:
            self.startup_error = str(exc)

    def open_panel(self, page="main"):
        if self.window_controller is not None:
            return self.window_controller.show_panel(page)
        url = urlsplit(self.session_url)
        launch_browser(url._replace(query="view=" + page).geturl(), self.root)
        return {"ok": True}

    def open_config_file(self):
        path = self.config.path
        try:
            if path.exists() and not path.is_file():
                raise ConfigError("config.toml 路径不是文件，请检查配置目录。")
            path.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation leaves an existing file and its timestamps intact.
            try:
                with path.open("xb"):
                    pass
            except FileExistsError:
                if not path.is_file():
                    raise ConfigError("config.toml 路径不是文件，请检查配置目录。")
            if not self.test_mode:
                open_local(path)
        except OSError as exc:
            raise ConfigError("无法打开 config.toml，请检查文件权限及 .toml 文件的系统默认应用。") from exc
        return {"ok": True, "path": str(path), "message": "已请求系统默认应用打开 config.toml。"}

    def quit(self):
        if self.job["busy"]:
            raise ConfigError("请等待操作完成后再退出。")
        if self.window_controller is not None:
            return self.window_controller.command("exit")
        if self.tray:
            self.tray.stop()
        if self.server:
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        return {"ok": True, "exiting": True}

    def state(self):
        error = ""
        try:
            config = self.config.public()
        except Exception as exc:
            config = {"mode": "error", "needsSetup": False, "path": str(self.config.path), "envPath": str(self.config.env_path)}
            error = str(exc) if isinstance(exc, (ConfigError, OSError)) else "无法读取配置。"
        with self.lock:
            job = json.loads(json.dumps(self.job))
        try:
            profiles, profile_error = self.profiles.public(), ""
        except ConfigError as exc:
            profiles, profile_error = [], str(exc)
        return {"config": config, "configError": error, "backups": self.backups.summary(),
                "environment": self.environment, "platform": self.integration.info, "preferences": self.preferences(),
                "settings": self.settings(),
                "startup": {"supported": self.autostart.supported, "error": self.startup_error},
                "job": job, "warning": self.warning, "testMode": self.test_mode, "version": __version__,
                "appUpdate": dict(self.self_updater.state), "uiReady": self.ui_ready,
                "profiles": profiles, "profileError": profile_error, "activeProfile": self.active_profile(),
                "selectedProfile": self.selected_profile(), "tray": self.tray_status,
                "nativeWindow": self.window_controller is not None, "updates": self.updater.state}

    def selected_profile(self):
        try:
            selected = json.loads((self.data_dir / "active-profile.json").read_text("utf-8"))
            return self.profiles.get(selected["id"])["id"]
        except (OSError, ValueError, KeyError, TypeError, ConfigError):
            return None

    def active_profile(self):
        try:
            profile = self.profiles.get(self.selected_profile())
            state = self.config.inspect()
            if state["mode"] != "thirdparty" or state["baseUrl"] != profile["baseUrl"] or state["tree"].get("model") != profile["model"]:
                return None
            if profile.get("envKey") and state["envKey"] != profile["envKey"]:
                return None
            if profile.get("secret") and read_env(Document.read(self.config.env_path).text, state["envKey"]) != profile["secret"]:
                return None
            provider = state["tree"].get("model_providers", {}).get(state["provider"], {})
            for field, key, source in [("reviewModel", "review_model", state["tree"]),
                                       ("reasoningEffort", "model_reasoning_effort", state["tree"]),
                                       ("wireApi", "wire_api", provider)]:
                if profile.get(field) and source.get(key) != profile[field]:
                    return None
            if profile.get("requiresAuth") and provider.get("requires_openai_auth") != (profile["requiresAuth"] == "true"):
                return None
            return profile["id"]
        except Exception:
            pass
        return None

    def apply_profile(self, profile_id, mode="thirdparty"):
        from .config import commit_documents
        profile = self.profiles.get(profile_id)
        self.progress("正在应用「" + profile["name"] + "」…")
        changes = profile_plan(self.config, profile, mode)
        target = self.integration.desktop_target()
        saved = commit_documents(self.backups, changes)
        atomic_write(self.data_dir / "active-profile.json", json.dumps({"id": profile_id}).encode())
        try:
            self.integration.restart(target, self.config, self.progress)
        except Exception as exc:
            message = str(exc) if isinstance(exc, ConfigError) else "请手动打开 ChatGPT。"
            return {"message": "已应用配置，重启需要处理。" + message, "partial": True, "saved": saved}
        return {"message": "已应用「" + profile["name"] + "」，已检测到 ChatGPT 重新启动。", "saved": saved}

    def progress(self, message):
        with self.lock:
            self.job["message"] = message
            self.job["logs"] = (self.job["logs"] + [message])[-120:]

    def start_job(self, kind, operation):
        with self.lock:
            if self.job["busy"] or self.self_updater.pending:
                raise ConfigError("已有操作正在进行，请稍候。")
            self.cancel.clear()
            self.job = {"busy": True, "kind": kind, "status": "running", "message": "正在准备…", "logs": [], "id": self.job["id"] + 1}
        if self.tray:
            self.tray.refresh()
        def worker():
            try:
                result = operation()
                with self.lock:
                    self.job.update(status="success", result=result, message=result.get("message", "操作完成。"))
            except Exception as exc:
                code = "setup_required" if isinstance(exc, SetupRequired) else "desktop_missing" if isinstance(exc, DesktopMissing) else "cancelled" if isinstance(exc, Cancelled) else "error"
                message = str(exc) if isinstance(exc, (ConfigError, OSError)) else "操作未完成，请检查网络、权限或安装环境后重试。"
                with self.lock:
                    self.job.update(status="error", code=code, message=message)
            finally:
                with self.lock:
                    self.job["busy"] = False
                self.last_ping = time.monotonic()
                if self.tray:
                    self.tray.refresh()
                    if kind in {"profile", "switch", "setup", "install"}:
                        self.tray.notify(self.job["message"])
                if kind == "app-update" and self.self_updater.pending:
                    self.quit()
        threading.Thread(target=worker, daemon=True).start()
        return {"started": True, "jobId": self.job["id"]}

    def switch(self, mode, options=None):
        self.progress("正在检查配置与桌面应用…")
        changes = self.config.plan(mode, options)
        target = self.integration.desktop_target()
        self.progress("正在备份并保存配置…")
        from .config import commit_documents
        saved = commit_documents(self.backups, changes)
        try:
            self.integration.restart(target, self.config, self.progress)
        except Exception as exc:
            message = str(exc) if isinstance(exc, ConfigError) else "ChatGPT 未能自动重启，请手动打开应用。"
            return {"message": "配置已保存，重启需要处理。" + message, "partial": True, "saved": saved}
        if self.config.inspect()["mode"] != mode:
            raise ConfigError("重启期间配置被其他程序修改，请检查当前模式。")
        return {"message": "已切换至" + ("账号额度" if mode == "account" else "第三方 API") + "，已检测到重新启动的 ChatGPT 进程。", "saved": saved}

    def action(self, route, body):
        if route == "/api/project/open":
            choices = {"project": PROJECT_URL, "releases": RELEASES_URL, "issues": PROJECT_URL + "/issues"}
            if body.get("target") not in choices:
                raise ConfigError("未知项目链接。")
            if not self.test_mode:
                webbrowser.open(choices[body["target"]])
            return {"ok": True, "message": "已请求浏览器打开项目页面。"}
        if route in {"/api/app-update/check", "/api/app-update/install"}:
            saved_proxy = self.preferences()
            settings = proxy_settings(body.get("proxyMode", saved_proxy["mode"]),
                                      body.get("proxyAddress", saved_proxy["address"]))
            checking = route.endswith("/check")
            operation = self.self_updater.check if checking else self.self_updater.install
            return self.start_job("app-update-check" if checking else "app-update",
                                  lambda: operation(settings, self.progress, self.cancel))
        if route == "/api/settings":
            behavior = body.get("closeBehavior")
            if behavior not in ("exit", "background"):
                raise ConfigError("请选择直接退出或在后台运行。")
            fields = {"closeBehavior": behavior}
            for field in ("autoStart", "autoStartBackground"):
                if field in body:
                    fields[field] = body[field]
            with self.lock:
                settings = self.settings_store.save(fields)
                self.startup_error = ""
            return {"saved": True, "settings": settings}
        if route == "/api/close":
            if self.window_controller is not None:
                return self.window_controller.command("close")
            if self.settings()["closeBehavior"] == "background" and self.tray_status == "ready":
                return {"hidden": True}
            return self.quit()
        if route == "/api/test/tray" and self.test_mode and self.tray and hasattr(self.tray, "diagnostics"):
            return self.tray.diagnostics()
        if route == "/api/shortcut":
            def shortcut():
                self.progress("正在创建 ChatGPT 桌面图标…")
                if self.test_mode:
                    return {"message": "测试：已模拟创建桌面图标，没有改动真实桌面。"}
                return create_shortcut(self.integration, self.data_dir)
            return self.start_job("shortcut", shortcut)
        if route == "/api/app-shortcut":
            def app_shortcut():
                self.progress("正在创建 ChatGPT Switch 桌面图标…")
                if self.test_mode:
                    return {"message": "测试：已模拟创建 ChatGPT Switch 桌面图标，没有改动真实桌面。"}
                return create_app_shortcut(self.data_dir)
            return self.start_job("app-shortcut", app_shortcut)
        if route in {"/api/updates/check", "/api/updates/apply"}:
            settings = proxy_settings(body.get("proxyMode", "direct"), body.get("proxyAddress", ""))
            selection = body.get("selection", [])
            checking = route.endswith("/check")
            if not checking:
                self.updater.validate(selection)
            def updates():
                if checking:
                    return self.updater.check(settings, self.progress, self.cancel)
                try:
                    return self.updater.update(selection, settings, self.progress, self.cancel)
                finally:
                    self.environment = self.integration.detect()
            return self.start_job("update-check" if checking else "update", updates)
        if route == "/api/window":
            if self.window_controller is None:
                raise ConfigError("当前页面没有原生窗口。")
            return self.window_controller.command(body.get("action"), body.get("edge"))
        if route == "/api/ping":
            self.last_ping = time.monotonic()
            return {"ok": True}
        if route == "/api/switch":
            # Validate before dispatch so a missing config can immediately open the form.
            self.config.plan(body.get("mode"))
            return self.start_job("switch", lambda: self.switch(body.get("mode")))
        if route == "/api/setup":
            options = {k: body.get(k, "") for k in ("baseUrl", "envKey", "secret")}
            if any(not isinstance(v, str) for v in options.values()):
                raise ConfigError("配置字段必须是文本。")
            self.config.plan(body.get("mode"), options)
            return self.start_job("setup", lambda: self.switch(body.get("mode"), options))
        if route == "/api/profile/save":
            if self.job["busy"]:
                raise ConfigError("请等待当前操作完成后再编辑配置。")
            profile_id = self.profiles.save(body)
            if self.tray:
                self.tray.refresh()
            return {"id": profile_id, "saved": True}
        if route == "/api/profile/delete":
            if self.job["busy"]:
                raise ConfigError("请等待当前操作完成。")
            self.profiles.delete(body.get("id"))
            if self.tray:
                self.tray.refresh()
            return {"deleted": True}
        if route == "/api/profile/apply":
            profile = self.profiles.get(body.get("id"))
            mode = body.get("mode", "thirdparty")
            profile_plan(self.config, profile, mode)
            return self.start_job("profile", lambda: self.apply_profile(profile["id"], mode))
        if route == "/api/preferences":
            settings = proxy_settings(body.get("mode", "direct"), body.get("address", ""))
            atomic_write(self.data_dir / "preferences.json", json.dumps(settings, ensure_ascii=False, indent=2).encode("utf-8"))
            return {"saved": True, "preferences": settings}
        if route == "/api/detect":
            def detect():
                self.progress("正在检测当前系统与安装状态…")
                self.environment = self.integration.detect()
                return {"message": "环境检测完成。"}
            return self.start_job("detect", detect)
        if route == "/api/install":
            settings = proxy_settings(body.get("proxyMode", "direct"), body.get("proxyAddress", ""))
            selection = body.get("selection", [])
            compatible = body.get("allowCompatible") is True
            from .installer import installation_plan
            if self.environment is None:
                raise ConfigError("环境仍在检测，请稍候。")
            installation_plan(self.environment, selection, settings, compatible)
            atomic_write(self.data_dir / "preferences.json", json.dumps(settings).encode("utf-8"))
            def install():
                try:
                    return self.installer.install(selection, settings, compatible, self.progress, self.cancel)
                finally:
                    self.environment = self.integration.detect()
            return self.start_job("install", install)
        if route == "/api/cancel":
            if self.job["busy"] and self.job["kind"] in {"install", "update", "update-check", "app-update", "app-update-check"}:
                self.cancel.set()
            return {"requested": True}
        if route == "/api/open":
            if body.get("target") == "config-file":
                return self.open_config_file()
            choices = {"backups": self.backups.root, "config": self.config.path.parent}
            if body.get("target") not in choices:
                raise ConfigError("未知打开目标。")
            path = choices[body["target"]]
            path.mkdir(parents=True, exist_ok=True)
            if not self.test_mode:
                open_local(path)
            return {"ok": True, "path": str(path)}
        if route == "/api/winget":
            if self.integration.info["system"] != "Windows":
                raise ConfigError("WinGet 仅适用于 Windows。")
            if not self.test_mode:
                os.startfile("ms-windows-store://pdp/?ProductId=9NBLGGH4NNS1")
            return {"message": "已打开官方应用安装程序页面；完成安装后请重新检测。"}
        if route == "/api/quit":
            return self.quit()
        raise ConfigError("未知操作。")


def open_local(path):
    path = Path(path)
    if os.name == "nt":
        os.startfile(str(path))
    elif os.uname().sysname == "Darwin":
        subprocess.Popen(["/usr/bin/open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def launch_browser(url, app_root):
    candidates = []
    if os.name == "nt":
        for folder in [os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", ""), os.environ.get("LOCALAPPDATA", "")]:
            if folder:
                candidates.extend([Path(folder) / "Microsoft/Edge/Application/msedge.exe", Path(folder) / "Google/Chrome/Application/chrome.exe"])
    elif os.uname().sysname == "Darwin":
        candidates = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"), Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")]
    else:
        candidates = [Path(p) for p in [shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chromium-browser"), shutil.which("microsoft-edge")] if p]
    for browser in candidates:
        if browser.is_file():
            profile = Path(app_root) / "data/browser"
            subprocess.Popen([str(browser), "--app=" + url, "--window-size=920,800", "--user-data-dir=" + str(profile),
                              "--no-first-run", "--no-default-browser-check", "--disable-background-mode",
                              "--disk-cache-size=10485760", "--media-cache-size=1048576"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **process_options())
            return
    if not webbrowser.open(url):
        raise ConfigError("未找到可用图形浏览器。请安装浏览器后重新启动，或在浏览器中打开本地地址。")


def serve(app, ui_root, port=0, no_browser=False):
    token = secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log request bodies, tokens, or API secrets.

        def send(self, status, data, content_type="application/json; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.send_header("Content-Length", str(len(data)))
            try:
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def json(self, status, value):
            self.send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"))

        def valid_host(self):
            return self.headers.get("Host") in {"127.0.0.1:" + str(self.server.server_port), "localhost:" + str(self.server.server_port)}

        def authorized(self):
            expected_origin = "http://" + self.headers.get("Host", "")
            return self.valid_host() and secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token) and self.headers.get("Origin", expected_origin) == expected_origin

        def do_GET(self):
            if not self.valid_host():
                return self.json(403, {"error": "Invalid host"})
            route = urlsplit(self.path).path
            if route == "/api/state":
                if not self.authorized():
                    return self.json(403, {"error": "请从程序启动器打开此窗口。"})
                return self.json(200, app.state())
            files = {"/settings.js": "settings.js", "/settings.css": "settings.css", "/dialogs.css": "dialogs.css", "/motion.css": "motion.css", "/features.js": "features.js", "/features.css": "features.css", "/": "index.html", "/app.css": "app.css", "/app.js": "app.js", "/window.js": "window.js", "/window.css": "window.css", "/icon.svg": "icon.svg"}
            if route not in files:
                return self.json(404, {"error": "Not found"})
            path = Path(ui_root) / files[route]
            self.send(200, path.read_bytes(), (mimetypes.guess_type(path.name)[0] or "application/octet-stream") + "; charset=utf-8")

        def do_POST(self):
            if not self.authorized():
                return self.json(403, {"error": "请求验证失败，请从程序重新打开窗口。"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 131072:
                    return self.json(413, {"error": "请求过大。"})
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ConfigError("请求必须是对象。")
                value = app.action(urlsplit(self.path).path, body)
                self.json(200, value)
            except (ConfigError, OSError, ValueError, TypeError) as exc:
                self.json(400, {"error": str(exc), "code": "setup_required" if isinstance(exc, SetupRequired) else "error"})
            except Exception:
                self.json(500, {"error": "操作未完成，请检查文件权限和安装环境。"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    app.server = server
    url = "http://127.0.0.1:" + str(server.server_port) + "/#" + token
    app.session_url = url
    session_path = app.data_dir / "session.json"
    atomic_write(session_path, json.dumps({"url": url, "root": str(app.root), "pid": os.getpid(), "nativeWindow": getattr(app, "native_mode", False)}).encode())
    if no_browser:
        print(url, flush=True)
    else:
        from .tray import Tray
        app.tray = Tray(app, url, Path(ui_root) / "icon.png")
        app.tray.start()
        launch_browser(url, app.root)
        def watchdog():
            while True:
                time.sleep(10)
                if app.tray_status != "ready" and not app.job["busy"] and time.monotonic() - app.last_ping > 75:
                    server.shutdown()
                    return
        threading.Thread(target=watchdog, daemon=True).start()
    try:
        if not no_browser and app.integration.info["system"] == "Darwin" and app.tray and app.tray.icon:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            app.tray.run_main()
        else:
            server.serve_forever(poll_interval=0.3)
    finally:
        if app.tray:
            app.tray.stop()
        server.server_close()
        try:
            saved = json.loads(session_path.read_text())
            if saved.get("pid") == os.getpid():
                session_path.unlink()
        except (OSError, ValueError):
            pass


def serve_webview(app, ui_root, port=0, start_hidden=False):
    """Run the same local UI inside a native frameless WebView window."""
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError("缺少 pywebview，无法启动无框窗口。请重新运行构建脚本。") from exc
    from .window import WindowController
    app.native_mode = True
    app.tray_status = "starting"
    worker = threading.Thread(target=serve, args=(app, ui_root, port, True), daemon=True)
    worker.start()
    deadline = time.monotonic() + 15
    while not getattr(app, "session_url", None) and time.monotonic() < deadline:
        time.sleep(0.05)
    if not getattr(app, "session_url", None):
        raise RuntimeError("本地 UI 服务启动超时。")
    window = webview.create_window("ChatGPT Switch", app.session_url, width=880, height=840,
                                   min_size=(760, 620), resizable=True, frameless=True,
                                   easy_drag=False, confirm_close=False, text_select=True,
                                   background_color="#edf1fa", hidden=start_hidden)
    controller = WindowController(app, window, start_hidden=start_hidden)
    app.window_controller = controller
    window.events.before_show += controller.initialize
    window.events.closing += controller.allow_close
    window.events.loaded += controller.on_loaded
    window.events.maximized += controller.on_maximized
    window.events.restored += controller.on_restored
    window.events.minimized += controller.on_minimized
    # Server cleanup follows actual native-window destruction, never precedes it.
    def stop_server():
        if app.tray:
            app.tray.stop()
        app.server.shutdown()
    window.events.closed += stop_server
    debug_port = os.environ.get("CHATGPT_SWITCH_TEST_DEBUG_PORT")
    if app.test_mode and debug_port:
        webview.settings["REMOTE_DEBUGGING_PORT"] = int(debug_port)
    try:
        webview.start(debug=False, gui="edgechromium" if os.name == "nt" else None)
    finally:
        if worker.is_alive():
            app.server.shutdown()
        worker.join(timeout=5)


def reuse_running(app_root, show=True):
    try:
        session = json.loads((Path(app_root) / "data/session.json").read_text("utf-8"))
        if session.get("root") != str(Path(app_root).resolve()):
            return False
        url = urlsplit(session["url"])
        if url.hostname != "127.0.0.1" or not url.fragment:
            return False
        request = urllib.request.Request("http://" + url.netloc + "/api/ping", data=b"{}", headers={"Authorization": "Bearer " + url.fragment, "Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=2) as response:
            if response.status != 200:
                return False
        if not show:
            return True
        if session.get("nativeWindow"):
            request = urllib.request.Request("http://" + url.netloc + "/api/window", data=b'{"action":"show"}', headers={"Authorization": "Bearer " + url.fragment, "Content-Type": "application/json"})
            with opener.open(request, timeout=5) as response:
                return response.status == 200
        launch_browser(session["url"], app_root)
        return True
    except Exception:
        return False


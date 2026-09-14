from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from .config import ConfigError
from .platforms import process_options


CLI_SH = "https://chatgpt.com/codex/install.sh"
CLI_PS = "https://chatgpt.com/codex/install.ps1"
MAC_DMG = "https://persistent.oaistatic.com/codex-app-prod/Codex.dmg"
LINUX_BASE = "https://persistent.oaistatic.com/codex-app-prod/linux/"
PROXY_NAMES = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"}


class Cancelled(ConfigError):
    pass


def proxy_settings(mode="direct", address=""):
    if mode not in {"direct", "system", "custom"}:
        raise ConfigError("未知代理模式。")
    address = address.strip()
    if mode == "custom":
        if "://" not in address:
            address = "http://" + address
        try:
            parts = urlsplit(address)
            valid = parts.scheme in {"http", "https"} and parts.hostname and parts.port and not parts.username and not parts.password and parts.path in {"", "/"} and not parts.query and not parts.fragment and not any(c.isspace() for c in address)
        except ValueError:
            valid = False
        if not valid:
            raise ConfigError("代理请输入 HTTP(S) 地址和端口，例如 http://127.0.0.1:7890；不支持 SOCKS 或带账号密码的代理 URL。")
        address = address.rstrip("/")
    return {"mode": mode, "address": address if mode == "custom" else ""}


def proxy_env(settings, source=None):
    env = dict(os.environ if source is None else source)
    if settings["mode"] != "system":
        for name in PROXY_NAMES:
            env.pop(name, None)
        if settings["mode"] == "custom":
            for name in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
                env[name] = settings["address"]
        env["NO_PROXY"] = env["no_proxy"] = "localhost,127.0.0.1,::1"
    return env


def installation_plan(detection, selection, settings, allow_compatible=False):
    settings = proxy_settings(settings.get("mode", "direct"), settings.get("address", ""))
    info, plan = detection["platform"], []
    if not isinstance(selection, list) or not selection or any(x not in {"cli", "desktop"} for x in selection):
        raise ConfigError("请选择要安装的组件。")
    for component in dict.fromkeys(selection):
        if detection.get(component):
            continue
        if component == "cli":
            if not info["cliSupported"]:
                raise ConfigError("此系统或处理器没有已知的官方 Codex CLI 安装方案。")
            plan.append({"component": "cli", "kind": "cli-script", "url": CLI_PS if info["system"] == "Windows" else CLI_SH})
            continue
        if not detection.get("desktopKnown", True):
            raise ConfigError("桌面安装状态尚未确认，请先重新检测。")
        if info["desktopSupport"] == "unavailable":
            raise ConfigError(info["reason"])
        if info["desktopSupport"] == "compatible" and not allow_compatible:
            raise ConfigError("此发行版不在官方支持列表，请先勾选允许尝试兼容包。")
        if info["system"] == "Windows":
            if not detection.get("winget"):
                raise ConfigError("安装 ChatGPT 需要 WinGet，请先点击「获取 WinGet」，完成后重新检测。")
            command = [detection["winget"], "install", "--id", "9PLM9XGG6VKS", "--exact", "--source", "msstore", "--accept-source-agreements", "--accept-package-agreements", "--disable-interactivity"]
            if settings["mode"] == "custom":
                command += ["--proxy", settings["address"]]
            elif settings["mode"] == "direct":
                command += ["--no-proxy"]
            plan.append({"component": "desktop", "kind": "winget", "command": command})
        elif info["system"] == "Darwin":
            plan.append({"component": "desktop", "kind": "dmg", "url": MAC_DMG})
        else:
            deb = info["family"] == "debian"
            filename = ("chatgpt_" + ("amd64" if info["arch"] == "x64" else "arm64") + ".deb") if deb else ("chatgpt." + ("x86_64" if info["arch"] == "x64" else "aarch64") + ".rpm")
            plan.append({"component": "desktop", "kind": "linux-package", "url": LINUX_BASE + ("deb" if deb else "rpm") + "/latest/" + filename, "family": info["family"]})
    return plan


class Installer:
    def __init__(self, app_root, integration):
        self.cache = Path(app_root) / "cache/installers"
        self.integration = integration
        self.process = None

    def _check_cancel(self, cancel):
        if cancel.is_set():
            raise Cancelled("已取消安装请求。已完成的组件会保留；系统安装器可能仍在完成当前步骤，请重新检测。")

    def download(self, url, destination, settings, progress, cancel):
        self._check_cancel(cancel)
        # The download URLs are generated internally, never supplied by a browser request.
        if urlsplit(url).scheme != "https" or urlsplit(url).hostname not in {"chatgpt.com", "persistent.oaistatic.com"}:
            raise ConfigError("下载地址不在官方安装来源中。")
        proxies = None if settings["mode"] == "system" else {} if settings["mode"] == "direct" else {"http": settings["address"], "https": settings["address"]}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_name(destination.name + ".part")
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "ChatGPT-Switch/2.0"})
            with opener.open(request, timeout=40) as response, temp.open("wb") as output:
                if urlsplit(response.url).scheme != "https":
                    raise ConfigError("安装下载被重定向到非 HTTPS 地址，已停止。")
                total, count, last = int(response.headers.get("Content-Length", 0)), 0, 0.0
                if total > 2 * 1024 ** 3:
                    raise ConfigError("安装包大小异常，已停止。")
                while True:
                    self._check_cancel(cancel)
                    chunk = response.read(1024 * 512)
                    if not chunk:
                        break
                    output.write(chunk)
                    count += len(chunk)
                    if count > 2 * 1024 ** 3:
                        raise ConfigError("安装包大小异常，已停止。")
                    if time.monotonic() - last > 1:
                        progress("正在下载官方安装文件：" + (str(round(count / total * 100)) + "%" if total else str(count // (1024 * 1024)) + " MB"))
                        last = time.monotonic()
                if total and count != total:
                    raise ConfigError("安装包下载不完整，请重试。")
            os.replace(str(temp), str(destination))
        finally:
            if temp.exists():
                temp.unlink()
        return destination

    def run(self, command, env, progress, cancel, timeout=1800):
        self._check_cancel(cancel)
        output_lines = []
        self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, encoding="utf-8", errors="replace", env=env, **process_options())
        process = self.process
        def reader():
            for raw in process.stdout:
                line = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", raw).strip()
                if line:
                    output_lines.append(line[:500])
                    if len(output_lines) > 100:
                        del output_lines[0]
                    progress(line[:500])
        worker = threading.Thread(target=reader, daemon=True)
        worker.start()
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                self._check_cancel(cancel)
                if time.monotonic() > deadline:
                    raise ConfigError("安装等待超时，请检查系统授权或商店窗口后重新检测。")
                time.sleep(0.2)
            worker.join(timeout=2)
            if process.returncode != 0:
                raise ConfigError("安装命令未成功（退出码 " + str(process.returncode) + "）。请查看安装记录，修正网络或权限问题后重试。")
        finally:
            if process.poll() is None:
                process.terminate()
            self.process = None

    def privileged(self, command, env, progress, cancel):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            return self.run(command, env, progress, cancel)
        if shutil.which("pkexec"):
            # GUI authorization; only package-manager commands selected by this program.
            forwarded = [k + "=" + v for k, v in env.items() if k in PROXY_NAMES]
            return self.run([shutil.which("pkexec"), "/usr/bin/env"] + forwarded + command, env, progress, cancel)
        raise ConfigError("系统未提供图形授权工具 pkexec。安装包已下载到软件 cache/installers，可使用系统包管理器授权安装；本工具不会索取或保存管理员密码。")

    def install(self, selection, settings, allow_compatible, progress, cancel):
        settings = proxy_settings(settings.get("mode", "direct"), settings.get("address", ""))
        detected = self.integration.detect()
        plan = installation_plan(detected, selection, settings, allow_compatible)
        if not plan:
            return {"message": "所选组件已经安装，无需重复安装。", "detection": detected}
        env = proxy_env(settings)
        env["CODEX_NON_INTERACTIVE"] = "1"
        completed = []
        for step in plan:
            self._check_cancel(cancel)
            progress("正在安装 " + ("Codex CLI" if step["component"] == "cli" else "ChatGPT 桌面端") + "…")
            kind = step["kind"]
            if kind == "cli-script":
                windows = detected["platform"]["system"] == "Windows"
                script = self.download(step["url"], self.cache / ("codex-install.ps1" if windows else "codex-install.sh"), settings, progress, cancel)
                if windows:
                    ps = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe")
                    command = [ps, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)]
                else:
                    command = ["/bin/sh", str(script)]
                self.run(command, env, progress, cancel)
            elif kind == "winget":
                self.run(step["command"], env, progress, cancel)
            elif kind == "dmg":
                package = self.download(step["url"], self.cache / "ChatGPT.dmg", settings, progress, cancel)
                mount = self.cache / ("mount-" + uuid4().hex)
                mount.mkdir()
                attached = False
                try:
                    self.run(["/usr/bin/hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mount), str(package)], env, progress, cancel)
                    attached = True
                    bundles = [p for p in mount.iterdir() if p.suffix == ".app" and p.is_dir() and not p.is_symlink()]
                    if len(bundles) != 1:
                        raise ConfigError("官方磁盘映像内的应用无法唯一识别。")
                    bundle = bundles[0]
                    self.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)], env, progress, cancel)
                    self.run(["/usr/sbin/spctl", "--assess", "--type", "execute", str(bundle)], env, progress, cancel)
                    destination = Path.home() / "Applications" / bundle.name
                    destination.parent.mkdir(exist_ok=True)
                    if destination.exists():
                        raise ConfigError("目标应用文件夹已存在，请重新检测安装状态。")
                    self.run(["/usr/bin/ditto", str(bundle), str(destination)], env, progress, cancel)
                finally:
                    if attached:
                        subprocess.run(["/usr/bin/hdiutil", "detach", str(mount)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    try:
                        mount.rmdir()
                    except OSError:
                        pass
            else:
                package = self.download(step["url"], self.cache / Path(urlsplit(step["url"]).path).name, settings, progress, cancel)
                if step["family"] == "debian":
                    manager = shutil.which("apt-get")
                    command = [manager, "install", "-y", str(package)] if manager else None
                elif step["family"] == "suse":
                    manager = shutil.which("zypper")
                    command = [manager, "--non-interactive", "install", str(package)] if manager else None
                else:
                    manager = shutil.which("dnf") or shutil.which("yum")
                    command = [manager, "install", "-y", str(package)] if manager else None
                if not command:
                    raise ConfigError("未找到此系列的系统包管理器。安装包已保留在 cache/installers。")
                self.privileged(command, env, progress, cancel)
            completed.append(step["component"])
            progress("正在核验已安装的组件…")
            result = self.integration.detect()
            if not result.get(step["component"]):
                raise ConfigError("安装命令已结束，但尚未检测到 " + step["component"] + "。请重新检测；没有将其标记为安装成功。")
        return {"message": "安装完成，已检测到所选组件。首次使用请在官方应用中登录。", "detection": self.integration.detect(), "completed": completed}


class FakeInstaller(Installer):
    def install(self, selection, settings, allow_compatible, progress, cancel):
        installation_plan(self.integration.detect(), selection, settings, allow_compatible)
        self.integration.installed = True
        progress("模拟安装完成，没有下载安装软件。")
        return {"message": "模拟安装完成", "detection": self.integration.detect()}

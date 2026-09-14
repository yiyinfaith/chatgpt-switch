"""Updates for this portable application, restricted to its GitHub releases."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__
from .config import ConfigError
from .installer import Cancelled

PROJECT_URL = "https://github.com/yiyinfaith/chatgpt-switch"
RELEASES_URL = PROJECT_URL + "/releases"
LATEST_API = "https://api.github.com/repos/yiyinfaith/chatgpt-switch/releases/latest"
MAX_SIZE = 512 * 1024 * 1024


def release_version(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(value))
    if not match:
        raise ConfigError("发布版本号需为 v主版本.次版本.修订号，请前往发布页查看。")
    return tuple(map(int, match.groups()))


def asset_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.netloc != "github.com" or
            not parsed.path.startswith("/yiyinfaith/chatgpt-switch/releases/download/") or
            parsed.query or parsed.fragment):
        raise ConfigError("更新文件不在本项目的 GitHub Releases 中。")
    return value


def select_asset(release):
    candidates = [a for a in release.get("assets", [])
                  if re.fullmatch(r"ChatGPT[ ._-]Switch(?:[ ._-](?:windows[ ._-])?x64)?\.exe",
                                  a.get("name", ""), re.IGNORECASE)
                  and a.get("state") == "uploaded"]
    if len(candidates) != 1:
        raise ConfigError("此版本没有唯一可识别的 Windows x64 EXE，请在发布页手动下载。")
    asset = candidates[0]
    asset_url(asset.get("browser_download_url", ""))
    if not 0 < int(asset.get("size", 0)) <= MAX_SIZE:
        raise ConfigError("更新文件大小异常。")
    if not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", asset.get("digest") or ""):
        raise ConfigError("此发布缺少 SHA-256 校验信息，请在发布页手动下载。")
    return dict(asset)


def verify_executable(path, size, digest):
    if path.stat().st_size != size:
        raise ConfigError("更新文件下载不完整，请重试。")
    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            raise ConfigError("下载内容不是 Windows EXE，已停止更新。")
        stream.seek(struct.unpack_from("<I", header, 60)[0])
        if stream.read(6) != b"PE\0\0\x64\x86":
            raise ConfigError("更新文件不是 Windows x64 程序。")
        stream.seek(0)
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual.lower() != digest.removeprefix("sha256:").lower():
        raise ConfigError("更新文件 SHA-256 校验失败，已停止更新。")


class SelfUpdater:
    def __init__(self, root, test_mode=False):
        self.root = Path(root)
        self.test_mode = test_mode
        self.asset = None
        self.pending = False
        self.state = {"current": __version__, "latest": "", "status": "unchecked",
                      "message": "点击检测，查看本应用是否有新版本。", "releaseUrl": RELEASES_URL,
                      "canInstall": False}

    @property
    def supported(self):
        return os.name == "nt" and getattr(sys, "frozen", False) and not self.test_mode

    @staticmethod
    def opener(settings):
        proxies = (None if settings["mode"] == "system" else {} if settings["mode"] == "direct"
                   else {"http": settings["address"], "https": settings["address"]})
        return urllib.request.build_opener(urllib.request.ProxyHandler(proxies))

    @staticmethod
    def cancelled(cancel):
        if cancel.is_set():
            raise Cancelled("已取消本应用更新，当前 EXE 保持原样。")

    def check(self, settings, progress, cancel):
        self.asset = None
        self.state.update(status="checking", latest="", canInstall=False, message="正在连接 GitHub Releases…")
        try:
            self.cancelled(cancel)
            progress(self.state["message"])
            request = urllib.request.Request(LATEST_API, headers={
                "User-Agent": "ChatGPT-Switch/" + __version__, "Accept": "application/vnd.github+json"})
            with self.opener(settings).open(request, timeout=25) as response:
                payload = response.read(2 * 1024 * 1024 + 1)
            if len(payload) > 2 * 1024 * 1024:
                raise ConfigError("发布信息过大，请前往发布页查看。")
            release = json.loads(payload)
            self.cancelled(cancel)
            if release.get("draft") or release.get("prerelease"):
                raise ConfigError("尚未找到可用的正式发布版本。")
            latest = release["tag_name"]
            newer = release_version(latest) > release_version(__version__)
            self.state.update(latest=latest, status="available" if newer else "current")
            if newer:
                self.asset = select_asset(release)
                self.state.update(canInstall=bool(self.supported), message=(
                    "发现新版本，下载完成后将自动替换并重启本应用。" if self.supported else
                    "发现新版本；当前运行方式请前往发布页下载。"))
            else:
                self.state["message"] = "当前已是最新版本。" if release_version(latest) == release_version(__version__) else "当前版本比 GitHub 最新正式版更新，无需更新。"
            return {"message": self.state["message"]}
        except Exception as exc:
            if isinstance(exc, urllib.error.HTTPError):
                message = ("GitHub 暂无正式发布版本。" if exc.code == 404 else
                           "GitHub 请求受限，请稍后重试或前往发布页。" if exc.code in (403, 429) else
                           "GitHub 暂时无法访问，请稍后重试。")
            elif isinstance(exc, (ConfigError, Cancelled)):
                message = str(exc)
            else:
                message = "版本检测失败，请检查代理或网络，或前往发布页手动下载。"
            self.state.update(status="error", message=message, canInstall=False)
            raise ConfigError(message) from exc

    def download(self, asset, destination, settings, progress, cancel):
        request = urllib.request.Request(asset_url(asset["browser_download_url"]),
                                         headers={"User-Agent": "ChatGPT-Switch/" + __version__})
        try:
            with self.opener(settings).open(request, timeout=40) as response, destination.open("xb") as output:
                final = urlsplit(response.url)
                if final.scheme != "https" or final.hostname not in {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}:
                    raise ConfigError("更新下载重定向到了未知来源。")
                count, last = 0, 0.0
                while True:
                    self.cancelled(cancel)
                    chunk = response.read(512 * 1024)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > asset["size"] or count > MAX_SIZE:
                        raise ConfigError("更新下载大小异常。")
                    output.write(chunk)
                    if time.monotonic() - last > 0.5:
                        progress(f"正在下载 ChatGPT Switch：{count * 100 // asset['size']}%")
                        last = time.monotonic()
            self.cancelled(cancel)
            verify_executable(destination, asset["size"], asset["digest"])
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    def handoff(self, stage, target, digest, expected_version, parent_pid):
        """Copy the helper outside _MEIPASS so it survives the one-file app exiting."""
        helper = stage / "apply-update.ps1"
        shutil.copyfile(Path(__file__).with_name("apply_update.ps1"), helper)
        plan = {"target": str(target), "source": str(stage / "update.exe"),
                "sha256": digest.removeprefix("sha256:"), "version": expected_version.lstrip("v"),
                "parentPid": parent_pid, "root": str(target.parent), "releases": RELEASES_URL}
        if self.test_mode:
            plan["testRoot"] = str(target.parent)
        (stage / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), "utf-8")
        env = os.environ.copy()
        for key in list(env):
            if key.startswith("_PYI_") or key in {"_MEIPASS2", "PSModulePath"}:
                del env[key]
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        process = subprocess.Popen([str(powershell), "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                                    "-ExecutionPolicy", "Bypass", "-File", str(helper)],
                                   cwd=target.parent, env=env, creationflags=subprocess.CREATE_NO_WINDOW,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if (stage / "ready").exists():
                return process
            if process.poll() is not None:
                break
            time.sleep(0.1)
        process.terminate() if process.poll() is None else None
        process.wait(timeout=5)
        raise ConfigError("无法启动自动替换程序，请前往发布页手动下载。")

    def install(self, settings, progress, cancel):
        if not self.supported or not self.asset or self.state["status"] != "available":
            raise ConfigError("请先检测新版本；当前环境不支持自动更新时，请前往发布页下载。")
        target = Path(sys.executable).resolve()
        stage = None
        try:
            # Same-volume staging permits atomic replacement and checks directory write access first.
            stage = Path(tempfile.mkdtemp(prefix=".chatgpt-switch-update-", dir=target.parent))
            self.state.update(status="downloading", canInstall=False)
            self.download(self.asset, stage / "update.exe", settings, progress, cancel)
            self.cancelled(cancel)
            progress("校验通过，正在准备替换并重启本应用…")
            self.handoff(stage, target, self.asset["digest"], self.state["latest"], os.getpid())
            self.pending = True
            self.state.update(status="restarting", message="下载已校验，正在退出并启动新版本…")
            return {"message": self.state["message"], "restart": True}
        except Exception as exc:
            if stage:
                shutil.rmtree(stage, ignore_errors=True)
            message = str(exc) if isinstance(exc, (ConfigError, Cancelled)) else "自动更新未完成，请检查网络或目录权限，或前往发布页手动下载。"
            self.state.update(status="error", message=message, canInstall=False)
            raise ConfigError(message) from exc

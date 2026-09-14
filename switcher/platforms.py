from __future__ import annotations

import json
import os
import platform
import plistlib
import shutil
import signal
import subprocess
import time
from pathlib import Path

from .config import ConfigError, Document, read_env


def process_options():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def run_capture(command, timeout=25, env=None):
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=env, **process_options())


def powershell(script, timeout=25):
    import base64
    executable = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    encoded = base64.b64encode(("[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding; " + script).encode("utf-16le")).decode("ascii")
    return run_capture([executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded], timeout)


def same_windows_executable(candidate, target):
    """Compare file identity, allowing the registered and redirected AppX paths.

    Windows can report C:\\Program Files\\WindowsApps even when Python resolves
    that same file to D:\\WindowsApps. Normalizing only the target loses the match.
    Keep the lexical fast path for paths that cannot be opened by this user.
    """
    if not candidate:
        return False
    import ntpath
    if ntpath.normcase(ntpath.normpath(candidate)) == ntpath.normcase(ntpath.normpath(target)):
        return True
    # AppX packages can be mounted through different lexical roots.  For
    # example, the package registration may resolve to D:\WindowsApps while
    # Win32 process inspection reports C:\Program Files\WindowsApps.  The
    # package-relative path is the stable identity in that case.
    def appx_relative(path):
        normalized = ntpath.normpath(path).replace("/", "\\")
        parts = normalized.split("\\")
        for index, part in enumerate(parts):
            if part.casefold() == "windowsapps":
                return "\\".join(parts[index + 1:]).casefold()
        return None
    candidate_relative = appx_relative(candidate)
    target_relative = appx_relative(target)
    if candidate_relative and target_relative and candidate_relative == target_relative:
        return True
    try:
        return os.path.samefile(candidate, target)
    except (OSError, ValueError):
        return False


def read_os_release(path=Path("/etc/os-release")):
    import shlex
    result = {}
    try:
        for line in path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                result[key] = " ".join(shlex.split(value))
    except (OSError, ValueError):
        pass
    return result


def platform_info(system=None, machine=None, release=None, libc=None):
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(machine, machine)
    info = {"system": system, "arch": arch, "family": system.lower(), "label": system,
            "distro": "", "version": "", "libc": "", "desktopSupport": "supported", "reason": ""}
    if system == "Linux":
        release = read_os_release() if release is None else release
        distro = release.get("ID", "unknown").lower()
        likes = set(release.get("ID_LIKE", "").lower().split()) | {distro}
        family = "debian" if likes & {"debian", "ubuntu", "linuxmint", "pop", "kali", "deepin", "uos"} else "fedora" if likes & {"fedora", "rhel", "centos", "rocky", "almalinux"} else "suse" if likes & {"suse", "opensuse", "opensuse-tumbleweed", "opensuse-leap"} else "arch" if likes & {"arch", "archlinux", "manjaro"} else "alpine" if "alpine" in likes else "nixos" if "nixos" in likes else "gentoo" if "gentoo" in likes else "void" if "void" in likes else "other"
        detected_libc = libc or ("musl" if list(Path("/lib").glob("ld-musl-*.so*")) else platform.libc_ver()[0] or "glibc")
        version = release.get("VERSION_ID", "")
        official = (distro == "ubuntu" and version in {"24.04", "26.04"}) or (distro == "debian" and version == "13") or (distro == "fedora" and version in {"43", "44"})
        support = "supported" if official else "compatible" if family in {"debian", "fedora", "suse"} else "unavailable"
        if detected_libc == "musl":
            support = "unavailable"
        info.update(family=family, distro=distro, version=version, libc=detected_libc,
                    label=release.get("PRETTY_NAME", "Linux") + " · " + arch, desktopSupport=support)
        if support == "compatible":
            info["reason"] = "此发行版不在官方支持列表，可自行选择尝试同系列 DEB / RPM 包；依赖兼容性由系统包管理器检查。"
        elif support == "unavailable":
            info["reason"] = "官方暂未提供此 Linux 系列的桌面安装包。本工具仍可配置 API、管理备份和安装 CLI；不会自动使用第三方桌面封装。"
    elif system == "Darwin":
        info.update(label="macOS · " + arch, family="macos")
        if arch != "arm64":
            info.update(desktopSupport="unavailable", reason="当前官方 ChatGPT / Codex 桌面下载面向 Apple Silicon，Intel Mac 可使用 CLI。")
    elif system == "Windows":
        info["label"] = "Windows · " + arch
    else:
        info.update(desktopSupport="unavailable", reason="此系统没有已知的官方桌面安装方案。")
    info["cliSupported"] = system in {"Windows", "Darwin", "Linux"} and arch in {"x64", "arm64"}
    if arch not in {"x64", "arm64"}:
        info.update(desktopSupport="unavailable", reason="官方安装方案仅适用于 x64 / ARM64。")
    return info


class DesktopMissing(ConfigError):
    pass


class SystemIntegration:
    def __init__(self):
        self.info = platform_info()
        if self.info["system"] == "Darwin" and self.info["arch"] == "x64":
            result = run_capture(["/usr/sbin/sysctl", "-n", "hw.optional.arm64"])
            if result.returncode == 0 and result.stdout.strip() == "1":
                self.info = platform_info("Darwin", "arm64")

    def cli_path(self):
        candidates = [shutil.which("codex"), shutil.which("codex.cmd"), shutil.which("codex.exe")]
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
        candidates += [local / "Programs/OpenAI/Codex/bin/codex.exe", local / "Microsoft/WinGet/Links/codex.exe",
                       Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming"))) / "npm/codex.cmd",
                       Path.home() / ".local/bin/codex", Path.home() / ".codex/bin/codex", Path("/opt/homebrew/bin/codex"), Path("/usr/local/bin/codex")]
        return next((str(p) for p in candidates if p and Path(p).is_file()), None)

    def desktop_target(self):
        system = self.info["system"]
        if system == "Windows":
            result = powershell("$ErrorActionPreference='Stop'; $packages=@(Get-AppxPackage -Name OpenAI.Codex)+@(Get-AppxPackage -Name OpenAI.ChatGPT)+@(Get-AppxPackage -Name OpenAI.ChatGPT-Desktop); foreach($p in $packages){$m=Get-AppxPackageManifest -Package $p; foreach($a in $m.Package.Applications.Application){if([IO.Path]::GetFileName([string]$a.Executable) -ieq 'ChatGPT.exe'){[pscustomobject]@{appId=$p.PackageFamilyName+'!'+$a.Id;packageFamily=$p.PackageFamilyName;version=[string]$p.Version;executable=[IO.Path]::Combine($p.InstallLocation,[string]$a.Executable)}|ConvertTo-Json -Compress;exit 0}}};exit 2")
            if result.returncode == 2:
                raise DesktopMissing("未检测到 ChatGPT 桌面端，请打开「安装与代理」安装。")
            if result.returncode != 0:
                raise ConfigError("无法读取 Windows 应用注册信息，请重新检测。")
            target = json.loads(result.stdout.strip().lstrip("\ufeff"))
            if not Path(target["executable"]).is_file():
                raise ConfigError("ChatGPT 注册的可执行文件不存在，请修复安装。")
            return target
        if system == "Darwin":
            for folder in [Path("/Applications"), Path.home() / "Applications"]:
                for name in ["ChatGPT.app", "Codex.app"]:
                    bundle = folder / name
                    plist = bundle / "Contents/Info.plist"
                    if plist.is_file():
                        with plist.open("rb") as f:
                            metadata = plistlib.load(f)
                        binary = bundle / "Contents/MacOS" / metadata.get("CFBundleExecutable", "ChatGPT")
                        if binary.is_file():
                            return {"executable": str(binary), "bundle": str(bundle), "bundleId": metadata.get("CFBundleIdentifier", "")}
        if system == "Linux":
            for candidate in [shutil.which("chatgpt"), "/opt/ChatGPT/chatgpt", "/opt/chatgpt/chatgpt", "/usr/lib/chatgpt/chatgpt", "/opt/Codex/codex"]:
                if candidate and Path(candidate).is_file():
                    return {"executable": str(Path(candidate).resolve())}
        raise DesktopMissing("未检测到 ChatGPT 桌面端，请打开「安装与代理」查看安装方案。")

    def detect(self):
        target, warning = None, ""
        try:
            target = self.desktop_target()
        except DesktopMissing:
            pass
        except Exception:
            warning = "桌面安装状态暂时无法确认，请重新检测。"
        winget = shutil.which("winget") if self.info["system"] == "Windows" else None
        if not winget and self.info["system"] == "Windows":
            candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WindowsApps/winget.exe"
            winget = str(candidate) if candidate.is_file() else None
        return {"platform": self.info, "cli": self.cli_path(), "desktop": target, "winget": winget,
                "warning": warning, "desktopKnown": not bool(warning)}

    def _matching_pids(self, target):
        system = self.info["system"]
        if system == "Windows":
            # Get-Process.Path can be blank for Store/Chromium child processes.
            # Compare CIM's returned paths in Python using the same file identity
            # rules on both sides; a moved Store install can have two valid paths.
            result = powershell("$ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Process -Filter \"Name='ChatGPT.exe'\" -ErrorAction Stop | Select-Object ProcessId,ExecutablePath) | ConvertTo-Json -Compress")
            if result.returncode:
                raise ConfigError("无法检查 ChatGPT 进程，未执行关闭。")
            raw = result.stdout.strip().lstrip("\ufeff")
            rows = json.loads(raw) if raw else []
            if not isinstance(rows, list):
                rows = [rows]
            return [int(row["ProcessId"]) for row in rows
                    if row and same_windows_executable(row.get("ExecutablePath"), target["executable"])]
        executable = Path(target["executable"]).resolve()
        if system == "Linux":
            found = []
            for entry in Path("/proc").iterdir():
                if entry.name.isdigit():
                    try:
                        if (entry / "exe").resolve() == executable:
                            found.append(int(entry.name))
                    except OSError:
                        pass
            return found
        result = run_capture(["/bin/ps", "-axo", "pid=,comm="])
        found = []
        for line in result.stdout.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[1] == str(executable):
                found.append(int(parts[0]))
        return found

    def restart(self, target, config, progress):
        progress("正在关闭 ChatGPT…")
        pids = self._matching_pids(target)
        if self.info["system"] == "Windows" and pids:
            # Store/Chromium uses several ChatGPT.exe processes and most child
            # processes have no window handle. Ask every PID to exit cleanly;
            # the bounded wait below decides whether a force stop is necessary.
            ids = ",".join(str(int(x)) for x in pids)
            # CloseMainWindow asks the primary UI process to exit cleanly.
            # Child/renderer processes generally have no window; calling
            # Process.Close() on them only disposes the PowerShell wrapper and
            # leaves the process alive, so those are handled by the bounded
            # force-stop fallback below.
            graceful = "Get-Process -Id " + ids + " -ErrorAction SilentlyContinue | ForEach-Object { try { if($_.MainWindowHandle -ne 0){$null=$_.CloseMainWindow()} } catch {} }"
            powershell(graceful)
        elif pids:
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 7
        while pids and time.monotonic() < deadline:
            time.sleep(0.3)
            pids = self._matching_pids(target)
        if pids:
            if self.info["system"] == "Windows":
                ids = ",".join(str(int(x)) for x in pids)
                progress("ChatGPT 未响应，正在结束残留进程…")
                powershell("Stop-Process -Id " + ids + " -Force -ErrorAction SilentlyContinue")
            else:
                for pid in pids:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        if self._matching_pids(target):
            raise ConfigError("配置已保存，但 ChatGPT 尚未退出，请手动重启。")
        progress("正在重新打开 ChatGPT…")
        env = os.environ.copy()
        state = config.inspect()
        secret = read_env(Document.read(config.env_path).text, state["envKey"])
        if secret:
            env[state["envKey"]] = secret
        # Directly start the discovered binary so the selected .env variable reaches
        # this desktop process. No registry, shell-profile, or global env changes.
        if self.info["system"] == "Windows" and target.get("appId"):
            # Store-packaged ChatGPT must be activated through its registered AppX
            # identity; launching ChatGPT.exe directly can create child processes
            # without bootstrapping the packaged app lifecycle.
            explorer = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "explorer.exe")
            subprocess.Popen([explorer, "shell:AppsFolder\\" + target["appId"]], env=env, **process_options())
        else:
            subprocess.Popen([target["executable"]], cwd=str(Path(target["executable"]).parent),
                             env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **process_options())
        time.sleep(1)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self._matching_pids(target):
                return
            time.sleep(0.5)
        raise ConfigError("配置已保存，但尚未检测到 ChatGPT 进程，请手动打开应用。")


class FakeIntegration(SystemIntegration):
    def __init__(self, info=None, installed=True):
        self.info = info or platform_info("Windows", "x86_64")
        self.installed = installed
        self.restarts = 0

    def cli_path(self):
        return "/test/codex" if self.installed else None

    def desktop_target(self):
        if not self.installed:
            raise DesktopMissing("测试环境：桌面端未安装。")
        return {"executable": "/test/ChatGPT"}

    def detect(self):
        return {"platform": self.info, "cli": self.cli_path(), "desktop": self.desktop_target() if self.installed else None,
                "winget": "TEST-WINGET", "warning": "", "desktopKnown": True}

    def restart(self, target, config, progress):
        self.restarts += 1
        progress("模拟重启完成（测试配置）")

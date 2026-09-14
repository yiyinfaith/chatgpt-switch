"""Desktop shortcuts use the registered app identity, never a versioned launch path."""
from __future__ import annotations

import json
import hashlib
import os
import sys
from pathlib import Path

from .config import ConfigError, atomic_write
from .icons import executable_icon, notify_shortcut
from .platforms import powershell


def ps_string(value):
    return "'" + str(value).replace("'", "''") + "'"


def create_shortcut(integration, data_dir, desktop_dir=None):
    # desktop_dir is for isolated tests only; the HTTP route never accepts a path.
    if integration.info["system"] != "Windows":
        raise ConfigError("一键创建桌面图标目前适用于 Windows。")
    target = integration.desktop_target()
    if not target.get("appId"):
        raise ConfigError("未找到 ChatGPT 的 Windows 应用注册信息，请先安装桌面端。")
    try:
        icon_data = executable_icon(target['executable'])
    except (OSError, ValueError) as exc:
        raise ConfigError('无法读取 ChatGPT 的完整图标资源，请检查安装是否完整。') from exc
    # A different path invalidates Explorer's cached low-resolution icon, without
    # deleting the user's icon cache or restarting Explorer.
    icon = Path(data_dir).resolve() / 'icons' / ('ChatGPT-' + hashlib.sha256(icon_data).hexdigest()[:12] + '.ico')
    if not icon.is_file() or icon.read_bytes() != icon_data:
        atomic_write(icon, icon_data)
    explorer = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "explorer.exe")
    folder = ps_string(desktop_dir) if desktop_dir is not None else "[Environment]::GetFolderPath('DesktopDirectory')"
    result = powershell("""
$ErrorActionPreference='Stop'
$desktop = %s
if (-not $desktop -or -not (Test-Path -LiteralPath $desktop -PathType Container)) { throw 'Desktop folder is unavailable.' }
$destination = [IO.Path]::Combine($desktop, 'ChatGPT.lnk')
$explorer = %s
$arguments = %s
$iconPath = %s
$shell = New-Object -ComObject WScript.Shell
if (Test-Path -LiteralPath $destination) {
    $existing = $shell.CreateShortcut($destination)
    if ($existing.TargetPath -and $existing.Arguments -ne $arguments -and [IO.Path]::GetFileName($existing.TargetPath) -ine 'ChatGPT.exe') {
        throw 'A different shortcut already uses the name ChatGPT.lnk; it was preserved.'
    }
}
$link = $shell.CreateShortcut($destination)
$link.TargetPath = $explorer
$link.Arguments = $arguments
$link.WorkingDirectory = [IO.Path]::GetDirectoryName($explorer)
$link.IconLocation = $iconPath + ',0'
$link.Description = 'ChatGPT'
$link.Save()
$verified = $shell.CreateShortcut($destination)
if ($verified.TargetPath -ine $explorer -or $verified.Arguments -ne $arguments) { throw 'Shortcut verification failed.' }
[pscustomobject]@{path=$destination} | ConvertTo-Json -Compress
""" % (folder, ps_string(explorer), ps_string("shell:AppsFolder\\" + target["appId"]),
       ps_string(icon)))
    if result.returncode:
        if "different shortcut" in result.stderr:
            raise ConfigError("桌面已有指向其他应用的 ChatGPT.lnk，请先重命名该图标后重试。")
        raise ConfigError("桌面图标创建失败，请确认桌面文件夹可写且 ChatGPT 安装完整。")
    path = json.loads(result.stdout.strip().lstrip("\ufeff"))["path"]
    notify_shortcut(path)
    return {"message": "已创建 ChatGPT 高清桌面图标。", "path": path}


def create_app_shortcut(data_dir, desktop_dir=None, executable=None):
    """Create a shortcut for this packaged ChatGPT Switch application."""
    if os.name != "nt":
        raise ConfigError("一键创建桌面图标目前适用于 Windows。")
    executable = Path(executable or sys.executable).resolve()
    if not executable.is_file() or executable.suffix.lower() != ".exe":
        raise ConfigError("未找到当前 ChatGPT Switch 应用文件。")
    try:
        icon_data = executable_icon(executable)
    except (OSError, ValueError) as exc:
        raise ConfigError("无法读取 ChatGPT Switch 的图标资源。") from exc
    icon = Path(data_dir).resolve() / "icons" / ("ChatGPT-Switch-" + hashlib.sha256(icon_data).hexdigest()[:12] + ".ico")
    if not icon.is_file() or icon.read_bytes() != icon_data:
        atomic_write(icon, icon_data)
    folder = ps_string(desktop_dir) if desktop_dir is not None else "[Environment]::GetFolderPath('DesktopDirectory')"
    destination_name = "ChatGPT Switch.lnk"
    result = powershell("""
$ErrorActionPreference='Stop'
$desktop = %s
if (-not $desktop -or -not (Test-Path -LiteralPath $desktop -PathType Container)) { throw 'Desktop folder is unavailable.' }
$destination = [IO.Path]::Combine($desktop, %s)
$target = %s
$iconPath = %s
$shell = New-Object -ComObject WScript.Shell
if (Test-Path -LiteralPath $destination) {
    $existing = $shell.CreateShortcut($destination)
    if ($existing.TargetPath -and ([IO.Path]::GetFullPath($existing.TargetPath) -ine [IO.Path]::GetFullPath($target))) {
        throw 'A different shortcut already uses the name ChatGPT Switch.lnk; it was preserved.'
    }
}
$link = $shell.CreateShortcut($destination)
$link.TargetPath = $target
$link.Arguments = ''
$link.WorkingDirectory = [IO.Path]::GetDirectoryName($target)
$link.IconLocation = $iconPath + ',0'
$link.Description = 'ChatGPT Switch'
$link.Save()
$verified = $shell.CreateShortcut($destination)
if ([IO.Path]::GetFullPath($verified.TargetPath) -ine [IO.Path]::GetFullPath($target)) { throw 'Shortcut verification failed.' }
[pscustomobject]@{path=$destination} | ConvertTo-Json -Compress
""" % (folder, ps_string(destination_name), ps_string(str(executable)), ps_string(icon)))
    if result.returncode:
        if "different shortcut" in result.stderr:
            raise ConfigError("桌面已有指向其他应用的 ChatGPT Switch.lnk，请先重命名该图标后重试。")
        raise ConfigError("ChatGPT Switch 桌面图标创建失败，请确认桌面文件夹可写。")
    path = json.loads(result.stdout.strip().lstrip("\ufeff"))["path"]
    notify_shortcut(path)
    return {"message": "已创建 ChatGPT Switch 桌面图标。", "path": path}

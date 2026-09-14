"""Read-only version checks and channel-preserving updates with scoped proxies."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from .config import ConfigError
from .installer import Cancelled, CLI_PS, CLI_SH, proxy_env, proxy_settings
from .platforms import run_capture
from .convenience import ps_string


STORE_ID = "9PLM9XGG6VKS"
NPM_REGISTRY = "https://registry.npmjs.org"


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+){1,3}", value):
        raise ConfigError("版本信息无法确认，请重新检测。")
    parts = tuple(int(x) for x in value.split('.'))
    return parts + (0,) * (4 - len(parts))


def winget_command(executable, action, settings, package_id=STORE_ID, source="msstore"):
    command = [executable, action, "--id", package_id, "--exact", "--source", source,
               "--accept-source-agreements", "--disable-interactivity"]
    if action == "upgrade":
        command += ["--accept-package-agreements"]
    if settings["mode"] == "custom":
        command += ["--proxy", settings["address"]]
    elif settings["mode"] == "direct":
        command += ["--no-proxy"]
    return command


def parse_store_versions(output, package_id=STORE_ID):
    # Locate the exact ID, not translated headings or whitespace column offsets.
    clean = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", output)
    rows = [line for line in clean.splitlines() if re.search(r"(?<!\S)" + re.escape(package_id) + r"(?!\S)", line)]
    if len(rows) != 1:
        raise ConfigError("商店未返回可确认的版本信息，请重新检测。")
    fields = rows[0].split(package_id, 1)[1].split()
    if not fields:
        raise ConfigError("商店未返回已安装版本。")
    current = fields[0]
    version_tuple(current)
    latest = current
    if len(fields) > 1 and fields[1].lower() not in {"msstore", "winget"}:
        latest = fields[1]
        version_tuple(latest)
    return current, latest


def detect_cli_owner(path):
    """Identify ownership from the selected binary/shim's actual location."""
    entry = Path(path)
    resolved = entry.resolve()
    parts = [p.lower() for p in resolved.parts]
    for kind in ("caskroom", "cellar"):
        if kind in parts:
            at = parts.index(kind)
            if len(parts) > at + 2 and parts[at + 1] == "codex":
                return {"channel": "brew", "kind": "cask" if kind == "caskroom" else "formula", "package": "codex"}
    # Scoop shims are real EXEs, not links; their .shim metadata points at the app.
    shim = entry.with_suffix('.shim')
    if shim.is_file():
        match = re.search(r'^path\s*=\s*"([^"\r\n]+)"', shim.read_text('utf-8'), re.M)
        if match:
            resolved = Path(os.path.expandvars(match[1])).resolve()
    parts = [p.lower() for p in resolved.parts]
    if "apps" in parts:
        at = parts.index("apps")
        if len(parts) > at + 2 and parts[at + 1] == "codex":
            root = Path(*resolved.parts[:at])
            current = root / 'apps/codex/current'
            manifest = current / 'manifest.json'
            info = current / 'install.json'
            if manifest.is_file() and info.is_file():
                installed = json.loads(info.read_text('utf-8-sig'))
                metadata = json.loads(manifest.read_text('utf-8-sig'))
                # Custom buckets may package a different app with the same name.
                if metadata.get('homepage', '').rstrip('/') == 'https://github.com/openai/codex':
                    return {"channel": "scoop", "root": str(root), "bucket": installed.get('bucket'),
                            "current": metadata['version'], "package": "codex"}
    if any(p.startswith('openai.codex_') for p in parts) and 'winget' in parts:
        return {"channel": "winget", "package": "OpenAI.Codex"}
    known = [Path.home() / ".local/bin", Path.home() / ".codex/bin",
             Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/OpenAI/Codex/bin"]
    if any(entry.parent.resolve() == p.resolve() for p in known):
        return {"channel": "standalone"}
    return {"channel": "unknown"}


def linux_package_owner(path):
    """Recognize distro-managed binaries without silently moving them to npm."""
    for manager, command, channel in [('dpkg-query',['-S',str(path)],'apt'),
                                     ('rpm',['-qf','--qf','%{NAME}',str(path)],'rpm'),
                                     ('pacman',['-Qoq',str(path)],'pacman')]:
        executable = shutil.which(manager)
        if not executable:
            continue
        result = run_capture([executable] + command, timeout=10)
        if result.returncode == 0:
            name = result.stdout.strip().split(':',1)[0] if manager == 'dpkg-query' else result.stdout.strip()
            if re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9+._-]*', name):
                return channel, name
    return None, None


def checked_winget(command, settings):
    result = run_capture(command, timeout=45, env=proxy_env(settings))
    if "ProxyCommandLineOptions" in result.stdout + result.stderr:
        raise ConfigError("WinGet 未启用代理参数。可选择「跟随系统」重试；手动代理/直连需管理员先运行 winget settings --enable ProxyCommandLineOptions。")
    if result.returncode:
        raise ConfigError("更新源检测失败（退出码 " + str(result.returncode) + "），请检查网络或代理后重试。")
    return result.stdout


def network_env(settings):
    env = proxy_env(settings)
    if settings['mode'] == 'system':
        # Command-line managers don't all read Windows/macOS system proxies.
        for protocol, address in urllib.request.getproxies().items():
            if protocol in {'http','https'}:
                env.setdefault(protocol.upper() + '_PROXY', address)
    elif settings['mode'] == 'direct':
        env['ALL_PROXY'] = env['all_proxy'] = ''
    return env


def scoop_runtime(installed):
    root = Path(installed['root'])
    script = root / 'apps/scoop/current/bin/scoop.ps1'
    if script.is_file():
        return root, script, False
    launcher = shutil.which('scoop.ps1')
    if launcher:
        # Official shim launches the script under this same Scoop root.
        for parent in Path(launcher).parents:
            candidate = parent / 'apps/scoop/current/bin/scoop.ps1'
            if candidate.is_file():
                return parent, candidate, True
    raise ConfigError('未找到此安装对应的 Scoop，请修复原安装器。')


def item(current="", latest="", status="unchecked", message="", channel=""):
    return {"current": current, "latest": latest, "status": status, "message": message, "channel": channel}


class UpdateManager:
    def __init__(self, integration, installer):
        self.integration = integration
        self.installer = installer
        self.state = {"checkedAt": None, "settings": None, "cli": item(), "desktop": item()}

    def cli_installation(self):
        path = self.integration.cli_path()
        if not path:
            return None
        entry = Path(path)
        owner = detect_cli_owner(path)
        if owner["channel"] not in {"unknown", "standalone"}:
            if "current" not in owner:
                result = run_capture([str(entry), "--version"], timeout=15)
                match = re.search(r"\bcodex(?:-cli)?\s+(\d+\.\d+\.\d+)\b", result.stdout)
                if result.returncode or not match:
                    raise ConfigError("无法读取当前 Codex CLI 版本。")
                owner['current'] = match[1]
            return {"path": path, **owner}
        # Inspect the package owning the selected shim, rather than another npm prefix.
        package = entry.parent / "node_modules/@openai/codex/package.json"
        if not package.is_file():
            resolved = entry.resolve()
            for folder in list(resolved.parents)[:4]:
                candidate = folder / "package.json"
                if candidate.is_file():
                    try:
                        if json.loads(candidate.read_text("utf-8")).get("name") == "@openai/codex":
                            package = candidate
                            break
                    except (ValueError, OSError):
                        pass
        if package.is_file():
            metadata = json.loads(package.read_text("utf-8"))
            if metadata.get("name") == "@openai/codex":
                version_tuple(metadata["version"])
                # .../<prefix>/node_modules/@openai/codex or <prefix>/lib/node_modules/...
                prefix = package.parents[3]
                if prefix.name == "lib":
                    prefix = prefix.parent
                return {"path": path, "current": metadata["version"], "channel": "npm", "prefix": str(prefix)}
        if entry.suffix.lower() in {".cmd", ".bat", ".ps1"}:
            raise ConfigError("无法识别 CLI 启动脚本的安装渠道，请使用原安装器更新。")
        result = run_capture([path, "--version"], timeout=15)
        match = re.search(r"\bcodex(?:-cli)?\s+(\d+\.\d+\.\d+)\b", result.stdout)
        if result.returncode or not match:
            raise ConfigError("无法读取当前 Codex CLI 版本。")
        if owner['channel'] == 'unknown' and self.integration.info['system'] == 'Linux':
            channel, package = linux_package_owner(entry.resolve())
            if channel:
                return {"path":path, "current":match[1], "channel":channel, "package":package}
        return {"path": path, "current": match[1], "channel": owner["channel"]}

    def latest_cli(self, settings):
        return self.fetch_json(NPM_REGISTRY + "/@openai%2fcodex/latest", settings)["version"]

    def fetch_json(self, url, settings):
        proxies = None if settings["mode"] == "system" else {} if settings["mode"] == "direct" else {"http": settings["address"], "https": settings["address"]}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
        request = urllib.request.Request(url, headers={"User-Agent": "ChatGPT-Switch"})
        with opener.open(request, timeout=20) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ConfigError("官方版本响应过大，请稍后重试。")
        return json.loads(raw)

    def check_cli(self, settings):
        installed = self.cli_installation()
        if not installed:
            return item(status="missing", message="尚未安装，请使用上方安装功能。")
        if installed["channel"] in {"unknown", "apt", "rpm", "pacman"}:
            return item(current=installed["current"], status="unsupported", channel=installed['channel'],
                        message="请通过原系统包管理器更新此 CLI。" if installed['channel']!='unknown' else "无法可靠识别安装渠道，请通过原安装器更新。")
        channel = installed["channel"]
        if channel == "winget":
            winget = self.integration.detect().get('winget')
            if not winget:
                raise ConfigError("未找到原安装器 WinGet。")
            _, latest = parse_store_versions(checked_winget(winget_command(winget, 'list', settings, 'OpenAI.Codex', 'winget'), settings), 'OpenAI.Codex')
        elif channel == "brew":
            latest_info = self.fetch_json('https://formulae.brew.sh/api/' + installed['kind'] + '/codex.json', settings)
            latest = latest_info['version'] if installed['kind'] == 'cask' else latest_info['versions']['stable']
        elif channel == "scoop":
            if installed.get('bucket') != 'main':
                return item(installed['current'], status='unsupported', channel='scoop', message='检测到自定义 Scoop bucket，请通过该 bucket 更新。')
            runtime_root, _, _ = scoop_runtime(installed)
            if (runtime_root / 'apps/scoop/current/config.json').is_file():
                return item(installed['current'], status='unsupported', channel='scoop', message='此 Scoop 使用便携配置，请通过 Scoop 自己选择代理并更新。')
            latest = self.fetch_json('https://raw.githubusercontent.com/ScoopInstaller/Main/master/bucket/codex.json', settings)['version']
        else:
            latest = self.latest_cli(settings)
        available = version_tuple(latest) > version_tuple(installed["current"])
        return item(installed["current"], latest, "available" if available else "current",
                    "发现新版本" if available else "当前版本无需更新", installed["channel"])

    def check_desktop(self, detected, settings):
        if not detected.get("desktop"):
            return item(status="missing", message="尚未安装，请使用上方安装功能。")
        if self.integration.info["system"] == "Darwin":
            return self.check_mac_desktop(detected['desktop'], settings)
        if self.integration.info["system"] != "Windows":
            channel, package = linux_package_owner(detected['desktop']['executable'])
            return item(status="unsupported", channel=channel or 'manual',
                        message="请通过 " + (channel.upper() + ' 更新 ' + package if channel else '桌面应用内的检查更新') + "。")
        if not detected.get("winget"):
            return item(status="unsupported", message="需要 WinGet 才能检测和更新商店应用。")
        package_id, current, latest = self.store_versions(detected, settings)
        available = version_tuple(latest) > version_tuple(current)
        result = item(current, latest, "available" if available else "current",
                      "发现新版本" if available else "商店当前未提供更新", "msstore")
        result['packageId'] = package_id
        return result

    def store_versions(self, detected, settings):
        target = detected['desktop']
        app_id = target.get('appId', '')
        if app_id.startswith('OpenAI.Codex_'):
            package_id = STORE_ID
            output = checked_winget(winget_command(detected['winget'], 'list', settings), settings)
        elif app_id.startswith('OpenAI.ChatGPT-Desktop_') or app_id.startswith('OpenAI.ChatGPT_'):
            command = winget_command(detected['winget'], 'list', settings)
            at = command.index('--id'); command[at:at+2] = ['--name','ChatGPT']
            output = checked_winget(command, settings)
            ids = re.findall(r'(?<!\S)([A-Z0-9]{12})(?!\S)',output)
            if len(ids) != 1:
                raise ConfigError('无法唯一识别此桌面版本对应的商店包。请使用应用内更新。')
            package_id = ids[0]
        else:
            raise ConfigError('此桌面应用的发行渠道无法确认，请使用应用内更新。')
        current, latest = parse_store_versions(output, package_id)
        if target.get('version') and version_tuple(current) != version_tuple(target['version']):
            raise ConfigError('商店记录和正在使用的桌面安装版本不一致，请重新检测。')
        return package_id, current, latest

    def check_mac_desktop(self, target, settings):
        import plistlib
        bundle = Path(target['bundle'])
        with (bundle / 'Contents/Info.plist').open('rb') as stream:
            metadata = plistlib.load(stream)
        current = metadata.get('CFBundleShortVersionString', '')
        brew = shutil.which('brew')
        if brew:
            result = run_capture([brew, 'list', '--cask', '--full-name'], timeout=15)
            # Homebrew receipts must own this bundle; a side-by-side manual app
            # must not be replaced just because another cask is installed.
            if result.returncode == 0 and 'codex-app' in result.stdout.splitlines():
                receipt = run_capture([brew, 'info', '--json=v2', '--cask', 'codex-app'], timeout=20)
                if receipt.returncode == 0:
                    data = json.loads(receipt.stdout)['casks'][0]
                    artifact_match = any(bundle.name in a.get('app', []) for a in data.get('artifacts', []) if isinstance(a, dict))
                    installed_versions = data.get('installed')
                    if isinstance(installed_versions,str): installed_versions=[installed_versions]
                    if current in (installed_versions or []) and bundle.parent == Path('/Applications') and data.get('tap') == 'homebrew/cask' and artifact_match:
                        latest = self.fetch_json('https://formulae.brew.sh/api/cask/codex-app.json', settings)['version']
                        version_tuple(latest); version_tuple(current)
                        return item(current, latest, 'available' if version_tuple(latest)>version_tuple(current) else 'current', '通过 Homebrew 更新桌面端', 'brew')
        return item(current, status='unsupported', message='检测到手动安装的应用，请使用应用内检查更新。', channel='manual')

    def check(self, settings, progress, cancel):
        settings = proxy_settings(settings.get("mode", "direct"), settings.get("address", ""))
        detected = self.integration.detect()
        result = {"checkedAt": time.time(), "settings": settings}
        for component in ("cli", "desktop"):
            self.installer._check_cancel(cancel)
            progress("正在检查 " + ("Codex CLI" if component == "cli" else "ChatGPT 桌面端") + " 更新…")
            try:
                result[component] = self.check_cli(settings) if component == "cli" else self.check_desktop(detected, settings)
            except (ConfigError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
                message = str(exc) if isinstance(exc, ConfigError) else "检测失败，请检查网络和代理后重试。"
                result[component] = item(status="error", message=message)
        self.installer._check_cancel(cancel)
        self.state = result
        count = sum(result[c]["status"] == "available" for c in ("cli", "desktop"))
        failed = any(result[c]["status"] == "error" for c in ("cli", "desktop"))
        return {"message": ("发现 " + str(count) + " 个组件可更新。" if count else "版本检测完成。") + ("部分组件检测失败，请查看更新区。" if failed else ""), "updates": result}

    def validate(self, selection):
        if not isinstance(selection, list) or not selection or any(c not in {"cli", "desktop"} for c in selection):
            raise ConfigError("请选择需要更新的组件。")
        if not self.state["checkedAt"] or time.time() - self.state["checkedAt"] > 1800:
            raise ConfigError("请先重新检测更新。")
        if any(self.state[c]["status"] != "available" for c in selection):
            raise ConfigError("所选组件没有已确认的可用更新，请重新检测。")

    def npm_command(self, installed):
        node = shutil.which("node")
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        if not node or not npm:
            raise ConfigError("未找到 Node.js / npm，请修复 CLI 的原安装环境。")
        npm_path = Path(npm)
        script = npm_path.parent / "node_modules/npm/bin/npm-cli.js" if os.name == "nt" else npm_path.resolve()
        if not script.is_file():
            raise ConfigError("无法定位 npm 程序，请修复 Node.js 安装。")
        return [node, str(script), "install", "--global", "--prefix", installed["prefix"], "@openai/codex@latest", "--registry", NPM_REGISTRY]

    def update_cli(self, settings, progress, cancel):
        installed = self.cli_installation()
        if not installed:
            raise ConfigError("CLI 已被移除，请重新安装。")
        if installed['channel'] != self.state['cli']['channel']:
            raise ConfigError('CLI 安装渠道已变化，请重新检测更新。')
        env = network_env(settings)
        if installed["channel"] == "npm":
            # Override npmrc as well as env so direct/custom choices are effective.
            if settings["mode"] != "system":
                proxy = settings["address"] if settings["mode"] == "custom" else ""
                env.update(npm_config_proxy=proxy, npm_config_https_proxy=proxy,
                           npm_config_noproxy="localhost,127.0.0.1,::1" if proxy else "*")
            else:
                proxies = urllib.request.getproxies()
                if proxies.get('http'): env['npm_config_proxy'] = proxies['http']
                if proxies.get('https'): env['npm_config_https_proxy'] = proxies['https']
            self.installer.run(self.npm_command(installed), env, progress, cancel)
        elif installed["channel"] == "winget":
            detected = self.integration.detect()
            command = winget_command(detected['winget'], 'upgrade', settings, 'OpenAI.Codex', 'winget')
            self.installer.run(command, env, progress, cancel)
        elif installed["channel"] == "brew":
            brew = shutil.which('brew')
            if not brew:
                raise ConfigError('未找到原安装器 Homebrew。')
            env['HOMEBREW_NO_AUTO_UPDATE'] = '1'
            env['HOMEBREW_NO_INSTALL_CLEANUP'] = '1'
            self.installer.run([brew, 'upgrade', '--' + installed['kind'], 'codex'], env, progress, cancel)
        elif installed["channel"] == "scoop":
            self.update_scoop(installed, settings, progress, cancel)
        elif installed["channel"] == "standalone":
            windows = self.integration.info["system"] == "Windows"
            script = self.installer.download(CLI_PS if windows else CLI_SH,
                self.installer.cache / ("codex-update.ps1" if windows else "codex-update.sh"), settings, progress, cancel)
            ps = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe")
            if windows:
                import base64
                network = ''
                if settings['mode'] == 'direct':
                    network = '[Net.WebRequest]::DefaultWebProxy = New-Object Net.WebProxy; '
                elif settings['mode'] == 'custom':
                    network = '$proxy = New-Object Net.WebProxy(' + ps_string(settings['address']) + '); [Net.WebRequest]::DefaultWebProxy=$proxy; $PSDefaultParameterValues["Invoke-WebRequest:Proxy"]=' + ps_string(settings['address']) + '; $PSDefaultParameterValues["Invoke-RestMethod:Proxy"]=' + ps_string(settings['address']) + '; '
                code = "$ErrorActionPreference='Stop'; " + network + '& ' + ps_string(script) + '; if (-not $?) { exit 1 }'
                command = [ps, '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', base64.b64encode(code.encode('utf-16le')).decode('ascii')]
            else:
                command = ["/bin/sh", str(script)]
            env["CODEX_NON_INTERACTIVE"] = "1"
            self.installer.run(command, env, progress, cancel)
        else:
            raise ConfigError("CLI 安装渠道发生变化，请重新检测。")

    def update_scoop(self, installed, settings, progress, cancel):
        if installed.get('bucket') != 'main':
            raise ConfigError('请使用原 Scoop bucket 更新。')
        root, script, global_install = scoop_runtime(installed)
        if (root / 'apps/scoop/current/config.json').is_file():
            raise ConfigError('此 Scoop 使用便携配置，请通过 Scoop 自己更新并选择代理。')
        ps = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
        env = network_env(settings)
        original = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'scoop/config.json'
        config = json.loads(original.read_text('utf-8-sig')) if original.is_file() else {}
        config['root_path'] = str(root)
        config['cache_path'] = str(root / 'cache')
        if settings['mode'] != 'system':
            config['proxy'] = settings['address'] if settings['mode'] == 'custom' else 'none'
        # Scoop reads proxy from its config; use a child-only config instead of
        # modifying the user's persistent scoop config proxy.
        self.installer.cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='scoop-update-', dir=self.installer.cache) as temporary:
            config_dir = Path(temporary) / 'scoop'
            config_dir.mkdir()
            (config_dir / 'config.json').write_text(json.dumps(config), 'utf-8')
            env['XDG_CONFIG_HOME'] = temporary
            env['SCOOP'] = str(root)
            if global_install:
                env['SCOOP_GLOBAL'] = installed['root']
            # Refresh the selected official bucket before an update; an upstream
            # version check must not be followed by an outdated local manifest.
            git = shutil.which('git')
            bucket = root / 'buckets/main'
            if not git or not (bucket / '.git').exists():
                raise ConfigError('未找到 Scoop main bucket 或 Git，请先修复原 Scoop 安装。')
            remote = run_capture([git, '-C', str(bucket), 'remote', 'get-url', 'origin'], timeout=10)
            if remote.returncode or remote.stdout.strip().lower().rstrip('/').removesuffix('.git') != 'https://github.com/scoopinstaller/main':
                raise ConfigError('Scoop main bucket 使用自定义来源，请通过原安装器更新。')
            command = [git, '-C', str(bucket)]
            if settings['mode'] != 'system':
                command += ['-c','http.proxy=' + (settings['address'] if settings['mode']=='custom' else '')]
            self.installer.run(command + ['pull','--ff-only'], env, progress, cancel)
            self.installer.run([ps, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script), 'update', 'codex'] + (['--global'] if global_install else []), env, progress, cancel)

    def update(self, selection, settings, progress, cancel):
        self.validate(selection)
        settings = proxy_settings(settings.get("mode", "direct"), settings.get("address", ""))
        completed, errors = [], []
        for component in dict.fromkeys(selection):
            self.installer._check_cancel(cancel)
            expected = self.state[component]["latest"]
            progress("正在更新 " + ("Codex CLI" if component == "cli" else "ChatGPT 桌面端") + "…")
            try:
                if component == "cli":
                    self.update_cli(settings, progress, cancel)
                    installed = self.cli_installation()
                    if installed['channel'] == 'npm':
                        node = shutil.which('node')
                        entry = Path(installed['prefix']) / ('node_modules' if os.name == 'nt' else 'lib/node_modules') / '@openai/codex/bin/codex.js'
                        result = run_capture([node, str(entry), '--version'], timeout=15)
                        match = re.search(r'\bcodex-cli\s+(\d+\.\d+\.\d+)\b', result.stdout)
                        if result.returncode or not match:
                            raise ConfigError('更新后 CLI 版本验证失败，请重新检测。')
                        current = match[1]
                    else:
                        current = installed["current"]
                else:
                    detected = self.integration.detect()
                    if not detected.get("desktop"):
                        raise ConfigError("桌面安装状态已变化，请重新检测。")
                    if self.state['desktop']['channel'] == 'brew' and self.integration.info['system'] == 'Darwin':
                        if self.check_mac_desktop(detected['desktop'], settings)['channel'] != 'brew':
                            raise ConfigError('桌面安装渠道已变化，请重新检测。')
                        env = network_env(settings)
                        env['HOMEBREW_NO_INSTALL_CLEANUP'] = '1'
                        self.installer.run([shutil.which('brew'), 'upgrade', '--cask', 'codex-app'], env, progress, cancel)
                        current = self.check_desktop(self.integration.detect(), settings)['current']
                    elif self.state['desktop']['channel'] == 'msstore' and detected.get('winget'):
                        package_id, _, _ = self.store_versions(detected, settings)
                        if package_id != self.state['desktop'].get('packageId', STORE_ID):
                            raise ConfigError('桌面商店包已变化，请重新检测。')
                        self.installer.run(winget_command(detected["winget"], "upgrade", settings, package_id), proxy_env(settings), progress, cancel)
                        # Verify installed AppX metadata without depending on a
                        # second network query after the update has succeeded.
                        current = self.integration.desktop_target().get('version', '')
                    else:
                        raise ConfigError('桌面安装渠道已变化，请重新检测。')
                if version_tuple(current) < version_tuple(expected):
                    raise ConfigError("命令已结束，但安装版本尚未达到检测到的新版本，请重新检测。")
                self.state[component] = item(current, expected, "current", "已更新并核验版本", self.state[component]["channel"])
                completed.append(component)
                progress(component + " 更新完成：" + current)
            except Cancelled as exc:
                self.state[component] = item(self.state[component]['current'], expected,
                    status='error', message='更新已取消，请重新检测实际安装状态。', channel=self.state[component]['channel'])
                raise Cancelled('更新已取消；已完成的组件会保留，请重新检测实际安装状态。') from exc
            except (ConfigError, OSError, ValueError, KeyError, TypeError) as exc:
                message = str(exc) if isinstance(exc, ConfigError) else "更新未完成，请重新检测安装状态。"
                self.state[component] = item(self.state[component]['current'], expected,
                                             status="error", message=message, channel=self.state[component]['channel'])
                errors.append(component + "：" + message)
                progress(errors[-1])
        if errors:
            raise ConfigError(("部分组件已完成更新。" if completed else "") + "；".join(errors))
        return {"message": "更新完成，已核验所选组件的安装版本。", "completed": completed}


class FakeUpdateManager(UpdateManager):
    def check(self, settings, progress, cancel):
        self.state = {"checkedAt": time.time(), "settings": proxy_settings(**settings),
                      "cli": item("1.0.0", "1.1.0", "available", "测试：发现新版本", "npm"),
                      "desktop": item("26.1.0.0", "26.2.0.0", "available", "测试：发现新版本", "msstore")}
        progress("模拟更新检测，没有访问更新服务。")
        return {"message": "测试：发现 2 个可用更新。"}

    def update(self, selection, settings, progress, cancel):
        self.validate(selection)
        for component in selection:
            self.state[component]["current"] = self.state[component]["latest"]
            self.state[component].update(status="current", message="模拟更新完成")
        progress("模拟更新完成，没有安装软件。")
        return {"message": "模拟更新完成。"}

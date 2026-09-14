from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

try:
    import tomllib
except ImportError:
    from .vendor import tomli as tomllib


DEFAULT_URL = "https://api.yiyinfaith.com/v1"
DEFAULT_KEY = "MY_API_KEY"
KEEP_BACKUPS = 5
MODES = {"account", "thirdparty"}  # Account comments API lines; third-party enables them.


class ConfigError(Exception):
    pass


class SetupRequired(ConfigError):
    pass


@dataclass
class Document:
    path: Path
    original: bytes | None
    text: str
    encoding: str = "utf-8"
    bom: bytes = b""

    @classmethod
    def read(cls, path: Path):
        if not path.exists():
            return cls(path, None, "")
        raw = path.read_bytes()
        encoding, bom = "utf-8", b""
        if raw.startswith((b"\xff\xfe\0\0", b"\0\0\xfe\xff")):
            raise ConfigError("不支持 UTF-32 配置，请使用 UTF-8。")
        for prefix, codec in [(b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")]:
            if raw.startswith(prefix):
                encoding, bom = codec, prefix
                break
        try:
            return cls(path, raw, raw[len(bom):].decode(encoding), encoding, bom)
        except UnicodeError as exc:
            raise ConfigError("配置文件编码无法识别，未修改文件。") from exc

    @property
    def newline(self):
        match = re.search(r"\r\n|\n|\r", self.text)
        return match.group() if match else os.linesep

    def encode(self, text):
        return self.bom + text.encode(self.encoding)

    def unchanged(self):
        return (self.path.read_bytes() if self.path.exists() else None) == self.original


class BackupStore:
    """Only owns exact filenames under app/backups. Never recursively deletes."""
    def __init__(self, app_root: Path):
        self.root = app_root.resolve() / "backups"
        self.lock = threading.RLock()

    def folder(self, kind):
        if kind not in {"config", "env"}:
            raise ConfigError("未知备份类型。")
        return self.root / kind

    def owned(self, kind):
        folder = self.folder(kind)
        if not folder.exists():
            return []
        return sorted((p for p in folder.iterdir() if p.is_file() and not p.is_symlink()
                       and re.fullmatch(r"\d{8}-\d{6}-\d{6}-[0-9a-f]{12}\.bak", p.name)),
                      key=lambda p: p.name, reverse=True)

    def prune(self, kind):
        with self.lock:
            for path in self.owned(kind)[KEEP_BACKUPS:]:
                try:
                    path.unlink()
                except OSError as exc:
                    raise ConfigError("旧备份无法清理，请检查软件 backups 文件夹的权限。") from exc

    def save(self, kind, data):
        if data is None:
            return None
        with self.lock:
            folder = self.folder(kind)
            folder.mkdir(parents=True, exist_ok=True)
            name = datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + uuid4().hex[:12] + ".bak"
            target = folder / name
            try:
                fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                self.prune(kind)
            except Exception:
                if target.exists():
                    target.unlink()
                raise
            return target

    def migrate_legacy(self, config_dir):
        """Move only our v1 exact-name backups; retain unrelated .bak files."""
        moved = 0
        if not config_dir.exists():
            return moved
        for kind, prefix in [("config", "config.toml"), ("env", ".env")]:
            pattern = re.compile(re.escape(prefix) + r"\.switch-backup-(\d{8}-\d{6})-(\d{3})-([0-9a-f]{8})\.bak")
            # Ascending import order keeps the newest original backups after pruning.
            for path in sorted(config_dir.iterdir(), key=lambda p: p.name):
                if path.is_file() and not path.is_symlink() and pattern.fullmatch(path.name):
                    raw = path.read_bytes()
                    target = self.save(kind, raw)
                    if target and target.read_bytes() == raw:
                        path.unlink()
                        moved += 1
            self.prune(kind)
        return moved

    def summary(self):
        return {"path": str(self.root), "limit": KEEP_BACKUPS,
                "configCount": len(self.owned("config")), "envCount": len(self.owned("env"))}


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".switch-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            shutil.copymode(str(path), temp)
        os.replace(temp, str(path))
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def commit_documents(backups, changes, writer=atomic_write):
    """Check both originals, back up centrally, and roll back earlier writes on failure."""
    changed = [(kind, doc, data) for kind, doc, data in changes if data != doc.original]
    if not changed:
        for kind, _, _ in changes:
            backups.prune(kind)
        return {"changed": False, "backups": []}
    with backups.lock:
        if any(not doc.unchanged() for _, doc, _ in changes):
            raise ConfigError("文件被其他程序修改，请刷新后再试。")
        saved = []
        for kind, doc, _ in changed:
            target = backups.save(kind, doc.original)
            if target:
                saved.append(str(target))
        written = []
        try:
            for _, doc, data in changed:
                if not doc.unchanged():
                    raise ConfigError("文件被其他程序修改，请刷新后再试。")
                writer(doc.path, data)
                written.append((doc, data))
            if any(doc.path.read_bytes() != data for _, doc, data in changed):
                raise ConfigError("写入后文件又发生变化，请检查配置。")
        except Exception as exc:
            conflicts = False
            for doc, written_data in reversed(written):
                try:
                    if doc.path.read_bytes() != written_data:
                        conflicts = True
                        continue
                    if doc.original is None:
                        doc.path.unlink()
                    else:
                        atomic_write(doc.path, doc.original)
                except OSError:
                    conflicts = True
            if conflicts:
                raise ConfigError("保存未完成，部分文件发生外部变化；请使用 backups 中的备份检查恢复。") from exc
            if isinstance(exc, ConfigError):
                raise
            raise ConfigError("保存失败，已恢复本次先前写入的文件，请检查目录权限。") from exc
        return {"changed": True, "backups": saved}


ASSIGNMENT = re.compile(r'''^(?P<indent>[ \t]*)(?P<comment>\#[ \t]*)?(?P<key>base_url|env_key)(?P<equal>[ \t]*=[ \t]*)(?P<value>"(?:[^"\\]|\\.)*"|'[^']*')(?P<tail>[ \t]*(?:\#.*)?)$''')
ANY_TARGET = re.compile(r"^[ \t]*#?[ \t]*(base_url|env_key)[ \t]*=")
ROOT_PROVIDER = re.compile(r'''^[ \t]*model_provider[ \t]*=[ \t]*(?:"(?:[^"\\]|\\.)*"|'[^']*')[ \t]*(?:#.*)?$''')


def validate_url(value):
    from urllib.parse import urlsplit
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.fragment and not any(c.isspace() for c in value)
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ConfigError("base_url 必须是完整的 HTTP 或 HTTPS 地址，例如 https://api.example.com/v1。")


def validate_key(value):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""):
        raise ConfigError("env_key 必须是有效的变量名，只能包含字母、数字和下划线，且不能以数字开头。")
    if value.upper() in {"PATH", "HOME", "USERPROFILE", "CODEX_HOME", "SYSTEMROOT", "COMSPEC", "PYTHONPATH", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES"}:
        raise ConfigError("请使用 API 专用变量名，例如 MY_API_KEY，不要使用系统变量名。")


def toml_string(value):
    return json.dumps(value, ensure_ascii=False)


def logical_lines(text):
    """Identify TOML structural lines without treating multiline string contents as keys."""
    state = None
    for index, raw in enumerate(text.splitlines(keepends=True)):
        content = raw.rstrip("\r\n")
        structural = state is None
        i = 0
        quote = None
        while i < len(content):
            if state:
                if content.startswith(state, i) and (state == "'''" or i == 0 or content[i - 1] != "\\"):
                    i += 3
                    state = None
                else:
                    i += 1
            elif quote:
                if content[i] == "\\" and quote == '"':
                    i += 2
                elif content[i] == quote:
                    quote = None
                    i += 1
                else:
                    i += 1
            elif content[i] == "#":
                break
            elif content.startswith('"""', i) or content.startswith("'''", i):
                state = content[i:i + 3]
                i += 3
            elif content[i] in "\"'":
                quote = content[i]
                i += 1
            else:
                i += 1
        yield index, raw, content, structural


class ConfigStore:
    def __init__(self, config_dir, backups):
        self.path = Path(config_dir) / "config.toml"
        self.env_path = Path(config_dir) / ".env"
        self.backups = backups

    def inspect(self):
        doc = Document.read(self.path)
        try:
            tree = tomllib.loads(doc.text)
        except (tomllib.TOMLDecodeError, ValueError) as exc:
            raise ConfigError("config.toml 语法不正确，请先修复原文件；未修改配置。") from exc
        provider = tree.get("model_provider")
        if provider is not None and not isinstance(provider, str):
            raise ConfigError("model_provider 必须是字符串。")
        providers = tree.get("model_providers", {})
        if not isinstance(providers, dict):
            raise ConfigError("model_providers 必须是 TOML 配置表。")
        lines, assignments = list(logical_lines(doc.text)), {}
        in_target, start, end, root_index, first_section = False, None, None, None, None
        for index, raw, content, structural in lines:
            if not structural:
                continue
            if content.lstrip().startswith("[") and not content.lstrip().startswith("#"):
                if first_section is None:
                    first_section = index
                if in_target:
                    end = index
                in_target = False
                try:
                    section_tree = tomllib.loads(content + "\n__glass_switch_marker = true")
                    section_provider = section_tree.get("model_providers", {}).get(provider, {})
                    in_target = isinstance(section_provider, dict) and section_provider.get("__glass_switch_marker") is True
                except (tomllib.TOMLDecodeError, AttributeError, TypeError):
                    pass
                if in_target:
                    if start is not None:
                        raise ConfigError("目标 provider 配置段重复，未修改文件。")
                    start, end = index, None
                continue
            if first_section is None and ROOT_PROVIDER.fullmatch(content):
                root_index = index
            if in_target and ANY_TARGET.match(content):
                match = ASSIGNMENT.fullmatch(content)
                if not match:
                    raise ConfigError("base_url 或 env_key 的格式无法安全识别。")
                name = match["key"]
                if name in assignments:
                    raise ConfigError("目标配置行重复，请先删除重复的 base_url 或 env_key。")
                try:
                    value = tomllib.loads("x = " + match["value"])["x"]
                except tomllib.TOMLDecodeError as exc:
                    raise ConfigError("目标配置值无法识别。") from exc
                assignments[name] = {"index": index, "value": value, "commented": match["comment"] is not None, "match": match}
        needs_setup = start is None or len(assignments) != 2
        mode = "setup"
        if not needs_setup:
            a, b = assignments["base_url"]["commented"], assignments["env_key"]["commented"]
            # Third-party mode enables both provider lines; account mode comments both.
            mode = "account" if a and b else "thirdparty" if not a and not b else "mixed"
        return {"document": doc, "tree": tree, "provider": provider, "assignments": assignments,
                "start": start, "end": end if end is not None else len(lines), "rootIndex": root_index,
                "firstSection": first_section, "needsSetup": needs_setup, "mode": mode,
                "baseUrl": assignments.get("base_url", {}).get("value", DEFAULT_URL),
                "envKey": assignments.get("env_key", {}).get("value", DEFAULT_KEY)}

    def public(self):
        state = self.inspect()
        return {k: state[k] for k in ("provider", "needsSetup", "mode", "baseUrl", "envKey")} | {
            "path": str(self.path), "envPath": str(self.env_path),
            "model": state["tree"].get("model", ""),
            "hasSecret": bool(read_env(Document.read(self.env_path).text, state["envKey"]))}

    def plan(self, mode, options=None):
        if mode not in MODES:
            raise ConfigError("请选择账号额度或第三方 API。")
        state = self.inspect()
        if state["needsSetup"] and options is None:
            raise SetupRequired("请先填写 API 地址、变量名和密钥。")
        doc, assignments = state["document"], state["assignments"]
        comment = mode == "account"
        source = doc.text.splitlines(keepends=True)
        replacements, insertions = {}, {}
        if options is None:
            for entry in assignments.values():
                i, match = entry["index"], entry["match"]
                line = source[i]
                if comment and not entry["commented"]:
                    line = line[:len(match["indent"])] + "#" + line[len(match["indent"]):]
                elif not comment and entry["commented"]:
                    pos = len(match["indent"])
                    line = line[:pos] + line[pos + len(match["comment"]):]
                replacements[i] = line
        else:
            url, key = options.get("baseUrl", "").strip(), options.get("envKey", "").strip()
            validate_url(url)
            validate_key(key)
            provider = state["provider"]
            if state["start"] is None:
                if not provider or provider in {"openai", "ollama", "lmstudio"}:
                    provider = "glass_switch"
                    count = 2
                    while provider in state["tree"].get("model_providers", {}):
                        provider = "glass_switch_" + str(count)
                        count += 1
                    provider_line = "model_provider = " + toml_string(provider) + doc.newline
                    if state["rootIndex"] is not None:
                        replacements[state["rootIndex"]] = provider_line
                    else:
                        insertions.setdefault(0, []).append(provider_line)
                block = "[model_providers." + toml_string(provider) + "]" + doc.newline
                block += 'name = "Custom API"' + doc.newline + 'wire_api = "responses"' + doc.newline + 'requires_openai_auth = true' + doc.newline
                for name, value in [("base_url", url), ("env_key", key)]:
                    block += ("#" if comment else "") + name + " = " + toml_string(value) + doc.newline
                insertions.setdefault(len(source), []).append(doc.newline + block)
            else:
                for name, value in [("base_url", url), ("env_key", key)]:
                    entry = assignments.get(name)
                    if entry:
                        match = entry["match"]
                        old = source[entry["index"]]
                        ending = old[len(old.rstrip("\r\n")):]
                        replacements[entry["index"]] = match["indent"] + ("#" if comment else "") + name + match["equal"] + toml_string(value) + match["tail"] + ending
                    else:
                        insertions.setdefault(state["end"], []).append(("#" if comment else "") + name + " = " + toml_string(value) + doc.newline)
        parts = []
        for index in range(len(source) + 1):
            if index in insertions:
                if parts and not parts[-1].endswith(("\r", "\n")):
                    parts.append(doc.newline)
                parts.extend(insertions[index])
            if index < len(source):
                parts.append(replacements.get(index, source[index]))
        transformed = "".join(parts)
        try:
            tomllib.loads(transformed)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError("生成的配置未通过语法校验，未写入文件。") from exc
        changes = []
        if options is not None:
            env = Document.read(self.env_path)
            secret = options.get("secret", "")
            if not secret:
                secret = read_env(env.text, key)
            if not secret or "\n" in secret or "\r" in secret or "\0" in secret:
                raise ConfigError("请填写单行 API 密钥；只有该变量已保存在 .env 中时才可以留空。")
            changes.append(("env", env, env.encode(update_env(env, key, secret))))
        changes.append(("config", doc, doc.encode(transformed)))
        return changes

    def save(self, mode, options=None):
        return commit_documents(self.backups, self.plan(mode, options))


ENV_ASSIGNMENT = re.compile(r"^(?P<prefix>[ \t]*(?:export[ \t]+)?)(?P<key>[A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*(?P<value>.*)$")


def read_env(text, key):
    value = None
    for line in text.splitlines():
        match = ENV_ASSIGNMENT.fullmatch(line)
        if not match or match["key"] != key:
            continue
        if value is not None:
            raise ConfigError(".env 中同名变量重复，请先整理后再保存。")
        raw = match["value"].strip()
        if raw.startswith("'"):
            end = raw.find("'", 1)
            if end == -1:
                raise ConfigError(".env 中目标变量的引号不完整。")
            value = raw[1:end]
        elif raw.startswith('"'):
            chars, i = [], 1
            while i < len(raw) and raw[i] != '"':
                if raw[i] == "\\" and i + 1 < len(raw):
                    i += 1
                    chars.append({"n": "\n", "r": "\r", "t": "\t"}.get(raw[i], raw[i]))
                else:
                    chars.append(raw[i])
                i += 1
            if i == len(raw):
                raise ConfigError(".env 中目标变量的引号不完整。")
            value = "".join(chars)
        else:
            value = re.split(r"[ \t]+#", raw, maxsplit=1)[0].rstrip()
    return value or ""


def quote_env(value):
    if "'" not in value:
        return "'" + value + "'"
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


def update_env(doc, key, value):
    read_env(doc.text, key)  # Reject duplicates before rewriting anything.
    lines, found = doc.text.splitlines(keepends=True), False
    for i, line in enumerate(lines):
        match = ENV_ASSIGNMENT.fullmatch(line.rstrip("\r\n"))
        if match and match["key"] == key:
            ending = line[len(line.rstrip("\r\n")):]
            lines[i] = match["prefix"] + key + "=" + quote_env(value) + ending
            found = True
    if not found:
        if lines and not lines[-1].endswith(("\r", "\n")):
            lines.append(doc.newline)
        lines.append(key + "=" + quote_env(value) + doc.newline)
    return "".join(lines)

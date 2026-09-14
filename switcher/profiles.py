from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from .config import ConfigError, Document, atomic_write, logical_lines, toml_string, tomllib, validate_key, validate_url, update_env, read_env


class ProfileStore:
    FIELDS = ("name", "baseUrl", "model", "envKey", "reviewModel", "reasoningEffort", "wireApi", "requiresAuth")

    def __init__(self, data_dir):
        self.path = Path(data_dir) / "profiles.json"
        # UI polling and edits use different HTTP threads. In particular, Windows
        # cannot atomically replace a file while another thread has it open.
        self.lock = threading.RLock()

    def read(self):
        with self.lock:
            if not self.path.exists():
                return []
            try:
                value = json.loads(self.path.read_text("utf-8"))
                if not isinstance(value, list) or any(not isinstance(p, dict) or not p.get("id") for p in value):
                    raise ValueError()
                return value
            except (ValueError, OSError) as exc:
                raise ConfigError("API 配置库无法读取，请检查软件 data/profiles.json。") from exc

    def public(self):
        return [{k: v for k, v in p.items() if k != "secret"} | {"hasSecret": bool(p.get("secret"))} for p in self.read()]

    def get(self, profile_id):
        for profile in self.read():
            if profile["id"] == profile_id:
                return profile
        raise ConfigError("没有找到这套 API 配置，请刷新后再试。")

    def save(self, values):
        with self.lock:
            return self._save(values)

    def _save(self, values):
        profiles = self.read()
        profile_id = values.get("id")
        previous = self.get(profile_id) if profile_id else {}
        if not profile_id and "envKey" not in values:
            values = {**values, "envKey": "MY_API_KEY"}
        value = {k: values.get(k, "").strip() for k in self.FIELDS if isinstance(values.get(k, ""), str)}
        if len(value) != len(self.FIELDS):
            raise ConfigError("配置字段必须是文本。")
        validate_url(value["baseUrl"])
        if not value["model"] or len(value["model"]) > 200 or any(c in value["model"] for c in "\r\n\0"):
            raise ConfigError("model 必填，请输入完整模型标识。")
        if value["envKey"]:
            validate_key(value["envKey"])
        if value["requiresAuth"] not in {"", "true", "false"}:
            raise ConfigError("认证选项必须为保持不变、true 或 false。")
        for k in ("name", "reviewModel", "reasoningEffort", "wireApi"):
            if len(value[k]) > 200 or any(c in value[k] for c in "\r\n\0"):
                raise ConfigError("参数必须为单行文本，且不超过 200 个字符。")
        secret = values.get("secret", "")
        if not isinstance(secret, str) or len(secret) > 8192 or any(c in secret for c in "\r\n\0"):
            raise ConfigError("密钥必须为单行文本。")
        value["name"] = value["name"] or (urlsplit(value["baseUrl"]).hostname + " / " + value["model"])
        value["secret"] = secret or previous.get("secret", "")
        value["id"] = profile_id or uuid4().hex
        profiles = [value if p["id"] == value["id"] else p for p in profiles]
        if not profile_id:
            profiles.append(value)
        self._write(profiles)
        return value["id"]

    def delete(self, profile_id):
        with self.lock:
            self.get(profile_id)
            self._write([p for p in self.read() if p["id"] != profile_id])

    def _write(self, profiles):
        atomic_write(self.path, json.dumps(profiles, ensure_ascii=False, indent=2).encode("utf-8"))
        try:
            self.path.chmod(0o600)
        except OSError:
            pass


def profile_plan(config, profile, mode="thirdparty"):
    """Apply this profile's fields and enable or comment API lines for the chosen mode."""
    if mode not in {"account", "thirdparty"}:
        raise ConfigError("请选择账号额度或第三方 API。")
    comment = mode == "account"
    validate_url(profile["baseUrl"])
    state = config.inspect()
    doc, lines = state["document"], state["document"].text.splitlines(keepends=True)
    replacements, inserts = {}, {}
    provider = state["provider"]
    new_provider = state["start"] is None
    root_changes = {"model": profile["model"]}
    if profile.get("reviewModel"):
        root_changes["review_model"] = profile["reviewModel"]
    if profile.get("reasoningEffort"):
        root_changes["model_reasoning_effort"] = profile["reasoningEffort"]
    if new_provider and (not provider or provider in {"openai", "ollama", "lmstudio"}):
        provider, suffix = "glass_switch", 2
        while provider in state["tree"].get("model_providers", {}):
            provider = "glass_switch_" + str(suffix)
            suffix += 1
        root_changes["model_provider"] = provider
    provider_changes = {"base_url": profile["baseUrl"]}
    if profile.get("envKey"):
        provider_changes["env_key"] = profile["envKey"]
    if profile.get("wireApi"):
        provider_changes["wire_api"] = profile["wireApi"]
    if profile.get("requiresAuth"):
        provider_changes["requires_openai_auth"] = profile["requiresAuth"] == "true"

    def serialized(value):
        return str(value).lower() if isinstance(value, bool) else toml_string(value)

    def replace_field(index, key, value, commented=False):
        content = lines[index].rstrip("\r\n")
        indent = content[:len(content) - len(content.lstrip(" \t"))]
        ending = lines[index][len(content):]
        tail_match = re.search(r'''(?:"(?:[^"\\]|\\.)*"|'[^']*'|true|false)\s*(?P<tail>\#.*)$''', content)
        tail = " " + tail_match["tail"] if tail_match else ""
        if '"""' in content or "'''" in content:
            raise ConfigError("目标参数使用多行字符串，无法安全覆盖。")
        replacements[index] = indent + ("#" if commented else "") + key + " = " + serialized(value) + tail + ending

    first = state["firstSection"] if state["firstSection"] is not None else len(lines)
    found = set()
    for index, raw, content, structural in logical_lines(doc.text):
        if index >= first:
            break
        if structural:
            for key, value in root_changes.items():
                if re.match(r"^[ \t]*" + re.escape(key) + r"[ \t]*=", content):
                    replace_field(index, key, value)
                    found.add(key)
    for key, value in root_changes.items():
        if key not in found:
            inserts.setdefault(first, []).append(key + " = " + serialized(value) + doc.newline)
    if new_provider:
        block = doc.newline + "[model_providers." + toml_string(provider) + "]" + doc.newline + 'name = "Custom API"' + doc.newline
        for key, value in provider_changes.items():
            block += ("#" if comment and key in {"base_url", "env_key"} else "") + key + " = " + serialized(value) + doc.newline
        inserts.setdefault(len(lines), []).append(block)
    else:
        found = set()
        for index, raw, content, structural in logical_lines(doc.text):
            if not structural or not state["start"] < index < state["end"]:
                continue
            for key, value in provider_changes.items():
                if re.match(r"^[ \t]*#?[ \t]*" + re.escape(key) + r"[ \t]*=", content):
                    if key in found:
                        raise ConfigError("目标参数重复，无法安全覆盖。")
                    replace_field(index, key, value, comment and key in {"base_url", "env_key"})
                    found.add(key)
        for key, value in provider_changes.items():
            if key not in found:
                inserts.setdefault(state["end"], []).append(("#" if comment and key in {"base_url", "env_key"} else "") + key + " = " + serialized(value) + doc.newline)
        # Preserve an omitted env_key's value while setting its comment state.
        if "env_key" not in provider_changes and "env_key" in state["assignments"]:
            entry = state["assignments"]["env_key"]
            if entry["commented"] != comment:
                position = len(entry["match"]["indent"])
                raw = lines[entry["index"]]
                replacements[entry["index"]] = (raw[:position] + "#" + raw[position:] if comment else
                    raw[:position] + raw[position + len(entry["match"]["comment"]):])
    output = []
    for index in range(len(lines) + 1):
        if index in inserts:
            if output and not output[-1].endswith(("\r", "\n")):
                output.append(doc.newline)
            output.extend(inserts[index])
        if index < len(lines):
            output.append(replacements.get(index, lines[index]))
    text = "".join(output)
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError("生成的配置未通过语法校验，未写入。") from exc
    changes = []
    if profile.get("secret"):
        key = profile.get("envKey") or state["assignments"].get("env_key", {}).get("value", "")
        if not key:
            raise ConfigError("此配置保存了密钥，但当前文件没有 env_key；请为这套 API 填写环境变量名。")
        validate_key(key)
        env = Document.read(config.env_path)
        changes.append(("env", env, env.encode(update_env(env, key, profile["secret"])) ))
    changes.append(("config", doc, doc.encode(text)))
    return changes

"""Non-secret preferences on disk; Report Keys only in the macOS Keychain."""
import json
import os
import re
import secrets
import subprocess
import threading
from pathlib import Path

SERVICE = "com.funsmith.local.max-revenue-report"


class PreferencesError(Exception):
    pass


def validate_key(key):
    if not isinstance(key, str):
        raise PreferencesError("请填写有效的 Report Key。")
    key = key.strip()
    if not key or len(key) > 4096 or any(c.isspace() for c in key):
        raise PreferencesError("请填写有效的 Report Key（不是 SDK Key）。")
    return key


class Keychain:
    def save(self, account, key):
        # Hex is sent on stdin to Apple's interactive client, never argv or a file.
        command = (f"add-generic-password -U -s {SERVICE} -a {account} "
                   f"-l MAX-Revenue-Report -X {key.encode('utf-8').hex()}\n")
        try:
            subprocess.run(["/usr/bin/security", "-i"], input=command, text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            if not secrets.compare_digest(self.read(account), key):
                raise PreferencesError("钥匙串保存未完成，请重试。")
        except (OSError, subprocess.TimeoutExpired):
            raise PreferencesError("无法访问 Mac 钥匙串，请解锁登录钥匙串后重试。") from None

    def read(self, account):
        try:
            result = subprocess.run(["/usr/bin/security", "find-generic-password",
                "-s", SERVICE, "-a", account, "-w"], text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            raise PreferencesError("无法读取 Mac 钥匙串，请解锁后重试。") from None
        if result.returncode:
            raise PreferencesError("无法读取所选 Report Key，请检查钥匙串或重新保存。")
        return result.stdout.rstrip("\n")


class Preferences:
    def __init__(self, directory=None, vault=None):
        self.directory = directory or Path.home() / "Library/Application Support/MAXRevenueReport"
        self.path = self.directory / "preferences.json"
        self.vault = vault or Keychain()
        self.lock = threading.RLock()

    def _load(self):
        if not self.path.exists():
            return {"profiles": [], "last_profile": "", "last_identifier": "",
                "apps": {"com.funjo.block.puzzle.arrow.escape": ["android"]}}
        try:
            result = json.loads(self.path.read_text())
            if not isinstance(result.get("profiles"), list) or not isinstance(result.get("apps"), dict):
                raise ValueError()
            return result
        except (OSError, ValueError, AttributeError):
            raise PreferencesError("本机偏好设置无法读取，请检查 preferences.json。") from None

    def _write(self, value):
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = self.directory / (".preferences-" + secrets.token_hex(8))
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temporary, self.path)
        except OSError:
            raise PreferencesError("本机偏好设置保存失败，请检查目录权限。") from None

    def public(self):
        with self.lock:
            return self._load()

    def save_profile(self, name, key):
        key = validate_key(key)
        name = str(name or "").strip()
        if not 1 <= len(name) <= 50 or any(ord(c) < 32 for c in name):
            raise PreferencesError("请为 Report Key 起一个 1–50 字的名称。")
        with self.lock:
            state = self._load()
            if any(profile["name"] == name for profile in state["profiles"]):
                raise PreferencesError("此名称已存在，请直接选择已有账号，或换一个名称。")
            if len(state["profiles"]) >= 50:
                raise PreferencesError("最多保存 50 个账号。")
            account = secrets.token_hex(16)
            self.vault.save(account, key)
            state["profiles"].append({"id": account, "name": name})
            state["last_profile"] = account
            self._write(state)
            return state

    def key_for(self, account):
        with self.lock:
            if not isinstance(account, str) or not re.fullmatch(r"[a-f0-9]{32}", account):
                raise PreferencesError("请选择已保存的 Report Key。")
            state = self._load()
            if not any(profile["id"] == account for profile in state["profiles"]):
                raise PreferencesError("所选账号不存在，请重新选择。")
            return self.vault.read(account)

    def remember_query(self, params, account=""):
        with self.lock:
            state = self._load()
            identifier = params.get("application", params.get("store_id"))
            platforms = state["apps"].setdefault(identifier, [])
            if params["platform"] not in platforms:
                platforms.append(params["platform"])
            state["last_identifier"] = identifier
            state["last_profile"] = account
            self._write(state)


preferences = Preferences()

"""Per-browser encrypted preferences for a single-process hosted deployment.

No real credentials are copied from the local Mac. The browser keeps an
authenticated encrypted HttpOnly cookie; the server keeps no profile database.
"""
import base64
import copy
import hashlib
import json
import re
import secrets
from http.cookies import SimpleCookie, CookieError

from preferences import PreferencesError, validate_key

COOKIE_NAME = "__Host-max-workspace"
COOKIE_AGE = 365 * 24 * 60 * 60


class CloudPreferences:
    def __init__(self, cipher, cookie_header=""):
        self.cipher = cipher
        self.dirty = False
        self.state = {"owner": secrets.token_hex(16), "profiles": [], "keys": {},
            "last_profile": "", "last_identifier": "",
            "apps": {"com.funjo.block.puzzle.arrow.escape": ["android"]}}
        try:
            from cryptography.fernet import InvalidToken
            cookies = SimpleCookie(cookie_header)
            token = cookies.get(COOKIE_NAME)
            if token and len(token.value) <= 3500:
                value = json.loads(cipher.decrypt(token.value.encode(), ttl=COOKIE_AGE))
                if (isinstance(value, dict) and re.fullmatch(r"[a-f0-9]{32}", value.get("owner", ""))
                        and isinstance(value.get("profiles"), list) and isinstance(value.get("keys"), dict)
                        and isinstance(value.get("apps"), dict)):
                    self.state = value
                    return
        except (CookieError, ValueError, TypeError, InvalidToken):
            pass
        self.dirty = True

    @property
    def owner(self):
        return self.state["owner"]

    def public(self):
        return dict(copy.deepcopy({k: v for k, v in self.state.items() if k not in ("keys", "owner")}),
                    storage="encrypted-cookie")

    def _commit(self, state):
        # Keep the full cookie below the browser's 4 KiB limit; never truncate.
        token = self.cipher.encrypt(json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode())
        if len(token) > 3500:
            raise PreferencesError("此浏览器的账号和应用记录已满；可临时输入 Key 查询。")
        self.state = state
        self.dirty = True

    def save_profile(self, name, key):
        key = validate_key(key)
        name = str(name or "").strip()
        if not 1 <= len(name) <= 50 or any(ord(c) < 32 for c in name):
            raise PreferencesError("请为 Report Key 起一个 1–50 字的名称。")
        if any(p["name"] == name for p in self.state["profiles"]):
            raise PreferencesError("此名称已存在，请选择已有账号或换一个名称。")
        state = copy.deepcopy(self.state)
        account = secrets.token_hex(16)
        state["profiles"].append({"id": account, "name": name})
        state["keys"][account] = key
        state["last_profile"] = account
        self._commit(state)
        return self.public()

    def key_for(self, account):
        if not isinstance(account, str) or account not in self.state["keys"]:
            raise PreferencesError("请选择此浏览器已保存的 Report Key。")
        return self.state["keys"][account]

    def remember_query(self, params, account=""):
        state = copy.deepcopy(self.state)
        identifier = params.get("application", params.get("store_id"))
        platforms = state["apps"].setdefault(identifier, [])
        if params["platform"] not in platforms:
            platforms.append(params["platform"])
        state["last_identifier"] = identifier
        state["last_profile"] = account
        self._commit(state)

    def cookie(self):
        token = self.cipher.encrypt(json.dumps(self.state, ensure_ascii=False, separators=(",", ":")).encode()).decode()
        return f"{COOKIE_NAME}={token}; Path=/; Max-Age={COOKIE_AGE}; Secure; HttpOnly; SameSite=Lax"


def make_cipher(secret):
    from cryptography.fernet import Fernet
    if len(secret) < 32:
        raise RuntimeError("MAX_COOKIE_SECRET must contain at least 32 random characters.")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))

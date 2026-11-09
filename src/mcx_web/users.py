"""
users.py - local (on-premise) accounts for the web console: users, roles, sessions. No web framework.

SQLite file next to the project (<project dir>/mcx_web.db). Passwords: PBKDF2-HMAC-SHA256, 240k
iterations, per-user salt. Sessions: a random token in an HttpOnly cookie; only its SHA-256 is
stored, with a per-session CSRF token. Five wrong passwords lock the account for 5 minutes.

Roles (each includes the ones below it):
  admin     users, API keys, MCP endpoint, licence
  engineer  sources, discovery, tools, profiles, limits, publish, test calls
  viewer    read only
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

ROLES = ("viewer", "engineer", "admin")
ROLE_TEXT = {"admin": "Admin", "engineer": "Engineer", "viewer": "Viewer"}
ROLE_HELP = {"admin": "users, API keys, MCP endpoint, licence - and everything an engineer can do",
             "engineer": "sources, discovery, tools, profiles, limits, publish, test calls",
             "viewer": "read only: overview, tools, model, audit log"}
ITER = 240_000
SESSION_S = 12 * 3600
LOCK_AFTER, LOCK_S = 5, 300
MIN_PASSWORD = 10


def role_at_least(role: str, need: str) -> bool:
    return ROLES.index(role) >= ROLES.index(need) if role in ROLES and need in ROLES else False


def hash_password(pw: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, ITER)
    return f"pbkdf2_sha256${ITER}${salt.hex()}${dk.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        _algo, it, salt, dk = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(got.hex(), dk)
    except (ValueError, TypeError):
        return False


def password_problem(pw: str) -> str:
    if len(pw) < MIN_PASSWORD:
        return f"at least {MIN_PASSWORD} characters"
    if pw.isdigit() or pw.isalpha():
        return "mix letters with digits or symbols"
    return ""


@dataclass
class User:
    id: int
    username: str
    name: str
    role: str
    active: bool
    created: str
    last_login: str

    @property
    def role_text(self) -> str:
        return ROLE_TEXT.get(self.role, self.role)

    def can(self, need: str) -> bool:
        return self.active and role_at_least(self.role, need)


class UserStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._fails: dict[str, list[float]] = {}
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE COLLATE NOCASE,
                name TEXT, role TEXT, pw TEXT, active INTEGER DEFAULT 1, created TEXT, last_login TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER, csrf TEXT,
                created REAL, expires REAL, flash TEXT DEFAULT '');
            """)

    def _db(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _user(r) -> User | None:
        return None if r is None else User(r["id"], r["username"], r["name"] or "", r["role"], bool(r["active"]),
                                           r["created"] or "", r["last_login"] or "")

    # ---- users
    def count(self) -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def admins(self) -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]

    def list(self) -> list[User]:
        with self._db() as db:
            return [self._user(r) for r in db.execute("SELECT * FROM users ORDER BY username")]

    def get(self, uid: int) -> User | None:
        with self._db() as db:
            return self._user(db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())

    def create(self, username: str, password: str, role: str, name: str = "") -> User:
        username = username.strip()
        if not username or not username.replace(".", "").replace("-", "").replace("_", "").isalnum():
            raise ValueError("user name: letters, digits, . - _ only")
        if role not in ROLES:
            raise ValueError("unknown role")
        p = password_problem(password)
        if p:
            raise ValueError(f"password: {p}")
        with self._db() as db:
            try:
                cur = db.execute("INSERT INTO users (username, name, role, pw, created) VALUES (?,?,?,?,?)",
                                 (username, name.strip(), role, hash_password(password), time.strftime("%Y-%m-%d %H:%M")))
            except sqlite3.IntegrityError:
                raise ValueError(f"user {username!r} already exists") from None
            return self.get(cur.lastrowid)

    def update(self, uid: int, *, role: str | None = None, active: bool | None = None, name: str | None = None):
        u = self.get(uid)
        if u is None:
            raise ValueError("no such user")
        if (role is not None and role != "admin" or active is False) and u.role == "admin" and u.active \
                and self.admins() <= 1:
            raise ValueError("keep at least one active admin")
        with self._db() as db:
            if role is not None:
                if role not in ROLES:
                    raise ValueError("unknown role")
                db.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
            if active is not None:
                db.execute("UPDATE users SET active=? WHERE id=?", (int(active), uid))
                if not active:
                    db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            if name is not None:
                db.execute("UPDATE users SET name=? WHERE id=?", (name.strip(), uid))

    def set_password(self, uid: int, password: str, keep_session: str | None = None):
        p = password_problem(password)
        if p:
            raise ValueError(f"password: {p}")
        with self._db() as db:
            db.execute("UPDATE users SET pw=? WHERE id=?", (hash_password(password), uid))
            # other sessions of this user end (a stolen session dies with the old password)
            db.execute("DELETE FROM sessions WHERE user_id=? AND token<>?", (uid, _h(keep_session or "")))

    def delete(self, uid: int):
        u = self.get(uid)
        if u and u.role == "admin" and u.active and self.admins() <= 1:
            raise ValueError("keep at least one active admin")
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            db.execute("DELETE FROM users WHERE id=?", (uid,))

    # ---- sign in
    def locked(self, username: str) -> float:
        now = time.time()
        fails = [t for t in self._fails.get(username.lower(), []) if now - t < LOCK_S]
        self._fails[username.lower()] = fails
        return LOCK_S - (now - fails[0]) if len(fails) >= LOCK_AFTER else 0.0

    def authenticate(self, username: str, password: str) -> User | None:
        with self._lock:
            if self.locked(username):
                return None
            with self._db() as db:
                r = db.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
            ok = r is not None and r["active"] and check_password(password, r["pw"])
            if r is None:
                check_password(password, hash_password("x"))     # same time whether or not the user exists
            if not ok:
                self._fails.setdefault(username.lower(), []).append(time.time())
                return None
            self._fails.pop(username.lower(), None)
            with self._db() as db:
                db.execute("UPDATE users SET last_login=? WHERE id=?", (time.strftime("%Y-%m-%d %H:%M"), r["id"]))
            return self._user(r)

    # ---- sessions
    def new_session(self, uid: int) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE expires < ?", (now,))
            db.execute("INSERT INTO sessions (token, user_id, csrf, created, expires) VALUES (?,?,?,?,?)",
                       (_h(token), uid, secrets.token_urlsafe(24), now, now + SESSION_S))
        return token

    def session(self, token: str | None) -> tuple[User, str] | None:
        """(user, csrf token) for a valid cookie, else None. Sliding expiry."""
        if not token:
            return None
        with self._db() as db:
            r = db.execute("SELECT * FROM sessions WHERE token=?", (_h(token),)).fetchone()
            if r is None or r["expires"] < time.time():
                return None
            db.execute("UPDATE sessions SET expires=? WHERE token=?", (time.time() + SESSION_S, _h(token)))
        u = self.get(r["user_id"])
        return (u, r["csrf"]) if u and u.active else None

    def end_session(self, token: str | None):
        if token:
            with self._db() as db:
                db.execute("DELETE FROM sessions WHERE token=?", (_h(token),))

    def flash(self, token: str, text: str | None = None) -> str:
        """Set (text given) or take (and clear) a one-time message - secrets shown once."""
        with self._db() as db:
            if text is not None:
                db.execute("UPDATE sessions SET flash=? WHERE token=?", (text, _h(token)))
                return text
            r = db.execute("SELECT flash FROM sessions WHERE token=?", (_h(token),)).fetchone()
            if r and r["flash"]:
                db.execute("UPDATE sessions SET flash='' WHERE token=?", (_h(token),))
                return r["flash"]
        return ""


def _h(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

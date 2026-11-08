"""Persistent owner account and revocable sessions for the local installation."""

import hashlib
import hmac
import secrets
import sqlite3
import time
from contextlib import contextmanager
from http.cookies import CookieError, SimpleCookie
from pathlib import Path

COOKIE_NAME = "zyvello_session"
SESSION_SECONDS = 7 * 24 * 3600


class AccountError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def cookie_token(header: str) -> str:
    cookie = SimpleCookie()
    try:
        cookie.load(header)
        value = cookie.get(COOKIE_NAME)
        return value.value if value else ""
    except CookieError:
        return ""


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)


class LocalAccounts:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.row_factory = sqlite3.Row
            db.executescript("""
                CREATE TABLE IF NOT EXISTS owner (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    email TEXT NOT NULL UNIQUE,
                    password_hash BLOB NOT NULL,
                    salt BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    digest TEXT PRIMARY KEY,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    client TEXT PRIMARY KEY,
                    count INTEGER NOT NULL,
                    started_at REAL NOT NULL
                );
            """)
            with db:
                yield db
        finally:
            db.close()

    def has_owner(self) -> bool:
        if not self.path.exists():
            return False
        with self._db() as db:
            return db.execute("SELECT 1 FROM owner").fetchone() is not None

    def user(self, token: str) -> dict | None:
        if not token or not self.path.exists():
            return None
        with self._db() as db:
            row = db.execute(
                "SELECT email FROM owner JOIN sessions ON sessions.digest = ? "
                "WHERE sessions.expires_at > ?", (_digest(token), time.time())
            ).fetchone()
            return {"email": row["email"]} if row else None

    def _attempt(self, client: str):
        now = time.time()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM attempts WHERE started_at < ?", (now - 900,))
            row = db.execute("SELECT count FROM attempts WHERE client = ?", (client,)).fetchone()
            if row and row["count"] >= 10:
                raise AccountError("Too many attempts. Try again in 15 minutes.", 429)
            db.execute(
                "INSERT INTO attempts VALUES (?, 1, ?) ON CONFLICT(client) "
                "DO UPDATE SET count = count + 1", (client, now)
            )

    def _session(self, db, old_token: str) -> str:
        db.execute("DELETE FROM sessions WHERE expires_at <= ? OR digest = ?",
                   (time.time(), _digest(old_token)))
        token = secrets.token_urlsafe(32)
        db.execute("INSERT INTO sessions VALUES (?, ?)",
                   (_digest(token), time.time() + SESSION_SECONDS))
        return token

    def signup(self, email: str, password: str, client: str, old_token: str = "") -> str:
        self._attempt(client)
        salt = secrets.token_bytes(16)
        hashed = _password_hash(password, salt)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM owner").fetchone():
                raise AccountError("This installation already has an owner. Please log in.", 409)
            db.execute("INSERT INTO owner VALUES (1, ?, ?, ?)", (email, hashed, salt))
            token = self._session(db, old_token)
            db.execute("DELETE FROM attempts WHERE client = ?", (client,))
            return token

    def login(self, email: str, password: str, client: str, old_token: str = "") -> str:
        self._attempt(client)
        with self._db() as db:
            row = db.execute("SELECT * FROM owner WHERE email = ?", (email,)).fetchone()
        salt = row["salt"] if row else bytes(16)
        hashed = _password_hash(password, salt)
        if not row or not hmac.compare_digest(hashed, row["password_hash"]):
            raise AccountError("Email or password is incorrect.", 401)
        with self._db() as db:
            token = self._session(db, old_token)
            db.execute("DELETE FROM attempts WHERE client = ?", (client,))
            return token

    def logout(self, token: str):
        if self.path.exists():
            with self._db() as db:
                db.execute("DELETE FROM sessions WHERE digest = ?", (_digest(token),))

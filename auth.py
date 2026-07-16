"""Authentication module — email domain whitelist, password + OTP login."""

import hashlib
import hmac
import os
import random
import secrets
import sqlite3
import smtplib
import time
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

DB_PATH = Path("data/auth.db")
OTP_TTL = 300  # 5 minutes
SESSION_TTL = 86400 * 7  # 7 days

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


INITIAL_ADMIN_EMAIL = os.getenv("INITIAL_ADMIN_EMAIL", "")


def init_db():
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS allowed_domains (
            domain TEXT PRIMARY KEY,
            added_at REAL DEFAULT (unixepoch())
        );
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at REAL DEFAULT (unixepoch()),
            is_active INTEGER DEFAULT 1,
            is_admin INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS otp_codes (
            email TEXT NOT NULL,
            code TEXT NOT NULL,
            created_at REAL NOT NULL,
            used INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            ip TEXT,
            user_agent TEXT
        );
        CREATE TABLE IF NOT EXISTS login_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            action TEXT NOT NULL,
            ip TEXT,
            user_agent TEXT,
            success INTEGER DEFAULT 0,
            timestamp REAL DEFAULT (unixepoch())
        );
    """)
    # Migration: add is_admin column if upgrading from older schema
    try:
        conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

    # Bootstrap: promote INITIAL_ADMIN_EMAIL if set and user exists
    if INITIAL_ADMIN_EMAIL:
        _bootstrap_admin(INITIAL_ADMIN_EMAIL.strip().lower())


def _bootstrap_admin(email: str):
    conn = _get_db()
    try:
        row = conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            conn.execute("UPDATE users SET is_admin = 1 WHERE email = ?", (email,))
            conn.commit()
            print(f"[AUTH] Admin bootstrapped: {email}")
        else:
            print(f"[AUTH] INITIAL_ADMIN_EMAIL={email} not yet registered. Will be promoted on next restart after registration.")
    finally:
        conn.close()


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000).hex()


def _extract_domain(email: str) -> str:
    return email.strip().lower().split("@")[-1]


# --- Domain Management ---

def add_domain(domain: str) -> bool:
    conn = _get_db()
    try:
        conn.execute("INSERT OR IGNORE INTO allowed_domains (domain) VALUES (?)", (domain.lower().strip(),))
        conn.commit()
        return True
    finally:
        conn.close()


def remove_domain(domain: str) -> bool:
    conn = _get_db()
    try:
        cur = conn.execute("DELETE FROM allowed_domains WHERE domain = ?", (domain.lower().strip(),))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_domains() -> list[dict]:
    conn = _get_db()
    try:
        rows = conn.execute("SELECT domain, added_at FROM allowed_domains ORDER BY added_at").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def is_domain_allowed(email: str) -> bool:
    domain = _extract_domain(email)
    conn = _get_db()
    try:
        row = conn.execute("SELECT 1 FROM allowed_domains WHERE domain = ?", (domain,)).fetchone()
        return row is not None
    finally:
        conn.close()


# --- User Management ---

def register_user(email: str, password: str) -> tuple[bool, str]:
    email = email.strip().lower()
    if not is_domain_allowed(email):
        return False, "Email domain not allowed"

    conn = _get_db()
    try:
        existing = conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            return False, "User already exists"

        salt = secrets.token_hex(16)
        pw_hash = _hash_password(password, salt)
        conn.execute(
            "INSERT INTO users (email, password_hash, salt) VALUES (?, ?, ?)",
            (email, pw_hash, salt),
        )
        conn.commit()
        return True, "ok"
    finally:
        conn.close()


def verify_password(email: str, password: str) -> bool:
    email = email.strip().lower()
    conn = _get_db()
    try:
        row = conn.execute("SELECT password_hash, salt, is_active FROM users WHERE email = ?", (email,)).fetchone()
        if not row or not row["is_active"]:
            return False
        return hmac.compare_digest(row["password_hash"], _hash_password(password, row["salt"]))
    finally:
        conn.close()


def list_users() -> list[dict]:
    conn = _get_db()
    try:
        rows = conn.execute("SELECT email, created_at, is_active, is_admin FROM users ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_user_active(email: str, active: bool) -> bool:
    conn = _get_db()
    try:
        cur = conn.execute("UPDATE users SET is_active = ? WHERE email = ?", (1 if active else 0, email.lower()))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def is_admin(email: str) -> bool:
    conn = _get_db()
    try:
        row = conn.execute("SELECT is_admin FROM users WHERE email = ? AND is_active = 1", (email.lower(),)).fetchone()
        return bool(row and row["is_admin"])
    finally:
        conn.close()


def set_admin(email: str, admin: bool) -> bool:
    conn = _get_db()
    try:
        cur = conn.execute("UPDATE users SET is_admin = ? WHERE email = ?", (1 if admin else 0, email.lower()))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# --- OTP ---

def generate_otp(email: str) -> str:
    email = email.strip().lower()
    code = f"{random.randint(0, 999999):06d}"
    conn = _get_db()
    try:
        conn.execute("DELETE FROM otp_codes WHERE email = ?", (email,))
        conn.execute(
            "INSERT INTO otp_codes (email, code, created_at) VALUES (?, ?, ?)",
            (email, code, time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return code


MAX_OTP_ATTEMPTS = 5

def verify_otp(email: str, code: str) -> bool:
    email = email.strip().lower()
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT code, created_at, used FROM otp_codes WHERE email = ? ORDER BY created_at DESC LIMIT 1",
            (email,),
        ).fetchone()
        if not row or row["used"]:
            return False
        if time.time() - row["created_at"] > OTP_TTL:
            return False
        # Rate limit: count recent failed OTP attempts
        recent_fails = conn.execute(
            "SELECT COUNT(*) as cnt FROM login_logs WHERE email = ? AND action = 'otp_fail' AND timestamp > ?",
            (email, time.time() - OTP_TTL),
        ).fetchone()
        if recent_fails and recent_fails["cnt"] >= MAX_OTP_ATTEMPTS:
            return False
        if not hmac.compare_digest(row["code"], code.strip()):
            return False
        conn.execute("UPDATE otp_codes SET used = 1 WHERE email = ? AND code = ?", (email, row["code"]))
        conn.commit()
        return True
    finally:
        conn.close()


def send_otp_email(email: str, code: str) -> bool:
    if not SMTP_HOST:
        print(f"[AUTH] OTP for {email}: {code} (SMTP not configured, printed to console)")
        return True

    msg = MIMEText(
        f"Your verification code is: {code}\n\nThis code expires in 5 minutes.\nIf you did not request this, please ignore.",
        "plain",
        "utf-8",
    )
    msg["Subject"] = f"Login Verification Code: {code}"
    msg["From"] = SMTP_FROM or SMTP_USER
    msg["To"] = email

    print(f"[OTP] {email}: {code}")
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        print(f"[OTP] Email sent successfully to {email}")
        return True
    except Exception as e:
        print(f"[AUTH] Failed to send email to {email}: {e}")
        return False


# --- Sessions ---

def create_session(email: str, ip: str = "", user_agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO sessions (token, email, created_at, expires_at, ip, user_agent) VALUES (?, ?, ?, ?, ?, ?)",
            (token, email.lower(), now, now + SESSION_TTL, ip, user_agent),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def validate_session(token: str) -> Optional[str]:
    if not token:
        return None
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT email, expires_at FROM sessions WHERE token = ?", (token,)
        ).fetchone()
        if not row:
            return None
        if time.time() > row["expires_at"]:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            conn.commit()
            return None
        return row["email"]
    finally:
        conn.close()


def revoke_session(token: str):
    conn = _get_db()
    try:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()


# --- Login Logs ---

def log_login(email: str, action: str, ip: str = "", user_agent: str = "", success: bool = False):
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO login_logs (email, action, ip, user_agent, success) VALUES (?, ?, ?, ?, ?)",
            (email.lower(), action, ip, user_agent, 1 if success else 0),
        )
        conn.commit()
    finally:
        conn.close()


def count_recent_failures(email: str, action: str, window_seconds: int) -> int:
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM login_logs WHERE email = ? AND action = ? AND success = 0 AND timestamp > ?",
            (email.lower(), action, time.time() - window_seconds),
        ).fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()


def get_login_logs(limit: int = 100) -> list[dict]:
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT email, action, ip, user_agent, success, timestamp FROM login_logs ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_active_sessions() -> list[dict]:
    conn = _get_db()
    now = time.time()
    try:
        rows = conn.execute(
            "SELECT email, created_at, expires_at, ip, user_agent FROM sessions WHERE expires_at > ? ORDER BY created_at DESC",
            (now,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

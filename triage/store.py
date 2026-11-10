"""SQLite persistence for accounts, tickets, and their event history.

Standard-library `sqlite3` + `hashlib`/`secrets` only -- no external
dependencies. Passwords are stored as PBKDF2-HMAC-SHA256 hashes with a per-user
salt (never plaintext). Sessions are random tokens kept server-side. Tickets are
owned by the account that created them, so each user only sees their own queue.

A new connection is opened per call: SQLite handles its own file locking, which
keeps things thread-safe under the threading HTTP server.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "tickets.db")
_PBKDF2_ROUNDS = 200_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _connect() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                username   TEXT UNIQUE NOT NULL,
                pw_hash    TEXT NOT NULL,
                pw_salt    TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions(
                token      TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tickets(
                reference  TEXT PRIMARY KEY,
                owner      INTEGER,
                subject    TEXT,
                body       TEXT,
                requester  TEXT,
                status     TEXT NOT NULL DEFAULT 'open',
                priority   TEXT,
                triage     TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events(
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                reference TEXT NOT NULL,
                type      TEXT NOT NULL,
                payload   TEXT,
                at        TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value INTEGER);
            CREATE INDEX IF NOT EXISTS idx_events_ref ON events(reference);
            """
        )
        # Migration: add owner column to tickets tables created before accounts,
        # BEFORE creating any index that references it.
        cols = [r[1] for r in c.execute("PRAGMA table_info(tickets)").fetchall()]
        if "owner" not in cols:
            c.execute("ALTER TABLE tickets ADD COLUMN owner INTEGER")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tickets_owner ON tickets(owner)")


# --- auth -------------------------------------------------------------------

def _hash(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), _PBKDF2_ROUNDS
    ).hex()


def create_user(username: str, password: str) -> dict | None:
    """Create an account. Returns the user dict, or None if the name is taken."""
    salt = secrets.token_hex(16)
    pw_hash = _hash(password, salt)
    now = _now()
    try:
        with _connect() as c:
            cur = c.execute(
                "INSERT INTO users(username, pw_hash, pw_salt, created_at) VALUES(?,?,?,?)",
                (username, pw_hash, salt, now),
            )
            return {"id": cur.lastrowid, "username": username, "created_at": now}
    except sqlite3.IntegrityError:
        return None


def verify_credentials(username: str, password: str) -> dict | None:
    with _connect() as c:
        row = c.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not row:
        return None
    if not secrets.compare_digest(_hash(password, row["pw_salt"]), row["pw_hash"]):
        return None
    return {"id": row["id"], "username": row["username"], "created_at": row["created_at"]}


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with _connect() as c:
        c.execute(
            "INSERT INTO sessions(token, user_id, created_at) VALUES(?,?,?)",
            (token, user_id, _now()),
        )
    return token


def session_user(token: str) -> dict | None:
    if not token:
        return None
    with _connect() as c:
        row = c.execute(
            "SELECT u.id, u.username, u.created_at FROM sessions s"
            " JOIN users u ON u.id = s.user_id WHERE s.token = ?",
            (token,),
        ).fetchone()
    return dict(row) if row else None


def delete_session(token: str) -> None:
    with _connect() as c:
        c.execute("DELETE FROM sessions WHERE token = ?", (token,))


def change_password(user_id: int, old: str, new: str) -> dict:
    with _connect() as c:
        row = c.execute("SELECT pw_hash, pw_salt FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return {"error": "Account not found."}
        if not secrets.compare_digest(_hash(old, row["pw_salt"]), row["pw_hash"]):
            return {"error": "Current password is incorrect."}
        salt = secrets.token_hex(16)
        c.execute("UPDATE users SET pw_hash = ?, pw_salt = ? WHERE id = ?",
                  (_hash(new, salt), salt, user_id))
        return {"ok": True}


def account_stats(user_id: int) -> dict:
    with _connect() as c:
        u = c.execute("SELECT username, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
        total = c.execute("SELECT COUNT(*) FROM tickets WHERE owner = ?", (user_id,)).fetchone()[0]
        open_ = c.execute(
            "SELECT COUNT(*) FROM tickets WHERE owner = ? AND status = 'open'", (user_id,)
        ).fetchone()[0]
    return {
        "username": u["username"] if u else "",
        "created_at": u["created_at"] if u else "",
        "total": total,
        "open": open_,
        "closed": total - open_,
    }


# --- internal ticket helpers ------------------------------------------------

def _next_seq(c: sqlite3.Connection) -> int:
    c.execute("INSERT INTO meta(key, value) VALUES('seq', 0) ON CONFLICT(key) DO NOTHING")
    c.execute("UPDATE meta SET value = value + 1 WHERE key = 'seq'")
    return c.execute("SELECT value FROM meta WHERE key = 'seq'").fetchone()[0]


def _add_event(c: sqlite3.Connection, reference: str, etype: str, payload: dict) -> str:
    at = _now()
    c.execute(
        "INSERT INTO events(reference, type, payload, at) VALUES(?,?,?,?)",
        (reference, etype, json.dumps(payload or {}), at),
    )
    return at


def _events(c: sqlite3.Connection, reference: str) -> list[dict]:
    rows = c.execute(
        "SELECT type, payload, at FROM events WHERE reference = ? ORDER BY id", (reference,)
    ).fetchall()
    return [{"type": r["type"], "at": r["at"], **(json.loads(r["payload"] or "{}"))} for r in rows]


def _owner_of(c: sqlite3.Connection, reference: str):
    row = c.execute("SELECT owner FROM tickets WHERE reference = ?", (reference,)).fetchone()
    return (True, row["owner"]) if row else (False, None)


def _new_reference(c: sqlite3.Connection) -> str:
    return f"TKT-{datetime.now().strftime('%Y%m%d')}-{_next_seq(c):04d}"


# --- tickets (all scoped to an owner) ---------------------------------------

def open_ticket(owner: int, subject="", body="", requester="") -> dict:
    with _connect() as c:
        reference = _new_reference(c)
        now = _now()
        c.execute(
            "INSERT INTO tickets(reference, owner, subject, body, requester, status, created_at, updated_at)"
            " VALUES(?,?,?,?,?, 'open', ?, ?)",
            (reference, owner, subject, body, requester, now, now),
        )
        _add_event(c, reference, "received", {"subject": subject or "(no subject)", "requester": requester})
        return {"reference": reference, "status": "open", "events": _events(c, reference)}


def save_triage(owner: int, reference: str, subject: str, body: str, requester: str, triage: dict) -> dict:
    with _connect() as c:
        exists, row_owner = _owner_of(c, reference) if reference else (False, None)
        if exists and row_owner != owner:
            return {"error": "forbidden"}
        if not exists:
            reference = _new_reference(c)
            now = _now()
            c.execute(
                "INSERT INTO tickets(reference, owner, subject, body, requester, status, created_at, updated_at)"
                " VALUES(?,?,?,?,?, 'open', ?, ?)",
                (reference, owner, subject, body, requester, now, now),
            )
            _add_event(c, reference, "received", {"subject": subject or "(no subject)", "requester": requester})
        c.execute(
            "UPDATE tickets SET subject=?, body=?, requester=?, priority=?, triage=?, updated_at=?"
            " WHERE reference=? AND owner=?",
            (subject, body, requester, triage.get("priority"), json.dumps(triage), _now(), reference, owner),
        )
        _add_event(c, reference, "triaged", {
            "priority": triage.get("priority"),
            "category": triage.get("category"),
            "assignment_group": triage.get("assignment_group"),
            "sla_response": triage.get("sla_response"),
        })
        return {"reference": reference, "events": _events(c, reference)}


def add_reply(owner: int, reference: str, body: str, source: str, to: str) -> dict:
    with _connect() as c:
        exists, row_owner = _owner_of(c, reference)
        if not exists or row_owner != owner:
            return {"error": "forbidden"}
        c.execute("UPDATE tickets SET updated_at=? WHERE reference=?", (_now(), reference))
        at = _add_event(c, reference, "reply", {"body": body, "source": source, "to": to})
        return {"reference": reference, "sent_at": at, "events": _events(c, reference)}


def change_status(owner: int, reference: str, status: str, resolution: str = "") -> dict:
    with _connect() as c:
        exists, row_owner = _owner_of(c, reference)
        if not exists or row_owner != owner:
            return {"error": "forbidden"}
        c.execute("UPDATE tickets SET status=?, updated_at=? WHERE reference=?", (status, _now(), reference))
        etype = "closed" if status == "closed" else "reopened"
        at = _add_event(c, reference, etype, {"resolution": resolution} if resolution else {})
        return {"reference": reference, "status": status, "at": at, "events": _events(c, reference)}


def list_tickets(owner: int, limit: int = 200, q: str = "", status: str = "", priority: str = "") -> list[dict]:
    clauses, params = ["owner = ?"], [owner]
    if q:
        clauses.append("(subject LIKE ? OR body LIKE ? OR reference LIKE ?)")
        like = f"%{q}%"
        params += [like, like, like]
    if status in ("open", "closed"):
        clauses.append("status = ?")
        params.append(status)
    if priority in ("P1", "P2", "P3", "P4"):
        clauses.append("priority = ?")
        params.append(priority)
    params.append(limit)
    with _connect() as c:
        rows = c.execute(
            "SELECT reference, subject, status, priority, updated_at FROM tickets WHERE "
            + " AND ".join(clauses) + " ORDER BY updated_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def get_ticket(owner: int, reference: str) -> dict | None:
    with _connect() as c:
        row = c.execute(
            "SELECT * FROM tickets WHERE reference = ? AND owner = ?", (reference, owner)
        ).fetchone()
        if not row:
            return None
        t = dict(row)
        t["triage"] = json.loads(t["triage"]) if t["triage"] else None
        t["events"] = _events(c, reference)
        return t

#!/usr/bin/env python3
"""Local web front end for the triage agent, backed by SQLite.

    python3 web.py            # then open http://127.0.0.1:8000

Tickets and their event history persist to data/tickets.db (stdlib sqlite3),
so the queue survives refreshes and restarts. Reuses the same triage backend as
the CLI (the authenticated `claude` CLI by default; set ANTHROPIC_API_KEY and
TRIAGE_BACKEND=api for the API path). No external dependencies.
"""
from __future__ import annotations

import csv
import json
import os
import sys
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from triage import agent, store
from triage.client import LLMError, get_client

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = os.environ.get("TRIAGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("TRIAGE_PORT", "8000"))

_client = None


def client():
    global _client
    if _client is None:
        _client = get_client(os.environ.get("TRIAGE_BACKEND"))
    return _client


def backend_name() -> str:
    return type(client()).__name__.replace("Client", "").lower()


def load_samples() -> list[dict]:
    with open(os.path.join(HERE, "data", "sample_tickets.csv"), newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class Handler(BaseHTTPRequestHandler):
    server_version = "TriageWeb/0.3"

    def _send(self, code: int, body, ctype: str = "application/json", headers=None) -> None:
        data = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code: int, obj, headers=None) -> None:
        self._send(code, json.dumps(obj), headers=headers)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def _session_token(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            morsel = SimpleCookie(raw).get("session")
            return morsel.value if morsel else None
        except Exception:
            return None

    def _user(self):
        return store.session_user(self._session_token())

    @staticmethod
    def _cookie(token: str, clear: bool = False) -> dict:
        if clear:
            return {"Set-Cookie": "session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"}
        return {"Set-Cookie": f"session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000"}

    def log_message(self, fmt, *args):  # quiet console
        return

    # -- GET ----------------------------------------------------------------
    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        if path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "web", "index.html"), "rb") as fh:
                    self._send(200, fh.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._json(500, {"error": "web/index.html missing"})
            return
        if path == "/api/health":
            return self._json(200, {"ok": True, "backend": backend_name()})
        if path == "/api/me":
            user = self._user()
            if not user:
                return self._json(401, {"error": "not authenticated"})
            return self._json(200, {"user": user, "stats": store.account_stats(user["id"])})

        # --- authenticated ticket reads ---
        user = self._user()
        if not user:
            return self._json(401, {"error": "not authenticated"})
        if path == "/api/tickets":
            qs = parse_qs(url.query)
            return self._json(200, store.list_tickets(
                user["id"],
                q=(qs.get("q", [""])[0]).strip(),
                status=(qs.get("status", [""])[0]).strip().lower(),
                priority=(qs.get("priority", [""])[0]).strip().upper(),
            ))
        if path == "/api/ticket":
            ref = (parse_qs(url.query).get("ref", [""])[0]).strip()
            t = store.get_ticket(user["id"], ref)
            return self._json(200 if t else 404, t or {"error": "not found"})
        self._json(404, {"error": "not found"})

    # -- POST ---------------------------------------------------------------
    def do_POST(self):
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "invalid JSON"})

        if path == "/api/register":
            return self._register(payload)
        if path == "/api/login":
            return self._login(payload)
        if path == "/api/logout":
            return self._logout()

        # --- authenticated ticket mutations ---
        user = self._user()
        if not user:
            return self._json(401, {"error": "not authenticated"})
        if path == "/api/open":
            return self._open(user, payload)
        if path == "/api/triage":
            return self._triage(user, payload)
        if path == "/api/reply":
            return self._reply(user, payload)
        if path == "/api/status":
            return self._status(user, payload)
        if path == "/api/password":
            return self._password(user, payload)
        self._json(404, {"error": "not found"})

    def _password(self, user, payload):
        new = payload.get("new") or ""
        if len(new) < 6:
            return self._json(400, {"error": "New password must be at least 6 characters."})
        res = store.change_password(user["id"], payload.get("old") or "", new)
        self._json(400 if res.get("error") else 200, res)

    # -- auth handlers ------------------------------------------------------
    def _register(self, payload):
        username = (payload.get("username") or "").strip()
        password = payload.get("password") or ""
        if len(username) < 3 or len(password) < 6:
            return self._json(400, {"error": "Username must be 3+ chars and password 6+ chars."})
        user = store.create_user(username, password)
        if not user:
            return self._json(409, {"error": "That username is already taken."})
        token = store.create_session(user["id"])
        # Seed the sample tickets as real tickets so a new account starts with a
        # realistic, populated queue.
        try:
            for s in load_samples():
                store.open_ticket(
                    user["id"],
                    (s.get("subject") or "").strip(),
                    (s.get("body") or "").strip(),
                    (s.get("requester") or "").strip(),
                )
        except Exception:
            pass
        self._json(200, {"user": {k: user[k] for k in ("id", "username", "created_at")}},
                   headers=self._cookie(token))

    def _login(self, payload):
        user = store.verify_credentials((payload.get("username") or "").strip(), payload.get("password") or "")
        if not user:
            return self._json(401, {"error": "Invalid username or password."})
        token = store.create_session(user["id"])
        self._json(200, {"user": user}, headers=self._cookie(token))

    def _logout(self):
        store.delete_session(self._session_token() or "")
        self._json(200, {"ok": True}, headers=self._cookie("", clear=True))

    # -- ticket handlers (owner-scoped) -------------------------------------
    def _open(self, user, payload):
        self._json(200, store.open_ticket(
            user["id"],
            (payload.get("subject") or "").strip(),
            (payload.get("body") or "").strip(),
            (payload.get("requester") or "").strip(),
        ))

    def _triage(self, user, payload):
        reference = (payload.get("id") or payload.get("reference") or "").strip()
        ticket = {
            "id": reference,
            "subject": (payload.get("subject") or "(no subject)").strip(),
            "body": (payload.get("body") or "").strip(),
            "requester": (payload.get("requester") or "").strip(),
        }
        if not ticket["body"]:
            return self._json(400, {"error": "Ticket body is required."})
        if not reference:
            reference = store.open_ticket(user["id"], ticket["subject"], ticket["body"], ticket["requester"])["reference"]
            ticket["id"] = reference
        elif store.get_ticket(user["id"], reference) is None:
            return self._json(403, {"error": "forbidden"})
        try:
            result = agent.triage_ticket(client(), ticket)
        except LLMError as exc:
            return self._json(502, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover
            return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
        saved = store.save_triage(user["id"], reference, ticket["subject"], ticket["body"], ticket["requester"], result)
        if saved.get("error"):
            return self._json(403, saved)
        self._json(200, {**result, "reference": reference, "events": saved["events"]})

    def _reply(self, user, payload):
        body = (payload.get("body") or "").strip()
        if not body:
            return self._json(400, {"error": "Reply text is required."})
        res = store.add_reply(user["id"], (payload.get("reference") or "").strip(),
                              body, payload.get("source") or "manual", (payload.get("to") or "").strip())
        self._json(403 if res.get("error") else 200, {"ok": not res.get("error"), **res})

    def _status(self, user, payload):
        status = (payload.get("status") or "").strip().lower()
        if status not in ("closed", "open"):
            return self._json(400, {"error": "status must be 'closed' or 'open'"})
        res = store.change_status(user["id"], (payload.get("reference") or "").strip(),
                                 status, (payload.get("resolution") or "").strip())
        self._json(403 if res.get("error") else 200, {"ok": not res.get("error"), **res})


def main() -> int:
    try:
        bname = backend_name()
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    store.init_db()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Triage web UI -> http://{HOST}:{PORT}   (backend: {bname}, DB: data/tickets.db, Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

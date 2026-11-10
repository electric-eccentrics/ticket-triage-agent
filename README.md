# AI Support-Ticket Triage Agent

Turn a raw IT support ticket into a **structured, routable, SLA-tagged record with a drafted reply** — in one command.

Built by [Brian Hobbins](mailto:brian.hobbins@proton.me). Pairs 20+ years of enterprise help-desk and automation work (Remedy, CMDB, SLAs, fleets of 68k+ machines) with modern LLM tooling.

---

## What it does

Feed it a messy, human-written ticket. It returns:

| Field | Example |
|---|---|
| **Summary** | "Finance team cannot access the F: shared drive during month-end close." |
| **Category** | `Network` *(from a fixed vocabulary)* |
| **Priority + SLA** | `P1` → first response in 15 minutes |
| **Assignment group** | `Network Operations` |
| **Sentiment** | `Frustrated` |
| **Needs more info?** | yes → *"How many users affected? Other drives too?"* |
| **Drafted reply** | a polite, specific first response to the requester |
| **Tags** | `shared-drive, network-path, finance, outage` |

…then prints a **priority-sorted queue** and (optionally) writes JSON or CSV for your ticketing system.

It encodes real service-desk judgment — e.g. *anything that looks like phishing, malware, or a breach is automatically at least P2 and routed to Security Operations.*

## Live demo output

```
──────────────────────────────────────────────────────────────────────────────
[P1] T001  Cannot access shared drive
  Summary     : Dana R. in Finance and her entire team cannot access the Finance
                shared drive (F:) during month-end close.
  Category    : Network   →  Network Operations
  SLA         : 15 minutes (critical / outage)
  Why         : Entire team fully blocked, no workaround, business-critical deadline today.
  Sentiment   : Frustrated    Tags: shared-drive, network-path, finance, month-end, outage
  Draft reply : Hi Dana, thanks for flagging this and I understand the urgency with
                month-end close due today... I'm escalating this to Network
                Operations as a top priority now.
──────────────────────────────────────────────────────────────────────────────
[P2] T002  Weird email asking for my password
  Category    : Security  →  Security Operations      (phishing rule → auto-escalated)
──────────────────────────────────────────────────────────────────────────────
[P4] T003  Laptop very slow
  Category    : Hardware  →  Desktop Support
==============================================================================
QUEUE (by priority):
  P1  T001   Network Operations       Finance team cannot access shared drive   (needs info)
  P2  T002   Security Operations      Suspected phishing email impersonating IT (needs info)
  P4  T003   Desktop Support          Laptop gradually slowed over two weeks    (needs info)
```

## Quickstart

No API key required — the default backend uses your installed, authenticated **`claude` CLI**.

```bash
# Triage the bundled sample tickets (first 3):
python3 -m triage.cli --input data/sample_tickets.csv --limit 3

# Triage a single ad-hoc ticket:
python3 -m triage.cli --subject "Email down" \
    --text "Outlook won't open, says my profile is corrupt"

# Batch a whole CSV to JSON for your ticketing system:
python3 -m triage.cli --input data/sample_tickets.csv --out results.json
```

Input CSV columns: `id, requester, subject, body`.

## Web UI

A local, dependency-free web front end is included. Start it and open the page:

```bash
python3 web.py          # -> http://127.0.0.1:8000
```

Register an account and your queue is seeded with a starter set of realistic
tickets (from `data/sample_tickets.csv`) to work through. Open any ticket (or
type a new one), hit **Triage**, and get the priority, SLA, routing, sentiment,
tags, clarifying questions, and a copy-to-clipboard drafted reply — rendered as
a color-coded card.

- **Accounts & login** — register / sign in with a username and password;
  passwords are stored as **PBKDF2-HMAC-SHA256** hashes (never plaintext) and
  sessions are HttpOnly cookies. Each account only sees and can act on **its own**
  tickets (ownership enforced server-side). A **My Account** panel shows your
  ticket stats and a log-out button.
- **Persistent tickets (SQLite)** — every ticket and its full event history are
  stored in `data/tickets.db` (stdlib `sqlite3`), so the queue survives refreshes
  and restarts. A **Saved tickets** panel lists them; click any one to reload its
  form, triage result, and activity timeline.
- **Search & filters** — search the queue by subject, body, or reference, and
  filter by status (open/closed) and priority (P1–P4); filtering runs in SQL.
- **Reference IDs** — every ticket gets a unique, sequential reference
  (`TKT-YYYYMMDD-0007`) shown on both panels.
- **Activity timeline** — a per-ticket history of every event (received, triaged,
  reply, closed, reopened), timestamped.
- **Reply inside the app** — a reply composer. Reach it two ways: **Quick reply**
  on the incoming side (manual reply) or **Reply with this draft** on the triage
  side (opens pre-filled with the AI reply, editable before sending).
- **Close / reopen** — close a ticket with an optional resolution note; status
  shows as an OPEN/CLOSED pill and is recorded on the timeline.

It calls the same `triage` package, so it runs on your Claude account by default
(set `ANTHROPIC_API_KEY` + `TRIAGE_BACKEND=api` for the API path). Pure Python
standard library — no Flask, no ORM, no build step. Endpoints: `GET /api/health`,
`GET /api/samples`, `GET /api/me`, `GET /api/tickets`, `GET /api/ticket?ref=`,
`POST /api/register`, `POST /api/login`, `POST /api/logout`, `POST /api/open`,
`POST /api/triage`, `POST /api/reply`, `POST /api/status` (ticket endpoints
require a session).

> **Auth scope:** this is solid *demo-grade* authentication — hashed passwords,
> server-side sessions, per-user data isolation. It is not production-hardened
> (no HTTPS enforcement, rate limiting, email verification, or password reset),
> which is appropriate for a local portfolio app. Those would be the next steps
> for real deployment.

## How it works

```
 ticket (CSV / text)
        │
        ▼
   triage.agent ──── system prompt encoding the triage policy
        │            (categories, priority matrix, routing + security rules)
        ▼
   triage.client ─── swappable LLM backend ──┬── cli : claude CLI  (your account, zero setup)
        │                                     └── api : Anthropic SDK (cheaper/faster at scale)
        ▼
   triage.schema ─── normalizer: snaps every field onto the allowed
        │            vocabulary, fills defaults, enforces consistency
        ▼
   structured record  →  priority-sorted queue  →  JSON / CSV
```

### Design decisions worth calling out

- **Swappable model backend.** The default `cli` backend drives the authenticated `claude` CLI, so it runs with zero API keys or setup. Set `ANTHROPIC_API_KEY` and `TRIAGE_BACKEND=api` to switch to the official Anthropic SDK — roughly 100× cheaper per ticket and faster, for real batch volume. One interface, two backends.
- **Controlled vocabularies + a normalizer.** LLM output is free-form; routing and reporting need fixed values. `schema.normalize()` is the guardrail — whatever the model returns, the record that leaves the system always has every field, snapped to a known category / priority / team.
- **Policy lives in the prompt, enforcement lives in code.** The urgency/impact matrix and the "security auto-escalates" rule are explicit and auditable, not buried in model guesswork.
- **No dependencies for the default path.** Pure standard library; `anthropic` is optional.

## Configuration

| Env var | Values | Default |
|---|---|---|
| `TRIAGE_BACKEND` | `cli`, `api`, `auto` | `auto` (api if `ANTHROPIC_API_KEY` is set, else cli) |
| `ANTHROPIC_API_KEY` | your key | *(unset — cli backend used)* |

## Tests

```bash
python3 tests/test_schema.py   # normalizer logic, no network calls
```

## Roadmap / productization

- [ ] Webhook/API wrapper so a ticketing system (Zendesk, Freshdesk, ServiceNow) can POST a ticket and get triage back
- [ ] Learn routing from a team's historical tickets (few-shot from closed tickets)
- [ ] Confidence score + "auto-route vs. human-review" threshold
- [ ] Weekly trend report (volume by category, SLA breaches, sentiment)

---

*MIT-licensed. Sample tickets are synthetic.*

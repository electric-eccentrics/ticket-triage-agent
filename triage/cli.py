"""Command-line entry point for the triage agent.

Examples
--------
# Triage the bundled sample tickets (first 3), pretty table to screen:
python -m triage.cli --input data/sample_tickets.csv --limit 3

# Triage a single ad-hoc ticket:
python -m triage.cli --text "Outlook won't open, says profile corrupt" --subject "Email down"

# Batch a CSV and write results to JSON:
python -m triage.cli --input data/sample_tickets.csv --out results.json
"""
from __future__ import annotations

import argparse
import csv
import json
import sys

from . import agent
from .client import LLMError, get_client

PRIORITY_ORDER = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}


def read_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    tickets = []
    for i, row in enumerate(rows, 1):
        tickets.append(
            {
                "id": (row.get("id") or f"T{i:03d}").strip(),
                "subject": (row.get("subject") or "").strip(),
                "body": (row.get("body") or "").strip(),
                "requester": (row.get("requester") or "").strip(),
            }
        )
    return tickets


def _c(text: str, code: str, color: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if color else text


def print_card(r: dict, color: bool = True) -> None:
    pri_color = {"P1": "1;31", "P2": "31", "P3": "33", "P4": "32"}.get(r["priority"], "0")
    bar = "─" * 78
    print(_c(bar, "90", color))
    head = f"[{r['priority']}] {r['ticket_id']}  {r['subject']}"
    print(_c(head, pri_color, color))
    print(f"  Summary     : {r['summary']}")
    print(f"  Category    : {r['category']}   →  {r['assignment_group']}")
    print(f"  SLA         : {r['sla_response']}")
    print(f"  Why         : {r['priority_reason']}")
    print(f"  Sentiment   : {r['sentiment']}    Tags: {', '.join(r['tags']) or '-'}")
    if r["needs_more_info"] and r["clarifying_questions"]:
        print(_c("  Need info   :", "33", color))
        for q in r["clarifying_questions"]:
            print(f"      - {q}")
    print(_c("  Draft reply :", "36", color))
    for line in _wrap(r["suggested_reply"], 72):
        print(f"      {line}")


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AI IT-support ticket triage agent")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="CSV with columns: id, subject, body, requester")
    src.add_argument("--text", help="Body of a single ad-hoc ticket to triage")
    ap.add_argument("--subject", default="(no subject)", help="Subject for --text")
    ap.add_argument("--requester", default="", help="Requester for --text")
    ap.add_argument("--limit", type=int, default=0, help="Only triage the first N tickets")
    ap.add_argument("--backend", choices=["cli", "api", "auto"], default=None)
    ap.add_argument("--out", help="Write full results to this .json or .csv file")
    ap.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    args = ap.parse_args(argv)

    if args.text:
        tickets = [{"id": "T001", "subject": args.subject, "body": args.text, "requester": args.requester}]
    else:
        tickets = read_csv(args.input)
    if args.limit > 0:
        tickets = tickets[: args.limit]

    try:
        client = get_client(args.backend)
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    color = sys.stdout.isatty() and not args.no_color
    print(f"Triaging {len(tickets)} ticket(s) with backend "
          f"'{type(client).__name__.replace('Client', '').lower()}'...\n")

    results = []
    for t in tickets:
        try:
            r = agent.triage_ticket(client, t)
        except LLMError as exc:
            print(f"  ! {t['id']} failed: {exc}", file=sys.stderr)
            continue
        results.append(r)
        print_card(r, color)

    # Sort a summary queue by priority.
    results.sort(key=lambda r: (PRIORITY_ORDER.get(r["priority"], 9), r["ticket_id"]))
    print("\n" + ("=" * 78))
    print("QUEUE (by priority):")
    for r in results:
        flag = "  (needs info)" if r["needs_more_info"] else ""
        print(f"  {r['priority']}  {r['ticket_id']:<6} {r['assignment_group']:<24} "
              f"{r['summary'][:40]}{flag}")

    if args.out:
        _write_out(args.out, results)
        print(f"\nWrote {len(results)} results to {args.out}")
    return 0


def _write_out(path: str, results: list[dict]) -> None:
    if path.lower().endswith(".csv"):
        cols = ["ticket_id", "subject", "priority", "sla_response", "category",
                "assignment_group", "sentiment", "needs_more_info", "summary",
                "priority_reason", "tags", "clarifying_questions", "suggested_reply"]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in results:
                row = dict(r)
                row["tags"] = "; ".join(r["tags"])
                row["clarifying_questions"] = "; ".join(r["clarifying_questions"])
                w.writerow(row)
    else:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())

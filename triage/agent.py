"""The triage agent: turn a raw support ticket into a structured, routable record."""
from __future__ import annotations

from . import schema

SYSTEM_PROMPT = """\
You are an experienced IT service-desk lead triaging inbound support tickets.
You classify each ticket, set a priority from an urgency/impact matrix, route it
to the correct team, and draft a first reply to the requester.

Respond with a SINGLE JSON object and nothing else -- no prose, no code fences.

JSON shape (all keys required):
{{
  "summary":            string,   // one concise sentence describing the issue
  "category":           string,   // EXACTLY one of: {CATEGORIES}
  "priority":           string,   // one of P1, P2, P3, P4
  "priority_reason":    string,   // brief justification referencing urgency + impact
  "assignment_group":   string,   // EXACTLY one of: {GROUPS}
  "sentiment":          string,   // one of: Frustrated, Neutral, Positive
  "needs_more_info":    boolean,  // true if you cannot resolve without more detail
  "clarifying_questions": [string], // questions to ask the requester (empty if none)
  "suggested_reply":    string,   // a polite, specific first response to the requester
  "tags":               [string]  // 2-5 short lowercase keywords
}}

Priority matrix (first-response SLA):
- P1: {P1}  -- business-critical outage, many users, security breach, data loss in progress
- P2: {P2}  -- major function broken for a user/team, no workaround
- P3: {P3}  -- degraded or inconvenient, workaround exists, single user
- P4: {P4}  -- request, question, cosmetic, or low-impact

Rules:
- Choose category and assignment_group ONLY from the lists above, verbatim.
- Security concerns (phishing, malware, suspected breach, exposed credentials)
  are at least P2 and route to Security Operations.
- If key facts are missing (which app, error text, when it started, how many
  users), set needs_more_info=true and ask for them in clarifying_questions, but
  STILL provide your best-guess category, priority, and routing.
- suggested_reply speaks directly to the requester, acknowledges their issue,
  and states the next step. Keep it under ~90 words.
""".format(
    CATEGORIES=", ".join(schema.CATEGORIES),
    GROUPS=", ".join(schema.ASSIGNMENT_GROUPS),
    P1=schema.PRIORITIES["P1"],
    P2=schema.PRIORITIES["P2"],
    P3=schema.PRIORITIES["P3"],
    P4=schema.PRIORITIES["P4"],
)


def build_user_prompt(ticket: dict) -> str:
    requester = ticket.get("requester", "").strip()
    subject = ticket.get("subject", "").strip()
    body = ticket.get("body", "").strip()
    parts = ["Triage this ticket:\n"]
    if requester:
        parts.append(f"Requester: {requester}")
    parts.append(f"Subject: {subject}")
    parts.append(f"Body:\n{body}")
    return "\n".join(parts)


def triage_ticket(client, ticket: dict) -> dict:
    """Run one ticket through the model and return a normalized triage record."""
    raw = client.complete(SYSTEM_PROMPT, build_user_prompt(ticket))
    result = schema.normalize(raw)
    result["sla_response"] = schema.sla_for(result["priority"])
    # Carry the ticket identity through for reporting.
    result["ticket_id"] = ticket.get("id", "")
    result["subject"] = ticket.get("subject", "")
    return result

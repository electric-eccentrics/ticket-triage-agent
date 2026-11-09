"""Triage output schema: the controlled vocabularies and a normalizer.

Keeping categories, priorities, and assignment groups as fixed sets is what
turns a free-text LLM response into something a help desk can actually route
and report on. `normalize()` is the guardrail: whatever the model returns, the
result that leaves this module always has every field, with values snapped to
the allowed vocabulary.
"""
from __future__ import annotations

# --- Controlled vocabularies -------------------------------------------------

CATEGORIES = [
    "Hardware",
    "Software",
    "Network",
    "Account & Access",
    "Email",
    "Security",
    "Database",
    "Phone / Telephony",
    "Other",
]

# Priority -> target first-response SLA. A classic urgency/impact matrix.
PRIORITIES = {
    "P1": "15 minutes (critical / outage)",
    "P2": "1 hour (high)",
    "P3": "4 business hours (medium)",
    "P4": "1 business day (low)",
}

ASSIGNMENT_GROUPS = [
    "Service Desk (Tier 1)",
    "Desktop Support",
    "Network Operations",
    "Identity & Access",
    "Security Operations",
    "Database Administration",
    "Application Support",
]

SENTIMENTS = ["Frustrated", "Neutral", "Positive"]

# Field -> default used when the model omits it or returns something off-vocab.
_DEFAULTS = {
    "summary": "",
    "category": "Other",
    "priority": "P3",
    "priority_reason": "",
    "assignment_group": "Service Desk (Tier 1)",
    "sentiment": "Neutral",
    "needs_more_info": False,
    "clarifying_questions": [],
    "suggested_reply": "",
    "tags": [],
}

FIELDS = list(_DEFAULTS.keys())


def _coerce_str(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _coerce_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _nearest(value: str, options: list[str], fallback: str) -> str:
    """Snap a model-provided value onto the allowed vocabulary (case/space
    tolerant), falling back to a safe default when there's no clear match."""
    v = _coerce_str(value).lower()
    if not v:
        return fallback
    for opt in options:
        if v == opt.lower():
            return opt
    # Loose contains match, e.g. "access" -> "Account & Access".
    for opt in options:
        key = opt.lower().split(" (")[0]
        if v in key or key in v:
            return opt
    return fallback


def normalize(raw: dict) -> dict:
    """Return a complete, vocabulary-safe triage record from the model's dict."""
    if not isinstance(raw, dict):
        raw = {}
    out = dict(_DEFAULTS)

    out["summary"] = _coerce_str(raw.get("summary")) or "(no summary)"
    out["category"] = _nearest(raw.get("category"), CATEGORIES, "Other")

    priority = _coerce_str(raw.get("priority")).upper().replace(" ", "")
    out["priority"] = priority if priority in PRIORITIES else "P3"

    out["priority_reason"] = _coerce_str(raw.get("priority_reason"))
    out["assignment_group"] = _nearest(
        raw.get("assignment_group"), ASSIGNMENT_GROUPS, "Service Desk (Tier 1)"
    )
    out["sentiment"] = _nearest(raw.get("sentiment"), SENTIMENTS, "Neutral")
    out["needs_more_info"] = bool(raw.get("needs_more_info", False))
    out["clarifying_questions"] = _coerce_list(raw.get("clarifying_questions"))
    out["suggested_reply"] = _coerce_str(raw.get("suggested_reply"))
    out["tags"] = _coerce_list(raw.get("tags"))

    # Internal consistency: if there are questions, we do need more info.
    if out["clarifying_questions"]:
        out["needs_more_info"] = True

    return out


def sla_for(priority: str) -> str:
    return PRIORITIES.get(priority, PRIORITIES["P3"])

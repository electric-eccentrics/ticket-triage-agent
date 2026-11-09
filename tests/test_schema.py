"""Unit tests for the normalizer -- no network/model calls, run anywhere."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from triage import schema  # noqa: E402


def test_fills_defaults_from_empty():
    r = schema.normalize({})
    for field in schema.FIELDS:
        assert field in r
    assert r["category"] == "Other"
    assert r["priority"] == "P3"
    assert r["assignment_group"] == "Service Desk (Tier 1)"


def test_snaps_offvocab_category_and_group():
    # Model returns near-miss casing / wording; normalizer snaps to vocabulary.
    r = schema.normalize({"category": "networking", "assignment_group": "Network Operations "})
    assert r["category"] == "Network"
    assert r["assignment_group"] == "Network Operations"
    # Unrecognizable value falls back safely rather than passing through.
    assert schema.normalize({"assignment_group": "xyz"})["assignment_group"] == "Service Desk (Tier 1)"


def test_invalid_priority_falls_back():
    assert schema.normalize({"priority": "urgent"})["priority"] == "P3"
    assert schema.normalize({"priority": "p1"})["priority"] == "P1"


def test_questions_force_needs_more_info():
    r = schema.normalize({"needs_more_info": False, "clarifying_questions": ["Which app?"]})
    assert r["needs_more_info"] is True


def test_sentiment_and_lists_coerced():
    r = schema.normalize({"sentiment": "FRUSTRATED", "tags": ["a", " ", "b"]})
    assert r["sentiment"] == "Frustrated"
    assert r["tags"] == ["a", "b"]


def test_sla_lookup():
    assert "15 minutes" in schema.sla_for("P1")
    assert schema.sla_for("bogus") == schema.PRIORITIES["P3"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")

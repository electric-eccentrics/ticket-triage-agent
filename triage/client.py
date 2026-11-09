"""Swappable LLM client.

Two backends, same interface (`complete(system, user) -> str`):

* **cli**  (default)  Shells out to the authenticated `claude` CLI
  (`claude -p --output-format json`). Uses your existing Claude / Claude Code
  subscription -- no API key, no setup. Great for demos; one call carries the
  Claude Code system prompt, so it costs more per ticket.

* **api**  Uses the official Anthropic Python SDK with an `ANTHROPIC_API_KEY`.
  Much cheaper per ticket and faster -- the right backend for production/batch.

Select with the `TRIAGE_BACKEND` env var: `cli`, `api`, or `auto` (default:
`api` when ANTHROPIC_API_KEY is set, otherwise `cli`).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model response, tolerating code
    fences or stray prose around it."""
    text = text.strip()
    # Strip ```json ... ``` fences if present.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Last resort: grab the outermost {...} span.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"Could not parse JSON from model output: {exc}") from exc
    raise LLMError("Model did not return JSON.")


class CLIClient:
    """Backend that drives the local `claude` CLI (uses your subscription)."""

    def __init__(self, timeout: int = 120):
        self.bin = shutil.which("claude")
        if not self.bin:
            raise LLMError(
                "`claude` CLI not found on PATH. Install Claude Code or set "
                "ANTHROPIC_API_KEY and TRIAGE_BACKEND=api."
            )
        self.timeout = timeout

    def complete(self, system: str, user: str) -> dict:
        cmd = [self.bin, "-p", "--output-format", "json"]
        if system:
            cmd += ["--append-system-prompt", system]
        try:
            proc = subprocess.run(
                cmd,
                input=user,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"claude CLI timed out after {self.timeout}s") from exc
        if proc.returncode != 0:
            raise LLMError(f"claude CLI failed: {proc.stderr.strip() or proc.stdout.strip()}")
        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Unexpected CLI output: {exc}") from exc
        if envelope.get("is_error"):
            raise LLMError(f"claude CLI reported an error: {envelope.get('result')}")
        return _extract_json(envelope.get("result", ""))


class APIClient:
    """Backend that uses the official Anthropic SDK (needs ANTHROPIC_API_KEY)."""

    def __init__(self, model: str = "claude-opus-5-5", max_tokens: int = 2048):
        try:
            import anthropic  # noqa: F401
        except ImportError as exc:
            raise LLMError(
                "The `anthropic` package is not installed. Run "
                "`pip install anthropic`, or use the CLI backend."
            ) from exc
        from anthropic import Anthropic

        self._anthropic = __import__("anthropic")
        self.client = Anthropic()  # reads ANTHROPIC_API_KEY from the environment
        self.model = model
        self.max_tokens = max_tokens

    def complete(self, system: str, user: str) -> dict:
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                thinking={"type": "adaptive"},
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except self._anthropic.APIError as exc:
            raise LLMError(f"Anthropic API error: {exc}") from exc
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        )
        return _extract_json(text)


def get_client(backend: str | None = None):
    """Factory honoring TRIAGE_BACKEND / explicit override."""
    backend = (backend or os.environ.get("TRIAGE_BACKEND", "auto")).lower()
    if backend == "auto":
        backend = "api" if os.environ.get("ANTHROPIC_API_KEY") else "cli"
    if backend == "cli":
        return CLIClient()
    if backend == "api":
        return APIClient()
    raise LLMError(f"Unknown backend: {backend!r} (use cli, api, or auto)")

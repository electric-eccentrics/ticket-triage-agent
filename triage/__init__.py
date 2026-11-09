"""AI support-ticket triage agent."""
from .agent import triage_ticket, SYSTEM_PROMPT
from .client import get_client, LLMError
from . import schema

__all__ = ["triage_ticket", "SYSTEM_PROMPT", "get_client", "LLMError", "schema"]
__version__ = "0.1.0"

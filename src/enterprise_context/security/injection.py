"""Heuristic detection of instruction-like text in untrusted content.

This is defense in depth, not the control. The controls are structural: retrieved
documents never become instructions, tools are allowlisted and typed, and policy and
authorization are enforced server-side regardless of what any text says. Flagged
documents are still cited by ID and title, but their text is kept out of answers.
"""

from __future__ import annotations

import re

_PATTERNS = (
    r"\bignore (all |any )?(previous|prior|above|earlier) (instructions|rules|directions)\b",
    r"\bdisregard (the |all |any )?(previous|prior|above|system) (instructions|rules|prompt)\b",
    r"\b(you are now|act as|pretend to be)\b",
    r"\b(system prompt|developer message)\b",
    r"\b(approve|whitelist|unblock) (this|the|all) suppliers?\b",
    r"\boverride (the )?(policy|approval|limits?)\b",
    r"\bexecute (the following|this) (command|action)\b",
)
_INJECTION = re.compile("|".join(f"(?:{pattern})" for pattern in _PATTERNS), re.I)


def contains_instruction_like_text(text: str) -> bool:
    return bool(_INJECTION.search(text))

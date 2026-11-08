"""Hook installers. Each provider module exposes install(), uninstall(), status()."""
from __future__ import annotations

import shlex
import sys
from pathlib import Path

PROVIDERS = ("claude", "codex")


def hook_command(provider: str) -> str:
    """Shell command the agent runs on every event. Fails open and prints nothing."""
    entry = Path(__file__).with_name("entry.py")
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(entry))} {provider} >/dev/null 2>&1; true"


def is_ours(command: str) -> bool:
    return "neopulse" in command and "entry.py" in command


def get(provider: str):
    if provider == "claude":
        from . import claude
        return claude
    if provider == "codex":
        from . import codex
        return codex
    raise ValueError(f"unknown provider {provider!r}; choose from {', '.join(PROVIDERS)}")

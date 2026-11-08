from __future__ import annotations
import os
from pathlib import Path


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    p = Path(base) / "neopulse"
    p.mkdir(parents=True, exist_ok=True)
    return p


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    p = Path(base) / "neopulse"
    p.mkdir(parents=True, exist_ok=True)
    return p


def events_file() -> Path:
    return state_dir() / "events.jsonl"


def config_file() -> Path:
    return config_dir() / "config.json"


def firmware_dir() -> Path | None:
    """Locate the firmware sources: $NEOPULSE_FIRMWARE_DIR, repo checkout, or data dir."""
    cands = []
    if os.environ.get("NEOPULSE_FIRMWARE_DIR"):
        cands.append(Path(os.environ["NEOPULSE_FIRMWARE_DIR"]))
    cands.append(Path(__file__).resolve().parents[1] / "firmware")
    cands.append(Path.home() / ".local" / "share" / "neopulse" / "firmware")
    for c in cands:
        if (c / "boot.py").exists():
            return c
    return None

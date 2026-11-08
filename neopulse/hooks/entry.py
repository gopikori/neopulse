"""Hook entry point: `python entry.py <provider>` with the agent's JSON event on stdin.

Kept dependency-light so each hook costs ~50 ms. It appends the event to the log and refreshes
the device unless a daemon is running (the daemon then picks the change up on its next tick).
Never prints to stdout: agent hooks feed stdout back into the model's context.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    provider = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from neopulse import monitor
        if monitor.is_internal(payload):
            return 0
        rec = monitor.normalize(provider, payload)
        monitor.append_event(rec)
        if not monitor.daemon_running():
            monitor.refresh()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

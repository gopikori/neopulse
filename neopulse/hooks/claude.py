"""Claude Code: hooks live in ~/.claude/settings.json under "hooks"."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import hook_command, is_ours

EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
          "Notification", "Stop", "SessionEnd")


def settings_path(base: Path | None = None) -> Path:
    return (base or Path.home()) / ".claude" / "settings.json"


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except ValueError as e:
        raise RuntimeError(f"{path} is not valid JSON: {e}")


def _strip(entries: list) -> list:
    out = []
    for e in entries:
        if not isinstance(e, dict):
            out.append(e)
            continue
        hooks = [h for h in e.get("hooks", []) if not (isinstance(h, dict) and is_ours(str(h.get("command", ""))))]
        if hooks:
            out.append({**e, "hooks": hooks})
    return out


def _save(path: Path, data: dict) -> Path | None:
    backup = None
    if path.exists():
        backup = path.with_suffix(f".json.bak-{time.strftime('%Y%m%d%H%M%S')}")
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return backup


def install(base: Path | None = None, dry_run: bool = False) -> str:
    path = settings_path(base)
    data = _load(path)
    hooks = data.setdefault("hooks", {})
    cmd = hook_command("claude")
    for ev in EVENTS:
        entries = _strip(hooks.get(ev, []) if isinstance(hooks.get(ev), list) else [])
        entries.append({"hooks": [{"type": "command", "command": cmd, "timeout": 10}]})
        hooks[ev] = entries
    if dry_run:
        return json.dumps({"hooks": hooks}, indent=2)
    backup = _save(path, data)
    return f"installed {len(EVENTS)} Claude Code hooks in {path}" + (f" (backup {backup.name})" if backup else "")


def uninstall(base: Path | None = None) -> str:
    path = settings_path(base)
    data = _load(path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return "no Claude Code hooks to remove"
    for ev in list(hooks):
        if isinstance(hooks[ev], list):
            hooks[ev] = _strip(hooks[ev])
            if not hooks[ev]:
                del hooks[ev]
    if not hooks:
        data.pop("hooks", None)
    _save(path, data)
    return f"removed NeoPulse hooks from {path}"


def status(base: Path | None = None) -> dict:
    path = settings_path(base)
    data = _load(path)
    hooks = data.get("hooks", {}) if isinstance(data.get("hooks"), dict) else {}
    present = [ev for ev, entries in hooks.items() if isinstance(entries, list) and any(
        is_ours(str(h.get("command", ""))) for e in entries if isinstance(e, dict) for h in e.get("hooks", []) if isinstance(h, dict))]
    return {"provider": "claude", "path": str(path), "installed": bool(present), "events": sorted(present)}

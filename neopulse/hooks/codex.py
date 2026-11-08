"""Codex CLI: hooks are [[hooks.<Event>]] tables in ~/.codex/config.toml, gated by
[features] hooks = true. Codex asks the user to trust new hooks on first launch; we also try
to pre-trust them through `codex app-server` so the prompt never appears."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import hook_command, is_ours

EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest",
          "Stop", "Interrupt")
START = "# >>> neopulse hooks >>>"
END = "# <<< neopulse hooks <<<"


def config_path(base: Path | None = None) -> Path:
    return (base or Path.home()) / ".codex" / "config.toml"


def _block() -> str:
    cmd = hook_command("codex")
    lines = [START, "# Managed by `neopulse hooks install codex`. Do not edit inside this block."]
    for ev in EVENTS:
        lines += [f"[[hooks.{ev}]]", 'matcher = "*"', f"[[hooks.{ev}.hooks]]", 'type = "command"',
                  f"command = '''{cmd}'''", "timeout = 10", ""]
    lines.append(END)
    return "\n".join(lines) + "\n"


def _strip_block(text: str) -> str:
    return re.sub(re.escape(START) + r".*?" + re.escape(END) + r"\n?", "", text, flags=re.S)


FEATURES_RE = r"^[ \t]*\[features\][ \t]*(#.*)?$"


def _ensure_feature(text: str) -> str:
    if re.search(r"^[ \t]*features[ \t]*=", text, re.M):      # inline table: features = { ... }
        if re.search(r"^[ \t]*features[ \t]*=[ \t]*\{[^}]*\bhooks[ \t]*=", text, re.M):
            return re.sub(r"(^[ \t]*features[ \t]*=[ \t]*\{[^}]*\bhooks[ \t]*=[ \t]*)\w+", r"\1true", text, count=1, flags=re.M)
        return re.sub(r"^([ \t]*features[ \t]*=[ \t]*\{)", r"\1 hooks = true,", text, count=1, flags=re.M)
    if re.search(FEATURES_RE, text, re.M):
        m = re.search(FEATURES_RE, text, re.M)
        rest = text[m.end():]
        nxt = re.search(r"^[ \t]*\[", rest, re.M)
        section = rest[: nxt.start()] if nxt else rest
        if re.search(r"^[ \t]*hooks[ \t]*=", section, re.M):
            section2 = re.sub(r"^[ \t]*hooks[ \t]*=.*$", "hooks = true", section, flags=re.M)
            return text[: m.end()] + section2 + (rest[nxt.start():] if nxt else "")
        return text[: m.end()] + "\nhooks = true" + rest
    return text.rstrip("\n") + "\n\n[features]\nhooks = true\n"


def _save(path: Path, text: str) -> Path | None:
    backup = None
    if path.exists():
        backup = path.with_suffix(f".toml.bak-{time.strftime('%Y%m%d%H%M%S')}")
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return backup


def _check_toml(text: str, path: Path) -> None:
    try:
        import tomllib
    except ImportError:  # Python 3.10: no validator available
        return
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise RuntimeError(f"refusing to write {path}: result would not be valid TOML ({e})")


def install(base: Path | None = None, dry_run: bool = False) -> str:
    path = config_path(base)
    orig = path.read_text() if path.exists() else ""
    text = _ensure_feature(_strip_block(orig)).rstrip("\n") + "\n\n" + _block()
    _check_toml(text, path)
    if dry_run:
        return _block()
    backup = _save(path, text)
    note = ""
    if base is None:
        trusted = _trust_hooks(path)
        note = f", pre-trusted {trusted} hook(s)" if trusted else ", Codex will ask you to trust the hooks on next launch"
    return f"installed {len(EVENTS)} Codex hooks in {path}" + (f" (backup {backup.name})" if backup else "") + note


def uninstall(base: Path | None = None) -> str:
    path = config_path(base)
    if not path.exists():
        return "no Codex config"
    text = _strip_block(path.read_text())
    text = re.sub(r'\n\[hooks\.state\."[^"]*neopulse[^"]*"\]\ntrusted_hash = "[^"]*"\n', "\n", text)
    _save(path, text)
    return f"removed NeoPulse hooks from {path}"


def status(base: Path | None = None) -> dict:
    path = config_path(base)
    text = path.read_text() if path.exists() else ""
    present = sorted(set(re.findall(r"\[\[hooks\.(\w+)\]\]", text[text.find(START):text.find(END)]))) if START in text else []
    return {"provider": "codex", "path": str(path), "installed": bool(present), "events": present}


# ---------------- hook trust via codex app-server ----------------
def _trust_hooks(config: Path, timeout: float = 8.0) -> int:
    codex = shutil.which("codex")
    if not codex:
        return 0
    req = "\n".join(json.dumps(m) for m in (
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"clientInfo": {"name": "neopulse", "version": "0"}, "capabilities": None}},
        {"jsonrpc": "2.0", "id": 2, "method": "hooks/list", "params": {"cwds": [str(Path.home())]}},
    )) + "\n"
    try:  # bounded: run() kills the child on timeout and still hands us the output so far
        out = subprocess.run([codex, "app-server", "--stdio"], input=req, capture_output=True,
                             text=True, timeout=timeout).stdout
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
    except OSError:
        return 0
    hashes: dict[str, str] = {}
    for line in out.splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("id") != 2:
            continue
        for h in (msg.get("result", {}).get("data") or [{}])[0].get("hooks", []):
            if isinstance(h, dict) and is_ours(str(h.get("command", ""))) and h.get("currentHash") and h.get("key"):
                hashes[h["key"]] = h["currentHash"]
    if not hashes:
        return 0
    text = config.read_text()
    if not re.search(r"^\s*\[hooks\.state\]\s*$", text, re.M):
        text = text.rstrip("\n") + "\n\n[hooks.state]\n"
    for key, hv in hashes.items():
        header = f'[hooks.state."{key.replace(chr(92), chr(92)*2).replace(chr(34), chr(92)+chr(34))}"]'
        if header in text:
            text = re.sub(re.escape(header) + r"\ntrusted_hash = \"[^\"]*\"", f'{header}\ntrusted_hash = "{hv}"', text)
        else:
            text = text.rstrip("\n") + f'\n\n{header}\ntrusted_hash = "{hv}"\n'
    config.write_text(text)
    return len(hashes)

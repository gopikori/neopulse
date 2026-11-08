"""Track agent sessions from hook events and keep the device in sync.

Everything is an append-only JSONL log (events.jsonl). Sessions are rebuilt by replaying the
tail of the log, so hooks, the CLI and the optional daemon never fight over shared state.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .paths import config_file, events_file, state_dir
from .states import Lane, State, aggregate, load_profile, rank, render

# Visibility rules (seconds)
DONE_VISIBLE = 20 * 60
IDLE_VISIBLE = 5 * 60
WORKING_STALE = 30 * 60      # a session that stopped sending events while "working" is gone
WAITING_STALE = 4 * 3600
TAIL_BYTES = 64 * 1024          # history kept after a rotation
ROTATE_BYTES = 512 * 1024       # rotate (checkpoint live sessions + keep the tail) above this

DEFAULT_CONFIG = {"mode": "unified", "profile": "default", "presence": False,
                  "transport": "auto", "device": None, "port": None, "max_lanes": None}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(config_file().read_text()))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    config_file().write_text(json.dumps(cfg, indent=2) + "\n")


def _locked(fn):
    lock_path = state_dir() / "lock"
    try:
        import fcntl
    except ImportError:
        return fn()
    with open(lock_path, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            return fn()
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)



# ---------------- events ----------------
WAIT_WORDS = ("permission", "approval", "approve", "waiting for", "needs your", "input", "question", "idle")


def state_for_event(provider: str, event: str, raw: dict) -> State | None | str:
    """Map a hook event to a State, None (no change), or "end" (drop the session)."""
    hint = str(raw.get("neopulse_state") or raw.get("state") or "").lower()
    if event == "Manual" and hint in State._value2member_map_:
        return State(hint)
    if event in ("SessionEnd",):
        return "end"
    if event in ("Interrupt",):
        return State.IDLE
    if event in ("PostToolUseFailure", "StopFailure", "PermissionDenied"):
        return State.BLOCKED
    if event == "PermissionRequest":
        return State.WAITING
    if event == "Notification":
        text = " ".join(str(raw.get(k, "")) for k in ("notification_type", "message", "title")).lower()
        if "complete" in text or "finished" in text:
            return State.DONE
        if any(w in text for w in WAIT_WORDS):
            return State.WAITING
        return State.WORKING
    if event in ("PreToolUse", "PostToolUse", "UserPromptSubmit", "PreCompact", "PostCompact", "SubagentStart"):
        return State.WORKING
    if event == "Stop":
        msg = str(raw.get("last_assistant_message") or "").strip()
        if msg.endswith("?"):
            return State.WAITING
        return State.DONE
    if event == "SessionStart":
        return State.IDLE
    return None  # SubagentStop and unknown events: leave the session as is


def normalize(provider: str, payload: dict) -> dict:
    event = str(payload.get("hook_event_name") or payload.get("event") or payload.get("type") or "Unknown")
    session = str(payload.get("session_id") or payload.get("thread_id") or payload.get("conversation_id") or "")
    rec = {
        "t": round(time.time(), 3),
        "provider": provider,
        "event": event,
        "session": session or f"{provider}-{os.getppid()}",
        "cwd": payload.get("cwd") or "",
    }
    if not isinstance(rec["cwd"], str):
        rec["cwd"] = ""
    for k in ("tool_name", "notification_type", "message", "title", "last_assistant_message",
              "neopulse_state", "state"):
        v = payload.get(k)
        if isinstance(v, str):
            rec[k] = v[-300:] if k == "last_assistant_message" else v[:300]   # keep the question mark
    return rec


def _checkpoint_records(sessions: "dict[str, Session]") -> list[dict]:
    """Synthetic records that rebuild the live sessions (and their pixel order) after a rotation."""
    out = []
    for x in sorted(sessions.values(), key=lambda x: (x.lane is None, x.lane or 0, x.updated)):
        prov, _, sid = x.key.partition(":")
        out.append({"t": x.updated, "provider": prov, "event": "Manual", "session": sid,
                    "cwd": x.cwd, "neopulse_state": x.state.value, "checkpoint": 1})
    return out


def append_event(rec: dict) -> None:
    path = events_file()

    def run():
        try:
            if path.exists() and path.stat().st_size > ROTATE_BYTES:
                live = build_sessions(read_records(), time.time())
                data = path.read_bytes()[-TAIL_BYTES:]
                tail = data[data.find(b"\n") + 1:]
                with open(path, "wb") as f:
                    for cp in _checkpoint_records(live):
                        f.write(json.dumps(cp, separators=(",", ":")).encode() + b"\n")
                    f.write(tail)
        except (OSError, ValueError):
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")

    _locked(run)


def read_records() -> list[dict]:
    """All records in the log (it is kept under ROTATE_BYTES, so this is cheap)."""
    try:
        lines = events_file().read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict) and isinstance(r.get("t"), (int, float)):
            out.append(r)
    return out


read_tail = read_records  # backwards-compatible name


# ---------------- sessions ----------------
@dataclass
class Session:
    key: str
    provider: str
    state: State
    updated: float
    cwd: str = ""
    label: str = ""
    lane: int | None = None   # pixel index this session owns; None = no free pixel (overflow)

    def age(self, now: float) -> float:
        return now - self.updated


def _expired(s: Session, now: float) -> bool:
    a = s.age(now)
    return ((s.state == State.DONE and a > DONE_VISIBLE) or (s.state == State.IDLE and a > IDLE_VISIBLE)
            or (s.state == State.WORKING and a > WORKING_STALE)
            or (s.state in (State.WAITING, State.BLOCKED) and a > WAITING_STALE))


def build_sessions(records: list[dict], now: float | None = None, lanes: int = 4) -> dict[str, Session]:
    """Replay the event log. Each session takes the lowest free pixel when it first appears and
    keeps it until it ends or expires. Expiry is applied as replay time advances, so a pixel freed
    an hour ago is free for a session that started later."""
    now = now or time.time()
    sessions: dict[str, Session] = {}

    def free_lane() -> int | None:
        used = {x.lane for x in sessions.values() if x.lane is not None}
        for i in range(lanes):
            if i not in used:
                return i
        return None

    def sweep(t: float) -> None:
        for k, x in list(sessions.items()):
            if _expired(x, t):
                del sessions[k]
        # promote overflow sessions into freed pixels, oldest first
        for x in sorted((x for x in sessions.values() if x.lane is None), key=lambda x: x.updated):
            x.lane = free_lane()

    for r in records:
        t = float(r.get("t", 0))
        sweep(t)
        ev = r.get("event", "")
        if ev == "Ack":  # touch pad / CLI acknowledgement: clear done + waiting
            for x in sessions.values():
                if x.state in (State.DONE, State.WAITING):
                    x.state, x.updated = State.IDLE, t
            continue
        if ev == "Clear":
            sessions.clear()
            continue
        key = f'{r.get("provider","?")}:{r.get("session","")}'
        st = state_for_event(r.get("provider", "?"), ev, r)
        if st == "end":
            sessions.pop(key, None)
            continue
        if st is None:
            if key in sessions:
                sessions[key].updated = t
            continue
        cwd = r.get("cwd") if isinstance(r.get("cwd"), str) else ""
        x = sessions.get(key)
        if x is None:
            x = sessions[key] = Session(key, str(r.get("provider", "?")), st, t, cwd, lane=free_lane())
        x.state, x.updated = st, t
        if cwd:
            x.cwd = cwd
        x.label = Path(x.cwd).name if x.cwd else str(r.get("session", ""))[-8:]
    sweep(now)
    return sessions


def current_sessions(now: float | None = None, lanes: int = 4) -> dict[str, Session]:
    return build_sessions(read_records(), now, lanes)


def slots_for(sessions: dict[str, Session], leds: int) -> list[Lane | None]:
    """One entry per pixel: the session that owns it, or None."""
    out: list[Lane | None] = [None] * leds
    for x in sessions.values():
        if x.lane is not None and x.lane < leds:
            out[x.lane] = Lane(x.state, x.provider)
    return out


def compute_program(sessions: dict[str, Session], leds: int, cfg: dict | None = None) -> str:
    cfg = cfg or load_config()
    profile = load_profile(cfg.get("profile", "default"))
    slots = slots_for(sessions, leds)
    agg = aggregate([x.state for x in sessions.values()])
    top = min(sessions.values(), key=lambda x: (rank(x.state), -x.updated), default=None)
    return render(cfg.get("mode", "lane"), agg, slots, leds, profile, bool(cfg.get("presence")),
                  provider=top.provider if top else None)


def daemon_running() -> bool:
    """True if daemon.pid names a live process (a stale file from a crash does not count)."""
    pid_file = state_dir() / "daemon.pid"
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


# ---------------- refresh (write to device) ----------------
_LAST = state_dir() / "last_program.txt"


def refresh(transport=None, force: bool = False, cfg: dict | None = None) -> str | None:
    """Recompute from the log and push to the device. Returns the program written (or None)."""
    cfg = cfg or load_config()

    def run():
        from .transport import TransportError, open_transport
        own = transport is None
        tr = transport
        try:
            if own:
                tr = open_transport(cfg.get("transport", "auto"), cfg.get("device"), cfg.get("port"))
            info = tr.info()
            leds = info.leds if info else 4
            prog = compute_program(current_sessions(lanes=leds), leds, cfg)
            try:
                prev = _LAST.read_text() if not force else None
                fresh = (time.time() - _LAST.stat().st_mtime) < 60 if not force else False
            except OSError:
                prev, fresh = None, False
            if prev == prog and fresh:
                return prog
            tr.write(prog)
            _LAST.write_text(prog)
            return prog
        except TransportError:
            return None
        finally:
            if own and tr is not None:
                tr.close()

    return _locked(run)


def ack() -> None:
    append_event({"t": round(time.time(), 3), "provider": "neopulse", "event": "Ack", "session": "-"})


def clear() -> None:
    append_event({"t": round(time.time(), 3), "provider": "neopulse", "event": "Clear", "session": "-"})


def manual(state: State, session: str = "manual", provider: str = "manual") -> None:
    append_event({"t": round(time.time(), 3), "provider": provider, "event": "Manual",
                  "session": session, "neopulse_state": state.value})

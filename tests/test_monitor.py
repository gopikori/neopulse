import json
import time

import pytest

from neopulse import monitor
from neopulse.states import State


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    yield


def ev(provider, event, session="s1", t=None, **extra):
    r = {"t": t or time.time(), "provider": provider, "event": event, "session": session, "cwd": "/w/proj"}
    r.update(extra)
    return r


def test_claude_lifecycle():
    now = time.time()
    recs = [ev("claude", "SessionStart", t=now - 10), ev("claude", "UserPromptSubmit", t=now - 9),
            ev("claude", "PreToolUse", t=now - 8), ev("claude", "PostToolUse", t=now - 7)]
    s = monitor.build_sessions(recs, now)
    assert s["claude:s1"].state == State.WORKING
    recs.append(ev("claude", "Notification", t=now - 6, notification_type="permission_prompt", message="Claude needs your permission"))
    assert monitor.build_sessions(recs, now)["claude:s1"].state == State.WAITING
    recs.append(ev("claude", "Stop", t=now - 5))
    assert monitor.build_sessions(recs, now)["claude:s1"].state == State.DONE
    recs.append(ev("claude", "Stop", t=now - 4, last_assistant_message="Shall I continue?"))
    assert monitor.build_sessions(recs, now)["claude:s1"].state == State.WAITING
    recs.append(ev("claude", "SessionEnd", t=now - 3))
    assert monitor.build_sessions(recs, now) == {}


def test_ack_and_expiry():
    now = time.time()
    recs = [ev("codex", "Stop", t=now - 60)]
    assert monitor.build_sessions(recs, now)["codex:s1"].state == State.DONE
    recs.append({"t": now - 30, "provider": "neopulse", "event": "Ack", "session": "-"})
    assert monitor.build_sessions(recs, now)["codex:s1"].state == State.IDLE
    assert monitor.build_sessions([ev("codex", "Stop", t=now - monitor.DONE_VISIBLE - 1)], now) == {}
    assert monitor.build_sessions([ev("codex", "PreToolUse", t=now - monitor.WORKING_STALE - 1)], now) == {}


def test_manual_and_program(tmp_path):
    monitor.manual(State.WAITING, "ci")
    sessions = monitor.current_sessions()
    assert sessions["manual:ci"].state == State.WAITING
    prog = monitor.compute_program(sessions, 4)
    assert "repeat" in prog
    monitor.clear()
    assert monitor.current_sessions() == {}


def test_normalize_claude_payload():
    rec = monitor.normalize("claude", {"hook_event_name": "PreToolUse", "session_id": "abc", "cwd": "/x", "tool_name": "Bash"})
    assert rec["event"] == "PreToolUse" and rec["session"] == "abc" and rec["tool_name"] == "Bash"


def test_refresh_null_transport():
    from neopulse.transport import NullTransport
    monitor.manual(State.WORKING, "x", "claude")
    tr = NullTransport(leds=4, out=open("/dev/null", "w"))
    prog = monitor.refresh(transport=tr, force=True)
    assert prog == tr.last and prog is not None

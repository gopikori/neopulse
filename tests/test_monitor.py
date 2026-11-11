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


def test_idle_prompt_does_not_turn_done_into_needs_you():
    now = time.time()
    idle = dict(notification_type="idle_prompt", message="Claude is waiting for your input")
    recs = [ev("claude", "UserPromptSubmit", t=now - 90), ev("claude", "Stop", t=now - 70),
            ev("claude", "Notification", t=now - 10, **idle)]
    assert monitor.build_sessions(recs, now)["claude:s1"].state == State.DONE
    recs = [ev("claude", "Stop", t=now - 70, last_assistant_message="Which one?"),
            ev("claude", "Notification", t=now - 10, **idle)]
    assert monitor.build_sessions(recs, now)["claude:s1"].state == State.WAITING


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


def test_sticky_lanes_and_overflow():
    now = time.time()
    recs = [ev("claude", "UserPromptSubmit", "a", t=now - 50), ev("codex", "UserPromptSubmit", "b", t=now - 40),
            ev("claude", "UserPromptSubmit", "c", t=now - 30), ev("codex", "UserPromptSubmit", "d", t=now - 20),
            ev("claude", "UserPromptSubmit", "e", t=now - 10)]
    s = monitor.build_sessions(recs, now)
    assert [s[k].lane for k in ("claude:a", "codex:b", "claude:c", "codex:d")] == [0, 1, 2, 3]
    assert s["claude:e"].lane is None                      # fifth session: no pixel
    # b becomes urgent: it stays on pixel 1 (no reshuffling)
    recs.append(ev("codex", "PermissionRequest", "b", t=now - 5))
    s = monitor.build_sessions(recs, now)
    assert s["codex:b"].lane == 1 and s["codex:b"].state == State.WAITING
    # a ends: its pixel 0 goes to the waiting overflow session e
    recs.append(ev("claude", "SessionEnd", "a", t=now - 4))
    recs.append(ev("claude", "PreToolUse", "e", t=now - 3))
    s = monitor.build_sessions(recs, now)
    assert "claude:a" not in s and s["claude:e"].lane == 0


def test_expired_pixel_is_reused_by_later_session():
    now = time.time()
    recs = [ev("claude", "Stop", "old", t=now - 3 * 3600),             # done long ago -> expired
            ev("codex", "UserPromptSubmit", "new", t=now - 10)]
    s = monitor.build_sessions(recs, now)
    assert list(s) == ["codex:new"] and s["codex:new"].lane == 0


def test_malformed_records_are_skipped_and_question_suffix_kept():
    now = time.time()
    recs = [{"t": now - 5, "provider": "claude", "event": "PreToolUse", "session": "s", "cwd": ["/tmp"]},
            "garbage", {"t": "x"}, ev("claude", "Stop", "s", t=now - 2, last_assistant_message="x" * 400 + " Continue?")]
    recs = [r for r in recs if isinstance(r, dict) and isinstance(r.get("t"), (int, float))]
    s = monitor.build_sessions(recs, now)
    assert s["claude:s"].cwd == "/w/proj" and s["claude:s"].state == State.WAITING   # list cwd ignored, later str kept
    rec = monitor.normalize("claude", {"hook_event_name": "Stop", "session_id": "s", "cwd": ["/tmp"],
                                       "last_assistant_message": "y" * 400 + " Continue?"})
    assert rec["cwd"] == "" and rec["last_assistant_message"].endswith("Continue?")


def test_rotation_keeps_live_sessions_and_pixels(monkeypatch):
    monkeypatch.setattr(monitor, "ROTATE_BYTES", 2000)
    monkeypatch.setattr(monitor, "TAIL_BYTES", 300)
    now = time.time()
    monitor.append_event(ev("claude", "UserPromptSubmit", "a", t=now - 100))
    monitor.append_event(ev("codex", "PermissionRequest", "b", t=now - 90))
    for i in range(40):                                   # heavy traffic from a third session
        monitor.append_event(ev("claude", "PreToolUse", "c", t=now - 80 + i, tool_name="Bash" * 20))
    s = monitor.current_sessions(now)
    assert s["claude:a"].lane == 0 and s["codex:b"].lane == 1 and s["codex:b"].state == State.WAITING
    assert s["claude:c"].lane == 2
    assert monitor.events_file().stat().st_size < 4000      # rotated, not grown without bound


def test_daemon_running_ignores_stale_pid():
    pid = monitor.state_dir() / "daemon.pid"
    pid.write_text("999999")
    assert monitor.daemon_running() is False
    import os
    pid.write_text(str(os.getpid()))
    assert monitor.daemon_running() is True


def test_internal_housekeeping_agents_get_no_pixel():
    from pathlib import Path
    now = time.time()
    recs = [ev("codex", "UserPromptSubmit", "main", t=now - 5),
            {"t": now - 4, "provider": "codex", "event": "PreToolUse", "session": "mem",
             "cwd": str(Path.home() / ".codex" / "memories"), "tool_name": "apply_patch"}]
    s = monitor.build_sessions(recs, now)
    assert list(s) == ["codex:main"] and s["codex:main"].lane == 0
    assert monitor.is_internal({"cwd": str(Path.home() / ".claude" / "x")})
    assert not monitor.is_internal({"cwd": str(Path.home() / "work" / ".claude-ish")})

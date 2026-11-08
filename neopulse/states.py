"""Agent states, priority, color profiles, and rendering of states into an LED program.

Two layouts:
  lane     -- pixel 0 shows the aggregate state, pixels 1..N-1 one agent session each
  unified  -- every pixel shows the aggregate state with the richer native animations
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from . import program as P
from . import animations as A
from .program import RGB, parse_color, rgb_hex, scale


class State(str, Enum):
    BLOCKED = "blocked"   # error / permission denied
    WAITING = "waiting"   # needs you: permission prompt or question
    WORKING = "working"   # thinking or running a tool
    DONE = "done"         # turn finished
    IDLE = "idle"         # session open, nothing happening


PRIORITY = [State.BLOCKED, State.WAITING, State.WORKING, State.DONE, State.IDLE]


def rank(s: State) -> int:
    return PRIORITY.index(s)


def aggregate(states: list[State]) -> State | None:
    return min(states, key=rank) if states else None


DEFAULT_ANIMATIONS = {"working": "roll", "waiting": "attention", "blocked": "kitt",
                      "done": "complete", "idle": "idle-pulse"}


@dataclass
class Profile:
    name: str
    colors: dict[str, RGB]
    provider_tint: dict[str, dict[str, RGB]] = field(default_factory=dict)
    presence: RGB | None = None
    description: str = ""
    animations: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ANIMATIONS))

    def animation(self, state: "State") -> str:
        return self.animations.get(state.value, DEFAULT_ANIMATIONS[state.value])

    def color(self, state: State, provider: str | None = None) -> RGB:
        if provider and state.value in self.provider_tint.get(provider, {}):
            return self.provider_tint[provider][state.value]
        return self.colors.get(state.value, (0, 0, 0))

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        def c(v):
            col = parse_color(v) if isinstance(v, str) else None
            return col if col is not None else (0, 0, 0)
        tint = {p: {k: c(v) for k, v in m.items()} for p, m in d.get("provider_tint", {}).items()}
        pres = d.get("presence")
        anims = dict(DEFAULT_ANIMATIONS)
        anims.update({k: v for k, v in d.get("animations", {}).items() if v in A.ANIMATIONS})
        return cls(name=d.get("name", "custom"), colors={k: c(v) for k, v in d["colors"].items()},
                   provider_tint=tint, presence=c(pres) if pres else None,
                   description=d.get("description", ""), animations=anims)


def builtin_profile_dir() -> Path:
    return Path(__file__).parent / "profiles"


def user_profile_dir() -> Path:
    from .paths import config_dir
    return config_dir() / "profiles"


def list_profiles() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for d in (builtin_profile_dir(), user_profile_dir()):
        if d.is_dir():
            for p in sorted(d.glob("*.json")):
                out[p.stem] = p
    return out


def load_profile(name: str = "default") -> Profile:
    p = Path(name)
    if not p.suffix == ".json" or not p.exists():
        paths = list_profiles()
        if name not in paths:
            raise FileNotFoundError(f"profile {name!r} not found; have {', '.join(paths)}")
        p = paths[name]
    return Profile.from_dict(json.loads(p.read_text()))


# ---------------- lane layout: keyframe animation ----------------
KEYFRAMES = 8
STEP_MS = 250  # 8 x 250 ms = 2 s cycle


def _intensity(state: State, k: int, presence: bool) -> float:
    f = k / KEYFRAMES
    if state == State.WORKING:
        return 0.55 + 0.45 * math.sin(2 * math.pi * f - math.pi / 2)  # breathe 0.1..1
    if state == State.WAITING:
        return (0.0, 1.0, 0.25, 0.0, 0.0, 1.0, 0.25, 0.0)[k % 8]      # two sharp pulses
    if state == State.BLOCKED:
        return (1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0)[k % 8]         # double blink
    if state == State.DONE:
        return 1.0
    return 1.0 if presence else 0.0


@dataclass
class Lane:
    state: State
    provider: str | None = None


def render_lanes(slots: list[Lane | None], leds: int, profile: Profile, presence: bool = False) -> str:
    """One pixel per agent session. slots[i] is the session owning pixel i, or None."""
    if not any(slots) and not presence:
        return P.OFF
    cells: list[tuple[State, RGB]] = []
    for i in range(leds):
        ln = slots[i] if i < len(slots) else None
        if ln is None:
            cells.append((State.IDLE, profile.presence or (0, 0, 0)))
        else:
            cells.append((ln.state, profile.color(ln.state, ln.provider)))
    frames: list[list[RGB]] = []
    for k in range(KEYFRAMES):
        frames.append([scale(col, _intensity(st, k, presence)) for st, col in cells])
    text = P.frames(frames, STEP_MS)
    if len(text.encode()) > P.MAX_BYTES:  # many pixels: short hex, then fewer keyframes
        text = _compact(frames, STEP_MS)
    if len(text.encode()) > P.MAX_BYTES:
        text = _compact(frames[::2], STEP_MS * 2)
    return text


def _compact(frames: list[list[RGB]], step_ms: int) -> str:
    def px(c: RGB) -> str:
        return "off" if c == (0, 0, 0) else _short_hex(c)
    return "\n".join(" ".join(px(c) for c in f) + f" {step_ms}ms linear" for f in frames) + "\nrepeat"


def _short_hex(c: RGB) -> str:
    return "#%x%x%x" % tuple(min(15, round(v / 17)) for v in c)


# ---------------- unified layout: native animations ----------------
def render_unified(agg: State | None, leds: int, profile: Profile, provider: str | None = None,
                   presence: bool = False) -> str:
    """Every pixel shows the one most urgent state, using the profile's named animation."""
    if agg is None or agg == State.IDLE:
        if presence and profile.presence:
            return A.build(profile.animation(State.IDLE), profile.presence, leds)
        return P.OFF
    return A.build(profile.animation(agg), profile.color(agg, provider), leds)


def render(mode: str, agg: State | None, slots: list[Lane | None], leds: int, profile: Profile,
           presence: bool = False, provider: str | None = None) -> str:
    if mode == "off":
        return P.OFF
    if mode == "unified":
        return render_unified(agg, leds, profile, provider, presence)
    return render_lanes(slots, leds, profile, presence)

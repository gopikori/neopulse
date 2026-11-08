"""Animation library: named LED programs generated for any pixel count.

Names match SidePulse's built-ins where the look is the same (roll, kitt, night-rider, tide,
attention, complete, idle-pulse, off) so profiles written for either tool read the same.
Every function returns program text for the LEDS.LED language (spec/PROTOCOL.md).
"""
from __future__ import annotations

from typing import Callable

from .program import RGB, parse_color, rgb_hex


def _c(color: str | RGB) -> str:
    return color if isinstance(color, str) else rgb_hex(color)


def _rgb(color: str | RGB) -> RGB:
    c = parse_color(color) if isinstance(color, str) else color
    return c if c is not None else (0, 0, 0)


def off() -> str:
    return "off"


def solid(color, fade_ms: int = 320) -> str:
    """Hold one color (SidePulse 'complete')."""
    return f"{_c(color)} {fade_ms}ms cosine"


complete = solid


def attention(color, period_ms: int = 1600) -> str:
    """Whole-board breathing pulse from dark (SidePulse 'attention' / 'amber-pulse')."""
    return f"off\n{_c(color)} {period_ms}ms pulse\nrepeat"


def breathe(color, period_ms: int = 2000, floor: float = 0.1) -> str:
    """Breathe between a dim floor and full color, never fully dark."""
    lo = rgb_hex(tuple(int(v * floor) for v in _rgb(color)))
    return f"{lo} 300ms cosine\n{_c(color)} {period_ms}ms pulse\nrepeat"


def roll(color, leds: int, pulse_ms: int = 760, step_ms: int | None = None) -> str:
    """Staggered pulses travelling along the strip (SidePulse 'cyan-roll' / 'tide')."""
    step = step_ms if step_ms is not None else (260 if leds <= 2 else max(60, 760 // leds))
    segs = "; ".join(f"{i}:{_c(color)} {pulse_ms}ms pulse {i * step}ms" for i in range(leds))
    return f"off 320ms cosine\n{segs}\nrepeat"


tide = roll


def kitt(color, leds: int, pulse_ms: int = 320, step_ms: int | None = None) -> str:
    """Scanner sweeping out and back (SidePulse 'kitt')."""
    step = step_ms if step_ms is not None else max(60, pulse_ms * 85 // 320)
    fwd = "; ".join(f"{i}:{_c(color)} {pulse_ms}ms pulse {i * step}ms" for i in range(leds))
    back_idx = list(range(leds - 2, -1, -1)) if leds > 2 else list(range(leds - 1, -1, -1))
    back = "; ".join(f"{i}:{_c(color)} {pulse_ms}ms pulse {k * step}ms" for k, i in enumerate(back_idx))
    return f"off 80ms cosine\n{fwd}\n{back}\nrepeat"


def night_rider(color, leds: int, pulse_ms: int = 360) -> str:
    """Faster, tighter scanner (SidePulse 'night-rider')."""
    return kitt(color, leds, pulse_ms=pulse_ms, step_ms=pulse_ms // 4)


def chase(color, leds: int, loop_ms: int = 600, tail: float = 0.3) -> str:
    """One bright pixel with a fading tail circling the strip."""
    c = _rgb(color)
    cols = [rgb_hex(c)] + [rgb_hex(tuple(int(v * tail ** i) for v in c)) if tail ** i > 0.03 else "off" for i in range(1, leds)]
    return " ".join(cols) + f"\nroll-left {loop_ms}ms linear\nrepeat"


def blink(color, on_ms: int = 100, off_ms: int = 120, count: int = 2, gap_ms: int = 800) -> str:
    """Double blink with a gap (blocked / error)."""
    c = _c(color)
    lines = []
    for _ in range(count):
        lines += [f"{c} {on_ms}ms none", f"off {off_ms}ms none"]
    lines[-1] = f"off {gap_ms}ms none"
    return "\n".join(lines) + "\nrepeat"


def idle_pulse(color, leds: int, period_ms: int = 2000) -> str:
    """Very dim slow glow on the middle pixels (SidePulse 'idle-pulse')."""
    c = _rgb(color)
    dim = rgb_hex(tuple(int(v * 0.4) for v in c))
    if leds <= 2:
        body = " ".join(f"{i}:{dim}" for i in range(leds))
    else:
        mid = list(range(leds))[leds // 2 - 1: leds // 2 + 1]
        body = " ".join(f"{i}:{rgb_hex(c) if i in mid else dim}" for i in range(leds))
    return f"off {period_ms}ms\n{body} {period_ms}ms ease\nrepeat"


def sweep_off(leds: int, step_ms: int = 75) -> str:
    """Turn pixels off from the ends toward the middle (SidePulse 'lid-closed')."""
    lines = []
    for k in range(leds // 2 + leds % 2):
        a, b = k, leds - 1 - k
        seg = f"{a}:#000000 {step_ms}ms ease" + (f"; {b}:#000000 {step_ms}ms ease" if b != a else "")
        lines.append(seg)
    return "\n".join(lines) + "\n#000000 1s"


def fill_in(color, leds: int, step_ms: int = 90) -> str:
    """Light pixels one by one, then hold (startup / done flourish)."""
    lines = [f"{i}:{_c(color)} {step_ms}ms ease" for i in range(leds)]
    return "\n".join(lines)


def rainbow(leds: int, loop_ms: int = 2000) -> str:
    cols = ["#ff0044", "#ff8800", "#ffff00", "#00ff66", "#00ccff", "#004cff", "#8800ff", "#ff00cc"]
    return " ".join(cols[i * len(cols) // leds] for i in range(leds)) + f"\nroll {loop_ms}ms linear\nrepeat"


# name -> builder(color, leds). Profiles refer to these names.
ANIMATIONS: dict[str, Callable[..., str]] = {
    "off": lambda color, leds: off(),
    "solid": lambda color, leds: solid(color),
    "complete": lambda color, leds: complete(color),
    "attention": lambda color, leds: attention(color),
    "breathe": lambda color, leds: breathe(color),
    "roll": lambda color, leds: roll(color, leds),
    "tide": lambda color, leds: tide(color, leds),
    "kitt": lambda color, leds: kitt(color, leds),
    "night-rider": lambda color, leds: night_rider(color, leds),
    "chase": lambda color, leds: chase(color, leds),
    "blink": lambda color, leds: blink(color),
    "idle-pulse": lambda color, leds: idle_pulse(color, leds),
    "fill-in": lambda color, leds: fill_in(color, leds),
    "rainbow": lambda color, leds: rainbow(leds),
}


def build(name: str, color: str | RGB, leds: int) -> str:
    if name not in ANIMATIONS:
        raise KeyError(f"unknown animation {name!r}; have {', '.join(sorted(ANIMATIONS))}")
    return ANIMATIONS[name](color, leds)

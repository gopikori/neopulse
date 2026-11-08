"""LED program language: parse, validate, and build.

A program is plain text, one step per line (see spec/PROTOCOL.md). This module mirrors the
firmware parser so the host can reject bad programs before they reach the device, and offers
small builders so callers rarely write the text by hand.
"""
from __future__ import annotations

from dataclasses import dataclass, field

MAX_BYTES = 512
MAX_LINES = 24
EASINGS = {"linear", "ease", "ease-in", "ease-out", "ease-in-out", "cosine", "pulse", "none"}
HEX = set("0123456789abcdef")

RGB = tuple[int, int, int]


class ProgramError(ValueError):
    pass


def parse_color(tok: str) -> RGB | None:
    t = tok.lower()
    if t == "off":
        return (0, 0, 0)
    if not t.startswith("#"):
        return None
    h = t[1:]
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6 or any(c not in HEX for c in h):
        return None
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def parse_duration(tok: str) -> int | None:
    t = tok.lower()
    try:
        if t.endswith("ms"):
            return int(float(t[:-2]))
        if t.endswith("s"):
            return int(float(t[:-1]) * 1000)
    except ValueError:
        return None
    return None


def rgb_hex(c: RGB) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in c)


def scale(c: RGB, f: float) -> RGB:
    return (int(c[0] * f), int(c[1] * f), int(c[2] * f))


@dataclass
class Step:
    kind: str                      # "color" | "roll"
    colors: list[RGB | None] = field(default_factory=list)  # per pixel (None = unspecified)
    duration_ms: int = 330
    easing: str = "linear"
    direction: int = 1             # roll only


@dataclass
class Program:
    steps: list[Step] = field(default_factory=list)
    repeat: int | None = None      # None = hold last frame, 0 = forever, n = count
    brightness: int | None = None
    source: str = ""

    @property
    def text(self) -> str:
        return self.source


def _is_comment(line: str) -> bool:
    if line[0] in ";" or line.startswith("//"):
        return True
    return line[0] == "#" and (len(line) < 2 or line[1].lower() not in HEX)


def parse(text: str, leds: int | None = None) -> Program:
    """Parse and validate. Raises ProgramError with a line-specific message."""
    if len(text.encode()) > MAX_BYTES:
        raise ProgramError(f"program is {len(text.encode())} bytes, max {MAX_BYTES}")
    lines = text.replace("\r", "").split("\n")
    if len(lines) > MAX_LINES:
        raise ProgramError(f"{len(lines)} lines, max {MAX_LINES}")
    prog = Program(source=text)
    for ln_no, raw in enumerate(lines, 1):
        ln = raw.strip()
        if not ln or _is_comment(ln):
            continue
        toks = ln.lower().split()
        w = toks[0]
        if w == "repeat":
            try:
                prog.repeat = int(toks[1]) if len(toks) > 1 else 0
            except ValueError:
                raise ProgramError(f"line {ln_no}: bad repeat count")
            continue
        if w == "brightness":
            try:
                prog.brightness = max(0, min(100, int(toks[1])))
            except (ValueError, IndexError):
                raise ProgramError(f"line {ln_no}: brightness needs 0-100")
            continue
        if w in ("roll", "roll-left", "roll-right"):
            st = Step("roll", direction=-1 if w == "roll-left" else 1)
            for t in toks[1:]:
                d = parse_duration(t)
                if d is None:
                    raise ProgramError(f"line {ln_no}: unexpected token {t!r}")
                st.duration_ms = d
            prog.steps.append(st)
            continue
        st = Step("color")
        pos: list[RGB] = []
        idx: dict[int, RGB] = {}
        for t in toks:
            if t in EASINGS:
                st.easing = t
                continue
            d = parse_duration(t)
            if d is not None:
                st.duration_ms = d
                continue
            if ":" in t:
                i, c = t.split(":", 1)
                col = parse_color(c)
                if col is None or not i.isdigit():
                    raise ProgramError(f"line {ln_no}: bad indexed color {t!r}")
                idx[int(i)] = col
                continue
            col = parse_color(t)
            if col is None:
                raise ProgramError(f"line {ln_no}: unknown token {t!r}")
            pos.append(col)
        if not pos and not idx:
            raise ProgramError(f"line {ln_no}: no color in step")
        if pos and idx:
            raise ProgramError(f"line {ln_no}: mix of positional and indexed colors")
        if idx:
            n = leds or (max(idx) + 1)
            st.colors = [idx.get(i) for i in range(n)]
        else:
            n = leds or len(pos)
            st.colors = [pos[i % len(pos)] for i in range(n)]
        prog.steps.append(st)
    return prog


def validate(text: str, leds: int | None = None) -> str | None:
    """Return an error message or None."""
    try:
        parse(text, leds)
    except ProgramError as e:
        return str(e)
    return None


# ---------------- builders ----------------
def solid(color: str | RGB, fade_ms: int = 300) -> str:
    c = color if isinstance(color, str) else rgb_hex(color)
    return f"{c} {fade_ms}ms ease"


def pulse(color: str | RGB, period_ms: int = 1600, base: str = "off") -> str:
    c = color if isinstance(color, str) else rgb_hex(color)
    return f"{base}\n{c} {period_ms}ms pulse\nrepeat"


def blink(color: str | RGB, on_ms: int = 120, off_ms: int = 120, count: int = 2, gap_ms: int = 700) -> str:
    c = color if isinstance(color, str) else rgb_hex(color)
    lines = []
    for _ in range(count):
        lines += [f"{c} {on_ms}ms none", f"off {off_ms}ms none"]
    lines[-1] = f"off {gap_ms}ms none"
    lines.append("repeat")
    return "\n".join(lines)


def chase(color: str | RGB, leds: int, step_ms: int = 150, tail: float = 0.25) -> str:
    c = parse_color(color) if isinstance(color, str) else color
    assert c is not None
    cols = [rgb_hex(c)]
    for i in range(1, leds):
        cols.append(rgb_hex(scale(c, tail ** i)) if tail ** i > 0.03 else "off")
    return " ".join(cols) + f" 1ms none\nroll-left {step_ms}ms\nrepeat"


def frames(frame_colors: list[list[RGB]], step_ms: int, easing: str = "linear") -> str:
    """Keyframe animation: each inner list is one frame (per-pixel colors)."""
    lines = [" ".join(rgb_hex(c) for c in f) + f" {step_ms}ms {easing}" for f in frame_colors]
    lines.append("repeat")
    return "\n".join(lines)


OFF = "off"

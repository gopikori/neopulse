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
class Segment:
    colors: list[RGB | None]      # per pixel; None = this segment leaves the pixel alone
    duration_ms: int = 16
    easing: str = "linear"
    delay_ms: int = 0


@dataclass
class Step:
    kind: str                      # "color" | "roll" | "repeat"
    segments: list[Segment] = field(default_factory=list)
    duration_ms: int = 16          # color: longest delay+duration; roll: one full loop
    easing: str = "linear"         # roll easing / first segment easing
    direction: int = 1             # roll only
    count: int = 0                 # repeat only (0 = forever)

    @property
    def colors(self) -> list[RGB | None]:
        """Merged per-pixel targets of all segments (last assignment wins)."""
        n = max((len(sg.colors) for sg in self.segments), default=0)
        out: list[RGB | None] = [None] * n
        for sg in self.segments:
            for i, c in enumerate(sg.colors):
                if c is not None:
                    out[i] = c
        return out


@dataclass
class Program:
    steps: list[Step] = field(default_factory=list)
    brightness: int | None = None  # 0..255 program-local scale (SidePulse); None = 255
    source: str = ""

    @property
    def repeat(self) -> int | None:
        """Count of the last repeat marker (0 = forever), or None if the program holds."""
        marks = [st for st in self.steps if st.kind == "repeat"]
        return marks[-1].count if marks else None

    @property
    def text(self) -> str:
        return self.source


def _is_comment(line: str) -> bool:
    if line[0] in ";" or line.startswith("//"):
        return True
    return line[0] == "#" and (len(line) < 2 or line[1].lower() not in HEX)


def _parse_duration_token(tok: str) -> int | None:
    d = parse_duration(tok)
    if d is None and tok.isdigit():
        d = int(tok)  # bare integer = milliseconds
    return d


def parse_segment(text: str, leds: int | None, ln_no: int) -> Segment:
    toks = text.split()
    positional: list[RGB] = []
    indexed: dict[int, RGB] = {}
    duration: int | None = None
    delay = 0
    easing: str | None = None
    seen_time = False
    for t in toks:
        if t in EASINGS:
            easing = t
            continue
        d = _parse_duration_token(t)
        if d is not None:
            if duration is None and not seen_time:
                duration = d
            else:
                delay = d
            seen_time = True
            continue
        if ":" in t:
            i, c = t.split(":", 1)
            col = parse_color(c)
            if col is None or not i.isdigit():
                raise ProgramError(f"line {ln_no}: bad indexed color {t!r}")
            indexed[int(i)] = col
            continue
        col = parse_color(t)
        if col is None:
            raise ProgramError(f"line {ln_no}: unknown token {t!r}")
        positional.append(col)
    if positional and indexed:
        raise ProgramError(f"line {ln_no}: mix of positional and indexed colors")
    if not positional and not indexed:
        raise ProgramError(f"line {ln_no}: no color in segment {text.strip()!r}")
    if positional:
        n = leds or len(positional)
        if len(positional) == 1:
            colors: list[RGB | None] = [positional[0]] * n          # one color = every pixel
        else:
            colors = [positional[i] if i < len(positional) else (0, 0, 0) for i in range(n)]  # rest off
    else:
        n = leds or (max(indexed) + 1)
        colors = [indexed.get(i) for i in range(n)]                # unmentioned pixels hold
    if duration is None:
        duration = 330 if (easing is not None or delay) else 16
    return Segment(colors, max(1, duration), easing or "linear", delay)


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
        low = ln.lower()
        toks = low.split()
        w = toks[0]
        if w == "repeat":
            try:
                prog.steps.append(Step("repeat", count=int(toks[1]) if len(toks) > 1 else 0))
            except ValueError:
                raise ProgramError(f"line {ln_no}: bad repeat count")
            continue
        if w == "brightness":
            try:
                prog.brightness = max(0, min(255, int(toks[1])))
            except (ValueError, IndexError):
                raise ProgramError(f"line {ln_no}: brightness needs 0-255")
            continue
        if w in ("roll", "roll-left", "roll-right"):
            st = Step("roll", direction=-1 if w == "roll-left" else 1, duration_ms=330)
            for t in toks[1:]:
                d = _parse_duration_token(t)
                if d is not None:
                    st.duration_ms = d
                elif t in EASINGS:
                    st.easing = t
                else:
                    raise ProgramError(f"line {ln_no}: unexpected token {t!r}")
            prog.steps.append(st)
            continue
        segs = [parse_segment(part, leds, ln_no) for part in low.split(";") if part.strip()]
        if not segs:
            raise ProgramError(f"line {ln_no}: empty step")
        total = max(sg.duration_ms + sg.delay_ms for sg in segs)
        prog.steps.append(Step("color", segs, total, segs[0].easing))
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


def chase(color: str | RGB, leds: int, loop_ms: int = 600, tail: float = 0.3) -> str:
    c = parse_color(color) if isinstance(color, str) else color
    assert c is not None
    cols = [rgb_hex(c)]
    for i in range(1, leds):
        cols.append(rgb_hex(scale(c, tail ** i)) if tail ** i > 0.03 else "off")
    return " ".join(cols) + f"\nroll-left {loop_ms}ms linear\nrepeat"


def frames(frame_colors: list[list[RGB]], step_ms: int, easing: str = "linear") -> str:
    """Keyframe animation: each inner list is one frame (per-pixel colors)."""
    lines = [" ".join(rgb_hex(c) for c in f) + f" {step_ms}ms {easing}" for f in frame_colors]
    lines.append("repeat")
    return "\n".join(lines)


OFF = "off"

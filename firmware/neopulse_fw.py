# NeoPulse firmware -- CircuitPython. Drives NeoPixels from a tiny text program (LEDS.LED DSL,
# compatible with SidePulse: segments separated by ';', per-segment delay, roll, repeat blocks).
# Inputs:  /LEDS.LED and /CMD.TXT on the drive, usb_cdc.data serial.  Spec: spec/PROTOCOL.md.
# RAM is scarce (SAMD21): parse line by line, avoid big strings, catch MemoryError and go on.
import time
import math
import gc
import supervisor
import microcontroller
import neopixel
import usb_cdc
from boards.profile import PIXEL_PIN, PIXELS, PIXEL_ORDER, TOUCH_PINS

supervisor.runtime.autoreload = False  # host writes LEDS.LED; must not restart us
FW = "0.2.2"
N = PIXELS
N3 = N * 3
ALL = (1 << N) - 1
px = neopixel.NeoPixel(PIXEL_PIN, N, brightness=1.0, auto_write=False, pixel_order=PIXEL_ORDER)
ser = usb_cdc.data
if ser:
    ser.write_timeout = 0   # drop output rather than stall animations when the host is not reading

# ---------------- persistent config (NVM: magic, brightness, local-actions) ----------------
nvm = microcontroller.nvm
BRIGHT = 60   # 2..100 device brightness
LOCAL = 1     # 1 = taps act on the device (ack/mute); 0 = taps only emit events
MUTE = 0
if nvm and nvm[0] == 0x5A:
    BRIGHT, LOCAL = nvm[1], nvm[2]
    if BRIGHT > 100 or BRIGHT < 2:
        BRIGHT = 60


def save_cfg():
    try:
        nvm[0:3] = bytes((0x5A, BRIGHT, LOCAL))
    except Exception:
        pass


# ---------------- program parser ----------------
EASE = {"linear": 0, "ease": 1, "ease-in-out": 1, "cosine": 1, "ease-in": 2,
        "ease-out": 3, "pulse": 4, "none": 5}
HEX = "0123456789abcdef"


def color(t):
    if t == "off":
        return (0, 0, 0)
    if t[0] != "#":
        return None
    h = t[1:]
    if len(h) == 3:
        h = h[0] * 2 + h[1] * 2 + h[2] * 2
    if len(h) != 6:
        return None
    for c in h:
        if c not in HEX:
            return None
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def dur(t):
    try:
        if t.endswith("ms"):
            return int(float(t[:-2]))
        if t.endswith("s"):
            return int(float(t[:-1]) * 1000)
        if t.isdigit():
            return int(t)  # bare integer = milliseconds
    except ValueError:
        pass
    return None


class Prog:
    def __init__(self):
        # step kinds: (0, segs, total_ms)  (1, dur, ease, dir)=roll  (2, count)=repeat marker
        # seg: (mask, colors bytearray(N3), ms, ease, delay)
        self.steps = []
        self.bright = 255   # SidePulse scale 0..255, program-local


def parse_segment(text):
    toks = text.split()
    cols = bytearray(N3)
    mask = 0
    ms, ease, delay, pos = None, None, 0, 0
    seen_time = False
    for t in toks:
        if t in EASE:
            ease = EASE[t]
            continue
        m = dur(t)
        if m is not None:
            if ms is None and not seen_time:
                ms = m
            else:
                delay = m
            seen_time = True
            continue
        if ":" in t:
            i, c = t.split(":", 1)
            c = color(c)
            if c is None:
                raise ValueError(t)
            i = int(i)
            if 0 <= i < N:
                cols[i * 3:i * 3 + 3] = bytes(c)
                mask |= 1 << i
            continue
        c = color(t)
        if c is None:
            raise ValueError(t)
        if pos < N:
            cols[pos * 3:pos * 3 + 3] = bytes(c)
        pos += 1
    if pos and mask:
        raise ValueError("mix")
    if pos:                     # positional: given LEDs set, the rest turn off
        mask = ALL
        if pos == 1:            # a single color means all LEDs
            for i in range(1, N):
                cols[i * 3:i * 3 + 3] = cols[0:3]
    if not mask:
        raise ValueError(text)
    if ms is None:
        ms = 330 if (ease is not None or delay) else 16
    if ease is None:
        ease = 0
    return (mask, cols, max(1, ms), ease, delay)


def parse_line(p, ln):
    """Add one program line to Prog p. Raises ValueError on bad input."""
    ln = ln.strip()
    if not ln or ln[0] == ";" or ln.startswith("//"):
        return
    if ln[0] == "#" and (len(ln) < 2 or ln[1].lower() not in HEX):
        return  # "# comment"
    low = ln.lower()
    toks = low.split()
    w = toks[0]
    if w == "repeat":
        p.steps.append((2, int(toks[1]) if len(toks) > 1 else 0))
        return
    if w == "brightness":
        p.bright = max(0, min(255, int(toks[1])))
        return
    if len(p.steps) >= 24:
        raise ValueError("steps")
    if w == "roll" or w == "roll-right" or w == "roll-left":
        d, e = 330, 0
        for t in toks[1:]:
            m = dur(t)
            if m is not None:
                d = m
            elif t in EASE:
                e = EASE[t]
            else:
                raise ValueError(t)
        p.steps.append((1, d, e, -1 if w == "roll-left" else 1))
        return
    segs = []
    total = 1
    for part in low.split(";"):
        if part.strip():
            if len(segs) >= 2 * N:
                raise ValueError("segs")
            s = parse_segment(part)
            segs.append(s)
            total = max(total, s[2] + s[4])
    if not segs:
        raise ValueError(ln)
    p.steps.append((0, segs, total))


def parse_lines(lines):
    p = Prog()
    for ln in lines:
        parse_line(p, ln)
    return p


# ---------------- animation engine ----------------
cur = bytearray(N3)    # frame being displayed (before brightness/gamma)
base = bytearray(N3)   # frame at the start of the current step
prog = Prog()
si = 0
t0 = 0
done = True
loops = -1             # remaining loops for the repeat marker being executed


def now():
    return supervisor.ticks_ms()


def since(t):
    return (supervisor.ticks_ms() - t) & 0x1FFFFFFF


def load(p):
    global prog, si, t0, done, loops
    prog = p
    si = 0
    loops = -1
    t0 = now()
    base[:] = cur
    done = not p.steps
    skip_markers()


def skip_markers():
    """Execute repeat markers at si (jumping back or passing); leaves si on a real step or done."""
    global si, done, loops
    while not done:
        if si >= len(prog.steps):
            done = True
            return
        st = prog.steps[si]
        if st[0] != 2:
            return
        count = st[1]
        start = 0
        for j in range(si - 1, -1, -1):   # block starts after the previous marker
            if prog.steps[j][0] == 2:
                start = j + 1
                break
        if start == si:                   # empty block: nothing to repeat, pass through
            loops = -1
            si += 1
            continue
        if count == 0:
            si = start
            continue
        if loops < 0:
            loops = count - 1
        if loops > 0:
            loops -= 1
            si = start
        else:
            loops = -1
            si += 1


def ease_f(e, f):
    if e == 5:
        return 1.0
    if e == 4:
        return math.sin(math.pi * f)
    if e == 2:
        return f * f
    if e == 3:
        return 1 - (1 - f) * (1 - f)
    if e == 1:
        return (1 - math.cos(math.pi * f)) / 2
    return f


def blend_pixel(i, tgt, g):
    gi = int(g * 1024)
    for k in range(i * 3, i * 3 + 3):
        cur[k] = (base[k] * (1024 - gi) + tgt[k] * gi) >> 10


def tick():
    global si, t0, done
    if done:
        return
    st = prog.steps[si]
    el = since(t0)
    if st[0] == 1:                         # roll: one full wraparound over dur
        d, e, direction = st[1], st[2], st[3]
        if el >= d:
            cur[:] = base
            finish_step()
            return
        shift = ease_f(e, el / d) * N      # 0..N positions
        whole = int(shift)
        frac = shift - whole
        for i in range(N):
            a = ((i - direction * whole) % N) * 3
            b = ((i - direction * (whole + 1)) % N) * 3
            gi = int(frac * 1024)
            for k in range(3):
                cur[i * 3 + k] = (base[a + k] * (1024 - gi) + base[b + k] * gi) >> 10
        return
    segs, total = st[1], st[2]
    if el >= total:
        for mask, cols, ms, e, delay in segs:
            for i in range(N):
                if mask >> i & 1:
                    if e == 4:
                        cur[i * 3:i * 3 + 3] = base[i * 3:i * 3 + 3]
                    else:
                        cur[i * 3:i * 3 + 3] = cols[i * 3:i * 3 + 3]
        finish_step()
        return
    for mask, cols, ms, e, delay in segs:
        le = el - delay
        if le < 0:
            continue
        f = le / ms
        if f >= 1:
            g = 0.0 if e == 4 else 1.0
        else:
            g = ease_f(e, f)
        for i in range(N):
            if mask >> i & 1:
                blend_pixel(i, cols, g)


def finish_step():
    global si, t0
    base[:] = cur
    t0 = now()
    si += 1
    skip_markers()


fb_t = 0      # brightness feedback: show white at the new level until fb_len ms after fb_t
fb_len = 0


def bright_feedback(ms):
    global fb_t, fb_len
    fb_t = now()
    fb_len = ms


def show():
    if fb_len and since(fb_t) < fb_len:
        w = BRIGHT * 100 * 65025 // 2550000       # white at the device brightness, same gamma path
        px.fill((w << 16) | (w << 8) | w)
        px.show()
        return
    b = BRIGHT * prog.bright * 100 // 255   # 0..10000
    if MUTE:
        b = 0
    for i in range(N):
        r, g, bl = cur[i * 3], cur[i * 3 + 1], cur[i * 3 + 2]
        px[i] = (((r * r * b) // 2550000) << 16) | (((g * g * b) // 2550000) << 8) | ((bl * bl * b) // 2550000)
    px.show()


def flash(rgb, times, ms=120):
    """Blocking attention flash (errors / identify)."""
    for _ in range(times):
        px.fill(rgb)
        px.show()
        time.sleep(ms / 1000)
        px.fill(0)
        px.show()
        time.sleep(ms / 1000)


# ---------------- host I/O ----------------
def emit(s):
    if ser and ser.connected:
        try:
            ser.write(bytes(s + "\n", "utf-8"))
        except Exception:
            pass


def set_off():
    load(Prog())
    cur[:] = bytes(N3)


last_sum = -1
bad_sum = -1


def file_sum(path):
    """Cheap content checksum of a file, or None if absent / half written (NULs)."""
    s = 0
    try:
        with open(path, "r") as f:
            for ln in f:
                if "\x00" in ln:
                    return None
                for ch in ln:
                    s = (s * 31 + ord(ch)) & 0xFFFFFF
    except OSError:
        return None
    return s


def poll_file():
    global last_sum, bad_sum
    s = file_sum("/LEDS.LED")
    if s is None or s == last_sum:
        return
    gc.collect()
    try:
        with open("/LEDS.LED", "r") as f:
            p = parse_lines(f)
    except Exception as ex:
        if s == bad_sum:            # same bad content on two polls: it really is bad
            last_sum = s
            emit("evt prog err file %s" % ex)
            flash((40, 0, 0), 3, 80)
        else:
            bad_sum = s
        return
    bad_sum = -1
    last_sum = s
    load(p)
    emit("evt prog ok file")


last_cmd = -1
quiet = 0   # 1 while running CMD.TXT lines: no ok/err/pong/info replies, only events


def poll_cmd():
    global last_cmd, quiet
    s = file_sum("/CMD.TXT")
    if s is None or s == last_cmd:
        return
    last_cmd = s
    lines = []
    try:
        with open("/CMD.TXT", "r") as f:   # read first, close, then run: NVM writes fail while a file is open
            for ln in f:
                ln = ln.strip()
                if ln and ln[0] != "#" and len(lines) < 8:
                    lines.append(ln)
    except OSError:
        return
    quiet = 1
    for ln in lines:
        handle(ln)
    quiet = 0


def info():
    gc.collect()
    return "info neopulse=1 fw=%s leds=%d touch=%d bright=%d mute=%d local=%d mem=%d" % (
        FW, N, len(TOUCH_PINS), BRIGHT, MUTE, LOCAL, gc.mem_free())


def reply(s):
    if not quiet:
        emit(s)


def handle(line):
    global BRIGHT, LOCAL, MUTE
    parts = line.split(" ", 1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""
    if cmd == "write":
        gc.collect()
        try:
            p = parse_lines(arg.split("\\n"))
        except Exception as ex:
            emit("evt prog err serial %s" % ex)
            reply("err parse")
            flash((40, 0, 0), 3, 80)
            return
        load(p)
        emit("evt prog ok serial")
        reply("ok")
    elif cmd == "off":
        set_off()
        reply("ok")
    elif cmd == "info":
        reply(info())
    elif cmd == "ping":
        reply("pong")
    elif cmd == "bright":
        try:
            a = arg.strip()
            v = BRIGHT + int(a) if a[:1] in "+-" else int(a)
            BRIGHT = max(2, min(100, v))
            save_cfg()
            bright_feedback(250)
            emit("evt bright %d" % BRIGHT)
            reply("ok")
        except ValueError:
            reply("err bright")
    elif cmd == "mute":
        MUTE = 1 if arg.strip() == "1" else 0
        reply("ok")
    elif cmd == "local":
        LOCAL = 1 if arg.strip() == "1" else 0
        save_cfg()
        reply("ok")
    elif cmd == "identify":
        flash((120, 120, 120), 3)
        reply("ok")
    else:
        reply("err unknown " + cmd)


rx = bytearray()


def poll_serial():
    global rx
    if not ser:
        return
    n = ser.in_waiting
    if not n:
        return
    rx += ser.read(n)
    while b"\n" in rx:
        line, rx = rx.split(b"\n", 1)
        try:
            line = str(line, "utf-8").strip()
        except Exception:
            continue
        if line:
            handle(line)
    if len(rx) > 700:
        rx = bytearray()


# ---------------- touch ----------------
touch = []
if TOUCH_PINS:
    import touchio
    for pin in TOUCH_PINS:
        try:
            touch.append(touchio.TouchIn(pin))
        except Exception:
            pass
NT = len(touch)
t_down = [0] * NT
t_hold = [0] * NT


def on_tap(i):
    global MUTE
    emit("evt touch%d tap" % (i + 1))
    if not LOCAL:
        return
    if i == 0:
        set_off()
    elif i == 1:
        MUTE = 0 if MUTE else 1
        emit("evt mute %d" % MUTE)


def on_hold(i, first):
    global BRIGHT
    if first:
        emit("evt touch%d hold" % (i + 1))
    BRIGHT = max(2, min(100, BRIGHT + (10 if i == 1 else -10)))
    bright_feedback(300)
    emit("evt bright %d" % BRIGHT)


def poll_touch():
    for i in range(NT):
        if touch[i].value:
            if not t_down[i]:
                t_down[i] = now()
            elif since(t_down[i]) >= 500 and since(t_hold[i]) >= 250:
                on_hold(i, t_hold[i] == 0)
                t_hold[i] = now()
        elif t_down[i]:
            if t_hold[i] == 0:
                on_tap(i)
            else:
                save_cfg()
            t_down[i] = t_hold[i] = 0


# ---------------- main ----------------
try:
    with open("/INIT.LED", "r") as f:
        load(parse_lines(f))
    emit("evt prog ok init")
except Exception:
    load(parse_lines(["off", "#102030 1.2s pulse"]))  # hello blink
last_cmd = file_sum("/CMD.TXT") or -1
gc.collect()
print("neopulse " + info())

last_poll = 0
odd = 0
while True:
    try:
        t = now()
        if since(last_poll) >= 250:
            last_poll = t
            poll_file()
            odd ^= 1
            if odd:
                poll_cmd()
        poll_serial()
        if NT:
            poll_touch()
        tick()
        show()
        time.sleep(0.02)
    except MemoryError:
        set_off()
        gc.collect()
        emit("evt error memory")
    except Exception as ex:
        gc.collect()
        emit("evt error %s" % ex)
        time.sleep(0.1)

# NeoPulse firmware -- CircuitPython. Drives NeoPixels from a tiny text program.
# Inputs:  /LEDS.LED (host writes it)   usb_cdc.data serial ("write ...", "info", ...)
# Outputs: pixels, "evt ..." lines on the data serial (touch, brightness, file status)
# Spec: spec/PROTOCOL.md in the NeoPulse repo.
import time
import math
import supervisor
import microcontroller
import neopixel
import usb_cdc
from boards.profile import PIXEL_PIN, PIXELS, PIXEL_ORDER, TOUCH_PINS

supervisor.runtime.autoreload = False  # host writes LEDS.LED; must not restart us
FW = "0.1.0"
N = PIXELS
N3 = N * 3
px = neopixel.NeoPixel(PIXEL_PIN, N, brightness=1.0, auto_write=False, pixel_order=PIXEL_ORDER)
ser = usb_cdc.data

# ---------------- persistent config (NVM: magic, brightness, local-actions) ----------------
nvm = microcontroller.nvm
MAGIC = 0x5A
BRIGHT = 60   # 0..100
LOCAL = 1     # 1 = touch pads act locally (ack/mute/brightness); 0 = only emit events
MUTE = 0
if nvm and nvm[0] == MAGIC:
    BRIGHT, LOCAL = nvm[1], nvm[2]
    if BRIGHT > 100:
        BRIGHT = 60


def save_cfg():
    try:
        nvm[0:3] = bytes((MAGIC, BRIGHT, LOCAL))
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
    except ValueError:
        pass
    return None


def parse(text):
    """Return (steps, repeat, brightness). steps: [(colors|None, ms, ease_or_dir, kind)]
    kind 0 = color step, 1 = roll. repeat: None=hold last, 0=forever, n=count."""
    steps = []
    rep = None
    bright = None
    lines = text.replace("\r", "").split("\n")
    if len(lines) > 24:
        raise ValueError("too many lines")
    for ln in lines:
        ln = ln.strip()
        if not ln or ln[0] == ";" or ln.startswith("//"):
            continue
        if ln[0] == "#" and (len(ln) < 2 or ln[1].lower() not in HEX):
            continue  # "# comment"
        toks = ln.lower().split()
        w = toks[0]
        if w == "repeat":
            rep = int(toks[1]) if len(toks) > 1 else 0
            continue
        if w == "brightness":
            bright = max(0, min(100, int(toks[1])))
            continue
        if w in ("roll", "roll-right", "roll-left"):
            d = 330
            for t in toks[1:]:
                m = dur(t)
                if m is not None:
                    d = m
            steps.append((None, d, -1 if w == "roll-left" else 1, 1))
            continue
        cols = bytearray(N3)
        ms, ease, pos, idx, has = 330, 0, 0, False, False
        for t in toks:
            if t in EASE:
                ease = EASE[t]
                continue
            m = dur(t)
            if m is not None:
                ms = m
                continue
            if ":" in t:
                i, c = t.split(":", 1)
                c = color(c)
                if c is None:
                    raise ValueError(t)
                i = int(i)
                if 0 <= i < N:
                    cols[i * 3:i * 3 + 3] = bytes(c)
                idx = has = True
                continue
            c = color(t)
            if c is None:
                raise ValueError(t)
            if pos < N:
                cols[pos * 3:pos * 3 + 3] = bytes(c)
            pos += 1
            has = True
        if not has:
            raise ValueError(ln)
        if not idx and pos < N:  # tile the given colors across the strip
            for i in range(pos, N):
                j = (i % pos) * 3
                cols[i * 3:i * 3 + 3] = cols[j:j + 3]
        steps.append((cols, max(1, ms), ease, 0))
    return steps, rep, bright


# ---------------- animation engine ----------------
cur = bytearray(N3)    # frame being displayed (before brightness/gamma)
base = bytearray(N3)   # frame at the start of the current step
steps, rep, pbright = [], None, None
si = 0
loops = 0
t0 = 0
done = True


def now():
    return supervisor.ticks_ms()


def load(prog):
    global steps, rep, pbright, si, loops, t0, done
    steps, rep, pbright = prog
    si = loops = 0
    t0 = now()
    done = not steps
    base[:] = cur


def show():
    b = BRIGHT * (pbright if pbright is not None else 100)  # 0..10000
    if MUTE:
        b = 0
    for i in range(N):
        r, g, bl = cur[i * 3], cur[i * 3 + 1], cur[i * 3 + 2]
        # gamma ~2.0 then brightness
        px[i] = ((r * r * b) // 2550000, (g * g * b) // 2550000, (bl * bl * b) // 2550000)
    px.show()


def rotated(src, d):
    out = bytearray(N3)
    for i in range(N):
        j = ((i + d) % N) * 3
        out[i * 3:i * 3 + 3] = src[j:j + 3]
    return out


def lerp(a, b, g):  # g in 0..1 -> cur
    gi = int(g * 1024)
    for k in range(N3):
        cur[k] = (a[k] * (1024 - gi) + b[k] * gi) >> 10


def tick():
    global si, loops, t0, done
    if done or not steps:
        return
    cols, ms, e, kind = steps[si]
    el = (now() - t0) & 0x3FFFFFFF
    if el >= ms:
        # finalize this step
        if kind == 1:
            cur[:] = rotated(base, e)
        elif e == 4:  # pulse returns to base
            cur[:] = base
        else:
            cur[:] = cols
        base[:] = cur
        t0 = now()
        si += 1
        if si >= len(steps):
            if rep is None:
                si = len(steps) - 1
                done = True
            elif rep == 0 or loops + 1 < rep:
                loops += 1
                si = 0
            else:
                si = len(steps) - 1
                done = True
        return
    f = el / ms
    if kind == 1:
        lerp(base, rotated(base, e), f)
        return
    if e == 5:
        cur[:] = cols
    elif e == 4:
        lerp(base, cols, math.sin(math.pi * f))
    elif e == 2:
        lerp(base, cols, f * f)
    elif e == 3:
        lerp(base, cols, 1 - (1 - f) * (1 - f))
    elif e == 1:
        lerp(base, cols, (1 - math.cos(math.pi * f)) / 2)
    else:
        lerp(base, cols, f)


def flash(rgb, times, ms=120):
    """Blocking attention flash (errors / identify)."""
    for _ in range(times):
        for i in range(N):
            px[i] = rgb
        px.show()
        time.sleep(ms / 1000)
        px.fill((0, 0, 0))
        px.show()
        time.sleep(ms / 1000)
    base[:] = cur


# ---------------- host I/O ----------------
def emit(s):
    if ser and ser.connected:
        try:
            ser.write(bytes(s + "\n", "utf-8"))
        except Exception:
            pass


def apply_text(text, src):
    try:
        load(parse(text))
        emit("evt prog ok " + src)
        return True
    except Exception as ex:
        emit("evt prog err %s %s" % (src, ex))
        flash((40, 0, 0), 3, 80)
        return False


last_file = None


def poll_file():
    global last_file
    try:
        with open("/LEDS.LED", "r") as f:
            txt = f.read(600)
    except OSError:
        return
    if txt != last_file:
        last_file = txt
        apply_text(txt, "file")


def info():
    return "info neopulse=1 fw=%s leds=%d touch=%d bright=%d mute=%d local=%d" % (
        FW, N, len(TOUCH_PINS), BRIGHT, MUTE, LOCAL)


rx = bytearray()


def poll_serial():
    global BRIGHT, LOCAL, MUTE, rx
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
        if not line:
            continue
        parts = line.split(" ", 1)
        cmd = parts[0]
        arg = parts[1] if len(parts) > 1 else ""
        cmd = cmd.lower()
        if cmd == "write":
            if apply_text(arg.replace("\\n", "\n"), "serial"):
                emit("ok")
            else:
                emit("err parse")
        elif cmd == "off":
            load(([], None, None))
            cur[:] = bytes(N3)
            emit("ok")
        elif cmd == "info":
            emit(info())
        elif cmd == "ping":
            emit("pong")
        elif cmd == "bright":
            try:
                BRIGHT = max(0, min(100, int(arg)))
                save_cfg()
                emit("ok")
            except ValueError:
                emit("err bright")
        elif cmd == "mute":
            MUTE = 1 if arg.strip() == "1" else 0
            emit("ok")
        elif cmd == "local":
            LOCAL = 1 if arg.strip() == "1" else 0
            save_cfg()
            emit("ok")
        elif cmd == "identify":
            flash((120, 120, 120), 3)
            emit("ok")
        else:
            emit("err unknown " + cmd)
    if len(rx) > 700:
        rx = bytearray()


# ---------------- touch ----------------
touch = []
if TOUCH_PINS:
    import touchio
    for p in TOUCH_PINS:
        try:
            touch.append(touchio.TouchIn(p))
        except Exception:
            pass
t_down = [0] * len(touch)       # press timestamp or 0
t_fired = [False] * len(touch)  # hold already fired
t_last_rep = [0] * len(touch)
HOLD_MS = 500
REP_MS = 250
both_fired = False


def set_bright(v):
    global BRIGHT
    BRIGHT = max(5, min(100, v))
    emit("evt bright %d" % BRIGHT)


def on_tap(i):
    global MUTE
    emit("evt touch%d tap" % (i + 1))
    if not LOCAL:
        return
    if i == 0:          # ack: clear the light
        load(([], None, None))
        cur[:] = bytes(N3)
    elif i == 1:        # mute toggle
        MUTE = 0 if MUTE else 1
        emit("evt mute %d" % MUTE)


def on_hold(i, first):
    if first:
        emit("evt touch%d hold" % (i + 1))
    set_bright(BRIGHT + (10 if i == 1 else -10))  # brightness is always local


def poll_touch():
    global both_fired
    t = now()
    pressed = [tp.value for tp in touch]
    if len(pressed) >= 2 and pressed[0] and pressed[1]:
        if t_down[0] and t_down[1] and not both_fired and \
                t - min(t_down[0], t_down[1]) > 1500:
            both_fired = True
            emit("evt both hold")
            flash((120, 120, 120), 3)
            save_cfg()
        for i in range(len(touch)):
            t_fired[i] = True  # suppress individual taps
        if not t_down[0]:
            t_down[0] = t
        if not t_down[1]:
            t_down[1] = t
        return
    for i, p in enumerate(pressed):
        if p:
            if not t_down[i]:
                t_down[i] = t
                t_fired[i] = False
                t_last_rep[i] = t
            elif t - t_down[i] >= HOLD_MS and (t - t_last_rep[i] >= REP_MS):
                on_hold(i, not t_fired[i])
                t_fired[i] = True
                t_last_rep[i] = t
        elif t_down[i]:
            if not t_fired[i] and not both_fired:
                on_tap(i)
            if t_fired[i] and both_fired is False and LOCAL:
                save_cfg()
            t_down[i] = 0
            t_fired[i] = False
            if not any(pressed):
                both_fired = False


# ---------------- main ----------------
try:
    with open("/INIT.LED", "r") as f:
        apply_text(f.read(600), "init")
except OSError:
    load(parse("#102030 1.2s pulse"))  # hello blink
print("neopulse " + info())

last_poll = 0
while True:
    t = now()
    if (t - last_poll) & 0x3FFFFFFF >= 250:
        last_poll = t
        poll_file()
    poll_serial()
    if touch:
        poll_touch()
    tick()
    show()
    time.sleep(0.02)

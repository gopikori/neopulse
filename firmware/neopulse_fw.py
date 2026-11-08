# NeoPulse firmware -- CircuitPython. Drives NeoPixels from a tiny text program.
# Inputs:  /LEDS.LED (host writes it)   usb_cdc.data serial ("write ...", "info", ...)
# Outputs: pixels, "evt ..." lines on the data serial (touch, brightness, file status)
# Spec: spec/PROTOCOL.md.  RAM is scarce (SAMD21): parse line by line, avoid big strings.
import time
import math
import gc
import supervisor
import microcontroller
import neopixel
import usb_cdc
from boards.profile import PIXEL_PIN, PIXELS, PIXEL_ORDER, TOUCH_PINS

supervisor.runtime.autoreload = False  # host writes LEDS.LED; must not restart us
FW = "0.1.3"
N = PIXELS
N3 = N * 3
px = neopixel.NeoPixel(PIXEL_PIN, N, brightness=1.0, auto_write=False, pixel_order=PIXEL_ORDER)
ser = usb_cdc.data

# ---------------- persistent config (NVM: magic, brightness, local-actions) ----------------
nvm = microcontroller.nvm
BRIGHT = 60   # 0..100
LOCAL = 1     # 1 = taps act on the device (ack/mute); 0 = taps only emit events
MUTE = 0
if nvm and nvm[0] == 0x5A:
    BRIGHT, LOCAL = nvm[1], nvm[2]
    if BRIGHT > 100:
        BRIGHT = 60


def save_cfg():
    try:
        nvm[0:3] = bytes((0x5A, BRIGHT, LOCAL))
    except Exception:
        pass


# ---------------- program parser (line oriented) ----------------
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


class Prog:
    def __init__(self):
        self.steps = []   # (colors|None, ms, ease_or_dir, kind)  kind 0 color, 1 roll
        self.rep = None   # None hold, 0 forever, n times
        self.bright = None


def parse_line(p, ln):
    """Add one program line to Prog p. Raises ValueError on bad input."""
    ln = ln.strip()
    if not ln or ln[0] == ";" or ln.startswith("//"):
        return
    if ln[0] == "#" and (len(ln) < 2 or ln[1].lower() not in HEX):
        return  # "# comment"
    toks = ln.lower().split()
    w = toks[0]
    if w == "repeat":
        p.rep = int(toks[1]) if len(toks) > 1 else 0
        return
    if w == "brightness":
        p.bright = max(0, min(100, int(toks[1])))
        return
    if len(p.steps) >= 24:
        raise ValueError("too many steps")
    if w == "roll" or w == "roll-right" or w == "roll-left":
        d = 330
        for t in toks[1:]:
            m = dur(t)
            if m is not None:
                d = m
        p.steps.append((None, d, -1 if w == "roll-left" else 1, 1))
        return
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
    p.steps.append((cols, max(1, ms), ease, 0))


def parse_lines(lines):
    p = Prog()
    for ln in lines:
        parse_line(p, ln)
    return p


# ---------------- animation engine ----------------
cur = bytearray(N3)    # frame being displayed (before brightness/gamma)
base = bytearray(N3)   # frame at the start of the current step
tgt = bytearray(N3)    # target of the current step (roll targets are precomputed here)
prog = Prog()
si = 0
loops = 0
t0 = 0
done = True


def now():
    return supervisor.ticks_ms()


def begin_step():
    """Prepare tgt for steps[si]."""
    cols, ms, e, kind = prog.steps[si]
    if kind == 1:
        for i in range(N):
            j = ((i + e) % N) * 3
            tgt[i * 3:i * 3 + 3] = base[j:j + 3]
    else:
        tgt[:] = cols


def load(p):
    global prog, si, loops, t0, done
    prog = p
    si = loops = 0
    t0 = now()
    done = not p.steps
    base[:] = cur
    if not done:
        begin_step()


def show():
    b = BRIGHT * (prog.bright if prog.bright is not None else 100)  # 0..10000
    if MUTE:
        b = 0
    for i in range(N):
        r, g, bl = cur[i * 3], cur[i * 3 + 1], cur[i * 3 + 2]
        # gamma ~2.0 then brightness; pack as int to avoid tuple allocations
        px[i] = (((r * r * b) // 2550000) << 16) | (((g * g * b) // 2550000) << 8) | ((bl * bl * b) // 2550000)
    px.show()


def lerp(g):  # cur = base + (tgt - base) * g, g in 0..1
    gi = int(g * 1024)
    for k in range(N3):
        cur[k] = (base[k] * (1024 - gi) + tgt[k] * gi) >> 10


def tick():
    global si, loops, t0, done
    if done:
        return
    cols, ms, e, kind = prog.steps[si]
    el = (now() - t0) & 0x3FFFFFFF
    if el >= ms:
        if kind == 0 and e == 4:   # pulse returns to base
            cur[:] = base
        else:
            cur[:] = tgt
        base[:] = cur
        t0 = now()
        si += 1
        n = len(prog.steps)
        if si >= n:
            if prog.rep is None or (prog.rep and loops + 1 >= prog.rep):
                si = n - 1
                done = True
                return
            loops += 1
            si = 0
        begin_step()
        return
    f = el / ms
    if kind == 1 or e == 0:
        lerp(f)
    elif e == 5:
        cur[:] = tgt
    elif e == 4:
        lerp(math.sin(math.pi * f))
    elif e == 2:
        lerp(f * f)
    elif e == 3:
        lerp(1 - (1 - f) * (1 - f))
    else:
        lerp((1 - math.cos(math.pi * f)) / 2)


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


last_sum = -1   # checksum of the last applied LEDS.LED
bad_sum = -1    # checksum of a file that failed once (report only if stable)


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


def poll_cmd():
    """Run each non-comment line of /CMD.TXT through handle() when the file changes."""
    global last_cmd, quiet
    s = file_sum("/CMD.TXT")
    if s is None or s == last_cmd:
        return
    last_cmd = s
    quiet = 1
    try:
        with open("/CMD.TXT", "r") as f:
            for ln in f:
                ln = ln.strip()
                if ln and ln[0] != "#":
                    handle(ln)
    except OSError:
        pass
    quiet = 0


def info():
    gc.collect()
    return "info neopulse=1 fw=%s leds=%d touch=%d bright=%d mute=%d local=%d mem=%d" % (
        FW, N, len(TOUCH_PINS), BRIGHT, MUTE, LOCAL, gc.mem_free())


rx = bytearray()
quiet = 0   # 1 while running CMD.TXT lines: no ok/err/pong/info replies, only events


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
            BRIGHT = max(5, min(100, v))
            save_cfg()
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
t_down = [0] * NT       # press timestamp or 0
t_hold = [0] * NT       # 0 = no hold yet, else last repeat timestamp
both_fired = False


def on_tap(i):
    global MUTE
    emit("evt touch%d tap" % (i + 1))
    if not LOCAL:
        return
    if i == 0:          # ack: clear the light
        set_off()
    elif i == 1:        # mute toggle
        MUTE = 0 if MUTE else 1
        emit("evt mute %d" % MUTE)


def on_hold(i, first):
    global BRIGHT
    if first:
        emit("evt touch%d hold" % (i + 1))
    BRIGHT = max(5, min(100, BRIGHT + (10 if i == 1 else -10)))  # always local
    emit("evt bright %d" % BRIGHT)


def poll_touch():
    global both_fired
    t = now()
    any_down = False
    for i in range(NT):
        if touch[i].value:
            any_down = True
            if not t_down[i]:
                t_down[i] = t
            elif t - t_down[i] >= 500 and t - t_hold[i] >= 250:
                if not both_fired:
                    on_hold(i, t_hold[i] == 0)
                t_hold[i] = t
        elif t_down[i]:
            if t_hold[i] == 0 and not both_fired:
                on_tap(i)
            elif t_hold[i]:
                save_cfg()
            t_down[i] = t_hold[i] = 0
    if NT >= 2 and t_down[0] and t_down[1] and not both_fired and \
            t - max(t_down[0], t_down[1]) > 1500:
        both_fired = True
        t_hold[0] = t_hold[1] = t  # suppress taps on release
        emit("evt both hold")
        flash((120, 120, 120), 3)
        save_cfg()
    if not any_down:
        both_fired = False


# ---------------- main ----------------
try:
    with open("/INIT.LED", "r") as f:
        load(parse_lines(f))
    emit("evt prog ok init")
except Exception:
    load(parse_lines(["#102030 1.2s pulse"]))  # hello blink
last_cmd = file_sum("/CMD.TXT") or -1   # commands written before this boot are stale
gc.collect()
print("neopulse " + info())

last_poll = 0
odd = 0
while True:
    try:
        t = now()
        if (t - last_poll) & 0x3FFFFFFF >= 250:
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
        print("MemoryError, recovered")
    except Exception as ex:
        gc.collect()
        emit("evt error %s" % ex)
        print("loop error:", repr(ex))
        time.sleep(0.1)

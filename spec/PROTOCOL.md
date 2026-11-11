# NeoPulse device protocol

A NeoPulse device is any CircuitPython board running `firmware/` that exposes:

1. a USB mass-storage drive labelled `NEOPULSE` (falls back to `CIRCUITPY`), and
2. a second USB CDC serial port (the "data channel") for fast commands and events.

Hosts need only one of the two. The drive works with nothing but a shell; the serial channel adds
sub-100 ms latency, device info and touch events.

## Files on the drive

| File | Direction | Purpose |
|---|---|---|
| `LEDS.LED` | host → device | The current LED program. Rewrite it to change the light. Polled every 250 ms. Write a temp file and rename it over `LEDS.LED` so the device never sees a half-written file (the CLI does this via `LEDS.TMP`). Half-written files (NULs) are ignored; a parse error is reported only when the same bad content is seen on two polls. |
| `INIT.LED` | host → device | Program run at power-up. Optional. |
| `CMD.TXT` | host → device | Any serial command (below), one per line, for hosts without serial or when a daemon holds the port. Polled every 500 ms; the content present at boot is ignored (no replay); lines starting with `#` are ignored, so prefix a timestamp comment to re-run an identical command. Firmware ≥ 0.1.2. |
| `STATUS.TXT` | device → host | Device descriptor written at boot: `neopulse=1 fw=0.2.0 board=... leds=4 touch=2 uid=...` |
| `boards/profile.py` | – | Hardware profile (pin, pixel count, touch pins). Edit to port. |

A host must not expect to read `LEDS.LED` back: on CircuitPython the firmware cannot write the
filesystem while the host has it mounted. A parse error flashes all pixels red three times.

## Program language

One step per line, ≤ 512 bytes and ≤ 24 lines. Compatible with SidePulse `LEDS.LED` programs
(firmware ≥ 0.2.0): segments, per-segment delays, roll loops and repeat blocks behave the same.

```
; comment            // comment           # comment (a '#' not followed by hex)
#ffffff                                    all pixels white, immediately (one frame)
off 320ms cosine                           fade everything out over 320 ms
#ff0000 #00ff00                            positional: pixel 0 red, 1 green, the rest OFF
0:#ffffff 2:#0000ff 200ms                  indexed: only pixels 0 and 2 change, others HOLD
0:#00e5ff 760ms pulse 0ms; 1:#00e5ff 760ms pulse 95ms   two segments on one line, staggered
#f00 #f80 #ff0 #0f6                        a palette...
roll 2s linear                             ...rotated one full loop (N positions) over 2 s
brightness 128                             program brightness 0-255 (times device brightness)
repeat                                     loop forever from the line after the previous repeat
repeat 3                                   run that block 3 times, then continue with the next line
```

Tokens in a segment, in any order:

- color: `#rgb`, `#rrggbb`, `off`, or `i:#color` (indexed, 0-based). One positional color means every pixel.
- duration, then optionally delay: `330ms`, `0.5s`, `2s`, or a bare integer in ms. The first time
  token is the duration, the second is the delay before the segment starts.
- easing: `linear` (default), `ease` / `ease-in-out` / `cosine`, `ease-in`, `ease-out`,
  `pulse` (go to the color and back to where it started), `none` (jump, then hold).
- A segment with an easing or delay but no duration lasts 330 ms. With no timing at all it is immediate.

Semantics: every segment transitions its pixels from the frame on screen at the start of the
line to its colors over its duration, after its delay. The line ends when the longest
delay + duration ends. Pixels no segment mentions keep their color. `roll` / `roll-left` /
`roll-right` rotate the frame by one full wraparound over the duration (`roll` = right). Without
a `repeat` the last frame holds. A parse error flashes all pixels red three times.

## Serial data channel (115200 8N1, line based, `\n` terminated)

Commands (host → device) and replies:

| Command | Reply | Notes |
|---|---|---|
| `ping` | `pong` | Use to find the right port. |
| `info` | `info neopulse=1 fw=.. leds=4 touch=2 bright=60 mute=0 local=1 mem=6100` | `mem` is free heap in bytes. |
| `write <program>` | `ok` or `err parse` | Newlines escaped as the two characters `\n`. |
| `off` | `ok` | Clear the program. |
| `bright 1-100` or `bright +N` / `bright -N` | `evt bright <n>`, `ok` | Device brightness, absolute or relative, clamped to 1..100 and persisted in NVM. All pixels flash white at the new level for 250 ms as feedback. Use `mute 1` for dark. |
| `mute 0|1` | `ok` | Blank the pixels without losing the program. |
| `local 0|1` | `ok` | 1: touch taps act on the device (ack / mute). 0: taps only emit events (a host daemon owns them). Persisted. |
| `identify` | `ok` | Three white flashes. |

Events (device → host), may arrive at any time:

```
evt prog ok file|serial|init        a program was accepted (and from which source)
evt prog err <source> <token>       parse error
evt touch1 tap | evt touch2 tap     short tap (< 500 ms)
evt touch1 hold | evt touch2 hold   first fired after 500 ms
evt bright <n>                      brightness changed by a pad hold
evt mute <0|1>
evt error memory | evt error <text>   the main loop caught an exception and recovered (program dropped)
```

## Physical layout (Neo Trinkey)

Component side up, USB plug to the left. The NeoPixel chain runs clockwise from the top-left:

```
  USB ══════ [SAMD21] (reset)   (0) (1)  [2]   pad 2, top edge
                                (3) (2)  [1]   pad 1, bottom edge
```

Index 0 is top-left, 1 top-right, 2 bottom-right, 3 bottom-left. The host's lane mode gives
each agent session one pixel, assigned in that order when the session starts.

## Touch gestures (Neo Trinkey default profile)

| Gesture | Device-local action (`local=1`) | With host daemon (`local=0`) |
|---|---|---|
| pad 1 tap | clear the light (ack) | ack: done/waiting sessions go idle |
| pad 2 tap | mute toggle | cycle mode: lane → unified → off |
| pad 1 hold | brightness down (repeats); pixels show white at the live level while held | same (always local) |
| pad 2 hold | brightness up (repeats); pixels show white at the live level while held | same (always local) |

## Porting to other hardware

Copy `firmware/boards/profile.py`, set `PIXEL_PIN`, `PIXELS`, `PIXEL_ORDER`, `TOUCH_PINS` (may be
empty). Boards with more RAM can run `neopulse_fw.py` directly instead of the `.mpy`. The host
reads `leds=` from `STATUS.TXT` / `info` and lays out lanes accordingly.

## Firmware limits (Neo Trinkey)

The SAMD21 has about 15 KB of heap after CircuitPython boots and roughly 6 KB once the engine
is loaded, so the firmware never holds a whole program text in memory: it checksums `LEDS.LED`
line by line and parses straight from the file. If the main loop ever hits `MemoryError` it
drops the program, emits `evt error memory` and keeps running. Solid white on all pixels means
CircuitPython itself is idle (code.py is not running): `neopulse reset` sends Ctrl-C, Ctrl-D to
the console port to restart it, and the daemon does this on its own when the drive is present but
`ping` goes unanswered.

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
| `CMD.TXT` | host → device | Any serial command (below), one per line, for hosts without serial or when a daemon holds the port. Polled every 500 ms; lines starting with `#` are ignored, so prefix a timestamp comment to re-run an identical command. Firmware ≥ 0.1.2. |
| `STATUS.TXT` | device → host | Device descriptor written at boot: `neopulse=1 fw=0.1.2 board=... leds=4 touch=2 uid=...` |
| `boards/profile.py` | – | Hardware profile (pin, pixel count, touch pins). Edit to port. |

A host must not expect to read `LEDS.LED` back: on CircuitPython the firmware cannot write the
filesystem while the host has it mounted. A parse error flashes all pixels red three times.

## Program language

One step per line, ≤ 512 bytes and ≤ 24 lines. Compatible with SidePulse `LEDS.LED` programs.

```
; comment            // comment           # comment (a '#' not followed by hex)
off                                        all pixels off, 330 ms linear fade
#ff3a00 1.6s pulse                         fade off→color→off over 1.6 s (sine)
#f00 #0f0 500ms                            colors tile across the strip: r g r g
0:#ffffff 2:#0000ff 200ms none             indexed colors; unspecified pixels are off
#00e5ff off off off 1ms none               a frame...
roll-left 150ms                            ...then rotate it one pixel every 150 ms (chase)
brightness 40                              program brightness 0-100 (multiplied by device brightness)
repeat                                     loop forever;  `repeat 3` = three times then hold
```

Tokens on a color line, in any order:

- color: `#rgb`, `#rrggbb`, `off`, or `i:#color` (indexed; 0-based)
- duration: `330ms`, `0.5s`, `2s` (default 330 ms)
- easing: `linear` (default), `ease` / `ease-in-out` / `cosine`, `ease-in`, `ease-out`, `pulse` (go to the color and back), `none` (jump, then hold for the duration)

Semantics: every step transitions from the frame on screen at its start to its colors over its
duration. Without `repeat` the last frame holds. `roll` / `roll-left` / `roll-right` rotate the
current frame one pixel over the duration.

## Serial data channel (115200 8N1, line based, `\n` terminated)

Commands (host → device) and replies:

| Command | Reply | Notes |
|---|---|---|
| `ping` | `pong` | Use to find the right port. |
| `info` | `info neopulse=1 fw=.. leds=4 touch=2 bright=60 mute=0 local=1 mem=6100` | `mem` is free heap in bytes. |
| `write <program>` | `ok` or `err parse` | Newlines escaped as the two characters `\n`. |
| `off` | `ok` | Clear the program. |
| `bright 0-100` or `bright +N` / `bright -N` | `evt bright <n>`, `ok` | Device brightness, absolute or relative, persisted in NVM. |
| `mute 0|1` | `ok` | Blank the pixels without losing the program. |
| `local 0|1` | `ok` | 1: touch taps act on the device (ack / mute). 0: taps only emit events (a host daemon owns them). Persisted. |
| `identify` | `ok` | Three white flashes. |

Events (device → host), may arrive at any time:

```
evt prog ok file|serial|init        a program was accepted (and from which source)
evt prog err <source> <token>       parse error
evt touch1 tap | evt touch2 tap     short tap (< 500 ms)
evt touch1 hold | evt touch2 hold   first fired after 500 ms
evt both hold                       both pads held 1.5 s (device flashes white, saves config)
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

Index 0 is top-left, 1 top-right, 2 bottom-right, 3 bottom-left. Lane layout uses 0 as the
aggregate and 1, 2, 3 as sessions in clockwise order.

## Touch gestures (Neo Trinkey default profile)

| Gesture | Device-local action (`local=1`) | With host daemon (`local=0`) |
|---|---|---|
| pad 1 tap | clear the light (ack) | ack: done/waiting sessions go idle |
| pad 2 tap | mute toggle | cycle layout: lane → unified → off |
| pad 1 hold | brightness down (repeats) | same (always local) |
| pad 2 hold | brightness up (repeats) | same (always local) |
| both held 1.5 s | identify flash + save config | same |

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

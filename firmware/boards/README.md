# Board profiles

`profile.py` is the only hardware-specific file in the firmware. Copy the one that
matches your board to `CIRCUITPY/boards/profile.py`.

| File | Board | LEDs | Touch |
|---|---|---|---|
| `profile.py` (default) | Adafruit Neo Trinkey | 4 | 2 |
| `profile_generic_strip.py` | Any CircuitPython board + WS2812 strip on a pin | N | 0 |

A profile exports: `PIXEL_PIN`, `PIXELS`, `PIXEL_ORDER`, `TOUCH_PINS` (tuple, may be empty).

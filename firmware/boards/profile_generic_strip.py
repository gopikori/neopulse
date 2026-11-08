# Generic profile: WS2812/NeoPixel strip on a GPIO, no touch pads.
# Edit PIXEL_PIN and PIXELS for your wiring, then rename to profile.py.
import board

PIXEL_PIN = board.D5
PIXELS = 8
PIXEL_ORDER = "GRB"
TOUCH_PINS = ()

# Board profile for Adafruit Neo Trinkey (SAMD21E18, 4 NeoPixels, 2 touch pads).
# To port NeoPulse to other hardware, copy this file and change these values.
import board

PIXEL_PIN = board.NEOPIXEL
PIXELS = 4
PIXEL_ORDER = "GRB"
TOUCH_PINS = (board.TOUCH1, board.TOUCH2)

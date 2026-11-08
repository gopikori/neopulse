# NeoPulse boot.py -- runs once at power-up, before USB enumerates.
# 1. Rename the drive to NEOPULSE so hosts can find it.
# 2. Enable a second USB serial channel (usb_cdc.data) for fast commands/events.
# 3. Write STATUS.TXT (device descriptor) -- the only time firmware may write the FS.
import storage
import usb_cdc
import board
import microcontroller

FW = "0.1.1"

try:
    from boards.profile import PIXELS, TOUCH_PINS  # board profile
except Exception:
    PIXELS, TOUCH_PINS = 4, ()

usb_cdc.enable(console=True, data=True)

try:
    storage.remount("/", readonly=False)
    try:
        storage.getmount("/").label = "NEOPULSE"
    except Exception:
        pass
    with open("/STATUS.TXT", "w") as f:
        f.write("neopulse=1\nfw=%s\nboard=%s\nleds=%d\ntouch=%d\nuid=%s\n" % (
            FW, board.board_id, PIXELS, len(TOUCH_PINS),
            "".join("%02X" % b for b in microcontroller.cpu.uid)))
    storage.remount("/", readonly=True)
except Exception as e:
    print("boot.py:", e)

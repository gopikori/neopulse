"""Find NeoPulse hardware: the mounted drive and/or the data serial port."""
from __future__ import annotations

import glob
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ADAFRUIT_VID = 0x239A
VOLUME_NAMES = ("NEOPULSE", "CIRCUITPY")


@dataclass
class DeviceInfo:
    leds: int = 4
    touch: int = 0
    fw: str = "?"
    board: str = "?"
    uid: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_text(cls, text: str) -> "DeviceInfo":
        d: dict[str, str] = {}
        for tok in text.replace("\n", " ").split():
            if "=" in tok:
                k, v = tok.split("=", 1)
                d[k.strip()] = v.strip()
        return cls(
            leds=int(d.get("leds", 4) or 4), touch=int(d.get("touch", 0) or 0),
            fw=d.get("fw", "?"), board=d.get("board", "?"), uid=d.get("uid", ""), extra=d)


def mount_candidates() -> list[Path]:
    roots: list[str] = []
    if sys.platform == "darwin":
        roots = ["/Volumes"]
    elif sys.platform.startswith("linux"):
        user = os.environ.get("USER", "")
        roots = [f"/media/{user}", f"/run/media/{user}", "/media", "/mnt"]
    elif sys.platform == "win32":
        import string
        return [Path(f"{d}:\\") for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
    out: list[Path] = []
    for r in roots:
        for name in VOLUME_NAMES:
            p = Path(r) / name
            if p.is_dir():
                out.append(p)
    return out


def find_mount(explicit: str | None = None) -> Path | None:
    """Return the NeoPulse drive (has STATUS.TXT with neopulse=1) or a bare CIRCUITPY."""
    if explicit:
        p = Path(explicit)
        return p if p.is_dir() else None
    cands = mount_candidates()
    for p in cands:
        try:
            if "neopulse=1" in (p / "STATUS.TXT").read_text():
                return p
        except OSError:
            pass
    return cands[0] if cands else None


def read_status(mount: Path) -> DeviceInfo | None:
    try:
        return DeviceInfo.from_text((mount / "STATUS.TXT").read_text())
    except OSError:
        return None


def serial_candidates() -> list[str]:
    """Serial ports that look like an Adafruit CDC device, data interface first."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    ports = []
    for p in list_ports.comports():
        if p.vid == ADAFRUIT_VID or (p.manufacturer or "").lower().startswith("adafruit"):
            ports.append(p.device)
    if not ports and sys.platform == "darwin":
        ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    # CircuitPython exposes console first, data second; try the later ports first.
    return sorted(ports, reverse=True)


def probe_serial(port: str, timeout: float = 0.6) -> bool:
    """True if `ping` answers `pong` on this port (so it is the NeoPulse data channel)."""
    try:
        import serial
        with serial.Serial(port, 115200, timeout=timeout, write_timeout=timeout) as s:
            s.reset_input_buffer()
            s.write(b"ping\n")
            deadline = time.time() + timeout
            buf = b""
            while time.time() < deadline:
                buf += s.read(64)
                if b"pong" in buf:
                    return True
            return False
    except Exception:
        return False


def find_serial(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    for port in serial_candidates():
        if probe_serial(port):
            return port
    return None


def console_candidates(data_port: str | None = None) -> list[str]:
    """Ports that may be the CircuitPython console (everything Adafruit that is not the data port)."""
    return [p for p in sorted(serial_candidates()) if p != data_port]


def soft_reset(data_port: str | None = None, wait: float = 4.0) -> bool:
    """Restart code.py by sending Ctrl-C, Ctrl-D to the console. Returns True if the data
    channel answers ping afterwards. Use when the firmware has stopped (solid white pixels)."""
    try:
        import serial
    except ImportError:
        return False
    for port in console_candidates(data_port):
        try:
            with serial.Serial(port, 115200, timeout=0.2) as s:
                s.write(b"\x03")
                time.sleep(0.4)
                s.write(b"\x03")
                time.sleep(0.4)
                s.read(4096)
                s.write(b"\x04")
        except Exception:
            continue
        deadline = time.time() + wait
        while time.time() < deadline:
            time.sleep(0.5)
            dp = data_port or find_serial()
            if dp and probe_serial(dp):
                return True
    return False

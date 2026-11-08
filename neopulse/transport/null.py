from __future__ import annotations

import sys

from .base import Transport
from ..device import DeviceInfo


class NullTransport(Transport):
    """Dry run: print what would be sent."""
    name = "null"

    def __init__(self, leds: int = 4, out=None):
        self.leds = leds
        self.out = out or sys.stderr
        self.last: str | None = None

    def write(self, program: str) -> None:
        self.last = program
        print("--- neopulse (dry-run) program ---\n" + program.rstrip("\n"), file=self.out)

    def info(self) -> DeviceInfo | None:
        return DeviceInfo(leds=self.leds, touch=2, fw="dry-run", board="null")

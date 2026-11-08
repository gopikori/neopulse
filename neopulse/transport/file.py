from __future__ import annotations

import os
from pathlib import Path

from .base import Transport, TransportError
from ..device import DeviceInfo, read_status

PROGRAM_FILE = "LEDS.LED"
INIT_FILE = "INIT.LED"


class FileTransport(Transport):
    """Write LEDS.LED on the mounted drive. Works everywhere with no drivers."""
    name = "file"

    def __init__(self, mount: Path):
        self.mount = Path(mount)

    def _write(self, name: str, text: str) -> None:
        """Write via a temp file and rename, so the device never reads a half-written program."""
        if not text.endswith("\n"):
            text += "\n"
        path = self.mount / name
        tmp = self.mount / (name.split(".")[0] + ".TMP")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
            try:
                os.write(fd, text.encode())
                try:
                    os.fsync(fd)
                except OSError:
                    pass
            finally:
                os.close(fd)
            os.replace(tmp, path)
        except OSError as e:
            raise TransportError(f"cannot write {path}: {e}") from e

    def write(self, program: str) -> None:
        self._write(PROGRAM_FILE, program)

    def write_init(self, program: str) -> None:
        """Set the program the device runs at power-up."""
        self._write(INIT_FILE, program)

    def info(self) -> DeviceInfo | None:
        return read_status(self.mount)

    def __repr__(self) -> str:
        return f"FileTransport({self.mount})"

from __future__ import annotations

import time
from typing import Callable, Iterator

from .base import Transport, TransportError
from ..device import DeviceInfo


class SerialTransport(Transport):
    """Line protocol on the device's second USB CDC port. Fast, and it can read events."""
    name = "serial"

    def __init__(self, port: str, timeout: float = 1.0):
        import serial
        self.port = port
        try:
            self.ser = serial.Serial(port, 115200, timeout=0.05, write_timeout=timeout)
        except Exception as e:
            raise TransportError(f"cannot open {port}: {e}") from e
        self.timeout = timeout
        self._pending: list[str] = []
        self.on_event: Callable[[str], None] | None = None

    def _readline(self, deadline: float) -> str | None:
        buf = b""
        while time.time() < deadline:
            ch = self.ser.read(1)
            if not ch:
                continue
            if ch == b"\n":
                return buf.decode(errors="replace").strip()
            buf += ch
        return None

    def command(self, line: str) -> str | None:
        """Send one command and return its reply, dispatching any `evt` lines on the way."""
        self.ser.write((line.rstrip("\n") + "\n").encode())
        deadline = time.time() + self.timeout
        while True:
            reply = self._readline(deadline)
            if reply is None:
                raise TransportError(f"no reply to {line.split()[0]!r}")
            if reply.startswith("evt "):
                self._pending.append(reply)
                if self.on_event:
                    self.on_event(reply)
                continue
            return reply

    def write(self, program: str) -> None:
        escaped = program.replace("\r", "").rstrip("\n").replace("\n", "\\n")
        reply = self.command("write " + escaped)
        if reply != "ok":
            raise TransportError(f"device rejected program: {reply}")

    def off(self) -> None:
        self.command("off")

    def info(self) -> DeviceInfo | None:
        reply = self.command("info")
        return DeviceInfo.from_text(reply[5:]) if reply and reply.startswith("info ") else None

    def events(self, idle_timeout: float | None = None) -> Iterator[str]:
        """Yield `evt ...` lines as they arrive. Blocks; stop with KeyboardInterrupt."""
        while self._pending:
            yield self._pending.pop(0)
        buf = b""
        last = time.time()
        while True:
            ch = self.ser.read(1)
            if not ch:
                if idle_timeout and time.time() - last > idle_timeout:
                    return
                continue
            if ch == b"\n":
                line = buf.decode(errors="replace").strip()
                buf = b""
                last = time.time()
                if line.startswith("evt "):
                    yield line
                continue
            buf += ch

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass

    def __repr__(self) -> str:
        return f"SerialTransport({self.port})"

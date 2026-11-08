"""Transports move a program text to the device. Pick one explicitly or use open_transport()."""
from __future__ import annotations

from .base import Transport, TransportError
from .file import FileTransport
from .null import NullTransport


def open_transport(prefer: str = "auto", device: str | None = None, port: str | None = None) -> Transport:
    """prefer: auto | file | serial | null. Raises TransportError when nothing is found."""
    from ..device import find_mount, find_serial
    if prefer == "null":
        return NullTransport()
    if prefer in ("auto", "file"):
        m = find_mount(device)
        if m is not None:
            return FileTransport(m)
        if prefer == "file":
            raise TransportError("no NEOPULSE/CIRCUITPY drive mounted")
    if prefer in ("auto", "serial"):
        from .serial import SerialTransport
        p = find_serial(port)
        if p:
            return SerialTransport(p)
        if prefer == "serial":
            raise TransportError("no NeoPulse serial port answered ping")
    raise TransportError("no NeoPulse device found (no drive mounted, no serial port answered)")


__all__ = ["Transport", "TransportError", "FileTransport", "NullTransport", "open_transport"]

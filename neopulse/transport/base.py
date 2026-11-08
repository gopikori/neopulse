from __future__ import annotations

from ..device import DeviceInfo


class TransportError(RuntimeError):
    pass


class Transport:
    name = "base"

    def write(self, program: str) -> None:
        """Send a program (validated by the caller)."""
        raise NotImplementedError

    def off(self) -> None:
        self.write("off")

    def info(self) -> DeviceInfo | None:
        return None

    def command(self, line: str) -> str | None:
        """Send a control command (serial only). Returns the reply or None if unsupported."""
        return None

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

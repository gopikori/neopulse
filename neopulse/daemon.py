"""The background monitor as a reusable object: the CLI daemon and the menu-bar app both drive it.

Responsibilities: keep the device program in sync with the event log, own the data serial port
(touch events, device info), and recover a stopped firmware.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable

from . import monitor
from .device import find_mount, find_serial, soft_reset
from .paths import state_dir
from .transport import TransportError

MODE_CYCLE = ["unified", "lane", "off"]


class Daemon:
    def __init__(self, log: Callable[[str], None] = print):
        self.log = log
        self.ser = None
        self.misses = 0
        self.last_refresh = 0.0
        self.last_info = 0.0
        self.listeners: list[Callable[[str], None]] = []   # called with every "evt ..." line
        self.pid_file = state_dir() / "daemon.pid"
        self._stop = threading.Event()
        self.io = threading.RLock()   # serial port is used from the loop thread and UI callbacks

    # ---------- lifecycle ----------
    def start(self) -> None:
        self.pid_file.write_text(str(os.getpid()))

    def stop(self) -> None:
        self._stop.set()
        if self.ser is not None:
            try:
                self.ser.command("local 1")
                self.ser.close()
            except Exception:
                pass
            self.ser = None
        self.pid_file.unlink(missing_ok=True)

    # ---------- device ----------
    def connect(self) -> None:
        port = find_serial()
        if port:
            self.misses = 0
            try:
                from .transport.serial import SerialTransport
                with self.io:
                    self.ser = SerialTransport(port)
                    self.ser.command("local 0")  # we own the tap gestures
                self.log(f"serial {port} connected")
                self.publish_info()
            except TransportError:
                self.ser = None
            return
        self.misses += 1
        if self.misses == 5 and find_mount():  # drive present but firmware silent: restart it
            self.log("firmware not answering; soft reset")
            self.log("restarted" if soft_reset() else "reset failed")
            self.misses = 0

    def publish_info(self) -> None:
        if self.ser is None:
            return
        try:
            with self.io:
                i = self.ser.info()
            if i:
                d = dict(i.extra)
                d["t"] = time.time()
                for k in ("leds", "touch", "bright", "mute", "local", "mem"):
                    if k in d:
                        d[k] = int(d[k])
                (state_dir() / "device.json").write_text(json.dumps(d))
            self.last_info = time.time()
        except Exception as e:
            self.log(f"info error: {e}")

    def device_info(self) -> dict:
        try:
            return json.loads((state_dir() / "device.json").read_text())
        except (OSError, ValueError):
            return {}

    # ---------- actions ----------
    def refresh(self, force: bool = False) -> None:
        try:
            monitor.refresh(transport=None, force=force)
        except Exception as e:
            self.log(f"refresh error: {e}")

    def ack(self) -> None:
        monitor.ack()
        self.refresh(force=True)

    def set_mode(self, mode: str, indicate: bool = True) -> str:
        cfg = monitor.load_config()
        cfg["mode"] = mode
        monitor.save_config(cfg)
        if indicate and self.ser is not None:
            n = MODE_CYCLE.index(mode) + 1
            try:
                with self.io:
                    self.ser.write(" ".join(f"{i}:#ffffff" for i in range(n)) + " 1ms none")
                time.sleep(0.8)
            except TransportError:
                pass
        self.refresh(force=True)
        self.log(f"mode -> {mode}")
        return mode

    def cycle_mode(self) -> str:
        cfg = monitor.load_config()
        return self.set_mode(MODE_CYCLE[(MODE_CYCLE.index(cfg.get("mode", "unified")) + 1) % len(MODE_CYCLE)])

    def command(self, line: str) -> str | None:
        """Send a device command (bright, mute, identify) over serial, or via the drive."""
        if self.ser is not None:
            try:
                with self.io:
                    r = self.ser.command(line)
                    if line.startswith(("bright", "mute")):
                        self.publish_info()
                return r
            except TransportError as e:
                self.log(f"serial lost: {e}")
                self.ser = None
        try:
            from .transport import open_transport
            with open_transport("file", None, None) as tr:
                return tr.command(line)
        except TransportError:
            return None

    # ---------- event handling ----------
    def handle_event(self, ev: str) -> None:
        self.log(f"{time.strftime('%H:%M:%S')} {ev}")
        if ev.startswith("evt bright ") or ev.startswith("evt mute "):
            self.publish_info()
        if ev == "evt touch1 tap":
            self.ack()
        elif ev == "evt touch2 tap":
            self.cycle_mode()
        for fn in self.listeners:
            try:
                fn(ev)
            except Exception as e:
                self.log(f"listener error: {e}")

    # ---------- one scheduler tick (call every ~1 s) ----------
    def step(self, read_events_for: float = 1.0) -> None:
        if self.ser is None:
            self.connect()
        if time.time() - self.last_refresh > 2:
            self.last_refresh = time.time()
            self.refresh()
        if self.ser is not None and time.time() - self.last_info > 15:
            self.publish_info()
        if self.ser is None:
            time.sleep(min(1.0, read_events_for))
            return
        try:
            with self.io:   # UI callbacks wait at most read_events_for before they get the port
                evs = list(self.ser.events(idle_timeout=read_events_for))
            for ev in evs:
                self.handle_event(ev)
        except Exception as e:
            self.log(f"serial lost: {e}")
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def run_forever(self) -> None:
        """Blocking loop for the headless CLI daemon."""
        self.start()
        self.log(f"neopulse monitor running (pid {os.getpid()})")
        try:
            while not self._stop.is_set():
                self.step()
        finally:
            self.stop()

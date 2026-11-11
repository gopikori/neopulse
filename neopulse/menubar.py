"""macOS menu-bar app (rumps). It *is* the daemon: one process owns the serial port, keeps the
device in sync, and shows every session in the menu. Install the extra: pip install 'neopulse[mac]'."""
from __future__ import annotations

import queue
import subprocess
import threading
import time
from pathlib import Path

from . import __version__, monitor

from .daemon import MODE_CYCLE, Daemon
from .states import State, list_profiles

ICON = {State.BLOCKED: "🔴", State.WAITING: "🟡", State.WORKING: "🔵", State.DONE: "🟢", State.IDLE: "⚪"}
LABEL = {State.BLOCKED: "blocked", State.WAITING: "needs you", State.WORKING: "working",
         State.DONE: "done", State.IDLE: "idle"}
MODE_LABEL = {"unified": "One light (most urgent agent)", "lane": "One pixel per agent", "off": "Off (light stays dark)"}


DOT = {State.BLOCKED: "ff3b30", State.WAITING: "ffcc00", State.WORKING: "0a84ff", State.DONE: "30d158",
       State.IDLE: "8e8e93"}


def _icon_file(state, connected: bool, off: bool) -> str:
    """Status-bar icon: a disc in the state color with a tick (device reachable) or a cross (not)
    drawn inside it. Mode off is a hollow ring. Rendered once per variant and cached as a PNG."""
    name = f"{'off' if off else (state.value if state else 'idle')}-{'ok' if connected else 'no'}.png"
    path = monitor.state_dir() / "icons-v1" / name
    if path.exists():
        return str(path)
    from AppKit import (NSBezierPath, NSBitmapImageRep, NSColor, NSGraphicsContext, NSImage, NSMakePoint,
                        NSMakeRect, NSMakeSize, NSPNGFileType, NSRoundLineCapStyle, NSRoundLineJoinStyle)
    hexc = DOT.get(state, DOT[State.IDLE])
    r, g, b = (int(hexc[i:i + 2], 16) / 255 for i in (0, 2, 4))
    k = 4                                     # 22 pt drawn at 4x for a sharp retina icon
    img = NSImage.alloc().initWithSize_(NSMakeSize(22 * k, 22 * k))
    img.lockFocus()
    disc = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(2 * k, 2 * k, 18 * k, 18 * k))
    if off:
        NSColor.colorWithSRGBRed_green_blue_alpha_(.56, .56, .58, 1).set()
        disc.setLineWidth_(1.8 * k)
        disc.stroke()
        mark = NSColor.colorWithSRGBRed_green_blue_alpha_(.56, .56, .58, 1)
    else:
        NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1).set()
        disc.fill()
        dark = state == State.WAITING         # white is unreadable on yellow
        mark = NSColor.colorWithSRGBRed_green_blue_alpha_(*((.1, .1, .1) if dark else (1, 1, 1)), 1)
    mark.set()
    m = NSBezierPath.bezierPath()
    m.setLineWidth_(2.3 * k)
    m.setLineCapStyle_(NSRoundLineCapStyle)
    m.setLineJoinStyle_(NSRoundLineJoinStyle)
    if connected:                             # tick
        for i, (x, y) in enumerate(((6.6, 11.2), (9.8, 7.8), (15.6, 14.6))):
            (m.moveToPoint_ if i == 0 else m.lineToPoint_)(NSMakePoint(x * k, y * k))
    else:                                     # cross
        for (x1, y1), (x2, y2) in (((7.6, 7.6), (14.4, 14.4)), ((7.6, 14.4), (14.4, 7.6))):
            m.moveToPoint_(NSMakePoint(x1 * k, y1 * k))
            m.lineToPoint_(NSMakePoint(x2 * k, y2 * k))
    m.stroke()
    rep_ = NSBitmapImageRep.alloc().initWithFocusedViewRect_(NSMakeRect(0, 0, 22 * k, 22 * k))
    img.unlockFocus()
    path.parent.mkdir(parents=True, exist_ok=True)
    rep_.representationUsingType_properties_(NSPNGFileType, None).writeToFile_atomically_(str(path), True)
    return str(path)


def _open_terminal(cwd: str) -> None:
    for app in ("iTerm", "Terminal"):
        try:
            subprocess.run(["open", "-a", app, cwd], check=True, capture_output=True)
            return
        except Exception:
            continue


def main() -> None:
    try:
        import rumps
    except ImportError:
        raise SystemExit("the menu-bar app needs rumps: pip install 'neopulse[mac]'")

    class NeoPulseApp(rumps.App):
        def __init__(self):
            super().__init__("NeoPulse", title=None, quit_button=None)
            self._icon_path = None
            self.daemon = Daemon(log=self._log)
            self.daemon.start()
            self.daemon.listeners.append(self._on_event)
            self.notify_waiting = monitor.load_config().get("notify", True)
            self._notified: set[str] = set()
            self._log_lines: list[str] = []
            self.sessions_items: list[rumps.MenuItem] = []
            self._bright_want: int | None = None     # latest slider value, sent by a worker thread
            self._bright_evt = threading.Event()
            self._bright_touched = 0.0
            self._build_menu()
            self._tick()
            # device/serial work happens on a thread; the UI refreshes on a timer
            threading.Thread(target=self._loop, daemon=True).start()
            threading.Thread(target=self._bright_loop, daemon=True).start()
            rumps.Timer(self._tick, 2).start()

        # ---------- background ----------
        def _loop(self):
            while True:
                try:
                    self.daemon.step(read_events_for=0.3)   # short, so UI commands get the port quickly
                except Exception as e:
                    self._log(f"loop error: {e}")
                    time.sleep(1)

        def _bright_loop(self):
            """Send only the newest slider value; a drag produces many, serial I/O is slow."""
            while True:
                self._bright_evt.wait()
                self._bright_evt.clear()
                v, self._bright_want = self._bright_want, None
                if v is not None:
                    try:
                        self.daemon.command(f"bright {v}")
                    except Exception as e:
                        self._log(f"bright error: {e}")

        def _log(self, s: str):
            self._log_lines.append(s)
            del self._log_lines[:-200]
            try:
                (monitor.state_dir() / "monitor.log").open("a").write(s + "\n")
            except OSError:
                pass

        def _on_event(self, ev: str):
            pass  # menu is refreshed by the timer; hook for future per-event UI

        # ---------- menu ----------
        def _build_menu(self):
            self.header = rumps.MenuItem("NeoPulse")   # rumps keys items by this initial title
            self.header_key = "NeoPulse"
            self.header.set_callback(None)
            self.sessions_sep = rumps.separator
            self.ack_item = rumps.MenuItem("Acknowledge (clear done / needs you)", callback=self.on_ack)
            self.mode_menu = rumps.MenuItem("Mode")
            self.mode_items = {}
            for m in MODE_CYCLE:
                it = rumps.MenuItem(MODE_LABEL[m], callback=lambda sender, m=m: self.on_mode(m))
                self.mode_items[m] = it
                self.mode_menu.add(it)
            # one custom-view item: a title in normal menu text (a callback-less MenuItem would be greyed
            # out and read as disabled) with the slider under it
            from AppKit import NSColor, NSFont, NSMakeRect, NSTextField, NSView, NSViewWidthSizable
            self.bright_slider = rumps.SliderMenuItem(value=60, min_value=1, max_value=100,
                                                      callback=self.on_bright_slide, dimensions=(200, 20))
            box = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 240, 46))
            self.bright_text = NSTextField.labelWithString_("Brightness")
            self.bright_text.setFont_(NSFont.menuFontOfSize_(0))
            self.bright_text.setTextColor_(NSColor.labelColor())
            self.bright_text.setFrame_(NSMakeRect(28, 25, 192, 18))   # x matches the menu's text column
            self.bright_slider._menuitem.setView_(None)   # detach first, or setView_(box) pulls the slider out of box
            self.bright_slider._slider.setFrame_(NSMakeRect(28, 3, 192, 20))
            for v in (box, self.bright_text, self.bright_slider._slider):
                v.setAutoresizingMask_(NSViewWidthSizable)   # stretch to the menu's width
            box.addSubview_(self.bright_text)
            box.addSubview_(self.bright_slider._slider)
            self.bright_slider._menuitem.setView_(box)
            self.profile_menu = rumps.MenuItem("Colors")
            self.profile_items = {}
            for name in list_profiles():
                it = rumps.MenuItem(name, callback=lambda sender, n=name: self.on_profile(n))
                self.profile_items[name] = it
                self.profile_menu.add(it)
            self.mute_item = rumps.MenuItem("Pause light", callback=self.on_mute)
            self.notify_item = rumps.MenuItem("Notify when an agent needs me", callback=self.on_notify)
            self.notify_item.state = 1 if self.notify_waiting else 0
            self.identify_item = rumps.MenuItem("Find my device (flash white)", callback=lambda _: self.daemon.command("identify"))
            self.device_item = rumps.MenuItem("device: looking...")
            self.device_item.set_callback(None)
            self.menu = [self.header, rumps.separator, self.ack_item, self.mode_menu, self.profile_menu,
                         rumps.separator, self.bright_slider, rumps.separator, self.mute_item, self.notify_item, rumps.separator,
                         self.identify_item, self.device_item, rumps.separator,
                         rumps.MenuItem(f"Quit NeoPulse {__version__}", callback=self.on_quit)]

        def _tick(self, _timer=None):
            try:
                self._refresh_menu()
            except Exception as e:
                self._log(f"menu error: {e}")

        def _refresh_menu(self):
            from AppKit import NSColor
            info = self.daemon.device_info()
            leds = int(info.get("leds", 4))
            sessions = monitor.current_sessions(lanes=leds)
            cfg = monitor.load_config()
            agg = min((s.state for s in sessions.values()), key=lambda st: list(State).index(st), default=None)
            active = sum(1 for s in sessions.values() if s.state != State.IDLE)
            connected = self.daemon.connected()
            off = cfg.get("mode") == "off"            # light deliberately dark
            icon = _icon_file(agg, connected, off)     # tick inside the disc = device reachable, cross = not
            if icon != self._icon_path:
                self._icon_path = icon
                self.icon = icon
            self.title = f"{active}" if (active > 1 and not off) else None
            self.header.title = f"NeoPulse: {len(sessions)} session{'s' if len(sessions) != 1 else ''}" if sessions else "NeoPulse: no agent sessions"
            # session rows
            for it in self.sessions_items:
                try:
                    del self.menu[it.title]
                except KeyError:
                    pass
            self.sessions_items = []
            now = time.time()
            for s in sorted(sessions.values(), key=lambda s: (s.lane is None, s.lane or 0)):
                px = f"pixel {s.lane}" if (s.lane is not None and cfg.get("mode") == "lane") else ""
                title = f"{ICON[s.state]} {LABEL[s.state]:9} {s.provider:6} {s.label}  {px}  ({int(s.age(now))}s)"
                while title in self.menu:          # keys must be unique
                    title += " "
                it = rumps.MenuItem(title)
                if s.cwd:
                    it.add(rumps.MenuItem("Open in Terminal", callback=lambda _, c=s.cwd: _open_terminal(c)))
                    it.add(rumps.MenuItem("Reveal folder in Finder", callback=lambda _, c=s.cwd: subprocess.run(["open", c])))
                self.menu.insert_after(self.header_key if not self.sessions_items else self.sessions_items[-1].title, it)
                self.sessions_items.append(it)
            for m, it in self.mode_items.items():
                it.state = 1 if cfg.get("mode") == m else 0
            for n, it in self.profile_items.items():
                it.state = 1 if cfg.get("profile") == n else 0
            self.mute_item.state = 1 if int(info.get("mute", 0)) else 0
            if connected and info:
                age = int(now - info.get("t", now))
                self.device_item.title = f"device: connected, fw {info.get('fw', '?')}, {leds} pixels  ({age}s ago)"
            else:
                self.device_item.title = "device: not connected (check the USB plug)"
            self.bright_slider._slider.setEnabled_(bool(connected))
            if now - self._bright_touched > 3 and "bright" in info:   # follow pad holds / CLI changes
                self.bright_slider.value = info["bright"]
            self.bright_text.setStringValue_(f"Brightness: {int(round(self.bright_slider.value))}%" if connected
                                             else "Brightness (device not connected)")
            self.bright_text.setTextColor_(NSColor.labelColor() if connected else NSColor.disabledControlTextColor())
            # notifications for sessions that newly need you
            if self.notify_waiting:
                for key, s in sessions.items():
                    if s.state == State.WAITING and key not in self._notified:
                        self._notified.add(key)
                        try:
                            rumps.notification("NeoPulse", f"{s.provider} needs you", s.label or key, sound=False)
                        except Exception as e:  # no CFBundleIdentifier in this interpreter, or denied
                            self._log(f"notification unavailable: {e}")
                    elif s.state != State.WAITING:
                        self._notified.discard(key)

        # ---------- callbacks ----------
        def on_ack(self, _):
            self.daemon.ack()
            self._tick()

        def on_mode(self, mode):
            self.daemon.set_mode(mode, indicate=False)
            self._tick()

        def on_bright_slide(self, sender):
            v = max(1, min(100, int(round(sender.value))))
            self._bright_touched = time.time()
            self.bright_text.setStringValue_(f"Brightness: {v}%")
            self._bright_want = v
            self._bright_evt.set()

        def on_profile(self, name):
            cfg = monitor.load_config()
            cfg["profile"] = name
            monitor.save_config(cfg)
            self.daemon.refresh(force=True)
            self._tick()

        def on_mute(self, sender):
            new = 0 if sender.state else 1
            self.daemon.command(f"mute {new}")
            sender.state = new

        def on_notify(self, sender):
            sender.state = 0 if sender.state else 1
            self.notify_waiting = bool(sender.state)
            cfg = monitor.load_config()
            cfg["notify"] = self.notify_waiting
            monitor.save_config(cfg)

        def on_quit(self, _):
            self.daemon.stop()
            rumps.quit_application()

    NeoPulseApp().run()


if __name__ == "__main__":
    main()

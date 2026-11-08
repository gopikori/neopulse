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
MODE_LABEL = {"unified": "One light (most urgent state)", "lane": "One pixel per agent", "off": "Off"}


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
            super().__init__("NeoPulse", title="⚪", quit_button=None)
            self.daemon = Daemon(log=self._log)
            self.daemon.start()
            self.daemon.listeners.append(self._on_event)
            self.notify_waiting = monitor.load_config().get("notify", True)
            self._notified: set[str] = set()
            self._log_lines: list[str] = []
            self.sessions_items: list[rumps.MenuItem] = []
            self._build_menu()
            self._tick()
            # device/serial work happens on a thread; the UI refreshes on a timer
            threading.Thread(target=self._loop, daemon=True).start()
            rumps.Timer(self._tick, 2).start()

        # ---------- background ----------
        def _loop(self):
            while True:
                try:
                    self.daemon.step(read_events_for=1.0)
                except Exception as e:
                    self._log(f"loop error: {e}")
                    time.sleep(1)

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
            self.mode_menu = rumps.MenuItem("Layout")
            self.mode_items = {}
            for m in MODE_CYCLE:
                it = rumps.MenuItem(MODE_LABEL[m], callback=lambda sender, m=m: self.on_mode(m))
                self.mode_items[m] = it
                self.mode_menu.add(it)
            self.bright_menu = rumps.MenuItem("Brightness")
            self.bright_menu.add(rumps.MenuItem("Brighter  (pad 2 hold)", callback=lambda _: self.on_bright("+10")))
            self.bright_menu.add(rumps.MenuItem("Dimmer  (pad 1 hold)", callback=lambda _: self.on_bright("-10")))
            self.bright_menu.add(rumps.separator)
            for v in (10, 30, 60, 100):
                self.bright_menu.add(rumps.MenuItem(f"{v}%", callback=lambda _, v=v: self.on_bright(str(v))))
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
            self.menu = [self.header, rumps.separator, self.ack_item, self.mode_menu, self.bright_menu,
                         self.profile_menu, self.mute_item, self.notify_item, rumps.separator,
                         self.identify_item, self.device_item, rumps.separator,
                         rumps.MenuItem(f"Quit NeoPulse {__version__}", callback=self.on_quit)]

        def _tick(self, _timer=None):
            try:
                self._refresh_menu()
            except Exception as e:
                self._log(f"menu error: {e}")

        def _refresh_menu(self):
            info = self.daemon.device_info()
            leds = int(info.get("leds", 4))
            sessions = monitor.current_sessions(lanes=leds)
            cfg = monitor.load_config()
            agg = min((s.state for s in sessions.values()), key=lambda st: list(State).index(st), default=None)
            active = sum(1 for s in sessions.values() if s.state != State.IDLE)
            self.title = (ICON[agg] if agg else "⚪") + (f" {active}" if active > 1 else "")
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
            if info:
                age = int(now - info.get("t", now))
                self.device_item.title = f"device: fw {info.get('fw', '?')}, {leds} pixels, brightness {info.get('bright', '?')}%  ({age}s ago)"
            else:
                self.device_item.title = "device: not connected"
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

        def on_bright(self, arg):
            self.daemon.command(f"bright {arg}")
            self._tick()

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

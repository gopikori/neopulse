"""neopulse command line. Run `neopulse -h`."""
from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import sys
import time
from pathlib import Path

from . import __version__, monitor
from . import program as P
from .device import find_mount, find_serial, read_status, soft_reset
from .paths import firmware_dir, state_dir
from .states import State, list_profiles, load_profile
from .transport import TransportError, open_transport

UF2_VERSION = "10.3.1"
UF2_URL = ("https://downloads.circuitpython.org/bin/neopixel_trinkey_m0/en_US/"
           f"adafruit-circuitpython-neopixel_trinkey_m0-en_US-{UF2_VERSION}.uf2")


def die(msg: str, code: int = 1):
    print(f"neopulse: {msg}", file=sys.stderr)
    sys.exit(code)


def transport_from(args):
    cfg = monitor.load_config()
    prefer = getattr(args, "transport", None) or cfg.get("transport", "auto")
    try:
        return open_transport(prefer, getattr(args, "device", None) or cfg.get("device"),
                              getattr(args, "port", None) or cfg.get("port"))
    except TransportError as e:
        die(str(e), 2)


# ---------------- device commands ----------------
def cmd_write(args):
    text = sys.stdin.read() if args.program == "-" else args.program
    text = text.replace("\\n", "\n")
    err = P.validate(text)
    if err:
        die(f"invalid program: {err}")
    with transport_from(args) as tr:
        tr.write(text)
        if args.init and hasattr(tr, "write_init"):
            tr.write_init(text)
    print(f"ok ({tr.name})")


def cmd_off(args):
    with transport_from(args) as tr:
        tr.off()
    print("off")


def cmd_info(args):
    with transport_from(args) as tr:
        info = tr.info()
        if not info:
            die("device did not report info")
        print(f"transport {tr.name}  {tr!r}")
        for k, v in info.extra.items():
            print(f"{k:8} {v}")


def last_device_info() -> dict:
    """Device info as last seen by the daemon (it holds the serial port)."""
    try:
        return json.loads((state_dir() / "device.json").read_text())
    except (OSError, ValueError):
        return {}


def cmd_bright(args):
    level = (args.level or "").lower()
    if not level:
        info = last_device_info()
        if "bright" in info:
            print(f"brightness {info['bright']} (1-100, as reported {int(time.time() - info.get('t', 0))}s ago)")
        elif monitor.daemon_running():
            print("brightness unknown yet (daemon just started; retry in a few seconds)")
        else:
            try:
                with open_transport("serial", None, args.port) as tr:
                    i = tr.info()
                    print(f"brightness {i.extra.get('bright', '?')} (1-100)")
            except TransportError:
                print("brightness unknown (no daemon running and the serial port did not answer)")
        print("set with: neopulse bright 1-100 | up | down | +10 | -10   (or hold pad 1 / pad 2)")
        return
    if level == "up":
        arg = "+10"
    elif level == "down":
        arg = "-10"
    elif level.lstrip("+-").isdigit():
        arg = level if level[0] in "+-" else str(max(1, min(100, int(level))))
    else:
        die(f"bad level {args.level!r}: use 1-100, up, down, +N or -N")
    with transport_from(args) as tr:
        reply = tr.command(f"bright {arg}")
        print(f"brightness {arg} -> {reply}" if tr.name == "file" else reply)


def cmd_identify(args):
    with transport_from(args) as tr:
        print(tr.command("identify"))


def cmd_reset(args):
    """Restart the firmware (Ctrl-C/Ctrl-D on the console port)."""
    if monitor.daemon_running():
        # the daemon holds the data port, so we cannot ping; just send the keystrokes
        ok = soft_reset(verify=False)
        print("restart sent; the daemon reconnects within a few seconds" if ok else "could not reach the console port; unplug and replug the board")
        return
    print("firmware restarted" if soft_reset() else "could not restart; unplug and replug the board")


def cmd_eject(args):
    """Unmount the drive so the board can be unplugged without the OS warning."""
    from .device import eject
    ok, msg = monitor._locked(eject)
    print(msg)
    if not ok:
        sys.exit(1)


def cmd_events(args):
    try:
        tr = open_transport("serial", None, args.port)
    except TransportError as e:
        die(str(e))
    print(f"listening on {tr.port} (Ctrl-C to stop)")
    try:
        for ev in tr.events():
            print(time.strftime("%H:%M:%S"), ev)
    except KeyboardInterrupt:
        pass
    finally:
        tr.close()


def cmd_demo(args):
    prof = load_profile(monitor.load_config().get("profile", "default"))
    from .states import Lane, aggregate, render
    with transport_from(args) as tr:
        info = tr.info()
        leds = info.leds if info else 4
        W, Wt, Bk, D = State.WORKING, State.WAITING, State.BLOCKED, State.DONE
        scenes = [
            ("1 Claude session working: pixel 0 breathes cyan", [Lane(W, "claude"), None, None, None]),
            ("a Codex session starts: pixel 1 breathes violet", [Lane(W, "claude"), Lane(W, "codex"), None, None]),
            ("Codex needs you: pixel 1 double-pulses amber", [Lane(W, "claude"), Lane(Wt, "codex"), None, None]),
            ("Claude done, Codex blocked: green + red blink", [Lane(D, "claude"), Lane(Bk, "codex"), None, None]),
            ("four sessions, one per pixel", [Lane(W, "claude"), Lane(D, "codex"), Lane(Wt, "claude"), Lane(W, "codex")]),
        ]
        for label, slots in scenes:
            print(label)
            tr.write(render("lane", None, slots[:leds], leds, prof))
            time.sleep(args.seconds)
        for label, st in (("unified: working chase", W), ("unified: needs you", Wt), ("unified: done", D)):
            print(label)
            tr.write(render("unified", st, [], leds, prof, provider="claude"))
            time.sleep(args.seconds)
        tr.off()
    print("demo done")


# ---------------- state / monitor ----------------
def cmd_state(args):
    monitor.manual(State(args.state), args.session, args.provider)
    prog = monitor.refresh(force=True)
    print(f"{args.state} -> {'written' if prog else 'no device'}")


def cmd_ack(args):
    monitor.ack()
    monitor.refresh(force=True)
    print("acknowledged")


def cmd_clear(args):
    monitor.clear()
    monitor.refresh(force=True)
    print("cleared")


def cmd_status(args):
    m = find_mount()
    running = monitor.daemon_running()
    port = None if (args.no_serial or running) else find_serial()
    info = read_status(m) if m else None
    print(f"drive    {m or '-'}")
    print(f"serial   {port or ('in use by daemon' if running else '-')}")
    if info:
        print(f"device   {info.board} fw={info.fw} leds={info.leds} touch={info.touch}")
    cfg = monitor.load_config()
    print(f"mode     {cfg['mode']}  profile={cfg['profile']}  presence={cfg['presence']}")
    pid = state_dir() / "daemon.pid"
    print(f"daemon   {'running pid ' + pid.read_text().strip() if running else 'not running'}")
    from .hooks import PROVIDERS, get
    for p in PROVIDERS:
        try:
            s = get(p).status()
            print(f"hooks    {p:7} {'installed' if s['installed'] else 'not installed'} ({len(s['events'])} events)")
        except Exception as e:
            print(f"hooks    {p:7} error: {e}")
    leds = info.leds if info else 4
    sessions = monitor.current_sessions(lanes=leds)
    print(f"sessions {len(sessions)}   (one pixel per session; pixel 0 top-left, 1 top-right, 2 bottom-right, 3 bottom-left)")
    now = time.time()
    for s in sorted(sessions.values(), key=lambda s: (s.lane is None, s.lane or 0)):
        px = f"pixel {s.lane}" if s.lane is not None else "no pixel"
        print(f"  {px:9} {s.state.value:8} {s.provider:7} {s.label:24} {int(s.age(now))}s ago")


def cmd_mode(args):
    cfg = monitor.load_config()
    if args.mode:
        cfg["mode"] = args.mode
        monitor.save_config(cfg)
        monitor.refresh(force=True)
    print(cfg["mode"])


def cmd_presence(args):
    cfg = monitor.load_config()
    cfg["presence"] = args.value == "on"
    monitor.save_config(cfg)
    monitor.refresh(force=True)
    print(f"presence {'on' if cfg['presence'] else 'off'}")


def cmd_profile(args):
    cfg = monitor.load_config()
    if args.action == "list":
        for name, path in list_profiles().items():
            mark = "*" if name == cfg.get("profile") else " "
            print(f"{mark} {name:10} {load_profile(name).description}")
    elif args.action == "show":
        print(Path(list_profiles()[args.name or cfg["profile"]]).read_text())
    elif args.action == "use":
        load_profile(args.name)
        cfg["profile"] = args.name
        monitor.save_config(cfg)
        monitor.refresh(force=True)
        print(f"profile {args.name}")


def cmd_hooks(args):
    from .hooks import PROVIDERS, get
    providers = PROVIDERS if args.provider in (None, "all") else (args.provider,)
    for p in providers:
        mod = get(p)
        if args.action == "install":
            print(mod.install(dry_run=args.dry_run))
        elif args.action == "uninstall":
            print(mod.uninstall())
        else:
            s = mod.status()
            print(f"{p:7} {'installed' if s['installed'] else 'not installed'}  {s['path']}  {' '.join(s['events'])}")


def cmd_event(args):
    """Internal: used by hooks (same as hooks/entry.py)."""
    from .hooks import entry
    sys.argv = ["entry", args.provider]
    sys.exit(entry.main())


def cmd_monitor(args):
    if args.action == "refresh":
        prog = monitor.refresh(force=True)
        print(prog if prog else "no device")
    elif args.action == "run":
        run_daemon(args)
    elif args.action == "install-service":
        print(install_service())
    elif args.action == "uninstall-service":
        print(uninstall_service())


def run_daemon(args):
    """Foreground loop: refresh on a timer (expiries) and react to touch events."""
    from .daemon import Daemon
    Daemon(log=lambda m: print(m, flush=True)).run_forever()


def cmd_menubar(args):
    from . import menubar
    menubar.main()


def menubar_available() -> bool:
    try:
        import rumps  # noqa: F401
        return sys.platform == "darwin"
    except ImportError:
        return False


def _launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "io.neopulse.monitor.plist"


def _systemd_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / "neopulse.service"


def install_service() -> str:
    exe = shutil.which("neopulse") or f"{sys.executable} -m neopulse.cli"
    log = state_dir() / "monitor.log"
    if sys.platform == "darwin":
        import plistlib
        p = _launch_agent_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        sub = ["menubar"] if menubar_available() else ["monitor", "run"]
        argv = ([exe] if shutil.which("neopulse") else [sys.executable, "-m", "neopulse.cli"]) + sub
        with open(p, "wb") as f:
            plistlib.dump({"Label": "io.neopulse.monitor", "ProgramArguments": argv, "RunAtLoad": True,
                           "KeepAlive": {"SuccessfulExit": False},   # restart after a crash, not after Quit
                           "StandardOutPath": str(log), "StandardErrorPath": str(log)}, f)
        rc = os.system(f"launchctl unload '{p}' >/dev/null 2>&1; launchctl load '{p}'")
        what = "menu-bar app" if sub == ["menubar"] else "headless monitor (pip install 'neopulse[mac]' for the menu-bar app)"
        return f"installed and started LaunchAgent {p} running the {what} (log: {log})" + ("" if rc == 0 else " -- launchctl load failed")
    if sys.platform.startswith("linux"):
        p = _systemd_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"""[Unit]
Description=NeoPulse agent status light monitor
[Service]
ExecStart={exe} monitor run
Restart=always
RestartSec=3
[Install]
WantedBy=default.target
""")
        os.system("systemctl --user daemon-reload && systemctl --user enable --now neopulse.service")
        return f"installed and started systemd user service {p}"
    return "service install is supported on macOS and Linux; run `neopulse monitor run` manually"


def uninstall_service() -> str:
    if sys.platform == "darwin":
        p = _launch_agent_path()
        os.system(f"launchctl unload '{p}' >/dev/null 2>&1")
        p.unlink(missing_ok=True)
        return "LaunchAgent removed"
    if sys.platform.startswith("linux"):
        os.system("systemctl --user disable --now neopulse.service >/dev/null 2>&1")
        _systemd_path().unlink(missing_ok=True)
        return "systemd user service removed"
    return "nothing to remove"


# ---------------- setup / firmware ----------------
def cmd_setup(args):
    print("1/3 firmware")
    m = find_mount()
    if m is None:
        print("   no NEOPULSE/CIRCUITPY drive; run `neopulse flash` with the board plugged in")
    else:
        info = read_status(m)
        if info and info.extra.get("neopulse") == "1":
            print(f"   found {m} fw={info.fw}")
        else:
            print(f"   {m} has CircuitPython but no NeoPulse firmware; deploying")
            print("   " + deploy_firmware(m))
    print("2/3 hooks")
    from .hooks import PROVIDERS, get
    for p in PROVIDERS:
        try:
            print("   " + get(p).install())
        except Exception as e:
            print(f"   {p}: skipped ({e})")
    print("3/3 background monitor")
    if args.no_service:
        print("   skipped (--no-service); run `neopulse monitor run` when you want touch + timeouts")
    else:
        print("   " + install_service())
    monitor.refresh(force=True)
    print("done. Try: neopulse state waiting   |   neopulse status   |   neopulse demo")


def deploy_firmware(mount: Path) -> str:
    fw = firmware_dir()
    if fw is None:
        return "firmware sources not found (set NEOPULSE_FIRMWARE_DIR or run from the repo)"
    mpy = fw / "dist" / "neopulse_fw.mpy"
    if not mpy.exists():
        return f"missing {mpy}; run tools/deploy.sh once to build it"
    (mount / "lib").mkdir(exist_ok=True)
    (mount / "boards").mkdir(exist_ok=True)
    shutil.copy(mpy, mount / "lib" / "neopulse_fw.mpy")
    shutil.copy(fw / "code.py", mount / "code.py")
    shutil.copy(fw / "boot.py", mount / "boot.py")
    if not (mount / "boards" / "profile.py").exists():
        shutil.copy(fw / "boards" / "profile.py", mount / "boards" / "profile.py")
    os.sync() if hasattr(os, "sync") else None
    return f"firmware copied to {mount}; unplug and replug the board (boot.py renames the drive to NEOPULSE)"


def cmd_firmware(args):
    m = find_mount(args.device)
    if m is None:
        die("no CIRCUITPY/NEOPULSE drive mounted")
    print(deploy_firmware(m))


def cmd_flash(args):
    """Put the board in its UF2 bootloader, copy CircuitPython, then deploy the firmware."""
    import glob
    import urllib.request
    uf2 = Path(args.uf2) if args.uf2 else None
    if uf2 is None:
        fw = firmware_dir()
        cache = (fw / "dist" if fw else state_dir()) / UF2_URL.rsplit("/", 1)[1]
        if not cache.exists():
            print(f"downloading CircuitPython {UF2_VERSION} ...")
            cache.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(UF2_URL, cache)
        uf2 = cache
    boot = Path("/Volumes/TRINKEYBOOT")
    if not boot.exists():
        ports = sorted(glob.glob("/dev/cu.usbmodem*")) if sys.platform == "darwin" else sorted(glob.glob("/dev/ttyACM*"))
        if ports:
            print(f"asking {ports[0]} to reboot into the bootloader (1200 baud touch)")
            try:
                import serial
                s = serial.Serial(ports[0], 1200)
                s.dtr = False
                s.close()
            except Exception as e:
                print(f"  could not: {e}")
        else:
            print("no serial port found; double-tap the reset button on the board now")
        for _ in range(40):
            if boot.exists():
                break
            time.sleep(0.5)
    if not boot.exists():
        die("TRINKEYBOOT drive did not appear. Double-tap the tiny reset button and rerun.")
    print(f"copying {uf2.name} to {boot}")
    shutil.copy(uf2, boot / uf2.name)
    for _ in range(60):
        m = find_mount()
        if m:
            break
        time.sleep(0.5)
    else:
        die("CIRCUITPY did not come back")
    time.sleep(1)
    print(deploy_firmware(m))


# ---------------- parser ----------------
OVERVIEW = """neopulse -- USB status light for coding agents

Everyday
  neopulse status                 what the device, hooks, daemon and sessions are doing
  neopulse bright [60|up|down]    show or set brightness (or hold pad 1 / pad 2)
  neopulse ack                    clear done/waiting lights (or tap pad 1)
  neopulse mode unified|lane|off  one light, one pixel per agent, or dark (pad 2 cycles)
  neopulse demo                   cycle through every state

Drive it yourself
  neopulse state waiting --session ci     feed a state from scripts, CI, cron
  neopulse write '#30d158 400ms ease'     raw LED program (see spec/PROTOCOL.md)
  neopulse off | identify | events | info

Setup
  neopulse setup                  flash check + hooks + background daemon / menu-bar app
  neopulse menubar                macOS menu-bar app (also runs the daemon)
  neopulse hooks install|status   Claude Code / Codex hooks
  neopulse flash                  put CircuitPython + firmware on a Neo Trinkey
  neopulse reset                  restart a stopped firmware (solid white pixels)
  neopulse eject                  unmount the drive before you unplug the board

neopulse help <command> for details, e.g. neopulse help bright
"""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        if "invalid choice" in message and self.prog == "neopulse":
            bad = message.split("'")[1]
            close = difflib.get_close_matches(bad, COMMANDS, n=3, cutoff=0.5)
            hint = f"  did you mean: {', '.join(close)}?" if close else ""
            sys.stderr.write(f"neopulse: unknown command {bad!r}.{hint}\n\n{OVERVIEW}")
            sys.exit(2)
        super().error(message)


COMMANDS: list[str] = []
F = argparse.RawDescriptionHelpFormatter


def build_parser() -> argparse.ArgumentParser:
    ap = Parser(prog="neopulse", description=OVERVIEW, formatter_class=F)
    ap.add_argument("--version", action="version", version=f"neopulse {__version__}")
    sub = ap.add_subparsers(dest="cmd", metavar="<command>")

    def add(name, help, desc=None, examples=None):
        epilog = ("examples:\n" + "\n".join("  " + e for e in examples)) if examples else None
        p = sub.add_parser(name, help=help, description=desc or help, epilog=epilog, formatter_class=F)
        COMMANDS.append(name)
        return p

    def dev_opts(p):
        g = p.add_argument_group("device selection (normally automatic)")
        g.add_argument("--transport", choices=["auto", "file", "serial", "null"],
                       help="file = write to the NEOPULSE drive, serial = data port, null = print only")
        g.add_argument("--device", metavar="PATH", help="mount path of the drive, e.g. /Volumes/NEOPULSE")
        g.add_argument("--port", metavar="PORT", help="serial port of the data channel")

    p = add("help", "show help for a command", examples=["neopulse help", "neopulse help bright"])
    p.add_argument("topic", nargs="?", metavar="<command>")
    p.set_defaults(fn=lambda a: (build_parser().parse_args([a.topic, "-h"]) if a.topic else print(OVERVIEW)))

    p = add("status", "device, hooks, daemon and live sessions"); p.add_argument("--no-serial", action="store_true", help="skip the serial probe")
    p.set_defaults(fn=cmd_status)
    p = add("bright", "show or set brightness",
            "Brightness is stored on the device (1-100; use mute or mode off for dark). Without an argument, shows the current level.\n"
            "Holding touch pad 1 lowers it and pad 2 raises it.",
            ["neopulse bright", "neopulse bright 60", "neopulse bright up", "neopulse bright -20"])
    p.add_argument("level", nargs="?", metavar="1-100|up|down|+N|-N"); dev_opts(p); p.set_defaults(fn=cmd_bright)
    p = add("ack", "clear done/waiting lights (same as tapping pad 1)"); p.set_defaults(fn=cmd_ack)
    p = add("clear", "forget every session and turn the light off"); p.set_defaults(fn=cmd_clear)
    p = add("mode", "show or set the mode",
            "unified (default): all pixels show the most urgent state.  lane: one pixel per agent session.\n"
            "off: dark. Tapping pad 2 cycles these.", ["neopulse mode", "neopulse mode unified"])
    p.add_argument("mode", nargs="?", choices=["lane", "unified", "off"]); p.set_defaults(fn=cmd_mode)
    p = add("presence", "dim glow when nothing is happening", examples=["neopulse presence on"])
    p.add_argument("value", choices=["on", "off"]); p.set_defaults(fn=cmd_presence)
    p = add("profile", "list or switch color profiles",
            "Built-in: default, ember, mono. Drop your own JSON into ~/.config/neopulse/profiles/.",
            ["neopulse profile list", "neopulse profile use ember", "neopulse profile show default"])
    p.add_argument("action", choices=["list", "show", "use"]); p.add_argument("name", nargs="?"); p.set_defaults(fn=cmd_profile)
    p = add("demo", "cycle through every state on the device", examples=["neopulse demo", "neopulse demo --seconds 2"])
    dev_opts(p); p.add_argument("--seconds", type=float, default=4, metavar="N", help="seconds per state (default 4)")
    p.set_defaults(fn=cmd_demo)

    p = add("state", "set an agent state by hand (for scripts, CI, cron)",
            "Feeds the same state machine the agent hooks use. Each --session owns one pixel.",
            ["neopulse state working --session build", "neopulse state done --session build",
             "neopulse state waiting --session deploy --provider codex"])
    p.add_argument("state", choices=[s.value for s in State]); p.add_argument("--session", default="manual", metavar="NAME")
    p.add_argument("--provider", default="manual", metavar="NAME", help="claude, codex or any label (sets the tint)")
    p.set_defaults(fn=cmd_state)
    p = add("write", "send a raw LED program",
            "A program is one step per line; pass it as text (use \\n between lines) or '-' for stdin.\n"
            "Language reference: spec/PROTOCOL.md",
            ["neopulse write '#30d158 400ms ease'", "neopulse write 'off\\n#ff3a00 1.6s pulse\\nrepeat'",
             "printf 'off\\n#ffb000 700ms pulse\\nrepeat\\n' | neopulse write -"])
    dev_opts(p); p.add_argument("program", metavar="PROGRAM|-")
    p.add_argument("--init", action="store_true", help="also save as the power-on program (INIT.LED)")
    p.set_defaults(fn=cmd_write)
    p = add("off", "turn the light off"); dev_opts(p); p.set_defaults(fn=cmd_off)
    p = add("identify", "flash white three times to find the device"); dev_opts(p); p.set_defaults(fn=cmd_identify)
    p = add("events", "stream touch and device events (stop the daemon first, it holds the port)")
    p.add_argument("--port", metavar="PORT"); p.set_defaults(fn=cmd_events)
    p = add("info", "device descriptor (firmware, pixel count, free memory)"); dev_opts(p); p.set_defaults(fn=cmd_info)

    p = add("setup", "firmware check + hooks + background daemon, in one go")
    p.add_argument("--no-service", action="store_true", help="do not install the launchd/systemd daemon"); p.set_defaults(fn=cmd_setup)
    p = add("hooks", "install, remove or inspect agent hooks",
            examples=["neopulse hooks status", "neopulse hooks install claude", "neopulse hooks uninstall all"])
    p.add_argument("action", choices=["install", "uninstall", "status"])
    p.add_argument("provider", nargs="?", choices=["claude", "codex", "all"], help="default: all")
    p.add_argument("--dry-run", action="store_true", help="print what would be written"); p.set_defaults(fn=cmd_hooks)
    p = add("menubar", "macOS menu-bar app: sessions, mode, brightness, notifications (needs rumps)")
    p.set_defaults(fn=cmd_menubar)
    p = add("monitor", "the background daemon (touch pads, timeouts, device recovery)",
            examples=["neopulse monitor run", "neopulse monitor install-service", "neopulse monitor refresh"])
    p.add_argument("action", choices=["run", "refresh", "install-service", "uninstall-service"]); p.set_defaults(fn=cmd_monitor)
    p = add("reset", "restart the firmware if it stopped (all pixels solid white)"); p.set_defaults(fn=cmd_reset)
    p = add("eject", "unmount the drive so the board can be unplugged without a warning"); p.set_defaults(fn=cmd_eject)
    p = add("flash", "install CircuitPython + NeoPulse firmware on a Neo Trinkey",
            "Reboots the board into its UF2 bootloader (or asks you to double-tap reset), copies CircuitPython, then the firmware.")
    p.add_argument("--uf2", metavar="FILE", help="use this .uf2 instead of downloading"); p.set_defaults(fn=cmd_flash)
    p = add("firmware", "copy the firmware files to a mounted CIRCUITPY/NEOPULSE drive")
    p.add_argument("--device", metavar="PATH"); p.set_defaults(fn=cmd_firmware)
    p = add("event", argparse.SUPPRESS); p.add_argument("provider"); p.set_defaults(fn=cmd_event)
    return ap


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(OVERVIEW)
        return
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()

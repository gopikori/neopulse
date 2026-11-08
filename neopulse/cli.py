"""neopulse command line. Run `neopulse -h`."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path

from . import __version__, monitor
from . import program as P
from .device import find_mount, find_serial, read_status
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


def cmd_bright(args):
    with transport_from(args) as tr:
        if tr.name != "serial":
            try:
                tr.close()
                tr = open_transport("serial", None, args.port)
            except TransportError:
                die("brightness needs the serial channel; is the device plugged in?")
        print(tr.command(f"bright {args.level}"))


def cmd_identify(args):
    try:
        with open_transport("serial", None, args.port) as tr:
            print(tr.command("identify"))
    except TransportError as e:
        die(str(e))


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
    with transport_from(args) as tr:
        info = tr.info()
        leds = info.leds if info else 4
        from .states import Lane, render
        seq = [
            ("working (lane)", [Lane(State.WORKING, "claude")]),
            ("two agents: claude working + codex waiting", [Lane(State.WAITING, "codex"), Lane(State.WORKING, "claude")]),
            ("blocked", [Lane(State.BLOCKED, "claude")]),
            ("done", [Lane(State.DONE, "claude")]),
        ]
        for label, lanes in seq:
            from .states import aggregate
            print(label)
            tr.write(render("lane", aggregate([l.state for l in lanes]), lanes, leds, prof))
            time.sleep(args.seconds)
        for label, st in (("unified working", State.WORKING), ("unified waiting", State.WAITING), ("unified done", State.DONE)):
            print(label)
            tr.write(render("unified", st, [Lane(st, "claude")], leds, prof))
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
    port = None if args.no_serial else find_serial()
    info = read_status(m) if m else None
    print(f"drive    {m or '-'}")
    print(f"serial   {port or '-'}")
    if info:
        print(f"device   {info.board} fw={info.fw} leds={info.leds} touch={info.touch}")
    cfg = monitor.load_config()
    print(f"mode     {cfg['mode']}  profile={cfg['profile']}  presence={cfg['presence']}")
    pid = state_dir() / "daemon.pid"
    print(f"daemon   {'running pid ' + pid.read_text().strip() if pid.exists() else 'not running'}")
    from .hooks import PROVIDERS, get
    for p in PROVIDERS:
        try:
            s = get(p).status()
            print(f"hooks    {p:7} {'installed' if s['installed'] else 'not installed'} ({len(s['events'])} events)")
        except Exception as e:
            print(f"hooks    {p:7} error: {e}")
    sessions = monitor.current_sessions()
    print(f"sessions {len(sessions)}")
    now = time.time()
    for s in sorted(sessions.values(), key=lambda s: s.updated, reverse=True):
        print(f"  {s.state.value:8} {s.provider:7} {s.label:24} {int(s.age(now))}s ago")


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
    pid = state_dir() / "daemon.pid"
    pid.write_text(str(os.getpid()))
    stop = False

    def _sig(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    print(f"neopulse monitor running (pid {os.getpid()})", flush=True)
    ser = None
    last = 0.0
    mode_cycle = ["lane", "unified", "off"]
    try:
        while not stop:
            if ser is None:
                port = find_serial()
                if port:
                    try:
                        from .transport.serial import SerialTransport
                        ser = SerialTransport(port)
                        ser.command("local 0")  # daemon owns the tap gestures
                        print(f"serial {port} connected", flush=True)
                    except TransportError:
                        ser = None
            if time.time() - last > 2:
                last = time.time()
                try:
                    monitor.refresh(transport=None)
                except Exception as e:
                    print(f"refresh error: {e}", flush=True)
            if ser is None:
                time.sleep(1)
                continue
            try:
                for ev in ser.events(idle_timeout=1.0):
                    print(time.strftime("%H:%M:%S"), ev, flush=True)
                    if ev == "evt touch1 tap":
                        monitor.ack()
                        monitor.refresh(force=True)
                    elif ev == "evt touch2 tap":
                        cfg = monitor.load_config()
                        cfg["mode"] = mode_cycle[(mode_cycle.index(cfg.get("mode", "lane")) + 1) % len(mode_cycle)]
                        monitor.save_config(cfg)
                        monitor.refresh(force=True)
                        print(f"mode -> {cfg['mode']}", flush=True)
            except Exception as e:
                print(f"serial lost: {e}", flush=True)
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
    finally:
        if ser is not None:
            try:
                ser.command("local 1")
                ser.close()
            except Exception:
                pass
        pid.unlink(missing_ok=True)


def _launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "io.neopulse.monitor.plist"


def _systemd_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / "neopulse.service"


def install_service() -> str:
    exe = shutil.which("neopulse") or f"{sys.executable} -m neopulse.cli"
    log = state_dir() / "monitor.log"
    if sys.platform == "darwin":
        p = _launch_agent_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        args_xml = "".join(f"<string>{a}</string>" for a in exe.split() + ["monitor", "run"])
        p.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>io.neopulse.monitor</string>
  <key>ProgramArguments</key><array>{args_xml}</array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{log}</string><key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
""")
        os.system(f"launchctl unload '{p}' >/dev/null 2>&1; launchctl load '{p}'")
        return f"installed and started LaunchAgent {p} (log: {log})"
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
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="neopulse", description="USB status light for coding agents")
    ap.add_argument("--version", action="version", version=f"neopulse {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def dev_opts(p):
        p.add_argument("--transport", choices=["auto", "file", "serial", "null"])
        p.add_argument("--device", help="mount path of the drive")
        p.add_argument("--port", help="serial port of the data channel")

    p = sub.add_parser("write", help="send an LED program (text, or - for stdin)"); dev_opts(p)
    p.add_argument("program"); p.add_argument("--init", action="store_true", help="also save as power-on program")
    p.set_defaults(fn=cmd_write)
    p = sub.add_parser("off", help="turn the light off"); dev_opts(p); p.set_defaults(fn=cmd_off)
    p = sub.add_parser("info", help="device descriptor"); dev_opts(p); p.set_defaults(fn=cmd_info)
    p = sub.add_parser("bright", help="set brightness 0-100 (persisted on device)"); p.add_argument("level", type=int)
    p.add_argument("--port"); p.set_defaults(fn=cmd_bright)
    p = sub.add_parser("identify", help="flash white to find the device"); p.add_argument("--port"); p.set_defaults(fn=cmd_identify)
    p = sub.add_parser("events", help="stream touch/device events"); p.add_argument("--port"); p.set_defaults(fn=cmd_events)
    p = sub.add_parser("demo", help="cycle through the states"); dev_opts(p)
    p.add_argument("--seconds", type=float, default=4); p.set_defaults(fn=cmd_demo)

    p = sub.add_parser("state", help="set a state by hand (from scripts, CI, cron)")
    p.add_argument("state", choices=[s.value for s in State]); p.add_argument("--session", default="manual")
    p.add_argument("--provider", default="manual"); p.set_defaults(fn=cmd_state)
    sub.add_parser("ack", help="clear done/waiting lights").set_defaults(fn=cmd_ack)
    sub.add_parser("clear", help="forget all sessions").set_defaults(fn=cmd_clear)
    p = sub.add_parser("status", help="device, hooks, sessions"); p.add_argument("--no-serial", action="store_true")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("mode", help="lane | unified | off"); p.add_argument("mode", nargs="?", choices=["lane", "unified", "off"])
    p.set_defaults(fn=cmd_mode)
    p = sub.add_parser("presence", help="dim glow when idle"); p.add_argument("value", choices=["on", "off"]); p.set_defaults(fn=cmd_presence)
    p = sub.add_parser("profile", help="color profiles"); p.add_argument("action", choices=["list", "show", "use"])
    p.add_argument("name", nargs="?"); p.set_defaults(fn=cmd_profile)

    p = sub.add_parser("hooks", help="install agent hooks"); p.add_argument("action", choices=["install", "uninstall", "status"])
    p.add_argument("provider", nargs="?", choices=["claude", "codex", "all"]); p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_hooks)
    p = sub.add_parser("event", help=argparse.SUPPRESS); p.add_argument("provider"); p.set_defaults(fn=cmd_event)
    p = sub.add_parser("monitor", help="background monitor"); p.add_argument("action", choices=["run", "refresh", "install-service", "uninstall-service"])
    p.set_defaults(fn=cmd_monitor)

    p = sub.add_parser("setup", help="firmware check + hooks + service, in one go"); p.add_argument("--no-service", action="store_true")
    p.set_defaults(fn=cmd_setup)
    p = sub.add_parser("flash", help="install CircuitPython + NeoPulse firmware on a Trinkey"); p.add_argument("--uf2")
    p.set_defaults(fn=cmd_flash)
    p = sub.add_parser("firmware", help="copy firmware files to a mounted drive"); p.add_argument("--device"); p.set_defaults(fn=cmd_firmware)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()

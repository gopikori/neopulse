# NeoPulse

An open-source USB status light for AI coding agents, built on the $7 **Adafruit Neo Trinkey**
(4 NeoPixels, 2 touch pads) or any CircuitPython board with NeoPixels. Inspired by
[SidePulse](https://sidepulse.io) and compatible with its `LEDS.LED` program language.

Know whether Claude Code or Codex is **working**, **needs you**, is **blocked**, or **done**
without looking at the terminal. Four pixels show up to three agents at once.

```
pixel 0   aggregate (highest-priority state across all sessions)
pixel 1-3 one lane per agent session, most urgent first
```

| State | Lane pattern | Unified pattern | Default color |
|---|---|---|---|
| working / tool running | slow breathe | chase | cyan (Claude) / violet (Codex) |
| waiting for you | fast double pulse | fast pulse | amber |
| blocked / error | double blink | double blink | red |
| done | solid, fades after 20 min | solid | green |
| idle | off (or dim "presence" glow) | off | – |

## Install

```bash
git clone https://github.com/<you>/neopulse && cd neopulse
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
neopulse flash          # one time: puts CircuitPython + NeoPulse firmware on the Trinkey
neopulse setup          # installs Claude Code + Codex hooks and the background monitor
neopulse demo           # see every state
```

`neopulse flash` reboots the board into its UF2 bootloader (or asks you to double-tap the tiny
reset button), copies CircuitPython 10.3.1, then copies the firmware. Afterwards the board mounts as
`NEOPULSE`.

## Use it from anything

The device is a USB drive. Any script that can write a text file can drive the light:

```bash
echo "#30d158 400ms ease" > /Volumes/NEOPULSE/LEDS.LED              # solid green
printf 'off\n#ff3a00 1.6s pulse\nrepeat\n' > /Volumes/NEOPULSE/LEDS.LED   # pulse
```

Or with the CLI (file or serial transport, auto-detected):

```bash
neopulse write 'off\n#ffb000 700ms pulse\nrepeat'
neopulse state waiting --session ci     # feed the agent state machine from CI / cron / scripts
neopulse state done --session ci
neopulse ack                            # same as tapping pad 1
neopulse mode unified                   # or: lane | off   (pad 2 cycles these)
neopulse profile use ember              # color profiles; add your own JSON in ~/.config/neopulse/profiles
neopulse bright 40                      # or hold pad 1 / pad 2
neopulse events                         # watch touch events live
neopulse status
```

Full language and serial protocol: [spec/PROTOCOL.md](spec/PROTOCOL.md).

## How agents connect

`neopulse hooks install claude` adds command hooks to `~/.claude/settings.json`
(SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PostToolUseFailure, Notification, Stop,
SessionEnd). `neopulse hooks install codex` adds `[[hooks.*]]` tables to `~/.codex/config.toml`
and pre-trusts them. Both merge with what you already have and keep a backup.

Each hook runs `hooks/entry.py`, which appends one JSON line to
`~/.local/state/neopulse/events.jsonl` and refreshes the device. It costs 30 to 90 ms, prints
nothing (hook stdout would leak into the model's context), and always exits 0.

The optional daemon (`neopulse monitor run`, or `monitor install-service` for launchd / systemd)
handles what hooks cannot: fading "done" after 20 minutes, dropping stale sessions, and the touch
pads (ack, layout cycling). Without it everything still works except those three.

## Architecture

```
firmware/          CircuitPython: boot.py, code.py, neopulse_fw.py (engine), boards/profile.py
spec/PROTOCOL.md   the contract between device and host
neopulse/
  program.py       parse / validate / build LED programs (pure Python, mirrors the firmware)
  device.py        find the NEOPULSE drive and the data serial port
  transport/       FileTransport, SerialTransport, NullTransport
  states.py        State, priority, Profile (JSON), lane + unified renderers
  monitor.py       event log → sessions → program → device; ack / clear / manual states
  hooks/           claude.py, codex.py installers; entry.py hook process
  cli.py           the `neopulse` command
tools/deploy.sh    mpy-cross build + copy to the board
```

Every layer is importable on its own: use `neopulse.program` + `FileTransport` for a plain blinky,
or `neopulse.states` to render your own state machine.

## Other hardware

Any CircuitPython board with NeoPixels: copy `firmware/boards/profile_generic_strip.py` to
`boards/profile.py` on the drive, set the pin and pixel count, done. The host adapts to `leds=N`.
Boards with more RAM can skip the `.mpy` step and run `neopulse_fw.py` as `code.py`.

## Troubleshooting

- **All four pixels solid white**: CircuitPython is idle, the firmware is not running. Run
  `neopulse reset` (the daemon also does this automatically) or replug the board.
- **Nothing lights up after a hook fires**: `neopulse status` shows the sessions it knows about and
  whether the daemon is running; `~/.local/state/neopulse/monitor.log` has the daemon's view;
  `neopulse info` reports `bright=` (hold pad 2 or run `neopulse bright 60` if it is low).
- **Hooks only load when an agent session starts**, so restart Claude Code / Codex after
  `neopulse hooks install`.

## Development

```bash
pip install -e ".[dev]"
pytest
tools/deploy.sh            # rebuild lib/neopulse_fw.mpy (needs tools/mpy-cross 10.3.1) and copy to the board
```

Firmware debugging: the first USB serial port is the CircuitPython console; Ctrl-C shows where the
loop is, Ctrl-D reloads. Note this SAMD21 build lacks `str.encode`, `bytes.decode`, `str.partition`
and `json`.

MIT license.

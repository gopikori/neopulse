# SidePulse research (2026-11-07)

Product: SidePulse Dot ($29, 2 RGB LEDs, USB-C) and SidePulse Pro ($69, 8 LEDs, MacBook Pro SD slot).
Maker: Peter Kuhar / InteliWEAR LLC. CLI is MIT, Python 3.10+: https://github.com/inteliwear/sidepulse

## Transport (key design idea)
- Device enumerates as a tiny USB mass-storage drive (`/Volumes/SidePulseDot`, `/media/$USER/SidePulseDot`).
- Host writes plain text to `LEDS.LED` -> LEDs change. `INIT.LED` = boot program. `STATUS.TXT` = readable status/version.
  `FIRMWARE.BIN` copied to root = firmware update.
- Fallback "SPC2" direct USB control when the drive is read-only (MDM). Protocol undocumented.
- No account, cloud, or daemon needed for basic control: "any script that can write a text file can control the lights".

## LEDS.LED mini-language (LEDS_FORMAT.md)
- One step per line, max 512 bytes / 20 lines. Comments `;` `//` `#`.
- Colors `#rrggbb` or `off`; positional (`#f00 #0f0`) or indexed (`0:#fff 1:#f0e`); `;` separates segments.
- Duration `330ms` / `0.33s` (default 330ms), easings: linear, ease, ease-in, ease-out, ease-in-out, cosine, pulse, none.
- `brightness N`, `roll` / `roll-left` / `roll-right <dur>`, `repeat` / `repeat N`.
- Parse error -> all LEDs blink red 6x.
- Example: `off\n#ff3a00 1.6s pulse\nrepeat`

## CLI command surface
- write "<program>" [--device PATH|--to NAME|--all]  (stdin via `-`)
- push "<program>" --title --message             (prefers linked phone; hooks/remote notify)
- setup [provider] [--no-status-bar]              (installs hooks, menu-bar LaunchAgent/systemd)
- agent-monitor doctor|install [codex|claude|grok|cursor|junie]|uninstall|status|live|leds [--once --dry-run]
- status-bar start|stop ; service run|status ; settings
- link [token|channel] / unlink ID               (phone QR pairing, VM->Mac relay WIP)
- firmware version|upgrade [--version --file --dry-run]
- battery status|leds|configure
- update [--dry-run]
- Python API: `from sidepulse import AgentMonitor, LiveAgentMonitor, send_hook_event`

## Agent states -> LED
- Working / tool running: cyan roll
- Waiting for input / blocked / error: slow amber pulse
- Completed: solid green (held 20 min)
- Idle: very dim pulse
- Multi-session priority: Blocked > Waiting > ToolRunning > LongTask > Working > Completed > Idle
- Agents can emit hidden markers `<!-- sidepulse:ask|done|working|blocked|idle -->` in final message.
- Profiles: Cyan (default), Ember, Purple; JSON import/export.
- Hooks: Claude `~/.claude/settings.json`, Codex `~/.codex/config.toml`, Grok, Junie, Cursor. OpenCode via community plugin.
- Paths: settings `~/.config/sidepulse/agent-monitor/settings.json`, logs `~/.local/state/sidepulse/agent-monitor/*.jsonl`

## Use cases seen in the wild
1. Coding-agent status (Claude Code / Codex / Cursor / OpenCode): working / needs-you / done — the dominant use.
2. Lid-closed or remote-agent work: glance at the laptop/Mac mini across the room.
3. Phone notification via `push` from CI, build servers, cron, long jobs (iOS/Android app + Shortcuts).
4. Battery level bar (Pro).
5. Build pass/fail signals from scripts.
6. Novelty: audio level meter, pomodoro, "ask the agent to display whatever you want".

## Ecosystem / comparable DIY projects
- khromov/sidepulse-swift (Swift CLI + menu bar), bambidotexe/my-sidepulse (menu bar, per-session), inteliwear/sidepulse-bridge (curl-only shell CLI), inteliwear/sidepulse-ios.
- artemy/semaphore (USB-HID traffic light, Claude/Codex/OpenCode hooks), ClauBlink (ESP32/RP2040 + NeoPixel over serial), glowbug (serial daemon), capslight (Caps Lock LED).
- Hook hygiene lesson: hook scripts must fail silently when device absent and suppress stdout so nothing leaks into model context.

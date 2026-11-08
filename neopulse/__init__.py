"""NeoPulse: drive a NeoPixel USB status light from a tiny text program.

Layers (each usable on its own):
  program    -- parse / validate / build LED programs (pure Python)
  device     -- find the mounted drive and the data serial port
  transport  -- FileTransport, SerialTransport, NullTransport
  states     -- agent states, priority, color profiles, rendering to a program
  monitor    -- per-session agent tracking fed by editor/CLI hooks
  hooks      -- installers for Claude Code and Codex
"""
__version__ = "0.2.0"

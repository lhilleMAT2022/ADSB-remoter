# ADS-B Console Respin

Clean Python respin of the BaseStation ADS-B stream parser, playback tool, tracker, retransmitter, and Textual TUI.

## Development Environment

This project targets Python 3.13 or newer and uses Astral uv.

```powershell
uv python install 3.13
uv sync --group dev
```

The runtime dependency set is intentionally small. Textual provides the TUI; BaseStation parsing, playback, TCP/UDP I/O, tracking, and coordinate transforms should remain independently testable without the TUI layer.


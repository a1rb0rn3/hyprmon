# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running and testing

```bash
# Test current config without starting the daemon
hyprmon --once --verbose

# Run as daemon with debug output
hyprmon --verbose

# Override config path
hyprmon -c /path/to/config.toml --once

# Force re-apply even if profile is unchanged (lid switch binding)
hyprmon --once --force
```

No test suite exists — validation is done by running `--once --verbose` against a live Hyprland session.

## Architecture

Single-file Python daemon (`hyprmon.py`). The entire runtime is one `run()` function with an inner `evaluate()` closure that holds state via `nonlocal`.

**Event flow:**
1. On startup: load TOML config → evaluate current state once → connect to `.socket2.sock`
2. On `monitoradded`/`monitorremoved` IPC events: sleep `event_delay` → call `evaluate()`
3. `evaluate()`: query `hyprctl -j monitors` → match against profiles in order → if profile or lid state changed, call `apply_profile()`
4. `apply_profile()`: sync systemd user env vars, run `exec[]`, then run `exec_lid_open[]` or `exec_lid_closed[]` based on lid state

**Profile matching** (`profile_matches`): every glob pattern in `match` must match at least one connected monitor description string. Empty `match` always matches (fallback profile). First match wins.

**Lid detection**: reads a raw text file (e.g. `/proc/acpi/button/lid/LID/state`) and checks if `open_string` (default: `"open"`) appears in the content. If no lid config, lid-aware exec hooks still run — `exec_lid_open` is used as the default.

**Env tracking**: `current_env` dict persists across evaluations. `apply_env()` diffs against previous env to only set/unset what changed, using `systemctl --user set-environment` / `unset-environment`.

**Socket reconnect**: the main loop wraps the socket in a `while True` with a 3-second backoff on `ConnectionResetError`/`OSError`, so the daemon survives compositor restarts.

## Config format

TOML file at `~/.config/hyprmon/config.toml`. Top-level keys:
- `event_delay` — float seconds to wait after monitor event (default 0.5)
- `[lid]` — optional lid detection: `state_file`, `open_string`
- `[[profile]]` — ordered list of profiles

Each profile: `name`, `match[]` (glob patterns), `exec[]`, `exec_lid_open[]`, `exec_lid_closed[]`, optional `[profile.env]` table.

Monitor descriptions come from `hyprctl monitors | grep description` — use `*` suffix to match the connector Hyprland appends (e.g. `"Dell Inc. Model Serial*"`).

## Dependencies

- Python 3.11+ (stdlib `tomllib`) or Python 3.9+ with `pip install tomli`
- `hyprctl` in `$PATH`
- Hyprland with `HYPRLAND_INSTANCE_SIGNATURE` env var set (only available inside a Hyprland session)

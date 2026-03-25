# hyprmon

A lightweight Hyprland daemon that automatically applies monitor configuration profiles when displays are connected or disconnected.

## Why not kanshi or shikane?

Tools like [kanshi](https://sr.ht/~emersion/kanshi/) and [shikane](https://github.com/hw0lff/shikane) manage monitors via the `wlr-output-management` Wayland protocol. Hyprland has a known bug ([#1274](https://github.com/hyprwm/Hyprland/issues/1274)) where **disabled monitor heads are silently removed from the compositor's output list**. Once an output disappears from that list, `wlr-output-management`-based tools can never re-enable it — the monitor is invisible to them until the next physical reconnect.

This makes it impossible to reliably switch between a docked profile (laptop screen off) and a mobile profile (laptop screen on) using kanshi or shikane on Hyprland.

`hyprmon` avoids this entirely by **never disabling monitors**. Instead, it uses `hyprctl keyword monitor` — Hyprland's own native IPC — to reposition and reconfigure displays. This bypasses `wlr-output-management` and keeps all heads visible to the compositor at all times.

## How it works

1. On startup, evaluates the current monitor state and applies the matching profile.
2. Connects to Hyprland's IPC event socket (`.socket2.sock`).
3. On every `monitoradded` / `monitorremoved` event, re-evaluates and applies the correct profile.
4. Profile matching uses glob patterns against monitor description strings (which include make, model, and serial — stable across reboots and connector changes).

## No kanshi or shikane needed

`hyprmon` is a complete replacement. `hyprctl keyword monitor` covers everything — resolution, refresh rate, position, rotation, scale, transform — so there is no need to run kanshi or shikane alongside it. On Hyprland, both tools are unreliable anyway due to the wlr-output-management bug described above.

## Requirements

- Python 3.11+ (uses stdlib `tomllib`) — or Python 3.9+ with `pip install tomli`
- Hyprland (any recent version)
- `hyprctl` in `$PATH`

## Installation

```bash
git clone https://github.com/a1rb0rn3/hyprmon.git
cd hyprmon
bash install.sh
```

This installs:
- `~/.local/bin/hyprmon` — the daemon
- `~/.config/hyprmon/config.toml` — your config (copied from `config.example.toml`)
- `~/.config/systemd/user/hyprmon.service` — systemd user service

## Configuration

Edit `~/.config/hyprmon/config.toml`. Profiles are evaluated in order — the first match wins.

```toml
event_delay = 0.5  # seconds to wait after a monitor event before re-evaluating

[[profile]]
name = "docked"
match = [
    "Make Model SERIAL1*",  # left display
    "Make Model SERIAL2*",  # right display
]
exec = [
    "hyprctl keyword monitor desc:Make Model SERIAL1,2560x1440@60,0x0,1",
    "hyprctl keyword monitor desc:Make Model SERIAL2,2560x1440@60,2560x0,1",
]
exec_lid_open  = ["hyprctl keyword monitor eDP-1,preferred,5120x0,2"]
exec_lid_closed = ['hyprctl keyword monitor "eDP-1,disabled"']

[[profile]]
name = "mobile"
match = []  # empty match = fallback, always matches last
exec_lid_open  = ["hyprctl keyword monitor eDP-1,preferred,0x0,2"]
exec_lid_closed = ['hyprctl keyword monitor "eDP-1,disabled"']
```

### Finding your monitor descriptions

```bash
hyprctl monitors | grep description
```

Use `*` as a wildcard suffix to match the connector name that Hyprland appends (e.g. `"Dell Inc. DELL U2722D XXXXXXX*"` matches `"Dell Inc. DELL U2722D XXXXXXX (DP-1)"`).

### Profile matching rules

| Scenario | Result |
|---|---|
| All patterns in `match` have a connected monitor | Profile matches |
| `match` is empty | Always matches (use as fallback) |
| First matching profile in list | Applied; rest are skipped |
| Profile unchanged from last event | Not re-applied (no duplicate commands) |

### Noctalia lock screen monitor

If you use [noctalia shell](https://github.com/noctalia-dev/noctalia-shell), add `lockscreen_monitors` to a profile to pin the lock screen to specific connectors when that profile is active:

```toml
[[profile]]
name = "docked"
lockscreen_monitors = ["DP-3"]  # show lock screen only on this connector

[[profile]]
name = "mobile"
lockscreen_monitors = []  # empty = all monitors (noctalia default)
```

hyprmon writes the value to `~/.config/noctalia/settings.json` (`general.lockScreenMonitors`) on every profile switch. Omit the key entirely to leave the setting untouched.

Use `hyprctl monitors | awk '/^Monitor/{print $2}'` to find the connector names for your current setup.

### Per-profile environment variables

Add an `[profile.env]` table to set systemd user environment variables when a profile activates. Variables are applied via `systemctl --user set-environment` and automatically unset when switching to a profile that does not define them.

Useful for XWayland scaling tied to a specific monitor layout:

```toml
[[profile]]
name = "mobile"
match = []
exec_lid_open = ["hyprctl keyword monitor eDP-1,3200x2000@120,0x0,2"]

[profile.env]
QT_SCALE_FACTOR = "1.6"
```

## Autostart

Enable the systemd user service:

```bash
systemctl --user enable --now hyprmon.service
```

The service starts automatically with your Hyprland session and restarts on failure.

## Usage

```
hyprmon [-h] [-c CONFIG] [--once] [--force] [-v]

  -c, --config PATH   Path to TOML config (default: ~/.config/hyprmon/config.toml)
  --once              Apply current profile once and exit (useful for testing)
  --force             Re-apply even if profile and lid state are unchanged
  -v, --verbose       Enable debug logging
```

### Testing your config

```bash
# Apply current profile and exit — no daemon
hyprmon --once --verbose

# Run as daemon with debug output
hyprmon --verbose
```

## Lid-aware profiles

Profiles support `exec_lid_open` and `exec_lid_closed` in addition to `exec`. Configure the lid state file under `[lid]`:

```toml
[lid]
state_file = "/proc/acpi/button/lid/LID/state"

[[profile]]
name = "office"
match = ["Some Monitor*"]
exec = ["hyprctl keyword monitor desc:Some Monitor,..."]
exec_lid_open  = ["hyprctl keyword monitor eDP-1,3200x2000@120,0x2560,2"]
exec_lid_closed = ['hyprctl keyword monitor "eDP-1,disabled"']
```

The profile is re-applied automatically when the lid state changes, even without a monitor connect/disconnect event. Use `--force` to trigger a re-apply from a Hyprland lid switch binding:

```ini
# hyprland.conf
bindl = , switch:on:Lid Switch,  exec, hyprmon --once --force
bindl = , switch:off:Lid Switch, exec, hyprmon --once --force
```

For suspend-on-lid-close when undocked, configure `systemd-logind`:

```ini
# /etc/systemd/logind.conf.d/lid.conf
[Login]
HandleLidSwitch=suspend
HandleLidSwitchDocked=ignore
```

## License

MIT

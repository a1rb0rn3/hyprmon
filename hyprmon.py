#!/usr/bin/env python3
"""
hyprmon — Hyprland monitor event daemon

Listens to Hyprland IPC events and applies monitor configuration profiles
based on which displays are currently connected. Uses hyprctl directly,
bypassing wlr-output-management to avoid the Hyprland bug where disabled
monitor heads are silently removed from the compositor's output list.

Supports lid-aware profiles: exec_lid_open / exec_lid_closed are run in
addition to exec when the lid state is known, enabling proper handling of
the internal laptop display without manual bindings.

Supports per-profile environment variables via the [env] table: variables
are applied with `systemctl --user set-environment` on profile activation
and unset with `unset-environment` when switching away from a profile that
had them. This allows XWayland scaling variables (e.g. QT_SCALE_FACTOR)
to be tied to a specific monitor layout.
"""

import argparse
import fnmatch
import json
import logging
import os
import socket
import subprocess
import sys
import time

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        sys.exit("Error: Python 3.11+ required, or install tomli: pip install tomli")

DEFAULT_CONFIG = os.path.expanduser("~/.config/hyprmon/config.toml")
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
LOG_DATE = "%H:%M:%S"


# ---------------------------------------------------------------------------
# Hyprland IPC
# ---------------------------------------------------------------------------

def get_socket_path() -> str:
    his = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not his:
        sys.exit("HYPRLAND_INSTANCE_SIGNATURE not set — is Hyprland running?")
    xdg = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return f"{xdg}/hypr/{his}/.socket2.sock"


def get_connected_monitors() -> list[str]:
    """Return description strings for all currently active (non-disabled) monitors."""
    result = subprocess.run(
        ["hyprctl", "-j", "monitors"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logging.warning("hyprctl monitors failed: %s", result.stderr.strip())
        return []
    try:
        return [m.get("description", "") for m in json.loads(result.stdout)]
    except json.JSONDecodeError:
        logging.warning("Failed to parse hyprctl output")
        return []


# ---------------------------------------------------------------------------
# Lid state
# ---------------------------------------------------------------------------

def get_lid_state(lid_config: dict) -> str | None:
    """
    Returns 'open' or 'closed', or None if lid detection is not configured
    or the state file cannot be read.
    """
    state_file = lid_config.get("state_file")
    if not state_file:
        return None
    try:
        content = open(state_file).read()
        open_string = lid_config.get("open_string", "open")
        return "open" if open_string in content else "closed"
    except OSError as exc:
        logging.warning("Could not read lid state file %s: %s", state_file, exc)
        return None


# ---------------------------------------------------------------------------
# Profile matching
# ---------------------------------------------------------------------------

def profile_matches(patterns: list[str], connected: list[str]) -> bool:
    """
    A profile matches when every pattern in its match list corresponds to
    at least one currently connected monitor. An empty match list always
    matches (use as a fallback profile at the end of the list).
    """
    return all(
        any(fnmatch.fnmatch(desc, pattern) for desc in connected)
        for pattern in patterns
    )


def find_profile(profiles: list[dict], connected: list[str]) -> dict | None:
    """Return the first profile whose match list is satisfied."""
    for profile in profiles:
        if profile_matches(profile.get("match", []), connected):
            return profile
    return None


# ---------------------------------------------------------------------------
# Profile application
# ---------------------------------------------------------------------------

def run_cmd(cmd: str) -> None:
    logging.debug("  $ %s", cmd)
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        logging.warning("Command failed: %s\n%s", cmd, result.stderr.strip())


def apply_env(new_env: dict[str, str], prev_env: dict[str, str]) -> None:
    """
    Sync the systemd user environment from prev_env to new_env.

    Variables present in new_env are set; variables that were in prev_env
    but are absent from new_env are unset so they don't leak into the next
    profile.
    """
    to_set = {k: v for k, v in new_env.items() if prev_env.get(k) != v}
    to_unset = [k for k in prev_env if k not in new_env]

    if to_set:
        args = [f"{k}={v}" for k, v in to_set.items()]
        logging.info("  env set: %s", " ".join(args))
        result = subprocess.run(
            ["systemctl", "--user", "set-environment"] + args,
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            logging.warning("set-environment failed: %s", result.stderr.strip())

    if to_unset:
        logging.info("  env unset: %s", " ".join(to_unset))
        result = subprocess.run(
            ["systemctl", "--user", "unset-environment"] + to_unset,
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            logging.warning("unset-environment failed: %s", result.stderr.strip())


NOCTALIA_SETTINGS = os.path.expanduser("~/.config/noctalia/settings.json")


def apply_lockscreen_monitors(monitors: list[str]) -> None:
    """Write lockscreen_monitors into the noctalia settings.json."""
    try:
        with open(NOCTALIA_SETTINGS) as f:
            data = json.load(f)
        data.setdefault("general", {})["lockScreenMonitors"] = monitors
        with open(NOCTALIA_SETTINGS, "w") as f:
            json.dump(data, f, indent=2)
        logging.info("  lockscreen monitors: %s", monitors or "(all)")
    except OSError as exc:
        logging.warning("Could not update noctalia settings: %s", exc)
    except (json.JSONDecodeError, KeyError) as exc:
        logging.warning("Could not parse noctalia settings: %s", exc)


def apply_profile(profile: dict, lid_state: str | None, prev_env: dict[str, str]) -> dict[str, str]:
    """Apply a profile and return its env dict (for tracking in the main loop)."""
    name = profile.get("name", "unnamed")
    logging.info("Applying profile '%s' (lid: %s)", name, lid_state or "unknown")

    if "lockscreen_monitors" in profile:
        apply_lockscreen_monitors(profile["lockscreen_monitors"])

    new_env: dict[str, str] = profile.get("env", {})
    apply_env(new_env, prev_env)

    for cmd in profile.get("exec", []):
        run_cmd(cmd)

    if lid_state == "open":
        for cmd in profile.get("exec_lid_open", []):
            run_cmd(cmd)
    elif lid_state == "closed":
        for cmd in profile.get("exec_lid_closed", []):
            run_cmd(cmd)
    else:
        # No lid config — treat like open (run exec_lid_open as default)
        for cmd in profile.get("exec_lid_open", []):
            run_cmd(cmd)

    return new_env


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict:
    if not os.path.exists(path):
        sys.exit(
            f"Config not found: {path}\n"
            f"Copy config.example.toml to {path} and edit it."
        )
    with open(path, "rb") as f:
        return tomllib.load(f)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run(config_path: str, once: bool = False, force: bool = False) -> None:
    config = load_config(config_path)
    profiles: list[dict] = config.get("profile", [])
    lid_config: dict = config.get("lid", {})
    event_delay: float = config.get("event_delay", 0.5)

    if not profiles:
        sys.exit("No [[profile]] entries defined in config.")

    current_profile_name: str | None = None
    current_lid_state: str | None = None
    current_env: dict[str, str] = {}

    def evaluate(force_apply: bool = False) -> None:
        nonlocal current_profile_name, current_lid_state, current_env
        connected = get_connected_monitors()
        lid_state = get_lid_state(lid_config)
        logging.debug("Connected: %s | Lid: %s", connected, lid_state)

        profile = find_profile(profiles, connected)
        if not profile:
            logging.info("No profile matched (connected: %s)", connected)
            return

        name = profile.get("name")
        profile_changed = name != current_profile_name
        lid_changed = lid_state != current_lid_state

        if profile_changed or lid_changed or force_apply:
            current_env = apply_profile(profile, lid_state, current_env)
            current_profile_name = name
            current_lid_state = lid_state

    evaluate(force_apply=force)

    if once:
        return

    socket_path = get_socket_path()
    logging.info("Listening on %s", socket_path)

    while True:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.connect(socket_path)
                buf = ""
                while True:
                    data = sock.recv(4096).decode("utf-8", errors="replace")
                    if not data:
                        raise ConnectionResetError("Socket closed by compositor")
                    buf += data
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        line = line.strip()
                        if line.startswith(("monitoradded>>", "monitorremoved>>")):
                            event, monitor_name = line.split(">>", 1)
                            logging.info("Event: %s (%s)", event, monitor_name)
                            time.sleep(event_delay)
                            evaluate()
        except (ConnectionResetError, OSError) as exc:
            logging.warning("Socket error: %s — reconnecting in 3s", exc)
            time.sleep(3)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Hyprland monitor event daemon — applies hyprctl monitor profiles "
            "on display connect/disconnect events."
        )
    )
    parser.add_argument(
        "-c", "--config",
        default=DEFAULT_CONFIG,
        help=f"Path to TOML config file (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Evaluate and apply the current profile once, then exit.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-apply even if profile and lid state are unchanged (useful for lid switch bindings).",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format=LOG_FORMAT,
        datefmt=LOG_DATE,
    )

    run(args.config, once=args.once, force=args.force)


if __name__ == "__main__":
    main()

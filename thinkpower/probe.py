"""Read the system. Only things policy.toml does NOT own.

Anything thinkpower declares is read from policy.toml, not guessed from the
machine -- an earlier version reverse-engineered the idle ladder out of
hypridle's config file and broke the moment thinkpower started generating
that file itself.

FAIL FAST. Nothing here catches exceptions. If a tool is missing or a sysfs
path moved, the traceback is the bug report.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
from pathlib import Path


def sh(*cmd: str) -> str:
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=5, check=True).stdout


def read(path: str) -> str:
    return open(path, encoding="utf-8").read().strip()


def first(pattern: str) -> str:
    return read(sorted(glob.glob(pattern))[0])


# Only the lid has a power-source variant; key handlers are single-valued,
# which is a logind limit rather than a configuration choice.
LOGIND = ("HandlePowerKey", "HandlePowerKeyLongPress", "HandleLidSwitch",
          "HandleLidSwitchExternalPower", "HandleLidSwitchDocked",
          "IdleAction", "IdleActionUSec", "InhibitDelayMaxUSec")


def logind() -> dict:
    """Ask the daemon, not the files: it merges drop-ins over the main config
    and compiles in defaults, so only it knows the effective value."""
    out = sh("busctl", "get-property", "org.freedesktop.login1",
             "/org/freedesktop/login1", "org.freedesktop.login1.Manager", *LOGIND)
    result = {}
    for prop, line in zip(LOGIND, out.strip().splitlines()):
        kind, _, raw = line.strip().partition(" ")
        result[prop] = raw.strip().strip('"') if kind == "s" else int(raw) // 1_000_000
    return result


def pkg_version(binary: str) -> str | None:
    """Version from the resolved /nix/store path.

    Not `hyprctl version`: that needs HYPRLAND_INSTANCE_SIGNATURE and so only
    works from inside the session, while this works anywhere.
    """
    path = os.path.realpath(f"/run/current-system/sw/bin/{binary}")
    m = re.search(r"-([0-9][^/-]*)/bin/", path)
    return m.group(1) if m else None


def on_ac() -> bool:
    """Single cheap read, shared with act's condition_cmd path, which runs on
    every listener timeout and must not pay for a full state() probe."""
    return bool(int(first("/sys/class/power_supply/A*/online")))


def unit_active(unit: str, user: bool = False) -> bool | None:
    """True/False only for a real answer; None if the bus was unreachable."""
    cmd = ["systemctl"] + (["--user"] if user else []) + ["is-active", unit]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
    return {"active": True, "inactive": False, "failed": False}.get(out)


SERVICES = [("hypridle", "hypridle", True), ("tlp", "tlp", False),
            ("upower", "upower", False), ("acpid", "acpid", False),
            ("thermald", "thermald", False),
            ("hibernate-on-low-battery.timer", None, False)]


def state() -> dict:
    """Everything the UI shows that policy.toml does not declare."""
    bat = sorted(glob.glob("/sys/class/power_supply/BAT*"))[0]
    panel = sorted(glob.glob("/sys/class/backlight/*"))[0]
    uwh = lambda n: int(read(f"{bat}/{n}")) / 1e6
    pick = lambda s: next(w.strip("[]") for w in s.split() if w.startswith("["))
    mem = read("/sys/power/mem_sleep")

    # tlp-stat prints the source file per setting, so defaults.conf vs
    # tlp.conf says what was actually chosen here. TLP's naming also makes
    # power-source-dependent settings self-identifying: every X_ON_AC has an
    # X_ON_BAT sibling.
    tlp_rows = re.findall(r'^(\S+)\s+L\d+:\s*(\w+)="([^"]*)"', sh("tlp-stat", "-c"), re.M)
    tlp_conf = {k: v for _, k, v in tlp_rows}

    radios = {}
    for line in sh("rfkill", "-rn", "-o", "TYPE,SOFT,HARD").strip().splitlines():
        kind, soft, hard = line.split()
        radios[kind] = "blocked" if "blocked" in (soft, hard) else "on"

    return {
        "on_ac": on_ac(),
        "percent": int(read(f"{bat}/capacity")),
        "status": read(f"{bat}/status"),
        "cycles": int(read(f"{bat}/cycle_count")),
        "energy_full": uwh("energy_full"),
        "energy_design": uwh("energy_full_design"),
        "watts": uwh("power_now"),
        "bl_now": int(read(f"{panel}/brightness")),
        "bl_max": int(read(f"{panel}/max_brightness")),
        "suspend_mode": pick(mem),
        "suspend_modes": [w.strip("[]") for w in mem.split()],
        "hibernate_method": pick(read("/sys/power/disk")),
        "os": re.search(r'PRETTY_NAME="([^"]*)"', read("/etc/os-release")).group(1),
        "kernel": read("/proc/sys/kernel/osrelease"),
        "systemd": sh("systemctl", "--version").split()[1],
        "services": [(u, unit_active(u, user), pkg_version(b) if b else None)
                     for u, b, user in SERVICES],
        "hyprland_version": pkg_version("Hyprland"),
        "logind": logind(),
        # True/False only when the answer is real; None means the user bus
        # could not be reached, which is not the same as "not running".
        "hypridle": {"active": True, "inactive": False, "failed": False}.get(
            subprocess.run(["systemctl", "--user", "is-active", "hypridle.service"],
                           capture_output=True, text=True).stdout.strip()),
        "tlp_set": {k: v for s, k, v in tlp_rows if not s.endswith("defaults.conf")},
        "tlp_all": tlp_conf,
        "tlp_pairs": {k[:-6]: (v, tlp_conf.get(k[:-6] + "_ON_BAT"))
                      for k, v in tlp_conf.items() if k.endswith("_ON_AC")},
        "governor": first("/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor"),
        "charge_start": int(first("/sys/class/power_supply/BAT*/charge_control_start_threshold")),
        "charge_stop": int(first("/sys/class/power_supply/BAT*/charge_control_end_threshold")),
        "radios": radios,
    }

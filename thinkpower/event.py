"""Handlers for events the daemons route to us.

logind and acpid stop deciding anything; they hand the event here and
policy.toml decides. Everything in this module runs as root (acpid and the
charge timer are system units), so it must not assume a session.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from . import act, policy

# One press emits two ACPI events on this hardware (PBTN and PWRF), so
# without this the action runs twice and the second gets "suspend already in
# progress". Observed 2026-09-26.
DEBOUNCE_SECONDS = 3

ACTIONS = {
    "suspend": ["systemctl", "suspend"],
    "hibernate": ["systemctl", "hibernate"],
    "suspend-then-hibernate": ["systemctl", "suspend-then-hibernate"],
    "poweroff": ["systemctl", "poweroff"],
    "lock": ["loginctl", "lock-sessions"],
    "ignore": None,
}


def debounced(name: str, seconds: int = DEBOUNCE_SECONDS) -> bool:
    stamp = act.runtime_dir() / f"{name}.stamp"
    now = time.time()
    if stamp.exists() and now - stamp.stat().st_mtime < seconds:
        return True
    stamp.write_text(str(now))
    return False


def run_action(name: str) -> None:
    cmd = ACTIONS.get(name)
    if cmd is None and name not in ACTIONS:
        raise SystemExit(f"unknown action in policy: {name}")
    if cmd:
        subprocess.run(cmd, check=False)


def source() -> str:
    return "ac" if act.on_ac() else "battery"


def ev_power() -> int:
    if debounced("powerkey"):
        return 0
    run_action(policy.load()["button"]["power"][source()])
    return 0


def ev_charge() -> int:
    """The low-battery ladder, over descending charge.

    Ported from the hand-written script in power.nix, with two changes: the
    thresholds come from policy.toml, and the brightness save goes through
    act so it cannot clobber a save the idle ladder already made.
    """
    pol = policy.load()["battery"]["low"]
    if act.on_ac():
        _recover()
        return 0
    cap = int(Path("/sys/class/power_supply/BAT0/capacity").read_text())
    if cap > pol["backlight_off"]:
        _recover()
        return 0
    if cap <= pol["hibernate"]:
        # Fall back rather than doing nothing: a refused hibernate must not
        # leave a flat battery awake.
        if subprocess.run(["systemctl", "hibernate"], check=False).returncode:
            subprocess.run(["systemctl", "suspend"], check=False)
        return 0
    # Between the two thresholds: park the screen, once, and keep polling.
    latch = act.runtime_dir() / "low-battery"
    if not latch.exists():
        latch.touch()
        subprocess.run(["loginctl", "lock-sessions"], check=False)
        act.cmd_do("backlight-off")
    return 0


def _recover() -> None:
    latch = act.runtime_dir() / "low-battery"
    if latch.exists():
        latch.unlink()
        act.cmd_do("restore")

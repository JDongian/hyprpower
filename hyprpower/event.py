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

ACTIONS = {a: ["systemctl", a] for a in
           ("suspend", "hibernate", "suspend-then-hibernate", "poweroff")}
ACTIONS |= {"lock": ["loginctl", "lock-sessions"], "ignore": None}


def latch() -> Path:
    return act.runtime_dir() / "low-battery"


def debounced(name: str, seconds: int = DEBOUNCE_SECONDS) -> bool:
    stamp = act.runtime_dir() / f"{name}.stamp"
    now = time.time()
    if stamp.exists() and now - stamp.stat().st_mtime < seconds:
        return True
    stamp.write_text(str(now))
    return False


def run_action(name: str) -> None:
    if name not in ACTIONS:
        raise SystemExit(f"unknown action in policy: {name}")
    if cmd := ACTIONS[name]:            # "ignore" maps to None on purpose
        subprocess.run(cmd, check=True)


def source() -> str:
    return "ac" if act.on_ac() else "battery"


def ev_power() -> int:
    if debounced("powerkey"):
        return 0
    run_action(policy.load()["button"]["power"][source()])
    return 0


def ev_charge() -> int:
    """The low-battery ladder, over descending charge.

    The brightness save goes through act so it cannot clobber a save the
    idle ladder already made.
    """
    pol = policy.load()["battery"]["low"]
    cap = int(Path("/sys/class/power_supply/BAT0/capacity").read_text())
    if act.on_ac() or cap > pol["backlight_off"]:
        _recover()
        return 0
    if cap <= pol["hibernate"]:
        # The one deliberate fallback here: a refused hibernate must not
        # leave a nearly-flat battery awake.
        if subprocess.run(["systemctl", "hibernate"], check=False).returncode:
            subprocess.run(["systemctl", "suspend"], check=True)
        return 0
    if not latch().exists():
        latch().touch()
        subprocess.run(["loginctl", "lock-sessions"], check=True)
        act.cmd_do("backlight-off")
    return 0


def _recover() -> None:
    if latch().exists():
        latch().unlink()
        act.cmd_do("restore")

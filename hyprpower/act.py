"""The actions the generated hypridle config calls back into.

`on <source>` is a condition_cmd: exit 0 to proceed, non-zero to defer.
`do <action>` performs one rung of the ladder.

THIS MODULE OWNS THE BRIGHTNESS SAVE SLOT. `brightnessctl -s` keeps exactly
one saved value per device, so a dim followed by a backlight-off overwrites
the real brightness with the dimmed one and `-r` restores 10 instead of what
you had. That bug is live in the current config. We keep our own save file
and never call `brightnessctl -s`.
"""

from __future__ import annotations

import glob
import os
import subprocess
from pathlib import Path

from . import policy
from .probe import on_ac


def runtime_dir() -> Path:
    """One save location shared by BOTH privilege levels.

    hypridle's rungs run as the user; the low-battery handler runs as root.
    If they kept separate files, root could save the already-dimmed value and
    "restore" you to 10% -- the same single-slot bug, across the uid boundary.
    /run/hyprpower is reachable from both."""
    d = Path("/run/hyprpower")
    if not d.exists():
        d.mkdir()
        os.chmod(d, 0o1777)   # sticky, like /tmp: either uid may write its own
    return d


def saved() -> Path:
    return runtime_dir() / "backlight"


def panel() -> str:
    return sorted(glob.glob("/sys/class/backlight/*"))[0]



def sh(*cmd: str) -> None:
    subprocess.run(cmd, check=True)


def save_brightness() -> None:
    """First writer wins, so a later rung never clobbers the real value."""
    if not stored():
        saved().write_text(open(f"{panel()}/brightness").read().strip())
        # 0666 because the two privilege levels share this file: the idle
        # ladder saves as you, the low-battery handler as root, and either
        # may need to clear it. The directory is sticky, so whoever did NOT
        # create it cannot unlink -- hence clearing by truncation below.
        os.chmod(saved(), 0o666)


def stored() -> str | None:
    """The saved brightness, or None. Empty means cleared, not missing:
    truncation is how the other uid releases the slot under a sticky dir."""
    if not saved().exists():
        return None
    return saved().read_text().strip() or None


def source() -> str:
    return "ac" if on_ac() else "battery"


def cmd_on(source: str) -> int:
    assert source in ("ac", "battery"), source
    return 0 if (source == "ac") == on_ac() else 1


def cmd_do(action: str) -> int:
    pol = policy.load()
    if action in ("dim", "backlight-off"):
        save_brightness()
        sh("brightnessctl", "--quiet", "set",
           pol["display"]["dim_to"] if action == "dim" else "0")
    elif action == "display-off":
        # Opt-in only: a dpms-off listener crashed the whole Hyprland session
        # on 0.55.x (SIGABRT -> greetd relogin).
        sh("hyprctl", "dispatch", "dpms", "off")
    elif action == "lock":
        sh("loginctl", "lock-session")
    elif action in ("suspend", "hibernate", "shutdown"):
        sh("systemctl", policy.systemd_sleep(pol, source(), action))
    elif action == "restore":
        # Brightness first: it always applies, and it must not be blocked by
        # hyprctl failing. dpms only when the display-off rung is configured
        # at all -- that rung leaves the panel powered down, so restoring the
        # brightness alone would wake you to a black screen.
        if value := stored():
            sh("brightnessctl", "--quiet", "set", value)
            saved().write_text("")
        if any(pol["idle"]["display_off"][s] for s in ("ac", "battery")):
            sh("hyprctl", "dispatch", "dpms", "on")
    else:
        raise SystemExit(f"unknown action: {action}")
    return 0

"""Assert the machine matches policy.toml.

Only two things can disagree, and both mean "policy was edited and not
applied":

  * hypridle reads its config once at startup
  * logind caches its own config until reloaded

[button.power] and [charge] read policy at event time, so they cannot drift
and are not checked. Mismatch is an error, not a status — fail fast.
"""

from __future__ import annotations

import glob
from pathlib import Path

from . import apply, policy, probe
from .view import secs as _fmt


def pending(pol: dict | None = None) -> list[tuple[str, str, str, bool]]:
    """Rows whose policy value is not yet in force.

    Returns (row id, in force, wanted, needs root). [button.power] and
    [charge] never appear: they read policy at event time.
    """
    pol = pol or policy.load()
    out = []

    conf = apply.state_dir() / "hypridle.conf"
    live = policy.parse_generated(conf.read_text()) if conf.exists() else {}
    for step, _action, _ in policy.STEPS:
        for src in policy.SOURCES:
            want = policy.seconds(pol["idle"][step][src])
            have = live.get((step, src))
            if want != have:
                out.append((f"idle.{step}",
                            _fmt(have), _fmt(want), False))
                break          # one row per step; columns share an id

    lg = probe.logind()
    for key, prop in (("ac", "HandleLidSwitchExternalPower"),
                      ("battery", "HandleLidSwitch"),
                      ("docked", "HandleLidSwitchDocked")):
        if lg[prop] != pol["lid"][key]:
            rid = "lid.docked" if key == "docked" else "lid.close"
            out.append((rid, lg[prop], pol["lid"][key], True))

    # Charge thresholds. These CAN drift: TLP used to own them and still
    # reasserts its own values whenever it restarts, so a mismatch here also
    # means "something else is writing these".
    bat = sorted(glob.glob("/sys/class/power_supply/BAT*"))[0]
    for key, attr, rid in (("start", "charge_control_start_threshold", "chg.start"),
                           ("stop", "charge_control_end_threshold", "chg.stop")):
        live = int(open(f"{bat}/{attr}").read())
        if live != pol["battery"]["charge"][key]:
            out.append((rid, f"{live}%", f'{pol["battery"]["charge"][key]}%', True))
    return out



def problems(pol: dict | None = None) -> list[str]:
    pol = pol or policy.load()
    out = []

    conf = apply.state_dir() / "hypridle.conf"
    want = policy.render_hypridle(pol, exe=apply.thinkpower_exe())
    if not conf.exists():
        out.append(f"{conf} does not exist — run `thinkpower apply`")
    elif conf.read_text() != want:
        out.append(f"{conf} is stale — run `thinkpower apply`")

    lg = probe.logind()
    for key, prop in (("ac", "HandleLidSwitchExternalPower"),
                      ("battery", "HandleLidSwitch"),
                      ("docked", "HandleLidSwitchDocked")):
        if lg[prop] != pol["lid"][key]:
            out.append(f"logind {prop} is {lg[prop]!r}, policy says "
                       f"{pol['lid'][key]!r} — run `sudo thinkpower apply --system`")
    return out


def check(pol: dict | None = None) -> None:
    if found := problems(pol):
        raise SystemExit("\n".join(found))

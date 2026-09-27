"""Assert the machine matches policy.toml.

Four things can disagree: hypridle reads its config once at startup, logind
caches its own until reloaded, TLP reasserts its charge thresholds whenever it
restarts, and the hibernate delay lives in a systemd drop-in. [button.power] and the low-battery ladder read policy at
event time, so they cannot drift and are not checked.
"""

from __future__ import annotations

import glob

from . import apply, policy, probe
from .view import secs as _fmt

LID = (("ac", "HandleLidSwitchExternalPower", "lid.close"),
       ("battery", "HandleLidSwitch", "lid.close"),
       ("docked", "HandleLidSwitchDocked", "lid.docked"))

CHARGE = (("start", "charge_control_start_threshold", "chg.start"),
          ("stop", "charge_control_end_threshold", "chg.stop"))


def lid_drift(pol: dict) -> list[tuple[str, str, str, str]]:
    lg = probe.logind()
    return [(rid, prop, lg[prop], pol["lid"][key])
            for key, prop, rid in LID if lg[prop] != pol["lid"][key]]


def charge_drift(pol: dict) -> list[tuple[str, int, int]]:
    bat = sorted(glob.glob("/sys/class/power_supply/BAT*"))[0]
    out = []
    for key, attr, rid in CHARGE:
        live, want = int(open(f"{bat}/{attr}").read()), pol["battery"]["charge"][key]
        if live != want:
            out.append((rid, live, want))
    return out


def pending(pol: dict | None = None) -> list[tuple[str, str, str, bool]]:
    """Rows whose policy value is not yet in force: (id, in force, wanted, needs root)."""
    pol = pol or policy.load()
    conf = apply.state_dir() / "hypridle.conf"
    live = policy.parse_generated(conf.read_text()) if conf.exists() else {}

    want = {(src, a): t for t, src, a, _ in policy.rungs(pol)}
    rows: dict[str, tuple] = {}
    for key in sorted(set(want) | set(live)):
        if want.get(key) != live.get(key):
            rid = policy.ROW[key[1]]
            rows.setdefault(rid, (rid, _fmt(live.get(key)), _fmt(want.get(key)), False))
    out = list(rows.values())
    out += [(rid, have, want, True) for rid, _, have, want in lid_drift(pol)]
    out += [(rid, f"{h}%", f"{w}%", True) for rid, h, w in charge_drift(pol)]
    return out


def sleep_drift(pol: dict) -> bool:
    want = apply.sleep_text(pol)
    have = apply.SLEEP_PATH.read_text() if apply.SLEEP_PATH.exists() else None
    return want != have


def problems(pol: dict | None = None) -> list[str]:
    pol = pol or policy.load()
    conf = apply.state_dir() / "hypridle.conf"
    want = policy.render_hypridle(pol, exe=apply.hyprpower_exe())

    out = []
    if not conf.exists():
        out.append("Idle config is missing. Apply to write it.")
    elif conf.read_text() != want:
        out.append("Idle config is out of date. Apply to update it.")
    if lid_drift(pol):
        out.append("Lid action in logind does not match policy. Apply to fix.")
    if charge_drift(pol):
        out.append("Charge thresholds do not match policy. Apply to fix.")
    if sleep_drift(pol):
        out.append("Hibernate delay in systemd does not match policy. Apply to fix.")
    return out


def check(pol: dict | None = None) -> None:
    if found := problems(pol):
        raise SystemExit("\n".join(found))

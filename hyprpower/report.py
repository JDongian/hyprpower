"""Everything shown, and everything concluded. PURE.

Takes a policy and one machine snapshot and returns rows, flags and drift.
It never reads the machine itself -- an earlier version let the view fetch
its own state by default, and that is where purity eroded.

Policy-owned settings come from the profile: it IS the truth, so there is no
"declared vs live" column. Where the two could disagree that is drift, and
drift is a flag rather than a column.
"""

from __future__ import annotations

from enum import IntEnum
from typing import NamedTuple

from . import policy

class Row(NamedTuple):
    """One line in a table. `values` has one entry per column of the section
    it lands in, so a mismatch is a crash at add_row rather than a wrong
    cell -- which is how it was found the first time."""
    id: str
    label: str
    values: list[str]
    detail: str | None = None


class Pending(NamedTuple):
    """A row whose profile value is not yet in force."""
    row: str
    have: str
    want: str
    needs_root: bool


class Drift(NamedTuple):
    row: str
    prop: str           # the logind property, or the sysfs attribute
    have: str
    want: str


class Col(IntEnum):
    """Which value column a cell is in. IntEnum so the TUI can look up by the
    raw cursor column and still match these keys."""
    AC = 0
    BATTERY = 1
    ONLY = 0        # single-valued rows have just the one column


ONE, TWO = [""], ["Plugged in", "On battery"]

LADDER = [("Dim display after", "dim"),
          ("Turn off backlight after", "backlight_off"),
          ("Turn off display after", "display_off"),
          ("Lock after", "lock"),
          ("Suspend after", "suspend"),
          ("Hibernate after", "hibernate")]
LABEL = {key: label for label, key in LADDER}
RATE_LABEL = {"Discharging": "Discharge rate", "Charging": "Charge rate"}

# TLP has ~59 settings. Grouped by what each controls, most consequential
# first; vendor-irrelevant ones last.
TLP_GROUPS = [
    ("Charging policy", ("START_CHARGE", "STOP_CHARGE", "RESTORE_THRESHOLDS")),
    ("CPU and platform", ("CPU_", "PLATFORM_PROFILE", "NMI_")),
    ("Radios and network", ("WIFI_", "WOL_", "DEVICES_TO_")),
    ("Disk", ("DISK_", "SATA_", "AHCI_", "MAX_LOST_WORK", "BAY_")),
    ("USB and PCIe", ("USB_", "PCIE_", "RUNTIME_PM")),
    ("Audio", ("SOUND_",)),
    ("Graphics", ("AMDGPU_", "RADEON_", "INTEL_")),
    ("TLP itself", ("TLP_", "NATACPI", "TPSMAPI", "TPACPI", "RESTORE_DEVICE")),
]


def tlp_group(key: str) -> str:
    for name, prefixes in TLP_GROUPS:
        if key.startswith(prefixes):
            return name
    return "Other"


def secs(v) -> str:
    if v is None or v is False:
        return "never"
    if isinstance(v, str):
        return v if not v[0].isdigit() else secs(policy.seconds(v))
    m, s = divmod(int(v), 60)
    return f"{m}m{s}s" if m and s else f"{m}m" if m else f"{s}s"


def pct(v) -> str:
    return "—" if v is None else f"{v}%"


class Build:
    """Accumulates rows and the id -> (label, value) map the flags cite."""

    def __init__(self, pol: dict, st: dict):
        self.pol, self.st = pol, st
        self.shown: dict[str, tuple[str, str]] = {}

    def row(self, rid, label, values, detail=None) -> Row:
        cells = [str(v) for v in values]
        self.shown[rid] = (label, " / ".join(cells))
        return Row(rid, label, cells, detail)


def build(pol: dict, st: dict):
    b = Build(pol, st)
    r, lg = b.row, st["logind"]
    idle, disp = pol["idle"], pol["display"]
    health = 100 * st["energy_full"] / st["energy_design"]
    both = lambda k: [pol[k]["ac"], pol[k]["battery"]]

    status = [
        r("now.source", "Power source", ["Plugged in" if st["on_ac"] else "On battery"]),
        r("now.charge", "Battery level", [f'{st["percent"]}%']),
        r("now.status", "Charging status", [st["status"]]),
        # power_now is the CHARGE rate when plugged in and the DISCHARGE rate
        # when not, so a fixed "Power draw" label means two different things
        # and reads as "the laptop uses 0 W" while idle on AC.
        r("now.rate", RATE_LABEL.get(st["status"], "Battery power flow"),
          [f'{st["watts"]:.1f} W' if st["watts"] else "—"]),
        r("now.backlight", "Display brightness",
          [f'{st["bl_now"]}/{st["bl_max"]} ({100 * st["bl_now"] / st["bl_max"]:.0f}%)']),
        r("now.cycles", "Cycle count", [st["cycles"]]),
        r("now.health", "Battery health",
          [f'{health:.1f}%  ({st["energy_full"]:.1f}/{st["energy_design"]:.1f} Wh)'],
          detail=("energy_full is what the gauge last learned, and relearning "
                  "needs charge counted from full all the way down to empty -- "
                  "a full charge alone does not do it. Near-design capacity at "
                  "a high cycle count means the design figure, not a "
                  "measurement.")),
        None,
        r("now.bluetooth", "Bluetooth", [st["radios"]["bluetooth"]]),
        r("now.wlan", "Wi-Fi", [st["radios"]["wlan"]]),
    ]

    system = [
        r("sys.os", "OS", [st["os"]]),
        r("sys.kernel", "Kernel", [st["kernel"]]),
        r("sys.systemd", "systemd", [st["systemd"]]),
        r("sys.compositor", "Compositor", [f'Hyprland {st["hyprland_version"]}']),
        None,
        # Shown because a drift flag naming a file the UI never displayed
        # was confusing.
        r("sys.policy", "Profile", [st["profile"]]),
        r("sys.generated", "Generated config",
          [st["generated"]]),
        None,
    ]
    # The ladder in Power management is only real while hypridle is running.
    for unit, active, version in st["services"]:
        system.append(r(f"svc.{unit}", unit,
                        [("running" if active else "stopped")
                         + (f"   v{version}" if version else "")]))

    press = [
        r("key.power", "Power button action",
          [pol["button"]["power"]["ac"], pol["button"]["power"]["battery"]]),
        r("key.power_held", "Power button action (press and hold)",
          [pol["button"]["power_held"]["ac"], pol["button"]["power_held"]["battery"]]),
        r("lid.close", "Lid close action", both("lid")),
    ]
    # docked wins over AC/battery, so it is a row not a column.
    press.append(r("lid.docked", "Lid close action (docked)",
                   [pol["lid"]["docked"]] * 2))
    mech = [
        r("susp.mode", "Suspend mode", [st["suspend_mode"]]),
        r("susp.all", "Suspend modes available", [", ".join(st["suspend_modes"])]),
        r("susp.hib", "Hibernate method", [st["hibernate_method"]]),
    ]

    ladder = []
    for label, key in LADDER:
        detail = None
        if key == "dim":
            detail = f'to {disp["dim_to"]}'
        ladder.append(r(f"idle.{key}", label,
                        [secs(idle[key]["ac"]), secs(idle[key]["battery"])],
                        detail=detail))
    ch, chg = pol["battery"]["low"], pol["battery"]["charge"]
    charge = [
        r("low.screen_off", "Turn off backlight + lock at",
          [pct(ch["backlight_off"])]),
        r("low.hibernate", "Hibernate at", [pct(ch["hibernate"])]),
        r("low.poll", "Battery level checked every", [ch["poll"]]),
        None,
        r("chg.start", "Start charging below", [pct(chg["start"])]),
        r("chg.stop", "Stop charging at", [pct(chg["stop"])]),
    ]
    logind_rows = [
        r("idle.logind", "Idle action",
          [f'{lg["IdleAction"]}   (armed {secs(lg["IdleActionUSec"])})']),
        r("susp.inhibit", "Inhibit delay max", [secs(lg["InhibitDelayMaxUSec"])]),
    ]

    # A trailing * means the setting is in /etc/tlp.conf here, not left at
    # a TLP default.
    star = lambda k: "  *" if k in st["tlp_set"] else ""
    paired = {f"{k}_ON_{sfx}" for k in st["tlp_pairs"] for sfx in ("AC", "BAT")}
    grouped: dict[str, list] = {}
    for key, (ac_v, bat_v) in st["tlp_pairs"].items():
        grouped.setdefault(tlp_group(key), []).append(
            r(f"tlp.{key}", key.lower() + star(f"{key}_ON_AC"),
              [ac_v or "—", bat_v or "—"]))
    for key, val in st["tlp_all"].items():
        if key not in paired:
            # Single-valued: leave the battery cell blank rather than
            # repeating, so "one value" reads at a glance.
            grouped.setdefault(tlp_group(key), []).append(
                r(f"tlp.{key}", key.lower() + star(key), [val or "—", ""]))
    grouped.setdefault("Charging policy", []).extend([
        r("tlp.cstart", "in force: start charging below",
          [pct(st["charge_start"]), ""]),
        r("tlp.cstop", "in force: stop charging at",
          [pct(st["charge_stop"]), ""]),
    ])
    grouped.setdefault("CPU and platform", []).append(
        r("tlp.now", "in force: cpu governor", [st["governor"], ""]))
    tlp_sheets = [(name, TWO, sorted(rows, key=lambda x: x[1]))
                  for name in [g for g, _ in TLP_GROUPS] + ["Other"]
                  if (rows := grouped.get(name))]
    if not st["tlp_all"]:
        tlp_sheets = [(None, ONE, [r("tlp.absent", "TLP", ["not installed"])])]

    panels = [
        ("Status", [(None, ONE, status), ("System", ONE, system)]),
        ("Hardware", [("Button and lid triggers", TWO, press),
                      ("Suspend and hibernate mechanism", ONE, mech)]),
        ("Power management", [("Idle actions", TWO, ladder),
                              ("Battery level triggers (only on battery)", ONE, charge),
                              ("Idle, system-wide (logind)", ONE, logind_rows)]),
        ("TLP", tlp_sheets),
    ]
    return panels, flags(pol, st, b.shown), b.shown


def flags(pol, st, shown):
    """Static sentences. The values that triggered each one are rendered from
    its deps, so nothing here interpolates."""
    out = []
    add = lambda text, *deps: out.append((text, [d for d in deps if d in shown]))
    idle, lg = pol["idle"], st["logind"]

    # Over the emitted listeners, not the raw policy: a hibernate that
    # follows a suspend is dropped, because systemd performs that escalation.
    by_source = {src: sorted((r.after, r.action) for r in policy.rungs(pol) if r.source == src)
                 for src in policy.SOURCES}
    if not any(a in policy.SLEEPS for rs in by_source.values() for _t, a in rs):
        add("Never suspends or hibernates when idle. Stays awake until you close "
            "the lid.",
            "idle.suspend", "lid.close")
    if cols := policy.collisions(pol):
        add("Two actions fire at the same time. Order is up to hypridle.",
            *sorted({policy.ROW[a] for _, _, acts in cols for a in acts}))
    # Inverted: `ignore` is systemd's default and is correct. An action here
    # would mean logind and hypridle both hold idle policy and race.
    if len(policy.delay_gaps(pol)) > 1:
        add("The two power sources want different hibernate delays, but "
            "systemd has only one. Applying will refuse.",
            "idle.suspend", "idle.hibernate")
    if lg["IdleAction"] != "ignore":
        add("logind acts on idle too. It fights the ladder above.", "idle.logind")

    dead, volatile = set(), set()
    for src, rs in by_source.items():
        if not (asleep := next(((t, a) for t, a in rs if a in policy.SLEEPS), None)):
            continue
        dead |= {policy.ROW[a] for t, a in rs if t > asleep[0]}
        if src == policy.Source.BATTERY and not (
                asleep[1] in policy.REACHES_DISK or policy.escalates(pol, src)):
            volatile.add(policy.ROW[asleep[1]])
    # The lid and button escalate by the same rule as the ladder, so losing
    # the hibernate rung silently unprotects them too.
    for rid, action in [("lid.close", pol["lid"]["battery"]),
                        ("key.power", pol["button"]["power"]["battery"])]:
        if action == policy.Action.SUSPEND and not policy.escalates(pol, "battery"):
            volatile.add(rid)
    if dead:
        add("Scheduled after the machine is already asleep, so it never runs.",
            *sorted(dead))
    if volatile:
        add("This suspend never reaches disk, so the battery can run flat.",
            *sorted(volatile), "idle.hibernate")

    if not st["hypridle"]:
        add("hypridle is not running, so no idle action happens.",
            "svc.hypridle", "idle.dim", "idle.lock")
    for problem in problems(pol, st):
        add(problem)
    return out


# Four things can disagree. hypridle reads its config once at startup, logind
# caches its own until reloaded, TLP reasserts charge thresholds whenever it
# restarts, and the hibernate delay lives in a systemd drop-in. [button.power]
# and the low-battery ladder read the profile at event time, so they cannot
# drift and are not checked.

LID_ROW = {"HandleLidSwitchExternalPower": "lid.close",
           "HandleLidSwitch": "lid.close",
           "HandleLidSwitchDocked": "lid.docked"}

CHARGE_ROW = (("start", "charge_start", "chg.start"),
              ("stop", "charge_stop", "chg.stop"))


def lid_drift(pol: dict, st: dict) -> list[Drift]:
    want = policy.logind_want(pol)
    return [Drift(LID_ROW[prop], prop, st["logind"][prop], v)
            for prop, v in want.items() if st["logind"][prop] != v]


def charge_drift(pol: dict, st: dict) -> list[Drift]:
    return [Drift(rid, key, st[key], pol["battery"]["charge"][name])
            for name, key, rid in CHARGE_ROW
            if st[key] != pol["battery"]["charge"][name]]


def pending(pol: dict, st: dict) -> list[Pending]:
    live = policy.parse_generated(st["live_hypridle"]) if st["live_hypridle"] else {}
    want = {(r.source, r.action): r.after for r in policy.rungs(pol)}
    rows: dict[str, tuple] = {}
    for key in sorted(set(want) | set(live)):
        if want.get(key) != live.get(key):
            rid = policy.ROW[key[1]]
            rows.setdefault(rid, Pending(rid, secs(live.get(key)),
                                         secs(want.get(key)), False))
    out = list(rows.values())
    out += [Pending(d.row, d.have, d.want, True) for d in lid_drift(pol, st)]
    out += [Pending(d.row, f"{d.have}%", f"{d.want}%", True)
            for d in charge_drift(pol, st)]
    return out


def problems(pol: dict, st: dict) -> list[str]:
    out = []
    if st["live_hypridle"] is None:
        out.append("Generated config is missing. Apply to write it.")
    elif st["live_hypridle"] != st["want_hypridle"]:
        out.append("Generated config is out of date. Apply to update it.")
    if lid_drift(pol, st):
        out.append("Lid action in logind does not match the profile. Apply to fix.")
    if charge_drift(pol, st):
        out.append("Charge thresholds do not match the profile. Apply to fix.")
    if policy.sleep_text(pol) != st["live_sleep"]:
        out.append("Hibernate delay in systemd does not match the profile. Apply to fix.")
    return out


EDITABLE: dict[tuple[str, int], tuple[str, ...]] = {
    **{(f"idle.{k}", Col[src.upper()]): ("idle", k, src)
       for k in (s.key for s in policy.STEPS) for src in policy.SOURCES},
    ("lid.close", Col.AC): ("lid", "ac"),
    ("lid.close", Col.BATTERY): ("lid", "battery"),
    ("lid.docked", Col.ONLY): ("lid", "docked"),
    ("key.power", Col.AC): ("button", "power", "ac"),
    ("key.power", Col.BATTERY): ("button", "power", "battery"),
    ("key.power_held", Col.AC): ("button", "power_held", "ac"),
    ("key.power_held", Col.BATTERY): ("button", "power_held", "battery"),
    ("chg.start", Col.ONLY): ("battery", "charge", "start"),
    ("chg.stop", Col.ONLY): ("battery", "charge", "stop"),
    ("low.screen_off", Col.ONLY): ("battery", "low", "backlight_off"),
    ("low.hibernate", Col.ONLY): ("battery", "low", "hibernate"),
    ("low.poll", Col.ONLY): ("battery", "low", "poll"),
}


def coerce(raw: str):
    """'never'/'false' -> False, digits -> int, else the string as given."""
    text = raw.strip()
    if text.lower() in ("never", "false", "off", "none"):
        return False
    if text.isdigit():
        return int(text)
    return text


def current(pol: dict, path: tuple[str, ...]):
    node = pol
    for key in path:
        node = node[key]
    return node

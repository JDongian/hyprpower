"""Build the display: check configs, check system, lay them out.

Policy-owned settings come from policy.toml — it IS the truth, so there is
no "declared vs live" column. Everything else is read from the machine.
Where the two could disagree (see verify.py) that is an error, not a status.

Layout, preserved deliberately: four tabs, sub-tabs per system where a
comparison exists, and two columns only where a setting genuinely differs by
power source.
"""

from __future__ import annotations

from . import apply, policy, probe

SYSTEMS = ("this machine", "macOS", "Windows")
ONE, TWO = [""], ["Plugged in", "On battery"]
NEVER, VARIES, ON_SUSPEND = "never", "varies", "on suspend"
RATE_LABEL = {"Discharging": "Discharge rate", "Charging": "Charge rate"}

# TLP has ~59 settings. Group by what each controls, ordered by how much it
# shapes battery life; vendor-irrelevant ones last.
# Order matters: most consequential first.
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
        return NEVER
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

    def row(self, rid, label, values, mac=None, win=None, detail=None):
        n = len(values)
        fill = lambda v: [v] * n if not isinstance(v, (list, tuple)) else list(v)
        cells = {"this machine": [str(v) for v in values],
                 "macOS": [str(v) for v in fill(mac if mac is not None else "—")],
                 "Windows": [str(v) for v in fill(win if win is not None else "—")]}
        self.shown[rid] = (label, cells["this machine"][0])
        return (rid, label, cells, detail)


def build(pol: dict | None = None, st: dict | None = None):
    pol = pol or policy.load()
    st = st or probe.state()
    b = Build(pol, st)
    r, lg = b.row, st["logind"]
    idle, disp = pol["idle"], pol["display"]
    health = 100 * st["energy_full"] / st["energy_design"]
    both = lambda k: [pol[k]["ac"], pol[k]["battery"]]

    # --- 1. Status -------------------------------------------------------
    status = [
        r("now.source", "Power source", ["AC" if st["on_ac"] else "battery"]),
        r("now.charge", "Battery level", [f'{st["percent"]}%']),
        r("now.status", "Charging status", [st["status"]]),
        # power_now is the CHARGE rate when plugged in and the DISCHARGE rate
        # when not, so a fixed "Power draw" label means two different things
        # and reads as "the laptop uses 0 W" while idle on AC.
        r("now.rate", RATE_LABEL.get(st["status"], "Battery power flow"),
          [f'{st["watts"]:.1f} W' if st["watts"] else "—"]),
        r("now.backlight", "Screen brightness",
          [f'{st["bl_now"]}/{st["bl_max"]} ({100 * st["bl_now"] / st["bl_max"]:.0f}%)']),
        r("now.cycles", "Cycle count", [st["cycles"]]),
        r("now.health", "Battery health",
          [f'{health:.1f}%  ({st["energy_full"]:.1f}/{st["energy_design"]:.1f} Wh)']),
        None,
        r("now.bluetooth", "Bluetooth", [st["radios"].get("bluetooth", "—")]),
        r("now.wlan", "Wi-Fi", [st["radios"].get("wlan", "—")]),
        # The ladder in Power management is only real while this is running.
        # None means the user bus was unreachable, which is not "stopped".
    ]

    # --- 1b. System ------------------------------------------------------
    system = [
        r("sys.os", "OS", [st["os"]]),
        r("sys.kernel", "Kernel", [st["kernel"]]),
        r("sys.systemd", "systemd", [st["systemd"]]),
        r("sys.compositor", "Compositor", [f'Hyprland {st["hyprland_version"]}']),
        None,
        # Make the chain visible: policy is the source, the generated config
        # is the artifact, hypridle is what enforces it. A "stale config"
        # flag referring to a file the UI never showed was just confusing.
        r("sys.policy", "Policy file", [str(policy.path())]),
        r("sys.generated", "Idle config (generated)",
          [f'{apply.state_dir() / "hypridle.conf"}']),
        None,
    ]
    for unit, active, version in st["services"]:
        system.append(r(f"svc.{unit}", unit,
                        [{True: "running", False: "stopped"}.get(active, "unknown")
                         + (f"   v{version}" if version else "")]))

    # --- 2. Hardware -----------------------------------------------------
    press = [
        r("key.power", "Power button action",
          [pol["button"]["power"]["ac"], pol["button"]["power"]["battery"]],
          mac="suspend", win="suspend"),
        r("key.power_held", "Power button action (press and hold)",
          [pol["button"]["power_held"]["ac"], pol["button"]["power_held"]["battery"]]),
        r("lid.close", "Lid close action", both("lid"), mac="suspend", win="suspend"),
    ]
    # Rows, not columns: the lid's axis is {docked, AC, battery} with docked
    # winning, so it is not the two-valued power-source split used above.
    press.append(r("lid.docked", "Lid close action (docked)",
                   [pol["lid"]["docked"]] * 2))
    mech = [
        r("susp.mode", "Suspend mode", [st["suspend_mode"]]),
        r("susp.all", "Suspend modes available", [", ".join(st["suspend_modes"])]),
        r("susp.hib", "Hibernate method", [st["hibernate_method"]]),
    ]

    # --- 3. Power management --------------------------------------------
    # (label, policy key, macOS ac/bat, Windows ac/bat)
    LADDER = [("Dim display after", "dim", (585, 105), (300, 120)),
              ("Turn off backlight after", "backlight_off", (600, 120), (300, 180)),
              ("Turn off display after", "display_off", (None, None), (None, None)),
              ("Lock after", "lock", (600, 120), (ON_SUSPEND, ON_SUSPEND)),
              ("Suspend after", "suspend", (NEVER, VARIES), (900, 600))]
    ladder = []
    for label, key, mac, win in LADDER:
        detail = None
        if key == "dim":
            detail = f'to {disp["dim_to"]}'
        elif key == "backlight_off":
            detail = f'via {disp["off_method"]}'
        ladder.append(r(f"idle.{key}", label,
                        [secs(idle[key]["ac"]), secs(idle[key]["battery"])],
                        mac=[secs(m) for m in mac], win=[secs(w) for w in win],
                        detail=detail))

    ch, chg = pol["battery"]["low"], pol["battery"]["charge"]
    charge = [
        r("low.screen_off", "Turn off backlight + lock at",
          [pct(ch["backlight_off"])], win="—"),
        r("low.hibernate", "Hibernate at", [pct(ch["hibernate"])], mac=VARIES, win="5%"),
        r("low.poll", "Battery level checked every", [ch["poll"]]),
        None,
        r("chg.start", "Start charging below", [pct(chg["start"])]),
        r("chg.stop", "Stop charging at", [pct(chg["stop"])]),
    ]
    logind_rows = [
        r("idle.logind", "Idle action (logind)",
          [f'{lg["IdleAction"]}   (armed {secs(lg["IdleActionUSec"])})']),
        r("susp.inhibit", "Inhibit delay max", [secs(lg["InhibitDelayMaxUSec"])]),
    ]

    # --- 4. TLP (read-only; TLP owns these) ------------------------------
    # Every setting TLP has in effect, grouped by what it controls rather
    # than by whether it happens to split by power source. A trailing *
    # means it is set in /etc/tlp.conf here, not left at a TLP default.
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
        r("tlp.cstart", "in effect now: start charging below",
          [pct(st["charge_start"]), ""]),
        r("tlp.cstop", "in effect now: stop charging at",
          [pct(st["charge_stop"]), ""]),
    ])
    grouped.setdefault("CPU and platform", []).append(
        r("tlp.now", "in effect now: cpu governor", [st["governor"], ""]))
    tlp_sheets = [(name, TWO, sorted(rows, key=lambda x: x[1]))
                  for name in [g for g, _ in TLP_GROUPS] + ["Other"]
                  if (rows := grouped.get(name))]

    panels = [
        ("Status", [(None, ONE, status), ("System", ONE, system)], False),
        ("Hardware", [("Button and lid triggers", TWO, press),
                      ("Suspend and hibernate mechanism", ONE, mech)], True),
        ("Power management", [("Idle Actions", TWO, ladder),
                              ("Battery level triggers (only on battery)", ONE, charge),
                              ("Idle, system-wide (logind)", ONE, logind_rows)], True),
        ("TLP", tlp_sheets, False),
    ]
    return panels, flags(pol, st, b.shown, health), b.shown


def flags(pol, st, shown, health):
    out = []
    add = lambda text, *deps: out.append((text, [d for d in deps if d in shown]))
    idle, lg = pol["idle"], st["logind"]

    # No flag for display_off = never. It is a deliberate choice: dpms off
    # crashed the whole Hyprland session on 0.55.x, and on this ladder the
    # saving is ~0.13 Wh (the 2m->10m window on battery). The row still shows
    # "never"; it just is not a finding.
    if not idle["suspend"]["ac"] and not idle["suspend"]["battery"]:
        add("Never suspends on idle; awake until the lid closes.",
            "idle.suspend", "lid.close")
    # One rule, not one flag per occurrence: the finding is "two actions are
    # scheduled at the same moment", and the instances are its evidence.
    if cols := policy.collisions(pol):
        where = "; ".join(f"{' and '.join(a)} at {secs(t)} on {src}"
                          for t, src, a in cols)
        deps = sorted({f"idle.{a.replace('-', '_')}"
                       for _, _, acts in cols for a in acts})
        add(f"Actions scheduled at the same moment run in an undefined order "
            f"({where}).", *deps)
    if health > 95 and st["charge_stop"] < 100:
        add("Health is unproven: charging stops short of full, so the gauge may "
            "never have recalibrated.", "now.health", "tlp.cstop")
    # Inverted: `ignore` is correct and is systemd's default. A real action
    # here would be the finding — logind and hypridle would BOTH hold idle
    # policy and race. (logind could not act anyway: IdleHint is never set on
    # this Wayland session, so it never sees the machine as idle.)
    if lg["IdleAction"] != "ignore":
        add(f"logind has its own idle action ({lg['IdleAction']} after "
            f"{secs(lg['IdleActionUSec'])}), competing with the idle ladder.",
            "idle.logind")
    if st["suspend_mode"] == "s2idle" and "deep" in st["suspend_modes"]:
        # TODO benchmark this rather than trusting the claim. s2idle drain is
        # platform-dependent and Whiskey Lake S0ix is reportedly weak, but
        # that is hearsay until measured on this machine:
        #   1. on battery, read /sys/class/power_supply/BAT0/energy_now
        #   2. suspend for a measured hour, resume, read it again
        #   3. `echo deep | sudo tee /sys/power/mem_sleep` (resets on reboot)
        #   4. repeat identically
        # Same duration and starting charge both times, nothing plugged in.
        # If deep wins: boot.kernelParams = [ "mem_sleep_default=deep" ].
        add("Suspend uses s2idle; deep (S3) is also supported and has not been "
            "measured on this machine.", "susp.mode", "susp.all")
    # The ladder is only real if its daemon is alive; without this the UI
    # shows policy as though it were in force. Dropped in the rewrite.
    if st["hypridle"] is False:
        add("hypridle is not running, so none of the idle ladder is in effect.",
            "svc.hypridle", "idle.dim", "idle.lock")
    # The UI presents policy.toml as the truth, so say when it is not applied.
    from .verify import problems
    for problem in problems(pol):
        add(problem)
    # No flag for radios staying up on battery. TLP can drop them when idle
    # (DEVICES_TO_DISABLE_ON_BAT_NOT_IN_USE) but Wi-Fi is always in use and
    # an idle Bluetooth controller is ~5-25mW: under 0.4% of this battery
    # over a full discharge. Detectable, not meaningful.
    return out

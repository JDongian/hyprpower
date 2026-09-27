"""Every side effect, in one place.

This is the ONLY module that runs a process, reads /sys, or writes a file.
Everything else is a function of its arguments. The rule is mechanical:

    grep -lE 'subprocess|/sys/|/etc/' hyprpower/*.py   ->   system.py

`read()` takes one snapshot of the machine -- facts and live artifact
contents together -- and the pure modules work from that snapshot rather
than reaching for the world themselves.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import NamedTuple

from . import policy


def run(*cmd: str) -> None:
    subprocess.run(cmd, check=True)


def out(*cmd: str) -> str:
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=5, check=True).stdout


def slurp(path: str) -> str:
    return open(path, encoding="utf-8").read().strip()


def first(pattern: str) -> str:
    return slurp(sorted(glob.glob(pattern))[0])


def battery() -> str:
    """The first battery. Everything uses this, so a dual-battery machine is
    consistently wrong rather than inconsistently wrong."""
    return sorted(glob.glob("/sys/class/power_supply/BAT*"))[0]


def panel() -> str:
    return sorted(glob.glob("/sys/class/backlight/*"))[0]



# Fixed search order, NOT the caller's PATH. The rendered config must be
# byte-identical whoever generates it, or `verify` reports drift purely
# because apply ran from a different shell than the check did.
BINDIRS = ("/run/current-system/sw/bin", "/usr/bin", "/bin", "/usr/local/bin")


def which(binary: str) -> str:
    for d in BINDIRS:
        if (p := Path(d) / binary).exists():
            return str(p)
    if p := shutil.which(binary):
        return p
    raise SystemExit(f"cannot find {binary}; hypridle runs with a minimal "
                     f"PATH and needs an absolute path or its rungs exit 127")


def bins() -> dict:
    return {b: which(b) for b in ("loginctl", "hyprctl", "systemctl")}


def exe() -> str:
    """Absolute path to ourselves, always.

    hypridle runs with a minimal PATH, so a bare name in the generated config
    fails with exit 127 and every rung silently stops working.
    """
    for c in (os.environ.get("HYPRPOWER_EXE"),
              os.path.expanduser("~/.local/bin/hyprpower"),
              shutil.which("hyprpower")):
        if c and c.startswith("/") and os.path.exists(c):
            return c
    raise SystemExit("cannot locate an absolute path to hyprpower; "
                     "set HYPRPOWER_EXE")



def profile_path() -> Path:
    """$XDG_CONFIG_HOME/hyprpower/profile.toml.

    Yours, not the packaging's: anything home-manager generates is a
    read-only /nix/store symlink the TUI could never write. It may be a
    symlink into a dotfiles or NixOS repo, which is how the file stays
    versioned while staying editable.

    HYPRPOWER_POLICY overrides it, and root callers need that: acpid and the
    battery timer run with HOME=/root.
    """
    if env := os.environ.get("HYPRPOWER_POLICY"):
        return Path(env)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "hyprpower" / "profile.toml"


def default_path() -> Path:
    """Inside the package, not beside it: an installed copy has no repo
    checkout next to it, so a sibling config/ directory would make the
    first-run message name a file that does not exist."""
    return Path(__file__).resolve().parent / "default.toml"


def load() -> dict:
    f = profile_path()
    if not f.exists():
        raise SystemExit(f"no profile at {f}\n"
                         f"  mkdir -p {f.parent} && cp {default_path()} {f}")
    return policy.parse(f.read_text(), where=str(f))


def save(path: tuple[str, ...], value) -> None:
    """Write one setting back, preserving comments.

    tomlkit, not tomllib+dump: the profile carries the reasoning for most of
    its values and a plain TOML writer would delete every comment.
    """
    import tomlkit   # deferred: ~30ms, and only an edit ever needs it

    # resolve() so a symlinked profile is written THROUGH, not replaced: an
    # atomic rename onto the link would swap it for a regular file and edits
    # would silently stop reaching the versioned copy.
    file = profile_path().resolve()
    if str(file).startswith("/nix/store/"):
        raise SystemExit(
            f"{profile_path()} resolves into the read-only store ({file}).\n"
            f"  home.file generates store symlinks; use "
            f"config.lib.file.mkOutOfStoreSymlink, or a plain file.")
    # The temp file lands beside the target, so the DIRECTORY must be
    # writable, not just the file.
    if not os.access(file.parent, os.W_OK):
        raise SystemExit(f"cannot write {file.parent}; the profile resolves "
                         f"there and edits need it writable")
    doc = tomlkit.parse(file.read_text())
    node = doc
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    tmp = file.with_suffix(".toml.tmp")
    tmp.write_text(tomlkit.dumps(doc))
    tmp.replace(file)


def state_dir() -> Path:
    d = Path(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"))
    d = d / "hyprpower"
    d.mkdir(parents=True, exist_ok=True)
    return d


HYPRIDLE_CONF = lambda: state_dir() / "hypridle.conf"
LOGIND_PATH = Path("/etc/systemd/logind.conf.d/50-hyprpower.conf")
SLEEP_PATH = Path("/etc/systemd/sleep.conf.d/50-hyprpower.conf")



# Only the lid has a power-source variant; key handlers are single-valued,
# which is a logind limit rather than a configuration choice.
LOGIND = ("HandlePowerKey", "HandlePowerKeyLongPress", "HandleLidSwitch",
          "HandleLidSwitchExternalPower", "HandleLidSwitchDocked",
          "IdleAction", "IdleActionUSec", "InhibitDelayMaxUSec")


def logind() -> dict:
    """Ask the daemon, not the files: it merges drop-ins over the main config
    and compiles in defaults, so only it knows the effective value."""
    text = out("busctl", "get-property", "org.freedesktop.login1",
               "/org/freedesktop/login1", "org.freedesktop.login1.Manager", *LOGIND)
    result = {}
    for prop, line in zip(LOGIND, text.strip().splitlines()):
        kind, _, raw = line.strip().partition(" ")
        result[prop] = raw.strip().strip('"') if kind == "s" else int(raw) // 1_000_000
    return result


def pkg_version(binary: str) -> str | None:
    """Version from a resolved store path, when there is one.

    Not `hyprctl version`: that needs HYPRLAND_INSTANCE_SIGNATURE and so only
    works from inside the session. None off NixOS, where the path carries no
    version -- a blank cell, not a wrong one.
    """
    path = os.path.realpath(f"/run/current-system/sw/bin/{binary}")
    m = re.search(r"-([0-9][^/-]*)/bin/", path)
    return m.group(1) if m else None


def on_ac() -> bool:
    """Single cheap read, shared with the condition_cmd path, which runs on
    every listener timeout and must not pay for a full snapshot."""
    return bool(int(first("/sys/class/power_supply/A*/online")))


def source() -> str:
    return "ac" if on_ac() else "battery"


def unit_active(unit: str, user: bool = False) -> bool:
    """is-active exits non-zero for a stopped unit, so the return code cannot
    be used; parse the word."""
    cmd = ["systemctl"] + (["--user"] if user else []) + ["is-active", unit]
    text = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
    if text not in ("active", "inactive", "failed", "activating", "deactivating"):
        raise SystemExit(f"cannot determine state of {unit}: {text!r} - no user "
                         f"bus (XDG_RUNTIME_DIR is "
                         f"{os.environ.get('XDG_RUNTIME_DIR', 'unset')})"
                         if user else
                         f"cannot determine state of {unit}: {text!r}")
    return text == "active"


class Service(NamedTuple):
    unit: str
    binary: str | None      # None when there is no version to report
    user: bool              # a --user unit rather than a system one


SERVICES = (Service("hypridle", "hypridle", True),
            Service("tlp", "tlp", False),
            Service("upower", "upower", False),
            Service("acpid", "acpid", False),
            Service("thermald", "thermald", False),
            Service("hibernate-on-low-battery.timer", None, False))


def read(pol: dict) -> dict:
    """One snapshot: machine facts, live artifact contents, and what the
    policy WOULD render to.

    The pure modules take this as an argument, which is what keeps them
    pure -- an earlier version had the view fetch its own state by default
    and purity eroded from there.
    """
    bat, pan = battery(), panel()
    uwh = lambda n: int(slurp(f"{bat}/{n}")) / 1e6
    pick = lambda s: next(w.strip("[]") for w in s.split() if w.startswith("["))
    mem = slurp("/sys/power/mem_sleep")

    # tlp-stat prints the source file per setting, so defaults.conf vs
    # tlp.conf says what was actually chosen. TLP's naming also makes
    # power-source-dependent settings self-identifying: every X_ON_AC has an
    # X_ON_BAT sibling. Optional: TLP owns tunables hyprpower only reads, so
    # its absence is a smaller machine to describe, not an error.
    tlp_rows = (re.findall(r'^(\S+)\s+L\d+:\s*(\w+)="([^"]*)"',
                           out("tlp-stat", "-c"), re.M)
                if shutil.which("tlp-stat") else [])
    tlp_conf = {k: v for _, k, v in tlp_rows}

    radios = {}
    for line in out("rfkill", "-rn", "-o", "TYPE,SOFT,HARD").strip().splitlines():
        kind, soft, hard = line.split()
        radios[kind] = "blocked" if "blocked" in (soft, hard) else "on"

    conf = HYPRIDLE_CONF()
    return {
        "on_ac": on_ac(),
        "percent": int(slurp(f"{bat}/capacity")),
        "status": slurp(f"{bat}/status"),
        "cycles": int(slurp(f"{bat}/cycle_count")),
        "energy_full": uwh("energy_full"),
        "energy_design": uwh("energy_full_design"),
        "watts": uwh("power_now"),
        "bl_now": int(slurp(f"{pan}/brightness")),
        "bl_max": int(slurp(f"{pan}/max_brightness")),
        "suspend_mode": pick(mem),
        "suspend_modes": [w.strip("[]") for w in mem.split()],
        "hibernate_method": pick(slurp("/sys/power/disk")),
        "os": re.search(r'PRETTY_NAME="([^"]*)"', slurp("/etc/os-release")).group(1),
        "kernel": slurp("/proc/sys/kernel/osrelease"),
        "systemd": out("systemctl", "--version").split()[1],
        "services": [(sv.unit, unit_active(sv.unit, sv.user),
                      pkg_version(sv.binary) if sv.binary else None)
                     for sv in SERVICES],
        "hyprland_version": pkg_version("Hyprland"),
        "logind": logind(),
        "hypridle": unit_active("hypridle.service", user=True),
        "tlp_set": {k: v for s, k, v in tlp_rows if not s.endswith("defaults.conf")},
        "tlp_all": tlp_conf,
        "tlp_pairs": {k[:-6]: (v, tlp_conf.get(k[:-6] + "_ON_BAT"))
                      for k, v in tlp_conf.items() if k.endswith("_ON_AC")},
        "governor": first("/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor"),
        "charge_start": int(slurp(f"{bat}/charge_control_start_threshold")),
        "charge_stop": int(slurp(f"{bat}/charge_control_end_threshold")),
        "radios": radios,
        "live_hypridle": conf.read_text() if conf.exists() else None,
        # Rendered here, not in report: it needs our own path and the
        # resolved binaries, both of which are facts about this machine.
        "want_hypridle": policy.hypridle_conf(pol, exe=exe(), bins=bins()),
        "live_sleep": SLEEP_PATH.read_text() if SLEEP_PATH.exists() else None,
        "profile": str(profile_path()),
        "generated": str(conf),
    }



def apply_session(restart: bool = True) -> Path:
    """Write the hypridle config and point hypridle at it.

    The unit is declared with -c pointing here, so this only writes and
    restarts.
    """
    conf = HYPRIDLE_CONF()
    conf.write_text(policy.hypridle_conf(load(), exe=exe(), bins=bins()))
    if restart:
        run("systemctl", "--user", "restart", "hypridle.service")
    return conf


def apply_system(reload: bool = True) -> Path:
    """logind drop-in, sleep drop-in and charge thresholds. Requires root."""
    if os.geteuid() != 0:
        raise SystemExit("hyprpower apply --system must run as root "
                         "(writes /etc/systemd and battery sysfs)")
    pol = load()

    LOGIND_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOGIND_PATH.write_text(policy.LOGIND_DROPIN.format(**policy.logind_want(pol)))
    if reload:
        run("systemctl", "reload", "systemd-logind")

    if len(gaps := policy.delay_gaps(pol)) > 1:
        raise SystemExit(
            f"suspend->hibernate gaps differ by power source ({sorted(gaps)}s) "
            f"but HibernateDelaySec is one global value. Make the gaps equal, "
            f"or use hibernate on only one source.")
    if (want := policy.sleep_text(pol)) is None:
        SLEEP_PATH.unlink(missing_ok=True)
    else:
        SLEEP_PATH.parent.mkdir(parents=True, exist_ok=True)
        SLEEP_PATH.write_text(want)

    # TLP used to own these; it still reasserts its own values on service
    # restart, so it must no longer set them in tlp.conf.
    bat = battery()
    start = Path(f"{bat}/charge_control_start_threshold")
    Path(f"{bat}/charge_control_end_threshold").write_text(
        str(pol["battery"]["charge"]["stop"]))
    start.write_text(str(pol["battery"]["charge"]["start"]))
    # Nudge the EC. Observed 2026-09-27: after a correct write it sat at
    # "Not charging" for >12s at 72% with a 75% start threshold; rewriting
    # the same value started charging immediately. Without this a correctly
    # applied policy looks like it did nothing.
    start.write_text(str(pol["battery"]["charge"]["start"]))
    return LOGIND_PATH


def sudo_refresh() -> int:
    """Validate/cache the sudo credential up front, so applying later does
    not stop to ask."""
    return subprocess.run(["sudo", "-v"]).returncode


def sudo_apply_system() -> int:
    """Escalate for the system half. Returns sudo's exit code rather than
    raising: a declined password must not kill the TUI, but it must not be
    reported as applied either."""
    return subprocess.run(["sudo", exe(), "apply", "--system"]).returncode



def runtime_dir() -> Path:
    """One save location shared by BOTH privilege levels.

    hypridle's rungs run as the user; the low-battery handler runs as root.
    Separate files would let root save the already-dimmed value and
    "restore" you to 10%.
    """
    d = Path("/run/hyprpower")
    if not d.exists():
        d.mkdir()
        os.chmod(d, 0o1777)
    return d


def slot() -> Path:
    return runtime_dir() / "backlight"


def stored() -> str | None:
    """Saved brightness, or None. Empty means cleared, not missing:
    truncation is how the other uid releases the slot under a sticky dir."""
    if not slot().exists():
        return None
    return slot().read_text().strip() or None


def save_brightness() -> None:
    """First writer wins, so a later rung never clobbers the real value.

    We keep our own slot and never call `brightnessctl -s`: that flag has one
    saved value per device, so a dim followed by a backlight-off overwrote
    the real brightness and restored 10 instead of what you had.
    """
    if not stored():
        slot().write_text(slurp(f"{panel()}/brightness"))
        # 0666 because the two privilege levels share this file and the
        # directory is sticky, so whoever did NOT create it cannot unlink.
        os.chmod(slot(), 0o666)


def cmd_on(src: str) -> int:
    assert src in policy.SOURCES, src
    return 0 if (src == "ac") == on_ac() else 1


def cmd_do(action: str) -> int:
    pol = load()
    if action in (policy.Action.DIM, policy.Action.BACKLIGHT_OFF):
        save_brightness()
        run("brightnessctl", "--quiet", "set",
            pol["display"]["dim_to"] if action == policy.Action.DIM else "0")
    elif action == policy.Action.DISPLAY_OFF:
        # Opt-in only: a dpms-off listener crashed the whole Hyprland session
        # on 0.55.x (SIGABRT -> greetd relogin).
        run("hyprctl", "dispatch", "dpms", "off")
    elif action == policy.Action.LOCK:
        run("loginctl", "lock-session")
    elif action in policy.SYSTEMCTL:
        run("systemctl", policy.systemd_sleep(pol, source(), action))
    elif action == policy.Action.RESTORE:
        # Brightness first: it always applies and must not be blocked by
        # hyprctl failing. dpms only when the display-off rung is configured
        # at all -- that rung leaves the panel powered down, so restoring
        # brightness alone would wake you to a black screen.
        if value := stored():
            run("brightnessctl", "--quiet", "set", value)
            slot().write_text("")
        if any(pol["idle"]["display_off"][s] for s in policy.SOURCES):
            run("hyprctl", "dispatch", "dpms", "on")
    else:
        raise SystemExit(f"unknown action: {action}")
    return 0



# One press emits two ACPI events on this hardware (PBTN and PWRF), so
# without this the action runs twice and the second gets "suspend already in
# progress". Observed 2026-09-26.
DEBOUNCE_SECONDS = 3

# Actions a hardware event can trigger that are not handed to systemctl.
EVENT_ACTIONS = {policy.Action.LOCK: ["loginctl", "lock-sessions"],
                 policy.Action.IGNORE: None}


def latch() -> Path:
    return runtime_dir() / "low-battery"


def debounced(name: str, secs: int = DEBOUNCE_SECONDS) -> bool:
    stamp = runtime_dir() / f"{name}.stamp"
    now = time.time()
    if stamp.exists() and now - stamp.stat().st_mtime < secs:
        return True
    stamp.write_text(str(now))
    return False


def run_action(pol: dict, name: str) -> None:
    if name in policy.SYSTEMCTL:
        run("systemctl", policy.systemd_sleep(pol, source(), name))
    elif name in EVENT_ACTIONS:
        if cmd := EVENT_ACTIONS[name]:   # "ignore" maps to None on purpose
            run(*cmd)
    else:
        raise SystemExit(f"unknown action in profile: {name}")


def ev_power() -> int:
    if debounced("powerkey"):
        return 0
    pol = load()
    run_action(pol, pol["button"]["power"][source()])
    return 0


def ev_charge() -> int:
    """The low-battery ladder, over descending charge."""
    low = load()["battery"]["low"]
    cap = int(slurp(f"{battery()}/capacity"))
    if on_ac() or cap > low["backlight_off"]:
        if latch().exists():
            latch().unlink()
            cmd_do("restore")
        return 0
    if cap <= low["hibernate"]:
        # The one deliberate fallback here: a refused hibernate must not
        # leave a nearly-flat battery awake.
        if subprocess.run(["systemctl", "hibernate"], check=False).returncode:
            run("systemctl", "suspend")
        return 0
    if not latch().exists():
        latch().touch()
        run("loginctl", "lock-sessions")
        cmd_do("backlight-off")
    return 0

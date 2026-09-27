"""The declared model, and how it compiles.

PURE. Nothing here reads the machine, runs a process or touches a file.
Everything it needs arrives as an argument, so the same policy always
compiles to the same bytes -- which is what lets `verify` compare a rendered
config against a live one and trust the answer.

The policy is the source of truth; the daemons are dispatchers. hypridle is
a timer that calls back into `hyprpower on <source>` to decide whether a
rung applies and `hyprpower do <action>` to perform it.
"""

from __future__ import annotations

import re
import tomllib

SOURCES = ("ac", "battery")

# (policy key, emitted action, does activity undo it)
STEPS = [("dim", "dim", True),
         ("backlight_off", "backlight-off", True),
         ("display_off", "display-off", True),
         ("lock", "lock", False),
         ("suspend", "suspend", False),
         ("hibernate", "hibernate", False)]

ROW = {action: f"idle.{key}" for key, action, _ in STEPS}

# Does the machine survive the battery running out in it, on its own.
SLEEP = {"suspend": False, "hibernate": True}

# systemd's names for our actions. The ONLY place systemd vocabulary appears:
# policy says suspend / hibernate / shutdown and nothing else.
SYSTEMD = {"shutdown": "poweroff"}

# Every key that must be present. hyprpower holds no values of its own, so a
# missing one is an error that names itself rather than a KeyError mid-render.
REQUIRED = (
    [("display", "dim_to"), ("lock", "unit"), ("lock", "restart_on_resume"),
     ("lid", "docked"), ("lid", "ac"), ("lid", "battery"),
     ("battery", "charge", "start"), ("battery", "charge", "stop"),
     ("battery", "low", "backlight_off"), ("battery", "low", "hibernate"),
     ("battery", "low", "poll")]
    + [("idle", k, s) for k, _, _ in STEPS for s in SOURCES]
    + [("button", b, s) for b in ("power", "power_held") for s in SOURCES]
)

_MISSING = object()


def _get(pol: dict, keys):
    node = pol
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return _MISSING
        node = node[k]
    return node


def parse(text: str, where: str = "profile") -> dict:
    pol = tomllib.loads(text)
    missing = [".".join(p) for p in REQUIRED if _get(pol, p) is _MISSING]
    if missing:
        raise SystemExit(f"{where} is incomplete; add:\n  " + "\n  ".join(missing))
    return pol


def seconds(v) -> int | None:
    """'2m30s' -> 150.  false -> None (never)."""
    if v is False or v is None:
        return None
    if isinstance(v, int):
        return v
    total = 0
    for n, unit in re.findall(r"(\d+)\s*([hms])", v):
        total += int(n) * {"h": 3600, "m": 60, "s": 1}[unit]
    if not total:
        raise ValueError(f"unparseable duration: {v!r}")
    return total


def escalates(pol: dict, source: str) -> bool:
    """Does a suspend on this source go on to reach disk by itself."""
    s, h = (seconds(pol["idle"][k][source]) for k in ("suspend", "hibernate"))
    return s is not None and h is not None and h > s


def systemd_sleep(pol: dict, source: str, action: str) -> str:
    """systemd's name for an action, never policy's."""
    if action == "suspend" and escalates(pol, source):
        return "suspend-then-hibernate"
    return SYSTEMD.get(action, action)


def rungs(pol: dict) -> list[tuple[int, str, str, bool]]:
    """The listeners to emit, as (seconds, source, action, restores).

    A hibernate scheduled after a suspend on the same source is dropped:
    nothing in userspace runs while asleep, so it could never fire. The
    escalation is systemd's job, arranged by `escalates`.
    """
    out = []
    for source in SOURCES:
        at = {key: seconds(pol["idle"][key][source]) for key, _a, _r in STEPS}
        for key, action, restores in STEPS:
            if at[key] is None or (key == "hibernate" and escalates(pol, source)):
                continue
            out.append((at[key], source, action, restores))
    return sorted(out)


def collisions(pol: dict) -> list[tuple[int, str, list[str]]]:
    """Steps firing at the same moment on the same source.

    Not an error: both run and the end state is the same either way. But
    hypridle decides the order, so it is undefined, and undefined is worth
    saying out loud.
    """
    seen: dict[tuple[int, str], list[str]] = {}
    for after, source, action, _ in rungs(pol):
        seen.setdefault((after, source), []).append(action)
    return [(after, source, acts)
            for (after, source), acts in sorted(seen.items()) if len(acts) > 1]


def delay_gaps(pol: dict) -> set[int]:
    """Distinct suspend->hibernate gaps across the power sources.

    More than one is unrepresentable: HibernateDelaySec is a single global
    systemd setting. Returned as a set rather than asserted, so the TUI can
    open and show the conflict -- an assert here made the tool unusable,
    including the screen you would fix it on.
    """
    return {seconds(pol["idle"]["hibernate"][s]) - seconds(pol["idle"]["suspend"][s])
            for s in SOURCES if escalates(pol, s)}


def hibernate_delay(pol: dict) -> int | None:
    """None if unset OR in conflict; writers must check delay_gaps first."""
    gaps = delay_gaps(pol)
    return gaps.pop() if len(gaps) == 1 else None


# --- compiled artifacts ----------------------------------------------------

def general(pol: dict, bins: dict) -> str:
    """The hypridle `general` block.

    `bins` is passed in rather than looked up: hypridle runs with a minimal
    PATH where a bare name exits 127 and every rung silently stops, so these
    must be absolute -- and resolving them here would make the output depend
    on who called it, which broke `verify` once already.
    """
    resume = [f"{bins['hyprctl']} dispatch dpms on"]
    if pol["lock"]["restart_on_resume"]:
        resume.append(f"{bins['systemctl']} --user try-restart {pol['lock']['unit']}")
    return (f"general {{\n"
            f"    lock_cmd = {bins['systemctl']} --user start {pol['lock']['unit']}\n"
            f"    before_sleep_cmd = {bins['loginctl']} lock-session\n"
            f"    after_sleep_cmd = {'; '.join(resume)}\n"
            f"}}\n")


def hypridle_conf(pol: dict, exe: str, bins: dict) -> str:
    """One listener per step per power source, gated on the source.

    condition_cmd runs at timeout and defers on non-zero; condition_retry
    re-checks while still idle, so unplugging mid-idle lands on the battery
    rung without any extra daemon watching for the transition.
    """
    out = ["# GENERATED by hyprpower from profile.toml - do not edit.",
           "# Edit the profile and run `hyprpower apply`.",
           "", general(pol, bins)]
    for after, source, action, restores in rungs(pol):
        lines = ["listener {",
                 f"    timeout = {after}",
                 f"    condition_cmd = {exe} on {source}",
                 f"    condition_retry = 30",
                 f"    on-timeout = {exe} do {action}"]
        if restores:
            lines.append(f"    on-resume = {exe} do restore")
        out.append("\n".join(lines) + "\n}\n")
    return "\n".join(out)


LOGIND_DROPIN = """# GENERATED by hyprpower from profile.toml - do not edit.
# Drop-ins override the main logind.conf, so this wins over whatever the
# distro generated. HandlePowerKey is 'ignore' because logind has no
# power-source variant for keys; acpid routes the press to
# `hyprpower event power`, which reads [button.power] and can differ.
[Login]
HandleLidSwitchExternalPower={HandleLidSwitchExternalPower}
HandleLidSwitch={HandleLidSwitch}
HandleLidSwitchDocked={HandleLidSwitchDocked}
HandlePowerKey=ignore
"""

SLEEP_DROPIN = """# GENERATED by hyprpower from profile.toml - do not edit.
# The gap between [idle.suspend] and [idle.hibernate].
[Sleep]
HibernateDelaySec={delay}
"""


def logind_want(pol: dict) -> dict[str, str]:
    """logind property -> intended value, in systemd's vocabulary.

    Shared by the writer and the checker so the lid cannot be written
    translated and compared untranslated.
    """
    return {
        "HandleLidSwitchExternalPower": systemd_sleep(pol, "ac", pol["lid"]["ac"]),
        "HandleLidSwitch": systemd_sleep(pol, "battery", pol["lid"]["battery"]),
        # docked is orthogonal to the power source, so no escalation applies.
        "HandleLidSwitchDocked": SYSTEMD.get(pol["lid"]["docked"], pol["lid"]["docked"]),
    }


def sleep_text(pol: dict) -> str | None:
    """None when there is nothing to write, INCLUDING an unresolvable
    conflict. Must not raise: the flags call this, and throwing took down
    the very screen you would fix the conflict on."""
    delay = hibernate_delay(pol)
    return None if delay is None else SLEEP_DROPIN.format(delay=delay)


def parse_generated(text: str) -> dict[tuple[str, str], int]:
    """Read back a config WE generated: (source, action) -> seconds.

    Safe because we author the format. Deliberately not a general hypridle
    parser -- an earlier version tried that and broke the moment the
    commands changed.
    """
    out = {}
    for block in re.findall(r"listener\s*\{(.*?)\}", text, re.S):
        m_t = re.search(r"timeout\s*=\s*(\d+)", block)
        m_a = re.search(r"on-timeout\s*=.*?\bdo\s+([\w-]+)", block)
        m_s = re.search(r"condition_cmd\s*=.*?\bon\s+(ac|battery)\b", block)
        assert m_t and m_a and m_s, block
        out[(m_s.group(1), m_a.group(1))] = int(m_t.group(1))
    return out

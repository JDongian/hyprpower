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
from enum import StrEnum
from typing import NamedTuple

class Source(StrEnum):
    """The condition every setting splits on. StrEnum so these ARE the keys
    used in profile.toml and no conversion is needed at the boundary."""
    AC = "ac"
    BATTERY = "battery"


SOURCES = tuple(Source)


class Action(StrEnum):
    """The whole vocabulary. Nothing outside this list is a valid action, in
    the profile or on the `hyprpower do` command line."""
    IGNORE = "ignore"
    DIM = "dim"
    BACKLIGHT_OFF = "backlight-off"
    DISPLAY_OFF = "display-off"
    RESTORE = "restore"
    LOCK = "lock"
    SUSPEND = "suspend"          # to RAM
    HIBERNATE = "hibernate"      # to disk
    SHUTDOWN = "shutdown"


# Actions that put the machine to sleep, and the subset that survives the
# battery running out while it is there.
SLEEPS = frozenset({Action.SUSPEND, Action.HIBERNATE})
REACHES_DISK = frozenset({Action.HIBERNATE})

# Dispatched by handing the name to systemctl.
SYSTEMCTL = SLEEPS | {Action.SHUTDOWN}

# The only place systemd vocabulary appears.
SYSTEMD = {Action.SHUTDOWN: "poweroff"}


class Rung(NamedTuple):
    """One emitted hypridle listener."""
    after: int          # seconds of idle
    source: Source
    action: Action
    restores: bool


class Step(NamedTuple):
    key: str            # the [idle.<key>] section it is configured under
    action: Action
    restores: bool      # activity undoes it, so the listener gets on-resume


STEPS = (Step("dim", Action.DIM, True),
         Step("backlight_off", Action.BACKLIGHT_OFF, True),
         Step("display_off", Action.DISPLAY_OFF, True),
         Step("lock", Action.LOCK, False),
         Step("suspend", Action.SUSPEND, False),
         Step("hibernate", Action.HIBERNATE, False))

ROW = {s.action: f"idle.{s.key}" for s in STEPS}

UNIT = {"h": 3600, "m": 60, "s": 1}
_TOKEN = re.compile(r"(\d+)\s*([hms])\s*")


def seconds(v) -> int | None:
    """'2m30s' -> 150.  false -> None (never).

    fullmatch, not a scan. Scanning accepted anything with a token buried in
    it: '1.5h' matched the '5h' and silently meant five hours, '-5m' dropped
    the sign, and 'abc10m' passed. A bool is excluded because it is an int in
    Python, so True would otherwise mean one second.
    """
    if v is False or v is None:
        return None
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    if not isinstance(v, str) or not re.fullmatch(f"(?:{_TOKEN.pattern})+", v):
        raise ValueError(f"unparseable duration: {v!r}")
    return sum(int(n) * UNIT[u] for n, u in _TOKEN.findall(v))


# What a trigger may be told to do. dim / backlight-off / restore are rungs
# of the idle ladder, not things a lid or a button can be set to.
TRIGGERABLE = SYSTEMCTL | {Action.LOCK, Action.IGNORE}

# Every key, and the kind of value it takes. hyprpower holds no values of its
# own, so a missing key is an error -- and so is a value of the wrong shape,
# which used to travel until something deep in rendering choked on it.
SPAN, LEVEL, DO, TEXT, FLAG = "duration", "level", "action", "text", "flag"

SCHEMA = {
    ("display", "dim_to"): TEXT,
    ("lock", "unit"): TEXT,
    ("lock", "restart_on_resume"): FLAG,
    ("lid", "docked"): DO,
    ("lid", "ac"): DO,
    ("lid", "battery"): DO,
    ("battery", "charge", "start"): LEVEL,
    ("battery", "charge", "stop"): LEVEL,
    ("battery", "low", "dim"): LEVEL,
    ("battery", "low", "backlight_off"): LEVEL,
    ("battery", "low", "hibernate"): LEVEL,
    ("battery", "low", "poll"): SPAN,
    **{("idle", st.key, src): SPAN for st in STEPS for src in SOURCES},
    **{("button", b, src): DO for b in ("power", "power_held") for src in SOURCES},
}


def check(kind: str, value) -> None:
    """Raise ValueError if the value is not what this key takes."""
    if kind == SPAN:
        seconds(value)
    elif kind == LEVEL:
        if value is not False and not (isinstance(value, int)
                                       and not isinstance(value, bool)
                                       and 0 <= value <= 100):
            raise ValueError("expected a percentage 0-100, or false")
    elif kind == DO:
        if value not in TRIGGERABLE:
            raise ValueError("expected one of " +
                             ", ".join(sorted(str(a) for a in TRIGGERABLE)))
    elif kind == FLAG:
        if not isinstance(value, bool):
            raise ValueError("expected true or false")
    elif kind == TEXT:
        if not isinstance(value, str) or not value:
            raise ValueError("expected a non-empty string")


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
    missing, bad = [], []
    for keys, kind in SCHEMA.items():
        value = _get(pol, keys)
        if value is _MISSING:
            missing.append(".".join(keys))
            continue
        try:
            check(kind, value)
        except ValueError as e:
            bad.append(f"{'.'.join(keys)} = {value!r}: {e}")
    if missing:
        raise SystemExit(f"{where} is incomplete; add:\n  " + "\n  ".join(missing))
    if bad:
        raise SystemExit(f"{where} has bad values:\n  " + "\n  ".join(bad))
    return pol


def escalates(pol: dict, source: str) -> bool:
    """Does a suspend on this source go on to reach disk by itself."""
    s, h = (seconds(pol["idle"][k][source]) for k in ("suspend", "hibernate"))
    return s is not None and h is not None and h > s


def systemd_sleep(pol: dict, source: str, action: str) -> str:
    """systemd's name for an action, never policy's."""
    if action == Action.SUSPEND and escalates(pol, source):
        return "suspend-then-hibernate"
    return SYSTEMD.get(action, action)


def rungs(pol: dict) -> list[Rung]:
    """The listeners to emit, as (seconds, source, action, restores).

    A hibernate scheduled after a suspend on the same source is dropped:
    nothing in userspace runs while asleep, so it could never fire. The
    escalation is systemd's job, arranged by `escalates`.
    """
    out = []
    for source in SOURCES:
        at = {s.key: seconds(pol["idle"][s.key][source]) for s in STEPS}
        for st in STEPS:
            if at[st.key] is None or (st.action is Action.HIBERNATE
                                      and escalates(pol, source)):
                continue
            out.append(Rung(at[st.key], source, st.action, st.restores))
    return sorted(out)


def collisions(pol: dict) -> list[tuple[int, str, list[str]]]:
    """Steps firing at the same moment on the same source.

    Not an error: both run and the end state is the same either way. But
    hypridle decides the order, so it is undefined, and undefined is worth
    saying out loud.
    """
    seen: dict[tuple[int, str], list[str]] = {}
    for r in rungs(pol):
        seen.setdefault((r.after, r.source), []).append(r.action)
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
    for r in rungs(pol):
        lines = ["listener {",
                 f"    timeout = {r.after}",
                 f"    condition_cmd = {exe} on {r.source}",
                 f"    condition_retry = 30",
                 f"    on-timeout = {exe} do {r.action}"]
        if r.restores:
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
        "HandleLidSwitchDocked": SYSTEMD.get(pol["lid"]["docked"],
                                             pol["lid"]["docked"]),
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

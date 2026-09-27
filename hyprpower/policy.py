"""Load policy.toml and render what the daemons read.

The policy is the source of truth; this turns it into a hypridle config.
hypridle becomes a timer and hyprpower is the policy engine: every listener
calls back into `hyprpower on <source>` to decide whether it applies, and
`hyprpower do <action>` to act.
"""

from __future__ import annotations

import os
import re
import shutil
import tomllib
from pathlib import Path

SOURCES = ("ac", "battery")

# Display steps restore on activity; lock and suspend do not.
STEPS = [("dim", "dim", True),
         ("backlight_off", "backlight-off", True),
         ("display_off", "display-off", True),
         ("lock", "lock", False),
         ("suspend", "suspend", False),
         ("hibernate", "hibernate", False)]

ROW = {"dim": "idle.dim", "backlight-off": "idle.backlight_off",
       "display-off": "idle.display_off", "lock": "idle.lock",
       "suspend": "idle.suspend", "hibernate": "idle.hibernate"}

# Does the machine survive the battery running out in it, on its own.
SLEEP = {"suspend": False, "hibernate": True}

# systemd's names for our actions. This is the ONLY place systemd vocabulary
# appears: policy says suspend / hibernate / shutdown and nothing else.
SYSTEMD = {"shutdown": "poweroff"}

def general(pol: dict) -> str:
    """The hypridle `general` block.

    Binaries are resolved to absolute paths at render time: hypridle runs as
    a user service with a minimal PATH, where a bare name exits 127 and every
    rung silently stops working. shutil.which rather than a hard-coded prefix
    so this is not NixOS-only.
    """
    loginctl, hyprctl, systemctl = (which(b) for b in
                                    ("loginctl", "hyprctl", "systemctl"))
    resume = [f"{hyprctl} dispatch dpms on"]
    if pol["lock"]["restart_on_resume"]:
        resume.append(f"{systemctl} --user try-restart {pol['lock']['unit']}")
    return (f"general {{\n"
            f"    lock_cmd = {systemctl} --user start {pol['lock']['unit']}\n"
            f"    before_sleep_cmd = {loginctl} lock-session\n"
            f"    after_sleep_cmd = {'; '.join(resume)}\n"
            f"}}\n")


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


def path() -> Path:
    """The active profile: $XDG_CONFIG_HOME/hyprpower/profile.toml.

    Owned by you, not by the packaging. home-manager generating it would make
    it a read-only /nix/store symlink that the TUI could never write, which is
    the whole reason it is not managed declaratively.

    HYPRPOWER_POLICY overrides it, and root callers need that: acpid and the
    battery timer run with HOME=/root and cannot find your config otherwise.
    """
    if env := os.environ.get("HYPRPOWER_POLICY"):
        return Path(env)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "hyprpower" / "profile.toml"


def default_path() -> Path:
    return Path(__file__).resolve().parent.parent / "config" / "default.toml"


def load(p: Path | None = None) -> dict:
    f = p or path()
    if not f.exists():
        raise SystemExit(f"no profile at {f}\n"
                         f"  mkdir -p {f.parent} && cp {default_path()} {f}")
    return tomllib.loads(f.read_text())


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


def escalates(pol: dict, source: str) -> bool:
    """Does a suspend on this source go on to reach disk by itself."""
    s, h = (seconds(pol["idle"][k][source]) for k in ("suspend", "hibernate"))
    return s is not None and h is not None and h > s


def systemd_sleep(pol: dict, source: str, action: str) -> str:
    """systemd's name for an action, never policy's."""
    if action == "suspend" and escalates(pol, source):
        return "suspend-then-hibernate"
    return SYSTEMD.get(action, action)


def hibernate_delay(pol: dict) -> int | None:
    """Seconds to stay suspended before hibernating, or None."""
    gaps = {seconds(pol["idle"]["hibernate"][s]) - seconds(pol["idle"]["suspend"][s])
            for s in SOURCES
            if seconds(pol["idle"]["suspend"][s]) is not None
            and seconds(pol["idle"]["hibernate"][s]) is not None
            and seconds(pol["idle"]["hibernate"][s]) > seconds(pol["idle"]["suspend"][s])}
    assert len(gaps) <= 1, f"HibernateDelaySec is one global value, got {gaps}"
    return gaps.pop() if gaps else None


def render_hypridle(pol: dict, exe: str = "hyprpower") -> str:
    """One listener per step per power source, gated on the source.

    condition_cmd runs at timeout and defers on non-zero; condition_retry
    re-checks while still idle, so unplugging mid-idle lands on the battery
    rung without any extra daemon watching for the transition.
    """
    out = [
        "# GENERATED by hyprpower from policy.toml — do not edit.",
        "# Edit the policy and run `hyprpower apply`.",
        "",
        general(pol),
    ]
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


def collisions(pol: dict) -> list[tuple[int, str, list[str]]]:
    """Steps that fire at the same moment on the same power source.

    Not an error and not something to work around: both actions run and the
    end state is the same either way. But hypridle decides the order, so it
    is undefined, and undefined is worth saying out loud.
    """
    seen: dict[tuple[int, str], list[str]] = {}
    for after, source, action, _ in rungs(pol):
        seen.setdefault((after, source), []).append(action)
    return [(after, source, acts) for (after, source), acts in sorted(seen.items())
            if len(acts) > 1]


def parse_generated(text: str) -> dict[tuple[str, str], int]:
    """Read back a config WE generated: (source, action) -> seconds.

    Safe to parse because we author the format. This is deliberately not a
    general hypridle parser -- an earlier version tried that and broke the
    moment the commands changed.
    """
    out = {}
    for block in re.findall(r"listener\s*\{(.*?)\}", text, re.S):
        m_t = re.search(r"timeout\s*=\s*(\d+)", block)
        m_a = re.search(r"on-timeout\s*=.*?\bdo\s+([\w-]+)", block)
        m_s = re.search(r"condition_cmd\s*=.*?\bon\s+(ac|battery)\b", block)
        assert m_t and m_a and m_s, block
        out[(m_s.group(1), m_a.group(1))] = int(m_t.group(1))
    return out

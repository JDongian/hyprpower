"""Write a single setting back into policy.toml.

tomlkit, not tomllib+dump: policy.toml carries the reasoning for most of its
values and a round-trip through a plain TOML writer would delete every
comment in the file.
"""

from __future__ import annotations

import tomlkit

from . import policy

# Column index is the power source: 0 = plugged in, 1 = on battery.
EDITABLE: dict[tuple[str, int], tuple[str, ...]] = {
    **{(f"idle.{k}", i): ("idle", k, src)
       for k in ("dim", "backlight_off", "display_off", "lock", "suspend",
                 "hibernate")
       for i, src in enumerate(("ac", "battery"))},
    ("lid.close", 0): ("lid", "ac"),
    ("lid.close", 1): ("lid", "battery"),
    ("lid.docked", 0): ("lid", "docked"),
    ("key.power", 0): ("button", "power", "ac"),
    ("key.power", 1): ("button", "power", "battery"),
    ("key.power_held", 0): ("button", "power_held", "ac"),
    ("key.power_held", 1): ("button", "power_held", "battery"),
    ("chg.start", 0): ("battery", "charge", "start"),
    ("chg.stop", 0): ("battery", "charge", "stop"),
    ("low.screen_off", 0): ("battery", "low", "backlight_off"),
    ("low.hibernate", 0): ("battery", "low", "hibernate"),
    ("low.poll", 0): ("battery", "low", "poll"),
}


def coerce(raw: str):
    """'never'/'false' -> False, digits -> int, else the string as given."""
    text = raw.strip()
    if text.lower() in ("never", "false", "off", "none"):
        return False
    if text.isdigit():
        return int(text)
    return text


def current(path: tuple[str, ...]):
    node = policy.load()
    for key in path:
        node = node[key]
    return node


def write(path: tuple[str, ...], value) -> None:
    # resolve() first: policy.toml is a mkOutOfStoreSymlink into the git repo,
    # and an atomic rename onto the LINK replaces the link with a regular
    # file -- edits would stop reaching the repo and home-manager would
    # later refuse to manage the path. Write the target instead.
    file = policy.path().resolve()
    doc = tomlkit.parse(file.read_text())
    node = doc
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    tmp = file.with_suffix(".toml.tmp")
    tmp.write_text(tomlkit.dumps(doc))
    tmp.replace(file)

"""Entry point.

Imports are lazy on purpose: `on` and `do` run on every listener timeout and
every condition_retry, so they must not pay for importing textual.
"""

from __future__ import annotations

import sys


def main() -> None:
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "tui"

    if cmd == "on":
        from .act import cmd_on
        raise SystemExit(cmd_on(argv[1]))
    if cmd == "do":
        from .act import cmd_do
        raise SystemExit(cmd_do(argv[1]))
    if cmd == "event":
        from .event import ev_charge, ev_power
        raise SystemExit({"power": ev_power, "charge": ev_charge}[argv[1]]())
    if cmd == "apply" and "--system" in argv:
        from .apply import apply_system
        print("wrote", apply_system())
        return
    if cmd == "apply":
        # --no-restart is for hypridle's own ExecStartPre: regenerate the
        # config, but do not restart the unit that is currently starting.
        from .apply import apply_session
        print("wrote", apply_session(restart="--no-restart" not in argv))
        return
    if cmd == "render":
        from . import policy
        print(policy.render_hypridle(policy.load()))
        return
    if cmd in ("verify", "check"):
        from .verify import problems
        found = problems()
        print("\n".join(found) if found else "system matches policy.toml")
        raise SystemExit(1 if found else 0)
    if cmd == "tui":
        from .tui import main as tui_main
        tui_main()
        return
    raise SystemExit("usage: thinkpower [tui|capture|render|apply [--system]|verify|"
                 "on <ac|battery>|do <action>|event <power|charge>]")

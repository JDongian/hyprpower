"""Entry point.

Imports are lazy on purpose: `on` and `do` run on every listener timeout and
every condition_retry, so they must not pay for importing textual.
"""

from __future__ import annotations

import sys

USAGE = ("usage: hyprpower [tui | apply [--system|--no-restart] | verify | "
         "on <ac|battery> | do <action> | event <power|charge>]")


def main() -> None:
    argv = sys.argv[1:]
    cmd, args = (argv[0], argv[1:]) if argv else ("tui", [])

    if cmd == "on":
        from .act import cmd_on
        raise SystemExit(cmd_on(args[0]))
    if cmd == "do":
        from .act import cmd_do
        raise SystemExit(cmd_do(args[0]))
    if cmd == "event":
        from .event import ev_charge, ev_power
        raise SystemExit({"power": ev_power, "charge": ev_charge}[args[0]]())
    if cmd == "apply":
        # --no-restart is for hypridle's own ExecStartPre: regenerate the
        # config, but do not restart the unit that is currently starting.
        from .apply import apply_session, apply_system
        print("wrote", apply_system() if "--system" in args
              else apply_session(restart="--no-restart" not in args))
        return
    if cmd == "verify":
        from .verify import problems
        found = problems()
        print("\n".join(found) if found else "system matches policy.toml")
        raise SystemExit(1 if found else 0)
    if cmd == "tui":
        from .tui import main as tui_main
        return tui_main()
    raise SystemExit(USAGE)

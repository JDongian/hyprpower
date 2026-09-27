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

    from . import system

    if cmd == "on":
        raise SystemExit(system.cmd_on(args[0]))
    if cmd == "do":
        raise SystemExit(system.cmd_do(args[0]))
    if cmd == "event":
        raise SystemExit({"power": system.ev_power,
                          "charge": system.ev_charge}[args[0]]())
    if cmd == "apply":
        # --no-restart is for hypridle's own ExecStartPre: regenerate the
        # config, but do not restart the unit that is currently starting.
        print("wrote", system.apply_system() if "--system" in args
              else system.apply_session(restart="--no-restart" not in args))
        return
    if cmd == "verify":
        from . import report
        pol = system.load()
        found = report.problems(pol, system.read(pol))
        print("\n".join(found) if found else "system matches policy.toml")
        raise SystemExit(1 if found else 0)
    if cmd == "tui":
        from .tui import main as tui_main
        return tui_main()
    raise SystemExit(USAGE)

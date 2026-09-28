"""Four tabs over the policy and the machine it runs on.

Selecting a flag highlights every row that produced it, across all tabs.
The flags already carry their dependency row ids, so this is presentation
only -- no extra data model.

Rows are styled in place rather than recomposed: build() shells out to
busctl, tlp-stat and rfkill, so rebuilding on each keypress would re-probe
the whole machine.
"""

from __future__ import annotations

import os
import sys

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.widgets import (DataTable, Footer, Input, Label, ListItem,
                             ListView, Static, TabbedContent, TabPane)

from textual.screen import ModalScreen

from . import system
from .report import EDITABLE, build, coerce, current, pending, problems

HL = "bold black on yellow"


class ConfirmApply(ModalScreen[bool]):
    """GParted-style: list exactly what will change, then confirm."""

    BINDINGS = [("y", "ok", "apply"), ("n", "cancel", "cancel"),
                ("escape", "cancel", "cancel")]
    CSS = """
    ConfirmApply { align: center middle; }
    #box { width: 84; height: auto; border: thick $warning; padding: 1 2;
           background: $surface; }
    """

    def __init__(self, changes: dict, problems: list[str]) -> None:
        super().__init__()
        self.changes = changes
        self.problems = problems

    def compose(self) -> ComposeResult:
        lines = [Text("Apply these changes?\n", style="bold")]
        for p in self.changes.values():
            line = Text(f"  {p.row:24} {p.have}  ->  ", style="")
            line.append(str(p.want), style="bold")
            if p.needs_root:
                line.append("   (needs root)", style="dim")
            lines.append(line)
        if not self.changes:
            for problem in self.problems:
                lines.append(Text(f"  {problem}", style="dim"))
        body = Text("\n").join(lines)
        body.append("\n\n  y = apply    n = cancel", style="dim")
        yield Static(body, id="box")

    def action_ok(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class EditValue(ModalScreen):
    """Prompt for one value. Writing goes to policy.toml, not the system --
    applying is a separate, confirmed step."""

    BINDINGS = [("escape", "cancel", "cancel")]
    CSS = """
    EditValue { align: center middle; }
    #editbox { width: 70; height: auto; border: thick $accent;
               padding: 1 2; background: $surface; }
    """

    def __init__(self, path, value) -> None:
        super().__init__()
        self.path, self.value = path, value

    def compose(self) -> ComposeResult:
        with Vertical(id="editbox"):
            yield Static(Text(".".join(self.path), style="bold"))
            yield Static(Text("enter = save to the profile, escape = cancel\n"
                              "'never' for no action", style="dim"))
            yield Input(value=str(self.value), id="value")

    def on_mount(self) -> None:
        self.query_one("#value", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss((self.path, event.value))

    def action_cancel(self) -> None:
        self.dismiss(None)


class HyprPower(App):
    CSS = """
    Screen { layout: vertical; }
    /* The flags block sizes to content; the tabs take whatever is left.
       Without 1fr here TabbedContent claimed the whole screen, the layout
       overflowed, and every DataTable inside collapsed to height 0 -- the
       tables were populated but invisible. */
    #flags { height: auto; max-height: 12; margin: 0 0 1 0; border: round $panel; }
    #main { height: 1fr; }
    TabPane { height: auto; }
    DataTable { height: auto; margin-bottom: 1; }
    .sub { color: $text-muted; text-style: italic; margin: 1 0 0 1; }
    #flags > ListItem { padding: 0 1; }
    """
    BINDINGS = [("q", "quit", "quit"), ("space", "toggle", "select flag"),
                ("r", "reload", "reload"), ("a", "apply", "apply changes"),
                ("e", "edit", "edit value"), ("tab", "focus_next", "next pane")]

    def __init__(self) -> None:
        super().__init__()
        self._snapshot()
        # One entry per place a row is drawn.
        self._rows: dict[str, list[tuple[DataTable, str, str]]] = {}
        self._labels: list[Label] = []
        # Remembered across recompose: an edit that dumped you back on tab 1
        # made editing several values in a row miserable.
        self._active_tab: str | None = None
        # So selecting a flag can mark the tabs holding its evidence.
        self._row_tabs: dict[str, set[str]] = {}
        self._titles: dict[str, str] = {}
        self._selected: int | None = None

    def _snapshot(self) -> None:
        """One policy load and one machine read per refresh. Everything
        below is computed from them, so the screen cannot show two
        different moments."""
        self.pol = system.load()
        self.st = system.read(self.pol)
        self.panels, self.flags, self.shown = build(self.pol, self.st)
        # Marks modified rows GParted-style.
        self.pending = {p.row: p for p in pending(self.pol, self.st)}
        self.problems = problems(self.pol, self.st)

    def compose(self) -> ComposeResult:
        # Flags sit above the tabs, not inside one: a flag's evidence is
        # spread across tabs, so it is not the property of any single one.
        yield Static("FLAGS — space to select, highlights its rows and tabs",
                     classes="sub")
        self._labels = [Label(self._flag_text(i)) for i in range(len(self.flags))]
        yield ListView(*[ListItem(w) for w in self._labels], id="flags")
        # `initial` rather than setting .active after recompose: a post-hoc
        # assignment runs before the new panes exist and is silently lost.
        with TabbedContent(id="main", initial=self._active_tab or "tab0"):
            for n, (title, sheets) in enumerate(self.panels):
                tab_id = f"tab{n}"
                self._titles[tab_id] = title
                with TabPane(title, id=tab_id):
                    with VerticalScroll():
                        yield from self._sheets(sheets, tab_id)
        yield Footer()

    def _flag_text(self, i: int) -> Text:
        """Marker, sentence, then the values that produced it.

        The marker is drawn rather than relying on the ListView cursor: the
        cursor only shows while the list has focus, so the selection went
        invisible the moment you tabbed to another panel.
        """
        text, deps = self.flags[i]
        out = Text("[*] " if i == self._selected else "[ ] ",
                   style="bold" if i == self._selected else "dim")
        out.append(text)
        parts = "; ".join(f"{self.shown[d][0]} = {self.shown[d][1]}" for d in deps)
        if parts:
            out.append(f"  ({parts})", style="dim")
        return out

    def on_mount(self) -> None:
        self.query_one("#flags", ListView).focus()

    def on_data_table_cell_highlighted(self, event) -> None:
        """Column 0 is the row label -- never a value, never editable, so the
        cursor bounces off it instead of stopping there."""
        if event.coordinate.column == 0:
            event.data_table.cursor_coordinate = Coordinate(event.coordinate.row, 1)

    def action_reload(self) -> None:
        """Re-read everything: probe the system again and re-read policy.toml.

        Equivalent to restarting the app. The tracking dicts must be cleared
        first or they keep pointing at widgets recompose is about to destroy.
        """
        self._remember_position()
        self._snapshot()
        self._rows.clear()
        self._row_tabs.clear()
        self._titles.clear()
        self._labels = []
        self._selected = None
        self.refresh(recompose=True)
        self.call_after_refresh(self._restore_position)
        self.notify("reloaded")

    def _remember_position(self) -> None:
        tabs = self.query_one("#main", TabbedContent)
        self._active_tab = tabs.active


    def _restore_position(self) -> None:
        self.query_one("#flags", ListView).focus()

    def _sheets(self, sheets, tab_id):
        for subtitle, headers, rows in sheets:
            if subtitle:
                yield Static(subtitle, classes="sub")
            table = DataTable(cursor_type="cell", show_header=any(headers))
            table.add_column("", key="label", width=34)
            for i, h in enumerate(headers):
                # No fixed width: paths and version strings were truncated.
                table.add_column(h or "value", key=f"c{i}")
            for row in rows:
                if row is None:
                    table.add_row("", *[""] * len(headers))
                    continue
                mark = "* " if row.id in self.pending else "  "
                table.add_row(f"{mark}{row.label}", *row.values, key=row.id)
                self._rows.setdefault(row.id, []).append((table, row.id, row.label))
                self._row_tabs.setdefault(row.id, set()).add(tab_id)
                if row.detail:
                    table.add_row(Text(f"    └ {row.detail}", style="dim italic"),
                                  *[""] * len(headers))
            # Explicit height: DataTable's `height: auto` does not resolve for
            # rows added during compose and collapses to 0, so the tables were
            # fully populated and completely invisible.
            table.styles.height = table.row_count + (1 if any(headers) else 0)
            yield table

    def _paint(self, highlighted: set[str]) -> None:
        for rid, places in self._rows.items():
            style = HL if rid in highlighted else ""
            for table, key, label in places:
                table.update_cell(key, "label", Text(f"  {label}", style=style))

    def action_apply(self) -> None:
        """problems() is the authority on whether anything needs applying;
        pending() only supplies the per-row detail. They differ when the
        GENERATOR changed rather than a value -- e.g. a new rung -- which
        shows as stale text with no row-level diff."""
        if not self.problems:
            self.notify("nothing to apply — system matches the profile")
            return
        self.push_screen(ConfirmApply(self.pending, self.problems), self._do_apply)

    def _do_apply(self, confirmed: bool | None) -> None:
        if not confirmed:
            return
        root = any(p.needs_root for p in self.pending.values())
        if root:
            # Before apply_session, not after: a declined password used to
            # leave the session half applied and the system half not.
            # Cached from startup, so this normally does not prompt; drop the
            # alt screen anyway in case the credential has since timed out.
            with self.suspend():
                print("\nApplying the system half (logind, charge thresholds)...")
                rc = system.sudo_apply_system()
            if rc:
                self.notify(f"nothing applied (sudo exit {rc})", severity="error")
                return
        system.apply_session()
        self.notify("applied (system + session)" if root else "applied")
        self.action_reload()

    def action_edit(self) -> None:
        """Edit the policy value under the cursor.

        Only policy rows are editable; TLP and live-state rows are owned
        elsewhere.
        """
        table = self.focused
        if not isinstance(table, DataTable):
            self.notify("select a cell in a table first (tab to move focus)")
            return
        row_key, col = table.coordinate_to_cell_key(table.cursor_coordinate)
        rid = str(row_key.value)
        column = table.cursor_coordinate.column - 1     # column 0 is the label
        path = EDITABLE.get((rid, column))
        if path is None:
            self.notify(f"{rid} is not editable here — it is owned elsewhere")
            return
        self.push_screen(EditValue(path, current(self.pol, path)), self._do_edit)

    def _do_edit(self, result) -> None:
        if result is None:
            return
        path, raw = result
        try:
            value = coerce(path, raw)
        except ValueError as e:
            self.notify(f"{raw!r} rejected: {e}", severity="error")
            return
        system.save(path, value)
        self.action_reload()
        self.notify(f"{'.'.join(path)} = {raw}   (press a to apply)")

    def action_toggle(self) -> None:
        """Space toggles the flag under the cursor. One at a time: selecting
        another replaces it, selecting the same one clears it."""
        idx = self.query_one("#flags", ListView).index
        if idx is None:
            return
        self._selected = None if self._selected == idx else idx
        for i, label in enumerate(self._labels):
            label.update(self._flag_text(i))
        deps = set() if self._selected is None else set(self.flags[self._selected][1])
        self._paint(deps)
        self._mark_tabs(deps)

    def _mark_tabs(self, deps: set[str]) -> None:
        affected = {t for rid in deps for t in self._row_tabs[rid]}
        tabs = self.query_one("#main", TabbedContent)
        for tab_id, title in self._titles.items():
            tab = tabs.get_tab(tab_id)
            tab.label = (Text("* " + title, style="bold yellow")
                         if tab_id in affected else Text(title))


def main() -> None:
    # Up front, not at apply time: the TUI itself stays unprivileged (it must
    # write ~/.config and drive the user's hypridle), but applying needs root
    # for the logind drop-in and the charge thresholds. Caching the credential
    # here means the apply never stops to ask.
    # Not a privilege boundary -- root is simply broken here. HOME becomes
    # /root so the profile is not found, and there is no user bus, so
    # hypridle can be neither read nor restarted. Fail with the reason
    # rather than showing an empty screen.
    if os.geteuid() == 0:
        print("run hyprpower as your own user, not root: as root there is no "
              "user bus, so hypridle cannot be read or restarted. It escalates "
              "on its own for the parts that need it.", file=sys.stderr)
        raise SystemExit(1)
    if rc := system.sudo_refresh():
        print("sudo is required: applying writes /etc/systemd/logind.conf.d "
              "and battery sysfs", file=sys.stderr)
        raise SystemExit(rc)
    HyprPower().run()



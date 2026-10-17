"""The keys of a rig front panel (ControlPanel), shared by the rig view and the dashboard.

Navigation: the cursor keys move between the options. Editing (Enter or Space on one): the
cursor keys change it at once, or the value is typed; Enter confirms, Esc puts the old value
back. The application provides ``_panel()``, ``_panel_active()`` (whether the keys are the
panel's now) and ``_message()`` (``_note()`` for the help shown while moving, by default the
same); it may handle some options itself (``_set_special``).
"""

from __future__ import annotations

from ic705suite.i18n import _
from ic705suite.station.links import LinkError
from ic705suite.ui.controlpanel import ControlPanel, format_value


class PanelKeys:
    link: object

    # ---- provided by the application -------------------------------------------------

    def _panel(self) -> ControlPanel:
        raise NotImplementedError

    def _panel_active(self) -> bool:
        return True

    def _message(self, kind: str, text: str) -> None:
        raise NotImplementedError

    def _note(self, kind: str, text: str) -> None:
        self._message(kind, text)

    async def _set_special(self, control_id: str, value) -> bool:
        """Options the application handles itself (not rig controls); True if handled."""
        return False

    def _typed_value(self, panel: ControlPanel):
        return panel.typed_value()

    # ---- setting a value ------------------------------------------------------------------

    async def _set(self, control_id: str, value) -> None:
        panel = self._panel()
        before = panel.values.get(control_id)
        # Shown at once and kept until the radio reports it: the wheel's next step goes on
        # from here, not from the value the state still carries.
        panel.hold(control_id, value)
        panel.refresh()
        if await self._set_special(control_id, value):
            return
        try:
            reply = await self.link.call("rig.set", id=control_id, value=value)
        except LinkError as exc:
            panel.held.pop(control_id, None)
            panel.values[control_id] = before
            panel.refresh()
            self._message("error", str(exc))
            return
        if reply.get("error"):
            panel.held.pop(control_id, None)
            panel.values[control_id] = before
            panel.refresh()
            self._message("error", reply["error"])
            return
        panel.hold(control_id, reply.get("value"))
        panel.refresh()
        control = panel.control(control_id)
        if control:
            self._note("info", f"{_(control['label'])} = {format_value(control, reply.get('value'))}")

    # ---- keys -------------------------------------------------------------------------------

    async def action_arrow(self, dx: int, dy: int, big: bool = False) -> None:
        """Navigation: move between the options. Editing: change the value
        (→ and ↑ up, ← and ↓ down)."""
        if not self._panel_active():
            return
        panel = self._panel()
        if panel.editing:
            await self.action_adjust(1 if dx > 0 or dy < 0 else -1, big)
            return
        if dy:
            panel.move_vertical(dy)
        else:
            panel.move_horizontal(dx)
        self._note("info", panel.help_line())

    async def action_enter(self) -> None:
        if not self._panel_active():
            return
        panel = self._panel()
        if not panel.editing:
            if panel.control(panel.selected) is None:
                return
            panel.start_edit()
            self._note("info", _("editing {option}: ←→ ↑↓ change it or type the value · "
                                    "Enter done · Esc cancel").format(
                option=_(panel.control(panel.selected)["label"])))
            return
        if panel.buffer.strip():
            try:
                value = self._typed_value(panel)
            except ValueError:
                self._message("error", _("«{text}» is not a value for {option}").format(
                    text=panel.buffer, option=_(panel.control(panel.selected)["label"])))
                panel.buffer = ""
                panel.refresh()
                return
            await self._set(panel.selected, value)
        panel.end_edit()

    async def _cancel_edit(self) -> None:
        panel = self._panel()
        control_id, original = panel.selected, panel.original
        changed = panel.values.get(control_id) != original
        panel.end_edit()
        if changed and original is not None:
            await self._set(control_id, original)
        self._note("info", _("edit cancelled"))

    async def action_adjust(self, direction: int, big: bool = False) -> None:
        if not self._panel_active():
            return
        change = self._panel().adjusted(direction, big)
        if change is not None:
            await self._set(*change)

"""Dialogs drawn in place of the main frame, as hamrlog does."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, ScreenResultType
from textual.widgets import Button, Input, Label, Static

from ..i18n import N_, _


class PanelScreen(ModalScreen[ScreenResultType]):
    """A dialog covering exactly the main frame.

    The status line, the entry line and the footer stay in view, and the
    entry's help line shows this panel's keys while it holds the keyboard.
    """

    #: Keys shown on the entry's help line while this panel is on top.
    _keys: str = ""

    def on_mount(self) -> None:
        panel = self.query(".modal").first()
        titles = panel.query(".modal-title")
        if titles:
            title = titles.first()
            panel.border_title = str(title.content)  # type: ignore[attr-defined]
            title.display = False
        _show_keys(self.app, self._keys)
        self.call_after_refresh(self._fit_to_panel)

    def dismiss(self, result: ScreenResultType | None = None):  # type: ignore[no-untyped-def]
        awaitable = super().dismiss(result)
        self.app.call_later(_restore_keys, self.app)
        return awaitable

    def on_resize(self) -> None:
        self.call_after_refresh(self._fit_to_panel)

    def _fit_to_panel(self) -> None:
        try:
            frame = self.app.screen_stack[0].query_one("#log-frame")
            panel = self.query(".modal").first()
        except Exception:  # noqa: BLE001 - nothing to fit, e.g. during teardown
            return
        region = frame.region
        panel.styles.offset = (region.x, region.y)
        panel.styles.width = region.width
        panel.styles.height = region.height


def _show_keys(app: Any, keys: str) -> None:
    try:
        entry = app.screen_stack[0].query_one("#entry")
    except Exception:  # noqa: BLE001 - the main screen is being torn down
        return
    entry.show_keys(keys)


def _restore_keys(app: Any) -> None:
    top = app.screen
    if isinstance(top, PanelScreen):
        _show_keys(app, top._keys)
    else:
        app.refresh_keys()


class ConfirmScreen(PanelScreen[bool]):
    """Yes/no confirmation, defaulting to no."""

    _keys = N_("←→ choose · Enter confirms · Y yes · N or Esc no")

    BINDINGS = [
        Binding("escape", "no", "No"),
        Binding("y", "yes", "Yes", show=False),
        Binding("s", "yes", "Yes", show=False),
        Binding("n", "no", "No", show=False),
        Binding("left", "focus_yes", "Yes", show=False),
        Binding("right", "focus_no", "No", show=False),
    ]

    def __init__(self, question: str, *, danger: bool = False) -> None:
        super().__init__()
        self.question = question
        self.danger = danger

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Label(self.question, classes="modal-title")
            yield Static(self.question, classes="modal-subtitle")
            with Horizontal(classes="modal-buttons"):
                yield Button(_("Yes (Y)"), variant="error" if self.danger else "primary",
                             id="yes")
                yield Button(_("No (Esc)"), variant="default", id="no")

    def on_mount(self) -> None:
        super().on_mount()
        self.query_one("#no", Button).focus()

    def action_focus_yes(self) -> None:
        self.query_one("#yes", Button).focus()

    def action_focus_no(self) -> None:
        self.query_one("#no", Button).focus()

    @on(Button.Pressed, "#yes")
    def action_yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def action_no(self) -> None:
        self.dismiss(False)


@dataclass(slots=True)
class Field:
    """One input of a FormScreen."""

    key: str
    label: str
    value: str = ""
    hint: str = ""
    password: bool = False


class FormScreen(PanelScreen[dict[str, str] | None]):
    """Small vertical form; dismisses with a dict of values or None.

    ``validate`` may return an error message to keep the form open.
    """

    _keys = N_("Tab next field · Enter or Ctrl+S save · Esc cancel")

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
        Binding("up", "app.focus_previous", "Previous", show=False),
        Binding("down", "app.focus_next", "Next", show=False),
    ]

    def __init__(self, title: str, fields: list[Field], *, subtitle: str = "",
                 save_label: str = "", validate: Any = None) -> None:
        super().__init__()
        self.title_text = title
        self.subtitle_text = subtitle
        self.fields = fields
        self.save_label = save_label or _("Save")
        self.validate = validate

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Label(self.title_text, classes="modal-title")
            if self.subtitle_text:
                yield Static(self.subtitle_text, classes="modal-subtitle")
            with VerticalScroll(classes="form-body"):
                for field in self.fields:
                    with Horizontal(classes="form-row"):
                        yield Label(field.label, classes="form-label")
                        yield Input(value=field.value, password=field.password,
                                    id=f"field-{field.key}", classes="form-input")
                        if field.hint:
                            yield Label(field.hint, classes="form-hint")
            yield Static("", id="form-error", classes="form-error")
            with Horizontal(classes="modal-buttons"):
                yield Button(f"{self.save_label} (Ctrl+S)", variant="primary", id="save")
                yield Button(_("Cancel (Esc)"), id="cancel")

    def on_mount(self) -> None:
        super().on_mount()
        self.query_one(f"#field-{self.fields[0].key}", Input).focus()

    def collect(self) -> dict[str, str]:
        return {
            field.key: self.query_one(f"#field-{field.key}", Input).value.strip()
            for field in self.fields
        }

    @on(Input.Submitted)
    def _on_submitted(self) -> None:
        self.action_save()

    @on(Button.Pressed, "#save")
    def action_save(self) -> None:
        values = self.collect()
        error = self.validate(values) if self.validate else None
        if error:
            self.query_one("#form-error", Static).update(error)
            return
        self.dismiss(values)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)

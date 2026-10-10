"""The two dialogs the main screen still needs: a confirmation and a form."""

from __future__ import annotations

from dataclasses import dataclass

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, ScreenResultType
from textual.widgets import Button, Input, Label, Static

from ...i18n import N_, _


class PanelScreen(ModalScreen[ScreenResultType]):
    """A dialog drawn in the main panel instead of a floating box.

    It covers exactly the area of the log (``#log-frame``) and leaves the
    status line, the entry line and the footer in view, so the
    application never looks like it stacked a dialog on top of itself. It is
    still a screen underneath, so it takes the keyboard and Escape goes back.

    The container with the ``modal`` class is sized and placed in code: the
    log's position depends on the terminal and on the entry line's height,
    which a stylesheet cannot know.

    Panels carry no help text: the line of ``modal-help`` class is hidden.
    """

    def on_mount(self) -> None:
        # The title goes on the frame, as «Log» does on the log.
        panel = self.query(".modal").first()
        titles = panel.query(".modal-title")
        if titles:
            title = titles.first()
            panel.border_title = str(title.content)  # type: ignore[attr-defined]
            title.display = False
        helps = panel.query(".modal-help")
        if helps:
            helps.first().display = False
        self.call_after_refresh(self._fit_to_panel)

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
        panel.styles.max_width = None
        panel.styles.max_height = None


class MainFrame(Vertical):
    """The frame of the main window, which a PanelScreen covers.

    Its height changes when the entry window grows or shrinks, e.g. while
    editing, so a panel open at that moment is fitted again.
    """

    def on_resize(self) -> None:
        top = self.app.screen
        if isinstance(top, PanelScreen):
            self.call_after_refresh(top._fit_to_panel)  # noqa: SLF001


class ConfirmScreen(PanelScreen[bool]):
    """Yes/no confirmation, defaulting to no.

    Three ways to answer, because a confirmation appears at the moment the
    operator is least willing to stop and think: the arrow keys move between
    the buttons as they are laid out, Tab cycles them, and Y (or S, for «sí»)
    and N answer outright.
    """

    BINDINGS = [
        Binding("escape", "no", N_("No")),
        Binding("y", "yes", N_("Yes"), show=False),
        Binding("s", "yes", N_("Yes"), show=False),
        Binding("n", "no", N_("No"), show=False),
        # Positional: Yes is drawn on the left, No on the right. Inputs consume
        # the arrows first, so these only fire once a button holds the focus.
        Binding("left", "focus_yes", N_("Yes"), show=False),
        Binding("right", "focus_no", N_("No"), show=False),
        Binding("up", "focus_yes", N_("Yes"), show=False),
        Binding("down", "focus_no", N_("No"), show=False),
    ]

    def __init__(self, question: str, *, detail: str = "", danger: bool = False) -> None:
        super().__init__()
        self.question = question
        self.detail = detail
        self.danger = danger

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Label(self.question, classes="modal-title")
            if self.detail:
                yield Static(self.detail, classes="modal-subtitle")
            with Horizontal(classes="modal-buttons"):
                yield Button(
                    _("Yes (Y)"), variant="error" if self.danger else "primary", id="yes"
                )
                yield Button(_("No (Esc)"), variant="default", id="no")
            yield Static(
                _("←→ choose · Enter confirms · Y yes · N or Esc no"),
                classes="modal-help",
            )

    def on_mount(self) -> None:
        # Defaults to No: a confirmation answered by reflex must not destroy
        # anything.
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
    """One input of a FormScreen.

    Attributes:
        key: Name the value is returned under.
        label: Prompt shown to the operator.
        value: Initial value.
        placeholder: Hint shown when empty.
        editable: False renders the value read-only, used by the QSO editor to
            show fields an automatic contact does not allow changing.
        kind: "text", "integer" or "datetime".
    """

    key: str
    label: str
    value: str = ""
    placeholder: str = ""
    editable: bool = True
    kind: str = "text"


class FormScreen(PanelScreen[dict[str, str] | None]):
    """Small vertical form; dismisses with a dict of values or None."""

    BINDINGS = [
        Binding("escape", "cancel", N_("Cancel")),
        Binding("ctrl+s", "save", N_("Save")),
        # Only reached once a button has the focus: the text fields use the
        # arrows to move the caret.
        Binding("left", "focus_save", N_("Save"), show=False),
        Binding("right", "focus_cancel", N_("Cancel"), show=False),
    ]

    def __init__(
        self,
        title: str,
        fields: list[Field],
        *,
        subtitle: str = "",
        save_label: str = "",
    ) -> None:
        super().__init__()
        self.title_text = title
        self.subtitle_text = subtitle
        self.fields = fields
        self.save_label = save_label or _("Save")

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal"):
            yield Label(self.title_text, classes="modal-title")
            if self.subtitle_text:
                yield Static(self.subtitle_text, classes="modal-subtitle")
            with VerticalScroll(classes="form-body"):
                for field in self.fields:
                    with Horizontal(classes="form-row"):
                        yield Label(field.label, classes="form-label")
                        if field.editable:
                            yield Input(
                                value=field.value,
                                placeholder=field.placeholder,
                                id=f"field-{field.key}",
                                classes="form-input",
                            )
                        else:
                            yield Static(
                                field.value or "-",
                                id=f"field-{field.key}",
                                classes="form-input form-readonly",
                            )
            yield Static("", id="form-error", classes="form-error")
            with Horizontal(classes="modal-buttons"):
                yield Button(f"{self.save_label} (Ctrl+S)", variant="primary", id="save")
                yield Button(_("Cancel (Esc)"), id="cancel")

    def on_mount(self) -> None:
        for field in self.fields:
            if field.editable:
                self.query_one(f"#field-{field.key}", Input).focus()
                break

    def action_focus_save(self) -> None:
        self.query_one("#save", Button).focus()

    def action_focus_cancel(self) -> None:
        self.query_one("#cancel", Button).focus()

    def show_error(self, message: str) -> None:
        self.query_one("#form-error", Static).update(message)

    def collect(self) -> dict[str, str]:
        """Current values of every editable field."""
        values: dict[str, str] = {}
        for field in self.fields:
            if not field.editable:
                continue
            values[field.key] = self.query_one(f"#field-{field.key}", Input).value.strip()
        return values

    @on(Input.Submitted)
    def _on_submitted(self) -> None:
        self.action_save()

    @on(Button.Pressed, "#save")
    def action_save(self) -> None:
        self.dismiss(self.collect())

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)

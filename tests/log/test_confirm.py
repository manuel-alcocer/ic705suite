"""Confirmation dialogs: every way of answering them."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Button, Static

from ic705suite.apps.log.tui.screens.base import ConfirmScreen, Field, FormScreen


class Harness(App[None]):
    """Minimal host so the modal screens can be driven on their own."""

    def compose(self) -> ComposeResult:
        yield Static("fondo")


async def open_confirm(pilot, app) -> None:
    app.push_screen(ConfirmScreen("¿Seguro?", detail="detalle"))
    await pilot.pause()


async def test_defaults_to_no():
    """A confirmation answered by reflex must not destroy anything."""
    app = Harness()
    async with app.run_test() as pilot:
        await open_confirm(pilot, app)
        assert app.focused.id == "no"


async def test_arrows_move_between_the_buttons():
    app = Harness()
    async with app.run_test() as pilot:
        await open_confirm(pilot, app)

        await pilot.press("left")
        await pilot.pause()
        assert app.focused.id == "yes"

        await pilot.press("right")
        await pilot.pause()
        assert app.focused.id == "no"

        # Up and down work too, for anyone who reads the buttons as a list.
        await pilot.press("up")
        await pilot.pause()
        assert app.focused.id == "yes"
        await pilot.press("down")
        await pilot.pause()
        assert app.focused.id == "no"


async def test_enter_confirms_the_focused_button():
    app = Harness()
    async with app.run_test() as pilot:
        result: list[bool | None] = []
        app.push_screen(ConfirmScreen("¿Seguro?"), result.append)
        await pilot.pause()

        await pilot.press("left")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert result == [True]


async def test_enter_on_the_default_answers_no():
    app = Harness()
    async with app.run_test() as pilot:
        result: list[bool | None] = []
        app.push_screen(ConfirmScreen("¿Seguro?"), result.append)
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert result == [False]


async def test_tab_still_cycles_the_buttons():
    app = Harness()
    async with app.run_test() as pilot:
        await open_confirm(pilot, app)
        assert app.focused.id == "no"
        await pilot.press("tab")
        await pilot.pause()
        assert isinstance(app.focused, Button)
        assert app.focused.id == "yes"


async def test_letter_keys_answer_outright():
    for key, expected in (("s", True), ("y", True), ("n", False), ("escape", False)):
        app = Harness()
        async with app.run_test() as pilot:
            result: list[bool | None] = []
            app.push_screen(ConfirmScreen("¿Seguro?"), result.append)
            await pilot.pause()
            await pilot.press(key)
            await pilot.pause()
            assert result == [expected], key


async def test_form_arrows_edit_text_but_select_buttons_once_focused():
    """The arrows must keep moving the caret while a field has the focus."""
    app = Harness()
    async with app.run_test() as pilot:
        app.push_screen(FormScreen("Prueba", [Field("name", "Nombre", "abc")]))
        # Children are mounted on a later refresh than the screen itself.
        for _ in range(60):
            if app.screen.query("#field-name"):
                break
            await pilot.pause()

        field = app.screen.query_one("#field-name")
        assert app.focused is field
        field.cursor_position = 3
        await pilot.press("left")
        await pilot.pause()
        # Still editing text, not jumping to a button.
        assert app.focused is field
        assert field.cursor_position == 2

        app.screen.query_one("#cancel", Button).focus()
        await pilot.pause()
        await pilot.press("left")
        await pilot.pause()
        assert app.focused.id == "save"

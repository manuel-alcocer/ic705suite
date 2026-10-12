"""Main Textual application.

Layout, top to bottom: active configuration, history panel, fast entry
panel, counters. Everything the operator does during a session happens in the
entry line; ``/commands`` typed there change what a new QSO inherits.
"""

from __future__ import annotations

from importlib import resources

from textual import on
from textual.actions import SkipAction
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical

from .. import appconfig
from ..core import bands, callsign, modes
from ..core import entry as entry_parser
from ..core.services import (
    AntennaService,
    ContactService,
    EquipmentService,
    OperatorService,
    ProfileService,
    QsoService,
    RepeaterService,
    ServiceError,
    SettingsService,
    StationService,
)
from ..core.state import SessionState
from ..db.session import init_engine
from ..i18n import N_, _
from .address_book import CONTACT_KINDS
from .inventory import KINDS, Item, Kind, ListSuggester, tab_bar
from .profiles import PROFILE_KINDS, ProfileKind
from .profiles import legend as profile_legend
from .repeaters import REPEATER_KINDS
from .screens.base import ConfirmScreen, Field, FormScreen, MainFrame
from .screens.card import QsoCardScreen
from .settings import DATABASE as DATABASE_SETTING
from .settings import LANGUAGE as LANGUAGE_SETTING
from .settings import RESTART_NOTE, SETTING_KINDS
from .tools import TOOL_KINDS
from .widgets.detail import DetailPanel
from .widgets.entry import (
    BROWSE_PROMPT,
    EDIT_EXTRA_FIELDS,
    BrowseBar,
    EntryField,
    EntryPanel,
)
from .widgets.footer import StatsFooter
from .widgets.history import HistoryPanel
from .widgets.items import InventoryView, ItemTable
from .widgets.statusline import StatusLine

#: Settings key and defaults of the Prometheus exporter.
METRICS_KEY = "metrics"
DEFAULT_METRICS = {"enabled": False, "port": 9119}

#: Command word -> action name. Commands are the only way to change the
#: session from the entry line.
COMMANDS: dict[str, str] = {
    "ayuda": "help", "help": "help", "?": "help",
    "banda": "band", "band": "band",
    "frec": "frequency", "freq": "frequency", "qrg": "frequency", "frecuencia": "frequency",
    "modo": "mode", "mode": "mode", "digital": "mode",
    "perfil": "profiles", "perfiles": "profiles", "profile": "profiles",
    "repetidor": "repeater", "rptr": "repeater", "repeater": "repeater",
    "directo": "direct", "simplex": "direct",
    "deshacer": "undo", "undo": "undo", "borrar": "undo", "delete": "undo",
    "marca": "brand", "brand": "brand",
    "buscar": "search", "search": "search", "find": "search",
    "salir": "quit", "quit": "quit", "exit": "quit",
}

#: What ``/help`` puts on the feedback line. Short enough for 80 columns; a
#: command typed without its value explains itself. Translated where shown.
COMMAND_SUMMARY = N_("Commands: /band /freq /mode /profile /repeater /direct /undo /quit")

#: ``/help`` and the action bar of the profiles view.
PROFILES_SUMMARY = N_(
    "Profiles: Enter on one activates it · * makes it the default, activated on start · "
    "Ctrl+0…9 activate the main ones from anywhere · /profile NAME or KEY"
)
PROFILES_BROWSE = N_("Enter activate · * default · E edit · D delete · ↓ back to typing")

#: ``/help`` in the inventory view.
INVENTORY_SUMMARY = N_("Inventory: /brand NAME filters the list · /brand alone clears the filter")

#: Action bar over an item of the inventory view.
INVENTORY_BROWSE = N_("D delete · E edit · ↓ back to typing")

#: ``/help`` in the address book view.
CONTACTS_SUMMARY = N_(
    "Address book: /search TEXT looks up callsign, name, city, province, country "
    "or DMR ID · /search alone shows everything"
)

#: ``/help`` and the action bar of the repeaters view.
REPEATERS_SUMMARY = N_(
    "Repeaters: Enter on one tunes it · /search TEXT looks up callsign, URE number, "
    "channel, club, place, locator or mode · /search alone shows everything"
)
REPEATERS_BROWSE = N_("Enter tune · E edit · D delete · ↓ back to typing")

#: ``/help`` in the tools view.
TOOLS_SUMMARY = N_("Tools: type in the box to narrow the list · ↑↓ move through it")

#: ``/help`` and the action bar of the settings view.
SETTINGS_SUMMARY = N_(
    "Settings: Enter or E changes the one under the cursor, Enter saves it, Esc cancels"
)
SETTINGS_BROWSE = N_("Enter or E change")

#: The lists each list view shows, its title, and its ``/help``.
LIST_VIEWS: dict[str, tuple[tuple[Kind, ...], str, str]] = {
    "inventory": (KINDS, N_("Inventory"), INVENTORY_SUMMARY),
    "contacts": (CONTACT_KINDS, N_("Address book"), CONTACTS_SUMMARY),
    "profiles": (PROFILE_KINDS, N_("Profiles"), PROFILES_SUMMARY),
    "repeaters": (REPEATER_KINDS, N_("Repeaters"), REPEATERS_SUMMARY),
    "tools": (TOOL_KINDS, N_("Tools"), TOOLS_SUMMARY),
    "settings": (SETTING_KINDS, N_("Settings"), SETTINGS_SUMMARY),
}

#: Views whose tab names show even when they have a single tab, because
#: more are meant to follow.
TABBED_VIEWS = frozenset({"tools"})

#: Action bar over an item, per list view; INVENTORY_BROWSE otherwise.
BROWSE_PROMPTS: dict[str, str] = {
    "profiles": PROFILES_BROWSE,
    "repeaters": REPEATERS_BROWSE,
    "settings": SETTINGS_BROWSE,
}

#: The function key that opens each list view from the log.
VIEW_KEYS: dict[str, str] = {
    "f2": "inventory", "f3": "contacts", "f4": "profiles", "f5": "repeaters",
    "f8": "tools", "f9": "settings",
}

#: Shown when a command that needs a value is typed without one.
COMMAND_USAGE: dict[str, str] = {
    "band": N_("Usage: /band 40m"),
    "frequency": N_("Usage: /freq 7.100"),
    "mode": N_("Usage: /mode SSB"),
    "profiles": N_("Usage: /profile NAME, or /profile 3 for the one on Ctrl+3"),
    "repeater": N_("Usage: /repeater CALLSIGN · /direct to go back to simplex"),
}

#: How many previously typed lines the up/down keys can recall.
LINE_HISTORY_LIMIT = 100

LOG_WATCH_SECONDS = 5.0      # how often a shared log is looked at for changes


class HamrlogApp(App[None]):
    """The logbook application."""

    # Read as package data rather than by file path: inside a PyInstaller
    # bundle the modules live in an archive and __file__ points nowhere on
    # disk, so CSS_PATH would fail there.
    CSS = resources.files("ic705suite.apps.log.tui").joinpath("styles.tcss").read_text(encoding="utf-8")
    TITLE = "hamrlog"
    SUB_TITLE = N_("amateur radio logbook")

    # priority=True so the shortcuts work while the entry line has focus.
    BINDINGS = [
        # Every function key goes through one action: what it does depends
        # on the view, and a key a view does not use must do nothing rather
        # than reach the entry line.
        *(
            Binding(f"f{number}", f"function_key('f{number}')", N_("Function key"),
                    priority=True, show=False)
            for number in range(1, 13)
        ),
        Binding("pageup", "function_key('pageup')", N_("Previous page"),
                priority=True, show=False),
        Binding("pagedown", "function_key('pagedown')", N_("Next page"),
                priority=True, show=False),
        Binding("shift+pageup", "function_key('shift+pageup')", N_("Previous tab"),
                priority=True, show=False),
        Binding("shift+pagedown", "function_key('shift+pagedown')", N_("Next tab"),
                priority=True, show=False),
        Binding("ctrl+n", "function_key('ctrl+n')", N_("Next tab"), priority=True, show=False),
        Binding("alt+up", "cycle_brand(-1)", N_("Previous brand"), priority=True, show=False),
        Binding("alt+down", "cycle_brand(1)", N_("Next brand"), priority=True, show=False),
        # The ten main profiles, from any view.
        *(
            Binding(f"ctrl+{slot}", f"activate_slot({slot})", N_("Activate profile"),
                    priority=True, show=False)
            for slot in range(10)
        ),
        Binding("ctrl+d", "delete_qso", N_("Delete QSO"), priority=True),
        Binding("ctrl+r", "refresh_log", N_("Refresh the log"), priority=True, show=False),
        Binding("ctrl+a", "mark_all", N_("Select all"), priority=True, show=False),
        Binding("escape", "back_to_entry", N_("Back to typing"), show=False),
        Binding("ctrl+q", "quit", N_("Quit"), priority=True),
    ]

    def __init__(self, database_url: str | None = None, *, demo: bool = False) -> None:
        super().__init__()
        self.sub_title = _(self.SUB_TITLE)
        self.database_url = database_url
        #: A demo run (``hamrlog --demo``): said on the status line.
        self.demo = demo
        self.state = SessionState()
        self._line_history: list[dict[str, str]] = []
        self._history_index: int | None = None
        self._last_dup_check = ""
        #: True while the feedback line describes the selected QSO, so that
        #: leaving the selection clears it without wiping other messages.
        self._showing_selection = False
        #: Id of the logged QSO (or item) the entry form is correcting, if any.
        self._editing_id: int | None = None
        #: The QSOs an edit of several at once applies to; empty otherwise.
        self._bulk_ids: list[int] = []
        #: "log" or a LIST_VIEWS key: what the main frame and the entry line serve.
        self._view = "log"
        #: Active list of each list view, an index into its kinds.
        self._tabs: dict[str, int] = dict.fromkeys(LIST_VIEWS, 0)
        #: /search text of each searchable list, by kind key.
        self._queries: dict[str, str] = {}
        #: Brand filter per tab key; empty shows every brand.
        self._brands: dict[str, str] = {}
        #: Half-typed entries per view, so switching never loses one.
        self._drafts: dict[str, dict[str, str]] = {}

    # ------------------------------------------------------------- layout --
    def compose(self) -> ComposeResult:
        yield StatusLine(id="statusline")
        # Three windows, each in its own frame, between the top line and the
        # status line: the list, the detail of its selection, and the entry.
        with MainFrame(id="log-frame"):
            yield HistoryPanel(id="history")
            yield InventoryView(id="inventory")
            yield BrowseBar(id="entry-browse")
        yield DetailPanel(id="detail")
        yield EntryPanel(id="entry")
        yield StatsFooter(id="stats")

    async def on_mount(self) -> None:
        init_engine(self.database_url)
        self._load_initial_state()
        # Before anything renders a frequency.
        self.state.apply_frequency_format()
        self._maybe_start_metrics()
        self.query_one(HistoryPanel).order = self.state.history_order
        self._apply_config()
        self.query_one(HistoryPanel).load(QsoService.recent())
        self._refresh_status()
        self._refresh_stats()
        # Wait for the field boxes to exist before handing them the keyboard,
        # so nothing else can take it first.
        self.query_one(StatusLine).demo = self.demo
        self.query_one("#log-frame", Vertical).border_title = _("Log")
        self.query_one(DetailPanel).border_title = _("Detail")
        self.query_one(EntryPanel).border_title = _("Entry")
        self.query_one(InventoryView).display = False
        self._refresh_detail(None)
        panel = self.query_one(EntryPanel)
        await panel.ready()
        panel.focus_input()
        if self.state.operator_id is None:
            self.call_after_refresh(self._first_run_wizard)

        self._log_version = QsoService.version()        # as loaded: changes from here
        self.set_interval(LOG_WATCH_SECONDS, self._watch_log)
    def _load_initial_state(self) -> None:
        """Restore the previous session, then activate the default profile."""
        self.state = SettingsService.load_state()

        default_profile = ProfileService.get_default()
        if default_profile is not None:
            ProfileService.apply_to_state(default_profile.id, self.state)
        elif self.state.profile_id is not None:
            # Still active from the last run, unless it was deleted since.
            if ProfileService.get(self.state.profile_id) is None:
                self.state.leave_profile()

        # The stored operator may have been removed since the last run.
        if self.state.operator_id is not None:
            if OperatorService.get(self.state.operator_id) is None:
                self.state.operator_id = None
        if self.state.operator_id is None:
            operators = OperatorService.list_all()
            if operators:
                self.state.operator_id = operators[0].id

        if not self.state.mode:
            self.state.mode = modes.DEFAULT_MODE
        if not self.state.band and self.state.freq_hz is None:
            self.state.set_band("40m")

    def _maybe_start_metrics(self) -> None:
        """Start the Prometheus exporter when it is enabled in the settings."""
        config = SettingsService.get(METRICS_KEY, {}) or {}
        if not config.get("enabled"):
            return
        from ..api.metrics import start_exporter

        port = int(config.get("port", DEFAULT_METRICS["port"]))
        if start_exporter(port):
            self.notify(
                _("Prometheus metrics on port {port}.").format(port=port),
                severity="information",
            )
        else:
            self.notify(
                _(
                    "Could not start the metrics exporter. "
                    "Check the port or install hamrlog[metrics]."
                ),
                severity="warning",
            )

    def _first_run_wizard(self) -> None:
        """Ask for a callsign the first time the application is opened."""
        self.push_screen(
            FormScreen(
                _("Welcome to hamrlog"),
                [
                    Field("callsign", _("Your callsign"), placeholder="EA7WM"),
                    Field("name", _("Name")),
                    Field("gridsquare", _("Locator"), placeholder="IM76"),
                ],
                subtitle=_("I need an operator to start logging contacts."),
                save_label=_("Start"),
            ),
            self._create_first_operator,
        )

    def _create_first_operator(self, values: dict[str, str] | None) -> None:
        if not values or not values.get("callsign"):
            self.notify(
                _("Without an operator no contacts can be logged."), severity="warning"
            )
            return
        try:
            operator = OperatorService.create(
                values["callsign"], values.get("name", ""), values.get("gridsquare", "")
            )
        except ServiceError as exc:
            self.notify(str(exc), severity="error")
            return
        self.state.operator_id = operator.id
        self._refresh_status()
        self.notify(
            _("Operator {call} ready. Good luck with the DX.").format(call=operator.callsign)
        )

    # ------------------------------------------------------------ refresh --
    def _refresh_status(self) -> None:
        """Push the session state into the status line."""
        status = self.query_one(StatusLine)
        operator = OperatorService.get(self.state.operator_id) if self.state.operator_id else None

        status.operator = operator.callsign if operator else ""
        status.repeater = self.state.repeater_call
        status.band = self.state.band
        status.freq_hz = self.state.freq_hz
        status.mode = self.state.mode
        status.station = self._equipment_summary()
        status.profile = self.state.profile_name
        status.digital_summary = modes.status_summary(
            self.state.digital_data, has_repeater=self.state.via_repeater
        )
        self.state.apply_frequency_format()
        if self._view == "log":
            self.query_one(EntryPanel).build_fields(self.state.field_order)
        history = self.query_one(HistoryPanel)
        history.set_order(self.state.history_order)
        history.refresh_headers()
        # Reactives only redraw when their value changes, and a format change
        # leaves every value untouched.
        status.refresh()
        if not self.query_one(HistoryPanel).selected_qso_id():
            self._refresh_detail(None)

    def _refresh_detail(self, row: object | None) -> None:
        """Show the selected QSO, or what a new one would inherit."""
        detail = self.query_one(DetailPanel)
        if row is not None:
            detail.show_qso(row)  # type: ignore[arg-type]
            return

        operator = OperatorService.get(self.state.operator_id) if self.state.operator_id else None
        stats = QsoService.stats()
        detail.show_session(
            self.state,
            operator=operator.display if operator else "",
            station=self._equipment_summary(),
            stats_line=_("{count} QSO today").format(count=stats.today) if stats.today else "",
        )

    def _equipment_summary(self) -> str:
        """The setup in use, or the rig, antenna and power; empty without them."""
        setup = (
            EquipmentService.get(self.state.equipment_id) if self.state.equipment_id else None
        )
        if setup is not None:
            return setup.name
        station = StationService.get(self.state.station_id) if self.state.station_id else None
        if station is None:
            return ""
        antenna = (
            AntennaService.get(self.state.antenna_id) if self.state.antenna_id else None
        )
        return station.summary(antenna)

    def _apply_config(self) -> None:
        """Put the settings that take effect at once (F9) to use."""
        config = appconfig.load()
        self.query_one(HistoryPanel).set_day_separator(config.day_separator)
        self.query_one(StatsFooter).timezone = config.timezone

    def _refresh_stats(self) -> None:
        self.query_one(StatsFooter).stats = QsoService.stats()

    def _reload_history(self) -> None:
        panel = self.query_one(HistoryPanel)
        panel.set_order(self.state.history_order)
        panel.load(QsoService.recent())

    def action_refresh_log(self) -> None:
        """Ctrl+R: the log as it is now in the database (another client, the core's FT8)."""
        self._log_version = None
        self._watch_log()

    def _watch_log(self) -> None:
        """Every few seconds: the list again when the log changed elsewhere (a shared
        database: the core logs FT8, another client logs too). With no version noted
        (Ctrl+R), it is reloaded anyway."""
        try:
            version = QsoService.version()
        except Exception:  # noqa: BLE001 - the database away for a moment: next time
            return
        if version != getattr(self, "_log_version", None):
            self._reload_history()
            self._refresh_stats()
        self._log_version = version

    def _persist_state(self) -> None:
        try:
            SettingsService.save_state(self.state)
        except Exception:  # noqa: BLE001 - never block exit on a settings write
            pass

    # -------------------------------------------------------------- entry --
    @on(EntryField.MoveHistory)
    def _on_move_history(self, event: EntryField.MoveHistory) -> None:
        """Arrow keys browse the log without moving the focus."""
        if self._editing_id is not None:
            # The form holds that row's values: moving away would orphan them.
            return
        if self._view in LIST_VIEWS:
            self.query_one(ItemTable).move_selection(event.delta)
            return
        self.query_one(HistoryPanel).move_selection(event.delta)

    @on(HistoryPanel.SelectionChanged)
    def _on_selection_changed(self, event: HistoryPanel.SelectionChanged) -> None:
        """Tell the operator what the arrows have landed on."""
        panel = self.query_one(EntryPanel)
        if event.qso_id is None:
            self._editing_id = None
            panel.set_browsing(False)
            self._refresh_detail(None)
            # Only clear a message this handler put there: returning to the
            # insert row must not wipe the confirmation of what was just saved.
            if self._showing_selection:
                panel.feedback("")
                self._showing_selection = False
            return

        row = QsoService.get(event.qso_id)
        if row is None:
            return
        # The detail pane says what this QSO is; the feedback line is left
        # free for messages rather than repeating it.
        self._refresh_detail(row)
        panel.set_browsing(True)
        if self._showing_selection:
            panel.feedback("")
        self._showing_selection = True

    @on(EntryField.Recall)
    def _on_recall(self, event: EntryField.Recall) -> None:
        """Walk previously entered QSOs with Ctrl+Up and Ctrl+Down."""
        if not self._line_history or self._editing_id is not None or self._view != "log":
            return
        if self._history_index is None:
            self._history_index = len(self._line_history)
        self._history_index = max(
            0, min(len(self._line_history), self._history_index + event.direction)
        )
        entry = self.query_one(EntryPanel)
        if self._history_index >= len(self._line_history):
            entry.clear()
        else:
            entry.set_values(self._line_history[self._history_index])

    @on(EntryField.Changed)
    def _on_entry_changed(self, event: EntryField.Changed) -> None:
        """Warn about a duplicate as soon as the callsign is recognisable.

        EntryField does not define its own Changed, so this fires for every
        Input on screen, including the boxes of the form dialog.
        """
        if (
            isinstance(event.input, EntryField)
            and self._view in LIST_VIEWS
            and not self._kind.browses
        ):
            self._filter(event.value)
            return
        if (
            not isinstance(event.input, EntryField)
            or self._editing_id is not None
            or self._view != "log"
        ):
            return
        if event.input.field_name != "call":
            return
        panel = self.query_one(EntryPanel)
        text = event.value.strip()
        if not text or entry_parser.is_command(text):
            if self._last_dup_check:
                panel.feedback("")
                self._last_dup_check = ""
            return

        token, _override = callsign.strip_override(text.upper())
        if len(token) < 4:
            if self._last_dup_check:
                panel.feedback("")
                self._last_dup_check = ""
            return
        if token == self._last_dup_check:
            return
        self._last_dup_check = token

        # Flag a malformed callsign as it is typed, before Enter is pressed.
        if self.state.callsign_validation != "off":
            problem = callsign.validate(token)
            if problem is not None:
                panel.feedback(problem, "error")
                return

        # Who they are, from the address book, and whether we know them already.
        known = ContactService.lookup(token) if self.state.autofill_from_book else None
        identity = known.summary if known else ""

        # A duplicate is the same band and mode today (UTC), or ever in a contest profile;
        # another day is another QSO, only noted as known.
        rule = self.state.dupe_rule or "day"
        previous = [] if rule == "off" else QsoService.find_duplicates(
            token, self.state.band, self.state.mode, since_day=rule == "day"
        )
        if previous:
            last = previous[0]
            message = _(
                " DUPLICATE  already worked {count}× today on {band}/{mode} · last {when} UTC"
                if rule == "day" else
                " DUPLICATE  already worked {count}× on {band}/{mode} · last {when} UTC"
            ).format(
                count=len(previous),
                band=last.band,
                mode=last.mode,
                when=f"{last.qso_utc:%H:%M}" if rule == "day" else f"{last.qso_utc:%Y-%m-%d %H:%M}",
            )
            name = identity or last.name
            panel.feedback(f"{message} · {name}" if name else message, "dup")
            return

        worked = QsoService.find_duplicates(token, "", "")
        if worked:
            last = worked[0]
            message = _("Known: {count} previous QSO · last on {band}/{mode} on {date}").format(
                count=len(worked), band=last.band, mode=last.mode,
                date=f"{last.qso_utc:%Y-%m-%d}",
            )
            name = identity or last.name
            panel.feedback(f"{message} · {name}" if name else message, "info")
        elif identity:
            panel.feedback(_("Address book: {who}").format(who=identity), "info")
        else:
            panel.feedback("")

    @on(EntryPanel.Submitted)
    def _on_entry_submitted(self) -> None:
        panel = self.query_one(EntryPanel)
        history = self.query_one(HistoryPanel)
        values = panel.values()

        if self._view in LIST_VIEWS and not entry_parser.is_command(panel.first_value):
            if self._editing_id is None and not self._kind.can_add:
                return
            if self._editing_id is not None:
                self._save_item(self._editing_id, values)
            elif any(values.values()):
                self._save_item(None, values)
            return

        if self._bulk_ids:
            self._save_bulk(values)
            return
        if self._editing_id is not None:
            self._save_edit(self._editing_id, values)
            return

        # A command is typed in the leading box, on its own.
        first = panel.first_value
        if entry_parser.is_command(first):
            panel.clear()
            self._last_dup_check = ""
            self._run_command(first)
            return

        if not any(values.values()):
            return

        # Anything filled in is a new QSO, wherever the cursor happens to be.
        history.go_to_insert_row()

        self._line_history.append(values)
        del self._line_history[:-LINE_HISTORY_LIMIT]
        self._history_index = None
        self._last_dup_check = ""

        self._log_contact(values)

    def _log_contact(self, values: dict[str, str]) -> None:
        """Validate the form and store the QSO."""
        panel = self.query_one(EntryPanel)
        parsed = entry_parser.from_fields(
            values,
            mode_name=self.state.mode,
            validation=self.state.callsign_validation,
        )
        if not parsed.ok:
            panel.feedback(parsed.error or _("Invalid entry."), "error")
            return

        # Checked before logging: afterwards the entry may exist because
        # this very QSO created it.
        was_known = (
            ContactService.lookup(str(parsed.fields.get("call", ""))) is not None
            if self.state.add_to_book
            else True
        )

        try:
            row = QsoService.log(parsed.fields, self.state, digital=parsed.digital)
        except ServiceError as exc:
            panel.feedback(str(exc), "error")
            return

        panel.clear()
        self.query_one(HistoryPanel).append_row_for(row)
        self._refresh_stats()
        self._refresh_detail(None)

        message = _("✓ {call} logged at {time} UTC").format(
            call=row.call, time=f"{row.qso_utc:%H:%M:%S}"
        )
        if row.country:
            message += f" · {_(row.country)}"
        if not was_known:
            message += _(" · new in the address book")
        if row.name_drift:
            message += " · " + _("d: the address book says «{name}»").format(
                name=row.book_name
            )
        if parsed.warnings:
            panel.feedback(f"{message}   ⚠ {' '.join(parsed.warnings)}", "warning")
        else:
            panel.feedback(message, "ok")

    def _run_command(self, line: str) -> None:
        """Dispatch a ``/command`` typed in the entry line."""
        panel = self.query_one(EntryPanel)
        command = entry_parser.parse_command(line)
        action = COMMANDS.get(command.name)
        if action is None:
            panel.feedback(
                _("Unknown command: «/{command}». Type /help to see the list.").format(
                    command=command.name
                ),
                "error",
            )
            return

        if action == "help":
            panel.feedback(
                _(LIST_VIEWS[self._view][2] if self._view in LIST_VIEWS else COMMAND_SUMMARY),
                "info",
            )
            return
        if action == "brand":
            self._filter_brand(command.argument)
            return
        if action == "search":
            self._search(command.argument)
            return
        if action in COMMAND_USAGE and not command.argument:
            panel.feedback(_(COMMAND_USAGE[action]), "info")
            return

        if command.argument:
            if action == "band":
                self._apply_band(command.argument)
                return
            if action == "frequency":
                freq_hz = bands.parse_frequency(command.argument)
                if freq_hz is None:
                    panel.feedback(
                        _("Frequency not recognised: «{text}»").format(text=command.argument),
                        "error",
                    )
                    return
                self._apply_frequency(freq_hz)
                return
            if action == "mode":
                self._apply_mode(command.argument)
                return
            if action == "profiles":
                self._load_profile_by_name(command.argument)
                return
            if action == "repeater":
                self._load_repeater_by_callsign(command.argument)
                return

        getattr(self, f"action_{action}")()

    # ------------------------------------------------------------ actions --
    @property
    def _modal_open(self) -> bool:
        """True when a dialog sits on top of the main screen."""
        return len(self.screen_stack) > 1

    def _apply_band(self, band_name: str) -> None:
        band = bands.get(band_name)
        panel = self.query_one(EntryPanel)
        if band is None:
            panel.feedback(_("Unknown band: «{band}»").format(band=band_name), "error")
            return
        self.state.set_band(band.name)
        self.state.leave_profile()
        self._refresh_status()
        panel.feedback(
            _("Band {band} · {freq}").format(
                band=band.name, freq=bands.format_frequency(self.state.freq_hz)
            ),
            "ok",
        )
        panel.focus_input()

    def _apply_frequency(self, freq_hz: int) -> None:
        self.state.set_frequency(freq_hz)
        self.state.leave_profile()
        self._refresh_status()
        panel = self.query_one(EntryPanel)
        freq = bands.format_frequency(freq_hz)
        if self.state.band:
            panel.feedback(
                _("Frequency {freq} · band {band}").format(freq=freq, band=self.state.band),
                "ok",
            )
        else:
            panel.feedback(_("Frequency {freq} · out of band").format(freq=freq), "warning")
        panel.focus_input()

    def _apply_mode(self, mode_name: str) -> None:
        mode = modes.get(mode_name)
        panel = self.query_one(EntryPanel)
        if mode is None:
            panel.feedback(_("Unknown mode: «{mode}»").format(mode=mode_name), "error")
            return
        self.state.set_mode(mode.name)
        self.state.leave_profile()
        self._refresh_status()
        if mode.digital_fields:
            self._ask_digital_fields(mode)
        else:
            panel.feedback(_("Mode {mode}").format(mode=mode.name), "ok")
            panel.focus_input()

    def _ask_digital_fields(self, mode: modes.Mode) -> None:
        """Ask for the values a digital mode needs (talkgroup, reflector...)."""
        fields = [
            Field(key, _(label), self.state.digital_data.get(key, ""))
            for key, label in mode.digital_fields
        ]
        self.push_screen(
            FormScreen(
                _("{mode} settings").format(mode=mode.name),
                fields,
                subtitle=_(
                    "They apply to every contact until you change them. "
                    "They are exported as APP_HAMRLOG_* fields in ADIF."
                ),
                save_label=_("Apply"),
            ),
            self._on_digital_fields,
        )

    def _on_digital_fields(self, values: dict[str, str] | None) -> None:
        panel = self.query_one(EntryPanel)
        if values is not None:
            self.state.digital_data = {k: v for k, v in values.items() if v}
        self._refresh_status()
        panel.feedback(_("Mode {mode} set up").format(mode=self.state.mode), "ok")
        panel.focus_input()

    def _apply_repeater(self, repeater_id: int) -> None:
        panel = self.query_one(EntryPanel)
        try:
            RepeaterService.apply_to_state(repeater_id, self.state)
        except ServiceError as exc:
            panel.feedback(str(exc), "error")
            return
        self.state.leave_profile()
        self._refresh_status()
        repeater = RepeaterService.get(repeater_id)
        if repeater is not None:
            detail = _("listen {rx} · transmit {tx}").format(
                rx=bands.format_frequency(repeater.output_hz),
                tx=bands.format_frequency(repeater.input_hz),
            )
            if repeater.ctcss_tx:
                detail += _(" · tone {tone}").format(tone=repeater.ctcss_tx)
            panel.feedback(
                _("Via repeater {call} · {detail}").format(
                    call=repeater.callsign, detail=detail
                ),
                "ok",
            )
        panel.focus_input()

    def action_direct(self) -> None:
        """Leave the repeater and work simplex on the current frequency."""
        panel = self.query_one(EntryPanel)
        if not self.state.via_repeater:
            panel.feedback(_("You were already working direct."), "info")
            panel.focus_input()
            return
        self.state.clear_repeater()
        self.state.leave_profile()
        self._refresh_status()
        panel.feedback(
            _("Direct on {freq} (simplex)").format(
                freq=bands.format_frequency(self.state.freq_hz)
            ),
            "ok",
        )
        panel.focus_input()

    def _load_repeater_by_callsign(self, call: str) -> None:
        """``/repeater ED7ZAE``; ``/repeater ED4ZAH DMR`` when a callsign names several."""
        panel = self.query_one(EntryPanel)
        try:
            repeater = RepeaterService.resolve(call)
        except ServiceError as exc:
            panel.feedback(str(exc), "error")
            return
        self._apply_repeater(repeater.id)

    def _load_profile_by_name(self, name: str) -> None:
        """``/profile HF-Casa``, or ``/profile 3`` for the one on Ctrl+3."""
        panel = self.query_one(EntryPanel)
        needle = name.strip().lower()
        if needle.isdigit() and len(needle) == 1:
            self.action_activate_slot(int(needle))
            return
        for profile in ProfileService.list_all():
            if profile.name.lower() == needle:
                self._activate_profile(profile.id)
                return
        panel.feedback(_("There is no profile «{name}»").format(name=name), "error")

    def action_activate_slot(self, slot: int) -> None:
        """Ctrl+0 to Ctrl+9: activate the profile holding that key."""
        if self._modal_open:
            raise SkipAction()
        profile = ProfileService.get_by_slot(slot)
        if profile is None:
            self.query_one(EntryPanel).feedback(
                _("No profile has the key Ctrl+{key}; give it one in Profiles (F4).").format(
                    key=slot
                ),
                "warning",
            )
            return
        self._activate_profile(profile.id)

    def _activate_profile(self, profile_id: int) -> None:
        """Make a profile the active one: every new QSO inherits it."""
        panel = self.query_one(EntryPanel)
        try:
            ProfileService.apply_to_state(profile_id, self.state)
        except ServiceError as exc:
            panel.feedback(str(exc), "error")
            return
        self._refresh_status()
        if self._view == "profiles" and self._editing_id is None:
            item = self.query_one(ItemTable).selected_item()
            self._reload_items(keep_id=item.id if item else None)
            # The reload lands on the same row: this message must stay.
            self._showing_selection = False
        panel.feedback(
            _("Profile «{name}» active: new QSOs are logged with it").format(
                name=self.state.profile_name
            ),
            "ok",
        )
        panel.focus_input()

    async def action_back_to_entry(self) -> None:
        """Escape leaves the list and returns to the insert row.

        While editing it only abandons the edit, staying on that row. It never
        changes view: that is what the function keys are for.
        """
        if self._modal_open:
            return
        if self._editing_id is not None:
            self._end_edit()
            self.query_one(EntryPanel).feedback(_("Edit cancelled."), "info")
            return
        if self._view in LIST_VIEWS:
            table = self.query_one(ItemTable)
            if not table.on_insert_row:
                table.go_to_insert_row()
            return
        self.query_one(HistoryPanel).go_to_insert_row()

    @on(BrowseBar.Action)
    def _on_browse_action(self, event: BrowseBar.Action) -> None:
        """D, E and R act on the QSO the cursor is sitting on."""
        if self._view in LIST_VIEWS:
            self._on_item_action(event.action)
            return
        history = self.query_one(HistoryPanel)
        row = history.selected_row()
        if row is None or event.action in ("activate", "default"):
            return
        if event.action == "view":
            self._show_card(row.id)
        elif event.action == "delete":
            self._confirm_delete(row.id)
        elif event.action == "edit":
            if len(history.marked) > 1:
                self._begin_bulk_edit(history.marked_rows())
            else:
                self._begin_edit(row)
        elif event.action == "repeat":
            self._repeat_qso(row)
        elif event.action == "mark":
            history.toggle_mark(row.id)
            self._report_marks()

    def _show_card(self, qso_id: int) -> None:
        """V: everything about the QSO, in a card over the log."""
        card = QsoService.card(qso_id)
        if card is None:
            return
        self.push_screen(QsoCardScreen(card))

    def action_mark_all(self) -> None:
        """Ctrl+A: select every QSO of the log, or none if all were."""
        if self._modal_open or self._view != "log" or self._editing_id is not None:
            raise SkipAction()
        history = self.query_one(HistoryPanel)
        history.toggle_all()
        self._report_marks()

    def _report_marks(self) -> None:
        count = len(self.query_one(HistoryPanel).marked)
        self._showing_selection = False
        self.query_one(EntryPanel).feedback(
            _("{count} QSOs selected · E edits frequency, mode and setup of all of them").format(
                count=count
            )
            if count > 1
            else _("{count} QSO selected").format(count=count),
            "info",
        )

    def _begin_bulk_edit(self, rows: list) -> None:  # type: ignore[type-arg]
        """Edit several QSOs at once: only their frequency, mode and setup.

        A box starts with the value the QSOs share, or empty when they differ;
        an empty box leaves each QSO as it was.
        """
        def shared(values: list[str]) -> str:
            return values[0] if len(set(values)) == 1 else ""

        values = {
            "freq_hz": shared(
                [bands.format_frequency(r.freq_hz) if r.freq_hz else "" for r in rows]
            ),
            "mode": shared([r.mode for r in rows]),
            "equipment": shared([r.equipment_name for r in rows]),
        }
        panel = self.query_one(EntryPanel)
        self._bulk_ids = [r.id for r in rows]
        self._editing_id = rows[0].id
        self._showing_selection = False
        panel.set_suggesters(
            {"equipment": ListSuggester([e.name for e in EquipmentService.list_all()])}
        )
        panel.start_edit(values, bulk=True)
        panel.feedback(
            _("Editing {count} QSOs: only frequency, mode and setup; "
              "an empty box leaves them as they are").format(count=len(rows)),
            "info",
        )

    def _save_bulk(self, values: dict[str, str]) -> None:
        """Apply the frequency, mode and setup typed to every selected QSO."""
        panel = self.query_one(EntryPanel)
        changes: dict[str, object] = {}
        freq_text = values.get("freq_hz", "").strip()
        if freq_text:
            freq_hz = bands.parse_frequency(freq_text)
            if freq_hz is None:
                panel.feedback(
                    _("Frequency not recognised: «{text}»").format(text=freq_text), "error"
                )
                return
            found = bands.from_frequency(freq_hz)
            changes.update(freq_hz=freq_hz, band=found.name if found else "")
        mode_text = values.get("mode", "").strip()
        if mode_text:
            mode = modes.get(mode_text)
            if mode is None:
                panel.feedback(_("Unknown mode: «{mode}»").format(mode=mode_text), "error")
                return
            changes["mode"] = mode.name
        setup_text = values.get("equipment", "").strip()
        if setup_text:
            setup = next(
                (e for e in EquipmentService.list_all() if e.name.lower() == setup_text.lower()),
                None,
            )
            if setup is None:
                panel.feedback(
                    _("There is no setup «{name}».").format(name=setup_text), "error"
                )
                return
            changes["equipment_id"] = setup.id
        if not changes:
            self._end_edit()
            panel.feedback(_("No changes."), "info")
            return

        history = self.query_one(HistoryPanel)
        updated_rows = []
        for qso_id in self._bulk_ids:
            row = QsoService.get(qso_id)
            if row is None:
                continue
            own = dict(changes)
            if "freq_hz" in own and own["freq_hz"] != row.freq_hz and row.repeater_call:
                # The repeater no longer describes where this QSO took place.
                own.update(freq_tx_hz=None, repeater_id=None, repeater_call="")
            try:
                updated_rows.append(QsoService.update(qso_id, own))
            except ServiceError as exc:
                panel.feedback(str(exc), "error")
                return
        self._end_edit()
        for updated in updated_rows:
            history.replace_row(updated)
        current = history.selected_row()
        if current is not None:
            self._refresh_detail(current)
        self._refresh_stats()
        flagged = sum(1 for r in updated_rows if r.equipment_mismatch)
        message = _("✓ {count} QSOs updated").format(count=len(updated_rows))
        if flagged:
            panel.feedback(
                message + "   ⚠ " + _("{count} marked E: the setup does not fit their frequency")
                .format(count=flagged),
                "warning",
            )
        else:
            panel.feedback(message, "ok")

    @on(BrowseBar.UnknownKey)
    def _on_unknown_browse_key(self) -> None:
        """Remind the operator that the line is not a text field right now."""
        if self._view in LIST_VIEWS:
            self.query_one(EntryPanel).feedback(
                _("You are on an item of the list: {keys}").format(keys=_(self._browse_prompt)),
                "warning",
            )
            return
        self.query_one(EntryPanel).feedback(
            _(
                "You are on a logged QSO: D delete · E edit · R repeat · "
                "↓ to «<Insert new>» to type"
            ),
            "warning",
        )

    def _begin_edit(self, row) -> None:  # type: ignore[no-untyped-def]
        """Load the browsed QSO into the entry form to correct it in place.

        Everything the form shows can change; the timestamp is not in it.
        """
        panel = self.query_one(EntryPanel)
        order = self.state.field_order + tuple(
            name for name in EDIT_EXTRA_FIELDS if name not in self.state.field_order
        )
        values = entry_parser.values_from_row(row, order)
        for key in entry_parser.DIGITAL_KEYS:
            if key in values:
                values[key] = str((row.digital_data or {}).get(key, ""))
        values["equipment"] = row.equipment_name
        self._editing_id = row.id
        self._showing_selection = False
        panel.set_suggesters(
            {"equipment": ListSuggester([e.name for e in EquipmentService.list_all()])}
        )
        panel.start_edit(values)
        panel.feedback(
            _("Editing the QSO with {call} at {time} UTC").format(
                call=row.call, time=f"{row.qso_utc:%H:%M:%S}"
            ),
            "info",
        )

    def _end_edit(self) -> None:
        self._editing_id = None
        self._bulk_ids = []
        self.query_one(EntryPanel).stop_edit()

    def _save_edit(self, qso_id: int, values: dict[str, str]) -> None:
        """Validate the form and write it over the QSO being edited."""
        panel = self.query_one(EntryPanel)
        row = QsoService.get(qso_id)
        if row is None:
            self._end_edit()
            panel.feedback(_("That QSO no longer exists."), "warning")
            return

        # The set is not a QSO field the parser knows: it is looked up by name.
        values = dict(values)
        equipment_text = values.pop("equipment", "").strip()
        equipment = None
        if equipment_text:
            equipment = next(
                (
                    candidate
                    for candidate in EquipmentService.list_all()
                    if candidate.name.lower() == equipment_text.lower()
                ),
                None,
            )
            if equipment is None:
                panel.feedback(
                    _("There is no setup «{name}».").format(name=equipment_text), "error"
                )
                return

        parsed = entry_parser.from_fields(
            values,
            mode_name=values.get("mode") or row.mode,
            validation=self.state.callsign_validation,
        )
        if not parsed.ok:
            panel.feedback(parsed.error or _("Invalid entry."), "error")
            return
        # The parser drops a value it cannot read and warns; saving then would
        # silently blank a field the operator meant to change.
        unreadable = [
            name for name in ("freq_hz", "mode", "power_w")
            if values.get(name) and name not in parsed.fields
        ]
        if unreadable:
            panel.feedback(" ".join(parsed.warnings), "error")
            return

        changes: dict[str, object] = {}
        for name in values:
            # The band is never typed here, even when the entry line has a box
            # for it: it is worked out from the frequency below.
            if name in entry_parser.DIGITAL_KEYS or name == "band":
                continue
            empty = None if name in ("freq_hz", "power_w") else ""
            changes[name] = parsed.fields.get(name, empty)

        warnings = list(parsed.warnings)
        new_freq = changes.get("freq_hz", row.freq_hz)
        moved = new_freq != row.freq_hz
        if moved and new_freq:
            found = bands.from_frequency(int(new_freq))  # type: ignore[call-overload]
            changes["band"] = found.name if found else ""
            if found is None:
                warnings.append(_("Frequency outside the amateur radio bands."))
        if moved and row.repeater_call:
            # The repeater no longer describes where this QSO took place.
            changes.update(freq_tx_hz=None, repeater_id=None, repeater_call="")

        digital_keys = [name for name in values if name in entry_parser.DIGITAL_KEYS]
        if digital_keys:
            digital = dict(row.digital_data or {})
            for key in digital_keys:
                if values[key]:
                    digital[key] = values[key]
                else:
                    digital.pop(key, None)
            changes["digital_data"] = digital

        changes = {
            key: value
            for key, value in changes.items()
            if key == "repeater_id" or getattr(row, key, None) != value
        }
        if (equipment.name if equipment else "") != row.equipment_name:
            changes["equipment_id"] = equipment.id if equipment else None
        if not set(changes) - {"repeater_id"}:
            self._end_edit()
            panel.feedback(_("No changes."), "info")
            return

        try:
            updated = QsoService.update(qso_id, changes)
        except ServiceError as exc:
            panel.feedback(str(exc), "error")
            return

        self._end_edit()
        self.query_one(HistoryPanel).replace_row(updated)
        self._refresh_detail(updated)
        self._refresh_stats()
        message = _("✓ {call} updated").format(call=updated.call)
        if moved and updated.band:
            message += _(" · band {band}").format(band=updated.band)
        if updated.equipment_mismatch:
            warnings.append(
                _("The frequency does not fit the setup «{name}»; the QSO is marked E.").format(
                    name=updated.equipment_name
                )
            )
        if warnings:
            panel.feedback(f"{message}   ⚠ {' '.join(warnings)}", "warning")
        else:
            panel.feedback(message, "ok")

    def _repeat_qso(self, row) -> None:  # type: ignore[no-untyped-def]
        """Load a logged QSO back into the entry line as a new one.

        The line comes back editable with the current field order, so the
        operator changes what differs and presses Enter. It is logged with the
        session's band and mode, not the old ones: repeating means working the
        same station again, now.
        """
        panel = self.query_one(EntryPanel)
        self.query_one(HistoryPanel).go_to_insert_row()
        panel.set_values(entry_parser.values_from_row(row, self.state.field_order))
        self._showing_selection = False
        # No message here on purpose: filling the line is the confirmation,
        # and the duplicate check that fires next says something more useful.

    def action_delete_qso(self) -> None:
        """Delete a QSO without leaving the main screen.

        Which one depends on where the focus is: the highlighted row when the
        operator is browsing the history, otherwise the most recent QSO,
        which is the one they just mistyped.

        The binding needs priority because Input already uses Ctrl+D to delete
        forwards, which would otherwise swallow it. That means it also fires
        over an open dialog, where it does nothing.
        """
        if self._modal_open:
            return
        if self._view in LIST_VIEWS:
            self._on_item_action("delete")
            return
        history = self.query_one(HistoryPanel)
        qso_id = history.selected_qso_id()
        if qso_id is None:
            recent = QsoService.recent(limit=1)
            if not recent:
                self.query_one(EntryPanel).feedback(_("No QSO to delete."), "warning")
                return
            qso_id = recent[-1].id
        self._confirm_delete(qso_id)

    #: ``/undo`` is the command form of the same action.
    action_undo = action_delete_qso

    def _confirm_delete(self, qso_id: int) -> None:
        """Ask before removing a QSO, naming it so there is no doubt."""
        row = QsoService.get(qso_id)
        panel = self.query_one(EntryPanel)
        if row is None:
            panel.feedback(_("That QSO no longer exists."), "warning")
            return
        self.push_screen(
            ConfirmScreen(
                _("Delete the QSO with {call}?").format(call=row.call),
                detail=f"{row.qso_utc:%Y-%m-%d %H:%M:%S} UTC · {row.band or '-'} · "
                f"{row.mode or '-'}" + (f" · {row.name}" if row.name else ""),
                danger=True,
            ),
            lambda confirmed: self._do_delete(qso_id, confirmed),
        )

    def _do_delete(self, qso_id: int, confirmed: bool | None) -> None:
        panel = self.query_one(EntryPanel)
        if confirmed:
            try:
                QsoService.delete(qso_id)
            except ServiceError as exc:
                panel.feedback(str(exc), "error")
                return
            self._reload_history()
            self._refresh_stats()
            panel.feedback(_("QSO deleted."), "ok")
        panel.focus_input()

    # ---------------------------------------------------------- equipment --
    @property
    def _kinds(self) -> tuple[Kind, ...]:
        return LIST_VIEWS[self._view][0]

    @property
    def _tab(self) -> int:
        return self._tabs.get(self._view, 0)

    @property
    def _kind(self) -> Kind:
        return self._kinds[self._tab]

    def _draft_key(self) -> str:
        return "log" if self._view == "log" else self._kind.key

    @property
    def _browse_prompt(self) -> str:
        """What the action bar offers over an item of the list view in use."""
        return BROWSE_PROMPTS.get(self._view, INVENTORY_BROWSE)

    async def action_function_key(self, key: str) -> None:
        """Function keys, page keys and tab keys.

        Each function key opens its view from any other (F1 the log, F2 the
        inventory...); the one of the view in use, or one no view has, does
        nothing. Page Up and Page Down page through the list in view. Ctrl+N
        cycles through the tabs of a view that has them, and Shift+Page
        Up/Down go back and forth. Over a dialog the key is passed on to it.
        """
        if self._modal_open:
            raise SkipAction()
        if key == "f1" or key in VIEW_KEYS:
            view = "log" if key == "f1" else VIEW_KEYS[key]
            if view != self._view:
                await self._show_view(view)
            return
        if key in ("pageup", "pagedown"):
            if self._editing_id is None:
                self._page(-1 if key == "pageup" else 1)
            return
        if self._view == "log":
            return
        tabs = len(self._kinds)
        if tabs < 2:
            return
        if key == "shift+pageup":
            await self._show_view(self._view, (self._tab - 1) % tabs)
        elif key in ("ctrl+n", "shift+pagedown"):
            await self._show_view(self._view, (self._tab + 1) % tabs)

    def _page(self, direction: int) -> None:
        """Move the cursor of the list in view by as many rows as it shows."""
        table = (
            self.query_one(HistoryPanel)
            if self._view == "log"
            else self.query_one(ItemTable)
        )
        # The header takes one row of the table's height. Paging lands on a
        # row, where the action bar takes one more, so the page is measured
        # with the bar in place and Page Up and Page Down stay symmetric.
        bar = 0 if self.query_one("#entry-browse", BrowseBar).display else 1
        rows = max(1, table.size.height - 1 - bar)
        table.move_selection(direction * rows)

    async def _show_view(self, view: str, tab: int | None = None) -> None:
        """Turn the main frame and the entry line to the log or to a tab.

        What was half typed is put aside per view and tab and comes back on
        return, as browsing does with the insert row.
        """
        panel = self.query_one(EntryPanel)
        self._drafts[self._draft_key()] = panel.pending_values
        self._editing_id = None
        self._showing_selection = False
        panel.reset()

        self._view = view
        if tab is not None:
            self._tabs[view] = tab
        in_log = view == "log"
        self.query_one(HistoryPanel).display = in_log
        self.query_one(InventoryView).display = not in_log
        self.query_one("#log-frame", Vertical).border_title = (
            _("Log") if in_log else _(LIST_VIEWS[view][1])
        )

        if in_log:
            panel.build_fields(self.state.field_order)
            panel.set_browse_prompt(BROWSE_PROMPT)
            self._refresh_detail(None)
        else:
            panel.build_fields(self._kind.fields, extra=(), second=self._kind.second_row)
            panel.set_browse_prompt(self._browse_prompt)
            self._reload_items()
        await panel.ready()
        panel.set_suggesters({} if in_log else self._kind.suggesters())
        panel.set_values(self._drafts.get(self._draft_key(), {}))
        panel.feedback("")
        if in_log:
            # Other views change what the rows show: a contact's name decides
            # the d flag, a setup's name and parts the setup column and E.
            # Reloading leaves the cursor on the insert row.
            self._reload_history()

    def _reload_items(self, keep_id: int | None = None) -> None:
        kind = self._kind
        if isinstance(kind, ProfileKind):
            kind.active_id = self.state.profile_id
        query = self._queries.get(kind.key, "")
        items = kind.items(query)
        brand = self._brands.get(kind.key, "")
        if brand:
            items = [item for item in items if item.brand.lower() == brand.lower()]
        total = kind.total(query) if kind.searchable or not kind.browses else len(items)
        self.query_one(InventoryView).set_tabs(
            profile_legend()
            if isinstance(kind, ProfileKind)
            else tab_bar(
                self._kinds,
                self._tab,
                brand,
                query,
                len(items),
                total,
                show_tabs=self._view in TABBED_VIEWS,
            )
        )
        self.query_one(ItemTable).show(kind, items, keep_id)

    def _filter(self, text: str) -> None:
        """Narrow a read-only list as its box is typed in."""
        if entry_parser.is_command(text):
            return
        self._queries[self._kind.key] = text.strip()
        self._reload_items()

    def _search(self, text: str) -> None:
        """``/search TEXT``: narrow a searchable list; alone, show all of it."""
        panel = self.query_one(EntryPanel)
        if self._view not in LIST_VIEWS or not self._kind.searchable:
            panel.feedback(
                _("/search is for the address book (F3) and the repeaters (F5)."), "warning"
            )
            return
        self._queries[self._kind.key] = text.strip()
        self._reload_items()
        table = self.query_one(ItemTable)
        panel.feedback(
            _("{count} found for «{text}».").format(count=table.item_count, text=text.strip())
            if text.strip()
            else _("Showing everything."),
            "info",
        )

    @on(ItemTable.SelectionChanged)
    def _on_item_selection(self, event: ItemTable.SelectionChanged) -> None:
        panel = self.query_one(EntryPanel)
        detail = self.query_one(DetailPanel)
        if event.item is None:
            self._editing_id = None
            panel.set_browsing(False)
            detail.show_lines(tuple(_(line) for line in self._kind.guide))
            if self._showing_selection:
                panel.feedback("")
                self._showing_selection = False
            return
        detail.show_lines(event.item.detail, locked=event.item.locked)
        if not self._kind.browses:
            # A read-only list: the box keeps the keyboard to filter it.
            return
        panel.set_browsing(True)
        if self._showing_selection:
            panel.feedback("")
        self._showing_selection = True

    def _on_item_action(self, action: str) -> None:
        item = self.query_one(ItemTable).selected_item()
        panel = self.query_one(EntryPanel)
        if item is None:
            return
        profiles = self._view == "profiles"
        if self._view == "settings":
            if action in ("activate", "edit"):
                action = "edit"
            elif action == "delete":
                panel.feedback(_("Settings are changed, not deleted."), "warning")
                return
        if action == "activate":
            if profiles:
                self._activate_profile(item.id)
            elif self._view == "repeaters":
                self._apply_repeater(item.id)
            return
        if action == "default" and profiles:
            self._toggle_default(item)
            return
        if action in ("mark", "default"):
            panel.feedback(
                _("Nothing to select here: {keys}").format(keys=_(self._browse_prompt)),
                "warning",
            )
            return
        if action == "repeat":
            panel.feedback(
                _("Nothing to repeat here: {keys}").format(keys=_(self._browse_prompt)),
                "warning",
            )
            return
        if action == "view":
            panel.feedback(
                _("V opens the card of a QSO, in the log: {keys}").format(
                    keys=_(self._browse_prompt)
                ),
                "warning",
            )
            return
        if item.locked:
            message = (
                _(
                    "{name} is from the URE list: it is neither changed nor deleted. "
                    "Enter tunes it."
                )
                if self._view == "repeaters"
                else _(
                    "«{name}» is from the catalog: it is neither changed nor deleted. "
                    "Use it when putting a setup together."
                )
            )
            panel.feedback(message.format(name=item.name), "warning")
            return
        if action == "edit":
            self._editing_id = item.id
            self._showing_selection = False
            panel.start_edit(self._kind.values(item.id))
            panel.feedback(_("Editing «{name}»").format(name=item.name), "info")
        elif action == "delete":
            self.push_screen(
                ConfirmScreen(
                    _("Delete «{name}»?").format(name=item.name),
                    detail=_("{kind}: only this item is deleted.").format(
                        kind=_(self._kind.title)
                    ),
                    danger=True,
                ),
                lambda confirmed: self._do_delete_item(item, confirmed),
            )

    def _toggle_default(self, item: Item) -> None:
        """``*``: the profile activated on start, or none if it already was."""
        panel = self.query_one(EntryPanel)
        default = ProfileService.get_default()
        if default is not None and default.id == item.id:
            ProfileService.clear_default()
            message = _("No profile is activated on start now.")
        else:
            ProfileService.set_default(item.id)
            message = _("«{name}» is activated on start.").format(name=item.name)
        self._reload_items(keep_id=item.id)
        self._showing_selection = False
        panel.feedback(message, "ok")

    def _do_delete_item(self, item: Item, confirmed: bool | None) -> None:
        panel = self.query_one(EntryPanel)
        if confirmed:
            try:
                self._kind.delete(item.id)
            except ServiceError as exc:
                panel.feedback(str(exc), "error")
                panel.focus_input()
                return
            if self._view == "profiles" and item.id == self.state.profile_id:
                # Its values stay in use; they just belong to no profile now.
                self.state.leave_profile()
            self._reload_items()
            self._refresh_status()
            panel.feedback(_("«{name}» deleted.").format(name=item.name), "ok")
        panel.focus_input()

    def _save_item(self, item_id: int | None, values: dict[str, str]) -> None:
        """Create or update an item of the active tab from the entry line."""
        panel = self.query_one(EntryPanel)
        try:
            name = self._kind.save(item_id, values)
        except ServiceError as exc:
            panel.feedback(str(exc), "error")
            return
        if item_id is not None:
            self._end_edit()
            if self._view == "profiles" and item_id == self.state.profile_id:
                # The QSOs logged from now on take the new values.
                ProfileService.apply_to_state(item_id, self.state)
            self._reload_items(keep_id=item_id)
            message = _("✓ «{name}» updated").format(name=name)
            if self._view == "settings":
                self._apply_config()
                if item_id in (DATABASE_SETTING, LANGUAGE_SETTING):
                    message = f"{message} · {_(RESTART_NOTE)}"
            panel.feedback(message, "ok")
        else:
            panel.clear()
            self._reload_items()
            panel.feedback(_("✓ «{name}» added").format(name=name), "ok")
        panel.set_suggesters(self._kind.suggesters())
        self._refresh_status()

    def _filter_brand(self, text: str) -> None:
        """``/brand Icom``: show only that brand; ``/brand`` alone shows all."""
        panel = self.query_one(EntryPanel)
        kind = self._kind
        if self._view != "inventory" or not kind.has_brands:
            panel.feedback(
                _("The brand filter is for Radios, Antennas and Supplies (F2)."), "warning"
            )
            return
        needle = text.strip().lower()
        if not needle:
            self._brands[kind.key] = ""
            self._reload_items()
            panel.feedback(_("All brands."), "info")
            return
        brands = self._brand_list()
        match = next((b for b in brands if b.lower() == needle), None) or next(
            (b for b in brands if b.lower().startswith(needle)), None
        )
        if match is None:
            panel.feedback(
                _("No {items} of the brand «{brand}».").format(
                    items=_(kind.title).lower(), brand=text.strip()
                ),
                "error",
            )
            return
        self._brands[kind.key] = match
        self._reload_items()
        panel.feedback(_("Brand {brand}.").format(brand=match), "info")

    def _brand_list(self) -> list[str]:
        return sorted({item.brand for item in self._kind.items() if item.brand}, key=str.lower)

    def action_cycle_brand(self, delta: int) -> None:
        """Alt+↑ Alt+↓ walk the brands of the tab, with «all» in between."""
        if self._view != "inventory" or self._modal_open or not self._kind.has_brands:
            return
        if self._editing_id is not None:
            return
        options = ["", *self._brand_list()]
        current = self._brands.get(self._kind.key, "")
        index = options.index(current) if current in options else 0
        self._brands[self._kind.key] = options[(index + delta) % len(options)]
        self._reload_items()

    def action_quit(self) -> None:  # type: ignore[override]
        self._persist_state()
        self.exit()

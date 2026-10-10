"""The repeaters view (F5): what to tune to work through each repeater.

One list, searched like the address book: callsign, URE number, channel,
club, place, locator or mode. The repeaters of the URE list are loaded as a
read-only catalog (``data/preseed/repetidores.json``); the operator adds the
ones missing from it. Enter on a repeater puts the session on it, as
``/repeater`` does.
"""

from __future__ import annotations

from textual.suggester import Suggester

from ..core import bands, modes, repeaters
from ..core.services import RepeaterService, ServiceError
from ..db.models import Repeater
from ..i18n import N_, _
from .inventory import Item, Kind, ListSuggester
from .profiles import format_digital, parse_digital

#: Most repeaters the list holds at once; the URE list is well below it.
LIMIT = 1000

URE_LIST_NOTE = N_("from the URE list: Enter tunes it; it cannot be changed or deleted")


def _frequency(text: str, what: str) -> int | None:
    clean = text.strip()
    if not clean:
        return None
    freq_hz = bands.parse_frequency(clean)
    if freq_hz is None:
        raise ServiceError(
            _("{what}: frequency not recognised: «{text}»").format(what=what, text=clean)
        )
    return freq_hz


def _input(text: str, output_hz: int) -> int | None:
    """The input box: a frequency, or a shift when it starts with a sign."""
    clean = text.strip()
    if not clean:
        return None
    if clean[0] in "+-":
        shift_hz = repeaters.parse_shift(clean)
        if shift_hz is None:
            raise ServiceError(_("Shift not recognised: «{text}»").format(text=clean))
        return output_hz + shift_hz
    return _frequency(clean, _("Input"))


def _tone(repeater: Repeater) -> str:
    return repeater.ctcss_tx or (f"D{repeater.dcs}" if repeater.dcs else "")


class RepeaterKind(Kind):
    key = "repeaters"
    title = N_("Repeaters")
    insert_label = N_("<New repeater>")
    guide = (
        N_("NEW REPEATER   callsign and output; the input defaults to the band's shift"),
        N_("INPUT: frequency, or shift (-600, -7.6 MHz) · TONE: CTCSS in Hz · URE: R5, R73"),
        N_("/search TEXT looks up callsign, URE number, channel, club, place, locator or mode"),
    )
    fields = ("call", "output", "input", "tone", "mode", "ure_number", "channel")
    second_row = ("owner", "qth", "gridsquare", "digital", "notes")
    # Narrow enough to leave the owner some room at 80 columns.
    columns = (
        ("URE", 4),
        (N_("CALLSIGN"), 10),
        (N_("OUTPUT"), 9),
        (N_("INPUT"), 9),
        (N_("TONE"), 5),
        (N_("MODE"), 5),
        (N_("CHANNEL"), 5),
        (N_("GRID"), 6),
        (N_("OWNER"), None),
    )
    #: The owner column, the only one wide enough for «<New repeater>».
    insert_column = 8
    searchable = True
    count_text = N_("{total} repeaters")

    def items(self, query: str = "") -> list[Item]:
        rows = []
        for repeater in RepeaterService.search(query, limit=LIMIT):
            output = bands.format_frequency(repeater.output_hz, with_unit=False)
            tx = bands.format_frequency(repeater.input_hz, with_unit=False)
            tone = _tone(repeater)
            digital = modes.status_summary(dict(repeater.digital_data or {}))
            place = " · ".join(p for p in (repeater.qth, repeater.gridsquare) if p)
            rows.append(
                Item(
                    repeater.id,
                    repeater.callsign,
                    (
                        repeater.ure_number,
                        repeater.callsign,
                        output,
                        tx if repeater.shift_hz else "",
                        tone,
                        repeater.mode,
                        repeater.channel,
                        repeater.gridsquare,
                        repeater.name,
                    ),
                    (
                        " · ".join(
                            p
                            for p in (
                                repeater.callsign,
                                repeater.ure_number,
                                repeater.channel,
                                repeater.name,
                            )
                            if p
                        ),
                        " · ".join(
                            p
                            for p in (
                                _("listen {rx} · transmit {tx}").format(
                                    rx=bands.format_frequency(repeater.output_hz),
                                    tx=bands.format_frequency(repeater.input_hz),
                                ),
                                repeaters.format_shift(repeater.shift_hz),
                                repeater.mode,
                                _("tone {tone} Hz").format(tone=repeater.ctcss_tx)
                                if repeater.ctcss_tx
                                else "",
                                digital,
                            )
                            if p
                        ),
                        " · ".join(
                            p
                            for p in (
                                place,
                                repeater.notes,
                                _(URE_LIST_NOTE) if repeater.preset else _("Enter tunes it"),
                            )
                            if p
                        ),
                    ),
                    locked=repeater.preset,
                )
            )
        return rows

    def total(self, query: str = "") -> int:
        return RepeaterService.count(query)

    def values(self, item_id: int) -> dict[str, str]:
        repeater = RepeaterService.get(item_id)
        if repeater is None:
            return {}
        return {
            "call": repeater.callsign,
            "output": bands.format_frequency(repeater.output_hz, with_unit=False),
            "input": bands.format_frequency(repeater.input_hz, with_unit=False),
            "tone": repeater.ctcss_tx,
            "mode": repeater.mode,
            "ure_number": repeater.ure_number,
            "channel": repeater.channel,
            "owner": repeater.name,
            "qth": repeater.qth,
            "gridsquare": repeater.gridsquare,
            "digital": format_digital(dict(repeater.digital_data or {})),
            "notes": repeater.notes,
        }

    def save(self, item_id: int | None, values: dict[str, str]) -> str:
        output_hz = _frequency(values.get("output", ""), _("Output"))
        if output_hz is None:
            raise ServiceError(_("The repeater output frequency is missing."))
        input_hz = _input(values.get("input", ""), output_hz)

        mode_name = "FM"
        mode_text = values.get("mode", "").strip()
        if mode_text:
            mode = modes.get(mode_text)
            if mode is None:
                raise ServiceError(_("Unknown mode: «{mode}»").format(mode=mode_text))
            mode_name = mode.name

        fields = dict(
            name=values.get("owner", "").strip(),
            mode=mode_name,
            ctcss_tx=values.get("tone", ""),
            ure_number=values.get("ure_number", "").strip().upper(),
            channel=values.get("channel", "").strip().upper(),
            qth=values.get("qth", "").strip(),
            gridsquare=values.get("gridsquare", "").strip().upper(),
            digital_data=parse_digital(values.get("digital", ""), mode_name),
            notes=values.get("notes", "").strip(),
        )
        call = values.get("call", "")
        if item_id is None:
            return RepeaterService.create(
                call, output_hz=output_hz, input_hz=input_hz, **fields
            ).callsign
        if input_hz is None:
            band = bands.from_frequency(output_hz)
            input_hz = output_hz + repeaters.default_shift(band.name if band else "")
        return RepeaterService.update(
            item_id, callsign=call, output_hz=output_hz, input_hz=input_hz, **fields
        ).callsign

    def delete(self, item_id: int) -> None:
        RepeaterService.delete(item_id)

    def suggesters(self) -> dict[str, Suggester]:
        return {
            "mode": ListSuggester([mode.name for mode in modes.MODES]),
            "tone": ListSuggester(list(repeaters.CTCSS_TONES)),
        }


#: The repeaters view has a single list.
REPEATER_KINDS: tuple[Kind, ...] = (RepeaterKind(),)

"""The address book view (F3): who each callsign is.

One list, searched rather than shown whole: a DMR user list loaded into the
book can hold tens of thousands of entries, so the view shows the first
LIMIT matches of the current /search, ordered by callsign.
"""

from __future__ import annotations

from typing import Any

from ..core.services import ContactService, ServiceError
from ..i18n import N_, _, untranslate
from .inventory import Item, Kind

#: Most entries the list holds at once.
LIMIT = 500


def _int(text: str, what: str) -> int | None:
    clean = text.strip()
    if not clean:
        return None
    if not clean.isdigit():
        raise ServiceError(_("{what} must be a number: «{text}»").format(what=what, text=text))
    return int(clean)


class ContactKind(Kind):
    key = "contacts"
    title = N_("Address book")
    insert_label = N_("<New contact>")
    guide = (
        N_("NEW CONTACT   callsign, name and whatever else you know of the station"),
        N_("DMR ID: number · GRID: locator · COUNTRY in your language, stored in English"),
        N_("/search TEXT looks up callsign, name, city, province, country or DMR ID"),
    )
    fields = ("call", "first_name", "last_name", "dmr_id", "gridsquare")
    second_row = ("city", "state", "country", "email", "notes")
    columns = (
        (N_("CALLSIGN"), 12),
        (N_("NAME"), 26),
        (N_("DMR ID"), 9),
        (N_("CITY"), 16),
        (N_("PROVINCE"), 14),
        (N_("COUNTRY"), 14),
        (N_("GRID"), 8),
        (N_("QSO"), None),
    )
    searchable = True
    count_text = N_("{total} contacts")

    def items(self, query: str = "") -> list[Item]:
        rows = []
        for contact in ContactService.search(query, limit=LIMIT):
            country = _(contact.country) if contact.country else ""
            place = " · ".join(
                part for part in (contact.city, contact.state, country, contact.gridsquare) if part
            )
            extra = [
                _("{count} QSO in the log").format(count=contact.qso_count)
                if contact.qso_count
                else _("no QSO in the log"),
            ]
            if contact.email:
                extra.append(contact.email)
            if contact.notes:
                extra.append(f"«{contact.notes}»")
            rows.append(
                Item(
                    contact.id,
                    contact.callsign,
                    (
                        contact.callsign,
                        contact.full_name,
                        str(contact.dmr_id or ""),
                        contact.city,
                        contact.state,
                        country,
                        contact.gridsquare,
                        str(contact.qso_count or ""),
                    ),
                    (
                        " · ".join(
                            part
                            for part in (
                                contact.callsign,
                                contact.full_name,
                                f"DMR {contact.dmr_id}" if contact.dmr_id else "",
                            )
                            if part
                        ),
                        place or "—",
                        " · ".join(extra),
                    ),
                )
            )
        return rows

    def total(self, query: str = "") -> int:
        return ContactService.count(query)

    def values(self, item_id: int) -> dict[str, str]:
        contact = ContactService.get(item_id)
        if contact is None:
            return {}
        return {
            "call": contact.callsign,
            "first_name": contact.first_name,
            "last_name": contact.last_name,
            "dmr_id": str(contact.dmr_id or ""),
            "gridsquare": contact.gridsquare,
            "city": contact.city,
            "state": contact.state,
            "country": _(contact.country) if contact.country else "",
            "email": contact.email,
            "notes": contact.notes,
        }

    def save(self, item_id: int | None, values: dict[str, str]) -> str:
        fields: dict[str, Any] = {
            "first_name": values.get("first_name", "").strip(),
            "last_name": values.get("last_name", "").strip(),
            "dmr_id": _int(values.get("dmr_id", ""), _("The DMR ID")),
            "gridsquare": values.get("gridsquare", "").strip().upper(),
            "city": values.get("city", "").strip(),
            "state": values.get("state", "").strip(),
            # Typed in the operator's language, stored in English like the log.
            "country": untranslate(values.get("country", "")),
            "email": values.get("email", "").strip(),
            "notes": values.get("notes", "").strip(),
        }
        call = values.get("call", "")
        if item_id is None:
            return ContactService.create(call, **fields).callsign
        return ContactService.update(item_id, callsign=call, **fields).callsign

    def delete(self, item_id: int) -> None:
        ContactService.delete(item_id)


#: The address book view has a single list.
CONTACT_KINDS: tuple[Kind, ...] = (ContactKind(),)

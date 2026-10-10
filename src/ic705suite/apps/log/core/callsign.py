"""Callsign normalisation, validation and prefix lookup.

The DXCC table is deliberately small: it covers the prefixes a European
operator meets daily and degrades to "unknown" rather than guessing. Replacing
it with a full cty.dat parser later only requires changing ``country_for``.
"""

from __future__ import annotations

import re

from ..i18n import N_, _

#: Structure of a callsign: a prefix of up to three alphanumerics, the digit
#: that separates prefix from suffix, and a suffix of up to four characters
#: ending in a letter. This admits the shapes that actually exist — EA7WM,
#: K1ABC, 9A1AA, 3DA0RS, VP2E and special event calls such as AM500ITU —
#: while rejecting typos like "EA77" or "QWERTY".
#: Portable indicators (EA7WM/P, F/EA7WM) are stripped before matching.
CALLSIGN_RE = re.compile(r"^[A-Z0-9]{1,3}[0-9][A-Z0-9]{0,3}[A-Z]$")

#: Characters a callsign may contain at all, before splitting on slashes.
_ALLOWED_RE = re.compile(r"^[A-Z0-9/]+$")

_COMMON_SUFFIXES = {"P", "M", "MM", "AM", "QRP", "A", "B", "R"}

#: Appending this to a callsign accepts it even if it fails validation, for
#: the genuinely unusual call that no reasonable pattern covers.
OVERRIDE_MARKER = "!"

# Prefix -> DXCC entity name, in English as stored and exported to ADIF.
# The names are marked with N_() so the interface can show them translated
# with _(). Longest prefix wins.
_PREFIXES: dict[str, str] = {
    "EA": N_("Spain"), "EB": N_("Spain"), "EC": N_("Spain"), "ED": N_("Spain"),
    "EE": N_("Spain"), "EF": N_("Spain"), "EG": N_("Spain"), "EH": N_("Spain"),
    "AM": N_("Spain"), "AN": N_("Spain"), "AO": N_("Spain"),
    "CT": N_("Portugal"), "CR": N_("Portugal"), "CS": N_("Portugal"), "CQ": N_("Portugal"),
    "F": N_("France"), "TM": N_("France"), "TK": N_("Corsica"),
    "I": N_("Italy"), "IZ": N_("Italy"), "IK": N_("Italy"), "IW": N_("Italy"),
    "DL": N_("Germany"), "DK": N_("Germany"), "DJ": N_("Germany"), "DB": N_("Germany"),
    "DD": N_("Germany"), "DF": N_("Germany"), "DG": N_("Germany"), "DH": N_("Germany"),
    "DO": N_("Germany"), "DM": N_("Germany"), "DA": N_("Germany"),
    "G": N_("England"), "M": N_("England"), "2E": N_("England"),
    "GM": N_("Scotland"), "MM": N_("Scotland"), "GW": N_("Wales"), "MW": N_("Wales"),
    "GI": N_("Northern Ireland"), "GD": N_("Isle of Man"), "GJ": N_("Jersey"), "GU": N_("Guernsey"),
    "EI": N_("Ireland"), "EJ": N_("Ireland"),
    "ON": N_("Belgium"), "OO": N_("Belgium"), "PA": N_("Netherlands"), "PD": N_("Netherlands"),
    "PE": N_("Netherlands"), "PI": N_("Netherlands"),
    "LX": N_("Luxembourg"), "HB": N_("Switzerland"), "HB0": N_("Liechtenstein"),
    "OE": N_("Austria"),
    "SP": N_("Poland"), "SQ": N_("Poland"), "OK": N_("Czech Republic"), "OL": N_("Czech Republic"),
    "OM": N_("Slovak Republic"), "HA": N_("Hungary"), "HG": N_("Hungary"),
    "S5": N_("Slovenia"), "9A": N_("Croatia"), "E7": N_("Bosnia-Herzegovina"),
    "YU": N_("Serbia"), "YT": N_("Serbia"), "Z3": N_("North Macedonia"), "ZA": N_("Albania"),
    "SV": N_("Greece"), "SY": N_("Greece"), "SZ": N_("Greece"), "5B": N_("Cyprus"),
    "C4": N_("Cyprus"),
    "TA": N_("Turkey"), "YM": N_("Turkey"), "LZ": N_("Bulgaria"), "YO": N_("Romania"),
    "YR": N_("Romania"),
    "ER": N_("Moldova"), "UR": N_("Ukraine"), "UT": N_("Ukraine"), "UY": N_("Ukraine"),
    "US": N_("Ukraine"),
    "EU": N_("Belarus"), "EV": N_("Belarus"), "EW": N_("Belarus"),
    "R": N_("Russia"), "UA": N_("Russia"), "RA": N_("Russia"), "RK": N_("Russia"),
    "RV": N_("Russia"),
    "LY": N_("Lithuania"), "YL": N_("Latvia"), "ES": N_("Estonia"),
    "OH": N_("Finland"), "OH0": N_("Aland Islands"), "OF": N_("Finland"),
    "SM": N_("Sweden"), "SA": N_("Sweden"), "SK": N_("Sweden"), "8S": N_("Sweden"),
    "LA": N_("Norway"), "LB": N_("Norway"), "LN": N_("Norway"), "JW": N_("Svalbard"),
    "OZ": N_("Denmark"), "OU": N_("Denmark"), "OY": N_("Faroe Islands"), "OX": N_("Greenland"),
    "TF": N_("Iceland"),
    "9H": N_("Malta"), "1A": N_("Sovereign Military Order of Malta"), "HV": N_("Vatican City"),
    "T7": N_("San Marino"),
    "3A": N_("Monaco"), "C3": N_("Andorra"), "ZB": N_("Gibraltar"),
    "CN": N_("Morocco"), "7X": N_("Algeria"), "3V": N_("Tunisia"), "5A": N_("Libya"),
    "SU": N_("Egypt"),
    "EA8": N_("Canary Islands"), "EA9": N_("Ceuta & Melilla"), "EA6": N_("Balearic Islands"),
    "CT3": N_("Madeira Islands"), "CU": N_("Azores"),
    "K": N_("United States"), "W": N_("United States"), "N": N_("United States"),
    "AA": N_("United States"), "AB": N_("United States"), "AC": N_("United States"),
    "KH6": N_("Hawaii"), "KL7": N_("Alaska"), "KP4": N_("Puerto Rico"),
    "VE": N_("Canada"), "VA": N_("Canada"), "VO": N_("Canada"), "VY": N_("Canada"),
    "XE": N_("Mexico"), "LU": N_("Argentina"), "PY": N_("Brazil"), "PP": N_("Brazil"),
    "PU": N_("Brazil"),
    "CE": N_("Chile"), "CX": N_("Uruguay"), "CP": N_("Bolivia"), "OA": N_("Peru"),
    "HK": N_("Colombia"),
    "YV": N_("Venezuela"), "HC": N_("Ecuador"), "ZP": N_("Paraguay"),
    "CO": N_("Cuba"), "CM": N_("Cuba"), "HI": N_("Dominican Republic"), "TI": N_("Costa Rica"),
    "JA": N_("Japan"), "JH": N_("Japan"), "JR": N_("Japan"), "JE": N_("Japan"), "JF": N_("Japan"),
    "BY": N_("China"), "BG": N_("China"), "BH": N_("China"), "BD": N_("China"),
    "HL": N_("South Korea"), "DS": N_("South Korea"), "BV": N_("Taiwan"),
    "VK": N_("Australia"), "ZL": N_("New Zealand"), "YB": N_("Indonesia"), "DU": N_("Philippines"),
    "9M": N_("Malaysia"), "HS": N_("Thailand"), "9V": N_("Singapore"), "VU": N_("India"),
    "4X": N_("Israel"), "4Z": N_("Israel"), "A4": N_("Oman"), "A6": N_("United Arab Emirates"),
    "A7": N_("Qatar"), "A9": N_("Bahrain"), "HZ": N_("Saudi Arabia"), "9K": N_("Kuwait"),
    "ZS": N_("South Africa"), "5Z": N_("Kenya"), "5H": N_("Tanzania"), "TR": N_("Gabon"),
    "D2": N_("Angola"), "C9": N_("Mozambique"), "3B8": N_("Mauritius"), "FR": N_("Reunion Island"),
}

# Longest prefixes first so EA8 beats EA and KH6 beats K.
_SORTED_PREFIXES = sorted(_PREFIXES, key=len, reverse=True)


def normalize(raw: str) -> str:
    """Upper-case and strip a callsign, keeping portable indicators intact."""
    return raw.strip().upper().replace(" ", "")


def base_call(raw: str) -> str:
    """Return the home callsign, dropping prefixes and portable suffixes.

    ``F/EA7WM/P`` becomes ``EA7WM``. The longest slash-separated part that
    looks like a full callsign wins, which is how DX clusters resolve it.
    """
    call = normalize(raw)
    if "/" not in call:
        return call

    parts = [part for part in call.split("/") if part]
    candidates = [part for part in parts if part not in _COMMON_SUFFIXES and len(part) > 2]
    if not candidates:
        return parts[0] if parts else call
    # Prefer a part that matches the callsign shape, else the longest one.
    for part in candidates:
        if CALLSIGN_RE.match(part):
            return part
    return max(candidates, key=len)


def is_valid(raw: str) -> bool:
    """True when the callsign has a plausible structure."""
    return validate(raw) is None


def validate(raw: str) -> str | None:
    """Check a callsign and explain what is wrong with it.

    The point is to catch typing mistakes during a fast session, not to
    enforce licensing rules. The message names the actual problem so the
    operator can fix it without guessing.

    Returns:
        None when the callsign is acceptable, otherwise the reason it is not.
    """
    call = normalize(raw)
    if not call:
        return _("The callsign is missing.")
    if not _ALLOWED_RE.match(call):
        invalid = sorted({c for c in call if not c.isalnum() and c != "/"})
        return _(
            "«{call}» contains characters that are not allowed ({chars}). "
            "A callsign only has letters, digits and the portable slash."
        ).format(call=call, chars=" ".join(invalid))

    base = base_call(call)
    if not base:
        return _("«{call}» does not contain any callsign.").format(call=call)
    if len(base) < 3:
        return _("«{call}» is too short to be a callsign.").format(call=base)
    if len(base) > 8:
        return _("«{call}» is too long to be a callsign.").format(call=base)
    if not any(character.isdigit() for character in base):
        return _(
            "«{call}» has no digit. Every callsign has a digit that separates "
            "the prefix from the suffix, as in EA7WM."
        ).format(call=base)
    if CALLSIGN_RE.match(base):
        return None
    return _(
        "«{call}» does not look like a callsign. Expected a prefix, a digit and "
        "a suffix, such as EA7WM or 9A1AA. Add «{marker}» at the end to log it "
        "anyway."
    ).format(call=base, marker=OVERRIDE_MARKER)


def strip_override(raw: str) -> tuple[str, bool]:
    """Split a trailing override marker off a callsign.

    Returns:
        The callsign without the marker, and whether it was present.
    """
    call = raw.strip()
    if call.endswith(OVERRIDE_MARKER):
        return call[: -len(OVERRIDE_MARKER)].strip(), True
    return call, False


def dx_prefix(raw: str) -> str:
    """Return the prefix used for country lookup.

    For ``F/EA7WM`` the operating prefix is ``F``, so a leading slash segment
    shorter than the base call takes precedence.
    """
    call = normalize(raw)
    if "/" in call:
        parts = [part for part in call.split("/") if part]
        head = parts[0]
        if head and head not in _COMMON_SUFFIXES and not CALLSIGN_RE.match(head):
            return head
    return base_call(call)


def country_for(raw: str) -> str:
    """Best-effort DXCC entity name, or an empty string when unknown."""
    prefix = dx_prefix(raw)
    if not prefix:
        return ""
    for candidate in _SORTED_PREFIXES:
        if prefix.startswith(candidate):
            return _PREFIXES[candidate]
    return ""

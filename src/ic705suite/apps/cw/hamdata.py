"""Callsign countries and Q codes, taken from HAMRLOG (same author, MIT licence).

The prefix table covers the countries a European operator meets daily and returns ""
rather than guessing. Texts are English, marked with N_() and translated where shown.
"""

from __future__ import annotations

from dataclasses import dataclass

from ic705suite.i18n import N_

# Prefix -> DXCC entity name. Longest prefix wins.
PREFIXES: dict[str, str] = {
    # Added for the CW skimmer: Dutch prefixes heard in contests.
    "PB": N_("Netherlands"), "PC": N_("Netherlands"), "PF": N_("Netherlands"),
    "PG": N_("Netherlands"), "PH": N_("Netherlands"),
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

_SORTED_PREFIXES = sorted(PREFIXES, key=len, reverse=True)


_PORTABLE = {"P", "M", "MM", "AM", "QRP", "A", "B", "R"}


def country_for(call: str) -> str:
    """English DXCC entity name for a callsign (translate with _()), or "" if unknown."""
    parts = [p for p in call.strip().upper().split("/") if p]
    if not parts:
        return ""
    # F/EA7WM operates from France; EA7WM/P (portable) is still Spain.
    prefixes = [p for p in parts if len(p) <= 3 and p not in _PORTABLE]
    call = prefixes[0] if len(parts) > 1 and prefixes else max(parts, key=len)
    for prefix in _SORTED_PREFIXES:
        if call.startswith(prefix):
            return PREFIXES[prefix]
    return ""


@dataclass(frozen=True, slots=True)
class QCode:
    code: str
    statement: str
    question: str


QCODES: tuple[QCode, ...] = (
    QCode("QRA", N_("The name of my station is ..."), N_("What is the name of your station?")),
    QCode("QRB", N_("The distance between our stations is ..."),
          N_("How far are you from my station?")),
    QCode("QRG", N_("Your exact frequency is ..."), N_("Will you tell me my exact frequency?")),
    QCode("QRH", N_("Your frequency varies"), N_("Does my frequency vary?")),
    QCode("QRI", N_("The tone of your transmission is ... (1 good, 2 variable, 3 bad)"),
          N_("How is the tone of my transmission?")),
    QCode("QRK", N_("The readability of your signals is ... (1 to 5)"),
          N_("What is the readability of my signals?")),
    QCode("QRL", N_("I am busy; please do not interfere"),
          N_("Are you busy? Is the frequency in use?")),
    QCode("QRM", N_("I am being interfered with ... (1 nil to 5 extreme)"),
          N_("Are you being interfered with?")),
    QCode("QRN", N_("I am troubled by static ... (1 nil to 5 extreme)"),
          N_("Are you troubled by static?")),
    QCode("QRO", N_("Increase power"), N_("Shall I increase power?")),
    QCode("QRP", N_("Decrease power"), N_("Shall I decrease power?")),
    QCode("QRQ", N_("Send faster (... words per minute)"), N_("Shall I send faster?")),
    QCode("QRS", N_("Send more slowly (... words per minute)"), N_("Shall I send more slowly?")),
    QCode("QRT", N_("Stop sending; I am closing the station"), N_("Shall I stop sending?")),
    QCode("QRU", N_("I have nothing for you"), N_("Have you anything for me?")),
    QCode("QRV", N_("I am ready"), N_("Are you ready?")),
    QCode("QRW", N_("Please inform ... that I am calling on ... kHz"),
          N_("Shall I inform ... that you are calling on ... kHz?")),
    QCode("QRX", N_("I will call you again at ... hours on ... kHz; wait"),
          N_("When will you call me again?")),
    QCode("QRZ", N_("You are being called by ... on ... kHz"), N_("Who is calling me?")),
    QCode("QSA", N_("The strength of your signals is ... (1 to 5)"),
          N_("What is the strength of my signals?")),
    QCode("QSB", N_("Your signals are fading"), N_("Are my signals fading?")),
    QCode("QSD", N_("Your keying is defective"), N_("Is my keying defective?")),
    QCode("QSK", N_("I can hear you between my signals; break in"),
          N_("Can you hear me between your signals?")),
    QCode("QSL", N_("I am acknowledging receipt"), N_("Can you acknowledge receipt?")),
    QCode("QSM", N_("Repeat the last message you sent me"),
          N_("Shall I repeat the last message I sent you?")),
    QCode("QSN", N_("I heard you on ... kHz"), N_("Did you hear me on ... kHz?")),
    QCode("QSO", N_("I can communicate with ... direct or by relay"),
          N_("Can you communicate with ... direct or by relay?")),
    QCode("QSP", N_("I will relay to ..."), N_("Will you relay to ...?")),
    QCode("QSR", N_("Repeat your call on the calling frequency"),
          N_("Shall I repeat my call on the calling frequency?")),
    QCode("QSS", N_("I will use the working frequency ... kHz"),
          N_("What working frequency will you use?")),
    QCode("QSU", N_("Send or reply on this frequency (or on ... kHz)"),
          N_("Shall I send or reply on this frequency?")),
    QCode("QSV", N_("Send a series of Vs on this frequency"),
          N_("Shall I send a series of Vs on this frequency?")),
    QCode("QSW", N_("I am going to send on this frequency (or on ... kHz)"),
          N_("Will you send on this frequency?")),
    QCode("QSX", N_("I am listening to ... on ... kHz"),
          N_("Will you listen to ... on ... kHz?")),
    QCode("QSY", N_("Change to transmission on another frequency (or on ... kHz)"),
          N_("Shall I change to transmission on another frequency?")),
    QCode("QSZ", N_("Send each word or group twice (or ... times)"),
          N_("Shall I send each word or group more than once?")),
    QCode("QTA", N_("Cancel message number ..."), N_("Shall I cancel message number ...?")),
    QCode("QTC", N_("I have ... messages for you"), N_("How many messages have you to send?")),
    QCode("QTH", N_("My position is ... (latitude and longitude, or place)"),
          N_("What is your position?")),
    QCode("QTR", N_("The correct time is ... UTC"), N_("What is the correct time?")),
)

QCODE_BY_NAME = {q.code: q for q in QCODES}

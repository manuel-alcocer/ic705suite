"""The Q code: the three-letter abbreviations of radio operating.

Each code is both a statement and, followed by a question mark, a question.
The list holds the codes amateurs use on the air, with the meanings of ITU
Radio Regulations Appendix 14 as they are used in amateur practice.

The texts are English, marked with ``N_()`` and translated where shown.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..i18n import N_, _


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


def search(text: str) -> list[QCode]:
    """The codes whose letters or meaning, in the language in use, contain ``text``."""
    needle = text.strip().lower()
    if not needle:
        return list(QCODES)
    return [
        code
        for code in QCODES
        if needle in code.code.lower()
        or needle in _(code.statement).lower()
        or needle in _(code.question).lower()
    ]

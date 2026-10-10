from ic705suite import i18n
from ic705suite.apps.cw.interpret import Interpreter, interpret


def summary(text: str) -> str:
    return ". ".join(interpret(text.split()).summary)


def test_cq_call():
    s = summary("CQ CQ CQ DE EA4ABC EA4ABC K")
    assert "EA4ABC (Spain) is calling CQ: looking for anyone to talk to" in s
    assert s.endswith("Over: waiting for an answer")


def test_answer_report_name_and_location():
    s = summary("EA4ABC DE DL1XYZ GM OM UR RST 579 NAME IS HANS QTH IS BERLIN HW? KN")
    assert "DL1XYZ (Germany) answers EA4ABC (Spain)" in s
    assert "Signal report 579: perfectly readable, strength 7 of 9" in s
    assert "Operator name: Hans" in s and "Location: Berlin" in s
    assert "Over: only the station called may answer" in s


def test_contest_and_cut_numbers():
    assert "calling CQ in a contest" in summary("TEST OK1ABC OK1ABC")
    s = summary("SP3BZU 5NN 1T TU")
    assert "Signal report 599" in s and "Contest exchange: 10" in s


def test_goodbye_with_trailing_courtesy():
    s = summary("R TU 73 GL DE DL1XYZ SK EE")
    assert "Says goodbye" in s and s.endswith("End of the contact")


def test_decoder_noise_is_ignored():
    r = interpret("I E *DWC E T".split())
    assert r.empty


def test_streaming_splits_transmissions():
    clock = [0.0]
    it = Interpreter(clock=lambda: clock[0])
    done = it.feed("CQ DE EA4ABC K ")
    assert len(done) == 1 and "calling CQ" in done[0].summary[0]
    assert it.feed("EA4ABC DE G4XYZ") == []
    clock[0] = 10.0  # long pause ends the transmission
    late = it.tick()
    assert late is not None and "G4XYZ (England) answers EA4ABC (Spain)" in late.summary[0]


def test_spanish():
    try:
        i18n.set_language("es")
        s = summary("CQ DE EA4ABC K")
        assert s == "EA4ABC (España) llama CQ: busca a cualquiera para hablar. Cambio: espera respuesta"
    finally:
        i18n.set_language("en")


def test_scp_correction_is_doubtful_and_never_overrides_repeated_calls():
    scp = {"DL2NFC", "EI2HF", "OZ3SM"}
    s = summary_scp = ". ".join(interpret("SL2NFC OZ3SM".split(), scp).summary)
    assert "DL2NFC? (Germany)" in s and "OZ3SM (Denmark)" in summary_scp
    s = ". ".join(interpret("EI2HY DE EI2HY".split(), scp).summary)
    assert "EI2HY" in s and "EI2HF" not in s

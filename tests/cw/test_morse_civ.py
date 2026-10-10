from ic705suite import morse
from ic705suite.rig import bands
from ic705suite.rig.icom import civ
from ic705suite.rig.base import smeter_text
from ic705suite.rig.icom.rig import filter_index_to_width, filter_width_to_index


def test_normalize_prosigns_and_dropped_chars():
    text, dropped = morse.normalize_tx("cq de ea4abc <ar> ñ  <sk>")
    assert text == "CQ DE EA4ABC + ^SK"
    assert dropped == "Ñ"


def test_chunks_respect_limit_and_keep_word_gaps():
    text = "CQ CQ CQ DE EA4ABC EA4ABC EA4ABC PSE K TU 73 ^SK"
    chunks = morse.chunk_for_icom(text)
    assert all(len(c) <= 30 for c in chunks)
    assert "".join(chunks) == text


def test_chunks_never_split_caret_group():
    word = "A" * 28 + "^SK" + "B" * 10
    for chunk in morse.chunk_for_icom(word):
        assert not chunk.endswith("^") and not chunk.endswith("^S")


def test_paris_is_50_units():
    assert morse.text_units("PARIS ") == 50


def test_frequency_bcd_roundtrip():
    assert civ.encode_frequency(7_030_120) == bytes([0x20, 0x01, 0x03, 0x07, 0x00])
    for hz in (30_000, 7_030_000, 14_060_550, 432_100_000):
        assert civ.decode_frequency(civ.encode_frequency(hz)) == hz


def test_level_bcd():
    assert civ.encode_level(128) == b"\x01\x28"
    assert civ.decode_level(b"\x02\x55") == 255


def test_parser_handles_split_frames_and_noise():
    parser = civ.CivParser()
    frame = civ.build_frame(civ.CONTROLLER_ADDR, b"\x03\x00\x00\x03\x07\x00", src=0xA4)
    assert parser.feed(b"\x00\x11" + frame[:4]) == []
    frames = parser.feed(frame[4:] + frame)
    assert len(frames) == 2
    assert frames[0].src == 0xA4 and frames[0].cmd == 0x03
    assert civ.decode_frequency(frames[0].body[1:]) == 7_030_000


def test_filter_index_mapping():
    assert filter_width_to_index(500) == 9
    assert filter_width_to_index(1200) == 16
    for i in range(41):
        assert filter_width_to_index(filter_index_to_width(i)) == i


def test_smeter_text():
    assert smeter_text(0) == "S0"
    assert smeter_text(120) == "S9"
    assert smeter_text(241) == "S9+60"


def test_frequency_parsing_and_bands():
    assert bands.parse_frequency("7.030") == 7_030_000
    assert bands.parse_frequency("7030,5") == 7_030_500
    assert bands.parse_frequency("14060000") == 14_060_000
    assert bands.find_band("40").name == "40m"
    assert bands.band_for(14_100_000).name == "20m"
    assert bands.format_frequency(7_030_120) == "7.030.12"

"""The speaker button: the radio's receive audio on this computer (a fake sound card)."""

from tests.gui.test_pages import until, window


class FakeSpeaker:
    instances: list = []
    fail = ""

    def __init__(self, rate, volume=1.0):
        self.rate, self.volume, self.chunks, self.stopped = rate, volume, [], False
        FakeSpeaker.instances.append(self)

    def start(self):
        from ic705suite.speaker import SpeakerError

        if FakeSpeaker.fail:
            raise SpeakerError(FakeSpeaker.fail)

    def play(self, pcm):
        self.chunks.append(pcm)

    def stop(self):
        self.stopped = True


class FakeMicrophone:
    instances: list = []

    def __init__(self, rate, send, gain=1.0):
        self.rate, self.send, self.stopped = rate, send, False
        FakeMicrophone.instances.append(self)

    def start(self):
        pass

    def stop(self):
        self.stopped = True


def test_holding_the_microphone_button_talks_through_the_radio(tmp_path, run, monkeypatch):
    import ic705suite.speaker

    monkeypatch.setattr(ic705suite.speaker, "Microphone", FakeMicrophone)
    FakeMicrophone.instances = []

    async def scenario():
        win, link = await window(tmp_path)
        sim = link.core.station.transport
        try:
            link.core.config.tx_enabled = link.core.station.rig.tx_enabled = True
            sim.mode = 0x01                                     # USB
            win.mic_button.pressed.emit()
            await until(lambda: sim.ptt)
            assert win.mic_button.property("talking")
            microphone = FakeMicrophone.instances[0]
            microphone.send(b"\x00\x10" * 160)                  # what it captures
            assert sim.tx_audio_bytes == 320
            win.mic_button.released.emit()
            await until(lambda: microphone.stopped and not sim.ptt)
            assert not win.mic_button.property("talking") and win.microphone is None
            sim.mode = 0x03                                     # CW: refused, said
            win.mic_button.pressed.emit()
            await until(lambda: any("in CW" in text for _kind, text in win.dashboard.messages))
            assert not sim.ptt and win.microphone is None
            assert not win.mic_button.property("talking")
        finally:
            await link.close()

    run(scenario())


def test_the_speaker_button_plays_the_receive_audio(tmp_path, run, monkeypatch):
    import ic705suite.speaker

    monkeypatch.setattr(ic705suite.speaker, "Speaker", FakeSpeaker)
    FakeSpeaker.instances, FakeSpeaker.fail = [], ""

    async def scenario():
        win, link = await window(tmp_path)
        try:
            win.volume_slider.setValue(40)
            win.speaker_button.click()
            await until(lambda: FakeSpeaker.instances and FakeSpeaker.instances[0].chunks)
            speaker = FakeSpeaker.instances[0]
            assert speaker.rate == link.core.station.sample_rate and speaker.volume == 0.4
            win.volume_slider.setValue(70)
            assert speaker.volume == 0.7 and win.config.volume == 70
            win.speaker_button.click()                         # off: the core stops sending
            await until(lambda: speaker.stopped)
            assert win.speaker is None and not link.core._pcm
        finally:
            await link.close()

    run(scenario())


def test_without_a_sound_card_it_says_so(tmp_path, run, monkeypatch):
    import ic705suite.speaker

    monkeypatch.setattr(ic705suite.speaker, "Speaker", FakeSpeaker)
    FakeSpeaker.instances, FakeSpeaker.fail = [], "no audio output: PortAudio library not found"

    async def scenario():
        win, link = await window(tmp_path)
        try:
            win.speaker_button.click()
            await until(lambda: any("PortAudio" in text for _kind, text in win.dashboard.messages))
            assert not win.speaker_button.isChecked() and win.speaker is None
            assert not link.core._pcm
        finally:
            await link.close()

    run(scenario())

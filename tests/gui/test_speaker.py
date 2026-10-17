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

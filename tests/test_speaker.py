"""The receive audio on the speakers: the jitter buffer and the resampler (no sound card)."""

import numpy as np

from ic705suite.speaker import JitterBuffer, Resampler, pcm_to_float


def test_jitter_buffer_waits_for_the_prefill_and_again_after_running_out():
    buf = JitterBuffer(1000, prefill=0.1, maximum=0.5)        # 100 samples, 500 at most
    buf.push(np.ones(60, np.float32))
    assert not buf.pull(20).any()                             # still filling up
    buf.push(np.ones(60, np.float32))
    assert buf.pull(100).all() and len(buf) == 20
    out = buf.pull(50)                                        # runs out: the rest silence
    assert out[:20].all() and not out[20:].any() and buf.underruns == 1
    buf.push(np.ones(50, np.float32))
    assert not buf.pull(10).any()                             # waiting for the prefill again


def test_jitter_buffer_drops_old_audio_to_keep_the_delay_short():
    buf = JitterBuffer(1000, prefill=0.1, maximum=0.5)
    buf.push(np.arange(600, dtype=np.float32))
    assert len(buf) == 100 and buf.pull(1)[0] == 500          # the newest kept


def test_resampler_is_continuous_across_blocks():
    for source, target in ((8000, 48000), (8000, 44100), (48000, 8000)):
        resample = Resampler(source, target)
        tone = np.sin(2 * np.pi * 440 * np.arange(source) / source).astype(np.float32)
        out = np.concatenate([resample(tone[i:i + 137]) for i in range(0, source, 137)])
        assert abs(len(out) - target) <= 1
        # One input sample late (the previous block's last one leads), otherwise the tone.
        expected = np.sin(2 * np.pi * 440 * (np.arange(len(out)) / target - 1 / source))
        assert np.abs(out - expected)[10:].max() < 0.02


def test_pcm_to_float():
    pcm = np.array([0, 16384, -32768], "<i2").tobytes()
    assert pcm_to_float(pcm).tolist() == [0.0, 0.5, -1.0]

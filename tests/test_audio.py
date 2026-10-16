"""Tests for the chunk conditioning in app.audio."""

import array
import math

from app import audio

RATE = 24000


def _chunk(lead, speech, tail, amp_start, amp_end, rate=RATE):
    """Silence, a tone that changes loudness, then silence, as 16-bit mono frames."""
    n = int(rate * speech)
    body = [int((amp_start + (amp_end - amp_start) * i / n) * math.sin(i / 15)) for i in range(n)]
    samples = array.array("h", [0] * int(rate * lead) + body + [0] * int(rate * tail))
    return samples.tobytes()


def _samples(frames):
    samples = array.array("h")
    samples.frombytes(frames)
    return samples


def _quarter_levels(frames):
    samples = _samples(frames)
    size = len(samples) // 4
    return [audio.rms_db(samples[i * size : (i + 1) * size]) for i in range(4)]


def test_rms_db_of_a_full_scale_square_wave_is_zero():
    assert abs(audio.rms_db(array.array("h", [32767, -32767] * 100))) < 0.01


def test_rms_db_of_nothing_is_minus_infinity():
    assert audio.rms_db(array.array("h")) == -math.inf


def test_silence_around_the_speech_is_trimmed():
    out, info = audio.condition_chunk(_chunk(0.6, 4.0, 0.9, 8000, 8000), RATE, None)
    duration = len(out) / 2 / RATE
    assert 4.0 < duration < 4.3  # the silence is gone, a small margin stays
    assert info["lead"] > 0.4 and info["tail"] > 0.7


def test_without_a_target_the_level_is_left_alone():
    out, info = audio.condition_chunk(_chunk(0.0, 2.0, 0.0, 8000, 8000), RATE, None)
    assert info["gain_start_db"] == info["gain_end_db"] == 0.0
    quarters = _quarter_levels(out)
    assert max(quarters) - min(quarters) < 0.5


def test_a_fade_inside_the_chunk_is_evened_out():
    frames = _chunk(0.6, 4.0, 0.9, 8000, 3000)  # about 8 dB quieter at the end
    before = _quarter_levels(frames[int(RATE * 0.6) * 2 : -int(RATE * 0.9) * 2])
    assert before[0] - before[-1] > 6
    out, info = audio.condition_chunk(frames, RATE, -20)
    quarters = _quarter_levels(out)
    assert max(quarters) - min(quarters) < 1.5
    assert info["peak_db"] <= -0.4


def test_the_speech_lands_on_the_target_level():
    out, _ = audio.condition_chunk(_chunk(0.0, 3.0, 0.0, 2000, 2000), RATE, -20)
    assert abs(audio.rms_db(_samples(out)) + 20) < 1.0


def test_gain_is_capped_so_the_peak_does_not_clip():
    # A pulse far above the speech would need more gain than the ceiling allows.
    samples = _samples(_chunk(0.0, 3.0, 0.0, 500, 500))
    samples[RATE] = 20000
    out, info = audio.condition_chunk(samples.tobytes(), RATE, -10)
    assert info["peak_db"] <= -0.4
    assert max(map(abs, _samples(out))) < 32768 * 0.95


def test_neighbouring_chunks_meet_at_the_same_level():
    first, _ = audio.condition_chunk(_chunk(0.3, 3.0, 0.5, 9000, 3500), RATE, -20)
    second, _ = audio.condition_chunk(_chunk(0.4, 3.0, 0.2, 7000, 2500), RATE, -20)
    assert abs(_quarter_levels(first)[-1] - _quarter_levels(second)[0]) < 1.5


def test_the_edges_fade_in_and_out():
    out, _ = audio.condition_chunk(_chunk(0.0, 1.0, 0.0, 9000, 9000), RATE, None)
    samples = _samples(out)
    assert abs(samples[0]) < 50 and abs(samples[-1]) < 50


def test_a_chunk_with_no_speech_comes_back_unchanged():
    silence = array.array("h", [0] * RATE).tobytes()
    out, info = audio.condition_chunk(silence, RATE, -20)
    assert out == silence and info == {}


def test_an_empty_chunk_comes_back_unchanged():
    assert audio.condition_chunk(b"", RATE, -20) == (b"", {})

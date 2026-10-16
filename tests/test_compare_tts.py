"""The TTS comparison script's pure parts; the engines themselves need a GPU."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "compare_tts.py"
spec = importlib.util.spec_from_file_location("compare_tts", SCRIPT)
compare_tts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare_tts)


def test_truncate_ends_on_a_paragraph_boundary():
    text = "First paragraph here.\n\nSecond paragraph here.\n\nThird."
    assert compare_tts.truncate(text, 30) == "First paragraph here."
    assert compare_tts.truncate(text, 10_000) == text


def test_index_lists_players_and_escapes_failures():
    page = compare_tts.build_index(
        [
            {
                "label": "kokoro-af_heart",
                "file": "kokoro-af_heart.mp3",
                "seconds": 4.0,
                "audio_seconds": 60.0,
                "rtf": 0.067,
            },
            {"label": "chatterbox", "error": "AssertionError: <need ref>"},
        ]
    )
    assert '<audio controls preload=none src="kokoro-af_heart.mp3">' in page
    assert "&lt;need ref&gt;" in page


def _chunk(lead, speech, tail, amp_start, amp_end, rate=24000):
    import array
    import math

    n = int(rate * speech)
    body = [int((amp_start + (amp_end - amp_start) * i / n) * math.sin(i / 15)) for i in range(n)]
    samples = array.array("h", [0] * int(rate * lead) + body + [0] * int(rate * tail))
    return samples.tobytes(), rate


def _levels(frames, rate, parts=4):
    import array

    samples = array.array("h")
    samples.frombytes(frames)
    size = len(samples) // parts
    return [compare_tts._rms_db(samples[i * size : (i + 1) * size]) for i in range(parts)]


def test_condition_chunk_trims_silence_and_evens_out_a_fade():
    frames, rate = _chunk(lead=0.6, speech=4.0, tail=0.9, amp_start=8000, amp_end=3000)
    out, info = compare_tts.condition_chunk(frames, rate, target_db=-20)
    duration = len(out) / 2 / rate
    assert 4.0 < duration < 4.3  # silence gone, small margin kept
    assert info["lead"] > 0.4 and info["tail"] > 0.7
    quarters = _levels(out, rate)
    assert max(quarters) - min(quarters) < 1.5  # was about 8 dB end to end
    assert info["peak_db"] <= -0.4


def test_condition_chunk_edges_fade_in_and_out():
    import array

    frames, rate = _chunk(lead=0.0, speech=1.0, tail=0.0, amp_start=9000, amp_end=9000)
    out, _ = compare_tts.condition_chunk(frames, rate, target_db=None)
    samples = array.array("h")
    samples.frombytes(out)
    assert abs(samples[0]) < 50 and abs(samples[-1]) < 50


def test_neighbouring_chunks_meet_at_the_same_level():
    first, rate = _chunk(0.3, 3.0, 0.5, 9000, 3500)
    second, _ = _chunk(0.4, 3.0, 0.2, 7000, 2500)
    a, _ = compare_tts.condition_chunk(first, rate, -20)
    b, _ = compare_tts.condition_chunk(second, rate, -20)
    end_of_first, start_of_second = _levels(a, rate)[-1], _levels(b, rate)[0]
    assert abs(end_of_first - start_of_second) < 1.5

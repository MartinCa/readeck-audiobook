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

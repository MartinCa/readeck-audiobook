"""Tests for pure functions in app.tts (no I/O required)."""

import pytest

import app.tts as tts
from app.tts import DEFAULT_VOICE, _clean_markdown, _pick_voice


class TestPickVoice:
    def test_known_language(self):
        assert _pick_voice("en") == "en-US-AriaNeural"
        assert _pick_voice("de") == "de-DE-KatjaNeural"
        assert _pick_voice("fr") == "fr-FR-DeniseNeural"

    def test_language_subcode(self):
        # "en-US" → base "en" should still resolve
        assert _pick_voice("en-US") == "en-US-AriaNeural"
        assert _pick_voice("pt-BR") == "pt-PT-RaquelNeural"

    def test_unknown_language_falls_back(self):
        assert _pick_voice("xx") == DEFAULT_VOICE
        assert _pick_voice("zh") == DEFAULT_VOICE

    def test_empty_string_falls_back(self):
        assert _pick_voice("") == DEFAULT_VOICE

    def test_case_insensitive(self):
        assert _pick_voice("EN") == "en-US-AriaNeural"
        assert _pick_voice("De") == "de-DE-KatjaNeural"


class TestCleanMarkdown:
    def test_removes_fenced_code_blocks(self):
        md = "Before\n```python\ncode here\n```\nAfter"
        result = _clean_markdown(md)
        assert "```" not in result
        assert "code here" not in result
        assert "Before" in result
        assert "After" in result

    def test_removes_inline_code(self):
        result = _clean_markdown("Use `print()` to output.")
        assert "`" not in result
        assert "Use" in result
        assert "to output." in result

    def test_keeps_link_text_removes_url(self):
        result = _clean_markdown("See [the docs](http://example.com) for details.")
        assert "the docs" in result
        assert "http://example.com" not in result

    def test_removes_images(self):
        result = _clean_markdown("![alt text](http://example.com/img.png)")
        assert "![" not in result
        assert "http://example.com" not in result

    def test_removes_heading_markers(self):
        result = _clean_markdown("# Title\n## Subtitle\n### Section")
        assert "#" not in result
        assert "Title" in result
        assert "Subtitle" in result

    def test_removes_bold_and_italic(self):
        result = _clean_markdown("**bold** and *italic* and ***both***")
        assert "*" not in result
        assert "bold" in result
        assert "italic" in result
        assert "both" in result

    def test_removes_bold_underscore(self):
        result = _clean_markdown("__bold__ and _italic_")
        assert "__" not in result
        assert "bold" in result
        assert "italic" in result

    def test_removes_horizontal_rules(self):
        result = _clean_markdown("Above\n---\nBelow")
        lines = [line for line in result.splitlines() if line.strip()]
        assert not any(set(line.strip()) <= {"-"} for line in lines)

    def test_collapses_excess_blank_lines(self):
        result = _clean_markdown("A\n\n\n\nB")
        assert "\n\n\n" not in result

    def test_passthrough_plain_text(self):
        plain = "This is plain text with no markdown."
        assert _clean_markdown(plain) == plain

    def test_empty_string(self):
        assert _clean_markdown("") == ""


class TestGenerateAudio:
    """Covers generate_audio's engine-dispatch logic; synthesize_kokoro/synthesize_edge_tts
    are faked out since exercising the real backends needs edge_tts/kokoro installed."""

    async def test_edge_tts_default_engine(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tts, "AUDIO_DIR", tmp_path)
        monkeypatch.setattr(tts, "TTS_ENGINE", "edge-tts")
        calls = {}

        async def fake_edge(text, output_path, voice):
            calls["edge"] = voice
            output_path.write_bytes(b"x")

        monkeypatch.setattr(tts, "synthesize_edge_tts", fake_edge)
        result = await tts.generate_audio("job1", "Hello world", "en")

        assert calls["edge"] == "en-US-AriaNeural"
        assert result == tmp_path / "job1.mp3"

    async def test_kokoro_used_for_supported_language(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tts, "AUDIO_DIR", tmp_path)
        monkeypatch.setattr(tts, "TTS_ENGINE", "kokoro")
        calls = {}

        async def fake_kokoro(text, output_path):
            calls["kokoro"] = True
            output_path.write_bytes(b"x")

        async def fake_edge(text, output_path, voice):
            calls["edge"] = True
            output_path.write_bytes(b"x")

        monkeypatch.setattr(tts, "synthesize_kokoro", fake_kokoro)
        monkeypatch.setattr(tts, "synthesize_edge_tts", fake_edge)
        await tts.generate_audio("job1", "Hello world", "en-US")

        assert calls.get("kokoro") is True
        assert "edge" not in calls

    async def test_kokoro_falls_back_to_edge_tts_for_unsupported_language(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(tts, "AUDIO_DIR", tmp_path)
        monkeypatch.setattr(tts, "TTS_ENGINE", "kokoro")
        calls = {}

        async def fake_kokoro(text, output_path):
            calls["kokoro"] = True
            output_path.write_bytes(b"x")

        async def fake_edge(text, output_path, voice):
            calls["edge"] = voice
            output_path.write_bytes(b"x")

        monkeypatch.setattr(tts, "synthesize_kokoro", fake_kokoro)
        monkeypatch.setattr(tts, "synthesize_edge_tts", fake_edge)
        await tts.generate_audio("job1", "Hallo Welt", "de")

        assert "kokoro" not in calls
        assert calls["edge"] == "de-DE-KatjaNeural"

    async def test_empty_text_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tts, "AUDIO_DIR", tmp_path)
        with pytest.raises(ValueError, match="No readable text"):
            await tts.generate_audio("job1", "", "en")

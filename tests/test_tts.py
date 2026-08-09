"""Tests for pure functions in app.tts (no I/O required)."""

import asyncio
from pathlib import Path

import pytest

import app.tts as tts
from app.tts import DEFAULT_VOICE, _clean_markdown, _find_output_file, _pick_voice


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


class TestFindOutputFile:
    def test_no_files_raises(self, tmp_path):
        with pytest.raises(RuntimeError, match="no output file"):
            _find_output_file(tmp_path)

    def test_single_file_returned(self, tmp_path):
        audio = tmp_path / "book.mp3"
        audio.write_bytes(b"data")
        assert _find_output_file(tmp_path) == audio

    def test_single_file_in_subdir_returned(self, tmp_path):
        subdir = tmp_path / "nested"
        subdir.mkdir()
        audio = subdir / "book.mp3"
        audio.write_bytes(b"data")
        assert _find_output_file(tmp_path) == audio

    def test_multiple_files_raises(self, tmp_path):
        (tmp_path / "a.mp3").write_bytes(b"data")
        (tmp_path / "b.mp3").write_bytes(b"data")
        with pytest.raises(RuntimeError, match="multiple output files"):
            _find_output_file(tmp_path)


class _FakeProcess:
    def __init__(self, returncode: int, stdout: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self.killed = False

    async def communicate(self):
        return self._stdout, b""

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


class TestSynthesizeEbook2Audiobook:
    def _fake_cli(self, tmp_path) -> Path:
        cli = tmp_path / "ebook2audiobook.command"
        cli.write_text("#!/bin/sh\n")
        return cli

    async def test_missing_cli_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tts, "EBOOK2AUDIOBOOK_CMD", str(tmp_path / "missing"))
        with pytest.raises(RuntimeError, match="not found"):
            await tts.synthesize_ebook2audiobook(b"epub-bytes", tmp_path / "out.mp3", lang="en")

    async def test_success_copies_output(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tts, "EBOOK2AUDIOBOOK_CMD", str(self._fake_cli(tmp_path)))

        captured_cmd = {}

        async def fake_create_subprocess_exec(*cmd, stdout=None, stderr=None):
            captured_cmd["cmd"] = cmd
            output_dir = Path(cmd[cmd.index("--output_dir") + 1])
            (output_dir / "book.mp3").write_bytes(b"audio-data")
            return _FakeProcess(returncode=0)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

        output_path = tmp_path / "result.mp3"
        await tts.synthesize_ebook2audiobook(b"epub-bytes", output_path, lang="en-US")

        assert output_path.read_bytes() == b"audio-data"
        cmd = captured_cmd["cmd"]
        assert "--headless" in cmd
        assert cmd[cmd.index("--language") + 1] == "en"
        assert cmd[cmd.index("--output_format") + 1] == "mp3"

    async def test_nonzero_exit_raises_with_output(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tts, "EBOOK2AUDIOBOOK_CMD", str(self._fake_cli(tmp_path)))

        async def fake_create_subprocess_exec(*cmd, stdout=None, stderr=None):
            return _FakeProcess(returncode=1, stdout=b"boom: something failed")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

        with pytest.raises(RuntimeError, match="boom: something failed"):
            await tts.synthesize_ebook2audiobook(b"epub-bytes", tmp_path / "out.mp3", lang="en")

    async def test_timeout_kills_process_and_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tts, "EBOOK2AUDIOBOOK_CMD", str(self._fake_cli(tmp_path)))
        fake_proc = _FakeProcess(returncode=0)

        async def fake_create_subprocess_exec(*cmd, stdout=None, stderr=None):
            return fake_proc

        async def fake_wait_for(coro, timeout):
            coro.close()
            raise TimeoutError

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
        monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

        with pytest.raises(RuntimeError, match="timed out"):
            await tts.synthesize_ebook2audiobook(b"epub-bytes", tmp_path / "out.mp3", lang="en")

        assert fake_proc.killed

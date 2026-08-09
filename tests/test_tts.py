"""Tests for text preparation, filenames and engine dispatch in app.tts."""

import subprocess
import sys
import types
from pathlib import Path

import pytest

import app.tts as tts
from app.tts import DEFAULT_VOICE, _clean_markdown, _pick_voice, _split_text, slugify


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


class TestResolveEngineAndVoice:
    def test_edge_tts_by_default(self, monkeypatch):
        monkeypatch.setattr(tts.config, "TTS_ENGINE", "edge-tts")
        assert tts.resolve_engine_and_voice("de") == ("edge-tts", "de-DE-KatjaNeural")

    def test_kokoro_for_supported_language(self, monkeypatch):
        monkeypatch.setattr(tts.config, "TTS_ENGINE", "kokoro")
        monkeypatch.setattr(tts.config, "KOKORO_VOICE", "af_heart")
        assert tts.resolve_engine_and_voice("en-GB") == ("kokoro", "af_heart")

    def test_kokoro_falls_back_for_unsupported_language(self, monkeypatch):
        """The resolved pair is stored on the job, so the UI reports the engine
        that actually ran rather than whatever TTS_ENGINE is set to."""
        monkeypatch.setattr(tts.config, "TTS_ENGINE", "kokoro")
        assert tts.resolve_engine_and_voice("da") == ("edge-tts", "da-DK-ChristelNeural")


class TestResolveKokoroSid:
    """sherpa-onnx picks a speaker by integer id, so KOKORO_VOICE names are mapped."""

    def test_maps_known_voices(self):
        assert tts.resolve_kokoro_sid("af_heart") == 3
        assert tts.resolve_kokoro_sid("am_adam") == 11
        assert tts.resolve_kokoro_sid("bf_alice") == 20

    def test_default_voice_is_mappable(self):
        """The shipped default must resolve, or every kokoro job fails."""
        assert tts.resolve_kokoro_sid(tts.config.KOKORO_VOICE) == 3

    def test_unknown_voice_raises_with_suggestions(self):
        """A typo would otherwise synthesize a whole article in the wrong voice."""
        with pytest.raises(ValueError, match="af_heart"):
            tts.resolve_kokoro_sid("af_hearts")

    def test_ids_are_unique(self):
        assert len(set(tts.KOKORO_VOICES.values())) == len(tts.KOKORO_VOICES)


class TestSlugify:
    def test_basic_title(self):
        assert slugify("How to Build a Thing") == "how-to-build-a-thing"

    def test_strips_punctuation_and_collapses_separators(self):
        assert slugify("Rust: Fast, Safe — and *Fun*!") == "rust-fast-safe-and-fun"

    def test_transliterates_accents(self):
        assert slugify("Café Münster") == "cafe-munster"

    def test_transliterates_nordic_letters(self):
        # These carry no combining form, so NFKD alone would drop them entirely.
        assert slugify("Blåbærgrød på Øresund") == "blabaergrod-pa-oresund"

    def test_truncates_on_a_word_boundary(self):
        slug = slugify("word " * 40, max_length=20)
        assert len(slug) <= 20
        assert not slug.endswith("-")

    def test_returns_empty_for_untransliterable_title(self):
        assert slugify("日本語") == ""

    def test_empty_input(self):
        assert slugify("") == ""


class TestAudioFilename:
    def test_named_after_the_article(self):
        name = tts.audio_filename("My Great Article", "abcdef12-3456-7890-abcd-ef1234567890")
        assert name == "my-great-article-abcdef12.mp3"

    def test_falls_back_when_title_has_no_usable_characters(self):
        assert tts.audio_filename("日本語", "abcdef12-3456").startswith("article-")

    def test_same_title_different_jobs_stay_distinct(self, audio_dir):
        a = tts.audio_filename("Same Title", "11111111-1111-1111-1111-111111111111")
        b = tts.audio_filename("Same Title", "22222222-2222-2222-2222-222222222222")
        assert a != b

    def test_extends_the_suffix_on_collision(self, audio_dir):
        job_id = "abcdef12-3456-7890-abcd-ef1234567890"
        (audio_dir / "clash-abcdef12.mp3").write_bytes(b"taken")
        name = tts.audio_filename("Clash", job_id)
        assert name != "clash-abcdef12.mp3"
        assert name.startswith("clash-abcdef12")

    def test_avoids_names_claimed_in_the_same_batch(self, audio_dir):
        job_id = "abcdef12-3456-7890-abcd-ef1234567890"
        name = tts.audio_filename("Clash", job_id, existing={"clash-abcdef12.mp3"})
        assert name != "clash-abcdef12.mp3"

    def test_is_safe_to_serve(self):
        name = tts.audio_filename("../../etc/passwd", "abcdef12-3456")
        assert "/" not in name and ".." not in name


class TestCleanMarkdown:
    def test_removes_fenced_code_blocks(self):
        md = "Before\n```python\ncode here\n```\nAfter"
        result = _clean_markdown(md)
        assert "```" not in result
        assert "code here" not in result
        assert "Before" in result
        assert "After" in result

    def test_inline_code_keeps_its_contents(self):
        """Dropping the code text left fragments like "Run  to save your work."."""
        result = _clean_markdown("Run `git commit -m msg` to save your work.")
        assert "`" not in result
        assert result == "Run git commit -m msg to save your work."

    def test_keeps_link_text_removes_url(self):
        result = _clean_markdown("See [the docs](http://example.com) for details.")
        assert result == "See the docs for details."

    def test_removes_images(self):
        result = _clean_markdown("![alt text](http://example.com/img.png)")
        assert "![" not in result
        assert "http://example.com" not in result

    def test_removes_heading_markers_and_adds_a_pause(self):
        result = _clean_markdown("# Title\n## Subtitle\n### Section")
        assert "#" not in result
        assert result == "Title.\nSubtitle.\nSection."

    def test_heading_keeps_existing_punctuation(self):
        assert _clean_markdown("## Is this thing on?") == "Is this thing on?"

    def test_removes_bold_and_italic(self):
        result = _clean_markdown("**bold** and *italic* and ***both***")
        assert "*" not in result
        assert result == "bold and italic and both"

    def test_removes_bold_underscore(self):
        result = _clean_markdown("__bold__ and _italic_")
        assert "__" not in result
        assert result == "bold and italic"

    def test_preserves_snake_case_identifiers(self):
        """The old emphasis rule turned `send_user_file` into `senduserfile`."""
        result = _clean_markdown("Use the send_user_file and get_data_now helpers.")
        assert result == "Use the send_user_file and get_data_now helpers."

    def test_preserves_multiplication_asterisks(self):
        result = _clean_markdown("Ratings were 3 * 4 and 10 * 2 in the table.")
        assert result == "Ratings were 3 * 4 and 10 * 2 in the table."

    def test_strips_bullet_list_markers(self):
        result = _clean_markdown("* first item\n* second item\n- third item")
        assert result == "first item\nsecond item\nthird item"

    def test_strips_numbered_list_markers(self):
        result = _clean_markdown("1. Step one\n2) Step two")
        assert result == "Step one\nStep two"

    def test_strips_blockquote_markers(self):
        result = _clean_markdown("> A quoted remark.\n>> Nested one.")
        assert result == "A quoted remark.\nNested one."

    def test_renders_tables_as_prose(self):
        md = "| Language | Voice |\n|---|---|\n| English | Aria |"
        result = _clean_markdown(md)
        assert "|" not in result
        assert result == "Language, Voice.\nEnglish, Aria."

    def test_removes_horizontal_rules(self):
        result = _clean_markdown("Above\n---\nBelow")
        lines = [line for line in result.splitlines() if line.strip()]
        assert not any(set(line.strip()) <= {"-"} for line in lines)

    def test_removes_footnote_references(self):
        assert _clean_markdown("A claim[^1] follows.") == "A claim follows."

    def test_removes_bare_urls(self):
        result = _clean_markdown("Read more at https://example.com/a/b today.")
        assert "example.com" not in result

    def test_unescapes_html_entities(self):
        assert _clean_markdown("Tom &amp; Jerry &mdash; friends") == "Tom & Jerry — friends"

    def test_strips_stray_html_tags(self):
        assert _clean_markdown("<p>Hello <em>there</em></p>") == "Hello there"

    def test_collapses_excess_blank_lines(self):
        result = _clean_markdown("A\n\n\n\nB")
        assert "\n\n\n" not in result

    def test_passthrough_plain_text(self):
        plain = "This is plain text with no markdown."
        assert _clean_markdown(plain) == plain

    def test_empty_string(self):
        assert _clean_markdown("") == ""


class TestSplitText:
    def test_short_text_is_a_single_chunk(self):
        assert _split_text("Hello world.", 100) == ["Hello world."]

    def test_splits_on_paragraph_boundaries(self):
        text = "\n\n".join(["A" * 60, "B" * 60, "C" * 60])
        chunks = _split_text(text, 100)
        assert len(chunks) > 1
        assert all(len(c) <= 100 for c in chunks)

    def test_splits_long_paragraphs_on_sentences(self):
        text = " ".join(f"Sentence number {i} here." for i in range(40))
        chunks = _split_text(text, 120)
        assert all(len(c) <= 120 for c in chunks)
        assert "".join(chunks).replace("\n", " ").count("Sentence") == 40

    def test_hard_wraps_a_single_enormous_sentence(self):
        chunks = _split_text("x" * 500, 100)
        assert len(chunks) == 5
        assert all(len(c) <= 100 for c in chunks)

    def test_loses_no_words(self):
        text = "\n\n".join(f"Paragraph {i} with some words in it." for i in range(30))
        chunks = _split_text(text, 90)
        assert sum(c.count("Paragraph") for c in chunks) == 30

    def test_blank_text_yields_no_chunks(self):
        assert _split_text("   \n\n  ", 100) == []


class TestSynthesizeKokoro:
    """Covers the sherpa-onnx wiring. sherpa_onnx and soundfile only ship in the
    kokoro image, so both are stubbed — this exercises the plumbing, not the model."""

    @pytest.fixture
    def fake_sherpa(self, monkeypatch):
        seen = {}

        class FakeAudio:
            # Deliberately not 24000: the sample rate must come from the engine
            # rather than the literal the torch-based implementation hardcoded.
            samples = [0.0, 0.1, -0.1]
            sample_rate = 22050

        class FakeEngine:
            num_speakers = len(tts.KOKORO_VOICES)

            def generate(self, text, gen_config):
                seen["text"] = text
                seen["sid"] = gen_config.sid
                seen["speed"] = gen_config.speed
                return seen.get("audio", FakeAudio())

        sherpa = types.ModuleType("sherpa_onnx")
        sherpa.GenerationConfig = type("GenerationConfig", (), {})

        def fake_write(path, samples, sample_rate):
            seen["wav"] = (samples, sample_rate)
            Path(path).write_bytes(b"wav")

        soundfile = types.ModuleType("soundfile")
        soundfile.write = fake_write

        def fake_run(cmd, capture_output=False):
            seen["ffmpeg"] = cmd
            Path(cmd[-1]).write_bytes(b"mp3")
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        monkeypatch.setitem(sys.modules, "sherpa_onnx", sherpa)
        monkeypatch.setitem(sys.modules, "soundfile", soundfile)
        monkeypatch.setattr(tts, "_get_kokoro_tts", FakeEngine)
        monkeypatch.setattr(subprocess, "run", fake_run)
        return seen

    async def test_maps_the_voice_name_to_a_speaker_id(self, tmp_path, fake_sherpa):
        await tts.synthesize_kokoro("Hello world", tmp_path / "out.mp3", "am_adam")
        assert fake_sherpa["sid"] == 11
        assert fake_sherpa["text"] == "Hello world"

    async def test_uses_the_sample_rate_the_engine_reports(self, tmp_path, fake_sherpa):
        out = tmp_path / "out.mp3"
        await tts.synthesize_kokoro("Hello world", out, "af_heart")
        assert fake_sherpa["wav"][1] == 22050
        assert out.read_bytes() == b"mp3"

    async def test_blank_voice_falls_back_to_config(self, tmp_path, fake_sherpa, monkeypatch):
        monkeypatch.setattr(tts.config, "KOKORO_VOICE", "bf_emma")
        await tts.synthesize_kokoro("Hi", tmp_path / "out.mp3", "")
        assert fake_sherpa["sid"] == 21

    async def test_unknown_voice_raises_before_loading_the_model(
        self, tmp_path, fake_sherpa, monkeypatch
    ):
        def boom():
            raise AssertionError("the model must not load for an invalid voice")

        monkeypatch.setattr(tts, "_get_kokoro_tts", boom)
        with pytest.raises(ValueError, match="Unknown Kokoro voice"):
            await tts.synthesize_kokoro("Hi", tmp_path / "out.mp3", "af_hearts")

    async def test_empty_audio_raises(self, tmp_path, fake_sherpa):
        fake_sherpa["audio"] = type("Empty", (), {"samples": [], "sample_rate": 22050})()
        with pytest.raises(RuntimeError, match="no audio"):
            await tts.synthesize_kokoro("Hi", tmp_path / "out.mp3", "af_heart")

    async def test_speaker_id_beyond_the_loaded_model_raises(
        self, tmp_path, fake_sherpa, monkeypatch
    ):
        """Guards against the name→id table drifting from the bundled model:
        an out-of-range id would otherwise read back as some other speaker."""
        monkeypatch.setattr(
            tts, "_get_kokoro_tts", lambda: type("Small", (), {"num_speakers": 11})()
        )
        with pytest.raises(RuntimeError, match="only has 11 speakers"):
            await tts.synthesize_kokoro("Hi", tmp_path / "out.mp3", "bf_emma")


class TestGenerateAudio:
    """Covers generate_audio's dispatch, naming and atomic write behaviour;
    the real backends need edge_tts/sherpa_onnx installed, so they are faked."""

    @pytest.fixture
    def fake_backends(self, monkeypatch):
        calls = {}

        async def fake_edge(text, output_path, voice):
            calls["edge"] = {"voice": voice, "text": text}
            output_path.write_bytes(b"edge-audio")

        async def fake_kokoro(text, output_path, voice=""):
            calls["kokoro"] = {"voice": voice, "text": text}
            output_path.write_bytes(b"kokoro-audio")

        monkeypatch.setattr(tts, "synthesize_edge_tts", fake_edge)
        monkeypatch.setattr(tts, "synthesize_kokoro", fake_kokoro)
        monkeypatch.setattr(tts, "_tag_mp3", lambda *a, **k: None)
        return calls

    async def test_uses_the_engine_recorded_on_the_job(self, audio_dir, fake_backends):
        await tts.generate_audio("job1", "Hello world", engine="kokoro", voice="af_heart")
        assert "kokoro" in fake_backends
        assert "edge" not in fake_backends

    async def test_falls_back_to_resolving_from_language(
        self, audio_dir, fake_backends, monkeypatch
    ):
        monkeypatch.setattr(tts.config, "TTS_ENGINE", "edge-tts")
        await tts.generate_audio("job1", "Hallo Welt", lang="de")
        assert fake_backends["edge"]["voice"] == "de-DE-KatjaNeural"

    async def test_file_is_named_after_the_article(self, audio_dir, fake_backends):
        path = await tts.generate_audio(
            "abcdef12-3456-7890-abcd-ef1234567890",
            "Hello world",
            title="A Nice Long Read",
            engine="edge-tts",
            voice="v",
        )
        assert path.name == "a-nice-long-read-abcdef12.mp3"
        assert path.read_bytes() == b"edge-audio"

    async def test_text_is_cleaned_before_synthesis(self, audio_dir, fake_backends):
        await tts.generate_audio(
            "job1", "# Heading\n\nUse `code` here.", engine="edge-tts", voice="v"
        )
        assert fake_backends["edge"]["text"] == "Heading.\n\nUse code here."

    async def test_empty_text_raises(self, audio_dir):
        with pytest.raises(ValueError, match="No readable text"):
            await tts.generate_audio("job1", "", engine="edge-tts", voice="v")

    async def test_leaves_no_partial_file_when_synthesis_fails(self, audio_dir, monkeypatch):
        async def boom(text, output_path, voice):
            output_path.write_bytes(b"half a file")
            raise RuntimeError("connection dropped")

        monkeypatch.setattr(tts, "synthesize_edge_tts", boom)
        with pytest.raises(RuntimeError, match="connection dropped"):
            await tts.generate_audio("job1", "Hello", title="T", engine="edge-tts", voice="v")

        assert list(audio_dir.iterdir()) == []

    async def test_rejects_an_empty_output_file(self, audio_dir, monkeypatch):
        async def writes_nothing(text, output_path, voice):
            output_path.write_bytes(b"")

        monkeypatch.setattr(tts, "synthesize_edge_tts", writes_nothing)
        with pytest.raises(RuntimeError, match="empty audio file"):
            await tts.generate_audio("job1", "Hello", engine="edge-tts", voice="v")
        assert list(audio_dir.iterdir()) == []


class TestEdgeTtsChunking:
    async def test_concatenates_every_chunk(self, monkeypatch, tmp_path):
        monkeypatch.setattr(tts.config, "TTS_CHUNK_CHARS", 30)
        seen = []

        async def fake_chunk(text, voice):
            seen.append(text)
            return f"[{len(text)}]".encode()

        monkeypatch.setattr(tts, "_edge_tts_chunk", fake_chunk)
        out = tmp_path / "out.mp3"
        await tts.synthesize_edge_tts("\n\n".join(["word " * 5] * 4), out, "v")

        assert len(seen) > 1
        assert out.read_bytes() == b"".join(f"[{len(t)}]".encode() for t in seen)

    async def test_retries_a_failing_chunk(self, monkeypatch, tmp_path):
        monkeypatch.setattr(tts.config, "TTS_MAX_RETRIES", 3)
        monkeypatch.setattr(tts.asyncio, "sleep", _no_sleep)
        attempts = {"n": 0}

        async def flaky(text, voice):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise RuntimeError("websocket closed")
            return b"ok"

        monkeypatch.setattr(tts, "_edge_tts_chunk", flaky)
        out = tmp_path / "out.mp3"
        await tts.synthesize_edge_tts("Short text.", out, "v")

        assert attempts["n"] == 3
        assert out.read_bytes() == b"ok"

    async def test_gives_up_after_the_retry_budget(self, monkeypatch, tmp_path):
        monkeypatch.setattr(tts.config, "TTS_MAX_RETRIES", 2)
        monkeypatch.setattr(tts.asyncio, "sleep", _no_sleep)

        async def always_fails(text, voice):
            raise RuntimeError("websocket closed")

        monkeypatch.setattr(tts, "_edge_tts_chunk", always_fails)
        with pytest.raises(RuntimeError, match="failed after 2 attempt"):
            await tts.synthesize_edge_tts("Short text.", tmp_path / "out.mp3", "v")


async def _no_sleep(_seconds):
    return None


class TestTagging:
    def test_writes_the_article_title_into_the_file(self, tmp_path):
        from mutagen.id3 import ID3

        path = tmp_path / "an-article-abc123.mp3"
        path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 400)

        tts._tag_mp3(path, "Blåbærgrød på Øresund", "https://example.com/post", "edge-tts")

        tags = ID3(str(path))
        assert str(tags["TIT2"]) == "Blåbærgrød på Øresund"
        assert str(tags["TPE1"]) == "Readeck Audiobook"
        assert tags["WOAS"].url == "https://example.com/post"

    def test_falls_back_to_the_filename_without_a_title(self, tmp_path):
        from mutagen.id3 import ID3

        path = tmp_path / "fallback-abc123.mp3"
        path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 400)

        tts._tag_mp3(path, "", "", "edge-tts")
        assert str(ID3(str(path))["TIT2"]) == "fallback-abc123"

    def test_tagging_failure_never_breaks_a_job(self, tmp_path):
        """Tags are cosmetic; a job that produced good audio must still succeed."""
        missing = tmp_path / "does-not-exist.mp3"
        tts._tag_mp3(missing, "Title", "", "edge-tts")  # must not raise

    async def test_generated_files_are_tagged(self, audio_dir, monkeypatch):
        from mutagen.id3 import ID3

        async def fake_edge(text, output_path, voice):
            output_path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 400)

        monkeypatch.setattr(tts, "synthesize_edge_tts", fake_edge)
        path = await tts.generate_audio(
            "abcdef12-3456", "Hello there.", title="Tagged Read", engine="edge-tts", voice="v"
        )
        assert str(ID3(str(path))["TIT2"]) == "Tagged Read"

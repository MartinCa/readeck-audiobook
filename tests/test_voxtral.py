"""Tests for the Voxtral engine in app.voxtral; the server is faked with httpx."""

import array
import asyncio
import io
import json
import math
import shutil
import subprocess
import wave
from pathlib import Path

import httpx
import pytest

from app import tts, voxtral

RATE = 24000
REAL_ASYNC_CLIENT = httpx.AsyncClient
REAL_RUN = subprocess.run


def _wav(seconds=2.0, amp=6000, lead=0.3, tail=0.4, *, width=2, channels=1) -> bytes:
    """A WAV holding a tone between two stretches of silence."""
    n = int(RATE * seconds)
    tone = [int(amp * math.sin(i / 15)) for i in range(n)]
    samples = array.array("h", [0] * int(RATE * lead) + tone + [0] * int(RATE * tail))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes() if width == 2 else b"\x00" * len(samples) * width)
    return buf.getvalue()


@pytest.fixture
def server(monkeypatch):
    """A fake Voxtral server: records requests, answers with `reply(request)`.

    ffmpeg is faked too (CI has none): the commands it is given are recorded in
    `ffmpeg` and a placeholder is written where the MP3 would go. Tests that
    need the real encoder ask for `real_ffmpeg`.
    """
    monkeypatch.setattr(voxtral.config, "VOXTRAL_URL", "http://voxtral.test")
    monkeypatch.setattr(voxtral.config, "TTS_MAX_RETRIES", 1)  # no sleeping between retries
    monkeypatch.setattr(voxtral.config, "VOXTRAL_IDLE_SLEEP_SECONDS", 0)  # no sleep/wake calls
    state = type("FakeServer", (), {})()
    state.requests = []
    state.ffmpeg = []
    state.ffmpeg_returncode = 0
    state.reply = lambda request: httpx.Response(200, content=_wav())

    def handler(request: httpx.Request) -> httpx.Response:
        state.requests.append(json.loads(request.content))
        return state.reply(request)

    def fake_run(cmd, **kwargs):
        state.ffmpeg.append(cmd)
        if state.ffmpeg_returncode == 0:
            Path(cmd[-1]).write_bytes(b"mp3")
        return subprocess.CompletedProcess(cmd, state.ffmpeg_returncode, b"", b"no such codec")

    monkeypatch.setattr(voxtral.subprocess, "run", fake_run)
    monkeypatch.setattr(
        voxtral.httpx,
        "AsyncClient",
        lambda **kw: REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kw),
    )
    return state


@pytest.fixture
def real_ffmpeg(server, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is not installed")
    monkeypatch.setattr(voxtral.subprocess, "run", REAL_RUN)


class TestSplitText:
    def test_short_text_is_one_chunk_with_no_trailing_gap(self):
        assert voxtral.split_text("Hello there.", 1800) == [voxtral.Chunk("Hello there.", 0.0)]

    def test_blank_text_yields_nothing(self):
        assert voxtral.split_text("  \n\n  ", 1800) == []

    def test_paragraphs_are_packed_up_to_the_limit(self):
        text = "\n\n".join(["A" * 100] * 5)
        chunks = voxtral.split_text(text, 450)
        assert [c.text.count("A") for c in chunks] == [400, 100]
        assert chunks[0].text.count("\n\n") == 3  # paragraph breaks are kept inside a chunk

    def test_a_paragraph_that_fits_is_never_split(self):
        paragraph = "One sentence. " * 20  # about 280 characters
        chunks = voxtral.split_text(f"{paragraph}\n\n{paragraph}", 300)
        assert len(chunks) == 2
        assert all(c.text == paragraph.strip() for c in chunks)

    def test_the_gap_is_longer_after_a_paragraph_than_after_a_sentence(self):
        chunks = voxtral.split_text("A" * 100 + "\n\n" + "B" * 100, 150)
        assert [c.gap for c in chunks] == [voxtral.PARAGRAPH_GAP_SECONDS, 0.0]
        long_paragraph = " ".join(f"Sentence number {i} is here." for i in range(30))
        chunks = voxtral.split_text(long_paragraph, 200)
        assert len(chunks) > 1
        assert all(c.gap == voxtral.SENTENCE_GAP_SECONDS for c in chunks[:-1])
        assert chunks[-1].gap == 0.0

    def test_a_long_paragraph_is_cut_at_sentence_boundaries(self):
        long_paragraph = " ".join(f"Sentence number {i} is here." for i in range(30))
        for chunk in voxtral.split_text(long_paragraph, 200):
            assert chunk.text.endswith(".") and len(chunk.text) <= 200

    def test_a_single_enormous_sentence_is_wrapped_hard(self):
        chunks = voxtral.split_text("word " * 200, 100)
        assert all(len(c.text) <= 100 for c in chunks)

    def test_loses_no_words(self):
        text = "\n\n".join(" ".join(f"w{p}x{i}" for i in range(80)) + "." for p in range(12))
        chunks = voxtral.split_text(text, 500)
        assert " ".join(c.text for c in chunks).split() == text.split()

    def test_no_chunk_exceeds_the_limit(self):
        text = "\n\n".join("Some words go here. " * 30 for _ in range(10))
        assert all(len(c.text) <= 700 for c in voxtral.split_text(text, 700))


class TestSynthesize:
    async def test_sends_the_documented_request(self, tmp_path, server, monkeypatch):
        monkeypatch.setattr(voxtral.config, "VOXTRAL_MODEL", "mistralai/Voxtral-4B-TTS-2603")
        monkeypatch.setattr(voxtral.config, "VOXTRAL_MAX_NEW_TOKENS", 3000)
        await voxtral.synthesize_voxtral("Hello there.", tmp_path / "out.mp3", "neutral_male")
        assert server.requests == [
            {
                "model": "mistralai/Voxtral-4B-TTS-2603",
                "input": "Hello there.",
                "voice": "neutral_male",
                "language": "English",
                "response_format": "wav",
                "max_new_tokens": 3000,
            }
        ]

    async def test_a_blank_voice_falls_back_to_the_environment(self, tmp_path, server, monkeypatch):
        monkeypatch.setattr(voxtral.config, "VOXTRAL_VOICE", "cheerful_female")
        await voxtral.synthesize_voxtral("Hello.", tmp_path / "out.mp3")
        assert server.requests[0]["voice"] == "cheerful_female"

    async def test_writes_an_mp3_the_length_of_the_speech_gaps_and_end_silence(
        self, tmp_path, server, real_ffmpeg, monkeypatch
    ):
        monkeypatch.setattr(voxtral.config, "VOXTRAL_CHUNK_CHARS", 200)
        text = "\n\n".join(["Word " * 30] * 3)  # three chunks, paragraph breaks between
        out = tmp_path / "out.mp3"
        await voxtral.synthesize_voxtral(text, out, "casual_female")
        assert len(server.requests) == 3
        # 3 x 2 s speech (about 0.1 s margin each), 2 x 0.6 s gap, 2 s end silence.
        expected = 3 * 2.1 + 2 * voxtral.PARAGRAPH_GAP_SECONDS + tts.END_SILENCE_SECONDS
        assert abs(tts.audio_duration(out) - expected) < 0.5

    async def test_the_models_own_edge_silence_is_trimmed(self, tmp_path, server, real_ffmpeg):
        server.reply = lambda request: httpx.Response(200, content=_wav(2.0, lead=1.5, tail=1.5))
        out = tmp_path / "out.mp3"
        await voxtral.synthesize_voxtral("Hello there.", out)
        assert abs(tts.audio_duration(out) - (2.1 + tts.END_SILENCE_SECONDS)) < 0.5

    async def test_encodes_the_joined_audio_to_the_output_path(self, tmp_path, server):
        out = tmp_path / "out.mp3.part"
        await voxtral.synthesize_voxtral("Hello there.", out)
        (cmd,) = server.ffmpeg
        assert cmd[0] == "ffmpeg" and cmd[-1] == str(out)
        assert cmd[cmd.index("-f") + 1] == "mp3"  # the .part name tells ffmpeg nothing
        assert out.read_bytes() == b"mp3"

    async def test_an_ffmpeg_failure_is_reported(self, tmp_path, server):
        server.ffmpeg_returncode = 1
        with pytest.raises(RuntimeError, match="ffmpeg failed encoding Voxtral.*no such codec"):
            await voxtral.synthesize_voxtral("Hello there.", tmp_path / "out.mp3")

    async def test_reports_progress_per_chunk(self, tmp_path, server, monkeypatch):
        monkeypatch.setattr(voxtral.config, "VOXTRAL_CHUNK_CHARS", 200)
        seen = []

        async def on_progress(done, total):
            seen.append((done, total))

        await voxtral.synthesize_voxtral(
            "\n\n".join(["Word " * 30] * 3), tmp_path / "out.mp3", "casual_female", on_progress
        )
        assert seen == [(0, 3), (1, 3), (2, 3), (3, 3)]

    async def test_chunks_are_levelled_to_the_target(self, tmp_path, server, monkeypatch):
        monkeypatch.setattr(voxtral.config, "VOXTRAL_TARGET_DB", -20.0)
        seen = []
        real = voxtral.audio.condition_chunk
        monkeypatch.setattr(
            voxtral.audio,
            "condition_chunk",
            lambda frames, rate, target: seen.append(target) or real(frames, rate, target),
        )
        await voxtral.synthesize_voxtral("Hello there.", tmp_path / "out.mp3")
        assert seen == [-20.0]

    async def test_levelling_can_be_switched_off(self, tmp_path, server, monkeypatch):
        monkeypatch.setattr(voxtral.config, "VOXTRAL_TARGET_DB", None)
        seen = []
        real = voxtral.audio.condition_chunk
        monkeypatch.setattr(
            voxtral.audio,
            "condition_chunk",
            lambda frames, rate, target: seen.append(target) or real(frames, rate, target),
        )
        await voxtral.synthesize_voxtral("Hello there.", tmp_path / "out.mp3")
        assert seen == [None]

    async def test_blank_text_raises(self, tmp_path, server):
        with pytest.raises(ValueError, match="No readable text"):
            await voxtral.synthesize_voxtral("  ", tmp_path / "out.mp3")

    async def test_without_a_server_url_it_says_so(self, tmp_path, monkeypatch):
        monkeypatch.setattr(voxtral.config, "VOXTRAL_URL", "")
        with pytest.raises(RuntimeError, match="VOXTRAL_URL"):
            await voxtral.synthesize_voxtral("Hello.", tmp_path / "out.mp3")

    async def test_a_server_error_is_reported_with_its_message(self, tmp_path, server):
        server.reply = lambda request: httpx.Response(500, text="model exploded")
        with pytest.raises(RuntimeError, match="returned 500: model exploded"):
            await voxtral.synthesize_voxtral("Hello.", tmp_path / "out.mp3")

    async def test_an_unreachable_server_is_reported(self, tmp_path, server):
        def refuse(request):
            raise httpx.ConnectError("connection refused")

        server.reply = refuse
        with pytest.raises(RuntimeError, match="Cannot reach the Voxtral server at http://voxtral"):
            await voxtral.synthesize_voxtral("Hello.", tmp_path / "out.mp3")

    @pytest.mark.parametrize("status", [400, 401, 404, 422])
    async def test_a_request_the_server_rejects_is_not_retried(
        self, tmp_path, server, monkeypatch, status
    ):
        monkeypatch.setattr(voxtral.config, "TTS_MAX_RETRIES", 3)
        server.reply = lambda request: httpx.Response(status, text="no such voice")
        with pytest.raises(RuntimeError, match=f"failed: Voxtral server returned {status}"):
            await voxtral.synthesize_voxtral("Hello.", tmp_path / "out.mp3")
        assert len(server.requests) == 1

    @pytest.mark.parametrize("status", [408, 429, 500, 503])
    async def test_timeouts_rate_limits_and_server_errors_are_retried(
        self, tmp_path, server, monkeypatch, status
    ):
        monkeypatch.setattr(voxtral.config, "TTS_MAX_RETRIES", 2)
        monkeypatch.setattr(tts.asyncio, "sleep", lambda s: _noop())
        replies = iter(
            [httpx.Response(status, text="try later"), httpx.Response(200, content=_wav())]
        )
        server.reply = lambda request: next(replies)
        await voxtral.synthesize_voxtral("Hello.", tmp_path / "out.mp3")
        assert len(server.requests) == 2

    async def test_transient_failures_are_retried(self, tmp_path, server, monkeypatch):
        monkeypatch.setattr(voxtral.config, "TTS_MAX_RETRIES", 2)
        monkeypatch.setattr(tts.asyncio, "sleep", lambda s: _noop())
        replies = iter([httpx.Response(503, text="busy"), httpx.Response(200, content=_wav())])
        server.reply = lambda request: next(replies)
        out = tmp_path / "out.mp3"
        await voxtral.synthesize_voxtral("Hello.", out)
        assert len(server.requests) == 2 and out.stat().st_size > 0

    async def test_audio_that_is_not_16_bit_mono_is_rejected(self, tmp_path, server):
        server.reply = lambda request: httpx.Response(200, content=_wav(width=3))
        with pytest.raises(RuntimeError, match="not 16-bit mono"):
            await voxtral.synthesize_voxtral("Hello.", tmp_path / "out.mp3")

    async def test_a_reply_that_is_not_a_wav_is_rejected(self, tmp_path, server):
        server.reply = lambda request: httpx.Response(200, content=b"not audio")
        with pytest.raises(RuntimeError, match="not a readable WAV"):
            await voxtral.synthesize_voxtral("Hello.", tmp_path / "out.mp3")

    async def test_silence_from_the_server_is_an_error(self, tmp_path, server):
        server.reply = lambda request: httpx.Response(200, content=_wav(amp=0))
        with pytest.raises(RuntimeError, match="no audible speech"):
            await voxtral.synthesize_voxtral("Hello.", tmp_path / "out.mp3")

    async def test_leaves_no_temporary_wav_behind(self, tmp_path, server, monkeypatch):
        monkeypatch.setattr(voxtral.tempfile, "tempdir", str(tmp_path / "tmp"))
        (tmp_path / "tmp").mkdir()
        server.reply = lambda request: httpx.Response(500, text="boom")
        with pytest.raises(RuntimeError):
            await voxtral.synthesize_voxtral("Hello.", tmp_path / "out.mp3")
        assert list((tmp_path / "tmp").iterdir()) == []


async def _noop():
    return None


class TestIdleSleep:
    @pytest.fixture(autouse=True)
    def reset(self, monkeypatch):
        monkeypatch.setattr(voxtral, "_users", 0)
        monkeypatch.setattr(voxtral, "_sleep_timer", None)
        monkeypatch.setattr(voxtral, "_sleep_task", None)
        monkeypatch.setattr(voxtral, "_sleep_lock", None)

    @staticmethod
    def _controls(server):
        calls = []
        base = server.reply

        def reply(request):
            if "/v1/omni/" in str(request.url):
                calls.append((request.url.path, json.loads(request.content)))
                return httpx.Response(200, json={"status": "SUCCESS", "acks": []})
            return base(request)

        server.reply = reply
        return calls

    async def test_zero_makes_no_control_calls(self, tmp_path, server, monkeypatch):
        calls = self._controls(server)
        await voxtral.synthesize_voxtral("Hello.", tmp_path / "out.mp3")
        assert calls == []
        assert voxtral._sleep_timer is None

    async def test_wakes_before_the_job_and_sleeps_after_the_idle_period(
        self, tmp_path, server, monkeypatch
    ):
        monkeypatch.setattr(voxtral.config, "VOXTRAL_IDLE_SLEEP_SECONDS", 0.05)
        monkeypatch.setattr(voxtral.config, "VOXTRAL_SLEEP_LEVEL", 2)
        calls = self._controls(server)
        await voxtral.synthesize_voxtral("Hello.", tmp_path / "out.mp3")
        assert [path for path, _ in calls] == ["/v1/omni/wakeup"]
        assert calls[0][1] == {"stage_ids": [0]}
        await asyncio.sleep(0.3)
        assert [path for path, _ in calls] == ["/v1/omni/wakeup", "/v1/omni/sleep"]
        assert calls[1][1] == {"stage_ids": [0], "level": 2}

    async def test_a_new_job_cancels_the_pending_sleep(self, tmp_path, server, monkeypatch):
        monkeypatch.setattr(voxtral.config, "VOXTRAL_IDLE_SLEEP_SECONDS", 0.2)
        calls = self._controls(server)
        await voxtral.synthesize_voxtral("Hello.", tmp_path / "a.mp3")
        await voxtral.synthesize_voxtral("Hello.", tmp_path / "b.mp3")
        await asyncio.sleep(0.5)
        assert [path for path, _ in calls].count("/v1/omni/sleep") == 1

    async def test_a_failing_control_call_does_not_fail_the_job(
        self, tmp_path, server, monkeypatch
    ):
        monkeypatch.setattr(voxtral.config, "VOXTRAL_IDLE_SLEEP_SECONDS", 0.05)
        base = server.reply
        server.reply = lambda r: (
            httpx.Response(501, json={"detail": "no"}) if "/v1/omni/" in str(r.url) else base(r)
        )
        await voxtral.synthesize_voxtral("Hello.", tmp_path / "out.mp3")
        await asyncio.sleep(0.2)
        assert (tmp_path / "out.mp3").exists()

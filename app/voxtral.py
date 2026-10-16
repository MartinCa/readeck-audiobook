"""Voxtral TTS, called over HTTP on a vLLM-Omni server.

The server (see docker-compose.voxtral.yml) holds the model and the GPU; this
module only sends text and assembles the audio. Each request is independent:
the model carries no voice, pace or intonation from one request to the next, and
neither the open weights nor vLLM-Omni offer a mode that does. So an article is
sent in as few, as long, requests as the model handles well, cut at paragraph
boundaries, and the seams are smoothed in `app.audio`.
"""

import asyncio
import io
import logging
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

import httpx

from app import audio, config, tts

logger = logging.getLogger(__name__)

# The pause between chunks: longer where the cut falls on a paragraph break.
PARAGRAPH_GAP_SECONDS = 0.6
SENTENCE_GAP_SECONDS = 0.3

# Client errors that are worth retrying: request timeout and too many requests.
RETRYABLE_STATUSES = {408, 429}


# Idle sleep. The server's sleep mode gives the model's VRAM back while it keeps
# running (it must be started with --enable-sleep-mode). Jobs hold the server
# awake; once none is left, a timer puts it to sleep after the idle period.
_users = 0
_sleep_timer: asyncio.TimerHandle | None = None
_sleep_task: asyncio.Task | None = None
_sleep_lock: asyncio.Lock | None = None


async def _control(action: str, body: dict) -> bool:
    """POST to one of the server's /v1/omni controls. A failure is logged, never raised."""
    try:
        async with httpx.AsyncClient(timeout=config.VOXTRAL_TIMEOUT_SECONDS) as client:
            response = await client.post(f"{config.VOXTRAL_URL}/v1/omni/{action}", json=body)
        if response.status_code >= 400:
            logger.warning(
                "Voxtral %s returned %s: %s", action, response.status_code, response.text[:300]
            )
            return False
    except httpx.HTTPError as exc:
        logger.warning("Voxtral %s failed: %r", action, exc)
        return False
    return True


async def _sleep_stages() -> bool:
    """Put every configured stage to sleep; True if any went.

    All the stages go in one call. If the server refuses that (a stage that
    cannot sleep, say), they are tried one by one, so the ones that can still do.
    """
    stages, level = config.VOXTRAL_SLEEP_STAGES, config.VOXTRAL_SLEEP_LEVEL
    if await _control("sleep", {"stage_ids": stages, "level": level}):
        return True
    if len(stages) < 2:
        return False
    results = [await _control("sleep", {"stage_ids": [s], "level": level}) for s in stages]
    return any(results)


def _lock() -> asyncio.Lock:
    global _sleep_lock
    if _sleep_lock is None:
        _sleep_lock = asyncio.Lock()
    return _sleep_lock


async def _wake() -> None:
    """Make sure the server is awake. Waking one that is not asleep is a no-op there.

    A wake that fails is an error: the speech requests would go to a server whose
    model is not loaded and hang until they time out. (vLLM-Omni 0.30.0 cannot
    wake from level 2 at all; only a restart of the server brings it back.)
    """
    async with _lock():
        if not await _control("wakeup", {"stage_ids": config.VOXTRAL_SLEEP_STAGES}):
            raise RuntimeError(
                "Could not wake the Voxtral server from sleep; see the log above. If it was "
                "put to sleep at level 2, restart it: that level cannot be woken."
            )
        logger.debug("Voxtral server awake")


async def _sleep_if_idle() -> None:
    global _sleep_task
    async with _lock():
        if _users > 0:
            return
        if await _sleep_stages():
            logger.info(
                "Voxtral server asleep after %ss idle, releasing its GPU memory. "
                "The next Voxtral job wakes it.",
                config.VOXTRAL_IDLE_SLEEP_SECONDS,
            )
    _sleep_task = None


def _start_sleep_task() -> None:
    global _sleep_timer, _sleep_task
    _sleep_timer = None
    _sleep_task = asyncio.get_running_loop().create_task(_sleep_if_idle())


async def _acquire_server() -> None:
    global _users, _sleep_timer
    _users += 1
    if _sleep_timer is not None:
        _sleep_timer.cancel()
        _sleep_timer = None
    if config.VOXTRAL_IDLE_SLEEP_SECONDS > 0:
        await _wake()


def _release_server() -> None:
    global _users, _sleep_timer
    _users -= 1
    if _users == 0 and config.VOXTRAL_IDLE_SLEEP_SECONDS > 0:
        _sleep_timer = asyncio.get_running_loop().call_later(
            config.VOXTRAL_IDLE_SLEEP_SECONDS, _start_sleep_task
        )


@dataclass(frozen=True)
class Chunk:
    text: str
    gap: float  # seconds of silence to add after it


def split_text(text: str, max_chars: int) -> list[Chunk]:
    """Cut text into requests of up to ``max_chars``, preferring paragraph breaks.

    A paragraph that fits is never split. One that does not is cut at sentence
    boundaries, and a single sentence longer than ``max_chars`` is wrapped hard.
    Whole paragraphs and sentences are packed together up to the limit, so a
    long article becomes few requests.
    """
    units: list[tuple[str, bool]] = []  # (text, ends a paragraph)
    for paragraph in tts._PARAGRAPH_SPLIT.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            units.append((paragraph, True))
            continue
        sentences: list[str] = []
        for sentence in tts._SENTENCE_SPLIT.split(paragraph):
            sentence = sentence.strip()
            if sentence:
                sentences.extend(
                    [sentence]
                    if len(sentence) <= max_chars
                    else tts._hard_wrap(sentence, max_chars)
                )
        units.extend((sentence, False) for sentence in sentences[:-1])
        units.append((sentences[-1], True))

    chunks: list[Chunk] = []
    current, ends_paragraph = "", True
    for unit, ends in units:
        if current:
            joiner = "\n\n" if ends_paragraph else " "
            if len(current) + len(joiner) + len(unit) <= max_chars:
                current, ends_paragraph = current + joiner + unit, ends
                continue
            chunks.append(
                Chunk(current, PARAGRAPH_GAP_SECONDS if ends_paragraph else SENTENCE_GAP_SECONDS)
            )
        current, ends_paragraph = unit, ends
    if current:
        chunks.append(Chunk(current, 0.0))
    return chunks


async def _request_chunk(client: httpx.AsyncClient, text: str, voice: str) -> bytes:
    try:
        response = await client.post(
            f"{config.VOXTRAL_URL}/v1/audio/speech",
            json={
                "model": config.VOXTRAL_MODEL,
                "input": text,
                "voice": voice,
                "language": "English",
                "response_format": "wav",
                "max_new_tokens": config.VOXTRAL_MAX_NEW_TOKENS,
            },
        )
    except httpx.TransportError as exc:
        raise RuntimeError(
            f"Cannot reach the Voxtral server at {config.VOXTRAL_URL}: {exc!r}"
        ) from exc
    if response.status_code >= 400:
        message = f"Voxtral server returned {response.status_code}: {response.text[:300]}"
        # A request the server rejects (a wrong model or voice, say) fails the
        # same way every time; only a timeout, a rate limit or a server error is
        # worth another go.
        if response.status_code < 500 and response.status_code not in RETRYABLE_STATUSES:
            raise tts.PermanentError(message)
        raise RuntimeError(message)
    return response.content


def _read_wav(data: bytes) -> tuple[bytes, int]:
    """The PCM frames and sample rate of a 16-bit mono WAV."""
    try:
        with wave.open(io.BytesIO(data)) as piece:
            if piece.getsampwidth() != 2 or piece.getnchannels() != 1:
                raise RuntimeError(
                    "Voxtral returned audio that is not 16-bit mono "
                    f"({piece.getsampwidth() * 8}-bit, {piece.getnchannels()} channels)"
                )
            return piece.readframes(piece.getnframes()), piece.getframerate()
    except wave.Error as exc:
        raise RuntimeError(f"Voxtral returned audio that is not a readable WAV: {exc}") from exc


async def synthesize_voxtral(
    text: str,
    output_path: Path,
    voice: str = "",
    on_progress: tts.ProgressCallback | None = None,
):
    """Synthesize speech with Voxtral and write the finished MP3 to ``output_path``."""
    if not config.VOXTRAL_URL:
        raise RuntimeError("VOXTRAL_URL is not set, so there is no Voxtral server to call")
    voice = voice or config.VOXTRAL_VOICE
    chunks = split_text(text, config.VOXTRAL_CHUNK_CHARS)
    if not chunks:
        raise ValueError("No readable text to synthesize")

    await tts._report_progress(on_progress, 0, len(chunks))
    try:
        await _acquire_server()
        await _synthesize(chunks, output_path, voice, on_progress)
    finally:
        _release_server()


async def _synthesize(
    chunks: list[Chunk],
    output_path: Path,
    voice: str,
    on_progress: tts.ProgressCallback | None,
):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)
    writer = None
    try:
        async with httpx.AsyncClient(timeout=config.VOXTRAL_TIMEOUT_SECONDS) as client:
            for index, chunk in enumerate(chunks, 1):
                label = f"Voxtral chunk {index}/{len(chunks)}"

                async def request(chunk=chunk):
                    return await _request_chunk(client, chunk.text, voice)

                frames, rate = _read_wav(await tts._with_retries(request, label))
                frames, info = await asyncio.to_thread(
                    audio.condition_chunk, frames, rate, config.VOXTRAL_TARGET_DB
                )
                if not info:
                    raise RuntimeError(f"Voxtral produced no audible speech for {label}")
                logger.debug(
                    "%s: speech level %.1f to %.1f dBFS, trimmed %.2fs/%.2fs",
                    label,
                    info["start_db"],
                    info["end_db"],
                    info["lead"],
                    info["tail"],
                )
                if writer is None:
                    writer = wave.open(str(wav_path), "wb")
                    writer.setnchannels(1)
                    writer.setsampwidth(2)
                    writer.setframerate(rate)
                elif rate != writer.getframerate():
                    raise RuntimeError(
                        f"Voxtral changed sample rate mid-article ({writer.getframerate()} "
                        f"to {rate} Hz) at {label}"
                    )
                writer.writeframes(frames)
                writer.writeframes(b"\x00\x00" * int(rate * chunk.gap))
                await tts._report_progress(on_progress, index, len(chunks))
        writer.close()
        writer = None

        result = await asyncio.to_thread(
            subprocess.run, tts._wav_to_mp3_command(wav_path, output_path), capture_output=True
        )
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")
            raise RuntimeError(f"ffmpeg failed encoding Voxtral output to mp3: {stderr[-2000:]}")
    finally:
        if writer is not None:
            writer.close()
        wav_path.unlink(missing_ok=True)

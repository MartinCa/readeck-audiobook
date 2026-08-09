"""Text preparation and TTS backend implementations."""

import asyncio
import html
import logging
import os
import re
import threading
import unicodedata
from pathlib import Path

from app import config

logger = logging.getLogger(__name__)

AUDIO_DIR = config.AUDIO_DIR

# Language code → preferred edge-tts voice
LANG_VOICE_MAP = {
    "en": "en-US-AriaNeural",
    "de": "de-DE-KatjaNeural",
    "fr": "fr-FR-DeniseNeural",
    "es": "es-ES-ElviraNeural",
    "it": "it-IT-ElsaNeural",
    "nl": "nl-NL-ColetteNeural",
    "pt": "pt-PT-RaquelNeural",
    "pl": "pl-PL-ZofiaNeural",
    "sv": "sv-SE-SofieNeural",
    "da": "da-DK-ChristelNeural",
    "nb": "nb-NO-PernilleNeural",
    "fi": "fi-FI-NooraNeural",
}

DEFAULT_VOICE = config.EDGE_TTS_VOICE
# Kokoro-82M only covers a handful of languages; everything else still goes
# through edge-tts even when TTS_ENGINE=kokoro. English only for now.
KOKORO_SUPPORTED_LANGS = {"en"}

# sherpa-onnx picks a Kokoro speaker by integer id, not by name. Keeping the
# familiar names in KOKORO_VOICE means mapping them here — this is the published
# id→name table for the kokoro-multi-lang-v1_0 model that Dockerfile.kokoro
# bundles. Changing the bundled model means changing this table.
KOKORO_VOICES = {
    "af_alloy": 0, "af_aoede": 1, "af_bella": 2, "af_heart": 3,
    "af_jessica": 4, "af_kore": 5, "af_nicole": 6, "af_nova": 7,
    "af_river": 8, "af_sarah": 9, "af_sky": 10, "am_adam": 11,
    "am_echo": 12, "am_eric": 13, "am_fenrir": 14, "am_liam": 15,
    "am_michael": 16, "am_onyx": 17, "am_puck": 18, "am_santa": 19,
    "bf_alice": 20, "bf_emma": 21, "bf_isabella": 22, "bf_lily": 23,
    "bm_daniel": 24, "bm_fable": 25, "bm_george": 26, "bm_lewis": 27,
    "ef_dora": 28, "em_alex": 29, "ff_siwis": 30, "hf_alpha": 31,
    "hf_beta": 32, "hm_omega": 33, "hm_psi": 34, "if_sara": 35,
    "im_nicola": 36, "jf_alpha": 37, "jf_gongitsune": 38, "jf_nezumi": 39,
    "jf_tebukuro": 40, "jm_kumo": 41, "pf_dora": 42, "pm_alex": 43,
    "pm_santa": 44, "zf_xiaobei": 45, "zf_xiaoni": 46, "zf_xiaoxiao": 47,
    "zf_xiaoyi": 48, "zm_yunjian": 49, "zm_yunxi": 50, "zm_yunxia": 51,
    "zm_yunyang": 52,
}  # fmt: skip

# Also model-specific. The archive ships a gb-en lexicon alongside these, but
# sherpa-onnx's own documented invocation leaves it out — loading both would let
# British pronunciations win for the American voices we default to.
KOKORO_LEXICONS = ("lexicon-us-en.txt", "lexicon-zh.txt")

ENGINE_EDGE = "edge-tts"
ENGINE_KOKORO = "kokoro"


def _pick_voice(lang: str) -> str:
    if not lang:
        return DEFAULT_VOICE
    base = lang.split("-")[0].lower()
    return LANG_VOICE_MAP.get(base, DEFAULT_VOICE)


def resolve_engine_and_voice(lang: str) -> tuple[str, str]:
    """Decide which engine and voice a job in `lang` will actually use.

    Resolved once when the job is created and then stored on the row, so the
    Jobs page reports the engine that really ran rather than whatever
    TTS_ENGINE happens to be set to at display time.
    """
    base = (lang or "").split("-")[0].lower()
    if config.TTS_ENGINE == ENGINE_KOKORO and base in KOKORO_SUPPORTED_LANGS:
        return ENGINE_KOKORO, config.KOKORO_VOICE
    return ENGINE_EDGE, _pick_voice(lang)


def resolve_kokoro_sid(voice: str) -> int:
    """Map a Kokoro voice name to the speaker id sherpa-onnx expects.

    A typo would otherwise synthesize a whole article in the wrong voice (or in
    the wrong language — the ids run across all of the model's locales), so an
    unknown name fails the job instead.
    """
    try:
        return KOKORO_VOICES[voice]
    except KeyError:
        english = sorted(v for v in KOKORO_VOICES if v[0] in "ab")
        raise ValueError(
            f"Unknown Kokoro voice {voice!r}. English voices: {', '.join(english)}"
        ) from None


# ── Filenames ──────────────────────────────────────────────────────────────────

# NFKD strips most diacritics, but a few letters carry no combining form and
# would simply vanish — awkward for the Nordic languages this app supports.
_TRANSLITERATE = str.maketrans(
    {
        "ø": "o", "Ø": "O", "æ": "ae", "Æ": "Ae", "å": "a", "Å": "A",
        "ß": "ss", "đ": "d", "Đ": "D", "ł": "l", "Ł": "L",
        "þ": "th", "Þ": "Th", "ð": "d", "Ð": "D", "œ": "oe", "Œ": "Oe",
    }
)  # fmt: skip

SLUG_MAX_LENGTH = 80


def slugify(value: str, max_length: int = SLUG_MAX_LENGTH) -> str:
    """Turn an article title into a lowercase, filesystem-safe slug."""
    if not value:
        return ""
    ascii_text = (
        unicodedata.normalize("NFKD", value.translate(_TRANSLITERATE))
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_text).strip("-").lower()
    if len(slug) > max_length:
        # Prefer cutting on a word boundary rather than mid-word.
        head = slug[:max_length]
        slug = head.rsplit("-", 1)[0] if "-" in head.strip("-") else head
    return slug.strip("-")


def audio_filename(title: str, job_id: str, existing: set[str] | None = None) -> str:
    """Build a readable, unique MP3 filename for a job.

    Named after the article rather than the job UUID so downloaded files are
    recognisable in a media player. The short job-id suffix keeps two articles
    with the same title apart and ties the file back to its job.
    """
    slug = slugify(title) or "article"
    suffix = job_id.replace("-", "")
    for length in (8, 16, len(suffix)):
        candidate = f"{slug}-{suffix[:length]}.mp3"
        taken = existing if existing is not None else set()
        if candidate not in taken and not (AUDIO_DIR / candidate).exists():
            return candidate
    return f"{slug}-{suffix}.mp3"


# ── Markdown → speakable text ──────────────────────────────────────────────────

_FENCED_CODE = re.compile(r"(?:^|\n)[ \t]*(?:```|~~~)[\s\S]*?(?:```|~~~)[ \t]*(?=\n|$)")
_INLINE_CODE = re.compile(r"`+([^`]+)`+")
_IMAGE = re.compile(r"!\[[^\]]*\]\((?:[^()]|\([^()]*\))*\)|!\[[^\]]*\]\[[^\]]*\]")
_INLINE_LINK = re.compile(r"\[([^\]]*)\]\((?:[^()]|\([^()]*\))*\)")
_REFERENCE_LINK = re.compile(r"\[([^\]]*)\]\[[^\]]*\]")
_LINK_DEFINITION = re.compile(r"^[ \t]{0,3}\[[^\]]+\]:[ \t]*\S+.*$", re.MULTILINE)
_FOOTNOTE_REF = re.compile(r"\[\^[^\]]*\]")
_AUTOLINK = re.compile(r"<(?:https?|mailto):[^>\s]+>")
_HTML_TAG = re.compile(r"<[^>\n]{1,200}>")
_HTML_COMMENT = re.compile(r"<!--[\s\S]*?-->")
_BARE_URL = re.compile(r"(?<![\w/])(?:https?://|www\.)\S+")

_HEADING = re.compile(r"^[ \t]{0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_SETEXT_UNDERLINE = re.compile(r"^[ \t]{0,3}(?:=+|-+)[ \t]*$")
_HORIZONTAL_RULE = re.compile(r"^[ \t]{0,3}([-*_])[ \t]*(?:\1[ \t]*){2,}$")
_BLOCKQUOTE = re.compile(r"^[ \t]*(?:>[ \t]?)+")
_LIST_MARKER = re.compile(r"^[ \t]*(?:[-*+]|\d{1,3}[.)])[ \t]+")
_TABLE_ROW = re.compile(r"^[ \t]*\|.*$")
_TABLE_DIVIDER = re.compile(r"^[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")

# Emphasis markers are only treated as formatting when they hug the text they
# wrap. Without the guards, `snake_case_name` loses its underscores and the
# multiplication in "3 * 4" disappears.
_EMPHASIS = [
    re.compile(r"\*\*\*(\S(?:[^*\n]*?\S)?)\*\*\*"),
    re.compile(r"\*\*(\S(?:[^*\n]*?\S)?)\*\*"),
    re.compile(r"(?<!\*)\*(\S(?:[^*\n]*?\S)?)\*(?!\*)"),
    re.compile(r"(?<![\w\\])___(\S(?:[^_\n]*?\S)?)___(?!\w)"),
    re.compile(r"(?<![\w\\])__(\S(?:[^_\n]*?\S)?)__(?!\w)"),
    re.compile(r"(?<![\w\\])_(\S(?:[^_\n]*?\S)?)_(?!\w)"),
]
_STRIKETHROUGH = re.compile(r"~~(\S(?:[^~\n]*?\S)?)~~")

_SENTENCE_END = ".!?:;,…\"')]"


def _table_row_to_sentence(line: str) -> str:
    """Render a table row as prose — spoken pipes are unlistenable."""
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    cells = [c for c in cells if c]
    if not cells:
        return ""
    text = ", ".join(cells)
    return text if text.endswith(tuple(_SENTENCE_END)) else text + "."


def _clean_block_markup(text: str) -> str:
    """Strip line-level structure: headings, lists, quotes, rules and tables."""
    out: list[str] = []
    for line in text.split("\n"):
        if _HORIZONTAL_RULE.match(line) or _SETEXT_UNDERLINE.match(line):
            out.append("")
            continue
        if _TABLE_DIVIDER.match(line) and "|" in line:
            continue
        if _TABLE_ROW.match(line):
            out.append(_table_row_to_sentence(line))
            continue

        line = _BLOCKQUOTE.sub("", line)

        heading = _HEADING.match(line)
        if heading:
            title = heading.group(2).strip()
            # A trailing stop makes the engine pause instead of running the
            # heading into the first sentence of the section.
            if title and not title.endswith(tuple(_SENTENCE_END)):
                title += "."
            out.append(title)
            continue

        out.append(_LIST_MARKER.sub("", line).rstrip())
    return "\n".join(out)


def _clean_markdown(text: str) -> str:
    """Strip Markdown formatting so TTS reads cleaner text."""
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _HTML_COMMENT.sub("", text)
    text = _FENCED_CODE.sub("\n", text)
    text = _LINK_DEFINITION.sub("", text)
    text = _IMAGE.sub("", text)
    text = _INLINE_LINK.sub(r"\1", text)
    text = _REFERENCE_LINK.sub(r"\1", text)
    text = _FOOTNOTE_REF.sub("", text)
    text = _AUTOLINK.sub("", text)
    # Inline code keeps its contents: dropping them mid-sentence leaves
    # fragments like "Run  to save your work."
    text = _INLINE_CODE.sub(r"\1", text)

    text = _clean_block_markup(text)

    for pattern in _EMPHASIS:
        text = pattern.sub(r"\1", text)
    text = _STRIKETHROUGH.sub(r"\1", text)

    text = _HTML_TAG.sub("", text)
    text = html.unescape(text)
    text = _BARE_URL.sub("", text)

    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+(\n)", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── Chunking ───────────────────────────────────────────────────────────────────

_PARAGRAPH_SPLIT = re.compile(r"\n{2,}")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def _hard_wrap(piece: str, max_chars: int) -> list[str]:
    return [piece[i : i + max_chars] for i in range(0, len(piece), max_chars)]


def _split_text(text: str, max_chars: int) -> list[str]:
    """Break text into synthesis-sized pieces on natural boundaries.

    One request for a whole long-form article is the case most likely to fail,
    and it fails only after doing all of the work.
    """
    if len(text) <= max_chars:
        return [text] if text.strip() else []

    pieces: list[str] = []
    for paragraph in _PARAGRAPH_SPLIT.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            pieces.append(paragraph)
            continue
        for sentence in _SENTENCE_SPLIT.split(paragraph):
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) <= max_chars:
                pieces.append(sentence)
            else:
                pieces.extend(_hard_wrap(sentence, max_chars))

    # Recombine adjacent pieces so we make as few requests as possible.
    merged: list[str] = []
    for piece in pieces:
        if merged and len(merged[-1]) + len(piece) + 1 <= max_chars:
            merged[-1] = f"{merged[-1]}\n{piece}"
        else:
            merged.append(piece)
    return merged


async def _with_retries(factory, description: str):
    """Run an async factory, retrying transient synthesis failures."""
    last_error: Exception | None = None
    for attempt in range(1, config.TTS_MAX_RETRIES + 1):
        try:
            return await factory()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt == config.TTS_MAX_RETRIES:
                break
            delay = 2 ** (attempt - 1)
            logger.warning(
                "%s failed (attempt %d/%d): %s — retrying in %ss",
                description,
                attempt,
                config.TTS_MAX_RETRIES,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
    raise RuntimeError(
        f"{description} failed after {config.TTS_MAX_RETRIES} attempt(s): {last_error}"
    ) from last_error


# ── Backends ───────────────────────────────────────────────────────────────────


async def _edge_tts_chunk(text: str, voice: str) -> bytes:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice)
    buffer = bytearray()
    async for item in communicate.stream():
        if item["type"] == "audio":
            buffer.extend(item["data"])
    if not buffer:
        raise RuntimeError("edge-tts returned no audio")
    return bytes(buffer)


async def synthesize_edge_tts(text: str, output_path: Path, voice: str):
    chunks = _split_text(text, config.TTS_CHUNK_CHARS)
    if not chunks:
        raise ValueError("No readable text to synthesize")

    with output_path.open("wb") as fh:
        for index, chunk in enumerate(chunks, 1):

            async def synthesize(chunk=chunk):
                return await _edge_tts_chunk(chunk, voice)

            fh.write(await _with_retries(synthesize, f"edge-tts chunk {index}/{len(chunks)}"))


_kokoro_tts = None
# One cached engine is shared by every worker thread, and MAX_CONCURRENT_JOBS
# defaults to 2. sherpa-onnx makes no thread-safety promise about OfflineTts, so
# serialize synthesis rather than find out the hard way.
_kokoro_lock = threading.Lock()


def _kokoro_model_file(name: str) -> str:
    path = config.KOKORO_MODEL_DIR / name
    if not path.exists():
        raise RuntimeError(
            f"Kokoro model file {path} is missing. KOKORO_MODEL_DIR should point at an "
            "extracted sherpa-onnx Kokoro model directory."
        )
    return str(path)


def _get_kokoro_tts():
    """Lazily construct and cache the sherpa-onnx Kokoro engine (loads the model once)."""
    global _kokoro_tts
    if _kokoro_tts is None:
        import sherpa_onnx

        provider = config.KOKORO_PROVIDER
        model_config = sherpa_onnx.OfflineTtsModelConfig(
            kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
                model=_kokoro_model_file("model.onnx"),
                voices=_kokoro_model_file("voices.bin"),
                tokens=_kokoro_model_file("tokens.txt"),
                data_dir=_kokoro_model_file("espeak-ng-data"),
                lexicon=",".join(
                    str(config.KOKORO_MODEL_DIR / name)
                    for name in KOKORO_LEXICONS
                    if (config.KOKORO_MODEL_DIR / name).exists()
                ),
            ),
            provider=provider,
            num_threads=config.KOKORO_NUM_THREADS,
            debug=config.KOKORO_DEBUG,
        )
        # No max_num_sentences here: sherpa-onnx ignores it for Kokoro models and
        # logs a warning for any value but the default. Kokoro does its own
        # sentence splitting internally, so long articles go in as one string.
        _kokoro_tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(model=model_config))
        logger.info(
            "Kokoro model loaded from %s: %s speakers (requested provider: %s, threads: %s)",
            config.KOKORO_MODEL_DIR,
            _kokoro_tts.num_speakers,
            provider,
            config.KOKORO_NUM_THREADS,
        )
        if _kokoro_tts.num_speakers != len(KOKORO_VOICES):
            logger.warning(
                "KOKORO_VOICES maps %s names but the loaded model has %s speakers — the "
                "name→id table is specific to kokoro-multi-lang-v1_0. Voices may be wrong.",
                len(KOKORO_VOICES),
                _kokoro_tts.num_speakers,
            )
        if provider == "cuda" and not config.KOKORO_DEBUG:
            # sherpa-onnx has no equivalent of torch.cuda.is_available(): when the
            # CUDA execution provider fails to register it falls back to CPU
            # without complaint, and its debug output is the only reliable signal.
            logger.info(
                "CUDA requested. sherpa-onnx falls back to CPU silently if the provider "
                "fails to register — set KOKORO_DEBUG=1 to have it log which providers did."
            )
    return _kokoro_tts


async def synthesize_kokoro(text: str, output_path: Path, voice: str = ""):
    """Synthesize speech with the local Kokoro-82M model via sherpa-onnx."""
    import subprocess
    import tempfile

    import sherpa_onnx
    import soundfile as sf

    voice = voice or config.KOKORO_VOICE
    sid = resolve_kokoro_sid(voice)

    # Synthesized in pieces for the same reason edge-tts is, plus one specific to
    # a local model: Kokoro returns the whole waveform as one in-memory array, so
    # a long article means hundreds of MB of float samples in a single
    # allocation. Feeding a full-length article in one call got the container
    # killed outright — no Python exception, just a dead process and a job that
    # burned all its attempts.
    chunks = _split_text(text, config.TTS_CHUNK_CHARS)
    if not chunks:
        raise ValueError("No readable text to synthesize")

    def _run_sync():
        engine = _get_kokoro_tts()
        if sid >= engine.num_speakers:
            raise RuntimeError(
                f"Kokoro voice {voice!r} maps to speaker id {sid}, but the loaded "
                f"model only has {engine.num_speakers} speakers."
            )
        gen_config = sherpa_onnx.GenerationConfig()
        gen_config.sid = sid
        gen_config.speed = 1.0

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = Path(tmp.name)
        try:
            # Appended to the WAV as each piece is produced rather than
            # concatenated at the end, so peak memory stays at one chunk of audio
            # however long the article is.
            writer = None
            try:
                for index, chunk in enumerate(chunks, 1):
                    with _kokoro_lock:
                        audio = engine.generate(chunk, gen_config)
                    if audio.sample_rate <= 0 or len(audio.samples) == 0:
                        raise RuntimeError(
                            f"Kokoro produced no audio for chunk {index}/{len(chunks)}"
                        )
                    if writer is None:
                        writer = sf.SoundFile(
                            str(wav_path),
                            mode="w",
                            samplerate=audio.sample_rate,
                            channels=1,
                            subtype="PCM_16",
                        )
                    writer.write(audio.samples)
                    del audio
            finally:
                if writer is not None:
                    writer.close()

            ffmpeg_cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(wav_path),
                "-codec:a",
                "libmp3lame",
                "-qscale:a",
                "2",
                str(output_path),
            ]
            result = subprocess.run(ffmpeg_cmd, capture_output=True)
            if result.returncode != 0:
                stderr = result.stderr.decode(errors="replace")
                raise RuntimeError(f"ffmpeg failed encoding Kokoro output to mp3: {stderr[-2000:]}")
        finally:
            wav_path.unlink(missing_ok=True)

    await asyncio.to_thread(_run_sync)


# ── Tagging ────────────────────────────────────────────────────────────────────


def _tag_mp3(path: Path, title: str, url: str, engine: str) -> None:
    """Write ID3 tags so players show the article instead of a bare filename."""
    try:
        from mutagen.id3 import ID3, TALB, TCON, TIT2, TPE1, WOAS, ID3NoHeaderError

        try:
            tags = ID3(str(path))
        except ID3NoHeaderError:
            tags = ID3()
        tags.add(TIT2(encoding=3, text=title or path.stem))
        tags.add(TPE1(encoding=3, text="Readeck Audiobook"))
        tags.add(TALB(encoding=3, text="Readeck"))
        tags.add(TCON(encoding=3, text="Speech"))
        if url:
            tags.add(WOAS(url=url))
        tags.save(str(path), v2_version=3)
    except Exception as exc:  # tagging is cosmetic — never fail a job over it
        logger.warning("Could not write ID3 tags to %s: %s", path.name, exc)


# ── Entry point ────────────────────────────────────────────────────────────────


async def generate_audio(
    job_id: str,
    text: str,
    *,
    title: str = "",
    url: str = "",
    engine: str = "",
    voice: str = "",
    lang: str = "",
) -> Path:
    """Generate audio for a job and return the path to the finished MP3."""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    cleaned = _clean_markdown(text)
    if not cleaned:
        raise ValueError("No readable text found in bookmark article")

    if not engine or not voice:
        resolved_engine, resolved_voice = resolve_engine_and_voice(lang)
        engine = engine or resolved_engine
        voice = voice or resolved_voice

    output_path = AUDIO_DIR / audio_filename(title, job_id)
    # Synthesize to a sibling temp file and rename into place, so a crash
    # mid-write can never leave a truncated MP3 at the final path.
    partial_path = output_path.with_name(output_path.name + ".part")
    try:
        if engine == ENGINE_KOKORO:
            await synthesize_kokoro(cleaned, partial_path, voice)
        else:
            await synthesize_edge_tts(cleaned, partial_path, voice)

        if not partial_path.exists() or partial_path.stat().st_size == 0:
            raise RuntimeError(f"{engine} produced an empty audio file")

        _tag_mp3(partial_path, title, url, engine)
        os.replace(partial_path, output_path)
    finally:
        partial_path.unlink(missing_ok=True)

    return output_path

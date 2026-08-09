"""TTS backend implementations."""

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

AUDIO_DIR = Path("/app/audio")

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

DEFAULT_VOICE = os.environ.get("EDGE_TTS_VOICE", "en-US-AriaNeural")
TTS_ENGINE = os.environ.get("TTS_ENGINE", "edge-tts")
KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "af_heart")
# Kokoro-82M only covers a handful of languages; everything else still goes
# through edge-tts even when TTS_ENGINE=kokoro. English only for now.
KOKORO_LANG_CODE = "a"  # American English
KOKORO_SUPPORTED_LANGS = {"en"}


def _pick_voice(lang: str) -> str:
    if not lang:
        return DEFAULT_VOICE
    base = lang.split("-")[0].lower()
    return LANG_VOICE_MAP.get(base, DEFAULT_VOICE)


def _clean_markdown(text: str) -> str:
    """Strip Markdown formatting so TTS reads cleaner text."""
    # Remove code blocks
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`[^`]+`", "", text)
    # Remove images and links (keep link text)
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    # Remove heading markers
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove bold/italic
    text = re.sub(r"\*{1,3}(.*?)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}(.*?)_{1,3}", r"\1", text)
    # Remove horizontal rules
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    # Collapse excess blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def synthesize_edge_tts(text: str, output_path: Path, voice: str):
    import edge_tts

    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(output_path))


_kokoro_pipeline = None


def _get_kokoro_pipeline():
    """Lazily construct and cache the Kokoro pipeline (loads the model once per process)."""
    global _kokoro_pipeline
    if _kokoro_pipeline is None:
        from kokoro import KPipeline

        _kokoro_pipeline = KPipeline(lang_code=KOKORO_LANG_CODE)
    return _kokoro_pipeline


async def synthesize_kokoro(text: str, output_path: Path, voice: str = KOKORO_VOICE):
    """Synthesize speech with the local Kokoro-82M model (CUDA if available, else CPU)."""
    import asyncio
    import subprocess
    import tempfile

    import numpy as np
    import soundfile as sf

    def _run_sync():
        pipeline = _get_kokoro_pipeline()
        chunks = [audio for _, _, audio in pipeline(text, voice=voice)]
        if not chunks:
            raise RuntimeError("Kokoro produced no audio")
        audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = Path(tmp.name)
        try:
            sf.write(str(wav_path), audio, 24000)
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


async def generate_audio(job_id: str, text: str, lang: str) -> Path:
    """Generate audio and return the path to the output file."""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    output_path = AUDIO_DIR / f"{job_id}.mp3"

    cleaned = _clean_markdown(text)
    if not cleaned:
        raise ValueError("No readable text found in bookmark article")

    base_lang = (lang or "").split("-")[0].lower()
    if TTS_ENGINE == "kokoro" and base_lang in KOKORO_SUPPORTED_LANGS:
        await synthesize_kokoro(cleaned, output_path)
    else:
        voice = _pick_voice(lang)
        await synthesize_edge_tts(cleaned, output_path, voice)

    return output_path

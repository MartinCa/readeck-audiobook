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
EBOOK2AUDIOBOOK_URL = os.environ.get("EBOOK2AUDIOBOOK_URL", "")


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


async def synthesize_ebook2audiobook(epub_bytes: bytes, output_path: Path, lang: str = "en"):
    """Call the ebook2audiobook Gradio service via gradio_client.

    The Gradio app exposes a REST-like API that gradio_client wraps.
    The client handles uploading the EPUB and downloading the result.
    """
    import asyncio
    import shutil
    import tempfile

    if not EBOOK2AUDIOBOOK_URL:
        raise RuntimeError("EBOOK2AUDIOBOOK_URL is not set — add it to your .env")

    def _run_sync():
        from gradio_client import Client, handle_file

        # Write the EPUB bytes to a temp file so gradio_client can upload it
        with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
            tmp.write(epub_bytes)
            epub_path = tmp.name

        try:
            client = Client(EBOOK2AUDIOBOOK_URL)
            # ebook2audiobook's Gradio interface: first positional arg is the ebook file.
            # Additional args (voice, language, custom_model, …) all have defaults so
            # we only pass the ebook and the language.
            result = client.predict(
                ebook_file_input=handle_file(epub_path),
                target_voice_file=None,
                language=lang or "en",
                use_custom_model=False,
                custom_model_file=None,
                custom_config_file=None,
                custom_vocab_file=None,
                custom_model_url="",
                temperature=0.65,
                length_penalty=1.0,
                repetition_penalty=2.5,
                top_k=50,
                top_p=0.8,
                speed=1.0,
                enable_text_splitting=True,
                api_name="/convert_ebook",
            )
            # result is a tuple; the audio file path is the first element
            audio_result = result[0] if isinstance(result, (list, tuple)) else result
            shutil.copy(str(audio_result), str(output_path))
        finally:
            Path(epub_path).unlink(missing_ok=True)

    await asyncio.to_thread(_run_sync)


async def generate_audio(
    job_id: str, text: str, lang: str, epub_bytes: bytes | None = None
) -> Path:
    """Generate audio and return the path to the output file."""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    if TTS_ENGINE == "ebook2audiobook" and epub_bytes:
        output_path = AUDIO_DIR / f"{job_id}.mp3"
        await synthesize_ebook2audiobook(epub_bytes, output_path, lang=lang)
    else:
        if TTS_ENGINE == "ebook2audiobook":
            logger.warning(
                "TTS_ENGINE=ebook2audiobook but no EPUB bytes available for job %s; "
                "falling back to edge-tts",
                job_id,
            )
        voice = _pick_voice(lang)
        output_path = AUDIO_DIR / f"{job_id}.mp3"
        cleaned = _clean_markdown(text)
        if not cleaned:
            raise ValueError("No readable text found in bookmark article")
        await synthesize_edge_tts(cleaned, output_path, voice)

    return output_path

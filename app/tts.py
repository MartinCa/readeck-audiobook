"""TTS backend implementations."""
import os
import re
from pathlib import Path

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


async def synthesize_ebook2audiobook(epub_bytes: bytes, output_path: Path):
    import httpx
    if not EBOOK2AUDIOBOOK_URL:
        raise RuntimeError("EBOOK2AUDIOBOOK_URL is not set")
    async with httpx.AsyncClient(timeout=600) as client:
        # POST EPUB, poll for result — implementation depends on the API
        resp = await client.post(
            f"{EBOOK2AUDIOBOOK_URL}/convert",
            content=epub_bytes,
            headers={"Content-Type": "application/epub+zip"},
        )
        resp.raise_for_status()
        output_path.write_bytes(resp.content)


async def generate_audio(job_id: str, text: str, lang: str,
                         epub_bytes: bytes | None = None) -> Path:
    """Generate audio and return the path to the output file."""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    if TTS_ENGINE == "ebook2audiobook" and epub_bytes:
        output_path = AUDIO_DIR / f"{job_id}.mp3"
        await synthesize_ebook2audiobook(epub_bytes, output_path)
    else:
        voice = _pick_voice(lang)
        output_path = AUDIO_DIR / f"{job_id}.mp3"
        cleaned = _clean_markdown(text)
        if not cleaned:
            raise ValueError("No readable text found in bookmark article")
        await synthesize_edge_tts(cleaned, output_path, voice)

    return output_path

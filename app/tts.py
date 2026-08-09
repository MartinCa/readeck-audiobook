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
EBOOK2AUDIOBOOK_CMD = os.environ.get("EBOOK2AUDIOBOOK_CMD", "/app/ebook2audiobook.command")
EBOOK2AUDIOBOOK_TIMEOUT = int(os.environ.get("EBOOK2AUDIOBOOK_TIMEOUT_SECONDS", "7200"))


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


def _find_output_file(output_dir: Path) -> Path:
    """Locate the single audio file ebook2audiobook produced under output_dir."""
    candidates = [p for p in output_dir.rglob("*") if p.is_file()]
    if not candidates:
        raise RuntimeError(f"ebook2audiobook produced no output file in {output_dir}")
    if len(candidates) > 1:
        names = ", ".join(p.name for p in candidates)
        raise RuntimeError(f"ebook2audiobook produced multiple output files: {names}")
    return candidates[0]


async def synthesize_ebook2audiobook(epub_bytes: bytes, output_path: Path, lang: str = "en"):
    """Convert an EPUB via ebook2audiobook's headless CLI, run as a local subprocess.

    ebook2audiobook's Gradio UI does not expose a stable HTTP API for conversion —
    the only supported non-interactive path is `--headless` mode. This assumes the
    ebook2audiobook runtime is bundled into this image (see Dockerfile.ebook2audiobook)
    so the CLI is available locally at EBOOK2AUDIOBOOK_CMD.
    """
    import asyncio
    import shutil
    import tempfile

    if not Path(EBOOK2AUDIOBOOK_CMD).exists():
        raise RuntimeError(
            f"{EBOOK2AUDIOBOOK_CMD} not found — TTS_ENGINE=ebook2audiobook requires an image "
            "built from Dockerfile.ebook2audiobook, which bundles the ebook2audiobook runtime."
        )

    tmp_dir = Path(tempfile.mkdtemp(prefix="e2a-job-"))
    try:
        input_path = tmp_dir / "input.epub"
        input_path.write_bytes(epub_bytes)
        output_dir = tmp_dir / "output"
        output_dir.mkdir()

        base_lang = (lang or "en").split("-")[0].lower()
        cmd = [
            EBOOK2AUDIOBOOK_CMD,
            "--headless",
            "--ebook",
            str(input_path),
            "--language",
            base_lang,
            "--output_format",
            "mp3",
            "--output_dir",
            str(output_dir),
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=EBOOK2AUDIOBOOK_TIMEOUT)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                f"ebook2audiobook conversion timed out after {EBOOK2AUDIOBOOK_TIMEOUT}s"
            ) from None

        output = stdout.decode(errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(
                f"ebook2audiobook exited with code {proc.returncode}: {output[-4000:]}"
            )

        result_file = _find_output_file(output_dir)
        shutil.copy(str(result_file), str(output_path))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


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

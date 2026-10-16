"""Render the same article with several TTS engines so they can be compared by ear.

Run it from the repository root on the machine with the GPU:

    python scripts/compare_tts.py article.md --engines kokoro,edge,chatterbox-turbo

It cleans the text exactly as the app does, takes the first --max-chars of it
(the whole article with --full), synthesizes it once per engine and writes
``<engine>.mp3`` files, a ``results.json`` and an ``index.html`` with one audio
player per file into --out (default ./tts-compare). Results from several runs
into the same directory are merged, so engines that need different Python
environments can be run separately.

Engines (each is skipped with a message when its dependencies are missing):

    kokoro           the app's Kokoro/sherpa-onnx path; needs sherpa-onnx and
                     KOKORO_MODEL_DIR (and KOKORO_PROVIDER=cuda for the GPU)
    edge             edge-tts; needs network access
    chatterbox-turbo Chatterbox Turbo (350M, English)
    chatterbox       the original Chatterbox (500M), which has a default voice
    voxtral          Mistral Voxtral TTS, called over HTTP on a vLLM-Omni server
                     you start separately (see --voxtral-url); stdlib only

The two Chatterbox engines need PyTorch and are best run from a separate venv:

    python -m venv .venv-chatterbox && . .venv-chatterbox/bin/activate
    pip install -r requirements.txt chatterbox-tts
    python scripts/compare_tts.py article.md --engines chatterbox-turbo,chatterbox

Chatterbox Turbo may ship without a built-in voice; if it complains, pass
--ref-audio with a clean 10 second recording of the voice to imitate (it must be
longer than 5 seconds). The weights download from Hugging Face on first use.
"""

import argparse
import asyncio
import gc
import html
import io
import json
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import tts  # noqa: E402

# Gap between Chatterbox chunks, which it generates one at a time.
CHUNK_GAP_SECONDS = 0.3
ENGINES = ("kokoro", "edge", "chatterbox-turbo", "chatterbox", "voxtral")
VOXTRAL_MODEL = "mistralai/Voxtral-4B-TTS-2603"


def truncate(cleaned: str, max_chars: int) -> str:
    """The first stretch of cleaned text, ending on a paragraph or sentence boundary."""
    pieces = tts._split_text(cleaned, max_chars)
    return pieces[0] if pieces else ""


def encode_mp3(wav_path: Path, mp3_path: Path, tempo: float = 1.0) -> None:
    # atempo changes the speed without changing the pitch.
    slow = ["-af", f"atempo={tempo}"] if tempo != 1.0 else []
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(wav_path),
            *slow,
            "-codec:a",
            "libmp3lame",
            "-qscale:a",
            "2",
            str(mp3_path),
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.decode(errors='replace')[-1500:]}")


def run_kokoro(text: str, out: Path, voice: str) -> dict:
    try:
        asyncio.run(tts.synthesize_kokoro(text, out, voice))
    finally:
        tts.release_kokoro()
    return {}


def run_edge(text: str, out: Path, voice: str) -> dict:
    asyncio.run(tts.synthesize_edge_tts(text, out, voice))
    return {}


def run_chatterbox(
    text: str,
    out: Path,
    turbo: bool,
    ref_audio: str | None,
    chunk_chars: int,
    tempo: float = 1.0,
    options: dict | None = None,
) -> dict:
    import numpy as np
    import soundfile as sf
    import torch

    if turbo:
        from chatterbox.tts_turbo import ChatterboxTurboTTS as Model
    else:
        from chatterbox.tts import ChatterboxTTS as Model

    chunks = tts._split_text(text, chunk_chars)
    kwargs = dict(options or {})
    if ref_audio:
        kwargs["audio_prompt_path"] = ref_audio

    started = time.perf_counter()
    model = Model.from_pretrained(device="cuda")
    load_seconds = time.perf_counter() - started
    torch.cuda.reset_peak_memory_stats()

    wav_path = out.with_suffix(".wav")
    gap = np.zeros(int(model.sr * CHUNK_GAP_SECONDS), dtype="float32")
    try:
        with sf.SoundFile(str(wav_path), mode="w", samplerate=model.sr, channels=1) as writer:
            for index, chunk in enumerate(chunks, 1):
                print(f"    chunk {index}/{len(chunks)}", flush=True)
                try:
                    wav = model.generate(chunk, **kwargs)
                except AssertionError as exc:
                    raise RuntimeError(
                        f"{exc} - pass --ref-audio with a clean recording of 5+ seconds."
                    ) from exc
                writer.write(wav.squeeze(0).detach().cpu().numpy().astype("float32"))
                writer.write(gap)
        peak_vram_gb = torch.cuda.max_memory_allocated() / 1024**3
        encode_mp3(wav_path, out, tempo)
    finally:
        wav_path.unlink(missing_ok=True)
        del model
        gc.collect()
        torch.cuda.empty_cache()
    return {"load_seconds": round(load_seconds, 1), "peak_vram_gb": round(peak_vram_gb, 2)}


def _http_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def run_voxtral(text: str, out: Path, url: str, voice: str, chunk_chars: int) -> dict:
    """Synthesize through a vLLM-Omni server's OpenAI-style /v1/audio/speech route."""
    try:
        voices = _http_json(f"{url}/v1/audio/voices").get("voices", [])
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"Cannot reach the Voxtral server at {url}: {exc}") from exc
    if voices and voice not in voices:
        raise RuntimeError(
            f"Unknown Voxtral voice {voice!r}. The server offers: {', '.join(voices)}"
        )

    chunks = tts._split_text(text, chunk_chars)
    wav_path = out.with_suffix(".wav")
    try:
        writer = None
        for index, chunk in enumerate(chunks, 1):
            print(f"    chunk {index}/{len(chunks)}", flush=True)
            body = json.dumps(
                {
                    "model": VOXTRAL_MODEL,
                    "input": chunk,
                    "voice": voice,
                    "language": "English",
                    "response_format": "wav",
                }
            ).encode()
            request = urllib.request.Request(
                f"{url}/v1/audio/speech", body, {"Content-Type": "application/json"}
            )
            try:
                with urllib.request.urlopen(request, timeout=600) as resp:
                    data = resp.read()
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:500]
                raise RuntimeError(f"Voxtral server returned {exc.code}: {detail}") from exc
            with wave.open(io.BytesIO(data)) as piece:
                if writer is None:
                    writer = wave.open(str(wav_path), "wb")
                    writer.setparams(piece.getparams())
                    gap = b"\x00" * (
                        int(piece.getframerate() * CHUNK_GAP_SECONDS)
                        * piece.getsampwidth()
                        * piece.getnchannels()
                    )
                writer.writeframes(piece.readframes(piece.getnframes()))
                writer.writeframes(gap)
        if writer is None:
            raise RuntimeError("No readable text to synthesize")
        writer.close()
        encode_mp3(wav_path, out)
    finally:
        wav_path.unlink(missing_ok=True)
    return {}


def build_index(results: list[dict]) -> str:
    rows, players = [], []
    for r in results:
        label = html.escape(r["label"])
        if r.get("error"):
            rows.append(
                f"<tr><td>{label}</td><td colspan=4>failed: {html.escape(r['error'])}</td></tr>"
            )
            continue
        rows.append(
            f"<tr><td>{label}</td><td>{r['seconds']}</td><td>{r['audio_seconds']}</td>"
            f"<td>{r['rtf']}</td><td>{r.get('peak_vram_gb', '')}</td></tr>"
        )
        players.append(
            f'<h2>{label}</h2><audio controls preload=none src="{html.escape(r["file"])}"></audio>'
        )
    return (
        "<!doctype html><meta charset=utf-8><title>TTS comparison</title>"
        "<style>body{font:16px system-ui;max-width:48rem;margin:2rem auto;padding:0 1rem}"
        "td,th{padding:.25rem .75rem;text-align:left}audio{width:100%}</style>"
        "<h1>TTS comparison</h1><table>"
        "<tr><th>Engine<th>Seconds<th>Audio s<th>RTF<th>Peak VRAM GB</tr>"
        + "".join(rows)
        + "</table>"
        + "".join(players)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("article", help="Markdown or text file, or - for stdin")
    parser.add_argument(
        "--engines",
        default="kokoro,edge,chatterbox-turbo",
        help=f"comma list of {', '.join(ENGINES)}",
    )
    parser.add_argument("--out", type=Path, default=Path("tts-compare"))
    parser.add_argument("--max-chars", type=int, default=3000, help="sample length (default 3000)")
    parser.add_argument("--full", action="store_true", help="render the whole article")
    parser.add_argument("--kokoro-voice", action="append", help="repeatable (default af_heart)")
    parser.add_argument(
        "--edge-voice", action="append", help="repeatable (default en-US-AriaNeural)"
    )
    parser.add_argument(
        "--voxtral-url",
        default="http://localhost:8091",
        help="vLLM-Omni server running Voxtral (default http://localhost:8091)",
    )
    parser.add_argument(
        "--voxtral-voice", action="append", help="repeatable (default casual_female)"
    )
    parser.add_argument(
        "--voxtral-chunk-chars",
        type=int,
        default=500,
        help="text per Voxtral request (default 500, the length the model card benchmarks)",
    )
    parser.add_argument("--ref-audio", help="reference clip for the Chatterbox engines")
    parser.add_argument(
        "--tempo",
        type=float,
        default=1.0,
        help="Chatterbox playback speed after synthesis, 0.5-2.0 (0.9 = 10%% slower, same pitch)",
    )
    parser.add_argument(
        "--cfg-weight",
        type=float,
        help="original Chatterbox only (Turbo ignores it); upstream suggests ~0.3 for fast speech",
    )
    parser.add_argument(
        "--exaggeration",
        type=float,
        help="original Chatterbox only (Turbo ignores it); higher tends to speak faster",
    )
    parser.add_argument(
        "--chatterbox-chunk-chars",
        type=int,
        default=400,
        help="text per Chatterbox call (default 400, an untested starting point)",
    )
    args = parser.parse_args()

    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    unknown = [e for e in engines if e not in ENGINES]
    if unknown:
        parser.error(f"unknown engine(s): {', '.join(unknown)}")

    raw = sys.stdin.read() if args.article == "-" else Path(args.article).read_text()
    cleaned = tts._clean_markdown(raw)
    if not cleaned:
        parser.error("no readable text in the article")
    text = cleaned if args.full else truncate(cleaned, args.max_chars)
    print(f"Sample: {len(text)} characters, {tts.count_words(text)} words")

    args.out.mkdir(parents=True, exist_ok=True)
    results_path = args.out / "results.json"
    previous = json.loads(results_path.read_text()) if results_path.exists() else []
    results = {r["label"]: r for r in previous}

    jobs: list[tuple[str, callable]] = []
    if not 0.5 <= args.tempo <= 2.0:
        parser.error("--tempo must be between 0.5 and 2.0")
    ref_tag = "-cloned" if args.ref_audio else ""
    # Tuned runs get their own label so they sit beside the defaults, not over them.
    tempo_tag = f"-x{args.tempo:g}" if args.tempo != 1.0 else ""
    original_options = {}
    if args.cfg_weight is not None:
        original_options["cfg_weight"] = args.cfg_weight
    if args.exaggeration is not None:
        original_options["exaggeration"] = args.exaggeration
    cfg_tag = "".join(f"-{k.split('_')[0]}{v:g}" for k, v in original_options.items())
    for engine in engines:
        if engine == "kokoro":
            for voice in args.kokoro_voice or ["af_heart"]:
                jobs.append((f"kokoro-{voice}", lambda out, v=voice: run_kokoro(text, out, v)))
        elif engine == "edge":
            for voice in args.edge_voice or ["en-US-AriaNeural"]:
                jobs.append((f"edge-{voice}", lambda out, v=voice: run_edge(text, out, v)))
        elif engine == "voxtral":
            for voice in args.voxtral_voice or ["casual_female"]:
                jobs.append(
                    (
                        f"voxtral-{voice}",
                        lambda out, v=voice: run_voxtral(
                            text, out, args.voxtral_url.rstrip("/"), v, args.voxtral_chunk_chars
                        ),
                    )
                )
        else:
            turbo = engine == "chatterbox-turbo"
            options = {} if turbo else original_options
            label = engine + ref_tag + ("" if turbo else cfg_tag) + tempo_tag
            jobs.append(
                (
                    label,
                    lambda out, t=turbo, o=options: run_chatterbox(
                        text, out, t, args.ref_audio, args.chatterbox_chunk_chars, args.tempo, o
                    ),
                )
            )

    for label, run in jobs:
        print(f"{label} ...", flush=True)
        out = args.out / f"{label}.mp3"
        out.unlink(missing_ok=True)
        started = time.perf_counter()
        try:
            extra = run(out)
            seconds = time.perf_counter() - started
            audio_seconds = tts.audio_duration(out) or 0.0
            results[label] = {
                "label": label,
                "file": out.name,
                "seconds": round(seconds, 1),
                "audio_seconds": round(audio_seconds, 1),
                "rtf": round(seconds / audio_seconds, 3) if audio_seconds else None,
                **extra,
            }
            print(f"  done: {seconds:.1f}s for {audio_seconds:.1f}s of audio")
        except Exception as exc:  # one engine failing must not stop the others
            results[label] = {"label": label, "error": f"{type(exc).__name__}: {exc}"}
            print(f"  FAILED: {type(exc).__name__}: {exc}")
            traceback.print_exc()

    ordered = list(results.values())
    results_path.write_text(json.dumps(ordered, indent=2))
    (args.out / "index.html").write_text(build_index(ordered))
    print(
        f"\nOpen {args.out / 'index.html'} to listen. "
        "rtf = render seconds per second of audio (lower is faster)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

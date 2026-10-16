"""Preparing synthesized speech chunks so they join without a seam.

Pure Python on 16-bit mono PCM (``array`` and ``math`` only) so the app image
needs no numerical library for it.
"""

import array
import math
import sys

WINDOW_SECONDS = 0.05
# A window counts as speech when it is within this many dB of the loud ones.
SPEECH_RANGE_DB = 25
# Below this the audio is treated as silence outright, however quiet the rest
# of the chunk is. Voxtral's raw speech sits around -37 dBFS.
SILENCE_DB = -60
TRIM_MARGIN_SECONDS = 0.05
EDGE_FADE_SECONDS = 0.01
# Gain is applied in steps this long; a straight-line ramp over many seconds
# moves a fraction of a dB per step, far below what the ear can follow.
GAIN_STEP_SECONDS = 0.01
PEAK_CEILING = 10 ** (-0.5 / 20)
FULL_SCALE = 32768


def rms_db(samples) -> float:
    """RMS level of 16-bit samples in dBFS; -inf for nothing."""
    energy = sum(x * x for x in samples) if len(samples) else 0
    if not energy:
        return -math.inf
    return 20 * math.log10(math.sqrt(energy / len(samples)) / FULL_SCALE)


def _window_db(samples, window: int) -> list[float]:
    return [rms_db(samples[i : i + window]) for i in range(0, len(samples), window)]


def _mean_db(values: list[float]) -> float:
    """Average level of several windows, by power."""
    if not values:
        return -math.inf
    return 10 * math.log10(sum(10 ** (v / 10) for v in values) / len(values))


def _from_bytes(frames: bytes) -> array.array:
    samples = array.array("h")
    samples.frombytes(frames)
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def _to_bytes(samples: array.array) -> bytes:
    if sys.byteorder == "big":
        samples.byteswap()
    return samples.tobytes()


def condition_chunk(frames: bytes, rate: int, target_db: float | None) -> tuple[bytes, dict]:
    """Prepare one 16-bit mono chunk so it joins its neighbours without a seam.

    - Trims the silence the model leaves around the speech, so the pause
      between chunks is the one the caller inserts and not a random extra.
    - Fades the cut edges over 10 ms, so they cannot click.
    - With ``target_db``, moves the gain along a straight line in dB so the
      start and the end of the speech both land on the target. That evens out
      the fade-out each request has and makes the end of one chunk match the
      start of the next, where one constant gain per chunk would step at every
      border. The gain is capped so the peak stays below -0.5 dBFS.

    Returns the new frames and a dict of what was measured; the dict is empty
    when the chunk holds no speech, in which case the frames come back as given.
    """
    samples = _from_bytes(frames)
    window = max(1, int(rate * WINDOW_SECONDS))
    levels = _window_db(samples, window)
    if not levels:
        return frames, {}
    loud = sorted(levels)[int(0.9 * (len(levels) - 1))]
    if loud < SILENCE_DB:
        return frames, {}
    gate = max(loud - SPEECH_RANGE_DB, SILENCE_DB)
    speech = [i for i, v in enumerate(levels) if v >= gate]

    margin = int(rate * TRIM_MARGIN_SECONDS)
    lo = max(0, speech[0] * window - margin)
    hi = min(len(samples), (speech[-1] + 1) * window + margin)
    kept = samples[lo:hi]
    n = len(kept)

    kept_levels = _window_db(kept, window)
    voiced = [i for i, v in enumerate(kept_levels) if v >= gate]
    head = [kept_levels[i] for i in voiced if i < len(kept_levels) * 0.25]
    tail = [kept_levels[i] for i in voiced if i >= len(kept_levels) * 0.75]
    overall = _mean_db([kept_levels[i] for i in voiced])
    start_db = _mean_db(head) if head else overall
    end_db = _mean_db(tail) if tail else overall

    step = max(1, int(rate * GAIN_STEP_SECONDS))
    blocks = range(0, n, step)
    gain_start_db = gain_end_db = 0.0
    gains = [1.0] * len(blocks)
    if target_db is not None:
        gain_start_db, gain_end_db = target_db - start_db, target_db - end_db
        slope = (gain_end_db - gain_start_db) / 0.75
        gains = [
            10 ** ((gain_start_db + slope * ((b + step / 2) / n - 0.125)) / 20) for b in blocks
        ]

    loudest = max(
        (max(map(abs, kept[b : b + step])) * g for b, g in zip(blocks, gains, strict=True)),
        default=0,
    )
    limit = min(1.0, FULL_SCALE * PEAK_CEILING / loudest) if loudest else 1.0

    fade = min(int(rate * EDGE_FADE_SECONDS), n // 2)
    out = array.array("h")
    for b, g in zip(blocks, gains, strict=True):
        scale = g * limit
        out.extend(
            max(-FULL_SCALE, min(FULL_SCALE - 1, round(x * scale))) for x in kept[b : b + step]
        )
    for k in range(fade):
        ramp = k / fade
        out[k] = round(out[k] * ramp)
        out[n - 1 - k] = round(out[n - 1 - k] * ramp)

    peak = max(map(abs, out), default=0) or 1
    return _to_bytes(out), {
        "lead": lo / rate,
        "tail": (len(samples) - hi) / rate,
        "start_db": start_db,
        "end_db": end_db,
        "gain_start_db": gain_start_db,
        "gain_end_db": gain_end_db,
        "peak_db": 20 * math.log10(peak / FULL_SCALE),
    }

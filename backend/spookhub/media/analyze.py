"""Onset and tempo analysis on a mono PCM stream (pure Python, no numpy).

Pipeline: RMS envelope at 100 Hz -> positive diff of log-energy (onset strength) -> adaptive
threshold (moving median + k*MAD) peak picking with minimum spacing -> optional BPM from the
autocorrelation of the onset strength.
"""

from __future__ import annotations

import array
import math
from operator import mul
from typing import Any

ENV_RATE = 100  # envelope frames per second
MIN_SPACING = 0.08  # seconds between onsets
MEDIAN_HALF = 50  # frames each side of the moving-median window (~1 s window)
MAD_K = 3.0
# Global threshold floor (fraction of the loudest onset in the clip). It keeps noise out of
# silent passages, but also means a very quiet hit next to a much louder one (< 10 % of its
# strength) is not reported.
FLOOR_FRAC = 0.1
MIN_LAG_S, MAX_LAG_S = 0.3, 1.5
BPM_MIN_CONFIDENCE = 0.15


def envelope(raw: bytes, sample_rate: int) -> list[float]:
    """RMS per 1/ENV_RATE s frame, in 16-bit sample units."""
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - len(raw) % 2])
    size = max(1, sample_rate // ENV_RATE)
    out = []
    for i in range(0, len(samples) - size + 1, size):
        chunk = samples[i : i + size]
        out.append(math.sqrt(sum(map(mul, chunk, chunk)) / size))
    return out


def onset_strength(env: list[float]) -> list[float]:
    logs = [0.0] + [math.log1p(e) for e in env]  # leading silent frame: onset at t=0 counts
    return [max(0.0, logs[i] - logs[i - 1]) for i in range(1, len(logs))]


def _median(vals: list[float]) -> float:
    v = sorted(vals)
    return v[len(v) // 2]


def pick_peaks(strength: list[float]) -> list[tuple[int, float]]:
    top = max(strength, default=0.0)
    if top <= 0:
        return []
    n = len(strength)
    min_gap = max(1, round(MIN_SPACING * ENV_RATE))
    picked: list[tuple[int, float]] = []
    for i, s in enumerate(strength):
        if s <= 0 or (i and s < strength[i - 1]) or (i + 1 < n and s <= strength[i + 1]):
            continue  # not a local maximum (plateaus resolve to their last frame)
        win = strength[max(0, i - MEDIAN_HALF) : i + MEDIAN_HALF + 1]
        med = _median(win)
        mad = _median([abs(x - med) for x in win])
        if s <= max(med + MAD_K * mad, FLOOR_FRAC * top):
            continue
        if picked and i - picked[-1][0] < min_gap:
            if s > picked[-1][1]:
                picked[-1] = (i, s)
            continue
        picked.append((i, s))
    return picked


def estimate_bpm(strength: list[float]) -> float | None:
    lo, hi = round(MIN_LAG_S * ENV_RATE), round(MAX_LAG_S * ENV_RATE)
    if len(strength) < hi * 3:
        return None
    mean = sum(strength) / len(strength)
    x = [s - mean for s in strength]
    zero = sum(map(mul, x, x))
    if zero <= 0:
        return None
    n = len(x)
    ac = {lag: sum(map(mul, x, x[lag:])) * n / (n - lag) for lag in range(lo - 1, hi + 2)}
    best = lo
    for lag in range(lo + 1, hi + 1):
        if ac[lag] > ac[best] * 1.05:  # prefer the shorter lag over near-ties (sub-harmonics)
            best = lag
    if ac[best] / zero < BPM_MIN_CONFIDENCE:
        return None
    a, b, c = ac[best - 1], ac[best], ac[best + 1]
    denom = a - 2 * b + c
    shift = 0.5 * (a - c) / denom if denom < 0 else 0.0
    return round(60.0 * ENV_RATE / (best + max(-1.0, min(1.0, shift))), 1)


def analyze(raw: bytes, sample_rate: int) -> dict[str, Any]:
    strength = onset_strength(envelope(raw, sample_rate))
    peaks = pick_peaks(strength)
    top = max((s for _, s in peaks), default=1.0) or 1.0
    onsets = [{"t": round(i / ENV_RATE, 3), "strength": round(s / top, 3)} for i, s in peaks]
    return {"rate": ENV_RATE, "onsets": onsets, "bpm": estimate_bpm(strength)}

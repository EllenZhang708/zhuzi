"""Voice activity detection with Silero VAD v6, run on onnxruntime.

Adapted from faster-whisper's vad.py (MIT, SYSTRAN), itself adapted from
https://github.com/snakers4/silero-vad (MIT). Kept free of faster-whisper so
the app needs no CTranslate2, which has no Windows ARM64 build.
"""
from __future__ import annotations

import bisect
import functools
import sys
from pathlib import Path

import numpy as np

SR = 16000
WINDOW = 512  # samples per VAD frame at 16 kHz
_ASSETS = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "assets"


@functools.lru_cache
def _session():
    import onnxruntime

    opts = onnxruntime.SessionOptions()
    opts.inter_op_num_threads = 1
    opts.intra_op_num_threads = 1  # one core per call, so chunks can run in parallel threads
    opts.enable_cpu_mem_arena = False
    opts.log_severity_level = 4
    return onnxruntime.InferenceSession(
        str(_ASSETS / "silero_vad_v6.onnx"), providers=["CPUExecutionProvider"], sess_options=opts
    )


def _probabilities(audio: np.ndarray, context: int = 64) -> np.ndarray:
    """Speech probability for every 512-sample frame, computed in large batches."""
    frames = audio.reshape(-1, WINDOW)
    ctx = frames[..., -context:].copy()
    ctx[-1] = 0
    ctx = np.roll(ctx, 1, 0)
    batched = np.concatenate([ctx, frames], 1)
    h = np.zeros((1, 1, 128), dtype=np.float32)
    c = np.zeros((1, 1, 128), dtype=np.float32)
    out = []
    for i in range(0, len(batched), 10000):
        probs, h, c = _session().run(None, {"input": batched[i : i + 10000], "h": h, "c": c})
        out.append(probs)
    return np.concatenate(out, axis=0).reshape(-1)


def speech_spans(
    audio: np.ndarray,
    threshold: float = 0.5,
    min_silence_ms: int = 2000,
    speech_pad_ms: int = 400,
    max_speech_s: float = float("inf"),
) -> list[tuple[int, int]]:
    """(start, end) sample ranges that contain speech."""
    neg_threshold = max(threshold - 0.15, 0.01)
    pad = SR * speech_pad_ms / 1000
    max_speech = SR * max_speech_s - WINDOW - 2 * pad
    min_silence = SR * min_silence_ms / 1000
    min_silence_at_max = SR * 98 / 1000
    n = len(audio)
    probs = _probabilities(np.pad(audio, (0, WINDOW - n % WINDOW)).astype(np.float32))

    speeches: list[dict] = []
    cur: dict = {}
    triggered = False
    temp_end = prev_end = next_start = 0
    for i, p in enumerate(probs):
        pos = WINDOW * i
        if p >= threshold and temp_end:
            temp_end = 0
            if next_start < prev_end:
                next_start = pos
        if p >= threshold and not triggered:
            triggered = True
            cur["start"] = pos
            continue
        if triggered and pos - cur["start"] > max_speech:
            if prev_end:
                cur["end"] = prev_end
                speeches.append(cur)
                cur = {}
                if next_start < prev_end:
                    triggered = False
                else:
                    cur["start"] = next_start
                prev_end = next_start = temp_end = 0
            else:
                cur["end"] = pos
                speeches.append(cur)
                cur = {}
                prev_end = next_start = temp_end = 0
                triggered = False
                continue
        if p < neg_threshold and triggered:
            if not temp_end:
                temp_end = pos
            if pos - temp_end > min_silence_at_max:
                prev_end = temp_end
            if pos - temp_end < min_silence:
                continue
            cur["end"] = temp_end
            speeches.append(cur)
            cur = {}
            prev_end = next_start = temp_end = 0
            triggered = False
    if cur:
        cur["end"] = n
        speeches.append(cur)

    # Pad each span, splitting short gaps between neighbours.
    for i, s in enumerate(speeches):
        if i == 0:
            s["start"] = int(max(0, s["start"] - pad))
        if i != len(speeches) - 1:
            gap = speeches[i + 1]["start"] - s["end"]
            if gap < 2 * pad:
                s["end"] += int(gap // 2)
                speeches[i + 1]["start"] = int(max(0, speeches[i + 1]["start"] - gap // 2))
            else:
                s["end"] = int(min(n, s["end"] + pad))
                speeches[i + 1]["start"] = int(max(0, speeches[i + 1]["start"] - pad))
        else:
            s["end"] = int(min(n, s["end"] + pad))
    return [(s["start"], s["end"]) for s in speeches]


class TimeMap:
    """Maps a time in speech-only audio (spans glued together) back to the original."""

    def __init__(self, spans: list[tuple[int, int]]):
        self.ends: list[int] = []  # each span's end, in glued samples
        self.silence_before: list[float] = []  # seconds removed before each span
        removed, prev = 0, 0
        for start, end in spans:
            removed += start - prev
            prev = end
            self.ends.append(end - removed)
            self.silence_before.append(removed / SR)

    def original(self, t: float, is_end: bool = False) -> float:
        sample = int(t * SR)
        if is_end and sample in self.ends:
            i = self.ends.index(sample)
        else:
            i = min(bisect.bisect(self.ends, sample), len(self.ends) - 1)
        return round(self.silence_before[i] + t, 2)


def speech_only(audio: np.ndarray) -> tuple[np.ndarray, TimeMap | None]:
    """Keep only the stretches with a voice in them, plus the map back to the
    original times; (empty, None) if there is no speech at all."""
    spans = speech_spans(audio)
    if not spans:
        return np.zeros(0, dtype=np.float32), None
    return np.concatenate([audio[s:e] for s, e in spans]).astype(np.float32), TimeMap(spans)


def pieces(audio: np.ndarray, max_len: float = 28.0, max_gap: float = 1.0) -> list[tuple[int, int, bool]]:
    """Speech grouped into pieces of at most `max_len` seconds for models that
    take one short clip at a time (SenseVoice). Returns (start, end, capped):
    capped means the piece was cut only for length, so it likely ends mid-sentence."""
    out: list[list] = []
    # The full 400 ms pad: with less, the first syllable after a pause gets clipped ("Hello" -> "lo").
    for start, end in speech_spans(audio, min_silence_ms=300, speech_pad_ms=400, max_speech_s=max_len):
        if out:
            p_start, p_end, _ = out[-1]
            if (start - p_end) / SR <= max_gap:
                if (end - p_start) / SR <= max_len:
                    out[-1][1] = end
                    continue
                out[-1][2] = True
        out.append([start, end, False])
    return [tuple(p) for p in out]

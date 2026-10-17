"""Decoding, chunking and compressing audio."""
from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

import vad

SR = vad.SR


class AudioError(Exception):
    pass


def decode(path: Path) -> np.ndarray:
    """Decode to 16 kHz mono float32. A file object sidesteps FFmpeg's trouble
    with non-ASCII paths."""
    import av

    chunks = []
    try:
        with open(path, "rb") as f, av.open(f) as container:
            resampler = av.AudioResampler(format="flt", layout="mono", rate=SR)
            for frame in container.decode(audio=0):
                for out in resampler.resample(frame):
                    chunks.append(out.to_ndarray().reshape(-1))
            for out in resampler.resample(None):
                chunks.append(out.to_ndarray().reshape(-1))
    except Exception as e:
        raise AudioError("无法读取这个音频文件，它可能已损坏。") from e
    if not chunks:
        raise AudioError("这个音频文件里没有声音。")
    return np.concatenate(chunks).astype(np.float32)


def probe_duration(path: Path) -> float | None:
    import av

    try:
        with open(path, "rb") as f, av.open(f) as container:
            if container.duration:
                return container.duration / 1_000_000
    except Exception:  # reported later, when decoding
        return None
    return None


def cut_points(audio: np.ndarray, target: float = 600, search: float = 15.0) -> list[int]:
    """Chunk boundaries near every `target` seconds, moved to the quietest
    0.4 s within ±`search` seconds so no word is split."""
    win = int(0.4 * SR)
    bounds = [0]
    t = target
    while t * SR < len(audio) - 30 * SR:  # no tiny last chunk
        lo, hi = int((t - search) * SR), int((t + search) * SR)
        region = audio[lo:hi]
        frames = region[: len(region) // win * win].reshape(-1, win)
        quietest = int(np.argmin((frames**2).mean(axis=1)))
        bounds.append(lo + quietest * win + win // 2)
        t = bounds[-1] / SR + target
    bounds.append(len(audio))
    return bounds


def chunks(audio: np.ndarray, target: float = 600) -> list[tuple[int, np.ndarray]]:
    """(start sample, samples) for each ~`target`-second chunk."""
    b = cut_points(audio, target)
    return [(b[i], audio[b[i] : b[i + 1]]) for i in range(len(b) - 1)]


def speech_pieces(audio: np.ndarray, max_len: float = 28.0, workers: int = 8) -> list[tuple[int, int, bool]]:
    """Short speech pieces over the whole file, for clip-at-a-time models.
    The VAD runs on each 10-minute chunk in parallel; it is single-threaded."""
    parts = chunks(audio)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        found = list(pool.map(lambda p: vad.pieces(p[1], max_len=max_len), parts))
    return [(start + s, start + e, capped) for (start, _), ps in zip(parts, found) for s, e, capped in ps]


def to_opus(samples: np.ndarray, bitrate: int = 32000) -> bytes:
    import av

    buf = io.BytesIO()
    with av.open(buf, mode="w", format="ogg") as out:
        stream = out.add_stream("libopus", rate=SR, layout="mono")
        stream.bit_rate = bitrate
        for i in range(0, len(samples), SR):  # one second per frame; the encoder re-blocks
            frame = av.AudioFrame.from_ndarray(samples[i : i + SR].reshape(1, -1), format="flt", layout="mono")
            frame.sample_rate = SR
            frame.pts = i
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return buf.getvalue()

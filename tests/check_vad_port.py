"""Compare our Silero VAD port with faster-whisper's on a real episode.

    <python with faster-whisper> tests\\check_vad_port.py AUDIO   -> compares both
    <any Zhuzi venv>             tests\\check_vad_port.py AUDIO   -> times ours only
"""
import sys
import time
from pathlib import Path

import av
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import vad  # noqa: E402


def decode(path: Path) -> np.ndarray:
    out = []
    with open(path, "rb") as f, av.open(f) as c:
        rs = av.AudioResampler(format="flt", layout="mono", rate=16000)
        for frame in c.decode(audio=0):
            out += [x.to_ndarray().reshape(-1) for x in rs.resample(frame)]
    return np.concatenate(out).astype(np.float32)


audio = decode(Path(sys.argv[1]))
t = time.perf_counter()
ours = vad.speech_spans(audio)
print(f"ours: {len(ours)} spans in {time.perf_counter() - t:.1f}s, first {[(s / 16000, e / 16000) for s, e in ours[:2]]}")
try:
    from faster_whisper.vad import VadOptions, get_speech_timestamps
except ImportError:
    sys.exit(0)
theirs = [(s["start"], s["end"]) for s in get_speech_timestamps(audio, VadOptions())]
print(f"faster-whisper: {len(theirs)} spans; identical: {ours == theirs}")

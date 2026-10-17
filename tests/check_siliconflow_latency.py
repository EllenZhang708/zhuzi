"""How long does each SiliconFlow request take, at different concurrency?

    .venv-arm64\\Scripts\\python.exe tests\\check_siliconflow_latency.py SETTINGS_JSON AUDIO [workers...]
"""
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import audio  # noqa: E402
import cloud  # noqa: E402
import core  # noqa: E402

settings = json.loads(Path(sys.argv[1]).read_text("utf-8"))
key = core.get_key(settings, "siliconflow")
samples = audio.decode(Path(sys.argv[2]))[: 600 * audio.SR]  # first 10 minutes
pieces = audio.speech_pieces(samples)
payloads = [audio.to_opus(samples[s:e]) for s, e, _ in pieces]
print(f"{len(pieces)} pieces, {sum(len(p) for p in payloads) / 1e6:.1f} MB total")

for workers in [int(w) for w in sys.argv[3:]] or [8, 4, 2]:
    times = []

    def one(i):
        t = time.perf_counter()
        cloud._post_audio(cloud.SILICONFLOW, key, [("model", cloud.SILICONFLOW.model)], f"p{i}.ogg", payloads[i], threading.Event())
        times.append(time.perf_counter() - t)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, range(len(payloads))))
    wall = time.perf_counter() - t0
    times.sort()
    print(f"workers={workers}: wall {wall:.1f}s | per request min {times[0]:.1f}s, median {times[len(times) // 2]:.1f}s, max {times[-1]:.1f}s")

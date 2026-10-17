"""End-to-end run of the pipeline without the UI.

    .venv-arm64\\Scripts\\python.exe tests\\smoke.py <xiaoyuzhou link or audio file> [clip seconds] [cloud|local]
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import core  # noqa: E402


class PrintEvents:
    def __init__(self):
        self.last = 0.0
        self.count = 0

    def stage(self, index):
        print(f"== stage {index + 1}", flush=True)

    def progress(self, fraction, detail):
        if time.monotonic() - self.last > 3 or fraction in (0.0, 1.0):
            print(f"   {'  …' if fraction is None else f'{fraction:4.0%}'}  {detail}", flush=True)
            self.last = time.monotonic()

    def segment(self, seg):
        self.count += 1
        if self.count <= 4:
            print(f"   [{core.fmt_ts(seg.start)}] {seg.text[:60]}", flush=True)


def main():
    source = sys.argv[1]
    clip = float(sys.argv[2]) if len(sys.argv) > 2 else 120
    engine = sys.argv[3] if len(sys.argv) > 3 else core.CLOUD
    ep = core.fetch_episode(source) if core.find_episode_id(source) else core.local_episode(Path(source))
    out = Path(__file__).resolve().parent / "out"
    t0 = time.monotonic()
    result = core.run_job(ep, engine, out, PrintEvents(), threading.Event(), clip_seconds=clip or None)
    print(f"wrote {result.path.name} ({result.segments} segments, {result.engine}) in {time.monotonic() - t0:.0f}s")


if __name__ == "__main__":
    main()

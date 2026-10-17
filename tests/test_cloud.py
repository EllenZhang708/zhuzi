"""Offline checks for audio handling and the cloud filters (no network).
Run: .venv-arm64\\Scripts\\python.exe tests\\test_cloud.py"""
import io
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import audio  # noqa: E402
import cloud  # noqa: E402
import core  # noqa: E402
import secrets_store  # noqa: E402
import vad  # noqa: E402

SR = audio.SR

# Invented lines over music are dropped; real speech, even slow, is kept.
assert cloud.looks_invented("请不吝点赞，订阅，转发，打赏支持明镜与点点栏目", 30)
assert cloud.looks_invented("作词曲，李宗盛", 30)
assert cloud.looks_invented("嗯", 12)
assert not cloud.looks_invented("Hello 大家好，我是孟岩。欢迎来到新一期的《无人知晓》。", 9)
assert not cloud.looks_invented("欢迎大家点赞订阅转发我们的节目", 4)  # a host may really say this

# Cuts land in the quiet gap nearest each 10-minute mark.
rng = np.random.default_rng(0)
noise = (rng.standard_normal(SR * 1500) * 0.3).astype(np.float32)
gap = int(605 * SR)
noise[gap : gap + SR] = 0.0
bounds = audio.cut_points(noise)
assert bounds[0] == 0 and bounds[-1] == len(noise) and gap <= bounds[1] <= gap + SR, [b / SR for b in bounds]

# Opus keeps the length at a small fraction of the size.
import av  # noqa: E402

tone = (0.2 * np.sin(2 * np.pi * 220 * np.arange(SR * 30) / SR)).astype(np.float32)
data = audio.to_opus(tone)
assert len(data) < 200_000, len(data)
with av.open(io.BytesIO(data)) as c:
    assert abs(c.duration / 1_000_000 - 30) < 0.5

# The VAD time map: 2 s of speech, 3 s cut, then more speech at 5 s.
tm = vad.TimeMap([(0, 2 * SR), (5 * SR, 7 * SR)])
assert tm.original(1.0) == 1.0 and tm.original(2.5) == 5.5, (tm.original(1.0), tm.original(2.5))

# Keys round-trip through DPAPI; unreadable values come back empty.
s: dict = {}
core.set_key(s, "groq", "gsk_example")
assert s["keys"]["groq"].startswith("dpapi:") and core.get_key(s, "groq") == "gsk_example"
assert secrets_store.unprotect("dpapi:AAAA") == ""

# Providers that have a key, preferred first.
core.set_key(s, "siliconflow", "sk-example")
s["provider"] = "groq"
assert [p.key for p in cloud.order(s)] == ["groq", "siliconflow"]
core.set_key(s, "groq", "")
assert [p.key for p in cloud.order(s)] == ["siliconflow"]

# Homophones of the show's names are corrected (same tones only).
fix = core.name_fixer(core.Episode(title="E46", source="", podcast="无人知晓", author="孟岩"))
assert fix("大家好，我是梦言，这期播课") == "大家好，我是孟岩，这期播客", fix("大家好，我是梦言，这期播课")
assert fix("我在写博客") == "我在写博客"  # different tone, left alone
assert core.tidy("<|zh|><|NEUTRAL|>你好🎼") == "你好"

print("cloud checks passed")

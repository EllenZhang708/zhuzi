"""Unit checks for transcript text handling. Run: .venv\\Scripts\\python.exe tests\\test_text.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core import Paragraphs, Segment, find_episode_id, paragraph_text, safe_filename, tidy  # noqa: E402

# Half-width marks next to Chinese become full-width; English keeps its own.
assert tidy("Hello 大家好,我是孟岩。") == "Hello 大家好，我是孟岩。", tidy("Hello 大家好,我是孟岩。")
assert tidy("对吧?") == "对吧？"
assert tidy("Rick Rubin说的,对吧?") == "Rick Rubin说的，对吧？"
assert tidy("version 1.5, really") == "version 1.5, really"
# A space between Chinese characters is a pause.
assert tidy("大家好 我是孟岩") == "大家好，我是孟岩"
# Traditional characters are converted.
assert tidy("這是一個測試") == "这是一个测试"

# Segments join with commas and close with a full stop.
group = [Segment(0, 2, "好久没见了"), Segment(2, 4, "但其实我一直在创作，")]
assert paragraph_text(group) == "好久没见了，但其实我一直在创作。", paragraph_text(group)

# A long pause opens a paragraph; a long paragraph only breaks after a sentence end.
p = Paragraphs(max_gap=1.5, max_span=10)
assert p.add(Segment(0, 4, "一"))
assert not p.add(Segment(4.5, 8, "二，"))
assert not p.add(Segment(8, 12, "三，"))  # over 10s, but mid-sentence
assert not p.add(Segment(12, 14, "四。"))
assert p.add(Segment(14, 16, "五"))  # over 10s and the last one ended a sentence
assert p.add(Segment(20, 22, "六"))  # pause of 4s

assert find_episode_id("【E46】https://www.xiaoyuzhoufm.com/episode/6a8bfde1ef65145dfcc47d26?s=abc") == "6a8bfde1ef65145dfcc47d26"
assert find_episode_id("https://example.com") is None
assert safe_filename('a/b:c*"d"?') == "a b c d"

print("text checks passed")

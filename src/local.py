"""Offline transcription with SenseVoice-Small on sherpa-onnx.

Runs natively on x64 and ARM64. The model (~230 MB, int8) is downloaded on
first use. Speech is cut into pieces of up to 28 s and decoded in batches.
"""
from __future__ import annotations

import os
import threading
from typing import Iterator

import numpy as np

import audio
import core

SR = audio.SR
REPO = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"  # the official SenseVoiceSmall export
FILES = ["model.int8.onnx", "tokens.txt"]
FOLDER = core.MODELS_DIR / "sense-voice"
BATCH = 16
LABEL = "SenseVoice-Small（本机）"


def ready() -> bool:
    return (FOLDER / ".complete").exists()


def _recognizer():
    import sherpa_onnx

    return sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(FOLDER / "model.int8.onnx"),
        tokens=str(FOLDER / "tokens.txt"),
        num_threads=os.cpu_count() or 4,
        language="zh",
        use_itn=True,  # punctuation and numbers
        debug=False,
    )


def transcribe(samples: np.ndarray, ep: core.Episode, events: core.Events, cancel: threading.Event):
    """(label(), segments)."""
    first = not ready()
    events.progress(0.0 if first else None, "首次使用，正在下载识别模型…" if first else "正在载入识别模型…")
    core.fetch_model(
        REPO,
        FILES,
        FOLDER,
        lambda done, total: events.progress(
            done / total if total else None, f"首次使用，正在下载识别模型 · {done / 1_048_576:,.0f} / {(total or 0) / 1_048_576:,.0f} MB"
        ),
        cancel,
    )
    events.progress(None, "正在载入识别模型…")
    try:
        recognizer = _recognizer()
    except Exception as e:
        raise core.VerbatimError(f"识别模型载入失败，可尝试删除 {FOLDER} 后重试。") from e

    def segments() -> Iterator[core.Segment]:
        events.stage(2)
        events.progress(None, "正在按停顿切分音频…")
        found = audio.speech_pieces(samples)
        fix = core.name_fixer(ep)
        total = samples.shape[0] / SR
        for i in range(0, len(found), BATCH):
            if cancel.is_set():
                raise core.Cancelled()
            batch = found[i : i + BATCH]
            streams = []
            for start, end, _capped in batch:
                stream = recognizer.create_stream()
                stream.accept_waveform(SR, samples[start:end])
                streams.append(stream)
            recognizer.decode_streams(streams)
            for (start, end, capped), stream in zip(batch, streams):
                text = fix(core.tidy(stream.result.text))
                if capped and text.endswith("。"):  # cut for length, so probably mid-sentence
                    text = text[:-1] + "，"
                if text:
                    yield core.Segment(start / SR, end / SR, text)
            done = batch[-1][1] / SR
            events.progress(min(done / total, 1.0), f"已识别 {core.fmt_ts(done)} / {core.fmt_ts(total)}")

    return (lambda: LABEL), segments()

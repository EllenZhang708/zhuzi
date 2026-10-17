"""Cloud transcription through free speech APIs: SiliconFlow (SenseVoice,
reachable in mainland China) and Groq (Whisper large-v3-turbo, blocked there).

Each provider gets the audio its own way, both in parallel:
  Groq        ~10-minute chunks, music cut out first (Whisper invents text over
              music), returns timed segments.
  SiliconFlow speech pieces of up to 28 s (it returns text only, so each
              piece's own span serves as its timestamp).
If the preferred provider cannot be reached and the other has a key, the job
moves over to it before anything has been transcribed.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Callable, Iterator

import numpy as np

import audio
import core
import vad

log = logging.getLogger("zhuzi")
SR = audio.SR
MAX_WAIT = 90  # seconds of rate-limit back-off worth waiting out
# Cloudflare in front of Groq rejects Python-urllib's default User-Agent (error 1010).
USER_AGENT = f"Zhuzi/{core.APP_VERSION}"


@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    api: str
    model: str
    key_prefix: str
    key_page: str
    signup_hint: str
    region_note: str
    workers: int  # parallel requests
    timeout: float  # seconds before a request is abandoned and sent again


SILICONFLOW = Provider(
    "siliconflow",
    "硅基流动",
    "https://api.siliconflow.cn/v1",
    "FunAudioLLM/SenseVoiceSmall",
    "sk-",
    "https://cloud.siliconflow.cn/account/ak",
    "用手机号注册并完成实名认证，然后在「API 密钥」页面新建一个密钥",
    "国内可直接使用",
    # Pieces are small and cheap to resend. After a quiet spell the service warms
    # up slowly: early requests can stall for most of a minute, a retry is quick.
    workers=16,
    timeout=30,
)
GROQ = Provider(
    "groq",
    "Groq",
    "https://api.groq.com/openai/v1",
    "whisper-large-v3-turbo",
    "gsk_",
    "https://console.groq.com/keys",
    "免费注册，不需要信用卡，在 API Keys 页面点 Create API Key",
    "中国大陆需要通过代理",
    workers=8,  # ~10-minute chunks; the free tier allows 20 requests a minute
    timeout=180,
)
PROVIDERS = {p.key: p for p in (SILICONFLOW, GROQ)}


class ProviderError(core.VerbatimError):
    def __init__(self, message: str, reachable: bool = True):
        super().__init__(message)
        self.reachable = reachable  # False: worth trying the other provider


# ── HTTP ───────────────────────────────────────────────────────────────


def _request(p: Provider, path: str, key: str, data: bytes | None = None, content_type: str | None = None,
             timeout: float = 30):
    headers = {"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT}
    if content_type:
        headers["Content-Type"] = content_type
    return urllib.request.urlopen(urllib.request.Request(f"{p.api}{path}", data=data, headers=headers), timeout=timeout)


def _explain(p: Provider, e: urllib.error.HTTPError, body: str) -> ProviderError:
    if e.code == 401:
        return ProviderError(f"{p.name} 密钥无效，请重新设置。")
    if e.code == 403:
        return ProviderError(f"{p.name} 拒绝了连接（HTTP 403）。{p.region_note}。", reachable=False)
    if e.code == 429:
        return ProviderError(f"{p.name} 的免费额度暂时用完了，请稍后再试，或改用本机离线识别。")
    return ProviderError(f"{p.name} 返回了错误（HTTP {e.code}）：{body[:120]}")


def reachable(p: Provider, timeout: float = 6) -> bool:
    """Whether the provider answers from here at all (401 without a key counts)."""
    try:
        _request(p, "/models", "none", timeout=timeout).close()
        return True
    except urllib.error.HTTPError as e:
        return e.code == 401
    except OSError:
        return False


def check_key(p: Provider, key: str) -> None:
    """Raise ProviderError unless the key works and can use the model."""
    key = key.strip()
    if not key.startswith(p.key_prefix):
        raise ProviderError(f"{p.name} 的密钥以 {p.key_prefix} 开头，请检查是否复制完整。")
    try:
        with _request(p, "/models", key, timeout=20) as resp:
            models = [m["id"] for m in json.load(resp).get("data", [])]
    except urllib.error.HTTPError as e:
        raise _explain(p, e, e.read().decode("utf-8", "replace")) from e
    except OSError as e:
        raise ProviderError(f"无法连接 {p.name}，请检查网络。", reachable=False) from e
    if models and p.model not in models:
        log.warning("%s does not list %s among %d models", p.name, p.model, len(models))


def _multipart(fields: list[tuple[str, str]], filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    body = bytearray()
    for name, value in fields:
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    body += (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: audio/ogg\r\n\r\n"
    ).encode()
    body += data + f"\r\n--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def _post_audio(p: Provider, key: str, fields: list[tuple[str, str]], name: str, data: bytes,
                cancel: threading.Event) -> dict:
    body, content_type = _multipart(fields, name, data)
    for attempt in range(1, 6):
        if cancel.is_set():
            raise core.Cancelled()
        try:
            with _request(p, "/audio/transcriptions", key, body, content_type, timeout=p.timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            retry_after = float(e.headers.get("retry-after") or 2 * attempt)
            if e.code in (429, 500, 502, 503) and attempt < 5 and retry_after <= MAX_WAIT:
                log.warning("%s %s: HTTP %d, retrying in %.0fs", p.name, name, e.code, retry_after)
                if cancel.wait(retry_after):
                    raise core.Cancelled() from e
                continue
            raise _explain(p, e, text) from e
        except OSError as e:  # includes timeouts
            if attempt == 5:
                raise ProviderError(f"无法连接 {p.name}，请检查网络。", reachable=False) from e
            log.warning("%s %s: %s, sending again", p.name, name, type(e).__name__)
            time.sleep(attempt)
    raise AssertionError("unreachable")


# ── Filters ────────────────────────────────────────────────────────────

# Lines Whisper is known to invent over music and silence (they come from video
# subtitles in its training data). Kept narrow: a host may well say 点赞订阅 for real.
HALLUCINATIONS = re.compile(
    r"不吝点赞|打赏支持明镜|明镜与点点|字幕由.{0,20}提供|Amara\.org|优优独播剧场|YoYo Television|^作词|^作曲"
)
_CHARS = re.compile(rf"[{core.CJK}A-Za-z0-9]")


def looks_invented(text: str, duration: float) -> bool:
    """Groq reports no_speech_prob as 0 even over music, so judge by the text:
    real Mandarin runs 3–6 characters a second, while an invented line is
    usually stretched thin over a long run of music."""
    if HALLUCINATIONS.search(text):
        return True
    return duration >= 8 and len(_CHARS.findall(text)) / duration < 1.0


# ── Running jobs in parallel, handing results over in order ────────────


def _ordered(jobs: list, work: Callable, events: core.Events, cancel: threading.Event, unit: str,
             workers: int) -> Iterator:
    """Run `work` on every job in parallel, yielding (job, result) in job order."""
    n = len(jobs)
    results: dict[int, object] = {}
    emitted = 0
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        pending = {pool.submit(work, job): i for i, job in enumerate(jobs)}
        while pending:
            done, _ = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
            if cancel.is_set():
                raise core.Cancelled()
            for future in done:
                results[pending.pop(future)] = future.result()  # re-raises a job's error
            if done:
                events.progress(len(results) / n, f"云端已识别 {len(results)} / {n} {unit}")
            while emitted in results:
                yield jobs[emitted], results.pop(emitted)
                emitted += 1
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _groq(samples: np.ndarray, ep: core.Episode, key: str, events, cancel) -> Iterator[core.Segment]:
    p, prompt = GROQ, core.build_prompt(ep)
    parts = list(enumerate(audio.chunks(samples)))  # (index, (start, samples))
    events.progress(0.0, f"正在上传 {len(parts)} 段音频…")

    def work(job):
        index, (_start, chunk) = job
        speech, times = vad.speech_only(chunk)  # music never reaches Whisper
        if times is None:
            return {"segments": []}, None
        fields = [("model", p.model), ("language", "zh"), ("response_format", "verbose_json"),
                  ("timestamp_granularities[]", "segment"), ("temperature", "0"), ("prompt", prompt)]
        return _post_audio(p, key, fields, f"chunk{index:02d}.ogg", audio.to_opus(speech), cancel), times

    for (_index, (start, _chunk)), (result, times) in _ordered(parts, work, events, cancel, "段", p.workers):
        offset = start / SR
        for s in result.get("segments", []):
            text = core.tidy(s["text"])
            begin = offset + times.original(s["start"])
            if not text or looks_invented(text, s["end"] - s["start"]):
                if text:
                    log.info("dropped invented line at %s: %s", core.fmt_ts(begin), text[:40])
                continue
            yield core.Segment(begin, max(offset + times.original(s["end"], is_end=True), begin), text)


def _siliconflow(samples: np.ndarray, ep: core.Episode, key: str, events, cancel) -> Iterator[core.Segment]:
    p, fix = SILICONFLOW, core.name_fixer(ep)
    events.progress(None, "正在按停顿切分音频…")
    found = audio.speech_pieces(samples)
    events.progress(0.0, f"正在上传 {len(found)} 个片段…")

    def work(piece):
        start, end, _capped = piece
        return _post_audio(p, key, [("model", p.model)], f"p{start}.ogg", audio.to_opus(samples[start:end]), cancel)

    for (start, end, capped), result in _ordered(found, work, events, cancel, "个片段", p.workers):
        text = fix(core.tidy(result.get("text", "")))
        if capped and text.endswith("。"):  # cut for length, so probably mid-sentence
            text = text[:-1] + "，"
        if text:
            yield core.Segment(start / SR, end / SR, text)


RUNNERS = {"groq": _groq, "siliconflow": _siliconflow}


def order(settings: dict) -> list[Provider]:
    """Providers that have a key, the preferred one first."""
    preferred = settings.get("provider", SILICONFLOW.key)
    ranked = sorted(PROVIDERS.values(), key=lambda p: p.key != preferred)
    return [p for p in ranked if core.get_key(settings, p.key)]


def transcribe(samples: np.ndarray, ep: core.Episode, settings: dict, events: core.Events, cancel: threading.Event):
    """(label(), segments). Falls back to the next provider if one cannot be
    reached before it has produced anything."""
    candidates = order(settings)
    if not candidates:
        raise core.VerbatimError("云端识别需要一个免费的 API 密钥，请在「识别方式」下设置。")
    used: list[Provider] = []

    def segments():
        events.stage(2)
        for i, p in enumerate(candidates):
            used[:] = [p]
            produced = False
            try:
                for seg in RUNNERS[p.key](samples, ep, core.get_key(settings, p.key), events, cancel):
                    produced = True
                    yield seg
                return
            except ProviderError as e:
                if produced or e.reachable or i == len(candidates) - 1:
                    raise
                log.warning("%s unreachable (%s); trying %s", p.name, e, candidates[i + 1].name)
                events.progress(None, f"{p.name} 连接不上，改用 {candidates[i + 1].name}…")

    def label() -> str:
        p = used[0] if used else candidates[0]
        return f"{p.name} {p.model}"

    return label, segments()

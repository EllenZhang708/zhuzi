"""The transcription pipeline, free of any UI.

A job resolves a source (a Xiaoyuzhou episode link or a local audio file),
fetches the audio, transcribes it in the cloud (Groq or SiliconFlow) or on
this machine (SenseVoice), and writes a Markdown transcript.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Protocol

import secrets_store

APP_NAME = "逐字"
APP_ID = "Zhuzi"
APP_VERSION = "1.1.0-beta.1"

log = logging.getLogger("zhuzi")

# A frozen macOS build's Python looks for CA certificates where the build
# machine's OpenSSL kept them, which a user's Mac doesn't have; every HTTPS
# request would fail. Point it at the bundled certifi set instead.
if sys.platform == "darwin" and getattr(sys, "frozen", False) and "SSL_CERT_FILE" not in os.environ:
    import certifi

    os.environ["SSL_CERT_FILE"] = certifi.where()


def _documents() -> Path:
    """The user's Documents folder, wherever OneDrive or policy has moved it."""
    if sys.platform == "win32":
        import ctypes
        import uuid

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                        ("Data4", ctypes.c_ubyte * 8)]

        u = uuid.UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7")  # FOLDERID_Documents
        guid = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
        shell32, ole32 = ctypes.WinDLL("shell32"), ctypes.WinDLL("ole32")
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), ctypes.c_uint32, ctypes.c_void_p,
                                                 ctypes.POINTER(ctypes.c_wchar_p)]
        ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        out = ctypes.c_wchar_p()
        if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0:
            try:
                return Path(out.value)
            finally:
                ole32.CoTaskMemFree(ctypes.cast(out, ctypes.c_void_p))
    return Path.home() / "Documents"


def _locations() -> tuple[Path, Path, bool]:
    """(data folder, default output folder, portable?).

    A build with a file named `portable` beside the exe keeps everything next
    to itself; an installed build uses %LOCALAPPDATA% (on a Mac, Application
    Support) and Documents; running from source uses the project folder."""
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / APP_ID, _documents() / "逐字稿", False
        exe_dir = Path(sys.executable).resolve().parent
        if (exe_dir / "portable").exists():
            return exe_dir / "data", exe_dir.parent / "逐字稿", True
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_ID
        return base, _documents() / "逐字稿", False
    project = Path(__file__).resolve().parents[1]
    return project / "data", project / "逐字稿", True


DATA_DIR, DEFAULT_OUTPUT, PORTABLE = _locations()
MODELS_DIR = DATA_DIR / "models"
AUDIO_DIR = DATA_DIR / "audio"
LOG_DIR = DATA_DIR / "logs"
SETTINGS_FILE = DATA_DIR / "settings.json"
FALLBACK_OUTPUT = DATA_DIR / "逐字稿"  # used when the chosen folder refuses writes

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
SAMPLE_RATE = 16000
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".mp4", ".webm", ".wma", ".aif", ".aiff"}


class VerbatimError(Exception):
    """A failure whose message is written for the person using the app."""


class Cancelled(Exception):
    pass


# ── Engines ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EngineSpec:
    key: str
    label: str
    note: str
    stages: tuple[str, str, str]


CLOUD, LOCAL = "cloud", "local"
ENGINES = {
    CLOUD: EngineSpec(CLOUD, "云端极速", "一期约半分钟 · 免费额度 · 需联网", ("获取音频", "准备音频", "云端识别")),
    LOCAL: EngineSpec(LOCAL, "本机离线", "SenseVoice · 首次需下载约 230 MB · 不联网", ("获取音频", "准备模型", "识别文字")),
}
DEFAULT_ENGINE = CLOUD


# ── Settings and keys ───────────────────────────────────────────────────


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), "utf-8")


def get_key(settings: dict, provider: str) -> str:
    return secrets_store.unprotect(settings.get("keys", {}).get(provider, ""))


def set_key(settings: dict, provider: str, key: str) -> None:
    keys = settings.setdefault("keys", {})
    if key:
        keys[provider] = secrets_store.protect(provider, key)
    elif provider in keys:
        secrets_store.forget(keys.pop(provider))


# ── Sources ─────────────────────────────────────────────────────────────


@dataclass
class Episode:
    title: str
    source: str  # the episode page, or the local file path
    podcast: str = ""
    author: str = ""
    duration: float | None = None
    published: date | None = None
    audio_url: str | None = None
    cover_url: str | None = None
    episode_id: str | None = None
    local_path: Path | None = None


EPISODE_RE = re.compile(r"xiaoyuzhoufm\.com/episode/([0-9A-Za-z]+)")


def find_episode_id(text: str) -> str | None:
    m = EPISODE_RE.search(text or "")
    return m.group(1) if m else None


def http_get(url: str, timeout: float = 20, attempts: int = 3, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError:
            raise
        except OSError:  # timeouts and dropped connections are often momentary
            if attempt == attempts:
                raise
            log.warning("GET %s failed (attempt %d), retrying", url, attempt, exc_info=True)
            time.sleep(attempt)
    raise AssertionError("unreachable")


def fetch_episode(text: str) -> Episode:
    eid = find_episode_id(text)
    if not eid:
        raise VerbatimError("这不像是小宇宙单集链接。")
    url = f"https://www.xiaoyuzhoufm.com/episode/{eid}"
    try:
        page = http_get(url).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise VerbatimError("找不到这一集，链接可能已失效。") from e
        raise VerbatimError(f"小宇宙返回了错误（HTTP {e.code}）。") from e
    except OSError as e:
        raise VerbatimError("无法连接小宇宙，请检查网络。") from e

    data: dict = {}
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if m:
        try:
            data = json.loads(m.group(1))["props"]["pageProps"]["episode"] or {}
        except (ValueError, KeyError, TypeError):
            data = {}

    def meta(prop: str) -> str | None:
        mm = re.search(rf'<meta[^>]+property="og:{prop}"[^>]+content="([^"]*)"', page)
        return html.unescape(mm.group(1)) if mm else None

    podcast = data.get("podcast") or {}
    audio = (
        (data.get("enclosure") or {}).get("url")
        or ((data.get("media") or {}).get("source") or {}).get("url")
        or meta("audio")
    )
    if not audio:
        raise VerbatimError("这一集没有公开音频，可能是付费或私密内容。")

    podcast_name = podcast.get("title") or ""
    if not podcast_name:
        mm = re.search(r"《(.+?)》", meta("description") or "")
        podcast_name = mm.group(1) if mm else ""

    published = None
    if data.get("pubDate"):
        try:
            published = datetime.fromisoformat(data["pubDate"].replace("Z", "+00:00")).astimezone().date()
        except ValueError:
            pass

    image = data.get("image") or podcast.get("image") or {}
    return Episode(
        title=(data.get("title") or meta("title") or eid).strip(),
        source=url,
        podcast=podcast_name,
        author=podcast.get("author") or "",
        duration=float(data["duration"]) if data.get("duration") else None,
        published=published,
        audio_url=audio,
        cover_url=image.get("thumbnailUrl") or image.get("picUrl"),
        episode_id=eid,
    )


def fetch_cover(url: str) -> bytes | None:
    try:
        return http_get(url, timeout=15)
    except OSError:
        return None


def local_episode(path: Path) -> Episode:
    import audio

    if not path.is_file():
        raise VerbatimError("找不到这个文件。")
    if path.suffix.lower() not in AUDIO_EXTS:
        raise VerbatimError("不支持这种文件，请选择 MP3、M4A、WAV 等音频。")
    return Episode(title=path.stem, source=str(path), duration=audio.probe_duration(path), local_path=path)


# ── Downloads ───────────────────────────────────────────────────────────

ProgressFn = Callable[[int, int | None], None]


def _download_once(url: str, dest: Path, on_progress: ProgressFn, cancel: threading.Event) -> None:
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": USER_AGENT}
    if have:
        headers["Range"] = f"bytes={have}-"
    try:
        resp = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30)
    except urllib.error.HTTPError as e:
        if e.code == 416 and have:  # the partial file is already whole
            part.replace(dest)
            return
        raise
    with resp:
        if have and resp.status != 206:  # server ignored the range; start over
            have = 0
        length = resp.headers.get("Content-Length")
        total = have + int(length) if length else None
        done = have
        with open(part, "ab" if have else "wb") as f:
            while True:
                if cancel.is_set():
                    raise Cancelled()
                chunk = resp.read(1 << 18)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                on_progress(done, total)
    if total is not None and done < total:
        raise ConnectionError("download ended early")
    part.replace(dest)


def download(url: str, dest: Path, on_progress: ProgressFn, cancel: threading.Event, attempts: int = 4) -> None:
    """Download with resume: a dropped connection picks up where it stopped."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, attempts + 1):
        try:
            _download_once(url, dest, on_progress, cancel)
            return
        except Cancelled:
            raise
        except urllib.error.HTTPError:
            raise
        except OSError:
            if attempt == attempts:
                raise
            log.warning("Download interrupted (attempt %d), resuming", attempt, exc_info=True)
            time.sleep(2 * attempt)


def fetch_audio(ep: Episode, on_progress: ProgressFn, cancel: threading.Event) -> Path:
    if ep.local_path:
        return ep.local_path
    suffix = Path(urllib.parse.urlparse(ep.audio_url).path).suffix or ".m4a"
    dest = AUDIO_DIR / f"{ep.episode_id}{suffix}"
    if not dest.exists():
        try:
            download(ep.audio_url, dest, on_progress, cancel)
        except (Cancelled, VerbatimError):
            raise
        except OSError as e:
            raise VerbatimError("音频下载失败，请检查网络后重试。") from e
    return dest


HF_ENDPOINTS = ("https://huggingface.co", "https://hf-mirror.com")


def fetch_model(repo: str, files: list[str], folder: Path, on_progress: ProgressFn, cancel: threading.Event) -> Path:
    """Fetch model files from Hugging Face into `folder`, falling back to a mirror."""
    if (folder / ".complete").exists():
        return folder
    last: Exception | None = None
    for endpoint in HF_ENDPOINTS:
        try:
            listing = json.loads(http_get(f"{endpoint}/api/models/{repo}/tree/main", timeout=15))
            sizes = {item["path"]: int(item.get("size") or 0) for item in listing if item.get("type") == "file"}
            missing = [f for f in files if f not in sizes]
            if missing:
                raise ValueError(f"{endpoint} lists no {missing}")
            total, base = sum(sizes[f] for f in files), 0
            for name in files:
                dest = folder / name
                if not (dest.exists() and dest.stat().st_size == sizes[name]):
                    download(
                        f"{endpoint}/{repo}/resolve/main/{name}",
                        dest,
                        lambda done, _total, base=base: on_progress(base + done, total),
                        cancel,
                    )
                base += sizes[name]
            (folder / ".complete").write_text(f"{repo} via {endpoint}\n", "utf-8")
            return folder
        except Cancelled:
            raise
        except (OSError, ValueError, KeyError) as e:
            log.warning("Model download from %s failed", endpoint, exc_info=True)
            last = e
    raise VerbatimError("识别模型下载失败，请检查网络后重试。") from last


# ── Text ────────────────────────────────────────────────────────────────


@dataclass
class Segment:
    start: float
    end: float
    text: str


_converter = None
CJK = "㐀-鿿豈-﫿"
# Whisper marks pauses inside a Chinese segment with a space.
_PAUSE = re.compile(rf"(?<=[{CJK}])\s+(?=[{CJK}])")
# ...and often punctuates Chinese with half-width marks.
_HALF = re.compile(rf"(?<=[{CJK}])\s*([,?!:;])|([,?!:;])\s*(?=[{CJK}])")
_FULL = {",": "，", "?": "？", "!": "！", ":": "：", ";": "；"}
# SenseVoice can tag language, emotion and sound events, and mark them with emoji.
_TAGS = re.compile(r"<\|[^|]*\|>|[\U0001F300-\U0001FAFF☀-➿]")
_ENDS_PUNCT = tuple("。！？…；，、：”’）》!?.;,:")
_ENDS_SENTENCE = tuple("。！？…!?")


def tidy(text: str) -> str:
    global _converter
    if _converter is None:
        from opencc import OpenCC

        _converter = OpenCC("t2s")
    text = _PAUSE.sub("，", _converter.convert(_TAGS.sub("", text).strip()))
    return _HALF.sub(lambda m: _FULL[m.group(1) or m.group(2)], text)


def build_prompt(ep: Episode) -> str:
    """Bias Whisper toward Simplified Chinese with punctuation, and toward the
    show's own names. It copies the prompt's style, so the prompt needs real
    punctuation: a bare "以下是普通话的句子。" yields unpunctuated output."""
    prompt = "以下是普通话的句子。"
    if ep.podcast:
        prompt += f"这是播客《{ep.podcast}》" + (f"，主播是{ep.author}" if ep.author else "") + "。"
    else:
        prompt += f"这是一期中文播客，标题是《{ep.title}》。"
    return prompt


def name_fixer(ep: Episode) -> Callable[[str], str]:
    """For engines that take no prompt (SenseVoice): replace homophones of the
    show's names — same pinyin and same tones — with the names themselves,
    so 梦言 becomes 孟岩. Matching tones keeps 黎明 from becoming 李明."""
    from pypinyin import Style, lazy_pinyin

    terms = [t for t in (ep.podcast, ep.author, "播客") if t and re.fullmatch(rf"[{CJK}]{{2,8}}", t)]
    wanted = [(t, lazy_pinyin(t, style=Style.TONE3)) for t in terms]

    def fix(text: str) -> str:
        for term, sound in wanted:
            n, i = len(term), 0
            while i + n <= len(text):
                window = text[i : i + n]
                if window != term and re.fullmatch(rf"[{CJK}]+", window) and lazy_pinyin(window, style=Style.TONE3) == sound:
                    text = text[:i] + term + text[i + n :]
                i += 1
        return text

    return fix


class Paragraphs:
    """Groups segments into paragraphs as they arrive: at a long pause, or at
    the first sentence end once a paragraph has run long."""

    def __init__(self, max_gap: float = 1.5, max_span: float = 45.0):
        self.max_gap = max_gap
        self.max_span = max_span
        self.groups: list[list[Segment]] = []

    def add(self, seg: Segment) -> bool:
        """Return True when the segment opens a new paragraph."""
        cur = self.groups[-1] if self.groups else None
        if cur is None or seg.start - cur[-1].end > self.max_gap or self._long(cur, seg):
            self.groups.append([seg])
            return True
        cur.append(seg)
        return False

    def _long(self, cur: list[Segment], seg: Segment) -> bool:
        span = seg.end - cur[0].start
        return span > 2 * self.max_span or (span > self.max_span and cur[-1].text.endswith(_ENDS_SENTENCE))


def joint(text: str, last: bool) -> str:
    """The punctuation to put after a segment: a comma between, a full stop at the end."""
    if not text.endswith(_ENDS_PUNCT):
        return "。" if last else "，"
    return ""


def paragraph_text(group: list[Segment]) -> str:
    out = []
    for i, seg in enumerate(group):
        text, last = seg.text, i == len(group) - 1
        if last and text.endswith(("，", "、", ",")):
            text = text[:-1]
        out.append(text + joint(text, last))
    return "".join(out)


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def fmt_minutes(seconds: float) -> str:
    """A spoken-style duration: 1 小时 20 分, 12 分钟, 不到 1 分钟."""
    minutes = round(seconds / 60)
    if minutes < 1:
        return "不到 1 分钟"
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h} 小时 {m} 分"
    return f"{h} 小时" if h else f"{m} 分钟"


class Events(Protocol):
    def stage(self, index: int) -> None: ...
    def progress(self, fraction: float | None, detail: str) -> None: ...
    def segment(self, seg: Segment) -> None: ...


# ── Markdown ────────────────────────────────────────────────────────────


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", name).strip(" .")
    return cleaned[:120] or "未命名"


def unique_path(folder: Path, stem: str, suffix: str = ".md") -> Path:
    path = folder / f"{stem}{suffix}"
    n = 2
    while path.exists():
        path = folder / f"{stem} ({n}){suffix}"
        n += 1
    return path


def write_markdown(path: Path, ep: Episode, segments: list[Segment], engine: str, stopped_at: float | None = None) -> None:
    q = lambda s: json.dumps(s, ensure_ascii=False)  # a JSON string is a valid YAML scalar
    front = ["---", f"title: {q(ep.title + '｜逐字稿')}", "aliases:", f"  - {q(ep.title)}", "type: podcast-transcript"]
    if ep.podcast:
        front.append(f"podcast: {q(ep.podcast)}")
    if ep.author:
        front.append(f"host: {q(ep.author)}")
    front.append(f"source: {q(ep.source if not ep.local_path else ep.local_path.name)}")
    if ep.published:
        front.append(f"published: {ep.published.isoformat()}")
    if ep.duration:
        front.append(f"duration: {q(fmt_ts(ep.duration))}")
    front += [
        f"model: {q(engine)}",
        f"transcribed: {date.today().isoformat()}",
        f"status: {'partial' if stopped_at is not None else 'auto-transcribed'}",
        "tags:",
        "  - podcast",
        "  - transcript",
        "---",
    ]
    body = [
        "",
        f"# {ep.title}｜逐字稿",
        "",
        "> [!warning] 自动识别提示",
        f"> 以下内容由语音识别模型（{engine}）自动生成，可能包含错字、专有名词识别错误和时间戳偏差；"
        "请与原音频核对。未执行说话人分离。",
        "",
    ]
    if stopped_at is not None:
        body += ["> [!note] 未完成", f"> 转写在 {fmt_ts(stopped_at)} 处停止，之后的内容没有转写。", ""]
    info = []
    if ep.podcast:
        info.append(f"- 节目：{ep.podcast}" + (f"（{ep.author}）" if ep.author else ""))
    if ep.local_path:
        info.append(f"- 原始音频：`{ep.local_path.name}`")
    else:
        info.append(f"- 来源：{ep.source}")
    if ep.published:
        info.append(f"- 发布：{ep.published.isoformat()}")
    if ep.duration:
        info.append(f"- 时长：{fmt_ts(ep.duration)}")
    body += info + ["", "## 完整逐字稿", ""]
    paragraphs = Paragraphs()
    for seg in segments:
        paragraphs.add(seg)
    for group in paragraphs.groups:
        body += [f"`{fmt_ts(group[0].start)}` {paragraph_text(group)}", ""]
    body += ["## 校对与个人笔记", "", "- ", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(front + body), "utf-8")


# ── The whole job ───────────────────────────────────────────────────────


def _mb(n: int) -> str:
    return f"{n / 1_048_576:,.0f} MB"


class _StayAwake:
    """Keep the computer working while a job runs.

    Windows: holds power requests so the machine does not idle into sleep and
    the process is not suspended when the screen goes dark (Modern Standby), and
    opts the process out of EcoQoS throttling, which otherwise slows a window
    that is in the background. macOS: declares a user-initiated activity, which
    blocks idle sleep and keeps App Nap away. Closing the lid or choosing Sleep
    still pauses the job."""

    SYSTEM_REQUIRED, EXECUTION_REQUIRED = 1, 3  # POWER_REQUEST_TYPE
    REASON = "逐字：正在誊写音频"

    def __enter__(self):
        self.handle = self.activity = None
        if sys.platform == "darwin":
            try:
                import mac

                self.activity = mac.begin_activity(self.REASON)
            except (OSError, AttributeError):
                log.warning("Could not declare the activity; the Mac may sleep mid-job", exc_info=True)
            return self
        if sys.platform != "win32":
            return self
        import ctypes
        from ctypes import wintypes

        # A private kernel32 handle, so declaring signatures here touches no one else's.
        # They must be declared: undeclared, x64 ctypes passes GetCurrentProcess's
        # pseudo-handle truncated to 32 bits and SetProcessInformation quietly fails.
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.SetProcessInformation.restype = wintypes.BOOL

        class Throttling(ctypes.Structure):  # PROCESS_POWER_THROTTLING_STATE
            _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]

        # Version 1, control EXECUTION_SPEED, state 0 = never throttle; 4 = ProcessPowerThrottling
        state = Throttling(1, 0x1, 0)
        if not k32.SetProcessInformation(k32.GetCurrentProcess(), 4, ctypes.byref(state), ctypes.sizeof(state)):
            log.warning("Could not opt out of power throttling (error %d)", ctypes.get_last_error())

        class Detailed(ctypes.Structure):
            _fields_ = [
                ("LocalizedReasonModule", wintypes.HMODULE),
                ("LocalizedReasonId", wintypes.ULONG),
                ("ReasonStringCount", wintypes.ULONG),
                ("ReasonStrings", ctypes.POINTER(wintypes.LPWSTR)),
            ]

        class Reason(ctypes.Union):
            _fields_ = [("Detailed", Detailed), ("SimpleReasonString", wintypes.LPWSTR)]

        class ReasonContext(ctypes.Structure):  # REASON_CONTEXT
            _fields_ = [("Version", wintypes.ULONG), ("Flags", wintypes.DWORD), ("Reason", Reason)]

        context = ReasonContext(0, 0x1)  # POWER_REQUEST_CONTEXT_SIMPLE_STRING
        context.Reason.SimpleReasonString = self.REASON
        k32.PowerCreateRequest.restype = wintypes.HANDLE
        handle = k32.PowerCreateRequest(ctypes.byref(context))
        if handle and handle != wintypes.HANDLE(-1).value:
            self.handle = handle
            for kind in (self.SYSTEM_REQUIRED, self.EXECUTION_REQUIRED):
                k32.PowerSetRequest(wintypes.HANDLE(handle), kind)
        else:
            log.warning("PowerCreateRequest failed")
        return self

    def __exit__(self, *_exc):
        if self.activity:
            import mac

            mac.end_activity(self.activity)
        if self.handle:
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32")
            for kind in (self.SYSTEM_REQUIRED, self.EXECUTION_REQUIRED):
                k32.PowerClearRequest(wintypes.HANDLE(self.handle), kind)
            k32.CloseHandle(wintypes.HANDLE(self.handle))


@dataclass
class Result:
    path: Path | None
    segments: int
    stopped_at: float | None  # set when the job was stopped part-way
    engine: str = ""


def collect(segment_iter, events: Events) -> tuple[list[Segment], bool]:
    """Gather segments, passing each to the UI. A stop part-way keeps what was
    done and reports stopped=True; a stop before anything came back re-raises."""
    segments: list[Segment] = []
    try:
        for seg in segment_iter:
            events.segment(seg)
            segments.append(seg)
    except Cancelled:
        if not segments:
            raise
        return segments, True
    return segments, False


def run_job(
    ep: Episode,
    engine: str,
    out_dir: Path,
    events: Events,
    cancel: threading.Event,
    clip_seconds: float | None = None,
    settings: dict | None = None,
) -> Result:
    import audio

    settings = settings if settings is not None else load_settings()
    with _StayAwake():
        events.stage(0)
        events.progress(1.0 if ep.local_path else 0.0, "使用本地音频" if ep.local_path else "正在连接…")
        audio_path = fetch_audio(
            ep,
            lambda done, total: events.progress(
                done / total if total else None,
                f"已下载 {_mb(done)}" + (f" / {_mb(total)}" if total else ""),
            ),
            cancel,
        )
        events.progress(1.0, "音频已就绪")

        events.stage(1)
        events.progress(None, "正在解码音频…")
        try:
            samples = audio.decode(audio_path)
        except audio.AudioError as e:
            raise VerbatimError(str(e)) from e
        if clip_seconds:
            samples = samples[: int(clip_seconds * SAMPLE_RATE)]
        if cancel.is_set():
            raise Cancelled()

        if engine == CLOUD:
            import cloud

            label, segment_iter = cloud.transcribe(samples, ep, settings, events, cancel)
        else:
            import local

            label, segment_iter = local.transcribe(samples, ep, events, cancel)
        segments, stopped = collect(segment_iter, events)
        stopped_at = segments[-1].end if stopped else None

        name = safe_filename(f"{ep.title}-逐字稿")
        try:
            path = unique_path(out_dir, name)  # even looking inside a refused folder fails
            write_markdown(path, ep, segments, label(), stopped_at)
        except PermissionError:
            # macOS asks before an app may write into Documents; if that was
            # declined (or the folder is locked some other way), keep the work anyway.
            log.warning("No permission to write %s; saving to %s", path, FALLBACK_OUTPUT, exc_info=True)
            path = unique_path(FALLBACK_OUTPUT, name)
            write_markdown(path, ep, segments, label(), stopped_at)
        if not ep.local_path and stopped_at is None:
            audio_path.unlink(missing_ok=True)  # the episode page stays the reference
        return Result(path, len(segments), stopped_at, label())

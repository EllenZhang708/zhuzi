"""逐字 · Zhuzi — turn a podcast episode into a Markdown transcript.

    Zhuzi.exe                                                   open the app
    Zhuzi.exe --selftest SOURCE [--engine cloud|local] [--clip N]  run one job without the window
    Zhuzi.exe --uitest SOURCE --out DIR [--clip N]              drive the window through one job
    Zhuzi.exe --screenshot DIR                                  render each screen to PNG files

On a Mac the program is 逐字.app/Contents/MacOS/Zhuzi, with the same options.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import ssl
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

# A windowed build has no console; give libraries that print somewhere to write.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import core  # noqa: E402

log = logging.getLogger("zhuzi")
ASSETS = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "assets"


def setup_logging(name: str = "zhuzi.log") -> None:
    core.LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(core.LOG_DIR / name, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


# ── Headless self-test ──────────────────────────────────────────────────


class LogEvents:
    def __init__(self):
        self.last = 0.0
        self.segments = 0

    def stage(self, index):
        log.info("stage %d", index + 1)

    def progress(self, fraction, detail):
        if time.monotonic() - self.last > 5 or fraction in (0.0, 1.0):
            log.info("progress %s %s", "…" if fraction is None else f"{fraction:.0%}", detail)
            self.last = time.monotonic()

    def segment(self, seg):
        self.segments += 1
        if self.segments <= 5:
            log.info("segment [%s] %s", core.fmt_ts(seg.start), seg.text)


def selftest(source: str, clip: float, engine: str, out: Path) -> int:
    setup_logging("selftest.log")
    report: dict = {"source": source, "clip": clip, "engine": engine}
    started = time.monotonic()
    try:
        ep = core.fetch_episode(source) if core.find_episode_id(source) else core.local_episode(Path(source))
        report["title"] = ep.title
        result = core.run_job(ep, engine, out, LogEvents(), threading.Event(), clip_seconds=clip or None)
        report.update(ok=True, path=str(result.path), segments=result.segments, model=result.engine)
        report["cafile"] = ssl.get_default_verify_paths().cafile  # a frozen Mac build must use its own
    except Exception as e:
        log.exception("self-test failed")
        report.update(ok=False, error=f"{type(e).__name__}: {e}")
    report["seconds"] = round(time.monotonic() - started, 1)
    (core.LOG_DIR / "selftest-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    return 0 if report["ok"] else 1


# ── The window ──────────────────────────────────────────────────────────

from PySide6.QtCore import (  # noqa: E402
    QEasingCurve,
    QEvent,
    QObject,
    QPropertyAnimation,
    Qt,
    QThread,
    QTimer,
    QUrl,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QIcon, QPalette, QPixmap  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialog,
    QFileDialog,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import cloud  # noqa: E402
import local  # noqa: E402
import secrets_store  # noqa: E402
import theme  # noqa: E402
import updates  # noqa: E402
from widgets import (  # noqa: E402
    ANIMATE,
    ElidedLabel,
    EpisodeCard,
    Orbit,
    ProgressLine,
    Segmented,
    StageRow,
    TranscriptView,
    button,
    label,
    spaced,
)


class Resolver(QObject):
    """Reads a link's episode page (or probes a local file) off the UI thread.

    Uses a daemon thread rather than a QThread, so closing the window mid-read
    simply ends it."""

    resolved = Signal(int, object, object)
    failed = Signal(int, str)

    def resolve(self, token: int, text: str):
        threading.Thread(target=self._run, args=(token, text), daemon=True).start()

    def _run(self, token: int, text: str):
        try:
            if core.find_episode_id(text):
                ep = core.fetch_episode(text)
                cover = core.fetch_cover(ep.cover_url) if ep.cover_url else None
            else:
                ep, cover = core.local_episode(Path(text)), None
            self.resolved.emit(token, ep, cover)
        except core.VerbatimError as e:
            self.failed.emit(token, str(e))
        except Exception:
            log.exception("resolve failed")
            self.failed.emit(token, "读取节目信息失败。")


class JobThread(QThread):
    staged = Signal(int)
    progressed = Signal(float, str)  # a negative fraction means "busy, no measure"
    segmented = Signal(object)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        ep: core.Episode,
        engine: str,
        out_dir: Path,
        parent: QObject,
        settings: dict,
        clip: float | None = None,
    ):
        super().__init__(parent)
        self.ep, self.engine, self.out_dir, self.clip = ep, engine, out_dir, clip
        self.settings = dict(settings)  # a snapshot: the UI may change settings mid-job
        self.cancel = threading.Event()

    # core.Events
    def stage(self, index):
        self.staged.emit(index)

    def progress(self, fraction, detail):
        self.progressed.emit(-1.0 if fraction is None else fraction, detail)

    def segment(self, seg):
        self.segmented.emit(seg)

    def run(self):
        try:
            self.succeeded.emit(
                core.run_job(self.ep, self.engine, self.out_dir, self, self.cancel, self.clip, self.settings)
            )
        except core.Cancelled:
            self.cancelled.emit()
        except core.VerbatimError as e:
            log.exception("job failed")
            self.failed.emit(str(e))
        except Exception:
            log.exception("job crashed")
            self.failed.emit(f"出现了意外错误，详情记录在 {core.LOG_DIR / 'zhuzi.log'}。")


def tint_titlebar(widget: QWidget, p: theme.Palette) -> None:
    """Paint the Windows 11 title bar in the page's own colors."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    def colorref(hex_: str) -> int:
        r, g, b = (int(hex_[i : i + 2], 16) for i in (1, 3, 5))
        return r | g << 8 | b << 16

    hwnd = wintypes.HWND(int(widget.winId()))
    # DWMWA_USE_IMMERSIVE_DARK_MODE, _BORDER_COLOR, _CAPTION_COLOR, _TEXT_COLOR
    for attr, value in ((20, int(p.dark)), (34, colorref(p.line)), (35, colorref(p.bg)), (36, colorref(p.ink))):
        v = ctypes.c_int(value)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))


class UpdateCheck(QObject):
    """Asks GitHub, off the UI thread, whether a newer release is out."""

    found = Signal(str)

    def check(self):
        def run():
            tag = updates.newer_release()
            if tag:
                self.found.emit(tag)

        threading.Thread(target=run, daemon=True).start()


class Probe(QObject):
    """Checks, off the UI thread, which cloud providers answer from this network."""

    found = Signal(str, bool)

    def check(self, providers):
        for p in providers:
            threading.Thread(target=lambda p=p: self.found.emit(p.key, cloud.reachable(p)), daemon=True).start()


class SetupDialog(QDialog):
    """Choose a cloud provider and give it a key; only a working key is saved.
    The first time, it doubles as the welcome."""

    USE_LOCAL = 2  # exec() result: "use offline transcription instead"

    def __init__(self, parent: "MainWindow", settings: dict, welcome: bool = False):
        super().__init__(parent)
        self.setObjectName("KeyDialog")
        self.setWindowTitle("云端识别")
        self.setMinimumWidth(580)
        self.settings = settings
        self.reach: dict[str, bool] = {}
        self.provider = settings.get("provider") or cloud.SILICONFLOW.key
        self.chose = bool(settings.get("provider"))  # once the user picks, probing stops steering

        col = QVBoxLayout(self)
        col.setContentsMargins(34, 28, 34, 26)
        col.setSpacing(0)
        col.addWidget(label("欢迎使用逐字" if welcome else "云端识别", "DialogTitle", 1))
        col.addSpacing(12)
        intro = (
            "云端识别一期节目只要半分钟左右，用的是服务商的免费额度。选一家，花一分钟拿个免费密钥就能开始。"
            if welcome
            else "选择一家云端服务，填入你的免费密钥。两家都填，连不上时会自动换另一家。"
        )
        col.addWidget(label(intro, "DialogBody", wrap=True))
        col.addSpacing(18)

        self.choice = Segmented([(p.key, p.name) for p in cloud.PROVIDERS.values()], self.provider)
        self.choice.changed.connect(self._pick)
        col.addWidget(self.choice, 0, Qt.AlignmentFlag.AlignLeft)
        col.addSpacing(8)
        self.status = label("", "Hint", 0.3)
        col.addWidget(self.status)
        col.addSpacing(16)

        self.steps = QLabel()
        self.steps.setObjectName("DialogBody")
        self.steps.setTextFormat(Qt.TextFormat.RichText)
        self.steps.setOpenExternalLinks(True)
        self.steps.setWordWrap(True)
        col.addWidget(self.steps)
        col.addSpacing(14)
        self.field = QLineEdit(objectName="KeyField")
        self.field.setEchoMode(QLineEdit.EchoMode.Password)
        col.addWidget(self.field)
        col.addSpacing(10)
        self.message = label("", "Hint", wrap=True)
        col.addWidget(self.message)
        col.addSpacing(24)

        row = QHBoxLayout()
        row.setSpacing(12)
        if welcome:
            offline = button("先用本机离线识别", "Link")
            offline.clicked.connect(lambda: self.done(self.USE_LOCAL))
            row.addWidget(offline)
        row.addStretch()
        cancel = button("以后再说" if welcome else "取消", "Ghost")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.save = button("验证并保存", "Primary")
        self.save.setDefault(True)
        self.save.clicked.connect(self._save)
        row.addWidget(self.save)
        col.addLayout(row)

        self._pick(self.provider, by_user=False)
        self.probe = Probe(self)
        self.probe.found.connect(self._found)
        self.probe.check(cloud.PROVIDERS.values())

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        tint_titlebar(self, self.parent().palette_now())

    def _pick(self, key: str, by_user: bool = True):
        self.provider = key
        self.chose = self.chose or by_user
        p = cloud.PROVIDERS[key]
        host = p.key_page.split("/")[2]
        self.steps.setText(
            f'<p style="line-height:160%">1.&nbsp;打开 <a href="{p.key_page}">{host}</a>，{p.signup_hint}<br>'
            f"2.&nbsp;复制以 {p.key_prefix} 开头的密钥，粘贴到下面</p>"
        )
        self.field.setPlaceholderText(f"{p.key_prefix}…")
        self.field.setText(core.get_key(self.settings, key))
        self._say(secrets_store.WHERE)
        self._show_status()

    def _found(self, key: str, ok: bool):
        self.reach[key] = ok
        # Steer a first-time user to whichever provider their network can reach.
        if not self.chose and not ok and key == self.provider:
            other = next(k for k in cloud.PROVIDERS if k != key)
            if self.reach.get(other, True):
                self.choice.buttons[other].setChecked(True)
                self._pick(other, by_user=False)
        self._show_status()

    def _show_status(self):
        p = cloud.PROVIDERS[self.provider]
        if self.provider not in self.reach:
            state = "正在检测网络…"
        elif self.reach[self.provider]:
            state = "当前网络可以连接"
        else:
            state = "当前网络连不上"
        self.status.setText(f"{p.region_note} · {state}")

    def _say(self, text: str, error: bool = False):
        self.message.setObjectName("Error" if error else "Hint")
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)
        self.message.setText(text)

    def _save(self):
        p, key = cloud.PROVIDERS[self.provider], self.field.text().strip()
        self.save.setEnabled(False)
        self.save.setText("正在验证…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            cloud.check_key(p, key)  # about a second; not worth a thread
        except core.VerbatimError as e:
            self._say(str(e), error=True)
            return
        finally:
            QApplication.restoreOverrideCursor()
            self.save.setEnabled(True)
            self.save.setText("验证并保存")
        core.set_key(self.settings, p.key, key)
        self.settings["provider"] = p.key
        self.accept()


class MainWindow(QWidget):
    def __init__(self, palette: theme.Palette | None = None, welcome: bool = True):
        super().__init__()
        self.setObjectName("Root")
        self.setWindowTitle("逐字")
        self.setWindowIcon(QIcon(str(ASSETS / ("icon.ico" if sys.platform == "win32" else "icon.png"))))
        self.resize(980, 760)
        self.setMinimumSize(860, 680)
        self.setAcceptDrops(True)

        self.settings = core.load_settings()
        chosen = self.settings.get("engine")
        self.engine = chosen if chosen in core.ENGINES else core.DEFAULT_ENGINE
        self.out_dir = Path(self.settings.get("output") or core.DEFAULT_OUTPUT)
        self.episode: core.Episode | None = None
        self.job: JobThread | None = None
        self.result: core.Result | None = None
        self.token = 0
        self.forced_palette = palette
        self.clip_seconds: float | None = None  # set by --uitest to keep runs short

        outer = QVBoxLayout(self)
        outer.setContentsMargins(56, 30, 56, 34)
        outer.setSpacing(0)
        outer.addLayout(self._header())
        outer.addSpacing(30)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._compose_page())
        self.stack.addWidget(self._work_page())
        outer.addWidget(self.stack, 1)

        self.resolver = Resolver(self)
        self.resolver.resolved.connect(self._resolved)
        self.resolver.failed.connect(self._resolve_failed)
        self.last_clip = ""
        self.debounce = QTimer(self, singleShot=True, interval=350)
        self.debounce.timeout.connect(self._resolve)
        self.source.textChanged.connect(lambda: self.debounce.start())

        hints = QGuiApplication.styleHints()
        hints.colorSchemeChanged.connect(lambda _scheme: self.apply_theme())
        self.apply_theme()
        self._update_engine_hint()
        QTimer.singleShot(0, self._prefill_from_clipboard)
        if welcome and not self.settings.get("welcomed") and not cloud.order(self.settings):
            QTimer.singleShot(600, self._welcome)  # once, after the window has appeared
        if welcome:
            self.updater = UpdateCheck(self)
            self.updater.found.connect(self._offer_update)
            self.updater.check()

    # ── layout ──

    def _header(self):
        row = QHBoxLayout()
        row.setSpacing(12)
        seal = QLabel()
        dpr = self.devicePixelRatioF() or 1
        pix = QPixmap(str(ASSETS / "icon.png")).scaled(
            round(30 * dpr), round(30 * dpr), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        pix.setDevicePixelRatio(dpr)
        seal.setPixmap(pix)
        row.addWidget(seal)
        row.addWidget(label("逐字", "Wordmark", 3))
        row.addStretch()
        self.update_link = button("", "Link")  # appears only when a newer release exists
        self.update_link.hide()
        self.update_link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(updates.RELEASES)))
        row.addWidget(self.update_link)
        row.addSpacing(18)
        row.addWidget(label("ZHUZI", "Kicker", 3.5))
        return row

    def _offer_update(self, tag: str):
        self.update_link.setText(f"有新版本 {tag}")
        self.update_link.show()

    def _compose_page(self):
        page = QWidget(objectName="Page")
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addStretch(2)  # with the stretch below, sits the form just above center
        self.headline = label("把声音，誊成文字。", "Headline", 1.5)
        col.addWidget(self.headline)
        # A small orbiting system beside the headline; placed by hand, outside the layout.
        self.deco = Orbit(196, [(1.0, 96, -64), (0.6, 47, 152), (0.27, 0, 0)], body=3.2, core_dot=2.2)
        self.deco.setParent(page)
        page.installEventFilter(self)
        self.compose = page
        col.addSpacing(10)
        col.addWidget(label("贴入一集小宇宙的链接，或拖入一段本地音频。", "Lede", 0.5))
        col.addSpacing(44)

        col.addWidget(label("来源", "FieldLabel", 2))
        row = QHBoxLayout()
        row.setSpacing(18)
        self.source = QLineEdit(objectName="Source")
        self.source.setPlaceholderText("https://www.xiaoyuzhoufm.com/episode/…")
        self.source.setClearButtonEnabled(True)
        self.source.returnPressed.connect(self._start_if_ready)
        row.addWidget(self.source, 1)
        pick = button("选择本地文件", "Link")
        pick.clicked.connect(self._pick_file)
        row.addWidget(pick, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addLayout(row)
        col.addSpacing(18)

        # Fixed height, so the page does not jump as the card comes and goes.
        slot = QWidget()
        slot.setFixedHeight(96)
        slot_col = QVBoxLayout(slot)
        slot_col.setContentsMargins(0, 0, 0, 0)
        self.card = EpisodeCard()
        self.card.hide()
        self.source_hint = label("支持 MP3、M4A、WAV 等格式 · 也可以直接把文件拖进窗口", "Hint", 0.3)
        slot_col.addWidget(self.card)
        slot_col.addWidget(self.source_hint, 0, Qt.AlignmentFlag.AlignTop)
        slot_col.addStretch()
        col.addWidget(slot)
        col.addSpacing(30)

        grid = QGridLayout()
        grid.setHorizontalSpacing(56)
        grid.setVerticalSpacing(10)
        grid.addWidget(label("识别方式", "FieldLabel", 2), 0, 0)
        grid.addWidget(label("保存至", "FieldLabel", 2), 0, 1)
        self.engines = Segmented([(k, e.label) for k, e in core.ENGINES.items()], self.engine)
        self.engines.changed.connect(self._set_engine)
        grid.addWidget(self.engines, 1, 0, Qt.AlignmentFlag.AlignLeft)
        out_row = QHBoxLayout()
        out_row.setSpacing(16)
        self.out_label = ElidedLabel(str(self.out_dir), "Path")
        out_row.addWidget(self.out_label)
        change = button("更改", "Link")
        change.clicked.connect(self._pick_folder)
        out_row.addWidget(change)
        out_row.addStretch()
        grid.addLayout(out_row, 1, 1)
        hint_row = QHBoxLayout()
        hint_row.setSpacing(12)
        self.engine_hint = label("", "Hint", 0.3)
        hint_row.addWidget(self.engine_hint)
        self.key_link = button("设置", "Link")
        self.key_link.clicked.connect(self._setup_cloud)
        hint_row.addWidget(self.key_link)
        hint_row.addStretch()
        grid.addLayout(hint_row, 2, 0)
        grid.addWidget(label("每次都可以换一个文件夹", "Hint", 0.3), 2, 1)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        col.addLayout(grid)

        col.addStretch(3)
        bottom = QHBoxLayout()
        self.privacy = label("", "Footer", 1)
        bottom.addWidget(self.privacy, 0, Qt.AlignmentFlag.AlignBottom)
        bottom.addStretch()
        self.start_btn = button("开始誊写　→", "Primary")
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._start)
        bottom.addWidget(self.start_btn)
        col.addLayout(bottom)
        return page

    def _work_page(self):
        page = QWidget(objectName="Page")
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        state_row = QHBoxLayout()
        state_row.setSpacing(8)
        self.spinner = Orbit(16, [(1.0, 1.6, -90)], body=2.2)  # turns while the job runs
        state_row.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        self.state_label = label("正在誊写", "FieldLabel", 2)
        state_row.addWidget(self.state_label, 0, Qt.AlignmentFlag.AlignVCenter)
        state_row.addStretch()
        col.addLayout(state_row)
        col.addSpacing(8)
        self.work_title = label("", "WorkTitle", wrap=True)
        col.addWidget(self.work_title)
        col.addSpacing(6)
        self.work_meta = label("", "WorkMeta", 0.5)
        col.addWidget(self.work_meta)
        col.addSpacing(26)
        self.stages = StageRow()
        col.addWidget(self.stages)
        col.addSpacing(6)

        nums = QHBoxLayout()
        self.percent = label("0", "Percent")
        self.percent.setTextFormat(Qt.TextFormat.RichText)  # so the % shares the number's baseline
        self._pct_shown: float | None = 0.0
        self._pct_anim = QVariantAnimation(self, duration=450)
        self._pct_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._pct_anim.valueChanged.connect(self._render_percent)
        nums.addWidget(self.percent, 0, Qt.AlignmentFlag.AlignBottom)
        nums.addStretch()
        self.detail = label("", "Detail", 0.3, wrap=True)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        nums.addWidget(self.detail, 1, Qt.AlignmentFlag.AlignBottom)
        col.addLayout(nums)
        col.addSpacing(14)
        self.bar = ProgressLine()
        col.addWidget(self.bar)
        col.addSpacing(26)

        col.addWidget(label("实时文稿", "FieldLabel", 2))
        col.addSpacing(10)
        self.transcript = TranscriptView()
        col.addWidget(self.transcript, 1)
        col.addSpacing(20)

        row = QHBoxLayout()
        row.setSpacing(12)
        self.stop_btn = button("停止", "Ghost")
        self.stop_btn.clicked.connect(self._stop)
        row.addWidget(self.stop_btn)
        self.awake_hint = label("可以切到别的窗口；合上盖子或让电脑睡眠会暂停誊写", "Footer", 0.5)
        self.awake_hint.setContentsMargins(10, 0, 0, 0)  # a margin, not a spacer, so it hides with the label
        row.addWidget(self.awake_hint)
        self.again_btn = button("再誊一集", "Link")
        self.again_btn.clicked.connect(self._back)
        row.addWidget(self.again_btn)
        row.addStretch()
        self.folder_btn = button("打开文件夹", "Ghost")
        self.folder_btn.clicked.connect(self._open_folder)
        row.addWidget(self.folder_btn)
        self.open_btn = button("打开文稿", "Primary")
        self.open_btn.clicked.connect(self._open_result)
        row.addWidget(self.open_btn)
        self.retry_btn = button("重试", "Primary")
        self.retry_btn.clicked.connect(self._start)
        row.addWidget(self.retry_btn)
        col.addLayout(row)
        return page

    # ── theme ──

    def palette_now(self) -> theme.Palette:
        if self.forced_palette:
            return self.forced_palette
        dark = QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        return theme.DARK if dark else theme.LIGHT

    def apply_theme(self):
        p = self.palette_now()
        app = QApplication.instance()
        qpal = app.palette()
        qpal.setColor(QPalette.ColorRole.PlaceholderText, QColor(p.ink3))  # style sheets cannot reach these
        qpal.setColor(QPalette.ColorRole.Link, QColor(p.accent))
        app.setPalette(qpal)
        app.setStyleSheet(theme.stylesheet(p))
        self.bar.set_colors(p.line, p.accent)
        self.deco.set_colors(p.line, p.accent, p.ink2)
        self.spinner.set_colors(p.line, p.accent)
        self.stages.set_palette(p)
        self.transcript.set_palette(p)
        if self.episode and self.card.isVisible():
            self.card.show_episode(self.episode, getattr(self, "cover", None), p)
        if self.isVisible():
            tint_titlebar(self, p)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        tint_titlebar(self, self.palette_now())

    # ── compose ──

    def _prefill_from_clipboard(self):
        # Not on a Mac: recent macOS asks the user each time an app reads the
        # clipboard on its own, which would greet every return to the window.
        if sys.platform == "darwin" or self.source.text() or self.stack.currentIndex() != 0:
            return
        text = QGuiApplication.clipboard().text()
        eid = core.find_episode_id(text)
        if eid and text != self.last_clip:  # once per copy, so clearing the field sticks
            self.last_clip = text
            self.source.setText(f"https://www.xiaoyuzhoufm.com/episode/{eid}")

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        if event.type() == event.Type.ActivationChange and self.isActiveWindow():
            self._prefill_from_clipboard()

    def _resolve(self):
        text = self.source.text().strip().strip('"')
        self.token += 1
        self.episode = None
        self.start_btn.setEnabled(False)
        self.card.hide()
        if not text:
            self._hint("支持 MP3、M4A、WAV 等格式 · 也可以直接把文件拖进窗口")
            return
        if not core.find_episode_id(text) and not Path(text).is_file():
            self._hint("请贴入小宇宙单集链接，或选择一个本地音频文件。", error=True)
            return
        self._hint("正在读取节目信息…")
        self.resolver.resolve(self.token, text)

    def _resolved(self, token: int, ep: core.Episode, cover: bytes | None):
        if token != self.token:
            return
        self.episode, self.cover = ep, cover
        self.source_hint.hide()
        self.card.show_episode(ep, cover, self.palette_now())
        self.card.show()
        self.start_btn.setEnabled(True)

    def _resolve_failed(self, token: int, message: str):
        if token == self.token:
            self._hint(message, error=True)

    def _hint(self, text: str, error: bool = False):
        self.source_hint.setText(text)
        self.source_hint.setObjectName("Error" if error else "Hint")
        self.source_hint.style().unpolish(self.source_hint)
        self.source_hint.style().polish(self.source_hint)
        self.source_hint.show()

    def _pick_file(self):
        exts = " ".join(f"*{e}" for e in sorted(core.AUDIO_EXTS))
        path, _ = QFileDialog.getOpenFileName(self, "选择音频", self.settings.get("last_audio_dir", ""), f"音频 ({exts})")
        if path:
            self.settings["last_audio_dir"] = str(Path(path).parent)
            core.save_settings(self.settings)
            self.source.setText(str(Path(path)))

    def _pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择保存位置", str(self.out_dir))
        if folder:
            self.out_dir = Path(folder)
            self.out_label.setText(str(self.out_dir))
            self.settings["output"] = str(self.out_dir)
            core.save_settings(self.settings)

    def _set_engine(self, key: str):
        self.engine = key
        self.settings["engine"] = key
        core.save_settings(self.settings)
        self._update_engine_hint()

    def _update_engine_hint(self):
        spec = core.ENGINES[self.engine]
        if self.engine == core.CLOUD:
            ready = cloud.order(self.settings)
            if ready:
                via = ready[0].name + (f"，{ready[1].name}备用" if len(ready) > 1 else "")
                self.engine_hint.setText(f"{spec.note} · {via}")
                self.key_link.setText("更改")
            else:
                self.engine_hint.setText(f"{spec.note} · 需要一个免费密钥")
                self.key_link.setText("设置密钥")
            self.key_link.show()
            self.privacy.setText(f"云端识别 · 音频会压缩后上传到 {ready[0].name if ready else '云端服务'} 处理")
        else:
            self.engine_hint.setText(spec.note + (" · 已就绪" if local.ready() else ""))
            self.key_link.hide()
            self.privacy.setText("在本机离线识别 · 音频不会上传")

    def _setup_cloud(self, welcome: bool = False) -> bool:
        """Open the cloud setup; True once a working key is saved."""
        result = SetupDialog(self, self.settings, welcome).exec()
        if result == SetupDialog.USE_LOCAL:
            self.engines.buttons[core.LOCAL].setChecked(True)
            self._set_engine(core.LOCAL)
            return False
        if result:
            core.save_settings(self.settings)
        self._update_engine_hint()
        return bool(result)

    def _welcome(self):
        self.settings["welcomed"] = True
        core.save_settings(self.settings)
        self._setup_cloud(welcome=True)

    # drag and drop
    def _dropped(self, mime) -> str | None:
        for url in mime.urls():
            if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in core.AUDIO_EXTS:
                return url.toLocalFile()
        if mime.hasText() and core.find_episode_id(mime.text()):
            return mime.text().strip()
        return None

    def _set_drop_look(self, on: bool):
        self.source.setProperty("drop", on)
        self.source.style().unpolish(self.source)
        self.source.style().polish(self.source)

    def dragEnterEvent(self, event):  # noqa: N802
        if self.stack.currentIndex() == 0 and self._dropped(event.mimeData()):
            event.acceptProposedAction()
            self._set_drop_look(True)

    def dragLeaveEvent(self, event):  # noqa: N802
        self._set_drop_look(False)

    def dropEvent(self, event):  # noqa: N802
        self._set_drop_look(False)
        value = self._dropped(event.mimeData())
        if value:
            self.source.setText(str(Path(value)) if not core.find_episode_id(value) else value)
            event.acceptProposedAction()

    # ── job ──

    def _start_if_ready(self):
        if self.start_btn.isEnabled():
            self._start()

    def _start(self):
        if not self.episode or (self.job and self.job.isRunning()):
            return
        if self.engine == core.CLOUD and not cloud.order(self.settings) and not self._setup_cloud():
            return
        ep = self.episode
        self.result = None
        self.work_title.setText(ep.title)
        bits = [ep.podcast or ("本地音频" if ep.local_path else ""), ep.author]
        if ep.duration:
            bits.append(core.fmt_minutes(ep.duration))
        spec = core.ENGINES[self.engine]
        bits.append(spec.label)
        self.stages.set_names(spec.stages)
        self.work_meta.setText(" · ".join(b for b in bits if b))
        self.transcript.reset()
        self.bar.reset()
        self._set_percent(0.0, instant=True)
        self.detail.setObjectName("Detail")
        self._repolish(self.detail)
        self.detail.setText("")
        self.stages.set_stage(0)
        self._show_state("running")
        self._switch(1)

        self.job = JobThread(ep, self.engine, self.out_dir, self, self.settings, self.clip_seconds)
        self.job.staged.connect(self.stages.set_stage)
        self.job.progressed.connect(self._progress)
        self.job.segmented.connect(self.transcript.add_segment)
        self.job.succeeded.connect(self._succeeded)
        self.job.failed.connect(self._failed)
        self.job.cancelled.connect(self._cancelled)
        self.job.start()

    def _progress(self, fraction: float, detail: str):
        self.bar.set_value(None if fraction < 0 else fraction)
        self._set_percent(None if fraction < 0 else fraction)
        self.detail.setText(detail)

    def _set_percent(self, fraction: float | None, instant: bool = False):
        """Roll the big number to its new value rather than jumping."""
        if fraction is None:  # a step with no measure, such as loading the model
            self._pct_anim.stop()
            self._pct_shown = None
            self.percent.setText("—")
            return
        target = fraction * 100
        running = self._pct_anim.state() == QVariantAnimation.State.Running
        if instant or not ANIMATE or self._pct_shown is None:
            self._pct_anim.stop()
            self._render_percent(target)
        elif not (running and self._pct_anim.endValue() == target):  # already rolling there
            self._pct_anim.stop()
            self._pct_anim.setStartValue(float(self._pct_shown))
            self._pct_anim.setEndValue(float(target))
            self._pct_anim.start()
        if self.job and self.job.isRunning():
            self.setWindowTitle(f"{int(fraction * 100)}% · 逐字")

    def _render_percent(self, value: float):
        self._pct_shown = value
        sign = f'<span style="font-size:22px; color:{self.palette_now().ink3}">&thinsp;%</span>'
        self.percent.setText(f"{int(value)}{sign}")

    def _succeeded(self, result: core.Result):
        self.result = result
        self.setWindowTitle("逐字")
        self.transcript.close_paragraph()
        self._update_engine_hint()
        if result.stopped_at is not None:
            self.stages.set_stage(2)
            self._show_state("stopped")
            self.detail.setText(f"已保存前 {core.fmt_ts(result.stopped_at)} 的内容 · {result.path.name}")
        else:
            self.stages.set_stage(2, finished=True)
            self.bar.set_value(1.0)
            self._set_percent(1.0)
            self._show_state("done")
            self.detail.setText(f"共 {len(self.transcript.paragraphs.groups)} 段 · 已保存为 {result.path.name}")
        if result.path.parent != self.out_dir:  # the chosen folder refused the write
            self.detail.setText(f"没有权限写入所选文件夹，已改存到 {result.path.parent}")
        QApplication.alert(self)

    def _failed(self, message: str):
        self.setWindowTitle("逐字")
        self.bar.set_value(0.0)
        self._show_state("failed")
        self.detail.setObjectName("DetailError")
        self._repolish(self.detail)
        self.detail.setText(message)
        QApplication.alert(self)

    def _cancelled(self):
        self.setWindowTitle("逐字")
        self._back()

    def _stop(self):
        if self.job and self.job.isRunning():
            self.job.cancel.set()
            self.stop_btn.setEnabled(False)
            self.detail.setText("正在停止…")

    def _back(self):
        self._update_engine_hint()
        self._switch(0)

    def _show_state(self, state: str):
        running = state == "running"
        self.state_label.setText(
            {"running": "正在誊写", "done": "已完成", "stopped": "已停止", "failed": "未能完成"}[state]
        )
        self.stop_btn.setVisible(running)
        self.stop_btn.setEnabled(running)
        self.awake_hint.setVisible(running)
        self.spinner.setVisible(running)
        self.again_btn.setVisible(not running)
        self.again_btn.setText("返回" if state == "failed" else "再誊一集")
        self.folder_btn.setVisible(state in ("done", "stopped"))
        self.open_btn.setVisible(state in ("done", "stopped"))
        self.retry_btn.setVisible(state == "failed")

    def _open_result(self):
        if self.result and self.result.path and self.result.path.exists():
            if sys.platform == "win32":
                os.startfile(self.result.path)
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.result.path)))

    def _open_folder(self):
        """Show the transcript selected in Explorer or Finder."""
        if self.result and self.result.path:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(self.result.path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(self.result.path)])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.result.path.parent)))

    # ── helpers ──

    def _switch(self, index: int):
        self.stack.setCurrentIndex(index)
        page = self.stack.currentWidget()
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", page)
        fade.setDuration(260)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.finished.connect(lambda: page.setGraphicsEffect(None))
        fade.start()

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self.compose and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            QTimer.singleShot(0, self._place_deco)  # after the layout has moved the headline
        return super().eventFilter(obj, event)

    def _place_deco(self):
        """Sit the orbit at the right edge, level with the headline."""
        head = self.headline.geometry()
        self.deco.move(
            self.compose.width() - self.deco.width(), head.center().y() - self.deco.height() // 2 + 14
        )

    @staticmethod
    def _repolish(w: QWidget):
        w.style().unpolish(w)
        w.style().polish(w)

    def closeEvent(self, event):  # noqa: N802
        if self.job and self.job.isRunning():
            box = QMessageBox(self)
            box.setWindowTitle("逐字")
            box.setText("正在誊写，确定要退出吗？\n已识别的部分会保存下来。")
            keep = box.addButton("继续誊写", QMessageBox.ButtonRole.RejectRole)
            box.addButton("停止并退出", QMessageBox.ButtonRole.AcceptRole)
            box.setDefaultButton(keep)
            box.exec()
            if box.clickedButton() is keep:
                event.ignore()
                return
            self.job.cancel.set()
            if not self.job.wait(15000):  # mid-way through a model call
                log.warning("job did not stop in time; exiting anyway")
                os._exit(0)
        event.accept()


# ── Screenshots, for checking the design ────────────────────────────────


def screenshots(folder: Path) -> int:
    """Render every screen with a made-up episode: these images get published,
    so they show no one else's show, words or cover art."""
    from datetime import date

    from PySide6.QtCore import QEventLoop

    folder.mkdir(parents=True, exist_ok=True)

    def settle(ms=400):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    sample = [
        core.Segment(61.0, 64.2, "欢迎来到新一期的节目。"),
        core.Segment(64.2, 69.0, "今天想和你聊聊，声音和文字之间到底隔着什么"),
        core.Segment(69.0, 74.5, "我们每天听很多播客，可真正留下来的，往往只有几句话。"),
        core.Segment(74.5, 80.0, "所以我开始把听过的节目誊成文字，慢慢读，慢慢想。"),
        core.Segment(80.0, 86.0, "文字让声音停了下来，也让我们有机会回头看看，自己究竟听到了什么。"),
        core.Segment(89.0, 93.5, "这一期，我们就从一个很小的问题开始。"),
        core.Segment(93.5, 99.0, "为什么有些话，听的时候不觉得，读出来却让人愣住"),
        core.Segment(99.0, 104.0, "也许是因为，读的时候，我们终于有了停顿。"),
    ]
    ep = core.Episode(
        title="第 12 期｜声音停下来的地方",
        source="https://www.xiaoyuzhoufm.com/episode/0123456789abcdef",
        podcast="示例播客",
        author="主播",
        duration=3725,
        published=date(2026, 9, 12),
    )
    cover = None  # the waveform stand-in
    for name, pal in (("light", theme.LIGHT), ("dark", theme.DARK)):
        win = MainWindow(palette=pal, welcome=False)
        # Screenshots get published; show no real paths.
        win.out_label.setText("/Users/me/Documents/逐字稿" if sys.platform == "darwin" else r"C:\Users\me\Documents\逐字稿")
        win.show()
        settle()
        win.grab().save(str(folder / f"{name}-1-empty.png"))
        win.source.blockSignals(True)
        win.source.setText(ep.source)
        win.source.blockSignals(False)
        win._resolved(win.token, ep, cover)
        settle()
        win.grab().save(str(folder / f"{name}-2-ready.png"))
        dialog = SetupDialog(win, {}, welcome=True)  # an empty settings copy: shows the first-run state
        dialog.show()
        settle(2500)  # let the network probe report
        dialog.grab().save(str(folder / f"{name}-2b-setup.png"))
        dialog.close()
        win.work_title.setText(ep.title)  # stage the work page without running a job
        win.work_meta.setText("示例播客 · 主播 · 1 小时 2 分 · 云端极速")
        win.stages.set_names(core.ENGINES[core.CLOUD].stages)
        win._show_state("running")
        win._switch(1)
        win.stages.set_stage(2)
        for seg in sample:
            win.transcript.add_segment(seg)
        win._progress(0.375, "云端已识别 3 / 8 段")
        settle(900)
        win.grab().save(str(folder / f"{name}-3-working.png"))
        win.stages.set_stage(2, finished=True)
        win._progress(1.0, "共 96 段 · 已保存为 第 12 期｜声音停下来的地方-逐字稿.md")
        win._show_state("done")
        settle(900)
        win.grab().save(str(folder / f"{name}-4-done.png"))
        win.close()
    return 0


def uitest(source: str, clip: float, folder: Path) -> int:
    """Drive the real window: paste a link, press start, and record the outcome."""
    folder.mkdir(parents=True, exist_ok=True)
    report: dict = {"source": source, "clip": clip}
    win = MainWindow(welcome=False)
    win.out_dir = folder  # for this run only; settings are untouched
    win.clip_seconds = clip or None
    win.show()
    started = time.monotonic()
    shots = {"n": 0}

    def finish(state: str):
        report["state"] = state
        report["seconds"] = round(time.monotonic() - started, 1)
        report["detail"] = win.detail.text()
        report["paragraphs"] = len(win.transcript.paragraphs.groups)
        if win.result and win.result.path:
            report["path"] = str(win.result.path)

        def save():
            win.grab().save(str(folder / "ui-3-final.png"))
            (folder / "uitest-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
            QApplication.instance().quit()

        QTimer.singleShot(1200, save)

    def snapshot():
        if win.job and win.job.isRunning():
            shots["n"] += 1
            win.grab().save(str(folder / "ui-2-working.png"))

    def wait_ready():
        if win.start_btn.isEnabled():
            win.grab().save(str(folder / "ui-1-ready.png"))
            report["resolve_seconds"] = round(time.monotonic() - started, 1)
            win._start()
            win.job.succeeded.connect(lambda r: finish("stopped" if r.stopped_at is not None else "done"))
            win.job.failed.connect(lambda m: finish("failed"))
            win.job.cancelled.connect(lambda: finish("cancelled"))
            ticker.timeout.connect(snapshot)
            ticker.start(8000)
        elif time.monotonic() - started > 40:
            report["error"] = f"never became ready: {win.source_hint.text()}"
            finish("not-ready")
        else:
            QTimer.singleShot(250, wait_ready)

    ticker = QTimer(win)
    win.source.setText(source)
    QTimer.singleShot(250, wait_ready)
    return QApplication.instance().exec()


def main() -> int:
    parser = argparse.ArgumentParser(prog="Zhuzi")
    parser.add_argument("--selftest", metavar="SOURCE")
    parser.add_argument("--clip", type=float, default=0, help="only transcribe the first N seconds")
    parser.add_argument("--engine", default=core.DEFAULT_ENGINE, choices=list(core.ENGINES))
    parser.add_argument("--out", type=Path, default=core.DEFAULT_OUTPUT)
    parser.add_argument("--screenshot", metavar="DIR", type=Path)
    parser.add_argument("--uitest", metavar="SOURCE")
    args = parser.parse_args()

    if args.selftest:
        return selftest(args.selftest, args.clip, args.engine, args.out)

    setup_logging()
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Zhuzi.Transcriber")
    app = QApplication(sys.argv)
    app.setApplicationName("逐字")
    # Qt's own strings (the right-click menu in text fields) in Simplified Chinese.
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

    translator = QTranslator(app)
    if translator.load(QLocale("zh_CN"), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)
    app.setStyle("Fusion")
    font = app.font()
    font.setFamilies(theme.SANS)
    app.setFont(font)

    def excepthook(kind, value, tb):
        log.error("uncaught", exc_info=(kind, value, tb))

    sys.excepthook = excepthook

    if args.screenshot:
        return screenshots(args.screenshot)
    if args.uitest:
        return uitest(args.uitest, args.clip, args.out)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

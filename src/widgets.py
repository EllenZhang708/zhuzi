"""Small custom widgets: the progress line, stage row, episode card, the live
transcript, and the orbit motif."""
from __future__ import annotations

import math
import sys
import time

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPixmap,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

import core
from theme import MONO, SERIF, Palette


def animations_enabled() -> bool:
    """Honour Windows' Animation effects switch (Settings → Accessibility →
    Visual effects) and the Mac's Reduce motion; when motion is off, everything
    here holds still."""
    if sys.platform == "darwin":
        try:
            import mac

            return not mac.reduce_motion()
        except (OSError, AttributeError):
            return True
    if sys.platform != "win32":
        return True
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
    value = wintypes.BOOL(True)
    spi_getclientareaanimation = 0x1042
    if not user32.SystemParametersInfoW(spi_getclientareaanimation, 0, ctypes.byref(value), 0):
        return True
    return bool(value.value)


ANIMATE = animations_enabled()


def mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


def spaced(widget: QWidget, px: float) -> QWidget:
    """Letter-spacing, which style sheets cannot set."""
    font = widget.font()
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, px)
    widget.setFont(font)
    return widget


def label(text: str, name: str, spacing: float = 0, wrap: bool = False) -> QLabel:
    w = QLabel(text)
    w.setObjectName(name)
    w.setWordWrap(wrap)
    w.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    if spacing:
        spaced(w, spacing)
    return w


def button(text: str, name: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName(name)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class ElidedLabel(QLabel):
    """Shows as much of a long path as fits, eliding the middle."""

    def __init__(self, text: str = "", name: str = ""):
        super().__init__()
        self.setObjectName(name)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def sizeHint(self):  # noqa: N802
        hint = super().sizeHint()
        hint.setWidth(self.fontMetrics().horizontalAdvance(self._full) + 2)
        return hint

    def minimumSizeHint(self):  # noqa: N802
        hint = super().minimumSizeHint()
        hint.setWidth(60)
        return hint

    def setText(self, text: str) -> None:  # noqa: N802 (Qt naming)
        self._full = text
        self.setToolTip(text)
        self.updateGeometry()
        self._elide()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        super().setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideMiddle, max(self.width(), 40)))


class Segmented(QFrame):
    """A row of mutually exclusive options in a rounded tray."""

    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], current: str):
        super().__init__()
        self.setObjectName("SegGroup")
        row = QHBoxLayout(self)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(2)
        self.group = QButtonGroup(self)
        self.buttons: dict[str, QPushButton] = {}
        for key, text in options:
            b = button(text, "Seg")
            b.setCheckable(True)
            b.setChecked(key == current)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self.group.addButton(b)
            self.buttons[key] = b
            row.addWidget(b)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


class ProgressLine(QWidget):
    """A hairline progress bar; with no value it shows a gliding segment."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(3)
        self._value = 0.0
        self._shown = 0.0
        self._phase = 0.0
        self._track = QColor("#ddd")
        self._fill = QColor("#b33")
        self._busy = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=1600, loopCount=-1)
        self._busy.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._busy.valueChanged.connect(self._on_phase)
        self._ease = QVariantAnimation(self, duration=450)
        self._ease.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._ease.valueChanged.connect(self._on_ease)

    def set_colors(self, track: str, fill: str):
        self._track, self._fill = QColor(track), QColor(fill)
        self.update()

    def set_value(self, value: float | None):
        if value is None:
            if self._busy.state() != QVariantAnimation.State.Running:
                self._busy.start()
            return
        self._busy.stop()
        if abs(value - self._value) < 1e-4:
            return
        self._value = value
        self._ease.stop()
        self._ease.setStartValue(self._shown)
        self._ease.setEndValue(value)
        self._ease.start()

    def reset(self):
        self._busy.stop()
        self._ease.stop()
        self._value = self._shown = 0.0
        self.update()

    def _on_phase(self, v):
        self._phase = v
        self.update()

    def _on_ease(self, v):
        self._shown = v
        self.update()

    def paintEvent(self, _event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._track)
        p.drawRoundedRect(QRectF(0, h / 2 - 0.5, w, 1), 0.5, 0.5)
        p.setBrush(self._fill)
        if self._busy.state() == QVariantAnimation.State.Running:
            seg = w * 0.22
            x = -seg + (w + seg) * self._phase
            p.drawRoundedRect(QRectF(x, 0, seg, h), h / 2, h / 2)
        elif self._shown > 0:
            p.drawRoundedRect(QRectF(0, 0, max(w * self._shown, h), h), h / 2, h / 2)


class Orbit(QWidget):
    """Concentric orbits with small bodies travelling round them: a nod to a
    little universe, drawn as plain line art. Static when animations are off.

    rings: (radius as a fraction of the space, seconds per revolution or 0 for
    no body, starting angle in degrees)."""

    def __init__(self, size: int, rings: list[tuple[float, float, float]], body: float, core_dot: float = 0):
        super().__init__()
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.rings, self.body, self.core_dot = rings, body, core_dot
        self.ring_color, self.body_color, self.core_color = QColor("#ccc"), QColor("#36f"), QColor("#333")
        self._t0 = time.monotonic()
        self._timer = QTimer(self, interval=33)
        self._timer.timeout.connect(self.update)

    def set_colors(self, ring: str, body: str, core: str | None = None):
        self.ring_color, self.body_color = QColor(ring), QColor(body)
        self.core_color = QColor(core or body)
        self.update()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if ANIMATE:
            self._timer.start()

    def hideEvent(self, event):  # noqa: N802
        super().hideEvent(event)
        self._timer.stop()

    def paintEvent(self, _event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        space = self.width() / 2 - self.body - 1
        t = time.monotonic() - self._t0 if ANIMATE else 0.0
        p.setPen(self.ring_color)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for frac, _period, _start in self.rings:
            p.drawEllipse(c, space * frac, space * frac)
        p.setPen(Qt.PenStyle.NoPen)
        if self.core_dot:
            p.setBrush(self.core_color)
            p.drawEllipse(c, self.core_dot, self.core_dot)
        p.setBrush(self.body_color)
        for frac, period, start in self.rings:
            if period:
                a = math.radians(start + 360 * t / period)
                r = space * frac
                p.drawEllipse(QPointF(c.x() + r * math.cos(a), c.y() + r * math.sin(a)), self.body, self.body)


class StageRow(QWidget):
    """01 获取音频 · 02 准备模型 · 03 识别文字, marking where the job is."""

    def __init__(self):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(34)
        self.names = core.ENGINES[core.DEFAULT_ENGINE].stages
        self.labels = [QLabel() for _ in self.names]
        for w in self.labels:
            w.setTextFormat(Qt.TextFormat.RichText)
            row.addWidget(w)
        row.addStretch()
        self.palette_: Palette | None = None
        self.current = -1
        self.finished = False

    def set_palette(self, p: Palette):
        self.palette_ = p
        self._render()

    def set_names(self, names: tuple[str, ...]):
        self.names = names
        self._render()

    def set_stage(self, index: int, finished: bool = False):
        self.current, self.finished = index, finished
        self._render()

    def _render(self):
        p = self.palette_
        if not p:
            return
        mono = MONO[0]
        for i, (w, name) in enumerate(zip(self.labels, self.names)):
            done = i < self.current or self.finished
            active = i == self.current and not self.finished
            color = p.ink if active else (p.ink2 if done else p.ink3)
            mark = "✓" if done else ("●" if active else f"{i + 1:02d}")
            mark_color = p.accent if active else color
            weight = 600 if active else 400
            w.setText(
                f'<span style="font-family:{mono}; font-size:11px; color:{mark_color}">{mark}</span>'
                f'&nbsp;&nbsp;<span style="font-size:13px; color:{color}; font-weight:{weight}">{name}</span>'
            )


def rounded_pixmap(source: QPixmap, size: int, radius: float, dpr: float) -> QPixmap:
    out = QPixmap(round(size * dpr), round(size * dpr))
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    p.setClipPath(path)
    scaled = source.scaled(
        round(size * dpr),
        round(size * dpr),
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    scaled.setDevicePixelRatio(dpr)
    p.drawPixmap(0, 0, scaled)
    p.end()
    return out


def waveform_tile(size: int, p: Palette, dpr: float) -> QPixmap:
    """Stand-in cover for a local file: a few bars of a waveform."""
    out = QPixmap(round(size * dpr), round(size * dpr))
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(p.line_soft if not p.dark else p.line))
    painter.drawRoundedRect(QRectF(0, 0, size, size), 10, 10)
    painter.setBrush(QColor(p.ink2))
    heights = [0.28, 0.5, 0.72, 0.42, 0.6, 0.3]
    bar, gap = size * 0.07, size * 0.06
    x = (size - (len(heights) * bar + (len(heights) - 1) * gap)) / 2
    for h in heights:
        bh = size * h
        painter.drawRoundedRect(QRectF(x, (size - bh) / 2, bar, bh), bar / 2, bar / 2)
        x += bar + gap
    painter.end()
    return out


class EpisodeCard(QFrame):
    COVER = 60

    def __init__(self):
        super().__init__()
        self.setObjectName("Card")
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 14, 20, 14)
        row.setSpacing(16)
        self.cover = QLabel()
        self.cover.setFixedSize(self.COVER, self.COVER)
        row.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(3)
        self.meta = label("", "CardMeta", 0.5)
        self.title = label("", "CardTitle")
        self.detail = label("", "CardDetail")
        col.addStretch()
        col.addWidget(self.meta)
        col.addWidget(self.title)
        col.addWidget(self.detail)
        col.addStretch()
        row.addLayout(col, 1)

    def show_episode(self, ep: core.Episode, cover: bytes | None, p: Palette):
        dpr = self.devicePixelRatioF()
        pix = QPixmap()
        if cover and pix.loadFromData(cover):
            self.cover.setPixmap(rounded_pixmap(pix, self.COVER, 10, dpr))
        else:
            self.cover.setPixmap(waveform_tile(self.COVER, p, dpr))
        if ep.local_path:
            self.meta.setText("本地音频")
        else:
            self.meta.setText(" · ".join(x for x in (ep.podcast, ep.author) if x) or "小宇宙")
        self.title.setText(ep.title)
        bits = []
        if ep.duration:
            bits.append(core.fmt_minutes(ep.duration))
        if ep.published:
            bits.append(f"{ep.published.year} 年 {ep.published.month} 月 {ep.published.day} 日")
        if ep.local_path:
            bits.append(ep.local_path.suffix.lstrip(".").upper())
        self.detail.setText(" · ".join(bits))


class TranscriptView(QTextBrowser):
    """The transcript as it is written, gathered into paragraphs. New words
    surface faintly and darken into ink, like a print developing."""

    FADE = 0.8  # seconds

    def __init__(self):
        super().__init__()
        self.setObjectName("Transcript")
        self.setReadOnly(True)
        self.setOpenLinks(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.document().setDocumentMargin(2)
        self.palette_: Palette | None = None
        self._fading: list[tuple[int, int, float]] = []  # (start, end, born)
        self._fade_timer = QTimer(self, interval=30)
        self._fade_timer.timeout.connect(self._fade_step)
        self.reset()

    def reset(self):
        self._settle()
        self.clear()
        self.paragraphs = core.Paragraphs()
        self._last: str | None = None

    def set_palette(self, p: Palette):
        self.palette_ = p
        self._text_fmt = QTextCharFormat()
        serif = QFont()
        serif.setFamilies(SERIF)
        serif.setPixelSize(15)
        self._text_fmt.setFont(serif)
        self._text_fmt.setForeground(QColor(p.ink))
        self._ts_fmt = QTextCharFormat()
        mono = QFont()
        mono.setFamilies(MONO)
        mono.setPixelSize(11)
        self._ts_fmt.setFont(mono)
        self._ts_fmt.setForeground(QColor(p.ink3))
        self._block_fmt = QTextBlockFormat()
        self._block_fmt.setLineHeight(165, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        self._block_fmt.setBottomMargin(16)
        self._ink = QColor(p.ink)
        self._faint = mix(QColor(p.ink), QColor(p.surface), 0.85)
        # Replay what is already shown in the new colors.
        groups = self.paragraphs.groups if hasattr(self, "paragraphs") else []
        self.reset()
        for group in groups:
            for seg in group:
                self.add_segment(seg, animate=False)

    def add_segment(self, seg: core.Segment, animate: bool = True):
        bar = self.verticalScrollBar()
        follow = bar.value() >= bar.maximum() - 24
        opens = self.paragraphs.add(seg)
        cur = QTextCursor(self.document())
        cur.movePosition(QTextCursor.MoveOperation.End)
        if opens:
            if self._last is None:
                cur.setBlockFormat(self._block_fmt)
            else:
                cur.insertText(core.joint(self._last, last=True), self._text_fmt)
                cur.insertBlock(self._block_fmt)
            cur.insertText(core.fmt_ts(seg.start) + " ", self._ts_fmt)
            text = seg.text
        else:
            text = core.joint(self._last, last=False) + seg.text
        if animate and ANIMATE:
            fmt = QTextCharFormat(self._text_fmt)
            fmt.setForeground(self._faint)
            start = cur.position()
            cur.insertText(text, fmt)
            self._fading.append((start, cur.position(), time.monotonic()))
            self._fade_timer.start()
        else:
            cur.insertText(text, self._text_fmt)
        self._last = seg.text
        if follow:
            bar.setValue(bar.maximum())

    def _fade_step(self):
        now = time.monotonic()
        still = []
        for start, end, born in self._fading:
            k = min((now - born) / self.FADE, 1.0)
            self._recolor(start, end, mix(self._faint, self._ink, 1 - (1 - k) ** 3))
            if k < 1:
                still.append((start, end, born))
        self._fading = still
        if not still:
            self._fade_timer.stop()

    def _recolor(self, start: int, end: int, color: QColor):
        cur = QTextCursor(self.document())
        cur.setPosition(start)
        cur.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        fmt = QTextCharFormat()
        fmt.setForeground(color)
        cur.mergeCharFormat(fmt)

    def _settle(self):
        """Finish any fades at once (before the text is edited or cleared)."""
        for start, end, _born in self._fading:
            self._recolor(start, end, self._ink)
        self._fading = []
        self._fade_timer.stop()

    def close_paragraph(self):
        """End the last paragraph the way the Markdown file does."""
        if self._last is None:
            return
        self._settle()
        cur = QTextCursor(self.document())
        cur.movePosition(QTextCursor.MoveOperation.End)
        if self._last.endswith(("，", "、", ",")):
            cur.deletePreviousChar()
            self._last = self._last[:-1]
        cur.insertText(core.joint(self._last, last=True), self._text_fmt)

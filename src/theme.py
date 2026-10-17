"""Colors, type and the style sheet. Neutral paper and ink, with one ultramarine
accent taken (a shade deeper) from the app icon, used sparingly."""
from __future__ import annotations

from dataclasses import dataclass

SERIF = ["Noto Serif SC", "Source Han Serif SC", "SimSun"]
SANS = ["Microsoft YaHei UI", "Segoe UI"]
MONO = ["Consolas", "Cascadia Mono"]


@dataclass(frozen=True)
class Palette:
    dark: bool
    bg: str
    surface: str
    ink: str
    ink2: str
    ink3: str
    line: str
    line_soft: str
    accent: str
    selection: str
    button: str
    button_text: str
    button_hover: str
    button_off: str
    button_off_text: str


LIGHT = Palette(
    dark=False,
    bg="#F5F4F0",
    surface="#FCFCFA",
    ink="#15171C",
    ink2="#4E525B",
    ink3="#80848D",
    line="#DBDBD6",
    line_soft="#E9E9E4",
    accent="#2A5BDB",
    selection="#DCE4F8",
    button="#15171C",
    button_text="#F5F4F0",
    button_hover="#30333B",
    button_off="#DDDDD8",
    button_off_text="#9599A1",
)

DARK = Palette(
    dark=True,
    bg="#111318",
    surface="#181B21",
    ink="#ECEEF2",
    ink2="#A9AEB8",
    ink3="#757B86",
    line="#2B2F37",
    line_soft="#20232A",
    accent="#7C9BFF",
    selection="#2A3656",
    button="#ECEEF2",
    button_text="#111318",
    button_hover="#FFFFFF",
    button_off="#262A31",
    button_off_text="#666C77",
)


def _families(names: list[str]) -> str:
    return ", ".join(f'"{n}"' for n in names)


def stylesheet(p: Palette) -> str:
    serif, sans, mono = _families(SERIF), _families(SANS), _families(MONO)
    return f"""
    QWidget {{ color: {p.ink}; font-family: {sans}; font-size: 14px; }}
    QWidget#Root, QStackedWidget, QWidget#Page {{ background: {p.bg}; }}

    QLabel#Wordmark {{ font-family: {serif}; font-size: 19px; font-weight: 600; }}
    QLabel#Kicker {{ color: {p.ink3}; font-size: 10px; }}
    QLabel#Headline {{ font-family: {serif}; font-size: 36px; font-weight: 600; }}
    QLabel#Lede {{ color: {p.ink2}; font-size: 14px; }}
    QLabel#FieldLabel {{ color: {p.ink3}; font-size: 11px; }}
    QLabel#Hint {{ color: {p.ink3}; font-size: 12px; }}
    QLabel#Error {{ color: {p.accent}; font-size: 13px; }}
    QLabel#Path {{ color: {p.ink}; font-size: 13px; }}
    QLabel#Footer {{ color: {p.ink3}; font-size: 11px; }}

    QLineEdit#Source {{
        background: transparent; border: none; border-bottom: 1px solid {p.line};
        padding: 10px 0 12px 0; font-size: 16px;
        selection-background-color: {p.selection}; selection-color: {p.ink};
    }}
    QLineEdit#Source:focus {{ border-bottom: 1px solid {p.ink}; }}
    QLineEdit#Source[drop="true"] {{ border-bottom: 1px solid {p.accent}; }}

    QPushButton {{ outline: none; }}
    QPushButton#Link {{ background: transparent; border: none; color: {p.ink2}; padding: 4px 0; font-size: 13px; }}
    QPushButton#Link:hover {{ color: {p.ink}; text-decoration: underline; }}
    QPushButton#Primary {{
        background: {p.button}; color: {p.button_text}; border: none; border-radius: 10px;
        padding: 13px 28px; font-size: 14px; font-weight: 600;
    }}
    QPushButton#Primary:hover {{ background: {p.button_hover}; }}
    QPushButton#Primary:disabled {{ background: {p.button_off}; color: {p.button_off_text}; }}
    QPushButton#Ghost {{
        background: transparent; color: {p.ink}; border: 1px solid {p.line}; border-radius: 10px;
        padding: 12px 22px; font-size: 14px;
    }}
    QPushButton#Ghost:hover {{ border-color: {p.ink3}; }}

    QFrame#SegGroup {{ background: {p.surface}; border: 1px solid {p.line}; border-radius: 10px; }}
    QPushButton#Seg {{
        background: transparent; border: none; border-radius: 7px; color: {p.ink2};
        padding: 7px 20px; font-size: 13px;
    }}
    QPushButton#Seg:hover {{ color: {p.ink}; }}
    QPushButton#Seg:checked {{ background: {p.button}; color: {p.button_text}; }}

    QFrame#Card {{ background: {p.surface}; border: 1px solid {p.line_soft}; border-radius: 14px; }}
    QFrame#Card QLabel {{ background: transparent; }}
    QLabel#CardMeta {{ color: {p.ink3}; font-size: 12px; }}
    QLabel#CardTitle {{ font-family: {serif}; font-size: 19px; font-weight: 600; }}
    QLabel#CardDetail {{ color: {p.ink3}; font-size: 12px; }}

    QLabel#WorkTitle {{ font-family: {serif}; font-size: 26px; font-weight: 600; }}
    QLabel#WorkMeta {{ color: {p.ink3}; font-size: 12px; }}
    QLabel#Percent {{ font-family: {serif}; font-size: 64px; font-weight: 300; }}
    QLabel#Detail {{ color: {p.ink2}; font-size: 13px; }}
    QLabel#DetailError {{ color: {p.accent}; font-size: 13px; }}

    QTextBrowser#Transcript {{
        background: {p.surface}; border: 1px solid {p.line_soft}; border-radius: 14px;
        padding: 16px 10px 16px 22px;
        selection-background-color: {p.selection}; selection-color: {p.ink};
    }}

    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 8px 2px 8px 0; }}
    QScrollBar::handle:vertical {{ background: {p.line}; border-radius: 4px; min-height: 40px; }}
    QScrollBar::handle:vertical:hover {{ background: {p.ink3}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

    QDialog#KeyDialog {{ background: {p.bg}; }}
    QLabel#DialogTitle {{ font-family: {serif}; font-size: 22px; font-weight: 600; }}
    QLabel#DialogBody {{ color: {p.ink2}; font-size: 13px; }}
    QLineEdit#KeyField {{
        background: {p.surface}; border: 1px solid {p.line}; border-radius: 8px;
        padding: 10px 12px; font-family: {mono}; font-size: 13px;
        selection-background-color: {p.selection}; selection-color: {p.ink};
    }}
    QLineEdit#KeyField:focus {{ border-color: {p.ink3}; }}

    QMessageBox {{ background: {p.bg}; }}
    QMessageBox QLabel {{ color: {p.ink}; font-size: 14px; }}
    QMessageBox QPushButton {{
        background: transparent; color: {p.ink}; border: 1px solid {p.line}; border-radius: 8px;
        padding: 7px 18px; min-width: 64px;
    }}
    QMessageBox QPushButton:default {{ background: {p.button}; color: {p.button_text}; border: none; }}
    QToolTip {{ background: {p.ink}; color: {p.bg}; border: none; padding: 6px 8px; }}
    """

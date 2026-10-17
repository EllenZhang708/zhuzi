"""Keep API keys encrypted at rest with Windows DPAPI.

A protected value can only be decrypted by the same Windows user on the same
machine, so a copied settings file reveals nothing. Elsewhere (macOS, Linux)
values are stored as-is, marked so they are not mistaken for ciphertext.
"""
from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes

_DPAPI, _PLAIN = "dpapi:", "plain:"


class _Blob(ctypes.Structure):  # DATA_BLOB
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _api():
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32")
    sig = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
           wintypes.DWORD, ctypes.POINTER(_Blob)]
    crypt32.CryptProtectData.argtypes = sig
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p] + sig[2:]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    return crypt32, kernel32


def _run(fn, data: bytes, *extra) -> bytes:
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _Blob()
    if not fn(ctypes.byref(blob_in), *extra, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError(ctypes.get_last_error(), "DPAPI call failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        _api()[1].LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def protect(text: str) -> str:
    if sys.platform != "win32":
        return _PLAIN + text
    crypt32, _ = _api()
    return _DPAPI + base64.b64encode(_run(crypt32.CryptProtectData, text.encode("utf-8"), "Zhuzi")).decode()


def unprotect(stored: str) -> str:
    """The plain value, or "" if it cannot be read (another machine or user)."""
    if not stored:
        return ""
    if stored.startswith(_PLAIN):
        return stored[len(_PLAIN) :]
    if stored.startswith(_DPAPI) and sys.platform == "win32":
        crypt32, _ = _api()
        try:
            return _run(crypt32.CryptUnprotectData, base64.b64decode(stored[len(_DPAPI) :]), None).decode("utf-8")
        except (OSError, ValueError):
            return ""
    return ""

"""Keep API keys out of the settings file in plain text.

Windows: encrypted with DPAPI, so only the same Windows user on the same
machine can decrypt them; a copied settings file reveals nothing.
macOS: kept in the login Keychain, and the settings file only names the item.
Items are written and read through /usr/bin/security, so the Keychain trusts
that Apple-signed tool rather than this app, whose ad-hoc signature changes
with every release and would otherwise bring a Keychain prompt after each update.
Elsewhere, or if the Keychain cannot be used, values are stored as-is, marked
so they are not mistaken for ciphertext.
"""
from __future__ import annotations

import base64
import logging
import subprocess
import sys

log = logging.getLogger("zhuzi")

_DPAPI, _KEYCHAIN, _PLAIN = "dpapi:", "keychain:", "plain:"
SERVICE = "Zhuzi"  # the Keychain item's service name; tests use their own
SECURITY = "/usr/bin/security"

if sys.platform == "win32":
    WHERE = "密钥加密保存在本机，只有你这个 Windows 账户能读取。"
elif sys.platform == "darwin":
    WHERE = "密钥保存在这台 Mac 的「钥匙串」里，只有你的账户能读取。"
else:
    WHERE = "密钥保存在本机的设置文件里。"


# ── Windows: DPAPI ──────────────────────────────────────────────────────


def _dpapi(protect: bool, data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):  # DATA_BLOB
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32")
    tail = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR if protect else ctypes.c_void_p] + tail
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = Blob()
    if not fn(ctypes.byref(blob_in), "Zhuzi" if protect else None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError(ctypes.get_last_error(), "DPAPI call failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


# ── macOS: Keychain ─────────────────────────────────────────────────────

_cache: dict[str, str] = {}  # account -> secret; each read is a process launch


def _security(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    # UTF-8 spelled out: an app opened from Finder may start with no locale set.
    return subprocess.run(
        [SECURITY, *args], input=stdin, capture_output=True, encoding="utf-8", errors="replace", timeout=20
    )


def _keychain_set(account: str, secret: str) -> None:
    # Through `security -i` on stdin, so the secret never appears in a process list.
    quoted = '"' + secret.replace("\\", "\\\\").replace('"', '\\"') + '"'
    command = f'add-generic-password -U -s {SERVICE} -a {account} -l "逐字 · {account}" -w {quoted}\n'
    result = _security("-i", stdin=command)
    if result.returncode or result.stderr.strip():
        raise OSError(f"security add-generic-password failed: {result.stderr.strip()[:200]}")
    _cache[account] = secret


def _keychain_get(account: str) -> str:
    if account not in _cache:
        result = _security("find-generic-password", "-s", SERVICE, "-a", account, "-w")
        if result.returncode:
            return ""  # not found (44), or the user declined to unlock the Keychain
        _cache[account] = result.stdout.rstrip("\n")
    return _cache[account]


def _keychain_delete(account: str) -> None:
    _cache.pop(account, None)
    _security("delete-generic-password", "-s", SERVICE, "-a", account)


# ── The interface ───────────────────────────────────────────────────────


def protect(name: str, text: str) -> str:
    """Store `text` (the key for `name`) and return what the settings file keeps."""
    if sys.platform == "win32":
        return _DPAPI + base64.b64encode(_dpapi(True, text.encode("utf-8"))).decode()
    if sys.platform == "darwin":
        try:
            _keychain_set(name, text)
            return _KEYCHAIN + name
        except (OSError, subprocess.SubprocessError):
            log.warning("Keychain unavailable; keeping the key in the settings file", exc_info=True)
    return _PLAIN + text


def unprotect(stored: str) -> str:
    """The plain value, or "" if it cannot be read (another machine or user)."""
    if not stored:
        return ""
    if stored.startswith(_PLAIN):
        return stored[len(_PLAIN) :]
    if stored.startswith(_DPAPI) and sys.platform == "win32":
        try:
            return _dpapi(False, base64.b64decode(stored[len(_DPAPI) :])).decode("utf-8")
        except (OSError, ValueError):
            return ""
    if stored.startswith(_KEYCHAIN) and sys.platform == "darwin":
        try:
            return _keychain_get(stored[len(_KEYCHAIN) :])
        except (OSError, subprocess.SubprocessError):
            return ""
    return ""


def forget(stored: str) -> None:
    """Remove a stored value for good (only Keychain items live elsewhere)."""
    if stored.startswith(_KEYCHAIN) and sys.platform == "darwin":
        try:
            _keychain_delete(stored[len(_KEYCHAIN) :])
        except (OSError, subprocess.SubprocessError):
            log.warning("Could not delete the Keychain item", exc_info=True)

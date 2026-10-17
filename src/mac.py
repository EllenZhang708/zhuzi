"""The few macOS calls the app needs, made through the Objective-C runtime with
ctypes, so no PyObjC is bundled. Import only on macOS."""
from __future__ import annotations

import ctypes
from ctypes import c_bool, c_char_p, c_uint64, c_void_p

_objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
ctypes.CDLL("/System/Library/Frameworks/Foundation.framework/Foundation")  # registers NSProcessInfo, NSString
_objc.objc_getClass.restype = c_void_p
_objc.objc_getClass.argtypes = [c_char_p]
_objc.sel_registerName.restype = c_void_p
_objc.sel_registerName.argtypes = [c_char_p]
_objc.objc_autoreleasePoolPush.restype = c_void_p
_objc.objc_autoreleasePoolPop.argtypes = [c_void_p]


def _send(restype, *argtypes):
    """objc_msgSend with a declared signature. On Apple silicon it must be
    declared: called as a variadic function, it would read its arguments from
    the wrong place."""
    return ctypes.CFUNCTYPE(restype, c_void_p, c_void_p, *argtypes)(("objc_msgSend", _objc))


def _cls(name: str) -> int:
    return _objc.objc_getClass(name.encode())


def _sel(name: str) -> int:
    return _objc.sel_registerName(name.encode())


# NSActivityUserInitiated: user-requested work that should run at full speed.
# It includes NSActivityIdleSystemSleepDisabled, and an app holding it is not
# put into App Nap when its window is hidden.
_USER_INITIATED = 0x00FFFFFF | 1 << 20


def begin_activity(reason: str) -> int:
    """Start an activity; pass the token to end_activity when the work is done."""
    pool = _objc.objc_autoreleasePoolPush()
    try:
        info = _send(c_void_p)(_cls("NSProcessInfo"), _sel("processInfo"))
        text = _send(c_void_p, c_char_p)(_cls("NSString"), _sel("stringWithUTF8String:"), reason.encode())
        token = _send(c_void_p, c_uint64, c_void_p)(
            info, _sel("beginActivityWithOptions:reason:"), _USER_INITIATED, text
        )
        if not token:
            raise OSError("beginActivityWithOptions:reason: returned nil")
        return _send(c_void_p)(token, _sel("retain"))  # outlive the pool
    finally:
        _objc.objc_autoreleasePoolPop(pool)


def end_activity(token: int) -> None:
    info = _send(c_void_p)(_cls("NSProcessInfo"), _sel("processInfo"))
    _send(None, c_void_p)(info, _sel("endActivity:"), token)
    _send(None)(token, _sel("release"))


def reduce_motion() -> bool:
    """System Settings → Accessibility → Display → Reduce motion."""
    ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
    workspace = _send(c_void_p)(_cls("NSWorkspace"), _sel("sharedWorkspace"))
    return bool(_send(c_bool)(workspace, _sel("accessibilityDisplayShouldReduceMotion")))

"""Read-only native Windows prerequisites; no elevation or registry changes."""

import ctypes
import os
import platform
import struct
import sys


def facts():
    if sys.platform != "win32" or os.name != "nt":
        raise ValueError("Native Windows required; WSL2 is not acceptance")
    from ctypes import wintypes as w

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    a = ctypes.WinDLL("advapi32", use_last_error=True)
    n = ctypes.WinDLL("ntdll")
    wine = hasattr(n, "wine_get_version")
    k.GetCurrentProcess.restype = w.HANDLE
    k.IsWow64Process2.argtypes = [
        w.HANDLE,
        ctypes.POINTER(w.WORD),
        ctypes.POINTER(w.WORD),
    ]
    k.IsWow64Process2.restype = w.BOOL
    process, native = w.WORD(), w.WORD()
    if not k.IsWow64Process2(
        k.GetCurrentProcess(), ctypes.byref(process), ctypes.byref(native)
    ):
        raise ValueError("Cannot determine native architecture")
    a.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    a.OpenProcessToken.restype = w.BOOL
    a.GetTokenInformation.argtypes = [
        w.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        w.DWORD,
        ctypes.POINTER(w.DWORD),
    ]
    a.GetTokenInformation.restype = w.BOOL
    a.ConvertStringSidToSidW.argtypes = [w.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    a.ConvertStringSidToSidW.restype = w.BOOL
    a.CheckTokenMembership.argtypes = [
        w.HANDLE,
        ctypes.c_void_p,
        ctypes.POINTER(w.BOOL),
    ]
    a.CheckTokenMembership.restype = w.BOOL
    k.CloseHandle.argtypes = [w.HANDLE]
    k.LocalFree.argtypes = [ctypes.c_void_p]
    token = w.HANDLE()
    sid = ctypes.c_void_p()
    if not a.OpenProcessToken(k.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ValueError("Cannot inspect test token")
    try:
        kind, elevated, size = w.DWORD(), w.DWORD(), w.DWORD()
        for item, buffer in [(18, kind), (20, elevated)]:
            if not a.GetTokenInformation(
                token,
                item,
                ctypes.byref(buffer),
                ctypes.sizeof(buffer),
                ctypes.byref(size),
            ):
                raise ValueError("Cannot inspect elevation")
        if not a.ConvertStringSidToSidW("S-1-5-32-544", ctypes.byref(sid)):
            raise ValueError("Cannot inspect admin membership")
        member = w.BOOL()
        if not a.CheckTokenMembership(None, sid, ctypes.byref(member)):
            raise ValueError("Cannot inspect admin membership")
        return {
            "schema": 1,
            "sysPlatform": "win32",
            "osName": "nt",
            "machine": platform.machine(),
            "nativeMachine": native.value,
            "processMachine": process.value,
            "pointerBits": struct.calcsize("P") * 8,
            "python": platform.python_version(),
            "windowsBuild": sys.getwindowsversion().build,
            "wine": wine,
            "adminMember": bool(member.value),
            "elevated": bool(elevated.value),
            "elevationType": kind.value,
            "standardUser": not member.value and not elevated.value and kind.value == 1,
        }
    finally:
        if sid.value:
            k.LocalFree(sid)
        k.CloseHandle(token)


def eligible(value):
    """Pure gate supports synthetic rejection tests, not simulated OS acceptance."""
    return (
        value.get("sysPlatform") == "win32"
        and value.get("osName") == "nt"
        and value.get("nativeMachine") == 0x8664
        and value.get("processMachine") == 0
        and value.get("pointerBits") == 64
        and value.get("python") == "3.14.10"
        and value.get("wine") is False
        and value.get("adminMember") is False
        and value.get("elevated") is False
        and value.get("elevationType") == 1
        and value.get("standardUser") is True
    )

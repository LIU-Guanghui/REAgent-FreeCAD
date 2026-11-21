"""Protect saved API keys with Windows current-user DPAPI; no plaintext fallback."""

import base64
import ctypes
import os
from ctypes import wintypes


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(value, address, *, decrypt):
    if os.name != "nt":
        raise ValueError("Protected API key storage requires Windows.")
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except OSError:
        raise ValueError("Windows key protection is unavailable. The API key has not been saved.") from None
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source = ctypes.create_string_buffer(value)
    entropy = ctypes.create_string_buffer(("REAgent API key\0" + address).encode("utf-8"))
    incoming = _Blob(len(value), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    extra = _Blob(len(entropy.raw) - 1, ctypes.cast(entropy, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = _Blob()
    try:
        # CRYPTPROTECT_UI_FORBIDDEN; deliberately omit LOCAL_MACHINE scope.
        if not function(ctypes.byref(incoming), None, ctypes.byref(extra), None, None, 1, ctypes.byref(outgoing)):
            raise ValueError("The saved API key could not be protected/opened for this Windows account. Re-enter it.")
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        ctypes.memset(source, 0, len(source))
        if outgoing.data:
            ctypes.memset(outgoing.data, 0, outgoing.size)
            kernel32.LocalFree(outgoing.data)


def protect_key(value, address):
    return base64.b64encode(_crypt(value.encode("ascii"), address, decrypt=False)).decode("ascii")


def open_key(value, address):
    try:
        return _crypt(base64.b64decode(value, validate=True), address, decrypt=True).decode("ascii")
    except (ValueError, UnicodeError):
        raise ValueError("The saved API key could not be opened for this Windows account. Re-enter it.") from None

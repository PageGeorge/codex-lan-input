import ctypes
import threading
import time
from ctypes import wintypes

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_RETURN = 0x0D
VK_V = 0x56
CLIPBOARD_RETRIES = 10
CLIPBOARD_RETRY_DELAY = 0.05
PASTE_DELAY_SECONDS = 0.03

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wintypes.BOOL
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
user32.SendInput.restype = wintypes.UINT

kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


class InjectionError(RuntimeError):
    pass


class BusyError(InjectionError):
    pass


_ACTION_LOCK = threading.Lock()


def paste_text(text: str):
    if not _ACTION_LOCK.acquire(blocking=False):
        raise BusyError("当前已有输入动作正在执行")

    try:
        _write_text_to_clipboard(text)
        time.sleep(PASTE_DELAY_SECONDS)
        _send_virtual_keys(
            _key_down(VK_CONTROL),
            _key_down(VK_V),
            _key_up(VK_V),
            _key_up(VK_CONTROL),
        )
    finally:
        _ACTION_LOCK.release()


def press_enter():
    if not _ACTION_LOCK.acquire(blocking=False):
        raise BusyError("当前已有输入动作正在执行")

    try:
        _send_virtual_keys(_key_down(VK_RETURN), _key_up(VK_RETURN))
    finally:
        _ACTION_LOCK.release()


def _write_text_to_clipboard(text: str):
    if not _open_clipboard_with_retry():
        raise InjectionError("无法打开系统剪贴板")

    memory_handle = None

    try:
        if not user32.EmptyClipboard():
            raise InjectionError("无法清空系统剪贴板")

        buffer = ctypes.create_unicode_buffer(text)
        buffer_size = ctypes.sizeof(buffer)

        memory_handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, buffer_size)
        if not memory_handle:
            raise InjectionError("无法为剪贴板分配内存")

        locked_memory = kernel32.GlobalLock(memory_handle)
        if not locked_memory:
            raise InjectionError("无法锁定剪贴板内存")

        try:
            ctypes.memmove(locked_memory, ctypes.addressof(buffer), buffer_size)
        finally:
            kernel32.GlobalUnlock(memory_handle)

        if not user32.SetClipboardData(CF_UNICODETEXT, memory_handle):
            raise InjectionError("无法写入剪贴板文本")

        memory_handle = None
    finally:
        if memory_handle:
            kernel32.GlobalFree(memory_handle)
        user32.CloseClipboard()


def _open_clipboard_with_retry():
    for _ in range(CLIPBOARD_RETRIES):
        if user32.OpenClipboard(None):
            return True
        time.sleep(CLIPBOARD_RETRY_DELAY)
    return False


def _send_virtual_keys(*inputs: INPUT):
    input_array = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(input_array), ctypes.byref(input_array), ctypes.sizeof(INPUT))
    if sent != len(input_array):
        error_code = ctypes.get_last_error()
        raise InjectionError(
            f"无法发送键盘输入事件 (Win32 错误码: {error_code})"
        )


def _key_down(virtual_key: int):
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=virtual_key))


def _key_up(virtual_key: int):
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=virtual_key, dwFlags=KEYEVENTF_KEYUP))

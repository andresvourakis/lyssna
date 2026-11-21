r"""The few Win32 calls the probe needs that lyssna/win does not offer: the Windows version, the
keyboard layout, the cursor, the window in front and its program, the no-activate window
style, a throwaway Run key value, and finding and filling a Notepad window.

Every DLL is loaded on first use, so this module imports on a Mac (the preview never calls it).
Each function returns plain values and raises on failure; the probe records the exception."""

from __future__ import annotations

import os
import subprocess
import sys
import time

_DLLS: dict = {}

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WM_SETTEXT = 0x000C
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DESKTOP_READOBJECTS = 0x0001
VK_RMENU = 0xA5

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
PROBE_RUN_VALUE = "LyssnaProbeTest"  # never the real "Lyssna" value


def _dlls():
    if _DLLS:
        return _DLLS
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    HWND = wintypes.HWND
    sigs = (
        (user32.GetForegroundWindow, [], HWND),
        (user32.GetClassNameW, [HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        (user32.GetWindowThreadProcessId, [HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        (user32.GetCursorPos, [ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
        (user32.GetKeyboardLayoutNameW, [wintypes.LPWSTR], wintypes.BOOL),
        (user32.GetKeyboardLayout, [wintypes.DWORD], ctypes.c_void_p),
        (user32.VkKeyScanExW, [wintypes.WCHAR, ctypes.c_void_p], ctypes.c_short),
        (user32.GetAsyncKeyState, [ctypes.c_int], ctypes.c_short),
        (user32.GetWindowLongPtrW, [HWND, ctypes.c_int], ctypes.c_ssize_t),
        (user32.SetWindowLongPtrW, [HWND, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t),
        (user32.GetWindowRect, [HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
        (user32.IsWindowVisible, [HWND], wintypes.BOOL),
        (user32.SetForegroundWindow, [HWND], wintypes.BOOL),
        (user32.FindWindowExW, [HWND, HWND, wintypes.LPCWSTR, wintypes.LPCWSTR], HWND),
        (user32.SendMessageW, [HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR], ctypes.c_ssize_t),
        (user32.OpenInputDesktop, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        (user32.CloseDesktop, [wintypes.HANDLE], wintypes.BOOL),
        (kernel32.OpenProcess, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        (kernel32.CloseHandle, [wintypes.HANDLE], wintypes.BOOL),
        (kernel32.QueryFullProcessImageNameW, [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                               ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        (kernel32.SetConsoleCtrlHandler, [ctypes.c_void_p, wintypes.BOOL], wintypes.BOOL),
        (kernel32.LCIDToLocaleName, [wintypes.DWORD, wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD], ctypes.c_int),
    )
    for fn, argtypes, restype in sigs:
        fn.argtypes, fn.restype = argtypes, restype
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [EnumProc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    _DLLS.update(ctypes=ctypes, wintypes=wintypes, user32=user32, kernel32=kernel32, ntdll=ntdll, EnumProc=EnumProc)
    return _DLLS


def last_error() -> int:
    return _dlls()["ctypes"].get_last_error()


# ----- the PC ------------------------------------------------------------------------------


def windows_version() -> dict:
    """The real version and build (RtlGetVersion, which does not lie to old programs) and the
    names the registry gives."""
    d = _dlls()
    ctypes, wintypes = d["ctypes"], d["wintypes"]

    class OSVERSIONINFOEXW(ctypes.Structure):
        _fields_ = (("dwOSVersionInfoSize", wintypes.DWORD), ("dwMajorVersion", wintypes.DWORD),
                    ("dwMinorVersion", wintypes.DWORD), ("dwBuildNumber", wintypes.DWORD),
                    ("dwPlatformId", wintypes.DWORD), ("szCSDVersion", wintypes.WCHAR * 128),
                    ("wServicePackMajor", wintypes.WORD), ("wServicePackMinor", wintypes.WORD),
                    ("wSuiteMask", wintypes.WORD), ("wProductType", wintypes.BYTE), ("wReserved", wintypes.BYTE))

    info = OSVERSIONINFOEXW()
    info.dwOSVersionInfoSize = ctypes.sizeof(info)
    d["ntdll"].RtlGetVersion(ctypes.byref(info))
    out = {"major": info.dwMajorVersion, "minor": info.dwMinorVersion, "build": info.dwBuildNumber,
           "product_type": info.wProductType}
    out["name"] = "Windows 11" if info.dwMajorVersion == 10 and info.dwBuildNumber >= 22000 else f"Windows {info.dwMajorVersion}"
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            for name in ("ProductName", "DisplayVersion", "EditionID", "UBR"):
                try:
                    out[f"registry_{name}"] = winreg.QueryValueEx(k, name)[0]
                except OSError:
                    pass
    except OSError as e:
        out["registry_error"] = str(e)
    return out


def dpi_awareness() -> str:
    """What DPI mode this process runs in (Qt sets per-monitor v2 when it starts)."""
    d = _dlls()
    try:
        user32 = d["user32"]
        user32.GetThreadDpiAwarenessContext.restype = d["ctypes"].c_void_p
        user32.GetAwarenessFromDpiAwarenessContext.argtypes = [d["ctypes"].c_void_p]
        ctx = user32.GetThreadDpiAwarenessContext()
        value = user32.GetAwarenessFromDpiAwarenessContext(ctx)
        return {0: "unaware", 1: "system aware", 2: "per-monitor aware"}.get(value, f"unknown ({value})")
    except Exception as e:
        return f"unknown ({type(e).__name__})"


ALTGR_PROBE_CHARS = "@{}[]\\|~€$#^`<>"


def keyboard_layout() -> dict:
    d = _dlls()
    ctypes, user32 = d["ctypes"], d["user32"]
    buf = ctypes.create_unicode_buffer(9)
    user32.GetKeyboardLayoutNameW(buf)
    hkl = user32.GetKeyboardLayout(0) or 0
    lang_id = hkl & 0xFFFF
    name_buf = ctypes.create_unicode_buffer(85)
    n = d["kernel32"].LCIDToLocaleName(lang_id, name_buf, 85, 0)
    scans = {ch: int(user32.VkKeyScanExW(ch, hkl)) for ch in ALTGR_PROBE_CHARS}
    from .analysis import altgr_from_scans

    return {"layout_id": buf.value, "hkl": f"0x{hkl:X}", "language": name_buf.value if n else "", **altgr_from_scans(scans)}


def interactive_desktop() -> bool:
    """Is there a desktop that takes input (a signed-in session)? False on a service with none."""
    d = _dlls()
    handle = d["user32"].OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
    if not handle:
        return False
    d["user32"].CloseDesktop(handle)
    return True


def frozen_info() -> dict:
    return {"frozen": bool(getattr(sys, "frozen", False)), "executable": sys.executable,
            "bundle_dir": getattr(sys, "_MEIPASS", "")}


# ----- windows and the cursor --------------------------------------------------------------


def cursor_pos() -> tuple[int, int]:
    d = _dlls()
    pt = d["wintypes"].POINT()
    if not d["user32"].GetCursorPos(d["ctypes"].byref(pt)):
        raise OSError(f"GetCursorPos failed with error {last_error()}")
    return int(pt.x), int(pt.y)


def key_down(vk: int) -> bool:
    return bool(_dlls()["user32"].GetAsyncKeyState(vk) & 0x8000)


def window_class(hwnd: int) -> str:
    d = _dlls()
    buf = d["ctypes"].create_unicode_buffer(256)
    n = d["user32"].GetClassNameW(hwnd, buf, 256)
    return buf.value[:n] if n > 0 else ""


def window_pid(hwnd: int) -> int:
    d = _dlls()
    pid = d["wintypes"].DWORD(0)
    d["user32"].GetWindowThreadProcessId(hwnd, d["ctypes"].byref(pid))
    return int(pid.value)


def process_name(pid: int) -> str:
    d = _dlls()
    kernel32 = d["kernel32"]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return f"(error {last_error()})"
    try:
        buf = d["ctypes"].create_unicode_buffer(1024)
        size = d["wintypes"].DWORD(1024)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, d["ctypes"].byref(size)):
            return f"(error {last_error()})"
        return os.path.basename(buf.value)
    finally:
        kernel32.CloseHandle(handle)


def foreground() -> dict:
    """The window in front: its class name and its program. No window title (it can hold a
    document name)."""
    hwnd = _dlls()["user32"].GetForegroundWindow() or 0
    if not hwnd:
        return {"hwnd": 0, "class": "", "pid": 0, "process": ""}
    pid = window_pid(hwnd)
    return {"hwnd": int(hwnd), "class": window_class(hwnd), "pid": pid, "process": process_name(pid) if pid else "",
            "is_probe": pid == os.getpid()}


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    d = _dlls()
    r = d["wintypes"].RECT()
    if not d["user32"].GetWindowRect(hwnd, d["ctypes"].byref(r)):
        return None
    return (r.left, r.top, r.right - r.left, r.bottom - r.top)


def add_no_activate(hwnd: int) -> dict:
    """Add WS_EX_NOACTIVATE to a window's extended style; returns the style before and after."""
    user32 = _dlls()["user32"]
    before = int(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, before | WS_EX_NOACTIVATE)
    after = int(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))
    return {"exstyle_before": f"0x{before & 0xFFFFFFFF:08X}", "exstyle_after": f"0x{after & 0xFFFFFFFF:08X}",
            "noactivate_set": bool(after & WS_EX_NOACTIVATE), "toolwindow": bool(after & WS_EX_TOOLWINDOW),
            "topmost": bool(after & WS_EX_TOPMOST)}


def find_windows(class_names: set[str]) -> list[int]:
    """Visible top-level windows whose class is one of these."""
    d = _dlls()
    found: list[int] = []

    def visit(hwnd, _lparam):
        if d["user32"].IsWindowVisible(hwnd) and window_class(hwnd) in class_names:
            found.append(int(hwnd))
        return True

    d["user32"].EnumWindows(d["EnumProc"](visit), 0)
    return found


def wait_for_window(class_names: set[str], ignore: set[int], timeout: float) -> int:
    """The first new visible window of these classes, waiting up to `timeout` seconds; 0 if none."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for hwnd in find_windows(class_names):
            if hwnd not in ignore:
                return hwnd
        time.sleep(0.2)
    return 0


def set_foreground(hwnd: int) -> bool:
    return bool(_dlls()["user32"].SetForegroundWindow(hwnd))


NOTEPAD_EDIT_CLASSES = ("RichEditD2DPT", "Edit")  # Windows 11 Notepad, then the classic one


def notepad_edit(hwnd: int) -> int:
    """The text area inside a Notepad window, searched a few levels down."""
    user32 = _dlls()["user32"]
    level = [hwnd]
    for _ in range(4):
        nxt = []
        for parent in level:
            for cls in NOTEPAD_EDIT_CLASSES:
                child = user32.FindWindowExW(parent, None, cls, None)
                if child:
                    return int(child)
            child = user32.FindWindowExW(parent, None, None, None)
            while child:
                nxt.append(child)
                child = user32.FindWindowExW(parent, child, None, None)
        level = nxt
    return 0


def set_window_text(hwnd: int, text: str) -> bool:
    return bool(_dlls()["user32"].SendMessageW(hwnd, WM_SETTEXT, 0, text))


def ignore_console_ctrl_c(ignore: bool) -> bool:
    """While True, a Ctrl+C reaching our console does not stop this process."""
    return bool(_dlls()["kernel32"].SetConsoleCtrlHandler(None, ignore))


def kill_process(pid: int) -> int:
    return subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False).returncode


def open_folder(path: str) -> None:
    """Open Explorer with the file selected."""
    subprocess.Popen(["explorer", f"/select,{path}"])


# ----- the registry ------------------------------------------------------------------------


def run_key_round_trip() -> dict:
    """Write, read and remove a throwaway value under the Run key (never the real one)."""
    import winreg

    out: dict = {"value_name": PROBE_RUN_VALUE}
    command = f'"{sys.executable}"'
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as k:
        winreg.SetValueEx(k, PROBE_RUN_VALUE, 0, winreg.REG_SZ, command)
        out["written"] = True
        value, _kind = winreg.QueryValueEx(k, PROBE_RUN_VALUE)
        out["read_back_matches"] = value == command
        winreg.DeleteValue(k, PROBE_RUN_VALUE)
        out["removed"] = True
        try:
            winreg.QueryValueEx(k, PROBE_RUN_VALUE)
            out["gone_after_remove"] = False
        except FileNotFoundError:
            out["gone_after_remove"] = True
    return out


def office_installed() -> dict:
    """Is Word or Outlook installed? Looked up under App Paths, where Office registers itself."""
    import winreg

    found = {}
    for exe in ("WINWORD.EXE", "OUTLOOK.EXE"):
        hit = False
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"):
                    hit = True
            except OSError:
                continue
        found[exe.split(".")[0].lower()] = hit
    return found


# ----- the machine -------------------------------------------------------------------------

PF_AVX2_INSTRUCTIONS_AVAILABLE = 40
PF_AVX512F_INSTRUCTIONS_AVAILABLE = 41


def cpu_name() -> str:
    import winreg

    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
        return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()


def physical_cores() -> int:
    """Physical cores, from GetLogicalProcessorInformation."""
    from .analysis import count_cores

    d = _dlls()
    ctypes, wintypes = d["ctypes"], d["wintypes"]
    fn = d["kernel32"].GetLogicalProcessorInformation
    fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    fn.restype = wintypes.BOOL
    size = wintypes.DWORD(0)
    fn(None, ctypes.byref(size))  # fails on purpose and reports the size it needs
    buf = ctypes.create_string_buffer(size.value)
    if not fn(buf, ctypes.byref(size)):
        raise OSError(f"GetLogicalProcessorInformation failed with error {last_error()}")
    return count_cores(buf.raw[: size.value])


def total_memory_bytes() -> int:
    d = _dlls()
    ctypes, wintypes = d["ctypes"], d["wintypes"]

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = (("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong))

    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(status)
    if not d["kernel32"].GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError(f"GlobalMemoryStatusEx failed with error {last_error()}")
    return int(status.ullTotalPhys)


def processor_features() -> dict:
    fn = _dlls()["kernel32"].IsProcessorFeaturePresent
    fn.argtypes = [_dlls()["wintypes"].DWORD]
    fn.restype = _dlls()["wintypes"].BOOL
    return {"avx2": bool(fn(PF_AVX2_INSTRUCTIONS_AVAILABLE)), "avx512f": bool(fn(PF_AVX512F_INSTRUCTIONS_AVAILABLE))}

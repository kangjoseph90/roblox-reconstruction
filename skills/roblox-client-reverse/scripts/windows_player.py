"""Windows Player process/window handles. No collection or recovery dependencies."""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import platform

from compatibility import running_players
from windows_access_probe import kernel


def require_windows():
    if platform.system() != "Windows":
        raise OSError("Player control requires Windows")


def user32():
    require_windows()
    u = C.WinDLL("user32", use_last_error=True)
    callback = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    signatures = {
        "EnumWindows": ([callback, W.LPARAM], W.BOOL),
        "GetWindowThreadProcessId": ([W.HWND, C.POINTER(W.DWORD)], W.DWORD),
        "GetWindowTextLengthW": ([W.HWND], C.c_int),
        "GetWindowTextW": ([W.HWND, W.LPWSTR, C.c_int], C.c_int),
        "GetClassNameW": ([W.HWND, W.LPWSTR, C.c_int], C.c_int),
        "IsWindow": ([W.HWND], W.BOOL),
        "IsWindowVisible": ([W.HWND], W.BOOL),
        "IsIconic": ([W.HWND], W.BOOL),
        "GetWindow": ([W.HWND, W.UINT], W.HWND),
        "GetWindowRect": ([W.HWND, C.POINTER(W.RECT)], W.BOOL),
        "PostMessageW": ([W.HWND, W.UINT, W.WPARAM, W.LPARAM], W.BOOL),
    }
    for name, (args, result) in signatures.items():
        getattr(u, name).argtypes, getattr(u, name).restype = args, result
    u.callback_type = callback
    return u


def _error(operation):
    return C.WinError(C.get_last_error(), operation)


class Session:
    """Retain a process handle so subsequent actions cannot follow a reused PID."""
    def __init__(self, pid, terminate=False):
        require_windows()
        self.k = kernel()
        self.u = user32()
        self.k.GetProcessTimes.argtypes = [W.HANDLE] + [C.POINTER(W.FILETIME)] * 4
        self.k.GetProcessTimes.restype = W.BOOL
        self.k.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
        self.k.WaitForSingleObject.restype = W.DWORD
        self.k.TerminateProcess.argtypes = [W.HANDLE, W.UINT]
        self.k.TerminateProcess.restype = W.BOOL
        self.handle = self.k.OpenProcess(0x1000 | 0x100000 | (1 if terminate else 0), False, pid)
        if not self.handle:
            raise _error("OpenProcess")
        try:
            buffer, size = C.create_unicode_buffer(32768), W.DWORD(32768)
            if not self.k.QueryFullProcessImageNameW(self.handle, 0, buffer, C.byref(size)):
                raise _error("QueryFullProcessImageNameW")
            if Path(buffer.value).name.lower() != "robloxplayerbeta.exe":
                raise ValueError("PID does not identify RobloxPlayerBeta.exe")
            times = [W.FILETIME() for _ in range(4)]
            if not self.k.GetProcessTimes(self.handle, *(C.byref(t) for t in times)):
                raise _error("GetProcessTimes")
            self.identity = {"pid": pid, "path": buffer.value,
                             "created_at_ticks": (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime}
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle)
            self.handle = None

    def wait(self, seconds=0):
        result = self.k.WaitForSingleObject(self.handle, int(seconds * 1000))
        if result == 0xFFFFFFFF:
            raise _error("WaitForSingleObject")
        return result == 0

    def window_info(self, hwnd):
        u, owner, rect = self.u, W.DWORD(), W.RECT()
        if not u.IsWindow(hwnd) or not u.GetWindowThreadProcessId(hwnd, C.byref(owner)):
            raise OSError("target window disappeared")
        if owner.value != self.identity["pid"] or self.wait():
            raise OSError("target window/process identity changed")
        length = u.GetWindowTextLengthW(hwnd)
        title, cls = C.create_unicode_buffer(length + 1), C.create_unicode_buffer(256)
        u.GetWindowTextW(hwnd, title, len(title))
        u.GetClassNameW(hwnd, cls, len(cls))
        if not u.GetWindowRect(hwnd, C.byref(rect)):
            raise _error("GetWindowRect")
        return {"hwnd": int(hwnd), "pid": owner.value, "title": title.value, "class": cls.value,
                "visible": bool(u.IsWindowVisible(hwnd)), "minimized": bool(u.IsIconic(hwnd)),
                "owned": bool(u.GetWindow(hwnd, 4)),
                "rect": [rect.left, rect.top, rect.right, rect.bottom]}

    def windows(self):
        u, rows = self.u, []
        @u.callback_type
        def visit(hwnd, _):
            owner = W.DWORD()
            u.GetWindowThreadProcessId(hwnd, C.byref(owner))
            if owner.value == self.identity["pid"]:
                try:
                    rows.append(self.window_info(hwnd))
                except OSError:
                    pass  # Window may disappear during enumeration.
            return True
        if not u.EnumWindows(visit, 0):
            raise _error("EnumWindows")
        return [row for row in rows if row["visible"] and not row["owned"]]

    def request_close(self, hwnd):
        self.window_info(hwnd)
        # Same normal-close path as the title-bar button; does not require focus.
        if not self.u.PostMessageW(hwnd, 0x0112, 0xF060, 0):  # WM_SYSCOMMAND / SC_CLOSE
            raise _error("PostMessageW(SC_CLOSE)")

    def terminate(self):
        with Session(self.identity["pid"], terminate=True) as target:
            if target.identity != self.identity or self.wait():
                raise OSError("process identity changed before forced termination")
            if not target.k.TerminateProcess(target.handle, 1):
                raise _error("TerminateProcess")


class WindowsPlayer:
    def require_available(self):
        require_windows()

    def session(self, pid):
        return Session(pid)

    def players(self):
        self.require_available()
        rows, errors = [], []
        for item in running_players(include_unreadable=True):
            try:
                with self.session(item["pid"]) as process:
                    rows.append(dict(process.identity, windows=process.windows()))
            except (OSError, ValueError) as exc:
                errors.append({"pid": item["pid"], "error": str(exc)})
        return {"players": rows, "errors": errors}

    def launch(self, uri, executable=None):
        import os
        import subprocess
        self.require_available()
        if executable:
            child = subprocess.Popen([str(executable), uri], cwd=str(executable.parent),
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"method": "explicit_executable", "launcher_pid": child.pid, "requested_executable": str(executable)}
        os.startfile(uri)
        return {"method": "registered_protocol", "launcher_pid": None}

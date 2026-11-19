"""Read-only Windows prerequisite diagnostic, NOT a Player script collector.

An owned child exposes a fixed marker over a pipe. For --pid, only the first
64 bytes of the RobloxPlayerBeta.exe image are read and compared with its file.
No arbitrary addresses, heap scanning, injection, debugger attachment, privilege
changes, target writes or security changes are supported.
"""
import argparse
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

MARKER = b"RCR_OWNED_PROBE!"


def kernel():
    k = C.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "OpenProcess": ([W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
        "CloseHandle": ([W.HANDLE], W.BOOL),
        "QueryFullProcessImageNameW": ([W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)], W.BOOL),
        "CreateToolhelp32Snapshot": ([W.DWORD, W.DWORD], W.HANDLE),
        "ReadProcessMemory": ([W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)], W.BOOL),
    }
    for name, (args, result) in signatures.items():
        function = getattr(k, name)
        function.argtypes, function.restype = args, result
    return k


def outcome(success):
    # LastError is meaningful only on API failure.
    error = 0 if success else C.get_last_error()
    return {"attempted": True, "success": bool(success), "win32_error": error,
            "message": "" if success else C.FormatError(error).strip()}


def read_marker(k, process, address, expected):
    buffer = C.create_string_buffer(len(expected))
    read = C.c_size_t()
    ok = k.ReadProcessMemory(process, address, buffer, len(expected), C.byref(read))
    result = outcome(ok)
    data = buffer.raw[:read.value]
    result.update(requested_bytes=len(expected), bytes_read=read.value,
                  matches_expected=bool(ok and data == expected))
    # The bytes are an owned marker or executable header, never script/heap data.
    result["sha256"] = hashlib.sha256(data).hexdigest() if data else None
    return result


def owned_control(k):
    child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--owned-child"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        descriptor = json.loads(child.stdout.readline())
        # A Windows venv redirector may have a different PID from its interpreter.
        # The marker address belongs to the PID reported by that interpreter.
        process = k.OpenProcess(0x10, False, descriptor["pid"])  # PROCESS_VM_READ
        opened = outcome(process)
        result = {"pid": descriptor["pid"], "launcher_pid": child.pid, "descriptor": descriptor,
                  "child_running_before_read": child.poll() is None,
                  "open_process_vm_read": opened}
        if process:
            try:
                result["memory_read"] = read_marker(k, process, descriptor["address"], MARKER)
            finally:
                k.CloseHandle(process)
        else:
            result["memory_read"] = {"attempted": False, "reason": "OpenProcess failed"}
        return result
    finally:
        child.communicate("done\n", timeout=10)


def player_probe(k, pid):
    result = {"pid": pid, "scope": "main executable DOS header only; UI/game state is recorded separately"}
    query = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    result["open_process_query_limited"] = outcome(query)
    if not query:
        result["memory_read"] = {"attempted": False, "reason": "cannot verify target identity"}
        return result
    try:
        path_buffer, size = C.create_unicode_buffer(32768), W.DWORD(32768)
        ok = k.QueryFullProcessImageNameW(query, 0, path_buffer, C.byref(size))
        result["query_image_path"] = outcome(ok)
        if not ok:
            result["memory_read"] = {"attempted": False, "reason": "image path query failed"}
            return result
        image_path = Path(path_buffer.value)
        result["image_path"] = str(image_path)
        if image_path.name.lower() != "robloxplayerbeta.exe":
            raise ValueError("--pid must identify RobloxPlayerBeta.exe")
    finally:
        k.CloseHandle(query)

    process = k.OpenProcess(0x10, False, pid)
    result["open_process_vm_read"] = outcome(process)
    if not process:
        result["memory_read"] = {"attempted": False, "reason": "OpenProcess(PROCESS_VM_READ) failed"}
        return result
    try:
        class Module(C.Structure):
            _fields_ = [("dwSize", W.DWORD), ("th32ModuleID", W.DWORD),
                        ("th32ProcessID", W.DWORD), ("GlblcntUsage", W.DWORD),
                        ("ProccntUsage", W.DWORD), ("modBaseAddr", C.c_void_p),
                        ("modBaseSize", W.DWORD), ("hModule", W.HMODULE),
                        ("szModule", W.WCHAR * 256), ("szExePath", W.WCHAR * 260)]
        k.Module32FirstW.argtypes = [W.HANDLE, C.POINTER(Module)]
        k.Module32FirstW.restype = W.BOOL
        k.Module32NextW.argtypes = [W.HANDLE, C.POINTER(Module)]
        k.Module32NextW.restype = W.BOOL
        attempts = []
        for _ in range(3):
            snapshot = k.CreateToolhelp32Snapshot(0x08 | 0x10, pid)
            entry = outcome(snapshot != C.c_void_p(-1).value)
            attempts.append(entry)
            if entry["success"] or entry["win32_error"] != 24:  # ERROR_BAD_LENGTH
                break
        result["module_snapshot_attempts"] = attempts
        if not attempts[-1]["success"]:
            result["memory_read"] = {"attempted": False, "reason": "module snapshot failed"}
            return result
        try:
            module = Module()
            module.dwSize = C.sizeof(module)
            ok = k.Module32FirstW(snapshot, C.byref(module))
            result["module_first"] = outcome(ok)
            while ok:
                if os.path.normcase(module.szExePath) == os.path.normcase(str(image_path)):
                    with image_path.open("rb") as stream:
                        expected = stream.read(64)
                    if len(expected) != 64 or expected[:2] != b"MZ":
                        raise ValueError("target file has no expected DOS header")
                    result["main_module"] = {"base_address": hex(module.modBaseAddr),
                                             "image_size": module.modBaseSize}
                    result["memory_read"] = read_marker(k, process, module.modBaseAddr, expected)
                    return result
                ok = k.Module32NextW(snapshot, C.byref(module))
            result["module_enumeration_end"] = outcome(False)
            result["memory_read"] = {"attempted": False, "reason": "main module not found"}
            return result
        finally:
            k.CloseHandle(snapshot)
    finally:
        k.CloseHandle(process)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, help="optional Roblox Player PID; executable header only")
    parser.add_argument("--out", type=Path, help="new JSON evidence file")
    parser.add_argument("--owned-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Windows only")
    if args.owned_child:
        marker = C.create_string_buffer(MARKER)
        print(json.dumps({"pid": os.getpid(), "address": C.addressof(marker)}), flush=True)
        sys.stdin.readline()
        return 0
    if C.sizeof(C.c_void_p) != 8:
        parser.error("64-bit Python required for this diagnostic")
    k = kernel()
    report = {"schema": "rcr-windows-access/v1", "created_at": datetime.now(timezone.utc).isoformat(),
              "platform": platform.platform(), "architecture": platform.machine(),
              "python": platform.python_version(), "command": sys.argv,
              "status": "access_diagnostic_only", "scope": "this diagnostic reads only the marker and the Player DOS header",
              "debugger_attach": "not_attempted", "target_writes": False,
              "privilege_changes": False, "heap_scan_performed": False, "game_scripts_collected": 0,
              "owned_control": owned_control(k)}
    if args.pid is not None:
        if not 0 < args.pid <= 0xFFFFFFFF:
            parser.error("--pid must be a positive 32-bit process ID")
        report["player"] = player_probe(k, args.pid)
    serialized = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
    if args.out:
        with args.out.open("x", encoding="utf-8") as output:
            output.write(serialized)
    print(serialized)
    passed = report["owned_control"]["memory_read"].get("matches_expected", False)
    if args.pid is not None:
        passed = passed and report["player"]["memory_read"].get("matches_expected", False)
    return 0 if passed else 2


if __name__ == "__main__":
    sys.exit(main())

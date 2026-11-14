"""Known-build lookup and explicitly unverified best-effort profile selection."""
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import platform
import struct
import copy

REGISTRY = Path(__file__).resolve().parents[1] / "references/compatibility.json"


def profiles():
    return json.loads(REGISTRY.read_text(encoding="utf-8"))["profiles"]


def fingerprint(path):
    path = Path(path).resolve()
    with path.open("rb") as stream:
        dos = stream.read(64)
        if len(dos) != 64 or dos[:2] != b"MZ":
            raise ValueError("Player executable is not a PE image")
        stream.seek(struct.unpack_from("<I", dos, 60)[0])
        pe = stream.read(6)
        if pe[:4] != b"PE\0\0":
            raise ValueError("Player executable has no PE signature")
        machine = struct.unpack_from("<H", pe, 4)[0]
        stream.seek(0)
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "sha256": digest.hexdigest(), "pe_machine": machine}


def select_profile(image, os_name=None, architecture=None):
    os_name = os_name or platform.system()
    architecture = (architecture or platform.machine()).upper()
    if architecture == "X86_64":
        architecture = "AMD64"
    for profile in profiles():
        if (profile["os"] == os_name and profile["architecture"] == architecture
                and profile["player_sha256"] == image["sha256"]
                and profile["pe_machine"] == image["pe_machine"]):
            return profile
    return None


def choose_profile(image, os_name=None, architecture=None):
    """An unknown hash is a confidence label, not a version-based refusal."""
    os_name = os_name or platform.system()
    architecture = (architecture or platform.machine()).upper()
    architecture = "AMD64" if architecture == "X86_64" else architecture
    exact = select_profile(image, os_name, architecture)
    if exact:
        return dict(exact, compatibility_status="verified", baseline_profile=exact["id"])
    candidates = [p for p in profiles() if p["os"] == os_name and p["architecture"] == architecture
                  and p["pe_machine"] == image["pe_machine"]]
    if not candidates:
        return None
    baseline = candidates[-1]
    result = copy.deepcopy(baseline)
    result.update(id="best-effort-" + image["sha256"][:12], baseline_profile=baseline["id"],
                  baseline_player_sha256=baseline["player_sha256"], player_sha256=image["sha256"],
                  player_version="unverified", compatibility_status="unverified",
                  status="best-effort candidate; runtime structures and buffers must validate")
    return result


def installed_players():
    if platform.system() != "Windows":
        return []
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "Roblox/Versions"
    if not root.is_dir():
        return []
    return sorted(root.glob("*/RobloxPlayerBeta.exe"), key=lambda p: p.stat().st_mtime, reverse=True)


def runtime(executable=None):
    result = {"os": platform.system(), "architecture": platform.machine(),
              "python_bits": C.sizeof(C.c_void_p) * 8, "compatible": False}
    if result["os"] != "Windows" or result["python_bits"] != 64:
        return dict(result, reason="live collector requires Windows with 64-bit Python; offline commands remain available")
    paths = [Path(executable)] if executable else installed_players()
    if not paths:
        return dict(result, reason="Roblox Player is not installed in the default location; specify --player-exe")
    # Match the newest installed image; do not silently launch an older supported client.
    image = fingerprint(paths[0])
    profile = choose_profile(image)
    return dict(result, image=image, profile=profile, compatible=profile is not None,
                compatibility_status=profile["compatibility_status"] if profile else "unsupported_platform",
                notice="Unverified Player build: attempting best-effort collection with runtime validation" if profile and profile["compatibility_status"] == "unverified" else None,
                reason=None if profile else "no collector implementation for this OS/architecture/PE machine")


def running_players():
    if platform.system() != "Windows":
        return []
    from windows_access_probe import kernel, outcome
    k = kernel()
    class Process(C.Structure):
        _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD), ("pid", W.DWORD),
                    ("heap", C.c_void_p), ("module", W.DWORD), ("threads", W.DWORD),
                    ("parent", W.DWORD), ("priority", W.LONG), ("flags", W.DWORD),
                    ("name", W.WCHAR * 260)]
    for name in ("Process32FirstW", "Process32NextW"):
        f = getattr(k, name)
        f.argtypes, f.restype = [W.HANDLE, C.POINTER(Process)], W.BOOL
    snapshot = k.CreateToolhelp32Snapshot(2, 0)
    if snapshot == C.c_void_p(-1).value:
        raise OSError(json.dumps(outcome(False)))
    result = []
    try:
        entry = Process()
        entry.dwSize = C.sizeof(entry)
        ok = k.Process32FirstW(snapshot, C.byref(entry))
        while ok:
            if entry.name.lower() == "robloxplayerbeta.exe":
                handle = k.OpenProcess(0x1000, False, entry.pid)
                if handle:
                    try:
                        buffer, count = C.create_unicode_buffer(32768), W.DWORD(32768)
                        if k.QueryFullProcessImageNameW(handle, 0, buffer, C.byref(count)):
                            result.append({"pid": entry.pid, "path": buffer.value})
                    finally:
                        k.CloseHandle(handle)
            ok = k.Process32NextW(snapshot, C.byref(entry))
    finally:
        k.CloseHandle(snapshot)
    return result

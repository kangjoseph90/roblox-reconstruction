# Player acquisition and client-dump import

## Current capability

Windows 11 x64 was tested on 2026-11-13 with Python 3.12.10 and validly signed
Player 0.747.0.7471481, PID 24084, at its home screen, without elevation.
Read-only process-handle requests, module enumeration and a 64-byte DOS header
read succeeded; that header matched the executable on disk. A separate owned
interpreter yielded its known 16-byte marker. No game was entered. No debugger
attach, script instance enumeration or script bytecode extraction was attempted.
No owned/explicitly permitted experience was available. These facts supersede
earlier statements that Windows had never been tested, but do not establish
compatibility of any community executor or a live script collector.

Invoke the diagnostic explicitly with 64-bit Python:

```powershell
python scripts/windows_access_probe.py --out owned-control.json
python scripts/windows_access_probe.py --pid PLAYER_PID --out windows-access.json
```

The optional PID must be a RobloxPlayerBeta.exe process. Its only memory read is
the first 64 bytes of that executable's image; arbitrary addresses and memory
scans are not exposed. It always runs an owned-child marker control. Output must
be a new file. Exit 0 means requested read comparisons passed, exit 2 means a
comparison failed or a prerequisite was denied; neither means code was collected.
On Windows, a virtual-environment launcher may have a different PID from the
interpreter: the control uses the interpreter's pipe-reported PID and address.

API contracts: [OpenProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-openprocess),
[ReadProcessMemory](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-readprocessmemory),
[CreateToolhelp32Snapshot](https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/nf-tlhelp32-createtoolhelp32snapshot),
[Module32FirstW](https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/nf-tlhelp32-module32firstw).

There is no verified live Player collector in this skill. `rcr acquire PLACE_ID --backend auto --out OUTPUT` and `--backend player` return `collector_unavailable` (exit 2). They create an inspectable failure report rather than disguising a metadata request as collection. `--backend official` selects the earlier, separate official asset-download path. `rcr doctor` reports these capabilities.

On 2026-11-11 the researcher launched the signed macOS ARM64 Player, which updated from 0.744 to **0.747.0.7471481**, and joined the external target through its normal experience-launch URI. A screenshot confirmed in-game rendering. No game scripts or bytecode were acquired. No controlled-Player source comparison was possible without a collector. No executor was installed, no process was instrumented, and the app signature/OS protection settings were not changed.

## Actual collection dependency

[UniversalSynSaveInstance](https://github.com/luau/UniversalSynSaveInstance/tree/888b5f318d1e035434c473c7b952bd1f85b767ab) is a real instance serializer, with a separate dependency on a runtime exposing script inspection and file output. Its pinned source calls `getscriptbytecode` for optional raw bytecode and `decompile` for reconstructed text. `SaveBytecode` embeds bytes in a Base64 comment inside the serialized Source property; a failure/disabled decompiler can coexist with useful bytecode. This is why treating every Source string as recovered implementation was incorrect.

The serializer supports binary/XML place/model output and a serialized hierarchy. Its options can reparent, isolate, rename or omit instances; the saved path is not automatically the original runtime path. Bytecode coverage also depends on the runtime's access to particular script containers, current replication, streaming, unloaded modules and per-script failures. A Source marker is not proof of live capture or source authenticity.

The project is active at commit `888b5f318d1e035434c473c7b952bd1f85b767ab` (2026-11-08). Its [license](https://github.com/luau/UniversalSynSaveInstance/blob/888b5f318d1e035434c473c7b952bd1f85b767ab/LICENSE) contains AGPLv3 plus additional section-7 terms, so GitHub labels it “Other.” This skill implements an independent output-format reader and does not redistribute or load that collector.

## macOS ARM64 versus Windows

| Candidate | Code-bearing path | Platform evidence | Current-build status / integration |
|---|---|---|---|
| USSI | Runtime-provided bytecode/decompiler → serialized Source plus optional Base64 bytes | Luau script; host runtime supplies native capabilities | Output convention supported by importer. No live runtime tested. |
| [K4NK4N/Macsploit](https://github.com/K4NK4N/Macsploit/tree/91369de0cbab5c8d5a895d0c06293bf0ef7b60df) | Native executor/source includes script bytecode interface | CMake explicitly targets x86_64; last commit 2024-06-08; C++/Apple frameworks and native dependencies; no license file observed | Does not establish current ARM64 compatibility. Not built/injected. |
| [Nexus42Dev/MacSploit](https://github.com/Nexus42Dev/MacSploit/tree/1c2bcfe68f186d75a841a86cc72cadb05ac75a46) | Distributed binary patch/injection route | Installer modifies Player binary/signature; last commit 2025-03-16; no license file observed | Not executed. Compatibility and source acquisition not established. |
| [RobloxExternal-macOS](https://github.com/TheRouletteBoi/RobloxExternal-macOS/tree/5e18926133b8a35389c861a13740448b86c1ccd1) | ESP/process inspection and injection | CMake has ARM64/x86_64 branches; C++23/CMake/Apple dependencies, optional Vulkan; MIT; last commit 2026-04-23 | ARM64 compilation intent is not script-collection capability. No verified code-dump output found; not used. |
| [tyronetheqt/Xeno](https://github.com/tyronetheqt/Xeno/tree/3785b403de16b12a28f19998eb615f852688b2de) | Native bytecode retrieval bridge; client wrapper loads USSI; optional remote decompiler | Windows C++ and native dependencies; source lineage dated 2025-04-24; Apache-2.0 plus vendored notices | Concrete source-level collection interfaces exist. No Windows host or current-build execution test here; no claim it works on 2026 Player. Windows binaries do not supply a Mac ARM64 runtime. |

Licensing, platform intent, executable availability and current-build compatibility are separate findings. Native memory layouts/serialization and bytecode-loading boundaries are build-specific. Existing upstream VM capture experiments do not validate attachment or script access in Player. No universal offset/layout, native API or cache format was established by this follow-up. An installed vendor avatar RBXM was recognized as binary-v0 with a plaintext LocalScript; that is a local vendor resource, not game-source acquisition or target bytecode compatibility.

## Offline import procedure

The executable `scripts/rcr` can be put on PATH, or use `python3 scripts/rcr.py`:

```sh
rcr import /path/to/authorized-client-dump.rbxlx --out /new/repository
rcr decompile /path/to/bytecode-directory --out /new/decompiled --tools /path/to/tools.json --bytecode-format luau
rcr validate /new/repository
```

Import recognizes the pinned USSI Source convention in both binary and XML containers. It preserves parent/child instance IDs and exact originals/hashes, extracts embedded bytes, records malformed/conflicting data, and distinguishes captured-decompiler/cached text from original source. Disabled, failed, ignored, empty and comment-only decompiler outputs are not counted as implementations. Unknown bytecode encoding remains opaque. The vanilla Luau assertion must not be used merely to force Roblox-transformed bytes through a standard-opcode decoder.

The new tests exercise the adapter using explicitly constructed protocol fixtures; these are labeled as such and are not live Player evidence. A real dump must arrive from an authorized collector before the skill can recover target code. This follow-up's live-acquisition acceptance result is **failure**, despite the functioning importer and CLI extension.

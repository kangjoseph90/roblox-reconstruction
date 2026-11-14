# Acquisition decisions

Acquisition yields source-bearing artifacts; decompilation transforms already acquired bytecode. Run `extract` directly whenever an artifact is supplied. Do not make network access a prerequisite for static analysis.

## Available paths and requirements

| Method | Input → output | OS / version coupling | Permissions/access | Implemented / observed |
|---|---|---|---|---|
| Public metadata | URL/Place ID → universe/creator/copy-policy JSON | Any Python host; public API schema may change | Public read access | Implemented; Sell Lemons resolved on 2026-11-11. No source obtained. |
| Official asset delivery | Asset/place ID → binary/XML file, or denial/error | Any Python host; current endpoints recorded in reports | Server-granted download permission; explicit Open Cloud key for authenticated path, `legacy-asset:manage` scope | Implemented; anonymous Sell Lemons returned embedded 401. Authenticated success path is contract-tested, not live-tested with an account/key. |
| Studio place export | Editable place → `.rbxl`/`.rbxlx` | Studio Windows/macOS; serializer version/reflection dependent | Own/edit permission, or copy-enabled experience and authenticated Studio | Offline readers tested on owned synthetic exports generated independently with rbx-dom. Actual Studio UI export not run here. |
| Model/package export | Accessible model/package → `.rbxm`/`.rbxmx` | Studio Windows/macOS; common binary/XML format | Asset access and applicable reuse permission | Reader supports same format; package dependencies are inventoried, not fetched recursively. |
| Selected local file/tree/dump | Explicit input path → originals + source repository | Python 3.9+, optional LZ4/ZSTD | Access to the provided artifact | Implemented and tested; no automatic scanning of unrelated Roblox caches. |
| Studio editor snapshot | Authorized edit session → `rcr-dump/v1` | Studio API/security-context dependent | Editor/command-bar source access | Bundled exporter; syntax checked with official compiler, not executed in Studio. Prefer full file export for large places. |
| Studio client snapshot | Authorized Play client → metadata JSON | Snapshot/build specific; streaming dependent | Owned/permitted testing session | Exporter provided; runtime source/bytecode access intentionally absent. Not live-tested here. |
| Owned upstream Luau host instrumentation | Owned source → captured serialized buffer at `luau_load` → bytecode/recovered source | Pinned upstream Luau API/VM; macOS ARM64 tested | Control of the embedding host and test input | Separate controlled experiment; demonstrates a capture boundary in our host, not a Roblox Player collector. |
| Roblox Player process memory | Permitted process dump/instrumented session → VM/instance state requiring reconstruction | Exact Roblox client build, architecture, debugger/OS policies | Explicit target permission and a permitted instrumentation environment; no bypass | Researched only. No memory scanner, injection, protected-process attach, or claimed Player acquisition. |

The Studio snippet returns JSON to its caller; the caller must save it without log truncation. It neither writes local files nor sends HTTP. In runtime mode it inventories only the current client data model. Editor mode requests `ScriptEditorService:GetEditorSource` and records permission failures individually.

## Acquisition stop conditions

An HTTP 401/403, an embedded API error with those codes, or a missing permission ends that download path. Do not retry through alternate domains/proxies or acquire session cookies. HTTP 200 alone does not prove success. Unknown/CDN redirect hosts are refused; credentials are never attached to content/CDN requests. A rate limit is reported without automated retry.

A copy-enabled setting grants an editable-copy workflow, not guaranteed unauthenticated file download. Public/free models can still require authentication. The official transition to authenticated asset delivery is documented in [Roblox's announcement and FAQ](https://devforum.roblox.com/t/creator-action-required-new-asset-delivery-api-endpoints-for-community-tools/3574403/87).

## Coverage and runtime requirements

Client-visible hierarchy is not the server data model. Streaming, per-player delivery, runtime cloning, object destruction, unparented objects, conditional loading and different places/sessions can all change a snapshot. A script may be present without ever executing. A module may be replicated without ever being required. Missing objects are not proof they never exist.

Memory/heap profilers provide allocation, object/reference and performance information, not a documented complete serialized-source export. A raw process dump contains build-specific VM structures, pointers, constants and instruction arrays; it is not automatically a loadable Luau bytecode file. A collector would need authorized access, correct layouts, lifetime-safe reads, nested prototype/constant reconstruction, an encoding profile, and snapshot coverage validation. This skill does not pretend those requirements are satisfied.

Keep static recovery useful even when runtime acquisition is unavailable. For an access-limited target, deliver the metadata/provenance report and state which artifact or authorized environment would enable the next stage.

# Adaptive Windows build compatibility — v0.3.4

This is bounded, observational structure recovery. It does not guarantee arbitrary future ABI/encoding changes. `runtime_validated` describes the checks below; it is separate from historical build support, syntax, CFG and behavior states.

## Resolution and acceptance

1. Fingerprint the selected x64 PE executable. Verify the actual PID image and readable DOS header with the existing read-only access probe. Never use elevation, injection, memory writes or protection changes.
2. Validate hinted class descriptors against live class names. If they moved, reuse the existing bounded descriptor discovery. An unlisted executable hash is not itself a failure.
3. On a cache miss, enumerate references to ModuleScript, LocalScript and DataModel descriptors in readable private regions. Enumerate candidate base/self/descriptor fields, requiring repeated class witnesses and an image-owned anchor relation. This relation does not prove that a pointer is executable code.
4. Locate a unique target DataModel using requested Place ID and an independently fetched Universe ID (`apis.roblox.com/universes/v1/places/ID/universe`). Preserve the actual response. Missing/ambiguous identity stops collection.
5. Discover name/parent fields and a common children-vector field/stride. Require valid ancestor chains to that root and reciprocal child membership, with several parents and at least two service classes. Reject multiple surviving layouts.
6. Partition script instances by sorted-address index parity before buffer probing. Buffer calibration uses only the collection's target/service scope, excluding other DataModels, detached instances and platform services. Ancestry/name metadata determines eligibility; a passing payload never selects its own witness. Related ancestor/child metadata can be read during structural selection, so this is not a statistical independence claim for every header read.
7. Discover class-specific buffer fields and a common container-string field from training instances. Bound probing by the next recognized adjacent Instance where available. Require one candidate, then verify unused payload witnesses: stable owner/header/data double reads, unchanged path, RSB1 XXH32 integrity, bounded Zstd expansion and supported bytecode/type headers. Require multiple distinct payloads in both classes. No recovered code executes.
8. Enumerate the target with the resolved fields and preserve before/after path/class/name/parent observations per script. Any instance that changes is unavailable rather than being associated with another object's code. Existing decompiler, parser and CFG pipeline consume the captured bytes unchanged.

## Cache

The default is `OUTPUT_PARENT/.rcr-structures/EXECUTABLE_SHA256.json`; `--structure-cache DIR` overrides it. Only a passed validation result can be committed atomically. Store field offsets and descriptor RVAs only; approved bytecode/type versions always come from the bundled compatibility profile, and no PID, absolute process pointer or game ID is reused. The JSON checksum detects accidental edits, not malicious forgery or authenticity.

A cache hit is only a candidate. Recheck metadata, target identity, relationships and stable payload witnesses in the current process. If rejected, preserve its rejection and perform bounded fresh discovery. If that cannot pass, stop before capture. A historical known layout is not a fallback. Corrupt/other-hash cache files cannot confer trust. `structure-validation.json` distinguishes `cache_revalidated` from `discovered_and_held_out_validated` and records rejected cached hypotheses.

## Supported assumptions and limits

- Windows x64 pointer alignment, an image-owned Instance anchor at zero, uniform common Instance fields, descriptor name pointer at +8, and MSVC std::string length/capacity representation. These ABI assumptions are not dynamically rediscovered; incompatible representations fail.
- Search bounds: common fields within `0x180`, DataModel identity within `0x400`, script fields within `0x600`, container header within `0x80`. These are search bounds, not accepted offsets. Children containers support pointer/shared_ptr vectors with stride 8/16. Parent chains are bounded to 128 nodes.
- At least six observed objects of each script class, enough target-scope objects in both partitions, multiple parents/services, and diverse valid payloads are required. Sparse, unloaded, ambiguous or unsupported patterns may be rejected.
- Descriptor discovery retains its own byte/probe/time limits. Private scans use the configured budget **per pass**; cold discovery and final enumeration are separate. Small validation reads are additional. Both phase coverage records remain visible. A readable-region snapshot is non-atomic; unreadable ranges and unknown game completeness cannot be turned into complete coverage.
- The reused decoder/profile currently recognizes the tested RSB1/Luau headers. A changed bytecode/envelope is an unsupported format until independently implemented, not a reason to guess object offsets or pass bytes to the wrong decompiler encoding.
- LocalScript/ModuleScript replicated containers only; no server code, arbitrary teleport handling or RunContext Client Script support was added. Image-owned anchors, checksums and syntax do not prove semantic or behavioral equivalence.

## Results and comparison

Read `evidence/structure-validation.json`, `resolved-profile.json`, `target-identity.json`, `inventory.json`, `capture.json` and repository/report.json. `capture.json` separates observed, integrity-valid, unavailable and opaque instances, decoder failures and revalidation reasons. `structure_discovery_coverage` and final `coverage` are separate. The root `collection_result` can be partial even when every observed buffer passes if scan coverage was incomplete.

Normal termination is not success. Interpret actual collection counters, syntax/CFG outcomes, unsupported patterns and failure reports. Compare full path+class groups and bytecode sets across snapshots; retain duplicate-path ambiguity. A lower count means not observed in that snapshot, not proof of deletion or a collector bug. Place/server/replication/player differences need separate evidence. Do not label source-only or fixture testing as a real second-build capture.

The release validation records the actual builds attempted, normal updater transitions, successful and failed runs, corpus regression, varied-layout fixtures and remaining untested compatibility. Native v0.3.3 compiler/decompiler/CFG sources are unchanged.

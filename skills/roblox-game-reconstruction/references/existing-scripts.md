# Reused mechanics and boundaries

The workflow reuses the source-inventory, constructor extraction, explicit source-body CFG mapping, citation/hash auditing and artifact-manifest patterns exercised in the earlier reconstruction. Packaging removes fixed game IDs, module names, expected counts and workstation paths. Domain semantics remain agent work.

- `scripts/luau_static.py`: the previously used read-only tokenizer/constructor parser, unchanged. It returns constructor ASTs/unevaluated expressions; it is not a full Luau parser or final-state evaluator.
- `scripts/inventory.py`: parameterized prior `build_evidence.py` extraction. Same bounded static require candidates, numeric payload export and existing proof mappings; missing metadata stays unknown.
- `scripts/evidence.py`: parameterized prior citation audit/normalization. It uses explicit registry IDs, checks hashes and ranges and records actual body certificate scope. No new code search or CFG proof algorithm.
- `scripts/reconstruct.py`: minimal repeated stage/manifest/package glue. It does not call a model, discover systems semantically or run Roblox.
- `scripts/semantic.py` (v1.1): minimal source-review freeze and obligation/coverage accounting. It verifies preserved observations/relationships, reciprocal rule IDs, function denominators, content dispositions and UNKNOWN investigation records. Agents author and judge these records; this helper performs no search, call-graph construction, natural-language entailment or automatic semantic discovery.
- `scripts/literal_tables.py`: optional, narrow reuse of the tested linear literal-table assembler. Example: `python scripts/literal_tables.py --input EXISTING_ORIGINAL_JSON --source-label SOURCE --out NEW_DIR --self-test`. It rejects unsupported instructions. Its output does not grant a function/module certificate.

Do not ship prior game specifications, game-specific formula tests, hardcoded source corrections, fixed system catalogues or raw builds as the portable skill. Those are validation fixtures/results in the separate provenance bundle. The original renderer/packager's elaborate styling is not necessary for reproduction; the package must make its checked evidence and implementation contracts inspectable and relocation-safe.

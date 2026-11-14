# Install and run

Extract the distribution and copy the entire `roblox-game-reconstruction` folder into the agent's skills directory (normally `%USERPROFILE%\.codex\skills\roblox-game-reconstruction` on this Windows host). Preserve an existing installation separately. The skill is usable from its extracted path as well.

Requirements: Python 3.9+ with its standard library, shell/file tools and an agent capable of reading Luau. Concurrent and independent workers require the host's subagent capability; the CLI does not spawn AI models. No API keys, native build, Python runtime packages, game login or network access are required by this skill. Optional skill-frontmatter validation is a development check requiring PyYAML in the validator environment.

Invoke the agent with:

> Use $roblox-game-reconstruction on the recovered repository at INPUT. Produce a system specification with parallel analysis, independent cross-review, evidence audit and a package at OUTPUT. Keep unknown server/instance behavior explicit.

The agent reads SKILL.md and executes the full workflow. CLI helpers are resumable mechanical stages, not an automatic substitute for agent analysis:

```powershell
python scripts/reconstruct.py prepare --repo INPUT --run work/new-analysis
# Agent discovers systems, writes catalog/plan and assigns analysis workers.
# Separate scout reads raw source before claims, writes source-review/v1 JSON.
python scripts/reconstruct.py freeze-review --run work/new-analysis --id scout-01 --input work/scout-source-first.json
# Compare frozen obligations with claims, integrate semantic-coverage.json,
# reciprocal claim semantic_refs, function scope, content and UNKNOWN audits.
python scripts/reconstruct.py audit --run work/new-analysis
python scripts/reconstruct.py package --run work/new-analysis --out outputs/new-spec
python scripts/reconstruct.py validate --package outputs/new-spec
python -m unittest discover -s tests -v
```

Use a new run/output directory for a different input. On resume, preserve source hashes and existing findings; do not silently combine incompatible input versions. Missing CFG metadata remains UNVERIFIED. Insufficient input, worker availability, context or source data must be reflected in coverage and the final result.

V1.1 packages distinguish PARTIAL from IN_SCOPE_REVIEWED and legacy NOT_ASSESSED. A pass means the declared evidence/accounting checks passed. An agent still judges semantic mappings, discovers omitted source rules and reports unavailable server behavior. See references/completeness.md for the exact ledger and independent source-first protocol.

# Install and run

Extract the distribution and copy the entire `roblox-game-reconstruction` folder into the agent's skills directory (normally `%USERPROFILE%\.codex\skills\roblox-game-reconstruction` on this Windows host). Preserve an existing installation separately. The skill is usable from its extracted path as well.

Requirements: Python 3.9+ with its standard library, shell/file tools and an agent capable of reading Luau. Concurrent and independent workers require the host's subagent capability; the CLI does not spawn AI models. No API keys, native build, Python runtime packages, game login or network access are required by this skill. Optional skill-frontmatter validation is a development check requiring PyYAML in the validator environment.

Invoke the agent with:

> Use $roblox-game-reconstruction on the recovered repository at INPUT. Produce a system specification with parallel analysis, independent cross-review, evidence audit and a package at OUTPUT. Keep unknown server/instance behavior explicit.

The agent reads SKILL.md and executes the full workflow. CLI helpers are resumable mechanical stages, not an automatic substitute for agent analysis:

```powershell
python scripts/reconstruct.py prepare --repo INPUT --run work/new-analysis
# Agent discovers systems and writes catalog/plan.
python scripts/reconstruct.py scaffold --run work/new-analysis --kind analysis --system SYSTEM
# Worker completes analysis/SYSTEM, sets draft false, then validates its own output.
python scripts/reconstruct.py validate-worker --run work/new-analysis --kind analysis --system SYSTEM
# Separate scout receives raw source before claims. Repeat --module-id for its scope.
python scripts/reconstruct.py scaffold --run work/new-analysis --kind source-review --id scout-01 --module-id MODULE_ID --reviewer REVIEWER
# Scout completes review-drafts/scout-01.json and sets draft false.
python scripts/reconstruct.py validate-worker --run work/new-analysis --kind source-review --input work/new-analysis/review-drafts/scout-01.json
python scripts/reconstruct.py freeze-review --run work/new-analysis --id scout-01 --input work/new-analysis/review-drafts/scout-01.json
# Compare frozen obligations with claims, integrate semantic-coverage.json,
# reciprocal claim semantic_refs, function scope, content and UNKNOWN audits.
python scripts/reconstruct.py audit --run work/new-analysis
python scripts/reconstruct.py package --run work/new-analysis --out outputs/new-spec
python scripts/reconstruct.py validate --package outputs/new-spec
python -m unittest discover -s tests -v
```

Use a new run/output directory for a different input. On resume, preserve source hashes and existing findings; do not silently combine incompatible input versions. Missing CFG metadata remains UNVERIFIED. Insufficient input, worker availability, context or source data must be reflected in coverage and the final result.

V0.1.2 packages separate audit status from analysis_status and prominently report reviewed/total functions, material relations not traced and remaining questions. Preflight checks are also applied automatically before freeze. A pass means declared evidence/accounting checks passed; an agent still judges semantic mappings, discovers omitted source rules and reports unavailable behavior. See references/completeness.md.

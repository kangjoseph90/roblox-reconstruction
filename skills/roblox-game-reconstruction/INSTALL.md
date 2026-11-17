# Install and run

Copy the entire `roblox-game-reconstruction` folder into the host agent's configured skills directory. Preserve an existing installation separately. The skill is usable from its extracted path as well.

Requirements: Python 3.9+ with its standard library, shell/file tools and an agent capable of reading Luau. Concurrent and independent workers require the host's subagent capability; the CLI does not spawn AI models. No API keys, native build, Python runtime packages, game login or network access are required by this skill. Optional skill-frontmatter validation is a development check requiring PyYAML in the validator environment.

The input adapter requires source and bytecode SHA-256 identities on source-bearing records. Source-only exports without a bytecode identity are not accepted. Missing CFG metadata is allowed and stays UNVERIFIED.

Invoke the agent with:

> Use $roblox-game-reconstruction on the recovered repository at INPUT. Produce a system specification with parallel analysis, independent cross-review, evidence audit and a package at OUTPUT. Keep unknown server/instance behavior explicit.

The agent reads SKILL.md and executes the full workflow. CLI helpers are resumable mechanical stages, not an automatic substitute for agent analysis:

```powershell
python scripts/reconstruct.py prepare --repo INPUT --run work/new-analysis
# Agent discovers systems and writes catalog/plan; list modules not yet owned or excluded.
python scripts/reconstruct.py query modules --run work/new-analysis --unowned
python scripts/reconstruct.py check-plan --run work/new-analysis
python scripts/reconstruct.py scaffold --run work/new-analysis --kind analysis --system SYSTEM
# Look up registry rows instead of reading the large shared JSON files.
python scripts/reconstruct.py query functions --run work/new-analysis --system SYSTEM
# Worker completes analysis/SYSTEM, sets draft false, expands trace shorthand, then validates.
python scripts/reconstruct.py normalize-worker --run work/new-analysis --kind analysis --system SYSTEM
python scripts/reconstruct.py validate-worker --run work/new-analysis --kind analysis --system SYSTEM
# Separate scout receives raw source before claims. Repeat --module-id for its scope.
python scripts/reconstruct.py scaffold --run work/new-analysis --kind source-review --id scout-01 --module-id MODULE_ID --reviewer REVIEWER
# Scout completes review-drafts/scout-01.json and sets draft false.
python scripts/reconstruct.py validate-worker --run work/new-analysis --kind source-review --input work/new-analysis/review-drafts/scout-01.json
python scripts/reconstruct.py freeze-review --run work/new-analysis --id scout-01 --input work/new-analysis/review-drafts/scout-01.json
# Compare frozen obligations with claims; finish explicit mapping/review decisions.
python scripts/reconstruct.py scaffold --run work/new-analysis --kind integration
# Complete integration-decisions.json and set draft false. No semantic choices are automatic.
python scripts/reconstruct.py integrate --run work/new-analysis --input work/new-analysis/integration-decisions.json
python scripts/reconstruct.py audit --run work/new-analysis
python scripts/reconstruct.py package --run work/new-analysis --out outputs/new-spec
python scripts/reconstruct.py validate --package outputs/new-spec
python -m unittest discover -s tests -v
```

Use a new run/output directory for a different input. On resume, preserve source hashes and existing findings; do not silently combine incompatible input versions. Missing CFG metadata remains UNVERIFIED. Insufficient input, worker availability, context or source data must be reflected in coverage and the final result.

0.1.2 packages separate audit status from analysis_status and prominently report reviewed/total functions, material relations not traced and remaining questions. Preflight checks are also applied automatically before freeze. A pass means declared evidence/accounting checks passed; an agent still judges semantic mappings, discovers omitted source rules and reports unavailable behavior. See references/completeness.md.

0.1.3 requires function-specific traced steps with own-body citations for every TRACED/REVIEWED entry. Generic rationales are insufficient. Audit accepts only runs prepared by this release; for a run from another release, prepare a new run instead of editing frozen reviews to manufacture support. The completion banner distinguishes supported traces, source-review traces, reviewed, surveyed and unanalysed functions. These are analysis records, not new CFG or behavior certificates.

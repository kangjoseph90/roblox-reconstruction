# Analyze a recovered repository

After `recover GAME`, have one analysis subagent read the entire `GAME/repository` and produce a whole-game design document. Keep interpretation in the agent's analysis, not in CLI checks. The repository contains client/shared code; label server behavior as inference.

## Map, analyze, check, follow up

1. Write a map of recovered modules:

   ```sh
   python scripts/rcr.py repo-map GAME/repository --out GAME/analysis/repo-map.md
   ```

2. Give one subagent `GAME/repository`, `GAME/analysis/repo-map.md`, and the prompt below. Ask it to work alone and cover the whole experience, not a single system.

3. Check citations and find modules the document does not mention:

   ```sh
   python scripts/rcr.py check-analysis GAME/repository GAME/analysis/DESIGN.md --map GAME/analysis/unmentioned.md
   ```

   Recovery keeps different logical paths separate, even when their code matches. At the same logical path, only observations with the same class, bytecode encoding and bytecode are merged; conflicting versions remain separate variant modules. Analyze each distinct path/version explicitly. For versioned modules, mention the versioned instance path in the document: shared function names alone do not satisfy the coverage check for variants. A resolving citation only shows that a path and line exist; it does not show that the cited code supports the claim. Citation-like text that is not exactly `path:LINE[-END]` is reported as `malformed_citation` rather than skipped: in backticks for example `` `path:12, 40` ``, a path containing `:`, or Roblox dot notation such as `` `ReplicatedStorage.Module:12` `` (unless that dotted name is a recovered path, such as a supplied `Main.client.luau`); unquoted, a second line number after a citation such as `path:2–9`, `path:2:9` or `path:2..9`. Use `path:2-9` for ranges; an en dash written directly between the numbers (`path:2–9`) is read the same way. Unquoted citations need slash-separated paths without spaces; a comma ends one. `repo-map` and the coverage counts leave out default player scripts (directly under `StarterPlayer/StarterPlayerScripts`, `StarterPlayer/StarterCharacterScripts` or `Players/*/PlayerScripts`) and modules without recovered source; find those in `repository/report.json`. `--map` and `repo-map --out` refuse to write over the input document or inside the recovered repository. Read the uncovered map, assess whether meaningful gameplay/configuration scope remains, and check important claims against their cited source lines.

4. If meaningful scope remains, use a fresh subagent for the follow-up. Provide the document body, `GAME/analysis/unmentioned.md`, and the areas that appear uncovered. Ask for only new or corrected items as an addendum, then run `check-analysis` on the addendum too. Update analysis claims only where the recovered evidence supports the change.

After adding a Place, apply `recovery.json`'s `citation_remaps` (and any `earlier_changes`) before checking old citations. Recheck line citations into modules listed in `changed_sources`. If a new natural module occupies a previous variant path, the old citation may still resolve to that different module. Keep line numbers when the before/after source hashes match. When they differ, check the cited code and update the line or claim as needed. Use `changes.added_modules` and the updated map to limit the follow-up to the affected code.

## Whole-game analysis prompt

```markdown
# Task: whole-game design analysis from recovered client code

Analyze the whole experience in the recovered repository. Cover gameplay systems, progression and reset layers, economy formulas and values, unlock conditions, timers, quests, collectibles, events, minigames, monetization, and client/server interfaces relevant to design. Prefer exact formulas and numbers when the code supports them.

## Inputs
- Recovered sources: [GAME]/repository/ (report.json maps each instance_path to its source file and directory).
- Module map: [GAME]/analysis/repo-map.md.

## Rules
- Work alone; do not start additional subagents.
- Read sources directly. Do not treat decompiled code as Place-discovery evidence.
- The capture is client/shared scope. Mark server-side behavior as inference and distinguish observed code from unknown behavior.
- Preserve uncertainty: syntax and CFG evidence do not prove runtime behavior.

## Output
Write [DESIGN.md] in [LANGUAGE], organized by system. Support formulas, values and conditions with citations in the form instance/path/of/module:LINE, where LINE is from the recovered source file. State confidence and evidence scope.

Citation format: write the full instance path every time, with no `...` or bare module names, as `ReplicatedStorage/Modules/Shop:12` or `ReplicatedStorage/Modules/Shop:12-20`. Give one line or one range per citation; for several ranges repeat the full path (`ReplicatedStorage/Modules/Shop:12-20`, `ReplicatedStorage/Modules/Shop:40`) instead of listing `:12-20, 40`. Cite only recovered modules, not report.json or other metadata files.
```

## Fresh follow-up prompt

```markdown
# Task: identify important omissions or errors in a game design analysis

Review [DESIGN_BODY.md] against the recovered repository. Find important gameplay systems, rules, formulas, values, unlock conditions, schedules, collectibles, events, monetization, or interfaces that the document misses or states incorrectly.

## Inputs
- Recovered sources: [GAME]/repository/.
- Modules not mentioned by the document: [GAME]/analysis/unmentioned.md. This map includes incidental code; do not limit your search to it.
- Areas that appear uncovered: [AREAS].

## Rules
- Work alone; do not start additional subagents.
- The sources cover client/shared code. Mark server-side behavior as inference.
- Cite source lines and preserve uncertainty. A resolving citation alone does not prove a claim.

## Output
Write [ADDENDUM.md] with only new or corrected findings. For each item give the system, rule, evidence in the form instance/path/of/module:LINE, and confidence.

Citation format: write the full instance path every time, with no `...` or bare module names, as `ReplicatedStorage/Modules/Shop:12` or `ReplicatedStorage/Modules/Shop:12-20`. Give one line or one range per citation; for several ranges repeat the path instead of listing numbers after a comma. Cite only recovered modules, not report.json or other metadata files.
```

## Interpretation limits

The map and citation checker are mechanical aids. They do not determine which modules are important, whether a line supports an interpretation, or whether the game behaves as code suggests. Captures do not establish server behavior, execution, complete replication, or equivalence across Places unless separately evidenced.

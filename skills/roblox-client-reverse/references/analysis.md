# Analysing a recovered repository

A recovered `repository/` is ordinary source that an agent can read directly. One subagent asked for the whole game in one pass, with evidence citations, writes a broad and accurate design document. `check-analysis` then checks the citations and lists modules the document never mentions; a second subagent fills real gaps.

## Workflow

The main agent collects and recovers the repository, then delegates analysis:

1. **Map.** Write a folder map of the distinct recovered modules:

   ```sh
   python scripts/rcr.py repo-map RUN_ROOT --out RUN_ROOT/repo-map.md
   ```

2. **Analysis subagent.** Start one subagent with the analysis prompt below and the map. Ask for the whole game; a narrow request (for example "the economy") leaves whole systems out. The subagent works alone and does not start subagents of its own.

3. **Check.** When it returns:

   ```sh
   python scripts/rcr.py check-analysis RUN_ROOT DESIGN.md --map RUN_ROOT/unmentioned.md
   ```

   - `citations`: each `instance/path:LINE` or `instance/path:START-END` (in backticks, or unquoted without spaces) must name a recovered script and a line inside its `init.luau`. Any invalid citation makes the status `invalid_citations` (exit 2); have the subagent fix those lines.
   - `coverage`: distinct modules (one per bytecode, Roblox default player scripts excluded) whose name, function names or table keys do not appear in the document. `--map` writes them as a folder map; `--full` lists every module in the JSON output.

4. **Decide.** Read the unmentioned map. Effects, UI plumbing, utilities, vendored packages and admin tooling are expected to remain. If game logic or configuration remains (data tables, quests, collectibles, products, events), run a follow-up pass.

5. **Follow-up subagent.** Start a new subagent, not the one that wrote the document: independent runs explore different parts of the code, so a fresh pass finds more than the same agent continuing. Give it the document body without bulky appendices, the unmentioned map and one line naming the areas that look uncovered. It writes an addendum; run step 3 on the addendum as well.

Mentioning a module is not proof that it was analysed, and a citation that resolves is not proof that the line supports the claim. Spot-check important formulas against the cited lines. Use the scripts' syntax and CFG states in `report.json` to judge how far a decompiled function can be trusted. Results vary between runs; the check exists to catch what one run missed.

## Analysis prompt

Replace the bracketed parts.

```markdown
# Task: game design document of a Roblox experience (from decompiled client code)

Write a reverse-engineered game design document covering the WHOLE game: every gameplay system, progression and reset layer, economy formulas and numbers, unlock conditions, timers and schedules, quests, collectibles, events, minigames, monetization (products, offers, passes, ads), and client/server interfaces that matter for design. Prefer exact formulas and numbers over prose.

## Inputs
- Decompiled client/shared Luau sources: `[RUN_ROOT]/repository/` (`report.json` maps each `instance_path` to its `directory`; each directory has `init.luau`). Server scripts were not collected; mark server-side inferences as such.
- A map of the repository (distinct module counts per folder and their most common names): `[RUN_ROOT]/repo-map.md`. Use it to plan coverage.

## Rules
- Work alone; do not start subagents.
- Time box: about [12] minutes of exploration, then write the document.

## Output
Write `[DESIGN.md]` in [language], organized by system. Give evidence for formulas, numbers and conditions as `instance/path/of/module:LINE` (line in that module's init.luau).
```

## Follow-up prompt

```markdown
# Task: find what a game design document missed

`[DESIGN_BODY.md]` was written from the decompiled client code of a Roblox experience. Find important game-design content it misses or states wrongly: whole systems, rules, formulas, numbers, unlock conditions, schedules, collectibles, events, monetization.

## Inputs
- Decompiled client/shared Luau sources: `[RUN_ROOT]/repository/` (`report.json` maps each `instance_path` to its `directory`; each directory has `init.luau`). Server scripts were not collected; mark server-side inferences as such.
- Modules the document never mentions, as a folder map: `[RUN_ROOT]/unmentioned.md`. It includes incidental code. [Areas that look uncovered: ...] Use the map as a reference; do not limit the search to it.

## Rules
- Work alone; do not start subagents.
- Time box: about [8] minutes of exploration, then write the addendum.

## Output
Write `[ADDENDUM.md]` in [language] with only new or corrected items. For each: system, rule, evidence as `instance/path/of/module:LINE`, and confidence.
```

## Limits

The repository holds client and shared code only. Server calculations, persistence and validation are not collected; describe what the client sends, receives and displays, and label conclusions about the server as inferences.

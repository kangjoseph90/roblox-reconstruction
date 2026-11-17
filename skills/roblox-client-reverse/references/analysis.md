# Analysing a recovered repository

A recovered `repository/` is ordinary source that an agent can read directly. A single agent asked for the whole game in one pass, with evidence citations, gives broad and accurate design documents; `check-analysis` then checks the citations and lists modules the document never mentions.

## Workflow

1. Recover a repository with `run`, `replay` or `extract`.
2. Give one agent the prompt below. Ask for the whole game; a narrow request (for example "the economy") leaves whole systems out.
3. Run the mechanical check:

   ```sh
   python scripts/rcr.py check-analysis RUN_ROOT DOCUMENT.md
   ```

   - `citations`: each `` `instance/path:LINE` `` or `` `instance/path:START-END` `` must name a recovered script and a line inside its `init.luau`. Any invalid citation makes the status `invalid_citations` (exit 2).
   - `coverage`: distinct modules (one per bytecode, Roblox default player scripts excluded) whose name, function names or table keys do not appear in the document, grouped by folder and listed largest first. `--full` lists all of them.

4. Read the unmentioned folders. If they hold game logic or configuration rather than effects, UI plumbing and utilities, run one follow-up pass with the same agent: "find what this document missed", pointing at the existing document. Stop when the remaining modules are incidental.

Mentioning a module is not proof that it was analysed, and a citation that resolves is not proof that the line supports the claim. Spot-check important formulas against the cited lines. Use the scripts' syntax and CFG states in `report.json` to judge how far a decompiled function can be trusted.

## Prompt

Replace the bracketed parts.

```markdown
# Task: game design document of a Roblox experience (from decompiled client code)

Write a reverse-engineered game design document covering the WHOLE game: every gameplay system, progression and reset layer, economy formulas and numbers, unlock conditions, timers and schedules, quests, collectibles, events, minigames, monetization (products, offers, passes, ads), and client/server interfaces that matter for design. Prefer exact formulas and numbers over prose.

## Inputs
- Decompiled client/shared Luau sources: `[RUN_ROOT]/repository/` (`report.json` maps each `instance_path` to its `directory`; each directory has `init.luau`). Server scripts were not collected; mark server-side inferences as such.

## Rules
- Time box: about [12] minutes of exploration, then write the document.

## Output
Write `[DOCUMENT.md]` in [language], organized by system. Give evidence for formulas, numbers and conditions as `instance/path/of/module:LINE` (line in that module's init.luau).
```

The follow-up pass uses the same inputs and output rules with this task: "The document `[DOCUMENT.md]` was written from this source. Find important game-design content it missed or states wrongly — whole systems, rules, formulas, numbers, unlock conditions, schedules, monetization — and write only those items to `[ADDENDUM.md]`."

## Limits

The repository holds client and shared code only. Server calculations, persistence and validation are not collected; describe what the client sends, receives and displays, and label conclusions about the server as inferences.

# Computer Use bridge

The Python CLI starts the normal Roblox experience URI, then pauses. Use an actual host Computer Use tool to observe and operate the Player. A shell launch is not evidence of game entry. Do not manufacture a UI observation from a PID or memory scan.

On Codex Windows with the bundled Computer Use plugin, read that plugin's SKILL.md and its guidance/API/confirmations documents first. Discover the `node_repl` tool, then initialize:

```javascript
if (!globalThis.sky) { const { sky } = await import('@oai/sky'); globalThis.sky = sky; }
globalThis.rcrWindows = await sky.list_windows();
nodeRepl.write(JSON.stringify(rcrWindows.filter(w => /RobloxPlayerBeta\.exe/i.test(w.app))));
```

Use returned app/window objects. Require exactly one relevant window or resolve ambiguity from returned objects; never invent a handle. Get and activate it, then observe a fresh screenshot:

```javascript
globalThis.rcrWindow = await sky.get_window({id: ACTUAL_RETURNED_ID, app: ACTUAL_RETURNED_APP});
await sky.activate_window({window:rcrWindow});
globalThis.rcrState = await sky.get_window_state({window:rcrWindow,include_screenshot:true,include_text:true});
nodeRepl.write(JSON.stringify(rcrState.accessibility));
```

Observe and stop before choosing input. For subsequent actions use only the latest returned element index or screenshot ID, perform one action, and refresh. Use normal navigation only as needed to join the requested game. Do not automate a terminal or Windows Run dialog, authentication/security dialogs, purchases, or reward claims. An app/browser warning does not authorize a bypass.

After visually observing the actual game scene, use the normal filesystem tool to write `ui-confirmed.json` at the path reported in launch.json. Record current UTC ISO time, numeric requested Place ID, `game_scene_visible:true`, and the actual returned `window.id`/`window.app`. A screenshot may be preserved as evidence when the host supports it; it is not required. The CLI additionally requires live DataModel Place ID equality. For a visible terminal error, record false and `terminal_error` instead, then report the actual blocker.

No hardcoded Codex tool name is required in the CLI: another host may supply a different Computer Use tool, but must satisfy the same observation contract. No Computer Use tool means live run cannot satisfy its prerequisite; use offline modes and report that limitation.

When another provider returns an app name/bundle ID plus PID rather than an executable path, keep its original `returned_app`, window ID and provider name. Resolve only that returned PID to its actual image using `compatibility.running_players()` (read-only process-image query), and record both the mapping and that image path as `window.app`. Do not append a guessed `.exe` suffix or invent a window handle. The screenshot still must be visually inspected; a PID or memory read is not game-entry evidence. The collector independently checks the selected image and target identity.

If a requested isolated build's normal updater starts a different executable, preserve requested/observed image hashes and record `terminal_error`. A rendered game in that other build does not verify the requested build. Do not disable update/authentication/security checks. A host interruption may leave a nonterminal state file; it is an interrupted attempt, not a successful capture, and should be preserved before starting a new output.

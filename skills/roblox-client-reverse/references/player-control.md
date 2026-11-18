# Windows Player control

The Player commands do not need Computer Use or native recovery builds. They request normal launch/shutdown and report observable process/window facts. Inspect the actual screenshot yourself before considering a game entered.

```sh
python scripts/rcr.py players
python scripts/rcr.py open PLACE_ID --game GAME
python scripts/rcr.py screenshot --pid PID --out IMAGE.png
python scripts/rcr.py close --pid PID
```

The standard workflow discovers Places first, opens the input Place first, and inspects the screenshot. If entry/access fails, record the result and continue to another Place when appropriate. Do not repeatedly ask the user or automate login, unlock, purchase, or other account actions. Close only a Player owned by the workflow. A Player the user entered manually remains open.

## Optional screenshot dependencies

Listing, opening and closing use the Python standard library. For screenshots, install `requirements-windows.txt` from the skill directory. The pinned screenshot package supports Python 3.9–3.12 on Windows x64. It uses Windows Graphics Capture on the selected HWND; unsupported environments fail instead of falling back to desktop screenshots, title matching or coordinate crops.

## `players`

Returns observed Player PIDs, executable paths, process creation ticks and visible top-level windows, including each HWND. Creation ticks distinguish PID reuse. Read errors are reported. No matching Player is a valid empty inventory. A visible window does not prove entry or identify a Place.

## `open PLACE_ID --game GAME`

The Place must be listed in `GAME/places.json`. The command requests launch through the normal Roblox URI handler, optionally with `--player-exe PATH`; it leaves update and launcher behavior enabled. It identifies newly observed Player process/window state and records an entry summary. `pass` means the new process/window was identified, not that the Place opened or became accessible. A launch/access failure remains explicit; no login or access-control flow is automated.

## `screenshot --pid PID --out IMAGE.png`

The PID must currently identify `RobloxPlayerBeta.exe`. The process handle binds the operation to that process. One eligible visible HWND is selected; use `--hwnd HWND` from `players` if there is more than one. Windows Graphics Capture targets the HWND without bringing it forward or sending input. Hidden/minimized windows, loss/replacement, missing frames or blank content fail. The blank-content check is heuristic and a successful image still does not establish Place identity.

Success writes a PNG and `IMAGE.png.json` sidecar containing process/window identity, capture method, frame data, timestamps and image SHA-256. Existing output paths are rejected. Read and interpret the returned image before capture.

## `record-entry --game GAME --place PLACE_ID --outcome OUTCOME`

After inspecting the screenshot, record `loaded_scene`, `access_denied`, or `home_screen` with `--screenshot IMAGE.png`. A `timed_out` observation can be recorded without an image. Use `--error-code` with `access_denied` only when the screen shows a code, and `--detail` for a short observation. The command saves `entry.observation` in `places.json` and preserves the launch and capture statuses. A positive observation confirms a game scene. Capture identifies the actual Place and records redirects separately.

Keep the PNG with its sidecar. The command checks the screenshot hash, time and identified Player where available. Save evidence inside GAME to keep its reference relative. A new `open` attempt clears the prior observation for that Place. A later manual visit and capture leave the direct entry observation intact.

## `close --pid PID [--force]`

The normal path sends `SC_CLOSE` to the selected Player's windows and waits for that process to exit. If normal shutdown fails, it reports failure and leaves the process alone. Explicit `--force` permits termination of that selected process after rechecking identity. Use this only for a Player launched by the current workflow.

## Capture and later manual entry

When a Place was not initially accessible but the user later enters it manually, run `players`, take and inspect a screenshot, then capture the existing PID. Do not relaunch that Place. Recover the same GAME to merge the newly captured Place, refresh `repo-map`, and make an analysis addendum. Leave the user's Player open.

JSON results distinguish command success from game entry, raw capture, recovered source and behavioral evidence. Exit 2 denotes a failed/partial operation; exit 1 denotes invalid invocation or a configuration error.

# Install and use

Copy the complete `roblox-client-reverse` skill directory into the agent's skills directory. Keep its `assets/`, `references/`, `tests/`, `agents/`, and `scripts/` directories together. Python 3.9+ is required. Native recovery builds additionally need Git and network access to the pinned upstream repositories, plus `make` and `clang++` on macOS/Linux, or CMake with Visual Studio 2022 or later (Desktop development with C++ and a Windows SDK) on Windows. `scripts/bootstrap_tools.py` declares the upstream revisions; the bundled `assets/` directory carries the patch, preserved source, hashes and the [decompiler source notice](assets/LUAUDEC-NOTICE.md) used by `setup`.

Create an environment from the skill directory:

```powershell
py -3 -m venv work\.venv
$py = ".\work\.venv\Scripts\python.exe"
& $py -m pip install -r requirements.txt
& $py scripts\rcr.py doctor
```

Build native tools only when needed for recovery or regression:

```powershell
& $py scripts\rcr.py setup --tools-dir work\.rcr-tools
```

The commands below write `python` for the environment's interpreter (`$py` above, or `work/.venv/bin/python` on macOS/Linux).

`setup` records local executable paths and hashes in `tools.json`. Keep that file with its local tools directory and rebuild after moving the directory or changing pinned sources; a cached `tools.json` built from other revisions is refused. If `setup` stops part-way (for example on a network error), it leaves `.rcr-setup-in-progress` and a later `setup` into that directory refuses; delete the directory (or choose a new `--tools-dir`) and run it again. `setup` never deletes files. Do not commit generated tools or machine-specific configuration. The skill's `work/` directory is a local workspace: it is not part of the shipped package. See the [native decompiler](references/decompiler.md) for lifting, output and test coverage.

## Windows Player screenshots

Player listing, launch requests and close use the Python standard library. For HWND-targeted screenshots on Windows x64, install the optional dependencies. They need Python 3.9–3.12, so create the environment with one of those versions (for example `py -3.12 -m venv work\.venv`):

```powershell
& $py -m pip install -r requirements-windows.txt
```

The screenshot backend uses Windows Graphics Capture. It does not fall back to desktop screenshots or coordinate crops. A screenshot confirms what was visibly rendered in the selected window; it does not prove the current Place identity. See [player-control.md](references/player-control.md).

## Basic workflow

`GAME` is a game directory; keep it outside the skill directory (or under its unshipped `work\`), because the skill directory is the installable package.

```powershell
python scripts\rcr.py places PLACE_ID --out GAME
python scripts\rcr.py players
python scripts\rcr.py open PLACE_ID --game GAME
python scripts\rcr.py screenshot --pid PID --out GAME\evidence\PLACE_ID-entry.png
python scripts\rcr.py record-entry --game GAME --place PLACE_ID --outcome OBSERVED_OUTCOME --screenshot GAME\evidence\PLACE_ID-entry.png
python scripts\rcr.py capture --pid PID [--place PLACE_ID] --game GAME
python scripts\rcr.py close --pid PID --game GAME --place PLACE_ID
python scripts\rcr.py recover GAME --tools work\.rcr-tools\tools.json
python scripts\rcr.py validate GAME
```

The input Place is attempted first. Choose `OBSERVED_OUTCOME` after inspecting the screenshot and capture only if a game scene loaded. Screenshots used as entry evidence must be saved inside GAME. `close` acts only on the Player process that `open` recorded as a new process for that Place (same PID and creation time); it sends a normal `SC_CLOSE` request, with explicit `--force` available if normal shutdown fails. Without `--tools` or `--tools-dir`, `recover GAME` builds the pinned tools under `GAME/.tools`, which needs the build prerequisites above and network access. Details and failure semantics are in [SKILL.md](SKILL.md), [places.md](references/places.md), and [player-control.md](references/player-control.md).

To inspect an accessible artifact without recovering it, use `fetch TARGET --out DIR` or `inspect FILE`. Fetch downloads only. To recover a supplied local file or raw capture, use `recover INPUT --out DIR`; optional offline settings include `--tools`, `--tools-dir`, `--include-server`, and `--bytecode-format`. Older supplied raw capture layouts are accepted as input adapters, not as separate commands. See [acquisition.md](references/acquisition.md).

## Development checks

From the skill directory, run the authored semantic suite and unit suite using the environment's tools. `RCR_TEST_TOOLS` enables the native CFG, normalizer and compiler tests; without it they are skipped:

```powershell
python tests\semantic\run_suite.py --tools work\.rcr-tools\tools.json --out work\semantic.json
$env:RCR_TEST_TOOLS = "work\.rcr-tools\tools.json"
python -m unittest discover -s tests -v
```

Runtime Python packages are pinned in `requirements.txt`. The screenshot frame tests additionally need NumPy and OpenCV and are skipped without them. CI builds pinned native tools and runs unit and authored semantic suites on Linux and Windows. Authored behavior fixtures and their outputs are distinct from recovered game behavior; the VM never executes recovered game code.

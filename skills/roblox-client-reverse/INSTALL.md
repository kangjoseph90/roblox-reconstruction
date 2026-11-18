# Install and use

Copy the complete `roblox-client-reverse` skill directory into the agent's skills directory. Keep its `assets/`, `references/`, `tests/`, `agents/`, and `scripts/` directories together. Python 3.9+ is required. Native recovery builds additionally need Git, CMake, and a C++ toolchain; on Windows use Visual Studio 2022 or later with Desktop development with C++ and a Windows SDK. `scripts/bootstrap_tools.py` declares the upstream revisions; the bundled `assets/` directory carries the patch, preserved source, hashes and notices used by `setup`.

Create an environment from the skill directory:

```powershell
py -3 -m venv work\.venv
& .\work\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\work\.venv\Scripts\python.exe scripts\rcr.py doctor
```

Build native tools only when needed for recovery or regression:

```powershell
& .\work\.venv\Scripts\python.exe scripts\rcr.py setup --tools-dir work\.rcr-tools
```

`setup` records local executable paths and hashes in `tools.json`. Keep that file with its local tools directory and rebuild after moving the directory or changing pinned sources. Do not commit generated tools or machine-specific configuration.

## Windows Player screenshots

Player listing, launch requests and close use the Python standard library. For HWND-targeted screenshots on Windows x64, install the optional dependencies (Python 3.9–3.12):

```powershell
& .\work\.venv\Scripts\python.exe -m pip install -r requirements-windows.txt
```

The screenshot backend uses Windows Graphics Capture. It does not fall back to desktop screenshots or coordinate crops. A screenshot confirms what was visibly rendered in the selected window; it does not prove the current Place identity. See [player-control.md](references/player-control.md).

## Basic workflow

```powershell
python scripts\rcr.py places PLACE_ID --out GAME
python scripts\rcr.py players
python scripts\rcr.py open PLACE_ID --game GAME
python scripts\rcr.py screenshot --pid PID --out player.png
python scripts\rcr.py capture --pid PID [--place PLACE_ID] --game GAME
python scripts\rcr.py close --pid PID
python scripts\rcr.py recover GAME
python scripts\rcr.py validate GAME
```

The input Place is attempted first. Inspect the actual screenshot before capturing. Only close a Player launched by the current workflow; close sends a normal `SC_CLOSE` request, with explicit `--force` available if normal shutdown fails. Capture stores raw observations and never decompiles. Existing Place captures are not silently replaced. Details and failure semantics are in [SKILL.md](SKILL.md), [places.md](references/places.md), and [player-control.md](references/player-control.md).

To inspect an accessible artifact without recovering it, use `fetch TARGET --out DIR` or `inspect FILE`. Fetch downloads only. To recover a supplied local file or raw capture, use `recover INPUT --out DIR`; optional offline settings include `--tools`, `--tools-dir`, `--include-server`, and `--bytecode-format`. Older supplied raw capture layouts are accepted as input adapters, not as separate commands. See [acquisition.md](references/acquisition.md).

## Development checks

From the skill directory, run the authored semantic suite and unit suite using the environment's tools:

```powershell
python tests\semantic\run_suite.py --tools work\.rcr-tools\tools.json --out work\semantic.json
python -m unittest discover -s tests -v
```

The frontmatter validator needs PyYAML in its development environment. Runtime Python packages are pinned in `requirements.txt`. CI builds pinned native tools and runs unit and authored semantic suites on Linux and Windows. Authored behavior fixtures and their outputs are distinct from recovered game behavior; the VM never executes recovered game code.

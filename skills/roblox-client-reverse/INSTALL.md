# Install and reproduce

1. Extract the package and place the whole `roblox-client-reverse` directory in the agent's skills directory, normally `%USERPROFILE%\.codex\skills\roblox-client-reverse`. Preserve `assets/`, `references/`, `tests/`, `agents/` and `scripts/`. Do not merge over an older installation silently; retain it separately.
2. Install Python 3.9+ x64. For native builds, install Git, Visual Studio 2022 with **Desktop development with C++** and its Windows SDK/CMake component. These prerequisites are not installed or changed by this skill.
3. From the skill directory, create a local environment without changing PowerShell execution policy:

   ```powershell
   py -3 -m venv work\.venv
   & .\work\.venv\Scripts\python.exe -m pip install -r requirements.txt
   & .\work\.venv\Scripts\python.exe scripts\rcr.py doctor
   & .\work\.venv\Scripts\python.exe scripts\rcr.py setup --tools-dir work\tools
   & .\work\.venv\Scripts\python.exe scripts\rcr.py regression --out work\regression --tools work\tools\tools.json
   & .\work\.venv\Scripts\python.exe scripts\rcr.py regression --dataset place-189707 --out work\second-regression --tools work\tools\tools.json
   & .\work\.venv\Scripts\python.exe tests\semantic\run_suite.py --tools work\tools\tools.json --out work\semantic.json
   ```

4. Invoke `$roblox-client-reverse` with a game URL. The agent follows SKILL.md and supplies the Computer Use observation while one `run` CLI process stays active. A shell-only invocation cannot independently observe GUI login/loading/error states.

Runtime Python dependencies are pinned in `requirements.txt` (`lz4`, `zstandard`). Tests use `unittest`. The frontmatter validator additionally needs PyYAML in the environment that runs that development tool; it is not a runtime dependency.

Tool pins: Luau `f46d879e28302a430029304f7f6a162aed66f103`; luaudec `6d0a7a2ef292171f306bf022ad56362139ac738e` plus `assets/luaudec-v0.3.3.patch`. `setup` fetches these pins, checks/applies the patch, verifies patched source hashes against the preserved source, builds Windows x64 with MSVC `/utf-8`, and runs native CFG/emitter invariant tests. Compiler, decompiler and authored-test VM paths/hashes are written into a machine-local `tools.json`. Regenerate it after moving machines; use a new tools directory for a version update. Setup is automatic on `run`/`replay` if `--tools` is omitted. Cache reuse checks both executable and patch hashes. Modified source is preserved in `assets/luaudec-source/`; the upstream decompiler pin contains no license file, and no license has been invented for it. Luau retains its upstream MIT license.

Unit checks never join a game. Set `RCR_TEST_TOOLS` to an absolute local tools.json path and run `python -m unittest discover -s tests -v` to enable native certificate negative controls and compiler integration checks. `regression` requires exact v0.3.3 payload states and source hashes; all previous 21 syntax failures now pass. The historical 19 unavailable Sell Lemons records remain unavailable and unclassified. Authored semantic tests alone run code under the VM; real game candidates are only compiled and statically compared. Rebuild from a new directory after a failed/incomplete setup.

`references/compatibility.json` retains historical build evidence and class-metadata hints. v0.3.4 discovers object fields rather than accepting the baseline layout. An unlisted Windows x64 hash is not rejected on version alone; structural ambiguity, insufficient witnesses, unavailable independent identity, unreadable data or unsupported ABI/encoding stop collection. Read [adaptive-builds.md](references/adaptive-builds.md) before a live run. Cold discovery and final enumeration each have the configured scan budget. Offline readers/recovery remain unchanged.

To reuse a verified structure across fresh runs, add `--structure-cache work/structures` to `run` or `acquire --backend player`. Every cache entry is keyed by executable SHA-256 and is revalidated in the current process. Copying an entry or a matching checksum never authorizes its offsets. To test an isolated official Player installation, use `--player-exe PATH`; the exact executable receives the normal URI, and normal updater behavior remains enabled. If it starts another build, record the transition rather than claiming the requested build was tested. Do not modify or remove the updater to force a comparison.

For CFG assumptions, behavior fixtures and adversarial checks, read [references/verification-v0.3.3.md](references/verification-v0.3.3.md). Both native patches and their source are preserved in assets/. v0.3.4 changes Python collection/validation orchestration; native v0.3.3 source and tools are intentionally reused without modification. Live calibration is observational and does not establish source semantics or execution.


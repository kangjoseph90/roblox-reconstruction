# Roblox Reconstruction

An agent skill for recovering Roblox client code into a source repository and checking analysis documents written from it.

| Component | Purpose | Version |
|---|---|---|
| [roblox-client-reverse](skills/roblox-client-reverse/SKILL.md) | Client bytecode collection, Luau decompilation, CFG validation and analysis checks | 0.4.1 |
| [Native tools](native/README.md) | Luau compiler and patched luaudec sources | [Revisions](native/UPSTREAM.json) |

## Installation

Copy `skills/roblox-client-reverse` into your agent's skills directory and follow its [installation guide](skills/roblox-client-reverse/INSTALL.md).

Python 3.9+ is required. Windows collection also requires Roblox Player and a Computer Use provider. See [native/](native/README.md) for native build instructions.

## Usage

```sh
python skills/roblox-client-reverse/scripts/rcr.py --help
```

Recover a repository, write its folder map with `rcr.py repo-map`, and give both to an analysis subagent with the prompt in [references/analysis.md](skills/roblox-client-reverse/references/analysis.md). `rcr.py check-analysis` then checks the document's citations and maps the modules it never mentions for an optional follow-up subagent.

The earlier `roblox-game-reconstruction` skill (multi-stage traced specifications) was removed after 0.1.5; its last release is the git tag `roblox-game-reconstruction-0.1.5`.

## Development

See [AGENTS.md](AGENTS.md) for development conventions. Test commands and their prerequisites are documented in the skill's installation guide.

# Roblox Reconstruction

An agent skill for recovering Roblox client code into a source repository and checking analysis documents written from it.

| Component | Purpose | Version |
|---|---|---|
| [roblox-client-reverse](skills/roblox-client-reverse/SKILL.md) | Client bytecode collection, Luau decompilation, CFG validation and analysis checks | 0.4.0 |
| [Native tools](native/README.md) | Luau compiler and patched luaudec sources | [Revisions](native/UPSTREAM.json) |

## Installation

Copy `skills/roblox-client-reverse` into your agent's skills directory and follow its [installation guide](skills/roblox-client-reverse/INSTALL.md).

Python 3.9+ is required. Windows collection also requires Roblox Player and a Computer Use provider. See [native/](native/README.md) for native build instructions.

## Usage

```sh
python skills/roblox-client-reverse/scripts/rcr.py --help
```

Recover a repository, give it to an agent with the prompt in [references/analysis.md](skills/roblox-client-reverse/references/analysis.md), then run `rcr.py check-analysis` on the resulting document to check its citations and list modules it never mentions.

The earlier `roblox-game-reconstruction` skill (multi-stage traced specifications) was removed after 0.1.5; its last release is the git tag `roblox-game-reconstruction-0.1.5`.

## Development

See [AGENTS.md](AGENTS.md) for development conventions. Test commands and their prerequisites are documented in the skill's installation guide.

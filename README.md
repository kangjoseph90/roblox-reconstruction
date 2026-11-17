# Roblox Reconstruction

Agent skills for Roblox client-code recovery and source-backed game-system reconstruction.

| Component | Purpose | Version |
|---|---|---|
| [roblox-client-reverse](skills/roblox-client-reverse/SKILL.md) | Client bytecode collection, Luau decompilation and CFG validation | 0.3.6 |
| [roblox-game-reconstruction](skills/roblox-game-reconstruction/SKILL.md) | Parallel system analysis, implementation specifications and evidence audits | 0.1.4 |
| [Native tools](native/README.md) | Luau compiler and patched luaudec sources | [Revisions](native/UPSTREAM.json) |

## Installation

Copy either directory under `skills/` into your agent's skills directory. Each skill includes its own instructions, dependencies and tests.

- [Client recovery setup](skills/roblox-client-reverse/INSTALL.md)
- [Reconstruction setup](skills/roblox-game-reconstruction/INSTALL.md)

Python 3.9+ is required. Windows collection also requires Roblox Player and a Computer Use provider. See [native/](native/README.md) for native build instructions.

## Usage

```sh
python skills/roblox-client-reverse/scripts/rcr.py --help
python skills/roblox-game-reconstruction/scripts/reconstruct.py --help
```

Use `roblox-client-reverse` to produce a recovered source repository, then provide it to `roblox-game-reconstruction`. Follow each skill's instructions for execution, required inputs and validation limits.

Reconstruction currently requires bytecode-backed records with source and bytecode SHA-256 identities. Source-only Studio/file imports are supported by client-reverse; reconstruction lists such records as unsupported missing modules rather than analysing them.

## Development

See [AGENTS.md](AGENTS.md) for the component boundaries and development conventions. Test commands and their prerequisites are documented in each skill's installation guide.

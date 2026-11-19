# Roblox Reconstruction

`roblox-client-reverse` is an installable agent skill for discovering Roblox Places through official APIs, controlling a Windows Player, saving raw client captures, recovering a source repository, and checking analysis documents.

| Component | Purpose | Version |
|---|---|---|
| [roblox-client-reverse](skills/roblox-client-reverse/SKILL.md) | Place discovery, Player control, raw capture, offline recovery and analysis checks | 0.6.3 |
| [Native tools](native/README.md) | Pinned Luau compiler and patched luaudec sources | [Revisions](native/UPSTREAM.json) |

## Install

Copy `skills/roblox-client-reverse` into the agent's skills directory and follow its [installation guide](skills/roblox-client-reverse/INSTALL.md). Python 3.9+ is required. Native tools are built from pinned sources when recovery is needed; Windows live capture requires Windows x64 and Roblox Player. Screenshots have a separate optional Windows dependency set.

## Workflow

```sh
python skills/roblox-client-reverse/scripts/rcr.py places PLACE_ID --out GAME
python skills/roblox-client-reverse/scripts/rcr.py players
python skills/roblox-client-reverse/scripts/rcr.py open PLACE_ID --game GAME
python skills/roblox-client-reverse/scripts/rcr.py screenshot --pid PID --out GAME/evidence/PLACE_ID-entry.png
python skills/roblox-client-reverse/scripts/rcr.py record-entry --game GAME --place PLACE_ID --outcome OBSERVED_OUTCOME --screenshot GAME/evidence/PLACE_ID-entry.png
python skills/roblox-client-reverse/scripts/rcr.py capture --pid PID --place PLACE_ID --game GAME
python skills/roblox-client-reverse/scripts/rcr.py close --pid PID --game GAME --place PLACE_ID
python skills/roblox-client-reverse/scripts/rcr.py recover GAME
```

The standard agent workflow uses official Universe/Place APIs to enumerate Places, opens the input Place first, checks a screenshot of the actual Player window, and captures raw buffers only after entry is observed. Access failures are recorded and the next listed Place may be tried. The workflow does not automate account unlocks, login, purchases or repeated user requests. See [Place discovery](skills/roblox-client-reverse/references/places.md) and [Player control](skills/roblox-client-reverse/references/player-control.md).

Recovery creates one `GAME/repository` from all available captures. For offline inputs, use `recover INPUT --out DIR`. Use `repo-map` and `check-analysis` with the analysis workflow in [analysis.md](skills/roblox-client-reverse/references/analysis.md). Syntax, function CFG, module CFG and observed behavior are separate evidence claims.

## Development

See [AGENTS.md](AGENTS.md) for development conventions, and the skill installation guide for test prerequisites. Native source pins, patches, hashes and license notices are documented in [native/](native/README.md).

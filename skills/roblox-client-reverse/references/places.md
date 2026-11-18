# Official Place discovery

```sh
python scripts/rcr.py places PLACE_ID_OR_GAME_URL --out GAME
```

This command queries official Universe/Place APIs, follows pagination, and writes `game.json` and `places.json`. It does not require a Player, credentials, Computer Use or native tools. Never use decompiled code or third-party lists as Place-ID discovery fallback.

The input Place is the first entry target. The discovered Universe catalogue can include other Places; each is an independent entry and capture attempt. Discovery success means only that the API listing was complete. It says nothing about permission to enter a Place, visibility of its game scene, or successful collection.

## Catalogue state

`game.json` records the original input, entry Place ID, Universe ID and creation time. `places.json` holds metadata plus separate `entry` and `capture` summaries for every Place. A capture path, when present, is `captures/<PlaceID>`. Refreshing metadata preserves these summaries and their extra fields.

Use a new/empty directory or a catalogue for the same Universe. Refresh behavior is conservative:

- A complete listing marks previously known but absent Places as `listed:false`; it does not delete their records or captures.
- An incomplete listing may update Places returned by the API but does not mark unseen Places removed.
- A failed Universe lookup writes no catalogue. A Place-list failure after Universe identity is known is recorded as incomplete.
- Repeated/invalid cursors, invalid API responses, HTTP failures and pagination limits produce an explicit incomplete result.

Exit 0 means complete discovery, 2 means incomplete/failed discovery, and 1 means invalid input or local state. Inspect `discovery.complete` and `errors`. Updates are atomic per file; concurrent writers to one GAME directory are unsupported, so serialize commands.

## Entry and access failures

Record entry status independently from capture status. A Place may fail through its direct entry route yet be entered by another normal route later. Keep the failure and capture state separate. An API-listed Place is not an authorization decision. When entry fails or access is denied, report it and proceed to the next Place when useful. Do not repeat user requests or automate login, account unlocks, purchases, or access controls.

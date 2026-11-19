"""Official Place discovery and persistent per-Place entry/capture summaries."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlencode

from network import get, target_id
from pipeline import now


def _positive_id(value):
    return type(value) is int and 0 < value <= 2 ** 63 - 1


def _read(path):
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(path.name + " must contain an object")
    return value


def _save(path, value):
    """Replace one JSON file atomically; a failed write leaves its old contents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, prefix="." + path.name + ".", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _load_game(root):
    game = _read(root / "game.json")
    if game.get("schema") != "rcr-game/v1" or not _positive_id(game.get("universe_id")):
        raise ValueError("invalid game.json schema or Universe ID")
    path = root / "places.json"
    if path.exists():
        catalog = _read(path)
        if catalog.get("schema") != "rcr-places/v1" or catalog.get("universe_id") != game["universe_id"]:
            raise ValueError("places.json does not belong to game.json")
        rows = catalog.get("places")
        if not isinstance(rows, list):
            raise ValueError("places.json places must be an array")
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or not _positive_id(row.get("id")) or row["id"] in seen:
                raise ValueError("places.json has duplicate or invalid Place IDs")
            seen.add(row["id"])
            for key in ("entry", "capture"):
                if key in row and not isinstance(row[key], dict):
                    raise ValueError("places.json " + key + " must be an object")
    else:
        catalog = {"schema": "rcr-places/v1", "universe_id": game["universe_id"], "places": []}
    return game, catalog


def _request(url, request):
    try:
        metadata, raw = request(url)
    except OSError as exc:
        return None, {"url": url, "status": None}, {"issue": "network_error", "detail": str(exc)}
    response = {k: v for k, v in metadata.items() if k in ("status", "bytes", "sha256", "error")}
    response["url"] = url
    if metadata.get("status") != 200 or metadata.get("error"):
        return None, response, {"issue": "api_request_failed", "response": response}
    try:
        value = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("response must be an object")
    except (UnicodeError, ValueError) as exc:
        return None, response, {"issue": "invalid_api_json", "detail": str(exc), "response": response}
    return value, response, None


def refresh_places(target, output, request=None, max_pages=1000):
    """Refresh API metadata without replacing entry/capture state or deleting old Places."""
    place_id = target_id(target)
    if type(max_pages) is not int or max_pages < 1:
        raise ValueError("max_pages must be positive")
    root = Path(output).resolve()
    if root.exists() and not root.is_dir():
        raise ValueError("--out must be a game directory")
    game_path = root / "game.json"
    previous = _load_game(root) if game_path.exists() else None
    if previous is None and root.exists() and any(root.iterdir()):
        raise ValueError("--out must be new/empty or contain a valid game.json")
    request = request or get
    lookup_url = f"https://apis.roblox.com/universes/v1/places/{place_id}/universe"
    value, response, error = _request(lookup_url, request)
    if error or not _positive_id(value.get("universeId") if value else None):
        return {"status": "failed", "stage": "universe_lookup", "place_id": place_id,
                "errors": [error or {"issue": "invalid_universe_id", "response": response}], "files_written": []}
    universe_id = value["universeId"]
    if previous and previous[0]["universe_id"] != universe_id:
        raise ValueError("--out already belongs to another Universe; use a separate game directory")
    stamp = now()
    game, catalog = previous or (
        {"schema": "rcr-game/v1", "universe_id": universe_id, "entry_place_id": place_id,
         "input": str(target), "created_at": stamp},
        {"schema": "rcr-places/v1", "universe_id": universe_id, "places": []})
    endpoint = f"https://develop.roblox.com/v1/universes/{universe_id}/places"
    cursor, seen_cursors, found, responses, errors = None, set(), {}, [], []
    complete, pages_received = False, 0
    for _ in range(max_pages):
        params = {"sortOrder": "Asc", "limit": 100}
        if cursor:
            params["cursor"] = cursor
        page, meta, error = _request(endpoint + "?" + urlencode(params), request)
        responses.append(meta)
        if error:
            errors.append(error)
            break
        rows = page.get("data")
        if (not isinstance(rows, list) or "nextPageCursor" not in page
                or any(not isinstance(row, dict) or not _positive_id(row.get("id"))
                       or not isinstance(row.get("name"), str)
                       or (row.get("universeId") is not None and row["universeId"] != universe_id) for row in rows)):
            errors.append({"issue": "invalid_place_page", "response": meta})
            break
        pages_received += 1
        for row in rows:
            found[row["id"]] = {"id": row["id"], "name": row["name"]}
        cursor = page["nextPageCursor"]
        if cursor is None or cursor == "":
            complete = True
            break
        if not isinstance(cursor, str) or cursor in seen_cursors:
            errors.append({"issue": "invalid_or_repeated_cursor", "response": meta})
            break
        seen_cursors.add(cursor)
    else:
        errors.append({"issue": "pagination_limit_reached", "max_pages": max_pages})

    # Read current lifecycle data after network I/O, not the pre-request snapshot.
    # Commands writing the same game should still be serialized by their caller.
    if game_path.exists():
        game, catalog = _load_game(root)
        if game["universe_id"] != universe_id:
            raise ValueError("game directory changed to another Universe during lookup")
        previous = (game, catalog)
    elif previous is not None:
        raise ValueError("game.json was removed during lookup")
    elif root.exists() and any(root.iterdir()):
        raise ValueError("--out changed during lookup; refusing to overwrite unrelated files")

    # API metadata is an allowlist: no API field can overwrite local lifecycle data.
    merged = {row["id"]: deepcopy(row) for row in catalog["places"]}
    if complete:
        for row in merged.values():
            row["listed"] = False
    for pid, details in found.items():
        row = merged.setdefault(pid, {"id": pid})
        row.update(name=details["name"], listed=True, last_seen_at=stamp)
    for row in merged.values():
        row.setdefault("entry", {"status": "not_attempted"})
        row.setdefault("capture", {"status": "not_captured", "path": None})
    status = "pass" if complete else ("partial" if found else "failed")
    catalog.update(places=[merged[pid] for pid in sorted(merged)], discovery={
        "status": status, "complete": complete, "queried_place_id": place_id, "checked_at": stamp,
        "places_received": len(found), "pages_received": pages_received,
        "lookup": response, "requests": responses, "errors": errors})
    if complete:
        catalog["last_complete_at"] = stamp
    if previous is None:
        _save(game_path, game)
    _save(root / "places.json", catalog)
    return {"status": status, "universe_id": universe_id, "complete": complete,
            "places_received": len(found), "known_places": len(merged),
            "catalog": str(root / "places.json"), "errors": errors,
            "meaning": "Official Place metadata and preserved local state; listing does not establish entry permission or collection."}


def update_place_state(root, place_id, *, entry=None, capture=None):
    """Shared writer for later Player commands; it does not assert or execute collection."""
    if not _positive_id(place_id):
        raise ValueError("invalid Place ID")
    root = Path(root).resolve()
    _, catalog = _load_game(root)
    rows = [row for row in catalog["places"] if row["id"] == place_id]
    if len(rows) != 1:
        raise ValueError("Place ID is not in places.json; refresh the official list first")
    if entry is None and capture is None:
        raise ValueError("entry or capture update required")
    for key, patch in (("entry", entry), ("capture", capture)):
        if patch is not None:
            if not isinstance(patch, dict) or not isinstance(patch.get("status"), str) or not patch["status"].strip():
                raise ValueError(key + " update requires a nonempty status")
    row = rows[0]
    if entry is not None:
        row["entry"] = {**row.get("entry", {}), **deepcopy(entry), "updated_at": now()}
    if capture is not None:
        # A capture summary describes one whole attempt; fields of an earlier attempt must not survive.
        row["capture"] = {**deepcopy(capture), "updated_at": now()}
    _save(root / "places.json", catalog)
    return deepcopy(row)

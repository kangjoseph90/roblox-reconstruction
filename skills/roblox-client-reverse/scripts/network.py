"""Read-only official API acquisition. Explicit credentials only, no client hooks."""
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
import zlib

from formats import MAGIC, MAX_BYTES, sha
from pipeline import extract, finish, new_output, now


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def target_id(target, kind="experience"):
    if re.fullmatch(r"[0-9]+", str(target)):
        value = int(target)
    else:
        p = urllib.parse.urlsplit(target)
        if p.scheme != "https" or p.username or p.password or p.port or p.hostname not in {"www.roblox.com", "roblox.com", "create.roblox.com"}:
            raise ValueError("use a numeric ID or an https Roblox experience/asset URL")
        pattern = r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?games/([0-9]+)(?:/|$)" if kind == "experience" else r"/(?:store/asset|library|catalog)/([0-9]+)(?:/|$)"
        match = re.search(pattern, p.path, re.I)
        if not match:
            raise ValueError("URL does not contain a " + kind + " ID")
        value = int(match.group(1))
    if value <= 0 or value > 2 ** 63 - 1:
        raise ValueError("ID is outside supported positive 64-bit range")
    return value


def allowed_url(url):
    p = urllib.parse.urlsplit(url)
    host = p.hostname or ""
    return p.scheme == "https" and not p.username and not p.password and not p.port and any(
        host == d or host.endswith("." + d) for d in ("roblox.com", "rbxcdn.com"))


def get(url, headers=None):
    if not allowed_url(url):
        raise ValueError("refusing non-Roblox/CDN URL")
    request = urllib.request.Request(url, headers=dict({"User-Agent": "roblox-client-reverse/1.0"}, **(headers or {})))
    opener = urllib.request.build_opener(NoRedirect())
    try:
        response = opener.open(request, timeout=20)
    except urllib.error.HTTPError as e:
        response = e
    except (OSError, urllib.error.URLError) as e:
        return {"status": None, "error": type(e).__name__ + ": " + str(e)}, b""
    with response:
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            return {"status": response.code, "error": "response exceeds 128 MiB"}, b""
        h = response.headers
        encoding = (h.get("Content-Encoding") or "identity").lower()
        transport = {}
        if encoding in {"gzip", "deflate"}:
            transport = {"content_encoding": encoding, "encoded_bytes": len(data), "encoded_sha256": sha(data)}
            decoder = zlib.decompressobj(31 if encoding == "gzip" else 15)
            try:
                data = decoder.decompress(data, MAX_BYTES + 1)
                if len(data) > MAX_BYTES or decoder.unconsumed_tail or not decoder.eof:
                    raise ValueError("compressed HTTP body exceeds limit or is truncated")
            except (ValueError, zlib.error) as e:
                return {"status": response.code, "error": str(e)}, b""
        elif encoding != "identity":
            return {"status": response.code, "error": "unsupported HTTP content encoding: " + encoding}, b""
        # Do not collect request headers, cookies, API keys, or unrelated local credentials.
        return dict(transport, status=response.code, content_type=h.get("Content-Type"),
                    location=h.get("Location"), bytes=len(data), sha256=sha(data)), data


def acquire(target, output, kind="experience", api_key_env=None, asset_version=None, tools=None):
    identity = target_id(target, kind)
    key = None
    if api_key_env:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_key_env):
            raise ValueError("invalid API-key environment variable name")
        key = os.environ.get(api_key_env)
        if not key:
            raise ValueError("explicit API-key environment variable is empty")
    out = new_output(output)
    report = {"schema": "rcr-report/v1", "operation": "acquire", "created_at": now(),
        "target": {"kind": kind, "id": identity}, "network_used": True,
        "requests": [], "artifacts": [], "scripts": [], "errors": [],
        "coverage": {"scope": "official accessible API responses", "game_completeness": "unknown", "runtime_executed": False},
        "methods": [
            {"method": "official_asset_delivery", "status": "pending", "access": "asset download permission; API key for authenticated requests", "runtime": False},
            {"method": "studio_export", "status": "requires_user_artifact", "access": "edit permission or copy-enabled experience; Studio on Windows/macOS", "runtime": False},
            {"method": "local_artifact_or_dump", "status": "requires_user_artifact", "access": "provide an explicitly selected local path to extract", "runtime": False},
            {"method": "controlled_runtime_snapshot", "status": "not_attempted", "access": "owned/permitted Studio session; use bundled exporter", "runtime": True},
            {"method": "process_memory_instrumentation", "status": "not_selected",
             "implementation": "windows_readonly_collector", "backend": "player",
             "access": "Windows x64 Player, Computer Use confirmation and runtime-validated structure; use run or acquire --backend player",
             "runtime": True}]}

    def request(label, url, headers=None):
        meta, data = get(url, headers)
        # Signed redirect locations need not be printed or included in the general report.
        location = meta.pop("location", None)
        filename = "responses/" + label + ".bin"
        p = out / filename
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(data)
        meta.update(label=label, url=url, response_path=filename)
        report["requests"].append(meta)
        return meta, data, location

    if kind == "experience":
        meta, raw, _ = request("universe", "https://apis.roblox.com/universes/v1/places/" + str(identity) + "/universe")
        if meta.get("status") == 200:
            try:
                uid = int(json.loads(raw)["universeId"])
                report["target"]["universe_id"] = uid
                details, d, _ = request("experience", "https://games.roblox.com/v1/games?universeIds=" + str(uid))
                if details.get("status") == 200:
                    report["target"]["public_metadata"] = json.loads(d)
            except (ValueError, KeyError, TypeError) as e:
                report["errors"].append("public metadata decoding failed: " + str(e))
    if key:
        url = "https://apis.roblox.com/asset-delivery-api/v1/assetId/" + str(identity)
        if asset_version:
            url += "/version/" + str(asset_version)
        headers = {"x-api-key": key}
    else:
        url = "https://assetdelivery.roblox.com/v2/assetId/" + str(identity)
        if asset_version:
            url += "/version/" + str(asset_version)
        headers = None
    meta, data, location = request("asset-delivery", url, headers)
    status = meta.get("status")
    method = report["methods"][0]
    if status in {401, 403}:
        method.update(status="access_denied", http_status=status)
    elif status == 429:
        method.update(status="rate_limited", http_status=status)
    elif status not in {200, 301, 302, 303, 307, 308}:
        method.update(status="unavailable", http_status=status)
    else:
        method["status"] = "response_received"
        if not location and not data.startswith(MAGIC) and not data.lstrip().startswith((b"<roblox", b"<?xml")):
            try:
                response = json.loads(data)
                locations = response.get("locations", [])
                location = response.get("location")
                if not location and locations:
                    location = locations[0].get("location")
                if response.get("errors"):
                    errors = response["errors"]
                    denied = any(str(e.get("code")) in {"401", "403"} for e in errors if isinstance(e, dict))
                    method.update(status="access_denied" if denied else "api_error", api_errors=errors)
            except (ValueError, TypeError, AttributeError):
                pass
        if location:
            if isinstance(location, str) and allowed_url(location):
                # Critical: the API key is NEVER attached to a CDN request or redirect.
                meta2, data = get(location)
                meta2.pop("location", None)
                meta2.update(label="asset-content", url="[Roblox CDN location omitted]", response_path="responses/asset-content.bin")
                (out / meta2["response_path"]).write_bytes(data)
                report["requests"].append(meta2)
                if meta2.get("status") != 200:
                    method.update(status="content_download_failed", http_status=meta2.get("status"))
                    data = b""
            else:
                method["status"] = "refused_untrusted_redirect"
                data = b""
        if data.startswith(MAGIC) or data.lstrip().startswith((b"<roblox", b"<?xml")):
            ext = ".rbxl" if data.startswith(MAGIC) else ".rbxlx"
            if kind == "asset":
                ext = ".rbxm" if data.startswith(MAGIC) else ".rbxmx"
            p = out / ("downloaded" + ext)
            p.write_bytes(data)
            report["artifacts"].append({"path": p.name, "sha256": sha(data), "bytes": len(data)})
            method["status"] = "acquired"
            extracted = extract(p, out / "repository", tools=tools)
            report["extraction_report"] = "repository/report.json"
            report["summary"] = dict(extracted["summary"], acquisition="roblox_artifact_acquired")
        elif method["status"] == "response_received":
            method["status"] = "no_recognized_source_artifact"
    report.setdefault("summary", {"status": "metadata_only" if report["target"].get("public_metadata") else "no_artifact",
                                  "source_files": 0, "acquisition": method["status"]})
    finish(out, report)
    return report

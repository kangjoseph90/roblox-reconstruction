"""Read-only official API acquisition. Explicit credentials only, no client hooks."""
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
import zlib

from formats import MAGIC, MAX_BYTES, VERSION, sha
from pipeline import finish, new_output, now


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
    try:
        p = urllib.parse.urlsplit(url)
        host = p.hostname or ""
        # Credentials, explicit ports, non-HTTPS schemes, trailing-dot aliases,
        # and malformed authorities are never accepted for API or CDN traffic.
        return (p.scheme == "https" and not p.username and not p.password and
                p.port is None and host == host.lower() and not host.endswith(".") and
                any(host == d or host.endswith("." + d) for d in ("roblox.com", "rbxcdn.com")))
    except (TypeError, ValueError):
        return False


def get(url, headers=None):
    if not allowed_url(url):
        raise ValueError("refusing non-Roblox/CDN URL")
    request = urllib.request.Request(url, headers=dict({"User-Agent": "roblox-client-reverse/" + VERSION}, **(headers or {})))
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


def fetch(target, output, kind="experience", api_key_env=None, asset_version=None):
    """Fetch an original Roblox artifact and write a provenance/integrity report.

    Result states are ``acquired`` (artifact bytes saved), ``metadata_only``
    (public metadata saved, no artifact), ``partial`` (some requested result
    was obtained but another request failed), and ``failed`` (neither metadata
    nor artifact was obtained). No extraction, decompilation, or tool building
    is performed.
    """
    if kind not in {"experience", "asset"}:
        raise ValueError("kind must be 'experience' or 'asset'")
    if asset_version is not None and not re.fullmatch(r"[0-9]+", str(asset_version)):
        raise ValueError("asset version must be a positive integer")
    identity = target_id(target, kind)
    key = None
    if api_key_env:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_key_env):
            raise ValueError("invalid API-key environment variable name")
        key = os.environ.get(api_key_env)
        if not key:
            raise ValueError("explicit API-key environment variable is empty")
    out = new_output(output)
    report = {"schema": "rcr-report/v1", "operation": "fetch", "created_at": now(),
        "target": {"kind": kind, "id": identity}, "network_used": True,
        "requests": [], "artifacts": [], "errors": [],
        "coverage": {"scope": "official accessible API responses", "game_completeness": "unknown", "runtime_executed": False}}

    def request(label, url, headers=None):
        meta, data = get(url, headers)
        # Signed redirect locations need not be printed or included in the general report.
        location = meta.pop("location", None)
        meta.update(label=label, url=url)
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
    method = {"method": "official_asset_delivery"}
    report["delivery"] = method
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
                # Deliberately make a fresh unauthenticated request. The signed
                # URL is transport-only and is never copied into the report.
                try:
                    meta2, data = get(location)
                except ValueError:
                    method.update(status="refused_untrusted_redirect")
                    data = b""
                    meta2 = None
                if meta2 is not None:
                    meta2.pop("location", None)
                    meta2.update(label="asset-content", url="[Roblox CDN location omitted]")
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
        elif method["status"] == "response_received":
            method["status"] = "no_recognized_artifact"
    got_metadata = "public_metadata" in report["target"]
    got_artifact = bool(report["artifacts"])
    request_failures = bool(report["errors"]) or any(
        not isinstance(r.get("status"), int) or r["status"] >= 400 for r in report["requests"])
    if got_artifact:
        result = "partial" if request_failures else "acquired"
    elif got_metadata:
        result = "partial" if method.get("status") in {
            "access_denied", "rate_limited", "unavailable", "content_download_failed",
            "refused_untrusted_redirect", "api_error"} else "metadata_only"
    else:
        result = "failed"
    report["summary"] = {"status": result, "artifact_count": len(report["artifacts"]),
                          "metadata_available": got_metadata, "delivery_status": method.get("status")}
    finish(out, report)
    return report

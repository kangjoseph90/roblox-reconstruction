import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import unittest.mock
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from formats import sha
import network
from network import fetch, get


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / "out"

    def test_download_only_success_preserves_original_bytes_and_integrity(self):
        raw = b"<roblox version=\"4\"></roblox>"
        with patch("network.get", return_value=({"status": 200, "content_type": "application/xml"}, raw)), \
                patch("pipeline.extract") as extract, patch("bootstrap_tools.build_tools") as build_tools:
            report = fetch("42", self.out, kind="asset")
        extract.assert_not_called()
        build_tools.assert_not_called()
        artifact = self.out / "downloaded.rbxmx"
        self.assertEqual(artifact.read_bytes(), raw)
        self.assertEqual(report["summary"]["status"], "acquired")
        self.assertEqual(report["artifacts"], [{"path": artifact.name, "sha256": sha(raw), "bytes": len(raw)}])
        integrity = json.loads((self.out / "files.json").read_text(encoding="utf-8"))
        self.assertIn({"path": artifact.name, "bytes": len(raw), "sha256": sha(raw)}, integrity["files"])
        self.assertFalse((self.out / "repository").exists())

    def test_denied_rate_limited_and_unrecognized_responses_are_explicit(self):
        cases = [
            (({"status": 403}, b"denied"), "access_denied", "failed"),
            (({"status": 429}, b"slow down"), "rate_limited", "failed"),
            (({"status": 200}, b"not an artifact"), "no_recognized_artifact", "failed"),
        ]
        for index, (response, delivery_status, result_status) in enumerate(cases):
            with self.subTest(delivery_status=delivery_status):
                out = self.out / str(index)
                with patch("network.get", return_value=response):
                    report = fetch("42", out, kind="asset")
                self.assertEqual(report["delivery"]["status"], delivery_status)
                self.assertEqual(report["summary"]["status"], result_status)

    def test_signed_redirect_does_not_forward_api_key_and_url_is_not_reported(self):
        signed = "https://c0.rbxcdn.com/file?signature=secret"
        raw = b"<roblox version=\"4\"></roblox>"
        calls = []

        def fake_get(url, headers=None):
            calls.append((url, headers))
            if len(calls) == 1:
                return {"status": 302, "location": signed}, b""
            return {"status": 200, "content_type": "application/xml"}, raw

        with patch.dict(os.environ, {"RCR_TEST_KEY": "synthetic-secret"}), patch("network.get", side_effect=fake_get):
            report = fetch("42", self.out, kind="asset", api_key_env="RCR_TEST_KEY")
        self.assertEqual(calls[0][1], {"x-api-key": "synthetic-secret"})
        self.assertEqual(calls[1], (signed, None))
        serialized = json.dumps(report)
        self.assertNotIn("signature=secret", serialized)
        self.assertNotIn("synthetic-secret", serialized)
        self.assertEqual(report["summary"]["status"], "acquired")

    def test_untrusted_redirect_is_refused(self):
        calls = []

        def fake_get(url, headers=None):
            calls.append(url)
            return {"status": 302, "location": "https://evil.example/artifact"}, b""

        with patch("network.get", side_effect=fake_get):
            report = fetch("42", self.out, kind="asset")
        self.assertEqual(len(calls), 1)
        self.assertEqual(report["delivery"]["status"], "refused_untrusted_redirect")
        self.assertEqual(report["summary"]["status"], "failed")
        self.assertFalse(report["artifacts"])

    def test_http_200_embedded_auth_error_is_access_denied(self):
        body = b'{"errors":[{"code":"401","message":"Authentication required"}]}'
        with patch("network.get", return_value=({"status": 200}, body)):
            report = fetch("42", self.out, kind="asset")
        self.assertEqual(report["delivery"]["status"], "access_denied")
        self.assertEqual(report["summary"]["status"], "failed")

    def test_opencloud_top_level_location_downloads_artifact(self):
        raw = b"<roblox version=\"4\"></roblox>"
        location = "https://c0.rbxcdn.com/opencloud-artifact"
        calls = []

        def fake_get(url, headers=None):
            calls.append((url, headers))
            if len(calls) == 1:
                return ({"status": 200, "content_type": "application/json"},
                        json.dumps({"location": location}).encode())
            return {"status": 200, "content_type": "application/xml"}, raw

        with patch("network.get", side_effect=fake_get):
            report = fetch("42", self.out, kind="asset")
        self.assertEqual(calls, [
            ("https://assetdelivery.roblox.com/v2/assetId/42", None), (location, None)])
        self.assertEqual((self.out / "downloaded.rbxmx").read_bytes(), raw)
        self.assertEqual(report["summary"]["status"], "acquired")

    def test_malformed_input_is_rejected_before_output_creation(self):
        for args in [("https://evil.example/games/42", "experience"), ("42", "unknown")]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                fetch(args[0], self.out, kind=args[1])
            self.assertFalse(self.out.exists())
        with self.assertRaises(ValueError):
            fetch("42", self.out, kind="asset", asset_version="1/2")
        self.assertFalse(self.out.exists())

    def test_xml_error_document_is_not_an_artifact(self):
        with patch("network.get", return_value=({"status": 200}, b'<?xml version="1.0"?><Error>denied</Error>')):
            report = fetch("42", self.out, kind="asset")
        self.assertEqual((report["delivery"]["status"], report["summary"]["status"]), ("no_recognized_artifact", "failed"))
        self.assertFalse(report["artifacts"])

    def test_denial_with_a_location_is_not_downloaded(self):
        body = json.dumps({"errors": [{"code": 403}], "location": "https://c0.rbxcdn.com/x"}).encode()
        calls = []
        def fake_get(url, headers=None):
            calls.append(url)
            return ({"status": 200}, body) if len(calls) == 1 else ({"status": 200}, b"<roblox version=\"4\"></roblox>")
        with patch("network.get", side_effect=fake_get):
            report = fetch("42", self.out, kind="asset")
        self.assertEqual(len(calls), 1)
        self.assertEqual(report["delivery"]["status"], "access_denied")
        self.assertFalse(report["artifacts"])

    def test_transport_error_with_ok_status_is_a_failed_request(self):
        with patch("network.get", return_value=({"status": 200, "error": "response exceeds 128 MiB"}, b"")):
            report = fetch("42", self.out, kind="asset")
        self.assertEqual(report["delivery"]["status"], "content_download_failed")
        self.assertEqual(report["summary"]["status"], "failed")

    def test_asset_version_zero_is_rejected(self):
        with self.assertRaises(ValueError):
            fetch("42", self.out, kind="asset", asset_version=0)
        self.assertFalse(self.out.exists())
        urls = []
        with patch("network.get", side_effect=lambda url, headers=None: (urls.append(url), ({"status": 404}, b""))[1]):
            report = fetch("42", self.out, kind="asset", asset_version=3)
        self.assertTrue(urls[0].endswith("/version/3"))
        self.assertEqual(report["target"]["asset_version"], 3)

    def test_non_list_api_errors_are_final(self):
        calls = []
        def fake_get(url, headers=None):
            calls.append(url)
            return ({"status": 200, "location": "https://c0.rbxcdn.com/x"}, b'{"errors": 403}')
        with patch("network.get", side_effect=fake_get):
            report = fetch("42", self.out, kind="asset")
        self.assertEqual((len(calls), report["delivery"]["status"]), (1, "access_denied"))

    def test_invalid_or_cut_xml_is_not_an_artifact(self):
        for body in (b'<roblox version="4"><Item></roblox>', b'<roblox version="4"><Item class="Part">'):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as tmp, \
                    patch("network.get", return_value=({"status": 200}, body)):
                report = fetch("42", Path(tmp) / "out", kind="asset")
                self.assertFalse(report["artifacts"])

    def test_empty_public_metadata_is_not_metadata(self):
        def fake_get(url, headers=None):
            if "games.roblox.com" in url:
                return {"status": 200}, b"null"
            if url.endswith("/universe"):
                return {"status": 200}, b'{"universeId": 7}'
            return {"status": 403}, b""
        with patch("network.get", side_effect=fake_get):
            report = fetch("42", self.out)
        self.assertFalse(report["summary"]["metadata_available"])

    def test_failed_artifact_write_leaves_no_partial_file(self):
        raw = b'<roblox version="4"></roblox>'
        real = Path.open
        class Broken:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *a): self.stream.close()
            def write(self, data): self.stream.write(data[:5]); raise OSError("disk full")
        def failing(path, mode="r", *a, **k):
            stream = real(path, mode, *a, **k)
            return Broken(stream) if path.name.startswith("downloaded") else stream
        with patch("network.get", return_value=({"status": 200}, raw)), patch.object(Path, "open", failing), \
                self.assertRaises(OSError):
            fetch("42", self.out, kind="asset")
        self.assertFalse((self.out / "downloaded.rbxmx").exists())


    def test_empty_lookup_result_is_not_metadata(self):
        def fake_get(url, headers=None):
            if "games.roblox.com" in url:
                return {"status": 200}, b'{"data": []}'
            if url.endswith("/universe"):
                return {"status": 200}, b'{"universeId": 7}'
            return {"status": 403}, b""
        with patch("network.get", side_effect=fake_get):
            report = fetch("42", self.out)
        self.assertFalse(report["summary"]["metadata_available"])


class TransportTests(unittest.TestCase):
    class Response:
        def __init__(self, body, headers, error=None):
            self.body, self.headers, self.code, self.error = body, headers, 200, error
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self, limit):
            if self.error:
                raise self.error
            return self.body[:limit]

    def open_with(self, response):
        opener = unittest.mock.Mock()
        opener.open.return_value = response
        return patch("network.urllib.request.build_opener", return_value=opener)

    def test_short_or_interrupted_body_is_an_error(self):
        import http.client
        import email.message
        headers = email.message.Message()
        headers["Content-Length"] = "100"
        with self.open_with(self.Response(b"partial", headers)):
            meta, data = get("https://assetdelivery.roblox.com/v2/assetId/1")
        self.assertIn("error", meta)
        self.assertEqual(data, b"")
        with self.open_with(self.Response(b"", email.message.Message(), http.client.IncompleteRead(b"x", 10))):
            meta, data = get("https://assetdelivery.roblox.com/v2/assetId/1")
        self.assertIn("error", meta)

    def test_trailing_gzip_member_is_not_silently_dropped(self):
        import gzip, email.message
        headers = email.message.Message()
        headers["Content-Encoding"] = "gzip"
        with self.open_with(self.Response(gzip.compress(b"first") + gzip.compress(b"second"), headers)):
            meta, data = get("https://assetdelivery.roblox.com/v2/assetId/1")
        self.assertIn("error", meta)

    def test_environment_proxies_are_not_used(self):
        with patch("network.urllib.request.build_opener") as build:
            build.return_value.open.side_effect = OSError("offline")
            get("https://assetdelivery.roblox.com/v2/assetId/1")
        handlers = build.call_args[0]
        proxy = next(h for h in handlers if isinstance(h, network.urllib.request.ProxyHandler))
        self.assertEqual(proxy.proxies, {})


if __name__ == "__main__":
    unittest.main()

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from formats import sha
from network import fetch


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
        raw = b"<roblox></roblox>"
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
        raw = b"<roblox></roblox>"
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


if __name__ == "__main__":
    unittest.main()

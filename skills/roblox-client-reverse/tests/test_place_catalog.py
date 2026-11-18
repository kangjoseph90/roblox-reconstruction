"""Official API discovery and lifecycle preservation on synthetic game folders."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import place_catalog
import rcr


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def page(*rows, cursor=None):
    return {"data": list(rows), "nextPageCursor": cursor}


def response(value, status=200):
    raw = value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")
    return {"status": status, "bytes": len(raw)}, raw


def requests(*values):
    return unittest.mock.Mock(side_effect=[response(v) if not isinstance(v, tuple) else v for v in values])


class PlaceCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "game"

    def initial(self):
        return place_catalog.refresh_places("1001", self.root, request=requests(
            {"universeId": 200}, page({"id": 1001, "name": "Main"}, {"id": 1002, "name": "Other"})))

    def test_all_pages_and_encoded_cursor_create_unicode_metadata_and_separate_states(self):
        cursor = "page 2+/=&"
        request = requests({"universeId": 200}, page({"id": 1002, "name": "다른 곳 🌙"}, cursor=cursor),
                           page({"id": 1001, "name": "Main"}, {"id": 1002, "name": "다른 곳 🌙"}))
        result = place_catalog.refresh_places("https://www.roblox.com/ko/games/1001/Example", self.root, request=request)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["complete"])
        self.assertEqual(result["places_received"], 2)
        self.assertEqual(request.call_count, 3)
        query = parse_qs(urlsplit(request.call_args_list[2].args[0]).query)
        self.assertEqual(query["cursor"], [cursor])
        catalog = read(self.root / "places.json")
        self.assertEqual([p["id"] for p in catalog["places"]], [1001, 1002])
        self.assertEqual(catalog["places"][1]["name"], "다른 곳 🌙")
        self.assertEqual(catalog["places"][0]["entry"], {"status": "not_attempted"})
        self.assertEqual(catalog["places"][0]["capture"], {"status": "not_captured", "path": None})
        self.assertEqual(read(self.root / "game.json")["entry_place_id"], 1001)

    def test_refresh_preserves_failed_direct_entry_and_subsequent_capture(self):
        self.initial()
        place_catalog.update_place_state(self.root, 1002, entry={"status": "failed", "error_code": 524})
        place_catalog.update_place_state(self.root, 1002, capture={
            "status": "partial", "path": "captures/1002", "integrity_valid_instances": 5})
        before = read(self.root / "places.json")["places"][1]
        # Unexpected API fields cannot replace local status summaries.
        request = requests({"universeId": 200}, page({"id": 1002, "name": "Renamed", "capture": {"status": "failed"}}))
        place_catalog.refresh_places("1002", self.root, request=request)
        after = read(self.root / "places.json")["places"][1]
        self.assertEqual(after["entry"], before["entry"])
        self.assertEqual(after["capture"], before["capture"])
        self.assertEqual(after["name"], "Renamed")
        self.assertEqual(read(self.root / "game.json")["entry_place_id"], 1001)

    def test_complete_list_retains_unlisted_places_and_their_captures(self):
        self.initial()
        place_catalog.update_place_state(self.root, 1002, capture={"status": "captured", "path": "captures/1002"})
        request = requests({"universeId": 200}, page({"id": 1001, "name": "Main"}))
        result = place_catalog.refresh_places("1001", self.root, request=request)
        self.assertEqual((result["places_received"], result["known_places"]), (1, 2))
        removed = read(self.root / "places.json")["places"][1]
        self.assertFalse(removed["listed"])
        self.assertEqual(removed["capture"]["status"], "captured")

    def test_refresh_preserves_state_written_while_waiting_for_the_api(self):
        self.initial()
        def request(url):
            if url.endswith('/universe'):
                return response({'universeId':200})
            place_catalog.update_place_state(self.root, 1002, capture={'status':'partial','path':'captures/1002'})
            return response(page({'id':1001,'name':'Main'}, {'id':1002,'name':'Other'}))
        result = place_catalog.refresh_places('1001', self.root, request=request)
        self.assertEqual(result['status'], 'pass')
        row = read(self.root/'places.json')['places'][1]
        self.assertEqual(row['capture']['status'], 'partial')

    def test_partial_refresh_does_not_treat_unseen_places_as_removed(self):
        self.initial()
        before = read(self.root / "places.json")
        request = requests({"universeId": 200}, page({"id": 1003, "name": "New"}, cursor="next"),
                           response({"errors": []}, status=429))
        result = place_catalog.refresh_places("1001", self.root, request=request)
        catalog = read(self.root / "places.json")
        self.assertEqual(result["status"], "partial")
        self.assertFalse(catalog["discovery"]["complete"])
        self.assertEqual(catalog["last_complete_at"], before["last_complete_at"])
        self.assertTrue(all(p["listed"] for p in catalog["places"]))
        self.assertEqual(len(catalog["places"]), 3)

    def test_listing_failure_keeps_previous_states_and_reports_current_failure(self):
        self.initial()
        place_catalog.update_place_state(self.root, 1001, capture={"status": "partial", "path": "captures/1001"})
        before = read(self.root / "places.json")
        result = place_catalog.refresh_places("1001", self.root, request=requests(
            {"universeId": 200}, response({}, status=503)))
        after = read(self.root / "places.json")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(before["places"], after["places"])
        self.assertEqual(after["discovery"]["status"], "failed")
        self.assertEqual(after["last_complete_at"], before["last_complete_at"])

    def test_wrong_universe_is_rejected_without_listing_or_changing_files(self):
        self.initial()
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        request = requests({"universeId": 999})
        with self.assertRaisesRegex(ValueError, "another Universe"):
            place_catalog.refresh_places("5001", self.root, request=request)
        self.assertEqual(request.call_count, 1)
        self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir()}, before)

    def test_lookup_failures_do_not_create_a_game_or_modify_an_existing_game(self):
        for value in (response({}, status=403), b"not json", {"universeId": True}, {"universeId": 0}):
            with self.subTest(value=value):
                result = place_catalog.refresh_places("1001", self.root, request=requests(value))
                self.assertEqual(result["status"], "failed")
                self.assertFalse(self.root.exists())
        self.initial()
        before = (self.root / "places.json").read_bytes()
        result = place_catalog.refresh_places("1001", self.root, request=requests(response({}, status=404)))
        self.assertEqual(result["stage"], "universe_lookup")
        self.assertEqual((self.root / "places.json").read_bytes(), before)

    def test_malformed_pages_and_cursors_never_claim_a_complete_list(self):
        cases = [{"data": []}, {"data": "invalid", "nextPageCursor": None},
                 page({"id": True, "name": "Bad"}), page({"id": 1001, "name": None}),
                 page({"id": 1001, "name": "Wrong", "universeId": 999}),
                 page({"id": 1001, "name": "Valid row"}, cursor=123)]
        for i, payload in enumerate(cases):
            with self.subTest(payload=payload):
                root = self.root.with_name("case-" + str(i))
                result = place_catalog.refresh_places("1001", root, request=requests({"universeId": 200}, payload))
                self.assertFalse(result["complete"])
                self.assertNotEqual(result["status"], "pass")

    def test_repeated_cursor_and_page_budget_stop_as_partial(self):
        for budget, pages, issue in [(10, [page({"id": 1001, "name": "A"}, cursor="same"), page(cursor="same")],
                                      "invalid_or_repeated_cursor"),
                                     (1, [page({"id": 1001, "name": "A"}, cursor="next")], "pagination_limit_reached")]:
            with self.subTest(budget=budget):
                root = self.root.with_name("budget-" + str(budget))
                request = requests({"universeId": 200}, *pages)
                result = place_catalog.refresh_places("1001", root, request=request, max_pages=budget)
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["errors"][0]["issue"], issue)

    def test_foreign_output_and_invalid_local_catalog_are_not_overwritten(self):
        self.root.mkdir()
        (self.root / "notes.txt").write_text("Keep this file")
        request = requests({"universeId": 200})
        with self.assertRaisesRegex(ValueError, "new/empty"):
            place_catalog.refresh_places("1001", self.root, request=request)
        request.assert_not_called()
        other = self.root.with_name("valid")
        place_catalog.refresh_places("1001", other, request=requests({"universeId": 200}, page()))
        malformed = read(other / "places.json")
        malformed["places"] = [{"id": 1001, "capture": "invalid"}]
        path = other / "places.json"
        path.write_text(json.dumps(malformed), encoding="utf-8")
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "capture must be an object"):
            place_catalog.refresh_places("1001", other, request=request)
        self.assertEqual(path.read_bytes(), before)

    def test_state_writer_validates_all_changes_before_writing(self):
        self.initial()
        before = (self.root / "places.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "capture update"):
            place_catalog.update_place_state(self.root, 1001, entry={"status": "failed"}, capture={"status": []})
        with self.assertRaisesRegex(ValueError, "refresh the official list"):
            place_catalog.update_place_state(self.root, 9999, capture={"status": "captured"})
        self.assertEqual((self.root / "places.json").read_bytes(), before)

    def test_failed_atomic_write_preserves_the_previous_catalog(self):
        self.initial()
        before = (self.root / "places.json").read_bytes()
        with patch("place_catalog.os.replace", side_effect=OSError("simulated write failure")):
            with self.assertRaisesRegex(OSError, "simulated write failure"):
                place_catalog.update_place_state(self.root, 1001, entry={"status": "failed"})
        self.assertEqual((self.root / "places.json").read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["game.json", "places.json"])

    def test_cli_exit_codes_and_no_native_tools_or_extraction(self):
        for folder, replies, code, status in [
            ("ok", [{"universeId": 200}, page({"id": 1001, "name": "Main"})], 0, "pass"),
            ("failed", [{"universeId": 200}, response({}, status=403)], 2, "failed")]:
            with self.subTest(status=status):
                stdout = io.StringIO()
                with patch.object(sys, "argv", ["rcr.py", "places", "1001", "--out", str(self.root / folder)]), \
                        patch("place_catalog.get", requests(*replies)), \
                        patch("bootstrap_tools.build_tools") as build, patch("pipeline.extract") as extract, redirect_stdout(stdout):
                    self.assertEqual(rcr.main(), code)
                self.assertEqual(json.loads(stdout.getvalue())["status"], status)
                build.assert_not_called()
                extract.assert_not_called()
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["rcr.py", "places", "invalid", "--out", str(self.root)]), redirect_stderr(stderr):
            self.assertEqual(rcr.main(), 1)
        self.assertEqual(json.loads(stderr.getvalue())["status"], "error")


if __name__ == "__main__":
    unittest.main()

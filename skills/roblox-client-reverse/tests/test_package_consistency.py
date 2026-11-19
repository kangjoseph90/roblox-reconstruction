"""Version labels and the package manifest must agree with the shipped files."""
import hashlib
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from formats import VERSION

MANIFEST = "PACKAGE_MANIFEST.json"
IGNORED_PARTS = {"__pycache__", ".DS_Store"}
# The documented local workspace (virtual environment, built tools, run outputs) is never shipped.
LOCAL_WORKSPACE = "work"


class PackageConsistencyTests(unittest.TestCase):
    def test_version_labels_agree(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(re.search(r"Version \*\*([^*]+)\*\*", skill).group(1), VERSION)
        self.assertEqual(json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))["version"], VERSION)
        readme = ROOT.parents[1] / "README.md"
        # Only the source repository's README lists this skill; an installed copy may sit beside an unrelated README.
        rows = [line for line in readme.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.startswith("|") and "(skills/" + ROOT.name + "/" in line] \
            if readme.is_file() else []
        for row in rows:
            self.assertEqual(row.rstrip(" |").rsplit("|", 1)[1].strip(), VERSION)

    def test_manifest_matches_shipped_files(self):
        manifest = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
        listed = {}
        for entry in manifest["files"]:
            data = (ROOT / entry["path"]).read_bytes()
            listed[entry["path"]] = (len(data), hashlib.sha256(data).hexdigest())
            with self.subTest(path=entry["path"]):
                self.assertEqual((entry["bytes"], entry["sha256"]), listed[entry["path"]])
        shipped = {p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*")
                   if p.is_file() and p.name != MANIFEST and not IGNORED_PARTS & set(p.parts) and p.suffix != ".pyc"
                   and p.relative_to(ROOT).parts[0] != LOCAL_WORKSPACE}
        self.assertEqual(sorted(shipped - set(listed)), [], "files missing from " + MANIFEST)
        self.assertEqual(sorted(set(listed) - shipped), [], "manifest lists absent files")

    def test_preserved_decompiler_sources_match_their_manifest(self):
        manifest = json.loads((ROOT / "assets/luaudec-source-manifest.json").read_text(encoding="utf-8"))
        for relative, expected in manifest.items():
            with self.subTest(path=relative):
                data = (ROOT / "assets/luaudec-source" / relative).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()

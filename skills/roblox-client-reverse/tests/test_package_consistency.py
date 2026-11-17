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


class PackageConsistencyTests(unittest.TestCase):
    def test_version_labels_agree(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(re.search(r"Version \*\*([^*]+)\*\*", skill).group(1), VERSION)
        self.assertEqual(json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))["version"], VERSION)
        readme = ROOT.parents[1] / "README.md"
        if readme.is_file():  # present only inside the source repository, not an installed skill
            row = next(line for line in readme.read_text(encoding="utf-8").splitlines() if "(skills/" + ROOT.name + "/" in line)
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
                   if p.is_file() and p.name != MANIFEST and not IGNORED_PARTS & set(p.parts) and p.suffix != ".pyc"}
        self.assertEqual(sorted(shipped - set(listed)), [], "files missing from " + MANIFEST)
        self.assertEqual(sorted(set(listed) - shipped), [], "manifest lists absent files")


if __name__ == "__main__":
    unittest.main()

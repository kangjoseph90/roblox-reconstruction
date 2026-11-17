"""Mechanical checks for an analysis document written from a recovered repository.

Two checks, no interpretation:
- citations: every `instance/path:LINE[-END]` in backticks names a recovered script and a line inside it;
- coverage: distinct modules (one per bytecode) whose name, function names or table keys never appear in
  the document, as candidates for a follow-up pass. Mentioning a name is not proof that it was analysed.
"""
import json
from pathlib import Path
import re

CITATION = re.compile(r"`([^`\s:][^`:]*?):(\d+)(?:-(\d+))?`")
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
FUNCTION_NAME = re.compile(r"\bfunction\s+(?:[A-Za-z_][\w]*[.:])*([A-Za-z_]\w{3,})\s*\(")
FIELD_NAME = re.compile(r"\b([A-Z][A-Za-z0-9_]{3,})\s*=(?!=)")
# Roblox-provided player scripts that ship with every experience.
DEFAULT_SCRIPTS = {"PlayerModule", "RbxCharacterSounds", "ChatScript", "BubbleChat", "Animate", "Health"}


def load_scripts(path):
    """Return (repository directory, script records) for a run root or its repository/."""
    root = Path(path)
    for candidate in (root / "repository", root):
        report = candidate / "report.json"
        if report.is_file():
            scripts = json.loads(report.read_text(encoding="utf-8-sig")).get("scripts")
            if isinstance(scripts, list):
                return candidate, scripts
    raise ValueError("no repository report.json with scripts under " + str(root))


def _source(repo, script):
    path = repo / script.get("directory", "") / "init.luau"
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None


def _line_count(text):
    return text.count("\n") + (0 if not text or text.endswith("\n") else 1)


def check_citations(repo, scripts, document):
    """Resolve each cited instance path, with or without its DataModel root, and check the line range."""
    by_path = {}
    for s in scripts:
        parts = s.get("instance_path") or []
        for key in ("/".join(parts), "/".join(parts[1:])):
            by_path.setdefault(key, s)
    lines, invalid, total = {}, [], 0
    for path, start, end in CITATION.findall(document):
        script = by_path.get(path.strip().strip("/"))
        if script is None:
            if "/" in path:  # plain `a:1` style text is not a module citation
                total += 1
                invalid.append({"citation": "%s:%s" % (path, end and start + "-" + end or start), "issue": "unknown_module"})
            continue
        total += 1
        key = script.get("directory")
        if key not in lines:
            text = _source(repo, script)
            lines[key] = None if text is None else _line_count(text)
        first, last = int(start), int(end or start)
        if lines[key] is None:
            issue = "no_source"
        elif not 1 <= first <= last <= lines[key]:
            issue = "line_out_of_range"
        else:
            continue
        invalid.append({"citation": "%s:%s" % (path, end and start + "-" + end or start), "issue": issue,
                        "source_lines": lines[key]})
    return total, invalid


def distinct_modules(scripts):
    """One record per bytecode, keeping the shortest instance path; Roblox default scripts are skipped."""
    chosen = {}
    for s in scripts:
        parts = s.get("instance_path") or []
        if not parts or DEFAULT_SCRIPTS & set(parts):
            continue
        meta = s.get("metadata") or {}
        identity = meta.get("bytecode_sha256") or meta.get("source_sha256") or s.get("directory")
        current = chosen.get(identity)
        if current is None or (len(parts), parts) < (len(current["instance_path"]), current["instance_path"]):
            chosen[identity] = s
    return list(chosen.values())


def check_coverage(repo, scripts, document):
    words = set(WORD.findall(document))
    mentioned, unmentioned = 0, []
    for s in distinct_modules(scripts):
        parts = s["instance_path"]
        text = _source(repo, s) or ""
        names = set(FUNCTION_NAME.findall(text)) | set(FIELD_NAME.findall(text))
        hits = names & words
        if parts[-1] in words or "/".join(parts[1:]) in document or len(hits) >= 2:
            mentioned += 1
            continue
        unmentioned.append({"path": "/".join(parts[1:]), "class": s.get("class"), "lines": _line_count(text)})
    unmentioned.sort(key=lambda r: (-r["lines"], r["path"]))
    folders = {}
    for r in unmentioned:
        folder = r["path"].rsplit("/", 1)[0] if "/" in r["path"] else ""
        folders[folder] = folders.get(folder, 0) + 1
    return mentioned, unmentioned, dict(sorted(folders.items(), key=lambda kv: (-kv[1], kv[0])))


def check_analysis(repository, document_path, limit=40):
    repo, scripts = load_scripts(repository)
    document = Path(document_path).read_text(encoding="utf-8")
    total, invalid = check_citations(repo, scripts, document)
    mentioned, unmentioned, folders = check_coverage(repo, scripts, document)
    shown = unmentioned if limit is None else unmentioned[:limit]
    return {
        "status": "pass" if not invalid else "invalid_citations",
        "citations": {"total": total, "invalid": len(invalid), "issues": invalid if limit is None else invalid[:limit]},
        "coverage": {
            "modules": mentioned + len(unmentioned), "mentioned": mentioned, "unmentioned": len(unmentioned),
            "unmentioned_by_folder": folders, "largest_unmentioned": shown,
            "note": "Name matching only: unmentioned modules are candidates for another pass, not proven gaps.",
        },
    }

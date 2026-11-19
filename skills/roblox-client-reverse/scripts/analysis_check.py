"""Mechanical checks for an analysis document written from a recovered repository.

Also renders a folder map of distinct modules, for the whole repository or for the unmentioned ones.

Two checks, no interpretation:
- citations: every `instance/path:LINE[-END]` (in backticks, or unquoted without spaces) names a recovered
  script and a line inside it; citation-like text in another form is reported as malformed, not skipped;
- coverage: distinct logical modules/versions whose name, function names or table keys never appear in
  the document, as candidates for a follow-up pass. Mentioning a name is not proof that it was analysed.
"""
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re

CITATION = re.compile(r"`([^`\s:][^`:]*?):(\d+)(?:-(\d+))?`")
# Unquoted citations (for example in pipe-separated tables) need a slash and cannot contain spaces.
BARE_CITATION = re.compile(r"(?<![\w/.`@-])((?:[\w.@\-\[\]]+/)+[\w.@\-\[\]]+):(\d+)(?:-(\d+))?(?![\w`-])")
QUOTED = re.compile(r"`[^`]*`")
# Text with a path separator followed by :DIGIT looks like a citation; URLs are not citations.
CITATION_LIKE = re.compile(r"/.*:\d")
# `game.ReplicatedStorage.Module:12` uses Roblox dot notation; citations need slash-separated paths.
DOTTED_PATH = re.compile(r"(?:game|workspace|[A-Z]\w*)(?:\.[A-Za-z_]\w*)+")
# After a complete unquoted citation, another line number (`:2:999`, `:2–999`, `:2..999`) means a range or
# position the checker would not see; it is reported instead of truncated.
TRAILING_LINE = re.compile(r"[^\S\n]*(?::|\u2013|\u2014|~|\.\.)[^\S\n]*\d|[^\S\n]+-[^\S\n]*\d")  # within one line
TOKEN_EDGE = "()[]{}<>.,;!?\"'*_"
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
FUNCTION_NAME = re.compile(r"\bfunction\s+(?:[A-Za-z_][\w]*[.:])*([A-Za-z_]\w{3,})\s*\(")
FIELD_NAME = re.compile(r"\b([A-Z][A-Za-z0-9_]{3,})\s*=(?!=)")
# Roblox-provided player scripts that ship with every experience.
DEFAULT_SCRIPTS = {"PlayerModule", "RbxCharacterSounds", "ChatScript", "BubbleChat", "Animate", "Health"}
# The containers into which Roblox inserts them; the same names anywhere else are treated as game code
# (character copies in Workspace cannot be told apart from game models by name and stay listed).
STARTER_CONTAINERS = {"StarterPlayerScripts", "StarterCharacterScripts"}


def is_default_script(parts, rooted=True):
    """A default player script (or its descendant) directly in StarterPlayer's script containers or in a player's
    PlayerScripts, counted from the top-level service (after the DataModel root when `rooted`)."""
    top = list(parts[1:] if rooted else parts)
    if len(top) >= 3 and top[0] == "StarterPlayer" and top[1] in STARTER_CONTAINERS and top[2] in DEFAULT_SCRIPTS:
        return True
    return len(top) >= 4 and top[0] == "Players" and top[2] == "PlayerScripts" and top[3] in DEFAULT_SCRIPTS


def _same(a, b):
    """Same file or directory, also on case-insensitive file systems and through hard links."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return Path(a).resolve() == Path(b).resolve()


def checked_output(path, repo, inputs=()):
    """Refuse an output that would replace an input document or write inside the recovered repository."""
    target = Path(path).resolve()
    if any(_same(target, p) for p in inputs):
        raise ValueError("output would replace an input: " + str(target))
    # Compare every existing ancestor with the repository by file identity, not by spelling.
    for candidate in (target, *target.parents):
        if candidate.exists() and _same(candidate, repo):
            raise ValueError("output must be outside the recovered repository: " + str(target))
    return target


def load_scripts(path):
    """Return (repository directory, script records) for a run root or its repository/.

    Capture recoveries root every instance path at the DataModel; supplied-file extraction records paths
    as found in the file. `display_root` tells map/coverage output whether to drop that first element."""
    root = Path(path)
    for candidate in (root / "repository", root):
        report = candidate / "report.json"
        if report.is_file():
            value = json.loads(report.read_text(encoding="utf-8-sig"))
            scripts = value.get("scripts")
            if isinstance(scripts, list):
                if value.get("operation") == "extract":
                    scripts = [dict(s, display_root=True) for s in scripts]
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
        # Capture paths may be cited with or without the DataModel root; supplied-file paths only in full.
        for key in {"/".join(parts), "/".join(shown_path(s))}:
            by_path.setdefault(key, []).append(s)
    lines, invalid, total = {}, [], 0
    found = CITATION.findall(document) + BARE_CITATION.findall(QUOTED.sub(" ", document))
    for text in malformed_citations(document, set(by_path)):
        total += 1
        invalid.append({"citation": text, "issue": "malformed_citation"})
    for path, start, end in found:
        candidates = by_path.get(path.strip().strip("/"))
        if candidates is None:
            if "/" in path:  # plain `a:1` style text is not a module citation
                total += 1
                invalid.append({"citation": "%s:%s" % (path, end and start + "-" + end or start), "issue": "unknown_module"})
            continue
        total += 1
        identities = {(s.get("bytecode", {}).get("format"), s.get("bytecode", {}).get("sha256") or (s.get("metadata") or {}).get("bytecode_sha256"),
                       s.get("source_sha256"), s.get("class")) for s in candidates}
        if len(identities) > 1:
            invalid.append({"citation": path + ":" + start, "issue": "ambiguous_module"})
            continue
        script = candidates[0]
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


def malformed_citations(document, known=()):
    """Citation-like text that neither citation form matches completely, so it would otherwise go unchecked.

    `known` holds recovered module paths; a dotted backtick path that names one (for example a supplied
    `Main.client.luau`) is a citation, not Roblox dot notation."""
    found = []
    for span in QUOTED.findall(document):
        inner = span[1:-1]
        whole = CITATION.fullmatch(span)
        if "://" in inner:
            continue
        named = inner.split(":", 1)[0].strip() in known  # for example `Main.client.luau:2, 9`
        if ((CITATION_LIKE.search(inner) or named) and re.search(r":\d", inner) and not whole) or (
                whole and whole.group(1) not in known and DOTTED_PATH.fullmatch(whole.group(1))):
            found.append(inner)
    # Backtick spans are blanked but keep their line breaks, so line-bound checks still see line ends.
    unquoted = QUOTED.sub(lambda m: "\n" * m.group().count("\n") or " ", document)
    for m in BARE_CITATION.finditer(unquoted):
        # A second line number after a complete citation, even across spaces (`path:2 – 9`), is a range
        # the checker would otherwise truncate.
        trailing = TRAILING_LINE.match(unquoted, m.end())
        if trailing:
            found.append(unquoted[m.start():trailing.end()])
    for token in re.split(r"[\s|]+", unquoted):
        token = token.strip(TOKEN_EDGE)
        if not CITATION_LIKE.search(token) or "://" in token:
            continue
        # A token holding a complete citation followed by punctuation (`path:12:`, `(path:12)`) is checked as
        # that citation; text without a complete citation, or with another line number after one, is malformed.
        if not BARE_CITATION.search(token):
            found.append(token)
    return found


def shown_path(script):
    """Instance path without its DataModel root, unless the paths have no such root."""
    parts = script.get("instance_path") or []
    return list(parts) if script.get("display_root") else list(parts[1:])


def distinct_modules(scripts):
    """One record per logical path/content/class, preserving distinct aliases and versions."""
    chosen = {}
    for s in scripts:
        parts = s.get("instance_path") or []
        if not parts or is_default_script(parts, not s.get("display_root")) or (s.get("recovery") and not s.get("source")):
            continue  # default player scripts and modules without recovered source are not mapped
        meta = s.get("metadata") or {}
        code = s.get("bytecode", {})
        digest = code.get("sha256") or meta.get("bytecode_sha256") or s.get("source_sha256") or meta.get("source_sha256") or s.get("directory")
        identity = (tuple(parts), s.get("class"), code.get("format"), digest)
        current = chosen.get(identity)
        if current is None or (len(parts), parts) < (len(current["instance_path"]), current["instance_path"]):
            chosen[identity] = s
    return list(chosen.values())


def check_coverage(repo, scripts, document):
    words = set(WORD.findall(document))
    mentioned, unmentioned = 0, []
    modules = distinct_modules(scripts)
    names_by_count = Counter(s["instance_path"][-1] for s in modules)
    for s in modules:
        parts = s["instance_path"]
        text = _source(repo, s) or ""
        names = set(FUNCTION_NAME.findall(text)) | set(FIELD_NAME.findall(text))
        hits = names & words
        variant = bool((s.get("metadata") or {}).get("variant"))
        labels = ["/".join(parts), "/".join(shown_path(s))]
        if names_by_count[parts[-1]] == 1:
            labels.append(parts[-1])
        explicit = any(label and re.search(r"(?<![\w@/-])" + re.escape(label) + r"(?![\w@/-])", document) for label in labels)
        needs_explicit_name = variant or "@" in parts[-1]
        if explicit or (not needs_explicit_name and (parts[-1] in words or len(hits) >= 2)):
            mentioned += 1
            continue
        unmentioned.append({"path": "/".join(shown_path(s)), "class": s.get("class"), "lines": _line_count(text)})
    unmentioned.sort(key=lambda r: (-r["lines"], r["path"]))
    folders = {}
    for r in unmentioned:
        folder = r["path"].rsplit("/", 1)[0] if "/" in r["path"] else ""
        folders[folder] = folders.get(folder, 0) + 1
    return mentioned, unmentioned, dict(sorted(folders.items(), key=lambda kv: (-kv[1], kv[0])))


def render_map(paths, title):
    """Markdown folder map: distinct module count per folder (paths deeper than three levels are grouped at
    three) and the ten most common next path elements, ties by name."""
    groups = defaultdict(list)
    for parts in paths:
        groups["/".join(parts[:3]) if len(parts) > 3 else "/".join(parts[:-1])].append(parts)
    lines = ["# " + title, "",
             "Distinct modules (one per logical path, class, encoding and bytecode or source) per folder, with the most common names below it. Instance paths "
             "resolve to source directories through repository/report.json.", ""]
    for folder, members in sorted(groups.items()):
        depth = len(folder.split("/")) if folder else 0
        children = Counter(m[depth] for m in members if len(m) > depth)
        top = sorted(children.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        names = ", ".join(n + ("(%d)" % c if c > 1 else "") for n, c in top)
        lines.append("%5d  %s%s" % (len(members), folder or "(root)", "  - " + names if names else ""))
    return "\n".join(lines) + "\n"


def repository_map(repository, out=None):
    repo, scripts = load_scripts(repository)
    if out is not None:
        checked_output(out, repo)
    return render_map([shown_path(s) for s in distinct_modules(scripts)], "Repository map")


def check_analysis(repository, document_path, limit=40, map_path=None):
    repo, scripts = load_scripts(repository)
    if map_path:
        map_path = checked_output(map_path, repo, [document_path])
    document = Path(document_path).read_text(encoding="utf-8")
    total, invalid = check_citations(repo, scripts, document)
    mentioned, unmentioned, folders = check_coverage(repo, scripts, document)
    shown = unmentioned if limit is None else unmentioned[:limit]
    if map_path:
        Path(map_path).parent.mkdir(parents=True, exist_ok=True)
        Path(map_path).write_text(render_map([r["path"].split("/") for r in unmentioned],
                                             "Modules not mentioned by " + Path(document_path).name), encoding="utf-8")
    return {
        "status": "pass" if not invalid else "invalid_citations",
        "citations": {"total": total, "invalid": len(invalid), "issues": invalid if limit is None else invalid[:limit]},
        "coverage": {
            "modules": mentioned + len(unmentioned), "mentioned": mentioned, "unmentioned": len(unmentioned),
            "unmentioned_by_folder": folders, "largest_unmentioned": shown,
            "note": "Name matching only: unmentioned modules are candidates for another pass, not proven gaps.",
        },
    }

"""The impact map: where else a change's old values still live (2026-11-04).

A section of the request, made by the tool at hand-off from Git objects alone.
Facts for the judge to open, with no conclusion attached: the tool gains no
judgment, and a row is a place to look, never a claim of a defect.

Why it exists, measured (recall inventory, 2026-11-04): all 10 causes of the
replay's real suite defects were a second place the change did not carry
along, while 97% of round findings sit inside the diff. Astra named 3 of 4
where the file at fault was in the diff, 1 of 6 where it was not; 4 of the 5
misses with a rollout never opened that file. On those 10 diffs this bundle
shows 4 of the 6 misses (a pinned sentence, a version's floor, a registry, a
merge's pinned spelling) and not the other 2. Those are training numbers: the
rules were tuned looking at those causes. Precision is low, 4 true rows in the
76 those four diffs show, so a row costs a read and proves nothing. Bounded in
rows and bytes because the request is already large: a median of 4.5 rows and
about 610 bytes on a control of 8 real round-1 diffs, 7 KB at most.

Three kinds, each ranked and capped by `select`:

  paired    a `KEY = old` the change moved to `KEY = new`, whose old value still
            stands in value position in another file: a version and its floor
  residual  a string the change removed from everywhere it touched (quoted text,
            an identifier, a version, a whole line, a run of six words in prose,
            a deleted path) that a file still names: a test pinning a sentence
  registry  a non-test, non-Markdown file naming three or more siblings of an
            item this change added to a list, close together, and not the item

Refused as noise on the same measurement, and so not here: an enclosing symbol
named in a test, sibling rows in test files, stand-ins naming a changed script.

What it reads: the target's tree, by object id, never the working tree. What it never does: refuse. A map is a convenience for the reader, so any
failure to compute one prints a line saying so and the hand-off goes on.
"""
from __future__ import annotations

import collections
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

from .config import caller_env

MAX_ROWS = 60
MAX_BYTES = 7168
PER_TOKEN = 6
#: A token named more often than this is a word, not a distinctive string.
MAX_HITS = 12
#: The most strings one scan looks for, shortest first. A change past that, or
#: a tree past `MAX_TREE_BYTES`, is mapped in part, and the section says so.
MAX_PATTERNS = 20000
MAX_FILE_BYTES = 1 << 20
MAX_TREE_BYTES = 64 << 20
#: The whole computation's wall-clock limit (L29f7f0663a rounds 1 and 2). One
#: boundary and not a count per phase: a regular expression can backtrack inside
#: a single charged step, and hits multiply before a later phase can charge them.
#: Measured runs take 1 to 16 s. An advisory map never holds a mandatory hand-off.
MAX_SECONDS = 30
LINE_SHOWN = 140
TOKEN_SHOWN = 80
_AROUND = 40

HEADING = "## Impact map"
_NOTE = ("Places the change may not have carried along, computed by the tool "
         "from the diff and the target's tree. Each row is a place to open, "
         "never a claim; most are nothing.")

_TEST = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*$")
_PROSE_FILE = re.compile(r"\.(md|toml|txt)$")
_QUOTED = re.compile(r'''"([^"\n]{6,120})"|'([^'\n]{6,120})\'''')
_IDENTIFIER = re.compile(r"\b[A-Za-z_][\w]*(?:[.-][A-Za-z_][\w]*)+\b"
                         r"|\b[A-Z][A-Z0-9_]{5,}\b"
                         r"|\b[a-z]+(?:_[a-z0-9]+){1,}\b")
_VERSION = re.compile(r"\b\d+\.\d+\.\d+\b")
_WORD = re.compile(r"[A-Za-z0-9`'’,;:-]+")
_STOPWORDS = frozenset(
    "function return import default string number value should result output "
    "option options command message because between section reviewer request "
    "verdict handoff".split())
_ASSIGNED = re.compile(r"""^\s*([\w.-]+)\s*(?::\s*\w+\s*)?[:=]\s*["']?"""
                       r"""([^"'\s,#]{3,})["']?\s*,?\s*(?:#.*)?$""")
_ITEM = re.compile(r"""["']([\w.-]{3,60})["']""")
_LIST_LINE = re.compile(r"""(?:[\[({]?\s*["'][\w.-]{3,60}["']\s*"""
                        r"""(?::\s*[^,]+)?[,\])}]*\s*)+""")
_WINDOW = 6
_SIBLINGS = 3
_SIBLING_SPAN = 12


class _Abandoned(BaseException):
    """The time limit. Not an `Exception`, so no reader on the way out, `git`'s
    or an attribute lookup's, can take it for a failure of its own and go on."""


class MapUnavailable(RuntimeError):
    """A git read the map needs did not answer."""


def _git_bytes(repo_root, *args: str, stdin: str | None = None,
               seconds: int = 120, no_replace: bool = False,
               git_options: tuple[str, ...] = ()) -> bytes:
    try:
        out = subprocess.run(
            ["git", *(["--no-replace-objects"] if no_replace else []),
             "-C", str(repo_root), *git_options, *args],
            capture_output=True, input=(stdin or "").encode("utf-8"),
            timeout=seconds, env=caller_env())
    except (subprocess.SubprocessError, OSError) as exc:
        raise MapUnavailable(f"reading {args[0]}: {type(exc).__name__}") from exc
    if out.returncode != 0:
        raise MapUnavailable(f"reading {args[0]} exited {out.returncode}")
    return out.stdout


def _git(repo_root, *args: str, **kwargs) -> str:
    return _git_bytes(repo_root, *args, **kwargs).decode("utf-8", "replace")


def _changed_paths(repo_root, spec: str, seconds: int) -> tuple[list, list]:
    """(the paths the span adds or changes, the paths it deletes), rename
    detection off, so a rename counts as a deletion and an addition."""
    raw = _git(repo_root, "diff", "--name-status", "--no-renames", "-z", spec,
               seconds=seconds, no_replace=True)
    fields = raw.split("\0")
    live, gone = [], []
    for status, path in zip(fields[0::2], fields[1::2]):
        (gone if status.startswith("D") else live).append(path)
    return live, gone


def _hunks(repo_root, spec: str, paths: list, seconds: int) -> dict:
    """{path: {"add": [...], "rem": [...]}} of the span's changed lines."""
    files: dict = collections.defaultdict(lambda: {"add": [], "rem": []})
    for start in range(0, len(paths), 200):
        chunk = paths[start:start + 200]
        raw = _git(repo_root, "diff", "-U0", "--no-renames", "--no-ext-diff",
                   "--no-textconv", "--no-color", "--src-prefix=a/",
                   "--dst-prefix=b/", spec, "--",
                   *(f":(literal){p}" for p in chunk), seconds=seconds,
                   no_replace=True, git_options=("-c", "core.quotepath=off"))
        current = None
        old = None
        for line in raw.split("\n"):
            if line.startswith("--- "):
                old = line[6:] if line.startswith("--- a/") else None
            elif line.startswith("+++ "):
                current = line[6:] if line.startswith("+++ b/") else old
            elif line.startswith("@@"):
                continue
            elif current and line.startswith("+"):
                files[current]["add"].append(line[1:])
            elif current and line.startswith("-"):
                files[current]["rem"].append(line[1:])
    return files


def _tokens(line: str) -> set:
    found = set()
    for match in _QUOTED.finditer(line):
        text = (match.group(1) or match.group(2)).strip()
        if len(text) >= 6:
            found.add(text)
    for match in _IDENTIFIER.finditer(line):
        text = match.group(0)
        if len(text) >= 8 and text.lower() not in _STOPWORDS:
            found.add(text)
    found.update(_VERSION.findall(line))
    return found


def _prose_runs(line: str) -> set:
    words = _WORD.findall(line)
    return {" ".join(words[i:i + 6]) for i in range(0, max(0, len(words) - 5))}


class _Tree:
    """The text files at `sha` a row may name, read once, by `cat-file`.

    Not `git grep`: it tries each fixed pattern in turn on each line, and a
    change's removed strings run to thousands (a first cut took over 100 s on
    a 1,271-line change). One scan with a prefix table over about 15 MB takes
    seconds. A file over `MAX_FILE_BYTES`, or holding a NUL, is not text here;
    past `MAX_TREE_BYTES` the rest are not read, and the section says so.
    """

    def __init__(self, repo_root, sha: str, live, seconds: int):
        self.capped = False
        self.text: dict[str, str] = {}
        listing = _git(repo_root, "ls-tree", "-r", "-l", "-z", sha,
                       seconds=seconds, no_replace=True)
        wanted, total = [], 0
        for row in listing.split("\0"):
            meta, tab, path = row.partition("\t")
            fields = meta.split()
            if not tab or len(fields) != 4 or fields[1] != "blob" \
                    or not fields[3].isdigit():
                continue
            size = int(fields[3])
            if size > MAX_FILE_BYTES or not live(path):
                continue
            total += size
            if total > MAX_TREE_BYTES:
                self.capped = True
                break
            wanted.append((path, fields[2], size))
        raw = _git_bytes(repo_root, "cat-file", "--batch",
                         stdin="".join(f"{oid}\n" for _, oid, _ in wanted),
                         seconds=seconds, no_replace=True)
        at = 0
        for path, _oid, size in wanted:
            at = raw.index(b"\n", at) + 1
            blob = raw[at:at + size]
            at += size + 1
            if b"\0" not in blob:
                self.text[path] = blob.decode("utf-8", "replace")

    def scan(self, patterns) -> tuple[dict, bool]:
        """({pattern: [(path, line number, text)]}, whether capped)."""
        wanted = sorted({p for p in patterns
                         if p and "\n" not in p and "\0" not in p},
                        key=lambda p: (len(p), p))
        capped = len(wanted) > MAX_PATTERNS
        wanted = wanted[:MAX_PATTERNS]
        found: dict = collections.defaultdict(list)
        index = collections.defaultdict(list)
        for pattern in wanted:
            index[pattern[:5]].append(pattern)
        if not index:
            return found, capped
        # What is too short to be worth a row is each producer's to say, for
        # prose; a short version, value, item or path is a candidate here.
        sizes = sorted({len(key) for key in index})
        for path, blob in self.text.items():
            for number, line in enumerate(blob.split("\n"), 1):
                line = line[:800]
                seen = set()
                for i in range(len(line)):
                    for size in sizes:
                        for pattern in index.get(line[i:i + size], ()):
                            if pattern not in seen \
                                    and line.startswith(pattern, i):
                                seen.add(pattern)
                                found[pattern].append((path, number, line))
        return found, capped


def _residual_patterns(files, tree, gone_paths) -> set:
    removed = collections.defaultdict(set)
    for path, change in files.items():
        for line in change["rem"]:
            stripped = line.strip()
            candidates = _tokens(line)
            if len(stripped) >= 14:
                candidates.add(stripped)
            if not _TEST.search(path) and _PROSE_FILE.search(path):
                candidates |= _prose_runs(line)
            for candidate in candidates:
                removed[candidate].add(path)
    gone = {token for token, where in removed.items()
            if all(token not in tree(p) for p in where)}
    gone.update(gone_paths)
    return gone


def _residual_rows(patterns, hits) -> list:
    rows = []
    for token in patterns:
        places = hits.get(token, ())
        if len(places) <= MAX_HITS:
            rows.extend(("residual", path, number, token, text)
                        for path, number, text in places)
    return rows


def _paired_patterns(files) -> dict:
    old_values = {}
    for path, change in files.items():
        removed = {}
        for line in change["rem"]:
            if match := _ASSIGNED.match(line):
                removed[match.group(1)] = match.group(2)
        for line in change["add"]:
            match = _ASSIGNED.match(line)
            if match and match.group(1) in removed \
                    and removed[match.group(1)] != match.group(2):
                old_values[removed[match.group(1)]] = (match.group(1), path)
    return old_values


def _paired_rows(old_values, hits) -> list:
    rows = []
    for value, (key, source) in old_values.items():
        in_value = re.compile(r"""[=:]\s*["']?""" + re.escape(value)
                              + r"""["']?\s*,?\s*(#.*)?$""")
        rows.extend(("paired", path, number,
                     f"{key} was {value} in {source}", text)
                    for path, number, text in hits.get(value, ())
                    if path != source and in_value.search(text))
    return rows


def _registry_items(files, tree) -> dict:
    """{(the file, an item this change added to a list): its siblings there}."""
    items = {}
    for path, change in files.items():
        if _TEST.search(path):
            continue
        removed = "\n".join(change["rem"])
        lines = None
        for line in change["add"]:
            stripped = line.strip()
            if not _LIST_LINE.fullmatch(stripped):
                continue
            if lines is None:
                lines = tree(path).splitlines()
            for item in _ITEM.findall(stripped):
                if f'"{item}"' in removed or f"'{item}'" in removed:
                    continue
                quoted = re.compile(r"""["']""" + re.escape(item)
                                    + r"""["']""")
                for i, text in enumerate(lines):
                    if quoted.search(text):
                        near = set()
                        for j in range(max(0, i - _WINDOW),
                                       min(len(lines), i + _WINDOW + 1)):
                            near.update(_ITEM.findall(lines[j]))
                        near.discard(item)
                        if len(near) >= _SIBLINGS:
                            items[(path, item)] = near
                        break
    return items


def _registry_rows(items, hits, tree) -> list:
    """One row a file: not a test, not Markdown, naming `_SIBLINGS` siblings
    close together and not the item."""
    by_file = collections.defaultdict(lambda: collections.defaultdict(list))
    for near in items.values():
        for sibling in near:
            for path, number, _ in hits.get(sibling, ()):
                if not _TEST.search(path) and not path.endswith(".md"):
                    by_file[path][sibling].append(number)
    rows = {}
    for (source, item), near in items.items():
        for path, named in by_file.items():
            if path == source or path in rows:
                continue
            present = sorted({(n, s) for s in near & set(named)
                              for n in named[s]})
            start = next((n for n, _ in present
                          if len({s for m, s in present
                                  if n <= m <= n + _SIBLING_SPAN})
                          >= _SIBLINGS), None)
            if start is None or item in tree(path):
                continue
            siblings = sorted({s for m, s in present
                               if start <= m <= start + _SIBLING_SPAN})
            rows[path] = ("registry", path, start,
                          f"lists siblings of {item}, new in {source}, "
                          f"and not it", ",".join(siblings))
    return list(rows.values())


def clean(text: str, shown: int) -> str:
    """One line a request can carry whatever the target's files hold.

    Every whitespace `str.splitlines` would break on goes (`_split_sections`
    reads a heading off any of them), and a closing wrapper or a code fence
    that a test file pins cannot close the envelope or open a block here.
    """
    text = " ".join(text.split())
    text = text.replace("</", "<\\/").replace("```", "'''")
    text = "".join(c if c.isprintable() else "?" for c in text)
    return text[:shown - 1] + "…" if len(text) > shown else text


def select(rows: list) -> tuple[list, int]:
    """(the rows shown, how many were left out): paired first, then rows in
    test files, then residual rows, at most `PER_TOKEN` a token, `MAX_ROWS`
    in all and `MAX_BYTES` of text."""
    ordered = sorted(
        set(rows),
        key=lambda r: (r[0] != "paired", not _TEST.search(r[1]),
                       r[0] != "residual", r[1], r[2], r[3]))
    per_token: collections.Counter = collections.Counter()
    places: set = set()
    shown, size, left = [], 0, 0
    for row in ordered:
        if (row[1], row[2]) in places:    # overlapping runs of one sentence
            continue
        if per_token[row[3]] >= PER_TOKEN:
            left += 1
            continue
        line = render_row(row)
        if len(shown) >= MAX_ROWS or size + len(line.encode("utf-8")) > MAX_BYTES:
            left += 1
            continue
        per_token[row[3]] += 1
        places.add((row[1], row[2]))
        shown.append(line)
        size += len(line.encode("utf-8")) + 1
    return shown, left


def render_row(row) -> str:
    """A residual row shows the string where it stands, marked, with the line
    around it; a paired or registry row says what it found and shows the line."""
    kind, path, number, token, text = row
    at = text.find(token) if kind == "residual" else -1
    if at >= 0:
        shown = (clean(text[max(0, at - _AROUND):at], _AROUND)
                 + f" [{clean(token, TOKEN_SHOWN)}] "
                 + clean(text[at + len(token):at + len(token) + _AROUND],
                         _AROUND)).strip()
    else:
        shown = f"[{clean(token, TOKEN_SHOWN)}] {clean(text, LINE_SHOWN)}"
    return f"- {kind} {clean(path, 120)}:{number} {shown}"


def compute(repo_root, base: str, head: str, skip=(), seconds: int = 120,
            live=None) -> dict:
    """{"rows": lines, "omitted": n, "capped": bool}; raises MapUnavailable.

    `skip` is a function from paths to the subset the target's attributes
    mark generated: nothing they removed is read, and no row names one.
    """
    spec = f"{base}...{head}"
    mark = skip if callable(skip) else (lambda paths: set(skip) & set(paths))
    live_paths, gone = _changed_paths(repo_root, spec, seconds)
    generated = mark([*live_paths, *gone])
    read = [p for p in [*live_paths, *gone] if p not in generated]
    files = _hunks(repo_root, spec, read, seconds) if read else {}
    tree = _Tree(repo_root, head, live or live_scope, seconds)
    changed_blobs: dict = {}

    def blob(path: str) -> str:
        if path in tree.text:
            return tree.text[path]
        if path not in changed_blobs:
            try:
                changed_blobs[path] = _git(repo_root, "show", f"{head}:{path}",
                                           seconds=seconds, no_replace=True)
            except MapUnavailable:     # deleted at the target
                changed_blobs[path] = ""
        return changed_blobs[path]

    old_values = _paired_patterns(files)
    residual = _residual_patterns(files, blob, gone)
    items = _registry_items(files, blob)
    siblings = set().union(*items.values()) if items else set()
    hits, capped = tree.scan({*old_values, *residual, *siblings})
    rows = [*_paired_rows(old_values, hits), *_residual_rows(residual, hits),
            *_registry_rows(items, hits, blob)]
    marked = mark(sorted({r[1] for r in rows}))
    rows = [r for r in rows if r[1] not in marked]
    shown, omitted = select(rows)
    return {"rows": shown, "omitted": omitted,
            "capped": capped or tree.capped}


#: What is read as a record rather than as something that runs or configures
#: when it sits below the root: `README.md` and a root config stay.
_RECORD = re.compile(r"/[^/]*\.(md|txt|rst|adoc|jsonl|json|csv|tsv|html|xml|log"
                     r"|svg)$")


#: Directories that hold what was, not what runs, wherever they sit. On the
#: 8-diff control, code under `research/` took the median from 610 bytes to
#: about 1,050 and named nothing true.
_RECORD_DIR = re.compile(r"(^|/)(research|experiments|archive|vendor|"
                         r"third_party|node_modules)/")


def live_scope(path: str) -> bool:
    """The files a row may name: every tracked file that is not a record."""
    return not (_RECORD.search(path) or _RECORD_DIR.search(path))


def _within_the_limit(work):
    """`work()`, or None past `MAX_SECONDS`, with the old handler and any timer
    already running put back either way. A timer needs the main thread: off it
    this refuses rather than run with no limit."""
    if threading.current_thread() is not threading.main_thread():
        raise MapUnavailable("its time limit only works on the main thread")

    def abandon(signum, frame):
        raise _Abandoned

    started = time.monotonic()
    handler = signal.signal(signal.SIGALRM, abandon)
    delay, interval = signal.setitimer(signal.ITIMER_REAL, MAX_SECONDS)
    try:
        try:
            return work()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    except _Abandoned:
        return None
    finally:
        signal.signal(signal.SIGALRM,
                      signal.SIG_DFL if handler is None else handler)
        if delay:
            signal.setitimer(signal.ITIMER_REAL,
                             max(delay - (time.monotonic() - started), 0.001),
                             interval)


def section(repo_root, base: str | None, head: str, skip=(), seconds: int = 120,
            lineage_base: str | None = None, live=None) -> list[str]:
    """The request's section, as lines. Never raises: a map that cannot be
    computed says so in one line and the hand-off goes on, and `_within_the_limit`
    holds the whole computation to `MAX_SECONDS`.

    `lineage_base` is round 1's base on a later round: a later round's diff is
    an increment, and 2 of the 4 real rounds that missed a replay cause were
    later rounds, one on a merge. Where that base no longer reads (a rewritten
    history), the map falls back to this round's.
    """
    lines = [HEADING, "", _NOTE]
    spans = [lineage_base] if lineage_base and lineage_base != base else []

    def attempts():
        why = "this request names no base"
        for start in (*spans, base):
            if not start:
                continue
            try:
                return compute(repo_root, start, head, skip, seconds, live), start
            except (MapUnavailable, ValueError) as exc:
                why = exc
        return why, None

    try:
        outcome = _within_the_limit(attempts)
    except MapUnavailable as exc:
        return [*lines, "", f"Not computed: {exc}."]
    if outcome is None:
        return [*lines, "", f"Partial: this map was abandoned at its limit of "
                            f"{MAX_SECONDS} s and holds no rows; nothing is "
                            f"known either way about what it would have named."]
    result, start = outcome
    if start is None:
        return [*lines, "", f"Not computed: {result}."]
    if start != base:
        lines.append(f"Mapped over the whole lineage, from {start[:12]}, "
                     f"not only this round's diff.")
    lines.append("")
    if not result["rows"]:
        lines.append("No rows in what was searched." if result["capped"] else
                     "No rows: nothing the change removed or added to a list "
                     "is left behind elsewhere, by these three rules.")
    else:
        lines.extend(result["rows"])
    if result["omitted"]:
        lines += ["", f"{result['omitted']} more row(s) left out by the "
                      f"bounds: {MAX_ROWS} rows, {MAX_BYTES} bytes, "
                      f"{PER_TOKEN} per string."]
    if result["capped"]:
        lines += ["", "This change is too large to map whole: only part of "
                      "what it removed was searched."]
    return lines

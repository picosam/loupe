"""Shared fixtures for the transport test modules.

Extracted verbatim 2026-08-31 when `test_transport.py` (5,308 lines, 27 test
classes behind one filename) was split along the production seams it
exercises. Nothing here is new and nothing is duplicated: these are the only
module-level helpers the original shared across classes. Every other helper
was already private to one class, and no class in that file inherited from
another or shared a `setUp`, so the split needed no fixture surgery.

`request_text` and `evidence_cell` are also imported by other test modules;
see `test_worktree_and_brief.py` and `test_lineage_reference.py`.

Consolidated 2026-09-02: the two scaffolds that had grown by copy across
the transport modules now live here once — the warm-cache scaffold
(`warm_cache_fixture`, five copies before) and the real-CLI scratch
repository (`scratch_loop_repo` + `run_cli`, two copies before) — with the
shell helpers `sh`/`git_out` the scratch repositories are built with.
"""

import contextlib
import dataclasses
import io
import json
import re
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from review import cli, config, tool_identity, transport, vocab
from review.ledger import Ledger
from review.tests import synth
from review.tests.util import LINEAGE, REPO_ROOT

CFG = config.load(REPO_ROOT)
SHA_A, SHA_B, SHA_C = "a" * 40, "b" * 40, "c" * 40


def fake_git(mapping):
    calls = []

    def run(*args):
        calls.append(args)
        if args not in mapping:
            raise AssertionError(f"unexpected git call: {args}")
        value = mapping[args]
        if isinstance(value, Exception):
            raise value
        return value

    run.calls = calls
    return run


def authority_calls(sha=None):
    """The two reads `resolve_authority` makes about `sha` (lineage 12).

    The warm-cache path resolves the TARGET's authority to decide the role
    key (round 2 F1), so a scripted runner reaching that path has to answer
    them. Kept here rather than repeated at each fixture: a mapping that
    drifts from what the resolver asks is a fixture that tests nothing.
    """
    sha = sha or SHA_B
    return {
        ("ls-tree", "--full-tree", sha, "--", "review.toml"):
            "100644 blob " + "0" * 40 + "\treview.toml",
        ("show", f"{sha}:review.toml"):
            (REPO_ROOT / "review.toml").read_text(encoding="utf-8"),
    }


def request_text(sha=SHA_B, base=SHA_A, reviewer="codex", author="claude",
                 round_no=1, push=True, refs=None, transport_attr=None,
                 tool_attr=None):
    """A structurally valid request (round-2 F4: `take` judges the
    target-independent grammar before any git call, so a unit fixture must
    pass it — risk stated, NOT captured stated, every §5.1 section present
    in order, a reference with a digest). `refs=None` supplies review.toml
    at the digest the TestTake fake git serves; pass "" for none."""
    push_lines = ("Push:   refs/heads/main = %s @ origin "
                  "(ssh://example.invalid/x.git) — ls-remote observed after "
                  "push\nVerify: git fetch ssh://example.invalid/x.git "
                  "refs/heads/main && git cat-file -e %s\n" % (sha, sha)
                  if push else "")
    if refs is None:
        digest = transport.sha256_file(REPO_ROOT / "review.toml")
        refs = f"  review.toml  sha256:{digest}  [required] the config\n"
    # RVW-T11: absent by default, so the fixture keeps exercising what an
    # envelope emitted before the attribute existed does — the topology
    # reader has to read that absence as the default, not as an unknown.
    tr = f' transport="{transport_attr}"' if transport_attr else ""
    # Round 1 F2 put the identity in the warm-cache key, so a fixture that
    # has to REACH the checks past it must stamp one. Absent by default,
    # exactly like the transport attribute above and for the same reason:
    # what an envelope emitted before the field existed does is a state the
    # readers have to keep answering for.
    tl = f' tool="{tool_attr}"' if tool_attr else ""
    return (f'<loupe-review-request sha="{sha}" branch="main" '
            f'author="{author}" reviewer="{reviewer}" round="{round_no}"'
            f'{tr}{tl}>\n'
            f"Roles: author={author} · reviewer={reviewer} · relay=user.\n\n"
            # The emitter stopped writing "ruled on in round 0" (brief
            # `round-cap-stamp-misreports`): there is no round 0, so the
            # fixture no longer models a line the tool cannot produce.
            f"Target: {sha}\nBase:   {base}   (round 1 opens this lineage: "
            f"the base is the one the author declared)\n"
            f"Diff:   git diff {base}...{sha}\nTree:   clean at emission\n"
            f"{push_lines}"
            f"## Taxonomy\n\nSeverity, ordered:      Blocker > High > Medium "
            f"> Low > Info\nBlocking severities:    Blocker, High\n"
            f"Classification, one of:\n  factual_error\n  "
            f"internal_contradiction\n  design_gap\n  unsupported_claim\n  "
            f"process_defect\n\n## Claim\n\nObjective / decision boundary: x\n"
            f"\nSelf-assessed risk: low (fixture)\n"
            f"\nWhat changed (1 files, 1 insertions, 0 deletions = 1 changed "
            f"lines, 1 areas — machine-computed):\n\n  f.txt\n\n## Evidence\n\n"
            f"```loupe-attestations\n[]\n```\n\nNOT captured — this handoff "
            f"cannot vouch for these:\n  - (none)\n\n## Contract\n\n"
            f"(none declared)\n\n## Reference\n\n{refs}\n\n"
            f"## Review scope\n\nall\n</loupe-review-request>\n")


def verdict_text(sha=SHA_B, verdict="changes requested", findings=1):
    """The transport modules' verdict: `synth.verdict_text`'s shape with
    its evidence anchored at `f.txt` — the one file the scratch
    repositories these tests build actually carry — where synth's builder
    pins `review/wire.py` with no parameter to change it. The finding
    block itself is synth's."""
    body = f'<loupe-review-verdict sha="{sha}">\nVERDICT: {verdict}\n\n## findings\n\n'
    if verdict == "clean to advance":
        body += "None\n"
    else:
        for i in range(1, findings + 1):
            body += synth.finding(i, evidence="f.txt:1")
    body += "## evidence checked\n\nf.txt\n</loupe-review-verdict>\n"
    return body


def lineage_ledger():
    """A ledger with one completed round and a cap override — the state a
    closed lineage leaves behind."""
    ledger = Ledger.in_memory()
    ledger.add({"event": "request", "round": 1, "sha": SHA_A, "bytes": 1})
    ledger.add({"event": "verdict", "round": 1, "sha": SHA_A,
                "verdict": "changes requested", "bytes": 1, "finding_ids": 1})
    ledger.add({"event": "finding", "round": 1, "id": "F1", "fp": "fp2:1",
                "severity": "Low"})
    ledger.add({"event": "cap_override", "round_cap": 5, "reason": "r",
                "authorized_by": "user", "default_cap": 3})
    ledger.add({"event": "lineage", "kind": "alias", "from_fp": "fp1:x",
                "to_fp": "fp2:1"})
    return ledger


def _cli(fn, cfg, **kw):
    """Run one CLI command function with a Namespace built from `kw`,
    returning (exit code, parsed JSON payload)."""
    import argparse
    import contextlib
    import io
    args = argparse.Namespace(**kw)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = fn(args, cfg)
    out = buf.getvalue()
    try:
        return code, json.loads(out)
    except json.JSONDecodeError:
        return code, out


def reviewer_clone_git():
    """The reviewer clone, faked. Sweep F6: `take` reads the target's
    review.toml and every reference from the target tree, so the fake
    serves `show`/`cat-file -t` for the paths these tests reference and
    answers 'absent' (a git failure) for any other path — the same
    answer a real object store gives for a path the commit lacks."""
    toml = (REPO_ROOT / "review.toml").read_text(encoding="utf-8")
    known = {
        ("fetch", "ssh://example.invalid/x.git", "refs/heads/main"): "",
        ("cat-file", "-e", f"{SHA_B}^{{commit}}"): "",
        ("cat-file", "-e", f"{SHA_A}^{{commit}}"): "",
        ("merge-base", "--is-ancestor", SHA_A, SHA_B): "",
        ("ls-tree", "--full-tree", SHA_B, "--", "review.toml"):
                        "100644 blob 0000000\treview.toml",
                    ("show", f"{SHA_B}:review.toml"): toml,
        ("cat-file", "-t", f"{SHA_B}:review.toml"): "blob",
        ("cat-file", "-t", f"{SHA_B}:review/wire.py"): "blob",
        ("show", f"{SHA_B}:review/wire.py"): "not the manifest's bytes",
        ("cat-file", "-t", f"{SHA_B}:review"): "tree",
        # `reviewer_checkout` (2026-09-18): what this clone's working tree
        # IS. The fixture clone sits at the target with a clean tree.
        ("rev-parse", "HEAD"): SHA_B,
        ("status", "--porcelain"): "",
    }
    inner = fake_git(known)

    def run(*args):
        if args not in known and args[0] in ("cat-file", "show"):
            inner.calls.append(args)
            raise RuntimeError(f"path does not exist in {SHA_B[:7]}")
        return inner(*args)
    run.calls = inner.calls
    return run


#: What `request_text`'s reachability stamp points at — the repository the
#: fixture's reviewer clone is a clone OF. `probe_target` compares the two
#: before it fetches (`take-binds-to-caller-checkout`), so a test that wants
#: the ordinary reviewer path declares this rather than inheriting whatever
#: remotes the checkout running the suite happens to have.
REVIEWER_REMOTES = ["ssh://example.invalid/x.git"]


def ledgerless_cfg():
    """The repo config with no ledger directory — the shape every unit-level
    transport test uses, so nothing it does can reach a real ledger on disk."""
    return dataclasses.replace(CFG, ledger_dir=None)


#: The committer every fixture repository records, written into the
#: repository's own `.git/config` by `write_identity` — the bytes
#: `git config user.name a` and its two siblings would write, without the
#: three launches. It lives in the repository and not in the environment
#: because loupe itself commits in these repositories (a hand-off commits
#: outstanding work), and loupe's git calls read the repository's config.
FIXTURE_IDENTITY = (("user", "name", "a"),
                    ("user", "email", "a@example.invalid"),
                    ("commit", "gpgsign", "false"))


def identity_of(name, email):
    """The three-key identity `write_identity` writes, for `name <email>`
    with signing off — the loop `for k, v in (("user.name", ...), ...):
    git config k v` many fixtures ran, as data."""
    return (("user", "name", name), ("user", "email", email),
            ("commit", "gpgsign", "false"))


def write_identity(repo, identity=FIXTURE_IDENTITY):
    """Append `identity` ((section, key, value), ...) to `repo`'s
    `.git/config` — or to `repo/config` for a bare repository — in the
    layout `git config` writes: one `[section]` header per run of keys."""
    repo = Path(repo)
    cfg = repo / ".git" / "config"
    if not cfg.is_file():
        cfg = repo / "config"
    lines, section = [], None
    for sec, key, value in identity:
        if sec != section:
            lines.append(f"[{sec}]")
            section = sec
        lines.append(f"\t{key} = {value}")
    with cfg.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


#: Process-wide memo of fixture trees: key -> (template root, facts).
_TEMPLATES: dict = {}
_TEMPLATE_PARENT: list = []


def _template_parent():
    """One directory per test process holding every template it built,
    removed when the process exits."""
    if not _TEMPLATE_PARENT:
        import atexit
        parent = Path(tempfile.mkdtemp(prefix="loupe-fixture-templates-"))
        atexit.register(shutil.rmtree, parent, ignore_errors=True)
        _TEMPLATE_PARENT.append(parent)
    return _TEMPLATE_PARENT[0]


#: Files inside a copied tree that may carry the template's absolute path:
#: a remote URL in `config`, a fetched URL in `FETCH_HEAD`, the two halves
#: of a linked worktree (`gitdir`, and a `.git` that is a file), a
#: borrowed object store (`objects/info/alternates`, a `--shared` clone),
#: and every reflog (`logs/...`: a clone's first entry names its source).
_PATH_BEARING = frozenset({"config", "FETCH_HEAD", "gitdir", ".git",
                           "alternates"})


def copy_tree(src, dst):
    """A private, writable copy of the directory `src` at `dst` (created,
    or filled when it exists and is empty): `shutil.copytree`, no process
    launched, on every platform.

    Not `git clone`: a clone rewrites `remote.origin`, the reflog and the
    object layout, all of which a test may read. Not `cp -c -R` (an APFS
    clone): measured 2026-09-27 on this Mac at 6.8 ms and one launch per
    copy of a 45-entry fixture repository, against 5.1 ms and none for
    `copytree`; fixture trees are small enough that copy-on-write buys
    nothing. A copy shares no file with `src` or any other copy, so a
    write in one is invisible in every other.

    `src` is settled first (`settle_maintenance`): a tree whose build has
    just committed may still be held by git's detached maintenance."""
    settle_maintenance(src)
    shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True)


#: How long `settle_maintenance` waits, in seconds, before it gives up.
MAINTENANCE_SETTLE_S = 60.0


def settle_maintenance(src):
    """Return once no `maintenance.lock` exists anywhere under `src`.

    Every `git commit` runs `git maintenance run --auto --detach`, which
    takes `<objects>/maintenance.lock`, forks a daemon to hold it and
    returns; the daemon deletes the lock when it is done. So the lock of
    every git command already returned is on disk before this looks, and
    its absence means that daemon has finished. Copying while it runs is a
    race: `copytree` lists the lock, the daemon deletes it, and the copy
    fails with ENOENT — measured 2026-10-01, nightly run 36839648235 at
    7a68c0f, three gates red on a two-core runner; not reproduced on the
    author's Mac in 80 commits.
    Waiting, rather than turning maintenance off, leaves the git config
    every test exercises as git ships it."""
    deadline = time.monotonic() + MAINTENANCE_SETTLE_S
    while held := sorted(Path(src).rglob("maintenance.lock")):
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"git maintenance still holds {', '.join(map(str, held))} "
                f"after {MAINTENANCE_SETTLE_S:g}s; refusing to copy {src}")
        time.sleep(0.01)


def fixture_tree(key, build):
    """The template for `key`, built by `build(root) -> facts` the first
    time this process asks for it, returned as (root, facts). The template
    is never handed to a test: `copy_fixture` copies it."""
    if key not in _TEMPLATES:
        root = Path(tempfile.mkdtemp(prefix="t-", dir=_template_parent()))
        _TEMPLATES[key] = (root, build(root))
    return _TEMPLATES[key]


def copy_fixture(key, build, dst):
    """Copy the fixture tree `key` (built once per process by `build`) into
    `dst`, rebase every absolute path the template recorded onto `dst`, and
    return the template's facts — commit ids included, which are therefore
    the same in every copy (and within one second of each other before
    this, when each test built its own: git's timestamps are seconds, so a
    test could never rely on two builds' ids differing).

    Building once is safe only for what a template is: a pure function of
    `key`. A `build` that reads anything else (the clock aside) belongs in
    the key."""
    root, facts = fixture_tree(key, build)
    dst = Path(dst)
    copy_tree(root, dst)
    rebase_paths(dst, root)
    return facts


def assert_private_copies(case, key, build, repo="."):
    """The isolation proof every converted fixture carries: two copies of
    template `key` are two directories, neither of them the template; the
    git repository at `repo` inside each carries the template's HEAD with
    the template's working-tree status (read without the optional index
    refresh, so the proof never writes the template); and a commit made in
    the first copy moves neither the second nor the template. Returns
    (facts, first copy, second copy) for the fixture's own id assertions."""
    root, facts = fixture_tree(key, build)
    copies = []
    for _ in range(2):
        dst = scratch_tmp(case, "copy-proof-")
        case.assertEqual(copy_fixture(key, build, dst), facts)
        copies.append(dst)
    case.assertEqual(len({c.resolve() for c in copies} | {root.resolve()}), 3,
                     "a copy is the template or the other copy")
    head = git_out(root / repo, "rev-parse", "HEAD")
    status = _status(root / repo)
    for c in copies:
        case.assertEqual(git_out(c / repo, "rev-parse", "HEAD"), head)
        case.assertEqual(_status(c / repo), status)
    first = copies[0] / repo
    (first / "copy-proof.txt").write_text("first copy only\n",
                                          encoding="utf-8")
    sh("git", "-C", str(first), "add", "copy-proof.txt")
    sh("git", "-C", str(first), "-c", "user.name=p",
       "-c", "user.email=p@example.invalid", "commit", "-q", "-m", "proof")
    case.assertNotEqual(git_out(first, "rev-parse", "HEAD"), head)
    for untouched in (copies[1] / repo, root / repo):
        case.assertEqual(git_out(untouched, "rev-parse", "HEAD"), head)
        case.assertFalse((untouched / "copy-proof.txt").exists())
        case.assertEqual(_status(untouched), status)
    return facts, copies[0], copies[1]


def _status(repo):
    """`git status --porcelain` that takes no optional lock, so reading a
    template never rewrites its index."""
    return git_out(repo, "--no-optional-locks", "status", "--porcelain")


def rebase_paths(dst, root):
    """Rewrite every absolute path to `root` that git metadata under `dst`
    records (`_path_bearing`) into the same path under `dst`."""
    dst = Path(dst)
    # Each spelling of the template's root onto the same spelling of the
    # copy's: git records a linked worktree by its resolved path and a
    # remote by the path it was given, and on macOS the two differ
    # (`/var/folders` is `/private/var/folders`). Longest first, so the
    # resolved form is never half-matched by the plain one inside it.
    pairs = sorted({(os.path.realpath(root), os.path.realpath(dst)),
                    (str(root), str(dst))}, key=lambda p: -len(p[0]))
    for path in _path_bearing(dst):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new = text
        for old, rebased in pairs:
            new = new.replace(old, rebased)
        if new != text:
            path.write_text(new, encoding="utf-8")


class _BuildCase:
    """The one-shot test case a template's own constructor runs under: a
    cleanup it registers is run when the build ends, a skip it asks for
    is a skip of whoever asked for the template."""

    def __init__(self):
        self.cleanups = []

    def addCleanup(self, fn, *args, **kw):
        self.cleanups.append((fn, args, kw))

    def skipTest(self, why):
        raise unittest.SkipTest(why)


def copied_instance(cls, case, prefix, root_attr="root", **kw):
    """An instance of fixture class `cls` — whose constructor is
    `cls(case, prefix, **kw)`, builds everything under a fresh directory
    it keeps as `.<root_attr>`, and registers that directory's removal —
    built ONCE per process per `kw` and copied for each caller.

    The constructor runs unmodified, once, into a scratch directory; that
    tree becomes the template. Each caller gets a fresh directory (named
    by `prefix`, removed at its cleanup), a copy of the tree with its git
    metadata rebased, and an instance whose attributes are the built
    one's with every path under the built root moved under its own — so
    what the fixture's own methods then do happens in the caller's copy
    and nowhere else. Only for a constructor whose output depends on `kw`
    alone (the clock aside): `prefix` names the directory, nothing more."""
    key = ("instance", cls.__module__, cls.__qualname__,
           tuple(sorted((k, repr(v)) for k, v in kw.items())))

    def build(troot):
        built_case = _BuildCase()
        try:
            obj = cls(built_case, "template-", **kw)
            built = Path(getattr(obj, root_attr))
            copy_tree(built, troot)
            rebase_paths(troot, built)
            # The build's own case is a stand-in: whatever attribute held
            # it holds the CALLER's case in each copy.
            attrs = {k: (_CALLER_CASE if v is built_case else v)
                     for k, v in vars(obj).items()}
            return _moved(attrs, built, Path(troot))
        finally:
            for fn, args, fkw in reversed(built_case.cleanups):
                fn(*args, **fkw)

    root = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    case.addCleanup(shutil.rmtree, root, True)
    troot, _ = fixture_tree(key, build)
    attrs = copy_fixture(key, build, root)
    obj = cls.__new__(cls)
    vars(obj).update({k: (case if v is _CALLER_CASE else v) for k, v in
                      _moved(attrs, Path(troot), root).items()})
    return obj


#: Where a template's attributes held the case that built it.
_CALLER_CASE = object()


def _moved(attrs, old, new):
    """`attrs` with each Path under `old`, and each string naming a path
    under it (a URL, say), moved under `new`."""
    spellings = sorted({str(old), os.path.realpath(old)}, key=len,
                       reverse=True)
    out = {}
    for k, v in attrs.items():
        if isinstance(v, Path):
            for sp in spellings:
                if str(v) == sp or str(v).startswith(sp + os.sep):
                    v = Path(str(new) + str(v)[len(sp):])
                    break
        elif isinstance(v, str):
            for sp in spellings:
                v = v.replace(sp, str(new))
        out[k] = v
    return out


def _path_bearing(top):
    """The git-metadata files under `top` that may name an absolute path:
    a `.git` file (a linked worktree's pointer), or one of `_PATH_BEARING`
    inside a git directory (`.git`, or a bare `*.git`), or a reflog under
    its `logs`. Of an object store
    only `objects/info` is walked; working-tree files are never touched."""
    top = Path(top)
    for dirpath, dirnames, filenames in os.walk(top):
        parts = Path(dirpath).relative_to(top).parts
        in_git = any(p == ".git" or p.endswith(".git") for p in parts)
        if in_git and parts[-1] == "objects":
            dirnames[:] = [d for d in dirnames if d == "info"]
        elif in_git:
            dirnames[:] = [d for d in dirnames if d != "hooks"]
        for name in filenames:
            if name == ".git" or (in_git and (name in _PATH_BEARING
                                              or "logs" in parts)):
                yield Path(dirpath) / name


def sh(*args):
    """Run one command to completion, raising on failure; output discarded."""
    subprocess.run(args, check=True, capture_output=True, text=True,
                   timeout=60)


def git_out(where, *args):
    """`git -C where args...`, returning stripped stdout."""
    out = subprocess.run(["git", "-C", str(where), *args], check=True,
                         capture_output=True, text=True, timeout=60)
    return out.stdout.strip()


def scratch_tmp(case, prefix):
    """A temporary directory the test case owns, or a skip where the
    filesystem refuses writes (the read-only pass)."""
    try:
        tmp = Path(tempfile.mkdtemp(prefix=prefix))
    except OSError as exc:
        case.skipTest(f"filesystem writes denied ({exc})")
    case.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
    return tmp


#: `warm_cache_fixture(tool_attr=...)` default: stamp the running identity.
CURRENT_IDENTITY = object()


@dataclasses.dataclass
class WarmCache:
    """What `warm_cache_fixture` built: a config whose state directory holds
    the kept request, the in-memory ledger that recorded it, the scripted
    runner, and the envelope text plus the path it was kept at."""
    cfg: object
    ledger: Ledger
    git: object
    text: str
    kept: str
    claim_digest: str

    def cached(self, **kw):
        """`cached_handoff` for round 1 under this scaffold; `kw` is the
        key dimension under test (roles, transport, debug, ...). The base
        is the one the scaffold's request names (`request_text`'s default),
        stated because an unstated base is cold (public issue #7); a test
        of the base itself passes its own."""
        kw.setdefault("base", SHA_A)
        return transport.cached_handoff(self.cfg, self.ledger, 1, LINEAGE,
                                        git=self.git,
                                        claim_digest=self.claim_digest, **kw)


def warm_cache_fixture(case, text=None, *, prefix="warm-cache-",
                       tool_attr=CURRENT_IDENTITY, transport_attr=None,
                       claim_digest=transport.NO_CLAIM):
    """The warm-cache scaffold every cache-key test starts from: one kept
    round-1 request, the ledger event that recorded it, and a runner that
    reports a clean tree at its SHA.

    Three facts the scaffold has to get right, or the test never reaches
    its own subject:
      * a recorded claim state, because round 5 F1 made "neither side says
        anything" cold — `claim_digest` is recorded on the event and is
        what `cached()` presents back;
      * the current tool identity on the envelope, because round 1 F2 put
        it in the warm key — the identity's own cold cases live in
        `test_transport_lifecycle`, and pass `tool_attr=None` to reach them;
      * the target's authority answered (round 2 F1: the warm path
        resolves the TARGET's `review.toml` to decide the role key).
    `text` overrides the envelope outright for a caller that stamps its
    own attributes.
    """
    tmp = scratch_tmp(case, prefix)
    cfg = dataclasses.replace(CFG, ledger_dir=tmp)
    if text is None:
        stamp = tool_identity() if tool_attr is CURRENT_IDENTITY else tool_attr
        text = request_text(tool_attr=stamp, transport_attr=transport_attr)
    kept = transport.keep_bytes(cfg, 1, "request", text, lineage=LINEAGE)
    git = fake_git({("rev-parse", "HEAD"): SHA_B,
                    ("status", "--porcelain"): "",
                    **authority_calls()})
    ledger = Ledger.in_memory()
    ledger.add({"event": "request", "round": 1, "sha": SHA_B,
                "source_digest": transport._digest_text(text),
                "bytes": len(text), "claim_digest": claim_digest})
    return WarmCache(cfg, ledger, git, text, kept, claim_digest)


def _build_scratch_loop(repo):
    """`scratch_loop_repo`'s repository, built at `repo`: returns its base."""
    sh("git", "init", "-q", "-b", "main", str(repo))
    write_identity(repo)
    toml = (REPO_ROOT / "review.toml").read_text(encoding="utf-8")
    toml = toml[:toml.index("[[gates]]")] + toml[toml.index("[roles]"):]
    # This repository declares `transport = "path"` (asked once, 2026-09-03);
    # the scratch loop declares its topology per test — by flag, config line
    # or environment — so the copy carries no transport of its own.
    toml = re.sub(r"^transport\s*=.*\n", "", toml, flags=re.M)
    (repo / "review.toml").write_text(toml, encoding="utf-8")
    (repo / "f.txt").write_text("one\n", encoding="utf-8")
    sh("git", "-C", str(repo), "add", ".")
    sh("git", "-C", str(repo), "commit", "-q", "-m", "init")
    base = git_out(repo, "rev-parse", "HEAD")
    (repo / "f.txt").write_text("two\n", encoding="utf-8")
    sh("git", "-C", str(repo), "commit", "-qam", "change")
    return base


def scratch_loop_repo(case, prefix, name="repo", objective="loop test",
                      tree=None):
    """A real repository the real CLI can run a round against: this repo's
    `review.toml` with its gates spliced out, `f.txt` committed twice so
    there is a base and a head, and a claim file naming `review.toml` as
    the required reference. The caller's cwd is restored at cleanup, since
    `run_cli` changes it. Returns tmp, repo, base, claim and cwd.

    The repository is built once per test process and copied into each
    caller's own directory (`copy_fixture`): every test still gets a
    repository of its own, with the same two commits under the same ids.

    `tree=(key, build)` copies a larger template into the test's directory
    instead — one whose `build(root)` put this repository at `root/name`
    (`copy_fixture(..., root / name)` of this one) and whatever else a
    module's fixture adds around it, returning facts with `base` among
    them. The facts come back as `.facts`."""
    tmp = scratch_tmp(case, prefix)
    repo = tmp / name
    if tree is None:
        base = copy_fixture("scratch_loop_repo", _build_scratch_loop, repo)
        facts = {"base": base}
    else:
        facts = copy_fixture(*tree, tmp)
        base = facts["base"]
    claim = tmp / "claim.json"
    claim.write_text(json.dumps({
        "objective": objective,
        "references": [{"path": "review.toml", "required": True}]}),
        encoding="utf-8")
    cwd = os.getcwd()
    case.addCleanup(os.chdir, cwd)
    return SimpleNamespace(tmp=tmp, repo=repo, base=base, claim=claim,
                           cwd=cwd, facts=facts)


def run_cli(where, state, *argv, cwd, env=None):
    """`loupe --ledger-dir state argv...` run from inside `where`, returning
    (exit code, parsed JSON payload — or the raw text when it is not JSON).

    The host running this suite may itself be a cloud sandbox carrying a
    transport declaration; the tests here state their own environment, so
    the host's is scrubbed and `env` (if any) is the whole declaration set.
    """
    os.chdir(where)
    buf = io.StringIO()
    scrubbed = {k: v for k, v in os.environ.items()
                if k != vocab.TRANSPORT_ENV
                and k not in {var for var, _v, _t
                              in vocab.TRANSPORT_PROVIDER_SIGNALS}}
    scrubbed.update(env or {})
    try:
        with mock.patch.dict(os.environ, scrubbed, clear=True):
            with contextlib.redirect_stdout(buf):
                code = cli.main(["--ledger-dir", str(state), *argv])
    finally:
        os.chdir(cwd)
    out = buf.getvalue()
    try:
        return code, json.loads(out)
    except json.JSONDecodeError:
        return code, out

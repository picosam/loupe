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
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from review import cli, config, tool_identity, transport, vocab
from review.ledger import Ledger
from review.tests import synth
from review.tests.util import REPO_ROOT

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
            f"Target: {sha}\nBase:   {base}   (the SHA ruled on in round 0)\n"
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
        key dimension under test (roles, transport, debug, ...)."""
        return transport.cached_handoff(self.cfg, self.ledger, 1,
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
    kept = transport.keep_bytes(cfg, 1, "request", text)
    git = fake_git({("rev-parse", "HEAD"): SHA_B,
                    ("status", "--porcelain"): "",
                    **authority_calls()})
    ledger = Ledger.in_memory()
    ledger.add({"event": "request", "round": 1, "sha": SHA_B,
                "source_digest": transport._digest_text(text),
                "bytes": len(text), "claim_digest": claim_digest})
    return WarmCache(cfg, ledger, git, text, kept, claim_digest)


def scratch_loop_repo(case, prefix, name="repo", objective="loop test"):
    """A real repository the real CLI can run a round against: this repo's
    `review.toml` with its gates spliced out, `f.txt` committed twice so
    there is a base and a head, and a claim file naming `review.toml` as
    the required reference. The caller's cwd is restored at cleanup, since
    `run_cli` changes it. Returns tmp, repo, base, claim and cwd."""
    tmp = scratch_tmp(case, prefix)
    repo = tmp / name
    sh("git", "init", "-q", "-b", "main", str(repo))
    for k, v in (("user.name", "a"), ("user.email", "a@example.invalid"),
                 ("commit.gpgsign", "false")):
        sh("git", "-C", str(repo), "config", k, v)
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
    claim = tmp / "claim.json"
    claim.write_text(json.dumps({
        "objective": objective,
        "references": [{"path": "review.toml", "required": True}]}),
        encoding="utf-8")
    cwd = os.getcwd()
    case.addCleanup(os.chdir, cwd)
    return SimpleNamespace(tmp=tmp, repo=repo, base=base, claim=claim,
                           cwd=cwd)


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

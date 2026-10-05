"""Two worktrees, one ledger, TWO reviews: the keyed lineage's falsification.

The [[worktree-shared-ledger]] probe, INVERTED. That probe measured what a
positional lineage did to two worktrees of one repository: both handoffs
computed lineage 1 round 1, neither was told about the other, `brief` in one
returned the other's round, and closing either SILENTLY DISCARDED the other's
open request — never ruled, never refused, gone from the record. Version
0.19.0 stopped the loss with a refusal (brief `concurrent-round-refusal`).
This module is what replaces that refusal: with the lineage KEYED, both
handoffs must SUCCEED, on distinct ids, and neither may be able to touch the
other (brief `keyed-lineage`).

Real repositories and real worktrees, through the real CLI — and, for the
overlap, two real processes: the collision is a property of two checkouts
sharing one state directory, which no in-memory fixture reproduces. The one
exception is stated where it is made and is about pausing, not about the
lock. Self-skips where filesystem writes are denied.

**Start states, built once and copied.** Most tests here begin AFTER one or
two handoffs — two reviews open, of two commits or of one, on one carrier or
another — and those handoffs are where most of this module's process
launches went, because every test ran its own. A test now names the state
it starts from (`starts_from`): the layered repository, then a recipe of
steps (`_STEPS`) — a handoff or any other verb through the same in-process
CLI entry a test body uses, or a plain git edit. Each state is built once
per test process and COPIED into every test that starts from it; no test
ever writes to the state itself. A step
records what it did and asserts nothing: the tests that start from a state
assert every exit and record they rely on, so a mutation that breaks a
setup handoff fails those tests by name. What still runs inside a test is
the verb under test — and every handoff whose interleaving IS the subject
(the overlaps, the reservation hooks, an amend, a re-emission).

THE DOMAIN THIS MODULE CLOSES
=============================

**Every event kind, and whether it is keyed.** Keyed — carrying the lineage
id of the review it belongs to: `request`, `verdict`, `take`, `evidence`,
`finding`, `closure`, `disposition`, `falsification_run`, `import`,
`ingest`, `cap_override`, `breaker_override`, `correction`,
`finding_waiver`, `advance`, `lineage_closed`. Deliberately UNKEYED, and
tested as such: `lineage` (fingerprint identity aliases, which describe
finding identity across the whole repository history) and `waiver` (a
decision that a COMMIT was not reviewed, which survives every lineage
boundary). Those two are read by `Ledger.lineage()` and `Ledger.waivers()`,
both of which read the whole file.

**The two seams the key enters by.** (1) THE EVENT FIELD: `Ledger.add`
stamps `lineage` on every event whose writer names one, and
`declared_lineage` is the one reader of it. (2) THE CLOSURE MARKER: a
`lineage_closed` event carries the id of the lineage it closes, so
`closures_of_lineage(id)` and `is_closed(id)` answer per review rather than
by counting markers in a file. Nothing else derives a lineage from position
except `Ledger.lineage_keys`, which derives the LEGACY key and only that.

**Every verb's lineage resolution rule.**

  `handoff`            the OPEN lineage whose requests were recorded on this
                       worktree's branch; a NEW minted id when this branch
                       holds none.
  `close --verdict`    the lineage the verdict's SHA resolves to, through
                       the request that recorded that commit.
  `close --lineage`    this branch's open lineage (it is given no envelope).
  `respond`            the lineage the answered verdict's SHA resolves to.
  `brief <envelope>`   the lineage that envelope's SHA resolves to.
  `brief` (no arg)     this branch's open lineage, and it NAMES every other
                       open lineage — id, branch, round, state.
  `ledger add`         the envelope's SHA, else this branch's open lineage;
                       a request whose commit nothing has recorded may mint.
  `ledger report`      this branch's lineage, plus the repository aggregate.
  `authorize-advance`  this branch's open lineage.
  `waive --finding`    this branch's open lineage (`waive <sha>` is global).
  `take`               the id the `git:<id>/<round>` reference carried, else
                       the id THE REQUEST ENVELOPE STAMPS (round-2 F5), else
                       the lineage this ledger already recorded for the SHA,
                       else the open lineage whose recorded AUTHOR BRANCH is
                       this envelope's, else a new id — and a refusal where
                       more than one association is still possible. The
                       reviewer's own branch is never consulted: a request
                       event's `branch` is the AUTHOR's, and step 4 compares
                       it with the author branch this ledger recorded.
  `validate --from-target`  the round's carried lineage, for the ref it
                       pushes the verdict to.

  Every SHA-resolving verb above keeps the review the INVOCATION carries
  (round-2 F1). A commit identifies code, not the review that owns its
  findings, so where two reviews bind one SHA the branch — or the id an
  explicit reference names — decides, and where neither does the verb
  refuses without writing. One lineage binding a SHA resolves as it always
  did, whatever is carried.

  TWO states where a lineage cannot be chosen, and both refuse rather than
  pick: a detached HEAD (or a branch git will not name) with SEVERAL
  lineages open, except where an explicit reference resolves it; and an
  envelope whose SHA is bound by rounds in several lineages, reached from a
  checkout that names none of them.

**When the lifecycle is read.** Every reservation-taking verb — `handoff`,
`close` and `authorize-advance` — takes its authoritative lifecycle
snapshot UNDER the reservation, by re-reading the ledger from disk after
`acquire()` and revalidating the lineage it selected before it (round-2
F2). `Ledger.events` caches its first read, and a lock cannot make an
earlier read current.

**How many times a source is read.** Once (round-2 F3). Each verb captures
the bytes its `--verdict`/envelope argument names exactly once and uses
that capture for both the lineage resolution and the processing, so
standard input is not consumed by a preliminary read and a mutable file or
a moving ref cannot be seen differently by the two.

**The three states of the field.** PRESENT: a string id — a minted
`L<10 hex>` or a legacy ordinal materialised by `migrate-state`. ABSENT: the
legacy positional lineage, the ordinal counted by `lineage_closed` markers
in the prefix BEFORE the first keyed event, as a decimal string. MIXED: a
legacy prefix followed by keyed events, where the prefix keeps its ordinals
and the counter freezes at the first keyed event. Absence is never a
collision and never a default of "the current lineage".

**The provenance a verb carries, and its precedence** (round-3 F1, F3).
A `git:<lineage>/<round>` reference names the review outright; the
`lineage="<id>"` stamp on request bytes names it next; the branch the verb
runs from is consulted only when neither is present. `brief`, `ledger add`,
`respond`, `close`, `validate` and `take` all read the same precedence, so
handing review B's request to a verb run from A's checkout acts on B, and a
stamp naming no review here refuses without writing. On the reviewer's side
a request is its BYTES: two independently emitted reviews of one commit are
two takes in two lineages, and only the same bytes carried under a second
lineage are refused as a retake.

**Both carriers.** `path`: the kept copies live at
`exchange/lineage-<id>/round-<n>-<kind>-<digest>.md`, so two lineages'
round 1 requests are two files. `git`: the envelope rides
`refs/loupe/<id>/<round>/<leg>`, force-pushed deliberately so a re-emission
replaces its own bytes — which is safe exactly because the id is unique.
Both are exercised here, and the `git` one is where the pre-keying loss was
unrecoverable: two worktrees computing one ref meant the second overwrote
the first.

**The two reservations, and what each excludes.** `lineage-<id>.lock` is
held across a whole admission by every verb that acts on a lineage that
EXISTS, so two commands touching one review — its ref, its round number, its
terminal marker — exclude each other. `lineage-new-<branch>-<digest>.lock`
stands for the lineage a branch has not opened yet, because an id that does
not exist cannot be locked by id; `close` takes it too when this branch
names no lineage, so a close arriving mid-admission is told the reservation
is held rather than told the branch has no review. Neither lock is
repository-wide: one that was would be held across the gates and would
refuse the second worktree for the whole interval, which is the refusal
keying exists to retire.

THE MUTATION
============

Derive the id from position again — make `Ledger.choose_open_lineage`
ignore the branch and return the single open lineage — and the second
worktree's handoff collides into the first's review, and the close then
swallows the first's request unruled. That is the pre-keying state, and
`TestTwoWorktreesOpenTwoLineages` is what detects it.

One mutation per round-2 finding, each named on the class it falsifies:
restore the newest whole-ledger SHA match (`TestTwoReviewsOfOneCommit`);
make `Ledger.reload` a no-op, so the snapshot taken before the reservation
is the one used (`TestTheSnapshotIsTakenUnderTheReservation`); and restore
adoption by the sole open lineage in `take_lineage`
(`TestPathAndPasteIngressKeepReviewsApart`). The fourth, restoring the
second source read, is falsified in `test_transport_topology` and
`test_worktree_and_brief`, where the stdin carriers live.
"""

import atexit
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from review import env_var, transport, vocab, wire
from review.ledger import AmbiguousLineage, Ledger, render_cross_lineage_md
from review.tests._transport_fixtures import (
    CFG, _build_scratch_loop, copy_fixture, copy_tree, fixture_tree, git_out,
    run_cli, scratch_loop_repo, scratch_tmp, settle_maintenance, sh,
    verdict_text)
from review.tests.util import REPO_ROOT
from review.validate import validate_request

#: A gate the test controls, declared in the throwaway repository's own
#: review.toml with absolute paths. It is what holds the admission interval
#: open long enough for a second worktree to run inside it — which is the
#: whole of F1: the branch check is a single read, and the gates are minutes
#: of destruction after it. Three markers, all under one directory:
#:
#:   <name>.hold     while it exists, the gate blocks (the pause)
#:   <name>.started  written by the gate, so a test can wait for the pause
#:   <name>.fail     the gate exits 1 (a failed handoff, for the cleanup case)
#:
#: `<name>` is the worktree's directory name, so the two worktrees are
#: controlled independently from one committed manifest. The iteration bound
#: is a backstop: a wedged test fails in about a minute instead of sitting
#: until the runner's own 600s gate timeout.
_GATE_SCRIPT = (
    'd="{gate_dir}"; n=$(basename "$PWD"); mkdir -p "$d"; '
    ': > "$d/$n.started"; '
    'i=0; while [ -e "$d/$n.hold" ] && [ $i -lt 1200 ]; do sleep 0.05; '
    'i=$((i+1)); done; '
    'if [ -e "$d/$n.fail" ]; then exit 1; fi; exit 0'
)
_GATE_BLOCK = """

[[gates]]
id = "pause"
command = ['/bin/sh', '-c', '{script}']
blocking = true
"""


def cli_process_env() -> dict:
    """The environment a SEPARATE CLI process runs in.

    Three deliberate edits to this process's own environment:

      * the transport declarations are scrubbed, exactly as `run_cli` does —
        the host running this suite may itself be a cloud sandbox that
        declares one, and these tests state their own topology;
      * `PYTHONPATH`/`PYTHONSAFEPATH` reproduce what `bin/loupe` sets, so
        `python3 -m review` loads THIS checkout's package and not a `review`
        directory that happens to sit in the scratch worktree;
      * the gate-run re-entrancy guard is cleared. The suite may itself be
        running inside a gate, where `run_gates` refuses to run gates at all
        — and the pause IS the subject here. Nothing recurses: the gate
        below is `/bin/sh` waiting on a file and never invokes the tool.
    """
    env = {k: v for k, v in os.environ.items()
           if k != vocab.TRANSPORT_ENV
           and k not in {var for var, _v, _t
                         in vocab.TRANSPORT_PROVIDER_SIGNALS}}
    env.pop(env_var("IN_GATE_RUN"), None)
    env.pop(env_var("STATE_DIR"), None)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONSAFEPATH"] = "1"
    return env


def wait_for(path: Path, what: str, timeout: float = 60.0) -> None:
    """Block until `path` appears, or fail the run saying what was awaited."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out after {timeout}s waiting for {what} "
                         f"({path})")


def _layer_other(root, facts):
    """The second branch checkout, one commit ahead of main."""
    other = root / "other"
    sh("git", "-C", str(root / "repo"), "worktree", "add", "-q", "-b",
       "other", str(other))
    (other / "f.txt").write_text("three\n", encoding="utf-8")
    sh("git", "-C", str(other), "commit", "-qam", "the other branch")


def _layer_origin(root, facts):
    """A bare `origin` with main pushed: the `git` carrier's remote."""
    bare = root / "origin.git"
    sh("git", "init", "-q", "--bare", "-b", "main", str(bare))
    sh("git", "-C", str(root / "repo"), "remote", "add", "origin", str(bare))
    sh("git", "-C", str(root / "repo"), "push", "-q", "-u", "origin", "main")


def _layer_push_other(root, facts):
    """The second branch pushed too: the reviewer can fetch both targets."""
    sh("git", "-C", str(root / "other"), "push", "-q", "-u", "origin",
       "other")


def _layer_same(root, facts):
    """A second branch AT main's head: one commit under two checkouts."""
    repo = root / "repo"
    sh("git", "-C", str(repo), "worktree", "add", "-q", "-b", "same",
       str(root / "same"), "HEAD")
    facts["shared"] = git_out(repo, "rev-parse", "HEAD")


_LAYERS = {"other": _layer_other, "origin": _layer_origin,
           "push-other": _layer_push_other, "same": _layer_same}


def _worktree_tree(layers):
    """The template (key, build) for `_TwoWorktrees` with `layers` applied
    over the scratch repository, in order — built once per process and
    copied per test by `scratch_loop_repo(tree=...)`."""
    def build(root):
        facts = {"base": copy_fixture("scratch_loop_repo",
                                      _build_scratch_loop, root / "repo")}
        (root / "gates").mkdir()
        for name in layers:
            _LAYERS[name](root, facts)
        return facts
    return ("concurrent-worktrees", layers), build


# ----------------------------------------------------------- start states
#
# ONE FIXED PATH PER PROCESS. A handoff writes absolute paths into what it
# keeps: the request's Diff, Push and Verify lines name the checkout and the
# bare origin, its ledger line names the state directory, and `take` fetches
# from the Push line's URL. Rebasing those would change kept bytes under the
# digest that names them, and leaving them would point every copy at the
# state it was copied from. So every start state is BUILT at `_live()`,
# moved aside as a template, and each test's private copy is put back at
# that same path. unittest runs one test at a time in a process; each test
# removes its copy at cleanup, and the next copy replaces whatever is left.

#: Process-wide memo: (pausing, layers, steps) -> (template dir, facts).
_STARTS: dict = {}
_STARTS_PARENT: list = []


def _starts_parent(case) -> Path:
    """One directory per test process for the templates and the live copy,
    removed when the process exits — or a skip where writes are denied."""
    if not _STARTS_PARENT:
        try:
            parent = Path(tempfile.mkdtemp(prefix="concurrent-starts-"))
        except OSError as exc:
            case.skipTest(f"filesystem writes denied ({exc})")
        atexit.register(shutil.rmtree, parent, ignore_errors=True)
        _STARTS_PARENT.append(parent)
    return _STARTS_PARENT[0]


def _live(case) -> Path:
    """The one path every start state is built at and every copy lives at."""
    return _starts_parent(case) / "live"


def _declare_pausing_gate(repo: Path, gates: Path) -> None:
    """Append the gate to review.toml and COMMIT it — the manifest that runs
    is the target commit's, never this checkout's working copy."""
    toml = repo / "review.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8")
        + _GATE_BLOCK.format(script=_GATE_SCRIPT.format(gate_dir=gates)),
        encoding="utf-8")
    sh("git", "-C", str(repo), "commit", "-qam", "the controllable gate")


def _base_tree(live: Path, layers, pausing: bool) -> dict:
    """The layered repository at `live`, plus the claim every handoff files.

    Without a pausing gate it is `_worktree_tree(layers)`, copied and
    rebased. With one, the gate is committed BEFORE the layers run, so the
    second worktree branches from a commit that carries it; its script names
    `live/gates`, which is every copy's gate directory."""
    if pausing:
        facts = {"base": copy_fixture("scratch_loop_repo",
                                      _build_scratch_loop, live / "repo")}
        (live / "gates").mkdir()
        _declare_pausing_gate(live / "repo", live / "gates")
        for name in layers:
            _LAYERS[name](live, facts)
    else:
        facts = dict(copy_fixture(*_worktree_tree(layers), live))
    (live / "claim.json").write_text(json.dumps({
        "objective": "collision",
        "references": [{"path": "review.toml", "required": True}]}),
        encoding="utf-8")
    return facts


def _cli_at(live: Path, where: str, *argv, scrub_gate_run=False):
    """One in-process CLI call from checkout `where` on the start's ledger.

    `scrub_gate_run` is `_TwoWorktrees._handoff`'s scrub, for the same
    reason: the suite may itself run inside a gate, and a handoff inheriting
    the marker would have its gates marked "not run" and be refused."""
    with mock.patch.dict(os.environ):
        if scrub_gate_run:
            os.environ.pop(env_var("IN_GATE_RUN"), None)
        return run_cli(live / where, live / "state", *argv, cwd=os.getcwd())


def _step_handoff(live, facts, name, where, *extra):
    """`handoff` from `where`; its (exit, record) is `facts[name]`."""
    facts[name] = _cli_at(live, where, "handoff", "--claim-file",
                          str(live / "claim.json"), "--base", facts["base"],
                          *extra, scrub_gate_run=True)


def _step_cli(live, facts, name, where, *argv):
    """Any other verb from `where`; its (exit, payload) is `facts[name]`."""
    facts[name] = _cli_at(live, where, *argv)


def _step_layer(live, facts, name):
    """A `_LAYERS` entry applied after handoffs, e.g. a bare origin."""
    _LAYERS[name](live, facts)


def _step_commit(live, facts, where, text, message, push=None):
    """Rewrite `f.txt` in `where` and commit it, pushing `push` if named."""
    (live / where / "f.txt").write_text(text, encoding="utf-8")
    sh("git", "-C", str(live / where), "commit", "-qam", message)
    if push:
        sh("git", "-C", str(live / where), "push", "-q", "origin", push)


def _step_cap_of_one(live, facts):
    """Commit `round_cap = 1` into the manifest the reviews are judged by,
    so round 2 is past the cap and the notice is observable.

    The commit moves `main`; the fixture's whole point is ONE commit under
    two reviews, so the second branch follows it and the shared SHA is
    re-read rather than remembered."""
    repo = live / "repo"
    toml = repo / "review.toml"
    text = toml.read_text(encoding="utf-8")
    if re.search(r"^round_cap\s*=.*$", text, re.M):
        text = re.sub(r"^round_cap\s*=.*$", "round_cap = 1", text,
                      count=1, flags=re.M)
    elif "[limits]" in text:
        text = text.replace("[limits]", "[limits]\nround_cap = 1", 1)
    else:
        text += "\n[limits]\nround_cap = 1\n"
    toml.write_text(text, encoding="utf-8")
    sh("git", "-C", str(repo), "commit", "-qam", "a cap of one")
    sh("git", "-C", str(live / "same"), "reset", "-q", "--hard", "main")
    facts["shared"] = git_out(repo, "rev-parse", "HEAD")
    facts["base"] = git_out(repo, "rev-parse", "HEAD~1")


def _step_answer(live, facts, where, name):
    """A verdict recorded and answered in the ONE review `facts[name]`
    opened, so that review may open a round 2: `facts[name + ":close"]` and
    `facts[name + ":respond"]` are the two (exit, record) pairs."""
    record = facts[name][1]
    lineage = record.get("lineage") if isinstance(record, dict) else None
    verdict = live / f"v-{lineage}.md"
    verdict.write_text(verdict_text(sha=facts["shared"]), encoding="utf-8")
    facts[f"{name}:close"] = _cli_at(live, where, "close", "--verdict",
                                     str(verdict))
    dispositions = live / f"d-{lineage}.json"
    dispositions.write_text(json.dumps({
        "head": facts["shared"],
        "dispositions": [{
            "finding_id": "F1", "disposition": "accepted",
            "payload": {"change": "fixed", "verification": "the test",
                        "falsification": {"status": "pass",
                                          "mutation": "fails_without_fix"}}}]}),
        encoding="utf-8")
    facts[f"{name}:respond"] = _cli_at(
        live, where, "respond", "--verdict", str(verdict), "--from-json",
        str(dispositions), "--out", str(live / f"disposition-{lineage}.md"))


def _step_round_two(live, facts):
    """One new commit that both `same` and `main` point at."""
    _step_commit(live, facts, "same", "round two\n", "round two")
    sh("git", "-C", str(live / "repo"), "merge", "-q", "--ff-only", "same")
    facts["shared"] = git_out(live / "repo", "rev-parse", "HEAD")


_STEPS = {"handoff": _step_handoff, "cli": _step_cli, "layer": _step_layer,
          "commit": _step_commit, "cap-of-one": _step_cap_of_one,
          "answer": _step_answer, "round-two": _step_round_two}


def _start(case, pausing: bool, layers, steps):
    """The template for this start state, built at `_live()` the first time
    this process asks for it — from the template of its own prefix, so
    states that share leading steps share their build — and returned as
    (template dir, facts). The template is never handed to a test.

    The build is settled (`settle_maintenance`) BEFORE it is renamed. A
    step's last git command (`git push` into `origin.git`, a commit) may
    leave git's detached maintenance holding `<objects>/maintenance.lock`,
    and git records that lock by its ABSOLUTE path and unlinks that path
    when done. Renamed under it, the daemon unlinks a path that no longer
    exists and the template keeps the lock for good, so every later copy of
    it waits `MAINTENANCE_SETTLE_S` and refuses — nightly 37287818489 at
    80b47a10 (`start-14`, a pushed commit), two tests red under the
    config-perturbation gate on a two-core runner."""
    key = (bool(pausing), tuple(layers), tuple(steps))
    if key not in _STARTS:
        prefix = _start(case, pausing, layers, steps[:-1]) if steps else None
        live = _live(case)
        shutil.rmtree(live, ignore_errors=True)
        live.mkdir()
        if prefix is None:
            facts = _base_tree(live, layers, pausing)
        else:
            copy_tree(prefix[0], live)
            facts = copy.deepcopy(prefix[1])
            name, *args = steps[-1]
            _STEPS[name](live, facts, *args)
        template = _starts_parent(case) / f"start-{len(_STARTS)}"
        settle_maintenance(live)
        live.rename(template)
        _STARTS[key] = (template, facts)
    return _STARTS[key]


def starts_from(steps):
    """Mark one test with the start state it begins from, overriding its
    class's `START`. `None` is no repository at all: a state directory."""
    def mark(test):
        test.start = steps
        return test
    return mark


LOCAL = ("--local-only",)
GIT = ("--transport", "git")
PASTE = ("--transport", "paste")
#: The author's declaration that a second review of one commit is meant
#: (RR1, 2026-10-04): without it the second hand-off refuses before its gates.
SHARE = ("--shared-target",)

#: One review, opened from `main`.
MAIN_OPENED = (("handoff", "first", "repo", *LOCAL),)
#: ...and a second from the other branch, at its own commit.
TWO_BRANCHES = MAIN_OPENED + (("handoff", "second", "other", *LOCAL),)
#: ...or a second from `same`, at main's commit: one commit, two reviews.
ONE_COMMIT = MAIN_OPENED + (("handoff", "second", "same", *LOCAL, *SHARE),)
#: The same two reviews of one commit, `same` arriving first.
ONE_COMMIT_REVERSED = (("handoff", "first", "same", *LOCAL),
                       ("handoff", "second", "repo", *LOCAL, *SHARE))
#: Two reviews of one commit on the `git` carrier, and on `paste`: a bare
#: remote first, because a declared cross-machine round may not bind an
#: unfetchable SHA.
ONE_COMMIT_GIT = (("layer", "origin"), ("handoff", "first", "repo", *GIT),
                  ("handoff", "second", "same", *GIT, *SHARE))
ONE_COMMIT_PASTE = (("layer", "origin"),
                    ("handoff", "first", "repo", *PASTE),
                    ("handoff", "second", "same", *PASTE, *SHARE))
#: Two branches' reviews on the `git` carrier (layers other, origin).
TWO_BRANCHES_GIT = (("handoff", "first", "repo", *GIT),
                    ("handoff", "second", "other", *GIT))
#: Round 1 answered in BOTH reviews of one commit under a committed cap of
#: one, one new commit both branches point at, a round-2 request emitted in
#: each, and the cap raised for `same`'s review only.
ROUND_TWO_CAPPED = (
    (("cap-of-one",),) + ONE_COMMIT
    + (("answer", "repo", "first"), ("answer", "same", "second"),
       ("round-two",),
       ("handoff", "b_two", "same", *LOCAL),
       ("handoff", "a_two", "repo", *LOCAL, *SHARE),
       ("cli", "raised", "same", "ledger", "authorize-cap", "--to", "2",
        "--reason", "the falsification's own scenario", "--by", "a person")))
#: Two branches' reviews on the default carrier, both branches pushed
#: (layers other, origin, push-other) — and the same on `paste` and `git`.
INGRESS = (("handoff", "first", "repo"), ("handoff", "second", "other"))
INGRESS_PASTE = (("handoff", "first", "repo", *PASTE),
                 ("handoff", "second", "other", *PASTE))
INGRESS_GIT = (("handoff", "first", "repo", *GIT),
               ("handoff", "second", "other", *GIT))
#: ...then a second emission of `main`'s review at a new, pushed tip.
INGRESS_AGAIN = INGRESS + (("commit", "repo", "four\n", "more", "main"),
                           ("handoff", "again", "repo"))
#: ...or `main`'s review closed on the AUTHOR's side and a second review
#: opened from `main`, two rounds of it at two new pushed tips (`third`,
#: `fourth`). A reviewer who takes `first` and `third` and never learns of
#: the close holds two open reviews recorded on one author branch.
INGRESS_REOPENED = INGRESS + (
    ("cli", "closed", "repo", "close", "--lineage", "--reason",
     "superseded by a new review of main", "--by", "a person"),
    ("commit", "repo", "four\n", "more", "main"),
    ("handoff", "third", "repo"),
    ("commit", "repo", "five\n", "again", "main"),
    ("handoff", "fourth", "repo"))


class _TwoWorktrees(unittest.TestCase):
    """One repository, two branch checkouts, ONE ledger.

    The state directory is shared deliberately and is what a real pair of
    worktrees gets for free: `repo_identity` canonicalises a linked worktree
    back to the main checkout, so both resolve to the same id and the same
    ledger file. `--ledger-dir` makes that literal here rather than relying
    on the machine's real state directory.

    Each test gets a private copy of its START STATE (see the module
    docstring and `_start`): the layers, then the steps it names.
    """

    PREFIX = "concurrent-"
    #: Declare the controllable gate in the committed manifest. Off by
    #: default: the sequential classes want no gate at all, and the gate is
    #: committed BEFORE the second worktree branches so both carry it. Its
    #: script names `<live>/gates`, which is every copy's gate directory, so
    #: a pausing tree is a start state like any other.
    PAUSING_GATE = False
    #: What the fixture builds over the scratch repository, in order (see
    #: `_LAYERS`): the second worktree always, then what a class adds —
    #: a bare origin, the second branch pushed, a third checkout at main.
    LAYERS = ("other",)
    #: The steps a test starts from after the layers (`_STEPS`); a test
    #: overrides it with `starts_from`. `None`: no repository, a state
    #: directory only — for the tests that read and write a ledger directly.
    START = ()

    def setUp(self):
        start = getattr(getattr(self, self._testMethodName), "start",
                        self.START)
        if start is None:
            self.tmp = scratch_tmp(self, self.PREFIX)
            self.state = self.tmp / "state"
            return
        template, facts = _start(self, self.PAUSING_GATE, self.LAYERS, start)
        live = _live(self)
        shutil.rmtree(live, ignore_errors=True)
        copy_tree(template, live)
        self.addCleanup(lambda: shutil.rmtree(live, ignore_errors=True))
        self.cwd = os.getcwd()
        self.addCleanup(os.chdir, self.cwd)
        #: This test's own copy of what the start recorded.
        self.facts = copy.deepcopy(facts)
        self.tmp, self.repo = live, live / "repo"
        self.base, self.claim = self.facts["base"], live / "claim.json"
        self.state = live / "state"
        self.gates = live / "gates"
        self.other = live / "other"
        if "same" in self.LAYERS:
            self.same = live / "same"
            self.shared = self.facts["shared"]

    def _started(self, *names):
        """The records of the start's handoffs (or other steps) `names`,
        each asserted to have exited 0 — the assertion every test made on
        the handoffs it used to run itself."""
        records = []
        for name in names:
            code, record = self.facts[name]
            self.assertEqual(code, 0, record)
            records.append(record)
        return records[0] if len(records) == 1 else records

    # Both checkouts, one state directory: that is the whole fixture.
    def _in_main(self, *argv):
        return run_cli(self.repo, self.state, *argv, cwd=self.cwd)

    def _in_other(self, *argv):
        return run_cli(self.other, self.state, *argv, cwd=self.cwd)

    def _handoff(self, where, *extra):
        # An IN-PROCESS handoff inherits this process's environment, and
        # the suite may itself be a gate: the runner's re-entrancy guard
        # then marks the fixture's pausing gate "not run" and refuses the
        # emission before the test body starts (measured twice, lineage 27
        # round 3 and lineage 28 round 1, both in the handoff's own `tests`
        # gate). `cli_process_env` scrubs it for the subprocesses; this is
        # the same scrub for the in-process calls. Nothing recurses: the
        # pausing gate is `/bin/sh` waiting on a file.
        with mock.patch.dict(os.environ):
            os.environ.pop(env_var("IN_GATE_RUN"), None)
            return where("handoff", "--claim-file", str(self.claim),
                         "--base", self.base, *extra)

    # ---------------------------------------------- separate CLI processes
    #
    # The overlap is a property of two PROCESSES, not two calls: what F1
    # found is that both pass the branch check because neither has recorded
    # anything yet, and a same-process call cannot be inside another's gate
    # run. So these spawn the tool the way `bin/loupe` does.

    def _spawn(self, where, *argv):
        proc = subprocess.Popen(
            [sys.executable, "-m", "review", "--ledger-dir", str(self.state),
             *argv],
            cwd=str(where), env=cli_process_env(), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # A test that fails mid-overlap must not leave a handoff running
        # into the teardown that removes the repository under it.
        self.addCleanup(self._reap, proc)
        return proc

    @staticmethod
    def _reap(proc):
        if proc.poll() is None:
            proc.kill()
            try:
                proc.communicate(timeout=30)
            except (ValueError, OSError, subprocess.SubprocessError):
                pass

    def _finish(self, proc, timeout=120):
        out, err = proc.communicate(timeout=timeout)
        try:
            return proc.returncode, json.loads(out)
        except json.JSONDecodeError:
            return proc.returncode, {"stdout": out, "stderr": err}

    def _run(self, where, *argv, timeout=120):
        return self._finish(self._spawn(where, *argv), timeout=timeout)

    def _spawn_handoff(self, where, *extra):
        return self._spawn(where, "handoff", "--claim-file", str(self.claim),
                           "--base", self.base, *extra)

    def _run_handoff(self, where, *extra, timeout=120):
        return self._finish(self._spawn_handoff(where, *extra),
                            timeout=timeout)

    # The gate's three markers, by worktree directory name.
    def _marker(self, where, suffix):
        return self.gates / f"{Path(where).name}.{suffix}"

    def _hold(self, where):
        self._marker(where, "hold").write_text("", encoding="utf-8")

    def _release(self, where):
        self._marker(where, "hold").unlink(missing_ok=True)

    def _fail(self, where):
        self._marker(where, "fail").write_text("", encoding="utf-8")

    def _await_gate(self, where):
        wait_for(self._marker(where, "started"),
                 f"the gate to start in {Path(where).name}")

    def _events(self, kind=None):
        path = self.state / "ledger.jsonl"
        if not path.is_file():
            return []
        events = [json.loads(line)
                  for line in path.read_text(encoding="utf-8").splitlines()
                  if line.strip()]
        return [e for e in events if kind is None or e.get("event") == kind]

    def _verdict_file(self, sha, name="v.md", verdict="changes requested"):
        path = self.tmp / name
        path.write_text(verdict_text(sha=sha, verdict=verdict),
                        encoding="utf-8")
        return str(path)




class TestTwoWorktreesOpenTwoLineages(_TwoWorktrees):
    """The inverted probe: both handoffs SUCCEED, on distinct lineages.

    Each of the three destructions the original probe measured has its
    control here — two rounds where the ledger held one, a close that
    swallowed the other's request, and a `brief` that returned the other
    worktree's round. All three are now the ordinary, correct outcome of two
    reviews running beside each other.

    Every test starts from both handoffs made (`TWO_BRANCHES`), except the
    two that are about one review.
    """

    PREFIX = "keyed-emit-"
    START = TWO_BRANCHES

    def test_both_handoffs_succeed_with_distinct_lineage_ids(self):
        first, second = self._started("first", "second")
        # Two reviews, each at its own round 1 — and two IDS, not one
        # position in a file.
        self.assertEqual(first["round"], 1)
        self.assertEqual(second["round"], 1)
        self.assertNotEqual(first["lineage"], second["lineage"])
        # Minted ids are letter-led, so neither can be read as an ordinal.
        for rec in (first, second):
            self.assertTrue(rec["lineage"].startswith("L"), rec["lineage"])
        # Both requests are recorded, each stamped with its own lineage and
        # its own branch.
        requests = self._events("request")
        self.assertEqual([e["branch"] for e in requests], ["main", "other"])
        self.assertEqual([e["lineage"] for e in requests],
                         [first["lineage"], second["lineage"]])

    def test_closing_one_leaves_the_other_open_and_readable(self):
        """Destruction 2, inverted. A clean verdict closes ITS lineage; the
        other worktree's request is untouched, unruled by nobody, and still
        the round its own `brief` reports."""
        first, second = self._started("first", "second")
        code, closed = self._in_main(
            "close", "--verdict",
            self._verdict_file(first["sha"], name="clean.md",
                               verdict="clean to advance"))
        self.assertEqual(code, 0, closed)
        closures = self._events(Ledger.LINEAGE_CLOSED)
        self.assertEqual(len(closures), 1, closures)
        # The closure names the lineage it closed, and only that one.
        self.assertEqual(closures[0]["lineage"], first["lineage"])
        # The other review is open and reads exactly as it did.
        code, briefed = self._in_other("brief")
        self.assertEqual(code, 0, briefed)
        self.assertEqual(briefed["sha"], second["sha"])
        self.assertEqual(briefed["lineage"], second["lineage"])
        # And it can still be ruled: the swallowed round of the original
        # probe could never carry a verdict.
        code, ruled = self._in_other(
            "close", "--verdict",
            self._verdict_file(second["sha"], name="v2.md"))
        self.assertEqual(code, 0, ruled)
        self.assertEqual(ruled["round"], 1)

    def test_brief_in_each_worktree_finds_its_own_and_names_the_other(self):
        """Destruction 3, inverted: `brief` in worktree A returned B's round.

        It now returns A's, and NAMES B's — id, branch, round and state — so
        a reader is not left believing theirs is the only review live.
        """
        first, second = self._started("first", "second")
        for where, mine, theirs, branch in (
                (self._in_main, first, second, "other"),
                (self._in_other, second, first, "main")):
            code, briefed = where("brief")
            self.assertEqual(code, 0, briefed)
            self.assertEqual(briefed["sha"], mine["sha"])
            self.assertEqual(briefed["lineage"], mine["lineage"])
            named = {o["lineage"]: o for o in briefed["open_lineages"]}
            self.assertEqual(list(named), [theirs["lineage"]])
            other = named[theirs["lineage"]]
            self.assertEqual(other["branch"], branch)
            self.assertEqual(other["round"], 1)
            self.assertEqual(other["state"], "awaiting a verdict")

    @starts_from(MAIN_OPENED)
    def test_one_open_lineage_names_no_other(self):
        """The paired control: with one review live, nothing is named."""
        self._started("first")
        code, briefed = self._in_main("brief")
        self.assertEqual(code, 0, briefed)
        self.assertEqual(briefed["open_lineages"], [])

    @starts_from(MAIN_OPENED)
    def test_amend_and_re_emit_still_supersedes_within_one_lineage(self):
        """The flow that makes SHA useless as an axis, unchanged: it stays
        in ITS lineage rather than opening a second one."""
        first = self._started("first")
        sh("git", "-C", str(self.repo), "commit", "--amend", "-q", "-m",
           "change, amended")
        amended = git_out(self.repo, "rev-parse", "HEAD")
        self.assertNotEqual(amended, first["sha"])
        code, again = self._handoff(self._in_main, "--local-only")
        self.assertEqual(code, 0, again)
        self.assertEqual(again["sha"], amended)
        self.assertEqual(again["round"], 1)
        self.assertEqual(again["lineage"], first["lineage"],
                         "amend-and-re-emit opened a second lineage")
        code, briefed = self._in_main("brief")
        self.assertEqual(code, 0, briefed)
        self.assertEqual(briefed["superseded"], 1)
        self.assertEqual(briefed["sha"], amended)

    def test_the_report_aggregate_lists_both_open_lineages(self):
        """The repository aggregate ruled 2026-09-06: a FACT, no breaker."""
        first, second = self._started("first", "second")
        report = json.loads(
            subprocess.run(
                [sys.executable, "-m", "review", "--ledger-dir",
                 str(self.state), "ledger", "report", "--format", "json"],
                cwd=str(self.repo), env=cli_process_env(), text=True,
                capture_output=True, check=True).stdout)
        rows = {r["lineage"]: r for r in report["open_lineages"]}
        self.assertEqual(sorted(rows),
                         sorted([first["lineage"], second["lineage"]]))
        self.assertEqual(rows[first["lineage"]]["branch"], "main")
        self.assertEqual(rows[second["lineage"]]["branch"], "other")
        self.assertEqual(rows[first["lineage"]]["rounds"], [1])
        # This report is about ONE of them, by id, and says which.
        self.assertEqual(report["lineage"]["id"], first["lineage"])
        # No breaker was invented for the aggregate.
        self.assertEqual(report["breakers_fired"], [])


class TestTheGitCarrierKeepsBothEnvelopes(_TwoWorktrees):
    """The carrier where the pre-keying loss was unrecoverable.

    `carrier_ref` keys the envelope ref on (lineage, round, leg) and
    `push_envelope` force-pushes to it — deliberately, since a re-emitted
    round replaces its own bytes. Two worktrees computing the SAME lineage
    and round therefore pushed to ONE ref and the second silently overwrote
    the first. With the lineage keyed the two refs are two names, and both
    envelopes survive.
    """

    PREFIX = "keyed-git-"
    LAYERS = ("other", "origin")
    START = TWO_BRANCHES_GIT

    def _envelope_refs(self):
        return git_out(self.repo, "ls-remote", "origin", "refs/loupe/*")

    def test_both_envelopes_survive_on_distinct_refs(self):
        first, second = self._started("first", "second")
        self.assertNotEqual(first["carrier"]["ref"], second["carrier"]["ref"])
        for rec in (first, second):
            self.assertEqual(
                rec["carrier"]["ref"],
                f"refs/loupe/{rec['lineage']}/1/request")
            self.assertIn(rec["carrier"]["ref"], self._envelope_refs())
        cfg = type("C", (), {"repo_root": self.repo})()
        for rec in (first, second):
            fetched = transport.fetch_envelope(cfg, rec["lineage"], 1,
                                               "request")
            self.assertIn(f'sha="{rec["sha"]}"', fetched)

    def test_closing_one_lineage_leaves_the_others_ref_readable(self):
        first, second = self._started("first", "second")
        code, closed = self._in_main(
            "close", "--verdict",
            self._verdict_file(first["sha"], name="clean.md",
                               verdict="clean to advance"))
        self.assertEqual(code, 0, closed)
        cfg = type("C", (), {"repo_root": self.repo})()
        fetched = transport.fetch_envelope(cfg, second["lineage"], 1,
                                           "request")
        self.assertIn(f'sha="{second["sha"]}"', fetched)

    @starts_from(None)
    def test_the_round_reference_carries_the_id(self):
        """One word a person carries, and the id is what makes it unique."""
        self.assertEqual(transport.round_reference("L0a1b2c3d4e", 3),
                         "git:L0a1b2c3d4e/3")
        self.assertEqual(transport.parse_round_reference("git:L0a1b2c3d4e/3"),
                         ("L0a1b2c3d4e", 3))
        # A legacy lineage's ordinal is still a valid reference, so every
        # word already written down still resolves.
        self.assertEqual(transport.parse_round_reference("git:21/3"),
                         ("21", 3))
        self.assertEqual(transport.carrier_ref("21", 3, "request"),
                         "refs/loupe/21/3/request")


class TestTheReservationExcludesOneLineageAndNotTheOther(_TwoWorktrees):
    """The INTERVAL, keyed (lineage 27 round 1 F1, re-ruled 2026-09-06).

    A ledger read is one moment, and everything after it destroys: the
    commit and push, the gate run, and the force-push to the round's ref. So
    two overlapping commands acting on ONE lineage must still exclude each
    other — and two acting on DIFFERENT lineages must not, because they
    share no ref, no round number and no terminal marker.

    Two SEPARATE CLI processes, because that is what the defect is made of,
    and a gate the test controls, because the gates are the interval. The
    rows about the locks themselves (their names, and `fcntl`'s absence)
    need no repository and start from a state directory alone.
    """

    PREFIX = "keyed-overlap-"
    PAUSING_GATE = True
    LAYERS = ("other", "origin")

    def test_two_overlapping_handoffs_of_one_lineage_are_refused(self):
        """Same worktree, same lineage: the second is refused before it
        commits, pushes, runs a gate of its own or records anything."""
        self._hold(self.repo)
        a = self._spawn_handoff(self.repo, "--transport", "path")
        try:
            self._await_gate(self.repo)
            code, second = self._run_handoff(self.repo, "--transport", "path")
        finally:
            self._release(self.repo)
        self.assertNotEqual(code, 0, second)
        self.assertIn("reservation", second["error"])
        self.assertIsNone(second["next"])
        acode, arec = self._finish(a)
        self.assertEqual(acode, 0, arec)
        self.assertEqual(len(self._events("request")), 1)

    def test_a_different_lineage_is_no_longer_refused(self):
        """The retirement, measured. The other worktree's handoff overlaps
        this one and is ADMITTED: it opens its own lineage, and there is no
        shared ref, round or marker for it to overwrite."""
        self._hold(self.repo)
        a = self._spawn_handoff(self.repo, "--transport", "git")
        try:
            self._await_gate(self.repo)
            code, b = self._run_handoff(self.other, "--transport", "git")
        finally:
            self._release(self.repo)
        self.assertEqual(code, 0, b)
        acode, arec = self._finish(a)
        self.assertEqual(acode, 0, arec)
        self.assertNotEqual(arec["lineage"], b["lineage"])
        self.assertEqual(len(self._events("request")), 2)
        refs = git_out(self.repo, "ls-remote", "origin", "refs/loupe/*")
        self.assertIn(f"refs/loupe/{arec['lineage']}/1/request", refs)
        self.assertIn(f"refs/loupe/{b['lineage']}/1/request", refs)

    def test_a_close_overlapping_its_own_lineages_handoff_is_refused(self):
        """`close --lineage` from the SAME branch, mid-admission: it would
        end the lineage over a round the ledger does not yet show."""
        self._hold(self.repo)
        a = self._spawn_handoff(self.repo, "--transport", "path")
        decision = ("close", "--lineage", "--reason",
                    "ending it mid-admission", "--by", "user")
        try:
            self._await_gate(self.repo)
            code, refused = self._run(self.repo, *decision)
        finally:
            self._release(self.repo)
        self.assertNotEqual(code, 0, refused)
        self.assertIn("reservation", refused["error"])
        self.assertEqual(self._events(Ledger.LINEAGE_CLOSED), [],
                         "the lineage was closed over an admission in flight")
        acode, arec = self._finish(a)
        self.assertEqual(acode, 0, arec)

    def test_a_close_of_another_lineage_is_not_refused(self):
        """The other worktree's own review closes while this one's handoff
        runs: two reviews, two locks, no contention."""
        code, theirs = self._handoff(self._in_other, "--transport", "path")
        self.assertEqual(code, 0, theirs)
        self._marker(self.other, "started").unlink(missing_ok=True)
        self._hold(self.repo)
        a = self._spawn_handoff(self.repo, "--transport", "path")
        try:
            self._await_gate(self.repo)
            code, closed = self._run(
                self.other, "close", "--lineage", "--reason",
                "the other review ends on its own schedule", "--by", "user")
        finally:
            self._release(self.repo)
        self.assertEqual(code, 0, closed)
        self.assertEqual(closed["lineage"], theirs["lineage"])
        acode, arec = self._finish(a)
        self.assertEqual(acode, 0, arec)
        self.assertNotEqual(arec["lineage"], theirs["lineage"])
        closures = self._events(Ledger.LINEAGE_CLOSED)
        self.assertEqual([c["lineage"] for c in closures],
                         [theirs["lineage"]])

    @starts_from(None)
    def test_without_flock_admission_is_refused_rather_than_unguarded(self):
        """The platform guard, unchanged: where `fcntl` is absent the
        reservation cannot be taken and the tool refuses admission naming
        why — it never proceeds unguarded."""
        reservation = transport.LineageReservation(Ledger(self.state),
                                                   branch="main",
                                                   lineage="L0a1b2c3d4e")
        with mock.patch.object(transport, "fcntl", None):
            with self.assertRaises(transport.Refusal) as caught:
                reservation.acquire()
        self.assertIn("fcntl", str(caught.exception))
        self.assertTrue(caught.exception.remedy)

    @starts_from(None)
    def test_the_lock_files_are_named_by_lineage(self):
        """Two names, which is what makes the exclusion per review."""
        led = Ledger(self.state)
        one = transport.LineageReservation(led, branch="main",
                                           lineage="L0a1b2c3d4e")
        two = transport.LineageReservation(led, branch="other",
                                           lineage="Lfedcba9876")
        self.assertNotEqual(one.path, two.path)
        self.assertEqual(one.path.name, "lineage-L0a1b2c3d4e.lock")

    @starts_from(None)
    def test_the_opening_lock_is_named_by_branch(self):
        """The lineage that has no id yet is reserved by BRANCH: a slug a
        person can read, and a digest so two branch names never collapse
        onto one lock. A detached HEAD gets the single unnamed lock."""
        led = Ledger(self.state)
        mine = transport.NewLineageReservation(led, branch="main")
        theirs = transport.NewLineageReservation(led, branch="feature/x")
        self.assertNotEqual(mine.path, theirs.path)
        self.assertTrue(mine.path.name.startswith("lineage-new-main-"))
        self.assertTrue(theirs.path.name.startswith("lineage-new-feature-x-"))
        self.assertEqual(
            transport.NewLineageReservation(led, branch="HEAD").path.name,
            "lineage-new.lock")

    @starts_from(None)
    def test_two_openers_on_one_branch_cannot_mint_two_lineages(self):
        """An id that does not exist yet cannot be locked by id, so the
        assignment and the admission after it are one critical section —
        keyed by branch, because that is the resource. The second opener
        waits for no one: it is refused while the first holds it."""
        led = Ledger(self.state)
        first = transport.NewLineageReservation(led, branch="main",
                                                verb="handoff").acquire()
        self.addCleanup(first.release)
        second = transport.NewLineageReservation(led, branch="main",
                                                 verb="handoff")
        with self.assertRaises(transport.Refusal) as caught:
            second.acquire()
        self.assertIn("reservation", str(caught.exception))
        self.assertIn("a new lineage on this branch", str(caught.exception))
        first.release()
        second.acquire().release()          # free again: no stale state

    @starts_from(None)
    def test_two_openers_on_two_branches_exclude_nothing(self):
        """The counterpart, and the reason the opening lock is not
        repository-wide: two branches opening two reviews share no id, no
        ref, no round and no marker, so neither may wait on the other."""
        led = Ledger(self.state)
        first = transport.NewLineageReservation(led, branch="main",
                                                verb="handoff").acquire()
        self.addCleanup(first.release)
        second = transport.NewLineageReservation(led, branch="other",
                                                 verb="handoff").acquire()
        second.release()


#: One review opened from `main` on the default carrier (bare origin), then
#: a new commit on `main`: the open lineage's next head, which a test moves
#: `same` onto so an open and a fresh lineage contend for one head.
OPENED_THEN_MOVED = (("handoff", "first", "repo"),
                     ("commit", "repo", "four\n", "more", "main"))


class TestTheHeadIsReservedAcrossItsAdmission(_TwoWorktrees):
    """0.29.0 review round 1 F1: the shared-target admission is HELD, not
    read once.

    `require_unshared_target` reads which lineages name a head; the hand-off
    then gates for minutes before `record_handoff` writes the request that
    makes the head visible. Two hand-offs of one head from two branches hold
    two DIFFERENT lineage reservations, so both read "nobody targets this",
    both gated, pushed and recorded, and the SHA-only resolver raised
    `AmbiguousLineage` (measured on the reviewed tree: both exits 0, two
    requests). The sequential order refused the second. `admit_target` now
    reserves the head (`transport.TargetReservation`, `targets/<sha>.lock`)
    before the read, re-reads the ledger under it, and the caller releases it
    after the record.

    THE DOMAIN, each row a test (A holds the head inside its gate, B runs):

      refuse  fresh A / fresh B, two branches, both undeclared — either
              arrival order
      refuse  A undeclared, B --shared-target (exclusive holder); control:
              B's same declaration after A ends is admitted
      refuse  A --shared-target, B undeclared (shared holder)
      pass    A and B both --shared-target (paired control for the mode)
      refuse  an OPEN lineage continuing onto the head against a fresh one,
              both orders; control: sequentially, the ledger refuses
      pass    B on another branch at another head (unrelated-SHA control)
      refuse  emit-request beside a hand-off of the head, both orders
      pass    after A FAILS its gate, B is admitted (released on refusal)
      pass    after A is KILLED in its gate, B is admitted (the kernel
              releases a dead holder: no stale state, no race)
      held    the head stays reserved while `record_handoff` runs
      read    a request recorded after the caller's ledger was cached is
              seen under the reservation (`Ledger.reload`)

    Same branch, same head: two hand-offs of ONE branch are refused earlier,
    by the lineage or opening reservation, before a commit is known
    (`TestTheReservationExcludesOneLineageAndNotTheOther`), and the cached
    re-serve of a kept request adds no lineage to its head, so it takes none.

    Every refusal is checked against the promise the refusal makes: B's own
    gate never started, B's branch never reached the remote, and B recorded
    no request.

    MUTATIONS (2026-10-05, python3.15, `review/cli.py` and
    `review/transport.py` copied aside, restored and compared byte for
    byte; the reviewer's FALSIFICATION script run under each):
      `admit_target` takes no reservation -> 11 of the 17 red (every refuse
        row, the cleanup rows and the record-interval row) and the
        falsification red (two requests, `AmbiguousLineage`);
      `ledger.reload()` dropped from `admit_target` ->
        `test_the_read_is_taken_under_the_reservation` red;
      every reservation taken exclusive -> `test_two_declared_reviews_run_
        side_by_side`, the shared/shared lock row and the shared-holder
        evidence red;
      a declared admission takes none -> both mixed-mode rows and the two
        mixed lock rows red;
      the reservation released before `record_handoff` ->
        `test_the_head_stays_reserved_while_the_request_is_recorded` red.
    """

    PREFIX = "head-reservation-"
    PAUSING_GATE = True
    LAYERS = ("other", "origin", "same")

    def _overlap(self, holder, holder_extra, second):
        """Hold `holder`'s hand-off inside its gate, run `second()` (which
        returns (exit, record)), release, and return both outcomes."""
        for where in (self.repo, self.other, self.same):
            self._marker(where, "started").unlink(missing_ok=True)
        self._hold(holder)
        first = self._spawn_handoff(holder, *holder_extra)
        try:
            self._await_gate(holder)
            b_code, b = second()
        finally:
            self._release(holder)
        a_code, a = self._finish(first)
        return a_code, a, b_code, b

    def _assert_refused_unrun(self, code, rec, where, *, sha):
        """The refusal's promise: blocked, before B's gate, nothing pushed
        from B's branch and no request recorded by B."""
        self.assertNotEqual(code, 0, rec)
        self.assertIsNone(rec.get("next"), rec)
        self.assertEqual(rec.get("next_kind"), "blocked", rec)
        self.assertIn(f"another hand-off of the head {sha[:12]} is being "
                      f"admitted right now", rec["error"])
        self.assertIn("nothing was gated, pushed, emitted or recorded",
                      rec["error"])
        self.assertIn("Wait for it and run this again", rec["remedy"])
        self.assertFalse(self._marker(where, "started").exists(),
                         "the refused hand-off ran its gate")
        branch = Path(where).name if Path(where) != self.repo else "main"
        if branch != "main":
            self.assertNotIn(f"refs/heads/{branch}",
                             git_out(self.repo, "ls-remote", "origin"))

    def _head(self, where):
        return git_out(where, "rev-parse", "HEAD")

    def _one_review_of(self, sha):
        requests = [e for e in self._events("request") if e["sha"] == sha]
        self.assertEqual(len({e["lineage"] for e in requests}), 1, requests)
        Ledger(self.state).recorded_lineage_for_sha(sha)   # no ambiguity
        return requests

    # ------------------------------------------------- fresh / fresh rows

    def test_an_undeclared_overlap_of_one_head_is_refused(self):
        sha = self._head(self.repo)
        a_code, a, b_code, b = self._overlap(
            self.repo, (), lambda: self._run_handoff(self.same))
        self._assert_refused_unrun(b_code, b, self.same, sha=sha)
        self.assertIn("declared with --shared-target", b["error"])
        self.assertEqual(a_code, 0, a)
        self.assertEqual(len(self._one_review_of(sha)), 1)

    def test_the_other_arrival_order_is_refused_too(self):
        sha = self._head(self.same)
        a_code, a, b_code, b = self._overlap(
            self.same, (), lambda: self._run_handoff(self.repo))
        self._assert_refused_unrun(b_code, b, self.repo, sha=sha)
        self.assertEqual(a_code, 0, a)
        self.assertEqual(len(self._one_review_of(sha)), 1)

    def test_a_declared_review_cannot_run_beside_an_undeclared_one(self):
        sha = self._head(self.repo)
        a_code, a, b_code, b = self._overlap(
            self.repo, (), lambda: self._run_handoff(self.same, *SHARE))
        self._assert_refused_unrun(b_code, b, self.same, sha=sha)
        self.assertEqual(a_code, 0, a)
        # Control: the same declaration once A has recorded is admitted —
        # the sequential order, a second review that was meant.
        code, again = self._run_handoff(self.same, *SHARE)
        self.assertEqual(code, 0, again)
        self.assertEqual(again["sha"], sha)
        self.assertNotEqual(again["lineage"], a["lineage"])

    def test_an_undeclared_review_cannot_run_beside_a_declared_one(self):
        sha = self._head(self.repo)
        a_code, a, b_code, b = self._overlap(
            self.repo, SHARE, lambda: self._run_handoff(self.same))
        self._assert_refused_unrun(b_code, b, self.same, sha=sha)
        self.assertIn("declared --shared-target holds it", b["error"])
        self.assertEqual(a_code, 0, a)
        self.assertEqual(len(self._one_review_of(sha)), 1)

    def test_two_declared_reviews_run_side_by_side(self):
        """The mode's paired control: a share both sides declared is what
        `--shared-target` is for, and neither waits for the other."""
        sha = self._head(self.repo)
        a_code, a, b_code, b = self._overlap(
            self.repo, SHARE, lambda: self._run_handoff(self.same, *SHARE))
        self.assertEqual(b_code, 0, b)
        self.assertEqual(a_code, 0, a)
        self.assertEqual({a["sha"], b["sha"]}, {sha})
        self.assertNotEqual(a["lineage"], b["lineage"])

    def test_an_unrelated_head_is_not_refused(self):
        """The unrelated-SHA control: another branch at another commit is
        another reservation, admitted beside the holder."""
        a_code, a, b_code, b = self._overlap(
            self.repo, (), lambda: self._run_handoff(self.other))
        self.assertEqual(b_code, 0, b)
        self.assertEqual(a_code, 0, a)
        self.assertNotEqual(a["sha"], b["sha"])
        self.assertNotEqual(a["lineage"], b["lineage"])

    # ------------------------------------------------- open / fresh rows

    def _moved(self):
        """`same` fast-forwarded onto `main`'s new head, which `main`'s open
        lineage (`first`) has not yet requested."""
        first = self._started("first")
        sh("git", "-C", str(self.same), "merge", "-q", "--ff-only", "main")
        sha = self._head(self.repo)
        self.assertEqual(self._head(self.same), sha)
        self.assertNotEqual(first["sha"], sha)
        return first, sha

    @starts_from(OPENED_THEN_MOVED)
    def test_an_open_lineage_holding_the_head_refuses_a_fresh_one(self):
        first, sha = self._moved()
        a_code, a, b_code, b = self._overlap(
            self.repo, (), lambda: self._run_handoff(self.same))
        self._assert_refused_unrun(b_code, b, self.same, sha=sha)
        self.assertEqual(a_code, 0, a)
        self.assertEqual(a["lineage"], first["lineage"])
        self.assertEqual(len(self._one_review_of(sha)), 1)
        # Control: sequentially, the recorded request refuses it instead.
        code, after = self._run_handoff(self.same)
        self.assertNotEqual(code, 0, after)
        self.assertIn("is already the target of lineage(s) "
                      f"{first['lineage']}", after["error"])

    @starts_from(OPENED_THEN_MOVED)
    def test_a_fresh_lineage_holding_the_head_refuses_an_open_one(self):
        first, sha = self._moved()
        a_code, a, b_code, b = self._overlap(
            self.same, (), lambda: self._run_handoff(self.repo))
        self._assert_refused_unrun(b_code, b, self.repo, sha=sha)
        self.assertEqual(a_code, 0, a)
        self.assertNotEqual(a["lineage"], first["lineage"])
        self.assertEqual(len(self._one_review_of(sha)), 1)

    # ------------------------------------------------- emit-request rows

    def _emit_request(self, where, out):
        return self._run(where, "emit-request", "--claim-file",
                         str(self.claim), "--base", self.base,
                         "--out", str(out))

    def test_emit_request_beside_a_handoff_of_the_head_is_refused(self):
        sha = self._head(self.repo)
        out = self.tmp / "beside.xml"
        a_code, a, b_code, b = self._overlap(
            self.repo, (), lambda: self._emit_request(self.same, out))
        self._assert_refused_unrun(b_code, b, self.same, sha=sha)
        self.assertFalse(out.exists(), "the refused emit-request wrote")
        self.assertEqual(a_code, 0, a)

    def test_a_handoff_beside_emit_request_of_the_head_is_refused(self):
        sha = self._head(self.repo)
        out = self.tmp / "holder.xml"
        self._marker(self.same, "started").unlink(missing_ok=True)
        self._hold(self.repo)
        first = self._spawn(self.repo, "emit-request", "--claim-file",
                            str(self.claim), "--base", self.base,
                            "--out", str(out))
        try:
            self._await_gate(self.repo)
            b_code, b = self._run_handoff(self.same)
        finally:
            self._release(self.repo)
        a_code, a = self._finish(first)
        self._assert_refused_unrun(b_code, b, self.same, sha=sha)
        self.assertEqual(a_code, 0, a)
        self.assertTrue(out.exists())
        self.assertEqual(self._events("request"), [])

    # ------------------------------------------------- cleanup rows

    def test_a_failed_holder_releases_the_head(self):
        sha = self._head(self.repo)
        self._fail(self.repo)
        a_code, a, b_code, b = self._overlap(
            self.repo, (), lambda: self._run_handoff(self.same))
        self._assert_refused_unrun(b_code, b, self.same, sha=sha)
        self.assertNotEqual(a_code, 0, a)
        self.assertEqual(self._events("request"), [])
        # Released on the refusal path: the next hand-off is admitted.
        self._marker(self.same, "started").unlink(missing_ok=True)
        code, after = self._run_handoff(self.same)
        self.assertEqual(code, 0, after)
        self.assertEqual(len(self._one_review_of(sha)), 1)

    def test_a_killed_holder_releases_the_head(self):
        """A holder that dies mid-gate leaves a holder record and no lock:
        the kernel released it, so the next hand-off is admitted, with no
        timeout, override or stale-lock detection involved."""
        sha = self._head(self.repo)
        self._hold(self.repo)
        first = self._spawn_handoff(self.repo)
        try:
            self._await_gate(self.repo)
            lock = (self.state / transport.TARGET_LOCK_DIR
                    / transport.target_lock_basename(sha))
            self.assertIn('"verb": "handoff"', lock.read_text("utf-8"))
            first.kill()
            first.communicate(timeout=30)
        finally:
            self._release(self.repo)
        code, after = self._run_handoff(self.same)
        self.assertEqual(code, 0, after)
        self.assertEqual(len(self._one_review_of(sha)), 1)

    # ------------------------------------------------- the interval itself

    def test_the_head_stays_reserved_while_the_request_is_recorded(self):
        """In-process: while `record_handoff` writes the request, a second
        reservation of the head is refused — the interval ends AFTER the
        record, not when `_emit` returns."""
        seen = []
        real = transport.record_handoff

        def recording(cfg, ledger, envelope, *a, **kw):
            sha = wire.parse_request(envelope).sha
            probe = transport.TargetReservation(Ledger(self.state),
                                                branch="probe", verb="probe")
            try:
                probe.reserve(sha, shared=False)
            except transport.Refusal:
                seen.append("held")
            else:
                seen.append("free")
                probe.release()
            return real(cfg, ledger, envelope, *a, **kw)

        with mock.patch.object(transport, "record_handoff", recording):
            code, rec = self._handoff(self._in_main)
        self.assertEqual(code, 0, rec)
        self.assertEqual(seen, ["held"])

    @starts_from(None)
    def test_the_read_is_taken_under_the_reservation(self):
        """A request recorded after the caller's ledger cached its read is
        seen once the head is reserved: `admit_target` re-reads."""
        from review.cli import SharedTarget, admit_target
        sha = "a" * 40
        mine = Ledger(self.state)
        self.assertEqual(mine.events(), [])          # cached, empty
        Ledger(self.state).add({"event": "request", "sha": sha, "round": 1,
                                "branch": "other"}, lineage="Lfeedface01")
        hold = transport.TargetReservation(mine, branch="main")
        self.addCleanup(hold.release)
        with self.assertRaises(SharedTarget) as caught:
            admit_target(mine, "Lmine000001", {"sha": sha}, False, hold)
        self.assertIn("Lfeedface01", str(caught.exception))

    # ------------------------------------------------- the lock itself

    @starts_from(None)
    def test_the_modes_exclude_as_the_admission_requires(self):
        led = Ledger(self.state)
        sha = "b" * 40

        def res():
            return transport.TargetReservation(led, branch="x")

        cases = (("exclusive", False, "exclusive", False, False),
                 ("exclusive", False, "shared", True, False),
                 ("shared", True, "exclusive", False, False),
                 ("shared", True, "shared", True, True))
        for first, f_shared, second, s_shared, admitted in cases:
            with self.subTest(holder=first, second=second):
                one = res().reserve(sha, shared=f_shared)
                try:
                    two = res()
                    if admitted:
                        two.reserve(sha, shared=s_shared).release()
                    else:
                        with self.assertRaises(transport.Refusal):
                            two.reserve(sha, shared=s_shared)
                finally:
                    one.release()
                res().reserve(sha, shared=False).release()   # free again
        held = res().reserve(sha, shared=False)
        try:                                   # another head's lock is its own
            res().reserve("c" * 40, shared=False).release()
        finally:
            held.release()

    @starts_from(None)
    def test_the_lock_files_are_named_by_head(self):
        self.assertEqual(transport.target_lock_basename("d" * 40),
                         "d" * 40 + ".lock")
        odd = transport.target_lock_basename("../HEAD")
        self.assertNotIn("/", odd)
        self.assertTrue(odd.startswith("x-"))

    @starts_from(None)
    def test_without_flock_the_head_is_not_admitted(self):
        hold = transport.TargetReservation(Ledger(self.state), branch="main")
        with mock.patch.object(transport, "fcntl", None):
            with self.assertRaises(transport.Refusal) as caught:
                hold.reserve("e" * 40, shared=False)
        self.assertIn("fcntl", str(caught.exception))


class TestALegacyLedgerReadsAsItDidBeforeKeying(_TwoWorktrees):
    """The append-only half, and the one that decides whether this change is
    safe to ship: an UNMIGRATED ledger must read exactly as it always did.

    In-process over a real ledger file: these are reads, and the events are
    written the way a pre-0.20.0 installation wrote them — with no `lineage`
    field at all. No repository: a state directory is the whole fixture.
    """

    PREFIX = "keyed-legacy-"
    START = None

    def _legacy(self, closures=2):
        """A ledger with `closures` closed lineages and one open, none of
        whose events declares an id — the pre-keying shape exactly."""
        led = Ledger(self.state)
        for n in range(1, closures + 2):
            sha = f"{n:040x}"
            led.add({"event": "request", "round": 1, "sha": sha, "bytes": 1})
            led.add({"event": "verdict", "round": 1, "sha": sha, "bytes": 1,
                     "verdict": "changes requested", "finding_ids": 0})
            if n <= closures:
                # Distinct reasons: `_uid` content-addresses an event, so
                # two byte-identical closures would deduplicate into one and
                # the fixture would build fewer lineages than it states.
                led.add({"event": Ledger.LINEAGE_CLOSED, "at_round": 1,
                         "outcome": "decision", "reason": f"done {n}",
                         "authorized_by": "user"})
        return led

    def test_no_event_declares_an_id(self):
        """The fixture's own premise, asserted rather than assumed."""
        led = self._legacy()
        self.assertTrue(led.events())
        self.assertEqual([e for e in led.events() if "lineage" in e], [])

    def test_the_positional_ordinals_are_the_keys(self):
        led = self._legacy(closures=2)
        self.assertEqual(led.lineages(), ["1", "2", "3"])
        self.assertEqual(led.open_lineages(), ["3"])
        self.assertTrue(led.is_closed("1"))
        self.assertTrue(led.is_closed("2"))
        self.assertFalse(led.is_closed("3"))

    def test_current_reads_what_it_read_before(self):
        """`current()` meant everything after the last closure marker;
        `current(<the open ordinal>)` is the same list, event for event."""
        led = self._legacy(closures=2)
        events = led.events()
        marks = [i for i, e in enumerate(events)
                 if e.get("event") == Ledger.LINEAGE_CLOSED]
        self.assertEqual(led.current("3"), events[marks[-1] + 1:])
        self.assertEqual(led.rounds("3"), [1])
        self.assertEqual(led.completed_rounds("3"), [1])

    def test_the_numbers_are_the_numbers_it_reported(self):
        led = self._legacy(closures=26)
        self.assertEqual(led.open_lineages(), ["27"])
        self.assertEqual(led.lineage_number("27"), 27)
        report = led.report("27", round_cap=3)
        self.assertEqual(report["lineage"]["id"], "27")
        self.assertEqual(report["lineage"]["number"], 27)
        self.assertEqual(len(report["lineage"]["closed_before"]), 26)

    def test_an_unbranched_open_lineage_is_still_continued(self):
        """A round opened before the branch field existed carries none, so
        no branch matches it — and adopting it is what keeps a handoff into
        a pre-upgrade ledger continuing that review rather than opening a
        second one beside it."""
        led = self._legacy(closures=1)
        self.assertEqual(led.choose_open_lineage("main"),
                         ("2", Ledger.LINEAGE_ADOPTED))
        self.assertEqual(led.choose_open_lineage("other"),
                         ("2", Ledger.LINEAGE_ADOPTED))
        # And a detached HEAD adopts it too, because one open lineage is
        # not a choice.
        self.assertEqual(led.choose_open_lineage(""),
                         ("2", Ledger.LINEAGE_ADOPTED))

    def test_a_mixed_ledger_keeps_the_prefixs_ordinals(self):
        """A legacy prefix, then keyed events: the prefix is unrenumbered
        and the counter freezes at the first keyed event."""
        led = self._legacy(closures=2)
        led.add({"event": "request", "round": 1, "sha": "f" * 40,
                 "bytes": 1, "branch": "feature"}, lineage="L0a1b2c3d4e")
        self.assertEqual(led.lineages(), ["1", "2", "3", "L0a1b2c3d4e"])
        self.assertEqual(led.lineage_keys()[-1], "L0a1b2c3d4e")
        # The legacy events are where they were.
        self.assertEqual(len(led.current("3")), 2)
        self.assertEqual(len(led.current("L0a1b2c3d4e")), 1)
        # And a branch now chooses between them deterministically.
        self.assertEqual(led.choose_open_lineage("feature"),
                         ("L0a1b2c3d4e", Ledger.LINEAGE_BOUND))
        self.assertEqual(led.choose_open_lineage("main"),
                         ("3", Ledger.LINEAGE_ADOPTED))

    def test_a_detached_head_with_two_open_lineages_cannot_choose(self):
        """The one state a verb must refuse rather than guess."""
        led = self._legacy(closures=1)
        led.add({"event": "request", "round": 1, "sha": "f" * 40,
                 "bytes": 1, "branch": "feature"}, lineage="L0a1b2c3d4e")
        self.assertEqual(led.choose_open_lineage(""),
                         (None, Ledger.LINEAGE_AMBIGUOUS))
        # `HEAD` is a state and not a name, and reads the same way.
        self.assertEqual(led.choose_open_lineage("HEAD"),
                         (None, Ledger.LINEAGE_AMBIGUOUS))

    def test_the_deliberately_global_kinds_stay_unkeyed(self):
        """Aliases and commit waivers survive every lineage boundary, so
        `Ledger.add` never stamps them and both readers read the file."""
        led = self._legacy(closures=1)
        led.add({"event": "lineage", "kind": "rename", "from": "a" * 16,
                 "to": "b" * 16})
        led.add({"event": "waiver", "sha": "c" * 40, "reason": "trivial",
                 "authorized_by": "user"})
        self.assertEqual(len(led.lineage()), 1)
        self.assertEqual(len(led.waivers()), 1)
        for e in led.events():
            if e.get("event") in ("lineage", "waiver"):
                self.assertNotIn("lineage", e)


class TestTheCrossLineageNotice(_TwoWorktrees):
    """Ruling 1 of 2026-09-06: per-lineage answers plus a NOTICE.

    Aliases are global by design, so one identity can be ruled in two
    lineages at once. Answers stay derived over one lineage's rulings; what
    the notice adds is that a reader is told the other review exists, with
    its id, round and disposition. Paired controls throughout: the notice
    appears when the identity is live elsewhere and disappears when it is
    not, when the other lineage is closed, and when there is no other. No
    repository: a ledger file in a state directory is the whole fixture.
    """

    PREFIX = "keyed-notice-"
    START = None

    FP = "fp2:" + "a" * 16

    def _ruled(self, led, lineage, round_no=1, fp=None, disposition=None):
        """One ruling in `lineage`, optionally answered."""
        sha = f"{abs(hash((lineage, round_no))) % (16 ** 40):040x}"
        led.add({"event": "request", "round": round_no, "sha": sha,
                 "bytes": 1, "branch": lineage}, lineage=lineage)
        led.add({"event": "verdict", "round": round_no, "sha": sha,
                 "bytes": 1, "verdict": "changes requested",
                 "finding_ids": 1}, lineage=lineage)
        led.add({"event": "finding", "round": round_no,
                 "fp": fp or self.FP, "id": "F1", "severity": "High",
                 "title": "the shared identity"}, lineage=lineage)
        if disposition is not None:
            led.add({"event": "disposition", "round": round_no,
                     "fp": fp or self.FP, "finding_id": "F1",
                     "disposition": disposition}, lineage=lineage)
        return led

    def test_the_notice_names_the_other_open_lineage(self):
        led = Ledger(self.state)
        self._ruled(led, "Lmine000001")
        self._ruled(led, "Ltheirs0002", disposition="refuted")
        notices = led.cross_lineage_notices("Lmine000001")
        self.assertEqual(len(notices), 1, notices)
        self.assertEqual(notices[0]["lineage"], "Ltheirs0002")
        self.assertEqual(notices[0]["round"], 1)
        self.assertEqual(notices[0]["disposition"], "refuted")
        self.assertEqual(notices[0]["fp"], self.FP)
        # It renders on the face of an envelope and in `brief`.
        rendered = render_cross_lineage_md(notices)
        self.assertIn("Ltheirs0002", rendered)
        self.assertIn("refuted", rendered)

    def test_an_unanswered_identity_elsewhere_is_named_too(self):
        """"Unanswered in another open lineage" is the other half of the
        ruling, and it reports as `unanswered` rather than as silence."""
        led = Ledger(self.state)
        self._ruled(led, "Lmine000001")
        self._ruled(led, "Ltheirs0002")
        notices = led.cross_lineage_notices("Lmine000001")
        self.assertEqual([n["disposition"] for n in notices], ["unanswered"])

    def test_a_different_identity_elsewhere_is_not_named(self):
        """Control 1: another live review that shares no identity."""
        led = Ledger(self.state)
        self._ruled(led, "Lmine000001")
        self._ruled(led, "Ltheirs0002", fp="fp2:" + "b" * 16)
        self.assertEqual(led.cross_lineage_notices("Lmine000001"), [])
        self.assertEqual(render_cross_lineage_md([]), "")

    def test_a_closed_lineage_is_not_named(self):
        """Control 2: a settled review is history. The notice is about two
        reviews that are both LIVE."""
        led = Ledger(self.state)
        self._ruled(led, "Lmine000001")
        self._ruled(led, "Ltheirs0002", disposition="refuted")
        self.assertEqual(len(led.cross_lineage_notices("Lmine000001")), 1)
        led.add({"event": Ledger.LINEAGE_CLOSED, "at_round": 1,
                 "outcome": "decision", "reason": "done",
                 "authorized_by": "user"}, lineage="Ltheirs0002")
        self.assertEqual(led.cross_lineage_notices("Lmine000001"), [])

    def test_a_settled_finding_here_raises_no_notice(self):
        """Control 3: the notice is about STANDING findings. An identity
        this lineage has already settled is not one a reader must weigh."""
        led = Ledger(self.state)
        self._ruled(led, "Lmine000001", disposition="accepted")
        self._ruled(led, "Ltheirs0002", disposition="refuted")
        self.assertEqual(led.cross_lineage_notices("Lmine000001"), [])

    def test_the_only_lineage_raises_no_notice(self):
        """Control 4: one review live, nothing to name."""
        led = Ledger(self.state)
        self._ruled(led, "Lmine000001")
        self.assertEqual(led.cross_lineage_notices("Lmine000001"), [])

    def test_the_notice_changes_no_record(self):
        """It is a report. Reading it appends nothing, and each lineage's
        own answers are exactly what they were."""
        led = Ledger(self.state)
        self._ruled(led, "Lmine000001")
        self._ruled(led, "Ltheirs0002", disposition="refuted")
        before = list(led.events())
        led.cross_lineage_notices("Lmine000001")
        self.assertEqual(led.events(), before)
        self.assertEqual(
            [f["id"] for f in led.standing_findings("Lmine000001")], ["F1"])
        self.assertEqual(
            [f["id"] for f in led.standing_findings("Ltheirs0002")], ["F1"])


class TestBriefNamesTheDiscardedRound(_TwoWorktrees):
    """The third state `brief` must be able to name, kept.

    A keyed lineage makes the state unreachable going forward — a handoff on
    another branch opens its OWN review, so there is no foreign request in
    this one to swallow. Ledgers that already carry the state must still
    read truthfully, which is why `discarded_requests` survives the stopgap
    that produced it, keyed to the closed lineage it describes.
    """

    PREFIX = "keyed-discarded-"
    START = MAIN_OPENED

    def _discarded(self):
        """The pre-keying state, built the only way it can now be reached:
        two rounds in ONE lineage, and a close that rules one of them with
        the unruled-request guard disabled."""
        first = self._started("first")
        sh("git", "-C", str(self.repo), "commit", "--amend", "-q", "-m",
           "a second emission at a second sha")
        code, second = self._handoff(self._in_main, "--local-only")
        self.assertEqual(code, 0, second)
        with mock.patch.object(transport, "_unruled_open_requests",
                               lambda *a, **k: []):
            code, closed = self._in_main(
                "close", "--verdict",
                self._verdict_file(first["sha"], name="clean.md",
                                   verdict="clean to advance"))
        self.assertEqual(code, 0, closed)
        return first, second

    def test_the_discarded_round_is_named_rather_than_denied(self):
        first, second = self._discarded()
        code, rec = self._in_main("brief")
        self.assertNotEqual(code, 0, rec)
        self.assertIn("discarded", rec["error"])
        self.assertIn(second["sha"][:12], rec["error"])
        self.assertNotIn("already carries a verdict", rec["error"],
                         "the false sentence is still being told")

    def test_an_ordinary_closed_lineage_still_reads_as_it_did(self):
        """The control: nothing was discarded, so nothing is claimed."""
        opened = self._started("first")
        code, closed = self._in_main(
            "close", "--verdict",
            self._verdict_file(opened["sha"], name="clean.md",
                               verdict="clean to advance"))
        self.assertEqual(code, 0, closed)
        code, rec = self._in_main("brief")
        self.assertNotEqual(code, 0, rec)
        self.assertIn("already carries a verdict", rec["error"])
        self.assertNotIn("discarded", rec["error"])



class _SameCommitWorktrees(_TwoWorktrees):
    """`main` and a second branch AT MAIN'S HEAD: one commit, two reviews.

    `_TwoWorktrees` gives two branches at two commits, which is the ordinary
    concurrency. Round-2 F1 is the other axis: a commit identifies CODE, and
    two independently scoped reviews may examine the same code — a release
    branch and a feature branch at one tip, a re-review of an unchanged tip
    under a different claim. The SHA then names two reviews and cannot, by
    itself, name either.

    A test starts from both reviews of the one commit opened, `main` first
    (`ONE_COMMIT`), unless it names another start state.
    """

    LAYERS = ("other", "same")
    START = ONE_COMMIT

    def _in_same(self, *argv):
        return run_cli(self.same, self.state, *argv, cwd=self.cwd)

    def _two_reviews(self):
        """The start's two handoffs of the SAME commit, in its arrival
        order: both admitted, one SHA, two lineages."""
        first, second = self._started("first", "second")
        self.assertEqual(first["sha"], second["sha"])
        self.assertNotEqual(first["lineage"], second["lineage"])
        return first, second


class TestTwoReviewsOfOneCommit(_SameCommitWorktrees):
    """Round-2 F1: the SHA resolvers keep the review the INVOCATION carries.

    `Ledger._round_event_for_sha` chose the newest request or take for a SHA
    across the whole ledger. With one commit under review twice that is a
    file position wearing a lineage's name: `close` from `main` exited 0 and
    appended its closure to the OTHER branch's lineage, and `brief` from
    `main` reported "its bytes were not kept" about a file that existed —
    the resolver had searched the wrong review.

    Every test here is IN-PROCESS through the real CLI; the collision is a
    property of one ledger holding two reviews of one commit, not of two
    processes, so no subprocess is needed to reach it.

    MUTATION: restore the newest whole-ledger SHA match — in
    `Ledger._round_event_for_sha`, return the first `request`/`take` found
    scanning `reversed(self.events())` — and the closures, the retained
    bytes and the refusals below all move to whichever review happened to be
    recorded last.
    """

    PREFIX = "same-commit-"

    def _two_requests_of_one_commit(self, first, second):
        requests = self._events("request")
        self.assertEqual(len(requests), 2, requests)
        self.assertEqual({e["sha"] for e in requests}, {self.shared})
        self.assertEqual({e["lineage"] for e in requests},
                         {first["lineage"], second["lineage"]})

    def test_main_first_opens_two_reviews_of_one_commit(self):
        self._two_requests_of_one_commit(*self._two_reviews())

    @starts_from(ONE_COMMIT_REVERSED)
    def test_the_other_arrival_order_opens_two_reviews_as_well(self):
        """Insertion order reversed: the second review recorded is not the
        one an event-order resolver would pick for either verb."""
        self._two_requests_of_one_commit(*self._two_reviews())

    def test_a_close_appends_its_closure_to_its_own_review(self):
        """The exact incident: a clean close from `main` landed in the other
        branch's lineage."""
        first, second = self._two_reviews()
        code, closed = self._in_main(
            "close", "--verdict",
            self._verdict_file(self.shared, name="clean.md",
                               verdict="clean to advance"))
        self.assertEqual(code, 0, closed)
        # `close`'s `lineage` field is the LIFECYCLE sentence, not the id;
        # the id is where the bytes were kept and what the marker names.
        self.assertIn(f"lineage-{first['lineage']}", closed["kept"])
        closures = self._events(Ledger.LINEAGE_CLOSED)
        self.assertEqual([e["lineage"] for e in closures], [first["lineage"]])
        # The other review is untouched: still open, still round 1.
        code, briefed = self._in_same("brief")
        self.assertEqual(code, 0, briefed)
        self.assertEqual(briefed["lineage"], second["lineage"])

    def test_the_other_branch_closes_its_own_review_afterwards(self):
        """Reversed insertion order for the CLOSE: whichever rules first,
        the second still has a review to close."""
        first, second = self._two_reviews()
        code, closed = self._in_same(
            "close", "--verdict",
            self._verdict_file(self.shared, name="clean-same.md",
                               verdict="clean to advance"))
        self.assertEqual(code, 0, closed)
        self.assertIn(f"lineage-{second['lineage']}", closed["kept"])
        code, closed = self._in_main(
            "close", "--verdict",
            self._verdict_file(self.shared, name="clean-main.md",
                               verdict="clean to advance"))
        self.assertEqual(code, 0, closed)
        self.assertIn(f"lineage-{first['lineage']}", closed["kept"])
        self.assertEqual(sorted(e["lineage"]
                                for e in self._events(Ledger.LINEAGE_CLOSED)),
                         sorted([first["lineage"], second["lineage"]]))

    def test_brief_without_a_source_finds_its_own_retained_bytes(self):
        """"round 1 is open but its bytes were not kept" — about a file that
        was there, in the other review's directory."""
        first, second = self._two_reviews()
        for where, mine in ((self._in_main, first), (self._in_same, second)):
            code, briefed = where("brief")
            self.assertEqual(code, 0, briefed)
            self.assertEqual(briefed["lineage"], mine["lineage"])
            self.assertEqual(briefed["sha"], self.shared)
            self.assertIn(f"lineage-{mine['lineage']}", mine["kept"])

    def test_brief_with_a_source_stays_in_the_review_it_was_run_from(self):
        first, second = self._two_reviews()
        for where, mine in ((self._in_main, first), (self._in_same, second)):
            code, briefed = where("brief", mine["kept"])
            self.assertEqual(code, 0, briefed)
            self.assertEqual(briefed["lineage"], mine["lineage"])

    @starts_from(ONE_COMMIT_GIT)
    def test_validate_publishes_the_verdict_to_its_own_reviews_ref(self):
        """The verdict PUBLICATION boundary: `validate --from-target` is the
        one place a ruling is pushed, and the ref it goes to is keyed on the
        lineage. Choosing by event order put both branches' verdicts on one
        review's ref."""
        first, second = self._started("first", "second")
        self.assertNotEqual(first["lineage"], second["lineage"])
        for where, mine in ((self._in_main, first), (self._in_same, second)):
            code, validated = where(
                "validate", self._verdict_file(
                    self.shared, name=f"v-{mine['lineage']}.md"),
                "--from-target")
            self.assertEqual(code, 0, validated)
            self.assertIn(f"git:{mine['lineage']}/1", validated["relay"])
        refs = git_out(self.repo, "ls-remote", "origin", "refs/loupe/*")
        for mine in (first, second):
            self.assertIn(f"refs/loupe/{mine['lineage']}/1/verdict", refs)

    def test_respond_answers_the_verdict_of_its_own_review(self):
        first, second = self._two_reviews()
        verdict = self._verdict_file(self.shared, name="v.md")
        code, closed = self._in_same("close", "--verdict", verdict)
        self.assertEqual(code, 0, closed)
        self.assertIn(f"lineage-{second['lineage']}", closed["kept"])
        # The finding is ruled in `same`'s review only, and `respond` run
        # there answers it. `main`'s review never saw this verdict.
        rulings = [e for e in self._events("finding")]
        self.assertEqual({e["lineage"] for e in rulings}, {second["lineage"]})
        code, briefed = self._in_main("brief")
        self.assertEqual(code, 0, briefed)
        self.assertEqual(briefed["lineage"], first["lineage"])

    # ---- round-3 F1: the review identity an envelope CARRIES outranks the
    # branch the verb runs from, and travels through brief, ledger add and
    # respond alike — by stamp (path, stdin) and by git reference.

    def _rewrite_stamp(self, kept: str, lineage: str) -> str:
        text = Path(kept).read_text(encoding="utf-8")
        assert 'lineage="' in text
        text = re.sub(r'lineage="[^"]*"', f'lineage="{lineage}"', text, 1)
        path = self.tmp / f"restamped-{lineage}.md"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_brief_of_the_other_reviews_request_names_that_review(self):
        first, second = self._two_reviews()
        code, briefed = self._in_main("brief", second["kept"])
        self.assertEqual(code, 0, briefed)
        self.assertEqual(briefed["lineage"], second["lineage"])
        self.assertIn(second["kept"], briefed["relay"])
        self.assertNotIn(first["kept"], briefed["relay"])

    @starts_from(ONE_COMMIT_REVERSED)
    def test_the_other_arrival_order_briefs_the_stamped_review_too(self):
        first, second = self._two_reviews()
        code, briefed = self._in_same("brief", second["kept"])
        self.assertEqual(code, 0, briefed)
        self.assertEqual(briefed["lineage"], second["lineage"])

    def test_brief_on_stdin_carries_the_stamp_as_a_path_does(self):
        first, second = self._two_reviews()
        proc = subprocess.Popen(
            [sys.executable, "-m", "review", "--ledger-dir", str(self.state),
             "brief", "-"],
            cwd=str(self.repo), env=cli_process_env(), text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        out, err = proc.communicate(
            Path(second["kept"]).read_text(encoding="utf-8"), timeout=120)
        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(json.loads(out)["lineage"], second["lineage"])

    def test_ledger_add_of_the_other_reviews_request_records_only_it(self):
        first, second = self._two_reviews()
        before = len(self._events())
        code, added = self._in_main("ledger", "add", second["kept"])
        if code == 0:
            self.assertEqual(added["lineage"], second["lineage"], added)
            new = self._events()[before:]
            self.assertTrue(all(e.get("lineage") == second["lineage"]
                                for e in new), new)
        else:
            self.assertEqual(len(self._events()), before, added)

    def test_a_stamp_naming_no_review_here_refuses_without_writes(self):
        first, second = self._two_reviews()
        foreign = self._rewrite_stamp(second["kept"], "Lffffffffff")
        before = len(self._events())
        # A READ renders what it is handed and substitutes nothing: the
        # resolved lineage is the stamp's, never this checkout's, and the
        # ledger is untouched.
        code, briefed = self._in_main("brief", foreign)
        self.assertEqual(len(self._events()), before)
        self.assertNotEqual(briefed.get("lineage"), first["lineage"], briefed)
        self.assertNotEqual(briefed.get("lineage"), second["lineage"], briefed)
        # A WRITE either records only under the stamp or refuses; it never
        # lands the bytes in the checkout's review.
        code, added = self._in_main("ledger", "add", foreign)
        new = self._events()[before:]
        if code == 0:
            self.assertTrue(new and all(e.get("lineage") == "Lffffffffff"
                                        for e in new), new)
        else:
            self.assertEqual(new, [], added)

    @starts_from(ONE_COMMIT_GIT)
    def test_respond_with_the_other_reviews_reference_answers_it(self):
        """`respond --verdict git:B/1` from A's checkout answers B (round-3
        F1: the test that used to carry this name never invoked respond)."""
        first, second = self._started("first", "second")
        verdict = self._verdict_file(self.shared, name="v-second.md")
        code, validated = self._in_same("validate", verdict, "--from-target")
        self.assertEqual(code, 0, validated)
        code, closed = self._in_same("close", "--verdict", verdict)
        self.assertEqual(code, 0, closed)
        dispositions = self.tmp / "d-second.json"
        dispositions.write_text(json.dumps({
            "head": self.shared,
            "dispositions": [{
                "finding_id": "F1", "disposition": "accepted",
                "payload": {"change": "fixed", "verification": "the test",
                            "falsification": {"status": "pass",
                                              "mutation": "fails_without_fix"}}}]}),
            encoding="utf-8")
        out = self.tmp / "disposition-second.md"
        code, responded = self._in_main(
            "respond", "--verdict", f"git:{second['lineage']}/1",
            "--from-json", str(dispositions), "--out", str(out))
        self.assertEqual(code, 0, responded)
        recorded = [e for e in self._events("disposition")]
        self.assertTrue(recorded, "no disposition recorded")
        self.assertEqual({e.get("lineage") for e in recorded},
                         {second["lineage"]})

    def _both_reviews_at_round_two(self):
        """The start's `ROUND_TWO_CAPPED`: round 1 answered in BOTH reviews
        (under a committed cap of one), then one new commit that both
        branches point at, and a round-2 request emitted in each — every
        exit asserted, as when the test ran them itself.

        The SHA must be under both reviews or the finding is unreachable:
        with a commit only one review has recorded, `recorded_lineage_for_sha`
        answers correctly from the SHA alone and nothing the invocation
        carries is ever consulted.
        """
        first, second = self._two_reviews()
        self._started("first:close", "first:respond",
                      "second:close", "second:respond")
        b_two, a_two = self._started("b_two", "a_two")
        self.assertEqual(a_two["round"], 2, a_two)
        self.assertEqual(b_two["round"], 2, b_two)
        self.assertEqual(a_two["sha"], self.shared, a_two)
        return first, second, a_two, b_two

    @starts_from(ROUND_TWO_CAPPED)
    def test_validate_judges_a_stamped_request_under_the_review_it_names(self):
        """FALSIFICATION for round-3 F2.

        `cmd_validate` resolved by the checkout's branch while holding an
        envelope that stamps its own review — and it is itself classified as
        a cross-installation reader of both stamped kinds. The cap is the
        one thing a REQUEST's judged lineage decides, so it is what makes
        the wrong answer visible: both reviews are at round 2 of one commit,
        B's cap is raised and A's is not, and B's request is past A's cap
        and within its own.

        MUTATION: drop `carried=` from cmd_validate's `_read_lineage` call;
        B's envelope is judged under A's cap from A's checkout and the
        notice reappears, which is the state it was found in.
        """
        first, second, _a_two, b_two = self._both_reviews_at_round_two()
        # The human raised the cap for THIS lineage only (the start's last
        # step, from `same`): B may reach two, A keeps the cap of one.
        self._started("raised")

        code, judged = self._in_main("validate", b_two["kept"])
        self.assertEqual(code, 0, judged)
        codes = {i["code"] for i in judged.get("items", [])}
        self.assertNotIn(
            "R-BUDGET", codes,
            "B's request was judged against A's cap: the review an envelope "
            "stamps is the one whose limits apply to it")

    @starts_from(ROUND_TWO_CAPPED)
    def test_the_cap_notice_still_reaches_the_review_that_did_not_raise_it(self):
        """The control the falsification needs: R-BUDGET is reachable, and
        reaches an envelope of the review whose cap was NOT raised — same
        checkout, same commit, same round, different stamped review. A fix
        that silenced the notice everywhere would pass the test above and
        fail this one."""
        first, second, a_two, _b_two = self._both_reviews_at_round_two()
        self._started("raised")
        code, judged = self._in_main("validate", a_two["kept"])
        self.assertEqual(code, 0, judged)
        codes = {i["code"] for i in judged.get("items", [])}
        self.assertIn("R-BUDGET", codes, judged)

    def test_ledger_add_records_into_the_review_it_was_run_from(self):
        first, second = self._two_reviews()
        verdict = self._verdict_file(self.shared, name="v.md")
        code, added = self._in_same("ledger", "add", verdict)
        self.assertEqual(code, 0, added)
        self.assertEqual(added["lineage"], second["lineage"])
        self.assertEqual({e["lineage"] for e in self._events("verdict")},
                         {second["lineage"]})

    def test_an_unresolvable_commit_refuses_and_records_nothing(self):
        """A THIRD checkout, on a branch holding no review: nothing there
        says which of the two reviews of this commit is meant, so the verb
        refuses instead of choosing by the order they were recorded in."""
        first, second = self._two_reviews()
        third = self.tmp / "third"
        sh("git", "-C", str(self.repo), "worktree", "add", "-q", "-b",
           "third", str(third), "HEAD")
        before = len(self._events())
        code, rec = run_cli(third, self.state, "close", "--verdict",
                            self._verdict_file(self.shared, name="v3.md"),
                            cwd=self.cwd)
        self.assertNotEqual(code, 0, rec)
        self.assertEqual(rec["next_kind"], "blocked", rec)
        self.assertIn("2 lineages", rec["error"])
        self.assertIn(first["lineage"], rec["error"])
        self.assertIn(second["lineage"], rec["error"])
        self.assertEqual(len(self._events()), before,
                         "an ambiguity refusal wrote to the ledger")

    @starts_from(ONE_COMMIT_GIT)
    def test_a_carried_git_reference_stays_authoritative(self):
        """The one carrier that names its review in the argument itself: the
        reference decides, from any checkout."""
        first, second = self._started("first", "second")
        self.assertNotEqual(first["lineage"], second["lineage"])
        for mine in (first, second):
            code, briefed = self._in_main(
                "brief", transport.round_reference(mine["lineage"], 1))
            self.assertEqual(code, 0, briefed)
            self.assertEqual(briefed["lineage"], mine["lineage"])
            self.assertEqual(briefed["sha"], self.shared)

    @starts_from(ONE_COMMIT_PASTE)
    def test_a_paste_round_of_a_shared_commit_keeps_its_review(self):
        first, second = self._started("first", "second")
        self.assertNotEqual(first["lineage"], second["lineage"])
        for where, mine in ((self._in_main, first), (self._in_same, second)):
            code, briefed = where("brief")
            self.assertEqual(code, 0, briefed)
            self.assertEqual(briefed["lineage"], mine["lineage"])

    # ------------------------------------------------------------- controls

    @starts_from(TWO_BRANCHES)
    def test_two_distinct_commits_resolve_as_they_always_did(self):
        """The valid control: two reviews of two commits need no
        disambiguation, and every resolver answers exactly as before."""
        first, second = self._started("first", "second")
        self.assertNotEqual(first["sha"], second["sha"])
        # Resolved from ANY checkout, because one SHA names one review.
        for mine in (first, second):
            code, briefed = self._in_same("brief", mine["kept"])
            self.assertEqual(code, 0, briefed)
            self.assertEqual(briefed["lineage"], mine["lineage"])

    @starts_from(MAIN_OPENED)
    def test_one_review_of_a_commit_needs_nothing_carried(self):
        """The single-match control, from a checkout that names no review."""
        only = self._started("first")
        code, briefed = self._in_same("brief", only["kept"])
        self.assertEqual(code, 0, briefed)
        self.assertEqual(briefed["lineage"], only["lineage"])

    @starts_from(MAIN_OPENED)
    def test_a_repeated_sha_in_a_closed_and_an_open_review(self):
        """One commit, one branch, TWO reviews in sequence: the first closed
        and historical, the second open. The branch names the open one."""
        first = self._started("first")
        code, closed = self._in_main(
            "close", "--verdict",
            self._verdict_file(self.shared, name="clean.md",
                               verdict="clean to advance"))
        self.assertEqual(code, 0, closed)
        # RR1 (2026-10-04): a closed lineage's target still counts, so the
        # second review of this commit is declared.
        code, second = self._handoff(self._in_main, "--local-only", *SHARE)
        self.assertEqual(code, 0, second)
        self.assertNotEqual(second["lineage"], first["lineage"])
        self.assertEqual(second["sha"], first["sha"])
        code, briefed = self._in_main("brief")
        self.assertEqual(code, 0, briefed)
        self.assertEqual(briefed["lineage"], second["lineage"])
        # And the historical review is still readable by its own bytes.
        led = Ledger(self.state)
        self.assertEqual(sorted(led.lineages_for_sha(self.shared)),
                         sorted([first["lineage"], second["lineage"]]))
        self.assertTrue(led.is_closed(first["lineage"]))

    def test_the_resolvers_answer_the_set_rather_than_the_newest(self):
        """The unit statement of the rule, under the CLI's own state."""
        first, second = self._two_reviews()
        led = Ledger(self.state)
        self.assertEqual(sorted(led.lineages_for_sha(self.shared)),
                         sorted([first["lineage"], second["lineage"]]))
        with self.assertRaises(AmbiguousLineage):
            led.recorded_lineage_for_sha(self.shared)
        with self.assertRaises(AmbiguousLineage):
            led.carrier_lineage_for_sha(self.shared)
        for mine in (first, second):
            self.assertEqual(
                led.recorded_lineage_for_sha(self.shared,
                                             prefer=mine["lineage"]),
                mine["lineage"])
            self.assertEqual(
                led.carrier_lineage_for_sha(self.shared,
                                            prefer=mine["lineage"]),
                mine["lineage"])

class TestAFreshReviewerTakesBothReviewsOfOneCommit(_SameCommitWorktrees):
    """Round-3 F3. A reviewer's ledger that has seen neither review takes
    both git-carried reviews of one commit: a request is its BYTES, and two
    independently emitted requests are two, however many commits they
    share. What stays refused is the same bytes carried under a second
    lineage — a retake that would move a verdict's destination.

    MUTATION: compare the incoming lineage against the sole SHA candidate
    again (`carrier_lineage_for_sha(sha, prefer=lineage)`) and the second
    legitimate take is refused as "already taken".
    """
    PREFIX = "fresh-reviewer-"
    START = ONE_COMMIT_GIT

    def setUp(self):
        super().setUp()
        self.reviewer = self.tmp / "reviewer-state"

    def _take(self, *argv):
        return run_cli(self.repo, self.reviewer, "take", *argv, "--as",
                       "codex", cwd=self.cwd)

    def _reviewer_lineages(self):
        led = Ledger(self.reviewer)
        return [e.get("lineage") for e in led.events()
                if e.get("event") == "take"]

    def _two_git_reviews(self):
        """The start's two `git`-carried reviews of one commit (a bare
        origin added after the `same` checkout, as the `origin` layer
        does)."""
        return self._started("first", "second")

    def test_both_reviews_of_one_commit_enter_a_fresh_reviewer_ledger(self):
        first, second = self._two_git_reviews()
        for mine in (first, second):
            code, taken = self._take(f"git:{mine['lineage']}/1")
            self.assertEqual(code, 0, taken)
        self.assertEqual(set(self._reviewer_lineages()),
                         {first["lineage"], second["lineage"]})

    def test_the_other_arrival_order_enters_too(self):
        first, second = self._two_git_reviews()
        for mine in (second, first):
            code, taken = self._take(f"git:{mine['lineage']}/1")
            self.assertEqual(code, 0, taken)
        self.assertEqual(set(self._reviewer_lineages()),
                         {first["lineage"], second["lineage"]})

    def test_a_retake_is_idempotent(self):
        first, second = self._two_git_reviews()
        for mine in (first, second, first):
            code, taken = self._take(f"git:{mine['lineage']}/1")
            self.assertEqual(code, 0, taken)
        self.assertEqual(set(self._reviewer_lineages()),
                         {first["lineage"], second["lineage"]})

    def test_the_same_bytes_under_a_second_lineage_refuse_without_writes(self):
        first, second = self._two_git_reviews()
        code, taken = self._take(f"git:{first['lineage']}/1")
        self.assertEqual(code, 0, taken)
        bare = str(self.tmp / "origin.git")
        blob = git_out(Path(bare), "rev-parse",
                       f"refs/loupe/{first['lineage']}/1/request")
        sh("git", "-C", bare, "update-ref",
           "refs/loupe/Lc0nf11c700/1/request", blob)
        before = len(Ledger(self.reviewer).events())
        code, refused = self._take("git:Lc0nf11c700/1")
        self.assertNotEqual(code, 0, refused)
        self.assertIn("already taken", json.dumps(refused))
        self.assertEqual(len(Ledger(self.reviewer).events()), before)


class TestTheSnapshotIsTakenUnderTheReservation(_TwoWorktrees):
    """Round-2 F2: a lifecycle read taken BEFORE the lock is not the state.

    `cmd_handoff` selected its lineage and read the ledger before acquiring
    the reservation, and `Ledger.events` caches its first disk read — so an
    ordinary close completing in that interval was invisible. Acquisition
    then succeeded normally, the handoff exited 0, and a round-1 request was
    appended AFTER that lineage's clean closure; a fresh `brief` reported no
    open request. The lock excludes an operation while it is held; only a
    read taken under it is current.

    The pause is a DETERMINISTIC SCHEDULING HOOK immediately before the real
    `acquire` — it changes scheduling and nothing else: the real lock, the
    real preflight, the real emission and the real record all run. The
    competing operation is a REAL CLI SUBPROCESS, because a same-process
    call could not hold the other side of the file lock.

    MUTATION: reuse the pre-acquisition snapshot — delete the `ledger.reload()`
    calls in `cmd_handoff`, `cmd_close` and `cmd_authorize_advance` (or make
    `Ledger.reload` a no-op) — and every interleaving below succeeds into a
    lineage that has already ended.
    """

    PREFIX = "snapshot-"
    START = MAIN_OPENED

    def _hook(self, target, competitor):
        """Patch `target.acquire` so `competitor()` runs to completion the
        first time it is called, then the real acquire proceeds."""
        real = target.acquire
        state = {"fired": False}

        def hook(reservation, *a, **kw):
            if not state["fired"]:
                state["fired"] = True
                state["result"] = competitor()
            return real(reservation, *a, **kw)

        return mock.patch.object(target, "acquire", hook), state

    def _clean_close_subprocess(self, sha, name):
        return lambda: self._run(
            self.repo, "close", "--verdict",
            self._verdict_file(sha, name=name, verdict="clean to advance"))

    def test_a_close_completing_before_acquisition_stops_the_handoff(self):
        first = self._started("first")
        patch, state = self._hook(
            transport.LineageReservation,
            self._clean_close_subprocess(first["sha"], "clean.md"))
        with patch:
            code, rec = self._handoff(self._in_main, "--local-only")
        self.assertEqual(state["result"][0], 0, state["result"])
        self.assertNotEqual(code, 0, rec)
        self.assertEqual(rec["next_kind"], "blocked", rec)
        self.assertIn("closed while this handoff was waiting", rec["error"])
        # And the record shows it: no request lands behind the marker.
        events = self._events()
        closure = max(i for i, e in enumerate(events)
                      if e.get("event") == Ledger.LINEAGE_CLOSED)
        self.assertEqual(
            [e for e in events[closure:] if e.get("event") == "request"], [],
            "a request was appended into an already-closed lineage")
        # A fresh brief agrees with the refusal.
        code, briefed = self._in_main("brief")
        self.assertNotEqual(code, 0, briefed)

    @starts_from(())
    def test_an_opener_that_finished_in_the_interval_is_continued(self):
        """The opening-to-existing transition. The branch's opening lock is
        held while a second opener finishes, and the re-read under it must
        reach the FILE — reading the cached snapshot mints a SECOND lineage
        for one branch, which is the state keying exists to remove."""
        patch, state = self._hook(
            transport.NewLineageReservation,
            lambda: self._run_handoff(self.repo, "--local-only"))
        with patch:
            code, rec = self._handoff(self._in_main, "--local-only")
        self.assertEqual(state["result"][0], 0, state["result"])
        self.assertEqual(code, 0, rec)
        opened = state["result"][1]["lineage"]
        self.assertEqual(rec["lineage"], opened,
                         "the opener that finished under the lock was not "
                         "continued")
        led = Ledger(self.state)
        self.assertEqual(led.lineages(), [opened])

    def test_a_close_completing_before_acquisition_stops_a_second_close(self):
        """The terminal-marker writer, with `--lineage`: two closures for one
        review is exactly the state the marker is supposed to make
        impossible."""
        self._started("first")
        patch, state = self._hook(
            transport.LineageReservation,
            lambda: self._run(self.repo, "close", "--lineage", "--reason",
                              "the competing decision", "--by", "someone"))
        with patch:
            code, rec = self._in_main("close", "--lineage", "--reason",
                                      "this decision", "--by", "another")
        self.assertEqual(state["result"][0], 0, state["result"])
        self.assertNotEqual(code, 0, rec)
        self.assertIn("closed while this close was waiting", rec["error"])
        self.assertEqual(len(self._events(Ledger.LINEAGE_CLOSED)), 1,
                         "a second terminal marker was appended")

    def test_a_close_completing_before_acquisition_stops_an_advance(self):
        """The third terminal-marker writer."""
        self._started("first")
        patch, state = self._hook(
            transport.LineageReservation,
            lambda: self._run(self.repo, "close", "--lineage", "--reason",
                              "the competing decision", "--by", "someone"))
        with patch:
            code, rec = self._in_main("authorize-advance", "--reason",
                                      "advancing anyway", "--by", "a person")
        self.assertEqual(state["result"][0], 0, state["result"])
        self.assertNotEqual(code, 0, rec)
        self.assertIn("closed while this advance was waiting", rec["error"])
        self.assertEqual(len(self._events(Ledger.LINEAGE_CLOSED)), 1)

    # ------------------------------------------------------------- controls

    def test_an_uncontended_handoff_is_unaffected(self):
        """The valid control: nothing competes, and the re-read under the
        reservation changes nothing about an ordinary round."""
        first = self._started("first")
        code, again = self._handoff(self._in_main, "--local-only")
        self.assertEqual(code, 0, again)
        self.assertEqual(again["lineage"], first["lineage"])

    @starts_from(TWO_BRANCHES)
    def test_a_close_of_another_branchs_review_does_not_stop_this_one(self):
        """The different-branch overlap control: two reviews, and the close
        of one completing mid-admission of the other is no reason to refuse
        — that refusal is what keying retired."""
        first, second = self._started("first", "second")
        patch, state = self._hook(
            transport.LineageReservation,
            self._clean_close_subprocess(second["sha"], "clean-other.md"))
        with patch:
            code, rec = self._handoff(self._in_main, "--local-only")
        self.assertEqual(state["result"][0], 0, state["result"])
        self.assertEqual(code, 0, rec)
        self.assertEqual(rec["lineage"], first["lineage"])

class TestPathAndPasteIngressKeepReviewsApart(_TwoWorktrees):
    """Round-2 F5: the reviewer's side, where two reviews became one.

    `take_lineage`'s fallback assigned any previously unseen SHA to the sole
    open lineage when no `git:` reference carried an id. Taking two
    path-carried reviews of two branches into one fresh reviewer state
    therefore exited 0 twice and recorded BOTH under one id: branch
    selection overwritten, requests appearing superseded, and closing one
    review meeting the other branch's unruled request.

    The provenance the ingress was missing is now on the envelope — the
    emitter has always known the id — and a pre-0.20.0 envelope, which
    stamps none, falls back to the AUTHOR BRANCH both ends already record.
    Where neither answers, this refuses rather than adopting.

    An AUTHOR ledger and a SEPARATE REVIEWER ledger, which is the topology
    the finding is about: two machines, two state directories, one pair of
    envelopes crossing between them. In-process through the real CLI, except
    the pasted takes, which are subprocesses so the bytes ride real standard
    input.

    The AUTHOR's side is the start state: both reviews emitted (`INGRESS`,
    or its `paste` and `git` twins), and for the continuation rows a second
    emission of `main`'s review at a new pushed tip (`INGRESS_AGAIN`). The
    author's second emission therefore precedes the reviewer's first take
    in those rows; the two ledgers are separate, the reviewer's sees the
    same takes in the same order, and the first round's commit stays in
    the reviewer's clone, so nothing a take reads differs.

    MUTATION: restore adoption by the sole open lineage — make
    `take_lineage` return `ledger.open_lineages()[0]` whenever exactly one
    is open, before the stamp is consulted — and both independent reviews
    land in one reviewer lineage again.
    """

    PREFIX = "reviewer-ingress-"

    LAYERS = ("other", "origin", "push-other")
    START = INGRESS

    def setUp(self):
        super().setUp()
        #: THE REVIEWER'S OWN LEDGER — a different machine's state.
        self.reviewer = self.tmp / "reviewer-state"

    def _take(self, *argv):
        return run_cli(self.repo, self.reviewer, "take", *argv, "--as",
                       "codex", cwd=self.cwd)

    def _take_pasted(self, envelope: str):
        """`take -`, through a real process, so the bytes ride stdin."""
        proc = subprocess.Popen(
            [sys.executable, "-m", "review", "--ledger-dir",
             str(self.reviewer), "take", "-", "--as", "codex"],
            cwd=str(self.repo), env=cli_process_env(), text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        self.addCleanup(self._reap, proc)
        out, err = proc.communicate(envelope, timeout=120)
        try:
            return proc.returncode, json.loads(out)
        except json.JSONDecodeError:
            return proc.returncode, {"stdout": out, "stderr": err}

    def _reviewer_events(self, kind=None):
        led = Ledger(self.reviewer)
        return [e for e in led.events()
                if kind is None or e.get("event") == kind]

    def _two_handoffs(self):
        """The start's two reviews, `main`'s and the other branch's, on the
        carrier its recipe names."""
        first, second = self._started("first", "second")
        self.assertNotEqual(first["lineage"], second["lineage"])
        return first, second

    @staticmethod
    def _legacy(text: str, drop_branch: bool = False) -> str:
        """The same envelope as a PRE-0.20.0 emitter would have written it."""
        text = re.sub(r' lineage="[^"]*"', "", text, count=1)
        if drop_branch:
            text = re.sub(r' branch="[^"]*"', "", text, count=1)
        return text

    def _bytes(self, rec) -> str:
        return Path(rec["kept"]).read_text(encoding="utf-8")

    def _take_bytes(self, envelope: str, name: str):
        """`take <path>` over envelope bytes this test wrote itself."""
        path = self.tmp / name
        path.write_text(envelope, encoding="utf-8")
        return self._take(str(path))

    # ------------------------------------------------------------ the stamp

    def test_the_request_envelope_stamps_its_lineage_id(self):
        rec, _ = self._two_handoffs()
        self.assertIn(f'lineage="{rec["lineage"]}"', self._bytes(rec))
        parsed = wire.parse_request(self._bytes(rec))
        self.assertEqual(parsed.attrs["lineage"], rec["lineage"])
        self.assertEqual(transport.stamped_lineage(parsed), rec["lineage"])

    def test_a_pre_0_20_0_envelope_carries_none_and_still_reads(self):
        """The upgrade shape: an older emitter stamps no id, and the reader
        meets that absence without a flag day (§3.1)."""
        first, _ = self._two_handoffs()
        legacy = self._legacy(self._bytes(first))
        parsed = wire.parse_request(legacy)
        self.assertNotIn("lineage", parsed.attrs)
        self.assertIsNone(transport.stamped_lineage(parsed))
        self.assertEqual(
            [i.code for i in validate_request(parsed, CFG,
                                              structural_only=True)
             if i.level == "error"], [])

    # ---------------------------------------------- two independent reviews

    def test_two_path_carried_reviews_stay_two_on_the_reviewer_side(self):
        first, second = self._two_handoffs()
        for mine in (first, second):
            code, taken = self._take(mine["kept"])
            self.assertEqual(code, 0, taken)
            self.assertEqual(taken["lineage"], mine["lineage"], taken)
        requests = self._reviewer_events("request")
        self.assertEqual(len(requests), 2, requests)
        self.assertEqual({e["lineage"] for e in requests},
                         {first["lineage"], second["lineage"]})
        self.assertEqual({e["branch"] for e in requests}, {"main", "other"})

    def test_the_reversed_arrival_order_keeps_them_apart_too(self):
        first, second = self._two_handoffs()
        for mine in (second, first):
            code, taken = self._take(mine["kept"])
            self.assertEqual(code, 0, taken)
            self.assertEqual(taken["lineage"], mine["lineage"], taken)
        self.assertEqual(
            {e["lineage"] for e in self._reviewer_events("request")},
            {first["lineage"], second["lineage"]})

    @starts_from(INGRESS_PASTE)
    def test_two_pasted_reviews_stay_two(self):
        """The other carrier that names no reference: the bytes themselves."""
        first, second = self._two_handoffs()
        for mine in (first, second):
            code, taken = self._take_pasted(self._bytes(mine))
            self.assertEqual(code, 0, taken)
            self.assertEqual(taken["lineage"], mine["lineage"], taken)
        self.assertEqual(
            {e["lineage"] for e in self._reviewer_events("request")},
            {first["lineage"], second["lineage"]})

    @starts_from(INGRESS_AGAIN)
    def test_each_review_is_continued_and_closed_independently(self):
        """Both reviews live on the reviewer's ledger at once: a second
        round of one lands in ITS lineage, and closing one leaves the other
        open and readable."""
        first, second = self._two_handoffs()
        for mine in (first, second):
            self.assertEqual(self._take(mine["kept"])[1]["lineage"],
                             mine["lineage"])
        # A second emission of `main`'s review at a new tip (the start's
        # `again`): same review, same id, and the reviewer's take must land
        # in the same lineage.
        again = self._started("again")
        self.assertEqual(again["lineage"], first["lineage"])
        code, taken = self._take(again["kept"])
        self.assertEqual(code, 0, taken)
        self.assertEqual(taken["lineage"], first["lineage"])
        led = Ledger(self.reviewer)
        self.assertEqual(sorted(led.lineages()),
                         sorted([first["lineage"], second["lineage"]]))
        # Closing one on the reviewer's own ledger leaves the other open.
        code, closed = run_cli(self.repo, self.reviewer, "close", "--lineage",
                               "--reason", "done here", "--by", "a person",
                               cwd=self.cwd)
        self.assertEqual(code, 0, closed)
        self.assertEqual(led.reload().open_lineages(),
                         [l for l in (first["lineage"], second["lineage"])
                          if l != closed["lineage"]])

    def test_a_retake_of_one_review_is_idempotent(self):
        first, _ = self._two_handoffs()
        code, once = self._take(first["kept"])
        self.assertEqual(code, 0, once)
        code, twice = self._take(first["kept"])
        self.assertEqual(code, 0, twice)
        self.assertEqual(twice["lineage"], once["lineage"])
        self.assertEqual(len(self._reviewer_events("take")), 1,
                         "a retake recorded a second take event")

    @starts_from(INGRESS_GIT)
    def test_an_explicit_git_reference_still_decides(self):
        """The control the finding leaves untouched: where the argument
        names the review, it is authoritative and nothing else is
        consulted."""
        first, second = self._two_handoffs()
        for mine in (second, first):
            code, taken = self._take(
                transport.round_reference(mine["lineage"], 1))
            self.assertEqual(code, 0, taken)
            self.assertEqual(taken["lineage"], mine["lineage"], taken)

    # ------------------------------------------- the legacy (unstamped) path

    @starts_from(INGRESS_AGAIN)
    def test_a_legacy_envelope_continues_the_review_on_its_branch(self):
        """Sequential continuation, preserved: an envelope with no id lands
        where the review from the same author branch already is."""
        first, _ = self._two_handoffs()
        code, taken = self._take_bytes(self._legacy(self._bytes(first)),
                                       "legacy-1.md")
        self.assertEqual(code, 0, taken)
        opened = taken["lineage"]
        again = self._started("again")
        code, taken = self._take_bytes(self._legacy(self._bytes(again)),
                                       "legacy-2.md")
        self.assertEqual(code, 0, taken)
        self.assertEqual(taken["lineage"], opened,
                         "an unstamped continuation opened a second review")

    def test_a_legacy_envelope_from_another_branch_opens_its_own_review(self):
        """The defect itself, on the legacy path: two unstamped envelopes
        from two author branches are two reviews, not one."""
        first, second = self._two_handoffs()
        code, one = self._take_bytes(self._legacy(self._bytes(first)),
                                     "legacy-main.md")
        self.assertEqual(code, 0, one)
        code, two = self._take_bytes(self._legacy(self._bytes(second)),
                                     "legacy-other.md")
        self.assertEqual(code, 0, two)
        self.assertNotEqual(one["lineage"], two["lineage"])
        self.assertEqual(
            {e["branch"]: e["lineage"]
             for e in self._reviewer_events("request")},
            {"main": one["lineage"], "other": two["lineage"]})

    @starts_from(INGRESS_AGAIN)
    def test_an_unresolvable_legacy_envelope_refuses_rather_than_adopting(self):
        """Neither an id nor a branch, and two reviews open here: there is
        nothing to bind by, so nothing is recorded."""
        first, second = self._two_handoffs()
        for mine, name in ((first, "l1.md"), (second, "l2.md")):
            code, taken = self._take_bytes(self._legacy(self._bytes(mine)),
                                           name)
            self.assertEqual(code, 0, taken)
        before = len(self._reviewer_events())
        again = self._started("again")
        code, refused = self._take_bytes(
            self._legacy(self._bytes(again), drop_branch=True), "l3.md")
        self.assertNotEqual(code, 0, refused)
        self.assertIn("2 reviews are open", refused["error"])
        self.assertEqual(len(self._reviewer_events()), before,
                         "a refused take wrote to the reviewer's ledger")

    @starts_from(INGRESS_AGAIN)
    def test_one_open_review_still_adopts_an_unbranded_legacy_envelope(self):
        """The paired control: with a single open review and nothing to
        distinguish, sequential continuation is exactly what a legacy
        envelope means, and it is preserved."""
        first, _ = self._two_handoffs()
        code, one = self._take_bytes(
            self._legacy(self._bytes(first), drop_branch=True), "u1.md")
        self.assertEqual(code, 0, one)
        again = self._started("again")
        code, two = self._take_bytes(
            self._legacy(self._bytes(again), drop_branch=True), "u2.md")
        self.assertEqual(code, 0, two)
        self.assertEqual(two["lineage"], one["lineage"])

    def _reviewer_files(self) -> dict:
        """Every file of the reviewer's state, by relative path, as bytes:
        what a refused take must leave exactly as it found it."""
        return {str(p.relative_to(self.reviewer)): p.read_bytes()
                for p in sorted(self.reviewer.rglob("*")) if p.is_file()}

    @starts_from(INGRESS_REOPENED)
    def test_a_legacy_envelope_refuses_when_its_branch_has_two_open_reviews(self):
        """Two reviews open on this ledger record the envelope's own author
        branch, and the envelope carries no id: which one it continues
        cannot be derived, so the take refuses and records nothing.

        MUTATION: make `take_lineage` return `mine[0]` however many open
        reviews name the branch, and this row goes red (the take adopts the
        first); its paired control below stays green."""
        first, third, fourth = self._started("first", "third", "fourth")
        self.assertNotEqual(third["lineage"], first["lineage"],
                            "the author's close did not open a new review")
        self.assertEqual(fourth["lineage"], third["lineage"])
        for mine in (first, third):
            code, taken = self._take(mine["kept"])
            self.assertEqual(code, 0, taken)
            self.assertEqual(taken["lineage"], mine["lineage"], taken)
        self.assertEqual(
            sorted(e["branch"] for e in self._reviewer_events("request")),
            ["main", "main"])
        before = self._reviewer_files()
        code, refused = self._take_bytes(self._legacy(self._bytes(fourth)),
                                         "reopened.md")
        self.assertNotEqual(code, 0, refused)
        self.assertIn("2 open reviews on this ledger record branch main",
                      refused["error"])
        self.assertEqual(self._reviewer_files(), before,
                         "a refused take changed the reviewer's state")

    @starts_from(INGRESS_REOPENED)
    def test_one_open_review_on_the_branch_adopts_beside_another_branchs(self):
        """The paired control: the same envelope, and two reviews open here
        as above, but only one of them on the envelope's branch — the legacy
        continuation lands in it."""
        first, second, fourth = self._started("first", "second", "fourth")
        for mine in (first, second):
            code, taken = self._take(mine["kept"])
            self.assertEqual(code, 0, taken)
            self.assertEqual(taken["lineage"], mine["lineage"], taken)
        code, taken = self._take_bytes(self._legacy(self._bytes(fourth)),
                                       "one-on-main.md")
        self.assertEqual(code, 0, taken)
        self.assertEqual(taken["lineage"], first["lineage"], taken)

class TestTheWorktreeTemplateIsCopiedWhole(unittest.TestCase):
    """Every `_TwoWorktrees` start state is built over one template per
    layer set: a main checkout, linked worktrees and a bare origin, all of
    which record absolute paths. Each copy must be a tree of its own — its worktrees linked to
    ITS main checkout, its remote ITS bare repository — with the
    template's commit ids, and nothing written in one copy visible in
    another or in the template.

    MUTATION: skip the path rebase in `copy_fixture` and the pointers
    below name the template, and the push lands in the template's origin.
    """

    def test_each_copy_links_its_own_worktrees_and_its_own_origin(self):
        key, build = _worktree_tree(("other", "same", "origin"))
        copies = [scratch_loop_repo(self, f"wt-copy-{n}-", tree=(key, build))
                  for n in "ab"]
        a, b = copies
        root, facts = fixture_tree(key, build)
        self.assertEqual(len({a.tmp.resolve(), b.tmp.resolve(),
                              root.resolve()}), 3)
        for c in copies:
            self.assertEqual(c.facts, facts)
            self.assertEqual(git_out(c.repo, "rev-parse", "HEAD"),
                             facts["shared"])
            self.assertEqual(git_out(c.repo, "remote", "get-url", "origin"),
                             str(c.tmp / "origin.git"))
            for wt in ("other", "same"):
                common = git_out(c.tmp / wt, "rev-parse",
                                 "--path-format=absolute",
                                 "--git-common-dir")
                self.assertEqual(Path(common).resolve(),
                                 (c.repo / ".git").resolve(), wt)
            listed = git_out(c.repo, "worktree", "list", "--porcelain")
            self.assertNotIn(str(root.resolve()), listed)
            self.assertNotIn(str(root), listed)
        (a.tmp / "other" / "f.txt").write_text("four\n", encoding="utf-8")
        sh("git", "-C", str(a.tmp / "other"), "commit", "-qam", "only in a")
        sh("git", "-C", str(a.tmp / "other"), "push", "-q", "origin", "other")
        moved = git_out(a.tmp / "other", "rev-parse", "HEAD")
        self.assertEqual(git_out(a.tmp / "origin.git", "rev-parse", "other"),
                         moved)
        for untouched in (b.tmp, root):
            self.assertNotEqual(git_out(untouched / "other", "rev-parse",
                                        "HEAD"), moved)
            self.assertNotEqual(
                subprocess.run(["git", "-C", str(untouched / "origin.git"),
                                "rev-parse", "--verify", "-q", "other"],
                               capture_output=True, text=True).stdout.strip(),
                moved)


class TestAStartIsSettledBeforeItMoves(unittest.TestCase):
    """`_start` builds every start state at `_live()` and renames it into
    a template. git's detached maintenance (`git maintenance run --auto
    --detach`, which a push into a bare origin and a commit both start)
    takes `<objects>/maintenance.lock` by its ABSOLUTE path and unlinks
    that path when it finishes (git 2.55 `builtin/gc.c`
    `maintenance_run_tasks`; `tempfile.c` stores the absolute path). A
    template renamed while it runs keeps the lock forever, and every copy
    of it then waits out `MAINTENANCE_SETTLE_S` and refuses: nightly
    37287818489, reproduced on Linux with git 2.55 under CPU load (48 of
    150 push-then-rename builds kept the lock; none when settled first).

    The daemon is simulated here, exactly as git behaves and without the
    race: a step leaves a lock that a thread unlinks by its absolute path
    after the step has returned.

    MUTATION: drop `settle_maintenance(live)` from `_start` and the
    template keeps the lock, which the thread then cannot find.
    """

    HOLD_S = 0.3

    def test_a_lock_released_after_the_step_returns_is_not_kept(self):
        released = []

        def held_lock(live, facts):
            lock = live / "daemon" / "objects" / "maintenance.lock"
            lock.parent.mkdir(parents=True)
            lock.write_bytes(b"")
            path = os.path.abspath(lock)

            def finish():
                time.sleep(self.HOLD_S)
                try:
                    os.unlink(path)
                    released.append(path)
                except FileNotFoundError:
                    pass
            daemon = threading.Thread(target=finish, daemon=True)
            daemon.start()
            self.addCleanup(daemon.join)

        with mock.patch.dict(_STEPS, {"held-lock": held_lock}):
            template, _ = _start(self, False, ("other",), (("held-lock",),))
        self.assertEqual(
            sorted(map(str, template.rglob("maintenance.lock"))), [],
            "the template kept a lock its holder released at the build path")
        self.assertEqual(len(released), 1)


if __name__ == "__main__":
    unittest.main()

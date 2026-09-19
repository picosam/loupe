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

import json
import os
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from review import env_var, transport, vocab, wire
from review.ledger import AmbiguousLineage, Ledger, render_cross_lineage_md
from review.tests._transport_fixtures import (
    CFG, git_out, run_cli, scratch_loop_repo, sh, verdict_text)
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


class _TwoWorktrees(unittest.TestCase):
    """One repository, two branch checkouts, ONE ledger.

    The state directory is shared deliberately and is what a real pair of
    worktrees gets for free: `repo_identity` canonicalises a linked worktree
    back to the main checkout, so both resolve to the same id and the same
    ledger file. `--ledger-dir` makes that literal here rather than relying
    on the machine's real state directory.
    """

    PREFIX = "concurrent-"
    #: Declare the controllable gate in the committed manifest. Off by
    #: default: the sequential classes want no gate at all, and the gate is
    #: committed BEFORE the second worktree branches so both carry it.
    PAUSING_GATE = False

    def setUp(self):
        scratch = scratch_loop_repo(self, self.PREFIX, objective="collision")
        self.tmp, self.repo = scratch.tmp, scratch.repo
        self.base, self.claim = scratch.base, scratch.claim
        self.cwd = scratch.cwd
        self.state = self.tmp / "state"
        self.gates = self.tmp / "gates"
        self.gates.mkdir()
        if self.PAUSING_GATE:
            self._declare_pausing_gate()
        self.other = self.tmp / "other"
        sh("git", "-C", str(self.repo), "worktree", "add", "-q", "-b",
           "other", str(self.other))
        (self.other / "f.txt").write_text("three\n", encoding="utf-8")
        sh("git", "-C", str(self.other), "commit", "-qam", "the other branch")

    def _declare_pausing_gate(self):
        """Append the gate to review.toml and COMMIT it — the manifest that
        runs is the target commit's, never this checkout's working copy."""
        toml = self.repo / "review.toml"
        toml.write_text(
            toml.read_text(encoding="utf-8")
            + _GATE_BLOCK.format(
                script=_GATE_SCRIPT.format(gate_dir=self.gates)),
            encoding="utf-8")
        sh("git", "-C", str(self.repo), "commit", "-qam",
           "the controllable gate")

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
    """

    PREFIX = "keyed-emit-"

    def test_both_handoffs_succeed_with_distinct_lineage_ids(self):
        code, first = self._handoff(self._in_main, "--local-only")
        self.assertEqual(code, 0, first)
        code, second = self._handoff(self._in_other, "--local-only")
        self.assertEqual(code, 0, second)
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
        code, first = self._handoff(self._in_main, "--local-only")
        self.assertEqual(code, 0, first)
        code, second = self._handoff(self._in_other, "--local-only")
        self.assertEqual(code, 0, second)
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
        code, first = self._handoff(self._in_main, "--local-only")
        self.assertEqual(code, 0, first)
        code, second = self._handoff(self._in_other, "--local-only")
        self.assertEqual(code, 0, second)
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

    def test_one_open_lineage_names_no_other(self):
        """The paired control: with one review live, nothing is named."""
        code, only = self._handoff(self._in_main, "--local-only")
        self.assertEqual(code, 0, only)
        code, briefed = self._in_main("brief")
        self.assertEqual(code, 0, briefed)
        self.assertEqual(briefed["open_lineages"], [])

    def test_amend_and_re_emit_still_supersedes_within_one_lineage(self):
        """The flow that makes SHA useless as an axis, unchanged: it stays
        in ITS lineage rather than opening a second one."""
        code, first = self._handoff(self._in_main, "--local-only")
        self.assertEqual(code, 0, first)
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
        code, first = self._handoff(self._in_main, "--local-only")
        self.assertEqual(code, 0, first)
        code, second = self._handoff(self._in_other, "--local-only")
        self.assertEqual(code, 0, second)
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

    def setUp(self):
        super().setUp()
        self.bare = self.tmp / "origin.git"
        sh("git", "init", "-q", "--bare", "-b", "main", str(self.bare))
        sh("git", "-C", str(self.repo), "remote", "add", "origin",
           str(self.bare))
        sh("git", "-C", str(self.repo), "push", "-q", "-u", "origin", "main")

    def _envelope_refs(self):
        return git_out(self.repo, "ls-remote", "origin", "refs/loupe/*")

    def test_both_envelopes_survive_on_distinct_refs(self):
        code, first = self._handoff(self._in_main, "--transport", "git")
        self.assertEqual(code, 0, first)
        code, second = self._handoff(self._in_other, "--transport", "git")
        self.assertEqual(code, 0, second)
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
        code, first = self._handoff(self._in_main, "--transport", "git")
        self.assertEqual(code, 0, first)
        code, second = self._handoff(self._in_other, "--transport", "git")
        self.assertEqual(code, 0, second)
        code, closed = self._in_main(
            "close", "--verdict",
            self._verdict_file(first["sha"], name="clean.md",
                               verdict="clean to advance"))
        self.assertEqual(code, 0, closed)
        cfg = type("C", (), {"repo_root": self.repo})()
        fetched = transport.fetch_envelope(cfg, second["lineage"], 1,
                                           "request")
        self.assertIn(f'sha="{second["sha"]}"', fetched)

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
    and a gate the test controls, because the gates are the interval.
    """

    PREFIX = "keyed-overlap-"
    PAUSING_GATE = True

    def setUp(self):
        super().setUp()
        self.bare = self.tmp / "origin.git"
        sh("git", "init", "-q", "--bare", "-b", "main", str(self.bare))
        sh("git", "-C", str(self.repo), "remote", "add", "origin",
           str(self.bare))
        sh("git", "-C", str(self.repo), "push", "-q", "-u", "origin", "main")

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

    def test_the_lock_files_are_named_by_lineage(self):
        """Two names, which is what makes the exclusion per review."""
        led = Ledger(self.state)
        one = transport.LineageReservation(led, branch="main",
                                           lineage="L0a1b2c3d4e")
        two = transport.LineageReservation(led, branch="other",
                                           lineage="Lfedcba9876")
        self.assertNotEqual(one.path, two.path)
        self.assertEqual(one.path.name, "lineage-L0a1b2c3d4e.lock")

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


class TestALegacyLedgerReadsAsItDidBeforeKeying(_TwoWorktrees):
    """The append-only half, and the one that decides whether this change is
    safe to ship: an UNMIGRATED ledger must read exactly as it always did.

    In-process over a real ledger file: these are reads, and the events are
    written the way a pre-0.20.0 installation wrote them — with no `lineage`
    field at all.
    """

    PREFIX = "keyed-legacy-"

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
    not, when the other lineage is closed, and when there is no other.
    """

    PREFIX = "keyed-notice-"

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

    def _discarded(self):
        """The pre-keying state, built the only way it can now be reached:
        two rounds in ONE lineage, and a close that rules one of them with
        the unruled-request guard disabled."""
        code, first = self._handoff(self._in_main, "--local-only")
        self.assertEqual(code, 0, first)
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
        code, opened = self._handoff(self._in_main, "--local-only")
        self.assertEqual(code, 0, opened)
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
    """

    def setUp(self):
        super().setUp()
        self.same = self.tmp / "same"
        sh("git", "-C", str(self.repo), "worktree", "add", "-q", "-b", "same",
           str(self.same), "HEAD")
        self.shared = git_out(self.repo, "rev-parse", "HEAD")

    def _in_same(self, *argv):
        return run_cli(self.same, self.state, *argv, cwd=self.cwd)

    def _origin(self):
        """A bare remote: the `git` carrier pushes envelopes to it, and a
        declared cross-machine round may not bind an unfetchable SHA."""
        bare = self.tmp / "origin.git"
        sh("git", "init", "-q", "--bare", "-b", "main", str(bare))
        sh("git", "-C", str(self.repo), "remote", "add", "origin", str(bare))
        sh("git", "-C", str(self.repo), "push", "-q", "-u", "origin", "main")

    def _two_reviews(self, first_where, second_where):
        """Two handoffs of the SAME commit, in the given arrival order."""
        code, first = self._handoff(first_where, "--local-only")
        self.assertEqual(code, 0, first)
        code, second = self._handoff(second_where, "--local-only")
        self.assertEqual(code, 0, second)
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
        self._two_requests_of_one_commit(
            *self._two_reviews(self._in_main, self._in_same))

    def test_the_other_arrival_order_opens_two_reviews_as_well(self):
        """Insertion order reversed: the second review recorded is not the
        one an event-order resolver would pick for either verb."""
        self._two_requests_of_one_commit(
            *self._two_reviews(self._in_same, self._in_main))

    def test_a_close_appends_its_closure_to_its_own_review(self):
        """The exact incident: a clean close from `main` landed in the other
        branch's lineage."""
        first, second = self._two_reviews(self._in_main, self._in_same)
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
        first, second = self._two_reviews(self._in_main, self._in_same)
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
        first, second = self._two_reviews(self._in_main, self._in_same)
        for where, mine in ((self._in_main, first), (self._in_same, second)):
            code, briefed = where("brief")
            self.assertEqual(code, 0, briefed)
            self.assertEqual(briefed["lineage"], mine["lineage"])
            self.assertEqual(briefed["sha"], self.shared)
            self.assertIn(f"lineage-{mine['lineage']}", mine["kept"])

    def test_brief_with_a_source_stays_in_the_review_it_was_run_from(self):
        first, second = self._two_reviews(self._in_main, self._in_same)
        for where, mine in ((self._in_main, first), (self._in_same, second)):
            code, briefed = where("brief", mine["kept"])
            self.assertEqual(code, 0, briefed)
            self.assertEqual(briefed["lineage"], mine["lineage"])

    def test_validate_publishes_the_verdict_to_its_own_reviews_ref(self):
        """The verdict PUBLICATION boundary: `validate --from-target` is the
        one place a ruling is pushed, and the ref it goes to is keyed on the
        lineage. Choosing by event order put both branches' verdicts on one
        review's ref."""
        self._origin()
        code, first = self._handoff(self._in_main, "--transport", "git")
        self.assertEqual(code, 0, first)
        code, second = self._handoff(self._in_same, "--transport", "git")
        self.assertEqual(code, 0, second)
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
        first, second = self._two_reviews(self._in_main, self._in_same)
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
        first, second = self._two_reviews(self._in_main, self._in_same)
        code, briefed = self._in_main("brief", second["kept"])
        self.assertEqual(code, 0, briefed)
        self.assertEqual(briefed["lineage"], second["lineage"])
        self.assertIn(second["kept"], briefed["relay"])
        self.assertNotIn(first["kept"], briefed["relay"])

    def test_the_other_arrival_order_briefs_the_stamped_review_too(self):
        first, second = self._two_reviews(self._in_same, self._in_main)
        code, briefed = self._in_same("brief", second["kept"])
        self.assertEqual(code, 0, briefed)
        self.assertEqual(briefed["lineage"], second["lineage"])

    def test_brief_on_stdin_carries_the_stamp_as_a_path_does(self):
        first, second = self._two_reviews(self._in_main, self._in_same)
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
        first, second = self._two_reviews(self._in_main, self._in_same)
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
        first, second = self._two_reviews(self._in_main, self._in_same)
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

    def test_respond_with_the_other_reviews_reference_answers_it(self):
        """`respond --verdict git:B/1` from A's checkout answers B (round-3
        F1: the test that used to carry this name never invoked respond)."""
        self._origin()
        code, first = self._handoff(self._in_main, "--transport", "git")
        self.assertEqual(code, 0, first)
        code, second = self._handoff(self._in_same, "--transport", "git")
        self.assertEqual(code, 0, second)
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

    def _cap_of_one(self):
        """Commit `round_cap = 1` into the manifest the reviews are judged
        by, so round 2 is past the cap and the notice is observable."""
        toml = self.repo / "review.toml"
        text = toml.read_text(encoding="utf-8")
        if re.search(r"^round_cap\s*=.*$", text, re.M):
            text = re.sub(r"^round_cap\s*=.*$", "round_cap = 1", text,
                          count=1, flags=re.M)
        elif "[limits]" in text:
            text = text.replace("[limits]", "[limits]\nround_cap = 1", 1)
        else:
            text += "\n[limits]\nround_cap = 1\n"
        toml.write_text(text, encoding="utf-8")
        sh("git", "-C", str(self.repo), "commit", "-qam", "a cap of one")
        # The commit moved `main`; the fixture's whole point is ONE commit
        # under two reviews, so the second branch follows it and the shared
        # SHA is re-read rather than remembered from setUp.
        sh("git", "-C", str(self.same), "reset", "-q", "--hard", "main")
        self.shared = git_out(self.repo, "rev-parse", "HEAD")
        self.base = git_out(self.repo, "rev-parse", "HEAD~1")

    def _both_reviews_at_round_two(self):
        """Round 1 answered in BOTH reviews, then one new commit that both
        branches point at, and a round-2 request emitted in each.

        The SHA must be under both reviews or the finding is unreachable:
        with a commit only one review has recorded, `recorded_lineage_for_sha`
        answers correctly from the SHA alone and nothing the invocation
        carries is ever consulted.
        """
        first, second = self._two_reviews(self._in_main, self._in_same)
        self._answer_round_one(self._in_main, first["lineage"])
        self._answer_round_one(self._in_same, second["lineage"])
        (self.same / "f.txt").write_text("round two\n", encoding="utf-8")
        sh("git", "-C", str(self.same), "commit", "-qam", "round two")
        sh("git", "-C", str(self.repo), "merge", "-q", "--ff-only", "same")
        self.shared = git_out(self.repo, "rev-parse", "HEAD")
        code, b_two = self._handoff(self._in_same, "--local-only")
        self.assertEqual(code, 0, b_two)
        code, a_two = self._handoff(self._in_main, "--local-only")
        self.assertEqual(code, 0, a_two)
        self.assertEqual(a_two["round"], 2, a_two)
        self.assertEqual(b_two["round"], 2, b_two)
        return first, second, a_two, b_two

    def _answer_round_one(self, where, lineage):
        """A verdict recorded and answered in ONE review, so that review may
        open a round 2."""
        verdict = self._verdict_file(self.shared, name=f"v-{lineage}.md")
        code, closed = where("close", "--verdict", verdict)
        self.assertEqual(code, 0, closed)
        dispositions = self.tmp / f"d-{lineage}.json"
        dispositions.write_text(json.dumps({
            "head": self.shared,
            "dispositions": [{
                "finding_id": "F1", "disposition": "accepted",
                "payload": {"change": "fixed", "verification": "the test",
                            "falsification": {"status": "pass",
                                              "mutation": "fails_without_fix"}}}]}),
            encoding="utf-8")
        code, responded = where(
            "respond", "--verdict", verdict, "--from-json", str(dispositions),
            "--out", str(self.tmp / f"disposition-{lineage}.md"))
        self.assertEqual(code, 0, responded)

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
        self._cap_of_one()
        first, second, _a_two, b_two = self._both_reviews_at_round_two()
        # The human raises the cap for THIS lineage only: B may reach two,
        # A keeps the cap of one.
        code, raised = self._in_same(
            "ledger", "authorize-cap", "--to", "2",
            "--reason", "the falsification's own scenario", "--by", "a person")
        self.assertEqual(code, 0, raised)

        code, judged = self._in_main("validate", b_two["kept"])
        self.assertEqual(code, 0, judged)
        codes = {i["code"] for i in judged.get("items", [])}
        self.assertNotIn(
            "R-BUDGET", codes,
            "B's request was judged against A's cap: the review an envelope "
            "stamps is the one whose limits apply to it")

    def test_the_cap_notice_still_reaches_the_review_that_did_not_raise_it(self):
        """The control the falsification needs: R-BUDGET is reachable, and
        reaches an envelope of the review whose cap was NOT raised — same
        checkout, same commit, same round, different stamped review. A fix
        that silenced the notice everywhere would pass the test above and
        fail this one."""
        self._cap_of_one()
        first, second, a_two, _b_two = self._both_reviews_at_round_two()
        code, raised = self._in_same(
            "ledger", "authorize-cap", "--to", "2",
            "--reason", "the falsification's own scenario", "--by", "a person")
        self.assertEqual(code, 0, raised)
        code, judged = self._in_main("validate", a_two["kept"])
        self.assertEqual(code, 0, judged)
        codes = {i["code"] for i in judged.get("items", [])}
        self.assertIn("R-BUDGET", codes, judged)

    def test_ledger_add_records_into_the_review_it_was_run_from(self):
        first, second = self._two_reviews(self._in_main, self._in_same)
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
        first, second = self._two_reviews(self._in_main, self._in_same)
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

    def test_a_carried_git_reference_stays_authoritative(self):
        """The one carrier that names its review in the argument itself: the
        reference decides, from any checkout."""
        self._origin()
        code, first = self._handoff(self._in_main, "--transport", "git")
        self.assertEqual(code, 0, first)
        code, second = self._handoff(self._in_same, "--transport", "git")
        self.assertEqual(code, 0, second)
        self.assertNotEqual(first["lineage"], second["lineage"])
        for mine in (first, second):
            code, briefed = self._in_main(
                "brief", transport.round_reference(mine["lineage"], 1))
            self.assertEqual(code, 0, briefed)
            self.assertEqual(briefed["lineage"], mine["lineage"])
            self.assertEqual(briefed["sha"], self.shared)

    def test_a_paste_round_of_a_shared_commit_keeps_its_review(self):
        self._origin()
        code, first = self._handoff(self._in_main, "--transport", "paste")
        self.assertEqual(code, 0, first)
        code, second = self._handoff(self._in_same, "--transport", "paste")
        self.assertEqual(code, 0, second)
        self.assertNotEqual(first["lineage"], second["lineage"])
        for where, mine in ((self._in_main, first), (self._in_same, second)):
            code, briefed = where("brief")
            self.assertEqual(code, 0, briefed)
            self.assertEqual(briefed["lineage"], mine["lineage"])

    # ------------------------------------------------------------- controls

    def test_two_distinct_commits_resolve_as_they_always_did(self):
        """The valid control: two reviews of two commits need no
        disambiguation, and every resolver answers exactly as before."""
        code, first = self._handoff(self._in_main, "--local-only")
        self.assertEqual(code, 0, first)
        code, second = self._handoff(self._in_other, "--local-only")
        self.assertEqual(code, 0, second)
        self.assertNotEqual(first["sha"], second["sha"])
        # Resolved from ANY checkout, because one SHA names one review.
        for mine in (first, second):
            code, briefed = self._in_same("brief", mine["kept"])
            self.assertEqual(code, 0, briefed)
            self.assertEqual(briefed["lineage"], mine["lineage"])

    def test_one_review_of_a_commit_needs_nothing_carried(self):
        """The single-match control, from a checkout that names no review."""
        code, only = self._handoff(self._in_main, "--local-only")
        self.assertEqual(code, 0, only)
        code, briefed = self._in_same("brief", only["kept"])
        self.assertEqual(code, 0, briefed)
        self.assertEqual(briefed["lineage"], only["lineage"])

    def test_a_repeated_sha_in_a_closed_and_an_open_review(self):
        """One commit, one branch, TWO reviews in sequence: the first closed
        and historical, the second open. The branch names the open one."""
        code, first = self._handoff(self._in_main, "--local-only")
        self.assertEqual(code, 0, first)
        code, closed = self._in_main(
            "close", "--verdict",
            self._verdict_file(self.shared, name="clean.md",
                               verdict="clean to advance"))
        self.assertEqual(code, 0, closed)
        code, second = self._handoff(self._in_main, "--local-only")
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
        first, second = self._two_reviews(self._in_main, self._in_same)
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

    def setUp(self):
        super().setUp()
        self._origin()
        self.reviewer = self.tmp / "reviewer-state"

    def _take(self, *argv):
        return run_cli(self.repo, self.reviewer, "take", *argv, "--as",
                       "codex", cwd=self.cwd)

    def _reviewer_lineages(self):
        led = Ledger(self.reviewer)
        return [e.get("lineage") for e in led.events()
                if e.get("event") == "take"]

    def _two_git_reviews(self):
        code, first = self._handoff(self._in_main, "--transport", "git")
        self.assertEqual(code, 0, first)
        code, second = self._handoff(self._in_same, "--transport", "git")
        self.assertEqual(code, 0, second)
        return first, second

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
        code, first = self._handoff(self._in_main, "--local-only")
        self.assertEqual(code, 0, first)
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
        code, first = self._handoff(self._in_main, "--local-only")
        self.assertEqual(code, 0, first)
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
        code, first = self._handoff(self._in_main, "--local-only")
        self.assertEqual(code, 0, first)
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
        code, first = self._handoff(self._in_main, "--local-only")
        self.assertEqual(code, 0, first)
        code, again = self._handoff(self._in_main, "--local-only")
        self.assertEqual(code, 0, again)
        self.assertEqual(again["lineage"], first["lineage"])

    def test_a_close_of_another_branchs_review_does_not_stop_this_one(self):
        """The different-branch overlap control: two reviews, and the close
        of one completing mid-admission of the other is no reason to refuse
        — that refusal is what keying retired."""
        code, first = self._handoff(self._in_main, "--local-only")
        self.assertEqual(code, 0, first)
        code, second = self._handoff(self._in_other, "--local-only")
        self.assertEqual(code, 0, second)
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

    MUTATION: restore adoption by the sole open lineage — make
    `take_lineage` return `ledger.open_lineages()[0]` whenever exactly one
    is open, before the stamp is consulted — and both independent reviews
    land in one reviewer lineage again.
    """

    PREFIX = "reviewer-ingress-"

    def setUp(self):
        super().setUp()
        self.bare = self.tmp / "origin.git"
        sh("git", "init", "-q", "--bare", "-b", "main", str(self.bare))
        sh("git", "-C", str(self.repo), "remote", "add", "origin",
           str(self.bare))
        sh("git", "-C", str(self.repo), "push", "-q", "-u", "origin", "main")
        sh("git", "-C", str(self.other), "push", "-q", "-u", "origin", "other")
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

    def _two_handoffs(self, *extra):
        code, first = self._handoff(self._in_main, *extra)
        self.assertEqual(code, 0, first)
        code, second = self._handoff(self._in_other, *extra)
        self.assertEqual(code, 0, second)
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

    def test_two_pasted_reviews_stay_two(self):
        """The other carrier that names no reference: the bytes themselves."""
        first, second = self._two_handoffs("--transport", "paste")
        for mine in (first, second):
            code, taken = self._take_pasted(self._bytes(mine))
            self.assertEqual(code, 0, taken)
            self.assertEqual(taken["lineage"], mine["lineage"], taken)
        self.assertEqual(
            {e["lineage"] for e in self._reviewer_events("request")},
            {first["lineage"], second["lineage"]})

    def test_each_review_is_continued_and_closed_independently(self):
        """Both reviews live on the reviewer's ledger at once: a second
        round of one lands in ITS lineage, and closing one leaves the other
        open and readable."""
        first, second = self._two_handoffs()
        for mine in (first, second):
            self.assertEqual(self._take(mine["kept"])[1]["lineage"],
                             mine["lineage"])
        # A second emission of `main`'s review at a new tip: same review,
        # same id, and the reviewer's take must land in the same lineage.
        (self.repo / "f.txt").write_text("four\n", encoding="utf-8")
        sh("git", "-C", str(self.repo), "commit", "-qam", "more")
        sh("git", "-C", str(self.repo), "push", "-q", "origin", "main")
        code, again = self._handoff(self._in_main)
        self.assertEqual(code, 0, again)
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

    def test_an_explicit_git_reference_still_decides(self):
        """The control the finding leaves untouched: where the argument
        names the review, it is authoritative and nothing else is
        consulted."""
        first, second = self._two_handoffs("--transport", "git")
        for mine in (second, first):
            code, taken = self._take(
                transport.round_reference(mine["lineage"], 1))
            self.assertEqual(code, 0, taken)
            self.assertEqual(taken["lineage"], mine["lineage"], taken)

    # ------------------------------------------- the legacy (unstamped) path

    def test_a_legacy_envelope_continues_the_review_on_its_branch(self):
        """Sequential continuation, preserved: an envelope with no id lands
        where the review from the same author branch already is."""
        first, _ = self._two_handoffs()
        code, taken = self._take_bytes(self._legacy(self._bytes(first)),
                                       "legacy-1.md")
        self.assertEqual(code, 0, taken)
        opened = taken["lineage"]
        (self.repo / "f.txt").write_text("five\n", encoding="utf-8")
        sh("git", "-C", str(self.repo), "commit", "-qam", "again")
        sh("git", "-C", str(self.repo), "push", "-q", "origin", "main")
        code, again = self._handoff(self._in_main)
        self.assertEqual(code, 0, again)
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

    def test_an_unresolvable_legacy_envelope_refuses_rather_than_adopting(self):
        """Neither an id nor a branch, and two reviews open here: there is
        nothing to bind by, so nothing is recorded."""
        first, second = self._two_handoffs()
        for mine, name in ((first, "l1.md"), (second, "l2.md")):
            code, taken = self._take_bytes(self._legacy(self._bytes(mine)),
                                           name)
            self.assertEqual(code, 0, taken)
        before = len(self._reviewer_events())
        (self.repo / "f.txt").write_text("six\n", encoding="utf-8")
        sh("git", "-C", str(self.repo), "commit", "-qam", "yet again")
        sh("git", "-C", str(self.repo), "push", "-q", "origin", "main")
        code, again = self._handoff(self._in_main)
        self.assertEqual(code, 0, again)
        code, refused = self._take_bytes(
            self._legacy(self._bytes(again), drop_branch=True), "l3.md")
        self.assertNotEqual(code, 0, refused)
        self.assertIn("2 reviews are open", refused["error"])
        self.assertEqual(len(self._reviewer_events()), before,
                         "a refused take wrote to the reviewer's ledger")

    def test_one_open_review_still_adopts_an_unbranded_legacy_envelope(self):
        """The paired control: with a single open review and nothing to
        distinguish, sequential continuation is exactly what a legacy
        envelope means, and it is preserved."""
        first, _ = self._two_handoffs()
        code, one = self._take_bytes(
            self._legacy(self._bytes(first), drop_branch=True), "u1.md")
        self.assertEqual(code, 0, one)
        (self.repo / "f.txt").write_text("seven\n", encoding="utf-8")
        sh("git", "-C", str(self.repo), "commit", "-qam", "once more")
        sh("git", "-C", str(self.repo), "push", "-q", "origin", "main")
        code, again = self._handoff(self._in_main)
        self.assertEqual(code, 0, again)
        code, two = self._take_bytes(
            self._legacy(self._bytes(again), drop_branch=True), "u2.md")
        self.assertEqual(code, 0, two)
        self.assertEqual(two["lineage"], one["lineage"])

if __name__ == "__main__":
    unittest.main()

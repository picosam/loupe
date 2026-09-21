"""§9bis.3 / RVW-T9: the transport loop end to end.

One integration class runs the whole loop for real — handoff -> take (second
clone, fetch over a bare path remote) -> verdict -> close -> next handoff —
because a first real execution is a different instrument from unit tests
(slice-1 lesson 3); it self-skips, with the reason stated, where filesystem
writes are denied.

Split from `test_transport.py` 2026-08-31; the classes are verbatim.

Also here, on the same instrument and for the same reason: the request
header lines that REPORT THE ROUND'S OWN STATE — `Round:`, `Base:`, and the
ruling the disposition ledger answers (brief `round-cap-stamp-misreports`).
Each was wrong in a state no unit test reached, because each renders from
the lineage's recorded history and the state that made it wrong is a round
the loop has to actually run to: round 1 of a fresh lineage, round 1 of a
lineage opened after a closed one, a round past the cap, a cap moved by a
recorded override. `_HeaderLoop` drives the real CLI to each of them.
"""

import contextlib
import dataclasses
import json
import math
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from review import config, emit, transport, validate, vocab, wire
from review.emit import _git
from review.ledger import Ledger
from review.tests import synth
from review.tests._transport_fixtures import (
    CFG, run_cli, scratch_loop_repo, sh, verdict_text)
from review.tests.util import LINEAGE

class TestFullLoopIntegration(unittest.TestCase):
    """The first-real-execution instrument: two clones, a bare path remote,
    no network. handoff in the author clone → take in the reviewer clone
    (fetching over the stamped path) → verdict → close → the next handoff
    → lineage close → round 1 again with the repo default cap."""

    def setUp(self):
        scratch = scratch_loop_repo(self, "loop-", name="author",
                                    objective="loop test")
        self.tmp, self.author = scratch.tmp, scratch.repo
        self.base, self.claim, self.cwd = scratch.base, scratch.claim, scratch.cwd
        self.remote = self.tmp / "remote.git"
        self.reviewer = self.tmp / "reviewer"
        # The bare remote names its initial branch too. Without `-b`, HEAD
        # takes git's built-in default (`master`); the author pushes `main`,
        # so the remote's HEAD points at a branch that never exists and
        # `git clone` below yields an EMPTY working tree — no review.toml,
        # so `take` refuses with T-UNDECLARED. Invisible on a machine whose
        # system gitconfig sets init.defaultBranch=main (Apple's git does);
        # the first CI run of this suite (2026-08-17) found it.
        self._sh("git", "init", "-q", "--bare", "-b", "main",
                 str(self.remote))
        self._sh("git", "-C", str(self.author), "remote", "add", "origin",
                 str(self.remote))
        self._sh("git", "-C", str(self.author), "push", "-q", "-u", "origin",
                 "main")
        self._sh("git", "clone", "-q", str(self.remote), str(self.reviewer))
        self.author_state = self.tmp / "state-author"
        self.reviewer_state = self.tmp / "state-reviewer"

    _sh = staticmethod(sh)

    def _run(self, where, state, *argv):
        return run_cli(where, state, *argv, cwd=self.cwd)

    def test_the_loop_end_to_end(self):
        head = _git(self.author, "rev-parse", "HEAD")
        # 1. handoff (author): emits round 1, records, keeps.
        code, rec = self._run(self.author, self.author_state, "handoff",
                              "--claim-file", str(self.claim),
                              "--base", self.base)
        self.assertEqual(code, 0, rec)
        self.assertEqual(rec["sha"], head)
        self.assertFalse(rec["cached"])
        self.assertTrue(Path(rec["kept"]).is_file())
        # 1b. handoff again on the unchanged tip: cached, gates not re-run.
        code, again = self._run(self.author, self.author_state, "handoff",
                                "--claim-file", str(self.claim),
                                "--base", self.base)
        self.assertEqual(code, 0, again)
        self.assertTrue(again["cached"])
        self.assertEqual(again["digest"], rec["digest"])
        # 2. take (reviewer clone): fetches over the stamped path remote.
        #    --as is mandatory since round 1 F4 — the loop no longer runs on a
        #    defaulted identity, so the real path must declare one too.
        code, taken = self._run(self.reviewer, self.reviewer_state, "take",
                                rec["kept"], "--as", "codex")
        self.assertEqual(code, 0, taken)
        self.assertEqual(taken["sha"], head)
        self.assertIn("fetched", taken["target"]["fetch"])
        self.assertEqual(taken["target"]["target"], "present")
        self.assertTrue(taken["references"][0]["status"].startswith("checked"))
        self.assertIn(f"diff {self.base}...{head}", taken["diff"])
        # 2b. taking it as the wrong identity refuses (exit 1, next given).
        code, wrong = self._run(self.reviewer, self.reviewer_state, "take",
                                rec["kept"], "--as", "claude")
        self.assertEqual(code, 1)
        self.assertIn("next", wrong)
        # 3. verdict → close (author): round 1 closed, lineage open.
        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head), encoding="utf-8")
        code, closed = self._run(self.author, self.author_state, "close",
                                 "--verdict", str(v1))
        self.assertEqual(code, 0, closed)
        self.assertEqual(closed["round"], 1)
        self.assertEqual(closed["lineage"], "open")
        # 3b. respond --out (author): the disposition is recorded and kept
        #     without a manual ledger add; the round-2 request then carries it.
        d_json = self.tmp / "d.json"
        d_json.write_text(json.dumps({
            "head": head, "round": 1, "author": "claude",
            "dispositions": [{"finding_id": "F1", "disposition": "accepted",
                              "payload": {"change": "fixed", "verification":
                                          "observed",
                                          "falsification": {
                                              "status": "pass",
                                              "mutation": "fails_without_fix"}
                                          }}]}), encoding="utf-8")
        d_md = self.tmp / "d.md"
        code, resp = self._run(self.author, self.author_state, "respond",
                               "--verdict", str(v1), "--from-json",
                               str(d_json), "--out", str(d_md))
        self.assertEqual(code, 0, resp)
        # The disposition AND its falsification run: the second event is the
        # product-path writer the `stale`/`unverifiable` breakers never had —
        # before it, the only `falsification_run` events in existence were
        # seeded by hand inside test_breakers.
        self.assertEqual(resp["events_added"], 2)
        self.assertTrue(Path(resp["kept"]).is_file())
        runs = [e for e in Ledger(self.author_state).events()
                if e.get("event") == "falsification_run"]
        self.assertEqual(len(runs), 1, runs)
        self.assertEqual(runs[0]["status"], "pass")
        self.assertEqual(runs[0]["mutation"], "fails_without_fix")
        self.assertEqual(runs[0]["source"], "disposition")
        self.assertEqual(runs[0]["round"], 1)
        # 4. next handoff opens round 2 (same tip; not cached, new round).
        code, r2 = self._run(self.author, self.author_state, "handoff",
                             "--claim-file", str(self.claim))
        self.assertEqual(code, 0, r2)
        self.assertEqual(r2["round"], 2)
        self.assertFalse(r2["cached"])
        r2_text = Path(r2["kept"]).read_text(encoding="utf-8")
        self.assertIn("**accepted**", r2_text)
        # ...and the run record beside it, so the reviewer rules on it.
        self.assertIn("[falsification: pass; mutation: fails_without_fix]",
                      r2_text)
        # 5. close the lineage by decision; the next handoff is round 1
        #    of a NEW lineage and needs an explicit base again. Its id is
        #    minted when that handoff runs, so `close` predicts none —
        #    naming a successor would be an invented fact (brief
        #    `keyed-lineage`).
        code, lin = self._run(self.author, self.author_state, "close",
                              "--lineage", "--reason", "loop test done",
                              "--by", "user")
        self.assertEqual(code, 0, lin)
        self.assertTrue(lin["open_request"])
        self.assertIsNone(lin["next_lineage"])
        code, r1b = self._run(self.author, self.author_state, "handoff",
                              "--claim-file", str(self.claim),
                              "--base", self.base)
        self.assertEqual(code, 0, r1b)
        self.assertEqual(r1b["round"], 1)
        self.assertNotEqual(r1b["lineage"], lin["lineage"])
        # The ledger tells the story in order.
        ledger = Ledger(self.author_state)
        kinds = [e["event"] for e in ledger.events()]
        self.assertEqual(kinds.count("request"), 3)
        self.assertEqual(kinds.count("verdict"), 1)
        self.assertEqual(kinds.count("disposition"), 1)
        self.assertEqual(kinds.count(Ledger.LINEAGE_CLOSED), 1)
        # Two reviews, in order: the one the loop ran in is closed, and the
        # one the last handoff opened is the only open one.
        self.assertEqual(len(ledger.lineages()), 2)
        self.assertEqual(ledger.open_lineages(), [r1b["lineage"]])
        self.assertTrue(ledger.is_closed(lin["lineage"]))

    def test_handoff_refuses_when_response_is_omitted(self):
        """FALSIFICATION for sweep F4 (Blocker). The loop, with the author's
        answer skipped: handoff → take → verdict → close → handoff. The
        second handoff must refuse — no request event, no kept envelope, no
        gate run — until every finding of the round-1 verdict has its
        disposition. Mutation: reintroduce the verdict-only transition (call
        `next_round` without the preflight — remove `handoff_preflight`
        from `cmd_handoff`) and the second handoff opens round 2 with an
        empty disposition ledger; this fails on the first assertNotEqual.
        Two controls: the same handoff after `respond` succeeds, and a
        clean verdict — which closes the lineage — is followed by a round-1
        handoff that owes nothing."""
        head = _git(self.author, "rev-parse", "HEAD")
        code, rec = self._run(self.author, self.author_state, "handoff",
                              "--claim-file", str(self.claim),
                              "--base", self.base)
        self.assertEqual(code, 0, rec)
        code, taken = self._run(self.reviewer, self.reviewer_state, "take",
                                rec["kept"], "--as", "codex")
        self.assertEqual(code, 0, taken)
        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head, findings=2), encoding="utf-8")
        code, closed = self._run(self.author, self.author_state, "close",
                                 "--verdict", str(v1))
        self.assertEqual(code, 0, closed)
        before = Ledger(self.author_state).events()
        # The illegal transition: hand off with no response recorded.
        code, refused = self._run(self.author, self.author_state, "handoff",
                                  "--claim-file", str(self.claim))
        self.assertNotEqual(code, 0, refused)
        self.assertEqual(refused["next_kind"], "blocked")
        self.assertIsNone(refused["next"])
        self.assertIn("2 of 2", refused["error"])
        self.assertEqual(Ledger(self.author_state).events(), before,
                         "a refused handoff appends nothing")
        self.assertFalse((Path(self.author_state) / "exchange"
                          / "round-2-request.md").exists())
        # A PARTIAL answer is still owed: one of two.
        d_json = self.tmp / "d.json"
        one = {"head": head, "author": "claude",
               "dispositions": [{"finding_id": "F1", "disposition": "accepted",
                                 "payload": {"change": "fixed",
                                             "verification": "observed",
                                             "falsification": {
                                                 "status": "pass",
                                                 "mutation":
                                                     "fails_without_fix"}}}]}
        d_json.write_text(json.dumps(one), encoding="utf-8")
        code, resp = self._run(self.author, self.author_state, "respond",
                               "--verdict", str(v1), "--from-json",
                               str(d_json), "--out", str(self.tmp / "d.md"))
        # respond itself refuses an incomplete answer (D-INCOMPLETE): the
        # preflight is the second wall, for the case where respond was never
        # run at all — which is exactly what the first refusal above proved.
        self.assertNotEqual(code, 0, resp)
        # Control 1: the complete answer, then the handoff opens round 2.
        two = dict(one)
        two["dispositions"] = one["dispositions"] + [
            dict(one["dispositions"][0], finding_id="F2")]
        d_json.write_text(json.dumps(two), encoding="utf-8")
        code, resp = self._run(self.author, self.author_state, "respond",
                               "--verdict", str(v1), "--from-json",
                               str(d_json), "--out", str(self.tmp / "d.md"))
        self.assertEqual(code, 0, resp)
        code, r2 = self._run(self.author, self.author_state, "handoff",
                             "--claim-file", str(self.claim))
        self.assertEqual(code, 0, r2)
        self.assertEqual(r2["round"], 2)
        # Control 2: a clean verdict closes the lineage; round 1 of the next
        # lineage owes no disposition and opens.
        v2 = self.tmp / "v2.md"
        v2.write_text(verdict_text(sha=head, verdict="clean to advance"),
                      encoding="utf-8")
        code, closed = self._run(self.author, self.author_state, "close",
                                 "--verdict", str(v2))
        self.assertEqual(code, 0, closed)
        code, r1b = self._run(self.author, self.author_state, "handoff",
                              "--claim-file", str(self.claim),
                              "--base", self.base)
        self.assertEqual(code, 0, r1b)
        self.assertEqual(r1b["round"], 1)

    def test_take_recovers_from_an_empty_reviewer_checkout(self):
        """FALSIFICATION for sweep F6 (High). The reviewer's clone has an
        unborn HEAD and an empty working tree — the exact state the first
        CI run produced when the bare remote's default branch never
        existed. `take` must fetch the stamped target, read the target's
        own review.toml, validate against it, check every reference from
        the target tree, and record the take. Mutation: restore pre-fetch
        validation against the reviewer's local config (validate before
        `probe_target`, with `cfg` instead of `target_config`) and this
        empty checkout refuses with T-UNDECLARED — the first assertEqual
        fails. Control: the ordinary clone validates against the target's
        config too, and says so.

        F3, in the same topology: this repository's own committed
        review.toml (the fixture's copy of it) declares `debug`,
        `review_default`, `enforcement`, `round_cap` and `token_budget`,
        and declares no `transport` — the one key no repository default
        governs. `take`'s `decide` must report ONLY `roles.transport`,
        from BOTH checkouts, empty or ordinary: the target's own
        declared keys are the authority a `take` validates against, and
        an authority the request was just validated under cannot also be
        reported as silent about the same keys. Mutation: build `decide`
        from the caller's own `cfg` (`_decide(cfg, …)` in `cmd_take`) and
        the empty checkout — which declares nothing at all — reports the
        five declared keys as undeclared, failing both the exact-set
        assertion and the parity with the ordinary clone.
        """
        # The target declares every optional key except roles.transport —
        # written here, not inherited from whichever config the scratch
        # loop copied (the workbench's declares them; the published
        # example does not).
        import re
        toml_path = self.author / "review.toml"
        toml = toml_path.read_text(encoding="utf-8")
        for key in ("debug", "review_default", "enforcement",
                    "round_cap", "token_budget", "transport"):
            toml = re.sub(rf"^{key}\s*=.*\n", "", toml, flags=re.M)
        toml = toml.replace("[roles]\n", "[roles]\ndebug = false\n"
                            "review_default = \"on\"\nenforcement = \"none\"\n", 1)
        toml += "\n[limits]\nround_cap = 3\ntoken_budget = 200000\n" \
            if "[limits]" not in toml else ""
        toml = re.sub(r"^\[limits\]\n", "[limits]\nround_cap = 3\n"
                      "token_budget = 200000\n", toml, count=1, flags=re.M) \
            if "round_cap" not in toml else toml
        toml_path.write_text(toml, encoding="utf-8")
        self._sh("git", "-C", str(self.author), "commit", "-qam",
                 "declare every optional key but transport")
        head = _git(self.author, "rev-parse", "HEAD")
        code, rec = self._run(self.author, self.author_state, "handoff",
                              "--claim-file", str(self.claim),
                              "--base", self.base)
        self.assertEqual(code, 0, rec)
        # An empty reviewer repository: initialised, nothing checked out,
        # no review.toml anywhere in it.
        empty = self.tmp / "reviewer-empty"
        self._sh("git", "init", "-q", "-b", "main", str(empty))
        self.assertEqual(sorted(x.name for x in empty.iterdir()), [".git"])
        empty_state = self.tmp / "state-empty"
        code, taken = self._run(empty, empty_state, "take", rec["kept"],
                                "--as", "codex")
        self.assertEqual(code, 0, taken)
        self.assertEqual(taken["sha"], head)
        self.assertIn("fetched", taken["target"]["fetch"])
        self.assertTrue(taken["target"]["config"].startswith(
            "target review.toml at"), taken["target"]["config"])
        self.assertTrue(taken["references"][0]["status"].startswith(
            "checked"), taken["references"])
        kinds = [e["event"] for e in Ledger(empty_state).events()]
        self.assertIn("take", kinds)
        # And the working tree was left exactly as found: empty.
        self.assertEqual(sorted(x.name for x in empty.iterdir()), [".git"])
        # F3: the target's declared keys are not reported as decisions —
        # this empty checkout has NOTHING of its own, and still reports
        # only the one key the target genuinely never declares.
        self.assertEqual([e["key"] for e in taken["decide"]],
                         [vocab.DECIDE_TRANSPORT])
        # Control: the ordinary clone, whose checkout DOES carry the same
        # review.toml, is validated against the target's copy as well.
        code, taken2 = self._run(self.reviewer, self.reviewer_state, "take",
                                 rec["kept"], "--as", "codex")
        self.assertEqual(code, 0, taken2)
        self.assertTrue(taken2["target"]["config"].startswith(
            "target review.toml at"))
        # Parity: both checkouts are judged by the same target authority,
        # so both report the same `decide` set — an empty checkout is not
        # a repository with MORE undeclared keys than an ordinary clone of
        # the exact commit under review.
        self.assertEqual([e["key"] for e in taken2["decide"]],
                         [vocab.DECIDE_TRANSPORT])
        # A target that carries no review.toml is governed by the checkout,
        # and the record says so rather than pretending otherwise. Round 3
        # F2: absence is the answer to `ls-tree`, not the answer to a failed
        # read — a target whose config cannot be READ is a refusal, and the
        # sibling rows for that live in TestAuthorityOriginIsClassified.
        rec2, origin = transport.resolve_authority(
            dataclasses.replace(CFG, ledger_dir=None), "0" * 40,
            git=lambda *a: "")
        self.assertEqual(origin, transport.AUTHORITY_EXTERNAL)
        self.assertIn("carries no review.toml", rec2.source)
        self.assertEqual(rec2.taxonomy, CFG.taxonomy)


class _HeaderLoop(unittest.TestCase):
    """A real repository, a real bare remote, a real reviewer clone, and a
    round the real CLI drives as far as a test asks — the instrument the
    request-header classes below share.

    Every finding carries id `F1` (the id grammar is sequential from F1, so
    a round-2 verdict opening at F2 is refused) with a title that names its
    round, so each round's ruling is a DISTINCT fingerprint. That is not
    decoration: a fingerprint re-raised after an `accepted` disposition with
    a passing falsification trips the `stale` breaker, and the next
    hand-off would refuse for a reason that has nothing to do with what
    these tests measure.
    """

    PREFIX = "header-"

    def setUp(self):
        scratch = scratch_loop_repo(self, self.PREFIX, name="author",
                                    objective="header test")
        self.tmp, self.author = scratch.tmp, scratch.repo
        self.base, self.claim, self.cwd = (scratch.base, scratch.claim,
                                           scratch.cwd)
        # The fixture DECLARES its cap; it does not inherit this
        # repository's. The scratch config is a copy of the workbench's
        # review.toml, so "round 4 of 3" restated a configured number and
        # `bin/config-perturbation` went red on it (cap 3 -> 34) the day
        # these classes landed. What they measure is a round past a cap of
        # three, whatever this repository's cap is that day.
        import re
        toml_path = self.author / "review.toml"
        toml, pinned = re.subn(r"^round_cap\s*=.*$", "round_cap = 3",
                               toml_path.read_text(encoding="utf-8"),
                               count=1, flags=re.M)
        self.assertEqual(pinned, 1, "the scratch config declares no round_cap")
        toml_path.write_text(toml, encoding="utf-8")
        sh("git", "-C", str(self.author), "commit", "-q", "--allow-empty",
           "-am", "the fixture declares its own round cap")
        self.remote = self.tmp / "remote.git"
        self.reviewer = self.tmp / "reviewer"
        sh("git", "init", "-q", "--bare", "-b", "main", str(self.remote))
        sh("git", "-C", str(self.author), "remote", "add", "origin",
           str(self.remote))
        sh("git", "-C", str(self.author), "push", "-q", "-u", "origin", "main")
        sh("git", "clone", "-q", str(self.remote), str(self.reviewer))
        self.state = self.tmp / "state-author"
        self.reviewer_state = self.tmp / "state-reviewer"

    def _run(self, *argv, where=None, state=None):
        return run_cli(where or self.author, state or self.state, *argv,
                       cwd=self.cwd)

    def _handoff(self, *extra):
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              *extra)
        self.assertEqual(code, 0, rec)
        rec["text"] = Path(rec["kept"]).read_text(encoding="utf-8")
        return rec

    def _verdict_file(self, sha, round_no):
        path = self.tmp / f"v{round_no}.md"
        path.write_text(
            f'<loupe-review-verdict sha="{sha}">\nVERDICT: changes '
            f'requested\n\n## findings\n\n'
            + synth.finding(1, title=f"the round {round_no} finding",
                            evidence="f.txt:1")
            + "## evidence checked\n\nf.txt\n</loupe-review-verdict>\n",
            encoding="utf-8")
        return path

    def _answer(self, round_no, sha):
        """Close round `round_no` with a verdict and record its answer, so
        the next hand-off may open a round at all."""
        verdict = self._verdict_file(sha, round_no)
        code, closed = self._run("close", "--verdict", str(verdict))
        self.assertEqual(code, 0, closed)
        dispositions = self.tmp / f"d{round_no}.json"
        dispositions.write_text(json.dumps({
            "head": sha, "author": "claude",
            "dispositions": [{
                "finding_id": "F1", "disposition": "accepted",
                "payload": {"change": "fixed", "verification": "observed",
                            "falsification": {
                                "status": "pass",
                                "mutation": "fails_without_fix"}}}]}),
            encoding="utf-8")
        code, resp = self._run("respond", "--verdict", str(verdict),
                               "--from-json", str(dispositions), "--out",
                               str(self.tmp / f"d{round_no}.md"))
        self.assertEqual(code, 0, resp)
        return closed

    def _next_round(self, previous, *extra):
        """Answer `previous` and emit the round after it."""
        self._answer(previous["round"], previous["sha"])
        return self._handoff(*extra)

    def _line(self, text, prefix):
        [line] = [l for l in text.splitlines() if l.startswith(prefix)]
        return line

    def _round_line(self, rec):
        return self._line(rec["text"], "Round:")

    def _base_line(self, rec):
        return self._line(rec["text"], "Base:")

    def _pointer(self, rec):
        """The line the tool writes under the disposition-ledger heading."""
        lines = rec["text"].splitlines()
        i = next(n for n, l in enumerate(lines)
                 if l.startswith("## Disposition ledger"))
        return lines[i + 2]

    def _ledger_heading(self, rec):
        return self._line(rec["text"], "## Disposition ledger")

    def _ledger_body(self, rec):
        """The block itself: heading, blank, pointer, blank, then this."""
        lines = rec["text"].splitlines()
        i = next(n for n, l in enumerate(lines)
                 if l.startswith("## Disposition ledger"))
        return lines[i + 4]


class TestTheRoundLineReportsTheRoundsOwnState(_HeaderLoop):
    """Brief `round-cap-stamp-misreports`.

    The parenthetical switched on ONE axis — whether a ledger override had
    moved the cap — so `(budget breaker fires past the cap)` was printed
    byte-identically on round 1 and on round 4 of 3. The brief's falsifier:
    emit round 1 and a round past the cap in the same lineage and assert the
    two `Round:` lines differ. They did not, and no test noticed.

    The domain is partitioned on both axes at once — round 1 · under the cap
    · at the cap · past the cap × no override · an override that puts the
    round within the cap · an override the round is still past — because
    either axis alone is what the defect was.

    MUTATION: restore the cap-override-only ternary in `emit._round_line`
    and round 1, round 3 of 3 and round 4 of 3 render one parenthetical
    again; `test_the_parenthetical_is_not_a_constant` fails first.
    """

    PREFIX = "roundline-"

    def _drive_to_round_four(self):
        """Rounds 1..4 against the fixture's declared cap of 3."""
        records = [self._handoff("--base", self.base)]
        while records[-1]["round"] < 4:
            records.append(self._next_round(records[-1]))
        self.assertEqual([r["round"] for r in records], [1, 2, 3, 4])
        return records

    def test_the_parenthetical_is_not_a_constant(self):
        r1, r2, r3, r4 = self._drive_to_round_four()
        within = [self._round_line(r) for r in (r1, r2, r3)]
        past = self._round_line(r4)
        # The falsifier, stated as the brief states it.
        self.assertNotEqual(self._round_line(r1), past)
        # …and on the half that used to be constant, not merely on the
        # counts, which always differed.
        def parenthetical(line):
            return line[line.index("("):]
        self.assertNotEqual(parenthetical(within[0]), parenthetical(past))
        # Round 1 equals neither the at-cap nor the past-cap line.
        self.assertNotEqual(within[0], within[2])
        self.assertNotEqual(within[0], past)
        # Control: the three rounds WITHIN the cap share one parenthetical,
        # because they share the state it reports.
        self.assertEqual({parenthetical(l) for l in within},
                         {"(the cap is advisory and this round is within "
                          "it)"})

    def test_the_past_cap_text_says_what_the_tool_actually_does(self):
        r4 = self._drive_to_round_four()[3]
        line = self._round_line(r4)
        self.assertEqual(
            line,
            "Round:  4 of 3 (past the advisory cap: nothing is gated and no "
            "authorization is needed to continue — the tool emits rather "
            "than refusing, and the convergence report under `## Ledger "
            "report` below is what the count was a poor proxy for)")
        # The pointer is to bytes the envelope already carries.
        self.assertIn("## Ledger report", r4["text"])
        self.assertIn("Convergence", r4["text"])

    def test_no_round_line_reads_as_an_event(self):
        records = self._drive_to_round_four()
        for rec in records:
            line = self._round_line(rec)
            for word in ("fires", "breaker", "fired"):
                self.assertNotIn(word, line, line)

    def test_an_override_keeps_its_half_and_still_reports_the_round(self):
        records = self._drive_to_round_four()
        # An override that puts the round WITHIN the cap.
        code, raised = self._run("ledger", "authorize-cap", "--to", "6",
                                 "--reason", "the loop is converging",
                                 "--by", "user")
        self.assertEqual(code, 0, raised)
        r5 = self._next_round(records[-1])
        self.assertEqual(
            self._round_line(r5),
            "Round:  5 of 6 (repo default is 3; this lineage is authorized "
            "to 6 by a recorded ledger override — the cap is advisory and "
            "this round is within it)")
        # An override the round is still PAST: the last recorded override is
        # the one in force, so lowering it puts round 6 outside it.
        code, lowered = self._run("ledger", "authorize-cap", "--to", "4",
                                  "--reason", "narrowed", "--by", "user")
        self.assertEqual(code, 0, lowered)
        r6 = self._next_round(r5)
        self.assertEqual(
            self._round_line(r6),
            "Round:  6 of 4 (repo default is 3; this lineage is authorized "
            "to 4 by a recorded ledger override — past the advisory cap: "
            "nothing is gated and no authorization is needed to continue — "
            "the tool emits rather than refusing, and the convergence "
            "report under `## Ledger report` below is what the count was a "
            "poor proxy for)")

    def test_every_round_still_validates_and_takes(self):
        """The header is envelope text other readers parse; a round past the
        cap must still be a valid request a reviewer can take."""
        records = self._drive_to_round_four()
        for rec in records:
            code, out = self._run("validate", rec["kept"])
            self.assertEqual(code, 0, out)
        past_cap = records[-1]
        code, taken = self._run("take", past_cap["kept"], "--as", "codex",
                                where=self.reviewer,
                                state=self.reviewer_state)
        self.assertEqual(code, 0, taken)
        self.assertEqual(taken["sha"], past_cap["sha"])


class TestTheBaseLineNamesWhatTheBaseActuallyIs(_HeaderLoop):
    """`Base:` printed `(the SHA ruled on in round {round_no - 1})`
    unconditionally, so round 1 of a fresh lineage read "ruled on in round
    0" — an authority, a reviewer and a round that never existed. The base
    of a round-1 request is the author's `--base`; `_emit` refuses without
    one when the lineage holds no verdict.

    MUTATION: make the line unconditional again and round 1 says "ruled on
    in round 0"; the first two tests fail.
    """

    PREFIX = "baseline-"

    def test_round_one_of_a_fresh_lineage_names_the_author_declared_base(self):
        r1 = self._handoff("--base", self.base)
        line = self._base_line(r1)
        self.assertNotIn("round 0", line)
        self.assertEqual(
            line,
            f"Base:   {self.base}   (round 1 opens this lineage: the base "
            f"is the one the author declared, ruled on by no round of this "
            f"lineage; no verdict in this ledger rules on it)")

    def test_a_later_round_keeps_the_meaning_it_had(self):
        r1 = self._handoff("--base", self.base)
        r2 = self._next_round(r1)
        self.assertEqual(self._base_line(r2),
                         f"Base:   {r1['sha']}   (the SHA ruled on in "
                         f"round 1)")

    def test_round_one_after_a_closed_lineage_reports_the_earlier_ruling(self):
        """The common shape: a lineage opens on the tip the previous one
        closed on, so a verdict DID rule on that commit — in another
        lineage. The line says which, and still refuses to call it this
        lineage's."""
        r1 = self._handoff("--base", self.base)
        self._answer(1, r1["sha"])
        code, closed = self._run("close", "--lineage", "--reason", "done",
                                 "--by", "user")
        self.assertEqual(code, 0, closed)
        (self.author / "f.txt").write_text("three\n", encoding="utf-8")
        sh("git", "-C", str(self.author), "commit", "-qam", "more")
        second = self._handoff("--base", r1["sha"])
        self.assertEqual(second["round"], 1)
        line = self._base_line(second)
        self.assertIn("round 1 opens this lineage", line)
        self.assertIn("ruled on by no round of this lineage", line)
        self.assertIn(f"a verdict elsewhere in this ledger did rule on it "
                      f"(lineage {closed['lineage']} round 1)", line)

    def test_the_absence_is_derived_from_the_record_not_assumed(self):
        """Paired control for the clause above: the SAME new lineage, based
        on a commit no verdict ever ruled on, reports the absence."""
        r1 = self._handoff("--base", self.base)
        self._answer(1, r1["sha"])
        code, closed = self._run("close", "--lineage", "--reason", "done",
                                 "--by", "user")
        self.assertEqual(code, 0, closed)
        (self.author / "f.txt").write_text("three\n", encoding="utf-8")
        sh("git", "-C", str(self.author), "commit", "-qam", "more")
        second = self._handoff("--base", self.base)
        self.assertIn("no verdict in this ledger rules on it",
                      self._base_line(second))


class TestTheDispositionLedgerNamesTheRulingItAnswers(_HeaderLoop):
    """The reviewer's standing ask: the disposition ledger listed answers to
    a ruling the envelope never named, so reading the two together meant
    hunting for the verdict by hand.

    The pointer is derived from the RECORD — the round's verdict event
    carries `source_digest`, and the digest is half the retained copy's
    name — never by listing `exchange/`. Retention is best effort, so the
    absence of a kept copy is a state the line reports in words.

    MUTATION: derive the path by globbing
    `exchange/lineage-*/round-N-verdict-*.md` and
    `test_a_missing_kept_copy_is_stated_not_omitted` still finds a path to
    print (the glob matches the other lineage's copy, or nothing, with no
    digest to name).
    """

    PREFIX = "pointer-"

    def test_round_one_says_there_is_no_ruling_to_answer(self):
        r1 = self._handoff("--base", self.base)
        self.assertEqual(self._pointer(r1),
                         "Round 1 opens this lineage: there is no previous "
                         "verdict for these to answer.")

    def test_the_pointer_is_the_recorded_digest_and_its_retained_copy(self):
        r1 = self._handoff("--base", self.base)
        closed = self._answer(1, r1["sha"])
        r2 = self._handoff()
        recorded = [e for e in Ledger(self.state).events()
                    if e.get("event") == "verdict" and e.get("round") == 1]
        digest = recorded[-1]["source_digest"]
        self.assertEqual(digest, closed["digest"])
        kept = transport.exchange_path(
            dataclasses.replace(CFG, ledger_dir=str(self.state)), 1,
            "verdict", lineage=r2["lineage"], digest=digest)
        self.assertTrue(kept.is_file(), kept)
        self.assertEqual(
            self._pointer(r2),
            f"The ruling these answer: the round 1 verdict, sha256 "
            f"`{digest}`, kept at `{kept}` — this round declares transport "
            f"`path`, so that path is one both ends read.")

    def test_a_missing_kept_copy_is_stated_not_omitted(self):
        """A verdict closed from stdin on another machine, or an imported
        legacy round, leaves the digest and no bytes. The line says so and
        still names the digest.

        Two lineages, and only the SECOND one's copy removed — which is
        what makes this the discriminator between a pointer derived from
        the record and one derived from the directory. Lineage A's round-1
        verdict is still on disk under its own `lineage-<id>/` directory,
        so `round-1-verdict-*.md` still matches something; a reader that
        lists rather than composes would print THAT file as the ruling
        lineage B's dispositions answer, which is the lineage-25 round-1 F4
        defect in a second place."""
        first = self._handoff("--base", self.base)
        first_closed = self._answer(1, first["sha"])
        code, ended = self._run("close", "--lineage", "--reason", "done",
                                "--by", "user")
        self.assertEqual(code, 0, ended)
        (self.author / "f.txt").write_text("three\n", encoding="utf-8")
        sh("git", "-C", str(self.author), "commit", "-qam", "more")
        second = self._handoff("--base", first["sha"])
        self.assertEqual(second["round"], 1)
        self.assertNotEqual(second["lineage"], first_closed["lineage"])
        closed = self._answer(1, second["sha"])
        Path(closed["kept"]).unlink()
        # The other lineage's round-1 verdict is untouched, and matches any
        # round-numbered listing of `exchange/`.
        decoy = Path(first_closed["kept"])
        self.assertTrue(decoy.is_file(), decoy)
        r2 = self._handoff()
        self.assertEqual(
            self._pointer(r2),
            f"The ruling these answer: the round 1 verdict, sha256 "
            f"`{closed['digest']}` — no copy of it was kept beside this "
            f"ledger (a verdict closed from stdin on another machine, or an "
            f"imported round), so the digest is the whole pointer.")
        self.assertNotIn(str(decoy), r2["text"])
        self.assertNotIn(first_closed["digest"], r2["text"])

    def _transport_round_two(self, carrier):
        r1 = self._handoff("--base", self.base, "--transport", carrier)
        self._answer(1, r1["sha"])
        return self._pointer(self._handoff("--transport", carrier))

    def test_paste_shares_no_filesystem_and_the_line_says_which_half_it_gives(self):
        """On `paste` the reviewer is handed bytes, not a disk: the path is
        the author's and the digest is what they can check. Paired with the
        `path` round above, which says the opposite because it is true
        there."""
        pointer = self._transport_round_two("paste")
        self.assertIn("the round 1 verdict, sha256 `", pointer)
        self.assertIn("that path is on the author's machine, which this "
                      "round's `paste` transport does not share, so the "
                      "digest is the half you can check.", pointer)

    def test_git_shares_no_filesystem_either(self):
        """The third carrier: the envelope rides a ref both clones fetch,
        which makes the BYTES reachable and the author's `exchange/` path
        no more readable than `paste` does."""
        pointer = self._transport_round_two("git")
        self.assertIn("the round 1 verdict, sha256 `", pointer)
        self.assertIn("that path is on the author's machine, which this "
                      "round's `git` transport does not share, so the "
                      "digest is the half you can check.", pointer)


class TestTheDispositionLedgerNamesNoRoundZero(_HeaderLoop):
    """The leftover the `Base:`/`Round:` commit named and did not take: a
    round-1 request headed its disposition section `## Disposition ledger —
    round 0, emitted by the tool` and filled it with `(no round-0
    dispositions in the ledger)`.

    There is no round 0. Round 1 opens the lineage, so no earlier round
    ruled and there is nothing to dispose of — the same invention the
    `Base:` line was carrying, one section further down, reporting the
    emptiness of a round that never existed.

    The grammar was checked before the text moved, not assumed:
    `validate.REQUIRED_REQUEST_SECTIONS` is taxonomy / claim / evidence /
    contract / reference, and `wire.section_key` reduces a heading to its
    leading word, so the only identity any reader can match on is
    `disposition` — which no production reader asks for. The leading word
    is held here anyway, because the tests that locate the ruling pointer
    do it by that prefix.

    MUTATION: restore `f"## Disposition ledger — round {round_no - 1}"` and
    `test_round_one_heads_the_section_with_a_round_that_exists` fails on
    the heading; restore the unconditional `(no round-{round_no}
    dispositions …)` and the body test fails.
    """

    PREFIX = "dispzero-"

    def test_round_one_heads_the_section_with_a_round_that_exists(self):
        r1 = self._handoff("--base", self.base)
        self.assertEqual(
            self._ledger_heading(r1),
            "## Disposition ledger — round 1 opens this lineage, emitted by "
            "the tool")
        self.assertNotIn("round 0", r1["text"])

    def test_round_one_says_there_is_nothing_to_carry(self):
        r1 = self._handoff("--base", self.base)
        self.assertEqual(
            self._ledger_body(r1),
            "(round 1 opens this lineage: no earlier round ruled, so there "
            "are no dispositions to carry)")
        self.assertNotIn("round-0", r1["text"])

    def test_a_later_round_keeps_the_heading_it_had(self):
        """THE PAIRED CONTROL. Round 2 names round 1, which exists and did
        rule; nothing about that sentence was wrong and nothing moves."""
        r1 = self._handoff("--base", self.base)
        r2 = self._next_round(r1)
        self.assertEqual(
            self._ledger_heading(r2),
            "## Disposition ledger — round 1, emitted by the tool")

    def test_the_headings_identity_is_unchanged_on_every_round(self):
        """What any reader could match on: the leading word. Both rounds
        reduce to `disposition`, as every request before this did."""
        r1 = self._handoff("--base", self.base)
        r2 = self._next_round(r1)
        for rec in (r1, r2):
            heading = self._ledger_heading(rec).removeprefix("## ")
            self.assertEqual(wire.section_key(heading), "disposition")
            self.assertNotIn(
                "disposition",
                validate.REQUIRED_REQUEST_SECTIONS,
                "the section became part of the validated grammar; this "
                "change is free text only while it is not")

    def test_an_empty_ledger_answers_each_round_number_for_itself(self):
        """The block's own domain, below the loop: round 0 (what a round-1
        request asks for), and rounds 1 and 2, which are real rounds whose
        emptiness is an ordinary absence."""
        ledger = Ledger(self.tmp / "empty-state")
        self.assertEqual(
            emit._dispositions_block(ledger, 0, "L0"),
            "(round 1 opens this lineage: no earlier round ruled, so there "
            "are no dispositions to carry)")
        for n in (1, 2):
            self.assertEqual(emit._dispositions_block(ledger, n, "L0"),
                             f"(no round-{n} dispositions in the ledger)")

    def test_round_one_still_validates_and_takes(self):
        r1 = self._handoff("--base", self.base)
        code, out = self._run("validate", r1["kept"])
        self.assertEqual(code, 0, out)
        code, taken = self._run("take", r1["kept"], "--as", "codex",
                                where=self.reviewer,
                                state=self.reviewer_state)
        self.assertEqual(code, 0, taken)


class TestTheSpanListsGeneratedPathsSeparately(unittest.TestCase):
    """Brief `take-objective-map` item 2: a 12,520-line round paid the
    reviewer's full attention on rendered inventories nobody wrote by hand.

    The span now lists a path the TARGET TREE's git attributes mark
    `linguist-generated` under its own heading, so the reviewer skips it by
    rule. Derived from git attributes and not from a `review.toml` key: a
    config key is a cross-installation contract every adopter would have to
    learn, while the attribute is already in the repository, already
    travels with the commit, and already means this on the forge. The
    brief's own proposal — the workbench README's generated column — was
    rejected for the mirror-image reason: that table reaches no other
    installation.

    THE DOMAIN, every case through the real emitter in its own scratch
    repository, because the classification is a git read and a fake one
    would only pin the fake: the attribute bare · absent · explicitly unset
    · `=true` · `=false` · by pattern · by exact path · in a nested
    `.gitattributes` · in the WORKING TREE only (must not count) · in the
    TARGET only (must count) · on a renamed, a deleted and an added path ·
    on a path with a space and non-ASCII bytes · and a repository with no
    `.gitattributes`, which must render the span byte for byte as every
    round before this one did.

    MUTATIONS, each red: read the attributes from the working tree (drop
    `--source=<sha>`) and the working-tree-only case counts one;
    merge the sub-list back into the main listing and the two-list
    assertions fail; drop the suffix and the header stops carrying the
    second total.
    """

    #: The head commit's hand-written half, one line so the arithmetic in
    #: the exact-span assertions stays readable.
    HAND = "hand.py"
    GEN = "gen.json"

    def _commit(self, repo, written, message):
        for name, body in written.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        sh("git", "-C", str(repo), "add", "--", *written)
        sh("git", "-C", str(repo), "commit", "-qm", message)

    def _repo(self, attributes=None, files=None, base_files=None,
              prefix="genspan-"):
        """A scratch repository whose HEAD adds `files` (and, unless it is
        None, a `.gitattributes`) on top of a base that carries
        `base_files` — which is where an attributes file goes when the test
        needs it in force but out of the span."""
        scratch = scratch_loop_repo(self, prefix, name="author",
                                    objective="generated span")
        repo = scratch.repo
        if base_files:
            self._commit(repo, base_files, "base")
        scratch.base = _git(repo, "rev-parse", "HEAD")
        written = dict(files if files is not None else
                       {self.GEN: "[]\n", self.HAND: "one\n"})
        if attributes is not None:
            written[".gitattributes"] = attributes
        self._commit(repo, written, "span")
        return scratch

    def _span(self, scratch, where=None, state="state"):
        """The `What changed` span the REAL emitter renders.

        `where` is the checkout the verb runs in — the repository itself
        unless a case is exercising a linked worktree or a `--shared`
        clone — and `state` names the ledger directory, so a case that
        emits many times does not accumulate rounds in one lineage."""
        code, out = run_cli(where or scratch.repo, scratch.tmp / state,
                            "emit-request", "--claim-file",
                            str(scratch.claim), "--base", scratch.base,
                            "--local-only", "--transport", "path",
                            cwd=scratch.cwd)
        self.assertEqual(code, 0, out)
        start = out.index("What changed (")
        end = out.index("\nDeliberately not done:")
        # 0.25.0: the in-scope count is its own block AFTER the span (it
        # renders whenever a path is generated or excluded); the span this
        # helper returns ends before it. `test_claim_members` owns it.
        scoped = out.find("\n\nIn scope: ", start, end)
        return out[start:scoped + 1 if scoped != -1 else end]

    def _lists(self, span):
        """(header, the ordinary listing, the generated listing)."""
        head, _, rest = span.partition("\n")
        plain, marker, gen = rest.partition(
            "\nGenerated by declaration — these carry")
        rows = [l.strip() for l in plain.splitlines() if l.strip()]
        marked = ([l.strip() for l in gen.splitlines()[2:] if l.strip()]
                  if marker else [])
        return head, rows, marked

    # ------------------------------------------------ the attribute's forms

    FORMS = {
        "bare": ("gen.json linguist-generated\n", True),
        "absent": ("*.txt text\n", False),
        "explicitly unset": ("gen.json -linguist-generated\n", False),
        "=true": ("gen.json linguist-generated=true\n", True),
        "=false": ("gen.json linguist-generated=false\n", False),
        "by pattern": ("*.json linguist-generated\n", True),
        "unrecognised value": ("gen.json linguist-generated=maybe\n", False),
    }

    def test_every_form_of_the_attribute_is_read_as_git_reads_it(self):
        for label, (attributes, expected) in self.FORMS.items():
            with self.subTest(form=label):
                _, rows, marked = self._lists(
                    self._span(self._repo(attributes)))
                self.assertEqual(marked,
                                 ["gen.json   (+1 -0)"] if expected else [])
                self.assertIn(self.HAND, rows)
                self.assertEqual(self.GEN in " ".join(rows), not expected,
                                 "a path belongs to exactly one of the two "
                                 "lists")

    def test_a_nested_gitattributes_is_the_authority_for_its_subtree(self):
        """git resolves attributes per directory; the span reads them
        through git rather than parsing the root file itself, so this works
        for the same reason the root case does."""
        scratch = self._repo(None, files={
            "sub/deep.json": "[]\n", self.HAND: "one\n",
            "sub/.gitattributes": "deep.json linguist-generated\n"})
        _, rows, marked = self._lists(self._span(scratch))
        self.assertEqual(marked, ["sub/deep.json   (+1 -0)"])
        self.assertNotIn("sub/deep.json", " ".join(rows))

    # -------------------------------------------- which tree is the authority

    def _working_tree_says(self, repo, path):
        """What `check-attr` answers with no `--source` — this checkout's
        own authority, which is what the read must NOT be using."""
        out = _git(repo, "check-attr", "-z", emit.GENERATED_ATTR, "--", path)
        return out.split("\0")[2]

    # These two do not go through `emit-request`, and the reason is a fact
    # about the verb rather than a convenience: the author-side emitter
    # SWEEPS the working tree into the target before it emits, so by the
    # time it computes a span its checkout and its target agree by
    # construction and the divergence is unreachable there. The axis is
    # still real — `emit_request` is a library entry point, and lineage 12
    # round 3 F1 found three reads that had drifted to the local graph — so
    # it is exercised where it exists, on the production function, against
    # a real repository built to make the two authorities DISAGREE. Each
    # asserts that disagreement first; without it the case would pass
    # whichever tree the code read.

    def test_the_working_tree_alone_does_not_make_a_path_generated(self):
        scratch = self._repo(None)
        target = _git(scratch.repo, "rev-parse", "HEAD")
        (scratch.repo / ".gitattributes").write_text(
            "gen.json linguist-generated\n", encoding="utf-8")
        self.assertEqual(self._working_tree_says(scratch.repo, self.GEN),
                         "set")
        self.assertEqual(
            emit.generated_at_target(scratch.repo, target,
                                     [self.GEN, self.HAND]), set())

    def test_the_target_still_decides_when_the_working_tree_has_dropped_it(
            self):
        """THE PAIRED CONTROL, the other direction: the attribute is in the
        commit and gone from the checkout, and the commit still wins."""
        scratch = self._repo("gen.json linguist-generated\n")
        target = _git(scratch.repo, "rev-parse", "HEAD")
        # From the index too: with the file merely deleted on disk, git
        # falls back to the staged copy and this checkout would still
        # answer `set`, which would make the fixture agree with itself and
        # prove nothing.
        sh("git", "-C", str(scratch.repo), "rm", "-q", "--", ".gitattributes")
        self.assertEqual(self._working_tree_says(scratch.repo, self.GEN),
                         "unspecified")
        self.assertEqual(
            emit.generated_at_target(scratch.repo, target,
                                     [self.GEN, self.HAND]), {self.GEN})

    # ------------------------------------------------- what the diff can do

    def test_an_added_a_deleted_and_a_renamed_generated_path(self):
        """Three shapes the diff reports differently, one repository. The
        rename is the one the display spelling cannot answer: `--numstat`
        renders it `{old.json => new.json}`, which is not a path, so the
        classification runs on the `-z` read's post-image."""
        scratch = scratch_loop_repo(self, "genspan-shapes-", name="author",
                                    objective="generated span")
        repo = scratch.repo
        # EXACT paths, not `*.json`: a pattern would match the composite
        # spelling `old.json => new.json` by accident, and a fixture that
        # passes for the wrong reason proves the wrong thing.
        attrs = "".join(f"{n}.json linguist-generated\n"
                        for n in ("new", "gone", "added"))
        self._commit(repo, {"old.json": "old\n", "gone.json": "gone\n",
                            ".gitattributes": attrs}, "before")
        scratch.base = _git(repo, "rev-parse", "HEAD")
        sh("git", "-C", str(repo), "mv", "old.json", "new.json")
        sh("git", "-C", str(repo), "rm", "-q", "--", "gone.json")
        self._commit(repo, {"added.json": "added\n", self.HAND: "one\n"},
                     "shapes")
        _, rows, marked = self._lists(self._span(scratch))
        self.assertEqual(rows, [self.HAND])
        self.assertEqual(
            sorted(marked),
            ["added.json   (+1 -0)", "gone.json   (+0 -1)",
             "old.json => new.json   (+0 -0)"])

    def test_a_path_with_a_space_and_non_ascii_bytes(self):
        """`--numstat` C-quotes such a path (`"caf\\303\\251 x.json"`), so
        the spelling the envelope prints cannot be handed to git. The
        display keeps that spelling and the classification uses the raw
        one."""
        name = "café x.json"
        scratch = self._repo(
            None, files={name: "[]\n", self.HAND: "one\n"},
            base_files={".gitattributes": "*.json linguist-generated\n"})
        _, rows, marked = self._lists(self._span(scratch))
        self.assertEqual(rows, [self.HAND])
        self.assertEqual(marked, ['"caf\\303\\251 x.json"   (+1 -0)'])

    # ------------------------------------------------------ the two renderings

    def test_a_repository_that_marks_nothing_renders_the_old_span(self):
        """BYTE-IDENTICAL, asserted as the literal the emitter produced
        before any of this existed: no suffix, no heading, one list in the
        order `--numstat` returned it."""
        self.assertEqual(
            self._span(self._repo(None)),
            "What changed (2 files, 2 insertions, 0 deletions = 2 changed "
            "lines, 1 areas, spanning 1 commit — machine-computed):\n"
            "\n"
            "  gen.json\n"
            "  hand.py\n")

    def test_the_marked_span_states_both_totals_and_both_lists(self):
        """The whole rendering, for one small example. The totals count
        EVERY path — the span is what the round touched — and the suffix
        carries the other total beside it rather than instead of it."""
        self.assertEqual(
            self._span(self._repo("gen.json linguist-generated\n")),
            "What changed (3 files, 3 insertions, 0 deletions = 3 changed "
            "lines, 1 areas, spanning 1 commit — machine-computed; 1 of "
            "those paths carries `linguist-generated` in the target tree's "
            "attributes, leaving 2 files and 2 changed lines that do "
            "not):\n"
            "\n"
            "  .gitattributes\n"
            "  hand.py\n"
            "\n"
            "Generated by declaration — these carry `linguist-generated` "
            "in the target tree's attributes, so a reviewer may skip "
            "them by rule rather than by inspection:\n"
            "\n"
            "  gen.json   (+1 -0)\n")

    def test_a_span_that_is_all_generated_says_so_rather_than_nothing(self):
        """An empty ordinary list would read as "nothing changed", which is
        the opposite of what happened."""
        scratch = self._repo(
            None, files={self.GEN: "[]\n", "two.json": "[]\n"},
            base_files={".gitattributes": "*.json linguist-generated\n"})
        _, rows, marked = self._lists(self._span(scratch))
        self.assertEqual(
            rows, ["(none — every changed path is generated by declaration)"])
        self.assertEqual(sorted(marked),
                         ["gen.json   (+1 -0)", "two.json   (+1 -0)"])

    # ------------------------- the machine-local sources (round 1 F1)

    # `--source=<sha>` selects which TREE supplies the TRACKED
    # `.gitattributes`. It does not isolate the REST of git's attribute
    # lookup, and round 1 F1 showed what that costs: a local override
    # promoted a handwritten path to "generated, skip by rule" under a
    # heading saying the target tree declared it. Measured on git 2.54.0,
    # four sources reach the answer — `$GIT_DIR/info/attributes` (above
    # the tree), `core.attributesFile` wherever configured, its default
    # user file, and the system file.
    #
    # THE PARTITION, every cell through the real emitter on a real
    # repository: tracked declaration kind × local source × local intent.
    # The target never moves inside a cell, so the two renderings are
    # comparable byte for byte — both lists and both totals, since the
    # suffix carries the non-generated totals and the sub-list carries the
    # generated ones.

    ODD = "café x.json"
    DEEP = "sub/deep.json"

    #: Tracked declaration kind -> (the files HEAD adds, whether the
    #: target declares anything). The FILE SET never varies, so a cell
    #: differs from its reference only in where the declaration lives.
    TRACKED = {
        "root rule": (
            {"hand.py": "one\n", "gen.json": "[]\n", "sub/deep.json": "[]\n",
             "café x.json": "[]\n",
             ".gitattributes": ("gen.json linguist-generated\n"
                                "caf* linguist-generated\n")},
            True),
        "nested .gitattributes": (
            {"hand.py": "one\n", "gen.json": "[]\n", "sub/deep.json": "[]\n",
             "café x.json": "[]\n",
             "sub/.gitattributes": "deep.json linguist-generated\n"},
            True),
        "macro": (
            {"hand.py": "one\n", "gen.json": "[]\n", "sub/deep.json": "[]\n",
             "café x.json": "[]\n",
             ".gitattributes": ("[attr]robot linguist-generated\n"
                                "gen.json robot\n")},
            True),
        "no declaration": (
            {"hand.py": "one\n", "gen.json": "[]\n", "sub/deep.json": "[]\n",
             "café x.json": "[]\n"},
            False),
    }

    #: What a local source TRIES to do. Suppression names every path any
    #: tracked kind declares, so one rules file is maximally hostile to
    #: all four; `caf*` rather than the spelt name because a pattern
    #: cannot carry a space.
    INTENTS = {
        "promote an unmarked path": "hand.py linguist-generated\n",
        "suppress with -": ("gen.json -linguist-generated\n"
                            "caf* -linguist-generated\n"
                            "sub/deep.json -linguist-generated\n"),
        "suppress with !": ("gen.json !linguist-generated\n"
                            "caf* !linguist-generated\n"
                            "sub/deep.json !linguist-generated\n"),
    }

    @contextlib.contextmanager
    def _environment(self, **named):
        """`os.environ` with exactly these names set — and any whose value
        is None REMOVED, which is how a case says a variable is unset.

        `run_cli` derives the child environment from `os.environ`, and so
        does `config.caller_env` under it, so this is the only place a
        case can put a machine-local setting. Every case here injects its
        own HOME and XDG_CONFIG_HOME: the developer's real user attributes
        file must not decide a result, in either direction.

        `also=` carries a source's own overrides and WINS over the named
        arguments beside it, which is how `~/.config/git/attributes` says
        XDG_CONFIG_HOME is unset while the caller is still setting it."""
        overrides = named.pop("also", None) or {}
        names = {**named, **overrides}
        env = {k: v for k, v in os.environ.items() if k not in names}
        env.update({k: v for k, v in names.items() if v is not None})
        with mock.patch.dict(os.environ, env, clear=True):
            yield

    def _write(self, path: Path, text: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return lambda: path.unlink()

    def _sources(self, repo, tmp, home, xdg):
        """Every machine-local attribute source, as label -> install.

        An installer takes the rules and returns (environment overrides,
        undo), so a cell leaves the repository as it found it."""
        user = tmp / "user-attributes"

        def info_attributes(rules):
            return {}, self._write(repo / ".git" / "info" / "attributes",
                                   rules)

        def local_config(rules):
            undo = self._write(user, rules)
            sh("git", "-C", str(repo), "config", "core.attributesFile",
               str(user))

            def back():
                sh("git", "-C", str(repo), "config", "--unset",
                   "core.attributesFile")
                undo()
            return {}, back

        def global_config(rules):
            undo = self._write(user, rules)
            undo_cfg = self._write(home / ".gitconfig",
                                   f"[core]\n\tattributesFile = {user}\n")
            return {}, lambda: (undo(), undo_cfg())

        def xdg_default(rules):
            return {}, self._write(xdg / "git" / "attributes", rules)

        def home_default(rules):
            # The `~/.config` default applies only where XDG_CONFIG_HOME
            # is UNSET; with it set, git never looks here at all, and a
            # case that left it set would pass without testing anything.
            return ({"XDG_CONFIG_HOME": None},
                    self._write(home / ".config" / "git" / "attributes",
                                rules))

        return {"$GIT_DIR/info/attributes": info_attributes,
                "core.attributesFile, local config": local_config,
                "core.attributesFile, global config": global_config,
                "$XDG_CONFIG_HOME/git/attributes": xdg_default,
                "~/.config/git/attributes": home_default}

    def test_no_machine_local_attribute_source_reaches_the_answer(self):
        """THE PARTITION. At an unchanged target the whole span is
        identical with each local source present and absent.

        MUTATION: restore the unrestricted `check-attr` — every cell of
        the info and `core.attributesFile` rows fails, in both intents.
        """
        cell = 0
        for kind, (files, declares) in self.TRACKED.items():
            scratch = self._repo(None, files=files, prefix="genspan-local-")
            home, xdg = scratch.tmp / "home", scratch.tmp / "xdg"
            home.mkdir()
            xdg.mkdir()
            head = _git(scratch.repo, "rev-parse", "HEAD")
            with self._environment(HOME=str(home), XDG_CONFIG_HOME=str(xdg)):
                reference = self._span(scratch, state="reference")
            _, rows, marked = self._lists(reference)
            # PAIRED CONTROL on the reference itself: a kind that declares
            # nothing must mark nothing, and one that declares must mark —
            # otherwise every cell below would agree with a vacant answer.
            self.assertEqual(bool(marked), declares, kind)
            self.assertIn(self.HAND, " ".join(rows),
                          "the handwritten path is ordinary at the target")
            for label, install in self._sources(
                    scratch.repo, scratch.tmp, home, xdg).items():
                for intent, rules in self.INTENTS.items():
                    cell += 1
                    with self.subTest(declared=kind, source=label,
                                      intent=intent):
                        overrides, undo = install(rules)
                        try:
                            with self._environment(HOME=str(home),
                                                   XDG_CONFIG_HOME=str(xdg),
                                                   also=overrides):
                                got = self._span(scratch, state=f"s{cell}")
                        finally:
                            undo()
                        self.assertEqual(
                            _git(scratch.repo, "rev-parse", "HEAD"), head,
                            "the target moved, so the two spans are not "
                            "comparable")
                        self.assertEqual(got, reference)

    def test_a_local_source_cannot_reach_it_from_a_linked_worktree(self):
        """`info/attributes` lives in the COMMON directory, so a linked
        worktree inherits its parent's machine-local source — and the
        object store it must borrow is `--git-path objects`, not an
        assumed `<repo>/.git/objects`."""
        scratch = self._repo("gen.json linguist-generated\n",
                             prefix="genspan-worktree-")
        home, xdg = scratch.tmp / "home", scratch.tmp / "xdg"
        home.mkdir()
        xdg.mkdir()
        linked = scratch.tmp / "linked"
        sh("git", "-C", str(scratch.repo), "worktree", "add", "-q", "-b",
           "linked", str(linked), "HEAD")
        with self._environment(HOME=str(home), XDG_CONFIG_HOME=str(xdg)):
            reference = self._span(scratch, where=linked, state="wt-ref")
        _, _, marked = self._lists(reference)
        self.assertEqual(marked, ["gen.json   (+1 -0)"])
        self._write(scratch.repo / ".git" / "info" / "attributes",
                    self.INTENTS["promote an unmarked path"]
                    + self.INTENTS["suppress with -"])
        with self._environment(HOME=str(home), XDG_CONFIG_HOME=str(xdg)):
            self.assertEqual(self._span(scratch, where=linked, state="wt"),
                             reference)

    def test_an_object_store_that_has_its_own_alternates(self):
        """A `--shared` clone: the store this reader borrows borrows one
        of its own, and the target's tree has to resolve through both."""
        scratch = self._repo("gen.json linguist-generated\n",
                             prefix="genspan-shared-")
        home, xdg = scratch.tmp / "home", scratch.tmp / "xdg"
        home.mkdir()
        xdg.mkdir()
        clone = scratch.tmp / "shared-clone"
        sh("git", "clone", "-q", "--shared", str(scratch.repo), str(clone))
        # `--local-only` refuses where a fetchable remote exists, and the
        # clone's `origin` is the repository beside it; the case is about
        # the object store, not the topology.
        sh("git", "-C", str(clone), "remote", "remove", "origin")
        for k, v in (("user.name", "a"), ("user.email", "a@example.invalid"),
                     ("commit.gpgsign", "false")):
            sh("git", "-C", str(clone), "config", k, v)
        self.assertTrue(
            (clone / ".git" / "objects" / "info" / "alternates").is_file(),
            "the fixture is not a shared clone, so it proves nothing")
        with self._environment(HOME=str(home), XDG_CONFIG_HOME=str(xdg)):
            reference = self._span(scratch, where=clone, state="alt-ref")
        _, _, marked = self._lists(reference)
        self.assertEqual(marked, ["gen.json   (+1 -0)"])
        self._write(clone / ".git" / "info" / "attributes",
                    self.INTENTS["promote an unmarked path"]
                    + self.INTENTS["suppress with -"])
        with self._environment(HOME=str(home), XDG_CONFIG_HOME=str(xdg)):
            self.assertEqual(self._span(scratch, where=clone, state="alt"),
                             reference)

    def _check_attr_calls(self, call):
        """(argv, env) for every `check-attr` the production reader
        spawned while `call` ran — the real subprocess still runs, so the
        answer is measured beside the invocation that produced it."""
        real, seen = subprocess.run, []

        def record(argv, **kw):
            if "check-attr" in list(argv):
                seen.append((list(argv), dict(kw.get("env") or {})))
            return real(argv, **kw)
        with mock.patch("subprocess.run", record):
            answer = call()
        return answer, seen

    def test_the_system_file_is_refused_in_the_spawned_environment(self):
        """The system attributes file is `$(prefix)/etc/gitattributes` and
        NOTHING relocates it — `GIT_CONFIG_SYSTEM` moves the system
        CONFIG, not this — so simulating one means writing inside the git
        installation's own prefix, which needs root. That source is
        therefore measured at the door instead: every `check-attr` this
        reader spawns carries `GIT_ATTR_NOSYSTEM=1`.

        And carries no OTHER `GIT_*` name, which is not tidiness:
        `GIT_CONFIG_COUNT`/`KEY`/`VALUE` outranks repository config and
        re-opens the door the scratch directory closes (measured), while
        `GIT_DIR` and `GIT_COMMON_DIR` would move the directory itself.
        The hostile ambient values below are the paired control — with
        them inherited, `plain.py` comes back marked."""
        scratch = self._repo("gen.json linguist-generated\n",
                             prefix="genspan-env-")
        home, xdg = scratch.tmp / "home", scratch.tmp / "xdg"
        home.mkdir()
        xdg.mkdir()
        hostile = scratch.tmp / "hostile-attributes"
        hostile.write_text("hand.py linguist-generated\n", encoding="utf-8")
        head = _git(scratch.repo, "rev-parse", "HEAD")
        with self._environment(HOME=str(home), XDG_CONFIG_HOME=str(xdg),
                               GIT_CONFIG_COUNT="1",
                               GIT_CONFIG_KEY_0="core.attributesFile",
                               GIT_CONFIG_VALUE_0=str(hostile),
                               GIT_ATTR_NOSYSTEM="0"):
            answer, calls = self._check_attr_calls(
                lambda: emit.generated_at_target(
                    scratch.repo, head, [self.GEN, self.HAND]))
        self.assertEqual(answer, {self.GEN},
                         "an inherited GIT_CONFIG_KEY decided the answer")
        self.assertTrue(calls, "no check-attr was spawned at all")
        for argv, env in calls:
            self.assertEqual(env.get("GIT_ATTR_NOSYSTEM"), "1")
            self.assertEqual({k for k in env if k.startswith("GIT_")},
                             {"GIT_ATTR_NOSYSTEM"})
            self.assertIn("--no-replace-objects", argv)
            self.assertTrue(any(w.startswith("--git-dir=") for w in argv),
                            argv)

    def test_the_lookup_is_one_subprocess_per_chunk_not_per_path(self):
        """A 12,000-line round touched 173 paths; the isolation must not
        turn the bounded argv into a spawn per path."""
        scratch = self._repo("gen.json linguist-generated\n",
                             prefix="genspan-chunk-")
        head = _git(scratch.repo, "rev-parse", "HEAD")
        many = [f"p{i}.json" for i in range(emit._ATTR_CHUNK * 2 + 1)]
        home, xdg = scratch.tmp / "home", scratch.tmp / "xdg"
        home.mkdir()
        xdg.mkdir()
        with self._environment(HOME=str(home), XDG_CONFIG_HOME=str(xdg)):
            _, calls = self._check_attr_calls(
                lambda: emit.generated_at_target(scratch.repo, head, many))
        self.assertEqual(len(calls),
                         math.ceil(len(many) / emit._ATTR_CHUNK))

    def test_isolation_that_cannot_be_established_marks_nothing(self):
        """NO THIRD STATE. Where the isolation cannot be built, every path
        stays in the ordinary list and the span says nothing about
        generated paths — which is byte for byte the rendering of every
        round before any of this existed.

        MUTATION: fall back to the unrestricted `check-attr` instead of to
        silence, and each case here fails — the first two because a
        partial answer marks `gen.json`, the rendering because the heading
        returns."""
        scratch = self._repo("gen.json linguist-generated\n",
                             prefix="genspan-fallback-")
        head = _git(scratch.repo, "rev-parse", "HEAD")
        home, xdg = scratch.tmp / "home", scratch.tmp / "xdg"
        home.mkdir()
        xdg.mkdir()
        both = [self.GEN, self.HAND]
        with self._environment(HOME=str(home), XDG_CONFIG_HOME=str(xdg)):
            self.assertEqual(
                emit.generated_at_target(scratch.repo, head, both),
                {self.GEN},
                "the control: with the isolation intact it classifies")
        failures = {
            "no scratch directory": mock.patch(
                "tempfile.mkdtemp", side_effect=OSError("denied")),
            "the isolation cannot be built": mock.patch.object(
                emit, "_isolated_attr_dir",
                side_effect=RuntimeError("no object store")),
        }
        for label, patched in failures.items():
            with self.subTest(failure=label):
                with self._environment(HOME=str(home),
                                       XDG_CONFIG_HOME=str(xdg)), patched:
                    self.assertEqual(
                        emit.generated_at_target(scratch.repo, head, both),
                        set())
        with self._environment(HOME=str(home), XDG_CONFIG_HOME=str(xdg)), \
                mock.patch.object(emit, "_isolated_attr_dir",
                                  side_effect=RuntimeError("no store")):
            self.assertEqual(
                self._span(scratch, state="fallback"),
                "What changed (3 files, 3 insertions, 0 deletions = 3 "
                "changed lines, 1 areas, spanning 1 commit — "
                "machine-computed):\n"
                "\n"
                "  .gitattributes\n"
                "  gen.json\n"
                "  hand.py\n")

    def test_every_emitted_request_still_validates(self):
        for attributes in (None, "gen.json linguist-generated\n"):
            with self.subTest(attributes=attributes):
                scratch = self._repo(attributes)
                out = self._span(scratch)
                self.assertIn("What changed", out)
                envelope = scratch.tmp / "request.md"
                code, text = run_cli(
                    scratch.repo, scratch.tmp / "state-v", "emit-request",
                    "--claim-file", str(scratch.claim), "--base",
                    scratch.base, "--local-only", "--transport", "path",
                    "--out", str(envelope), cwd=scratch.cwd)
                self.assertEqual(code, 0, text)
                code, items = run_cli(scratch.repo, scratch.tmp / "state-v",
                                      "validate", str(envelope),
                                      cwd=scratch.cwd)
                self.assertEqual(code, 0, items)


class TestTheScopeReportReachesTheAuthor(unittest.TestCase):
    """Round-9 F4, at the verb (`claim-scope-consistency-check`).

    The comparison is worth nothing in a function nobody calls: both author
    doors measure the span already — the envelope renders it as `What
    changed` — so the report rides the same measurement out to the author,
    as a `scope` field an agent relays and a notice line a human reads. It
    never changes the exit: `handoff` succeeds either way, which is what
    non-blocking means here.

    MUTATION: drop the `scope_items` call from `_emit` and the round
    completes with the claim and the span still disagreeing, exactly as
    round 9 did.
    """

    def setUp(self):
        scratch = scratch_loop_repo(self, "scope-", name="repo",
                                    objective="scope test")
        self.tmp, self.repo = scratch.tmp, scratch.repo
        self.base, self.claim, self.cwd = (scratch.base, scratch.claim,
                                           scratch.cwd)
        self.state = self.tmp / "state"

    def handoff(self):
        return run_cli(self.repo, self.state, "handoff", "--claim-file",
                       str(self.claim), "--base", self.base,
                       "--local-only", cwd=self.cwd)

    def test_a_changed_path_the_claim_never_names_is_reported(self):
        # The fixture's claim names `review.toml` as its reference; the
        # span changed `f.txt`, which it names nowhere.
        code, rec = self.handoff()
        self.assertEqual(code, 0, rec)
        self.assertTrue(rec["ok"], "the report never decides the exit")
        [item] = rec["scope"]
        self.assertEqual(item["level"], "notice")
        self.assertEqual(item["code"], "R-SCOPE-UNNAMED")
        self.assertIn("f.txt", item["message"])

    def test_a_claim_that_accounts_for_it_reports_nothing(self):
        self.claim.write_text(json.dumps({
            "objective": "scope test",
            "review_scope": "f.txt is the whole change",
            "references": [{"path": "review.toml", "required": True}]}),
            encoding="utf-8")
        code, rec = self.handoff()
        self.assertEqual(code, 0, rec)
        self.assertNotIn("scope", rec,
                         "a claim that accounts for the span says nothing "
                         "here — the field exists to carry a disagreement")


class TestRespondRefusesAMismatchedAuthor(unittest.TestCase):
    """Round-3 F2's real-verb falsification.

    A round's request already names its author — `handoff` stamps the
    repository's configured `[roles] author` on it. A disposition JSON
    naming a DIFFERENT author (the REVIEWER, say, answering its own
    verdict) must refuse before `respond --out` writes the envelope, the
    kept exchange copy or any ledger event; the otherwise identical
    response naming the request's real author must succeed and the
    recorded disposition events must carry it.

    MUTATION: reverting `cmd_respond` to `data.get("author",
    cfg.roles["author"])` (dropping the derive-and-refuse block) makes the
    mismatched case succeed, record `author: "codex"`, and this test's
    refusal assertions fail.
    """

    def setUp(self):
        scratch = scratch_loop_repo(self, "respond-author-", name="author",
                                    objective="respond author test")
        self.tmp, self.repo = scratch.tmp, scratch.repo
        self.base, self.claim, self.cwd = scratch.base, scratch.claim, scratch.cwd
        self.state = self.tmp / "state"

    def _run(self, *argv):
        return run_cli(self.repo, self.state, *argv, cwd=self.cwd)

    def _disposition_json(self, head, author):
        return json.dumps({
            "head": head, "round": 1, "author": author,
            "dispositions": [{"finding_id": "F1", "disposition": "accepted",
                              "payload": {"change": "fixed",
                                          "verification": "observed",
                                          "falsification": {
                                              "status": "pass",
                                              "mutation": "fails_without_fix"}
                                          }}]})

    def test_a_mismatched_author_refuses_before_any_write(self):
        head = _git(self.repo, "rev-parse", "HEAD")
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              "--base", self.base, "--local-only")
        self.assertEqual(code, 0, rec)
        # The request `handoff` just recorded stamps this repo's configured
        # author, "claude" — confirmed here rather than assumed, since the
        # whole test rests on it.
        req = next(e for e in Ledger(self.state).events()
                  if e.get("event") == "request" and e.get("round") == 1)
        self.assertEqual(req.get("author"), "claude")

        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head), encoding="utf-8")
        code, closed = self._run("close", "--verdict", str(v1))
        self.assertEqual(code, 0, closed)

        d_json = self.tmp / "d.json"
        d_json.write_text(self._disposition_json(head, "codex"),
                          encoding="utf-8")
        d_md = self.tmp / "d.md"
        before_ledger = self.state.joinpath("ledger.jsonl").read_bytes()

        code, resp = self._run("respond", "--verdict", str(v1),
                               "--from-json", str(d_json), "--out", str(d_md))

        self.assertEqual(code, 1, resp)
        self.assertIn("author", resp.get("error", ""))
        self.assertFalse(d_md.exists(),
                         "the mismatched response must write no envelope")
        self.assertEqual(
            self.state.joinpath("ledger.jsonl").read_bytes(), before_ledger,
            "the mismatched response must record no ledger event")

        # The control: the otherwise identical response, naming the
        # request's real author, succeeds — and the recorded disposition
        # event carries it.
        d_json.write_text(self._disposition_json(head, "claude"),
                          encoding="utf-8")
        code, resp2 = self._run("respond", "--verdict", str(v1),
                                "--from-json", str(d_json), "--out", str(d_md))
        self.assertEqual(code, 0, resp2)
        self.assertTrue(d_md.exists())
        disp = [e for e in Ledger(self.state).events()
               if e.get("event") == "disposition"]
        self.assertEqual(len(disp), 1, disp)
        self.assertEqual(disp[0].get("author"), "claude")

    def test_an_omitted_author_falls_back_to_the_recorded_request(self):
        """The control the falsification does not need but the design
        claims: a disposition that names NO author is not a mismatch — it
        is silence, and the recorded request's author fills it, exactly as
        `cfg.roles["author"]` did before this fix existed for a round with
        no derivable request."""
        head = _git(self.repo, "rev-parse", "HEAD")
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              "--base", self.base, "--local-only")
        self.assertEqual(code, 0, rec)
        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head), encoding="utf-8")
        code, closed = self._run("close", "--verdict", str(v1))
        self.assertEqual(code, 0, closed)
        d_json = self.tmp / "d.json"
        data = json.loads(self._disposition_json(head, "codex"))
        del data["author"]
        d_json.write_text(json.dumps(data), encoding="utf-8")
        d_md = self.tmp / "d.md"

        code, resp = self._run("respond", "--verdict", str(v1),
                               "--from-json", str(d_json), "--out", str(d_md))

        self.assertEqual(code, 0, resp)
        disp = [e for e in Ledger(self.state).events()
               if e.get("event") == "disposition"]
        self.assertEqual(len(disp), 1, disp)
        self.assertEqual(disp[0].get("author"), "claude")

    def test_b_respond_without_out_refuses_before_any_envelope_is_printed(self):
        """Round-4 F1's falsification, second door: `respond` WITHOUT
        `--out` used to skip the derive-and-refuse check entirely, because
        `recorded` (the thing the check reads the round from) was only ever
        resolved `if args.out`. A mismatched author must refuse before the
        envelope is rendered to stdout at all — a run that succeeds prints
        the disposition envelope (not JSON), so a refusal is distinguished
        here by the response staying JSON (`_blocked`'s shape) rather than
        becoming the printed envelope.

        MUTATION: reverting the `recorded = transport.recorded_verdict(...,LINEAGE)`
        resolution back inside `if args.out:` (round 4 F1's regression)
        makes `recorded` stay `None` on this path, the author check never
        runs, and the mismatched case below prints the `author="codex"`
        envelope with exit 0 — this test's refusal assertions fail.
        """
        head = _git(self.repo, "rev-parse", "HEAD")
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              "--base", self.base, "--local-only")
        self.assertEqual(code, 0, rec)
        req = next(e for e in Ledger(self.state).events()
                  if e.get("event") == "request" and e.get("round") == 1)
        self.assertEqual(req.get("author"), "claude")
        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head), encoding="utf-8")
        code, closed = self._run("close", "--verdict", str(v1))
        self.assertEqual(code, 0, closed)

        d_json = self.tmp / "d.json"
        d_json.write_text(self._disposition_json(head, "codex"),
                          encoding="utf-8")

        code, resp = self._run("respond", "--verdict", str(v1),
                               "--from-json", str(d_json))

        self.assertEqual(code, 1, resp)
        self.assertIsInstance(
            resp, dict,
            "a refusal is `_blocked`'s JSON shape, never the envelope text "
            "the success path prints")
        self.assertIn("author", resp.get("error", ""))

        # The control: the otherwise identical response, naming the
        # request's real author, succeeds and prints the envelope, carrying
        # that author on its open tag.
        d_json.write_text(self._disposition_json(head, "claude"),
                          encoding="utf-8")
        code, resp2 = self._run("respond", "--verdict", str(v1),
                                "--from-json", str(d_json))
        self.assertEqual(code, 0, resp2)
        self.assertIsInstance(resp2, str)
        self.assertIn('author="claude"', resp2)

    def test_c_ledger_add_refuses_a_mismatched_disposition_before_appending(
            self):
        """Round-4 F1's falsification, third door: standalone `ledger add`
        of a disposition envelope never compared its author against the
        recorded request at all — `disposition_events`' own docstring
        documented that this door "carries the wire author unverified".
        Built directly with `wire.emit_disposition` here, bypassing
        `respond` entirely, the way a disposition arriving from another
        installation does.

        MUTATION: removing the `transport.check_disposition_author` call
        `cmd_ledger_add`'s disposition branch now makes before
        `validate_disposition`/`ledger.add_all` makes the mismatched
        `ledger add` below exit 0 and append an `author: "codex"`
        disposition event — this test's refusal assertions fail.
        """
        head = _git(self.repo, "rev-parse", "HEAD")
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              "--base", self.base, "--local-only")
        self.assertEqual(code, 0, rec)
        req = next(e for e in Ledger(self.state).events()
                  if e.get("event") == "request" and e.get("round") == 1)
        self.assertEqual(req.get("author"), "claude")
        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head), encoding="utf-8")
        code, closed = self._run("close", "--verdict", str(v1))
        self.assertEqual(code, 0, closed)

        scratch_cfg = config.load(self.repo)
        dispositions = [{"finding_id": "F1", "disposition": "accepted",
                         "payload": {"change": "fixed",
                                     "verification": "observed",
                                     "falsification": {
                                         "status": "pass",
                                         "mutation": "fails_without_fix"}}}]

        def _envelope(author):
            return wire.emit_disposition(
                tag=scratch_cfg.wrapper_tag, verdict_sha=head, head=head,
                author=author, round_no=1, dispositions=dispositions)

        d_md = self.tmp / "d-codex.md"
        d_md.write_text(_envelope("codex"), encoding="utf-8")
        before_ledger = self.state.joinpath("ledger.jsonl").read_bytes()

        code, resp = self._run("ledger", "add", str(d_md))

        self.assertEqual(code, 1, resp)
        self.assertIn("author", resp.get("error", ""))
        self.assertEqual(
            self.state.joinpath("ledger.jsonl").read_bytes(), before_ledger,
            "the mismatched disposition must append no ledger event")

        # The control: the otherwise identical envelope, naming the
        # request's real author, is accepted and recorded with it.
        d_md2 = self.tmp / "d-claude.md"
        d_md2.write_text(_envelope("claude"), encoding="utf-8")

        code, resp2 = self._run("ledger", "add", str(d_md2))

        self.assertEqual(code, 0, resp2)
        disp = [e for e in Ledger(self.state).events()
               if e.get("event") == "disposition"]
        self.assertEqual(len(disp), 1, disp)
        self.assertEqual(disp[0].get("author"), "claude")

    def _raw_envelope(self, cfg, head, dispositions, *,
                      wrapper_author, body_author):
        """A disposition envelope with the wrapper's `author` attribute and
        the body's `author` member set INDEPENDENTLY (`None` omits the
        stamp entirely) — the states `wire.emit_disposition` cannot
        produce, since its single `author: str` parameter always stamps
        both identically. Round 5 F1: standalone `ledger add` is the one
        door that reads an already-written file, so the two stamps can
        already disagree, or either can be missing, before this door ever
        sees the envelope."""
        data = {"verdict_sha": head, "head": head, "round": 1,
                "dispositions": dispositions}
        if body_author is not None:
            data["author"] = body_author
        attrs = f'verdict_sha="{head}" head="{head}"'
        if wrapper_author is not None:
            attrs += f' author="{wrapper_author}"'
        attrs += (f' round="1" tool="{wire.tool_identity()}" '
                 f'shape="{wire.shape_identity()}"')
        body = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)
        tag = cfg.wrapper_tag
        return (f"<{tag}-review-disposition {attrs}>\n{body}\n"
               f"</{tag}-review-disposition>\n")

    def test_d_ledger_add_derives_an_omitted_author_from_the_recorded_request(
            self):
        """Round-5 F1's falsification: standalone `ledger add` passed only
        the wrapper attribute into `check_disposition_author`, which
        returned without action whenever the SUPPLIED author was `None` —
        even when the round's recorded request named one. So a disposition
        that named no author at all (both wrapper and body omitted, the
        way a hand-assembled or third-party envelope might arrive) still
        appended `author: null`, silently losing the round's real author.

        MUTATION: reverting `check_disposition_author` to `if expected is
        None or author is None: return` (round-5 F1's regression) — or
        dropping the `parsed.attrs["author"] = resolved` /
        `parsed.data["author"] = resolved` assignment in `cmd_ledger_add`
        that carries its answer forward — makes this envelope append
        `author: null` again and this test's assertion fails.
        """
        head = _git(self.repo, "rev-parse", "HEAD")
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              "--base", self.base, "--local-only")
        self.assertEqual(code, 0, rec)
        req = next(e for e in Ledger(self.state).events()
                  if e.get("event") == "request" and e.get("round") == 1)
        self.assertEqual(req.get("author"), "claude")
        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head), encoding="utf-8")
        code, closed = self._run("close", "--verdict", str(v1))
        self.assertEqual(code, 0, closed)

        scratch_cfg = config.load(self.repo)
        dispositions = [{"finding_id": "F1", "disposition": "accepted",
                         "payload": {"change": "fixed",
                                     "verification": "observed",
                                     "falsification": {
                                         "status": "pass",
                                         "mutation": "fails_without_fix"}}}]

        d_md = self.tmp / "d-omitted.md"
        d_md.write_text(self._raw_envelope(
            scratch_cfg, head, dispositions,
            wrapper_author=None, body_author=None), encoding="utf-8")

        code, resp = self._run("ledger", "add", str(d_md))

        self.assertEqual(code, 0, resp)
        disp = [e for e in Ledger(self.state).events()
               if e.get("event") == "disposition"]
        self.assertEqual(len(disp), 1, disp)
        self.assertEqual(
            disp[0].get("author"), "claude",
            "an omitted disposition author must derive to the round's "
            "recorded request author, never be recorded as null")

    def test_e_ledger_add_still_admits_the_legacy_no_request_author_state(
            self):
        """The distinct control the Required outcome names explicitly:
        derivation only applies when the round's REQUEST carries an
        author. A ledger whose request predates the field (or is simply
        older than it) has nothing to derive from, and an omitted
        disposition author there is the declared legacy state — recorded
        as `author: null`, exactly as before round-5 F1's fix, and NOT a
        refusal.

        MUTATION: making `check_disposition_author`/`cmd_ledger_add`
        derive or refuse unconditionally, even absent a recorded request
        author, makes this legacy ledger addition raise or record a
        fabricated author instead of the documented null.
        """
        head = _git(self.repo, "rev-parse", "HEAD")
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              "--base", self.base, "--local-only")
        self.assertEqual(code, 0, rec)
        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head), encoding="utf-8")
        code, closed = self._run("close", "--verdict", str(v1))
        self.assertEqual(code, 0, closed)

        # Simulate a ledger whose round-1 request predates the `author`
        # field: strip it from the recorded request event directly, the
        # state `request_author` documents as indistinguishable from an
        # older ledger.
        ledger_path = self.state / "ledger.jsonl"
        lines = ledger_path.read_text(encoding="utf-8").splitlines()
        rewritten = []
        stripped = 0
        for line in lines:
            ev = json.loads(line)
            if ev.get("event") == "request" and ev.get("round") == 1:
                self.assertIn("author", ev)
                del ev["author"]
                stripped += 1
            rewritten.append(json.dumps(ev, sort_keys=True,
                                        ensure_ascii=False))
        self.assertEqual(stripped, 1, "expected exactly one round-1 request")
        ledger_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

        scratch_cfg = config.load(self.repo)
        dispositions = [{"finding_id": "F1", "disposition": "accepted",
                         "payload": {"change": "fixed",
                                     "verification": "observed",
                                     "falsification": {
                                         "status": "pass",
                                         "mutation": "fails_without_fix"}}}]
        d_md = self.tmp / "d-legacy.md"
        d_md.write_text(self._raw_envelope(
            scratch_cfg, head, dispositions,
            wrapper_author=None, body_author=None), encoding="utf-8")

        code, resp = self._run("ledger", "add", str(d_md))

        self.assertEqual(code, 0, resp)
        disp = [e for e in Ledger(self.state).events()
               if e.get("event") == "disposition"]
        self.assertEqual(len(disp), 1, disp)
        self.assertIsNone(
            disp[0].get("author"),
            "with no recorded request author, an omitted disposition "
            "author is the declared legacy state, not a fabricated one")

    def test_f_ledger_add_accepts_author_present_in_only_one_stamp(self):
        """Presence coverage: `wire.emit_disposition` always stamps the
        wrapper attribute and the body member identically, so an envelope
        with only ONE of the two set is a state only a hand-assembled or
        edited-after-the-fact file (exactly `ledger add`'s door) can
        produce. Either stamp alone is enough to supply the author `ledger
        add` compares against the recorded request.
        """
        head = _git(self.repo, "rev-parse", "HEAD")
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              "--base", self.base, "--local-only")
        self.assertEqual(code, 0, rec)
        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head), encoding="utf-8")
        code, closed = self._run("close", "--verdict", str(v1))
        self.assertEqual(code, 0, closed)
        scratch_cfg = config.load(self.repo)
        dispositions = [{"finding_id": "F1", "disposition": "accepted",
                         "payload": {"change": "fixed",
                                     "verification": "observed",
                                     "falsification": {
                                         "status": "pass",
                                         "mutation": "fails_without_fix"}}}]

        for case, wrapper_author, body_author in (
                ("wrapper only", "claude", None),
                ("body only", None, "claude")):
            with self.subTest(case=case):
                d_md = self.tmp / f"d-{case.replace(' ', '-')}.md"
                d_md.write_text(self._raw_envelope(
                    scratch_cfg, head, dispositions,
                    wrapper_author=wrapper_author,
                    body_author=body_author), encoding="utf-8")

                code, resp = self._run("ledger", "add", str(d_md))

                self.assertEqual(code, 0, resp)
                # Each envelope's bytes differ (only one stamp set), so its
                # digest and `batch` differ too — this appends a NEW
                # disposition event rather than deduping against the
                # other case's; the most recent one is this case's.
                disp = [e for e in Ledger(self.state).events()
                       if e.get("event") == "disposition"]
                self.assertEqual(
                    disp[-1].get("author"), "claude",
                    f"{case}: the single supplied stamp must be recorded, "
                    f"agreeing with the round's recorded request author")

    def test_g_ledger_add_refuses_wrapper_body_author_disagreement(self):
        """Agreement coverage: when BOTH stamps are present but name
        different authors, there is no single supplied value left to
        compare against the recorded request — `ledger add` must refuse
        outright, before anything is validated or appended, rather than
        silently preferring one stamp over the other.

        The wrapper here names the round's REAL recorded author
        (`claude`), so the pre-existing supplied-vs-expected mismatch
        check alone would not catch this: a wrapper that already agrees
        with the request passes it. Only comparing wrapper against body
        catches the body's disagreeing `codex` — the exact case that
        matters, since it is the one an author-vs-expected check cannot
        distinguish from an honest, agreeing envelope.

        MUTATION: dropping `transport.disposition_supplied_author`'s
        disagreement check (falling back to the wrapper attribute alone,
        as the door did before round 5 F1) makes this envelope succeed and
        record the wrapper's `claude`, even though the body secretly
        claims `codex` — this test's refusal assertions fail.
        """
        head = _git(self.repo, "rev-parse", "HEAD")
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              "--base", self.base, "--local-only")
        self.assertEqual(code, 0, rec)
        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head), encoding="utf-8")
        code, closed = self._run("close", "--verdict", str(v1))
        self.assertEqual(code, 0, closed)
        scratch_cfg = config.load(self.repo)
        dispositions = [{"finding_id": "F1", "disposition": "accepted",
                         "payload": {"change": "fixed",
                                     "verification": "observed",
                                     "falsification": {
                                         "status": "pass",
                                         "mutation": "fails_without_fix"}}}]
        d_md = self.tmp / "d-disagree.md"
        d_md.write_text(self._raw_envelope(
            scratch_cfg, head, dispositions,
            wrapper_author="claude", body_author="codex"), encoding="utf-8")
        before_ledger = self.state.joinpath("ledger.jsonl").read_bytes()

        code, resp = self._run("ledger", "add", str(d_md))

        self.assertEqual(code, 1, resp)
        self.assertIn("author", resp.get("error", ""))
        self.assertEqual(
            self.state.joinpath("ledger.jsonl").read_bytes(), before_ledger,
            "a disposition whose wrapper and body name different authors "
            "must append no ledger event")


class TestGateIdIsASafeFilenameComponent(unittest.TestCase):
    """Round 6 F2: a gate id is a logical identity AND a path component.

    `_retain_output` writes `<ledger>/gate-output/<sha>/<run>/<id>.log`, so an
    ABSOLUTE id discards the directory entirely — measured: a manifest whose
    id was an absolute path was accepted, ran, and wrote outside the ledger's
    subtree — while separators and dot segments escape or alias it. Round 5
    established that two rows cannot share an id STRING; two distinct strings
    can still name one destination, and on a case-folding filesystem
    routinely do.

    The grammar is closed rather than the escapes enumerated: containment and
    one-id-one-file are properties of the alphabet, not of a blacklist.

    Mutations: accept any non-blank id and the escape rows pass; compare ids
    case-sensitively and the alias row passes.
    """

    def _cfg(self):
        return dataclasses.replace(CFG, ledger_dir=None)

    def _rows(self, *ids):
        return "".join(f'[[gates]]\nid = "{i}"\ncommand = ["true"]\n'
                       for i in ids)

    def _refused(self, body, door):
        with self.assertRaises(config.ConfigError) as ctx:
            if door == "from_text":
                config.from_text(body, self._cfg(), source="t")
            else:
                tmp = Path(tempfile.mkdtemp(prefix="gate-id-"))
                self.addCleanup(lambda: __import__("shutil").rmtree(
                    tmp, ignore_errors=True))
                subprocess.run(["git", "init", "-q", str(tmp)], check=True,
                               capture_output=True, timeout=60)
                (tmp / "review.toml").write_text(body, encoding="utf-8")
                config.load(tmp)
        return str(ctx.exception)

    ESCAPES = {
        "absolute": "/tmp/escaped",
        "parent segment": "../escaped",
        "separator": "a/b",
        "leading dot": ".hidden",
        "dot segment": "..",
        "backslash": "a\\b",
        "space": "two words",
    }

    def test_no_escape_form_survives_either_configuration_door(self):
        for door in ("from_text", "load"):
            for label, gid in self.ESCAPES.items():
                with self.subTest(door=door, form=label):
                    self.assertIn("filename component",
                                  self._refused(self._rows(gid), door))

    def test_two_ids_naming_one_destination_refuse(self):
        """Distinct strings, one file: the alias round 5's string equality
        could not see."""
        self.assertIn("one retained output",
                      self._refused(self._rows("tests", "TESTS"),
                                    "from_text"))

    def test_distinct_valid_ids_are_the_paired_control(self):
        cfg = config.from_text(self._rows("tests", "lint-2", "a.b_c"),
                               self._cfg(), source="t")
        self.assertEqual([g["id"] for g in cfg.gates],
                         ["tests", "lint-2", "a.b_c"])

    def test_every_admitted_id_stays_an_immediate_child(self):
        """The property the grammar exists to give `_retain_output`: for any
        id the schema admits, the destination is one level under the
        per-SHA directory and nowhere else."""
        target = Path("/ledger/gate-output/abcdef")
        cfg = config.from_text(self._rows(*self.CONTAINMENT), self._cfg(),
                               source="t")
        for gate in cfg.gates:
            with self.subTest(id=gate["id"]):
                out = (target / f"{gate['id']}.log").resolve()
                self.assertEqual(out.parent, target.resolve())

    CONTAINMENT = ("tests", "lint-2", "a.b_c", "A", "z9")


if __name__ == "__main__":
    unittest.main()

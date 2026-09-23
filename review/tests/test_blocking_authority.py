"""One authority for "is this finding blocking?" (brief
`unverifiable-breaker-target-authority`, public issue #6; loupe 0.26.0).

0.25.0 moved the verdict précis to the TARGET commit's `review.toml` — the
configuration `close` and `validate --from-target` already judge a verdict
by. Three readers of the same question stayed on the CHECKOUT's
configuration: the `blocking` stamp `respond --out` (and `ledger add`)
writes on a falsification run, the `unverifiable` breaker's judgment of a
run that carries no stamp, and the D-BLOCKING rule that decides whether a
finding may be deferred. In a round whose checkout declares another
blocking list than the reviewed commit — a taxonomy edit in flight — each
could answer the other way from the précis.

MEASURED FIRST (the brief's falsification condition: every reader already
receives the target's configuration). Run against 0.26.0's code before the
change, the rows marked * below failed and only the agreeing control
passed, so the condition was false. The track report records that run.

Through the real entry point: `bin/loupe` as a subprocess in a scratch
repository. The TARGET's blocking list is committed and reviewed; the
CHECKOUT's is an uncommitted edit of the same `review.toml` after `close`.
The finding carries a falsification test, so its effective blocking state
is its severity's membership.

| class                        | target      | checkout    | act                     | expected                         |
|------------------------------|-------------|-------------|-------------------------|----------------------------------|
| * stamp, target blocks       | B,High      | B           | respond --out, cannot   | run blocking=true, fires         |
| * stamp, target does not     | B           | B,High      | respond --out, cannot   | run blocking=false, silent       |
|   stamp, agree (control)     | B,High      | B,High      | respond --out, cannot   | blocking=true, fires             |
| * ledger add door            | B,High      | B           | ledger add, cannot      | run blocking=true, fires         |
| * deferral, target blocks    | B,High      | B           | respond --out, deferred | refused D-BLOCKING, state kept   |
| * deferral, target does not  | B           | B,High      | respond --out, deferred | recorded                         |
| * unstamped run, target blocks | B,High    | B           | ledger report           | unverifiable fires               |
| * unstamped run, target does not | B       | B,High      | ledger report           | silent                           |

"State kept" is byte-identity of the ledger and the absence of the
`--out` file. `B` is Blocker.

MUTATIONS (results in the track report): stamp `respond --out` with the
checkout; stamp the `ledger add` door with the checkout; judge an
unstamped run by the checkout's list; validate `respond`'s dispositions
under the checkout.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from review import config
from review.ledger import Ledger
from review.tests import synth
from review.tests._real_cli import Scratch, git, loupe
from review.tests.test_git_timeout import TAXONOMY

#: The scratch taxonomy's blocking line, read from it rather than restated.
_BLOCKING_LINE = next(l for l in TAXONOMY.splitlines()
                      if l.startswith("blocking = "))
BOTH = ["Blocker", "High"]
ONLY_BLOCKER = ["Blocker"]
TEST = "run review/tests/test_x.py and see it fail without the fix"
CANNOT = {"status": "cannot_execute", "mutation": "not_run",
          "note": "needs a runner this sandbox lacks"}


def _config(blocking: list[str]) -> str:
    text = TAXONOMY.format(roles_extra="", limits_extra="")
    return text.replace(_BLOCKING_LINE, f"blocking = {json.dumps(blocking)}")


class _Split(unittest.TestCase):
    """A closed round whose target declares one blocking list while the
    checkout now declares another."""

    def round(self, target: list[str], checkout: list[str],
              severity: str = "High"):
        s = Scratch(self, "blocking-authority-")
        cfg = s.repo / config.CONFIG_BASENAME
        cfg.write_text(_config(target), encoding="utf-8")
        git(s.repo, "commit", "-q", "--allow-empty", "-am",
            "the target's taxonomy")
        code, rec, out, err = loupe(s, "handoff", "--claim-file",
                                    str(s.claim), "--base", s.base)
        self.assertEqual(code, 0, (out, err))
        s.sha = rec["sha"]
        s.verdict = s.root / "v1.md"
        s.verdict.write_text(
            f'<{synth.TAG}-review-verdict sha="{s.sha}">\nVERDICT: changes '
            f"requested\n\n## findings\n\n"
            + synth.finding(1, severity=severity, falsification=TEST,
                            evidence="f.txt:1")
            + f"## evidence checked\n\nf.txt\n</{synth.TAG}-review-verdict>\n",
            encoding="utf-8")
        code, rec, out, err = loupe(s, "close", "--verdict", str(s.verdict))
        self.assertEqual(code, 0, (out, err))
        # The taxonomy edit in flight: the checkout's configuration only.
        cfg.write_text(_config(checkout), encoding="utf-8")
        return s

    def answers(self, s, disposition: str, payload: dict, **extra):
        path = s.root / f"answers-{disposition}.json"
        path.write_text(json.dumps({
            "head": s.sha, "author": "claude", **extra,
            "dispositions": [{"finding_id": "F1", "disposition": disposition,
                              "payload": payload}]}), encoding="utf-8")
        return path

    def respond(self, s, disposition: str, payload: dict):
        out = s.root / f"d-{disposition}.md"
        code, rec, stdout, err = loupe(
            s, "respond", "--verdict", str(s.verdict), "--from-json",
            str(self.answers(s, disposition, payload)), "--out", str(out))
        return code, rec, stdout, err, out

    def runs(self, s) -> list[dict]:
        return [e for e in Ledger(s.state).events()
                if e.get("event") == "falsification_run"]

    def legacy_run(self, s):
        """A run as a ledger written before the stamp holds it: an accepted
        row and a `cannot_execute` run, with no `blocking`."""
        ledger = Ledger(s.state)
        finding = next(e for e in ledger.events()
                       if e.get("event") == "finding")
        lineage = ledger.lineage_of(finding)
        ledger.add({"event": "disposition", "round": 1, "finding_id": "F1",
                    "fp": finding["fp"], "disposition": "accepted",
                    "payload": {}, "verdict_sha": s.sha, "head": s.sha},
                   lineage=lineage)
        ledger.add({"event": "falsification_run", "round": 1,
                    "fp": finding["fp"], "status": "cannot_execute"},
                   lineage=lineage)

    def fired(self, s) -> list[str]:
        code, report, out, err = loupe(s, "ledger", "report", "--format",
                                       "json")
        self.assertEqual(code, 0, (out, err))
        return [b["breaker"] for b in report["breakers_fired"]]


ACCEPTED_CANNOT = {"change": "fixed", "verification": "observed",
                   "falsification": CANNOT}


class TestTheStampIsTheTargets(_Split):

    def check(self, row, target, checkout, blocking: bool):
        with self.subTest(row=row):
            s = self.round(target, checkout)
            code, rec, out, err, _ = self.respond(s, "accepted",
                                                  ACCEPTED_CANNOT)
            self.assertEqual(code, 0, (out, err))
            (run,) = self.runs(s)
            self.assertIs(run["blocking"], blocking, row)
            self.assertEqual("unverifiable" in self.fired(s), blocking, row)

    def test_respond_out(self):
        self.check("stamp, target blocks", BOTH, ONLY_BLOCKER, True)
        self.check("stamp, target does not", ONLY_BLOCKER, BOTH, False)
        self.check("stamp, agree (control)", BOTH, BOTH, True)

    def test_the_ledger_add_door(self):
        s = self.round(BOTH, ONLY_BLOCKER)
        # The disposition envelope as `respond` renders it — printed, not
        # recorded — then recorded through the standalone door.
        code, _rec, envelope, err = loupe(
            s, "respond", "--verdict", str(s.verdict), "--from-json",
            str(self.answers(s, "accepted", ACCEPTED_CANNOT, round=1)))
        self.assertEqual(code, 0, (envelope, err))
        path = s.root / "d-standalone.md"
        path.write_text(envelope, encoding="utf-8")
        self.assertEqual(self.runs(s), [], "the preview recorded nothing")
        code, rec, out, err = loupe(s, "ledger", "add", str(path))
        self.assertEqual(code, 0, (out, err))
        (run,) = self.runs(s)
        self.assertIs(run["blocking"], True)
        self.assertIn("unverifiable", self.fired(s))


class TestTheDeferralRuleIsTheTargets(_Split):

    DEFERRED = {"destination": "the next release",
                "trigger": "when the runner exists"}

    def test_a_finding_the_target_blocks_cannot_be_deferred(self):
        s = self.round(BOTH, ONLY_BLOCKER)
        before = (s.state / "ledger.jsonl").read_bytes()
        code, rec, out, err, written = self.respond(s, "deferred",
                                                    self.DEFERRED)
        self.assertNotIn("Traceback", err)
        self.assertEqual(code, 1, (out, err))
        self.assertIn("D-BLOCKING", {i["code"] for i in rec["items"]})
        self.assertEqual((s.state / "ledger.jsonl").read_bytes(), before)
        self.assertFalse(written.exists())
        # The paired control: the same finding accepted is recorded.
        code, rec, out, err, written = self.respond(s, "accepted",
                                                    ACCEPTED_CANNOT)
        self.assertEqual(code, 0, (out, err))
        self.assertTrue(written.exists())

    def test_a_finding_the_target_does_not_block_may_be(self):
        s = self.round(ONLY_BLOCKER, BOTH)
        code, rec, out, err, written = self.respond(s, "deferred",
                                                    self.DEFERRED)
        self.assertEqual(code, 0, (out, err))
        self.assertTrue(written.exists())
        self.assertEqual(
            [e["disposition"] for e in Ledger(s.state).events()
             if e.get("event") == "disposition"], ["deferred"])


class TestAnUnstampedRunIsJudgedByItsTarget(_Split):
    """A run recorded before the stamp existed, or by a hand, carries no
    `blocking`: the breaker judges it by its finding's severity — against
    the configuration of the commit that finding was ruled on."""


    def test_target_blocks(self):
        s = self.round(BOTH, ONLY_BLOCKER)
        self.legacy_run(s)
        self.assertNotIn("blocking", self.runs(s)[0])
        self.assertIn("unverifiable", self.fired(s))

    def test_target_does_not_block(self):
        s = self.round(ONLY_BLOCKER, BOTH)
        self.legacy_run(s)
        self.assertNotIn("unverifiable", self.fired(s))


class TestTheHandoffReadsTheSameAuthority(_Split):
    """The two other readers of an unstamped run: the hand-off's preflight,
    which refuses on an unauthorized firing (`unauthorized_breakers`), and
    the ledger report the emitted request carries on its face. The round-2
    hand-off sweeps the checkout's edited `review.toml` into its own commit,
    so the emission's configuration is the checkout's list too — and the
    finding is still judged by the commit its round-1 verdict ruled on.

    | class                     | round 1 target | checkout / round 2 | expected                              |
    |---------------------------|----------------|--------------------|---------------------------------------|
    | * preflight, target blocks | B,High        | B                  | refused: unverifiable; state kept     |
    | * report, target does not  | B             | B,High             | emitted; its report fires nothing     |
    """

    def fix(self, s):
        (s.repo / "f.txt").write_text("the fix\n", encoding="utf-8")

    def test_preflight_target_blocks(self):
        s = self.round(BOTH, ONLY_BLOCKER)
        self.legacy_run(s)
        self.fix(s)
        before = ((s.state / "ledger.jsonl").read_bytes(),
                  git(s.repo, "rev-parse", "HEAD"),
                  Scratch.snapshot(s.remote, bare=True))
        code, rec, out, err = loupe(s, "handoff", "--claim-file",
                                    str(s.claim))
        self.assertNotIn("Traceback", err)
        self.assertEqual(code, 1, (out, err))
        self.assertIn("unverifiable", rec["error"])
        self.assertIn("a circuit breaker fired", rec["error"])
        self.assertEqual(((s.state / "ledger.jsonl").read_bytes(),
                          git(s.repo, "rev-parse", "HEAD"),
                          Scratch.snapshot(s.remote, bare=True)), before)

    def test_report_target_does_not_block(self):
        s = self.round(ONLY_BLOCKER, BOTH)
        self.legacy_run(s)
        self.fix(s)
        code, rec, out, err = loupe(s, "handoff", "--claim-file",
                                    str(s.claim))
        self.assertEqual(code, 0, (out, err))
        request = Path(rec["kept"]).read_text(encoding="utf-8")
        self.assertIn("Breakers fired: **0**", request)
        self.assertNotIn("**unverifiable**", request)


if __name__ == "__main__":
    unittest.main()

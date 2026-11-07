"""Past the third `changes requested`: "should we redesign?" (Osama, 2026-11-04).

Real rounds through the real CLI. Partitions: fewer verdicts, a clean third, a
third that asks for changes, a fourth, a raised cap; a claim with an answer,
without one, with an empty one; an envelope already kept.
"""
from __future__ import annotations

import json
import subprocess
import unittest

from review import TOOL_NAME, vocab, wire
from review.ledger import Ledger
from review.tests._transport_fixtures import run_cli, scratch_loop_repo, sh

FINDING = """### F1
Severity: {severity}
Classification: {cls}
Title: {title}
Evidence: {anchor}:1 shows it
Why: because
Required outcome: fix it
FALSIFICATION: run it
"""


class Loop(unittest.TestCase):

    def setUp(self):
        self.s = scratch_loop_repo(self, "redesign-", name="author",
                                   objective="an objective")
        self.state = self.s.tmp / "state"
        self.base = self.s.base
        self.rounds = 0
        self.claim = json.loads(self.s.claim.read_text(encoding="utf-8"))

    def cli(self, *argv):
        return run_cli(self.s.repo, self.state, *argv, cwd=self.s.cwd)

    def head(self):
        return subprocess.run(
            ["git", "-C", str(self.s.repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()

    def handoff(self, **claim):
        self.rounds += 1
        (self.s.repo / "f.txt").write_text(f"round {self.rounds}\n",
                                           encoding="utf-8")
        sh("git", "-C", str(self.s.repo), "add", "f.txt")
        sh("git", "-C", str(self.s.repo), "commit", "-q", "-m",
           f"round {self.rounds}")
        path = self.s.tmp / "claim.json"
        path.write_text(json.dumps({**self.claim, **claim}), encoding="utf-8")
        args = ["handoff", "--claim-file", str(path), "--local-only"]
        if self.rounds == 1:
            args += ["--base", self.base]
        return self.cli(*args)

    def close(self, term="changes requested"):
        sha = self.head()
        n = self.rounds
        findings = ("None" if term == vocab.VERDICT_CLEAN else
                    FINDING.format(severity="Medium", cls="design_gap",
                                   title=f"finding of round {n}",
                                   anchor=f"a{n}.py"))
        closures = ""
        for e in Ledger(self.state).events():
            if e.get("event") == "finding" and e.get("round") == n - 1:
                closures = (f"\n## closures\n\n- {e['fp']} withdrawn: "
                            f"answered\n")
        text = (f'<{TOOL_NAME}-review-verdict sha="{sha}">\nVERDICT: {term}\n\n'
                f"## findings\n\n{findings}\n\n## evidence checked\n\nread it\n"
                f"{closures}</{TOOL_NAME}-review-verdict>\n")
        self.verdict_path = self.s.tmp / f"verdict{n}.md"
        self.verdict_path.write_text(text, encoding="utf-8")
        return self.cli("close", "--verdict", str(self.verdict_path))

    def respond(self):
        doc = {"head": self.head(), "dispositions": [
            {"finding_id": "F1", "disposition": "accepted",
             "payload": {"change": "fixed it", "verification": "ran the test",
                         "falsification": {"status": "pass",
                                           "mutation": "fails_without_fix"}}}]}
        path = self.s.tmp / "d.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        return self.cli("respond", "--verdict", str(self.verdict_path),
                        "--from-json", str(path), "--out",
                        str(self.s.tmp / f"disp{self.rounds}.md"))

    def full_round(self, **claim):
        code, rec = self.handoff(**claim)
        self.assertEqual(code, 0, rec)
        code, closed = self.close()
        self.assertEqual(code, 0, closed)
        code, responded = self.respond()
        self.assertEqual(code, 0, responded)
        return closed, responded


class TestTheTrigger(Loop):

    def test_nothing_fires_on_the_first_two(self):
        for _ in range(2):
            closed, responded = self.full_round()
            self.assertNotIn("redesign", closed)
            self.assertNotIn("redesign", responded)
            self.assertEqual(responded["next"], "loupe handoff")

    def test_the_third_changes_requested_asks_in_every_result(self):
        self.full_round()
        self.full_round()
        closed, responded = self.full_round()
        for result in (closed, responded):
            self.assertIn("Should we redesign?", result["then"])
            self.assertEqual(result["redesign"]["verdicts"], 3)
            self.assertIsNone(result["next"], "a question is not a command")
        self.assertEqual(
            [(f["round"], f["anchor"], f["classification"], f["severity"])
             for f in closed["redesign"]["findings"]],
            [(1, "a1.py", "design_gap", "Medium"),
             (2, "a2.py", "design_gap", "Medium"),
             (3, "a3.py", "design_gap", "Medium")])
        code, conv = self.cli("ledger", "convergence")
        self.assertEqual(code, 0, conv)
        self.assertEqual(conv["redesign"]["verdicts"], 3)

    def test_control_convergence_asks_nothing_before_it(self):
        self.full_round()
        code, conv = self.cli("ledger", "convergence")
        self.assertEqual(code, 0, conv)
        self.assertIsNone(conv["redesign"])

    def test_a_clean_third_asks_nothing(self):
        self.full_round()
        self.full_round()
        code, rec = self.handoff()
        self.assertEqual(code, 0, rec)
        code, closed = self.close(vocab.VERDICT_CLEAN)
        self.assertEqual(code, 0, closed)
        self.assertNotIn("redesign", closed)
        self.assertTrue(closed["lineage"].startswith("closed"))
        ledger = Ledger(self.state)
        lineage = ledger.lineage_of(next(e for e in ledger.events()
                                         if e.get("event") == "verdict"))
        self.assertIsNone(ledger.redesign_question(lineage))
        self.rounds = 0
        code, rec = self.handoff()
        self.assertEqual(code, 0, rec)


class TestAReplayIsNotARound(Loop):
    """L29f7f0663a F1: `ledger add --tokens` files one ruling as new rows."""

    def replay(self):
        for tokens in ("100", "200"):
            code, out = self.cli("ledger", "add", str(self.verdict_path),
                                 "--round", str(self.rounds),
                                 "--tokens", tokens)
            self.assertEqual(code, 0, out)

    def rows_of_this_ruling(self):
        return [e for e in Ledger(self.state).events()
                if e.get("event") == "verdict"
                and e.get("round") == self.rounds]

    def test_metadata_variants_of_one_round_ask_nothing(self):
        code, rec = self.handoff()
        self.assertEqual(code, 0, rec)
        self.assertEqual(self.close()[0], 0)
        self.replay()
        self.assertEqual(len(self.rows_of_this_ruling()), 3,
                         "the paired fact: these are distinct rows")
        code, responded = self.respond()
        self.assertEqual(code, 0, responded)
        self.assertNotIn("redesign", responded)
        code, rec = self.handoff()
        self.assertEqual(code, 0, rec)

    def test_metadata_variants_of_two_rounds_ask_nothing(self):
        for _ in range(2):
            code, rec = self.handoff()
            self.assertEqual(code, 0, rec)
            self.assertEqual(self.close()[0], 0)
            self.replay()
            code, responded = self.respond()
            self.assertEqual(code, 0, responded)
            self.assertNotIn("redesign", responded)
        code, rec = self.handoff()
        self.assertEqual(code, 0, rec)

    def test_control_three_distinct_rounds_with_replays_ask_and_count_three(self):
        for _ in range(3):
            code, rec = self.handoff()
            self.assertEqual(code, 0, rec)
            self.assertEqual(self.close()[0], 0)
            self.replay()
            code, responded = self.respond()
            self.assertEqual(code, 0, responded)
        self.assertEqual(responded["redesign"]["verdicts"], 3)
        code, rec = self.handoff()
        self.assertNotEqual(code, 0)
        self.assertIn("redesign_answer", json.dumps(rec))

    def test_control_a_latest_clean_asks_nothing_replayed_or_not(self):
        self.full_round()
        self.full_round()
        code, rec = self.handoff()
        self.assertEqual(code, 0, rec)
        code, closed = self.close(vocab.VERDICT_CLEAN)
        self.assertEqual(code, 0, closed)
        ledger = Ledger(self.state)
        lineage = ledger.lineage_of(next(e for e in ledger.events()
                                         if e.get("event") == "verdict"))
        self.assertIsNone(ledger.redesign_question(lineage))

    def test_control_another_lineage_does_not_add_to_this_one(self):
        self.full_round()
        self.full_round()
        code, rec = self.handoff()
        self.assertEqual(code, 0, rec)
        self.assertEqual(self.close(vocab.VERDICT_CLEAN)[0], 0)
        self.rounds = 0
        closed, responded = self.full_round()
        self.assertNotIn("redesign", closed)
        self.assertNotIn("redesign", responded)


class TestTheFourthRequest(Loop):

    def three(self):
        for _ in range(3):
            self.full_round()

    def test_refused_without_an_answer_and_nothing_moves(self):
        self.three()
        before = len(Ledger(self.state).events())
        code, rec = self.handoff()
        self.assertNotEqual(code, 0)
        text = json.dumps(rec)
        self.assertIn("Should we redesign?", text)
        self.assertIn("a2.py", text)
        self.assertIn(vocab.REDESIGN_ANSWER_FIELD, text)
        self.assertEqual(len(Ledger(self.state).events()), before)

    def test_refused_with_an_empty_answer(self):
        self.three()
        code, rec = self.handoff(redesign_answer="   ")
        self.assertNotEqual(code, 0)
        self.assertIn("redesign_answer", json.dumps(rec))

    def test_carried_with_an_answer_and_printed_in_the_claim(self):
        self.three()
        code, rec = self.handoff(redesign_answer="MARK-ANSWER each is real")
        self.assertEqual(code, 0, rec)
        kept = open(rec["kept"], encoding="utf-8").read()
        claim = wire.parse_request(kept).sections["claim"]
        self.assertIn("MARK-ANSWER each is real", claim)

    def test_each_further_request_answers_again(self):
        self.three()
        code, rec = self.handoff(redesign_answer="first answer")
        self.assertEqual(code, 0, rec)
        code, closed = self.close()
        self.assertEqual(code, 0, closed)
        self.assertEqual(closed["redesign"]["verdicts"], 4)
        self.respond()
        code, rec = self.handoff()
        self.assertNotEqual(code, 0)

    def test_a_raised_cap_does_not_silence_it(self):
        self.full_round()
        code, out = self.cli("ledger", "authorize-cap", "--to", "9",
                             "--reason", "large change", "--by", "osama")
        self.assertEqual(code, 0, out)
        self.full_round()
        closed, _ = self.full_round()
        self.assertIn("redesign", closed)

    def test_control_an_answer_nobody_asked_for_is_harmless(self):
        code, rec = self.handoff(redesign_answer="early")
        self.assertEqual(code, 0, rec)

    def test_a_kept_envelope_without_an_answer_stays_valid(self):
        """The refusal is the author's door. `validate` reads a request as
        ever, so no older round-4 envelope stops being valid."""
        self.three()
        code, rec = self.handoff(redesign_answer="MARK-ANSWER")
        self.assertEqual(code, 0, rec)
        text = open(rec["kept"], encoding="utf-8").read()
        lines = [ln for ln in text.splitlines() if "MARK-ANSWER" not in ln]
        path = self.s.tmp / "older.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        code, out = self.cli("validate", str(path))
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()

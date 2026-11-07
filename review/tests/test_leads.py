"""`## leads`: a Low or an Info that costs no round (2026-11-04).

Today's rule, unchanged: `clean to advance` carries exactly `None` under
`## findings`, so any finding of any severity forces `changes requested`. A lead
is the additive way out: no verdict of record holds the heading, and a reader
older than this one reads past it. Every partition goes through the real
`validate` and `close`.
"""
from __future__ import annotations

import json
import subprocess
import unittest

from review import TOOL_NAME, vocab, wire
from review.ledger import Ledger
from review.tests._transport_fixtures import run_cli, scratch_loop_repo

FINDING = """### F1
Severity: {severity}
Classification: design_gap
Title: a title
Evidence: f.txt:1 shows it
Why: because
Required outcome: fix it
FALSIFICATION: run it
"""


def verdict(sha, term="clean to advance", findings="None", extra=""):
    return (f'<{TOOL_NAME}-review-verdict sha="{sha}">\nVERDICT: {term}\n\n'
            f"## findings\n\n{findings}\n\n## evidence checked\n\nread it\n"
            f"{extra}</{TOOL_NAME}-review-verdict>\n")


class Round(unittest.TestCase):

    def setUp(self):
        self.s = scratch_loop_repo(self, "leads-", name="author",
                                   objective="an objective")
        self.state = self.s.tmp / "state"
        code, self.rec = self.cli("handoff", "--claim-file", str(self.s.claim),
                                  "--base", self.s.base, "--local-only")
        self.assertEqual(code, 0, self.rec)
        self.sha = subprocess.run(
            ["git", "-C", str(self.s.repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()

    def cli(self, *argv):
        return run_cli(self.s.repo, self.state, *argv, cwd=self.s.cwd)

    def write(self, text):
        path = self.s.tmp / "verdict.md"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def validate(self, text):
        return self.cli("validate", self.write(text), "--from-target")

    def codes(self, text):
        code, out = self.validate(text)
        items = out.get("items", []) if isinstance(out, dict) else []
        return code, [i["code"] for i in items if i["level"] == "error"]


class TestTheRule(Round):

    def test_the_current_rule_any_finding_refuses_clean(self):
        for severity in ("Blocker", "High", "Medium", "Low", "Info"):
            with self.subTest(severity):
                code, errors = self.codes(verdict(
                    self.sha, findings=FINDING.format(severity=severity)))
                self.assertNotEqual(code, 0)
                self.assertIn("V-CLEAN-FINDINGS", errors)

    def test_a_low_in_findings_is_pointed_at_leads_and_a_high_is_not(self):
        _, out = self.validate(verdict(
            self.sha, findings=FINDING.format(severity="Low")))
        self.assertIn("## leads", json.dumps(out))
        _, out = self.validate(verdict(
            self.sha, findings=FINDING.format(severity="High")))
        self.assertNotIn("## leads", json.dumps(out))

    def test_clean_carries_leads(self):
        code, errors = self.codes(verdict(self.sha, extra=(
            "\n## leads\n\n- Low high f.txt:1: this reads oddly\n"
            "- Info low f.txt: a thought\n")))
        self.assertEqual((code, errors), (0, []))

    def test_changes_requested_carries_leads_too(self):
        code, errors = self.codes(verdict(
            self.sha, term="changes requested",
            findings=FINDING.format(severity="Medium"),
            extra="\n## leads\n\n- Info medium f.txt:1: a thought\n"))
        self.assertEqual((code, errors), (0, []))

    def test_none_and_an_absent_section_state_no_leads(self):
        for extra in ("", "\n## leads\n\nNone\n", "\n## leads\n\n"):
            with self.subTest(extra):
                self.assertEqual(self.codes(verdict(self.sha, extra=extra)),
                                 (0, []))

    def test_each_defect_of_a_lead_by_its_code(self):
        for line, code in (
                ("- Medium high f.txt:1: worse", "V-LEAD-SEVERITY"),
                ("- High high f.txt:1: worse", "V-LEAD-SEVERITY"),
                ("- Blocker high f.txt:1: worse", "V-LEAD-SEVERITY"),
                ("- low high f.txt:1: wrong case", "V-LEAD-SEVERITY"),
                ("- Trivial high f.txt:1: undeclared", "V-LEAD-SEVERITY"),
                ("- Low sure f.txt:1: bad confidence", "V-LEAD-CONFIDENCE"),
                ("- Low high f.txt:1", "V-LEAD-GRAMMAR"),
                ("- Low f.txt:1: no confidence", "V-LEAD-GRAMMAR"),
                ("prose with no bullet", "V-LEAD-GRAMMAR")):
            with self.subTest(line):
                exit_code, errors = self.codes(verdict(
                    self.sha, extra=f"\n## leads\n\n{line}\n"))
                self.assertNotEqual(exit_code, 0)
                self.assertIn(code, errors)

    def test_a_repeated_section_is_refused(self):
        _, errors = self.codes(verdict(self.sha, extra=(
            "\n## leads\n\n- Low high f.txt:1: a\n"
            "\n## leads\n\n- Low high f.txt:2: b\n")))
        self.assertIn("V-LEAD-DUPLICATE", errors)

    def test_a_repository_that_calls_low_blocking_has_no_low_leads(self):
        toml = self.s.repo / "review.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace(
            'blocking = ["Blocker", "High"]',
            'blocking = ["Blocker", "High", "Low"]'), encoding="utf-8")
        cfg = __import__("review.config", fromlist=["load"]).load(self.s.repo)
        from review.validate import validate_verdict
        items = validate_verdict(wire.parse_verdict(verdict(self.sha, extra=(
            "\n## leads\n\n- Low high f.txt:1: x\n"))), cfg)
        self.assertIn("V-LEAD-SEVERITY", [i.code for i in items])


class TestWhatCloseDoes(Round):

    def test_clean_with_leads_closes_the_lineage_and_records_each(self):
        code, rec = self.cli("close", "--verdict", self.write(verdict(
            self.sha, extra="\n## leads\n\n- Low high f.txt:1: this reads oddly"
                            "\n- Info low f.txt: a thought\n")))
        self.assertEqual(code, 0, rec)
        self.assertEqual(rec["verdict"], "clean to advance")
        self.assertEqual(rec["findings"], 0)
        self.assertTrue(rec["lineage"].startswith("closed"))
        self.assertEqual(rec["leads"], ["Low high f.txt:1: this reads oddly",
                                        "Info low f.txt: a thought"])
        events = Ledger(self.state).events()
        leads = [e for e in events if e.get("event") == vocab.LEAD_EVENT]
        self.assertEqual([e["where"] for e in leads], ["f.txt:1", "f.txt"])
        self.assertEqual([e["round"] for e in leads], [1, 1])
        self.assertFalse([e for e in events if e.get("event") == "finding"],
                         "a lead is no finding, so no breaker counts it")

    def test_control_a_clean_with_none_reports_none(self):
        code, rec = self.cli("close", "--verdict",
                             self.write(verdict(self.sha)))
        self.assertEqual(code, 0, rec)
        self.assertNotIn("leads", rec)

    def test_two_leads_that_read_alike_stay_two(self):
        code, rec = self.cli("close", "--verdict", self.write(verdict(
            self.sha, extra="\n## leads\n\n- Low high f.txt:1: same\n"
                            "- Low high f.txt:1: same\n")))
        self.assertEqual(code, 0, rec)
        self.assertEqual(len([e for e in Ledger(self.state).events()
                              if e.get("event") == vocab.LEAD_EVENT]), 2)

    def test_the_kept_clean_verdict_reads_clean_to_a_merge_door(self):
        """`guarded-merge` asks three things of a kept verdict: its term, its
        sha, and that `validate` takes it. None looks at leads."""
        text = verdict(self.sha, extra="\n## leads\n\n- Low high f.txt:1: x\n")
        code, rec = self.cli("close", "--verdict", self.write(text))
        self.assertEqual(code, 0, rec)
        kept = open(rec["kept"], "rb").read()
        parsed = wire.parse_verdict(wire.decode_envelope(kept, "k"))
        self.assertEqual(parsed.verdict, vocab.VERDICT_CLEAN)
        self.assertEqual(parsed.sha, self.sha)
        self.assertTrue(parsed.findings_none)

    def test_the_verdict_shape_tells_the_judge(self):
        text = open(self.rec["kept"], encoding="utf-8").read()
        self.assertIn("`## leads`", text)
        self.assertIn(vocab.LEAD_GRAMMAR, text)


if __name__ == "__main__":
    unittest.main()

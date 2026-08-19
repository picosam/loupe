"""RVW-T3 and RVW-T4 — the round-5 deferrals now worked.

T3: the closure note-continuation rule rebuilt round-4 F2's vanishing act
(fp2:230b5241e01a1f15) — an unbulleted closure-shaped line was absorbed into
the note above it without error; and `validate <directory>` reached an
uncaught IsADirectoryError traceback (fp2:9f8d9f3677f33f51), a reachable
filesystem failure bypassing the structured-output contract.

T4: a partially counted token history reported state `live` and silently
undercounted (fp2:0a2cf43451b3d38f) — the fourth state, one level inside the
three round-4 F5 named apart; and the inventory summary presented an authored
`covers_falsification` boolean as a measurement (fp2:584a0004e158bc61).
"""
import contextlib
import io
import json
import unittest
import unittest.mock
from pathlib import Path

from review import validate, wire
from review.cli import main
from review.ledger import Ledger
from review.tests.util import REPO_ROOT

FP_A = "fp2:aaaaaaaaaaaaaaaa"
FP_B = "fp2:bbbbbbbbbbbbbbbb"


class TestUnbulletedClosureCannotVanish(unittest.TestCase):
    """FALSIFICATION: an unbulleted `<fp> <term>: <note>` line yields a
    defective record that fails validation — never silence inside another
    record's note."""

    def test_closure_shaped_line_is_a_defect_not_a_continuation(self):
        text = (f"- {FP_A} sustained: the fix misses the adjacent case\n"
                f"{FP_B} withdrawn: the refutation lands\n")
        closures = wire.parse_closures(text)
        self.assertEqual(len(closures), 2,
                         "the second line must yield its own record")
        self.assertIn("C-UNBULLETED",
                      {code for code, _ in closures[1].defects})
        self.assertNotIn(FP_B, closures[0].note,
                         "the vanishing act: absorbed into the note above")

    def test_the_defect_fails_verdict_validation(self):
        from review import config
        cfg = config.load(REPO_ROOT)
        body = (f'VERDICT: changes requested\n\n## findings\n\nNone\n\n'
                f'## closures\n\n- {FP_A} sustained: stands\n'
                f'{FP_B} withdrawn: lands\n')
        v = wire.parse_verdict(
            f'<{cfg.wrapper_tag}-review-verdict sha="{"9" * 40}">\n{body}\n'
            f'</{cfg.wrapper_tag}-review-verdict>')
        self.assertIn("C-UNBULLETED",
                      {i.code for i in validate.validate_closures(v)
                       if i.level == "error"})

    def test_wrapped_note_with_a_colon_still_continues(self):
        # Adjacent state, guarding against over-widening: prose continuations
        # can contain `token: value` shapes; only a FINGERPRINT-led
        # closure-shaped line reclassifies.
        text = (f"- {FP_A} sustained: the rule is stated at\n"
                f"  design:102 and the fix does not reach it\n")
        closures = wire.parse_closures(text)
        self.assertEqual(len(closures), 1)
        self.assertTrue(closures[0].well_formed)
        self.assertIn("design:102", closures[0].note)

    def test_fingerprint_led_prose_without_closure_shape_still_continues(self):
        # Adjacent state on the other side: a wrapped note may START with a
        # fingerprint; without the `<term>: <note>` shape it is prose.
        text = (f"- {FP_A} sustained: this repeats what\n"
                f"{FP_B} already established last round\n")
        closures = wire.parse_closures(text)
        self.assertEqual(len(closures), 1)
        self.assertIn(FP_B, closures[0].note)

    def test_unbulleted_closure_with_no_record_above_is_still_a_defect(self):
        closures = wire.parse_closures(f"{FP_A} sustained: stands\n")
        self.assertEqual(len(closures), 1)
        self.assertFalse(closures[0].well_formed)


class TestFilesystemFailuresKeepTheContract(unittest.TestCase):
    """FALSIFICATION: every reachable filesystem failure of `validate <path>`
    exits through the structured next-command contract, never a traceback."""

    def _run(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(argv)
        return code, json.loads(buf.getvalue())

    def test_validate_a_directory_is_a_structured_usage_error(self):
        code, out = self._run(["validate", str(REPO_ROOT / "review")])
        self.assertEqual(code, 2)
        self.assertIn("next", out)
        self.assertFalse(out["ok"])

    def test_validate_a_missing_file_is_a_structured_usage_error(self):
        code, out = self._run(["validate",
                               str(REPO_ROOT / "does-not-exist.md")])
        self.assertEqual(code, 2)
        self.assertIn("next", out)

    def test_validate_an_undecodable_file_is_a_structured_usage_error(self):
        # Adjacent state: bytes that are not text (UnicodeDecodeError is a
        # ValueError, caught by the same contract).
        binary = REPO_ROOT / ".git" / "index"
        if not binary.is_file():
            self.skipTest("no .git/index in this checkout")
        code, out = self._run(["validate", str(binary)])
        self.assertEqual(code, 2)
        self.assertIn("next", out)


def _envelope_events(with_tokens: dict):
    """(round, kind) -> tokens|None, as ledger events."""
    ledger = Ledger.in_memory()
    for (r, kind), tokens in sorted(with_tokens.items()):
        event = {"event": kind, "round": r, "sha": chr(96 + r) * 40,
                 "bytes": 10}
        if kind == "verdict":
            event.update({"verdict": "changes requested", "finding_ids": 0})
        if tokens is not None:
            event["tokens"] = tokens
        ledger.add(event)
    return ledger


class TestPartialTokenHistoryIsNeverLive(unittest.TestCase):
    """FALSIFICATION: a ledger where SOME envelope events carry counts
    reports `partial` with the uncounted events named — never `live`."""

    def test_partially_counted_is_partial_and_names_the_gaps(self):
        ledger = _envelope_events({(1, "request"): 100, (1, "verdict"): None,
                                   (2, "request"): None})
        state = ledger.token_state(1000)
        self.assertEqual(state["state"], "partial")
        self.assertEqual(state["spent"], 100)
        self.assertEqual(state["uncounted"],
                         ["round 1 verdict", "round 2 request"])
        self.assertIn("LOWER BOUND", state["why"])

    def test_fully_counted_is_live(self):
        ledger = _envelope_events({(1, "request"): 100, (1, "verdict"): 200})
        state = ledger.token_state(1000)
        self.assertEqual(state["state"], "live")
        self.assertEqual(state["spent"], 300)

    def test_uncounted_only_is_still_no_counts(self):
        ledger = _envelope_events({(1, "request"): None, (1, "verdict"): None})
        self.assertEqual(ledger.token_state(1000)["state"], "no_counts")

    def test_no_budget_stays_no_budget_even_partially_counted(self):
        ledger = _envelope_events({(1, "request"): 100, (1, "verdict"): None})
        self.assertEqual(ledger.token_state(None)["state"], "no_budget")

    def test_breaker_fires_on_a_lower_bound_breach_and_says_so(self):
        ledger = _envelope_events({(1, "request"): 1500, (1, "verdict"): None})
        fired = [b for b in ledger.breakers(round_cap=9, token_budget=1000)
                 if b.get("limit") == "tokens"]
        self.assertEqual(len(fired), 1)
        self.assertIn("LOWER BOUND", fired[0]["rule"])

    def test_breaker_stays_silent_under_budget_on_partial(self):
        ledger = _envelope_events({(1, "request"): 100, (1, "verdict"): None})
        self.assertEqual(
            [b for b in ledger.breakers(round_cap=9, token_budget=1000)
             if b.get("limit") == "tokens"], [])


if __name__ == "__main__":
    unittest.main()

"""Breaker composition (§5.3d), including the two amended rules:
evidence is content-addressed (a new pointer to old bytes is not novelty),
and a refuted finding re-raised with genuinely new evidence trips NEITHER
repetition NOR no-progress.

Also here: the progress breakers reading only COMPLETED rounds (round-3
F2), and the cumulative-token breaker with its four input states (round-3
F6, round-4 F5, round-5 fp2:0a2cf43451b3d38f).
"""
import contextlib
import dataclasses
import io
import json
import unittest
import unittest.mock

from review.cli import cmd_ledger_add, cmd_ledger_report
from review.fingerprint import compute
from review.ledger import Ledger
from review.tests import synth
from review.tests.util import CliArgs, LINEAGE, REPO_ROOT, cli_report

FP = compute("design_gap", "design", "", "the ledger has no owner")
OTHER = compute("design_gap", "design", "", "an unrelated second claim")


def make_ledger():
    return Ledger.in_memory()


def base_round1(ledger, disposition="refuted"):
    ledger.add({"event": "request", "round": 1, "sha": "a" * 40, "bytes": 10})
    ledger.add({"event": "verdict", "round": 1, "sha": "a" * 40,
                "verdict": "changes requested", "bytes": 10, "finding_ids": 1})
    ledger.add({"event": "finding", "round": 1, "id": "F1", "fp": FP,
                "severity": "High", "classification": "design_gap",
                "title": "t", "preventable_by": None})
    ledger.add({"event": "evidence", "round": 1, "fp": FP,
                "digest": "sha256:old", "pointer": "notes/old.txt"})
    ledger.add({"event": "disposition", "round": 1, "finding_id": "F1",
                "fp": FP, "disposition": disposition, "payload": {}})


def re_raise(ledger, evidence=None):
    ledger.add({"event": "request", "round": 2, "sha": "b" * 40, "bytes": 10})
    ledger.add({"event": "verdict", "round": 2, "sha": "b" * 40,
                "verdict": "changes requested", "bytes": 10, "finding_ids": 1})
    ledger.add({"event": "finding", "round": 2, "id": "F1", "fp": FP,
                "severity": "High", "classification": "design_gap",
                "title": "t", "preventable_by": None})
    if evidence:
        ledger.add({"event": "evidence", "round": 2, "fp": FP, **evidence})


# The declared blocking set: sweep F12 bound the `unverifiable` breaker to
# it, so a hand-seeded run (which carries no `blocking` stamp of its own) is
# judged through its finding event's severity against this.
BLOCKING = ["Blocker", "High"]


def fired(ledger, name):
    return [b for b in ledger.breakers(LINEAGE, round_cap=3,
                                       blocking_severities=BLOCKING)
            if b["breaker"] == name]


class TestRepetition(unittest.TestCase):
    def test_fires_on_refuted_reraise_without_new_evidence(self):
        led = make_ledger()
        base_round1(led)
        re_raise(led)
        self.assertTrue(fired(led, "repetition"),
                        "the reviewer is restating, not answering")

    def test_new_pointer_to_same_bytes_is_not_novelty(self):
        # Evidence is content-addressed (§5.3 F5d): same digest, new pointer.
        led = make_ledger()
        base_round1(led)
        re_raise(led, {"digest": "sha256:old", "pointer": "notes/renamed.txt"})
        self.assertTrue(fired(led, "repetition"))

    def test_does_not_fire_with_genuinely_new_evidence(self):
        led = make_ledger()
        base_round1(led)
        re_raise(led, {"digest": "sha256:new", "pointer": "notes/new.txt"})
        self.assertFalse(fired(led, "repetition"))


class TestNoProgress(unittest.TestCase):
    def test_refuted_reraised_with_new_evidence_does_not_trip(self):
        # The R1-F5c scenario: the new-evidence exemption must extend to
        # no-progress, or the two breakers contradict each other.
        led = make_ledger()
        base_round1(led)
        re_raise(led, {"digest": "sha256:new", "pointer": "notes/new.txt"})
        self.assertFalse(fired(led, "no-progress"))
        self.assertFalse(fired(led, "repetition"))

    def test_fires_on_a_truly_empty_round(self):
        led = make_ledger()
        base_round1(led)
        re_raise(led)  # same fp, no dispositions, no evidence, no runs
        self.assertTrue(fired(led, "no-progress"))

    def test_a_current_withdrawal_is_progress(self):
        # Round-5 F1 — FALSIFICATION: a round-2 closure that SETTLES the
        # round-1 ruling (no re-raise) is lifecycle progress, so no-progress
        # must not fire; the identical round with no such closure is the
        # paired control that must still fire. Mutation: drop `round_settles`
        # from the no-progress condition and the first assertion fails.
        led = make_ledger()
        base_round1(led)
        led.add({"event": "request", "round": 2, "sha": "b" * 40, "bytes": 1})
        led.add({"event": "verdict", "round": 2, "sha": "b" * 40,
                 "verdict": "changes requested", "bytes": 1, "finding_ids": 0})
        led.add({"event": "closure", "round": 2, "fp": FP,
                 "closure": "withdrawn", "ref": "F1"})
        self.assertEqual(led.standing_findings(LINEAGE), [])
        self.assertFalse(fired(led, "no-progress"))

        control = make_ledger()
        base_round1(control)
        control.add({"event": "request", "round": 2, "sha": "b" * 40,
                     "bytes": 1})
        control.add({"event": "verdict", "round": 2, "sha": "b" * 40,
                     "verdict": "changes requested", "bytes": 1,
                     "finding_ids": 0})
        self.assertTrue(fired(control, "no-progress"))

        # Round-6 F2 — FALSIFICATION: a LATER re-raise of the same
        # fingerprint must not retroactively turn round 2's genuine
        # settlement into a no-progress firing; round 3 (nothing answers
        # it) fires on its own. Mutation: filter settlement through the
        # lineage's global current_answers() again (drop `as_of_round`)
        # and the round-2 assertion below fails.
        def re_re_raise(ledger):
            ledger.add({"event": "request", "round": 3, "sha": "c" * 40,
                       "bytes": 1})
            ledger.add({"event": "verdict", "round": 3, "sha": "c" * 40,
                       "verdict": "changes requested", "bytes": 1,
                       "finding_ids": 1})
            ledger.add({"event": "finding", "round": 3, "id": "F1", "fp": FP,
                       "severity": "High", "classification": "design_gap",
                       "title": "t", "preventable_by": None})

        re_re_raise(led)
        led_rounds = {b["round"] for b in fired(led, "no-progress")}
        self.assertNotIn(2, led_rounds,
                         "round 2's own settlement must not un-happen")
        self.assertIn(3, led_rounds, "round 3 itself is genuinely empty")

        re_re_raise(control)
        control_rounds = {b["round"] for b in fired(control, "no-progress")}
        self.assertIn(2, control_rounds)
        self.assertIn(3, control_rounds)

    def test_healthy_converging_round_does_not_trip(self):
        led = make_ledger()
        base_round1(led, disposition="accepted")
        led.add({"event": "request", "round": 2, "sha": "b" * 40, "bytes": 1})
        led.add({"event": "verdict", "round": 2, "sha": "b" * 40,
                 "verdict": "changes requested", "bytes": 1, "finding_ids": 1})
        led.add({"event": "finding", "round": 2, "id": "F1", "fp": OTHER,
                 "severity": "Medium", "classification": "design_gap",
                 "title": "new territory", "preventable_by": None})
        self.assertFalse(led.breakers(LINEAGE, round_cap=3))


class TestF2ProgressBreakersNeedCompletedRounds(unittest.TestCase):
    """FALSIFICATION: Ledgering a request without its verdict fires no
    progress breaker; adding a completed empty repeat round fires it.

    The second half is `TestNoProgress.test_fires_on_a_truly_empty_round`
    above; this class holds the first half and the deliberate exception."""

    def test_request_only_round_fires_no_progress_breaker(self):
        led = make_ledger()
        base_round1(led)
        led.add({"event": "request", "round": 2, "sha": "b" * 40, "bytes": 1})
        progress = [b for b in led.breakers(LINEAGE, round_cap=3)
                    if b["breaker"] in ("no-progress", "repetition", "stale")]
        self.assertEqual(progress, [],
                         "an in-flight round is not a spinning loop")

    def test_budget_still_counts_started_rounds(self):
        # A fourth round that has begun has already spent its budget, so the
        # budget breaker deliberately reads started rounds, not completed.
        led = make_ledger()
        base_round1(led)
        led.add({"event": "request", "round": 4, "sha": "d" * 40, "bytes": 1})
        fired = [b["breaker"] for b in led.breakers(LINEAGE, round_cap=3)]
        self.assertIn("budget", fired)


class TestStale(unittest.TestCase):
    def test_fires_when_accepted_and_passing_test_reappears(self):
        # New evidence exempts `repetition` and `no-progress`; it does NOT
        # exempt `stale` — a proven fix returning is a SHA problem whatever
        # the reviewer attaches to it. (The product-path firing, with no
        # seeded event, is in test_falsification_record.)
        led = make_ledger()
        base_round1(led, disposition="accepted")
        led.add({"event": "falsification_run", "round": 1, "fp": FP,
                 "status": "pass"})
        re_raise(led, {"digest": "sha256:new", "pointer": "x"})
        self.assertTrue(fired(led, "stale"),
                        "the reviewer is on an old SHA or ignoring evidence")


class TestBudget(unittest.TestCase):
    def test_budget_fires_past_the_cap(self):
        led = make_ledger()
        base_round1(led)
        for r in (2, 3, 4):
            led.add({"event": "request", "round": r, "sha": str(r) * 40,
                     "bytes": 1})
            led.add({"event": "verdict", "round": r, "sha": str(r) * 40,
                     "verdict": "changes requested", "bytes": 1,
                     "finding_ids": 0})
            led.add({"event": "finding", "round": r, "id": "F1",
                     "fp": compute("design_gap", "x", "", f"claim {r}"),
                     "severity": "Low", "classification": "design_gap",
                     "title": "t", "preventable_by": None})
        self.assertTrue(fired(led, "budget"))
        led2 = make_ledger()
        base_round1(led2)
        self.assertFalse(fired(led2, "budget"), "round 1 of 3 is within cap")


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


class TestTokenBreakerIsLive(unittest.TestCase):
    """Round-4 F5 — FALSIFICATION: Product-ingested rounds below budget
    remain clean; adding a product-ingested round that crosses the budget
    fires exactly one breaker naming the arithmetic.

    Round-5 fp2:0a2cf43451b3d38f — FALSIFICATION: a ledger where SOME
    envelope events carry counts reports `partial` with the uncounted events
    named — never `live`. That is the fourth input state, one level inside
    the three round 4 named apart."""

    FIXTURE = str(REPO_ROOT / "review/tests/fixtures/mini-verdict.md")

    def _ingest(self, ledger, round_no, tokens):
        # The product path: the same function `main` dispatches to, with the
        # in-memory backend so no writable directory is needed.
        cfg = dataclasses.replace(synth.CFG, ledger_dir=None)
        args = CliArgs(envelope=self.FIXTURE, round=round_no, tokens=tokens)
        with contextlib.redirect_stdout(io.StringIO()):
            with unittest.mock.patch("review.cli._ledger",
                                     return_value=ledger):
                code = cmd_ledger_add(args, cfg)
        self.assertEqual(code, 0)

    def test_below_budget_is_clean_and_over_budget_fires_once(self):
        ledger = Ledger.in_memory()
        self._ingest(ledger, 1, 40_000)
        self._ingest(ledger, 2, 40_000)
        fired = [b for b in ledger.breakers(LINEAGE, round_cap=9, token_budget=100_000)
                 if b.get("limit") == "tokens"]
        self.assertEqual(fired, [], "80k of a 100k budget is not over it")

        self._ingest(ledger, 3, 40_000)
        fired = [b for b in ledger.breakers(LINEAGE, round_cap=9, token_budget=100_000)
                 if b.get("limit") == "tokens"]
        self.assertEqual(len(fired), 1, fired)
        self.assertIn("120000", fired[0]["rule"])
        self.assertIn("100000", fired[0]["rule"])
        self.assertIn("40000 + 40000 + 40000", fired[0]["rule"])

    def test_the_four_input_states_are_partitioned(self):
        # no_budget outranks everything, counted or not.
        self.assertEqual(Ledger.in_memory().token_state(None, LINEAGE)["state"],
                         "no_budget")
        partial = _envelope_events({(1, "request"): 100,
                                    (1, "verdict"): None})
        self.assertEqual(partial.token_state(None, LINEAGE)["state"], "no_budget")
        # no_counts: a budget and nothing counted at all.
        self.assertEqual(Ledger.in_memory().token_state(100, LINEAGE)["state"],
                         "no_counts")
        uncounted = _envelope_events({(1, "request"): None,
                                      (1, "verdict"): None})
        self.assertEqual(uncounted.token_state(1000, LINEAGE)["state"], "no_counts")
        # partial: some counted, the gaps named, the spend a lower bound.
        state = _envelope_events({(1, "request"): 100, (1, "verdict"): None,
                                  (2, "request"): None}).token_state(1000, LINEAGE)
        self.assertEqual(state["state"], "partial")
        self.assertEqual(state["spent"], 100)
        self.assertEqual(state["uncounted"],
                         ["round 1 verdict", "round 2 request"])
        self.assertIn("LOWER BOUND", state["why"])
        # live: every envelope event counted.
        state = _envelope_events({(1, "request"): 100,
                                  (1, "verdict"): 200}).token_state(1000, LINEAGE)
        self.assertEqual(state["state"], "live")
        self.assertEqual(state["spent"], 300)

    def test_no_counts_is_not_a_spend_of_zero(self):
        # The half-state this finding named: arithmetic that looks live
        # because it reports a number. With no counts there is no number.
        ledger = Ledger.in_memory()
        state = ledger.token_state(100, LINEAGE)
        self.assertEqual(state["spent"], 0)
        self.assertIn("absent is not zero", state["why"])
        self.assertEqual(
            [b for b in ledger.breakers(LINEAGE, round_cap=9, token_budget=100)
             if b.get("limit") == "tokens"], [])

    def test_breaker_fires_on_a_lower_bound_breach_and_says_so(self):
        ledger = _envelope_events({(1, "request"): 1500, (1, "verdict"): None})
        fired = [b for b in ledger.breakers(LINEAGE, round_cap=9, token_budget=1000)
                 if b.get("limit") == "tokens"]
        self.assertEqual(len(fired), 1)
        self.assertIn("LOWER BOUND", fired[0]["rule"])

    def test_breaker_stays_silent_under_budget_on_partial(self):
        ledger = _envelope_events({(1, "request"): 100, (1, "verdict"): None})
        self.assertEqual(
            [b for b in ledger.breakers(LINEAGE, round_cap=9, token_budget=1000)
             if b.get("limit") == "tokens"], [])

    def test_the_budget_source_is_config_and_reaches_every_report_path(self):
        self.assertIn("token_budget", synth.CFG.limits)
        ledger = Ledger.in_memory()
        report = ledger.report(LINEAGE, 3, gate_manifest=synth.CFG.gate_ids,
                               token_budget=synth.CFG.token_budget)
        self.assertIn("tokens", report)
        self.assertEqual(report["tokens"]["budget"], synth.CFG.token_budget)


class TestGateManifestReachesTheMetric(unittest.TestCase):
    """Round-4 F8 — FALSIFICATION: CLI and emitted reports compute the same
    manifest-backed value as the ledger API; absent proposed gates remain
    excluded."""

    def _synthetic_ledger(self):
        # Two labelled findings, one naming a real gate id from the manifest,
        # one naming a gate nobody built. (The real corpus counts are
        # asserted by TestCorpusReport above.)
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": "a" * 40})
        ledger.add({"event": "verdict", "round": 1, "sha": "a" * 40,
                    "verdict": "changes requested", "finding_ids": 2})
        ledger.add({"event": "finding", "round": 1, "id": "F1", "fp": "fp2:1",
                    "severity": "High", "preventable_by": synth.CFG.gate_ids[0]})
        ledger.add({"event": "finding", "round": 1, "id": "F2", "fp": "fp2:2",
                    "severity": "High",
                    "preventable_by": "a linter nobody built"})
        return ledger

    def test_cli_report_matches_the_ledger_api(self):
        ledger = self._synthetic_ledger()
        api = ledger.report(LINEAGE, ledger.effective_round_cap(synth.CFG.round_cap, LINEAGE),
                            gate_manifest=synth.CFG.gate_ids,
                            token_budget=synth.CFG.token_budget)
        cli = cli_report(self, ledger, synth.CFG)
        self.assertNotEqual(synth.CFG.gate_ids, [])
        self.assertEqual(cli["gate_manifest"], synth.CFG.gate_ids)
        self.assertTrue(api["metrics"]["rounds"])
        for r in api["metrics"]["rounds"]:
            self.assertEqual(
                cli["metrics"]["rounds"][str(r)]["deterministic_preventable"],
                api["metrics"]["rounds"][r]["deterministic_preventable"])

    def test_the_product_call_is_no_longer_not_computable(self):
        # The defect: `cli.py` and the emitter omitted the manifest, so the
        # product path reported "not computable (no gate manifest declared)"
        # while a manifest was declared two files away.
        rounds = cli_report(self, self._synthetic_ledger(), synth.CFG)["metrics"]["rounds"]
        self.assertTrue(rounds)
        for r, m in rounds.items():
            with self.subTest(round=r):
                self.assertEqual(m["deterministic_preventable"]["count"], 1)
                self.assertNotIn("not computable",
                                 str(m["deterministic_preventable"]["share"]))

    def test_absent_proposed_gates_remain_excluded(self):
        # A label naming a gate that does not exist in the manifest is a
        # candidate, never a count.
        m = self._synthetic_ledger().metrics(LINEAGE,
            gate_manifest=synth.CFG.gate_ids)["rounds"][1]
        self.assertEqual(m["deterministic_preventable"]["count"], 1)
        self.assertEqual(m["deterministic_preventable"]["gates"],
                         [synth.CFG.gate_ids[0]])

    def test_gate_identities_not_display_labels(self):
        # The manifest reaches the metric as the declared ids, never as the
        # commands that implement them (the exact id list is review.toml's
        # to declare — restating it here made this test fail on every
        # manifest change, which is config drift, not a defect).
        self.assertIn("tests", synth.CFG.gate_ids)
        self.assertEqual(synth.CFG.gate_ids, [g["id"] for g in synth.CFG.gates])
        self.assertTrue(all(" " not in gid for gid in synth.CFG.gate_ids))
        self.assertNotIn("python3 -m unittest discover -s review/tests -t .",
                         synth.CFG.gate_ids)
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": "a" * 40})
        ledger.add({"event": "verdict", "round": 1, "sha": "a" * 40,
                    "verdict": "changes requested", "finding_ids": 2})
        ledger.add({"event": "finding", "round": 1, "id": "F1", "fp": "fp2:1",
                    "severity": "High", "preventable_by": "tests"})
        ledger.add({"event": "finding", "round": 1, "id": "F2", "fp": "fp2:2",
                    "severity": "High",
                    "preventable_by": "a doc-consistency linter nobody built"})
        m = ledger.metrics(LINEAGE, gate_manifest=synth.CFG.gate_ids)["rounds"][1]
        self.assertEqual(m["deterministic_preventable"]["count"], 1)
        self.assertEqual(m["deterministic_preventable"]["gates"], ["tests"])

    def test_absent_manifest_is_still_a_distinct_state(self):
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": "a" * 40})
        ledger.add({"event": "verdict", "round": 1, "sha": "a" * 40,
                    "verdict": "changes requested", "finding_ids": 1})
        ledger.add({"event": "finding", "round": 1, "id": "F1", "fp": "fp2:1",
                    "severity": "High", "preventable_by": "some gate"})
        m = ledger.metrics(LINEAGE)["rounds"][1]["deterministic_preventable"]
        self.assertIsNone(m["count"])
        self.assertIn("not computable", m["share"])


if __name__ == "__main__":
    unittest.main()

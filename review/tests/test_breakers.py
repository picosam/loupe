"""Breaker composition (§5.3d), including the two amended rules:
evidence is content-addressed (a new pointer to old bytes is not novelty),
and a refuted finding re-raised with genuinely new evidence trips NEITHER
repetition NOR no-progress.
"""
import tempfile
import unittest
from pathlib import Path

from review.fingerprint import compute
from review.ledger import Ledger

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
    return [b for b in ledger.breakers(round_cap=3,
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

    def test_healthy_converging_round_does_not_trip(self):
        led = make_ledger()
        base_round1(led, disposition="accepted")
        led.add({"event": "request", "round": 2, "sha": "b" * 40, "bytes": 1})
        led.add({"event": "verdict", "round": 2, "sha": "b" * 40,
                 "verdict": "changes requested", "bytes": 1, "finding_ids": 1})
        led.add({"event": "finding", "round": 2, "id": "F1", "fp": OTHER,
                 "severity": "Medium", "classification": "design_gap",
                 "title": "new territory", "preventable_by": None})
        self.assertFalse(led.breakers(round_cap=3))


class TestStale(unittest.TestCase):
    def test_fires_when_accepted_and_passing_test_reappears(self):
        led = make_ledger()
        base_round1(led, disposition="accepted")
        led.add({"event": "falsification_run", "round": 1, "fp": FP,
                 "status": "pass"})
        re_raise(led, {"digest": "sha256:new", "pointer": "x"})
        self.assertTrue(fired(led, "stale"),
                        "the reviewer is on an old SHA or ignoring evidence")


class TestUnverifiableAndBudget(unittest.TestCase):
    def test_unverifiable_fires_on_cannot_execute(self):
        led = make_ledger()
        base_round1(led)
        led.add({"event": "falsification_run", "round": 1, "fp": FP,
                 "status": "cannot_execute"})
        self.assertTrue(fired(led, "unverifiable"))

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


if __name__ == "__main__":
    unittest.main()

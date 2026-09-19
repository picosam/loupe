"""The falsification record an `accepted` disposition carries (§5.3a), the
`falsification_run` event derived from it, and the breakers that read it.

Before this record existed, `falsification_run` had exactly one writer in
the repository: the hand-seeded events in test_breakers.py. The two breakers
that depend on it (`stale`, `unverifiable`) were tested and never reachable
from any product path. This file's last class is the wire end to end: a
disposition validated against a real verdict, recorded through the same
derivation `respond --out` and `ledger add` use, and a breaker firing from
what it recorded — no event seeded by hand.
"""
import unittest

from review import transport, validate, wire
from review.digest import sha256_text
from review.ledger import Ledger
from review.tests.test_validate import (CFG, FINDING, SHA, disposition_envelope,
                                        errs, record, verdict_text)
from review.tests.util import LINEAGE

RUN = {"status": "pass", "mutation": "fails_without_fix"}


def verdict(fals="run x", sev="Medium", n=1):
    blocks = []
    for i in range(1, n + 1):
        blocks.append(FINDING.format(sev=sev, fals=fals)
                      .replace("### F1", f"### F{i}")
                      .replace("not provided", f"not provided ({i})"))
    body = "VERDICT: changes requested\n\n## findings\n\n" + "\n".join(blocks)
    return wire.parse_verdict(verdict_text(body))


def accepted(falsification=None, subtype=None, extra=None):
    payload = {"change": "fixed", "verification": "the named test passes"}
    if falsification is not None:
        payload["falsification"] = falsification
    if extra:
        payload.update(extra)
    return record("F1", "accepted", subtype=subtype, payload=payload)


def check(rec, against):
    d = wire.parse_disposition(disposition_envelope([rec]))
    return errs(validate.validate_disposition(d, CFG, against=against))


def falsification_errors(codes):
    return {c for c in codes if c.startswith("D-FALSIFICATION")}


class TestRecordRequired(unittest.TestCase):
    def test_a_named_test_requires_a_recorded_run(self):
        self.assertIn("D-FALSIFICATION-MISSING",
                      check(accepted(), verdict(fals="run x")))

    def test_a_recorded_run_satisfies_it(self):
        self.assertEqual(falsification_errors(
            check(accepted(RUN), verdict(fals="run x"))), set())

    def test_no_named_test_needs_no_record(self):
        # A non-blocking finding may carry no falsification test at all;
        # there is then nothing to run and nothing to demand.
        self.assertEqual(falsification_errors(
            check(accepted(), verdict(fals=""))), set())

    def test_only_acceptance_carries_the_requirement(self):
        rec = record("F1", "refuted", payload={"evidence": "it is not so"})
        self.assertEqual(falsification_errors(
            check(rec, verdict(fals="run x"))), set())

    def test_without_the_verdict_only_the_shape_is_judged(self):
        # No `against`: the validator cannot know whether a test was named,
        # so absence is not an error here — but a malformed record still is.
        d = wire.parse_disposition(disposition_envelope([accepted()]))
        self.assertEqual(falsification_errors(
            errs(validate.validate_disposition(d, CFG))), set())
        d = wire.parse_disposition(
            disposition_envelope([accepted("passed, trust me")]))
        self.assertIn("D-FALSIFICATION",
                      errs(validate.validate_disposition(d, CFG)))


class TestRecordShape(unittest.TestCase):
    def test_both_axes_are_required(self):
        self.assertIn("D-FALSIFICATION",
                      check(accepted({"status": "pass"}), verdict()))
        self.assertIn("D-FALSIFICATION",
                      check(accepted({"mutation": "fails_without_fix"}),
                            verdict()))

    def test_both_vocabularies_are_closed(self):
        self.assertIn("D-FALSIFICATION",
                      check(accepted({"status": "green",
                                      "mutation": "fails_without_fix"}),
                            verdict()))
        self.assertIn("D-FALSIFICATION",
                      check(accepted({"status": "pass",
                                      "mutation": "checked"}), verdict()))


class TestContradictions(unittest.TestCase):
    def test_a_test_that_still_fails_is_not_an_acceptance(self):
        self.assertIn("D-FALSIFICATION-FAILS",
                      check(accepted({"status": "fail",
                                      "mutation": "fails_without_fix"}),
                            verdict()))

    def test_a_test_that_passes_without_the_fix_is_inert(self):
        # Round 5 F1 of this tool's own review: the test ratified the bug in
        # a comment. This is that state, named, and refused.
        self.assertIn("D-FALSIFICATION-INERT",
                      check(accepted({"status": "pass",
                                      "mutation": "passes_without_fix"}),
                            verdict()))

    def test_unexecutable_contradicts_a_mutation_result(self):
        codes = check(accepted({"status": "cannot_execute",
                                "mutation": "fails_without_fix",
                                "note": "no runner"}), verdict())
        self.assertIn("D-FALSIFICATION", codes)


class TestUnexecutedStatesNeedAReason(unittest.TestCase):
    def test_cannot_execute_without_a_note_is_silence(self):
        rec = accepted({"status": "cannot_execute", "mutation": "not_run"})
        self.assertIn("D-FALSIFICATION-SILENT", check(rec, verdict()))

    def test_cannot_execute_with_a_reason_is_a_recorded_state(self):
        rec = accepted({"status": "cannot_execute", "mutation": "not_run",
                        "note": "the test needs a second clone this "
                                "sandbox cannot make"})
        self.assertEqual(falsification_errors(check(rec, verdict())), set())

    def test_mutation_not_run_without_a_note_is_silence(self):
        rec = accepted({"status": "pass", "mutation": "not_run"})
        self.assertIn("D-FALSIFICATION-SILENT", check(rec, verdict()))

    def test_mutation_not_run_with_a_reason_is_a_recorded_state(self):
        rec = accepted({"status": "pass", "mutation": "not_run",
                        "note": "observation kind: nothing to reintroduce"})
        self.assertEqual(falsification_errors(check(rec, verdict())), set())


class TestRunEvent(unittest.TestCase):
    def events_for(self, rec, against):
        d = wire.parse_disposition(disposition_envelope([rec]))
        return [e for e in transport.disposition_events(d, against)
                if e["event"] == "falsification_run"]

    def test_an_accepted_record_becomes_one_run_event(self):
        v = verdict(fals="command: python3 -m unittest x")
        runs = self.events_for(accepted(RUN), v)
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual(run["fp"], v.findings[0].fingerprint())
        self.assertEqual(run["of"], "F1")
        self.assertEqual(run["status"], "pass")
        self.assertEqual(run["mutation"], "fails_without_fix")
        self.assertEqual(run["source"], "disposition")
        self.assertEqual(run["round"], 2)
        self.assertNotIn("note", run)

    def test_the_event_binds_to_the_named_test_by_digest(self):
        v = verdict(fals="command: python3 -m unittest x")
        run = self.events_for(accepted(RUN), v)[0]
        self.assertEqual(run["test_digest"],
                         sha256_text("command: python3 -m unittest x"))

    def test_an_amended_test_is_the_one_bound(self):
        v = verdict(fals="command: the unsatisfiable one")
        rec = accepted(RUN, subtype="test_amended", extra={
            "original_test": "command: the unsatisfiable one",
            "amended_test": "command: the one that can pass",
            "why_unsatisfiable": "it asserted a path that cannot exist"})
        run = self.events_for(rec, v)[0]
        self.assertEqual(run["test_digest"],
                         sha256_text("command: the one that can pass"))

    def test_the_note_travels_when_present(self):
        run = self.events_for(accepted({"status": "pass",
                                        "mutation": "not_run",
                                        "note": "observation kind"}),
                              verdict())[0]
        self.assertEqual(run["note"], "observation kind")

    def test_no_record_no_event(self):
        self.assertEqual(self.events_for(accepted(), verdict(fals="")), [])

    def test_only_acceptance_produces_one(self):
        rec = record("F1", "refuted", payload={"evidence": "not so"})
        self.assertEqual(self.events_for(rec, verdict()), [])


class TestBreakersReadTheProductPath(unittest.TestCase):
    """No event seeded by hand: the finding comes from `verdict_events`, the
    run from `disposition_events`, and the breaker reads what they wrote."""

    def round_one(self, led, run, sev="High"):
        v = verdict(fals="run x", sev=sev)
        led.add({"event": "request", "round": 1, "sha": SHA, "bytes": 10})
        led.add_all(transport.verdict_events(v, 1, "sha256:v1", 10))
        d = wire.parse_disposition(wire.emit_disposition(
            "loupe", SHA, "8" * 40, "claude", 1, [accepted(run)]))
        self.assertEqual(
            {c for c in errs(validate.validate_disposition(d, CFG, against=v))
             if c.startswith("D-FALSIFICATION")}, set())
        # The product path: `respond --out` and `ledger add` both hand the
        # config in, so the run is bound to its finding's blocking state.
        led.add_all(transport.disposition_events(d, v, CFG))
        return v

    CANNOT = {"status": "cannot_execute", "mutation": "not_run",
              "note": "needs a runner this sandbox lacks"}

    def test_unverifiable_fires_from_a_recorded_cannot_execute(self):
        led = Ledger.in_memory()
        self.round_one(led, self.CANNOT)
        names = [b["breaker"] for b in led.breakers(LINEAGE, round_cap=3)]
        self.assertIn("unverifiable", names)

    def test_nonblocking_cannot_execute_does_not_fire_unverifiable(self):
        """FALSIFICATION for sweep F12 (Medium). The breaker is defined over
        a BLOCKING finding's test (public design §5.3d) and fired for every
        `cannot_execute` run — a Medium finding's escalated with a rule text
        naming a severity it did not have. Product path, no seeded event:
        the finding from verdict_events, the run from disposition_events.
        Mutation: drop the severity join (fire on every cannot_execute) and
        the Medium case fires — this fails — while the High control below
        still fires either way."""
        led = Ledger.in_memory()
        self.round_one(led, self.CANNOT, sev="Medium")
        run = next(e for e in led.events()
                   if e["event"] == "falsification_run")
        self.assertIs(run["blocking"], False,
                      "the run is bound to its finding's blocking state")
        names = [b["breaker"] for b in led.breakers(LINEAGE, round_cap=3)]
        self.assertNotIn("unverifiable", names)
        # High control: fires, and its rule text is now true.
        led2 = Ledger.in_memory()
        self.round_one(led2, self.CANNOT, sev="High")
        fired = [b for b in led2.breakers(LINEAGE, round_cap=3)
                 if b["breaker"] == "unverifiable"]
        self.assertEqual(len(fired), 1)
        self.assertIn("blocking finding", fired[0]["rule"])
        # Blocker as well — the whole declared blocking set, not one member.
        led3 = Ledger.in_memory()
        self.round_one(led3, self.CANNOT, sev="Blocker")
        self.assertIn("unverifiable",
                      [b["breaker"] for b in led3.breakers(LINEAGE, round_cap=3)])

    def test_a_run_without_a_stamp_is_joined_through_its_finding(self):
        # Legacy or hand-seeded runs carry no `blocking`; the breaker joins
        # them to the finding event and judges against the supplied set.
        # The run binds to its answer by legacy order (a preceding row for
        # its identity) — a run with NO row is an orphan, judged below.
        led = Ledger.in_memory()
        v = verdict(fals="run x", sev="High")
        led.add({"event": "request", "round": 1, "sha": SHA, "bytes": 10})
        led.add_all(transport.verdict_events(v, 1, "sha256:v1", 10))
        fp = v.findings[0].fingerprint()
        led.add({"event": "disposition", "round": 1, "finding_id": "F1",
                 "fp": fp, "disposition": "accepted", "payload": {},
                 "verdict_sha": SHA, "head": "b" * 40})
        led.add({"event": "falsification_run", "round": 1, "fp": fp,
                 "status": "cannot_execute"})
        self.assertIn("unverifiable", [
            b["breaker"] for b in led.breakers(LINEAGE,
                round_cap=3, blocking_severities=CFG.blocking_severities)])
        # Without a set to judge by, it does not claim a severity it
        # cannot establish.
        self.assertNotIn("unverifiable",
                         [b["breaker"] for b in led.breakers(LINEAGE, round_cap=3)])

    def test_a_rowless_run_escalates_as_an_orphan_not_as_a_verdict(self):
        # Round 3 F1: a run that answers no recorded emission must neither
        # certify nor impersonate one — it fires the `orphan` breaker,
        # blocking set or none, and `unverifiable` (a claim about a
        # recorded answer's test) stays silent.
        led = Ledger.in_memory()
        v = verdict(fals="run x", sev="High")
        led.add({"event": "request", "round": 1, "sha": SHA, "bytes": 10})
        led.add_all(transport.verdict_events(v, 1, "sha256:v1", 10))
        led.add({"event": "falsification_run", "round": 1,
                 "fp": v.findings[0].fingerprint(),
                 "status": "cannot_execute"})
        for severities in (CFG.blocking_severities, None):
            names = [b["breaker"] for b in led.breakers(LINEAGE,
                round_cap=3, blocking_severities=severities)]
            self.assertIn("orphan", names)
            self.assertNotIn("unverifiable", names)

    def test_stale_fires_when_a_proven_fix_returns(self):
        led = Ledger.in_memory()
        v = self.round_one(led, RUN)
        # Round 2 re-raises the same finding, unchanged, on a new tip.
        led.add({"event": "request", "round": 2, "sha": "b" * 40, "bytes": 10})
        led.add_all(transport.verdict_events(v, 2, "sha256:v2", 10))
        names = [b["breaker"] for b in led.breakers(LINEAGE, round_cap=3)]
        self.assertIn("stale", names)

    def test_a_proven_run_is_progress(self):
        led = Ledger.in_memory()
        self.round_one(led, RUN)
        names = [b["breaker"] for b in led.breakers(LINEAGE, round_cap=3)]
        self.assertNotIn("unverifiable", names)
        self.assertNotIn("no-progress", names)


class TestAcceptanceMetric(unittest.TestCase):
    """§5.4 unverified-acceptance read a payload key nothing wrote; it now
    reads the run events and keeps the two absences apart."""

    def metric(self, led):
        return led.metrics(LINEAGE, gate_manifest=[])["rounds"][1]["unverified_acceptance"]

    def seed(self, led, run, fals="run x"):
        v = verdict(fals=fals)
        led.add({"event": "request", "round": 1, "sha": SHA, "bytes": 10})
        led.add_all(transport.verdict_events(v, 1, "sha256:v1", 10))
        d = wire.parse_disposition(wire.emit_disposition(
            "loupe", SHA, "8" * 40, "claude", 1, [accepted(run)]))
        led.add_all(transport.disposition_events(d, v))

    def test_proven(self):
        led = Ledger.in_memory()
        self.seed(led, RUN)
        self.assertEqual(self.metric(led),
                         {"test passed, mutation proven": 1})

    def test_unproven(self):
        led = Ledger.in_memory()
        self.seed(led, {"status": "pass", "mutation": "not_run", "note": "n"})
        self.assertEqual(self.metric(led),
                         {"test passed, mutation not run": 1})

    def test_unexecutable(self):
        led = Ledger.in_memory()
        self.seed(led, {"status": "cannot_execute", "mutation": "not_run",
                        "note": "n"})
        self.assertEqual(self.metric(led),
                         {"test could not be executed": 1})

    def test_the_two_absences_are_different_states(self):
        led = Ledger.in_memory()
        self.seed(led, None, fals="")
        self.assertEqual(self.metric(led), {"no test named": 1})
        led = Ledger.in_memory()
        # A named test and no record — the legacy shape, recorded straight
        # into the ledger as lineage 2's dispositions were.
        v = verdict(fals="run x")
        led.add({"event": "request", "round": 1, "sha": SHA, "bytes": 10})
        led.add_all(transport.verdict_events(v, 1, "sha256:v1", 10))
        led.add({"event": "disposition", "round": 1, "finding_id": "F1",
                 "fp": v.findings[0].fingerprint(), "disposition": "accepted",
                 "payload": {"change": "x", "verification": "y"}})
        self.assertEqual(self.metric(led), {"named test, no run recorded": 1})


if __name__ == "__main__":
    unittest.main()

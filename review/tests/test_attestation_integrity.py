"""RVW-T2: the three ways a §5.1 attestation that passed every field check
could still lie — round-5 findings fp2:48dfa913b5cc270e (request binding),
fp2:117659ca19397907 (self-declared blocking), fp2:3563b8cede30a6cf (not-run
records skip the evidence requirements).

Each class carries the finding's own falsification, then the states adjacent
to it (slice-1 lesson 1: passing the named test is necessary and nowhere near
sufficient).
"""
import dataclasses
import unittest

from review import validate
from review.tests.test_round4_fixes import (ONE_GATE, complete_attestation,
                                            evidence_with)

REQUEST_SHA = "9" * 40
OTHER_SHA = "b" * 40


def codes(items, level=None):
    return {i.code for i in items if level is None or i.level == level}


class TestRequestBinding(unittest.TestCase):
    """FALSIFICATION (a): a wholesale attestation block from another commit —
    internally consistent, bound, exit 0 — validates against a request for a
    different SHA. It must not."""

    def test_a_block_from_another_commit_fails_the_request(self):
        rec = complete_attestation(target_sha=OTHER_SHA,
                                   executed_sha=OTHER_SHA)
        items = validate.validate_attestations(
            evidence_with([rec]), ONE_GATE, request_sha=REQUEST_SHA)
        self.assertIn("A-REQUEST-SHA", codes(items, "error"))
        # The block really is internally consistent — the ONLY defect is the
        # request binding, which is what makes this the named lie.
        self.assertNotIn("A-SHA-MISMATCH", codes(items))
        self.assertNotIn("A-UNBOUND", codes(items))

    def test_a_matching_block_passes(self):
        rec = complete_attestation(target_sha=REQUEST_SHA,
                                   executed_sha=REQUEST_SHA)
        items = validate.validate_attestations(
            evidence_with([rec]), ONE_GATE, request_sha=REQUEST_SHA)
        self.assertNotIn("A-REQUEST-SHA", codes(items))

    def test_without_a_request_sha_the_check_reports_nothing(self):
        # Adjacent state: a caller that cannot supply the request SHA gets
        # the old behaviour, not a spurious failure against None.
        rec = complete_attestation(target_sha=OTHER_SHA,
                                   executed_sha=OTHER_SHA)
        items = validate.validate_attestations(evidence_with([rec]), ONE_GATE)
        self.assertNotIn("A-REQUEST-SHA", codes(items))

    def test_both_defects_fire_when_both_exist(self):
        # Adjacent state: request mismatch AND internal mismatch are two
        # different lies and each is reported on its own.
        rec = complete_attestation(target_sha=OTHER_SHA)
        items = validate.validate_attestations(
            evidence_with([rec]), ONE_GATE, request_sha=REQUEST_SHA)
        self.assertIn("A-REQUEST-SHA", codes(items, "error"))
        self.assertIn("A-SHA-MISMATCH", codes(items, "error"))


class TestManifestDecidesBlocking(unittest.TestCase):
    """FALSIFICATION (b): a record declaring itself non-blocking downgrades a
    manifest-blocking failure to advisory. Blocking is the manifest's to
    decide, never the record's."""

    def test_self_declared_downgrade_no_longer_works(self):
        rec = complete_attestation(exit_code=1, blocking=False)
        items = validate.validate_attestations(evidence_with([rec]), ONE_GATE)
        self.assertIn("A-FAILED", codes(items, "error"),
                      "the manifest says blocking, so the failure is fatal")
        self.assertIn("A-BLOCKING-CLAIM", codes(items, "error"),
                      "the record's false claim is itself a defect")

    def test_self_declared_downgrade_of_not_run_no_longer_works(self):
        rec = {"id": "tests", "blocking": False, "error": "skipped"}
        items = validate.validate_attestations(evidence_with([rec]), ONE_GATE)
        self.assertIn("A-NOT-RUN", codes(items, "error"))
        self.assertIn("A-BLOCKING-CLAIM", codes(items, "error"))

    def test_an_absent_blocking_field_is_not_a_contradiction(self):
        # Adjacent state: absent is not a false claim; the manifest still
        # decides, and the failure is still fatal.
        rec = complete_attestation(exit_code=1)
        del rec["blocking"]
        items = validate.validate_attestations(evidence_with([rec]), ONE_GATE)
        self.assertIn("A-FAILED", codes(items, "error"))
        self.assertNotIn("A-BLOCKING-CLAIM", codes(items))

    def test_a_truthful_blocking_claim_raises_nothing(self):
        rec = complete_attestation()
        items = validate.validate_attestations(evidence_with([rec]), ONE_GATE)
        self.assertNotIn("A-BLOCKING-CLAIM", codes(items))

    def test_upgrade_lies_are_also_claims(self):
        # Adjacent state to the downgrade: a record claiming MORE authority
        # than the manifest grants is the same defect in the other direction.
        soft = dataclasses.replace(ONE_GATE, gates=[
            {"id": "tests", "command": ["true"], "blocking": False}])
        rec = complete_attestation(blocking=True)
        items = validate.validate_attestations(evidence_with([rec]), soft)
        self.assertIn("A-BLOCKING-CLAIM", codes(items, "error"))


class TestNotRunRecordsMeetEvidenceRequirements(unittest.TestCase):
    """FALSIFICATION (c): the one record shape that means "no evidence" was
    the one shape with no evidence requirements."""

    def test_a_not_run_record_without_a_reason_fails(self):
        rec = {"id": "tests", "blocking": True, "error": ""}
        items = validate.validate_attestations(evidence_with([rec]), ONE_GATE)
        self.assertIn("A-NOT-RUN-SHAPE", codes(items, "error"))

    def test_a_not_run_record_without_a_gate_id_fails(self):
        rec = {"blocking": True, "error": "could not execute"}
        items = validate.validate_attestations(evidence_with([rec]), ONE_GATE)
        self.assertIn("A-NOT-RUN-SHAPE", codes(items, "error"))

    def test_run_only_fields_contradict_not_run(self):
        # A record claiming both "could not run" and run-evidence is not a
        # richer record; it is an incoherent one.
        rec = {"id": "tests", "blocking": True, "error": "skipped",
               "exit_code": 0}
        items = validate.validate_attestations(evidence_with([rec]), ONE_GATE)
        self.assertIn("A-NOT-RUN-SHAPE", codes(items, "error"))

    def test_a_well_formed_not_run_record_reports_only_its_state(self):
        # Adjacent-clean state: the emitter's own not-run shape — id, honest
        # reason, truthful blocking — yields exactly the A-NOT-RUN state and
        # no shape defect.
        rec = {"id": "tests", "blocking": True,
               "error": "not run: nested inside a gate execution"}
        items = validate.validate_attestations(evidence_with([rec]), ONE_GATE)
        self.assertNotIn("A-NOT-RUN-SHAPE", codes(items))
        self.assertIn("A-NOT-RUN", codes(items, "error"))


if __name__ == "__main__":
    unittest.main()

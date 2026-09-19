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

from review import emit, validate, vocab
from review.tests.synth import ONE_GATE, complete_attestation, evidence_with

REQUEST_SHA = "9" * 40
OTHER_SHA = "b" * 40


def codes(items, level=None):
    return {i.code for i in items if level is None or i.level == level}


def errs(items):
    return codes(items, "error")


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


class TestF3AttestationSignalsAreIndependentlyRequired(unittest.TestCase):
    """Round-4 F3 — FALSIFICATION: Removing or corrupting each §5.1
    attestation field independently produces a specific error, while one
    complete record validates."""

    REQUIRED = ("id", "command", "exit_code", "tool_version", "target_sha",
                "executed_sha", "tree", "binding", "duration_s", "output")

    def _errs(self, records, cfg=ONE_GATE):
        return errs(validate.validate_attestations(
            evidence_with(records), cfg))

    def test_one_complete_record_validates(self):
        self.assertEqual(self._errs([complete_attestation()]), set())

    def test_removing_each_required_field_fails_on_its_own(self):
        for field in self.REQUIRED:
            rec = complete_attestation()
            del rec[field]
            with self.subTest(removed=field):
                self.assertTrue(self._errs([rec]),
                                f"removing {field} produced no error")

    def test_corrupting_each_required_field_fails_on_its_own(self):
        corruptions = {
            "id": "", "command": "", "exit_code": "0",
            "tool_version": "", "target_sha": "not-a-sha",
            "executed_sha": "not-a-sha", "tree": "probably clean",
            "binding": "", "duration_s": "fast",
            "output": {"sha256": "a" * 64},  # no pointer, no size
        }
        for field, bad in corruptions.items():
            with self.subTest(corrupted=field):
                self.assertTrue(self._errs([complete_attestation(**{field: bad})]),
                                f"corrupting {field} produced no error")

    def test_the_defect_this_finding_names_is_gone(self):
        # The round-4 Evidence: "Removing exit-code and target-SHA text from
        # the emitted round-4 request still produced zero validation errors."
        stripped = complete_attestation()
        del stripped["exit_code"]
        del stripped["target_sha"]
        self.assertEqual(self._errs([stripped]), {"A-EXIT", "A-TARGET-SHA",
                                                  "A-SHA-MISMATCH"})

    def test_a_missing_block_is_not_a_clean_block(self):
        self.assertIn("A-BLOCK", errs(validate.validate_attestations(
            "Machine attestations: everything passed.\nNOT captured: none",
            ONE_GATE)))

    def test_a_declared_gate_with_no_attestation_fails(self):
        two = dataclasses.replace(ONE_GATE, gates=[
            {"id": "tests", "command": ["true"], "blocking": True},
            {"id": "whitespace", "command": ["true"], "blocking": True}])
        self.assertIn("A-MISSING", self._errs([complete_attestation()], two))

    def test_a_blocking_gate_that_could_not_run_is_fatal(self):
        self.assertIn("A-NOT-RUN", self._errs(
            [{"id": "tests", "blocking": True,
              "error": "not run: nested inside a gate execution"}]))

    def test_a_blocking_gate_that_ran_and_failed_is_fatal(self):
        # Found by the first real emission under this validator, not by
        # design: the nested suite exited 1, the attestation recorded
        # `exit_code: 1` accurately, and the request validated CLEAN. Reporting
        # a failure faithfully is not the same as the failure not counting —
        # the same container-versus-contents defect F3 named, committed by the
        # fix for it.
        self.assertIn("A-FAILED",
                      self._errs([complete_attestation(exit_code=1)]))

    # Rewritten for RVW-T2(b): the original versions declared blocking=False
    # in the RECORD while the manifest said True, and asserted a notice —
    # asserting the exact self-downgrade the finding names. Non-blocking now
    # means the MANIFEST says so.
    SOFT_GATE = dataclasses.replace(
        ONE_GATE,
        gates=[{"id": "tests", "command": ["true"], "blocking": False}])

    def test_a_nonblocking_gate_that_failed_is_a_notice(self):
        items = validate.validate_attestations(evidence_with(
            [complete_attestation(exit_code=1, blocking=False)]),
            self.SOFT_GATE)
        self.assertEqual(errs(items), set())
        self.assertIn("A-FAILED", {i.code for i in items})

    def test_a_nonblocking_gate_that_could_not_run_is_a_notice(self):
        items = validate.validate_attestations(evidence_with(
            [{"id": "tests", "blocking": False, "error": "could not execute"}]),
            self.SOFT_GATE)
        self.assertEqual(errs(items), set())
        self.assertIn("A-NOT-RUN", {i.code for i in items})


class TestACIAttestedRowClaimsOnlyWhatCIRan(unittest.TestCase):
    """The three lies above, retried by a row whose EXECUTOR was CI (brief
    `ci-attested-gates`, 2026-09-07).

    A gate may now declare `attested_by = "ci"`, and the runner records the
    Actions run's identity and conclusion instead of executing the command
    here. That adds keys to the record — `attested_by`, `ci_run` — and an
    added key must buy no immunity: every check in this module is about what
    a row CLAIMS, and who ran the command changes none of them. The rows here
    are the exact shapes `emit._ci_attestation` and `emit._ci_not_run` build;
    `test_ci_attested_gates` proves the runner builds them, this proves the
    validator still judges them.
    """

    RUN = {"id": 4242, "url": "https://github.com/acme/widget/actions/runs/4242",
           "status": "completed", "conclusion": "success",
           "head_sha": REQUEST_SHA, "workflow": "gates",
           "completed": "2026-09-07T10:00:00Z"}

    def ci_row(self, **over) -> dict:
        rec = complete_attestation(
            attested_by="ci", ci_run=dict(self.RUN),
            tool_version="attested by ci: acme/widget gates run 4242 (success)",
            target_sha=REQUEST_SHA, executed_sha=REQUEST_SHA)
        # Round-3 F2/F4: the receipt row CI wrote for this gate, which the
        # validator now requires beside the run.
        rec["receipt"] = {"id": rec["id"], "command": rec["command"],
                          "exit_code": 0, "duration_s": 1.5, "not_run": None,
                          "artifact": vocab.ci_receipt_artifact(REQUEST_SHA),
                          "sha": REQUEST_SHA, "tool_version": "loupe/test",
                          "schema": vocab.CI_RECEIPT_SCHEMA}
        rec.update(over)
        return rec

    # ---- round-3 F4: the receipt and the run are validated on their own
    # terms, independently of the outer row, which stays green throughout.

    def _codes(self, rec):
        return errs(validate.validate_attestations(
            evidence_with([rec]), ONE_GATE, request_sha=REQUEST_SHA))

    def test_every_ci_run_field_is_required_and_typed(self):
        for name, code, _ok, _why in validate._CI_RUN_FIELDS:
            with self.subTest(field=name, mutation="deleted"):
                rec = self.ci_row(); del rec["ci_run"][name]
                self.assertIn(code, self._codes(rec))
            with self.subTest(field=name, mutation="wrong type"):
                rec = self.ci_row(); rec["ci_run"][name] = [] if name != "id" else {}
                self.assertIn(code, self._codes(rec))

    #: A value that fails each declared kind, so the parametrised test below
    #: needs no per-field knowledge of its own.
    WRONG_FOR_KIND = {"nonempty": 7, "int": {}, "number": {},
                      "text_or_none": 7, "sha": "not-a-sha"}

    def test_every_declared_receipt_field_is_required_and_typed(self):
        """FALSIFICATION for round-3 F3.

        Derived from `vocab.CI_RECEIPT_ROW` — the mapping `emit` BUILDS the
        row from — rather than from the validator's own table, which is what
        the finding named: the old test read `validate._CI_RECEIPT_FIELDS`,
        so shrinking that table shrank what "every field" meant and the six
        unchecked fields were invisible to the test and the validator alike.

        MUTATION: drop a field from the authority and the emitted row loses
        it too, so the receipt stops carrying what the record needs; narrow
        a kind and the wrong-type case for that field goes green.
        """
        for name, (code, kind, _why) in vocab.CI_RECEIPT_ROW.items():
            with self.subTest(field=name, mutation="deleted"):
                rec = self.ci_row(); del rec["receipt"][name]
                self.assertIn(code, self._codes(rec))
            with self.subTest(field=name, mutation="wrong type"):
                rec = self.ci_row()
                rec["receipt"][name] = self.WRONG_FOR_KIND[kind]
                self.assertIn(code, self._codes(rec))

    def test_what_ci_emits_per_gate_is_declared_in_the_authority(self):
        """The completeness direction the old test could not reach: every
        key CI's OWN receipt writes for a gate is a key this validator was
        told about. A new field in `gate_receipt` with no entry in the
        authority fails here rather than riding into a record unchecked."""
        gates = [{"id": "heavy", "command": ["true"], "blocking": True}]
        doc = emit.gate_receipt(
            REQUEST_SHA, gates,
            {"heavy": {"id": "heavy", "command": "true", "exit_code": 0,
                       "duration_s": 1.5}})
        [row] = doc["gates"]
        undeclared = sorted(set(row) - set(vocab.CI_RECEIPT_ROW))
        self.assertEqual(undeclared, [],
                         "CI writes a per-gate field the receipt authority "
                         "does not declare, so nothing validates it")
        # The document's own fields travel into the row beside the gate's,
        # and they are declared too — all but `artifact`, which is this
        # tool's name for where the document travelled rather than
        # something CI wrote inside it.
        document = sorted(set(doc) - {"gates"} - set(vocab.CI_RECEIPT_ROW))
        self.assertEqual(document, [])
        self.assertIn("artifact", vocab.CI_RECEIPT_ROW)

    def test_a_receipt_in_another_grammar_is_refused(self):
        rec = self.ci_row()
        rec["receipt"]["schema"] = "loupe-gate-receipt/99"
        self.assertIn("A-CI-RECEIPT-GRAMMAR", self._codes(rec))

    def test_a_receipt_about_another_commit_is_refused(self):
        # The outer row stays green: only the receipt's own SHA moves, and
        # its artifact name moves with it so the name check is not what
        # fires.
        rec = self.ci_row()
        rec["receipt"]["sha"] = OTHER_SHA
        rec["receipt"]["artifact"] = vocab.ci_receipt_artifact(OTHER_SHA)
        codes = self._codes(rec)
        self.assertIn("A-CI-RECEIPT-TARGET", codes)
        self.assertNotIn("A-CI-RECEIPT-ARTIFACT-NAME", codes)

    def test_an_artifact_name_that_does_not_carry_its_commit_is_refused(self):
        rec = self.ci_row()
        rec["receipt"]["artifact"] = vocab.ci_receipt_artifact(OTHER_SHA)
        codes = self._codes(rec)
        self.assertIn("A-CI-RECEIPT-ARTIFACT-NAME", codes)
        self.assertNotIn("A-CI-RECEIPT-TARGET", codes)

    def test_a_missing_run_or_receipt_object_is_named(self):
        rec = self.ci_row(); del rec["ci_run"]
        self.assertIn("A-CI-RUN", self._codes(rec))
        rec = self.ci_row(); del rec["receipt"]
        self.assertIn("A-CI-RECEIPT", self._codes(rec))

    def test_only_the_nested_values_change_and_the_outer_row_stays_green(self):
        # The outer row keeps exit_code 0, binding bound, executed_sha at
        # the target; only what the receipt or the run says is altered.
        cases = {
            "A-CI-SHA": ("ci_run", "head_sha", OTHER_SHA),
            "A-CI-CONCLUSION": ("ci_run", "conclusion", "failure"),
            "A-CI-INCOMPLETE": ("ci_run", "status", "in_progress"),
            "A-CI-NOT-RUN": ("receipt", "not_run", "declared not-run in CI"),
            "A-CI-GATE": ("receipt", "id", "some-other-gate"),
            "A-CI-COMMAND": ("receipt", "command", "echo not-the-command"),
            "A-CI-EXIT": ("receipt", "exit_code", 3),
        }
        for code, (obj, key, value) in cases.items():
            with self.subTest(code=code):
                rec = self.ci_row(); rec[obj][key] = value
                self.assertIn(code, self._codes(rec))
                self.assertEqual(rec["exit_code"], 0)
                self.assertEqual(rec["executed_sha"], REQUEST_SHA)

    def test_the_receipt_must_agree_with_the_request_target(self):
        rec = self.ci_row()
        items = validate.validate_attestations(
            evidence_with([rec]), ONE_GATE, request_sha=OTHER_SHA)
        self.assertIn("A-CI-REQUEST-SHA", errs(items))

    def test_local_and_honest_not_run_rows_are_untouched_by_the_ci_checks(self):
        local = complete_attestation(target_sha=REQUEST_SHA,
                                     executed_sha=REQUEST_SHA)
        self.assertNotIn("A-CI-RUN", self._codes(local))
        self.assertNotIn("A-CI-RECEIPT", self._codes(local))

    def test_a_complete_ci_row_validates(self):
        items = validate.validate_attestations(
            evidence_with([self.ci_row()]), ONE_GATE, request_sha=REQUEST_SHA)
        self.assertEqual(errs(items), set())

    def test_a_ci_row_still_needs_every_required_field(self):
        # The added keys are not a substitute for the required ones: an
        # `attested_by` that excused a missing exit code would let a row say
        # "CI attested this" and never say what CI concluded.
        for field in ("exit_code", "command", "target_sha", "output",
                      "binding"):
            with self.subTest(removed=field):
                rec = self.ci_row()
                del rec[field]
                self.assertTrue(errs(validate.validate_attestations(
                    evidence_with([rec]), ONE_GATE)),
                    f"removing {field} from a CI row produced no error")

    def test_a_failing_ci_conclusion_is_fatal_for_a_blocking_gate(self):
        rec = self.ci_row(exit_code=1,
                          ci_run={**self.RUN, "conclusion": "failure"})
        self.assertIn("A-FAILED", errs(validate.validate_attestations(
            evidence_with([rec]), ONE_GATE)))

    def test_a_green_ci_run_for_another_commit_is_not_this_requests_evidence(self):
        # RVW-T2(a) in its CI form, and the one this mechanism makes easy to
        # get wrong: a run that really did pass, really did complete, and is
        # about a different commit.
        rec = self.ci_row(target_sha=OTHER_SHA, executed_sha=OTHER_SHA,
                          ci_run={**self.RUN, "head_sha": OTHER_SHA})
        self.assertIn("A-REQUEST-SHA", errs(validate.validate_attestations(
            evidence_with([rec]), ONE_GATE, request_sha=REQUEST_SHA)))

    def test_an_unattested_ci_gate_meets_the_not_run_requirements(self):
        # `gh` absent, the repository unresolvable, or no completed run
        # inside the timeout: no answer was obtained, so the record carries
        # a reason and NONE of the fields only a run could produce.
        rec = {"id": "tests", "blocking": True, "attested_by": "ci",
               "error": ("not run: no completed run for " + REQUEST_SHA
                         + " within 900s (45 poll(s) of acme/widget@main; "
                           "1 run(s) at that SHA still in flight) — an "
                           "unattested gate is not evidence")}
        items = validate.validate_attestations(evidence_with([rec]), ONE_GATE)
        self.assertNotIn("A-NOT-RUN-SHAPE", codes(items))
        self.assertIn("A-NOT-RUN", errs(items))

    def test_a_ci_row_that_waited_may_not_also_claim_a_run(self):
        # The shape a future edit would reach for if it wanted the timeout
        # record to "look complete": an error AND run-only fields. It is not
        # a richer record, it is an incoherent one.
        rec = {"id": "tests", "blocking": True, "attested_by": "ci",
               "error": "not run: no completed run for " + REQUEST_SHA,
               "exit_code": 0, "duration_s": 900.0}
        self.assertIn("A-NOT-RUN-SHAPE", errs(validate.validate_attestations(
            evidence_with([rec]), ONE_GATE)))


if __name__ == "__main__":
    unittest.main()

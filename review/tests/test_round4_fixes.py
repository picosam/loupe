"""Round-4 findings, each with the reviewer's own falsification test.

One class per finding, named for it, carrying the verdict's FALSIFICATION
line verbatim as its docstring — so a later round runs exactly the test the
finding demanded rather than a paraphrase of it.

Round-4 F11 is the reason that sentence is now checkable rather than
asserted: the round-4 request claimed a one-class-per-finding map for round 3
that did not exist. The workbench that produced these rounds machine-checks
the real finding→test mapping against its corpus; this file is one half of
what it inventories.
"""
import argparse
import contextlib
import dataclasses
import io
import json
import os
import re
import shlex
import unittest
import unittest.mock
from pathlib import Path

from review import TOOL_NAME, config, emit, env_var, validate, wire
from review.cli import (UsageError, build_parser, cmd_ledger_add,
                        cmd_ledger_report, main)
from review.fingerprint import LineageError, compute, resolve_identity
from review.ledger import Ledger
from review.tests.util import REPO_ROOT

CFG = config.load(REPO_ROOT)
# In-memory ledger backend and no gate output retention: the whole suite must
# run with no writable directory (round-3 F9), which the round-4 fixes must
# not quietly undo.
NO_STATE = dataclasses.replace(CFG, ledger_dir=None)
SHA = "9" * 40


def errs(items):
    return {i.code for i in items if i.level == "error"}


def run_cli(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(argv)
    return code, buf.getvalue()


def _cli_verbs():
    """The real verb list, read off the parser so it cannot drift."""
    sub = next(a for a in build_parser()._actions
               if isinstance(a, argparse._SubParsersAction))
    return set(sub.choices)


_VERBS = _cli_verbs()


# --------------------------------------------------------------------- F1
# Moved to the workbench evidence suite (stays behind): F1 reads
# this workbench's design text. Its implementation half — no config, no
# taxonomy, refuse to rule — is test_round3_fixes' round-3 F5 class.

# --------------------------------------------------------------------- F2

CLOSURE_VERDICT = (
    '<loupe-review-verdict sha="{sha}">\n'
    'VERDICT: changes requested\n\n## findings\n\nNone\n\n'
    '## closures\n\n{closures}\n'
    '</loupe-review-verdict>')


class TestF2MalformedClosuresCannotVanish(unittest.TestCase):
    """FALSIFICATION: Independently mutating the fingerprint, separator, note,
    term, outcome, or required-record presence produces a specific validation
    error; valid records still ingest through `ledger add`."""

    GOOD = "- fp2:836d98fc52230998 withdrawn: the refutation lands"

    def _errs(self, closures, answering=None):
        v = wire.parse_verdict(CLOSURE_VERDICT.format(sha=SHA,
                                                      closures=closures))
        return errs(validate.validate_closures(v, answering))

    def test_a_valid_record_is_clean(self):
        self.assertEqual(self._errs(self.GOOD, answering=[]), set())

    def test_mutating_the_fingerprint_is_caught(self):
        self.assertIn("C-FP", self._errs(
            "- not-a-fingerprint withdrawn: the refutation lands",
            answering=[]))
        # A round-local finding number is the tempting wrong answer.
        self.assertIn("C-FP", self._errs("- F3 withdrawn: lands", answering=[]))

    def test_mutating_the_separator_is_caught(self):
        self.assertIn("C-SHAPE", self._errs(
            "- fp2:836d98fc52230998 withdrawn the refutation lands",
            answering=[]))

    def test_mutating_the_note_is_caught(self):
        self.assertIn("C-EVIDENCE", self._errs(
            "- fp2:836d98fc52230998 withdrawn:", answering=[]))

    def test_mutating_the_term_is_caught(self):
        self.assertIn("C-TERM", self._errs(
            "- fp2:836d98fc52230998 dismissed: the refutation lands",
            answering=[]))

    def test_mutating_the_outcome_is_caught(self):
        self.assertIn("C-OUTCOME", self._errs(
            "- fp2:836d98fc52230998 withdrawn ratified: lands", answering=[]))
        self.assertIn("C-AMENDMENT", self._errs(
            "- fp2:836d98fc52230998 test_amendment: lands", answering=[]))

    def test_a_line_that_is_not_a_record_at_all_is_caught(self):
        self.assertIn("C-SHAPE", self._errs(
            "I have decided not to close anything this round.", answering=[]))

    def test_the_defect_this_finding_names_is_gone(self):
        # The exact probe from the round-4 Evidence: these parsed as ZERO
        # closures and produced ZERO closure errors. Absent is not none.
        for line in ("- not-a-fingerprint sustained: because",
                     "- fp2:abc sustained because"):
            v = wire.parse_verdict(
                CLOSURE_VERDICT.format(sha=SHA, closures=line))
            self.assertEqual(len(v.closures), 1, line)
            self.assertTrue(errs(validate.validate_closures(v, [])), line)

    def test_required_record_presence_is_enforced(self):
        answering = [{"fp": "fp2:836d98fc52230998", "disposition": "refuted"}]
        self.assertIn("C-MISSING", self._errs("- fp2:aaaabbbbccccdddd "
                                              "sustained: unrelated",
                                              answering=answering))
        self.assertEqual(self._errs(self.GOOD, answering=answering), set())

    def test_test_amended_requires_its_own_closure_term(self):
        answering = [{"fp": "fp2:836d98fc52230998", "disposition": "accepted",
                      "subtype": "test_amended"}]
        self.assertIn("C-MISSING-AMENDMENT",
                      self._errs(self.GOOD, answering=answering))
        self.assertEqual(
            self._errs("- fp2:836d98fc52230998 test_amendment ratified: the "
                       "amended test is the right one", answering=answering),
            set())

    def test_unchecked_presence_is_reported_as_its_own_state(self):
        v = wire.parse_verdict(CLOSURE_VERDICT.format(sha=SHA,
                                                      closures=self.GOOD))
        codes = {i.code for i in validate.validate_closures(v, None)}
        self.assertIn("C-UNCHECKED", codes)

    def test_a_wrapped_note_stays_one_record(self):
        v = wire.parse_verdict(CLOSURE_VERDICT.format(
            sha=SHA, closures=f"{self.GOOD}\n  and answers the evidence"))
        self.assertEqual(len(v.closures), 1)
        self.assertIn("answers the evidence", v.closures[0].note)

    def test_valid_records_still_ingest_through_ledger_add(self):
        # The other half of the falsification: hardening the grammar must not
        # break the ingestion path it guards. A committed synthetic verdict
        # with two closures (the real round-4 verdict with its eighteen is
        # asserted in the workbench evidence suite).
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 4, "sha": SHA})
        args = _Args(envelope=str(REPO_ROOT / "review/tests/fixtures/"
                                  "verdict-with-closures.md"), round=4)
        cfg = dataclasses.replace(CFG, ledger_dir=None)
        with contextlib.redirect_stdout(io.StringIO()):
            with unittest.mock.patch("review.cli._ledger",
                                     return_value=ledger):
                code = cmd_ledger_add(args, cfg)
        self.assertEqual(code, 0)
        found = [e for e in ledger.events() if e.get("event") == "closure"]
        self.assertEqual(len(found), 2)


@dataclasses.dataclass
class _Args:
    envelope: str = ""
    round: int | None = None
    tokens: int | None = None
    against: str | None = None
    format: str | None = "json"
    ledger_dir: str | None = None


# --------------------------------------------------------------------- F3

def complete_attestation(**over):
    rec = {
        "id": "tests",
        "command": "python3 -m unittest discover -s review/tests -t .",
        "exit_code": 0,
        "tool_version": "python3 -> /usr/bin/python3 sha256:0123456789abcdef",
        "runner": "loupe/0.2.0",
        "target_sha": SHA,
        "executed_sha": SHA,
        "tree": "clean",
        "binding": "bound",
        "duration_s": 12.3,
        "output": {"sha256": "a" * 64, "bytes": 42,
                   "pointer": "/state/gate-output/tests.log"},
        "blocking": True,
    }
    rec.update(over)
    return rec


def evidence_with(records):
    return ("Machine attestations:\n\n"
            f"```{wire.attestation_fence(CFG.wrapper_tag)}\n"
            f"{json.dumps(records, indent=2)}\n```\n\n"
            "NOT captured — this handoff cannot vouch for these:\n"
            "  - nothing else\n")


ONE_GATE = dataclasses.replace(
    CFG, gates=[{"id": "tests", "command": ["true"], "blocking": True}],
    ledger_dir=None)


class TestF3AttestationSignalsAreIndependentlyRequired(unittest.TestCase):
    """FALSIFICATION: Removing or corrupting each §5.1 attestation field
    independently produces a specific error, while one complete record
    validates."""

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


# --------------------------------------------------------------------- F4

class TestF4GatesBindToTheExecutedTree(unittest.TestCase):
    """FALSIFICATION: Supplying any head other than the executed checkout
    cannot produce a passing attestation bound to that head."""

    PROBE = dataclasses.replace(
        CFG, ledger_dir=None,
        gates=[{"id": "tests", "command": ["true"], "blocking": True}])

    def probe(self, target):
        """Run the one-gate probe manifest with the re-entrancy guard OFF.

        This suite is itself a declared gate, so when it runs during an
        emission every test in this class inherits LOUPE_IN_GATE_RUN and
        `run_gates` correctly returns not-run records — which made these
        assertions pass locally and fail inside the emitter, the exact class
        of environment-dependent test the guard exists to prevent elsewhere.
        Clearing it here cannot recurse: this manifest runs `true`, not the
        suite. `test_the_guard_still_fires_when_nested` holds the other half.
        """
        env = {k: v for k, v in os.environ.items() if k != "LOUPE_IN_GATE_RUN"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            return emit.run_gates(self.PROBE, target)

    def test_the_guard_still_fires_when_nested(self):
        with unittest.mock.patch.dict(os.environ,
                                      {"LOUPE_IN_GATE_RUN": "1"}):
            [rec] = emit.run_gates(self.PROBE, "0" * 40)
        self.assertIn("not run", rec["error"])
        self.assertNotIn("exit_code", rec)
        # And a not-run blocking gate is fatal, never a silent pass.
        self.assertIn("A-NOT-RUN", errs(validate.validate_attestations(
            evidence_with([rec]), self.PROBE)))

    def test_a_foreign_target_cannot_produce_a_bound_attestation(self):
        # The round-4 probe verbatim: run a trivially passing command in this
        # tree and attest it to the null SHA.
        [rec] = self.probe("0" * 40)
        self.assertEqual(rec["exit_code"], 0)
        self.assertTrue(rec["binding"].startswith("unbound"), rec["binding"])
        self.assertNotEqual(rec["executed_sha"], "0" * 40)
        codes = errs(validate.validate_attestations(
            evidence_with([rec]), self.PROBE))
        self.assertIn("A-UNBOUND", codes)
        self.assertIn("A-SHA-MISMATCH", codes)

    def test_the_recorded_sha_is_derived_from_the_executed_tree(self):
        import subprocess
        head = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse",
                               "HEAD"], capture_output=True, text=True,
                              timeout=60).stdout.strip()
        [rec] = self.probe("0" * 40)
        self.assertEqual(rec["executed_sha"], head)

    def test_a_dirty_tree_is_recorded_and_unbound(self):
        import subprocess
        head = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse",
                               "HEAD"], capture_output=True, text=True,
                              timeout=60).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
            capture_output=True, text=True, timeout=60).stdout.strip())
        [rec] = self.probe(head)
        self.assertEqual(rec["tree"], "dirty" if dirty else "clean")
        # Matching SHAs are necessary but not sufficient: a dirty tree is not
        # the target commit's content, so it stays unbound.
        self.assertEqual(rec["binding"] == "bound", not dirty)

    def test_output_is_digested_even_when_it_cannot_be_retained(self):
        [rec] = self.probe("0" * 40)
        self.assertEqual(len(rec["output"]["sha256"]), 64)
        self.assertIn("not retained", rec["output"]["pointer"])


# --------------------------------------------------------------------- F5

class TestF5TokenBreakerIsLive(unittest.TestCase):
    """FALSIFICATION: Product-ingested rounds below budget remain clean;
    adding a product-ingested round that crosses the budget fires exactly one
    breaker naming the arithmetic."""

    FIXTURE = str(REPO_ROOT / "review/tests/fixtures/mini-verdict.md")

    def _ingest(self, ledger, round_no, tokens):
        # The product path: the same function `main` dispatches to, with the
        # in-memory backend so no writable directory is needed.
        cfg = dataclasses.replace(CFG, ledger_dir=None)
        args = _Args(envelope=self.FIXTURE, round=round_no, tokens=tokens)
        with contextlib.redirect_stdout(io.StringIO()):
            with unittest.mock.patch("review.cli._ledger",
                                     return_value=ledger):
                code = cmd_ledger_add(args, cfg)
        self.assertEqual(code, 0)

    def test_below_budget_is_clean_and_over_budget_fires_once(self):
        ledger = Ledger.in_memory()
        self._ingest(ledger, 1, 40_000)
        self._ingest(ledger, 2, 40_000)
        fired = [b for b in ledger.breakers(round_cap=9, token_budget=100_000)
                 if b.get("limit") == "tokens"]
        self.assertEqual(fired, [], "80k of a 100k budget is not over it")

        self._ingest(ledger, 3, 40_000)
        fired = [b for b in ledger.breakers(round_cap=9, token_budget=100_000)
                 if b.get("limit") == "tokens"]
        self.assertEqual(len(fired), 1, fired)
        self.assertIn("120000", fired[0]["rule"])
        self.assertIn("100000", fired[0]["rule"])
        self.assertIn("40000 + 40000 + 40000", fired[0]["rule"])

    def test_the_three_input_states_are_distinguished(self):
        ledger = Ledger.in_memory()
        self.assertEqual(ledger.token_state(None)["state"], "no_budget")
        self.assertEqual(ledger.token_state(100)["state"], "no_counts")
        self._ingest(ledger, 1, 10)
        self.assertEqual(ledger.token_state(100)["state"], "live")

    def test_no_counts_is_not_a_spend_of_zero(self):
        # The half-state this finding named: arithmetic that looks live
        # because it reports a number. With no counts there is no number.
        ledger = Ledger.in_memory()
        state = ledger.token_state(100)
        self.assertEqual(state["spent"], 0)
        self.assertIn("absent is not zero", state["why"])
        self.assertEqual(
            [b for b in ledger.breakers(round_cap=9, token_budget=100)
             if b.get("limit") == "tokens"], [])

    def test_the_budget_source_is_config_and_reaches_every_report_path(self):
        self.assertIn("token_budget", CFG.limits)
        ledger = Ledger.in_memory()
        report = ledger.report(3, gate_manifest=CFG.gate_ids,
                               token_budget=CFG.token_budget)
        self.assertIn("tokens", report)
        self.assertEqual(report["tokens"]["budget"], CFG.token_budget)


# --------------------------------------------------------------------- F6

class TestF6LineageFailsClosed(unittest.TestCase):
    """FALSIFICATION: Every member of an accepted lineage component resolves
    identically; cycles and multiple outgoing targets fail closed with a typed
    error."""

    def test_every_member_of_a_chain_resolves_identically(self):
        chain = [{"kind": "alias", "from_fp": "fp1:a", "to_fp": "fp2:b"},
                 {"kind": "rename", "from_fp": "fp2:b", "to_fp": "fp2:c"}]
        ends = {resolve_identity(fp, chain) for fp in ("fp1:a", "fp2:b",
                                                       "fp2:c")}
        self.assertEqual(ends, {"fp2:c"})

    def test_a_cycle_fails_closed(self):
        cycle = [{"kind": "alias", "from_fp": "A", "to_fp": "B"},
                 {"kind": "alias", "from_fp": "B", "to_fp": "A"}]
        # The defect: A resolved to A and B resolved to B, so one malformed
        # component silently became two identities.
        for start in ("A", "B"):
            with self.subTest(start=start):
                with self.assertRaises(LineageError):
                    resolve_identity(start, cycle)

    def test_a_self_edge_fails_closed(self):
        with self.assertRaises(LineageError):
            resolve_identity("A", [{"kind": "alias", "from_fp": "A",
                                    "to_fp": "A"}])

    def test_two_outgoing_merge_targets_fail_closed(self):
        with self.assertRaises(LineageError):
            resolve_identity("A", [{"kind": "alias", "from_fp": "A",
                                    "to_fp": "B"},
                                   {"kind": "rename", "from_fp": "A",
                                    "to_fp": "C"}])

    def test_a_split_parent_with_many_children_is_well_formed(self):
        # `split` forks a parent into distinct children and does NOT merge, so
        # five outgoing split edges are correct, not a conflict. The live
        # ledger has exactly this shape.
        splits = [{"kind": "split", "from_fp": "A", "to_fp": f"C{i}"}
                  for i in range(5)]
        self.assertEqual(resolve_identity("A", splits), "A")

    def test_the_error_is_typed_and_routes_through_the_next_command(self):
        self.assertIsInstance(LineageError("x"), ValueError)


# --------------------------------------------------------------------- F7

class TestF7DeclaredCitationsDoNotSplit(unittest.TestCase):
    """FALSIFICATION: Moved line numbers for primary and secondary structured
    citations normalize identically, while `timeout:30/60` and `port:8080/9090`
    remain distinct."""

    def _fp(self, claim, citations=""):
        return compute("design_gap", "code.py", "", claim,
                       citations=citations)

    def test_secondary_extensionless_citations_survive_a_moved_line(self):
        # The round-4 probe verbatim: primary path code.py, secondary
        # citations README and design, which have no extension and no
        # separator and so were invisible to inference.
        for target in ("README", "design", "RFC"):
            with self.subTest(citation=target):
                a = self._fp(f"{target}:10 says the contract", target)
                b = self._fp(f"{target}:20 says the contract", target)
                self.assertEqual(a, b)

    def test_primary_citations_still_survive_a_moved_line(self):
        a = compute("design_gap", "preflight.py", "",
                    "P2 rejects BEHIND at preflight.py:150")
        b = compute("design_gap", "preflight.py", "",
                    "P2 rejects BEHIND at preflight.py:9")
        self.assertEqual(a, b)

    def test_semantic_literals_remain_distinct(self):
        self.assertNotEqual(self._fp("timeout:30 is unsafe"),
                            self._fp("timeout:60 is unsafe"))
        self.assertNotEqual(self._fp("port:8080 is wrong"),
                            self._fp("port:9090 is wrong"))

    def test_declaring_a_citation_does_not_capture_unrelated_literals(self):
        # Declaring `design` must not make `timeout:30` a citation too.
        self.assertNotEqual(self._fp("design:10 and timeout:30", "design"),
                            self._fp("design:10 and timeout:60", "design"))

    def test_declaring_citations_does_not_disable_derivation(self):
        # Additive, not exclusive: a reviewer that declares `design` must not
        # have to re-declare every path it also cites.
        a = self._fp("design:10 and review/wire.py:212", "design")
        b = self._fp("design:99 and review/wire.py:900", "design")
        self.assertEqual(a, b)

    def test_absent_declaration_is_a_recorded_state(self):
        block = ("### F1\nSeverity: High\nClassification: design_gap\n"
                 "Title: t\nEvidence: code.py:1\nCitations: design, README\n"
                 "Why: w\nRequired outcome: r\nFALSIFICATION: f\n")
        declared = wire.parse_findings(block)[0]
        derived = wire.parse_findings(
            block.replace("Citations: design, README\n", ""))[0]
        self.assertEqual(declared.citation_source, "declared")
        self.assertEqual(derived.citation_source, "derived-from-shape")

# --------------------------------------------------------------------- F8

class TestF8GateManifestReachesTheMetric(unittest.TestCase):
    """FALSIFICATION: CLI and emitted reports compute the same manifest-backed
    value as the ledger API; absent proposed gates remain excluded."""

    def _cli_report(self, ledger):
        """`ledger report` driven over a supplied ledger — the product
        function `main` dispatches to, with an in-memory backend so the check
        holds in a read-only sandbox."""
        args = _Args(format="json")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with unittest.mock.patch("review.cli._ledger",
                                     return_value=ledger):
                code = cmd_ledger_report(args, CFG)
        self.assertEqual(code, 0)
        return json.loads(buf.getvalue())

    def _synthetic_ledger(self):
        # Two labelled findings, one naming a real gate id from the manifest,
        # one naming a gate nobody built. (The real corpus counts are
        # asserted in the workbench evidence suite.)
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": "a" * 40})
        ledger.add({"event": "verdict", "round": 1, "sha": "a" * 40,
                    "verdict": "changes requested", "finding_ids": 2})
        ledger.add({"event": "finding", "round": 1, "id": "F1", "fp": "fp2:1",
                    "severity": "High", "preventable_by": CFG.gate_ids[0]})
        ledger.add({"event": "finding", "round": 1, "id": "F2", "fp": "fp2:2",
                    "severity": "High",
                    "preventable_by": "a linter nobody built"})
        return ledger

    def test_cli_report_matches_the_ledger_api(self):
        ledger = self._synthetic_ledger()
        api = ledger.report(ledger.effective_round_cap(CFG.round_cap),
                            gate_manifest=CFG.gate_ids,
                            token_budget=CFG.token_budget)
        cli = self._cli_report(ledger)
        self.assertNotEqual(CFG.gate_ids, [])
        self.assertEqual(cli["gate_manifest"], CFG.gate_ids)
        self.assertTrue(api["metrics"]["rounds"])
        for r in api["metrics"]["rounds"]:
            self.assertEqual(
                cli["metrics"]["rounds"][str(r)]["deterministic_preventable"],
                api["metrics"]["rounds"][r]["deterministic_preventable"])

    def test_the_product_call_is_no_longer_not_computable(self):
        # The defect: `cli.py` and the emitter omitted the manifest, so the
        # product path reported "not computable (no gate manifest declared)"
        # while a manifest was declared two files away.
        rounds = self._cli_report(self._synthetic_ledger())["metrics"]["rounds"]
        self.assertTrue(rounds)
        for r, m in rounds.items():
            with self.subTest(round=r):
                self.assertEqual(m["deterministic_preventable"]["count"], 1)
                self.assertNotIn("not computable",
                                 str(m["deterministic_preventable"]["share"]))

    def test_absent_proposed_gates_remain_excluded(self):
        # A label naming a gate that does not exist in the manifest is a
        # candidate, never a count.
        m = self._synthetic_ledger().metrics(
            gate_manifest=CFG.gate_ids)["rounds"][1]
        self.assertEqual(m["deterministic_preventable"]["count"], 1)
        self.assertEqual(m["deterministic_preventable"]["gates"],
                         [CFG.gate_ids[0]])

    def test_gate_identities_not_display_labels(self):
        # The manifest reaches the metric as the declared ids, never as the
        # commands that implement them (the exact id list is review.toml's
        # to declare — restating it here made this test fail on every
        # manifest change, which is config drift, not a defect).
        self.assertIn("tests", CFG.gate_ids)
        self.assertEqual(CFG.gate_ids, [g["id"] for g in CFG.gates])
        self.assertTrue(all(" " not in gid for gid in CFG.gate_ids))
        self.assertNotIn("python3 -m unittest discover -s review/tests -t .",
                         CFG.gate_ids)
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": "a" * 40})
        ledger.add({"event": "verdict", "round": 1, "sha": "a" * 40,
                    "verdict": "changes requested", "finding_ids": 2})
        ledger.add({"event": "finding", "round": 1, "id": "F1", "fp": "fp2:1",
                    "severity": "High", "preventable_by": "tests"})
        ledger.add({"event": "finding", "round": 1, "id": "F2", "fp": "fp2:2",
                    "severity": "High",
                    "preventable_by": "a doc-consistency linter nobody built"})
        m = ledger.metrics(gate_manifest=CFG.gate_ids)["rounds"][1]
        self.assertEqual(m["deterministic_preventable"]["count"], 1)
        self.assertEqual(m["deterministic_preventable"]["gates"], ["tests"])

    def test_absent_manifest_is_still_a_distinct_state(self):
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": "a" * 40})
        ledger.add({"event": "verdict", "round": 1, "sha": "a" * 40,
                    "verdict": "changes requested", "finding_ids": 1})
        ledger.add({"event": "finding", "round": 1, "id": "F1", "fp": "fp2:1",
                    "severity": "High", "preventable_by": "some gate"})
        m = ledger.metrics()["rounds"][1]["deterministic_preventable"]
        self.assertIsNone(m["count"])
        self.assertIn("not computable", m["share"])


# --------------------------------------------------------------------- F9

class TestF9EveryNonZeroExitIsStructured(unittest.TestCase):
    """FALSIFICATION: Every reachable exit 1 or 2 returns parseable structured
    output containing a nonempty `next` field."""

    # Every reachable non-zero branch in cli.py, enumerated. The round-4
    # defect was a branch nobody had listed, so the list is the artifact:
    # `test_the_enumeration_is_complete` checks it against the source.
    BRANCHES = (
        ("validate",),                                    # usage: missing arg
        ("nonsense",),                                    # usage: unknown verb
        ("ledger",),                                      # usage: missing sub
        ("respond", "--verdict", "/nope"),                # usage: missing arg
        ("validate", "/nonexistent.md"),                  # usage: no file
        ("validate", "README.md"),                        # findings: unknown
        ("ledger", "add", "README.md"),                   # findings: unknown
        ("ledger", "add",
         "review/tests/fixtures/legacy-verdict.md"),      # findings: invalid
        # Round 3 F6: the branch the enumeration was missing. An envelope the
        # tool RECOGNISES and then rejects exits through _finish, which the
        # three above never reach — and _finish was the one path still
        # labelling a sentence `command`.
        ("validate",
         "review/tests/fixtures/legacy-verdict.md"),      # recognised, invalid
    )

    def assert_typed_recovery(self, payload):
        """The round-2 F8 contract, applied to any non-zero payload.

        Round 4 asked only that `next` be nonempty, which a diagnosis
        satisfies as happily as a command. So the check is now the property
        an agent actually depends on: either `next` is argv it can RUN — put
        through the real parser, not eyeballed — or the exit is typed
        `blocked`, `next` is null, and a remedy names what a person must do.
        """
        self.assertIn(payload.get("next_kind"), ("command", "blocked"),
                      payload)
        if payload["next_kind"] == "blocked":
            self.assertIsNone(payload["next"], payload)
            self.assertTrue(payload.get("remedy"), payload)
            return
        cmd = payload.get("next")
        self.assertTrue(cmd, payload)
        tokens = shlex.split(cmd)
        self.assertTrue(tokens, payload)
        # Round 3 F6: a sentence has a verb that is not a verb. `fix`,
        # `re-run`, `inspect`, `restore` and a leading placeholder are all
        # shapes prose takes when it is put in an argv field, and each one
        # shipped at least once.
        self.assertNotIn(",", tokens[0],
                         "a comma in the verb is the shape of a sentence")
        self.assertNotIn(tokens[0], {"fix", "re-run", "inspect", "restore",
                                     "make", "wrap", "remove", "whoever"},
                         f"`next` opens with an English imperative: {cmd}")
        self.assertFalse(tokens[0].startswith(("<", "{", "[")),
                         f"`next` opens with a placeholder: {cmd}")
        if tokens[0] != TOOL_NAME:
            return                       # `git …` and friends: not our grammar
        rest = [t for t in tokens[1:] if not t.startswith("#")]
        # RVW-T16 (2026-08-19): the lineage-2 round-4 F9 tolerance is
        # withdrawn. It admitted "a placeholder the human fills in; the verb
        # must be real" for the CLI surface, while the transport surface had
        # to be literal argv or `blocked`. Two rules for one field an agent
        # executes is one rule too many: an argv with `<verdict.md>` in it is
        # not runnable, and the adapters tell every agent to run `next`
        # VERBATIM. A recovery only a person can complete is `blocked` with a
        # remedy — that state exists precisely for this.
        placeholders = [x for x in rest
                        if x.startswith("<") or x.startswith("{")]
        self.assertFalse(placeholders,
                         f"`next` carries a placeholder an agent cannot run: "
                         f"{cmd} — it should be blocked with a remedy")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), \
                    contextlib.redirect_stderr(buf):
                build_parser().parse_args(rest)
        except SystemExit as exc:
            # `--help` parses, prints, and exits 0. That is a runnable
            # command; only a non-zero exit means the argv is not valid.
            if int(exc.code or 0) != 0:              # pragma: no cover
                self.fail(f"`next` is not runnable argv: {cmd}")
        except UsageError as exc:                    # pragma: no cover
            self.fail(f"`next` does not parse as a command: {cmd} ({exc})")

    def test_every_enumerated_branch_returns_structured_output(self):
        for argv in self.BRANCHES:
            with self.subTest(argv=argv):
                code, out = run_cli(list(argv))
                self.assertNotEqual(code, 0)
                self.assert_typed_recovery(json.loads(out))

    def test_the_defect_this_finding_names_is_gone(self):
        # The round-4 probe verbatim: exit 1, then json.loads raised.
        code, out = run_cli(["validate", "README.md"])
        self.assertEqual(code, 1)
        self.assert_typed_recovery(json.loads(out))

    def test_config_load_failures_are_structured(self):
        """Round 1 F8 (Medium), falsification.

        The enumeration above covers branches reached AFTER configuration
        resolves. Round 1 found the ones before it: config.load ran above the
        boundary, so malformed TOML left a traceback and the one instruction
        the adapters give — run the `next` command — was unavailable exactly
        where a fresh install fails first.
        """
        import os
        import tempfile
        env = env_var("CONFIG")
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "broken.toml"
            bad.write_text("this is not = = valid toml\n", encoding="utf-8")
            previous, cwd = os.environ.get(env), os.getcwd()
            os.environ[env] = str(bad)
            os.chdir(tmp)                 # away from this repo's review.toml
            try:
                code, out = run_cli(["validate", str(bad)])
            finally:
                os.chdir(cwd)
                if previous is None:
                    os.environ.pop(env, None)
                else:
                    os.environ[env] = previous
        self.assertNotEqual(code, 0)
        payload = json.loads(out)         # the round-1 probe: this raised
        self.assert_typed_recovery(payload)
        self.assertIn("TOML", payload["error"])

    def test_an_unreadable_config_is_structured_too(self):
        # The adjacent state: valid TOML the process cannot read.
        import os
        import stat
        import tempfile
        env = env_var("CONFIG")
        with tempfile.TemporaryDirectory() as tmp:
            denied = Path(tmp) / "denied.toml"
            denied.write_text("[taxonomy]\n", encoding="utf-8")
            denied.chmod(0o000)
            if os.access(denied, os.R_OK):     # running as root: not provable
                denied.chmod(stat.S_IRUSR | stat.S_IWUSR)
                self.skipTest("this process can read a 0o000 file")
            previous, cwd = os.environ.get(env), os.getcwd()
            os.environ[env] = str(denied)
            os.chdir(tmp)
            try:
                code, out = run_cli(["validate", str(denied)])
            finally:
                os.chdir(cwd)
                denied.chmod(stat.S_IRUSR | stat.S_IWUSR)
                if previous is None:
                    os.environ.pop(env, None)
                else:
                    os.environ[env] = previous
        self.assertNotEqual(code, 0)
        self.assert_typed_recovery(json.loads(out))

    def test_recognized_invalid_validate_has_typed_runnable_recovery(self):
        """Round 3 F6 (Medium), falsification.

        The typed-recovery rule was applied to the paths round 2 named and
        not to the class it declared. `_finish` — the exit every RECOGNISED
        but invalid envelope takes — labelled its recovery `command` while
        being handed `fix the listed items in <file>, then re-run …`. An
        adapter obeying the contract literally tries to execute the word
        `fix`. The round-2 enumeration missed it because all three of its
        failure branches stop before `_finish` is reached.

        Both `_finish` paths an author actually hits are covered here.
        """
        cases = (
            (["validate", "review/tests/fixtures/legacy-verdict.md"],
             "a recognised verdict that fails validation"),
            (["respond", "--verdict",
              "review/tests/fixtures/legacy-verdict.md",
              "--from-json", "-"],
             "a disposition payload that fails validation"),
        )
        for argv, what in cases:
            with self.subTest(case=what):
                stdin = unittest.mock.patch(
                    "sys.stdin", io.StringIO(json.dumps(
                        {"verdict_sha": "9" * 40, "head": "8" * 40,
                         "author": "claude", "round": 1,
                         "dispositions": []})))
                with stdin:
                    code, out = run_cli(argv)
                self.assertNotEqual(code, 0, what)
                payload = json.loads(out)
                # The contract, executed rather than eyeballed.
                self.assertTrue(payload.get("items"), payload)
                self.assert_typed_recovery(payload)
                # And the defect named: the sentence must not come back as
                # something an agent is told to run.
                if payload["next_kind"] == "command":
                    self.assertNotIn("then re-run", payload["next"])
                    self.assertNotIn("fix ", payload["next"])
                else:
                    self.assertIsNone(payload["next"])
                    self.assertTrue(payload["remedy"])

    def test_a_finish_path_with_a_real_command_stays_a_command(self):
        # The control for the test above: `_finish` must still be able to
        # return a command. `ledger add` on a recognised-but-invalid envelope
        # hands it `loupe validate <file>`, which is genuinely runnable, and
        # typing everything `blocked` would satisfy F6 by giving up.
        code, out = run_cli(
            ["ledger", "add", "review/tests/fixtures/legacy-verdict.md"])
        payload = json.loads(out)
        self.assertNotEqual(code, 0)
        self.assertEqual(payload["next_kind"], "command", payload)
        self.assertTrue(payload["next"].startswith(f"{TOOL_NAME} validate"))
        self.assert_typed_recovery(payload)

    def test_config_failure_next_is_executable_command(self):
        """Round 2 F8 (Medium), falsification.

        Round 1 moved configuration inside the structured boundary; round 2
        found what was inside it. `next` read `fix /path/to/broken.toml, then
        re-run` — a sentence, arriving through the one field the adapters tell
        an agent to execute verbatim, at the fresh-install state a first run
        hits before anything else. The old control asserted `next` was
        truthy, which that sentence is.

        Nothing the tool can run repairs a human's broken TOML, so the fix is
        not to invent a command: it is to say `blocked` and hand the person a
        remedy. This asserts BOTH halves — that no config failure ships prose
        in `next`, and that whatever it does ship is the declared kind.
        """
        import os
        import tempfile
        env = env_var("CONFIG")
        cases = {
            "malformed": "this is not = = valid toml\n",
            "unclosed-table": "[taxonomy\n",
            "duplicate-key": "a = 1\na = 2\n",
        }
        for name, body in cases.items():
            with self.subTest(config=name):
                with tempfile.TemporaryDirectory() as tmp:
                    bad = Path(tmp) / f"{name}.toml"
                    bad.write_text(body, encoding="utf-8")
                    previous, cwd = os.environ.get(env), os.getcwd()
                    os.environ[env] = str(bad)
                    os.chdir(tmp)          # away from this repo's review.toml
                    try:
                        code, out = run_cli(["validate", str(bad)])
                    finally:
                        os.chdir(cwd)
                        if previous is None:
                            os.environ.pop(env, None)
                        else:
                            os.environ[env] = previous
                self.assertNotEqual(code, 0)
                payload = json.loads(out)
                # The contract, executed rather than eyeballed.
                self.assert_typed_recovery(payload)
                # And the defect itself, named: the round-2 probe's exact
                # string shape must not come back through `next` under any
                # kind. A diagnosis is recognisable — it has a comma and an
                # imperative — and it belongs in `remedy`, never in argv.
                self.assertIsNone(payload["next"], payload)
                self.assertEqual(payload["next_kind"], "blocked", payload)
                self.assertNotIn("then re-run", str(payload["next"]))
                self.assertTrue(payload["remedy"])

    def test_a_config_failure_with_a_real_command_stays_a_command(self):
        """The control for the test above.

        `blocked` must not become the answer to everything — that would
        satisfy F8 by never offering a command again. The legacy-state
        refusal has a genuine one, and it must still type as `command` and
        still parse.
        """
        with unittest.mock.patch.object(
                config, "legacy_state_dir",
                return_value=Path("/tmp/former-name/repo")):
            with unittest.mock.patch.object(Path, "is_dir",
                                            return_value=False):
                code, out = run_cli(["ledger", "report"])
        payload = json.loads(out)
        self.assertNotEqual(code, 0)
        self.assertEqual(payload["next_kind"], "command", payload)
        self.assertEqual(payload["next"], f"{TOOL_NAME} migrate-state")
        self.assert_typed_recovery(payload)

    def test_no_failure_path_can_print_prose(self):
        # The enumeration above can only ever be as complete as someone
        # remembered to make it, which is precisely how F9's branch survived
        # five tested paths. This is the structural half: `print` may appear
        # only in the structured-output helper and in the three commands whose
        # exit-0 job is to write an artifact to stdout. Any new bare print on
        # a failure path fails here without anyone having to list it.
        import ast
        source = (REPO_ROOT / "review" / "cli.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        printers = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for child in ast.walk(node):
                if (isinstance(child, ast.Call)
                        and isinstance(child.func, ast.Name)
                        and child.func.id == "print"):
                    printers.add(node.name)
        self.assertEqual(printers, {"_out", "cmd_respond",
                                    "cmd_ledger_report", "cmd_emit_request"},
                         "a print() outside the structured helper and the "
                         "artifact-writing commands is a prose exit waiting "
                         "to happen")


# --------------------------------------------------------------------- F10

# ------------------------------------------------------------- sweep F11
def _dotted(node) -> str:
    """`paths.command` for an Attribute chain, `command` for a Name."""
    import ast
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


class TestEveryFailureExit(unittest.TestCase):
    """Sweep F11 (Medium): the transport refusal surface, enumerated.

    `TestF9EveryNonZeroExitIsStructured` enumerates the CLI's own exits.
    Transport verbs raise `Refusal(why, next_cmd)` and the CLI stamps every
    nonempty `next_cmd` as `next_kind: command` — so a Refusal whose second
    argument was a sentence (`check remote access to …, then re-run loupe
    take`; `return the envelope to the author`; `remove 'x' from the
    disposition and re-run`) reached the agent as a program to execute.
    Every Refusal site in transport.py is read here from the source, and
    its `next_cmd` must be either empty (the CLI types that `blocked`, with
    the account as the remedy) or a literal runnable line: `&&`-separated
    segments, each opening with `loupe <real verb>` or `git`, no
    `<placeholder>`, no English connective. A trailing `# comment` is the
    established hint form and stays allowed.
    """

    TRANSPORT = REPO_ROOT / "review" / "transport.py"

    @classmethod
    def _refusal_sites(cls):
        """[(lineno, template)] for every `Refusal(...)` in transport.py.
        FormattedValues render as `TOOL_NAME` when they are that name and
        as `‹X›` otherwise; conditional expressions yield both branches."""
        import ast
        tree = ast.parse(cls.TRANSPORT.read_text(encoding="utf-8"))
        sites = []

        def render(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return [node.value]
            if isinstance(node, ast.JoinedStr):
                outs = [""]
                for part in node.values:
                    if isinstance(part, ast.Constant):
                        outs = [o + part.value for o in outs]
                    elif isinstance(part, ast.FormattedValue):
                        v = part.value
                        piece = (TOOL_NAME if isinstance(v, ast.Name)
                                 and v.id == "TOOL_NAME" else "‹X›")
                        outs = [o + piece for o in outs]
                return outs
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                return [a + b for a in render(node.left)
                        for b in render(node.right)]
            if isinstance(node, ast.IfExp):
                return render(node.body) + render(node.orelse)
            # Round 5 F1: recoveries are built by `paths.command(...)` now,
            # not spliced into f-strings. A `Lit` word renders as the
            # literal the source states; every other word is a value, which
            # this enumeration has always shown as ‹X›.
            if (isinstance(node, ast.Call)
                    and _dotted(node.func) in ("paths.command", "command")):
                words = []
                flat = []
                for arg in node.args:
                    if (isinstance(arg, ast.Starred)
                            and isinstance(arg.value, ast.Call)
                            and _dotted(arg.value.func) in ("paths.lits",
                                                            "lits")):
                        # `*paths.lits(a, b)` is several Lit words at once.
                        flat.extend(
                            ast.Call(func=ast.Name(id="Lit", ctx=ast.Load()),
                                     args=[a], keywords=[])
                            for a in arg.value.args)
                    else:
                        flat.append(arg)
                for arg in flat:
                    if (isinstance(arg, ast.Call)
                            and _dotted(arg.func) in ("paths.Lit", "Lit")
                            and len(arg.args) == 1):
                        inner_node = arg.args[0]
                        if (isinstance(inner_node, ast.Name)
                                and inner_node.id == "TOOL_NAME"):
                            words.append(TOOL_NAME)
                            continue
                        inner = render(inner_node)
                        words.append(inner[0] if inner and inner[0]
                                     is not None else "‹X›")
                    elif (isinstance(arg, ast.Name)
                          and arg.id == "TOOL_NAME"):
                        words.append(TOOL_NAME)
                    else:
                        words.append("‹X›")
                return [" ".join(words)]
            return [None]                      # not a literal: judged as such

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "Refusal"):
                continue
            nxt = None
            if len(node.args) >= 2:
                nxt = node.args[1]
            for kw in node.keywords:
                if kw.arg == "next_cmd":
                    nxt = kw.value
            for template in render(nxt) if nxt is not None else [None]:
                sites.append((node.lineno, template))
        return sites

    def _judge(self, template):
        """None if `template` is a legal `next_cmd`, else the reason."""
        if template is None:
            return "next_cmd is not a string literal the source states"
        if template == "":
            return None                       # blocked: typed by the CLI
        line = template.split("#", 1)[0].strip()   # the hint form
        if not line:
            return "only a comment"
        if "<" in line or ">" in line:
            return f"placeholder in a field an agent executes: {template!r}"
        for connective in (", then", " then ", " or ", "re-run", "return "):
            if connective in line:
                return f"English in argv: {template!r}"
        for segment in line.split("&&"):
            tokens = shlex.split(segment.replace("‹X›", "X"))
            if not tokens:
                return f"empty command segment in {template!r}"
            if tokens[0] == TOOL_NAME:
                if len(tokens) < 2 or tokens[1] not in _VERBS:
                    return f"`{TOOL_NAME}` without a real verb: {template!r}"
            elif tokens[0] != "git":
                return f"opens with {tokens[0]!r}, not a runnable: {template!r}"
        return None

    def test_every_transport_refusal_has_typed_runnable_recovery(self):
        """FALSIFICATION for F11. Mutation: restore any prose-valued
        `next_cmd` — e.g. `"return the envelope to the author"` on the
        base-ancestry refusal in probe_target — and the enumeration names
        the line and fails."""
        sites = self._refusal_sites()
        self.assertGreaterEqual(len(sites), 25, "the enumeration must see "
                                "the whole surface, not a sample")
        bad = [(ln, t, why) for ln, t in sites
               for why in [self._judge(t)] if why]
        self.assertEqual(bad, [], "\n".join(
            f"transport.py:{ln}: {why}" for ln, t, why in bad))
        # Both shapes exist on the surface: this is not satisfied by typing
        # everything blocked, nor by typing everything a command.
        self.assertTrue(any(t == "" for _, t in sites))
        self.assertTrue(any(t for _, t in sites))

    def test_paired_real_command_controls(self):
        """The refusals that DO carry commands carry runnable ones, and the
        ones that cannot are blocked — exercised at runtime, not only read
        from the source."""
        from review import transport
        from review.ledger import Ledger
        from review.tests._transport_fixtures import (SHA_A, SHA_B, SHA_C,
                                                 fake_git, request_text)
        cfg = dataclasses.replace(config.load(REPO_ROOT), ledger_dir=None)
        # Blocked: no stamp — a person returns the envelope.
        with self.assertRaises(transport.Refusal) as ctx:
            transport.probe_target(cfg, None, SHA_B, SHA_A, git=fake_git({}))
        self.assertEqual(ctx.exception.next_cmd, "")
        # Blocked: the fetch itself failed — access is a person's to restore.
        git = fake_git({("fetch", "u", "r"): RuntimeError("no route")})
        push = {"state": "pushed", "url": "u", "ref": "r"}
        with self.assertRaises(transport.Refusal) as ctx:
            transport.probe_target(cfg, push, SHA_B, None, git=git)
        self.assertEqual(ctx.exception.next_cmd, "")
        # Command: the fetch was skipped and the re-take is known, so the
        # recovery is the literal fetch-and-retake line.
        git = fake_git({("cat-file", "-e", f"{SHA_B}^{{commit}}"):
                        RuntimeError("missing")})
        with self.assertRaises(transport.Refusal) as ctx:
            transport.probe_target(cfg, push, SHA_B, None, fetch=False,
                                   git=git, retake=("r.md", "codex"))
        cmd = ctx.exception.next_cmd
        self.assertTrue(cmd.startswith("git -C "), cmd)
        self.assertIn(f" && {TOOL_NAME} take r.md --as codex", cmd)
        self.assertIsNone(self._judge(cmd), cmd)
        # ...and without a known re-take, blocked rather than a placeholder.
        with self.assertRaises(transport.Refusal) as ctx:
            transport.probe_target(cfg, push, SHA_B, None, fetch=False,
                                   git=git)
        self.assertEqual(ctx.exception.next_cmd, "")
        # Command: a waiver on an unresolvable SHA points at the log.
        with self.assertRaises(transport.Refusal) as ctx:
            transport.waive(cfg, Ledger.in_memory(), SHA_C, "r", "user",
                            git=fake_git({("rev-parse", "--verify",
                                           f"{SHA_C}^{{commit}}"):
                                          RuntimeError("unknown")}))
        self.assertTrue(ctx.exception.next_cmd.startswith("git -C "))
        self.assertIsNone(self._judge(ctx.exception.next_cmd))
        # Through the CLI: a transport refusal with an empty next_cmd is
        # typed blocked, next null, remedy present.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.object(
                    transport, "take",
                    side_effect=transport.Refusal("planted", "")):
            env = Path(tmp) / "r.md"
            env.write_text(request_text(), encoding="utf-8")
            code, out = run_cli(["--ledger-dir", tmp, "take", str(env),
                                 "--as", "codex"])
        self.assertNotEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["next_kind"], "blocked")
        self.assertIsNone(payload["next"])
        self.assertTrue(payload["remedy"])
        # Sanity on the judge itself, against the shapes F11 cited.
        for prose in ("check remote access to u, then re-run loupe take",
                      "git fetch --unshallow or fetch the base ref, then "
                      "re-run loupe take",
                      "return the envelope to the author",
                      "remove 'x' from the disposition and re-run",
                      f"{TOOL_NAME} take r.md --as <id>",
                      f"git fetch u r && {TOOL_NAME} take <envelope>"):
            self.assertIsNotNone(self._judge(prose), prose)
        for real in ("", f"{TOOL_NAME} ledger report",
                     f"{TOOL_NAME} validate r.md  # then return it",
                     "git -C /r log --oneline -5",
                     f"git -C /r fetch u r && {TOOL_NAME} take r.md --as x"):
            self.assertIsNone(self._judge(real), real)


if __name__ == "__main__":
    unittest.main()

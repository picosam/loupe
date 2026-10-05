"""A gate the schedule attests, which a hand-off records deferred (0.28.0).

THE ASK (the operator's ruling of 2026-09-27, brief `test-and-ci-cost`):
two gates re-run most of the suite at every hand-off. Run them nightly and
before every publication instead, declared not run at hand-off with the
reason in the record. `attested_by = "ci"` cannot say that: a hand-off then
waits for CI's receipt at the reviewed commit, which a nightly-only CI never
produces.

WHAT EACH CLASS PINS:

  the grammar      `schedule` is admitted beside `ci`; every other spelling
                   is refused by name, as before.
  the runner       the hand-off's half (`run_local_gates`, and the emission's
                   `run_gates(..., defer_scheduled=True)`) records a
                   schedule-attested gate deferred and does NOT execute it —
                   proven by a marker file the command would write. Every
                   other caller executes it (the control), and a gate with no
                   attester is never deferred.
  the validator    a deferred record is a notice, not an error, but only for
                   a gate the MANIFEST declares `attested_by = "schedule"`: a
                   record cannot defer itself out of a blocking gate. Its
                   shape carries a reason and nothing only a run produces.
                   A schedule-attested gate that did run is judged as a run.
  the pre-push     `gate_before_push` does not refuse on a deferred gate, and
  refusal          still refuses the same failing command when the manifest
                   does not declare it deferred.
  the précis       `loupe brief` names deferred gates on their own line and
                   never counts them as the target's failure or as unbound.

MUTATIONS (each measured red when this module was written; see the
brief's record for the table): drop `schedule` from `config.GATE_ATTESTERS`;
ignore `defer_scheduled` in `emit.run_gates`; defer whatever the caller asks;
default `run_local_gates` to executing; drop `A-DEFERRED-UNDECLARED`; drop
the run-only-field check; fall through to the run's field table instead of
`continue`; stop filtering deferred rows out of `brief._gate_banners`.
"""
from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
import unittest.mock
from pathlib import Path

from review import brief, config, emit, env_var, validate, wire
from review.tests._transport_fixtures import copy_fixture, write_identity
from review.tests.synth import ONE_GATE, evidence_with

#: The variables a gate run exports or a shim reads. This module is itself
#: run inside the `tests` gate, whose marker would turn every record here
#: into "not run: nested inside a gate execution".
_SCRUB = (env_var("SHIM"), env_var("CALLER_PYTHONSAFEPATH"),
          env_var("CALLER_PYTHONPATH"), env_var("IN_GATE_RUN"),
          "PYTHONSAFEPATH", "PYTHONPATH", "GITHUB_ACTIONS")

#: What a gate command does when it runs: leave a marker, then exit with the
#: code it was given. The marker is how "not executed" is proven rather than
#: inferred from the record.
_PROBE = ("import pathlib, sys; pathlib.Path(sys.argv[1]).write_text('ran'); "
          "sys.exit(int(sys.argv[2]))")


def _build_repo(repo):
    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True)
    git("init", "-q", "-b", "main")
    write_identity(repo, (("user", "email", "suite@example.invalid"),
                          ("user", "name", "suite")))
    (repo / "probe.txt").write_text("probe\n", encoding="utf-8")
    git("add", "probe.txt")
    git("commit", "-q", "-m", "probe")
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True,
                          check=True).stdout.strip()


class _Harness(unittest.TestCase):
    """A copied one-commit repository, a state directory, a marker directory,
    and a two-gate manifest: `plain` (no attester) and `later`."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.repo, self.state, self.marks = (root / "repo", root / "state",
                                             root / "marks")
        self.marks.mkdir()
        self.head = copy_fixture("schedule-attested-probe", _build_repo,
                                 self.repo)

    def gate(self, gid: str, *, exit_code: int = 0, blocking: bool = True,
             attester: str | None = None) -> dict:
        row = {"id": gid, "blocking": blocking,
               "command": [sys.executable, "-c", _PROBE,
                           str(self.marks / gid), str(exit_code)]}
        if attester is not None:
            row["attested_by"] = attester
        return row

    def cfg(self, *gates: dict):
        return dataclasses.replace(ONE_GATE, repo_root=self.repo,
                                   ledger_dir=self.state, gates=list(gates))

    def two(self, *, later_exit: int = 1, attester: str | None = "schedule"):
        """`plain` passes; `later` would FAIL if it ran, so a deferral that
        silently executed would also turn the run red."""
        return self.cfg(self.gate("plain"),
                        self.gate("later", exit_code=later_exit,
                                  attester=attester))

    def scrubbed(self):
        base = {k: v for k, v in os.environ.items() if k not in _SCRUB}
        return unittest.mock.patch.dict(os.environ, base, clear=True)

    def ran(self) -> list[str]:
        return sorted(p.name for p in self.marks.iterdir())

    def by_id(self, records) -> dict:
        return {r["id"]: r for r in records}


class TestTheGrammar(unittest.TestCase):
    def shape(self, value: str):
        return config.check_shape(tomllib.loads(
            f'[[gates]]\nid = "t"\ncommand = ["true"]\nblocking = true\n'
            f'attested_by = {value}\n'), "test:review.toml")

    def test_schedule_and_ci_are_admitted(self):
        for value in ('"schedule"', '"ci"'):
            with self.subTest(value=value):
                self.shape(value)

    def test_every_near_spelling_is_refused_by_name(self):
        for bad in ('"Schedule"', '" schedule"', '"schedule "', '""',
                    '"nightly"', '"scheduled"', 'true', '1', '["schedule"]'):
            with self.subTest(value=bad):
                with self.assertRaises(config.ConfigError) as cm:
                    self.shape(bad)
                self.assertIn("attested_by", str(cm.exception))
                self.assertIn("'schedule'", str(cm.exception))


class TestTheHandOffDefers(_Harness):
    def test_the_hand_offs_half_records_it_deferred_and_never_runs_it(self):
        with self.scrubbed():
            local = emit.run_local_gates(self.two(), self.head)
        recs = self.by_id(local.records)
        self.assertEqual([r["id"] for r in local.records], ["plain", "later"])
        self.assertEqual(recs["later"], {
            "id": "later", "blocking": True, "attested_by": "schedule",
            "deferred": emit.SCHEDULE_DEFERRED})
        self.assertEqual(recs["plain"]["exit_code"], 0)
        self.assertEqual(self.ran(), ["plain"], "the deferred command ran")

    def test_the_emission_carries_the_deferral_without_running_it(self):
        cfg = self.two()
        with self.scrubbed():
            local = emit.run_local_gates(cfg, self.head)
            final = emit.run_gates(cfg, self.head, prior=local,
                                   defer_scheduled=True)
        self.assertEqual(final, list(local.records))
        self.assertEqual(self.ran(), ["plain"])

    def test_the_emission_with_no_prior_half_defers_too(self):
        with self.scrubbed():
            recs = self.by_id(emit.run_gates(self.two(), self.head,
                                             defer_scheduled=True))
        self.assertIn("deferred", recs["later"])
        self.assertEqual(self.ran(), ["plain"])

    def test_a_gate_with_no_attester_is_never_deferred(self):
        cfg = self.cfg(self.gate("plain"), self.gate("other", exit_code=1))
        with self.scrubbed():
            recs = self.by_id(emit.run_local_gates(cfg, self.head).records)
        self.assertEqual(recs["other"]["exit_code"], 1)
        self.assertEqual(self.ran(), ["other", "plain"])


class TestEveryOtherCallerExecutesIt(_Harness):
    """The paired control: the same manifest, run the way a scheduled or
    pre-publication full run calls it, executes the gate."""

    def test_run_gates_without_the_hand_off_flag_executes_it(self):
        with self.scrubbed():
            recs = self.by_id(emit.run_gates(self.two(), self.head))
        self.assertEqual(recs["later"]["exit_code"], 1)
        self.assertNotIn("deferred", recs["later"])
        self.assertEqual(self.ran(), ["later", "plain"])

    def test_the_local_half_executes_it_when_told_not_to_defer(self):
        with self.scrubbed():
            recs = self.by_id(emit.run_local_gates(
                self.two(), self.head, defer_scheduled=False).records)
        self.assertEqual(recs["later"]["exit_code"], 1)
        self.assertEqual(self.ran(), ["later", "plain"])


class TestThePrePushRefusal(_Harness):
    def test_a_deferred_gate_does_not_stop_the_hand_off(self):
        with self.scrubbed():
            local = emit.gate_before_push(self.two(),
                                          {"sha": self.head,
                                           "committed": False})
        self.assertIn("deferred", self.by_id(local.records)["later"])

    def test_the_same_failing_gate_undeclared_still_refuses(self):
        with self.scrubbed():
            with self.assertRaises(emit.GatesRefused) as cm:
                emit.gate_before_push(self.two(attester=None),
                                      {"sha": self.head, "committed": False})
        self.assertIn("later", str(cm.exception))


class TestTheValidator(unittest.TestCase):
    """Judged against the manifest, never against the record's own claim."""

    RECORD = {"id": "later", "blocking": True, "attested_by": "schedule",
              "deferred": emit.SCHEDULE_DEFERRED}

    def cfg(self, attester: str | None = "schedule"):
        row = {"id": "later", "command": ["true"], "blocking": True}
        if attester is not None:
            row["attested_by"] = attester
        return dataclasses.replace(ONE_GATE, gates=[row])

    def items(self, rec, attester: str | None = "schedule"):
        return validate.validate_attestations(evidence_with([rec]),
                                              self.cfg(attester))

    def errors(self, rec, attester: str | None = "schedule") -> set:
        return {i.code for i in self.items(rec, attester)
                if i.level == "error"}

    def test_a_declared_deferral_is_a_notice_and_no_error(self):
        items = self.items(dict(self.RECORD))
        self.assertEqual({i.code for i in items if i.level == "error"}, set())
        notices = [i for i in items if i.code == "A-DEFERRED"]
        self.assertEqual(len(notices), 1)
        self.assertIn("not run at hand-off", notices[0].message)

    def test_a_record_cannot_defer_a_gate_the_manifest_runs(self):
        for attester in (None, "ci"):
            with self.subTest(manifest=attester):
                self.assertIn("A-DEFERRED-UNDECLARED",
                              self.errors(dict(self.RECORD), attester))

    def test_the_deferred_shape(self):
        bad = {
            "no reason": {"deferred": ""},
            "blank reason": {"deferred": "   "},
            "reason not text": {"deferred": 3},
            "no attester": {"attested_by": None},
            "ci attester": {"attested_by": "ci"},
            "an exit code": {"exit_code": 0},
            "an output": {"output": {"pointer": "x"}},
            "a duration": {"duration_s": 1.0},
            "a could-not-run error": {"error": "x"},
        }
        for label, change in bad.items():
            with self.subTest(label):
                rec = {**self.RECORD, **change}
                rec = {k: v for k, v in rec.items() if v is not None}
                self.assertIn("A-DEFERRED-SHAPE", self.errors(rec))

    def test_the_deferred_shape_is_closed(self):
        """Lineage Lb8575b3a9a round 1 F1: a deferred record admits exactly
        `validate.DEFERRED_SHAPE`. The execution fields are taken from the
        validator's own run table and from a complete ran-record and a
        CI-attested one, so a field added to either is covered here without
        an edit. FALSIFICATION. Mutation: go back to rejecting only
        exit_code/output/duration_s/error, and every other execution row
        turns red; drop the `blocking` check, and its two rows turn red."""
        from review.tests.synth import complete_attestation
        self.assertEqual(set(self.RECORD), set(validate.DEFERRED_SHAPE))
        # The control: exactly the shape, only the notice.
        items = self.items(dict(self.RECORD))
        self.assertEqual({i.code for i in items if i.level == "error"}, set())
        self.assertEqual([i.code for i in items], ["A-DEFERRED"])
        ran = complete_attestation()
        ci = {"ci_run": {"id": 1, "url": "https://example.invalid/run/1",
                         "status": "completed", "conclusion": "success",
                         "head_sha": "a" * 40, "workflow": "gates",
                         "completed": "2026-09-28T00:00:00Z"},
              "receipt": {"id": "later", "exit_code": 0}}
        execution = ({name for name, *_ in validate._ATTESTATION_FIELDS}
                     | set(ran) | set(ci) | {"runner", "error", "note"})
        execution -= set(validate.DEFERRED_SHAPE)
        for name in ("binding", "tree", "executed_sha", "target_sha",
                     "tool_version", "ci_run", "receipt", "command"):
            self.assertIn(name, execution)    # the finding's named fields
        values = {**ran, **ci, "runner": "loupe/0.28.0", "error": "x",
                  "note": "anything"}
        for name in sorted(execution):
            with self.subTest(field=name):
                rec = {**self.RECORD, name: values[name]}
                self.assertIn("A-DEFERRED-SHAPE", self.errors(rec))
        for label, blocking in (("no blocking", None),
                                ("blocking not a boolean", "yes")):
            with self.subTest(label):
                rec = {**self.RECORD, "blocking": blocking}
                rec = {k: v for k, v in rec.items() if v is not None}
                self.assertIn("A-DEFERRED-SHAPE", self.errors(rec))

    def test_a_schedule_gate_that_ran_is_judged_as_a_run(self):
        """No deferral claimed: the run's own result decides, so a red run of
        a blocking schedule-attested gate is still an error."""
        from review.tests.synth import attestation_records
        for code, want_error in ((0, False), (1, True)):
            with self.subTest(exit_code=code):
                [rec] = attestation_records("a" * 40, gate_id="later",
                                            exit_code=code, blocking=True)
                rec["attested_by"] = "schedule"
                errors = self.errors(rec)
                self.assertEqual("A-FAILED" in errors, want_error, errors)
                self.assertNotIn("A-DEFERRED-SHAPE", errors)


class TestThePrecis(unittest.TestCase):
    DEFERRED = {"id": "later", "blocking": True, "attested_by": "schedule",
                "deferred": emit.SCHEDULE_DEFERRED}
    PASSED = {"id": "plain", "blocking": True, "exit_code": 0,
              "binding": "bound"}

    def test_a_deferred_gate_has_its_own_line_and_no_failure_line(self):
        lines = brief._gate_banners([self.PASSED, self.DEFERRED])
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("deferred to the schedule", lines[0])
        self.assertIn("later", lines[0])

    def advisory_line(self, *recs):
        lines = [l for l in brief._gate_banners(list(recs))
                 if "Advisory gates" in l]
        self.assertEqual(len(lines), 1, lines)
        return lines[0]

    def test_a_deferred_blocking_gate_is_never_counted_as_passed(self):
        """FALSIFICATION — lineage L1ee41bf159's tool feedback. Mutation:
        restore `rest = "every blocking gate passed at the target" if not
        hard` and the claim covers `later`, which never ran."""
        soft = {"id": "ci-evidence", "blocking": False, "exit_code": 1,
                "binding": "bound"}
        line = self.advisory_line(self.PASSED, self.DEFERRED, soft)
        self.assertNotIn("every blocking gate passed", line)
        self.assertIn("every blocking gate that ran passed at the target; "
                      "1 blocking gate(s) deferred, not run: later", line)

    def test_the_admitted_domain_of_the_advisory_line(self):
        """Every combination the line can meet: blocking failure or not,
        deferred rows blocking, non-blocking or of unknown blocking.
        Mutations: drop the `blocking is False` exemption (the non-blocking
        row reads as deferred blocking) or narrow it to `blocking is True`
        (the unknown row reads as passed)."""
        soft = {"id": "ci-evidence", "blocking": False, "exit_code": 1,
                "binding": "bound"}
        hard = {"id": "tests", "blocking": True, "exit_code": 1,
                "binding": "bound"}
        nb_deferred = {**self.DEFERRED, "id": "optional", "blocking": False}
        unknown = {k: v for k, v in self.DEFERRED.items() if k != "blocking"}
        unknown["id"] = "unknown"
        passed = "every blocking gate passed at the target"
        cases = {
            # control: nothing deferred, the unqualified claim is true
            "no deferral": ([self.PASSED, soft], passed, "deferred, not run"),
            "only a non-blocking deferral": (
                [self.PASSED, nb_deferred, soft], passed, "deferred, not run"),
            "a blocking deferral": (
                [self.PASSED, self.DEFERRED, soft],
                "deferred, not run: later", passed),
            "blocking unknown on a deferral": (
                [self.PASSED, unknown, soft],
                "deferred, not run: unknown", passed),
            "both kinds deferred": (
                [self.PASSED, self.DEFERRED, nb_deferred, soft],
                "1 blocking gate(s) deferred, not run: later", "optional"),
            "a blocking failure wins": (
                [hard, self.DEFERRED, soft],
                "see the blocking failures above", passed),
        }
        for name, (recs, want, never) in cases.items():
            with self.subTest(case=name):
                line = self.advisory_line(*recs)
                self.assertIn(want, line)
                self.assertNotIn(never, line)

    def test_the_precis_carries_the_qualified_line(self):
        """The real entry point: a request whose evidence holds a deferred
        blocking gate and a red advisory one."""
        from review.tests.test_take_compact import envelope, record
        env = envelope([record(), record("ci-evidence", exit_code=1,
                                         blocking=False),
                        {**self.DEFERRED, "id": "later"}])
        text = brief.request_precis(wire.parse_request(env))
        self.assertIn("every blocking gate that ran passed at the target",
                      text)
        self.assertNotIn("every blocking gate passed", text)

    def test_the_blocking_count_counts_blocking_rows_that_ran(self):
        """The N of "k of N BLOCKING" over its admitted domain: non-blocking
        rows are out, deferred rows are out (they have their own line), and
        a row of unknown blocking or not an object is in. Mutations: count
        every row (`len(records)`) and the mixed case reads 1 of 3; count
        only `blocking is True` and the unknown case reads 1 of 1."""
        hard = {"id": "tests", "blocking": True, "exit_code": 1,
                "binding": "bound"}
        soft = {"id": "ci-evidence", "blocking": False, "exit_code": 0,
                "binding": "bound"}
        unknown = {"id": "lint", "exit_code": 0, "binding": "bound"}
        cases = {
            # control: every row blocking, the old and new counts agree
            "all blocking": ([hard, self.PASSED], "1 of 2 BLOCKING"),
            "a non-blocking row": ([hard, self.PASSED, soft],
                                   "1 of 2 BLOCKING"),
            "a deferred row": ([hard, self.PASSED, self.DEFERRED],
                               "1 of 2 BLOCKING"),
            "blocking unknown": ([hard, unknown], "1 of 2 BLOCKING"),
            "not an object": ([hard, "tests"], "2 of 2 BLOCKING"),
        }
        for name, (recs, want) in cases.items():
            with self.subTest(case=name):
                text = "\n".join(brief._gate_banners(list(recs)))
                self.assertIn(want, text)

    def test_the_control_a_not_run_blocking_gate_is_a_failure(self):
        not_run = {"id": "later", "blocking": True, "error": "not run: x"}
        text = "\n".join(brief._gate_banners([self.PASSED, not_run]))
        self.assertIn("BLOCKING gate(s) did not pass", text)
        self.assertNotIn("deferred to the schedule", text)


if __name__ == "__main__":
    unittest.main()

"""Heavy gates run last, and not at all after a red blocking gate (0.30.0).

THE ASK (brief `shared-checkout-guards`, guard 2, ruled by the operator
on 2026-10-06): on 2026-10-05 a full manifest ran 10m49s of heavy gates
after `extraction-audit` had failed in three seconds, and the run's answer
was that gate's. A `[[gates]]` row may now declare `heavy = true`; the runner
executes every other gate first and the heavy ones only when every blocking
gate of the first phase passed. Otherwise each heavy gate is recorded in the
not-run shape, `not run: blocked by <ids>`, with the ids in `blocked_by`.

THE PARTITION, one class per row below (each row is its own test, so each
has its own result line):

  dimension            classes
  `heavy` spelling     true | false | absent (admitted) ; "true" | "false" |
                       1 | 0 | a table | an array (refused, naming `heavy`)
  reader version       current (admits) | the prior grammar (refuses,
                       naming the skew) | the prior reader with the
                       key's floor declared (refuses, naming both)
  first phase result   all pass | a blocking red | a blocking gate that
                       cannot execute | a non-blocking red | several
                       blocking reds | a red gate after a heavy one in the
                       manifest
  heavy gate kind      blocking | non-blocking | deferred to the schedule
                       (hand-off) | a red heavy gate among heavy gates
  executor             this run | CI-attested (outside CI: never executed
                       here, never blocks) | nested inside a gate run
  execution model      the pool (4 workers) | sequential
                       (`LOUPE_GATE_WORKERS=1`)
  consumer             the validator | the pre-push refusal | the CI
                       receipt | the reviewer's precis and table

  The control beside every block is the same manifest with the first phase
  green: the heavy gates then run, after it. A manifest declaring no heavy
  gate runs as it always did (`TestNoHeavyGateIsTheOldRun`).

Not executed is PROVEN, not inferred: every gate command is a probe that
writes a marker holding its start and end times, so a blocked gate is one
whose marker does not exist, and "after" is the heavy gate's start against
the first phase's last end.

MUTATIONS (each run with `python -B`, the module's rows red in the brief's
record): drop `heavy` from `config.GATE_KEYS`; drop the bool check; run the
heavy phase whatever the blockers (`if not blockers` -> `if True`); count a
non-blocking red as a blocker; put the heavy gates in the first phase; drop
the sort in `emit.local_gate_failures`.

THE VALIDATOR'S BLOCKED RECORD (F1 of lineage L33abf7864f round 1),
re-run 2026-10-11, each red in its own rows of
`TestTheValidatorClosesTheBlockedRecord`: `_blocked_problems` returning [];
the validator never calling it; each rule dropped in turn (a blocked reason
without `blocked_by`, `blocked_by` beside another reason, the type check,
repetition, agreement with the reason, the gate's own heaviness, and each
blocker class: undeclared, non-blocking, heavy, no record, deferred,
passing); `emit.local_gate_failures` no longer passing `whole`.
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

from review import TOOL_VERSION, brief, config, emit, env_var, validate
from review.tests._transport_fixtures import copy_fixture, write_identity
from review.tests.synth import ONE_GATE, complete_attestation, evidence_with

#: The variables a gate run exports or a shim reads; this module itself runs
#: inside the `tests` gate, whose marker would make every record "nested".
_SCRUB = (env_var("SHIM"), env_var("CALLER_PYTHONSAFEPATH"),
          env_var("CALLER_PYTHONPATH"), env_var("IN_GATE_RUN"),
          env_var("GATE_WORKERS"), "PYTHONSAFEPATH", "PYTHONPATH",
          "GITHUB_ACTIONS")

#: A gate command: sleep, then write `<start ns> <end ns>` to the marker and
#: exit with the code given.
_PROBE = ("import pathlib, sys, time; s = time.time_ns(); "
          "time.sleep(float(sys.argv[3])); "
          "pathlib.Path(sys.argv[1]).write_text(f'{s} {time.time_ns()}'); "
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
    """A copied one-commit repository, a state directory and a marker
    directory; gates are built per row."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.repo, self.state, self.marks = (root / "repo", root / "state",
                                             root / "marks")
        self.marks.mkdir()
        self.head = copy_fixture("fail-fast-probe", _build_repo, self.repo)

    def gate(self, gid: str, *, exit_code: int = 0, blocking: bool = True,
             heavy: bool | None = None, attester: str | None = None,
             sleep: float = 0.0, command: list | None = None) -> dict:
        row = {"id": gid, "blocking": blocking,
               "command": command or [sys.executable, "-c", _PROBE,
                                      str(self.marks / gid), str(exit_code),
                                      str(sleep)]}
        if heavy is not None:
            row["heavy"] = heavy
        if attester is not None:
            row["attested_by"] = attester
        return row

    def cfg(self, *gates: dict):
        return dataclasses.replace(ONE_GATE, repo_root=self.repo,
                                   ledger_dir=self.state, gates=list(gates))

    def scrubbed(self, **extra):
        base = {k: v for k, v in os.environ.items() if k not in _SCRUB}
        base.update(extra)
        return unittest.mock.patch.dict(os.environ, base, clear=True)

    def run_gates(self, cfg, **kwargs):
        with self.scrubbed():
            return emit.run_gates(cfg, self.head, **kwargs)

    def ran(self) -> list[str]:
        return sorted(p.name for p in self.marks.iterdir())

    def times(self, gid: str) -> tuple[int, int]:
        start, end = (self.marks / gid).read_text().split()
        return int(start), int(end)

    def by_id(self, records) -> dict:
        return {r["id"]: r for r in records}

    def assert_after(self, first: list[str], heavy: list[str]) -> None:
        last_end = max(self.times(g)[1] for g in first)
        for gid in heavy:
            self.assertGreaterEqual(self.times(gid)[0], last_end,
                                    f"{gid} started before the first phase "
                                    f"ended")

    def assert_blocked(self, rec: dict, blockers: list[str],
                       blocking: bool = True) -> None:
        self.assertEqual(rec, {"id": rec["id"], "blocking": blocking,
                               "error": emit.blocked_reason(blockers),
                               "blocked_by": blockers})
        self.assertEqual(rec["error"],
                         "not run: blocked by " + ", ".join(blockers))


# --------------------------------------------------------------- the grammar

class TestTheGrammar(unittest.TestCase):
    def shape(self, line: str):
        return config.check_shape(tomllib.loads(
            f'[[gates]]\nid = "t"\ncommand = ["true"]\nblocking = true\n'
            f'{line}\n'), "test:review.toml")

    def test_true_is_admitted(self):
        self.shape("heavy = true")

    def test_false_is_admitted(self):
        self.shape("heavy = false")

    def test_absent_is_admitted(self):
        self.shape("")

    def test_every_other_spelling_is_refused_naming_the_key(self):
        """Each refusal's paired control is the row above it: the same row
        with a TOML boolean passes."""
        for bad in ('"true"', '"false"', '1', '0', '{}', '{ on = true }',
                    '[true]'):
            with self.subTest(value=bad):
                with self.assertRaises(config.ConfigError) as cm:
                    self.shape(f"heavy = {bad}")
                self.assertIn("`heavy` must be true or false",
                              str(cm.exception))

    def test_a_misspelling_is_still_an_unknown_key(self):
        """The grammar stays closed: `heavvy` is not `heavy`."""
        with self.assertRaises(config.ConfigError) as cm:
            self.shape("heavvy = true")
        self.assertIn("unknown key(s) ['heavvy']", str(cm.exception))


#: The release `heavy` joined the gate grammar in, and the one before it: a
#: fact about history (the CHANGELOG's section of that version names the
#: key), not a configured value, like test_config_compat's 0.12.1.
HEAVY_SINCE, PRIOR = "0.30.0", "0.29.0"


class TestTheKeyIsACrossInstallationContract(unittest.TestCase):
    """Brief `config-keys-are-a-cross-installation-contract`: the prior
    reader (this grammar without `heavy`) refuses a manifest declaring it,
    and says a skew is possible; with the floor the key needs declared, it
    names both versions instead. The current reader admits the same
    bytes."""

    MANIFEST = ('[[gates]]\nid = "t"\ncommand = ["true"]\nblocking = true\n'
                'heavy = true\n')

    def old_reader(self):
        older = tuple(k for k in config.GATE_KEYS if k != "heavy")
        return unittest.mock.patch.object(config, "GATE_KEYS", older)

    def test_the_prior_grammar_refuses_naming_the_skew(self):
        with self.old_reader(), self.assertRaises(config.ConfigError) as cm:
            config.check_shape(tomllib.loads(self.MANIFEST),
                               "repo:review.toml")
        self.assertIn("unknown key(s) ['heavy']", str(cm.exception))
        self.assertIn("version skew", cm.exception.remedy)

    def test_with_the_floor_declared_it_names_both_versions(self):
        user = tomllib.loads(f'[tool]\nrequires = "{HEAVY_SINCE}"\n'
                             + self.MANIFEST)
        with self.assertRaises(config.ConfigError) as cm:
            config.check_tool_version(user, "repo:review.toml",
                                      installed=PRIOR)
        self.assertIn(f"requires loupe {HEAVY_SINCE} or newer",
                      str(cm.exception))
        self.assertIn(f"this installation is {PRIOR}", str(cm.exception))

    def test_the_current_reader_admits_the_same_bytes(self):
        user = tomllib.loads(f'[tool]\nrequires = "{TOOL_VERSION}"\n'
                             + self.MANIFEST)
        config.check_tool_version(user, "repo:review.toml")
        config.check_shape(user, "repo:review.toml")


# ---------------------------------------------------------------- the runner

class TestNoHeavyGateIsTheOldRun(_Harness):
    """The control for the whole feature: no row declares `heavy`, so every
    gate runs in one phase, exactly as 0.29.0 ran it. A red blocking gate
    stops nothing else, and nothing carries `blocked_by`."""

    def test_every_gate_runs_whatever_fails(self):
        cfg = self.cfg(self.gate("a", exit_code=1), self.gate("b"),
                       self.gate("c", exit_code=1, blocking=False))
        recs = self.run_gates(cfg)
        self.assertEqual([r["id"] for r in recs], ["a", "b", "c"])
        self.assertEqual([r["exit_code"] for r in recs], [1, 0, 1])
        self.assertEqual(self.ran(), ["a", "b", "c"])
        self.assertFalse(any("blocked_by" in r for r in recs))

    def test_heavy_false_is_the_same_run(self):
        cfg = self.cfg(self.gate("a", exit_code=1),
                       self.gate("b", heavy=False))
        recs = self.run_gates(cfg)
        self.assertEqual(self.by_id(recs)["b"]["exit_code"], 0)
        self.assertEqual(self.ran(), ["a", "b"])


class TestTheHeavyPhase(_Harness):

    def manifest(self, *, red=None, blocking=True):
        """`h1` sits FIRST in the manifest and `h2` last, so manifest order
        cannot be what puts them second; `f1` and `f2` take 0.3 s, so a heavy
        gate run beside them would start before they end."""
        return self.cfg(self.gate("h1", heavy=True),
                        self.gate("f1", sleep=0.3,
                                  exit_code=1 if red == "f1" else 0,
                                  blocking=blocking),
                        self.gate("f2", sleep=0.3),
                        self.gate("h2", heavy=True, blocking=False))

    def test_all_fast_pass_then_heavy_run_after_them(self):
        """The control beside every block below."""
        recs = self.run_gates(self.manifest())
        self.assertEqual([r["id"] for r in recs], ["h1", "f1", "f2", "h2"])
        self.assertEqual([r["exit_code"] for r in recs], [0, 0, 0, 0])
        self.assertEqual(self.ran(), ["f1", "f2", "h1", "h2"])
        self.assert_after(["f1", "f2"], ["h1", "h2"])

    def test_a_blocking_fast_red_executes_no_heavy_gate(self):
        recs = self.by_id(self.run_gates(self.manifest(red="f1")))
        self.assertEqual(self.ran(), ["f1", "f2"], "a heavy gate executed")
        self.assertEqual(recs["f1"]["exit_code"], 1)
        self.assertEqual(recs["f2"]["exit_code"], 0)
        self.assert_blocked(recs["h1"], ["f1"])
        self.assert_blocked(recs["h2"], ["f1"], blocking=False)

    def test_a_fast_gate_that_cannot_execute_blocks(self):
        cfg = self.cfg(self.gate("ghost", command=["no-such-binary-xyz"]),
                       self.gate("h", heavy=True))
        recs = self.by_id(self.run_gates(cfg))
        self.assertIn("could not execute", recs["ghost"]["error"])
        self.assert_blocked(recs["h"], ["ghost"])
        self.assertEqual(self.ran(), [])

    def test_a_non_blocking_fast_red_blocks_nothing(self):
        recs = self.by_id(self.run_gates(self.manifest(red="f1",
                                                       blocking=False)))
        self.assertEqual(recs["f1"]["exit_code"], 1)
        self.assertEqual(recs["h1"]["exit_code"], 0)
        self.assertEqual(self.ran(), ["f1", "f2", "h1", "h2"])
        self.assert_after(["f1", "f2"], ["h1", "h2"])

    def test_several_blocking_reds_are_all_named_in_manifest_order(self):
        cfg = self.cfg(self.gate("z-red", exit_code=1), self.gate("ok"),
                       self.gate("a-red", exit_code=3),
                       self.gate("ghost", command=["no-such-binary-xyz"]),
                       self.gate("h", heavy=True))
        recs = self.by_id(self.run_gates(cfg))
        self.assert_blocked(recs["h"], ["z-red", "a-red", "ghost"])

    def test_a_red_heavy_gate_blocks_no_other_heavy_gate(self):
        cfg = self.cfg(self.gate("f"),
                       self.gate("h1", heavy=True, exit_code=1),
                       self.gate("h2", heavy=True))
        recs = self.by_id(self.run_gates(cfg))
        self.assertEqual(recs["h1"]["exit_code"], 1)
        self.assertEqual(recs["h2"]["exit_code"], 0)

    def test_the_sequential_path_keeps_both_phases(self):
        """`LOUPE_GATE_WORKERS=1`, the way back that needs no code change:
        same phases, same block."""
        with self.scrubbed(**{env_var("GATE_WORKERS"): "1"}):
            green = emit.run_gates(self.manifest(), self.head)
        self.assertEqual([r["exit_code"] for r in green], [0, 0, 0, 0])
        self.assert_after(["f1", "f2"], ["h1", "h2"])
        for path in self.marks.iterdir():
            path.unlink()
        with self.scrubbed(**{env_var("GATE_WORKERS"): "1"}):
            red = self.by_id(emit.run_gates(self.manifest(red="f1"),
                                            self.head))
        self.assertEqual(self.ran(), ["f1", "f2"])
        self.assert_blocked(red["h1"], ["f1"])

    def test_nested_inside_a_gate_run_nothing_is_blocked(self):
        """The re-entrancy guard is unchanged: every gate, heavy or not, is
        `not run: nested`, and none is said to be blocked."""
        with self.scrubbed(**{env_var("IN_GATE_RUN"): "1"}):
            recs = emit.run_gates(self.manifest(red="f1"), self.head)
        self.assertEqual({r["error"] for r in recs},
                         {"not run: nested inside a gate execution"})
        self.assertFalse(any("blocked_by" in r for r in recs))
        self.assertEqual(self.ran(), [])


class TestGatesThisRunDoesNotExecute(_Harness):
    """A gate answered by another executor neither blocks nor is blocked."""

    def test_a_deferred_heavy_gate_stays_deferred_at_a_hand_off(self):
        cfg = self.cfg(self.gate("red", exit_code=1),
                       self.gate("later", heavy=True, attester="schedule"),
                       self.gate("h", heavy=True))
        with self.scrubbed():
            local = emit.run_local_gates(cfg, self.head)
        recs = self.by_id(local.records)
        self.assertEqual(recs["later"]["deferred"], emit.SCHEDULE_DEFERRED)
        self.assertNotIn("blocked_by", recs["later"])
        self.assert_blocked(recs["h"], ["red"])
        self.assertEqual(self.ran(), ["red"])

    def test_outside_a_hand_off_the_schedule_gate_is_blocked_like_any(self):
        """The control: every runner but a hand-off executes a
        schedule-attested gate, so there it is an ordinary heavy gate."""
        cfg = self.cfg(self.gate("red", exit_code=1),
                       self.gate("later", heavy=True, attester="schedule"))
        recs = self.by_id(self.run_gates(cfg))
        self.assert_blocked(recs["later"], ["red"])

    def test_a_red_ci_answer_blocks_nothing(self):
        """Outside CI a CI-attested gate is awaited, never executed: its
        answer arrives beside the local gates (and, at a hand-off, after
        the push), so it cannot gate the heavy phase. `_await_ci` is
        replaced by its red answer; the heavy gate still runs."""
        cfg = self.cfg(self.gate("remote", attester="ci"),
                       self.gate("h", heavy=True))
        red = {"remote": {"id": "remote", "blocking": True,
                          "attested_by": "ci", "exit_code": 1}}
        with unittest.mock.patch.object(emit, "_await_ci",
                                        return_value=red):
            recs = self.by_id(self.run_gates(cfg))
        self.assertEqual(recs["remote"]["exit_code"], 1)
        self.assertEqual(recs["h"]["exit_code"], 0)
        self.assertEqual(self.ran(), ["h"])

    def test_inside_ci_a_ci_gate_executes_and_blocks_like_any(self):
        """The control: on the runner every gate executes here, so a red
        CI-attested fast gate is a red fast gate."""
        cfg = self.cfg(self.gate("remote", attester="ci", exit_code=1),
                       self.gate("h", heavy=True))
        with self.scrubbed(GITHUB_ACTIONS="true"):
            recs = self.by_id(emit.run_gates(cfg, self.head))
        self.assert_blocked(recs["h"], ["remote"])


# ---------------------------------------------------- what reads the record

class TestTheHandOffRefusal(_Harness):
    def manifest(self, red: bool = True):
        return self.cfg(self.gate("heavy-first", heavy=True),
                        self.gate("quick", exit_code=1 if red else 0),
                        self.gate("heavy-last", heavy=True))

    def test_a_red_fast_gate_refuses_naming_it_first(self):
        with self.scrubbed():
            with self.assertRaises(emit.GatesRefused) as cm:
                emit.gate_before_push(self.manifest(),
                                      {"sha": self.head, "committed": False})
        message = str(cm.exception)
        self.assertTrue(message.startswith(
            f"1 local gate(s) did not pass at {self.head[:12]}: quick; "
            f"2 heavy gate(s) not run, blocked by quick: heavy-first, "
            f"heavy-last — "), message)
        first = cm.exception.items[0]
        self.assertEqual((first.code, first.message.split(":")[0]),
                         ("A-FAILED", "quick"))
        self.assertEqual([i.message.split(":")[0]
                          for i in cm.exception.items[1:]],
                         ["heavy-first", "heavy-last"])
        self.assertEqual(self.ran(), ["quick"])

    def test_the_control_hands_off_and_runs_the_heavy_gates(self):
        with self.scrubbed():
            local = emit.gate_before_push(self.manifest(red=False),
                                          {"sha": self.head,
                                           "committed": False})
        self.assertEqual([r["exit_code"] for r in local.records], [0, 0, 0])
        self.assertEqual(self.ran(), ["heavy-first", "heavy-last", "quick"])

    def test_a_red_with_no_heavy_gate_reads_as_before(self):
        """No heavy gate: the 0.29.0 sentence, byte for byte."""
        cfg = self.cfg(self.gate("a", exit_code=1), self.gate("b"))
        with self.scrubbed():
            with self.assertRaises(emit.GatesRefused) as cm:
                emit.gate_before_push(cfg, {"sha": self.head,
                                            "committed": False})
        self.assertTrue(str(cm.exception).startswith(
            f"1 local gate(s) did not pass at {self.head[:12]}: a — judged "
            f"by the request validator's own attestation rule before "
            f"anything left this machine."), str(cm.exception))


class TestTheValidatorClosesTheBlockedRecord(unittest.TestCase):
    """A blocked record is the not-run shape plus `blocked_by`: an error for
    a blocking gate, a notice for a non-blocking one, never a shape error.

    Its `blocked_by` is evidence a structured consumer reads instead of the
    reason, so the validator judges it against what it claims (lineage
    L33abf7864f round 1, F1). The control is a COMPLETE block: two failed
    blocking fast gates and the heavy gates they blocked, beside a passing
    gate, a failed non-blocking one and a deferral. Each row corrupts one
    thing in it and must add exactly one `A-BLOCKED-SHAPE` error, naming the
    record.

      dimension            classes (all refused)
      presence             a blocked reason with no `blocked_by`
      type                 a string | an empty list | a non-string member |
                           an empty-string member
      repetition           an id twice
      agreement            the reason's order differs | its contents differ
      the record's gate    not declared `heavy`
      each blocker         undeclared | non-blocking | heavy | passing |
                           deferred | declared but absent from the block
      the converse         `blocked_by` beside a reason that is not a
                           blocked reason (an unrelated could-not-execute
                           record)
    """

    GOOD = ["quick", "quick2"]
    GATES = (
        {"id": "quick", "command": ["true"], "blocking": True},
        {"id": "quick2", "command": ["true"], "blocking": True},
        {"id": "other", "command": ["true"], "blocking": True},
        {"id": "advice", "command": ["true"], "blocking": False},
        {"id": "later", "command": ["true"], "blocking": True,
         "attested_by": "schedule"},
        {"id": "deep", "command": ["true"], "blocking": True, "heavy": True},
        {"id": "h", "command": ["true"], "blocking": True, "heavy": True},
    )

    @staticmethod
    def blocked(ids, gate: str = "h", error: str | None = None, **fields):
        rec = {"id": gate, "blocking": True,
               "error": emit.blocked_reason(ids) if error is None else error,
               "blocked_by": list(ids)}
        rec.update(fields)
        return rec

    def cfg(self, h_blocking: bool = True):
        return dataclasses.replace(ONE_GATE, gates=[
            dict(g, blocking=h_blocking if g["id"] == "h" else g["blocking"])
            for g in self.GATES])

    def records(self, **over) -> list:
        recs = {
            "quick": complete_attestation(id="quick", exit_code=1),
            "quick2": complete_attestation(id="quick2", exit_code=1),
            "other": complete_attestation(id="other"),
            "advice": complete_attestation(id="advice", exit_code=1,
                                           blocking=False),
            "later": {"id": "later", "blocking": True,
                      "attested_by": "schedule",
                      "deferred": "attested by the scheduled run"},
            "deep": self.blocked(self.GOOD, gate="deep"),
            "h": self.blocked(self.GOOD),
        }
        recs.update(over)
        return [r for r in recs.values() if r is not None]

    def judge(self, records: list, cfg=None):
        return validate.validate_attestations(
            evidence_with(records), cfg or self.cfg())

    def shape_items(self, items) -> list:
        return [i for i in items if i.code == "A-BLOCKED-SHAPE"]

    def test_the_two_spellings_of_the_reason_agree(self):
        self.assertEqual(validate.BLOCKED_PREFIX, emit.BLOCKED_PREFIX)

    def test_the_complete_block_has_no_blocked_shape_error(self):
        items = self.judge(self.records())
        self.assertEqual(self.shape_items(items), [])
        self.assertEqual(
            sorted((i.level, i.code) for i in items),
            [("error", "A-FAILED"), ("error", "A-FAILED"),
             ("error", "A-NOT-RUN"), ("error", "A-NOT-RUN"),
             ("notice", "A-DEFERRED"), ("notice", "A-FAILED")])

    def test_a_blocking_heavy_gate_blocked_is_an_error(self):
        items = [i for i in self.judge(self.records())
                 if i.code == "A-NOT-RUN" and i.message.startswith("h:")]
        self.assertEqual([(i.level, i.code) for i in items],
                         [("error", "A-NOT-RUN")])
        self.assertIn("not run: blocked by quick, quick2", items[0].message)

    def test_a_non_blocking_heavy_gate_blocked_is_a_notice(self):
        rec = self.blocked(self.GOOD, blocking=False)
        items = self.judge(self.records(h=rec), self.cfg(h_blocking=False))
        self.assertEqual(self.shape_items(items), [])
        self.assertEqual(
            [(i.level, i.code) for i in items
             if i.message.startswith("h:")], [("notice", "A-NOT-RUN")])


def _blocked_row(gate: str, fragment: str, over: dict):
    """One corruption of the complete block: `over` replaces records by id
    (None removes one), and exactly one error must name `gate`."""
    def test(self):
        items = self.judge(self.records(**over))
        shape = self.shape_items(items)
        self.assertEqual(
            len(shape), 1,
            [(i.level, i.code, i.message) for i in items])
        self.assertEqual(shape[0].level, "error")
        self.assertTrue(shape[0].message.startswith(f"{gate}: blocked "
                                                    f"record: "),
                        shape[0].message)
        self.assertIn(fragment, shape[0].message)
    return test


_B = TestTheValidatorClosesTheBlockedRecord
_GOOD, _blocked = _B.GOOD, _B.blocked
_NOT_LIST = "not a non-empty list of gate ids"
_ROWS = (
    ("absent", "h", "carries no `blocked_by`",
     {"h": {"id": "h", "blocking": True,
            "error": emit.blocked_reason(_GOOD)}}),
    ("a_string", "h", _NOT_LIST,
     {"h": _blocked(_GOOD, blocked_by="quick")}),
    ("an_empty_list", "h", _NOT_LIST, {"h": _blocked([])}),
    ("a_non_string_member", "h", _NOT_LIST,
     {"h": _blocked(_GOOD, blocked_by=["quick", 2])}),
    ("an_empty_string_member", "h", _NOT_LIST,
     {"h": _blocked(_GOOD, blocked_by=["quick", ""])}),
    ("a_repeated_id", "h", "more than once",
     {"h": _blocked(["quick", "quick"])}),
    ("the_reasons_order_differs", "h", "id for id and in order",
     {"h": _blocked(["quick2", "quick"],
                    error=emit.blocked_reason(_GOOD))}),
    ("the_reasons_contents_differ", "h", "id for id and in order",
     {"h": _blocked(["quick"], error=emit.blocked_reason(_GOOD))}),
    ("a_gate_that_is_not_heavy", "other", "does not declare its gate `heavy`",
     {"other": _blocked(_GOOD, gate="other")}),
    ("an_undeclared_blocker", "h", "declared in no manifest row",
     {"h": _blocked(["ghost"])}),
    ("a_non_blocking_blocker", "h", "is not a blocking gate",
     {"h": _blocked(["advice"])}),
    ("a_heavy_blocker", "h", "is a heavy gate",
     {"h": _blocked(["deep"])}),
    ("a_passing_blocker", "h", "passed (exit 0)",
     {"h": _blocked(["other"])}),
    ("a_deferred_blocker", "h", "was deferred and not run",
     {"h": _blocked(["later"])}),
    ("a_blocker_absent_from_the_block", "h", "has no record in this block",
     {"quick2": None, "deep": _blocked(["quick"], gate="deep")}),
    ("blocked_by_on_an_unrelated_record", "other",
     "not a blocked-by reason",
     {"other": {"id": "other", "blocking": True,
                "error": "could not execute: git is missing",
                "blocked_by": ["quick"]}}),
)
for _name, _gate, _fragment, _over in _ROWS:
    setattr(_B, f"test_refused_{_name}", _blocked_row(_gate, _fragment, _over))


class TestTheProducersBlockedRecordsAreInsideTheDomain(_Harness):
    """The closed domain admits what `run_gates` writes: a real run, judged
    by the validator, is the control the corrupted rows depart from."""

    def test_a_real_blocked_run_has_no_blocked_shape_error(self):
        cfg = self.cfg(self.gate("quick", exit_code=1),
                       self.gate("advice", exit_code=1, blocking=False),
                       self.gate("deep", heavy=True),
                       self.gate("h", heavy=True))
        records = self.run_gates(cfg)
        self.assert_blocked(self.by_id(records)["h"], ["quick"])
        items = validate.validate_attestations(evidence_with(records), cfg)
        self.assertEqual([i for i in items if i.code == "A-BLOCKED-SHAPE"],
                         [])
        self.assertEqual(
            sorted(i.message.split(":")[0] for i in items
                   if i.code == "A-NOT-RUN"), ["deep", "h"])

    def test_the_pre_push_judgement_reads_the_same_relation(self):
        """The refusal before the push judges a gate at a time, so the
        blocked record's blockers are not in its own block: it is given the
        whole run, and a corrupted `blocked_by` is refused there as well as
        in the emitted request."""
        cfg = self.cfg(self.gate("quick", exit_code=1),
                       self.gate("h", heavy=True))
        with self.scrubbed():
            local = emit.run_local_gates(cfg, self.head)
        failed, items = emit.local_gate_failures(cfg, local)
        self.assertEqual(failed, ["quick", "h"])
        self.assertEqual([i.code for i in items if i.code ==
                          "A-BLOCKED-SHAPE"], [])
        corrupt = dataclasses.replace(local, records=[
            dict(r, blocked_by=["ghost"]) if r["id"] == "h" else r
            for r in local.records])
        _, items = emit.local_gate_failures(cfg, corrupt)
        shape = [i for i in items if i.code == "A-BLOCKED-SHAPE"]
        self.assertEqual(len(shape), 2, [i.message for i in items])
        self.assertTrue(all(i.message.startswith("h: blocked record: ")
                            for i in shape))


class TestTheReceiptAndThePrecis(_Harness):
    def records(self):
        cfg = self.cfg(self.gate("quick", exit_code=1),
                       self.gate("h", heavy=True))
        return cfg, self.run_gates(cfg)

    def test_the_receipt_row_carries_the_reason(self):
        cfg, recs = self.records()
        receipt = emit.gate_receipt(self.head, cfg.gates, self.by_id(recs))
        rows = {r["id"]: r for r in receipt["gates"]}
        self.assertEqual(rows["h"], {
            "id": "h", "command": " ".join(cfg.gates[1]["command"]),
            "exit_code": None, "duration_s": None,
            "not_run": "not run: blocked by quick"})
        self.assertEqual(rows["quick"]["exit_code"], 1)

    def test_the_reviewers_table_and_banner_name_it(self):
        _, recs = self.records()
        row = brief._attestation_row(self.by_id(recs)["h"], self.head)
        self.assertIn("NOT RUN: not run: blocked by quick", row)
        banner = "\n".join(brief._gate_banners(recs))
        self.assertIn("2 of 2 BLOCKING gate(s) did not pass AT THE TARGET: "
                      "quick, h", banner)


if __name__ == "__main__":
    unittest.main()

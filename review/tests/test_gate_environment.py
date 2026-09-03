"""The CHILD environment is the caller's, not the shim's — gates and git alike.

Found on the first per-project onboarding (2026-08-19): the bin/
shim exports PYTHONSAFEPATH=1 and PYTHONPATH=<tool root> to harden the
tool's own interpreter, both environment variables, so every gate
subprocess inherited them — and twelve of that repository's sixteen gates
failed on ModuleNotFoundError under `handoff` while passing by hand.
PYTHONSAFEPATH strips the script directory and cwd from sys.path, which is
precisely what a repository's ad-hoc check scripts rely on; the leaked
PYTHONPATH additionally let any gate import this package by accident.

FALSIFICATION (from the pilot record): a gate that exits 1 when
PYTHONSAFEPATH is set or the tool root is on PYTHONPATH, run through the
emitter, exits 1 before the fix and must exit 0 after; and a PYTHONPATH the
caller genuinely set must reach the gate unchanged. Mutation: removing the
restore in `_caller_env` sends TestGateSeesCallerEnvironment red.

RVW-T21 D2 (2026-08-29) widened the subject from gates to every child
process the tool starts. A repository's git HOOKS are the repository's own
code in the same trust position as a gate — `commit -a` runs pre-commit and
commit-msg, `push` runs pre-push — and only `run_gates` passed the caller's
environment, so a `beos` pre-push hook importing a sibling module died of
the leaked PYTHONSAFEPATH and refused a handoff that the same push in a
clean environment completed. FALSIFICATION: each of the six git doors is
driven with a polluted ambient environment and the env it hands to
`subprocess.run` inspected (`TestEveryGitDoorGetsTheCallerEnvironment`),
and a real hook of exactly the pilot's shape runs through `emit._git` in a
scratch repository (`TestARealHookRunsInTheCallerEnvironment`). Mutation:
dropping `env=caller_env()` from any one door sends that door's test red.

These tests run real subprocess gates in a temp git repository of their
own, so they hold in the workbench and in an extracted candidate alike.
"""
import dataclasses
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from review import TOOL_NAME, config, emit, env_var, transport, validate
from review.ledger import Ledger
from review.tests.synth import evidence_with
from review.tests.util import REPO_ROOT

CFG = config.load(REPO_ROOT)

# The variables under test, spelled once.
SHIM = env_var("SHIM")
STASH_SAFE = env_var("CALLER_PYTHONSAFEPATH")
STASH_PATH = env_var("CALLER_PYTHONPATH")
IN_GATE = env_var("IN_GATE_RUN")

PROBE = ("import json, os; print(json.dumps({k: os.environ.get(k) for k in ("
         f"'PYTHONSAFEPATH', 'PYTHONPATH', '{SHIM}', '{STASH_SAFE}', "
         f"'{STASH_PATH}', '{IN_GATE}')}}))")


def _tool_root() -> str:
    return str(Path(emit.__file__).resolve().parent.parent)


class _GateHarness(unittest.TestCase):
    """A temp git repo with one committed file, and a probe-gate runner."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.repo = root / "repo"
        self.repo.mkdir()

        def git(*args):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                           capture_output=True)

        git("init", "-q", "-b", "main")
        git("config", "user.email", "suite@example.invalid")
        git("config", "user.name", "suite")
        (self.repo / "probe.txt").write_text("probe\n", encoding="utf-8")
        git("add", "probe.txt")
        git("commit", "-q", "-m", "probe")
        self.head = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()
        self.state = root / "state"

    def run_probe(self, environ: dict, command: list[str] | None = None):
        """run_gates with one gate under a controlled ambient environment.

        The ambient base keeps the real environment (git needs PATH and
        HOME) minus the variables under test and the re-entrancy marker —
        this suite is itself a declared gate, so during an emission every
        test here inherits the marker and run_gates would correctly return
        not-run records (the round-4 F4 probe documents the pattern).
        """
        cfg = dataclasses.replace(
            CFG, repo_root=self.repo, ledger_dir=self.state,
            gates=[{"id": "probe",
                    "command": command or [sys.executable, "-c", PROBE],
                    "blocking": True}])
        base = {k: v for k, v in os.environ.items()
                if k not in (SHIM, STASH_SAFE, STASH_PATH, IN_GATE,
                             "PYTHONSAFEPATH", "PYTHONPATH")}
        with unittest.mock.patch.dict(os.environ, {**base, **environ},
                                      clear=True):
            [rec] = emit.run_gates(cfg, self.head)
        return rec

    def probe_env(self, rec) -> dict:
        self.assertNotIn("error", rec, rec.get("error"))
        log = Path(rec["output"]["pointer"]).read_text(encoding="utf-8")
        return json.loads(log)


class TestRetainedGateOutputIsPerRun(_GateHarness):
    """FALSIFICATION (`concurrent-gates-single-flake`, measured 2026-09-02).

    Retained output was keyed by the executed SHA alone, so a second run at
    one commit overwrote the first in place. The evidence that costs is
    always the FAILING run's, and the natural response to a failing gate is
    to re-run it — so the act of investigating destroyed what the
    investigation came for, twice, and the brief's own instruction ("capture
    the retained output before anything else") could not be followed as
    written.

    A failing run then a passing run at the same commit: both logs must
    survive, each reachable from its own attestation, with `newest` naming
    the later one.

    MUTATION: retain at `<sha>/<id>.log` again and the second run overwrites
    the first — the pointers compare equal and the failing output is gone.
    """

    FAILS = "import sys; print('the failing run'); sys.exit(1)"
    PASSES = "print('the passing run')"

    def setUp(self):
        super().setUp()
        # Every test here calls run_gates, directly or through run_gate;
        # strip the re-entrancy marker for the whole test, the way
        # run_probe does per call, so the class stays hermetic under an
        # emission (this suite is a declared gate).
        base = {k: v for k, v in os.environ.items()
                if k not in (SHIM, STASH_SAFE, STASH_PATH, IN_GATE,
                             "PYTHONSAFEPATH", "PYTHONPATH")}
        patch = unittest.mock.patch.dict(os.environ, base, clear=True)
        patch.start()
        self.addCleanup(patch.stop)

    def run_gate(self, script):
        # Through the harness's environment patch: this suite is itself a
        # declared gate, so under an emission a bare run_gates inherits the
        # re-entrancy marker and correctly returns not-run records — which
        # is what refused lineage 21's first handoff (2026-09-03).
        return self.run_probe({}, command=[sys.executable, "-c", script])

    def test_a_failing_run_survives_the_next_run_at_that_commit(self):
        failed = self.run_gate(self.FAILS)
        self.assertEqual(failed["exit_code"], 1)
        passed = self.run_gate(self.PASSES)
        self.assertEqual(passed["exit_code"], 0)
        self.assertNotEqual(failed["output"]["pointer"],
                            passed["output"]["pointer"],
                            "two runs at one commit must not write one path")
        self.assertIn("the failing run",
                      Path(failed["output"]["pointer"]).read_text("utf-8"))
        self.assertIn("the passing run",
                      Path(passed["output"]["pointer"]).read_text("utf-8"))

    def test_newest_names_the_last_run(self):
        self.run_gate(self.FAILS)
        self.run_gate(self.PASSES)
        newest = (self.state / "gate-output" / self.head / emit.NEWEST_RUN
                  / "probe.log")
        self.assertIn("the passing run", newest.read_text("utf-8"))

    def test_every_gate_of_one_run_retains_together(self):
        # The run is the unit: one manifest, one directory, whatever the
        # gates did individually.
        cfg = dataclasses.replace(
            CFG, repo_root=self.repo, ledger_dir=self.state,
            gates=[{"id": "a", "command": [sys.executable, "-c",
                                           self.PASSES], "blocking": True},
                   {"id": "b", "command": [sys.executable, "-c",
                                           self.FAILS], "blocking": True}])
        records = emit.run_gates(cfg, self.head)
        parents = {Path(r["output"]["pointer"]).parent for r in records}
        self.assertEqual(len(parents), 1)
        self.assertEqual(sorted(p.name for p in parents.pop().iterdir()),
                         ["a.log", "b.log"])

    def test_prune_still_owns_the_per_sha_directory(self):
        # The retention contract is unchanged: the pruneable unit is the
        # per-SHA directory, and a run directory goes when its SHA does.
        self.run_gate(self.FAILS)
        self.run_gate(self.PASSES)
        cfg = dataclasses.replace(CFG, repo_root=self.repo,
                                  ledger_dir=self.state)
        result = transport.prune_gate_output(cfg, Ledger.in_memory())
        self.assertEqual([e["sha"] for e in result["pruned"]], [self.head])
        self.assertEqual(result["kept_unrecognised"], [])
        self.assertFalse((self.state / "gate-output" / self.head).exists())


class TestGateSeesCallerEnvironment(_GateHarness):
    """Through the shim's stash protocol, the gate gets the caller's values."""

    def test_caller_had_neither_variable(self):
        # The common case: a clean shell ran bin/loupe. No stash variables,
        # only the marker — the gate must see both variables UNSET.
        rec = self.run_probe({SHIM: "1", "PYTHONSAFEPATH": "1",
                              "PYTHONPATH": _tool_root()})
        seen = self.probe_env(rec)
        self.assertIsNone(seen["PYTHONSAFEPATH"])
        self.assertIsNone(seen["PYTHONPATH"])

    def test_a_caller_set_pythonpath_reaches_the_gate_unchanged(self):
        rec = self.run_probe({SHIM: "1", STASH_PATH: "/x",
                              "PYTHONSAFEPATH": "1",
                              "PYTHONPATH": f"{_tool_root()}{os.pathsep}/x"})
        self.assertEqual(self.probe_env(rec)["PYTHONPATH"], "/x")

    def test_a_caller_set_empty_value_is_restored_as_empty_not_unset(self):
        # Presence of the stash variable distinguishes set-to-empty from
        # unset; collapsing the two is how the defect would half-return.
        rec = self.run_probe({SHIM: "1", STASH_SAFE: "",
                              "PYTHONSAFEPATH": "1",
                              "PYTHONPATH": _tool_root()})
        self.assertEqual(self.probe_env(rec)["PYTHONSAFEPATH"], "")

    def test_the_pilot_falsification_gate_now_exits_zero(self):
        # The pilot record's falsification, verbatim in spirit: exit 1 when
        # the shim's hardening reaches the gate. Before the fix this recorded
        # exit_code 1 (A-FAILED on a blocking gate, refused emission).
        script = (f"import os, sys; sys.exit(1 if os.environ.get("
                  f"'PYTHONSAFEPATH') or {_tool_root()!r} in "
                  f"os.environ.get('PYTHONPATH', '') else 0)")
        rec = self.run_probe({SHIM: "1", "PYTHONSAFEPATH": "1",
                              "PYTHONPATH": _tool_root()},
                             command=[sys.executable, "-c", script])
        self.assertEqual(rec["exit_code"], 0)

    def test_stash_and_marker_stay_out_of_the_gate_environment(self):
        rec = self.run_probe({SHIM: "1", STASH_PATH: "/x", STASH_SAFE: "1",
                              "PYTHONSAFEPATH": "1",
                              "PYTHONPATH": _tool_root()})
        seen = self.probe_env(rec)
        self.assertIsNone(seen[SHIM])
        self.assertIsNone(seen[STASH_SAFE])
        self.assertIsNone(seen[STASH_PATH])

    def test_the_reentrancy_marker_still_reaches_the_gate(self):
        # The fix must not undo the nested-emission guard: the marker is
        # added on top of the caller environment, never dropped with it.
        rec = self.run_probe({SHIM: "1", "PYTHONSAFEPATH": "1",
                              "PYTHONPATH": _tool_root()})
        self.assertEqual(self.probe_env(rec)[IN_GATE], "1")


class TestWithoutTheShimTheApproximationApplies(_GateHarness):
    """No stash, no exact answer: drop the hardening, keep the rest.

    Round-1 F1. FALSIFICATION: only components exactly equal to the tool
    root are removed; every other component — empty ones included, which
    Python reads as the current working directory — keeps its value and its
    position. Reinstating the truthiness filter (`if p and p != own_root`)
    in `_caller_env` must fail this class.
    """

    def test_safepath_dropped_and_own_root_stripped_from_pythonpath(self):
        rec = self.run_probe({"PYTHONSAFEPATH": "1",
                              "PYTHONPATH":
                              f"{_tool_root()}{os.pathsep}/kept"})
        seen = self.probe_env(rec)
        self.assertIsNone(seen["PYTHONSAFEPATH"])
        self.assertEqual(seen["PYTHONPATH"], "/kept")

    def test_pythonpath_of_only_the_own_root_is_removed_entirely(self):
        rec = self.run_probe({"PYTHONPATH": _tool_root()})
        self.assertIsNone(self.probe_env(rec)["PYTHONPATH"])

    def test_the_component_partition_preserves_everything_but_the_root(self):
        # The complete admitted domain for the one value the fallback may
        # edit, F1's required partition: absent; empty-only; own-root-only;
        # own-root beside nonempty components in every position; own-root
        # beside empty components in every position; repeats of each. The
        # paired controls prove removal (root goes) and preservation
        # (nothing else moves) against the same inputs.
        R = _tool_root()
        sep = os.pathsep
        cases = [
            (None, None),                          # absent stays absent
            ("", ""),                              # set-empty stays exact
            (R, None),                             # root-only: nothing left
            (f"{R}{sep}{R}", None),                # repeated root-only
            (f"{R}{sep}/kept", "/kept"),           # root leading
            (f"/kept{sep}{R}", "/kept"),           # root trailing
            (f"/a{sep}{R}{sep}/b", f"/a{sep}/b"),  # root between, order kept
            (f"{sep}{R}", ""),                     # leading empty survives
            (f"{R}{sep}", ""),                     # trailing empty survives
            (f"{sep}{R}{sep}", sep),               # both empties survive
            (f"{sep}{sep}{R}{sep}/a", f"{sep}{sep}/a"),  # repeated empties
            (f"{R}{sep}{R}{sep}/a", "/a"),         # repeated root removed
            (f"/a{sep}{sep}/b", f"/a{sep}{sep}/b"),  # no root: untouched
        ]
        for value, expected in cases:
            with self.subTest(PYTHONPATH=value):
                environ = {"PYTHONSAFEPATH": "1"}
                if value is not None:
                    environ["PYTHONPATH"] = value
                seen = self.probe_env(self.run_probe(environ))
                self.assertIsNone(seen["PYTHONSAFEPATH"])
                self.assertEqual(seen["PYTHONPATH"], expected)


class TestShimProtocol(_GateHarness):
    """The real entry point, end to end: bin/loupe → emitter → gate.

    Round-1 F2. The synthetic classes above construct the post-shim state
    by hand, so they cannot falsify the shim's own half of the protocol —
    and that half carried the defect: a stash variable already present in
    the caller's environment survived an invocation whose caller variable
    was unset, and was restored into the gates as a value the caller never
    set. FALSIFICATION: drive the real `bin/loupe` over the full matrix —
    both caller variables across unset, empty, and nonempty, with the
    protocol variables absent and pre-populated with poison — and require
    every gate to see exactly the caller's values, none of the protocol
    variables, and the re-entrancy marker. Deleting the shim's
    clear-or-overwrite step must fail this class.
    """

    STATES = (None, "", "/x")  # unset, set-empty, set-nonempty
    POISON = {"LOUPE_SHIM": "1",
              "LOUPE_CALLER_PYTHONSAFEPATH": "stale",
              "LOUPE_CALLER_PYTHONPATH": "/stale"}

    def setUp(self):
        super().setUp()
        shim = REPO_ROOT / "bin" / TOOL_NAME
        if not shim.is_file():
            self.skipTest(f"no shim at {shim}: nothing end-to-end to test")
        self.shim = shim
        (self.repo / "probe.py").write_text(
            "import json, os\n"
            "print(json.dumps({k: os.environ.get(k) for k in (\n"
            f"    'PYTHONSAFEPATH', 'PYTHONPATH', {SHIM!r}, {STASH_SAFE!r},\n"
            f"    {STASH_PATH!r}, {IN_GATE!r})}}))\n",
            encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "probe.py"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-q", "-m",
                        "probe gate"], check=True, capture_output=True)
        self.head = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()
        # Round 7 F1/F2: a reviewed commit carries the rules it is judged by,
        # so this fixture's configuration lives IN the repository and is
        # committed. The env var still points at it — same bytes, and the
        # shim protocol is what this class is about.
        root = self.repo.parent
        self.config = self.repo / "review.toml"
        self.config.write_text(
            '[taxonomy]\n'
            'severities = ["High", "Low"]\n'
            'blocking = ["High"]\n'
            'classifications = ["defect"]\n'
            '[[gates]]\n'
            'id = "probe"\n'
            'command = ["python3", "probe.py"]\n'
            'blocking = true\n'
            '[roles]\n'
            'author = "claude"\n'
            'reviewer = "codex"\n'
            'relay = "user"\n'
            'permitted_authors = ["claude"]\n'
            'permitted_reviewers = ["codex"]\n',
            encoding="utf-8")
        for args in (("add", "review.toml"),
                     ("commit", "-q", "-m", "rules")):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                           capture_output=True)
        self.head = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()
        claim = root / "claim.json"
        claim.write_text(json.dumps(
            {"objective": "probe the gate environment through the shim",
             "references": [{"path": "probe.py"}]}), encoding="utf-8")
        self.claim = claim

    def emit_and_read_gate_env(self, caller: dict, poison: bool) -> dict:
        """One real emission under `caller`'s environment; the gate's view.

        The child environment starts from the real one (git, python3 and
        HOME must work) minus every protocol variable, the two caller
        variables, and the re-entrancy marker — this suite runs inside a
        declared gate during a real emission, and the marker would
        correctly turn the nested emission's gates into not-run records
        (the round-4 F4 probe documents the pattern; recursion ends here
        because the nested manifest runs the probe, not the suite).
        """
        state = self.repo.parent / f"state-{len(list(self.repo.parent.iterdir()))}"
        env = {k: v for k, v in os.environ.items()
               if k not in (SHIM, STASH_SAFE, STASH_PATH, IN_GATE,
                            "PYTHONSAFEPATH", "PYTHONPATH",
                            env_var("CONFIG"), env_var("STATE_DIR"))}
        env[env_var("CONFIG")] = str(self.config)
        env[env_var("STATE_DIR")] = str(state)
        if poison:
            env.update(self.POISON)
        env.update({k: v for k, v in caller.items() if v is not None})
        proc = subprocess.run(
            [str(self.shim), "emit-request", "--claim-file", str(self.claim),
             "--base", "HEAD~1", "--local-only"],
            cwd=self.repo, env=env, capture_output=True, text=True,
            timeout=120)
        self.assertEqual(proc.returncode, 0,
                         f"emission failed:\n{proc.stdout}\n{proc.stderr}")
        # Retained output is per RUN, and `newest` is the symlink each
        # per-SHA directory keeps pointing at the last one — the path a
        # person reaches for when they want "the log for this gate here".
        log = (state / "gate-output" / self.head / emit.NEWEST_RUN
               / "probe.log")
        return json.loads(log.read_text(encoding="utf-8"))

    def test_the_full_matrix_with_and_without_poisoned_protocol_state(self):
        for poison in (False, True):
            for safe in self.STATES:
                for path in self.STATES:
                    with self.subTest(poison=poison, PYTHONSAFEPATH=safe,
                                      PYTHONPATH=path):
                        seen = self.emit_and_read_gate_env(
                            {"PYTHONSAFEPATH": safe, "PYTHONPATH": path},
                            poison)
                        self.assertEqual(seen["PYTHONSAFEPATH"], safe)
                        self.assertEqual(seen["PYTHONPATH"], path)
                        self.assertIsNone(seen[SHIM])
                        self.assertIsNone(seen[STASH_SAFE])
                        self.assertIsNone(seen[STASH_PATH])
                        self.assertEqual(seen[IN_GATE], "1")


class _EnvRecorder:
    """Stands in for `subprocess.run`, keeping the env each call was given.

    It never executes anything: the question here is what the door HANDS to
    the operating system, which is decided before git ever starts. The
    return value is shaped by the `text` keyword the door itself passed, so
    each door's own decoding path still runs.
    """

    def __init__(self):
        self.calls = []

    def __call__(self, argv, *a, **kw):
        self.calls.append({"argv": list(argv), "env": kw.get("env")})
        empty = "" if kw.get("text") else b""
        return subprocess.CompletedProcess(argv, 0, empty, empty)


class TestEveryGitDoorGetsTheCallerEnvironment(unittest.TestCase):
    """RVW-T21 D2: all six git doors, not just the gate runner.

    The doors are the only mechanism — no `os.system`, no `Popen`, no
    `check_output` anywhere in the package — so covering each one covers
    `commit`, `push`, `fetch`, `status` and every future subcommand.

    FALSIFICATION: with PYTHONSAFEPATH=1 and the tool root on PYTHONPATH in
    the ambient environment and no shim stash (the plain `python3 -m review`
    shape, where the documented approximation applies), the env handed to
    `subprocess.run` must carry no PYTHONSAFEPATH and no tool root, while
    the caller's own PYTHONPATH component survives untouched. MUTATION:
    remove `env=caller_env()` from one door and that door's test fails with
    `env=None` — the inheriting state, which is the defect.
    """

    #: A component the caller genuinely set: it must survive, or the fix
    #: would be "clear the environment", which is a different bug.
    KEPT = "/kept-by-the-caller"

    def setUp(self):
        self.repo = REPO_ROOT           # never read: nothing is executed
        self.cfg = dataclasses.replace(CFG, repo_root=self.repo)
        self.polluted = {"PYTHONSAFEPATH": "1",
                         "PYTHONPATH": f"{_tool_root()}{os.pathsep}"
                                       f"{self.KEPT}"}

    def env_handed_to_git(self, door) -> dict:
        rec = _EnvRecorder()
        base = {k: v for k, v in os.environ.items()
                if k not in (SHIM, STASH_SAFE, STASH_PATH, IN_GATE,
                             "PYTHONSAFEPATH", "PYTHONPATH")}
        with unittest.mock.patch.dict(os.environ,
                                      {**base, **self.polluted}, clear=True):
            with unittest.mock.patch("subprocess.run", rec):
                door()
        self.assertEqual(len(rec.calls), 1,
                         "the door did not reach subprocess.run exactly once")
        return rec.calls[0]["env"]

    def assert_sanitised(self, door):
        env = self.env_handed_to_git(door)
        self.assertIsNotNone(
            env, "no env was passed, so the git child INHERITS the shim's "
                 "hardened environment — this is the defect")
        self.assertNotIn("PYTHONSAFEPATH", env)
        self.assertEqual(env.get("PYTHONPATH"), self.KEPT)
        # The stash protocol's own variables are never a child's business.
        for name in (SHIM, STASH_SAFE, STASH_PATH):
            self.assertNotIn(name, env)

    def test_emit_git(self):
        self.assert_sanitised(
            lambda: emit._git(self.repo, "rev-parse", "HEAD"))

    def test_emit_git_bytes(self):
        self.assert_sanitised(
            lambda: emit._git_bytes(self.repo, "show", "HEAD:review.toml"))

    def test_emit_is_ancestor(self):
        self.assert_sanitised(
            lambda: emit._is_ancestor(self.repo, "HEAD~1", "HEAD"))

    def test_config_git(self):
        self.assert_sanitised(
            lambda: config._git(self.repo, "rev-parse", "--show-toplevel"))

    def test_transport_git(self):
        self.assert_sanitised(
            lambda: transport._git(self.repo, "rev-parse", "HEAD"))

    def test_transport_run_bytes(self):
        self.assert_sanitised(
            lambda: transport.run_bytes(self.cfg, None, "show",
                                        "HEAD:review.toml"))

    def test_no_git_door_carries_the_gate_reentrancy_marker(self):
        """The marker stays where it is: added by `run_gates`, nowhere else.

        A hook may legitimately run a loupe READ verb; if a git subprocess
        carried IN_GATE_RUN, that nested invocation would see itself as
        running inside a gate execution and record its own gates as not
        run. FALSIFICATION: add the marker to `caller_env` and this fails.
        """
        doors = {
            "emit._git": lambda: emit._git(self.repo, "rev-parse", "HEAD"),
            "emit._git_bytes": lambda: emit._git_bytes(self.repo, "show",
                                                       "HEAD:review.toml"),
            "emit._is_ancestor": lambda: emit._is_ancestor(self.repo, "a",
                                                           "b"),
            "config._git": lambda: config._git(self.repo, "rev-parse",
                                               "--show-toplevel"),
            "transport._git": lambda: transport._git(self.repo, "rev-parse",
                                                     "HEAD"),
            "transport.run_bytes": lambda: transport.run_bytes(
                self.cfg, None, "show", "HEAD:review.toml"),
        }
        for name, door in doors.items():
            with self.subTest(door=name):
                env = self.env_handed_to_git(door)
                self.assertIsNotNone(env, "no env was passed at all")
                self.assertNotIn(IN_GATE, env)

    def test_one_authority_not_a_copy(self):
        """`emit._caller_env` is the same object as `config.caller_env`.

        The helper now has three consumers. Two implementations that agree
        today is exactly the drift this repository keeps paying for, so the
        backward-compatible name is an alias and this says so.
        """
        self.assertIs(emit._caller_env, config.caller_env)


class TestARealHookRunsInTheCallerEnvironment(unittest.TestCase):
    """End to end: a real git hook, in a real scratch repository.

    RVW-T21 D2's own reproducer, reduced to what a suite can run. The
    `beos` pre-push hook imported a sibling module and died under
    PYTHONSAFEPATH; a pre-commit hook of the same shape is the same defect
    on the same handoff path (`emit._git(repo, "commit", "-a", ...)`) with
    no remote to reach. MUTATION: drop `env=caller_env()` from `emit._git`
    and this fails — the hook exits 1, git returns non-zero, and the door
    raises `a git subprocess failed`.
    """

    def setUp(self):
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="hook-env-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        for args in (("init", "-q", "-b", "main"),
                     ("config", "user.email", "suite@example.invalid"),
                     ("config", "user.name", "suite"),
                     ("config", "commit.gpgsign", "false")):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                           capture_output=True, timeout=60)
        (self.repo / "f.txt").write_text("one\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "f.txt"],
                       check=True, capture_output=True, timeout=60)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-q", "-m",
                        "base"], check=True, capture_output=True, timeout=60)

    def install_hook(self, name: str):
        """The pilot falsification's gate, as a hook: exit 1 when the
        shim's hardening reached this process."""
        hook = self.repo / ".git" / "hooks" / name
        hook.write_text(
            f"#!{sys.executable}\n"
            "import os, sys\n"
            "sys.exit(1 if os.environ.get('PYTHONSAFEPATH') or "
            f"{_tool_root()!r} in os.environ.get('PYTHONPATH', '') else 0)\n",
            encoding="utf-8")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP
                   | stat.S_IXOTH)

    def polluted(self):
        """The post-shim ambient state, without the stash: what the caller
        of a `python3 -m review` invocation leaves behind, and the state
        the hook must never see."""
        base = {k: v for k, v in os.environ.items()
                if k not in (SHIM, STASH_SAFE, STASH_PATH, IN_GATE,
                             "PYTHONSAFEPATH", "PYTHONPATH")}
        return unittest.mock.patch.dict(
            os.environ,
            {**base, "PYTHONSAFEPATH": "1", "PYTHONPATH": _tool_root()},
            clear=True)

    def test_the_hook_the_pilot_reported_no_longer_blocks_a_commit(self):
        self.install_hook("pre-commit")
        (self.repo / "f.txt").write_text("two\n", encoding="utf-8")
        with self.polluted():
            emit._git(self.repo, "commit", "-a", "-m", "hooked")
        subject = subprocess.run(
            ["git", "-C", str(self.repo), "log", "-1", "--format=%s"],
            capture_output=True, text=True, check=True, timeout=60)
        self.assertEqual(subject.stdout.strip(), "hooked")

    def test_the_control_a_hook_that_sees_the_hardening_does_refuse(self):
        """The falsification is not passing because the hook is inert: the
        SAME hook, handed the hardened environment, blocks the commit."""
        self.install_hook("pre-commit")
        (self.repo / "f.txt").write_text("three\n", encoding="utf-8")
        with self.polluted():
            with self.assertRaises(RuntimeError):
                _commit_inheriting_the_ambient_environment(self.repo)


def _commit_inheriting_the_ambient_environment(repo: Path):
    """`emit._git`'s pre-fix body, verbatim in the one respect under test:
    no `env`, so the child inherits. Kept here rather than mocked, because
    a control that shares the fixed code cannot fail (RVW-T21 D2)."""
    out = subprocess.run(["git", "-C", str(repo), "commit", "-a", "-m",
                          "hooked"], capture_output=True, text=True,
                         timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"a `git` subprocess failed: {out.stderr.strip()}")
    return out.stdout.strip()


if __name__ == "__main__":
    unittest.main()


class TestGatesRunConcurrentlyInManifestOrder(_GateHarness):
    """2026-08-31: `run_gates` runs the manifest concurrently (145.4s -> 85.8s
    measured on the real manifest). Concurrency is only admissible here if the
    attestation block is byte-identical whatever order the gates finish in,
    because these documents are compared byte-for-byte across machines.

    So the falsification is a manifest whose completion order is deliberately
    the REVERSE of its declared order: the first gate sleeps longest, the last
    not at all. A results list built from completion order fails; one
    re-sequenced by manifest index passes.
    """

    def _staggered(self, n=4):
        # gate i sleeps (n-1-i) * 0.3s, so completion order is exactly
        # reversed relative to declaration order.
        return [{"id": f"g{i}",
                 "command": [sys.executable, "-c",
                             f"import time;time.sleep({(n - 1 - i) * 0.3})"],
                 "blocking": True}
                for i in range(n)]

    def _run(self, gates, workers):
        cfg = dataclasses.replace(
            CFG, repo_root=self.repo, ledger_dir=self.state, gates=gates)
        base = {k: v for k, v in os.environ.items() if k != IN_GATE}
        with unittest.mock.patch.dict(
                os.environ, {**base, "LOUPE_GATE_WORKERS": str(workers)},
                clear=True):
            return emit.run_gates(cfg, self.head)

    def test_order_is_the_manifests_not_the_completion_order(self):
        gates = self._staggered()
        recs = self._run(gates, workers=4)
        self.assertEqual([r["id"] for r in recs], ["g0", "g1", "g2", "g3"])
        # and the stagger really did invert completion: the first-declared
        # gate is the slowest one.
        self.assertGreater(recs[0]["duration_s"], recs[-1]["duration_s"])

    def test_concurrent_and_sequential_agree_on_everything_but_timing(self):
        gates = self._staggered()
        par = self._run(gates, workers=4)
        seq = self._run(gates, workers=1)

        def stable(recs):
            return [{k: v for k, v in r.items()
                     if k not in ("duration_s", "output")} for r in recs]

        self.assertEqual(stable(par), stable(seq))

    def test_workers_one_is_the_sequential_escape_hatch(self):
        # A concurrency defect in the producer of review evidence must have a
        # way back that needs no code change.
        gates = self._staggered(n=3)
        recs = self._run(gates, workers=1)
        self.assertEqual([r["id"] for r in recs], ["g0", "g1", "g2"])

    def test_an_unparsable_worker_count_falls_back_rather_than_raising(self):
        cfg = dataclasses.replace(
            CFG, repo_root=self.repo, ledger_dir=self.state,
            gates=self._staggered(n=2))
        base = {k: v for k, v in os.environ.items() if k != IN_GATE}
        with unittest.mock.patch.dict(
                os.environ, {**base, "LOUPE_GATE_WORKERS": "not-a-number"},
                clear=True):
            recs = emit.run_gates(cfg, self.head)
        self.assertEqual([r["id"] for r in recs], ["g0", "g1"])


def _errs(items):
    return {i.code for i in items if i.level == "error"}


class TestF4GatesBindToTheExecutedTree(unittest.TestCase):
    """Round-4 F4 — FALSIFICATION: Supplying any head other than the executed
    checkout cannot produce a passing attestation bound to that head."""

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
        env = {k: v for k, v in os.environ.items() if k != IN_GATE}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            return emit.run_gates(self.PROBE, target)

    def test_the_guard_still_fires_when_nested(self):
        with unittest.mock.patch.dict(os.environ, {IN_GATE: "1"}):
            [rec] = emit.run_gates(self.PROBE, "0" * 40)
        self.assertIn("not run", rec["error"])
        self.assertNotIn("exit_code", rec)
        # And a not-run blocking gate is fatal, never a silent pass.
        self.assertIn("A-NOT-RUN", _errs(validate.validate_attestations(
            evidence_with([rec]), self.PROBE)))

    def test_a_foreign_target_cannot_produce_a_bound_attestation(self):
        # The round-4 probe verbatim: run a trivially passing command in this
        # tree and attest it to the null SHA.
        [rec] = self.probe("0" * 40)
        self.assertEqual(rec["exit_code"], 0)
        self.assertTrue(rec["binding"].startswith("unbound"), rec["binding"])
        self.assertNotEqual(rec["executed_sha"], "0" * 40)
        codes = _errs(validate.validate_attestations(
            evidence_with([rec]), self.PROBE))
        self.assertIn("A-UNBOUND", codes)
        self.assertIn("A-SHA-MISMATCH", codes)

    def test_the_recorded_sha_is_derived_from_the_executed_tree(self):
        head = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse",
                               "HEAD"], capture_output=True, text=True,
                              timeout=60).stdout.strip()
        [rec] = self.probe("0" * 40)
        self.assertEqual(rec["executed_sha"], head)

    def test_a_dirty_tree_is_recorded_and_unbound(self):
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

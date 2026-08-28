"""The gate environment is the caller's, not the shim's.

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

These tests run real subprocess gates in a temp git repository of their
own, so they hold in the workbench and in an extracted candidate alike.
"""
import dataclasses
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from review import TOOL_NAME, config, emit, env_var
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
        log = state / "gate-output" / self.head / "probe.log"
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


if __name__ == "__main__":
    unittest.main()

"""A gate CI attests instead of this machine executing (brief
`ci-attested-gates`, asked 2026-09-07).

THE ASK: the heavy half of this manifest runs twice per handoff — once on the
author's laptop, then again in CI on the push the handoff just made, the same
manifest at the same commit. Keep the second, drop the first: wall time may
grow, local CPU must not be spent twice.

WHAT THAT MAKES TESTABLE, and what each class here pins:

  the grammar      `attested_by = "ci"` is admitted and anything else is
                   refused BY NAME, because a value silently treated as "not
                   ci, so run it locally" would turn a typo into a claim
                   nobody made. Same for `[limits] ci_timeout`'s kind.
  the executor     inside CI (`GITHUB_ACTIONS`) the gate EXECUTES: somebody
                   has to produce the attestation, and there it is this
                   process. Locally it polls instead.
  the receipt      a run's CONCLUSION is not a gate's result (round-3 F2).
                   CI publishes one row per declared gate — command, exit
                   code, reason for not running — and the runner reads the
                   gate's own row. An unrelated green workflow, an absent
                   receipt, a row declared `--not-run` and a changed command
                   are each NOT-RUN; only a matching row inside an agreeing
                   run is a pass.
  the SHA match    a completed run on the same branch at a DIFFERENT commit
                   is not evidence about this one. This is the falsifiable
                   heart of the mechanism — MUTATION: drop the `headSha ==
                   target_sha` filter in `emit._ci_decision` and
                   `test_a_run_for_another_commit_is_not_this_commits_
                   evidence` goes red (measured, see the module docstring of
                   the brief).
  the failure map  failure, cancellation and a timeout each produce a
                   DIFFERENT record, and none of them produces a pass. The
                   two states with no CI answer at all use §5.1's not-run
                   shape, which carries no run-only fields — an unattested
                   gate is not evidence, and RVW-T2(c) is what keeps a record
                   from claiming more than it ran.

`gh` is a REAL executable on PATH here — a stub script this module writes,
canned per invocation and logging its argv — rather than an injected
callable. The absent-`gh` state and the `--repo`/`--branch` argv are the two
things an injected runner cannot check, and both are load-bearing: `gh`
resolves the repository from ambient state unless told, and a fork shares
SHAs with its source.

No network is reached and no `gh` on this machine is consulted.
"""
from __future__ import annotations

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

from review import config, emit, env_var, validate
from review.tests.synth import evidence_with
from review.tests.util import REPO_ROOT

CFG = config.load(REPO_ROOT)

SHIM = env_var("SHIM")
STASH_SAFE = env_var("CALLER_PYTHONSAFEPATH")
STASH_PATH = env_var("CALLER_PYTHONPATH")
IN_GATE = env_var("IN_GATE_RUN")

#: The stub's own coordinate, so the fake never has to guess where its canned
#: answers live. Not a tool name: nothing in `review/` reads it.
STUB_DIR_ENV = "LOUPE_TEST_GH_STUB_DIR"

STUB = f'''#!{sys.executable}
"""A canned `gh`, dispatching on the subcommand.

`run list` answers from a QUEUE — one response per poll, the last repeating —
because polling is a sequence and these tests drive it as one. The receipt
calls (`api .../artifacts` and `run download`) answer from a TABLE keyed by
run id, because they are questions about a particular run rather than steps
in that sequence: counting them into the poll queue would make adding a
receipt silently change what the second poll returns.
"""
import json, os, pathlib, sys

d = pathlib.Path(os.environ["{STUB_DIR_ENV}"])
argv = sys.argv[1:]
with (d / "argv.log").open("a", encoding="utf-8") as log:
    log.write(json.dumps(argv) + "\\n")


def finish(r):
    sys.stdout.write(r.get("stdout", ""))
    sys.stderr.write(r.get("stderr", ""))
    raise SystemExit(r.get("exit", 0))


def canned(table, run_id):
    return table[run_id] if run_id in table else table.get("*", {{}})


ci_path = d / "ci.json"
ci = json.loads(ci_path.read_text(encoding="utf-8")) if ci_path.is_file() else {{}}

if argv[:1] == ["api"] and len(argv) > 1 and argv[1].endswith("/artifacts"):
    entry = canned(ci.get("artifacts", {{}}), argv[1].split("/")[-2])
    if {{"stdout", "stderr", "exit"}} & set(entry):
        finish(entry)
    finish({{"stdout": json.dumps(
        {{"artifacts": [{{"name": n}} for n in entry.get("names", [])]}})}})

if argv[:2] == ["run", "download"]:
    entry = canned(ci.get("receipts", {{}}), argv[2])
    if {{"stderr", "exit"}} & set(entry):
        finish(entry)
    dest = pathlib.Path(argv[argv.index("-D") + 1])
    dest.mkdir(parents=True, exist_ok=True)
    for name, doc in entry.get("files", {{}}).items():
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(doc if isinstance(doc, str) else json.dumps(doc),
                        encoding="utf-8")
    finish({{}})

responses = json.loads((d / "responses.json").read_text(encoding="utf-8"))
counter = d / "count"
n = int(counter.read_text(encoding="utf-8")) if counter.is_file() else 0
counter.write_text(str(n + 1), encoding="utf-8")
finish(responses[min(n, len(responses) - 1)])
'''


def run_row(sha: str, *, status: str = "completed",
            conclusion: str | None = "success", run_id: int = 4242,
            workflow: str = "gates", updated: str = "2026-09-07T10:00:00Z"):
    """One row in the shape `gh run list --json` answers with."""
    return {"databaseId": run_id, "headSha": sha, "status": status,
            "conclusion": conclusion, "workflowName": workflow,
            "updatedAt": updated,
            "url": f"https://github.com/acme/widget/actions/runs/{run_id}"}


class _CIGateHarness(unittest.TestCase):
    """A temp git repo with a github.com remote, and a stub `gh` on PATH."""

    #: The probe prints a marker no attestation can echo by accident: the
    #: retained CI blob now carries the gate's COMMAND, and a marker spelled
    #: literally in the source would appear there whether or not anything
    #: ran. Concatenated here, present only in OUTPUT.
    PROBE = "print('the local' + ' command ran')"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        self.state = root / "state"
        self.stub_dir = root / "stub"
        # PATH is REPLACED for every run, never prepended to, and holds
        # exactly two executables: git, and the canned `gh`. With the real
        # PATH behind it a test of the absent-`gh` state would silently fall
        # through to this machine's own `gh` and reach the network — which is
        # measurable: it did, and took 120s per call to say so.
        self.bin = root / "bin"
        self.bin_no_gh = root / "bin-no-gh"
        for d in (self.stub_dir, self.bin, self.bin_no_gh):
            d.mkdir()
        real_git = shutil.which("git")
        self.assertIsNotNone(real_git, "these tests drive real git")
        for d in (self.bin, self.bin_no_gh):
            os.symlink(real_git, d / "git")
        gh = self.bin / "gh"
        gh.write_text(STUB, encoding="utf-8")
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)

        def git(*args):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                           capture_output=True)

        git("init", "-q", "-b", "main")
        git("config", "user.email", "suite@example.invalid")
        git("config", "user.name", "suite")
        # A real remote, because the coordinates are resolved from git and a
        # test that stubbed that resolution would prove nothing about it.
        git("remote", "add", "origin", "https://github.com/acme/widget.git")
        (self.repo / "probe.txt").write_text("probe\n", encoding="utf-8")
        git("add", "probe.txt")
        git("commit", "-q", "-m", "probe")
        self.head = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()
        self._pages: list[list] = []
        self._ci_explicit = False

    def queue(self, *responses: dict) -> None:
        """What the stub answers, in order; the last repeats."""
        (self.stub_dir / "responses.json").write_text(
            json.dumps(list(responses)), encoding="utf-8")

    def queue_runs(self, *pages: list) -> None:
        self._pages = list(pages)
        self.queue(*[{"stdout": json.dumps(page)} for page in pages])

    def argv(self) -> list[list[str]]:
        log = self.stub_dir / "argv.log"
        if not log.is_file():
            return []
        return [json.loads(line) for line in
                log.read_text(encoding="utf-8").splitlines() if line]

    def polls(self) -> list[list[str]]:
        """The `run list` calls only. The receipt fetch adds two more `gh`
        invocations per answered poll, and a test that counted every argv
        line would be counting the mechanism's internals rather than the
        thing it means: how many times CI was ASKED."""
        return [a for a in self.argv() if a[:2] == ["run", "list"]]

    def not_run(self, rec, *fragments: str):
        """The shape and the words. §5.1's not-run record carries no field
        only a run could produce, and a blocking gate in this state stays
        blocking — an unreceipted gate is not evidence."""
        self.assertIn("error", rec)
        for field in ("exit_code", "output", "duration_s"):
            self.assertNotIn(field, rec)
        for fragment in fragments:
            self.assertIn(fragment, rec["error"])
        cfg = dataclasses.replace(
            CFG, ledger_dir=None,
            gates=[{"id": "heavy", "command": ["true"], "blocking": True,
                    "attested_by": "ci"}])
        items = validate.validate_attestations(evidence_with([rec]), cfg)
        codes = {i.code for i in items if i.level == "error"}
        self.assertIn("A-NOT-RUN", codes)
        self.assertNotIn("A-NOT-RUN-SHAPE", codes)

    # ---------------------------------------------------------- the receipt

    def receipt_row(self, gate: dict, *, exit_code: int = 0,
                    not_run: str | None = None, command: str | None = None,
                    duration: float = 0.5) -> dict:
        """One row in the shape `emit.gate_receipt` writes."""
        ran = not_run is None
        return {"id": gate["id"],
                "command": command if command is not None
                else " ".join(gate["command"]),
                "exit_code": exit_code if ran else None,
                "duration_s": duration if ran else None,
                "not_run": not_run}

    def receipt(self, rows: list[dict], *, sha: str | None = None) -> dict:
        return {"schema": emit.CI_RECEIPT_SCHEMA, "sha": sha or self.head,
                "tool_version": "loupe/test", "gates": rows}

    def serve_ci(self, *, artifacts: dict | None = None,
                 receipts: dict | None = None) -> None:
        """What the stub answers about ARTIFACTS, keyed by run id (`"*"` is
        the default). Marks the receipt as explicitly configured, so the
        auto-receipt below stops filling it in."""
        self._ci_explicit = True
        (self.stub_dir / "ci.json").write_text(
            json.dumps({"artifacts": artifacts or {},
                        "receipts": receipts or {}}), encoding="utf-8")

    def serve_receipt(self, doc, *, artifact: str | None = None,
                      run_id: str = "*",
                      filename: str = emit.CI_RECEIPT_FILENAME) -> None:
        """The run uploaded `artifact`, and it contains `doc` — or, with
        `doc=None`, contains nothing readable."""
        name = (artifact if artifact is not None
                else emit.ci_receipt_artifact(self.head))
        files = {} if doc is None else {filename: doc}
        self.serve_ci(artifacts={run_id: {"names": [name]}},
                      receipts={run_id: {"files": files}})

    def _auto_receipt(self, cfg) -> None:
        """The receipt a green (or red) CI run of THIS manifest would have
        left, so a test whose subject is not the receipt need not build one.

        Its exit code mirrors what the queued runs say, because a receipt
        that disagreed with the run it came from is a state of its own and
        would silently become every test's subject.
        """
        page = self._pages[-1] if self._pages else []
        completed = [r for r in page if r.get("headSha") == self.head
                     and r.get("status") == "completed"]
        failed = any(r.get("conclusion") != "success" for r in completed)
        rows = [self.receipt_row(g, exit_code=1 if failed else 0)
                for g in cfg.gates]
        self.serve_receipt(self.receipt(rows))
        self._ci_explicit = False

    def cfg(self, *, attested: bool = True, timeout: int = 5,
            command: list[str] | None = None, blocking: bool = True):
        gate = {"id": "heavy",
                "command": command or [sys.executable, "-c", self.PROBE],
                "blocking": blocking}
        if attested:
            gate["attested_by"] = "ci"
        return dataclasses.replace(
            CFG, repo_root=self.repo, ledger_dir=self.state, gates=[gate],
            limits={**CFG.limits, "ci_timeout": timeout})

    def run_gates(self, cfg=None, *, environ: dict | None = None,
                  interval: float = 0.01, gh_on_path: bool = True,
                  **kwargs):
        """`run_gates` under a controlled environment.

        The ambient base keeps the real environment minus the shim variables
        and the re-entrancy marker: this suite is itself a declared gate, so
        during an emission every test here would otherwise inherit the marker
        and get not-run records back.
        """
        base = {k: v for k, v in os.environ.items()
                if k not in (SHIM, STASH_SAFE, STASH_PATH, IN_GATE,
                             "PYTHONSAFEPATH", "PYTHONPATH", "GITHUB_ACTIONS")}
        base["PATH"] = str(self.bin if gh_on_path else self.bin_no_gh)
        base[STUB_DIR_ENV] = str(self.stub_dir)
        cfg = cfg or self.cfg()
        if not self._ci_explicit:
            self._auto_receipt(cfg)
        with unittest.mock.patch.dict(os.environ, {**base, **(environ or {})},
                                      clear=True):
            return emit.run_gates(cfg, self.head,
                                  ci_poll_interval=interval, **kwargs)


class TestTheGrammar(unittest.TestCase):
    """`attested_by` and `ci_timeout` enter a CLOSED grammar, so each admits
    exactly what it names and refuses the rest by name."""

    def shape(self, doc: str):
        import tomllib
        return config.check_shape(tomllib.loads(doc), "test:review.toml")

    def test_ci_is_admitted(self):
        self.shape('[[gates]]\nid = "t"\ncommand = ["true"]\n'
                   'blocking = true\nattested_by = "ci"\n')

    def test_absent_is_admitted_and_means_local_execution(self):
        self.shape('[[gates]]\nid = "t"\ncommand = ["true"]\nblocking = true\n')

    def test_any_other_attester_is_refused_by_name(self):
        for bad in ('"local"', '"github"', '"CI"', 'true', '3'):
            with self.subTest(value=bad):
                with self.assertRaises(config.ConfigError) as cm:
                    self.shape(f'[[gates]]\nid = "t"\ncommand = ["true"]\n'
                               f'attested_by = {bad}\n')
                self.assertIn("attested_by", str(cm.exception))
                self.assertIn("'ci'", str(cm.exception))

    def test_an_unknown_gate_key_names_the_version_skew(self):
        # The half `[roles]` already had (brief
        # `config-keys-are-a-cross-installation-contract`): an unknown key in
        # a gate row is ALSO what a manifest written for a newer tool looks
        # like from here, and `attested_by` is the first key to prove it. The
        # remedy must not send a person to repair a correct file.
        with self.assertRaises(config.ConfigError) as cm:
            self.shape('[[gates]]\nid = "t"\ncommand = ["true"]\n'
                       'attested_by_ci = true\n')
        self.assertIn("version skew", cm.exception.remedy)

    def test_ci_timeout_takes_a_whole_number(self):
        self.shape('[limits]\nci_timeout = 120\n')
        for bad in ('"120"', '12.5', 'true', '-1'):
            with self.subTest(value=bad):
                with self.assertRaises(config.ConfigError) as cm:
                    self.shape(f'[limits]\nci_timeout = {bad}\n')
                self.assertIn("ci_timeout", str(cm.exception))

    def test_the_built_in_timeout_is_declared_once(self):
        # DEFAULTS is the authority; the runner's constant is the same number
        # and a test rather than a comment says so.
        self.assertEqual(config.DEFAULTS["limits"]["ci_timeout"],
                         emit.CI_TIMEOUT_DEFAULT_S)


class TestInsideCITheGateExecutes(_CIGateHarness):
    """CI is the executor. A gate that polled from inside the run that would
    answer it would wait for itself."""

    def test_github_actions_executes_the_command(self):
        [rec] = self.run_gates(environ={"GITHUB_ACTIONS": "true"})
        self.assertEqual(rec["exit_code"], 0)
        self.assertNotIn("attested_by", rec)
        self.assertIn("the local command ran",
                      Path(rec["output"]["pointer"]).read_text("utf-8"))
        self.assertEqual(self.argv(), [], "no poll may be made inside CI")

    def test_execute_ci_gates_forces_local_execution(self):
        [rec] = self.run_gates(execute_ci_gates=True)
        self.assertEqual(rec["exit_code"], 0)
        self.assertEqual(self.argv(), [])

    def test_an_unattested_gate_is_untouched(self):
        self.queue_runs([])
        [rec] = self.run_gates(self.cfg(attested=False))
        self.assertEqual(rec["exit_code"], 0)
        self.assertNotIn("attested_by", rec)
        self.assertEqual(self.argv(), [])


class TestACompletedRunAtTheTargetSHA(_CIGateHarness):
    """The state the mechanism exists for: CI finished this commit."""

    def setUp(self):
        super().setUp()
        self.queue_runs([run_row(self.head)])
        [self.rec] = self.run_gates()

    def test_the_gate_passed_without_running_locally(self):
        self.assertEqual(self.rec["exit_code"], 0)
        self.assertEqual(self.rec["attested_by"], "ci")
        self.assertNotIn("the local command ran",
                         Path(self.rec["output"]["pointer"]).read_text("utf-8"))

    def test_the_record_carries_the_run_identity(self):
        ci = self.rec["ci_run"]
        self.assertEqual(ci["id"], 4242)
        self.assertEqual(ci["conclusion"], "success")
        self.assertEqual(ci["head_sha"], self.head)
        self.assertEqual(ci["workflow"], "gates")
        self.assertIn("actions/runs/4242", ci["url"])
        self.assertEqual(ci["completed"], "2026-09-07T10:00:00Z")

    def test_the_retained_output_is_the_run_not_a_command_transcript(self):
        blob = json.loads(Path(self.rec["output"]["pointer"]).read_text("utf-8"))
        self.assertEqual(blob["attested_by"], "ci")
        self.assertEqual(blob["target_sha"], self.head)
        self.assertEqual(blob["repository"], "acme/widget")
        self.assertEqual(blob["branch"], "main")
        self.assertEqual(blob["deciding_run"]["url"], self.rec["ci_run"]["url"])

    def test_the_attestation_binds_to_the_target(self):
        # CI checked out exactly this commit, which is a STRONGER binding
        # than a local gate can offer from a tree that may be dirty.
        self.assertEqual(self.rec["executed_sha"], self.head)
        self.assertEqual(self.rec["target_sha"], self.head)
        self.assertEqual(self.rec["binding"], "bound")
        self.assertEqual(self.rec["tree"], "clean")

    def test_the_tool_version_names_the_run_not_a_local_digest(self):
        # A local sha256 for a command that ran on a runner would be the
        # exact lie this mechanism exists to avoid.
        self.assertIn("attested by ci", self.rec["tool_version"])
        self.assertIn("4242", self.rec["tool_version"])
        self.assertNotIn("sha256:", self.rec["tool_version"])

    def test_the_poll_names_the_repository_and_the_remote_branch(self):
        [argv] = self.polls()
        self.assertEqual(argv[:3], ["run", "list", "--repo"])
        self.assertIn("acme/widget", argv)
        self.assertIn("--branch", argv)
        self.assertEqual(argv[argv.index("--branch") + 1], "main")

    def test_the_poll_never_uses_the_commit_filter(self):
        # `gh run list --commit` returns an empty list silently for a
        # commit that plainly has runs (measured, gh 2.x); a poller using it
        # cannot tell "no run" from "not started" and times out every time.
        self.assertNotIn("--commit", self.polls()[0])

    def test_the_record_validates_as_a_clean_attestation(self):
        cfg = dataclasses.replace(
            CFG, ledger_dir=None,
            gates=[{"id": "heavy", "command": ["true"], "blocking": True,
                    "attested_by": "ci"}])
        items = validate.validate_attestations(
            evidence_with([self.rec]), cfg, request_sha=self.head)
        self.assertEqual({i.code for i in items if i.level == "error"}, set())


class TestTheSHAMatchIsTheAttestation(_CIGateHarness):
    """MUTATION TARGET. Remove the `headSha == target_sha` filter from
    `emit._ci_decision` and this class goes red — everything else stays
    green, which is what makes it the falsification."""

    OTHER = "1234567890" * 4

    def test_a_run_for_another_commit_is_not_this_commits_evidence(self):
        self.queue_runs([run_row(self.OTHER)])
        [rec] = self.run_gates(self.cfg(timeout=0))
        self.assertNotIn("exit_code", rec)
        self.assertIn("no completed run for", rec["error"])
        self.assertIn(self.head, rec["error"])

    def test_a_green_run_at_another_commit_beside_a_pending_one_here(self):
        # The staleness `ci-evidence` reports, met inside the poller: a green
        # run one commit behind must not answer for this one.
        self.queue_runs([run_row(self.OTHER),
                         run_row(self.head, status="in_progress",
                                 conclusion=None, run_id=99)])
        [rec] = self.run_gates(self.cfg(timeout=0))
        self.assertIn("error", rec)
        self.assertIn("1 run(s) at that SHA still in flight", rec["error"])


class TestTheFailureStates(_CIGateHarness):

    def test_a_failed_run_fails_the_gate_with_its_url(self):
        self.queue_runs([run_row(self.head, conclusion="failure")])
        [rec] = self.run_gates()
        self.assertEqual(rec["exit_code"], 1)
        self.assertEqual(rec["ci_run"]["conclusion"], "failure")
        blob = Path(rec["output"]["pointer"]).read_text("utf-8")
        self.assertIn("actions/runs/4242", blob)
        self.assertIn("actions/runs/4242", rec["ci_run"]["url"])

    def test_a_cancelled_run_is_a_failure_naming_the_conclusion(self):
        self.queue_runs([run_row(self.head, conclusion="cancelled")])
        [rec] = self.run_gates()
        self.assertEqual(rec["exit_code"], 1)
        self.assertEqual(rec["ci_run"]["conclusion"], "cancelled")

    def test_a_skipped_run_is_a_failure_too(self):
        self.queue_runs([run_row(self.head, conclusion="skipped")])
        [rec] = self.run_gates()
        self.assertEqual(rec["exit_code"], 1)
        self.assertEqual(rec["ci_run"]["conclusion"], "skipped")

    def test_a_failure_decides_immediately_beside_a_pending_sibling(self):
        # A doomed handoff must not wait out the timeout for a second
        # workflow it already knows cannot rescue the first.
        self.queue_runs([run_row(self.head, conclusion="failure"),
                         run_row(self.head, status="queued", conclusion=None,
                                 run_id=77)])
        [rec] = self.run_gates(self.cfg(timeout=5))
        self.assertEqual(rec["exit_code"], 1)
        self.assertEqual(len(self.polls()), 1)

    def test_a_blocking_ci_failure_is_fatal_to_the_request(self):
        self.queue_runs([run_row(self.head, conclusion="failure")])
        [rec] = self.run_gates()
        cfg = dataclasses.replace(
            CFG, ledger_dir=None,
            gates=[{"id": "heavy", "command": ["true"], "blocking": True,
                    "attested_by": "ci"}])
        items = validate.validate_attestations(evidence_with([rec]), cfg)
        self.assertIn("A-FAILED", {i.code for i in items if i.level == "error"})


class TestTheStatesWithNoAnswerAtAll(_CIGateHarness):
    """No CI answer is NOT a pass, and it is not a run either: §5.1's not-run
    shape, which RVW-T2(c) forbids from carrying run-only fields."""

    def not_run_shape(self, rec):
        self.assertIn("error", rec)
        for field in ("exit_code", "output", "duration_s"):
            self.assertNotIn(field, rec,
                             f"a record with no CI answer carries {field!r}, "
                             f"which claims more than it ran")
        cfg = dataclasses.replace(
            CFG, ledger_dir=None,
            gates=[{"id": "heavy", "command": ["true"], "blocking": True,
                    "attested_by": "ci"}])
        items = validate.validate_attestations(evidence_with([rec]), cfg)
        codes = {i.code for i in items if i.level == "error"}
        self.assertIn("A-NOT-RUN", codes)
        self.assertNotIn("A-NOT-RUN-SHAPE", codes)

    def test_a_timeout_names_the_sha_and_the_ceiling(self):
        self.queue_runs([run_row(self.head, status="in_progress",
                                 conclusion=None)])
        [rec] = self.run_gates(self.cfg(timeout=0))
        self.assertIn(f"no completed run for {self.head} within 0s",
                      rec["error"])
        self.not_run_shape(rec)

    def test_gh_absent_says_so(self):
        self.queue_runs([run_row(self.head)])
        [rec] = self.run_gates(gh_on_path=False)
        self.assertIn("gh", rec["error"])
        self.assertIn("did not run", rec["error"])
        self.not_run_shape(rec)

    def test_an_unauthenticated_gh_refuses_rather_than_reporting_no_runs(self):
        self.queue({"exit": 4, "stderr": "gh: authentication required\n"})
        [rec] = self.run_gates()
        self.assertIn("authentication required", rec["error"])
        self.not_run_shape(rec)

    def test_a_non_json_answer_refuses(self):
        self.queue({"stdout": "not json at all"})
        [rec] = self.run_gates()
        self.assertIn("did not answer JSON", rec["error"])
        self.not_run_shape(rec)

    def test_an_unresolvable_repository_says_which_coordinate_failed(self):
        subprocess.run(["git", "-C", str(self.repo), "remote", "set-url",
                        "origin", "https://github.com/lonely"], check=True,
                       capture_output=True)
        self.queue_runs([run_row(self.head)])
        [rec] = self.run_gates()
        self.assertIn("not resolvable", rec["error"])
        self.not_run_shape(rec)
        self.assertEqual(self.argv(), [],
                         "no repository, no poll — a guess would attest the "
                         "wrong one")

    def test_a_non_blocking_ci_gate_that_never_answered_is_a_notice(self):
        self.queue_runs([])
        [rec] = self.run_gates(self.cfg(timeout=0, blocking=False))
        cfg = dataclasses.replace(
            CFG, ledger_dir=None,
            gates=[{"id": "heavy", "command": ["true"], "blocking": False,
                    "attested_by": "ci"}])
        items = validate.validate_attestations(evidence_with([rec]), cfg)
        self.assertEqual({i.code for i in items if i.level == "error"}, set())


class TestPolling(_CIGateHarness):

    def test_pending_then_completed_across_two_polls(self):
        self.queue_runs(
            [run_row(self.head, status="in_progress", conclusion=None)],
            [run_row(self.head)])
        [rec] = self.run_gates(self.cfg(timeout=5), interval=0.01)
        self.assertEqual(rec["exit_code"], 0)
        self.assertEqual(len(self.polls()), 2)
        blob = json.loads(Path(rec["output"]["pointer"]).read_text("utf-8"))
        self.assertEqual(blob["polls"], 2)

    def test_one_poll_answers_every_ci_gate(self):
        # The gates share a question. Asking it once per gate would be five
        # API calls for one answer, and five chances to read a different one.
        gates = [{"id": f"heavy{i}", "command": [sys.executable, "-c", "pass"],
                  "blocking": True, "attested_by": "ci"} for i in range(4)]
        cfg = dataclasses.replace(
            CFG, repo_root=self.repo, ledger_dir=self.state, gates=gates,
            limits={**CFG.limits, "ci_timeout": 5})
        self.queue_runs([run_row(self.head)])
        records = self.run_gates(cfg)
        self.assertEqual([r["exit_code"] for r in records], [0, 0, 0, 0])
        self.assertEqual(len(self.polls()), 1)

    def test_the_manifest_order_survives_a_mixed_manifest(self):
        # Order is the manifest's, never completion order: these documents
        # are compared byte for byte.
        gates = [
            {"id": "local-a", "command": [sys.executable, "-c", "pass"],
             "blocking": True},
            {"id": "attested", "command": [sys.executable, "-c", "pass"],
             "blocking": True, "attested_by": "ci"},
            {"id": "local-b", "command": [sys.executable, "-c", "pass"],
             "blocking": True},
        ]
        cfg = dataclasses.replace(
            CFG, repo_root=self.repo, ledger_dir=self.state, gates=gates,
            limits={**CFG.limits, "ci_timeout": 5})
        self.queue_runs([run_row(self.head)])
        for workers in ("1", "4"):
            with self.subTest(workers=workers):
                (self.stub_dir / "count").unlink(missing_ok=True)
                records = self.run_gates(
                    cfg, environ={env_var("GATE_WORKERS"): workers})
                self.assertEqual([r["id"] for r in records],
                                 ["local-a", "attested", "local-b"])
                self.assertEqual(records[1]["attested_by"], "ci")


class TestTheReceiptIsTheEvidence(_CIGateHarness):
    """Round-3 F2. A run's CONCLUSION is not a gate's result.

    The first cut of this mechanism assigned a successful run at the target
    SHA to every declared CI gate. Reproduced as a defect rather than argued
    as one: a stub `gh` returning a single green workflow named `docs-only`
    produced `exit_code 0`, `binding: bound` for a gate whose command exits
    1. A successful workflow is not evidence that THIS gate executed — it may
    be absent, filtered, still queued, or have declared the gate `--not-run`
    inside the workflow itself.

    So CI publishes a RECEIPT: one row per declared gate, with its command,
    its exit code and the reason it did not run. The runner downloads
    `loupe-gates-<sha>` and reads the gate's own row, and every state below
    is decided by that row rather than by the run around it.

    MUTATION: decide on run status and `headSha` alone again — replace the
    body of `emit._ci_records` with the pre-receipt
    `{g["id"]: _ci_attestation(...)}` over the run's state. The unrelated-only
    and declared-skip controls here go red, because they are exactly the two
    states a conclusion cannot distinguish from a pass.
    """

    OTHER = "1234567890" * 4
    FAILS = "raise SystemExit(1)"

    # ------------------------------------------- no receipt about this commit

    def test_an_unrelated_green_workflow_attests_nothing(self):
        # THE F2 REPRODUCTION. A workflow that completed successfully at this
        # exact commit, and never ran this gate. The gate's command exits 1,
        # so a pass here could only have come from the run's conclusion.
        self.queue_runs([run_row(self.head, workflow="docs-only")])
        self.serve_ci(artifacts={"*": {"names": ["docs-site"]}})
        [rec] = self.run_gates(self.cfg(command=[sys.executable, "-c",
                                                 self.FAILS]))
        self.not_run(rec, "no gate receipt", self.head,
                     f"uploaded no {emit.ci_receipt_artifact(self.head)}",
                     "docs-site")

    def test_the_expected_workflow_still_pending_is_not_an_answer(self):
        # The gate workflow is running; an unrelated one has already gone
        # green. The unrelated success must not stand in for the pending one.
        self.queue_runs([run_row(self.head, workflow="docs-only"),
                         run_row(self.head, workflow="gates", run_id=99,
                                 status="in_progress", conclusion=None)])
        self.serve_receipt(self.receipt([self.receipt_row(self.cfg().gates[0])]))
        [rec] = self.run_gates(self.cfg(timeout=0))
        self.not_run(rec, "no completed run for", self.head,
                     "1 run(s) at that SHA still in flight")

    def test_a_run_that_uploaded_no_readable_receipt(self):
        # The artifact is named and downloads, and carries nothing this tool
        # can read. Absent evidence, not implied evidence.
        self.queue_runs([run_row(self.head)])
        self.serve_receipt(None)
        [rec] = self.run_gates()
        self.not_run(rec, "no gate receipt", "carries no readable gate receipt")

    def test_a_receipt_about_another_commit_is_not_this_commits(self):
        # The artifact name carries the SHA, but the file says what it is
        # about and the file decides — a stale listing must not answer for
        # the commit under review.
        self.queue_runs([run_row(self.head)])
        self.serve_receipt(
            self.receipt([self.receipt_row(self.cfg().gates[0])],
                         sha=self.OTHER))
        [rec] = self.run_gates()
        self.not_run(rec, "no gate receipt", "its receipt is about",
                     self.OTHER)

    # --------------------------------------- a receipt that answers about it

    def test_a_receipt_with_no_row_for_this_gate(self):
        # CI ran the workflow. It did not run THIS gate, and says so by
        # omission — which the reader turns into not-run rather than silence.
        self.queue_runs([run_row(self.head)])
        self.serve_receipt(self.receipt(
            [self.receipt_row({"id": "something-else", "command": ["true"]})]))
        [rec] = self.run_gates()
        self.not_run(rec, "CI ran the workflow but not this gate",
                     "something-else", "'heavy'")

    def test_a_row_declared_not_run_is_not_a_pass(self):
        # THE EDIT THE OLD COMMENT COULD ONLY WARN ABOUT. Declaring a
        # CI-attested gate `--not-run` in the workflow left CI green and
        # every handoff attesting a gate nothing ran. It now lands in the
        # receipt AS not-run, and the record says so.
        cfg = self.cfg()
        reason = "declared --not-run in .github/workflows/gates.yml"
        self.queue_runs([run_row(self.head)])
        self.serve_receipt(emit.gate_receipt(self.head, cfg.gates, {},
                                             {"heavy": reason}))
        [rec] = self.run_gates(cfg)
        self.not_run(rec, "CI declared this gate not run", reason)

    def test_a_changed_command_is_a_different_gate(self):
        self.queue_runs([run_row(self.head)])
        self.serve_receipt(self.receipt(
            [self.receipt_row(self.cfg().gates[0], command="pytest -x")]))
        [rec] = self.run_gates()
        self.not_run(rec, "attests the command", "pytest -x",
                     "a changed command is a different gate")

    def test_a_non_zero_row_fails_the_gate_at_that_exit_with_the_url(self):
        self.queue_runs([run_row(self.head, conclusion="failure")])
        self.serve_receipt(self.receipt(
            [self.receipt_row(self.cfg().gates[0], exit_code=2)]))
        [rec] = self.run_gates()
        self.assertEqual(rec["exit_code"], 2)
        self.assertEqual(rec["receipt"]["exit_code"], 2)
        self.assertIn("actions/runs/4242", rec["ci_run"]["url"])
        cfg = dataclasses.replace(
            CFG, ledger_dir=None,
            gates=[{"id": "heavy", "command": ["true"], "blocking": True,
                    "attested_by": "ci"}])
        self.assertIn("A-FAILED", {i.code for i in validate.validate_attestations(
            evidence_with([rec]), cfg) if i.level == "error"})

    def test_a_receipt_that_disagrees_with_its_own_run_is_refused(self):
        # Green gate inside a run that concluded otherwise, and the mirror
        # image. Either way the two halves of the evidence contradict each
        # other, and a contradiction is not a weaker pass.
        for conclusion, exit_code in (("failure", 0), ("success", 3)):
            with self.subTest(conclusion=conclusion, exit_code=exit_code):
                (self.stub_dir / "argv.log").unlink(missing_ok=True)
                (self.stub_dir / "count").unlink(missing_ok=True)
                self.queue_runs([run_row(self.head, conclusion=conclusion)])
                self.serve_receipt(self.receipt(
                    [self.receipt_row(self.cfg().gates[0],
                                      exit_code=exit_code)]))
                [rec] = self.run_gates()
                self.not_run(rec, "the receipt and the run disagree")

    def test_complete_positive_evidence_is_a_pass_carrying_its_receipt(self):
        cfg = self.cfg()
        self.queue_runs([run_row(self.head)])
        self.serve_receipt(self.receipt(
            [self.receipt_row(cfg.gates[0], exit_code=0, duration=61.5)]))
        [rec] = self.run_gates(cfg)
        self.assertEqual(rec["exit_code"], 0)
        self.assertEqual(rec["command"], " ".join(cfg.gates[0]["command"]))
        # The row is RETAINED, not consumed: a reader can see which gate CI
        # ran, under which command, to which exit, in which artifact.
        self.assertEqual(rec["receipt"]["id"], "heavy")
        self.assertEqual(rec["receipt"]["command"], rec["command"])
        self.assertEqual(rec["receipt"]["exit_code"], 0)
        self.assertEqual(rec["receipt"]["duration_s"], 61.5)
        self.assertIsNone(rec["receipt"]["not_run"])
        self.assertEqual(rec["receipt"]["artifact"],
                         emit.ci_receipt_artifact(self.head))
        self.assertEqual(rec["receipt"]["sha"], self.head)
        self.assertIn(emit.ci_receipt_artifact(self.head), rec["tool_version"])
        items = validate.validate_attestations(
            evidence_with([rec]), dataclasses.replace(
                CFG, ledger_dir=None,
                gates=[{"id": "heavy", "command": cfg.gates[0]["command"],
                        "blocking": True, "attested_by": "ci"}]),
            request_sha=self.head)
        self.assertEqual({i.code for i in items if i.level == "error"}, set())

    def test_the_artifact_is_asked_for_by_name_at_the_target_commit(self):
        self.queue_runs([run_row(self.head)])
        self.serve_receipt(self.receipt([self.receipt_row(self.cfg().gates[0])]))
        self.run_gates()
        [api] = [a for a in self.argv() if a[:1] == ["api"]]
        self.assertEqual(api[1], "repos/acme/widget/actions/runs/4242/artifacts")
        [download] = [a for a in self.argv() if a[:2] == ["run", "download"]]
        self.assertEqual(download[2], "4242")
        self.assertIn("--repo", download)
        self.assertEqual(download[download.index("--repo") + 1], "acme/widget")
        self.assertEqual(download[download.index("-n") + 1],
                         f"loupe-gates-{self.head}")


class TestTheRunLevelStateSurvivesReceiptSelection(_CIGateHarness):
    """FALSIFICATION for round-3 F1.

    `_ci_decision` is fail-closed at the RUN level: any completed run at the
    reviewed SHA concluding other than success makes the commit `failed`,
    and its docstring says so as the honest limit of asking a run-level API
    about a gate-level fact. Receipt selection then threw that away — it
    walked every completed run newest first for the first READABLE receipt,
    so a newer failed run that uploaded nothing fell through to an older
    green run's receipt and bound a pass at a red commit.

    The state travels into selection now. Mutation: drop the `state ==
    "failed"` narrowing and let selection see the whole admitted set again;
    the first test here goes green on a bound pass, which is the state it
    was found in.
    """

    def _older_green_newer_failed(self, newer_conclusion="failure"):
        """Two completed runs at this commit: 10:00 green, 11:00 red."""
        return [run_row(self.head, run_id=1, workflow="gates",
                        updated="2026-09-07T10:00:00Z"),
                run_row(self.head, run_id=2, workflow="gates",
                        conclusion=newer_conclusion,
                        updated="2026-09-07T11:00:00Z")]

    def test_a_newer_failed_run_is_not_bypassed_by_an_older_green_receipt(self):
        # THE F1 REPRODUCTION. The older run went green and left a complete
        # passing receipt; the newer run failed and uploaded nothing. The
        # older receipt is about the same commit and reads perfectly — and
        # it is still not evidence that this commit's gates pass, because a
        # later run at the same commit says they do not.
        cfg = self.cfg()
        artifact = emit.ci_receipt_artifact(self.head)
        self.queue_runs(self._older_green_newer_failed())
        self.serve_ci(
            artifacts={"1": {"names": [artifact]}, "2": {"names": []}},
            receipts={"1": {"files": {emit.CI_RECEIPT_FILENAME: self.receipt(
                [self.receipt_row(cfg.gates[0], exit_code=0)])}}})
        [rec] = self.run_gates(cfg)
        self.not_run(rec, "no gate receipt", self.head,
                     "concluded other than success",
                     "only their own receipt can answer")
        # Not merely absent from the record: the older run's receipt must
        # not have supplied any of it.
        self.assertNotIn("receipt", rec)

    def test_the_failed_runs_own_receipt_fails_the_gate_at_its_exit(self):
        # The failed run DID leave a receipt. That one answers: it is this
        # commit's own per-gate evidence, and it records the failure.
        cfg = self.cfg()
        artifact = emit.ci_receipt_artifact(self.head)
        self.queue_runs(self._older_green_newer_failed())
        self.serve_ci(
            artifacts={"1": {"names": [artifact]}, "2": {"names": [artifact]}},
            receipts={
                "1": {"files": {emit.CI_RECEIPT_FILENAME: self.receipt(
                    [self.receipt_row(cfg.gates[0], exit_code=0)])}},
                "2": {"files": {emit.CI_RECEIPT_FILENAME: self.receipt(
                    [self.receipt_row(cfg.gates[0], exit_code=1)])}}})
        [rec] = self.run_gates(cfg)
        self.assertEqual(rec["exit_code"], 1)
        self.assertEqual(rec["receipt"]["exit_code"], 1)
        # The run it names is the failed one, not the green one it could
        # have reached.
        self.assertIn("run 2 (failure)", rec["tool_version"])

    def test_a_green_row_inside_a_failed_run_is_a_disagreement(self):
        # The failed run's receipt claims this gate exited 0. The run says
        # the commit failed. A contradiction is not evidence, and it is
        # certainly not a pass — the biconditional already ruled this, and
        # it must still be reached now that selection is scoped.
        cfg = self.cfg()
        artifact = emit.ci_receipt_artifact(self.head)
        self.queue_runs(self._older_green_newer_failed())
        self.serve_ci(
            artifacts={"2": {"names": [artifact]}},
            receipts={"2": {"files": {emit.CI_RECEIPT_FILENAME: self.receipt(
                [self.receipt_row(cfg.gates[0], exit_code=0)])}}})
        [rec] = self.run_gates(cfg)
        self.not_run(rec, "records exit 0", "the receipt and the run disagree")

    def test_a_cancelled_newer_run_is_a_failure_for_this_purpose(self):
        # `_ci_decision` admits anything that is not `success` as failed.
        # Selection scopes by the same test, so a cancelled run cannot be
        # stepped over either.
        cfg = self.cfg()
        artifact = emit.ci_receipt_artifact(self.head)
        self.queue_runs(self._older_green_newer_failed("cancelled"))
        self.serve_ci(
            artifacts={"1": {"names": [artifact]}, "2": {"names": []}},
            receipts={"1": {"files": {emit.CI_RECEIPT_FILENAME: self.receipt(
                [self.receipt_row(cfg.gates[0], exit_code=0)])}}})
        [rec] = self.run_gates(cfg)
        self.not_run(rec, "no gate receipt", "concluded other than success")

    def test_several_green_runs_still_answer_from_the_one_with_a_receipt(self):
        # THE CONTROL the narrowing must not break. Two runs, both green,
        # so the state is `success` and every completed run is admissible —
        # the newest uploaded nothing, and the walk finds the other's
        # receipt exactly as it did before.
        cfg = self.cfg()
        artifact = emit.ci_receipt_artifact(self.head)
        self.queue_runs([
            run_row(self.head, run_id=1, updated="2026-09-07T10:00:00Z"),
            run_row(self.head, run_id=2, workflow="docs-only",
                    updated="2026-09-07T11:00:00Z")])
        self.serve_ci(
            artifacts={"1": {"names": [artifact]}, "2": {"names": []}},
            receipts={"1": {"files": {emit.CI_RECEIPT_FILENAME: self.receipt(
                [self.receipt_row(cfg.gates[0], exit_code=0)])}}})
        [rec] = self.run_gates(cfg)
        self.assertEqual(rec["exit_code"], 0)
        self.assertEqual(rec["receipt"]["id"], "heavy")


class TestTheReceiptCoversTheDeclaredManifest(unittest.TestCase):
    """The manifest/receipt relationship, mechanically. `gate_receipt` is
    what CI writes; a gate missing from it is a gate the reader must refuse,
    so omission may not be the way a skip is spelled."""

    GATES = [{"id": "a", "command": ["true"]},
             {"id": "b", "command": ["false"]},
             {"id": "c", "command": ["echo", "hi"]}]
    SHA = "a" * 40

    def test_every_declared_gate_gets_a_row_in_manifest_order(self):
        doc = emit.gate_receipt(
            self.SHA, self.GATES,
            {"a": {"id": "a", "command": "true", "exit_code": 0,
                   "duration_s": 1.5}},
            {"b": "declared away on the runner"})
        self.assertEqual([r["id"] for r in doc["gates"]], ["a", "b", "c"])
        self.assertEqual(doc["sha"], self.SHA)
        self.assertEqual(doc["schema"], emit.CI_RECEIPT_SCHEMA)

    def test_a_declared_away_gate_carries_its_reason_and_no_exit_code(self):
        doc = emit.gate_receipt(self.SHA, self.GATES, {},
                                {"b": "declared away on the runner"})
        row = {r["id"]: r for r in doc["gates"]}["b"]
        self.assertEqual(row["not_run"], "declared away on the runner")
        self.assertIsNone(row["exit_code"])
        self.assertEqual(row["command"], "false")

    def test_a_gate_that_produced_no_attestation_is_not_run_not_absent(self):
        doc = emit.gate_receipt(self.SHA, self.GATES, {}, {})
        rows = {r["id"]: r for r in doc["gates"]}
        self.assertEqual(sorted(rows), ["a", "b", "c"])
        self.assertIn("no attestation", rows["a"]["not_run"])
        self.assertIsNone(rows["a"]["exit_code"])

    def test_a_gate_that_could_not_execute_carries_its_error(self):
        doc = emit.gate_receipt(
            self.SHA, self.GATES,
            {"c": {"id": "c", "error": "not run: the interpreter is missing"}},
            {})
        row = {r["id"]: r for r in doc["gates"]}["c"]
        self.assertIn("interpreter is missing", row["not_run"])
        self.assertIsNone(row["exit_code"])

    def test_the_command_recorded_is_the_one_that_ran(self):
        # A record's own command wins over the manifest's rendering of it:
        # the receipt attests what executed, and the reader compares that
        # against the manifest rather than trusting them to agree.
        doc = emit.gate_receipt(
            self.SHA, self.GATES,
            {"a": {"id": "a", "command": "true --now", "exit_code": 0}}, {})
        row = {r["id"]: r for r in doc["gates"]}["a"]
        self.assertEqual(row["command"], "true --now")
        self.assertEqual(row["exit_code"], 0)


if __name__ == "__main__":
    unittest.main()

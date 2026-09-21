"""Gate before push (loupe 0.25.0, public issue #2).

Ruled by the orchestrator of 0.25.0: gate before push, and NO undo. Until
0.25.0 `handoff` and `emit-request` committed the outstanding work, PUSHED
it, and only then ran the gates, so a red blocking gate refused an
emission whose commit was already on the remote. The order is now:

  1. preflight and commit, exactly as before (sweep, read-back, authority,
     roles — all before anything leaves the machine);
  2. the LOCAL gates at that commit — every manifest gate not attested by
     CI, or all of them inside CI — with `binding` computed as always;
  3. a blocking local gate that the REQUEST VALIDATOR would refuse stops
     the hand-off here: nothing pushed, emitted or recorded, the local
     commit named (or "no commit was made"), every failed gate named. The
     tool does not undo its commit — `SweepRefused` and `AuthorityAbsent`
     already leave one for the author to amend;
  4. otherwise the push and the observation of the remote ref, THEN the
     CI-attested gates (they need the pushed SHA), merged in manifest
     order, and the emission — the gates run once per hand-off, never
     twice;
  5. `--local-only`: no push; the gates, then the emission.

The pre-push refusal and the post-emission validation must never disagree,
so the predicate is the validator's own (`validate.validate_attestations`,
through `emit.local_gate_failures`), never a second one — `not run: nested`
records included.

Everything runs through `python3 -m review` as a separate process, in a
scratch repository with a bare remote and its own state directory. Each
gate is a real command that APPENDS one line per execution to a counter
outside the repository — the gate id, and the tip the remote carried when
it ran — so "ran once" and "ran before the push" are counted, not assumed.
A gate's exit code is read from a control file, so red and green are
flipped without a commit.

The one-base section holds the review BASE to one id (0.25.0 review
round 1 F1): resolved once, before the hand-off's own commit, and that id
is what every LOCAL gate is told and what the request names, measures and
validates. The last section holds the guarantee to that scope (round 2
F2): a CI-attested gate is told no base, so its receipt binds the target
commit but does not prove the review base, a range-sensitive gate must run
locally to receive the guarantee, and every published statement of the
guarantee names that limit.

MUTATIONS, each applied alone with bytecode off and measured red, recorded
in the track reports of 2026-09-21 (loupe 0.25.0, track T1; round 2, track
R2a for the base; round 3, track R3b for its scope).
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from review import adapters, cli, config, emit, env_var, validate, wire
from review.tests.test_ci_attested_gates import STUB, STUB_DIR_ENV, run_row
from review.tests.test_git_timeout import (Scratch, cli_env, git,
                                           sleeping_pre_push)
from review.tests.util import REPO_ROOT, public_path

#: A gate: one line to the counter per execution — its id, the remote's tip
#: at that moment, and the review range the runner told it (base and head)
#: — then the exit code its control file holds (0 when there is none).
GATE_SCRIPT = """\
import json, os, pathlib, subprocess, sys
ctl = pathlib.Path({ctl!r})
tip = subprocess.run(["git", "ls-remote", {remote!r}, "refs/heads/main"],
                     capture_output=True, text=True).stdout.split("\\t")[0]
with (ctl / "runs.jsonl").open("a", encoding="utf-8") as log:
    log.write(json.dumps({{"gate": {gid!r}, "remote_tip": tip,
                          "base": os.environ.get({base_name!r}),
                          "head": os.environ.get({head_name!r})}}) + "\\n")
code = ctl / {gid!r}
sys.exit(int(code.read_text()) if code.exists() else 0)
"""


def gate_block(gid: str, ctl: Path, remote: Path, *, blocking: bool,
               ci: bool = False) -> str:
    command = [sys.executable, "-c",
               GATE_SCRIPT.format(ctl=str(ctl), remote=str(remote), gid=gid,
                                  base_name=env_var("GATE_BASE"),
                                  head_name=env_var("GATE_HEAD"))]
    return (f"\n[[gates]]\nid = {json.dumps(gid)}\n"
            f"command = {json.dumps(command)}\n"
            f"blocking = {'true' if blocking else 'false'}\n"
            + ('attested_by = "ci"\n' if ci else ""))


#: The range-sensitive gate round 1 F1 of 0.25.0's review names, verbatim:
#: it reads nothing but the two names the runner exports, so what it
#: attests is exactly the range it was told.
RANGE_GATE = ["sh", "-c", f'git diff --check "${env_var("GATE_BASE")}" '
                          f'"${env_var("GATE_HEAD")}"']


class _Gated(unittest.TestCase):
    """A scratch repository whose committed manifest is `self.GATES`."""

    #: (id, blocking, attested by CI)
    GATES = (("block", True, False), ("advise", False, False))
    PREFIX = "gate-before-push-"
    REMOTE_DIR = "remote.git"
    URL_FORM = "path"
    LIMITS = None
    #: Manifest text appended verbatim after `GATES`' recorders.
    EXTRA_GATES = ""

    def setUp(self):
        self.fresh()

    def fresh(self) -> Scratch:
        """A new scratch, made `self.scratch` — for a test whose rows each
        need their own repository, remote and ledger."""
        # The remote's path is needed inside the gates, and the gates are
        # committed with the configuration — so the scratch is built once
        # to learn its root, then the manifest is written against it.
        probe = Scratch(self, self.PREFIX, remote_dir=self.REMOTE_DIR,
                        url_form=self.URL_FORM)
        self.scratch = probe
        self.ctl = probe.root / "ctl"
        self.ctl.mkdir()
        gates = "".join(gate_block(gid, self.ctl, probe.remote,
                                   blocking=blocking, ci=ci)
                        for gid, blocking, ci in self.GATES)
        probe.write_config(git_timeout=self.LIMITS,
                           gates=gates + self.EXTRA_GATES)
        git(probe.repo, "commit", "-qam", "the manifest")
        probe.head = git(probe.repo, "rev-parse", "HEAD")
        return probe

    # ------------------------------------------------------------ control

    def red(self, gid: str, code: int = 1) -> None:
        (self.ctl / gid).write_text(str(code), encoding="utf-8")

    def green(self, gid: str) -> None:
        (self.ctl / gid).unlink(missing_ok=True)

    def runs(self) -> list[dict]:
        log = self.ctl / "runs.jsonl"
        if not log.is_file():
            return []
        return [json.loads(line) for line in
                log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def remote_tip(self) -> str:
        return git(self.scratch.remote, "rev-parse", "refs/heads/main")

    def requests(self) -> list[dict]:
        return self.scratch.events(kind="request")

    def attestations(self, envelope: str) -> list[dict]:
        request = wire.parse_request(envelope)
        records, err, _tag = validate.parse_attestations(
            wire.section(request.sections, "evidence"))
        self.assertIsNone(err)
        return records

    def assert_refused_before_push(self, code, payload, stderr, *,
                                   failed: list[str], tip: str,
                                   ran: bool = True):
        """`ran`: the failed gates executed, so each names its retained
        output; a not-run record has none to name."""
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(code, 1, payload)
        self.assertEqual(payload["next_kind"], "blocked")
        self.assertIsNone(payload["next"])
        for gid in failed:
            self.assertIn(gid, payload["error"])
            if ran:
                self.assertTrue(Path(payload["gate_output"][gid]).is_file())
            else:
                self.assertNotIn(gid, payload["gate_output"])
        self.assertIn("Nothing was pushed, emitted or recorded",
                      payload["error"])
        self.assertIn("judged by the request validator's own attestation "
                      "rule", payload["error"])
        self.assertEqual(self.remote_tip(), tip)
        self.assertEqual(self.requests(), [])


class TestARedBlockingGateStopsThePush(_Gated):

    def test_with_outstanding_work_the_commit_is_named_and_kept(self):
        s = self.scratch
        tip = self.remote_tip()
        self.red("block")
        (s.repo / "f.txt").write_text("three\n", encoding="utf-8")
        code, payload, stderr = s.handoff()
        committed = git(s.repo, "rev-parse", "HEAD")
        self.assertNotEqual(committed, s.head, "no commit was made")
        self.assert_refused_before_push(code, payload, stderr,
                                        failed=["block"], tip=tip)
        self.assertIn(f"committed its outstanding work locally at "
                      f"{committed}", payload["error"])
        self.assertIn(f"amends or resets the local commit {committed[:12]}",
                      payload["remedy"])
        self.assertIn("the tool does not undo its own commit",
                      payload["remedy"])
        self.assertEqual(payload["sha"], committed)
        self.assertIn("A-FAILED", {i["code"] for i in payload["items"]})
        # The advisory gate's red is not what refused: only `block` failed.
        self.assertNotIn("advise", payload["error"])
        runs = self.runs()
        self.assertEqual(sorted(r["gate"] for r in runs), ["advise", "block"])
        self.assertEqual({r["remote_tip"] for r in runs}, {tip},
                         "a gate ran after the push")
        # The paired control: the author fixes it, and the same hand-off —
        # over the commit the tool left — pushes and emits.
        self.green("block")
        code, payload, stderr = s.handoff()
        self.assertEqual(code, 0, (payload, stderr))
        self.assertEqual(self.remote_tip(), committed)
        self.assertEqual(len(self.requests()), 1)
        again = self.runs()[len(runs):]
        self.assertEqual(sorted(r["gate"] for r in again), ["advise", "block"])
        self.assertEqual({r["remote_tip"] for r in again}, {tip})

    def test_with_nothing_outstanding_no_commit_is_made(self):
        s = self.scratch
        tip = self.remote_tip()
        self.red("block", 3)
        Scratch.settle(s.repo)
        before = Scratch.snapshot(s.repo)
        code, payload, stderr = s.handoff()
        self.assert_refused_before_push(code, payload, stderr,
                                        failed=["block"], tip=tip)
        self.assertIn(f"no commit was made (HEAD is {s.head})",
                      payload["error"])
        self.assertIn("commits the fix and re-runs", payload["remedy"])
        self.assertEqual(Scratch.snapshot(s.repo), before)

    def test_emit_request_is_the_same_door(self):
        s = self.scratch
        tip = self.remote_tip()
        self.red("block")
        out = s.root / "request.md"
        code, payload, stderr = s.run(
            "emit-request", "--claim-file", str(s.claim), "--base", s.base,
            "--out", str(out))
        self.assert_refused_before_push(code, payload, stderr,
                                        failed=["block"], tip=tip)
        self.assertIn("`loupe emit-request`", payload["remedy"])
        self.assertFalse(out.exists())
        self.green("block")
        code, payload, stderr = s.run(
            "emit-request", "--claim-file", str(s.claim), "--base", s.base,
            "--out", str(out))
        self.assertEqual(code, 0, (payload, stderr))
        self.assertEqual(self.remote_tip(), s.head)


class TestAnUnknownMapGateStopsBeforeAnyGate(_Gated):
    """Integration of T1 and T3 (0.25.0): an `attestation_map` row naming a
    gate the governing manifest does not declare is judged in the
    gate-before-push callback — after the commit, which is the first moment
    the target's manifest exists, and BEFORE any gate runs or anything is
    pushed. Before this placement the refusal came at emission, after the
    push. Mutation: drop the `check_map_gates` call from the callback and
    the gates run and the push happens before the refusal."""

    FP = "fp2:" + "a" * 16

    def claim_with_map(self, gate: str) -> Path:
        path = self.scratch.root / f"claim-{gate}.json"
        path.write_text(json.dumps({
            "objective": "map gate placement",
            "references": [{"path": "review.toml", "required": True}],
            "carried_findings": [{"fingerprint": self.FP,
                                  "origin": "Lffffffffff/1",
                                  "outcome": "fixed"}],
            "attestation_map": [{"fingerprint": self.FP, "gate": gate}]}),
            encoding="utf-8")
        return path

    def test_refused_before_any_gate_and_before_the_push(self):
        s = self.scratch
        tip = self.remote_tip()
        (s.repo / "f.txt").write_text("four\n", encoding="utf-8")
        code, payload, stderr = s.run(
            "handoff", "--claim-file", str(self.claim_with_map("nope")),
            "--base", s.base)
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(code, 1, payload)
        self.assertEqual(payload["next_kind"], "blocked")
        self.assertIn("nope", payload["error"])
        self.assertIn("does not declare", payload["error"])
        self.assertEqual(self.runs(), [], "a gate ran before the refusal")
        self.assertEqual(self.remote_tip(), tip, "the push happened")
        self.assertEqual(self.requests(), [])
        # The commit the tool made before the manifest could be read is
        # kept, as every refusal after the commit keeps it.
        self.assertNotEqual(git(s.repo, "rev-parse", "HEAD"), s.head)

    def test_control_a_declared_gate_runs_the_gates_and_pushes(self):
        s = self.scratch
        (s.repo / "f.txt").write_text("four\n", encoding="utf-8")
        code, payload, stderr = s.run(
            "handoff", "--claim-file", str(self.claim_with_map("block")),
            "--base", s.base)
        self.assertEqual(code, 0, (payload, stderr))
        self.assertEqual(sorted(r["gate"] for r in self.runs()),
                         ["advise", "block"])
        self.assertEqual(self.remote_tip(), git(s.repo, "rev-parse", "HEAD"))


class TestWhatDoesNotStopThePush(_Gated):

    def test_a_red_advisory_gate_pushes_and_emits_as_before(self):
        s = self.scratch
        tip = self.remote_tip()
        self.red("advise")
        code, rec, stderr = s.handoff()
        self.assertEqual(code, 0, (rec, stderr))
        self.assertEqual(self.remote_tip(), s.head)
        self.assertEqual(len(self.requests()), 1)
        envelope = Path(rec["kept"]).read_text(encoding="utf-8")
        by_id = {r["id"]: r for r in self.attestations(envelope)}
        self.assertEqual(by_id["advise"]["exit_code"], 1)
        self.assertEqual(by_id["block"]["exit_code"], 0)
        self.assertEqual({r["remote_tip"] for r in self.runs()}, {tip})

    def test_all_green_every_gate_runs_once_and_before_the_push(self):
        s = self.scratch
        tip = self.remote_tip()
        code, rec, stderr = s.handoff()
        self.assertEqual(code, 0, (rec, stderr))
        runs = self.runs()
        self.assertEqual(sorted(r["gate"] for r in runs), ["advise", "block"])
        self.assertEqual({r["remote_tip"] for r in runs}, {tip})
        self.assertEqual(self.remote_tip(), s.head)
        envelope = Path(rec["kept"]).read_text(encoding="utf-8")
        self.assertEqual([r["id"] for r in self.attestations(envelope)],
                         ["block", "advise"])
        for record in self.attestations(envelope):
            self.assertEqual(record["binding"], "bound")

    def test_the_gates_are_told_the_resolved_base(self):
        """The base is resolved before the commit, and the gates — which
        now run before the push — are told the id the emission binds, not
        the spelling the author typed."""
        s = self.scratch
        code, rec, stderr = s.run("handoff", "--claim-file", str(s.claim),
                                  "--base", s.base[:10])
        self.assertEqual(code, 0, (rec, stderr))
        self.assertEqual({r["base"] for r in self.runs()}, {s.base})

    def test_no_derivable_base_refuses_before_any_gate_or_push(self):
        s = self.scratch
        tip = self.remote_tip()
        code, payload, stderr = s.run("handoff", "--claim-file",
                                      str(s.claim))
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(code, 2, payload)
        self.assertIn("no prior verdict in the ledger; pass --base",
                      payload["error"])
        self.assertEqual(self.runs(), [])
        self.assertEqual(self.remote_tip(), tip)
        self.assertEqual(self.requests(), [])

    def test_the_warm_cache_runs_no_gate_and_pushes_nothing(self):
        s = self.scratch
        counter = s.root / "pre-push.count"
        s.hook("pre-push", sleeping_pre_push(0, counter=counter))
        code, first, stderr = s.handoff()
        self.assertEqual(code, 0, (first, stderr))
        runs, pushes = len(self.runs()), counter.read_text().count("run")
        code, warm, stderr = s.handoff()
        self.assertEqual(code, 0, (warm, stderr))
        self.assertTrue(warm["cached"])
        self.assertEqual(warm["digest"], first["digest"])
        self.assertEqual(len(self.runs()), runs, "a gate ran on a warm serve")
        self.assertEqual(counter.read_text().count("run"), pushes,
                         "the warm serve pushed")


class TestLocalOnly(_Gated):

    def setUp(self):
        super().setUp()
        git(self.scratch.repo, "remote", "remove", "origin")

    def test_a_red_blocking_gate_refuses_before_the_emission(self):
        s = self.scratch
        self.red("block")
        out = s.root / "request.md"
        code, payload, stderr = s.handoff("--local-only", "--out", str(out))
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(code, 1, payload)
        self.assertEqual(payload["next_kind"], "blocked")
        self.assertIn("block", payload["error"])
        self.assertIn(f"no commit was made (HEAD is {s.head})",
                      payload["error"])
        self.assertFalse(out.exists())
        self.assertEqual(self.requests(), [])

    def test_green_gates_then_the_emission(self):
        s = self.scratch
        code, rec, stderr = s.handoff("--local-only")
        self.assertEqual(code, 0, (rec, stderr))
        self.assertEqual(len(self.requests()), 1)
        self.assertEqual(sorted(r["gate"] for r in self.runs()),
                         ["advise", "block"])
        envelope = Path(rec["kept"]).read_text(encoding="utf-8")
        self.assertIn(wire.PUSH_LOCAL_MARKER, envelope)


class TestNestedRecordsAreJudgedOnce(_Gated):
    """A hand-off run INSIDE a gate execution records its gates `not run:
    nested`. The pre-push check and the post-emission validation are the
    same rule, so they agree on those records too."""

    NESTED = {env_var("IN_GATE_RUN"): "1"}

    def test_a_nested_blocking_gate_refuses_before_the_push(self):
        s = self.scratch
        tip = self.remote_tip()
        code, payload, stderr = s.handoff(env=self.NESTED)
        self.assert_refused_before_push(code, payload, stderr,
                                        failed=["block"], tip=tip, ran=False)
        codes = {i["code"] for i in payload["items"]}
        self.assertIn("A-NOT-RUN", codes)
        self.assertIn("nested", json.dumps(payload["items"]))
        self.assertEqual(self.runs(), [], "a nested run executed a gate")


class TestANestedAdvisoryGateDoesNotStopIt(_Gated):
    GATES = (("advise", False, False),)
    PREFIX = "gate-nested-advisory-"

    def test_pushed_emitted_and_valid_after_as_before(self):
        s = self.scratch
        code, rec, stderr = s.handoff(env={env_var("IN_GATE_RUN"): "1"})
        self.assertEqual(code, 0, (rec, stderr))
        self.assertEqual(self.remote_tip(), s.head)
        [record] = self.attestations(Path(rec["kept"]).read_text(
            encoding="utf-8"))
        self.assertEqual(record["error"],
                         "not run: nested inside a gate execution")


class TestThePredicateIsTheValidators(unittest.TestCase):
    """`local_gate_failures` against `validate_attestations` over the record
    domain: they may never disagree, because they are one rule."""

    SHA = "a" * 40

    def _records(self):
        ran = {"command": "true", "tool_version": "t", "runner": "r",
               "target_sha": self.SHA, "executed_sha": self.SHA,
               "tree": "clean", "binding": "bound", "duration_s": 0.1,
               "output": {"sha256": "0" * 64, "pointer": "p"}}
        nested = "not run: nested inside a gate execution"
        return {
            "passing blocking": ({**ran, "exit_code": 0}, True),
            "failed blocking": ({**ran, "exit_code": 1}, True),
            "failed advisory": ({**ran, "exit_code": 1}, False),
            "nested blocking": ({"error": nested}, True),
            "nested advisory": ({"error": nested}, False),
            "unbound advisory": ({**ran, "exit_code": 0, "tree": "dirty",
                                  "binding": "unbound: dirty"}, False),
            "foreign target": ({**ran, "exit_code": 0,
                                "target_sha": "b" * 40,
                                "executed_sha": "b" * 40}, True),
        }

    def test_every_record_is_judged_alike_before_and_after(self):
        base = config.load(Path(__file__).resolve().parents[2])
        for name, (body, blocking) in self._records().items():
            with self.subTest(record=name):
                gate = {"id": "g", "command": ["true"], "blocking": blocking}
                cfg = dataclasses.replace(base, gates=[gate])
                record = {"id": "g", "blocking": blocking, **body}
                local = emit.LocalGates(self.SHA, "run", (record,))
                failed, items = emit.local_gate_failures(cfg, local)
                after = validate.errors_in(validate.validate_attestations(
                    emit._attestation_block(cfg.wrapper_tag, [record]), cfg,
                    request_sha=self.SHA))
                self.assertEqual(bool(failed), bool(after), (items, after))
                self.assertEqual([i.code for i in items],
                                 [i.code for i in after])


class TestThePriorHalfIsThisCommitsAndThisManifests(unittest.TestCase):
    """The half run before the push may only be carried by the request
    about the same commit, under the same manifest."""

    def test_another_commit_or_another_manifest_is_refused(self):
        cfg = dataclasses.replace(
            config.load(Path(__file__).resolve().parents[2]),
            gates=[{"id": "a", "command": ["true"], "blocking": True},
                   {"id": "b", "command": ["true"], "blocking": True}])
        record = {"id": "a", "blocking": True, "error": "not run: x"}
        other = {"id": "b", "blocking": True, "error": "not run: x"}
        rows = {
            "another commit": emit.LocalGates("b" * 40, "run",
                                              (record, other)),
            "another manifest": emit.LocalGates("a" * 40, "run", (record,)),
        }
        for name, prior in rows.items():
            with self.subTest(prior=name):
                with self.assertRaises(RuntimeError):
                    emit.run_gates(cfg, "a" * 40, prior=prior)
        control = emit.LocalGates("a" * 40, "run", (record, other))
        self.assertEqual(emit.run_gates(cfg, "a" * 40, prior=control),
                         [record, other])

    def test_another_range_is_refused(self):
        """Round 1 F1 of 0.25.0's review: the half run before the push was
        told a base, and a request binding another base may not carry it —
        its range-sensitive gates attested a range the request does not
        name. A base-less half is another range too. Mutation: drop the
        base comparison in `run_gates` and these rows stop refusing."""
        cfg = dataclasses.replace(
            config.load(Path(__file__).resolve().parents[2]),
            gates=[{"id": "a", "command": ["true"], "blocking": True}])
        record = {"id": "a", "blocking": True, "error": "not run: x"}
        rows = {
            "another base": ("c" * 40, "d" * 40),
            "a base-less half, a request with a base": (None, "d" * 40),
            "a half with a base, a base-less request": ("c" * 40, None),
        }
        for name, (told, binds) in rows.items():
            with self.subTest(prior=name):
                prior = emit.LocalGates("a" * 40, "run", (record,), told)
                with self.assertRaises(RuntimeError) as caught:
                    emit.run_gates(cfg, "a" * 40, base=binds, prior=prior)
                self.assertIn("base", str(caught.exception))
        for told in ("c" * 40, None):
            with self.subTest(control=told):
                prior = emit.LocalGates("a" * 40, "run", (record,), told)
                self.assertEqual(emit.run_gates(cfg, "a" * 40, base=told,
                                                prior=prior), [record])


class TestTheCIAttestedHalfWaitsForThePush(_Gated):
    """A mixed manifest — a CI-attested gate FIRST, then a local one. The
    local gate runs once, before the push; the Actions API is polled after
    it, about the pushed SHA; the envelope carries both in manifest order."""

    GATES = (("heavy", True, True), ("block", True, False))
    PREFIX = "gate-ci-half-"
    REMOTE_DIR = "acme/widget.git"
    URL_FORM = "file"

    TIP_LOG = (
        "import subprocess as _sp\n"
        "_tip = _sp.run(['git', 'ls-remote', os.environ['T1_REMOTE'], "
        "'refs/heads/main'], capture_output=True, text=True)"
        ".stdout.split('\\t')[0]\n"
        "with (d / 'tips.log').open('a', encoding='utf-8') as _f:\n"
        "    _f.write(json.dumps({'argv': argv, 'tip': _tip}) + '\\n')\n")

    def setUp(self):
        super().setUp()
        s = self.scratch
        self.stub = s.root / "stub"
        self.bin = s.root / "bin"
        for d in (self.stub, self.bin):
            d.mkdir()
        os.symlink(shutil.which("git"), self.bin / "git")
        anchor = "argv = sys.argv[1:]\n"
        gh = self.bin / "gh"
        gh.write_text(STUB.replace(anchor, anchor + self.TIP_LOG, 1),
                      encoding="utf-8")
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        cfg_gates = {g["id"]: g for g in
                     config.load(s.repo, ledger_dir=str(s.state)).gates}
        (self.stub / "responses.json").write_text(json.dumps(
            [{"stdout": json.dumps([run_row(s.head)])}]), encoding="utf-8")
        receipt = {"schema": emit.CI_RECEIPT_SCHEMA, "sha": s.head,
                   "tool_version": "loupe/test",
                   "gates": [{"id": gid,
                              "command": " ".join(cfg_gates[gid]["command"]),
                              "exit_code": 0, "duration_s": 1.0,
                              "not_run": None} for gid in cfg_gates]}
        (self.stub / "ci.json").write_text(json.dumps({
            "artifacts": {"*": {"names": [emit.ci_receipt_artifact(s.head)]}},
            "receipts": {"*": {"files": {emit.CI_RECEIPT_FILENAME:
                                         receipt}}}}), encoding="utf-8")
        self.env = {"PATH": f"{self.bin}:/usr/bin:/bin",
                    STUB_DIR_ENV: str(self.stub),
                    "T1_REMOTE": str(s.remote), "GITHUB_ACTIONS": ""}

    def test_local_before_the_push_ci_after_it_both_in_manifest_order(self):
        s = self.scratch
        tip = self.remote_tip()
        code, rec, stderr = s.handoff(env=self.env)
        self.assertEqual(code, 0, (rec, stderr))
        runs = self.runs()
        self.assertEqual([r["gate"] for r in runs], ["block"],
                         "only the local gate executes here, once")
        self.assertEqual(runs[0]["remote_tip"], tip)
        polls = [json.loads(line) for line in
                 (self.stub / "tips.log").read_text().splitlines()
                 if '"list"' in line]
        self.assertTrue(polls, "the Actions API was never asked")
        self.assertEqual({p["tip"] for p in polls}, {s.head},
                         "CI was polled before the push")
        records = self.attestations(Path(rec["kept"]).read_text(
            encoding="utf-8"))
        self.assertEqual([r["id"] for r in records], ["heavy", "block"])
        self.assertEqual(records[0]["attested_by"], "ci")
        self.assertEqual(records[0]["binding"], "bound")
        self.assertEqual(records[1]["executed_sha"], s.head)


# ------------------------------------------------ one base, resolved once
#
# Round 1 F1 of 0.25.0's review (High): `ensure_pushed` resolved `--base`
# BEFORE its own commit and told the gates that id, while `_emit` handed the
# ORIGINAL expression to `emit_request` and `diff_shape`, which resolved it
# again AFTER the commit and the push. For any base that moves with the
# tool's own action — `HEAD`, `HEAD~1`, the branch, its remote-tracking ref
# — the blocking gate attested one range and the request named another.
#
# THE INTERPRETATION, one and documented (`emit.ensure_pushed`): a base is
# resolved ONCE, before the hand-off commits anything, to the commit it
# names at that moment — the reading the author had when they typed it,
# since no automatic commit existed yet. That id is what every LOCAL gate
# is told, what `Base:` stamps, what the shape measures and what the
# post-emission validation recomputes against. (A CI-attested gate is told
# none: round 2 F2, the last section.)

#: The `Base:` line's id.
BASE_LINE = re.compile(r"^Base:\s+([0-9a-f]{40,64})\b", re.M)


class _Ranged(_Gated):
    """A manifest of a recorder (`block`) and the finding's range gate."""

    GATES = (("block", True, False),)
    EXTRA_GATES = (f'\n[[gates]]\nid = "whitespace"\n'
                   f"command = {json.dumps(RANGE_GATE)}\nblocking = true\n")
    PREFIX = "gate-base-"
    VERBS = ("handoff", "emit-request")

    def verb(self, verb: str, base: str):
        """(exit, payload, stderr, envelope or None) — `--base=<value>` in
        one word, so an option-shaped or empty value reaches the tool as a
        value rather than as argparse's business."""
        s = self.scratch
        out = s.root / "request.md"
        code, payload, stderr = s.run(verb, "--claim-file", str(s.claim),
                                      f"--base={base}", "--out", str(out))
        envelope = (out.read_text(encoding="utf-8") if out.exists()
                    else None)
        if verb == "handoff" and code == 0:
            # The kept bytes are the recorded request; `--out` is a copy.
            self.assertEqual(Path(payload["kept"]).read_text(
                encoding="utf-8"), envelope)
        return code, payload, stderr, envelope

    def emitted(self, verb: str) -> int:
        """How many requests the verb emitted: recorded ones for `handoff`,
        the written file for `emit-request`, which records nothing."""
        if verb == "handoff":
            return len(self.requests())
        return int((self.scratch.root / "request.md").exists())

    def rerun(self, base: str, head: str) -> int:
        """The range gate itself, over a range this test names."""
        return subprocess.run(
            RANGE_GATE, cwd=str(self.scratch.repo), capture_output=True,
            text=True, timeout=60, stdin=subprocess.DEVNULL,
            env={**os.environ, env_var("GATE_BASE"): base,
                 env_var("GATE_HEAD"): head}).returncode

    def assert_one_range(self, verb, code, payload, stderr, envelope, *,
                         expected: str) -> None:
        """The emitted request names ONE range, and it is the one every
        gate was told, the one the range gate's attestation holds over, and
        the one the shape was measured on — the finding's assertions, in
        that order — and, last, the one the documented interpretation
        names (`expected`, read before the run)."""
        s = self.scratch
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(code, 0, (payload, stderr))
        self.assertEqual(self.emitted(verb), 1)
        target = git(s.repo, "rev-parse", "HEAD")
        self.assertEqual(wire.parse_request(envelope).sha, target)
        [base] = BASE_LINE.findall(envelope)
        runs = self.runs()
        self.assertEqual(sorted(r["gate"] for r in runs), ["block"])
        for run in runs:
            self.assertEqual((run["base"], run["head"]), (base, target),
                             "a gate was told another range than the "
                             "request names")
        by_id = {r["id"]: r for r in self.attestations(envelope)}
        self.assertEqual(by_id["whitespace"]["binding"], "bound")
        self.assertEqual(by_id["whitespace"]["exit_code"],
                         self.rerun(base, target),
                         "the range gate over the EMITTED range disagrees "
                         "with its attestation")
        self.assertIn(f"{base}...{target}", envelope)
        numstat = [line.split("\t", 2) for line in git(
            s.repo, "diff", "--numstat", f"{base}...{target}").splitlines()]
        self.assertEqual(
            tuple(int(x) for x in
                  validate._DIFF_SHAPE_RE.search(envelope).groups()),
            (len(numstat), sum(int(a) for a, _d, _p in numstat),
             sum(int(d) for _a, d, _p in numstat)))
        self.assertEqual(base, expected,
                         "the base is what the expression named before the "
                         "hand-off's own commit and push")

    def assert_refused_nothing_emitted(self, verb, code, payload, stderr,
                                       envelope) -> None:
        self.assertNotIn("Traceback", stderr)
        self.assertNotEqual(code, 0, payload)
        self.assertIsNone(envelope)
        self.assertEqual(self.emitted(verb), 0)


class TestEveryBaseFormBindsOneRange(_Ranged):
    """THE PARTITION: immutable (full, abbreviated), symbolic (the branch,
    its remote-tracking ref, a lightweight and an annotated tag), relative
    (`HEAD`, `HEAD~1`) — with and without outstanding tracked work — through
    BOTH author verbs. Every row: the request names the base its expression
    named before the run, every gate was told exactly that range, and the
    range gate re-run over the emitted range agrees with its attestation.

    The rows that exercise a MOVE assert that they do: after the run their
    expression names another commit, so a second resolution would have
    found it. `origin/main` moves with the PUSH even when nothing was
    committed. Mutation (the finding's): forward the unresolved expression
    to the emission again, and every moving row fails."""

    FORMS = ("full", "abbreviated", "branch", "remote-tracking",
             "lightweight tag", "annotated tag", "HEAD", "HEAD~1")
    #: Which forms name another commit after the run, by outstanding work.
    MOVES = {True: {"branch", "remote-tracking", "HEAD", "HEAD~1"},
             False: {"remote-tracking"}}

    def spelled(self, form: str) -> str:
        s = self.scratch
        parent = git(s.repo, "rev-parse", "HEAD~1")
        git(s.repo, "tag", "light", parent)
        git(s.repo, "tag", "-a", "-m", "annotated", "ann", parent)
        return {"full": parent, "abbreviated": parent[:10],
                "branch": "main", "remote-tracking": "origin/main",
                "lightweight tag": "light", "annotated tag": "ann",
                "HEAD": "HEAD", "HEAD~1": "HEAD~1"}[form]

    def test_the_partition_through_both_verbs(self):
        for verb in self.VERBS:
            for outstanding in (True, False):
                for form in self.FORMS:
                    with self.subTest(verb=verb, outstanding=outstanding,
                                      form=form):
                        s = self.fresh()
                        spelled = self.spelled(form)
                        expected = git(s.repo, "rev-parse", "--verify",
                                       f"{spelled}^{{commit}}")
                        if outstanding:
                            (s.repo / "f.txt").write_text(
                                "three\nfour\n", encoding="utf-8")
                        code, payload, stderr, envelope = self.verb(
                            verb, spelled)
                        self.assert_one_range(verb, code, payload, stderr,
                                              envelope, expected=expected)
                        after = git(s.repo, "rev-parse", "--verify",
                                    f"{spelled}^{{commit}}")
                        self.assertEqual(
                            after != expected,
                            form in self.MOVES[outstanding],
                            "the row does not exercise what it names")


class TestTheWhitespaceReproduction(_Ranged):
    """The reviewer's reproduction, through the real CLI. Commit A holds
    `x ` (a trailing space), B removes it, the outstanding work C restores
    it. The range gate is `git diff --check "$BASE" "$HEAD"`.

    `--base HEAD~1` names A (it named A when typed): the gate checks A..C,
    which is clean, and the request names A..C — the gate re-run over the
    emitted range agrees. Before the fix the request named B..C, over which
    the gate exits 2, while the envelope stamped its exit 0.

    The paired control is the immutable B, and `--base HEAD` — B when
    typed — names the same range: the blocking gate is red over B..C, so
    the hand-off refuses before the push and emits nothing."""

    def abc(self) -> tuple[str, str]:
        s = self.fresh()
        (s.repo / "f.txt").write_text("x \n", encoding="utf-8")
        git(s.repo, "commit", "-qam", "A: a trailing space")
        a = git(s.repo, "rev-parse", "HEAD")
        (s.repo / "f.txt").write_text("x\n", encoding="utf-8")
        git(s.repo, "commit", "-qam", "B: clean")
        b = git(s.repo, "rev-parse", "HEAD")
        (s.repo / "f.txt").write_text("x \n", encoding="utf-8")
        return a, b

    def test_head_tilde_one_checks_and_emits_one_range(self):
        for verb in self.VERBS:
            with self.subTest(verb=verb):
                a, b = self.abc()
                code, payload, stderr, envelope = self.verb(verb, "HEAD~1")
                self.assert_one_range(verb, code, payload, stderr, envelope,
                                      expected=a)
                target = git(self.scratch.repo, "rev-parse", "HEAD")
                self.assertNotEqual(target, b, "C was not committed")
                # The range the defect emitted, for the record: the same
                # gate over B..C is red.
                self.assertNotEqual(self.rerun(b, target), 0)

    def test_the_immutable_control_and_head_refuse_before_the_push(self):
        for verb in self.VERBS:
            for spelling in ("B", "HEAD"):
                with self.subTest(verb=verb, base=spelling):
                    a, b = self.abc()
                    s = self.scratch
                    tip = self.remote_tip()
                    code, payload, stderr, envelope = self.verb(
                        verb, b if spelling == "B" else "HEAD")
                    self.assert_refused_nothing_emitted(
                        verb, code, payload, stderr, envelope)
                    self.assertEqual(code, 1, payload)
                    self.assertEqual(payload["next_kind"], "blocked")
                    self.assertIn("whitespace", payload["error"])
                    self.assertEqual(self.remote_tip(), tip)
                    committed = git(s.repo, "rev-parse", "HEAD")
                    self.assertEqual(git(s.repo, "rev-parse", "HEAD~1"), b)
                    self.assertEqual(
                        {(r["base"], r["head"]) for r in self.runs()},
                        {(b, committed)})
                    # The refusal agrees with the gate over the range it
                    # judged — and that range is B..C, not A..C.
                    self.assertNotEqual(self.rerun(b, committed), 0)
                    self.assertEqual(self.rerun(a, committed), 0)


class TestABaseThatNamesNoOneCommitRefusesBeforeTheCommit(_Ranged):
    """A base that does not resolve to exactly one commit — absent, past
    the root, a RANGE, option-shaped, empty, a tree — refuses before
    anything is committed, gated, pushed or emitted: the refs, index,
    status and files byte-identical. Before the fix a range or an
    option-shaped value resolved to SEVERAL lines, and a tree to a tree:
    each was committed and gated, and the tree was pushed. Mutations: drop
    `--verify`, or the `^{commit}` peel, and those rows go red."""

    BASES = ("nosuch", "HEAD~50", "HEAD~1..HEAD", "--all", "",
             "HEAD^{tree}")

    def test_every_verb_every_unresolvable_spelling(self):
        for verb in self.VERBS:
            for base in self.BASES:
                with self.subTest(verb=verb, base=base):
                    s = self.fresh()
                    (s.repo / "f.txt").write_text("outstanding\n",
                                                  encoding="utf-8")
                    Scratch.settle(s.repo)
                    before = Scratch.snapshot(s.repo)
                    tip = self.remote_tip()
                    code, payload, stderr, envelope = self.verb(verb, base)
                    self.assert_refused_nothing_emitted(
                        verb, code, payload, stderr, envelope)
                    self.assertEqual(code, 2, payload)
                    self.assertIn("does not name one commit",
                                  payload["error"])
                    self.assertIn("nothing has been committed",
                                  payload["error"])
                    self.assertEqual(Scratch.snapshot(s.repo), before)
                    self.assertEqual(self.runs(), [])
                    self.assertEqual(self.remote_tip(), tip)

    def test_control_the_same_scratch_with_a_resolvable_base(self):
        for verb in self.VERBS:
            with self.subTest(verb=verb):
                s = self.fresh()
                (s.repo / "f.txt").write_text("outstanding\n",
                                              encoding="utf-8")
                code, payload, stderr, envelope = self.verb(verb, "HEAD~1")
                self.assert_one_range(verb, code, payload, stderr, envelope,
                                      expected=git(s.repo, "rev-parse",
                                                   "HEAD~2"))


class TestANonAncestorBaseEmitsNothing(_Ranged):
    """An immutable base that is not an ancestor of the target resolves,
    and is refused by `emit_request`'s ancestry rule: no request. (That
    refusal comes after the push, as it did before 0.25.0 — not this
    finding's; recorded in the track report.)"""

    def test_both_verbs(self):
        for verb in self.VERBS:
            with self.subTest(verb=verb):
                s = self.fresh()
                git(s.repo, "checkout", "-q", "-b", "side", s.base)
                (s.repo / "side.txt").write_text("side\n", encoding="utf-8")
                git(s.repo, "add", "side.txt")
                git(s.repo, "commit", "-qm", "side")
                side = git(s.repo, "rev-parse", "HEAD")
                git(s.repo, "checkout", "-q", "main")
                code, payload, stderr, envelope = self.verb(verb, side)
                self.assert_refused_nothing_emitted(
                    verb, code, payload, stderr, envelope)
                self.assertIn("is not an ancestor", payload["error"])
                self.assertEqual({r["base"] for r in self.runs()}, {side})


# ------------------------------- the one base stops at the local gates
#
# Round 2 F2 of 0.25.0's review (Medium): the one-base guarantee above was
# PUBLISHED for every gate, while a gate declared `attested_by = "ci"` is
# run by CI, which is told no review base, and the hand-off takes CI's
# receipt. That evidence binds the target commit and proves nothing about
# the range. The ruled answer is a complete NARROWING, not a CI redesign:
# the guarantee reaches the gates the hand-off runs LOCALLY, a
# range-sensitive gate must run locally to receive it, and every statement
# of it says so.

GATE_BASE, GATE_HEAD = env_var("GATE_BASE"), env_var("GATE_HEAD")

#: The reviewer's range gate, verbatim: told no base, it falls back to the
#: last commit — which is exactly the range CI, told no base, checks.
FALLBACK_RANGE_GATE = ["sh", "-c",
                       f'git diff --check "${{{GATE_BASE}:-HEAD~1}}" '
                       f'"${GATE_HEAD}"']

#: What this process's environment may not leak into a runner it starts.
RUNNER_NAMES = tuple(env_var(n) for n in (
    "IN_GATE_RUN", "GATE_HEAD", "GATE_BASE", "STATE_DIR", "CONFIG",
    "GATE_WORKERS", "SHIM", "CALLER_PYTHONSAFEPATH", "CALLER_PYTHONPATH"))


class _CIRange(_Gated):
    """The reviewer's A/B/C through the real hand-off: A clean (the
    manifest commit), B introduces a trailing space, C is an unrelated
    clean change and the target. The manifest is a local recorder
    (`block`) and the range gate `whitespace` — CI-attested or local,
    the SAME command either way. CI's receipt is produced by the real
    runner, inside CI and told no base, and served by a fake `gh`."""

    GATES = (("block", True, False),)
    PREFIX = "gate-ci-range-"
    REMOTE_DIR = "acme/widget.git"
    URL_FORM = "file"
    VERBS = ("handoff", "emit-request")
    #: The CI entry point the workbench's workflow runs. An extracted
    #: candidate ships none, and there the receipt comes from the two calls
    #: it makes (`run_gates` inside CI, then `gate_receipt`) alone.
    RUNNER = REPO_ROOT / "bin" / "loupe-gates"

    def abc(self, *, ci: bool) -> tuple[str, str, str]:
        self.EXTRA_GATES = (f'\n[[gates]]\nid = "whitespace"\n'
                            f"command = {json.dumps(FALLBACK_RANGE_GATE)}\n"
                            f"blocking = true\n"
                            + ('attested_by = "ci"\n' if ci else ""))
        s = self.fresh()
        a = s.head
        (s.repo / "f.txt").write_text("x \n", encoding="utf-8")
        git(s.repo, "commit", "-qam", "B: a trailing space")
        b = git(s.repo, "rev-parse", "HEAD")
        (s.repo / "other.txt").write_text("an unrelated clean change\n",
                                          encoding="utf-8")
        git(s.repo, "add", "other.txt")
        git(s.repo, "commit", "-qm", "C: clean")
        c = git(s.repo, "rev-parse", "HEAD")
        declared = {g["id"]: g for g in
                    config.load(s.repo, ledger_dir=str(s.state)).gates}
        self.assertEqual(declared["whitespace"]["command"],
                         FALLBACK_RANGE_GATE)
        self.assertEqual(declared["whitespace"].get("attested_by"),
                         "ci" if ci else None)
        self.install_gh()
        return a, b, c

    def install_gh(self) -> None:
        s = self.scratch
        self.stub, self.bin = s.root / "stub", s.root / "bin"
        for d in (self.stub, self.bin):
            d.mkdir()
        os.symlink(shutil.which("git"), self.bin / "git")
        gh = self.bin / "gh"
        gh.write_text(STUB, encoding="utf-8")
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        # `GITHUB_ACTIONS` empty: this suite also runs inside CI, where the
        # hand-off would otherwise execute the CI-attested gate itself.
        self.env = {"PATH": f"{self.bin}:/usr/bin:/bin",
                    STUB_DIR_ENV: str(self.stub), "GITHUB_ACTIONS": ""}

    @staticmethod
    def rows(receipt: dict) -> list[dict]:
        """A receipt's rows without wall time, the one field two runs of
        the same manifest may not share."""
        return [{k: v for k, v in r.items() if k != "duration_s"}
                for r in receipt["gates"]]

    def ci_receipt(self, head: str) -> dict:
        """CI's receipt for `head`, made the way CI makes it: the runner
        inside CI (`GITHUB_ACTIONS`), with NO review base — the workflow
        passes none — then one row per declared gate. Where the workbench's
        own entry point exists it runs too, from inside the scratch
        repository as CI runs it from inside the checkout, with
        `--receipt`; its rows must be the in-process ones, and its receipt
        is the one served."""
        s = self.scratch
        ambient = {k: v for k, v in os.environ.items()
                   if k not in RUNNER_NAMES + ("PYTHONSAFEPATH",
                                               "PYTHONPATH")}
        ambient.update({"PATH": self.env["PATH"], emit.CI_ENV: "true"})
        cfg = config.load(s.repo, ledger_dir=str(s.root / "state-ci"))
        with mock.patch.dict(os.environ, ambient, clear=True):
            records = emit.run_gates(cfg, head)
        receipt = emit.gate_receipt(head, cfg.gates,
                                    {r["id"]: r for r in records})
        if not self.RUNNER.is_file():
            return receipt
        # The runner's root is its grandparent, so it sits where CI's does:
        # `bin/loupe-gates` inside the checkout it gates — ignored, so the
        # tree stays exactly the target's (it is CI's tool, not the work).
        runner = s.repo / "bin" / "loupe-gates"
        runner.parent.mkdir()
        shutil.copy2(self.RUNNER, runner)
        exclude = s.repo / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a", encoding="utf-8") as fh:
            fh.write("/bin/\n")
        out = s.root / "loupe-receipt.json"
        proc = subprocess.run(
            [sys.executable, "-B", str(runner), "--receipt", str(out)],
            cwd=str(s.repo), capture_output=True, text=True, timeout=180,
            stdin=subprocess.DEVNULL,
            env=cli_env(s.root / "state-ci-runner",
                        {"PATH": self.env["PATH"], emit.CI_ENV: "true"}))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(git(s.repo, "status", "--porcelain"), "")
        produced = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(self.rows(produced), self.rows(receipt))
        self.assertEqual({k: v for k, v in produced.items() if k != "gates"},
                         {k: v for k, v in receipt.items() if k != "gates"})
        return produced

    def serve(self, head: str, receipt: dict) -> None:
        """The fake `gh`: one completed green run at `head`, whose artifact
        `loupe-gates-<head>` holds `receipt`."""
        (self.stub / "responses.json").write_text(json.dumps(
            [{"stdout": json.dumps([run_row(head)])}]), encoding="utf-8")
        (self.stub / "ci.json").write_text(json.dumps({
            "artifacts": {"*": {"names": [emit.ci_receipt_artifact(head)]}},
            "receipts": {"*": {"files": {emit.CI_RECEIPT_FILENAME:
                                         receipt}}}}), encoding="utf-8")

    def verb(self, verb: str, base: str):
        """(exit, payload, stderr, envelope or None)."""
        s = self.scratch
        out = s.root / "request.md"
        out.unlink(missing_ok=True)
        code, payload, stderr = s.run(verb, "--claim-file", str(s.claim),
                                      f"--base={base}", "--out", str(out),
                                      env=self.env)
        envelope = (out.read_text(encoding="utf-8") if out.exists()
                    else None)
        return code, payload, stderr, envelope

    def emitted(self, verb: str) -> int:
        if verb == "handoff":
            return len(self.requests())
        return int((self.scratch.root / "request.md").exists())

    def rerun(self, base: str | None, head: str) -> int:
        """The range gate itself, told `base` (none: CI's own telling)."""
        env = {k: v for k, v in os.environ.items() if k not in RUNNER_NAMES}
        env.update({"PATH": self.env["PATH"], GATE_HEAD: head})
        if base is not None:
            env[GATE_BASE] = base
        return subprocess.run(
            FALLBACK_RANGE_GATE, cwd=str(self.scratch.repo),
            capture_output=True, text=True, timeout=60,
            stdin=subprocess.DEVNULL, env=env).returncode

    def told(self, since: int) -> list[tuple]:
        """(gate, base, head) for every recorder execution after `since`."""
        return [(r["gate"], r["base"], r["head"])
                for r in self.runs()[since:]]


class TestACIAttestedRangeGateBindsTheCommitNotTheBase(_CIRange):
    """THE LIMIT, demonstrated through the real runner and the real
    hand-off. The one review base reaches the gates the hand-off runs
    LOCALLY; a CI-attested gate's evidence binds the target commit but
    does not prove the review base, because CI runs the manifest with no
    base and its receipt carries none. So a range-sensitive gate must run
    locally to receive the guarantee — the paired class below is that
    local control.

    Row base=A (the reviewer's): CI's receipt, made base-less over B..C,
    records the blocking range gate exit 0; `--base A` emits A..C with
    that attestation, bound and green; the same gate over the EMITTED
    range exits 2. The recorder, a local gate, was told A. Row base=B: CI's
    fallback range IS the emitted range, and the two agree — the
    disagreement is the base, not the mechanism."""

    def test_the_reviewers_abc_ci_attested(self):
        for verb in self.VERBS:
            for name in ("A", "B"):
                with self.subTest(verb=verb, base=name):
                    a, b, c = self.abc(ci=True)
                    base = {"A": a, "B": b}[name]
                    receipt = self.ci_receipt(c)
                    keys = set(receipt) | {k for r in receipt["gates"]
                                           for k in r}
                    self.assertEqual([k for k in keys if "base" in k], [],
                                     "a CI receipt carries no review base")
                    [row] = [r for r in receipt["gates"]
                             if r["id"] == "whitespace"]
                    self.assertEqual((row["exit_code"], row["not_run"]),
                                     (0, None))
                    self.serve(c, receipt)
                    since = len(self.runs())
                    code, payload, stderr, envelope = self.verb(verb, base)
                    self.assertNotIn("Traceback", stderr)
                    self.assertEqual(code, 0, (payload, stderr))
                    self.assertEqual(self.emitted(verb), 1)
                    self.assertEqual(self.remote_tip(), c)
                    self.assertEqual(wire.parse_request(envelope).sha, c)
                    self.assertEqual(BASE_LINE.findall(envelope), [base])
                    # The one base reached the gate the hand-off ran.
                    self.assertEqual(self.told(since), [("block", base, c)])
                    by_id = {r["id"]: r for r in self.attestations(envelope)}
                    ws = by_id["whitespace"]
                    self.assertEqual(
                        (ws["attested_by"], ws["binding"], ws["exit_code"]),
                        ("ci", "bound", 0))
                    # What CI's evidence is about: its own base-less range.
                    self.assertEqual(self.rerun(None, c), 0)
                    self.assertEqual(self.rerun(b, c), 0)
                    # What the request names: A..C for row A, where the
                    # same gate is red — the limit, in one line.
                    self.assertEqual(self.rerun(base, c),
                                     2 if name == "A" else 0)
                    self.assertEqual(self.rerun(a, c), 2)


class TestTheSameGateRunLocallyRefusesTheEmittedRange(_CIRange):
    """THE LOCAL CONTROL: the identical command, not CI-attested, over the
    identical A/B/C. The hand-off tells it the one base, so with
    `--base A` it checks A..C, is red, and the hand-off refuses BEFORE the
    push: the remote's refs, the repository (refs, index, status, files)
    and the ledger byte-identical, nothing emitted. With `--base B` it
    checks B..C, is green, and the hand-off emits — the gate is live, not
    red by construction."""

    def test_the_bad_emitted_range_refuses_before_the_push(self):
        for verb in self.VERBS:
            with self.subTest(verb=verb):
                a, b, c = self.abc(ci=False)
                s = self.scratch
                Scratch.settle(s.repo)
                repo = Scratch.snapshot(s.repo)
                remote = Scratch.snapshot(s.remote, bare=True)
                ledger = s.state / "ledger.jsonl"
                recorded = (ledger.read_bytes() if ledger.is_file()
                            else None)
                tip = self.remote_tip()
                since = len(self.runs())
                code, payload, stderr, envelope = self.verb(verb, a)
                self.assert_refused_before_push(
                    code, payload, stderr, failed=["whitespace"], tip=tip)
                self.assertIsNone(envelope)
                self.assertEqual(self.emitted(verb), 0)
                self.assertIn(f"no commit was made (HEAD is {c})",
                              payload["error"])
                self.assertEqual(Scratch.snapshot(s.repo), repo)
                self.assertEqual(Scratch.snapshot(s.remote, bare=True),
                                 remote)
                self.assertEqual(ledger.read_bytes() if ledger.is_file()
                                 else None, recorded)
                self.assertEqual(self.told(since), [("block", a, c)])
                self.assertIn("trailing whitespace", Path(
                    payload["gate_output"]["whitespace"]).read_text(
                        encoding="utf-8"))
                self.assertEqual(self.rerun(a, c), 2)
                self.assertEqual(self.rerun(b, c), 0)

    def test_control_the_range_that_is_clean_emits(self):
        for verb in self.VERBS:
            with self.subTest(verb=verb):
                a, b, c = self.abc(ci=False)
                code, payload, stderr, envelope = self.verb(verb, b)
                self.assertNotIn("Traceback", stderr)
                self.assertEqual(code, 0, (payload, stderr))
                self.assertEqual(self.emitted(verb), 1)
                self.assertEqual(self.remote_tip(), c)
                self.assertEqual(BASE_LINE.findall(envelope), [b])
                ws = {r["id"]: r for r in
                      self.attestations(envelope)}["whitespace"]
                self.assertNotIn("attested_by", ws)
                self.assertEqual((ws["binding"], ws["exit_code"]),
                                 ("bound", 0))


# ------------------------------------ every statement names its limit
#
# The guarantee is prose in several published places, and each is a
# promise an adopter relies on. A statement of it — in any phrasing it has
# been published in — must name the limit in the SAME paragraph or list
# item, so no reader meets the promise without its scope. Lexical, so it
# owns exactly what it says: a statement the detector does not recognise
# escapes it (the detector is the phrasings below, and the controls prove
# each is live); it proves nothing about behaviour, which the two classes
# above do.

#: A statement of the one-base guarantee.
GUARANTEE = re.compile(
    r"\bone review base\b|\bthe one base\b|\bresolved once\b"
    r"|\bgates?\b[^.;]{0,80}?\b(?:is|are) told\b"
    r"|\btells? (?:the |every |each |all )?(?:local )?gates?\b"
    r"|GATE_BASE[^.;]{0,60}?\b(?:every|each|all) gates?\b",
    re.I)

#: The limit, clause by clause: each must be in the statement's own block.
LIMIT = (
    ("the guarantee reaches the gates run locally",
     re.compile(r"\blocal(?:ly)?\b", re.I)),
    ("a CI-attested gate is outside it",
     re.compile(r'CI-attested|attested_by = "ci"', re.I)),
    ("CI evidence binds the target commit",
     re.compile(r"\bbinds? the target commit\b", re.I)),
    ("but does not prove the review base",
     re.compile(r"\b(?:does not|doesn't|cannot|never) prove (?:the|this) "
                r"(?:review )?base\b|\bnot (?:the|this) (?:review )?base\b",
                re.I)),
    ("a range-sensitive gate must run locally",
     re.compile(r"\brange-sensitive gate\b[^.;]*?\b(?:must run locally"
                r"|should not be CI-attested)", re.I)),
)

#: The 0.25.0 bullet as round 2 found it — the overbroad every-gate
#: promise. The detector must flag it and the limit must be missing.
OVERBROAD = (
    "- One review base. A symbolic or relative `--base` (`HEAD`, `HEAD~1`, "
    "the\n  branch, its remote-tracking ref, a tag) is resolved once, "
    "before the\n  hand-off commits outstanding work. That one commit id "
    "is what every gate\n  is told, what `Base:` stamps, and what the shape "
    "and the post-emission\n  validation measure. Anything that is not "
    "exactly one commit (a range, a\n  tree, an absent name) refuses before "
    "anything is committed.\n")


def blocks(text: str) -> list[str]:
    """Paragraphs and list items, each on one line."""
    out = []
    for para in re.split(r"\n[ \t]*\n", text):
        for item in re.split(r"\n(?=[ \t]*(?:[-*+]|\d+\.)[ \t])", para):
            flat = re.sub(r"\s+", " ", item).strip()
            if flat:
                out.append(flat)
    return out


def unlimited(text: str) -> list[tuple[str, list[str]]]:
    """(statement, the limit clauses it lacks) for every statement of the
    guarantee in `text` that does not name the whole limit."""
    found = []
    for block in blocks(text):
        if GUARANTEE.search(block):
            missing = [name for name, rx in LIMIT if not rx.search(block)]
            if missing:
                found.append((block, missing))
    return found


def base_help() -> dict[str, str]:
    """The `--base` help of every verb that takes one, as argparse holds
    it — what `--help` prints."""
    parser = cli.build_parser()
    [sub] = [a for a in parser._actions if a.__class__.__name__ ==
             "_SubParsersAction"]
    return {name: action.help for name, verb in sub.choices.items()
            for action in verb._actions if "--base" in action.option_strings}


def guarantee_sources() -> dict[str, str]:
    """Every downstream text the guarantee is stated in, by name: the
    0.25.0 CHANGELOG section, every shipped doc and the README (whichever
    of this tree's two layouts holds them — `public/` in the workbench, the
    root in an extracted candidate; absent ones are simply not sources),
    every rendered adapter, the `--base` help, and the docstring of the one
    place the base is resolved."""
    out = {}
    changelog = public_path("CHANGELOG.md")
    if changelog is not None:
        found = re.search(r"^## 0\.25\.0\n(.*?)(?=^## )",
                          changelog.read_text(encoding="utf-8"),
                          re.S | re.M)
        assert found, "the CHANGELOG no longer carries a 0.25.0 section"
        out["CHANGELOG 0.25.0"] = found.group(1)
    readme = public_path("README.md")
    if readme is not None:
        out["README.md"] = readme.read_text(encoding="utf-8")
    onboarding = public_path("docs/onboarding.md")
    if onboarding is not None:
        for doc in sorted(onboarding.parent.glob("*.md")):
            out[f"docs/{doc.name}"] = doc.read_text(encoding="utf-8")
    for kind in adapters.OUTPUTS:
        out[f"adapter {kind}"] = adapters.render(kind)
    for verb, text in base_help().items():
        out[f"{verb} --base help"] = text
    out["emit.ensure_pushed"] = emit.ensure_pushed.__doc__
    return out


class TestEveryStatementOfTheOneBaseNamesItsLimit(unittest.TestCase):
    """Every downstream statement of the one-base guarantee names the
    local/CI limit. Mutation (the reviewer's): restore the overbroad
    every-gate wording in the CHANGELOG, and this fails naming it."""

    def test_the_detector_is_live_on_the_overbroad_wording(self):
        """The red control: round 2's bullet is a statement, lacking every
        clause but the ones it happened to carry. And the phrasings the
        guarantee has had are each recognised alone."""
        [(block, missing)] = unlimited(OVERBROAD)
        self.assertIn("what every gate is told", block)
        self.assertEqual(missing, [name for name, _rx in LIMIT])
        for phrasing in ("One review base.", "the one base",
                         "it is resolved once", "every gate is told",
                         "the gates are told the base",
                         "the hand-off tells the gates its base",
                         "exports LOUPE_GATE_BASE to every gate"):
            with self.subTest(phrasing=phrasing):
                self.assertTrue(GUARANTEE.search(phrasing))

    def test_every_statement_names_the_limit(self):
        sources = guarantee_sources()
        # Paired controls: the sources are present and read (the rendered
        # adapters and the help exist in every tree), and the ones that
        # state the guarantee today are found stating it.
        self.assertTrue(any(k.startswith("adapter ") for k in sources))
        self.assertEqual({k for k in sources if k.endswith("--base help")},
                         {"handoff --base help",
                          "emit-request --base help"})
        stating = {name for name, text in sources.items()
                   if any(GUARANTEE.search(b) for b in blocks(text))}
        expected = {"handoff --base help", "emit-request --base help",
                    "emit.ensure_pushed"}
        if "CHANGELOG 0.25.0" in sources:
            expected.add("CHANGELOG 0.25.0")
        if "docs/onboarding.md" in sources:
            expected.add("docs/onboarding.md")
        self.assertLessEqual(expected, stating,
                             "a known statement of the guarantee is no "
                             "longer recognised: the guard reads nothing")
        for name, text in sources.items():
            with self.subTest(source=name):
                self.assertEqual(
                    unlimited(text), [],
                    f"{name} states the one-base guarantee without its "
                    f"local/CI limit")

    def test_the_gate_manifest_guidance_keeps_range_gates_local(self):
        """Consumer guidance where an adopter DECLARES gates: onboarding
        §2 says a range-sensitive gate should not be CI-attested, and
        why. Mutation: delete that bullet, and this fails."""
        onboarding = public_path("docs/onboarding.md")
        if onboarding is None:
            self.skipTest("no docs/onboarding.md shipped in this tree")
        found = re.search(r"^## 2\. .*?(?=^## 3\. )",
                          onboarding.read_text(encoding="utf-8"),
                          re.S | re.M)
        self.assertIsNotNone(found, "onboarding §2 is not where this "
                                    "check reads it")
        guidance = [b for b in blocks(found.group(0))
                    if re.search(r"\brange-sensitive gate\b", b)]
        self.assertTrue(guidance, "§2 no longer says where a "
                                  "range-sensitive gate runs")
        self.assertTrue(any(not [n for n, rx in LIMIT if not rx.search(b)]
                            for b in guidance),
                        f"§2's guidance lacks part of the limit: "
                        f"{guidance}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

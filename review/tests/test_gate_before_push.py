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

Every row runs in a scratch repository with a bare remote and its own
state directory, at one of two levels. END TO END, through
`python3 -m review` as a separate process, as a caller runs it: each guard
keeps its accept row and one row per refusal kind there, through both
author verbs wherever the verb is a dimension, so the wiring is proven
where a caller meets it. IN PROCESS, the rest of each partition, against
the functions that decide it, composed as `cli._emit` composes them:
`emit.ensure_pushed` (the one resolution of the base, its refusals, the
commit and the push) with, as its `before_push`, `emit.gate_before_push`
(the local gates at the commit, refused before the push), under the
environment `Scratch.run` gives its CLI child (`child_environment`), so a
row run inside a gate execution is judged as that child would be.
`emit.local_gate_failures` and `emit.run_gates`' carried half are judged
directly. Each class that splits its rows says which run where, and why.

The scratch — author repository, bare remote, the committed manifest —
is built once per manifest and remote shape and copied per test
(`GatedScratch`); a row that commits, pushes or runs a gate does so in
its own copy. Each gate is a real command that APPENDS one line per
execution to a counter outside the repository — the gate id, and the tip
the remote carried when it ran — so "ran once" and "ran before the push"
are counted, not assumed. A gate's exit code is read from a control file,
so red and green are flipped without a commit.

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
R2a for the base; round 3, track R3b for its scope), and measured red
again against the rows as they now run, before and after the split
between the two levels (track report of 2026-09-27).
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

from review import (TOOL_NAME, adapters, cli, config, emit, env_var,
                    validate, wire)
from review.tests._transport_fixtures import copied_instance
from review.tests.test_ci_attested_gates import STUB, STUB_DIR_ENV, run_row
from review.tests.test_git_timeout import (Scratch, cli_env, git,
                                           sleeping_pre_push)
from review.tests.util import REPO_ROOT, public_path

GATE_BASE, GATE_HEAD = env_var("GATE_BASE"), env_var("GATE_HEAD")

#: What this process's environment may not leak into a runner it starts —
#: nor into an in-process row, which stands where that runner's child was.
RUNNER_NAMES = tuple(env_var(n) for n in (
    "IN_GATE_RUN", "GATE_HEAD", "GATE_BASE", "STATE_DIR", "CONFIG",
    "GATE_WORKERS", "SHIM", "CALLER_PYTHONSAFEPATH", "CALLER_PYTHONPATH"))

#: A gate: one line to the counter per execution — its id, the remote's tip
#: at that moment, and the review range the runner told it (base and head)
#: — then the exit code its control file holds (0 when there is none). The
#: scratch is found from the gate's working directory, the scratch
#: repository (`<root>/author`) every runner starts a gate in, so the
#: manifest names no path of its own and is committed once, into the
#: template every copy is made from (`GatedScratch`).
GATE_SCRIPT = """\
import json, os, pathlib, subprocess, sys
root = pathlib.Path.cwd().parent
ctl = root / "ctl"
tip = subprocess.run(["git", "ls-remote", str(root / {remote_dir!r}),
                      "refs/heads/main"],
                     capture_output=True, text=True).stdout.split("\\t")[0]
with (ctl / "runs.jsonl").open("a", encoding="utf-8") as log:
    log.write(json.dumps({{"gate": {gid!r}, "remote_tip": tip,
                          "base": os.environ.get({base_name!r}),
                          "head": os.environ.get({head_name!r})}}) + "\\n")
code = ctl / {gid!r}
sys.exit(int(code.read_text()) if code.exists() else 0)
"""


def gate_block(gid: str, remote_dir: str, *, blocking: bool,
               ci: bool = False) -> str:
    command = [sys.executable, "-c",
               GATE_SCRIPT.format(remote_dir=remote_dir, gid=gid,
                                  base_name=GATE_BASE, head_name=GATE_HEAD)]
    return (f"\n[[gates]]\nid = {json.dumps(gid)}\n"
            f"command = {json.dumps(command)}\n"
            f"blocking = {'true' if blocking else 'false'}\n"
            + ('attested_by = "ci"\n' if ci else ""))


class GatedScratch(Scratch):
    """`Scratch` (base <- change) with a committed gate manifest on top
    (<- the manifest) and the gates' control directory. `copied_instance`
    builds it once per process per manifest and remote shape and copies
    it: the manifest names no path (`GATE_SCRIPT`), so the template's
    commit is every copy's, id included."""

    def __init__(self, case, prefix, *, manifest: str, git_timeout=None,
                 **kw):
        super().__init__(case, prefix, **kw)
        self.ctl = self.root / "ctl"
        self.ctl.mkdir()
        self.write_config(git_timeout=git_timeout, gates=manifest)
        git(self.repo, "commit", "-qam", "the manifest")
        self.head = git(self.repo, "rev-parse", "HEAD")


def child_environment(state: Path, extra: dict | None = None):
    """THIS process's environment made the one `Scratch.run` gives its CLI
    child — `cli_env`'s, less every name a gate runner exports (the suite
    may itself run inside a gate, `IN_GATE_RUN` set), with `extra` on top —
    for the duration of an in-process row. `run_gates` reads the nesting
    marker and `gh`'s stub directory from here, and every git door and gate
    inherits it."""
    env = {k: v for k, v in cli_env(state).items() if k not in RUNNER_NAMES}
    env[env_var("STATE_DIR")] = str(state)
    env.update(extra or {})
    return mock.patch.dict(os.environ, env, clear=True)


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
        need their own repository, remote and ledger. A copy of the one
        `GatedScratch` built for this manifest and remote shape: author,
        bare remote, base <- change <- the manifest, hooks directory and
        the gates' control directory."""
        manifest = "".join(gate_block(gid, self.REMOTE_DIR,
                                      blocking=blocking, ci=ci)
                           for gid, blocking, ci in self.GATES)
        s = copied_instance(GatedScratch, self, self.PREFIX,
                            manifest=manifest + self.EXTRA_GATES,
                            git_timeout=self.LIMITS,
                            remote_dir=self.REMOTE_DIR,
                            url_form=self.URL_FORM)
        self.scratch, self.ctl = s, s.ctl
        return s

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

    # --------------------------------------------------------- in process

    def in_process(self, base: str, *, gates: bool = False,
                   verb: str = "handoff", env: dict | None = None,
                   seen: list | None = None) -> tuple[dict, list, object]:
        """The hand-off up to its emission, IN THIS PROCESS: the two
        functions `cli._emit` composes, called as it calls them for
        `<verb> --base <base>`, under `child_environment`. `emit.ensure_pushed`
        resolves the base, commits, reaches its `before_push` and pushes;
        the callback records what it was handed and the remote's tip at
        that moment into `seen` and — with `gates` — runs
        `emit.gate_before_push` exactly as the CLI's callback does (the
        governing manifest, the resolved base, the verb). Returns
        (record, seen, the local gates or None); raises what the two raise,
        `seen` then holding what the callback saw."""
        s = self.scratch
        seen = [] if seen is None else seen
        local = []

        def before_push(record: dict) -> None:
            seen.append((dict(record), self.remote_tip()))
            if gates:
                local.append(emit.gate_before_push(
                    record["governing"], record, base=record["base"],
                    verb=verb))

        with child_environment(s.state, env):
            cfg = config.load(s.repo, ledger_dir=str(s.state))
            record = emit.ensure_pushed(cfg, base=base,
                                        before_push=before_push)
        return record, seen, (local[0] if local else None)

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


class TestTheScratchIsACopyOfOneBuild(_Gated):
    """`fresh()` copies one built `GatedScratch` per manifest and remote
    shape: each copy's paths, remote URL, hooks and control directories are
    its own, its commits — the manifest's included — the template's, and a
    push from one reaches no other copy's remote, nor a gate's record
    another copy's counter.

    MUTATIONS: skip the path rebase in `copy_fixture` and the copy's
    `core.hooksPath` and origin name the template's directories; leave
    the attributes unmoved (`_moved` returning `attrs`) and `repo` is the
    template's."""

    def test_two_scratches_are_private_and_share_their_base(self):
        a = self.scratch
        b = self.fresh()
        self.assertNotEqual(a.root, b.root)
        self.assertEqual((a.base, a.head), (b.base, b.head))
        for s in (a, b):
            for attr in ("repo", "remote", "state", "hooks", "claim", "ctl"):
                self.assertTrue(str(getattr(s, attr)).startswith(
                    str(s.root) + os.sep), attr)
            self.assertIs(s.case, self)
            self.assertEqual(git(s.repo, "config", "core.hooksPath"),
                             str(s.hooks))
            self.assertEqual(git(s.repo, "remote", "get-url", "origin"),
                             s.url)
            self.assertIn(str(s.remote), s.url)
            self.assertEqual(git(s.remote, "rev-parse", "main"), s.base)
            # base <- change <- the manifest (the template's commit).
            self.assertEqual(git(s.repo, "rev-parse", "HEAD~2"), s.base)
            self.assertEqual(git(s.repo, "rev-parse", "HEAD"), s.head)
            self.assertEqual(git(s.repo, "status", "--porcelain"), "")
        git(a.repo, "push", "-q", "origin", "main")
        self.assertEqual(git(a.remote, "rev-parse", "main"), a.head)
        self.assertEqual(git(b.remote, "rev-parse", "main"), b.base)
        # A gate, run as a runner runs it — from the copy's repository —
        # records into that copy's counter and reads that copy's remote.
        [block] = [g for g in config.load(
            a.repo, ledger_dir=str(a.state)).gates if g["id"] == "block"]
        subprocess.run(block["command"], cwd=str(a.repo), check=True,
                       capture_output=True, timeout=60,
                       stdin=subprocess.DEVNULL)
        self.scratch, self.ctl = a, a.ctl
        self.assertEqual([(r["gate"], r["remote_tip"]) for r in self.runs()],
                         [("block", a.head)])
        self.assertFalse((b.ctl / "runs.jsonl").exists())


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
    """End to end, one hand-off per outcome: a red advisory gate pushes and
    emits; no derivable base refuses; and all green — one cold hand-off
    that carries three rows (every gate once, before the push, bound, in
    manifest order; the gates told the resolved id, not the spelling; then
    the same hand-off warm, served from the cache with no gate run and
    nothing pushed)."""

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
        """All green, cold: every gate runs once, before the push, and is
        told the id the emission binds, not the spelling the author typed
        (`--base <abbreviated>`): the base is resolved before the commit,
        and the gates now run before the push. Then the same hand-off
        again, warm: served from the cache — the same digest — with no gate
        run and nothing pushed (a `pre-push` counter)."""
        s = self.scratch
        tip = self.remote_tip()
        counter = s.root / "pre-push.count"
        s.hook("pre-push", sleeping_pre_push(0, counter=counter))
        argv = ("handoff", "--claim-file", str(s.claim),
                "--base", s.base[:10])
        code, first, stderr = s.run(*argv)
        self.assertEqual(code, 0, (first, stderr))
        runs = self.runs()
        self.assertEqual(sorted(r["gate"] for r in runs), ["advise", "block"])
        self.assertEqual({r["remote_tip"] for r in runs}, {tip})
        self.assertEqual({r["base"] for r in runs}, {s.base})
        self.assertEqual(self.remote_tip(), s.head)
        envelope = Path(first["kept"]).read_text(encoding="utf-8")
        self.assertEqual([r["id"] for r in self.attestations(envelope)],
                         ["block", "advise"])
        for record in self.attestations(envelope):
            self.assertEqual(record["binding"], "bound")
        pushes = counter.read_text().count("run")
        code, warm, stderr = s.run(*argv)
        self.assertEqual(code, 0, (warm, stderr))
        self.assertTrue(warm["cached"])
        self.assertEqual(warm["digest"], first["digest"])
        self.assertEqual(len(self.runs()), len(runs),
                         "a gate ran on a warm serve")
        self.assertEqual(counter.read_text().count("run"), pushes,
                         "the warm serve pushed")

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
    (`HEAD`, `HEAD~1`) — with and without outstanding tracked work. Every
    row: the base is the commit its expression named before the run, and
    that id is what the callback that runs the gates is handed, before the
    push.

    The rows that exercise a MOVE assert that they do: after the run their
    expression names another commit, so a second resolution would have
    found it. `origin/main` moves with the PUSH even when nothing was
    committed.

    WHERE THE ROWS RUN. The form decides one thing — which commit the
    expression names — and one function decides it: `emit.ensure_pushed`,
    which resolves the base once, before its own commit and push, and hands
    the id to the callback where the hand-off runs its gates. So the whole
    partition runs IN PROCESS against it (`in_process`: the real commit and
    the real push, a recording callback in the gates' place). What the id
    then reaches — every gate told it, `Base:`, the range gate's
    attestation, the shape, the validation — does not depend on the form,
    and is proven END TO END through the real CLI on the two kinds of move,
    one author verb each: `handoff` with the branch and outstanding work
    (it moves with the commit), `emit-request` with the remote-tracking ref
    and none (it moves with the push alone). `HEAD~1` with outstanding work
    runs end to end through both verbs in `TestTheWhitespaceReproduction`
    and in the control of
    `TestABaseThatNamesNoOneCommitRefusesBeforeTheCommit`.

    MUTATIONS. The finding's — forward the unresolved expression to the
    emission again — fails the end-to-end rows (it acts after the
    resolution the in-process rows stop at). Resolve the base after the
    hand-off's own commit rather than before it, and every in-process row
    that moves with the commit fails; drop the `^{commit}` peel, and the
    annotated tag's rows fail."""

    FORMS = ("full", "abbreviated", "branch", "remote-tracking",
             "lightweight tag", "annotated tag", "HEAD", "HEAD~1")
    #: Which forms name another commit after the run, by outstanding work.
    MOVES = {True: {"branch", "remote-tracking", "HEAD", "HEAD~1"},
             False: {"remote-tracking"}}
    #: End to end: (verb, form, outstanding work) — one move of each kind,
    #: one verb each.
    THROUGH_THE_CLI = (("handoff", "branch", True),
                       ("emit-request", "remote-tracking", False))

    def spelled(self, form: str) -> str:
        s = self.scratch
        parent = git(s.repo, "rev-parse", "HEAD~1")
        git(s.repo, "tag", "light", parent)
        git(s.repo, "tag", "-a", "-m", "annotated", "ann", parent)
        return {"full": parent, "abbreviated": parent[:10],
                "branch": "main", "remote-tracking": "origin/main",
                "lightweight tag": "light", "annotated tag": "ann",
                "HEAD": "HEAD", "HEAD~1": "HEAD~1"}[form]

    def assert_moves(self, form: str, outstanding: bool, spelled: str,
                     expected: str) -> None:
        after = git(self.scratch.repo, "rev-parse", "--verify",
                    f"{spelled}^{{commit}}")
        self.assertEqual(after != expected, form in self.MOVES[outstanding],
                         "the row does not exercise what it names")

    def test_the_partition_at_the_one_resolution(self):
        for outstanding in (True, False):
            for form in self.FORMS:
                with self.subTest(outstanding=outstanding, form=form):
                    s = self.fresh()
                    spelled = self.spelled(form)
                    expected = git(s.repo, "rev-parse", "--verify",
                                   f"{spelled}^{{commit}}")
                    tip = self.remote_tip()
                    if outstanding:
                        (s.repo / "f.txt").write_text(
                            "three\nfour\n", encoding="utf-8")
                    record, seen, _local = self.in_process(spelled)
                    target = git(s.repo, "rev-parse", "HEAD")
                    self.assertEqual(
                        (record["state"], record["sha"], record["committed"]),
                        ("pushed", target, outstanding))
                    self.assertEqual(record["base"], expected,
                                     "the base is what the expression named "
                                     "before the hand-off's own commit and "
                                     "push")
                    self.assertEqual(
                        [(handed["base"], handed["sha"], at)
                         for handed, at in seen], [(expected, target, tip)],
                        "the callback that runs the gates was not handed "
                        "that id, once, before the push")
                    self.assertEqual(self.remote_tip(), target)
                    self.assert_moves(form, outstanding, spelled, expected)

    def test_the_moves_through_the_real_cli(self):
        for verb, form, outstanding in self.THROUGH_THE_CLI:
            with self.subTest(verb=verb, form=form, outstanding=outstanding):
                s = self.fresh()
                spelled = self.spelled(form)
                expected = git(s.repo, "rev-parse", "--verify",
                               f"{spelled}^{{commit}}")
                if outstanding:
                    (s.repo / "f.txt").write_text("three\nfour\n",
                                                  encoding="utf-8")
                code, payload, stderr, envelope = self.verb(verb, spelled)
                self.assert_one_range(verb, code, payload, stderr, envelope,
                                      expected=expected)
                self.assert_moves(form, outstanding, spelled, expected)


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
    the hand-off refuses before the push and emits nothing.

    WHERE THE ROWS RUN. `HEAD~1` end to end through both author verbs: it
    is the reproduction. Of the four refusal rows (two spellings, two
    verbs), two run end to end — `handoff --base HEAD`, `emit-request
    --base B`, one spelling per verb — and the other two in process, where
    the refusal is decided (`in_process` with the gates: GatesRefused
    before the push, the verb's own command in its remedy)."""

    #: The refusal, end to end: (verb, spelling), each verb and each
    #: spelling once. The other pairing runs in process.
    REFUSED_THROUGH_THE_CLI = (("handoff", "HEAD"), ("emit-request", "B"))
    REFUSED_IN_PROCESS = (("handoff", "B"), ("emit-request", "HEAD"))

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

    def assert_judged_b_to_c(self, a: str, b: str, tip: str) -> None:
        """Refused before the push, over B..C: the remote untouched, C the
        commit the hand-off left on B, every gate told B..C, and the gate
        over that range red where A..C is clean."""
        s = self.scratch
        self.assertEqual(self.remote_tip(), tip)
        committed = git(s.repo, "rev-parse", "HEAD")
        self.assertEqual(git(s.repo, "rev-parse", "HEAD~1"), b)
        self.assertEqual({(r["base"], r["head"]) for r in self.runs()},
                         {(b, committed)})
        # The refusal agrees with the gate over the range it judged — and
        # that range is B..C, not A..C.
        self.assertNotEqual(self.rerun(b, committed), 0)
        self.assertEqual(self.rerun(a, committed), 0)

    def test_the_immutable_control_and_head_refuse_before_the_push(self):
        for verb, spelling in self.REFUSED_THROUGH_THE_CLI:
            with self.subTest(verb=verb, base=spelling):
                a, b = self.abc()
                tip = self.remote_tip()
                code, payload, stderr, envelope = self.verb(
                    verb, b if spelling == "B" else "HEAD")
                self.assert_refused_nothing_emitted(
                    verb, code, payload, stderr, envelope)
                self.assertEqual(code, 1, payload)
                self.assertEqual(payload["next_kind"], "blocked")
                self.assertIn("whitespace", payload["error"])
                self.assertIn(f"`{TOOL_NAME} {verb}`", payload["remedy"])
                self.assert_judged_b_to_c(a, b, tip)

    def test_the_other_pairing_refuses_in_process(self):
        for verb, spelling in self.REFUSED_IN_PROCESS:
            with self.subTest(verb=verb, base=spelling):
                a, b = self.abc()
                tip = self.remote_tip()
                seen = []
                with self.assertRaises(emit.GatesRefused) as caught:
                    self.in_process(b if spelling == "B" else "HEAD",
                                    gates=True, verb=verb, seen=seen)
                refusal = caught.exception
                self.assertIn("whitespace", str(refusal))
                self.assertIn("Nothing was pushed, emitted or recorded",
                              str(refusal))
                self.assertIn(f"`{TOOL_NAME} {verb}`", refusal.remedy)
                self.assertEqual([handed["base"] for handed, _at in seen],
                                 [b])
                self.assertEqual(refusal.sha,
                                 git(self.scratch.repo, "rev-parse", "HEAD"))
                self.assert_judged_b_to_c(a, b, tip)


class TestABaseThatNamesNoOneCommitRefusesBeforeTheCommit(_Ranged):
    """A base that does not resolve to exactly one commit — absent, past
    the root, a RANGE, option-shaped, empty, a tree — refuses before
    anything is committed, gated, pushed or emitted: the refs, index,
    status and files byte-identical. Before the fix a range or an
    option-shaped value resolved to SEVERAL lines, and a tree to a tree:
    each was committed and gated, and the tree was pushed. Mutations: drop
    `--verify`, or the `^{commit}` peel, and those rows go red.

    WHERE THE ROWS RUN. The refusal is `emit.ensure_pushed`'s, before its
    commit, and the spelling is all that varies, so every spelling runs IN
    PROCESS against it (`in_process`, gates on): the refusal, the
    repository byte-identical, the remote untouched, the gate callback
    never reached — beside the paired control, the same scratch with a
    resolvable base, which reaches it and gates. END TO END each author
    verb keeps one row, spelled as argparse must hand over as a VALUE
    rather than parse — `handoff --base=--all` (option-shaped),
    `emit-request --base=` (empty) — with the exit code and the blocked
    payload, and the control through both verbs."""

    BASES = ("nosuch", "HEAD~50", "HEAD~1..HEAD", "--all", "",
             "HEAD^{tree}")
    #: End to end: (verb, spelling), one per verb.
    THROUGH_THE_CLI = (("handoff", "--all"), ("emit-request", ""))

    def outstanding(self) -> tuple[Scratch, dict, str]:
        """A fresh scratch with outstanding tracked work, settled: (the
        scratch, its snapshot, the remote's tip)."""
        s = self.fresh()
        (s.repo / "f.txt").write_text("outstanding\n", encoding="utf-8")
        Scratch.settle(s.repo)
        return s, Scratch.snapshot(s.repo), self.remote_tip()

    def assert_untouched(self, before: dict, tip: str) -> None:
        self.assertEqual(Scratch.snapshot(self.scratch.repo), before)
        self.assertEqual(self.runs(), [])
        self.assertEqual(self.remote_tip(), tip)

    def test_every_unresolvable_spelling_in_process(self):
        for base in self.BASES:
            with self.subTest(base=base):
                _s, before, tip = self.outstanding()
                seen = []
                with self.assertRaises(RuntimeError) as caught:
                    self.in_process(base, gates=True, seen=seen)
                self.assertNotIsInstance(caught.exception, emit.GatesRefused)
                self.assertIn("does not name one commit",
                              str(caught.exception))
                self.assertIn("nothing has been committed",
                              str(caught.exception))
                self.assertEqual(seen, [], "the gate callback was reached")
                self.assert_untouched(before, tip)
        with self.subTest(control="HEAD~1"):
            s, _before, _tip = self.outstanding()
            record, seen, local = self.in_process("HEAD~1", gates=True)
            self.assertEqual(record["base"],
                             git(s.repo, "rev-parse", "HEAD~2"))
            self.assertEqual(len(seen), 1)
            self.assertEqual([r["id"] for r in local.records],
                             ["block", "whitespace"])
            self.assertEqual(self.remote_tip(), record["sha"])

    def test_one_spelling_per_verb_through_the_real_cli(self):
        for verb, base in self.THROUGH_THE_CLI:
            with self.subTest(verb=verb, base=base):
                _s, before, tip = self.outstanding()
                code, payload, stderr, envelope = self.verb(verb, base)
                self.assert_refused_nothing_emitted(
                    verb, code, payload, stderr, envelope)
                self.assertEqual(code, 2, payload)
                self.assertIn("does not name one commit", payload["error"])
                self.assertIn("nothing has been committed", payload["error"])
                self.assert_untouched(before, tip)

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

#: The reviewer's range gate, verbatim: told no base, it falls back to the
#: last commit — which is exactly the range CI, told no base, checks.
FALLBACK_RANGE_GATE = ["sh", "-c",
                       f'git diff --check "${{{GATE_BASE}:-HEAD~1}}" '
                       f'"${GATE_HEAD}"']


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

    def ci_receipt(self, head: str, *, through_the_runner: bool = False
                   ) -> dict:
        """CI's receipt for `head`, made the way CI makes it: the runner
        inside CI (`GITHUB_ACTIONS`), with NO review base — the workflow
        passes none — then one row per declared gate. `through_the_runner`:
        where the workbench's own entry point exists it runs too, from
        inside the scratch repository as CI runs it from inside the
        checkout, with `--receipt`; its rows must be the in-process ones,
        and its receipt is the one served."""
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
        if not through_the_runner or not self.RUNNER.is_file():
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
    disagreement is the base, not the mechanism.

    WHERE THE ROWS RUN. Both bases IN PROCESS, through the hand-off's own
    composition up to the envelope: `in_process` with the gates (the local
    half at the commit, told the base, then the push), then
    `emit.run_gates` carrying that half and awaiting CI's receipt from the
    fake `gh`, as `emit_request` calls it — the attestation the envelope
    would carry. END TO END, one row per author verb, one base each
    (`handoff --base A`, `emit-request --base B`), the envelope read back.
    CI's receipt is made in process on every row; the workbench's CI entry
    point runs once, on the `handoff --base A` row, and must produce the
    same receipt."""

    #: End to end: (verb, base); the runner runs on the first.
    THROUGH_THE_CLI = (("handoff", "A"), ("emit-request", "B"))

    def base_less_receipt(self, c: str, *, through_the_runner=False) -> dict:
        receipt = self.ci_receipt(c, through_the_runner=through_the_runner)
        keys = set(receipt) | {k for r in receipt["gates"] for k in r}
        self.assertEqual([k for k in keys if "base" in k], [],
                         "a CI receipt carries no review base")
        [row] = [r for r in receipt["gates"] if r["id"] == "whitespace"]
        self.assertEqual((row["exit_code"], row["not_run"]), (0, None))
        return receipt

    def assert_the_limit(self, name: str, a: str, b: str, c: str,
                         ws: dict) -> None:
        """The attestation CI's evidence gives the range gate, against the
        gate itself over each range."""
        self.assertEqual((ws["attested_by"], ws["binding"], ws["exit_code"]),
                         ("ci", "bound", 0))
        # What CI's evidence is about: its own base-less range.
        self.assertEqual(self.rerun(None, c), 0)
        self.assertEqual(self.rerun(b, c), 0)
        # What the request names: A..C for row A, where the same gate is
        # red — the limit, in one line.
        self.assertEqual(self.rerun({"A": a, "B": b}[name], c),
                         2 if name == "A" else 0)
        self.assertEqual(self.rerun(a, c), 2)

    def test_the_reviewers_abc_ci_attested(self):
        for row, (verb, name) in enumerate(self.THROUGH_THE_CLI):
            with self.subTest(verb=verb, base=name):
                a, b, c = self.abc(ci=True)
                base = {"A": a, "B": b}[name]
                self.serve(c, self.base_less_receipt(
                    c, through_the_runner=row == 0))
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
                self.assert_the_limit(name, a, b, c, by_id["whitespace"])

    def test_both_bases_in_process(self):
        for name in ("A", "B"):
            with self.subTest(base=name):
                a, b, c = self.abc(ci=True)
                base = {"A": a, "B": b}[name]
                self.serve(c, self.base_less_receipt(c))
                since, tip = len(self.runs()), self.remote_tip()
                record, seen, local = self.in_process(base, gates=True,
                                                      env=self.env)
                self.assertEqual((record["state"], record["sha"],
                                  record["base"]), ("pushed", c, base))
                self.assertEqual(self.remote_tip(), c)
                self.assertEqual([at for _handed, at in seen], [tip],
                                 "the local half ran after the push")
                # The one base reached the gate the hand-off ran.
                self.assertEqual(self.told(since), [("block", base, c)])
                with child_environment(self.scratch.state, self.env):
                    records = emit.run_gates(record["governing"], c,
                                             base=record["base"],
                                             prior=local)
                self.assertEqual([r["id"] for r in records],
                                 ["block", "whitespace"])
                self.assertEqual(self.told(since), [("block", base, c)],
                                 "the local half ran twice")
                by_id = {r["id"]: r for r in records}
                self.assert_the_limit(name, a, b, c, by_id["whitespace"])


class TestTheSameGateRunLocallyRefusesTheEmittedRange(_CIRange):
    """THE LOCAL CONTROL: the identical command, not CI-attested, over the
    identical A/B/C. The hand-off tells it the one base, so with
    `--base A` it checks A..C, is red, and the hand-off refuses BEFORE the
    push: the remote's refs, the repository (refs, index, status, files)
    and the ledger byte-identical, nothing emitted. With `--base B` it
    checks B..C, is green, and the hand-off emits — the gate is live, not
    red by construction.

    WHERE THE ROWS RUN. Each outcome end to end through one author verb —
    the refusal through `handoff`, the control through `emit-request` —
    and the other verb's row of each IN PROCESS (`in_process` with the
    gates): the refusal as GatesRefused before the push, naming the range
    gate and the verb's own command; the control as the local half the
    envelope would carry, pushed."""

    def refused_state(self) -> tuple:
        """(repository snapshot, remote snapshot, ledger bytes or None,
        the remote's tip, the recorder's count) before a refused run."""
        s = self.scratch
        Scratch.settle(s.repo)
        ledger = s.state / "ledger.jsonl"
        return (Scratch.snapshot(s.repo), Scratch.snapshot(s.remote,
                                                           bare=True),
                ledger.read_bytes() if ledger.is_file() else None,
                self.remote_tip(), len(self.runs()))

    def assert_nothing_moved(self, before: tuple, a: str, b: str,
                             c: str) -> None:
        s = self.scratch
        repo, remote, recorded, _tip, since = before
        ledger = s.state / "ledger.jsonl"
        self.assertEqual(Scratch.snapshot(s.repo), repo)
        self.assertEqual(Scratch.snapshot(s.remote, bare=True), remote)
        self.assertEqual(ledger.read_bytes() if ledger.is_file() else None,
                         recorded)
        self.assertEqual(self.told(since), [("block", a, c)])
        self.assertEqual(self.rerun(a, c), 2)
        self.assertEqual(self.rerun(b, c), 0)

    def test_the_bad_emitted_range_refuses_before_the_push(self):
        verb = "handoff"
        with self.subTest(verb=verb):
            a, b, c = self.abc(ci=False)
            before = self.refused_state()
            code, payload, stderr, envelope = self.verb(verb, a)
            self.assert_refused_before_push(
                code, payload, stderr, failed=["whitespace"], tip=before[3])
            self.assertIsNone(envelope)
            self.assertEqual(self.emitted(verb), 0)
            self.assertIn(f"no commit was made (HEAD is {c})",
                          payload["error"])
            self.assertIn("trailing whitespace", Path(
                payload["gate_output"]["whitespace"]).read_text(
                    encoding="utf-8"))
            self.assert_nothing_moved(before, a, b, c)

    def test_control_the_range_that_is_clean_emits(self):
        verb = "emit-request"
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
            self.assertEqual((ws["binding"], ws["exit_code"]), ("bound", 0))

    def test_the_other_verb_of_each_in_process(self):
        with self.subTest(verb="emit-request", base="A"):
            a, b, c = self.abc(ci=False)
            before = self.refused_state()
            seen = []
            with self.assertRaises(emit.GatesRefused) as caught:
                self.in_process(a, gates=True, verb="emit-request",
                                env=self.env, seen=seen)
            refusal = caught.exception
            self.assertEqual({i.code for i in refusal.items}, {"A-FAILED"})
            self.assertEqual(set(refusal.outputs), {"whitespace"})
            self.assertIn("whitespace", str(refusal))
            self.assertIn("Nothing was pushed, emitted or recorded",
                          str(refusal))
            self.assertIn(f"no commit was made (HEAD is {c})", str(refusal))
            self.assertIn(f"`{TOOL_NAME} emit-request`", refusal.remedy)
            self.assertIn("trailing whitespace", Path(
                refusal.outputs["whitespace"]).read_text(encoding="utf-8"))
            self.assertEqual([(handed["base"], at) for handed, at in seen],
                             [(a, before[3])])
            self.assertEqual(self.remote_tip(), before[3])
            self.assert_nothing_moved(before, a, b, c)
        with self.subTest(verb="handoff", base="B"):
            a, b, c = self.abc(ci=False)
            record, _seen, local = self.in_process(b, gates=True,
                                                   env=self.env)
            self.assertEqual((record["state"], record["base"]),
                             ("pushed", b))
            self.assertEqual(self.remote_tip(), c)
            ws = {r["id"]: r for r in local.records}["whitespace"]
            self.assertNotIn("attested_by", ws)
            self.assertEqual((ws["binding"], ws["exit_code"]), ("bound", 0))


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


#: A blocking gate that records the bytes it read at `f.txt` (or ABSENT)
#: into the control directory and passes: what a gate judged, kept beside
#: what the commit holds.
DISK_SCRIPT = ("import pathlib; root = pathlib.Path.cwd(); f = root / 'f.txt'; "
               "(root.parent / 'ctl' / 'disk.txt').write_bytes("
               "f.read_bytes() if f.exists() else b'ABSENT')")
DISK_GATE = (f"\n[[gates]]\nid = \"disk\"\n"
             f"command = {json.dumps([sys.executable, '-c', DISK_SCRIPT])}\n"
             f"blocking = true\n")

#: Each flag state as the update-index calls that make it: one flag per
#: call, since one call applies only one of the two (measured, git 2.54).
FLAGS = {"assume-unchanged": (["--assume-unchanged"],),
         "skip-worktree": (["--skip-worktree"],),
         "both": (["--assume-unchanged"], ["--skip-worktree"])}


class TestWhatTheIndexHidesIsNotBound(_Gated):
    """Round-2 F1 of lineage L4746274c96, loupe's own half. `git status`
    trusts an index entry flagged assume-unchanged or skip-worktree, so a
    flagged file edited on disk left the status clean, every gate read the
    edit, and `run_gates` recorded `bound` to a commit that does not hold
    those bytes: a hand-off pushed and emitted it. `emit.index_hidden` now
    makes every record `unbound`, naming the paths, whenever an entry is
    flagged (a sparse checkout's omitted paths included), and the
    validator's A-UNBOUND stops the hand-off before its push.

    END TO END through `python3 -m review handoff`: each flag state, with
    the flagged file's disk bytes differing from and matching HEAD's, and
    a sparse checkout, each refused before the push with no commit made;
    the controls are the same edit with the flag cleared (committed,
    pushed, emitted) and the untouched scratch. IN PROCESS on `run_gates`:
    the binding's wording, the bound on named paths, `pending_tree` not
    overriding it, and the guard mutated (`index_hidden` answering "")
    reproducing the defect: `bound` over a flagged edit.

    MUTATION (manual, the CLI rows; recorded in the round-3 disposition):
    `elif hidden:` weakened to `elif False:` in a copy of review/emit.py
    sends every refusal row red, the hand-off pushing and emitting."""

    EXTRA_GATES = DISK_GATE

    def flag(self, s, how: str) -> None:
        for args in FLAGS[how]:
            git(s.repo, "update-index", *args, "f.txt")

    def disk(self) -> bytes:
        return (self.ctl / "disk.txt").read_bytes()

    def refused(self, s, tip, code, payload, stderr) -> str:
        self.assert_refused_before_push(code, payload, stderr,
                                        failed=["block", "disk"], tip=tip)
        self.assertIn(f"no commit was made (HEAD is {s.head})",
                      payload["error"])
        self.assertEqual(git(s.repo, "rev-parse", "HEAD"), s.head)
        unbound = [i["message"] for i in payload["items"]
                   if i["code"] == "A-UNBOUND"]
        self.assertTrue(unbound, payload["items"])
        return "\n".join(unbound)

    def test_a_flagged_entry_refuses_the_hand_off_before_its_push(self):
        for how in FLAGS:
            for disk in ("differs", "matches"):
                with self.subTest(flag=how, disk=disk):
                    s = self.fresh()
                    tip = self.remote_tip()
                    if disk == "differs":
                        (s.repo / "f.txt").write_text("edited\n",
                                                      encoding="utf-8")
                    self.flag(s, how)
                    # The premise: git's own status sees nothing.
                    self.assertEqual(git(s.repo, "status", "--porcelain"),
                                     "")
                    code, payload, stderr = s.handoff()
                    text = self.refused(s, tip, code, payload, stderr)
                    self.assertIn("the index hides 1 tracked path(s)", text)
                    state = ("assume-unchanged and skip-worktree"
                             if how == "both" else how)
                    self.assertIn(f"'f.txt' ({state})", text)
                    if disk == "differs":
                        # What the gate judged is not what HEAD holds.
                        self.assertEqual(self.disk(), b"edited\n")
                        self.assertEqual(git(s.repo, "show", "HEAD:f.txt"),
                                         "two")

    def test_a_sparse_checkout_refuses_the_hand_off_before_its_push(self):
        s = self.scratch
        tip = self.remote_tip()
        # A copied scratch's index carries the template's stat data, and
        # sparse-checkout keeps a file it cannot prove unmodified.
        Scratch.settle(s.repo)
        git(s.repo, "sparse-checkout", "set", "--no-cone", "/*", "!/f.txt")
        self.assertFalse((s.repo / "f.txt").exists())
        code, payload, stderr = s.handoff()
        text = self.refused(s, tip, code, payload, stderr)
        self.assertIn("the index hides 1 tracked path(s)", text)
        self.assertIn("'f.txt' (skip-worktree)", text)
        self.assertEqual(self.disk(), b"ABSENT")

    def test_control_the_flag_cleared_the_edit_is_committed_and_pushed(self):
        s = self.scratch
        (s.repo / "f.txt").write_text("edited\n", encoding="utf-8")
        self.flag(s, "both")
        git(s.repo, "update-index", "--no-assume-unchanged", "f.txt")
        git(s.repo, "update-index", "--no-skip-worktree", "f.txt")
        code, payload, stderr = s.handoff()
        self.assertEqual(code, 0, (payload, stderr))
        committed = git(s.repo, "rev-parse", "HEAD")
        self.assertNotEqual(committed, s.head)
        self.assertEqual(self.remote_tip(), committed)
        self.assertEqual(git(s.repo, "show", "HEAD:f.txt"), "edited")
        self.assertEqual(self.disk(), b"edited\n")
        self.assertEqual(len(self.requests()), 1)

    def test_control_nothing_hidden_pushes_and_emits(self):
        s = self.scratch
        code, payload, stderr = s.handoff()
        self.assertEqual(code, 0, (payload, stderr))
        self.assertEqual(self.remote_tip(), s.head)

    # ---------------------------------------------------------- in process

    def binding(self, s, **kw) -> str:
        with child_environment(s.state):
            cfg = config.load(s.repo, ledger_dir=str(s.state))
            records = emit.run_gates(cfg, git(s.repo, "rev-parse", "HEAD"),
                                     **kw)
        bindings = {r["binding"] for r in records}
        self.assertEqual(len(bindings), 1, bindings)
        return bindings.pop()

    def test_the_binding_names_five_paths_and_counts_the_rest(self):
        s = self.scratch
        names = [f"p{i}.txt" for i in range(7)]
        for name in names:
            (s.repo / name).write_text(name, encoding="utf-8")
        git(s.repo, "add", *names)
        git(s.repo, "commit", "-qm", "seven")
        git(s.repo, "update-index", "--assume-unchanged", *names)
        binding = self.binding(s)
        self.assertTrue(binding.startswith(
            "unbound: the index hides 7 tracked path(s) from git's "
            "comparison ('p0.txt' (assume-unchanged), "), binding)
        self.assertIn("'p4.txt' (assume-unchanged) and 2 more), so the "
                      "executed tree cannot be shown to be the target "
                      "commit's content", binding)
        self.assertNotIn("p5.txt", binding)

    def test_a_pending_tree_does_not_bind_what_the_index_hides(self):
        s = self.scratch
        (s.repo / "f.txt").write_text("edited\n", encoding="utf-8")
        self.flag(s, "skip-worktree")
        tree = git(s.repo, "rev-parse", "HEAD^{tree}")
        binding = self.binding(s, pending_tree=tree)
        self.assertTrue(binding.startswith("unbound: the index hides 1 "),
                        binding)
        # The control: the flag cleared, the same pending tree binds.
        git(s.repo, "update-index", "--no-skip-worktree", "f.txt")
        git(s.repo, "checkout", "--", "f.txt")
        self.assertTrue(self.binding(s, pending_tree=tree).startswith(
            "bound to the pending commit's tree"))

    def test_the_guards_mutated_bind_a_flagged_edit(self):
        """`index_hidden` answering "" and `tree_differs` answering [] is
        the pre-fix runner: the flagged edit reaches the gate and the
        record says `bound`. Either guard alone still refuses it: the flag
        names it, and the cold index (round 3) reads its bytes."""
        s = self.scratch
        (s.repo / "f.txt").write_text("edited\n", encoding="utf-8")
        self.flag(s, "assume-unchanged")
        self.assertTrue(self.binding(s).startswith("unbound: the index "))
        with mock.patch.object(emit, "index_hidden", return_value=""):
            self.assertIn("differ from the target commit's content",
                          self.binding(s))
            with mock.patch.object(emit, "tree_differs", return_value=[]):
                self.assertEqual(self.binding(s), "bound")
        self.assertEqual(self.disk(), b"edited\n")


#: A filesystem monitor (hook version 2) that reports nothing changed:
#: a monitor that missed an edit. `ACCURATE_MONITOR` reports everything.
STALE_MONITOR = '#!/bin/sh\nprintf "token\\0"\n'
ACCURATE_MONITOR = '#!/bin/sh\nprintf "token\\0/\\0"\n'


class TestWhatAnyCacheHidesIsNotBound(_Gated):
    """Round-3 F1 of lineage L4746274c96. Round 2's fix read the flags
    `git ls-files -v` shows, and a filesystem monitor's valid bit is not
    one of them (`ls-files -f` shows it); trusted stat data
    (`core.trustctime=false` and a same-size edit with its time restored)
    is not in the index at all. Either left `git status` clean over an
    edit the gate read, so `run_gates` recorded `bound` and a hand-off
    pushed and emitted a commit holding the earlier bytes. Now the
    working tree is compared with the tree being bound through a cold
    index (`emit.tree_differs`), so no cache answers for the disk.

    END TO END through `python3 -m review handoff`, each refused before
    the push with the remote unchanged: a stale monitor over an edit, over
    a deletion, and over an edit beside staged outstanding work (the
    hand-off commits that work, names its commit and pushes nothing); and
    trusted stat data over a same-size edit. Controls, each pushing and
    emitting: the stale monitor with nothing edited; an accurate monitor
    over the edit (committed and pushed); the stale monitor with the
    edit's valid bit cleared, the remedy (committed and pushed). IN
    PROCESS on `run_gates`: the wording against the target and against a
    pending tree, and the guard mutated (`tree_differs` answering [])
    binding the stale edit.

    MUTATION (manual, the CLI rows; recorded in the commit): `elif
    differ:` weakened to `elif False:` in a copy of review/emit.py sends
    every refusal row red, the hand-off pushing and emitting."""

    EXTRA_GATES = DISK_GATE

    def monitor(self, s, script: str = STALE_MONITOR) -> None:
        hook = s.root / "monitor"
        hook.write_text(script, encoding="utf-8")
        hook.chmod(0o755)
        git(s.repo, "config", "core.fsmonitor", str(hook))
        git(s.repo, "config", "core.fsmonitorHookVersion", "2")

    def stale(self, s, edit: str | None) -> None:
        """f.txt cached as monitor-valid, then `edit`ed (None deletes)."""
        self.monitor(s)
        git(s.repo, "status", "--porcelain")
        git(s.repo, "update-index", "--fsmonitor-valid", "--", "f.txt")
        if edit is None:
            (s.repo / "f.txt").unlink()
        else:
            (s.repo / "f.txt").write_text(edit, encoding="utf-8")
        # The premise: the monitor's valid bit, which -v does not show, and
        # a status that sees nothing.
        self.assertEqual(git(s.repo, "ls-files", "-f", "--", "f.txt"),
                         "h f.txt")
        self.assertEqual(git(s.repo, "ls-files", "-v", "--", "f.txt"),
                         "H f.txt")
        self.assertEqual(git(s.repo, "status", "--porcelain", "--", "f.txt"),
                         "")

    def disk(self) -> bytes:
        return (self.ctl / "disk.txt").read_bytes()

    def refused(self, s, tip, code, payload, stderr, *, committed=None):
        self.assert_refused_before_push(code, payload, stderr,
                                        failed=["block", "disk"], tip=tip)
        head = git(s.repo, "rev-parse", "HEAD")
        if committed is None:
            self.assertIn(f"no commit was made (HEAD is {s.head})",
                          payload["error"])
            self.assertEqual(head, s.head)
        else:
            self.assertIn(f"committed its outstanding work locally at "
                          f"{head}", payload["error"])
        text = "\n".join(i["message"] for i in payload["items"]
                         if i["code"] == "A-UNBOUND")
        self.assertIn("1 tracked path(s) on disk differ from the target "
                      "commit's content ('f.txt') when read through a cold "
                      "index", text)
        self.assertEqual(git(s.repo, "show", "HEAD:f.txt"), "two")

    def test_a_stale_monitor_refuses_the_hand_off_before_its_push(self):
        for label, edit in (("an edit", "edited\n"), ("a deletion", None)):
            with self.subTest(disk=label):
                s = self.fresh()
                tip = self.remote_tip()
                self.stale(s, edit)
                code, payload, stderr = s.handoff()
                self.refused(s, tip, code, payload, stderr)
                self.assertEqual(self.disk(),
                                 b"edited\n" if edit else b"ABSENT")

    def test_a_stale_monitor_beside_staged_work_commits_it_and_refuses(self):
        s = self.scratch
        tip = self.remote_tip()
        (s.repo / "g.txt").write_text("staged\n", encoding="utf-8")
        git(s.repo, "add", "g.txt")
        self.stale(s, "edited\n")
        code, payload, stderr = s.handoff()
        self.refused(s, tip, code, payload, stderr, committed=True)
        self.assertEqual(git(s.repo, "show", "HEAD:g.txt"), "staged")

    def test_trusted_stat_data_refuses_the_hand_off_before_its_push(self):
        s = self.scratch
        tip = self.remote_tip()
        git(s.repo, "config", "core.trustctime", "false")
        Scratch.settle(s.repo)
        path = s.repo / "f.txt"
        then = path.stat()
        path.write_text("TWO\n", encoding="utf-8")       # the same size
        os.utime(path, ns=(then.st_atime_ns, then.st_mtime_ns))
        self.assertEqual(git(s.repo, "status", "--porcelain"), "")
        code, payload, stderr = s.handoff()
        self.refused(s, tip, code, payload, stderr)
        self.assertEqual(self.disk(), b"TWO\n")

    def test_control_a_stale_monitor_and_nothing_edited_pushes(self):
        s = self.scratch
        self.stale(s, "two\n")
        code, payload, stderr = s.handoff()
        self.assertEqual(code, 0, (payload, stderr))
        self.assertEqual(self.remote_tip(), s.head)

    def test_control_an_accurate_monitor_commits_and_pushes_the_edit(self):
        s = self.scratch
        self.monitor(s, ACCURATE_MONITOR)
        git(s.repo, "status", "--porcelain")
        (s.repo / "f.txt").write_text("edited\n", encoding="utf-8")
        code, payload, stderr = s.handoff()
        self.assertEqual(code, 0, (payload, stderr))
        committed = git(s.repo, "rev-parse", "HEAD")
        self.assertNotEqual(committed, s.head)
        self.assertEqual(self.remote_tip(), committed)
        self.assertEqual(git(s.repo, "show", "HEAD:f.txt"), "edited")

    def test_control_the_valid_bit_cleared_commits_and_pushes_the_edit(self):
        s = self.scratch
        self.stale(s, "edited\n")
        git(s.repo, "update-index", "--no-fsmonitor-valid", "--", "f.txt")
        code, payload, stderr = s.handoff()
        self.assertEqual(code, 0, (payload, stderr))
        committed = git(s.repo, "rev-parse", "HEAD")
        self.assertEqual(self.remote_tip(), committed)
        self.assertEqual(git(s.repo, "show", "HEAD:f.txt"), "edited")

    # ---------------------------------------------------------- in process

    def binding(self, s, **kw) -> str:
        return TestWhatTheIndexHidesIsNotBound.binding(self, s, **kw)

    def test_the_binding_names_the_path_against_the_target_or_the_pending_tree(self):
        s = self.scratch
        self.stale(s, "edited\n")
        self.assertTrue(self.binding(s).startswith(
            "unbound: 1 tracked path(s) on disk differ from the target "
            "commit's content ('f.txt') when read through a cold index, "
            "though the index's own view showed no change"))
        tree = git(s.repo, "rev-parse", "HEAD^{tree}")
        self.assertTrue(self.binding(s, pending_tree=tree).startswith(
            f"unbound: 1 tracked path(s) on disk differ from the pending "
            f"commit's tree {tree} ('f.txt')"))

    def test_the_guard_mutated_binds_a_stale_monitors_edit(self):
        s = self.scratch
        self.stale(s, "edited\n")
        with mock.patch.object(emit, "tree_differs", return_value=[]):
            self.assertEqual(self.binding(s), "bound")
        self.assertEqual(self.disk(), b"edited\n")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

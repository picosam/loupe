"""§9bis.3 / RVW-T9: the transport loop end to end.

One integration class runs the whole loop for real — handoff -> take (second
clone, fetch over a bare path remote) -> verdict -> close -> next handoff —
because a first real execution is a different instrument from unit tests
(slice-1 lesson 3); it self-skips, with the reason stated, where filesystem
writes are denied.

Split from `test_transport.py` 2026-08-31; the classes are verbatim.
"""

import contextlib
import dataclasses
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from review import cli, config, transport, validate
from review.emit import _git
from review.ledger import Ledger
from review.tests.util import REPO_ROOT
from review.tests._transport_fixtures import (
    CFG, verdict_text)

class TestFullLoopIntegration(unittest.TestCase):
    """The first-real-execution instrument: two clones, a bare path remote,
    no network. handoff in the author clone → take in the reviewer clone
    (fetching over the stamped path) → verdict → close → the next handoff
    → lineage close → round 1 again with the repo default cap."""

    def setUp(self):
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="loop-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc}): the loop "
                          f"integration runs only in the writable pass")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.remote = self.tmp / "remote.git"
        self.author = self.tmp / "author"
        self.reviewer = self.tmp / "reviewer"
        # The bare remote names its initial branch too. Without `-b`, HEAD
        # takes git's built-in default (`master`); the author pushes `main`,
        # so the remote's HEAD points at a branch that never exists and
        # `git clone` below yields an EMPTY working tree — no review.toml,
        # so `take` refuses with T-UNDECLARED. Invisible on a machine whose
        # system gitconfig sets init.defaultBranch=main (Apple's git does);
        # the first CI run of this suite (2026-08-17) found it.
        self._sh("git", "init", "-q", "--bare", "-b", "main",
                 str(self.remote))
        self._sh("git", "init", "-q", "-b", "main", str(self.author))
        for k, v in (("user.name", "a"), ("user.email", "a@example.invalid"),
                     ("commit.gpgsign", "false")):
            self._sh("git", "-C", str(self.author), "config", k, v)
        toml = (REPO_ROOT / "review.toml").read_text(encoding="utf-8")
        toml = toml[:toml.index("[[gates]]")] + toml[toml.index("[roles]"):]
        (self.author / "review.toml").write_text(toml, encoding="utf-8")
        (self.author / "f.txt").write_text("one\n", encoding="utf-8")
        self._sh("git", "-C", str(self.author), "add", ".")
        self._sh("git", "-C", str(self.author), "commit", "-q", "-m", "init")
        self.base = _git(self.author, "rev-parse", "HEAD")
        (self.author / "f.txt").write_text("two\n", encoding="utf-8")
        self._sh("git", "-C", str(self.author), "commit", "-qam", "change")
        self._sh("git", "-C", str(self.author), "remote", "add", "origin",
                 str(self.remote))
        self._sh("git", "-C", str(self.author), "push", "-q", "-u", "origin",
                 "main")
        self._sh("git", "clone", "-q", str(self.remote), str(self.reviewer))
        self.claim = self.tmp / "claim.json"
        self.claim.write_text(json.dumps({
            "objective": "loop test",
            "references": [{"path": "review.toml", "required": True}]}),
            encoding="utf-8")
        self.author_state = self.tmp / "state-author"
        self.reviewer_state = self.tmp / "state-reviewer"
        self.cwd = os.getcwd()
        self.addCleanup(os.chdir, self.cwd)

    def _sh(self, *args):
        subprocess.run(args, check=True, capture_output=True, text=True,
                       timeout=60)

    def _run(self, where, state, *argv):
        os.chdir(where)
        from io import StringIO
        import contextlib
        buf = StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["--ledger-dir", str(state), *argv])
        os.chdir(self.cwd)
        out = buf.getvalue()
        try:
            return code, json.loads(out)
        except json.JSONDecodeError:
            return code, out

    def test_the_loop_end_to_end(self):
        head = _git(self.author, "rev-parse", "HEAD")
        # 1. handoff (author): emits round 1, records, keeps.
        code, rec = self._run(self.author, self.author_state, "handoff",
                              "--claim-file", str(self.claim),
                              "--base", self.base)
        self.assertEqual(code, 0, rec)
        self.assertEqual(rec["sha"], head)
        self.assertFalse(rec["cached"])
        self.assertTrue(Path(rec["kept"]).is_file())
        # 1b. handoff again on the unchanged tip: cached, gates not re-run.
        code, again = self._run(self.author, self.author_state, "handoff",
                                "--claim-file", str(self.claim),
                                "--base", self.base)
        self.assertEqual(code, 0, again)
        self.assertTrue(again["cached"])
        self.assertEqual(again["digest"], rec["digest"])
        # 2. take (reviewer clone): fetches over the stamped path remote.
        #    --as is mandatory since round 1 F4 — the loop no longer runs on a
        #    defaulted identity, so the real path must declare one too.
        code, taken = self._run(self.reviewer, self.reviewer_state, "take",
                                rec["kept"], "--as", "codex")
        self.assertEqual(code, 0, taken)
        self.assertEqual(taken["sha"], head)
        self.assertIn("fetched", taken["target"]["fetch"])
        self.assertEqual(taken["target"]["target"], "present")
        self.assertTrue(taken["references"][0]["status"].startswith("checked"))
        self.assertIn(f"diff {self.base}...{head}", taken["diff"])
        # 2b. taking it as the wrong identity refuses (exit 1, next given).
        code, wrong = self._run(self.reviewer, self.reviewer_state, "take",
                                rec["kept"], "--as", "claude")
        self.assertEqual(code, 1)
        self.assertIn("next", wrong)
        # 3. verdict → close (author): round 1 closed, lineage open.
        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head), encoding="utf-8")
        code, closed = self._run(self.author, self.author_state, "close",
                                 "--verdict", str(v1))
        self.assertEqual(code, 0, closed)
        self.assertEqual(closed["round"], 1)
        self.assertEqual(closed["lineage"], "open")
        # 3b. respond --out (author): the disposition is recorded and kept
        #     without a manual ledger add; the round-2 request then carries it.
        d_json = self.tmp / "d.json"
        d_json.write_text(json.dumps({
            "head": head, "round": 1, "author": "claude",
            "dispositions": [{"finding_id": "F1", "disposition": "accepted",
                              "payload": {"change": "fixed", "verification":
                                          "observed",
                                          "falsification": {
                                              "status": "pass",
                                              "mutation": "fails_without_fix"}
                                          }}]}), encoding="utf-8")
        d_md = self.tmp / "d.md"
        code, resp = self._run(self.author, self.author_state, "respond",
                               "--verdict", str(v1), "--from-json",
                               str(d_json), "--out", str(d_md))
        self.assertEqual(code, 0, resp)
        # The disposition AND its falsification run: the second event is the
        # product-path writer the `stale`/`unverifiable` breakers never had —
        # before it, the only `falsification_run` events in existence were
        # seeded by hand inside test_breakers.
        self.assertEqual(resp["events_added"], 2)
        self.assertTrue(Path(resp["kept"]).is_file())
        runs = [e for e in Ledger(self.author_state).events()
                if e.get("event") == "falsification_run"]
        self.assertEqual(len(runs), 1, runs)
        self.assertEqual(runs[0]["status"], "pass")
        self.assertEqual(runs[0]["mutation"], "fails_without_fix")
        self.assertEqual(runs[0]["source"], "disposition")
        self.assertEqual(runs[0]["round"], 1)
        # 4. next handoff opens round 2 (same tip; not cached, new round).
        code, r2 = self._run(self.author, self.author_state, "handoff",
                             "--claim-file", str(self.claim))
        self.assertEqual(code, 0, r2)
        self.assertEqual(r2["round"], 2)
        self.assertFalse(r2["cached"])
        r2_text = Path(r2["kept"]).read_text(encoding="utf-8")
        self.assertIn("**accepted**", r2_text)
        # ...and the run record beside it, so the reviewer rules on it.
        self.assertIn("[falsification: pass; mutation: fails_without_fix]",
                      r2_text)
        # 5. close the lineage by decision; the next handoff is round 1
        #    of lineage 2 and needs an explicit base again.
        code, lin = self._run(self.author, self.author_state, "close",
                              "--lineage", "--reason", "loop test done",
                              "--by", "user")
        self.assertEqual(code, 0, lin)
        self.assertTrue(lin["open_request"])
        self.assertEqual(lin["next_lineage"], 2)
        code, r1b = self._run(self.author, self.author_state, "handoff",
                              "--claim-file", str(self.claim),
                              "--base", self.base)
        self.assertEqual(code, 0, r1b)
        self.assertEqual(r1b["round"], 1)
        # The ledger tells the story in order.
        ledger = Ledger(self.author_state)
        kinds = [e["event"] for e in ledger.events()]
        self.assertEqual(kinds.count("request"), 3)
        self.assertEqual(kinds.count("verdict"), 1)
        self.assertEqual(kinds.count("disposition"), 1)
        self.assertEqual(kinds.count(Ledger.LINEAGE_CLOSED), 1)
        self.assertEqual(ledger.lineage_number(), 2)

    def test_handoff_refuses_when_response_is_omitted(self):
        """FALSIFICATION for sweep F4 (Blocker). The loop, with the author's
        answer skipped: handoff → take → verdict → close → handoff. The
        second handoff must refuse — no request event, no kept envelope, no
        gate run — until every finding of the round-1 verdict has its
        disposition. Mutation: reintroduce the verdict-only transition (call
        `next_round` without the preflight — remove `handoff_preflight`
        from `cmd_handoff`) and the second handoff opens round 2 with an
        empty disposition ledger; this fails on the first assertNotEqual.
        Two controls: the same handoff after `respond` succeeds, and a
        clean verdict — which closes the lineage — is followed by a round-1
        handoff that owes nothing."""
        head = _git(self.author, "rev-parse", "HEAD")
        code, rec = self._run(self.author, self.author_state, "handoff",
                              "--claim-file", str(self.claim),
                              "--base", self.base)
        self.assertEqual(code, 0, rec)
        code, taken = self._run(self.reviewer, self.reviewer_state, "take",
                                rec["kept"], "--as", "codex")
        self.assertEqual(code, 0, taken)
        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head, findings=2), encoding="utf-8")
        code, closed = self._run(self.author, self.author_state, "close",
                                 "--verdict", str(v1))
        self.assertEqual(code, 0, closed)
        before = Ledger(self.author_state).events()
        # The illegal transition: hand off with no response recorded.
        code, refused = self._run(self.author, self.author_state, "handoff",
                                  "--claim-file", str(self.claim))
        self.assertNotEqual(code, 0, refused)
        self.assertEqual(refused["next_kind"], "blocked")
        self.assertIsNone(refused["next"])
        self.assertIn("2 of 2", refused["error"])
        self.assertEqual(Ledger(self.author_state).events(), before,
                         "a refused handoff appends nothing")
        self.assertFalse((Path(self.author_state) / "exchange"
                          / "round-2-request.md").exists())
        # A PARTIAL answer is still owed: one of two.
        d_json = self.tmp / "d.json"
        one = {"head": head, "author": "claude",
               "dispositions": [{"finding_id": "F1", "disposition": "accepted",
                                 "payload": {"change": "fixed",
                                             "verification": "observed",
                                             "falsification": {
                                                 "status": "pass",
                                                 "mutation":
                                                     "fails_without_fix"}}}]}
        d_json.write_text(json.dumps(one), encoding="utf-8")
        code, resp = self._run(self.author, self.author_state, "respond",
                               "--verdict", str(v1), "--from-json",
                               str(d_json), "--out", str(self.tmp / "d.md"))
        # respond itself refuses an incomplete answer (D-INCOMPLETE): the
        # preflight is the second wall, for the case where respond was never
        # run at all — which is exactly what the first refusal above proved.
        self.assertNotEqual(code, 0, resp)
        # Control 1: the complete answer, then the handoff opens round 2.
        two = dict(one)
        two["dispositions"] = one["dispositions"] + [
            dict(one["dispositions"][0], finding_id="F2")]
        d_json.write_text(json.dumps(two), encoding="utf-8")
        code, resp = self._run(self.author, self.author_state, "respond",
                               "--verdict", str(v1), "--from-json",
                               str(d_json), "--out", str(self.tmp / "d.md"))
        self.assertEqual(code, 0, resp)
        code, r2 = self._run(self.author, self.author_state, "handoff",
                             "--claim-file", str(self.claim))
        self.assertEqual(code, 0, r2)
        self.assertEqual(r2["round"], 2)
        # Control 2: a clean verdict closes the lineage; round 1 of the next
        # lineage owes no disposition and opens.
        v2 = self.tmp / "v2.md"
        v2.write_text(verdict_text(sha=head, verdict="clean to advance"),
                      encoding="utf-8")
        code, closed = self._run(self.author, self.author_state, "close",
                                 "--verdict", str(v2))
        self.assertEqual(code, 0, closed)
        code, r1b = self._run(self.author, self.author_state, "handoff",
                              "--claim-file", str(self.claim),
                              "--base", self.base)
        self.assertEqual(code, 0, r1b)
        self.assertEqual(r1b["round"], 1)

    def test_take_recovers_from_an_empty_reviewer_checkout(self):
        """FALSIFICATION for sweep F6 (High). The reviewer's clone has an
        unborn HEAD and an empty working tree — the exact state the first
        CI run produced when the bare remote's default branch never
        existed. `take` must fetch the stamped target, read the target's
        own review.toml, validate against it, check every reference from
        the target tree, and record the take. Mutation: restore pre-fetch
        validation against the reviewer's local config (validate before
        `probe_target`, with `cfg` instead of `target_config`) and this
        empty checkout refuses with T-UNDECLARED — the first assertEqual
        fails. Control: the ordinary clone validates against the target's
        config too, and says so."""
        head = _git(self.author, "rev-parse", "HEAD")
        code, rec = self._run(self.author, self.author_state, "handoff",
                              "--claim-file", str(self.claim),
                              "--base", self.base)
        self.assertEqual(code, 0, rec)
        # An empty reviewer repository: initialised, nothing checked out,
        # no review.toml anywhere in it.
        empty = self.tmp / "reviewer-empty"
        self._sh("git", "init", "-q", "-b", "main", str(empty))
        self.assertEqual(sorted(x.name for x in empty.iterdir()), [".git"])
        empty_state = self.tmp / "state-empty"
        code, taken = self._run(empty, empty_state, "take", rec["kept"],
                                "--as", "codex")
        self.assertEqual(code, 0, taken)
        self.assertEqual(taken["sha"], head)
        self.assertIn("fetched", taken["target"]["fetch"])
        self.assertTrue(taken["target"]["config"].startswith(
            "target review.toml at"), taken["target"]["config"])
        self.assertTrue(taken["references"][0]["status"].startswith(
            "checked"), taken["references"])
        kinds = [e["event"] for e in Ledger(empty_state).events()]
        self.assertIn("take", kinds)
        # And the working tree was left exactly as found: empty.
        self.assertEqual(sorted(x.name for x in empty.iterdir()), [".git"])
        # Control: the ordinary clone, whose checkout DOES carry the same
        # review.toml, is validated against the target's copy as well.
        code, taken2 = self._run(self.reviewer, self.reviewer_state, "take",
                                 rec["kept"], "--as", "codex")
        self.assertEqual(code, 0, taken2)
        self.assertTrue(taken2["target"]["config"].startswith(
            "target review.toml at"))
        # A target that carries no review.toml is governed by the checkout,
        # and the record says so rather than pretending otherwise. Round 3
        # F2: absence is the answer to `ls-tree`, not the answer to a failed
        # read — a target whose config cannot be READ is a refusal, and the
        # sibling rows for that live in TestAuthorityOriginIsClassified.
        rec2, origin = transport.resolve_authority(
            dataclasses.replace(CFG, ledger_dir=None), "0" * 40,
            git=lambda *a: "")
        self.assertEqual(origin, transport.AUTHORITY_EXTERNAL)
        self.assertIn("carries no review.toml", rec2.source)
        self.assertEqual(rec2.taxonomy, CFG.taxonomy)


class TestGateIdIsASafeFilenameComponent(unittest.TestCase):
    """Round 6 F2: a gate id is a logical identity AND a path component.

    `_retain_output` writes `<ledger>/gate-output/<sha>/<id>.log`, so an
    ABSOLUTE id discards the directory entirely — measured: a manifest whose
    id was an absolute path was accepted, ran, and wrote outside the ledger's
    subtree — while separators and dot segments escape or alias it. Round 5
    established that two rows cannot share an id STRING; two distinct strings
    can still name one destination, and on a case-folding filesystem
    routinely do.

    The grammar is closed rather than the escapes enumerated: containment and
    one-id-one-file are properties of the alphabet, not of a blacklist.

    Mutations: accept any non-blank id and the escape rows pass; compare ids
    case-sensitively and the alias row passes.
    """

    def _cfg(self):
        return dataclasses.replace(CFG, ledger_dir=None)

    def _rows(self, *ids):
        return "".join(f'[[gates]]\nid = "{i}"\ncommand = ["true"]\n'
                       for i in ids)

    def _refused(self, body, door):
        with self.assertRaises(config.ConfigError) as ctx:
            if door == "from_text":
                config.from_text(body, self._cfg(), source="t")
            else:
                tmp = Path(tempfile.mkdtemp(prefix="gate-id-"))
                self.addCleanup(lambda: __import__("shutil").rmtree(
                    tmp, ignore_errors=True))
                subprocess.run(["git", "init", "-q", str(tmp)], check=True,
                               capture_output=True, timeout=60)
                (tmp / "review.toml").write_text(body, encoding="utf-8")
                config.load(tmp)
        return str(ctx.exception)

    ESCAPES = {
        "absolute": "/tmp/escaped",
        "parent segment": "../escaped",
        "separator": "a/b",
        "leading dot": ".hidden",
        "dot segment": "..",
        "backslash": "a\\b",
        "space": "two words",
    }

    def test_no_escape_form_survives_either_configuration_door(self):
        for door in ("from_text", "load"):
            for label, gid in self.ESCAPES.items():
                with self.subTest(door=door, form=label):
                    self.assertIn("filename component",
                                  self._refused(self._rows(gid), door))

    def test_two_ids_naming_one_destination_refuse(self):
        """Distinct strings, one file: the alias round 5's string equality
        could not see."""
        self.assertIn("one retained output",
                      self._refused(self._rows("tests", "TESTS"),
                                    "from_text"))

    def test_distinct_valid_ids_are_the_paired_control(self):
        cfg = config.from_text(self._rows("tests", "lint-2", "a.b_c"),
                               self._cfg(), source="t")
        self.assertEqual([g["id"] for g in cfg.gates],
                         ["tests", "lint-2", "a.b_c"])

    def test_every_admitted_id_stays_an_immediate_child(self):
        """The property the grammar exists to give `_retain_output`: for any
        id the schema admits, the destination is one level under the
        per-SHA directory and nowhere else."""
        target = Path("/ledger/gate-output/abcdef")
        cfg = config.from_text(self._rows(*self.CONTAINMENT), self._cfg(),
                               source="t")
        for gate in cfg.gates:
            with self.subTest(id=gate["id"]):
                out = (target / f"{gate['id']}.log").resolve()
                self.assertEqual(out.parent, target.resolve())

    CONTAINMENT = ("tests", "lint-2", "a.b_c", "A", "z9")


if __name__ == "__main__":
    unittest.main()

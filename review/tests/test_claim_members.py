"""The claim's 0.25.0 members (public issue #4, brief `take-objective-map`).

A FOURTH member kind — a list of objects, each member with its own closed
field table (`vocab.CLAIM_OBJECT_LIST_FIELDS`) — and five members:
`carried_findings`, `objectives`, `observations`, `attestation_map` of that
kind, and `excluded_paths` in the `scope_paths` grammar. Everything they
render is ADDITIVE: no wrapper attribute, no required section moved, the
attestation fence untouched.

WHERE A ROW RUNS. This module's cost was process launches — a `bin/loupe`
child per row and the git it runs — not Python. So each guard's partition
runs IN-PROCESS against the function that decides it, and a few rows per
guard stay END-TO-END through the real CLI (a subprocess, real argv, a
scratch repository with its own configuration and state directory): one
accept, and one per refusal kind. Each end-to-end row is also compared with
the in-process answer to the same input, so the in-process rows are proven
to be what the CLI runs. A fixture is built once per class and only read;
an end-to-end row that could move it works on a copy (`copy_loop`).

THE PARTITION:

  TestGrammarPartition — per member: absent (control), empty list, a
    non-list, a non-object element, an unknown field, each required field
    missing, each field of each wrong type and grammar, a duplicated
    carried fingerprint; each refusal names its member path. The cases are
    DERIVED from the field tables, so a field added there joins the
    partition. In-process: every row, through the claim boundary both
    emitting verbs call first (`cli._capture_claim`) and the blocked exit
    both print (`cli._claim_defect_exit`). End-to-end: one row per refusal
    kind the partition produces (type, empty, unknown, missing, shape,
    duplicate) and `excluded_paths` through `handoff`, and one through
    `emit-request`; each on its own copy of a DIRTY repository (so a commit
    would show) that it leaves byte-identical — refs, index, worktree and
    ledger — printing exactly the in-process payload.
  TestRecordBound — the ledger-bound checks: a carried fingerprint absent
    from an origin verdict this ledger holds (refused, before anything is
    committed) against one this ledger does not hold (stated, emitted);
    `required` read from the kept origin verdict, and a typed one that
    differs; fix commits inside and outside the span; an attestation-map
    row resolving to the previous round's standing disposition, to a
    carried entry, to neither (refused), and naming a gate the governing
    manifest lacks (refused at emission, before any gate runs).
    In-process, against `emit.check_claim_record` and the block it renders:
    the origin not held, the kept `required`, the typed one that differs,
    the map row a carried entry answers. End-to-end: the fix-commit span
    and the previous-round map row (the accepts, the second carrying the
    gate's own attestation columns), and every refusal.
  TestSpanRendering — objectives mapped against the span, the unmapped
    changed files and the objective paths matching nothing; excluded paths
    as a prefix, a glob and an exact path; generated paths subtracted from
    the in-scope counts; the scoped diff stamp RUN, printing only scoped
    paths; observations rendered outside the attestation block; and the
    request précis carrying the in-scope counts and the notices. End to
    end over one shared emission, which no row moves; a claim with no
    `scope_paths` is an emission of its own, and the scope report is
    `validate.scope_gaps` read in-process.
  TestScopedMatchesInScope — round-2 F3: the `Scoped:` command selects
    EXACTLY the changed paths `emit.in_scope` selects over the same span.
    Each row renders the line `emit_request` renders — in-process,
    `emit._scoped_stamp` over the span's `emit.diff_shape` with the call
    site's renderer — EXECUTES the command through `/bin/sh` and compares
    every path its `diff --git` headers name (both sides) with `in_scope`
    over `git diff --name-only -z` of the span. Seven rows also run through
    the real `emit-request` — the partition's `mixed` command and its
    `none` line, the `[^a].txt` reproduction, the withheld line over 1,400
    paths, the rename prefix that is not compressed, the reviewer's
    diverged rename and both copy prefixes — and each prints exactly the
    line the in-process row judged. Rows: exact
    entries (a file, a file in a directory, a name that
    is only a directory, a file that became a directory and the reverse),
    `dir/` prefixes (nested, over a file that became a directory, one
    matching nothing), globs with `*` (crossing `/`), `?`, sets, ranges,
    `[^a]`, `[!a]`, a POSIX class and a backslash, literal metacharacter
    names (`*.txt`, `?.txt`, `[a].txt`, an unclosed `[a.txt`), a space,
    case, a rename's target and its source, and an empty match. The
    reviewer's five reproductions are paired controls in their own spans:
    `[^a].txt` and exact `src` diverged, `[!a].txt`, `a.txt` and `src/`
    agreed. Size: a prefix over 1,400 paths is one pathspec; a glob over
    100 runs, one over 900 (~58 KB) runs, and one over 1,400 is
    withheld with its byte count.
    Round-3 F1, in a span of renames: outgoing, incoming and within-prefix
    renames, each with and without an independently matched file, under
    exact entries, prefixes and globs; both ends selected by two entries;
    a nested move; a source that became a directory holding a matched
    file (its prefix is named path by path, not compressed); a directory
    a rename emptied that became a file (exact and glob); an incoming
    target git re-pairs with a matched deletion; a source and a target
    named with a space and a non-ASCII letter; and the reviewer's
    `old/r.txt → new/r.txt` + `old/keep.txt` reproduction with its
    exact-file and destination-prefix controls. Copies, in a span of their
    own under `diff.renames = copies`, add no path the command could
    select: a copy's source is walked only when it changed, and is then
    judged under its own name. The rename treatment the
    comparison uses is the one `in_scope` has: it judges a rename by its
    target, so a source is never selected and the command must name none.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

from review import cli, config, emit, paths, vocab
from review.ledger import Ledger
from review.tests.test_precis_taxonomy import (SHIM, ScratchLoop, cli_env,
                                               finding, taxonomy_toml,
                                               verdict)

GATE = '\n[[gates]]\nid = "unit"\ncommand = ["true"]\nblocking = true\n'
FP = "fp2:0123456789abcdef"
REFS = [{"path": "review.toml", "required": True}]


class _ClassCase:
    """What `ScratchLoop` needs of a test case, at class scope."""

    def __init__(self, cls):
        self.cls = cls

    def addCleanup(self, fn, *a, **k):
        self.cls.addClassCleanup(fn, *a, **k)

    def skipTest(self, why):
        raise unittest.SkipTest(why)


def claim(**members) -> dict:
    return {"objective": "t3 members", "references": REFS, **members}


def copy_loop(case, loop: ScratchLoop) -> ScratchLoop:
    """A private copy of `loop` — its repository, its state directory and
    the files beside them — for ONE test that could move what it is handed.
    The fixture is built once; copying it launches nothing, and a row that
    failed to refuse moves only its own copy."""
    try:
        tmp = Path(tempfile.mkdtemp(prefix="t3c-"))
    except OSError as exc:
        case.skipTest(f"filesystem writes denied ({exc})")
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    shutil.copytree(loop.tmp, tmp, symlinks=True, dirs_exist_ok=True)
    twin = copy.copy(loop)
    twin.tmp, twin.repo, twin.state = tmp, tmp / "repo", tmp / "state"
    # A copied file is a new inode, so the copied index's stat data is
    # stale and the CLI's first `git status` would rewrite it — a moved
    # index the snapshot would blame on the row. Refreshed once, here, the
    # copy's index is as current as the original's.
    twin.git("update-index", "-q", "--refresh")
    return twin


class _Loop(unittest.TestCase):
    """Shared helpers: a refusal is judged on its payload AND, end to end,
    on the repository it must not have touched."""

    def scratch(self) -> Path:
        """A directory of this test's own, outside every repository."""
        if getattr(self, "_scratch", None) is None:
            try:
                self._scratch = Path(tempfile.mkdtemp(prefix="t3s-"))
            except OSError as exc:
                self.skipTest(f"filesystem writes denied ({exc})")
            self.addCleanup(shutil.rmtree, self._scratch, ignore_errors=True)
        return self._scratch

    def state_bytes(self, loop):
        return {str(p.relative_to(loop.state)): p.read_bytes()
                for p in sorted(loop.state.rglob("*"))
                if p.is_file() and p.suffix != ".lock"} \
            if loop.state.exists() else {}

    def refused_in_process(self, body, member_path, case="", path=None):
        """IN-PROCESS: the claim boundary both emitting verbs call before
        anything else (`cli._capture_claim`) refuses `body`, and the exit
        both print for it (`cli._claim_defect_exit`) is blocked and names
        `member_path`. Returns (the defect, the payload)."""
        if path is None:
            path = self.scratch() / "refused.json"
            path.write_text(json.dumps(body), encoding="utf-8")
        try:
            cli._capture_claim(types.SimpleNamespace(claim_file=str(path)))
        except emit.ClaimDefective as exc:
            defect = exc
        else:
            self.fail(f"{case}: the claim boundary admitted it")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli._claim_defect_exit(defect)
        payload = json.loads(out.getvalue())
        self.assertEqual(code, 1, f"{case}: {payload}")
        self.assertEqual(payload.get("next_kind"), "blocked", case)
        self.assertIn(f"(at {member_path})", payload.get("remedy", ""),
                      f"{case}: {payload}")
        return defect, payload

    def assert_refused_untouched(self, loop, verb, body, member_path,
                                 case=""):
        """END TO END: `verb` refuses `body` naming `member_path`; refs,
        index, worktree and the ledger are byte-identical afterwards.
        Returns (the payload, the claim file it was handed)."""
        before, ledger_before = loop.snapshot(), self.state_bytes(loop)
        path = loop.claim(body, name="refused.json")
        argv = [verb, "--claim-file", path, "--base", loop.base]
        if verb == "emit-request":
            argv += ["--out", loop.tmp / "never.md"]
        code, payload = loop.loupe(*argv, "--local-only")
        self.assertEqual(code, 1, f"{case}: {payload}")
        self.assertIsInstance(payload, dict, case)
        self.assertEqual(payload.get("next_kind"), "blocked", case)
        self.assertIn(f"(at {member_path})", payload.get("remedy", ""),
                      f"{case}: {payload}")
        self.assertEqual(loop.snapshot(), before, f"{case}: repo moved")
        self.assertEqual(self.state_bytes(loop), ledger_before,
                         f"{case}: the ledger moved")
        self.assertFalse((loop.tmp / "never.md").exists(), case)
        return payload, path


class TestGrammarPartition(_Loop):

    VALID = {
        "carried_findings": {"fingerprint": FP, "origin": "Lfeedbeef00/2",
                             "outcome": "fixed", "required": "r",
                             "fix": ["a" * 40]},
        "objectives": {"title": "t", "paths": ["f.txt"], "tests": ["t"],
                       "references": ["r.md"], "authority": "inventory.txt",
                       "covers": ["a"]},
        "observations": {"command": "c", "result": "r", "context": "x"},
        "attestation_map": {"fingerprint": FP, "gate": "unit", "test": "t"},
    }
    #: Per field kind: wrong JSON kinds, and values of the right kind
    #: outside the field's grammar.
    WRONG = {
        "string": [3, [], None, {}, "", "  "],
        "list_of_string": ["x", [], [1], [""], None],
        "scope_paths": ["x", [], [1], [""], None],
        "commits": ["x", [], [1], ["abc"], ["A" * 40], ["a" * 39]],
        "fingerprint": [3, "", "fp2:xyz", "fp1:0123456789abcdef",
                        "fp2:0123456789ABCDEF", "fp2:0123456789abcdef0"],
        "origin": [3, "", "L1", "L1/0", "/1", "L 1/1", "L1/x"],
        "carried_outcome": [3, "", "withdrawn", "Fixed"],
        "gate_id": [3, "", "a/b", ".x", "x" * 65],
        # 0.26.0: a path in the reference-manifest grammar.
        "path": [3, [], "", "  ", "a b", "-x", "dir/", "/abs", "../x",
                 "a/./b", "a//b"],
    }
    #: The rows that ALSO run through the real CLI: (verb, case, body,
    #: member path) — one per refusal kind the partition produces, the
    #: top-level list member, and the other emitting verb.
    END_TO_END = (
        ("handoff", "type: a member that is not a list",
         claim(objectives="x"), "objectives"),
        ("handoff", "empty: a member that is an empty list",
         claim(observations=[]), "observations"),
        ("handoff", "unknown: a field no table declares",
         claim(carried_findings=[{**VALID["carried_findings"],
                                  "zz_typo": "x"}]),
         "carried_findings[0].zz_typo"),
        ("handoff", "missing: a required field",
         claim(attestation_map=[{"fingerprint": FP}]),
         "attestation_map[0].gate"),
        ("handoff", "shape: a value outside its field's grammar",
         claim(carried_findings=[{**VALID["carried_findings"],
                                  "origin": "L1/0"}]),
         "carried_findings[0].origin"),
        ("handoff", "duplicate: one fingerprint carried twice",
         claim(carried_findings=[VALID["carried_findings"],
                                 {**VALID["carried_findings"],
                                  "outcome": "deferred"}]),
         "carried_findings[1].fingerprint"),
        ("handoff", "excluded_paths: an element that is not a string",
         claim(excluded_paths=[1]), "excluded_paths[0]"),
        ("emit-request", "emit-request refuses at the same boundary",
         claim(objectives=[{"title": "t"}]), "objectives[0].paths"),
    )

    @classmethod
    def setUpClass(cls):
        # The end-to-end rows' fixture, built once and copied per row.
        cls.loop = ScratchLoop(_ClassCase(cls),
                               taxonomy_toml(("High", "Low"), ("High",),
                                             gates=GATE))
        # A DIRTY tree: a refusal that reached the sweep would commit it.
        cls.loop.write("g.txt", "outstanding\n")

    def rows(self, group=None):
        """Every in-process row as (group, case, body, member path),
        derived from the field tables."""
        tables = vocab.CLAIM_OBJECT_LIST_FIELDS
        out = []
        for member in tables:
            for wrong in ("x", {}, 3, None, True):
                out.append(("member", f"{member} = {wrong!r}",
                            claim(**{member: wrong}), member))
            out.append(("member", f"{member} empty", claim(**{member: []}),
                        member))
            for element in (1, "x", [], None):
                out.append(("member", f"{member}[0] = {element!r}",
                            claim(**{member: [element]}), f"{member}[0]"))
            # A bad SECOND entry is located as the second.
            out.append(("member", f"{member}[1]",
                        claim(**{member: [self.VALID[member], 7]}),
                        f"{member}[1]"))
        for member in tables:
            out.append(("fields", f"{member} unknown field",
                        claim(**{member: [{**self.VALID[member],
                                           "zz_typo": "x"}]}),
                        f"{member}[0].zz_typo"))
            for field in vocab.CLAIM_OBJECT_REQUIRED[member]:
                entry = {k: v for k, v in self.VALID[member].items()
                         if k != field}
                out.append(("fields", f"{member} missing {field}",
                            claim(**{member: [entry]}),
                            f"{member}[0].{field}"))
        for member, table in tables.items():
            for field, kind in table.items():
                for wrong in self.WRONG[kind]:
                    where = f"{member}[0].{field}"
                    if isinstance(wrong, list) and wrong:
                        where += "[0]"
                    out.append(("values", f"{where} = {wrong!r}",
                                claim(**{member: [{**self.VALID[member],
                                                   field: wrong}]}),
                                where))
        entry = self.VALID["carried_findings"]
        out.append(("duplicate", "duplicate", claim(carried_findings=[
            entry, {**entry, "outcome": "deferred"}]),
            "carried_findings[1].fingerprint"))
        for wrong, where in (("x", "excluded_paths"), ({}, "excluded_paths"),
                             ([1], "excluded_paths[0]"),
                             ([None], "excluded_paths[0]")):
            out.append(("excluded", f"excluded_paths = {wrong!r}",
                        claim(excluded_paths=wrong), where))
        return [r for r in out if group is None or r[0] == group]

    def refuse_group(self, group):
        for _group, case, body, where in self.rows(group):
            with self.subTest(row=case):
                self.refused_in_process(body, where, case)

    def test_the_tables_cover_every_member_and_kind(self):
        self.assertEqual(set(self.VALID), set(vocab.CLAIM_OBJECT_LIST_FIELDS))
        kinds = {k for t in vocab.CLAIM_OBJECT_LIST_FIELDS.values()
                 for k in t.values()}
        self.assertEqual(kinds - set(self.WRONG), set())
        for member, table in vocab.CLAIM_OBJECT_LIST_FIELDS.items():
            self.assertEqual(set(self.VALID[member]), set(table), member)
            self.assertLessEqual(set(vocab.CLAIM_OBJECT_REQUIRED[member]),
                                 set(table), member)

    def test_the_member_itself(self):
        self.refuse_group("member")

    def test_unknown_and_missing_fields(self):
        self.refuse_group("fields")

    def test_every_field_refuses_every_wrong_value(self):
        self.refuse_group("values")
        located = {w for _g, _c, _b, w in self.rows("values")}
        pairs = {(m, f) for m, t in vocab.CLAIM_OBJECT_LIST_FIELDS.items()
                 for f in t}
        self.assertEqual({(m, f) for m, f in pairs
                          if {f"{m}[0].{f}", f"{m}[0].{f}[0]"} & located},
                         pairs)

    def test_a_carried_fingerprint_is_carried_once(self):
        self.refuse_group("duplicate")

    def test_excluded_paths_is_a_list_of_strings(self):
        self.refuse_group("excluded")

    def test_the_end_to_end_rows_cover_every_refusal_kind(self):
        """The partition's own bookkeeping: every refusal kind an
        in-process row produces has an end-to-end row."""
        def kinds(rows):
            return {self.refused_in_process(body, where, case)[0].defect
                    for case, body, where in rows}
        partition = kinds((c, b, w) for _g, c, b, w in self.rows())
        self.assertEqual(partition, {"type", "empty", "unknown", "missing",
                                     "shape", "duplicate"})
        self.assertLessEqual(partition, kinds(
            (c, b, w) for _v, c, b, w in self.END_TO_END))

    def test_end_to_end_one_row_per_refusal_kind(self):
        """Through the real CLI, each on its own copy of the dirty fixture:
        refused, nothing moved, and the payload the in-process row
        prints for the same file."""
        for verb, case, body, where in self.END_TO_END:
            with self.subTest(row=case, verb=verb):
                payload, path = self.assert_refused_untouched(
                    copy_loop(self, self.loop), verb, body, where, case)
                self.assertEqual(payload, self.refused_in_process(
                    body, where, case, path=path)[1])

    def test_emit_request_refuses_at_the_same_boundary(self):
        """`emit-request`'s row, in-process; end to end it is the last row
        of `END_TO_END`, which prints the same payload `handoff`'s do."""
        verb, case, body, where = self.END_TO_END[-1]
        self.assertEqual(verb, "emit-request")
        self.refused_in_process(body, where, case)


class TestControls(_Loop):
    """Absent, and every member valid at once: both emit."""

    def test_absent_and_all_valid_emit(self):
        loop = ScratchLoop(self, taxonomy_toml(("High", "Low"), ("High",),
                                               gates=GATE))
        for body, name in ((claim(), "absent.md"), (claim(
                carried_findings=[TestGrammarPartition.VALID[
                    "carried_findings"]],
                objectives=[TestGrammarPartition.VALID["objectives"]],
                observations=[TestGrammarPartition.VALID["observations"]],
                attestation_map=[TestGrammarPartition.VALID[
                    "attestation_map"]],
                excluded_paths=[]), "all.md")):
            out = loop.tmp / name
            code, payload = loop.loupe(
                "emit-request", "--claim-file",
                loop.claim(body, name=name + ".json"), "--base", loop.base,
                "--out", out, "--local-only")
            self.assertEqual(code, 0, payload)
            self.assertTrue(out.is_file())
        absent, full = ((loop.tmp / n).read_text() for n in ("absent.md",
                                                             "all.md"))
        for heading in ("Objectives —", "Carried findings —",
                        "Finding-to-attestation map —",
                        "Author-typed observations —", "Excluded paths"):
            self.assertNotIn(heading, absent)
            self.assertIn(heading, full)
        self.assertIn("Excluded paths: (declared empty — nothing excluded)",
                      full)


class TestRecordBound(_Loop):
    """Round 1 handed off, ruled and answered on `main`; round 2's target a
    new commit that fixes it. The carried and mapped fingerprints are then
    judged against this ledger.

    The fixture is built once and never moved: an in-process row reads it
    — configuration, ledger and lineage loaded fresh for each row — and an
    end-to-end row runs the real CLI on its own copy."""

    @classmethod
    def setUpClass(cls):
        loop = cls.loop = ScratchLoop(
            _ClassCase(cls), taxonomy_toml(("High", "Low"), ("High",),
                                           gates=GATE))
        run = cls._must
        run(loop.handoff())
        v1 = loop.file("v1.md", verdict(loop.head, finding(
            1, "High", title="the first", required="make f.txt say three")
            + finding(2, "Low", title="the second", evidence="g.txt:1")))
        run(loop.loupe("close", "--verdict", v1))
        fps = run(loop.loupe("fingerprint", v1))["findings"]
        cls.x, cls.z = fps[0]["fp"], fps[1]["fp"]
        cls.lineage = run(loop.loupe("ledger", "report", "--format",
                                     "json"))["lineage"]["id"]
        d = loop.file("d.json", json.dumps({
            "head": loop.head, "round": 1, "author": "claude",
            "dispositions": [
                {"finding_id": "F1", "disposition": "accepted",
                 "payload": {"change": "c", "verification": "v",
                             "falsification": {
                                 "status": "pass",
                                 "mutation": "fails_without_fix"}}},
                {"finding_id": "F2", "disposition": "accepted",
                 "payload": {"change": "c", "verification": "v",
                             "falsification": {
                                 "status": "pass",
                                 "mutation": "fails_without_fix"}}}]}))
        run(loop.loupe("respond", "--verdict", v1, "--from-json", d,
                       "--out", loop.tmp / "d.md"))
        loop.write("f.txt", "three\n")
        loop.git("commit", "-q", "-am", "the fix")
        cls.fix = loop.git("rev-parse", "HEAD")
        cls.round1 = loop.head

    @staticmethod
    def _must(result):
        code, payload = result
        if code != 0:
            raise AssertionError(f"fixture step failed: {payload}")
        return payload

    # ------------------------------------------------------ end to end

    def emit(self, loop, body, name):
        out = loop.tmp / name
        code, payload = loop.loupe(
            "emit-request", "--claim-file",
            loop.claim(body, name=name + ".json"),
            "--base", self.round1, "--out", out, "--local-only")
        self.assertEqual(code, 0, payload)
        return out.read_text(encoding="utf-8")

    def refused(self, body, member_path, case, verb="emit-request"):
        """`verb` refuses `body` on a copy of the fixture made DIRTY, so a
        commit would show."""
        loop = copy_loop(self, self.loop)
        loop.write("g.txt", "outstanding\n")
        return self.assert_refused_untouched(loop, verb, body, member_path,
                                             case)[0]

    # ------------------------------------------------------- in-process

    def record(self, body):
        """(configuration, record) IN-PROCESS: `body` through the claim
        boundary, then `emit.check_claim_record` — the one function the
        hand-off calls before its commit and `emit_request` calls to render
        — over this fixture's configuration, ledger and lineage, each read
        fresh."""
        path = self.scratch() / "claim.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        captured = cli._capture_claim(
            types.SimpleNamespace(claim_file=str(path)))
        cfg = config.load(repo_root=self.loop.repo,
                          ledger_dir=str(self.loop.state))
        ledger = Ledger(cfg.ledger_dir)
        lineage = cli._read_lineage(ledger, cfg, "emit-request")
        self.assertEqual(lineage, self.lineage)
        return cfg, emit.check_claim_record(captured.claim, ledger, lineage,
                                            cfg, path=path)

    def carried(self, body) -> str:
        """The Carried findings block `emit_request` renders for `body`
        over this span (`--base` round 1, the fix as the target)."""
        cfg, record = self.record(body)
        return "\n".join(emit._carried_block(record, cfg.repo_root,
                                             self.round1, self.fix))

    # ---------------------------------------------------- carried findings

    def test_a_fingerprint_its_ledger_origin_never_ruled_is_refused(self):
        body = claim(carried_findings=[{"fingerprint": FP,
                                        "origin": f"{self.lineage}/1",
                                        "outcome": "fixed"}])
        payload = self.refused(body, "carried_findings[0].fingerprint",
                               "absent from origin")
        self.assertIn("recorded in this ledger without that finding",
                      payload["error"])
        # And through `handoff`, the verb that commits: a dirty tree stays
        # dirty, uncommitted, and no round is recorded.
        self.refused(body, "carried_findings[0].fingerprint", "handoff",
                     verb="handoff")
        # In-process, the same refusal from the function both call.
        with self.assertRaises(emit.ClaimDefective) as ctx:
            self.record(body)
        self.assertEqual(ctx.exception.member,
                         "carried_findings[0].fingerprint")
        self.assertEqual(payload["error"],
                         f"the claim file {ctx.exception.detail}")

    def test_an_origin_this_ledger_does_not_hold_is_stated(self):
        text = self.carried(claim(carried_findings=[
            {"fingerprint": FP, "origin": "Lnothere000/3",
             "outcome": "deferred", "required": "typed by hand"}]))
        self.assertIn(f"- `{FP}` · origin Lnothere000/3 · **deferred**", text)
        self.assertIn("origin verdict not in this ledger — not verifiable "
                      "here", text)
        self.assertIn("required (typed by the author): typed by hand", text)
        self.assertIn("Notice — carried finding(s) whose origin verdict is "
                      "not in this ledger, so not verifiable here (1)", text)

    def test_required_comes_from_the_kept_origin_verdict(self):
        text = self.carried(claim(carried_findings=[
            {"fingerprint": self.x, "origin": f"{self.lineage}/1",
             "outcome": "fixed", "fix": [self.fix]}]))
        self.assertIn("required (the origin verdict, kept on this machine): "
                      "make f.txt say three", text)
        self.assertIn(f"fix: `{self.fix[:12]}`\n", text)
        self.assertNotIn("Notice — carried", text)
        self.assertNotIn("Notice — fix commit", text)

    def test_a_typed_required_that_differs_is_a_notice(self):
        text = self.carried(claim(carried_findings=[
            {"fingerprint": self.x, "origin": f"{self.lineage}/1",
             "outcome": "fixed", "required": "something else"}]))
        self.assertIn("make f.txt say three", text)
        self.assertNotIn("something else", text)
        self.assertIn("the claim's `required` differs from the origin "
                      "verdict's Required outcome", text)
        self.assertIn("Notice — carried finding(s) whose `required` differs "
                      "from the origin verdict's (1)", text)

    def test_fix_commits_inside_and_outside_the_span(self):
        """End to end, the carried block's accept row: its span is the
        one the CLI resolves from `--base`, and it renders exactly the
        in-process block."""
        body = claim(carried_findings=[
            {"fingerprint": self.x, "origin": f"{self.lineage}/1",
             "outcome": "fixed",
             "fix": [self.fix, self.loop.base, "f" * 40]}])
        text = self.emit(copy_loop(self, self.loop), body, "span.md")
        self.assertIn(f"`{self.fix[:12]}`, `{self.loop.base[:12]}` (NOT in "
                      f"this review's span), `{'f' * 12}` (NOT in this "
                      f"review's span)", text)
        self.assertIn("Notice — fix commit(s) outside this review's span "
                      "(2)", text)
        self.assertIn(self.carried(body), text)

    # ----------------------------------------------------- attestation map

    def test_a_row_resolves_to_the_previous_rounds_disposition(self):
        """End to end, the map's accept row: the command, result and
        binding columns are joined from this request's own attestation."""
        text = self.emit(copy_loop(self, self.loop), claim(attestation_map=[
            {"fingerprint": self.x, "gate": "unit", "test": "t_one"}]),
            "map.md")
        self.assertRegex(text, re.escape(
            f"| `{self.x}` | round 1 F1 (accepted) | unit | t_one | true | "
            f"exit 0 | bound |"))
        # The finding no row covers is named, so the reviewer reruns it.
        self.assertIn("Notice — finding(s) this request answers that no map "
                      f"row covers — rerun these (1): `{self.z}`", text)

    def test_a_row_resolves_to_a_carried_entry(self):
        """In-process: what the row answers is decided by
        `check_claim_record`; the gate columns it joins are the
        previous-disposition row's, end to end."""
        _cfg, record = self.record(claim(
            carried_findings=[{"fingerprint": FP,
                               "origin": "Lnothere000/3",
                               "outcome": "fixed"}],
            attestation_map=[{"fingerprint": FP, "gate": "unit"}]))
        self.assertEqual([r["answers"] for r in record["mapped"]],
                         ["carried from Lnothere000/3"])
        text = "\n".join(emit._attestation_map_block(record, []))
        self.assertIn(f"| `{FP}` | carried from Lnothere000/3 | unit | - |",
                      text)

    def test_a_row_about_no_answered_finding_is_refused(self):
        payload = self.refused(
            claim(attestation_map=[{"fingerprint": FP, "gate": "unit"}]),
            "attestation_map[0].fingerprint", "unanswered")
        self.assertIn("resolves to no standing disposition of round 1",
                      payload["error"])

    def test_a_gate_the_governing_manifest_lacks_is_refused(self):
        """Judged at emission — the governing manifest is the target's —
        and before any gate runs. A clean tree, so nothing is committed or
        pushed on the way there, and the refusal leaves it as it was."""
        loop = copy_loop(self, self.loop)
        before = loop.snapshot()
        out = loop.tmp / "never-gate.md"
        code, payload = loop.loupe(
            "emit-request", "--claim-file",
            loop.claim(claim(attestation_map=[
                {"fingerprint": self.x, "gate": "nope"}]),
                name="gate.json"),
            "--base", self.round1, "--out", out, "--local-only")
        self.assertEqual(code, 1, payload)
        self.assertEqual(payload["next_kind"], "blocked")
        self.assertIn("nope", payload["error"])
        self.assertIn("does not declare", payload["error"])
        self.assertEqual(loop.snapshot(), before)
        self.assertFalse(out.exists())


class TestSpanRendering(_Loop):

    @classmethod
    def setUpClass(cls):
        loop = cls.loop = ScratchLoop(_ClassCase(cls),
                                      taxonomy_toml(("High", "Low"),
                                                    ("High",)))
        loop.write(".gitattributes", "gen/** linguist-generated\n")
        for rel, text in (("src/a.py", "a\n"), ("docs/x.md", "d\n"),
                          ("gen/out.json", "g\n"), ("other.txt", "o\n"),
                          ("notes/n.txt", "n\n")):
            loop.write(rel, text)
        loop.git("add", ".")
        loop.git("commit", "-q", "-m", "tree")
        cls.base = loop.git("rev-parse", "HEAD")
        for rel, text in (("src/a.py", "a\nb\n"), ("src/b.py", "b\n"),
                          ("docs/x.md", "d\ne\n"),
                          ("gen/out.json", "g\nh\ni\n"),
                          ("other.txt", "o2\n"), ("notes/n.txt", "n\nm\n")):
            loop.write(rel, text)
        loop.git("add", ".")
        loop.git("commit", "-q", "-m", "span")
        cls.body = claim(
            scope_paths=["src/", "docs/x.md", "gen/*.json"],
            excluded_paths=["docs/", "*.txt", "notes/n.txt"],
            objectives=[{"title": "the source change", "paths": ["src/"],
                         "tests": ["test_a"], "references": ["docs/x.md"]},
                        {"title": "nothing here", "paths": ["lib/",
                                                            "src/a.py"]}],
            observations=[{"command": "pytest -q", "result": "3 passed",
                           "context": "author laptop, before the rebase"}])
        out = loop.tmp / "request.md"
        code, payload = loop.loupe(
            "emit-request", "--claim-file", loop.claim(cls.body),
            "--base", cls.base, "--out", out, "--local-only")
        if code != 0:
            raise AssertionError(payload)
        cls.text = out.read_text(encoding="utf-8")
        cls.out = out

    def test_objectives_map_the_span(self):
        self.assertIn("| the source change | `src/` | src/a.py, src/b.py | "
                      "test_a | docs/x.md |", self.text)
        self.assertIn("| nothing here | `lib/`, `src/a.py` | src/a.py | - | "
                      "- |", self.text)
        self.assertIn("Notice — objective path(s) that match no changed "
                      "file (1): `lib/` (nothing here)", self.text)

    def test_unmapped_is_counted_over_the_in_scope_paths(self):
        # other.txt and notes/n.txt are excluded (glob and exact), docs/ by
        # prefix, gen/ is generated: every in-scope path is mapped.
        self.assertNotIn("no objective maps", self.text)

    def test_generated_and_excluded_are_subtracted_from_the_counts(self):
        # Span: 6 files, 7 insertions, 1 deletion. Less gen/out.json
        # (+2, generated) and docs/x.md (+1), other.txt (+1 -1),
        # notes/n.txt (+1), excluded by prefix, glob and exact path.
        self.assertIn("What changed (6 files, 7 insertions, 1 deletions",
                      self.text)
        self.assertIn("In scope: 2 files, 2 insertions, 0 deletions "
                      "(machine-computed: the span less 1 path(s) generated "
                      "by declaration and 3 matched by excluded_paths)",
                      self.text)
        for line in ("  - `docs/` (matches 1 span path(s))",
                     "  - `*.txt` (matches 2 span path(s))",
                     "  - `notes/n.txt` (matches 1 span path(s))"):
            self.assertIn(line, self.text)
        # The machine total is still the FIRST diff shape in the body: a
        # 0.24.x `take` recomputes against exactly that one.
        from review.validate import _DIFF_SHAPE_RE
        self.assertEqual(_DIFF_SHAPE_RE.search(self.text).groups(),
                         ("6", "7", "1"))

    def test_the_scoped_diff_runs_and_prints_only_scoped_paths(self):
        line = next(ln for ln in self.text.splitlines()
                    if ln.startswith("Scoped:"))
        command = line.split(":", 1)[1].strip()
        self.assertTrue(command.startswith(paths.diff_command(
            self.loop.repo.resolve(), self.base,
            self.loop.git("rev-parse", "HEAD"))
            + " -- "), command)
        run = subprocess.run(["/bin/sh", "-c", command], cwd="/",
                             capture_output=True, text=True, timeout=60,
                             stdin=subprocess.DEVNULL)
        self.assertEqual(run.returncode, 0, run.stderr)
        shown = set(re.findall(r"^diff --git a/(\S+) ", run.stdout, re.M))
        self.assertEqual(shown, {"src/a.py", "src/b.py", "docs/x.md",
                                 "gen/out.json"})

    def test_no_scope_paths_no_scoped_line(self):
        loop = ScratchLoop(self, taxonomy_toml(("High",), ("High",)))
        out = loop.tmp / "plain.md"
        code, payload = loop.loupe("emit-request", "--claim-file",
                                   loop.claim(), "--base", loop.base,
                                   "--out", out, "--local-only")
        self.assertEqual(code, 0, payload)
        text = out.read_text(encoding="utf-8")
        self.assertNotIn("Scoped:", text)
        self.assertNotIn("In scope:", text)

    def test_observations_are_not_attestations(self):
        evidence = self.text.split("## Evidence", 1)[1].split("## ", 1)[0]
        self.assertNotIn("pytest -q", evidence)
        claim_part = self.text.split("## Claim", 1)[1].split("## ", 1)[0]
        self.assertIn("Author-typed observations — typed by the author, NOT "
                      "this hand-off's attestations; this tool ran and "
                      "checked none of them:", claim_part)
        self.assertIn("- `pytest -q` → 3 passed — context: author laptop, "
                      "before the rebase", claim_part)

    def test_the_request_precis_carries_counts_and_notices(self):
        code, payload = self.loop.loupe("brief", self.out)
        self.assertEqual(code, 0, payload)
        self.assertIn("; in scope: 2 files, 2 insertions, 0 deletions",
                      payload["brief"])
        self.assertIn("- **Claim — objective path(s) that match no changed "
                      "file (1)**", payload["brief"])

    def test_the_request_still_validates_and_keeps_its_sections(self):
        code, payload = self.loop.loupe("validate", self.out)
        self.assertEqual(code, 0, payload)
        from review.validate import REQUIRED_REQUEST_SECTIONS
        from review.wire import section_key
        headings = [section_key(h) for h in
                    re.findall(r"^## (.+?)\s*$", self.text, re.M)]
        self.assertEqual([h for h in headings
                          if h in REQUIRED_REQUEST_SECTIONS],
                         list(REQUIRED_REQUEST_SECTIONS))

    def test_scope_report_accounts_for_patterns(self):
        """Nothing in the span is unnamed: `src/` (scope and objective),
        `docs/`, `*.txt`, `notes/n.txt` and `gen/*.json` account for every
        path through the one matcher."""
        from review.validate import scope_gaps
        span = ["src/a.py", "src/b.py", "docs/x.md", "gen/out.json",
                "other.txt", "notes/n.txt", "unrelated.md"]
        self.assertEqual(scope_gaps(self.body, span), ["unrelated.md"])


# ------------------------------------ round-2 F3: the scoped diff's paths

_C_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13,
              '"': 34, "\\": 92}


def _c_unquote(token: str) -> str:
    """git's C-quoted path (`"a/\\\\x"`, `"a/caf\\303\\251"`) as the path."""
    body, out, i = token[1:-1], bytearray(), 0
    while i < len(body):
        if body[i] == "\\":
            nxt = body[i + 1]
            if nxt in "01234567":
                out.append(int(body[i + 1:i + 4], 8))
                i += 4
                continue
            out.append(_C_ESCAPES[nxt])
            i += 2
            continue
        out += body[i].encode("utf-8")
        i += 1
    return out.decode("utf-8")


def _header_sides(rest: str) -> tuple[str, str]:
    """The two paths of one `diff --git <a> <b>` header, prefixes dropped.
    An unquoted pair of equal paths is split at its middle, which is exact
    for a name with a space; a rename's unquoted pair splits at ` b/`."""
    sides: list[str] = []
    while rest:
        if rest.startswith('"'):
            j = 1
            while rest[j] != '"':
                j += 2 if rest[j] == "\\" else 1
            sides.append(_c_unquote(rest[:j + 1]))
            rest = rest[j + 1:].lstrip(" ")
        elif sides:
            sides.append(rest)
            rest = ""
        elif ' "' in rest:
            first, rest = rest.split(' "', 1)
            sides.append(first)
            rest = '"' + rest
        else:
            half = (len(rest) - 1) // 2
            a, b = rest[:half], rest[half + 1:]
            if a[2:] != b[2:]:
                a, b = rest.split(" b/", 1)
                b = "b/" + b
            sides += [a, b]
            rest = ""
    a, b = sides
    assert a.startswith("a/") and b.startswith("b/"), (a, b)
    return a[2:], b[2:]


def scoped_line(text: str) -> str:
    return next(ln for ln in text.splitlines()
                if ln.startswith("Scoped:")).split(":", 1)[1].strip()


def run_line(command: str) -> str:
    """EXECUTE a `Scoped:` command as printed; its output."""
    run = subprocess.run(["/bin/sh", "-c", command], cwd="/",
                         capture_output=True, timeout=120,
                         stdin=subprocess.DEVNULL)
    if run.returncode != 0:
        raise AssertionError(run.stderr.decode("utf-8", "replace"))
    return run.stdout.decode("utf-8")


def header_paths(output: str) -> set[str]:
    """Every path the `diff --git` headers of `output` name, either side."""
    shown: set[str] = set()
    for line in output.split("\n"):
        if line.startswith("diff --git "):
            shown.update(_header_sides(line[len("diff --git "):]))
    return shown


class Span:
    """One span the `Scoped:` line is judged over, IN-PROCESS: the shape
    `emit_request` computes for it (`emit.diff_shape`, whose second read
    carries each rename's source) and the line `emit._scoped_stamp`
    renders from that shape with the call site's renderer. Built once per
    span and only read."""

    def __init__(self, loop, base):
        self.base = base
        self.repo = config.find_repo_root(loop.repo)
        self.head = loop.git("rev-parse", "HEAD")
        self.shape = emit.diff_shape(self.repo, base, self.head)

    def line(self, scope) -> str:
        """The `Scoped:` value `emit_request` renders for `scope`."""
        stamp = emit._scoped_stamp(
            lambda *specs: paths.diff_command(self.repo, self.base,
                                              self.head, *specs),
            scope, self.shape)
        return stamp.split(":", 1)[1].strip()

    def table(self, objectives) -> str:
        """The Objectives block `emit_request` renders for `objectives`
        over this span (no path of these spans is generated)."""
        return "\n".join(emit._objectives_block(objectives, self.shape,
                                                set(), ()))


class TestScopedMatchesInScope(_Loop):
    """Round-2 F3 FALSIFICATION. Mutation: hand the claim's own patterns to
    git again (the pre-fix `_pathspecs`) and the `[^a]`, exact-directory,
    POSIX-class, backslash, file-to-directory and rename-source rows go
    red while every agreeing control stays green."""

    #: Changed in the partition span, beside the loop's own `f.txt`.
    ADDED = ("a.txt", "b.txt", "^.txt", "1.txt", "d].txt", "[a].txt",
             "*.txt", "?.txt", "\\a.txt", "sp ace.txt", "[a.txt", "Up.TXT",
             "src/x.txt", "src/deep/y.py", "docs/x.md")

    #: (row, scope_paths). The expectation of every row is the same
    #: equality; the comment says which set `in_scope` holds.
    ROWS = (
        ("exact file", ["a.txt"]),
        ("exact file in a directory", ["docs/x.md"]),
        ("exact name of a directory only", ["src"]),              # none
        ("exact file that became a directory", ["d"]),           # d only
        ("exact directory that became a file", ["e"]),           # e only
        ("exact file and one child", ["d", "d/x"]),
        ("prefix", ["src/"]),
        ("nested prefix", ["src/deep/"]),
        ("prefix over a file that became a directory", ["d/"]),  # d/x, d/y
        ("prefix matching nothing beside one that matches",
         ["nothing/", "docs/"]),
        ("star crosses /", ["*.txt"]),
        ("star inside a directory", ["src/*"]),
        ("question mark", ["?.txt"]),
        ("bracket set", ["[ab].txt"]),
        ("bracket range", ["[a-c].txt"]),
        ("bracket caret", ["[^a].txt"]),                  # ^.txt, a.txt
        ("bracket bang", ["[!a].txt"]),
        ("POSIX class", ["[[:digit:]].txt"]),             # d].txt only
        ("backslash", ["\\*.txt"]),                       # \a.txt only
        ("literal star name", ["*.txt", "src/"]),
        ("literal bracket name", ["[a].txt"]),            # [a].txt, a.txt
        ("unclosed bracket name", ["[a.txt"]),
        ("space in a name", ["sp ace.txt"]),
        ("case: lower exact against Up.TXT", ["up.txt"]),       # none
        ("case: upper glob", ["*.TXT"]),
        ("case: exact", ["Up.TXT"]),
        ("rename target", ["new/r.txt"]),
        ("rename target by glob", ["*/r.txt"]),
        ("rename source only", ["old/r.txt"]),                  # none
        ("rename source prefix", ["old/"]),                     # none
        ("empty match", ["nothing/"]),                          # none
        ("mixed", ["src/", "*.md", "d", "[^a].txt"]),
    )
    #: The partition rows ALSO run through the real `emit-request`: a
    #: command that holds a caret, an exact file that became a directory
    #: (with its exclusions), a prefix and a glob — and the `none` line.
    END_TO_END_ROWS = ("mixed", "empty match")

    #: The reviewer's reproductions, each in a span of its own.
    REPRODUCED = (
        ("[^a].txt diverged", ("a.txt", "b.txt", "^.txt"), ["[^a].txt"]),
        ("[!a].txt agreed", ("a.txt", "b.txt", "^.txt"), ["[!a].txt"]),
        ("a.txt agreed", ("a.txt", "b.txt", "^.txt"), ["a.txt"]),
        ("exact src diverged", ("src/x.txt",), ["src"]),
        ("src/ agreed", ("src/x.txt",), ["src/"]),
    )
    #: The reproduction ALSO run through the real `emit-request`.
    END_TO_END_REPRODUCED = "[^a].txt diverged"

    @classmethod
    def setUpClass(cls):
        loop = cls.loop = ScratchLoop(_ClassCase(cls),
                                      taxonomy_toml(("High",), ("High",)))
        # Before the span: a file to rename (long enough to be detected),
        # a file `d` that becomes a directory, a directory `e` that becomes
        # a file.
        loop.write("old/r.txt", "".join(f"line {i}\n" for i in range(40)))
        loop.write("d", "a file\n")
        loop.write("e/x", "below e\n")
        loop.git("add", "old/r.txt", "d", "e/x")
        loop.git("commit", "-q", "-m", "tree")
        cls.base = loop.git("rev-parse", "HEAD")
        (loop.repo / "new").mkdir()
        loop.git("mv", "old/r.txt", "new/r.txt")
        loop.git("rm", "-q", "--", "d", "e/x")
        for rel in cls.ADDED + ("d/x", "d/y", "e"):
            loop.write(rel, f"{rel}\n")
        loop.git("add", "--", *(f":(literal){p}"
                                for p in cls.ADDED + ("d/x", "d/y", "e")))
        loop.git("commit", "-q", "-m", "span")
        cls.span = cls.changed(loop, cls.base)
        cls.view = Span(loop, cls.base)
        # The partition is only as good as its span: the rename is a
        # rename, and both transitions changed both of their paths.
        status = loop.git("diff", "--name-status", f"{cls.base}...HEAD")
        assert re.search(r"^R\d+\told/r\.txt\tnew/r\.txt$", status, re.M), \
            status
        assert {"d", "d/x", "d/y", "e", "e/x"} <= set(cls.span), cls.span

    @staticmethod
    def changed(loop, base) -> list[str]:
        """The span's changed paths as `--name-only -z` lists them: raw,
        post-image, with git's default rename detection — the rows the
        emitter matches."""
        raw = subprocess.run(
            ["git", "-C", str(loop.repo), "diff", "--name-only", "-z",
             f"{base}...HEAD"], check=True, capture_output=True,
            stdin=subprocess.DEVNULL).stdout.decode("utf-8")
        return [p for p in raw.split("\0") if p]

    @staticmethod
    def run_emit(loop, base, scope, name, **members):
        """END TO END: (exit, payload, the `Scoped:` value, the request)
        of one real `emit-request`, in a state directory of its own, so it
        shares the span and nothing else."""
        out = loop.tmp / f"{name}.md"
        argv = ["emit-request", "--claim-file",
                loop.claim(claim(scope_paths=scope, **members),
                           name=f"{name}.json"),
                "--base", base, "--out", out, "--local-only"]
        cmd = ([str(SHIM)] if SHIM.is_file()
               else [sys.executable, "-m", "review"])
        proc = subprocess.run(cmd + [str(a) for a in argv],
                              cwd=str(loop.repo),
                              env=cli_env(loop.tmp / f"state-{name}"),
                              capture_output=True, text=True, timeout=300,
                              stdin=subprocess.DEVNULL)
        if proc.returncode != 0:
            return proc.returncode, proc.stdout + proc.stderr, None, None
        text = out.read_text(encoding="utf-8")
        return 0, None, scoped_line(text), text

    def emit(self, loop, base, scope, name) -> str:
        code, payload, line, _text = self.run_emit(loop, base, scope, name)
        self.assertEqual(code, 0, payload)
        return line

    def assert_end_to_end(self, loop, base, scope, name, view, **members):
        """The real `emit-request` prints the line the in-process row
        judged. Returns the request."""
        code, payload, line, text = self.run_emit(loop, base, scope, name,
                                                  **members)
        self.assertEqual(code, 0, payload)
        self.assertEqual(line, view.line(scope))
        return text

    def assert_agrees(self, span, scope, line) -> tuple[str, str | None]:
        """The row's whole assertion, in two named checks. `set`: the paths
        the line SELECTS — every path the executed command's headers name,
        none for a line that is not a command — are exactly `in_scope`'s.
        `form`: a non-empty set is a command handing git only `:(literal)`
        pathspecs; an empty one is the `none` line, never a command.
        Returns (the line, the executed command's output or None)."""
        want = {p for p in span if emit.in_scope(p, scope)}
        command = line.startswith("git ")
        output = run_line(line) if command else None
        with self.subTest(check="set"):
            self.assertEqual(header_paths(output) if command else set(),
                             want, line[:300])
        with self.subTest(check="form"):
            if want:
                self.assertTrue(command, line)
                for spec in shlex.split(line.split(" -- ", 1)[1]):
                    self.assertRegex(spec, r"^:\((exclude,)?literal\)")
            else:
                self.assertTrue(line.startswith("none — "), line)
        return line, output

    def test_the_partition(self):
        """Each row's selected set equals `in_scope`'s, and no pattern
        reaches git: every pathspec it hands over is literal."""
        for row, scope in self.ROWS:
            with self.subTest(row=row):
                self.assert_agrees(self.span, scope, self.view.line(scope))
        rows = dict(self.ROWS)
        for i, row in enumerate(self.END_TO_END_ROWS):
            with self.subTest(row=row, path="end-to-end"):
                self.assert_end_to_end(self.loop, self.base, rows[row],
                                       f"row{i}", self.view)

    def test_the_rows_hold_the_sets_they_claim(self):
        """The partition's own bookkeeping, so a row cannot pass by testing
        nothing: the divergent rows hold the set only `in_scope` gives."""
        def matched(scope):
            return {p for p in self.span if emit.in_scope(p, scope)}
        self.assertEqual(matched(["[^a].txt"]), {"^.txt", "a.txt"})
        self.assertEqual(matched(["[[:digit:]].txt"]), {"d].txt"})
        self.assertEqual(matched(["\\*.txt"]), {"\\a.txt"})
        self.assertEqual(matched(["[a].txt"]), {"[a].txt", "a.txt"})
        self.assertEqual(matched(["d"]), {"d"})
        self.assertEqual(matched(["e"]), {"e"})
        self.assertEqual(matched(["d/"]), {"d/x", "d/y"})
        for scope in (["src"], ["up.txt"], ["old/r.txt"], ["old/"],
                      ["nothing/"]):
            self.assertEqual(matched(scope), set(), scope)
        self.assertLessEqual(set(self.END_TO_END_ROWS), set(dict(self.ROWS)))

    def test_the_command_is_literal_and_says_how(self):
        """Every pathspec is `:(literal)`; a prefix stays one word; a path
        git would also select below a matched FILE is excluded by name."""
        line, _out = self.assert_agrees(self.span, ["src/", "d"],
                                        self.view.line(["src/", "d"]))
        specs = shlex.split(line.split(" -- ", 1)[1])
        self.assertEqual(specs, [":(literal)src/", ":(literal)d",
                                 ":(exclude,literal)d/x",
                                 ":(exclude,literal)d/y"])

    def assert_reproduced(self, span, scope, line, table) -> None:
        """A reproduction's row: the line agrees with `in_scope`, and the
        objective table the reviewer read maps exactly what the command
        they ran selects."""
        selected, output = self.assert_agrees(span, scope, line)
        row_md = re.search(r"^\| the scope \| [^|]+ \| ([^|]+) \|", table,
                           re.M)
        mapped = set(row_md[1].strip().split(", ")) - {"(none)"}
        with self.subTest(check="table"):
            self.assertEqual(mapped, header_paths(output)
                             if selected.startswith("git ") else set())

    def test_the_reviewers_reproductions_are_paired_controls(self):
        spans: dict = {}
        for i, (row, files, scope) in enumerate(self.REPRODUCED):
            with self.subTest(row=row):
                if files not in spans:
                    loop = ScratchLoop(self, taxonomy_toml(("High",),
                                                           ("High",)))
                    for rel in files:
                        loop.write(rel, f"{rel}\n")
                    loop.git("add", "--", *files)
                    loop.git("commit", "-q", "-m", "span")
                    spans[files] = (loop, self.changed(loop, loop.base),
                                    Span(loop, loop.base))
                loop, span, view = spans[files]
                # As reproduced: the same patterns as scope_paths AND as an
                # objective's paths, so the table the reviewer read and the
                # command they ran are compared on one request.
                objectives = [{"title": "the scope", "paths": scope}]
                self.assert_reproduced(span, scope, view.line(scope),
                                       view.table(objectives))
                if row == self.END_TO_END_REPRODUCED:
                    with self.subTest(path="end-to-end"):
                        text = self.assert_end_to_end(
                            loop, loop.base, scope, f"rep{i}", view,
                            objectives=objectives)
                        self.assert_reproduced(span, scope,
                                               scoped_line(text), text)
        self.assertIn(self.END_TO_END_REPRODUCED,
                      [r for r, _f, _s in self.REPRODUCED])

    def test_size_a_prefix_is_one_word_and_a_long_list_is_withheld(self):
        loop = ScratchLoop(self, taxonomy_toml(("High",), ("High",)))
        names = [f"big/entry-{i:04d}-{'x' * 32}.dat" for i in range(1400)]
        for rel in names:
            loop.write(rel, "b\n")
        loop.git("add", "big")
        loop.git("commit", "-q", "-m", "a large span")
        span = self.changed(loop, loop.base)
        view = Span(loop, loop.base)
        # A prefix over every one of them: one pathspec, and it runs.
        line, _out = self.assert_agrees(span, ["big/"], view.line(["big/"]))
        self.assertEqual(line.split(" -- ", 1)[1], "':(literal)big/'")
        # A glob over 100 of them names each, and still runs.
        self.assert_agrees(span, ["big/entry-00*"],
                           view.line(["big/entry-00*"]))
        # 900 of them: ~58 KB, under the bound, and `/bin/sh -c` still
        # runs it (1,000 measured 64,244 bytes with a long TMPDIR — too
        # close to the bound to be a stable row).
        line, _out = self.assert_agrees(span, ["big/entry-0[0-8]*"],
                                        view.line(["big/entry-0[0-8]*"]))
        self.assertGreater(len(line.encode("utf-8")), 50_000)
        # A glob over all 1,400: over the bound, withheld — never a
        # shortened list, and never the whole span. In-process, and
        # through the real `emit-request`, which prints the same line.
        for how, line in (("in-process", view.line(["big/*.dat"])),
                          ("end-to-end", self.emit(loop, loop.base,
                                                   ["big/*.dat"], "all"))):
            with self.subTest(path=how):
                self.assertTrue(line.startswith(
                    "withheld — the claim's scope_paths match 1400 changed "
                    "paths"), line)
                self.assertIn(f"over the {emit.SCOPED_COMMAND_MAX}-byte "
                              f"bound", line)
                self.assertNotIn("git ", line)
                self.assertEqual(line, view.line(["big/*.dat"]))

    def test_unreadable_raw_paths_are_withheld(self):
        """`_numstat_entries` returns no rows when its two reads disagree;
        the display spellings then name no file, so no command is given.
        Not reachable through the CLI without breaking git itself."""
        shape = {"file_list": ['"caf\\303\\251.txt"'], "entries": []}
        line = emit._scoped_stamp(lambda *specs: self.fail(specs),
                                  ["*.txt"], shape)
        self.assertEqual(line, "Scoped: withheld — the span's raw paths "
                               "could not be read, so no command can name "
                               "the in-scope paths exactly")

    # --------------------------------- round-3 F1: both ends of every rename
    #
    # Mutation (named): compress a matched prefix without accounting for
    # rename sources — the round-2 `_pathspecs` — and every row whose
    # selected region holds a source goes red on `set`, the reviewer's
    # outgoing-prefix reproduction among them, while its exact-file and
    # destination-prefix controls stay green.

    #: (old path, new path) of every rename in the rename span. Each file
    #: carries forty lines of its own, so git pairs exactly these.
    RENAMES = (
        ("old/r.txt", "new/r.txt"),        # out of old/, beside an edit
        ("out/a.txt", "land/a.txt"),       # out of a directory it empties
        ("from/b.txt", "in/b.txt"),        # into in/, beside an added file
        ("mv/c.txt", "mv/c2.txt"),         # within mv/, alone
        ("mv2/d.txt", "mv2/d2.txt"),       # within mv2/, beside an edit
        ("nest/sub/e.txt", "nest/e.txt"),  # out of nest/sub/, within nest/
        ("old2/x", "away/x"),              # its old name becomes a directory
        ("dd/x", "moved/x"),               # empties dd/, which becomes a file
        ("srcx/y.txt", "rp/y.txt"),        # into rp/, beside a like deletion
        ("q/caf é.txt", "z/caf é.txt"),    # a space and a non-ASCII letter
    )

    #: (row, scope_paths, the set `in_scope` holds over the rename span —
    #: None for the whole span).
    RENAME_ROWS = (
        # Outgoing: the source lies in the scope, the target does not.
        ("outgoing, prefix, with a matched file", ["old/"],
         {"old/keep.txt"}),
        ("outgoing, prefix, nothing else", ["out/"], set()),
        ("outgoing, exact source", ["old/r.txt"], set()),
        ("outgoing, exact matched file", ["old/keep.txt"], {"old/keep.txt"}),
        ("outgoing, glob, with a matched file", ["old/*"], {"old/keep.txt"}),
        ("outgoing, glob, nothing else", ["out/*"], set()),
        ("outgoing, nested prefix", ["nest/sub/"], {"nest/sub/keep.txt"}),
        ("outgoing, the source became a directory", ["old2/"],
         {"old2/x/child", "old2/y.txt"}),
        ("outgoing, nested prefixes over that directory",
         ["old2/", "old2/x/"], {"old2/x/child", "old2/y.txt"}),
        ("outgoing, exact file where a rename emptied a directory", ["dd"],
         {"dd"}),
        ("outgoing, glob file where a rename emptied a directory", ["d?"],
         {"dd"}),
        ("outgoing, a source named with a space and a non-ASCII letter",
         ["q/"], {"q/keep.txt"}),
        # Incoming: the target lies in the scope, the source does not.
        ("incoming, prefix, nothing else", ["new/"], {"new/r.txt"}),
        ("incoming, exact target", ["new/r.txt"], {"new/r.txt"}),
        ("incoming, glob, nothing else", ["new/*"], {"new/r.txt"}),
        ("incoming, glob crossing /", ["*/r.txt"], {"new/r.txt"}),
        ("incoming, prefix, with a matched file", ["in/"],
         {"in/b.txt", "in/own.txt"}),
        ("incoming, glob, with a matched file", ["in/*"],
         {"in/b.txt", "in/own.txt"}),
        ("incoming, beside a matched deletion git pairs it with", ["rp/"],
         {"rp/d.txt", "rp/y.txt"}),
        ("incoming, a target named with a space and a non-ASCII letter",
         ["z/"], {"z/caf é.txt"}),
        # Within: both ends lie under one entry.
        ("within, prefix, nothing else", ["mv/"], {"mv/c2.txt"}),
        ("within, exact target", ["mv/c2.txt"], {"mv/c2.txt"}),
        ("within, exact source", ["mv/c.txt"], set()),
        ("within, prefix, with a matched file", ["mv2/"],
         {"mv2/d2.txt", "mv2/own.txt"}),
        ("within, glob, with a matched file", ["mv2/*"],
         {"mv2/d2.txt", "mv2/own.txt"}),
        ("within, the outer prefix of a nested move", ["nest/"],
         {"nest/e.txt", "nest/sub/keep.txt"}),
        # Both ends selected, by two entries.
        ("both ends, two prefixes", ["old/", "new/"],
         {"old/keep.txt", "new/r.txt"}),
        ("both ends, a prefix and the exact target", ["old/", "new/r.txt"],
         {"old/keep.txt", "new/r.txt"}),
        ("both ends, a prefix and a glob", ["old/", "*/r.txt"],
         {"old/keep.txt", "new/r.txt"}),
        ("every changed path, by glob", ["*"], None),
    )
    #: The rename row ALSO run through the real `emit-request`: the prefix
    #: that is named path by path, not compressed.
    END_TO_END_RENAME = "outgoing, the source became a directory"

    #: The exact pathspecs of the rows whose form carries the fix.
    RENAME_FORMS = (
        ("outgoing, prefix, with a matched file",
         [":(literal)old/", ":(exclude,literal)old/r.txt"]),
        ("outgoing, the source became a directory",
         [":(literal)old2/x/child", ":(literal)old2/y.txt"]),
        ("outgoing, nested prefixes over that directory",
         [":(literal)old2/x/", ":(literal)old2/y.txt"]),
        ("outgoing, exact file where a rename emptied a directory",
         [":(literal)dd", ":(exclude,literal)dd/x"]),
        ("within, prefix, nothing else",
         [":(literal)mv/", ":(exclude,literal)mv/c.txt"]),
        ("outgoing, a source named with a space and a non-ASCII letter",
         [":(literal)q/", ":(exclude,literal)q/caf é.txt"]),
        ("both ends, two prefixes",
         [":(literal)old/", ":(literal)new/",
          ":(exclude,literal)old/r.txt"]),
    )

    #: Edited on both sides of the rename span; `in/own.txt`,
    #: `old2/x/child` and the file `dd` are added by it.
    EDITED = ("old/keep.txt", "nest/sub/keep.txt", "mv2/own.txt",
              "old2/y.txt", "q/keep.txt")

    def rename_span(self):
        """(loop, base, span) of a span holding `RENAMES`, the edits, the
        additions and one deletion, `rp/d.txt`, that shares 28 of its 40
        lines with `rp/y.txt`: in the whole span `y` is an exact rename,
        and once its source is out of view git pairs `d` with it."""
        loop = ScratchLoop(self, taxonomy_toml(("High",), ("High",)))
        for old, _new in self.RENAMES:
            loop.write(old, "".join(f"{old} line {i}\n" for i in range(40)))
        loop.write("rp/d.txt",
                   "".join(f"srcx/y.txt line {i}\n" for i in range(28))
                   + "".join(f"rp/d.txt own {i}\n" for i in range(12)))
        for rel in self.EDITED:
            loop.write(rel, "before\n")
        loop.git("add", "--", *(old for old, _new in self.RENAMES),
                 "rp/d.txt", *self.EDITED)
        loop.git("commit", "-q", "-m", "before the renames")
        base = loop.git("rev-parse", "HEAD")
        for old, new in self.RENAMES:
            (loop.repo / new).parent.mkdir(parents=True, exist_ok=True)
            loop.git("mv", old, new)
        loop.git("rm", "-q", "--", "rp/d.txt")
        if (loop.repo / "dd").is_dir():
            (loop.repo / "dd").rmdir()     # `git mv` leaves it, emptied
        for rel in self.EDITED:
            loop.write(rel, "after\n")
        added = {"in/own.txt": "added\n", "old2/x/child": "below x\n",
                 "dd": "a file where a directory was\n"}
        for rel, text in added.items():
            loop.write(rel, text)
        loop.git("add", "--", *self.EDITED, *added)
        loop.git("commit", "-q", "-m", "the renames")
        # The partition is only as good as its span: every rename is the
        # rename it names, and the deletion is a deletion.
        status = self.name_status(loop, base)
        for old, new in self.RENAMES:
            assert ("R100", old, new) in status, (old, new, status)
        assert ("D", "rp/d.txt") in status, status
        return loop, base, self.changed(loop, base)

    @staticmethod
    def name_status(loop, base) -> set[tuple]:
        """The span's `--name-status -z` rows as (status, path, ...), raw:
        a rename or copy carries both of its paths."""
        raw = subprocess.run(
            ["git", "-C", str(loop.repo), "diff", "--name-status", "-z",
             f"{base}...HEAD"], check=True, capture_output=True,
            stdin=subprocess.DEVNULL).stdout.decode("utf-8")
        tokens, rows = [t for t in raw.split("\0") if t], set()
        while tokens:
            status = tokens.pop(0)
            width = 2 if status[0] in "RC" else 1
            rows.add((status, *tokens[:width]))
            del tokens[:width]
        return rows

    def assert_rename_treatment(self, output: str | None, want: set) -> None:
        """The treatment the comparison states, read off the EXECUTED
        output: an in-scope rename target is never paired with its own
        source; unpaired, it is a whole-file addition, and paired, it is
        paired with another in-scope path, as git's own rename detection
        does among the paths it was given."""
        if output is None:
            return
        source_of = {new: old for old, new in self.RENAMES}
        for block in re.split(r"^(?=diff --git )", output, flags=re.M):
            if not block.startswith("diff --git "):
                continue
            a, b = _header_sides(block.split("\n", 1)[0][len("diff --git "):])
            if b not in source_of:
                continue
            self.assertNotEqual(a, source_of[b], block[:300])
            if a == b:
                self.assertIn("\nnew file mode ", block, block[:300])
            else:
                self.assertIn(a, want, block[:300])

    def test_the_rename_partition(self):
        """Every row's selected set equals `in_scope`'s over the rename
        span, the command it runs shows no rename source, and the rows
        that carry the fix have the exact form it gives them."""
        loop, base, span = self.rename_span()
        view = Span(loop, base)
        lines = {}
        for row, scope, held in self.RENAME_ROWS:
            with self.subTest(row=row):
                want = {p for p in span if emit.in_scope(p, scope)}
                with self.subTest(check="held"):
                    self.assertEqual(want, set(span) if held is None
                                     else held)
                line, output = self.assert_agrees(span, scope,
                                                  view.line(scope))
                with self.subTest(check="treatment"):
                    self.assert_rename_treatment(output, want)
                lines[row] = line
        for row, specs in self.RENAME_FORMS:
            with self.subTest(row=row, check="specs"):
                self.assertEqual(
                    shlex.split(lines[row].split(" -- ", 1)[1]), specs)
        row = self.END_TO_END_RENAME
        with self.subTest(row=row, path="end-to-end"):
            self.assert_end_to_end(loop, base, dict(
                (r, s) for r, s, _h in self.RENAME_ROWS)[row], "rename",
                view)

    def test_the_reviewers_rename_reproduction_and_its_controls(self):
        """Round-3 F1 as reproduced: `old/r.txt → new/r.txt` beside an edit
        of `old/keep.txt`. The outgoing prefix selected the deletion of
        `old/r.txt` too; now it selects only `old/keep.txt`. The exact-file
        and destination-prefix controls agreed before the fix and still
        do, and both prefixes together select the edit and the target —
        never the source. The diverged row also runs through the real
        `emit-request`."""
        loop = ScratchLoop(self, taxonomy_toml(("High",), ("High",)))
        loop.write("old/r.txt", "".join(f"line {i}\n" for i in range(40)))
        loop.write("old/keep.txt", "before\n")
        loop.git("add", "--", "old/r.txt", "old/keep.txt")
        loop.git("commit", "-q", "-m", "base")
        base = loop.git("rev-parse", "HEAD")
        (loop.repo / "new").mkdir()
        loop.git("mv", "old/r.txt", "new/r.txt")
        loop.write("old/keep.txt", "after\n")
        loop.git("add", "--", "old/keep.txt")
        loop.git("commit", "-q", "-m", "head")
        span = self.changed(loop, base)
        self.assertEqual(span, ["new/r.txt", "old/keep.txt"])
        view = Span(loop, base)
        rows = (
            ("outgoing prefix (diverged)", ["old/"], {"old/keep.txt"},
             [":(literal)old/", ":(exclude,literal)old/r.txt"]),
            ("exact file (agreed)", ["old/keep.txt"], {"old/keep.txt"},
             [":(literal)old/keep.txt"]),
            ("destination prefix (agreed)", ["new/"], {"new/r.txt"},
             [":(literal)new/"]),
            ("both prefixes", ["old/", "new/"], {"old/keep.txt", "new/r.txt"},
             [":(literal)old/", ":(literal)new/",
              ":(exclude,literal)old/r.txt"]),
        )
        for row, scope, held, specs in rows:
            with self.subTest(row=row):
                with self.subTest(check="held"):
                    self.assertEqual(
                        {p for p in span if emit.in_scope(p, scope)}, held)
                line, _out = self.assert_agrees(span, scope,
                                                view.line(scope))
                with self.subTest(check="specs"):
                    self.assertEqual(shlex.split(line.split(" -- ", 1)[1]),
                                     specs)
        row, scope = rows[0][:2]
        with self.subTest(row=row, path="end-to-end"):
            self.assert_end_to_end(loop, base, scope, "reproduced", view)

    def test_a_copy_adds_no_path_the_command_could_select(self):
        """With `diff.renames = copies` the span also lists copies, and a
        copy's source stays at the target. Git's tree walk visits that
        source only when it changed, and then `in_scope` judges it under
        its own name — so a copy's two ends are both judged, or the source
        is not in the walk at all. Every row agrees; the copy target of an
        unchanged source is an addition, since plain copy detection reads
        only changed sources. Both prefixes together also run through the
        real `emit-request`, whose span reads the same configuration."""
        loop = ScratchLoop(self, taxonomy_toml(("High",), ("High",)))
        loop.git("config", "diff.renames", "copies")
        source = "".join(f"cp/src.txt line {i}\n" for i in range(40))
        still = "".join(f"cp/still.txt line {i}\n" for i in range(40))
        loop.write("cp/src.txt", source)
        loop.write("cp/still.txt", still)
        loop.write("cp/keep.txt", "before\n")
        loop.git("add", "--", "cp")
        loop.git("commit", "-q", "-m", "before the copies")
        base = loop.git("rev-parse", "HEAD")
        loop.write("cp2/copy.txt", source)
        loop.write("cp2/still-copy.txt", still)
        loop.write("cp/src.txt", source.replace("line 39", "line thirty-nine"))
        loop.write("cp/keep.txt", "after\n")
        loop.git("add", "--", "cp", "cp2")
        loop.git("commit", "-q", "-m", "the copies")
        status = self.name_status(loop, base)
        self.assertIn(("C100", "cp/src.txt", "cp2/copy.txt"), status)
        self.assertIn(("A", "cp2/still-copy.txt"), status)
        span = self.changed(loop, base)
        view = Span(loop, base)
        both = {"cp/keep.txt", "cp/src.txt"}
        copies = {"cp2/copy.txt", "cp2/still-copy.txt"}
        for scope, held in (
                (["cp/"], both), (["cp2/"], copies), (["cp/", "cp2/"],
                                                      both | copies),
                (["cp2/*"], copies), (["cp/src.txt"], {"cp/src.txt"}),
                (["cp/still.txt"], set())):
            with self.subTest(scope=scope):
                with self.subTest(check="held"):
                    self.assertEqual(
                        {p for p in span if emit.in_scope(p, scope)}, held)
                self.assert_agrees(span, scope, view.line(scope))
        with self.subTest(scope=["cp/", "cp2/"], path="end-to-end"):
            self.assert_end_to_end(loop, base, ["cp/", "cp2/"], "copies",
                                   view)


if __name__ == "__main__":
    unittest.main()

"""The claim's 0.25.0 members (public issue #4, brief `take-objective-map`).

A FOURTH member kind — a list of objects, each member with its own closed
field table (`vocab.CLAIM_OBJECT_LIST_FIELDS`) — and five members:
`carried_findings`, `objectives`, `observations`, `attestation_map` of that
kind, and `excluded_paths` in the `scope_paths` grammar. Everything they
render is ADDITIVE: no wrapper attribute, no required section moved, the
attestation fence untouched.

THE PARTITION, all through the real CLI in scratch repositories with their
own configuration and state directory:

  TestGrammarPartition — per member: absent (control), empty list, a
    non-list, a non-object element, an unknown field, each required field
    missing, each field of each wrong type and grammar, a duplicated
    carried fingerprint; each refusal names its member path and leaves the
    repository's refs, index and worktree byte-identical (a DIRTY tree, so
    a commit would show). The cases are DERIVED from the field tables, so
    a field added there joins the partition.
  TestRecordBound — the ledger-bound checks: a carried fingerprint absent
    from an origin verdict this ledger holds (refused, before anything is
    committed) against one this ledger does not hold (stated, emitted);
    `required` read from the kept origin verdict, and a typed one that
    differs; fix commits inside and outside the span; an attestation-map
    row resolving to the previous round's standing disposition, to a
    carried entry, to neither (refused), and naming a gate the governing
    manifest lacks (refused at emission, before any gate runs).
  TestSpanRendering — objectives mapped against the span, the unmapped
    changed files and the objective paths matching nothing; excluded paths
    as a prefix, a glob and an exact path; generated paths subtracted from
    the in-scope counts; the scoped diff stamp RUN, printing only scoped
    paths; observations rendered outside the attestation block; and the
    request précis carrying the in-scope counts and the notices.
  TestScopedMatchesInScope — round-2 F3: the `Scoped:` command selects
    EXACTLY the changed paths `emit.in_scope` selects over the same span.
    Each row runs the real `emit-request`, EXECUTES the emitted command
    through `/bin/sh` and compares every path its `diff --git` headers name
    (both sides) with `in_scope` over `git diff --name-only -z` of the
    span. Rows: exact entries (a file, a file in a directory, a name that
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

import concurrent.futures
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

from review import emit, vocab
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


class _Loop(unittest.TestCase):
    """Shared helpers: a refusal is judged on its payload AND on the
    repository it must not have touched."""

    def state_bytes(self, loop):
        return {str(p.relative_to(loop.state)): p.read_bytes()
                for p in sorted(loop.state.rglob("*"))
                if p.is_file() and p.suffix != ".lock"} \
            if loop.state.exists() else {}

    def assert_refused_untouched(self, loop, verb, body, member_path,
                                 case=""):
        """`verb` refuses `body` naming `member_path`; refs, index,
        worktree and the ledger are byte-identical afterwards."""
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
        return payload


class TestGrammarPartition(_Loop):

    VALID = {
        "carried_findings": {"fingerprint": FP, "origin": "Lfeedbeef00/2",
                             "outcome": "fixed", "required": "r",
                             "fix": ["a" * 40]},
        "objectives": {"title": "t", "paths": ["f.txt"], "tests": ["t"],
                       "references": ["r.md"]},
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
    }

    @classmethod
    def setUpClass(cls):
        cls.loop = ScratchLoop(_ClassCase(cls),
                               taxonomy_toml(("High", "Low"), ("High",),
                                             gates=GATE))
        # A DIRTY tree: a refusal that reached the sweep would commit it.
        cls.loop.write("g.txt", "outstanding\n")

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
        for member in vocab.CLAIM_OBJECT_LIST_FIELDS:
            for wrong in ("x", {}, 3, None, True):
                self.assert_refused_untouched(
                    self.loop, "handoff", claim(**{member: wrong}), member,
                    f"{member} = {wrong!r}")
            self.assert_refused_untouched(
                self.loop, "handoff", claim(**{member: []}), member,
                f"{member} empty")
            for element in (1, "x", [], None):
                self.assert_refused_untouched(
                    self.loop, "handoff", claim(**{member: [element]}),
                    f"{member}[0]", f"{member}[0] = {element!r}")
            # A bad SECOND entry is located as the second.
            self.assert_refused_untouched(
                self.loop, "handoff",
                claim(**{member: [self.VALID[member], 7]}), f"{member}[1]",
                f"{member}[1]")

    def test_unknown_and_missing_fields(self):
        for member, table in vocab.CLAIM_OBJECT_LIST_FIELDS.items():
            entry = {**self.VALID[member], "zz_typo": "x"}
            self.assert_refused_untouched(
                self.loop, "handoff", claim(**{member: [entry]}),
                f"{member}[0].zz_typo", f"{member} unknown field")
            for field in vocab.CLAIM_OBJECT_REQUIRED[member]:
                entry = {k: v for k, v in self.VALID[member].items()
                         if k != field}
                self.assert_refused_untouched(
                    self.loop, "handoff", claim(**{member: [entry]}),
                    f"{member}[0].{field}", f"{member} missing {field}")

    def test_every_field_refuses_every_wrong_value(self):
        seen = set()
        for member, table in vocab.CLAIM_OBJECT_LIST_FIELDS.items():
            for field, kind in table.items():
                for wrong in self.WRONG[kind]:
                    entry = {**self.VALID[member], field: wrong}
                    where = f"{member}[0].{field}"
                    if isinstance(wrong, list) and wrong:
                        where += "[0]"
                    self.assert_refused_untouched(
                        self.loop, "handoff", claim(**{member: [entry]}),
                        where, f"{where} = {wrong!r}")
                seen.add((member, field))
        self.assertEqual(seen, {(m, f) for m, t in
                                vocab.CLAIM_OBJECT_LIST_FIELDS.items()
                                for f in t})

    def test_a_carried_fingerprint_is_carried_once(self):
        entry = self.VALID["carried_findings"]
        self.assert_refused_untouched(
            self.loop, "handoff",
            claim(carried_findings=[entry, {**entry, "outcome": "deferred"}]),
            "carried_findings[1].fingerprint", "duplicate")

    def test_excluded_paths_is_a_list_of_strings(self):
        for wrong, where in (("x", "excluded_paths"), ({}, "excluded_paths"),
                             ([1], "excluded_paths[0]"),
                             ([None], "excluded_paths[0]")):
            self.assert_refused_untouched(
                self.loop, "handoff", claim(excluded_paths=wrong), where,
                f"excluded_paths = {wrong!r}")

    def test_emit_request_refuses_at_the_same_boundary(self):
        self.assert_refused_untouched(
            self.loop, "emit-request",
            claim(objectives=[{"title": "t"}]), "objectives[0].paths",
            "emit-request")


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
    judged against this ledger."""

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

    def emit(self, body, name):
        out = self.loop.tmp / name
        code, payload = self.loop.loupe(
            "emit-request", "--claim-file",
            self.loop.claim(body, name=name + ".json"),
            "--base", self.round1, "--out", out, "--local-only")
        self.assertEqual(code, 0, payload)
        return out.read_text(encoding="utf-8")

    def refused(self, body, member_path, case, dirty=True):
        if dirty:
            self.loop.write("g.txt", "outstanding\n")
        try:
            return self.assert_refused_untouched(
                self.loop, "emit-request", body, member_path, case)
        finally:
            if dirty:
                self.loop.git("checkout", "-q", "--", "g.txt")

    # ---------------------------------------------------- carried findings

    def test_a_fingerprint_its_ledger_origin_never_ruled_is_refused(self):
        payload = self.refused(
            claim(carried_findings=[{"fingerprint": FP,
                                     "origin": f"{self.lineage}/1",
                                     "outcome": "fixed"}]),
            "carried_findings[0].fingerprint", "absent from origin")
        self.assertIn("recorded in this ledger without that finding",
                      payload["error"])
        # And through `handoff`, the verb that commits: a dirty tree stays
        # dirty, uncommitted, and no round is recorded.
        self.loop.write("g.txt", "outstanding\n")
        try:
            self.assert_refused_untouched(
                self.loop, "handoff",
                claim(carried_findings=[{"fingerprint": FP,
                                         "origin": f"{self.lineage}/1",
                                         "outcome": "fixed"}]),
                "carried_findings[0].fingerprint", "handoff")
        finally:
            self.loop.git("checkout", "-q", "--", "g.txt")

    def test_an_origin_this_ledger_does_not_hold_is_stated(self):
        text = self.emit(claim(carried_findings=[
            {"fingerprint": FP, "origin": "Lnothere000/3",
             "outcome": "deferred", "required": "typed by hand"}]),
            "elsewhere.md")
        self.assertIn(f"- `{FP}` · origin Lnothere000/3 · **deferred**", text)
        self.assertIn("origin verdict not in this ledger — not verifiable "
                      "here", text)
        self.assertIn("required (typed by the author): typed by hand", text)
        self.assertIn("Notice — carried finding(s) whose origin verdict is "
                      "not in this ledger, so not verifiable here (1)", text)

    def test_required_comes_from_the_kept_origin_verdict(self):
        text = self.emit(claim(carried_findings=[
            {"fingerprint": self.x, "origin": f"{self.lineage}/1",
             "outcome": "fixed", "fix": [self.fix]}]), "kept.md")
        self.assertIn("required (the origin verdict, kept on this machine): "
                      "make f.txt say three", text)
        self.assertIn(f"fix: `{self.fix[:12]}`\n", text)
        self.assertNotIn("Notice — carried", text)
        self.assertNotIn("Notice — fix commit", text)

    def test_a_typed_required_that_differs_is_a_notice(self):
        text = self.emit(claim(carried_findings=[
            {"fingerprint": self.x, "origin": f"{self.lineage}/1",
             "outcome": "fixed", "required": "something else"}]),
            "differs.md")
        self.assertIn("make f.txt say three", text)
        self.assertNotIn("something else", text)
        self.assertIn("the claim's `required` differs from the origin "
                      "verdict's Required outcome", text)
        self.assertIn("Notice — carried finding(s) whose `required` differs "
                      "from the origin verdict's (1)", text)

    def test_fix_commits_inside_and_outside_the_span(self):
        text = self.emit(claim(carried_findings=[
            {"fingerprint": self.x, "origin": f"{self.lineage}/1",
             "outcome": "fixed",
             "fix": [self.fix, self.loop.base, "f" * 40]}]), "span.md")
        self.assertIn(f"`{self.fix[:12]}`, `{self.loop.base[:12]}` (NOT in "
                      f"this review's span), `{'f' * 12}` (NOT in this "
                      f"review's span)", text)
        self.assertIn("Notice — fix commit(s) outside this review's span "
                      "(2)", text)

    # ----------------------------------------------------- attestation map

    def test_a_row_resolves_to_the_previous_rounds_disposition(self):
        text = self.emit(claim(attestation_map=[
            {"fingerprint": self.x, "gate": "unit", "test": "t_one"}]),
            "map.md")
        self.assertRegex(text, re.escape(
            f"| `{self.x}` | round 1 F1 (accepted) | unit | t_one | true | "
            f"exit 0 | bound |"))
        # The finding no row covers is named, so the reviewer reruns it.
        self.assertIn("Notice — finding(s) this request answers that no map "
                      f"row covers — rerun these (1): `{self.z}`", text)

    def test_a_row_resolves_to_a_carried_entry(self):
        text = self.emit(claim(
            carried_findings=[{"fingerprint": FP,
                               "origin": "Lnothere000/3",
                               "outcome": "fixed"}],
            attestation_map=[{"fingerprint": FP, "gate": "unit"}]),
            "carried-map.md")
        self.assertIn(f"| `{FP}` | carried from Lnothere000/3 | unit | - | "
                      f"true | exit 0 | bound |", text)

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
        before = self.loop.snapshot()
        out = self.loop.tmp / "never-gate.md"
        code, payload = self.loop.loupe(
            "emit-request", "--claim-file",
            self.loop.claim(claim(attestation_map=[
                {"fingerprint": self.x, "gate": "nope"}]),
                name="gate.json"),
            "--base", self.round1, "--out", out, "--local-only")
        self.assertEqual(code, 1, payload)
        self.assertEqual(payload["next_kind"], "blocked")
        self.assertIn("nope", payload["error"])
        self.assertIn("does not declare", payload["error"])
        self.assertEqual(self.loop.snapshot(), before)
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
        from review import paths
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


def executed_paths(command: str) -> set[str]:
    """EXECUTE a `Scoped:` command as printed and return every path its
    `diff --git` headers name, on either side."""
    run = subprocess.run(["/bin/sh", "-c", command], cwd="/",
                         capture_output=True, timeout=120,
                         stdin=subprocess.DEVNULL)
    if run.returncode != 0:
        raise AssertionError(run.stderr.decode("utf-8", "replace"))
    shown: set[str] = set()
    for line in run.stdout.decode("utf-8").split("\n"):
        if line.startswith("diff --git "):
            shown.update(_header_sides(line[len("diff --git "):]))
    return shown


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

    #: The reviewer's reproductions, each in a span of its own.
    REPRODUCED = (
        ("[^a].txt diverged", ("a.txt", "b.txt", "^.txt"), ["[^a].txt"]),
        ("[!a].txt agreed", ("a.txt", "b.txt", "^.txt"), ["[!a].txt"]),
        ("a.txt agreed", ("a.txt", "b.txt", "^.txt"), ["a.txt"]),
        ("exact src diverged", ("src/x.txt",), ["src"]),
        ("src/ agreed", ("src/x.txt",), ["src/"]),
    )

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
        """(exit, payload, the `Scoped:` value, the request) of one real
        `emit-request`, in a state directory of its own so rows can run
        side by side."""
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

    def assert_agrees(self, loop, base, span, scope, name,
                      line=None) -> str:
        """The row's whole assertion, in two named checks. `set`: the paths
        the line SELECTS — every path the executed command's headers name,
        none for a line that is not a command — are exactly `in_scope`'s.
        `form`: a non-empty set is a command handing git only `:(literal)`
        pathspecs; an empty one is the `none` line, never a command."""
        import shlex
        want = {p for p in span if emit.in_scope(p, scope)}
        if line is None:
            line = self.emit(loop, base, scope, name)
        command = line.startswith("git ")
        with self.subTest(check="set"):
            self.assertEqual(executed_paths(line) if command else set(),
                             want, line[:300])
        with self.subTest(check="form"):
            if want:
                self.assertTrue(command, line)
                for spec in shlex.split(line.split(" -- ", 1)[1]):
                    self.assertRegex(spec, r"^:\((exclude,)?literal\)")
            else:
                self.assertTrue(line.startswith("none — "), line)
        return line

    def test_the_partition(self):
        """Each row's selected set equals `in_scope`'s, and no pattern
        reaches git: every pathspec it hands over is literal."""
        # Four emissions at a time, each with its own state directory: the
        # rows share a span and nothing else.
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            emitted = list(pool.map(
                lambda ir: self.run_emit(self.loop, self.base, ir[1][1],
                                         f"row{ir[0]}"),
                enumerate(self.ROWS)))
        for (row, scope), (code, payload, line, _t) in zip(self.ROWS,
                                                           emitted):
            with self.subTest(row=row):
                self.assertEqual(code, 0, payload)
                self.assert_agrees(self.loop, self.base, self.span, scope,
                                   row, line=line)

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

    def test_the_command_is_literal_and_says_how(self):
        """Every pathspec is `:(literal)`; a prefix stays one word; a path
        git would also select below a matched FILE is excluded by name."""
        line = self.assert_agrees(self.loop, self.base, self.span,
                                  ["src/", "d"], "shape")
        import shlex
        specs = shlex.split(line.split(" -- ", 1)[1])
        self.assertEqual(specs, [":(literal)src/", ":(literal)d",
                                 ":(exclude,literal)d/x",
                                 ":(exclude,literal)d/y"])

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
                    spans[files] = (loop, self.changed(loop, loop.base))
                loop, span = spans[files]
                # As reproduced: the same patterns as scope_paths AND as an
                # objective's paths, so the table the reviewer read and the
                # command they ran are compared on one request.
                code, payload, line, text = self.run_emit(
                    loop, loop.base, scope, f"rep{i}",
                    objectives=[{"title": "the scope", "paths": scope}])
                self.assertEqual(code, 0, payload)
                selected = self.assert_agrees(loop, loop.base, span, scope,
                                              row, line=line)
                row_md = re.search(r"^\| the scope \| [^|]+ \| ([^|]+) \|",
                                   text, re.M)
                mapped = set(row_md[1].strip().split(", ")) - {"(none)"}
                with self.subTest(check="table"):
                    self.assertEqual(
                        mapped, executed_paths(selected)
                        if selected.startswith("git ") else set())

    def test_size_a_prefix_is_one_word_and_a_long_list_is_withheld(self):
        loop = ScratchLoop(self, taxonomy_toml(("High",), ("High",)))
        names = [f"big/entry-{i:04d}-{'x' * 32}.dat" for i in range(1400)]
        for rel in names:
            loop.write(rel, "b\n")
        loop.git("add", "big")
        loop.git("commit", "-q", "-m", "a large span")
        span = self.changed(loop, loop.base)
        # A prefix over every one of them: one pathspec, and it runs.
        line = self.assert_agrees(loop, loop.base, span, ["big/"], "prefix")
        self.assertEqual(line.split(" -- ", 1)[1], "':(literal)big/'")
        # A glob over 100 of them names each, and still runs.
        self.assert_agrees(loop, loop.base, span, ["big/entry-00*"],
                           "hundred")
        # 900 of them: ~58 KB, under the bound, and `/bin/sh -c` still
        # runs it (1,000 measured 64,244 bytes with a long TMPDIR — too
        # close to the bound to be a stable row).
        line = self.assert_agrees(loop, loop.base, span,
                                  ["big/entry-0[0-8]*"], "nine-hundred")
        self.assertGreater(len(line.encode("utf-8")), 50_000)
        # A glob over all 1,400: over the bound, withheld — never a
        # shortened list, and never the whole span.
        line = self.emit(loop, loop.base, ["big/*.dat"], "all")
        self.assertTrue(line.startswith("withheld — the claim's scope_paths "
                                        "match 1400 changed paths"), line)
        self.assertIn(f"over the {emit.SCOPED_COMMAND_MAX}-byte bound", line)
        self.assertNotIn("git ", line)

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

    def assert_rename_treatment(self, line: str, want: set) -> None:
        """The treatment the comparison states, read off the EXECUTED
        output: an in-scope rename target is never paired with its own
        source; unpaired, it is a whole-file addition, and paired, it is
        paired with another in-scope path, as git's own rename detection
        does among the paths it was given."""
        if not line.startswith("git "):
            return
        run = subprocess.run(["/bin/sh", "-c", line], cwd="/",
                             capture_output=True, timeout=120,
                             stdin=subprocess.DEVNULL, check=True)
        source_of = {new: old for old, new in self.RENAMES}
        out = run.stdout.decode("utf-8")
        for block in re.split(r"^(?=diff --git )", out, flags=re.M):
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
        import shlex
        loop, base, span = self.rename_span()
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            emitted = list(pool.map(
                lambda ir: self.run_emit(loop, base, ir[1][1],
                                         f"rename{ir[0]}"),
                enumerate(self.RENAME_ROWS)))
        lines = {}
        for (row, scope, held), (code, payload, line, _t) in zip(
                self.RENAME_ROWS, emitted):
            with self.subTest(row=row):
                self.assertEqual(code, 0, payload)
                want = {p for p in span if emit.in_scope(p, scope)}
                with self.subTest(check="held"):
                    self.assertEqual(want, set(span) if held is None
                                     else held)
                self.assert_agrees(loop, base, span, scope, row, line=line)
                with self.subTest(check="treatment"):
                    self.assert_rename_treatment(line, want)
                lines[row] = line
        for row, specs in self.RENAME_FORMS:
            with self.subTest(row=row, check="specs"):
                self.assertEqual(
                    shlex.split(lines[row].split(" -- ", 1)[1]), specs)

    def test_the_reviewers_rename_reproduction_and_its_controls(self):
        """Round-3 F1 as reproduced: `old/r.txt → new/r.txt` beside an edit
        of `old/keep.txt`. The outgoing prefix selected the deletion of
        `old/r.txt` too; now it selects only `old/keep.txt`. The exact-file
        and destination-prefix controls agreed before the fix and still
        do, and both prefixes together select the edit and the target —
        never the source."""
        import shlex
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
        for i, (row, scope, held, specs) in enumerate(rows):
            with self.subTest(row=row):
                with self.subTest(check="held"):
                    self.assertEqual(
                        {p for p in span if emit.in_scope(p, scope)}, held)
                line = self.assert_agrees(loop, base, span, scope,
                                          f"reproduced{i}")
                with self.subTest(check="specs"):
                    self.assertEqual(shlex.split(line.split(" -- ", 1)[1]),
                                     specs)

    def test_a_copy_adds_no_path_the_command_could_select(self):
        """With `diff.renames = copies` the span also lists copies, and a
        copy's source stays at the target. Git's tree walk visits that
        source only when it changed, and then `in_scope` judges it under
        its own name — so a copy's two ends are both judged, or the source
        is not in the walk at all. Every row agrees; the copy target of an
        unchanged source is an addition, since plain copy detection reads
        only changed sources."""
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
        both = {"cp/keep.txt", "cp/src.txt"}
        copies = {"cp2/copy.txt", "cp2/still-copy.txt"}
        for i, (scope, held) in enumerate((
                (["cp/"], both), (["cp2/"], copies), (["cp/", "cp2/"],
                                                      both | copies),
                (["cp2/*"], copies), (["cp/src.txt"], {"cp/src.txt"}),
                (["cp/still.txt"], set()))):
            with self.subTest(scope=scope):
                with self.subTest(check="held"):
                    self.assertEqual(
                        {p for p in span if emit.in_scope(p, scope)}, held)
                self.assert_agrees(loop, base, span, scope, f"copy{i}")


if __name__ == "__main__":
    unittest.main()

"""A claim pattern naming a directory at the target without its trailing
`/` is refused, naming the member and the fix (brief
`loupe-tool-feedback-2026-10-06` item 2).

The `scope_paths` grammar — `scope_paths`, `excluded_paths`, each
objective's `paths` — is an exact path, a directory prefix ending in `/`,
or a glob, matched by the one matcher `emit.in_scope`. A bare `src` is an
exact path; a span lists files; so it matched nothing, and lineage
Lb81962e787 round 1 carried two such objective paths and 27 unmapped-path
notices. The design keeps the claim the author's (§Claim: "the tool
carries it and never invents it"), so the pattern is refused rather than
reinterpreted: `emit.check_directory_paths`.

THE PARTITION (in-process, on a real scratch repository, against the one
function both callers run):

  I1  a bare directory with changes under it         refused, located
  I2  the same with its `/`                          admitted
  I3  an exact file; I3b one the span did not change admitted
  I4  a path the target does not hold                admitted
  I5  a glob; I5b a glob spelling a real directory   admitted
  I6  non-canonical spellings (`./src`, `/src`)      admitted, not judged
  I7  a file the span replaced with a directory      admitted (it maps the
      deleted file); I7c the same name not in the span: refused
  I8  a submodule pointer; I9 a symlink to a dir     admitted, and
      I8b, I9b the same the span did not change      admitted
  I10 a directory nothing in the span changed        refused
  I11 each member: `scope_paths`, `excluded_paths`, a second objective's
      second path                                    refused, located
  I12 two offenders                                  both named, one refusal
  I13 no candidate                                   the span is never read

THROUGH THE REAL ENTRY POINT (`emit-request` and `handoff` as
subprocesses, scratch state): C1 the I1 refusal, the repository and the
ledger byte-identical and no request written; C2 every admitted class in
one claim, emitted; C3 and C4 `scope_paths` and `excluded_paths` refused;
C5 a hand-off with a remote refused in its gate-before-push callback — no
gate ran (the gate's marker is absent), nothing pushed, the checkout
byte-identical.

MUTATIONS (manual, copies of review/emit.py and review/cli.py,
`python3.15 -B`; recorded in the commit): the callback's call removed
sends C5 red (the gate runs and the hand-off pushes before the emission
refuses); the span exemption removed sends I7 and C2 red; the glob
exclusion removed sends I5b and C2 red; the canonical-path filter removed
sends I6 and C2 red; `== "tree"` weakened to any kind sends I3b, I8b,
I9b and C2 red; the trailing-`/` exemption removed sends I2, I13, C2
and C5's paired control red.
"""
from __future__ import annotations

import shutil
import unittest

from review import emit, transport
from review.tests.test_precis_taxonomy import ScratchLoop, taxonomy_toml

MARK_GATE = ('\n[[gates]]\nid = "mark"\ncommand = ["sh", "-c", '
             '"echo ran > ../gate-ran"]\nblocking = false\n')
REFS = [{"path": "review.toml", "required": True}]


def claim(**members) -> dict:
    return {"objective": "directory paths", "references": REFS, **members}


def objectives(*paths, title="o") -> list[dict]:
    return [{"title": title, "paths": list(paths)}]


def build(case, *, remote=False) -> tuple[ScratchLoop, str, str]:
    """base: `x` a file, `docs/d.md`, a symlink `oldlink` to `docs` and a
    submodule pointer `oldsub`, none of which the span changes but `x`.
    head: `src/a.py`, `src/b.py`, `x` replaced by the directory `x/`
    (different bytes, so no rename), a submodule pointer `sub`, a symlink
    `link` to `src`, and a directory literally named `lit[1]`. Returns
    (loop, base, head)."""
    loop = ScratchLoop(case, taxonomy_toml(("High",), ("High",),
                                           gates=MARK_GATE), remote=remote)
    loop.write("x", "a file\n")
    loop.write("docs/d.md", "docs\n")
    (loop.repo / "oldlink").symlink_to("docs")
    loop.git("add", "x", "docs/d.md", "oldlink")
    loop.git("update-index", "--add", "--cacheinfo",
             f"160000,{loop.base},oldsub")
    (loop.repo / "oldsub").mkdir()
    loop.git("commit", "-q", "-m", "base")
    base = loop.git("rev-parse", "HEAD")
    if remote:
        loop.git("push", "-q", "origin", "main")
    loop.write("src/a.py", "a\n")
    loop.write("src/b.py", "b\n")
    loop.git("rm", "-q", "x")
    loop.write("x/y", "now a directory\n")
    loop.write("lit[1]/z", "z\n")
    (loop.repo / "link").symlink_to("src")
    loop.git("add", "src", "x/y", "lit[1]/z", "link")
    loop.git("update-index", "--add", "--cacheinfo",
             f"160000,{base},sub")
    loop.git("commit", "-q", "-m", "head")
    # An uninitialised submodule is an empty directory; without it the
    # pointer reads as deleted and a hand-off would commit the deletion.
    (loop.repo / "sub").mkdir()
    case_status = loop.git("status", "--porcelain")
    if case_status:
        raise AssertionError(f"the fixture is not clean: {case_status}")
    return loop, base, loop.git("rev-parse", "HEAD")


class TestPartitionInProcess(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        class _Case:
            def addCleanup(self, fn, *a, **k):
                cls.addClassCleanup(fn, *a, **k)

            def skipTest(self, why):
                raise unittest.SkipTest(why)
        cls.loop, cls.base, cls.head = build(_Case())
        cls.span = emit.span_raw_paths(emit.diff_shape(cls.loop.repo,
                                                       cls.base, cls.head))

    def check(self, body, changed=None):
        emit.check_directory_paths(body, self.loop.repo, self.head,
                                   changed or (lambda: self.span))

    def refused(self, body, *located, changed=None) -> str:
        with self.assertRaises(transport.Refusal) as ctx:
            self.check(body, changed)
        text = str(ctx.exception)
        for where, p in located:
            self.assertIn(f"{where} is `{p}`, write `{p}/`", text)
        self.assertEqual(ctx.exception.next_cmd, "")
        self.assertIn("trailing `/`", ctx.exception.remedy)
        return text

    def test_the_span_is_what_the_fixture_says(self):
        self.assertEqual(sorted(self.span),
                         ["link", "lit[1]/z", "src/a.py", "src/b.py", "sub",
                          "x", "x/y"])

    def test_i1_a_bare_directory_is_refused_and_located(self):
        text = self.refused(claim(objectives=objectives("src")),
                            ("objectives[0].paths[0]", "src"))
        self.assertIn("No gate was run and nothing was pushed, emitted or "
                      "recorded", text)

    def test_i2_the_directory_with_its_slash_is_admitted(self):
        self.check(claim(objectives=objectives("src/")))

    def test_i3_an_exact_file_is_admitted(self):
        self.check(claim(objectives=objectives("src/a.py")))

    def test_i3b_a_file_the_span_did_not_change_is_admitted(self):
        self.check(claim(objectives=objectives("docs/d.md")))

    def test_i4_a_path_the_target_does_not_hold_is_admitted(self):
        self.check(claim(objectives=objectives("lib")))

    def test_i5_globs_are_admitted(self):
        self.check(claim(objectives=objectives("sr?", "src*")))

    def test_i5b_a_glob_spelling_a_real_directory_is_a_glob(self):
        self.check(claim(objectives=objectives("lit[1]")))

    def test_i6_non_canonical_spellings_are_not_judged(self):
        self.check(claim(objectives=objectives("./src", "/src", "src/./")))

    def test_i7_a_file_the_span_replaced_with_a_directory_is_admitted(self):
        self.check(claim(objectives=objectives("x")))

    def test_i7c_control_the_same_name_outside_the_span_is_refused(self):
        self.refused(claim(objectives=objectives("x")),
                     ("objectives[0].paths[0]", "x"),
                     changed=lambda: [p for p in self.span if p != "x"])

    def test_i8_a_submodule_pointer_is_admitted(self):
        self.check(claim(objectives=objectives("sub")))

    def test_i8b_an_unchanged_submodule_pointer_is_admitted(self):
        self.check(claim(objectives=objectives("oldsub")))

    def test_i9_a_symlink_to_a_directory_is_admitted(self):
        self.check(claim(objectives=objectives("link")))

    def test_i9b_an_unchanged_symlink_to_a_directory_is_admitted(self):
        self.check(claim(objectives=objectives("oldlink")))

    def test_i10_a_directory_nothing_changed_is_refused(self):
        self.refused(claim(objectives=objectives("docs")),
                     ("objectives[0].paths[0]", "docs"))

    def test_i11_every_member_is_judged_and_located(self):
        for body, where, p in (
                (claim(scope_paths=["src/", "docs"]), "scope_paths[1]",
                 "docs"),
                (claim(excluded_paths=["docs"]), "excluded_paths[0]",
                 "docs"),
                (claim(objectives=objectives("src/") + [
                    {"title": "two", "paths": ["src/a.py", "src"]}]),
                 "objectives[1].paths[1]", "src")):
            with self.subTest(where=where):
                self.refused(body, (where, p))

    def test_i12_two_offenders_are_named_in_one_refusal(self):
        text = self.refused(claim(scope_paths=["docs"],
                                  objectives=objectives("src")),
                            ("scope_paths[0]", "docs"),
                            ("objectives[0].paths[0]", "src"))
        self.assertIn("the claim names 2 path(s) that are directories",
                      text)

    def test_i13_no_candidate_never_reads_the_span(self):
        def boom():
            raise AssertionError("the span was read")
        self.check(claim(objectives=objectives("src/", "sr?")), boom)
        self.check(claim(), boom)


class TestThroughTheCli(unittest.TestCase):

    def emit_request(self, loop, base, body, name):
        out = loop.tmp / f"{name}.md"
        code, payload = loop.loupe(
            "emit-request", "--claim-file", loop.claim(body, name=name),
            "--base", base, "--out", out, "--local-only")
        return code, payload, out

    def state_bytes(self, loop):
        return ({str(p.relative_to(loop.state)): p.read_bytes()
                 for p in sorted(loop.state.rglob("*"))
                 if p.is_file() and p.suffix != ".lock"}
                if loop.state.exists() else {})

    def assert_refused_untouched(self, loop, base, body, where, p):
        before, ledger = loop.snapshot(), self.state_bytes(loop)
        code, payload, out = self.emit_request(loop, base, body,
                                               f"refused-{where}.json")
        self.assertEqual(code, 1, payload)
        self.assertEqual(payload.get("next_kind"), "blocked", payload)
        self.assertIn(f"{where} is `{p}`, write `{p}/`", payload["error"])
        self.assertEqual(loop.snapshot(), before)
        self.assertEqual(self.state_bytes(loop), ledger)
        self.assertFalse(out.exists())
        self.assertFalse((loop.tmp / "gate-ran").exists())

    def test_c1_c3_c4_refusals_leave_everything_as_it_was(self):
        loop, base, _ = build(self)
        for body, where, p in (
                (claim(objectives=objectives("src")),
                 "objectives[0].paths[0]", "src"),
                (claim(scope_paths=["docs"]), "scope_paths[0]", "docs"),
                (claim(excluded_paths=["docs"]), "excluded_paths[0]",
                 "docs")):
            with self.subTest(where=where):
                self.assert_refused_untouched(loop, base, body, where, p)

    def test_c2_every_admitted_class_emits(self):
        loop, base, _ = build(self)
        code, payload, out = self.emit_request(loop, base, claim(
            objectives=objectives("src/", "src/a.py", "lib", "sr?",
                                  "lit[1]", "./src", "x", "sub", "link",
                                  "docs/d.md", "oldsub", "oldlink")),
            "admitted.json")
        self.assertEqual(code, 0, payload)
        text = out.read_text(encoding="utf-8")
        self.assertIn("Objectives —", text)
        self.assertIn("src/a.py, src/b.py", text)

    def test_c5_a_hand_off_refuses_before_any_gate_or_push(self):
        loop, base, head = build(self, remote=True)
        remote_before = loop.git("ls-remote", "origin", "refs/heads/main")
        before = loop.snapshot()
        code, payload = loop.loupe(
            "handoff", "--claim-file",
            loop.claim(claim(objectives=objectives("src")), name="h.json"),
            "--base", base)
        self.assertEqual(code, 1, payload)
        self.assertEqual(payload.get("next_kind"), "blocked", payload)
        self.assertIn("objectives[0].paths[0] is `src`, write `src/`",
                      payload["error"])
        self.assertFalse((loop.tmp / "gate-ran").exists(),
                         "a gate ran before the refusal")
        self.assertEqual(loop.git("ls-remote", "origin", "refs/heads/main"),
                         remote_before)
        self.assertEqual(loop.snapshot(), before)
        self.assertEqual(loop.git("rev-parse", "HEAD"), head)
        # The paired control: the same hand-off with the `/` gates and
        # pushes.
        code, payload = loop.loupe(
            "handoff", "--claim-file",
            loop.claim(claim(objectives=objectives("src/")), name="ok.json"),
            "--base", base)
        self.assertEqual(code, 0, payload)
        self.assertTrue((loop.tmp / "gate-ran").exists())
        self.assertIn(head, loop.git("ls-remote", "origin",
                                     "refs/heads/main"))


if __name__ == "__main__":
    unittest.main()

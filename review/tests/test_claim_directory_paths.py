"""A claim path names itself and everything below it, as git reads a
literal pathspec (brief `claim-bare-directory-pathspec`, Osama's ruling of
2026-11-01, which reverses item 2 of brief `loupe-tool-feedback-2026-10-06`).

The `scope_paths` grammar — `scope_paths`, `excluded_paths`, each
objective's `paths` — is a path or a glob, matched by the one matcher
`emit.in_scope`. Until 2026-11-01 a bare `src` was an exact path: a span
lists files, so it matched nothing, and from 2026-09-19 the hand-off refused
it after its commit (`emit.check_directory_paths`, deleted). 16 of one
adopting repository's refused hand-offs in two weeks were that refusal, and
two of them had first waited 883 s for the machine's suite lock. Now `src`
and `src/` admit the same paths. Nothing rewrites the claim: the grammar
gives the bare name its meaning.

THE PARTITION, in-process, `emit.in_scope` over the span of a real scratch
repository and probe paths beside it:

  P1  a bare directory                 admits what its `dir/` admits, nested
                                       paths included, over every path
  P2  a sibling sharing the prefix     never admitted (`src-old/x`, `srcx`,
                                       `src.py`), bare or with its `/`
  P3  an exact file                    admits itself only; its `file/`
                                       admits nothing
  P4  a file the span replaced with a  the bare name admits both the file
      directory                        and what is below; `x/` the latter
  P5  a symlink, a submodule pointer   admit themselves only
  P6  a glob                           fnmatch as before; a glob spelling a
                                       real directory (`lit[1]`) also names
                                       it literally, as git's literal-first
                                       pathspec match does
  P7  non-canonical spellings          admit nothing (`./src`, `/src`,
                                       `src/./`, an empty string)
  P8  a path the target does not hold  admits nothing
  P9  a directory the span deleted     the bare name admits the deleted
                                       paths below it, as its `dir/` does

THROUGH THE REAL ENTRY POINT (`emit-request` and `handoff` as
subprocesses, scratch state): C1 `emit-request` with `src` as an objective
path, a scope path and an excluded path maps, scopes and subtracts what
`src/` does, its `Scoped:` command naming `:(literal)src/`; C2 a hand-off
with a remote and a bare objective directory runs its gate, pushes, and
keeps a request mapping the directory's files; C3 a hand-off whose
outstanding work lies under a bare `scope_paths` directory sweeps it, and
the paired control, a scope that does not hold it, refuses outside the
scope with the checkout byte-identical; C4 a claim the base admitted,
naming a directory the span deleted (`docs`) or a glob spelling a real
directory (`lit[1]`), now maps, subtracts and scopes the paths below it,
as the brief's upgrader note says (round 1 F1 of lineage L93b1f9d73b), the
paired `docs/` rendering the same rows.

MUTATIONS (manual, copies of review/emit.py, `python3.15 -B`, restored by
`cmp`; recorded in the commit): `in_scope` back to a bare exact path sends
P1, P4, P6, C1, C2 and C3 red; a bare entry matched as a string prefix (no
`/`) sends P1 to P4 and P7 red; the glob branch removed sends P6 red; the
`Scoped:` builder compressing only a `dir/` entry sends C1 red. Round 1
F1: `in_scope` back to a bare exact path sends P9 and C4 red as well.
"""
from __future__ import annotations

import unittest

from review import emit
from review.tests.test_precis_taxonomy import ScratchLoop, taxonomy_toml

MARK_GATE = ('\n[[gates]]\nid = "mark"\ncommand = ["sh", "-c", '
             '"echo ran > ../gate-ran"]\nblocking = false\n')
REFS = [{"path": "review.toml", "required": True}]

#: Paths beside the span, so a row is judged on what it must NOT admit
#: as well as on what the span holds.
PROBES = ("src", "src/deep/c.py", "src-old/x", "srcx", "src.py",
          "src/a.py.bak", "src/a.pyc", "docs/d.md", "lib/l.py", "x",
          "x/y", "xy", "lit1", "lit[1]", "lit[1]/z", "link/inner",
          "sub/inner")


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


def build_deleted(case) -> tuple[ScratchLoop, str, str]:
    """`build`, then `docs/d.md` deleted in a commit of its own, so the
    span holds a directory the target no longer has."""
    loop, base, _ = build(case)
    loop.git("rm", "-q", "docs/d.md")
    loop.git("commit", "-q", "-m", "docs deleted")
    return loop, base, loop.git("rev-parse", "HEAD")


def span_of(loop, base, head) -> list[str]:
    return [row["raw"] for row in emit._span_rows(
        emit.diff_shape(loop.repo, base, head))]


class TestPartitionInProcess(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        class _Case:
            def addCleanup(self, fn, *a, **k):
                cls.addClassCleanup(fn, *a, **k)

            def skipTest(self, why):
                raise unittest.SkipTest(why)
        cls.loop, cls.base, cls.head = build(_Case())
        cls.span = span_of(cls.loop, cls.base, cls.head)
        cls.paths = sorted(set(cls.span) | set(PROBES))

    def admitted(self, *entries) -> set[str]:
        return {p for p in self.paths if emit.in_scope(p, list(entries))}

    def test_the_span_is_what_the_fixture_says(self):
        self.assertEqual(sorted(self.span),
                         ["link", "lit[1]/z", "src/a.py", "src/b.py", "sub",
                          "x", "x/y"])

    def test_p1_a_bare_directory_admits_what_its_slash_admits(self):
        self.assertEqual(self.admitted("src"),
                         {"src/a.py", "src/b.py", "src/deep/c.py",
                          "src/a.py.bak", "src/a.pyc", "src"})
        # A glob's `/`-ended twin is a glob of its own (P6), not a pair.
        for name in ("src", "docs", "x", "src/deep"):
            with self.subTest(name=name):
                self.assertEqual(self.admitted(name) - {name},
                                 self.admitted(f"{name}/"))

    def test_p2_a_sibling_sharing_the_prefix_is_never_admitted(self):
        for entry in ("src", "src/"):
            with self.subTest(entry=entry):
                self.assertFalse({"src-old/x", "srcx", "src.py"}
                                 & self.admitted(entry))

    def test_p3_an_exact_file_admits_itself_only(self):
        self.assertEqual(self.admitted("src/a.py"), {"src/a.py"})
        self.assertEqual(self.admitted("src/a.py/"), set())

    def test_p4_a_file_replaced_by_a_directory(self):
        self.assertEqual(self.admitted("x"), {"x", "x/y"})
        self.assertEqual(self.admitted("x/"), {"x/y"})

    def test_p5_a_symlink_and_a_submodule_pointer_admit_themselves(self):
        # Git lists nothing below either; the probes `link/inner` and
        # `sub/inner` stand for a path the matcher would admit if it did.
        self.assertEqual(self.admitted("link") & set(self.span), {"link"})
        self.assertEqual(self.admitted("sub") & set(self.span), {"sub"})

    def test_p6_globs(self):
        self.assertEqual(self.admitted("src*"),
                         {"src", "src/a.py", "src/b.py", "src/deep/c.py",
                          "src-old/x", "srcx", "src.py", "src/a.py.bak",
                          "src/a.pyc"})
        self.assertEqual(self.admitted("sr?"), {"src"})
        self.assertEqual(self.admitted("lit[1]"),
                         {"lit1", "lit[1]", "lit[1]/z"})

    def test_p7_non_canonical_spellings_admit_nothing(self):
        for entry in ("./src", "/src", "src/./", ""):
            with self.subTest(entry=entry):
                self.assertEqual(self.admitted(entry), set())

    def test_p8_a_path_the_target_does_not_hold_admits_nothing(self):
        self.assertEqual(self.admitted("lib") & set(self.span), set())

    def test_p9_a_directory_the_span_deleted(self):
        loop, base, head = build_deleted(self)
        span = span_of(loop, base, head)
        self.assertIn("docs/d.md", span)
        for entry in ("docs", "docs/"):
            with self.subTest(entry=entry):
                self.assertEqual({p for p in span
                                  if emit.in_scope(p, [entry])},
                                 {"docs/d.md"})


class TestThroughTheCli(unittest.TestCase):

    def emit_request(self, loop, base, body, name):
        out = loop.tmp / f"{name}.md"
        code, payload = loop.loupe(
            "emit-request", "--claim-file", loop.claim(body, name=name),
            "--base", base, "--out", out, "--local-only")
        self.assertEqual(code, 0, payload)
        return out.read_text(encoding="utf-8")

    @staticmethod
    def lines(text, *starts) -> list[str]:
        return [ln for ln in text.splitlines() if ln.startswith(starts)]

    def test_c1_bare_and_slashed_emit_the_same_request_rows(self):
        loop, base, _ = build(self)
        texts = {}
        for entry in ("src", "src/"):
            texts[entry] = self.emit_request(loop, base, claim(
                objectives=objectives(entry), scope_paths=[entry],
                excluded_paths=[entry]), f"c1-{entry.strip('/')}"
                f"{'-slash' if entry.endswith('/') else ''}.json")
        bare, slashed = texts["src"], texts["src/"]
        self.assertIn("| o | `src` | src/a.py, src/b.py |", bare)
        self.assertIn("  - `src` (matches 2 span path(s))", bare)
        scoped = self.lines(bare, "Scoped:")
        self.assertEqual(len(scoped), 1, bare)
        self.assertTrue(scoped[0].endswith(" -- ':(literal)src/'"),
                        scoped[0])
        # The paired control: `src/` yields the same rows, only the
        # declared spelling differs.
        self.assertEqual(
            [ln.replace("`src/`", "`src`")
             for ln in self.lines(slashed, "| o |", "  - `src", "Scoped:")],
            self.lines(bare, "| o |", "  - `src", "Scoped:"))

    def test_c2_a_hand_off_with_a_bare_directory_gates_and_pushes(self):
        loop, base, head = build(self, remote=True)
        code, payload = loop.loupe(
            "handoff", "--claim-file",
            loop.claim(claim(objectives=objectives("src")), name="h.json"),
            "--base", base)
        self.assertEqual(code, 0, payload)
        self.assertTrue((loop.tmp / "gate-ran").exists())
        self.assertIn(head, loop.git("ls-remote", "origin",
                                     "refs/heads/main"))
        kept = payload["kept"]
        with open(kept, encoding="utf-8") as fh:
            self.assertIn("| o | `src` | src/a.py, src/b.py |", fh.read())

    def test_c3_outstanding_work_under_a_bare_scope_path_is_swept(self):
        for scope, admitted in ((["src"], True), (["lib"], False)):
            with self.subTest(scope=scope):
                loop, base, _ = build(self)
                loop.write("src/a.py", "a, changed\n")
                before = loop.snapshot()
                code, payload = loop.handoff(loop.claim(
                    claim(scope_paths=scope), name="sweep.json"))
                if admitted:
                    self.assertEqual(code, 0, payload)
                    self.assertIn("src/a.py", loop.git(
                        "show", "--name-only", "--format=", "HEAD"))
                else:
                    self.assertNotEqual(code, 0, payload)
                    self.assertIn("src/a.py", payload["error"])
                    self.assertIn("scope", payload["error"])
                    self.assertEqual(loop.snapshot(), before)

    def test_c4_an_accepted_claim_widens_where_it_has_paths_below(self):
        loop, base, _ = build_deleted(self)
        rows = {}
        for entry, below in (("docs", "docs/d.md"), ("docs/", "docs/d.md"),
                             ("lit[1]", "lit[1]/z")):
            with self.subTest(entry=entry):
                text = self.emit_request(loop, base, claim(
                    objectives=objectives(entry), scope_paths=[entry],
                    excluded_paths=[entry]),
                    f"c4-{len(rows)}.json")
                self.assertIn(f"| o | `{entry}` | {below} |", text)
                self.assertIn(f"  - `{entry}` (matches 1 span path(s))",
                              text)
                scoped = self.lines(text, "Scoped:")
                self.assertEqual(len(scoped), 1, text)
                prefix = entry.rstrip("/") + "/"
                self.assertTrue(scoped[0].endswith(f"':(literal){prefix}'"),
                                scoped[0])
                rows[entry] = [ln.replace(f"`{entry}`", "`E`") for ln in
                               self.lines(text, "| o |", f"  - `{entry}`",
                                          "Scoped:")]
        # The paired control: the bare `docs` renders what `docs/` does.
        self.assertEqual(rows["docs"], rows["docs/"])


if __name__ == "__main__":
    unittest.main()

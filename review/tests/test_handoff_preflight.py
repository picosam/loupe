"""The hand-off preflight: what a commit would sweep, judged before it exists.

Brief `handoff-guards-generalized` (2026-09-18), the three classes that live
inside `handoff`:

  1. COMMIT BEFORE GATE. `handoff` commits and pushes outstanding tracked
     work, then gates — so a gate protects the envelope and never the
     commit. Ruled: the order stays, the claim gains `scope_paths`, and a
     sweep beyond it is refused BEFORE the commit.
  2. THE ENVIRONMENT CANNOT AUTHOR. An interpreter below the floor died in a
     traceback; the package's first statement now says why instead.
  5. A KILLED PROCESS LEAVES ITS FIXTURES. A file that says of itself it is
     corrupt-by-design is never swept.

What is and is not covered. The end-to-end class drives the real CLI in a
scratch repository and asserts on HEAD, so "nothing was committed" is a
measurement, not a reading of the refusal's prose. The interpreter guard is
held two ways: statically (the package's first file must PARSE as old
Python, or the guard can never run) everywhere, and by execution wherever an
older interpreter is installed — skipped, with the reason, where none is.
NOT covered: that a gate's own domain stays inside the tree (class 3, the
manifest's), and a suite that writes a fixture WITHOUT the marker — the tool
cannot know a file's intent that the file does not state.
"""

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

import review
from review import emit, vocab
from review.emit import _git
from review.tests._transport_fixtures import run_cli, scratch_loop_repo, sh
from review.tests.util import REPO_ROOT, declared_interval, public_path


class TestTheInterpreterGuard(unittest.TestCase):

    INIT = Path(review.__file__)

    def test_the_packages_first_file_parses_as_old_python(self):
        """The guard can only speak if the interpreter that needs it can
        compile the file it lives in. FALSIFICATION. Mutation: put a `match`
        statement (3.10 syntax) in `review/__init__.py` and this fails."""
        ast.parse(self.INIT.read_text(encoding="utf-8"),
                  feature_version=(3, 7))

    def test_the_guard_runs_before_anything_that_can_fail(self):
        """It must precede every import and every annotated def: under 3.9
        the file died at an evaluated `Path | None`."""
        tree = ast.parse(self.INIT.read_text(encoding="utf-8"))
        guard = next(i for i, node in enumerate(tree.body)
                     if isinstance(node, ast.If)
                     and "version_info" in ast.dump(node.test))
        before = tree.body[:guard]
        self.assertTrue(all(
            isinstance(n, (ast.Expr, ast.Assign))
            or (isinstance(n, ast.Import)
                and [a.name for a in n.names] == ["sys"])
            for n in before), [type(n).__name__ for n in before])

    def test_the_floor_is_the_package_metadatas(self):
        """Both ends of the guard's interval are the declared ones.

        The declaration is read through `declared_interval`, the one
        authority for that value's spelling. Until 2026-09-19 this read it
        with a `re.search` for `>=\\s*(\\d+)\\.(\\d+)` over a SUBSTRING —
        so `>=3.14,<3.15,!=3.14.1` and a multiline value whose first line
        happened to match were both accepted here while the generator
        refused them, and a leading zero or a non-ASCII digit passed
        through `int()` without comment. One reader, one grammar, whole
        value. FALSIFICATION. Mutation: put either substring regex back and
        `test_this_package_has_ONE_grammar_for_the_declared_interval` names
        this file, and the workbench's reader-agreement table goes red on
        the rows this one now refuses.
        """
        pyproject = public_path("pyproject.toml")
        if pyproject is None:
            self.skipTest("no pyproject.toml in this tree")
        declared = tomllib.loads(pyproject.read_text(
            encoding="utf-8"))["project"]["requires-python"]
        floor, below = declared_interval(declared)
        self.assertEqual(review.REQUIRES_PYTHON, floor)
        self.assertEqual(review.REQUIRES_PYTHON_BELOW, below)

    def test_the_declaration_is_refused_when_it_is_not_the_one_shape(self):
        """The authority refuses, rather than extracting from, every
        spelling the generator refuses — including the two families the old
        substring search accepted here."""
        for spec in (">=3.14", ">=3.14,<3.15,!=3.14.1", ">= 3.14,<3.15",
                     ">=3.014,<3.15", ">=3.14,<3.15\n>=3.11,<3.12", "",
                     ">=11111.14,<3.15", 314):
            with self.subTest(spec=spec):
                with self.assertRaises(ValueError):
                    declared_interval(spec)
        self.assertEqual(declared_interval(">=3.14,<3.15"),
                         ((3, 14), (3, 15)))

    def _older_interpreter(self):
        names = ["/usr/bin/python3"] + [f"python3.{m}" for m in range(8, 14)]
        for name in names:
            found = shutil.which(name)
            if not found:
                continue
            probe = subprocess.run(
                [found, "-c", "import sys; print(sys.version_info[:2] < "
                              f"{review.REQUIRES_PYTHON!r})"],
                capture_output=True, text=True)
            if probe.returncode == 0 and probe.stdout.strip() == "True":
                return found
        return None

    def test_an_older_interpreter_gets_a_sentence_not_a_traceback(self):
        """THE REPRODUCTION. Before the guard this printed `TypeError:
        unsupported operand type(s) for |` from inside the package.
        FALSIFICATION. Mutation: delete the guard and the traceback is
        back."""
        older = self._older_interpreter()
        if older is None:
            self.skipTest("no interpreter below the floor on this machine")
        done = subprocess.run(
            [older, "-c", "import review"], capture_output=True, text=True,
            cwd=str(self.INIT.parent.parent),
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": ""})
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("needs Python 3.14 or newer", done.stderr)
        self.assertIn("Nothing was read, committed, pushed or emitted",
                      done.stderr)
        self.assertNotIn("Traceback", done.stderr)


class TestScopeEntries(unittest.TestCase):

    def test_scope_entries_are_exact_a_directory_or_a_glob(self):
        scope = ["README.md", "review/", "tools/tests/test_*.py", "docs"]
        for path, expected in (
                ("README.md", True), ("review/emit.py", True),
                ("review/tests/test_x.py", True),
                ("tools/tests/test_x.py", True),
                ("tools/tests/helper.py", False),
                ("reviewer/notes.md", False),   # a prefix is not a directory
                ("docs", True),
                ("docs/README.md", False)):   # `docs` names a path, not a tree
            with self.subTest(path=path):
                self.assertEqual(emit.in_scope(path, scope), expected)

    def test_scope_paths_is_a_claim_member(self):
        self.assertIn("scope_paths", vocab.CLAIM_LIST_FIELDS)


def change(path, status="M", data=b"x = 1\n", mode="100644"):
    return {"status": status, "path": path, "mode": mode,
            "blob": "0" * 40, "bytes": None if status == "D" else data}


class TestTheSweepPreflight(unittest.TestCase):
    """The judgement over a candidate's changes; the candidate itself is
    held by `TestTheCandidateDomain` against real git."""

    MARKED = f"commit: not-a-sha\n<!-- {emit.FIXTURE_MARKER} -->\n".encode()

    def test_the_control_inside_the_scope_passes_and_is_recorded(self):
        record = emit.sweep_preflight([change("work.py")], ["work.py"])
        self.assertEqual(record, {"swept": ["work.py (M)"], "declared": True,
                                  "outside": [], "allowed_outside": False})

    def test_a_path_outside_the_scope_refuses_with_a_remedy(self):
        with self.assertRaises(emit.SweepRefused) as ctx:
            emit.sweep_preflight([change("work.py"), change("other.py")],
                                 ["work.py"])
        self.assertIn("other.py", str(ctx.exception))
        self.assertNotIn("work.py,", str(ctx.exception))
        self.assertIn("Nothing has been committed", ctx.exception.remedy)

    def test_both_ends_of_a_rename_are_changes(self):
        """W3 F4: a rename is its D and its A. FALSIFICATION. Mutation: drop
        `D` entries from the scope comparison and the outside source is
        deleted unlisted."""
        moved = [change("f.txt", "D"), change("allowed/f.txt", "A")]
        with self.assertRaises(emit.SweepRefused) as ctx:
            emit.sweep_preflight(moved, ["allowed/"])
        self.assertIn("f.txt", str(ctx.exception))
        record = emit.sweep_preflight(moved, ["allowed/", "f.txt"])
        self.assertEqual(record["swept"], ["allowed/f.txt (A)", "f.txt (D)"])
        record = emit.sweep_preflight(moved, ["allowed/"],
                                      allow_outside_scope=True)
        self.assertEqual(record["outside"], ["f.txt"])

    def test_the_override_sweeps_and_says_so(self):
        record = emit.sweep_preflight([change("work.py"), change("other.py")],
                                      ["work.py"], allow_outside_scope=True)
        self.assertEqual(record["outside"], ["other.py"])
        self.assertTrue(record["allowed_outside"])
        (line,) = [l for l in emit.render_preflight_lines({"sweep": record})
                   if l.startswith("Swept:")]
        self.assertIn("OUTSIDE the declared `scope_paths`", line)
        self.assertIn("other.py", line)

    def test_no_declared_scope_sweeps_as_before_and_the_face_says_so(self):
        record = emit.sweep_preflight([change("work.py")], None)
        self.assertFalse(record["declared"])
        (line,) = emit.render_preflight_lines({"sweep": record})
        self.assertIn("declares no `scope_paths`", line)

    def test_an_empty_scope_is_declared_and_admits_nothing(self):
        """`[]` is a declaration — "this round sweeps nothing" — and is not
        the same as saying nothing. FALSIFICATION. Mutation: test
        `if scope_paths` instead of `is not None` and this sweeps."""
        with self.assertRaises(emit.SweepRefused):
            emit.sweep_preflight([change("work.py")], [])

    def test_a_marked_change_refuses_and_no_flag_overrides_it(self):
        """FALSIFICATION. Mutation: skip the marker scan when the override
        is set, and the msx fixture is published under a flag."""
        for allow in (False, True):
            with self.subTest(allow=allow), \
                    self.assertRaises(emit.SweepRefused) as ctx:
                emit.sweep_preflight([change("fixture.md", "A", self.MARKED)],
                                     None, allow_outside_scope=allow)
            self.assertIn(emit.FIXTURE_MARKER, str(ctx.exception))
            self.assertIn("fixture.md", str(ctx.exception))

    def test_a_gitlink_is_indeterminate_and_refused_by_name(self):
        """W3 F2: an entry type with no bytes to judge is a stated
        refusal, never a silent pass."""
        with self.assertRaises(emit.SweepRefused) as ctx:
            emit.sweep_preflight([change("vendor/lib", "M", None, "160000")],
                                 None)
        self.assertIn("submodule", str(ctx.exception))
        # A deleted gitlink has nothing left to judge and is an ordinary D.
        emit.sweep_preflight([change("vendor/lib", "D", None, "160000")], None)

    def test_a_deleted_path_is_swept_and_never_read(self):
        record = emit.sweep_preflight([change("vanished.py", "D")], None)
        self.assertEqual(record["swept"], ["vanished.py (D)"])

    def test_the_marker_is_a_line_of_its_own_never_a_mention(self):
        """FALSIFICATION. Mutation: go back to a substring test and the
        sentence case below refuses — as would this repository's own
        hand-off, whose diff explains the marker in prose."""
        marker = emit.FIXTURE_MARKER
        for text in (marker, f"# {marker}", f"<!-- {marker} -->",
                     f"// {marker}", f"/* {marker} */", f"  ; {marker}  ",
                     f"commit: not-a-sha\r\n# {marker}\r\nmore"):
            with self.subTest(marked=text):
                self.assertTrue(emit.carries_fixture_marker(text.encode()))
        for text in (f"a fixture carries `{marker}` on its own line",
                     f'MARKER = "{marker}"', f"see {marker}.",
                     f"# {marker} (explained in the README)"):
            with self.subTest(mention=text):
                self.assertFalse(emit.carries_fixture_marker(text.encode()))

    def test_no_file_this_tool_ships_trips_its_own_refusal(self):
        listed = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
            capture_output=True)
        if listed.returncode != 0:
            self.skipTest("not a git checkout")
        tripping = []
        for raw in listed.stdout.split(b"\0"):
            path = REPO_ROOT / raw.decode("utf-8", "surrogateescape")
            if raw and path.is_file() and emit.carries_fixture_marker(
                    path.read_bytes()):
                tripping.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(tripping, [])


class TestTheCandidateDomain(unittest.TestCase):
    """`CandidateCommit` against real git: every entry kind and index state
    W3 named, each with the bytes git will record. This is the domain the
    first draft predicted from display text; it is now READ, and these
    tests hold the read to what `git commit -a` then actually records
    (`recorded()` on the commit, compared by blob id)."""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="candidate-"))
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.git("init", "-q", "-b", "main")
        for k, v in (("user.name", "t"), ("user.email", "t@invalid"),
                     ("commit.gpgsign", "false")):
            self.git("config", k, v)
        (self.repo / "f.txt").write_text("one\n")
        self.git("add", "f.txt")
        self.git("commit", "-q", "-m", "init")
        self.marked = f"commit: not-a-sha\n# {emit.FIXTURE_MARKER}\n"

    def git(self, *args, env=None):
        return subprocess.run(["git", "-C", str(self.repo), *args],
                              check=True, capture_output=True,
                              env={**os.environ, **(env or {})}).stdout

    def changes(self):
        return {c["path"]: c for c in emit.CandidateCommit(self.repo).changes()}

    def commit_and_compare(self):
        """The whole contract in one call: candidate, then the real
        `commit -a`, then the commit's own diff equals the candidate."""
        before = emit.CandidateCommit(self.repo).changes()
        self.git("commit", "-q", "-a", "-m", "sweep")
        head = self.git("rev-parse", "HEAD").decode().strip()
        recorded = emit.CandidateCommit(self.repo).recorded(head)
        self.assertEqual({(c["status"], c["path"], c["blob"]) for c in before},
                         {(s, p, b) for s, p, _m, b in recorded})
        # And the real index was never touched by the read.
        self.assertEqual(self.git("status", "--porcelain"), b"")
        return {c["path"]: c for c in before}

    def test_quoted_filenames_are_read_losslessly(self):
        """W3 F1, THE REPRODUCTION. FALSIFICATION. Mutation: parse
        `status --porcelain` text again and `café.md` is read as
        `"caf\\303\\251.md"`, unreadable, taken for a deletion."""
        names = ["café.md", 'quo"te.md', "back\\slash.md", "sp ace.md",
                 "tab\tname.md"]
        for quote_path in ("true", "false"):
            with self.subTest(quotePath=quote_path):
                self.git("config", "core.quotePath", quote_path)
                for name in names:
                    (self.repo / name).write_text(self.marked)
                    self.git("add", "--intent-to-add", "--", name)
                found = self.changes()
                for name in names:
                    self.assertIn(name, found, list(found))
                    self.assertEqual(found[name]["status"], "A")
                    self.assertTrue(emit.carries_fixture_marker(
                        found[name]["bytes"]), name)
                for name in names:
                    self.git("rm", "-q", "--cached", "--", name)
                    (self.repo / name).unlink()

    def test_the_index_is_what_is_recorded_not_the_working_tree(self):
        """W3 F2, THE REPRODUCTION, both flags. FALSIFICATION. Mutation:
        read `Path(path).read_bytes()` instead of the staged blob and the
        marked bytes below are never seen."""
        for flag in ("--skip-worktree", "--assume-unchanged"):
            with self.subTest(flag=flag):
                (self.repo / "f.txt").write_text(self.marked)
                self.git("add", "f.txt")
                self.git("update-index", flag, "f.txt")
                (self.repo / "f.txt").write_text("innocent working tree\n")
                found = self.commit_and_compare()
                self.assertTrue(emit.carries_fixture_marker(
                    found["f.txt"]["bytes"]))
                self.git("update-index", "--no" + flag[1:], "f.txt")
                (self.repo / "f.txt").write_text("one\n")
                self.git("commit", "-q", "-a", "-m", "restore")

    def test_a_symlink_is_its_link_text(self):
        """W3 F2: a symlink to a marked file is NOT marked — git records the
        link text — and a legitimate link commits. FALSIFICATION. Mutation:
        follow the link and read the referent."""
        target = self.repo / "referent.md"
        target.write_text(self.marked)
        self.git("add", "referent.md")
        self.git("commit", "-q", "-m", "a marked file already committed")
        (self.repo / "link.md").symlink_to("referent.md")
        self.git("add", "--intent-to-add", "link.md")
        found = self.commit_and_compare()
        self.assertEqual(found["link.md"]["mode"], "120000")
        self.assertEqual(found["link.md"]["bytes"], b"referent.md")
        emit.sweep_preflight(list(found.values()), None)   # not refused

    def test_a_rename_is_both_ends_and_a_copy_is_one(self):
        """W3 F4. FALSIFICATION. Mutation: pass `-M` (rename detection) to
        the raw diff and the source path vanishes from the candidate."""
        (self.repo / "allowed").mkdir()
        self.git("mv", "f.txt", "allowed/f.txt")
        found = self.commit_and_compare()
        self.assertEqual({p: c["status"] for p, c in found.items()},
                         {"f.txt": "D", "allowed/f.txt": "A"})
        shutil.copy(self.repo / "allowed/f.txt", self.repo / "copy.txt")
        self.git("add", "--intent-to-add", "copy.txt")
        found = self.commit_and_compare()
        self.assertEqual({p: c["status"] for p, c in found.items()},
                         {"copy.txt": "A"})

    def test_every_status_and_type_change_is_read(self):
        (self.repo / "f.txt").write_text("two\n")          # M
        (self.repo / "g.txt").write_text("new\n")
        self.git("add", "--intent-to-add", "g.txt")         # A
        (self.repo / "h.txt").write_text("h\n")
        self.git("add", "h.txt")
        self.git("commit", "-q", "-m", "h")
        (self.repo / "h.txt").unlink()                       # D
        (self.repo / "h.txt").symlink_to("f.txt")            # T
        found = self.commit_and_compare()
        self.assertEqual({p: c["status"] for p, c in found.items()},
                         {"f.txt": "M", "g.txt": "A", "h.txt": "T"})
        self.assertEqual(found["h.txt"]["bytes"], b"f.txt")

    def test_a_gitlink_is_reported_with_no_bytes(self):
        inner = self.repo.parent / (self.repo.name + "-sub")
        self.addCleanup(shutil.rmtree, inner, ignore_errors=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(inner)],
                       check=True)
        for k, v in (("user.name", "t"), ("user.email", "t@invalid"),
                     ("commit.gpgsign", "false")):
            subprocess.run(["git", "-C", str(inner), "config", k, v],
                           check=True)
        (inner / "x").write_text("x")
        subprocess.run(["git", "-C", str(inner), "add", "x"], check=True)
        subprocess.run(["git", "-C", str(inner), "commit", "-q", "-m", "x"],
                       check=True)
        self.git("-c", "protocol.file.allow=always", "submodule", "add",
                 "-q", str(inner), "vendor/lib")
        self.git("commit", "-q", "-m", "submodule")
        (inner / "y").write_text("y")
        subprocess.run(["git", "-C", str(inner), "add", "y"], check=True)
        subprocess.run(["git", "-C", str(inner), "commit", "-q", "-m", "y"],
                       check=True)
        subprocess.run(["git", "-C", str(self.repo / "vendor/lib"), "pull",
                        "-q"], check=True)
        found = self.changes()
        self.assertEqual(found["vendor/lib"]["mode"], "160000")
        self.assertIsNone(found["vendor/lib"]["bytes"])
        with self.assertRaises(emit.SweepRefused):
            emit.sweep_preflight(list(found.values()), None)
        # W4 F2: no diff setting hides the gitlink from either reader.
        # FALSIFICATION. Mutation: drop `--ignore-submodules=none` from the
        # candidate reader and the first assertion fails; from the read-back
        # and the second does.
        for key in ("diff.ignoreSubmodules", "submodule.vendor/lib.ignore"):
            with self.subTest(setting=key):
                self.git("config", key, "all")
                self.assertIn("vendor/lib", self.changes())
                (self.repo / "f.txt").write_text("beside the pointer\n")
                self.assertEqual(set(self.changes()), {"vendor/lib", "f.txt"})
                self.git("config", "--unset", key)
                (self.repo / "f.txt").write_text("one\n")
        self.git("config", "diff.ignoreSubmodules", "all")
        # Under that setting `commit -a` stages nothing at all (measured:
        # "nothing to commit"), so the pointer is staged by name here; the
        # read-back must still see it.
        self.git("add", "vendor/lib")
        self.git("commit", "-q", "-m", "bump")
        head = self.git("rev-parse", "HEAD").decode().strip()
        recorded = emit.CandidateCommit(self.repo).recorded(head)
        self.assertIn("vendor/lib", {p for _s, p, _m, _b in recorded})
        self.git("config", "--unset", "diff.ignoreSubmodules")

    def test_a_clean_tree_is_no_change_and_the_index_is_left_alone(self):
        self.assertEqual(self.changes(), {})
        (self.repo / "f.txt").write_text("two\n")
        self.changes()
        self.assertEqual(self.git("diff", "--cached", "--name-only"), b"",
                         "the read staged something in the REAL index")

    def test_the_read_never_stages_in_the_real_index(self):
        (self.repo / "f.txt").write_text("two\n")
        self.changes()
        self.assertEqual(self.git("status", "--porcelain"), b" M f.txt\n")


class TestTheEnvironmentLine(unittest.TestCase):

    BASE = {"python": "3.14.1", "python_in_range": True,
            "python_range": ">=3.14,<3.15", "root": False,
            "bare_tool": "/x/loupe", "bare_version": "9.9.9",
            "tool_version": "9.9.9"}

    def line(self, **over):
        (line,) = emit.render_preflight_lines(
            {"environment": {**self.BASE, **over}})
        return line

    def test_the_normal_state_prints_too(self):
        self.assertEqual(self.line(), "Env:    python 3.14.1; not root; "
                                      "`loupe` on PATH is 9.9.9, this one")

    def test_every_deviation_is_named(self):
        self.assertIn("OUTSIDE the declared >=3.14,<3.15",
                      self.line(python="3.15.0", python_in_range=False))
        self.assertIn("; ROOT;", self.line(root=True))
        self.assertIn("identity unknown", self.line(root=None))
        self.assertIn("NOT this 9.9.9", self.line(bare_version="0.1.0"))
        self.assertIn("no `loupe` on PATH", self.line(bare_tool=None))

    def test_the_report_reads_this_process(self):
        report = emit.environment_report()
        self.assertEqual(report["python"],
                         ".".join(map(str, sys.version_info[:3])))
        self.assertEqual(report["tool_version"], review.TOOL_VERSION)
        self.assertIs(report["python_in_range"], True)


class TestHandoffEndToEnd(unittest.TestCase):
    """The msx case, reproduced: a suite killed at a timeout leaves a
    corrupt-by-design evidence file tracked by intent-to-add, and the next
    hand-off in that workspace would commit and push it."""

    def setUp(self):
        scratch = scratch_loop_repo(self, "preflight-", name="author",
                                    objective="preflight")
        self.tmp, self.repo = scratch.tmp, scratch.repo
        self.base, self.cwd = scratch.base, scratch.cwd
        self.state = self.tmp / "state"

    def claim(self, **members):
        path = self.tmp / f"claim-{len(members)}-{id(members)}.json"
        path.write_text(json.dumps({
            "objective": "preflight",
            "references": [{"path": "review.toml", "required": True}],
            **members}), encoding="utf-8")
        return path

    def handoff(self, claim, *extra):
        return run_cli(self.repo, self.state, "handoff", "--claim-file",
                       str(claim), "--base", self.base, "--local-only",
                       *extra, cwd=self.cwd)

    def head(self):
        return _git(self.repo, "rev-parse", "HEAD")

    def test_a_killed_suites_fixture_is_not_committed(self):
        """FALSIFICATION. Mutation: remove the `sweep_preflight` call from
        `ensure_pushed` and HEAD moves — the fixture is committed."""
        evidence = self.repo / "docs" / "evidence"
        evidence.mkdir(parents=True)
        (evidence / "phase0a.md").write_text(
            f"commit: not-a-sha\nexecuted_at: 1900-01-01\n"
            f"# {emit.FIXTURE_MARKER}\n", encoding="utf-8")
        sh("git", "-C", str(self.repo), "add", "--intent-to-add",
           "docs/evidence/phase0a.md")
        before = self.head()
        code, out = self.handoff(self.claim())
        self.assertEqual(code, 1, out)
        self.assertEqual(out["next_kind"], "blocked")
        self.assertIn("docs/evidence/phase0a.md", out["error"])
        self.assertEqual(self.head(), before, "the fixture was committed")
        self.assertIn("phase0a.md", _git(self.repo, "status", "--porcelain"),
                      "the refusal must leave the tree as it found it")

    def test_a_path_outside_the_scope_is_not_committed(self):
        (self.repo / "f.txt").write_text("three\n", encoding="utf-8")
        (self.repo / "review.toml").write_text(
            (self.repo / "review.toml").read_text(encoding="utf-8")
            + "\n# a stray edit\n", encoding="utf-8")
        before = self.head()
        code, out = self.handoff(self.claim(scope_paths=["f.txt"]))
        self.assertEqual(code, 1, out)
        self.assertIn("review.toml", out["error"])
        self.assertEqual(self.head(), before)

    def test_the_control_commits_and_the_request_names_what_it_swept(self):
        (self.repo / "f.txt").write_text("three\n", encoding="utf-8")
        before = self.head()
        code, out = self.handoff(self.claim(scope_paths=["f.txt"]))
        self.assertEqual(code, 0, out)
        self.assertNotEqual(self.head(), before)
        request = Path(out["kept"]).read_text(encoding="utf-8")
        self.assertIn("Swept:  1 path(s) committed by this hand-off (A added, "
                      "M modified, D deleted, T type changed): f.txt (M) "
                      "— all inside the declared `scope_paths`", request)
        self.assertRegex(request, r"(?m)^Env:    python \d+\.\d+\.\d+; ")
        self.assertRegex(request, r"(?m)^Scope paths:\n  - f\.txt$")

    def test_the_override_commits_and_is_stated_on_the_face(self):
        (self.repo / "f.txt").write_text("three\n", encoding="utf-8")
        (self.repo / "review.toml").write_text(
            (self.repo / "review.toml").read_text(encoding="utf-8")
            + "\n# a stray edit\n", encoding="utf-8")
        code, out = self.handoff(self.claim(scope_paths=["f.txt"]),
                                 "--allow-outside-scope")
        self.assertEqual(code, 0, out)
        request = Path(out["kept"]).read_text(encoding="utf-8")
        self.assertIn("OUTSIDE the declared `scope_paths`, swept under "
                      "--allow-outside-scope: review.toml", request)

    def test_a_hook_that_changes_the_commit_is_refused_after_it(self):
        """The post-commit proof (RVW-T17): a pre-commit hook adds a file, so
        the commit records more than the candidate. FALSIFICATION. Mutation:
        drop the `recorded` comparison and this hand-off emits over a
        commit the preflight never judged."""
        hooks = self.repo / ".git" / "hooks"
        hooks.mkdir(exist_ok=True)
        hook = hooks / "pre-commit"
        hook.write_text("#!/bin/sh\necho sneaked > sneaked.txt\n"
                        "git add sneaked.txt\n")
        hook.chmod(0o755)
        (self.repo / "f.txt").write_text("three\n", encoding="utf-8")
        before = self.head()
        code, out = self.handoff(self.claim(scope_paths=["f.txt"]))
        self.assertEqual(code, 1, out)
        self.assertIn("does not record what the preflight judged",
                      out["error"])
        self.assertIn("sneaked.txt", out["error"])
        self.assertNotEqual(self.head(), before,
                            "the commit exists, and the refusal says so")
        self.assertIn("is not pushed", out["error"])

    def test_a_hook_that_changes_only_the_mode_is_refused_after_it(self):
        """W4 F4. FALSIFICATION. Mutation: compare (status, path, blob)
        without the mode and both hooks below pass unnoticed."""
        hooks = self.repo / ".git" / "hooks"
        hooks.mkdir(exist_ok=True)
        hook = hooks / "pre-commit"
        for label, prepare in (
                ("a modified file", lambda: (self.repo / "f.txt").write_text(
                    "three\n", encoding="utf-8")),
                ("an added file", lambda: (
                    (self.repo / "g.txt").write_text("g\n", encoding="utf-8"),
                    sh("git", "-C", str(self.repo), "add", "--intent-to-add",
                       "g.txt")))):
            with self.subTest(label):
                prepare()
                target = "f.txt" if label == "a modified file" else "g.txt"
                hook.write_text(f"#!/bin/sh\ngit update-index --chmod=+x "
                                f"{target}\n")
                hook.chmod(0o755)
                code, out = self.handoff(self.claim())
                self.assertEqual(code, 1, out)
                self.assertIn("does not record what the preflight judged",
                              out["error"])
                self.assertIn("mode", out["error"])
                hook.unlink()
                sh("git", "-C", str(self.repo), "reset", "-q", "--hard",
                   "HEAD~1")
                sh("git", "-C", str(self.repo), "clean", "-q", "-fd")
        # The control: a file already executable, inspected as such, passes.
        (self.repo / "f.txt").chmod(0o755)
        (self.repo / "f.txt").write_text("four\n", encoding="utf-8")
        code, out = self.handoff(self.claim())
        self.assertEqual(code, 0, out)

    def test_the_msx_fixture_under_a_quoted_name_is_not_committed(self):
        """W3 F1 end to end: the same fixture, named `café.md`."""
        evidence = self.repo / "docs" / "evidence"
        evidence.mkdir(parents=True)
        (evidence / "café.md").write_text(
            f"commit: not-a-sha\n# {emit.FIXTURE_MARKER}\n", encoding="utf-8")
        sh("git", "-C", str(self.repo), "add", "--intent-to-add",
           "docs/evidence/café.md")
        before = self.head()
        code, out = self.handoff(self.claim())
        self.assertEqual(code, 1, out)
        self.assertIn("café.md", out["error"])
        self.assertEqual(self.head(), before)
        # And the paired control: an UNMARKED café.md, exactly in scope.
        (evidence / "café.md").write_text("fine\n", encoding="utf-8")
        code, out = self.handoff(self.claim(scope_paths=["docs/evidence/café.md"]))
        self.assertEqual(code, 0, out)
        self.assertIn("docs/evidence/café.md (A)",
                      Path(out["kept"]).read_text(encoding="utf-8"))

    def test_a_clean_tree_says_it_swept_nothing(self):
        code, out = self.handoff(self.claim())
        self.assertEqual(code, 0, out)
        self.assertIn("Swept:  nothing — the hand-off committed no "
                      "outstanding work",
                      Path(out["kept"]).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

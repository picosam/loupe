"""`copy_tree` never copies a tree git's detached maintenance still holds.

The race it closes (nightly run 36839648235, 2026-10-01): a fixture's build
commits, the commit leaves `git maintenance run --auto --detach` holding
`.git/objects/maintenance.lock`, and `copytree` lists the lock just before
the daemon deletes it. The daemon is played here by a thread, so the order
is fixed rather than left to a scheduler.

MUTATION: drop `settle_maintenance(src)` from `copy_tree` and
`test_a_held_lock_is_waited_out` copies the lock and
`test_a_lock_never_released_refuses` copies instead of refusing; make
`settle_maintenance` look at `src` alone (`glob`, not `rglob`) and both go
red the same way, since the lock sits three levels down.

What `_real_cli.loupe` rests on (Osama, "cut in tests only", 2026-11-05).

A row forked from one loaded process says and writes what a launched `bin/loupe`
says and writes, cannot fail unseen, and the launch stays the entry point of a
verb's first row. A `Scratch` is a private copy of one build.

`TestTheForkIsTheLaunch` plays one review both ways at the same path, from the
same bytes: launched whole, then forked. Every word of every row, every byte
left under the state directory, the remote and the checkout, and what a gate is
handed must agree, but for the clock and the ids drawn fresh (`Neutral`).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from review.tests import _real_cli, synth
from review.tests import _transport_fixtures as fx
from review.tests._real_cli import LOUPE, Scratch, git, loupe, loupe_env
from review.tests._transport_fixtures import copy_tree
from review.tests.test_respond_input import _ClassCase
from review.tests.util import REPO_ROOT

LOCK = Path(".git", "objects", "maintenance.lock")


class _Tree(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fixture-copy-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = self.tmp / "src"
        (self.src / LOCK).parent.mkdir(parents=True)
        (self.src / "f.txt").write_text("one\n", encoding="utf-8")
        self.dst = self.tmp / "dst"


class TestCopyWaitsForMaintenance(_Tree):
    def test_a_held_lock_is_waited_out(self):
        (self.src / LOCK).write_text("", encoding="utf-8")
        released = []

        def daemon():
            time.sleep(0.2)
            (self.src / LOCK).unlink()
            released.append(time.monotonic())

        t = threading.Thread(target=daemon)
        t.start()
        self.addCleanup(t.join)
        fx.copy_tree(self.src, self.dst)
        done = time.monotonic()
        t.join()
        self.assertTrue(released and released[0] <= done)
        self.assertFalse((self.dst / LOCK).exists())
        self.assertEqual((self.dst / "f.txt").read_text(encoding="utf-8"),
                         "one\n")

    def test_a_lock_never_released_refuses(self):
        (self.src / LOCK).write_text("", encoding="utf-8")
        with mock.patch.object(fx, "MAINTENANCE_SETTLE_S", 0.05):
            with self.assertRaises(AssertionError) as cm:
                fx.copy_tree(self.src, self.dst)
        self.assertIn(str(self.src / LOCK), str(cm.exception))
        self.assertFalse(self.dst.exists())

    def test_a_tree_with_no_lock_copies_at_once(self):
        started = time.monotonic()
        fx.copy_tree(self.src, self.dst)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertTrue((self.dst / "f.txt").is_file())


class TestARealCommitSettles(unittest.TestCase):
    """The same path through real git: a repository copied straight after
    its commits holds no lock and every commit in the copy."""

    def test_a_just_committed_repository_copies_whole(self):
        tmp = Path(tempfile.mkdtemp(prefix="fixture-copy-git-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        src = tmp / "src"
        fx.sh("git", "init", "-q", "-b", "main", str(src))
        fx.write_identity(src)
        for n in range(3):
            (src / "f.txt").write_text(f"{n}\n", encoding="utf-8")
            fx.sh("git", "-C", str(src), "add", "f.txt")
            fx.sh("git", "-C", str(src), "commit", "-q", "-m", f"c{n}")
        fx.copy_tree(src, tmp / "dst")
        self.assertFalse((tmp / "dst" / LOCK).exists())
        out = subprocess.run(
            ["git", "-C", str(tmp / "dst"), "log", "--format=%s"],
            check=True, capture_output=True, text=True).stdout
        self.assertEqual(out, "c2\nc1\nc0\n")


if __name__ == "__main__":
    unittest.main()


#: A gate that keeps the environment it was given: `bin/loupe` restores the
#: caller's PYTHONPATH and PYTHONSAFEPATH for a gate from what it stashed, so a
#: difference in what a row was handed shows here. No shell, which would set
#: PWD for itself.
ENV_GATE = f"""
[[gates]]
id = "env"
command = {json.dumps([sys.executable, "-c",
                       "import json, os; json.dump(dict(os.environ), "
                       "open(os.environ['GATE_ENV_OUT'], 'w'))"])}
blocking = true
"""
#: What differs between two runs of one review whichever way it is run.
CLOCK = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:?\d\d)?"
                   r'|"duration_s": [\d.]+')
#: What a launch's `sh` sets or counts and a program never sees to act on.
SHELL_ONLY = ("_", "OLDPWD", "SHLVL")


class Neutral:
    """One run's words with the clock taken out, and each id drawn fresh or
    derived from one (a lineage, a digest of a text naming it, a gate run)
    named by the order it first appears: a row that repeats an id must repeat
    it in the same place."""

    DRAWN = re.compile(r"\b(?:L[0-9a-f]{10}|[0-9a-f]{12,64}"
                       r"|\d{8}T\d{6}Z-[0-9a-f]{6})\b")

    def __init__(self):
        self.ids = {}

    def __call__(self, text: str) -> str:
        return self.DRAWN.sub(
            lambda m: self.ids.setdefault(m[0], f"<{len(self.ids)}>"),
            CLOCK.sub("<clock>", text))

    def tree(self, top: Path) -> dict:
        return {self(p.relative_to(top).as_posix()): self(
                    p.read_bytes().decode("utf-8", "replace"))
                for p in sorted(top.rglob("*"))
                if p.is_file() and not p.name.endswith(".lock")}


def handed(text: str) -> dict:
    """What `ENV_GATE` kept, less what only a launch's `sh` sets or counts."""
    return {k: v for k, v in json.loads(text).items() if k not in SHELL_ONLY}


def same_handed(case, launch: dict, fork: dict) -> None:
    """A forked row sets PWD as `sh` does; a shell that does not export it is
    the one difference allowed."""
    if "PWD" not in launch:
        fork = {k: v for k, v in fork.items() if k != "PWD"}
    case.assertEqual(launch, fork)


def launched(argv, cwd, env, timeout=180):
    proc = subprocess.run([str(LOUPE), *argv], cwd=cwd, env=env,
                          capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def verdict(sha):
    return (f'<{synth.TAG}-review-verdict sha="{sha}">\nVERDICT: changes '
            f"requested\n\n## findings\n\n"
            + synth.finding(1, severity="High", evidence="f.txt:1")
            + f"## evidence checked\n\nf.txt\n</{synth.TAG}-review-verdict>\n")


class TestTheForkIsTheLaunch(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.s = Scratch(_ClassCase(cls), "real-cli-", gates=ENV_GATE)
        saved = Path(tempfile.mkdtemp(prefix="real-cli-saved-")).resolve()
        cls.addClassCleanup(shutil.rmtree, saved, True)
        Scratch.settle(cls.s.repo)
        copy_tree(cls.s.root, saved / "t")

        def play(way):
            shutil.rmtree(cls.s.root)
            copy_tree(saved / "t", cls.s.root)
            return cls.review(way)
        cls.runs = {"launched": play(launched),
                    "forked": play(_real_cli.forked)}

    @classmethod
    def review(cls, way):
        """Each row of one review by `way`: (rows said, bytes left, what the
        gate was handed)."""
        s = cls.s
        said, env, neutral = [], loupe_env(s, s.state), Neutral()

        def row(*argv, where=None, extra=None):
            code, out, err = way([str(a) for a in argv], str(where or s.repo),
                                 {**env, **(extra or {})})
            said.append((tuple(neutral(str(a)) for a in argv), code,
                         neutral(out), neutral(err)))
            return json.loads(out) if out.startswith("{") else None

        row("--version")
        row("--help")
        row("handoff", "--help")
        row("no-such-verb")
        row("brief")
        row("validate", s.root / "absent.md")
        row("brief", where=s.root)                      # not a repository
        kept = s.root / "gate-env"
        given = row("handoff", "--claim-file", s.claim, "--base", s.base,
                    extra={"GATE_ENV_OUT": str(kept)})
        if "kept" not in (given or {}):
            raise AssertionError(f"the hand-off did not go through: {said[-1]}")
        row("handoff", "--claim-file", s.claim, "--base", s.base)
        row("brief")
        row("take", given["kept"], "--as", "codex")
        malformed = s.root / "malformed.md"
        malformed.write_text("VERDICT: nothing\n", encoding="utf-8")
        row("close", "--verdict", malformed)
        row("close", "--verdict", "-")                  # stdin is /dev/null
        path = s.root / "v1.md"
        path.write_text(verdict(given["sha"]), encoding="utf-8")
        row("validate", path)
        row("close", "--verdict", path)
        answer = s.root / "answer.json"
        answer.write_text(json.dumps({
            "head": given["sha"], "dispositions": [{
                "finding_id": "F1", "disposition": "refuted",
                "payload": {"evidence": "f.txt:1 says otherwise"}}]}),
            encoding="utf-8")
        row("respond", "--verdict", path, "--from-json", answer,
            "--out", s.root / "answer.md")
        row("validate", s.root / "answer.md", "--against", path)
        row("ledger", "add", s.root / "answer.md")
        row("ledger")
        row("brief", extra={"CODEX_HOME": str(s.root)})
        left = {"state": neutral.tree(s.state),
                "remote": git(s.remote, "for-each-ref",
                              "--format=%(refname) %(objectname)"),
                "checkout": git(s.repo, "status", "--porcelain=v2", "-z",
                                "--untracked-files=all"),
                "out": neutral((s.root / "answer.md").read_text("utf-8"))}
        return said, left, kept.read_text(encoding="utf-8")

    def test_every_row_says_what_it_says_launched(self):
        launch, fork = self.runs["launched"][0], self.runs["forked"][0]
        self.assertEqual([r[0] for r in launch], [r[0] for r in fork])
        self.assertGreater(len({r[1] for r in launch}), 1, "no row refused")
        for a, b in zip(launch, fork):
            with self.subTest(argv=a[0]):
                self.assertEqual(a, b)

    def test_it_leaves_what_it_leaves_launched(self):
        launch, fork = self.runs["launched"][1], self.runs["forked"][1]
        self.assertTrue(launch["state"], "the review wrote nothing")
        self.assertEqual(sorted(launch["state"]), sorted(fork["state"]))
        for name in launch["state"]:
            with self.subTest(name=name):
                self.assertEqual(launch["state"][name], fork["state"][name])
        for name in ("remote", "checkout", "out"):
            self.assertEqual(launch[name], fork[name], name)

    def test_a_gate_is_handed_what_it_is_handed_launched(self):
        launch, fork = (handed(self.runs[w][2]) for w in ("launched", "forked"))
        self.assertIn("HOME", launch)
        same_handed(self, launch, fork)


class TestACallersPathIsStashedForTheGates(unittest.TestCase):
    """`bin/loupe` keeps a caller's PYTHONPATH and PYTHONSAFEPATH for its gates
    (`ScratchLoop` is such a caller): a forked row does the same."""

    def test_a_gate_gets_the_callers_values_back_either_way(self):
        seen = {}
        for name, way in (("launched", launched), ("forked", _real_cli.forked)):
            s = Scratch(self, "stash-", gates=ENV_GATE)
            kept = s.root / "gate-env"
            code, out, err = way(
                ["handoff", "--claim-file", str(s.claim), "--base", s.base],
                str(s.repo),
                {**loupe_env(s, s.state), "PYTHONPATH": str(REPO_ROOT),
                 "PYTHONSAFEPATH": "1", "GATE_ENV_OUT": str(kept)})
            self.assertEqual(code, 0, (out, err))
            seen[name] = handed(kept.read_text(encoding="utf-8").replace(
                str(s.root), "<root>"))
        self.assertEqual(seen["forked"]["PYTHONPATH"], str(REPO_ROOT))
        self.assertEqual(seen["forked"]["PYTHONSAFEPATH"], "1")
        same_handed(self, seen["launched"], seen["forked"])


class TestTheLoadBeforeTheFork(unittest.TestCase):
    """A forked row can only be the row if loading the package decides nothing
    the row's own environment or directory should."""

    PROBE = r"""
import json, os, threading
read, cwd = [], []
class Environ(type(os.environ)):
    def __getitem__(self, key):
        read.append(key)
        return super().__getitem__(key)
os.environ.__class__ = Environ
real = os.getcwd
os.getcwd = lambda: (cwd.append(1), real())[1]
from review.cli import main
import tempfile
print(json.dumps({"read": sorted(set(read)), "cwd": len(cwd),
                  "tempdir": tempfile.tempdir,
                  "threads": threading.active_count()}))
"""

    def test_it_reads_no_variable_or_directory_and_starts_no_thread(self):
        env = dict(os.environ, PYTHONSAFEPATH="1", PYTHONPATH=str(REPO_ROOT))
        done = subprocess.run([sys.executable, "-c", self.PROBE], env=env,
                              capture_output=True, text=True, timeout=60,
                              stdin=subprocess.DEVNULL)
        self.assertEqual(done.returncode, 0, done.stderr)
        seen = json.loads(done.stdout)
        # The standard library's own switch for how `subprocess` starts a child.
        self.assertEqual(set(seen["read"]) - {
            "_PYTHON_SUBPROCESS_USE_POSIX_SPAWN"}, set())
        self.assertEqual((seen["cwd"], seen["tempdir"], seen["threads"]),
                         (0, None, 1))


class TestTheLoadedProcess(unittest.TestCase):
    """A row cannot fail unseen, nor the process outlive its test run."""

    def setUp(self):
        self.s = Scratch(self, "loaded-")
        self.loaded = _real_cli._Loaded()
        self.addCleanup(self.loaded.close)

    def row(self, *argv, timeout=60):
        cwd = str(self.s.repo)
        return self.loaded.run(list(argv), cwd, _real_cli.as_launched(
            loupe_env(self.s, self.s.state), cwd), timeout)

    def test_a_row_past_its_timeout_is_killed_and_the_next_runs(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.row("handoff", "--claim-file", str(self.s.claim), "--base",
                     self.s.base, timeout=0.05)
        code, out, _ = self.row("--version")
        self.assertEqual(code, 0, out)

    def test_a_row_ending_nonzero_says_so(self):
        code, out, err = self.row("no-such-verb")
        self.assertEqual(code, 2, (out, err))

    def test_its_death_is_said(self):
        self.loaded.proc.kill()
        self.loaded.proc.wait(timeout=30)
        with self.assertRaisesRegex(AssertionError, "the loaded process died"):
            self.row("--version")

    def test_a_closed_pipe_ends_it(self):
        self.loaded.close()
        self.assertEqual(self.loaded.proc.returncode, 0)


class TestWhichRowsAreLaunched(unittest.TestCase):

    def setUp(self):
        self.s = Scratch(self, "which-rows-")
        patcher = mock.patch.object(_real_cli, "_LAUNCHED", set())
        patcher.start()
        self.addCleanup(patcher.stop)

    def launches(self, *argv, **kw) -> int:
        """How many times one row launched `bin/loupe`."""
        real, seen = subprocess.run, []

        def spy(cmd, *a, **k):
            if cmd[0] == str(LOUPE):
                seen.append(cmd)
            return real(cmd, *a, **k)
        with mock.patch.object(subprocess, "run", spy):
            code, _, out, err = loupe(self.s, *argv, **kw)
        self.assertNotIn("Traceback", err)
        self.assertEqual(code, 0, (out, err))
        return len(seen)

    def test_a_verbs_first_row_is_launched_and_the_next_forked(self):
        self.assertEqual(self.launches("ledger", "--help"), 1)
        self.assertEqual(self.launches("ledger", "--help"), 0)
        self.assertEqual(self.launches("--version"), 1)
        self.assertEqual(self.launches("--version"), 0)

    def test_a_row_naming_what_the_interpreter_reads_is_launched(self):
        self.launches("ledger", "--help")
        for name, value in (("PYTHONWARNINGS", "default"),
                            ("LC_ALL", "C.UTF-8"), ("LANG", "C.UTF-8"),
                            ("PATH", os.environ["PATH"])):
            with self.subTest(name=name):
                self.assertEqual(self.launches("ledger", "--help", env={name: value}), 1)
        self.assertEqual(self.launches("ledger", "--help", env={"CODEX_HOME": ""}), 0)

    def test_a_row_from_another_thread_is_launched(self):
        self.launches("ledger", "--help")
        counts = []
        worker = threading.Thread(
            target=lambda: counts.append(self.launches("ledger", "--help")))
        worker.start()
        worker.join()
        self.assertEqual(counts, [1])

    def test_every_row_can_be_launched(self):
        self.launches("ledger", "--help")
        with mock.patch.dict(os.environ, TEST_LAUNCH_EVERY_ROW="1"):
            self.assertEqual(self.launches("ledger", "--help"), 1)

    def test_a_directory_that_is_gone_fails_as_a_launch_does(self):
        self.launches("ledger", "--help")
        with self.assertRaises(FileNotFoundError):
            loupe(self.s, "ledger", where=self.s.root / "absent")


class TestASnapshotSeesEveryRefMove(unittest.TestCase):
    """`Scratch.snapshot` reads refs from their files, not through git."""

    def setUp(self):
        self.s = Scratch(self, "snapshot-")
        self.before = Scratch.snapshot(self.s.repo)

    def moved(self, *args) -> bool:
        git(self.s.repo, *args)
        return Scratch.snapshot(self.s.repo)["refs"] != self.before["refs"]

    def test_a_branch_moved_added_or_deleted(self):
        self.assertTrue(self.moved("update-ref", "refs/heads/main", self.s.base))
        self.s = Scratch(self, "snapshot-")
        self.before = Scratch.snapshot(self.s.repo)
        self.assertTrue(self.moved("branch", "other", self.s.base))
        self.before = Scratch.snapshot(self.s.repo)
        self.assertTrue(self.moved("branch", "-D", "other"))

    def test_head_repointed(self):
        git(self.s.repo, "branch", "other")
        self.before = Scratch.snapshot(self.s.repo)
        self.assertTrue(self.moved("symbolic-ref", "HEAD", "refs/heads/other"))

    def test_a_ref_only_packed_counts_and_a_lock_does_not(self):
        lock = self.s.repo / ".git" / "refs" / "heads" / "main.lock"
        lock.write_text("", encoding="utf-8")
        self.assertEqual(Scratch.snapshot(self.s.repo)["refs"],
                         self.before["refs"])
        lock.unlink()
        self.assertTrue(self.moved("pack-refs", "--all"))

    def test_a_push_shows_in_the_bare_remote(self):
        before = Scratch.snapshot(self.s.remote, bare=True)
        git(self.s.repo, "push", "-q", "origin", "main")
        self.assertNotEqual(Scratch.snapshot(self.s.remote, bare=True), before)


class TestARestoredStateIsTheBuildsBytes(unittest.TestCase):
    """`_real_cli.restored`: built once, put back whole at its own path."""

    def setUp(self):
        self.builds = 0
        patcher = mock.patch.dict(_real_cli._RESTORABLE, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def build(self, case):
        self.builds += 1
        s = Scratch(case, "restored-")
        code, rec, out, err = loupe(s, "handoff", "--claim-file", str(s.claim),
                                    "--base", s.base)
        self.assertEqual(code, 0, (out, err))
        return s, rec

    def test_a_write_in_one_is_gone_from_the_next_and_its_verbs_read_it(self):
        key = ("test", self.id())
        a, first = _real_cli.restored(self, key, self.build)
        before = Neutral().tree(a.state)
        (a.state / "ledger.jsonl").write_text("not json\n", encoding="utf-8")
        (a.repo / "f.txt").write_text("edited\n", encoding="utf-8")
        first["sha"] = "changed by this caller"
        b, again = _real_cli.restored(self, key, self.build)
        self.assertEqual(self.builds, 1)
        self.assertEqual(b.root, a.root)
        self.assertIs(b.case, self)
        self.assertEqual(Neutral().tree(b.state), before)
        self.assertEqual((b.repo / "f.txt").read_text(encoding="utf-8"),
                         "two\n")
        self.assertEqual(again["sha"], b.head)
        code, seen, out, err = loupe(b, "brief")
        self.assertEqual(code, 0, (out, err))
        self.assertEqual(seen["source"], again["kept"])


class TestAScratchIsACopyOfOneBuild(unittest.TestCase):

    def test_two_scratches_are_private_and_share_their_commits(self):
        a, b = Scratch(self, "copy-a-"), Scratch(self, "copy-b-")
        self.assertNotEqual(a.root, b.root)
        self.assertEqual((a.base, a.head), (b.base, b.head))
        for s in (a, b):
            self.assertEqual(git(s.repo, "rev-parse", "HEAD"), s.head)
            self.assertEqual(git(s.repo, "config", "core.hooksPath"),
                             str(s.hooks))
            self.assertEqual(git(s.repo, "remote", "get-url", "origin"), s.url)
            self.assertEqual(git(s.remote, "rev-parse", "refs/heads/main"),
                             s.base)
        (a.repo / "f.txt").write_text("three\n", encoding="utf-8")
        git(a.repo, "commit", "-qam", "only here")
        git(a.repo, "push", "-q", "origin", "main")
        self.assertEqual(git(b.repo, "rev-parse", "HEAD"), b.head)
        self.assertEqual(git(b.remote, "rev-parse", "refs/heads/main"), b.base)
        self.assertEqual((b.repo / "f.txt").read_text(encoding="utf-8"),
                         "two\n")
        self.assertEqual(Scratch(self, "copy-c-").head, b.head,
                         "a later copy saw the first copy's commit")

    def test_each_shape_has_its_own_build(self):
        plain = Scratch(self, "shape-")
        ceiling = Scratch(self, "shape-", git_timeout=7)
        by_url = Scratch(self, "shape-", url_form="file")
        elsewhere = Scratch(self, "shape-", remote_dir="deep/er/origin.git")
        self.assertEqual(ceiling.ceiling(), 7)
        self.assertNotEqual(plain.ceiling(), 7)
        self.assertEqual(git(by_url.repo, "remote", "get-url", "origin"),
                         f"file://{by_url.remote}")
        self.assertEqual(git(elsewhere.remote, "rev-parse", "refs/heads/main"),
                         elsewhere.base)

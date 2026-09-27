"""Every git read launches git: no memo, no batch process, no cache.

The guard of the operator's ruling on lineage `Lc31cc149ce` (2026-09-27,
round 2, option 1). 0.27.0 first shipped a per-invocation memo of git reads
and a long-lived `cat-file --batch-command` reader, and review showed twice
that a transparent cache cannot see what changes a read's answer:

  round 1 (F1, F2): a linked worktree shares `refs/*`, the repository
  configuration and `.git/shallow` with this checkout, and a fetch, a
  `git config` or a ref update there — or in a gate or a hook — is a git
  process no launch of this invocation sees; and output the configuration
  formats (`ls-tree` without `-z`, `cat-file -p`) changes with
  `core.quotePath` although every object id stays the same.
  round 2 (F1): a full object id fixes an object's CONTENT, not this
  repository's ACCESS to it. `objects/info/alternates` and object storage
  change which ids resolve, so a successful read of a full id can become a
  refusal (and a refusal a success) between two reads of one invocation.

So the memo and the batch reader were withdrawn and every runner reads
exactly as 0.26.0's did (`emit._git`, `_git_bytes`, `_git_raw`,
`transport._git`, `run_bytes`, `config._git`; `corpus.Reader._git` is
covered in the workbench-only `test_corpus`). This module keeps every
regression test of those two rounds as a guard: each asks its read inside
ONE real `cli.main` call (`in_one_invocation`, the entry point where the
memo lived), changes the input where a change is the point, and requires
the answer to equal a fresh git call; the launch guards also require every
repeated read to launch git again (`counting`, a `Popen` subclass that sees
`subprocess.run` and any long-lived process alike). Everything runs through
real git in temporary repositories.

Each changed-input test is paired with an unchanged-input control that
asserts the ANSWER, not the launch count, so a control stays green under a
cache while the changed-input test beside it goes red.

RED MUTATIONS, each applied to the production code once and this module
run against it; the tests named failed as recorded, and every control
stayed green:

  the memo as reviewed in round 2 (lineage F1), 2026-09-27: the five
  production modules restored from `03d2491` and this module and
  `test_corpus` run against them, then 0.26.0's put back —
      git checkout 03d2491 -- review/config.py review/emit.py \
          review/transport.py review/corpus.py review/cli.py
      python3.15 -B -m unittest review.tests.test_git_memo \
          review.tests.test_corpus
      git checkout 18803e4 -- (the same five paths)
  45 failures. FAILS every row of test_a_withdrawn_alternate_turns_a_
  success_into_git_s_refusal (13 of 13, each "RuntimeError not raised":
  the memo's stale success), both rows of test_another_object_after_the_
  withdrawal_is_refused (the batch process's stale view of an object it
  was never asked for), test_corpus's test_a_withdrawn_alternate_is_the_
  readers_refusal; and every launch guard: test_every_formerly_kept_shape_
  launches_each_time (11 of 11), test_each_environment_launches,
  test_a_git_wide_option_and_its_absence_both_launch, test_corpus's
  test_a_repeated_corpus_read_launches_each_time, and five of the six
  tests of TestObjectReadsAnswerAsGitDoes (15 subtests, served by the
  batch or kept; the tree read through `show` passes, since the batch
  never served a tree). PASSES:
  test_unchanged_availability_control (lent and withdrawn, every row, and
  test_corpus's), test_a_bare_full_id_answers_without_the_object, and
  test_a_lent_alternate_turns_a_refusal_into_the_object with test_corpus's
  test_a_lent_alternate_is_the_object — the reviewed memo kept no refusal,
  and git's batch reader re-reads the alternates on a miss (git 2.54), so
  that direction is guarded but was not broken. The round-1 guards all
  PASS under it (round 1 was already fixed there).
  the memo with its moving class (round 1, F1 and F2): the same procedure
  with `edd7baf` in place of `03d2491`: 62 failures — the 45 above, and
  every round-1 changed-input test: TestALinkedWorktreeMovesSharedState's
  test_a_fetch_there_moves_the_remote_tracking_ref, both reads of
  test_a_config_edit_there_moves_the_remote_url, test_a_deepening_fetch_
  there_is_seen and test_a_fetch_there_that_makes_a_complete_clone_
  shallow_is_seen; test_ls_tree_without_z_follows_core_quotepath and
  test_cat_file_p_of_a_tree_follows_core_quotepath; all nine rows of
  TestEveryWritePathIsSeenByTheNextRead; test_a_moving_read_launches_each_
  time. Its three unchanged-input controls, the `-z` and ASCII-name
  controls and every alternates control PASS. TestAShallowCloneMovesItsCut
  passes under it (a tool launch between its reads dropped the moving
  class), so:
  history walks kept again: `03d2491` restored as above, then in
  `config.py` `rev-list --count` and `merge-base --is-ancestor|
  --independent` added to `_STABLE_OPTIONS` and `[~^][0-9]*` to
  `_REV_SUFFIX`; this module alone: 48 failures — the 43 of this module
  above, and all three tests of TestAShallowCloneMovesItsCut with both
  shallow tests of TestALinkedWorktreeMovesSharedState. test_the_history_
  walks_unchanged_control PASSES.

Removed with the memo, because what they guarded no longer exists: the
scope-identity test (a nested entry shares the open scope), the classifier
table (`git_read_class`), and the batch reader's own tests — a hung batch
refuses with the same timeout, the batch is closed when `main` returns and
when it raises, and the batch runs in the caller's environment. Timeouts
and the caller's environment at every runner stay guarded by
`test_git_timeout` and `test_gate_environment`.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from review import cli, config, emit, env_var, transport
from review.tests.util import REPO_ROOT  # noqa: F401 - the travelling set

#: A test that runs real gates must not look nested inside a gate execution
#: to `emit.run_gates`, which refuses re-entry: the hand-off's `tests` gate
#: sets the marker for every worker, as `test_gate_environment` records.
IN_GATE = env_var("IN_GATE_RUN")

NO_REPLACE = "--no-replace-objects"


def _outside_a_gate_run(**overrides) -> dict:
    env = {k: v for k, v in os.environ.items() if k != IN_GATE}
    env.update(overrides)
    return env


# ---------------------------------------------------------------- helpers

def _sh(*argv, cwd=None, env=None, input_bytes=None) -> str:
    out = subprocess.run(list(argv), check=True, capture_output=True,
                         timeout=60, cwd=cwd, env=env, input=input_bytes)
    return out.stdout.decode("utf-8", "surrogateescape").strip()


def _git_refusal(repo: Path, *args) -> str:
    """Git's own message for a read that fails, launched directly."""
    out = subprocess.run(["git", NO_REPLACE, "-C", str(repo), *args],
                         capture_output=True, timeout=60)
    assert out.returncode != 0, (args, out.stdout)
    return out.stderr.decode("utf-8", "replace").strip()


class Launches(list):
    """Every process started while it is active, as its argv."""

    def git(self, *words) -> list:
        """The git launches whose argv contains every word given."""
        return [a for a in self if a and Path(a[0]).name == "git"
                and all(w in a for w in words)]


@contextlib.contextmanager
def counting():
    seen = Launches()
    real = subprocess.Popen

    class Counting(real):
        def __init__(self, args, *a, **kw):
            seen.append([str(x) for x in args]
                        if isinstance(args, (list, tuple)) else [str(args)])
            super().__init__(args, *a, **kw)

    with mock.patch.object(subprocess, "Popen", Counting):
        yield seen


def in_one_invocation(call):
    """`call()` run as the command of ONE real `cli.main` call; its value
    returned, or its exception re-raised once `main` has returned.

    Only the argument parser and the configuration load are stood in for
    (a probe command, no configuration), so everything `main` wraps around
    a command — as the withdrawn memo's scope did — wraps `call` too. That
    is what lets a test here go red when a cache comes back."""
    outcome: dict = {}

    def command(args, cfg):
        try:
            outcome["value"] = call()
        except BaseException as exc:  # re-raised below, outside `main`
            outcome["error"] = exc
        return 0

    parser = mock.Mock()
    parser.parse_args.return_value = argparse.Namespace(
        command="probe", ledger_dir=None, func=command)
    real_load, loads = config.load, []

    def load(*a, **kw):
        if not loads:  # `main`'s own load; any later one is real
            loads.append(1)
            return None
        return real_load(*a, **kw)

    with mock.patch.object(cli, "build_parser", return_value=parser), \
            mock.patch.object(config, "load", load):
        code = cli.main([])
    if "error" in outcome:
        raise outcome["error"]
    assert code == 0, code
    return outcome.get("value")


BINARY = b"\x00\x01\xff\xfe\r\nline\r\n\x00tail without a newline"


class Repo(unittest.TestCase):
    """A scratch repository: a base and a target commit, a binary blob, a
    path with a space and a non-ASCII name, a directory, and a bare remote
    the branch is pushed to."""

    def setUp(self):
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="git-memo-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.repo = self.tmp / "repo"
        self.remote = self.tmp / "remote.git"
        _sh("git", "init", "-q", "-b", "main", str(self.repo))
        for k, v in (("user.name", "a"), ("user.email", "a@example.invalid"),
                     ("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
            self.git("config", k, v)
        (self.repo / "f.txt").write_text("one\n", encoding="utf-8")
        (self.repo / "bin.dat").write_bytes(BINARY)
        (self.repo / "dir").mkdir()
        self.odd = "dir/space é name.txt"
        (self.repo / self.odd).write_text("odd\n", encoding="utf-8")
        self.git("add", "f.txt", "bin.dat", self.odd)
        self.git("commit", "-q", "-m", "base")
        self.base = self.git("rev-parse", "HEAD")
        (self.repo / "f.txt").write_text("two\n", encoding="utf-8")
        self.git("commit", "-qam", "target")
        self.target = self.git("rev-parse", "HEAD")
        _sh("git", "init", "-q", "--bare", "-b", "main", str(self.remote))
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-q", "-u", "origin", "main")
        self.cfg = dataclasses.replace(
            config.load(self.repo, ledger_dir=str(self.tmp / "state")),
            repo_root=self.repo, ledger_dir=self.tmp / "state")

    def git(self, *args) -> str:
        return _sh("git", "-C", str(self.repo), *args)

    def hook(self, name: str, body: str) -> None:
        path = self.repo / ".git" / "hooks" / name
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP
                   | stat.S_IXOTH)

    def fake_bin(self, name: str, body: str) -> Path:
        bindir = self.tmp / "bin"
        bindir.mkdir(exist_ok=True)
        path = bindir / name
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        path.chmod(0o755)
        return bindir


class Donor(Repo):
    """`Repo`, plus a donor repository whose objects this repository does
    not hold: a commit with two blobs (`d1.txt`, `d2.txt`) whose contents
    exist nowhere else. `lend()` makes them reachable through this
    repository's `objects/info/alternates`, `withdraw()` removes that file;
    both are plain file writes, which no git launch of the tool sees."""

    def setUp(self):
        super().setUp()
        self.donor = self.tmp / "donor"
        _sh("git", "init", "-q", "-b", "main", str(self.donor))
        (self.donor / "d1.txt").write_text("donor one\n", encoding="utf-8")
        (self.donor / "d2.txt").write_text("donor two\n", encoding="utf-8")
        _sh("git", "-C", str(self.donor), "add", "d1.txt", "d2.txt")
        _sh("git", "-C", str(self.donor), "-c", "user.name=d", "-c",
            "user.email=d@example.invalid", "-c", "commit.gpgsign=false",
            "commit", "-q", "-m", "donor")
        self.lent = _sh("git", "-C", str(self.donor), "rev-parse", "HEAD")
        self.blob1 = _sh("git", "-C", str(self.donor), "rev-parse",
                         "HEAD:d1.txt")
        self.blob2 = _sh("git", "-C", str(self.donor), "rev-parse",
                         "HEAD:d2.txt")
        self.alternates = self.repo / ".git" / "objects" / "info" / \
            "alternates"
        self.withdraw()

    def lend(self) -> None:
        self.alternates.parent.mkdir(parents=True, exist_ok=True)
        self.alternates.write_text(
            f"{self.donor / '.git' / 'objects'}\n", encoding="utf-8")

    def withdraw(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self.alternates.unlink()


# ------------------------------------------------------ every read launches

class TestEveryReadLaunches(Repo):

    def test_every_formerly_kept_shape_launches_each_time(self):
        """Through the real entry point: each read shape the withdrawn memo
        kept for the whole invocation (full object ids, replacement objects
        off, through the admitted verbs and options), asked twice in one
        `cli.main` call, launches git twice and answers what git answers."""
        t = self.target
        shapes = (
            ("emit._git", emit._git, ("rev-parse", "--verify",
                                      f"{t}^{{commit}}")),
            ("transport._git", transport._git, ("rev-parse", "--verify",
                                                f"{t}^{{commit}}")),
            ("emit._git", emit._git, ("cat-file", "-t", f"{t}:dir")),
            ("transport._git", transport._git, ("cat-file", "-e",
                                                f"{t}^{{commit}}")),
            ("emit._git", emit._git, ("cat-file", "-s", f"{t}:f.txt")),
            ("emit._git", emit._git, ("ls-tree", "-z", "--full-tree", t)),
            ("emit._git", emit._git, ("ls-tree", "-z", "-r", "--name-only",
                                      t)),
            ("emit._git_bytes", emit._git_bytes, ("show", f"{t}:f.txt")),
            ("emit._git_bytes", emit._git_bytes, ("cat-file", "-t",
                                                  f"{t}:f.txt")),
            ("emit._git_raw", emit._git_raw, ("cat-file", "blob",
                                              f"{t}:bin.dat")),
            ("transport.run_bytes",
             lambda root, *a, **kw: transport.run_bytes(self.cfg, None, *a,
                                                        **kw),
             ("show", f"{t}:bin.dat")),
        )
        for label, door, args in shapes:
            with self.subTest(door=label, read=args):
                with counting() as seen:
                    got = in_one_invocation(lambda: [
                        door(self.repo, *args, no_replace=True)
                        for _ in range(2)])
                fresh = door(self.repo, *args, no_replace=True)
                self.assertEqual(got, [fresh, fresh])
                self.assertEqual(len(seen.git(*args)), 2, seen)
                self.assertEqual(seen.git("--batch-command"), [], seen)

    def test_a_moving_read_launches_each_time(self):
        """HEAD through three runners in one invocation: three launches."""
        with counting() as seen:
            got = in_one_invocation(lambda: [
                emit._git(self.repo, "rev-parse", "HEAD"),
                transport._git(self.repo, "rev-parse", "HEAD"),
                config._git(self.repo, "rev-parse", "HEAD")])
        self.assertEqual(got, [self.target] * 3)
        self.assertEqual(len(seen.git("rev-parse", "HEAD")), 3, seen)

    def test_outside_main_every_call_launches(self):
        """The paired control: a runner called with no invocation around
        it launches every time, as it always has."""
        with counting() as seen:
            for _ in range(2):
                emit._git(self.repo, "rev-parse", "--verify",
                          f"{self.target}^{{commit}}", no_replace=True)
                emit._git_bytes(self.repo, "show", f"{self.target}:f.txt",
                                no_replace=True)
        self.assertEqual(len(seen.git(f"{self.target}^{{commit}}")), 2)
        self.assertEqual(len(seen.git("show")), 2)
        self.assertEqual(seen.git("--batch-command"), [])

    def test_a_second_invocation_sees_an_external_commit(self):
        """The shipped tests call `cli.main` many times in one process and
        commit between the calls; each call reads afresh."""
        stable = ("rev-parse", "--verify", f"{self.base}^{{commit}}")
        got = []

        def verb():
            emit._git(self.repo, *stable, no_replace=True)
            got.append(emit._git(self.repo, "rev-parse", "HEAD"))
            got.append(emit._git(self.repo, "status", "--porcelain"))

        with counting() as seen:
            in_one_invocation(verb)
            (self.repo / "f.txt").write_text("three\n", encoding="utf-8")
            self.git("commit", "-qam", "external")
            (self.repo / "loose.txt").write_text("x\n", encoding="utf-8")
            in_one_invocation(verb)
        self.assertEqual(len(seen.git(f"{self.base}^{{commit}}")), 2)
        self.assertEqual(got[0], self.target)
        self.assertEqual(got[1], "")
        self.assertEqual(got[2], self.git("rev-parse", "HEAD"))
        self.assertNotEqual(got[2], self.target)
        self.assertEqual(got[3], "?? loose.txt")


class TestKeysNoLongerExist(Repo):
    """What the withdrawn memo keyed on — the environment, git-wide
    options, the replacement switch — now simply reaches git each time."""

    def _read(self, **kw):
        return emit._git(self.repo, "rev-parse", "--verify",
                         f"{self.target}^{{commit}}", no_replace=True, **kw)

    def test_each_environment_launches(self):
        a = {**config.caller_env(), "GIT_INDEX_FILE": str(self.tmp / "a")}
        b = {**config.caller_env(), "GIT_INDEX_FILE": str(self.tmp / "b")}
        with counting() as seen:
            got = in_one_invocation(lambda: [
                self._read(env=a), self._read(env=b), self._read(env=a)])
        self.assertEqual(got, [self.target] * 3)
        self.assertEqual(len(seen.git(f"{self.target}^{{commit}}")), 3)

    def test_a_git_wide_option_and_its_absence_both_launch(self):
        opts = (f"--git-dir={self.repo / '.git'}",)
        with counting() as seen:
            got = in_one_invocation(lambda: [
                self._read(git_options=opts), self._read(git_options=opts),
                self._read(), self._read()])
        self.assertEqual(got, [self.target] * 4)
        self.assertEqual(len(seen.git(opts[0])), 2)
        self.assertEqual(len(seen.git(f"{self.target}^{{commit}}")), 4)

    def test_the_replacement_switch_selects_the_tree(self):
        """With a replacement planted, the same read with and without the
        switch answers two different trees inside one invocation, each as
        a fresh git call does."""
        self.git("replace", self.target, self.base)
        read = lambda **kw: emit._git(self.repo, "ls-tree", "-z",  # noqa
                                      self.target, "f.txt", **kw)
        replaced, original, again = in_one_invocation(lambda: (
            read(), read(no_replace=True), read()))
        self.assertNotEqual(replaced, original)
        self.assertEqual(replaced, again)
        self.assertEqual(original,
                         self.git(NO_REPLACE, "ls-tree", "-z", self.target,
                                  "f.txt"))


# ------------------------------------------------------------ write paths

class TestEveryWritePathIsSeenByTheNextRead(Repo):
    """One row per write path that exists: a read of what it moves, asked
    before and after it within one invocation, launches every time and
    answers what the write left."""

    def _read_write_read(self, read, write):
        def steps():
            before = read()
            read()
            held = len(seen)
            write()
            after_write = len(seen)
            after = read()
            return before, after, held, len(seen) > after_write

        with counting() as seen:
            before, after, held, relaunched = in_one_invocation(steps)
        self.assertEqual(held, 2, "the second read was not launched")
        self.assertTrue(relaunched, "the read after the write was not "
                                    "launched")
        return before, after

    def head(self):
        return emit._git(self.repo, "rev-parse", "HEAD")

    def test_emit_commit(self):
        def write():
            (self.repo / "f.txt").write_text("c\n", encoding="utf-8")
            emit._git(self.repo, "commit", "-qam", "c")
        before, after = self._read_write_read(self.head, write)
        self.assertNotEqual(before, after)
        self.assertEqual(after, self.git("rev-parse", "HEAD"))

    def test_emit_push(self):
        (self.repo / "f.txt").write_text("p\n", encoding="utf-8")
        self.git("commit", "-qam", "p")
        tracking = lambda: emit._git(self.repo, "rev-parse",  # noqa: E731
                                     "refs/remotes/origin/main")
        before, after = self._read_write_read(
            tracking, lambda: emit._git(self.repo, "push", "-q", "origin",
                                        "main"))
        self.assertEqual(before, self.target)
        self.assertEqual(after, self.git("rev-parse", "HEAD"))

    def test_transport_push(self):
        (self.repo / "f.txt").write_text("q\n", encoding="utf-8")
        self.git("commit", "-qam", "q")
        tracking = lambda: transport._git(self.repo, "rev-parse",  # noqa
                                          "refs/remotes/origin/main")
        before, after = self._read_write_read(
            tracking, lambda: transport._git(self.repo, "push", "-q",
                                             "origin", "main"))
        self.assertNotEqual(before, after)

    def test_transport_fetch(self):
        other = self.tmp / "other"
        _sh("git", "clone", "-q", str(self.remote), str(other))
        _sh("git", "-C", str(other), "-c", "user.name=b", "-c",
            "user.email=b@example.invalid", "commit", "-q", "--allow-empty",
            "-m", "elsewhere")
        _sh("git", "-C", str(other), "push", "-q", "origin", "main")
        tracking = lambda: transport._git(self.repo, "rev-parse",  # noqa
                                          "refs/remotes/origin/main")
        before, after = self._read_write_read(
            tracking, lambda: transport._git(self.repo, "fetch", "-q",
                                             "origin"))
        self.assertEqual(before, self.target)
        self.assertEqual(after, _sh("git", "-C", str(other), "rev-parse",
                                    "HEAD"))

    def test_emit_add_into_a_scratch_index(self):
        scratch = {"GIT_INDEX_FILE": str(self.tmp / "scratch-index")}
        self._read_write_read(
            self.head, lambda: emit._git_raw(self.repo, "add", "-u",
                                             env=scratch))

    def test_transport_hash_object_write(self):
        self._read_write_read(
            self.head, lambda: transport._blob_of(self.cfg, "envelope\n"))

    def test_a_gh_launch(self):
        bindir = self.fake_bin("gh", "echo answered\n")
        path = f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"
        with mock.patch.dict(os.environ, {"PATH": path}):
            self._read_write_read(
                self.head, lambda: emit._gh(["gh", "api", "x"]))

    def test_the_bare_version_probe(self):
        bindir = self.fake_bin("loupe", "echo loupe 0.0.0\n")
        path = f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"
        with mock.patch.dict(os.environ, {"PATH": path}):
            self._read_write_read(self.head, emit.environment_report)

    def test_a_verb_nobody_classified(self):
        self._read_write_read(
            self.head, lambda: emit._git(self.repo, "gc", "--auto", "-q"))


class TestAHookThatMovesARef(Repo):
    """The operator's second required test: a hook the tool's own launch
    fires moves a ref, and the tool's next read of that ref sees it."""

    def test_a_commit_hook_that_moves_a_branch(self):
        self.git("branch", "side", self.base)
        self.hook("post-commit", "git update-ref refs/heads/side HEAD\n")

        def steps():
            before = emit._git(self.repo, "rev-parse", "refs/heads/side")
            (self.repo / "f.txt").write_text("h\n", encoding="utf-8")
            emit._git(self.repo, "commit", "-qam", "hooked")
            return before, emit._git(self.repo, "rev-parse",
                                     "refs/heads/side")

        before, after = in_one_invocation(steps)
        self.assertEqual(before, self.base)
        self.assertEqual(after, self.git("rev-parse", "HEAD"))

    def test_a_pre_push_hook_that_moves_a_tag(self):
        self.git("tag", "moved", self.base)
        self.hook("pre-push", "git tag -f moved HEAD >/dev/null 2>&1\n")
        (self.repo / "f.txt").write_text("t\n", encoding="utf-8")
        self.git("commit", "-qam", "to push")

        def steps():
            before = transport._git(self.repo, "rev-parse", "moved^{commit}")
            transport._git(self.repo, "push", "-q", "origin", "main")
            return before, transport._git(self.repo, "rev-parse",
                                          "moved^{commit}")

        before, after = in_one_invocation(steps)
        self.assertEqual(before, self.base)
        self.assertEqual(after, self.git("rev-parse", "HEAD"))


class TestAGateThatDirtiesTheTree(Repo):
    """The operator's first required test: a gate writes inside the
    repository, and the tree check after the gates sees it — on the
    sequential path and on the pool path."""

    def _gate(self, gate_id, code):
        return {"id": gate_id, "command": [sys.executable, "-c", code],
                "blocking": True}

    def test_the_tree_check_after_a_gate_sees_what_it_wrote(self):
        dirty = self._gate("dirty", "open('f.txt','w').write('gate\\n')")
        clean = self._gate("clean", "pass")
        for label, workers, gates in (("sequential", "1", [dirty]),
                                      ("pool", "4", [clean, dirty])):
            with self.subTest(path=label):
                self.git("checkout", "-q", "--", "f.txt")
                cfg = dataclasses.replace(self.cfg, gates=gates)

                def steps():
                    before = emit._git(self.repo, "status", "--porcelain")
                    records = emit.run_gates(cfg, self.target,
                                             local_only=True)
                    return before, records, emit._git(
                        self.repo, "status", "--porcelain")

                with mock.patch.dict(
                        os.environ,
                        _outside_a_gate_run(LOUPE_GATE_WORKERS=workers),
                        clear=True):
                    before, records, after = in_one_invocation(steps)
                self.assertEqual(before, "")
                self.assertEqual([r.get("exit_code") for r in records],
                                 [0] * len(gates), records)
                self.assertEqual(after, "M f.txt")


class TestAShallowCloneMovesItsCut(Repo):
    """The operator's follow-up rule: a fetch moves a shallow clone's cut,
    and a history walk read before it must not be answered after it. Both
    directions, and a fetch the TOOL did not make (a gate's): `.git/shallow`
    appears, grows and disappears under reads that name only object ids."""

    def _clone(self, *depth) -> Path:
        where = self.tmp / f"clone{len(list(self.tmp.iterdir()))}"
        _sh("git", "clone", "-q", *depth, f"file://{self.repo}", str(where))
        return where

    def _count(self, where) -> str:
        return emit._git(where, "rev-list", "--count", self.target,
                         no_replace=True)

    def test_a_deepening_fetch_is_seen_within_one_invocation(self):
        shallow = self._clone("--depth", "1")
        self.assertTrue((shallow / ".git" / "shallow").is_file())

        def steps():
            before = self._count(shallow)
            with self.assertRaises(RuntimeError):
                emit._git(shallow, "rev-parse", "--verify",
                          f"{self.target}~1", no_replace=True)
            transport._git(shallow, "fetch", "-q", "--unshallow")
            return before, self._count(shallow), emit._git(
                shallow, "rev-parse", "--verify", f"{self.target}~1",
                no_replace=True)

        before, after, parent = in_one_invocation(steps)
        self.assertEqual((before, after), ("1", "2"))
        self.assertEqual(parent, self.base)

    def test_a_gate_that_deepens_is_seen(self):
        shallow = self._clone("--depth", "1")
        gate = {"id": "deepen", "blocking": True,
                "command": ["git", "fetch", "-q", "--deepen=1"]}
        cfg = dataclasses.replace(self.cfg, repo_root=shallow, gates=[gate])

        def steps():
            before = self._count(shallow)
            records = emit.run_gates(cfg, self.target, local_only=True)
            return before, records, self._count(shallow)

        with mock.patch.dict(os.environ,
                             _outside_a_gate_run(LOUPE_GATE_WORKERS="1"),
                             clear=True):
            before, records, after = in_one_invocation(steps)
        self.assertEqual([r.get("exit_code") for r in records], [0], records)
        self.assertEqual((before, after), ("1", "2"))

    def test_a_fetch_that_makes_a_complete_clone_shallow_is_seen(self):
        full = self._clone()
        self.assertFalse((full / ".git" / "shallow").exists())

        def steps():
            before = self._count(full)
            emit._git(full, "merge-base", "--is-ancestor", self.base,
                      self.target, no_replace=True)
            transport._git(full, "fetch", "-q", "--depth=1", "origin",
                           "main")
            after = self._count(full)
            with self.assertRaises(RuntimeError):
                emit._git(full, "merge-base", "--is-ancestor", self.base,
                          self.target, no_replace=True)
            return before, after

        before, after = in_one_invocation(steps)
        self.assertTrue((full / ".git" / "shallow").is_file())
        self.assertEqual((before, after), ("2", "1"))


class TestALinkedWorktreeMovesSharedState(Repo):
    """Round 1, F1. A linked worktree shares `refs/*`, the repository
    configuration and `.git/shallow` with this checkout; a fetch, a ref
    update or a `git config` there is a git process of its own, which no
    launch of this invocation sees. Each read below is asked, the linked
    worktree writes, and the read is asked again before the invocation
    launches anything else: it must answer the new state."""

    def _worktree(self, repo: Path, *how) -> Path:
        where = self.tmp / f"linked{len(list(self.tmp.iterdir()))}"
        _sh("git", "-C", str(repo), "worktree", "add", "-q", *how,
            str(where))
        return where

    def _push_elsewhere(self) -> str:
        other = self.tmp / "other"
        _sh("git", "clone", "-q", str(self.remote), str(other))
        _sh("git", "-C", str(other), "-c", "user.name=b", "-c",
            "user.email=b@example.invalid", "commit", "-q", "--allow-empty",
            "-m", "elsewhere")
        _sh("git", "-C", str(other), "push", "-q", "origin", "main")
        return _sh("git", "-C", str(other), "rev-parse", "HEAD")

    def _tracking(self) -> str:
        return transport._git(self.repo, "for-each-ref",
                              "--format=%(objectname)",
                              "refs/remotes/origin/main")

    def _urls(self):
        return (("remote get-url", lambda: transport._git(
                    self.repo, "remote", "get-url", "origin")),
                ("config --get", lambda: emit._git(
                    self.repo, "config", "--get", "remote.origin.url")))

    def test_a_fetch_there_moves_the_remote_tracking_ref(self):
        linked = self._worktree(self.repo, "-b", "linked")
        pushed = self._push_elsewhere()

        def steps():
            before = self._tracking()
            _sh("git", "-C", str(linked), "fetch", "-q", "origin")
            return before, self._tracking()

        before, after = in_one_invocation(steps)
        self.assertEqual(before, self.target)
        self.assertEqual(after, pushed)
        self.assertEqual(after, self.git("for-each-ref",
                                         "--format=%(objectname)",
                                         "refs/remotes/origin/main"))

    def test_the_remote_tracking_ref_unchanged_control(self):
        self._worktree(self.repo, "-b", "linked")
        got = in_one_invocation(lambda: (self._tracking(), self._tracking()))
        self.assertEqual(got, (self.target, self.target))

    def test_a_config_edit_there_moves_the_remote_url(self):
        linked = self._worktree(self.repo, "-b", "linked")
        moved = str(self.tmp / "moved.git")
        for label, read in self._urls():
            with self.subTest(read=label):
                self.git("config", "remote.origin.url", str(self.remote))

                def steps():
                    before = read()
                    _sh("git", "-C", str(linked), "config",
                        "remote.origin.url", moved)
                    return before, read()

                before, after = in_one_invocation(steps)
                self.assertEqual(before, str(self.remote))
                self.assertEqual(after, moved)
                self.assertEqual(after, self.git("remote", "get-url",
                                                 "origin"))

    def test_the_remote_url_unchanged_control(self):
        self._worktree(self.repo, "-b", "linked")
        for label, read in self._urls():
            with self.subTest(read=label):
                self.assertEqual(in_one_invocation(lambda: (read(), read())),
                                 (str(self.remote), str(self.remote)))

    # -- the shallow cut ------------------------------------------------------
    def _shallow_with_a_worktree(self) -> tuple[Path, Path]:
        """A depth-1 clone holding both the base (branch `old`) and the
        target as shallow tips, and a linked worktree of it."""
        self.git("branch", "old", self.base)
        shallow = self.tmp / "shallow"
        _sh("git", "clone", "-q", "--depth", "1", "--no-single-branch",
            f"file://{self.repo}", str(shallow))
        self.assertTrue((shallow / ".git" / "shallow").is_file())
        return shallow, self._worktree(shallow, "--detach")

    def _walks(self, where: Path):
        return (("rev-list --count", lambda: emit._git(
                    where, "rev-list", "--count", self.target,
                    no_replace=True)),
                ("merge-base --independent", lambda: emit._git(
                    where, "merge-base", "--independent", self.base,
                    self.target, no_replace=True)))

    def test_a_deepening_fetch_there_is_seen(self):
        shallow, linked = self._shallow_with_a_worktree()
        walks = self._walks(shallow)

        def steps():
            before = [read() for _, read in walks]
            _sh("git", "-C", str(linked), "fetch", "-q", "--unshallow")
            return before, [read() for _, read in walks]

        before, after = in_one_invocation(steps)
        self.assertFalse((shallow / ".git" / "shallow").exists())
        self.assertEqual(before[0], "1")
        self.assertEqual(sorted(before[1].split()),
                         sorted([self.base, self.target]))
        self.assertEqual(after, ["2", self.target])
        self.assertEqual(after, [
            _sh("git", "-C", str(shallow), NO_REPLACE, "rev-list", "--count",
                self.target),
            _sh("git", "-C", str(shallow), NO_REPLACE, "merge-base",
                "--independent", self.base, self.target)])

    def test_a_fetch_there_that_makes_a_complete_clone_shallow_is_seen(self):
        full = self.tmp / "full"
        _sh("git", "clone", "-q", f"file://{self.repo}", str(full))
        linked = self._worktree(full, "--detach")

        def steps():
            count = emit._git(full, "rev-list", "--count", self.target,
                              no_replace=True)
            emit._git(full, "merge-base", "--is-ancestor", self.base,
                      self.target, no_replace=True)
            _sh("git", "-C", str(linked), "fetch", "-q", "--depth=1",
                "origin", "main")
            recount = emit._git(full, "rev-list", "--count", self.target,
                                no_replace=True)
            with self.assertRaises(RuntimeError):
                emit._git(full, "merge-base", "--is-ancestor", self.base,
                          self.target, no_replace=True)
            return count, recount

        count, recount = in_one_invocation(steps)
        self.assertTrue((full / ".git" / "shallow").is_file())
        self.assertEqual((count, recount), ("2", "1"))

    def test_the_history_walks_unchanged_control(self):
        shallow, _ = self._shallow_with_a_worktree()
        for label, read in self._walks(shallow):
            with self.subTest(read=label):
                first, second = in_one_invocation(lambda: (read(), read()))
                self.assertEqual(first, second)
                self.assertEqual(sorted(first.split()), sorted(
                    ["1"] if label.startswith("rev-list")
                    else [self.base, self.target]))


class TestOutputTheConfigurationFormatsIsNeverKept(Repo):
    """Round 1, F2. `ls-tree` without `-z` quotes an unusual name by
    `core.quotePath`, so its bytes change when the configuration does
    although every object id is the same. Here the configuration is changed
    by the tool's own runner (a `git config` launch) between two reads in
    one invocation: the second read must equal a fresh git call. `-z`
    prints names verbatim; an ASCII-only tree prints the same either way.
    Both are the controls."""

    def setUp(self):
        super().setUp()
        # Explicit, so a machine whose global config turns quoting off
        # still starts from the quoting side.
        self.git("config", "core.quotePath", "true")
        self.dir_tree = self.git("rev-parse", f"{self.base}:dir")
        self.root_tree = self.git("rev-parse", f"{self.base}^{{tree}}")

    def _around_a_config_change(self, *read_args):
        def steps():
            first = emit._git(self.repo, *read_args, no_replace=True)
            emit._git(self.repo, "config", "core.quotePath", "false")
            return first, emit._git(self.repo, *read_args, no_replace=True)

        with counting() as seen:
            first, second = in_one_invocation(steps)
        fresh = _sh("git", "-C", str(self.repo), NO_REPLACE, *read_args)
        return first, second, fresh, len(seen.git(*read_args))

    def test_ls_tree_without_z_follows_core_quotepath(self):
        first, second, fresh, launches = self._around_a_config_change(
            "ls-tree", "--full-tree", self.dir_tree)
        self.assertIn('"space \\303\\251 name.txt"', first)
        self.assertEqual(second, fresh)
        self.assertIn("\tspace é name.txt", second)
        self.assertEqual(launches, 2)

    def test_ls_tree_z_control_is_unchanged(self):
        first, second, fresh, _ = self._around_a_config_change(
            "ls-tree", "-z", "--full-tree", self.dir_tree)
        self.assertIn("\tspace é name.txt", first)
        self.assertEqual((first, second), (fresh, fresh))

    def test_ls_tree_ascii_names_control(self):
        first, second, fresh, _ = self._around_a_config_change(
            "ls-tree", "--full-tree", self.root_tree)
        self.assertEqual((first, second), (fresh, fresh))
        self.assertIn("\tf.txt", first)

    def test_cat_file_p_of_a_tree_follows_core_quotepath(self):
        """`cat-file -p` of a tree prints the same listing `ls-tree` does,
        quoting included, and a name alone does not say it is a tree."""
        first, second, fresh, launches = self._around_a_config_change(
            "cat-file", "-p", self.dir_tree)
        self.assertIn('"space \\303\\251 name.txt"', first)
        self.assertEqual(second, fresh)
        self.assertIn("\tspace é name.txt", second)
        self.assertEqual(launches, 2)


# ----------------------------------------------------- object availability

class TestAlternatesChangeObjectAvailability(Donor):
    """Round 2, F1. A full object id fixes an object's content, not this
    repository's access to it. The donor's objects are reachable only
    through this repository's `objects/info/alternates`; the file is added
    or removed by a plain file write, an independent writer no git launch
    of the tool sees, between two reads of one `cli.main` call. The second
    read must answer what a fresh git call answers: git's own refusal, as
    the runner's `RuntimeError`, once the alternate is gone, and the
    object once it is lent.

    Every shape the withdrawn memo kept is a row, through every runner
    that reads objects, including the shapes the batch reader served
    (`cat-file -t`, `cat-file blob`, `show <id>:<path>` of a blob). A bare
    `rev-parse --verify <full id>` is not a row: git answers the id without
    looking the object up, with or without the alternate (measured on git
    2.54), so its peeled forms `<id>^{blob}` and `<id>^{commit}` are."""

    def _rows(self):
        r, b, c = self.repo, self.blob1, self.lent
        text = lambda *a: emit._git(r, *a, no_replace=True)  # noqa: E731
        ttext = lambda *a: transport._git(r, *a, no_replace=True)  # noqa
        raw = lambda *a: emit._git_raw(r, *a, no_replace=True)  # noqa
        byts = lambda *a: emit._git_bytes(r, *a, no_replace=True)  # noqa
        runb = lambda *a: transport.run_bytes(  # noqa: E731
            self.cfg, None, *a, no_replace=True)
        return (
            ("emit._git", text, ("cat-file", "-t", b)),
            ("transport._git", ttext, ("cat-file", "-t", b)),
            ("transport._git", ttext, ("cat-file", "-e", b)),
            ("emit._git", text, ("cat-file", "-s", b)),
            ("emit._git_bytes", byts, ("cat-file", "-t", b)),
            ("emit._git_raw", raw, ("cat-file", "blob", b)),
            ("transport.run_bytes", runb, ("cat-file", "blob", b)),
            ("emit._git_bytes", byts, ("show", f"{c}:d1.txt")),
            ("transport.run_bytes", runb, ("show", f"{c}:d1.txt")),
            ("emit._git", text, ("ls-tree", "-z", c)),
            ("transport._git", ttext, ("ls-tree", "-z", "-r", "--name-only",
                                       c)),
            ("emit._git", text, ("rev-parse", "--verify", f"{b}^{{blob}}")),
            ("transport._git", ttext, ("rev-parse", "--verify",
                                       f"{c}^{{commit}}")),
        )

    def _refusal(self, door, args) -> RuntimeError:
        with self.assertRaises(RuntimeError) as caught:
            door(*args)
        return caught.exception

    def _same_refusal(self, got: RuntimeError, want: RuntimeError, args):
        self.assertIs(type(got), RuntimeError)
        self.assertEqual(str(got), str(want))
        message = _git_refusal(self.repo, *args)
        if message:  # `cat-file -e` refuses with its exit status alone
            self.assertIn(message, str(got))

    def test_a_withdrawn_alternate_turns_a_success_into_git_s_refusal(self):
        for label, door, args in self._rows():
            with self.subTest(door=label, read=args):
                self.lend()

                def steps():
                    first = door(*args)
                    self.withdraw()
                    return first, self._refusal(door, args)

                first, second = in_one_invocation(steps)
                self.lend()
                self.assertEqual(first, door(*args))
                self.withdraw()
                self._same_refusal(second, self._refusal(door, args), args)

    def test_a_lent_alternate_turns_a_refusal_into_the_object(self):
        for label, door, args in self._rows():
            with self.subTest(door=label, read=args):
                self.withdraw()

                def steps():
                    first = self._refusal(door, args)
                    self.lend()
                    return first, door(*args)

                first, second = in_one_invocation(steps)
                self.assertEqual(second, door(*args))
                self.withdraw()
                self._same_refusal(first, self._refusal(door, args), args)

    def test_another_object_after_the_withdrawal_is_refused(self):
        """The batch reader's own stale view: one donor object read opens
        whatever long-lived reader there is, the alternate is withdrawn,
        and a DIFFERENT donor object — never read before, so no answer for
        it can be held — must be refused as fresh git refuses it."""
        c = self.lent
        rows = (("emit._git_bytes", lambda *a: emit._git_bytes(
                    self.repo, *a, no_replace=True)),
                ("transport.run_bytes", lambda *a: transport.run_bytes(
                    self.cfg, None, *a, no_replace=True)))
        for label, door in rows:
            with self.subTest(door=label):
                self.lend()

                def steps():
                    first = door("show", f"{c}:d1.txt")
                    self.withdraw()
                    return first, self._refusal(
                        door, ("cat-file", "blob", self.blob2))

                first, second = in_one_invocation(steps)
                self.assertEqual(first, b"donor one\n")
                self._same_refusal(
                    second, self._refusal(door, ("cat-file", "blob",
                                                 self.blob2)),
                    ("cat-file", "blob", self.blob2))

    def test_unchanged_availability_control(self):
        """The same read twice, the alternate untouched: the same answer,
        equal to a fresh call — lent, and withdrawn."""
        for label, door, args in self._rows():
            with self.subTest(door=label, read=args, lent=True):
                self.lend()
                first, second = in_one_invocation(
                    lambda: (door(*args), door(*args)))
                self.assertEqual((first, second), (door(*args),) * 2)
            with self.subTest(door=label, read=args, lent=False):
                self.withdraw()
                first, second = in_one_invocation(lambda: (
                    self._refusal(door, args), self._refusal(door, args)))
                fresh = self._refusal(door, args)
                self._same_refusal(first, fresh, args)
                self._same_refusal(second, fresh, args)

    def test_a_bare_full_id_answers_without_the_object(self):
        """The row that is not one: `rev-parse --verify <full id>` answers
        the id whether or not the object is reachable, and so does the
        runner, withdrawn or lent."""
        read = lambda: emit._git(self.repo, "rev-parse",  # noqa: E731
                                 "--verify", self.blob1, no_replace=True)

        def steps():
            self.lend()
            first = read()
            self.withdraw()
            return first, read()

        self.assertEqual(in_one_invocation(steps), (self.blob1, self.blob1))


# ------------------------------------------------------------ object reads

class TestObjectReadsAnswerAsGitDoes(Repo):
    """The reads the batch reader used to serve, asked inside one
    invocation: the bytes and the refusals of the read git itself runs,
    which is launched (`show`, `cat-file`), never a long-lived reader."""

    def _both(self, call):
        """(outside any invocation, inside one, launches inside)."""
        want = call()
        with counting() as seen:
            got = in_one_invocation(call)
        return want, got, seen

    def test_byte_identical_blobs(self):
        for path in ("f.txt", "bin.dat", self.odd):
            for label, call in (
                    ("emit._git_bytes", lambda p=path: emit._git_bytes(
                        self.repo, "show", f"{self.base}:{p}",
                        no_replace=True)),
                    ("transport.run_bytes", lambda p=path: transport.run_bytes(
                        self.cfg, None, "show", f"{self.base}:{p}",
                        no_replace=True))):
                with self.subTest(path=path, door=label):
                    want, got, seen = self._both(call)
                    self.assertEqual(got, want)
                    self.assertEqual(len(seen.git("show")), 1, seen)
                    self.assertEqual(seen.git("--batch-command"), [])
        raw = emit._git_bytes(self.repo, "show", f"{self.base}:bin.dat",
                              no_replace=True)
        self.assertEqual(raw, BINARY)

    def test_a_blob_by_id_through_the_raw_door(self):
        blob = self.git("rev-parse", f"{self.base}:bin.dat")
        want, got, seen = self._both(lambda: emit._git_raw(
            self.repo, "cat-file", "blob", blob, no_replace=True))
        self.assertEqual((got, want), (BINARY, BINARY))
        self.assertEqual(len(seen.git("cat-file", "blob", blob)), 1)

    def test_type_and_presence(self):
        rows = (
            (lambda: transport._git(self.repo, "cat-file", "-t",
                                    f"{self.base}:dir", no_replace=True),
             "tree", "-t"),
            (lambda: emit._git(self.repo, "cat-file", "-t",
                               f"{self.base}:f.txt", no_replace=True),
             "blob", "-t"),
            (lambda: transport._git(self.repo, "cat-file", "-e",
                                    f"{self.target}^{{commit}}",
                                    no_replace=True), "", "-e"),
            (lambda: emit._git_bytes(self.repo, "cat-file", "-t",
                                     f"{self.base}:f.txt", no_replace=True),
             b"blob\n", "-t"),
        )
        for call, expected, option in rows:
            with self.subTest(expected=expected):
                want, got, seen = self._both(call)
                self.assertEqual((got, want), (expected, expected))
                self.assertEqual(len(seen.git("cat-file", option)), 1)
                self.assertEqual(seen.git("--batch-command"), [])

    def test_a_tree_read_through_show_is_the_real_show(self):
        want, got, seen = self._both(lambda: emit._git_bytes(
            self.repo, "show", f"{self.base}:dir", no_replace=True))
        self.assertEqual(got, want)
        self.assertTrue(got.startswith(b"tree "), got[:40])
        self.assertEqual(len(seen.git("show")), 1)

    def test_a_missing_object_is_the_same_refusal(self):
        for label, call in (
                ("show", lambda: emit._git_bytes(
                    self.repo, "show", f"{self.base}:nope.txt",
                    no_replace=True)),
                ("cat-file -t", lambda: transport._git(
                    self.repo, "cat-file", "-t", f"{self.base}:nope.txt",
                    no_replace=True)),
                ("cat-file -e", lambda: transport._git(
                    self.repo, "cat-file", "-e", f"{'0' * 40}^{{commit}}",
                    no_replace=True))):
            with self.subTest(read=label):
                with self.assertRaises(RuntimeError) as want:
                    call()

                def twice():
                    errors = []
                    for _ in range(2):
                        with self.assertRaises(RuntimeError) as got:
                            call()
                        errors.append(got.exception)
                    return errors

                with counting() as seen:
                    errors = in_one_invocation(twice)
                for got in errors:
                    self.assertEqual(str(got), str(want.exception))
                    self.assertIs(type(got), type(want.exception))
                self.assertEqual(len(seen.git(NO_REPLACE)), 2, seen)

    def test_replacement_objects_stay_off(self):
        """A replacement planted for the blob: the read answers the
        ORIGINAL bytes, as `show --no-replace-objects` does, and its launch
        carries the switch."""
        blob = self.git("rev-parse", f"{self.base}:f.txt")
        other = _sh("git", "-C", str(self.repo), "hash-object", "-w",
                    "--stdin", input_bytes=b"planted\n")
        self.git("replace", blob, other)
        self.assertEqual(self.git("show", f"{self.base}:f.txt"), "planted")
        want, got, seen = self._both(lambda: emit._git_bytes(
            self.repo, "show", f"{self.base}:f.txt", no_replace=True))
        self.assertEqual((got, want), (b"one\n", b"one\n"))
        self.assertEqual(len(seen.git(NO_REPLACE, "show")), 1, seen)


if __name__ == "__main__":
    unittest.main()

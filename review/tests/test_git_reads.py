"""Every SHA-bound git read runs against the ORIGINAL object graph.

Lineage 12 round 3 F1. Three boundaries of this tool were found reading the
local object graph where they meant to read the target's — the authority
read (round 1), the reference reads (round 2), the diff, presence and
ancestry reads (round 3). Each was fixed where it was shown, and the domain
was a list someone maintained by hand, so the next one would have been
found the same way.

This module holds the BEHAVIOURAL half and travels with the tool: a
planted replacement must not reach a semantic read, and both printed diff
surfaces must carry the option. It depends on nothing but git.

The COMPLETENESS half — every classified object-graph read, derived from
`bin/loupe-git-read-audit`'s generated inventory — stays in the workbench as
`test_git_read_boundary.py`, because the authority it is judged against is a
workbench artefact. That is the same split the tool identity already makes:
its own properties travel in `test_tool_identity.py`, and the completeness
of the declared set is judged in `test_identity_boundary.py`, where the
inventory lives. The cost is stated rather than hidden: an extracted
candidate carries the hardened code and the behavioural proof, and not the
instrument that proves the set is complete.
"""
from __future__ import annotations

import dataclasses
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from review import config, emit, paths, transport
from review.tests.util import REPO_ROOT

SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")
NO_REPLACE = "--no-replace-objects"


def subcommand_of(argv: list[str]) -> tuple[str | None, bool]:
    """(subcommand, carries_the_flag) for a git argv, skipping git-wide
    options. Anything that is not a git invocation returns (None, False)."""
    if not argv or Path(argv[0]).name != "git":
        return None, False
    flag = NO_REPLACE in argv
    i = 1
    while i < len(argv):
        word = argv[i]
        if word == "-C":
            i += 2
            continue
        if word.startswith("-"):
            i += 1
            continue
        return word, flag
    return None, flag


class GitReadFixture(unittest.TestCase):
    """A scratch repository with a base, a target, and a bare remote."""

    def setUp(self):
        import shutil
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="git-reads-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.repo = self.tmp / "repo"
        self.remote = self.tmp / "remote.git"
        self._sh("git", "init", "-q", "-b", "main", str(self.repo))
        for k, v in (("user.name", "a"), ("user.email", "a@example.invalid"),
                     ("commit.gpgsign", "false")):
            self._sh("git", "-C", str(self.repo), "config", k, v)
        self._sh("git", "init", "-q", "--bare", str(self.remote))
        toml = (REPO_ROOT / "review.toml").read_text(encoding="utf-8")
        (self.repo / "review.toml").write_text(
            toml[:toml.index("[[gates]]")] + toml[toml.index("[roles]"):],
            encoding="utf-8")
        (self.repo / "f.txt").write_text("one\n", encoding="utf-8")
        self._sh("git", "-C", str(self.repo), "add", ".")
        self._sh("git", "-C", str(self.repo), "commit", "-q", "-m", "base")
        self.base = self._git("rev-parse", "HEAD")
        (self.repo / "f.txt").write_text("two\n", encoding="utf-8")
        self._sh("git", "-C", str(self.repo), "commit", "-qam", "target")
        self.target = self._git("rev-parse", "HEAD")
        self._sh("git", "-C", str(self.repo), "remote", "add", "origin",
                 str(self.remote))
        self._sh("git", "-C", str(self.repo), "push", "-q", "-u", "origin",
                 "main")
        self.cfg = dataclasses.replace(config.load(self.repo),
                                       repo_root=self.repo,
                                       ledger_dir=self.tmp / "state")

    def _sh(self, *argv):
        subprocess.run(list(argv), check=True, capture_output=True,
                       timeout=60)

    def _git(self, *args, input_text=None):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args], check=True,
            capture_output=True, text=True, timeout=60,
            input=input_text).stdout.strip()

    def plant_replacement(self, base_ref=None):
        """A replacement COMMIT for the target whose tree adds `extra.txt`
        — the shape round 3 F1's falsification names. Returns nothing; the
        target SHA is unchanged and now resolves, for any unguarded read,
        to a different tree."""
        env = {}
        if base_ref:
            env["GIT_REPLACE_REF_BASE"] = base_ref
        import os
        with mock.patch.dict(os.environ, env):
            (self.repo / "extra.txt").write_text("planted\n",
                                                 encoding="utf-8")
            self._sh("git", "-C", str(self.repo), "add", "extra.txt")
            self._sh("git", "-C", str(self.repo), "commit", "-qm", "planted")
            other = self._git("rev-parse", "HEAD")
            self._sh("git", "-C", str(self.repo), "reset", "-q", "--hard",
                     self.target)
            self._sh("git", "-C", str(self.repo), "replace", "-f",
                     self.target, other)
            # Live, or the assertions below prove nothing.
            self.assertIn(
                "extra.txt",
                self._git("ls-tree", "--name-only", self.target),
                "the replacement is not live")

    def remove_replacement(self, base_ref=None):
        """Deleting needs the SAME namespace the plant used: `replace -d`
        looks under `GIT_REPLACE_REF_BASE`, so a delete without it silently
        leaves an alternate-base replacement in place."""
        import os
        env = {"GIT_REPLACE_REF_BASE": base_ref} if base_ref else {}
        with mock.patch.dict(os.environ, env):
            self._sh("git", "-C", str(self.repo), "replace", "-d",
                     self.target)


class TestTheReplacementCannotReachASemanticRead(GitReadFixture):

    def test_presence_ancestry_and_shape_stay_on_the_original_tree(self):
        """The row F1 names at minimum: a replacement commit whose tree
        adds `extra.txt` must leave target presence, ancestry and the
        emitted shape bound to the ORIGINAL target tree.

        MUTATION: re-enable replacement at `diff_shape` or `_is_ancestor`
        and the corresponding assertion fails.
        """
        clean = emit.diff_shape(self.repo, self.base, self.target)
        self.assertNotIn("extra.txt", clean["file_list"])
        for base_ref in (None, "refs/altreplace/"):
            with self.subTest(base=base_ref or "refs/replace/"):
                self.plant_replacement(base_ref)
                shape = emit.diff_shape(self.repo, self.base, self.target)
                self.assertEqual(
                    shape, clean,
                    "the emitted shape followed the replacement tree")
                self.assertNotIn("extra.txt", shape["file_list"])
                self.assertTrue(
                    emit._is_ancestor(self.repo, self.base, self.target),
                    "ancestry followed the replacement")
                self.remove_replacement(base_ref)

    def test_both_printed_diff_surfaces_carry_the_option(self):
        """A printed command is a read too — by whoever runs it.

        MUTATION: remove the option from `paths.diff_command` and both
        assertions fail, because both surfaces render through it.
        """
        printed = paths.diff_command(self.repo, self.base, self.target)
        self.assertIn(NO_REPLACE, printed)
        sub, flag = subcommand_of(__import__("shlex").split(printed))
        self.assertEqual(sub, "diff")
        self.assertTrue(flag, "the flag is not in git-wide position")

    def test_the_option_precedes_the_subcommand(self):
        """`--no-replace-objects` is git-wide: after the subcommand git
        rejects it outright, so a printed command that placed it there
        would fail for the human running it."""
        out = subprocess.run(
            ["git", "-C", str(self.repo), "cat-file", NO_REPLACE, "-e",
             self.target], capture_output=True, text=True, timeout=60)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("unknown option", out.stderr)

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
red the same way, since the lock sits three levels down."""

import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from review.tests import _transport_fixtures as fx

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

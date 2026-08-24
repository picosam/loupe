"""Required references bind to the target tree (lineage 5 round 2, F3).

The defect this closes was committed by this tool's own review: a claim
declared a REQUIRED reference at a gitignored path, the emitter digested
the worktree bytes, and the reviewer's `take` — which reads references from
the target commit — could only label it unavailable, poisoning the round.
Required means the reviewer must read it; a digest over bytes no commit
carries binds nothing.

Domain per required reference: tracked file (valid — the handoff's own
commit carries it to the target), tracked directory with tracked content
(valid), ignored (refused), untracked (refused, by name), missing (refused,
tracked or not), absolute (refused), escaping the repository root
(refused). Advisory references keep the §5.1 three-state rendering — the
declared-unavailable state is the author's honesty mechanism and stays.
"""
from __future__ import annotations

import dataclasses
import subprocess
import tempfile
import unittest
from pathlib import Path

from review import emit
from review.tests.synth import CFG


def _sh(*args):
    subprocess.run(args, check=True, capture_output=True, text=True,
                   timeout=60)


class TestRequiredReferenceBinding(unittest.TestCase):

    def setUp(self):
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="refbind-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.repo = self.tmp / "repo"
        _sh("git", "init", "-q", "-b", "main", str(self.repo))
        for k, v in (("user.name", "t"), ("user.email", "t@example.invalid"),
                     ("commit.gpgsign", "false")):
            _sh("git", "-C", str(self.repo), "config", k, v)
        (self.repo / ".gitignore").write_text("/private/\n", encoding="utf-8")
        (self.repo / "tracked.md").write_text("tracked\n", encoding="utf-8")
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "kept.md").write_text("kept\n", encoding="utf-8")
        _sh("git", "-C", str(self.repo), "add", ".")
        _sh("git", "-C", str(self.repo), "commit", "-q", "-m", "init")
        (self.repo / "private").mkdir()
        (self.repo / "private" / "ignored.md").write_text("secret-ish\n",
                                                          encoding="utf-8")
        (self.repo / "loose.md").write_text("untracked\n", encoding="utf-8")
        self.cfg = dataclasses.replace(CFG, repo_root=self.repo)

    def _check(self, refs):
        return emit.check_required_references(self.cfg, refs)

    def test_tracked_file_and_tracked_directory_are_the_valid_controls(self):
        self.assertIsNone(self._check(
            [{"path": "tracked.md", "required": True},
             {"path": "docs", "required": True}]))

    def test_an_ignored_required_reference_refuses_naming_the_state(self):
        with self.assertRaises(emit.ReferenceUnbound) as ctx:
            self._check([{"path": "private/ignored.md", "required": True}])
        self.assertIn("ignored", str(ctx.exception))
        self.assertTrue(ctx.exception.remedy)

    def test_an_untracked_required_reference_refuses_by_name(self):
        with self.assertRaises(emit.ReferenceUnbound) as ctx:
            self._check([{"path": "loose.md", "required": True}])
        self.assertIn("untracked", str(ctx.exception))

    def test_a_missing_required_reference_refuses(self):
        with self.assertRaises(emit.ReferenceUnbound) as ctx:
            self._check([{"path": "no-such-file.md", "required": True}])
        self.assertIn("missing entirely", str(ctx.exception))

    def test_a_tracked_but_deleted_required_reference_refuses(self):
        # The auto-commit makes the tree carry what the disk carries, so a
        # deleted tracked file would leave the target without it.
        (self.repo / "tracked.md").unlink()
        with self.assertRaises(emit.ReferenceUnbound) as ctx:
            self._check([{"path": "tracked.md", "required": True}])
        self.assertIn("deletion", str(ctx.exception))

    def test_absolute_and_escaping_paths_refuse(self):
        with self.assertRaises(emit.ReferenceUnbound) as ctx:
            self._check([{"path": str(self.repo / "tracked.md"),
                          "required": True}])
        self.assertIn("absolute", str(ctx.exception))
        with self.assertRaises(emit.ReferenceUnbound) as ctx:
            self._check([{"path": "../outside.md", "required": True}])
        self.assertIn("escap", str(ctx.exception))

    def test_advisory_references_keep_their_three_state_rendering(self):
        # The boundary governs REQUIRED only: declared-unavailable stays
        # the honesty mechanism for advisory material that cannot travel.
        self.assertIsNone(self._check(
            [{"path": "private/ignored.md", "required": False},
             {"path": "loose.md", "required": False},
             {"path": "no-such-file.md", "required": False}]))

    def test_no_references_at_all_is_not_this_boundarys_business(self):
        self.assertIsNone(self._check(None))
        self.assertIsNone(self._check([]))

    def test_the_emitting_verbs_refuse_before_any_side_effect(self):
        """The tripwire: an ignored required reference blocks before the
        ledger is constructed, on both verbs (round-2 F3: 'before push,
        gates, emission, or recording')."""
        import argparse
        import contextlib
        import io
        import json
        import unittest.mock as mock
        from review import cli

        claim = self.tmp / "claim.json"
        claim.write_text(
            '{"objective": "x", "references": '
            '[{"path": "private/ignored.md", "required": true}]}',
            encoding="utf-8")

        def tripped(*a, **k):
            raise AssertionError("a side effect was reached past an "
                                 "unbound required reference")

        for verb, command in ((cli.cmd_handoff, "handoff"),
                              (cli.cmd_emit_request, "emit-request")):
            with self.subTest(verb=command):
                args = argparse.Namespace(
                    claim_file=str(claim), base=None, head=None,
                    local_only=False, out=None, ledger_dir=None,
                    command=command, author=None, reviewer=None)
                buf = io.StringIO()
                with mock.patch.object(cli, "_ledger", tripped), \
                        mock.patch.object(cli, "_emit", tripped), \
                        mock.patch.object(cli.emit, "ensure_pushed",
                                          tripped), \
                        mock.patch.object(cli.emit, "run_gates", tripped), \
                        mock.patch.object(cli.transport, "cached_handoff",
                                          tripped), \
                        contextlib.redirect_stdout(buf):
                    code = verb(args, self.cfg)
                self.assertNotEqual(code, 0)
                payload = json.loads(buf.getvalue())
                self.assertEqual(payload["next_kind"], "blocked")
                self.assertIn("ignored", payload["error"])


if __name__ == "__main__":
    unittest.main()

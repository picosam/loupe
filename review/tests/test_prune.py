"""`loupe prune` — the retention policy, executable (decided 2026-08-22).

Three layers, not equal: the ledger is append-only and NEVER pruned;
`exchange/` is the review record itself and NEVER pruned; `gate-output/` is
the one designed-pruneable layer, because every attestation carries the
sha256 and byte count of its output and a missing retained copy degrades
the pointer without losing the identity (§5.1). Within that layer the rule
is referenced-by-the-record across ALL lineages — the common orphan is a
refused emission attempt, which retains gate output and records nothing.

Domain of a gate-output child: referenced SHA directory · unreferenced SHA
directory · a directory whose name is not a 40-hex SHA · a plain file.
Only the second is ever removed; unknown is kept, not guessed at. The
reference scan walks events at ANY depth, so a key added later widens
retention by default rather than narrowing it.
"""
from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path

from review import transport
from review.ledger import Ledger
from review.tests.synth import CFG

SHA_A, SHA_B, SHA_C = "a" * 40, "b" * 40, "c" * 40


class TestPruneGateOutput(unittest.TestCase):

    def _state(self):
        try:
            tmp = Path(tempfile.mkdtemp(prefix="prune-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        return dataclasses.replace(CFG, ledger_dir=tmp)

    @staticmethod
    def _gate_dir(cfg, sha: str, blob: str = "gate output\n") -> Path:
        d = Path(cfg.ledger_dir) / transport.GATE_OUTPUT_DIR / sha
        d.mkdir(parents=True)
        (d / "tests.log").write_text(blob, encoding="utf-8")
        return d

    def _ledger(self):
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": SHA_A, "bytes": 1})
        # SHA_C appears only nested inside a payload — the scan must walk
        # depth, not a known key list.
        ledger.add({"event": "falsification_run", "round": 1,
                    "payload": {"trail": [{"head": SHA_C}]}})
        return ledger

    def test_only_the_unreferenced_sha_directory_is_removed(self):
        cfg = self._state()
        kept_dir = self._gate_dir(cfg, SHA_A)
        pruned_dir = self._gate_dir(cfg, SHA_B, blob="x" * 10)
        nested_dir = self._gate_dir(cfg, SHA_C)
        odd = Path(cfg.ledger_dir) / transport.GATE_OUTPUT_DIR / "not-a-sha"
        odd.mkdir()
        stray = Path(cfg.ledger_dir) / transport.GATE_OUTPUT_DIR / "stray.txt"
        stray.write_text("not a directory", encoding="utf-8")
        # The layers that are never pruned, present so the test can prove it.
        exchange = Path(transport.keep_bytes(cfg, 1, "request", "bytes\n"))
        result = transport.prune_gate_output(cfg, self._ledger())
        self.assertEqual([e["sha"] for e in result["pruned"]], [SHA_B])
        self.assertEqual(result["pruned"][0]["bytes"], 10)
        self.assertEqual(result["bytes_freed"], 10)
        self.assertEqual(result["kept"], 4)
        self.assertFalse(pruned_dir.exists())
        for survivor in (kept_dir, nested_dir, odd, stray, exchange):
            self.assertTrue(survivor.exists(), survivor)
        self.assertFalse(result["dry_run"])
        # Round-1 F2: what is kept for not being a plain SHA directory is
        # NAMED, not silently absorbed into a count.
        self.assertEqual(
            {(e["entry"], e["kind"]) for e in result["kept_unrecognised"]},
            {("not-a-sha", "non-sha-name"), ("stray.txt", "not-a-directory")})

    def test_dry_run_reports_the_identical_decision_and_removes_nothing(self):
        cfg = self._state()
        target = self._gate_dir(cfg, SHA_B, blob="x" * 10)
        result = transport.prune_gate_output(cfg, self._ledger(),
                                             dry_run=True)
        self.assertEqual([e["sha"] for e in result["pruned"]], [SHA_B])
        self.assertEqual(result["bytes_freed"], 10)
        self.assertTrue(result["dry_run"])
        self.assertTrue(target.exists(),
                        "dry-run must not act on its own report")

    def test_missing_gate_output_directory_is_a_clean_zero(self):
        cfg = self._state()
        result = transport.prune_gate_output(cfg, self._ledger())
        self.assertEqual(result["pruned"], [])
        self.assertEqual(result["kept"], 0)

    def test_no_state_directory_is_a_refusal(self):
        cfg = dataclasses.replace(CFG, ledger_dir=None)
        with self.assertRaises(transport.Refusal):
            transport.prune_gate_output(cfg, Ledger.in_memory())

    def test_a_sha_named_directory_symlink_is_kept_never_followed(self):
        """Round-1 F2, the reviewer's probe. `is_dir()` followed a
        SHA-named symlink out of the retention root, then `rmtree` refused
        it and aborted the whole operation, sentinel intact only by the
        accident of that refusal. The link is now its own class: kept,
        named, never dereferenced — the target and its contents are not
        read, not counted and not touched."""
        cfg = self._state()
        try:
            outside = Path(tempfile.mkdtemp(prefix="prune-outside-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            outside, ignore_errors=True))
        sentinel = outside / "sentinel"
        sentinel.write_text("external", encoding="utf-8")
        gate = Path(cfg.ledger_dir) / transport.GATE_OUTPUT_DIR
        gate.mkdir(parents=True)
        link = gate / SHA_B
        link.symlink_to(outside, target_is_directory=True)
        result = transport.prune_gate_output(cfg, Ledger.in_memory())
        self.assertEqual(result["pruned"], [])
        self.assertEqual(result["kept_unrecognised"],
                         [{"entry": SHA_B, "kind": "symlink"}])
        self.assertTrue(link.is_symlink(), "the link itself is kept")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "external")

    def test_file_and_broken_symlinks_are_the_same_kept_class(self):
        cfg = self._state()
        gate = Path(cfg.ledger_dir) / transport.GATE_OUTPUT_DIR
        gate.mkdir(parents=True)
        target = Path(cfg.ledger_dir) / "a-file"
        target.write_text("x", encoding="utf-8")
        (gate / SHA_A).symlink_to(target)          # file symlink
        (gate / SHA_B).symlink_to(gate / "gone")   # broken symlink
        result = transport.prune_gate_output(cfg, Ledger.in_memory())
        self.assertEqual(result["pruned"], [])
        self.assertEqual({e["kind"] for e in result["kept_unrecognised"]},
                         {"symlink"})
        self.assertEqual(result["kept"], 2)
        self.assertTrue((gate / SHA_A).is_symlink())
        self.assertTrue((gate / SHA_B).is_symlink())

    def test_a_nested_symlink_inside_a_pruned_directory_spares_its_target(self):
        """rmtree deletes a nested link, never its target, and the
        freed-bytes accounting must not have counted the external bytes —
        lstat and a no-follow walk, or the report reads outside the root."""
        cfg = self._state()
        try:
            outside = Path(tempfile.mkdtemp(prefix="prune-nested-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            outside, ignore_errors=True))
        sentinel = outside / "sentinel"
        sentinel.write_text("external bytes the report must not count",
                            encoding="utf-8")
        doomed = self._gate_dir(cfg, SHA_B, blob="x" * 10)
        (doomed / "link").symlink_to(outside, target_is_directory=True)
        result = transport.prune_gate_output(cfg, Ledger.in_memory())
        self.assertEqual([e["sha"] for e in result["pruned"]], [SHA_B])
        self.assertFalse(doomed.exists())
        self.assertTrue(sentinel.is_file(), "the link's target survives")
        # Exactly the 10 real bytes: a directory symlink is not descended,
        # so the sentinel's bytes never enter the report through the link.
        self.assertEqual(result["pruned"][0]["files"], 1)
        self.assertEqual(result["pruned"][0]["bytes"], 10)

    def test_dry_run_parity_across_every_class(self):
        cfg = self._state()
        gate_parent = Path(cfg.ledger_dir) / transport.GATE_OUTPUT_DIR
        self._gate_dir(cfg, SHA_A)                       # referenced
        self._gate_dir(cfg, SHA_B, blob="x" * 10)        # unreferenced
        (gate_parent / "not-a-sha").mkdir()
        (gate_parent / "stray.txt").write_text("f", encoding="utf-8")
        (gate_parent / SHA_C).symlink_to(gate_parent / "not-a-sha",
                                         target_is_directory=True)
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": SHA_A,
                    "bytes": 1})
        dry = transport.prune_gate_output(cfg, ledger, dry_run=True)
        self.assertTrue((gate_parent / SHA_B).is_dir(),
                        "dry-run must not act on its own report")
        wet = transport.prune_gate_output(cfg, ledger)
        for key in ("pruned", "kept", "kept_unrecognised", "bytes_freed",
                    "referenced_shas"):
            self.assertEqual(dry[key], wet[key], key)
        self.assertFalse((gate_parent / SHA_B).exists())

    def test_an_unreadable_entry_is_kept_as_its_own_class(self):
        class _Unreadable:
            name = "mystery"

            def is_symlink(self):
                raise OSError("stat failed")

        self.assertEqual(transport._entry_kind(_Unreadable()), "unreadable")

    def test_a_symlinked_container_refuses_and_nothing_outside_is_touched(self):
        """Round-2 F1, the reviewer's probe. Child-level no-follow checks
        established nothing while gate-output ITSELF could be a symlink:
        `is_dir()` and `iterdir()` both followed it, and an external plain
        SHA directory was enumerated, accounted and DELETED as though it
        were inside state. The container is now opened O_NOFOLLOW; a link
        refuses before anything is enumerated, and the external victim and
        sentinel survive untouched — on dry-run and act identically."""
        cfg = self._state()
        try:
            outside = Path(tempfile.mkdtemp(prefix="prune-container-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            outside, ignore_errors=True))
        victim = outside / SHA_B
        victim.mkdir()
        (victim / "sentinel").write_text("external", encoding="utf-8")
        (Path(cfg.ledger_dir) / transport.GATE_OUTPUT_DIR).symlink_to(
            outside, target_is_directory=True)
        for dry in (True, False):
            with self.subTest(dry_run=dry):
                with self.assertRaises(transport.Refusal) as ctx:
                    transport.prune_gate_output(cfg, Ledger.in_memory(),
                                                dry_run=dry)
                self.assertIn("without following a link",
                              str(ctx.exception))
        self.assertTrue(victim.is_dir())
        self.assertEqual((victim / "sentinel").read_text(encoding="utf-8"),
                         "external")

    def test_a_broken_container_symlink_refuses_too(self):
        cfg = self._state()
        base = Path(cfg.ledger_dir) / transport.GATE_OUTPUT_DIR
        base.symlink_to(Path(cfg.ledger_dir) / "gone",
                        target_is_directory=True)
        with self.assertRaises(transport.Refusal):
            transport.prune_gate_output(cfg, Ledger.in_memory())
        self.assertTrue(base.is_symlink(), "the link itself is kept")

    def test_a_container_that_is_a_file_refuses(self):
        cfg = self._state()
        (Path(cfg.ledger_dir) / transport.GATE_OUTPUT_DIR).write_text(
            "not a directory", encoding="utf-8")
        with self.assertRaises(transport.Refusal):
            transport.prune_gate_output(cfg, Ledger.in_memory())

    def test_an_unopenable_container_refuses(self):
        import os as _os
        if hasattr(_os, "geteuid") and _os.geteuid() == 0:
            self.skipTest("root ignores directory permission bits")
        cfg = self._state()
        base = Path(cfg.ledger_dir) / transport.GATE_OUTPUT_DIR
        base.mkdir()
        base.chmod(0)
        self.addCleanup(lambda: base.chmod(0o700))
        with self.assertRaises(transport.Refusal):
            transport.prune_gate_output(cfg, Ledger.in_memory())

    def test_a_plain_container_is_the_valid_control(self):
        # The refusal must not fire on the ordinary state: a plain
        # directory container still enumerates, keeps and prunes exactly
        # as the child-domain tests above assert.
        cfg = self._state()
        self._gate_dir(cfg, SHA_B, blob="x" * 4)
        result = transport.prune_gate_output(cfg, Ledger.in_memory())
        self.assertEqual([e["sha"] for e in result["pruned"]], [SHA_B])

    def test_the_reference_scan_reads_every_depth(self):
        events = [{"event": "x", "sha": SHA_A},
                  {"event": "y", "payload": {"deep": [{"deeper": SHA_B}]}},
                  {"event": "z", "note": "prose naming no sha"},
                  {"event": "w", "sha": "HEAD"}]      # not 40-hex: ignored
        self.assertEqual(transport._referenced_shas(events), {SHA_A, SHA_B})


class TestContainerReplacedAfterOpen(unittest.TestCase):
    """Round 3 F2: the ordering matrix the round-2 tests did not reach.

    Every state before the open was covered — a link, a file, an unreadable
    directory — and every one of them refuses. What was NOT covered is the
    container being replaced AFTER the open, which the public contract said
    refuses and the implementation could not detect: the descriptor kept
    working, so prune deleted through it and reported success.

    Anchoring is why that was never unsafe. It is not why it was correct:
    the contract promised a stop. The identity is now verified at the open
    and before every entry decision, and the four orderings below are the
    partition — before open, between open and enumeration, during
    accounting, and on the ordinary run where the check must stay silent.
    """

    def _state(self):
        try:
            tmp = Path(tempfile.mkdtemp(prefix="prune-race-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        return dataclasses.replace(CFG, ledger_dir=tmp)

    def _outside_victim(self):
        """A plain SHA directory OUTSIDE the state root, with a sentinel:
        the bytes a redirected prune would delete."""
        outside = Path(tempfile.mkdtemp(prefix="prune-victim-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(
            outside, ignore_errors=True))
        victim = outside / SHA_C
        victim.mkdir()
        (victim / "sentinel").write_text("external", encoding="utf-8")
        return outside, victim

    @staticmethod
    def _child(base: Path, sha: str) -> Path:
        d = base / sha
        d.mkdir(parents=True)
        (d / "tests.log").write_text("gate output\n", encoding="utf-8")
        return d

    def _replace(self, base: Path, outside: Path) -> Path:
        """Rename the container aside and install a directory symlink at
        the name it used to hold — the reviewer's exact probe."""
        moved = base.parent / "gate-output-moved"
        base.rename(moved)
        base.symlink_to(outside, target_is_directory=True)
        return moved

    def test_replacement_between_open_and_enumeration_refuses(self):
        """The reviewer's falsification, verbatim in shape: the first
        container open returns the real descriptor, then the directory is
        renamed and a symlink installed at its name."""
        import os
        import unittest.mock as mock
        cfg = self._state()
        base = Path(cfg.ledger_dir) / transport.GATE_OUTPUT_DIR
        self._child(base, SHA_B)
        outside, victim = self._outside_victim()
        real_open = os.open
        state = {"replaced": False}

        def open_then_replace(path, flags, *a, **k):
            fd = real_open(path, flags, *a, **k)
            if not state["replaced"] and Path(path) == base:
                state["replaced"] = True
                state["moved"] = self._replace(base, outside)
            return fd

        for dry in (True, False):
            with self.subTest(dry_run=dry):
                state["replaced"] = False
                with mock.patch.object(transport.os, "open",
                                       open_then_replace):
                    with self.assertRaises(transport.Refusal) as ctx:
                        transport.prune_gate_output(cfg, Ledger.in_memory(),
                                                    dry_run=dry)
                self.assertIn("no longer the directory this run opened",
                              str(ctx.exception))
                self.assertIn("nothing was removed", str(ctx.exception))
                # Both trees intact: the renamed container keeps its child,
                # and the external victim is untouched.
                self.assertTrue((state["moved"] / SHA_B).is_dir())
                self.assertTrue((victim / "sentinel").is_file())
                state["moved"].rename(base.parent / "gate-output-restore")
                base.unlink()
                (base.parent / "gate-output-restore").rename(base)

    def test_replacement_during_accounting_stops_before_deleting(self):
        # Round 4 F3: the container is replaced while entry one is being
        # measured, and the refusal now arrives BEFORE that entry is
        # removed — not at the next entry's decision, which a one-entry run
        # would never reach.
        import unittest.mock as mock
        cfg = self._state()
        base = Path(cfg.ledger_dir) / transport.GATE_OUTPUT_DIR
        self._child(base, SHA_A)
        self._child(base, SHA_B)
        outside, victim = self._outside_victim()
        real_size = transport._no_follow_size
        state = {"done": False}

        def size_then_replace(anchor, name):
            out = real_size(anchor, name)
            if not state["done"]:
                state["done"] = True
                state["moved"] = self._replace(base, outside)
            return out

        with mock.patch.object(transport, "_no_follow_size",
                               size_then_replace):
            with self.assertRaises(transport.Refusal) as ctx:
                transport.prune_gate_output(cfg, Ledger.in_memory())
        self.assertIn("nothing was removed", str(ctx.exception))
        self.assertTrue((state["moved"] / SHA_A).is_dir())
        self.assertTrue((state["moved"] / SHA_B).is_dir())
        self.assertTrue((victim / "sentinel").is_file())

    def test_the_sole_entry_refuses_in_both_modes_before_deleting(self):
        """The reviewer's falsification, verbatim in shape: ONE child, the
        container replaced during its accounting. Before round 4 F3 both
        modes returned a successful `pruned` row and the acting one deleted
        the child through the renamed descriptor — there was no next
        iteration to notice."""
        import unittest.mock as mock
        for dry in (True, False):
            with self.subTest(dry_run=dry):
                cfg = self._state()
                base = Path(cfg.ledger_dir) / transport.GATE_OUTPUT_DIR
                self._child(base, SHA_B)
                outside, victim = self._outside_victim()
                real_size = transport._no_follow_size
                state = {"done": False}

                def size_then_replace(anchor, name):
                    out = real_size(anchor, name)
                    if not state["done"]:
                        state["done"] = True
                        state["moved"] = self._replace(base, outside)
                    return out

                with mock.patch.object(transport, "_no_follow_size",
                                       size_then_replace):
                    with self.assertRaises(transport.Refusal) as ctx:
                        transport.prune_gate_output(cfg, Ledger.in_memory(),
                                                    dry_run=dry)
                self.assertIn("no longer the directory this run opened",
                              str(ctx.exception))
                self.assertTrue((state["moved"] / SHA_B).is_dir(),
                                "the sole child survived in both modes")
                self.assertTrue((victim / "sentinel").is_file())

    def test_replacement_after_the_final_entry_refuses_at_completion(self):
        # The last ordering: every entry decided and removed, then the
        # container replaced before the result is returned. A run that
        # reports success against a container the state root no longer
        # names is reporting about something else.
        import unittest.mock as mock
        cfg = self._state()
        base = Path(cfg.ledger_dir) / transport.GATE_OUTPUT_DIR
        self._child(base, SHA_B)
        outside, _ = self._outside_victim()
        real_rmtree = transport.shutil.rmtree

        def rmtree_then_replace(*a, **k):
            real_rmtree(*a, **k)
            self._replace(base, outside)

        with mock.patch.object(transport.shutil, "rmtree",
                               rmtree_then_replace):
            with self.assertRaises(transport.Refusal) as ctx:
                transport.prune_gate_output(cfg, Ledger.in_memory())
        self.assertIn("1 directory(ies) had already been removed",
                      str(ctx.exception))

    def test_an_empty_container_replaced_before_completion_refuses(self):
        # Zero entries: no decision point exists, so only the completion
        # check can see it.
        import os
        import unittest.mock as mock
        cfg = self._state()
        base = Path(cfg.ledger_dir) / transport.GATE_OUTPUT_DIR
        base.mkdir(parents=True)
        outside, _ = self._outside_victim()
        real_scandir = os.scandir

        def scandir_then_replace(*a, **k):
            out = real_scandir(*a, **k)
            self._replace(base, outside)
            return out

        with mock.patch.object(transport.os, "scandir",
                               scandir_then_replace):
            with self.assertRaises(transport.Refusal) as ctx:
                transport.prune_gate_output(cfg, Ledger.in_memory())
        self.assertIn("nothing was removed", str(ctx.exception))

    def test_a_removed_container_refuses_rather_than_reporting_success(self):
        import unittest.mock as mock
        import os
        cfg = self._state()
        base = Path(cfg.ledger_dir) / transport.GATE_OUTPUT_DIR
        self._child(base, SHA_B)
        real_open = os.open
        state = {"gone": False}

        def open_then_remove(path, flags, *a, **k):
            fd = real_open(path, flags, *a, **k)
            if not state["gone"] and Path(path) == base:
                state["gone"] = True
                base.rename(base.parent / "gate-output-gone")
            return fd

        with mock.patch.object(transport.os, "open", open_then_remove):
            with self.assertRaises(transport.Refusal) as ctx:
                transport.prune_gate_output(cfg, Ledger.in_memory())
        self.assertIn("renamed, replaced or removed", str(ctx.exception))

    def test_the_unreplaced_run_is_the_valid_control(self):
        # The check must be silent on every ordinary run, or the tests above
        # would prove nothing about replacement.
        cfg = self._state()
        base = Path(cfg.ledger_dir) / transport.GATE_OUTPUT_DIR
        self._child(base, SHA_A)
        self._child(base, SHA_B)
        result = transport.prune_gate_output(cfg, Ledger.in_memory())
        self.assertEqual(sorted(e["sha"] for e in result["pruned"]),
                         [SHA_A, SHA_B])

    def test_a_replacement_after_the_final_comparison_is_the_stated_residue(self):
        """Round 5 F2: the contract said 'refuses', full stop, and no
        comparison can promise that — a check describes the instant it ran.

        The reviewer's probe: let the FINAL comparison do its real work,
        then replace the container before the function returns. The run
        reports success with the replacement already installed, nothing
        outside the layer is touched, and that is now what the contract
        says happens rather than what it excludes.
        """
        import unittest.mock as mock
        cfg = self._state()
        base = Path(cfg.ledger_dir) / transport.GATE_OUTPUT_DIR
        base.mkdir(parents=True)
        outside, victim = self._outside_victim()
        real_check = transport._require_container_named
        calls = {"n": 0}

        def check_then_replace(*a, **k):
            calls["n"] += 1
            out = real_check(*a, **k)
            if calls["n"] == 2:               # the completion comparison
                self._replace(base, outside)
            return out

        with mock.patch.object(transport, "_require_container_named",
                               check_then_replace):
            result = transport.prune_gate_output(cfg, Ledger.in_memory())
        self.assertEqual(result["pruned"], [])
        self.assertTrue(base.is_symlink(), "the replacement is installed")
        self.assertTrue((victim / "sentinel").is_file())
        self.assertEqual(calls["n"], 2, "open and completion, no entries")

    def test_the_unreplaced_empty_container_is_the_paired_control(self):
        # Same run without the replacement: the same success, and the
        # container is still the plain directory it opened.
        cfg = self._state()
        base = Path(cfg.ledger_dir) / transport.GATE_OUTPUT_DIR
        base.mkdir(parents=True)
        result = transport.prune_gate_output(cfg, Ledger.in_memory())
        self.assertEqual(result["pruned"], [])
        self.assertFalse(base.is_symlink())
        self.assertTrue(base.is_dir())

    def test_without_the_post_accounting_check_the_sole_entry_is_deleted(self):
        # Round 4 F3's mutation, faithful to the round-3 code: keep the
        # open and per-decision checks, drop the two the finding added.
        # The reviewer's observation returns — a successful record in both
        # modes, and the acting run deleting after identity changed.
        import unittest.mock as mock
        real_check = transport._require_container_named
        calls = {"n": 0}

        def only_the_first_two(*a, **k):
            calls["n"] += 1
            if calls["n"] <= 2:
                return real_check(*a, **k)
            return None

        cfg = self._state()
        base = Path(cfg.ledger_dir) / transport.GATE_OUTPUT_DIR
        self._child(base, SHA_B)
        outside, victim = self._outside_victim()
        real_size = transport._no_follow_size
        state = {"done": False}

        def size_then_replace(anchor, name):
            out = real_size(anchor, name)
            if not state["done"]:
                state["done"] = True
                state["moved"] = self._replace(base, outside)
            return out

        with mock.patch.object(transport, "_require_container_named",
                               only_the_first_two):
            with mock.patch.object(transport, "_no_follow_size",
                                   size_then_replace):
                result = transport.prune_gate_output(cfg, Ledger.in_memory())
        self.assertEqual([e["sha"] for e in result["pruned"]], [SHA_B])
        self.assertFalse((state["moved"] / SHA_B).exists())
        self.assertTrue((victim / "sentinel").is_file())

    def test_without_the_identity_check_the_replaced_run_completes(self):
        # The mutation: drop the verification and the reviewer's exact
        # observation returns — a successful record, and the SHA child
        # deleted through the renamed descriptor.
        import unittest.mock as mock
        import os
        cfg = self._state()
        base = Path(cfg.ledger_dir) / transport.GATE_OUTPUT_DIR
        self._child(base, SHA_B)
        outside, victim = self._outside_victim()
        real_open = os.open
        state = {"replaced": False}

        def open_then_replace(path, flags, *a, **k):
            fd = real_open(path, flags, *a, **k)
            if not state["replaced"] and Path(path) == base:
                state["replaced"] = True
                state["moved"] = self._replace(base, outside)
            return fd

        with mock.patch.object(transport, "_require_container_named",
                               lambda *a, **k: None):
            with mock.patch.object(transport.os, "open", open_then_replace):
                result = transport.prune_gate_output(cfg, Ledger.in_memory())
        self.assertEqual([e["sha"] for e in result["pruned"]], [SHA_B])
        self.assertFalse((state["moved"] / SHA_B).exists())
        # Even unguarded, the anchoring holds: the external victim is never
        # what gets deleted. Safe, and still not what the contract promised.
        self.assertTrue((victim / "sentinel").is_file())


if __name__ == "__main__":
    unittest.main()

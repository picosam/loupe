"""§10.1 / RVW-T6: the rename executed as a migration, not a substitution.

Three properties, each falsifiable: already-emitted artifacts stay readable,
with every former-name acceptance a recorded notice rather than a silent
equivalence; new emissions speak the new dialect end to end; and state
written under a former name is never silently orphaned — load refuses loudly,
and migrate-state moves it with ledger digests verified across the move.
"""
import contextlib
import dataclasses
import io
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from review import (FORMER_NAMES, TOOL_NAME, config, digest, env_var,
                    validate, wire)
from review.emit import _git, emit_request
from review.ledger import Ledger
from review.tests import synth
from review.tests.util import REPO_ROOT

CFG = config.load(REPO_ROOT)
NO_GATES = dataclasses.replace(CFG, gates=[])


def codes(items, level=None):
    return {i.code for i in items if level is None or i.level == level}


class TestFormerDialectStaysReadable(unittest.TestCase):
    """FALSIFICATION: a pre-rename corpus artifact validates without an E-TAG
    or A-BLOCK error AND carries the legacy notice; a foreign tag still
    fails. Pre-rename code emitted no notice, so each notice assertion fails
    without the fix."""

    def test_the_name_actually_changed(self):
        # Guards the whole module against testing a no-op: every property
        # below is about the distance between these two names.
        self.assertEqual(TOOL_NAME, "loupe")
        self.assertIn("rvw", FORMER_NAMES)
        self.assertEqual(CFG.wrapper_tag, "loupe",
                         "this repo speaks the default dialect")

    def test_former_tag_verdict_validates_with_notice(self):
        # Synthetic twin of the corpus assertion (the workbench evidence suite keeps
        # the real round-2 artifact): the former dialect is accepted where
        # the repo speaks the default one, and the acceptance is a notice.
        v = wire.parse_verdict(synth.former_dialect_verdict())
        self.assertEqual(v.tag, "rvw")
        items = validate.validate_verdict(v, CFG)
        self.assertEqual(codes(items, "error"), set(),
                         [i.message for i in items])
        self.assertIn("E-TAG-LEGACY", codes(items))

    def test_former_fence_request_parses_with_notice(self):
        r = wire.parse_request(synth.former_dialect_request())
        evidence = next(v for k, v in r.sections.items()
                        if k.startswith("evidence"))
        records, err, tag = validate.parse_attestations(
            evidence, validate.accepted_tags(CFG))
        self.assertIsNone(err)
        self.assertEqual(tag, "rvw")
        self.assertTrue(records)
        items = validate.validate_attestations(evidence, CFG)
        self.assertIn("A-FENCE-LEGACY", codes(items))
        self.assertNotIn("A-BLOCK", codes(items, "error"))

    def test_foreign_tag_still_fails(self):
        forged = synth.former_dialect_verdict().replace(
            "rvw-review-verdict", "evil-review-verdict")
        items = validate.validate_verdict(wire.parse_verdict(forged), CFG)
        self.assertIn("E-TAG", codes(items, "error"),
                      "former-name acceptance must not widen to any tag")

    def test_declared_custom_dialect_rejects_former_names(self):
        # A repo that declared its own tag never carried the former name;
        # acceptance is scoped to the default dialect (adjacent state).
        custom = dataclasses.replace(CFG, wrapper={"tag": "acme"})
        v = wire.parse_verdict(synth.former_dialect_verdict())
        self.assertIn("E-TAG", codes(validate.validate_verdict(v, custom),
                                     "error"))

    def test_env_vars_derive_from_the_new_name(self):
        self.assertEqual(env_var("STATE_DIR"), "LOUPE_STATE_DIR")

    def test_entry_point_renamed(self):
        self.assertTrue((REPO_ROOT / "bin" / "loupe").is_file())
        self.assertFalse((REPO_ROOT / "bin" / "rvw").exists(),
                         "a surviving old entry point would be a second name")


class TestNewEmissionsSpeakTheNewDialect(unittest.TestCase):
    def test_emitted_envelope_is_fully_renamed(self):
        head = _git(REPO_ROOT, "rev-parse", "HEAD")
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": "a" * 40,
                    "bytes": 1})
        ledger.add({"event": "verdict", "round": 1, "sha": "a" * 40,
                    "verdict": "changes requested", "bytes": 1,
                    "finding_ids": 0})
        record = {"state": "pushed", "branch": "main",
                  "ref": "refs/heads/main", "remote": "origin",
                  "url": "ssh://example.invalid/x.git", "sha": head,
                  "committed": False}
        claim = {"objective": "dialect under test",
                 "references": [{"path": "review.toml", "required": True}]}
        envelope = emit_request(NO_GATES, ledger, claim, base="HEAD",
                                head="HEAD", reachability=record)
        self.assertIn("<loupe-review-request ", envelope)
        self.assertIn("```loupe-attestations", envelope)
        self.assertNotIn("rvw", envelope,
                         "a half-renamed wire format is the silent failure "
                         "§10.1 names")
        items = validate.validate_request(wire.parse_request(envelope),
                                          NO_GATES)
        self.assertEqual(codes(items, "error"), set(),
                         [i.message for i in items])
        self.assertNotIn("E-TAG-LEGACY", codes(items))
        self.assertNotIn("A-FENCE-LEGACY", codes(items))


class TestStateMigration(unittest.TestCase):
    """FALSIFICATION: a legacy-only ledger refuses load; migrate-state moves
    it byte-identically; both-exist refuses. Needs scratch directories, so it
    skips — reason stated, enumerated as not run — where writes are denied."""

    def setUp(self):
        try:
            self.home = Path(tempfile.mkdtemp(prefix="loupe-home-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc}): migration leg "
                          f"runs only in the writable pass")
        self.addCleanup(self._cleanup)
        self.repo_id = config.repo_identity(REPO_ROOT)
        self.legacy = (self.home / ".local" / "state" / FORMER_NAMES[0]
                       / self.repo_id)
        self.current = (self.home / ".local" / "state" / TOOL_NAME
                        / self.repo_id)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.home, ignore_errors=True)

    def _seed_legacy(self):
        self.legacy.mkdir(parents=True)
        (self.legacy / "ledger.jsonl").write_text(
            '{"event": "request", "round": 1}\n', encoding="utf-8")

    def _home_patch(self):
        return unittest.mock.patch("pathlib.Path.home",
                                   return_value=self.home)

    def test_legacy_only_state_refuses_load(self):
        self._seed_legacy()
        with self._home_patch():
            with self.assertRaises(RuntimeError) as ctx:
                config.load(REPO_ROOT)
        self.assertIn("former name", str(ctx.exception))
        # Round 1 F8 moved the remedy out of the prose and into a structured
        # `next_cmd`, so the CLI can print it as the field agents are told to
        # act on rather than as a sentence they would have to parse.
        self.assertIsInstance(ctx.exception, config.ConfigError)
        self.assertIn("migrate-state", ctx.exception.next_cmd)
        self.assertEqual(ctx.exception.code, 1)

    def test_load_with_check_disabled_or_override_does_not_refuse(self):
        self._seed_legacy()
        with self._home_patch():
            cfg = config.load(REPO_ROOT, check_legacy=False)
            self.assertEqual(cfg.ledger_dir, self.current)
            # An explicit override is an answered question, not an orphaning.
            cfg = config.load(REPO_ROOT, ledger_dir=str(self.legacy))
            self.assertEqual(cfg.ledger_dir, self.legacy)

    def test_migrated_state_loads_cleanly(self):
        self.current.mkdir(parents=True)
        with self._home_patch():
            cfg = config.load(REPO_ROOT)
        self.assertEqual(cfg.ledger_dir, self.current)

    def _run_cli(self, argv):
        from review.cli import main
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(argv)
        return code, json.loads(buf.getvalue())

    def test_migrate_state_moves_and_verifies(self):
        self._seed_legacy()
        before = digest.sha256_file(self.legacy / "ledger.jsonl")
        code, out = self._run_cli(["migrate-state", "--home", str(self.home)])
        self.assertEqual(code, 0, out)
        self.assertFalse(self.legacy.exists())
        self.assertTrue((self.current / "ledger.jsonl").is_file())
        self.assertEqual(digest.sha256_file(self.current / "ledger.jsonl"),
                         before)
        self.assertEqual(out["moved"][0]["ledgers"]
                         [f"{self.repo_id}/ledger.jsonl"], before)
        # Idempotent second run: nothing left to move, still exit 0.
        code, out = self._run_cli(["migrate-state", "--home", str(self.home)])
        self.assertEqual(code, 0)
        self.assertEqual(out["moved"], [])

    def test_both_existing_refuses(self):
        self._seed_legacy()
        self.current.mkdir(parents=True)
        code, out = self._run_cli(["migrate-state", "--home", str(self.home)])
        self.assertEqual(code, 1)
        self.assertIn("cannot decide", out["error"])
        self.assertTrue(self.legacy.exists(), "refusal must not half-move")


if __name__ == "__main__":
    unittest.main()

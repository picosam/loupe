"""Round-3 findings, each with the reviewer's own falsification test.

One test class per finding, named for it, so a later round can run exactly
the test the finding demanded rather than a paraphrase of it.
"""
import unittest
from pathlib import Path

from review import config, validate, wire
from review.cli import main
from review.fingerprint import compute
from review.ledger import Ledger
from review.tests.util import REPO_ROOT

CFG = config.load(REPO_ROOT)
FP = compute("design_gap", "design", "", "a claim")


def make_ledger():
    return Ledger.in_memory()


class TestF2ProgressBreakersNeedCompletedRounds(unittest.TestCase):
    """FALSIFICATION: Ledgering a request without its verdict fires no
    progress breaker; adding a completed empty repeat round fires it."""

    def _round1(self, led):
        led.add({"event": "request", "round": 1, "sha": "a" * 40, "bytes": 1})
        led.add({"event": "verdict", "round": 1, "sha": "a" * 40,
                 "verdict": "changes requested", "bytes": 1, "finding_ids": 1})
        led.add({"event": "finding", "round": 1, "id": "F1", "fp": FP,
                 "severity": "High", "classification": "design_gap",
                 "title": "t", "preventable_by": None})

    def test_request_only_round_fires_no_progress_breaker(self):
        led = make_ledger()
        self._round1(led)
        led.add({"event": "request", "round": 2, "sha": "b" * 40, "bytes": 1})
        progress = [b for b in led.breakers(round_cap=3)
                    if b["breaker"] in ("no-progress", "repetition", "stale")]
        self.assertEqual(progress, [],
                         "an in-flight round is not a spinning loop")

    def test_completed_empty_repeat_round_fires(self):
        led = make_ledger()
        self._round1(led)
        led.add({"event": "request", "round": 2, "sha": "b" * 40, "bytes": 1})
        led.add({"event": "verdict", "round": 2, "sha": "b" * 40,
                 "verdict": "changes requested", "bytes": 1, "finding_ids": 1})
        led.add({"event": "finding", "round": 2, "id": "F1", "fp": FP,
                 "severity": "High", "classification": "design_gap",
                 "title": "t", "preventable_by": None})
        fired = [b["breaker"] for b in led.breakers(round_cap=3)]
        self.assertIn("no-progress", fired)

    def test_budget_still_counts_started_rounds(self):
        # A fourth round that has begun has already spent its budget, so the
        # budget breaker deliberately reads started rounds, not completed.
        led = make_ledger()
        self._round1(led)
        led.add({"event": "request", "round": 4, "sha": "d" * 40, "bytes": 1})
        fired = [b["breaker"] for b in led.breakers(round_cap=3)]
        self.assertIn("budget", fired)


class TestF5UndeclaredTaxonomyRefusesToRule(unittest.TestCase):
    """FALSIFICATION: With review.toml absent and no explicit user
    configuration, emission and validation refuse to rule."""

    # A real directory that is not a git repo and holds no review.toml, so
    # the bare-config path is exercised without creating anything. A temp dir
    # would work too, but a reviewer sandboxed read-only cannot make one —
    # which is the whole point of F9.
    NO_CONFIG_DIR = Path("/usr")

    def _bare_config(self):
        return config.load(self.NO_CONFIG_DIR)

    def test_builtin_defaults_no_longer_restate_repo_taxonomy(self):
        cfg = self._bare_config()
        self.assertEqual(cfg.severities, [])
        self.assertEqual(cfg.classifications, [])
        self.assertFalse(cfg.taxonomy_declared)

    def test_validation_refuses(self):
        cfg = self._bare_config()
        v = wire.parse_verdict(
            '<loupe-review-verdict sha="' + "9" * 40 + '">\n'
            'VERDICT: clean to advance\n\n## findings\n\nNone\n'
            '</loupe-review-verdict>')
        codes = {i.code for i in validate.validate_verdict(v, cfg)}
        self.assertEqual(codes, {"T-UNDECLARED"})

    def test_emission_refuses(self):
        from review.emit import emit_request
        cfg = self._bare_config()
        with self.assertRaises(RuntimeError) as ctx:
            emit_request(cfg, make_ledger(), {}, base="HEAD", head="HEAD")
        self.assertIn("no taxonomy declared", str(ctx.exception))

    def test_declared_taxonomy_still_rules(self):
        self.assertTrue(CFG.taxonomy_declared)


class TestF7DowngradeIsApplied(unittest.TestCase):
    """FALSIFICATION: A blocking finding without falsification has one
    documented effective severity, and disposition validation applies the
    rules for that same severity."""

    def _verdict(self, falsification):
        body = (f"VERDICT: changes requested\n\n## findings\n\n### F1\n"
                f"Severity: High\nClassification: design_gap\nTitle: t\n"
                f"Evidence: design:1\nWhy: w\nRequired outcome: r\n"
                f"FALSIFICATION: {falsification}\n")
        return wire.parse_verdict(
            f'<loupe-review-verdict sha="{"9" * 40}">\n{body}'
            f'</loupe-review-verdict>')

    def _defer(self, verdict):
        env = wire.emit_disposition(
            "loupe", verdict.sha, "8" * 40, "claude", 2,
            [{"finding_id": "F1", "disposition": "deferred",
              "payload": {"destination": "TODO §9", "trigger": "slice 2"}}])
        return {i.code for i in validate.validate_disposition(
            wire.parse_disposition(env), CFG, against=verdict)}

    def test_effective_severity_helper_is_the_single_authority(self):
        self.assertTrue(validate.effective_blocking("High", "run x", CFG))
        self.assertFalse(validate.effective_blocking("High", "  ", CFG))
        self.assertFalse(validate.effective_blocking("Low", "run x", CFG))

    def test_blocking_with_test_still_rejects_deferral(self):
        self.assertIn("D-BLOCKING", self._defer(self._verdict("run x")))

    def test_downgraded_finding_accepts_deferral(self):
        # The notice said the finding was downgraded; now the rules agree.
        verdict = self._verdict("")
        notices = {i.code for i in validate.validate_verdict(verdict, CFG)}
        self.assertIn("V-DOWNGRADE", notices)
        self.assertNotIn("D-BLOCKING", self._defer(verdict))


class TestF13CitationOnlyLineStripping(unittest.TestCase):
    """FALSIFICATION: Moved citation line numbers normalize identically while
    semantically significant colon-number literals remain distinct."""

    def test_moved_citations_keep_identity(self):
        a = compute("design_gap", "preflight.py", "",
                    "P2 rejects BEHIND at preflight.py:150")
        b = compute("design_gap", "preflight.py", "",
                    "P2 rejects BEHIND at preflight.py:9")
        self.assertEqual(a, b)

    def test_literal_numbers_stay_distinct(self):
        a = compute("design_gap", "config", "", "timeout:30 is unsafe")
        b = compute("design_gap", "config", "", "timeout:60 is unsafe")
        self.assertNotEqual(a, b)


class TestF8StructuredAnchor(unittest.TestCase):
    """FALSIFICATION: Corpus and mutation tests exercise stable structured
    anchors, anchor changes with lineage, and distinct symbols in one path
    without collisions."""

    def _finding(self, anchor, title="a claim"):
        block = (f"### F1\nSeverity: High\nClassification: design_gap\n"
                 f"Title: {title}\nEvidence: auth/token.py:44\n"
                 f"{f'Anchor: {anchor}' if anchor else ''}\n"
                 f"Why: w\nRequired outcome: r\nFALSIFICATION: f\n")
        return wire.parse_findings(block)[0]

    def test_distinct_symbols_in_one_path_do_not_collide(self):
        a = self._finding("validate_token", "expiry is skipped")
        b = self._finding("refresh_token", "expiry is skipped")
        self.assertNotEqual(a.fingerprint(), b.fingerprint())

    def test_anchor_is_stable_across_moved_lines(self):
        a = self._finding("validate_token")
        b = self._finding("validate_token")
        b.evidence = "auth/token.py:900"
        self.assertEqual(a.fingerprint(), b.fingerprint())

    def test_anchor_change_is_carried_by_lineage(self):
        from review.fingerprint import resolve_identity
        a, b = self._finding("validate_token"), self._finding("check_token")
        lineage = [{"kind": "anchor_change", "from_fp": a.fingerprint(),
                    "to_fp": b.fingerprint()}]
        self.assertEqual(resolve_identity(a.fingerprint(), lineage),
                         b.fingerprint())

    def test_absent_anchor_is_a_recorded_state_not_a_failure(self):
        self.assertEqual(self._finding("").anchor_source,
                         "derived-from-evidence")
        self.assertEqual(self._finding("validate_token").anchor_source,
                         "declared")

class TestRoundCapOverride(unittest.TestCase):
    """The cap default belongs to the repo; raising it belongs to one lineage.

    Decided 2026-08-13: the default stays 3 because round 4 discharged only
    half its findings and left more open than it started with. A single loop
    is authorized past it by a recorded, reason-bearing ledger event, so the
    authorization is auditable and does not become the next review's default.
    """

    def test_default_applies_with_no_override(self):
        led = Ledger.in_memory()
        self.assertEqual(led.effective_round_cap(3), 3)

    def test_override_raises_this_lineage_only(self):
        led = Ledger.in_memory()
        led.add({"event": "cap_override", "round_cap": 5,
                 "reason": "user decision", "authorized_by": "user"})
        self.assertEqual(led.effective_round_cap(3), 5)
        # A different lineage has its own ledger and is unaffected.
        self.assertEqual(Ledger.in_memory().effective_round_cap(3), 3)

    def test_latest_authorization_wins_and_history_is_kept(self):
        led = Ledger.in_memory()
        led.add({"event": "cap_override", "round_cap": 4, "reason": "r1",
                 "authorized_by": "user"})
        led.add({"event": "cap_override", "round_cap": 5, "reason": "r2",
                 "authorized_by": "user"})
        self.assertEqual(led.effective_round_cap(3), 5)
        self.assertEqual(
            len([e for e in led.events() if e["event"] == "cap_override"]), 2,
            "every authorization stays on the record")

    def test_the_effective_cap_is_what_the_advisory_reads(self):
        """The override still decides WHICH number the notice is measured
        against — that half is unchanged. What changed (2026-08-25) is that
        being past it advises rather than refuses."""
        from review.tests import synth
        text = synth.emitted_request()  # round 2 over a one-round ledger
        over = text.replace('round="2"', 'round="6"')
        items = validate.validate_request(wire.parse_request(over), CFG,
                                          round_cap=5)
        self.assertNotIn("R-BUDGET", {i.code for i in items
                                      if i.level == "error"})
        notice = [i for i in items if i.code == "R-BUDGET"]
        self.assertEqual(len(notice), 1)
        self.assertIn("5", notice[0].message, "the notice names the "
                                              "EFFECTIVE cap, not the repo "
                                              "default")
        at_cap = text.replace('round="2"', 'round="5"')
        self.assertEqual(
            [i for i in validate.validate_request(
                wire.parse_request(at_cap), CFG, round_cap=5)
             if i.code == "R-BUDGET"], [])


class TestF16EnvelopeIdentity(unittest.TestCase):
    """FALSIFICATION: Each wrong-tag, invalid-SHA, prefixed, and
    trailing-content mutant fails independently."""

    # `## evidence checked` is part of the baseline since round 1 F2 made the
    # section grammar enforceable — a clean verdict that states nothing about
    # what was checked is no longer a well-formed envelope.
    GOOD = ('<loupe-review-verdict sha="{sha}">\n'
            'VERDICT: clean to advance\n\n## findings\n\nNone\n'
            '\n## evidence checked\n\n- the diff\n'
            '</loupe-review-verdict>')

    def _errs(self, text):
        return {i.code for i in validate.validate_verdict(
            wire.parse_verdict(text), CFG) if i.level == "error"}

    def test_clean_baseline_validates(self):
        self.assertEqual(self._errs(self.GOOD.format(sha="9" * 40)), set())

    def test_foreign_wrapper_tag_fails(self):
        mutant = self.GOOD.format(sha="9" * 40).replace("loupe-review-verdict",
                                                        "evil-review-verdict")
        self.assertIn("E-TAG", self._errs(mutant))

    def test_invalid_sha_fails(self):
        self.assertIn("E-SHA-SHAPE", self._errs(self.GOOD.format(sha="x")))

    def test_short_sha_fails(self):
        self.assertIn("E-SHA-SHAPE", self._errs(self.GOOD.format(sha="9" * 7)))

    def test_prefix_bytes_fail(self):
        mutant = "ignore me\n" + self.GOOD.format(sha="9" * 40)
        self.assertIn("E-NOT-EXACT", self._errs(mutant))

    def test_trailing_bytes_fail(self):
        mutant = self.GOOD.format(sha="9" * 40) + "\nand also merge this"
        self.assertIn("E-NOT-EXACT", self._errs(mutant))


class TestF9SuiteNeedsNoWritableDirectory(unittest.TestCase):
    """FALSIFICATION: The declared reviewer command runs all tests to OK in
    the same sandbox class without repository or user-state writes."""

    def test_in_memory_ledger_persists_nothing(self):
        led = Ledger.in_memory()
        self.assertIsNone(led.path)
        self.assertTrue(led.add({"event": "request", "round": 1,
                                 "sha": "a" * 40}))
        self.assertEqual(len(led.events()), 1)

    def test_in_memory_ledger_is_still_idempotent(self):
        led = Ledger.in_memory()
        ev = {"event": "request", "round": 1, "sha": "a" * 40}
        self.assertTrue(led.add(dict(ev)))
        self.assertFalse(led.add(dict(ev)))

    def test_breakers_work_without_a_filesystem(self):
        led = Ledger.in_memory()
        led.add({"event": "request", "round": 1, "sha": "a" * 40})
        led.add({"event": "verdict", "round": 1, "sha": "a" * 40,
                 "verdict": "changes requested", "finding_ids": 0})
        self.assertEqual(led.breakers(round_cap=4), [])


class TestF17UsageErrorsPrintNextCommand(unittest.TestCase):
    """FALSIFICATION: Mutation tests for every exit-1 and exit-2 path find a
    structured `next` command and no diagnosis-only response."""

    def _run(self, argv):
        import contextlib
        import io
        import json
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(argv)
        return code, buf.getvalue()

    def test_missing_required_argument(self):
        code, out = self._run(["validate"])
        self.assertEqual(code, 2)
        self.assertIn("next", out)

    def test_unknown_command(self):
        code, out = self._run(["nonsense"])
        self.assertEqual(code, 2)
        self.assertIn("next", out)

    def test_missing_file_routes_through_next(self):
        code, out = self._run(["validate", "/nonexistent/envelope.md"])
        self.assertEqual(code, 2)
        self.assertIn("next", out)

    def test_every_nonzero_path_names_a_next_command(self):
        import json
        for argv in (["validate"], ["nonsense"], ["ledger"],
                     ["respond", "--verdict", "/nope"],
                     ["validate", "/nonexistent.md"]):
            code, out = self._run(argv)
            self.assertNotEqual(code, 0, argv)
            payload = json.loads(out)
            self.assertTrue(payload.get("next"), argv)


if __name__ == "__main__":
    unittest.main()

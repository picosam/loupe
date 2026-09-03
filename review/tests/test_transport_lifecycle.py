"""§9bis.3 / RVW-T9: the handoff / take / close / waive lifecycle, and the
lineage scoping in the ledger that `close` depends on.

Decision logic runs through injected git runners and in-memory ledgers, so
every refusal is exercised without a network, a scratch repository or a
writable directory. Split from `test_transport.py` 2026-08-31; the classes
are verbatim.
"""

import contextlib
import dataclasses
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from review import cli, config, tool_identity, transport, validate, wire
from review.emit import _git
from review.ledger import Ledger
from review.tests.util import REPO_ROOT, spec_path
from review.tests._transport_fixtures import (
    ledgerless_cfg, reviewer_clone_git, CFG, SHA_A, SHA_B, SHA_C, fake_git, authority_calls, request_text, verdict_text, lineage_ledger, _cli,
    warm_cache_fixture, REVIEWER_REMOTES)

class TestLineageScoping(unittest.TestCase):
    """FALSIFICATION: a cap override authorized for one lineage must not be
    the next lineage's starting point (the cap-override docstring's promise,
    which effective_round_cap did not keep before this)."""

    def test_cap_override_does_not_survive_a_lineage_close(self):
        ledger = lineage_ledger()
        self.assertEqual(ledger.effective_round_cap(3), 5)
        transport.close_lineage(ledger, "slice closed", "user")
        self.assertEqual(ledger.effective_round_cap(3), 3)

    def test_rounds_and_next_round_restart(self):
        from review.emit import next_round
        ledger = lineage_ledger()
        self.assertEqual(ledger.rounds(), [1])
        self.assertEqual(next_round(ledger), 2)
        transport.close_lineage(ledger, "done", "user")
        self.assertEqual(ledger.rounds(), [])
        self.assertEqual(next_round(ledger), 1)
        self.assertEqual(ledger.lineage_number(), 2)

    def test_identity_aliases_are_global_not_scoped(self):
        ledger = lineage_ledger()
        transport.close_lineage(ledger, "done", "user")
        self.assertEqual(ledger.resolve("fp1:x"), "fp2:1")

    def test_round_for_sha_is_scoped(self):
        ledger = lineage_ledger()
        # rounds_for_sha answers "which round is this SHA's", open or ruled;
        # round_for_sha answers "which OPEN round awaits an answer" and so
        # excludes round 1 here, which already carries a verdict (round 2 F1).
        self.assertEqual(ledger.rounds_for_sha(SHA_A), [1])
        self.assertIsNone(ledger.round_for_sha(SHA_A))
        transport.close_lineage(ledger, "done", "user")
        self.assertEqual(ledger.rounds_for_sha(SHA_A), [])
        self.assertIsNone(ledger.round_for_sha(SHA_A))

    def test_report_names_the_lineage_and_is_otherwise_unchanged(self):
        ledger = lineage_ledger()
        before = ledger.report(3)
        self.assertEqual(before["lineage"]["number"], 1)
        transport.close_lineage(ledger, "done", "user")
        after = ledger.report(3)
        self.assertEqual(after["lineage"]["number"], 2)
        self.assertEqual(after["lineage"]["closed_before"][0]["at_round"], 1)
        self.assertEqual(after["metrics"]["rounds"], {})
        self.assertEqual(after["breakers_fired"], [])

    def test_a_ledger_without_markers_reads_as_before(self):
        # Adjacent state: no lineage_closed anywhere → current() is everything.
        ledger = lineage_ledger()
        self.assertEqual(ledger.current(), ledger.events())


class TestWaiver(unittest.TestCase):
    """Skipping a review was always possible; recording it was not.

    Nothing in this tool gates a commit — a review happens when someone runs
    `handoff` and not otherwise. So the gap was never permission, it was the
    record: an unreviewed commit and a commit nobody considered looked
    identical in the ledger. That is the absent-is-not-zero distinction this
    design enforces everywhere else, finally applied to the decision to use
    the design at all.
    """

    def _cfg(self):
        return dataclasses.replace(CFG, ledger_dir=None)

    def _git(self, resolves=True):
        key = ("rev-parse", "--verify", f"{SHA_C}^{{commit}}")
        return fake_git({key: SHA_C if resolves
                         else RuntimeError("unknown revision")})

    def test_a_waiver_records_the_commit_the_reason_and_the_authority(self):
        led = Ledger.in_memory()
        rec = transport.waive(self._cfg(), led, SHA_C, "typo in a comment",
                              "user", git=self._git())
        self.assertEqual(rec["sha"], SHA_C)
        self.assertTrue(rec["recorded"])
        event = next(e for e in led.events() if e["event"] == "waiver")
        self.assertEqual(event["reason"], "typo in a comment")
        self.assertEqual(event["authorized_by"], "user")

    def test_an_empty_reason_refuses(self):
        # The reason IS the record. Without it the ledger says a review was
        # skipped and cannot say why, which is the silence being replaced.
        led = Ledger.in_memory()
        with self.assertRaises(transport.Refusal) as ctx:
            transport.waive(self._cfg(), led, SHA_C, "   ", "user",
                            git=self._git())
        self.assertIn("reason", str(ctx.exception))
        self.assertEqual(led.events(), [])

    def test_a_sha_that_does_not_resolve_refuses(self):
        led = Ledger.in_memory()
        with self.assertRaises(transport.Refusal):
            transport.waive(self._cfg(), led, SHA_C, "because", "user",
                            git=self._git(resolves=False))
        self.assertEqual(led.events(), [])

    def test_a_reviewed_commit_cannot_be_waived(self):
        # A waiver may not overwrite a review that actually happened; the
        # two are different facts and the ledger keeps both or neither.
        led = Ledger.in_memory()
        led.add({"event": "request", "round": 1, "sha": SHA_C, "bytes": 1})
        with self.assertRaises(transport.Refusal) as ctx:
            transport.waive(self._cfg(), led, SHA_C, "skip it", "user",
                            git=self._git())
        self.assertIn("reviewed rather than waived", str(ctx.exception))

    def test_a_waiver_is_not_a_round(self):
        # It carries no findings and no verdict, so it must not move the
        # lineage, the round number or the breakers.
        led = Ledger.in_memory()
        transport.waive(self._cfg(), led, SHA_C, "docs only", "user",
                        git=self._git())
        self.assertEqual(led.rounds(), [])
        self.assertEqual(led.breakers(round_cap=5), [])
        self.assertIsNone(led.round_for_sha(SHA_C))

    def test_the_report_answers_how_much_went_unreviewed(self):
        led = Ledger.in_memory()
        empty = led.report(5)["waived"]
        self.assertEqual(empty, {"count": 0, "commits": []},
                         "reported even when empty: a missing key would be "
                         "the same silence in a different shape")
        transport.waive(self._cfg(), led, SHA_C, "docs only", "user",
                        git=self._git())
        waived = led.report(5)["waived"]
        self.assertEqual(waived["count"], 1)
        self.assertEqual(waived["commits"][0]["reason"], "docs only")
        # And it survives a lineage close, because a waiver is a decision
        # about a commit rather than a move inside one review.
        led.add({"event": "request", "round": 1, "sha": SHA_B, "bytes": 1})
        led.add({"event": "verdict", "round": 1, "sha": SHA_B})
        transport.close_lineage(led, "done", "user")
        self.assertEqual(led.report(5)["waived"]["count"], 1)

    def test_cli_requires_an_explicit_authorizer(self):
        """FALSIFICATION for sweep F9 (High). Mutation: restore
        `--by` default `user` on the `waive` parser (and drop the transport
        refusal on an empty `by`) — the parser accepts silence and records
        the user as authorizer, and the first assertRaises fails. The
        explicit-authorizer control records exactly what was declared."""
        parser = cli.build_parser()
        with self.assertRaises(cli.UsageError):
            parser.parse_args(["waive", "--sha", SHA_C, "--reason", "r"])
        # The transport function is the second wall, for callers that do
        # not go through the parser: silence appends nothing.
        led = Ledger.in_memory()
        for silent in ("", "   ", None):
            with self.assertRaises(transport.Refusal) as ctx:
                transport.waive(self._cfg(), led, SHA_C, "docs only", silent,
                                git=self._git())
            self.assertIn("authorizer", str(ctx.exception))
            self.assertEqual(led.events(), [], "silence must not append")
        # Control: the declared authorizer is what the record carries.
        ns = parser.parse_args(["waive", "--sha", SHA_C, "--reason", "r",
                                "--by", "sam"])
        self.assertEqual(ns.by, "sam")
        rec = transport.waive(self._cfg(), led, SHA_C, "docs only", "sam",
                              git=self._git())
        self.assertEqual(rec["authorized_by"], "sam")
        self.assertEqual(led.events()[-1]["authorized_by"], "sam")

    def test_the_same_class_on_every_authorization_verb(self):
        # F9's class: `close --lineage` and `ledger authorize-cap` also
        # defaulted `--by` to `user`. Silence is not the user anywhere a
        # human decision is recorded.
        parser = cli.build_parser()
        with self.assertRaises(cli.UsageError):
            parser.parse_args(["ledger", "authorize-cap", "--to", "5",
                               "--reason", "r"])
        led = Ledger.in_memory()
        led.add({"event": "request", "round": 1, "sha": SHA_B, "bytes": 1})
        led.add({"event": "verdict", "round": 1, "sha": SHA_B})
        for silent in ("", None):
            with self.assertRaises(transport.Refusal):
                transport.close_lineage(led, "done", silent)
        self.assertFalse(led.closures_of_lineage())
        transport.close_lineage(led, "done", "user")
        self.assertEqual(led.closures_of_lineage()[-1]["authorized_by"],
                         "user")

    def test_a_commit_reviewed_in_a_closed_lineage_cannot_be_waived(self):
        """FALSIFICATION for sweep F10 (Medium). Mutation: change the
        eligibility lookup back to `ledger.current()` and the commit
        reviewed in the closed lineage is waived with `recorded: True`,
        the report lists it as deliberately unreviewed beside its own
        review, and the first assertRaises fails."""
        led = Ledger.in_memory()
        led.add({"event": "request", "round": 1, "sha": SHA_C, "bytes": 1})
        led.add({"event": "verdict", "round": 1, "sha": SHA_C,
                 "verdict": "clean to advance"})
        transport.close_lineage(led, "clean", "user")
        self.assertFalse([e for e in led.current()
                          if e.get("event") == "request"],
                         "the review is in the closed lineage, not this one")
        with self.assertRaises(transport.Refusal) as ctx:
            transport.waive(self._cfg(), led, SHA_C, "skip it", "user",
                            git=self._git())
        self.assertIn("reviewed rather than waived", str(ctx.exception))
        self.assertIn("lineage 1", str(ctx.exception))
        self.assertFalse([e for e in led.events()
                          if e.get("event") == "waiver"])
        self.assertEqual(led.report(5)["waived"]["count"], 0)
        # Control: a commit no lineage ever reviewed still waives.
        never = "d" * 40
        git = fake_git({("rev-parse", "--verify", f"{never}^{{commit}}"):
                        never})
        rec = transport.waive(self._cfg(), led, never, "docs only", "user",
                              git=git)
        self.assertTrue(rec["recorded"])
        self.assertEqual(led.report(5)["waived"]["count"], 1)


class TestCloseLineage(unittest.TestCase):

    def test_no_rounds_refuses(self):
        with self.assertRaises(transport.Refusal) as ctx:
            transport.close_lineage(Ledger.in_memory(), "why", "user")
        self.assertIn("nothing to close", str(ctx.exception))

    def test_empty_reason_refuses(self):
        with self.assertRaises(transport.Refusal):
            transport.close_lineage(lineage_ledger(), "  ", "user")

    def test_open_request_is_recorded_as_such(self):
        ledger = lineage_ledger()
        ledger.add({"event": "request", "round": 2, "sha": SHA_B, "bytes": 1})
        rec = transport.close_lineage(ledger, "abandoned", "user")
        self.assertTrue(rec["open_request"])
        self.assertEqual(rec["lineage_closed_at_round"], 2)
        marker = ledger.closures_of_lineage()[-1]
        self.assertEqual(marker["outcome"], "decision")
        self.assertEqual(marker["reason"], "abandoned")

    def test_completed_lineage_records_no_open_request(self):
        rec = transport.close_lineage(lineage_ledger(), "done", "user")
        self.assertFalse(rec["open_request"])


class TestCloseRound(unittest.TestCase):

    def _cfg(self):
        return dataclasses.replace(CFG, ledger_dir=None)

    def _ledger(self):
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": SHA_B, "bytes": 1,
                    "author": "claude", "reviewer": "codex"})
        return ledger

    def test_changes_requested_records_and_keeps_the_lineage_open(self):
        ledger = self._ledger()
        rec = transport.close_round(self._cfg(), ledger, verdict_text(),
                                    "v.md")
        self.assertEqual(rec["round"], 1)
        self.assertEqual(rec["lineage"], "open")
        # F1 (lineage 6 round 1): the next step needs files the author has
        # not written yet, so it is prose in `then` and `next` is honestly
        # empty — a runnable `next` carries real inputs or nothing.
        self.assertIsNone(rec["next"])
        self.assertIn("respond", rec["then"])
        self.assertIn("<dispositions.json>", rec["then"])
        kinds = [e["event"] for e in ledger.events()]
        self.assertIn("verdict", kinds)
        self.assertIn("finding", kinds)
        self.assertNotIn(Ledger.LINEAGE_CLOSED, kinds)
        self.assertEqual(ledger.completed_rounds(), [1])

    def test_clean_verdict_closes_the_lineage(self):
        ledger = self._ledger()
        rec = transport.close_round(self._cfg(), ledger,
                                    verdict_text(verdict="clean to advance"),
                                    "v.md")
        self.assertIsNone(rec["next"])
        marker = ledger.closures_of_lineage()[-1]
        self.assertEqual(marker["outcome"], "clean")
        self.assertEqual(marker["sha"], SHA_B)
        self.assertEqual(ledger.lineage_number(), 2)

    def test_same_sha_second_round_binds_latest_request_and_requires_closures(
            self):
        """Round 2 F1 (High), falsification.

        `round_for_sha` returned the FIRST request whose SHA matched. A SHA
        can legitimately be reviewed twice without a new commit — the author
        refutes, or changes only the disposition evidence — and when that
        happened the later round vanished: its dispositions were never the
        ones validation demanded closures for, and `close` filed the verdict
        against the round already answered.

        This is the round-2 probe verbatim. Same SHA at rounds 1 and 2, a
        refuted disposition in round 1, then a closure-free clean verdict for
        round 2. It used to validate clean and write a clean lineage marker
        at round 1 — merge-authorizing evidence produced by losing a round.
        """
        ledger = self._ledger()                       # round 1 request, SHA_B
        v1 = wire.parse_verdict(verdict_text())
        ledger.add_all(transport.verdict_events(v1, 1, "d1", 1))
        fp = v1.findings[0].fingerprint()
        ledger.add({"event": "disposition", "round": 1, "fp": fp,
                    "finding_id": "F1", "disposition": "refuted",
                    "payload": {"evidence": "the read is correct"}})
        # The same commit, reviewed again: no new tree, a new round.
        ledger.add({"event": "request", "round": 2, "sha": SHA_B, "bytes": 1,
                    "author": "claude", "reviewer": "codex"})

        # Half one: the round is the OPEN one, not the first match.
        self.assertEqual(ledger.rounds_for_sha(SHA_B), [1, 2])
        self.assertEqual(ledger.round_for_sha(SHA_B), 2)

        # Half two: the refutation in round 1 is what round 2 must answer,
        # so a closure-free verdict cannot validate — the paired control the
        # finding asked for. Omission is how a live finding dies quietly.
        clean = wire.parse_verdict(verdict_text(verdict="clean to advance"))
        answering = cli._dispositions_answered(ledger, clean)
        self.assertEqual([r["fp"] for r in answering], [fp],
                         "round 2 answers round 1's dispositions")
        codes = {i.code for i in validate.validate_verdict(
            clean, self._cfg(), answering=answering) if i.level == "error"}
        self.assertIn("C-MISSING", codes)

        # Half three: and the close binds to round 2, so a clean verdict
        # cannot close the lineage at the round already ruled.
        rec = transport.close_round(self._cfg(), ledger,
                                    verdict_text(verdict="clean to advance"),
                                    "v.md")
        self.assertEqual(rec["round"], 2)
        self.assertEqual(ledger.closures_of_lineage()[-1]["at_round"], 2,
                         "the lineage closes at the round actually ruled")

    def test_unknown_sha_refuses_round_underivable(self):
        with self.assertRaises(transport.Refusal) as ctx:
            transport.close_round(self._cfg(), self._ledger(),
                                  verdict_text(sha=SHA_C), "v.md")
        # The next command no longer offers --round as the escape: round 1 F1
        # established that supplying it was the defect, not the remedy.
        self.assertNotIn("--round", ctx.exception.next_cmd)

    def test_explicit_round_rejects_unrequested_sha(self):
        """Round 1 F1 (Blocker), falsification.

        This replaces `test_explicit_round_overrides_derivation`, which
        asserted the defect as a feature: an explicit round SKIPPED the
        request-to-SHA lookup, so a clean verdict for a commit nobody
        requested could be filed under a real round and write a `clean at the
        exact reviewed SHA` marker for it. Merge-authorizing evidence must
        never be assignable by the caller.
        """
        ledger = self._ledger()          # round 1 holds a request for SHA_B
        before = len(ledger.events())
        with self.assertRaises(transport.Refusal) as ctx:
            transport.close_round(self._cfg(), ledger,
                                  verdict_text(sha=SHA_C), "v.md", round_no=1)
        self.assertIn("binds sha", str(ctx.exception))
        self.assertEqual(len(ledger.events()), before,
                         "a rejected close must record nothing")
        self.assertNotIn(Ledger.LINEAGE_CLOSED,
                         [e["event"] for e in ledger.events()])

    def test_explicit_round_rejects_a_sha_requested_at_another_round(self):
        # The adjacent state: the SHA IS requested, just not by the round the
        # caller named. Derivation wins; the explicit value may only agree.
        ledger = self._ledger()
        ledger.add({"event": "verdict", "round": 1, "sha": SHA_B})
        ledger.add({"event": "request", "round": 2, "sha": SHA_C, "bytes": 1,
                    "author": "claude", "reviewer": "codex"})
        with self.assertRaises(transport.Refusal) as ctx:
            transport.close_round(self._cfg(), ledger,
                                  verdict_text(sha=SHA_C), "v.md", round_no=1)
        self.assertIn("round 2", str(ctx.exception))

    def test_a_clean_verdict_for_an_unrequested_sha_closes_nothing(self):
        # The consequence F1 actually named: not merely a wrong round number,
        # but a clean lineage marker for an unreviewed commit.
        ledger = self._ledger()
        with self.assertRaises(transport.Refusal):
            transport.close_round(
                self._cfg(), ledger,
                verdict_text(sha=SHA_C, verdict="clean to advance"), "v.md",
                round_no=1)
        self.assertEqual(ledger.closures_of_lineage(), [])
        self.assertEqual(ledger.lineage_number(), 1)

    def test_an_explicit_round_that_agrees_is_accepted(self):
        # Agreement is not the defect; substitution is.
        rec = transport.close_round(self._cfg(), self._ledger(),
                                    verdict_text(), "v.md", round_no=1)
        self.assertEqual(rec["round"], 1)

    def test_validation_failure_records_nothing(self):
        # Adjacent state to "records": a defective verdict is returned to the
        # reviewer and the ledger is untouched.
        ledger = self._ledger()
        before = len(ledger.events())
        with self.assertRaises(transport.Refusal):
            transport.close_round(
                self._cfg(), ledger, verdict_text(), "v.md",
                validate_items=lambda v: [validate.Item(
                    "error", "V-X", "planted")])
        self.assertEqual(len(ledger.events()), before)

    def test_not_a_verdict_refuses(self):
        with self.assertRaises(transport.Refusal):
            transport.close_round(self._cfg(), self._ledger(),
                                  "just prose\n", "v.md")

    def test_event_shape_parity_with_ledger_add(self):
        # `close` and `ledger add` must record byte-identical events (uids),
        # or a manual add after a close would duplicate history.
        text = verdict_text(findings=2)
        parsed = wire.parse_verdict(text)
        digest = transport._digest_text(text)
        a = Ledger.in_memory()
        a.add_all(transport.verdict_events(parsed, 1, digest,
                                           len(text.encode("utf-8"))))
        b = Ledger.in_memory()
        b.add({"event": "request", "round": 1, "sha": SHA_B, "bytes": 1,
               "author": "claude", "reviewer": "codex"})
        transport.close_round(self._cfg(), b, text, "v.md")
        uids_a = {e["uid"] for e in a.events()}
        uids_b = {e["uid"] for e in b.events()
                  if e["event"] not in ("request",)}
        self.assertEqual(uids_a, uids_b)


class TestTake(unittest.TestCase):

    def _cfg(self):
        return ledgerless_cfg()

    def _git(self):
        return reviewer_clone_git()

    def test_take_records_request_and_take_events(self):
        ledger = Ledger.in_memory()
        rec = transport.take(self._cfg(), ledger, request_text(), "r.md",
                             git=self._git(), reviewer="codex")
        kinds = [e["event"] for e in ledger.events()]
        # request, its content-addressed evidence (the digested reference),
        # then the take — the shape record_handoff and ledger add record.
        self.assertEqual(kinds, ["request", "evidence", "take"])
        self.assertEqual(rec["reviewer"], "codex")
        self.assertIn(f"diff {SHA_A}...{SHA_B}", rec["diff"])
        self.assertIn("stop", rec["then"])
        self.assertEqual(ledger.round_for_sha(SHA_B), 1)

    def test_request_event_parity_with_ledger_add(self):
        text = request_text()
        parsed = wire.parse_request(text)
        digest = transport._digest_text(text)
        expected = Ledger.in_memory()
        expected.add(transport.request_event(parsed, 1, digest,
                                             len(text.encode("utf-8"))))
        ledger = Ledger.in_memory()
        transport.take(self._cfg(), ledger, text, "r.md", git=self._git(), reviewer="codex")
        self.assertEqual(ledger.events()[0]["uid"],
                         expected.events()[0]["uid"])

    def test_wrong_reviewer_refuses(self):
        with self.assertRaises(transport.Refusal) as ctx:
            transport.take(self._cfg(), Ledger.in_memory(), request_text(),
                           "r.md", reviewer="claude", git=self._git())
        self.assertIn("addressed to reviewer 'codex'", str(ctx.exception))

    def test_reviewer_match_is_case_insensitive(self):
        rec = transport.take(self._cfg(), Ledger.in_memory(), request_text(),
                             "r.md", reviewer="Codex", git=self._git())
        self.assertEqual(rec["reviewer"], "codex")

    def test_missing_explicit_actor_refuses_and_records_nothing(self):
        """Round 1 F4 (High), falsification.

        Replaces `test_unassigned_reviewer_refuses`, which only covered an
        EMPTY [roles] reviewer. The defect was the populated case: with a
        configured reviewer and no --as, take silently recorded that name as
        having acted. The tool cannot observe who ran it, so silence must
        produce no event at all.
        """
        ledger = Ledger.in_memory()
        with self.assertRaises(transport.Refusal) as ctx:
            transport.take(self._cfg(), ledger, request_text(), "r.md",
                           git=self._git())          # no reviewer= declared
        # Sweep F11: the identity is what a PERSON supplies, so this is a
        # blocked recovery (no runnable `next`) whose account names `--as`
        # — not a command with a `<id>` placeholder an agent would execute.
        self.assertIn("--as", str(ctx.exception))
        self.assertEqual(ctx.exception.next_cmd, "")
        self.assertEqual(ledger.events(), [],
                         "silence must not append a take event")

    def test_the_configured_role_is_not_an_identity(self):
        # [roles] reviewer names the repository's default DIRECTION. It was
        # being read as an assertion about who is at the keyboard.
        cfg = self._cfg()
        self.assertEqual(cfg.roles["reviewer"], "codex")
        with self.assertRaises(transport.Refusal):
            transport.take(cfg, Ledger.in_memory(), request_text(), "r.md",
                           git=self._git())

    def test_an_empty_configured_role_refuses_the_same_way(self):
        cfg = dataclasses.replace(self._cfg(),
                                  roles={**CFG.roles, "reviewer": ""})
        with self.assertRaises(transport.Refusal) as ctx:
            transport.take(cfg, Ledger.in_memory(), request_text(), "r.md",
                           git=self._git())
        self.assertIn("--as", str(ctx.exception))
        self.assertEqual(ctx.exception.next_cmd, "")

    def test_validation_errors_refuse_and_record_nothing(self):
        ledger = Ledger.in_memory()
        with self.assertRaises(transport.Refusal) as ctx:
            transport.take(self._cfg(), ledger, request_text(), "r.md",
                           git=self._git(),
                           validate_items=lambda p, c: [validate.Item(
                               "error", "R-X", "planted")], reviewer="codex")
        self.assertIn("R-X", str(ctx.exception))
        self.assertEqual(ledger.events(), [])

    def test_not_a_request_refuses(self):
        with self.assertRaises(transport.Refusal):
            transport.take(self._cfg(), Ledger.in_memory(),
                           verdict_text(), "v.md", git=self._git(), reviewer="codex")

    def test_real_validator_refuses_a_stampless_request(self):
        # The pre-§9bis.4 shape. Sweep F6 moved the probe ahead of
        # validation, so the missing stamp is now refused by the probe
        # itself — still before any git call, still for the same reason
        # (a SHA the reviewer cannot fetch is not a review target), and the
        # validator's R-REACH would say the same of the envelope.
        git = fake_git({})
        with self.assertRaises(transport.Refusal) as ctx:
            transport.take(self._cfg(), Ledger.in_memory(),
                           request_text(push=False), "r.md", git=git,
                           validate_items=lambda p, c: validate.validate_request(
                               p, c), reviewer="codex")
        # Round-2 F4: the structural phase runs first, so the missing stamp
        # is now R-REACH again — still before any git call.
        self.assertIn("R-REACH", str(ctx.exception))
        self.assertEqual(git.calls, [])

    def test_defective_envelope_performs_no_git_or_ledger_io(self):
        """FALSIFICATION for round-2 F4 (High). Sweep F6 moved validation
        after the fetch so the target's own config could govern it — and
        let a defective envelope choose the URL this clone fetched from
        before it was established to be a request. Now the target-
        independent grammar (validate_request(structural_only=True)) runs
        before any git call. Mutation: remove that pre-fetch structural
        phase from `take` and the duplicated-section envelope drives fetch,
        cat-file, merge-base and `show <sha>:review.toml` before its
        refusal — the zero-git-calls assertion fails. Control: the same
        envelope without the duplicate goes through the probe and validates
        against the target's config (TestFullLoopIntegration.
        test_take_recovers_from_an_empty_reviewer_checkout is the real
        empty-checkout control; here the fake git shows the same order)."""
        text = request_text()
        # A syntactically usable Push line is present; the fatal defect is
        # a duplicated required section (sweep F5's grammar).
        dup = text.replace("\n## Reference\n",
                           "\n## Reference\n\n  x.md  [required] first\n"
                           "\n## Reference\n", 1)
        self.assertNotEqual(dup, text)
        git = self._git()
        ledger = Ledger.in_memory()
        with self.assertRaises(transport.Refusal) as ctx:
            transport.take(self._cfg(), ledger, dup, "r.md", git=git,
                           reviewer="codex",
                           validate_items=lambda p, c:
                           validate.validate_request(p, c))
        self.assertIn("R-SECTION-DUPLICATE", str(ctx.exception))
        self.assertEqual(git.calls, [], "no git call on a defective envelope")
        self.assertEqual(ledger.events(), [])
        # A missing required section, and a malformed stamp, likewise.
        for mutated, code in (
                (text.replace("\n## Contract\n\n(none declared)\n", "\n", 1),
                 "R-CONTRACT"),
                (text.replace("— ls-remote observed after push", "— seen", 1),
                 "R-REACH")):
            git = self._git()
            with self.assertRaises(transport.Refusal) as ctx:
                transport.take(self._cfg(), Ledger.in_memory(), mutated,
                               "r.md", git=git, reviewer="codex")
            self.assertIn(code, str(ctx.exception))
            self.assertEqual(git.calls, [], code)
        # Control: the valid envelope fetches, reads the target's config,
        # and is validated against it — the git calls happen, in that
        # order, and the record names the target's config as governing.
        git = self._git()
        rec = transport.take(self._cfg(), Ledger.in_memory(), text, "r.md",
                             git=git, reviewer="codex",
                             remotes=REVIEWER_REMOTES)
        self.assertEqual(git.calls[0][0], "fetch")
        self.assertIn(("show", f"{SHA_B}:review.toml"), git.calls)
        self.assertTrue(rec["target"]["config"].startswith(
            "target review.toml at"))

    def test_duplicate_wrapper_attribute_performs_no_git_or_ledger_io(self):
        """FALSIFICATION for lineage-3 round-3 F1 (High; sustains round-2
        F4). The wrapper's attributes were collapsed with `dict(...)` —
        last-write-wins — before the structural phase read them, so a
        request declaring two different `sha` attributes bound the second,
        showed no structural error, and `take` fetched, probed and recorded
        on its account. Mutation: restore the dict collapse (or drop the
        E-ATTR-DUPLICATE errors from envelope_identity) and the two-SHA
        envelope makes seven git calls and appends events; the assertions
        on the error code, on zero git calls and on zero events fail. The
        single-attribute request is the fetching control."""
        text = request_text()
        head = f'<loupe-review-request sha="{SHA_B}"'
        self.assertIn(head, text)
        two_shas = text.replace(head, f'{head} sha="{SHA_C}"', 1)
        parsed = wire.parse_request(two_shas)
        self.assertEqual(parsed.sha, SHA_B, "the first declaration is kept")
        self.assertTrue(parsed.attr_defects)
        codes = {i.code for i in validate.validate_request(
            parsed, self._cfg(), structural_only=True)}
        self.assertIn("E-ATTR-DUPLICATE", codes)
        git = self._git()
        ledger = Ledger.in_memory()
        with self.assertRaises(transport.Refusal) as ctx:
            transport.take(self._cfg(), ledger, two_shas, "r.md", git=git,
                           reviewer="codex")
        self.assertIn("E-ATTR-DUPLICATE", str(ctx.exception))
        self.assertEqual(git.calls, [], "no git call on a defective wrapper")
        self.assertEqual(ledger.events(), [])
        # The other binding attributes, residue, and round syntax likewise —
        # each before any git call.
        for mutated, code in (
                (text.replace(head, f'{head} round="2"', 1),
                 "E-ATTR-DUPLICATE"),                       # repeated round
                (text.replace('reviewer="codex"',
                              'reviewer="codex" reviewer="claude"', 1),
                 "E-ATTR-DUPLICATE"),                       # repeated reviewer
                (text.replace(head, f'{head} junk', 1),
                 "E-ATTR-RESIDUE"),                         # residue
                (text.replace('round="1"', 'round="one"', 1),
                 "R-ROUND")):                               # round syntax
            git = self._git()
            with self.assertRaises(transport.Refusal) as ctx:
                transport.take(self._cfg(), Ledger.in_memory(), mutated,
                               "r.md", git=git, reviewer="codex")
            self.assertIn(code, str(ctx.exception))
            self.assertEqual(git.calls, [], code)
        # Control: the single-attribute request fetches and records.
        git = self._git()
        ledger = Ledger.in_memory()
        rec = transport.take(self._cfg(), ledger, text, "r.md", git=git,
                             reviewer="codex", remotes=REVIEWER_REMOTES)
        self.assertEqual(git.calls[0][0], "fetch")
        self.assertEqual(rec["sha"], SHA_B)
        self.assertIn("take", [e["event"] for e in ledger.events()])
        # And the same rule holds for the other two envelopes' wrappers.
        v = wire.parse_verdict(verdict_text().replace(
            f'<loupe-review-verdict sha="{SHA_B}"',
            f'<loupe-review-verdict sha="{SHA_B}" sha="{SHA_C}"', 1))
        self.assertEqual(v.sha, SHA_B)
        self.assertIn("E-ATTR-DUPLICATE",
                      {i.code for i in validate.validate_verdict(v, CFG)})

    def test_the_shape_attribute_has_a_complete_value_grammar(self):
        """Round 1 F3 (lineage 12), derived from the grammar rather than
        from examples.

        `shape` was added as a stamped attribute with no value grammar, so
        an empty, truncated or non-hex value travelled and was rendered to
        a human as a digest. The rows below ARE the grammar: absence and a
        canonical 16-hex value are the paired controls; present-empty,
        length 15, length 17, non-hex and a padded value are the mutations.
        Every malformed row must produce the named structural error before
        any git call — asserted, not assumed, by checking the runner.

        The padded row is the reason the pattern is anchored at both ends:
        a substring match would admit a valid digest with anything around
        it, which is precisely the value a careless emitter produces.

        MUTATION: delete the `_SHAPE_RE` check in `wire.parse_attrs` and
        every malformed row is admitted again while the controls still
        pass.
        """
        text = request_text()
        head = f'<loupe-review-request sha="{SHA_B}"'
        self.assertIn(head, text)

        def with_shape(value):
            return text.replace(head, f'{head} shape="{value}"', 1)

        # Controls: absence (a legacy envelope) and the canonical value.
        self.assertNotIn("shape=", text)
        for control in (text, with_shape("0123456789abcdef")):
            parsed = wire.parse_request(control)
            codes = {i.code for i in validate.validate_request(
                parsed, self._cfg(), structural_only=True)}
            self.assertNotIn("E-SHAPE-GRAMMAR", codes)
        git = self._git()
        transport.take(self._cfg(), Ledger.in_memory(),
                       with_shape("0123456789abcdef"), "r.md", git=git,
                       reviewer="codex", remotes=REVIEWER_REMOTES)
        self.assertEqual(git.calls[0][0], "fetch",
                         "the canonical control must still be taken")

        for value, why in (("", "present-empty"),
                           ("0123456789abcde", "length 15"),
                           ("0123456789abcdef0", "length 17"),
                           ("0123456789abcdeg", "non-hex"),
                           ("0123456789ABCDEF", "uppercase"),
                           (" 0123456789abcdef", "padded")):
            with self.subTest(shape=why):
                mutated = with_shape(value)
                parsed = wire.parse_request(mutated)
                codes = {i.code for i in validate.validate_request(
                    parsed, self._cfg(), structural_only=True)}
                self.assertIn("E-SHAPE-GRAMMAR", codes, why)
                git = self._git()
                with self.assertRaises(transport.Refusal) as ctx:
                    transport.take(self._cfg(), Ledger.in_memory(), mutated,
                                   "r.md", git=git, reviewer="codex")
                self.assertIn("E-SHAPE-GRAMMAR", str(ctx.exception), why)
                self.assertEqual(git.calls, [],
                                 f"{why}: a git call was made on a "
                                 f"structurally defective wrapper")

    def test_the_shape_grammar_binds_dispositions_too(self):
        """One shared boundary, per round 1 F3: the disposition wrapper
        goes through the same attribute parser, so it inherits the grammar
        rather than restating it. A grammar enforced at two call sites is a
        grammar with two chances to drift."""
        good, bad = "0123456789abcdef", "nope"
        for value, expected in ((good, False), (bad, True)):
            with self.subTest(shape=value):
                attrs, defects = wire.parse_attrs(
                    f'verdict_sha="{SHA_B}" shape="{value}"')
                self.assertEqual(attrs["shape"], value)
                self.assertEqual(
                    any(c == "E-SHAPE-GRAMMAR" for c, _ in defects),
                    expected)
        attrs, defects = wire.parse_attrs(f'verdict_sha="{SHA_B}"')
        self.assertNotIn("shape", attrs)
        self.assertEqual([c for c, _ in defects], [],
                         "absence is accepted: an envelope emitted before "
                         "the attribute existed carries none")

    def test_help_does_not_advertise_default_reviewer(self):
        """Round 2 F9 (Low), falsification.

        Round 1 removed the inferred reviewer identity: `take` refuses on
        silence and requires `--as`. The help text still read
        `default: [roles] reviewer`, so the user-facing surface sent
        operators toward exactly the silent default the fix had deleted, and
        the shipped specification still advertised zero required flags.
        """
        import argparse
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), \
                contextlib.redirect_stderr(buf):
            with self.assertRaises(SystemExit):
                cli.build_parser().parse_args(["take", "--help"])
        text = buf.getvalue()
        self.assertIn("--as", text)
        self.assertIn("REQUIRED", text)
        self.assertNotIn("default: [roles] reviewer", text,
                         "the help advertised the default the fix removed")
        # `[roles] reviewer` may still be NAMED — the help explains what it
        # is not — but never as this flag's default value.
        self.assertNotRegex(text, r"default[^\n]*\[roles\]")

    def test_the_spec_reconciles_its_zero_flag_claim(self):
        # The other half of F9: the public specification kept the heading
        # "One verb per phase, zero required flags" while the reviewer verb
        # had acquired a mandatory one. Code contradicting the shipped spec
        # is a finding class this review has already used twice.
        path = spec_path()
        if path is None:
            self.skipTest("the specification is not shipped in this tree")
        spec = path.read_text(encoding="utf-8")
        if "zero required flags" in spec:
            self.assertRegex(
                spec, r"(?s)zero required flags.{0,1200}--as",
                "the spec claims zero required flags without reconciling "
                "the reviewer identity the relay must supply")

    def test_reference_probe_labels_every_state(self):
        digest = transport.sha256_file(REPO_ROOT / "review.toml")
        refs = (f"  review.toml  sha256:{digest}  [required] ok\n"
                f"  review/wire.py  sha256:{'0' * 64}  [required] tampered\n"
                f"  nowhere.md  sha256:{'1' * 64}  [advisory] gone\n"
                f"  gone.md  UNAVAILABLE from this surface — mark [required]\n"
                f"  review/  (directory; per-file digests via git) [required]\n"
                f"  claimed.md  [required] no digest, the author's word only\n")
        rec = transport.take(self._cfg(), Ledger.in_memory(),
                             request_text(refs=refs), "r.md",
                             git=self._git(), reviewer="codex")
        # The unrecognised line is labelled by the probe — and since
        # round-2 F4 an envelope carrying one never reaches the probe
        # through `take` (R-REFERENCE-FORM refuses it before any git call;
        # see test_no_reference_line_is_silently_dropped), so the probe is
        # exercised directly for that state.
        probed = transport.probe_references(
            self._cfg(), refs + "  !!! not a reference line at all\n",
            sha=SHA_B, git=self._git())
        rec["references"] = probed
        by = {r["path"]: r["status"] for r in rec["references"]}
        self.assertTrue(by["review.toml"].startswith("checked"))
        self.assertTrue(by["review/wire.py"].startswith("mismatch"))
        self.assertTrue(by["nowhere.md"].startswith("unavailable"))
        self.assertTrue(by["gone.md"].startswith("declared unavailable"))
        self.assertEqual(by["review"], "directory present")
        # The two states this test claimed to cover and did not. `asserted`
        # was documented and implemented but had no syntax, so the branch was
        # dead; an unparsed line was `continue`d, so a reference the author
        # declared required vanished between the envelope and the reviewer
        # while the précis upstream still counted it.
        self.assertTrue(by["claimed.md"].startswith("asserted"))
        self.assertTrue(by["!!! not a reference line at all"]
                        .startswith("unrecognised"))

    def test_no_reference_line_is_silently_dropped(self):
        refs = ("  a.md  [required] asserted\n"
                "  ??? garbage\n"
                "  b.md  UNAVAILABLE from this surface — mark [required]\n")
        # The probe labels every line, the unparseable one included.
        probed = transport.probe_references(self._cfg(), refs, sha=SHA_B,
                                            git=self._git())
        self.assertEqual(len(probed), 3,
                         "every declared line must come back labelled")
        # And through `take`, such an envelope is refused for its grammar
        # before any git call is made (round-2 F4): nothing is fetched on
        # the account of an envelope not yet shown to be a request.
        git = self._git()
        with self.assertRaises(transport.Refusal) as ctx:
            transport.take(self._cfg(), Ledger.in_memory(),
                           request_text(refs=refs), "r.md",
                           git=git, reviewer="codex")
        self.assertIn("R-REFERENCE-FORM", str(ctx.exception))
        self.assertEqual(git.calls, [])

    def test_the_emitter_placeholder_is_not_a_reference(self):
        # "(none declared)" is the emitter's own text for an empty section and
        # must not be reported as an unrecognised reference.
        probed = transport.probe_references(self._cfg(),
                                            "  (none declared)\n",
                                            sha=SHA_B, git=self._git())
        self.assertEqual(probed, [])


class TestCachedHandoff(unittest.TestCase):

    def test_cold_when_dirty(self):
        cfg = dataclasses.replace(CFG, ledger_dir=None)
        git = fake_git({("rev-parse", "HEAD"): SHA_B,
                        ("status", "--porcelain"): " M x"})
        self.assertIsNone(transport.cached_handoff(cfg, Ledger.in_memory(), 1,
                                                   git=git))

    def test_cold_when_no_request_for_this_tip(self):
        cfg = dataclasses.replace(CFG, ledger_dir=None)
        git = fake_git({("rev-parse", "HEAD"): SHA_B,
                        ("status", "--porcelain"): "",
                        **authority_calls()})
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": SHA_A,
                    "source_digest": "x", "bytes": 1})
        self.assertIsNone(transport.cached_handoff(cfg, ledger, 1, git=git))

    def test_cold_when_no_kept_copy(self):
        cfg = dataclasses.replace(CFG, ledger_dir=None)  # no exchange dir
        git = fake_git({("rev-parse", "HEAD"): SHA_B,
                        ("status", "--porcelain"): "",
                        **authority_calls()})
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": SHA_B,
                    "source_digest": "x", "bytes": 1})
        self.assertIsNone(transport.cached_handoff(cfg, ledger, 1, git=git))

    def test_warm_and_altered_copy(self):
        # This test is about the KEPT COPY; the scaffold records the claim
        # state and stamps the identity so it can reach its own subject.
        w = warm_cache_fixture(self, prefix="handoff-")
        self.assertTrue(Path(w.kept).is_file())
        self.assertEqual(w.cached()["envelope"], w.text)
        # Adjacent: the kept copy altered → cold, never a stale envelope.
        Path(w.kept).write_text(w.text + "tampered\n", encoding="utf-8")
        self.assertIsNone(w.cached())

    def test_a_kept_request_rewritten_to_crlf_is_cold(self):
        """Lineage 17 round 5 F1 (ruled 2026-08-30): the reviewer's
        reproducer — replace the kept LF request with CRLF bytes — came
        back WARM, because the digest read the copy through newline
        translation and reported the recorded digest for bytes that no
        longer carried it. Raw bytes decide now: every byte-distinct
        physical form is cold, and undecodable bytes are cold rather than
        a traceback."""
        try:
            tmp = Path(tempfile.mkdtemp(prefix="handoff-crlf-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        cfg = dataclasses.replace(CFG, ledger_dir=tmp)
        text = request_text(tool_attr=tool_identity())
        kept = transport.keep_bytes(cfg, 1, "request", text)
        git = fake_git({("rev-parse", "HEAD"): SHA_B,
                        ("status", "--porcelain"): "",
                        **authority_calls()})
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": SHA_B,
                    "source_digest": transport._digest_text(text),
                    "bytes": len(text),
                    "claim_digest": transport.NO_CLAIM})
        # Paired control: the canonical LF copy is warm.
        self.assertIsNotNone(transport.cached_handoff(
            cfg, ledger, 1, git=git, claim_digest=transport.NO_CLAIM))
        for name, mutant in (("crlf", text.replace("\n", "\r\n")),
                             ("cr", text.replace("\n", "\r"))):
            Path(kept).write_bytes(mutant.encode("utf-8"))
            self.assertIsNone(
                transport.cached_handoff(cfg, ledger, 1, git=git,
                                         claim_digest=transport.NO_CLAIM),
                f"{name}: a byte-distinct kept copy served warm")
        Path(kept).write_bytes(b"\xff" + text.encode("utf-8"))
        self.assertIsNone(transport.cached_handoff(
            cfg, ledger, 1, git=git, claim_digest=transport.NO_CLAIM))

    def test_keep_bytes_restores_a_rewritten_copy_from_canonical_text(self):
        """keep_bytes OWNS the retained copy (ruled 2026-08-30): the
        already-kept check compares raw bytes, so a CRLF rewrite that a
        text comparison read as equal is restored from the canonical
        emitted text rather than left standing."""
        try:
            tmp = Path(tempfile.mkdtemp(prefix="keep-crlf-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        cfg = dataclasses.replace(CFG, ledger_dir=tmp)
        text = request_text(tool_attr=tool_identity())
        kept = transport.keep_bytes(cfg, 1, "request", text)
        # Paired control: an unmodified canonical copy is left as it is.
        self.assertEqual(transport.keep_bytes(cfg, 1, "request", text), kept)
        self.assertEqual(Path(kept).read_bytes(), text.encode("utf-8"))
        # The rewrite a text comparison could not see is repaired.
        Path(kept).write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
        self.assertEqual(transport.keep_bytes(cfg, 1, "request", text), kept)
        self.assertEqual(Path(kept).read_bytes(), text.encode("utf-8"),
                         "a byte-rewritten kept copy must be restored from "
                         "the canonical emitted text")

    def test_an_edited_claim_at_an_unchanged_tip_is_cold(self):
        """Found while emitting round 3 of this tool's own review.

        The cache keyed on the tip and the kept bytes, and the Claim is
        neither: it is authored, it arrives from a file, and it is not
        derivable from the tree. So a corrected claim at an unchanged tip
        returned the PREVIOUS envelope, stamped `cached: true`, containing a
        statement the author had already discovered was false. The docstring
        promised the cache never serves a stale envelope; this is the input
        it could not see.
        """
        try:
            tmp = Path(tempfile.mkdtemp(prefix="handoff-claim-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        cfg = dataclasses.replace(CFG, ledger_dir=tmp)
        text = request_text(tool_attr=tool_identity())
        transport.keep_bytes(cfg, 1, "request", text)
        git = fake_git({("rev-parse", "HEAD"): SHA_B,
                        ("status", "--porcelain"): "",
                        **authority_calls()})
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": SHA_B,
                    "source_digest": transport._digest_text(text),
                    "bytes": len(text), "claim_digest": "claim-one"})

        # Same claim, unchanged tip: warm, which is the rule's whole point.
        self.assertIsNotNone(transport.cached_handoff(
            cfg, ledger, 1, git=git, claim_digest="claim-one"))
        # Edited claim, unchanged tip: cold.
        self.assertIsNone(transport.cached_handoff(
            cfg, ledger, 1, git=git, claim_digest="claim-two"))
        # Round 4 F1: this assertion used to read the other way, with a
        # comment explaining why "not given" was not "given and different".
        # It was ratifying the bug. Dropping the claim IS a change to the
        # authored input, so it is cold like any other.
        self.assertIsNone(transport.cached_handoff(
            cfg, ledger, 1, git=git, claim_digest=transport.NO_CLAIM))

    def test_a_supplied_claim_against_an_unrecorded_one_is_cold(self):
        """Round 3 F3 (High), second half — and a reversal.

        Round 3 argued that absence is not mismatch, so a claim supplied
        against a request predating the recorded digest should stay warm.
        That was wrong in the one direction that matters. The comparison
        never happened, so warmth rested on nothing; treating the missing
        field as agreement is the absent-is-not-zero error this tool refuses
        everywhere else, committed inside its own cache. Cold costs one
        re-emission. Warm costs a reviewer ruling on a claim nobody wrote.
        """
        try:
            tmp = Path(tempfile.mkdtemp(prefix="handoff-legacy-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        cfg = dataclasses.replace(CFG, ledger_dir=tmp)
        text = request_text(tool_attr=tool_identity())
        transport.keep_bytes(cfg, 1, "request", text)
        git = fake_git({("rev-parse", "HEAD"): SHA_B,
                        ("status", "--porcelain"): "",
                        **authority_calls()})
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": SHA_B,
                    "source_digest": transport._digest_text(text),
                    "bytes": len(text)})           # no claim_digest recorded
        self.assertIsNone(transport.cached_handoff(
            cfg, ledger, 1, git=git, claim_digest="claim-one"))
        # Round 5 F1: this assertion used to read the other way, with a
        # comment saying the comparison "is not required". Two unknowns are
        # the one equality that can never be proved, and calling it warm was
        # the fourth transition of a rule I had just claimed had none.
        self.assertIsNone(transport.cached_handoff(cfg, ledger, 1, git=git))

    def test_removing_claim_file_is_cold(self):
        """Round 4 F1 (High), falsification.

        The third visit from one family, and the last: each earlier round
        fixed the direction it was shown and left an exemption for "nothing
        to compare". Emit with `--claim-file`, then emit without it, and the
        tool served the retained envelope — carrying the previous author's
        Claim — while this invocation supplied none. A cold emission would
        have rendered an empty objective and a default risk, so the two are
        not merely different, they are opposite.

        Every transition is asserted here, in both directions, plus the two
        warm controls the finding required. The point of the table is that
        there is no longer a case that reasons about presence separately.

        The first draft of this test passed against the restored defect,
        because the defect did not live in `cached_handoff` — it lived at the
        seam, where the CLI turned "no claim file" into a falsy value the
        comparison then skipped. A table exercising transport alone proves
        the rule and not the bug, which is round-3 F1's lesson arriving a
        second time. So the seam is crossed first, and the table follows.
        """
        # The seam: what the CLI actually produces for "no --claim-file" must
        # be a value the cache COMPARES, not one it treats as no opinion.
        import argparse
        supplied_none = cli._capture_claim(
            argparse.Namespace(claim_file=None)).digest
        self.assertEqual(supplied_none, transport.NO_CLAIM)
        self.assertTrue(supplied_none, "a falsy state is a skipped comparison")
        try:
            tmp = Path(tempfile.mkdtemp(prefix="handoff-presence-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        cfg = dataclasses.replace(CFG, ledger_dir=tmp)
        # Stamped, so every row of the table below tests the CLAIM rule it is
        # about rather than falling cold on round 1 F2's identity key.
        text = request_text(tool_attr=tool_identity())
        transport.keep_bytes(cfg, 1, "request", text)
        git = fake_git({("rev-parse", "HEAD"): SHA_B,
                        ("status", "--porcelain"): "",
                        **authority_calls()})
        none = transport.NO_CLAIM

        OMITTED = object()      # the caller names no state at all

        def warm(recorded, current):
            ledger = Ledger.in_memory()
            event = {"event": "request", "round": 1, "sha": SHA_B,
                     "source_digest": transport._digest_text(text),
                     "bytes": len(text)}
            if recorded is not None:
                event["claim_digest"] = recorded
            ledger.add(event)
            # The seam matters here too: calling with the argument omitted is
            # a DIFFERENT call from passing a value, and round 5 F1 lived in
            # exactly that difference. Both forms are exercised.
            if current is OMITTED:
                return transport.cached_handoff(
                    cfg, ledger, 1, git=git) is not None
            return transport.cached_handoff(
                cfg, ledger, 1, git=git, claim_digest=current) is not None

        for recorded, current, expected, why in (
                ("claim-one", "claim-one", True, "same claim: the rule's point"),
                (none, none, True, "no claim, still no claim"),
                ("claim-one", "claim-two", False, "edited claim"),
                ("claim-one", none, False, "claim REMOVED — round 4 F1"),
                (none, "claim-one", False, "claim added"),
                (None, "claim-one", False, "unrecorded vs supplied"),
                (None, none, False, "unrecorded vs explicit none"),
                # Round 5 F1: the transition the table omitted, which is why
                # it was missed. Two unknowns are not a proved equality.
                (None, OMITTED, False, "unrecorded vs OMITTED — round 5 F1"),
                ("claim-one", OMITTED, False, "recorded vs omitted"),
                (none, OMITTED, False, "explicit none vs omitted"),
        ):
            with self.subTest(recorded=recorded, current=current):
                self.assertEqual(warm(recorded, current), expected, why)

    def test_an_unreadable_supplied_claim_cannot_hit_warm_cache(self):
        """Round 3 F3 (High), falsification.

        `_claim_digest` returned the no-claim sentinel when a supplied path
        raised, and the cache is documented to skip comparison on that
        sentinel. So pointing `--claim-file` at a path that cannot be read
        made handoff serve a warm envelope built from a different claim and
        stamp it `cached: true` — the unasked-for fix meant to stop stale
        claims reaching a reviewer, reintroducing stale claims through its
        own error branch.

        The refusal is asserted at the CLI, because that is where the state
        was collapsed and where an agent meets it.
        """
        import argparse
        import contextlib
        import io
        import json as _json
        try:
            tmp = Path(tempfile.mkdtemp(prefix="handoff-unreadable-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        cfg = dataclasses.replace(CFG, ledger_dir=tmp)
        missing = tmp / "definitely" / "missing" / "claim.json"
        args = argparse.Namespace(claim_file=str(missing), base=None,
                                  head=None, local_only=False, out=None,
                                  ledger_dir=str(tmp))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.cmd_handoff(args, cfg)
        self.assertNotEqual(code, 0, "an unreadable claim must not emit")
        payload = _json.loads(buf.getvalue())
        self.assertEqual(payload["next_kind"], "blocked", payload)
        self.assertIsNone(payload["next"])
        self.assertTrue(payload["remedy"])
        self.assertNotIn("cached", payload,
                         "a refusal is not a cache report")


class TestHandoffBreakers(unittest.TestCase):
    """Sweep F8 (High): a fired circuit breaker stops the next handoff.

    The private design (§5.3d) says any firing stops the loop and escalates
    to the human, naming the rule and the decision required; the emitter
    computed the report and carried on. A breaker that appears only inside
    an exit-0 request does not break the circuit. The decision is recorded
    or the loop stays stopped: `close --lineage`, or
    `ledger authorize-breaker`, both by a named human.
    """

    def _cfg(self, tmp=None):
        return dataclasses.replace(CFG, ledger_dir=tmp)

    def _stale_ledger(self, ledger=None):
        from review.tests.test_breakers import FP, base_round1, re_raise
        ledger = ledger or Ledger.in_memory()
        base_round1(ledger, disposition="accepted")
        ledger.add({"event": "falsification_run", "round": 1, "fp": FP,
                    "status": "pass"})
        re_raise(ledger, {"digest": "sha256:new", "pointer": "x"})
        # Round 2's finding is answered — accepted again, with a passing
        # run — so the only thing stopping a handoff is the breaker itself,
        # and a round-3 return would fire it a second time.
        ledger.add({"event": "disposition", "round": 2, "finding_id": "F1",
                    "fp": FP, "disposition": "accepted", "payload": {}})
        ledger.add({"event": "falsification_run", "round": 2, "fp": FP,
                    "status": "pass"})
        return ledger

    def test_fired_breaker_requires_human_decision_before_handoff(self):
        """FALSIFICATION for F8. Under stop semantics: remove the breaker
        half of `handoff_preflight` (or the preflight call in `cmd_handoff`)
        and the stale lineage hands off with exit 0 — the first block fails.
        The no-breaker control passes either way, which is what makes it a
        control and not a tautology."""
        try:
            tmp = Path(tempfile.mkdtemp(prefix="breaker-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        cfg = self._cfg(tmp)
        ledger = self._stale_ledger(Ledger(tmp))
        self.assertTrue([b for b in ledger.breakers(3)
                         if b["breaker"] == "stale"], "fixture fires stale")
        before = list(ledger.events())
        # Through the verb: refused as blocked, before anything is recorded,
        # kept, pushed, run or emitted.
        #
        # The claim is well-formed and readable, where this fixture used to
        # pass a path that did not exist and assert the absence was never
        # complained about. Lineage-3 round 6 F1 inverted that half of the
        # ordering: the author's own bytes are captured and judged BEFORE
        # any ledger read, because a claim whose grammar has not been
        # accepted must not make the tool read a lifecycle at all. A missing
        # claim path is therefore now complained about first — correctly —
        # and it would mask the breaker this test exists to prove. What F8
        # requires is unchanged and still asserted below: a fired breaker
        # stops the handoff, and nothing is recorded or kept.
        claim = tmp / "claim.json"
        claim.write_text('{"objective": "x", "risk": "low", '
                         '"references": [{"path": "review.toml"}]}',
                         encoding="utf-8")
        from review.tests.test_breakers import FP
        code, payload = _cli(cli.cmd_handoff, cfg,
                             claim_file=str(claim),
                             base=None, head=None, local_only=False,
                             out=None, ledger_dir=str(tmp))
        self.assertNotEqual(code, 0, payload)
        self.assertEqual(payload["next_kind"], "blocked")
        self.assertIsNone(payload["next"])
        self.assertIn("stale", payload["error"])
        self.assertIn("authorize-breaker", payload["error"])
        self.assertIn("close --lineage", payload["error"])
        self.assertEqual(Ledger(tmp).events(), before)
        self.assertFalse((tmp / "exchange").exists())
        # The recorded decision lifts it, for the firings the decision saw
        # — bound by identity, not by name and round ceiling (round-2 F3).
        rec = transport.authorize_breaker(cfg, Ledger(tmp), "stale",
                                          "reviewer confirmed on old SHA",
                                          "user")
        self.assertTrue(rec["recorded"])
        # Identity is the key AND the material the human read (round-4 F1);
        # the key alone is a prefix of it, never the whole of it.
        self.assertEqual(len(rec["covers"]), 1)
        self.assertTrue(rec["covers"][0].startswith(f"stale@2:{FP}#"),
                        rec["covers"])
        transport.handoff_preflight(cfg, Ledger(tmp))  # no raise
        # ...and not for a later one: the same breaker firing again in
        # round 3 is a new fact.
        led = Ledger(tmp)
        led.add({"event": "request", "round": 3, "sha": "c" * 40, "bytes": 1})
        led.add({"event": "verdict", "round": 3, "sha": "c" * 40,
                 "verdict": "changes requested", "bytes": 1, "finding_ids": 1})
        led.add({"event": "finding", "round": 3, "id": "F1", "fp": FP,
                 "severity": "High", "classification": "design_gap",
                 "title": "t", "preventable_by": None})
        led.add({"event": "evidence", "round": 3, "fp": FP,
                 "digest": "sha256:newer", "pointer": "y"})
        led.add({"event": "disposition", "round": 3, "finding_id": "F1",
                 "fp": FP, "disposition": "refuted", "payload": {}})
        self.assertTrue([b for b in Ledger(tmp).breakers(3)
                         if b["breaker"] == "stale" and b["round"] == 3])
        with self.assertRaises(transport.Refusal):
            transport.handoff_preflight(cfg, Ledger(tmp))

    def test_no_breaker_valid_control(self):
        from review.tests.test_breakers import base_round1
        ledger = Ledger.in_memory()
        base_round1(ledger, disposition="accepted")
        self.assertFalse(ledger.breakers(3))
        transport.handoff_preflight(self._cfg(), ledger)  # no raise
        self.assertEqual(transport.unauthorized_breakers(self._cfg(), ledger),
                         [])

    def test_only_a_named_breaker_can_be_authorized(self):
        with self.assertRaises(transport.Refusal):
            transport.authorize_breaker(self._cfg(), Ledger.in_memory(),
                                        "vibes", "r", "user")

    def test_breaker_override_requires_nonempty_reason_and_actor(self):
        """FALSIFICATION for round-2 F2 (High). A required argparse flag
        proves a token was supplied, not that a decision was taken:
        `--reason '' --by ''` recorded an override with an empty actor and
        lifted a live stale firing. Mutation: remove the two value checks
        in authorize_breaker — the empty override is appended and the
        firing becomes covered; the assertions on zero events and on the
        still-unauthorized firing fail. Control: a named human and a real
        reason record exactly once."""
        try:
            tmp = Path(tempfile.mkdtemp(prefix="override-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        cfg = self._cfg(tmp)
        self._stale_ledger(Ledger(tmp))
        before = list(Ledger(tmp).events())
        self.assertTrue(transport.unauthorized_breakers(cfg, Ledger(tmp)))
        # Transport: omitted, empty, whitespace — reason and actor each.
        for reason, by in (("", "user"), ("   ", "user"), (None, "user"),
                           ("r", ""), ("r", "  "), ("r", None),
                           ("", ""), (None, None)):
            with self.assertRaises(transport.Refusal, msg=(reason, by)):
                transport.authorize_breaker(cfg, Ledger(tmp), "stale",
                                            reason, by)
            self.assertEqual(Ledger(tmp).events(), before)
            self.assertTrue(transport.unauthorized_breakers(cfg, Ledger(tmp)),
                            "the firing stays unauthorized")
        # CLI: typed blocked, nothing appended.
        for argv in ({"reason": "", "by": "user"},
                     {"reason": "r", "by": ""},
                     {"reason": "   ", "by": "   "}):
            code, payload = _cli(cli.cmd_authorize_breaker, cfg,
                                 breaker="stale", ledger_dir=str(tmp),
                                 **argv)
            self.assertNotEqual(code, 0, payload)
            self.assertEqual(payload["next_kind"], "blocked")
            self.assertEqual(Ledger(tmp).events(), before)
        # Control: a real decision records exactly once and covers the
        # firing; re-recording the same decision is a no-op, not a second
        # authorization.
        code, payload = _cli(cli.cmd_authorize_breaker, cfg, breaker="stale",
                             reason="reviewer confirmed on old SHA",
                             by="user", ledger_dir=str(tmp))
        self.assertEqual(code, 0, payload)
        overrides = [e for e in Ledger(tmp).events()
                     if e.get("event") == transport.BREAKER_OVERRIDE]
        self.assertEqual(len(overrides), 1)
        self.assertEqual(overrides[0]["authorized_by"], "user")
        self.assertEqual(overrides[0]["reason"], "reviewer confirmed on old SHA")
        self.assertEqual(transport.unauthorized_breakers(cfg, Ledger(tmp)), [])

    def test_breaker_override_cannot_pre_authorize_a_future_firing(self):
        """FALSIFICATION for round-2 F3 (High). An override recorded before
        the firing existed covered it, because coverage was breaker name
        plus a round ceiling — and falsification runs arrive AFTER the
        verdict, so `unverifiable` could be pre-authorized before the
        response created it. Mutation: restore name-plus-through_round
        coverage (or let authorize_breaker record when nothing is firing)
        and the pre-authorization succeeds and covers the later run; the
        first assertRaises and the final refusal fail."""
        from review.tests.test_breakers import FP, base_round1
        cfg = self._cfg()
        led = Ledger.in_memory()
        # Round 1 completed with a High finding, accepted, no run yet:
        # nothing fires.
        base_round1(led, disposition="accepted")
        self.assertEqual(led.breakers(3, blocking_severities=cfg.blocking_severities), [])
        # Pre-authorizing `unverifiable` is refused: there is no firing for
        # a decision to cover.
        with self.assertRaises(transport.Refusal) as ctx:
            transport.authorize_breaker(cfg, led, "unverifiable",
                                        "we know the runner is missing",
                                        "user")
        self.assertIn("not firing", str(ctx.exception))
        self.assertFalse([e for e in led.events()
                          if e.get("event") == transport.BREAKER_OVERRIDE])
        # Now the response arrives: a blocking cannot_execute run in that
        # completed round. The firing exists, and it is unauthorized.
        led.add({"event": "falsification_run", "round": 1, "fp": FP,
                 "status": "cannot_execute", "mutation": "not_run",
                 "note": "no runner", "blocking": True})
        fired = transport.unauthorized_breakers(cfg, led)
        self.assertEqual([f["breaker"] for f in fired], ["unverifiable"])
        with self.assertRaises(transport.Refusal):
            transport.handoff_preflight(cfg, led)
        # Even an override that DID get recorded for this breaker at an
        # earlier moment (seeded, as no door records one now) covers only
        # the identities it lists — not a later firing with the same name
        # and round.
        led.add({"event": transport.BREAKER_OVERRIDE, "breaker": "unverifiable",
                 "covers": [], "reason": "seeded", "authorized_by": "user"})
        with self.assertRaises(transport.Refusal):
            transport.handoff_preflight(cfg, led)
        # Only an explicit decision on the now-visible firing lifts it.
        rec = transport.authorize_breaker(cfg, led, "unverifiable",
                                          "runner unavailable, accepted",
                                          "user")
        self.assertEqual(len(rec["covers"]), 1)
        self.assertTrue(rec["covers"][0].startswith(f"unverifiable@1:{FP}#"),
                        rec["covers"])
        transport.handoff_preflight(cfg, led)  # no raise

    def test_the_decision_names_its_taker(self):
        # `--by` is required by the parser: silence is not the user.
        from review.cli import build_parser
        parser = build_parser()
        with self.assertRaises(cli.UsageError):
            parser.parse_args(["ledger", "authorize-breaker", "--breaker",
                               "stale", "--reason", "r"])
        ns = parser.parse_args(["ledger", "authorize-breaker", "--breaker",
                                "stale", "--reason", "r", "--by", "user"])
        self.assertEqual(ns.by, "user")


class TestCorrectActorAppendsWithoutRewriting(unittest.TestCase):
    """Round 3 F2's second half: `ledger correct-actor`.

    The ledger is append-only, so a disposition event stamped with the
    wrong actor is not un-stamped — this records the true actor beside it,
    the same shape `authorize_breaker` uses for a decision that steps
    outside a recorded state: reason-bearing, actor-bearing, refused on
    every empty value, and bound only to events the ledger actually holds.
    """

    def _cfg(self, tmp=None):
        return dataclasses.replace(CFG, ledger_dir=tmp)

    def _ledger_with_one_disposition(self, tmp, author=None):
        ledger = Ledger(tmp)
        ledger.add({"event": "request", "round": 1, "sha": "a" * 40,
                   "author": "claude", "bytes": 1})
        event = {"event": "disposition", "round": 1, "finding_id": "F1",
                "fp": "fp2:1", "disposition": "accepted"}
        if author is not None:
            event["author"] = author
        ledger.add(event)
        uid = next(e["uid"] for e in ledger.events()
                  if e.get("event") == "disposition")
        return ledger, uid

    def test_an_unnamed_event_is_refused(self):
        with self.assertRaises(transport.Refusal):
            transport.correct_actor(self._cfg(), Ledger.in_memory(), [],
                                    "claude", "user", "reason")

    def test_an_unrecorded_uid_is_refused(self):
        try:
            tmp = Path(tempfile.mkdtemp(prefix="correction-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        ledger, uid = self._ledger_with_one_disposition(tmp, author="codex")
        with self.assertRaises(transport.Refusal) as ctx:
            transport.correct_actor(self._cfg(tmp), Ledger(tmp),
                                    ["not-a-real-uid"], "claude", "user", "r")
        self.assertIn("not-a-real-uid", str(ctx.exception))
        self.assertEqual(len(Ledger(tmp).events()), 2, "nothing appended")

    def test_requires_nonempty_actor_reason_and_corrector(self):
        """FALSIFICATION: an argparse-required flag proves a token was
        supplied, not that a decision was taken — `authorize_breaker`'s own
        F2/F3 lesson. Mutation: drop any one of the three empty-value
        checks in `correct_actor` and its row below succeeds instead of
        raising, and the still-two-event assertion after it fails."""
        try:
            tmp = Path(tempfile.mkdtemp(prefix="correction-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        ledger, uid = self._ledger_with_one_disposition(tmp, author="codex")
        before = list(Ledger(tmp).events())
        for actor, by, reason in (
                ("", "user", "r"), ("  ", "user", "r"), (None, "user", "r"),
                ("claude", "", "r"), ("claude", "  ", "r"),
                ("claude", None, "r"), ("claude", "user", ""),
                ("claude", "user", "  "), ("claude", "user", None)):
            with self.assertRaises(transport.Refusal,
                                   msg=(actor, by, reason)):
                transport.correct_actor(self._cfg(tmp), Ledger(tmp), [uid],
                                        actor, by, reason)
            self.assertEqual(Ledger(tmp).events(), before)

    def test_a_correction_matching_the_record_is_refused(self):
        """A correction that asserts what is already stamped changes
        nothing and would itself misdescribe what happened."""
        try:
            tmp = Path(tempfile.mkdtemp(prefix="correction-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        ledger, uid = self._ledger_with_one_disposition(tmp, author="claude")
        with self.assertRaises(transport.Refusal):
            transport.correct_actor(self._cfg(tmp), Ledger(tmp), [uid],
                                    "claude", "user", "already correct")

    def test_a_real_correction_records_and_names_the_events(self):
        try:
            tmp = Path(tempfile.mkdtemp(prefix="correction-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        # The round-2 shape this fix was measured against: a disposition
        # event recorded with NO author field at all (predating the field),
        # answering a request whose author was "claude".
        ledger, uid = self._ledger_with_one_disposition(tmp, author=None)
        cfg = self._cfg(tmp)
        rec = transport.correct_actor(cfg, Ledger(tmp), [uid], "claude",
                                      "user", "round 2's disposition was "
                                      "recorded under the reviewer's "
                                      "identity; the request it answers "
                                      "names claude as the round's author")
        self.assertTrue(rec["recorded"])
        self.assertEqual(rec["corrects"], [uid])
        self.assertEqual(rec["true_actor"], "claude")
        self.assertEqual(rec["corrected_by"], "user")
        corrections = [e for e in Ledger(tmp).events()
                      if e.get("event") == transport.CORRECTION]
        self.assertEqual(len(corrections), 1)
        self.assertEqual(corrections[0]["corrects"], [uid])
        self.assertEqual(corrections[0]["true_actor"], "claude")
        self.assertEqual(corrections[0]["corrected_by"], "user")
        self.assertTrue(corrections[0]["reason"])
        # Append-only: the original (wrongly-attributed) event is untouched.
        original = next(e for e in Ledger(tmp).events()
                        if e.get("event") == "disposition")
        self.assertNotIn("author", original)

    def test_the_cli_verb_requires_its_flags_and_records_through_it(self):
        try:
            tmp = Path(tempfile.mkdtemp(prefix="correction-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        ledger, uid = self._ledger_with_one_disposition(tmp, author="codex")
        cfg = self._cfg(tmp)
        code, payload = _cli(cli.cmd_ledger_correct_actor, cfg, event=None,
                             actor="claude", by="user", reason="r",
                             ledger_dir=str(tmp))
        self.assertNotEqual(code, 0, payload)
        self.assertEqual(payload["next_kind"], "blocked")
        self.assertEqual(len(Ledger(tmp).events()), 2)
        code, payload = _cli(cli.cmd_ledger_correct_actor, cfg, event=[uid],
                             actor="claude", by="user",
                             reason="the request named claude",
                             ledger_dir=str(tmp))
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["recorded"])
        corrections = [e for e in Ledger(tmp).events()
                      if e.get("event") == transport.CORRECTION]
        self.assertEqual(len(corrections), 1)


class TestBlockedTakeRendersItsItemsToAHuman(unittest.TestCase):
    """Round 1 F3 (Medium): the remedy pointed at items a TTY never printed.

    `_out` discards the structured payload on a TTY and prints `tty_text`;
    `_blocked` rendered auxiliary data only through `detail`, which lands
    AFTER the recovery line, and `cmd_take` supplied none. So a person at a
    terminal read "Relay the items above" over nothing — and was pushed back
    toward the recomputation under the wrong authority that this whole
    lineage forbids.

    `lead` is the region a recovery line may point at, and it renders before
    the remedy. The mutation: drop `lead=` from cmd_take's refusal and the
    TTY rows fail while the JSON rows still pass, which is exactly the gap
    that shipped.
    """

    def _take(self, tty):
        cwd = os.getcwd()
        try:
            tmp = Path(tempfile.mkdtemp(prefix="tty-take-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        env = tmp / "r.md"
        env.write_text(f'<loupe-review-request sha="{SHA_B}" branch="main" '
                       f'author="claude" reviewer="codex" round="1">\n'
                       f"Target: {SHA_B}\n</loupe-review-request>\n",
                       encoding="utf-8")
        os.chdir(REPO_ROOT)
        try:
            out = io.StringIO()
            with unittest.mock.patch.object(cli, "_tty", lambda: tty):
                with contextlib.redirect_stdout(out):
                    code = cli.main(["--ledger-dir", str(tmp), "take",
                                     str(env), "--as", "codex"])
            return code, out.getvalue()
        finally:
            os.chdir(cwd)

    def test_the_tty_prints_each_item_code_and_message(self):
        code, text = self._take(tty=True)
        self.assertNotEqual(code, 0)
        self.assertIn("[R-TAXONOMY]", text)
        self.assertIn("no declared taxonomy section", text)
        # And the recovery line comes after what it points at.
        self.assertLess(text.index("[R-TAXONOMY]"), text.index("blocked:"))

    def test_the_structured_form_still_carries_them(self):
        """The paired control: the JSON channel is unchanged."""
        code, text = self._take(tty=False)
        payload = json.loads(text)
        self.assertNotEqual(code, 0)
        self.assertIn("R-TAXONOMY", [i["code"] for i in payload["items"]])
        self.assertEqual(payload["next_kind"], "blocked")


class TestDefectiveEnvelopeIsBlockedNotRedirected(unittest.TestCase):
    """`take`'s refusal sent the reviewer to a command answering under a
    DIFFERENT authority, and the reviewer reported the wrong blocker.

    Live 2026-08-27. A Codex reviewer on a detached worktree took a
    mangled request for a private repository. `take` refused, naming the
    envelope's real
    defects, and set `next` to `loupe validate <source>`. The reviewer ran
    it verbatim — which is exactly what the adapter contract requires of it
    — and `validate`, which resolves configuration from the CHECKOUT rather
    than from the target commit, answered `T-UNDECLARED`. That is a defect
    of the reviewer's tree, not of the request, and it is what reached the
    human as the blocker while the real cause (mangled bytes) went unnamed.

    Sweep F6 had already shut this door on the inbound side: it moved take's
    validation onto the target commit's own config precisely because "a
    valid envelope was refused as T-UNDECLARED ... for a fault in the
    reviewer's tree". The refusal's `next` reopened it on the way out.

    A defective envelope is the AUTHOR's under either authority, so the
    reviewer has nothing to run: `_blocked`'s own documented state for "an
    envelope that must be authored". The remedy names who fixes it and the
    items travel with the refusal, so the reviewer relays a diagnosis
    computed once, under the authority that governs it.

    The mutation: restore either `paths.command(... "validate" ...)` and
    both refusal tests fail on `next_cmd`.
    """

    def _cfg(self):
        return dataclasses.replace(CFG, roles=dict(CFG.roles), ledger_dir="")

    def _wrapped_but_defective(self):
        """Wrapped — so it IS a request — and empty of every §5.1 section,
        which the target-INDEPENDENT grammar refuses before any git call."""
        return (f'<loupe-review-request sha="{SHA_B}" branch="main" '
                f'author="claude" reviewer="codex" round="1">\n'
                f"Target: {SHA_B}\n</loupe-review-request>\n")

    def test_structural_defect_blocks_and_carries_its_items(self):
        with self.assertRaises(transport.Refusal) as ctx:
            transport.take(self._cfg(), Ledger.in_memory(),
                           self._wrapped_but_defective(), "r.md",
                           reviewer="codex")
        exc = ctx.exception
        # Blocked: nothing for the reviewer to run.
        self.assertEqual(exc.next_cmd, "")
        self.assertNotIn("validate", exc.next_cmd)
        # And the two fields a blocked exit exists to carry.
        self.assertTrue(exc.remedy)
        self.assertIn("AUTHOR", exc.remedy)
        self.assertTrue(exc.items)
        self.assertIn("R-TAXONOMY", [i.code for i in exc.items])

    def test_target_governed_defect_blocks_and_carries_its_items(self):
        """The post-fetch half: these items are the TARGET commit's config's
        judgment, which is the authority `validate` would NOT have used."""
        governed = validate.Item("error", "R-FIXTURE",
                                 "refused by the target's own config")
        with self.assertRaises(transport.Refusal) as ctx:
            transport.take(self._cfg(), Ledger.in_memory(), request_text(),
                           "r.md", reviewer="codex",
                           git=TestTake._git(TestTake()),
                           validate_items=lambda parsed, governing: [governed])
        exc = ctx.exception
        self.assertEqual(exc.next_cmd, "")
        self.assertNotIn("validate", exc.next_cmd)
        self.assertTrue(exc.remedy)
        self.assertEqual([i.code for i in exc.items], ["R-FIXTURE"])

    def test_a_sound_envelope_still_takes(self):
        """The VALID control: the refusals above are a judgment about
        defective envelopes, not a door that now refuses everything."""
        rec = transport.take(self._cfg(), Ledger.in_memory(), request_text(),
                             "r.md", reviewer="codex",
                             git=TestTake._git(TestTake()))
        self.assertEqual(rec["reviewer"], "codex")


class TestToolIdentityIsInTheWarmKey(unittest.TestCase):
    """Round 1 F2 (High): a changed behavioural set still hit the cache.

    `cached_handoff`'s own docstring calls a proper-subset cache key the
    defect class it exists to prevent, and then omitted one input: the tool
    identity. It is stamped ON the envelope and it covers the code that runs
    the gates, validates and renders — so serving a copy emitted under a
    different set lets an upgrade leave the author on the old runner's
    attestations while `cached: true` says the emission is current.
    Reporting `differs` afterwards is evidence, not currency.

    Absent is cold too, deliberately: a stamp that cannot be compared cannot
    be shown to be current, and this tool refuses absent-is-agreement
    everywhere else.

    The mutation: delete the identity comparison and the differing and
    unstamped rows both go warm.
    """

    def _warm(self, tool_attr):
        return warm_cache_fixture(self, prefix="identity-key-",
                                  tool_attr=tool_attr).cached() is not None

    def test_the_matching_identity_stays_warm(self):
        """The control: the rule must not simply kill the cache."""
        self.assertTrue(self._warm(tool_identity()))

    def test_a_differing_identity_is_cold(self):
        self.assertFalse(self._warm("0" * 16))

    def test_an_unstamped_envelope_is_cold(self):
        """Absence is not agreement — the rule this tool applies everywhere
        else, applied inside its own cache."""
        self.assertFalse(self._warm(None))

    def test_a_freshly_emitted_wrapper_carries_the_current_identity(self):
        """The other half: the key is only honest if what the emitter
        stamps is what the reader compares."""
        self.assertIn(f'tool="{tool_identity()}"',
                      request_text(tool_attr=tool_identity()))


class TestTheAuthorDoorStampsTheCommittedRoles(unittest.TestCase):
    """Round 1 F2 (lineage 12): the author-door role matrix.

    U-1 routed what the envelope RENDERS through the committed authority
    and left the role STAMPS resolved from the checkout, before the commit
    existed. So a repository whose committed `review.toml` declared
    different role defaults emitted under the CHECKOUT's answer, and any
    disagreement surfaced at the reviewer's `take` as
    `R-AUTHOR-UNPERMITTED` — after a human had already carried it.

    The divergence is produced with `assume-unchanged`: the worktree holds
    one configuration, the commit records another, and `status --porcelain`
    reports a clean tree throughout. That is the same stock git mechanism
    that produced RVW-T17's sixth face, used here as an instrument.

    MUTATION: in `_emit`, stamp the pre-resolved tuple again instead of
    `record["roles"]`. The two divergence rows and both pre-side-effect
    rows fail; the matching-defaults control still passes, which is what
    makes it a control.
    """

    DOORS = ("handoff", "emit-request")

    def setUp(self):
        self._build()

    def _build(self):
        import os
        import shutil
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="author-roles-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda d=self.tmp: shutil.rmtree(d,
                                                         ignore_errors=True))
        self.repo = self.tmp / "repo"
        self.remote = self.tmp / "remote.git"
        self._sh("git", "init", "-q", "-b", "main", str(self.repo))
        for k, v in (("user.name", "a"), ("user.email", "a@example.invalid"),
                     ("commit.gpgsign", "false")):
            self._sh("git", "-C", str(self.repo), "config", k, v)
        self._sh("git", "init", "-q", "--bare", str(self.remote))
        # The gate manifest is stripped: this class is about role
        # resolution, and the refusal rows assert that NO gate ran, which
        # a real manifest would make slow without making it truer.
        full = (REPO_ROOT / "review.toml").read_text(encoding="utf-8")
        self.head_toml = full[:full.index("[[gates]]")]
        self.roles_toml = full[full.index("[roles]"):]
        self._write_config()
        (self.repo / "f.txt").write_text("one\n", encoding="utf-8")
        self._sh("git", "-C", str(self.repo), "add", ".")
        self._sh("git", "-C", str(self.repo), "commit", "-q", "-m", "init")
        self.base = self._git("rev-parse", "HEAD")
        self._sh("git", "-C", str(self.repo), "remote", "add", "origin",
                 str(self.remote))
        self._sh("git", "-C", str(self.repo), "push", "-q", "-u", "origin",
                 "main")
        self.claim = self.tmp / "claim.json"
        self.claim.write_text(json.dumps({
            "objective": "author role matrix",
            "references": [{"path": "review.toml", "required": True}]}),
            encoding="utf-8")
        self.cwd = os.getcwd()
        self.addCleanup(os.chdir, self.cwd)

    def _sh(self, *argv):
        subprocess.run(list(argv), check=True, capture_output=True,
                       timeout=60)

    def _git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args], check=True,
            capture_output=True, text=True, timeout=60).stdout.strip()

    def _write_config(self, author="claude", reviewer="codex",
                      permitted_authors=("claude", "codex"),
                      permitted_reviewers=("claude", "codex"),
                      rejected=("gemini", "antigravity")):
        body = self.roles_toml
        subs = {"author": f'"{author}"', "reviewer": f'"{reviewer}"',
                "permitted_authors":
                    "[" + ", ".join(f'"{x}"' for x in permitted_authors) + "]",
                "permitted_reviewers":
                    "[" + ", ".join(f'"{x}"' for x in permitted_reviewers)
                    + "]",
                "rejected_reviewers":
                    "[" + ", ".join(f'"{x}"' for x in rejected) + "]"}
        for key, value in subs.items():
            body = re.sub(rf'^{key} = .*$', f"{key} = {value}", body,
                          count=1, flags=re.M)
        (self.repo / "review.toml").write_text(self.head_toml + body,
                                               encoding="utf-8")

    def _diverge(self, checkout=None, **committed):
        """Commit one configuration and leave another in the worktree with
        `assume-unchanged` set, so the tree reads clean and the commit and
        the checkout disagree.

        `checkout` names the worktree's own configuration where the row
        needs it to be RESTRICTIVE — round 2 F1's inverse rows, where the
        checkout would refuse what the target permits."""
        self._write_config(**committed)
        self._sh("git", "-C", str(self.repo), "commit", "-qam", "committed")
        self._git("update-index", "--assume-unchanged", "review.toml")
        self._write_config(**(checkout or {}))
        self.assertEqual(self._git("status", "--porcelain"), "",
                         "the divergence must be invisible to status")

    def _remote_tip(self):
        out = self._git("ls-remote", str(self.remote), "refs/heads/main")
        return out.split("\t")[0] if out else ""

    def _run(self, door, *extra):
        """One author door, end to end, with a gate spy.
        Returns (code, payload, gate_runs, stamped_roles_or_None)."""
        import contextlib
        import io
        import os
        from unittest import mock
        from review import cli, emit as _emit
        ran = []

        def spy(cfg, target_sha):
            ran.append(target_sha)
            return []

        out = self.tmp / "request.md"
        os.chdir(self.repo)
        buf = io.StringIO()
        try:
            with mock.patch.object(_emit, "run_gates", spy):
                with contextlib.redirect_stdout(buf):
                    code = cli.main(["--ledger-dir", str(self.tmp / "state"),
                                     door, "--claim-file", str(self.claim),
                                     "--base", self.base,
                                     "--out", str(out), *extra])
        finally:
            os.chdir(self.cwd)
        payload = json.loads(buf.getvalue())
        stamped = None
        if out.exists():
            attrs = wire.parse_request(
                out.read_text(encoding="utf-8")).attrs
            stamped = (attrs["author"], attrs["reviewer"])
        return code, payload, ran, stamped

    # ------------------------------------------------------------- rows

    def test_matching_defaults_are_the_paired_control(self):
        """The control: checkout and commit agree, so nothing about this
        change may alter the outcome."""
        for door in self.DOORS:
            with self.subTest(door=door):
                self._build()
                code, payload, ran, stamped = self._run(door)
                self.assertEqual(code, 0, payload)
                self.assertEqual(stamped, ("claude", "codex"))
                self.assertEqual(len(ran), 1, "gates must run on a pass")

    def test_different_committed_defaults_are_what_gets_stamped(self):
        """The no-flag divergence row. FAILS under the mutation."""
        for door in self.DOORS:
            with self.subTest(door=door):
                self._build()
                self._diverge(author="codex", reviewer="claude")
                code, payload, _, stamped = self._run(door)
                self.assertEqual(code, 0, payload)
                self.assertEqual(
                    stamped, ("codex", "claude"),
                    "a no-flag emission must stamp the TARGET's defaults, "
                    "not the checkout's")

    def test_a_checkout_invalid_target_valid_default_passes(self):
        """Round 2 F1's first inverse row. The CHECKOUT's permitted list
        excludes the identity the TARGET defaults to and permits. Under the
        preliminary `resolve_roles(cfg, ...)` this refused before the
        target was consulted — a false refusal of valid work, which is the
        inverse of the defect round 1 F2 closed and worse, because it
        blocks rather than mislabels."""
        for door in self.DOORS:
            with self.subTest(door=door):
                self._build()
                # The checkout must be invalid for its OWN DEFAULTS, or
                # the mutation cannot fail this row: with no flags,
                # `resolve_roles` never consults the permitted lists, so a
                # merely narrower permitted list is not a checkout refusal.
                # What DOES bind a default is assignment itself — a
                # checkout that assigns nothing. That is also the realistic
                # case: a repository governed only by a user-level config
                # legitimately assigns no roles in its worktree.
                self._diverge(
                    author="codex", reviewer="claude",
                    permitted_authors=("codex", "claude"),
                    permitted_reviewers=("codex", "claude"),
                    checkout=dict(author="", reviewer="",
                                  permitted_authors=("codex", "claude"),
                                  permitted_reviewers=("codex", "claude")))
                code, payload, _, stamped = self._run(door)
                self.assertEqual(code, 0, payload)
                self.assertEqual(
                    stamped, ("codex", "claude"),
                    "the target permits this pair and it is the only "
                    "authority entitled to say so")

    def test_a_checkout_excluding_target_permitting_flag_passes(self):
        """Round 2 F1's second inverse row, on an EXPLICIT selection."""
        for door in self.DOORS:
            with self.subTest(door=door):
                self._build()
                # Target: defaults claude/claude-reviewer is invalid, so
                # default author claude with reviewer claude would
                # self-review; use claude author / claude? No — the target
                # defaults author=claude reviewer=claude is invalid. Keep
                # the target's reviewer distinct and permit `codex` as an
                # author the CHECKOUT does not.
                self._diverge(
                    author="claude", reviewer="claude",
                    permitted_authors=("codex", "claude"),
                    permitted_reviewers=("claude", "codex"),
                    checkout=dict(author="claude", reviewer="codex",
                                  permitted_authors=("claude",)))
                code, payload, _, stamped = self._run(
                    door, "--author", "codex", "--reviewer", "claude")
                self.assertEqual(code, 0, payload)
                self.assertEqual(
                    stamped, ("codex", "claude"),
                    "the checkout does not permit `codex` as an author and "
                    "is not entitled to refuse it")

    def test_a_defaulted_identity_the_target_rejects_stops_first(self):
        """A DEFAULTED identity the committed authority rejects. The
        checkout would resolve it cleanly; the target would not. Nothing
        may be pushed and no gate may run. FAILS under the mutation."""
        for door in self.DOORS:
            with self.subTest(door=door):
                self._build()
                before = self._remote_tip()
                self._diverge(author="claude", reviewer="codex",
                              rejected=("codex",))
                code, payload, ran, _ = self._run(door)
                self.assertEqual(code, 1, payload)
                self.assertEqual(payload.get("next_kind"), "blocked")
                self.assertIn("codex", payload.get("error", ""))
                self.assertEqual(ran, [], "a gate ran on a refused row")
                self.assertEqual(self._remote_tip(), before,
                                 "the branch was pushed on a refused row")

    def test_an_explicit_target_permitted_flag_is_selected(self):
        for door in self.DOORS:
            with self.subTest(door=door):
                self._build()
                # The target defaults BOTH sides to codex, so the stamped
                # author can only be `claude` if the explicit flag was
                # honoured against the target's permitted list.
                self._diverge(author="codex", reviewer="codex")
                code, payload, _, stamped = self._run(door, "--author",
                                                      "claude")
                self.assertEqual(code, 0, payload)
                self.assertEqual(
                    stamped[0], "claude",
                    "an explicit selection the TARGET permits must stand")

    def test_an_explicit_target_unpermitted_flag_stops_first(self):
        """An EXPLICIT identity the committed authority does not permit,
        selected against a checkout that does. FAILS under the mutation."""
        for door in self.DOORS:
            with self.subTest(door=door):
                self._build()
                before = self._remote_tip()
                self._diverge(author="codex", reviewer="claude",
                              permitted_authors=("codex",))
                code, payload, ran, _ = self._run(door, "--author", "claude")
                self.assertEqual(code, 1, payload)
                self.assertEqual(payload.get("next_kind"), "blocked")
                self.assertEqual(ran, [], "a gate ran on a refused row")
                self.assertEqual(self._remote_tip(), before,
                                 "the branch was pushed on a refused row")


if __name__ == "__main__":
    unittest.main()

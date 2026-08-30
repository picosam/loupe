"""§9bis.3 / RVW-T9: the transport verbs `handoff`, `take`, `close`, and the
lineage scoping in the ledger that `close` depends on.

Decision logic runs through injected git runners and in-memory ledgers, so
every refusal is exercised without a network, a scratch repository or a
writable directory. One integration class runs the whole loop for real —
handoff → take (second clone, fetch over a bare path remote) → verdict →
close → next handoff — because a first real execution is a different
instrument from unit tests (slice-1 lesson 3); it self-skips, with the reason
stated, where filesystem writes are denied.
"""
import contextlib
import dataclasses
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from review import (TOOL_NAME, cli, config, tool_identity,
                    transport,
                    validate, wire)
from review import ledger as ledger_mod
from review.emit import _git
from review.ledger import Ledger
from review.tests.util import REPO_ROOT, spec_path

CFG = config.load(REPO_ROOT)
SHA_A, SHA_B, SHA_C = "a" * 40, "b" * 40, "c" * 40


def fake_git(mapping):
    calls = []

    def run(*args):
        calls.append(args)
        if args not in mapping:
            raise AssertionError(f"unexpected git call: {args}")
        value = mapping[args]
        if isinstance(value, Exception):
            raise value
        return value

    run.calls = calls
    return run



def authority_calls(sha=None):
    """The two reads `resolve_authority` makes about `sha` (lineage 12).

    The warm-cache path resolves the TARGET's authority to decide the role
    key (round 2 F1), so a scripted runner reaching that path has to answer
    them. Kept here rather than repeated at each fixture: a mapping that
    drifts from what the resolver asks is a fixture that tests nothing.
    """
    sha = sha or SHA_B
    return {
        ("ls-tree", "--full-tree", sha, "--", "review.toml"):
            "100644 blob " + "0" * 40 + "\treview.toml",
        ("show", f"{sha}:review.toml"):
            (REPO_ROOT / "review.toml").read_text(encoding="utf-8"),
    }


def request_text(sha=SHA_B, base=SHA_A, reviewer="codex", author="claude",
                 round_no=1, push=True, refs=None, transport_attr=None,
                 tool_attr=None):
    """A structurally valid request (round-2 F4: `take` judges the
    target-independent grammar before any git call, so a unit fixture must
    pass it — risk stated, NOT captured stated, every §5.1 section present
    in order, a reference with a digest). `refs=None` supplies review.toml
    at the digest the TestTake fake git serves; pass "" for none."""
    push_lines = ("Push:   refs/heads/main = %s @ origin "
                  "(ssh://example.invalid/x.git) — ls-remote observed after "
                  "push\nVerify: git fetch ssh://example.invalid/x.git "
                  "refs/heads/main && git cat-file -e %s\n" % (sha, sha)
                  if push else "")
    if refs is None:
        digest = transport.sha256_file(REPO_ROOT / "review.toml")
        refs = f"  review.toml  sha256:{digest}  [required] the config\n"
    # RVW-T11: absent by default, so the fixture keeps exercising what an
    # envelope emitted before the attribute existed does — the topology
    # reader has to read that absence as the default, not as an unknown.
    tr = f' transport="{transport_attr}"' if transport_attr else ""
    # Round 1 F2 put the identity in the warm-cache key, so a fixture that
    # has to REACH the checks past it must stamp one. Absent by default,
    # exactly like the transport attribute above and for the same reason:
    # what an envelope emitted before the field existed does is a state the
    # readers have to keep answering for.
    tl = f' tool="{tool_attr}"' if tool_attr else ""
    return (f'<loupe-review-request sha="{sha}" branch="main" '
            f'author="{author}" reviewer="{reviewer}" round="{round_no}"'
            f'{tr}{tl}>\n'
            f"Roles: author={author} · reviewer={reviewer} · relay=user.\n\n"
            f"Target: {sha}\nBase:   {base}   (the SHA ruled on in round 0)\n"
            f"Diff:   git diff {base}...{sha}\nTree:   clean at emission\n"
            f"{push_lines}"
            f"## Taxonomy\n\nSeverity, ordered:      Blocker > High > Medium "
            f"> Low > Info\nBlocking severities:    Blocker, High\n"
            f"Classification, one of:\n  factual_error\n  "
            f"internal_contradiction\n  design_gap\n  unsupported_claim\n  "
            f"process_defect\n\n## Claim\n\nObjective / decision boundary: x\n"
            f"\nSelf-assessed risk: low (fixture)\n"
            f"\nWhat changed (1 files, 1 insertions, 0 deletions = 1 changed "
            f"lines, 1 areas — machine-computed):\n\n  f.txt\n\n## Evidence\n\n"
            f"```loupe-attestations\n[]\n```\n\nNOT captured — this handoff "
            f"cannot vouch for these:\n  - (none)\n\n## Contract\n\n"
            f"(none declared)\n\n## Reference\n\n{refs}\n\n"
            f"## Review scope\n\nall\n</loupe-review-request>\n")


def verdict_text(sha=SHA_B, verdict="changes requested", findings=1):
    body = f'<loupe-review-verdict sha="{sha}">\nVERDICT: {verdict}\n\n## findings\n\n'
    if verdict == "clean to advance":
        body += "None\n"
    else:
        for i in range(1, findings + 1):
            body += (f"### F{i}\nSeverity: Low\nClassification: design_gap\n"
                     f"Title: finding number {i}\nEvidence: f.txt:1\n"
                     f"Why: because\nRequired outcome: fix it\n"
                     f"FALSIFICATION: observation: it is fixed\n\n")
    body += "## evidence checked\n\nf.txt\n</loupe-review-verdict>\n"
    return body


def lineage_ledger():
    """A ledger with one completed round and a cap override — the state a
    closed lineage leaves behind."""
    ledger = Ledger.in_memory()
    ledger.add({"event": "request", "round": 1, "sha": SHA_A, "bytes": 1})
    ledger.add({"event": "verdict", "round": 1, "sha": SHA_A,
                "verdict": "changes requested", "bytes": 1, "finding_ids": 1})
    ledger.add({"event": "finding", "round": 1, "id": "F1", "fp": "fp2:1",
                "severity": "Low"})
    ledger.add({"event": "cap_override", "round_cap": 5, "reason": "r",
                "authorized_by": "user", "default_cap": 3})
    ledger.add({"event": "lineage", "kind": "alias", "from_fp": "fp1:x",
                "to_fp": "fp2:1"})
    return ledger


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


class TestProbeTarget(unittest.TestCase):
    """FALSIFICATION: each state the reviewer-side probe names refuses
    independently; the happy path fetches, resolves target and base, and
    checks ancestry."""

    PUSH = {"state": "pushed", "ref": "refs/heads/main", "sha": SHA_B,
            "remote": "origin", "url": "ssh://example.invalid/x.git"}

    def happy_map(self, overrides=None):
        m = {("fetch", "ssh://example.invalid/x.git", "refs/heads/main"): "",
             ("cat-file", "-e", f"{SHA_B}^{{commit}}"): "",
             ("cat-file", "-e", f"{SHA_A}^{{commit}}"): "",
             ("merge-base", "--is-ancestor", SHA_A, SHA_B): ""}
        m.update(overrides or {})
        return m

    def test_happy_path_fetches_then_resolves(self):
        git = fake_git(self.happy_map())
        rec = transport.probe_target(CFG, self.PUSH, SHA_B, SHA_A, git=git)
        self.assertEqual(rec["target"], "present")
        self.assertEqual(rec["base"], "ancestor of target")
        self.assertEqual(git.calls[0][0], "fetch")

    def test_no_stamp_refuses(self):
        with self.assertRaises(transport.Refusal):
            transport.probe_target(CFG, None, SHA_B, SHA_A, git=fake_git({}))

    def test_fetch_failure_refuses(self):
        git = fake_git(self.happy_map({
            ("fetch", "ssh://example.invalid/x.git", "refs/heads/main"):
                RuntimeError("git fetch: could not read")}))
        with self.assertRaises(transport.Refusal) as ctx:
            transport.probe_target(CFG, self.PUSH, SHA_B, SHA_A, git=git)
        self.assertIn("cannot fetch", str(ctx.exception))

    def test_target_absent_after_fetch_refuses(self):
        git = fake_git(self.happy_map({
            ("cat-file", "-e", f"{SHA_B}^{{commit}}"): RuntimeError("no")}))
        with self.assertRaises(transport.Refusal) as ctx:
            transport.probe_target(CFG, self.PUSH, SHA_B, SHA_A, git=git)
        self.assertIn("not present", str(ctx.exception))

    def test_base_absent_refuses(self):
        git = fake_git(self.happy_map({
            ("cat-file", "-e", f"{SHA_A}^{{commit}}"): RuntimeError("no")}))
        with self.assertRaises(transport.Refusal) as ctx:
            transport.probe_target(CFG, self.PUSH, SHA_B, SHA_A, git=git)
        self.assertIn("base", str(ctx.exception))

    def test_base_not_ancestor_refuses(self):
        git = fake_git(self.happy_map({
            ("merge-base", "--is-ancestor", SHA_A, SHA_B): RuntimeError("1")}))
        with self.assertRaises(transport.Refusal) as ctx:
            transport.probe_target(CFG, self.PUSH, SHA_B, SHA_A, git=git)
        self.assertIn("not an ancestor", str(ctx.exception))

    def test_no_fetch_skips_the_fetch_but_still_requires_presence(self):
        m = self.happy_map()
        del m[("fetch", "ssh://example.invalid/x.git", "refs/heads/main")]
        git = fake_git(m)
        rec = transport.probe_target(CFG, self.PUSH, SHA_B, SHA_A,
                                     fetch=False, git=git)
        self.assertEqual(rec["fetch"], "skipped (--no-fetch)")
        self.assertNotIn("fetch", [c[0] for c in git.calls])

    def test_local_only_never_fetches(self):
        m = self.happy_map()
        del m[("fetch", "ssh://example.invalid/x.git", "refs/heads/main")]
        git = fake_git(m)
        rec = transport.probe_target(CFG, {"state": "local-only"}, SHA_B,
                                     SHA_A, git=git)
        self.assertIn("LOCAL-ONLY", rec["fetch"])

    def test_local_only_absent_names_the_clone(self):
        git = fake_git({("cat-file", "-e", f"{SHA_B}^{{commit}}"):
                        RuntimeError("no")})
        with self.assertRaises(transport.Refusal) as ctx:
            transport.probe_target(CFG, {"state": "local-only"}, SHA_B,
                                   None, git=git)
        self.assertIn("not the clone", str(ctx.exception))


class TestTake(unittest.TestCase):

    def _cfg(self):
        return dataclasses.replace(CFG, ledger_dir=None)

    def _git(self):
        """The reviewer clone, faked. Sweep F6: `take` reads the target's
        review.toml and every reference from the target tree, so the fake
        serves `show`/`cat-file -t` for the paths these tests reference and
        answers 'absent' (a git failure) for any other path — the same
        answer a real object store gives for a path the commit lacks."""
        toml = (REPO_ROOT / "review.toml").read_text(encoding="utf-8")
        known = {
            ("fetch", "ssh://example.invalid/x.git", "refs/heads/main"): "",
            ("cat-file", "-e", f"{SHA_B}^{{commit}}"): "",
            ("cat-file", "-e", f"{SHA_A}^{{commit}}"): "",
            ("merge-base", "--is-ancestor", SHA_A, SHA_B): "",
            ("ls-tree", "--full-tree", SHA_B, "--", "review.toml"):
                            "100644 blob 0000000\treview.toml",
                        ("show", f"{SHA_B}:review.toml"): toml,
            ("cat-file", "-t", f"{SHA_B}:review.toml"): "blob",
            ("cat-file", "-t", f"{SHA_B}:review/wire.py"): "blob",
            ("show", f"{SHA_B}:review/wire.py"): "not the manifest's bytes",
            ("cat-file", "-t", f"{SHA_B}:review"): "tree",
        }
        inner = fake_git(known)

        def run(*args):
            if args not in known and args[0] in ("cat-file", "show"):
                inner.calls.append(args)
                raise RuntimeError(f"path does not exist in {SHA_B[:7]}")
            return inner(*args)
        run.calls = inner.calls
        return run

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
                             git=git, reviewer="codex")
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
                             reviewer="codex")
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
                       reviewer="codex")
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


class TestSpanReport(unittest.TestCase):
    """Ruled 2026-08-30 (brief review-scope-envelope): the diff shape the
    envelope stamps — and the précis reprints — carries the COMMIT COUNT
    the span sweeps, machine-computed, so an author sees a thirteen-commit
    sweep before a human carries it. Report, never refuse."""

    def setUp(self):
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="span-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.tmp)],
                       check=True, capture_output=True, timeout=60)
        self.shas = []
        for n in range(3):
            (self.tmp / "f.txt").write_text(f"{n}\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(self.tmp), "-c", "user.email=s@example.invalid",
                 "-c", "user.name=s", "add", "-A"],
                check=True, capture_output=True, timeout=60)
            subprocess.run(
                ["git", "-C", str(self.tmp), "-c", "user.email=s@example.invalid",
                 "-c", "user.name=s", "commit", "-qm", f"c{n}"],
                check=True, capture_output=True, timeout=60)
            self.shas.append(_git(self.tmp, "rev-parse", "HEAD"))

    def test_the_shape_counts_the_commits_it_spans(self):
        from review import emit
        two = emit.diff_shape(self.tmp, self.shas[0], self.shas[2])
        self.assertEqual(two["commits"], 2)
        self.assertIn("spanning 2 commits", emit.shape_line(two))
        one = emit.diff_shape(self.tmp, self.shas[1], self.shas[2])
        self.assertEqual(one["commits"], 1)
        self.assertIn("spanning 1 commit", emit.shape_line(one))
        # The validator's machine-readable triple survives the suffix.
        from review.validate import _DIFF_SHAPE_RE
        line = f"What changed ({emit.shape_line(two)} — machine-computed):"
        found = _DIFF_SHAPE_RE.search(line)
        self.assertIsNotNone(found)
        self.assertEqual(tuple(int(x) for x in found.groups()),
                         (two["files"], two["insertions"], two["deletions"]))


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
        try:
            tmp = Path(tempfile.mkdtemp(prefix="handoff-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc}); warm-cache "
                          f"leg runs only in the writable pass")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        cfg = dataclasses.replace(CFG, ledger_dir=tmp)
        text = request_text(tool_attr=tool_identity())
        kept = transport.keep_bytes(cfg, 1, "request", text)
        self.assertTrue(Path(kept).is_file())
        git = fake_git({("rev-parse", "HEAD"): SHA_B,
                        ("status", "--porcelain"): "",
                        **authority_calls()})
        ledger = Ledger.in_memory()
        # A recorded claim state, because round 5 F1 made "neither side says
        # anything" cold: this test is about the KEPT COPY, so it has to get
        # past the claim check to reach its own subject.
        ledger.add({"event": "request", "round": 1, "sha": SHA_B,
                    "source_digest": transport._digest_text(text),
                    "bytes": len(text),
                    "claim_digest": transport.NO_CLAIM})
        warm = transport.cached_handoff(cfg, ledger, 1, git=git,
                                        claim_digest=transport.NO_CLAIM)
        self.assertEqual(warm["envelope"], text)
        # Adjacent: the kept copy altered → cold, never a stale envelope.
        Path(kept).write_text(text + "tampered\n", encoding="utf-8")
        self.assertIsNone(transport.cached_handoff(
            cfg, ledger, 1, git=git, claim_digest=transport.NO_CLAIM))

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


class TestFullLoopIntegration(unittest.TestCase):
    """The first-real-execution instrument: two clones, a bare path remote,
    no network. handoff in the author clone → take in the reviewer clone
    (fetching over the stamped path) → verdict → close → the next handoff
    → lineage close → round 1 again with the repo default cap."""

    def setUp(self):
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="loop-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc}): the loop "
                          f"integration runs only in the writable pass")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.remote = self.tmp / "remote.git"
        self.author = self.tmp / "author"
        self.reviewer = self.tmp / "reviewer"
        # The bare remote names its initial branch too. Without `-b`, HEAD
        # takes git's built-in default (`master`); the author pushes `main`,
        # so the remote's HEAD points at a branch that never exists and
        # `git clone` below yields an EMPTY working tree — no review.toml,
        # so `take` refuses with T-UNDECLARED. Invisible on a machine whose
        # system gitconfig sets init.defaultBranch=main (Apple's git does);
        # the first CI run of this suite (2026-08-17) found it.
        self._sh("git", "init", "-q", "--bare", "-b", "main",
                 str(self.remote))
        self._sh("git", "init", "-q", "-b", "main", str(self.author))
        for k, v in (("user.name", "a"), ("user.email", "a@example.invalid"),
                     ("commit.gpgsign", "false")):
            self._sh("git", "-C", str(self.author), "config", k, v)
        toml = (REPO_ROOT / "review.toml").read_text(encoding="utf-8")
        toml = toml[:toml.index("[[gates]]")] + toml[toml.index("[roles]"):]
        (self.author / "review.toml").write_text(toml, encoding="utf-8")
        (self.author / "f.txt").write_text("one\n", encoding="utf-8")
        self._sh("git", "-C", str(self.author), "add", ".")
        self._sh("git", "-C", str(self.author), "commit", "-q", "-m", "init")
        self.base = _git(self.author, "rev-parse", "HEAD")
        (self.author / "f.txt").write_text("two\n", encoding="utf-8")
        self._sh("git", "-C", str(self.author), "commit", "-qam", "change")
        self._sh("git", "-C", str(self.author), "remote", "add", "origin",
                 str(self.remote))
        self._sh("git", "-C", str(self.author), "push", "-q", "-u", "origin",
                 "main")
        self._sh("git", "clone", "-q", str(self.remote), str(self.reviewer))
        self.claim = self.tmp / "claim.json"
        self.claim.write_text(json.dumps({
            "objective": "loop test",
            "references": [{"path": "review.toml", "required": True}]}),
            encoding="utf-8")
        self.author_state = self.tmp / "state-author"
        self.reviewer_state = self.tmp / "state-reviewer"
        self.cwd = os.getcwd()
        self.addCleanup(os.chdir, self.cwd)

    def _sh(self, *args):
        subprocess.run(args, check=True, capture_output=True, text=True,
                       timeout=60)

    def _run(self, where, state, *argv):
        os.chdir(where)
        from io import StringIO
        import contextlib
        buf = StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["--ledger-dir", str(state), *argv])
        os.chdir(self.cwd)
        out = buf.getvalue()
        try:
            return code, json.loads(out)
        except json.JSONDecodeError:
            return code, out

    def test_the_loop_end_to_end(self):
        head = _git(self.author, "rev-parse", "HEAD")
        # 1. handoff (author): emits round 1, records, keeps.
        code, rec = self._run(self.author, self.author_state, "handoff",
                              "--claim-file", str(self.claim),
                              "--base", self.base)
        self.assertEqual(code, 0, rec)
        self.assertEqual(rec["sha"], head)
        self.assertFalse(rec["cached"])
        self.assertTrue(Path(rec["kept"]).is_file())
        # 1b. handoff again on the unchanged tip: cached, gates not re-run.
        code, again = self._run(self.author, self.author_state, "handoff",
                                "--claim-file", str(self.claim),
                                "--base", self.base)
        self.assertEqual(code, 0, again)
        self.assertTrue(again["cached"])
        self.assertEqual(again["digest"], rec["digest"])
        # 2. take (reviewer clone): fetches over the stamped path remote.
        #    --as is mandatory since round 1 F4 — the loop no longer runs on a
        #    defaulted identity, so the real path must declare one too.
        code, taken = self._run(self.reviewer, self.reviewer_state, "take",
                                rec["kept"], "--as", "codex")
        self.assertEqual(code, 0, taken)
        self.assertEqual(taken["sha"], head)
        self.assertIn("fetched", taken["target"]["fetch"])
        self.assertEqual(taken["target"]["target"], "present")
        self.assertTrue(taken["references"][0]["status"].startswith("checked"))
        self.assertIn(f"diff {self.base}...{head}", taken["diff"])
        # 2b. taking it as the wrong identity refuses (exit 1, next given).
        code, wrong = self._run(self.reviewer, self.reviewer_state, "take",
                                rec["kept"], "--as", "claude")
        self.assertEqual(code, 1)
        self.assertIn("next", wrong)
        # 3. verdict → close (author): round 1 closed, lineage open.
        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head), encoding="utf-8")
        code, closed = self._run(self.author, self.author_state, "close",
                                 "--verdict", str(v1))
        self.assertEqual(code, 0, closed)
        self.assertEqual(closed["round"], 1)
        self.assertEqual(closed["lineage"], "open")
        # 3b. respond --out (author): the disposition is recorded and kept
        #     without a manual ledger add; the round-2 request then carries it.
        d_json = self.tmp / "d.json"
        d_json.write_text(json.dumps({
            "head": head, "round": 1, "author": "claude",
            "dispositions": [{"finding_id": "F1", "disposition": "accepted",
                              "payload": {"change": "fixed", "verification":
                                          "observed",
                                          "falsification": {
                                              "status": "pass",
                                              "mutation": "fails_without_fix"}
                                          }}]}), encoding="utf-8")
        d_md = self.tmp / "d.md"
        code, resp = self._run(self.author, self.author_state, "respond",
                               "--verdict", str(v1), "--from-json",
                               str(d_json), "--out", str(d_md))
        self.assertEqual(code, 0, resp)
        # The disposition AND its falsification run: the second event is the
        # product-path writer the `stale`/`unverifiable` breakers never had —
        # before it, the only `falsification_run` events in existence were
        # seeded by hand inside test_breakers.
        self.assertEqual(resp["events_added"], 2)
        self.assertTrue(Path(resp["kept"]).is_file())
        runs = [e for e in Ledger(self.author_state).events()
                if e.get("event") == "falsification_run"]
        self.assertEqual(len(runs), 1, runs)
        self.assertEqual(runs[0]["status"], "pass")
        self.assertEqual(runs[0]["mutation"], "fails_without_fix")
        self.assertEqual(runs[0]["source"], "disposition")
        self.assertEqual(runs[0]["round"], 1)
        # 4. next handoff opens round 2 (same tip; not cached, new round).
        code, r2 = self._run(self.author, self.author_state, "handoff",
                             "--claim-file", str(self.claim))
        self.assertEqual(code, 0, r2)
        self.assertEqual(r2["round"], 2)
        self.assertFalse(r2["cached"])
        r2_text = Path(r2["kept"]).read_text(encoding="utf-8")
        self.assertIn("**accepted**", r2_text)
        # ...and the run record beside it, so the reviewer rules on it.
        self.assertIn("[falsification: pass; mutation: fails_without_fix]",
                      r2_text)
        # 5. close the lineage by decision; the next handoff is round 1
        #    of lineage 2 and needs an explicit base again.
        code, lin = self._run(self.author, self.author_state, "close",
                              "--lineage", "--reason", "loop test done",
                              "--by", "user")
        self.assertEqual(code, 0, lin)
        self.assertTrue(lin["open_request"])
        self.assertEqual(lin["next_lineage"], 2)
        code, r1b = self._run(self.author, self.author_state, "handoff",
                              "--claim-file", str(self.claim),
                              "--base", self.base)
        self.assertEqual(code, 0, r1b)
        self.assertEqual(r1b["round"], 1)
        # The ledger tells the story in order.
        ledger = Ledger(self.author_state)
        kinds = [e["event"] for e in ledger.events()]
        self.assertEqual(kinds.count("request"), 3)
        self.assertEqual(kinds.count("verdict"), 1)
        self.assertEqual(kinds.count("disposition"), 1)
        self.assertEqual(kinds.count(Ledger.LINEAGE_CLOSED), 1)
        self.assertEqual(ledger.lineage_number(), 2)

    def test_handoff_refuses_when_response_is_omitted(self):
        """FALSIFICATION for sweep F4 (Blocker). The loop, with the author's
        answer skipped: handoff → take → verdict → close → handoff. The
        second handoff must refuse — no request event, no kept envelope, no
        gate run — until every finding of the round-1 verdict has its
        disposition. Mutation: reintroduce the verdict-only transition (call
        `next_round` without the preflight — remove `handoff_preflight`
        from `cmd_handoff`) and the second handoff opens round 2 with an
        empty disposition ledger; this fails on the first assertNotEqual.
        Two controls: the same handoff after `respond` succeeds, and a
        clean verdict — which closes the lineage — is followed by a round-1
        handoff that owes nothing."""
        head = _git(self.author, "rev-parse", "HEAD")
        code, rec = self._run(self.author, self.author_state, "handoff",
                              "--claim-file", str(self.claim),
                              "--base", self.base)
        self.assertEqual(code, 0, rec)
        code, taken = self._run(self.reviewer, self.reviewer_state, "take",
                                rec["kept"], "--as", "codex")
        self.assertEqual(code, 0, taken)
        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head, findings=2), encoding="utf-8")
        code, closed = self._run(self.author, self.author_state, "close",
                                 "--verdict", str(v1))
        self.assertEqual(code, 0, closed)
        before = Ledger(self.author_state).events()
        # The illegal transition: hand off with no response recorded.
        code, refused = self._run(self.author, self.author_state, "handoff",
                                  "--claim-file", str(self.claim))
        self.assertNotEqual(code, 0, refused)
        self.assertEqual(refused["next_kind"], "blocked")
        self.assertIsNone(refused["next"])
        self.assertIn("2 of 2", refused["error"])
        self.assertEqual(Ledger(self.author_state).events(), before,
                         "a refused handoff appends nothing")
        self.assertFalse((Path(self.author_state) / "exchange"
                          / "round-2-request.md").exists())
        # A PARTIAL answer is still owed: one of two.
        d_json = self.tmp / "d.json"
        one = {"head": head, "author": "claude",
               "dispositions": [{"finding_id": "F1", "disposition": "accepted",
                                 "payload": {"change": "fixed",
                                             "verification": "observed",
                                             "falsification": {
                                                 "status": "pass",
                                                 "mutation":
                                                     "fails_without_fix"}}}]}
        d_json.write_text(json.dumps(one), encoding="utf-8")
        code, resp = self._run(self.author, self.author_state, "respond",
                               "--verdict", str(v1), "--from-json",
                               str(d_json), "--out", str(self.tmp / "d.md"))
        # respond itself refuses an incomplete answer (D-INCOMPLETE): the
        # preflight is the second wall, for the case where respond was never
        # run at all — which is exactly what the first refusal above proved.
        self.assertNotEqual(code, 0, resp)
        # Control 1: the complete answer, then the handoff opens round 2.
        two = dict(one)
        two["dispositions"] = one["dispositions"] + [
            dict(one["dispositions"][0], finding_id="F2")]
        d_json.write_text(json.dumps(two), encoding="utf-8")
        code, resp = self._run(self.author, self.author_state, "respond",
                               "--verdict", str(v1), "--from-json",
                               str(d_json), "--out", str(self.tmp / "d.md"))
        self.assertEqual(code, 0, resp)
        code, r2 = self._run(self.author, self.author_state, "handoff",
                             "--claim-file", str(self.claim))
        self.assertEqual(code, 0, r2)
        self.assertEqual(r2["round"], 2)
        # Control 2: a clean verdict closes the lineage; round 1 of the next
        # lineage owes no disposition and opens.
        v2 = self.tmp / "v2.md"
        v2.write_text(verdict_text(sha=head, verdict="clean to advance"),
                      encoding="utf-8")
        code, closed = self._run(self.author, self.author_state, "close",
                                 "--verdict", str(v2))
        self.assertEqual(code, 0, closed)
        code, r1b = self._run(self.author, self.author_state, "handoff",
                              "--claim-file", str(self.claim),
                              "--base", self.base)
        self.assertEqual(code, 0, r1b)
        self.assertEqual(r1b["round"], 1)

    def test_take_recovers_from_an_empty_reviewer_checkout(self):
        """FALSIFICATION for sweep F6 (High). The reviewer's clone has an
        unborn HEAD and an empty working tree — the exact state the first
        CI run produced when the bare remote's default branch never
        existed. `take` must fetch the stamped target, read the target's
        own review.toml, validate against it, check every reference from
        the target tree, and record the take. Mutation: restore pre-fetch
        validation against the reviewer's local config (validate before
        `probe_target`, with `cfg` instead of `target_config`) and this
        empty checkout refuses with T-UNDECLARED — the first assertEqual
        fails. Control: the ordinary clone validates against the target's
        config too, and says so."""
        head = _git(self.author, "rev-parse", "HEAD")
        code, rec = self._run(self.author, self.author_state, "handoff",
                              "--claim-file", str(self.claim),
                              "--base", self.base)
        self.assertEqual(code, 0, rec)
        # An empty reviewer repository: initialised, nothing checked out,
        # no review.toml anywhere in it.
        empty = self.tmp / "reviewer-empty"
        self._sh("git", "init", "-q", "-b", "main", str(empty))
        self.assertEqual(sorted(x.name for x in empty.iterdir()), [".git"])
        empty_state = self.tmp / "state-empty"
        code, taken = self._run(empty, empty_state, "take", rec["kept"],
                                "--as", "codex")
        self.assertEqual(code, 0, taken)
        self.assertEqual(taken["sha"], head)
        self.assertIn("fetched", taken["target"]["fetch"])
        self.assertTrue(taken["target"]["config"].startswith(
            "target review.toml at"), taken["target"]["config"])
        self.assertTrue(taken["references"][0]["status"].startswith(
            "checked"), taken["references"])
        kinds = [e["event"] for e in Ledger(empty_state).events()]
        self.assertIn("take", kinds)
        # And the working tree was left exactly as found: empty.
        self.assertEqual(sorted(x.name for x in empty.iterdir()), [".git"])
        # Control: the ordinary clone, whose checkout DOES carry the same
        # review.toml, is validated against the target's copy as well.
        code, taken2 = self._run(self.reviewer, self.reviewer_state, "take",
                                 rec["kept"], "--as", "codex")
        self.assertEqual(code, 0, taken2)
        self.assertTrue(taken2["target"]["config"].startswith(
            "target review.toml at"))
        # A target that carries no review.toml is governed by the checkout,
        # and the record says so rather than pretending otherwise. Round 3
        # F2: absence is the answer to `ls-tree`, not the answer to a failed
        # read — a target whose config cannot be READ is a refusal, and the
        # sibling rows for that live in TestAuthorityOriginIsClassified.
        rec2, origin = transport.resolve_authority(
            dataclasses.replace(CFG, ledger_dir=None), "0" * 40,
            git=lambda *a: "")
        self.assertEqual(origin, transport.AUTHORITY_EXTERNAL)
        self.assertIn("carries no review.toml", rec2.source)
        self.assertEqual(rec2.taxonomy, CFG.taxonomy)


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


if __name__ == "__main__":
    unittest.main()


def _cli(fn, cfg, **kw):
    """Run one CLI command function with a Namespace built from `kw`,
    returning (exit code, parsed JSON payload)."""
    import argparse
    import contextlib
    import io
    args = argparse.Namespace(**kw)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = fn(args, cfg)
    out = buf.getvalue()
    try:
        return code, json.loads(out)
    except json.JSONDecodeError:
        return code, out


class TestLedgerAddRequest(unittest.TestCase):
    """Sweep F3 (Blocker): the manual ingestion door validates a request.

    `ledger add` validated verdicts and dispositions and recorded a request
    on sight: a wrapped envelope carrying the single word `malformed` became
    a round's request event with exit 0. The ledger is the identity
    authority every later lifecycle decision reads, so an invalid request
    filed there is not a bad row — it is a round that exists.
    """

    def setUp(self):
        from review.tests import synth
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="ledger-add-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.cfg = dataclasses.replace(synth.NO_GATES, ledger_dir=self.tmp)
        self.synth = synth

    def _add(self, text, round_no=None):
        path = self.tmp / "req.md"
        path.write_text(text, encoding="utf-8")
        return _cli(cli.cmd_ledger_add, self.cfg, envelope=str(path),
                    round=round_no, tokens=None, ledger_dir=str(self.tmp))

    def test_invalid_request_records_nothing(self):
        """FALSIFICATION for F3. Mutation: remove the `validate_request`
        call from the request branch of `cmd_ledger_add` and the malformed
        envelope is recorded with exit 0 — this fails on the first
        assertion; the valid control below is unaffected by that mutation
        and keeps passing, which is what makes the two halves a pair."""
        malformed = ('<loupe-review-request sha="%s" branch="main" '
                     'author="claude" reviewer="codex" round="1">\n'
                     'malformed\n</loupe-review-request>\n' % SHA_B)
        code, payload = self._add(malformed)
        self.assertNotEqual(code, 0, payload)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["items"], "the refusal names what is wrong")
        self.assertEqual(Ledger(self.tmp).events(), [],
                         "a refused request appends nothing — not a request "
                         "event, not a round")
        # Valid control: the real emitter's request ingests, and records the
        # SAME shape the other two doors record — the request event and its
        # evidence events, through the one function they share.
        good = self.synth.emitted_request()
        code, payload = self._add(good)
        self.assertEqual(code, 0, payload)
        ledger = Ledger(self.tmp)
        parsed = wire.parse_request(good)
        expected = transport.request_events(
            parsed, int(parsed.attrs["round"]), transport._digest_text(good),
            len(good.encode("utf-8")))
        self.assertEqual(payload["events_added"], len(expected))
        kinds = [e["event"] for e in ledger.events()]
        # Lineage-7 round 2 F2: this door is a cross-installation READER of a
        # stamped envelope, so it also records WHO read it and whether the
        # two installations agree. That row belongs to the read, not to the
        # envelope, which is why it is not in `request_events`.
        self.assertEqual(kinds, [e["event"] for e in expected] + ["ingest"])
        ingest = ledger.events()[-1]
        self.assertEqual(ingest["kind"], "request")
        self.assertIn(ingest["tool_agreement"], ("match", "differs",
                                                 "unstamped"))
        # And parity is idempotence: filing the same envelope through the
        # normal path afterwards is a no-op, never a duplicate.
        self.assertEqual(ledger.add_all(expected), 0)

    def test_the_cap_in_force_is_advisory_at_this_door_too(self):
        """The cap reads the same at every door — and since 2026-08-25 it
        advises at every one of them rather than refusing. This door still
        RECORDS the request, because a round past the cap is a round that
        happened; what it no longer does is pretend the count is a defect."""
        import re
        text = re.sub(r'round="\d+"', 'round="9"',
                      self.synth.emitted_request(), count=1)
        code, payload = self._add(text)
        self.assertEqual(code, 0, payload)
        recorded = [e["event"] for e in Ledger(self.tmp).events()]
        self.assertIn("request", recorded,
                      "the round past the cap was not recorded: the cap is "
                      "advisory, so the record must still hold what happened")


class TestResponseLifecycle(unittest.TestCase):
    """Sweep F2 (Blocker): a response answers a valid, recorded verdict.

    `respond --out` parsed whatever verdict file it was handed, built and
    validated the disposition against it, wrote it and appended its events
    — with no verdict validation and no lookup in the ledger. An unwrapped
    legacy verdict that fails validation and was never recorded produced
    disposition events, fingerprints and falsification runs at a
    caller-supplied SHA, exit 0. The resolution boundary `ledger add`
    already used for a standalone disposition (`answered_verdict`) is now
    one function both doors call: exactly one recorded verdict, current
    lineage, the just-closed round, round and SHA derived from it.
    """

    RUN = {"status": "pass", "mutation": "fails_without_fix"}

    def setUp(self):
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="respond-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.cfg = dataclasses.replace(CFG, ledger_dir=self.tmp)

    def _respond(self, verdict, out=True, **data):
        vpath = self.tmp / "v.md"
        vpath.write_text(verdict, encoding="utf-8")
        payload = {"head": SHA_C, "author": "claude",
                   "dispositions": [{"finding_id": "F1",
                                     "disposition": "accepted",
                                     "payload": {"change": "x",
                                                 "verification": "y",
                                                 "falsification": self.RUN}}]}
        payload.update(data)
        jpath = self.tmp / "d.json"
        jpath.write_text(json.dumps(payload), encoding="utf-8")
        opath = self.tmp / "disposition.md"
        return _cli(cli.cmd_respond, self.cfg, verdict=str(vpath),
                    from_json=str(jpath), out=str(opath) if out else None,
                    ledger_dir=str(self.tmp)), opath

    def _dispositions(self):
        return [e for e in Ledger(self.tmp).events()
                if e.get("event") == "disposition"]

    def _record(self, round_no, sha):
        """A request and its verdict, filed the way the product files them."""
        ledger = Ledger(self.tmp)
        ledger.add({"event": "request", "round": round_no, "sha": sha,
                    "bytes": 1, "author": "claude", "reviewer": "codex"})
        text = verdict_text(sha=sha)
        transport.close_round(
            self.cfg, ledger, text, "v.md",
            validate_items=lambda v: validate.validate_verdict(v, self.cfg))
        return text

    def test_unvalidated_or_unrecorded_verdict_cannot_record_a_response(self):
        """FALSIFICATION for F2. Two mutations, each fails one half: remove
        the `validate_verdict` call in `cmd_respond` and the legacy verdict
        is answered (half 1 fails); remove the `recorded_verdict` resolution
        and the unrecorded-but-valid verdict is answered (half 2 fails). The
        valid control closes a recorded verdict, then responds."""
        # Half 1: an unwrapped legacy verdict fails validation; nothing is
        # written, nothing appended, whatever the JSON says.
        (code, payload), out = self._respond(self.LEGACY, verdict_sha=SHA_B,
                                             round=1)
        self.assertNotEqual(code, 0, payload)
        self.assertIn("V-WRAPPER", {i["code"] for i in payload["items"]})
        self.assertFalse(out.exists())
        self.assertEqual(self._dispositions(), [])
        # Half 2: a valid verdict that the ledger never recorded.
        (code, payload), out = self._respond(verdict_text(sha=SHA_B))
        self.assertNotEqual(code, 0, payload)
        self.assertEqual(payload["next_kind"], "blocked")
        self.assertFalse(out.exists())
        self.assertEqual(self._dispositions(), [])
        # Valid control: request → close --verdict → respond --out. Round and
        # SHA are derived from the record, not from the JSON.
        text = self._record(1, SHA_B)
        (code, payload), out = self._respond(text)
        self.assertEqual(code, 0, payload)
        self.assertTrue(out.exists())
        events = self._dispositions()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["round"], 1)
        written = wire.parse_disposition(out.read_text(encoding="utf-8"))
        self.assertEqual(written.attrs["verdict_sha"], SHA_B)
        self.assertEqual(int(written.data["round"]), 1)

    def test_a_supplied_round_or_sha_may_only_agree(self):
        text = self._record(1, SHA_B)
        (code, payload), out = self._respond(text, round=2)
        self.assertNotEqual(code, 0)
        self.assertEqual(payload["next_kind"], "blocked")
        self.assertEqual(self._dispositions(), [])
        (code, payload), _ = self._respond(text, verdict_sha=SHA_A)
        self.assertNotEqual(code, 0)
        self.assertEqual(self._dispositions(), [])
        (code, payload), _ = self._respond(text, round=1, verdict_sha=SHA_B)
        self.assertEqual(code, 0, payload)

    def test_only_the_just_closed_round_is_answerable(self):
        first = self._record(1, SHA_B)
        self._record(2, SHA_C)
        # Round 1's verdict is recorded and valid, and superseded.
        (code, payload), out = self._respond(first)
        self.assertNotEqual(code, 0, payload)
        self.assertFalse(out.exists())
        self.assertEqual(self._dispositions(), [])

    def test_a_closed_lineage_s_verdict_is_not_answerable(self):
        text = self._record(1, SHA_B)
        transport.close_lineage(Ledger(self.tmp), "done", "user")
        (code, payload), out = self._respond(text)
        self.assertNotEqual(code, 0, payload)
        self.assertEqual(self._dispositions(), [])

    LEGACY = ("VERDICT: changes requested\n\n### F1\nSeverity: Low\n"
              "Classification: design_gap\nTitle: t\nEvidence: f.txt:1\n"
              "Why: w\nRequired outcome: r\n"
              "FALSIFICATION: observation: o\n")

    def test_without_out_the_verdict_is_still_validated(self):
        # Rendering mode resolves nothing in the ledger, so verdict
        # validation is the only guard here — and the legacy verdict
        # carries F1, so the disposition against it would validate: what
        # refuses is the verdict, and only the verdict.
        (code, payload), _ = self._respond(self.LEGACY, out=False,
                                           verdict_sha=SHA_B, round=1)
        self.assertNotEqual(code, 0, payload)
        self.assertIn("V-WRAPPER", {i["code"] for i in payload["items"]})
        self.assertEqual(self._dispositions(), [])

    def test_conflicting_verdicts_in_one_round_are_ambiguous(self):
        """FALSIFICATION for round-2 F1 (Blocker). Two different valid
        verdicts for one round: the second must be refused at every door
        that files verdicts (`ledger add --round`, `close`), and if the
        ledger nonetheless holds two, no response may select one by digest.
        Mutation: restore digest-filter-before-uniqueness in
        recorded_verdict (and drop verdict_conflict) — the second ingestion
        exits 0 and `respond --out` against the first exits 0 and writes a
        disposition; both assertions fail. Control: a single recorded
        verdict still responds."""
        first = self._record(1, SHA_B)
        # A second, different, valid verdict for the same request/round.
        second = verdict_text(sha=SHA_B, findings=2)
        self.assertNotEqual(first, second)
        vpath = self.tmp / "second.md"
        vpath.write_text(second, encoding="utf-8")
        code, payload = _cli(cli.cmd_ledger_add, self.cfg,
                             envelope=str(vpath), round=1, tokens=None,
                             ledger_dir=str(self.tmp))
        self.assertNotEqual(code, 0, payload)
        self.assertEqual(payload["next_kind"], "blocked")
        verdicts = [e for e in Ledger(self.tmp).events()
                    if e.get("event") == "verdict"]
        self.assertEqual(len(verdicts), 1, "a round is ruled once")
        # `close` refuses it the same way (the round is no longer open,
        # and even with the round derived it is a second ruling).
        with self.assertRaises(transport.Refusal):
            transport.close_round(self.cfg, Ledger(self.tmp), second, "v.md",
                                  validate_items=lambda v:
                                  validate.validate_verdict(v, self.cfg))
        # And if two rulings DID sit in one round (seeded, since no door
        # will file them now), the boundary refuses both rather than
        # letting the author pick by file.
        led = Ledger(self.tmp)
        led.add_all(transport.verdict_events(
            wire.parse_verdict(second), 1, transport._digest_text(second),
            len(second.encode("utf-8"))))
        self.assertEqual(len([e for e in Ledger(self.tmp).events()
                              if e.get("event") == "verdict"]), 2)
        for text in (first, second):
            (code, payload), out = self._respond(text)
            self.assertNotEqual(code, 0, payload)
            self.assertEqual(payload["next_kind"], "blocked")
            self.assertFalse(out.exists())
        self.assertEqual(self._dispositions(), [])
        self.assertIsNone(transport.recorded_verdict(
            Ledger(self.tmp), digest=transport._digest_text(first)))
        # Control: one recorded verdict, one response.
        clean = dataclasses.replace(self.cfg,
                                    ledger_dir=self.tmp / "control")
        (self.tmp / "control").mkdir()
        led = Ledger(clean.ledger_dir)
        led.add({"event": "request", "round": 1, "sha": SHA_B, "bytes": 1,
                 "author": "claude", "reviewer": "codex"})
        transport.close_round(clean, led, first, "v.md",
                              validate_items=lambda v:
                              validate.validate_verdict(v, clean))
        self.assertIsNotNone(transport.recorded_verdict(
            Ledger(clean.ledger_dir), digest=transport._digest_text(first)))

    def test_ledger_add_shares_the_boundary(self):
        # The standalone door: a disposition for a superseded round is
        # refused by the same rule, and appends nothing.
        first = self._record(1, SHA_B)
        # A disposition rendered (not recorded) against the round-1 verdict.
        (code, payload), _ = self._respond(first, out=False, round=1,
                                           verdict_sha=SHA_B)
        self.assertEqual(code, 0, payload)
        rendered = payload if isinstance(payload, str) else ""
        self.assertTrue(rendered.startswith("<loupe-review-disposition"))
        self._record(2, SHA_C)
        dpath = self.tmp / "standalone.md"
        dpath.write_text(rendered, encoding="utf-8")
        code, payload = _cli(cli.cmd_ledger_add, self.cfg, envelope=str(dpath),
                             round=None, tokens=None,
                             ledger_dir=str(self.tmp))
        self.assertNotEqual(code, 0, payload)
        self.assertEqual(self._dispositions(), [])


class TestEvidenceIngestion(unittest.TestCase):
    """Round 1 F6 (Medium), falsification.

    The design says the ledger carries evidence digests and exempts a
    refuted-then-returning finding when the new round cites new
    content-addressed evidence. Ingestion recorded only the whole-envelope
    digest, so on the product path the exemption was unreachable: the breaker
    could observe the loop but never the escape from it.
    """

    def _cfg(self):
        return dataclasses.replace(CFG, ledger_dir=None)

    def _verdict_with_evidence(self, evidence, sha=SHA_B, citations=""):
        cite_line = f"Citations: {citations}\n" if citations else ""
        body = (f'<loupe-review-verdict sha="{sha}">\n'
                f"VERDICT: changes requested\n\n## findings\n\n"
                f"### F1\nSeverity: Low\nClassification: design_gap\n"
                f"Title: the same finding, returning\nEvidence: {evidence}\n"
                f"Why: because\nRequired outcome: fix it\n"
                f"FALSIFICATION: observation: it is fixed\n{cite_line}\n"
                f"## evidence checked\n\nf.txt\n</loupe-review-verdict>\n")
        return wire.parse_verdict(body)

    def _two_rounds(self, r2_refs, evidence="proof.md:1 the original read",
                    citations=""):
        """Raise a finding, refute it, and let it return in round 2.

        Round 2's REQUEST carries `r2_refs`; the finding's own prose is
        identical across both rounds, so the only thing that can excuse the
        repetition is the reference bytes. Returns the breakers that fired.
        """
        ledger = Ledger.in_memory()
        v1 = self._verdict_with_evidence(evidence, citations=citations)
        ledger.add({"event": "request", "round": 1, "sha": SHA_B, "bytes": 1})
        ledger.add_all(transport.evidence_events(wire.parse_request(
            request_text(refs=f"  proof.md  sha256:{'0' * 64}  [required] x\n")
        ), 1))
        ledger.add_all(transport.verdict_events(v1, 1, "d1", 1))
        fp = v1.findings[0].fingerprint()
        ledger.add({"event": "disposition", "round": 1, "fp": fp,
                    "finding_id": "F1", "disposition": "refuted",
                    "payload": {"evidence": "the read is correct"}})

        ledger.add({"event": "request", "round": 2, "sha": SHA_C, "bytes": 1})
        ledger.add_all(transport.evidence_events(
            wire.parse_request(request_text(sha=SHA_C, round_no=2,
                                            refs=r2_refs)), 2))
        v2 = self._verdict_with_evidence(evidence, sha=SHA_C,
                                         citations=citations)
        self.assertEqual(v2.findings[0].fingerprint(), fp,
                         "the identity must survive, or nothing is returning")
        ledger.add_all(transport.verdict_events(v2, 2, "d2", 1))
        return [b["breaker"] for b in ledger.breakers(round_cap=5)]

    def test_new_reference_digest_is_bound_to_returning_fingerprint(self):
        """Round 2 F6 (Medium), falsification.

        Round 1 recorded reference digests; round 2 found that nothing could
        use them. The breaker exempts a returning finding backed by new
        content-addressed evidence, but it asked for evidence carrying the
        finding's fingerprint, and a reference line carries a path — so the
        author could put genuinely new bytes under a file the finding cites
        and still be told the loop was spinning. The round-2 probe is the
        first case here: same finding prose, `proof.md` moved from one digest
        to another, repetition fired anyway.

        The three cases together are what make the binding meaningful rather
        than merely permissive.
        """
        # 1. New bytes under a path the finding cites: the exemption applies.
        fired = self._two_rounds(
            f"  proof.md  sha256:{'1' * 64}  [required] x\n")
        self.assertNotIn("repetition", fired,
                         "new bytes under a cited path are new evidence for "
                         "the finding that cites it")

        # 2. The control the exemption needs, or it excuses everything: new
        #    bytes under a path this finding does NOT cite prove nothing
        #    about it.
        fired = self._two_rounds(
            f"  unrelated.md  sha256:{'1' * 64}  [required] x\n")
        self.assertIn("repetition", fired,
                      "bytes under an uncited path are not this finding's "
                      "evidence; a join that accepted them would exempt "
                      "every returning finding from any change anywhere")

        # 3. The pointer/bytes distinction F6 names explicitly: the SAME
        #    bytes at a changed pointer are not new material, even when the
        #    finding cites the new pointer.
        fired = self._two_rounds(
            f"  moved/proof.md  sha256:{'0' * 64}  [required] x\n",
            evidence="proof.md:1 and moved/proof.md:1 the original read")
        self.assertIn("repetition", fired,
                      "relocating unchanged bytes is not new evidence")

    def test_duplicate_digest_preserves_every_cited_path_edge(self):
        """Round 3 F5 (Medium), falsification.

        De-duplication keyed on the digest alone, which answered the wrong
        question with the right key. The digest decides whether bytes are
        NEW; the path decides which findings those bytes can speak FOR. So
        when two references carried identical new bytes and the uncited one
        happened to be listed first, the cited one's event was discarded and
        the join vanished — reference ORDER decided whether a refutation was
        falsely escalated. Both orders are asserted here, because a fix that
        only works when the cited path comes first is the same bug with a
        friendlier fixture.
        """
        shared = "1" * 64
        for first, second in (("unrelated.md", "proof.md"),
                              ("proof.md", "unrelated.md")):
            with self.subTest(order=f"{first} then {second}"):
                fired = self._two_rounds(
                    f"  {first}  sha256:{shared}  [required] x\n"
                    f"  {second}  sha256:{shared}  [required] x\n")
                self.assertNotIn(
                    "repetition", fired,
                    "new bytes exist under the cited path whichever order "
                    "the references are listed in")

        # And the events themselves carry both edges, not just the first.
        parsed = wire.parse_request(request_text(
            refs=f"  unrelated.md  sha256:{shared}  [required] x\n"
                 f"  proof.md  sha256:{shared}  [required] x\n"))
        events = transport.evidence_events(parsed, 1)
        self.assertEqual([e["of"] for e in events],
                         ["unrelated.md", "proof.md"])
        self.assertEqual({e["digest"] for e in events}, {shared})

        # The negative control still holds: identical bytes under paths the
        # finding does NOT cite prove nothing about it.
        fired = self._two_rounds(
            f"  unrelated.md  sha256:{shared}  [required] x\n"
            f"  also-unrelated.md  sha256:{shared}  [required] x\n")
        self.assertIn("repetition", fired)

    def test_the_join_is_exact_not_fuzzy(self):
        # A path that merely LOOKS like the cited one must not bind. If it
        # did, the exemption would be a similarity test on filenames.
        fired = self._two_rounds(
            f"  vendor/proof.md  sha256:{'1' * 64}  [required] x\n")
        self.assertIn("repetition", fired,
                      "a shared basename is not the same file")

    def test_a_declared_citation_binds_as_well_as_evidence_prose(self):
        # Citations is the declared half of `cited_paths`; a finding whose
        # Evidence never spells the path still binds through it.
        fired = self._two_rounds(
            f"  proof.md  sha256:{'1' * 64}  [required] x\n",
            evidence="the read is wrong, with no path token at all",
            citations="proof.md, public/docs/design.md")
        self.assertNotIn("repetition", fired)

    def test_a_request_records_its_reference_digests(self):
        refs = f"  a.md  sha256:{'0' * 64}  [required] x\n"
        parsed = wire.parse_request(request_text(refs=refs))
        events = transport.evidence_events(parsed, 1)
        self.assertEqual([e["digest"] for e in events], ["0" * 64])
        self.assertEqual(events[0]["source"], "reference")

    def test_identical_bytes_at_a_new_pointer_are_not_new_evidence(self):
        # The property F6 names explicitly: keying on the digest, not the
        # path, so relocating gate output is not a claim of new material.
        first = wire.parse_request(request_text(
            refs=f"  a.md  sha256:{'0' * 64}  [required] x\n"))
        moved = wire.parse_request(request_text(
            refs=f"  moved/a.md  sha256:{'0' * 64}  [required] x\n"))
        self.assertEqual(
            {e["digest"] for e in transport.evidence_events(first, 1)},
            {e["digest"] for e in transport.evidence_events(moved, 2)})

    def test_new_finding_evidence_prevents_false_repetition(self):
        # Named for what it actually exercises. Round 2 F6 caught it carrying
        # the word "reference" while creating no reference events at all — it
        # changes the reviewer's own Evidence prose, which is the OTHER half
        # of the exemption. The reference half is the test above.
        ledger = Ledger.in_memory()
        cfg = self._cfg()

        # Round 1: the finding is raised, then refuted by the author.
        v1 = self._verdict_with_evidence("transport.py:1 the original read")
        ledger.add({"event": "request", "round": 1, "sha": SHA_B, "bytes": 1})
        ledger.add_all(transport.verdict_events(v1, 1, "d1", 1))
        fp = v1.findings[0].fingerprint()
        ledger.add({"event": "disposition", "round": 1, "fp": fp,
                    "finding_id": "F1", "disposition": "refuted",
                    "payload": {"evidence": "the read is correct"}})

        # Round 2: the SAME finding returns, citing genuinely new evidence.
        ledger.add({"event": "request", "round": 2, "sha": SHA_C, "bytes": 1})
        v2 = self._verdict_with_evidence("transport.py:99 a second call site",
                                         sha=SHA_C)
        self.assertEqual(v2.findings[0].fingerprint(), fp,
                         "identity must survive new evidence, or the "
                         "exemption could never apply to anything")
        ledger.add_all(transport.verdict_events(v2, 2, "d2", 1))

        fired = [b["breaker"] for b in ledger.breakers(round_cap=5)]
        self.assertNotIn("repetition", fired,
                         "new content-addressed evidence is the documented "
                         "exemption; it must be observable")

    def test_repeating_the_same_evidence_still_fires(self):
        # The control. Without it the test above would pass on a breaker that
        # simply never fires.
        ledger = Ledger.in_memory()
        same = "transport.py:1 the original read"
        v1 = self._verdict_with_evidence(same)
        ledger.add({"event": "request", "round": 1, "sha": SHA_B, "bytes": 1})
        ledger.add_all(transport.verdict_events(v1, 1, "d1", 1))
        ledger.add({"event": "disposition", "round": 1,
                    "fp": v1.findings[0].fingerprint(), "finding_id": "F1",
                    "disposition": "refuted",
                    "payload": {"evidence": "the read is correct"}})
        ledger.add({"event": "request", "round": 2, "sha": SHA_C, "bytes": 1})
        ledger.add_all(transport.verdict_events(
            self._verdict_with_evidence(same, sha=SHA_C), 2, "d2", 1))
        fired = [b["breaker"] for b in ledger.breakers(round_cap=5)]
        self.assertIn("repetition", fired)


class TestVerdictEvents(unittest.TestCase):
    """Round 1 F7 (Medium), falsification."""

    def _verdict(self, gate_line=""):
        body = ('<loupe-review-verdict sha="%s">\n'
                "VERDICT: changes requested\n\n## findings\n\n"
                "### F1\nSeverity: Low\nClassification: design_gap\n"
                "Title: a gate would have caught this\nEvidence: f.txt:1\n"
                "Why: because\nRequired outcome: fix it\n"
                "%sFALSIFICATION: observation: it is fixed\n\n"
                "## evidence checked\n\nf.txt\n</loupe-review-verdict>\n"
                % (SHA_B, gate_line))
        return wire.parse_verdict(body)

    def test_preventable_gate_reaches_metrics(self):
        # Before: transport hardcoded preventable_by=None, so the label never
        # left the verdict and the metric reported a measured zero.
        v = self._verdict("Preventable-by: tests\n")
        self.assertEqual(v.findings[0].preventable_by, "tests")
        events = transport.verdict_events(v, 1, "d", 1)
        finding = next(e for e in events if e["event"] == "finding")
        self.assertEqual(finding["preventable_by"], "tests")

        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": SHA_B, "bytes": 1})
        ledger.add_all(events)
        metric = ledger.metrics(gate_manifest=["tests", "whitespace"])
        preventable = metric["rounds"][1]["deterministic_preventable"]
        self.assertEqual(preventable["count"], 1)
        self.assertEqual(preventable["gates"], ["tests"])

    def test_no_label_reports_not_captured_rather_than_zero(self):
        # The honest-uncomputable half: absent is not zero.
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": SHA_B, "bytes": 1})
        ledger.add_all(transport.verdict_events(self._verdict(), 1, "d", 1))
        metric = ledger.metrics(gate_manifest=["tests"])
        preventable = metric["rounds"][1]["deterministic_preventable"]
        self.assertIsNone(preventable["count"])
        self.assertIn("not captured", preventable["share"])

    def test_partial_labels_report_not_captured(self):
        """Round 2 F7 (Medium), falsification.

        Round 1 taught the metric to say "not captured" when NO finding
        carried a gate label. It kept dividing by the full finding count the
        moment one did — so with two findings and one label the report read
        `1/2 (50%)`, quietly asserting that the unlabelled finding had been
        measured and found not preventable. Absent is not zero, and a
        denominator with an unmeasured member is not a denominator.
        """
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": SHA_B, "bytes": 1})
        ledger.add({"event": "verdict", "round": 1, "sha": SHA_B,
                    "verdict": "changes requested", "finding_ids": 2})
        ledger.add({"event": "finding", "round": 1, "id": "F1", "fp": "fp2:1",
                    "severity": "High", "preventable_by": "tests"})
        ledger.add({"event": "finding", "round": 1, "id": "F2", "fp": "fp2:2",
                    "severity": "Low", "preventable_by": None})

        metric = ledger.metrics(gate_manifest=["tests", "whitespace"])
        preventable = metric["rounds"][1]["deterministic_preventable"]
        self.assertIsNone(preventable["count"],
                          "the round-2 probe read 1/2 (50%) here")
        self.assertNotIn("%", preventable["share"],
                         "a percentage claims a denominator that was never "
                         "measured")
        self.assertIn("partially captured", preventable["share"])
        self.assertEqual(preventable["labelled"], 1)

        # The control, on its own ledger because this one is append-only:
        # when EVERY finding carries a label the share is real and is
        # reported. The rule narrows the claim; it does not refuse to count.
        complete = Ledger.in_memory()
        complete.add({"event": "request", "round": 1, "sha": SHA_B,
                      "bytes": 1})
        complete.add({"event": "verdict", "round": 1, "sha": SHA_B,
                      "verdict": "changes requested", "finding_ids": 2})
        complete.add({"event": "finding", "round": 1, "id": "F1",
                      "fp": "fp2:1", "severity": "High",
                      "preventable_by": "tests"})
        complete.add({"event": "finding", "round": 1, "id": "F2",
                      "fp": "fp2:2", "severity": "Low",
                      "preventable_by": "whitespace"})
        full = (complete.metrics(gate_manifest=["tests", "whitespace"])
                ["rounds"][1]["deterministic_preventable"])
        self.assertEqual(full["count"], 2)
        self.assertIn("100%", full["share"])

    def test_an_undeclared_gate_id_fails_validation(self):
        items = validate.validate_verdict(
            self._verdict("Preventable-by: no-such-gate\n"), CFG)
        self.assertIn("V-PREVENTABLE-GATE",
                      {i.code for i in items if i.level == "error"})


class TestRepeatedShaResolution(unittest.TestCase):
    """Round 2 F1 (High), falsification.

    `round_for_sha` returned the first historical match, so a SHA bound by two
    rounds resolved to the earlier one. A verdict then closed a round that was
    already answered and skipped the later round's dispositions entirely.
    """

    def _cfg(self):
        return dataclasses.replace(CFG, ledger_dir=None)

    def _ledger(self):
        led = Ledger.in_memory()
        led.add({"event": "request", "round": 1, "sha": SHA_B, "bytes": 1,
                 "author": "claude", "reviewer": "codex"})
        return led

    def test_a_ruled_round_is_not_the_answer_to_a_new_verdict(self):
        led = self._ledger()
        led.add({"event": "verdict", "round": 1, "sha": SHA_B})
        led.add({"event": "request", "round": 2, "sha": SHA_B, "bytes": 1,
                 "author": "claude", "reviewer": "codex"})
        # The same commit reviewed twice — a re-review on an unchanged tip.
        self.assertEqual(led.rounds_for_sha(SHA_B), [1, 2])
        self.assertEqual(led.round_for_sha(SHA_B), 2,
                         "the open round is the one awaiting an answer")

    def test_close_files_the_verdict_against_the_open_round(self):
        led = self._ledger()
        led.add({"event": "verdict", "round": 1, "sha": SHA_B})
        led.add({"event": "request", "round": 2, "sha": SHA_B, "bytes": 1,
                 "author": "claude", "reviewer": "codex"})
        rec = transport.close_round(self._cfg(), led, verdict_text(), "v.md")
        self.assertEqual(rec["round"], 2)

    def test_two_open_rounds_on_one_sha_refuse_rather_than_guess(self):
        led = self._ledger()
        led.add({"event": "request", "round": 2, "sha": SHA_B, "bytes": 1,
                 "author": "claude", "reviewer": "codex"})
        self.assertEqual(led.ambiguous_rounds_for_sha(SHA_B), [1, 2])
        before = len(led.events())
        with self.assertRaises(transport.Refusal) as ctx:
            transport.close_round(self._cfg(), led, verdict_text(), "v.md")
        self.assertIn("more than one OPEN request", str(ctx.exception))
        self.assertEqual(len(led.events()), before)

    def test_a_superseded_emission_is_not_ambiguity(self):
        # Re-emitting the SAME round twice is the common case and must still
        # resolve: two request events, one round.
        led = self._ledger()
        led.add({"event": "request", "round": 1, "sha": SHA_B, "bytes": 2,
                 "author": "claude", "reviewer": "codex"})
        self.assertEqual(led.ambiguous_rounds_for_sha(SHA_B), [])
        self.assertEqual(led.round_for_sha(SHA_B), 1)


class TestDispositionsSupersedeByRecency(unittest.TestCase):
    """Lineage 6 round 2: the standing answer is the newest disposition
    event per fingerprint, and re-emission supersedes instead of
    dead-ending the lineage.

    The live incident this closes: a disposition was recorded, the head
    then legitimately moved (a regenerated-artifact commit landed after
    the record), and the re-emitted disposition made every fingerprint
    "answered more than once" — a state with no legal exit, since the
    ledger may not be edited and no decision verb covered it. The rule is
    now the one `recorded_transport` already follows: recency decides,
    history stays.
    """

    FP = "fp2:0011223344556677"

    def _round1(self, ledger):
        ledger.add({"event": "request", "round": 1, "sha": SHA_B, "bytes": 1,
                    "author": "claude", "reviewer": "codex"})
        ledger.add({"event": "verdict", "round": 1, "sha": SHA_B,
                    "verdict": "changes requested", "bytes": 1,
                    "finding_ids": 1})
        ledger.add({"event": "finding", "round": 1, "id": "F1",
                    "fp": self.FP, "severity": "High",
                    "classification": "design_gap", "title": "t",
                    "preventable_by": None})
        return ledger

    def _disposition(self, head):
        return {"event": "disposition", "round": 1, "finding_id": "F1",
                "fp": self.FP, "disposition": "accepted",
                "payload": {"change": "c", "verification": "v"},
                "verdict_sha": SHA_B, "head": head}

    def test_an_unanswered_finding_still_refuses(self):
        # The control that keeps the preflight a door: omission is owed.
        ledger = self._round1(Ledger.in_memory())
        owed = transport.missing_dispositions(ledger)
        self.assertIsNotNone(owed)
        self.assertEqual([u["fp"] for u in owed["unanswered"]], [self.FP])

    def test_one_answer_satisfies(self):
        ledger = self._round1(Ledger.in_memory())
        ledger.add(self._disposition("a" * 40))
        self.assertIsNone(transport.missing_dispositions(ledger))

    def test_a_rebound_answer_supersedes_instead_of_dead_ending(self):
        # The incident, reproduced: two batches for the same fingerprint,
        # differing head. The newest is the standing answer; the preflight
        # passes; both events remain in the append-only file.
        ledger = self._round1(Ledger.in_memory())
        ledger.add(self._disposition("a" * 40))
        ledger.add(self._disposition("b" * 40))
        self.assertIsNone(transport.missing_dispositions(ledger))
        standing = ledger.standing_dispositions(round_no=1)
        self.assertEqual(len(standing), 1)
        self.assertEqual(standing[0]["head"], "b" * 40)
        kept = [e for e in ledger.events()
                if e.get("event") == "disposition"]
        self.assertEqual(len(kept), 2)

    def test_a_returning_finding_is_answered_per_round(self):
        # Cross-round control: recency supersedes WITHIN a round only. A
        # finding that returns in round 2 owes a fresh answer there, and
        # the round-1 answer stays standing for round 1.
        ledger = self._round1(Ledger.in_memory())
        ledger.add(self._disposition("a" * 40))
        two = dict(self._disposition("c" * 40), round=2)
        ledger.add({"event": "request", "round": 2, "sha": SHA_A, "bytes": 1,
                    "author": "claude", "reviewer": "codex"})
        ledger.add(two)
        self.assertEqual(len(ledger.standing_dispositions()), 2)
        self.assertEqual(len(ledger.standing_dispositions(round_no=1)), 1)
        self.assertEqual(
            ledger.standing_dispositions(round_no=1)[0]["head"], "a" * 40)


class TestDispositionSupersessionLifecycle(unittest.TestCase):
    """Round 2 F1: supersession is lifecycle-WIDE, not row-local.

    One recorded answer is up to three events (disposition, evidence,
    falsification_run), and the standing answer supersedes them as ONE unit
    — the batch. The complete consumer set reads the batch projection:
    missing-disposition preflight, verdict-closure validation
    (`standing_dispositions`), breaker evaluation, the reviewer-facing
    rendering, and per-round metrics. Raw events stay in the file as audit
    history. Every test here fails if a consumer is reverted to raw event
    reads or a companion is detached from its batch.
    """

    FP = "fp2:0011223344556677"
    FP_OLD = "fp1:8899aabbccddeeff"

    def _round1(self, ledger, severity="High"):
        ledger.add({"event": "request", "round": 1, "sha": SHA_B, "bytes": 1,
                    "author": "claude", "reviewer": "codex"})
        ledger.add({"event": "verdict", "round": 1, "sha": SHA_B,
                    "verdict": "changes requested", "bytes": 1,
                    "finding_ids": 1})
        ledger.add({"event": "finding", "round": 1, "id": "F1",
                    "fp": self.FP, "severity": severity,
                    "classification": "design_gap", "title": "t",
                    "falsification": "run the test",
                    "preventable_by": None})
        return ledger

    def _emission(self, head, status="pass", mutation="fails_without_fix",
                  disposition="accepted", evidence="", round_no=1,
                  batch=None, fp=None):
        """The three events one `respond` emission records, with the shared
        batch stamp the emitter writes (transport.disposition_events)."""
        fp = fp or self.FP
        batch = batch or f"batch-{head[:8]}-{status}"
        events = [{"event": "disposition", "round": round_no,
                   "finding_id": "F1", "fp": fp,
                   "disposition": disposition, "payload": {},
                   "verdict_sha": SHA_B, "head": head, "batch": batch}]
        if evidence:
            events.append({"event": "evidence", "round": round_no,
                           "fp": fp, "digest": f"d-{evidence}",
                           "source": "disposition", "of": "F1",
                           "batch": batch})
        if disposition == "accepted" and status:
            events.append({"event": "falsification_run", "round": round_no,
                           "fp": fp, "of": "F1", "status": status,
                           "mutation": mutation, "test_digest": "td",
                           "source": "disposition", "blocking": True,
                           "batch": batch})
        return events

    # ------------------------------------------------------------ the batch

    def test_one_answer_is_one_standing_batch(self):
        ledger = self._round1(Ledger.in_memory())
        ledger.add_all(self._emission("a" * 40, evidence="e1"))
        batches = ledger.standing_disposition_batches(round_no=1)
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]["disposition"]["head"], "a" * 40)
        self.assertEqual(len(batches[0]["evidence"]), 1)
        self.assertEqual(len(batches[0]["runs"]), 1)
        self.assertIsNone(transport.missing_dispositions(ledger))

    def test_idempotent_replay_is_a_no_op(self):
        # The same envelope re-recorded: same batch stamp, same uids.
        ledger = self._round1(Ledger.in_memory())
        events = self._emission("a" * 40, evidence="e1")
        self.assertEqual(ledger.add_all(events), 3)
        self.assertEqual(ledger.add_all(events), 0)
        self.assertEqual(len(ledger.standing_disposition_batches(1)), 1)

    def test_a_rebind_with_changed_head_supersedes_the_whole_batch(self):
        # The falsification of the finding: an accepted `cannot_execute`
        # answer, then a newer accepted `pass` answer. The newer head
        # stands AND the superseded run no longer fires `unverifiable`.
        ledger = self._round1(Ledger.in_memory())
        ledger.add_all(self._emission("b" * 40, status="cannot_execute",
                                      mutation="not_run"))
        ledger.add_all(self._emission("c" * 40, status="pass"))
        self.assertEqual(ledger.standing_dispositions(1)[0]["head"], "c" * 40)
        fired = ledger.breakers(3, blocking_severities=["High"])
        self.assertFalse(
            [b for b in fired if b["breaker"] == "unverifiable"],
            "a superseded emission's run fired a breaker: a consumer is "
            "reading raw falsification_run events instead of the standing "
            "batch")
        # Both emissions stay in the file — audit history, never erased.
        raw = [e for e in ledger.events()
               if e.get("event") == "falsification_run"]
        self.assertEqual(len(raw), 2)

    def test_a_standing_cannot_execute_still_fires(self):
        # The paired control that proves the breaker is alive: when the
        # NEWEST answer is the unexecutable one, it fires.
        ledger = self._round1(Ledger.in_memory())
        ledger.add_all(self._emission("b" * 40, status="pass"))
        ledger.add_all(self._emission("c" * 40, status="cannot_execute",
                                      mutation="not_run"))
        fired = ledger.breakers(3, blocking_severities=["High"])
        self.assertTrue([b for b in fired
                         if b["breaker"] == "unverifiable"])

    def test_companions_bind_by_batch_stamp_not_by_adjacency(self):
        # A companion stamped for batch A joins batch A even when recorded
        # after batch B's row — detaching a companion from its batch (or
        # loosening the stamp match to "newest row wins") fails here.
        ledger = self._round1(Ledger.in_memory())
        ledger.add({"event": "disposition", "round": 1, "finding_id": "F1",
                    "fp": self.FP, "disposition": "accepted", "payload": {},
                    "verdict_sha": SHA_B, "head": "b" * 40, "batch": "A"})
        ledger.add({"event": "disposition", "round": 1, "finding_id": "F1",
                    "fp": self.FP, "disposition": "accepted", "payload": {},
                    "verdict_sha": SHA_B, "head": "c" * 40, "batch": "B"})
        ledger.add({"event": "falsification_run", "round": 1, "fp": self.FP,
                    "of": "F1", "status": "cannot_execute",
                    "mutation": "not_run", "test_digest": "td",
                    "source": "disposition", "blocking": True, "batch": "A"})
        batches = {b["disposition"]["batch"]: b
                   for b in ledger.disposition_batches(1)
                   if b["disposition"]}
        self.assertEqual([r["status"] for r in batches["A"]["runs"]],
                         ["cannot_execute"])
        self.assertEqual(batches["A"]["standing"], False)
        self.assertEqual(batches["B"]["runs"], [])
        self.assertEqual(batches["B"]["standing"], True)
        # And the consumer proof: the run belongs to superseded A, so it
        # does not fire even though it is the newest run event in the file.
        fired = ledger.breakers(3, blocking_severities=["High"])
        self.assertFalse([b for b in fired
                         if b["breaker"] == "unverifiable"])

    def test_legacy_events_without_stamps_bind_by_emission_order(self):
        # Events recorded before the batch stamp existed: a companion binds
        # to the nearest preceding row for its identity, which is the order
        # the one emitter has always written.
        ledger = self._round1(Ledger.in_memory())
        for head, status in (("b" * 40, "cannot_execute"), ("c" * 40, "pass")):
            ledger.add({"event": "disposition", "round": 1,
                        "finding_id": "F1", "fp": self.FP,
                        "disposition": "accepted", "payload": {},
                        "verdict_sha": SHA_B, "head": head})
            ledger.add({"event": "falsification_run", "round": 1,
                        "fp": self.FP, "of": "F1", "status": status,
                        "mutation": "not_run", "test_digest": "td",
                        "source": "disposition", "blocking": True})
        standing = ledger.standing_disposition_batches(1)
        self.assertEqual(len(standing), 1)
        self.assertEqual([r["status"] for r in standing[0]["runs"]], ["pass"])
        fired = ledger.breakers(3, blocking_severities=["High"])
        self.assertFalse([b for b in fired
                          if b["breaker"] == "unverifiable"])

    def test_an_unstamped_run_binds_to_the_row_by_legacy_order(self):
        # Round 3 F1 corrected this test's own label: this run is NOT an
        # orphan — an unstamped disposition row precedes it, so the
        # legacy-order rule binds them, and the standing batch's
        # cannot_execute reaches the unverifiable breaker. The genuinely
        # rowless states live in TestOrphanCompanionsNeverStand.
        ledger = self._round1(Ledger.in_memory())
        ledger.add({"event": "disposition", "round": 1, "finding_id": "F1",
                    "fp": self.FP, "disposition": "accepted", "payload": {},
                    "verdict_sha": SHA_B, "head": "a" * 40})
        ledger.add({"event": "falsification_run", "round": 1, "fp": self.FP,
                    "of": "F1", "status": "cannot_execute",
                    "mutation": "not_run", "test_digest": "td",
                    "source": "disposition", "blocking": True})
        fired = ledger.breakers(3, blocking_severities=["High"])
        self.assertTrue([b for b in fired
                         if b["breaker"] == "unverifiable"])

    # ----------------------------------------------------------- consumers

    def test_every_disposition_kind_stands_once_in_metrics(self):
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": SHA_B, "bytes": 1,
                    "author": "claude", "reviewer": "codex"})
        ledger.add({"event": "verdict", "round": 1, "sha": SHA_B,
                    "verdict": "changes requested", "bytes": 1,
                    "finding_ids": 5})
        kinds = ["accepted", "refuted", "deferred", "preference", "escalated"]
        for i, kind in enumerate(kinds, 1):
            fp = f"fp2:{i:016x}"
            ledger.add({"event": "finding", "round": 1, "id": f"F{i}",
                        "fp": fp, "severity": "Low",
                        "classification": "design_gap", "title": "t",
                        "preventable_by": None})
            # Each answered twice — a re-bind — and each must count ONCE.
            for head in ("a" * 40, "b" * 40):
                ledger.add({"event": "disposition", "round": 1,
                            "finding_id": f"F{i}", "fp": fp,
                            "disposition": kind, "payload": {},
                            "verdict_sha": SHA_B, "head": head,
                            "batch": f"{kind}-{head[:4]}"})
        counts = ledger.metrics()["rounds"][1]["dispositions"]
        self.assertEqual(counts, {k: 1 for k in kinds},
                         "a superseded disposition row was counted: metrics "
                         "are reading raw events instead of standing batches")

    def test_metrics_attribute_answers_to_their_own_round(self):
        # The two-round probe from the verdict: a returning fingerprint's
        # answers must not be reported under both rounds.
        ledger = Ledger.in_memory()
        for r, sha, disp in ((1, SHA_A, "accepted"), (2, SHA_B, "refuted")):
            ledger.add({"event": "request", "round": r, "sha": sha,
                        "bytes": 1, "author": "claude", "reviewer": "codex"})
            ledger.add({"event": "verdict", "round": r, "sha": sha,
                        "verdict": "changes requested", "bytes": 1,
                        "finding_ids": 1})
            ledger.add({"event": "finding", "round": r, "id": "F1",
                        "fp": self.FP, "severity": "High",
                        "classification": "design_gap", "title": "t",
                        "preventable_by": None})
            ledger.add({"event": "disposition", "round": r,
                        "finding_id": "F1", "fp": self.FP,
                        "disposition": disp, "payload": {},
                        "verdict_sha": sha, "head": sha})
        rounds = ledger.metrics()["rounds"]
        self.assertEqual(rounds[1]["dispositions"], {"accepted": 1})
        self.assertEqual(rounds[2]["dispositions"], {"refuted": 1})

    def test_acceptance_state_reads_the_standing_run(self):
        # Every accepted falsification status/mutation combination, each
        # behind a superseded contrary emission, so a consumer reverted to
        # raw runs reports the stale state and fails.
        cases = [
            (("pass", "fails_without_fix"), "test passed, mutation proven"),
            (("pass", "not_run"), "test passed, mutation not run"),
            (("cannot_execute", "not_run"), "test could not be executed"),
            (("fail", "not_run"), "recorded fail/not_run"),
            (("pass", "passes_without_fix"), "recorded pass/passes_without_fix"),
        ]
        for (status, mutation), expected in cases:
            with self.subTest(status=status, mutation=mutation):
                ledger = self._round1(Ledger.in_memory())
                stale = ("pass" if status != "pass" else "cannot_execute")
                ledger.add_all(self._emission("b" * 40, status=stale,
                                              mutation="not_run"))
                ledger.add_all(self._emission("c" * 40, status=status,
                                              mutation=mutation))
                states = ledger.metrics()["rounds"][1][
                    "unverified_acceptance"]
                self.assertEqual(states, {expected: 1})

    def test_acceptance_with_no_standing_run_is_the_named_absence(self):
        # A superseded emission HAD a run; the standing one has none. The
        # rigor leak must be reported, not papered over by the stale run.
        ledger = self._round1(Ledger.in_memory())
        ledger.add_all(self._emission("b" * 40, status="pass"))
        ledger.add_all(self._emission("c" * 40, status=None))
        states = ledger.metrics()["rounds"][1]["unverified_acceptance"]
        self.assertEqual(states, {"named test, no run recorded": 1})

    def test_superseded_refutation_evidence_is_history(self):
        # The batch projection keeps each emission's evidence with its
        # emission; only the standing batch's evidence reaches consumers.
        ledger = self._round1(Ledger.in_memory())
        ledger.add_all(self._emission("b" * 40, disposition="refuted",
                                      status=None, evidence="old"))
        ledger.add_all(self._emission("c" * 40, disposition="refuted",
                                      status=None, evidence="new"))
        standing = ledger.standing_disposition_batches(1)
        self.assertEqual([e["digest"] for e in standing[0]["evidence"]],
                         ["d-new"])
        all_batches = ledger.disposition_batches(1)
        superseded = [b for b in all_batches if not b["standing"]]
        self.assertEqual([e["digest"] for b in superseded
                          for e in b["evidence"]], ["d-old"])

    def test_aliased_fingerprints_supersede_as_one_identity(self):
        from review.fingerprint import alias_event
        ledger = self._round1(Ledger.in_memory())
        ledger.add(alias_event(self.FP_OLD, self.FP))
        ledger.add_all(self._emission("b" * 40, fp=self.FP_OLD,
                                      status="cannot_execute",
                                      mutation="not_run"))
        ledger.add_all(self._emission("c" * 40, fp=self.FP, status="pass"))
        standing = ledger.standing_disposition_batches(1)
        self.assertEqual(len(standing), 1)
        self.assertEqual(standing[0]["disposition"]["head"], "c" * 40)
        fired = ledger.breakers(3, blocking_severities=["High"])
        self.assertFalse([b for b in fired
                          if b["breaker"] == "unverifiable"])

    def test_a_returning_fingerprint_owes_a_fresh_answer(self):
        # Preflight sentinel: the round-2 return of an identity answered in
        # round 1 is unanswered until round 2 answers it, however many
        # round-1 emissions exist.
        ledger = self._round1(Ledger.in_memory())
        ledger.add_all(self._emission("a" * 40))
        ledger.add_all(self._emission("b" * 40))
        ledger.add({"event": "request", "round": 2, "sha": SHA_A, "bytes": 1,
                    "author": "claude", "reviewer": "codex"})
        ledger.add({"event": "verdict", "round": 2, "sha": SHA_A,
                    "verdict": "changes requested", "bytes": 1,
                    "finding_ids": 1})
        ledger.add({"event": "finding", "round": 2, "id": "F1",
                    "fp": self.FP, "severity": "High",
                    "classification": "design_gap", "title": "t",
                    "preventable_by": None})
        owed = transport.missing_dispositions(ledger)
        self.assertIsNotNone(owed)
        self.assertEqual(owed["round"], 2)

    def test_the_reviewer_sees_one_standing_answer_marked(self):
        # The live round-2 request printed each answer three times. The
        # reviewer-facing block renders the standing answer once and marks
        # how many earlier emissions it supersedes.
        from review.emit import _dispositions_block
        ledger = self._round1(Ledger.in_memory())
        for head in ("a" * 40, "b" * 40, "c" * 40):
            ledger.add_all(self._emission(head))
        block = _dispositions_block(ledger, 1)
        self.assertEqual(block.count("**accepted**"), 1,
                         "a superseded disposition rendered as co-standing")
        self.assertIn("supersedes 2 earlier emission(s)", block)

    def test_the_emitter_stamps_one_batch_per_emission(self):
        # The product writer: every event of one disposition_events call
        # carries the same batch stamp, and the stamp is deterministic so
        # replay dedups.
        against = wire.parse_verdict(verdict_text(findings=1))
        disp = {
            "round": 1, "verdict_sha": SHA_B, "head": SHA_C,
            "dispositions": [{
                "finding_id": "F1", "disposition": "accepted",
                "payload": {"change": "c", "verification": "v",
                            "evidence": "seen it",
                            "falsification": {"status": "pass",
                                              "mutation":
                                                  "fails_without_fix"}},
            }]}
        parsed = _FakeDisposition(disp)
        events = transport.disposition_events(parsed, against)
        stamps = {e["batch"] for e in events}
        self.assertEqual(len(events), 3)
        self.assertEqual(len(stamps), 1)
        self.assertEqual(
            stamps, {e["batch"]
                     for e in transport.disposition_events(parsed, against)})


class TestOrphanCompanionsNeverStand(unittest.TestCase):
    """Round 3 F1: an unmatched companion is an ORPHAN — kept, visible on
    its own audit surface, never standing, certifying nothing and lending
    its run to no other emission. The ordering domain the seam admits is
    closed here: recognized and unknown batch stamps before and after
    disposition rows, truly rowless stamped and unstamped companions,
    stamped events beside legacy rows, aliases, and the later repair (a
    row completing the emission its companions already stamp). Each
    bypass is paired with a valid control proving the projection still
    reads live evidence, and the two named mutations — re-inserting the
    orphan as standing, and collapsing runs onto (fingerprint, round) —
    each fail a test below.
    """

    FP = TestDispositionSupersessionLifecycle.FP
    FP_OLD = TestDispositionSupersessionLifecycle.FP_OLD
    # Borrowed, not inherited: subclassing would re-run that whole suite
    # under this name (the TestTake precedent).
    _round1 = TestDispositionSupersessionLifecycle._round1
    _emission = TestDispositionSupersessionLifecycle._emission

    def _states(self, ledger):
        return (ledger.standing_disposition_batches(1),
                ledger.orphan_companion_batches(1),
                [b["breaker"] for b in ledger.breakers(
                    3, blocking_severities=["High"])])

    def test_an_unknown_stamp_after_the_row_cannot_certify_the_answer(self):
        # The exact-target probe of the finding: an accepted `answer-A`
        # with no run, then a pass/fails_without_fix run stamped
        # `orphan-X`. The old projection returned both as standing and
        # metrics certified answer-A with the foreign run.
        ledger = self._round1(Ledger.in_memory())
        ledger.add_all(self._emission("b" * 40, status=None,
                                      batch="answer-A"))
        ledger.add({"event": "falsification_run", "round": 1, "fp": self.FP,
                    "of": "F1", "status": "pass",
                    "mutation": "fails_without_fix", "test_digest": "td",
                    "source": "disposition", "blocking": True,
                    "batch": "orphan-X"})
        standing, orphans, breakers = self._states(ledger)
        self.assertEqual(len(standing), 1,
                         "an orphan stands beside the answer: the round-3 "
                         "F1 insertion is back")
        self.assertEqual(standing[0]["disposition"]["batch"], "answer-A")
        self.assertEqual(standing[0]["runs"], [],
                         "a foreign run was attributed to the answer")
        self.assertEqual(len(orphans), 1)
        self.assertEqual([r["batch"] for r in orphans[0]["runs"]],
                         ["orphan-X"])
        self.assertEqual(
            ledger.metrics()["rounds"][1]["unverified_acceptance"],
            {"named test, no run recorded": 1},
            "the (fingerprint, round) run collapse is back: an orphan's "
            "run certified an acceptance it never verified")
        self.assertIn("orphan", breakers)
        self.assertNotIn("unverifiable", breakers)

    def test_the_batchs_own_run_still_certifies_it(self):
        # The paired valid control: the same shape with the run stamped
        # for its own emission is a proven acceptance and no anomaly.
        ledger = self._round1(Ledger.in_memory())
        ledger.add_all(self._emission("b" * 40, batch="answer-A"))
        standing, orphans, breakers = self._states(ledger)
        self.assertEqual(len(standing), 1)
        self.assertEqual(len(standing[0]["runs"]), 1)
        self.assertEqual(orphans, [])
        self.assertEqual(
            ledger.metrics()["rounds"][1]["unverified_acceptance"],
            {"test passed, mutation proven": 1})
        self.assertNotIn("orphan", breakers)

    def test_an_unknown_stamp_before_any_row_is_an_orphan(self):
        ledger = self._round1(Ledger.in_memory())
        ledger.add({"event": "falsification_run", "round": 1, "fp": self.FP,
                    "of": "F1", "status": "pass",
                    "mutation": "fails_without_fix", "test_digest": "td",
                    "source": "disposition", "blocking": True,
                    "batch": "orphan-X"})
        ledger.add_all(self._emission("b" * 40, status=None,
                                      batch="answer-A"))
        standing, orphans, breakers = self._states(ledger)
        self.assertEqual(len(standing), 1)
        self.assertEqual(standing[0]["runs"], [])
        self.assertEqual(len(orphans), 1)
        self.assertIn("orphan", breakers)

    def test_a_recognized_stamp_before_its_row_is_one_repaired_batch(self):
        # The later repair: the row completes the emission its companion
        # already stamps — one batch, standing, no anomaly.
        ledger = self._round1(Ledger.in_memory())
        ledger.add({"event": "falsification_run", "round": 1, "fp": self.FP,
                    "of": "F1", "status": "pass",
                    "mutation": "fails_without_fix", "test_digest": "td",
                    "source": "disposition", "blocking": True,
                    "batch": "answer-A"})
        ledger.add_all(self._emission("b" * 40, status=None,
                                      batch="answer-A"))
        standing, orphans, breakers = self._states(ledger)
        self.assertEqual(len(standing), 1)
        self.assertEqual([r["batch"] for r in standing[0]["runs"]],
                         ["answer-A"])
        self.assertEqual(orphans, [])
        self.assertNotIn("orphan", breakers)
        self.assertEqual(
            ledger.metrics()["rounds"][1]["unverified_acceptance"],
            {"test passed, mutation proven": 1})

    def test_a_repair_supersedes_an_earlier_standing_row(self):
        # Row A stands; a companion stamped B orphans; row B adopts it and
        # supersedes A — recency rules rows, adoption does not cheat it.
        ledger = self._round1(Ledger.in_memory())
        ledger.add_all(self._emission("b" * 40, status=None, batch="A"))
        ledger.add({"event": "falsification_run", "round": 1, "fp": self.FP,
                    "of": "F1", "status": "pass",
                    "mutation": "fails_without_fix", "test_digest": "td",
                    "source": "disposition", "blocking": True, "batch": "B"})
        ledger.add_all(self._emission("c" * 40, status=None, batch="B"))
        standing, orphans, breakers = self._states(ledger)
        self.assertEqual(len(standing), 1)
        self.assertEqual(standing[0]["disposition"]["batch"], "B")
        self.assertEqual([r["batch"] for r in standing[0]["runs"]], ["B"])
        self.assertEqual(orphans, [])
        self.assertNotIn("orphan", breakers)

    def test_a_rowless_stamped_companion_answers_nothing(self):
        # No row ever: the key has NO standing answer — the finding stays
        # owed, and the orphan escalates rather than impersonating an
        # answer.
        ledger = self._round1(Ledger.in_memory())
        ledger.add({"event": "falsification_run", "round": 1, "fp": self.FP,
                    "of": "F1", "status": "pass",
                    "mutation": "fails_without_fix", "test_digest": "td",
                    "source": "disposition", "blocking": True,
                    "batch": "orphan-X"})
        standing, orphans, breakers = self._states(ledger)
        self.assertEqual(standing, [])
        self.assertEqual(len(orphans), 1)
        self.assertIn("orphan", breakers)
        owed = transport.missing_dispositions(ledger)
        self.assertIsNotNone(owed)

    def test_rowless_unstamped_companions_group_as_one_orphan(self):
        # The legacy pre-stamp shape with no row at all: consecutive
        # unstamped companions for one key are one anomaly, not many.
        ledger = self._round1(Ledger.in_memory())
        for status in ("pass", "cannot_execute"):
            ledger.add({"event": "falsification_run", "round": 1,
                        "fp": self.FP, "of": "F1", "status": status,
                        "mutation": "not_run", "test_digest": "td",
                        "source": "disposition"})
        standing, orphans, breakers = self._states(ledger)
        self.assertEqual(standing, [])
        self.assertEqual(len(orphans), 1)
        self.assertEqual(len(orphans[0]["runs"]), 2)
        self.assertEqual(breakers.count("orphan"), 1)

    def test_an_aliased_orphan_resolves_to_one_key(self):
        from review.fingerprint import alias_event
        ledger = self._round1(Ledger.in_memory())
        ledger.add(alias_event(self.FP_OLD, self.FP))
        ledger.add_all(self._emission("b" * 40, status=None,
                                      batch="answer-A"))
        ledger.add({"event": "falsification_run", "round": 1,
                    "fp": self.FP_OLD, "of": "F1", "status": "pass",
                    "mutation": "fails_without_fix", "test_digest": "td",
                    "source": "disposition", "blocking": True,
                    "batch": "orphan-X"})
        standing, orphans, breakers = self._states(ledger)
        self.assertEqual(len(standing), 1)
        self.assertEqual(standing[0]["runs"], [])
        self.assertEqual(len(orphans), 1)
        self.assertEqual(orphans[0]["key"], standing[0]["key"],
                         "the alias did not resolve: two keys for one "
                         "identity")

    def test_orphan_evidence_does_not_exempt_the_repetition_breaker(self):
        # Conservative on the evidence side too: an orphan's refutation
        # evidence is no live claim, so it cannot silently excuse a
        # returning finding. (The valid control is the standing batch's
        # evidence, exercised by the breaker suite.)
        ledger = self._round1(Ledger.in_memory())
        ledger.add({"event": "evidence", "round": 1, "fp": self.FP,
                    "digest": "d-orphan", "source": "disposition",
                    "of": "F1", "batch": "orphan-X"})
        standing, orphans, breakers = self._states(ledger)
        self.assertEqual(standing, [])
        self.assertEqual([e["digest"] for e in orphans[0]["evidence"]],
                         ["d-orphan"])
        self.assertIn("orphan", breakers)

    def test_the_reviewer_block_names_the_anomaly_without_rendering_it(self):
        from review.emit import _dispositions_block
        ledger = self._round1(Ledger.in_memory())
        ledger.add_all(self._emission("b" * 40, batch="answer-A"))
        ledger.add({"event": "falsification_run", "round": 1, "fp": self.FP,
                    "of": "F1", "status": "pass",
                    "mutation": "fails_without_fix", "test_digest": "td",
                    "source": "disposition", "blocking": True,
                    "batch": "orphan-X"})
        block = _dispositions_block(ledger, 1)
        self.assertEqual(block.count("**accepted**"), 1)
        self.assertIn("ANOMALY", block)
        self.assertIn("orphan", block)

    def test_the_orphan_breaker_is_a_recordable_decision(self):
        # The escalation has the same recorded exit every breaker has:
        # `authorize-breaker` covers the firing by identity, and the
        # lineage moves again.
        import dataclasses as _dc
        from review import config as _config
        from review.tests.util import REPO_ROOT as _root
        cfg = _config.load(_root)
        ledger = self._round1(Ledger.in_memory())
        ledger.add({"event": "falsification_run", "round": 1, "fp": self.FP,
                    "of": "F1", "status": "pass",
                    "mutation": "fails_without_fix", "test_digest": "td",
                    "source": "disposition", "blocking": True,
                    "batch": "orphan-X"})
        fired = [f for f in transport.unauthorized_breakers(cfg, ledger)
                 if f["breaker"] == "orphan"]
        self.assertEqual(len(fired), 1)
        rec = transport.authorize_breaker(cfg, ledger, "orphan",
                                          "audited: hand-seeded probe",
                                          "user")
        self.assertTrue(rec["recorded"])
        self.assertEqual(
            [f for f in transport.unauthorized_breakers(cfg, ledger)
             if f["breaker"] == "orphan"], [])

    # ------------------------------------------ one decision, one orphan

    def _cfg(self):
        from review import config as _config
        from review.tests.util import REPO_ROOT as _root
        return _config.load(_root)

    def _answered(self):
        """A round-1 ledger whose only finding HAS its standing answer, so
        the handoff preflight's stop, when it comes, is the breaker's and
        not the missing-disposition rule's."""
        ledger = self._round1(Ledger.in_memory())
        ledger.add_all(self._emission("b" * 40, status=None,
                                      batch="answer-A"))
        return ledger

    def _orphan_run(self, batch, digest):
        return {"event": "falsification_run", "round": 1, "fp": self.FP,
                "of": "F1", "status": "pass", "mutation": "fails_without_fix",
                "test_digest": digest, "source": "disposition",
                "blocking": True, "batch": batch}

    def _unauthorized_orphans(self, cfg, ledger):
        return [f for f in transport.unauthorized_breakers(cfg, ledger)
                if f["breaker"] == "orphan"]

    def test_a_decision_on_one_orphan_does_not_take_the_next_one(self):
        """FALSIFICATION for round-4 F1 (High). A decision covered
        `orphan@<round>:<fp>`, and every LATER orphan batch for that key
        reduced to the same string — so a second, unread anomaly was
        already authorized and handoff continued. Mutation: drop
        `material` from the orphan firing (or the `#material` half of
        `firing_id`) and orphan-Y is covered by X's decision, `preflight`
        does not raise, and this test fails."""
        cfg = self._cfg()
        ledger = self._answered()
        ledger.add(self._orphan_run("orphan-X", "td-x"))
        fired = self._unauthorized_orphans(cfg, ledger)
        self.assertEqual(len(fired), 1)
        with self.assertRaises(transport.Refusal):
            transport.handoff_preflight(cfg, ledger)
        rec = transport.authorize_breaker(cfg, ledger, "orphan",
                                          "audited orphan-X", "user")
        self.assertEqual(rec["covers"], [fired[0]["firing"]])
        # The control, first: re-reading the UNCHANGED X firing stays
        # covered, and the lineage moves.
        self.assertEqual(self._unauthorized_orphans(cfg, ledger), [])
        transport.handoff_preflight(cfg, ledger)  # no raise
        # A second, distinct stamped orphan for the same (round, fp) is
        # material the human never saw.
        ledger.add(self._orphan_run("orphan-Y", "td-y"))
        self.assertEqual(len(ledger.orphan_companion_batches()), 2)
        still = self._unauthorized_orphans(cfg, ledger)
        self.assertEqual(len(still), 1, "orphan-Y was pre-authorized by the "
                                        "decision on orphan-X")
        self.assertEqual([r["batch"] for r in
                          [b for b in ledger.orphan_companion_batches()
                           if b["runs"][0]["test_digest"] == "td-y"][0]["runs"]],
                         ["orphan-Y"])
        self.assertNotIn(still[0]["firing"], rec["covers"])
        with self.assertRaises(transport.Refusal) as ctx:
            transport.handoff_preflight(cfg, ledger)
        self.assertIn(still[0]["firing"], str(ctx.exception))
        # ...and X stays decided: the second decision covers only Y.
        rec2 = transport.authorize_breaker(cfg, ledger, "orphan",
                                           "audited orphan-Y", "user")
        self.assertEqual(rec2["covers"], [still[0]["firing"]])
        self.assertEqual(self._unauthorized_orphans(cfg, ledger), [])

    def test_a_rowless_orphan_that_gains_a_companion_is_a_new_firing(self):
        """The unstamped shape of the same seam. Unstamped companions for
        one key join ONE rowless batch, so a batch that grows after the
        decision has no stamp to distinguish it — the material identity is
        the discriminator, and the answer is defined: new material is a
        new firing, never an extension the earlier decision swallows."""
        cfg = self._cfg()
        ledger = self._answered()
        # A key with NO row of its own: an unstamped companion for a key
        # that already has a standing batch legitimately joins it (the
        # legacy pre-stamp shape), so the rowless orphan is the one that
        # binds nothing at all.
        rowless = "fp2:aabbccddeeff0011"

        def companion(status):
            return {"event": "falsification_run", "round": 1, "fp": rowless,
                    "of": "F2", "status": status, "mutation": "not_run",
                    "test_digest": "td", "source": "disposition"}

        ledger.add(companion("pass"))
        first = self._unauthorized_orphans(cfg, ledger)
        self.assertEqual(len(first), 1)
        transport.authorize_breaker(cfg, ledger, "orphan",
                                    "audited the rowless batch", "user")
        # Control: unchanged, still one batch, still covered.
        self.assertEqual(len(ledger.orphan_companion_batches()), 1)
        self.assertEqual(self._unauthorized_orphans(cfg, ledger), [])
        transport.handoff_preflight(cfg, ledger)  # no raise
        # A later companion joins that same batch — still ONE anomaly, but
        # not the one that was decided.
        ledger.add(companion("cannot_execute"))
        self.assertEqual(len(ledger.orphan_companion_batches()), 1)
        grown = self._unauthorized_orphans(cfg, ledger)
        self.assertEqual(len(grown), 1, "new post-decision material was "
                                        "covered by the earlier decision")
        self.assertNotEqual(grown[0]["firing"], first[0]["firing"])
        with self.assertRaises(transport.Refusal):
            transport.handoff_preflight(cfg, ledger)

    def test_an_authorized_orphan_survives_unrelated_appends(self):
        """The other half of the control: the identity is the firing's own
        material, not the ledger's. Events that do not belong to the
        decided batch leave its coverage alone — an identity that moved
        with every append would make every decision single-use and the
        breaker unusable."""
        cfg = self._cfg()
        ledger = self._answered()
        ledger.add(self._orphan_run("orphan-X", "td-x"))
        rec = transport.authorize_breaker(cfg, ledger, "orphan",
                                          "audited orphan-X", "user")
        ledger.add({"event": "evidence", "round": 1, "fp": self.FP,
                    "digest": "d-verdict", "source": "verdict", "of": "F1"})
        ledger.add({"event": "gate", "round": 1, "id": "tests",
                    "status": "pass"})
        after = ledger.orphan_companion_batches()
        self.assertEqual(len(after), 1)
        self.assertEqual(self._unauthorized_orphans(cfg, ledger), [])
        self.assertEqual(rec["covers"],
                         [f["firing"] for f in ledger.breakers(
                             3, blocking_severities=["High"])
                          if f["breaker"] == "orphan"])
        transport.handoff_preflight(cfg, ledger)  # no raise


class TestBreakerFiringIdentity(unittest.TestCase):
    """Round 4 F1, the whole seam. `firing_id` is the boundary an
    authorization binds to, and its domain is EVERY breaker — the finding
    named `orphan`, and the same collapse was live for `unverifiable` (a
    key admits several `cannot_execute` runs) and for `budget`/tokens (one
    firing per round, over a spend that grows without bound). Each is
    closed the same way and paired with the control that an unchanged
    firing stays decided.

    The named mutation for the class: strip `material` from the firing
    dicts, or drop the `#material` half of `firing_id`, and every
    `assertEqual(len(...), 1)` on a post-decision firing below fails.
    """

    FP = TestDispositionSupersessionLifecycle.FP
    _round1 = TestDispositionSupersessionLifecycle._round1
    _emission = TestDispositionSupersessionLifecycle._emission

    def _cfg(self):
        from review import config as _config
        from review.tests.util import REPO_ROOT as _root
        return _config.load(_root)

    def _of(self, cfg, ledger, breaker):
        return [f for f in transport.unauthorized_breakers(cfg, ledger)
                if f["breaker"] == breaker]

    def test_identity_carries_the_key_and_the_material(self):
        self.assertEqual(
            ledger_mod.firing_id({"breaker": "orphan", "round": 2,
                                  "fp": "fp2:dead", "material": "m1"}),
            "orphan@2:fp2:dead#m1")
        self.assertEqual(
            ledger_mod.firing_id({"breaker": "budget", "round": 2,
                                  "limit": "tokens", "material": "m1"}),
            "budget@2:tokens#m1")
        # A firing with no material is still identified by its key: the
        # material half is additive, never a required field the older
        # shapes would trip over.
        self.assertEqual(
            ledger_mod.firing_id({"breaker": "no-progress", "round": 3}),
            "no-progress@3:")

    def test_every_firing_carries_its_own_identity(self):
        ledger = self._round1(Ledger.in_memory())
        ledger.add({"event": "falsification_run", "round": 1, "fp": self.FP,
                    "of": "F1", "status": "cannot_execute",
                    "mutation": "not_run", "source": "disposition",
                    "blocking": True, "batch": "orphan-X"})
        fired = ledger.breakers(3, blocking_severities=["High"])
        self.assertTrue(fired)
        for f in fired:
            self.assertEqual(f["firing"], ledger_mod.firing_id(f))
            self.assertIn("#", f["firing"], f)

    def test_a_second_unexecutable_run_is_not_the_decided_one(self):
        cfg = self._cfg()
        ledger = self._round1(Ledger.in_memory())
        ledger.add_all(self._emission("b" * 40, status=None, batch="A"))

        def cannot_execute(digest):
            return {"event": "falsification_run", "round": 1, "fp": self.FP,
                    "of": "F1", "status": "cannot_execute",
                    "mutation": "not_run", "test_digest": digest,
                    "note": "no runner", "source": "disposition",
                    "blocking": True, "batch": "A"}

        ledger.add(cannot_execute("td-1"))
        first = self._of(cfg, ledger, "unverifiable")
        self.assertEqual(len(first), 1)
        transport.authorize_breaker(cfg, ledger, "unverifiable",
                                    "runner unavailable, accepted", "user")
        self.assertEqual(self._of(cfg, ledger, "unverifiable"), [])
        ledger.add(cannot_execute("td-2"))
        second = self._of(cfg, ledger, "unverifiable")
        self.assertEqual(len(second), 1, "a distinct unexecutable run was "
                                         "pre-authorized by the decision on "
                                         "the first")
        self.assertNotEqual(second[0]["firing"], first[0]["firing"])
        with self.assertRaises(transport.Refusal):
            transport.handoff_preflight(cfg, ledger)

    def test_a_grown_token_spend_is_not_the_decided_breach(self):
        from unittest import mock
        cfg = self._cfg()
        ledger = self._round1(Ledger.in_memory())
        ledger.add_all(self._emission("b" * 40, batch="A"))
        envelopes = [e for e in ledger.current()
                     if e.get("event") in ("request", "verdict")]
        for e in envelopes:
            e["tokens"] = 200
        with mock.patch.object(type(cfg), "token_budget",
                               property(lambda _s: 100)):
            first = self._of(cfg, ledger, "budget")
            self.assertEqual(len(first), 1)
            self.assertIn("400", first[0]["rule"])
            transport.authorize_breaker(cfg, ledger, "budget",
                                        "audited: 400 against 100", "user")
            # Control: the same measured spend re-read is the same firing.
            self.assertEqual(self._of(cfg, ledger, "budget"), [])
            for e in envelopes:
                e["tokens"] = 5000
            grown = self._of(cfg, ledger, "budget")
            self.assertEqual(len(grown), 1, "a 25x larger breach was covered "
                                            "by the decision taken at 400")
            self.assertIn("10000", grown[0]["rule"])
            self.assertNotEqual(grown[0]["firing"], first[0]["firing"])

    def test_the_report_prints_the_identity_a_decision_binds_to(self):
        ledger = self._round1(Ledger.in_memory())
        ledger.add({"event": "falsification_run", "round": 1, "fp": self.FP,
                    "of": "F1", "status": "pass",
                    "mutation": "fails_without_fix", "test_digest": "td",
                    "source": "disposition", "blocking": True,
                    "batch": "orphan-X"})
        report = ledger.report(3, blocking_severities=["High"])
        fired = [f for f in report["breakers_fired"]
                 if f["breaker"] == "orphan"]
        self.assertEqual(len(fired), 1)
        md = ledger_mod.render_report_md(report)
        self.assertIn(fired[0]["firing"], md)


class _FakeDisposition:
    """The minimal parsed-disposition shape `disposition_events` reads."""

    def __init__(self, data):
        self.data = data
        self.attrs = {"verdict_sha": data.get("verdict_sha"),
                      "head": data.get("head")}


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



class TestGoverningAuthorityReachesTheVerdictLeg(unittest.TestCase):
    """Sweep F6's authority stopped at `take`; the reviewer could not finish
    the step `take` tells them to run.

    Live 2026-08-27: a take SUCCEEDED — target fetched, references checked,
    six findings written — and `loupe validate <verdict.md>`, which the
    procedure requires before a verdict may be handed back, answered
    T-UNDECLARED, because the reviewer was on a detached worktree carrying
    no `review.toml`. `--from-target` reaches the authority the target
    itself declares, deriving the SHA from the envelope's own stamp.

    Round 6 F1 removed the other origin entirely, and with it the record,
    the digest and the skew they were built to survive: an authority living
    on one machine cannot be shown to a second, and a review is the act of
    showing it to a second. What is left is the case that never needed
    proving.
    """

    def _cfg(self):
        return dataclasses.replace(CFG, roles=dict(CFG.roles), ledger_dir="")

    def _target_carrying_config(self):
        toml = (REPO_ROOT / "review.toml").read_text(encoding="utf-8")
        return fake_git({("cat-file", "-e", f"{SHA_B}^{{commit}}"): "",
                         ("ls-tree", "--full-tree", SHA_B, "--",
                          "review.toml"): "100644 blob 0000000\treview.toml",
                         ("show", f"{SHA_B}:review.toml"): toml})

    def _configless(self):
        return fake_git({
            ("cat-file", "-e", f"{SHA_B}^{{commit}}"): "",
            ("ls-tree", "--full-tree", SHA_B, "--", "review.toml"): ""})

    def test_the_target_supplies_the_authority_it_declares(self):
        governing = transport.governing_for(
            self._cfg(), SHA_B, git=self._target_carrying_config())
        self.assertIn(SHA_B[:12], governing.source)
        self.assertTrue(governing.taxonomy_declared)

    def test_absent_target_refuses_rather_than_falling_back(self):
        """The property `target_config` deliberately did NOT have: silence
        here would hand back this checkout's rules under the target's name."""
        def git(*args):
            raise RuntimeError("no such object")
        with self.assertRaises(transport.Refusal) as ctx:
            transport.governing_for(self._cfg(), SHA_B, git=git)
        self.assertEqual(ctx.exception.next_cmd, "")
        self.assertIn("not in this clone", str(ctx.exception))

    def test_a_target_declaring_no_rules_refuses(self):
        """Round 6 F1: the origin that could not be proved across machines
        is not narrowed, it is gone — from BOTH ends, which is round 2 F1's
        rule that the two ends refuse and accept the same states."""
        with self.assertRaises(transport.Refusal) as ctx:
            transport.governing_for(self._cfg(), SHA_B,
                                    git=self._configless())
        self.assertIn("carries no review.toml", str(ctx.exception))
        # `take` refuses the same state on a real repository, where its probe
        # is real too: TestTheReviewedCommitCarriesItsOwnRules covers it.

    def test_take_hands_over_the_authority_it_used(self):
        rec = transport.take(self._cfg(), Ledger.in_memory(), request_text(),
                             "r.md", reviewer="codex",
                             git=TestTake._git(TestTake()))
        self.assertIn("--from-target", rec["then"])

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

    def setUp(self):
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="identity-key-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.cfg = dataclasses.replace(CFG, ledger_dir=self.tmp)
        self.git = fake_git({("rev-parse", "HEAD"): SHA_B,
                             ("status", "--porcelain"): "",
                             **authority_calls()})

    def _warm(self, tool_attr):
        text = request_text(tool_attr=tool_attr)
        transport.keep_bytes(self.cfg, 1, "request", text)
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": SHA_B,
                    "source_digest": transport._digest_text(text),
                    "bytes": len(text), "claim_digest": transport.NO_CLAIM})
        return transport.cached_handoff(
            self.cfg, ledger, 1, git=self.git,
            claim_digest=transport.NO_CLAIM) is not None

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



#: The finite liveness EFFECTS, and the whole of what an effect may be.
#:
#: RVW-T19 round 2 F3. Round 1 made the table's evidence cell exact against
#: the decorator, which closed drift between the two documents and closed
#: nothing about whether either described the measurement. The effect was
#: free-form prose read only by the renderer: `@proves_live("checkout",
#: "does not convert anything")` rendered a cell that compared equal while
#: the EOL row went on measuring CRLF, so the table could still contradict
#: what its test does.
#:
#: So an effect is no longer a description of the assertion. It IS the
#: assertion. Each name maps to the surfaces it may legally describe and to
#: the PREDICATE that `assert_live` runs over what the row observed — one
#: definition, rendered into the table and executed by the test, with no
#: second statement to keep in step. Declaring an effect outside this set
#: raises at decoration time, so a free-form effect cannot reach the table;
#: declaring one whose predicate does not hold fails the owning test.
#:
#: The surface set per effect is what closes the last swap: two effects may
#: share an implementation (`converts` and `redirects the read` are both
#: "the marker appears"), and without it one could be stamped onto the
#: other's row and stay green while saying something false about it.
LIVENESS_EFFECTS = {
    "converts": (
        frozenset({"show --textconv", "log -p", "checkout"}),
        lambda seen, mark: mark in seen),
    "writes CRLF": (
        frozenset({"checkout"}),
        lambda seen, _mark: b"\r\n" in seen),
    "writes UTF-16LE": (
        frozenset({"checkout"}),
        lambda seen, _mark: b"[\x00r\x00o\x00l\x00e\x00s\x00" in seen),
    "redirects the read": (
        frozenset({"replace"}),
        lambda seen, mark: mark in seen),
}


def assert_live(case, seen: bytes, mark: bytes) -> bytes:
    """Assert this row's mechanism is live BY RUNNING the predicate its own
    `@proves_live` effect names.

    This is the whole of F3's repair. Before it, every row asserted its
    liveness by hand and separately declared prose about it; the prose was
    checked against the table and against nothing else. Now the declaration
    performs the assertion, so a stamp that misdescribes the measurement
    cannot be green: `writes CRLF` on a row whose surface produces no CRLF
    fails here, at the row, before the table is ever read.
    """
    fn = getattr(type(case), case._testMethodName)
    surfaces, predicate = LIVENESS_EFFECTS[fn.liveness_effect]
    case.assertIn(
        fn.liveness_surface, surfaces,
        f"{case._testMethodName} declares the effect "
        f"{fn.liveness_effect!r} at the surface {fn.liveness_surface!r}, "
        f"which is not one that effect may describe")
    case.assertTrue(
        predicate(seen, mark),
        f"{case._testMethodName} declares that {fn.liveness_surface!r} "
        f"{fn.liveness_effect}, and it does not — so this row's negative "
        f"proves nothing and the table's cell is false")
    return seen


def proves_live(surface, effect=None):
    """Stamp the SURFACE at which a conversion row is proved live.

    RVW-T19, from lineage 12 round 5 F2. The conversion table in
    `design/lineage-12-domain-partition.md` states, per row and in prose,
    where its mechanism is proved live. Nothing compared that cell to
    anything: the reference check asserted only that the named test
    EXISTS, so mutating the evidence cell alone — leaving mechanism and
    test name intact — left every test green.

    The surface is now declared here, beside the test, and the
    declaration is LOAD-BEARING: `_live_at` builds the liveness
    observation from it, so a wrong declaration fails this test. The
    workbench check then requires the row's cell to name the declaration.
    Drift fails on one side or the other, and neither side is prose.

    `None` declares a row that proves no conversion live — the paired
    control — and the check requires its cell to say so.

    RVW-T19 round 2, from round 1's F3. The stamp carries the EFFECT as
    well, because the surface alone was not enough to be the authority for
    what the row says. The workbench check compared by containment —
    `assertIn(surface, evidence)` — so a cell mutated to `yes — no
    checkout occurs` still contained `checkout` and stayed green while
    contradicting the observation it claims. A token found inside a
    sentence proves the token is there, never that the sentence agrees. So
    the cell is RENDERED from the declaration by `evidence_cell` below and
    compared exactly: the prose has one source, and drift is not a thing
    the table can express.
    """
    def stamp(fn):
        # Decoration time, not test time: a free-form effect must not be
        # able to REACH the table, and an unstamped effect on a live
        # surface would leave the cell describing nothing. Raising here
        # fails collection, which is louder than one red row.
        if surface is None:
            if effect is not None:
                raise ValueError(
                    f"{fn.__name__} declares that it proves no conversion "
                    f"live and also declares the effect {effect!r}")
        elif effect not in LIVENESS_EFFECTS:
            raise ValueError(
                f"{fn.__name__} declares the effect {effect!r}, which is "
                f"not one of {sorted(LIVENESS_EFFECTS)} — an effect is a "
                f"predicate this row runs, never a sentence about it")
        elif surface not in LIVENESS_EFFECTS[effect][0]:
            raise ValueError(
                f"{fn.__name__} declares the effect {effect!r} at the "
                f"surface {surface!r}, which is not one that effect may "
                f"describe")
        fn.liveness_surface = surface
        fn.liveness_effect = effect
        return fn
    return stamp


def evidence_cell(fn) -> str:
    """The conversion table's evidence cell for a test, RENDERED from the
    stamp that test acts on.

    One value, two consumers: `_live_at` builds the liveness observation
    from the surface, and the workbench check requires the table's cell to
    be exactly this string. A wrong surface therefore fails the row's own
    test, and any prose the table carries that this does not produce fails
    the reference check — including a sentence that contains the surface
    and denies it, which is the whole of round 1's F3.

    Readable by construction rather than by permission: the sentence a
    reader sees is the sentence generated, so keeping it readable is a
    matter of what is declared here, not of remembering to update prose.

    Round 2's F3 closed the half this did not. Exact equality made the
    table and the decorator agree; it left the effect free-form prose that
    only this function read, so `("checkout", "does not convert
    anything")` rendered a cell that compared equal while the row went on
    measuring CRLF. The effect is now a key into `LIVENESS_EFFECTS` and
    the predicate there is what `assert_live` runs, so both halves of this
    string are executed by the test that owns them: a false surface fails
    through `_live_at`, and a false effect fails through its predicate.
    """
    surface = fn.liveness_surface
    if surface is None:
        if fn.liveness_effect is not None:
            raise AssertionError(
                f"{fn.__name__} declares that it proves no conversion "
                f"live and also declares an effect")
        return "n/a"
    if not fn.liveness_effect:
        raise AssertionError(
            f"{fn.__name__} evidences a row of the conversion table and "
            f"declares a surface with no effect, so its cell cannot be "
            f"rendered and the row would be checked against nothing")
    return f"yes — `{surface}` {fn.liveness_effect}"


class TestTheReviewedCommitCarriesItsOwnRules(unittest.TestCase):
    """Round 6 F1, end to end on real repositories.

    Rounds 2 to 5 built a proof that the rules a verdict was judged by were
    the rules its request was judged by, for the case where those rules live
    on the reviewer's machine rather than in the commit. Every round the
    proof held and a new seam appeared one level out — a record the far end
    could not have, a skew no report-only comparison reaches, a domain
    statement shipped only to the build that already agrees.

    The seam was never in the proof. An authority on one machine cannot be
    shown to a second, and a review is the act of showing it to a second. So
    the origin goes: a handoff refuses where the repository tracks no
    configuration, and both reviewer verbs refuse a target that declares
    none. Nothing is left to prove, and the machinery that proved it is
    deleted rather than narrowed.
    """

    def setUp(self):
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="own-rules-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.repo = self.tmp / "repo"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)],
                       check=True, capture_output=True, timeout=60)
        self.toml = (REPO_ROOT / "review.toml").read_text(encoding="utf-8")
        (self.repo / "keep.txt").write_text("keep\n", encoding="utf-8")
        self.base = self._commit("base")

    def _git(self, *args, input_text=None):
        return subprocess.run(
            ["git", "-C", str(self.repo), "-c", "user.email=t@example.invalid",
             "-c", "user.name=t", *args], check=True, capture_output=True,
            text=True, timeout=60, input=input_text).stdout.strip()

    def _commit(self, subject):
        self._git("add", "-A")
        self._git("commit", "--allow-empty", "-qm", subject)
        return self._git("rev-parse", "HEAD")

    def _cfg(self):
        return dataclasses.replace(config.load(self.repo),
                                   ledger_dir=self.tmp / "ledger")

    def _envelope(self, sha):
        blob = self._git("rev-parse", f"{sha}:keep.txt")
        digest = __import__("hashlib").sha256(subprocess.run(
            ["git", "-C", str(self.repo), "cat-file", "blob", blob],
            check=True, capture_output=True, timeout=60).stdout).hexdigest()
        return request_text(
            sha=sha, base=self.base, push=True,
            refs=f"  keep.txt  sha256:{digest}  [required] a kept file\n")

    def _validate(self, sha, *extra):
        verdict = self.tmp / "v.md"
        verdict.write_text(
            f'<loupe-review-verdict sha="{sha}">\nVERDICT: clean to '
            f"advance\n\n## findings\n\nNone\n\n## evidence "
            f"checked\n\n- took it\n</loupe-review-verdict>\n",
            encoding="utf-8")
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli.main(["--ledger-dir", str(self.tmp / "ledger"),
                                 "validate", str(verdict), *extra])
            return code, json.loads(out.getvalue())
        finally:
            os.chdir(cwd)

    def test_the_author_refuses_where_the_rules_do_not_travel(self):
        """The author's end — RVW-T17: after the commit, before the push.

        Round 6 asserted this against `handoff_preflight`, which asked
        before the commit existed and therefore PREDICTED it. The rule is
        unchanged and its site is not: `ensure_pushed` reads the commit it
        just made, with the same call `take` makes.
        """
        from review import emit as _emit
        with self.assertRaises(_emit.AuthorityAbsent) as ctx:
            _emit.ensure_pushed(self._cfg(), local_only=True)
        exc = ctx.exception
        self.assertIn("carries no review.toml", str(exc))
        self.assertIn("commits review.toml", exc.remedy)
        self.assertIn("Nothing has been pushed or emitted", exc.remedy)

    def test_a_user_level_config_opens_no_reviewed_door(self):
        """Lineage 18 round 1 F1, the runtime half: a VALID user-level
        config, resolved as this repository's source, opens none of the
        three reviewed doors while the repository tracks no review.toml.
        The doc half — the shipped specification describing exactly this
        three-door result — is test_adapters.TestConfigAuthorityClaims;
        this control is what must stay refusing while any mutation of the
        shipped prose back to the fallback claim fails that guard."""
        from review import emit as _emit
        home = self.tmp / "home"
        cfg_dir = home / ".config" / TOOL_NAME
        cfg_dir.mkdir(parents=True)
        repo_id = config.load(self.repo).repo_id
        (cfg_dir / f"{repo_id}.toml").write_text(self.toml,
                                                 encoding="utf-8")
        with unittest.mock.patch.dict(os.environ, {"HOME": str(home)}):
            loaded = config.load(self.repo)
            # Paired control: the user config is LIVE — it resolved as the
            # source and declares the taxonomy — so the refusals below are
            # about the doors, not about a config nothing read.
            self.assertIn("user config", loaded.source)
            self.assertTrue(loaded.taxonomy_declared)
            cfg = dataclasses.replace(loaded,
                                      ledger_dir=self.tmp / "user-ledger")
            with self.assertRaises(_emit.AuthorityAbsent) as author:
                _emit.ensure_pushed(cfg, local_only=True)
            self.assertIn("carries no review.toml", str(author.exception))
            head = self._git("rev-parse", "HEAD")
            with self.assertRaises(transport.Refusal) as reviewer:
                transport.governing_for(cfg, head)
            self.assertIn("carries no review.toml", str(reviewer.exception))
            code, payload = self._validate(head, "--from-target")
            self.assertNotEqual(code, 0, payload)
            self.assertIn("review.toml", payload["error"])

    def _plant_blob_replacement(self, via="replace"):
        """Commit rules saying `gemini`, then install a replacement BLOB
        saying `claude`. Returns (sha, original_bytes).

        `via` is the git subcommand that makes the mechanism live. The row
        of the conversion table this evidences passes its own declared
        surface, so a declaration that does not name what plants the
        replacement fails here rather than sailing into the table
        (RVW-T19)."""
        committed = self.toml.replace(
            'permitted_authors = ["claude", "codex"]',
            'permitted_authors = ["gemini"]')
        self.assertNotEqual(committed, self.toml)
        (self.repo / "review.toml").write_text(committed, encoding="utf-8")
        sha = self._commit("rules saying gemini")
        original = self._git("rev-parse", f"{sha}:review.toml")
        replacement = self._git("hash-object", "-w", "--stdin",
                                input_text=self.toml)
        self._git(via, original, replacement)
        return sha, committed

    @proves_live("replace", "redirects the read")
    def test_a_replacement_blob_cannot_change_the_committed_authority(self):
        """Round 1 F1 (lineage 12), the Blocker.

        `git replace` installs a ref that makes every ordinary object lookup
        return a REPLACEMENT. It is local, uncommitted, per-machine state —
        the same class of input as a filter driver — and it reaches
        `ls-tree` and `cat-file` exactly as it reaches `show`, so the
        `cat-file` switch this round's own risk paragraph proposed would not
        have helped. The resolver must read the original.

        MUTATION, asserted below rather than described: the identical read
        WITHOUT `--no-replace-objects` returns the replacement. That proves
        the planted replacement is live and that the flag is what stops it,
        so this test can fail.
        """
        sha, committed = self._plant_blob_replacement(
            via=self._declared_surface())
        governing, origin = transport.resolve_authority(self._cfg(), sha)
        self.assertEqual(origin, transport.AUTHORITY_TARGET)
        self.assertEqual(governing.roles["permitted_authors"], ["gemini"],
                         "the resolver returned the REPLACEMENT's rules")
        # The mutation: replacement processing on.
        leaked = transport._git(self.repo, "show", f"{sha}:review.toml")
        assert_live(self, leaked.encode(), b"claude")
        self.assertNotIn("gemini", leaked.split("permitted_authors")[1][:40])

    def test_a_replacement_commit_cannot_change_the_committed_authority(self):
        """The same attack one object up: replace the COMMIT, so the tree
        and therefore the config blob are reached through the replacement.
        Named separately because a guard placed on the blob read alone
        would pass the test above and fail this one.
        """
        (self.repo / "review.toml").write_text(
            self.toml.replace('permitted_authors = ["claude", "codex"]',
                              'permitted_authors = ["gemini"]'),
            encoding="utf-8")
        sha = self._commit("rules saying gemini")
        (self.repo / "review.toml").write_text(self.toml, encoding="utf-8")
        other = self._commit("rules saying claude")
        self._git("replace", sha, other)
        governing, origin = transport.resolve_authority(self._cfg(), sha)
        self.assertEqual(origin, transport.AUTHORITY_TARGET)
        self.assertEqual(governing.roles["permitted_authors"], ["gemini"])
        leaked = transport._git(self.repo, "show", f"{sha}:review.toml")
        self.assertIn("claude", leaked, "the commit replacement is not live")

    def test_an_alternate_replacement_ref_base_is_also_ignored(self):
        """`GIT_REPLACE_REF_BASE` moves the namespace `refs/replace/` lives
        in. A guard that unset or ignored only the default namespace would
        be bypassed by one environment variable; `--no-replace-objects` is
        evaluated before any base is consulted, so it is not.
        """
        import os
        from unittest import mock
        base = "refs/myreplace/"
        with mock.patch.dict(os.environ, {"GIT_REPLACE_REF_BASE": base}):
            sha, _ = self._plant_blob_replacement()
            self.assertTrue(
                self._git("for-each-ref", "--format=%(refname)",
                          base).strip(),
                "the replacement was not written to the alternate base, so "
                "this test is not exercising what it claims")
            governing, origin = transport.resolve_authority(self._cfg(), sha)
            self.assertEqual(origin, transport.AUTHORITY_TARGET)
            self.assertEqual(governing.roles["permitted_authors"], ["gemini"])
            leaked = transport._git(self.repo, "show", f"{sha}:review.toml")
            self.assertIn("claude", leaked,
                          "the alternate-base replacement is not live")

    # ---------------------------------------------------------------
    # Round 3 F2 (lineage 12). The first cut of this asserted a whole
    # class at once and exercised only part of it: the eol row could not
    # have failed (the committed bytes were already LF and the assertion
    # was that the READ had no CRLF), `working-tree-encoding` was named in
    # the reference but never configured in a test, and the `log -p` claim
    # was never exercised at all. A test that states more than it measures
    # is the same defect as a claim that does — one row per mechanism now,
    # each proving its own transformation LIVE at the surface where git is
    # supposed to apply it, then proving the blob form returns the object's
    # own bytes.
    #
    # The sentinel is deliberate: a first cut used `gemini`, which already
    # appears in this repository's `rejected_reviewers`, so both the
    # control and the assertion passed for reasons unrelated to any driver.
    MARK = "ZZCONVERTEDZZ"

    def _conversion_repo(self, attributes: str, **git_config):
        """A commit whose `review.toml` blob is LF-encoded UTF-8, with the
        attributes and config declared AFTERWARDS. Returns the sha.

        The ordering is not incidental. A conversion attribute applies in
        both directions, and declaring `working-tree-encoding=UTF-16LE`
        before the file is staged makes git read this UTF-8 file AS
        UTF-16LE on the way in and refuse. Committing the blob first
        isolates the direction these rows are about — what a CHECKOUT
        does — and leaves the object holding known bytes."""
        (self.repo / "review.toml").write_text(
            self.toml.replace('permitted_authors = ["claude", "codex"]',
                              'permitted_authors = ["claude"]'),
            encoding="utf-8", newline="\n")
        self._commit("rules, before any conversion is declared")
        (self.repo / ".gitattributes").write_text(attributes,
                                                  encoding="utf-8")
        for k, v in git_config.items():
            self._git("config", k.replace("__", "."), v)
        # ONLY the attributes file is staged. `add -A` would restage
        # `review.toml` THROUGH the conversion just declared — git would
        # read these UTF-8 bytes as UTF-16LE and either refuse or write a
        # mangled blob — and then the object under test would no longer be
        # the object these rows are about.
        self._git("add", ".gitattributes")
        self._git("commit", "-qm", "declare the conversion")
        return self._git("rev-parse", "HEAD")

    def _declared_surface(self):
        """The surface the RUNNING test declares, read off its own stamp.

        Read rather than passed, so the declaration the workbench check
        compares against the table is the same value this test acts on.
        """
        fn = getattr(type(self), self._testMethodName)
        try:
            return fn.liveness_surface
        except AttributeError:
            raise AssertionError(
                f"{self._testMethodName} evidences a row of the conversion "
                f"table and declares no liveness surface") from None

    def _live_at(self, sha):
        """The bytes the DECLARED surface produces, which is where this
        row's mechanism is supposed to fire.

        Every conversion row's liveness assertion runs through here, so a
        declaration that does not match what the row measures fails the
        row rather than passing quietly into the table.
        """
        surface = self._declared_surface()
        if surface == "show --textconv":
            return self._git("show", "--textconv",
                             f"{sha}:review.toml").encode()
        if surface == "log -p":
            return self._git("log", "-p", "-1", "--format=", "--",
                             "review.toml").encode()
        if surface == "checkout":
            (self.repo / "review.toml").unlink()
            self._git("checkout", "--", "review.toml")
            return (self.repo / "review.toml").read_bytes()
        raise AssertionError(
            f"{self._testMethodName} declares the liveness surface "
            f"{surface!r}, which no observation here can produce")

    def _blob(self, sha):
        """The object's own bytes.

        Deliberately does NOT go through `self._cfg()`: after a
        `working-tree-encoding` checkout the worktree copy is UTF-16LE and
        `config.load` cannot decode it — which is itself the point of that
        row, and would otherwise make the test fail for the reason it is
        trying to measure. Only `repo_root` is used here."""
        cfg = dataclasses.replace(CFG, repo_root=self.repo)
        return transport.run_bytes(cfg, None, "show",
                                   f"{sha}:review.toml", no_replace=True)

    @proves_live("show --textconv", "converts")
    def test_textconv_is_live_and_does_not_reach_the_read(self):
        """MUTATION: remove `diff.x.textconv` and the liveness assertion
        fails; add `--textconv` to the read and the negative fails."""
        sha = self._conversion_repo(
            "review.toml diff=x\n",
            diff__x__textconv=f"sed s/claude/{self.MARK}/")
        assert_live(self, self._live_at(sha), self.MARK.encode())
        self.assertNotIn(self.MARK.encode(), self._blob(sha))

    @proves_live("log -p", "converts")
    def test_log_p_converts_and_the_read_does_not(self):
        """The `log -p` claim, EXERCISED rather than asserted in prose:
        textconv is on by default in the log/diff family, which is what
        makes the blob form's silence a fact about the blob form rather
        than about the driver."""
        sha = self._conversion_repo(
            "review.toml diff=x\n",
            diff__x__textconv=f"sed s/claude/{self.MARK}/")
        patch = assert_live(self, self._live_at(sha), self.MARK.encode())
        self.assertNotIn(self.MARK.encode(), self._blob(sha))
        self.assertTrue(patch, "the patch is empty")

    @proves_live("checkout", "converts")
    def test_smudge_is_live_and_does_not_reach_the_read(self):
        """MUTATION: remove `filter.sm.smudge` and the checkout control
        fails."""
        sha = self._conversion_repo(
            "review.toml filter=sm\n",
            filter__sm__smudge=f"sed s/claude/{self.MARK}/",
            filter__sm__clean="cat")
        assert_live(self, self._live_at(sha), self.MARK.encode())
        self.assertNotIn(self.MARK.encode(), self._blob(sha))

    @proves_live("checkout", "writes CRLF")
    def test_eol_conversion_is_live_and_does_not_reach_the_read(self):
        """Proved from the WORKTREE bytes, which is what the first cut
        missed: the committed bytes are LF, so asserting the read has no
        CRLF could not fail. The row now asserts the checkout DOES produce
        CRLF and the read does not."""
        sha = self._conversion_repo("review.toml text eol=crlf\n")
        assert_live(self, self._live_at(sha), self.MARK.encode())
        blob = self._blob(sha)
        self.assertNotIn(b"\r\n", blob)
        self.assertIn(b"\n", blob)

    @proves_live("checkout", "writes UTF-16LE")
    def test_working_tree_encoding_is_live_and_does_not_reach_the_read(self):
        """`working-tree-encoding` re-encodes on checkout. UTF-16LE is used
        rather than UTF-16 because git requires a BOM for the latter and
        refuses outright — a refusal is not the transformation this row is
        about."""
        sha = self._conversion_repo(
            "review.toml working-tree-encoding=UTF-16LE\n")
        assert_live(self, self._live_at(sha), self.MARK.encode())
        blob = self._blob(sha)
        self.assertIn(b"[roles]", blob,
                      "the read was re-encoded")
        self.assertNotIn(b"[\x00r\x00", blob)

    @proves_live(None)
    def test_no_conversion_is_the_paired_control(self):
        """The control every row above needs: with no attributes and no
        drivers, the read returns the same bytes and the authority
        resolves normally, so none of the negatives is passing because the
        read is broken."""
        sha = self._conversion_repo("")
        blob = self._blob(sha)
        self.assertIn(b'permitted_authors = ["claude"]', blob)
        governing, origin = transport.resolve_authority(self._cfg(), sha)
        self.assertEqual(origin, transport.AUTHORITY_TARGET)
        self.assertEqual(governing.roles["permitted_authors"], ["claude"])

    def test_no_replacement_is_the_paired_control(self):
        """The control the falsification requires: the same repository with
        NO replacement refs resolves the valid target exactly as before, so
        the hardening refuses nothing it should admit."""
        (self.repo / "review.toml").write_text(self.toml, encoding="utf-8")
        sha = self._commit("rules")
        self.assertEqual(
            self._git("for-each-ref", "--format=%(refname)",
                      "refs/replace/").strip(), "")
        governing, origin = transport.resolve_authority(self._cfg(), sha)
        self.assertEqual(origin, transport.AUTHORITY_TARGET)
        self.assertTrue(governing.taxonomy_declared)

    def test_a_duplicated_tree_entry_is_refused_not_guessed(self):
        """A tree carrying `review.toml` TWICE.

        `git mktree` accepts duplicate entries and `ls-tree` prints two
        lines for them. The listing parser could not tell that from one
        line — `split(maxsplit=3)` yields four fields either way — so only
        the first entry's mode was ever checked and the second rode along
        unread. Reproduced with `mktree` below.

        Honest bound, and it is why the refusal says "not established"
        rather than naming an exploit: `git show` also resolves the first
        entry, so no divergence between the two ends is demonstrated. What
        is wrong is that the agreement rests on an undocumented tie-break
        nothing asked about.
        """
        from review import emit as _emit
        one = self._git("hash-object", "-w", "--stdin", input_text=self.toml)
        two = self._git("hash-object", "-w", "--stdin",
                        input_text=self.toml + "\n# second\n")
        listing = (f"100644 blob {one}\treview.toml\n"
                   f"100644 blob {two}\treview.toml\n")
        tree = self._git("mktree", "--missing", input_text=listing)
        sha = self._git("commit-tree", tree, "-m", "duplicated")
        with self.assertRaises(transport.Refusal) as ctx:
            transport.resolve_authority(self._cfg(), sha)
        self.assertIn("more than one entry", str(ctx.exception))

    def test_a_committed_config_is_the_paired_control(self):
        from review import emit as _emit
        (self.repo / "review.toml").write_text(self.toml, encoding="utf-8")
        self._commit("rules")
        record = _emit.ensure_pushed(self._cfg(), local_only=True)
        self.assertTrue(record["governing"].taxonomy_declared)

    def test_the_assume_unchanged_divergence_is_caught(self):
        """RVW-T17's SIXTH face, and the one that needs no hook, no filter
        and no attribute — only a stock git index bit.

        `git update-index --assume-unchanged review.toml` makes git presume
        the file has not changed. `status --porcelain` then reports a CLEAN
        tree, every prospective check passes (the path is tracked, is a
        regular file, carries no filter attribute, and hashes identically
        to itself), and `git commit -a` records the INDEX version while
        `config.load` read the WORKTREE version. Author and reviewer are
        then governed by different bytes at one SHA — round 2 F1's
        asymmetry, reached without any of the five mechanisms rounds 7 to
        11 closed.

        FALSIFICATION: resolve the authority from `cfg` instead of from the
        commit and this fails, because `cfg` is the worktree's.
        """
        from review import emit as _emit
        committed = self.toml.replace(
            'permitted_authors = ["claude", "codex"]',
            'permitted_authors = ["gemini"]')
        self.assertNotEqual(committed, self.toml, "fixture no longer edits "
                                                  "the value it means to")
        (self.repo / "review.toml").write_text(committed, encoding="utf-8")
        self._commit("rules the reviewer will read")
        self._git("update-index", "--assume-unchanged", "review.toml")
        (self.repo / "review.toml").write_text(self.toml, encoding="utf-8")
        self.assertEqual(self._git("status", "--porcelain"), "",
                         "the divergence is invisible to status, which is "
                         "why nothing before the commit could see it")
        # What the author's own checkout resolves — the worktree bytes.
        self.assertIn("claude", self._cfg().roles["permitted_authors"])
        # Round 1 F2: the committed authority excludes the checkout's
        # default author, so the emission stops HERE — after the commit,
        # before the push and before any gate. Under the pre-F2 code this
        # emitted cleanly, stamped `claude`, and the refusal arrived at the
        # reviewer's `take` as R-AUTHOR-UNPERMITTED after a human had
        # already carried the envelope.
        with self.assertRaises(_emit.RoleSelectionError) as ctx:
            _emit.ensure_pushed(self._cfg(), local_only=True)
        self.assertIn("gemini", str(ctx.exception))

    def test_the_assume_unchanged_bytes_govern_when_roles_still_permit(self):
        """The same divergence with the roles left alone, so the assertion
        is about WHICH BYTES govern rather than about the refusal.

        FALSIFICATION: resolve the authority from `cfg` instead of from the
        commit and this fails, because `cfg` is the worktree's.
        """
        from review import emit as _emit
        committed = self.toml.replace("round_cap = 3", "round_cap = 7")
        self.assertNotEqual(committed, self.toml,
                            "fixture no longer edits the value it means to")
        (self.repo / "review.toml").write_text(committed, encoding="utf-8")
        self._commit("rules with a different cap")
        self._git("update-index", "--assume-unchanged", "review.toml")
        (self.repo / "review.toml").write_text(self.toml, encoding="utf-8")
        self.assertEqual(self._git("status", "--porcelain"), "")
        self.assertEqual(self._cfg().round_cap, 3)
        record = _emit.ensure_pushed(self._cfg(), local_only=True)
        self.assertEqual(
            record["governing"].round_cap, 7,
            "the emission must be governed by what the COMMIT records, "
            "not by what this worktree happens to hold")

    def test_a_filter_attribute_needs_no_refusal_of_its_own(self):
        """The transformation axis collapses (RVW-T17).

        Rounds 8, 9 and 10 each added machinery to establish what a filter
        WOULD do to the bytes on their way into a commit. Reading the
        commit makes the question moot: whatever the filter did, the commit
        records something, and that something is what both ends read. This
        asserts the collapse directly — a declared filter driver is present
        and the boundary neither refuses nor cares.
        """
        from review import emit as _emit
        (self.repo / ".gitattributes").write_text(
            "review.toml filter=mangle\n", encoding="utf-8")
        self._git("config", "filter.mangle.clean", "cat")
        (self.repo / "review.toml").write_text(self.toml, encoding="utf-8")
        self._commit("rules under a filter")
        record = _emit.ensure_pushed(self._cfg(), local_only=True)
        self.assertTrue(record["governing"].taxonomy_declared)

    def test_the_skip_worktree_false_refusal_is_gone(self):
        """A live FALSE refusal the prediction produced.

        With `skip-worktree` set and the worktree copy removed,
        `prospective_authority` refused "deleted in the working tree" — but
        `git commit -a` provably does not delete such an entry, so a
        perfectly valid handoff was blocked. Reading the commit gets it
        right for the same reason it gets everything else right: it looks.
        """
        from review import emit as _emit
        (self.repo / "review.toml").write_text(self.toml, encoding="utf-8")
        self._commit("rules")
        self._git("update-index", "--skip-worktree", "review.toml")
        (self.repo / "review.toml").unlink()
        record = _emit.ensure_pushed(self._cfg(), local_only=True)
        self.assertTrue(
            record["governing"].taxonomy_declared,
            "the commit carries the rules; refusing here refuses a valid "
            "handoff on the strength of a guess about the worktree")

    def test_both_reviewer_verbs_refuse_a_target_declaring_no_rules(self):
        sha = self._commit("no rules")
        (self.repo / "review.toml").write_text(self.toml, encoding="utf-8")
        with self.assertRaises(transport.Refusal) as ctx:
            transport.take(self._cfg(), Ledger(self.tmp / "ledger"),
                           self._envelope(sha), "r.md", reviewer="codex",
                           fetch=False)
        self.assertIn("carries no review.toml", str(ctx.exception))
        code, payload = self._validate(sha, "--from-target")
        self.assertNotEqual(code, 0, payload)
        self.assertEqual(payload["next_kind"], "blocked", payload)
        self.assertNotIn("items", payload, payload)

    def test_the_printed_command_validates_a_commit_carrying_its_rules(self):
        (self.repo / "review.toml").write_text(self.toml, encoding="utf-8")
        sha = self._commit("rules")
        rec = transport.take(self._cfg(), Ledger(self.tmp / "ledger"),
                             self._envelope(sha), "r.md", reviewer="codex",
                             fetch=False)
        self.assertIn("--from-target", rec["then"])
        code, payload = self._validate(sha, "--from-target")
        codes = [i["code"] for i in payload.get("items", [])]
        self.assertIn("T-AUTHORITY", codes, payload)
        self.assertNotIn("T-UNDECLARED", codes, payload)

    def test_without_the_flag_nothing_about_the_default_changed(self):
        (self.repo / "review.toml").write_text(self.toml, encoding="utf-8")
        sha = self._commit("rules")
        (self.repo / "review.toml").unlink()
        code, payload = self._validate(sha)
        self.assertNotIn("T-AUTHORITY",
                         [i["code"] for i in payload.get("items", [])])

class TestAuthorityOriginIsClassified(unittest.TestCase):
    """Round 3 F2 and F3: absence and unreadability were one branch.

    `_git` turns every nonzero git exit into `RuntimeError`, and the old
    resolver caught that whole class as "the target carries no review.toml".
    So a present entry whose blob object was missing answered with the
    CHECKOUT's rules — measured: target `round_cap` 3, checkout 9, and the
    checkout won. Round 3's digest could not see it, because both ends asked
    the same misclassifying question; a green continuity proof sat on top.
    Invalid UTF-8 escaped further: the text-mode reader raised
    `UnicodeDecodeError` past every typed refusal, leaving an agent with no
    `next_kind` and no `remedy`.

    External fallback is advertised for ONE state, and it is now established
    by the question that answers it (`ls-tree`: silent and exit 0 for a path
    a tree lacks) rather than inferred from a failure to read. Every other
    outcome refuses.

    The mutation: restore `except RuntimeError: fall back` around the read
    and the corrupt-object and operational rows pass when they must not.
    """

    VALID = "review.toml"

    def _repo(self, body=None, *, as_bytes=False, as_dir=False,
              corrupt=False, symlink_to=None, executable=False,
              gitlink=False):
        """A target commit carrying `body` as review.toml, plus a DIFFERENT
        valid config in the checkout — so a silent fallback is visible as
        the wrong rules rather than as no rules at all."""
        try:
            tmp = Path(tempfile.mkdtemp(prefix="origin-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        repo = tmp / "repo"
        good = (REPO_ROOT / self.VALID).read_text(encoding="utf-8")
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)],
                       check=True, capture_output=True, timeout=60)
        if symlink_to is not None:
            # The link TEXT is itself valid TOML, and the file it names holds
            # different valid TOML: the two ends must not be able to read
            # different bytes for one SHA (round 4 F1).
            (repo / symlink_to).write_text(
                (REPO_ROOT / self.VALID).read_text(encoding="utf-8")
                .replace("round_cap = 3", "round_cap = 9"), encoding="utf-8")
            (repo / self.VALID).symlink_to(symlink_to)
        elif as_dir:
            (repo / self.VALID).mkdir()
            (repo / self.VALID / "x").write_text("x", encoding="utf-8")
        elif body is not None:
            target = repo / self.VALID
            if as_bytes:
                target.write_bytes(body)
            else:
                target.write_text(body, encoding="utf-8")
        (repo / "other.txt").write_text("x", encoding="utf-8")
        if executable:
            (repo / self.VALID).chmod(0o755)

        def commit(subject, stage=True):
            steps = (("add", "-A"),) if stage else ()
            for args in (*steps,
                         ("-c", "user.email=t@example.invalid", "-c",
                          "user.name=t", "commit", "--allow-empty", "-qm",
                          subject)):
                subprocess.run(["git", "-C", str(repo), *args], check=True,
                               capture_output=True, timeout=60)
            return subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                capture_output=True, text=True, timeout=60).stdout.strip()

        # A REAL base: the take row below must refuse for its own reason,
        # and a fixture base SHA that does not resolve refuses first — which
        # is how that row passed under the mutation it exists to catch.
        held = (repo / self.VALID)
        link = os.readlink(held) if held.is_symlink() else None
        stashed = (held.read_bytes()
                   if link is None and held.is_file() else None)
        if link is not None or stashed is not None:
            held.unlink()
        self.base = commit("base")
        if link is not None:
            held.symlink_to(link)          # still a symlink at the target
        elif stashed is not None:
            held.write_bytes(stashed)
        sha = commit("target")
        if gitlink:
            subprocess.run(
                ["git", "-C", str(repo), "update-index", "--add",
                 "--cacheinfo", f"160000,{sha},{self.VALID}"], check=True,
                capture_output=True, timeout=60)
            # No `add -A`: it would re-stage the worktree file as a blob and
            # undo the gitlink this row exists to test.
            sha = commit("gitlink", stage=False)
        if corrupt:
            blob = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", f"{sha}:{self.VALID}"],
                check=True, capture_output=True, text=True,
                timeout=60).stdout.strip()
            (repo / ".git" / "objects" / blob[:2] / blob[2:]).unlink()
        # The checkout's rules differ, so a substitution is legible.
        if as_dir or (repo / self.VALID).is_symlink():
            (repo / self.VALID).unlink() if (
                repo / self.VALID).is_symlink() else None
        if as_dir:
            __import__("shutil").rmtree(repo / self.VALID)
        (repo / self.VALID).write_text(
            good.replace("round_cap = 3", "round_cap = 9"), encoding="utf-8")
        cfg = dataclasses.replace(config.load(repo), ledger_dir=tmp / "l")
        return cfg, sha

    def _resolve(self, **kw):
        cfg, sha = self._repo(**kw)
        return transport.resolve_authority(cfg, sha)

    # ------------------------------------------------------ the controls

    def test_a_truly_absent_config_is_the_advertised_fallback(self):
        governing, origin = self._resolve(body=None)
        self.assertEqual(origin, transport.AUTHORITY_EXTERNAL)
        self.assertEqual(governing.round_cap, 9)   # the checkout's, honestly

    def test_a_readable_config_is_the_targets_own(self):
        good = (REPO_ROOT / self.VALID).read_text(encoding="utf-8")
        governing, origin = self._resolve(body=good)
        self.assertEqual(origin, transport.AUTHORITY_TARGET)
        self.assertEqual(governing.round_cap, 3)   # the TARGET's

    def test_ordinary_non_ascii_utf8_is_read(self):
        good = (REPO_ROOT / self.VALID).read_text(encoding="utf-8")
        governing, origin = self._resolve(body="# caf\u00e9 \u2014 \u00e9\n" + good)
        self.assertEqual(origin, transport.AUTHORITY_TARGET)
        self.assertEqual(governing.round_cap, 3)

    # ------------------------------------------------------ the refusals

    def _refuses(self, why, **kw):
        with self.assertRaises(transport.Refusal) as ctx:
            self._resolve(**kw)
        exc = ctx.exception
        self.assertEqual(exc.next_cmd, "", why)
        self.assertTrue(exc.remedy, why)
        return exc

    def test_a_present_but_unreadable_blob_refuses(self):
        """F2's own falsification: entry present, object gone."""
        good = (REPO_ROOT / self.VALID).read_text(encoding="utf-8")
        self.assertIn("PRESENT", str(self._refuses("corrupt object",
                                                   body=good, corrupt=True)))

    def test_an_executable_regular_file_is_still_a_file(self):
        """The control for the mode check: 100755 carries bytes a checkout
        reads, so it must be ADMITTED, not swept up with the refusals."""
        good = (REPO_ROOT / self.VALID).read_text(encoding="utf-8")
        governing, origin = self._resolve(body=good, executable=True)
        self.assertEqual(origin, transport.AUTHORITY_TARGET)
        self.assertEqual(governing.round_cap, 3)

    def test_a_tree_entry_refuses(self):
        self.assertIn("a directory",
                      str(self._refuses("tree entry", as_dir=True)))

    def test_a_submodule_entry_refuses(self):
        good = (REPO_ROOT / self.VALID).read_text(encoding="utf-8")
        self.assertIn("a submodule",
                      str(self._refuses("gitlink", body=good, gitlink=True)))

    def test_a_symlink_cannot_split_the_two_ends(self):
        """Round 4 F1's falsification. A symlink is mode 120000 and object
        type `blob`, so a type-only check admitted it and `git show` handed
        back the LINK TEXT — while the author's `config.load` followed the
        link and read the file. One SHA, two configurations."""
        cfg, sha = self._repo(symlink_to="linked.toml")
        # The author's end follows the link: this is the value it would use.
        self.assertEqual(cfg.round_cap, 9)
        with self.assertRaises(transport.Refusal) as ctx:
            transport.resolve_authority(cfg, sha)
        self.assertIn("a symbolic link", str(ctx.exception))
        self.assertEqual(ctx.exception.next_cmd, "")

    def test_a_dangling_symlink_refuses_for_the_same_reason(self):
        cfg, sha = self._repo(symlink_to="linked.toml")
        (cfg.repo_root / "linked.toml").unlink()
        with self.assertRaises(transport.Refusal) as ctx:
            transport.resolve_authority(cfg, sha)
        self.assertIn("a symbolic link", str(ctx.exception))

    def test_every_invalid_config_shape_is_a_typed_refusal(self):
        """Round 4 F4: "TOML the config layer rejects" was not a closed
        state. `taxonomy = []` crashed inside resolution with a raw
        TypeError, and a string `round_cap` acquired target authority and
        raised a raw ValueError later — after this boundary had reported
        success. The schema is derived from DEFAULTS, so each row below is a
        kind that authority already declares."""
        good = (REPO_ROOT / self.VALID).read_text(encoding="utf-8")
        for label, body in (
                ("a section that is not a table", "taxonomy = []"),
                ("a scalar of the wrong type",
                 "[limits]\nround_cap = \"not-an-int\""),
                ("a boolean where a count belongs",
                 "[limits]\nround_cap = true"),
                ("a list holding non-strings",
                 "[taxonomy]\nseverities = [1, 2]"),
                ("a table holding non-strings",
                 "[taxonomy]\nclassification_notes = {a = 1}"),
                ("an unknown section", "[nope]\nx = 1"),
                ("an unknown key", "[limits]\nnope = 1"),
                ("a gate row that is not a table", "gates = [1]"),
                ("a gate with no id",
                 "[[gates]]\nid = \"\"\ncommand = [\"true\"]"),
                ("a gate with an empty command",
                 "[[gates]]\nid = \"x\"\ncommand = []"),
                ("a gate with a non-boolean blocking",
                 "[[gates]]\nid = \"x\"\ncommand = [\"true\"]\n"
                 "blocking = \"yes\""),
                # Round 5 F2: shape was closed, IDENTITY was not. A gate id
                # names the retained output and joins the attestation, so two
                # rows sharing one are two gates wearing one identity — both
                # run, the second output overwrites the first, and the first
                # record's pointer then names bytes that are not its own.
                ("two gate rows sharing one id",
                 "[[gates]]\nid = \"dup\"\ncommand = [\"echo\", \"a\"]\n"
                 "[[gates]]\nid = \"dup\"\ncommand = [\"echo\", \"b\"]"),
        ):
            with self.subTest(shape=label):
                self.assertIn("AUTHOR", self._refuses(label, body=body).remedy)
        # The paired canonical control: the repository's own configuration,
        # which every row above is a mutation of, still resolves.
        governing, origin = self._resolve(body=good)
        self.assertEqual(origin, transport.AUTHORITY_TARGET)
        # And the distinct-id control, so the uniqueness rule is a rule about
        # collision rather than a rule against declaring two gates.
        two, _ = self._resolve(
            body="[[gates]]\nid = \"one\"\ncommand = [\"true\"]\n"
                 "[[gates]]\nid = \"two\"\ncommand = [\"true\"]\n"
                 + good)
        self.assertEqual([g["id"] for g in two.gates][:2], ["one", "two"])

    def _timeout(self, *_a):
        raise subprocess.TimeoutExpired(cmd="git", timeout=120)

    def test_a_timeout_at_the_presence_probe_is_typed(self):
        """Round 4 F3: a timeout is an ADMITTED outcome — both readers
        declare one — and TimeoutExpired is not a RuntimeError, so it tore
        through every caller's catch and left an agent with no next_kind."""
        cfg, _ = self._repo(body="round_cap = 3")
        with self.assertRaises(transport.Refusal) as ctx:
            transport.resolve_authority(cfg, "b" * 40, git=self._timeout)
        self.assertEqual(ctx.exception.next_cmd, "")
        self.assertTrue(ctx.exception.remedy)

    def test_a_timeout_at_the_byte_read_is_typed(self):
        cfg, sha = self._repo(
            body=(REPO_ROOT / self.VALID).read_text(encoding="utf-8"))
        entry = f"100644 blob 0000000\t{self.VALID}"

        def git(*args):
            if args[0] == "ls-tree":
                return entry
            raise subprocess.TimeoutExpired(cmd="git", timeout=120)

        with self.assertRaises(transport.Refusal) as ctx:
            transport.resolve_authority(cfg, sha, git=git)
        self.assertIn("cannot be read", str(ctx.exception))
        self.assertTrue(ctx.exception.remedy)

    def test_a_missing_executable_is_typed(self):
        cfg, _ = self._repo(body="round_cap = 3")

        def git(*args):
            raise FileNotFoundError(2, "no such file", "git")

        with self.assertRaises(transport.Refusal) as ctx:
            transport.resolve_authority(cfg, "b" * 40, git=git)
        self.assertEqual(ctx.exception.next_cmd, "")

    def test_an_unreadable_tree_listing_refuses(self):
        cfg, _ = self._repo(body="round_cap = 3")
        with self.assertRaises(transport.Refusal) as ctx:
            transport.resolve_authority(
                cfg, "b" * 40, git=lambda *a: "garbage")
        self.assertIn("cannot be read", str(ctx.exception))

    def test_invalid_leading_bytes_refuse(self):
        self.assertIn("not valid UTF-8",
                      str(self._refuses("0xff 0xfe", body=b"\xff\xfe\x00x",
                                    as_bytes=True)))

    def test_truncated_multibyte_input_refuses(self):
        self.assertIn("not valid UTF-8",
                      str(self._refuses("lone continuation byte",
                                    body=b"round_cap = 3\n\xc3",
                                    as_bytes=True)))

    def test_a_byte_order_mark_is_refused_rather_than_stripped(self):
        """BOM policy, stated rather than left to chance: the bytes decode,
        and the config layer then rejects them. Declared here so the row
        cannot silently become 'stripped' later."""
        good = (REPO_ROOT / self.VALID).read_text(encoding="utf-8")
        self._refuses("utf-8 BOM",
                      body=b"\xef\xbb\xbf" + good.encode("utf-8"),
                      as_bytes=True)

    def test_decodable_but_malformed_toml_still_refuses(self):
        """The paired control for the two byte rows: same typed refusal,
        different cause."""
        self._refuses("malformed TOML", body="[[[ not toml")

    # ------------------------------------------------- through the verbs

    def test_take_refuses_a_corrupt_target_config_and_records_nothing(self):
        good = (REPO_ROOT / self.VALID).read_text(encoding="utf-8")
        cfg, sha = self._repo(body=good, corrupt=True)
        ledger = Ledger(cfg.ledger_dir)
        with self.assertRaises(transport.Refusal) as ctx:
            transport.take(cfg, ledger,
                           request_text(sha=sha, base=self.base), "r.md",
                           reviewer="codex", fetch=False)
        # Bound to its own cause, not to whatever refuses first.
        self.assertIn("cannot be read", str(ctx.exception))
        self.assertEqual(ledger.events(), [],
                         "a refusal before validation appends nothing")



class TestGateIdIsASafeFilenameComponent(unittest.TestCase):
    """Round 6 F2: a gate id is a logical identity AND a path component.

    `_retain_output` writes `<ledger>/gate-output/<sha>/<id>.log`, so an
    ABSOLUTE id discards the directory entirely — measured: a manifest whose
    id was an absolute path was accepted, ran, and wrote outside the ledger's
    subtree — while separators and dot segments escape or alias it. Round 5
    established that two rows cannot share an id STRING; two distinct strings
    can still name one destination, and on a case-folding filesystem
    routinely do.

    The grammar is closed rather than the escapes enumerated: containment and
    one-id-one-file are properties of the alphabet, not of a blacklist.

    Mutations: accept any non-blank id and the escape rows pass; compare ids
    case-sensitively and the alias row passes.
    """

    def _cfg(self):
        return dataclasses.replace(CFG, ledger_dir=None)

    def _rows(self, *ids):
        return "".join(f'[[gates]]\nid = "{i}"\ncommand = ["true"]\n'
                       for i in ids)

    def _refused(self, body, door):
        with self.assertRaises(config.ConfigError) as ctx:
            if door == "from_text":
                config.from_text(body, self._cfg(), source="t")
            else:
                tmp = Path(tempfile.mkdtemp(prefix="gate-id-"))
                self.addCleanup(lambda: __import__("shutil").rmtree(
                    tmp, ignore_errors=True))
                subprocess.run(["git", "init", "-q", str(tmp)], check=True,
                               capture_output=True, timeout=60)
                (tmp / "review.toml").write_text(body, encoding="utf-8")
                config.load(tmp)
        return str(ctx.exception)

    ESCAPES = {
        "absolute": "/tmp/escaped",
        "parent segment": "../escaped",
        "separator": "a/b",
        "leading dot": ".hidden",
        "dot segment": "..",
        "backslash": "a\\b",
        "space": "two words",
    }

    def test_no_escape_form_survives_either_configuration_door(self):
        for door in ("from_text", "load"):
            for label, gid in self.ESCAPES.items():
                with self.subTest(door=door, form=label):
                    self.assertIn("filename component",
                                  self._refused(self._rows(gid), door))

    def test_two_ids_naming_one_destination_refuse(self):
        """Distinct strings, one file: the alias round 5's string equality
        could not see."""
        self.assertIn("one retained output",
                      self._refused(self._rows("tests", "TESTS"),
                                    "from_text"))

    def test_distinct_valid_ids_are_the_paired_control(self):
        cfg = config.from_text(self._rows("tests", "lint-2", "a.b_c"),
                               self._cfg(), source="t")
        self.assertEqual([g["id"] for g in cfg.gates],
                         ["tests", "lint-2", "a.b_c"])

    def test_every_admitted_id_stays_an_immediate_child(self):
        """The property the grammar exists to give `_retain_output`: for any
        id the schema admits, the destination is one level under the
        per-SHA directory and nowhere else."""
        target = Path("/ledger/gate-output/abcdef")
        cfg = config.from_text(self._rows(*self.CONTAINMENT), self._cfg(),
                               source="t")
        for gate in cfg.gates:
            with self.subTest(id=gate["id"]):
                out = (target / f"{gate['id']}.log").resolve()
                self.assertEqual(out.parent, target.resolve())

    CONTAINMENT = ("tests", "lint-2", "a.b_c", "A", "z9")


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

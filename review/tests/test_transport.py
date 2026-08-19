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
import dataclasses
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from review import cli, config, transport, validate, wire
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


def request_text(sha=SHA_B, base=SHA_A, reviewer="codex", author="claude",
                 round_no=1, push=True, refs=None):
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
    return (f'<loupe-review-request sha="{sha}" branch="main" '
            f'author="{author}" reviewer="{reviewer}" round="{round_no}">\n'
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
        self.assertIn("respond", rec["next"])
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
                        ("status", "--porcelain"): ""})
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": SHA_A,
                    "source_digest": "x", "bytes": 1})
        self.assertIsNone(transport.cached_handoff(cfg, ledger, 1, git=git))

    def test_cold_when_no_kept_copy(self):
        cfg = dataclasses.replace(CFG, ledger_dir=None)  # no exchange dir
        git = fake_git({("rev-parse", "HEAD"): SHA_B,
                        ("status", "--porcelain"): ""})
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
        text = request_text()
        kept = transport.keep_bytes(cfg, 1, "request", text)
        self.assertTrue(Path(kept).is_file())
        git = fake_git({("rev-parse", "HEAD"): SHA_B,
                        ("status", "--porcelain"): ""})
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
        text = request_text()
        transport.keep_bytes(cfg, 1, "request", text)
        git = fake_git({("rev-parse", "HEAD"): SHA_B,
                        ("status", "--porcelain"): ""})
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
        text = request_text()
        transport.keep_bytes(cfg, 1, "request", text)
        git = fake_git({("rev-parse", "HEAD"): SHA_B,
                        ("status", "--porcelain"): ""})
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
        text = request_text()
        transport.keep_bytes(cfg, 1, "request", text)
        git = fake_git({("rev-parse", "HEAD"): SHA_B,
                        ("status", "--porcelain"): ""})
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
        # and the record says so rather than pretending otherwise.
        rec2 = transport.target_config(
            dataclasses.replace(CFG, ledger_dir=None), "0" * 40,
            git=lambda *a: (_ for _ in ()).throw(RuntimeError("absent")))
        self.assertIn("the target carries no review.toml", rec2.source)
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
        claim.write_text('{"objective": "x", "risk": "low"}',
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
        self.assertEqual(rec["covers"], [f"stale@2:{FP}"])
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
        self.assertEqual(rec["covers"], [f"unverifiable@1:{FP}"])
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
        self.assertEqual(kinds, [e["event"] for e in expected])
        # And parity is idempotence: filing the same envelope through the
        # normal path afterwards is a no-op, never a duplicate.
        self.assertEqual(ledger.add_all(expected), 0)

    def test_the_cap_in_force_is_the_one_checked(self):
        # A round past this lineage's effective cap is invalid at this door
        # too, exactly as `handoff` and `validate` refuse it.
        import re
        text = re.sub(r'round="\d+"', 'round="9"',
                      self.synth.emitted_request(), count=1)
        code, payload = self._add(text)
        self.assertNotEqual(code, 0)
        self.assertIn("R-BUDGET", {i["code"] for i in payload["items"]})
        self.assertEqual(Ledger(self.tmp).events(), [])


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

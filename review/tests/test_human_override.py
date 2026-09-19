"""The human override of a single finding, and advancing on it (2026-09-01).

A finding dies exactly two ways in the protocol proper: the author accepts
and verifies it, or the reviewer withdraws it against a refutation. Neither
is available when a HUMAN reads a finding and decides to live with it, and
`escalated` — legal on a blocking finding, carrying `authority` and
`criterion` — could already ROUTE one to a named person while nothing
recorded what that person ANSWERED.

The cost of that gap was not ergonomic. The only way to reach a clean
verdict over a knowingly accepted risk was to ask the reviewer to withdraw a
finding they still stood behind, after which the record cannot separate "the
reviewer found nothing" from "the reviewer found something a human waved
off". These tests own that separation from both ends: the answer is recorded
as its own event, and advancing on it emits its own envelope kind rather
than a derived clean verdict.

THE LIFECYCLE AUTHORITY is `vocab.FINDING_ANSWERS` (lineage 20 rounds 3-4):
every answer kind the grammar can record — each closure term, each
disposition, the waiver — with its effect on the finding it answers, and ONE
binding rule for all of them: an answer answers the newest ruling of its
identity only if it was recorded no earlier than that ruling. The matrix in
`TestEveryAnswerIsBoundToTheRulingItAnswers` is derived from that table by
iteration, and `TestTheLifecycleTableIsClosed` fails by name when a closure
term or disposition joins the vocabulary without a row here. Rounds 3 and 4
each found one answer kind bound differently from the others (an acceptance
to its round; a withdrawal and a waiver to the identity for all time); the
table is what stops a fifth binding being invented for the next kind.

THE LIMIT, asserted rather than described (see `test_the_record_is_an_
assertion_not_a_proof`): `by` is asserted. The tool cannot observe who ran a
command, so none of this proves a human rather than an agent decided. It
buys attribution and visibility, and the tests say so where a reader of the
suite will meet it. The authorization envelope's own grammar is
`test_authorization_grammar.py`'s subject, not this file's.
"""
import dataclasses
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from review import transport, validate, vocab, wire
from review.ledger import Ledger
from review.tests.synth import CFG
from review.tests.util import LINEAGE

SHA = "a" * 40
SHA2 = "b" * 40
FP1 = "fp2:" + "1" * 16
FP2 = "fp2:" + "2" * 16


def _finding(fid="F1", fp=FP1, round_no=1, severity="Medium"):
    return {"event": "finding", "round": round_no, "id": fid, "fp": fp,
            "severity": severity, "title": f"{fid} title",
            "anchor_path": "review/x.py", "falsification": "run x"}


class _Base(unittest.TestCase):
    """A lineage with one ruled round, on a real state directory: the
    advance keeps envelope bytes, and a fake path would prove nothing about
    the verb that writes them."""

    def setUp(self):
        try:
            tmp = Path(tempfile.mkdtemp(prefix="override-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.tmp = tmp
        self.cfg = dataclasses.replace(CFG, ledger_dir=tmp)
        self.ledger = Ledger.in_memory()

    def rule(self, findings=(), verdict="changes requested", round_no=1,
             sha=SHA):
        self.ledger.add({"event": "request", "round": round_no, "sha": sha,
                         "bytes": 1, "author": "claude", "reviewer": "codex"})
        self.ledger.add({"event": "verdict", "round": round_no, "sha": sha,
                         "verdict": verdict, "bytes": 1,
                         "finding_ids": len(findings)})
        for f in findings or [_finding(round_no=round_no)]:
            self.ledger.add(f)

    def waive(self, ref="F1", reason="happy with this for now", by="sammy",
              **kw):
        return transport.waive_finding(self.cfg, self.ledger, ref, reason,
                                       by, LINEAGE, **kw)

    def advance(self, reason="ship", by="sammy"):
        return transport.authorize_advance(self.cfg, self.ledger, reason, by, LINEAGE)

    def standing_ids(self):
        return [f["id"] for f in self.ledger.standing_findings(LINEAGE)]

    def errors(self, envelope):
        parsed = wire.parse_authorization(envelope)
        return [i.code for i in validate.validate_authorization(parsed,
                                                                self.cfg)
                if i.level == "error"]

    def answer(self, kind, term, round_no, fp=FP1, fid="F1"):
        """Record one answer of the given kind at the given round, through
        the verb where one exists (the waiver) and as the ledger event the
        verdict/disposition ingestion writes otherwise."""
        if kind == "closure":
            ev = {"event": "closure", "round": round_no, "fp": fp,
                  "closure": term, "note": "n"}
            if term == "test_amendment":
                ev["outcome"] = "ratified"
            self.ledger.add(ev)
        elif kind == "disposition":
            self.ledger.add({"event": "disposition", "round": round_no,
                             "finding_id": fid, "fp": fp,
                             "disposition": term, "payload": {}})
        else:
            self.waive(ref=fp)


class TestTheAuthoritysAnswer(_Base):

    def test_a_waiver_records_the_finding_the_reason_and_who_decided(self):
        self.rule()
        rec = self.waive(destination="briefs/x.md", trigger="when it bites")
        self.assertEqual((rec["finding_id"], rec["fp"]), ("F1", FP1))
        self.assertEqual(rec["authorized_by"], "sammy")
        self.assertEqual(rec["destination"], "briefs/x.md")
        events = [e for e in self.ledger.current(LINEAGE)
                  if e["event"] == vocab.FINDING_WAIVER_EVENT]
        self.assertEqual(len(events), 1, "the answer is not in the ledger")
        self.assertEqual(events[0]["severity"], "Medium",
                         "the severity overruled is part of the record")
        self.assertEqual(events[0]["round"], 1,
                         "the waiver binds to the ruling it answers")

    def test_the_finding_does_not_die_by_omission(self):
        """The symmetry rule survives this feature rather than being
        loosened by it: the finding is answered by a RECORDED decision, and
        the record names all three of what, why and who.

        MUTATION: drop any of `fp`, `reason` or `authorized_by` from the
        event in `transport.waive_finding` and this fails — which is the
        difference between an override and a finding quietly going away."""
        self.rule()
        self.waive()
        event = [e for e in self.ledger.current(LINEAGE)
                 if e["event"] == vocab.FINDING_WAIVER_EVENT][0]
        for key in ("fp", "reason", "authorized_by"):
            self.assertTrue(str(event.get(key, "")).strip(), key)

    def test_a_silent_reason_or_authorizer_refuses(self):
        """Both halves, because either alone records that a decision
        happened and not what it was, or that one was taken and not by
        whom — the two silences this event exists to replace."""
        self.rule()
        for reason, by in (("", "sammy"), ("   ", "sammy"),
                           ("a reason", ""), ("a reason", "  ")):
            with self.subTest(reason=reason, by=by):
                with self.assertRaises(transport.Refusal):
                    self.waive(reason=reason, by=by)
        self.assertEqual([e for e in self.ledger.current(LINEAGE)
                          if e["event"] == vocab.FINDING_WAIVER_EVENT], [],
                         "a refused waiver appended anyway")

    def test_a_finding_that_was_never_ruled_cannot_be_overruled(self):
        self.rule()
        with self.assertRaises(transport.Refusal) as ctx:
            self.waive(ref="F9")
        self.assertIn("F9", str(ctx.exception))

    def test_an_id_naming_two_findings_refuses_and_asks_for_the_fingerprint(
            self):
        """A finding id is ROUND-SCOPED: `F1` in round 1 and `F1` in round 2
        are different findings sharing a label. Picking the newest would be
        the tool guessing at an identity it computes everywhere else.

        MUTATION: resolve to `matched[0]` without the fps check and this
        fails — the waiver then lands on whichever round the sort happened
        to put first."""
        self.rule(findings=[_finding()])
        self.rule(findings=[_finding(fp=FP2, round_no=2)], round_no=2,
                  sha=SHA2)
        with self.assertRaises(transport.Refusal) as ctx:
            self.waive(ref="F1")
        self.assertIn("round-scoped", str(ctx.exception))
        # Paired control: the fingerprint is unambiguous and is accepted.
        self.assertEqual(self.waive(ref=FP2)["fp"], FP2)

    def test_an_id_shared_by_two_rulings_of_one_identity_binds_the_newest(
            self):
        """`F1` in rounds 1 and 2 naming ONE identity is not ambiguous — it
        is one finding re-raised — and the waiver answers the ruling that
        currently stands, so it binds to round 2 (round 4 F1).

        MUTATION: take `matched[0]` as the ruling and the recorded round is
        whichever the ledger listed first."""
        self.rule(findings=[_finding()])
        self.rule(findings=[_finding(round_no=2)], round_no=2, sha=SHA2)
        self.waive(ref="F1")
        event = self.ledger.current_waivers(LINEAGE)[FP1]
        self.assertEqual(event["round"], 2)

    def test_one_finding_takes_one_answer(self):
        """A second waiver would let one reason hide another, which is the
        same defect the disposition grammar refuses for repeated members."""
        self.rule()
        self.waive(reason="first reason")
        with self.assertRaises(transport.Refusal) as ctx:
            self.waive(reason="second reason")
        self.assertIn("already overruled", str(ctx.exception))

    def test_whether_the_author_escalated_it_is_recorded_either_way(self):
        """Escalated-then-answered and overruled-unprompted are both
        legitimate and they are different facts, so the record carries
        which one happened rather than leaving a reader to assume the
        tidier of the two."""
        self.rule()
        self.assertFalse(self.waive()["answers_escalation"])
        # Paired control: the same finding, put to the authority first.
        other = _Base("run")
        other.setUp()
        other.rule(findings=[_finding(fid="F2", fp=FP2)])
        other.ledger.add({
            "event": "disposition", "round": 1, "finding_id": "F2",
            "fp": FP2, "disposition": "escalated",
            "payload": {"authority": "sammy", "criterion": "ship or not"}})
        self.assertTrue(
            transport.waive_finding(other.cfg, other.ledger, "F2", "r",
                                    "sammy",
                                    LINEAGE)["answers_escalation"])

    def test_the_record_is_an_assertion_not_a_proof(self):
        """Stated in the suite so nobody has to infer it from the docstring
        of a verb they did not read: the tool cannot observe who ran a
        command, so `--by` is carried, never verified. An agent can type a
        human's name here, and the protection against that lives in the
        instructions each agent operates under — not in this code. Anything
        that later claims this artifact PROVES human authorship is wrong,
        and this test is where that claim breaks."""
        self.rule()
        rec = self.waive(by="a name nobody verified")
        self.assertEqual(rec["authorized_by"], "a name nobody verified")


class TestAdvancingIsNeverClean(_Base):

    def test_the_artifact_is_its_own_kind_and_is_not_a_clean_verdict(self):
        """The core property. A derived clean verdict would make an
        overridden review indistinguishable from one where the reviewer
        found nothing, at exactly the moment the difference matters.

        MUTATION: emit `VERDICT: clean to advance` here instead and this
        fails on both assertions — the kind AND the absence of the clean
        term."""
        self.rule()
        self.waive()
        env = self.advance("shipping over one open finding")["envelope"]
        self.assertEqual(wire.detect_kind(env), vocab.AUTHORIZATION_KIND)
        self.assertNotIn(vocab.VERDICT_CLEAN, env)

    def test_it_names_every_overruled_finding_and_the_authorizer(self):
        self.rule(findings=[_finding(), _finding(fid="F2", fp=FP2)])
        self.waive(ref="F1", reason="first")
        self.waive(ref="F2", reason="second")
        rec = self.advance()
        self.assertEqual(sorted(rec["waived"]), ["F1", "F2"])
        for token in ("F1", "F2", "first", "second", "sammy"):
            self.assertIn(token, rec["envelope"], token)

    def test_a_clean_lineage_refuses_to_be_authorized(self):
        """An authorization beside a clean verdict would claim a human
        overruled findings that no longer stand."""
        self.rule()
        self.waive()
        self.ledger.add({"event": "verdict", "round": 2, "sha": SHA2,
                         "verdict": vocab.VERDICT_CLEAN, "bytes": 1,
                         "finding_ids": 0})
        with self.assertRaises(transport.Refusal) as ctx:
            self.advance()
        self.assertIn(vocab.VERDICT_CLEAN, str(ctx.exception))

    def test_advancing_over_nothing_refuses(self):
        """With no finding overruled there is nothing for a human to
        authorize, and a clean verdict is the artifact that says so."""
        self.rule()
        with self.assertRaises(transport.Refusal):
            self.advance()

    def test_a_lineage_with_no_ruling_has_nothing_to_advance_past(self):
        with self.assertRaises(transport.Refusal):
            self.advance()

    def test_a_silent_reason_or_authorizer_refuses(self):
        self.rule()
        self.waive()
        for reason, by in (("", "sammy"), ("ship", "")):
            with self.subTest(reason=reason, by=by):
                with self.assertRaises(transport.Refusal):
                    self.advance(reason, by)

    def test_the_lineage_closes_as_an_authorization_not_a_decision(self):
        """`close --lineage` already ends a review by decision; this is a
        different outcome and the ledger keeps them apart, so a later
        reader can tell an abandoned lineage from an advanced one."""
        self.rule()
        self.waive()
        self.advance()
        # Read the whole file: closing the lineage is what ENDS `current()`,
        # so the marker this asserts on is never inside it.
        self.assertEqual([e["outcome"]
                          for e in self.ledger.closures_of_lineage(LINEAGE)],
                         ["authorization"])
        self.assertEqual([e["event"] for e in self.ledger.events()
                          if e["event"] == vocab.ADVANCE_EVENT],
                         [vocab.ADVANCE_EVENT])

    def test_the_emitted_authorization_validates(self):
        """The bytes the verb keeps are bytes `loupe validate` accepts —
        the property round 4 F4 found broken for two ordinary names."""
        self.rule()
        self.waive()
        self.assertEqual(self.errors(self.advance()["envelope"]), [])


class TestTheLifecycleTableIsClosed(unittest.TestCase):
    """`vocab.FINDING_ANSWERS` is closed against the vocabularies it
    classifies, so a closure term or a disposition cannot join the grammar
    and leave the standing set to guess at its effect.

    MUTATION: add a term to `vocab.CLOSURES` or `vocab.DISPOSITIONS` without
    a row here and this fails by name."""

    def test_every_answer_kind_the_vocab_declares_is_classified(self):
        want = ({("closure", c) for c in vocab.CLOSURES}
                | {("disposition", d) for d in vocab.DISPOSITIONS}
                | {(vocab.FINDING_WAIVER_EVENT, None)})
        self.assertEqual(set(vocab.FINDING_ANSWERS), want)

    def test_every_effect_is_one_of_the_three(self):
        self.assertTrue(set(vocab.FINDING_ANSWERS.values()) <= {
            vocab.ANSWER_SETTLES, vocab.ANSWER_OVERRULES, vocab.ANSWER_OPEN})
        # The two deaths inside the loop and the one human answer, named:
        # the table would be trivially closed if it said `open` everywhere.
        self.assertEqual(vocab.FINDING_ANSWERS[("closure", "withdrawn")],
                         vocab.ANSWER_SETTLES)
        self.assertEqual(vocab.FINDING_ANSWERS[("disposition", "accepted")],
                         vocab.ANSWER_SETTLES)
        self.assertEqual(
            vocab.FINDING_ANSWERS[(vocab.FINDING_WAIVER_EVENT, None)],
            vocab.ANSWER_OVERRULES)


class TestEveryAnswerIsBoundToTheRulingItAnswers(_Base):
    """The derived matrix: every answer kind in `vocab.FINDING_ANSWERS`,
    recorded against the CURRENT ruling and against an EARLIER ruling of a
    re-raised identity. Current answers act by their effect; stale answers
    act as nothing at all, whatever their kind.

    Rounds 3 and 4 found the three kinds bound three ways — an acceptance
    to its round, a withdrawal and a waiver to the identity for all time —
    so a finding withdrawn or waived in round 1 and re-raised in round 2
    left the standing set with nobody having answered the round-2 ruling.

    MUTATION: drop the round comparison in `Ledger.current_answers` and
    every `stale` subtest for a settling or overruling kind fails; special-
    case any one kind back to identity-wide binding and that kind's stale
    subtest fails alone, which is how the matrix names the drift."""

    def kinds(self):
        for (kind, term), effect in sorted(
                vocab.FINDING_ANSWERS.items(), key=lambda kv: str(kv[0])):
            yield kind, term, effect

    def test_a_current_answer_acts_by_its_effect(self):
        for kind, term, effect in self.kinds():
            with self.subTest(kind=kind, term=term, effect=effect):
                self.setUp()
                self.rule(findings=[_finding()])
                self.answer(kind, term, round_no=1)
                waived = FP1 in self.ledger.current_waivers(LINEAGE)
                if effect == vocab.ANSWER_SETTLES:
                    self.assertEqual(self.standing_ids(), [])
                    self.assertFalse(waived)
                    with self.assertRaises(transport.Refusal) as ctx:
                        self.advance()
                    self.assertIn("nothing", str(ctx.exception))
                elif effect == vocab.ANSWER_OVERRULES:
                    self.assertEqual(self.standing_ids(), ["F1"],
                                     "a waiver records that the finding "
                                     "STANDS unfixed; it must not shrink "
                                     "the set the artifact enumerates")
                    self.assertTrue(waived)
                    self.assertEqual(self.advance()["waived"], ["F1"])
                else:
                    self.assertEqual(self.standing_ids(), ["F1"])
                    self.assertFalse(waived)
                    with self.assertRaises(transport.Refusal) as ctx:
                        self.advance()
                    self.assertIn("F1", str(ctx.exception))

    def test_a_stale_answer_leaves_the_reraised_ruling_open(self):
        for kind, term, effect in self.kinds():
            with self.subTest(kind=kind, term=term, effect=effect):
                self.setUp()
                self.rule(findings=[_finding()])
                self.answer(kind, term, round_no=1)
                # The reviewer rules again and the same identity returns.
                self.rule(findings=[_finding(fid="F9", round_no=2)],
                          round_no=2, sha=SHA2)
                self.assertEqual(self.standing_ids(), ["F9"],
                                 "a re-raised finding is open again")
                self.assertNotIn(FP1, self.ledger.current_waivers(LINEAGE))
                with self.assertRaises(transport.Refusal) as ctx:
                    self.advance()
                self.assertIn("F9", str(ctx.exception),
                              "the refusal names the CURRENT ruling")

    def test_an_answer_in_the_latest_round_still_settles_or_overrules(self):
        """The paired control, so the rule is not simply 'answers never
        count': the same kinds recorded against the round-2 ruling act
        exactly as they do against a single ruling."""
        for kind, term, effect in self.kinds():
            with self.subTest(kind=kind, term=term, effect=effect):
                self.setUp()
                self.rule(findings=[_finding()])
                self.rule(findings=[_finding(fid="F9", round_no=2)],
                          round_no=2, sha=SHA2)
                self.answer(kind, term, round_no=2, fid="F9")
                if effect == vocab.ANSWER_SETTLES:
                    self.assertEqual(self.standing_ids(), [])
                elif effect == vocab.ANSWER_OVERRULES:
                    self.assertEqual(self.advance()["waived"], ["F9"])
                else:
                    self.assertEqual(self.standing_ids(), ["F9"])


class TestTheAuthorizationEnumeratesTheStandingSet(_Base):
    """Lineage 20 round 3 F2 and round 4 F1-F3: the envelope's `waived`
    list IS the standing set, each record carrying the CURRENT ruling's
    facts and the CURRENT waiver's decision — never a stale waiver's copy
    of an older ruling, and never a partial set.

    MUTATION, run: drop the `unanswered` refusal and the partial row
    closes; drop the `open_round` refusal and the pending row closes; drop
    the round comparison in `Ledger.current_answers` and the two re-raised
    rows advance on the stale answer — the waiver row emitting `F1`, the
    older ruling's id, instead of `F9`. Reading the record's facts from the
    ruling rather than the waiver is not separately observable once the
    binding holds: a CURRENT waiver was recorded against that ruling and
    carries its facts. It is the rule that made the stale-waiver row emit
    the wrong id, and the binding is what makes it unreachable.
    """

    def two_open(self):
        self.rule(findings=[_finding(), _finding(fid="F2", fp=FP2)])

    def test_one_of_two_waived_cannot_advance(self):
        self.two_open()
        self.waive(ref="F1")
        with self.assertRaises(transport.Refusal) as ctx:
            self.advance()
        message = str(ctx.exception)
        self.assertIn("F2", message, "the refusal must NAME what is unanswered")
        self.assertIn("die of omission", message)

    def test_both_waived_advances_and_enumerates_the_derived_set(self):
        """The paired valid control, and the completeness property."""
        self.two_open()
        self.waive(ref="F1", reason="first")
        self.waive(ref="F2", reason="second")
        # Captured BEFORE the advance: closing the lineage is what ends
        # `current()`, so the authority must be read while the lineage the
        # artifact describes is still the current one.
        standing = set(self.standing_ids())
        rec = self.advance()
        self.assertEqual(sorted(rec["waived"]), ["F1", "F2"])
        self.assertEqual(set(rec["waived"]), standing,
                         "the artifact must enumerate the derived set")

    def test_a_newer_request_awaiting_a_verdict_blocks_the_advance(self):
        """Otherwise the authorization binds to the older ruled SHA while
        the loop has already moved past it — an approval for a commit
        nobody is reviewing any more."""
        self.rule()
        self.waive()
        self.ledger.add({"event": "request", "round": 2, "sha": SHA2,
                         "bytes": 1, "author": "claude", "reviewer": "codex"})
        self.assertEqual(self.ledger.open_round(LINEAGE), 2)
        with self.assertRaises(transport.Refusal) as ctx:
            self.advance()
        self.assertIn("awaiting a verdict", str(ctx.exception))

    def test_a_stale_waiver_does_not_answer_a_reraised_finding(self):
        """Round 4 F1, the reviewer's own row. F1 ruled in round 1, waived,
        re-raised as F9 in round 2: the round-1 waiver is an answer to a
        ruling that no longer stands. The advance refuses naming F9 until a
        round-2 waiver exists, and then emits F9 — the current ruling — not
        the F1 the stale waiver remembered."""
        self.rule(findings=[_finding()])
        self.waive(ref="F1", reason="round-1 reason")
        self.rule(findings=[_finding(fid="F9", round_no=2, severity="High")],
                  round_no=2, sha=SHA2)
        with self.assertRaises(transport.Refusal) as ctx:
            self.advance()
        self.assertIn("F9 (High)", str(ctx.exception))
        # Paired control: the current ruling, answered now.
        self.waive(ref=FP1, reason="round-2 reason")
        rec = self.advance()
        self.assertEqual(rec["waived"], ["F9"])
        body = wire.parse_authorization(rec["envelope"]).data
        (record,) = body["waived"]
        self.assertEqual((record["finding_id"], record["severity"],
                          record["title"], record["reason"]),
                         ("F9", "High", "F9 title", "round-2 reason"))
        self.assertNotIn("F1", rec["envelope"].replace("F1 title", ""))

    def test_a_stale_withdrawal_does_not_settle_a_reraised_finding(self):
        """Round 4 F2, the reviewer's own row. Withdrawn in round 1,
        re-raised in round 2 beside F2; only F2 waived. The re-raised
        identity stands and blocks the advance; a withdrawal in the latest
        ruled round still removes it."""
        self.rule(findings=[_finding()])
        self.answer("closure", "withdrawn", round_no=1)
        self.assertEqual(self.standing_ids(), [])
        self.rule(findings=[_finding(round_no=2),
                            _finding(fid="F2", fp=FP2, round_no=2)],
                  round_no=2, sha=SHA2)
        self.waive(ref="F2")
        self.assertEqual(self.standing_ids(), ["F1", "F2"])
        with self.assertRaises(transport.Refusal) as ctx:
            self.advance()
        self.assertIn("F1", str(ctx.exception))
        # Paired control: withdrawn against the ruling that stands.
        self.answer("closure", "withdrawn", round_no=2)
        self.assertEqual(self.standing_ids(), ["F2"])
        self.assertEqual(self.advance()["waived"], ["F2"])

    def test_two_standing_findings_sharing_a_label_are_both_emitted_and_validate(
            self):
        """Round 4 F3 through the emitter, not a hand-built envelope: `F1`
        of round 1 (never answered) and `F1` of round 2 (a different
        identity) are two standing findings both labelled F1. The tool
        emits both and its own validator accepts what it emitted."""
        self.rule(findings=[_finding()])
        self.rule(findings=[_finding(fp=FP2, round_no=2)], round_no=2,
                  sha=SHA2)
        self.assertEqual(self.standing_ids(), ["F1", "F1"])
        self.waive(ref=FP1, reason="one")
        self.waive(ref=FP2, reason="two")
        rec = self.advance()
        self.assertEqual(rec["waived"], ["F1", "F1"])
        self.assertEqual(self.errors(rec["envelope"]), [])


class TestTheAuthorizerIsRepresentable(_Base):
    """Round 4 F4. `by` is stamped into a double-quoted wrapper attribute
    with no escape, so `Alice "The Decider"` and `Alice > Bob` were
    accepted, the lineage closed, and the kept bytes failed validation.
    The answer is a REFUSAL by one grammar (`vocab.AUTHORIZER_NAME_RE`),
    shared by the recording verbs and the validator, and a validation of
    the emitted artifact before anything is kept or recorded.

    The partition is derived from the grammar over the complete Basic
    Latin and Latin-1 ranges plus a sample of wider scripts: every code
    point the grammar admits round-trips through the wrapper and
    validates; every one it refuses is refused by the verb before any
    record is written.

    MUTATION, run: drop `_refuse_unrepresentable_name` from the verbs and
    the refused rows record a waiver, close the lineage, and keep bytes
    that fail validation; drop the pre-mutation validation in
    `authorize_advance` and the emitter-drift row closes the lineage on
    an artifact the validator rejects."""

    GRAMMAR = re.compile(vocab.AUTHORIZER_NAME_RE)
    ADMITTED = ["sammy", "Mary O'Brien", "José Álvarez-Núñez", "Alice & Bob",
                "a.b_c-d (ops)", "山田 太郎", "x=y; z"]
    REFUSED = ['Alice "The Decider"', "Alice > Bob", "Alice < Bob", "a\tb",
               "a\nb", "a\x00b", "a\x7fb", "a\rb"]

    def snapshot(self):
        return (list(self.ledger.events()),
                sorted(p.name for p in self.tmp.rglob("*") if p.is_file()))

    def test_the_partition_is_the_grammars(self):
        """Every code point below U+0300, plus the wider samples above,
        falls on exactly the side the grammar says — and each admitted
        one is carried by the wrapper unchanged."""
        for cp in range(0x300):
            ch = chr(cp)
            name = f"a{ch}b"
            admitted = bool(self.GRAMMAR.match(name))
            with self.subTest(codepoint=hex(cp), admitted=admitted):
                if admitted:
                    env = wire.emit_authorization(
                        self.cfg.wrapper_tag, sha=SHA, round_no=1,
                        lineage=1, by=name, reason="r",
                        waived=[{"finding_id": "F1", "fp": FP1,
                                 "reason": "r", "by": name}])
                    parsed = wire.parse_authorization(env)
                    self.assertEqual(parsed.attrs.get("by"), name)
                    self.assertEqual(self.errors(env), [])
                else:
                    with self.assertRaises(transport.Refusal):
                        transport._refuse_unrepresentable_name(name)
        for name in self.ADMITTED:
            self.assertTrue(self.GRAMMAR.match(name), name)
        for name in self.REFUSED:
            self.assertFalse(self.GRAMMAR.match(name), repr(name))

    def test_every_admitted_name_advances_with_a_validating_artifact(self):
        for name in self.ADMITTED:
            with self.subTest(by=name):
                self.setUp()
                self.rule()
                self.waive(by=name)
                rec = self.advance(by=name)
                self.assertEqual(self.errors(rec["envelope"]), [])
                self.assertEqual(Path(rec["kept"]).read_text("utf-8"),
                                 rec["envelope"])
                self.assertEqual(
                    wire.parse_authorization(rec["envelope"]).data["by"],
                    name)

    def test_every_refused_name_leaves_the_record_untouched(self):
        for name in self.REFUSED:
            with self.subTest(by=name):
                self.setUp()
                self.rule()
                before = self.snapshot()
                with self.assertRaises(transport.Refusal) as ctx:
                    self.waive(by=name)
                self.assertIn(vocab.AUTHORIZER_NAME_WANT, str(ctx.exception))
                self.assertEqual(self.snapshot(), before,
                                 "a refused waiver wrote something")
                self.waive(by="sammy")
                before = self.snapshot()
                with self.assertRaises(transport.Refusal):
                    self.advance(by=name)
                self.assertEqual(self.snapshot(), before,
                                 "a refused advance wrote or recorded")
                self.assertEqual(self.ledger.closures_of_lineage(LINEAGE), [])

    def test_emitter_drift_cannot_commit_an_invalid_terminal_state(self):
        """The validation runs on the bytes the emitter actually produced,
        before `keep_bytes` and before either ledger event — so an emitter
        that drifts later is caught by the verb, not by the reader."""
        self.rule()
        self.waive()
        before = self.snapshot()
        real = wire.emit_authorization

        def drifted(*a, **kw):
            return real(*a, **kw).replace(f'sha="{SHA}"', 'sha="drifted"', 1)

        with mock.patch.object(transport.wire, "emit_authorization", drifted):
            with self.assertRaises(transport.Refusal) as ctx:
                self.advance()
        self.assertIn("does not validate", str(ctx.exception))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.ledger.closures_of_lineage(LINEAGE), [])
        # Paired control: the undrifted emitter advances.
        self.assertEqual(self.advance()["waived"], ["F1"])


class TestALegacyAtomicRulingIsARuling(_Base):
    """Round-9 F3 — FALSIFICATION, the synthetic half.

    `findings_in_round` counted a legacy `import` of kind `atomic` as a
    ruling; `latest_rulings`, `_finding_rounds_by_identity` (and so
    `current_answers`, `standing_findings` and `_ruling_withdrawn`) and
    `convergence` each filtered `finding` events alone. One ruling
    authority (`ledger.is_ruling`) now feeds all of them, so an answer
    binds to a legacy ruling exactly as it binds to a reviewer's, and an
    unanswered one stays in the cohort an advance must enumerate.

    MUTATION: put `_all_rulings` back to filtering `event == "finding"` and
    every case here fails — the atomic ruling vanishes from the standing
    set instead of standing, and its answers bind to nothing.
    """

    LEGACY_FP = "fp2:" + "7" * 16

    def atomic(self, round_no=1, fp=None, legacy_id="R1-F1a", **extra):
        row = {"event": "import", "kind": "atomic", "round": round_no,
               "legacy_id": legacy_id, "fp": fp or self.LEGACY_FP,
               "severity": "High", "classification": "design_gap",
               "verbatim": f"{legacy_id} claim text",
               "source_digest": "d" * 64}
        row.update(extra)
        self.ledger.add(row)
        return row

    def rule_legacy(self, round_no=1, **kw):
        """A round whose ruling is a legacy atomic import rather than a
        reviewer's finding — the shape every imported first round has."""
        self.ledger.add({"event": "request", "round": round_no, "sha": SHA,
                         "bytes": 1})
        self.ledger.add({"event": "verdict", "round": round_no, "sha": SHA,
                         "verdict": "changes requested", "bytes": 1,
                         "finding_ids": 1})
        return self.atomic(round_no=round_no, **kw)

    def test_an_unanswered_atomic_ruling_stands(self):
        self.rule_legacy()
        standing = self.ledger.standing_findings(LINEAGE)
        self.assertEqual([f["fp"] for f in standing], [self.LEGACY_FP])
        self.assertEqual(standing[0]["id"], "R1-F1a",
                         "the legacy row's own display facts, not None")
        self.assertEqual(standing[0]["title"], "R1-F1a claim text")
        self.assertIsNone(standing[0].get("anchor_path"),
                          "and no anchor invented for a row without one")

    def test_an_accepted_atomic_ruling_settles(self):
        self.rule_legacy()
        self.ledger.add({"event": "disposition", "round": 1,
                         "finding_id": "R1-F1a", "fp": self.LEGACY_FP,
                         "disposition": "accepted",
                         "payload": {"change": "x", "verification": "y"}})
        self.assertEqual(self.ledger.standing_findings(LINEAGE), [])
        self.assertEqual(
            [e for e, _ in self.ledger.current_answers(LINEAGE)[self.LEGACY_FP]],
            [vocab.ANSWER_SETTLES])

    def test_a_deferred_atomic_ruling_keeps_standing(self):
        self.rule_legacy()
        self.ledger.add({"event": "disposition", "round": 1,
                         "finding_id": "R1-F1a", "fp": self.LEGACY_FP,
                         "disposition": "deferred",
                         "payload": {"destination": "briefs/x.md",
                                     "trigger": "when it bites"}})
        self.assertEqual([f["fp"] for f in self.ledger.standing_findings(LINEAGE)],
                         [self.LEGACY_FP])

    def test_a_withdrawal_binds_to_the_atomic_ruling_it_targets(self):
        self.rule_legacy()
        self.rule(findings=[_finding(round_no=2)], round_no=2, sha=SHA2)
        self.ledger.add({"event": "closure", "round": 2, "fp": self.LEGACY_FP,
                         "closure": "withdrawn", "note": "n"})
        self.assertTrue(self.ledger._ruling_withdrawn(self.LEGACY_FP, 1, LINEAGE))
        self.assertEqual([f["fp"] for f in self.ledger.standing_findings(LINEAGE)],
                         [FP1], "the round-2 finding, and nothing else")

    def test_a_re_raised_atomic_ruling_reopens(self):
        # The `stale` rule holds for a legacy identity too: an answer to
        # round 1's ruling is no answer to round 2's.
        self.rule_legacy()
        self.ledger.add({"event": "closure", "round": 1, "fp": self.LEGACY_FP,
                         "closure": "withdrawn", "note": "n",
                         "answers_round": 1})
        self.assertEqual(self.ledger.standing_findings(LINEAGE), [])
        self.rule(findings=[self.atomic(round_no=2)], round_no=2, sha=SHA2)
        self.assertEqual([f["round"] for f in self.ledger.standing_findings(LINEAGE)],
                         [2])
        self.assertFalse(self.ledger._ruling_withdrawn(self.LEGACY_FP, 2, LINEAGE))
        self.assertTrue(self.ledger._ruling_withdrawn(self.LEGACY_FP, 1, LINEAGE))

    def test_an_advance_must_enumerate_a_standing_atomic_ruling(self):
        self.rule_legacy()
        with self.assertRaises(transport.Refusal) as ctx:
            self.advance()
        self.assertIn("R1-F1a", str(ctx.exception))
        self.waive(ref=self.LEGACY_FP)
        self.assertEqual(self.advance()["waived"], ["R1-F1a"])

    def test_convergence_reads_an_anchor_less_ruling_without_grouping_it(self):
        self.rule_legacy()
        self.rule(findings=[_finding(round_no=2)], round_no=2, sha=SHA2)
        c = self.ledger.convergence(LINEAGE)
        self.assertEqual(c["findings_per_round"], {1: 1, 2: 1})
        self.assertIn(self.LEGACY_FP, c["threads"])
        self.assertIsNone(c["threads"][self.LEGACY_FP]["anchor"])
        self.assertEqual(c["hunted_anchors"], {},
                         "a ruling that names no anchor is evidence about "
                         "none, and must not join a group under an absent "
                         "key")


class TestTheHandoffPreflightReadsTheRulingAuthority(_Base):
    """Round-10 F3 — FALSIFICATION. Round 9 unified `findings_in_round`,
    `latest_rulings`, `_finding_rounds_by_identity`, `convergence` and the
    breakers onto `ledger.is_ruling`, and left one lifecycle reader behind:
    `transport.missing_dispositions`, which `handoff_preflight` uses to
    enforce that a finding never dies by omission, still filtered
    `ledger._by("finding")`.

    So a completed round whose only unanswered ruling was a legacy ATOMIC
    IMPORT was reported as owing nothing — `missing_dispositions` returned
    None and the handoff opened the next round over a finding nobody had
    answered — while an equivalent ordinary finding correctly refused. The
    two must be indistinguishable here, and the ordinary case is the paired
    control that says the refusal itself still works.

    MUTATION: put `missing_dispositions` back to `_by("finding")` and the
    atomic cases below wrongly return None while the ordinary control still
    refuses — which is exactly the asymmetry that shipped.
    """

    LEGACY_FP = "fp2:" + "7" * 16

    def atomic_round(self, round_no=1, **extra):
        self.ledger.add({"event": "request", "round": round_no, "sha": SHA,
                         "bytes": 1})
        self.ledger.add({"event": "verdict", "round": round_no, "sha": SHA,
                         "verdict": "changes requested", "bytes": 1,
                         "finding_ids": 1})
        row = {"event": "import", "kind": "atomic", "round": round_no,
               "legacy_id": "R1-F1a", "fp": self.LEGACY_FP,
               "severity": "High", "classification": "design_gap",
               "verbatim": "R1-F1a claim text", "source_digest": "d" * 64}
        row.update(extra)
        self.ledger.add(row)

    def test_an_unanswered_atomic_ruling_is_owed_exactly_as_a_finding_is(self):
        self.atomic_round()
        owed = transport.missing_dispositions(self.ledger,LINEAGE)
        self.assertIsNotNone(
            owed, "an unanswered atomic ruling owes a disposition; returning "
                  "None is a finding dying by omission")
        self.assertEqual(owed["round"], 1)
        self.assertEqual(owed["findings"], 1)
        self.assertEqual([u["fp"] for u in owed["unanswered"]],
                         [self.LEGACY_FP])
        self.assertEqual([u["id"] for u in owed["unanswered"]], ["R1-F1a"],
                         "the display id comes through `ruling_facts`, not "
                         "as the None a legacy row's absent `id` would give")
        self.assertEqual([u["severity"] for u in owed["unanswered"]], ["High"])

    def test_the_ordinary_finding_control_reports_the_same_shape(self):
        # The equivalence the finding demands: an ordinary finding in the
        # same position produces the same payload shape and the same refusal.
        self.rule(findings=[_finding(round_no=1)], round_no=1)
        owed = transport.missing_dispositions(self.ledger,LINEAGE)
        self.assertEqual(owed["round"], 1)
        self.assertEqual(owed["findings"], 1)
        self.assertEqual([u["id"] for u in owed["unanswered"]], ["F1"])

    def test_the_preflight_refuses_the_handoff_and_names_the_ruling(self):
        self.atomic_round()
        with self.assertRaises(transport.Refusal) as ctx:
            transport.handoff_preflight(self.cfg, self.ledger, LINEAGE)
        self.assertIn("R1-F1a", str(ctx.exception))
        self.assertIn(self.LEGACY_FP, str(ctx.exception))

    def test_an_accepted_atomic_ruling_owes_nothing(self):
        self.atomic_round()
        self.ledger.add({"event": "disposition", "round": 1,
                         "finding_id": "R1-F1a", "fp": self.LEGACY_FP,
                         "disposition": "accepted",
                         "payload": {"change": "x", "verification": "y"}})
        self.assertIsNone(transport.missing_dispositions(self.ledger,LINEAGE))
        transport.handoff_preflight(self.cfg, self.ledger, LINEAGE)

    def test_a_deferred_atomic_ruling_is_answered_too(self):
        # `deferred` leaves the finding STANDING but it is an answer, and
        # this function asks whether one was recorded, not what it said.
        self.atomic_round()
        self.ledger.add({"event": "disposition", "round": 1,
                         "finding_id": "R1-F1a", "fp": self.LEGACY_FP,
                         "disposition": "deferred",
                         "payload": {"destination": "briefs/x.md",
                                     "trigger": "when it bites"}})
        self.assertIsNone(transport.missing_dispositions(self.ledger,LINEAGE))

    def test_a_mixed_round_owes_only_the_unanswered_one(self):
        self.rule(findings=[_finding(round_no=1)], round_no=1)
        self.ledger.add({"event": "import", "kind": "atomic", "round": 1,
                         "legacy_id": "R1-F1a", "fp": self.LEGACY_FP,
                         "severity": "High", "classification": "design_gap",
                         "verbatim": "R1-F1a claim text",
                         "source_digest": "d" * 64})
        self.ledger.add({"event": "disposition", "round": 1,
                         "finding_id": "F1", "fp": FP1,
                         "disposition": "accepted",
                         "payload": {"change": "x", "verification": "y"}})
        owed = transport.missing_dispositions(self.ledger,LINEAGE)
        self.assertEqual(owed["findings"], 2,
                         "both rulings are counted, whatever kind they are")
        self.assertEqual([u["id"] for u in owed["unanswered"]], ["R1-F1a"])


class TestOwedRulingsAreNotAnchoredToTheVerdictRound(_Base):
    """Round-11 F5 — FALSIFICATION (`lineage-20-residue`).

    `missing_dispositions` took the MAXIMUM verdict round and looked for
    owed rulings in that round alone, so a ruling made in any other round
    could not be owed however loudly it was unanswered. The case the
    verdict text names: a completed round-1 verdict, then an atomic ruling
    imported at round 2 with no verdict artifact of its own. The preflight
    must name it.

    Paired control: the same round-2 ruling with a recorded disposition
    owes nothing, so the check is not simply refusing everything.

    MUTATION: restore the anchor — `last = max(verdict rounds)` and
    `findings_in_round(last)` — and the round-2 import below is reported as
    owing nothing while the control still passes, which is what shipped.
    """

    LEGACY_FP = "fp2:" + "9" * 16

    def import_at(self, round_no):
        self.ledger.add({"event": "import", "kind": "atomic",
                         "round": round_no, "legacy_id": "R2-F1a",
                         "fp": self.LEGACY_FP, "severity": "High",
                         "classification": "design_gap",
                         "verbatim": "R2-F1a claim text",
                         "source_digest": "d" * 64})

    def closed_round_one(self):
        """Round 1: ruled, and its finding answered — so nothing about
        round 1 can be what a later refusal is naming."""
        self.rule(findings=[_finding(round_no=1)], round_no=1)
        self.answer("disposition", "accepted", 1)

    def test_a_ruling_in_a_round_with_no_verdict_is_still_owed(self):
        self.closed_round_one()
        self.import_at(2)
        owed = transport.missing_dispositions(self.ledger,LINEAGE)
        self.assertIsNotNone(
            owed, "a ruling outside the latest VERDICT round is still a "
                  "ruling, and an unanswered one is owed")
        self.assertEqual([u["fp"] for u in owed["unanswered"]],
                         [self.LEGACY_FP])
        self.assertEqual([u["round"] for u in owed["unanswered"]], [2])
        self.assertEqual(owed["findings"], 2, "both rulings are the domain")

    def test_the_preflight_names_it(self):
        self.closed_round_one()
        self.import_at(2)
        with self.assertRaises(transport.Refusal) as ctx:
            transport.handoff_preflight(self.cfg, self.ledger, LINEAGE)
        self.assertIn("R2-F1a", str(ctx.exception))
        self.assertIn(self.LEGACY_FP, str(ctx.exception))

    def test_the_paired_control_answers_it_and_owes_nothing(self):
        self.closed_round_one()
        self.import_at(2)
        self.answer("disposition", "accepted", 2, fp=self.LEGACY_FP,
                    fid="R2-F1a")
        self.assertIsNone(transport.missing_dispositions(self.ledger,LINEAGE))
        transport.handoff_preflight(self.cfg, self.ledger, LINEAGE)

    def test_an_unanswered_ruling_of_an_EARLIER_round_is_owed_too(self):
        # The same defect in the other direction: round 1 unanswered, round
        # 2 ruled and answered. The anchor reported the lineage as owing
        # nothing because it only ever looked at the newest verdict.
        self.rule(findings=[_finding(round_no=1)], round_no=1)
        self.rule(findings=[_finding(fid="F2", fp=FP2, round_no=2)],
                  round_no=2, sha=SHA2)
        self.answer("disposition", "accepted", 2, fp=FP2, fid="F2")
        owed = transport.missing_dispositions(self.ledger,LINEAGE)
        self.assertIsNotNone(owed)
        self.assertEqual([u["id"] for u in owed["unanswered"]], ["F1"])
        self.assertEqual(owed["round"], 1,
                         "the reported round is the newest OWED ruling's, "
                         "not the lineage's newest verdict")

    def test_a_reviewer_withdrawal_answers_it_as_a_disposition_does(self):
        # What ANSWERED means here: omission is the defect, and a ruling
        # the reviewer withdrew or a named human overruled was not omitted.
        self.rule(findings=[_finding(round_no=1)], round_no=1)
        self.answer("closure", "withdrawn", 1)
        self.assertIsNone(transport.missing_dispositions(self.ledger,LINEAGE))

    def test_a_human_waiver_answers_it_too(self):
        self.rule(findings=[_finding(round_no=1)], round_no=1)
        self.waive()
        self.assertIsNone(transport.missing_dispositions(self.ledger,LINEAGE))


if __name__ == "__main__":
    unittest.main()

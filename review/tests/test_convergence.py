"""The round cap advises; `convergence` answers what it was proxying for.

User decision 2026-08-25. A round count is a threshold, not an observed
anomaly: it says how long a loop has run and nothing about whether it is
closing anything, so it refused lineages doing exactly what they should and
stayed silent on ones that were genuinely stuck. Lineage 7 is the case that
forced it — three rounds, every finding real, every fix landing, and the
count was the only thing objecting.

What replaces it measures direction instead of duration, from what the
ledger already holds. Two signals, and the second is the one a count cannot
see at all:

  stalled  a fingerprint the reviewer refuses to withdraw across rounds —
           the loop failing to close one claim.
  hunting  an anchor whose findings are NEW identities round after round.
           Every one may be real and every fix may have landed; the domain
           still never closes, because what is being asked for is
           completeness over a set nobody can enumerate from inside. From
           inside any single round this looks exactly like progress.

Neither is a proof and the report says so where it lives. What it gives a
human is the shape of the loop over its whole length, which no single round
shows.
"""
import json
import os
import unittest
from pathlib import Path

from review import transport, validate, vocab, wire
from review.ledger import Ledger, render_convergence_md
from review.tests import synth
from review.tests._transport_fixtures import (
    run_cli, scratch_loop_repo, scratch_tmp)
from review.tests.util import LINEAGE, REPO_ROOT

CFG = synth.NO_GATES


def _finding(round_no, fp, anchor="review/thing.py", fid="F1"):
    return {"event": "finding", "round": round_no, "id": fid, "fp": fp,
            "severity": "High", "classification": "design_gap",
            "title": "t", "anchor_path": anchor, "preventable_by": None}


def _closure(round_no, fp, outcome):
    return {"event": "closure", "round": round_no, "fp": fp,
            "closure": outcome, "note": "n"}


def _round(ledger, round_no, sha):
    ledger.add({"event": "request", "round": round_no, "sha": sha,
                "bytes": 1, "author": "claude", "reviewer": "codex"})
    ledger.add({"event": "verdict", "round": round_no, "sha": sha,
                "verdict": "changes requested", "bytes": 1, "finding_ids": 1})


class TestTheCapAdvisesAndDoesNotRefuse(unittest.TestCase):

    def test_the_budget_breaker_no_longer_stops_a_handoff(self):
        """FALSIFICATION. Mutation: stop filtering `budget` out of the
        preflight's fired list and this fails — which is the behaviour that
        blocked a legitimate round 4 and taught nothing by blocking it.
        The paired control below proves the filter is not a blanket
        disarming of the breakers."""
        from review import transport
        ledger = Ledger.in_memory()
        for r, sha in ((1, "a" * 40), (2, "b" * 40), (3, "c" * 40),
                       (4, "d" * 40)):
            # Real material each round, so `budget` is the ONLY thing firing
            # — otherwise this would pass on `no-progress` being filtered,
            # which is not what it claims to test.
            _round(ledger, r, sha)
            ledger.add(_finding(r, f"fp2:{r:016x}", anchor=f"review/m{r}.py"))
            ledger.add({"event": "disposition", "round": r,
                        "finding_id": "F1", "fp": f"fp2:{r:016x}",
                        "disposition": "accepted", "payload": {}})
        fired = [f["breaker"] for f in ledger.breakers(LINEAGE,
            3, blocking_severities=["High"])]
        self.assertEqual(sorted(set(fired)), ["budget"],
                         "the breaker must still FIRE and be reported, and "
                         "it must be the only one firing here or this test "
                         "proves nothing about which one was exempted")
        transport.handoff_preflight(CFG, ledger, LINEAGE)  # no raise

    def test_every_other_breaker_still_stops_the_loop(self):
        """The control. `budget` is exempt because it names a threshold;
        every breaker that names an observed anomaly still refuses, and a
        change that disarmed them all would fail here."""
        from review import transport
        ledger = Ledger.in_memory()
        _round(ledger, 1, "a" * 40)
        fp = "fp2:0011223344556677"
        ledger.add(_finding(1, fp))
        ledger.add({"event": "disposition", "round": 1, "finding_id": "F1",
                    "fp": fp, "disposition": "accepted", "payload": {}})
        ledger.add({"event": "falsification_run", "round": 1, "fp": fp,
                    "of": "F1", "status": "pass",
                    "mutation": "fails_without_fix", "test_digest": "td",
                    "source": "disposition", "blocking": True,
                    "batch": "orphan-X"})
        self.assertIn("orphan", [f["breaker"] for f in ledger.breakers(LINEAGE,
            3, blocking_severities=["High"])])
        with self.assertRaises(transport.Refusal):
            transport.handoff_preflight(CFG, ledger, LINEAGE)

    def test_the_notice_names_the_cap_and_what_answers_it(self):
        text = synth.emitted_request()
        over = text.replace('round="2"', f'round="{CFG.round_cap + 5}"')
        items = validate.validate_request(wire.parse_request(over), CFG)
        notice = [i for i in items if i.code == "R-BUDGET"]
        self.assertEqual(len(notice), 1)
        self.assertNotEqual(notice[0].level, "error")
        self.assertIn(str(CFG.round_cap), notice[0].message)
        self.assertIn("convergence", notice[0].message)


class TestOnlyTheRoundThresholdAdvises(unittest.TestCase):
    """Round-4 F2: the first cut exempted every `budget` firing without
    looking at `limit`, which removed the TOKEN-budget stop nobody asked to
    remove. The two are different kinds of fact — a round count is a proxy,
    a token breach is measured against a declared ceiling — and only the
    proxy advises.
    """

    def _spent(self, ledger, request_tokens, verdict_tokens):
        """A completed round whose envelope events carry token counts."""
        ledger.add({"event": "request", "round": 1, "sha": "a" * 40,
                    "bytes": 1, "author": "claude", "reviewer": "codex",
                    "tokens": request_tokens})
        ledger.add({"event": "verdict", "round": 1, "sha": "a" * 40,
                    "verdict": "changes requested", "bytes": 1,
                    "finding_ids": 1, "tokens": verdict_tokens})
        ledger.add(_finding(1, "fp2:0000000000000001"))
        ledger.add({"event": "disposition", "round": 1, "finding_id": "F1",
                    "fp": "fp2:0000000000000001", "disposition": "accepted",
                    "payload": {}})
        return ledger

    def test_a_measured_token_breach_still_refuses(self):
        """FALSIFICATION for round-4 F2, the reviewer's own probe.
        Mutation: filter every `budget` firing regardless of `limit` — the
        state this round was handed back in — and this passes silently
        while the breaker still reports the breach."""
        from review import transport
        # Spent RELATIVE to the configured budget: this probe once spent a
        # literal 210,000 against review.toml's 200,000 and went silent the
        # day the repository raised its budget to 600,000 (2026-09-17,
        # brief codex-cli-review-loop). A probe of the breaker must not
        # depend on the number the repository happens to set.
        ledger = self._spent(Ledger.in_memory(), CFG.token_budget, 1)
        fired = [f for f in ledger.breakers(LINEAGE,
            3, token_budget=CFG.token_budget, blocking_severities=["High"])
            if f["breaker"] == "budget"]
        self.assertEqual([f["limit"] for f in fired], ["tokens"],
                         "the token breach is not firing; the probe is "
                         "measuring nothing")
        with self.assertRaises(transport.Refusal) as ctx:
            transport.handoff_preflight(CFG, ledger, LINEAGE)
        self.assertIn("budget", str(ctx.exception))

    def test_an_under_budget_lineage_passes(self):
        """The paired control: counted, under the ceiling, no refusal."""
        from review import transport
        ledger = self._spent(Ledger.in_memory(), 1_000, 1_000)
        self.assertEqual(
            [f for f in ledger.breakers(LINEAGE, 3, token_budget=CFG.token_budget,
                                        blocking_severities=["High"])
             if f["breaker"] == "budget" and f["limit"] == "tokens"], [])
        transport.handoff_preflight(CFG, ledger, LINEAGE)  # no raise

    def test_past_the_round_cap_alone_still_advises(self):
        """And the other side of the same line: the round threshold, with
        no token counts at all, passes."""
        from review import transport
        ledger = Ledger.in_memory()
        for r, sha in ((1, "a" * 40), (2, "b" * 40), (3, "c" * 40),
                       (4, "d" * 40)):
            _round(ledger, r, sha)
            ledger.add(_finding(r, f"fp2:{r:016x}", anchor=f"review/m{r}.py"))
            ledger.add({"event": "disposition", "round": r,
                        "finding_id": "F1", "fp": f"fp2:{r:016x}",
                        "disposition": "accepted", "payload": {}})
        limits = [f.get("limit") for f in ledger.breakers(LINEAGE,
            3, blocking_severities=["High"]) if f["breaker"] == "budget"]
        self.assertEqual(limits, ["rounds"])
        transport.handoff_preflight(CFG, ledger, LINEAGE)  # no raise

    def test_the_public_overview_states_the_round_cap_exception(self):
        """Lineage 20 round 1, F4: the overview called the round-count
        firing advisory and then said "Any firing stops the loop" — two
        opposite operational answers for the same admitted state. The
        published enforcement claim is bound here, beside the runtime
        controls above: it must carry the exception, and the unqualified
        universal cannot return.

        MUTATION: restore "Any firing stops the loop" in docs/overview.md
        and this fails while the round-only, under-token and over-token
        controls above stay the paired valid controls."""
        from review.tests.util import public_path
        doc = public_path("docs/overview.md")
        if doc is None:
            self.skipTest("no overview shipped in this tree")
        text = " ".join(doc.read_text(encoding="utf-8").split())
        self.assertNotIn("Any firing stops the loop", text,
                         "the overview flattens `budget` back into one "
                         "behaviour; only the round threshold advises")
        self.assertIn("Every firing except one stops the loop", text)
        self.assertIn("it advises rather than refuses", text)
        self.assertIn("A measured token breach refuses like every other "
                      "firing", text)


class TestRoundCapOverride(unittest.TestCase):
    """The cap default belongs to the repo; raising it belongs to one lineage.

    Decided 2026-08-13: the default stays 3 because round 4 discharged only
    half its findings and left more open than it started with. A single loop
    is authorized past it by a recorded, reason-bearing ledger event, so the
    authorization is auditable and does not become the next review's default.
    """

    def test_default_applies_with_no_override(self):
        led = Ledger.in_memory()
        self.assertEqual(led.effective_round_cap(3, LINEAGE), 3)

    def test_override_raises_this_lineage_only(self):
        led = Ledger.in_memory()
        led.add({"event": "cap_override", "round_cap": 5,
                 "reason": "user decision", "authorized_by": "user"})
        self.assertEqual(led.effective_round_cap(3, LINEAGE), 5)
        # A different lineage has its own ledger and is unaffected.
        self.assertEqual(Ledger.in_memory().effective_round_cap(3, LINEAGE), 3)

    def test_latest_authorization_wins_and_history_is_kept(self):
        led = Ledger.in_memory()
        led.add({"event": "cap_override", "round_cap": 4, "reason": "r1",
                 "authorized_by": "user"})
        led.add({"event": "cap_override", "round_cap": 5, "reason": "r2",
                 "authorized_by": "user"})
        self.assertEqual(led.effective_round_cap(3, LINEAGE), 5)
        self.assertEqual(
            len([e for e in led.events() if e["event"] == "cap_override"]), 2,
            "every authorization stays on the record")

    def test_the_effective_cap_is_what_the_advisory_reads(self):
        """The override still decides WHICH number the notice is measured
        against — that half is unchanged. What changed (2026-08-25) is that
        being past it advises rather than refuses."""
        text = synth.emitted_request()  # round 2 over a one-round ledger
        over = text.replace('round="2"', 'round="6"')
        items = validate.validate_request(wire.parse_request(over), synth.CFG,
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
                wire.parse_request(at_cap), synth.CFG, round_cap=5)
             if i.code == "R-BUDGET"], [])


class TestConvergenceReadsDirectionNotDuration(unittest.TestCase):

    def test_a_long_loop_that_closes_its_findings_is_not_flagged(self):
        """The case the round cap got wrong, and the reason it was replaced:
        many rounds, every finding withdrawn when answered. Duration says
        stop; direction says this is working."""
        ledger = Ledger.in_memory()
        for r in range(1, 7):
            _round(ledger, r, chr(96 + r) * 40)
            ledger.add(_finding(r, f"fp2:{r:016x}", anchor=f"review/m{r}.py"))
            if r > 1:
                ledger.add(_closure(r, f"fp2:{r - 1:016x}", "withdrawn"))
        c = ledger.convergence(LINEAGE)
        self.assertEqual(c["stalled_threads"], [])
        self.assertEqual(c["hunted_anchors"], {})
        self.assertNotIn(c["state"], ("stalled", "hunting"))

    def test_a_thread_the_reviewer_will_not_withdraw_is_stalled(self):
        """FALSIFICATION for the first signal. Mutation: count sustained
        closures without excluding withdrawn threads, or raise
        SUSTAINED_THREAD past 2, and this stops naming the thread."""
        ledger = Ledger.in_memory()
        fp = "fp2:00000000deadbeef"
        for r in (1, 2, 3):
            _round(ledger, r, chr(96 + r) * 40)
            ledger.add(_finding(r, fp))
            if r > 1:
                ledger.add(_closure(r, fp, "sustained"))
        c = ledger.convergence(LINEAGE)
        self.assertEqual(c["state"], "stalled")
        self.assertEqual(c["stalled_threads"], [fp])
        self.assertEqual(c["threads"][fp]["sustained"], 2)
        self.assertIn("refused to withdraw", c["reading"])

    def test_a_withdrawn_thread_is_not_stalled_however_long_it_took(self):
        """The paired control: sustained twice and then withdrawn is a
        thread that CLOSED. Counting it as stalled would flag every hard
        finding that took two rounds to answer."""
        ledger = Ledger.in_memory()
        fp = "fp2:00000000deadbeef"
        for r in (1, 2, 3, 4):
            _round(ledger, r, chr(96 + r) * 40)
        ledger.add(_finding(1, fp))
        ledger.add(_closure(2, fp, "sustained"))
        ledger.add(_closure(3, fp, "sustained"))
        ledger.add(_closure(4, fp, "withdrawn"))
        c = ledger.convergence(LINEAGE)
        self.assertEqual(c["stalled_threads"], [])
        self.assertTrue(c["threads"][fp]["withdrawn"])

    def test_a_stale_withdrawal_does_not_hide_a_reraised_stalled_thread(self):
        """Round-5 F2 — FALSIFICATION: a round-1 withdrawal is stale once
        the identity is re-raised and sustained in rounds 2 and 3 — it must
        not hide the stalled thread. Adding a withdrawal that answers the
        latest (round-3) ruling must then clear it. Mutation: compute
        `withdrawn` as `any(closure == "withdrawn")` across the whole
        identity again and the first assertion fails."""
        ledger = Ledger.in_memory()
        fp = "fp2:00000000deadbeef"
        for r in (1, 2, 3, 4):
            _round(ledger, r, chr(96 + r) * 40)
        ledger.add(_finding(1, fp))
        ledger.add(_closure(1, fp, "withdrawn"))
        ledger.add(_finding(2, fp))
        ledger.add(_closure(2, fp, "sustained"))
        ledger.add(_finding(3, fp))
        ledger.add(_closure(3, fp, "sustained"))
        c = ledger.convergence(LINEAGE)
        self.assertEqual(c["stalled_threads"], [fp])
        self.assertEqual(c["threads"][fp]["closures"],
                         ["withdrawn", "sustained", "sustained"])

        ledger.add(_closure(4, fp, "withdrawn"))
        c2 = ledger.convergence(LINEAGE)
        self.assertEqual(c2["stalled_threads"], [])
        self.assertTrue(c2["threads"][fp]["withdrawn"])

    def test_a_completed_withdrawal_does_not_make_a_fresh_reraise_stalled(self):
        """Round-6 F3 — FALSIFICATION: a thread sustained twice, then
        VALIDLY withdrawn, then re-raised with no current closure yet, must
        not be reported stalled from history that a completed withdrawal
        already closed — even though its complete closure history stays
        displayed. Two sustains recorded IN the new segment are the paired
        threshold control and must make it stalled. Mutation: sum sustained
        closures over the identity's whole lifetime again (drop the
        segment scoping) and the first assertion fails."""
        ledger = Ledger.in_memory()
        fp = "fp2:00000000deadbeef"
        for r in (1, 2, 3, 4, 5):
            _round(ledger, r, chr(96 + r) * 40)
        ledger.add(_finding(1, fp))
        ledger.add(_finding(2, fp))
        ledger.add(_closure(2, fp, "sustained"))
        ledger.add(_finding(3, fp))
        ledger.add(_closure(3, fp, "sustained"))
        ledger.add(_closure(4, fp, "withdrawn"))  # closes round 3's ruling
        ledger.add(_finding(5, fp))  # re-raised; no closure answers it yet
        c = ledger.convergence(LINEAGE)
        self.assertEqual(c["stalled_threads"], [])
        self.assertFalse(c["threads"][fp]["withdrawn"])
        self.assertEqual(c["threads"][fp]["closures"],
                         ["sustained", "sustained", "withdrawn"],
                         "the complete history stays displayed")

        for r in (6, 7):
            _round(ledger, r, chr(96 + r) * 40)
        ledger.add(_finding(6, fp))
        ledger.add(_closure(6, fp, "sustained"))
        ledger.add(_finding(7, fp))
        ledger.add(_closure(7, fp, "sustained"))
        c2 = ledger.convergence(LINEAGE)
        self.assertEqual(c2["stalled_threads"], [fp])
        self.assertEqual(c2["threads"][fp]["sustained"], 2)

    def test_one_anchor_producing_new_findings_each_round_is_hunting(self):
        """FALSIFICATION for the second signal, and the shape lineage 7 was
        actually in: every finding NEW, every one real, every fix landing,
        the same domain back next round. A count of findings per round sees
        nothing here — it was 4, 3, 3 — and neither does a stalled-thread
        check, because nothing is sustained.

        Mutation: require a repeated FINGERPRINT rather than a repeated
        anchor and this stops naming it, which is precisely the blindness
        that let the domain be hunted for three rounds.
        """
        ledger = Ledger.in_memory()
        for r in (1, 2, 3):
            _round(ledger, r, chr(96 + r) * 40)
            ledger.add(_finding(r, f"fp2:{r:016x}", anchor="review/id.py"))
            if r > 1:
                ledger.add(_closure(r, f"fp2:{r - 1:016x}", "withdrawn"))
        c = ledger.convergence(LINEAGE)
        self.assertEqual(c["state"], "hunting")
        self.assertIn("review/id.py", c["hunted_anchors"])
        self.assertEqual(c["stalled_threads"], [],
                         "nothing is sustained: this shape is invisible to "
                         "the stalled-thread signal alone")
        self.assertIn("narrowed", c["reading"])

    def test_one_anchor_with_one_finding_is_not_hunting(self):
        """The control that keeps the signal honest: an anchor is only
        hunted when it yields DISTINCT findings across rounds. A single
        finding restated in its own closure is one thread, not a hunt."""
        ledger = Ledger.in_memory()
        fp = "fp2:00000000abcdabcd"
        for r in (1, 2):
            _round(ledger, r, chr(96 + r) * 40)
            ledger.add(_finding(r, fp, anchor="review/id.py"))
        self.assertEqual(ledger.convergence(LINEAGE)["hunted_anchors"], {})

    def test_the_report_never_claims_more_than_it_counted(self):
        """The overclaim guard. Three rounds of this lineage were spent on
        exactly this failure — a mechanism asserting a property no test
        demonstrated — so the reading is required to stay a reading."""
        ledger = Ledger.in_memory()
        for r in (1, 2, 3):
            _round(ledger, r, chr(96 + r) * 40)
            ledger.add(_finding(r, f"fp2:{r:016x}", anchor="review/id.py"))
        c = ledger.convergence(LINEAGE)
        for forbidden in ("proves", "guarantees", "cannot converge",
                          "will never"):
            self.assertNotIn(forbidden, c["reading"].lower())
        self.assertIn(c["state"], ("stalled", "hunting", "closing", "open"))
        md = render_convergence_md(c)
        for r in c["rounds"]:
            self.assertIn(str(c["findings_per_round"][r]), md)

    def test_a_flat_inflow_is_not_reported_as_closing(self):
        """FALSIFICATION for round-4 F3. `closing` used to mean only "not
        rising", so a loop taking one fresh finding every round for ever
        reported as closing — a reassuring word handed to a human as the
        answer to whether the loop converges. Mutation: restore the
        not-rising test and this fails.

        The anchors differ per round so `hunting` does not fire; what is
        under test is the flat-inflow state on its own.
        """
        ledger = Ledger.in_memory()
        for r in (1, 2, 3, 4):
            _round(ledger, r, chr(96 + r) * 40)
            ledger.add(_finding(r, f"fp2:{r:016x}", anchor=f"review/m{r}.py"))
        c = ledger.convergence(LINEAGE)
        self.assertEqual(c["findings_per_round"], {1: 1, 2: 1, 3: 1, 4: 1})
        self.assertNotEqual(c["state"], "closing",
                            "a loop opening one finding every round and "
                            "closing none is reported as closing")
        self.assertEqual(c["state"], "steady")
        self.assertIn("not falling", c["reading"])

    def test_closing_requires_both_a_fall_and_a_withdrawal(self):
        """The paired control, and the definition: findings actually
        falling AND the reviewer actually withdrawing them. Either alone is
        not closure — a fall with nothing withdrawn is a quiet round, and a
        withdrawal under rising inflow is not a loop closing."""
        ledger = Ledger.in_memory()
        for r in (1, 2, 3):
            _round(ledger, r, chr(96 + r) * 40)
        for n in range(3):
            ledger.add(_finding(1, f"fp2:{n:016x}", anchor=f"review/a{n}.py",
                                fid=f"F{n + 1}"))
        ledger.add(_finding(2, "fp2:00000000000000ff", anchor="review/b.py"))
        for n in range(3):
            ledger.add(_closure(2, f"fp2:{n:016x}", "withdrawn"))
        ledger.add(_closure(3, "fp2:00000000000000ff", "withdrawn"))
        c = ledger.convergence(LINEAGE)
        self.assertEqual(c["state"], "closing")
        self.assertTrue(c["withdrawn_any"])
        self.assertIn("falling", c["reading"])

    def test_a_recurring_identity_is_not_rendered_as_fresh(self):
        """FALSIFICATION for round-4 F6. The hunting report says it counts
        FRESH identities by anchor; it rendered every identity present in a
        round, so a finding returning under the same fingerprint displayed
        as new — the report overstating the evidence it exists to weigh.
        Mutation: render every identity present and this fails."""
        ledger = Ledger.in_memory()
        recurring = "fp2:00000000cafecafe"
        for r in (1, 2, 3):
            _round(ledger, r, chr(96 + r) * 40)
            ledger.add(_finding(r, recurring, anchor="review/id.py"))
        ledger.add(_finding(2, "fp2:00000000beefbeef", anchor="review/id.py",
                            fid="F2"))
        c = ledger.convergence(LINEAGE)
        rounds = c["hunted_anchors"].get("review/id.py", {})
        self.assertEqual(
            rounds, {1: [recurring], 2: ["fp2:00000000beefbeef"]},
            "a recurring identity is rendered as a fresh finding in the "
            "round it merely returned in")
        self.assertNotIn(3, rounds,
                         "round 3 opened nothing new and must not appear")

    def test_an_empty_lineage_says_so_rather_than_guessing(self):
        c = Ledger.in_memory().convergence(LINEAGE)
        self.assertEqual(c["state"], "open")
        self.assertIn("too little recorded", c["reading"])


DECLARED = Ledger.EDGE_DECLARED
INFERRED = Ledger.EDGE_INFERRED

A = "fp2:00000000000000a1"   # the parent, ruled in round 1
B = "fp2:00000000000000b2"   # the residue, ruled in round 2
C = "fp2:00000000000000c3"   # a second residue, or a third link
D = "fp2:00000000000000d4"   # a genuinely new finding beside a residue
X = "review/x.py"
Y = "review/y.py"            # a second anchor, off the one the rows watch
SHA_R = "7" * 40             # the target a recorded verdict names


def _reclassified(round_no, fp, note="narrowed", residue=None):
    ev = {**_closure(round_no, fp, "reclassified"), "note": note}
    if residue is not None:
        ev["residue"] = list(residue)
    return ev


class TestAReclassifiedResidueIsNotANewIdentity(unittest.TestCase):
    """Brief `convergence-blind-to-reclassification`, raised by the reviewer
    on the lineage-26 round-3 verdict.

    A reviewer doing exactly the right thing — confirming most of a High
    finding fixed and re-issuing the surviving residue at Medium under an
    accurate new title — mints a second fingerprint on the same anchor, and
    `hunted` read it as a fresh unrelated finding: the report argued for
    stopping the loop at the moment the loop was narrowing. The continuity
    was already on the record, in the `reclassified` closure that names the
    prior fingerprint and, in the reviewer's own note, the round-local id of
    the residue.

    Bounded as the brief bounds it: the reading FOLLOWS that edge. It does
    not make fingerprints stable across re-titling — the fingerprint must
    change when the claim changes — so no identity is merged here.
    """

    #: The measured instance, its exact fingerprints (lineage 26, the
    #: `caller cwd never reaches analyst` thread) and the reviewer's own
    #: closure note, kept verbatim: the edge this reading follows is the one
    #: a real reviewer actually wrote, not a shape invented for a test.
    PARENT = "fp2:40fab9408f50c232"
    RESIDUE = "fp2:edfc4da8f6870d01"
    NOTE = ("High to Medium. The conservative launch form is restored and "
            "its limits stated, but the results brief retains the rejected "
            "categorical claim at lines 137-140. Current F1 records that "
            "bounded residue; the fix is not yet complete across all "
            "consumers.")
    ANCHOR = "briefs/capability-parity-rules.md"

    def _lineage_26(self, closure_term="reclassified", note=None):
        """Rounds 1–3 of lineage 26 on one anchor: the High finding, the
        reclassification into the Medium residue, and round 3 withdrawing
        both. `closure_term` is the knob the paired control turns."""
        ledger = Ledger.in_memory()
        for r in (1, 2, 3):
            _round(ledger, r, chr(96 + r) * 40)
        ledger.add(_finding(1, self.PARENT, anchor=self.ANCHOR, fid="F2"))
        ledger.add({**_closure(2, self.PARENT, closure_term),
                    "note": self.NOTE if note is None else note,
                    "answers_round": 1})
        ledger.add(_finding(2, self.RESIDUE, anchor=self.ANCHOR, fid="F1"))
        ledger.add({**_closure(3, self.RESIDUE, "withdrawn"),
                    "answers_round": 2})
        ledger.add({**_closure(3, self.PARENT, "withdrawn"),
                    "answers_round": 1})
        return ledger

    def test_the_narrowed_residue_is_not_reported_as_a_new_identity(self):
        """FALSIFICATION, the brief's own: replay the lineage in which one
        finding is reclassified and its residue later withdrawn, and the
        reading must not report a new identity for the descendant. Before
        this, the live lineage 26 read `hunting` on this anchor and the
        human had to supply the correction from outside the tool.

        Mutation: ignore the edge — stop subtracting `descends` in
        `new_per_round` and in `hunted`'s `fresh` — and every assertion
        below fails back to `hunting`."""
        c = self._lineage_26().convergence(LINEAGE)
        self.assertEqual(c["hunted_anchors"], {})
        self.assertEqual(c["state"], "closing")
        self.assertEqual(c["new_per_round"], {1: 1, 2: 0, 3: 0})
        self.assertEqual(c["reclassified_per_round"], {1: 0, 2: 1, 3: 0},
                         "the identity `new` no longer counts must be "
                         "counted somewhere; a number that just falls has "
                         "gone missing")
        self.assertEqual(c["threads"][self.RESIDUE]["descends_from"],
                         [self.PARENT])
        self.assertEqual(c["threads"][self.PARENT]["reclassified_to"],
                         [self.RESIDUE])

    def test_without_the_reclassified_closure_the_misreading_returns(self):
        """The paired control the brief demands: a mutation that removes the
        `reclassified` edge must make the check fail, proving the edge is
        what the reading depends on. Same two findings, same anchor, same
        rounds — only the closure term changes, and the anchor is hunted
        again."""
        c = self._lineage_26(closure_term="sustained").convergence(LINEAGE)
        self.assertIn(self.ANCHOR, c["hunted_anchors"])
        self.assertEqual(c["state"], "hunting")
        self.assertEqual(c["new_per_round"][2], 1)
        self.assertEqual(c["threads"][self.RESIDUE]["descends_from"], [])

    def test_a_note_that_names_no_residue_follows_nothing(self):
        """The other half of the control: the closure term alone is not the
        edge. A `reclassified` closure whose note names no ruling of its own
        round says a severity changed and nothing about where the claim
        went, so the reading refuses to invent the descendant."""
        c = self._lineage_26(note="High to Medium.").convergence(LINEAGE)
        self.assertIn(self.ANCHOR, c["hunted_anchors"])
        self.assertEqual(c["threads"][self.RESIDUE]["descends_from"], [])

    def test_a_genuinely_new_finding_beside_a_residue_is_still_hunting(self):
        """FALSIFICATION for the signal this fix must NOT suppress. A
        residue and an unrelated new finding arrive on the same anchor in
        the same round: the residue is continuity, the other is a second
        identity on a recurring anchor, and the anchor stays hunted naming
        only the second.

        Mutation: attribute every new identity on the parent's anchor in the
        closure's round to the parent — the cheap way to follow the edge —
        and this fails silently, which is the report going blind to exactly
        what it exists to see."""
        ledger = Ledger.in_memory()
        parent, residue = "fp2:00000000000000a1", "fp2:00000000000000b2"
        unrelated = "fp2:00000000000000d4"
        for r in (1, 2):
            _round(ledger, r, chr(96 + r) * 40)
        ledger.add(_finding(1, parent, anchor="review/x.py"))
        ledger.add({**_closure(2, parent, "reclassified"),
                    "note": "narrowed; F1 carries the residue"})
        ledger.add(_finding(2, residue, anchor="review/x.py", fid="F1"))
        ledger.add(_finding(2, unrelated, anchor="review/x.py", fid="F2"))
        c = ledger.convergence(LINEAGE)
        self.assertEqual(c["state"], "hunting")
        self.assertEqual(c["hunted_anchors"]["review/x.py"],
                         {1: [parent], 2: [unrelated]},
                         "the residue is continuity and the other finding "
                         "is not; naming both, or neither, is a different "
                         "report from the one the record supports")
        self.assertEqual(c["new_per_round"], {1: 1, 2: 1})
        self.assertEqual(c["reclassified_per_round"], {1: 0, 2: 1})

    def test_a_chain_of_reclassifications_is_followed_link_by_link(self):
        """Reclassified twice. Each link is read on its own evidence, so a
        thread narrowed in two successive rounds opens one new claim, not
        three — and a reclassification is never counted as a refusal to
        withdraw, so `stalled_threads` stays empty however long the chain."""
        a, b, d = ("fp2:00000000000000a1", "fp2:00000000000000b2",
                   "fp2:00000000000000c3")
        ledger = Ledger.in_memory()
        for r in (1, 2, 3):
            _round(ledger, r, chr(96 + r) * 40)
        ledger.add(_finding(1, a, anchor="review/x.py"))
        ledger.add({**_closure(2, a, "reclassified"),
                    "note": "narrowed; F1 carries the residue"})
        ledger.add(_finding(2, b, anchor="review/x.py"))
        ledger.add({**_closure(3, b, "reclassified"),
                    "note": "narrowed again; F1 carries the residue"})
        ledger.add(_finding(3, d, anchor="review/x.py"))
        c = ledger.convergence(LINEAGE)
        self.assertEqual(c["hunted_anchors"], {})
        self.assertEqual(c["new_per_round"], {1: 1, 2: 0, 3: 0})
        self.assertEqual(c["reclassified_per_round"], {1: 0, 2: 1, 3: 1})
        self.assertEqual(c["threads"][b]["descends_from"], [a])
        self.assertEqual(c["threads"][d]["descends_from"], [b])
        self.assertEqual(c["stalled_threads"], [])

    def test_the_edge_cannot_run_backwards_so_it_cannot_cycle(self):
        """A round-3 ruling re-raising the round-1 parent cannot descend
        from its own descendant: an edge is followed only from a parent
        ruled in an EARLIER round than the residue, which is what makes the
        graph acyclic by construction rather than by a visited set.

        Mutation: drop the `first_seen[parent] < closure round` clause and
        the back-edge is recorded, which is a cycle in a graph this reading
        walks."""
        a, b = "fp2:00000000000000a1", "fp2:00000000000000b2"
        ledger = Ledger.in_memory()
        for r in (1, 2, 3):
            _round(ledger, r, chr(96 + r) * 40)
        ledger.add(_finding(1, a, anchor="review/x.py"))
        ledger.add({**_closure(2, a, "reclassified"),
                    "note": "residue is F1"})
        ledger.add(_finding(2, b, anchor="review/x.py"))
        ledger.add({**_closure(3, b, "reclassified"),
                    "note": "residue is F1"})
        ledger.add(_finding(3, a, anchor="review/x.py"))  # the parent again
        c = ledger.convergence(LINEAGE)
        self.assertEqual(c["threads"][a]["descends_from"], [],
                         "the ancestor is not a residue of its own "
                         "descendant")
        self.assertEqual(c["threads"][b]["descends_from"], [a])

    def test_an_unruled_or_foreign_fingerprint_supports_no_descent(self):
        """A `reclassified` closure naming a fingerprint this lineage never
        ruled — a typo, or a finding belonging to another review — asserts
        nothing about a residue here, so the reading refuses the edge and
        the anchor stays hunted. Fail-closed, because following a wrong edge
        HIDES a finding while refusing one only restores the over-report a
        reader already corrects."""
        a, b, other = ("fp2:00000000000000a1", "fp2:00000000000000b2",
                       "fp2:00000000000000e5")
        for closed_fp, label in ((("fp2:" + "f" * 16), "no ruling anywhere"),
                                 (other, "ruled in another lineage")):
            with self.subTest(label):
                ledger = Ledger.in_memory()
                ledger.add({"event": "request", "round": 1, "sha": "z" * 40,
                            "bytes": 1, "author": "claude",
                            "reviewer": "codex", "lineage": "other"})
                ledger.add({**_finding(1, other, anchor="review/x.py"),
                            "lineage": "other"})
                for r in (1, 2):
                    _round(ledger, r, chr(96 + r) * 40)
                ledger.add(_finding(1, a, anchor="review/x.py"))
                ledger.add({**_closure(2, closed_fp, "reclassified"),
                            "note": "residue is F1"})
                ledger.add(_finding(2, b, anchor="review/x.py"))
                c = ledger.convergence(LINEAGE)
                self.assertIn("review/x.py", c["hunted_anchors"])
                self.assertEqual(c["threads"][b]["descends_from"], [])

    def test_two_residues_from_one_parent_and_the_ambiguous_note(self):
        """One closure per residue is readable and both edges are followed.
        ONE closure naming both is prose this reading cannot disambiguate —
        `narrowed to F1, unlike F3` names two ids and means one — so it
        follows neither and the round reads exactly as it did before."""
        a, b, d = ("fp2:00000000000000a1", "fp2:00000000000000b2",
                   "fp2:00000000000000c3")

        def _two(notes):
            ledger = Ledger.in_memory()
            for r in (1, 2):
                _round(ledger, r, chr(96 + r) * 40)
            ledger.add(_finding(1, a, anchor="review/x.py"))
            for note in notes:
                ledger.add({**_closure(2, a, "reclassified"), "note": note})
            ledger.add(_finding(2, b, anchor="review/x.py", fid="F1"))
            ledger.add(_finding(2, d, anchor="review/x.py", fid="F2"))
            return ledger.convergence(LINEAGE)

        split = _two(("split; F1 carries one half",
                      "split; F2 carries the other half"))
        self.assertEqual(split["hunted_anchors"], {})
        self.assertEqual(split["reclassified_per_round"], {1: 0, 2: 2})
        self.assertEqual(split["threads"][a]["reclassified_to"], [b, d])

        ambiguous = _two(("split across F1 and F2",))
        self.assertIn("review/x.py", ambiguous["hunted_anchors"])
        self.assertEqual(ambiguous["new_per_round"], {1: 1, 2: 2})
        self.assertEqual(ambiguous["threads"][a]["reclassified_to"], [])

    # --------------------------- what the note names, before eligibility

    def _read(self, note, round2, round1=((A, "F1", X),), residue=None,
              extra=()):
        """One row of the partition: round 1's rulings, a `reclassified`
        closure on the parent A carrying this note (and, where the row
        declares one, this residue), then round 2's rulings. Returns what
        the reading makes of it."""
        ledger = Ledger.in_memory()
        for r in (1, 2):
            _round(ledger, r, chr(96 + r) * 40)
        for e in extra:
            ledger.add(e)
        for fp, fid, anchor in round1:
            ledger.add(_finding(1, fp, anchor=anchor, fid=fid))
        ledger.add(_reclassified(2, A, note=note, residue=residue))
        for fp, fid, anchor in round2:
            ledger.add(_finding(2, fp, anchor=anchor, fid=fid))
        return ledger.convergence(LINEAGE)

    def _assert_row(self, row):
        """Every field the edge can move, on every thread — not only the one
        the row is about. A reading that got the right edge and the wrong
        count is not the reading this report needs."""
        c = self._read(**row["input"])
        edges = row["edges"]
        self.assertEqual(c["new_per_round"], row["new"], "new_per_round")
        self.assertEqual(c["reclassified_per_round"], row["reclassified"],
                         "reclassified_per_round")
        self.assertEqual(c["hunted_anchors"], row["hunted"], "hunted_anchors")
        self.assertEqual(c["state"], row["state"], "state")
        for ident, thread in c["threads"].items():
            self.assertEqual(thread["descends_via"], edges.get(ident, {}),
                             f"descends_via of {ident}")
            self.assertEqual(thread["descends_from"],
                             sorted(edges.get(ident, {})),
                             f"descends_from of {ident}")
            self.assertEqual(
                thread["reclassified_to"],
                sorted(child for child, parents in edges.items()
                       if ident in parents),
                f"reclassified_to of {ident}")
        return c

    def test_an_ineligible_name_does_not_make_another_name_unique(self):
        """FALSIFICATION for round-1 F2 of this lineage, over the partition
        the finding names: a note of a closure that declares NO residue,
        crossed with what each ruling it names IS.

        The defect was an ORDER. The eligibility bounds — not the parent,
        new in this round — ran BEFORE the count of how many rulings the
        note named, so `narrowed to F1, unlike F2` read as uniquely naming
        F2 whenever F1 happened to be the parent or a returning finding.
        The note expressly contrasted F2 with the residue; the reading gave
        it a parent, dropped it out of `new_per_round` and took its anchor
        out of `hunted_anchors` — a genuinely new finding hidden by prose
        that said the opposite.

        An id-shaped token this round cannot place hides a finding through
        the other door — `narrowed to f1, unlike F2` names F2 and nothing
        else, because a misspelling matches no id — so the note must be
        readable WHOLE: every token of `Ledger.ID_SHAPED` resolving exactly
        to an id of the closure's round, all of them to one identity. The
        rows that used to follow the good edge beside an unplaceable id no
        longer do; that is the over-report returning, which is the cheap
        error.

        Mutations, each its own run: (1) move the `ident != parent` and
        `first_seen[ident] == closed_in` filters back into the set
        comprehension, ahead of the `len(named) != 1` test — the
        `previously ruled + new` and `the parent + new` rows fail, each on
        four fields at once; (2) drop the `ID_SHAPED` refusal — every
        misspelt, zero-padded and unplaceable row fails.
        """
        rows = (
            {"label": "two names, both new in this round",
             "input": {"note": "narrowed to F1, unlike F2",
                       "round2": ((B, "F1", X), (D, "F2", X))},
             "edges": {}, "new": {1: 1, 2: 2}, "reclassified": {1: 0, 2: 0},
             "hunted": {X: {1: [A], 2: [B, D]}}, "state": "hunting"},

            {"label": "two names, one previously ruled and one new — the "
                      "finding's own reproduction",
             "input": {"note": "narrowed to F1, unlike F2",
                       "round1": ((A, "F1", X), (C, "F2", Y)),
                       "round2": ((C, "F1", Y), (D, "F2", X))},
             "edges": {}, "new": {1: 2, 2: 1}, "reclassified": {1: 0, 2: 0},
             "hunted": {X: {1: [A], 2: [D]}}, "state": "hunting"},

            {"label": "two names, one of them the parent re-raised",
             "input": {"note": "narrowed to F1, unlike F2",
                       "round2": ((A, "F1", X), (D, "F2", X))},
             "edges": {}, "new": {1: 1, 2: 1}, "reclassified": {1: 0, 2: 0},
             "hunted": {X: {1: [A], 2: [D]}}, "state": "hunting"},

            {"label": "three names",
             "input": {"note": "F1, F2 and F3 all changed",
                       "round2": ((B, "F1", X), (C, "F2", X), (D, "F3", X))},
             "edges": {}, "new": {1: 1, 2: 3}, "reclassified": {1: 0, 2: 0},
             "hunted": {X: {1: [A], 2: [B, C, D]}}, "state": "hunting"},

            {"label": "one id borne by two identities of the round",
             "input": {"note": "narrowed to F1",
                       "round2": ((B, "F1", X), (C, "F1", X))},
             "edges": {}, "new": {1: 1, 2: 2}, "reclassified": {1: 0, 2: 0},
             "hunted": {X: {1: [A], 2: [B, C]}}, "state": "hunting"},

            {"label": "one name, ineligible: previously ruled",
             "input": {"note": "narrowed to F1",
                       "round1": ((A, "F1", X), (C, "F2", Y)),
                       "round2": ((C, "F1", Y),)},
             "edges": {}, "new": {1: 2, 2: 0}, "reclassified": {1: 0, 2: 0},
             "hunted": {}, "state": "steady"},

            {"label": "one name, ineligible: the parent itself",
             "input": {"note": "narrowed to F1",
                       "round2": ((A, "F1", X),)},
             "edges": {}, "new": {1: 1, 2: 0}, "reclassified": {1: 0, 2: 0},
             "hunted": {}, "state": "steady"},

            # The controls. An edge IS followed when one identity is named
            # and that identity holds up — otherwise the rows above would
            # pass on a reading that never follows anything.
            {"label": "control: one name, eligible",
             "input": {"note": "narrowed to F1", "round2": ((B, "F1", X),)},
             "edges": {B: {A: INFERRED}}, "new": {1: 1, 2: 0},
             "reclassified": {1: 0, 2: 1}, "hunted": {}, "state": "steady"},

            {"label": "control: one name, plus a legacy id of another "
                      "round's shape — not id-shaped, so it refuses "
                      "nothing",
             "input": {"note": "narrowed to F1, unlike R1-F5",
                       "round1": ((A, "F1", X), (C, "R1-F5", Y)),
                       "round2": ((B, "F1", X),)},
             "edges": {B: {A: INFERRED}}, "new": {1: 2, 2: 0},
             "reclassified": {1: 0, 2: 1}, "hunted": {}, "state": "steady"},

            # An id-shaped token this round cannot place makes the note
            # unreadable, however well the OTHER reference is spelt. Each
            # of these followed the good edge until the strict reading.
            {"label": "one name, plus an id no ruling of this round carries",
             "input": {"note": "narrowed to F1, unlike F2",
                       "round2": ((B, "F1", X),)},
             "edges": {}, "new": {1: 1, 2: 1}, "reclassified": {1: 0, 2: 0},
             "hunted": {X: {1: [A], 2: [B]}}, "state": "hunting"},

            {"label": "one name, plus an id only an EARLIER round carries",
             "input": {"note": "narrowed to F1, unlike F2",
                       "round1": ((A, "F1", X), (C, "F2", Y)),
                       "round2": ((B, "F1", X),)},
             "edges": {}, "new": {1: 2, 2: 1}, "reclassified": {1: 0, 2: 0},
             "hunted": {X: {1: [A], 2: [B]}}, "state": "hunting"},

            {"label": "a misspelt reference beside a correctly spelt one",
             "input": {"note": "narrowed to f1, unlike F2",
                       "round2": ((B, "F1", X), (D, "F2", X))},
             "edges": {}, "new": {1: 1, 2: 2}, "reclassified": {1: 0, 2: 0},
             "hunted": {X: {1: [A], 2: [B, D]}}, "state": "hunting"},

            {"label": "a zero-padded reference beside a correctly spelt one",
             "input": {"note": "narrowed to F01, unlike F2",
                       "round2": ((B, "F1", X), (D, "F2", X))},
             "edges": {}, "new": {1: 1, 2: 2}, "reclassified": {1: 0, 2: 0},
             "hunted": {X: {1: [A], 2: [B, D]}}, "state": "hunting"},

            {"label": "a misspelt reference alone",
             "input": {"note": "narrowed to f1",
                       "round2": ((B, "F1", X),)},
             "edges": {}, "new": {1: 1, 2: 1}, "reclassified": {1: 0, 2: 0},
             "hunted": {X: {1: [A], 2: [B]}}, "state": "hunting"},

            {"label": "a zero-padded reference alone",
             "input": {"note": "narrowed to F01",
                       "round2": ((B, "F1", X),)},
             "edges": {}, "new": {1: 1, 2: 1}, "reclassified": {1: 0, 2: 0},
             "hunted": {X: {1: [A], 2: [B]}}, "state": "hunting"},
        )
        for row in rows:
            with self.subTest(row["label"]):
                self._assert_row(row)

    def test_several_ids_resolving_to_one_identity_are_one_name(self):
        """The ids are two and the residue is one: a ruling recorded under a
        pre-alias fingerprint and the same identity recorded under the
        canonical one. Named twice, the note still leaves exactly one answer
        to WHICH identity the residue is — and no second identity that
        following the edge could hide — so this is one name, not an
        ambiguity. Identity resolves first here as it does everywhere else
        in this reading.

        Mutation: count the ids instead of the identities they resolve to
        (drop the `resolve` and key the set by `ruling_id`) and the edge is
        refused, which loses the continuity this report exists to keep.

        `findings_per_round` and the per-round counts stay per RULING, not
        per identity: two rulings were made, the record says two, and one of
        them is the continuation."""
        old = "fp1:00000000000000b2"
        c = self._assert_row(
            {"input": {"note": "narrowed to F1, recorded again as F2",
                       "round2": ((old, "F1", X), (B, "F2", X)),
                       "extra": ({"event": "lineage", "kind": "alias",
                                  "from_fp": old, "to_fp": B,
                                  "reason": "fp1->fp2"},)},
             "edges": {B: {A: INFERRED}}, "new": {1: 1, 2: 0},
             "reclassified": {1: 0, 2: 2}, "hunted": {}, "state": "steady"})
        self.assertEqual(c["findings_per_round"], {1: 1, 2: 2})

    def test_the_declaration_wins_over_every_ambiguous_note(self):
        """The same notes, with a `Residue:` declared. The declaration is
        authoritative and the note is not read at all — so a row the note
        reading refuses still follows the declared edge, and follows only
        that one.

        Mutation: delete the `continue` after the declared branch and the
        note is read as well; the two-name rows gain a second edge, or the
        parent-re-raised row gains one to an identity the reviewer never
        declared."""
        rows = (
            {"label": "both new",
             "input": {"note": "narrowed to F1, unlike F2",
                       "round2": ((B, "F1", X), (D, "F2", X)),
                       "residue": [D]},
             "edges": {D: {A: DECLARED}}, "new": {1: 1, 2: 1},
             "reclassified": {1: 0, 2: 1},
             "hunted": {X: {1: [A], 2: [B]}}, "state": "hunting"},

            {"label": "one previously ruled and one new",
             "input": {"note": "narrowed to F1, unlike F2",
                       "round1": ((A, "F1", X), (C, "F2", Y)),
                       "round2": ((C, "F1", Y), (D, "F2", X)),
                       "residue": [D]},
             "edges": {D: {A: DECLARED}}, "new": {1: 2, 2: 0},
             "reclassified": {1: 0, 2: 1}, "hunted": {}, "state": "steady"},

            {"label": "one of them the parent re-raised",
             "input": {"note": "narrowed to F1, unlike F2",
                       "round2": ((A, "F1", X), (D, "F2", X)),
                       "residue": [D]},
             "edges": {D: {A: DECLARED}}, "new": {1: 1, 2: 0},
             "reclassified": {1: 0, 2: 1}, "hunted": {}, "state": "steady"},

            {"label": "three names",
             "input": {"note": "F1, F2 and F3 all changed",
                       "round2": ((B, "F1", X), (C, "F2", X), (D, "F3", X)),
                       "residue": [B]},
             "edges": {B: {A: DECLARED}}, "new": {1: 1, 2: 2},
             "reclassified": {1: 0, 2: 1},
             "hunted": {X: {1: [A], 2: [C, D]}}, "state": "hunting"},

            {"label": "one id borne by two identities of the round",
             "input": {"note": "narrowed to F1",
                       "round2": ((B, "F1", X), (C, "F1", X)),
                       "residue": [B]},
             "edges": {B: {A: DECLARED}}, "new": {1: 1, 2: 1},
             "reclassified": {1: 0, 2: 1},
             "hunted": {X: {1: [A], 2: [C]}}, "state": "hunting"},

            # The sharp one: a note this reading CAN read, naming the other
            # finding. The declaration still wins alone.
            {"label": "a readable note the declaration disagrees with",
             "input": {"note": "narrowed to F1",
                       "round2": ((B, "F1", X), (D, "F2", X)),
                       "residue": [D]},
             "edges": {D: {A: DECLARED}}, "new": {1: 1, 2: 1},
             "reclassified": {1: 0, 2: 1},
             "hunted": {X: {1: [A], 2: [B]}}, "state": "hunting"},
        )
        for row in rows:
            with self.subTest(row["label"]):
                self._assert_row(row)

    #: Every way a reviewer might write something id-shaped, and what this
    #: reading makes of it: `names` (an exact id of this round), `inert`
    #: (not a reference at all — the boundaries exclude it, so it neither
    #: names nor refuses) or `refuses` (loosely id-shaped, placeable by no
    #: id of this round, so the whole note is unreadable).
    SPELLINGS = (
        ("narrowed to F1", "names", "plain"),
        ("F1 carries the residue", "names", "at the start"),
        ("the residue is F1", "names", "at the end"),
        ("narrowed to F1.", "names", "full stop"),
        ("narrowed to (F1)", "names", "parenthesised"),
        ("narrowed; F1, the residue", "names", "comma"),
        ("narrowed to R2-F1", "inert", "a legacy id is not this id"),
        ("narrowed to fp2:00000000000000F1", "inert",
         "an id inside a fingerprint-shaped token"),
        ("narrowed; see docs/F1.md", "inert", "a path component"),
        ("split across F1/F2", "inert",
         "a path-shaped pair: neither half is id-shaped, so it names "
         "nothing rather than naming two"),
        ("narrowed to F10", "refuses", "a longer id is another ruling"),
        ("narrowed to f1", "refuses", "case"),
        ("narrowed to F01", "refuses", "zero padding"),
        ("narrowed to f01", "refuses", "both at once"),
    )

    def test_which_spellings_of_an_id_the_note_reading_admits(self):
        """What the reader would try next at this anchor: the SPELLING. One
        round-2 ruling carries `F1`, and the note writes a reference to it
        however a reviewer might.

        Read ALONE, `names` follows the edge and the other two do not — but
        alone the two are indistinguishable, so each spelling is read again
        BESIDE a correct reference to a second ruling in
        `test_an_inert_token_refuses_nothing_and_a_shaped_one_refuses_all`,
        where inert and refusing come apart."""
        for note, verdict, why in self.SPELLINGS:
            with self.subTest(f"alone: {why}"):
                c = self._read(note=note, round2=((B, "F1", X),))
                names = verdict == "names"
                self.assertEqual(
                    c["threads"][B]["descends_via"],
                    {A: INFERRED} if names else {}, note)
                self.assertEqual(c["new_per_round"][2], 0 if names else 1)
                self.assertEqual(c["hunted_anchors"],
                                 {} if names else {X: {1: [A], 2: [B]}})

    def test_an_inert_token_refuses_nothing_and_a_shaped_one_refuses_all(self):
        """The same spellings beside a reference this reading CAN place.
        Round 2 rules `F1` and `F2`; the note names F2 — the residue — and
        also carries the token under test.

        This is where the two kinds of miss separate. A token the
        boundaries exclude is not a reference and costs nothing: the note
        still reads, and the F2 edge is followed. A loosely id-shaped token
        that no id of this round places is a reference this reading cannot
        resolve, and it refuses the whole note — including the edge it
        would otherwise have followed. That asymmetry is the price of not
        hiding a contrasted finding, paid in over-report."""
        for note, verdict, why in self.SPELLINGS:
            with self.subTest(f"beside F2: {why}"):
                c = self._read(note=f"narrowed to F2; {note}",
                               round2=((B, "F1", X), (D, "F2", X)))
                # `names` names F1 TOO, so beside F2 it is two names.
                followed = verdict == "inert"
                self.assertEqual(
                    c["threads"][D]["descends_via"],
                    {A: INFERRED} if followed else {}, note)
                self.assertEqual(c["threads"][B]["descends_via"], {})
                self.assertEqual(c["new_per_round"],
                                 {1: 1, 2: 1} if followed else {1: 1, 2: 2})
                self.assertEqual(
                    c["hunted_anchors"],
                    {X: {1: [A], 2: [B]}} if followed
                    else {X: {1: [A], 2: [B, D]}})

    def test_shape_refuses_a_note_it_can_never_resolve(self):
        """The same defect as F2, reached through a typo, and closed the
        same way. `f1` matches no id — nothing is case-folded to resolve,
        because that would read `f1()` in a note about code as a reference
        — so the misspelling names nothing, and a note that misspells one
        of two references would read as naming the other one uniquely. That
        is a genuinely new finding hidden behind an edge its own note
        denied.

        So SHAPE refuses where spelling cannot resolve: a loosely id-shaped
        token — case-insensitive, leading zeros admitted — that is not
        exactly an id of this round makes the whole note unreadable.

        Mutation A: delete the `ID_SHAPED` refusal, and the misspelt
        reference beside the correct one follows the F2 edge again.
        Mutation B: use the loose shape to RESOLVE instead (case-fold and
        strip padding when matching ids), and the misspelt reference ALONE
        follows an edge no reviewer spelt."""
        both = self._read(note="narrowed to f1, unlike F2",
                          round2=((B, "F1", X), (D, "F2", X)))
        self.assertEqual(both["threads"][D]["descends_via"], {},
                         "an unplaceable id-shaped token must not leave the "
                         "one that resolved looking unique")
        self.assertEqual(both["threads"][B]["descends_via"], {})
        self.assertEqual(both["new_per_round"], {1: 1, 2: 2})
        self.assertEqual(both["hunted_anchors"], {X: {1: [A], 2: [B, D]}})

        alone = self._read(note="narrowed to f1", round2=((B, "F1", X),))
        self.assertEqual(alone["threads"][B]["descends_via"], {},
                         "a misspelling is refused, never resolved to the "
                         "id it looks like")

    def test_an_id_of_this_round_is_read_as_this_round_s(self):
        """The other documented uncovered case: ids are round-local, and a
        verdict numbers its findings from `F1` again every round, so the
        parent's own earlier id and a round-local id of the closure's round
        collide in prose. `F1` in a round-2 note is read as round 2's — the
        closure already names its parent by fingerprint, so the round-local
        reading is the only one the note adds anything with, and refusing
        the collision would refuse the commonest real shape there is."""
        c = self._read(note="the F1 of round 1 is narrowed; F1 remains",
                       round2=((B, "F1", X),))
        self.assertEqual(c["threads"][B]["descends_via"], {A: INFERRED})
        self.assertEqual(c["hunted_anchors"], {})

    def test_a_legacy_id_suffix_does_not_bind_the_wrong_ruling(self):
        """`R1-F5` contains `F5`, and the corpus writes closure notes that
        name legacy ids exactly that way. A word boundary that admits `-`
        would bind this round's `F5` to a note talking about a round-1
        legacy claim — continuity invented from a suffix."""
        a, b = "fp2:00000000000000a1", "fp2:00000000000000b2"
        ledger = Ledger.in_memory()
        for r in (1, 2):
            _round(ledger, r, chr(96 + r) * 40)
        ledger.add(_finding(1, a, anchor="review/x.py", fid="R1-F5"))
        ledger.add({**_closure(2, a, "reclassified"),
                    "note": "prior fingerprint generated by legacy import "
                            "(round-2 R1-F5); bound to the parent identity"})
        ledger.add(_finding(2, b, anchor="review/x.py", fid="F5"))
        c = ledger.convergence(LINEAGE)
        self.assertEqual(c["threads"][b]["descends_from"], [])
        self.assertIn("review/x.py", c["hunted_anchors"])

    def test_an_aliased_residue_is_the_same_thread(self):
        """Identity still resolves first: a residue recorded under its
        pre-alias fingerprint carries the edge to the canonical identity,
        not to the raw string the verdict happened to hold."""
        a, old, new = ("fp2:00000000000000a1", "fp1:00000000000000b2",
                       "fp2:00000000000000b2")
        ledger = Ledger.in_memory()
        for r in (1, 2):
            _round(ledger, r, chr(96 + r) * 40)
        ledger.add(_finding(1, a, anchor="review/x.py"))
        ledger.add({"event": "lineage", "kind": "alias", "from_fp": old,
                    "to_fp": new, "reason": "fp1->fp2"})
        ledger.add({**_closure(2, a, "reclassified"),
                    "note": "narrowed; F1 carries the residue"})
        ledger.add(_finding(2, old, anchor="review/x.py"))
        c = ledger.convergence(LINEAGE)
        self.assertEqual(c["threads"][new]["descends_from"], [a])
        self.assertEqual(c["hunted_anchors"], {})

    def test_the_rendering_shows_the_edge_it_followed(self):
        """A count that silently shrinks is worse than the over-count it
        replaced. The table carries `continued` beside `new identities`, and
        the section names the residue, the round and the parent, so a reader
        can check the continuity the numbers now assume."""
        md = render_convergence_md(self._lineage_26().convergence(LINEAGE))
        self.assertIn("| round | findings | new identities | continued |", md)
        self.assertIn(f"- `{self.RESIDUE}` opened round 2 continuing "
                      f"`{self.PARENT}` (inferred from the closure's note)",
                      md)


class TestTheDeclaredResidueEdge(unittest.TestCase):
    """Brief `convergence-blind-to-reclassification`, the half that makes the
    edge a grammar rather than a reading of prose.

    The first cut followed the `reclassified` edge by finding a round-local
    finding id inside the closure's NOTE. That worked on the lineage-26
    record because its reviewer happened to write one; nothing asked for it,
    and a deterministic tool resting continuity on a sentence is resting it
    on nothing. `Residue:` declares it, the closure event records it as
    fingerprints, and this reading follows the declaration FIRST.

    Authoritative means exactly that: a closure that declares is never also
    note-read — not as a supplement and not as a tie-break — because the
    weaker source would otherwise add edges the stronger one declined. The
    note survives as the fallback for records written before the field, and
    every followed edge says which of the two it rests on.

    Every row below runs through the REAL `loupe ledger convergence` over a
    ledger on disk, because the report a human reads is the CLI's, and the
    labelling is only true if it survives to the payload.
    """

    def setUp(self):
        self.cwd = os.getcwd()
        self.addCleanup(os.chdir, self.cwd)

    def _convergence(self, *events, rounds=(1, 2)):
        tmp = scratch_tmp(self, "residue-edge-")
        ledger = Ledger(tmp)
        for r in rounds:
            ledger.add({"event": "request", "round": r, "sha": chr(96 + r) * 40,
                        "bytes": 1, "author": "claude", "reviewer": "codex"})
            ledger.add({"event": "verdict", "round": r,
                        "sha": chr(96 + r) * 40, "bytes": 1,
                        "verdict": "changes requested", "finding_ids": 1})
        for e in events:
            ledger.add(e)
        code, payload = run_cli(tmp, tmp, "ledger", "convergence",
                                cwd=self.cwd)
        self.assertEqual(code, 0, payload)
        return payload

    def _via(self, c, child):
        return c["threads"][child]["descends_via"]

    # ------------------------------------------------- the declared edge

    def test_a_declared_residue_is_followed_and_labelled_declared(self):
        """The base case, and the falsification for the whole class.

        Mutation: ignore the declaration — delete the `if declared:` branch
        from `_reclassification_edges` — and this note-less record follows
        nothing, `new_per_round` counts the residue as new, and the anchor
        is hunted again: the exact misreading the brief raised, now
        reachable for every reviewer who says what they mean instead of
        happening to write an id into a sentence."""
        c = self._convergence(
            _finding(1, A, anchor=X),
            # The note deliberately names NO id: the declaration is the
            # whole evidence, so nothing here can be passing by accident
            # through the note reading.
            _reclassified(2, A, note="High to Medium.", residue=[B]),
            _finding(2, B, anchor=X, fid="F1"))
        self.assertEqual(c["hunted_anchors"], {})
        self.assertEqual(c["new_per_round"], {"1": 1, "2": 0})
        self.assertEqual(c["reclassified_per_round"], {"1": 0, "2": 1})
        self.assertEqual(c["threads"][B]["descends_from"], [A])
        self.assertEqual(self._via(c, B), {A: DECLARED})
        self.assertEqual(c["threads"][A]["reclassified_to"], [B])
        self.assertIn("1 declared", c["reading"])

    def test_two_declared_residues_from_one_parent(self):
        """One closure may split a claim in two, which the note reading
        could never express: `narrowed to F1 and F2` names two ids and the
        prose cannot say whether it means both or neither."""
        c = self._convergence(
            _finding(1, A, anchor=X),
            _reclassified(2, A, note="split.", residue=[B, C]),
            _finding(2, B, anchor=X, fid="F1"),
            _finding(2, C, anchor=X, fid="F2"))
        self.assertEqual(c["hunted_anchors"], {})
        self.assertEqual(c["reclassified_per_round"], {"1": 0, "2": 2})
        self.assertEqual(c["threads"][A]["reclassified_to"], [B, C])
        self.assertEqual(self._via(c, B), {A: DECLARED})
        self.assertEqual(self._via(c, C), {A: DECLARED})

    def test_a_chain_of_declared_reclassifications(self):
        """Narrowed in two successive rounds: one new claim, not three, and
        each link labelled on its own evidence."""
        c = self._convergence(
            _finding(1, A, anchor=X),
            _reclassified(2, A, note="narrowed.", residue=[B]),
            _finding(2, B, anchor=X),
            _reclassified(3, B, note="narrowed again.", residue=[C]),
            _finding(3, C, anchor=X),
            rounds=(1, 2, 3))
        self.assertEqual(c["hunted_anchors"], {})
        self.assertEqual(c["new_per_round"], {"1": 1, "2": 0, "3": 0})
        self.assertEqual(self._via(c, B), {A: DECLARED})
        self.assertEqual(self._via(c, C), {B: DECLARED})
        self.assertEqual(c["stalled_threads"], [])

    def test_a_declared_edge_cannot_run_backwards(self):
        """The cycle attempt. A round-3 closure declaring the round-1 parent
        as the residue of its own descendant is refused by the same clause
        the note path is: descent runs from a STRICTLY earlier round, which
        is what makes the graph acyclic by construction.

        Mutation: drop the `first_seen[parent] < closure round` clause and
        the back-edge is recorded."""
        c = self._convergence(
            _finding(1, A, anchor=X),
            _reclassified(2, A, note="narrowed.", residue=[B]),
            _finding(2, B, anchor=X),
            _reclassified(3, B, note="back.", residue=[A]),
            _finding(3, A, anchor=X),   # the parent, re-raised
            rounds=(1, 2, 3))
        self.assertEqual(c["threads"][A]["descends_from"], [],
                         "the ancestor is not a residue of its own "
                         "descendant")
        self.assertEqual(self._via(c, B), {A: DECLARED})

    def test_a_declaration_from_an_unruled_or_foreign_parent(self):
        """Fail-closed on the parent, exactly as the note path is: a
        fingerprint this lineage never ruled supports no claim of descent,
        however precisely the residue is declared."""
        other = "fp2:00000000000000e5"
        for closed_fp, label in ((("fp2:" + "f" * 16), "no ruling anywhere"),
                                 (other, "ruled in another lineage")):
            with self.subTest(label):
                tmp = scratch_tmp(self, "residue-foreign-")
                ledger = Ledger(tmp)
                ledger.add({"event": "request", "round": 1, "sha": "z" * 40,
                            "bytes": 1, "author": "claude",
                            "reviewer": "codex", "lineage": "other"})
                ledger.add({**_finding(1, other, anchor=X),
                            "lineage": "other"})
                # Closed, so THIS repository has one open review and the
                # verb reads it without being told which. The foreign
                # ruling stays on the record, which is the whole point.
                ledger.add({"event": Ledger.LINEAGE_CLOSED, "at_round": 1,
                            "outcome": "decision", "reason": "fixture",
                            "authorized_by": "user", "open_request": False,
                            "lineage": "other"})
                for r in (1, 2):
                    ledger.add({"event": "request", "round": r,
                                "sha": chr(96 + r) * 40, "bytes": 1,
                                "author": "claude", "reviewer": "codex"})
                    ledger.add({"event": "verdict", "round": r,
                                "sha": chr(96 + r) * 40, "bytes": 1,
                                "verdict": "changes requested",
                                "finding_ids": 1})
                ledger.add(_finding(1, A, anchor=X))
                ledger.add(_reclassified(2, closed_fp, note="narrowed.",
                                         residue=[B]))
                ledger.add(_finding(2, B, anchor=X))
                code, c = run_cli(tmp, tmp, "ledger", "convergence",
                                  cwd=self.cwd)
                self.assertEqual(code, 0, c)
                self.assertIn(X, c["hunted_anchors"])
                self.assertEqual(c["threads"][B]["descends_from"], [])

    def test_a_genuinely_new_finding_beside_a_declared_residue(self):
        """The control this fix must not suppress. A residue and an
        unrelated finding arrive on one anchor in one round: the anchor
        stays hunted, naming the second and only the second.

        Mutation: subtract every new identity of the closure's round on the
        parent's anchor instead of the declared one, and the report goes
        blind to what it exists to see."""
        c = self._convergence(
            _finding(1, A, anchor=X),
            _reclassified(2, A, note="narrowed.", residue=[B]),
            _finding(2, B, anchor=X, fid="F1"),
            _finding(2, D, anchor=X, fid="F2"))
        self.assertEqual(c["state"], "hunting")
        self.assertEqual(c["hunted_anchors"][X], {"1": [A], "2": [D]})
        self.assertEqual(c["new_per_round"], {"1": 1, "2": 1})
        self.assertEqual(c["reclassified_per_round"], {"1": 0, "2": 1})

    def test_an_aliased_declared_residue_is_the_same_thread(self):
        """Identity resolves first: a declaration recorded under a
        pre-alias fingerprint carries the edge to the canonical identity,
        not to the string the verdict happened to hold."""
        old, new = "fp1:00000000000000b2", B
        c = self._convergence(
            _finding(1, A, anchor=X),
            {"event": "lineage", "kind": "alias", "from_fp": old,
             "to_fp": new, "reason": "fp1->fp2"},
            _reclassified(2, A, note="narrowed.", residue=[old]),
            _finding(2, old, anchor=X))
        self.assertEqual(self._via(c, new), {A: DECLARED})
        self.assertEqual(c["hunted_anchors"], {})

    def test_a_declared_id_that_is_not_new_in_that_round(self):
        """A residue is a claim that begins where the parent was narrowed.
        An identity RULED BEFORE the closure is a returning finding, not a
        remainder, and declaring it does not make it one — the same bound
        the note path carries, applied to the field."""
        c = self._convergence(
            _finding(1, A, anchor=X, fid="F1"),
            _finding(1, B, anchor="review/y.py", fid="F2"),
            _reclassified(2, A, note="narrowed.", residue=[B]),
            _finding(2, B, anchor="review/y.py", fid="F1"))
        self.assertEqual(c["threads"][B]["descends_from"], [])
        self.assertEqual(c["new_per_round"], {"1": 2, "2": 0})

    def test_a_declaration_on_a_term_that_claims_no_descent(self):
        """The validator refuses this before it can be recorded
        (C-RESIDUE-TERM); the reading refuses it again, because a ledger is
        read long after the validator that admitted its rows, and `hunted`
        must not depend on a check that ran somewhere else."""
        for term in ("withdrawn", "sustained"):
            with self.subTest(term):
                c = self._convergence(
                    _finding(1, A, anchor=X),
                    {**_closure(2, A, term), "residue": [B]},
                    _finding(2, B, anchor=X))
                self.assertEqual(c["threads"][B]["descends_from"], [])
                self.assertIn(X, c["hunted_anchors"])

    # --------------------------------------- declared beats the note read

    def test_the_declaration_wins_and_the_note_is_not_read(self):
        """The ranking, measured where the two disagree: the note names F2
        and the declaration names F1. The declared edge is followed, the
        note's is not, and the count is one — not two.

        Mutation: read the note IN ADDITION to the declaration (drop the
        `continue`) and F2 gains an edge the reviewer never declared, which
        is the weaker source overruling the stronger."""
        c = self._convergence(
            _finding(1, A, anchor=X),
            _reclassified(2, A, note="narrowed; F2 carries the residue",
                          residue=["fp2:00000000000000b2"]),
            _finding(2, B, anchor=X, fid="F1"),
            _finding(2, C, anchor=X, fid="F2"))
        self.assertEqual(self._via(c, B), {A: DECLARED})
        self.assertEqual(c["threads"][C]["descends_from"], [],
                         "the note is not consulted at all once a closure "
                         "declares; reading both lets the weaker source add "
                         "edges the stronger one declined")
        self.assertEqual(c["reclassified_per_round"], {"1": 0, "2": 1})

    def test_a_refused_declaration_does_not_fall_back_to_the_note(self):
        """The sharp end of the same rule. The declaration names an id the
        record does not bear out; the note names one it does. A fallback
        here would mean a reviewer's own declaration being quietly replaced
        by a guess at their prose — so the closure follows nothing, and the
        round reads exactly as it did before the field existed."""
        c = self._convergence(
            _finding(1, A, anchor=X),
            _reclassified(2, A, note="narrowed; F1 carries the residue",
                          residue=["fp2:00000000000000ff"]),
            _finding(2, B, anchor=X, fid="F1"))
        self.assertEqual(c["threads"][B]["descends_from"], [])
        self.assertIn(X, c["hunted_anchors"])

    def test_a_record_with_only_a_note_is_still_followed_and_labelled(self):
        """The legacy record — every closure written before this field,
        lineage 26's among them. The note reading survives as the fallback
        for exactly those, and says so in the label rather than passing
        itself off as a declaration.

        Mutation: label every edge `declared` and this fails, which is the
        report claiming a reviewer wrote something they did not."""
        c = self._convergence(
            _finding(1, A, anchor=X),
            _reclassified(2, A, note="narrowed; F1 carries the residue"),
            _finding(2, B, anchor=X, fid="F1"))
        self.assertEqual(self._via(c, B), {A: INFERRED})
        self.assertEqual(c["hunted_anchors"], {})
        self.assertIn("1 inferred from the closure's note", c["reading"])

    def test_the_rendering_labels_every_edge_it_followed(self):
        """Two threads on one report, one of each kind. A reader deciding
        whether to trust a count that shrank is owed the source of each
        subtraction, and a rendering that prints them alike hides exactly
        the one worth checking."""
        c = self._convergence(
            _finding(1, A, anchor=X, fid="F1"),
            _finding(1, C, anchor="review/y.py", fid="F2"),
            _reclassified(2, A, note="High to Medium.", residue=[B]),
            _reclassified(2, C, note="narrowed; F2 carries the residue"),
            _finding(2, B, anchor=X, fid="F1"),
            _finding(2, D, anchor="review/y.py", fid="F2"))
        md = render_convergence_md(c)
        self.assertIn(f"- `{B}` opened round 2 continuing `{A}` (declared)",
                      md)
        self.assertIn(f"- `{D}` opened round 2 continuing `{C}` "
                      f"(inferred from the closure's note)", md)
        self.assertIn("| round | findings | new identities | continued |", md)


class TestTheDeclaredResidueReachesTheRecord(unittest.TestCase):
    """The declaration is only worth anything if it is IN the record: a
    field the verdict carries and the ledger drops is prose again by the
    next round. Both product ingress paths are driven here — `loupe close`,
    which is how a real round records a verdict, and `loupe ledger add`,
    the same shape authority through the other door — and what they write
    is fingerprints, resolved once against the verdict that declares them.
    """

    PARENT = "fp2:836d98fc52230998"

    def _verdict(self, sha, residue_line="  Residue: F1"):
        return (f'<loupe-review-verdict sha="{sha}">\n'
                f"VERDICT: changes requested\n\n## findings\n\n"
                f"{synth.finding(1, evidence='f.txt:1')}"
                f"## closures\n\n"
                f"- {self.PARENT} reclassified: High to Medium; the rest is "
                f"fixed\n{residue_line}\n\n"
                f"## evidence checked\n\nf.txt\n</loupe-review-verdict>\n")

    def _closure_event(self, state):
        events = [e for e in Ledger(state).events()
                  if e.get("event") == "closure"]
        self.assertEqual(len(events), 1, events)
        return events[0]

    def test_close_records_the_declaration_as_fingerprints(self):
        """The product path, for real: a scratch repository, a real
        `handoff`, a verdict that declares its residue, and a real `close`.

        Mutation: drop `residue_fps` from the `as_event` call in
        `transport.verdict_events` and the recorded closure carries no
        residue — the declaration validated, and then existed nowhere a
        later round could read it."""
        scratch = scratch_loop_repo(self, "residue-close-")
        state = scratch.tmp / "state"
        code, rec = run_cli(scratch.repo, state, "handoff",
                            "--claim-file", str(scratch.claim),
                            "--base", scratch.base, "--local-only",
                            cwd=scratch.cwd)
        self.assertEqual(code, 0, rec)
        path = scratch.tmp / "v1.md"
        path.write_text(self._verdict(rec["sha"]), encoding="utf-8")
        code, closed = run_cli(scratch.repo, state, "close", "--verdict",
                               str(path), cwd=scratch.cwd)
        self.assertEqual(code, 0, closed)
        event = self._closure_event(state)
        finding = [e for e in Ledger(state).events()
                   if e.get("event") == "finding"][0]
        self.assertEqual(event["residue"], [finding["fp"]],
                         "the declaration is recorded by the id every other "
                         "event binds to, resolved once against the verdict "
                         "that carries both halves")
        self.assertEqual(event["closure"], "reclassified")

    def test_ledger_add_records_it_through_the_same_authority(self):
        """The other door onto `transport.verdict_events`, and the paired
        control: a closure that declares nothing records no `residue`
        member at all — absent, not an empty list, because a record that
        states an empty declaration is stating something the reviewer did
        not."""
        for line, expected in (("  Residue: F1", True), ("", False)):
            with self.subTest(declared=expected):
                tmp = scratch_tmp(self, "residue-add-")
                ledger = Ledger(tmp)
                ledger.add({"event": "request", "round": 1, "sha": SHA_R,
                            "bytes": 1, "author": "claude",
                            "reviewer": "codex"})
                path = tmp / "v.md"
                path.write_text(self._verdict(SHA_R, residue_line=line)
                                .replace("\n\n\n", "\n\n"), encoding="utf-8")
                cwd = os.getcwd()
                self.addCleanup(os.chdir, cwd)
                # From the repository root, because `ledger add` validates
                # the verdict and a taxonomy has to come from somewhere;
                # the LEDGER is still the scratch one.
                code, out = run_cli(REPO_ROOT, tmp, "ledger", "add",
                                    str(path), "--round", "1", cwd=cwd)
                self.assertEqual(code, 0, out)
                event = self._closure_event(tmp)
                self.assertEqual("residue" in event, expected, event)

    def test_a_declaration_naming_no_finding_refuses_at_the_record(self):
        """The record's own bound, unreachable through `close` because the
        validator refuses it first (C-RESIDUE-UNKNOWN) — and stated anyway,
        because the alternative when it IS reached is a closure event whose
        declared edge names fewer residues than the reviewer wrote, which
        is the silently shrinking count this brief is about."""
        parsed = wire.parse_verdict(self._verdict(SHA_R,
                                                  residue_line="  Residue: F9"))
        with self.assertRaises(transport.Refusal) as ctx:
            transport.verdict_events(parsed, 1, "d", 1)
        self.assertIn("Residue: F9", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

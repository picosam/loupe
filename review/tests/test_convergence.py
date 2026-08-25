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
import unittest

from review import validate, vocab, wire
from review.ledger import Ledger, render_convergence_md
from review.tests import synth

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
        fired = [f["breaker"] for f in ledger.breakers(
            3, blocking_severities=["High"])]
        self.assertEqual(sorted(set(fired)), ["budget"],
                         "the breaker must still FIRE and be reported, and "
                         "it must be the only one firing here or this test "
                         "proves nothing about which one was exempted")
        transport.handoff_preflight(CFG, ledger)  # no raise

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
        self.assertIn("orphan", [f["breaker"] for f in ledger.breakers(
            3, blocking_severities=["High"])])
        with self.assertRaises(transport.Refusal):
            transport.handoff_preflight(CFG, ledger)

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
        ledger = self._spent(Ledger.in_memory(), 150_000, 60_000)
        fired = [f for f in ledger.breakers(
            3, token_budget=CFG.token_budget, blocking_severities=["High"])
            if f["breaker"] == "budget"]
        self.assertEqual([f["limit"] for f in fired], ["tokens"],
                         "the token breach is not firing; the probe is "
                         "measuring nothing")
        with self.assertRaises(transport.Refusal) as ctx:
            transport.handoff_preflight(CFG, ledger)
        self.assertIn("budget", str(ctx.exception))

    def test_an_under_budget_lineage_passes(self):
        """The paired control: counted, under the ceiling, no refusal."""
        from review import transport
        ledger = self._spent(Ledger.in_memory(), 1_000, 1_000)
        self.assertEqual(
            [f for f in ledger.breakers(3, token_budget=CFG.token_budget,
                                        blocking_severities=["High"])
             if f["breaker"] == "budget" and f["limit"] == "tokens"], [])
        transport.handoff_preflight(CFG, ledger)  # no raise

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
        limits = [f.get("limit") for f in ledger.breakers(
            3, blocking_severities=["High"]) if f["breaker"] == "budget"]
        self.assertEqual(limits, ["rounds"])
        transport.handoff_preflight(CFG, ledger)  # no raise


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
        c = ledger.convergence()
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
        c = ledger.convergence()
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
        c = ledger.convergence()
        self.assertEqual(c["stalled_threads"], [])
        self.assertTrue(c["threads"][fp]["withdrawn"])

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
        c = ledger.convergence()
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
        self.assertEqual(ledger.convergence()["hunted_anchors"], {})

    def test_the_report_never_claims_more_than_it_counted(self):
        """The overclaim guard. Three rounds of this lineage were spent on
        exactly this failure — a mechanism asserting a property no test
        demonstrated — so the reading is required to stay a reading."""
        ledger = Ledger.in_memory()
        for r in (1, 2, 3):
            _round(ledger, r, chr(96 + r) * 40)
            ledger.add(_finding(r, f"fp2:{r:016x}", anchor="review/id.py"))
        c = ledger.convergence()
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
        c = ledger.convergence()
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
        c = ledger.convergence()
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
        c = ledger.convergence()
        rounds = c["hunted_anchors"].get("review/id.py", {})
        self.assertEqual(
            rounds, {1: [recurring], 2: ["fp2:00000000beefbeef"]},
            "a recurring identity is rendered as a fresh finding in the "
            "round it merely returned in")
        self.assertNotIn(3, rounds,
                         "round 3 opened nothing new and must not appear")

    def test_an_empty_lineage_says_so_rather_than_guessing(self):
        c = Ledger.in_memory().convergence()
        self.assertEqual(c["state"], "open")
        self.assertIn("too little recorded", c["reading"])


if __name__ == "__main__":
    unittest.main()

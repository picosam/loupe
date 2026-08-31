"""§9bis.3 / RVW-T9: wire-to-ledger event derivation — request and response
events, evidence ingestion, verdicts, disposition supersession, orphan
companions, breaker firing identity and the span report.

Split from `test_transport.py` 2026-08-31; the classes are verbatim.
"""

import dataclasses
import json
import re
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from review import cli, config, transport, validate, wire
from review import ledger as ledger_mod
from review.emit import _git
from review.ledger import Ledger
from review.tests.util import REPO_ROOT
from review.tests._transport_fixtures import (
    CFG, SHA_A, SHA_B, SHA_C, request_text, verdict_text, _cli)

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


if __name__ == "__main__":
    unittest.main()

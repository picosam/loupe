"""§9bis.3 / RVW-T9: wire-to-ledger event derivation — request and response
events, evidence ingestion, verdicts, disposition supersession, orphan
companions, breaker firing identity and the span report.

Split from `test_transport.py` 2026-08-31; the classes are verbatim.
"""

import dataclasses
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from review import cli, config, fingerprint, transport, validate, vocab, wire
from review import ledger as ledger_mod
from review.emit import _git
from review.ledger import Ledger
from review.tests.util import LINEAGE, REPO_ROOT
from review.tests._transport_fixtures import (
    CFG, SHA_A, SHA_B, SHA_C, request_text, verdict_text, _cli)

#: "no argument given" told apart from "the argument None was given" — the
#: manifest tests need both.
_UNSET = object()

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
        self.assertEqual(ledger.add_all(expected, lineage=payload["lineage"]),
                         0)

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
            self.cfg, ledger, text, "v.md",LINEAGE,
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
        transport.close_lineage(Ledger(self.tmp), "done", "user", LINEAGE)
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
            transport.close_round(self.cfg, Ledger(self.tmp), second, "v.md",LINEAGE,
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
            Ledger(self.tmp), LINEAGE, digest=transport._digest_text(first)))
        # Control: one recorded verdict, one response.
        clean = dataclasses.replace(self.cfg,
                                    ledger_dir=self.tmp / "control")
        (self.tmp / "control").mkdir()
        led = Ledger(clean.ledger_dir)
        led.add({"event": "request", "round": 1, "sha": SHA_B, "bytes": 1,
                 "author": "claude", "reviewer": "codex"})
        transport.close_round(clean, led, first, "v.md",LINEAGE,
                              validate_items=lambda v:
                              validate.validate_verdict(v, clean))
        self.assertIsNotNone(transport.recorded_verdict(
            Ledger(clean.ledger_dir), LINEAGE, digest=transport._digest_text(first)))

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
        return [b["breaker"] for b in ledger.breakers(LINEAGE, round_cap=5)]

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

        fired = [b["breaker"] for b in ledger.breakers(LINEAGE, round_cap=5)]
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
        fired = [b["breaker"] for b in ledger.breakers(LINEAGE, round_cap=5)]
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
        metric = ledger.metrics(LINEAGE, gate_manifest=["tests", "whitespace"])
        preventable = metric["rounds"][1]["deterministic_preventable"]
        self.assertEqual(preventable["count"], 1)
        self.assertEqual(preventable["gates"], ["tests"])

    def test_no_label_reports_not_captured_rather_than_zero(self):
        # The honest-uncomputable half: absent is not zero.
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": SHA_B, "bytes": 1})
        ledger.add_all(transport.verdict_events(self._verdict(), 1, "d", 1))
        metric = ledger.metrics(LINEAGE, gate_manifest=["tests"])
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

        metric = ledger.metrics(LINEAGE, gate_manifest=["tests", "whitespace"])
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
        full = (complete.metrics(LINEAGE, gate_manifest=["tests", "whitespace"])
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
        self.assertEqual(led.rounds_for_sha(SHA_B, LINEAGE), [1, 2])
        self.assertEqual(led.round_for_sha(SHA_B, LINEAGE), 2,
                         "the open round is the one awaiting an answer")

    def test_close_files_the_verdict_against_the_open_round(self):
        led = self._ledger()
        led.add({"event": "verdict", "round": 1, "sha": SHA_B})
        led.add({"event": "request", "round": 2, "sha": SHA_B, "bytes": 1,
                 "author": "claude", "reviewer": "codex"})
        rec = transport.close_round(self._cfg(), led, verdict_text(), "v.md", LINEAGE)
        self.assertEqual(rec["round"], 2)

    def test_two_open_rounds_on_one_sha_refuse_rather_than_guess(self):
        led = self._ledger()
        led.add({"event": "request", "round": 2, "sha": SHA_B, "bytes": 1,
                 "author": "claude", "reviewer": "codex"})
        self.assertEqual(led.ambiguous_rounds_for_sha(SHA_B, LINEAGE), [1, 2])
        before = len(led.events())
        with self.assertRaises(transport.Refusal) as ctx:
            transport.close_round(self._cfg(), led, verdict_text(), "v.md", LINEAGE)
        self.assertIn("more than one OPEN request", str(ctx.exception))
        self.assertEqual(len(led.events()), before)

    def test_a_superseded_emission_is_not_ambiguity(self):
        # Re-emitting the SAME round twice is the common case and must still
        # resolve: two request events, one round.
        led = self._ledger()
        led.add({"event": "request", "round": 1, "sha": SHA_B, "bytes": 2,
                 "author": "claude", "reviewer": "codex"})
        self.assertEqual(led.ambiguous_rounds_for_sha(SHA_B, LINEAGE), [])
        self.assertEqual(led.round_for_sha(SHA_B, LINEAGE), 1)


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
        owed = transport.missing_dispositions(ledger,LINEAGE)
        self.assertIsNotNone(owed)
        self.assertEqual([u["fp"] for u in owed["unanswered"]], [self.FP])

    def test_one_answer_satisfies(self):
        ledger = self._round1(Ledger.in_memory())
        ledger.add(self._disposition("a" * 40))
        self.assertIsNone(transport.missing_dispositions(ledger,LINEAGE))

    def test_a_rebound_answer_supersedes_instead_of_dead_ending(self):
        # The incident, reproduced: two batches for the same fingerprint,
        # differing head. The newest is the standing answer; the preflight
        # passes; both events remain in the append-only file.
        ledger = self._round1(Ledger.in_memory())
        ledger.add(self._disposition("a" * 40))
        ledger.add(self._disposition("b" * 40))
        self.assertIsNone(transport.missing_dispositions(ledger,LINEAGE))
        standing = ledger.standing_dispositions(LINEAGE, round_no=1)
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
        self.assertEqual(len(ledger.standing_dispositions(LINEAGE)), 2)
        self.assertEqual(len(ledger.standing_dispositions(LINEAGE, round_no=1)), 1)
        self.assertEqual(
            ledger.standing_dispositions(LINEAGE, round_no=1)[0]["head"], "a" * 40)


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
        batches = ledger.standing_disposition_batches(LINEAGE, round_no=1)
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]["disposition"]["head"], "a" * 40)
        self.assertEqual(len(batches[0]["evidence"]), 1)
        self.assertEqual(len(batches[0]["runs"]), 1)
        self.assertIsNone(transport.missing_dispositions(ledger,LINEAGE))

    def test_idempotent_replay_is_a_no_op(self):
        # The same envelope re-recorded: same batch stamp, same uids.
        ledger = self._round1(Ledger.in_memory())
        events = self._emission("a" * 40, evidence="e1")
        self.assertEqual(ledger.add_all(events), 3)
        self.assertEqual(ledger.add_all(events), 0)
        self.assertEqual(len(ledger.standing_disposition_batches(LINEAGE, 1)), 1)

    def test_a_rebind_with_changed_head_supersedes_the_whole_batch(self):
        # The falsification of the finding: an accepted `cannot_execute`
        # answer, then a newer accepted `pass` answer. The newer head
        # stands AND the superseded run no longer fires `unverifiable`.
        ledger = self._round1(Ledger.in_memory())
        ledger.add_all(self._emission("b" * 40, status="cannot_execute",
                                      mutation="not_run"))
        ledger.add_all(self._emission("c" * 40, status="pass"))
        self.assertEqual(ledger.standing_dispositions(LINEAGE, 1)[0]["head"], "c" * 40)
        fired = ledger.breakers(LINEAGE, 3, blocking_severities=["High"])
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
        fired = ledger.breakers(LINEAGE, 3, blocking_severities=["High"])
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
                   for b in ledger.disposition_batches(LINEAGE, 1)
                   if b["disposition"]}
        self.assertEqual([r["status"] for r in batches["A"]["runs"]],
                         ["cannot_execute"])
        self.assertEqual(batches["A"]["standing"], False)
        self.assertEqual(batches["B"]["runs"], [])
        self.assertEqual(batches["B"]["standing"], True)
        # And the consumer proof: the run belongs to superseded A, so it
        # does not fire even though it is the newest run event in the file.
        fired = ledger.breakers(LINEAGE, 3, blocking_severities=["High"])
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
        standing = ledger.standing_disposition_batches(LINEAGE, 1)
        self.assertEqual(len(standing), 1)
        self.assertEqual([r["status"] for r in standing[0]["runs"]], ["pass"])
        fired = ledger.breakers(LINEAGE, 3, blocking_severities=["High"])
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
        fired = ledger.breakers(LINEAGE, 3, blocking_severities=["High"])
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
        counts = ledger.metrics(LINEAGE)["rounds"][1]["dispositions"]
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
        rounds = ledger.metrics(LINEAGE)["rounds"]
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
                states = ledger.metrics(LINEAGE)["rounds"][1][
                    "unverified_acceptance"]
                self.assertEqual(states, {expected: 1})

    def test_acceptance_with_no_standing_run_is_the_named_absence(self):
        # A superseded emission HAD a run; the standing one has none. The
        # rigor leak must be reported, not papered over by the stale run.
        ledger = self._round1(Ledger.in_memory())
        ledger.add_all(self._emission("b" * 40, status="pass"))
        ledger.add_all(self._emission("c" * 40, status=None))
        states = ledger.metrics(LINEAGE)["rounds"][1]["unverified_acceptance"]
        self.assertEqual(states, {"named test, no run recorded": 1})

    def test_superseded_refutation_evidence_is_history(self):
        # The batch projection keeps each emission's evidence with its
        # emission; only the standing batch's evidence reaches consumers.
        ledger = self._round1(Ledger.in_memory())
        ledger.add_all(self._emission("b" * 40, disposition="refuted",
                                      status=None, evidence="old"))
        ledger.add_all(self._emission("c" * 40, disposition="refuted",
                                      status=None, evidence="new"))
        standing = ledger.standing_disposition_batches(LINEAGE, 1)
        self.assertEqual([e["digest"] for e in standing[0]["evidence"]],
                         ["d-new"])
        all_batches = ledger.disposition_batches(LINEAGE, 1)
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
        standing = ledger.standing_disposition_batches(LINEAGE, 1)
        self.assertEqual(len(standing), 1)
        self.assertEqual(standing[0]["disposition"]["head"], "c" * 40)
        fired = ledger.breakers(LINEAGE, 3, blocking_severities=["High"])
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
        owed = transport.missing_dispositions(ledger,LINEAGE)
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
        block = _dispositions_block(ledger, 1, LINEAGE)
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
        return (ledger.standing_disposition_batches(LINEAGE, 1),
                ledger.orphan_companion_batches(LINEAGE, 1),
                [b["breaker"] for b in ledger.breakers(LINEAGE,
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
            ledger.metrics(LINEAGE)["rounds"][1]["unverified_acceptance"],
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
            ledger.metrics(LINEAGE)["rounds"][1]["unverified_acceptance"],
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
            ledger.metrics(LINEAGE)["rounds"][1]["unverified_acceptance"],
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
        owed = transport.missing_dispositions(ledger,LINEAGE)
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
        block = _dispositions_block(ledger, 1, LINEAGE)
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
        fired = [f for f in transport.unauthorized_breakers(cfg, ledger, LINEAGE)
                 if f["breaker"] == "orphan"]
        self.assertEqual(len(fired), 1)
        rec = transport.authorize_breaker(cfg, ledger, "orphan",
                                          "audited: hand-seeded probe",
                                          "user",LINEAGE)
        self.assertTrue(rec["recorded"])
        self.assertEqual(
            [f for f in transport.unauthorized_breakers(cfg, ledger, LINEAGE)
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
        return [f for f in transport.unauthorized_breakers(cfg, ledger, LINEAGE)
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
            transport.handoff_preflight(cfg, ledger, LINEAGE)
        rec = transport.authorize_breaker(cfg, ledger, "orphan",
                                          "audited orphan-X", "user", LINEAGE)
        self.assertEqual(rec["covers"], [fired[0]["firing"]])
        # The control, first: re-reading the UNCHANGED X firing stays
        # covered, and the lineage moves.
        self.assertEqual(self._unauthorized_orphans(cfg, ledger), [])
        transport.handoff_preflight(cfg, ledger, LINEAGE)  # no raise
        # A second, distinct stamped orphan for the same (round, fp) is
        # material the human never saw.
        ledger.add(self._orphan_run("orphan-Y", "td-y"))
        self.assertEqual(len(ledger.orphan_companion_batches(LINEAGE)), 2)
        still = self._unauthorized_orphans(cfg, ledger)
        self.assertEqual(len(still), 1, "orphan-Y was pre-authorized by the "
                                        "decision on orphan-X")
        self.assertEqual([r["batch"] for r in
                          [b for b in ledger.orphan_companion_batches(LINEAGE)
                           if b["runs"][0]["test_digest"] == "td-y"][0]["runs"]],
                         ["orphan-Y"])
        self.assertNotIn(still[0]["firing"], rec["covers"])
        with self.assertRaises(transport.Refusal) as ctx:
            transport.handoff_preflight(cfg, ledger, LINEAGE)
        self.assertIn(still[0]["firing"], str(ctx.exception))
        # ...and X stays decided: the second decision covers only Y.
        rec2 = transport.authorize_breaker(cfg, ledger, "orphan",
                                           "audited orphan-Y", "user", LINEAGE)
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
                                    "audited the rowless batch", "user", LINEAGE)
        # Control: unchanged, still one batch, still covered.
        self.assertEqual(len(ledger.orphan_companion_batches(LINEAGE)), 1)
        self.assertEqual(self._unauthorized_orphans(cfg, ledger), [])
        transport.handoff_preflight(cfg, ledger, LINEAGE)  # no raise
        # A later companion joins that same batch — still ONE anomaly, but
        # not the one that was decided.
        ledger.add(companion("cannot_execute"))
        self.assertEqual(len(ledger.orphan_companion_batches(LINEAGE)), 1)
        grown = self._unauthorized_orphans(cfg, ledger)
        self.assertEqual(len(grown), 1, "new post-decision material was "
                                        "covered by the earlier decision")
        self.assertNotEqual(grown[0]["firing"], first[0]["firing"])
        with self.assertRaises(transport.Refusal):
            transport.handoff_preflight(cfg, ledger, LINEAGE)

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
                                          "audited orphan-X", "user", LINEAGE)
        ledger.add({"event": "evidence", "round": 1, "fp": self.FP,
                    "digest": "d-verdict", "source": "verdict", "of": "F1"})
        ledger.add({"event": "gate", "round": 1, "id": "tests",
                    "status": "pass"})
        after = ledger.orphan_companion_batches(LINEAGE)
        self.assertEqual(len(after), 1)
        self.assertEqual(self._unauthorized_orphans(cfg, ledger), [])
        self.assertEqual(rec["covers"],
                         [f["firing"] for f in ledger.breakers(LINEAGE,
                             3, blocking_severities=["High"])
                          if f["breaker"] == "orphan"])
        transport.handoff_preflight(cfg, ledger, LINEAGE)  # no raise


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
        return [f for f in transport.unauthorized_breakers(cfg, ledger, LINEAGE)
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
        fired = ledger.breakers(LINEAGE, 3, blocking_severities=["High"])
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
                                    "runner unavailable, accepted", "user", LINEAGE)
        self.assertEqual(self._of(cfg, ledger, "unverifiable"), [])
        ledger.add(cannot_execute("td-2"))
        second = self._of(cfg, ledger, "unverifiable")
        self.assertEqual(len(second), 1, "a distinct unexecutable run was "
                                         "pre-authorized by the decision on "
                                         "the first")
        self.assertNotEqual(second[0]["firing"], first[0]["firing"])
        with self.assertRaises(transport.Refusal):
            transport.handoff_preflight(cfg, ledger, LINEAGE)

    def test_a_grown_token_spend_is_not_the_decided_breach(self):
        from unittest import mock
        cfg = self._cfg()
        ledger = self._round1(Ledger.in_memory())
        ledger.add_all(self._emission("b" * 40, batch="A"))
        envelopes = [e for e in ledger.current(LINEAGE)
                     if e.get("event") in ("request", "verdict")]
        for e in envelopes:
            e["tokens"] = 200
        with mock.patch.object(type(cfg), "token_budget",
                               property(lambda _s: 100)):
            first = self._of(cfg, ledger, "budget")
            self.assertEqual(len(first), 1)
            self.assertIn("400", first[0]["rule"])
            transport.authorize_breaker(cfg, ledger, "budget",
                                        "audited: 400 against 100", "user", LINEAGE)
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
        report = ledger.report(LINEAGE, 3, blocking_severities=["High"])
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


class _ImportLegacyCase(unittest.TestCase):
    """Shared plumbing for driving `cmd_import_legacy` over an in-memory
    ledger with a JSONL fixture AND a REAL GIT ANCHOR.

    Round-10 F2 replaced round 9's caller-written source manifest, so the
    fixture is now a real repository with a real remote: two tracked files
    committed and pushed, and the pushed commit as the anchor every row
    cites. Nothing here is mocked — the door runs the same `git ls-tree`,
    `git show` and `git for-each-ref --contains` it runs in production, so a
    test that passes proves the production reads pass.

    The source file is BUILT from `SOURCE_CLAIMS` because a row must now
    name bytes that carry its own words. A fixture claim string absent from
    that list refuses on containment, which is the point; the tests that
    exercise containment use strings deliberately left out.
    """

    CLASSIFICATION = "design_gap"
    ANCHOR = "review/x.py"

    SPLIT_PATH = "sources/split.md"
    VERDICT_PATH = "sources/verdict.md"

    #: Every claim text the helpers below can state, as the source carries
    #: it. One line each, in the shape a legacy split table uses.
    SOURCE_CLAIMS = (
        "t", "a real claim", "a claim", "a different claim",
        "an atomic claim", "an unrelated claim", "one atomic claim",
        "claim a", "claim b", "claim c", "claim p", "claim x",
        "claim new", "claim old", "claim one", "claim two",
        "x", "y", "stated", "briefs/x.md", "when it bites",
        "R1-F1", "R1-F1a",
        # Round 6 F1: an imported `request` row's `author`/`reviewer` are
        # LEGACY_CONTAINMENT members too, so a legacy request naming either
        # actor must cite bytes that carry the name.
        "claude", "codex",
    )

    @classmethod
    def source_text(cls) -> str:
        return "".join(f"| R1-F1a | {c} |\n" for c in cls.SOURCE_CLAIMS)

    #: `changes requested` is the verdict TERM a verdict row must contain.
    VERDICT_TEXT = ("VERDICT: changes requested\n\n"
                    "a legacy round-1 verdict\n")

    @classmethod
    def setUpClass(cls):
        try:
            cls.src = Path(tempfile.mkdtemp(prefix="legacy-anchor-"))
        except OSError as exc:                       # pragma: no cover
            raise unittest.SkipTest(f"filesystem writes denied ({exc})")
        cls.addClassCleanup(shutil.rmtree, cls.src, ignore_errors=True)
        cls.repo = cls.src / "work"
        bare = cls.src / "origin.git"
        subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
        cls.repo.mkdir()
        cls._git("init", "-q", "-b", "main")
        cls._git("config", "user.email", "fixture@invalid")
        cls._git("config", "user.name", "fixture")
        cls._git("remote", "add", "origin", str(bare))
        cls.split_bytes = cls.source_text().encode("utf-8")
        cls.verdict_bytes = cls.VERDICT_TEXT.encode("utf-8")
        (cls.repo / "sources").mkdir()
        (cls.repo / cls.SPLIT_PATH).write_bytes(cls.split_bytes)
        (cls.repo / cls.VERDICT_PATH).write_bytes(cls.verdict_bytes)
        cls._git("add", cls.SPLIT_PATH, cls.VERDICT_PATH)
        cls._git("commit", "-q", "-m", "the legacy corpus sources")
        cls._git("push", "-q", "origin", "main")
        cls._git("fetch", "-q", "origin")
        cls.anchor = cls._git("rev-parse", "HEAD")
        cls.SPLIT_DIGEST = hashlib.sha256(cls.split_bytes).hexdigest()
        cls.VERDICT_DIGEST = hashlib.sha256(cls.verdict_bytes).hexdigest()
        #: A commit a remote-tracking ref contains, so an envelope row has a
        #: target it can state. The anchor itself is the obvious one.
        cls.TARGET = cls.anchor

    @classmethod
    def _git(cls, *args):
        out = subprocess.run(["git", "-C", str(cls.repo), *args],
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0:                      # pragma: no cover
            raise AssertionError(f"git {args}: {out.stderr}")
        return out.stdout.strip()

    def local_only_commit(self, text: str) -> tuple[str, str]:
        """A commit made and NEVER pushed, plus the digest of the split file
        in it. Restores the branch afterwards so the class-level anchor is
        untouched: this is the "invented in the same session" case."""
        was = self._git("rev-parse", "HEAD")
        (self.repo / self.SPLIT_PATH).write_bytes(text.encode("utf-8"))
        self._git("add", self.SPLIT_PATH)
        self._git("commit", "-q", "-m", "local only")
        sha = self._git("rev-parse", "HEAD")
        self.addCleanup(self._git, "reset", "-q", "--hard", was)
        return sha, hashlib.sha256(text.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------ fixtures

    def source(self, path=None, digest=None) -> dict:
        path = self.SPLIT_PATH if path is None else path
        known = {self.SPLIT_PATH: self.SPLIT_DIGEST,
                 self.VERDICT_PATH: self.VERDICT_DIGEST}
        return {"source_path": path,
                "source_digest": digest or known.get(path, "0" * 64)}

    def finding(self, round_no, title, fid="F1", anchor_path=None,
                digest=None, path=None, **extra):
        """One well-formed `finding` row: the identity facts, and the
        fingerprint they actually hash to."""
        anchor_path = self.ANCHOR if anchor_path is None else anchor_path
        row = {"event": "finding", "round": round_no, "id": fid,
               "severity": "High", "classification": self.CLASSIFICATION,
               "title": title, "anchor_path": anchor_path,
               **self.source(path, digest)}
        row["fp"] = fingerprint.compute(self.CLASSIFICATION, anchor_path, "",
                                        title)
        row.update(extra)
        return row

    def atomic(self, round_no, verbatim, legacy_id="R1-F1a", digest=None,
               path=None, **extra):
        row = {"event": "import", "kind": "atomic", "round": round_no,
               "legacy_id": legacy_id, "severity": "High",
               "classification": self.CLASSIFICATION, "verbatim": verbatim,
               "anchor_path": self.ANCHOR, **self.source(path, digest)}
        row["fp"] = fingerprint.compute(self.CLASSIFICATION, self.ANCHOR, "",
                                        verbatim)
        row.update(extra)
        return row

    def fp_of(self, title, anchor_path=None):
        return fingerprint.compute(
            self.CLASSIFICATION,
            self.ANCHOR if anchor_path is None else anchor_path, "", title)

    def closure(self, round_no, fp, term="withdrawn", digest=None, path=None,
                **extra):
        row = {"event": "closure", "round": round_no, "fp": fp,
               "closure": term, "note": "n", **self.source(path, digest)}
        row.update(extra)
        return row

    def alias(self, from_fp, to_fp, digest=None, path=None):
        return {"event": "lineage", "kind": "alias", "from_fp": from_fp,
                "to_fp": to_fp, "reason": "carried",
                **self.source(path, digest)}

    def disposition(self, round_no, fp, term="accepted", payload=None,
                    digest=None, path=None, **extra):
        row = {"event": "disposition", "round": round_no, "finding_id": "F1",
               "fp": fp, "disposition": term,
               "payload": {"change": "x", "verification": "y"}
               if payload is None else payload,
               **self.source(path, digest)}
        row.update(extra)
        return row

    def request_row(self, round_no, author=None, digest=None, path=None,
                    **extra):
        """One well-formed legacy `request` row (round 6 F1). `author`,
        when given, must be a word `SOURCE_CLAIMS` actually carries — the
        row's own `author` is a `LEGACY_CONTAINMENT` member for this kind,
        so an uncontained name refuses on containment before author-binding
        is ever reached."""
        row = {"event": "request", "round": round_no,
               "sha": self.TARGET, "bytes": len(self.split_bytes),
               **self.source(path, digest)}
        if author is not None:
            row["author"] = author
        row.update(extra)
        return row

    def verdict_row(self, round_no=1, sha=None, digest=None, path=None,
                    **extra):
        row = {"event": "verdict", "round": round_no,
               "sha": self.TARGET if sha is None else sha,
               "verdict": "changes requested",
               "bytes": len(self.verdict_bytes), "finding_ids": 1,
               **self.source(path or self.VERDICT_PATH, digest)}
        row.update(extra)
        return row

    # --------------------------------------------------------------- driver

    def _import(self, ledger, events, commit=_UNSET, no_fetch=False):
        text = "\n".join(json.dumps(e) for e in events)
        return self._import_text(ledger, text, commit=commit,
                                 no_fetch=no_fetch)

    def _import_text(self, ledger, text, commit=_UNSET, no_fetch=False):
        """The fixture's remote is a local bare repository, so the DEFAULT
        path — fetch, then `ls-remote` and ancestry from an observed tip —
        runs here with no network. That is the production path; `no_fetch`
        is passed only where a case is about the weaker witness."""
        """The RAW-TEXT door (round-10 F4): a test about the parser must feed
        bytes, never pre-built Python dicts that can state no member twice."""
        tmp = Path(tempfile.mkstemp(suffix=".jsonl")[1])
        tmp.write_text(text, encoding="utf-8")
        self.addCleanup(tmp.unlink)
        cfg = dataclasses.replace(CFG, ledger_dir=None, repo_root=self.repo)
        where = self.anchor if commit is _UNSET else commit
        with unittest.mock.patch("review.cli._ledger", return_value=ledger):
            return _cli(cli.cmd_import_legacy, cfg, events=str(tmp),
                        source_commit=where, no_fetch=no_fetch)

    def refuses(self, ledger, events, why="", **kw):
        """Import must refuse AND append nothing — the atomicity half is
        never separable from the refusal half."""
        before = [dict(e) for e in ledger.events()]
        code, payload = self._import(ledger, events, **kw)
        self.assertNotEqual(code, 0, (why, payload))
        self.assertEqual(ledger.events(), before,
                         f"{why}: a refused batch must leave the ledger "
                         f"byte-for-byte as it was")
        return payload

    def admits(self, ledger, events, **kw):
        code, payload = self._import(ledger, events, **kw)
        self.assertEqual(code, 0, payload)
        return payload


class TestTheSourcePathGrammarIsClosed(_ImportLegacyCase):
    """Round-11 F6 — FALSIFICATION (`lineage-20-residue`).

    The grammar's own comment said "non-dot segments, no leading slash, no
    `..`" while the pattern excluded exactly three characters, so `.`, `..`
    and every dotted spelling of a real path were legal `source_path`
    values. They are not synonyms: git RESOLVES them, so
    `sources/../sources/split.md` reads one blob and the ledger records
    another path as its provenance.

    Nine refused forms, derived from the grammar rather than from examples:
    `.`, `..`, `../x`, `a/../x`, `a/./x`, `/x`, `a//x`, and the NUL and
    newline framings. Paired with ordinary nested paths, a dot-LEADING name
    (`.gitignore` is a tracked path, not a dot segment) and the fixture's
    own source file.

    MUTATION, run below rather than described: with the segment guard taken
    out, `sources/../sources/split.md` reaches git and git answers with a
    DIFFERENT tree entry — the aliasing the finding measured. The second
    half of the repair catches it there: the entry read must be the entry
    asked for.
    """

    REFUSED = (".", "..", "../x", "a/../x", "a/./x", "/x", "a//x",
               "a\x00b", "a\nb")
    #: The grammar as it shipped: the mutation, kept as a literal so the
    #: test states what it is mutating rather than editing production.
    PERMISSIVE = re.compile(r"\A(?!/)(?:[^/\x00\n]+/)*[^/\x00\n]+\Z")

    def authority(self):
        cfg = dataclasses.replace(CFG, ledger_dir=None, repo_root=self.repo)
        authority, problems = transport.read_source_authority(cfg,
                                                              self.anchor)
        self.assertEqual(problems, [], "the fixture anchor must resolve")
        return authority

    def recording(self, authority):
        """The authority's git runner, wrapped so the test can assert that
        a refused path costs no git read at all."""
        calls = []
        inner = authority._run

        def run(*args):
            calls.append(args)
            return inner(*args)

        authority._run = run
        return calls

    def test_every_refused_form_refuses_before_any_git_read(self):
        authority = self.authority()
        calls = self.recording(authority)
        for path in self.REFUSED:
            with self.subTest(path=path):
                self.assertIsNone(transport._SOURCE_PATH_RE.match(path),
                                  "the grammar must not admit it")
                data, why = authority.blob(path)
                self.assertIsNone(data)
                self.assertIn("not a repository path", why)
        self.assertEqual(calls, [], "a path the grammar refuses is never "
                                    "handed to git")

    def test_the_ordinary_paths_are_the_control(self):
        authority = self.authority()
        for path in (self.SPLIT_PATH, "a/b/c.md", ".gitignore", "...odd",
                     "a.b/c..d"):
            with self.subTest(path=path):
                self.assertIsNotNone(transport._SOURCE_PATH_RE.match(path),
                                     "a dot INSIDE a name is not a dot "
                                     "segment")
        data, why = authority.blob(self.SPLIT_PATH)
        self.assertIsNone(why)
        self.assertEqual(data, self.split_bytes)

    def test_the_alias_the_mutation_reopens_is_caught_by_the_entry_check(self):
        # First: the aliasing is real, measured through this fixture's own
        # git — the dotted spelling resolves to another tree entry.
        listing = self._git("ls-tree", "--full-tree", "-z", self.anchor,
                            "--", "sources/../sources/split.md")
        self.assertIn(self.SPLIT_PATH, listing)
        # Then: mutate the grammar back and the path reaches git, where the
        # canonical-entry check refuses it by name.
        authority = self.authority()
        with unittest.mock.patch.object(transport, "_SOURCE_PATH_RE",
                                        self.PERMISSIVE):
            data, why = authority.blob("sources/../sources/split.md")
        self.assertIsNone(data)
        self.assertIn("not the requested", why)
        self.assertIn(self.SPLIT_PATH, why)

    def test_a_row_citing_a_dotted_path_is_refused_at_the_door(self):
        # End to end, through the verb: the batch does not import and the
        # ledger is untouched.
        ledger = Ledger.in_memory()
        payload = self.refuses(
            ledger, [self.finding(1, "t", path="sources/../sources/split.md",
                                  digest=self.SPLIT_DIGEST)],
            "dotted source path")
        self.assertIn("not a repository path", json.dumps(payload))


class TestImportLegacySourceAuthority(_ImportLegacyCase):
    """Round-10 F2 — FALSIFICATION. Round 9 answered "a row may not be
    substantiated by another row in the same batch" with a source MANIFEST
    the caller wrote: `{path, sha256}` entries the tool hashed. The party
    who writes the batch also writes the manifest, so a throwaway file,
    hashed by its own author, substantiated a finding titled `fabricated`
    and emptied the standing cohort. The manifest is gone.

    Two properties replace it and both are exercised here, because either
    one alone leaves the reproduction open:

      ANCHORED   the bytes are read by this tool from `<commit>:<path>`, at
                 a commit a REMOTE-TRACKING REF contains — so history that
                 was already shared, never a file written in the same
                 session as the batch — and the row's `source_digest` is
                 recomputed, never believed.
      CONTAINED  the row's own claim text must occur in that content.

    THE STATED LIMIT, so no reader over-reads the controls below. This is
    not non-repudiation: someone able to push to the remote can commit a
    file saying whatever a row needs and then cite it, the anchor's message
    and authorship are unverified, and containment is a substring test over
    the whole file. It does establish that the cited bytes are durable,
    shared, attributable history and that the row's words are traceable into
    them — which is exactly the pair the manifest could not supply.
    """

    def test_a_batch_with_no_anchor_is_refused(self):
        ledger = Ledger.in_memory()
        self.refuses(ledger, [self.finding(1, "t")], "no anchor", commit=None)

    def test_the_round_10_reproduction_verbatim(self):
        # Source content `| R1-F1a | one atomic claim |`, a row titled
        # `fabricated`, its digest real and correctly computed over real,
        # pushed bytes — the exact batch that imported cleanly under the
        # manifest and emptied `standing_findings`.
        ledger = Ledger.in_memory()
        real = self.fp_of("a real claim")
        self.admits(ledger, [self.finding(1, "a real claim")])
        self.assertEqual([f["fp"] for f in ledger.standing_findings(LINEAGE)], [real])
        fabricated = self.fp_of("fabricated")
        payload = self.refuses(ledger, [
            self.finding(1, "fabricated"),
            self.disposition(1, fabricated),
            self.disposition(1, real, payload={"change": "x",
                                               "verification": "y"}),
        ], "fabricated claim over real anchored bytes")
        self.assertIn("does not occur in", json.dumps(payload))
        self.assertEqual([f["fp"] for f in ledger.standing_findings(LINEAGE)], [real],
                         "the real finding must still stand: a forged "
                         "acceptance may not answer it")

    def test_a_correctly_anchored_digest_does_not_substantiate_any_claim(self):
        # The narrower statement of the same thing, over a REAL committed
        # file of this fixture's repository: correct path, correct digest,
        # unrelated words. Anchoring alone is not provenance.
        ledger = Ledger.in_memory()
        self.refuses(ledger, [self.finding(1, "a claim nothing says")],
                     "anchored but uncontained")

    def test_a_row_may_not_cite_a_path_the_anchor_does_not_track(self):
        ledger = Ledger.in_memory()
        payload = self.refuses(
            ledger, [self.finding(1, "t", path="sources/invented.md",
                                  digest=self.SPLIT_DIGEST)],
            "untracked path")
        self.assertIn("is not tracked", json.dumps(payload))

    def test_the_digest_a_row_claims_is_recomputed_not_believed(self):
        ledger = Ledger.in_memory()
        payload = self.refuses(ledger, [self.finding(1, "t", digest="c" * 64)],
                               "asserted digest")
        self.assertIn("hashes to", json.dumps(payload))

    def test_a_commit_that_was_never_pushed_cannot_anchor_an_import(self):
        # The whole gain over the manifest: bytes invented in the same
        # session as the batch. The file says exactly what the row needs and
        # the digest is correct — and no ref carries the commit.
        sha, digest = self.local_only_commit("| R1-F1a | invented here |\n")
        ledger = Ledger.in_memory()
        payload = self.refuses(
            ledger, [self.finding(1, "invented here", digest=digest)],
            "local-only anchor", commit=sha)
        self.assertIn("ancestry of no ref", json.dumps(payload))

    def test_a_hand_written_remote_tracking_ref_does_not_anchor_it(self):
        """The attack on the anchor itself, measured 2026-09-02: a
        remote-tracking ref is LOCAL, WRITABLE state. `git update-ref
        refs/remotes/origin/main <any local commit>` exits 0 with no network
        and no remote, and `for-each-ref --contains` then names it.

        Resting the anchor on that would be round 9's mistake one level
        down — evidence the same party can author — so the witness is
        `ls-remote`: what the REMOTE answers for. The forged ref below is
        live and it still refuses.

        MUTATION: point `refs_containing` at `_remote_refs_containing` in
        the fetching branch of `read_source_authority` and this passes,
        which is the state the first cut of this mechanism was in.
        """
        sha, digest = self.local_only_commit("| R1-F1a | invented here |\n")
        self._git("update-ref", "refs/remotes/origin/forged", sha)
        self.assertIn("refs/remotes/origin/forged",
                      self._git("for-each-ref", "--contains", sha,
                                "--format=%(refname)", "refs/remotes/"),
                      "the forged ref is not live, so this proves nothing")
        # The paired control FIRST, because it is what the flag buys and
        # because the fetch below destroys the evidence: --no-fetch reads
        # those same local refs, admits the batch, and RECORDS that it made
        # the weaker claim rather than passing it off as the strong one.
        weak = Ledger.in_memory()
        code, payload = self._import(
            weak, [self.finding(1, "invented here", digest=digest)],
            commit=sha, no_fetch=True)
        self.assertEqual(code, 0, payload)
        self.assertIn("LOCAL state", payload["source_witness"])
        # And the door's own default refuses the same batch.
        ledger = Ledger.in_memory()
        payload = self.refuses(
            ledger, [self.finding(1, "invented here", digest=digest)],
            "anchor carried only by a hand-written remote-tracking ref",
            commit=sha)
        self.assertIn("ancestry of no ref", json.dumps(payload))
        # A second fact worth recording, since the run above establishes it:
        # `fetch --prune` DELETED the forged ref, because the remote does not
        # carry it. The weaker witness is self-repairing the moment anyone
        # reaches the remote at all.
        self.assertEqual(
            self._git("for-each-ref", "--contains", sha,
                      "--format=%(refname)", "refs/remotes/"), "",
            "--prune must remove a remote-tracking ref the remote disowns")

    def test_the_witness_is_recorded_on_every_import(self):
        ledger = Ledger.in_memory()
        payload = self.admits(ledger, [self.finding(1, "a real claim")])
        self.assertIn("ls-remote", payload["source_witness"])
        self.assertIn("refs/heads/main", payload["source_refs"])

    def test_a_forged_verdict_row_cannot_invent_a_round(self):
        # F2's own reproduction: bare request/verdict rows at round 99 used
        # to import and make `rounds_to_clean` report 99. Judged now against
        # the RECORD — envelope rounds are one contiguous loop — rather than
        # against a manifest the same author wrote.
        ledger = Ledger.in_memory()
        payload = self.refuses(ledger, [self.verdict_row(round_no=99)],
                               "round 99")
        self.assertIn("skips", json.dumps(payload))
        self.assertEqual(ledger.metrics(LINEAGE)["rounds_to_clean"],
                         "open (no clean verdict in 0 rounds)")

    def test_a_verdict_row_may_not_invent_a_target_commit(self):
        ledger = Ledger.in_memory()
        payload = self.refuses(ledger, [self.verdict_row(sha="b" * 40)],
                               "unshared target")
        self.assertIn("remote-tracking ref", json.dumps(payload))

    def test_a_verdict_row_may_not_restate_the_byte_count(self):
        ledger = Ledger.in_memory()
        self.refuses(ledger, [self.verdict_row(bytes=99999)], "wrong bytes")

    def test_a_verdict_row_matching_the_authority_imports(self):
        ledger = Ledger.in_memory()
        self.admits(ledger, [self.verdict_row()])
        self.assertEqual(ledger.completed_rounds(LINEAGE), [1])

    def test_a_verdict_row_must_cite_bytes_stating_its_own_verdict(self):
        # The split table carries claims, not a verdict term. A `verdict`
        # row citing it states a term its source never says.
        ledger = Ledger.in_memory()
        payload = self.refuses(
            ledger, [self.verdict_row(path=self.SPLIT_PATH,
                                      digest=self.SPLIT_DIGEST)],
            "split table as a verdict")
        self.assertIn("does not occur in", json.dumps(payload))

    def test_a_ruling_fingerprint_is_recomputed_not_believed(self):
        ledger = Ledger.in_memory()
        row = self.finding(1, "t")
        row["fp"] = "fp2:" + "9" * 16
        self.refuses(ledger, [row], "free-label fingerprint")
        atomic = self.atomic(1, "a claim")
        atomic["fp"] = self.fp_of("a different claim")
        self.refuses(ledger, [atomic], "borrowed fingerprint")

    def test_two_findings_cannot_be_collapsed_by_an_arbitrary_alias(self):
        # F2's second reproduction, at the strength round 10 needs. The
        # alias is anchored to a real, pushed file with a correct digest —
        # everything the manifest ever asked — and it still refuses, because
        # the file it cites carries neither ruling's words.
        ledger = Ledger.in_memory()
        one, two = self.fp_of("claim one"), self.fp_of("claim two")
        payload = self.refuses(ledger, [
            self.finding(1, "claim one", fid="F1"),
            self.finding(1, "claim two", fid="F2"),
            self.alias(one, two, path=self.VERDICT_PATH,
                       digest=self.VERDICT_DIGEST),
        ], "alias sourced to bytes naming neither claim")
        self.assertIn("ruling text", json.dumps(payload))

    def test_an_alias_over_the_bytes_that_carry_both_claims_imports(self):
        # The paired control: the same merge, cited to the source that does
        # carry both claims, is admitted. The rule is containment, not a ban
        # on aliases.
        ledger = Ledger.in_memory()
        one, two = self.fp_of("claim one"), self.fp_of("claim two")
        self.admits(ledger, [
            self.finding(1, "claim one", fid="F1"),
            self.finding(1, "claim two", fid="F2"),
            self.alias(one, two),
        ])
        self.assertEqual([f["fp"] for f in ledger.standing_findings(LINEAGE)], [two])

    def test_a_sourced_batch_still_imports_and_reads_back(self):
        # The positive control: everything above refuses because of what it
        # fails to name, not because the door stopped working.
        ledger = Ledger.in_memory()
        fp = self.fp_of("a real claim")
        payload = self.admits(ledger, [
            self.verdict_row(),
            self.finding(1, "a real claim"),
            self.disposition(1, fp, term="deferred",
                             payload={"destination": "briefs/x.md",
                                      "trigger": "when it bites"}),
        ])
        self.assertEqual(payload["events_added"], 3)
        self.assertEqual(payload["source_artifacts"], 2)
        self.assertEqual(payload["source_commit"], self.anchor)
        self.assertTrue(payload["source_refs"])
        self.assertEqual([f["fp"] for f in ledger.standing_findings(LINEAGE)], [fp])


class TestImportLegacyDuplicateMembers(_ImportLegacyCase):
    """Round-10 F4 — FALSIFICATION: the event-line parse was `json.loads`,
    which keeps the LAST of repeated object members and drops every earlier
    one BEFORE the closed schema sees the object. So `{"round": 999,
    "round": 1}` was already 1, and the conflict the grammar exists to catch
    never reached it.

    Every case here feeds RAW JSON TEXT, because a Python dict cannot state
    a member twice and a test built from one would test nothing.
    """

    def raw(self, row: dict, member: str, first) -> str:
        """`row` as JSON text with `member` stated TWICE — the conflicting
        value first, the row's own value second, which is the order
        last-write-wins hides."""
        body = json.dumps(row)
        assert body.startswith("{")
        return "{" + json.dumps(member) + ": " + json.dumps(first) + ", " \
            + body[1:]

    def test_every_member_of_every_row_refuses_a_conflicting_repeat(self):
        rows = {
            "finding": (self.finding(1, "a real claim"),
                        {"round": 999, "title": "fabricated",
                         "fp": "fp2:" + "9" * 16, "severity": "Low",
                         "source_path": "sources/invented.md",
                         "source_digest": "c" * 64, "id": "F9",
                         "classification": "other", "event": "closure",
                         "anchor_path": "elsewhere.py"}),
            "verdict": (self.verdict_row(),
                        {"round": 99, "bytes": 99999, "sha": "b" * 40,
                         "verdict": "clean to advance", "finding_ids": 42,
                         "source_path": self.SPLIT_PATH,
                         "source_digest": self.SPLIT_DIGEST,
                         "event": "request"}),
        }
        for kind, (row, conflicts) in rows.items():
            for member, first in conflicts.items():
                with self.subTest(kind=kind, member=member):
                    ledger = Ledger.in_memory()
                    code, payload = self._import_text(
                        ledger, self.raw(row, member, first))
                    self.assertNotEqual(code, 0, (member, payload))
                    self.assertIn("duplicate JSON member",
                                  json.dumps(payload))
                    self.assertEqual(ledger.events(), [],
                                     "a duplicate member refuses the WHOLE "
                                     "batch, atomically")

    def test_a_duplicate_in_a_nested_payload_is_refused_too(self):
        row = self.disposition(1, self.fp_of("a real claim"))
        text = json.dumps(row).replace(
            '"payload": {"change": "x"',
            '"payload": {"change": "not the change", "change": "x"')
        self.assertIn('"change": "not the change"', text)
        ledger = Ledger.in_memory()
        code, payload = self._import_text(ledger, text)
        self.assertNotEqual(code, 0, payload)
        self.assertIn("duplicate JSON member", json.dumps(payload))
        self.assertEqual(ledger.events(), [])

    def test_one_bad_line_refuses_the_lines_beside_it(self):
        good = self.finding(1, "a real claim")
        text = "\n".join([json.dumps(good),
                          self.raw(self.finding(2, "claim two", fid="F2"),
                                   "round", 999)])
        ledger = Ledger.in_memory()
        code, payload = self._import_text(ledger, text)
        self.assertNotEqual(code, 0, payload)
        self.assertEqual(ledger.events(), [])

    def test_the_single_member_control_still_imports(self):
        # Same rows, each member stated once: the refusals above are about
        # the repeat, not about the parser having stopped working.
        ledger = Ledger.in_memory()
        text = "\n".join(json.dumps(e) for e in
                         [self.verdict_row(), self.finding(1, "a real claim")])
        code, payload = self._import_text(ledger, text)
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["events_added"], 2)


class TestImportLegacyClosedSchema(_ImportLegacyCase):
    """Round-9 F2 — FALSIFICATION: the door admitted any object with a
    truthy `event` key, so `payload` could be a string (an uncaught
    AttributeError), `round` could be a list, and an unknown member was
    ignored in silence. The grammar is `vocab.LEGACY_EVENT_SCHEMA` and the
    cases below are DERIVED from it, so a member added there without a type
    fails here rather than being admitted by an omission.
    """

    #: Member TYPE -> values outside it. Every entry carries both wrong
    #: containers, a wrong scalar, the two absences JSON can express, and
    #: the near-misses that shape alone would let through (an uppercase
    #: digest, a short commit, a term outside its vocabulary). Keyed by the
    #: type rather than by the member, and proved COMPLETE against the
    #: schema below — a new member type has no cases until someone writes
    #: them, and that fails by name rather than passing by omission.
    WRONG_FOR = {
        "round": ([], {}, "1", None, True, 0, -3, 1.5),
        "count": ([], {}, "1", None, True, -1, 1.5),
        "text": ([], {}, 0, None, True, ""),
        "fingerprint": ([], {}, 0, None, True, ""),
        "digest": ([], {}, 0, None, True, "", "z" * 64, "A" * 64, "ab"),
        "commit": ([], {}, 0, None, True, "", "b" * 39, "A" * 40),
        "payload": ([], "x", 0, None, True, 1.5),
        "closure_term": ([], {}, 0, None, True, "", "retracted"),
        "disposition_term": ([], {}, 0, None, True, "", "approved"),
        "lineage_kind": ([], {}, 0, None, True, "", "merged"),
        "import_kind": ([], {}, 0, None, True, "", "child"),
        "verdict_term": ([], {}, 0, None, True, "", "looks fine"),
    }

    def test_every_member_type_the_schema_uses_has_wrong_values(self):
        used = {kind for entry in vocab.LEGACY_EVENT_SCHEMA.values()
                for kind in entry["required"].values()} - {"event_name"}
        self.assertEqual(sorted(used - set(self.WRONG_FOR)), [],
                         "a required member type with no wrong-value cases: "
                         "the matrix below would pass it by omission")

    def valid_rows(self):
        """One well-formed row per admitted kind, so the mutation matrix
        covers the whole grammar rather than the kinds someone remembered."""
        fp = self.fp_of("a claim")
        return {
            "finding": self.finding(1, "a claim"),
            "import": self.atomic(1, "an atomic claim"),
            "disposition": self.disposition(1, fp),
            "closure": self.closure(2, fp, answers_round=1),
            "lineage": self.alias(fp, self.fp_of("another claim")),
            "request": {"event": "request", "round": 1, "sha": self.TARGET,
                        "bytes": len(self.verdict_bytes),
                        "source_digest": self.VERDICT_DIGEST},
            "verdict": self.verdict_row(),
        }

    def test_the_grammar_covers_every_admitted_kind(self):
        self.assertEqual(sorted(self.valid_rows()),
                         sorted(transport.LEGACY_IMPORT_KINDS),
                         "a kind the door admits has no fixture here, so "
                         "nothing below tests its members")

    def test_every_required_member_refuses_every_wrong_type(self):
        rows = self.valid_rows()
        for kind, row in rows.items():
            required = vocab.LEGACY_EVENT_SCHEMA[kind]["required"]
            for member, member_type in sorted(required.items()):
                if member == "event":
                    continue        # the kind itself: routed, not typed
                for wrong in self.WRONG_FOR[member_type]:
                    with self.subTest(kind=kind, member=member, wrong=wrong):
                        bad = {**row, member: wrong}
                        self.refuses(Ledger.in_memory(), [bad],
                                     f"{kind}.{member}={wrong!r}")

    def test_a_conditionally_required_member_is_typed_too(self):
        rows = self.valid_rows()
        for (kind, member, value), demanded in sorted(
                vocab.LEGACY_CONDITIONAL_REQUIRED.items()):
            row = {**rows[kind], member: value}
            if demanded == "outcome":
                row = {**row, "outcome": "ratified"}
            with self.subTest(kind=kind, when=value, demanded=demanded):
                self.admits(Ledger.in_memory(),
                            [self.finding(1, "a claim"), row])
                self.refuses(
                    Ledger.in_memory(),
                    [self.finding(1, "a claim"),
                     {k: v for k, v in row.items() if k != demanded}],
                    f"{kind}({value}) with no {demanded}")

    def test_every_required_member_refuses_its_own_absence(self):
        for kind, row in self.valid_rows().items():
            for member in sorted(vocab.LEGACY_EVENT_SCHEMA[kind]["required"]):
                if member == "event":
                    continue
                with self.subTest(kind=kind, member=member):
                    bad = {k: v for k, v in row.items() if k != member}
                    self.refuses(Ledger.in_memory(), [bad],
                                 f"{kind} without {member}")

    def test_an_unknown_member_is_refused_by_name(self):
        for kind, row in self.valid_rows().items():
            with self.subTest(kind=kind):
                payload = self.refuses(
                    Ledger.in_memory(), [{**row, "answers_round_typo": 1}],
                    f"{kind} with a surplus member")
                self.assertIn("answers_round_typo", json.dumps(payload))

    def test_a_string_payload_is_a_refusal_not_an_attribute_error(self):
        ledger = Ledger.in_memory()
        fp = self.fp_of("a claim")
        payload = self.refuses(
            ledger, [self.finding(1, "a claim"),
                     self.disposition(1, fp, payload="fabricated")],
            "string payload")
        self.assertIn("payload", json.dumps(payload))

    def test_a_nested_payload_value_is_typed_too(self):
        ledger = Ledger.in_memory()
        fp = self.fp_of("a claim")
        self.refuses(
            ledger, [self.finding(1, "a claim"),
                     self.disposition(1, fp, payload={
                         "change": "x", "verification": "y",
                         "falsification": {"status": ["pass"]}})],
            "nested non-string")
        # …and the one-level object of strings the product path writes is
        # admitted, so the rule closes a shape rather than forbidding depth.
        self.admits(Ledger.in_memory(), [
            self.finding(1, "a claim"),
            self.disposition(1, fp, payload={
                "change": "x", "verification": "y",
                "falsification": {"status": "pass",
                                  "mutation": "fails_without_fix"}}),
        ])

    def test_every_disposition_carries_its_mandatory_payload(self):
        for term, keys in sorted(vocab.DISPOSITION_PAYLOADS.items()):
            with self.subTest(disposition=term):
                fp = self.fp_of("a claim")
                self.refuses(
                    Ledger.in_memory(),
                    [self.finding(1, "a claim"),
                     self.disposition(1, fp, term=term, payload={})],
                    f"{term} with no payload")
                self.admits(
                    Ledger.in_memory(),
                    [self.finding(1, "a claim"),
                     self.disposition(1, fp, term=term,
                                      payload={k: "stated" for k in keys})])

    def test_a_kind_outside_the_schema_is_refused(self):
        ledger = Ledger.in_memory()
        self.refuses(ledger, [{"event": "tool_feedback", "round": 1,
                               "sha": self.TARGET, "text": "hi"}],
                     "a kind with no schema entry")

    def test_a_fabricated_human_waiver_is_never_admitted(self):
        ledger = Ledger.in_memory()
        self.refuses(ledger, [self.finding(1, "a claim"),
                              {"event": "finding_waiver", "round": 1,
                               "fp": self.fp_of("a claim"),
                               "authorized_by": "attacker", "reason": "r",
                               "source_digest": self.SPLIT_DIGEST}],
                     "a minted human waiver")


class TestImportLegacyAnswersRoundIntegrity(_ImportLegacyCase):
    """Rounds 7 F2 and 8 F2, re-proved under the rebuilt door: a settling
    answer must substantiate the ruling it claims, and every way of failing
    to refuses the WHOLE batch before a row is appended.
    """

    def test_a_valid_batch_binds_and_reads_back(self):
        ledger = Ledger.in_memory()
        events = [
            self.finding(1, "claim a", fid="F1"),
            self.finding(3, "claim a", fid="F1"),
            self.finding(2, "claim b", fid="F2"),
            self.closure(4, self.fp_of("claim a"), answers_round=3),
        ]
        payload = self.admits(ledger, events)
        self.assertEqual(payload["events_added"], 4)

    def test_each_invalid_shape_refuses_the_whole_batch(self):
        a, b = self.fp_of("claim a"), self.fp_of("claim b")
        cases = {
            "future": [self.finding(1, "claim a"),
                       self.closure(1, a, answers_round=2)],
            "nonexistent": [self.finding(1, "claim a"),
                            self.closure(5, a, answers_round=3)],
            "same_round": [self.finding(1, "claim a"),
                           self.finding(2, "claim a"),
                           self.closure(2, a, answers_round=2)],
            "wrong_identity": [self.finding(1, "claim a"),
                               self.finding(3, "claim a"),
                               self.finding(2, "claim b"),
                               self.closure(4, a, answers_round=2)],
            "other_identitys_round": [self.finding(1, "claim a"),
                                      self.finding(2, "claim b"),
                                      self.closure(3, b, answers_round=1)],
            "non_integer": [self.finding(1, "claim a"),
                            self.closure(2, a, answers_round="1")],
            "zero": [self.finding(1, "claim a"),
                     self.closure(2, a, answers_round=0)],
            "unstamped": [self.finding(1, "claim a"),
                          self.closure(2, a)],
            "unstamped_in_collision": [self.finding(1, "claim a"),
                                       self.finding(2, "claim a"),
                                       self.closure(2, a)],
        }
        for name, events in cases.items():
            with self.subTest(case=name):
                self.refuses(Ledger.in_memory(), events, name)

    def test_an_accepted_disposition_still_needs_a_real_ruling(self):
        ledger = Ledger.in_memory()
        fp = self.fp_of("claim a")
        self.refuses(ledger, [self.disposition(1, fp)], "no backing ruling")
        self.refuses(ledger, [self.finding(1, "claim a"),
                              self.disposition(2, fp)], "wrong round")
        self.admits(ledger, [self.finding(1, "claim a"),
                             self.disposition(1, fp)])
        self.assertEqual(ledger.standing_findings(LINEAGE), [])

    def test_an_open_effect_answer_needs_no_binding(self):
        # `sustained` settles nothing (`vocab.FINDING_ANSWERS`), so it has
        # no authorization weight to forge and is not bound to a ruling —
        # the domain of the lifecycle pass is derived, not hand-picked.
        ledger = Ledger.in_memory()
        self.admits(ledger, [self.finding(1, "claim a"),
                             self.finding(2, "claim a"),
                             self.closure(2, self.fp_of("claim a"),
                                          term="sustained")])


class TestImportLegacyBatchLocalIdentity(_ImportLegacyCase):
    """Round-8 F1 — FALSIFICATION: identity must resolve through ONE
    authority spanning the ledger's persisted lineage AND this batch's own
    alias rows, order-independently, failing closed on a conflicting or
    cyclic graph exactly as `resolve_identity` does.
    """

    def test_a_stamped_closure_is_bound_under_the_batch_local_graph(self):
        # `answers_round: 1` names a round the ORIGINAL identity was ruled
        # in; the alias merges it into one whose only ruling is round 2, so
        # under the resolved identity the stamp names a real ruling and the
        # batch stands. Its mirror below is the same batch with a stamp
        # that names nothing.
        old, new = self.fp_of("claim old"), self.fp_of("claim new")
        events = [self.finding(1, "claim old"), self.alias(old, new),
                  self.finding(2, "claim new"), self.closure(3, old,
                                                             answers_round=1)]
        ledger = Ledger.in_memory()
        self.admits(ledger, events)
        standing = ledger.standing_findings(LINEAGE)
        self.assertEqual([f["round"] for f in standing], [2],
                         "the round-2 ruling is unanswered and must stand")

    def test_a_stamp_naming_no_ruling_of_the_merged_identity_refuses(self):
        old, new = self.fp_of("claim old"), self.fp_of("claim new")
        events = [self.finding(1, "claim old"), self.alias(old, new),
                  self.finding(2, "claim new"),
                  self.closure(3, old, answers_round=2)]
        # Round 2 IS a ruling of the merged identity, so this one stands…
        self.admits(Ledger.in_memory(), events)
        # …and a round nothing was ruled in does not.
        events[-1] = self.closure(4, old, answers_round=3)
        self.refuses(Ledger.in_memory(), events, "no round-3 ruling")

    def test_an_unstamped_collision_behind_a_batch_local_alias_refuses(self):
        old, new = self.fp_of("claim old"), self.fp_of("claim new")
        self.refuses(Ledger.in_memory(),
                     [self.finding(1, "claim old"), self.alias(old, new),
                      self.finding(2, "claim new"), self.closure(2, old)],
                     "unstamped behind an alias")

    def test_a_multi_hop_alias_collision_also_refuses(self):
        a, b, c = (self.fp_of("claim a"), self.fp_of("claim b"),
                   self.fp_of("claim c"))
        self.refuses(Ledger.in_memory(),
                     [self.finding(1, "claim a"), self.alias(a, b),
                      self.alias(b, c), self.finding(2, "claim c"),
                      self.closure(2, a)],
                     "unstamped behind two hops")

    def test_row_order_does_not_change_the_verdict_either_way(self):
        # The check may not depend on meeting the alias or the re-ruling
        # before the closure. Same graph, listed backwards, both ways round:
        # the unbound stamp still refuses and the bound one still stands.
        old, new = self.fp_of("claim old"), self.fp_of("claim new")
        graph = [self.finding(2, "claim new"), self.alias(old, new),
                 self.finding(1, "claim old")]
        self.refuses(Ledger.in_memory(),
                     [self.closure(4, old, answers_round=3)] + graph,
                     "shuffled rows, no round-3 ruling")
        self.admits(Ledger.in_memory(),
                    [self.closure(4, old, answers_round=1)] + graph)

    def test_a_conflicting_batch_local_alias_refuses(self):
        x, y, z = (self.fp_of("claim x"), self.fp_of("claim y"),
                   self.fp_of("claim z"))
        self.refuses(Ledger.in_memory(),
                     [self.finding(1, "claim x"), self.alias(x, y),
                      self.alias(x, z)],
                     "one identity, two canonical parents")

    def test_a_cyclic_batch_local_alias_refuses(self):
        p, q = self.fp_of("claim p"), self.fp_of("claim q")
        self.refuses(Ledger.in_memory(),
                     [self.finding(1, "claim p"), self.alias(p, q),
                      self.alias(q, p)],
                     "a cycle has no canonical member")


class TestImportLegacyCannotReinterpretPersistedAnswers(_ImportLegacyCase):
    """Round-9 F1 — FALSIFICATION, both halves of the repair.

    THE ELIMINATION. An unstamped settling closure means whatever the
    identity graph and the ruling set say AT READ TIME, and a later,
    separate import can change both. Validating harder cannot fix that: the
    validation is a statement about one moment and the meaning is
    re-derived later. So this door admits no unstamped `withdrawn` closure
    at all, and the round-9 evidence's own first call — which used to exit
    0 — now refuses.

    THE RECOMPUTATION. Unstamped closures still reach the ledger by the
    PRODUCT path, which writes one whenever no disposition record named the
    ruling (`verdict_events`). Those are the persisted answers a later
    import can still reinterpret, so every settling answer already on disk
    is bound twice — under the ledger as it stands, and under the ledger
    this batch would leave — and a batch that MOVES one is refused whole.
    """

    def persist_unstamped_withdrawal(self, ledger, title="claim old"):
        """What `close` writes when the verdict named no disposition
        record for the closure: a withdrawal with no `answers_round`."""
        self.admits(ledger, [self.finding(1, title)])
        ledger.add({"event": "closure", "round": 2, "fp": self.fp_of(title),
                    "closure": "withdrawn", "note": "the reviewer withdrew"})
        return self.fp_of(title)

    def test_the_evidences_own_first_call_no_longer_exits_zero(self):
        ledger = Ledger.in_memory()
        old = self.fp_of("claim old")
        self.refuses(ledger,
                     [self.finding(1, "claim old"), self.closure(2, old)],
                     "an unstamped settling closure at import")

    def test_a_later_batch_may_not_move_a_persisted_unstamped_closure(self):
        ledger = Ledger.in_memory()
        old = self.persist_unstamped_withdrawal(ledger)
        new = self.fp_of("claim new")
        self.assertEqual(ledger.standing_findings(LINEAGE), [],
                         "the withdrawal settles round 1 today")
        self.refuses(ledger,
                     [self.alias(old, new), self.finding(2, "claim new")],
                     "a second call retargeting the first call's closure")

    def test_a_multi_hop_retarget_across_three_calls_refuses(self):
        ledger = Ledger.in_memory()
        old = self.persist_unstamped_withdrawal(ledger)
        mid, new = self.fp_of("claim mid"), self.fp_of("claim new")
        self.admits(ledger, [self.alias(old, mid)])
        self.refuses(ledger,
                     [self.alias(mid, new), self.finding(2, "claim new")],
                     "the third call closes the loop")

    def test_the_re_ruling_may_arrive_before_the_alias(self):
        # Vary which call supplies which row: the ruling first, the alias
        # second. Neither call is the one that "obviously" does the damage.
        ledger = Ledger.in_memory()
        old = self.persist_unstamped_withdrawal(ledger)
        new = self.fp_of("claim new")
        self.admits(ledger, [self.finding(2, "claim new")])
        self.refuses(ledger, [self.alias(old, new)], "the alias arrives last")

    def test_a_same_identity_re_ruling_alone_refuses(self):
        # No alias at all: a later call that rules the SAME identity in the
        # closure's own round makes the persisted closure ambiguous.
        ledger = Ledger.in_memory()
        self.persist_unstamped_withdrawal(ledger)
        self.refuses(ledger, [self.finding(2, "claim old")],
                     "a re-ruling in the closure's own round")

    def test_the_explicitly_bound_closure_is_the_valid_control(self):
        # Same three rows, one difference: the persisted closure names the
        # ruling it answers. Nothing a later graph does can reinterpret an
        # explicit stamp, so the later call stands AND the later ruling
        # stands with it.
        ledger = Ledger.in_memory()
        old, new = self.fp_of("claim old"), self.fp_of("claim new")
        self.admits(ledger, [self.finding(1, "claim old"),
                             self.closure(2, old, answers_round=1)])
        self.assertEqual(ledger.standing_findings(LINEAGE), [])
        self.admits(ledger, [self.alias(old, new),
                             self.finding(2, "claim new")])
        standing = ledger.standing_findings(LINEAGE)
        self.assertEqual([f["round"] for f in standing], [2],
                         "the round-2 ruling nobody answered must stand")

    def test_an_unrelated_later_import_is_not_refused(self):
        # The other half of not deadlocking the door: a persisted unstamped
        # closure does not freeze the ledger — only a batch that MOVES it
        # is refused.
        ledger = Ledger.in_memory()
        self.persist_unstamped_withdrawal(ledger)
        self.admits(ledger, [self.finding(5, "an unrelated claim")])
        self.assertEqual([f["round"] for f in ledger.standing_findings(LINEAGE)], [5])


class TestImportLegacyAnswersCannotAcquireASameRoundRuling(_ImportLegacyCase):
    """Round-10 F1 — FALSIFICATION. Round 9 reduced every answer's binding to
    `(state, round)` and compared only those two scalars before and after a
    candidate batch. So the comparison could see the SLOT move and not its
    OCCUPANT change.

    The reproduction, in two calls: call 1 imports a round-1 finding and its
    accepted disposition, leaving nothing standing. Call 2 imports an alias
    from that fingerprint to a DISTINCT identity plus that identity's own
    finding AT THE SAME ROUND. The round did not change, so the round-9
    comparison said nothing had happened — and the persisted acceptance,
    untouched on disk, silently began covering a ruling it never answered.
    Both fingerprints resolved to the merged identity and the standing
    cohort stayed empty.

    An answer is now bound to the ruling MATERIAL at its target round — the
    content uid of every ruling event there — so an added or different
    ruling at that same round is a different binding and refuses.
    """

    def settled_ledger(self):
        """A ledger where `claim old` was ruled in round 1 and accepted."""
        ledger = Ledger.in_memory()
        old = self.fp_of("claim old")
        self.admits(ledger, [self.finding(1, "claim old"),
                             self.disposition(1, old)])
        self.assertEqual(ledger.standing_findings(LINEAGE), [],
                         "call 1 must leave nothing standing")
        return ledger, old

    def test_an_alias_may_not_move_a_settled_answer_onto_a_new_ruling(self):
        ledger, old = self.settled_ledger()
        new = self.fp_of("claim new")
        payload = self.refuses(
            ledger,
            [self.alias(old, new), self.finding(1, "claim new", fid="F2")],
            "a distinct same-round ruling behind an alias")
        self.assertIn("WHICH ruling occupies it did", json.dumps(payload))
        self.assertEqual([f["fp"] for f in ledger.standing_findings(LINEAGE)], [],
                         "the refused batch changed nothing at all")

    def test_the_alias_only_control_is_unaffected(self):
        # The paired control the finding demands: the SAME alias with no new
        # ruling at that round is a rename, and a rename may not be refused.
        ledger, old = self.settled_ledger()
        new = self.fp_of("claim new")
        self.admits(ledger, [self.alias(old, new)])
        self.assertEqual(ledger.standing_findings(LINEAGE), [],
                         "an alias-only rename preserves the settlement")

    def test_the_new_ruling_is_unsettled_after_the_refusal(self):
        # What the escape bought: `claim new` looked answered. After the
        # refusal it is not even in the ledger, and importing it WITHOUT the
        # alias leaves it standing and unanswered, which is the true state.
        ledger, _old = self.settled_ledger()
        self.admits(ledger, [self.finding(1, "claim new", fid="F2")])
        self.assertEqual([f["id"] for f in ledger.standing_findings(LINEAGE)],
                         ["F2"])

    def test_a_second_ruling_of_the_SAME_identity_at_that_round_refuses(self):
        # No alias at all, and no distinct identity: one more ruling event
        # of the answered identity, in the answered round. Multiplicity is
        # part of the material, so an acceptance that covered one ruling may
        # not silently come to cover two.
        ledger, _old = self.settled_ledger()
        self.refuses(ledger,
                     [self.finding(1, "claim old", fid="F1b",
                                   severity="Medium")],
                     "a second round-1 ruling of the settled identity")

    def test_an_atomic_import_is_ruling_material_too(self):
        # The ruling authority is `is_ruling`, so a legacy atomic import
        # arriving at the answered round moves the binding exactly as a
        # `finding` does — the two kinds cannot disagree here either.
        ledger, old = self.settled_ledger()
        new = self.fp_of("an atomic claim")
        self.refuses(ledger,
                     [self.alias(old, new), self.atomic(1, "an atomic claim")],
                     "an atomic ruling behind the alias")

    def test_a_ruling_at_another_round_is_not_this_answer_s_business(self):
        # The bound is tight, not blunt: a new ruling of the merged identity
        # in a DIFFERENT round does not touch the round-1 acceptance, and
        # the batch stands with the new ruling left standing.
        ledger, old = self.settled_ledger()
        new = self.fp_of("claim new")
        self.admits(ledger, [self.alias(old, new),
                             self.finding(2, "claim new", fid="F2")])
        self.assertEqual([f["round"] for f in ledger.standing_findings(LINEAGE)], [2])


class TestImportLegacyDispositionAuthor(_ImportLegacyCase):
    """Round 6 F1 — FALSIFICATION. `LEGACY_EVENT_SCHEMA` neither required
    nor permitted an `author` member on a `disposition` row, and none of
    `import-legacy`'s passes called `request_author` or
    `check_disposition_author` — the two functions rounds 3-5 built so
    `respond --out`, `respond` without `--out`, and standalone `ledger add`
    could never persist a disposition attributed to anyone but the round's
    own recorded request. `import-legacy` was a fourth ingress those three
    doors' fix never reached: a ledger holding a round-1 request stamped
    `author=claude` still admitted a source-valid `refuted` disposition for
    round 1 with no author at all, through the real command path.

    The request-author invariant now applies over the COMPLETE candidate
    state a batch would leave: a request PERSISTED before this call, a
    request in the SAME batch as the disposition answering it, and a
    request an EARLIER sequential `import-legacy` call already committed
    all bind identically. A request that genuinely carries no author is
    the paired legacy control, proved for the same three shapes. A
    disposition may also SUPPLY an author now (`vocab.LEGACY_EVENT_SCHEMA`
    admits it as optional): a matching value passes, a mismatch refuses
    the whole batch with zero rows appended — proved for every one of the
    five disposition terms, since the invariant does not read the term.
    """

    def _payload_for(self, term):
        return {k: "stated" for k in vocab.DISPOSITION_PAYLOADS[term]}

    def _finding_and_disposition(self, term, author=None, claim="a claim",
                                 round_no=1):
        fp = self.fp_of(claim)
        finding = self.finding(round_no, claim)
        disp = self.disposition(round_no, fp, term=term,
                                payload=self._payload_for(term))
        if author is not None:
            disp["author"] = author
        return finding, disp

    def _last_disposition(self, ledger):
        rows = [e for e in ledger.events() if e.get("event") == "disposition"]
        self.assertEqual(len(rows), 1, "exactly one disposition must land")
        return rows[0]

    # --------------------------------------------------------- case (1)

    def test_a_persisted_request_author_is_derived_onto_the_disposition(self):
        for term in vocab.DISPOSITIONS:
            with self.subTest(term=term):
                ledger = Ledger.in_memory()
                ledger.add({"event": "request", "round": 1, "sha": SHA_B,
                           "bytes": 1, "author": "claude"})
                finding, disp = self._finding_and_disposition(term)
                self.assertNotIn("author", disp)
                payload = self.admits(ledger, [finding, disp])
                self.assertEqual(payload["events_added"], 2)
                self.assertEqual(self._last_disposition(ledger)["author"],
                                 "claude")

    # --------------------------------------------------------- case (2)

    def test_a_batch_local_request_author_is_derived_onto_its_disposition(
            self):
        for term in vocab.DISPOSITIONS:
            with self.subTest(term=term):
                ledger = Ledger.in_memory()
                request = self.request_row(1, author="claude")
                finding, disp = self._finding_and_disposition(term)
                payload = self.admits(ledger, [request, finding, disp])
                self.assertEqual(payload["events_added"], 3)
                self.assertEqual(self._last_disposition(ledger)["author"],
                                 "claude")

    # --------------------------------------------------------- case (3)

    def test_a_sequentially_imported_request_author_is_derived(self):
        for term in vocab.DISPOSITIONS:
            with self.subTest(term=term):
                ledger = Ledger.in_memory()
                request = self.request_row(1, author="claude")
                first = self.admits(ledger, [request])
                self.assertEqual(first["events_added"], 1)
                finding, disp = self._finding_and_disposition(term)
                second = self.admits(ledger, [finding, disp])
                self.assertEqual(second["events_added"], 2)
                self.assertEqual(self._last_disposition(ledger)["author"],
                                 "claude")

    # ----------------------------------------------- paired legacy control

    def test_a_request_with_no_author_leaves_the_legacy_state(self):
        for term in vocab.DISPOSITIONS:
            with self.subTest(term=term, shape="persisted"):
                ledger = Ledger.in_memory()
                ledger.add({"event": "request", "round": 1, "sha": SHA_B,
                           "bytes": 1})
                finding, disp = self._finding_and_disposition(term)
                self.admits(ledger, [finding, disp])
                self.assertNotIn("author", self._last_disposition(ledger))
            with self.subTest(term=term, shape="batch-local"):
                ledger = Ledger.in_memory()
                request = self.request_row(1)
                finding, disp = self._finding_and_disposition(term)
                self.admits(ledger, [request, finding, disp])
                self.assertNotIn("author", self._last_disposition(ledger))
            with self.subTest(term=term, shape="sequential"):
                ledger = Ledger.in_memory()
                request = self.request_row(1)
                self.admits(ledger, [request])
                finding, disp = self._finding_and_disposition(term)
                self.admits(ledger, [finding, disp])
                self.assertNotIn("author", self._last_disposition(ledger))

    # ------------------------------------------------- supplied author

    def test_a_matching_supplied_author_passes(self):
        for term in vocab.DISPOSITIONS:
            with self.subTest(term=term):
                ledger = Ledger.in_memory()
                ledger.add({"event": "request", "round": 1, "sha": SHA_B,
                           "bytes": 1, "author": "claude"})
                finding, disp = self._finding_and_disposition(
                    term, author="claude")
                payload = self.admits(ledger, [finding, disp])
                self.assertEqual(payload["events_added"], 2)
                self.assertEqual(self._last_disposition(ledger)["author"],
                                 "claude")

    def test_a_mismatched_supplied_author_refuses_the_whole_batch(self):
        for term in vocab.DISPOSITIONS:
            with self.subTest(term=term):
                ledger = Ledger.in_memory()
                ledger.add({"event": "request", "round": 1, "sha": SHA_B,
                           "bytes": 1, "author": "claude"})
                finding, disp = self._finding_and_disposition(
                    term, author="codex")
                payload = self.refuses(
                    ledger, [finding, disp],
                    f"{term}: codex disposition against a claude request")
                self.assertIn("does not match", json.dumps(payload))
                self.assertEqual(
                    [e for e in ledger.events()
                     if e.get("event") == "disposition"], [],
                    "a refused batch must append no disposition at all")


if __name__ == "__main__":
    unittest.main()


class TestImportLegacyValidatesItsDestinationLineage(_ImportLegacyCase):
    """Round-2 F4 — FALSIFICATION. An import is validated against the
    lineage its rows are APPENDED to, not against the whole file.

    The door already selected a destination (`import_lineage`) and appended
    with it, but two of its passes still scanned `ledger.events()`. So
    another review's history substantiated this batch: a withdrawal naming a
    round-1 ruling that existed only in a concurrent lineage validated and
    landed in a lineage where no such ruling is, and a round-3 verdict
    imported over a round 2 that had never happened HERE. Both are ordinary
    mistaken imports on a machine running two reviews at once.

    The partition is one comparison, run for every settling answer kind and
    for the envelope sequence: the SAME batch against no backing evidence,
    against evidence in the destination, and against evidence held only by
    another lineage — open, closed, and the unkeyed legacy positional one a
    mixed ledger still carries. Only the destination-backed control may
    validate; every refusal appends zero events.

    Aliases are the deliberate exception and stay global, so they are
    checked here from both sides: a global merge may not import another
    lineage's ROUND HISTORY, and may not silently rebind an answer persisted
    in a lineage this batch appends nothing to.
    """

    DEST = "Ldest000001"
    OTHER = "Lother00001"

    #: Where the evidence a batch needs is held.
    ELSEWHERE = ("open", "closed", "legacy")

    def _ledger_where(self, where, evidence, dest_rounds=(1,)):
        """A ledger holding `evidence` in the place `where` names, whose
        destination for `import-legacy` is always `DEST`.

        `DEST` is bound by BRANCH: its request rows name this fixture
        repository's branch, so `choose_open_lineage` returns it however
        many other open lineages the ledger holds. "legacy" seeds the
        unkeyed positional prefix, which is what an unmigrated ledger's
        events read as (`Ledger.lineage_keys`).
        """
        ledger = Ledger.in_memory()
        if where == "legacy":
            for row in evidence:
                ledger.add(dict(row))
        for r in dest_rounds:
            ledger.add({"event": "request", "round": r, "sha": self.TARGET,
                        "bytes": 1, "branch": "main"}, lineage=self.DEST)
        if where == "dest":
            for row in evidence:
                ledger.add(dict(row), lineage=self.DEST)
        elif where in ("open", "closed"):
            for row in evidence:
                ledger.add(dict(row), lineage=self.OTHER)
            if where == "closed":
                ledger.add({"event": Ledger.LINEAGE_CLOSED, "at_round": 1,
                            "outcome": "decision", "reason": "done there",
                            "authorized_by": "user"}, lineage=self.OTHER)
        return ledger

    def refuses_and_appends_nothing(self, ledger, events, why):
        """`refuses` plus the length assertion stated in its own right: a
        refused batch leaves the ledger exactly as long as it was."""
        before = len(ledger.events())
        payload = self.refuses(ledger, events, why)
        self.assertEqual(len(ledger.events()), before,
                         f"{why}: a refused batch appends zero events")
        return payload

    def _compare(self, evidence, batch, why, dest_rounds=(1,)):
        """The three-case comparison itself: no evidence, destination
        evidence, evidence only elsewhere."""
        self.refuses_and_appends_nothing(
            self._ledger_where("none", evidence, dest_rounds), batch,
            f"{why}: no backing evidence anywhere")
        payload = self.admits(
            self._ledger_where("dest", evidence, dest_rounds), batch)
        self.assertEqual(payload["lineage"], self.DEST)
        self.assertEqual(payload["events_added"], len(batch),
                         f"{why}: the destination-backed control must import")
        for where in self.ELSEWHERE:
            with self.subTest(evidence_in=where):
                self.refuses_and_appends_nothing(
                    self._ledger_where(where, evidence, dest_rounds), batch,
                    f"{why}: evidence only in a {where} lineage")

    # ------------------------------------------------------------- the guard

    def test_the_fixture_imports_into_the_branch_bound_destination(self):
        # Without this the whole class could be comparing one lineage with
        # itself: every case below depends on DEST being the destination
        # even while another open lineage exists beside it.
        ledger = self._ledger_where("open", [self.finding(1, "claim b")])
        payload = self.admits(ledger, [self.finding(1, "claim a")])
        self.assertEqual(payload["lineage"], self.DEST)
        self.assertEqual(ledger.lineages(), [self.DEST, self.OTHER])
        self.assertEqual([f["fp"] for f in
                          ledger.standing_findings(self.DEST)],
                         [self.fp_of("claim a")])

    # ------------------------------------------- the settling answer kinds

    def test_a_withdrawal_binds_only_to_its_own_lineages_ruling(self):
        # ("closure", "withdrawn") — F4's own reproduction: the withdrawal
        # targets round 1, and only the destination's own round-1 ruling
        # may substantiate it.
        fp = self.fp_of("claim a")
        self._compare([self.finding(1, "claim a")],
                      [self.closure(2, fp, answers_round=1)],
                      "a withdrawal")

    def test_an_accepted_disposition_binds_only_to_its_own_lineages_ruling(
            self):
        # ("disposition", "accepted") — the other settling kind this door
        # can actually append. A disposition answers its OWN round, so the
        # ruling it needs is a round-1 ruling of the destination.
        fp = self.fp_of("claim a")
        self._compare([self.finding(1, "claim a")],
                      [self.disposition(1, fp, term="accepted")],
                      "an accepted disposition")

    def test_a_waiver_row_is_refused_whatever_lineage_holds_the_ruling(self):
        # (FINDING_WAIVER_EVENT, None) — the third settling kind. Its
        # candidate half is closed one pass earlier: the schema admits no
        # minted human waiver at all, so no lineage's ruling can
        # substantiate one and the comparison has no admitting control by
        # construction. Stated as a test so the kind's coverage is not
        # silently missing.
        waiver = {"event": vocab.FINDING_WAIVER_EVENT, "round": 2,
                  "fp": self.fp_of("claim a"), "answers_round": 1,
                  "authorized_by": "user", "reason": "r",
                  **self.source()}
        for where in ("none", "dest", *self.ELSEWHERE):
            with self.subTest(evidence_in=where):
                ledger = self._ledger_where(where,
                                            [self.finding(1, "claim a")])
                self.refuses_and_appends_nothing(
                    ledger, [waiver], f"an imported waiver ({where})")

    def test_an_envelope_round_is_contiguous_only_within_its_own_lineage(self):
        # The request/verdict sequence check. Round 2 exists as a real
        # envelope row; a round-3 verdict may only be stated by the lineage
        # that actually ran round 2.
        self._compare([{"event": "request", "round": 2, "sha": self.TARGET,
                        "bytes": 1}],
                      [self.verdict_row(round_no=3)],
                      "a round-3 verdict")

    def test_the_skipped_round_refusal_names_the_destination(self):
        ledger = self._ledger_where("open",
                                    [{"event": "request", "round": 2,
                                      "sha": self.TARGET, "bytes": 1}])
        payload = self.refuses_and_appends_nothing(
            ledger, [self.verdict_row(round_no=3)],
            "round 2 belongs to the other lineage")
        text = json.dumps(payload)
        self.assertIn("skips", text)
        self.assertIn(self.DEST, text)
        self.assertEqual(ledger.completed_rounds(self.DEST), [])

    # ------------------------------------------------ the global alias half

    def test_a_global_alias_does_not_merge_two_lineages_round_histories(self):
        # The alias is legitimate and global; what it may NOT do is lend the
        # destination the round history of the lineage it reaches into.
        # `claim b` is ruled at round 2 only in the other lineage, so a
        # withdrawal stamped to round 2 binds to nothing here.
        a, b = self.fp_of("claim a"), self.fp_of("claim b")
        for where in self.ELSEWHERE:
            with self.subTest(other_ruling_in=where):
                ledger = self._ledger_where(
                    where, [self.finding(2, "claim b")])
                ledger.add(self.finding(1, "claim a"), lineage=self.DEST)
                self.refuses_and_appends_nothing(
                    ledger, [self.alias(a, b), self.closure(3, b,
                                                            answers_round=2)],
                    f"a round-2 ruling held only by a {where} lineage")

    def test_the_same_alias_binds_through_the_destinations_own_ruling(self):
        # The paired control, and the proof the alias itself still works:
        # the identical merge, with the withdrawal stamped to the round the
        # DESTINATION actually ruled, imports and settles the thread.
        a, b = self.fp_of("claim a"), self.fp_of("claim b")
        ledger = self._ledger_where("open", [self.finding(2, "claim b")])
        ledger.add(self.finding(1, "claim a"), lineage=self.DEST)
        self.admits(ledger, [self.alias(a, b),
                             self.closure(3, b, answers_round=1)])
        self.assertEqual(ledger.standing_findings(self.DEST), [],
                         "the destination's own ruling is answered")
        self.assertEqual([f["round"] for f in
                          ledger.standing_findings(self.OTHER)], [2],
                         "the other lineage's round-2 ruling still stands")

    def test_a_persisted_answer_here_may_not_be_rebound_by_this_batch(self):
        # The round-9/10 protection, re-proved inside one lineage: two
        # round-1 rulings of the destination, an answer stamped to one of
        # them, and an alias that would make it cover both.
        a, c = self.fp_of("claim a"), self.fp_of("claim c")
        ledger = self._ledger_where("none", [])
        ledger.add(self.finding(1, "claim a"), lineage=self.DEST)
        ledger.add(self.finding(1, "claim c"), lineage=self.DEST)
        ledger.add({"event": vocab.FINDING_WAIVER_EVENT, "round": 2,
                    "fp": a, "answers_round": 1, "authorized_by": "user",
                    "reason": "overruled"}, lineage=self.DEST)
        self.refuses_and_appends_nothing(
            ledger, [self.alias(a, c)],
            "an alias moving a waiver persisted in the destination")

    def test_a_persisted_answer_in_another_lineage_is_protected_too(self):
        # Aliases are GLOBAL, so a batch appended entirely to DEST can still
        # move an answer that lives in another lineage. The persisted half
        # therefore runs over every lineage's own ruling set — scoping it to
        # the destination alone would be the opposite error to F4's.
        b, c = self.fp_of("claim b"), self.fp_of("claim c")
        for where in self.ELSEWHERE:
            with self.subTest(answer_in=where):
                ledger = self._ledger_where(
                    where, [self.finding(1, "claim b"),
                            self.finding(1, "claim c"),
                            {"event": vocab.FINDING_WAIVER_EVENT, "round": 2,
                             "fp": b, "answers_round": 1,
                             "authorized_by": "user", "reason": "overruled"}])
                self.refuses_and_appends_nothing(
                    ledger, [self.alias(b, c)],
                    f"an alias moving a waiver persisted in a {where} lineage")

    def test_another_lineages_ruling_cannot_disturb_this_lineages_answer(self):
        # The same shape, one difference: the second ruling of the merged
        # identity is in the OTHER lineage, so within the destination the
        # waiver still answers exactly the material it always did. A
        # whole-ledger comparison refuses this — a false refusal, the same
        # defect read from the other side.
        a, b = self.fp_of("claim a"), self.fp_of("claim b")
        ledger = self._ledger_where("open", [self.finding(1, "claim b")])
        ledger.add(self.finding(1, "claim a"), lineage=self.DEST)
        ledger.add({"event": vocab.FINDING_WAIVER_EVENT, "round": 2,
                    "fp": a, "answers_round": 1, "authorized_by": "user",
                    "reason": "overruled"}, lineage=self.DEST)
        self.admits(ledger, [self.alias(a, b)])
        answers = ledger.current_answers(self.DEST)
        self.assertEqual(
            [effect for pairs in answers.values() for effect, _ in pairs],
            [vocab.ANSWER_OVERRULES],
            "the destination's waiver still answers the ruling it always did")
        self.assertEqual([f["round"] for f in
                          ledger.standing_findings(self.OTHER)], [1],
                         "the other lineage's ruling is untouched by all this")

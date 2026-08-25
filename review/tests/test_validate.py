"""Validator rules (§5.1–5.2, §7) against the corpus and synthetic envelopes."""
import argparse
import contextlib
import dataclasses
import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Mapping

from review import config, transport, validate, vocab, wire
from review.cli import cmd_ledger_add
from review.digest import sha256_text
from review.ledger import Ledger
from review.tests import synth
from review.tests.util import REPO_ROOT

CFG = config.load(REPO_ROOT)
SHA = "9" * 40


def errs(items):
    return {i.code for i in items if i.level == "error"}


EVIDENCE_CHECKED = "\n\n## evidence checked\n\n- the diff\n"


def verdict_text(body, sha=SHA, wrapped=True, evidence=True):
    """A verdict envelope.

    `## evidence checked` is appended by default since round 1 F2 made the
    section grammar enforceable: these fixtures predate the check and every
    one of them omitted a section the hand-back shape has always declared
    mandatory, which is precisely the defect F2 named. Pass evidence=False to
    build the omission deliberately.
    """
    if evidence and "## evidence checked" not in body:
        body = body.rstrip("\n") + EVIDENCE_CHECKED
    if not wrapped:
        return body
    return f'<loupe-review-verdict sha="{sha}">\n{body}\n</loupe-review-verdict>'


FINDING = """### F1
Severity: {sev}
Classification: design_gap
Title: a mechanism is named but not provided
Evidence: design:100
Why: because
Required outcome: provide it
FALSIFICATION: {fals}
"""


class TestVerdictValidation(unittest.TestCase):
    def test_clean_with_findings_rejected(self):
        body = ("VERDICT: clean to advance\n\n## findings\n\n"
                + FINDING.format(sev="High", fals="run x"))
        v = wire.parse_verdict(verdict_text(body))
        self.assertIn("V-CLEAN-FINDINGS", errs(validate.validate_verdict(v, CFG)))

    def test_clean_requires_literal_none(self):
        body = "VERDICT: clean to advance\n\n## findings\n\nNone\n"
        v = wire.parse_verdict(verdict_text(body))
        self.assertEqual(errs(validate.validate_verdict(v, CFG)), set())

    def test_unavailable_references_block_a_clean_verdict(self):
        # §5.1 (F4/R2-F2): material unavailability maps to changes requested.
        body = ("VERDICT: clean to advance\n\n## findings\n\nNone\n\n"
                "## unavailable references\n\n- ~/Projects/elsewhere\n")
        v = wire.parse_verdict(verdict_text(body))
        self.assertIn("V-UNAVAILABLE-CLEAN",
                      errs(validate.validate_verdict(v, CFG)))

    def test_required_sections_and_order(self):
        """Round 1 F2 (High), falsification.

        Four probes, each of which previously returned zero validation items:
        a clean verdict with no evidence record, a reversed pair, a duplicated
        section, and an empty one.
        """
        clean_no_evidence = wire.parse_verdict(verdict_text(
            "VERDICT: clean to advance\n\n## findings\n\nNone\n",
            evidence=False))
        self.assertIn("V-SECTION-MISSING",
                      errs(validate.validate_verdict(clean_no_evidence, CFG)),
                      "merge-authorizing text may not omit what was checked")

        reversed_pair = wire.parse_verdict(verdict_text(
            "VERDICT: clean to advance\n\n## evidence checked\n\n- the diff\n"
            "\n## findings\n\nNone\n", evidence=False))
        self.assertIn("V-SECTION-ORDER",
                      errs(validate.validate_verdict(reversed_pair, CFG)))

        duplicated = wire.parse_verdict(verdict_text(
            "VERDICT: clean to advance\n\n## findings\n\nNone\n"
            "\n## findings\n\nNone\n\n## evidence checked\n\n- the diff\n",
            evidence=False))
        self.assertIn("V-SECTION-DUPLICATE",
                      errs(validate.validate_verdict(duplicated, CFG)))

        empty = wire.parse_verdict(verdict_text(
            "VERDICT: clean to advance\n\n## findings\n\nNone\n"
            "\n## evidence checked\n\n", evidence=False))
        self.assertIn("V-SECTION-EMPTY",
                      errs(validate.validate_verdict(empty, CFG)))

    def test_a_well_formed_verdict_raises_no_section_item(self):
        v = wire.parse_verdict(verdict_text(
            "VERDICT: clean to advance\n\n## findings\n\nNone\n"))
        self.assertEqual(errs(validate.validate_verdict(v, CFG)), set())

    def test_severity_order_enforced(self):
        f1 = FINDING.format(sev="Low", fals="x")
        f2 = FINDING.format(sev="High", fals="x").replace("### F1", "### F2")
        body = f"VERDICT: changes requested\n\n## findings\n\n{f1}\n{f2}"
        v = wire.parse_verdict(verdict_text(body))
        self.assertIn("V-ORDER", errs(validate.validate_verdict(v, CFG)))

    def test_blocking_without_falsification_downgrades_not_fails(self):
        body = ("VERDICT: changes requested\n\n## findings\n\n"
                + FINDING.format(sev="High", fals=""))
        v = wire.parse_verdict(verdict_text(body))
        items = validate.validate_verdict(v, CFG)
        self.assertNotIn("V-DOWNGRADE", errs(items))
        self.assertIn("V-DOWNGRADE", {i.code for i in items})


class TestRequestValidation(unittest.TestCase):
    def test_emitted_request_validates_clean(self):
        # The synthetic base every mutation below starts from: the real
        # emitter's output, gate-less, with a synthetic push stamp.
        text = synth.emitted_request()
        items = validate.validate_request(wire.parse_request(text),
                                          synth.NO_GATES)
        self.assertEqual(errs(items), set(), [i.message for i in items])

    def test_self_review_rejected(self):
        text = synth.emitted_request()
        stamp = f'reviewer="{CFG.roles["reviewer"]}"'
        self.assertIn(stamp, text)
        text = text.replace(stamp, f'reviewer="{CFG.roles["author"]}"')
        items = validate.validate_request(wire.parse_request(text), CFG)
        self.assertIn("R-SELF-REVIEW", errs(items))

    def test_gemini_reviewer_stamp_rejected(self):
        # §7 / §10.7: rejected by the validator, not merely discouraged.
        text = synth.emitted_request()
        text = text.replace(f'reviewer="{CFG.roles["reviewer"]}"',
                            'reviewer="gemini"')
        items = validate.validate_request(wire.parse_request(text), CFG)
        self.assertIn("R-REVIEWER-REJECTED", errs(items))

    def test_round_past_cap_is_advisory_and_still_emits(self):
        """User decision 2026-08-25: the cap warns, it does not refuse.

        A round count is a threshold, not an observed anomaly — it says how
        long the loop has run and nothing about whether it is closing
        anything, so it fired on lineages doing exactly what they should.
        The notice still names the round and the cap, and points at the
        verb that answers the question the count was standing in for.

        Mutation: make R-BUDGET an error again and the first assertion
        fails; drop it entirely and the second does.
        """
        text = synth.emitted_request()
        self.assertIn('round="2"', text)
        over = text.replace('round="2"', f'round="{CFG.round_cap + 1}"')
        items = validate.validate_request(wire.parse_request(over), CFG)
        self.assertNotIn("R-BUDGET", errs(items),
                         "the cap refuses again: it is advisory")
        past = [i for i in items if i.code == "R-BUDGET"]
        self.assertEqual(len(past), 1, "no notice at all is not advisory "
                                       "either — silence past the cap says "
                                       "nothing to the human")
        self.assertNotEqual(past[0].level, "error")
        self.assertIn("convergence", past[0].message,
                      "the notice does not name what answers the question "
                      "the count only proxies for")
        at_cap = text.replace('round="2"', f'round="{CFG.round_cap}"')
        self.assertEqual(
            [i for i in validate.validate_request(
                wire.parse_request(at_cap), CFG) if i.code == "R-BUDGET"],
            [], "a round AT the cap is not past it")

    def test_unrecognized_required_reference_cannot_validate_or_disappear_at_take(
            self):
        """Round 2 F5 (High), falsification.

        A required reference written in no recognised form validated clean,
        was counted by the précis the human reads, and was then dropped in
        silence by the reviewer-side probe. The human was told the reviewer
        must read five things while the reviewer's own command neither
        checked nor reported the fifth — required material evading the probe
        through a request the validator had blessed.

        Both halves are asserted here, because fixing either one alone leaves
        the same gap open from the other end.
        """
        from review import brief, transport
        text = synth.emitted_request()
        section = "\n## Reference\n"
        self.assertIn(section, text)
        good = f"  review.toml  sha256:{'0' * 64}  [required] the config\n"
        bad = "  missing-proof.md asserted with no digest\n"
        mutated = text.replace(section, section + "\n" + good + bad, 1)
        r = wire.parse_request(mutated)

        # Half one: it no longer validates. Nothing about the line can be
        # checked, so blessing it is the validator claiming an assurance it
        # never had.
        self.assertIn("R-REFERENCE-FORM",
                      errs(validate.validate_request(r, synth.NO_GATES)))

        # Half two: and it does not vanish on the way to the reviewer. The
        # count the human is given and the lines the reviewer's probe returns
        # must be the same set — that equality is the whole property.
        ref_section = r.sections["reference"]
        counted = brief._reference_states(ref_section)
        self.assertEqual(counted["in no recognised form"], 1)
        probed = transport.probe_references(CFG, ref_section)
        self.assertEqual(sum(counted.values()), len(probed),
                         "the précis and the probe must not disagree about "
                         "how many references exist")
        unreadable = [p for p in probed if p["status"].startswith("unrecognised")]
        self.assertEqual(len(unreadable), 1,
                         "a line nothing can check is reported as such, "
                         "never dropped")
        # The whole line is its own identifier: no path can be parsed out of
        # something that parses as nothing.
        self.assertEqual(unreadable[0]["path"], bad.strip())

        # The control: the recognised forms are still accepted, so the rule
        # bites the unreadable line rather than the section.
        only_good = text.replace(section, section + "\n" + good, 1)
        self.assertNotIn("R-REFERENCE-FORM",
                         errs(validate.validate_request(
                             wire.parse_request(only_good), synth.NO_GATES)))

    def test_diff_shape_drift_detected(self):
        # Hand-counted 723 vs machine 726 was a real round-1 finding. The
        # emitted request diffs HEAD...HEAD, so the machine shape is 0/0/0.
        r = wire.parse_request(synth.emitted_request())
        items = validate.validate_request(r, synth.NO_GATES,
                                          recomputed_shape=(0, 0, 0))
        self.assertNotIn("R-DIFF-SHAPE-DRIFT", errs(items))
        items = validate.validate_request(r, synth.NO_GATES,
                                          recomputed_shape=(1, 0, 0))
        self.assertIn("R-DIFF-SHAPE-DRIFT", errs(items))


def disposition_envelope(records, verdict_sha=SHA, head="8" * 40):
    return wire.emit_disposition("loupe", verdict_sha, head, "claude", 2,
                                 records)


def record(fid="F1", disp="accepted", subtype=None, payload=None):
    if payload is None:
        payload = {"change": "fixed", "verification": "test passes"}
    return {"finding_id": fid, "disposition": disp, "subtype": subtype,
            "payload": payload}


class TestDispositionValidation(unittest.TestCase):
    def make_verdict(self, n=2, sev="Medium"):
        blocks = []
        for i in range(1, n + 1):
            blocks.append(FINDING.format(sev=sev, fals="run x")
                          .replace("### F1", f"### F{i}")
                          .replace("not provided", f"not provided ({i})"))
        body = "VERDICT: changes requested\n\n## findings\n\n" + "\n".join(blocks)
        return wire.parse_verdict(verdict_text(body))

    def test_duplicate_wrapper_attribute_is_rejected(self):
        """FALSIFICATION for lineage-3 round-4 F2 (Medium). The round-3
        wrapper test claimed the single-value rule for all three envelopes
        and exercised only Request and Verdict; the Disposition plumbing
        (attr_defects → envelope_identity) had no assertion. Mutation: omit
        `d.attr_defects` from the envelope_identity call in
        validate_disposition and this fails on the first assertIn."""
        v = self.make_verdict(1)
        base = disposition_envelope([record("F1")])
        head = f'<loupe-review-disposition verdict_sha="{SHA}"'
        self.assertIn(head, base)
        # A repeated binding attribute: the FIRST is the value, the repeat
        # is refused.
        dup = base.replace(head, f'{head} verdict_sha="{"1" * 40}"', 1)
        d = wire.parse_disposition(dup)
        self.assertEqual(d.attrs["verdict_sha"], SHA)
        self.assertIn("E-ATTR-DUPLICATE",
                      errs(validate.validate_disposition(d, CFG, against=v)))
        # A repeated `head` as well.
        dup_head = base.replace(head, f'{head} head="{"9" * 40}"', 1)
        d = wire.parse_disposition(dup_head)
        self.assertIn("E-ATTR-DUPLICATE",
                      errs(validate.validate_disposition(d, CFG, against=v)))
        # Non-attribute residue inside the tag.
        residue = base.replace(head, f"{head} junk", 1)
        d = wire.parse_disposition(residue)
        self.assertIn("E-ATTR-RESIDUE",
                      errs(validate.validate_disposition(d, CFG, against=v)))
        # Canonical control: the single-valued wrapper carries neither.
        d = wire.parse_disposition(base)
        self.assertEqual(d.attr_defects, ())
        codes = errs(validate.validate_disposition(d, CFG, against=v))
        self.assertFalse(codes & {"E-ATTR-DUPLICATE", "E-ATTR-RESIDUE"})

    def test_completeness_every_finding_exactly_once(self):
        v = self.make_verdict(2)
        d = wire.parse_disposition(disposition_envelope([record("F1")]))
        items = validate.validate_disposition(d, CFG, against=v)
        self.assertIn("D-COMPLETENESS", errs(items),
                      "a finding never dies by omission")
        d = wire.parse_disposition(
            disposition_envelope([record("F1"), record("F1")]))
        self.assertIn("D-DUPLICATE",
                      errs(validate.validate_disposition(d, CFG, against=v)))

    def test_fingerprint_must_match_verdict(self):
        """Round 1 F3 (High), falsification.

        The probe: supply `fp2:0000000000000000` for a finding whose computed
        identity is something else. Validation returned no errors and the
        ledger kept the zero fingerprint, so a disposition could close an
        identity that no finding owns while the real one stayed open.
        """
        v = self.make_verdict(1)
        computed = v.findings[0].fingerprint()
        self.assertNotEqual(computed, "fp2:0000000000000000")
        rec = record("F1")
        rec["fingerprint"] = "fp2:0000000000000000"
        d = wire.parse_disposition(disposition_envelope([rec]))
        self.assertIn("D-IDENTITY",
                      errs(validate.validate_disposition(d, CFG, against=v)))

    def test_ledger_add_rejects_mismatched_fingerprint_without_against_flag(
            self):
        """Round 2 F2 (High), falsification.

        Round 1 closed the forged-identity hole in `respond`. It left the
        other door into the same ledger open: `ledger add` is a documented
        ingestion path, and standalone — with no `--against` — it validated a
        disposition without ever resolving the verdict being answered, then
        recorded whatever fingerprint the envelope carried. The real finding
        stayed open while the forged identity drove closure and breakers.

        The class, not the case: identity is derived wherever a disposition is
        recorded, and a path that cannot retrieve the answered verdict refuses
        instead of recording an identity it never checked.
        """
        # Only the unavailable leg lives here now. Round 3 F1 found that the
        # leg this test CALLED "retrievable" stored the verdict at round 1
        # while the disposition declares round 2, so both legs took the same
        # early return and neither ever compared a fingerprint. The retrieval
        # proof is its own test below, which asserts retrieval before relying
        # on it — the control this one could not be.
        with self._fixture() as (root, cfg, args, ledger):
            code, payload = self._ingest(args, cfg)
            self.assertNotEqual(code, 0)
            self.assertEqual(payload["ok"], False)
            self.assertIsNone(
                transport.answered_verdict(cfg, self._disposition(), ledger),
                "the premise of this leg: nothing is retrievable yet")
            self.assertEqual(self._dispositions(root), [],
                             "a disposition whose verdict cannot be "
                             "retrieved must leave no event behind")

    # ---------------------------------------------------------- ingest rig

    DISPOSITION_ROUND = 2          # what disposition_envelope() stamps

    def _forged_envelope(self):
        rec = record("F1")
        rec["fingerprint"] = "fp2:0000000000000000"
        return disposition_envelope([rec])

    def _disposition(self):
        return wire.parse_disposition(self._forged_envelope())

    @contextlib.contextmanager
    def _fixture(self):
        try:
            tmp = tempfile.TemporaryDirectory()
        except OSError as exc:                          # pragma: no cover
            self.skipTest(f"filesystem writes denied ({exc})")
        with tmp:
            root = Path(tmp.name)
            cfg = dataclasses.replace(CFG, ledger_dir=root)
            d_path = root / "d.md"
            d_path.write_text(self._forged_envelope(), encoding="utf-8")
            args = argparse.Namespace(envelope=str(d_path), round=None,
                                      tokens=None, ledger_dir=str(root))
            yield root, cfg, args, Ledger(root)

    @staticmethod
    def verdict_body(title=None, sev="Medium"):
        block = FINDING.format(sev=sev, fals="run x")
        if title:
            block = block.replace("a mechanism is named but not provided",
                                  title)
        return verdict_text(
            "VERDICT: changes requested\n\n## findings\n\n" + block)

    def _record_verdict(self, cfg, ledger, text=None):
        """File a verdict the way the product files one: ledger event first,
        bytes kept beside it, at the round the disposition declares."""
        text = text or self.verdict_body()
        v = wire.parse_verdict(text)
        self.assertTrue(v.findings, "the fixture must carry a finding")
        ledger.add_all(transport.verdict_events(
            v, self.DISPOSITION_ROUND, transport._digest_text(text),
            len(text.encode("utf-8"))))
        transport.keep_bytes(cfg, self.DISPOSITION_ROUND, "verdict", text)
        return text, v

    def _ingest(self, args, cfg):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cmd_ledger_add(args, cfg)
        return code, json.loads(buf.getvalue())

    @staticmethod
    def _dispositions(root):
        return [e for e in Ledger(root).events()
                if e.get("event") == "disposition"]

    def test_ledger_add_retrievable_branch_is_actually_retrieved(self):
        """Round 3 F1 (Blocker), falsification.

        The round-2 fix was accepted on a claim that `ledger add` had been
        proved in both the unavailable and the retrievable state. Only the
        first was true: the fixture kept the verdict at round 1 while the
        disposition declared round 2, the lookup derives its path from the
        disposition's round, and so the "retrievable" leg quietly took the
        unavailable early return. The forged-identity comparison — the whole
        point of the finding — had no regression behind it at all, while the
        named falsification command sat green.

        So this asserts retrieval FIRST, and only then rules on the refusal.
        A test that assumes its own premise is how the gap survived.
        """
        with self._fixture() as (root, cfg, args, ledger):
            text, v = self._record_verdict(cfg, ledger)
            computed = v.findings[0].fingerprint()
            self.assertNotEqual(computed, "fp2:0000000000000000")

            # The premise, proved rather than assumed.
            against = transport.answered_verdict(cfg, self._disposition(),
                                                 ledger)
            self.assertIsNotNone(against, "the retrievable leg must retrieve")
            self.assertEqual(against.findings[0].fingerprint(), computed)

            # And now the property: the forged identity is caught by
            # comparison, not by the verdict being missing.
            code, payload = self._ingest(args, cfg)
            self.assertNotEqual(code, 0)
            self.assertIn("D-IDENTITY",
                          {i["code"] for i in payload.get("items", [])},
                          "the refusal must be a fingerprint mismatch, not "
                          "an unavailable-verdict early return")
            self.assertEqual(self._dispositions(root), [])

    def test_ledger_add_rejects_tampered_exchange_verdict(self):
        """Round 3 F2 (High), falsification.

        Round 2 moved identity authority out of the disposition and into the
        retained exchange copy — a file kept best-effort, beside the ledger,
        guarded by nothing. Replacing it with a DIFFERENT valid verdict at the
        same SHA therefore handed the forged-identity capability straight
        back: the attacker picks which fingerprint reaches closures, breakers
        and the merge decision, and `ledger add` exited 0.

        The append-only record is the authority; the file is a cache of bytes
        that record already digested.
        """
        with self._fixture() as (root, cfg, args, ledger):
            original, v = self._record_verdict(cfg, ledger)
            legit = v.findings[0].fingerprint()

            # A different, entirely well-formed verdict at the SAME SHA,
            # carrying a finding of the attacker's choosing.
            attacker_text = self.verdict_body(
                title="an identity of my choosing")
            attacker = wire.parse_verdict(attacker_text)
            self.assertEqual(attacker.sha, v.sha, "same SHA is the premise")
            self.assertNotEqual(attacker.findings[0].fingerprint(), legit)

            path = transport.exchange_path(cfg, self.DISPOSITION_ROUND,
                                           "verdict")
            path.write_text(attacker_text, encoding="utf-8")

            self.assertIsNone(
                transport.answered_verdict(cfg, self._disposition(), ledger),
                "bytes that do not reproduce the recorded digest are not the "
                "recorded verdict, however well-formed they are")
            code, _ = self._ingest(args, cfg)
            self.assertNotEqual(code, 0)
            self.assertEqual(self._dispositions(root), [],
                             "the attacker-selected identity reached the "
                             "ledger before round 3")

            # The control: restore the recorded bytes and retrieval works
            # again, so the rule binds tampering and not the mechanism.
            path.write_text(original, encoding="utf-8")
            self.assertIsNotNone(
                transport.answered_verdict(cfg, self._disposition(), ledger))

    def test_two_verdicts_at_one_round_are_ambiguous_not_guessed(self):
        # The third refusal the required outcome names. One round holding two
        # different verdicts is a corrupt record, and picking either is the
        # guess this review has already rejected once for repeated SHAs.
        with self._fixture() as (root, cfg, args, ledger):
            self._record_verdict(cfg, ledger)
            other = self.verdict_body(title="a second ruling", sev="High")
            ledger.add_all(transport.verdict_events(
                wire.parse_verdict(other), self.DISPOSITION_ROUND,
                transport._digest_text(other), len(other.encode("utf-8"))))
            self.assertIsNone(
                transport.answered_verdict(cfg, self._disposition(), ledger))
            code, _ = self._ingest(args, cfg)
            self.assertNotEqual(code, 0)
            self.assertEqual(self._dispositions(root), [])

    def test_severity_cannot_be_restated_differently(self):
        # Severity travels with identity: understating it would let a blocking
        # finding be deferred, which D-BLOCKING reads from this same field.
        v = self.make_verdict(1, sev="High")
        rec = record("F1")
        rec["severity"] = "Low"
        d = wire.parse_disposition(disposition_envelope([rec]))
        self.assertIn("D-IDENTITY",
                      errs(validate.validate_disposition(d, CFG, against=v)))

    def test_a_matching_identity_is_accepted(self):
        # Agreement is not the defect; substitution is.
        v = self.make_verdict(1)
        rec = record("F1")
        rec["fingerprint"] = v.findings[0].fingerprint()
        rec["severity"] = v.findings[0].severity
        d = wire.parse_disposition(disposition_envelope([rec]))
        self.assertNotIn("D-IDENTITY",
                         errs(validate.validate_disposition(d, CFG,
                                                            against=v)))

    def test_a_record_for_an_unknown_finding_is_rejected(self):
        v = self.make_verdict(1)
        d = wire.parse_disposition(disposition_envelope([record("F99")]))
        self.assertIn("D-UNKNOWN-FINDING",
                      errs(validate.validate_disposition(d, CFG, against=v)))

    def test_unknown_vocabulary_rejected(self):
        d = wire.parse_disposition(
            disposition_envelope([record(disp="challenged")]))
        self.assertIn("D-TERM", errs(validate.validate_disposition(d, CFG)))

    def test_blocking_finding_cannot_be_deferred_or_preference(self):
        v = self.make_verdict(1, sev="High")
        for disp, payload in (
                ("deferred", {"destination": "TODO", "trigger": "later"}),
                ("preference", {"axis": "style",
                                "why_reviewer_option_acceptable": "both work"})):
            d = wire.parse_disposition(disposition_envelope(
                [record(disp=disp, payload=payload)]))
            items = validate.validate_disposition(d, CFG, against=v)
            self.assertIn("D-BLOCKING", errs(items), disp)

    def test_mandatory_payloads_enforced(self):
        d = wire.parse_disposition(disposition_envelope(
            [record(disp="deferred", payload={"destination": "TODO"})]))
        self.assertIn("D-PAYLOAD", errs(validate.validate_disposition(d, CFG)))

    def test_escalation_needs_evidence_or_authority(self):
        base = {"question": "q", "positions": "a vs b"}
        d = wire.parse_disposition(disposition_envelope(
            [record(disp="escalated", payload=base)]))
        self.assertIn("D-ESCALATION",
                      errs(validate.validate_disposition(d, CFG)))
        ok = dict(base, authority="the user", criterion="ownership boundary")
        d = wire.parse_disposition(disposition_envelope(
            [record(disp="escalated", payload=ok)]))
        self.assertEqual(errs(validate.validate_disposition(d, CFG)), set())

    def test_test_amended_subtype_payload(self):
        d = wire.parse_disposition(disposition_envelope(
            [record(subtype="test_amended")]))
        self.assertIn("D-TEST-AMENDED",
                      errs(validate.validate_disposition(d, CFG)))
        payload = {"change": "x", "verification": "y",
                   "original_test": "paraphrases collide",
                   "amended_test": "syntactic identity + lineage",
                   "why_unsatisfiable": "demands semantics from syntax"}
        d = wire.parse_disposition(disposition_envelope(
            [record(subtype="test_amended", payload=payload)]))
        self.assertEqual(errs(validate.validate_disposition(d, CFG)), set())

    def test_sha_binding_checked_against_verdict(self):
        v = self.make_verdict(1)
        d = wire.parse_disposition(
            disposition_envelope([record()], verdict_sha="0" * 40))
        self.assertIn("D-SHA-MISMATCH",
                      errs(validate.validate_disposition(d, CFG, against=v)))


class TestFindingFieldGrammar(unittest.TestCase):
    """Sweep F1 (Blocker): every finding field is single-valued.

    Fields were parsed last-write-wins while `fields_present` recorded only
    that a name had appeared, so a High finding that named a test and then
    repeated `FALSIFICATION:` empty validated clean, downgraded itself to
    non-blocking in every rule that reads `falsification`, and an acceptance
    without a run record validated against it — the named test never
    reached the ledger, the two breakers that read runs never saw it.
    """

    NAMED = "command: python3 -m unittest named.test"

    def _verdict(self, block):
        return wire.parse_verdict(verdict_text(
            "VERDICT: changes requested\n\n## findings\n\n" + block))

    def _accept_without_record(self, v):
        d = wire.parse_disposition(disposition_envelope([record(
            payload={"change": "fixed", "verification": "ran it"})]))
        return errs(validate.validate_disposition(d, CFG, against=v))

    def test_duplicate_falsification_cannot_suppress_the_named_test(self):
        """FALSIFICATION for F1. Mutation: restore last-write-wins parsing
        (`values[field] = ...` on every occurrence, no duplicate record) and
        this fails on the first assertion — the duplicated field is not
        rejected — and on the third, because the parsed test is empty."""
        block = FINDING.format(sev="High", fals=self.NAMED)
        dup = block.rstrip("\n") + "\nFALSIFICATION:\n"
        v = self._verdict(dup)
        # 1. The duplicated field is rejected as a verdict.
        codes = errs(validate.validate_verdict(v, CFG))
        self.assertIn("V-FIELD-DUPLICATE", codes)
        # 2. And presence alone was never the point: V-FIELDS still passes,
        #    which is exactly why the duplicate needs its own rule.
        self.assertNotIn("V-FIELDS", codes)
        # 3. The FIRST statement is the finding's value; the repeat neither
        #    replaced it nor was folded into it. So the finding still
        #    visibly names a test, is still blocking, and an acceptance with
        #    no run record is still refused — the bypass is closed at the
        #    disposition layer too, not only at the verdict layer.
        f = v.findings[0]
        self.assertEqual(f.falsification, self.NAMED)
        self.assertEqual(f.duplicate_fields, ("FALSIFICATION",))
        self.assertTrue(validate.effective_blocking(f.severity,
                                                   f.falsification, CFG))
        self.assertIn("D-FALSIFICATION-MISSING", self._accept_without_record(v))

    def test_a_repeat_with_continuation_lines_folds_into_nothing(self):
        # A repeated field carrying its own continuation must not append
        # those lines to the first value either — that would be a second
        # way for one statement to alter another.
        block = FINDING.format(sev="High", fals=self.NAMED)
        dup = block.rstrip("\n") + "\nFALSIFICATION: observation: x\n  more\n"
        f = self._verdict(dup).findings[0]
        self.assertEqual(f.falsification, self.NAMED)

    def test_every_field_is_single_valued_not_only_falsification(self):
        for name, extra in (("Severity", "Severity: Low"),
                            ("Title", "Title: another"),
                            ("Evidence", "Evidence: other:1"),
                            ("Anchor", "Anchor: a\nAnchor: b"),
                            ("Preventable-by", "Preventable-by: tests\n"
                                               "Preventable-by: tests")):
            block = FINDING.format(sev="High", fals=self.NAMED)
            v = self._verdict(block.rstrip("\n") + "\n" + extra + "\n")
            codes = errs(validate.validate_verdict(v, CFG))
            self.assertIn("V-FIELD-DUPLICATE", codes, name)

    def test_paired_valid_control(self):
        # The same finding stated once: no duplicate error, the test is
        # named, and an acceptance WITH a run record validates.
        v = self._verdict(FINDING.format(sev="High", fals=self.NAMED))
        self.assertNotIn("V-FIELD-DUPLICATE",
                         errs(validate.validate_verdict(v, CFG)))
        self.assertEqual(v.findings[0].duplicate_fields, ())
        d = wire.parse_disposition(disposition_envelope([record(
            payload={"change": "fixed", "verification": "ran it",
                     "falsification": {"status": "pass",
                                       "mutation": "fails_without_fix"}})]))
        self.assertNotIn("D-FALSIFICATION-MISSING",
                         errs(validate.validate_disposition(d, CFG, against=v)))


class TestRequestSectionGrammar(unittest.TestCase):
    """Sweep F5 (High): the request's section grammar is closed.

    The verdict grammar has been closed since lineage-2 round 1 (duplicates
    and order are defects, counted from raw headings). The request grammar
    was not: `_split_sections` is a dict, a second `## Reference` replaced
    the first, and a required reference the author had named vanished
    between the raw envelope and both the validator and the reviewer's
    probe — with zero errors. Same rule, second layer.
    """

    def _errs(self, text):
        return errs(validate.validate_request(wire.parse_request(text),
                                              synth.NO_GATES))

    def test_duplicate_reference_cannot_hide_required_material(self):
        """FALSIFICATION for F5. Mutation: drop the raw-heading count
        (`_request_section_grammar_items`) and let the parsed dict decide —
        the duplicated section validates clean and this fails."""
        text = synth.emitted_request()
        section = "\n## Reference\n"
        self.assertIn(section, text)
        first = (section + "\n  missing-first.md  sha256:" + "0" * 64
                 + "  [required] named first, then overwritten\n")
        mutated = text.replace(section, first + section, 1)
        codes = self._errs(mutated)
        self.assertIn("R-SECTION-DUPLICATE", codes)
        # The property that closes the gap: an envelope that names a
        # required reference under an earlier same-named heading is not a
        # valid request, so nothing downstream — the probe, the take, the
        # brief's reference count — can report the survivor as the whole.
        self.assertTrue(codes & {"R-SECTION-DUPLICATE"},
                        "a duplicated required section must fail before "
                        "any content under it is trusted")

    def test_every_required_section_is_covered(self):
        text = synth.emitted_request()
        for heading in ("## Taxonomy", "## Claim", "## Evidence",
                        "## Contract", "## Reference"):
            marker = "\n" + heading
            self.assertIn(marker, text, heading)
            body = "\n\n  duplicated content\n" if heading != "## Taxonomy" \
                else "\n\nSeverity, ordered: Blocker > High\n"
            mutated = text.replace(marker, marker + body + marker, 1)
            self.assertIn("R-SECTION-DUPLICATE", self._errs(mutated), heading)

    def test_declared_order_is_enforced(self):
        # Move the whole Contract section after Reference: every section is
        # still present exactly once, and the order is wrong.
        text = synth.emitted_request()
        c0 = text.index("\n## Contract")
        r0 = text.index("\n## Reference")
        self.assertLess(c0, r0)
        contract = text[c0:r0]
        end = text.index("\n## Review scope")
        mutated = text[:c0] + text[r0:end] + contract + text[end:]
        codes = self._errs(mutated)
        self.assertIn("R-SECTION-ORDER", codes)
        self.assertNotIn("R-SECTION-DUPLICATE", codes)

    def test_a_malformed_alias_is_absent_not_present(self):
        text = synth.emitted_request()
        mutated = text.replace("\n## Reference\n", "\n## References\n", 1)
        codes = self._errs(mutated)
        self.assertIn("R-REFERENCE", codes,
                      "a heading that only starts like the section is not it")

    def test_commentary_on_a_heading_is_still_that_section(self):
        # The corpus carries `## Contract — invariants that apply` and
        # `## Taxonomy (declared — ...)`; the leading word is the identity.
        text = synth.emitted_request()
        mutated = text.replace("\n## Reference\n",
                               "\n## Reference — what to read\n", 1)
        codes = self._errs(mutated)
        self.assertNotIn("R-REFERENCE", codes)
        self.assertNotIn("R-SECTION-DUPLICATE", codes)

    def test_paired_valid_control(self):
        codes = self._errs(synth.emitted_request())
        self.assertFalse(codes & {"R-SECTION-DUPLICATE", "R-SECTION-ORDER"},
                         codes)
        # The historical envelopes are checked in the workbench-only evidence
        # suite; a travelling test reads no workbench-only path.


class TestJSONSingleValueGrammar(unittest.TestCase):
    """Lineage-3 round 4, F1 (High): the fourth layer. `json.loads` keeps
    the LAST of repeated object members and discards the rest before any
    validator sees the object — so a disposition body could say
    `"disposition": "deferred"` then `"accepted"` and validate as accepted,
    and a machine attestation could say `"exit_code": 1` then `0` and
    validate as green. wire.load_json is the ONE boundary both bodies (and
    the author's judgment file) are read through; it refuses a repeated
    member at any depth and names it.
    """

    def _blocking_verdict(self):
        body = ("VERDICT: changes requested\n\n## findings\n\n"
                + FINDING.format(sev="High", fals="run x"))
        return wire.parse_verdict(verdict_text(body))

    def _disposition_text(self, body_json: str) -> str:
        return (f'<loupe-review-disposition verdict_sha="{SHA}" '
                f'head="{"8" * 40}" author="claude" round="1">\n'
                f"{body_json}\n</loupe-review-disposition>")

    def test_duplicate_disposition_and_attestation_keys_are_rejected(self):
        """FALSIFICATION for round-4 F1. Mutation: read either body with
        ordinary `json.loads` (or drop `body_defects` from the parser /
        the D-JSON-DUPLICATE errors from the validator) and the conflicting
        disposition validates as accepted and the conflicting attestation
        validates green — the two assertIn calls fail — while the canonical
        controls stay green either way."""
        from review import config
        from review.tests import synth
        v = self._blocking_verdict()
        # --- Disposition body: "deferred" then "accepted" for a blocking
        # finding. Ordinary json.loads keeps "accepted"; the boundary refuses.
        rec = ('{"author": "claude", "head": "%s", "round": 1, '
               '"dispositions": [{"finding_id": "F1", '
               '"disposition": "deferred", "disposition": "accepted", '
               '"payload": {"change": "x", "verification": "y", '
               '"falsification": {"status": "pass", '
               '"mutation": "fails_without_fix"}}}]}' % ("8" * 40))
        d = wire.parse_disposition(self._disposition_text(rec))
        self.assertTrue(d.body_defects)
        self.assertEqual(d.data, {}, "a body with a repeated member is not "
                                     "read at all — no value is chosen")
        codes = errs(validate.validate_disposition(d, CFG, against=v))
        self.assertIn("D-JSON-DUPLICATE", codes)
        message = next(i.message for i in validate.validate_disposition(
            d, CFG, against=v) if i.code == "D-JSON-DUPLICATE")
        self.assertIn("'disposition'", message, "the member is named")
        # Nested depth: a repeat inside the payload is refused too.
        nested = rec.replace('"status": "pass"',
                             '"status": "fail", "status": "pass"', 1)
        d2 = wire.parse_disposition(self._disposition_text(nested))
        self.assertIn("D-JSON-DUPLICATE",
                      errs(validate.validate_disposition(d2, CFG, against=v)))
        # Canonical control: the same record with one `disposition` member
        # validates (no D-JSON-DUPLICATE, no falsification error).
        canonical = rec.replace('"disposition": "deferred", ', "", 1)
        d3 = wire.parse_disposition(self._disposition_text(canonical))
        self.assertEqual(d3.body_defects, ())
        codes3 = errs(validate.validate_disposition(d3, CFG, against=v))
        self.assertNotIn("D-JSON-DUPLICATE", codes3)
        self.assertFalse({c for c in codes3 if c.startswith("D-FALSIF")})
        # --- Attestation block: `"exit_code": 1` before `"exit_code": 0` in
        # the first exact-SHA record. Ordinary json.loads keeps 0 (green).
        cfg = dataclasses.replace(config.load(REPO_ROOT),
                                  gates=[{"id": "probe", "command": ["true"],
                                          "blocking": True}])
        sha = synth.head_sha()
        block = synth.attestation_block(cfg.wrapper_tag, sha, gate_id="probe",
                                        exit_code=0, blocking=True)
        self.assertIn('"exit_code": 0', block)
        conflicting = block.replace('"exit_code": 0',
                                    '"exit_code": 1, "exit_code": 0', 1)
        evidence = f"{conflicting}\n\nNOT captured — nothing\n"
        items = validate.validate_attestations(evidence, cfg, request_sha=sha)
        codes = errs(items)
        self.assertTrue(codes, "a conflicting attestation must not validate")
        joined = " ".join(i.message for i in items)
        self.assertIn("'exit_code'", joined, "the member is named")
        # Canonical control: the unmodified block validates green.
        control = validate.validate_attestations(
            f"{block}\n\nNOT captured — nothing\n", cfg, request_sha=sha)
        self.assertEqual(errs(control), set(), [i.message for i in control])
        # And the boundary itself: depth, and the ordinary error kept.
        with self.assertRaises(wire.DuplicateMember) as ctx:
            wire.load_json('{"a": {"b": 1, "b": 2}}')
        self.assertEqual(ctx.exception.name, "b")
        with self.assertRaises(json.JSONDecodeError):
            wire.load_json("{not json")
        self.assertEqual(wire.load_json('{"a": [1, {"c": 2}]}'),
                         {"a": [1, {"c": 2}]})

    def test_duplicate_claim_members_are_rejected(self):
        """FALSIFICATION for lineage-3 round-5 F1 (High): the sixth
        boundary. `emit.load_claim` read the author's claim file with
        ordinary `json.loads`, so `"references": ["must-read.md"]` followed
        by `"references": []` loaded as `[]` and the earlier required
        reference was erased before emission, structural validation,
        digesting or the reviewer's probe could see it had been declared.
        Mutation: restore `json.loads` in load_claim (or drop the
        ClaimDefective handling from `_emit`/`cmd_handoff`) and the
        conflicting claim loads with `references == []` and both verbs reach
        `ensure_pushed`; the assertions on the refusal and on the sentinel
        fail. The canonical claim loads unchanged."""
        import argparse
        import contextlib
        import io
        import unittest.mock
        from review import emit
        from review.cli import cmd_emit_request, cmd_handoff
        with tempfile.TemporaryDirectory() as tmp:
            claim = Path(tmp) / "claim.json"
            # Top-level repeat: the earlier required reference would vanish.
            claim.write_text('{"objective": "x", "risk": "low", '
                             '"references": [{"path": "must-read.md", '
                             '"required": true}], "references": []}',
                             encoding="utf-8")
            with self.assertRaises(emit.ClaimDefective) as ctx:
                emit.load_claim(claim)
            self.assertEqual(ctx.exception.name, "references")
            # Nested repeat: inside a reference object.
            nested = Path(tmp) / "nested.json"
            nested.write_text('{"objective": "x", "references": [{"path": '
                              '"a.md", "required": false, "required": true}]}',
                              encoding="utf-8")
            with self.assertRaises(emit.ClaimDefective) as ctx:
                emit.load_claim(nested)
            self.assertEqual(ctx.exception.name, "required")
            # Canonical control loads unchanged.
            good = Path(tmp) / "good.json"
            good.write_text('{"objective": "x", "risk": "low", '
                            '"references": [{"path": "must-read.md", '
                            '"required": true}]}', encoding="utf-8")
            self.assertEqual(emit.load_claim(good)["references"][0]["path"],
                             "must-read.md")
            # Both verbs refuse as blocked, naming the member, BEFORE
            # ensure_pushed (which would push) — a sentinel that raises if
            # reached proves the order.
            cfg = dataclasses.replace(CFG, ledger_dir=Path(tmp))
            for fn, argv in (
                    (cmd_emit_request, dict(claim_file=str(claim), base=None,
                                            head=None, local_only=False,
                                            out=None, ledger_dir=tmp)),
                    (cmd_handoff, dict(claim_file=str(nested), base=None,
                                       head=None, local_only=False, out=None,
                                       ledger_dir=tmp))):
                buf = io.StringIO()
                with unittest.mock.patch.object(
                        emit, "ensure_pushed",
                        side_effect=AssertionError("pushed before the "
                                                   "claim was judged")), \
                        contextlib.redirect_stdout(buf):
                    code = fn(argparse.Namespace(**argv), cfg)
                self.assertNotEqual(code, 0)
                payload = json.loads(buf.getvalue())
                self.assertEqual(payload["next_kind"], "blocked", payload)
                self.assertIsNone(payload["next"])
                self.assertIn("more than once", payload["error"])
                self.assertTrue(payload["remedy"])
                self.assertEqual(Ledger(Path(tmp)).events(), [],
                                 "nothing recorded on a defective claim")

    def test_duplicate_claim_members_are_rejected_before_ledger_reads(self):
        """FALSIFICATION for lineage-3 round-6 F1 (High): the ordering, not
        the grammar. `cmd_handoff` built the ledger and ran
        `transport.handoff_preflight` — which resolves missing dispositions
        and fired breakers through `Ledger.events`, reading `ledger.jsonl` —
        BEFORE `load_claim` judged the author's bytes. A claim stating a
        member twice therefore caused ledger state to be read before its
        grammar was accepted. The round-5 regression asserts the ledger is
        left EMPTY, which proves no write and cannot see a read.
        Mutation: move the `_capture_claim` call back below
        `handoff_preflight` in `cmd_handoff` and the defective claim reaches
        the sentinel too, so the first assertion fails. The canonical control
        must reach the sentinel, or the fixture proves nothing."""
        import argparse
        import contextlib
        import io
        import unittest.mock
        from review.cli import cmd_handoff

        class LedgerRead(RuntimeError):
            """LEDGER_READ_BEFORE_CLAIM_VALIDATION."""

        def sentinel(self):
            raise LedgerRead("LEDGER_READ_BEFORE_CLAIM_VALIDATION")

        def handoff_args(claim_path, tmp):
            return argparse.Namespace(claim_file=str(claim_path), base=None,
                                      head=None, local_only=False, out=None,
                                      ledger_dir=tmp)

        with tempfile.TemporaryDirectory() as tmp:
            dup = Path(tmp) / "dup.json"
            dup.write_text('{"objective": "x", "references": [{"path": '
                           '"must-read.md", "required": true}], '
                           '"references": []}', encoding="utf-8")
            good = Path(tmp) / "good.json"
            good.write_text('{"objective": "x", "risk": "low"}',
                            encoding="utf-8")
            cfg = dataclasses.replace(CFG, ledger_dir=Path(tmp))
            # The defective claim is judged first: blocked recovery, and the
            # sentinel is never reached.
            buf = io.StringIO()
            with unittest.mock.patch.object(Ledger, "events", sentinel), \
                    contextlib.redirect_stdout(buf):
                code = cmd_handoff(handoff_args(dup, tmp), cfg)
            self.assertNotEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["next_kind"], "blocked", payload)
            self.assertIsNone(payload["next"])
            self.assertIn("more than once", payload["error"])
            self.assertIn("references", payload["error"])
            self.assertTrue(payload["remedy"])
            # Paired control: the sentinel is live — a canonical claim gets
            # past the claim boundary and does reach the ledger read.
            with unittest.mock.patch.object(Ledger, "events", sentinel), \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(LedgerRead):
                    cmd_handoff(handoff_args(good, tmp), cfg)

    def test_the_author_judgment_file_goes_through_the_same_boundary(self):
        # `respond --from-json` reads the author's file through load_json:
        # two `disposition` members for one finding are refused, blocked,
        # nothing rendered.
        import argparse
        import contextlib
        import io
        from review.cli import cmd_respond
        with tempfile.TemporaryDirectory() as tmp:
            v = Path(tmp) / "v.md"
            v.write_text(verdict_text(
                "VERDICT: changes requested\n\n## findings\n\n"
                + FINDING.format(sev="High", fals="run x")), encoding="utf-8")
            j = Path(tmp) / "d.json"
            j.write_text('{"head": "%s", "dispositions": [{"finding_id": '
                         '"F1", "disposition": "deferred", "disposition": '
                         '"accepted", "payload": {"change": "x", '
                         '"verification": "y"}}]}' % ("8" * 40),
                         encoding="utf-8")
            cfg = dataclasses.replace(CFG, ledger_dir=Path(tmp))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = cmd_respond(argparse.Namespace(
                    verdict=str(v), from_json=str(j), out=None,
                    ledger_dir=tmp), cfg)
            self.assertNotEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["next_kind"], "blocked")
            self.assertIn("'disposition'", payload["error"])


class ClaimSentinel(RuntimeError):
    """One of the things a defective claim must never reach."""


class TestClaimGrammarClosedWorld(unittest.TestCase):
    """FALSIFICATION for lineage-3 round-7 F1 (High): the claim boundary is
    closed over its whole domain, and both emitting verbs complete it before
    any ledger, second-read, Git, gate or record I/O.

    Rounds 5 and 6 closed ONE defect — a member stated twice — and put that
    check ahead of ledger reads. The domain stayed open: `null`, `true`, `3`,
    `"x"` and `[]` were all admitted as claims and reached lifecycle state;
    every declared field accepted any type and reached `ensure_pushed`, which
    commits and pushes tracked work; an unknown member was dropped in
    silence, so a misspelled `stop_condition_typo` erased a stop condition;
    malformed JSON escaped as a raw JSONDecodeError instead of the typed
    refusal; and `parse_claim("null")` returned None, colliding with `_emit`'s
    "not captured" sentinel and reopening the path, so the emitted Claim could
    come from a second read the digest did not attest.

    The table is DERIVED from `vocab.CLAIM_FIELDS`, `CLAIM_REFERENCE_FIELDS`
    and `CLAIM_REQUIRED` — the same authority the boundary validates from —
    so a member or kind added there without a case here fails the coverage
    assertions rather than passing untested.

    Mutation matrix; each of these must fail this class:
      1. restore ordinary duplicate-collapsing `json.loads` in parse_claim;
      2. move `_capture_claim` below `handoff_preflight` in cmd_handoff;
      3. restore `claim=None` as `_emit`'s overloaded "not captured" sentinel;
      4. drop the top-level-kind check in validate_claim;
      5. drop the declared-field type checks;
      6. drop the nested-reference checks;
      7. drop the unknown-member checks;
      8. reopen the claim path downstream (a second read);
      9. ignore `nonempty` for `objective` alone (round-8 F1);
     10. drop the UnicodeDecodeError conversion (round-8 F2);
     11. restore `Path(path) if path else None` in the CLI (round-8 F3);
     12. drop the Unicode-scalar check on claim strings (round-8 F4).
    """

    # Every JSON kind, so "every non-object top-level kind" is enumerated
    # rather than asserted. `object` is the admitted one.
    JSON_KINDS = {"null": "null", "boolean": "true", "number": "3",
                  "string": '"x"', "array": "[]", "object": "{}"}

    # A valid value per kind the authority declares — the valid side of the
    # table is schema-derived too, so a new member joins the full control
    # automatically.
    VALID = {"string": "x", "list_of_string": ["a"],
             "references": [{"path": "review.toml", "required": True,
                             "note": "n"}]}

    # A wrong value per kind: every JSON kind the member may NOT be, plus the
    # wrong-element case for the two container kinds.
    WRONG = {
        "string": [None, True, 3, [], {}],
        "list_of_string": [None, True, 3, "x", {}, [1], [None]],
        "references": [None, True, 3, "x", {}, [1], ["x"], [None]],
    }

    def setUp(self):
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="claim-closed-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.cfg = dataclasses.replace(CFG, ledger_dir=self.tmp)

    # ------------------------------------------------------------ the probe

    @contextlib.contextmanager
    def _sentinels(self, claim_path, reads):
        """Everything the claim boundary must precede, wired to fire.

        `_ledger` and `Ledger.events` are the lifecycle; `ensure_pushed` is
        Git (it commits and pushes); `run_gates` is execution; `Ledger.add`
        is the record; `load_claim` and the read counter are the second read.
        """
        import unittest.mock
        from review import cli, emit
        from review.ledger import Ledger

        def fire(name):
            def go(*a, **k):
                raise ClaimSentinel(name)
            return go

        real_read = Path.read_text

        def counting_read(self_path, *a, **k):
            if claim_path is not None and Path(self_path) == Path(claim_path):
                reads.append(str(self_path))
            return real_read(self_path, *a, **k)

        with unittest.mock.patch.object(cli, "_ledger", fire("_ledger")), \
                unittest.mock.patch.object(Ledger, "events",
                                           fire("Ledger.events")), \
                unittest.mock.patch.object(Ledger, "add",
                                           fire("Ledger.add")), \
                unittest.mock.patch.object(emit, "load_claim",
                                           fire("second read")), \
                unittest.mock.patch.object(emit, "ensure_pushed",
                                           fire("ensure_pushed")), \
                unittest.mock.patch.object(emit, "run_gates",
                                           fire("run_gates")), \
                unittest.mock.patch.object(Path, "read_text", counting_read):
            yield

    OMITTED = object()      # the option not given at all

    def _probe(self, body=None, claim_file=OMITTED):
        """Run both emitting verbs against `body` — claim text, raw bytes, or
        None for no file — with every sentinel armed. `claim_file` overrides
        the argument the verb receives, so the OPTION's own states (omitted,
        empty) are probed as well as the file's. Returns, per verb, either
        the blocked payload or the sentinel that fired."""
        import argparse
        import unittest.mock
        from review.cli import cmd_emit_request, cmd_handoff
        out = {}
        for verb in (cmd_handoff, cmd_emit_request):
            path = None
            if body is not None:
                path = self.tmp / f"{verb.__name__}.json"
                if isinstance(body, bytes):
                    path.write_bytes(body)
                else:
                    path.write_text(body, encoding="utf-8")
            value = str(path) if path is not None else None
            if claim_file is not self.OMITTED:
                value = claim_file
            args = argparse.Namespace(
                claim_file=value, base=None, head=None,
                local_only=False, out=None, ledger_dir=str(self.tmp))
            reads, buf = [], io.StringIO()
            with self._sentinels(path, reads):
                with contextlib.redirect_stdout(buf):
                    try:
                        code = verb(args, self.cfg)
                        fired = None
                    except ClaimSentinel as exc:
                        code, fired = None, str(exc)
            payload = None
            if fired is None:
                payload = json.loads(buf.getvalue()) if buf.getvalue() else {}
            out[verb.__name__] = {"code": code, "fired": fired,
                                  "payload": payload, "reads": len(reads)}
        return out

    def _assert_refused(self, body, case, **kw):
        """Refused as typed blocked recovery, by BOTH verbs, before every
        sentinel, having read the file at most once."""
        for verb, got in self._probe(body, **kw).items():
            self.assertIsNone(
                got["fired"],
                f"{case}: {verb} reached {got['fired']} before the claim was "
                f"judged")
            self.assertNotEqual(got["code"], 0, f"{case}: {verb} exit")
            payload = got["payload"]
            self.assertEqual(payload.get("next_kind"), "blocked",
                             f"{case}: {verb} {payload}")
            self.assertIsNone(payload.get("next"), f"{case}: {verb}")
            self.assertTrue(payload.get("error"), f"{case}: {verb} error")
            self.assertTrue(payload.get("remedy"), f"{case}: {verb} remedy")
            self.assertLessEqual(got["reads"], 1,
                                 f"{case}: {verb} read the claim "
                                 f"{got['reads']} times")

    def _assert_crosses(self, body, case, *, expect_reads, **kw):
        """A control: past the boundary, into the lifecycle, exactly one read
        of a supplied claim (none when no claim is supplied)."""
        for verb, got in self._probe(body, **kw).items():
            self.assertEqual(got["fired"], "_ledger",
                             f"{case}: {verb} did not cross the boundary "
                             f"({got['payload']})")
            self.assertEqual(got["reads"], expect_reads,
                             f"{case}: {verb} read the claim "
                             f"{got['reads']} times")

    # ------------------------------------------------------- the closed table

    def test_authority_is_covered(self):
        """The table cannot drift from the field authority: every kind the
        authority declares has a valid and a wrong recipe, and every member
        is exercised by the type cases below."""
        kinds = set(vocab.CLAIM_FIELDS.values())
        self.assertTrue(kinds <= set(self.VALID), f"no valid value: {kinds}")
        self.assertTrue(kinds <= set(self.WRONG), f"no wrong value: {kinds}")
        self.assertEqual(len(self.JSON_KINDS) - 1, 5,
                         "five non-object top-level kinds")
        self.assertTrue(set(vocab.CLAIM_REQUIRED) <= set(vocab.CLAIM_FIELDS))
        self.assertTrue(
            set(vocab.CLAIM_REFERENCE_REQUIRED)
            <= set(vocab.CLAIM_REFERENCE_FIELDS))
        # Round-8 F1: a nonempty constraint on a member that is not a string
        # field could never fire, and a nonempty member outside the authority
        # could never be reached. Either is a silently dead rule.
        self.assertTrue(set(vocab.CLAIM_NONEMPTY) <= set(vocab.CLAIM_FIELDS))
        for member in vocab.CLAIM_NONEMPTY:
            self.assertEqual(vocab.CLAIM_FIELDS[member], "string", member)

    def test_invalid_syntax_is_typed_recovery(self):
        for body in ('{"objective": "x"', "{bad}", "", "{'objective': 'x'}"):
            self._assert_refused(body, f"syntax {body!r}")

    def test_duplicate_members_at_any_depth(self):
        self._assert_refused(
            '{"objective": "x", "references": [{"path": "a.md"}], '
            '"references": []}', "duplicate top-level")
        self._assert_refused(
            '{"objective": "x", "references": [{"path": "a.md", '
            '"required": false, "required": true}]}', "duplicate nested")

    def test_every_non_object_top_level_kind(self):
        for kind, body in self.JSON_KINDS.items():
            if kind == "object":
                continue
            self._assert_refused(body, f"top-level {kind}")

    def test_unknown_members_are_refused(self):
        self._assert_refused('{"objective": "x", "stop_condition_typo": []}',
                             "unknown member")
        self._assert_refused(
            '{"objective": "x", "references": [{"path": "a.md", '
            '"requried": true}]}', "unknown nested member")

    def test_missing_required_members(self):
        for member in vocab.CLAIM_REQUIRED:
            body = {m: self.VALID[k] for m, k in vocab.CLAIM_FIELDS.items()
                    if m != member}
            self._assert_refused(json.dumps(body), f"missing {member}")
        for member in vocab.CLAIM_REFERENCE_REQUIRED:
            ref = {m: v for m, v in self.VALID["references"][0].items()
                   if m != member}
            self._assert_refused(
                json.dumps({"objective": "x", "references": [ref]}),
                f"reference missing {member}")

    def test_every_declared_field_rejects_every_wrong_type(self):
        seen = set()
        for member, kind in vocab.CLAIM_FIELDS.items():
            for wrong in self.WRONG[kind]:
                body = {"objective": "x", member: wrong}
                self._assert_refused(json.dumps(body),
                                     f"{member} = {wrong!r}")
            seen.add(member)
        self.assertEqual(seen, set(vocab.CLAIM_FIELDS),
                         "every declared member is exercised")

    def test_every_nested_reference_field_rejects_wrong_types(self):
        wrong_by_kind = {"string": [None, True, 3, [], {}],
                         "boolean": [None, 3, "yes", [], {}]}
        for member, kind in vocab.CLAIM_REFERENCE_FIELDS.items():
            self.assertIn(kind, wrong_by_kind, f"no recipe for {kind}")
            for wrong in wrong_by_kind[kind]:
                ref = dict(self.VALID["references"][0])
                ref[member] = wrong
                self._assert_refused(
                    json.dumps({"objective": "x", "references": [ref]}),
                    f"references[0].{member} = {wrong!r}")
        self._assert_refused(
            json.dumps({"objective": "x",
                        "references": [{"path": "   "}]}),
            "reference path blank")

    def test_every_nonempty_member_refuses_a_blank_value(self):
        """Round-8 F1: the table derived cases for kinds, missing members,
        wrong types and reference shape, but nothing from CLAIM_NONEMPTY —
        so making `_check_string` ignore `nonempty` for `objective` alone
        left all twelve legs green, and the exact rendering collapse the
        constraint exists to prevent could return under a passing test."""
        for member in vocab.CLAIM_NONEMPTY:
            for blank in ("", " ", "\t\n", "   "):
                body = {m: self.VALID[k]
                        for m, k in vocab.CLAIM_FIELDS.items()}
                body[member] = blank
                self._assert_refused(json.dumps(body),
                                     f"{member} = {blank!r}")
        # And the nonblank control still crosses.
        self._assert_crosses('{"objective": "a real objective"}',
                             "nonblank objective", expect_reads=1)

    def test_bytes_that_are_not_utf8_are_typed_recovery(self):
        """Round-8 F2: `read_text(encoding="utf-8")` was guarded by
        `except OSError`, and UnicodeDecodeError is not one — so a readable
        file of undecodable bytes escaped as a traceback: the one malformed
        input with no typed recovery, and the one exit that broke the
        structured-exit contract. The syntax table above writes decoded text
        and could never reach this state."""
        for case, raw in (("lone 0xff", b"\xff"),
                          ("utf-16 bom", b"\xff\xfe{\x00}\x00"),
                          ("truncated sequence", b'{"objective": "\xc3"}')):
            self._assert_refused(raw, f"undecodable: {case}")
        # Paired control: valid UTF-8, including non-ASCII, crosses.
        self._assert_crosses(
            json.dumps({"objective": "réalité — 日本語", "risk": "faible"},
                       ensure_ascii=False),
            "valid utf-8 non-ascii", expect_reads=1)

    def test_an_empty_claim_file_argument_is_not_an_omitted_one(self):
        """Round-8 F3: `Path(path) if path else None` folded an explicitly
        empty --claim-file into the no-claim state, so an author who supplied
        an empty path got the no-claim lifecycle, cache key and Git path —
        the supplied-versus-absent collapse arriving through the argument
        instead of through the file."""
        for case, value in (("empty string", ""), ("blank string", "   ")):
            self._assert_refused(None, f"--claim-file {case}",
                                 claim_file=value)
        # Paired control: the option omitted really is the no-claim state.
        self._assert_crosses(None, "omitted option", expect_reads=0,
                             claim_file=None)

    def test_unpaired_surrogates_are_refused_in_every_string_position(self):
        """Round-8 F4: JSON admits a lone escaped surrogate and the boundary
        checked `isinstance(value, str)`, so `{"objective": "\\ud800"}`
        crossed into the lifecycle — able to commit, push and run gates —
        while every surface that would carry it (envelope bytes, digest, kept
        file, stdout) is UTF-8 and cannot encode it. The domain of a claim
        string is the Unicode scalar values, not `str`.

        Derived from the string authorities: every top-level string field,
        every list-of-string element, and every string member of a
        reference."""
        LONE = "\\ud800"
        for member in vocab.CLAIM_STRING_FIELDS:
            self._assert_refused(
                '{"objective": "x", "%s": "%s"}' % (member, LONE)
                if member != "objective" else '{"objective": "%s"}' % LONE,
                f"surrogate in {member}")
        for member in vocab.CLAIM_LIST_FIELDS:
            self._assert_refused(
                '{"objective": "x", "%s": ["%s"]}' % (member, LONE),
                f"surrogate in {member}[0]")
        for member, kind in vocab.CLAIM_REFERENCE_FIELDS.items():
            if kind != "string":
                continue
            ref = dict(self.VALID["references"][0])
            ref[member] = "SURROGATE"
            body = json.dumps({"objective": "x", "references": [ref]})
            self._assert_refused(body.replace('"SURROGATE"', '"%s"' % LONE),
                                 f"surrogate in references[0].{member}")
        # Controls: ordinary non-ASCII, and a VALID surrogate pair, which is
        # one scalar value and must cross.
        self._assert_crosses('{"objective": "réalité"}', "non-ascii text",
                             expect_reads=1)
        self._assert_crosses('{"objective": "\\ud83d\\ude00"}',
                             "valid surrogate pair", expect_reads=1)

    def test_the_controls_cross_the_boundary(self):
        """No claim, the minimal valid claim, and one carrying every member
        the authority declares — each reaches the lifecycle, and a supplied
        claim is read exactly once."""
        self._assert_crosses(None, "no claim", expect_reads=0)
        self._assert_crosses('{"objective": "x"}', "minimal valid",
                             expect_reads=1)
        full = {m: self.VALID[k] for m, k in vocab.CLAIM_FIELDS.items()}
        self._assert_crosses(json.dumps(full), "full valid", expect_reads=1)

    def test_the_claim_is_read_once_all_the_way_to_git(self):
        """The controls above stop at the lifecycle. This one runs the whole
        handoff with only Git and a second read armed: the claim is read
        exactly once between the capture and `ensure_pushed`, which is the
        first thing that can commit and push."""
        import argparse
        import unittest.mock
        from review import emit
        from review.cli import cmd_handoff
        path = self.tmp / "deep.json"
        body = json.dumps({m: self.VALID[k]
                           for m, k in vocab.CLAIM_FIELDS.items()})
        path.write_text(body, encoding="utf-8")
        reads = []
        real_read = Path.read_text

        def counting_read(self_path, *a, **k):
            if Path(self_path) == path:
                reads.append(str(self_path))
            return real_read(self_path, *a, **k)

        def fire(name):
            def go(*a, **k):
                raise ClaimSentinel(name)
            return go

        args = argparse.Namespace(claim_file=str(path), base=None, head=None,
                                  local_only=False, out=None,
                                  ledger_dir=str(self.tmp))
        with unittest.mock.patch.object(emit, "ensure_pushed",
                                        fire("ensure_pushed")), \
                unittest.mock.patch.object(emit, "load_claim",
                                           fire("second read")), \
                unittest.mock.patch.object(Path, "read_text", counting_read):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(ClaimSentinel) as ctx:
                    cmd_handoff(args, self.cfg)
        self.assertEqual(str(ctx.exception), "ensure_pushed",
                         "the handoff must reach Git through the captured "
                         "claim, not through a second read")
        self.assertEqual(len(reads), 1,
                         f"the claim path was read {len(reads)} times")

    def test_no_optional_capture_channel_remains(self):
        """`_emit` takes the captured claim as a required parameter.

        This is the shape of the round-7 collision, closed at the source
        rather than at an input: while `_emit` had `claim=None` meaning "not
        captured — load it yourself", any value that parsed to None collided
        with the sentinel and the path was reopened. The schema now refuses
        a top-level `null` before emission, so no INPUT can reach that branch
        any more — which is exactly why the channel itself has to be gone,
        and why this assertion is on the signature and not on a claim file.
        """
        import inspect
        from review import cli, emit
        params = inspect.signature(cli._emit).parameters
        # All three authored inputs — the captured claim, the resolved roles
        # (§4) and the resolved transport (RVW-T11) — are required
        # parameters: an omitted capture is a capture nobody made, and an
        # omitted resolution would reopen a derive-it-yourself channel here
        # exactly as `claim=None` once did for the claim. The transport joins
        # them for the same reason and one more: it is part of the handoff
        # cache's key, so a value re-derived here could differ from the one
        # the cache was consulted with.
        for boundary in list(params.values())[-3:]:
            self.assertIs(boundary.default, inspect.Parameter.empty,
                          f"{boundary.name} is optional again: an omitted "
                          f"capture is a capture nobody made")
        self.assertEqual(len(params), 6, f"unexpected _emit signature "
                                         f"{list(params)}")
        # And no admitted claim captures as a non-mapping, so nothing
        # downstream can test the value for None and reopen the file.
        path = self.tmp / "shape.json"
        for body in ('{"objective": "x"}',
                     json.dumps({m: self.VALID[k]
                                 for m, k in vocab.CLAIM_FIELDS.items()})):
            path.write_text(body, encoding="utf-8")
            claim = emit.capture_claim(path).claim
            self.assertIsInstance(claim, Mapping, body)
        self.assertIsInstance(emit.capture_claim(None).claim, Mapping)

    def test_the_captured_claim_is_one_immutable_value(self):
        """The capture is a frozen record of ONE read: digest and value from
        the same bytes, `supplied` carrying presence instead of truthiness,
        and neither the record nor the claim editable afterwards."""
        from review import emit
        path = self.tmp / "c.json"
        body = '{"objective": "x", "references": [{"path": "a.md"}]}'
        path.write_text(body, encoding="utf-8")
        captured = emit.capture_claim(path)
        self.assertEqual(captured.digest, sha256_text(body))
        self.assertTrue(captured.supplied)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            captured.digest = "x"
        with self.assertRaises(TypeError):
            captured.claim["objective"] = "y"
        with self.assertRaises(TypeError):
            captured.claim["references"][0]["path"] = "y"
        none = emit.capture_claim(None)
        self.assertFalse(none.supplied)
        self.assertEqual(none.digest, vocab.NO_CLAIM)
        self.assertEqual(dict(none.claim), {})


if __name__ == "__main__":
    unittest.main()

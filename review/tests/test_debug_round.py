"""The debug round (2026-08-31): `--debug` stamps the request with
`debug="tool-feedback"`, the reviewer's procedure asks for a
`## tool feedback` verdict section — a self-critique of the TOOL's
performance that round (efficiency, cost, accuracy) — and close records
it as a `tool_feedback` ledger event so the critiques accumulate.

The section is legal on ANY verdict (a reviewer may always volunteer
it); the stamp is what asks for it, and nothing gates on it: advisory by
construction, per the 2026-08-30 prose-guards ruling. The stamp is part
of the emission cache's key under the same rule as the claim, the roles
and the transport — a warm copy stamped one way must not answer an
invocation that asked the other.
"""
import dataclasses
import re
import tempfile
import unittest
from pathlib import Path

from review import emit, transport, validate, vocab, wire
from review.ledger import Ledger
from review.tests.synth import (CFG, CLAIM, NO_GATES, head_sha,
                                reachability, shadow_ledger)


def _stamped(debug: bool) -> str:
    head = head_sha()
    return emit.emit_request(NO_GATES, shadow_ledger(), CLAIM,
                             base="HEAD", head="HEAD",
                             reachability=reachability(head), debug=debug)


VERDICT_WITH_FEEDBACK = """VERDICT: changes requested

## findings

### F1
Severity: High
Classification: design_gap
Title: a finding
Evidence: e
Why: w
Required outcome: r
FALSIFICATION: run x
Anchor: a

## evidence checked

- read the diff

## tool feedback

- efficiency: the take re-read two references the digest already bound
- cost: the envelope carried 24k bytes for a 10-line diff
- accuracy: the gate log pointer named a directory that did not exist
"""


class TestStamp(unittest.TestCase):

    def test_debug_stamps_and_default_does_not(self):
        stamped = _stamped(True)
        attrs = wire.parse_request(stamped).attrs
        self.assertEqual(attrs.get(vocab.DEBUG_ATTR),
                         vocab.DEBUG_TOOL_FEEDBACK)
        # Paired control: an ordinary emission carries no stamp.
        plain = wire.parse_request(_stamped(False)).attrs
        self.assertNotIn(vocab.DEBUG_ATTR, plain)

    def test_a_stamped_request_still_validates(self):
        parsed = wire.parse_request(_stamped(True))
        items = validate.validate_request(parsed, NO_GATES)
        self.assertEqual([i.code for i in items if i.level == "error"], [])


class TestVerdictSection(unittest.TestCase):

    def _wrap(self, body, sha="0" * 40):
        return (f'<loupe-review-verdict sha="{sha}">\n{body}'
                f"</loupe-review-verdict>\n")

    def test_the_feedback_section_is_legal_on_a_verdict(self):
        v = wire.parse_verdict(self._wrap(VERDICT_WITH_FEEDBACK))
        items = validate.validate_verdict(v, CFG)
        self.assertEqual([i.code for i in items if i.level == "error"], [],
                         items)

    def test_close_records_the_feedback_and_only_when_present(self):
        v = wire.parse_verdict(self._wrap(VERDICT_WITH_FEEDBACK))
        events = transport.verdict_events(v, 1, "d" * 64, 10)
        feedback = [e for e in events if e["event"] == "tool_feedback"]
        self.assertEqual(len(feedback), 1)
        self.assertEqual(feedback[0]["round"], 1)
        self.assertIn("efficiency", feedback[0]["text"])
        # Paired control: no section, no event — counts stay what every
        # existing close asserts.
        head, _, _ = VERDICT_WITH_FEEDBACK.partition("## tool feedback")
        plain = wire.parse_verdict(self._wrap(head))
        self.assertEqual(
            [e for e in transport.verdict_events(plain, 1, "d" * 64, 10)
             if e["event"] == "tool_feedback"], [])


class TestCacheKey(unittest.TestCase):
    """Mirrors TestCachedHandoff's warm scaffold: the stamp joins the
    claim, the roles and the transport in the key."""

    def _warm(self, text, debug):
        from review.tests.test_transport import (SHA_B, authority_calls,
                                                 fake_git)
        try:
            tmp = Path(tempfile.mkdtemp(prefix="debug-cache-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        cfg = dataclasses.replace(CFG, ledger_dir=tmp)
        transport.keep_bytes(cfg, 1, "request", text)
        git = fake_git({("rev-parse", "HEAD"): SHA_B,
                        ("status", "--porcelain"): "",
                        **authority_calls()})
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": SHA_B,
                    "source_digest": transport._digest_text(text),
                    "bytes": len(text),
                    "claim_digest": transport.NO_CLAIM})
        return transport.cached_handoff(cfg, ledger, 1, git=git,
                                        claim_digest=transport.NO_CLAIM,
                                        debug=debug)

    def _texts(self):
        from review.tests.test_transport import request_text
        from review.tests.test_transport import tool_identity
        plain = request_text(tool_attr=tool_identity())
        stamped = re.sub(
            r"^(<[^>\n]*-review-request [^>\n]*)>",
            rf'\1 {vocab.DEBUG_ATTR}="{vocab.DEBUG_TOOL_FEEDBACK}">',
            plain, count=1, flags=re.M)
        self.assertNotEqual(plain, stamped)
        return plain, stamped

    def test_matching_stamp_is_warm_and_mismatch_is_cold(self):
        plain, stamped = self._texts()
        self.assertIsNotNone(self._warm(plain, debug=False))
        self.assertIsNone(self._warm(plain, debug=True),
                          "a plain kept copy answered a debug invocation")

    def test_a_stamped_copy_answers_only_a_debug_invocation(self):
        _, stamped = self._texts()
        self.assertIsNotNone(self._warm(stamped, debug=True))
        self.assertIsNone(self._warm(stamped, debug=False),
                          "a debug-stamped kept copy answered a plain "
                          "invocation")


if __name__ == "__main__":
    unittest.main()

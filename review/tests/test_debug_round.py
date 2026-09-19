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
import unittest

from review import emit, transport, validate, vocab, wire
from review.tests.synth import (CFG, CLAIM, NO_GATES, head_sha,
                                reachability, shadow_ledger)
from review.tests.util import LINEAGE


def _stamped(debug: bool) -> str:
    head = head_sha()
    return emit.emit_request(NO_GATES, shadow_ledger(), CLAIM,
                             base="HEAD", head="HEAD",
                             reachability=reachability(head), debug=debug, lineage=LINEAGE)


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


class TestResolution(unittest.TestCase):
    """Flag > `[roles] debug` > off (decided 2026-08-31): a default that
    depends on whoever runs `handoff` remembering a flag is not a
    default. The transport precedence, cut to this value's three
    states."""

    def _cfg(self, declared):
        # CFG mirrors this repository's own config, which DECLARES debug —
        # start each case from the undeclared state.
        roles = dict(CFG.roles)
        roles.pop("debug", None)
        if declared is not None:
            roles["debug"] = declared
        return dataclasses.replace(CFG, roles=roles)

    def test_the_flag_wins_in_both_directions(self):
        self.assertTrue(emit.resolve_debug(self._cfg(False), True))
        self.assertFalse(emit.resolve_debug(self._cfg(True), False))

    def test_the_declaration_reaches_a_flagless_invocation(self):
        self.assertTrue(emit.resolve_debug(self._cfg(True), None))
        # Paired control: an explicit false declaration is off, not silence.
        self.assertFalse(emit.resolve_debug(self._cfg(False), None))

    def test_undeclared_and_flagless_is_off(self):
        self.assertFalse(emit.resolve_debug(self._cfg(None), None))

    def test_a_non_boolean_declaration_is_refused_at_the_boundary(self):
        from review import config
        with self.assertRaises(config.ConfigError) as ctx:
            config.from_text("[roles]\ndebug = \"yes\"\n",
                             like=CFG, source="review.toml")
        self.assertIn("debug", str(ctx.exception))


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
        from review.tests._transport_fixtures import warm_cache_fixture
        return warm_cache_fixture(self, text, prefix="debug-cache-").cached(
            debug=debug)

    def _texts(self):
        from review.tests._transport_fixtures import request_text
        from review import tool_identity
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

"""Fingerprint identity — the amended, reviewer-ratified test (§5.3b, R1-F5).

The claim under test is SYNTACTIC identity: verbatim and near-verbatim repeats
collide; moved lines and renames retain identity via explicit lineage events;
paraphrase equivalence is explicitly NOT claimed and NOT tested for.
"""
import unittest

from review.fingerprint import (FP_VERSION, compute, normalize_claim,
                                resolve_identity)


def normalize_text(text, anchor=""):
    return normalize_claim(text, anchor)


class TestNormalization(unittest.TestCase):
    def test_citation_line_numbers_are_excluded(self):
        # Line numbers move (§5.3b). `design:N` counts as a citation only
        # when `design` is the finding's own anchor — see F13 below.
        self.assertEqual(normalize_text("see design:102 for the rule", "design"),
                         normalize_text("see design:117 for the rule", "design"))
        self.assertEqual(normalize_text("preflight.py:150 blocks BEHIND"),
                         normalize_text("preflight.py:23 blocks BEHIND"))
        self.assertEqual(normalize_text("docs/agents.md:17 says so"),
                         normalize_text("docs/agents.md:9 says so"))

    def test_citation_line_ranges_are_excluded(self):
        self.assertEqual(
            normalize_text("lines design:139-146 assign P1", "design"),
            normalize_text("lines design:763-785 assign P1", "design"))

    def test_non_citation_numbers_survive(self):
        # Round-3 F13: `timeout:30` and `timeout:60` are distinct claims.
        # Nothing in the token's shape distinguishes it from a citation, so
        # only a path separator, a file extension, or the finding's own
        # anchor licenses stripping.
        self.assertNotEqual(normalize_text("timeout:30 is unsafe"),
                            normalize_text("timeout:60 is unsafe"))
        self.assertNotEqual(normalize_text("bind port:8080"),
                            normalize_text("bind port:9090"))

    def test_whitespace_case_and_markup_are_normalized(self):
        self.assertEqual(
            normalize_text("The  `bypass_actors`   mapping is WRONG"),
            normalize_text("the bypass_actors mapping is wrong"))

    def test_curly_quotes_normalize_to_straight(self):
        self.assertEqual(normalize_text("the “loose” default"),
                         normalize_text('the "loose" default'))

    def test_meaningful_identifiers_survive(self):
        # Underscores are identity, not markup: no_progress != noprogress.
        self.assertNotEqual(normalize_text("the no_progress breaker"),
                            normalize_text("the noprogress breaker"))


class TestIdentity(unittest.TestCase):
    def test_verbatim_repeat_collides(self):
        a = compute("design_gap", "design", "", "The ledger has no owner")
        b = compute("design_gap", "design", "", "The ledger has no owner")
        self.assertEqual(a, b)

    def test_moved_lines_retain_identity(self):
        # Same claim, cited at different line numbers: one fingerprint.
        a = compute("factual_error", "preflight.py:150", "",
                    "P2 rejects BEHIND at preflight.py:150")
        b = compute("factual_error", "preflight.py:9", "",
                    "P2 rejects BEHIND at preflight.py:9")
        self.assertEqual(a, b)

    def test_distinct_claims_at_one_anchor_do_not_collide(self):
        a = compute("internal_contradiction", "codex-AGENTS.md", "",
                    "the draft removed merger authority")
        b = compute("internal_contradiction", "codex-AGENTS.md", "",
                    "CLI flags outrank repo role configuration")
        self.assertNotEqual(a, b)

    def test_rename_changes_fingerprint_without_lineage(self):
        # A rename IS a new fingerprint (§5.3b) — identity is carried by an
        # explicit lineage event, never guessed by the hash.
        a = compute("design_gap", "docs/agents.md", "", "same claim")
        b = compute("design_gap", "docs/agents-v2.md", "",
                    "same claim")
        self.assertNotEqual(a, b)
        lineage = [{"kind": "rename", "from_fp": a, "to_fp": b}]
        self.assertEqual(resolve_identity(a, lineage),
                         resolve_identity(b, lineage))

    def test_anchor_change_carried_by_lineage(self):
        a = compute("design_gap", "design", "old-section", "claim text")
        b = compute("design_gap", "design", "new-section", "claim text")
        self.assertNotEqual(a, b)
        lineage = [{"kind": "anchor_change", "from_fp": a, "to_fp": b}]
        self.assertEqual(resolve_identity(a, lineage), b)

    def test_split_does_not_merge_identities(self):
        parent = compute("design_gap", "design", "", "bundled parent finding")
        child = compute("design_gap", "design", "", "atomic child claim")
        lineage = [{"kind": "split", "from_fp": parent, "to_fp": child}]
        self.assertNotEqual(resolve_identity(parent, lineage),
                            resolve_identity(child, lineage))

    def test_invariant_key_outranks_text_hash(self):
        # Round-3 F1: where a stable invariant key exists it OUTRANKS the
        # text hash, so rewording does not change identity (§5.3b). This is
        # the one identity that survives paraphrase — deterministically,
        # because the claim text is excluded from the hash entirely.
        a = compute("design_gap", "auth/token.py", "",
                    "token validation skips expiry", invariant_id="INV-AUTH-7")
        b = compute("design_gap", "auth/token.py", "",
                    "expiry is not checked during token validation",
                    invariant_id="INV-AUTH-7")
        self.assertEqual(a, b)

    def test_different_invariants_do_not_collide(self):
        a = compute("design_gap", "auth/token.py", "", "same words",
                    invariant_id="INV-AUTH-7")
        b = compute("design_gap", "auth/token.py", "", "same words",
                    invariant_id="INV-AUTH-8")
        self.assertNotEqual(a, b)

    def test_semantic_repeat_never_merges_identity(self):
        # Without an invariant key, paraphrase equivalence is a reviewer
        # judgment recorded as lineage — never a deterministic property.
        a = compute("design_gap", "x", "", "token validation skips expiry")
        b = compute("design_gap", "x", "", "expiry is not checked")
        self.assertNotEqual(a, b)
        lineage = [{"kind": "semantic_repeat", "from_fp": a, "to_fp": b}]
        self.assertNotEqual(resolve_identity(a, lineage), b,
                            "semantic_repeat records a judgment; it must not "
                            "silently merge deterministic identity")

    def test_algorithm_is_versioned(self):
        fp = compute("design_gap", "x", "", "claim")
        self.assertTrue(fp.startswith(f"{FP_VERSION}:"))

    def test_version_change_is_carried_by_alias_lineage(self):
        # A normalization change must be visible, not silently re-identify
        # history (§5.3b): the old id resolves to the new one via an alias.
        from review.fingerprint import alias_event, legacy_v1
        old = legacy_v1("design_gap", "design", "", "a claim")
        new = compute("design_gap", "design", "", "a claim")
        self.assertNotEqual(old, new)
        self.assertEqual(resolve_identity(old, [alias_event(old, new)]), new)


if __name__ == "__main__":
    unittest.main()

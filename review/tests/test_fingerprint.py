"""Fingerprint identity — the amended, reviewer-ratified test (§5.3b, R1-F5).

The claim under test is SYNTACTIC identity: verbatim and near-verbatim repeats
collide; moved lines and renames retain identity via explicit lineage events;
paraphrase equivalence is explicitly NOT claimed and NOT tested for.
"""
import unittest

from review import wire
from review.fingerprint import (FP_VERSION, LineageError, compute,
                                normalize_claim, resolve_identity)


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


class TestF8StructuredAnchor(unittest.TestCase):
    """Round-3 F8 — FALSIFICATION: Corpus and mutation tests exercise stable
    structured anchors, anchor changes with lineage, and distinct symbols in
    one path without collisions.

    Through the parsed `Anchor:` field, which is what TestIdentity's direct
    `compute` calls never touch; the lineage half is
    `TestIdentity.test_anchor_change_carried_by_lineage`."""

    def _finding(self, anchor, title="a claim"):
        block = (f"### F1\nSeverity: High\nClassification: design_gap\n"
                 f"Title: {title}\nEvidence: auth/token.py:44\n"
                 f"{f'Anchor: {anchor}' if anchor else ''}\n"
                 f"Why: w\nRequired outcome: r\nFALSIFICATION: f\n")
        return wire.parse_findings(block)[0]

    def test_distinct_symbols_in_one_path_do_not_collide(self):
        a = self._finding("validate_token", "expiry is skipped")
        b = self._finding("refresh_token", "expiry is skipped")
        self.assertNotEqual(a.fingerprint(), b.fingerprint())

    def test_anchor_is_stable_across_moved_lines(self):
        a = self._finding("validate_token")
        b = self._finding("validate_token")
        b.evidence = "auth/token.py:900"
        self.assertEqual(a.fingerprint(), b.fingerprint())

    def test_absent_anchor_is_a_recorded_state_not_a_failure(self):
        self.assertEqual(self._finding("").anchor_source,
                         "derived-from-evidence")
        self.assertEqual(self._finding("validate_token").anchor_source,
                         "declared")


class TestF6LineageFailsClosed(unittest.TestCase):
    """Round-4 F6 — FALSIFICATION: Every member of an accepted lineage
    component resolves identically; cycles and multiple outgoing targets fail
    closed with a typed error."""

    def test_every_member_of_a_chain_resolves_identically(self):
        chain = [{"kind": "alias", "from_fp": "fp1:a", "to_fp": "fp2:b"},
                 {"kind": "rename", "from_fp": "fp2:b", "to_fp": "fp2:c"}]
        ends = {resolve_identity(fp, chain) for fp in ("fp1:a", "fp2:b",
                                                       "fp2:c")}
        self.assertEqual(ends, {"fp2:c"})

    def test_a_cycle_fails_closed(self):
        cycle = [{"kind": "alias", "from_fp": "A", "to_fp": "B"},
                 {"kind": "alias", "from_fp": "B", "to_fp": "A"}]
        # The defect: A resolved to A and B resolved to B, so one malformed
        # component silently became two identities.
        for start in ("A", "B"):
            with self.subTest(start=start):
                with self.assertRaises(LineageError):
                    resolve_identity(start, cycle)

    def test_a_self_edge_fails_closed(self):
        with self.assertRaises(LineageError):
            resolve_identity("A", [{"kind": "alias", "from_fp": "A",
                                    "to_fp": "A"}])

    def test_two_outgoing_merge_targets_fail_closed(self):
        with self.assertRaises(LineageError):
            resolve_identity("A", [{"kind": "alias", "from_fp": "A",
                                    "to_fp": "B"},
                                   {"kind": "rename", "from_fp": "A",
                                    "to_fp": "C"}])

    def test_a_split_parent_with_many_children_is_well_formed(self):
        # `split` forks a parent into distinct children and does NOT merge, so
        # five outgoing split edges are correct, not a conflict. The live
        # ledger has exactly this shape.
        splits = [{"kind": "split", "from_fp": "A", "to_fp": f"C{i}"}
                  for i in range(5)]
        self.assertEqual(resolve_identity("A", splits), "A")

    def test_the_error_is_typed_and_routes_through_the_next_command(self):
        self.assertIsInstance(LineageError("x"), ValueError)


class TestF7DeclaredCitationsDoNotSplit(unittest.TestCase):
    """Round-4 F7 (and the round-3 F13 it sustained) — FALSIFICATION: Moved
    line numbers for primary and secondary structured citations normalize
    identically, while `timeout:30/60` and `port:8080/9090` remain
    distinct."""

    def _fp(self, claim, citations=""):
        return compute("design_gap", "code.py", "", claim,
                       citations=citations)

    def test_secondary_extensionless_citations_survive_a_moved_line(self):
        # The round-4 probe verbatim: primary path code.py, secondary
        # citations README and design, which have no extension and no
        # separator and so were invisible to inference.
        for target in ("README", "design", "RFC"):
            with self.subTest(citation=target):
                a = self._fp(f"{target}:10 says the contract", target)
                b = self._fp(f"{target}:20 says the contract", target)
                self.assertEqual(a, b)

    def test_primary_citations_still_survive_a_moved_line(self):
        a = compute("design_gap", "preflight.py", "",
                    "P2 rejects BEHIND at preflight.py:150")
        b = compute("design_gap", "preflight.py", "",
                    "P2 rejects BEHIND at preflight.py:9")
        self.assertEqual(a, b)

    def test_semantic_literals_remain_distinct(self):
        self.assertNotEqual(self._fp("timeout:30 is unsafe"),
                            self._fp("timeout:60 is unsafe"))
        self.assertNotEqual(self._fp("port:8080 is wrong"),
                            self._fp("port:9090 is wrong"))

    def test_declaring_a_citation_does_not_capture_unrelated_literals(self):
        # Declaring `design` must not make `timeout:30` a citation too.
        self.assertNotEqual(self._fp("design:10 and timeout:30", "design"),
                            self._fp("design:10 and timeout:60", "design"))

    def test_declaring_citations_does_not_disable_derivation(self):
        # Additive, not exclusive: a reviewer that declares `design` must not
        # have to re-declare every path it also cites.
        a = self._fp("design:10 and review/wire.py:212", "design")
        b = self._fp("design:99 and review/wire.py:900", "design")
        self.assertEqual(a, b)

    def test_absent_declaration_is_a_recorded_state(self):
        block = ("### F1\nSeverity: High\nClassification: design_gap\n"
                 "Title: t\nEvidence: code.py:1\nCitations: design, README\n"
                 "Why: w\nRequired outcome: r\nFALSIFICATION: f\n")
        declared = wire.parse_findings(block)[0]
        derived = wire.parse_findings(
            block.replace("Citations: design, README\n", ""))[0]
        self.assertEqual(declared.citation_source, "declared")
        self.assertEqual(derived.citation_source, "derived-from-shape")


if __name__ == "__main__":
    unittest.main()

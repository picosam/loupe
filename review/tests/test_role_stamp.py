"""Per-invocation role stamp (design §4, shipped 2026-08-22).

The published specification has stated the precedence since the first
extraction — the repo config says what assignments are PERMITTED, a
per-invocation stamp selects WITHIN that permission, unassigned refuses —
and the CLI never shipped the selection layer; the first per-project
onboarding hit the absence. These tests close the admitted domain of the
new input on both sides it touches: the resolution boundary
(`emit.resolve_roles`), the stamp the emitter writes, and the handoff
cache, whose warm-key the effective stamp joins.

Domain of one side's flag: absent · empty string · a permitted member (any
case) · a non-member · a member of a repo that declares no permitted list ·
a rejected identity (reviewer) · the identity the other side resolves to.
Each state has its own test; the valid controls prove the checks are live.
"""
from __future__ import annotations

import dataclasses
import unittest
from pathlib import Path

from review import emit, transport, wire
from review.ledger import Ledger
from review.tests.synth import (CFG, NO_GATES, emitted_request, head_sha,
                                reachability, shadow_ledger)

import tempfile


def cfg_with_roles(**over):
    roles = {"author": "claude", "reviewer": "codex",
             "permitted_authors": ["claude", "codex"],
             "permitted_reviewers": ["claude", "codex"],
             "rejected_reviewers": ["gemini", "antigravity"],
             "relay": "user"}
    roles.update(over)
    return dataclasses.replace(CFG, roles=roles)


class TestResolveRoles(unittest.TestCase):

    def test_no_flags_resolve_to_the_config_default(self):
        self.assertEqual(emit.resolve_roles(cfg_with_roles()),
                         ("claude", "codex"))

    def test_the_unassigned_cross_product_refuses_at_resolution(self):
        """Round-2 F2. The first version returned ('', '') here — with a
        comment calling the emitter the authority for the state — which
        froze the defect as intended behaviour: the ledger and the
        commit/push lifecycle answered to an invocation with no valid
        actor pair, downstream of the boundary that promised to precede
        them. Empty is a member of the domain; every row of the missing-
        side cross-product refuses at resolution."""
        for over in ({"author": ""}, {"reviewer": ""},
                     {"author": "", "reviewer": ""}):
            with self.subTest(unassigned=sorted(over)):
                with self.assertRaises(emit.RoleSelectionError) as ctx:
                    emit.resolve_roles(cfg_with_roles(**over))
                self.assertIn("no", str(ctx.exception))
                self.assertIn("at all", str(ctx.exception))

    def test_a_malformed_permitted_list_cannot_admit_emptiness(self):
        # The adjacent explicit route from the round-2 probe: permitted
        # list carrying "", flag "". Membership passes; the non-empty
        # invariant still refuses, naming the flag as the source.
        cfg = cfg_with_roles(permitted_authors=[""])
        with self.assertRaises(emit.RoleSelectionError) as ctx:
            emit.resolve_roles(cfg, author="")
        self.assertIn("--author", str(ctx.exception))
        cfg2 = cfg_with_roles(permitted_reviewers=["", "codex"])
        with self.assertRaises(emit.RoleSelectionError):
            emit.resolve_roles(cfg2, reviewer="")

    def test_a_permitted_member_is_selected_as_given(self):
        cfg = cfg_with_roles()
        self.assertEqual(emit.resolve_roles(cfg, author="codex",
                                            reviewer="claude"),
                         ("codex", "claude"))

    def test_membership_is_case_insensitive_like_the_validator(self):
        cfg = cfg_with_roles()
        author, reviewer = emit.resolve_roles(cfg, author="Codex",
                                              reviewer="Claude")
        self.assertEqual((author, reviewer), ("Codex", "Claude"))

    def test_a_non_member_is_refused_naming_the_list(self):
        cfg = cfg_with_roles()
        with self.assertRaises(emit.RoleSelectionError) as ctx:
            emit.resolve_roles(cfg, author="gpt")
        self.assertIn("permitted_authors", str(ctx.exception))
        self.assertTrue(ctx.exception.remedy)

    def test_an_empty_flag_is_a_selection_not_an_absence(self):
        # The repo rule, fourth file it has bitten: an explicit empty value
        # is a state. `--author ""` selects the empty identity, which no
        # list carries.
        cfg = cfg_with_roles()
        with self.assertRaises(emit.RoleSelectionError):
            emit.resolve_roles(cfg, author="")

    def test_a_flag_against_no_declared_list_is_refused(self):
        # With nothing declared there is no permission to select within —
        # the flag must not widen silence into "anything goes".
        cfg = cfg_with_roles(permitted_authors=[])
        with self.assertRaises(emit.RoleSelectionError) as ctx:
            emit.resolve_roles(cfg, author="claude")
        self.assertIn("no permitted_authors", str(ctx.exception))
        # Even when the flag names the identity the config defaults to:
        # the license is the declared list, not the value's harmlessness.
        cfg2 = cfg_with_roles(permitted_authors=[])
        with self.assertRaises(emit.RoleSelectionError):
            emit.resolve_roles(cfg2, author="claude")

    def test_a_rejected_reviewer_is_refused_whatever_list_carries_it(self):
        cfg = cfg_with_roles(
            permitted_reviewers=["claude", "codex", "gemini"])
        with self.assertRaises(emit.RoleSelectionError) as ctx:
            emit.resolve_roles(cfg, reviewer="gemini")
        self.assertIn("rejected", str(ctx.exception))

    def test_flag_induced_self_review_is_refused(self):
        cfg = cfg_with_roles()
        with self.assertRaises(emit.RoleSelectionError) as ctx:
            emit.resolve_roles(cfg, author="codex")   # reviewer defaults codex
        self.assertIn("own diff", str(ctx.exception))

    def test_config_default_self_review_is_refused_before_emission(self):
        # Invalid in every envelope (R-SELF-REVIEW); refusing here is
        # cheaper than emitting one the validator throws away.
        cfg = cfg_with_roles(author="codex", reviewer="codex")
        with self.assertRaises(emit.RoleSelectionError):
            emit.resolve_roles(cfg)

    def test_self_review_comparison_is_case_insensitive(self):
        cfg = cfg_with_roles()
        with self.assertRaises(emit.RoleSelectionError):
            emit.resolve_roles(cfg, author="Codex", reviewer="codex")


class TestSelectorGrammar(unittest.TestCase):
    """Round-1 F1, the raw-option half. argparse's ordinary store collapses
    a repeated option to its last value, so `--author claude --author
    codex` was admitted as the unambiguous stamp `codex` and neither the
    repetition nor the discarded actor ever reached resolution. A repeated
    selector — same value or conflicting, either order, either side, on
    both emitting verbs — is refused naming both occurrences; the single
    occurrence is the paired valid control."""

    def _parse(self, *argv):
        from review import cli
        return cli.build_parser().parse_args(list(argv))

    def test_a_single_selection_parses(self):
        args = self._parse("handoff", "--author", "claude",
                           "--reviewer", "codex")
        self.assertEqual((args.author, args.reviewer), ("claude", "codex"))

    def test_no_selection_parses_as_none(self):
        args = self._parse("handoff")
        self.assertEqual((args.author, args.reviewer), (None, None))

    def test_repeated_selectors_are_refused_never_collapsed(self):
        from review import cli
        for argv in (
                ["handoff", "--author", "claude", "--author", "codex"],
                ["handoff", "--author", "codex", "--author", "claude"],
                ["handoff", "--author", "claude", "--author", "claude"],
                ["handoff", "--reviewer", "codex", "--reviewer", "claude"],
                ["handoff", "--author", "claude", "--reviewer", "codex",
                 "--author", "claude"],
                ["emit-request", "--author", "a", "--author", "b"],
                ["emit-request", "--reviewer", "a", "--reviewer", "a"]):
            with self.subTest(argv=argv):
                with self.assertRaises(cli.UsageError) as ctx:
                    self._parse(*argv)
                self.assertIn("more than once", str(ctx.exception))


class TestResolveRolesDefaultedSources(unittest.TestCase):
    """Round-1 F1, the other end of the absent/present domain: the
    invariants bind the EFFECTIVE identity, so a config-DEFAULTED rejected
    or unpermitted identity refuses at resolution exactly as a flagged one
    does — never downstream of the commit, the push and the gate run."""

    def test_a_defaulted_rejected_reviewer_is_refused(self):
        # The reviewer's round-1 probe: gemini both permitted and rejected,
        # arriving from the config default with no flag anywhere. It
        # resolved to ('claude', 'gemini') before the fix.
        cfg = cfg_with_roles(reviewer="gemini",
                             permitted_reviewers=["codex", "gemini"])
        with self.assertRaises(emit.RoleSelectionError) as ctx:
            emit.resolve_roles(cfg)
        self.assertIn("rejected", str(ctx.exception))
        self.assertIn("default", str(ctx.exception))

    def test_a_defaulted_unpermitted_author_is_refused(self):
        cfg = cfg_with_roles(author="gpt")
        with self.assertRaises(emit.RoleSelectionError) as ctx:
            emit.resolve_roles(cfg)
        self.assertIn("permitted_authors", str(ctx.exception))

    def test_a_defaulted_unpermitted_reviewer_is_refused(self):
        cfg = cfg_with_roles(reviewer="gpt")
        with self.assertRaises(emit.RoleSelectionError):
            emit.resolve_roles(cfg)

    def test_defaults_with_no_declared_lists_still_resolve(self):
        # No list declared restricts nothing for the DEFAULTED source —
        # that is the pre-flag tool, byte for byte; only a flag needs a
        # declared list to select within.
        cfg = cfg_with_roles(permitted_authors=[], permitted_reviewers=[])
        self.assertEqual(emit.resolve_roles(cfg), ("claude", "codex"))


class TestSelectionPrecedesEverySideEffect(unittest.TestCase):
    """Round-1 F1, the lifecycle half: a refused selection reaches no
    ledger, no Git, no gate run, no cache and no record. Every downstream
    side effect is a tripwire; the refusal must arrive first."""

    def _tripped(self, *a, **k):
        raise AssertionError("a side effect was reached past a refused "
                             "role selection")

    def _blocked_payload(self, verb, args, cfg, patches):
        import contextlib
        import io
        import json
        import unittest.mock as mock
        from review import cli
        buf = io.StringIO()
        with contextlib.ExitStack() as stack:
            for target, name in patches:
                stack.enter_context(
                    mock.patch.object(target, name, self._tripped))
            stack.enter_context(contextlib.redirect_stdout(buf))
            code = verb(args, cfg)
        self.assertNotEqual(code, 0)
        return json.loads(buf.getvalue())

    def test_handoff_refuses_before_any_side_effect(self):
        import argparse
        from review import cli
        args = argparse.Namespace(claim_file=None, base=None, head=None,
                                  local_only=False, out=None,
                                  ledger_dir=None, command="handoff",
                                  author="gpt", reviewer=None)
        payload = self._blocked_payload(
            cli.cmd_handoff, args, cfg_with_roles(),
            [(cli, "_ledger"), (cli.emit, "ensure_pushed"),
             (cli.emit, "run_gates"), (cli.transport, "cached_handoff"),
             (cli.transport, "record_handoff"),
             (cli.transport, "handoff_preflight")])
        self.assertEqual(payload["next_kind"], "blocked")
        self.assertIn("permitted_authors", payload["error"])

    def test_emit_request_refuses_before_any_side_effect(self):
        import argparse
        from review import cli
        args = argparse.Namespace(claim_file=None, base=None, head=None,
                                  local_only=False, out=None,
                                  ledger_dir=None, command="emit-request",
                                  author=None, reviewer="gemini")
        payload = self._blocked_payload(
            cli.cmd_emit_request, args,
            cfg_with_roles(permitted_reviewers=["codex", "gemini"]),
            [(cli, "_ledger"), (cli, "_emit"),
             (cli.emit, "ensure_pushed"), (cli.emit, "run_gates")])
        self.assertEqual(payload["next_kind"], "blocked")
        self.assertIn("rejected", payload["error"])

    def test_unassigned_defaults_refuse_before_any_side_effect(self):
        # Round-2 F2's tripwire, both verbs: no-default config, no flags —
        # the refusal must arrive before the ledger is even constructed.
        import argparse
        from review import cli
        for verb, command in ((cli.cmd_handoff, "handoff"),
                              (cli.cmd_emit_request, "emit-request")):
            with self.subTest(verb=command):
                args = argparse.Namespace(
                    claim_file=None, base=None, head=None, local_only=False,
                    out=None, ledger_dir=None, command=command,
                    author=None, reviewer=None)
                payload = self._blocked_payload(
                    verb, args, cfg_with_roles(author="", reviewer=""),
                    [(cli, "_ledger"), (cli, "_emit"),
                     (cli.emit, "ensure_pushed"), (cli.emit, "run_gates"),
                     (cli.transport, "cached_handoff"),
                     (cli.transport, "record_handoff"),
                     (cli.transport, "handoff_preflight")])
                self.assertEqual(payload["next_kind"], "blocked")
                self.assertIn("at all", payload["error"])


class TestEmittedStamp(unittest.TestCase):

    def test_the_default_stamp_is_unchanged_without_a_selection(self):
        parsed = wire.parse_request(emitted_request())
        self.assertEqual(parsed.attrs["author"], "claude")
        self.assertEqual(parsed.attrs["reviewer"], "codex")

    def test_a_selection_reaches_the_envelope_for_that_artifact_only(self):
        head = head_sha()
        envelope = emit.emit_request(
            NO_GATES, shadow_ledger(), {"objective": "role-stamp control"},
            base="HEAD", head="HEAD", reachability=reachability(head),
            author="codex", reviewer="claude")
        parsed = wire.parse_request(envelope)
        self.assertEqual(parsed.attrs["author"], "codex")
        self.assertEqual(parsed.attrs["reviewer"], "claude")
        # The config object is untouched: the override is per invocation,
        # never a mutation the next emission inherits.
        self.assertEqual(NO_GATES.roles["author"], "claude")
        parsed2 = wire.parse_request(emitted_request())
        self.assertEqual(parsed2.attrs["author"], "claude")


class TestCacheRolesKey(unittest.TestCase):
    """The effective stamp joins the warm-key: a kept envelope emitted
    under one direction must not answer an invocation that selected
    another."""

    def _warm_fixture(self):
        try:
            tmp = Path(tempfile.mkdtemp(prefix="handoff-roles-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        from review.tests.test_transport import fake_git, request_text, SHA_B
        cfg = dataclasses.replace(CFG, ledger_dir=tmp)
        text = request_text()          # stamped author=claude reviewer=codex
        transport.keep_bytes(cfg, 1, "request", text)
        git = fake_git({("rev-parse", "HEAD"): SHA_B,
                        ("status", "--porcelain"): ""})
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": SHA_B,
                    "source_digest": transport._digest_text(text),
                    "bytes": len(text),
                    "claim_digest": transport.NO_CLAIM})
        return cfg, ledger, git

    def test_same_roles_stay_warm_explicit_and_defaulted(self):
        cfg, ledger, git = self._warm_fixture()
        self.assertIsNotNone(transport.cached_handoff(
            cfg, ledger, 1, git=git, claim_digest=transport.NO_CLAIM,
            roles=("claude", "codex")))
        # None means "no selection", which resolves to the config default —
        # a derivable state, so legacy behaviour is unchanged.
        self.assertIsNotNone(transport.cached_handoff(
            cfg, ledger, 1, git=git, claim_digest=transport.NO_CLAIM))

    def test_a_different_selection_is_cold(self):
        cfg, ledger, git = self._warm_fixture()
        self.assertIsNone(transport.cached_handoff(
            cfg, ledger, 1, git=git, claim_digest=transport.NO_CLAIM,
            roles=("codex", "claude")))


if __name__ == "__main__":
    unittest.main()

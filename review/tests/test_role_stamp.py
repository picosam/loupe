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
from review.tests.util import REPO_ROOT
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


class TestTheTargetAuthorisesRolesNotTheCheckout(unittest.TestCase):
    """Round-1 F1's lifecycle property, as amended by lineage 12.

    Round 1 F1 established that a refused role selection reaches no ledger,
    no Git, no gate, no cache and no record — with the CHECKOUT deciding.
    Lineage 12 round 1 F2 then moved authorization to the committed
    authority, and round 2 F1 found the half that was left behind: the
    checkout's own permitted and rejected lists could still VETO an
    identity the target permits. That is the inverse defect in the same
    place, and it is worse than the original, because it refuses work that
    is valid.

    So the property inverts. A checkout that would refuse must NOT refuse:
    the decision belongs to the authority the reviewer will judge by, and
    before the commit exists there is no such authority to ask. What
    survives unchanged is the OTHER half — a target-rejected role still
    stops before the push and before any gate — and that is asserted
    end-to-end, against real repositories and both author doors, in
    `test_transport.TestTheAuthorDoorStampsTheCommittedRoles`.
    """

    def _reached(self, *a, **k):
        raise _ReachedEnsurePushed()

    def _run(self, verb, args, cfg):
        import contextlib
        import io
        import json
        import unittest.mock as mock
        from review import cli
        from review.ledger import Ledger
        buf = io.StringIO()
        reached = False
        with contextlib.ExitStack() as stack:
            # An in-memory ledger, so the LIFECYCLE preflight (undisposed
            # findings, fired breakers) cannot refuse first and mask the
            # property under test. This class is about role authority, not
            # about the lifecycle, which has its own tests.
            stack.enter_context(
                mock.patch.object(cli, "_ledger",
                                  lambda *a, **k: Ledger.in_memory()))
            stack.enter_context(
                mock.patch.object(cli.emit, "ensure_pushed", self._reached))
            stack.enter_context(contextlib.redirect_stdout(buf))
            try:
                code = verb(args, cfg)
            except _ReachedEnsurePushed:
                reached, code = True, None
        raw = buf.getvalue()
        return reached, code, (json.loads(raw) if raw.strip() else None)

    def _args(self, command, **over):
        import argparse
        base = dict(claim_file=None, base=None, head=None, local_only=False,
                    out=None, ledger_dir=None, command=command,
                    author=None, reviewer=None)
        base.update(over)
        return argparse.Namespace(**base)

    def test_a_checkout_that_forbids_the_flag_no_longer_vetoes(self):
        """FALSIFICATION for round 2 F1. The checkout declares
        `permitted_authors` without `gpt`; under the pre-fix code this
        refused at the CLI boundary with a `permitted_authors` message,
        before the target was ever consulted. It must now reach the
        committed-authority boundary, where the target rules."""
        from review import cli
        for verb, command in ((cli.cmd_handoff, "handoff"),
                              (cli.cmd_emit_request, "emit-request")):
            with self.subTest(verb=command):
                reached, code, payload = self._run(
                    verb, self._args(command, author="gpt"),
                    cfg_with_roles())
                self.assertTrue(
                    reached,
                    f"{command} refused on the CHECKOUT's permitted list; "
                    f"the target is the only authority entitled to rule "
                    f"(round 2 F1). payload={payload}")

    def test_a_checkout_that_rejects_the_reviewer_no_longer_vetoes(self):
        """The same inversion on `rejected_reviewers`."""
        from review import cli
        for verb, command in ((cli.cmd_handoff, "handoff"),
                              (cli.cmd_emit_request, "emit-request")):
            with self.subTest(verb=command):
                reached, code, payload = self._run(
                    verb, self._args(command, reviewer="gemini"),
                    cfg_with_roles(permitted_reviewers=["codex", "gemini"]),
                )
                self.assertTrue(
                    reached,
                    f"{command} refused on the CHECKOUT's rejected list; "
                    f"payload={payload}")

    def test_unassigned_checkout_defaults_no_longer_veto(self):
        """A checkout assigning no roles at all was round-2 F2's refusal.
        It is now the target's question too: a checkout is not evidence
        about what the reviewed commit declares, and a repository governed
        only by a user-level config legitimately assigns nothing here."""
        from review import cli
        for verb, command in ((cli.cmd_handoff, "handoff"),
                              (cli.cmd_emit_request, "emit-request")):
            with self.subTest(verb=command):
                reached, code, payload = self._run(
                    verb, self._args(command),
                    cfg_with_roles(author="", reviewer=""))
                self.assertTrue(
                    reached,
                    f"{command} refused on the CHECKOUT's empty defaults; "
                    f"payload={payload}")

    def test_the_target_refusal_still_reaches_the_human_as_blocked(self):
        """The refusal moved; its SHAPE did not. `RoleSelectionError` from
        the committed-authority boundary must still exit blocked, with the
        refusal's own remedy — round 7 F3's rule, at the new site."""
        import unittest.mock as mock
        from review import cli, emit

        def refuse(*a, **k):
            raise emit.RoleSelectionError(
                "the target does not permit 'gpt'",
                remedy="a person selects a permitted identity")

        for verb, command in ((cli.cmd_handoff, "handoff"),
                              (cli.cmd_emit_request, "emit-request")):
            with self.subTest(verb=command):
                import contextlib
                import io
                import json
                from review.ledger import Ledger
                buf = io.StringIO()
                with mock.patch.object(
                        cli, "_ledger",
                        lambda *a, **k: Ledger.in_memory()), \
                        mock.patch.object(cli.emit, "ensure_pushed", refuse):
                    with contextlib.redirect_stdout(buf):
                        code = verb(self._args(command, author="gpt"),
                                    cfg_with_roles())
                payload = json.loads(buf.getvalue())
                self.assertNotEqual(code, 0)
                self.assertEqual(payload["next_kind"], "blocked")
                self.assertIn("does not permit", payload["error"])
                self.assertIn("permitted identity", payload["remedy"])


class _ReachedEnsurePushed(Exception):
    """Raised by the tripwire: the invocation got as far as the
    committed-authority boundary, which is the property under test."""


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
        from review import tool_identity
        # Round 1 F2 put the tool identity in the warm key, so a fixture
        # that must REACH the check it is about stamps the current one;
        # the identity's own cold cases live in test_transport.
        text = request_text(tool_attr=tool_identity())
        transport.keep_bytes(cfg, 1, "request", text)
        # Round 2 F1: the warm path resolves the TARGET's authority to
        # decide the role key, so a scripted runner must answer it.
        git = fake_git({("rev-parse", "HEAD"): SHA_B,
                        ("status", "--porcelain"): "",
                        ("ls-tree", "--full-tree", SHA_B, "--",
                         "review.toml"):
                            "100644 blob " + "0" * 40 + "\treview.toml",
                        ("show", f"{SHA_B}:review.toml"):
                            (REPO_ROOT / "review.toml").read_text(
                                encoding="utf-8"),
                        })
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

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
import json
import re
import unittest
from pathlib import Path

from review import emit, wire
from review.ledger import Ledger
from review.tests._transport_fixtures import (run_cli, scratch_loop_repo, sh)
from review.tests.synth import (CFG, CLAIM, NO_GATES, emitted_request,
                                head_sha, reachability, shadow_ledger)


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
    `test_transport_lifecycle.TestTheAuthorDoorStampsTheCommittedRoles`.
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


class TestRelayRendersAsAnActorNeverAsAnInstruction(unittest.TestCase):
    """Round-3 F3. `relay` is rendered on the Roles line, not as a wrapper
    attribute (`wire.parse_request` never sees it), so these check the
    envelope BODY directly. The capture boundary (`test_validate.py`,
    `TestClaimGrammarClosedWorld`) proves a non-actor value is refused
    before an envelope like these ever exists; this proves what a value
    that DOES cross renders as."""

    def _emit(self, claim):
        head = head_sha()
        return emit.emit_request(NO_GATES, shadow_ledger(), claim,
                                 base="HEAD", head="HEAD",
                                 reachability=reachability(head))

    def test_an_explicit_actor_and_an_omitted_relay_render_the_same_line(self):
        explicit = self._emit({**CLAIM, "relay": "user"})
        omitted = self._emit(CLAIM)
        self.assertIn("relay=user", explicit)
        self.assertIn("relay=user", omitted)
        # And neither carries the claim's dict object itself — the value
        # that reaches the line is a rendered string either way.
        self.assertNotIn("relay=None", omitted)

    def test_a_hand_back_instruction_renders_under_its_own_heading(self):
        instruction = ("return only the generated brief and its exact "
                       "relay; do not start another round")
        envelope = self._emit({**CLAIM, "hand_back": [instruction]})
        self.assertIn("## Hand-back", envelope)
        after_heading = envelope.split("## Hand-back", 1)[1]
        self.assertIn(instruction, after_heading)
        # And it never reaches the Roles line — the two fields render in
        # different places for different readers (relay: an identity every
        # reader parses; hand_back: prose only a human acts on).
        roles_line = next(l for l in envelope.splitlines()
                          if l.startswith("Roles:"))
        self.assertNotIn(instruction, roles_line)

    def _set_config_relay(self, repo: Path, value: str) -> None:
        """Overwrite the scratch repo's own `[roles] relay` (copied from
        this repo's review.toml, which already declares one) and commit
        it — `handoff` refuses a dirty tree, so the edit has to land in a
        clean commit to reach the real verb at all."""
        toml_path = repo / "review.toml"
        original = toml_path.read_text(encoding="utf-8")
        text, n = re.subn(r'(?m)^relay\s*=.*$', f"relay = {value!r}",
                          original, count=1)
        self.assertEqual(n, 1, "scratch review.toml must declare one "
                               "[roles] relay line to overwrite")
        toml_path.write_text(text, encoding="utf-8")
        sh("git", "-C", str(repo), "commit", "-qam", "relay config change")

    def test_a_config_relay_sentence_refuses_the_real_verb(self):
        """Round 4 F2's real-verb falsification. The claim states no
        `relay` of its own (the ordinary case — `CLAIM` below never sets
        one), and the repo's OWN `[roles] relay` — the other admitted
        source, copied onto the Roles line by `emit_request` when the claim
        is silent — is a hand-back sentence instead of an actor. The real
        emission verb, `handoff`, must refuse before ledger, Git, gates or
        envelope output; the paired control, `relay = "user"`, must pass
        and render `relay=user`.

        MUTATION: removing the `("roles", "relay")` grammar check from
        `config._check_value` (or not calling `config.check_shape` before
        `handoff` reaches `emit_request`) lets the sentence reach the
        config's effective roles unchecked — `handoff` then succeeds and
        the kept request carries the sentence on its Roles line, failing
        the assertions below.
        """
        scratch = scratch_loop_repo(self, "relay-cfg-", name="relay",
                                    objective="relay config test")
        tmp, repo = scratch.tmp, scratch.repo
        base, claim, cwd = scratch.base, scratch.claim, scratch.cwd
        state = tmp / "state"

        self._set_config_relay(
            repo, "After validation, return the verdict to the user")

        code, refused = run_cli(repo, state, "handoff", "--claim-file",
                                str(claim), "--base", base, "--local-only",
                                cwd=cwd)

        self.assertNotEqual(code, 0, refused)
        self.assertIsInstance(refused, dict, refused)
        self.assertIn("relay", refused.get("error", "").lower())
        self.assertFalse(state.exists(),
                         "a refused handoff must write no ledger state")

        self._set_config_relay(repo, "user")

        code, ok = run_cli(repo, state, "handoff", "--claim-file",
                           str(claim), "--base", base, "--local-only",
                           cwd=cwd)

        self.assertEqual(code, 0, ok)
        kept_text = Path(ok["kept"]).read_text(encoding="utf-8")
        self.assertIn("relay=user", kept_text)

    def test_a_config_relay_with_surrounding_whitespace_refuses_the_real_verb(
            self):
        """Round 5 F3's real-verb falsification, config-provided half.
        `config._check_value` used to match `value.strip()` against the
        actor grammar while `emit_request`'s Roles line rendered the
        `[roles] relay` value UNSTRIPPED — so `" user"`, `"user "` and
        `" user "` each crossed `handoff`'s config shape check and would
        have rendered surrounding whitespace on the Roles line. Each must
        refuse the real verb before ledger, Git, gates or envelope output;
        `"user"` is the paired control, proved above.

        MUTATION: restoring `value.strip()` in `config._check_value`'s
        `("roles", "relay")` check lets each whitespace value below reach
        `emit_request` and this test's refusal assertions fail.
        """
        scratch = scratch_loop_repo(self, "relay-cfg-ws-", name="relay-ws",
                                    objective="relay config whitespace test")
        tmp, repo = scratch.tmp, scratch.repo
        base, claim, cwd = scratch.base, scratch.claim, scratch.cwd
        state = tmp / "state"

        for whitespace_relay in (" user", "user ", " user "):
            self._set_config_relay(repo, whitespace_relay)

            code, refused = run_cli(repo, state, "handoff", "--claim-file",
                                    str(claim), "--base", base,
                                    "--local-only", cwd=cwd)

            self.assertNotEqual(code, 0, refused)
            self.assertIsInstance(refused, dict, refused)
            self.assertIn("relay", refused.get("error", "").lower())
            self.assertFalse(
                state.exists(),
                f"relay {whitespace_relay!r}: a refused handoff must write "
                f"no ledger state")

    def test_a_claim_relay_with_surrounding_whitespace_refuses_the_real_verb(
            self):
        """Round 5 F3's real-verb falsification, claim-provided half —
        the counterpart to the config case above. `validate_claim` used to
        match `item.strip()` against the actor grammar while
        `emit_request`'s Roles line rendered the claim's ORIGINAL,
        unstripped `relay` member — so a claim naming `" user"`, `"user "`
        or `" user "` crossed `handoff`'s claim boundary and would have
        rendered surrounding whitespace on the Roles line. Each must
        refuse the real verb before ledger, Git, gates or envelope output;
        `"user"` is the paired control, and the kept request's Roles line
        must carry it exactly.

        MUTATION: restoring `item.strip()` in `validate_claim`'s
        `member == "relay"` check lets each whitespace value below reach
        `emit_request`, and this test's refusal/control assertions fail.
        """
        scratch = scratch_loop_repo(self, "relay-claim-ws-",
                                    name="relay-claim-ws",
                                    objective="relay claim whitespace test")
        tmp, repo = scratch.tmp, scratch.repo
        base, claim, cwd = scratch.base, scratch.claim, scratch.cwd
        state = tmp / "state"

        def _write_claim(relay):
            claim.write_text(json.dumps({
                "objective": "relay claim whitespace test",
                "references": [{"path": "review.toml", "required": True}],
                "relay": relay}), encoding="utf-8")

        for whitespace_relay in (" user", "user ", " user "):
            _write_claim(whitespace_relay)

            code, refused = run_cli(repo, state, "handoff", "--claim-file",
                                    str(claim), "--base", base,
                                    "--local-only", cwd=cwd)

            self.assertNotEqual(code, 0, refused)
            self.assertIsInstance(refused, dict, refused)
            self.assertIn("relay", refused.get("error", "").lower())
            self.assertFalse(
                state.exists(),
                f"relay {whitespace_relay!r}: a refused handoff must write "
                f"no ledger state")

        # The control: the otherwise identical claim, naming a single-token
        # relay, succeeds and renders it exactly on the Roles line.
        _write_claim("user")

        code, ok = run_cli(repo, state, "handoff", "--claim-file",
                           str(claim), "--base", base, "--local-only",
                           cwd=cwd)

        self.assertEqual(code, 0, ok)
        kept_text = Path(ok["kept"]).read_text(encoding="utf-8")
        self.assertIn("relay=user", kept_text)


class TestCacheRolesKey(unittest.TestCase):
    """The effective stamp joins the warm-key: a kept envelope emitted
    under one direction must not answer an invocation that selected
    another."""

    def _warm_fixture(self):
        from review.tests._transport_fixtures import warm_cache_fixture
        return warm_cache_fixture(self, prefix="handoff-roles-")

    def test_same_roles_stay_warm_explicit_and_defaulted(self):
        w = self._warm_fixture()
        self.assertIsNotNone(w.cached(roles=("claude", "codex")))
        # None means "no selection", which resolves to the config default —
        # a derivable state, so legacy behaviour is unchanged.
        self.assertIsNotNone(w.cached())

    def test_a_different_selection_is_cold(self):
        self.assertIsNone(self._warm_fixture().cached(
            roles=("codex", "claude")))


if __name__ == "__main__":
    unittest.main()

"""§6.3 / RVW-T10: the per-agent adapters render from ONE source and cannot
drift from the CLI's verb surface; the tracked copies are byte-guarded.

FALSIFICATIONS: a verb the CLI has and the adapter omits (or the reverse)
fails; two adapters with different bodies fail; a tracked adapter that
differs from the render fails `--check`. Everything here is read-only except
the temp-dir round trips, which self-skip where writes are denied. The
install tests never touch a real agent directory: every target is a temp
path passed in explicitly, the in-process CLI tests patch `install_targets`
(and `legacy_targets`' interlock keeps a substituted target from reaching
any legacy path under the real HOME), and every subprocess test runs
`bin/loupe` — or the package alone — with HOME redirected into its own
temp tree.
"""
import contextlib
import hashlib
import json
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from review import (TOOL_NAME, TOOL_VERSION, adapters, cli, config, env_var,
                    vocab)
from review.digest import sha256_text
from review.tests.util import REPO_ROOT, public_path, spec_path

VERB_RE = re.compile(rf"`{TOOL_NAME} ([a-z-]+)")


class TestOneSource(unittest.TestCase):

    def test_every_kind_renders_and_names_the_tool(self):
        for kind in adapters.OUTPUTS:
            text = adapters.render(kind)
            self.assertIn(TOOL_NAME, text)
            self.assertIn("GENERATED", text)

    def test_bodies_are_identical_across_kinds(self):
        # The header names the surface; everything after "## What this is"
        # is the one procedure. Two adapters with different bodies would be
        # two sources.
        bodies = set()
        for kind in adapters.OUTPUTS:
            text = adapters.render(kind)
            bodies.add(text[text.index("## What this is"):]
                       .replace(f"<!-- END GENERATED: {TOOL_NAME} adapter -->",
                                "").rstrip())
        self.assertEqual(len(bodies), 1)

    def test_skill_frontmatter(self):
        for kind in ("claude-skill", "codex-skill"):
            text = adapters.render(kind)
            self.assertTrue(text.startswith("---\nname: " + TOOL_NAME + "\n"))
            self.assertIn("\ndescription: ", text.split("---")[1])

    def test_the_trigger_covers_both_config_layers(self):
        # The trigger once named only the in-tree review.toml. It must name
        # both layers: the in-tree file every reviewed door requires, and
        # the user-level config that still governs local verbs — the skill
        # fires for local-verb work too, in a repo with no open request
        # (found by the first pilot; scoping per 0.9.0 / lineage 18 F1).
        for kind in ("claude-skill", "codex-skill"):
            description = adapters.render(kind).split("---")[1]
            self.assertIn(f"~/.config/{TOOL_NAME}/", description)
            self.assertIn("review.toml", description)

    def test_verb_table_matches_the_parser_both_ways(self):
        parser_verbs = {name for name, _ in adapters.verb_table()}
        # From the parser directly, not through the helper under test.
        sub = next(a for a in cli.build_parser()._actions
                   if getattr(a, "choices", None))
        self.assertEqual(parser_verbs, set(sub.choices))
        for kind in adapters.OUTPUTS:
            text = adapters.render(kind)
            table = text[text.index("## Verbs"):text.index("## Where things")]
            in_table = set(VERB_RE.findall(table))
            self.assertEqual(in_table, parser_verbs, kind)
            # Every command line the procedure carries names a real verb.
            named = set(VERB_RE.findall(text))
            self.assertTrue(named <= parser_verbs | {"ledger"},
                            named - parser_verbs)

    def test_procedure_carries_the_stop_rule_on_both_sides(self):
        text = adapters.render("instructions-block")
        author = text[text.index("author stamp"):text.index("reviewer stamp")]
        reviewer = text[text.index("reviewer stamp"):text.index("## Verbs")]
        self.assertIn("STOP", author)
        self.assertIn("STOP", reviewer)
        self.assertIn("human", text[text.index("both sides"):
                                    text.index("author stamp")])

    def test_procedure_states_the_trust_model_on_both_sides(self):
        """The one-principal trust model (design §2, ruled 2026-09-03 after
        lineage 20) lives in the both-sides rules of every rendered surface,
        and in the contract; a forgery finding is out of taxonomy unless it
        names a party with less than operator access."""
        for surface in ("instructions-block", "claude-skill", "codex-skill"):
            text = adapters.render(surface)
            both = text[text.index("both sides"):text.index("author stamp")]
            self.assertIn("Trust model.", both)
            self.assertIn("LESS than operator access", both)
            self.assertIn("OMISSION and FATIGUE", both)
        # spec_path() resolves the design text in the workbench (public/docs)
        # and in the published candidate (docs/) alike.
        self.assertIn("Trust model — one principal", spec_path().read_text())

    def test_transport_verbs_are_the_procedure(self):
        text = adapters.render("claude-skill")
        for verb in ("handoff", "take", "close", "respond", "validate"):
            self.assertIn(f"`{TOOL_NAME} {verb}", text)

    def test_the_rule_step_tells_the_reviewer_to_anchor_falsification(self):
        """RVW-T21 D3: a falsification test anchored in a mutable artifact
        outside the reviewed tree (a PR body, an issue, a dashboard) stops
        being executable the moment that artifact moves — through no act of
        the author's — and nothing warned the reviewer against writing one.
        The `rule` step must now say: anchor in the tree wherever the
        defect admits it, and say so explicitly when it cannot, so a later
        `cannot_execute` reads as a stated dependency, not an evasion.

        FALSIFICATION: drop the added sentence from the `"reviewer"` entry
        of `procedure()`'s `"rule"` step and this fails in every kind (the
        text is shared, per `test_bodies_are_identical_across_kinds`).
        """
        for kind in adapters.OUTPUTS:
            text = adapters.render(kind)
            rule = text[text.index("**rule**"):text.index("**validate")]
            self.assertIn("mutable artifact outside the tree", rule, kind)
            self.assertIn("cannot_execute", rule, kind)
            self.assertIn("unverifiable", rule, kind)

    def test_the_respond_step_orders_fix_before_falsification_before_mutation_before_record_before_handoff(self):
        """Brief `respond-procedure-ordering`: read in order, the old text
        let an agent record `accepted` (with a falsification run) before the
        fix existed, or fix first and then wonder what "the mutation" meant.
        The reworded step must put five things in this order, textually,
        in every rendered adapter (the body is shared, per
        `test_bodies_are_identical_across_kinds`): the instruction to make
        the fix; the instruction to run the finding's falsification test on
        the fixed head; the instruction to run the mutation (defect
        reintroduced) and restore; the instruction to write the disposition
        record with `--out`; the instruction to hand off again.

        FALSIFICATION: restore the old ordering sentence ("...records the
        run of THAT test... Do the mutation before you write the record,
        not after. `--out` records and keeps it. Then make the changes and
        hand off again...") in the `"respond"` entry of `procedure()` and
        this fails in every kind, because "make the fix" no longer precedes
        "falsification test" in the text.
        """
        markers = ("make the fix", "falsification test", "mutation",
                   "`--out`", "hand off again")
        for kind in adapters.OUTPUTS:
            text = adapters.render(kind)
            # From the descriptive paragraph, not the command line above it
            # — that line names `--out` too, as a flag, and would satisfy
            # the marker before the ordering it is meant to check.
            respond = text[text.index("Exactly one disposition per finding"):
                          text.index("**close a lineage")]
            positions = [respond.index(m) for m in markers]
            self.assertEqual(positions, sorted(positions),
                             f"{kind}: {markers} out of order in {positions}")

    def test_the_respond_step_never_calls_a_proposed_fix_accepted(self):
        """A fix that is proposed but not yet made is `deferred` or
        `escalated`, never `accepted` — and a blocking finding cannot take
        `deferred`. The old text said nothing about the not-yet-made case
        at all, which is what let an agent reach for `accepted` there.

        FALSIFICATION: drop the "proposed but not made" sentence from the
        `"respond"` entry of `procedure()` and this fails in every kind.
        """
        for kind in adapters.OUTPUTS:
            text = adapters.render(kind)
            respond = text[text.index("**respond**"):
                          text.index("**close a lineage")]
            self.assertIn("proposed but not made", respond, kind)
            not_made = respond[respond.index("proposed but not made"):]
            self.assertIn("`deferred` or `escalated`", not_made, kind)
            self.assertIn("never `accepted`", not_made, kind)
            self.assertNotIn("is `accepted`", not_made, kind)
            self.assertIn("blocking finding cannot take `deferred`",
                         not_made, kind)


class TestTrackedCopiesAreCurrent(unittest.TestCase):

    def test_repo_adapters_match_the_render(self):
        # The gate `adapters` runs this through the CLI; here it is the
        # library call, so a stale tracked copy fails the suite too.
        self.assertEqual(adapters.check_all(REPO_ROOT / adapters.ADAPTERS_DIR),
                         [])

    def test_check_reports_stale_and_missing(self):
        try:
            tmp = Path(tempfile.mkdtemp(prefix="adapters-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc}); the round "
                          f"trip runs only in the writable pass")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        # Missing → all stale.
        self.assertEqual(len(adapters.check_all(tmp)), len(adapters.OUTPUTS))
        adapters.render_all(tmp)
        self.assertEqual(adapters.check_all(tmp), [])
        # One byte off → exactly that file.
        target = tmp / adapters.OUTPUTS["codex-skill"]
        target.write_text(target.read_text(encoding="utf-8") + "\n",
                          encoding="utf-8")
        self.assertEqual(adapters.check_all(tmp), [str(target)])
        # And through the CLI, exit 1 with a next command.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["--ledger-dir", str(tmp / "state"),
                             "render-adapters", "--check", "--dir", str(tmp)])
        self.assertEqual(code, 1)
        self.assertIn("render-adapters", buf.getvalue())




class TestPublicCommandParity(unittest.TestCase):
    """Sweep F13 (Medium): the documented common path is the executable one.

    The quick-start told a fresh reviewer to run `loupe take <request.md>`
    and the private design promised zero required flags, while the parser
    has required `--as` since lineage-2 round 1 and refuses silence. The
    identity is an integrity requirement, not a preference, so the
    documents and the CLI may not disagree about it. Every place the public
    surface shows the take command is checked here against the parser.
    """

    @staticmethod
    def _take_invocations(text: str) -> list[str]:
        """Every `loupe take …` invocation in a document, as printed."""
        # An invocation names an argument after the verb; a bare `loupe
        # take` in a verb table is a name, not a command.
        return re.findall(r"loupe take\s+[^\n`|]+", text)

    def test_readme_take_includes_the_mandatory_identity(self):
        """FALSIFICATION for F13. Mutation: restore the flagless
        `loupe take <request.md>` in the README quick-start (or the
        zero-required-flags sentence in the design) and this fails."""
        readme = public_path("README.md")
        if readme is None:
            self.skipTest("no README shipped in this tree")
        text = readme.read_text(encoding="utf-8")
        found = self._take_invocations(text)
        self.assertTrue(found, "the README shows the reviewer's command")
        for inv in found:
            self.assertIn("--as", inv, f"a take without its identity: {inv!r}")
        # The executable is the authority: `take` without `--as` refuses,
        # records nothing, and names the flag — the docs are checked against
        # that, not against each other. (Enforced by the verb, with the §7
        # reasoning, rather than by argparse: a usage error would say
        # "required" and not why.)
        from review.tests._transport_fixtures import request_text
        with tempfile.TemporaryDirectory() as tmp:
            req = Path(tmp) / "r.md"
            req.write_text(request_text(), encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = cli.main(["--ledger-dir", tmp, "take", str(req)])
            self.assertNotEqual(code, 0)
            payload = buf.getvalue()
            self.assertIn("--as", payload)
            self.assertNotIn("\"event\"", payload)
        parser = cli.build_parser()
        ns = parser.parse_args(["take", "r.md", "--as", "codex"])
        self.assertEqual(ns.as_, "codex")
        self.assertIn("REQUIRED", next(
            a.help for a in parser._actions
            if getattr(a, "choices", None) and "take" in a.choices
            for a in a.choices["take"]._actions if a.dest == "as_"))
        # The shipped spec explains the requirement, and never shows the
        # command without it.
        spec = spec_path()
        if spec is not None:
            for inv in self._take_invocations(spec.read_text(encoding="utf-8")):
                self.assertIn("--as", inv, inv)

    def test_the_rendered_adapters_agree(self):
        # The instruction block every adapter renders from shows the take
        # command with its identity — the reviewer's procedure is one line
        # the agent runs verbatim, so a placeholder-free `--as` is the
        # requirement made visible.
        seen = 0
        for kind in ("instructions-block", "claude-skill", "codex-skill"):
            for inv in self._take_invocations(adapters.render(kind)):
                seen += 1
                self.assertIn("--as", inv, f"{kind}: {inv!r}")
        self.assertTrue(seen, "the adapters show the reviewer's command")


class TestAdvertisedVersionParity(unittest.TestCase):
    """Lineage 6 round 5 F1 (Medium): the shipped README advertises the
    version the executable reports.

    `review.TOOL_VERSION` is the one authority — `--version`, the
    generated adapters and pyproject all read it — and the README Status
    line is a second surface stating the same fact by hand. The 0.4.0 ->
    0.5.0 bump moved the authority and left the advertisement behind, so
    the candidate a clean close would publish claimed two current
    versions at once. A hand-kept copy of a machine-known fact needs a
    gate or it drifts at the next bump, and this is that gate: it runs in
    the candidate's own standalone suite, which is where the defect was
    publishable from.
    """

    STATUS = re.compile(r"^Version (\S+) ", re.M)

    def _advertised(self):
        readme = public_path("README.md")
        if readme is None:
            self.skipTest("no README shipped in this tree")
        found = self.STATUS.search(readme.read_text(encoding="utf-8"))
        self.assertIsNotNone(found, "the README Status line names no version")
        return found.group(1)

    def test_the_readme_advertises_the_executable_version(self):
        """FALSIFICATION for round-5 F1. Mutation: restore `Version 0.4.0`
        in the README Status line, or move TOOL_VERSION without it, and
        this fails naming both values."""
        advertised = self._advertised()
        self.assertEqual(
            advertised, TOOL_VERSION,
            f"the README advertises {advertised} and the executable "
            f"reports {TOOL_VERSION}: the bump reached the authority and "
            f"not the advertisement")

    def test_the_cli_reports_the_same_version(self):
        """The paired control, through the surface a reader actually runs:
        `--version` is the authority the README is checked against, so the
        test cannot pass by comparing a constant with itself."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["--version"])
        self.assertEqual(code, 0)
        self.assertEqual(buf.getvalue().strip(), self._advertised())


class TestReadmeLifecycleClaims(unittest.TestCase):
    """Lineage 16 round 1, F2 and F4: two README sentences survived the
    rewrite from before the recovery contract split, and both stated the
    runtime wrong — the same hand-kept-copy drift the version-parity gate
    above exists for, on two more facts.

    F2: the README said every non-zero exit prints the next command. The
    recovery authority is `cli._blocked`, which partitions non-zero exits
    into `next_kind: command` (a literal `next` to run verbatim) and
    `next_kind: blocked` (`next: null`, a `remedy` a PERSON acts on) —
    blocked is a real state, not a gap, and a reader told otherwise
    improvises a command exactly where the tool says none can repair the
    state. The paired runtime controls are
    `test_command_boundary.TestFinishIsGuardedLikeBlocked`; this half
    binds the README's wording to that partition.

    F4: the README said the agents on either side "run one command each
    and stop", two paragraphs above a loop in which the author runs
    `handoff`, `close` and `respond`. One command per hand-off is the
    true claim; per side end-to-end it is false, and the loop itself is
    the evidence — so the check derives the verb count from the loop the
    document prints rather than restating it here.
    """

    def _readme(self):
        readme = public_path("README.md")
        if readme is None:
            self.skipTest("no README shipped in this tree")
        return readme.read_text(encoding="utf-8")

    def test_the_recovery_contract_states_both_kinds(self):
        """FALSIFICATION for F2. Mutation: restore "every non-zero exit
        prints the next command" in the exit-code paragraph and this fails
        while both runtime controls stay green."""
        text = self._readme()
        found = re.search(r"Exit codes mean exactly one thing.*?\n\n", text,
                          re.S)
        self.assertIsNotNone(
            found, "the README no longer carries its exit-code paragraph "
                   "where this check reads it")
        region = found.group(0)
        self.assertIsNone(
            re.search(r"every non-zero exit prints the next command", region),
            "the README claims a universal next command; blocked exits "
            "intentionally have none (`cli._blocked`)")
        for term in ("`next_kind`", "`command`", "`blocked`", "`next: null`",
                     "`remedy`"):
            self.assertIn(
                term, region,
                f"the exit-code paragraph no longer states {term}: the "
                f"two-state recovery contract must be stated whole, or a "
                f"reader cannot know a blocked exit is theirs to act on")

    def test_no_one_command_total_claim_beside_a_multi_verb_loop(self):
        """FALSIFICATION for F4. Mutation: restore "run one command each
        and stop" in the opener and this fails against the unchanged loop.
        Control: per-hand-off wording ("one command per hand-off") passes."""
        text = self._readme()
        fences = re.findall(r"^```[^\n]*\n(.*?)^```", text, re.S | re.M)
        self.assertTrue(fences, "the README no longer prints the loop")
        verbs = {}
        for body in fences:
            for side, verb in re.findall(r"^(author|reviewer)\s+loupe (\S+)",
                                         body, re.M):
                verbs.setdefault(side, set()).add(verb)
        multi_verb = any(len(v) > 1 for v in verbs.values())
        self.assertTrue(
            verbs, "the loop names no author/reviewer commands where this "
                   "check reads them")
        if multi_verb:
            self.assertIsNone(
                re.search(r"one command each", text),
                "the README claims one command per side while its own loop "
                "prints more than one verb for a side; say one command per "
                "hand-off, or drop the count")


class TestConfigAuthorityClaims(unittest.TestCase):
    """Lineage 18 round 1 F1: the shipped specification advertised a
    configuration fallback every reviewed door refuses — "identical
    behaviour whether the configuration is personal or committed",
    "user-level config preserves the zero-footprint property", and a
    `take` that falls back to this checkout's rules. The runtime says the
    opposite and had said it since 0.9.0.

    This is the DOC half of that finding's guard, finite and anchored:
    the fallback phrasings are banned from every shipped document, and
    the target-carried-authority rule must be stated where each document
    explains configuration. The runtime half — a valid user-level config
    opens none of the three reviewed doors — is
    `test_transport_authority.TestTheReviewedCommitCarriesItsOwnRules
    .test_a_user_level_config_opens_no_reviewed_door`; those controls
    must stay refusing while any mutation of the prose back to the
    fallback claim fails here.

    Mutation: restore any of the three 0.11.2 fallback paragraphs to the
    spec (ownership conclusion, ownership table paragraph, or the `take`
    bullet) and a banned phrase or a missing required statement fails
    this class while the runtime controls stay green.
    """

    BANNED = (
        "personal or committed",
        "preserves the zero-footprint property",
        "if the repo wants it",
        "is governed by this checkout",
    )

    def _text(self, path, name):
        if path is None:
            self.skipTest(f"no {name} shipped in this tree")
        return path.read_text(encoding="utf-8")

    def _flat(self, text):
        return re.sub(r"\s+", " ", text)

    def test_no_shipped_document_claims_the_fallback(self):
        for name, path in (("spec", spec_path()),
                           ("readme", public_path("README.md")),
                           ("onboarding", public_path("docs/onboarding.md"))):
            with self.subTest(document=name):
                flat = self._flat(self._text(path, name))
                for phrase in self.BANNED:
                    self.assertNotIn(
                        phrase, flat,
                        f"{name} claims the retired user-level fallback "
                        f"({phrase!r}); every reviewed door refuses it")

    def test_the_spec_states_the_three_door_rule(self):
        flat = self._flat(self._text(spec_path(), "spec"))
        ownership = re.search(r"## 2\. Ownership model.*?## 3\.", flat)
        self.assertIsNotNone(
            ownership, "the spec no longer carries its ownership section "
                       "where this check reads it")
        region = ownership.group(0)
        self.assertIn(
            "refuses a target that tracks no `review.toml`", region,
            "the ownership model no longer states the reviewed-door "
            "refusal")
        self.assertIn(
            "local verbs", region,
            "the ownership model no longer scopes user-level config to "
            "local verbs")
        self.assertIn(
            "refused here exactly as it is refused at `handoff`", flat,
            "the `take` description no longer refuses a configless target "
            "the way handoff does")

    def test_readme_and_onboarding_state_the_same_rule(self):
        readme = self._flat(self._text(public_path("README.md"), "readme"))
        self.assertIn(
            "refuse a target that tracks no `review.toml`", readme)
        self.assertIn("governs local verbs only", readme)
        onboarding = self._flat(
            self._text(public_path("docs/onboarding.md"), "onboarding"))
        self.assertIn("refuses a target that tracks none", onboarding)
        self.assertIn("governs every **local** verb", onboarding)


class TestChangelogCompatibilityClaim(unittest.TestCase):
    """Lineage 16 round 2 F1, strengthened by round 3 F1: the 0.11.1 note
    said "An older reader is unaffected" one sentence after disclosing
    that a cross-version round reads `differs`. Compatibility and absence
    of effect are different claims: the older reader accepts and
    validates — the mismatch is reported, never refused
    (`test_tool_identity.TestTheThreeAgreementStates` is the paired
    runtime control) — but it reports the mismatch, records the
    provenance, and renders its side with its own code.

    Round 3 F1: the first cut required the SUBSTRING `refused`, which the
    semantic opposite ("every envelope is refused") also contains — a
    check that accepts the reversal of the boundary it names binds
    nothing. So the load-bearing terms are now checked with their
    POLARITY: every refusal stem must sit inside a "never refused"
    collocation, and "reported" and "accepts" must not carry an immediate
    negation. The admitted domain is stated rather than implied: polarity
    is recognised through these stems and immediate negators only, over
    whitespace-normalised prose — a reversal reworded without the stems
    ("declines every envelope") escapes this check, exactly as a fence
    that grew a member list escapes the unenumerated guard; what cannot
    escape is any reversal that still uses the boundary's own words."""

    def test_the_0_11_1_note_states_the_boundary_not_no_effect(self):
        """FALSIFICATION for round-2 F1 and round-3 F1. Mutations, each
        run: restore "An older reader is unaffected" and this fails;
        state "the mismatch is reported and every envelope is refused"
        and this fails on the refusal polarity — while both runtime
        controls stay green either way."""
        changelog = public_path("CHANGELOG.md")
        if changelog is None:
            self.skipTest("no CHANGELOG shipped in this tree")
        text = changelog.read_text(encoding="utf-8")
        found = re.search(r"## 0\.11\.1\n(.*?)\n## ", text, re.S)
        self.assertIsNotNone(
            found, "the CHANGELOG no longer carries a 0.11.1 section where "
                   "this check reads it")
        # One whitespace regime, so a collocation split by a line wrap is
        # the same collocation.
        norm = " ".join(found.group(1).split())
        self.assertNotIn(
            "reader is unaffected", norm,
            "the 0.11.1 note claims no effect beside a disclosed `differs` "
            "report; state the boundary instead — accepted and validated, "
            "reported, rendered by the reader's own code")
        self.assertIn("`differs`", norm,
                      "the 0.11.1 note no longer discloses the `differs` "
                      "report the boundary exists to explain")
        refusals = re.findall(r"(?:\bnever\s+)?\brefus\w*", norm)
        self.assertTrue(
            refusals,
            "the 0.11.1 note no longer states the refusal side of the "
            "boundary at all")
        for tok in refusals:
            self.assertTrue(
                tok.startswith("never"),
                f"the 0.11.1 note carries a refusal claim outside the "
                f"'never refused' collocation ({tok!r}): an older reader "
                f"never refuses the envelope, and a note stating otherwise "
                f"reverses the boundary "
                f"(TestTheThreeAgreementStates is the runtime authority)")
        self.assertRegex(
            norm, r"(?<!never )(?<!not )\breported\b",
            "the 0.11.1 note no longer affirms that the mismatch is "
            "reported")
        self.assertNotRegex(
            norm, r"\b(?:never|not|no longer)\s+(?:reported|accepts)",
            "the 0.11.1 note negates a side of the boundary that the "
            "runtime affirms")
        self.assertIn(
            "accepts and validates", norm,
            "the 0.11.1 note no longer states what an older reader still "
            "does — accepts and validates the envelope")


class TestShippedRestatements(unittest.TestCase):
    """Round-2 F3: a CLOSED inventory of every fact the shipped documents
    restate by hand, each bound to the authority that owns it.

    The history is three rounds of the same lesson. Round-5 F1 found the
    version advertised on one surface and held on another. The repair
    guarded the version. Round-1 F2 found "Four mechanisms" beside five
    headings; that repair guarded the count. Round-2 F3 then deleted the
    `escalated` row from the shipped disposition table and every one of
    those gates stayed green — because each was a named regex for the
    newest reproducer, which is the example-by-example loop the
    boundary-closure rule prohibits.

    So the domain is enumerated instead. `RESTATEMENTS` binds each shipped
    restatement to its runtime authority and its exact location, every
    entry is checked in BOTH directions — a missing member and an invented
    one are equally wrong — and `test_every_closed_vocabulary_is_registered`
    fails when the code grows a closed vocabulary that no entry covers, so
    the inventory cannot fall behind the authority it describes.
    """

    #: name -> (authority members, where they are restated, how to read it)
    #: `pattern` captures ONE region of the shipped document; `reader` turns
    #: that region into the member set. A reader per entry rather than one
    #: rule for all, because the sites genuinely differ — a table states its
    #: members in the first column, the verdict strings carry a `VERDICT: `
    #: prefix, a closure member carries its outcomes after a colon — and a
    #: single blanket rule would have forced those documents to be reworded
    #: to suit the test rather than the reader.
    @staticmethod
    def _ticked(region):
        return set(re.findall(r"`([^`]+)`", region))

    @classmethod
    def _table_keys(cls, region):
        """A markdown table states its members in the first column."""
        return {row.split("|")[1].strip().strip("`")
                for row in region.splitlines()
                if row.startswith("|") and "---" not in row
                and row.split("|")[1].strip().startswith("`")}

    @classmethod
    def _before_colon(cls, region):
        return {t.split(":", 1)[0].strip() for t in cls._ticked(region)}

    @classmethod
    def _after_verdict(cls, region):
        return {t.split("VERDICT:", 1)[-1].strip()
                for t in cls._ticked(region)}

    RESTATEMENTS = {
        # (authority, document, pattern, reader)
        # Round-3 F2: the DOCUMENT is part of every entry. Round 2's
        # inventory read only the specification, so the README's own
        # breaker list — the very drift this lineage opened with — went
        # unchecked while the closed-world gate stayed green. An inventory
        # that cannot name two documents is not an inventory of what ships.
        "dispositions": (vocab.DISPOSITIONS, "design",
                         r"closed vocabulary of five.*?\n\n(\|.*?)\n\n",
                         "_table_keys"),
        "closures": (vocab.CLOSURES, "design",
                     r"\*\*Reviewer closure events\*\* —(.*?)— are",
                     "_before_colon"),
        "verdicts": (vocab.VERDICTS, "design",
                     r"\*\*two-string verdict\*\*:(.*?)the first "
                     r"meaningful line", "_after_verdict"),
        "transports": (vocab.TRANSPORTS, "design",
                       r"declared, closed-vocabulary input —(.*?)defaulting",
                       "_ticked"),
        "breakers (design)": (vocab.BREAKERS, "design",
                              r"the breakers\s*\((.*?)\)", "_ticked"),
        "breakers (readme)": (vocab.BREAKERS, "readme",
                              r"the breakers\s*\((.*?)\)", "_ticked"),
        "falsification_statuses": (vocab.FALSIFICATION_STATUSES, "design",
                                   r"records `status`\s*\((.*?)\) for the",
                                   "_ticked"),
        "mutation_outcomes": (vocab.MUTATION_OUTCOMES, "design",
                              r"and `mutation` \((.*?)\) for the same",
                              "_ticked"),
        # Round-3 F3: these four were declared "deliberately undocumented"
        # on the premise that the specification gives them no member list.
        # It gives all four one. The exclusion mechanism had certified real
        # restatements as non-restatements, so the inventory was complete
        # only relative to a false classification.
        "finding_fields": (vocab.FINDING_FIELDS, "design",
                           r"and each carries (.*?)\.\s", "_prose_list"),
        "accepted_subtypes": (vocab.ACCEPTED_SUBTYPES, "design",
                              r"one structured subtype, `accepted\((.*?)\)`",
                              "_bare"),
        "test_amended_payload": (vocab.TEST_AMENDED_PAYLOAD, "design",
                                 r"unsatisfiable \(payload: (.*?)\)",
                                 "_ticked"),
        "test_amendment_outcomes": (vocab.TEST_AMENDMENT_OUTCOMES, "design",
                                    r"`test_amendment: (.*?)`", "_alternates"),
        # Found by the tripwire below on its first run, in a table this
        # very lineage added: §3.3(e)'s reader table enumerates the stamped
        # kinds, so `STAMPED_KINDS` was a restatement the moment it was
        # written, and my own UNDOCUMENTED list was wrong about it exactly
        # the way round-3 F3 says such lists go wrong.
        "stamped_kinds": (vocab.STAMPED_KINDS, "design",
                          r"\| stamped \| written by \|(.*?)\n\n",
                          "_stamped_rows"),
        # Two more the cross-product discovery found in the README on its
        # first run, both of which the round-2 inventory could not have
        # seen because it had no notion of a second document.
        "verdicts (readme)": (vocab.VERDICTS, "readme",
                              r"→ records; (.*?)\n.*?lineage is closed",
                              "_ticked"),
        "stamped_kinds (readme)": (vocab.STAMPED_KINDS, "readme",
                                   r"review tool: (.*?)\nenvelopes",
                                   "_arrow_chain"),
        # 0.11.3: `references` joined CLAIM_REQUIRED, which armed the
        # cross-product tripwire on the README's walkthrough — its example
        # claim spells out exactly the required members, as any minimal
        # claim must. Registered so the example drifts loudly if the
        # required set moves again.
        "claim_required (readme)": (vocab.CLAIM_REQUIRED, "readme",
                                    r"\$ cat > \S*claim\.json <<'EOF'\n(.*?)\nEOF",
                                    "_json_keys"),
    }

    #: Authorities no shipped document enumerates. Kept explicit, and kept
    #: HONEST by `test_no_excluded_authority_is_quietly_enumerated` below —
    #: round-3 F3 was this list containing four vocabularies the
    #: specification spells out in full.
    UNDOCUMENTED = {
        "BLOCKING_ILLEGAL", "FALSIFICATION_KINDS", "FALSIFICATION_RECORD",
        "LINEAGE_KINDS", "LINEAGE_MERGING", "CLAIM_STRING_FIELDS",
        "CLAIM_LIST_FIELDS", "CLAIM_NONEMPTY",
        "CLAIM_REFERENCE_REQUIRED", "SEAM_CLASSES", "STAMPED_PARSE_CALLS",
        # Round 4 F1: git's tree-entry modes, not this tool's vocabulary.
        # The shipped design documents what the tool decides, and which
        # modes carry file bytes is git's rule — restating it here would
        # publish a second copy of someone else's specification.
        "GIT_FILE_MODES",
        # 2026-09-01, the authorization body. The specification documents
        # the ARTIFACT — that it exists, what it is for, that it is never a
        # derived clean verdict — and the validator owns its member
        # grammar, exactly as it owns the claim's. Publishing the member
        # list beside the argument would restate a grammar that only one
        # verb writes, and that verb is a human's.
        "AUTHORIZATION_REQUIRED", "WAIVED_REQUIRED",
        # 2026-09-01, round 3 F3 closed the authorization grammar; every
        # constant it added falls under the same rule as the two above. The
        # specification documents the ARTIFACT and the validator owns its
        # member grammar, exactly as it does for the claim.
        "AUTHORIZATION_WRAPPER_FIELDS", "AUTHORIZATION_FIELDS",
        "AUTHORIZATION_NONEMPTY", "AUTHORIZATION_DUPLICATED",
        "WAIVED_FIELDS",
        # Round-9 F2, the legacy import door's closed grammar. The shipped
        # specification documents the MECHANISM — that a legacy corpus is
        # ingested as pre-derived events bound to verified source bytes —
        # and the importer owns the member grammar, exactly as the
        # validator owns the claim's and the authorization's. Publishing
        # the row schema beside it would restate a grammar only one verb
        # reads, and whose other half (any given corpus's derivation) is
        # per-repository by construction.
        "LEGACY_IMPORT_ROW_KINDS", "LEGACY_SOURCE_REQUIRED",
        "LEGACY_SOURCE_ENVELOPE_FACTS", "LEGACY_MANIFEST_REQUIRED",
        # 0.25.0, the carried-finding outcome vocabulary: a member grammar
        # of the claim, which the validator owns like every other claim
        # grammar; the adapters render it DERIVED. If the shipped docs come
        # to spell it out, it moves to RESTATEMENTS.
        "CARRIED_OUTCOMES",
    }

    @classmethod
    def _registered(cls):
        """Which authorities RESTATEMENTS actually binds — read from the
        inventory itself, never restated beside it.

        Round-4 F5: this was a second hand list, so it could certify an
        authority as registered when no entry existed. That is the same
        false-classification path as round-3 F3, one level up: a list
        asserting a fact about another list, with nothing comparing them.
        """
        by_value = {id(getattr(vocab, name)): name
                    for name in cls._closed_vocabularies()}
        return {by_value[id(entry[0])] for entry in cls.RESTATEMENTS.values()
                if id(entry[0]) in by_value}

    @staticmethod
    def _arrow_chain(region):
        """`request → verdict → disposition → authorization`: the loop's
        four envelope kinds, of which three are stamped. `verdict` is named
        there for the same reason it has a row in the specification's table
        — it is the one with no emitter — so the reader drops it rather
        than the sentence being reworded to suit a test."""
        return {part.strip() for part in region.split("→")
                if part.strip() and part.strip() != "verdict"}

    @classmethod
    def _stamped_rows(cls, region):
        """The reader table lists the stamped kinds AND one row for the
        deliberately unstamped verdict, whose `compared by` cell is a bare
        em dash. The unstamped row is the point of that line, so the reader
        recognises it rather than the document being reworded to suit."""
        return {row.split("|")[1].strip()
                for row in region.splitlines()
                if row.startswith("|") and "---" not in row
                and row.split("|")[3].strip() != "—"}

    @staticmethod
    def _json_keys(region):
        """A JSON object shown as an example: its top-level member names.
        The walkthrough's claim is minimal by design, so its keys are
        exactly the required set. Nested objects live inside the reference
        list, so bracketed regions are dropped before reading keys."""
        top = re.sub(r"\[.*?\]", "[]", region, flags=re.S)
        return {m.group(1) for m in re.finditer(r'"(\w+)":', top)}

    @staticmethod
    def _bare(region):
        return {region.strip()}

    @staticmethod
    def _alternates(region):
        return {part.strip() for part in region.split("|")}

    @staticmethod
    def _prose_list(region):
        """A sentence listing members in prose: `a, b, c and d`."""
        return {part.strip().strip("`")
                for chunk in region.replace(" and ", ", ").split(",")
                for part in [chunk] if part.strip()}

    # One copy of each helper. (Lineage 8, author-found while working the
    # adapters exclusion, declared as unrequested: the pair below had been
    # accidentally quadruplicated by earlier edits — four byte-identical
    # definitions, the last silently winning. Dead weight, no behaviour
    # change.)
    #: PURPOSE (shared by every use below): `RESTATEMENTS`' locators anchor
    #: on a literal phrase before their capture group — `"closed vocabulary
    #: of five..."`, `"the breakers\s*\("`, `"**Reviewer closure events**
    #: —"` — written with ordinary single spaces between words. A
    #: restructuring REFLOWS running prose, the same words wrapped at a
    #: different column, and three locators broke on exactly that (measured
    #: 2026-08-31, briefs/guards-that-read-prose-adjacency.md, instance 1):
    #: the phrase was intact but a newline had moved into the middle of it,
    #: which a literal space does not match. A fourth guard here,
    #: `test_the_stated_count_matches_the_mechanisms_it_counts`, failed the
    #: same way and reported "says five and enumerates 0". Coupling a
    #: locator to the exact column a phrase happened to wrap at is prose
    #: ADJACENCY, the same class of defect as the deferral guard converted
    #: in review/tests/test_deferral_resolution.py.
    #:
    #: The fix widens the LOCATOR, not the document: every literal space in
    #: an anchor pattern becomes `\s+` (one or more whitespace characters,
    #: which — unlike `.` — already matches a newline with no flag needed),
    #: so the same anchor matches whether its words share a line or a wrap
    #: put a newline between them. The document text is read verbatim,
    #: never rewritten, so nothing here can corrupt a fenced code block, a
    #: table, or a JSON capture the way collapsing the whole document's
    #: whitespace would.
    @staticmethod
    def _ws_tolerant(pattern):
        return pattern.replace(" ", r"\s+")

    def _members(self, text, pattern, reader):
        found = re.search(self._ws_tolerant(pattern), text, re.S)
        self.assertIsNotNone(
            found, f"the shipped document no longer carries this "
                   f"restatement where the inventory says it is; a "
                   f"restatement nothing can locate is one nothing checks")
        return getattr(self, reader)(found.group(1))

    def _document(self, name):
        """A shipped document by short name.

        The set searched here is `design` and `readme`. That it is the
        WHOLE set of shipped documents restating a runtime vocabulary is
        proved in `test_identity_boundary.py`, against the extraction
        inventory — which stays in the workbench, where a new travelling
        document appears and must be decided (round-4 F4). This half may
        not read that inventory: a travelling file that references a
        workbench-only path is a coupling the extraction audit refuses, and
        it refused this when the derivation lived here.
        """
        path = spec_path() if name == "design" else public_path("README.md")
        if path is None:
            self.skipTest(f"no {name} shipped in this tree")
        return path.read_text(encoding="utf-8")

    def test_every_registered_restatement_matches_its_authority(self):
        """FALSIFICATION for round-2 F3, the reviewer's own probe: delete
        the `escalated` row from the shipped disposition table. Before this
        round all six parity tests stayed green; now this names it.
        Mutation, the other direction: add a member to any authority
        without documenting it, and the same entry fails."""
        for name, entry in self.RESTATEMENTS.items():
            authority, document, pattern, reader = entry
            with self.subTest(restatement=name):
                documented = self._members(self._document(document),
                                           pattern, reader)
                expected = set(authority)
                self.assertEqual(
                    expected - documented, set(),
                    f"the shipped document omits {name} member(s) the code "
                    f"declares")
                self.assertEqual(
                    documented - expected, set(),
                    f"the shipped document states {name} member(s) the code "
                    f"does not declare")

    @staticmethod
    def _closed_vocabularies():
        return {name for name in dir(vocab)
                if name.isupper()
                and isinstance(getattr(vocab, name), tuple)
                and getattr(vocab, name)
                and all(isinstance(x, str) for x in getattr(vocab, name))}

    def test_every_closed_vocabulary_is_registered_or_declared_absent(self):
        """The inventory cannot fall behind its subject: every closed
        vocabulary the code exports is either registered above or listed as
        deliberately undocumented. A new one fails here until someone
        decides which."""
        closed = self._closed_vocabularies()
        self.assertEqual(
            sorted(closed - self._registered() - self.UNDOCUMENTED), [],
            "a closed vocabulary is neither registered as a shipped "
            "restatement nor declared deliberately undocumented")
        self.assertEqual(
            sorted(self._registered() - closed), [],
            "the inventory registers something that is no longer a closed "
            "vocabulary")
        self.assertEqual(
            sorted(self.UNDOCUMENTED & self._registered()), [],
            "a vocabulary is both registered and declared undocumented")

    #: How close together every member of a vocabulary must appear before
    #: the text counts as ENUMERATING it rather than mentioning its members
    #: in passing. A member list is tight; incidental co-occurrence across a
    #: long table is not.
    ENUMERATION_SPAN = 400

    def _enumerates(self, text, members):
        """Whether `text` appears to spell out every member in one place."""
        if len(members) < 2:
            # A one-member vocabulary has no member LIST to find: the member
            # appearing in prose is a mention, not an enumeration, and
            # treating it as one would make every such vocabulary
            # permanently unregisterable.
            return False
        starts = []
        for member in members:
            found = [m.start() for m in re.finditer(re.escape(member), text)]
            if not found:
                return False
            starts.append(min(found))
        return max(starts) - min(starts) <= self.ENUMERATION_SPAN

    def test_every_document_that_enumerates_a_vocabulary_has_an_entry(self):
        """FALSIFICATION for round-3 F2 and F3 together, and the reason the
        first two attempts at this inventory were incomplete.

        Round 2's inventory was a list of entries, so it was exactly as
        complete as whoever wrote it: it omitted the README (F2) and it
        declared four vocabularies undocumented that the specification
        spells out in full (F3). Both failures share a shape — a hand list
        cannot discover what it left out.

        So the cross product is searched instead: every shipped document
        against every closed vocabulary. Wherever a document enumerates
        one, `RESTATEMENTS` must carry an entry for that exact pair. This
        is a TRIPWIRE, not a proof, and worth being exact about which: it
        recognises a member list by every member appearing within
        `ENUMERATION_SPAN` characters, and it can miss a list written in
        some shape it does not recognise. What it cannot do is let the
        README's breaker list, or the specification's finding fields, go
        unregistered again.

        Mutations, each a live state of this code: delete the
        `breakers (readme)` entry, or move any registered vocabulary into
        UNDOCUMENTED, and this fails naming the pair.
        """
        registered = {(entry[0], entry[1])
                      for entry in self.RESTATEMENTS.values()}
        documents = {name: self._document(name)
                     for name in ("design", "readme")}
        # The derived set and the searched set are the same set — asserted
        # by `test_every_shipped_document_is_searched_or_excluded_by_rule`,
        # so this walk cannot silently cover less than what ships.
        missing = []
        for name in sorted(self._closed_vocabularies()):
            members = getattr(vocab, name)
            for doc_name, text in documents.items():
                if not self._enumerates(text, members):
                    continue
                if (members, doc_name) not in registered:
                    missing.append(f"{name} in {doc_name}")
        self.assertEqual(
            missing, [],
            "shipped document(s) enumerate a closed vocabulary that the "
            "restatement inventory does not bind to that document: an "
            "inventory that cannot discover what it omits is complete only "
            "against its own omissions")

    def test_every_registered_document_is_retrievable(self):
        """Each entry names a document and a location, and both must
        resolve — a locator that matches nothing is a restatement nobody
        checks, which is indistinguishable from having no entry at all."""
        for name, entry in self.RESTATEMENTS.items():
            _authority, document, pattern, _reader = entry
            with self.subTest(restatement=name):
                text = self._document(document)
                self.assertIsNotNone(
                    re.search(self._ws_tolerant(pattern), text, re.S),
                    f"{name}: the locator matches nothing in {document}")

    def test_control_the_widened_locator_survives_a_reflowed_anchor(self):
        """CONTROL for `_ws_tolerant`: a minimal reproduction of the real
        defect it fixes. `raw_pattern` below is `dispositions`' own anchor
        text; `reflowed` puts a newline exactly where a restructuring put
        one in the real document 2026-08-31 — in the middle of "of five",
        not touching a single word. The un-widened pattern is proved to be
        the one that breaks (so this is testing the actual failure mode,
        not a strawman); `_ws_tolerant` is proved to survive it and still
        read the table correctly."""
        raw_pattern = r"closed vocabulary of five.*?\n\n(\|.*?)\n\n"
        # Table shape matches `_table_keys`' own contract: one member per
        # row, named in the first column.
        reflowed = ("closed vocabulary of\nfive, stated below.\n\n"
                    "| `a` | x |\n|---|---|\n| `b` | y |\n\n")
        self.assertIsNone(
            re.search(raw_pattern, reflowed, re.S),
            "setup is wrong: the raw pattern must be the one that breaks "
            "on this reflow, or the control proves nothing")
        found = re.search(self._ws_tolerant(raw_pattern), reflowed, re.S)
        self.assertIsNotNone(found, "the widened locator must survive a "
                             "reflow that moved only whitespace")
        self.assertEqual(self._table_keys(found.group(1)), {"a", "b"})

    def test_mutation_a_genuinely_missing_member_is_still_caught(self):
        """MUTATION, paired with the control above: widening the locator
        must not also widen what counts as a complete restatement. Reflow
        AND drop member `b` from the same synthetic table, and the
        widened locator must still report only `a` — not silently accept
        the reflow as cover for a real omission."""
        raw_pattern = r"closed vocabulary of five.*?\n\n(\|.*?)\n\n"
        reflowed_missing_b = ("closed vocabulary of\nfive, stated below."
                              "\n\n| `a` | x |\n|---|---|\n\n")
        found = re.search(self._ws_tolerant(raw_pattern), reflowed_missing_b,
                          re.S)
        self.assertIsNotNone(found)
        self.assertEqual(self._table_keys(found.group(1)), {"a"})

    def test_the_stated_count_matches_the_mechanisms_it_counts(self):
        """§3.3's count, derived from the headings it counts. Kept from
        round 1: a count is a restatement whose authority is the document's
        own structure rather than a vocabulary."""
        section = self._section()
        stated = re.search(
            self._ws_tolerant(r"^(\w+) mechanisms, all deterministic"),
            section, re.M)
        self.assertIsNotNone(stated, "§3.3 no longer opens with a count")
        words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
        word = stated.group(1).lower()
        self.assertIn(word, words, f"unrecognised count word {word!r}")
        headings = re.findall(r"^\*\*\(([a-z])\) ", section, re.M)
        self.assertEqual(words[word], len(headings),
                         f"§3.3 says {word} and enumerates {len(headings)}")

    def test_the_mechanism_letters_are_gapless(self):
        headings = re.findall(r"^\*\*\(([a-z])\) ", self._section(), re.M)
        self.assertTrue(headings)
        self.assertEqual(headings,
                         [chr(ord("a") + i) for i in range(len(headings))])

    def _section(self):
        text = self._document("design")
        start = text.index("### 3.3 ")
        return text[start:text.index("### 3.4 ", start)]

    #: The wrapper attributes are a restatement too, but their authority is
    #: the emitters rather than a vocabulary tuple — read from the emitters,
    #: never from a fixture, because a fixture is one more hand-kept copy.
    OPEN_TAG = re.compile(r"-review-(?:request|disposition)(.*?)>['\"]", re.S)

    def _emitted_attributes(self):
        found = set()
        for module in (REPO_ROOT / "review" / "emit.py",
                       REPO_ROOT / "review" / "wire.py"):
            source = module.read_text(encoding="utf-8")
            for tag in self.OPEN_TAG.findall(source):
                found |= set(re.findall(r'(\w+)="\{', tag))
        self.assertTrue(found, "no wrapper open tag found in the emitters")
        from review import wire
        live = wire.parse_disposition(wire.emit_disposition(
            tag=TOOL_NAME, verdict_sha="a" * 40, head="b" * 40,
            author="claude", round_no=1, dispositions=[])).attrs
        self.assertTrue(set(live) <= found,
                        f"a live emission carries {sorted(set(live) - found)}, "
                        f"which the source scan missed")
        return found

    def test_the_specification_enumerates_every_emitted_attribute(self):
        text = self._document("design")
        found = re.search(r"The wrapper's own attribute text is closed the "
                          r"same way[^\n]*\n(.*?)occurring once each",
                          text, re.S)
        self.assertIsNotNone(found, "§3.1 no longer enumerates the wrapper "
                                    "attributes in the shape this checks")
        listed = set(re.findall(r"`([a-z_]+)`", found.group(1)))
        self.assertEqual(self._emitted_attributes() - listed, set())
        self.assertEqual(listed - self._emitted_attributes(), set())


class TestInstall(unittest.TestCase):
    """The deploy the gate could not be.

    `--check` guards the TRACKED adapters. What an agent loads is
    `~/.claude/skills/loupe/SKILL.md` and its Codex twin, and until now
    nothing wrote those but a person with `cp`: on 2026-08-19 both were found
    rounds behind the source, advertising a `respond` step with no
    falsification record and a verb table missing `waive`. A rendered
    procedure nobody reads is not a procedure.

    FALSIFICATIONS: drop the equality check in `install_all` and the
    idempotence assertion fails; drop the retention and `kept` no longer
    names existing bytes; let `--install` run with a stale tracked copy and
    the refusal test fails.
    """

    def setUp(self):
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="adapters-install-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.rendered = self.tmp / "rendered"
        adapters.render_all(self.rendered)
        self.home = self.tmp / "home"
        self.targets = {kind: self.home / kind / "SKILL.md"
                        for kind in adapters.OUTPUTS}
        self.keep = self.tmp / "keep"

    def _install(self):
        return {r["kind"]: r for r in adapters.install_all(
            self.rendered, targets=self.targets, keep_dir=self.keep)}

    def test_install_is_idempotent_and_reports_every_target(self):
        first = self._install()
        self.assertEqual({r["status"] for r in first.values()}, {"installed"})
        for kind, target in self.targets.items():
            self.assertEqual(target.read_text(encoding="utf-8"),
                             adapters.render(kind))
        second = self._install()
        self.assertEqual({r["status"] for r in second.values()}, {"unchanged"})
        # Unchanged means untouched, not rewritten with the same bytes.
        self.assertFalse(any("kept" in r for r in second.values()))

    def test_a_locally_edited_copy_is_kept_and_named_never_lost(self):
        self._install()
        edited = self.targets["claude-skill"]
        edited.write_text("hand-edited by someone\n", encoding="utf-8")
        row = self._install()["claude-skill"]
        self.assertEqual(row["status"], "replaced")
        self.assertEqual(row["replaced_digest"],
                         sha256_text("hand-edited by someone\n"))
        kept = Path(row["kept"])
        self.assertTrue(kept.is_file(), row)
        self.assertEqual(kept.read_text(encoding="utf-8"),
                         "hand-edited by someone\n")
        self.assertEqual(edited.read_text(encoding="utf-8"),
                         adapters.render("claude-skill"))

    def test_retention_is_a_precondition_of_replacement(self):
        """Round-9 F1: retention used to degrade to a `"not kept (…)"` string
        and the overwrite proceeded anyway, so in exactly the states where
        preservation mattered — no keep directory, an unwritable one, a keep
        path occupied by something else — an author's hand edit was destroyed
        and the row called it `replaced`. The old test codified that loss as
        success. Every destination state is now partitioned, and any state
        that cannot PROVE preservation refuses and leaves the target alone.
        """
        edited = "hand-edited and not reproducible\n"
        kind = "claude-skill"
        target = self.targets[kind]
        digest_name = f"{kind}-{sha256_text(edited)[:12]}.md"

        def attempt(keep_dir):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(edited, encoding="utf-8")
            rows = {r["kind"]: r for r in adapters.install_all(
                self.rendered, targets={kind: target}, keep_dir=keep_dir)}
            return rows[kind]

        # 1. No retention directory at all.
        row = attempt(None)
        self.assertEqual(row["status"], "failed", row)
        self.assertFalse(row.get("preserved", False))
        self.assertEqual(target.read_text(encoding="utf-8"), edited,
                         "the target must be untouched when nothing was kept")

        # 2. A retention path that cannot be written: occupied by a file.
        blocked = self.tmp / "blocked-keep"
        blocked.write_text("not a directory\n", encoding="utf-8")
        row = attempt(blocked)
        self.assertEqual(row["status"], "failed", row)
        self.assertEqual(target.read_text(encoding="utf-8"), edited)

        # 3. A retention path that already holds DIFFERENT bytes under this
        #    digest's name is not evidence of anything.
        conflicting = self.tmp / "conflicting-keep"
        conflicting.mkdir()
        (conflicting / digest_name).write_text("something else\n",
                                               encoding="utf-8")
        row = attempt(conflicting)
        self.assertEqual(row["status"], "failed", row)
        self.assertEqual(target.read_text(encoding="utf-8"), edited)

        # 3b. A retention path holding bytes that are not UTF-8 at all: a
        #     read failure is a refusal like any other, not an escape that
        #     takes the whole installer result with it (round-10 F1).
        undecodable = self.tmp / "undecodable-keep"
        undecodable.mkdir()
        (undecodable / digest_name).write_bytes(b"\xff\xfe\x00")
        row = attempt(undecodable)
        self.assertEqual(row["status"], "failed", row)
        self.assertEqual(target.read_text(encoding="utf-8"), edited)

        # 4. A fresh, writable destination: kept, proven, then replaced.
        row = attempt(self.keep)
        self.assertEqual(row["status"], "replaced", row)
        kept = Path(row["kept"])
        self.assertEqual(kept.read_text(encoding="utf-8"), edited)
        self.assertEqual(target.read_text(encoding="utf-8"),
                         adapters.render(kind))

        # 5. An existing copy holding exactly those bytes is the idempotent
        #    success: kept, not rewritten, replacement proceeds.
        row = attempt(self.keep)
        self.assertEqual(row["status"], "replaced", row)
        self.assertEqual(Path(row["kept"]), kept)
        self.assertEqual(len(list(self.keep.iterdir())), 1)

        # Controls: retention is required only where bytes would be lost.
        # A fresh install has none, and an unchanged target replaces none —
        # both succeed with no retention directory at all.
        fresh = self.tmp / "fresh" / "SKILL.md"
        rows = {r["kind"]: r for r in adapters.install_all(
            self.rendered, targets={kind: fresh}, keep_dir=None)}
        self.assertEqual(rows[kind]["status"], "installed")
        rows = {r["kind"]: r for r in adapters.install_all(
            self.rendered, targets={kind: fresh}, keep_dir=None)}
        self.assertEqual(rows[kind]["status"], "unchanged")

    def test_a_kept_copy_that_does_not_read_back_is_not_preservation(self):
        """The kept copy is proof only if it is proven. A destination that
        accepts the write and then returns something else — a full disk that
        truncates, a path that is not what it appeared to be — must refuse
        like any other retention failure, not be trusted because `write_text`
        did not raise. Fault-injected, because a real filesystem that lies is
        exactly the state a read-back exists to catch."""
        import unittest.mock
        edited = "hand-edited and not reproducible\n"
        kind = "claude-skill"
        target = self.targets[kind]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(edited, encoding="utf-8")
        # The read-back is `read_bytes` since round-1 F2 of the 0.25.0
        # review (a text read-back proved nothing about line endings), so
        # that is the method the lying destination stands in for.
        real_read = Path.read_bytes

        def truncating(self_path, *a, **k):
            if Path(self_path).parent == self.keep:
                return b"truncated"
            return real_read(self_path, *a, **k)

        with unittest.mock.patch.object(Path, "read_bytes", truncating):
            rows = {r["kind"]: r for r in adapters.install_all(
                self.rendered, targets={kind: target}, keep_dir=self.keep)}
        self.assertEqual(rows[kind]["status"], "failed", rows)
        self.assertEqual(target.read_text(encoding="utf-8"), edited,
                         "an unproven copy must not license the overwrite")

        # And a read-back that cannot be answered at all is the same
        # refusal, not an escaping exception: the kept copy is proof only if
        # reading it back is a question with an answer (round-10 F1, second
        # handler). Nothing decodes any more, so the unanswerable read is an
        # I/O error rather than a UnicodeDecodeError.
        def unreadable(self_path, *a, **k):
            if Path(self_path).parent == self.keep:
                raise OSError(5, "Input/output error", str(self_path))
            return real_read(self_path, *a, **k)

        for stray in self.keep.iterdir():
            stray.unlink()
        with unittest.mock.patch.object(Path, "read_bytes", unreadable):
            rows = {r["kind"]: r for r in adapters.install_all(
                self.rendered, targets={kind: target}, keep_dir=self.keep)}
        self.assertEqual(rows[kind]["status"], "failed", rows)
        self.assertEqual(target.read_text(encoding="utf-8"), edited)

    def test_an_explicit_empty_scope_is_zero_targets(self):
        """Round-9 F3: `targets or install_targets()` made `{}` — an explicit,
        bounded scope — expand into the real user-level agent directories.
        `None` means the production defaults; a mapping means exactly those,
        and an empty mapping is exactly none."""
        import unittest.mock
        unexpected = self.tmp / "must-not-exist" / "SKILL.md"
        with unittest.mock.patch.object(
                adapters, "install_targets",
                lambda: {"codex-skill": unexpected}):
            self.assertEqual(
                adapters.install_all(self.rendered, targets={},
                                     keep_dir=self.keep), [])
            self.assertEqual(
                adapters.check_install(self.rendered, targets={}), [])
            self.assertFalse(unexpected.exists(),
                             "an empty scope must touch nothing")
            self.assertFalse(unexpected.parent.exists())
            # None is the production default, and it is reached.
            rows = adapters.install_all(self.rendered, targets=None,
                                        keep_dir=self.keep)
            self.assertEqual([r["kind"] for r in rows], ["codex-skill"])
            self.assertTrue(unexpected.is_file())
            self.assertEqual(
                [r["kind"] for r in adapters.check_install(self.rendered,
                                                           targets=None)],
                ["codex-skill"])
        # And a nonempty mapping touches only what it names.
        only = self.tmp / "only" / "SKILL.md"
        rows = adapters.install_all(self.rendered,
                                    targets={"claude-skill": only},
                                    keep_dir=self.keep)
        self.assertEqual([r["target"] for r in rows], [str(only)])

    def test_check_install_partitions_absent_stale_and_in_sync(self):
        rows = {r["kind"]: r for r in adapters.check_install(
            self.rendered, targets=self.targets)}
        self.assertEqual({r["status"] for r in rows.values()}, {"absent"})
        self._install()
        rows = {r["kind"]: r for r in adapters.check_install(
            self.rendered, targets=self.targets)}
        self.assertEqual({r["status"] for r in rows.values()}, {"in_sync"})
        self.targets["claude-skill"].write_text("older render\n",
                                                encoding="utf-8")
        rows = {r["kind"]: r for r in adapters.check_install(
            self.rendered, targets=self.targets)}
        self.assertEqual(rows["claude-skill"]["status"], "stale")
        self.assertEqual(rows["codex-skill"]["status"], "in_sync")
        self.assertNotEqual(rows["claude-skill"]["source_digest"],
                            rows["claude-skill"]["target_digest"])

    def test_check_install_reports_source_absent_and_names_the_source(self):
        """RVW-T21 D1: a missing SOURCE must never surface as the healthy
        TARGET. `source_absent` is distinct from the target-side `absent`,
        and the row carries `source` (never omits it the way the old
        `target`-only, no-`source` row did) — with no target-side keys,
        since the target was never even looked at.

        FALSIFICATION: revert the `is_file()` guard in `check_install` (fold
        the branch back into the bare `try: source.read_text(...)`) and this
        fails — the status reverts to the old undifferentiated `unreadable`
        with no `source` key.
        """
        missing = self.tmp / "no-such-rendered-dir"
        rows = {r["kind"]: r for r in adapters.check_install(
            missing, targets=self.targets)}
        for kind, row in rows.items():
            self.assertEqual(row["status"], "source_absent", row)
            self.assertEqual(row["source"],
                             str(missing / adapters.OUTPUTS[kind]))
            self.assertEqual(row["target"], str(self.targets[kind]))
            self.assertNotIn("source_digest", row)
            self.assertNotIn("target_digest", row)

    def test_check_install_reports_source_unreadable_distinctly(self):
        """The adjacent state: a source FILE that exists but cannot be read
        (permission denied) is `source_unreadable`, not `source_absent` and
        not the target-side `unreadable` — and it still names the `source`.
        Self-skips under a user that ignores file-mode permissions (root,
        some containers), following the convention in
        `test_cli_exits.test_an_unreadable_config_is_structured_too`.

        FALSIFICATION: revert the `is_file()` guard (same mutation as
        above) and the status reverts to plain `unreadable` with no
        `source` key — carrying the target's healthy path instead.
        """
        import os
        import stat
        kind = "claude-skill"
        source_path = self.rendered / adapters.OUTPUTS[kind]
        source_path.chmod(0o000)
        try:
            if os.access(source_path, os.R_OK):
                self.skipTest("this process can read a 0o000 file "
                              "(likely running as root)")
            rows = {r["kind"]: r for r in adapters.check_install(
                self.rendered, targets={kind: self.targets[kind]})}
        finally:
            source_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        row = rows[kind]
        self.assertEqual(row["status"], "source_unreadable", row)
        self.assertEqual(row["source"], str(source_path))
        self.assertIn("error", row)

    def test_a_partial_install_reports_every_target_it_touched(self):
        """Round-9 F2: the failure branch filtered to failed rows, so a run
        that installed one target and then failed on another reported only
        the failure — a partial deployment indistinguishable from none
        without a filesystem audit, and the successful replacement's digest
        and kept path lost with it. A non-zero exit does not mean the command
        was side-effect-free, and the operator recovering from it needs what
        it DID do."""
        import argparse
        import json
        import unittest.mock
        cfg = config.load(REPO_ROOT)
        blocker = self.tmp / "not-a-directory"
        blocker.write_text("x\n", encoding="utf-8")

        def run(targets):
            args = argparse.Namespace(check=False, install=True,
                                      check_install=False,
                                      dir=str(self.rendered))
            buf = io.StringIO()
            with unittest.mock.patch.object(adapters, "install_targets",
                                            lambda: targets), \
                    contextlib.redirect_stdout(buf):
                code = cli.cmd_render_adapters(args, cfg)
            return code, json.loads(buf.getvalue())

        # Sorted order is claude-skill, codex-skill: the good one runs first.
        good = self.tmp / "good" / "SKILL.md"
        code, payload = run({"claude-skill": good,
                             "codex-skill": blocker / "SKILL.md"})
        self.assertNotEqual(code, 0)
        self.assertEqual(payload["next_kind"], "blocked")
        self.assertTrue(good.is_file(), "the first target really was written")
        self.assertIn(str(good), json.dumps(payload),
                      "a written target must be named in the failure result")
        rows = {r["kind"]: r for r in payload["installed"]}
        self.assertEqual(rows["claude-skill"]["status"], "installed")
        self.assertEqual(rows["codex-skill"]["status"], "failed")

        # Failure BEFORE any write: the failing target sorts first, and the
        # result still accounts for every target attempted.
        second = self.tmp / "second" / "SKILL.md"
        code, payload = run({"claude-skill": blocker / "SKILL.md",
                             "codex-skill": second})
        self.assertNotEqual(code, 0)
        rows = {r["kind"]: r for r in payload["installed"]}
        self.assertEqual(rows["claude-skill"]["status"], "failed")
        self.assertEqual(rows["codex-skill"]["status"], "installed")

        # Failure after a RETAINED replacement: the kept path survives into
        # the blocked payload, because that is the recovery pointer.
        edited = self.tmp / "edited" / "SKILL.md"
        edited.parent.mkdir(parents=True, exist_ok=True)
        edited.write_text("local edit\n", encoding="utf-8")
        code, payload = run({"claude-skill": edited,
                             "codex-skill": blocker / "SKILL.md"})
        self.assertNotEqual(code, 0)
        rows = {r["kind"]: r for r in payload["installed"]}
        self.assertEqual(rows["claude-skill"]["status"], "replaced")
        self.assertEqual(Path(rows["claude-skill"]["kept"]).read_text(
            encoding="utf-8"), "local edit\n")

        # All-success control: same shape, ok, every row present.
        code, payload = run({"claude-skill": self.tmp / "a" / "SKILL.md",
                             "codex-skill": self.tmp / "b" / "SKILL.md"})
        self.assertEqual(code, 0, payload)
        self.assertEqual({r["status"] for r in payload["installed"]},
                         {"installed"})

    def test_auxiliary_payload_cannot_forge_the_non_zero_contract(self):
        """Round-10 F2: `--install` needed a refusal that carries the targets
        it already wrote, so `_blocked` gained `extra` — as an unrestricted
        `payload.update`, which handed every caller authority over the fields
        `_blocked` exists to own. A refusal could then report `ok: true`,
        exit 0, or a `next` command nothing selected, which is exactly what
        an agent acts on. Auxiliary data adds; it never redefines.

        The installer's own call is the reason this helper was widened, so
        the partition lives beside it: omitted, empty, non-colliding, and a
        collision with each reserved field, through both recoveries."""
        import json
        from review import cli

        def call(**kw):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = cli._blocked("", "must stop", **kw)
            return code, json.loads(buf.getvalue())

        # Round 5 F1: `next` takes a rendered command, so the fixture
        # renders one — and the forgery below still arrives as a raw
        # string through `extra`, which is the thing this test is about.
        from review import paths
        handoff = paths.command(*paths.lits(TOOL_NAME, "handoff"))

        def command(**kw):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = cli._blocked(handoff, "try again", **kw)
            return code, json.loads(buf.getvalue())

        for label, recovery, expect in (("blocked", call, ("blocked", None)),
                                        ("command", command,
                                         ("command",
                                          f"{TOOL_NAME} handoff"))):
            # Omitted and empty: unchanged behaviour.
            for kw in ({}, {"extra": {}}, {"extra": None}):
                code, p = recovery(**kw)
                self.assertNotEqual(code, 0, (label, kw))
                self.assertIs(p["ok"], False)
                self.assertEqual(p["next_kind"], expect[0])
                self.assertEqual(p["next"], expect[1])
                self.assertNotIn("ignored_control_fields", p)
            # Non-colliding: carried through untouched.
            code, p = recovery(extra={"installed": [{"kind": "codex-skill"}]})
            self.assertEqual(p["installed"], [{"kind": "codex-skill"}])
            self.assertIs(p["ok"], False)
            self.assertEqual(p["next_kind"], expect[0])
            # Every reserved field, one at a time and all at once.
            forgeries = {"ok": True, "exit": 0, "error": "forged",
                         "next": "rm -rf /", "next_kind": "command",
                         "remedy": "forged"}
            for field, value in forgeries.items():
                code, p = recovery(extra={field: value,
                                          "installed": ["kept"]})
                self.assertNotEqual(code, 0, (label, field))
                self.assertIs(p["ok"], False, field)
                self.assertEqual(p["exit"], code, field)
                self.assertEqual(p["error"], "must stop"
                                 if label == "blocked" else "try again")
                self.assertEqual(p["next_kind"], expect[0], field)
                self.assertEqual(p["next"], expect[1], field)
                self.assertEqual(p["ignored_control_fields"], [field])
                self.assertEqual(p["installed"], ["kept"],
                                 "auxiliary data still travels")
            code, p = recovery(extra=dict(forgeries))
            self.assertIs(p["ok"], False)
            self.assertEqual(p["next"], expect[1])
            self.assertEqual(p["ignored_control_fields"],
                             sorted(forgeries))
            self.assertEqual(set(cli.CONTROL_FIELDS), set(forgeries),
                             "the reserved set and the forgeries agree")

    def test_the_verb_refuses_to_install_a_stale_tracked_copy(self):
        """Deploying a stale adapter everywhere spreads the drift this verb
        exists to end. The refusal names the render as the next command."""
        import argparse
        import json
        import unittest.mock
        (self.rendered / adapters.OUTPUTS["codex-skill"]).write_text(
            "stale\n", encoding="utf-8")
        cfg = config.load(REPO_ROOT)
        args = argparse.Namespace(check=False, install=True,
                                  check_install=False,
                                  dir=str(self.rendered))
        buf = io.StringIO()
        with unittest.mock.patch.object(adapters, "install_targets",
                                        lambda: self.targets), \
                contextlib.redirect_stdout(buf):
            code = cli.cmd_render_adapters(args, cfg)
        self.assertNotEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["next_kind"], "command")
        self.assertEqual(payload["next"], f"{TOOL_NAME} render-adapters")
        self.assertFalse(any(t.exists() for t in self.targets.values()),
                         "nothing may be installed from a stale source")

    def test_the_verb_installs_and_check_install_then_agrees(self):
        import argparse
        import json
        import unittest.mock
        cfg = config.load(REPO_ROOT)

        def run(**flags):
            modes = {"check": False, "install": False, "check_install": False}
            modes.update(flags)
            args = argparse.Namespace(dir=str(self.rendered), **modes)
            buf = io.StringIO()
            with unittest.mock.patch.object(adapters, "install_targets",
                                            lambda: self.targets), \
                    contextlib.redirect_stdout(buf):
                code = cli.cmd_render_adapters(args, cfg)
            return code, json.loads(buf.getvalue())

        code, payload = run(check_install=True)
        self.assertNotEqual(code, 0, "an absent install is drift")
        self.assertEqual(payload["next"],
                         f"{TOOL_NAME} render-adapters --install")
        code, payload = run(install=True)
        self.assertEqual(code, 0, payload)
        self.assertEqual({r["status"] for r in payload["installed"]},
                         {"installed"})
        code, payload = run(check_install=True)
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["ok"])


#: The environment names a scratch run must not inherit: this suite is itself
#: run BY a gate (`tests`), which marks its children, and the host may carry
#: a CODEX_HOME, a state directory or a transport declaration of its own.
_INHERITED = (env_var("IN_GATE_RUN"), env_var("GATE_HEAD"),
              env_var("GATE_BASE"), env_var("STATE_DIR"), env_var("CONFIG"),
              "CODEX_HOME", "PYTHONPATH", "PYTHONSAFEPATH")


def scratch_env(home: Path, state: Path, **extra) -> dict:
    """HOME and the state directory redirected into the test's own temp
    tree, every inherited gate or Codex name removed, then `extra`."""
    env = {k: v for k, v in os.environ.items() if k not in _INHERITED}
    env["HOME"] = str(home)
    env[env_var("STATE_DIR")] = str(state)
    env.update(extra)
    return env


def run_loupe(argv, *, cwd, env, launcher=None):
    """The REAL entry point as a subprocess — `bin/loupe` of this tree
    unless `launcher` names another — returning (exit, parsed JSON)."""
    cmd = list(launcher or [str(REPO_ROOT / "bin" / "loupe")])
    proc = subprocess.run([*cmd, *argv], cwd=cwd, env=env,
                          capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=120)
    try:
        return proc.returncode, json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise AssertionError(f"not JSON (exit {proc.returncode}): "
                             f"{proc.stdout!r} {proc.stderr!r}") from None


class _Scratch(unittest.TestCase):
    """A temp tree with a HOME, a state directory and an unrelated cwd."""

    def setUp(self):
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="adapters-scratch-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.state = self.tmp / "state"
        self.cwd = self.tmp / "elsewhere"
        for d in (self.home, self.state, self.cwd):
            d.mkdir()

    def env(self, **extra):
        return scratch_env(self.home, self.state, **extra)

    def loupe(self, *argv, env=None, launcher=None):
        return run_loupe(list(argv), cwd=self.cwd, env=env or self.env(),
                         launcher=launcher)

    @property
    def documented(self):
        return self.home / ".agents" / "skills" / TOOL_NAME / "SKILL.md"

    @property
    def claude(self):
        return self.home / ".claude" / "skills" / TOOL_NAME / "SKILL.md"

    def legacy_at(self, root: Path, text="an older loupe skill\n",
                  extra=None) -> Path:
        path = root / "skills" / TOOL_NAME / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        for name in extra or ():
            (path.parent / name).write_text("someone's notes\n",
                                             encoding="utf-8")
        return path


class TestInstallIndependence(_Scratch):
    """D (0.25.0, the user's ruling 5 of 2026-09-21): no adopter step may
    depend on a same-machine scenario. A machine holding only the adopting
    repository and the published pin must install and verify its adapters.

    Measured on 0.24.1 under `uv tool install`: `--check-install` and
    `--install` with no `--dir` refused, "no adapters/ directory beside the
    installed package". So these tests run the package ALONE — `review/`
    copied into a temp dir, with no `adapters/`, no `bin/` and no checkout
    anywhere near it — from an unrelated cwd, HOME redirected. The source is
    now the running package's rendering; a directory planted where the old
    default looked must change nothing.

    FALSIFICATION. Mutations: restore the package-sibling default (the
    0.24.x `directory = installation_root() / "adapters"`) and the
    package-alone test refuses where it must install; source a no-`--dir`
    row from any directory and the planted-marker test sees the marker.
    """

    def setUp(self):
        super().setUp()
        self.pkg = self.tmp / "site"
        shutil.copytree(REPO_ROOT / "review", self.pkg / "review",
                        ignore=shutil.ignore_patterns("tests", "__pycache__"))
        self.launcher = [sys.executable, "-B", "-m", "review"]

    def alone(self, *argv):
        return self.loupe(*argv, launcher=self.launcher,
                          env=self.env(PYTHONPATH=str(self.pkg),
                                       PYTHONSAFEPATH="1"))

    def test_the_package_alone_installs_and_verifies_its_adapters(self):
        self.assertFalse((self.pkg / "adapters").exists())
        label = (f"rendered by {TOOL_NAME} {TOOL_VERSION} at "
                 f"{(self.pkg / 'review').resolve()}")
        code, before = self.alone("render-adapters", "--check-install")
        self.assertEqual(code, 1, before)
        self.assertEqual(before["next"],
                         f"{TOOL_NAME} render-adapters --install")
        self.assertEqual({r["status"] for r in before["install"]},
                         {"absent"})
        code, done = self.alone("render-adapters", "--install")
        self.assertEqual(code, 0, done)
        self.assertEqual({r["status"] for r in done["installed"]},
                         {"installed"})
        self.assertEqual({r["source"] for r in done["installed"]}, {label})
        self.assertEqual(self.claude.read_text(encoding="utf-8"),
                         adapters.render("claude-skill"))
        self.assertEqual(self.documented.read_text(encoding="utf-8"),
                         adapters.render("codex-skill"))
        code, after = self.alone("render-adapters", "--check-install")
        self.assertEqual(code, 0, after)
        self.assertEqual({r["status"] for r in after["install"]},
                         {"in_sync"})
        self.assertEqual({r["source"] for r in after["install"]}, {label})

    def test_a_directory_where_the_old_default_looked_changes_nothing(self):
        """The synthetic marker: rendered-looking files planted beside the
        package (the 0.24.x default) and under the cwd (the pre-RVW-T21
        one). Neither may reach any no-`--dir` output, byte for byte."""
        self.alone("render-adapters", "--install")
        code, clean = self.alone("render-adapters", "--check-install")
        self.assertEqual(code, 0, clean)
        for root in (self.pkg, self.cwd):
            for rel in adapters.OUTPUTS.values():
                planted = root / adapters.ADAPTERS_DIR / rel
                planted.parent.mkdir(parents=True, exist_ok=True)
                planted.write_text("SYNTHETIC MARKER\n", encoding="utf-8")
        code, planted = self.alone("render-adapters", "--check-install")
        self.assertEqual(code, 0, planted)
        self.assertEqual(planted, clean)
        code, reinstall = self.alone("render-adapters", "--install")
        self.assertEqual({r["status"] for r in reinstall["installed"]},
                         {"unchanged"})
        self.assertNotIn("SYNTHETIC",
                         self.documented.read_text(encoding="utf-8"))

    def test_dir_keeps_the_directory_sourced_behaviour(self):
        """The paired control: `--dir` still reads the directory, names it
        in every row, reports `source_absent` for a missing one and refuses
        to install a stale tracked copy."""
        rendered = self.tmp / "rendered"
        adapters.render_all(rendered)
        code, done = self.alone("render-adapters", "--install",
                                "--dir", str(rendered))
        self.assertEqual(code, 0, done)
        self.assertEqual(
            {r["source"] for r in done["installed"]},
            {str(rendered / adapters.OUTPUTS[k])
             for k in ("claude-skill", "codex-skill")})
        (rendered / adapters.OUTPUTS["codex-skill"]).write_text(
            "stale\n", encoding="utf-8")
        code, refused = self.alone("render-adapters", "--install",
                                   "--dir", str(rendered))
        self.assertEqual(code, 1, refused)
        self.assertEqual(refused["next"], f"{TOOL_NAME} render-adapters")
        code, drift = self.alone("render-adapters", "--check-install",
                                 "--dir", str(rendered))
        self.assertEqual(code, 1, drift)
        rows = {r["kind"]: r for r in drift["install"]}
        self.assertEqual(rows["codex-skill"]["status"], "stale")
        code, missing = self.alone("render-adapters", "--check-install",
                                   "--dir", str(self.tmp / "nothing"))
        self.assertEqual(code, 1, missing)
        self.assertEqual({r["status"] for r in missing["install"]},
                         {"source_absent"})

    def test_check_and_render_keep_the_cwd_relative_default(self):
        """`--check` and the bare render are repo-local and keep
        `<repo>/adapters`: an empty one there is all-stale, and the
        refusal names that directory, not the package."""
        import argparse
        cfg = argparse.Namespace(repo_root=self.tmp / "cwd-repo",
                                 ledger_dir=self.state)
        (cfg.repo_root / adapters.ADAPTERS_DIR).mkdir(parents=True)
        args = argparse.Namespace(check=True, install=False,
                                  check_install=False, dir=None,
                                  check_embedded=None, write_embedded=None)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.cmd_render_adapters(args, cfg)
        payload = json.loads(buf.getvalue())
        self.assertNotEqual(code, 0)
        self.assertEqual(payload["next"], f"{TOOL_NAME} render-adapters")
        self.assertIn(str(cfg.repo_root), payload["error"])


class TestCodexSkillPath(_Scratch):
    """G (public issue #1), ruled 2026-09-21: OpenAI's documentation
    decides, and it names `$HOME/.agents/skills` as the user scope. Codex
    also lists `$CODEX_HOME/skills` (measured the same day), and lists one
    name twice when both hold it — so a legacy `~/.codex/skills/loupe/`
    copy is moved OUT of discovery by `--install`, kept first, and reported
    as drift by `--check-install` while it is still there.

    Every row runs `bin/loupe` as a subprocess with HOME redirected: the
    only way migration is reachable at all (`legacy_targets`' interlock).

    FALSIFICATION, per guard: drop the preflight and the extra-file row
    writes the target; drop `_preserve` from the migration and the kept
    copy is missing; drop the legacy rows from `check_install` and a
    present legacy copy reads in sync; drop the real-path de-duplication
    and CODEX_HOME=~/.codex reports one copy twice.
    """

    OLD = "an older loupe skill\n"

    def kept_dir(self):
        return self.state / "replaced-adapters"

    def statuses(self, rows):
        return sorted((r["kind"], r["status"]) for r in rows)

    def test_fresh_home_installs_at_the_documented_path(self):
        code, done = self.loupe("render-adapters", "--install")
        self.assertEqual(code, 0, done)
        self.assertTrue(self.documented.is_file())
        self.assertFalse((self.home / ".codex").exists(),
                         "the legacy root must not be created")
        code, check = self.loupe("render-adapters", "--check-install")
        self.assertEqual(code, 0, check)
        self.assertEqual(self.statuses(check["install"]),
                         [("claude-skill", "in_sync"),
                          ("codex-skill", "in_sync")])

    def test_a_legacy_copy_alone_is_drift_then_migrated_and_kept(self):
        legacy = self.legacy_at(self.home / ".codex", self.OLD)
        other = self.home / ".codex" / "skills" / "other" / "SKILL.md"
        other.parent.mkdir(parents=True)
        other.write_text("another skill\n", encoding="utf-8")
        code, check = self.loupe("render-adapters", "--check-install")
        self.assertEqual(code, 1, check)
        self.assertEqual(check["next"],
                         f"{TOOL_NAME} render-adapters --install")
        rows = {r["target"]: r for r in check["install"]}
        self.assertEqual(rows[str(legacy)]["status"], "legacy_present")
        self.assertEqual(rows[str(legacy)]["superseded_by"],
                         str(self.documented))
        self.assertEqual(rows[str(self.documented)]["status"], "absent")
        code, done = self.loupe("render-adapters", "--install")
        self.assertEqual(code, 0, done)
        rows = {r["target"]: r for r in done["installed"]}
        self.assertEqual(rows[str(legacy)]["status"], "migrated")
        self.assertEqual(Path(rows[str(legacy)]["kept"]).read_text(
            encoding="utf-8"), self.OLD)
        self.assertEqual(Path(rows[str(legacy)]["kept"]).parent,
                         self.kept_dir())
        self.assertFalse(legacy.parent.exists(),
                         "the legacy loupe directory must leave discovery")
        self.assertEqual(other.read_text(encoding="utf-8"), "another skill\n",
                         "another skill must never be touched")
        self.assertTrue((self.home / ".codex" / "skills").is_dir())
        self.assertEqual(self.documented.read_text(encoding="utf-8"),
                         adapters.render("codex-skill"))
        code, check = self.loupe("render-adapters", "--check-install")
        self.assertEqual(code, 0, check)

    def test_legacy_beside_a_current_documented_copy(self):
        self.loupe("render-adapters", "--install")
        legacy = self.legacy_at(self.home / ".codex", self.OLD)
        code, check = self.loupe("render-adapters", "--check-install")
        self.assertEqual(code, 1, check)
        self.assertIn(("codex-skill", "legacy_present"),
                      self.statuses(check["install"]))
        self.assertIn(("codex-skill", "in_sync"),
                      self.statuses(check["install"]))
        code, done = self.loupe("render-adapters", "--install")
        self.assertEqual(code, 0, done)
        self.assertEqual(self.statuses(done["installed"]),
                         [("claude-skill", "unchanged"),
                          ("codex-skill", "migrated"),
                          ("codex-skill", "unchanged")])
        self.assertFalse(legacy.exists())

    def test_a_legacy_directory_holding_anything_else_is_refused(self):
        legacy = self.legacy_at(self.home / ".codex", self.OLD,
                                extra=["notes.md"])
        code, check = self.loupe("render-adapters", "--check-install")
        self.assertEqual(code, 1, check)
        self.assertEqual(check["next_kind"], "blocked")
        self.assertIsNone(check["next"])
        self.assertIn(("codex-skill", "legacy_blocked"),
                      self.statuses(check["install"]))
        code, done = self.loupe("render-adapters", "--install")
        self.assertEqual(code, 1, done)
        self.assertEqual(done["next_kind"], "blocked")
        self.assertIn("notes.md", done["error"])
        self.assertEqual({r["status"] for r in done["installed"]},
                         {"blocked"})
        self.assertEqual(legacy.read_text(encoding="utf-8"), self.OLD)
        self.assertTrue((legacy.parent / "notes.md").is_file())
        self.assertFalse(self.documented.exists(), "nothing may be written")
        self.assertFalse(self.claude.exists(), "nothing may be written")
        self.assertFalse(self.kept_dir().exists(), "nothing may be kept")

    def test_a_symlinked_legacy_directory_is_refused(self):
        real = self.tmp / "elsewhere-skill"
        real.mkdir()
        (real / "SKILL.md").write_text(self.OLD, encoding="utf-8")
        link = self.home / ".codex" / "skills" / TOOL_NAME
        link.parent.mkdir(parents=True)
        link.symlink_to(real, target_is_directory=True)
        code, done = self.loupe("render-adapters", "--install")
        self.assertEqual(code, 1, done)
        self.assertIn("symbolic link", done["error"])
        self.assertEqual((real / "SKILL.md").read_text(encoding="utf-8"),
                         self.OLD)
        self.assertFalse(self.documented.exists())

    def test_codex_home_set_and_distinct_is_a_second_legacy_root(self):
        codex_home = self.tmp / "codex-home"
        first = self.legacy_at(self.home / ".codex", "first\n")
        second = self.legacy_at(codex_home, "second\n")
        env = self.env(CODEX_HOME=str(codex_home))
        code, check = self.loupe("render-adapters", "--check-install",
                                 env=env)
        self.assertEqual(code, 1, check)
        legacy = sorted(r["target"] for r in check["install"]
                        if r["status"] == "legacy_present")
        self.assertEqual(legacy, sorted([str(first), str(second)]))
        code, done = self.loupe("render-adapters", "--install", env=env)
        self.assertEqual(code, 0, done)
        kept = sorted(Path(r["kept"]).read_text(encoding="utf-8")
                      for r in done["installed"] if r["status"] == "migrated")
        self.assertEqual(kept, ["first\n", "second\n"])
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        self.assertTrue(self.documented.is_file(),
                        "CODEX_HOME must not move the documented path")
        self.assertFalse((codex_home / ".agents").exists())

    def test_codex_home_equal_to_the_default_is_one_root(self):
        legacy = self.legacy_at(self.home / ".codex", self.OLD)
        for spelling in (str(self.home / ".codex"),
                         str(self.home / ".codex") + "/",
                         str(self.home / "x" / ".." / ".codex")):
            with self.subTest(spelling=spelling):
                code, check = self.loupe(
                    "render-adapters", "--check-install",
                    env=self.env(CODEX_HOME=spelling))
                self.assertEqual(code, 1, check)
                present = [r for r in check["install"]
                           if r["status"] == "legacy_present"]
                self.assertEqual([r["target"] for r in present],
                                 [str(legacy)])

    def test_a_relative_codex_home_names_no_legacy_root(self):
        self.legacy_at(self.cwd / "rel", self.OLD)
        code, check = self.loupe("render-adapters", "--check-install",
                                 env=self.env(CODEX_HOME="rel"))
        self.assertEqual(code, 1, check)
        self.assertFalse(any(r["status"].startswith("legacy_")
                             for r in check["install"]))

    def test_an_unwritable_retention_directory_leaves_the_legacy_copy(self):
        legacy = self.legacy_at(self.home / ".codex", self.OLD)
        self.kept_dir().write_text("occupied by a file\n", encoding="utf-8")
        code, done = self.loupe("render-adapters", "--install")
        self.assertEqual(code, 1, done)
        self.assertEqual(done["next_kind"], "blocked")
        rows = {r["target"]: r for r in done["installed"]}
        self.assertEqual(rows[str(legacy)]["status"], "failed")
        self.assertIs(rows[str(legacy)]["preserved"], False)
        self.assertEqual(legacy.read_text(encoding="utf-8"), self.OLD,
                         "an unkept legacy copy must stay where it was")
        code, check = self.loupe("render-adapters", "--check-install")
        self.assertEqual(code, 1, check)
        self.assertIn(("codex-skill", "legacy_present"),
                      self.statuses(check["install"]))

    def test_a_legacy_copy_is_never_moved_while_its_target_failed(self):
        """Removing the old copy while the documented one could not be
        written would leave Codex no loupe skill at all."""
        legacy = self.legacy_at(self.home / ".codex", self.OLD)
        (self.home / ".agents").mkdir()
        (self.home / ".agents" / "skills").write_text(
            "a file where the skills directory belongs\n", encoding="utf-8")
        code, done = self.loupe("render-adapters", "--install")
        self.assertEqual(code, 1, done)
        rows = {r["target"]: r for r in done["installed"]}
        self.assertEqual(rows[str(self.documented)]["status"], "failed")
        self.assertEqual(rows[str(legacy)]["status"], "failed")
        self.assertIn("not moved", rows[str(legacy)]["error"])
        self.assertEqual(legacy.read_text(encoding="utf-8"), self.OLD)
        self.assertFalse(self.kept_dir().exists())

    def test_the_interlock_keeps_substituted_targets_off_the_real_home(self):
        """A test that substitutes its own targets reaches no legacy path,
        whatever it forgets to patch: legacy candidates exist only beside
        the documented target itself."""
        self.assertEqual(adapters.legacy_targets(
            {"codex-skill": self.tmp / "x" / "SKILL.md"}), {})
        self.assertEqual(adapters.legacy_targets({}), {})
        with unittest.mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            os.environ.pop("CODEX_HOME", None)
            self.assertEqual(
                adapters.legacy_targets(),
                {"codex-skill": [self.home / ".codex" / "skills" / TOOL_NAME
                                 / "SKILL.md"]})

    def test_a_legacy_path_that_is_the_target_by_real_path_is_not_legacy(self):
        """A symlinked `~/.codex/skills` onto `~/.agents/skills`: the two
        paths are one file, and moving it would remove what was just
        installed."""
        (self.home / ".agents" / "skills").mkdir(parents=True)
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "skills").symlink_to(
            self.home / ".agents" / "skills", target_is_directory=True)
        code, done = self.loupe("render-adapters", "--install")
        self.assertEqual(code, 0, done)
        self.assertNotIn("migrated", {r["status"] for r in done["installed"]})
        self.assertTrue(self.documented.is_file())
        code, check = self.loupe("render-adapters", "--check-install")
        self.assertEqual(code, 0, check)

    def test_the_install_line_states_the_documented_path_and_the_observation(self):
        text = adapters.render("codex-skill")
        self.assertIn(f"Install: `~/.agents/skills/{TOOL_NAME}/SKILL.md`",
                      text)
        self.assertIn("https://learn.chatgpt.com/docs/build-skills", text)
        self.assertIn("retrieved 2026-09-21", text)
        self.assertIn("Observed, separately: observed 2026-09-21", text)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TestRetainedBytesAreTheOriginalBytes(_Scratch):
    """Round-1 F2 of the 0.25.0 review: legacy migration deleted the
    original after keeping a NORMALIZED copy. `_legacy_state` read the
    legacy SKILL.md with `read_text`, whose universal-newline decoding turns
    CRLF and a lone CR into LF; `_preserve` wrote that text, proved it by
    reading it back as text, and the original was unlinked. Measured by the
    reviewer through `render-adapters --install` in a scratch HOME: legacy
    bytes b'a\\r\\nb\\r\\n' exited 0, `migrated`, with the original gone and
    the kept file holding b'a\\nb\\n'.

    FALSIFICATION, the spec row by row. Every row runs `bin/loupe` as a
    subprocess with HOME redirected (the only way migration is reachable:
    `legacy_targets`' interlock), except the two fault-injected rows, which
    run `cli.main` in-process because a lying filesystem and a concurrent
    edit cannot be planted from outside a process:

    - LF, CRLF, lone CR, mixed endings, no final newline (both kinds) and
      valid non-ASCII UTF-8, each in BOTH legacy locations (`~/.codex` and
      a distinct `$CODEX_HOME`), comparing the kept `read_bytes()`, its
      digest, its name and the reported digests with the original bytes
      BEFORE asserting the source is gone;
    - the reviewer's reproduction as written (`--ledger-dir`,
      `CODEX_HOME=$HOME/.codex`), LF/CRLF/CR/invalid;
    - two legacy copies of one text with different endings: two kept files;
    - controls that must refuse and leave the legacy copy byte-identical:
      invalid UTF-8, an unwritable retention directory, and an existing
      retained file whose DECODED text agrees but whose raw endings differ
      (both directions, and lone CR), beside the paired control where the
      existing file holds exactly the bytes;
    - the other `_preserve` caller, a replaced installed adapter: the same
      endings kept byte for byte, a copy differing ONLY in line endings is
      `stale`/`replaced` rather than `in_sync`/`unchanged`, an invalid-UTF-8
      target is kept and replaced, and the same planted-endings refusal.

    Mutations (the track report records each one's own result): read the
    legacy copy with `read_text` again; compare an existing kept file as
    text; read back as text; drop the UTF-8 requirement; read a replaced
    target as text; judge `check_install` as text; drop the re-read before
    removal; ignore a retention failure.
    """

    ENDINGS = {
        "LF": b"line one\nline two\n",
        "CRLF": b"line one\r\nline two\r\n",
        "lone CR": b"line one\rline two\r",
        "mixed": b"line one\r\nline two\nline three\rline four\r\n",
        "no final newline, CRLF": b"line one\r\nline two",
        "no final newline, LF": b"line one\nline two",
        # NEL and U+2028 are line breaks to `str.splitlines`, never to a
        # file read; they ride along to prove nothing else is rewritten.
        "non-ASCII UTF-8": ("café — 日本語 "
                            "\U0001F50D\r\nnaïvex y\n"
                            ).encode("utf-8"),
        "non-ASCII UTF-8, LF": "caf\u00e9 \u2014 \u65e5\u672c\n".encode(
            "utf-8"),
    }
    INVALID = b"\xff\xfe not UTF-8\r\n"

    def locations(self, i):
        """(label, HOME, state, legacy root, env) for row `i`, each in its
        own fresh tree."""
        out = []
        for label in ("~/.codex", "distinct $CODEX_HOME"):
            base = self.tmp / f"row-{i}-{len(out)}"
            home, state = base / "home", base / "state"
            home.mkdir(parents=True)
            state.mkdir()
            if label == "~/.codex":
                root, env = home / ".codex", scratch_env(home, state)
            else:
                root = base / "codex-home"
                env = scratch_env(home, state, CODEX_HOME=str(root))
            out.append((label, home, state, root, env))
        return out

    @staticmethod
    def plant(root: Path, data: bytes) -> Path:
        path = root / "skills" / TOOL_NAME / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def cli_in(self, env, *argv):
        return run_loupe(list(argv), cwd=self.cwd, env=env)

    def assert_kept_exactly(self, row, original, state):
        """The retention proof, bytes FIRST: the kept content, its digest,
        the digest the row reports, then the name and the place."""
        kept = Path(row["kept"])
        self.assertEqual(kept.read_bytes(), original)
        self.assertEqual(_sha(kept.read_bytes()), _sha(original))
        self.assertEqual(row["replaced_digest"], _sha(original))
        self.assertEqual(kept.name,
                         f"{row['kind']}-{_sha(original)[:12]}.md")
        self.assertEqual(kept.parent, state / "replaced-adapters")

    def assert_survives(self, path, row, original):
        """The invariant every refusal protects, stated on bytes and
        asserted BEFORE any status: the original bytes still exist exactly,
        in place or at the path the row names as kept."""
        found = [path.read_bytes()] if path.is_file() else []
        kept = Path(row.get("kept") or "")
        if row.get("kept") and kept.is_file():
            found.append(kept.read_bytes())
        self.assertIn(original, found)

    # ------------------------------------------------------------ the matrix

    def test_every_ending_is_kept_byte_for_byte_in_both_locations(self):
        for i, (name, original) in enumerate(self.ENDINGS.items()):
            for label, home, state, root, env in self.locations(i):
                with self.subTest(ending=name, location=label):
                    legacy = self.plant(root, original)
                    code, check = self.cli_in(env, "render-adapters",
                                              "--check-install")
                    self.assertEqual(code, 1, check)
                    (present,) = [r for r in check["install"]
                                  if r["target"] == str(legacy)]
                    self.assertEqual(present["status"], "legacy_present")
                    code, done = self.cli_in(env, "render-adapters",
                                             "--install")
                    self.assertEqual(code, 0, done)
                    (row,) = [r for r in done["installed"]
                              if r["target"] == str(legacy)]
                    self.assertEqual(row["status"], "migrated", row)
                    # Bytes and digests FIRST; only then the removal.
                    self.assert_kept_exactly(row, original, state)
                    self.assertEqual(present["target_digest"],
                                     _sha(original))
                    self.assertFalse(legacy.exists())
                    self.assertFalse(legacy.parent.exists())

    def test_the_reviewers_reproduction_as_written(self):
        """The reviewer's reproduction script's `retention()`, unchanged in
        shape: `--ledger-dir`, `CODEX_HOME=$HOME/.codex`, no state variable.
        Measured on the unfixed head: LF exact, CRLF and CR migrated with
        normalized bytes, invalid refused."""
        cases = [("LF", b"a\nb\n"), ("CRLF", b"a\r\nb\r\n"),
                 ("CR", b"a\rb\r"), ("invalid_UTF8", b"\xff\r\n")]
        for name, raw in cases:
            with self.subTest(case=name):
                p = self.tmp / f"repro-{name}"
                home = p / "home"
                old = self.plant(home / ".codex", raw)
                env = scratch_env(home, p / "unused-state",
                                  CODEX_HOME=str(home / ".codex"))
                env.pop(env_var("STATE_DIR"))
                code, out = self.cli_in(env, "--ledger-dir", str(p / "state"),
                                        "render-adapters", "--install")
                moved = [r for r in out["installed"]
                         if r["status"] == "migrated"]
                if name == "invalid_UTF8":
                    self.assertEqual(code, 1, out)
                    self.assertEqual(moved, [])
                    self.assertEqual(old.read_bytes(), raw)
                    continue
                self.assertEqual(code, 0, out)
                (row,) = moved
                self.assertEqual(Path(row["kept"]).read_bytes(), raw)
                self.assertEqual(row["replaced_digest"], _sha(raw))
                self.assertFalse(old.exists())

    def test_two_copies_of_one_text_with_different_endings_are_two_kept(self):
        """Under the defect both normalized to one text, one digest and one
        kept file: the second copy "matched" the first's kept bytes and was
        deleted with nothing of its own kept."""
        (_l, home, state, codex_home, env), = self.locations("two")[1:]
        lf = self.plant(home / ".codex", self.ENDINGS["LF"])
        crlf = self.plant(codex_home, self.ENDINGS["CRLF"])
        code, done = self.cli_in(env, "render-adapters", "--install")
        self.assertEqual(code, 0, done)
        rows = {r["target"]: r for r in done["installed"]
                if r["status"] == "migrated"}
        self.assert_kept_exactly(rows[str(lf)], self.ENDINGS["LF"], state)
        self.assert_kept_exactly(rows[str(crlf)], self.ENDINGS["CRLF"], state)
        self.assertNotEqual(rows[str(lf)]["kept"], rows[str(crlf)]["kept"])
        self.assertFalse(lf.exists())
        self.assertFalse(crlf.exists())

    # ------------------------------------------------ refusals and controls

    def assert_refused_intact(self, env, legacy, original, state, *,
                              blocked):
        code, done = self.cli_in(env, "render-adapters", "--install")
        rows = {r["target"]: r for r in done["installed"]}
        self.assert_survives(legacy, rows.get(str(legacy), {}), original)
        self.assertEqual(code, 1, done)
        self.assertNotIn("migrated", {r["status"] for r in rows.values()})
        self.assertEqual(legacy.read_bytes(), original,
                         "a refused legacy copy must stay byte-identical")
        if blocked:
            self.assertEqual({r["status"] for r in rows.values()},
                             {"blocked"})
            self.assertFalse((state / "replaced-adapters").exists())
        return done, rows

    def test_invalid_utf8_is_refused_in_both_locations(self):
        for label, home, state, root, env in self.locations("bad"):
            with self.subTest(location=label):
                legacy = self.plant(root, self.INVALID)
                code, check = self.cli_in(env, "render-adapters",
                                          "--check-install")
                self.assertEqual(code, 1, check)
                self.assertIsNone(check["next"])
                self.assertIn(("codex-skill", "legacy_blocked"),
                              sorted((r["kind"], r["status"])
                                     for r in check["install"]))
                done, _rows = self.assert_refused_intact(
                    env, legacy, self.INVALID, state, blocked=True)
                self.assertIn("utf-8", done["error"].lower())
                self.assertFalse((home / ".agents").exists(),
                                 "nothing may be written")
                self.assertFalse((home / ".claude").exists())

    def test_a_retention_failure_leaves_the_bytes_in_both_locations(self):
        for label, home, state, root, env in self.locations("keepfail"):
            with self.subTest(location=label):
                original = self.ENDINGS["CRLF"]
                legacy = self.plant(root, original)
                occupied = state / "replaced-adapters"
                occupied.write_bytes(b"occupied by a file\r\n")
                _done, rows = self.assert_refused_intact(
                    env, legacy, original, state, blocked=False)
                self.assertEqual(rows[str(legacy)]["status"], "failed")
                self.assertIs(rows[str(legacy)]["preserved"], False)
                self.assertEqual(occupied.read_bytes(),
                                 b"occupied by a file\r\n")

    def test_an_existing_kept_file_must_agree_in_bytes_not_in_text(self):
        """The finding's own case: a retained file already at the digest's
        name whose DECODED text equals the original's but whose raw endings
        differ. `read_text` on both sides called them equal; only bytes can
        tell them apart. The last row is the paired control (exactly the
        bytes), which is the one idempotent success."""
        rows_spec = [
            ("CRLF original, LF kept", b"a\r\nb\r\n", b"a\nb\n"),
            ("LF original, CRLF kept", b"a\nb\n", b"a\r\nb\r\n"),
            ("lone-CR original, LF kept", b"a\rb\r", b"a\nb\n"),
        ]
        for i, (name, original, planted) in enumerate(rows_spec):
            for label, home, state, root, env in self.locations(f"k{i}"):
                with self.subTest(case=name, location=label):
                    self.assertEqual(
                        planted.decode().splitlines(),
                        original.decode().splitlines(),
                        "the planted copy must agree as decoded text")
                    legacy = self.plant(root, original)
                    keep = state / "replaced-adapters"
                    keep.mkdir()
                    at = keep / f"codex-skill-{_sha(original)[:12]}.md"
                    at.write_bytes(planted)
                    _done, rows = self.assert_refused_intact(
                        env, legacy, original, state, blocked=False)
                    self.assertEqual(rows[str(legacy)]["status"], "failed")
                    self.assertIs(rows[str(legacy)]["preserved"], False)
                    self.assertIn("different bytes",
                                  rows[str(legacy)]["error"])
                    self.assertEqual(at.read_bytes(), planted,
                                     "the planted file must not be touched")
        controls = [("LF", self.ENDINGS["LF"]),
                    ("mixed", self.ENDINGS["mixed"])]
        for i, (name, original) in enumerate(controls):
            for label, home, state, root, env in self.locations(f"exact{i}"):
                with self.subTest(case=f"control: exactly the bytes, {name}",
                                  location=label):
                    legacy = self.plant(root, original)
                    keep = state / "replaced-adapters"
                    keep.mkdir()
                    at = keep / f"codex-skill-{_sha(original)[:12]}.md"
                    at.write_bytes(original)
                    code, done = self.cli_in(env, "render-adapters",
                                             "--install")
                    self.assertEqual(code, 0, done)
                    (row,) = [r for r in done["installed"]
                              if r["target"] == str(legacy)]
                    self.assertEqual(row["status"], "migrated")
                    self.assertEqual(Path(row["kept"]), at)
                    self.assert_kept_exactly(row, original, state)
                    self.assertEqual(list(keep.iterdir()), [at])
                    self.assertFalse(legacy.exists())

    # ---------------------------------- the other caller: a replaced target

    def test_a_replaced_installed_adapter_is_kept_byte_for_byte(self):
        """`_preserve`'s other caller reads the installed copy it replaces,
        and its bytes can differ the same way: a hand edit saved with CRLF
        was kept normalized, then overwritten."""
        for i, (name, edit) in enumerate(self.ENDINGS.items()):
            for kind, rel in (("claude-skill", ".claude"),
                              ("codex-skill", ".agents")):
                with self.subTest(ending=name, kind=kind):
                    base = self.tmp / f"rep-{i}-{kind}"
                    home, state = base / "home", base / "state"
                    state.mkdir(parents=True)
                    target = home / rel / "skills" / TOOL_NAME / "SKILL.md"
                    target.parent.mkdir(parents=True)
                    original = b"hand-edited " + edit
                    target.write_bytes(original)
                    env = scratch_env(home, state)
                    code, done = self.cli_in(env, "render-adapters",
                                             "--install")
                    self.assertEqual(code, 0, done)
                    (row,) = [r for r in done["installed"]
                              if r["target"] == str(target)]
                    self.assertEqual(row["status"], "replaced")
                    self.assert_kept_exactly(row, original, state)
                    self.assertEqual(target.read_bytes(),
                                     adapters.render(kind).encode("utf-8"))

    def test_a_copy_differing_only_in_line_endings_is_not_in_sync(self):
        """`in_sync`/`unchanged` meant "the same text after newline
        translation", so a CRLF copy of the rendered text read as current
        and reported the rendered text's digest as its own. It is `stale`,
        with the digest of what is on disk, and `--install` keeps it before
        replacing it. Paired control: the LF rendering is `in_sync` and
        `unchanged`."""
        home, state = self.tmp / "crlf-home", self.tmp / "crlf-state"
        state.mkdir()
        env = scratch_env(home, state)
        code, done = self.cli_in(env, "render-adapters", "--install")
        self.assertEqual(code, 0, done)
        code, again = self.cli_in(env, "render-adapters", "--install")
        self.assertEqual({r["status"] for r in again["installed"]},
                         {"unchanged"})
        target = home / ".claude" / "skills" / TOOL_NAME / "SKILL.md"
        original = adapters.render("claude-skill").replace(
            "\n", "\r\n").encode("utf-8")
        target.write_bytes(original)
        code, check = self.cli_in(env, "render-adapters", "--check-install")
        self.assertEqual(code, 1, check)
        rows = {r["target"]: r for r in check["install"]}
        self.assertEqual(rows[str(target)]["status"], "stale")
        self.assertEqual(rows[str(target)]["target_digest"], _sha(original))
        self.assertNotEqual(rows[str(target)]["target_digest"],
                            rows[str(target)]["source_digest"])
        code, done = self.cli_in(env, "render-adapters", "--install")
        self.assertEqual(code, 0, done)
        (row,) = [r for r in done["installed"] if r["target"] == str(target)]
        self.assertEqual(row["status"], "replaced")
        self.assert_kept_exactly(row, original, state)
        code, check = self.cli_in(env, "render-adapters", "--check-install")
        self.assertEqual(code, 0, check)

    def test_an_invalid_utf8_installed_adapter_is_kept_and_replaced(self):
        """Measured before the fix: `--check-install` and `--install` both
        exited 2 as a USAGE error carrying a decode message. A replaced
        target's bytes are kept whatever they are: they are the user's."""
        home, state = self.tmp / "bad-home", self.tmp / "bad-state"
        state.mkdir()
        env = scratch_env(home, state)
        target = home / ".claude" / "skills" / TOOL_NAME / "SKILL.md"
        target.parent.mkdir(parents=True)
        target.write_bytes(self.INVALID)
        code, check = self.cli_in(env, "render-adapters", "--check-install")
        self.assertEqual(code, 1, check)
        rows = {r["target"]: r for r in check["install"]}
        self.assertEqual(rows[str(target)]["status"], "stale")
        self.assertEqual(rows[str(target)]["target_digest"],
                         _sha(self.INVALID))
        code, done = self.cli_in(env, "render-adapters", "--install")
        self.assertEqual(code, 0, done)
        (row,) = [r for r in done["installed"] if r["target"] == str(target)]
        self.assertEqual(row["status"], "replaced")
        self.assert_kept_exactly(row, self.INVALID, state)

    def test_a_replaced_target_refuses_a_kept_file_agreeing_only_as_text(self):
        home, state = self.tmp / "t-home", self.tmp / "t-state"
        env = scratch_env(home, state)
        target = home / ".claude" / "skills" / TOOL_NAME / "SKILL.md"
        target.parent.mkdir(parents=True)
        original = b"hand edit\nsecond line\n"
        target.write_bytes(original)
        keep = state / "replaced-adapters"
        keep.mkdir(parents=True)
        at = keep / f"claude-skill-{_sha(original)[:12]}.md"
        at.write_bytes(b"hand edit\r\nsecond line\r\n")
        code, done = self.cli_in(env, "render-adapters", "--install")
        (row,) = [r for r in done["installed"] if r["target"] == str(target)]
        self.assert_survives(target, row, original)
        self.assertEqual(code, 1, done)
        self.assertEqual(row["status"], "failed")
        self.assertIs(row["preserved"], False)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(at.read_bytes(), b"hand edit\r\nsecond line\r\n")

    # ------------------------------------------------- fault-injected rows

    def in_process(self, env, *argv):
        """`cli.main` with the same redirected environment, for the rows
        whose fault must be planted inside the process."""
        buf = io.StringIO()
        before = os.getcwd()
        os.chdir(self.cwd)
        try:
            with unittest.mock.patch.dict(os.environ, env, clear=True):
                with contextlib.redirect_stdout(buf):
                    code = cli.main(list(argv))
        finally:
            os.chdir(before)
        return code, json.loads(buf.getvalue())

    def test_a_kept_copy_stored_with_other_endings_is_not_proof(self):
        """A destination that stores different line endings from the bytes
        it was handed (what a text-mode write does on a platform that
        writes CRLF) must fail the read-back. A text read-back cannot see
        it: both sides decode to one text. Both callers, same proof."""
        real = adapters._write_synced

        def crlf_writer(target, data):
            real(target, data.replace(b"\n", b"\r\n"))

        home, state = self.tmp / "w-home", self.tmp / "w-state"
        env = scratch_env(home, state)
        legacy = self.plant(home / ".codex", b"a\nb\n")
        edited = home / ".claude" / "skills" / TOOL_NAME / "SKILL.md"
        edited.parent.mkdir(parents=True)
        edited.write_bytes(b"hand edit\n")
        with unittest.mock.patch.object(adapters, "_write_synced",
                                        crlf_writer):
            code, done = self.in_process(env, "render-adapters", "--install")
        rows = {r["target"]: r for r in done["installed"]}
        for path, original in ((legacy, b"a\nb\n"), (edited, b"hand edit\n")):
            self.assert_survives(path, rows[str(path)], original)
        self.assertEqual(code, 1, done)
        for path, original in ((legacy, b"a\nb\n"), (edited, b"hand edit\n")):
            self.assertEqual(rows[str(path)]["status"], "failed", rows)
            self.assertIs(rows[str(path)]["preserved"], False)
            self.assertIn("does not read back", rows[str(path)]["error"])
            self.assertEqual(path.read_bytes(), original)

    def test_a_legacy_copy_that_changes_before_removal_stays(self):
        """The legacy copy is read before any target is written. If it
        changes before the removal, what was kept is not what removing it
        would delete: it stays, holding the newer bytes, and the earlier
        bytes stay kept."""
        real = adapters._write_synced
        home, state = self.tmp / "r-home", self.tmp / "r-state"
        env = scratch_env(home, state)
        legacy = self.plant(home / ".codex", b"first\r\n")

        def concurrent_edit(target, data):
            real(target, data)
            if data == b"first\r\n":
                legacy.write_bytes(b"edited meanwhile\r\n")

        with unittest.mock.patch.object(adapters, "_write_synced",
                                        concurrent_edit):
            code, done = self.in_process(env, "render-adapters", "--install")
        (row,) = [r for r in done["installed"] if r["target"] == str(legacy)]
        self.assert_survives(legacy, row, b"edited meanwhile\r\n")
        self.assertEqual(code, 1, done)
        self.assertEqual(row["status"], "failed")
        self.assertIn("no longer holds", row["error"])
        self.assertEqual(Path(row["kept"]).read_bytes(), b"first\r\n")
        self.assertEqual(legacy.read_bytes(), b"edited meanwhile\r\n")


class TestEmbeddedRegion(_Scratch):
    """E (public issue #5): `--check-embedded <file>` compares the one
    region between the rendered block's own marker lines with this
    installation's rendering, and `--write-embedded <file>` replaces exactly
    that region. The partition, one row per input kind, each through
    `bin/loupe` as a subprocess:

      current · stale · missing markers · BEGIN only · END only · END before
      BEGIN · two regions · nested BEGIN · empty file · absent path · a
      directory · non-UTF-8 bytes · a symlink · CRLF in the region · CRLF
      outside it · a marker quoted in a fenced block · no final newline

    RULES CHOSEN (stated here and in `review/adapters.py`): markers are
    LEXICAL — a line beginning, at column 0, with the BEGIN or END prefix,
    no Markdown parsed, so a marker quoted at column 0 in a fenced example
    counts and makes the structure ambiguous (refused); a region carrying a
    carriage return is refused as `crlf`, while CRLF outside the region is
    not judged and is written back as it was.

    FALSIFICATION, per guard: drop the nested check and a BEGIN inside a
    region is read as the region; drop the duplicate check and the second
    region is silently ignored; drop the CR check and a CRLF region reads
    stale and is rewritten LF; write the whole rendering over the file and
    the outside-bytes assertion fails.
    """

    BLOCK = None

    def setUp(self):
        super().setUp()
        self.block = adapters.render("instructions-block")
        self.head = "# Project\n\nSome rules.\n\n"
        self.tail = "\n## After\n\nMore rules.\n"

    def file(self, text, name="AGENTS.md", raw=None):
        path = self.cwd / name
        if raw is not None:
            path.write_bytes(raw)
        else:
            path.write_text(text, encoding="utf-8", newline="")
        return path

    def check(self, path, *extra):
        return self.loupe("render-adapters", "--check-embedded", str(path),
                          *extra)

    def write(self, path):
        return self.loupe("render-adapters", "--write-embedded", str(path))

    def assert_refused(self, path, status, code=1):
        before = path.read_bytes() if path.is_file() else None
        for verb in (self.check, self.write):
            with self.subTest(verb=verb.__name__, status=status):
                got, payload = verb(path)
                self.assertEqual(got, code, payload)
                self.assertEqual(payload["status"], status, payload)
                self.assertEqual(payload["next_kind"], "blocked")
                self.assertIsNone(payload["next"])
                self.assertTrue(payload["remedy"])
                if before is not None:
                    self.assertEqual(path.read_bytes(), before,
                                     "a refused write changed the file")

    def stale_block(self):
        return self.block.replace("## What this is", "## What this WAS", 1)

    def test_rendering_carries_exactly_one_marker_pair(self):
        lines = self.block.split("\n")
        self.assertEqual(sum(l.startswith(adapters.EMBEDDED_BEGIN)
                             for l in lines), 1)
        self.assertEqual(sum(l.startswith(adapters.EMBEDDED_END)
                             for l in lines), 1)
        self.assertTrue(self.block.endswith("-->\n"))

    def test_current(self):
        path = self.file(self.head + self.block + self.tail)
        code, payload = self.check(path)
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["status"], "current")
        self.assertEqual(payload["source_digest"], payload["digest"])
        self.assertEqual(payload["data"]["begin_line"], 5)
        before = path.stat().st_mtime_ns
        code, payload = self.write(path)
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["status"], "current")
        self.assertEqual(path.stat().st_mtime_ns, before,
                         "a current region must not be rewritten")

    def test_stale_is_repaired_and_nothing_outside_moves(self):
        head = self.head + "trailing space here   \n\t\n"
        tail = self.tail + "\u2028 separator \x0c kept\n"
        path = self.file(head + self.stale_block() + tail)
        code, payload = self.check(path)
        self.assertEqual(code, 1, payload)
        self.assertEqual(payload["status"], "stale")
        self.assertEqual(payload["next_kind"], "command")
        self.assertEqual(payload["next"], f"{TOOL_NAME} render-adapters "
                                          f"--write-embedded {path}")
        path.chmod(0o640)
        code, payload = self.write(path)
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["status"], "written")
        after = path.read_bytes()
        self.assertEqual(after, (head + self.block + tail).encode("utf-8"))
        self.assertTrue(after.startswith(head.encode("utf-8")))
        self.assertTrue(after.endswith(tail.encode("utf-8")))
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
        code, payload = self.check(path)
        self.assertEqual(code, 0, payload)

    def test_a_region_ending_the_file_without_a_newline_is_stale(self):
        path = self.file(self.head + self.block.rstrip("\n"))
        code, payload = self.check(path)
        self.assertEqual(code, 1, payload)
        self.assertEqual(payload["status"], "stale")
        self.write(path)
        self.assertEqual(path.read_text(encoding="utf-8"),
                         self.head + self.block)

    def test_missing_markers(self):
        self.assert_refused(self.file(self.head + self.tail),
                            "missing_markers")

    def test_empty_file(self):
        self.assert_refused(self.file(""), "missing_markers")

    def test_begin_only(self):
        begin = self.block.split("\n", 1)[0] + "\n"
        self.assert_refused(self.file(self.head + begin + self.tail),
                            "unbalanced_begin")

    def test_end_only(self):
        end = self.block.rstrip("\n").rsplit("\n", 1)[1] + "\n"
        self.assert_refused(self.file(self.head + end + self.tail),
                            "unbalanced_end")

    def test_end_before_begin(self):
        begin, rest = self.block.split("\n", 1)
        body, end = rest.rstrip("\n").rsplit("\n", 1)
        text = end + "\n" + body + "\n" + begin + "\n"
        self.assert_refused(self.file(text), "end_before_begin")

    def test_two_regions(self):
        self.assert_refused(
            self.file(self.head + self.block + self.tail + self.block),
            "duplicated")

    def test_nested_begin(self):
        begin = self.block.split("\n", 1)[0] + "\n"
        text = self.head + begin + self.block + self.tail
        self.assert_refused(self.file(text), "nested")

    def test_a_marker_quoted_in_a_fenced_block_counts(self):
        begin = self.block.split("\n", 1)[0]
        example = f"```markdown\n{begin}\n```\n\n"
        self.assert_refused(self.file(example + self.block), "nested")
        # The control: the same example indented off column 0 is prose.
        indented = f"```markdown\n  {begin}\n```\n\n"
        code, payload = self.check(self.file(indented + self.block))
        self.assertEqual(code, 0, payload)

    def test_crlf_in_the_region_is_refused(self):
        crlf = (self.head + self.block).replace("\n", "\r\n")
        self.assert_refused(self.file(crlf), "crlf")

    def test_crlf_outside_the_region_is_not_judged_and_kept(self):
        head = self.head.replace("\n", "\r\n")
        path = self.file(head + self.stale_block() + "tail\r\n")
        code, payload = self.write(path)
        self.assertEqual(code, 0, payload)
        self.assertEqual(path.read_bytes(),
                         (head + self.block + "tail\r\n").encode("utf-8"))

    def test_non_utf8(self):
        raw = (self.head + self.block).encode("utf-8") + b"\xff\xfe\n"
        self.assert_refused(self.file(None, raw=raw), "not_utf8")

    def test_absent_path(self):
        self.assert_refused(self.cwd / "NOPE.md", "absent", code=2)

    def test_a_directory(self):
        (self.cwd / "dir.md").mkdir()
        self.assert_refused(self.cwd / "dir.md", "directory", code=2)

    def test_a_fifo_is_refused_before_any_read(self):
        """A named pipe would block the read forever: the file kind is
        checked before anything is opened."""
        if not hasattr(os, "mkfifo"):
            self.skipTest("no named pipes on this platform")
        fifo = self.cwd / "pipe.md"
        os.mkfifo(fifo)
        self.assert_refused(fifo, "not_a_file", code=2)

    def test_a_dangling_symlink_is_absent(self):
        (self.cwd / "CLAUDE.md").symlink_to(self.cwd / "AGENTS.md")
        self.assert_refused(self.cwd / "CLAUDE.md", "absent", code=2)

    def test_a_symlink_is_followed_for_reading_and_writing(self):
        target = self.file(self.head + self.stale_block() + self.tail)
        link = self.cwd / "CLAUDE.md"
        link.symlink_to("AGENTS.md")
        code, payload = self.check(link)
        self.assertEqual(code, 1, payload)
        self.assertEqual(payload["status"], "stale")
        self.assertEqual(payload["target"], str(target.resolve()))
        self.assertIn("followed the symbolic link", payload["note"])
        code, payload = self.write(link)
        self.assertEqual(code, 0, payload)
        self.assertIn("written", payload["note"])
        self.assertTrue(link.is_symlink(), "the link itself must survive")
        self.assertEqual(target.read_text(encoding="utf-8"),
                         self.head + self.block + self.tail)

    def test_dir_is_refused_with_the_embedded_modes(self):
        path = self.file(self.head + self.block)
        code, payload = self.check(path, "--dir", str(self.tmp))
        self.assertEqual(code, 2, payload)
        self.assertEqual(payload["next"], f"{TOOL_NAME} render-adapters "
                                          f"--check-embedded {path}")

    def test_the_modes_are_mutually_exclusive(self):
        path = self.file(self.head + self.block)
        code, payload = self.loupe("render-adapters", "--check-embedded",
                                   str(path), "--install")
        self.assertEqual(code, 2, payload)
        self.assertFalse(self.documented.exists())


class TestAdapterEnumerationsAreDerived(unittest.TestCase):
    """Lineage 8: the generated adapters ENUMERATE runtime vocabularies —
    dispositions, verdicts, the claim and reference grammars, the
    falsification record — which is exactly what the shipped-restatement
    discipline polices in `docs/design.md` and the README. The adapters'
    exclusion from that search claimed they restate no vocabulary; that
    claim was false. It is now true by construction, and this class is
    what makes the construction a fact rather than an intention: every
    enumeration is rendered from `review/vocab.py` at render time
    (`adapters.procedure()` is a function, not an import-time constant),
    so mutating the authority and re-rendering must move the text.

    The completeness proof is STRUCTURAL (round-1 F3): the inventory below
    classifies every collection-valued authority `vocab` exports — tuples
    of strings, mapping keys, composite constants, whatever shape — as
    DERIVED (probed by authority mutation, sentinels required in EVERY
    rendered kind), INCIDENTAL (flagged by the advisory proximity scan
    without an enumeration standing behind it, with the reason), or
    NOT_ENUMERATED (with the reason the adapters do not state it). A new
    collection fails by name until someone decides which it is. The
    lexical proximity scan is ADVISORY — a tripwire that catches what it
    can see, never the completeness proof; round-1 F3's probe showed a
    mapping-shaped enumeration walking straight past a tuples-only
    detector.
    """

    #: vocabulary -> the vocab attributes its rendering reads. `None`
    #: means patch by shape (tuple -> same-arity sentinels, dict -> one
    #: added sentinel key); a string is an explicit sentinel for a named
    #: constant. Sentinels must appear in every rendered kind.
    DERIVED = {
        "DISPOSITIONS": {"DISPOSITIONS": None},
        "BLOCKING_ILLEGAL": {"BLOCKING_ILLEGAL": None},
        "FALSIFICATION_STATUSES": {"FALSIFICATION_STATUSES": None},
        "MUTATION_OUTCOMES": {"MUTATION_OUTCOMES": None},
        "FALSIFICATION_RECORD": {"FALSIFICATION_RECORD": None},
        "TRANSPORTS": {"TRANSPORTS": None},
        "CLAIM_LIST_FIELDS": {"CLAIM_LIST_FIELDS": None},
        "CLAIM_FIELDS": {"CLAIM_FIELDS": None},
        "CLAIM_REQUIRED": {"CLAIM_REQUIRED": None},
        "CLAIM_REFERENCE_FIELDS": {"CLAIM_REFERENCE_FIELDS": None},
        "CLAIM_REFERENCE_REQUIRED": {"CLAIM_REFERENCE_REQUIRED": None},
        "VERDICTS": {"VERDICT_CLEAN": "zz-verdict-clean-sentinel",
                     "VERDICT_CHANGES": "zz-verdict-changes-sentinel"},
        # 0.25.0: the object-list members render with their own field
        # tables (`adapters._claim_members`). The tables are a mapping of
        # mappings, which the by-shape patch cannot reach, so the patch is
        # a function of the authority: one sentinel field per member.
        "CARRIED_OUTCOMES": {"CARRIED_OUTCOMES": None},
        "CLAIM_OBJECT_LIST_FIELDS": {"CLAIM_OBJECT_LIST_FIELDS": lambda v: (
            {m: {**t, f"zz-{m}-field": "string"} for m, t in v.items()},
            [f"zz-{m}-field" for m in v])},
        "CLAIM_OBJECT_REQUIRED": {"CLAIM_OBJECT_REQUIRED": lambda v: (
            {m: (*r, f"zz-{m}-required") for m, r in v.items()},
            [f"zz-{m}-required" for m in v])},
    }

    #: Vocabularies the advisory scan may flag in adapter text without a
    #: derived enumeration standing behind them, each with the reason the
    #: flag is incidental.
    INCIDENTAL = {
        "DISPOSITION_PAYLOADS": (
            "its keys are exactly DISPOSITIONS, so the scan flags it "
            "wherever the derived dispositions enumeration appears; the "
            "payloads themselves — the values — are the validator's "
            "contract and are never stated in the procedure"),
        "STAMPED_KINDS": (
            "its two members are the first and third names in the "
            "`request → verdict → disposition` chain describing the loop's "
            "three envelopes; the stamped SUBSET is never listed as such — "
            "the chain co-locates the members only where the surrounding "
            "frontmatter is short enough to bring them inside the span"),
    }

    #: Collections the adapters deliberately do not enumerate, each with
    #: the reason — the structural inventory's third arm, so a collection
    #: can never sit in no category (round-1 F3).
    NOT_ENUMERATED = {
        "ACCEPTED_SUBTYPES": "the subtype grammar is the validator's; the "
                             "procedure never lists subtypes",
        "AUTHORIZATION_REQUIRED": "the authorization body's grammar belongs "
                                  "to the verb a HUMAN runs, and the "
                                  "procedure's only word about that verb is "
                                  "that it is never the agent's to run — "
                                  "spelling out the body it emits would "
                                  "read as instructions for producing one",
        "WAIVED_REQUIRED": "same reason as AUTHORIZATION_REQUIRED: the "
                           "record of one overruled finding is emitted by a "
                           "human's verb, and the procedure states the "
                           "boundary rather than the shape",
        "AUTHORIZATION_WRAPPER_FIELDS": "the authorization's wrapper "
                                        "attribute grammar; the procedure "
                                        "names no wrapper attribute of any "
                                        "kind",
        "AUTHORIZATION_FIELDS": "the authorization body's member/type map — "
                                "the validator's domain, like the claim's",
        "AUTHORIZATION_NONEMPTY": "which authorization members may not be "
                                  "blank; a validator rule, never procedure "
                                  "text",
        "AUTHORIZATION_DUPLICATED": "the facts the wrapper and body both "
                                    "state and must agree on; enforced, not "
                                    "narrated",
        "WAIVED_FIELDS": "the member/type map of one overruled-finding "
                         "record, for the same reason as WAIVED_REQUIRED",
        "BREAKERS": "the procedure names the breaker MECHANISM, never the "
                    "member list; the shipped documents' breaker "
                    "enumerations are registered in RESTATEMENTS",
        "CLAIM_NONEMPTY": "nonemptiness is enforced at the boundary, not "
                          "stated as a member list",
        "CLAIM_STRING_FIELDS": "its members appear only inside the full "
                               "member list derived from CLAIM_FIELDS; the "
                               "string/list split is described "
                               "structurally ('the rest are strings'), "
                               "never as a list",
        "DECIDE_KEYS": "the keys a repository may leave undeclared "
                       "(2026-09-03). Every verb result carries the entries "
                       "for the keys THIS repository never declared, with "
                       "the meaning and the exact line beside each, so the "
                       "procedure names the FIELD and what to do when one "
                       "arrives — enumerating the keys in it would restate "
                       "a list the tool already hands the agent, per result "
                       "and already filtered to what is undecided",
        "CLOSURES": "the procedure tells the reviewer WHERE closures are "
                    "answered, not the closure vocabulary",
        "FALSIFICATION_KINDS": "not stated anywhere in the procedure",
        "GIT_FILE_MODES": "git's tree-entry modes, not this tool's "
                          "vocabulary: the procedure tells an agent what a "
                          "refusal means, never which modes git writes",
        "FINDING_ANSWERS": "the lifecycle table the ledger derives the "
                           "standing set from (lineage 20 round 4): what "
                           "each recorded answer does to the finding it "
                           "answers. The adapters tell an agent how to "
                           "record an answer, never how the ledger "
                           "settles one; the effect is the tool's to "
                           "compute, and stating it in prose would be a "
                           "second copy of a rule one module owns",
        "GIT_MODE_NAMES": "the plain-language name each git mode is "
                          "reported by; it exists so a refusal reads as "
                          "prose, and the procedure never lists modes",
        "FINDING_FIELDS": "the procedure says 'every required field', "
                          "never the field list; the design document's "
                          "enumeration is registered in RESTATEMENTS",
        # Round-9 F2: the legacy import door's closed grammar, one entry
        # per constant so a new one still fails by name. The adapters tell
        # an agent that a legacy corpus is ingested through one verb with
        # its own verified source manifest; the row schema, the manifest
        # schema and the conditional members are the importer's to enforce,
        # and every corpus's own derivation is per-repository by
        # construction, so narrating the shape would publish a grammar no
        # agent writes by hand.
        "LEGACY_EVENT_SCHEMA": "the imported row grammar; the importer "
                               "enforces it and no agent authors a row",
        "LEGACY_CONDITIONAL_REQUIRED": "which member one stated value makes "
                                       "mandatory; an enforcement rule, "
                                       "never procedure text",
        "LEGACY_COMMON_OPTIONAL": "the ledger's own bookkeeping members, "
                                  "admitted on any imported row",
        "LEGACY_IMPORT_ROW_KINDS": "what an `import` row can be; internal "
                                   "to the legacy door",
        "LEGACY_SOURCE_FIELDS": "the two members every imported row carries "
                                "to name the bytes it cites — the importer's "
                                "domain, like the claim's",
        "LEGACY_CONTAINMENT": "which member of each row kind must OCCUR in "
                              "the cited source content; an enforcement "
                              "rule, and one no agent authors a row against",
        "SETTLING_ANSWERS": "the subset of FINDING_ANSWERS that carries "
                            "authorization weight, derived from that table "
                            "for the same reason it is not enumerated: the "
                            "effect is the tool's to compute",
        "LINEAGE_KINDS": "fingerprint-lineage grammar; not in the "
                         "procedure",
        "LINEAGE_MERGING": "fingerprint-lineage grammar; not in the "
                           "procedure",
        "CI_RECEIPT_ROW": "the CI receipt row's field authority — the "
                          "emitter builds the row from it and the validator "
                          "checks against it; a machine contract between two "
                          "modules, and nothing an agent authors or reads in "
                          "the procedure",
        "SEAM_CLASSES": "reader-authority internals; the procedure never "
                        "names seams",
        "STAMPED_PARSE_CALLS": "reader-authority internals; the procedure "
                               "never names parser functions",
        "STAMPED_PARSE_SITES": "reader-authority internals; the procedure "
                               "never names parse sites",
        "STAMPED_READERS": "reader-authority internals; the procedure "
                           "never names readers",
        "TEST_AMENDED_PAYLOAD": "the subtype grammar is the validator's "
                                "and the specification's",
        "TEST_AMENDMENT_OUTCOMES": "the subtype grammar is the "
                                   "validator's and the specification's",
        "TRANSPORT_PROVIDER_SIGNALS": "provider-detection detail; the "
                                      "procedure names the environment "
                                      "declaration only",
    }

    #: The advisory scan reuses the restatement suite's criterion, span
    #: included — one detector, explicitly demoted to tripwire duty here.
    ENUMERATION_SPAN = TestShippedRestatements.ENUMERATION_SPAN

    @staticmethod
    def _collections():
        """Every collection-valued authority vocab exports, by shape —
        the structural inventory's domain. Not just tuples of strings:
        round-1 F3's probe walked a dict straight past a tuples-only
        detector."""
        out = {}
        for name in dir(vocab):
            if not name.isupper():
                continue
            value = getattr(vocab, name)
            if isinstance(value, (tuple, list, dict, set, frozenset)) \
                    and len(value):
                out[name] = value
        return out

    @staticmethod
    def _members_of(value):
        """The member names of a collection, whatever its shape: a
        mapping enumerates by its keys; a tuple of tuples by its rows'
        first elements; a flat collection by its string members."""
        if isinstance(value, dict):
            return [str(k) for k in value]
        if all(isinstance(x, str) for x in value):
            return list(value)
        return [str(x[0]) if isinstance(x, (tuple, list)) and x else str(x)
                for x in value]

    @classmethod
    def _sentinels(cls, name, attrs):
        """The patch set for one vocabulary: same shape, unmistakable.
        Returns (patches, expected sentinel strings)."""
        patches, expected = {}, []
        for attr, fixed in attrs.items():
            if callable(fixed):
                # A nested authority (0.25.0): the entry computes its own
                # patch from the live value, and names its sentinels.
                patches[attr], more = fixed(getattr(vocab, attr))
                expected.extend(more)
                continue
            if fixed is not None:
                patches[attr] = fixed
                expected.append(fixed)
                continue
            value = getattr(vocab, attr)
            if isinstance(value, dict):
                key = f"zz-{name.lower()}-key"
                patches[attr] = {**value, key: "string"}
                expected.append(key)
            else:
                sentinel = tuple(f"zz-{name.lower()}-{i}"
                                 for i in range(len(value)))
                patches[attr] = sentinel
                expected.extend(sentinel)
        return patches, expected

    @contextlib.contextmanager
    def _patched(self, patches):
        saved = {attr: getattr(vocab, attr) for attr in patches}
        try:
            for attr, value in patches.items():
                setattr(vocab, attr, value)
            yield
        finally:
            for attr, value in saved.items():
                setattr(vocab, attr, value)

    def test_the_structural_inventory_is_complete_and_disjoint(self):
        """THE COMPLETENESS PROOF (round-1 F3): every collection vocab
        exports is classified in exactly one of DERIVED, INCIDENTAL,
        NOT_ENUMERATED — mapping-shaped and composite collections
        included. A new collection fails here by name until someone
        decides which it is; a stale entry fails in the other
        direction."""
        collections = set(self._collections())
        classified = (set(self.DERIVED) | set(self.INCIDENTAL)
                      | set(self.NOT_ENUMERATED))
        self.assertEqual(
            sorted(collections - classified), [],
            "collection-valued vocabulary authorities with no decision: "
            "derived, incidental, or deliberately not enumerated")
        self.assertEqual(
            sorted(classified - collections), [],
            "the inventory classifies something vocab no longer exports "
            "as a collection")
        for a, b in (("DERIVED", "INCIDENTAL"),
                     ("DERIVED", "NOT_ENUMERATED"),
                     ("INCIDENTAL", "NOT_ENUMERATED")):
            self.assertEqual(
                sorted(set(getattr(self, a)) & set(getattr(self, b))), [],
                f"a vocabulary is in both {a} and {b}")
        for name, reason in {**self.INCIDENTAL,
                             **self.NOT_ENUMERATED}.items():
            self.assertTrue(str(reason).strip(),
                            f"{name} is classified without a reason")

    def test_every_derived_enumeration_follows_its_authority(self):
        """THE PROBE, per rendered kind (round-1 F3: sentinels are
        required in EVERY rendered kind, not one). Mutation, verified by
        hand: freeze any derived renderer in `adapters.procedure()` to
        the literal text it currently renders, and that vocabulary's
        subtests fail — the sentinel never appears, which is what
        distinguishes a derivation from a restatement that happens to
        agree."""
        for name, attrs in self.DERIVED.items():
            patches, expected = self._sentinels(name, attrs)
            with self._patched(patches):
                for kind in adapters.OUTPUTS:
                    rendered = adapters.render(kind)
                    for member in expected:
                        with self.subTest(vocabulary=name, kind=kind,
                                          member=member):
                            self.assertIn(
                                member, rendered,
                                f"{name}: the rendered {kind} did not "
                                f"follow a mutation of its authority — "
                                f"the enumeration is hand-typed, not "
                                f"derived")

    def test_unpatched_render_carries_every_derived_member(self):
        """The paired control: with the real authorities in place, every
        member of every derived vocabulary appears in every rendered
        kind."""
        for kind in adapters.OUTPUTS:
            rendered = adapters.render(kind)
            for name, attrs in self.DERIVED.items():
                for attr, fixed in attrs.items():
                    value = getattr(vocab, attr)
                    members = ([value] if isinstance(value, str)
                               else self._members_of(value))
                    for member in members:
                        with self.subTest(kind=kind, vocabulary=name,
                                          member=member):
                            self.assertIn(member, rendered)

    def test_the_advisory_scan_flags_nothing_unclassified(self):
        """THE TRIPWIRE, explicitly advisory (round-1 F3 demoted it): the
        restatement suite's proximity detector, run over every rendered
        kind. It catches tuple-shaped member lists it can see and can
        miss shapes it cannot — completeness is the structural inventory
        above, not this. A flag outside DERIVED and INCIDENTAL still
        fails by name, because a visible enumeration with no
        classification is exactly how the adapters/ exclusion went false
        the first time."""
        suite = TestShippedRestatements
        detector = suite._enumerates
        for kind in adapters.OUTPUTS:
            rendered = adapters.render(kind)
            for name, value in sorted(self._collections().items()):
                members = self._members_of(value)
                with self.subTest(kind=kind, vocabulary=name):
                    if not detector(self, rendered, tuple(members)):
                        continue
                    self.assertTrue(
                        name in self.DERIVED or name in self.INCIDENTAL,
                        f"the rendered {kind} enumerates {name} and the "
                        f"enumeration is neither derived from the "
                        f"authority nor declared incidental with a reason "
                        f"— the adapters/ exclusion is false again")


if __name__ == "__main__":
    unittest.main()

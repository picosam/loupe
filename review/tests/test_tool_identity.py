"""Lineage 7 round 1: an installation states the identity of the declared
travelling behavioural set it carries, and the installation reading its
envelope says whether the two agree. (Lineage 8 narrowed the claim: the
identity covers the declared set per its authority — `IDENTITY_ARTEFACTS`
— never "what the installation is"; the enumeration is the attackable
surface, and it is what the boundary gate proves complete.)

The defect this closes fired twice. On 2026-08-23 a reviewer ruled a round
with an installation grafted from three fixes earlier and relayed a command
carrying a shell syntax error that the round under review existed to fix.
Both installations honestly reported `0.4.0`, so nothing in the loop could
see it, and it was reconstructed afterwards only by noticing that the take
event lacked a field the request carried — inference from an absence.

A version string is what a tool chooses to say about itself; the digest of
the files that ran is what it is. `emit.py` has made that argument about
GATE executables since round 3. This makes it about loupe.

The domain closed here: the identity's two required properties (equal
across installations carrying the same code, unequal on any difference),
the manifest's completeness against the package and against the CLI's own
import closure, the three agreement states at the read seam, and what the
ledger records about which installation ruled.
"""
import ast
import json
import operator
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from review import (IDENTITY_ARTEFACTS,
                    SHAPE_ARTEFACTS, shape_identity,
                    TOOL_VERSION,
                    identity_paths, installation_root,
                    production_modules, tool_identity, vocab)
from review.digest import sha256_file_set
from review.ledger import Ledger
from review import cli, transport, wire
from review.tests.util import (LINEAGE, REPO_ROOT, SCOPE_RULES,
                               TRANSPARENT_NODES,
                               ast_grammar_nodes, declared_interval,
                               grammar_fields,
                               grammar_problems, public_path, spec_path,
                               stamped_parse_sites)

PACKAGE = Path(transport.__file__).resolve().parent


class TestTheManifestIsComplete(unittest.TestCase):
    """The manifest is a hand-written list of a machine-known fact, which is
    the shape round-5 F1 found drifting. Its completeness authority is
    therefore not this list and not a glob of the package directory — round
    1's version used exactly that, and the reviewer broke it at the first
    behavioural file outside `review/`: `bin/loupe`, which resolves the
    installation, sets PYTHONSAFEPATH and PYTHONPATH, and stashes the
    caller's environment for the gates. A package glob cannot see it.

    The authority is the EXTRACTION INVENTORY — the generated record of what
    travels, already gated by `extraction-audit`. Every travelling file must
    be carried in the identity or covered by a declared exclusion rule with
    a reason. A new travelling file is unaccounted until someone decides
    which, and that is the tripwire.
    """

    def test_every_carried_artefact_exists_in_this_installation(self):
        """Through the extraction transform, not by naive join: this
        workbench holds `pyproject.toml` as `public/pyproject.toml`."""
        for logical, source in identity_paths().items():
            with self.subTest(logical=logical):
                self.assertTrue(Path(source).is_file(),
                                f"{logical} is carried but not present")

    def test_nothing_the_cli_loads_is_missing_from_the_identity(self):
        """The exclusion may only cover code no CLI path reaches. A module
        that runs but is not in the identity is a behaviour difference the
        digest cannot see — exactly the blind spot this replaces.

        Measured in a FRESH interpreter, because `sys.modules` is global:
        read in-process during a full-suite run it reports whatever any
        other test imported, and this assertion failed on `corpus.py` for
        exactly that reason before the probe was isolated. A test that
        measures the wrong process measures nothing.
        """
        probe = ("import json, sys, review.cli; "
                 "print(json.dumps(sorted(m for m in sys.modules "
                 "if m.startswith('review.'))))")
        out = subprocess.run([sys.executable, "-c", probe], cwd=REPO_ROOT,
                             text=True, capture_output=True, check=True)
        loaded = {"review/" + name.split(".", 1)[1] + ".py"
                  for name in json.loads(out.stdout)
                  if not name.startswith("review.tests")
                  and "." not in name.split(".", 1)[1]}
        self.assertTrue(loaded, "nothing loaded; the probe is broken")
        self.assertEqual(
            loaded - set(IDENTITY_ARTEFACTS), set(),
            "the CLI loads a module the identity does not cover")
        # Loading a stays module (e.g. review/corpus.py) would fail the
        # assertion above — it is not in the travelling enumeration. The
        # workbench boundary suite, where the stays authority lives, holds
        # the other direction: no stays module enters this universe.

    def test_the_version_itself_is_covered(self):
        """`__init__.py` holds TOOL_VERSION, so an identity that omitted it
        would be blind to the very fact it exists to supplement. It is not
        in the CLI's runtime import closure (the package module is
        `review`, not `review.__init__`), which is why the manifest is
        declared rather than computed from `sys.modules`."""
        self.assertIn("review/__init__.py", IDENTITY_ARTEFACTS)
        self.assertIn("review/__main__.py", IDENTITY_ARTEFACTS)


class TestTheIdentityIsContent(unittest.TestCase):
    """Its two required properties. Either one failing makes it useless:
    without equality it cries wolf forever, without inequality it is the
    version string again."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tool-identity-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def _copy(self, name):
        """A minimal installation: every DECLARED artefact at its own path
        below a fresh root, which is what an extracted candidate is.

        Both declared sets are copied — the compared one and the shape one.
        A fixture that carried only the compared set could not express the
        probe RVW-T18 kept from round 1, because there would be no shim to
        break."""
        dest = self.tmp / name
        resolved = dict(identity_paths())
        resolved.update(identity_paths(artefacts=SHAPE_ARTEFACTS))
        for logical, source in resolved.items():
            target = dest / logical
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        return dest

    def _id(self, root):
        return tool_identity(root)

    def _shape(self, root):
        return shape_identity(root)

    def test_same_code_in_two_places_is_one_identity(self):
        """The control. An installation's location, mtimes and the presence
        of files outside the manifest say nothing about what it is."""
        a, b = self._copy("a"), self._copy("b")
        (b / "review/corpus.py").write_text("# not carried\n",
                                   encoding="utf-8")
        (b / "leftover.pyc").write_text("x", encoding="utf-8")
        (b / "README.md").write_text("# docs\n", encoding="utf-8")
        self.assertEqual(self._id(a), self._id(b))
        self.assertEqual(self._id(a), tool_identity())

    def test_one_changed_byte_in_any_carried_module_is_a_new_identity(self):
        """Every carried module, not a sample: a digest that covered most
        of them would be silent about the rest."""
        base = self._copy("base")
        baseline = self._id(base)
        for module in IDENTITY_ARTEFACTS:
            with self.subTest(module=module):
                drifted = self._copy(f"drift-{module}")
                path = drifted / module
                path.write_text(path.read_text(encoding="utf-8") + "\n# x\n",
                                encoding="utf-8")
                self.assertNotEqual(self._id(drifted), baseline)

    def test_a_missing_module_is_a_different_tool(self):
        """An installation with a carried module absent is not the same
        tool with less of it."""
        crippled = self._copy("crippled")
        (crippled / "review/brief.py").unlink()
        self.assertNotEqual(self._id(crippled), tool_identity())

    def test_two_modules_with_swapped_contents_are_a_different_tool(self):
        swapped = self._copy("swapped")
        one = (swapped / "review/vocab.py").read_text(encoding="utf-8")
        two = (swapped / "review/paths.py").read_text(encoding="utf-8")
        (swapped / "review/vocab.py").write_text(two, encoding="utf-8")
        (swapped / "review/paths.py").write_text(one, encoding="utf-8")
        self.assertNotEqual(self._id(swapped), tool_identity())

    def test_the_entrypoint_changes_the_shape_and_the_behaviour(self):
        """FALSIFICATION for round-1 F1, the reviewer's own probe, as
        amended by RVW-T18: insert `exit 73` after the shim's shebang. The
        tool stops working completely.

        Round 1 asserted this against the BEHAVIOURAL identity, because the
        shim was in that set. RVW-T18 moved it, for a reason round 1 could
        not have seen: the same assertion is unsatisfiable in a wheel
        install, where no `bin/loupe` is resolvable at all. So the probe now
        asserts against `shape_identity`, which is exactly where the shim's
        bytes went — the round-1 finding is answered, not dropped, and the
        thing round 1 actually cared about (a shim that decides what runs
        must not be invisible) still holds.

        Mutation: drop `bin/loupe` from SHAPE_ARTEFACTS and this fails on
        the shape while the behaviour assertion still passes, which is the
        original gap in its new home. Second mutation: put it back into
        `IDENTITY_ARTEFACTS` and the boundary suite fails instead."""
        working, broken = self._copy("shim-ok"), self._copy("shim-73")
        shim = broken / "bin/loupe"
        head, _, rest = shim.read_text(encoding="utf-8").partition("\n")
        shim.write_text(f"{head}\nexit 73\n{rest}", encoding="utf-8")
        shim.chmod(0o755)
        (working / "bin/loupe").chmod(0o755)
        ok = subprocess.run([str(working / "bin/loupe"), "--version"],
                            capture_output=True, text=True)
        bad = subprocess.run([str(broken / "bin/loupe"), "--version"],
                             capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(ok.stdout.strip(), TOOL_VERSION)
        self.assertEqual(bad.returncode, 73,
                         "the probe did not actually break the entrypoint")
        self.assertEqual(
            self._id(working), self._id(broken),
            "the shim is not behaviour of the PACKAGE: two trees whose "
            "modules are byte-identical must agree on the behavioural "
            "identity however their launchers differ, or RVW-T18's split "
            "bought nothing")
        self.assertNotEqual(
            self._shape(working), self._shape(broken),
            "the entrypoint decides what runs and no declared set can see "
            "it: round-1 F1 is back")

    def test_an_install_carrying_no_launcher_agrees_on_the_behaviour(self):
        """RVW-T18's whole point, as a falsification.

        A wheel install resolves NEITHER shape artefact — measured: 16 of
        the 18 formerly-declared artefacts present, `pyproject.toml` absent
        from the wheel RECORD and `bin/loupe` present only as a generated
        console script the package cannot resolve. This reproduces that
        shape by deleting both from a copy, and asserts the property the
        split exists to create: the behavioural identity is EQUAL to a full
        tree's, while the shape is not.

        Mutation: put either launcher back into `IDENTITY_ARTEFACTS` and
        the first assertion fails — which is precisely the state in which
        three advertised install paths computed three identities for one
        codebase."""
        full, wheelish = self._copy("full"), self._copy("wheelish")
        for logical in SHAPE_ARTEFACTS:
            target = wheelish / logical
            if target.exists():
                target.unlink()
        self.assertEqual(
            self._id(full), self._id(wheelish),
            "an install shape that carries no launcher must still agree "
            "on what the tool DOES; this is the DIFFERS that fired on "
            "every cross-machine round for no behavioural reason")
        self.assertNotEqual(
            self._shape(full), self._shape(wheelish),
            "absence must be visible SOMEWHERE, or the split discarded "
            "the launchers rather than relocating them")

    def test_a_differing_shape_is_never_a_differing_verdict(self):
        """`shape` is reported, never compared (RVW-T18).

        The failure mode being guarded is a later reader quietly folding
        the shape into the agreement, which would restore exactly the
        false DIFFERS the split removed. Two envelopes identical but for
        their `shape` attribute must yield the same `agreement`.
        """
        from review import transport

        class _Parsed:
            def __init__(self, attrs):
                self.attrs = attrs

        mine = tool_identity()
        same = transport.tool_agreement(
            _Parsed({"tool": mine, "shape": "0000000000000000"}))
        other = transport.tool_agreement(
            _Parsed({"tool": mine, "shape": "ffffffffffffffff"}))
        self.assertEqual(same["agreement"], "match")
        self.assertEqual(other["agreement"], "match")
        self.assertEqual(same["writer_shape"], "0000000000000000")
        self.assertEqual(other["writer_shape"], "ffffffffffffffff")
        absent = transport.tool_agreement(_Parsed({"tool": mine}))
        self.assertEqual(absent["agreement"], "match",
                         "an envelope predating the split declares no "
                         "shape; historical silence is not disagreement")
        self.assertIsNone(absent["writer_shape"])

    def test_two_extracted_installations_of_one_tree_match(self):
        """The control F1 required beside the mutation: byte-identical
        installations agree, wherever they sit and whatever non-carried
        files surround them."""
        a, b = self._copy("extract-a"), self._copy("extract-b")
        (b / "review/corpus.py").write_text("# workbench only\n",
                                            encoding="utf-8")
        (b / "docs").mkdir(parents=True, exist_ok=True)
        (b / "docs/design.md").write_text("# spec\n", encoding="utf-8")
        self.assertEqual(self._id(a), self._id(b))

    def test_the_value_depends_on_the_files_and_nothing_else(self):
        """What the canonical serialization actually buys, asserted rather
        than described: the caller's ordering and duplicates cannot move
        the value. (The first draft of this class also claimed the embedded
        path detects a content swap and that `absent` detects an incomplete
        tree; mutations showed neither is what makes those cases fail, and
        both claims were withdrawn rather than left standing as untested
        prose.)"""
        base = self._copy("base")
        resolved = identity_paths(base)
        straight = sha256_file_set(resolved)
        shuffled = sha256_file_set(dict(reversed(list(resolved.items()))))
        self.assertEqual(straight, shuffled)
        subset = sha256_file_set(
            {k: v for k, v in resolved.items() if k != "review/vocab.py"})
        self.assertNotEqual(straight, subset,
                            "a smaller declared set must not collide with "
                            "the full one")

    def test_the_version_string_alone_would_not_have_seen_it(self):
        """The 2026-08-23 case, stated: two installations differing in code
        while agreeing on `TOOL_VERSION`. The version is equal; the
        identity is not. This is the whole argument for the mechanism."""
        drifted = self._copy("intra-version")
        target = drifted / "review/brief.py"
        target.write_text(target.read_text(encoding="utf-8")
                          + "\n# a fix the other installation does not have\n",
                          encoding="utf-8")
        self.assertIn(f'TOOL_VERSION = "{TOOL_VERSION}"',
                      (drifted / "review/__init__.py").read_text(encoding="utf-8"),
                      "both installations report the same version")
        self.assertNotEqual(self._id(drifted), tool_identity())


class TestTheEnvelopesCarryIt(unittest.TestCase):
    """Which envelopes are stamped, and — round-1 F3 — which READER
    compares each stamp. A stamp with no reader is a decoration, and the
    specification claimed a comparison the code did not make.

    The verdict is deliberately unstamped: it is hand-authored by the
    reviewer agent from a shape block, so a stamp there would be a model
    transcribing a digest, which proves nothing about the code that ran.
    """

    def test_a_disposition_carries_the_emitting_installation(self):
        envelope = wire.emit_disposition(
            tag="loupe", verdict_sha="a" * 40, head="b" * 40,
            author="claude", round_no=1, dispositions=[])
        self.assertEqual(wire.parse_disposition(envelope).attrs.get("tool"),
                         tool_identity())

    def test_the_verdict_carries_no_stamp_and_the_spec_says_so(self):
        """The absence is a decision, asserted so it cannot be quietly
        reversed into an unverifiable field."""
        spec = spec_path()
        if spec is None:
            self.skipTest("no specification shipped in this tree")
        text = spec.read_text(encoding="utf-8")
        self.assertIn("The verdict is **not** stamped", text,
                      "the specification no longer states the decision")
        self.assertIn("hand-authors it from a shape block", text,
                      "the specification no longer states WHY")
        # And the code keeps it: wherever a verdict wrapper is written —
        # including the shape block that TELLS the reviewer agent what to
        # write — no `tool` attribute goes with it. Checked around each
        # occurrence rather than by absence of the tag, because the shape
        # block legitimately contains one and a blanket ban would have
        # matched it.
        for module in ("emit.py", "wire.py", "transport.py", "brief.py"):
            source = (REPO_ROOT / "review" / module).read_text(
                encoding="utf-8")
            for match in re.finditer(r"-review-verdict", source):
                window = source[match.start():match.start() + 200]
                self.assertNotIn(
                    'tool="', window,
                    f"{module} stamps a verdict wrapper; the decision not "
                    f"to is deliberate and needs revisiting, not "
                    f"inheriting")

    def test_the_public_overview_narrows_the_stamp_to_its_emitters(self):
        """Lineage 20 round 1, F1: the overview claimed EVERY envelope
        carries the `tool` digest, while the verdict deliberately carries
        none — the exact decision the test above keeps. The published
        restatement is bound here, beside that decision: the overview must
        name the two tool-emitted kinds as the stamped ones and state the
        verdict's absence, and the universal claim cannot return.

        MUTATION: restore "Every envelope carries" in docs/overview.md and
        this fails while the request and disposition stamps above stay the
        paired valid controls.

        The member list is DERIVED from `vocab.STAMPED_KINDS` rather than
        quoted: when the authorization joined the stamped kinds this test
        demanded the sentence follow, which is the whole reason to bind
        published prose to an authority instead of to a phrase."""
        doc = public_path("docs/overview.md")
        if doc is None:
            self.skipTest("no overview shipped in this tree")
        text = " ".join(doc.read_text(encoding="utf-8").split())
        self.assertNotIn("Every envelope carries", text,
                         "the overview universalises the stamp again; the "
                         "verdict is deliberately unstamped")
        head, _, rest = text.partition("carry a content digest (`tool`)")
        self.assertTrue(rest, "the overview no longer states the stamp")
        claim = head[-220:]
        for kind in vocab.STAMPED_KINDS:
            self.assertIn(kind, claim,
                          f"the overview names the stamped kinds and omits "
                          f"{kind!r}, which IS stamped")
        self.assertNotIn("verdict", claim,
                         "the overview lists the verdict among the kinds "
                         "that carry the stamp; it is deliberately unstamped")
        self.assertIn("The verdict carries none", text)

    def test_every_stamped_kind_is_stamped_by_its_emitter(self):
        """Both tool-emitted kinds carry the stamp. The comparison half is
        exercised through the production path in
        `TestTheDispositionDoorCompares` below — a source-level check for
        the string `tool_agreement` passed even with the whole comparison
        deleted, because `render_tool_agreement` contains it. A test that
        can be satisfied by an unrelated identifier is not a test."""
        emitters = {"request": "emit.py", "disposition": "wire.py",
                    "authorization": "wire.py"}
        # DERIVED from the authority, not listed beside it: a kind added to
        # STAMPED_KINDS with no emitter named here fails on the next line
        # rather than going unchecked, which is how the two-kind version of
        # this test would have greeted a third.
        self.assertEqual(sorted(emitters), sorted(vocab.STAMPED_KINDS),
                         "a stamped kind has no emitter to check")
        for kind, emitter in sorted(emitters.items()):
            with self.subTest(kind=kind):
                source = (REPO_ROOT / "review" / emitter).read_text(
                    encoding="utf-8")
                self.assertIn(f"-review-{kind}", source)
                self.assertIn('tool="{tool_identity()}"', source,
                              f"the {kind} emitter no longer stamps")

    def test_an_unknown_attribute_does_not_break_an_older_reader(self):
        """The compatibility fact this rests on, asserted rather than
        assumed: the wrapper grammar has no attribute allow-list, so an
        installation predating the stamp parses a stamped envelope without
        complaint. Detection is one-directional on first deployment, and
        that is a stated limit, not an oversight."""
        envelope = wire.emit_disposition(
            tag="loupe", verdict_sha="a" * 40, head="b" * 40,
            author="claude", round_no=1, dispositions=[])
        parsed = wire.parse_disposition(envelope)
        self.assertTrue(parsed.wrapped)
        self.assertEqual(parsed.attrs["head"], "b" * 40)


class TestTheThreeAgreementStates(unittest.TestCase):
    """The read seam. `differs` is why this exists; `unstamped` is the
    state that must not be folded into `match`, because historical silence
    is not agreement."""

    class _Parsed:
        def __init__(self, attrs):
            self.attrs = attrs

    def test_an_envelope_from_this_installation_matches(self):
        result = transport.tool_agreement(
            self._Parsed({"tool": tool_identity()}))
        self.assertEqual(result["agreement"], "match")
        self.assertEqual(result["reader"], result["writer"])

    def test_an_envelope_from_another_installation_differs(self):
        result = transport.tool_agreement(
            self._Parsed({"tool": "0" * 16}))
        self.assertEqual(result["agreement"], "differs")
        self.assertEqual(result["writer"], "0" * 16)
        self.assertEqual(result["reader"], tool_identity())
        self.assertIn("DIFFERENT declared behavioural set", result["note"])

    def test_an_unstamped_envelope_is_its_own_state(self):
        """FALSIFICATION. Mutation: return `match` (or nothing) when the
        attribute is absent — an envelope from any older installation then
        reports agreement that was never established, and this fails."""
        for attrs in ({}, {"tool": ""}, {"sha": "a" * 40}):
            with self.subTest(attrs=attrs):
                result = transport.tool_agreement(self._Parsed(attrs))
                self.assertEqual(result["agreement"], "unstamped")
                self.assertIsNone(result["writer"])
                self.assertEqual(result["reader"], tool_identity())

    def test_it_reports_and_never_refuses(self):
        """The decision this round takes, pinned so a later one cannot
        change it silently: a digest carries no ordering, so `differs`
        cannot tell a reader that is behind from one that is ahead, and
        refusing a sound envelope because the reviewer's tool is NEWER
        would block the better of the two."""
        for attrs in ({"tool": "0" * 16}, {}, {"tool": tool_identity()}):
            result = transport.tool_agreement(self._Parsed(attrs))
            self.assertIn(result["agreement"],
                          ("match", "differs", "unstamped"))
            self.assertNotIn("refus", json.dumps(result).lower())


class TestTheDispositionDoorCompares(unittest.TestCase):
    """Round-1 F3, through the production path the finding named.

    `ledger add` is the one door a disposition can arrive at from another
    installation. `respond --out` writes and records in one process, so a
    comparison there could only ever say `match` — a check that cannot fail
    is not a check, and the stamp was inert until this seam read it.
    """

    def setUp(self):
        import dataclasses
        from review.tests import synth
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="disposition-door-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.cfg = dataclasses.replace(synth.NO_GATES, ledger_dir=self.tmp)
        self.synth = synth

    def _recorded_round(self):
        """A request and its verdict recorded the way the loop records
        them: `ledger add` for the request, `close` for the verdict —
        because a disposition may only answer a verdict whose KEPT bytes
        reproduce the recorded digest, and only `close` keeps them."""
        from review import validate
        from review.tests._transport_fixtures import (SHA_B, request_text,
                                                 verdict_text)
        path = self.tmp / "req.md"
        path.write_text(request_text(sha=SHA_B), encoding="utf-8")
        code, payload = _cli_add(self.cfg, self.tmp, path)
        self.assertEqual(code, 0, payload)
        transport.close_round(
            self.cfg, Ledger(self.tmp), verdict_text(sha=SHA_B), "verdict.md",LINEAGE,
            validate_items=lambda v: validate.validate_verdict(v, self.cfg))
        return SHA_B

    def _disposition(self, sha, tool=None):
        from review.tests._transport_fixtures import verdict_text
        from review import validate
        parsed_verdict = wire.parse_verdict(verdict_text(sha=sha))
        rows = [{"finding_id": f.id, "disposition": "refuted",
                 "payload": {"argument": "x" * 40, "evidence": "e" * 40}}
                for f in parsed_verdict.findings]
        envelope = wire.emit_disposition(
            tag="loupe", verdict_sha=sha, head="c" * 40, author="claude",
            round_no=1, dispositions=rows)
        if tool is not None:
            envelope = envelope.replace(f'tool="{tool_identity()}"',
                                        f'tool="{tool}"')
        path = self.tmp / "disposition.md"
        path.write_text(envelope, encoding="utf-8")
        return path

    def test_a_disposition_from_another_installation_reports_differs(self):
        """FALSIFICATION for round-1 F3, the reviewer's own probe: replace
        only the `tool` attribute with zeros and pass it through the
        production recording path. Before this round it recorded the
        disposition with no agreement result at all. Mutation: delete the
        comparison from the `ledger add` disposition branch and this fails
        while the same envelope still records — which is exactly the state
        it was found in."""
        sha = self._recorded_round()
        code, payload = _cli_add(self.cfg, self.tmp,
                                 self._disposition(sha, tool="0" * 16))
        self.assertEqual(code, 0, payload)
        self.assertIn("tool", payload, "the door reports no agreement")
        self.assertEqual(payload["tool"]["agreement"], "differs")
        self.assertEqual(payload["tool"]["writer"], "0" * 16)
        self.assertEqual(payload["tool"]["reader"], tool_identity())
        ingests = [e for e in Ledger(self.tmp).events()
                   if e.get("event") == "ingest"
                   and e.get("kind") == "disposition"]
        self.assertEqual(len(ingests), 1)
        self.assertEqual(ingests[0]["tool_agreement"], "differs")
        self.assertEqual(ingests[0]["tool_writer"], "0" * 16)

    def test_a_disposition_from_this_installation_matches(self):
        """The paired control: the unmodified envelope this installation
        wrote, through the same door."""
        sha = self._recorded_round()
        code, payload = _cli_add(self.cfg, self.tmp, self._disposition(sha))
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["tool"]["agreement"], "match")
        ingests = [e for e in Ledger(self.tmp).events()
                   if e.get("event") == "ingest"
                   and e.get("kind") == "disposition"]
        self.assertEqual(ingests[0]["tool_agreement"], "match")

    def test_an_unstamped_disposition_is_reported_as_such(self):
        """The third state at this seam: an envelope from an installation
        older than the stamp records `unstamped`, and names no writer."""
        sha = self._recorded_round()
        path = self._disposition(sha)
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                f' tool="{tool_identity()}"', ""), encoding="utf-8")
        code, payload = _cli_add(self.cfg, self.tmp, path)
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["tool"]["agreement"], "unstamped")
        self.assertIsNone(payload["tool"]["writer"])
        ingests = [e for e in Ledger(self.tmp).events()
                   if e.get("event") == "ingest"
                   and e.get("kind") == "disposition"]
        self.assertNotIn("tool_writer", ingests[0])

    def test_the_disposition_events_record_who_wrote_the_answer(self):
        """Not only the ingest: each disposition row carries the writing
        installation, so the record of an ANSWER says which tool produced
        it however it later arrives."""
        sha = self._recorded_round()
        _cli_add(self.cfg, self.tmp, self._disposition(sha, tool="0" * 16))
        rows = [e for e in Ledger(self.tmp).events()
                if e.get("event") == "disposition"]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["tool"], "0" * 16)


class TestEveryStampedReaderCompares(unittest.TestCase):
    """Round-2 F2: the reader topology, derived from ONE declared authority
    rather than from whichever door was noticed last.

    Round 1 wired `take`. Round 2's reviewer found `brief` — an official
    read-only verb, available to either side at any time, that RENDERS the
    command a human carries — reading a stamped request and saying nothing.
    That is precisely the output a stale installation gets wrong: it is the
    2026-08-23 incident's own shape. And fixing only `brief` would have left
    `validate` and the request branch of `ledger add` as the next
    unpartitioned doors.

    `vocab.STAMPED_READERS` is the authority. Every seam classed `cross` is
    exercised here against all three agreement states; the one classed
    `same_process` is asserted NOT to compare, because a check that can only
    ever say `match` is worse than a stated absence.
    """

    def setUp(self):
        import dataclasses
        from review.tests import synth
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="reader-matrix-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.cfg = dataclasses.replace(synth.NO_GATES, ledger_dir=self.tmp)
        self.synth = synth

    def _request(self, tool="keep"):
        """A valid request stamped `match`, `differs` or not at all."""
        text = self.synth.emitted_request()
        stamp = re.search(r'tool="([0-9a-f]*)"', text)
        self.assertIsNotNone(stamp, "the emitter no longer stamps requests")
        if tool == "differs":
            text = text.replace(stamp.group(0), 'tool="0000000000000000"')
        elif tool == "unstamped":
            text = text.replace(" " + stamp.group(0), "")
        path = self.tmp / "request.md"
        path.write_text(text, encoding="utf-8")
        return path

    def _run(self, fn, **kw):
        import argparse
        import contextlib
        import io
        buf = io.StringIO()
        args = argparse.Namespace(ledger_dir=str(self.tmp), **kw)
        with contextlib.redirect_stdout(buf):
            code = fn(args, self.cfg)
        out = buf.getvalue()
        try:
            return code, json.loads(out), out
        except json.JSONDecodeError:
            return code, {}, out

    EXPECTED = {"keep": "match", "differs": "differs",
                "unstamped": "unstamped"}

    def test_brief_compares_before_it_renders_the_relay(self):
        """FALSIFICATION for round-2 F2, the reviewer's own probe: replace
        only the request's `tool` stamp with zeros and run `brief`. Before
        this round it returned the live relay with no agreement field and no
        identity line. Mutation: delete the `tool_agreement` call from the
        request branch of `cmd_brief` and all three cases fail."""
        for stamp, expected in self.EXPECTED.items():
            with self.subTest(stamp=stamp):
                code, payload, text = self._run(
                    cli.cmd_brief, envelope=str(self._request(stamp)),
                    full=False, paste=False)
                self.assertEqual(code, 0, payload or text)
                self.assertEqual(payload["tool"]["agreement"], expected)
                self.assertIn("relay", payload,
                              "the relay is still rendered — the comparison "
                              "informs it, it does not replace it")

    def test_validate_compares_on_every_stamped_kind(self):
        for stamp, expected in self.EXPECTED.items():
            with self.subTest(stamp=stamp):
                code, payload, text = self._run(
                    cli.cmd_validate, envelope=str(self._request(stamp)),
                    against=None, from_target=False)
                self.assertEqual(payload["tool"]["agreement"], expected,
                                 payload or text)

    def test_ledger_add_compares_on_a_request(self):
        for stamp, expected in self.EXPECTED.items():
            with self.subTest(stamp=stamp):
                fresh = Path(tempfile.mkdtemp(dir=self.tmp))
                import dataclasses
                cfg = dataclasses.replace(self.cfg, ledger_dir=fresh)
                import argparse
                import contextlib
                import io
                buf = io.StringIO()
                args = argparse.Namespace(
                    envelope=str(self._request(stamp)), round=None,
                    tokens=None, ledger_dir=str(fresh))
                with contextlib.redirect_stdout(buf):
                    code = cli.cmd_ledger_add(args, cfg)
                self.assertEqual(code, 0, buf.getvalue())
                payload = json.loads(buf.getvalue())
                self.assertEqual(payload["tool"]["agreement"], expected)
                ingest = [e for e in Ledger(fresh).events()
                          if e.get("event") == "ingest"
                          and e.get("kind") == "request"]
                self.assertEqual(len(ingest), 1)
                self.assertEqual(ingest[0]["tool_agreement"], expected)

    @staticmethod
    def _discover_parse_sites():
        """Every function in a PRODUCTION module that parses a stamped
        envelope, found by walking the module universe the identity
        declares — not a tuple written here.

        Round-4 F1: the first version walked fourteen module names typed
        into this test, so `review/corpus.py` was outside it (and does parse
        a request), and a new module could be added and never scanned. The
        universe now comes from `review.production_modules()`, derived from
        the same enumeration the boundary gate proves equals what travels.

        Lineage 8: the walk itself is `util.stamped_parse_sites` — THE
        scanner, which owns its call domain and is probed by synthetic
        modules below. This test contributes only the universe.
        """
        found = set()
        for logical in production_modules():
            source = (REPO_ROOT / logical).read_text(encoding="utf-8")
            module = logical.split("/")[-1][:-3]
            found |= stamped_parse_sites(module, source)
        return found

    def test_no_production_parse_of_a_stamped_envelope_is_unclassified(self):
        """FALSIFICATION for round-3 F1 and round-4 F1 together.

        Round 3's matrix bound the reader authority to a second HAND-WRITTEN
        list, so a reader missing from both was invisible to both — which is
        how the warm handoff cache went undeclared. Round 4's repair moved
        that hand list one level down, from reader names to MODULE names,
        and the reviewer broke it at `review/corpus.py`. Both times the
        mechanism was only as complete as something someone typed.

        The universe is now derived from `production_modules()`, which the
        boundary gate proves equals the declared travelling set plus the
        declared workbench-only modules. A module cannot exist without being
        scanned, so a new reader — however its caller names the helper that
        reaches it — is discovered where it parses.

        Mutations: delete any entry from STAMPED_PARSE_SITES and this names
        it; add a module that parses and attribute it nowhere and this names
        that too.
        """
        discovered = self._discover_parse_sites()
        self.assertTrue(discovered, "the walk found nothing; the probe is "
                                    "broken")
        declared = set(vocab.STAMPED_PARSE_SITES)
        self.assertEqual(
            sorted(discovered - declared), [],
            "production code parses a stamped envelope somewhere the reader "
            "authority does not name: an unclassified read path is how the "
            "warm handoff cache stayed silent")
        self.assertEqual(
            sorted(declared - discovered), [],
            "the authority names a parse site that no longer exists")

    def test_the_scan_universe_is_derived_and_not_typed_here(self):
        """The regression round-4 F1 named, as its own assertion: this test
        carries no module list of its own. Every module of the installed
        tree is in the derived universe, and every one of them exists."""
        universe = production_modules()
        self.assertIn("review/cli.py", universe)
        self.assertIn("review/__init__.py", universe)
        self.assertEqual(
            sorted(universe),
            sorted(p for p in IDENTITY_ARTEFACTS
                   if p.startswith("review/") and p.endswith(".py")),
            "the scan universe is not the identity's own module set")
        for logical in universe:
            self.assertTrue((REPO_ROOT / logical).is_file(), logical)

    #: Every comparison operator PEP 440 defines for a version specifier.
    #: This is the operator AUTHORITY: the regex below is built from it, so
    #: the syntax the gate recognises cannot drift from the set it
    #: classifies, and the mutation set is derived from it rather than from
    #: the refusal table — an entry deleted from that table then fails the
    #: completeness assertion instead of silently deleting its own test
    #: case (found by mutating this fix).
    RECOGNISED_OPS = ("===", "==", "!=", "~=", ">=", "<=", ">", "<")

    #: Clause operators whose meaning a (major, minor) model reproduces
    #: EXACTLY, and so the only ones this gate will judge. `>=3.14` admits
    #: every 3.14 patch and everything above; `<3.15` excludes every 3.15
    #: patch and everything above. Neither says anything about a patch
    #: within a minor, which is precisely why a minor-granular model can
    #: prove them.
    MINOR_EXACT_OPS = {">=": operator.ge, "<": operator.lt}

    #: Operators this gate RECOGNISES and refuses, with the reason. Under
    #: PEP 440 each carries patch-level force that a (major, minor) model
    #: silently erases (round-6 F1: `==3.14` and `>=3.14,<=3.14` passed the
    #: adjacent-minor assertions while admitting only 3.14.0 and refusing
    #: the 3.14.7 this suite runs on). The mutation set below is DERIVED
    #: from this table, so a spelling added here cannot keep the bypass.
    PATCH_SENSITIVE_OPS = {
        "==": "admits one release only — `==3.14` is 3.14.0, not 3.14.x",
        "<=": "`<=3.14` excludes 3.14.1 and later, since they compare "
              "greater than 3.14",
        ">": "`>3.14` excludes 3.14.0 while admitting later 3.14 patches",
        "!=": "excludes a single release from within a minor",
        "~=": "a compatible-release clause whose bound depends on the "
              "component count, not on the minor alone",
        "===": "an arbitrary-equality clause with no ordering semantics",
    }

    @classmethod
    def _admitted_minors(cls, spec):
        """`requires-python` as a predicate over (major, minor).

        FAIL-CLOSED (round-6 F1): only `MINOR_EXACT_OPS` are judged, so the
        admitted syntax is never wider than the semantics this model can
        prove. Every other operator — recognised-and-patch-sensitive, or
        not recognised at all — raises rather than being evaluated at a
        granularity that erases its meaning. That is the same posture as
        refusing an undeclared taxonomy: a check that cannot judge a shape
        says so instead of guessing.

        TWO authorities, each over its own question. The OPERATOR loop below
        is this gate's own — it is what names `==` and `~=` as refused and
        why, and the mutation set is derived from those tables. The
        SPELLING of the value is `declared_interval`, the one grammar every
        travelling reader of `project.requires-python` shares (2026-09-19).
        Until then the loop was also the spelling authority, with a regex
        that admitted optional whitespace, Unicode `\\d`, leading zeros,
        components of any length and a third clause — so this gate accepted
        values the generator downstream refused, which is the disagreement
        a reviewer named across two lineages. The operator classification
        runs FIRST, so a recognised-but-refused spelling still gets its
        reason rather than a bare shape error; the bounds then come from
        the shared authority, never from this regex's own `int()`.
        """
        ops = []
        for raw in spec.split(","):
            alternation = "|".join(re.escape(op) for op in sorted(
                cls.RECOGNISED_OPS, key=len, reverse=True))
            found = re.fullmatch(rf"\s*({alternation})\s*"
                                 rf"(\d+)\.(\d+)(\.\S+)?\s*", raw)
            if found is None:
                raise ValueError(
                    f"requires-python clause {raw!r} is not a bare "
                    f"`<op>X.Y`; this gate judges only the shapes whose "
                    f"admitted interval it can compute")
            op, patch = found.group(1), found.group(4)
            if patch is not None:
                raise ValueError(
                    f"requires-python clause {raw!r} names a patch "
                    f"component; the supported interval is a MINOR, "
                    f"because a minor is what carries an ast grammar")
            if op in cls.PATCH_SENSITIVE_OPS:
                raise ValueError(
                    f"requires-python clause {raw!r} uses `{op}`, which "
                    f"this gate refuses: {cls.PATCH_SENSITIVE_OPS[op]}. A "
                    f"(major, minor) model cannot prove it, and evaluating "
                    f"it at minor granularity would report full-minor "
                    f"support for a patch-pinned contract")
            if op not in cls.MINOR_EXACT_OPS:
                raise ValueError(
                    f"requires-python clause {raw!r} uses an operator this "
                    f"gate does not judge")
            ops.append(op)
        floor, below = declared_interval(spec)
        bounds = {">=": floor, "<": below}
        clauses = [(cls.MINOR_EXACT_OPS[op], bounds[op]) for op in ops]
        return lambda version: all(op(version, bound)
                                   for op, bound in clauses)

    def test_the_supported_interval_equals_the_gated_grammar(self):
        """The claim is no broader than its authority, at BOTH ends
        (round-5 F1) and at patch granularity (round-6 F1).

        The scanner's scope domain is closed against the `ast` grammar of
        the interpreter running this gate, and CI runs that one minor. The
        packaging contract must admit exactly that minor: the running one
        admitted, the minor below and above refused — and the whole minor,
        which is why every patch-sensitive operator is refused rather than
        evaluated at a granularity that would erase it.

        Mutation: delete the upper bound, widen it, drop the floor, or
        write any patch-sensitive spelling, and this fails naming what it
        admits. The bounded declaration is the paired valid control.
        """
        import tomllib
        pyproject = public_path("pyproject.toml")
        if pyproject is None:
            self.skipTest("no pyproject shipped in this tree")
        spec = tomllib.loads(pyproject.read_text(encoding="utf-8"))[
            "project"]["requires-python"]
        try:
            admits = self._admitted_minors(spec)
        except ValueError as exc:
            self.fail(str(exc))
        major, minor = sys.version_info[:2]
        self.assertTrue(
            admits((major, minor)),
            f"requires-python {spec!r} does not admit {major}.{minor}, the "
            f"interpreter this gate and CI actually run")
        for unsupported in ((major, minor - 1), (major, minor + 1)):
            with self.subTest(minor=f"{unsupported[0]}.{unsupported[1]}"):
                self.assertFalse(
                    admits(unsupported),
                    f"requires-python {spec!r} admits "
                    f"{unsupported[0]}.{unsupported[1]}, which no grammar "
                    f"authority and no CI job covers: the packaging "
                    f"contract is wider than what is gated")

    def test_no_patch_sensitive_spelling_passes_the_interval_gate(self):
        """FALSIFICATION for round-6 F1, with the mutation set DERIVED
        from the operator table rather than typed here — a spelling added
        to `PATCH_SENSITIVE_OPS` cannot retain the bypass, and one added
        to `MINOR_EXACT_OPS` must earn a reason first.

        The defect: `==3.14` and `>=3.14,<=3.14` admit 3.14.0 and refuse
        the 3.14.7 this suite runs on, yet passed every adjacent-minor
        assertion, because the model compared `(major, minor)` tuples
        while the operator's force is at patch level.
        """
        major, minor = sys.version_info[:2]
        refused = [op for op in self.RECOGNISED_OPS
                   if op not in self.MINOR_EXACT_OPS]
        specs = [f"{op}{major}.{minor}" for op in refused]
        specs += [f">={major}.{minor},{op}{major}.{minor}" for op in refused]
        specs.append(f">={major}.{minor}.0,<{major}.{minor + 1}")
        specs.append("")
        for spec in specs:
            with self.subTest(spec=spec):
                with self.assertRaises(ValueError):
                    self._admitted_minors(spec)
        # The paired valid control, and the two operators it uses.
        admits = self._admitted_minors(f">={major}.{minor},<{major}.{minor + 1}")
        self.assertTrue(admits((major, minor)))
        self.assertEqual(sorted(self.MINOR_EXACT_OPS), ["<", ">="])
        self.assertEqual(
            sorted(set(self.MINOR_EXACT_OPS) & set(self.PATCH_SENSITIVE_OPS)),
            [], "an operator is both minor-exact and patch-sensitive")
        classified = set(self.MINOR_EXACT_OPS) | set(self.PATCH_SENSITIVE_OPS)
        self.assertEqual(
            sorted(set(self.RECOGNISED_OPS) - classified), [],
            "a recognised operator is neither judged nor refused with a "
            "reason: it would drop out of the derived mutation set")
        self.assertEqual(
            sorted(classified - set(self.RECOGNISED_OPS)), [],
            "an operator is classified that the parser does not recognise")
        for op, reason in self.PATCH_SENSITIVE_OPS.items():
            self.assertTrue(str(reason).strip(),
                            f"{op} is refused without a reason")

    def test_no_spelling_the_shared_authority_refuses_passes_here(self):
        """The other half of the same gate: the SPELLING, decided by
        `declared_interval` rather than by a regex of this file's own.

        Each row was admitted here and refused by the generator that reads
        the same value — the disagreement two lineages named. FALSIFICATION.
        Mutation: take the bounds from this file's own clause regex again
        (`int(found.group(2))`, `int(found.group(3))`) and every row below
        goes green while the generator keeps refusing it.
        """
        major, minor = sys.version_info[:2]
        for label, spec in {
                "padding after the operator": f">= {major}.{minor},"
                                              f"<{major}.{minor + 1}",
                "padding after the comma": f">={major}.{minor}, "
                                           f"<{major}.{minor + 1}",
                "a leading zero": f">={major}.0{minor},"
                                  f"<{major}.{minor + 1}",
                "a non-ASCII decimal digit": ">=٣.١٤,<٣.١٥",
                "a component past the four-digit bound":
                    f">={'1' * 5}.{minor},<{major}.{minor + 1}",
                "a multiline value whose first line is admitted":
                    f">={major}.{minor},<{major}.{minor + 1}\n"
                    f">=3.11,<3.12",
        }.items():
            with self.subTest(spelling=label, spec=spec):
                with self.assertRaises(ValueError):
                    self._admitted_minors(spec)
        # The paired control: one character back from each family.
        admits = self._admitted_minors(
            f">={major}.{minor},<{major}.{minor + 1}")
        self.assertTrue(admits((major, minor)))

    def test_this_package_has_ONE_grammar_for_the_declared_interval(self):
        """The completeness half of the authority: no second reader.

        The debt was three programs parsing one value, each with a regex of
        its own, held to one shape by a comment. Making them agree is only
        half a fix — the other half is that a FOURTH cannot appear
        unnoticed. Every `re.*` call in this package whose pattern names
        `>=` is a reader of an interpreter interval, and there must be
        exactly one: the authority itself.

        FALSIFICATION. Mutation: restore the
        `re.search(r">=\\s*(\\d+)\\.(\\d+)", declared)` that
        `test_handoff_preflight` used until 2026-09-19 and this names it.
        """
        sites = []
        for path in sorted((REPO_ROOT / "review").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                function = getattr(node, "func", None)
                if not (isinstance(node, ast.Call)
                        and isinstance(function, ast.Attribute)
                        and isinstance(function.value, ast.Name)
                        and function.value.id == "re"
                        and function.attr in ("compile", "search", "match",
                                              "fullmatch")
                        and node.args):
                    continue
                pattern = "".join(
                    piece.value for piece in ast.walk(node.args[0])
                    if isinstance(piece, ast.Constant)
                    and isinstance(piece.value, str))
                if ">=" in pattern:
                    sites.append(f"{path.relative_to(REPO_ROOT)}:"
                                 f"{node.lineno}")
        self.assertEqual(
            [site.split(":")[0] for site in sites],
            ["review/tests/util.py"],
            f"more than one program in this package parses an interpreter "
            f"interval: {sites}")

    def test_the_shipped_prose_names_the_pinned_minor(self):
        """The floor is a promise on every shipped surface, not only in
        the metadata: a README saying `3.14+` re-advertises the range the
        interval just closed."""
        major, minor = sys.version_info[:2]
        for name in ("README.md", "docs/design.md"):
            document = public_path(name)
            if document is None:
                continue
            text = document.read_text(encoding="utf-8")
            with self.subTest(document=name):
                self.assertNotIn(
                    f"Python {major}.{minor}+", text,
                    f"{name} advertises an open-ended range the packaging "
                    f"contract does not")
                self.assertIn(f"Python {major}.{minor}.x", text,
                              f"{name} does not name the pinned minor")

    def test_the_scope_grammar_covers_the_ast_grammar(self):
        """THE AUTHORITY GATE. The scanner's execution-container domain is
        declared closed relative to the `ast` grammar — generated by
        CPython, finite, reviewable — and every node type and every field
        of every scope-introducing node is classified exactly once, with
        no prefix rule and no default arm.

        Mutations: delete any node from TRANSPARENT_NODES or SCOPE_RULES,
        or any field from a rule, and this names it.
        """
        self.assertEqual(
            grammar_problems(ast_grammar_nodes(), grammar_fields), [])

    def test_an_unclassified_node_or_field_is_named(self):
        """FALSIFICATION for the authority gate, run against a MODIFIED
        grammar rather than by editing the tables: a node type the grammar
        grows, and a field a scope-introducing node grows, must each be
        named. This is the reviewer's own probe shape, kept permanent —
        the live grammar above is the paired valid control."""
        grown = ast_grammar_nodes() | {"ZzNewLazyScope"}
        self.assertIn(
            "ZzNewLazyScope",
            " ".join(grammar_problems(grown, grammar_fields)),
            "a node type nothing classifies went unnamed")

        def with_new_field(name):
            fields = grammar_fields(name)
            if name == "FunctionDef" and fields is not None:
                return fields + ("zz_new_field",)
            return fields

        self.assertIn(
            "FunctionDef.zz_new_field",
            " ".join(grammar_problems(ast_grammar_nodes(), with_new_field)),
            "an unrouted field of a scope-introducing node went unnamed")

    def test_lazy_type_parameter_scopes(self):
        """Round-3 F1's grammar, version-gated: every PEP 695 / PEP 696
        lazy annotation scope reports `<annotation>`, with paired
        no-parser controls. The future import does NOT stringify these,
        which is asserted rather than assumed."""
        cases = {
            "function type-parameter bound": (
                "from . import wire\n"
                "def f[T: wire.parse_request('')]():\n    pass\n",
                {("m", "<annotation>")}),
            "async function type-parameter bound": (
                "from . import wire\n"
                "async def f[T: wire.parse_request('')]():\n    pass\n",
                {("m", "<annotation>")}),
            "class type-parameter bound": (
                "from . import wire\n"
                "class C[T: wire.parse_request('')]:\n    pass\n",
                {("m", "<annotation>")}),
            "type-parameter constraints tuple": (
                "from . import wire\n"
                "def f[T: (int, wire.parse_request(''))]():\n    pass\n",
                {("m", "<annotation>")}),
            "type alias value": (
                "from . import wire\n"
                "type Alias = wire.parse_request('')\n",
                {("m", "<annotation>")}),
            "type alias own type-parameter bound": (
                "from . import wire\n"
                "type Alias[T: wire.parse_request('')] = int\n",
                {("m", "<annotation>")}),
            "nested function type-parameter bound": (
                "from . import wire\n"
                "def outer():\n"
                "    def inner[T: wire.parse_request('')]():\n"
                "        pass\n"
                "    return inner\n",
                {("m", "<annotation>")}),
            "future import does not defer PEP 695 scopes": (
                "from __future__ import annotations\n"
                "from . import wire\n"
                "type Alias = wire.parse_request('')\n",
                {("m", "<annotation>")}),
            "control: no parser in a type parameter": (
                "from . import wire\n"
                "def f[T: wire.render_request('')]():\n    pass\n",
                set()),
            "control: legacy annotation still stringifies": (
                "from __future__ import annotations\n"
                "from . import wire\n"
                "def f(t: wire.parse_request('')):\n    return t\n",
                set()),
        }
        for label, (source, expected) in cases.items():
            with self.subTest(form=label):
                self.assertEqual(stamped_parse_sites("m", source), expected)

    def test_lazy_type_parameter_defaults(self):
        for label, source in {
            "TypeVar default": "def f[T = wire.parse_request('')]():\n"
                               "    pass\n",
            "ParamSpec default":
                "def f[**P = [wire.parse_request('')]]():\n    pass\n",
            "TypeVarTuple default":
                "def f[*Ts = *(wire.parse_request(''),)]():\n    pass\n",
        }.items():
            with self.subTest(form=label):
                self.assertEqual(
                    stamped_parse_sites("m", "from . import wire\n" + source),
                    {("m", "<annotation>")})

    def test_the_runtime_really_defers_those_scopes(self):
        """The live control behind the attribution: these expressions do
        not run at the definition statement, only on attribute access —
        which is WHY they may not be credited to the defining scope. The
        scanner is a structural walk; this is the fact it models."""
        for label, source, touch in (
            ("function type-parameter bound",
             "def f[T: probe()](): pass", lambda ns: ns["f"].__type_params__[0].__bound__),
            ("class type-parameter bound",
             "class C[T: probe()]: pass", lambda ns: ns["C"].__type_params__[0].__bound__),
            ("type alias value",
             "type Alias = probe()", lambda ns: ns["Alias"].__value__),
        ):
            with self.subTest(form=label):
                ran = []
                ns = {"probe": lambda: ran.append(1) or int}
                exec(compile(source, "<probe>", "exec"), ns)
                self.assertEqual(ran, [], f"{label} ran at definition")
                touch(ns)
                self.assertEqual(ran, [1], f"{label} never ran on access")

    def test_the_scanner_call_domain_on_synthetic_modules(self):
        """The scanner's admitted call domain, each form probed through THE
        scanner itself — `util.stamped_parse_sites`, the same function the
        production walk and the workbench walk use — never a re-derivation
        of its logic beside it (lineage 8; the workbench copy had silently
        lost the alias arm, which is what re-derivation costs).

        One synthetic module per admitted form, with the paired control.
        Mutations, each verified by hand against the factored scanner:
        drop the alias map and the alias case fails; drop the async visitor
        and the async case fails; drop the `<module>` fallback and the
        import-time case fails.
        """
        cases = {
            "bare call": ("from .wire import parse_request\n"
                          "def f(t):\n    return parse_request(t)\n",
                          {("m", "f")}),
            "attribute call": ("from . import wire\n"
                               "def g(t):\n    return wire.parse_request(t)\n",
                               {("m", "g")}),
            "aliased import": ("from .wire import parse_request as _pr\n"
                               "def sneaky(t):\n    return _pr(t)\n",
                               {("m", "sneaky")}),
            "async body": ("from . import wire\n"
                           "async def h(t):\n"
                           "    return wire.parse_request(t)\n",
                           {("m", "h")}),
            "innermost function wins": (
                "from . import wire\n"
                "def outer(t):\n"
                "    def inner(u):\n"
                "        return wire.parse_request(u)\n"
                "    return inner(t)\n",
                {("m", "inner")}),
            "import-time call is <module>": (
                "from . import wire\nX = wire.parse_request('')\n",
                {("m", "<module>")}),
            # Round-1 F2: definition-time expressions run in the ENCLOSING
            # scope when the def statement executes, never in the body.
            "top-level decorator is <module>": (
                "from . import wire\n"
                "@wire.parse_request('')\n"
                "def f(t):\n    return t\n",
                {("m", "<module>")}),
            "top-level positional default is <module>": (
                "from . import wire\n"
                "def f(x=wire.parse_request('')):\n    return x\n",
                {("m", "<module>")}),
            "top-level keyword-only default is <module>": (
                "from . import wire\n"
                "def f(*, x=wire.parse_request('')):\n    return x\n",
                {("m", "<module>")}),
            "async default is <module>": (
                "from . import wire\n"
                "async def f(x=wire.parse_request('')):\n    return x\n",
                {("m", "<module>")}),
            "nested default is the enclosing function": (
                "from . import wire\n"
                "def outer():\n"
                "    def inner(x=wire.parse_request('')):\n"
                "        return x\n"
                "    return inner\n",
                {("m", "outer")}),
            "nested decorator is the enclosing function": (
                "from . import wire\n"
                "def outer():\n"
                "    @wire.parse_request('')\n"
                "    def inner():\n        pass\n"
                "    return inner\n",
                {("m", "outer")}),
            # Round-2 F1: an annotation is its own seam on every module
            # that does not stringify it — the supported runtimes span
            # PEP 649: evaluated only when __annotations__ is read, so
            # the defining scope is not the scope that runs them.
            "return annotation is its own seam": (
                "from . import wire\n"
                "def f(t) -> wire.parse_request(''):\n    return t\n",
                {("m", "<annotation>")}),
            "parameter annotation is its own seam": (
                "from . import wire\n"
                "def f(t: wire.parse_request('')):\n    return t\n",
                {("m", "<annotation>")}),
            "stringified annotations are never evaluated": (
                "from __future__ import annotations\n"
                "from . import wire\n"
                "def f(t: wire.parse_request('')) "
                "-> wire.parse_request(''):\n    return t\n",
                set()),
            # Round-2 F1: a generator's body is deferred until a consumer
            # iterates; only the outermost iterable runs at creation.
            "generator element is deferred": (
                "from . import wire\n"
                "def f(xs):\n"
                "    return (wire.parse_request(x) for x in xs)\n",
                {("m", "<genexpr>")}),
            "generator filter is deferred": (
                "from . import wire\n"
                "def f(xs):\n"
                "    return (x for x in xs if wire.parse_request(x))\n",
                {("m", "<genexpr>")}),
            "generator inner iterable is deferred": (
                "from . import wire\n"
                "def f(xs):\n"
                "    return (x for a in xs "
                "for x in wire.parse_request(a))\n",
                {("m", "<genexpr>")}),
            "generator outermost iterable is eager": (
                "from . import wire\n"
                "def f():\n"
                "    return (x for x in wire.parse_request(''))\n",
                {("m", "f")}),
            "module-level generator element is deferred": (
                "from . import wire\n"
                "G = (wire.parse_request(x) for x in [])\n",
                {("m", "<genexpr>")}),
            "async generator element is deferred": (
                "from . import wire\n"
                "async def f(xs):\n"
                "    return (wire.parse_request(x) async for x in xs)\n",
                {("m", "<genexpr>")}),
            # Paired eager controls: comprehensions run when created.
            "list comprehension is the creating scope": (
                "from . import wire\n"
                "def f(xs):\n"
                "    return [wire.parse_request(x) for x in xs]\n",
                {("m", "f")}),
            "set comprehension is the creating scope": (
                "from . import wire\n"
                "def f(xs):\n"
                "    return {wire.parse_request(x) for x in xs}\n",
                {("m", "f")}),
            "dict comprehension is the creating scope": (
                "from . import wire\n"
                "def f(xs):\n"
                "    return {x: wire.parse_request(x) for x in xs}\n",
                {("m", "f")}),
            "lambda body is its own scope": (
                "from . import wire\n"
                "F = lambda t: wire.parse_request(t)\n",
                {("m", "<lambda>")}),
            "lambda default is the enclosing scope": (
                "from . import wire\n"
                "F = lambda t=wire.parse_request(''): t\n",
                {("m", "<module>")}),
            "class body is import-time code": (
                "from . import wire\n"
                "class C:\n    X = wire.parse_request('')\n",
                {("m", "<module>")}),
            "method default is import-time code": (
                "from . import wire\n"
                "class C:\n"
                "    def m(self, x=wire.parse_request('')):\n"
                "        return x\n",
                {("m", "<module>")}),
            # The stated over-approximation, pinned so a change is noticed:
            # no supported runtime evaluates a function-local variable
            # annotation, but it reports under the annotation seam rather
            # than vanishing.
            "function-local variable annotation is the annotation seam": (
                "from . import wire\n"
                "def f(t):\n"
                "    x: wire.parse_request('') = t\n"
                "    return x\n",
                {("m", "<annotation>")}),
            "control: not a parse call": (
                "from . import wire\n"
                "def f(t):\n    return wire.render_request(t)\n",
                set()),
            "control: non-parser decorator and default": (
                "from . import wire\n"
                "@wire.render_request('')\n"
                "def f(x=wire.render_request('')):\n    return x\n",
                set()),
        }
        for label, (source, expected) in cases.items():
            with self.subTest(form=label):
                self.assertEqual(stamped_parse_sites("m", source), expected)

    def test_every_attributed_seam_is_a_declared_reader(self):
        """The two halves of the authority agree: every seam a parse site
        is attributed to must be classified in `STAMPED_READERS`, and every
        classified seam must have at least one parse site serving it."""
        attributed = {seam for seams in vocab.STAMPED_PARSE_SITES.values()
                      for seam in seams}
        self.assertEqual(sorted(attributed - set(vocab.STAMPED_READERS)), [],
                         "a parse site is attributed to a seam nothing "
                         "classifies")
        self.assertEqual(sorted(set(vocab.STAMPED_READERS) - attributed), [],
                         "a classified seam has no parse site: it either "
                         "does not exist or reads by some other door")

    def test_the_warm_handoff_cache_compares_before_it_renders(self):
        """FALSIFICATION for round-3 F1's behavioural half, at the seam the
        reviewer found: a retained request whose stamp differs. Mutation:
        delete the comparison from the cached branch of `cmd_handoff` and
        this fails while the cache still returns warm — which is the state
        it was found in."""
        from review.tests._transport_fixtures import request_text
        for stamp, expected in self.EXPECTED.items():
            with self.subTest(stamp=stamp):
                text = self.synth.emitted_request()
                found = re.search(r'tool="([0-9a-f]*)"', text)
                if stamp == "differs":
                    text = text.replace(found.group(0),
                                        'tool="0000000000000000"')
                elif stamp == "unstamped":
                    text = text.replace(" " + found.group(0), "")
                agreement = transport.tool_agreement(wire.parse_request(text))
                self.assertEqual(agreement["agreement"], expected)
        # And the branch that serves the cache carries it into the result.
        source = (REPO_ROOT / "review" / "cli.py").read_text(encoding="utf-8")
        handoff = source[source.index("def cmd_handoff"):]
        handoff = handoff[:handoff.index("\ndef ")]
        cached = handoff[handoff.index("if cached is not None:"):]
        cached = cached[:cached.index("return EXIT_OK")]
        for required in ("tool_agreement", 'rec["tool"]',
                         "render_tool_agreement", '"event": "ingest"'):
            self.assertIn(required, cached,
                          f"the warm-cache branch does not {required}")

    def test_every_declared_cross_seam_has_a_case_here(self):
        """The authority binds the matrix. This covered set is still
        hand-written — a test case cannot be generated — but it can no
        longer hide an omitted READER, because the authority it is checked
        against is now derived from the production source above."""
        covered = {("take", "request"),        # transport.take, tested above
                   ("brief", "request"),
                   ("validate", "request"),
                   ("validate", "disposition"),
                   ("ledger add", "request"),
                   ("ledger add", "disposition"),
                   ("handoff cache", "request")}
        declared = {seam for seam, cls in vocab.STAMPED_READERS.items()
                    if cls == "cross"}
        self.assertEqual(
            declared - covered, set(),
            "a declared cross-installation reader has no case in this "
            "matrix")
        self.assertEqual(
            covered - declared, set(),
            "this matrix tests a seam the authority does not declare")

    def test_the_same_process_seam_deliberately_does_not_compare(self):
        """`respond --out` writes the disposition and records it in one
        run, so a comparison there could only ever say `match`. The
        absence is a decision and is asserted, not left to be read as an
        omission like the disposition stamp was in round 1."""
        self.assertEqual(vocab.STAMPED_READERS[("respond", "disposition")],
                         "same_process")
        source = (REPO_ROOT / "review" / "transport.py").read_text(
            encoding="utf-8")
        record = source[source.index("def record_response"):]
        record = record[:record.index("\n\n\n")]
        self.assertNotIn("tool_agreement", record)

    def test_the_authority_classifies_every_seam_it_names(self):
        for seam, cls in vocab.STAMPED_READERS.items():
            with self.subTest(seam=seam):
                self.assertIn(cls, vocab.SEAM_CLASSES)
                self.assertIn(seam[1], vocab.STAMPED_KINDS,
                              "a reader is declared for an envelope kind "
                              "nothing stamps")


class TestTheHumanSeesTheReport(unittest.TestCase):
    """Round-1 F4: report-not-refuse rests on the human SEEING the report.
    The agreement was returned in the JSON payload and rendered nowhere on
    the interactive path, so the documented command showed nothing — not
    even when the tool knew the installations differed. A decision cannot
    rest on a fact the visible command never names.

    The matrix is closed over all three states and both output modes.
    """

    STATES = {
        "match": {"agreement": "match", "reader": "aaaabbbbccccdddd",
                  "writer": "aaaabbbbccccdddd"},
        "differs": {"agreement": "differs", "reader": "aaaabbbbccccdddd",
                    "writer": "1111222233334444",
                    "note": "the envelope was written by a DIFFERENT "
                            "installation of this tool"},
        "unstamped": {"agreement": "unstamped", "reader": "aaaabbbbccccdddd",
                      "writer": None,
                      "note": "the envelope was written by an installation "
                              "that predates tool identity"},
    }

    def test_the_text_rendering_names_every_state_and_its_identities(self):
        """FALSIFICATION. Mutation: delete the `render_tool_agreement(...)`
        call from the interactive `take` rendering — the state this round
        was handed back in — and all three cases below fail, while the JSON
        controls keep passing, which is exactly the asymmetry that hid it."""
        for name, agreement in self.STATES.items():
            with self.subTest(state=name):
                rendered = cli.render_tool_agreement(agreement)
                self.assertIn(agreement["reader"], rendered,
                              "the reading installation is not named")
                if name == "match":
                    self.assertIn("match", rendered)
                else:
                    self.assertIn(name.upper(), rendered,
                                  "the state is not named in a form a "
                                  "human reading past it would notice")
                    self.assertIn(agreement["note"], rendered,
                                  "the reason is not carried")
                if agreement["writer"] and name != "match":
                    self.assertIn(agreement["writer"], rendered,
                                  "the writing installation is not named")

    def test_the_interactive_take_rendering_carries_it(self):
        """The wiring, not just the renderer: the `take` text handed to a
        TTY must contain what the renderer produces."""
        source = (REPO_ROOT / "review" / "cli.py").read_text(encoding="utf-8")
        take = source[source.index("def cmd_take"):]
        take = take[:take.index("\ndef ")]
        self.assertIn("render_tool_agreement(rec['tool'])", take,
                      "the interactive take rendering does not include the "
                      "agreement; round-1 F4 is back")

    def test_match_is_printed_too_not_only_disagreement(self):
        """A line that appears only on disagreement teaches the reader
        nothing about what its absence means — and this round's own record
        is that an absence gets read as agreement."""
        rendered = cli.render_tool_agreement(self.STATES["match"])
        self.assertTrue(rendered.strip(),
                        "agreement renders nothing, so silence has two "
                        "meanings again")

    def test_the_structured_result_carries_it_in_every_state(self):
        """The JSON control beside the text: both modes, all three states,
        so a repair to one cannot be mistaken for a repair to both."""
        for name, agreement in self.STATES.items():
            with self.subTest(state=name):
                self.assertEqual(agreement["agreement"], name)
                self.assertIn("tool", cli.PROSE_KEYS)
                for key in ("agreement", "reader", "writer"):
                    self.assertIn(key, cli.PROSE_KEYS,
                                  f"{key} is returned but undeclared at the "
                                  f"egress inventory")


class TestTheRecordSaysWhichInstallationRuled(unittest.TestCase):
    """The forensic half. The 2026-08-23 skew was reconstructed from an
    absence; the ledger states it now."""

    def test_the_take_event_carries_both_identities_and_the_verdict(self):
        from review.tests._transport_fixtures import request_text
        parsed = wire.parse_request(request_text())
        agreement = transport.tool_agreement(parsed)
        # The synthetic request predates the stamp, which is the honest
        # fixture for "an envelope an older installation wrote".
        self.assertEqual(agreement["agreement"], "unstamped")
        event = {"event": "take", "tool": agreement["reader"],
                 "tool_agreement": agreement["agreement"]}
        if agreement["writer"]:
            event["tool_writer"] = agreement["writer"]
        ledger = Ledger.in_memory()
        ledger.add(event)
        stored = [e for e in ledger.events() if e["event"] == "take"][0]
        self.assertEqual(stored["tool"], tool_identity())
        self.assertEqual(stored["tool_agreement"], "unstamped")
        self.assertNotIn("tool_writer", stored)


def _cli_add(cfg, tmp, path):
    """`ledger add <path>`, returning (exit code, parsed payload)."""
    import argparse
    import contextlib
    import io
    args = argparse.Namespace(envelope=str(path), round=None, tokens=None,
                              ledger_dir=str(tmp))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.cmd_ledger_add(args, cfg)
    out = buf.getvalue()
    try:
        return code, json.loads(out)
    except json.JSONDecodeError:
        return code, out


if __name__ == "__main__":
    unittest.main()

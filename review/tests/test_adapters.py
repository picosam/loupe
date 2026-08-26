"""§6.3 / RVW-T10: the per-agent adapters render from ONE source and cannot
drift from the CLI's verb surface; the tracked copies are byte-guarded.

FALSIFICATIONS: a verb the CLI has and the adapter omits (or the reverse)
fails; two adapters with different bodies fail; a tracked adapter that
differs from the render fails `--check`. Everything here is read-only except
one temp-dir round trip that self-skips where writes are denied. The
install tests never touch a real agent directory: every target is a temp
path passed in explicitly, and the CLI test patches `install_targets`.
"""
import contextlib
import json
import io
import re
import tempfile
import unittest
from pathlib import Path

from review import TOOL_NAME, TOOL_VERSION, adapters, cli, config, vocab
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

    def test_the_trigger_covers_zero_footprint_repositories(self):
        # The trigger named only an in-tree review.toml, and a zero-footprint
        # onboarding (user-level config, §4) declares nothing in-tree — so
        # the one configuration the ownership model exists to protect was
        # the one the skill would not fire on (found by the first pilot).
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

    def test_transport_verbs_are_the_procedure(self):
        text = adapters.render("claude-skill")
        for verb in ("handoff", "take", "close", "respond", "validate"):
            self.assertIn(f"`{TOOL_NAME} {verb}", text)


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
        from review.tests.test_transport import request_text
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
    }

    #: Authorities no shipped document enumerates. Kept explicit, and kept
    #: HONEST by `test_no_excluded_authority_is_quietly_enumerated` below —
    #: round-3 F3 was this list containing four vocabularies the
    #: specification spells out in full.
    UNDOCUMENTED = {
        "BLOCKING_ILLEGAL", "FALSIFICATION_KINDS", "FALSIFICATION_RECORD",
        "LINEAGE_KINDS", "LINEAGE_MERGING", "CLAIM_STRING_FIELDS",
        "CLAIM_LIST_FIELDS", "CLAIM_REQUIRED", "CLAIM_NONEMPTY",
        "CLAIM_REFERENCE_REQUIRED", "SEAM_CLASSES", "STAMPED_PARSE_CALLS",
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
        """`request → verdict → disposition`: the loop's three envelopes,
        of which two are stamped. `verdict` is named there for the same
        reason it has a row in the specification's table — it is the third
        envelope and the unstamped one — so the reader drops it rather than
        the sentence being reworded to suit a test."""
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
    def _members(self, text, pattern, reader):
        found = re.search(pattern, text, re.S)
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
                    re.search(pattern, text, re.S),
                    f"{name}: the locator matches nothing in {document}")

    def test_the_stated_count_matches_the_mechanisms_it_counts(self):
        """§3.3's count, derived from the headings it counts. Kept from
        round 1: a count is a restatement whose authority is the document's
        own structure rather than a vocabulary."""
        section = self._section()
        stated = re.search(r"^(\w+) mechanisms, all deterministic", section,
                           re.M)
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
        real_read = Path.read_text

        def truncating(self_path, *a, **k):
            if Path(self_path).parent == self.keep:
                return "truncated"
            return real_read(self_path, *a, **k)

        with unittest.mock.patch.object(Path, "read_text", truncating):
            rows = {r["kind"]: r for r in adapters.install_all(
                self.rendered, targets={kind: target}, keep_dir=self.keep)}
        self.assertEqual(rows[kind]["status"], "failed", rows)
        self.assertEqual(target.read_text(encoding="utf-8"), edited,
                         "an unproven copy must not license the overwrite")

        # And a read-back that cannot decode at all is the same refusal, not
        # an escaping ValueError: the kept copy is proof only if reading it
        # back is a question with an answer (round-10 F1, second handler).
        def undecodable(self_path, *a, **k):
            if Path(self_path).parent == self.keep:
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1,
                                         "invalid start byte")
            return real_read(self_path, *a, **k)

        for stray in self.keep.iterdir():
            stray.unlink()
        with unittest.mock.patch.object(Path, "read_text", undecodable):
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
        "CLOSURES": "the procedure tells the reviewer WHERE closures are "
                    "answered, not the closure vocabulary",
        "FALSIFICATION_KINDS": "not stated anywhere in the procedure",
        "FINDING_FIELDS": "the procedure says 'every required field', "
                          "never the field list; the design document's "
                          "enumeration is registered in RESTATEMENTS",
        "LINEAGE_KINDS": "fingerprint-lineage grammar; not in the "
                         "procedure",
        "LINEAGE_MERGING": "fingerprint-lineage grammar; not in the "
                           "procedure",
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

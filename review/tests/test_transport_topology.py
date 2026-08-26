"""RVW-T11: the declared topology, and everything that reads it.

The item this closes was raised 2026-08-16 and stood open for a week under a
single sentence: the loop assumes both agents read the same disk. Three legs
were named. Two had mechanisms and no way to know when to use them — `take -`
and `close --verdict -` have always read stdin — and the third, the reachable
target, had a stamp that made the problem visible and nothing that refused it.

What was missing was never a carrier. It was the FACT: nothing in the loop
recorded whether the two ends shared a filesystem, so every printed command
had to be right for both cases at once, and a caveat that is protection in a
topology nobody has run is noise in the one everybody runs.

The fact is now declared, stamped, recorded and read. These tests cover the
whole path — resolution, the contradiction it makes refusable, the stamp, the
cache, both relays, the reviewer's correction — and, at each end, the thing
the design refuses to do: infer it.
"""
import dataclasses
import unittest

from review import brief, config, emit, transport, vocab, wire
from review.validate import validate_request
from review.ledger import Ledger
from review.tests.test_transport import (SHA_A, SHA_B, fake_git, request_text,
                                         TestTake)
from review.tests.util import REPO_ROOT

CFG = config.load(REPO_ROOT)


class TestTheVocabularyIsClosed(unittest.TestCase):

    def test_two_states_and_the_default_is_one_of_them(self):
        self.assertEqual(vocab.TRANSPORTS,
                         (vocab.TRANSPORT_PATH, vocab.TRANSPORT_PASTE))
        self.assertIn(vocab.TRANSPORT_DEFAULT, vocab.TRANSPORTS)

    def test_the_default_is_stated_once(self):
        """Round 3 F2: config carries NO transport default at all.

        A silent default written into config would erase the difference
        between a repo that declared `path` and one that declared nothing,
        and the undeclared state is where the environment's own
        declaration gets its say — so "undeclared" must stay observable at
        the emission reader. The emission default itself is `path`, the
        workflow's steady case (round 3 F2 restored one coherent policy:
        the cross-machine case is covered by an environment-bound `paste`
        declaration, not by taxing every local round). Both defaults are
        vocab's, restated nowhere.
        """
        self.assertNotIn("transport", config.DEFAULTS["roles"])
        self.assertIs(vocab.TRANSPORT_EMISSION_DEFAULT,
                      vocab.TRANSPORT_PATH)
        self.assertIn(vocab.TRANSPORT_EMISSION_DEFAULT, vocab.TRANSPORTS)

    def test_the_environment_declarations_are_closed_and_exact(self):
        # The Loupe-specific declaration is derived from the tool name,
        # and the provider matrix admits exact (variable, value) pairs
        # entailing a member of the closed vocabulary — nothing here is an
        # open-ended inference hook.
        self.assertEqual(vocab.TRANSPORT_ENV, "LOUPE_TRANSPORT")
        self.assertEqual(
            vocab.TRANSPORT_PROVIDER_SIGNALS,
            (("CLAUDE_CODE_REMOTE", "true", vocab.TRANSPORT_PASTE),))
        for _var, _value, entailed in vocab.TRANSPORT_PROVIDER_SIGNALS:
            self.assertIn(entailed, vocab.TRANSPORTS)


class TestResolution(unittest.TestCase):
    """§4's precedence, applied to the topology: config declares the steady
    case, a flag names the exception, and neither may invent a value."""

    def _cfg(self, **roles):
        return dataclasses.replace(CFG, roles={**CFG.roles, **roles})

    def _undeclared(self):
        return dataclasses.replace(
            CFG, roles={k: v for k, v in CFG.roles.items()
                        if k != "transport"})

    def test_silence_resolves_to_the_config(self):
        self.assertEqual(emit.resolve_transport(self._cfg(transport="paste")),
                         "paste")

    def test_silence_with_no_config_resolves_to_the_emission_default(self):
        # Round 3 F2: nobody declared the topology anywhere — no flag, no
        # config, an empty environment — so the emission resolves to the
        # workflow's steady case, `path`.
        self.assertEqual(emit.resolve_transport(self._undeclared(), env={}),
                         vocab.TRANSPORT_EMISSION_DEFAULT)

    # ---------------------------------------- the environment declaration

    def test_the_cloud_environments_declaration_wins_over_the_default(self):
        # The cloud-declared control: a sandbox's configuration sets
        # LOUPE_TRANSPORT=paste, and the undeclared steady case yields.
        for declared in vocab.TRANSPORTS:
            self.assertEqual(
                emit.resolve_transport(
                    self._undeclared(),
                    env={vocab.TRANSPORT_ENV: declared}),
                declared)

    def test_removing_the_environment_declaration_restores_the_default(self):
        # The mutation that proves the selector is live in BOTH
        # directions: declaration present -> paste; removed -> path.
        env = {vocab.TRANSPORT_ENV: "paste"}
        self.assertEqual(emit.resolve_transport(self._undeclared(), env=env),
                         "paste")
        env.pop(vocab.TRANSPORT_ENV)
        self.assertEqual(emit.resolve_transport(self._undeclared(), env=env),
                         vocab.TRANSPORT_PATH)

    def test_config_and_flag_outrank_the_environment(self):
        # A human declaration always beats an environment one: repo config
        # over env, and the explicit flag over both.
        env = {vocab.TRANSPORT_ENV: "paste"}
        self.assertEqual(
            emit.resolve_transport(self._cfg(transport="path"), env=env),
            "path")
        self.assertEqual(
            emit.resolve_transport(self._undeclared(), "path", env=env),
            "path")

    def test_an_environment_declaration_outside_the_vocabulary_is_refused(self):
        # A forged or mistyped declaration names no topology: refused by
        # value — and the explicitly EMPTY variable is a distinct refusal,
        # never folded to the default (the claim-boundary rule).
        for value in ("stdin", "PATH", ""):
            with self.subTest(value=repr(value)):
                with self.assertRaises(emit.TransportSelectionError) as c:
                    emit.resolve_transport(
                        self._undeclared(),
                        env={vocab.TRANSPORT_ENV: value})
                self.assertIn(vocab.TRANSPORT_ENV, str(c.exception))

    # ------------------------------------------- the provider signal matrix

    def test_the_documented_provider_signal_entails_paste(self):
        # Claude Code cloud stamps CLAUDE_CODE_REMOTE=true; under the
        # stated assumption that the other endpoint is the operator's
        # local machine, the emission is paste.
        self.assertEqual(
            emit.resolve_transport(self._undeclared(),
                                   env={"CLAUDE_CODE_REMOTE": "true"}),
            vocab.TRANSPORT_PASTE)

    def test_only_the_exact_documented_value_is_a_signal(self):
        # Half-matching an env var is inferring, which §5.2 rejects: any
        # other value of the variable is NOT a signal and the steady case
        # holds.
        for value in ("false", "1", "True", "TRUE", ""):
            with self.subTest(value=repr(value)):
                self.assertEqual(
                    emit.resolve_transport(
                        self._undeclared(),
                        env={"CLAUDE_CODE_REMOTE": value}),
                    vocab.TRANSPORT_PATH)

    def test_the_loupe_declaration_outranks_the_provider_signal(self):
        # The Loupe-specific declaration is the environment's own word;
        # the signal is an inference and ranks below it in both
        # directions.
        self.assertEqual(
            emit.resolve_transport(
                self._undeclared(),
                env={vocab.TRANSPORT_ENV: "path",
                     "CLAUDE_CODE_REMOTE": "true"}),
            "path")

    def test_a_signal_resolved_paste_still_hits_the_local_only_refusal(self):
        # `--local-only` beside an environment-resolved `paste` is the
        # same contradiction as beside the flag: resolution does not
        # silently reconcile it, and ensure_pushed refuses it by name.
        resolved = emit.resolve_transport(
            self._undeclared(), local_only=True,
            env={"CLAUDE_CODE_REMOTE": "true"})
        self.assertEqual(resolved, vocab.TRANSPORT_PASTE)
        with self.assertRaises(RuntimeError) as caught:
            emit.ensure_pushed(CFG, local_only=True,
                               git=fake_git({}), transport=resolved)
        self.assertIn("cannot both be true", str(caught.exception))

    def test_local_only_silence_entails_path(self):
        # `--local-only` is a human declaration whose topology is entailed:
        # a target fetchable from no remote is reviewable only on this
        # filesystem. Entailment from the human's flag, not inference.
        cfg = self._undeclared()
        self.assertEqual(emit.resolve_transport(cfg, local_only=True,
                                                env={}),
                         vocab.TRANSPORT_PATH)
        # An explicit flag still wins over the entailment (and the
        # contradiction between paste and --local-only is refused at the
        # emitter's own check, not silently reconciled here).
        self.assertEqual(
            emit.resolve_transport(cfg, "paste", local_only=True), "paste")

    def test_the_flag_selects_over_the_config_in_both_directions(self):
        self.assertEqual(
            emit.resolve_transport(self._cfg(transport="path"), "paste"),
            "paste")
        self.assertEqual(
            emit.resolve_transport(self._cfg(transport="paste"), "path"),
            "path")

    def test_an_unknown_value_is_refused_whichever_source_states_it(self):
        """Not defaulted, not passed through. An unrecognised transport would
        be stamped on the envelope and appended to an append-only ledger as
        if it named something."""
        for kwargs, needle in ((dict(transport="stdin"), "governing config"),
                               (dict(), "--transport")):
            with self.subTest(source=needle):
                with self.assertRaises(emit.TransportSelectionError) as caught:
                    emit.resolve_transport(
                        self._cfg(**kwargs),
                        "stdin" if not kwargs else None)
                self.assertIn(needle, str(caught.exception))
                self.assertIn("path", caught.exception.remedy)
                self.assertIn("paste", caught.exception.remedy)


class TestTheContradictionIsRefused(unittest.TestCase):
    """RVW-T11 leg 2, which had a stamp and no refusal.

    `--local-only` declares a target fetchable from nowhere; `paste` declares
    a reviewer who cannot reach this filesystem. Both true means an envelope
    binding a SHA its own reviewer can never obtain — discovered at `take`,
    after a human has carried it.
    """

    def _git(self, remotes=""):
        return fake_git({
            ("rev-parse", "--abbrev-ref", "HEAD"): "main",
            ("status", "--porcelain"): "",
            ("rev-parse", "HEAD"): SHA_B,
            ("remote",): remotes,
        })

    def test_local_only_and_paste_cannot_both_hold(self):
        with self.assertRaises(RuntimeError) as caught:
            emit.ensure_pushed(CFG, local_only=True, git=self._git(),
                               transport="paste")
        self.assertIn("cannot both be true", str(caught.exception))

    def test_it_refuses_before_the_commit(self):
        """A contradiction between two declarations needs no git state to be
        false, so nothing is committed, pushed or run before it is named."""
        run = self._git()
        with self.assertRaises(RuntimeError):
            emit.ensure_pushed(CFG, local_only=True, git=run,
                               transport="paste")
        self.assertEqual(run.calls, [])

    def test_local_only_with_the_default_topology_is_untouched(self):
        record = emit.ensure_pushed(CFG, local_only=True, git=self._git(),
                                    transport="path")
        self.assertEqual(record["state"], "local-only")

    def test_an_unstated_transport_does_not_refuse_local_only(self):
        """Every caller that predates the parameter — and the emit path when
        a repo declares nothing — still reaches the same answer it did."""
        record = emit.ensure_pushed(CFG, local_only=True, git=self._git())
        self.assertEqual(record["state"], "local-only")


class TestTheStamp(unittest.TestCase):

    def test_absence_reads_as_the_default_not_as_unknown(self):
        """Every envelope emitted before the attribute existed came from a
        loop that had only one topology. Reading its silence as unknown would
        make the relay unrenderable for exactly those rounds."""
        self.assertEqual(
            transport.declared_transport(wire.parse_request(request_text())),
            vocab.TRANSPORT_DEFAULT)

    def test_a_declared_value_is_read_back(self):
        for declared in vocab.TRANSPORTS:
            parsed = wire.parse_request(request_text(transport_attr=declared))
            self.assertEqual(transport.declared_transport(parsed), declared)

    def test_an_unrecognised_stamp_is_refused_by_the_reader_itself(self):
        """R1-F2: neither quietly repaired NOR returned for someone else to
        judge. The old contract returned it as-is on the theory that
        R-TRANSPORT refuses it first — but `brief` read the stamp without
        validating, so a value the grammar refuses selected a live carrier
        anyway. The reader is the boundary now.
        """
        parsed = wire.parse_request(request_text(transport_attr="carrier"))
        with self.assertRaises(vocab.TransportDeclarationError) as caught:
            transport.declared_transport(parsed)
        self.assertIn("'carrier'", str(caught.exception))


class TestTheGrammarRefusesAnythingElse(unittest.TestCase):
    """R-TRANSPORT. The attribute is OPTIONAL — absence is the default, and
    every envelope emitted before it existed is silent — but a value outside
    the closed vocabulary is refused rather than defaulted.

    Without this the readers' tolerance became a hole: `declared_transport`
    returns an unrecognised value as written, and every comparison against it
    is `== paste`, so a typo fell through to `path` and the reviewer would be
    handed a kept path on the author's machine. Refusing at the grammar
    boundary is what lets every reader downstream assume two states.
    """

    def _items(self, text):
        return [i.code for i in validate_request(
            wire.parse_request(text), CFG, structural_only=True)]

    def test_the_two_declared_states_and_silence_all_pass(self):
        for attr in (None, "path", "paste"):
            self.assertNotIn("R-TRANSPORT", self._items(
                request_text(transport_attr=attr)), attr)

    def test_a_value_outside_the_vocabulary_is_refused_by_value(self):
        for attr in ("stdin", "PATH", "paste ", "carrier"):
            items = validate_request(
                wire.parse_request(request_text(transport_attr=attr)),
                CFG, structural_only=True)
            codes = [i.code for i in items]
            self.assertIn("R-TRANSPORT", codes, attr)
            named = [i for i in items if i.code == "R-TRANSPORT"][0]
            self.assertIn(repr(attr), named.message)

    def test_an_empty_declaration_is_not_an_omitted_one(self):
        """The same rule the claim boundary applies to `--claim-file ""`:
        stating a value emptily is a different act from stating none."""
        text = request_text(transport_attr="paste").replace(
            'transport="paste"', 'transport=""')
        self.assertIn("R-TRANSPORT", self._items(text))

    def test_it_is_target_independent_so_take_judges_it_before_git(self):
        """`structural_only` is the tier `take` runs before it chooses a
        fetch URL: this rule needs no configuration, so a defective stamp
        costs no git call."""
        self.assertIn("R-TRANSPORT", self._items(
            request_text(transport_attr="stdin")))


class TestTheRecord(unittest.TestCase):

    def test_the_request_event_records_what_the_envelope_declared(self):
        """Off the envelope, never off this machine's config: both ledgers
        must agree about the round even when the two machines do not."""
        for declared in vocab.TRANSPORTS:
            parsed = wire.parse_request(request_text(transport_attr=declared))
            event = transport.request_event(parsed, 1, "d", 10)
            self.assertEqual(event["transport"], declared)

    def test_recorded_transport_answers_from_either_side(self):
        for event in ("request", "take"):
            ledger = Ledger.in_memory()
            ledger.add({"event": event, "round": 1, "sha": SHA_B,
                        "transport": "paste"})
            self.assertEqual(transport.recorded_transport(ledger, SHA_B),
                             "paste")

    def test_a_sha_this_ledger_never_saw_is_the_default(self):
        self.assertEqual(
            transport.recorded_transport(Ledger.in_memory(), SHA_B),
            vocab.TRANSPORT_DEFAULT)

    def test_a_record_outside_the_vocabulary_is_refused_not_skipped(self):
        """R1-F2: the newest matching record DECIDES. The old loop scanned
        past an invalid record to whatever older one looked valid and fell
        to the default when none did — an invalid-record fallback selecting
        a carrier no record declared. Refusal keeps the defective record a
        fact a person has to look at."""
        ledger = Ledger.in_memory()
        ledger.add({"event": "take", "round": 1, "sha": SHA_B,
                    "transport": "carrier"})
        with self.assertRaises(vocab.TransportDeclarationError):
            transport.recorded_transport(ledger, SHA_B)

    def test_a_record_that_predates_the_attribute_is_the_default(self):
        # Legacy-absence compatibility: no transport key at all is a real
        # historical state, read as the default like an unstamped envelope.
        ledger = Ledger.in_memory()
        ledger.add({"event": "take", "round": 1, "sha": SHA_B})
        self.assertEqual(transport.recorded_transport(ledger, SHA_B),
                         vocab.TRANSPORT_DEFAULT)

    def test_the_newest_record_for_the_sha_wins(self):
        """The reviewer's correction is written after the request event the
        same `take` files, so a stale author declaration cannot outrank it."""
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": SHA_B,
                    "transport": "path"})
        ledger.add({"event": "take", "round": 1, "sha": SHA_B,
                    "transport": "paste"})
        self.assertEqual(transport.recorded_transport(ledger, SHA_B), "paste")


class TestTheReviewerMayCorrectIt(unittest.TestCase):
    """The author's declaration can be wrong in one direction that matters:
    an author who did not know the reviewer was elsewhere stamps `path`, and
    the verdict leg then prints a file on the REVIEWER's machine to an author
    who cannot open it — round 4's F2, arriving by mis-declaration.
    """

    # The reviewer clone's fixture, borrowed rather than inherited:
    # subclassing TestTake would re-run its whole suite under this name.
    _cfg = TestTake._cfg
    _git = TestTake._git

    def test_the_envelope_decides_when_the_reviewer_says_nothing(self):
        ledger = Ledger.in_memory()
        rec = transport.take(self._cfg(), ledger,
                             request_text(transport_attr="paste"), "r.md",
                             git=self._git(), reviewer="codex")
        self.assertEqual(rec["transport"], "paste")
        take = [e for e in ledger.events() if e["event"] == "take"][0]
        self.assertEqual(take["transport"], "paste")
        self.assertNotIn("declared_transport", take)

    def test_a_correction_is_recorded_beside_what_it_corrects(self):
        """Both values, because a disagreement about the channel is a fact
        worth keeping rather than one to overwrite."""
        ledger = Ledger.in_memory()
        rec = transport.take(self._cfg(), ledger, request_text(), "r.md",
                             git=self._git(), reviewer="codex",
                             transport="paste")
        self.assertEqual(rec["transport"], "paste")
        take = [e for e in ledger.events() if e["event"] == "take"][0]
        self.assertEqual(take["transport"], "paste")
        self.assertEqual(take["declared_transport"], vocab.TRANSPORT_DEFAULT)

    def test_stdin_is_not_read_as_evidence_of_a_paste(self):
        """The rejected inference, at the one place it looks most tempting.

        An agent may pipe a local file for reasons of its own, so the shape
        of the argument says nothing about where the other end is — and a
        carrier guessed from it fails open toward `path`, the answer that
        prints a pointer the far end cannot follow.
        """
        ledger = Ledger.in_memory()
        rec = transport.take(self._cfg(), ledger, request_text(), "-",
                             git=self._git(), reviewer="codex")
        self.assertEqual(rec["transport"], vocab.TRANSPORT_DEFAULT)


class TestTheCacheKeyIncludesIt(unittest.TestCase):
    """The same rule as the role stamp: a warm copy emitted under one
    topology must not answer an invocation that declared the other."""

    # Round 5 F1: an unrecorded claim state matches nothing, including
    # another unknown — so a fixture that recorded none would be cold for a
    # reason that has nothing to do with the transport under test.
    CLAIM = "claim-digest-fixture"

    def _ledger_and_cfg(self, tmp, declared):
        cfg = dataclasses.replace(CFG, ledger_dir=tmp)
        envelope = request_text(transport_attr=declared)
        (tmp / "exchange").mkdir(parents=True, exist_ok=True)
        path = transport.exchange_path(cfg, 1, "request")
        path.write_text(envelope, encoding="utf-8")
        ledger = Ledger(tmp)
        ledger.add(transport.request_event(
            wire.parse_request(envelope), 1,
            transport._digest_text(envelope), len(envelope),
            claim_digest=self.CLAIM))
        return cfg, ledger

    def _git(self):
        return fake_git({("rev-parse", "HEAD"): SHA_B,
                         ("status", "--porcelain"): ""})

    def _warm(self, declared, wanted):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            cfg, ledger = self._ledger_and_cfg(Path(tmp), declared)
            return transport.cached_handoff(cfg, ledger, 1, git=self._git(),
                                            claim_digest=self.CLAIM,
                                            roles=("claude", "codex"),
                                            transport=wanted) is not None

    def test_the_same_declaration_is_warm(self):
        for declared in vocab.TRANSPORTS:
            self.assertTrue(self._warm(declared, declared), declared)

    def test_a_different_declaration_is_cold_in_both_directions(self):
        """Cold costs one re-emission. Warm would hand the human the envelope
        that says `path` after they asked for the cross-machine round, and
        the reviewer's own relay would then print a path they cannot see."""
        self.assertFalse(self._warm("path", "paste"))
        self.assertFalse(self._warm("paste", "path"))

    def test_an_unstamped_kept_envelope_is_warm_for_the_default(self):
        """Legacy warm behaviour is unchanged: absence IS the default here,
        so a round emitted before the attribute existed still re-serves."""
        self.assertTrue(self._warm(None, vocab.TRANSPORT_DEFAULT))


class TestTheCommandsEachLegPrints(unittest.TestCase):

    def _parsed(self, declared=None):
        return wire.parse_request(request_text(transport_attr=declared))

    def test_the_reviewers_command_follows_the_declaration(self):
        self.assertEqual(
            str(transport._reviewer_next(self._parsed(), "/kept/r.md", "codex")),
            "loupe take /kept/r.md --as codex")
        self.assertEqual(
            str(transport._reviewer_next(self._parsed("paste"), "/kept/r.md",
                                         "codex")),
            "loupe take - --as codex")

    def test_unkept_bytes_leave_the_runnable_field_empty(self):
        # F1 (lineage 6 round 1): the not-kept form needs a stdin a person
        # must supply, so it is a Template — and a Template may not ride
        # `reviewer_next`, a field agents run verbatim. The command travels
        # as prose instead, placeholder and all.
        for declared in (None, "paste"):
            with self.subTest(declared=declared):
                parsed = self._parsed(declared)
                self.assertIsNone(transport._reviewer_next(
                    parsed, "not kept (…)", "codex"))
                note = transport._reviewer_note(parsed, "not kept (…)",
                                                "codex")
                self.assertIn("take - --as codex < <the envelope>", note)

    def test_kept_bytes_need_no_note(self):
        parsed = self._parsed()
        self.assertIsNone(transport._reviewer_note(parsed, "/kept/r.md",
                                                   "codex"))

    def test_a_declared_paste_round_needs_no_placeholder_redirect(self):
        """RVW-T16's named residue is the not-kept fallback alone. A declared
        paste round has real bytes at a real kept path, so the `< <the
        envelope>` a person would have to fill in does not arise."""
        rendered = str(transport._reviewer_next(self._parsed("paste"),
                                                "/kept/r.md", "codex"))
        self.assertNotIn("<", rendered)


class TestBothRelaysAgreeWithTheRecord(unittest.TestCase):
    """One declaration, two legs, and neither leg asks the reader to choose."""

    def _verdict(self):
        return wire.parse_verdict(
            f'<loupe-review-verdict sha="{SHA_B}">\n'
            f"VERDICT: changes requested\n\n## findings\n\nNone\n"
            f"\n## evidence checked\n\n- the tree\n")

    def test_the_request_leg_offers_exactly_one_carrier(self):
        for declared, present, absent in (
                ("path", "take /kept/r.md", "take -"),
                ("paste", "take -", "/kept/r.md")):
            with self.subTest(transport=declared):
                out = brief.relay("/kept/r.md",
                                  wire.parse_request(
                                      request_text(transport_attr=declared)),
                                  "bytes")
                self.assertIn(present, out)
                self.assertNotIn(absent, out)

    def test_the_verdict_leg_offers_exactly_one_carrier(self):
        for declared, present, absent in (
                ("path", "--verdict /tmp/v.md", "--verdict -"),
                ("paste", "--verdict -", "/tmp/v.md")):
            with self.subTest(transport=declared):
                out = brief.verdict_relay(self._verdict(), source="/tmp/v.md",
                                          transport=declared)
                self.assertIn(present, out)
                self.assertNotIn(absent, out)

    def test_the_verdict_bytes_ride_the_paste_relay_and_only_it(self):
        # Round 3, live: a verdict relayed as loose prose lost its markdown
        # headings to a chat renderer. On a paste round the bytes travel
        # fenced under the command that consumes them, exactly like the
        # request leg; on a path round the file is the carrier and no
        # bytes are printed.
        bytes_ = "<x-review-verdict>\n## findings\n</x-review-verdict>"
        pasted = brief.verdict_relay(self._verdict(), source="/tmp/v.md",
                                     transport="paste", envelope=bytes_)
        self.assertIn("The verdict, to paste:", pasted)
        self.assertIn(bytes_, pasted)
        pathed = brief.verdict_relay(self._verdict(), source="/tmp/v.md",
                                     transport="path", envelope=bytes_)
        self.assertNotIn("The verdict, to paste:", pathed)
        self.assertNotIn(bytes_, pathed)

    def test_only_the_paste_round_keeps_a_note(self):
        """The note survives exactly where it carries an INSTRUCTION: a paste
        round has to get the verdict bytes into the command. A path round's
        note restated the block's own heading and is gone (user,
        2026-08-26)."""
        for declared in vocab.TRANSPORTS:
            relay = brief.verdict_relay(self._verdict(), source="/tmp/v.md",
                                        transport=declared)
            if declared == vocab.TRANSPORT_PASTE:
                self.assertIn("paste the verdict into it", relay)
            else:
                self.assertNotIn("the author's to run", relay)

    def test_the_verdict_leg_defaults_when_nobody_says(self):
        self.assertIn("--verdict /tmp/v.md",
                      brief.verdict_relay(self._verdict(), source="/tmp/v.md"))


class TestTheLoopEndToEnd(unittest.TestCase):
    """A real repository, the real CLI, both declarations.

    The unit classes above prove each reader in isolation. This one is the
    part RVW-T15 named as missing evidence rather than a missing mechanism:
    the declaration travelling from a flag, through emission and the stamp,
    into the ledger, and out the other side as the command a person is told
    to run. It stops short of two machines — one filesystem is all a test
    host has — but every step that a second machine would exercise is here,
    and the topology is now a value the round records rather than a property
    of where the process happened to run.
    """

    def setUp(self):
        import os
        import shutil
        import tempfile
        from pathlib import Path
        from review.tests.test_worktree_and_brief import _sh, _git
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="topology-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.repo = self.tmp / "repo"
        _sh("git", "init", "-q", "-b", "main", str(self.repo))
        for k, v in (("user.name", "a"), ("user.email", "a@example.invalid"),
                     ("commit.gpgsign", "false")):
            _sh("git", "-C", str(self.repo), "config", k, v)
        toml = (REPO_ROOT / "review.toml").read_text(encoding="utf-8")
        toml = toml[:toml.index("[[gates]]")] + toml[toml.index("[roles]"):]
        (self.repo / "review.toml").write_text(toml, encoding="utf-8")
        (self.repo / "f.txt").write_text("one\n", encoding="utf-8")
        _sh("git", "-C", str(self.repo), "add", ".")
        _sh("git", "-C", str(self.repo), "commit", "-q", "-m", "init")
        self.base = _git(self.repo, "rev-parse", "HEAD")
        (self.repo / "f.txt").write_text("two\n", encoding="utf-8")
        _sh("git", "-C", str(self.repo), "commit", "-qam", "change")
        self.state = self.tmp / "state"
        self.claim = self.tmp / "claim.json"
        import json
        self.claim.write_text(json.dumps({
            "objective": "topology",
            "references": [{"path": "review.toml", "required": True}]}),
            encoding="utf-8")
        self.cwd = os.getcwd()
        self.addCleanup(os.chdir, self.cwd)

    def _remote(self):
        """A bare remote both `paste` and the push rule need: a declared
        cross-machine round may not bind a SHA no other clone can fetch."""
        import os
        from review.tests.test_worktree_and_brief import _sh
        bare = self.tmp / "origin.git"
        _sh("git", "init", "-q", "--bare", str(bare))
        _sh("git", "-C", str(self.repo), "remote", "add", "origin", str(bare))
        _sh("git", "-C", str(self.repo), "push", "-q", "-u", "origin", "main")

    def _run(self, *argv, env=None):
        import contextlib
        import json
        import os
        from io import StringIO
        from unittest import mock
        from review import cli
        os.chdir(self.repo)
        buf = StringIO()
        # The host running this suite may itself be a cloud sandbox
        # carrying a transport declaration; the tests here state their own
        # environment, so the host's is scrubbed and `env` (if any) is the
        # whole declaration set.
        scrubbed = {k: v for k, v in os.environ.items()
                    if k != vocab.TRANSPORT_ENV
                    and k not in {var for var, _v, _t
                                  in vocab.TRANSPORT_PROVIDER_SIGNALS}}
        scrubbed.update(env or {})
        try:
            with mock.patch.dict(os.environ, scrubbed, clear=True):
                with contextlib.redirect_stdout(buf):
                    code = cli.main(["--ledger-dir", str(self.state), *argv])
        finally:
            os.chdir(self.cwd)
        out = buf.getvalue()
        try:
            return code, json.loads(out)
        except json.JSONDecodeError:
            return code, out

    def test_the_contradiction_stops_the_verb(self):
        """`--local-only` and `--transport paste` reach the CLI together and
        the round does not open. This is RVW-T11 leg 2 — the reachable
        target — enforced instead of stamped and discovered later."""
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              "--base", self.base, "--local-only",
                              "--transport", "paste")
        self.assertNotEqual(code, 0)
        self.assertIn("cannot both be true", str(rec))
        # And nothing was recorded: the refusal precedes the ledger write.
        self.assertFalse((self.state / "ledger.jsonl").exists(), rec)

    def test_the_default_round_relays_one_command(self):
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              "--base", self.base, "--local-only")
        self.assertEqual(code, 0, rec)
        fence = rec["relay"].splitlines()
        body = fence[fence.index("```bash") + 1:]
        body = body[:body.index("```")]
        self.assertEqual(len(body), 1, rec["relay"])
        self.assertIn("take ", body[0])
        self.assertNotIn("take -", body[0])
        self.assertIn('transport="path"', rec["relay"] and self._envelope())

    def _envelope(self):
        from review import transport as t
        from review import config as c
        import dataclasses
        cfg = dataclasses.replace(c.load(self.repo, ledger_dir=str(self.state)))
        return t.exchange_path(cfg, 1, "request").read_text(encoding="utf-8")

    def test_a_declared_paste_round_carries_bytes_and_the_stdin_command(self):
        self._remote()
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              "--base", self.base, "--transport", "paste")
        self.assertEqual(code, 0, rec)
        envelope = self._envelope()
        self.assertIn('transport="paste"', envelope)
        self.assertIn("transport=paste", envelope)      # the Roles line
        self.assertIn("take - --as codex", rec["relay"])
        self.assertIn("take - --as codex", rec["reviewer_next"])
        # The bytes ride with the command that consumes them.
        self.assertIn(envelope.rstrip("\n"), rec["relay"])
        # And the ledger records what the envelope declared.
        import json
        events = [json.loads(l) for l in
                  (self.state / "ledger.jsonl").read_text().splitlines() if l]
        request = [e for e in events if e["event"] == "request"][0]
        self.assertEqual(request["transport"], "paste")

    def test_the_cache_will_not_serve_the_other_topology(self):
        """Re-running on an unchanged tip is a no-op — unless the declaration
        changed, which is a different envelope."""
        self._remote()
        first, rec = self._run("handoff", "--claim-file", str(self.claim),
                               "--base", self.base)
        self.assertEqual(first, 0, rec)
        self.assertFalse(rec["cached"])
        _, again = self._run("handoff", "--claim-file", str(self.claim),
                             "--base", self.base)
        self.assertTrue(again["cached"])
        # Undeclared silence emits `path` (the workflow's steady case), so
        # the OTHER topology here is an explicit `paste` declaration.
        _, switched = self._run("handoff", "--claim-file", str(self.claim),
                                "--base", self.base, "--transport", "paste")
        self.assertFalse(switched["cached"], switched)
        self.assertIn('transport="paste"', self._envelope())

    def test_local_and_cloud_declared_rounds_render_only_their_carrier(self):
        """Round 3 F2's paired controls, end to end: the undeclared local
        round renders the kept-path carrier and only it; the same round
        authored under a cloud environment's declaration — or under the
        documented provider signal — renders the paste carrier and only
        it. The mutation is the environment itself: remove the
        declaration and the carrier flips back."""
        self._remote()
        code, local = self._run("handoff", "--claim-file", str(self.claim),
                                "--base", self.base)
        self.assertEqual(code, 0, local)
        self.assertIn('transport="path"', self._envelope())
        self.assertIn("take ", local["relay"])
        self.assertNotIn("take -", local["relay"])
        for declaration in ({vocab.TRANSPORT_ENV: "paste"},
                            {"CLAUDE_CODE_REMOTE": "true"}):
            with self.subTest(declaration=declaration):
                code, cloud = self._run(
                    "handoff", "--claim-file", str(self.claim),
                    "--base", self.base, env=declaration)
                self.assertEqual(code, 0, cloud)
                # (The two declarations resolve to one effective carrier,
                # so the second is legitimately warm — the carrier, not
                # the cache state, is what this control pins.)
                self.assertIn('transport="paste"', self._envelope())
                self.assertIn("take - --as codex", cloud["relay"])
                # The bytes ride with the command that consumes them.
                self.assertIn(self._envelope().rstrip("\n"), cloud["relay"])
        # A forged declaration outside the vocabulary is refused before
        # anything is recorded or emitted.
        code, forged = self._run(
            "handoff", "--claim-file", str(self.claim),
            "--base", self.base, env={vocab.TRANSPORT_ENV: "carrier"})
        self.assertNotEqual(code, 0)
        self.assertEqual(forged["next_kind"], "blocked", forged)
        self.assertIn(vocab.TRANSPORT_ENV, forged["error"])

    def test_an_unknown_transport_never_reaches_the_ledger(self):
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              "--base", self.base, "--transport", "stdin")
        self.assertEqual(code, 2, rec)
        self.assertFalse((self.state / "ledger.jsonl").exists())

    def test_the_verdict_leg_reads_the_round_this_machine_recorded(self):
        """The reviewer's `validate` renders the author's command, and it
        gets the topology from the record its own `take` wrote — not from
        the verdict document, whose author could mis-transcribe a field that
        decides what the other side runs."""
        self._remote()
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              "--base", self.base, "--transport", "paste")
        self.assertEqual(code, 0, rec)
        code, taken = self._run("take", rec["kept"], "--as", "codex")
        self.assertEqual(code, 0, taken)
        self.assertEqual(taken["transport"], "paste")
        verdict = self.tmp / "v.md"
        verdict.write_text(
            f'<loupe-review-verdict sha="{rec["sha"]}">\n'
            f"VERDICT: clean to advance\n\n## findings\n\nNone\n\n"
            f"## evidence checked\n\n- the diff\n"
            f"</loupe-review-verdict>\n", encoding="utf-8")
        code, validated = self._run("validate", str(verdict))
        self.assertEqual(code, 0, validated)
        self.assertIn("close --verdict -", validated["relay"])
        self.assertNotIn(str(verdict), validated["relay"])
        # Round 3, live: the paste round's relay carries the verdict BYTES
        # in their own fence — bytes outside a fence are bytes a chat
        # surface may rewrite, which is how a validated verdict arrived
        # unreadable.
        self.assertIn("The verdict, to paste:", validated["relay"])
        self.assertIn(verdict.read_text(encoding="utf-8").rstrip("\n"),
                      validated["relay"])


class TestTransportDeclarationClosedWorld(unittest.TestCase):
    """R1-F2: ONE schema-derived lifecycle over every admitted source and
    reader.

    `vocab.transport_or_default` is the boundary; these tests partition its
    complete input domain per source — config missing/empty/valid/invalid,
    author flag omitted/valid/invalid/repeated, the reviewer's correction
    over the same occurrence domain, wrapper absent/empty/valid/invalid/
    duplicate, legacy absence, both record kinds, both relays, both cache
    directions, and direct `brief`/`validate` entry — with paired valid
    controls and a sentinel at every edge proving the refusal lands BEFORE
    a ledger write, a git call, cache use, or a runnable relay. The round-1
    probes this closes: `[roles] transport = ""` resolving to `path`,
    `--transport paste --transport path` collapsing to the last writer,
    and `brief` rendering a live `take` command from a wrapper stamped
    `transport="carrier"`.
    """

    # ------------------------------------------------------------- config

    def _cfg(self, **roles):
        return dataclasses.replace(CFG, roles={**CFG.roles, **roles})

    def test_config_missing_resolves_to_the_emission_default(self):
        # Round 3 F2: an undeclared topology in an environment carrying no
        # declaration emits the workflow's steady case, `path`.
        cfg = dataclasses.replace(
            CFG, roles={k: v for k, v in CFG.roles.items()
                        if k != "transport"})
        self.assertEqual(emit.resolve_transport(cfg, env={}),
                         vocab.TRANSPORT_EMISSION_DEFAULT)

    def test_config_valid_resolves_to_itself(self):
        for declared in vocab.TRANSPORTS:
            self.assertEqual(
                emit.resolve_transport(self._cfg(transport=declared)),
                declared)

    def test_config_explicitly_empty_is_refused_not_defaulted(self):
        # The first round-1 probe: `[roles] transport = ""` folded through
        # `or` to `path`. Stating a value emptily is not stating none.
        with self.assertRaises(vocab.TransportDeclarationError) as caught:
            emit.resolve_transport(self._cfg(transport=""))
        self.assertIn("explicitly empty", str(caught.exception))

    def test_config_invalid_is_refused_by_value(self):
        with self.assertRaises(vocab.TransportDeclarationError) as caught:
            emit.resolve_transport(self._cfg(transport="carrier"))
        self.assertIn("'carrier'", str(caught.exception))
        self.assertIn("path", caught.exception.remedy)
        self.assertIn("paste", caught.exception.remedy)

    # -------------------------------------- the author flag, the reviewer's
    # correction: omitted / valid / invalid / repeated-same / repeated-
    # conflicting, in both orders, over both verbs that admit the option.

    def _parse(self, *argv):
        from review import cli
        return cli.build_parser().parse_args(list(argv))

    def test_flag_omitted_leaves_the_config_in_charge(self):
        from review import cli
        args = self._parse("take", "x", "--as", "codex")
        self.assertIsNone(args.transport)
        args = self._parse("handoff")
        self.assertIsNone(args.transport)

    def test_flag_valid_parses_on_both_sides(self):
        for declared in vocab.TRANSPORTS:
            self.assertEqual(
                self._parse("handoff", "--transport", declared).transport,
                declared)
            self.assertEqual(
                self._parse("take", "x", "--as", "codex",
                            "--transport", declared).transport,
                declared)

    def test_flag_invalid_is_refused_at_the_parser(self):
        from review import cli
        for argv in (["handoff", "--transport", "carrier"],
                     ["take", "x", "--as", "codex", "--transport", ""],
                     ["take", "x", "--as", "codex", "--transport", "stdin"]):
            with self.subTest(argv=argv):
                with self.assertRaises(cli.UsageError):
                    self._parse(*argv)

    def test_flag_repeated_is_refused_never_last_write_wins(self):
        # The second round-1 probe: the reviewer's correction used ordinary
        # argparse storage, so `--transport paste --transport path` parsed
        # silently to `path`. Same value or conflicting, either order, both
        # verbs: refused with both occurrences named.
        from review import cli
        for argv in (
                ["take", "x", "--as", "codex",
                 "--transport", "paste", "--transport", "path"],
                ["take", "x", "--as", "codex",
                 "--transport", "path", "--transport", "paste"],
                ["take", "x", "--as", "codex",
                 "--transport", "paste", "--transport", "paste"],
                ["handoff", "--transport", "paste", "--transport", "path"],
                ["handoff", "--transport", "path", "--transport", "paste"],
                ["handoff", "--transport", "path", "--transport", "path"]):
            with self.subTest(argv=argv):
                with self.assertRaises(cli.UsageError) as ctx:
                    self._parse(*argv)
                self.assertIn("more than once", str(ctx.exception))

    # ------------------------------------------------- the wrapper's stamp

    def test_wrapper_absent_reads_as_the_default(self):
        # Legacy absence: every envelope emitted before the attribute
        # existed is silent, and its silence is the topology it ran.
        parsed = wire.parse_request(request_text())
        self.assertEqual(transport.declared_transport(parsed),
                         vocab.TRANSPORT_DEFAULT)

    def test_wrapper_valid_reads_back_both_values(self):
        for declared in vocab.TRANSPORTS:
            parsed = wire.parse_request(request_text(transport_attr=declared))
            self.assertEqual(transport.declared_transport(parsed), declared)

    def test_wrapper_empty_and_invalid_are_refused_by_the_reader(self):
        for stamp in ("", "carrier", "stdin", "PATH"):
            with self.subTest(stamp=stamp):
                text = request_text(transport_attr="paste").replace(
                    'transport="paste"', f'transport="{stamp}"')
                parsed = wire.parse_request(text)
                with self.assertRaises(vocab.TransportDeclarationError):
                    transport.declared_transport(parsed)

    def test_wrapper_duplicate_is_a_grammar_defect(self):
        text = request_text(transport_attr="paste").replace(
            'transport="paste"', 'transport="paste" transport="path"')
        codes = [i.code for i in validate_request(
            wire.parse_request(text), CFG, structural_only=True)]
        self.assertIn("E-ATTR-DUPLICATE", codes)

    # ------------------------------------------------------- the records

    def test_records_of_both_kinds_answer_with_their_value(self):
        for event in ("request", "take"):
            for declared in vocab.TRANSPORTS:
                ledger = Ledger.in_memory()
                ledger.add({"event": event, "round": 1, "sha": SHA_B,
                            "transport": declared})
                self.assertEqual(
                    transport.recorded_transport(ledger, SHA_B), declared)

    def test_recorded_empty_or_invalid_is_refused_before_a_relay(self):
        for value in ("", "carrier"):
            with self.subTest(value=value):
                ledger = Ledger.in_memory()
                ledger.add({"event": "take", "round": 1, "sha": SHA_B,
                            "transport": value})
                with self.assertRaises(vocab.TransportDeclarationError):
                    transport.recorded_transport(ledger, SHA_B)

    def test_recorded_legacy_absence_is_the_default(self):
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": SHA_B})
        self.assertEqual(transport.recorded_transport(ledger, SHA_B),
                         vocab.TRANSPORT_DEFAULT)

    # ------------------------------------------- sentinels at the lifecycle
    # edges: the refusal lands before anything downstream acts.

    def test_take_refuses_a_defective_stamp_before_git_and_the_ledger(self):
        cfg = TestTake._cfg(self)
        git = TestTake._git(self)
        ledger = Ledger.in_memory()
        text = request_text(transport_attr="paste").replace(
            'transport="paste"', 'transport="carrier"')
        with self.assertRaises((transport.Refusal,
                                vocab.TransportDeclarationError)):
            transport.take(cfg, ledger, text, "r.md", git=git,
                           reviewer="codex")
        self.assertEqual(ledger.events(), [])
        self.assertEqual(git.calls, [])

    def test_the_cache_refuses_an_empty_config_before_serving(self):
        # A WARM copy exists and the invocation's config declares the
        # transport emptily: the old `or` folded that to `path` and served
        # the envelope. The boundary refuses before the cache can answer.
        import tempfile
        from pathlib import Path
        fixture = TestTheCacheKeyIncludesIt()
        with tempfile.TemporaryDirectory() as tmp:
            cfg, ledger = fixture._ledger_and_cfg(Path(tmp), "path")
            cfg = dataclasses.replace(
                cfg, roles={**cfg.roles, "transport": ""})
            # Control: with a valid declaration the same state is warm.
            self.assertIsNotNone(transport.cached_handoff(
                dataclasses.replace(
                    cfg, roles={**cfg.roles, "transport": "path"}),
                ledger, 1, git=fixture._git(),
                claim_digest=fixture.CLAIM, roles=("claude", "codex")))
            with self.assertRaises(vocab.TransportDeclarationError):
                transport.cached_handoff(cfg, ledger, 1, git=fixture._git(),
                                         claim_digest=fixture.CLAIM,
                                         roles=("claude", "codex"))

    # ------------------------------------------------------- both relays

    def test_both_carriers_render_from_a_valid_declaration(self):
        # Paired controls: the boundary must not have closed the two
        # states it exists to distinguish.
        parsed_path = wire.parse_request(request_text(transport_attr="path"))
        self.assertIn("take /kept/r.md",
                      brief.relay("/kept/r.md", parsed_path, "bytes"))
        parsed_paste = wire.parse_request(
            request_text(transport_attr="paste"))
        self.assertIn("take - --as",
                      brief.relay("/kept/r.md", parsed_paste, "bytes"))
        for recorded in vocab.TRANSPORTS:
            self.assertIn("close --verdict",
                          brief.verdict_relay(
                              type("V", (), {"verdict": "clean to advance"}),
                              source="/kept/v.md", transport=recorded))

    def test_a_defective_stamp_cannot_reach_a_relay(self):
        parsed = wire.parse_request(request_text(transport_attr="paste")
                                    .replace('transport="paste"',
                                             'transport="carrier"'))
        with self.assertRaises(vocab.TransportDeclarationError):
            brief.relay("/kept/r.md", parsed, "bytes")

    # ------------------------------------------------- direct brief entry

    def _brief(self, text):
        import argparse as _argparse
        import contextlib
        import io
        import json
        import tempfile
        from pathlib import Path
        from review import cli
        with tempfile.TemporaryDirectory() as tmp:
            envelope = Path(tmp) / "request.md"
            envelope.write_text(text, encoding="utf-8")
            cfg = dataclasses.replace(CFG, ledger_dir=Path(tmp))
            args = _argparse.Namespace(envelope=str(envelope),
                                       ledger_dir=str(tmp), paste=False,
                                       full=False)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = cli.cmd_brief(args, cfg)
            return code, json.loads(buf.getvalue())

    def test_brief_refuses_the_defective_wrapper_before_the_relay(self):
        # The third round-1 probe: a request stamped `transport="carrier"`
        # returned `brief.relay` carrying the live command
        # `loupe take /authors-only/request.md --as codex`.
        text = request_text(transport_attr="paste").replace(
            'transport="paste"', 'transport="carrier"')
        code, payload = self._brief(text)
        self.assertNotEqual(code, 0)
        self.assertNotIn("relay", payload)
        self.assertIn("R-TRANSPORT",
                      [i["code"] for i in payload["items"]])

    def test_brief_still_renders_a_valid_request(self):
        code, payload = self._brief(request_text(transport_attr="paste"))
        self.assertEqual(code, 0, payload)
        self.assertIn("take - --as", payload["relay"])


class TestRecordedTransportProductReaders(unittest.TestCase):
    """Round 2 F2: the recorded transport is a fact resolved BEFORE anything
    is written, kept across a clean lineage closure, and refused through its
    TYPED recovery.

    The three product readers of `recorded_transport` — successful
    `validate`, direct `brief`, and both branches of `close` — are each
    partitioned over the record states: absent legacy, valid `path`, valid
    `paste`, explicitly empty, unknown, newest-invalid-over-older-valid and
    newest-valid-over-older-invalid. A defective record must have NO ledger
    side effect and must exit `next_kind: blocked` carrying the declaration
    remedy — never `<verb> --help`, which cannot repair an append-only
    record (the repair is a corrected `take`/`request` record, which
    supersedes by recency). These tests fail if resolution moves back after
    `close_round`, if the reader scans past the newest record, if a clean
    closure re-defaults the carrier, or if the generic usage handler
    swallows the typed error again.
    """

    def setUp(self):
        import tempfile
        import shutil
        from pathlib import Path
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="recorded-transport-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def _ledger(self, *transports, sha=SHA_B):
        """A ledger whose round-1 request records each value in turn —
        `absent` means a legacy event with no transport key at all."""
        led = Ledger(self.tmp)
        for i, value in enumerate(transports):
            event = {"event": "request", "round": 1, "sha": sha,
                     "author": "claude", "reviewer": "codex",
                     "source_digest": f"d{i}", "bytes": 1 + i}
            if value != "absent":
                event["transport"] = value
            led.add(event)
        return led

    def _run(self, *argv):
        import contextlib
        import io
        import json
        from review import cli
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["--ledger-dir", str(self.tmp), *argv])
        return code, json.loads(buf.getvalue())

    def _verdict_file(self, verdict="changes requested", sha=SHA_B):
        from review.tests.test_transport import verdict_text
        path = self.tmp / "v.md"
        path.write_text(verdict_text(sha=sha, verdict=verdict),
                        encoding="utf-8")
        return str(path)

    def _events(self):
        return [e["event"] for e in Ledger(self.tmp).events()]

    # ----------------------------------------------------- valid carriers

    def test_close_carries_each_valid_record_into_the_relay(self):
        # The paired valid controls that prove the readers are live.
        for value, marker in (("path", "--verdict "),
                              ("paste", "close --verdict -"),
                              ("absent", "--verdict ")):
            with self.subTest(record=value):
                import shutil
                shutil.rmtree(self.tmp / "exchange", ignore_errors=True)
                (self.tmp / "ledger.jsonl").unlink(missing_ok=True)
                self._ledger(value)
                code, rec = self._run("close", "--verdict",
                                      self._verdict_file())
                self.assertEqual(code, 0, rec)
                self.assertIn(marker, rec["relay"])
                if value == "paste":
                    self.assertNotIn(str(self.tmp / "v.md"), rec["relay"])

    def test_a_clean_close_keeps_the_recorded_paste_across_closure(self):
        # The lineage-closure leg: closing empties `current()`, so a reader
        # resolving AFTER the write silently fell to the default and told a
        # cross-machine author to open the reviewer's local path.
        self._ledger("paste")
        code, rec = self._run("close", "--verdict",
                              self._verdict_file("clean to advance"))
        self.assertEqual(code, 0, rec)
        self.assertIn("close --verdict -", rec["relay"])
        self.assertNotIn(str(self.tmp / "v.md"), rec["relay"])
        self.assertIn("lineage_closed", self._events())

    def test_newest_valid_supersedes_an_older_invalid_record(self):
        # Recency repairs: a corrected record ends the refusal.
        self._ledger("carrier", "paste")
        code, rec = self._run("close", "--verdict", self._verdict_file())
        self.assertEqual(code, 0, rec)
        self.assertIn("close --verdict -", rec["relay"])

    # -------------------------------------------------- defective records

    def test_a_defective_record_refuses_close_before_any_write(self):
        # The finding's live probe: a changes-requested close on a
        # `carrier` record appended the verdict, findings and aliases, then
        # exited 2 advertising `loupe close --help`.
        for value in ("carrier", ""):
            with self.subTest(record=repr(value)):
                (self.tmp / "ledger.jsonl").unlink(missing_ok=True)
                self._ledger(value)
                code, rec = self._run("close", "--verdict",
                                      self._verdict_file())
                self.assertNotEqual(code, 0)
                self.assertEqual(rec["next_kind"], "blocked", rec)
                self.assertIsNone(rec["next"])
                self.assertIn("declares one of", rec["remedy"])
                self.assertEqual(self._events(), ["request"],
                                 "close mutated the ledger before the "
                                 "transport refusal")

    def test_a_clean_close_on_a_defective_record_does_not_close_the_lineage(self):
        self._ledger("carrier")
        code, rec = self._run("close", "--verdict",
                              self._verdict_file("clean to advance"))
        self.assertNotEqual(code, 0)
        self.assertEqual(rec["next_kind"], "blocked")
        self.assertNotIn("lineage_closed", self._events())

    def test_newest_invalid_over_older_valid_refuses_not_scans_past(self):
        # R1-F2's rule holds at every reader: the newest record DECIDES,
        # and a reader that scans back to the older `paste` invents a
        # round nobody declared.
        self._ledger("paste", "carrier")
        code, rec = self._run("close", "--verdict", self._verdict_file())
        self.assertNotEqual(code, 0)
        self.assertEqual(rec["next_kind"], "blocked")
        self.assertEqual(self._events(), ["request", "request"])

    def test_brief_maps_the_defective_record_to_the_typed_recovery(self):
        # The finding's other live probe: `brief <verdict>` on a `carrier`
        # take record advertised `loupe brief --help` as runnable recovery.
        led = Ledger(self.tmp)
        led.add({"event": "take", "round": 1, "sha": SHA_B,
                 "transport": "carrier"})
        code, rec = self._run("brief", self._verdict_file())
        self.assertNotEqual(code, 0)
        self.assertEqual(rec["next_kind"], "blocked", rec)
        self.assertIsNone(rec["next"])
        self.assertIn("declares one of", rec["remedy"])

    def test_brief_reads_each_valid_record(self):
        for value, marker in (("paste", "close --verdict -"),
                              ("path", "--verdict "),
                              ("absent", "--verdict ")):
            with self.subTest(record=value):
                (self.tmp / "ledger.jsonl").unlink(missing_ok=True)
                led = Ledger(self.tmp)
                event = {"event": "take", "round": 1, "sha": SHA_B}
                if value != "absent":
                    event["transport"] = value
                led.add(event)
                code, rec = self._run("brief", self._verdict_file())
                self.assertEqual(code, 0, rec)
                self.assertIn(marker, rec["relay"])

    def test_validate_maps_the_defective_record_to_the_typed_recovery(self):
        # The third reader: successful validation of a verdict resolves the
        # carrier for the relay it emits, and a defective record must not
        # escape as usage help there either.
        led = Ledger(self.tmp)
        led.add({"event": "take", "round": 1, "sha": SHA_B,
                 "transport": "carrier"})
        code, rec = self._run("validate", self._verdict_file())
        self.assertNotEqual(code, 0)
        self.assertEqual(rec["next_kind"], "blocked", rec)
        self.assertIn("declares one of", rec["remedy"])

    def test_validate_renders_the_recorded_paste_relay(self):
        led = Ledger(self.tmp)
        led.add({"event": "take", "round": 1, "sha": SHA_B,
                 "transport": "paste"})
        code, rec = self._run("validate", self._verdict_file())
        self.assertEqual(code, 0, rec)
        self.assertIn("close --verdict -", rec["relay"])


if __name__ == "__main__":
    unittest.main()

"""The config-evolution seam: what a reader does with a config written for a
NEWER tool than itself (2026-09-01, brief
config-keys-are-a-cross-installation-contract).

Measured on lineage 20 round 2, before any diff was read. `[roles] debug`
entered the grammar in 0.14.0; the relay prints the generic `loupe take …`
because the tool renders its own name and cannot know how a reader installed
itself; the installation that command reached was the published clone at
0.12.1. It refused at exit 2:

    repo:review.toml declares 1 invalid value(s): [roles] states unknown key
      'debug'
    remedy: a person repairs repo:review.toml

The config was valid, the in-tree 0.14.0 reader parsed the identical bytes,
and the remedy instructed a person to repair a correct file.

THE TWO READERS THIS FILE BINDS. The verdict required the seam be tested
against a PRIOR-RELEASE reader and the current one, and an installed sibling
clone is an external mutable dependency — it can be upgraded, moved or
deleted between two runs of this suite, so a test resting on it proves
whatever that clone happened to be that morning. The prior-release reader
here is therefore GENERATED: `_prior_release_reader` derives it from this
package's own schema authority (`config.DEFAULTS` and
`config.CONFIG_DECLARED_ELSEWHERE`) with the post-0.12.1 names removed. It
is hermetic, and it cannot drift out of step with the real schema the way a
hand-maintained copy of an old key list would — a key added to the current
grammar and not named in `AFTER_0_12_1` simply stays in the synthetic old
reader, which fails loudly here rather than quietly passing.

WHAT THE FIX CAN AND CANNOT REACH. `[tool] requires` lets a repository state
the floor it was written for, so a reader below that floor names both
versions instead of blaming the config. It reaches only readers that know
the key: an installation older than `[tool]` itself still refuses with
`unknown section [tool]`. That case is asserted below as UNHELPABLE on
purpose — it is the mechanism's honest limit, not a gap in it, and no change
to the current version can reach a binary already deployed.

MUTATION: move `check_tool_version` below `check_shape` in either door of
`review/config.py` and the ordering row fails, because the reader would then
report the unknown key it met first and hide the version floor that explains
it. Delete the call entirely and every current-reader row fails while the
prior-release rows still pass — which is what makes those rows a control on
the seam rather than a restatement of it.
"""
import contextlib
import json
import re
import unittest
from pathlib import Path
from unittest import mock

from review import TOOL_VERSION, config, emit, validate, vocab, wire
from review.emit import emit_request
from review.ledger import Ledger
from review.tests._transport_fixtures import (git_out, run_cli,
                                              scratch_loop_repo, sh)
from review.tests.synth import CFG
from review.tests.util import REPO_ROOT

#: Config names this grammar gained AFTER the last published reader that a
#: relay's generic command actually reached (0.12.1). Removing exactly these
#: from the live authority is what makes the synthetic reader a prior
#: release rather than a different tool.
AFTER_0_12_1_KEYS = (("roles", "debug"), ("roles", "review_default"),
                     ("roles", "enforcement"))
AFTER_0_12_1_SECTIONS = ("tool",)


@contextlib.contextmanager
def _prior_release_reader():
    """This package's schema authority with the post-0.12.1 names removed.

    Derived, never transcribed: both `DEFAULTS` and
    `CONFIG_DECLARED_ELSEWHERE` are copied from the live module and pruned,
    so the synthetic reader is the CURRENT reader minus a named delta. A
    hand-written 0.12.1 key list would be a second authority to keep in
    step, which is the shape this repository refuses everywhere else.

    `check_shape` and `_kind_for` read both names as module globals at call
    time, so patching the module is enough to make the reader older; nothing
    is monkeypatched inside the functions under test.
    """
    defaults = {section: dict(body)
                for section, body in config.DEFAULTS.items()
                if section not in AFTER_0_12_1_SECTIONS}
    elsewhere = {name: kind
                 for name, kind in config.CONFIG_DECLARED_ELSEWHERE.items()
                 if name not in AFTER_0_12_1_KEYS
                 and name[0] not in AFTER_0_12_1_SECTIONS}
    for section, key in AFTER_0_12_1_KEYS:
        defaults.get(section, {}).pop(key, None)
    with mock.patch.object(config, "DEFAULTS", defaults), \
            mock.patch.object(config, "CONFIG_DECLARED_ELSEWHERE", elsewhere):
        yield


def _read(text, source="repo:review.toml"):
    """The current reader's full config door, on TOML text."""
    return config.from_text(text, like=CFG, source=source)


def _read_old(text, source="repo:review.toml"):
    """The prior-release reader's only door: it has no version check to run,
    which is precisely the state being characterised."""
    import tomllib
    with _prior_release_reader():
        config.check_shape(tomllib.loads(text), source)


class TestThePriorReleaseReader(unittest.TestCase):
    """What an ALREADY-DEPLOYED reader does. Nothing in this class can be
    improved by editing the current version; the refusals here are emitted
    by a reader that shipped before the fix existed."""

    def test_a_prior_release_reader_refuses_a_config_carrying_a_newer_key(
            self):
        """The measured defect, reproduced hermetically.

        This is the UNHELPABLE case and it is asserted as such: the refusal
        comes from a binary that predates every line of the fix, so its
        message stays wrong forever. Recording it here is what stops the
        seam being re-litigated as "just improve the 0.14.0 error text" —
        that text is not the one the reviewer saw.
        """
        with self.assertRaises(config.ConfigError) as ctx:
            _read_old("[roles]\ndebug = true\n")
        message = str(ctx.exception)
        self.assertIn("unknown key 'debug'", message)
        # And the remedy is the false one: a person, repairing a valid file.
        self.assertIn("a person repairs", ctx.exception.remedy)

    def test_the_current_reader_accepts_the_bytes_the_old_one_refused(self):
        """The paired control that makes the row above a SKEW and not a
        broken config: identical bytes, two readers, opposite answers."""
        cfg = _read("[roles]\ndebug = true\n")
        self.assertTrue(cfg.roles["debug"])

    def test_a_prior_release_reader_refuses_the_requires_key_itself(self):
        """The chicken-and-egg limit, asserted rather than described.

        An installation older than `[tool]` cannot be helped by `[tool]`.
        It refuses with `unknown section [tool]` — which is why the section
        is a section: the message names a whole feature the reader has never
        heard of, instead of a key that reads like a typo in a section it
        does understand. This is the argument for shipping the mechanism
        early, when the too-old population is smallest, and it is why this
        repository's own `review.toml` declares no floor yet.
        """
        with self.assertRaises(config.ConfigError) as ctx:
            _read_old(f'[tool]\nrequires = "{TOOL_VERSION}"\n')
        self.assertIn("unknown section [tool]", str(ctx.exception))

    def test_this_repository_declares_its_floor_and_stages_its_reader(self):
        """The bootstrap property, re-based 2026-09-03.

        Until then this repository's committed config had to parse under
        the prior-release reader, because the relay's generic command
        reached whatever `loupe` sat on PATH — measured at 0.12.1 while the
        checkout was 0.16.0. That constraint is gone by construction: the
        blocking `loupe-stage` gate refuses a handoff while bare `loupe`
        lags this checkout's TOOL_VERSION, and `[tool] requires` names the
        floor so any reader that still lags refuses with the version, not
        with a false config repair. Both halves are asserted here: drop
        either and a lagging reader is once more reached silently.
        """
        import tomllib
        toml = tomllib.loads((REPO_ROOT / "review.toml")
                             .read_text(encoding="utf-8"))
        if "tool" not in toml:
            self.skipTest("the published example config declares no floor; "
                          "staging is the workbench's own property")
        self.assertEqual(toml["tool"]["requires"], TOOL_VERSION)
        gates = {g["id"]: g for g in toml["gates"]}
        self.assertIn("loupe-stage", gates)
        self.assertEqual(gates["loupe-stage"]["command"],
                         ["bin/loupe-stage", "--check"])
        self.assertTrue(gates["loupe-stage"]["blocking"])
        with self.assertRaises(config.ConfigError):
            _read_old((REPO_ROOT / "review.toml").read_text(encoding="utf-8"))


class TestTheVersionFloor(unittest.TestCase):
    """The current reader, meeting a config that states its floor."""

    def _requires(self, value):
        return f'[tool]\nrequires = "{value}"\n'

    def test_a_floor_above_the_installation_refuses_naming_both_versions(
            self):
        """The message the whole finding turns on.

        Both numbers, exactly: the required floor answers "what would read
        this?", the installed version answers "what am I?", and a reader
        given only one of them still cannot tell a skew from a defect. The
        refusal must also NOT tell a person to repair the config, which is
        the false remedy being replaced.
        """
        with self.assertRaises(config.ConfigError) as ctx:
            _read(self._requires("99.0.0"))
        message = str(ctx.exception)
        self.assertIn("99.0.0", message)
        self.assertIn(TOOL_VERSION, message)
        self.assertIn("requires loupe 99.0.0 or newer", message)
        self.assertIn("this installation is " + TOOL_VERSION, message)
        self.assertIn("The configuration is not broken", message)
        # A blocked state with a human remedy — never a command an agent
        # would run, and never an instruction to edit the valid file.
        self.assertEqual(ctx.exception.next_cmd, "")
        self.assertEqual(ctx.exception.kind, "blocked")
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("do not edit", ctx.exception.remedy)

    def test_a_floor_at_or_below_the_installation_passes(self):
        """Equal, lower, and differing component counts.

        `0.14` and `0.14.0` are one version: the shorter side is padded, not
        compared by length. MUTATION: drop the padding in `version_below`
        and `0.9` reads as newer than the three-component installed value,
        refusing a repository that requires something ancient.
        """
        for floor in (TOOL_VERSION, "0.9.9", "0.1", "0", "0.14"):
            with self.subTest(floor=floor):
                cfg = _read(self._requires(floor))
                self.assertEqual(cfg.required_version, floor)

    def test_an_undeclared_floor_is_silence_and_not_a_floor_of_zero(self):
        """`required_version` is None, not `"0.0.0"`. A repository that
        declares nothing has not declared a minimum of nothing — the same
        distinction `token_budget` holds, and the reason `requires` has no
        default in DEFAULTS."""
        self.assertIsNone(_read("[roles]\nauthor = \"a\"\n").required_version)

    def test_a_malformed_floor_is_a_typed_refusal_naming_the_value(self):
        """A floor the reader cannot parse is refused, never ignored.

        Silently passing would make the guarantee depend on spelling: a
        repository that typed `0.14.0-rc1` would believe it had declared a
        floor and would have declared nothing. Each refusal must name the
        offending value, or a person cannot find it — and none of them may
        be a crash, because every config door owes a typed error the CLI
        boundary can turn into a remedy.
        """
        for bad in ("0.14.0-rc1", "banana", "", "0.14.", ".1", "v0.14.0",
                    "0.14.0 ", "١.٢"):
            with self.subTest(value=bad):
                with self.assertRaises(config.ConfigError) as ctx:
                    _read("[tool]\nrequires = {}\n".format(json.dumps(bad)))
                message = str(ctx.exception)
                self.assertIn("unreadable [tool] requires value", message)
                # The value itself, so a person can find the line. `repr`
                # rather than the bare string: an empty or space-padded
                # value is invisible unquoted, and those are exactly the
                # ones hardest to spot in a file.
                self.assertIn(repr(bad), message)
                self.assertEqual(ctx.exception.code, 2)

    def test_a_non_string_floor_is_refused_before_it_is_compared(self):
        """The kind error and the version check meet at the same value, and
        the version check runs first. It must therefore refuse a non-string
        itself rather than assume `check_shape` will get there — a bare
        comparison against an int would be a TypeError escaping the
        boundary, which is the class of failure round 4 F4 closed."""
        for bad in ("14", "true", "[\"0.14.0\"]"):
            with self.subTest(value=bad):
                with self.assertRaises(config.ConfigError) as ctx:
                    _read(f"[tool]\nrequires = {bad}\n")
                self.assertIn("unreadable [tool] requires value",
                              str(ctx.exception))


class TestTheGrammarStaysClosed(unittest.TestCase):
    """The paired controls. The fix must diagnose a version skew WITHOUT
    opening the hole that tolerating unknown keys would open."""

    def test_a_misspelled_key_still_refuses_under_the_current_reader(self):
        """Closed grammar is live. `debgu` is not `debug`, and a reader that
        shrugged at it would let a misspelling silently erase what it meant
        to declare — the exact reason option 3 (tolerate unknown keys) was
        rejected and stays rejected."""
        with self.assertRaises(config.ConfigError) as ctx:
            _read("[roles]\ndebgu = true\n")
        self.assertIn("unknown key 'debgu'", str(ctx.exception))

    def test_an_unknown_section_still_refuses_under_the_current_reader(self):
        """The section half of the same control: adding `[tool]` to the
        grammar must not have opened it to any section at all."""
        with self.assertRaises(config.ConfigError) as ctx:
            _read("[toool]\nrequires = \"0.1.0\"\n")
        self.assertIn("unknown section [toool]", str(ctx.exception))

    def test_the_version_refusal_wins_over_an_unknown_key(self):
        """THE ORDERING CONTROL, and the reason the check sits above the
        schema at both doors.

        A config that declares a floor this reader does not meet AND uses a
        key only the newer reader knows has ONE cause, not two. Reported the
        other way round, the reader names the symptom it met first and hides
        the cause that explains it — which is the false-remedy defect
        restated, not fixed. MUTATION: swap the two calls in
        `config.from_text` and this row fails while every other row here
        still passes.
        """
        with self.assertRaises(config.ConfigError) as ctx:
            _read('[tool]\nrequires = "99.0.0"\n'
                  '[roles]\nfrom_the_future = "x"\n')
        message = str(ctx.exception)
        self.assertIn("requires loupe 99.0.0 or newer", message)
        self.assertNotIn("unknown key", message)

    def test_the_ordering_holds_at_the_load_door_too(self):
        """`from_text` governs a TARGET's config and `load` governs the
        reader's own; both were the doors that emitted the measured refusal
        (`repo:review.toml` is `load`'s label). A fix wired into one of them
        would leave the other reporting the symptom, so the ordering is
        asserted at both.
        """
        import os
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "review.toml"
            cfg.write_text('[tool]\nrequires = "99.0.0"\n'
                           '[roles]\nfrom_the_future = "x"\n',
                           encoding="utf-8")
            with mock.patch.dict(os.environ,
                                 {"LOUPE_CONFIG": str(cfg)}):
                with self.assertRaises(config.ConfigError) as ctx:
                    config.load(Path(tmp))
        message = str(ctx.exception)
        self.assertIn("99.0.0", message)
        self.assertIn(TOOL_VERSION, message)
        self.assertNotIn("unknown key", message)


class TestTheUndeclaredNameRefusalNamesTheSkew(unittest.TestCase):
    """The residual of `config-keys-are-a-cross-installation-contract`,
    landed 2026-09-03: what the CURRENT reader says when it meets a name it
    does not know and NO floor fired.

    `[tool] requires` closes the class from 0.15.0 forward, and only for a
    repository that declares it. The case left over is the one that raised
    the brief: a config written for a newer tool that declares no floor — or
    declares one this reader has never heard of — looks exactly like a
    misspelling from here, and the refusal sent a person to repair a file
    that may be correct. The reader cannot tell the two apart, so it names
    both possibilities and its own version.

    Forward-only, as the brief says: this reaches skews that come after it,
    never a reader already deployed. And it opens nothing — the grammar is
    as closed as it was, which the misspelling control next door pins.

    MUTATION: drop the `undeclared` branch and the skew is unmentioned
    again, with the false remedy standing alone.
    """

    def remedy_for(self, body):
        with self.assertRaises(config.ConfigError) as ctx:
            _read(body)
        return ctx.exception.remedy

    def test_an_unknown_key_names_the_skew_and_this_installation(self):
        remedy = self.remedy_for("[roles]\nfrom_the_future = \"x\"\n")
        self.assertIn("version skew", remedy)
        self.assertIn(TOOL_VERSION, remedy)
        self.assertIn("[tool] requires", remedy)

    def test_an_unknown_section_says_the_same(self):
        remedy = self.remedy_for("[future]\nkey = \"x\"\n")
        self.assertIn("version skew", remedy)
        self.assertIn(TOOL_VERSION, remedy)

    def test_a_wrong_KIND_is_the_control_and_says_nothing_about_versions(self):
        # A declared key with the wrong type is the config's own defect in
        # every version: no reader, old or new, would have accepted it. The
        # skew sentence there would be noise that teaches a person to
        # discount it where it is true.
        remedy = self.remedy_for("[limits]\nround_cap = \"three\"\n")
        self.assertNotIn("version skew", remedy)

    def test_a_declared_floor_still_wins_over_the_skew_note(self):
        # Where the repository DID declare a floor, the true diagnosis is
        # available and is what the reader must say — the ordering control
        # next door, restated from this side: the guess never displaces the
        # fact.
        with self.assertRaises(config.ConfigError) as ctx:
            _read('[tool]\nrequires = "99.0.0"\n'
                  '[roles]\nfrom_the_future = "x"\n')
        self.assertNotIn("version skew", ctx.exception.remedy)
        self.assertIn("99.0.0", str(ctx.exception))


class TestVersionComparison(unittest.TestCase):
    """The comparison itself: stdlib only, no `packaging`, no
    `pkg_resources` — this package imports nothing outside the standard
    library and never will (§6.1's neighbour rule)."""

    def test_parse_rejects_everything_that_is_not_dotted_ascii_digits(self):
        """Returning None rather than raising is deliberate: every caller is
        a boundary that owes a typed refusal, and the value comes from a
        file anyone may edit. Non-ASCII digits are rejected explicitly —
        `\\d` in a str pattern would accept `١٤` and then compare against a
        version nobody wrote."""
        for bad in ("", "v1", "1.", ".1", "1..2", "1.2.3a", "1 . 2", " 1.2",
                    "١.٢", None, 14, True, ["1", "2"]):
            with self.subTest(value=bad):
                self.assertIsNone(config.parse_version(bad))

    def test_parse_reads_the_real_shapes(self):
        self.assertEqual(config.parse_version("0.14.0"), (0, 14, 0))
        self.assertEqual(config.parse_version("0"), (0,))
        self.assertEqual(config.parse_version("10.0.99"), (10, 0, 99))
        # Numeric, not lexical: 14 is above 9, and above 2 digits of it.
        self.assertEqual(config.parse_version("0.014.0"), (0, 14, 0))

    def test_comparison_is_numeric_and_length_insensitive(self):
        below = config.version_below
        parse = config.parse_version
        cases = (("0.9.9", "0.14.0", True),
                 ("0.14.0", "0.14.0", False),
                 ("0.14", "0.14.0", False),
                 ("0.14.0", "0.14", False),
                 ("0.14.0", "0.14.1", True),
                 ("0.14.1", "0.14.0", False),
                 ("0.9", "0.14", True),
                 ("1.0", "0.99.99", False),
                 ("0.99.99", "1", True))
        for installed, required, expected in cases:
            with self.subTest(installed=installed, required=required):
                self.assertEqual(below(parse(installed), parse(required)),
                                 expected)

    def test_the_installed_version_is_read_from_the_package(self):
        """Never hardcoded: the floor is compared against the package's own
        `TOOL_VERSION`, so a version bump cannot leave this check judging
        against a number someone typed into a test."""
        with self.assertRaises(config.ConfigError) as ctx:
            config.check_tool_version({"tool": {"requires": "5.0.0"}},
                                      "src", installed="4.9.9")
        self.assertIn("4.9.9", str(ctx.exception))
        config.check_tool_version({"tool": {"requires": "5.0.0"}},
                                  "src", installed="5.0.0")


class TestTheTwoKeysRuledOn20260903(unittest.TestCase):
    """`[roles] review_default` and `[roles] enforcement`.

    Both are declarations the tool resolves and one of them refuses on:
    `handoff` will not open a round on the remote's default branch when the
    repository declares that the approval rides a pull request. Everything
    else about the pathway is the agents' work with `gh`, outside a tool
    that touches no forge.
    """

    def test_the_grammar_admits_them(self):
        cfg = _read('[roles]\nreview_default = "off"\n'
                    'enforcement = "pr-approval"\n')
        self.assertEqual(cfg.roles["review_default"], "off")
        self.assertEqual(cfg.roles["enforcement"], "pr-approval")

    def test_absence_resolves_to_the_stated_defaults(self):
        cfg = _read("[roles]\nauthor = \"a\"\n")
        self.assertEqual(emit.resolve_review_default(cfg),
                         vocab.REVIEW_DEFAULT_ON)
        self.assertEqual(emit.resolve_enforcement(cfg),
                         vocab.ENFORCEMENT_NONE)

    def test_neither_key_carries_a_config_default(self):
        """Absence must stay observable, exactly as `transport`'s does: a
        default written into `DEFAULTS` would erase the difference between a
        repository that declared `on` and one that declared nothing, and the
        second is what `decide` reports."""
        for key in ("review_default", "enforcement"):
            self.assertNotIn(key, config.DEFAULTS["roles"], key)

    def test_a_value_outside_the_vocabulary_is_refused_by_the_resolver(self):
        for key, resolve in (("review_default", emit.resolve_review_default),
                             ("enforcement", emit.resolve_enforcement)):
            with self.subTest(key=key):
                cfg = _read(f'[roles]\n{key} = "sometimes"\n')
                with self.assertRaises(vocab.TransportDeclarationError) as ctx:
                    resolve(cfg)
                self.assertIn("sometimes", str(ctx.exception))
                self.assertTrue(ctx.exception.remedy)

    def test_an_explicitly_empty_declaration_is_not_an_omitted_one(self):
        for key, resolve in (("review_default", emit.resolve_review_default),
                             ("enforcement", emit.resolve_enforcement)):
            with self.subTest(key=key):
                cfg = _read(f'[roles]\n{key} = ""\n')
                with self.assertRaises(vocab.TransportDeclarationError):
                    resolve(cfg)


class TestTheEnforcementRefusal(unittest.TestCase):
    """`handoff` on the remote's default branch under `pr-approval`.

    The runner is injected, so every state is exercised without a network:
    the same shape `ensure_pushed`'s refusals use.
    """

    def _cfg(self, declared):
        return _read(f'[roles]\nenforcement = "{declared}"\n')

    def _git(self, branch="main", default="refs/remotes/origin/HEAD -> main",
             remotes="origin", symref=True, remote="origin", upstream="",
             remote_defaults=None):
        """`remote` is the destination `_resolve_push_destination` is
        expected to derive — the sole remote for the single-remote tests,
        or the branch's upstream remote for the F2 multi-remote tests.
        `upstream` is the raw `for-each-ref --format=...` answer: empty
        for none (the single-remote tests fall through to the sole
        remote), or `"<remote>\\trefs/heads/<branch>"` to declare one.
        `remote_defaults` maps a remote name to its default branch, for a
        second (decoy) remote the F2 tests need to prove is never
        consulted — `origin`'s default answers `symbolic-ref`/`ls-remote`
        by name now, not by being the only remote in the fixture.
        """
        defaults = dict(remote_defaults or {})
        defaults.setdefault(remote, default)

        def run(*args):
            if args == ("rev-parse", "--abbrev-ref", "HEAD"):
                return branch
            if args == ("remote",):
                return remotes
            if args[:1] == ("for-each-ref",) and args[-1:] == (
                    f"refs/heads/{branch}",):
                return upstream
            if len(args) == 2 and args[0] == "symbolic-ref":
                name = args[1].split("/")[2]
                if not symref or name not in defaults:
                    raise RuntimeError("no symbolic ref")
                return f"refs/remotes/{name}/{defaults[name]}"
            if (len(args) == 4 and args[0] == "ls-remote"
                    and args[1] == "--symref"):
                name = args[2]
                if name not in defaults:
                    raise RuntimeError("no such remote")
                return f"ref: refs/heads/{defaults[name]}\tHEAD\nabc\tHEAD"
            raise AssertionError(f"unexpected git call: {args}")
        return run

    def test_the_default_branch_refuses_with_a_remedy(self):
        with self.assertRaises(emit.EnforcementUnsatisfiable) as ctx:
            emit.check_enforcement(self._cfg("pr-approval"),
                                   git=self._git(branch="main",
                                                 default="main"))
        self.assertIn("default branch", str(ctx.exception))
        self.assertIn('enforcement = "none"', ctx.exception.remedy)

    def test_a_working_branch_passes(self):
        emit.check_enforcement(self._cfg("pr-approval"),
                               git=self._git(branch="work", default="main"))

    def test_the_symref_fallback_answers_when_the_clone_does_not(self):
        """A clone with no `refs/remotes/<remote>/HEAD` still gets a true
        answer: the remote itself is asked."""
        with self.assertRaises(emit.EnforcementUnsatisfiable):
            emit.check_enforcement(self._cfg("pr-approval"),
                                   git=self._git(branch="main",
                                                 default="main",
                                                 symref=False))

    def test_the_declaration_is_what_arms_it(self):
        """`none` — and silence — reach no git call at all: the refusal is
        the declared pathway's, never a policy the tool applies unasked."""
        def refuse(*args):
            raise AssertionError(f"git was consulted: {args}")
        emit.check_enforcement(self._cfg("none"), git=refuse)
        emit.check_enforcement(_read("[roles]\nauthor = \"a\"\n"),
                               git=refuse)

    def test_a_decoy_origin_is_not_the_remote_tested(self):
        """F2: `origin/develop` is not the current branch's destination —
        the branch tracks `publish/main`, and `publish` declares `main`
        as its own default. `ensure_pushed` would push to `publish`, so
        enforcement must refuse against `publish`'s default branch, never
        `origin`'s only because that name matches."""
        git = self._git(branch="main", default="main",
                        remotes="origin\npublish", remote="publish",
                        upstream="publish\trefs/heads/main",
                        remote_defaults={"origin": "develop"})
        with self.assertRaises(emit.EnforcementUnsatisfiable) as ctx:
            emit.check_enforcement(self._cfg("pr-approval"), git=git)
        self.assertIn("default branch", str(ctx.exception))
        self.assertIn("publish", str(ctx.exception))

    def test_a_working_branch_on_the_same_second_remote_passes(self):
        """The control: still tracking `publish`, whose default is still
        `main` — but this branch is not named `main`, so nothing is
        unsatisfiable."""
        git = self._git(branch="work", default="main",
                        remotes="origin\npublish", remote="publish",
                        upstream="publish\trefs/heads/work",
                        remote_defaults={"origin": "develop"})
        emit.check_enforcement(self._cfg("pr-approval"), git=git)

    def test_a_differently_named_local_branch_targeting_default_ref_refuses(self):
        """Round-2 F1: the right side of the refspec decides.

        Local ``work`` still lands directly on ``publish/main``.  Comparing
        the remote default to the local name would call this safe even though
        ``ensure_pushed`` will update the remote's default branch.
        """
        git = self._git(branch="work", default="main",
                        remotes="origin\npublish", remote="publish",
                        upstream="publish\trefs/heads/main",
                        remote_defaults={"origin": "develop"})
        with self.assertRaises(emit.EnforcementUnsatisfiable) as ctx:
            emit.check_enforcement(self._cfg("pr-approval"), git=git)
        self.assertIn("publish:refs/heads/main", str(ctx.exception))

    def test_a_destination_without_a_branch_identity_refuses(self):
        git = self._git(branch="work", default="main",
                        remotes="publish", remote="publish",
                        upstream="publish\trefs/tags/main")
        with self.assertRaisesRegex(RuntimeError,
                                    "branch identity cannot be derived"):
            emit.check_enforcement(self._cfg("pr-approval"), git=git)

    def test_a_slash_containing_default_refuses_via_the_cached_symref(self):
        """Round 3 F1: the cached ``refs/remotes/<remote>/HEAD`` reader
        must take the COMPLETE suffix after ``refs/remotes/<remote>/``,
        not its last slash component — else a default branch such as
        ``release/main`` reads back as bare ``main`` and a push straight
        to that default passes preflight.

        MUTATION: restoring ``ref.rsplit("/", 1)[-1]`` makes this pass
        incorrectly (it would compare ``main`` == ``main``, true by
        accident) — the assertion below is on the exact destination
        named in the refusal, not merely that some exception fires, so a
        wrong-but-still-truncated match cannot slip through undetected.
        """
        with self.assertRaises(emit.EnforcementUnsatisfiable) as ctx:
            emit.check_enforcement(self._cfg("pr-approval"),
                                   git=self._git(branch="release/main",
                                                 default="release/main",
                                                 symref=True))
        self.assertIn("release/main", str(ctx.exception))

    def test_a_slash_containing_default_refuses_via_the_symref_fallback(self):
        """The same state, forced through ``ls-remote --symref`` because
        the clone holds no cached ``refs/remotes/<remote>/HEAD`` — the
        other reader `check_enforcement` falls back to, and the one the
        finding names as the second truncating site."""
        with self.assertRaises(emit.EnforcementUnsatisfiable) as ctx:
            emit.check_enforcement(self._cfg("pr-approval"),
                                   git=self._git(branch="release/main",
                                                 default="release/main",
                                                 symref=False))
        self.assertIn("release/main", str(ctx.exception))

    def test_a_sibling_slash_branch_passes_both_readers(self):
        """The control: ``release/work`` is not ``release/main``, the
        remote's declared default — under either reader, this must not
        be mistaken for a push to the default branch."""
        emit.check_enforcement(self._cfg("pr-approval"),
                               git=self._git(branch="release/work",
                                             default="release/main",
                                             symref=True))
        emit.check_enforcement(self._cfg("pr-approval"),
                               git=self._git(branch="release/work",
                                             default="release/main",
                                             symref=False))


class TestTheEnforcementRefusalAtTheVerb(unittest.TestCase):
    """The refusal where it actually fires: `handoff`, a real repository, a
    real remote whose default branch is the one being reviewed.

    The class above drives `check_enforcement` with an injected runner, which
    proves the decision. This proves the WIRING — that the verb asks before
    it commits, pushes, runs a gate or emits anything, and that the refusal
    reaches the agent as a blocked exit with a remedy rather than as a
    traceback or a generic usage error.
    """

    def setUp(self):
        scratch = scratch_loop_repo(self, "enforcement-", objective="pathway")
        self.tmp, self.repo, self.cwd = scratch.tmp, scratch.repo, scratch.cwd
        self.base, self.claim = scratch.base, scratch.claim
        self.state = self.tmp / "state"
        bare = self.tmp / "origin.git"
        sh("git", "init", "-q", "--bare", str(bare))
        # The remote's own default branch, stated: `ls-remote --symref` is
        # the fallback this fixture exercises, and a bare repository
        # initialised with some other default would make the test pass for
        # the wrong reason.
        sh("git", "-C", str(bare), "symbolic-ref", "HEAD", "refs/heads/main")
        sh("git", "-C", str(self.repo), "remote", "add", "origin", str(bare))
        sh("git", "-C", str(self.repo), "push", "-q", "-u", "origin", "main")

    def _declare(self, line):
        path = self.repo / "review.toml"
        text = path.read_text(encoding="utf-8")
        key = line.split("=", 1)[0].strip()
        # The scratch config is this repository's own, which since
        # 2026-09-03 declares `enforcement` itself: replace a declared line
        # rather than duplicating the key into a TOML refusal.
        declared = re.compile(rf"^{re.escape(key)}\s*=.*$", re.M)
        if declared.search(text):
            text = declared.sub(line, text, count=1)
        else:
            text = text.replace("[roles]", f"[roles]\n{line}", 1)
        path.write_text(text, encoding="utf-8")
        sh("git", "-C", str(self.repo), "commit", "--allow-empty", "-qam", "declare")

    def _handoff(self):
        return run_cli(self.repo, self.state, "handoff", "--claim-file",
                       str(self.claim), "--base", self.base, cwd=self.cwd)

    def test_pr_approval_on_the_default_branch_refuses(self):
        self._declare('enforcement = "pr-approval"')
        code, rec = self._handoff()
        self.assertEqual(code, 1, rec)
        self.assertEqual(rec["next_kind"], "blocked")
        self.assertIn("default branch", rec["error"])
        self.assertIn('enforcement = "none"', rec["remedy"])
        # Nothing ran: the refusal precedes the ledger, the gates and the
        # push, so no round was opened by a verb that refused.
        self.assertFalse((self.state / "ledger.jsonl").exists(), rec)

    def test_the_same_repository_on_a_branch_opens_the_round(self):
        """The control. Only the branch differs, and the round proceeds —
        which is what makes the refusal above about the declared pathway
        rather than about the declaration existing."""
        self._declare('enforcement = "pr-approval"')
        sh("git", "-C", str(self.repo), "checkout", "-q", "-b", "work")
        code, rec = self._handoff()
        self.assertEqual(code, 0, rec)
        self.assertEqual(rec["round"], 1)

    def test_none_on_the_default_branch_opens_the_round(self):
        """The other control: the same branch, the other declaration."""
        self._declare('enforcement = "none"')
        code, rec = self._handoff()
        self.assertEqual(code, 0, rec)
        self.assertEqual(rec["round"], 1)

    def _add_decoy_and_publish(self, publish_branch, destination_branch=None,
                               remote_default=None):
        """`origin` (from `setUp`) is repointed to a default that is NOT
        the branch under test, and a second bare remote `publish` is
        added whose default IS `main` — the branch's real destination
        once its upstream is pushed there. F2's live reproduction: a
        remote named `origin` existing is not evidence about where the
        push actually lands. `remote_default` overrides `publish`'s own
        default branch (round-3 F1: a slash-containing name such as
        `release/main`, to prove the real destination reader does not
        truncate it)."""
        origin_bare = self.tmp / "origin.git"
        sh("git", "-C", str(origin_bare), "symbolic-ref", "HEAD",
           "refs/heads/develop")
        publish_bare = self.tmp / "publish.git"
        sh("git", "init", "-q", "--bare", str(publish_bare))
        sh("git", "-C", str(publish_bare), "symbolic-ref", "HEAD",
           f"refs/heads/{remote_default or 'main'}")
        sh("git", "-C", str(self.repo), "remote", "add", "publish",
           str(publish_bare))
        destination_branch = destination_branch or publish_branch
        sh("git", "-C", str(self.repo), "push", "-q", "-u", "publish",
           f"{publish_branch}:refs/heads/{destination_branch}")

    def test_pr_approval_refuses_by_the_branchs_real_destination(self):
        """F2: `origin/develop` is a decoy, not the current branch's
        destination — the branch (`main`) tracks `publish/main`, and
        `publish` declares `main` as ITS default. `check_enforcement`
        must refuse against `publish`'s default branch, and the real
        `handoff` must refuse before anything is committed, pushed,
        gated or recorded — restoring the plain `else "origin"`
        selection would test `origin`'s `develop` default instead, find
        no match, and pass this state incorrectly."""
        self._declare('enforcement = "pr-approval"')
        self._add_decoy_and_publish("main")
        code, rec = self._handoff()
        self.assertEqual(code, 1, rec)
        self.assertEqual(rec["next_kind"], "blocked")
        self.assertIn("default branch", rec["error"])
        self.assertFalse((self.state / "ledger.jsonl").exists(), rec)

    def test_a_working_branch_tracking_the_same_publish_remote_passes(self):
        """The control: still tracking `publish`, whose default is still
        `main`, but this branch is `work` — not `publish`'s default — so
        the round proceeds."""
        self._declare('enforcement = "pr-approval"')
        sh("git", "-C", str(self.repo), "checkout", "-q", "-b", "work")
        self._add_decoy_and_publish("work")
        code, rec = self._handoff()
        self.assertEqual(code, 0, rec)
        self.assertEqual(rec["round"], 1)

    def test_local_work_targeting_publish_main_refuses_before_mutation(self):
        """Round-2 F1's real-verb reproducer and side-effect boundary."""
        self._declare('enforcement = "pr-approval"')
        sh("git", "-C", str(self.repo), "checkout", "-q", "-b", "work")
        self._add_decoy_and_publish("work", "main")
        before_head = git_out(self.repo, "rev-parse", "HEAD")
        before_remote = git_out(self.repo, "ls-remote", "publish",
                                "refs/heads/main")
        (self.repo / "f.txt").write_text("dirty but uncommitted\n",
                                         encoding="utf-8")

        code, rec = self._handoff()

        self.assertEqual(code, 1, rec)
        self.assertEqual(rec["next_kind"], "blocked")
        self.assertIn("publish:refs/heads/main", rec["error"])
        self.assertEqual(git_out(self.repo, "rev-parse", "HEAD"), before_head)
        self.assertEqual(git_out(self.repo, "ls-remote", "publish",
                                 "refs/heads/main"), before_remote)
        self.assertFalse((self.state / "ledger.jsonl").exists(), rec)

    def test_a_slash_containing_default_refuses_before_mutation(self):
        """Round-3 F1's real-verb reproducer. `publish`'s own default
        branch is `release/main`, not `main`; local `work` pushes
        straight onto it via `publish:refs/heads/release/main`. Neither
        reader `check_enforcement` consults holds a cached
        `refs/remotes/publish/HEAD` here (the remote was added by hand,
        never cloned), so this exercises the `ls-remote --symref`
        fallback specifically — the injected-runner tests above cover
        the cached-symref reader for the same slash-containing name.

        MUTATION: truncating either reader back to its last path
        component reads this default as bare `main`, `release/main` !=
        `main`, and the push proceeds — reintroducing either
        `rsplit("/", 1)[-1]` makes this test fail.
        """
        self._declare('enforcement = "pr-approval"')
        sh("git", "-C", str(self.repo), "checkout", "-q", "-b", "work")
        self._add_decoy_and_publish("work", "release/main",
                                    remote_default="release/main")
        before_head = git_out(self.repo, "rev-parse", "HEAD")
        before_remote = git_out(self.repo, "ls-remote", "publish",
                                "refs/heads/release/main")

        code, rec = self._handoff()

        self.assertEqual(code, 1, rec)
        self.assertEqual(rec["next_kind"], "blocked")
        self.assertIn("publish:refs/heads/release/main", rec["error"])
        self.assertEqual(git_out(self.repo, "rev-parse", "HEAD"), before_head)
        self.assertEqual(git_out(self.repo, "ls-remote", "publish",
                                 "refs/heads/release/main"), before_remote)
        self.assertFalse((self.state / "ledger.jsonl").exists(), rec)

    def test_a_sibling_slash_branch_opens_the_round(self):
        """The control: `publish`'s default is still `release/main`, but
        this push lands on the sibling `release/work` — not the default
        — so the round proceeds."""
        self._declare('enforcement = "pr-approval"')
        sh("git", "-C", str(self.repo), "checkout", "-q", "-b", "work")
        self._add_decoy_and_publish("work", "release/work",
                                    remote_default="release/main")
        code, rec = self._handoff()
        self.assertEqual(code, 0, rec)
        self.assertEqual(rec["round"], 1)


class TestTheDecideField(unittest.TestCase):
    """What a repository never declared, reported rather than applied in
    silence (brief `config-absent-asks-once`)."""

    def _keys(self, entries):
        return [e["key"] for e in entries]

    def test_a_repository_that_declares_nothing_is_asked_about_every_key(
            self):
        entries = _read("[roles]\nauthor = \"a\"\n").decisions()
        self.assertEqual(self._keys(entries),
                         [key for key, *_ in vocab.DECIDE_KEYS])

    def test_every_entry_carries_the_five_fields(self):
        for entry in _read("[roles]\nauthor = \"a\"\n").decisions():
            with self.subTest(key=entry["key"]):
                self.assertEqual(sorted(entry),
                                 ["applied", "key", "meaning", "set",
                                  "unset"])
                self.assertTrue(entry["meaning"].strip())
                self.assertTrue(entry["set"].startswith(
                    entry["key"].split(".")[-1] + " = "))

    def test_a_declared_key_produces_no_entry(self):
        """THE control: declaring the key is what ends the asking, and it
        ends it for that key alone."""
        entries = _read('[roles]\ntransport = "paste"\ndebug = true\n'
                        'review_default = "off"\n'
                        'enforcement = "none"\n'
                        '[limits]\nround_cap = 5\ntoken_budget = 10\n'
                        ).decisions()
        self.assertEqual(entries, [])
        one = _read('[roles]\ntransport = "paste"\n').decisions()
        self.assertNotIn(vocab.DECIDE_TRANSPORT, self._keys(one))
        self.assertIn(vocab.DECIDE_DEBUG, self._keys(one))

    def test_the_lines_are_the_exact_toml_a_person_writes(self):
        entries = {e["key"]: e
                   for e in _read("[roles]\nauthor = \"a\"\n").decisions()}
        self.assertEqual(entries[vocab.DECIDE_DEBUG]["set"], "debug = true")
        self.assertEqual(entries[vocab.DECIDE_DEBUG]["unset"],
                         "debug = false")
        self.assertEqual(entries[vocab.DECIDE_ENFORCEMENT]["set"],
                         'enforcement = "pr-approval"')
        self.assertEqual(entries[vocab.DECIDE_ENFORCEMENT]["unset"],
                         'enforcement = "none"')
        self.assertEqual(entries[vocab.DECIDE_REVIEW_DEFAULT]["set"],
                         'review_default = "on"')
        # A key with no off state says so with null rather than inventing
        # a line that declares nothing.
        self.assertIsNone(entries[vocab.DECIDE_TRANSPORT]["unset"])
        self.assertIsNone(entries[vocab.DECIDE_ROUND_CAP]["unset"])

    def test_the_applied_value_is_what_was_used(self):
        entries = {e["key"]: e for e in
                   _read("[roles]\nauthor = \"a\"\n").decisions(
                       {vocab.DECIDE_TRANSPORT: "paste"})}
        self.assertEqual(entries[vocab.DECIDE_TRANSPORT]["applied"], "paste")
        # The round cap's built-in value comes from the config layer, which
        # owns it — not from a second copy in the vocabulary table.
        self.assertEqual(entries[vocab.DECIDE_ROUND_CAP]["applied"],
                         config.DEFAULTS["limits"]["round_cap"])
        self.assertEqual(entries[vocab.DECIDE_ROUND_CAP]["set"],
                         f"round_cap = {config.DEFAULTS['limits']['round_cap']}")
        # A budget nobody declared is uncounted, and null is that state.
        self.assertIsNone(entries[vocab.DECIDE_TOKEN_BUDGET]["applied"])

    def test_the_refusal_keys_are_not_in_the_table(self):
        """Taxonomy, the roles and the config file itself refuse when
        absent (design §2). They are rulings, not defaults, so a `decide`
        entry about them would ask for a decision already forced."""
        keys = [key for key, *_ in vocab.DECIDE_KEYS]
        for absent in ("taxonomy.severities", "roles.author",
                       "roles.reviewer", "taxonomy.classifications"):
            self.assertNotIn(absent, keys)


class TestF5UndeclaredTaxonomyRefusesToRule(unittest.TestCase):
    """Round-3 F5 — FALSIFICATION: With review.toml absent and no explicit
    user configuration, emission and validation refuse to rule."""

    # A real directory that is not a git repo and holds no review.toml, so
    # the bare-config path is exercised without creating anything. A temp dir
    # would work too, but a reviewer sandboxed read-only cannot make one —
    # which is the whole point of round-3 F9.
    NO_CONFIG_DIR = Path("/usr")

    def _bare_config(self):
        return config.load(self.NO_CONFIG_DIR)

    def test_builtin_defaults_no_longer_restate_repo_taxonomy(self):
        cfg = self._bare_config()
        self.assertEqual(cfg.severities, [])
        self.assertEqual(cfg.classifications, [])
        self.assertFalse(cfg.taxonomy_declared)

    def test_validation_refuses(self):
        cfg = self._bare_config()
        v = wire.parse_verdict(
            '<loupe-review-verdict sha="' + "9" * 40 + '">\n'
            'VERDICT: clean to advance\n\n## findings\n\nNone\n'
            '</loupe-review-verdict>')
        codes = {i.code for i in validate.validate_verdict(v, cfg)}
        self.assertEqual(codes, {"T-UNDECLARED"})

    def test_emission_refuses(self):
        cfg = self._bare_config()
        with self.assertRaises(RuntimeError) as ctx:
            emit_request(cfg, Ledger.in_memory(), {}, base="HEAD", head="HEAD")
        self.assertIn("no taxonomy declared", str(ctx.exception))

    def test_declared_taxonomy_still_rules(self):
        self.assertTrue(CFG.taxonomy_declared)


if __name__ == "__main__":
    unittest.main()

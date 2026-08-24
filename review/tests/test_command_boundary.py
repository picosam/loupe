"""The command boundary, enforced rather than described (workshop (a), (b)).

Lineage 5 closed at round 6 with two Medium findings parked: the rendered-
command marker was not runtime-enforced against future source drift
(`fp2:03ee76f0f1c6df89`), and the executable output policy was enforced by
selected callers rather than one complete egress (`fp2:2bf8915bfd9105b5`).
Neither was a live defect — every production site rendered its commands and
every result carried a rendered one. Both were claims wider than their proof,
which is the same shape of defect the lineage spent three rounds closing in
the commands themselves.

What changed, and why the tests below are the shape they are:

(a) `Lit`'s stated invariant was "may only ever wrap a literal this package's
    source contains, which the inventory checks statically". A runtime cannot
    observe whether a value was a source literal, and the inventory did not
    check it either — it matched four exact spellings of the CALL, so every
    route to the same constructor that spelled it differently passed. The
    invariant moved off provenance and onto the VALUE: a `Lit` renders
    unquoted, so it must be shell-inert, an operator this package writes, or
    a `<placeholder>` a person fills in. That is checkable, and it is checked
    where the value is built — which is why every route below fails
    identically. Route-independence IS the fix; the tests prove it rather
    than enumerating spellings a scan would have to keep up with.

(b) The policy had six doors. `_out` is now the one the structured channel
    leaves by, and the inventory below is CLOSED: every payload key is
    classified runnable or prose, and an unclassified key fails.
"""
from __future__ import annotations

import ast
import contextlib
import io
import unittest
from pathlib import Path

from review import TOOL_NAME, brief, cli, config, paths, transport, wire
from review.tests.util import REPO_ROOT

HOSTILE = "x; rm -rf /"


def _silently(fn, *a, **kw):
    """Run something that prints, keeping the test output readable."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


# --------------------------------------------------------------- (a) routes

class TestEveryRouteToLitIsTheSameDoor(unittest.TestCase):
    """The eight forms round 6 probed, plus three the probe did not name.

    Each was verified admitting `HOSTILE` before the guard landed: the probe
    at `6c7ff7e` rendered `loupe x; rm -rf /` for every one of them.
    """

    def _refused(self, label, fn):
        with self.assertRaises((ValueError, TypeError), msg=label):
            fn()

    def test_direct(self):
        self._refused("Lit(x)", lambda: paths.Lit(HOSTILE))

    def test_aliased_import(self):
        L = paths.Lit                      # `from review.paths import Lit as L`
        self._refused("L(x)", lambda: L(HOSTILE))

    def test_fully_qualified(self):
        import review.paths
        self._refused("review.paths.Lit(x)",
                      lambda: review.paths.Lit(HOSTILE))

    def test_renamed_reexport(self):
        Word = paths.Lit
        self._refused("Word(x)", lambda: Word(HOSTILE))

    def test_subclassed(self):
        # The sharpest of the eight: `command()` admits any `isinstance(w,
        # Lit)`, so a subclass was a key to the door rather than a use of it.
        # Refused at CLASS CREATION, so no instance can exist to be rendered.
        with self.assertRaises(TypeError):
            class MyLit(paths.Lit):
                pass

    def test_command_subclassed(self):
        with self.assertRaises(TypeError):
            class MyCmd(paths.Command):
                pass

    def test_indirect_call_through_a_variable(self):
        def build(make, value):
            return make(value)
        self._refused("build(Lit, x)", lambda: build(paths.Lit, HOSTILE))

    def test_getattr_dispatch(self):
        self._refused("getattr(paths,'Lit')(x)",
                      lambda: getattr(paths, "Lit")(HOSTILE))

    def test_mapping_dispatch(self):
        table = {"lit": paths.Lit}
        self._refused("table['lit'](x)", lambda: table["lit"](HOSTILE))

    def test_shadowed_constant(self):
        # A module rebinding TOOL_NAME turned all 18 `Lit(TOOL_NAME)` sites
        # into injection sites at once. The value is checked, so the name it
        # arrived under is not load-bearing.
        shadowed = HOSTILE
        self._refused("Lit(shadowed TOOL_NAME)", lambda: paths.Lit(shadowed))

    def test_keyword_construction(self):
        # Not among the eight, and the cheapest of them all: the scan read
        # `node.args` and never `node.keywords`, while `str` accepts
        # `object=`. Verified building a genuine unquoted Lit before the fix.
        self._refused("Lit(object=x)", lambda: paths.Lit(object=HOSTILE))

    def test_direct_command_construction(self):
        # A one-line promotion of any string to "rendered", needing no
        # import and no obfuscation, invisible to a scan that knew only
        # `Lit` and three program names.
        self._refused("Command(x)", lambda: paths.Command(f"loupe take {HOSTILE}"))


class TestTheControlsStillRender(unittest.TestCase):
    """Paired controls: the guard must not have closed the legitimate forms.

    A guard that refuses everything passes every hostile test above and is
    worthless, so each class of real word is asserted admitted.
    """

    def _constructor_literals(self, *names) -> set:
        """Every string literal the production source hands the named
        constructors — read from the source rather than restated, so a new
        word the grammar would refuse fails HERE, at the moment it is
        written, rather than in whatever command it was destined for."""
        found = set()
        for path in sorted((REPO_ROOT / "review").rglob("*.py")):
            if "tests" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (func.attr if isinstance(func, ast.Attribute)
                        else getattr(func, "id", ""))
                if name in names:
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(
                                arg.value, str):
                            found.add(arg.value)
        return found

    def test_every_literal_the_package_actually_uses_is_admitted(self):
        found = self._constructor_literals("Lit", "lits")
        self.assertGreater(len(found), 30, "the scan found no literals")
        for word in sorted(found):
            with self.subTest(word=word):
                self.assertEqual(str(paths.Lit(word)), word)

    def test_every_placeholder_the_package_actually_uses_is_admitted(self):
        found = self._constructor_literals("Ph")
        self.assertGreater(len(found), 5, "the scan found no placeholders")
        for word in sorted(found):
            with self.subTest(word=word):
                self.assertEqual(str(paths.Ph(word)), word)

    def test_every_quoted_placeholder_body_is_admitted(self):
        found = self._constructor_literals("qph")
        self.assertTrue(found, "the scan found no qph bodies")
        for body in sorted(found):
            with self.subTest(body=body):
                self.assertEqual(str(paths.qph(body)), f'"{body}"')

    def test_the_operators_the_tool_writes(self):
        # The two intentional operators are their own type; the inert word
        # grammar refuses them, so neither can be smuggled in as a Lit.
        self.assertEqual(str(paths.Op("&&")), "&&")
        self.assertEqual(str(paths.Op("<")), "<")
        for word in ("&&", "<"):
            with self.subTest(word=word):
                with self.assertRaises(ValueError):
                    paths.Lit(word)
        # And nothing else can become one: membership is the whole grammar.
        for word in (";", "|", ">", "&", "||", "<<", HOSTILE):
            with self.subTest(word=word):
                with self.assertRaises(ValueError):
                    paths.Op(word)

    def test_placeholders_a_person_fills_in(self):
        # The shapes the package writes, each admitted by its constructor
        # and refused by the inert word grammar — the types cannot cross.
        for word in ("<verdict.md>", "<sha>:<path>", "<the reviewer's file>",
                     "<your id>", "<the envelope>"):
            with self.subTest(word=word):
                self.assertEqual(str(paths.Ph(word)), word)
                with self.assertRaises(ValueError):
                    paths.Lit(word)
        self.assertEqual(str(paths.qph("<why>")), '"<why>"')
        self.assertEqual(str(paths.qph("...")), '"..."')
        self.assertEqual(
            str(paths.opt(paths.Lit("--base"), paths.Ph("<sha>"))),
            "[--base <sha>]")

    def test_a_placeholder_may_not_smuggle_shell_syntax(self):
        # The placeholder form is a hole in an otherwise inert grammar, so
        # its inner class is closed: no substitution, separator, redirect,
        # unbalanced quote or control character can be spelled.
        for word in ("<x>; rm -rf /", "<a> && rm", "<a>|sh", "<a><b>;rm",
                     "<disposition.md>;", "[--base", "<sha>]",
                     "<$(printf /dev/null)>", "<`id`>", '<a">', "<a\nb>",
                     'word"', "safe;", '"unclosed', '"<why>";'):
            with self.subTest(word=word):
                with self.assertRaises(ValueError):
                    paths.Ph(word)
                with self.assertRaises(ValueError):
                    paths.Lit(word)

    def test_a_dynamic_word_still_renders_quoted_through_command(self):
        # The point of refusing `Lit(dynamic)` is that `command()` already
        # has a correct home for a dynamic word: bare, and quoted as one.
        rendered = paths.command(*paths.lits(TOOL_NAME, "take"), HOSTILE)
        self.assertEqual(str(rendered), f"{TOOL_NAME} take 'x; rm -rf /'")
        self.assertEqual(paths.executable(rendered, "control"), rendered)

    def test_a_proved_token_is_admitted(self):
        self.assertEqual(str(paths.token("close")), "close")
        with self.assertRaises(ValueError):
            paths.token(HOSTILE)

    def test_the_renderer_still_mints_a_command(self):
        self.assertIsInstance(paths.command(paths.Lit("git")), paths.Command)
        self.assertIsInstance(paths.diff_command("/r", "a" * 40, "b" * 40),
                              paths.Command)


class TestTheExecutableDoorChecksTypes(unittest.TestCase):

    def test_the_empty_string_exception_is_a_type_test_not_an_equality_test(self):
        """A door checking types had one branch asking the value's opinion.

        `value == ""` let anything whose `__eq__` answers True to the empty
        string through — and `executable` RETURNS its argument, so the
        caller then wrote it into the field an agent runs.
        """
        class AlwaysEqual(str):
            def __eq__(self, other):
                return True

            def __hash__(self):
                return 0

        with self.assertRaises(TypeError):
            paths.executable(AlwaysEqual(HOSTILE), "probe")
        # Control: the real empty string is still the tool's "no command".
        self.assertEqual(paths.executable("", "probe"), "")

    def test_a_bare_string_is_refused(self):
        with self.assertRaises(TypeError):
            paths.executable(f"{TOOL_NAME} take {HOSTILE}", "probe")


class TestTheWordGrammarIsClosed(unittest.TestCase):
    """Lineage 6 round 1, F1 — the closed-world regression.

    The round-1 probe crossed two doors on d84f10c: `Lit("A;")` rendered
    `printf A; printf B`, which Bash ran as two commands, and a base-
    constructor-forged Command satisfied `executable()`. The boundary is
    now structural — one grammar per word type, a construction record on
    the rendered value — so both cross nowhere, and the controls prove the
    doors still open for every legitimate shape. Reintroducing either
    bypass (the permissive literal grammar, or an executable door that
    checks the type alone) must fail this class.
    """

    def test_the_round_1_semicolon_literal_cannot_render_two_commands(self):
        # The exact first half of the round-1 falsification probe: refused
        # at construction, so nothing reaches a shell at all.
        with self.assertRaises(ValueError):
            paths.command(paths.Lit("printf"), paths.Lit("A;"),
                          paths.Lit("printf"), paths.Lit("B"))

    def test_the_round_1_forged_command_is_refused_at_the_door(self):
        # The exact second half: `str.__new__` still constructs the type —
        # a str subclass cannot seal its base — but it constructs one
        # without the record `command()` writes, and the door checks the
        # record, not the type alone.
        forged = getattr(str, "__new__")(
            paths.Command, f"{TOOL_NAME} validate x; printf forged")
        with self.assertRaises(TypeError):
            paths.executable(forged, "probe")

    def test_the_other_round_1_literal_shapes_are_refused(self):
        # The three residues the probe named beside the semicolon: an
        # unmatched quote, a substitution inside angle brackets, a newline
        # inside a placeholder.
        for word in ('word"', "<$(printf /dev/null)>", "<a\nb>"):
            with self.subTest(word=word):
                with self.assertRaises(ValueError):
                    paths.Lit(word)
                with self.assertRaises(ValueError):
                    paths.Ph(word)

    def test_a_placeholder_template_cannot_satisfy_an_executable_door(self):
        template = paths.command(*paths.lits(TOOL_NAME, "close", "--verdict"),
                                 paths.Ph("<verdict.md>"))
        self.assertIsInstance(template, paths.Template)
        with self.assertRaises(TypeError):
            paths.executable(template, "probe")

    def test_a_command_and_a_template_may_not_nest(self):
        rendered = paths.command(*paths.lits(TOOL_NAME, "brief"))
        with self.assertRaises(TypeError):
            paths.command(paths.Lit("bash"), paths.Lit("-c"), rendered)

    def test_a_dynamic_operator_stays_an_inert_argument(self):
        # Arbitrary values cannot acquire operator semantics: a dynamic
        # `&&` is quoted into one argument, not spliced between commands.
        rendered = paths.command(*paths.lits(TOOL_NAME, "take"), "&&")
        self.assertIsInstance(rendered, paths.Command)
        self.assertEqual(str(rendered), f"{TOOL_NAME} take '&&'")

    def test_a_dynamic_word_may_not_carry_a_line_terminator(self):
        for hostile in ("a\nb", "a\rb", "a\x00b", "a\x1bb"):
            with self.subTest(word=hostile):
                with self.assertRaises(ValueError):
                    paths.command(*paths.lits(TOOL_NAME, "take"), hostile)

    def test_the_controls_still_render(self):
        # Paired controls: every legitimate word class still opens its
        # door — a grammar that refuses everything passes every hostile
        # case above and proves nothing.
        safe = paths.command(*paths.lits(TOOL_NAME, "validate"),
                             "/tmp/a b.md")
        self.assertIsInstance(safe, paths.Command)
        self.assertEqual(paths.executable(safe, "control"), safe)
        chained = paths.command(*paths.lits("git", "fetch"), "origin",
                                paths.Op("&&"), *paths.lits("git", "log"))
        self.assertIsInstance(chained, paths.Command)
        self.assertEqual(paths.executable(chained, "control"), chained)
        redirected = paths.command(*paths.lits(TOOL_NAME, "take", "-"),
                                   paths.Op("<"), paths.Ph("<the envelope>"))
        self.assertIsInstance(redirected, paths.Template)


# ------------------------------------------------------ (b) the one egress

# The result schema lives at the egress it governs — `cli.RUNNABLE_KEYS`
# and `cli.PROSE_KEYS`, enforced by `_out` on every payload at runtime
# (R1-F3). These tests derive from that schema rather than restating it:
# a second copy here would be the two-sources-for-one-fact defect the
# egress exists to end.


class TestTheOutputInventoryIsClosed(unittest.TestCase):
    """R1-F3: the inventory is closed AT THE EGRESS, not by a source scan.

    The old proof was an AST walk over dict literals in three modules. It
    missed subscript assignments, updates, merges, dynamic keys and any
    result producer elsewhere, so eight keys the egress already emitted —
    `cached`, `claim_digest`, `config`, `ignored_control_fields`,
    `lineage`, `note`, `remedy`, `tokens` — were invisible to the claimed
    closure. No lexical walk can prove every way arbitrary Python builds a
    dict; the egress can, because every payload passes through `_out` at
    runtime. So the regression below mutates the SEMANTIC schema — known
    runnable, known prose, null, unknown — across construction routes,
    instead of chasing dict spellings.
    """

    def test_every_top_level_construction_route_is_classified(self):
        """However the dict was built, an unknown key is refused at _out.

        One unknown key, six construction routes — a literal, a subscript
        assignment, update(), a **-merge, a helper return, a dynamic key —
        and the refusal is identical for each, because the door reads the
        RESULT, not the source that produced it. The routes are the ones
        the round-1 evidence listed as invisible to the old scan.
        """
        def helper_from_another_module():
            # Stands in for a producer in any fourth module: _out cannot
            # see where a dict came from, which is the point.
            return {"ok": True, "unheard_of": 1}

        def routes():
            yield "literal", {"ok": True, "unheard_of": 1}
            d = {"ok": True}
            d["unheard_of"] = 1
            yield "subscript", d
            d2 = {"ok": True}
            d2.update(unheard_of=1)
            yield "update", d2
            yield "unpack", {**{"ok": True}, **{"unheard_of": 1}}
            yield "helper-return", helper_from_another_module()
            key = "".join(["unheard", "_", "of"])
            yield "dynamic-key", {"ok": True, key: 1}

        for route, payload in routes():
            with self.subTest(route=route):
                with self.assertRaises(TypeError):
                    _silently(cli._out, payload)

    def test_the_round_1_omissions_are_now_classified(self):
        # The eight keys the round-1 probe found the old scan blind to,
        # each a real current output: they pass the door as prose.
        omissions = ("cached", "claim_digest", "config",
                     "ignored_control_fields", "lineage", "note", "remedy",
                     "tokens")
        for key in omissions:
            self.assertIn(key, cli.PROSE_KEYS, key)
        _silently(cli._out, {"ok": True, **{k: "x" for k in omissions}})

    def test_no_key_is_in_both_halves(self):
        self.assertEqual(set(cli.RUNNABLE_KEYS) & set(cli.PROSE_KEYS), set())

    def _keys_in(self, *modules) -> set:
        """ADVISORY drift evidence, not the closure (R1-F3).

        A lexical inventory of dict-literal keys and constant-key subscript
        assignments in the named modules. It cannot see every construction
        route — that is why the enforced schema is at `_out` — but a key it
        finds that the schema does not classify is cheap early evidence of
        drift, caught at authoring time instead of first execution.
        """
        found = set()
        for name in modules:
            tree = ast.parse((REPO_ROOT / "review" / name).read_text(
                encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Dict):
                    for key in node.keys:
                        if isinstance(key, ast.Constant) and isinstance(
                                key.value, str):
                            found.add(key.value)
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (isinstance(target, ast.Subscript)
                                and isinstance(target.slice, ast.Constant)
                                and isinstance(target.slice.value, str)):
                            found.add(target.slice.value)
        return found

    def test_the_advisory_scan_finds_no_unclassified_key(self):
        classified = set(cli.RUNNABLE_KEYS) | set(cli.PROSE_KEYS)
        found = self._keys_in("cli.py", "transport.py", "config.py")
        unclassified = sorted(found - classified)
        self.assertEqual(unclassified, [],
                         f"key(s) the advisory scan sees and the egress "
                         f"schema does not classify: {unclassified} — "
                         f"declare each in cli.RUNNABLE_KEYS or "
                         f"cli.PROSE_KEYS")


class TestTheDoorEveryStructuredResultLeavesBy(unittest.TestCase):
    """`_out` — schema-derived mutations, with the three controls."""

    def test_a_bare_string_in_any_runnable_key_is_refused(self):
        for key in cli.RUNNABLE_KEYS:
            with self.subTest(key=key):
                with self.assertRaises(TypeError):
                    _silently(cli._out, {"ok": False, key: f"loupe {HOSTILE}"})

    def test_a_rendered_command_passes(self):
        rendered = paths.command(*paths.lits(TOOL_NAME, "handoff"))
        for key in cli.RUNNABLE_KEYS:
            with self.subTest(key=key):
                _silently(cli._out, {"ok": True, key: rendered})

    def test_null_passes(self):
        for key in cli.RUNNABLE_KEYS:
            with self.subTest(key=key):
                _silently(cli._out, {"ok": True, key: None})

    def test_prose_keys_are_not_checked(self):
        # The control that keeps the door narrow: a remedy is a sentence and
        # must stay one.
        _silently(cli._out, {"ok": False, "remedy": "a person must edit the file",
                             "then": "hand it back", "author_next": "STOP"})


class TestFinishIsGuardedLikeBlocked(unittest.TestCase):
    """`fp2:2bf8915bfd9105b5`, the named half.

    Verified before the fix: `_finish([error], "loupe take x; rm -rf /")`
    emitted `next_kind: command` with the bare string as `next`.
    """

    def _error(self):
        from review.validate import Item
        return [Item("error", "X-TEST", "something")]

    def test_a_bare_shell_active_string_is_refused(self):
        with self.assertRaises(TypeError):
            _silently(cli._finish, self._error(), f"{TOOL_NAME} take {HOSTILE}")

    def test_a_rendered_command_is_emitted_as_a_command(self):
        rendered = paths.command(*paths.lits(TOOL_NAME, "validate"),
                                 "/tmp/a b.md")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli._finish(self._error(), rendered)
        self.assertEqual(code, cli.EXIT_FINDINGS)
        self.assertIn('"next_kind": "command"', out.getvalue())

    def test_the_blocked_state_still_takes_no_command(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli._finish(self._error(), "", remedy="a person must fix it")
        self.assertIn('"next_kind": "blocked"', out.getvalue())


class TestTheRemainingSurfaces(unittest.TestCase):

    def test_config_error_is_guarded_like_refusal(self):
        # The asymmetry: `Refusal` has guarded at construction since round 5
        # F1; its exact counterpart stored the same field raw.
        with self.assertRaises(TypeError):
            config.ConfigError("why", f"{TOOL_NAME} take {HOSTILE}")
        with self.assertRaises(TypeError):
            transport.Refusal("why", f"{TOOL_NAME} take {HOSTILE}")
        rendered = paths.command(*paths.lits(TOOL_NAME, "handoff"))
        self.assertEqual(config.ConfigError("why", rendered).next_cmd, rendered)
        self.assertEqual(config.ConfigError("why", "").kind, "blocked")

    def test_the_envelope_stamp_lines_have_a_door(self):
        """`paths.py` and the inventory both NAMED these as typed fields.

        They were not: a `Command` erodes to `str` under f-string and `+`, so
        the type was gone before either line existed. The claim was live and
        false; the door is what makes it true.
        """
        with self.assertRaises(TypeError):
            wire.executable_stamp("Verify", f"git fetch {HOSTILE}")
        rendered = paths.diff_command("/r", "a" * 40, "b" * 40)
        self.assertEqual(wire.executable_stamp("Diff", rendered),
                         f"Diff:   {rendered}")
        self.assertEqual(wire.executable_stamp("Verify", rendered),
                         f"Verify: {rendered}")

    def test_a_comment_may_not_carry_a_newline(self):
        """Workshop (c): a comment is inert only while it stays one line.

        A `#` line ends at the newline, so a comment carrying one would end
        there and hand whatever followed to the shell as a live command —
        inside the one block the tool tells a person to paste and run.
        """
        for hostile in (f"note\n{HOSTILE}", f"note\r{HOSTILE}"):
            with self.subTest(hostile=hostile):
                with self.assertRaises(ValueError):
                    paths.comment(hostile)
        # Control: an ordinary one-line note, and it renders as a no-op that
        # the fence door accepts because it is a rendered Command.
        note = paths.comment("for the author's session")
        self.assertEqual(str(note), "# for the author's session")
        self.assertEqual(brief._fence(note)[1], "# for the author's session")

    def test_a_fenced_relay_line_is_still_a_door(self):
        rendered = paths.command(*paths.lits(TOOL_NAME, "brief"))
        self.assertEqual(brief._fence(rendered)[1], f"{TOOL_NAME} brief")
        with self.assertRaises(TypeError):
            brief._fence(f"{TOOL_NAME} take {HOSTILE}")

    def test_the_envelope_paste_fence_is_deliberately_exempt(self):
        # Classified prose, not guarded: `lang=""` carries BYTES, and the
        # bytes are whatever the envelope says.
        self.assertEqual(brief._fence("not a command", lang="")[1],
                         "not a command")


class TestDriftExitsThroughTheBoundary(unittest.TestCase):

    def test_the_type_error_is_caught_by_main(self):
        """The door's own failure must not be the crash it exists to prevent.

        `paths.executable` raises `TypeError`; every CLI catch clause listed
        `(RuntimeError, ValueError, OSError, KeyError)`. So a future drift at
        any guarded door escaped `main()` as a traceback — the exact defect
        class round 1 F8 closed for config failures.
        """
        source = (REPO_ROOT / "review" / "cli.py").read_text(encoding="utf-8")
        handlers = [n for n in ast.walk(ast.parse(source))
                    if isinstance(n, ast.ExceptHandler)
                    and isinstance(n.type, ast.Tuple)
                    and any(getattr(e, "id", "") == "RuntimeError"
                            for e in n.type.elts)]
        self.assertTrue(handlers, "the structured catch clauses moved")
        for handler in handlers:
            names = {getattr(e, "id", "") for e in handler.type.elts}
            self.assertIn("TypeError", names,
                          f"line {handler.lineno}: a guarded door's TypeError "
                          f"would escape as a traceback")


class TestTheDetectorHoldsTheResidue(unittest.TestCase):
    """What the runtime guard cannot close, the scan must.

    The two layers are now explicitly divided: the TYPE checks the value at
    construction, and the SCAN holds the routes that get past construction
    itself. Before this, the scan claimed both and held neither cleanly.
    """

    def _scan(self, body: str, name: str = "synthetic.py"):
        import tempfile
        from review.tests import test_command_surface as surface
        tmp = Path(tempfile.mkdtemp(prefix="boundary-"))
        self.addCleanup(
            lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        target = tmp / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return surface.violations([target])

    def test_the_base_constructor_bypass_is_caught(self):
        # `str.__new__(Lit, x)` builds a genuine Lit without ever entering
        # `Lit.__new__`, because a str subclass cannot seal its base.
        for spelling in ("str.__new__(paths.Lit, value)",
                         "str.__new__(Lit, value)",
                         "str.__new__(paths.Command, value)"):
            with self.subTest(spelling=spelling):
                found = self._scan(f"x = {spelling}\n")
                self.assertTrue(found, f"{spelling} scanned clean")
                self.assertIn("str.__new__", found[0][3])

    def test_an_ordinary_str_new_is_not_flagged(self):
        # The control: the rule is about the guarded types, not about
        # `str.__new__` as such.
        self.assertEqual(self._scan("x = str.__new__(str, value)\n"), [])

    def test_a_subpackage_paths_module_gets_no_exemption(self):
        """The exemption matched `module.name == "paths.py"` — a BASENAME.

        `modules()` rglobs, so a future `review/<subpkg>/paths.py` would have
        inherited the renderer's own total exemption from the literal rule.
        """
        body = "import paths\nx = paths.Lit(dynamic)\n"
        self.assertTrue(self._scan(body, name="paths.py"),
                        "a non-canonical paths.py was exempted by basename")


if __name__ == "__main__":
    unittest.main()

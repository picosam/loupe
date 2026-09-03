"""Commands are built one way, and the doors that execute them check it.

Three rounds tried to prove commands safe by reading the source that built
them. Round 3 replaced a five-token regex with an f-string scan; round 4
added four construction forms; round 5 F1 then walked through a keyword
`.format`, a joined list, a tab separator and a `<` that was a redirection
rather than a placeholder — and pointed out the real defect: source-shape
inference fails OPEN by nature, because whatever it cannot analyse it has
to call prose.

So the boundary moved off the shape and onto the type.

  * `paths.command(...)` is the only way a command is built. Every word
    that is not a typed word (`Lit`, `Op`, `Ph`) is quoted as it is built,
    so there is no later moment at which a value could be unrendered.
  * `Lit` marks a shell-inert word the TOOL wrote — a verb, a flag. An
    operator is an `Op`, a `<placeholder>` is a `Ph`; each type validates
    its value at construction, and the three cannot cross (lineage 6
    round 1, F1).
  * `paths.token()` is the third form, for a document surface where a
    quote would corrupt the artifact: it PROVES the value shell-inert and
    raises otherwise.
  * The fields agents execute — a `Refusal`'s `next_cmd`, the CLI's
    `next`, a fenced relay line — call `paths.executable`, which accepts a
    rendered `Command` and refuses a bare string. A raw f-string cannot
    reach an agent by being unanalysable; it is rejected for its type.

What remains here is a DRIFT DETECTOR, not the safety boundary: it finds
source that builds a command any other way, over every module of the
package recursively, and it fails closed — an expression it cannot resolve
inside a command is a violation, never prose. It is best-effort evidence
by nature — Python admits indefinitely many spellings of the same call,
and no lexical walk can enumerate them — which is exactly why it is not
the invariant: the value grammar and the construction record in
`review/paths.py` are, and `test_command_boundary` proves those hold on
every route, however spelled.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import re
import shlex
import subprocess
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path
from types import SimpleNamespace

from review import brief, cli, paths, transport, wire
from review.tests.util import REPO_ROOT

# A command starts at one of these words, followed by ANY whitespace — a
# tab is a shell separator exactly like a space (round 5 F1).
COMMAND_START = re.compile(r"(?:^|[\s`(])(git|loupe|python3)(?:\s|$)")

# `Lit`/`lits` mark the tool's own words. Workshop (a): `RENDERERS` and
# `PROVERS` used to sit beside this and were DEAD — `rendered` was built and
# never read, and every id `_literal_words` collected was collected again
# unconditionally by the `LITERALS` walk below. So the file named `command`
# and `token` among its targets while checking neither, which made the
# invariant read wider than the code — the same defect this module exists to
# catch, in the module that catches it. What is listed here is what is used.
LITERALS = ("paths.Lit", "Lit", "paths.lits", "lits",
            "paths.Ph", "Ph", "paths.qph", "qph", "paths.Op", "Op")

# The one route the runtime guard cannot close: a `str` subclass cannot seal
# its base's constructor, so `str.__new__(Lit, x)` still builds one. It is a
# spelling no ordinary edit produces, which is exactly what makes it the
# scan's to hold rather than the type's.
BASE_CONSTRUCTORS = ("str.__new__",)
GUARDED_TYPES = ("Lit", "Command", "paths.Lit", "paths.Command")

# Constants of this package, fixed at import: not dynamic, so legal as a
# literal word.
# `TRANSPORT_GIT` joined 2026-09-03: its VALUE is the word `git`, so
# its own definition reads as a command to the scan below for the same
# reason `TOOL_NAME = "loupe"` does. It is a constant of this package,
# fixed at import, and the definition of a name is not a use of it.
CONSTANTS = ("TOOL_NAME", "TOOL_VERSION", "TRANSPORT_GIT")


def dotted(node) -> str:
    """`paths.command` for an attribute chain, `command` for a name."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def modules():
    """Every production module, RECURSIVELY (round 5 F1: a top-level glob
    left a future subpackage outside the inventory it claims to be)."""
    return sorted(p for p in (REPO_ROOT / "review").rglob("*.py")
                  if "tests" not in p.parts)


def _docstrings(tree) -> set:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef,
                             ast.AsyncFunctionDef)):
            text = ast.get_docstring(node, clean=False)
            if text is not None:
                out.add(text)
    return out


# Where a list of words is an ARGV rather than a command line: it reaches
# the OS as a sequence, no shell parses it, so no quoting question exists.
# A list handed to `" ".join(...)` is the opposite and is not exempt.
ARGV_CALLS = ("subprocess.run", "subprocess.Popen", "subprocess.check_output",
              "subprocess.check_call")


def _argv_constants(tree) -> set:
    """Constants inside a list/tuple passed straight to `subprocess`."""
    out = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and dotted(node.func) in ARGV_CALLS):
            continue
        for arg in node.args:
            if isinstance(arg, (ast.List, ast.Tuple)):
                for element in arg.elts:
                    out.add(id(element))
    return out


def _constant_definitions(tree) -> set:
    """The right-hand side of `TOOL_NAME = "loupe"`: the definition of the
    tool's own name is not a command that names it."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any(n in CONSTANTS for n in names):
                out.add(id(node.value))
    return out


def violations(paths_to_scan=None):
    """(module, line, what, why) for every command the source builds by
    hand, or every literal a dynamic value could enter."""
    out = []
    for module in (paths_to_scan or modules()):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        docs = _docstrings(tree)
        exempt = _argv_constants(tree) | _constant_definitions(tree)
        literal_args = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and dotted(node.func) in BASE_CONSTRUCTORS
                    and node.args
                    and dotted(node.args[0]) in GUARDED_TYPES):
                out.append((module.name, node.lineno, ast.unparse(node),
                            "the guarded constructor bypassed: Lit and "
                            "Command check their value when built, and "
                            "str.__new__ is the one way past that check"))
            if isinstance(node, ast.Call) and dotted(node.func) in LITERALS:
                for arg in node.args:
                    literal_args.add(id(arg))
                    if module.resolve() == (
                            REPO_ROOT / "review" / "paths.py").resolve():
                        # The renderer's own implementation constructs the
                        # types this rule is about; the rule is for callers.
                        continue
                    # A Lit may only wrap something the SOURCE states: a
                    # string constant, or a constant of this package.
                    if isinstance(arg, ast.Constant) and isinstance(
                            arg.value, str):
                        continue
                    if isinstance(arg, ast.Name) and arg.id in CONSTANTS:
                        continue
                    out.append((module.name, node.lineno, ast.unparse(arg),
                                "a literal word that is not a literal: a "
                                "dynamic value must be quoted or proved, "
                                "never marked as the tool's own word"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)):
                continue
            if (node.value in docs or id(node) in literal_args
                    or id(node) in exempt):
                continue
            if COMMAND_START.search(node.value):
                out.append((module.name, node.lineno, node.value[:60],
                            "a command written as a string: commands are "
                            "built by paths.command(...), whose every "
                            "dynamic word is rendered as it is built"))
    return out


class TestCommandsAreBuiltOneWay(unittest.TestCase):

    def test_no_module_builds_a_command_by_hand(self):
        self.assertEqual(violations(), [])

    def _synthetic(self, body: str, name: str = "synthetic.py"):
        tmp = Path(tempfile.mkdtemp(prefix="surface-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        target = tmp / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return violations([target])

    def test_every_construction_form_round_5_named_is_caught(self):
        # The four forms that scanned clean at 750d7c8, plus the two round
        # 4 named. Each fails here for the same reason: the command is a
        # string this module wrote instead of one `command()` rendered.
        forms = {
            "f-string": 'def f(r):\n    return f"git -C {r} diff"\n',
            "concatenation": 'def f(r):\n    return "git -C " + r + " diff"\n',
            "percent": 'def f(r):\n    return "git -C %s diff" % r\n',
            "positional format":
                'def f(r):\n    return "git -C {} diff".format(r)\n',
            "keyword format":
                'def f(r):\n    return "git -C {r} diff".format(r=r)\n',
            "joined list":
                'def f(r):\n    return " ".join(["git", "-C", r, "diff"])\n',
            "tab separator": 'def f(v):\n    return f"loupe\\t{v}"\n',
            "redirection as placeholder":
                'def f(p):\n    return f"loupe take <{p}"\n',
        }
        for name, body in forms.items():
            with self.subTest(form=name):
                self.assertTrue(self._synthetic(body), f"{name} scanned clean")

    def test_the_rendered_form_is_the_valid_control(self):
        # Each of those, written the one way, is silent — or the test above
        # would prove nothing but that the scan fires.
        self.assertEqual(self._synthetic(
            'from review import paths\n'
            'def f(r):\n'
            '    return paths.command(*paths.lits("git", "-C"), r,\n'
            '                         paths.Lit("diff"))\n'), [])

    def test_a_dynamic_value_cannot_enter_as_a_literal_word(self):
        found = self._synthetic(
            'from review import paths\n'
            'def f(v):\n'
            '    return paths.command(paths.Lit("loupe"), paths.Lit(v))\n')
        self.assertEqual(len(found), 1, found)
        self.assertIn("not a literal", found[0][3])

    def test_a_proved_token_is_admitted_where_a_quote_would_corrupt(self):
        self.assertEqual(self._synthetic(
            'from review import paths\n'
            'def f(v):\n'
            '    return paths.command(paths.Lit("loupe"), paths.token(v))\n'),
            [])

    def test_a_module_in_a_subpackage_is_inventoried(self):
        # Round 5 F1: the glob was top-level only, so a future subpackage
        # was outside the inventory that calls itself complete.
        found = self._synthetic('def f(r):\n    return f"git -C {r} diff"\n',
                                name="sub/deep.py")
        self.assertTrue(found)
        self.assertEqual(found[0][0], "deep.py")

    def test_the_live_module_list_is_recursive_and_excludes_tests(self):
        found = modules()
        self.assertTrue(any(m.name == "cli.py" for m in found))
        self.assertFalse(any("tests" in m.parts for m in found))
        # Whatever the package's layout becomes, the walk is the layout.
        self.assertEqual(
            {m.resolve() for m in found},
            {m.resolve() for m in (REPO_ROOT / "review").rglob("*.py")
             if "tests" not in m.parts})


class TestTheDoorsRefuseAnythingElse(unittest.TestCase):
    """The type is the boundary; these are the doors that check it."""

    def test_a_refusal_takes_a_rendered_command_or_nothing(self):
        self.assertEqual(transport.Refusal("why", "").next_cmd, "")
        rendered = paths.command(*paths.lits("loupe", "brief"))
        self.assertEqual(transport.Refusal("why", rendered).next_cmd,
                         rendered)
        with self.assertRaises(TypeError):
            transport.Refusal("why", "loupe brief")

    def test_the_cli_next_field_takes_a_rendered_command_or_nothing(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli._blocked(paths.command(*paths.lits("loupe", "handoff")), "x")
        self.assertEqual(json.loads(buf.getvalue())["next"], "loupe handoff")
        with self.assertRaises(TypeError):
            cli._blocked("loupe handoff", "x")

    def test_the_result_records_check_their_runnable_fields_too(self):
        # `reviewer_next`, `diff` and `next` are the same kind of field on
        # the RESULT side: an agent runs what they hold. They go through
        # the same door rather than being trusted for being nearby.
        self.assertEqual(transport._runnable({"next": None}, "next"),
                         {"next": None})
        rendered = paths.command(*paths.lits("loupe", "handoff"))
        self.assertEqual(transport._runnable({"next": rendered}, "next"),
                         {"next": rendered})
        with self.assertRaises(TypeError):
            transport._runnable({"next": "loupe handoff"}, "next")

    def test_a_bash_fence_takes_rendered_commands_only(self):
        rendered = paths.command(*paths.lits("loupe", "brief"))
        self.assertEqual(brief._fence(rendered)[1], "loupe brief")
        with self.assertRaises(TypeError):
            brief._fence("loupe brief")
        # The envelope paste is bytes, not commands, and is untouched.
        self.assertEqual(brief._fence("any bytes", lang="")[1], "any bytes")

    def test_the_renderer_quotes_every_dynamic_word(self):
        for hostile in HOSTILE_WORDS:
            with self.subTest(word=hostile):
                rendered = paths.command(paths.Lit("loupe"),
                                         paths.Lit("take"), hostile)
                self.assertEqual(shlex.split(rendered),
                                 ["loupe", "take", hostile])

    def test_the_prover_refuses_what_it_cannot_render_unquoted(self):
        self.assertEqual(paths.token("render-adapters"), "render-adapters")
        for hostile in HOSTILE_WORDS:
            with self.subTest(word=hostile):
                with self.assertRaises(ValueError):
                    paths.token(hostile)


HOSTILE_PATHS = ("/tmp/verdict with space.md", "/tmp/it's.md",
                 '/tmp/q"uote.md', "/tmp/semi;colon.md", "/tmp/uni-été.md",
                 "/tmp/dollar$var.md", "/tmp/back`tick.md")

# Values Git and review.toml actually accept, carrying shell syntax.
HOSTILE_WORDS = ("codex;printf_LOUPE_INJECTION", "codex && whoami",
                 "codex$(id)", "codex`id`", "codex with space", "codex'q",
                 "codex\tsplit")


class TestSurfacesSplitToOneArgument(unittest.TestCase):
    """The behavioural half: every surface produced and split back. A scan
    proves the shape of the source, never the argv."""

    def test_git_accepts_a_ref_carrying_shell_syntax(self):
        out = subprocess.run(
            ["git", "check-ref-format", "refs/heads/topic;printf_LOUPE"],
            capture_output=True, timeout=60)
        self.assertEqual(out.returncode, 0)

    def test_the_take_relay_carries_one_word_per_field(self):
        for identity in HOSTILE_WORDS:
            for path in HOSTILE_PATHS[:3]:
                with self.subTest(identity=identity, path=path):
                    relay = brief.relay(
                        path, SimpleNamespace(attrs={"reviewer": identity}),
                        "envelope bytes")
                    line = next(l for l in relay.splitlines()
                                if l.startswith("loupe take")
                                and not l.startswith("loupe take -"))
                    self.assertEqual(shlex.split(line),
                                     ["loupe", "take", path, "--as",
                                      identity])

    def test_the_paste_relay_carries_one_word_per_field(self):
        for identity in HOSTILE_WORDS:
            with self.subTest(identity=identity):
                relay = brief.relay(
                    "not kept", SimpleNamespace(attrs={"reviewer": identity}),
                    "envelope bytes")
                command = next(l for l in relay.splitlines()
                               if l.startswith("loupe take -"))
                self.assertEqual(shlex.split(command),
                                 ["loupe", "take", "-", "--as", identity])

    def test_the_diff_command_carries_one_word_per_revision(self):
        cmd = paths.diff_command("/tmp/repo with space", "a" * 40,
                                 "topic;printf")
        # Round 3 F1 (lineage 12): the printed command carries
        # `--no-replace-objects`, so the diff a human runs is the diff the
        # tool measured. It is git-wide and therefore precedes `diff`.
        self.assertEqual(shlex.split(cmd),
                         ["git", "-C", "/tmp/repo with space",
                          "--no-replace-objects", "diff",
                          f"{'a' * 40}...topic;printf"])

    def _verdict_text(self, sha="a" * 40):
        from review.tests.synth import TAG
        return (f'<{TAG}-review-verdict sha="{sha}">\n'
                f"VERDICT: changes requested\n\n## findings\n\n### F1\n"
                f"Severity: High\nClassification: correctness\n"
                f"Title: t\nEvidence: e\nWhy: w\nRequired outcome: r\n"
                f"FALSIFICATION: f\nPreventable-by: tests\n"
                f"</{TAG}-review-verdict>\n")

    def test_close_round_recovery_carries_one_path_argument(self):
        from review.ledger import Ledger
        from review.tests.synth import CFG
        for path in HOSTILE_PATHS:
            with self.subTest(path=path):
                with self.assertRaises(transport.Refusal) as ctx:
                    transport.close_round(CFG, Ledger.in_memory(),
                                          self._verdict_text(), source=path)
                argv = shlex.split(ctx.exception.next_cmd)
                self.assertEqual(argv[:2], ["loupe", "brief"])
                self.assertEqual(argv[2], path)

    def test_the_verify_line_carries_one_argument_per_field(self):
        record = {"state": "pushed", "branch": "main",
                  "ref": "refs/heads/feature branch",
                  "remote": "origin", "url": "/tmp/remote with space.git",
                  "sha": "b" * 40}
        verify = next(l for l in wire.render_push_lines(record)
                      if l.startswith("Verify:"))
        argv = shlex.split(verify[len("Verify:"):])
        self.assertEqual(argv[:2], ["git", "fetch"])
        self.assertEqual(argv[2], record["url"])
        self.assertEqual(argv[3], record["ref"])
        self.assertEqual(argv[-1], record["sha"])

    def test_the_import_legacy_recovery_carries_one_path_argument(self):
        try:
            tmp = Path(tempfile.mkdtemp(prefix="cmd surface-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        events = tmp / "not events.jsonl"
        events.write_text("{not json\n", encoding="utf-8")
        from review.tests.synth import CFG
        args = argparse.Namespace(events=str(events), ledger_dir=str(tmp),
                                  command="import-legacy")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.cmd_import_legacy(args, CFG)
        self.assertNotEqual(code, 0)
        remedy = json.loads(buf.getvalue())["remedy"]
        argv = shlex.split(remedy.split("`")[1])
        self.assertEqual(argv[:2], ["loupe", "import-legacy"])
        self.assertEqual(argv[2], str(events))


if __name__ == "__main__":
    unittest.main()

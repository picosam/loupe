"""Every non-zero exit is structured and carries a typed recovery.

Round-3 F17 asked that usage errors print the next command; round-4 F9
found a sixth path that returned prose after five tested ones; round-2 F8
and round-3 F6 found `next` fields that were sentences rather than argv;
sweep F11 extended the same contract to the transport refusal surface; and
round-5 RVW-T3 found a reachable filesystem failure (`validate <dir>`) that
bypassed the whole thing with a traceback.

The enumerations here are the artifact: a branch nobody listed is how each
of those defects survived, so `test_no_failure_path_can_print_prose` and
`test_every_transport_refusal_has_typed_runnable_recovery` read the source
rather than a sample.
"""
import argparse
import ast
import contextlib
import dataclasses
import io
import json
import os
import shlex
import stat
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from review import TOOL_NAME, config, env_var
from review.cli import UsageError, build_parser, main
from review.tests.util import REPO_ROOT


def run_cli(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(argv)
    return code, buf.getvalue()


def _cli_verbs():
    """The real verb list, read off the parser so it cannot drift."""
    sub = next(a for a in build_parser()._actions
               if isinstance(a, argparse._SubParsersAction))
    return set(sub.choices)


_VERBS = _cli_verbs()


class TestF9EveryNonZeroExitIsStructured(unittest.TestCase):
    """Round-4 F9 — FALSIFICATION: Every reachable exit 1 or 2 returns
    parseable structured output containing a nonempty `next` field."""

    # Every reachable non-zero branch in cli.py, enumerated. The round-4
    # defect was a branch nobody had listed, so the list is the artifact:
    # `test_no_failure_path_can_print_prose` checks it against the source.
    BRANCHES = (
        ("validate",),                                    # usage: missing arg
        ("nonsense",),                                    # usage: unknown verb
        ("ledger",),                                      # usage: missing sub
        ("respond", "--verdict", "/nope"),                # usage: missing arg
        ("validate", "/nonexistent.md"),                  # usage: no file
        ("validate", "README.md"),                        # findings: unknown
        ("ledger", "add", "README.md"),                   # findings: unknown
        ("ledger", "add",
         "review/tests/fixtures/legacy-verdict.md"),      # findings: invalid
        # Round 3 F6: the branch the enumeration was missing. An envelope the
        # tool RECOGNISES and then rejects exits through _finish, which the
        # three above never reach — and _finish was the one path still
        # labelling a sentence `command`.
        ("validate",
         "review/tests/fixtures/legacy-verdict.md"),      # recognised, invalid
    )

    def assert_typed_recovery(self, payload):
        """The round-2 F8 contract, applied to any non-zero payload.

        Round 4 asked only that `next` be nonempty, which a diagnosis
        satisfies as happily as a command. So the check is now the property
        an agent actually depends on: either `next` is argv it can RUN — put
        through the real parser, not eyeballed — or the exit is typed
        `blocked`, `next` is null, and a remedy names what a person must do.
        """
        self.assertIn(payload.get("next_kind"), ("command", "blocked"),
                      payload)
        if payload["next_kind"] == "blocked":
            self.assertIsNone(payload["next"], payload)
            self.assertTrue(payload.get("remedy"), payload)
            return
        cmd = payload.get("next")
        self.assertTrue(cmd, payload)
        tokens = shlex.split(cmd)
        self.assertTrue(tokens, payload)
        # Round 3 F6: a sentence has a verb that is not a verb. `fix`,
        # `re-run`, `inspect`, `restore` and a leading placeholder are all
        # shapes prose takes when it is put in an argv field, and each one
        # shipped at least once.
        self.assertNotIn(",", tokens[0],
                         "a comma in the verb is the shape of a sentence")
        self.assertNotIn(tokens[0], {"fix", "re-run", "inspect", "restore",
                                     "make", "wrap", "remove", "whoever"},
                         f"`next` opens with an English imperative: {cmd}")
        self.assertFalse(tokens[0].startswith(("<", "{", "[")),
                         f"`next` opens with a placeholder: {cmd}")
        if tokens[0] != TOOL_NAME:
            return                       # `git …` and friends: not our grammar
        rest = [t for t in tokens[1:] if not t.startswith("#")]
        # RVW-T16 (2026-08-19): the lineage-2 round-4 F9 tolerance is
        # withdrawn. It admitted "a placeholder the human fills in; the verb
        # must be real" for the CLI surface, while the transport surface had
        # to be literal argv or `blocked`. Two rules for one field an agent
        # executes is one rule too many: an argv with `<verdict.md>` in it is
        # not runnable, and the adapters tell every agent to run `next`
        # VERBATIM. A recovery only a person can complete is `blocked` with a
        # remedy — that state exists precisely for this.
        placeholders = [x for x in rest
                        if x.startswith("<") or x.startswith("{")]
        self.assertFalse(placeholders,
                         f"`next` carries a placeholder an agent cannot run: "
                         f"{cmd} — it should be blocked with a remedy")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), \
                    contextlib.redirect_stderr(buf):
                build_parser().parse_args(rest)
        except SystemExit as exc:
            # `--help` parses, prints, and exits 0. That is a runnable
            # command; only a non-zero exit means the argv is not valid.
            if int(exc.code or 0) != 0:              # pragma: no cover
                self.fail(f"`next` is not runnable argv: {cmd}")
        except UsageError as exc:                    # pragma: no cover
            self.fail(f"`next` does not parse as a command: {cmd} ({exc})")

    def test_every_enumerated_branch_returns_structured_output(self):
        for argv in self.BRANCHES:
            with self.subTest(argv=argv):
                code, out = run_cli(list(argv))
                self.assertNotEqual(code, 0)
                self.assert_typed_recovery(json.loads(out))

    def test_the_defect_this_finding_names_is_gone(self):
        # The round-4 probe verbatim: exit 1, then json.loads raised.
        code, out = run_cli(["validate", "README.md"])
        self.assertEqual(code, 1)
        self.assert_typed_recovery(json.loads(out))

    def test_config_load_failures_are_structured(self):
        """Round 1 F8 (Medium), falsification.

        The enumeration above covers branches reached AFTER configuration
        resolves. Round 1 found the ones before it: config.load ran above the
        boundary, so malformed TOML left a traceback and the one instruction
        the adapters give — run the `next` command — was unavailable exactly
        where a fresh install fails first.
        """
        env = env_var("CONFIG")
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "broken.toml"
            bad.write_text("this is not = = valid toml\n", encoding="utf-8")
            previous, cwd = os.environ.get(env), os.getcwd()
            os.environ[env] = str(bad)
            os.chdir(tmp)                 # away from this repo's review.toml
            try:
                code, out = run_cli(["validate", str(bad)])
            finally:
                os.chdir(cwd)
                if previous is None:
                    os.environ.pop(env, None)
                else:
                    os.environ[env] = previous
        self.assertNotEqual(code, 0)
        payload = json.loads(out)         # the round-1 probe: this raised
        self.assert_typed_recovery(payload)
        self.assertIn("TOML", payload["error"])

    def test_an_unreadable_config_is_structured_too(self):
        # The adjacent state: valid TOML the process cannot read.
        env = env_var("CONFIG")
        with tempfile.TemporaryDirectory() as tmp:
            denied = Path(tmp) / "denied.toml"
            denied.write_text("[taxonomy]\n", encoding="utf-8")
            denied.chmod(0o000)
            if os.access(denied, os.R_OK):     # running as root: not provable
                denied.chmod(stat.S_IRUSR | stat.S_IWUSR)
                self.skipTest("this process can read a 0o000 file")
            previous, cwd = os.environ.get(env), os.getcwd()
            os.environ[env] = str(denied)
            os.chdir(tmp)
            try:
                code, out = run_cli(["validate", str(denied)])
            finally:
                os.chdir(cwd)
                denied.chmod(stat.S_IRUSR | stat.S_IWUSR)
                if previous is None:
                    os.environ.pop(env, None)
                else:
                    os.environ[env] = previous
        self.assertNotEqual(code, 0)
        self.assert_typed_recovery(json.loads(out))

    def test_recognized_invalid_validate_has_typed_runnable_recovery(self):
        """Round 3 F6 (Medium), falsification.

        The typed-recovery rule was applied to the paths round 2 named and
        not to the class it declared. `_finish` — the exit every RECOGNISED
        but invalid envelope takes — labelled its recovery `command` while
        being handed `fix the listed items in <file>, then re-run …`. An
        adapter obeying the contract literally tries to execute the word
        `fix`. The round-2 enumeration missed it because all three of its
        failure branches stop before `_finish` is reached.

        Both `_finish` paths an author actually hits are covered here.
        """
        cases = (
            (["validate", "review/tests/fixtures/legacy-verdict.md"],
             "a recognised verdict that fails validation"),
            (["respond", "--verdict",
              "review/tests/fixtures/legacy-verdict.md",
              "--from-json", "-"],
             "a disposition payload that fails validation"),
        )
        for argv, what in cases:
            with self.subTest(case=what):
                stdin = unittest.mock.patch(
                    "sys.stdin", io.StringIO(json.dumps(
                        {"verdict_sha": "9" * 40, "head": "8" * 40,
                         "author": "claude", "round": 1,
                         "dispositions": []})))
                with stdin:
                    code, out = run_cli(argv)
                self.assertNotEqual(code, 0, what)
                payload = json.loads(out)
                # The contract, executed rather than eyeballed.
                self.assertTrue(payload.get("items"), payload)
                self.assert_typed_recovery(payload)
                # And the defect named: the sentence must not come back as
                # something an agent is told to run.
                if payload["next_kind"] == "command":
                    self.assertNotIn("then re-run", payload["next"])
                    self.assertNotIn("fix ", payload["next"])
                else:
                    self.assertIsNone(payload["next"])
                    self.assertTrue(payload["remedy"])

    def test_a_finish_path_with_a_real_command_stays_a_command(self):
        # The control for the test above: `_finish` must still be able to
        # return a command. `ledger add` on a recognised-but-invalid envelope
        # hands it `loupe validate <file>`, which is genuinely runnable, and
        # typing everything `blocked` would satisfy F6 by giving up.
        code, out = run_cli(
            ["ledger", "add", "review/tests/fixtures/legacy-verdict.md"])
        payload = json.loads(out)
        self.assertNotEqual(code, 0)
        self.assertEqual(payload["next_kind"], "command", payload)
        self.assertTrue(payload["next"].startswith(f"{TOOL_NAME} validate"))
        self.assert_typed_recovery(payload)

    def test_config_failure_next_is_executable_command(self):
        """Round 2 F8 (Medium), falsification.

        Round 1 moved configuration inside the structured boundary; round 2
        found what was inside it. `next` read `fix /path/to/broken.toml, then
        re-run` — a sentence, arriving through the one field the adapters tell
        an agent to execute verbatim, at the fresh-install state a first run
        hits before anything else. The old control asserted `next` was
        truthy, which that sentence is.

        Nothing the tool can run repairs a human's broken TOML, so the fix is
        not to invent a command: it is to say `blocked` and hand the person a
        remedy. This asserts BOTH halves — that no config failure ships prose
        in `next`, and that whatever it does ship is the declared kind.
        """
        env = env_var("CONFIG")
        cases = {
            "malformed": "this is not = = valid toml\n",
            "unclosed-table": "[taxonomy\n",
            "duplicate-key": "a = 1\na = 2\n",
        }
        for name, body in cases.items():
            with self.subTest(config=name):
                with tempfile.TemporaryDirectory() as tmp:
                    bad = Path(tmp) / f"{name}.toml"
                    bad.write_text(body, encoding="utf-8")
                    previous, cwd = os.environ.get(env), os.getcwd()
                    os.environ[env] = str(bad)
                    os.chdir(tmp)          # away from this repo's review.toml
                    try:
                        code, out = run_cli(["validate", str(bad)])
                    finally:
                        os.chdir(cwd)
                        if previous is None:
                            os.environ.pop(env, None)
                        else:
                            os.environ[env] = previous
                self.assertNotEqual(code, 0)
                payload = json.loads(out)
                # The contract, executed rather than eyeballed.
                self.assert_typed_recovery(payload)
                # And the defect itself, named: the round-2 probe's exact
                # string shape must not come back through `next` under any
                # kind. A diagnosis is recognisable — it has a comma and an
                # imperative — and it belongs in `remedy`, never in argv.
                self.assertIsNone(payload["next"], payload)
                self.assertEqual(payload["next_kind"], "blocked", payload)
                self.assertNotIn("then re-run", str(payload["next"]))
                self.assertTrue(payload["remedy"])

    def test_a_config_failure_with_a_real_command_stays_a_command(self):
        """The control for the test above.

        `blocked` must not become the answer to everything — that would
        satisfy F8 by never offering a command again. The legacy-state
        refusal has a genuine one, and it must still type as `command` and
        still parse.
        """
        with unittest.mock.patch.object(
                config, "legacy_state_dir",
                return_value=Path("/tmp/former-name/repo")):
            with unittest.mock.patch.object(Path, "is_dir",
                                            return_value=False):
                code, out = run_cli(["ledger", "report"])
        payload = json.loads(out)
        self.assertNotEqual(code, 0)
        self.assertEqual(payload["next_kind"], "command", payload)
        self.assertEqual(payload["next"], f"{TOOL_NAME} migrate-state")
        self.assert_typed_recovery(payload)

    def test_no_failure_path_can_print_prose(self):
        # The enumeration above can only ever be as complete as someone
        # remembered to make it, which is precisely how F9's branch survived
        # five tested paths. This is the structural half: `print` may appear
        # only in the structured-output helper and in the three commands whose
        # exit-0 job is to write an artifact to stdout. Any new bare print on
        # a failure path fails here without anyone having to list it.
        source = (REPO_ROOT / "review" / "cli.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        printers = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for child in ast.walk(node):
                if (isinstance(child, ast.Call)
                        and isinstance(child.func, ast.Name)
                        and child.func.id == "print"):
                    printers.add(node.name)
        self.assertEqual(printers, {"_out", "cmd_respond",
                                    "cmd_ledger_report", "cmd_emit_request"},
                         "a print() outside the structured helper and the "
                         "artifact-writing commands is a prose exit waiting "
                         "to happen")


class TestFilesystemFailuresKeepTheContract(unittest.TestCase):
    """Round-5 RVW-T3 (fp2:9f8d9f3677f33f51) — FALSIFICATION: every
    reachable filesystem failure of `validate <path>` exits through the
    structured next-command contract, never a traceback."""

    def _run(self, argv):
        code, out = run_cli(argv)
        return code, json.loads(out)

    def test_validate_a_directory_is_a_structured_usage_error(self):
        code, out = self._run(["validate", str(REPO_ROOT / "review")])
        self.assertEqual(code, 2)
        self.assertIn("next", out)
        self.assertFalse(out["ok"])

    def test_validate_a_missing_file_is_a_structured_usage_error(self):
        code, out = self._run(["validate",
                               str(REPO_ROOT / "does-not-exist.md")])
        self.assertEqual(code, 2)
        self.assertIn("next", out)

    def test_validate_an_undecodable_file_is_a_structured_usage_error(self):
        # Adjacent state: bytes that are not text (UnicodeDecodeError is a
        # ValueError, caught by the same contract). Two bytes no UTF-8
        # decoder accepts, in a scratch file — never a checkout artifact
        # whose presence depends on how the tree was obtained.
        try:
            tmp = tempfile.TemporaryDirectory(prefix="undecodable-")
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        with tmp:
            binary = Path(tmp.name) / "not-text.md"
            binary.write_bytes(b"\xff\xfe")
            code, out = self._run(["validate", str(binary)])
        self.assertEqual(code, 2)
        self.assertIn("next", out)


def _dotted(node) -> str:
    """`paths.command` for an Attribute chain, `command` for a Name."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


class TestEveryFailureExit(unittest.TestCase):
    """Sweep F11 (Medium): the transport refusal surface, enumerated.

    `TestF9EveryNonZeroExitIsStructured` enumerates the CLI's own exits.
    Transport verbs raise `Refusal(why, next_cmd)` and the CLI stamps every
    nonempty `next_cmd` as `next_kind: command` — so a Refusal whose second
    argument was a sentence (`check remote access to …, then re-run loupe
    take`; `return the envelope to the author`; `remove 'x' from the
    disposition and re-run`) reached the agent as a program to execute.
    Every Refusal site in transport.py is read here from the source, and
    its `next_cmd` must be either empty (the CLI types that `blocked`, with
    the account as the remedy) or a literal runnable line: `&&`-separated
    segments, each opening with `loupe <real verb>` or `git`, no
    `<placeholder>`, no English connective. A trailing `# comment` is the
    established hint form and stays allowed.
    """

    TRANSPORT = REPO_ROOT / "review" / "transport.py"

    @classmethod
    def _refusal_sites(cls):
        """[(lineno, template)] for every `Refusal(...)` in transport.py.
        FormattedValues render as `TOOL_NAME` when they are that name and
        as `‹X›` otherwise; conditional expressions yield both branches."""
        tree = ast.parse(cls.TRANSPORT.read_text(encoding="utf-8"))
        sites = []

        def render(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return [node.value]
            if isinstance(node, ast.JoinedStr):
                outs = [""]
                for part in node.values:
                    if isinstance(part, ast.Constant):
                        outs = [o + part.value for o in outs]
                    elif isinstance(part, ast.FormattedValue):
                        v = part.value
                        piece = (TOOL_NAME if isinstance(v, ast.Name)
                                 and v.id == "TOOL_NAME" else "‹X›")
                        outs = [o + piece for o in outs]
                return outs
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                return [a + b for a in render(node.left)
                        for b in render(node.right)]
            if isinstance(node, ast.IfExp):
                return render(node.body) + render(node.orelse)
            # Round 5 F1: recoveries are built by `paths.command(...)` now,
            # not spliced into f-strings. A `Lit` word renders as the
            # literal the source states; every other word is a value, which
            # this enumeration has always shown as ‹X›.
            if (isinstance(node, ast.Call)
                    and _dotted(node.func) in ("paths.command", "command")):
                words = []
                flat = []
                for arg in node.args:
                    if (isinstance(arg, ast.Starred)
                            and isinstance(arg.value, ast.Call)
                            and _dotted(arg.value.func) in ("paths.lits",
                                                            "lits")):
                        # `*paths.lits(a, b)` is several Lit words at once.
                        flat.extend(
                            ast.Call(func=ast.Name(id="Lit", ctx=ast.Load()),
                                     args=[a], keywords=[])
                            for a in arg.value.args)
                    else:
                        flat.append(arg)
                for arg in flat:
                    if (isinstance(arg, ast.Call)
                            and _dotted(arg.func) in ("paths.Lit", "Lit")
                            and len(arg.args) == 1):
                        inner_node = arg.args[0]
                        if (isinstance(inner_node, ast.Name)
                                and inner_node.id == "TOOL_NAME"):
                            words.append(TOOL_NAME)
                            continue
                        inner = render(inner_node)
                        words.append(inner[0] if inner and inner[0]
                                     is not None else "‹X›")
                    elif (isinstance(arg, ast.Name)
                          and arg.id == "TOOL_NAME"):
                        words.append(TOOL_NAME)
                    else:
                        words.append("‹X›")
                return [" ".join(words)]
            return [None]                      # not a literal: judged as such

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "Refusal"):
                continue
            nxt = None
            if len(node.args) >= 2:
                nxt = node.args[1]
            for kw in node.keywords:
                if kw.arg == "next_cmd":
                    nxt = kw.value
            for template in render(nxt) if nxt is not None else [None]:
                sites.append((node.lineno, template))
        return sites

    def _judge(self, template):
        """None if `template` is a legal `next_cmd`, else the reason."""
        if template is None:
            return "next_cmd is not a string literal the source states"
        if template == "":
            return None                       # blocked: typed by the CLI
        line = template.split("#", 1)[0].strip()   # the hint form
        if not line:
            return "only a comment"
        if "<" in line or ">" in line:
            return f"placeholder in a field an agent executes: {template!r}"
        for connective in (", then", " then ", " or ", "re-run", "return "):
            if connective in line:
                return f"English in argv: {template!r}"
        for segment in line.split("&&"):
            tokens = shlex.split(segment.replace("‹X›", "X"))
            if not tokens:
                return f"empty command segment in {template!r}"
            if tokens[0] == TOOL_NAME:
                if len(tokens) < 2 or tokens[1] not in _VERBS:
                    return f"`{TOOL_NAME}` without a real verb: {template!r}"
            elif tokens[0] != "git":
                return f"opens with {tokens[0]!r}, not a runnable: {template!r}"
        return None

    def test_every_transport_refusal_has_typed_runnable_recovery(self):
        """FALSIFICATION for F11. Mutation: restore any prose-valued
        `next_cmd` — e.g. `"return the envelope to the author"` on the
        base-ancestry refusal in probe_target — and the enumeration names
        the line and fails."""
        sites = self._refusal_sites()
        self.assertGreaterEqual(len(sites), 25, "the enumeration must see "
                                "the whole surface, not a sample")
        bad = [(ln, t, why) for ln, t in sites
               for why in [self._judge(t)] if why]
        self.assertEqual(bad, [], "\n".join(
            f"transport.py:{ln}: {why}" for ln, t, why in bad))
        # Both shapes exist on the surface: this is not satisfied by typing
        # everything blocked, nor by typing everything a command.
        self.assertTrue(any(t == "" for _, t in sites))
        self.assertTrue(any(t for _, t in sites))

    def test_paired_real_command_controls(self):
        """The refusals that DO carry commands carry runnable ones, and the
        ones that cannot are blocked — exercised at runtime, not only read
        from the source."""
        from review import transport
        from review.ledger import Ledger
        from review.tests._transport_fixtures import (SHA_A, SHA_B, SHA_C,
                                                      fake_git, request_text)
        cfg = dataclasses.replace(config.load(REPO_ROOT), ledger_dir=None)
        # Blocked: no stamp — a person returns the envelope.
        with self.assertRaises(transport.Refusal) as ctx:
            transport.probe_target(cfg, None, SHA_B, SHA_A, git=fake_git({}))
        self.assertEqual(ctx.exception.next_cmd, "")
        # Blocked: the fetch itself failed — access is a person's to restore.
        git = fake_git({("fetch", "u", "r"): RuntimeError("no route")})
        push = {"state": "pushed", "url": "u", "ref": "r"}
        # `remotes` declares that this clone IS a clone of the stamped
        # repository, so each refusal below is reached for its own reason
        # rather than for the foreign-checkout one that precedes them.
        with self.assertRaises(transport.Refusal) as ctx:
            transport.probe_target(cfg, push, SHA_B, None, git=git,
                                   remotes=["u"])
        self.assertEqual(ctx.exception.next_cmd, "")
        # Command: the fetch was skipped and the re-take is known, so the
        # recovery is the literal fetch-and-retake line.
        git = fake_git({("cat-file", "-e", f"{SHA_B}^{{commit}}"):
                        RuntimeError("missing")})
        with self.assertRaises(transport.Refusal) as ctx:
            transport.probe_target(cfg, push, SHA_B, None, fetch=False,
                                   git=git, retake=("r.md", "codex"),
                                   remotes=["u"])
        cmd = ctx.exception.next_cmd
        self.assertTrue(cmd.startswith("git -C "), cmd)
        self.assertIn(f" && {TOOL_NAME} take r.md --as codex", cmd)
        self.assertIsNone(self._judge(cmd), cmd)
        # ...and without a known re-take, blocked rather than a placeholder.
        with self.assertRaises(transport.Refusal) as ctx:
            transport.probe_target(cfg, push, SHA_B, None, fetch=False,
                                   git=git, remotes=["u"])
        self.assertEqual(ctx.exception.next_cmd, "")
        # Command: a waiver on an unresolvable SHA points at the log.
        with self.assertRaises(transport.Refusal) as ctx:
            transport.waive(cfg, Ledger.in_memory(), SHA_C, "r", "user",
                            git=fake_git({("rev-parse", "--verify",
                                           f"{SHA_C}^{{commit}}"):
                                          RuntimeError("unknown")}))
        self.assertTrue(ctx.exception.next_cmd.startswith("git -C "))
        self.assertIsNone(self._judge(ctx.exception.next_cmd))
        # Through the CLI: a transport refusal with an empty next_cmd is
        # typed blocked, next null, remedy present.
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.object(
                    transport, "take",
                    side_effect=transport.Refusal("planted", "")):
            env = Path(tmp) / "r.md"
            env.write_text(request_text(), encoding="utf-8")
            code, out = run_cli(["--ledger-dir", tmp, "take", str(env),
                                 "--as", "codex"])
        self.assertNotEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["next_kind"], "blocked")
        self.assertIsNone(payload["next"])
        self.assertTrue(payload["remedy"])
        # Sanity on the judge itself, against the shapes F11 cited.
        for prose in ("check remote access to u, then re-run loupe take",
                      "git fetch --unshallow or fetch the base ref, then "
                      "re-run loupe take",
                      "return the envelope to the author",
                      "remove 'x' from the disposition and re-run",
                      f"{TOOL_NAME} take r.md --as <id>",
                      f"git fetch u r && {TOOL_NAME} take <envelope>"):
            self.assertIsNotNone(self._judge(prose), prose)
        for real in ("", f"{TOOL_NAME} ledger report",
                     f"{TOOL_NAME} validate r.md  # then return it",
                     "git -C /r log --oneline -5",
                     f"git -C /r fetch u r && {TOOL_NAME} take r.md --as x"):
            self.assertIsNone(self._judge(real), real)


if __name__ == "__main__":
    unittest.main()

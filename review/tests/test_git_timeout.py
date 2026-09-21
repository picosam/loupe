"""`[limits] git_timeout` and the typed git-timeout refusal (loupe 0.25.0).

THE DEFECT, measured rather than supposed. Every git door ran with a ceiling
written into the call — 120 s at the doors that push and fetch. `commit -a`
and `push` execute the REPOSITORY's own hooks, and a repository whose
`pre-push` runs a full test gate is an ordinary design: one adopter's took
133 s, and every push the tool made there died of
`subprocess.TimeoutExpired`. At `review/transport.py`'s door that was a
TRACEBACK (`respond --out`'s envelope push), and at `review/emit.py`'s it was
a generic "did not complete" with a `<verb> --help` recovery. Neither said
which ceiling was in force, whether a person could move it, or that the thing
being waited on was usually the repository's own hook — and git runs
`pre-push` even when there is nothing to send.

WHAT THIS MODULE HOLDS, partitioned:

  the key       undeclared (the old 120, read off the real door), declared
                (read back from the scratch repository's own committed
                configuration, never restated), and every invalid kind —
                0, negative, bool, string, float, list, table — refused at
                config load through the real CLI, each beside a valid
                paired control.
  the doors     every door that HOLDS a configuration takes
                `config.git_timeout(cfg)` (structural, over the source, and
                by recording the real subprocess argument); every door
                converts `TimeoutExpired` into `transport.GitTimeout`; every
                wrapper on a push/fetch path passes it through rather than
                re-describing it.
  the ceiling   a real `pre-push` hook that sleeps, against a ceiling BELOW,
                AT and ABOVE its sleep, through `python3 -m review handoff`.
                "At" is deterministic by construction: the subprocess
                deadline starts after `git push` has been exec'd, and git
                has work to do (spawn the remote side, read its
                advertisement) before it starts the hook, so the hook's
                sleep ends strictly after the deadline.
  the verbs     every verb that pushes or fetches, timed out through the
                real entry point, returning the typed blocked result whose
                remedy names the key — never a traceback — with its paired
                control succeeding once the slow hook or remote is gone, and
                with the refs, the index and the working tree left
                byte-identical where the refusal comes before any write.

Real repositories, a bare remote, a reviewer clone, each with its own state
directory and its own identity; a slow REMOTE is a `packObjectsHook` in a
scratch `GIT_CONFIG_GLOBAL` (upload-pack honours it only from protected
configuration), a slow HOOK is a script under the repository's own
`core.hooksPath`. Nothing reaches a network.

MUTATIONS, each applied alone with bytecode off and measured red, recorded
in the track report of 2026-09-21 (loupe 0.25.0, track T1).
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from review import cli, config, emit, env_var, transport, vocab
from review.config import GIT_TIMEOUT_DEFAULT_S, git_ceiling, git_timeout
from review.tests._transport_fixtures import verdict_text

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The environment names a scratch CLI process must not inherit: the
#: transport declarations (the host may be a cloud sandbox that declares
#: one), and every name the gate runner exports — this module may itself be
#: running inside a gate.
_TRANSPORT_NAMES = ({vocab.TRANSPORT_ENV}
                    | {var for var, _v, _t in vocab.TRANSPORT_PROVIDER_SIGNALS})
_RUNNER_NAMES = tuple(env_var(n) for n in ("IN_GATE_RUN", "GATE_HEAD",
                                           "GATE_BASE", "STATE_DIR", "CONFIG",
                                           "GATE_WORKERS"))

TAXONOMY = """[taxonomy]
severities = ["Blocker", "High", "Medium", "Low", "Info"]
blocking = ["Blocker", "High"]
classifications = ["design_gap", "factual_error"]

[roles]
author = "claude"
reviewer = "codex"
relay = "user"
permitted_authors = ["claude", "codex"]
permitted_reviewers = ["claude", "codex"]
enforcement = "none"
review_default = "on"
{roles_extra}
[limits]
round_cap = 3
{limits_extra}
"""


def git(where, *args, env=None) -> str:
    out = subprocess.run(["git", "-C", str(where), *args], check=True,
                         capture_output=True, text=True, timeout=60,
                         env=env)
    return out.stdout.strip()


def cli_env(state: Path, extra: dict | None = None) -> dict:
    """The environment of a separate `python3 -m review` process — this
    checkout's package, its own state directory, and nothing the suite's
    own environment would otherwise decide for it."""
    env = {k: v for k, v in os.environ.items()
           if k not in _TRANSPORT_NAMES and k not in _RUNNER_NAMES}
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONSAFEPATH"] = "1"
    env[env_var("STATE_DIR")] = str(state)
    env.update(extra or {})
    return env


def sleeping_pre_push(seconds: float, pattern: str = "",
                      counter: Path | None = None) -> str:
    """A `pre-push` hook that sleeps `seconds` when a pushed ref line
    contains `pattern` (every push, when empty). It counts itself BEFORE it
    sleeps: a hook git was killed under still ran."""
    count = f'echo run >> "{counter}"\n' if counter else ""
    return (f"lines=$(cat)\n{count}"
            f'case "$lines" in *"{pattern}"*) /bin/sleep {seconds} ;; esac\n'
            f"exit 0\n")


class Scratch:
    """An author repository with a bare remote, and (on request) a reviewer
    clone — each with its own identity, its own hooks directory and its
    own state directory. The configuration is COMMITTED: a reviewed commit
    carries its own authority, and the ceiling under test is read back
    from it rather than restated."""

    IDENTITY = (("user.name", "author"), ("user.email", "a@example.invalid"),
                ("commit.gpgsign", "false"))

    def __init__(self, case: unittest.TestCase, prefix: str, *,
                 git_timeout=None, gates: str = "", transport: str = "",
                 remote_dir: str = "remote.git", url_form: str = "path"):
        self.case = case
        try:
            self.root = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
        except OSError as exc:  # pragma: no cover - read-only pass
            case.skipTest(f"filesystem writes denied ({exc})")
        case.addCleanup(shutil.rmtree, self.root, True)
        self.repo = self.root / "author"
        self.remote = self.root / remote_dir
        self.state = self.root / "state-author"
        self.hooks = self.root / "hooks-author"
        self.hooks.mkdir()
        git(self.root, "init", "-q", "-b", "main", str(self.repo))
        for key, value in self.IDENTITY + (("core.hooksPath",
                                            str(self.hooks)),):
            git(self.repo, "config", key, value)
        self.write_config(git_timeout=git_timeout, gates=gates,
                          transport=transport)
        (self.repo / "f.txt").write_text("one\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "init")
        self.base = git(self.repo, "rev-parse", "HEAD")
        self.remote.parent.mkdir(parents=True, exist_ok=True)
        git(self.root, "init", "-q", "--bare", "-b", "main", str(self.remote))
        self.url = (str(self.remote) if url_form == "path"
                    else f"file://{self.remote}")
        git(self.repo, "remote", "add", "origin", self.url)
        git(self.repo, "push", "-q", "-u", "origin", "main")
        (self.repo / "f.txt").write_text("two\n", encoding="utf-8")
        git(self.repo, "commit", "-qam", "change")
        self.head = git(self.repo, "rev-parse", "HEAD")
        self.claim = self.root / "claim.json"
        self.claim.write_text(json.dumps({
            "objective": "git ceiling test",
            "references": [{"path": "review.toml", "required": True}]}),
            encoding="utf-8")

    # ------------------------------------------------------------ config

    def write_config(self, *, git_timeout=None, gates: str = "",
                     transport: str = "", where: Path | None = None) -> None:
        roles = f'transport = "{transport}"\n' if transport else ""
        limits = (f"git_timeout = {git_timeout}\n"
                  if git_timeout is not None else "")
        text = TAXONOMY.format(roles_extra=roles, limits_extra=limits) + gates
        (where or self.repo).joinpath("review.toml").write_text(
            text, encoding="utf-8")

    def ceiling(self, where: Path | None = None) -> int:
        """The ceiling the committed configuration declares, READ back
        through the one reader — never the number this fixture wrote."""
        return git_timeout(config.load(where or self.repo,
                                       ledger_dir=str(self.state)))

    # ------------------------------------------------------------- hooks

    def hook(self, name: str, body: str, hooks: Path | None = None) -> Path:
        path = (hooks or self.hooks) / name
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        path.chmod(0o755)
        return path

    def unhook(self, name: str, hooks: Path | None = None) -> None:
        ((hooks or self.hooks) / name).unlink(missing_ok=True)

    def slow_remote(self, seconds: float) -> dict:
        """The environment of a process whose REMOTE is slow: upload-pack
        runs `pack-objects` through this hook, which it honours only from
        protected configuration — here a scratch global file."""
        hook = self.root / f"slow-pack-{seconds}"
        hook.write_text(f'#!/bin/sh\n/bin/sleep {seconds}\nexec "$@"\n',
                        encoding="utf-8")
        hook.chmod(0o755)
        cfg = self.root / f"gitconfig-slow-{seconds}"
        cfg.write_text(f"[uploadpack]\n\tpackObjectsHook = {hook}\n",
                       encoding="utf-8")
        return {"GIT_CONFIG_GLOBAL": str(cfg)}

    # ------------------------------------------------------------ clones

    def reviewer_clone(self, name: str = "reviewer") -> tuple[Path, Path, Path]:
        """(clone, its state directory, its hooks directory)."""
        clone = self.root / name
        # `--no-local`: a path clone otherwise hardlinks the remote's WHOLE
        # object store, envelope blobs included, and a later fetch then has
        # nothing to transfer — nor anything for a slow remote to slow.
        git(self.root, "clone", "-q", "--no-local", self.url, str(clone))
        hooks = self.root / f"hooks-{name}"
        hooks.mkdir()
        for key, value in (("user.name", name),
                           ("user.email", f"{name}@example.invalid"),
                           ("commit.gpgsign", "false"),
                           ("core.hooksPath", str(hooks))):
            git(clone, "config", key, value)
        return clone, self.root / f"state-{name}", hooks

    # --------------------------------------------------------------- run

    def run(self, *argv, where: Path | None = None,
            state: Path | None = None, env: dict | None = None,
            timeout: float = 180) -> tuple[int, dict, str]:
        """`python3 -B -m review argv...` — the real entry point, as a
        separate process — returning (exit, JSON payload, stderr)."""
        proc = subprocess.run(
            [sys.executable, "-B", "-m", "review", *argv],
            cwd=str(where or self.repo), env=cli_env(state or self.state, env),
            capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL)
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload = {"stdout": proc.stdout}
        return proc.returncode, payload, proc.stderr

    def handoff(self, *extra, **kw):
        return self.run("handoff", "--claim-file", str(self.claim),
                        "--base", self.base, *extra, **kw)

    def events(self, state: Path | None = None, kind: str | None = None):
        path = (state or self.state) / "ledger.jsonl"
        if not path.is_file():
            return []
        rows = [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [r for r in rows if kind is None or r.get("event") == kind]

    # ---------------------------------------------------------- snapshot

    @staticmethod
    def settle(repo: Path) -> None:
        """Make the index stable under a later `git status`: every tracked
        file is dated in the past and the index refreshed once, so no entry
        is racily clean and no refresh has anything left to write."""
        past = time.time() - 120
        for path in repo.rglob("*"):
            if path.is_file() and ".git" not in path.relative_to(repo).parts:
                os.utime(path, (past, past))
        subprocess.run(["git", "-C", str(repo), "update-index", "-q",
                        "--refresh"], capture_output=True, timeout=60)

    @staticmethod
    def snapshot(repo: Path, bare: bool = False) -> dict:
        state = {"refs": git(repo, "for-each-ref",
                             "--format=%(refname) %(objectname)")}
        if bare:
            return state
        state["head"] = git(repo, "rev-parse", "HEAD")
        state["index"] = (repo / ".git" / "index").read_bytes()
        state["status"] = git(repo, "status", "--porcelain=v2", "-z",
                              "--untracked-files=all")
        state["files"] = {
            p.relative_to(repo).as_posix(): p.read_bytes()
            for p in sorted(repo.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(repo).parts}
        return state


def assert_names_command(case, text: str, subcommand: str) -> None:
    """The refusal opens with the command exactly as it ran — the git
    argv, `-C <root>` and every git-wide option included — and that
    command carries `subcommand`."""
    case.assertTrue(text.startswith("`git "), text[:120])
    shown = text[1:text.index("`", 1)]
    case.assertRegex(shown, rf"(^| ){re.escape(subcommand)}( |$)")


class TimeoutAssertions(unittest.TestCase):
    """The typed refusal, asserted field by field."""

    def assert_typed(self, code, payload, stderr, *, subcommand: str,
                     ceiling: int, origin: str, configurable: bool = True):
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(code, 1, payload)
        self.assertEqual(payload.get("next_kind"), "blocked", payload)
        self.assertIsNone(payload.get("next"), payload)
        error, remedy = payload["error"], payload["remedy"]
        assert_names_command(self, error, subcommand)
        self.assertIn(f"did not finish within {ceiling} s", error)
        self.assertIn(origin, error)
        self.assertIn("never skips a repository's hooks", remedy)
        if configurable:
            self.assertIn("`[limits] git_timeout`", remedy)
            self.assertIn(f"`[tool] requires` to {config.GIT_TIMEOUT_SINCE}",
                          remedy)
            self.assertIn("also raised for an unreachable remote", remedy)
        else:
            self.assertIn("does not move its ceiling", remedy)

    def declared_in(self, ceiling: int) -> str:
        return f"[limits] git_timeout = {ceiling}, declared in repo:review.toml"


# ======================================================================
# The key: kind, default, declaration, refusal
# ======================================================================

class TestTheKey(unittest.TestCase):

    def test_undeclared_is_the_old_built_in_at_every_reader(self):
        """The default is the number every door hardcoded, so declaring
        nothing moves nothing. Asserted at the three places it can live —
        the DEFAULTS entry, the one reader, the door's own view of it."""
        scratch = Scratch(self, "git-timeout-key-")
        cfg = config.load(scratch.repo, ledger_dir=str(scratch.state))
        self.assertNotIn("limits.git_timeout", cfg.declared)
        self.assertEqual(config.DEFAULTS["limits"]["git_timeout"],
                         GIT_TIMEOUT_DEFAULT_S)
        self.assertEqual(git_timeout(cfg), GIT_TIMEOUT_DEFAULT_S)
        ceiling = git_ceiling(cfg)
        self.assertEqual(ceiling.seconds, GIT_TIMEOUT_DEFAULT_S)
        self.assertTrue(ceiling.configurable)
        self.assertIn("built-in default of [limits] git_timeout",
                      ceiling.origin)
        # The preserved number itself: 0.24.x hardcoded 120 at every door
        # that holds a configuration. A spec of the TOOL, not a restated
        # repository value — the perturbation gate perturbs review.toml.
        self.assertEqual(GIT_TIMEOUT_DEFAULT_S, 120)

    def test_a_declared_ceiling_is_read_from_the_committed_file(self):
        for declared in (1, 7, 133, 900):
            with self.subTest(declared=declared):
                scratch = Scratch(self, "git-timeout-key-",
                                  git_timeout=declared)
                cfg = config.load(scratch.repo, ledger_dir=str(scratch.state))
                self.assertIn("limits.git_timeout", cfg.declared)
                self.assertEqual(git_timeout(cfg),
                                 cfg.limits["git_timeout"])
                ceiling = git_ceiling(cfg)
                self.assertEqual(ceiling.seconds, cfg.limits["git_timeout"])
                self.assertEqual(
                    ceiling.origin,
                    f"[limits] git_timeout = {cfg.limits['git_timeout']}, "
                    f"declared in repo:review.toml")

    #: (TOML literal, the words the refusal must carry)
    INVALID = (
        ("0", "must be at least 1"),
        ("-5", "must not be negative"),
        ("true", "must be a whole number, not bool"),
        ('"120"', "must be a whole number, not str"),
        ("1.5", "must be a whole number, not float"),
        ("[120]", "must be a whole number, not list"),
        ("{ s = 120 }", "must be a whole number, not dict"),
    )

    def test_every_invalid_kind_is_refused_at_load_through_the_cli(self):
        """Each row through `python3 -m review decide` — a reading verb that
        writes nothing — so the refusal is config load's, typed, exit 2,
        naming the key and the reason; and a valid declaration is the paired
        control beside every one of them."""
        scratch = Scratch(self, "git-timeout-invalid-")
        for literal, words in self.INVALID:
            with self.subTest(value=literal):
                scratch.write_config(git_timeout=literal)
                code, payload, stderr = scratch.run("decide")
                self.assertNotIn("Traceback", stderr)
                self.assertEqual(code, 2, payload)
                self.assertEqual(payload.get("next_kind"), "blocked")
                self.assertIn("[limits] git_timeout", payload["error"])
                self.assertIn(words, payload["error"])
            with self.subTest(value=literal, control="valid"):
                scratch.write_config(git_timeout=1)
                code, payload, stderr = scratch.run("decide")
                self.assertEqual(code, 0, payload)
                self.assertNotIn("git_timeout", json.dumps(payload))

    def test_the_reader_defends_a_half_built_cfg(self):
        """`git_timeout` runs where a malformed configuration is refused
        elsewhere, so it answers with the built-in instead of raising."""
        cfg = config.load(REPO_ROOT)
        for limits in (None, {}, "not-a-table", {"git_timeout": 0},
                       {"git_timeout": -1}, {"git_timeout": True},
                       {"git_timeout": "120"}, {"git_timeout": 1.5}):
            with self.subTest(limits=limits):
                self.assertEqual(
                    git_timeout(dataclasses.replace(cfg, limits=limits)),
                    GIT_TIMEOUT_DEFAULT_S)
        self.assertEqual(git_timeout(None), GIT_TIMEOUT_DEFAULT_S)
        self.assertFalse(git_ceiling(None).configurable)

    def test_it_is_classified_for_egress_as_prose(self):
        self.assertIn("git_timeout", cli.PROSE_KEYS)
        self.assertNotIn("git_timeout", cli.RUNNABLE_KEYS)


# ======================================================================
# The doors: every door holding a configuration takes its ceiling
# ======================================================================

class TestEveryConfiguredDoorTakesTheCeiling(unittest.TestCase):
    """Structural over the source, then measured on the real functions."""

    #: A runner lambda that forwards into a git door: `lambda *a: _git(...)`.
    @staticmethod
    def _runner_lambdas(tree):
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = {a.arg for a in node.args.args + node.args.kwonlyargs}
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Lambda)
                        and isinstance(inner.body, ast.Call)
                        and isinstance(inner.body.func, ast.Name)
                        and inner.body.func.id == "_git"):
                    yield node.name, params, inner.body

    #: Doors that hold a configuration and keep the built-in in 0.25.0,
    #: each for a stated reason — never silently. `check_references` reads
    #: only the index (`ls-files`, `check-ignore`): no hook, no remote.
    #: `check_enforcement` asks the remote (`ls-remote --symref`) only under
    #: `pr-approval` and only when the clone holds no answer, and a remote
    #: that does not answer already passes there by design. A row leaves
    #: this set when its runner takes the ceiling, and a door that joins it
    #: has to be named here to pass.
    #: Empty since integration (0.25.0): `check_enforcement` and
    #: `check_references` hold a cfg, so they take the ceiling like every
    #: other such door. The set stays so a future exemption is named here.
    HELD_AT_BUILT_IN: set = set()

    def test_every_runner_of_a_door_that_holds_cfg_passes_the_ceiling(self):
        """A function that holds `cfg` and binds a runner into `_git` passes
        `ceiling=git_ceiling(cfg)`; one that holds only a root does not
        pretend to. The rule is the door's, as `caller_env`'s is."""
        configured, held = set(), set()
        for module in (emit, transport):
            tree = ast.parse(inspect.getsource(module))
            for name, params, call in self._runner_lambdas(tree):
                keywords = {k.arg: ast.unparse(k.value) for k in call.keywords}
                door = (module.__name__, name)
                with self.subTest(module=module.__name__, function=name):
                    if "cfg" not in params:
                        self.assertNotIn("ceiling", keywords)
                    elif door in self.HELD_AT_BUILT_IN:
                        held.add(door)
                        self.assertNotIn("ceiling", keywords)
                    else:
                        configured.add(door)
                        self.assertEqual(keywords.get("ceiling"),
                                         "git_ceiling(cfg)", keywords)
        self.assertEqual(held, self.HELD_AT_BUILT_IN)
        # The audit table's configured runners: ensure_pushed, _await_ci,
        # check_enforcement, check_references (held at the built-in until
        # integration) and the twelve in transport. Counted, so a door that
        # stops binding a runner is noticed rather than silently skipped.
        self.assertEqual(len(configured), 16, sorted(configured))

    def test_the_byte_doors_take_it_too(self):
        for fn in (transport._blob_of, transport.run_bytes):
            with self.subTest(door=fn.__name__):
                source = inspect.getsource(fn)
                self.assertIn("ceiling = git_ceiling(cfg)", source)
                self.assertIn("timeout=ceiling.seconds", source)

    def _recorder(self, replies=None):
        calls = []

        def run(argv, **kw):
            calls.append((list(argv), kw.get("timeout")))
            out = ""
            for sub, reply in (replies or {}).items():
                if sub in argv:
                    out = reply
            if kw.get("text"):
                return subprocess.CompletedProcess(argv, 0, out, "")
            return subprocess.CompletedProcess(argv, 0, out.encode(), b"")
        return calls, run

    def test_the_real_push_and_commit_doors_read_the_declared_ceiling(self):
        """Through the REAL functions, reading the subprocess argument —
        not a runner the test builds to look like the one they build."""
        scratch = Scratch(self, "git-timeout-doors-", git_timeout=7)
        cfg = config.load(scratch.repo, ledger_dir=str(scratch.state))
        declared = git_timeout(cfg)
        calls, run = self._recorder({"remote": "origin",
                                     "hash-object": "0" * 40})
        with mock.patch.object(transport.subprocess, "run", run):
            transport.push_envelope(cfg, "L1", 1, "request", "bytes\n")
        self.assertTrue(any("push" in argv for argv, _t in calls), calls)
        self.assertEqual({t for _a, t in calls}, {declared})
        calls, run = self._recorder({"rev-parse": "0" * 40,
                                     "--abbrev-ref": "main"})
        with mock.patch.object(emit.subprocess, "run", run):
            with self.assertRaises(Exception):
                emit.ensure_pushed(cfg)
        status = [t for argv, t in calls if "status" in argv]
        self.assertTrue(status, calls)
        self.assertEqual(set(status), {declared})

    def test_an_undeclared_ceiling_is_the_old_behaviour_at_the_door(self):
        scratch = Scratch(self, "git-timeout-doors-")
        cfg = config.load(scratch.repo, ledger_dir=str(scratch.state))
        calls, run = self._recorder({"remote": "origin",
                                     "hash-object": "0" * 40})
        with mock.patch.object(transport.subprocess, "run", run):
            transport.push_envelope(cfg, "L1", 1, "request", "bytes\n")
        self.assertEqual({t for _a, t in calls}, {GIT_TIMEOUT_DEFAULT_S})


# ======================================================================
# Every door types the timeout; every wrapper passes it through
# ======================================================================

def _expire(argv, **kw):
    raise subprocess.TimeoutExpired(argv, kw.get("timeout"))


class TestEveryDoorTypesTheTimeout(unittest.TestCase):
    """`TimeoutExpired` at each door becomes `transport.GitTimeout`, naming
    the command, the ceiling in force and where it came from."""

    def setUp(self):
        self.scratch = Scratch(self, "git-timeout-types-", git_timeout=9)
        self.cfg = config.load(self.scratch.repo,
                               ledger_dir=str(self.scratch.state))
        self.declared = git_timeout(self.cfg)

    def _typed(self, call, module, subcommand, seconds, configurable):
        with mock.patch.object(module.subprocess, "run", _expire):
            with self.assertRaises(transport.GitTimeout) as ctx:
                call()
        exc = ctx.exception
        self.assertEqual(exc.next_cmd, "")
        self.assertEqual(exc.ceiling.seconds, seconds)
        self.assertEqual(exc.ceiling.configurable, configurable)
        assert_names_command(self, str(exc), subcommand)
        self.assertIn(f"within {seconds} s", str(exc))
        return exc

    def test_the_configured_doors(self):
        repo, cfg = self.scratch.repo, self.cfg
        rows = (
            ("emit._git", lambda: emit._git(repo, "push", "origin",
                                            ceiling=git_ceiling(cfg)),
             emit, "push"),
            ("transport._git", lambda: transport._git(
                repo, "fetch", "origin", ceiling=git_ceiling(cfg)),
             transport, "fetch"),
            ("transport._blob_of", lambda: transport._blob_of(cfg, "x"),
             transport, "hash-object"),
            ("transport.run_bytes", lambda: transport.run_bytes(
                cfg, None, "cat-file", "blob", "HEAD"), transport,
             "cat-file"),
        )
        for name, call, module, sub in rows:
            with self.subTest(door=name):
                exc = self._typed(call, module, sub, self.declared, True)
                self.assertIn("declared in repo:review.toml", str(exc))

    def test_the_doors_that_hold_no_configuration(self):
        repo = self.scratch.repo
        rows = (
            ("emit._git (no cfg)", lambda: emit._git(repo, "status"), emit,
             "status", GIT_TIMEOUT_DEFAULT_S),
            ("emit._git_bytes", lambda: emit._git_bytes(repo, "show",
                                                        "HEAD:f.txt"),
             emit, "show", GIT_TIMEOUT_DEFAULT_S),
            ("emit._is_ancestor", lambda: emit._is_ancestor(repo, "a", "b"),
             emit, "merge-base", emit._ANCESTRY_TIMEOUT_S),
            ("transport._git (no cfg)", lambda: transport._git(repo, "remote"),
             transport, "remote", GIT_TIMEOUT_DEFAULT_S),
        )
        for name, call, module, sub, seconds in rows:
            with self.subTest(door=name):
                exc = self._typed(call, module, sub, seconds, False)
                self.assertIn("does not move its ceiling", exc.remedy)

    def test_config_locates_the_repository_with_a_typed_refusal(self):
        """`config._git` runs before any configuration exists, so its
        refusal is the config layer's own typed error — blocked, exit 1."""
        with mock.patch.object(config.subprocess, "run", _expire):
            with self.assertRaises(config.ConfigError) as ctx:
                config.find_repo_root(self.scratch.repo)
        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(ctx.exception.kind, "blocked")
        assert_names_command(self, str(ctx.exception),
                             "rev-parse --show-toplevel")
        self.assertIn("does not move its ceiling", ctx.exception.remedy)

    def test_the_candidate_readers_timeout_is_typed_at_its_one_caller(self):
        """`_git_raw` (the sweep preflight's reader) holds only a root and
        does not type its own timeout; `ensure_pushed`, its one caller,
        does — before anything is committed."""
        (self.scratch.repo / "f.txt").write_text("three\n", encoding="utf-8")
        real = subprocess.run

        def expire_add(argv, **kw):
            if "add" in argv and "-u" in argv:
                raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
            return real(argv, **kw)

        with mock.patch.object(emit.subprocess, "run", expire_add):
            with self.assertRaises(transport.GitTimeout) as ctx:
                emit.ensure_pushed(self.cfg)
        assert_names_command(self, str(ctx.exception), "add -u")
        self.assertIn("Nothing has been committed", str(ctx.exception))
        self.assertEqual(git(self.scratch.repo, "rev-parse", "HEAD"),
                         self.scratch.head)

    def test_the_url_userinfo_is_scrubbed_from_the_refusal(self):
        exc = transport.GitTimeout(
            ("fetch", "https://user:s3cret@example.invalid/x.git", "main"),
            git_ceiling(self.cfg))
        self.assertNotIn("s3cret", str(exc))
        self.assertIn("https://example.invalid/x.git", str(exc))


class TestEveryWrapperPassesItThrough(unittest.TestCase):
    """A caller that wraps a git failure in a refusal of its own must hand a
    `GitTimeout` on unchanged: its remedy is the true one, and a wrapper's
    ("make the remote writable", "restore access") would not be."""

    def setUp(self):
        self.scratch = Scratch(self, "git-timeout-wrap-", git_timeout=9)
        self.cfg = config.load(self.scratch.repo,
                               ledger_dir=str(self.scratch.state))
        self.timeout = transport.GitTimeout(("push",), git_ceiling(self.cfg))

    def _runner(self, fail_on: str, replies: dict):
        def run(*args):
            if args[0] == fail_on or fail_on in args:
                raise self.timeout
            for key, reply in replies.items():
                if key in args:
                    return reply
            return ""
        return run

    def _passes(self, call):
        with self.assertRaises(transport.GitTimeout) as ctx:
            call()
        self.assertIn("`[limits] git_timeout`", ctx.exception.remedy)
        return ctx.exception

    def test_the_carrier_wrappers(self):
        cfg = self.cfg
        rows = (
            ("carrier_remote: remote", lambda: transport.carrier_remote(
                cfg, git=self._runner("remote", {}))),
            ("carrier_remote: branch", lambda: transport.carrier_remote(
                cfg, git=self._runner("rev-parse",
                                      {"remote": "a\nb"}))),
            ("push_envelope: push", lambda: transport.push_envelope(
                cfg, "L1", 1, "request", "x",
                git=self._runner("push", {"remote": "origin",
                                          "hash-object": "0" * 40}))),
            ("fetch_envelope: fetch", lambda: transport.fetch_envelope(
                cfg, "L1", 1, "request",
                git=self._runner("fetch", {"remote": "origin"}))),
            ("fetch_envelope: cat-file", lambda: transport.fetch_envelope(
                cfg, "L1", 1, "request",
                git=self._runner("cat-file", {"remote": "origin"}))),
            ("current_branch", lambda: transport.current_branch(
                cfg, git=self._runner("rev-parse", {}))),
        )
        for name, call in rows:
            with self.subTest(wrapper=name):
                self._passes(call)

    def test_the_take_probe_wrappers(self):
        push = {"state": "pushed", "url": str(self.scratch.remote),
                "ref": "refs/heads/main"}
        for fail_on in ("fetch", "cat-file"):
            with self.subTest(wrapper=f"probe_target: {fail_on}"):
                self._passes(lambda: transport.probe_target(
                    self.cfg, push, "b" * 40, None, git=self._runner(
                        fail_on, {}), remotes=[str(self.scratch.remote)]))

    def test_the_legacy_anchor_wrappers(self):
        for fail_on in ("fetch", "ls-remote"):
            with self.subTest(wrapper=f"read_source_authority: {fail_on}"):
                self._passes(lambda: transport.read_source_authority(
                    self.cfg, "HEAD", git=self._runner(
                        fail_on, {"remote": "origin",
                                  "rev-parse": "c" * 40})))

    def test_the_hand_off_wrappers(self):
        """`ensure_pushed`'s push, and the observation after it, add what
        the hand-off had done — and stay the typed refusal."""
        sha = self.scratch.head
        base = {("rev-parse", "--abbrev-ref", "HEAD"): "main",
                ("rev-parse", "HEAD"): sha,
                ("status", "--porcelain"): "",
                ("remote",): "origin",
                ("remote", "get-url", "origin"): str(self.scratch.remote),
                ("push", "origin", "refs/heads/main:refs/heads/main"): "",
                ("for-each-ref",
                 "--format=%(upstream:remotename)\t%(upstream:remoteref)",
                 "refs/heads/main"): "origin\trefs/heads/main"}
        for fail_on, words in (("push", "whether main reached origin is "
                                        "unconfirmed"),
                               ("ls-remote", "could not be observed")):
            with self.subTest(wrapper=f"ensure_pushed: {fail_on}"):
                def run(*args, fail_on=fail_on):
                    if args[0] == fail_on:
                        raise self.timeout
                    if args in base:
                        return base[args]
                    raise AssertionError(f"unexpected git call {args}")

                with mock.patch.object(transport, "resolve_authority",
                                       lambda *a, **k: (self.cfg, "target")):
                    exc = self._passes(lambda: emit.ensure_pushed(
                        self.cfg, git=run))
                self.assertIn(words, str(exc))
                self.assertIn(f"made no commit (HEAD is {sha[:12]})",
                              str(exc))

    def test_the_gate_runner_wrappers(self):
        cfg = dataclasses.replace(
            self.cfg, gates=[{"id": "g", "command": ["true"],
                              "blocking": True, "attested_by": "ci"},
                             {"id": "h", "command": ["true"],
                              "blocking": True}])
        clean = {k: v for k, v in os.environ.items()
                 if k not in _RUNNER_NAMES and k != emit.CI_ENV}

        def boom(*a, **k):
            raise self.timeout

        with mock.patch.dict(os.environ, clean, clear=True):
            with mock.patch.object(emit, "_git", boom):
                with self.subTest(wrapper="run_gates: executed tree"):
                    self._passes(lambda: emit.run_gates(
                        cfg, self.scratch.head, local_only=True))
                with self.subTest(wrapper="_await_ci: CI coordinates"):
                    self._passes(lambda: emit._await_ci(
                        cfg, cfg.gates[:1], self.scratch.head, "run",
                        interval=0.01, timeout=1))


# ======================================================================
# The ceiling against a real hook, through the real entry point
# ======================================================================

class TestTheCeilingAgainstARealHook(TimeoutAssertions):
    """A `pre-push` hook that sleeps, and `python3 -m review handoff` with
    a ceiling below, at and above its sleep — and undeclared."""

    SLEEP_LONG = 3

    def _refused_with_nothing_moved(self, scratch, *extra):
        Scratch.settle(scratch.repo)
        before = Scratch.snapshot(scratch.repo)
        remote_before = Scratch.snapshot(scratch.remote, bare=True)
        code, payload, stderr = scratch.handoff(*extra)
        ceiling = scratch.ceiling()
        self.assert_typed(code, payload, stderr, subcommand="push",
                          ceiling=ceiling, origin=self.declared_in(ceiling))
        self.assertIn("The hand-off made no commit", payload["error"])
        self.assertEqual(Scratch.snapshot(scratch.repo), before)
        self.assertEqual(Scratch.snapshot(scratch.remote, bare=True),
                         remote_before)
        self.assertEqual(scratch.events(kind="request"), [])
        return payload

    def test_a_ceiling_below_the_hook_refuses_typed(self):
        scratch = Scratch(self, "git-ceiling-below-", git_timeout=1)
        scratch.hook("pre-push", sleeping_pre_push(self.SLEEP_LONG))
        self.assertLess(scratch.ceiling(), self.SLEEP_LONG)
        self._refused_with_nothing_moved(scratch)

    def test_a_ceiling_at_the_hook_refuses_typed(self):
        """Deterministic: the deadline starts after `git push` is exec'd,
        and git spawns the remote side and reads its advertisement before
        it starts the hook, so a sleep equal to the ceiling ends after it."""
        scratch = Scratch(self, "git-ceiling-at-", git_timeout=2)
        ceiling = scratch.ceiling()
        scratch.hook("pre-push", sleeping_pre_push(ceiling))
        self._refused_with_nothing_moved(scratch)

    def test_a_ceiling_above_the_hook_pushes_and_emits(self):
        scratch = Scratch(self, "git-ceiling-above-", git_timeout=5)
        scratch.hook("pre-push", sleeping_pre_push(1))
        self.assertGreater(scratch.ceiling(), 1)
        code, payload, stderr = scratch.handoff()
        self.assertEqual(code, 0, (payload, stderr))
        self.assertEqual(git(scratch.remote, "rev-parse", "main"),
                         scratch.head)
        self.assertEqual(len(scratch.events(kind="request")), 1)

    def test_undeclared_is_the_built_in_and_a_short_hook_passes(self):
        scratch = Scratch(self, "git-ceiling-undeclared-")
        scratch.hook("pre-push", sleeping_pre_push(1))
        self.assertEqual(scratch.ceiling(), GIT_TIMEOUT_DEFAULT_S)
        code, payload, stderr = scratch.handoff()
        self.assertEqual(code, 0, (payload, stderr))
        self.assertEqual(git(scratch.remote, "rev-parse", "main"),
                         scratch.head)


# ======================================================================
# Every verb that pushes or fetches
# ======================================================================

class TestEveryVerbThatPushesOrFetches(TimeoutAssertions):
    """Each row: the verb timed out through the real entry point — typed,
    blocked, the remedy naming the key, no traceback — then its paired
    control with the slow hook or remote gone."""

    CEILING = 1
    SLOW = 3

    def test_handoff_branch_push_with_outstanding_work_names_its_commit(self):
        scratch = Scratch(self, "git-verb-commit-push-",
                          git_timeout=self.CEILING)
        scratch.hook("pre-push", sleeping_pre_push(self.SLOW))
        (scratch.repo / "f.txt").write_text("three\n", encoding="utf-8")
        remote_before = Scratch.snapshot(scratch.remote, bare=True)
        code, payload, stderr = scratch.handoff()
        ceiling = scratch.ceiling()
        self.assert_typed(code, payload, stderr, subcommand="push",
                          ceiling=ceiling, origin=self.declared_in(ceiling))
        committed = git(scratch.repo, "rev-parse", "HEAD")
        self.assertNotEqual(committed, scratch.head)
        self.assertIn(f"The hand-off committed {committed[:12]} locally",
                      payload["error"])
        self.assertEqual(Scratch.snapshot(scratch.remote, bare=True),
                         remote_before)
        self.assertEqual(scratch.events(kind="request"), [])
        scratch.unhook("pre-push")
        code, payload, stderr = scratch.handoff()
        self.assertEqual(code, 0, (payload, stderr))
        self.assertEqual(git(scratch.remote, "rev-parse", "main"), committed)

    def test_handoff_commit_hook(self):
        scratch = Scratch(self, "git-verb-commit-", git_timeout=self.CEILING)
        scratch.hook("pre-commit", f"/bin/sleep {self.SLOW}\nexit 0\n")
        (scratch.repo / "f.txt").write_text("three\n", encoding="utf-8")
        code, payload, stderr = scratch.handoff()
        ceiling = scratch.ceiling()
        self.assert_typed(code, payload, stderr, subcommand="commit",
                          ceiling=ceiling, origin=self.declared_in(ceiling))
        self.assertIn("was committing its outstanding work", payload["error"])
        self.assertEqual(git(scratch.repo, "rev-parse", "HEAD"), scratch.head)
        self.assertEqual(git(scratch.remote, "rev-parse", "main"),
                         scratch.base)
        # git was killed inside pre-commit, holding the index lock: the
        # refusal names it, and removing it is the person's act.
        lock = Path(git(scratch.repo, "rev-parse", "--path-format=absolute",
                        "--git-path", "index.lock"))
        self.assertTrue(lock.exists(), "the measured case left no lock")
        self.assertIn(f"{lock} remains", payload["error"])
        lock.unlink()
        scratch.unhook("pre-commit")
        code, payload, stderr = scratch.handoff()
        self.assertEqual(code, 0, (payload, stderr))

    def test_emit_request_branch_push(self):
        scratch = Scratch(self, "git-verb-emit-", git_timeout=self.CEILING)
        scratch.hook("pre-push", sleeping_pre_push(self.SLOW))
        Scratch.settle(scratch.repo)
        before = Scratch.snapshot(scratch.repo)
        out = scratch.root / "request.md"
        argv = ("emit-request", "--claim-file", str(scratch.claim),
                "--base", scratch.base, "--out", str(out))
        code, payload, stderr = scratch.run(*argv)
        ceiling = scratch.ceiling()
        self.assert_typed(code, payload, stderr, subcommand="push",
                          ceiling=ceiling, origin=self.declared_in(ceiling))
        self.assertEqual(Scratch.snapshot(scratch.repo), before)
        self.assertFalse(out.exists())
        scratch.unhook("pre-push")
        code, payload, stderr = scratch.run(*argv)
        self.assertEqual(code, 0, (payload, stderr))
        self.assertTrue(out.is_file())

    def test_import_legacy_fetch(self):
        scratch = Scratch(self, "git-verb-import-", git_timeout=self.CEILING)
        # The remote gains a commit this clone lacks, so the fetch packs.
        other, _state, _hooks = scratch.reviewer_clone("other")
        (other / "g.txt").write_text("g\n", encoding="utf-8")
        git(other, "add", "g.txt")
        git(other, "commit", "-q", "-m", "elsewhere")
        git(other, "push", "-q", "origin", "HEAD:refs/heads/other")
        events = scratch.root / "events.jsonl"
        events.write_text("", encoding="utf-8")
        argv = ("import-legacy", str(events), "--source-commit",
                scratch.base)
        refs_before = Scratch.snapshot(scratch.repo)["refs"]
        code, payload, stderr = scratch.run(
            *argv, env=scratch.slow_remote(self.SLOW))
        ceiling = scratch.ceiling()
        self.assert_typed(code, payload, stderr, subcommand="fetch",
                          ceiling=ceiling, origin=self.declared_in(ceiling))
        self.assertEqual(Scratch.snapshot(scratch.repo)["refs"], refs_before)
        code, payload, stderr = scratch.run(*argv)
        self.assertNotIn("did not finish within", json.dumps(payload))
        self.assertNotIn("Traceback", stderr)

    def test_take_probe_fetch_by_the_stamped_url(self):
        """`take <kept request>`: the target is fetched from the URL the
        stamp names, through the probe's own wrapper."""
        scratch = Scratch(self, "git-verb-take-path-",
                          git_timeout=self.CEILING)
        # Cloned BEFORE the hand-off pushes its target, so the take's fetch
        # has a commit to transfer.
        clone, state, _hooks = scratch.reviewer_clone()
        code, rec, stderr = scratch.handoff()
        self.assertEqual(code, 0, (rec, stderr))
        argv = ("take", rec["kept"], "--as", "codex")
        refs_before = Scratch.snapshot(clone)["refs"]
        code, payload, stderr = scratch.run(
            *argv, where=clone, state=state, env=scratch.slow_remote(self.SLOW))
        ceiling = scratch.ceiling(clone)
        self.assert_typed(code, payload, stderr, subcommand="fetch",
                          ceiling=ceiling, origin=self.declared_in(ceiling))
        self.assertEqual(Scratch.snapshot(clone)["refs"], refs_before)
        self.assertEqual(scratch.events(state, "take"), [])
        code, payload, stderr = scratch.run(*argv, where=clone, state=state)
        self.assertEqual(code, 0, (payload, stderr))
        self.assertIn("fetched", payload["target"]["fetch"])


class TestTheGitCarrierRound(TimeoutAssertions):
    """The `git` carrier's whole round, each leg timed out and then run:
    the request push (`handoff`), its fetch (`take git:…`), the verdict
    push (`validate --from-target`), its fetch (`close --verdict git:…`) and
    the disposition push (`respond --out`). One walk, one row per leg: each
    leg's control is the state the next leg starts from."""

    CEILING = 1
    SLOW = 3

    def test_every_leg(self):
        scratch = Scratch(self, "git-verb-carrier-", git_timeout=self.CEILING)
        ceiling = scratch.ceiling()
        origin = self.declared_in(ceiling)

        with self.subTest(leg="handoff: the request envelope's push"):
            scratch.hook("pre-push", sleeping_pre_push(self.SLOW,
                                                       "refs/loupe/"))
            code, payload, stderr = scratch.handoff("--transport", "git")
            self.assert_typed(code, payload, stderr, subcommand="push",
                              ceiling=ceiling, origin=origin)
            self.assertIn("refs/loupe/", payload["error"])
            self.assertIn("is kept locally", payload["error"])
            self.assertEqual(scratch.events(kind="request"), [])
            scratch.unhook("pre-push")
            code, rec, stderr = scratch.handoff("--transport", "git")
            self.assertEqual(code, 0, (rec, stderr))
            reference = f"git:{rec['lineage']}/1"

        clone, state, hooks = scratch.reviewer_clone()
        with self.subTest(leg="take git:…: the request envelope's fetch"):
            refs_before = Scratch.snapshot(clone)["refs"]
            code, payload, stderr = scratch.run(
                "take", reference, "--as", "codex", where=clone, state=state,
                env=scratch.slow_remote(self.SLOW))
            self.assert_typed(code, payload, stderr, subcommand="fetch",
                              ceiling=ceiling, origin=origin)
            self.assertEqual(Scratch.snapshot(clone)["refs"], refs_before)
            code, taken, stderr = scratch.run(
                "take", reference, "--as", "codex", where=clone, state=state)
            self.assertEqual(code, 0, (taken, stderr))

        verdict = scratch.root / "verdict.md"
        verdict.write_text(verdict_text(sha=taken["sha"]), encoding="utf-8")
        with self.subTest(leg="validate --from-target: the verdict's push"):
            scratch.hook("pre-push", sleeping_pre_push(self.SLOW,
                                                       "refs/loupe/"),
                         hooks=hooks)
            code, payload, stderr = scratch.run(
                "validate", str(verdict), "--from-target", where=clone,
                state=state)
            self.assert_typed(code, payload, stderr, subcommand="push",
                              ceiling=ceiling, origin=origin)
            scratch.unhook("pre-push", hooks=hooks)
            code, ruled, stderr = scratch.run(
                "validate", str(verdict), "--from-target", where=clone,
                state=state)
            self.assertEqual(code, 0, (ruled, stderr))

        with self.subTest(leg="close --verdict git:…: the verdict's fetch"):
            refs_before = Scratch.snapshot(scratch.repo)["refs"]
            code, payload, stderr = scratch.run(
                "close", "--verdict", reference,
                env=scratch.slow_remote(self.SLOW))
            self.assert_typed(code, payload, stderr, subcommand="fetch",
                              ceiling=ceiling, origin=origin)
            self.assertEqual(Scratch.snapshot(scratch.repo)["refs"],
                             refs_before)
            self.assertEqual(scratch.events(kind="verdict"), [])
            code, closed, stderr = scratch.run("close", "--verdict",
                                               reference)
            self.assertEqual(code, 0, (closed, stderr))

        with self.subTest(leg="respond --out: the disposition's push"):
            answer = scratch.root / "d.json"
            answer.write_text(json.dumps({
                "head": taken["sha"], "round": 1, "author": "claude",
                "dispositions": [{
                    "finding_id": "F1", "disposition": "accepted",
                    "payload": {"change": "fixed", "verification": "observed",
                                "falsification": {
                                    "status": "pass",
                                    "mutation": "fails_without_fix"}}}]}),
                encoding="utf-8")
            argv = ("respond", "--verdict", reference, "--from-json",
                    str(answer), "--out", str(scratch.root / "d.md"))
            scratch.hook("pre-push", sleeping_pre_push(self.SLOW,
                                                       "disposition"))
            code, payload, stderr = scratch.run(*argv)
            self.assert_typed(code, payload, stderr, subcommand="push",
                              ceiling=ceiling, origin=origin)
            scratch.unhook("pre-push")
            code, resp, stderr = scratch.run(*argv)
            self.assertEqual(code, 0, (resp, stderr))
            self.assertIn(f"refs/loupe/{rec['lineage']}/1/disposition",
                          git(scratch.repo, "ls-remote", "origin"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

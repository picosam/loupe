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
import io
import re
import tempfile
import unittest
from pathlib import Path

from review import TOOL_NAME, TOOL_VERSION, adapters, cli, config
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


if __name__ == "__main__":
    unittest.main()

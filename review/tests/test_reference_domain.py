"""The reference wire's complete domain: path grammar and object mode.

Lineage 5 round 3 F1, the layer round 2's `test_reference_binding.py` did
not reach. That round closed the TRACKING states (ignored, untracked,
missing, absolute, escaping); this one closes the two that let a reference
pass the preflight and still arrive unusable:

  * the PATH the manifest line renders. `required file.md` is tracked,
    present and inside the root, so the preflight admitted it — and the
    whitespace-delimited line it rendered came back from `take` as
    `unrecognised`, after the handoff had committed, pushed and run every
    gate.
  * the OBJECT the two sides digest. A tracked `required.md -> ignored`
    symlink passed the same preflight, then emission digested the bytes it
    pointed at while `take` digested the link, and the reference arrived
    `mismatch`.

Both are now refused before any side effect, and both sides derive kind and
digest through the one `refs` pair, so the agreement is structural. Every
admitted class has a paired VALID control here, every refused class names
its own state, and the two mutations at the end reintroduce the exact
defects — the round-2 rendering and the worktree-following digest — to
prove these checks are what stop them.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io
import json
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

from review import cli, emit, refs, transport
from review.digest import sha256_file
from review.tests.synth import CFG


def _sh(*args, **kw):
    return subprocess.run(args, check=True, capture_output=True, text=True,
                          timeout=60, **kw).stdout.strip()


class RepoFixture(unittest.TestCase):
    """A real repository carrying one instance of every object mode the
    boundary has to rule on."""

    def setUp(self):
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="refdomain-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        _sh("git", "init", "-q", "-b", "main", str(self.repo))
        for k, v in (("user.name", "t"), ("user.email", "t@example.invalid"),
                     ("commit.gpgsign", "false"), ("core.quotePath", "true")):
            _sh("git", "-C", str(self.repo), "config", k, v)
        (self.repo / ".gitignore").write_text("/private/\n", encoding="utf-8")
        (self.repo / "private").mkdir()
        (self.repo / "private" / "ignored.md").write_text(
            "bytes no commit carries\n", encoding="utf-8")

        # The valid controls, one per admitted class.
        (self.repo / "ordinary.md").write_text("ordinary\n", encoding="utf-8")
        (self.repo / "runnable.sh").write_text("#!/bin/sh\nexit 0\n",
                                               encoding="utf-8")
        (self.repo / "runnable.sh").chmod(0o755)
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "kept.md").write_text("kept\n", encoding="utf-8")
        (self.repo / "docs" / "café.md").write_text("accented\n",
                                                    encoding="utf-8")
        (self.repo / "docs" / "it's;fine$.md").write_text("shellish\n",
                                                          encoding="utf-8")
        # The refused classes, each really present in the tree.
        (self.repo / "required file.md").write_text("spaced\n",
                                                    encoding="utf-8")
        # The symlink domain (round 4 F5): one link per referent state, so
        # the object mode is what decides, never what the link points at.
        (self.repo / "linked.md").symlink_to("private/ignored.md")
        (self.repo / "dangling.md").symlink_to("missing-target.md")
        (self.repo / "outside.md").symlink_to("../outside-target.md")
        (self.repo / "cyclic.md").symlink_to("cyclic.md")
        (self.repo / "to-untracked.md").symlink_to("loose-untracked.md")
        (self.repo / "to-tracked.md").symlink_to("ordinary.md")
        (self.repo / "sub").mkdir()
        _sh("git", "-C", str(self.repo), "add", "-A")
        # Untracked on purpose, and created AFTER `git add -A` so it stays
        # that way: the advisory tracking-state control.
        (self.repo / "loose-untracked.md").write_text("untracked\n",
                                                      encoding="utf-8")
        # A gitlink without a submodule checkout: the index entry is what the
        # target tree carries, and it is the state the boundary must name.
        _sh("git", "-C", str(self.repo), "update-index", "--add",
            "--cacheinfo", f"160000,{'a' * 40},sub")
        _sh("git", "-C", str(self.repo), "commit", "-q", "-m", "init")
        self.head = _sh("git", "-C", str(self.repo), "rev-parse", "HEAD")
        self.cfg = dataclasses.replace(CFG, repo_root=self.repo)

    def check(self, path, required=True):
        return emit.check_required_references(
            self.cfg, [{"path": path, "required": required}])

    def refusal(self, path):
        with self.assertRaises(emit.ReferenceUnbound) as ctx:
            self.check(path)
        return str(ctx.exception)


class TestPathGrammar(unittest.TestCase):
    """The admitted string domain, decided in one place (`refs.path_error`)
    and partitioned here: what the wire can carry, and what it cannot."""

    ADMITTED = (
        ("ordinary", "review.toml"),
        ("nested", "docs/nested/manifest.md"),
        ("non-ascii", "docs/café.md"),
        ("single quote", "docs/it's-fine.md"),
        ("double quote", 'docs/"quoted".md'),
        ("semicolon", "docs/a;b.md"),
        ("dollar and backtick", "docs/$x`y`.md"),
        ("dash inside", "docs/a-b.md"),
        ("backslash", "docs/a\\b.md"),
        ("dot in a name", "docs/.hidden.md"),
    )

    REFUSED = (
        ("empty", "", "empty path"),
        ("space", "required file.md", "whitespace"),
        ("tab", "docs/a\tb.md", "whitespace"),
        ("no-break space", "docs/a b.md", "whitespace"),
        ("line separator", "docs/a b.md", "whitespace"),
        ("newline", "docs/a\nb.md", "whitespace"),
        ("carriage return", "docs/a\rb.md", "whitespace"),
        ("zero-width space", "docs/a​b.md", "invisible"),
        ("right-to-left override", "docs/a‮b.md", "invisible"),
        ("delete", "docs/a\x7fb.md", "control"),
        ("C0 control", "docs/a\x01b.md", "control"),
        ("leading dash", "-rf.md", "option"),
        ("trailing slash", "docs/", "trailing"),
        ("parent segment", "docs/../outside.md", "escaping"),
        ("dot segment", "docs/./kept.md", "non-canonical"),
        ("absolute", "/etc/hosts", "absolute"),
        ("doubled separator", "docs//kept.md", "empty segment"),
    )

    def test_every_admitted_class_passes_the_grammar(self):
        for name, path in self.ADMITTED:
            with self.subTest(admitted=name):
                self.assertIsNone(refs.path_error(path))

    def test_every_refused_class_names_its_own_state(self):
        seen = set()
        for name, path, word in self.REFUSED:
            with self.subTest(refused=name):
                state = refs.path_error(path)
                self.assertIsNotNone(state, name)
                self.assertIn(word, state)
                seen.add(state)
        # Distinct states, not one message doing duty for a class it does
        # not describe: a person reading the refusal must learn which rule
        # they hit.
        self.assertGreaterEqual(len(seen), 8)

    def test_the_parser_is_built_from_the_grammar_the_preflight_enforces(self):
        # The drift this closes: two hand-written rules, one in the
        # preflight and one in the line regex, that agreed until they did
        # not. One character class, textually shared.
        self.assertIn(refs.PATH_CHARS, transport._REF_LINE_RE.pattern)

    def test_every_admitted_path_renders_a_line_the_parser_recognises(self):
        for name, path in self.ADMITTED:
            with self.subTest(admitted=name):
                line = f"  {path}  sha256:{'0' * 64}  [required] note"
                match = transport._REF_LINE_RE.match(line)
                self.assertIsNotNone(match, f"{name}: {line!r}")
                self.assertEqual(match.group("path"), path)


class TestObjectMode(RepoFixture):
    """Which target-tree objects both sides can read the same way."""

    def test_a_regular_file_is_the_valid_control(self):
        self.assertIsNone(self.check("ordinary.md"))

    def test_an_executable_file_is_admitted_like_any_regular_file(self):
        self.assertIsNone(self.check("runnable.sh"))

    def test_a_directory_prefix_is_admitted_and_never_digested(self):
        self.assertIsNone(self.check("docs"))

    def test_a_tracked_symlink_refuses_naming_both_derivations(self):
        state = self.refusal("linked.md")
        self.assertIn("symlink", state)
        self.assertIn("points at", state)

    def test_a_gitlink_refuses_naming_the_other_repository(self):
        state = self.refusal("sub")
        self.assertIn("submodule", state)

    def test_every_link_form_reaches_the_same_symlink_state(self):
        # Round 4 F5: the object's mode decides, never its referent. A
        # dangling link used to report "tracked but missing ... would carry
        # its deletion" and a link pointing outside used to report "a path
        # escaping the repository root" — both fail-closed, both false, and
        # neither remedy described the fix.
        for path in ("linked.md", "dangling.md", "outside.md", "cyclic.md",
                     "to-untracked.md", "to-tracked.md"):
            with self.subTest(link=path):
                state = self.refusal(path)
                self.assertIn("symlink", state)
                self.assertIn("points at", state)

    def test_the_states_the_link_forms_used_to_borrow_still_exist(self):
        # The paired controls: a genuinely deleted tracked file and a
        # genuinely escaping lexical path keep their own distinct messages,
        # so the reorder narrowed nothing.
        (self.repo / "ordinary.md").unlink()
        self.assertIn("deletion", self.refusal("ordinary.md"))
        self.assertIn("escaping", self.refusal("docs/../outside.md"))

    def test_a_regular_file_replaced_by_a_link_since_the_commit_refuses(self):
        # The ordering the index cannot describe: `handoff` commits what the
        # disk carries, so the target tree gets the link while `ls-files
        # --stage` still reports 100644.
        (self.repo / "ordinary.md").unlink()
        (self.repo / "ordinary.md").symlink_to("private/ignored.md")
        self.assertIn("symlink", self.refusal("ordinary.md"))

    def test_a_whitespace_path_refuses_before_the_tracking_state(self):
        # Tracked, present, inside the root — round 2's boundary admitted
        # it. The wire is what cannot carry it.
        self.assertIn("whitespace", self.refusal("required file.md"))

    def test_advisory_references_keep_every_tracking_state(self):
        # Requiredness decides a POLICY — whether unavailable evidence
        # blocks a clean verdict — so an advisory row may be ignored,
        # untracked, missing, a link or a submodule.
        for path in ("linked.md", "sub", "private/ignored.md",
                     "loose-untracked.md", "no-such-file.md"):
            with self.subTest(advisory=path):
                self.assertIsNone(self.check(path, required=False))

    def test_advisory_references_are_bound_by_the_wire_grammar(self):
        # Round 4 F4: requiredness cannot change what the LINE can carry.
        # The advisory row with a space rendered a digest and came back
        # `unrecognised:` — no advisory state at all.
        with self.assertRaises(emit.ReferenceUnbound) as ctx:
            self.check("required file.md", required=False)
        self.assertIn("whitespace", str(ctx.exception))
        self.assertIn("advisory", str(ctx.exception))
        self.assertTrue(ctx.exception.remedy)

    def test_the_render_boundary_refuses_what_the_wire_cannot_carry(self):
        # The reviewer's probe called `_reference_block` directly. The
        # preflight is upstream of it; the render boundary refuses too, so
        # no caller can produce an unparseable manifest line.
        with self.assertRaises(emit.ReferenceUnbound):
            emit._reference_block(
                self.repo,
                [{"path": "required file.md", "required": False}], self.head)


class TestEmissionAndTakeAgree(RepoFixture):
    """One derivation, so the manifest the author renders and the states
    the reviewer computes cannot disagree — for every admitted row, and for
    the modes only advisory references can still carry."""

    def _round_trip(self, paths, required=True):
        block = emit._reference_block(
            self.repo, [{"path": p, "required": required} for p in paths],
            self.head)
        probed = transport.probe_references(self.cfg, block, sha=self.head)
        return {entry["path"].rstrip("/"): entry["status"] for entry in probed}

    def test_every_admitted_row_checks_against_the_target_tree(self):
        states = self._round_trip(["ordinary.md", "runnable.sh",
                                   "docs/café.md", "docs/it's;fine$.md"])
        for path, status in states.items():
            with self.subTest(path=path):
                self.assertEqual(status, "checked (digest matches)")

    def test_a_directory_row_is_present_on_both_sides(self):
        self.assertEqual(self._round_trip(["docs"])["docs"],
                         "directory present")

    def test_a_symlink_row_agrees_instead_of_mismatching(self):
        # Advisory only — required refuses — but the derivation is shared,
        # so even the mode the preflight rejects can no longer produce two
        # different answers.
        self.assertEqual(self._round_trip(["linked.md"], required=False)
                         ["linked.md"], "checked (digest matches)")

    def test_material_no_commit_carries_is_declared_unavailable_at_emission(self):
        # The reviewer would label it unavailable anyway; saying so in the
        # manifest is the honest state, not a digest of local bytes.
        states = self._round_trip(["private/ignored.md"], required=False)
        self.assertIn("unavailable", states["private/ignored.md"].lower())

    def test_the_emitted_digest_is_the_target_trees_bytes(self):
        block = emit._reference_block(
            self.repo, [{"path": "ordinary.md"}], self.head)
        shown = subprocess.run(
            ["git", "-C", str(self.repo), "show", f"{self.head}:ordinary.md"],
            capture_output=True, timeout=60).stdout
        import hashlib
        self.assertIn(hashlib.sha256(shown).hexdigest(), block)


class TestPreflightPrecedesEverySideEffect(RepoFixture):
    """Both emitting verbs, with every downstream effect as a tripwire: a
    reference the wire cannot carry refuses before the ledger, Git, the
    gates, the cache and the record."""

    def _tripped(self, *a, **k):
        raise AssertionError("a side effect was reached past a refused "
                             "required reference")

    def _claim_file(self, path, required=True):
        claim = self.tmp / f"claim-{abs(hash((path, required)))}.json"
        claim.write_text(json.dumps(
            {"objective": "a claim whose reference cannot travel",
             "references": [{"path": path, "required": required}]}),
            encoding="utf-8")
        return str(claim)

    def _blocked(self, verb, command, path, patches, required=True):
        args = argparse.Namespace(
            claim_file=self._claim_file(path, required), base=None,
            head=None,
            local_only=False, out=None, ledger_dir=None, command=command,
            author=None, reviewer=None)
        buf = io.StringIO()
        with contextlib.ExitStack() as stack:
            for target, name in patches:
                stack.enter_context(
                    mock.patch.object(target, name, self._tripped))
            stack.enter_context(contextlib.redirect_stdout(buf))
            code = verb(args, self.cfg)
        self.assertNotEqual(code, 0)
        return json.loads(buf.getvalue())

    HANDOFF_EFFECTS = [(cli, "_ledger"), (cli, "_emit"),
                       (cli.emit, "ensure_pushed"), (cli.emit, "run_gates"),
                       (cli.transport, "cached_handoff"),
                       (cli.transport, "record_handoff"),
                       (cli.transport, "handoff_preflight")]
    EMIT_EFFECTS = [(cli, "_ledger"), (cli, "_emit"),
                    (cli.emit, "ensure_pushed"), (cli.emit, "run_gates")]

    def test_both_verbs_refuse_a_whitespace_path_before_any_side_effect(self):
        for verb, command, patches in (
                (cli.cmd_handoff, "handoff", self.HANDOFF_EFFECTS),
                (cli.cmd_emit_request, "emit-request", self.EMIT_EFFECTS)):
            with self.subTest(verb=command):
                payload = self._blocked(verb, command, "required file.md",
                                        patches)
                self.assertEqual(payload["next_kind"], "blocked")
                self.assertIn("whitespace", payload["error"])
                self.assertTrue(payload["remedy"])

    def test_both_verbs_refuse_a_symlink_reference_before_any_side_effect(self):
        for verb, command, patches in (
                (cli.cmd_handoff, "handoff", self.HANDOFF_EFFECTS),
                (cli.cmd_emit_request, "emit-request", self.EMIT_EFFECTS)):
            with self.subTest(verb=command):
                payload = self._blocked(verb, command, "linked.md", patches)
                self.assertEqual(payload["next_kind"], "blocked")
                self.assertIn("symlink", payload["error"])

    def test_both_verbs_refuse_an_advisory_wire_path_before_any_effect(self):
        # Round 4 F4: an advisory row the line cannot carry is refused at
        # the same boundary, before the same tripwires.
        for verb, command, patches in (
                (cli.cmd_handoff, "handoff", self.HANDOFF_EFFECTS),
                (cli.cmd_emit_request, "emit-request", self.EMIT_EFFECTS)):
            with self.subTest(verb=command):
                payload = self._blocked(verb, command, "required file.md",
                                        patches, required=False)
                self.assertEqual(payload["next_kind"], "blocked")
                self.assertIn("whitespace", payload["error"])
                self.assertIn("advisory", payload["error"])

    def test_the_valid_control_reaches_the_next_stage(self):
        # The paired control: with an admitted reference the preflight is
        # silent and the FIRST tripwire past it is what stops the run —
        # proof the refusals above are the boundary's, not an unrelated
        # failure earlier in the verb.
        args = argparse.Namespace(
            claim_file=self._claim_file("ordinary.md"), base=None, head=None,
            local_only=False, out=None, ledger_dir=None, command="handoff",
            author=None, reviewer=None)
        with mock.patch.object(cli, "_ledger", self._tripped):
            with self.assertRaises(AssertionError) as ctx:
                with contextlib.redirect_stdout(io.StringIO()):
                    cli.cmd_handoff(args, self.cfg)
        self.assertIn("side effect was reached", str(ctx.exception))


class TestMutations(RepoFixture):
    """Each mutation reintroduces one half of the defect and asserts the
    exact state the reviewer reproduced. They pass here because the
    reintroduction works — which is what makes the checks above load-
    bearing rather than decorative."""

    def test_admitting_whitespace_again_returns_the_unrecognised_row(self):
        with mock.patch.object(refs, "path_error", lambda raw: None):
            self.assertIsNone(self.check("required file.md"))
            block = emit._reference_block(
                self.repo, [{"path": "required file.md", "required": True}],
                self.head)
            probed = transport.probe_references(self.cfg, block,
                                                sha=self.head)
        self.assertTrue(probed[0]["status"].startswith("unrecognised:"),
                        probed)

    def test_following_the_link_first_returns_the_borrowed_states(self):
        # Round 4 F5's mutation: put the target-following checks back in
        # front of the object mode, and the two link forms report the two
        # false states the reviewer reproduced.
        real = emit._reference_object_state

        def resolve_first(repo, run, raw, candidate):
            # What the round-3 order did: exists()/resolve() decided first,
            # and the mode check was reached only for a tracked, present
            # path — which a dangling or escaping link never is.
            full = repo / candidate
            if not full.exists():
                return None
            try:
                if not full.resolve().is_relative_to(repo.resolve()):
                    return None
            except OSError:
                return None
            return real(repo, run, raw, candidate)

        with mock.patch.object(emit, "_reference_object_state",
                               resolve_first):
            self.assertIn("missing", self.refusal("dangling.md"))
            self.assertIn("escaping", self.refusal("outside.md"))

    def test_skipping_the_grammar_for_advisory_rows_returns_unrecognised(self):
        # Round 4 F4's mutation: let requiredness gate the syntax check
        # again, and the advisory row reproduces the original wire defect
        # after emission.
        original = refs.path_error

        def only_required(raw):
            return None if raw == "required file.md" else original(raw)

        with mock.patch.object(refs, "path_error", only_required):
            self.assertIsNone(self.check("required file.md", required=False))
            block = emit._reference_block(
                self.repo,
                [{"path": "required file.md", "required": False}], self.head)
            probed = transport.probe_references(self.cfg, block,
                                                sha=self.head)
        self.assertTrue(probed[0]["status"].startswith("unrecognised:"),
                        probed)

    def test_digesting_the_worktree_again_returns_the_mismatch(self):
        # The round-2 rendering: kind and digest from the working tree,
        # following the link. `take` still reads the target tree, and the
        # two answers diverge exactly where they did before.
        def worktree_block(repo_root, references, sha, **kw):
            out = []
            for ref in references:
                full = repo_root / ref["path"]
                out.append(f"  {ref['path']}  sha256:{sha256_file(full)}  "
                           f"[required]")
            return "\n".join(out)

        block = worktree_block(
            self.repo, [{"path": "linked.md", "required": False}], self.head)
        probed = transport.probe_references(self.cfg, block, sha=self.head)
        self.assertTrue(probed[0]["status"].startswith("mismatch:"), probed)


if __name__ == "__main__":
    unittest.main()

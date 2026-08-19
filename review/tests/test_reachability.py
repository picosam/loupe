"""§9bis.4 / RVW-T7: emit-request pushes the reviewed branch before emitting,
stamps the OBSERVED remote ref, and refuses every state that cannot yield a
fetchable target — because a SHA the reviewer cannot fetch is not a review
target.

Decision logic runs through an injected git runner so every refusal state is
exercised without a network or a scratch repository (and therefore under the
denied-writes rerun). One integration class runs real git against a scratch
clone with a bare path remote — no network — and self-skips, with the reason
stated, where filesystem writes are denied; that skip is enumerated in the
session's not-run list, never silent.
"""
import dataclasses
import subprocess
import tempfile
import unittest
from pathlib import Path

from review import config, validate, wire
from review.emit import _git, emit_request, ensure_pushed, next_round
from review.ledger import Ledger
from review.tests.util import REPO_ROOT

CFG = config.load(REPO_ROOT)
NO_GATES = dataclasses.replace(CFG, gates=[])

SHA = "a" * 40
UPSTREAM_FMT = "--format=%(upstream:remotename)\t%(upstream:remoteref)"


def fake_git(mapping):
    """A scripted runner: exact args tuple -> stdout, or a RuntimeError to
    raise. Unexpected calls fail the test rather than improvising."""
    calls = []

    def run(*args):
        calls.append(args)
        if args not in mapping:
            raise AssertionError(f"unexpected git call: {args}")
        value = mapping[args]
        if isinstance(value, Exception):
            raise value
        return value

    run.calls = calls
    return run


def clean_repo_map(overrides=None):
    m = {
        ("rev-parse", "--abbrev-ref", "HEAD"): "main",
        ("status", "--porcelain"): "",
        ("rev-parse", "HEAD"): SHA,
        ("remote",): "origin",
        ("for-each-ref", UPSTREAM_FMT, "refs/heads/main"):
            "origin\trefs/heads/main",
        ("remote", "get-url", "origin"): "ssh://example.invalid/x.git",
        ("push", "origin", "refs/heads/main:refs/heads/main"): "",
        ("ls-remote", "origin", "refs/heads/main"):
            f"{SHA}\trefs/heads/main",
    }
    m.update(overrides or {})
    return m


def shadow_ledger():
    ledger = Ledger.in_memory()
    ledger.add({"event": "request", "round": 1, "sha": "a" * 40, "bytes": 1})
    ledger.add({"event": "verdict", "round": 1, "sha": "a" * 40,
                "verdict": "changes requested", "bytes": 1, "finding_ids": 0})
    return ledger


def synthetic_record(sha):
    return {"state": "pushed", "branch": "main", "ref": "refs/heads/main",
            "remote": "origin", "url": "ssh://example.invalid/x.git",
            "sha": sha, "committed": False}


# A minimal claim that satisfies the §5.1 content model, so the zero-error
# assertions below test reachability and not an unrelated R-REFERENCE.
CLAIM = {"objective": "reachability stamp under test",
         "references": [{"path": "review.toml", "required": True}]}


class TestEnsurePushedDecisions(unittest.TestCase):
    """FALSIFICATION: each state §9bis.4 names refuses independently, and the
    happy path stamps the ls-remote observation, not the push exit code."""

    def test_clean_tree_with_upstream_pushes_and_observes(self):
        git = fake_git(clean_repo_map())
        record = ensure_pushed(CFG, git=git)
        self.assertEqual(record, {
            "state": "pushed", "branch": "main", "ref": "refs/heads/main",
            "remote": "origin", "url": "ssh://example.invalid/x.git",
            "sha": SHA, "committed": False})
        self.assertIn(("push", "origin", "refs/heads/main:refs/heads/main"),
                      git.calls)
        self.assertIn(("ls-remote", "origin", "refs/heads/main"), git.calls)

    def test_detached_head_refuses(self):
        git = fake_git({("rev-parse", "--abbrev-ref", "HEAD"): "HEAD"})
        with self.assertRaises(RuntimeError) as ctx:
            ensure_pushed(CFG, git=git)
        self.assertIn("detached HEAD", str(ctx.exception))

    def test_untracked_files_refuse_and_are_named(self):
        git = fake_git(clean_repo_map({
            ("status", "--porcelain"): "?? scratch.txt\n M review/emit.py"}))
        with self.assertRaises(RuntimeError) as ctx:
            ensure_pushed(CFG, git=git)
        self.assertIn("scratch.txt", str(ctx.exception))
        self.assertNotIn(("commit", "-a", "-m",
                          "emit-request: outstanding work"), git.calls,
                         "refusal must precede the commit step")

    def test_tracked_changes_are_committed_first(self):
        subject = "emit-request: outstanding work for round 2"
        git = fake_git(clean_repo_map({
            ("status", "--porcelain"): " M review/emit.py",
            ("commit", "-a", "-m", subject): ""}))
        record = ensure_pushed(CFG, round_no=2, git=git)
        self.assertTrue(record["committed"])
        self.assertIn(("commit", "-a", "-m", subject), git.calls)
        self.assertLess(git.calls.index(("commit", "-a", "-m", subject)),
                        git.calls.index(("rev-parse", "HEAD")),
                        "the target is resolved AFTER the commit")

    def test_claim_supplied_commit_subject_wins(self):
        git = fake_git(clean_repo_map({
            ("status", "--porcelain"): " M review/emit.py",
            ("commit", "-a", "-m", "authored subject"): ""}))
        record = ensure_pushed(CFG, commit_subject="authored subject", git=git)
        self.assertTrue(record["committed"])

    def test_no_upstream_single_remote_derives_destination(self):
        git = fake_git(clean_repo_map({
            ("for-each-ref", UPSTREAM_FMT, "refs/heads/main"): "\t"}))
        record = ensure_pushed(CFG, git=git)
        self.assertEqual(record["remote"], "origin")
        self.assertIn(("push", "origin", "refs/heads/main:refs/heads/main"),
                      git.calls)

    def test_no_upstream_several_remotes_refuses(self):
        git = fake_git(clean_repo_map({
            ("remote",): "origin\nbackup",
            ("for-each-ref", UPSTREAM_FMT, "refs/heads/main"): "\t"}))
        with self.assertRaises(RuntimeError) as ctx:
            ensure_pushed(CFG, git=git)
        self.assertIn("not derivable", str(ctx.exception))

    def test_upstream_with_different_remote_branch_name(self):
        git = fake_git(clean_repo_map({
            ("for-each-ref", UPSTREAM_FMT, "refs/heads/main"):
                "origin\trefs/heads/trunk",
            ("push", "origin", "refs/heads/main:refs/heads/trunk"): "",
            ("ls-remote", "origin", "refs/heads/trunk"):
                f"{SHA}\trefs/heads/trunk"}))
        record = ensure_pushed(CFG, git=git)
        self.assertEqual(record["ref"], "refs/heads/trunk",
                         "the stamp names the REMOTE ref, not the local one")

    def test_no_remote_refuses_without_the_flag(self):
        git = fake_git(clean_repo_map({("remote",): ""}))
        with self.assertRaises(RuntimeError) as ctx:
            ensure_pushed(CFG, git=git)
        self.assertIn("no remote configured", str(ctx.exception))

    def test_no_remote_with_flag_is_a_recorded_state(self):
        git = fake_git(clean_repo_map({("remote",): ""}))
        record = ensure_pushed(CFG, local_only=True, git=git)
        self.assertEqual(record["state"], "local-only")
        self.assertNotIn(("push", "origin", "refs/heads/main:refs/heads/main"),
                         git.calls, "local-only never pushes")

    def test_flag_with_a_remote_present_refuses(self):
        # The flag exists for repos with no fetchable surface, never as a
        # bypass of the push rule.
        git = fake_git(clean_repo_map())
        with self.assertRaises(RuntimeError) as ctx:
            ensure_pushed(CFG, local_only=True, git=git)
        self.assertIn("bypass", str(ctx.exception))

    def test_rejected_push_refuses_with_the_reason(self):
        git = fake_git(clean_repo_map({
            ("push", "origin", "refs/heads/main:refs/heads/main"):
                RuntimeError("git push: ! [rejected] non-fast-forward")}))
        with self.assertRaises(RuntimeError) as ctx:
            ensure_pushed(CFG, git=git)
        self.assertIn("refusing to emit", str(ctx.exception))
        self.assertIn("non-fast-forward", str(ctx.exception))

    def test_ls_remote_disagreement_refuses(self):
        git = fake_git(clean_repo_map({
            ("ls-remote", "origin", "refs/heads/main"):
                f"{'b' * 40}\trefs/heads/main"}))
        with self.assertRaises(RuntimeError) as ctx:
            ensure_pushed(CFG, git=git)
        self.assertIn("b" * 40, str(ctx.exception))

    def test_ls_remote_absent_ref_refuses(self):
        git = fake_git(clean_repo_map({
            ("ls-remote", "origin", "refs/heads/main"): ""}))
        with self.assertRaises(RuntimeError) as ctx:
            ensure_pushed(CFG, git=git)
        self.assertIn("absent", str(ctx.exception))

    def test_credentials_are_scrubbed_from_the_stamped_url(self):
        git = fake_git(clean_repo_map({
            ("remote", "get-url", "origin"):
                "https://user:token@host.invalid/x.git"}))
        record = ensure_pushed(CFG, git=git)
        self.assertEqual(record["url"], "https://host.invalid/x.git")
        self.assertNotIn("token", record["url"])

    def test_head_override_that_is_not_the_tip_refuses(self):
        git = fake_git(clean_repo_map({
            ("rev-parse", "deadbeef"): "b" * 40}))
        with self.assertRaises(RuntimeError) as ctx:
            ensure_pushed(CFG, head="deadbeef", git=git)
        self.assertIn("not the tip", str(ctx.exception))
        self.assertNotIn(("push", "origin", "refs/heads/main:refs/heads/main"),
                         git.calls)

    def test_head_override_equal_to_tip_proceeds(self):
        git = fake_git(clean_repo_map({("rev-parse", "HEAD"): SHA,
                                         ("rev-parse", "somesha"): SHA}))
        record = ensure_pushed(CFG, head="somesha", git=git)
        self.assertEqual(record["sha"], SHA)


class TestEmitRequestReachability(unittest.TestCase):
    """FALSIFICATION: emission without a reachability record raises; the
    emitted envelope carries the stamp and the validator requires it."""

    def test_emission_without_a_record_refuses(self):
        with self.assertRaises(RuntimeError) as ctx:
            emit_request(NO_GATES, shadow_ledger(), {}, base="HEAD",
                         head="HEAD")
        self.assertIn("reachability", str(ctx.exception))

    def test_stale_record_refuses(self):
        with self.assertRaises(RuntimeError) as ctx:
            emit_request(NO_GATES, shadow_ledger(), {}, base="HEAD",
                         head="HEAD", reachability=synthetic_record("c" * 40))
        self.assertIn("stale", str(ctx.exception))

    def test_envelope_carries_the_stamp_and_validates(self):
        head = _git(REPO_ROOT, "rev-parse", "HEAD")
        envelope = emit_request(
            NO_GATES, shadow_ledger(), CLAIM, base="HEAD", head="HEAD",
            reachability=synthetic_record(head))
        self.assertIn("Push:   refs/heads/main = ", envelope)
        self.assertIn("Verify: git fetch ssh://example.invalid/x.git "
                      "refs/heads/main && git cat-file -e", envelope)
        parsed = wire.parse_request(envelope)
        push = wire.parse_push_line(parsed.body)
        self.assertEqual(push["state"], "pushed")
        self.assertEqual(push["sha"], head)
        items = validate.validate_request(parsed, NO_GATES)
        self.assertEqual({i.code for i in items if i.level == "error"}, set(),
                         [i.message for i in items])

    def test_base_that_is_not_an_ancestor_refuses(self):
        # HEAD~1 as head with HEAD as base: the reviewer could fetch the
        # branch and still not compute the diff. Requires history depth 2;
        # a fresh single-commit checkout (an extraction candidate) skips
        # with the reason stated rather than failing on its history.
        try:
            parent = _git(REPO_ROOT, "rev-parse", "HEAD~1")
        except RuntimeError as exc:
            self.skipTest(f"history depth 1, no HEAD~1 ({exc})")
        with self.assertRaises(RuntimeError) as ctx:
            emit_request(NO_GATES, shadow_ledger(), {}, base="HEAD",
                         head=parent, reachability=synthetic_record(parent))
        self.assertIn("not an ancestor", str(ctx.exception))

    def test_local_only_envelope_validates_with_a_notice(self):
        head = _git(REPO_ROOT, "rev-parse", "HEAD")
        record = {"state": "local-only", "branch": "main", "sha": head,
                  "committed": False}
        envelope = emit_request(NO_GATES, shadow_ledger(), CLAIM, base="HEAD",
                                head="HEAD", reachability=record)
        self.assertIn(wire.PUSH_LOCAL_MARKER, envelope)
        items = validate.validate_request(wire.parse_request(envelope),
                                          NO_GATES)
        self.assertEqual({i.code for i in items if i.level == "error"}, set())
        self.assertIn("R-LOCAL", {i.code for i in items})

    def test_validator_rejects_a_stamp_for_a_different_sha(self):
        head = _git(REPO_ROOT, "rev-parse", "HEAD")
        envelope = emit_request(
            NO_GATES, shadow_ledger(), {}, base="HEAD", head="HEAD",
            reachability=synthetic_record(head))
        forged = envelope.replace(f"= {head} @", f"= {'b' * 40} @")
        items = validate.validate_request(wire.parse_request(forged),
                                          NO_GATES)
        self.assertIn("R-REACH-SHA",
                      {i.code for i in items if i.level == "error"})

    def test_validator_requires_the_stamp(self):
        head = _git(REPO_ROOT, "rev-parse", "HEAD")
        envelope = emit_request(
            NO_GATES, shadow_ledger(), {}, base="HEAD", head="HEAD",
            reachability=synthetic_record(head))
        stripped = "\n".join(ln for ln in envelope.splitlines()
                             if not ln.startswith(("Push:", "Verify:")))
        items = validate.validate_request(wire.parse_request(stripped),
                                          NO_GATES)
        self.assertIn("R-REACH",
                      {i.code for i in items if i.level == "error"})

    def test_next_round_derivation(self):
        self.assertEqual(next_round(shadow_ledger()), 2)
        self.assertEqual(next_round(Ledger.in_memory()), 1)


class TestRealPushIntegration(unittest.TestCase):
    """The first-real-execution instrument (slice-1 lesson 3): ensure_pushed
    against actual git — scratch clone, bare path remote, no network. Skips
    where filesystem writes are denied; the skip reason names the mode so it
    can be enumerated, never silently thinned coverage."""

    def setUp(self):
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="reach-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc}): integration "
                          f"leg runs only in the writable pass")
        self.addCleanup(self._cleanup)
        self.repo = self.tmp / "work"
        self.remote = self.tmp / "remote.git"
        self._sh("git", "init", "-q", "-b", "main", str(self.repo))
        self._sh("git", "-C", str(self.repo), "config", "user.name", "t")
        self._sh("git", "-C", str(self.repo), "config", "user.email",
                 "t@example.invalid")
        # The tool's own commit step runs in this repo; a global gpgsign
        # setting must not make the scratch commit hang on a key prompt.
        self._sh("git", "-C", str(self.repo), "config", "commit.gpgsign",
                 "false")
        (self.repo / "f.txt").write_text("one\n", encoding="utf-8")
        self._sh("git", "-C", str(self.repo), "add", "f.txt")
        self._sh("git", "-C", str(self.repo), "commit", "-q", "-m", "init")
        self._sh("git", "init", "-q", "--bare", str(self.remote))
        self._sh("git", "-C", str(self.repo), "remote", "add", "origin",
                 str(self.remote))
        self.cfg = dataclasses.replace(CFG, repo_root=self.repo)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _sh(self, *args):
        subprocess.run(args, check=True, capture_output=True, text=True,
                       timeout=60)

    def _rev(self, ref="HEAD"):
        return _git(self.repo, "rev-parse", ref)

    def test_push_commit_and_observation_end_to_end(self):
        # Dirty tracked file: committed by the tool, then pushed, then the
        # remote actually holds the target — checked against the bare repo
        # itself, not against the record.
        (self.repo / "f.txt").write_text("two\n", encoding="utf-8")
        record = ensure_pushed(self.cfg, round_no=1)
        self.assertTrue(record["committed"])
        self.assertEqual(record["sha"], self._rev())
        self.assertEqual(_git(self.remote, "rev-parse", "refs/heads/main"),
                         record["sha"])
        # Idempotent re-run on the now-clean tree: no new commit, same stamp.
        again = ensure_pushed(self.cfg, round_no=1)
        self.assertFalse(again["committed"])
        self.assertEqual(again["sha"], record["sha"])

    def test_untracked_file_refuses_for_real(self):
        (self.repo / "loose.txt").write_text("x\n", encoding="utf-8")
        with self.assertRaises(RuntimeError) as ctx:
            ensure_pushed(self.cfg)
        self.assertIn("loose.txt", str(ctx.exception))

    def test_detached_head_refuses_for_real(self):
        self._sh("git", "-C", str(self.repo), "checkout", "-q",
                 "--detach", "HEAD")
        with self.assertRaises(RuntimeError) as ctx:
            ensure_pushed(self.cfg)
        self.assertIn("detached", str(ctx.exception))

    def test_rejected_push_refuses_for_real(self):
        # The remote moves ahead via a second clone; the non-fast-forward
        # push must refuse, not silently do nothing.
        other = self.tmp / "other"
        self._sh("git", "clone", "-q", str(self.remote), str(other))
        ensure_pushed(self.cfg)  # seed the remote with main
        self._sh("git", "-C", str(other), "fetch", "-q", "origin")
        self._sh("git", "-C", str(other), "checkout", "-q", "main")
        self._sh("git", "-C", str(other), "config", "user.name", "o")
        self._sh("git", "-C", str(other), "config", "user.email",
                 "o@example.invalid")
        self._sh("git", "-C", str(other), "config", "commit.gpgsign", "false")
        (other / "f.txt").write_text("three\n", encoding="utf-8")
        self._sh("git", "-C", str(other), "commit", "-aqm", "ahead")
        self._sh("git", "-C", str(other), "push", "-q", "origin", "main")
        (self.repo / "f.txt").write_text("conflict\n", encoding="utf-8")
        with self.assertRaises(RuntimeError) as ctx:
            ensure_pushed(self.cfg, round_no=1)
        self.assertIn("refusing to emit", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

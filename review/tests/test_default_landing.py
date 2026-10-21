"""A hand-off pushes a reviewed branch only to a branch of its own name
(0.31.0; Osama, 2026-10-20 and 2026-10-21).

Measured in an adopter's repository: `git switch -c <work> origin/main`
gave the branch `origin/main` as its upstream (git's default
`branch.autoSetupMerge`), and `handoff` pushed the reviewed branch to that
upstream by an explicit refspec — a fast-forward of `main`, before the
review ran, past the repository's merge rule — under `[roles] enforcement
= "none"`. `emit.refuse_renamed_push` refuses a push to a branch of another
name under every enforcement value, before anything is committed, pushed,
run or recorded.

The rule compares two names and reads nothing else. Three review rounds of
lineage L3b14a226c9 broke the earlier design, which learnt the remote's
default branch, each by making it read the wrong repository's answer: a
second push url, a cached `refs/remotes/<remote>/HEAD` gone stale, a url
rewritten twice. Those three configurations are rows here, refused without
a read; so is a push with the remote unreachable.

The domain is how the destination is decided. It is
`_resolve_push_destination`'s answer, pushed by an explicit refspec, so
`push.default`, `branch.<name>.pushRemote`, `remote.pushDefault` and
`remote.<name>.push` cannot move it — each is a row, refused when the
upstream has another name and landing on the branch's own name when it has
the same. Push urls, url rewriting and the cached HEAD change where the
push lands or what a remote would say, never the two names, and are rows
too.

`TestTheDecision` drives the comparison; `TestTheDoor` the hand-off's
derivation through an injected runner, for states a real repository cannot
be put in on demand (a timeout). `TestAtTheVerb` runs the real `handoff`
and `emit-request` against bare path remotes, and asserts on the bytes a
refusal must leave alone: HEAD, the working tree, every ref of every
remote, and the ledger.

A local branch NAMED like the default (local `main` to the remote's `main`)
pushes to its own name and passes (Osama, 2026-10-20: "Allow it"); one row
pins that, and `enforcement = "pr-approval"` remains the declaration that
refuses it.
"""
import json
import re
import shlex
import subprocess
import unittest
from pathlib import Path

from review import config, emit, transport
from review.tests._transport_fixtures import (git_out, run_cli,
                                              scratch_loop_repo, sh)

#: This repository's configuration: a door's runner is injected, so only its
#: root and its ceiling are read from it.
CFG = config.load(Path(__file__).resolve().parents[2])

#: `_resolve_push_destination`'s upstream read, as it is spelled.
UPSTREAM_FMT = "--format=%(upstream:remotename)\t%(upstream:remoteref)"


def runner(answers):
    """A scripted runner: an exact args tuple -> stdout, or an exception to
    raise. An unscripted call fails the test, so a row proves which reads
    the decision makes as well as what it decides."""
    calls = []

    def run(*args):
        calls.append(args)
        if args not in answers:
            raise AssertionError(f"unexpected git call: {args}")
        value = answers[args]
        if isinstance(value, BaseException):
            raise value
        return value

    run.calls = calls
    return run


def timeout():
    return transport.GitTimeout(("git", "remote"),
                                config.built_in_git_ceiling())


class TestTheDecision(unittest.TestCase):
    """`refuse_renamed_push(branch, remote, merge_ref, remotes)`: two names,
    and no runner at all, so nothing can be read."""

    def refuse(self, branch, destination, remote="origin",
               remotes=("origin",)):
        with self.assertRaises(emit.RenamedPush) as ctx:
            emit.refuse_renamed_push(branch, remote,
                                     f"refs/heads/{destination}",
                                     list(remotes))
        return ctx.exception

    def test_a_destination_of_the_branchs_own_name_passes(self):
        """THE control for every refusal below, the default's own name
        included (Osama: "Allow it")."""
        for branch in ("work", "main", "release/main", "claude/a.b-c_1"):
            with self.subTest(branch=branch):
                emit.refuse_renamed_push(branch, "origin",
                                         f"refs/heads/{branch}", ["origin"])

    def test_every_other_name_refuses(self):
        """The default (`main`), a non-default branch, and the near
        misses a lax comparison would pass: a last path component, a case,
        a prefix, a suffix. git compares ref names exactly."""
        for branch, destination in (
                ("claude/agents-followups", "main"),
                ("work", "feature-v2"),
                ("main", "trunk"),
                ("work", "release/work"),
                ("release/work", "work"),
                ("x/work", "y/work"),
                ("work", "Work"),
                ("work", "work2"),
                ("work2", "work")):
            with self.subTest(branch=branch, destination=destination):
                exc = self.refuse(branch, destination)
                self.assertIn(f"origin:refs/heads/{destination}", str(exc))
                self.assertIn("a branch of another name", str(exc))

    def test_the_incident_names_the_destination_and_the_fix(self):
        exc = self.refuse("claude/agents-followups", "main")
        self.assertIn("default branch", str(exc))
        self.assertIn("Nothing has been committed, pushed, run or recorded",
                      str(exc))
        self.assertIn("`git branch --unset-upstream`", exc.remedy)
        self.assertIn("`git push -u origin refs/heads/claude/agents-followups"
                      ":refs/heads/claude/agents-followups`", exc.remedy)

    def test_the_fix_names_a_configured_remote(self):
        """With several remotes, unsetting the upstream leaves no derivable
        destination, so the explicit push is the fix; an upstream naming
        `.` (`branch.autoSetupMerge = always` from a local start point) or
        a url is no remote to `push -u` to: the sole remote stands in, and
        with several a placeholder does."""
        exc = self.refuse("work", "main", remote="publish",
                          remotes=("origin", "publish"))
        self.assertIn("`git push -u publish refs/heads/work:refs/heads/work`",
                      exc.remedy)
        self.assertNotIn("--unset-upstream", exc.remedy)
        exc = self.refuse("work", "main", remote=".", remotes=("origin",))
        self.assertIn("`git push -u origin refs/heads/work:refs/heads/work`",
                      exc.remedy)
        self.assertIn("`git branch --unset-upstream`", exc.remedy)
        exc = self.refuse("work", "main", remote=".",
                          remotes=("origin", "fork"))
        self.assertIn("`git push -u <remote> refs/heads/work:refs/heads/work`",
                      exc.remedy)

    def test_a_destination_outside_refs_heads_is_not_this_refusal(self):
        with self.assertRaisesRegex(RuntimeError,
                                    "branch identity cannot be derived") as c:
            emit.refuse_renamed_push("work", "origin", "refs/tags/work",
                                     ["origin"])
        self.assertNotIsInstance(c.exception, emit.RenamedPush)


class TestTheDoor(unittest.TestCase):
    """`check_renamed_push(cfg)`: the derivation `handoff` makes at its
    door — local configuration only. A state with no derivable destination
    passes here and meets `ensure_pushed`'s own refusal, still before its
    commit."""

    def door(self, answers):
        run = runner(answers)
        emit.check_renamed_push(CFG, git=run)
        return run

    def head(self, branch="work", remotes="origin", upstream=""):
        return {("rev-parse", "--abbrev-ref", "HEAD"): branch,
                ("remote",): remotes,
                ("for-each-ref", UPSTREAM_FMT, f"refs/heads/{branch}"):
                    upstream}

    def test_the_upstream_is_the_destination(self):
        with self.assertRaises(emit.RenamedPush):
            self.door(self.head(upstream="origin\trefs/heads/main"))
        run = self.door(self.head(upstream="origin\trefs/heads/work"))
        self.assertEqual({c[0] for c in run.calls},
                         {"rev-parse", "remote", "for-each-ref"},
                         "the door reads local configuration and nothing else")

    def test_no_upstream_and_one_remote_is_the_branchs_own_name(self):
        self.door(self.head(upstream="\t"))

    def test_states_with_no_destination_pass_to_ensure_pushed(self):
        for label, answers in (
                ("detached", {("rev-parse", "--abbrev-ref", "HEAD"): "HEAD"}),
                ("no remote", self.head(remotes="")),
                ("several remotes, no upstream",
                 self.head(remotes="origin\nfork", upstream="\t")),
                ("upstream outside refs/heads",
                 self.head(upstream="origin\trefs/tags/main")),
                ("unborn HEAD", {("rev-parse", "--abbrev-ref", "HEAD"):
                                 RuntimeError("ambiguous argument 'HEAD'")})):
            with self.subTest(label):
                self.door(answers)

    def test_a_timeout_at_the_door_is_not_a_pass(self):
        with self.assertRaises(transport.GitTimeout):
            self.door({**self.head(), ("remote",): timeout()})


class TestAtTheVerb(unittest.TestCase):
    """The real `handoff` and `emit-request`, a real repository, bare path
    remotes. `origin`'s default is `main`, pushed; the branch under review
    is cut the way the measured incident cut it."""

    BRANCH = "claude/agents-followups"
    ENFORCEMENT = ('enforcement = "none"', None, 'enforcement = "pr-approval"')

    def setUp(self):
        scratch = scratch_loop_repo(self, "landing-", objective="landing")
        self.tmp, self.repo, self.cwd = scratch.tmp, scratch.repo, scratch.cwd
        self.base, self.claim = scratch.base, scratch.claim
        self.state = self.tmp / "state"
        self.remotes = {}
        self.add_remote("origin", default="main", push=["main"])

    # ------------------------------------------------------------ fixtures

    def git(self, *args):
        sh("git", "-C", str(self.repo), *args)

    def add_remote(self, name, default, push=(), add=True):
        """A bare remote whose HEAD names `default`; each of `push` is
        pushed to it under its own name, so a default not in `push` is
        unborn there. Pushed, never fetched: git 2.48 and later create
        `refs/remotes/<remote>/HEAD` on a fetch (measured on 2.54)."""
        bare = self.tmp / f"{name}.git"
        sh("git", "init", "-q", "--bare", str(bare))
        sh("git", "-C", str(bare), "symbolic-ref", "HEAD",
           f"refs/heads/{default}")
        if add:
            self.git("remote", "add", name, str(bare))
        for branch in push:
            self.git("push", "-q", name if add else str(bare),
                     f"main:refs/heads/{branch}")
        self.remotes[name] = bare
        return bare

    def declare(self, line):
        """Rewrite `enforcement` on `main` and commit it there; None
        removes the line (an undeclared pathway resolves to `none`)."""
        path = self.repo / "review.toml"
        text = path.read_text(encoding="utf-8")
        text = "".join(ln for ln in text.splitlines(keepends=True)
                       if not ln.startswith("enforcement ="))
        if line is not None:
            text = text.replace("[roles]\n", f"[roles]\n{line}\n", 1)
        path.write_text(text, encoding="utf-8")
        self.git("commit", "-qam", "declare")
        self.git("push", "-q", "origin", "main")

    def cut(self, branch=None, start="origin/main", merge=None):
        """`git switch -c <branch> origin/main` under git's default
        `branch.autoSetupMerge`, stated here so a machine-wide setting
        cannot change what the fixture is; `merge` repoints the upstream
        at another branch of the same remote. Leaves outstanding work."""
        branch = branch or self.BRANCH
        self.git("-c", "branch.autoSetupMerge=true", "switch", "-q", "-c",
                 branch, start)
        if merge:
            self.git("config", f"branch.{branch}.merge", f"refs/heads/{merge}")
        (self.repo / "f.txt").write_text("outstanding\n", encoding="utf-8")

    def recut(self, line, merge=None):
        """Back to `main`, the enforcement line declared, the branch cut
        again: one repository serves every enforcement value of a row."""
        self.git("switch", "-q", "main")
        self.git("checkout", "-q", "--", "f.txt")
        if git_out(self.repo, "branch", "--list", self.BRANCH):
            self.git("branch", "-q", "-D", self.BRANCH)
        self.declare(line)
        self.cut(merge=merge)

    def snapshot(self):
        refs = {name: git_out(bare, "for-each-ref",
                              "--format=%(refname) %(objectname)")
                for name, bare in self.remotes.items()}
        ledger = self.state / "ledger.jsonl"
        return {"head": git_out(self.repo, "rev-parse", "HEAD"),
                "status": git_out(self.repo, "status", "--porcelain"),
                "remotes": refs,
                "ledger": ledger.read_bytes() if ledger.exists() else None}

    def handoff(self):
        return run_cli(self.repo, self.state, "handoff", "--claim-file",
                       str(self.claim), "--base", self.base, cwd=self.cwd)

    def run_verb(self, verb, out):
        return run_cli(self.repo, self.state, verb, "--claim-file",
                       str(self.claim), "--base", self.base, "--out",
                       str(out), cwd=self.cwd)

    def own_push(self, remote, branch=None):
        """The repair the refusal renders: source and destination spelt
        out, so no `remote.<name>.push` mapping can redirect it."""
        ref = f"refs/heads/{branch or self.BRANCH}"
        return f"`git push -u {remote} {ref}:{ref}`"

    def assertRefusedUntouched(self, run, destination):
        before = self.snapshot()
        code, rec = run()
        self.assertEqual(code, 1, rec)
        self.assertEqual(rec["next_kind"], "blocked", rec)
        self.assertIsNone(rec["next"], rec)
        self.assertIn("default branch", rec["error"])
        self.assertIn(destination, rec["error"])
        self.assertEqual(self.snapshot(), before,
                         "a refusal commits, pushes and records nothing")
        return rec

    def assertBothVerbsRefuse(self, destination, label):
        """`handoff` and `emit-request`, each refused untouched, with no
        request written."""
        for verb in ("handoff", "emit-request"):
            with self.subTest(label, verb=verb):
                out = self.tmp / "request.md"
                self.assertRefusedUntouched(lambda: self.run_verb(verb, out),
                                            destination)
                self.assertFalse(out.exists())

    def assertOpens(self, landed_on, receivers=("origin",)):
        """The round opens and `landed_on` holds the new HEAD in every
        receiver; every other ref of every remote is where it was."""
        before = self.snapshot()
        code, rec = self.handoff()
        self.assertEqual(code, 0, rec)
        self.assertEqual(rec["round"], 1)
        head = git_out(self.repo, "rev-parse", "HEAD")
        for name in receivers:
            self.assertEqual(git_out(self.remotes[name], "rev-parse",
                                     landed_on), head, name)
        for name, refs in before["remotes"].items():
            for line in refs.splitlines():
                ref, sha = line.split()
                if ref != landed_on or name not in receivers:
                    self.assertEqual(git_out(self.remotes[name], "rev-parse",
                                             ref), sha,
                                     f"{name}:{ref} moved")
        return rec

    # ---------------------------------------------------------------- rows

    def test_the_incident_refuses_under_every_enforcement_value(self):
        """`none` (the adopter's), undeclared, and `pr-approval` — where
        the pathway's own refusal answers first, about the same push —
        through both author verbs."""
        for line in self.ENFORCEMENT:
            self.recut(line)
            self.assertBothVerbsRefuse("origin:refs/heads/main",
                                       f"enforcement={line}")
        self.recut('enforcement = "none"')
        rec = self.assertRefusedUntouched(self.handoff,
                                          "origin:refs/heads/main")
        self.assertIn(self.own_push("origin"), rec["remedy"])
        self.assertIn("a branch of another name", rec["error"])

    def test_the_refusal_asks_no_remote(self):
        """`origin`'s url points nowhere: the refusal is the same, at once,
        and is this refusal, not a failure to reach the remote."""
        self.cut()
        self.git("remote", "set-url", "origin", str(self.tmp / "nowhere.git"))
        rec = self.assertRefusedUntouched(self.handoff,
                                          "origin:refs/heads/main")
        self.assertIn("a branch of another name", rec["error"])

    def test_a_non_default_branch_of_another_name_refuses_too(self):
        """The rule is the name, not the default: `feature-v2` exists on
        `origin` and is not its default, and the push to it is refused;
        pushed to its own name, the same branch opens."""
        self.git("push", "-q", "origin", "main:refs/heads/feature-v2")
        self.cut(merge="feature-v2")
        rec = self.assertRefusedUntouched(self.handoff,
                                          "origin:refs/heads/feature-v2")
        self.assertIn("a branch of another name", rec["error"])
        self.git("branch", "--unset-upstream")
        self.assertOpens(f"refs/heads/{self.BRANCH}")

    def test_either_remedy_opens_the_round_on_the_branchs_own_name(self):
        ref = f"refs/heads/{self.BRANCH}"
        for fix in (("branch", "--unset-upstream"),
                    ("push", "-q", "-u", "origin", f"{ref}:{ref}")):
            with self.subTest(fix=fix[0]):
                self.setUp()
                self.cut()
                self.assertRefusedUntouched(self.handoff,
                                            "origin:refs/heads/main")
                self.git(*fix)
                self.assertOpens(ref)

    def test_the_rendered_repair_lands_on_the_branchs_own_name(self):
        """Round 1 F2 of lineage L3b14a226c9: the command the refusal
        renders is RUN, under a `remote.origin.push` mapping of the branch
        onto `main` written with a full and a short source, and with none,
        with one remote and with two. It leaves `main` where it was, puts
        the work on the branch's own name, makes that the upstream, and
        the hand-off then opens. The short `git push -u origin <branch>`
        follows the mapping onto `main`, which is why it is not rendered."""
        mappings = {"no mapping": None,
                    "full source": f"refs/heads/{self.BRANCH}:refs/heads/main",
                    "short source": f"{self.BRANCH}:main"}
        for remotes in ("one remote", "two remotes"):
            for label, mapping in mappings.items():
                with self.subTest(remotes=remotes, mapping=label):
                    self.setUp()
                    if remotes == "two remotes":
                        self.add_remote("fork", default="main",
                                        push=["main"])
                    self.cut()
                    if mapping:
                        self.git("config", "remote.origin.push", mapping)
                    rec = self.assertRefusedUntouched(
                        self.handoff, "origin:refs/heads/main")
                    self.assertIn(self.own_push("origin"), rec["remedy"])
                    command = re.search(r"`(git push -u [^`]+)`",
                                        rec["remedy"]).group(1)
                    origin = self.remotes["origin"]
                    main = git_out(origin, "rev-parse", "refs/heads/main")
                    subprocess.run(shlex.split(command), cwd=self.repo,
                                   check=True, capture_output=True,
                                   timeout=60)
                    self.assertEqual(git_out(origin, "rev-parse",
                                             "refs/heads/main"), main)
                    self.assertEqual(
                        git_out(origin, "rev-parse",
                                f"refs/heads/{self.BRANCH}"),
                        git_out(self.repo, "rev-parse", "HEAD"))
                    self.assertEqual(
                        git_out(self.repo, "rev-parse", "--abbrev-ref",
                                f"{self.BRANCH}@{{upstream}}"),
                        f"origin/{self.BRANCH}")
                    self.assertOpens(f"refs/heads/{self.BRANCH}")

    def test_unsetting_the_upstream_lands_on_the_branchs_own_name(self):
        """The other rendered repair, with one remote, under both
        mappings: the hand-off's own push names its refspec, so it lands
        on the branch's own name and `main` stays where it was."""
        for mapping in (f"refs/heads/{self.BRANCH}:refs/heads/main",
                        f"{self.BRANCH}:main"):
            with self.subTest(mapping=mapping):
                self.setUp()
                self.cut()
                self.git("config", "remote.origin.push", mapping)
                self.assertRefusedUntouched(self.handoff,
                                            "origin:refs/heads/main")
                self.git("branch", "--unset-upstream")
                self.assertOpens(f"refs/heads/{self.BRANCH}")

    def test_push_configuration_cannot_move_the_push(self):
        """`push.default` in each value, `branch.<name>.pushRemote`,
        `remote.pushDefault` and `remote.origin.push`: the push is made by
        an explicit remote and refspec, so with the upstream on `main`
        each is refused, naming `origin:refs/heads/main`, and `fork` —
        the remote each would send it to — is left alone too."""
        self.add_remote("fork", default="main", push=["main"])
        self.cut()
        settings = [("push.default", v) for v in
                    ("nothing", "current", "upstream", "simple", "matching")]
        settings += [(f"branch.{self.BRANCH}.pushRemote", "fork"),
                     ("remote.pushDefault", "fork"),
                     ("remote.origin.push",
                      f"refs/heads/{self.BRANCH}:refs/heads/elsewhere")]
        for key, value in settings:
            with self.subTest(key=key, value=value):
                self.git("config", key, value)
                self.assertRefusedUntouched(self.handoff,
                                            "origin:refs/heads/main")
                self.git("config", "--unset", key)

    def test_push_configuration_on_the_branchs_own_name_opens(self):
        """The control: every setting above at once, the branch's upstream
        its own name — the round opens and lands on origin's
        `refs/heads/<branch>`, never on `fork`, `elsewhere` or `main`."""
        self.add_remote("fork", default="main", push=["main"])
        self.cut()
        ref = f"refs/heads/{self.BRANCH}"
        self.git("push", "-q", "-u", "origin", f"{ref}:{ref}")
        for key, value in (("push.default", "matching"),
                           (f"branch.{self.BRANCH}.pushRemote", "fork"),
                           ("remote.pushDefault", "fork"),
                           ("remote.origin.push", f"{ref}:refs/heads/main")):
            self.git("config", key, value)
        self.assertOpens(ref)

    def test_a_remote_other_than_origin(self):
        self.git("remote", "rename", "origin", "upstream")
        self.remotes["upstream"] = self.remotes.pop("origin")
        self.cut(start="upstream/main")
        rec = self.assertRefusedUntouched(self.handoff,
                                          "upstream:refs/heads/main")
        self.assertIn(self.own_push("upstream"), rec["remedy"])
        self.git("branch", "--unset-upstream")
        self.assertOpens(f"refs/heads/{self.BRANCH}", receivers=("upstream",))

    def test_round_1s_receivers_are_refused_without_a_read(self):
        """Round 1 F1's configuration: `origin` pushes to its own
        repository (default `main`) and to `second` (HEAD on unborn
        `trunk`); the branch tracks `trunk`. Refused through both verbs
        (the rule reads no enforcement value; the incident row walks every
        one); its own name lands in both receivers."""
        self.add_remote("second", default="trunk", push=["main"], add=False)
        for url in (self.remotes["origin"], self.remotes["second"]):
            self.git("remote", "set-url", "--add", "--push", "origin",
                     str(url))
        self.cut(merge="trunk")
        self.assertBothVerbsRefuse("origin:refs/heads/trunk", "two urls")
        self.git("branch", "--unset-upstream")
        self.assertOpens(f"refs/heads/{self.BRANCH}",
                         receivers=("origin", "second"))

    def test_round_2s_stale_caches_are_refused_without_a_read(self):
        """Round 2 F1's two histories, from a cache `fetch` wrote itself
        (`refs/remotes/origin/HEAD -> origin/main`): `origin` repointed at
        `second`, whose HEAD names unborn `trunk`; and `origin`'s own HEAD
        moved to unborn `trunk`. The branch tracks `trunk`: refused
        through both verbs; its own name opens."""
        for history in ("retarget", "default change"):
            with self.subTest(history=history):
                self.setUp()
                self.git("fetch", "-q", "origin")
                self.assertEqual(
                    git_out(self.repo, "symbolic-ref",
                            "refs/remotes/origin/HEAD"),
                    "refs/remotes/origin/main",
                    "precondition: fetch wrote the cache")
                receiver = "origin"
                if history == "retarget":
                    self.add_remote("second", default="trunk",
                                    push=["main"], add=False)
                    self.git("remote", "set-url", "origin",
                             str(self.remotes["second"]))
                    receiver = "second"
                else:
                    sh("git", "-C", str(self.remotes["origin"]),
                       "symbolic-ref", "HEAD", "refs/heads/trunk")
                self.cut(merge="trunk")
                self.assertBothVerbsRefuse("origin:refs/heads/trunk", history)
                self.git("branch", "--unset-upstream")
                self.assertOpens(f"refs/heads/{self.BRANCH}",
                                 receivers=(receiver,))

    def test_round_3s_rewritten_url_is_refused_without_a_read(self):
        """Round 3 F1's configuration: `origin`'s url is an alias that
        `url.<A>.insteadOf` rewrites to A (default `main`), and
        `url.<B>.insteadOf = <A>` would rewrite A again to B (default
        `develop`) for anything handed A's url. The branch tracks `main`:
        refused through both verbs; its own name lands on A, and A's `main`
        and B are left alone."""
        a = self.remotes["origin"]
        b = self.add_remote("b", default="develop", push=["develop"],
                            add=False)
        self.git("config", f"url.{a}.insteadOf", "local-alias:repository")
        self.git("config", f"url.{b}.insteadOf", str(a))
        self.git("remote", "set-url", "origin", "local-alias:repository")
        self.cut()
        self.assertBothVerbsRefuse("origin:refs/heads/main", "rewritten url")
        self.git("branch", "--unset-upstream")
        self.assertOpens(f"refs/heads/{self.BRANCH}")

    def test_a_branch_named_like_the_default_passes(self):
        """Osama, 2026-10-20: "Allow it" — pinned so a change is a
        decision. Local `main` to `origin/main` opens the round under
        `enforcement = "none"`."""
        self.git("branch", "-q", "-u", "origin/main")
        self.assertOpens("refs/heads/main")

    def test_detached_head_is_ensure_pusheds_refusal(self):
        self.git("checkout", "-q", "--detach")
        code, rec = self.handoff()
        self.assertNotEqual(code, 0, rec)
        self.assertIn("detached HEAD", json.dumps(rec))
        self.assertNotIn("another name", json.dumps(rec))

    def test_a_kept_request_is_not_served_once_the_upstream_moves(self):
        """The hand-off's warm path re-records a kept request and pushes
        nothing. Its door answers before that path: the round opened on
        the branch's own name, then the upstream set to `origin/main`, and
        the re-run is refused with the ledger byte-identical."""
        self.cut()
        self.git("commit", "-qam", "outstanding")
        self.git("branch", "--unset-upstream")
        self.assertOpens(f"refs/heads/{self.BRANCH}")
        self.git("branch", "-q", "-u", "origin/main")
        self.assertRefusedUntouched(self.handoff, "origin:refs/heads/main")


if __name__ == "__main__":
    unittest.main()

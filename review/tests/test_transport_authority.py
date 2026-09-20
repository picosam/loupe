"""§9bis.3 / RVW-T9: target and reference AUTHORITY — probing the target,
resolving the governing authority, classifying its origin, and the liveness
table proving the reviewed commit carries its own rules.

The liveness cluster (LIVENESS_EFFECTS, assert_live, proves_live,
evidence_cell) lives here because `TestTheReviewedCommitCarriesItsOwnRules`
is its only owner in this file; `test_lineage_reference.py` imports
`evidence_cell` from `_transport_fixtures`-adjacent scope, so it is
re-exported there.

Split from `test_transport.py` 2026-08-31; the classes are verbatim.
"""

import contextlib
import dataclasses
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from review import TOOL_NAME, cli, config, transport, validate
from review.emit import _git
from review.ledger import Ledger
from review.tests.util import REPO_ROOT
from review.tests._transport_fixtures import (
    ledgerless_cfg, reviewer_clone_git, CFG, SHA_A, SHA_B, fake_git,
    request_text)


#: The finite liveness EFFECTS, and the whole of what an effect may be.
#:
#: RVW-T19 round 2 F3. Round 1 made the table's evidence cell exact against
#: the decorator, which closed drift between the two documents and closed
#: nothing about whether either described the measurement. The effect was
#: free-form prose read only by the renderer: `@proves_live("checkout",
#: "does not convert anything")` rendered a cell that compared equal while
#: the EOL row went on measuring CRLF, so the table could still contradict
#: what its test does.
#:
#: So an effect is no longer a description of the assertion. It IS the
#: assertion. Each name maps to the surfaces it may legally describe and to
#: the PREDICATE that `assert_live` runs over what the row observed — one
#: definition, rendered into the table and executed by the test, with no
#: second statement to keep in step. Declaring an effect outside this set
#: raises at decoration time, so a free-form effect cannot reach the table;
#: declaring one whose predicate does not hold fails the owning test.
#:
#: The surface set per effect is what closes the last swap: two effects may
#: share an implementation (`converts` and `redirects the read` are both
#: "the marker appears"), and without it one could be stamped onto the
#: other's row and stay green while saying something false about it.
# The repository's own cap, READ and never restated (brief
# `handoff-guards-generalized` class 4). These tests edit the live
# `review.toml` text to make a target and a checkout disagree, and once did
# it with `.replace("round_cap = 3", ...)` beside `assertEqual(..., 3)`: the
# day the repository sets another cap, the replace is a silent no-op and six
# assertions go red over a correct tool. The probe that found them is
# `bin/config-perturbation`.
LIVE_CAP = config.load(REPO_ROOT).round_cap
OTHER_CAP = LIVE_CAP + 4      # the committed, divergent value
CHECKOUT_CAP = LIVE_CAP + 6   # the checkout's, where a substitution shows

_CAP_LINE = re.compile(r"^round_cap = \d+$", re.M)


def with_cap(toml: str, cap: int) -> str:
    """`toml` with its `round_cap` line set to `cap`; an edit that finds
    nothing to edit is a broken fixture, never a quiet one."""
    edited, count = _CAP_LINE.subn(f"round_cap = {cap}", toml)
    assert count == 1, f"expected one round_cap line, found {count}"
    return edited


LIVENESS_EFFECTS = {
    "converts": (
        frozenset({"show --textconv", "log -p", "checkout"}),
        lambda seen, mark: mark in seen),
    "writes CRLF": (
        frozenset({"checkout"}),
        lambda seen, _mark: b"\r\n" in seen),
    "writes UTF-16LE": (
        frozenset({"checkout"}),
        lambda seen, _mark: b"[\x00r\x00o\x00l\x00e\x00s\x00" in seen),
    "redirects the read": (
        frozenset({"replace"}),
        lambda seen, mark: mark in seen),
}


def assert_live(case, seen: bytes, mark: bytes) -> bytes:
    """Assert this row's mechanism is live BY RUNNING the predicate its own
    `@proves_live` effect names.

    This is the whole of F3's repair. Before it, every row asserted its
    liveness by hand and separately declared prose about it; the prose was
    checked against the table and against nothing else. Now the declaration
    performs the assertion, so a stamp that misdescribes the measurement
    cannot be green: `writes CRLF` on a row whose surface produces no CRLF
    fails here, at the row, before the table is ever read.
    """
    fn = getattr(type(case), case._testMethodName)
    surfaces, predicate = LIVENESS_EFFECTS[fn.liveness_effect]
    case.assertIn(
        fn.liveness_surface, surfaces,
        f"{case._testMethodName} declares the effect "
        f"{fn.liveness_effect!r} at the surface {fn.liveness_surface!r}, "
        f"which is not one that effect may describe")
    case.assertTrue(
        predicate(seen, mark),
        f"{case._testMethodName} declares that {fn.liveness_surface!r} "
        f"{fn.liveness_effect}, and it does not — so this row's negative "
        f"proves nothing and the table's cell is false")
    return seen


def proves_live(surface, effect=None):
    """Stamp the SURFACE at which a conversion row is proved live.

    RVW-T19, from lineage 12 round 5 F2. The conversion table in
    `design/lineage-12-domain-partition.md` states, per row and in prose,
    where its mechanism is proved live. Nothing compared that cell to
    anything: the reference check asserted only that the named test
    EXISTS, so mutating the evidence cell alone — leaving mechanism and
    test name intact — left every test green.

    The surface is now declared here, beside the test, and the
    declaration is LOAD-BEARING: `_live_at` builds the liveness
    observation from it, so a wrong declaration fails this test. The
    workbench check then requires the row's cell to name the declaration.
    Drift fails on one side or the other, and neither side is prose.

    `None` declares a row that proves no conversion live — the paired
    control — and the check requires its cell to say so.

    RVW-T19 round 2, from round 1's F3. The stamp carries the EFFECT as
    well, because the surface alone was not enough to be the authority for
    what the row says. The workbench check compared by containment —
    `assertIn(surface, evidence)` — so a cell mutated to `yes — no
    checkout occurs` still contained `checkout` and stayed green while
    contradicting the observation it claims. A token found inside a
    sentence proves the token is there, never that the sentence agrees. So
    the cell is RENDERED from the declaration by `evidence_cell` below and
    compared exactly: the prose has one source, and drift is not a thing
    the table can express.
    """
    def stamp(fn):
        # Decoration time, not test time: a free-form effect must not be
        # able to REACH the table, and an unstamped effect on a live
        # surface would leave the cell describing nothing. Raising here
        # fails collection, which is louder than one red row.
        if surface is None:
            if effect is not None:
                raise ValueError(
                    f"{fn.__name__} declares that it proves no conversion "
                    f"live and also declares the effect {effect!r}")
        elif effect not in LIVENESS_EFFECTS:
            raise ValueError(
                f"{fn.__name__} declares the effect {effect!r}, which is "
                f"not one of {sorted(LIVENESS_EFFECTS)} — an effect is a "
                f"predicate this row runs, never a sentence about it")
        elif surface not in LIVENESS_EFFECTS[effect][0]:
            raise ValueError(
                f"{fn.__name__} declares the effect {effect!r} at the "
                f"surface {surface!r}, which is not one that effect may "
                f"describe")
        fn.liveness_surface = surface
        fn.liveness_effect = effect
        return fn
    return stamp


def evidence_cell(fn) -> str:
    """The conversion table's evidence cell for a test, RENDERED from the
    stamp that test acts on.

    One value, two consumers: `_live_at` builds the liveness observation
    from the surface, and the workbench check requires the table's cell to
    be exactly this string. A wrong surface therefore fails the row's own
    test, and any prose the table carries that this does not produce fails
    the reference check — including a sentence that contains the surface
    and denies it, which is the whole of round 1's F3.

    Readable by construction rather than by permission: the sentence a
    reader sees is the sentence generated, so keeping it readable is a
    matter of what is declared here, not of remembering to update prose.

    Round 2's F3 closed the half this did not. Exact equality made the
    table and the decorator agree; it left the effect free-form prose that
    only this function read, so `("checkout", "does not convert
    anything")` rendered a cell that compared equal while the row went on
    measuring CRLF. The effect is now a key into `LIVENESS_EFFECTS` and
    the predicate there is what `assert_live` runs, so both halves of this
    string are executed by the test that owns them: a false surface fails
    through `_live_at`, and a false effect fails through its predicate.
    """
    surface = fn.liveness_surface
    if surface is None:
        if fn.liveness_effect is not None:
            raise AssertionError(
                f"{fn.__name__} declares that it proves no conversion "
                f"live and also declares an effect")
        return "n/a"
    if not fn.liveness_effect:
        raise AssertionError(
            f"{fn.__name__} evidences a row of the conversion table and "
            f"declares a surface with no effect, so its cell cannot be "
            f"rendered and the row would be checked against nothing")
    return f"yes — `{surface}` {fn.liveness_effect}"


class TestProbeTarget(unittest.TestCase):
    """FALSIFICATION: each state the reviewer-side probe names refuses
    independently; the happy path fetches, resolves target and base, and
    checks ancestry."""

    PUSH = {"state": "pushed", "ref": "refs/heads/main", "sha": SHA_B,
            "remote": "origin", "url": "ssh://example.invalid/x.git"}
    #: What the reviewer clone is a clone OF. Declared rather than read
    #: from the machine, so these tests state the two-repository question
    #: they are answering instead of inheriting the answer from whatever
    #: checkout the suite happens to run in.
    REMOTES = ["ssh://example.invalid/x.git"]

    def happy_map(self, overrides=None):
        m = {("fetch", "ssh://example.invalid/x.git", "refs/heads/main"): "",
             ("cat-file", "-e", f"{SHA_B}^{{commit}}"): "",
             ("cat-file", "-e", f"{SHA_A}^{{commit}}"): "",
             ("merge-base", "--is-ancestor", SHA_A, SHA_B): ""}
        m.update(overrides or {})
        return m

    def test_happy_path_fetches_then_resolves(self):
        git = fake_git(self.happy_map())
        rec = transport.probe_target(CFG, self.PUSH, SHA_B, SHA_A, git=git,
                                     remotes=self.REMOTES)
        self.assertEqual(rec["target"], "present")
        self.assertEqual(rec["base"], "ancestor of target")
        self.assertEqual(git.calls[0][0], "fetch")

    def test_no_stamp_refuses(self):
        with self.assertRaises(transport.Refusal):
            transport.probe_target(CFG, None, SHA_B, SHA_A, git=fake_git({}),
                                     remotes=self.REMOTES)

    def test_fetch_failure_refuses(self):
        git = fake_git(self.happy_map({
            ("fetch", "ssh://example.invalid/x.git", "refs/heads/main"):
                RuntimeError("git fetch: could not read")}))
        with self.assertRaises(transport.Refusal) as ctx:
            transport.probe_target(CFG, self.PUSH, SHA_B, SHA_A, git=git,
                                     remotes=self.REMOTES)
        self.assertIn("cannot fetch", str(ctx.exception))

    def test_target_absent_after_fetch_refuses(self):
        git = fake_git(self.happy_map({
            ("cat-file", "-e", f"{SHA_B}^{{commit}}"): RuntimeError("no")}))
        with self.assertRaises(transport.Refusal) as ctx:
            transport.probe_target(CFG, self.PUSH, SHA_B, SHA_A, git=git,
                                     remotes=self.REMOTES)
        self.assertIn("not present", str(ctx.exception))

    def test_base_absent_refuses(self):
        git = fake_git(self.happy_map({
            ("cat-file", "-e", f"{SHA_A}^{{commit}}"): RuntimeError("no")}))
        with self.assertRaises(transport.Refusal) as ctx:
            transport.probe_target(CFG, self.PUSH, SHA_B, SHA_A, git=git,
                                     remotes=self.REMOTES)
        self.assertIn("base", str(ctx.exception))

    def test_base_not_ancestor_refuses(self):
        git = fake_git(self.happy_map({
            ("merge-base", "--is-ancestor", SHA_A, SHA_B): RuntimeError("1")}))
        with self.assertRaises(transport.Refusal) as ctx:
            transport.probe_target(CFG, self.PUSH, SHA_B, SHA_A, git=git,
                                     remotes=self.REMOTES)
        self.assertIn("not an ancestor", str(ctx.exception))

    def test_no_fetch_skips_the_fetch_but_still_requires_presence(self):
        m = self.happy_map()
        del m[("fetch", "ssh://example.invalid/x.git", "refs/heads/main")]
        git = fake_git(m)
        rec = transport.probe_target(CFG, self.PUSH, SHA_B, SHA_A,
                                     fetch=False, git=git,
                                     remotes=self.REMOTES)
        self.assertEqual(rec["fetch"], "skipped (--no-fetch)")
        self.assertNotIn("fetch", [c[0] for c in git.calls])

    def test_local_only_never_fetches(self):
        m = self.happy_map()
        del m[("fetch", "ssh://example.invalid/x.git", "refs/heads/main")]
        git = fake_git(m)
        rec = transport.probe_target(CFG, {"state": "local-only"}, SHA_B,
                                     SHA_A, git=git,
                                     remotes=self.REMOTES)
        self.assertIn("LOCAL-ONLY", rec["fetch"])

    def test_local_only_absent_names_the_clone(self):
        git = fake_git({("cat-file", "-e", f"{SHA_B}^{{commit}}"):
                        RuntimeError("no")})
        with self.assertRaises(transport.Refusal) as ctx:
            transport.probe_target(CFG, {"state": "local-only"}, SHA_B,
                                   None, git=git, remotes=self.REMOTES)
        self.assertIn("not the clone", str(ctx.exception))



class TestTheClonesRepositoryIsCheckedBeforeTheFetch(unittest.TestCase):
    """FALSIFICATION (`take-binds-to-caller-checkout`, measured 2026-08-31):
    an envelope whose target is absent from the caller's repository refuses
    before the fetch and writes no event; the paired control — the same
    envelope in a clone that knows the stamped repository — reaches the
    fetch, and a taken round is recorded in that repository's ledger.

    MUTATION: drop the `remote_key(...) not in known` guard (accept every
    caller) and the foreign case is taken here exactly as it was live.
    """

    PUSH = {"state": "pushed", "ref": "refs/heads/main", "sha": SHA_B,
            "remote": "origin",
            "url": "ssh://tester@example.invalid/adopter.git"}

    def happy_map(self):
        return {("fetch", "ssh://tester@example.invalid/adopter.git",
                 "refs/heads/main"): "",
                ("cat-file", "-e", f"{SHA_B}^{{commit}}"): "",
                ("cat-file", "-e", f"{SHA_A}^{{commit}}"): "",
                ("merge-base", "--is-ancestor", SHA_A, SHA_B): ""}

    def absent_map(self):
        m = self.happy_map()
        m[("cat-file", "-e", f"{SHA_B}^{{commit}}")] = RuntimeError("no")
        return m

    def test_a_foreign_target_refuses_before_the_fetch(self):
        runner = fake_git(self.absent_map())
        with self.assertRaises(transport.Refusal) as ctx:
            transport.probe_target(CFG, self.PUSH, SHA_B, SHA_A, git=runner,
                                   remotes=["ssh://example.invalid/other-repo.git"])
        self.assertIn("not the repository the envelope names",
                      str(ctx.exception))
        self.assertNotIn("fetch", [c[0] for c in runner.calls],
                         "the fetch is what removes the signal: it must not "
                         "have run")

    def test_the_paired_control_is_a_clone_that_knows_the_remote(self):
        runner = fake_git(self.absent_map())
        # Same envelope, same absent object — a clone of the repository the
        # stamp names fetches it, which is the whole cross-machine flow.
        with self.assertRaises(transport.Refusal) as ctx:
            transport.probe_target(
                CFG, self.PUSH, SHA_B, SHA_A, git=runner,
                remotes=["https://example.invalid/adopter"])
        self.assertIn("not present in this clone", str(ctx.exception))
        self.assertIn("fetch", [c[0] for c in runner.calls])

    def test_a_present_target_is_taken_in_any_clone_that_holds_it(self):
        # The second passing state: the object is already here, so nothing
        # foreign is pulled in on the envelope's say-so.
        runner = fake_git(self.happy_map())
        rec = transport.probe_target(
            CFG, self.PUSH, SHA_B, SHA_A, git=runner,
            remotes=["ssh://example.invalid/other-repo.git"])
        self.assertEqual(rec["target"], "present")

    def test_a_clone_with_no_remote_contradicts_no_stamp(self):
        # The third passing state, and the one the empty-CI reviewer is:
        # a checkout that declares no remote claims to be no repository in
        # particular, so there is nothing for the stamp to disagree with.
        runner = fake_git(self.absent_map())
        with self.assertRaises(transport.Refusal) as ctx:
            transport.probe_target(CFG, self.PUSH, SHA_B, SHA_A, git=runner,
                                   remotes=[])
        self.assertIn("not present in this clone", str(ctx.exception))
        self.assertIn("fetch", [c[0] for c in runner.calls])

    def test_take_writes_no_event_when_the_clone_is_foreign(self):
        # The part that cannot be undone: the ledger. The live incident
        # recorded request/evidence/take for another repository's round.
        ledger = Ledger.in_memory()
        runner = fake_git({("cat-file", "-e", f"{SHA_B}^{{commit}}"):
                           RuntimeError("no")})
        with self.assertRaises(transport.Refusal):
            transport.take(ledgerless_cfg(), ledger, request_text(), "r.md",
                           reviewer="codex", git=runner,
                           remotes=["ssh://example.invalid/other.git"])
        self.assertEqual(ledger.events(), [])

    def test_take_records_the_round_in_the_clone_that_knows_the_remote(self):
        ledger = Ledger.in_memory()
        transport.take(ledgerless_cfg(), ledger, request_text(), "r.md",
                       reviewer="codex", git=reviewer_clone_git(),
                       remotes=["https://example.invalid/x"])
        self.assertEqual([e["event"] for e in ledger.events()],
                         ["request", "evidence", "take"])

    def test_one_repository_is_one_key_across_the_spellings_accepted(self):
        keys = {transport.remote_key(u) for u in (
            "tester@example.invalid:owner/widget.git",
            "https://example.invalid/owner/widget",
            "https://tester:token@example.invalid/owner/widget.git",
            "ssh://tester@example.invalid/owner/widget.git",
            "https://example.invalid/owner/widget/")}
        self.assertEqual(keys, {"example.invalid/owner/widget"})
        self.assertNotEqual(
            transport.remote_key("tester@example.invalid:x/widget.git"),
            transport.remote_key("tester@example.invalid:y/widget.git"))
        self.assertEqual(transport.remote_key("file:///tmp/r.git"),
                         transport.remote_key("/tmp/r"))


class TestGoverningAuthorityReachesTheVerdictLeg(unittest.TestCase):
    """Sweep F6's authority stopped at `take`; the reviewer could not finish
    the step `take` tells them to run.

    Live 2026-08-27: a take SUCCEEDED — target fetched, references checked,
    six findings written — and `loupe validate <verdict.md>`, which the
    procedure requires before a verdict may be handed back, answered
    T-UNDECLARED, because the reviewer was on a detached worktree carrying
    no `review.toml`. `--from-target` reaches the authority the target
    itself declares, deriving the SHA from the envelope's own stamp.

    Round 6 F1 removed the other origin entirely, and with it the record,
    the digest and the skew they were built to survive: an authority living
    on one machine cannot be shown to a second, and a review is the act of
    showing it to a second. What is left is the case that never needed
    proving.
    """

    def _cfg(self):
        return dataclasses.replace(CFG, roles=dict(CFG.roles), ledger_dir="")

    def _target_carrying_config(self):
        toml = (REPO_ROOT / "review.toml").read_text(encoding="utf-8")
        return fake_git({("cat-file", "-e", f"{SHA_B}^{{commit}}"): "",
                         ("ls-tree", "--full-tree", SHA_B, "--",
                          "review.toml"): "100644 blob 0000000\treview.toml",
                         ("show", f"{SHA_B}:review.toml"): toml})

    def _configless(self):
        return fake_git({
            ("cat-file", "-e", f"{SHA_B}^{{commit}}"): "",
            ("ls-tree", "--full-tree", SHA_B, "--", "review.toml"): ""})

    def test_the_target_supplies_the_authority_it_declares(self):
        governing = transport.governing_for(
            self._cfg(), SHA_B, git=self._target_carrying_config())
        self.assertIn(SHA_B[:12], governing.source)
        self.assertTrue(governing.taxonomy_declared)

    def test_absent_target_refuses_rather_than_falling_back(self):
        """The property `target_config` deliberately did NOT have: silence
        here would hand back this checkout's rules under the target's name."""
        def git(*args):
            raise RuntimeError("no such object")
        with self.assertRaises(transport.Refusal) as ctx:
            transport.governing_for(self._cfg(), SHA_B, git=git)
        self.assertEqual(ctx.exception.next_cmd, "")
        self.assertIn("not in this clone", str(ctx.exception))

    def test_a_target_declaring_no_rules_refuses(self):
        """Round 6 F1: the origin that could not be proved across machines
        is not narrowed, it is gone — from BOTH ends, which is round 2 F1's
        rule that the two ends refuse and accept the same states."""
        with self.assertRaises(transport.Refusal) as ctx:
            transport.governing_for(self._cfg(), SHA_B,
                                    git=self._configless())
        self.assertIn("carries no review.toml", str(ctx.exception))
        # `take` refuses the same state on a real repository, where its probe
        # is real too: TestTheReviewedCommitCarriesItsOwnRules covers it.

    def test_take_hands_over_the_authority_it_used(self):
        rec = transport.take(self._cfg(), Ledger.in_memory(), request_text(),
                             "r.md", reviewer="codex",
                             git=reviewer_clone_git())
        self.assertIn("--from-target", rec["then"])


class TestTheReviewedCommitCarriesItsOwnRules(unittest.TestCase):
    """Round 6 F1, end to end on real repositories.

    Rounds 2 to 5 built a proof that the rules a verdict was judged by were
    the rules its request was judged by, for the case where those rules live
    on the reviewer's machine rather than in the commit. Every round the
    proof held and a new seam appeared one level out — a record the far end
    could not have, a skew no report-only comparison reaches, a domain
    statement shipped only to the build that already agrees.

    The seam was never in the proof. An authority on one machine cannot be
    shown to a second, and a review is the act of showing it to a second. So
    the origin goes: a handoff refuses where the repository tracks no
    configuration, and both reviewer verbs refuse a target that declares
    none. Nothing is left to prove, and the machinery that proved it is
    deleted rather than narrowed.
    """

    def setUp(self):
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="own-rules-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.repo = self.tmp / "repo"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)],
                       check=True, capture_output=True, timeout=60)
        self.toml = (REPO_ROOT / "review.toml").read_text(encoding="utf-8")
        (self.repo / "keep.txt").write_text("keep\n", encoding="utf-8")
        self.base = self._commit("base")

    def _git(self, *args, input_text=None):
        return subprocess.run(
            ["git", "-C", str(self.repo), "-c", "user.email=t@example.invalid",
             "-c", "user.name=t", *args], check=True, capture_output=True,
            text=True, timeout=60, input=input_text).stdout.strip()

    def _commit(self, subject):
        self._git("add", "-A")
        self._git("commit", "--allow-empty", "-qm", subject)
        return self._git("rev-parse", "HEAD")

    def _cfg(self):
        return dataclasses.replace(config.load(self.repo),
                                   ledger_dir=self.tmp / "ledger")

    def _envelope(self, sha):
        blob = self._git("rev-parse", f"{sha}:keep.txt")
        digest = __import__("hashlib").sha256(subprocess.run(
            ["git", "-C", str(self.repo), "cat-file", "blob", blob],
            check=True, capture_output=True, timeout=60).stdout).hexdigest()
        return request_text(
            sha=sha, base=self.base, push=True,
            refs=f"  keep.txt  sha256:{digest}  [required] a kept file\n")

    def _validate(self, sha, *extra):
        verdict = self.tmp / "v.md"
        verdict.write_text(
            f'<loupe-review-verdict sha="{sha}">\nVERDICT: clean to '
            f"advance\n\n## findings\n\nNone\n\n## evidence "
            f"checked\n\n- took it\n</loupe-review-verdict>\n",
            encoding="utf-8")
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli.main(["--ledger-dir", str(self.tmp / "ledger"),
                                 "validate", str(verdict), *extra])
            return code, json.loads(out.getvalue())
        finally:
            os.chdir(cwd)

    def test_the_author_refuses_where_the_rules_do_not_travel(self):
        """The author's end — RVW-T17: after the commit, before the push.

        Round 6 asserted this against `handoff_preflight`, which asked
        before the commit existed and therefore PREDICTED it. The rule is
        unchanged and its site is not: `ensure_pushed` reads the commit it
        just made, with the same call `take` makes.
        """
        from review import emit as _emit
        with self.assertRaises(_emit.AuthorityAbsent) as ctx:
            _emit.ensure_pushed(self._cfg(), local_only=True)
        exc = ctx.exception
        self.assertIn("carries no review.toml", str(exc))
        self.assertIn("commits review.toml", exc.remedy)
        self.assertIn("Nothing has been pushed or emitted", exc.remedy)

    def test_a_user_level_config_opens_no_reviewed_door(self):
        """Lineage 18 round 1 F1, the runtime half: a VALID user-level
        config, resolved as this repository's source, opens none of the
        three reviewed doors while the repository tracks no review.toml.
        The doc half — the shipped specification describing exactly this
        three-door result — is test_adapters.TestConfigAuthorityClaims;
        this control is what must stay refusing while any mutation of the
        shipped prose back to the fallback claim fails that guard."""
        from review import emit as _emit
        home = self.tmp / "home"
        cfg_dir = home / ".config" / TOOL_NAME
        cfg_dir.mkdir(parents=True)
        repo_id = config.load(self.repo).repo_id
        (cfg_dir / f"{repo_id}.toml").write_text(self.toml,
                                                 encoding="utf-8")
        with unittest.mock.patch.dict(os.environ, {"HOME": str(home)}):
            loaded = config.load(self.repo)
            # Paired control: the user config is LIVE — it resolved as the
            # source and declares the taxonomy — so the refusals below are
            # about the doors, not about a config nothing read.
            self.assertIn("user config", loaded.source)
            self.assertTrue(loaded.taxonomy_declared)
            cfg = dataclasses.replace(loaded,
                                      ledger_dir=self.tmp / "user-ledger")
            with self.assertRaises(_emit.AuthorityAbsent) as author:
                _emit.ensure_pushed(cfg, local_only=True)
            self.assertIn("carries no review.toml", str(author.exception))
            head = self._git("rev-parse", "HEAD")
            with self.assertRaises(transport.Refusal) as reviewer:
                transport.governing_for(cfg, head)
            self.assertIn("carries no review.toml", str(reviewer.exception))
            code, payload = self._validate(head, "--from-target")
            self.assertNotEqual(code, 0, payload)
            self.assertIn("review.toml", payload["error"])

    def _plant_blob_replacement(self, via="replace"):
        """Commit rules saying `gemini`, then install a replacement BLOB
        saying `claude`. Returns (sha, original_bytes).

        `via` is the git subcommand that makes the mechanism live. The row
        of the conversion table this evidences passes its own declared
        surface, so a declaration that does not name what plants the
        replacement fails here rather than sailing into the table
        (RVW-T19)."""
        committed = self.toml.replace(
            'permitted_authors = ["claude", "codex"]',
            'permitted_authors = ["gemini"]')
        self.assertNotEqual(committed, self.toml)
        (self.repo / "review.toml").write_text(committed, encoding="utf-8")
        sha = self._commit("rules saying gemini")
        original = self._git("rev-parse", f"{sha}:review.toml")
        replacement = self._git("hash-object", "-w", "--stdin",
                                input_text=self.toml)
        self._git(via, original, replacement)
        return sha, committed

    @proves_live("replace", "redirects the read")
    def test_a_replacement_blob_cannot_change_the_committed_authority(self):
        """Round 1 F1 (lineage 12), the Blocker.

        `git replace` installs a ref that makes every ordinary object lookup
        return a REPLACEMENT. It is local, uncommitted, per-machine state —
        the same class of input as a filter driver — and it reaches
        `ls-tree` and `cat-file` exactly as it reaches `show`, so the
        `cat-file` switch this round's own risk paragraph proposed would not
        have helped. The resolver must read the original.

        MUTATION, asserted below rather than described: the identical read
        WITHOUT `--no-replace-objects` returns the replacement. That proves
        the planted replacement is live and that the flag is what stops it,
        so this test can fail.
        """
        sha, committed = self._plant_blob_replacement(
            via=self._declared_surface())
        governing, origin = transport.resolve_authority(self._cfg(), sha)
        self.assertEqual(origin, transport.AUTHORITY_TARGET)
        self.assertEqual(governing.roles["permitted_authors"], ["gemini"],
                         "the resolver returned the REPLACEMENT's rules")
        # The mutation: replacement processing on.
        leaked = transport._git(self.repo, "show", f"{sha}:review.toml")
        assert_live(self, leaked.encode(), b"claude")
        self.assertNotIn("gemini", leaked.split("permitted_authors")[1][:40])

    def test_a_replacement_commit_cannot_change_the_committed_authority(self):
        """The same attack one object up: replace the COMMIT, so the tree
        and therefore the config blob are reached through the replacement.
        Named separately because a guard placed on the blob read alone
        would pass the test above and fail this one.
        """
        (self.repo / "review.toml").write_text(
            self.toml.replace('permitted_authors = ["claude", "codex"]',
                              'permitted_authors = ["gemini"]'),
            encoding="utf-8")
        sha = self._commit("rules saying gemini")
        (self.repo / "review.toml").write_text(self.toml, encoding="utf-8")
        other = self._commit("rules saying claude")
        self._git("replace", sha, other)
        governing, origin = transport.resolve_authority(self._cfg(), sha)
        self.assertEqual(origin, transport.AUTHORITY_TARGET)
        self.assertEqual(governing.roles["permitted_authors"], ["gemini"])
        leaked = transport._git(self.repo, "show", f"{sha}:review.toml")
        self.assertIn("claude", leaked, "the commit replacement is not live")

    def test_an_alternate_replacement_ref_base_is_also_ignored(self):
        """`GIT_REPLACE_REF_BASE` moves the namespace `refs/replace/` lives
        in. A guard that unset or ignored only the default namespace would
        be bypassed by one environment variable; `--no-replace-objects` is
        evaluated before any base is consulted, so it is not.
        """
        import os
        from unittest import mock
        base = "refs/myreplace/"
        with mock.patch.dict(os.environ, {"GIT_REPLACE_REF_BASE": base}):
            sha, _ = self._plant_blob_replacement()
            self.assertTrue(
                self._git("for-each-ref", "--format=%(refname)",
                          base).strip(),
                "the replacement was not written to the alternate base, so "
                "this test is not exercising what it claims")
            governing, origin = transport.resolve_authority(self._cfg(), sha)
            self.assertEqual(origin, transport.AUTHORITY_TARGET)
            self.assertEqual(governing.roles["permitted_authors"], ["gemini"])
            leaked = transport._git(self.repo, "show", f"{sha}:review.toml")
            self.assertIn("claude", leaked,
                          "the alternate-base replacement is not live")

    # ---------------------------------------------------------------
    # Round 3 F2 (lineage 12). The first cut of this asserted a whole
    # class at once and exercised only part of it: the eol row could not
    # have failed (the committed bytes were already LF and the assertion
    # was that the READ had no CRLF), `working-tree-encoding` was named in
    # the reference but never configured in a test, and the `log -p` claim
    # was never exercised at all. A test that states more than it measures
    # is the same defect as a claim that does — one row per mechanism now,
    # each proving its own transformation LIVE at the surface where git is
    # supposed to apply it, then proving the blob form returns the object's
    # own bytes.
    #
    # The sentinel is deliberate: a first cut used `gemini`, which already
    # appears in this repository's `rejected_reviewers`, so both the
    # control and the assertion passed for reasons unrelated to any driver.
    MARK = "ZZCONVERTEDZZ"

    def _conversion_repo(self, attributes: str, **git_config):
        """A commit whose `review.toml` blob is LF-encoded UTF-8, with the
        attributes and config declared AFTERWARDS. Returns the sha.

        The ordering is not incidental. A conversion attribute applies in
        both directions, and declaring `working-tree-encoding=UTF-16LE`
        before the file is staged makes git read this UTF-8 file AS
        UTF-16LE on the way in and refuse. Committing the blob first
        isolates the direction these rows are about — what a CHECKOUT
        does — and leaves the object holding known bytes."""
        (self.repo / "review.toml").write_text(
            self.toml.replace('permitted_authors = ["claude", "codex"]',
                              'permitted_authors = ["claude"]'),
            encoding="utf-8", newline="\n")
        self._commit("rules, before any conversion is declared")
        (self.repo / ".gitattributes").write_text(attributes,
                                                  encoding="utf-8")
        for k, v in git_config.items():
            self._git("config", k.replace("__", "."), v)
        # ONLY the attributes file is staged. `add -A` would restage
        # `review.toml` THROUGH the conversion just declared — git would
        # read these UTF-8 bytes as UTF-16LE and either refuse or write a
        # mangled blob — and then the object under test would no longer be
        # the object these rows are about.
        self._git("add", ".gitattributes")
        self._git("commit", "-qm", "declare the conversion")
        return self._git("rev-parse", "HEAD")

    def _declared_surface(self):
        """The surface the RUNNING test declares, read off its own stamp.

        Read rather than passed, so the declaration the workbench check
        compares against the table is the same value this test acts on.
        """
        fn = getattr(type(self), self._testMethodName)
        try:
            return fn.liveness_surface
        except AttributeError:
            raise AssertionError(
                f"{self._testMethodName} evidences a row of the conversion "
                f"table and declares no liveness surface") from None

    def _live_at(self, sha):
        """The bytes the DECLARED surface produces, which is where this
        row's mechanism is supposed to fire.

        Every conversion row's liveness assertion runs through here, so a
        declaration that does not match what the row measures fails the
        row rather than passing quietly into the table.
        """
        surface = self._declared_surface()
        if surface == "show --textconv":
            return self._git("show", "--textconv",
                             f"{sha}:review.toml").encode()
        if surface == "log -p":
            return self._git("log", "-p", "-1", "--format=", "--",
                             "review.toml").encode()
        if surface == "checkout":
            (self.repo / "review.toml").unlink()
            self._git("checkout", "--", "review.toml")
            return (self.repo / "review.toml").read_bytes()
        raise AssertionError(
            f"{self._testMethodName} declares the liveness surface "
            f"{surface!r}, which no observation here can produce")

    def _blob(self, sha):
        """The object's own bytes.

        Deliberately does NOT go through `self._cfg()`: after a
        `working-tree-encoding` checkout the worktree copy is UTF-16LE and
        `config.load` cannot decode it — which is itself the point of that
        row, and would otherwise make the test fail for the reason it is
        trying to measure. Only `repo_root` is used here."""
        cfg = dataclasses.replace(CFG, repo_root=self.repo)
        return transport.run_bytes(cfg, None, "show",
                                   f"{sha}:review.toml", no_replace=True)

    @proves_live("show --textconv", "converts")
    def test_textconv_is_live_and_does_not_reach_the_read(self):
        """MUTATION: remove `diff.x.textconv` and the liveness assertion
        fails; add `--textconv` to the read and the negative fails."""
        sha = self._conversion_repo(
            "review.toml diff=x\n",
            diff__x__textconv=f"sed s/claude/{self.MARK}/")
        assert_live(self, self._live_at(sha), self.MARK.encode())
        self.assertNotIn(self.MARK.encode(), self._blob(sha))

    @proves_live("log -p", "converts")
    def test_log_p_converts_and_the_read_does_not(self):
        """The `log -p` claim, EXERCISED rather than asserted in prose:
        textconv is on by default in the log/diff family, which is what
        makes the blob form's silence a fact about the blob form rather
        than about the driver."""
        sha = self._conversion_repo(
            "review.toml diff=x\n",
            diff__x__textconv=f"sed s/claude/{self.MARK}/")
        patch = assert_live(self, self._live_at(sha), self.MARK.encode())
        self.assertNotIn(self.MARK.encode(), self._blob(sha))
        self.assertTrue(patch, "the patch is empty")

    @proves_live("checkout", "converts")
    def test_smudge_is_live_and_does_not_reach_the_read(self):
        """MUTATION: remove `filter.sm.smudge` and the checkout control
        fails."""
        sha = self._conversion_repo(
            "review.toml filter=sm\n",
            filter__sm__smudge=f"sed s/claude/{self.MARK}/",
            filter__sm__clean="cat")
        assert_live(self, self._live_at(sha), self.MARK.encode())
        self.assertNotIn(self.MARK.encode(), self._blob(sha))

    @proves_live("checkout", "writes CRLF")
    def test_eol_conversion_is_live_and_does_not_reach_the_read(self):
        """Proved from the WORKTREE bytes, which is what the first cut
        missed: the committed bytes are LF, so asserting the read has no
        CRLF could not fail. The row now asserts the checkout DOES produce
        CRLF and the read does not."""
        sha = self._conversion_repo("review.toml text eol=crlf\n")
        assert_live(self, self._live_at(sha), self.MARK.encode())
        blob = self._blob(sha)
        self.assertNotIn(b"\r\n", blob)
        self.assertIn(b"\n", blob)

    @proves_live("checkout", "writes UTF-16LE")
    def test_working_tree_encoding_is_live_and_does_not_reach_the_read(self):
        """`working-tree-encoding` re-encodes on checkout. UTF-16LE is used
        rather than UTF-16 because git requires a BOM for the latter and
        refuses outright — a refusal is not the transformation this row is
        about."""
        sha = self._conversion_repo(
            "review.toml working-tree-encoding=UTF-16LE\n")
        assert_live(self, self._live_at(sha), self.MARK.encode())
        blob = self._blob(sha)
        self.assertIn(b"[roles]", blob,
                      "the read was re-encoded")
        self.assertNotIn(b"[\x00r\x00", blob)

    @proves_live(None)
    def test_no_conversion_is_the_paired_control(self):
        """The control every row above needs: with no attributes and no
        drivers, the read returns the same bytes and the authority
        resolves normally, so none of the negatives is passing because the
        read is broken."""
        sha = self._conversion_repo("")
        blob = self._blob(sha)
        self.assertIn(b'permitted_authors = ["claude"]', blob)
        governing, origin = transport.resolve_authority(self._cfg(), sha)
        self.assertEqual(origin, transport.AUTHORITY_TARGET)
        self.assertEqual(governing.roles["permitted_authors"], ["claude"])

    def test_no_replacement_is_the_paired_control(self):
        """The control the falsification requires: the same repository with
        NO replacement refs resolves the valid target exactly as before, so
        the hardening refuses nothing it should admit."""
        (self.repo / "review.toml").write_text(self.toml, encoding="utf-8")
        sha = self._commit("rules")
        self.assertEqual(
            self._git("for-each-ref", "--format=%(refname)",
                      "refs/replace/").strip(), "")
        governing, origin = transport.resolve_authority(self._cfg(), sha)
        self.assertEqual(origin, transport.AUTHORITY_TARGET)
        self.assertTrue(governing.taxonomy_declared)

    def test_a_duplicated_tree_entry_is_refused_not_guessed(self):
        """A tree carrying `review.toml` TWICE.

        `git mktree` accepts duplicate entries and `ls-tree` prints two
        lines for them. The listing parser could not tell that from one
        line — `split(maxsplit=3)` yields four fields either way — so only
        the first entry's mode was ever checked and the second rode along
        unread. Reproduced with `mktree` below.

        Honest bound, and it is why the refusal says "not established"
        rather than naming an exploit: `git show` also resolves the first
        entry, so no divergence between the two ends is demonstrated. What
        is wrong is that the agreement rests on an undocumented tie-break
        nothing asked about.
        """
        from review import emit as _emit
        one = self._git("hash-object", "-w", "--stdin", input_text=self.toml)
        two = self._git("hash-object", "-w", "--stdin",
                        input_text=self.toml + "\n# second\n")
        listing = (f"100644 blob {one}\treview.toml\n"
                   f"100644 blob {two}\treview.toml\n")
        tree = self._git("mktree", "--missing", input_text=listing)
        sha = self._git("commit-tree", tree, "-m", "duplicated")
        with self.assertRaises(transport.Refusal) as ctx:
            transport.resolve_authority(self._cfg(), sha)
        self.assertIn("more than one entry", str(ctx.exception))

    def test_a_committed_config_is_the_paired_control(self):
        from review import emit as _emit
        (self.repo / "review.toml").write_text(self.toml, encoding="utf-8")
        self._commit("rules")
        record = _emit.ensure_pushed(self._cfg(), local_only=True)
        self.assertTrue(record["governing"].taxonomy_declared)

    def test_the_assume_unchanged_divergence_is_caught(self):
        """RVW-T17's SIXTH face, and the one that needs no hook, no filter
        and no attribute — only a stock git index bit.

        `git update-index --assume-unchanged review.toml` makes git presume
        the file has not changed. `status --porcelain` then reports a CLEAN
        tree, every prospective check passes (the path is tracked, is a
        regular file, carries no filter attribute, and hashes identically
        to itself), and `git commit -a` records the INDEX version while
        `config.load` read the WORKTREE version. Author and reviewer are
        then governed by different bytes at one SHA — round 2 F1's
        asymmetry, reached without any of the five mechanisms rounds 7 to
        11 closed.

        FALSIFICATION: resolve the authority from `cfg` instead of from the
        commit and this fails, because `cfg` is the worktree's.
        """
        from review import emit as _emit
        committed = self.toml.replace(
            'permitted_authors = ["claude", "codex"]',
            'permitted_authors = ["gemini"]')
        self.assertNotEqual(committed, self.toml, "fixture no longer edits "
                                                  "the value it means to")
        (self.repo / "review.toml").write_text(committed, encoding="utf-8")
        self._commit("rules the reviewer will read")
        self._git("update-index", "--assume-unchanged", "review.toml")
        (self.repo / "review.toml").write_text(self.toml, encoding="utf-8")
        self.assertEqual(self._git("status", "--porcelain"), "",
                         "the divergence is invisible to status, which is "
                         "why nothing before the commit could see it")
        # What the author's own checkout resolves — the worktree bytes.
        self.assertIn("claude", self._cfg().roles["permitted_authors"])
        # Round 1 F2: the committed authority excludes the checkout's
        # default author, so the emission stops HERE — after the commit,
        # before the push and before any gate. Under the pre-F2 code this
        # emitted cleanly, stamped `claude`, and the refusal arrived at the
        # reviewer's `take` as R-AUTHOR-UNPERMITTED after a human had
        # already carried the envelope.
        with self.assertRaises(_emit.RoleSelectionError) as ctx:
            _emit.ensure_pushed(self._cfg(), local_only=True)
        self.assertIn("gemini", str(ctx.exception))

    def test_the_assume_unchanged_bytes_govern_when_roles_still_permit(self):
        """The same divergence with the roles left alone, so the assertion
        is about WHICH BYTES govern rather than about the refusal.

        FALSIFICATION: resolve the authority from `cfg` instead of from the
        commit and this fails, because `cfg` is the worktree's.
        """
        from review import emit as _emit
        committed = with_cap(self.toml, OTHER_CAP)
        self.assertNotEqual(committed, self.toml,
                            "fixture no longer edits the value it means to")
        (self.repo / "review.toml").write_text(committed, encoding="utf-8")
        self._commit("rules with a different cap")
        self._git("update-index", "--assume-unchanged", "review.toml")
        (self.repo / "review.toml").write_text(self.toml, encoding="utf-8")
        self.assertEqual(self._git("status", "--porcelain"), "")
        self.assertEqual(self._cfg().round_cap, LIVE_CAP)
        record = _emit.ensure_pushed(self._cfg(), local_only=True)
        self.assertEqual(
            record["governing"].round_cap, OTHER_CAP,
            "the emission must be governed by what the COMMIT records, "
            "not by what this worktree happens to hold")

    def test_a_filter_attribute_needs_no_refusal_of_its_own(self):
        """The transformation axis collapses (RVW-T17).

        Rounds 8, 9 and 10 each added machinery to establish what a filter
        WOULD do to the bytes on their way into a commit. Reading the
        commit makes the question moot: whatever the filter did, the commit
        records something, and that something is what both ends read. This
        asserts the collapse directly — a declared filter driver is present
        and the boundary neither refuses nor cares.
        """
        from review import emit as _emit
        (self.repo / ".gitattributes").write_text(
            "review.toml filter=mangle\n", encoding="utf-8")
        self._git("config", "filter.mangle.clean", "cat")
        (self.repo / "review.toml").write_text(self.toml, encoding="utf-8")
        self._commit("rules under a filter")
        record = _emit.ensure_pushed(self._cfg(), local_only=True)
        self.assertTrue(record["governing"].taxonomy_declared)

    def test_the_skip_worktree_false_refusal_is_gone(self):
        """A live FALSE refusal the prediction produced.

        With `skip-worktree` set and the worktree copy removed,
        `prospective_authority` refused "deleted in the working tree" — but
        `git commit -a` provably does not delete such an entry, so a
        perfectly valid handoff was blocked. Reading the commit gets it
        right for the same reason it gets everything else right: it looks.
        """
        from review import emit as _emit
        (self.repo / "review.toml").write_text(self.toml, encoding="utf-8")
        self._commit("rules")
        self._git("update-index", "--skip-worktree", "review.toml")
        (self.repo / "review.toml").unlink()
        record = _emit.ensure_pushed(self._cfg(), local_only=True)
        self.assertTrue(
            record["governing"].taxonomy_declared,
            "the commit carries the rules; refusing here refuses a valid "
            "handoff on the strength of a guess about the worktree")

    def test_both_reviewer_verbs_refuse_a_target_declaring_no_rules(self):
        sha = self._commit("no rules")
        (self.repo / "review.toml").write_text(self.toml, encoding="utf-8")
        with self.assertRaises(transport.Refusal) as ctx:
            transport.take(self._cfg(), Ledger(self.tmp / "ledger"),
                           self._envelope(sha), "r.md", reviewer="codex",
                           fetch=False)
        self.assertIn("carries no review.toml", str(ctx.exception))
        code, payload = self._validate(sha, "--from-target")
        self.assertNotEqual(code, 0, payload)
        self.assertEqual(payload["next_kind"], "blocked", payload)
        self.assertNotIn("items", payload, payload)

    def test_the_printed_command_validates_a_commit_carrying_its_rules(self):
        (self.repo / "review.toml").write_text(self.toml, encoding="utf-8")
        sha = self._commit("rules")
        rec = transport.take(self._cfg(), Ledger(self.tmp / "ledger"),
                             self._envelope(sha), "r.md", reviewer="codex",
                             fetch=False)
        self.assertIn("--from-target", rec["then"])
        code, payload = self._validate(sha, "--from-target")
        codes = [i["code"] for i in payload.get("items", [])]
        self.assertIn("T-AUTHORITY", codes, payload)
        self.assertNotIn("T-UNDECLARED", codes, payload)

    def test_without_the_flag_nothing_about_the_default_changed(self):
        (self.repo / "review.toml").write_text(self.toml, encoding="utf-8")
        sha = self._commit("rules")
        (self.repo / "review.toml").unlink()
        code, payload = self._validate(sha)
        self.assertNotIn("T-AUTHORITY",
                         [i["code"] for i in payload.get("items", [])])


class TestAuthorityOriginIsClassified(unittest.TestCase):
    """Round 3 F2 and F3: absence and unreadability were one branch.

    `_git` turns every nonzero git exit into `RuntimeError`, and the old
    resolver caught that whole class as "the target carries no review.toml".
    So a present entry whose blob object was missing answered with the
    CHECKOUT's rules — measured: target `round_cap` 3, checkout 9, and the
    checkout won. Round 3's digest could not see it, because both ends asked
    the same misclassifying question; a green continuity proof sat on top.
    Invalid UTF-8 escaped further: the text-mode reader raised
    `UnicodeDecodeError` past every typed refusal, leaving an agent with no
    `next_kind` and no `remedy`.

    External fallback is advertised for ONE state, and it is now established
    by the question that answers it (`ls-tree`: silent and exit 0 for a path
    a tree lacks) rather than inferred from a failure to read. Every other
    outcome refuses.

    The mutation: restore `except RuntimeError: fall back` around the read
    and the corrupt-object and operational rows pass when they must not.
    """

    VALID = "review.toml"

    def _repo(self, body=None, *, as_bytes=False, as_dir=False,
              corrupt=False, symlink_to=None, executable=False,
              gitlink=False):
        """A target commit carrying `body` as review.toml, plus a DIFFERENT
        valid config in the checkout — so a silent fallback is visible as
        the wrong rules rather than as no rules at all."""
        try:
            tmp = Path(tempfile.mkdtemp(prefix="origin-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        repo = tmp / "repo"
        good = (REPO_ROOT / self.VALID).read_text(encoding="utf-8")
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)],
                       check=True, capture_output=True, timeout=60)
        if symlink_to is not None:
            # The link TEXT is itself valid TOML, and the file it names holds
            # different valid TOML: the two ends must not be able to read
            # different bytes for one SHA (round 4 F1).
            (repo / symlink_to).write_text(
                with_cap((REPO_ROOT / self.VALID).read_text(encoding="utf-8"),
                         CHECKOUT_CAP), encoding="utf-8")
            (repo / self.VALID).symlink_to(symlink_to)
        elif as_dir:
            (repo / self.VALID).mkdir()
            (repo / self.VALID / "x").write_text("x", encoding="utf-8")
        elif body is not None:
            target = repo / self.VALID
            if as_bytes:
                target.write_bytes(body)
            else:
                target.write_text(body, encoding="utf-8")
        (repo / "other.txt").write_text("x", encoding="utf-8")
        if executable:
            (repo / self.VALID).chmod(0o755)

        def commit(subject, stage=True):
            steps = (("add", "-A"),) if stage else ()
            for args in (*steps,
                         ("-c", "user.email=t@example.invalid", "-c",
                          "user.name=t", "commit", "--allow-empty", "-qm",
                          subject)):
                subprocess.run(["git", "-C", str(repo), *args], check=True,
                               capture_output=True, timeout=60)
            return subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                capture_output=True, text=True, timeout=60).stdout.strip()

        # A REAL base: the take row below must refuse for its own reason,
        # and a fixture base SHA that does not resolve refuses first — which
        # is how that row passed under the mutation it exists to catch.
        held = (repo / self.VALID)
        link = os.readlink(held) if held.is_symlink() else None
        stashed = (held.read_bytes()
                   if link is None and held.is_file() else None)
        if link is not None or stashed is not None:
            held.unlink()
        self.base = commit("base")
        if link is not None:
            held.symlink_to(link)          # still a symlink at the target
        elif stashed is not None:
            held.write_bytes(stashed)
        sha = commit("target")
        if gitlink:
            subprocess.run(
                ["git", "-C", str(repo), "update-index", "--add",
                 "--cacheinfo", f"160000,{sha},{self.VALID}"], check=True,
                capture_output=True, timeout=60)
            # No `add -A`: it would re-stage the worktree file as a blob and
            # undo the gitlink this row exists to test.
            sha = commit("gitlink", stage=False)
        if corrupt:
            blob = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", f"{sha}:{self.VALID}"],
                check=True, capture_output=True, text=True,
                timeout=60).stdout.strip()
            (repo / ".git" / "objects" / blob[:2] / blob[2:]).unlink()
        # The checkout's rules differ, so a substitution is legible.
        if as_dir or (repo / self.VALID).is_symlink():
            (repo / self.VALID).unlink() if (
                repo / self.VALID).is_symlink() else None
        if as_dir:
            __import__("shutil").rmtree(repo / self.VALID)
        (repo / self.VALID).write_text(
            with_cap(good, CHECKOUT_CAP), encoding="utf-8")
        cfg = dataclasses.replace(config.load(repo), ledger_dir=tmp / "l")
        return cfg, sha

    def _resolve(self, **kw):
        cfg, sha = self._repo(**kw)
        return transport.resolve_authority(cfg, sha)

    # ------------------------------------------------------ the controls

    def test_a_truly_absent_config_is_the_advertised_fallback(self):
        governing, origin = self._resolve(body=None)
        self.assertEqual(origin, transport.AUTHORITY_EXTERNAL)
        self.assertEqual(governing.round_cap, CHECKOUT_CAP)   # the checkout's, honestly

    def test_a_readable_config_is_the_targets_own(self):
        good = (REPO_ROOT / self.VALID).read_text(encoding="utf-8")
        governing, origin = self._resolve(body=good)
        self.assertEqual(origin, transport.AUTHORITY_TARGET)
        self.assertEqual(governing.round_cap, LIVE_CAP)   # the TARGET's

    def test_ordinary_non_ascii_utf8_is_read(self):
        good = (REPO_ROOT / self.VALID).read_text(encoding="utf-8")
        governing, origin = self._resolve(body="# caf\u00e9 \u2014 \u00e9\n" + good)
        self.assertEqual(origin, transport.AUTHORITY_TARGET)
        self.assertEqual(governing.round_cap, LIVE_CAP)

    # ------------------------------------------------------ the refusals

    def _refuses(self, why, **kw):
        with self.assertRaises(transport.Refusal) as ctx:
            self._resolve(**kw)
        exc = ctx.exception
        self.assertEqual(exc.next_cmd, "", why)
        self.assertTrue(exc.remedy, why)
        return exc

    def test_a_present_but_unreadable_blob_refuses(self):
        """F2's own falsification: entry present, object gone."""
        good = (REPO_ROOT / self.VALID).read_text(encoding="utf-8")
        self.assertIn("PRESENT", str(self._refuses("corrupt object",
                                                   body=good, corrupt=True)))

    def test_an_executable_regular_file_is_still_a_file(self):
        """The control for the mode check: 100755 carries bytes a checkout
        reads, so it must be ADMITTED, not swept up with the refusals."""
        good = (REPO_ROOT / self.VALID).read_text(encoding="utf-8")
        governing, origin = self._resolve(body=good, executable=True)
        self.assertEqual(origin, transport.AUTHORITY_TARGET)
        self.assertEqual(governing.round_cap, LIVE_CAP)

    def test_a_tree_entry_refuses(self):
        self.assertIn("a directory",
                      str(self._refuses("tree entry", as_dir=True)))

    def test_a_submodule_entry_refuses(self):
        good = (REPO_ROOT / self.VALID).read_text(encoding="utf-8")
        self.assertIn("a submodule",
                      str(self._refuses("gitlink", body=good, gitlink=True)))

    def test_a_symlink_cannot_split_the_two_ends(self):
        """Round 4 F1's falsification. A symlink is mode 120000 and object
        type `blob`, so a type-only check admitted it and `git show` handed
        back the LINK TEXT — while the author's `config.load` followed the
        link and read the file. One SHA, two configurations."""
        cfg, sha = self._repo(symlink_to="linked.toml")
        # The author's end follows the link: this is the value it would use.
        self.assertEqual(cfg.round_cap, CHECKOUT_CAP)
        with self.assertRaises(transport.Refusal) as ctx:
            transport.resolve_authority(cfg, sha)
        self.assertIn("a symbolic link", str(ctx.exception))
        self.assertEqual(ctx.exception.next_cmd, "")

    def test_a_dangling_symlink_refuses_for_the_same_reason(self):
        cfg, sha = self._repo(symlink_to="linked.toml")
        (cfg.repo_root / "linked.toml").unlink()
        with self.assertRaises(transport.Refusal) as ctx:
            transport.resolve_authority(cfg, sha)
        self.assertIn("a symbolic link", str(ctx.exception))

    def test_every_invalid_config_shape_is_a_typed_refusal(self):
        """Round 4 F4: "TOML the config layer rejects" was not a closed
        state. `taxonomy = []` crashed inside resolution with a raw
        TypeError, and a string `round_cap` acquired target authority and
        raised a raw ValueError later — after this boundary had reported
        success. The schema is derived from DEFAULTS, so each row below is a
        kind that authority already declares."""
        good = (REPO_ROOT / self.VALID).read_text(encoding="utf-8")
        for label, body in (
                ("a section that is not a table", "taxonomy = []"),
                ("a scalar of the wrong type",
                 "[limits]\nround_cap = \"not-an-int\""),
                ("a boolean where a count belongs",
                 "[limits]\nround_cap = true"),
                ("a list holding non-strings",
                 "[taxonomy]\nseverities = [1, 2]"),
                ("a table holding non-strings",
                 "[taxonomy]\nclassification_notes = {a = 1}"),
                ("an unknown section", "[nope]\nx = 1"),
                ("an unknown key", "[limits]\nnope = 1"),
                ("a gate row that is not a table", "gates = [1]"),
                ("a gate with no id",
                 "[[gates]]\nid = \"\"\ncommand = [\"true\"]"),
                ("a gate with an empty command",
                 "[[gates]]\nid = \"x\"\ncommand = []"),
                ("a gate with a non-boolean blocking",
                 "[[gates]]\nid = \"x\"\ncommand = [\"true\"]\n"
                 "blocking = \"yes\""),
                # Round 5 F2: shape was closed, IDENTITY was not. A gate id
                # names the retained output and joins the attestation, so two
                # rows sharing one are two gates wearing one identity — both
                # run, the second output overwrites the first, and the first
                # record's pointer then names bytes that are not its own.
                ("two gate rows sharing one id",
                 "[[gates]]\nid = \"dup\"\ncommand = [\"echo\", \"a\"]\n"
                 "[[gates]]\nid = \"dup\"\ncommand = [\"echo\", \"b\"]"),
        ):
            with self.subTest(shape=label):
                self.assertIn("AUTHOR", self._refuses(label, body=body).remedy)
        # The paired canonical control: the repository's own configuration,
        # which every row above is a mutation of, still resolves.
        governing, origin = self._resolve(body=good)
        self.assertEqual(origin, transport.AUTHORITY_TARGET)
        # And the distinct-id control, so the uniqueness rule is a rule about
        # collision rather than a rule against declaring two gates.
        two, _ = self._resolve(
            body="[[gates]]\nid = \"one\"\ncommand = [\"true\"]\n"
                 "[[gates]]\nid = \"two\"\ncommand = [\"true\"]\n"
                 + good)
        self.assertEqual([g["id"] for g in two.gates][:2], ["one", "two"])

    def _timeout(self, *_a):
        raise subprocess.TimeoutExpired(cmd="git", timeout=120)

    def test_a_timeout_at_the_presence_probe_is_typed(self):
        """Round 4 F3: a timeout is an ADMITTED outcome — both readers
        declare one — and TimeoutExpired is not a RuntimeError, so it tore
        through every caller's catch and left an agent with no next_kind."""
        cfg, _ = self._repo(body="round_cap = 3")
        with self.assertRaises(transport.Refusal) as ctx:
            transport.resolve_authority(cfg, "b" * 40, git=self._timeout)
        self.assertEqual(ctx.exception.next_cmd, "")
        self.assertTrue(ctx.exception.remedy)

    def test_a_timeout_at_the_byte_read_is_typed(self):
        cfg, sha = self._repo(
            body=(REPO_ROOT / self.VALID).read_text(encoding="utf-8"))
        entry = f"100644 blob 0000000\t{self.VALID}"

        def git(*args):
            if args[0] == "ls-tree":
                return entry
            raise subprocess.TimeoutExpired(cmd="git", timeout=120)

        with self.assertRaises(transport.Refusal) as ctx:
            transport.resolve_authority(cfg, sha, git=git)
        self.assertIn("cannot be read", str(ctx.exception))
        self.assertTrue(ctx.exception.remedy)

    def test_a_missing_executable_is_typed(self):
        cfg, _ = self._repo(body="round_cap = 3")

        def git(*args):
            raise FileNotFoundError(2, "no such file", "git")

        with self.assertRaises(transport.Refusal) as ctx:
            transport.resolve_authority(cfg, "b" * 40, git=git)
        self.assertEqual(ctx.exception.next_cmd, "")

    def test_an_unreadable_tree_listing_refuses(self):
        cfg, _ = self._repo(body="round_cap = 3")
        with self.assertRaises(transport.Refusal) as ctx:
            transport.resolve_authority(
                cfg, "b" * 40, git=lambda *a: "garbage")
        self.assertIn("cannot be read", str(ctx.exception))

    def test_invalid_leading_bytes_refuse(self):
        self.assertIn("not valid UTF-8",
                      str(self._refuses("0xff 0xfe", body=b"\xff\xfe\x00x",
                                    as_bytes=True)))

    def test_truncated_multibyte_input_refuses(self):
        self.assertIn("not valid UTF-8",
                      str(self._refuses("lone continuation byte",
                                    body=b"round_cap = 3\n\xc3",
                                    as_bytes=True)))

    def test_a_byte_order_mark_is_refused_rather_than_stripped(self):
        """BOM policy, stated rather than left to chance: the bytes decode,
        and the config layer then rejects them. Declared here so the row
        cannot silently become 'stripped' later."""
        good = (REPO_ROOT / self.VALID).read_text(encoding="utf-8")
        self._refuses("utf-8 BOM",
                      body=b"\xef\xbb\xbf" + good.encode("utf-8"),
                      as_bytes=True)

    def test_decodable_but_malformed_toml_still_refuses(self):
        """The paired control for the two byte rows: same typed refusal,
        different cause."""
        self._refuses("malformed TOML", body="[[[ not toml")

    # ------------------------------------------------- through the verbs

    def test_take_refuses_a_corrupt_target_config_and_records_nothing(self):
        good = (REPO_ROOT / self.VALID).read_text(encoding="utf-8")
        cfg, sha = self._repo(body=good, corrupt=True)
        ledger = Ledger(cfg.ledger_dir)
        with self.assertRaises(transport.Refusal) as ctx:
            transport.take(cfg, ledger,
                           request_text(sha=sha, base=self.base), "r.md",
                           reviewer="codex", fetch=False)
        # Bound to its own cause, not to whatever refuses first.
        self.assertIn("cannot be read", str(ctx.exception))
        self.assertEqual(ledger.events(), [],
                         "a refusal before validation appends nothing")


if __name__ == "__main__":
    unittest.main()

"""§9bis.3 / RVW-T9: the transport loop end to end.

One integration class runs the whole loop for real — handoff -> take (second
clone, fetch over a bare path remote) -> verdict -> close -> next handoff —
because a first real execution is a different instrument from unit tests
(slice-1 lesson 3); it self-skips, with the reason stated, where filesystem
writes are denied.

Split from `test_transport.py` 2026-08-31; the classes are verbatim.
"""

import dataclasses
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from review import config, transport, validate, vocab, wire
from review.emit import _git
from review.ledger import Ledger
from review.tests._transport_fixtures import (
    CFG, run_cli, scratch_loop_repo, sh, verdict_text)

class TestFullLoopIntegration(unittest.TestCase):
    """The first-real-execution instrument: two clones, a bare path remote,
    no network. handoff in the author clone → take in the reviewer clone
    (fetching over the stamped path) → verdict → close → the next handoff
    → lineage close → round 1 again with the repo default cap."""

    def setUp(self):
        scratch = scratch_loop_repo(self, "loop-", name="author",
                                    objective="loop test")
        self.tmp, self.author = scratch.tmp, scratch.repo
        self.base, self.claim, self.cwd = scratch.base, scratch.claim, scratch.cwd
        self.remote = self.tmp / "remote.git"
        self.reviewer = self.tmp / "reviewer"
        # The bare remote names its initial branch too. Without `-b`, HEAD
        # takes git's built-in default (`master`); the author pushes `main`,
        # so the remote's HEAD points at a branch that never exists and
        # `git clone` below yields an EMPTY working tree — no review.toml,
        # so `take` refuses with T-UNDECLARED. Invisible on a machine whose
        # system gitconfig sets init.defaultBranch=main (Apple's git does);
        # the first CI run of this suite (2026-08-17) found it.
        self._sh("git", "init", "-q", "--bare", "-b", "main",
                 str(self.remote))
        self._sh("git", "-C", str(self.author), "remote", "add", "origin",
                 str(self.remote))
        self._sh("git", "-C", str(self.author), "push", "-q", "-u", "origin",
                 "main")
        self._sh("git", "clone", "-q", str(self.remote), str(self.reviewer))
        self.author_state = self.tmp / "state-author"
        self.reviewer_state = self.tmp / "state-reviewer"

    _sh = staticmethod(sh)

    def _run(self, where, state, *argv):
        return run_cli(where, state, *argv, cwd=self.cwd)

    def test_the_loop_end_to_end(self):
        head = _git(self.author, "rev-parse", "HEAD")
        # 1. handoff (author): emits round 1, records, keeps.
        code, rec = self._run(self.author, self.author_state, "handoff",
                              "--claim-file", str(self.claim),
                              "--base", self.base)
        self.assertEqual(code, 0, rec)
        self.assertEqual(rec["sha"], head)
        self.assertFalse(rec["cached"])
        self.assertTrue(Path(rec["kept"]).is_file())
        # 1b. handoff again on the unchanged tip: cached, gates not re-run.
        code, again = self._run(self.author, self.author_state, "handoff",
                                "--claim-file", str(self.claim),
                                "--base", self.base)
        self.assertEqual(code, 0, again)
        self.assertTrue(again["cached"])
        self.assertEqual(again["digest"], rec["digest"])
        # 2. take (reviewer clone): fetches over the stamped path remote.
        #    --as is mandatory since round 1 F4 — the loop no longer runs on a
        #    defaulted identity, so the real path must declare one too.
        code, taken = self._run(self.reviewer, self.reviewer_state, "take",
                                rec["kept"], "--as", "codex")
        self.assertEqual(code, 0, taken)
        self.assertEqual(taken["sha"], head)
        self.assertIn("fetched", taken["target"]["fetch"])
        self.assertEqual(taken["target"]["target"], "present")
        self.assertTrue(taken["references"][0]["status"].startswith("checked"))
        self.assertIn(f"diff {self.base}...{head}", taken["diff"])
        # 2b. taking it as the wrong identity refuses (exit 1, next given).
        code, wrong = self._run(self.reviewer, self.reviewer_state, "take",
                                rec["kept"], "--as", "claude")
        self.assertEqual(code, 1)
        self.assertIn("next", wrong)
        # 3. verdict → close (author): round 1 closed, lineage open.
        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head), encoding="utf-8")
        code, closed = self._run(self.author, self.author_state, "close",
                                 "--verdict", str(v1))
        self.assertEqual(code, 0, closed)
        self.assertEqual(closed["round"], 1)
        self.assertEqual(closed["lineage"], "open")
        # 3b. respond --out (author): the disposition is recorded and kept
        #     without a manual ledger add; the round-2 request then carries it.
        d_json = self.tmp / "d.json"
        d_json.write_text(json.dumps({
            "head": head, "round": 1, "author": "claude",
            "dispositions": [{"finding_id": "F1", "disposition": "accepted",
                              "payload": {"change": "fixed", "verification":
                                          "observed",
                                          "falsification": {
                                              "status": "pass",
                                              "mutation": "fails_without_fix"}
                                          }}]}), encoding="utf-8")
        d_md = self.tmp / "d.md"
        code, resp = self._run(self.author, self.author_state, "respond",
                               "--verdict", str(v1), "--from-json",
                               str(d_json), "--out", str(d_md))
        self.assertEqual(code, 0, resp)
        # The disposition AND its falsification run: the second event is the
        # product-path writer the `stale`/`unverifiable` breakers never had —
        # before it, the only `falsification_run` events in existence were
        # seeded by hand inside test_breakers.
        self.assertEqual(resp["events_added"], 2)
        self.assertTrue(Path(resp["kept"]).is_file())
        runs = [e for e in Ledger(self.author_state).events()
                if e.get("event") == "falsification_run"]
        self.assertEqual(len(runs), 1, runs)
        self.assertEqual(runs[0]["status"], "pass")
        self.assertEqual(runs[0]["mutation"], "fails_without_fix")
        self.assertEqual(runs[0]["source"], "disposition")
        self.assertEqual(runs[0]["round"], 1)
        # 4. next handoff opens round 2 (same tip; not cached, new round).
        code, r2 = self._run(self.author, self.author_state, "handoff",
                             "--claim-file", str(self.claim))
        self.assertEqual(code, 0, r2)
        self.assertEqual(r2["round"], 2)
        self.assertFalse(r2["cached"])
        r2_text = Path(r2["kept"]).read_text(encoding="utf-8")
        self.assertIn("**accepted**", r2_text)
        # ...and the run record beside it, so the reviewer rules on it.
        self.assertIn("[falsification: pass; mutation: fails_without_fix]",
                      r2_text)
        # 5. close the lineage by decision; the next handoff is round 1
        #    of lineage 2 and needs an explicit base again.
        code, lin = self._run(self.author, self.author_state, "close",
                              "--lineage", "--reason", "loop test done",
                              "--by", "user")
        self.assertEqual(code, 0, lin)
        self.assertTrue(lin["open_request"])
        self.assertEqual(lin["next_lineage"], 2)
        code, r1b = self._run(self.author, self.author_state, "handoff",
                              "--claim-file", str(self.claim),
                              "--base", self.base)
        self.assertEqual(code, 0, r1b)
        self.assertEqual(r1b["round"], 1)
        # The ledger tells the story in order.
        ledger = Ledger(self.author_state)
        kinds = [e["event"] for e in ledger.events()]
        self.assertEqual(kinds.count("request"), 3)
        self.assertEqual(kinds.count("verdict"), 1)
        self.assertEqual(kinds.count("disposition"), 1)
        self.assertEqual(kinds.count(Ledger.LINEAGE_CLOSED), 1)
        self.assertEqual(ledger.lineage_number(), 2)

    def test_handoff_refuses_when_response_is_omitted(self):
        """FALSIFICATION for sweep F4 (Blocker). The loop, with the author's
        answer skipped: handoff → take → verdict → close → handoff. The
        second handoff must refuse — no request event, no kept envelope, no
        gate run — until every finding of the round-1 verdict has its
        disposition. Mutation: reintroduce the verdict-only transition (call
        `next_round` without the preflight — remove `handoff_preflight`
        from `cmd_handoff`) and the second handoff opens round 2 with an
        empty disposition ledger; this fails on the first assertNotEqual.
        Two controls: the same handoff after `respond` succeeds, and a
        clean verdict — which closes the lineage — is followed by a round-1
        handoff that owes nothing."""
        head = _git(self.author, "rev-parse", "HEAD")
        code, rec = self._run(self.author, self.author_state, "handoff",
                              "--claim-file", str(self.claim),
                              "--base", self.base)
        self.assertEqual(code, 0, rec)
        code, taken = self._run(self.reviewer, self.reviewer_state, "take",
                                rec["kept"], "--as", "codex")
        self.assertEqual(code, 0, taken)
        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head, findings=2), encoding="utf-8")
        code, closed = self._run(self.author, self.author_state, "close",
                                 "--verdict", str(v1))
        self.assertEqual(code, 0, closed)
        before = Ledger(self.author_state).events()
        # The illegal transition: hand off with no response recorded.
        code, refused = self._run(self.author, self.author_state, "handoff",
                                  "--claim-file", str(self.claim))
        self.assertNotEqual(code, 0, refused)
        self.assertEqual(refused["next_kind"], "blocked")
        self.assertIsNone(refused["next"])
        self.assertIn("2 of 2", refused["error"])
        self.assertEqual(Ledger(self.author_state).events(), before,
                         "a refused handoff appends nothing")
        self.assertFalse((Path(self.author_state) / "exchange"
                          / "round-2-request.md").exists())
        # A PARTIAL answer is still owed: one of two.
        d_json = self.tmp / "d.json"
        one = {"head": head, "author": "claude",
               "dispositions": [{"finding_id": "F1", "disposition": "accepted",
                                 "payload": {"change": "fixed",
                                             "verification": "observed",
                                             "falsification": {
                                                 "status": "pass",
                                                 "mutation":
                                                     "fails_without_fix"}}}]}
        d_json.write_text(json.dumps(one), encoding="utf-8")
        code, resp = self._run(self.author, self.author_state, "respond",
                               "--verdict", str(v1), "--from-json",
                               str(d_json), "--out", str(self.tmp / "d.md"))
        # respond itself refuses an incomplete answer (D-INCOMPLETE): the
        # preflight is the second wall, for the case where respond was never
        # run at all — which is exactly what the first refusal above proved.
        self.assertNotEqual(code, 0, resp)
        # Control 1: the complete answer, then the handoff opens round 2.
        two = dict(one)
        two["dispositions"] = one["dispositions"] + [
            dict(one["dispositions"][0], finding_id="F2")]
        d_json.write_text(json.dumps(two), encoding="utf-8")
        code, resp = self._run(self.author, self.author_state, "respond",
                               "--verdict", str(v1), "--from-json",
                               str(d_json), "--out", str(self.tmp / "d.md"))
        self.assertEqual(code, 0, resp)
        code, r2 = self._run(self.author, self.author_state, "handoff",
                             "--claim-file", str(self.claim))
        self.assertEqual(code, 0, r2)
        self.assertEqual(r2["round"], 2)
        # Control 2: a clean verdict closes the lineage; round 1 of the next
        # lineage owes no disposition and opens.
        v2 = self.tmp / "v2.md"
        v2.write_text(verdict_text(sha=head, verdict="clean to advance"),
                      encoding="utf-8")
        code, closed = self._run(self.author, self.author_state, "close",
                                 "--verdict", str(v2))
        self.assertEqual(code, 0, closed)
        code, r1b = self._run(self.author, self.author_state, "handoff",
                              "--claim-file", str(self.claim),
                              "--base", self.base)
        self.assertEqual(code, 0, r1b)
        self.assertEqual(r1b["round"], 1)

    def test_take_recovers_from_an_empty_reviewer_checkout(self):
        """FALSIFICATION for sweep F6 (High). The reviewer's clone has an
        unborn HEAD and an empty working tree — the exact state the first
        CI run produced when the bare remote's default branch never
        existed. `take` must fetch the stamped target, read the target's
        own review.toml, validate against it, check every reference from
        the target tree, and record the take. Mutation: restore pre-fetch
        validation against the reviewer's local config (validate before
        `probe_target`, with `cfg` instead of `target_config`) and this
        empty checkout refuses with T-UNDECLARED — the first assertEqual
        fails. Control: the ordinary clone validates against the target's
        config too, and says so.

        F3, in the same topology: this repository's own committed
        review.toml (the fixture's copy of it) declares `debug`,
        `review_default`, `enforcement`, `round_cap` and `token_budget`,
        and declares no `transport` — the one key no repository default
        governs. `take`'s `decide` must report ONLY `roles.transport`,
        from BOTH checkouts, empty or ordinary: the target's own
        declared keys are the authority a `take` validates against, and
        an authority the request was just validated under cannot also be
        reported as silent about the same keys. Mutation: build `decide`
        from the caller's own `cfg` (`_decide(cfg, …)` in `cmd_take`) and
        the empty checkout — which declares nothing at all — reports the
        five declared keys as undeclared, failing both the exact-set
        assertion and the parity with the ordinary clone.
        """
        # The target declares every optional key except roles.transport —
        # written here, not inherited from whichever config the scratch
        # loop copied (the workbench's declares them; the published
        # example does not).
        import re
        toml_path = self.author / "review.toml"
        toml = toml_path.read_text(encoding="utf-8")
        for key in ("debug", "review_default", "enforcement",
                    "round_cap", "token_budget", "transport"):
            toml = re.sub(rf"^{key}\s*=.*\n", "", toml, flags=re.M)
        toml = toml.replace("[roles]\n", "[roles]\ndebug = false\n"
                            "review_default = \"on\"\nenforcement = \"none\"\n", 1)
        toml += "\n[limits]\nround_cap = 3\ntoken_budget = 200000\n" \
            if "[limits]" not in toml else ""
        toml = re.sub(r"^\[limits\]\n", "[limits]\nround_cap = 3\n"
                      "token_budget = 200000\n", toml, count=1, flags=re.M) \
            if "round_cap" not in toml else toml
        toml_path.write_text(toml, encoding="utf-8")
        self._sh("git", "-C", str(self.author), "commit", "-qam",
                 "declare every optional key but transport")
        head = _git(self.author, "rev-parse", "HEAD")
        code, rec = self._run(self.author, self.author_state, "handoff",
                              "--claim-file", str(self.claim),
                              "--base", self.base)
        self.assertEqual(code, 0, rec)
        # An empty reviewer repository: initialised, nothing checked out,
        # no review.toml anywhere in it.
        empty = self.tmp / "reviewer-empty"
        self._sh("git", "init", "-q", "-b", "main", str(empty))
        self.assertEqual(sorted(x.name for x in empty.iterdir()), [".git"])
        empty_state = self.tmp / "state-empty"
        code, taken = self._run(empty, empty_state, "take", rec["kept"],
                                "--as", "codex")
        self.assertEqual(code, 0, taken)
        self.assertEqual(taken["sha"], head)
        self.assertIn("fetched", taken["target"]["fetch"])
        self.assertTrue(taken["target"]["config"].startswith(
            "target review.toml at"), taken["target"]["config"])
        self.assertTrue(taken["references"][0]["status"].startswith(
            "checked"), taken["references"])
        kinds = [e["event"] for e in Ledger(empty_state).events()]
        self.assertIn("take", kinds)
        # And the working tree was left exactly as found: empty.
        self.assertEqual(sorted(x.name for x in empty.iterdir()), [".git"])
        # F3: the target's declared keys are not reported as decisions —
        # this empty checkout has NOTHING of its own, and still reports
        # only the one key the target genuinely never declares.
        self.assertEqual([e["key"] for e in taken["decide"]],
                         [vocab.DECIDE_TRANSPORT])
        # Control: the ordinary clone, whose checkout DOES carry the same
        # review.toml, is validated against the target's copy as well.
        code, taken2 = self._run(self.reviewer, self.reviewer_state, "take",
                                 rec["kept"], "--as", "codex")
        self.assertEqual(code, 0, taken2)
        self.assertTrue(taken2["target"]["config"].startswith(
            "target review.toml at"))
        # Parity: both checkouts are judged by the same target authority,
        # so both report the same `decide` set — an empty checkout is not
        # a repository with MORE undeclared keys than an ordinary clone of
        # the exact commit under review.
        self.assertEqual([e["key"] for e in taken2["decide"]],
                         [vocab.DECIDE_TRANSPORT])
        # A target that carries no review.toml is governed by the checkout,
        # and the record says so rather than pretending otherwise. Round 3
        # F2: absence is the answer to `ls-tree`, not the answer to a failed
        # read — a target whose config cannot be READ is a refusal, and the
        # sibling rows for that live in TestAuthorityOriginIsClassified.
        rec2, origin = transport.resolve_authority(
            dataclasses.replace(CFG, ledger_dir=None), "0" * 40,
            git=lambda *a: "")
        self.assertEqual(origin, transport.AUTHORITY_EXTERNAL)
        self.assertIn("carries no review.toml", rec2.source)
        self.assertEqual(rec2.taxonomy, CFG.taxonomy)


class TestTheScopeReportReachesTheAuthor(unittest.TestCase):
    """Round-9 F4, at the verb (`claim-scope-consistency-check`).

    The comparison is worth nothing in a function nobody calls: both author
    doors measure the span already — the envelope renders it as `What
    changed` — so the report rides the same measurement out to the author,
    as a `scope` field an agent relays and a notice line a human reads. It
    never changes the exit: `handoff` succeeds either way, which is what
    non-blocking means here.

    MUTATION: drop the `scope_items` call from `_emit` and the round
    completes with the claim and the span still disagreeing, exactly as
    round 9 did.
    """

    def setUp(self):
        scratch = scratch_loop_repo(self, "scope-", name="repo",
                                    objective="scope test")
        self.tmp, self.repo = scratch.tmp, scratch.repo
        self.base, self.claim, self.cwd = (scratch.base, scratch.claim,
                                           scratch.cwd)
        self.state = self.tmp / "state"

    def handoff(self):
        return run_cli(self.repo, self.state, "handoff", "--claim-file",
                       str(self.claim), "--base", self.base,
                       "--local-only", cwd=self.cwd)

    def test_a_changed_path_the_claim_never_names_is_reported(self):
        # The fixture's claim names `review.toml` as its reference; the
        # span changed `f.txt`, which it names nowhere.
        code, rec = self.handoff()
        self.assertEqual(code, 0, rec)
        self.assertTrue(rec["ok"], "the report never decides the exit")
        [item] = rec["scope"]
        self.assertEqual(item["level"], "notice")
        self.assertEqual(item["code"], "R-SCOPE-UNNAMED")
        self.assertIn("f.txt", item["message"])

    def test_a_claim_that_accounts_for_it_reports_nothing(self):
        self.claim.write_text(json.dumps({
            "objective": "scope test",
            "review_scope": "f.txt is the whole change",
            "references": [{"path": "review.toml", "required": True}]}),
            encoding="utf-8")
        code, rec = self.handoff()
        self.assertEqual(code, 0, rec)
        self.assertNotIn("scope", rec,
                         "a claim that accounts for the span says nothing "
                         "here — the field exists to carry a disagreement")


class TestRespondRefusesAMismatchedAuthor(unittest.TestCase):
    """Round-3 F2's real-verb falsification.

    A round's request already names its author — `handoff` stamps the
    repository's configured `[roles] author` on it. A disposition JSON
    naming a DIFFERENT author (the REVIEWER, say, answering its own
    verdict) must refuse before `respond --out` writes the envelope, the
    kept exchange copy or any ledger event; the otherwise identical
    response naming the request's real author must succeed and the
    recorded disposition events must carry it.

    MUTATION: reverting `cmd_respond` to `data.get("author",
    cfg.roles["author"])` (dropping the derive-and-refuse block) makes the
    mismatched case succeed, record `author: "codex"`, and this test's
    refusal assertions fail.
    """

    def setUp(self):
        scratch = scratch_loop_repo(self, "respond-author-", name="author",
                                    objective="respond author test")
        self.tmp, self.repo = scratch.tmp, scratch.repo
        self.base, self.claim, self.cwd = scratch.base, scratch.claim, scratch.cwd
        self.state = self.tmp / "state"

    def _run(self, *argv):
        return run_cli(self.repo, self.state, *argv, cwd=self.cwd)

    def _disposition_json(self, head, author):
        return json.dumps({
            "head": head, "round": 1, "author": author,
            "dispositions": [{"finding_id": "F1", "disposition": "accepted",
                              "payload": {"change": "fixed",
                                          "verification": "observed",
                                          "falsification": {
                                              "status": "pass",
                                              "mutation": "fails_without_fix"}
                                          }}]})

    def test_a_mismatched_author_refuses_before_any_write(self):
        head = _git(self.repo, "rev-parse", "HEAD")
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              "--base", self.base, "--local-only")
        self.assertEqual(code, 0, rec)
        # The request `handoff` just recorded stamps this repo's configured
        # author, "claude" — confirmed here rather than assumed, since the
        # whole test rests on it.
        req = next(e for e in Ledger(self.state).events()
                  if e.get("event") == "request" and e.get("round") == 1)
        self.assertEqual(req.get("author"), "claude")

        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head), encoding="utf-8")
        code, closed = self._run("close", "--verdict", str(v1))
        self.assertEqual(code, 0, closed)

        d_json = self.tmp / "d.json"
        d_json.write_text(self._disposition_json(head, "codex"),
                          encoding="utf-8")
        d_md = self.tmp / "d.md"
        before_ledger = self.state.joinpath("ledger.jsonl").read_bytes()

        code, resp = self._run("respond", "--verdict", str(v1),
                               "--from-json", str(d_json), "--out", str(d_md))

        self.assertEqual(code, 1, resp)
        self.assertIn("author", resp.get("error", ""))
        self.assertFalse(d_md.exists(),
                         "the mismatched response must write no envelope")
        self.assertEqual(
            self.state.joinpath("ledger.jsonl").read_bytes(), before_ledger,
            "the mismatched response must record no ledger event")

        # The control: the otherwise identical response, naming the
        # request's real author, succeeds — and the recorded disposition
        # event carries it.
        d_json.write_text(self._disposition_json(head, "claude"),
                          encoding="utf-8")
        code, resp2 = self._run("respond", "--verdict", str(v1),
                                "--from-json", str(d_json), "--out", str(d_md))
        self.assertEqual(code, 0, resp2)
        self.assertTrue(d_md.exists())
        disp = [e for e in Ledger(self.state).events()
               if e.get("event") == "disposition"]
        self.assertEqual(len(disp), 1, disp)
        self.assertEqual(disp[0].get("author"), "claude")

    def test_an_omitted_author_falls_back_to_the_recorded_request(self):
        """The control the falsification does not need but the design
        claims: a disposition that names NO author is not a mismatch — it
        is silence, and the recorded request's author fills it, exactly as
        `cfg.roles["author"]` did before this fix existed for a round with
        no derivable request."""
        head = _git(self.repo, "rev-parse", "HEAD")
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              "--base", self.base, "--local-only")
        self.assertEqual(code, 0, rec)
        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head), encoding="utf-8")
        code, closed = self._run("close", "--verdict", str(v1))
        self.assertEqual(code, 0, closed)
        d_json = self.tmp / "d.json"
        data = json.loads(self._disposition_json(head, "codex"))
        del data["author"]
        d_json.write_text(json.dumps(data), encoding="utf-8")
        d_md = self.tmp / "d.md"

        code, resp = self._run("respond", "--verdict", str(v1),
                               "--from-json", str(d_json), "--out", str(d_md))

        self.assertEqual(code, 0, resp)
        disp = [e for e in Ledger(self.state).events()
               if e.get("event") == "disposition"]
        self.assertEqual(len(disp), 1, disp)
        self.assertEqual(disp[0].get("author"), "claude")

    def test_b_respond_without_out_refuses_before_any_envelope_is_printed(self):
        """Round-4 F1's falsification, second door: `respond` WITHOUT
        `--out` used to skip the derive-and-refuse check entirely, because
        `recorded` (the thing the check reads the round from) was only ever
        resolved `if args.out`. A mismatched author must refuse before the
        envelope is rendered to stdout at all — a run that succeeds prints
        the disposition envelope (not JSON), so a refusal is distinguished
        here by the response staying JSON (`_blocked`'s shape) rather than
        becoming the printed envelope.

        MUTATION: reverting the `recorded = transport.recorded_verdict(...)`
        resolution back inside `if args.out:` (round 4 F1's regression)
        makes `recorded` stay `None` on this path, the author check never
        runs, and the mismatched case below prints the `author="codex"`
        envelope with exit 0 — this test's refusal assertions fail.
        """
        head = _git(self.repo, "rev-parse", "HEAD")
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              "--base", self.base, "--local-only")
        self.assertEqual(code, 0, rec)
        req = next(e for e in Ledger(self.state).events()
                  if e.get("event") == "request" and e.get("round") == 1)
        self.assertEqual(req.get("author"), "claude")
        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head), encoding="utf-8")
        code, closed = self._run("close", "--verdict", str(v1))
        self.assertEqual(code, 0, closed)

        d_json = self.tmp / "d.json"
        d_json.write_text(self._disposition_json(head, "codex"),
                          encoding="utf-8")

        code, resp = self._run("respond", "--verdict", str(v1),
                               "--from-json", str(d_json))

        self.assertEqual(code, 1, resp)
        self.assertIsInstance(
            resp, dict,
            "a refusal is `_blocked`'s JSON shape, never the envelope text "
            "the success path prints")
        self.assertIn("author", resp.get("error", ""))

        # The control: the otherwise identical response, naming the
        # request's real author, succeeds and prints the envelope, carrying
        # that author on its open tag.
        d_json.write_text(self._disposition_json(head, "claude"),
                          encoding="utf-8")
        code, resp2 = self._run("respond", "--verdict", str(v1),
                                "--from-json", str(d_json))
        self.assertEqual(code, 0, resp2)
        self.assertIsInstance(resp2, str)
        self.assertIn('author="claude"', resp2)

    def test_c_ledger_add_refuses_a_mismatched_disposition_before_appending(
            self):
        """Round-4 F1's falsification, third door: standalone `ledger add`
        of a disposition envelope never compared its author against the
        recorded request at all — `disposition_events`' own docstring
        documented that this door "carries the wire author unverified".
        Built directly with `wire.emit_disposition` here, bypassing
        `respond` entirely, the way a disposition arriving from another
        installation does.

        MUTATION: removing the `transport.check_disposition_author` call
        `cmd_ledger_add`'s disposition branch now makes before
        `validate_disposition`/`ledger.add_all` makes the mismatched
        `ledger add` below exit 0 and append an `author: "codex"`
        disposition event — this test's refusal assertions fail.
        """
        head = _git(self.repo, "rev-parse", "HEAD")
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              "--base", self.base, "--local-only")
        self.assertEqual(code, 0, rec)
        req = next(e for e in Ledger(self.state).events()
                  if e.get("event") == "request" and e.get("round") == 1)
        self.assertEqual(req.get("author"), "claude")
        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head), encoding="utf-8")
        code, closed = self._run("close", "--verdict", str(v1))
        self.assertEqual(code, 0, closed)

        scratch_cfg = config.load(self.repo)
        dispositions = [{"finding_id": "F1", "disposition": "accepted",
                         "payload": {"change": "fixed",
                                     "verification": "observed",
                                     "falsification": {
                                         "status": "pass",
                                         "mutation": "fails_without_fix"}}}]

        def _envelope(author):
            return wire.emit_disposition(
                tag=scratch_cfg.wrapper_tag, verdict_sha=head, head=head,
                author=author, round_no=1, dispositions=dispositions)

        d_md = self.tmp / "d-codex.md"
        d_md.write_text(_envelope("codex"), encoding="utf-8")
        before_ledger = self.state.joinpath("ledger.jsonl").read_bytes()

        code, resp = self._run("ledger", "add", str(d_md))

        self.assertEqual(code, 1, resp)
        self.assertIn("author", resp.get("error", ""))
        self.assertEqual(
            self.state.joinpath("ledger.jsonl").read_bytes(), before_ledger,
            "the mismatched disposition must append no ledger event")

        # The control: the otherwise identical envelope, naming the
        # request's real author, is accepted and recorded with it.
        d_md2 = self.tmp / "d-claude.md"
        d_md2.write_text(_envelope("claude"), encoding="utf-8")

        code, resp2 = self._run("ledger", "add", str(d_md2))

        self.assertEqual(code, 0, resp2)
        disp = [e for e in Ledger(self.state).events()
               if e.get("event") == "disposition"]
        self.assertEqual(len(disp), 1, disp)
        self.assertEqual(disp[0].get("author"), "claude")

    def _raw_envelope(self, cfg, head, dispositions, *,
                      wrapper_author, body_author):
        """A disposition envelope with the wrapper's `author` attribute and
        the body's `author` member set INDEPENDENTLY (`None` omits the
        stamp entirely) — the states `wire.emit_disposition` cannot
        produce, since its single `author: str` parameter always stamps
        both identically. Round 5 F1: standalone `ledger add` is the one
        door that reads an already-written file, so the two stamps can
        already disagree, or either can be missing, before this door ever
        sees the envelope."""
        data = {"verdict_sha": head, "head": head, "round": 1,
                "dispositions": dispositions}
        if body_author is not None:
            data["author"] = body_author
        attrs = f'verdict_sha="{head}" head="{head}"'
        if wrapper_author is not None:
            attrs += f' author="{wrapper_author}"'
        attrs += (f' round="1" tool="{wire.tool_identity()}" '
                 f'shape="{wire.shape_identity()}"')
        body = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)
        tag = cfg.wrapper_tag
        return (f"<{tag}-review-disposition {attrs}>\n{body}\n"
               f"</{tag}-review-disposition>\n")

    def test_d_ledger_add_derives_an_omitted_author_from_the_recorded_request(
            self):
        """Round-5 F1's falsification: standalone `ledger add` passed only
        the wrapper attribute into `check_disposition_author`, which
        returned without action whenever the SUPPLIED author was `None` —
        even when the round's recorded request named one. So a disposition
        that named no author at all (both wrapper and body omitted, the
        way a hand-assembled or third-party envelope might arrive) still
        appended `author: null`, silently losing the round's real author.

        MUTATION: reverting `check_disposition_author` to `if expected is
        None or author is None: return` (round-5 F1's regression) — or
        dropping the `parsed.attrs["author"] = resolved` /
        `parsed.data["author"] = resolved` assignment in `cmd_ledger_add`
        that carries its answer forward — makes this envelope append
        `author: null` again and this test's assertion fails.
        """
        head = _git(self.repo, "rev-parse", "HEAD")
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              "--base", self.base, "--local-only")
        self.assertEqual(code, 0, rec)
        req = next(e for e in Ledger(self.state).events()
                  if e.get("event") == "request" and e.get("round") == 1)
        self.assertEqual(req.get("author"), "claude")
        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head), encoding="utf-8")
        code, closed = self._run("close", "--verdict", str(v1))
        self.assertEqual(code, 0, closed)

        scratch_cfg = config.load(self.repo)
        dispositions = [{"finding_id": "F1", "disposition": "accepted",
                         "payload": {"change": "fixed",
                                     "verification": "observed",
                                     "falsification": {
                                         "status": "pass",
                                         "mutation": "fails_without_fix"}}}]

        d_md = self.tmp / "d-omitted.md"
        d_md.write_text(self._raw_envelope(
            scratch_cfg, head, dispositions,
            wrapper_author=None, body_author=None), encoding="utf-8")

        code, resp = self._run("ledger", "add", str(d_md))

        self.assertEqual(code, 0, resp)
        disp = [e for e in Ledger(self.state).events()
               if e.get("event") == "disposition"]
        self.assertEqual(len(disp), 1, disp)
        self.assertEqual(
            disp[0].get("author"), "claude",
            "an omitted disposition author must derive to the round's "
            "recorded request author, never be recorded as null")

    def test_e_ledger_add_still_admits_the_legacy_no_request_author_state(
            self):
        """The distinct control the Required outcome names explicitly:
        derivation only applies when the round's REQUEST carries an
        author. A ledger whose request predates the field (or is simply
        older than it) has nothing to derive from, and an omitted
        disposition author there is the declared legacy state — recorded
        as `author: null`, exactly as before round-5 F1's fix, and NOT a
        refusal.

        MUTATION: making `check_disposition_author`/`cmd_ledger_add`
        derive or refuse unconditionally, even absent a recorded request
        author, makes this legacy ledger addition raise or record a
        fabricated author instead of the documented null.
        """
        head = _git(self.repo, "rev-parse", "HEAD")
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              "--base", self.base, "--local-only")
        self.assertEqual(code, 0, rec)
        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head), encoding="utf-8")
        code, closed = self._run("close", "--verdict", str(v1))
        self.assertEqual(code, 0, closed)

        # Simulate a ledger whose round-1 request predates the `author`
        # field: strip it from the recorded request event directly, the
        # state `request_author` documents as indistinguishable from an
        # older ledger.
        ledger_path = self.state / "ledger.jsonl"
        lines = ledger_path.read_text(encoding="utf-8").splitlines()
        rewritten = []
        stripped = 0
        for line in lines:
            ev = json.loads(line)
            if ev.get("event") == "request" and ev.get("round") == 1:
                self.assertIn("author", ev)
                del ev["author"]
                stripped += 1
            rewritten.append(json.dumps(ev, sort_keys=True,
                                        ensure_ascii=False))
        self.assertEqual(stripped, 1, "expected exactly one round-1 request")
        ledger_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

        scratch_cfg = config.load(self.repo)
        dispositions = [{"finding_id": "F1", "disposition": "accepted",
                         "payload": {"change": "fixed",
                                     "verification": "observed",
                                     "falsification": {
                                         "status": "pass",
                                         "mutation": "fails_without_fix"}}}]
        d_md = self.tmp / "d-legacy.md"
        d_md.write_text(self._raw_envelope(
            scratch_cfg, head, dispositions,
            wrapper_author=None, body_author=None), encoding="utf-8")

        code, resp = self._run("ledger", "add", str(d_md))

        self.assertEqual(code, 0, resp)
        disp = [e for e in Ledger(self.state).events()
               if e.get("event") == "disposition"]
        self.assertEqual(len(disp), 1, disp)
        self.assertIsNone(
            disp[0].get("author"),
            "with no recorded request author, an omitted disposition "
            "author is the declared legacy state, not a fabricated one")

    def test_f_ledger_add_accepts_author_present_in_only_one_stamp(self):
        """Presence coverage: `wire.emit_disposition` always stamps the
        wrapper attribute and the body member identically, so an envelope
        with only ONE of the two set is a state only a hand-assembled or
        edited-after-the-fact file (exactly `ledger add`'s door) can
        produce. Either stamp alone is enough to supply the author `ledger
        add` compares against the recorded request.
        """
        head = _git(self.repo, "rev-parse", "HEAD")
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              "--base", self.base, "--local-only")
        self.assertEqual(code, 0, rec)
        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head), encoding="utf-8")
        code, closed = self._run("close", "--verdict", str(v1))
        self.assertEqual(code, 0, closed)
        scratch_cfg = config.load(self.repo)
        dispositions = [{"finding_id": "F1", "disposition": "accepted",
                         "payload": {"change": "fixed",
                                     "verification": "observed",
                                     "falsification": {
                                         "status": "pass",
                                         "mutation": "fails_without_fix"}}}]

        for case, wrapper_author, body_author in (
                ("wrapper only", "claude", None),
                ("body only", None, "claude")):
            with self.subTest(case=case):
                d_md = self.tmp / f"d-{case.replace(' ', '-')}.md"
                d_md.write_text(self._raw_envelope(
                    scratch_cfg, head, dispositions,
                    wrapper_author=wrapper_author,
                    body_author=body_author), encoding="utf-8")

                code, resp = self._run("ledger", "add", str(d_md))

                self.assertEqual(code, 0, resp)
                # Each envelope's bytes differ (only one stamp set), so its
                # digest and `batch` differ too — this appends a NEW
                # disposition event rather than deduping against the
                # other case's; the most recent one is this case's.
                disp = [e for e in Ledger(self.state).events()
                       if e.get("event") == "disposition"]
                self.assertEqual(
                    disp[-1].get("author"), "claude",
                    f"{case}: the single supplied stamp must be recorded, "
                    f"agreeing with the round's recorded request author")

    def test_g_ledger_add_refuses_wrapper_body_author_disagreement(self):
        """Agreement coverage: when BOTH stamps are present but name
        different authors, there is no single supplied value left to
        compare against the recorded request — `ledger add` must refuse
        outright, before anything is validated or appended, rather than
        silently preferring one stamp over the other.

        The wrapper here names the round's REAL recorded author
        (`claude`), so the pre-existing supplied-vs-expected mismatch
        check alone would not catch this: a wrapper that already agrees
        with the request passes it. Only comparing wrapper against body
        catches the body's disagreeing `codex` — the exact case that
        matters, since it is the one an author-vs-expected check cannot
        distinguish from an honest, agreeing envelope.

        MUTATION: dropping `transport.disposition_supplied_author`'s
        disagreement check (falling back to the wrapper attribute alone,
        as the door did before round 5 F1) makes this envelope succeed and
        record the wrapper's `claude`, even though the body secretly
        claims `codex` — this test's refusal assertions fail.
        """
        head = _git(self.repo, "rev-parse", "HEAD")
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              "--base", self.base, "--local-only")
        self.assertEqual(code, 0, rec)
        v1 = self.tmp / "v1.md"
        v1.write_text(verdict_text(sha=head), encoding="utf-8")
        code, closed = self._run("close", "--verdict", str(v1))
        self.assertEqual(code, 0, closed)
        scratch_cfg = config.load(self.repo)
        dispositions = [{"finding_id": "F1", "disposition": "accepted",
                         "payload": {"change": "fixed",
                                     "verification": "observed",
                                     "falsification": {
                                         "status": "pass",
                                         "mutation": "fails_without_fix"}}}]
        d_md = self.tmp / "d-disagree.md"
        d_md.write_text(self._raw_envelope(
            scratch_cfg, head, dispositions,
            wrapper_author="claude", body_author="codex"), encoding="utf-8")
        before_ledger = self.state.joinpath("ledger.jsonl").read_bytes()

        code, resp = self._run("ledger", "add", str(d_md))

        self.assertEqual(code, 1, resp)
        self.assertIn("author", resp.get("error", ""))
        self.assertEqual(
            self.state.joinpath("ledger.jsonl").read_bytes(), before_ledger,
            "a disposition whose wrapper and body name different authors "
            "must append no ledger event")


class TestGateIdIsASafeFilenameComponent(unittest.TestCase):
    """Round 6 F2: a gate id is a logical identity AND a path component.

    `_retain_output` writes `<ledger>/gate-output/<sha>/<run>/<id>.log`, so an
    ABSOLUTE id discards the directory entirely — measured: a manifest whose
    id was an absolute path was accepted, ran, and wrote outside the ledger's
    subtree — while separators and dot segments escape or alias it. Round 5
    established that two rows cannot share an id STRING; two distinct strings
    can still name one destination, and on a case-folding filesystem
    routinely do.

    The grammar is closed rather than the escapes enumerated: containment and
    one-id-one-file are properties of the alphabet, not of a blacklist.

    Mutations: accept any non-blank id and the escape rows pass; compare ids
    case-sensitively and the alias row passes.
    """

    def _cfg(self):
        return dataclasses.replace(CFG, ledger_dir=None)

    def _rows(self, *ids):
        return "".join(f'[[gates]]\nid = "{i}"\ncommand = ["true"]\n'
                       for i in ids)

    def _refused(self, body, door):
        with self.assertRaises(config.ConfigError) as ctx:
            if door == "from_text":
                config.from_text(body, self._cfg(), source="t")
            else:
                tmp = Path(tempfile.mkdtemp(prefix="gate-id-"))
                self.addCleanup(lambda: __import__("shutil").rmtree(
                    tmp, ignore_errors=True))
                subprocess.run(["git", "init", "-q", str(tmp)], check=True,
                               capture_output=True, timeout=60)
                (tmp / "review.toml").write_text(body, encoding="utf-8")
                config.load(tmp)
        return str(ctx.exception)

    ESCAPES = {
        "absolute": "/tmp/escaped",
        "parent segment": "../escaped",
        "separator": "a/b",
        "leading dot": ".hidden",
        "dot segment": "..",
        "backslash": "a\\b",
        "space": "two words",
    }

    def test_no_escape_form_survives_either_configuration_door(self):
        for door in ("from_text", "load"):
            for label, gid in self.ESCAPES.items():
                with self.subTest(door=door, form=label):
                    self.assertIn("filename component",
                                  self._refused(self._rows(gid), door))

    def test_two_ids_naming_one_destination_refuse(self):
        """Distinct strings, one file: the alias round 5's string equality
        could not see."""
        self.assertIn("one retained output",
                      self._refused(self._rows("tests", "TESTS"),
                                    "from_text"))

    def test_distinct_valid_ids_are_the_paired_control(self):
        cfg = config.from_text(self._rows("tests", "lint-2", "a.b_c"),
                               self._cfg(), source="t")
        self.assertEqual([g["id"] for g in cfg.gates],
                         ["tests", "lint-2", "a.b_c"])

    def test_every_admitted_id_stays_an_immediate_child(self):
        """The property the grammar exists to give `_retain_output`: for any
        id the schema admits, the destination is one level under the
        per-SHA directory and nowhere else."""
        target = Path("/ledger/gate-output/abcdef")
        cfg = config.from_text(self._rows(*self.CONTAINMENT), self._cfg(),
                               source="t")
        for gate in cfg.gates:
            with self.subTest(id=gate["id"]):
                out = (target / f"{gate['id']}.log").resolve()
                self.assertEqual(out.parent, target.resolve())

    CONTAINMENT = ("tests", "lint-2", "a.b_c", "A", "z9")


if __name__ == "__main__":
    unittest.main()

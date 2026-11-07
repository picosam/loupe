"""The lifecycle's edges, through the real `bin/loupe` in a scratch repository
(`_real_cli.Scratch`: its own identity, HOME, hooks and state directory). One
class per defect of the 2026-11-04 read of 0.33.0, each row beside the nearest
valid control; a refusal is asserted on the bytes it guards.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import itertools
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from review import cli, transport
from review.ledger import Ledger
from review.tests import synth
from review.tests._real_cli import Scratch, git, loupe, loupe_env, restored

TEST ="review/tests/test_x.py::test_y"
#: The two commands a verdict enters a review by, and the two terms it rules.
DOORS = (("close", "--verdict"), ("ledger", "add"))
VERDICTS = (("clean to advance", "None\n"),
            ("changes requested", synth.finding(1, evidence="f.txt:1")))


def verdict_text(sha, term="changes requested", findings="None\n", tail=""):
    return (f'<{synth.TAG}-review-verdict sha="{sha}">\nVERDICT: {term}\n\n'
            f"## findings\n\n{findings}\n## evidence checked\n\nf.txt\n{tail}"
            f"</{synth.TAG}-review-verdict>\n")


class Round(unittest.TestCase):
    """A scratch repository and the verbs of one review."""

    def setUp(self):
        self.s = Scratch(self, "lifecycle-")
        self.n = 0

    def run_ok(self, *argv, **kw):
        code, rec, out, err = loupe(self.s, *argv, **kw)
        self.assertEqual(code, 0, (argv, out, err))
        return rec

    def handoff(self, *extra):
        return self.run_ok("handoff", "--claim-file", str(self.s.claim),
                           *extra)

    def restore(self, key, make):
        """`make()` on a scratch as built, done once in this process and put
        back for each test (`restored`): the bytes a test's own would leave and
        the record it would have been given. A scratch this test has already
        used is refused, since putting one back would lose that."""
        s = self.s
        self.assertFalse(s.state.exists(), "this scratch has already run")
        self.assertEqual(
            (s.repo / ".git" / "refs" / "heads" / "main").read_text(
                encoding="utf-8").strip(), s.head, "this scratch has moved")

        def build(case):
            outer, n = self.s, self.n
            self.s, self.n = Scratch(case, "lifecycle-"), 0
            try:
                return self.s, make()
            finally:
                self.s, self.n = outer, n
        self.s, facts = restored(self, key, build)
        return facts

    def first_handoff(self):
        """`handoff --base` on this scratch, restored. Returns its record."""
        return self.restore("first-handoff",
                            lambda: self.handoff("--base", self.s.base))

    def two_emissions(self):
        """Round 1 handed off, a commit, and handed off again, restored."""
        def make():
            first = self.handoff("--base", self.s.base)
            self.commit()
            second = self.handoff("--base", self.s.base)
            self.assertEqual(first["round"], second["round"])
            self.assertNotEqual(first["sha"], second["sha"])
            return first, second
        return self.restore("two-emissions", make)

    def write(self, name, text):
        path = self.s.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def verdict_file(self, sha, term="changes requested",
                     findings="None\n", tail=""):
        self.n += 1
        return self.write(f"v{self.n}.md",
                          verdict_text(sha, term, findings, tail))

    def commit(self, text="more\n"):
        with (self.s.repo / "f.txt").open("a", encoding="utf-8") as fh:
            fh.write(text)
        git(self.s.repo, "commit", "-qam", "step")
        return git(self.s.repo, "rev-parse", "HEAD")

    def ledger(self):
        return [json.loads(l) for l in (self.s.state / "ledger.jsonl")
                .read_text(encoding="utf-8").splitlines() if l.strip()]

    def guarded(self):
        s = self.s
        state = {p.relative_to(s.state).as_posix(): p.read_bytes()
                 for p in sorted(s.state.rglob("*"))
                 if p.is_file() and not p.name.endswith(".lock")}
        return {"state": state, "remote": Scratch.snapshot(s.remote, bare=True),
                "checkout": Scratch.snapshot(s.repo)}

    def refused(self, *argv, says, before=None, **kw):
        """A refusal: nonzero, no traceback, `says` in its words, and every
        byte it guards unchanged (`before`: as a caller already read them)."""
        if before is None:
            Scratch.settle(self.s.repo)
            before = self.guarded()
        code, rec, out, err = loupe(self.s, *argv, **kw)
        self.assertNotIn("Traceback", err)
        self.assertNotEqual(code, 0, out)
        self.assertIn(says, (rec or {}).get("error", out) + err)
        self.assertEqual(self.guarded(), before, f"{argv} moved guarded state")
        return rec

    def reset_to_here(self):
        """A way back to this state directory, for rows that share one."""
        state = self.s.state
        held = {p: p.read_bytes() for p in state.rglob("*") if p.is_file()}

        def reset():
            for p in state.rglob("*"):
                if p.is_file() and p not in held:
                    p.unlink()
            for p, data in held.items():
                p.write_bytes(data)
        return reset

    def each_refused(self, rows):
        """Each `(row, argv, says)` refused, its bytes against one reading and
        the state put back after it, so that a row that wrote decides no other."""
        Scratch.settle(self.s.repo)
        before, reset = self.guarded(), self.reset_to_here()
        for row, argv, says in rows:
            with self.subTest(row=row):
                self.refused(*argv, says=says, before=before)
            reset()

    def spellings(self, sha, *rounds):
        """A verdict at `sha` through each door, by term and by how the round
        is spelled: `((door, term, flags), argv)`."""
        for door, (term, findings) in itertools.product(DOORS, VERDICTS):
            path = self.verdict_file(sha, term, findings)
            for flags in rounds:
                yield (door[0], term, flags), (*door, str(path), *flags)

    def every_door_refuses(self, sha, at, says):
        """Every spelling of a verdict at `sha`, where `at` is the round it
        derives: omitted and matching refused with `says`, mismatching by the
        round."""
        wrong = ("--round", str(at + 1))
        self.each_refused(
            (row, argv, "does not hold" if row[2] == wrong else says)
            for row, argv in self.spellings(sha, (), ("--round", str(at)),
                                            wrong))

    def briefs(self, rec, superseded=1):
        """`brief` names `rec`: the commit, the bytes it relays and the count
        beside them. Returns its record."""
        seen = self.run_ok("brief")
        self.assertEqual(seen["sha"], rec["sha"])
        self.assertEqual(seen["source"], rec["kept"])
        self.assertIn(rec["kept"], seen["relay"])
        self.assertEqual(seen["superseded"], superseded)
        return seen

    def first_round(self):
        """Round 1 handed off and closed with F1 standing, restored. Returns
        the verdict's path and the request's record."""
        def make():
            rec = self.handoff("--base", self.s.base)
            path = self.verdict_file(rec["sha"], findings=synth.finding(
                1, severity="High", falsification=TEST, evidence="f.txt:1"))
            self.run_ok("close", "--verdict", str(path))
            return path, rec
        path, rec = self.restore("first-round", make)
        self.n = max(self.n, 1)
        return path, rec

    def respond(self, verdict, head, record, name="d"):
        self.n += 1
        answer = self.write(f"{name}{self.n}.json", json.dumps(
            {"head": head, "dispositions": [record]}))
        return loupe(self.s, "respond", "--verdict", str(verdict),
                     "--from-json", str(answer),
                     "--out", str(self.s.root / f"{name}{self.n}.md"))


ACCEPTED = {"finding_id": "F1", "disposition": "accepted", "payload": {
    "change": "fixed", "verification": "ran",
    "falsification": {"status": "pass", "mutation": "fails_without_fix"}}}
REFUTED = {"finding_id": "F1", "disposition": "refuted", "payload": {
    "evidence": "not so", "reproduction": "run", "why": "because"}}


def block(rec) -> str:
    text = Path(rec["kept"]).read_text(encoding="utf-8")
    start = text.index("## Disposition ledger")
    return text[start:text.index("\n## ", start + 1)]


class TestAWarmHandoffKeysOnTheAnswers(Round):
    """D1. The class: what happened to round 1's answers between two
    hand-offs of round 2 at one tip."""

    def second_round(self):
        verdict, _ = self.first_round()
        head = self.commit()
        code, _, out, err = self.respond(verdict, head, ACCEPTED)
        self.assertEqual(code, 0, (out, err))
        rec = self.handoff()
        self.assertIs(rec["cached"], False)
        self.assertIn("accepted", block(rec))
        return verdict, head, rec

    def test_round_one_has_no_answers_and_stays_warm(self):
        first = self.first_handoff()
        self.assertIs(first["cached"], False)
        second = self.handoff("--base", self.s.base)
        self.assertIs(second["cached"], True)
        self.assertEqual(second["digest"], first["digest"])

    def test_nothing_answered_since_is_warm(self):
        _, _, rec = self.second_round()
        again = self.handoff()
        self.assertIs(again["cached"], True)
        self.assertEqual(again["digest"], rec["digest"])

    def test_the_same_answer_again_is_warm(self):
        verdict, head, rec = self.second_round()
        code, _, out, err = self.respond(verdict, head, ACCEPTED)
        self.assertEqual(code, 0, (out, err))
        again = self.handoff()
        self.assertIs(again["cached"], True, "the record did not change")
        self.assertEqual(again["digest"], rec["digest"])

    def test_another_answer_is_cold_and_shows_the_standing_one(self):
        verdict, head, rec = self.second_round()
        code, _, out, err = self.respond(verdict, head, REFUTED)
        self.assertEqual(code, 0, (out, err))
        again = self.handoff()
        self.assertIs(again["cached"], False, "served a superseded answer")
        self.assertNotEqual(again["digest"], rec["digest"])
        self.assertIn("**refuted", block(again))
        self.assertNotIn("**accepted", block(again))
        warm = self.handoff()
        self.assertIs(warm["cached"], True, "the new request is warm again")
        self.assertEqual(warm["digest"], again["digest"])

    def test_a_request_recording_no_digest_is_cold(self):
        _, _, _ = self.second_round()
        path = self.s.state / "ledger.jsonl"
        rows = self.ledger()
        for row in rows:
            row.pop("answers_digest", None)
        path.write_text("".join(json.dumps(r, sort_keys=True) + "\n"
                                for r in rows), encoding="utf-8")
        self.assertIs(self.handoff()["cached"], False)


class TestAVerdictBehindAClosure(Round):
    """D2. The class: a verdict on the open request of a closed lineage,
    by term; the controls are the same verdicts before the closure."""

    def closed(self):
        rec = self.first_handoff()
        self.run_ok("close", "--lineage", "--reason", "abandon", "--by",
                    "osama")
        return rec

    def test_a_clean_verdict_is_refused_naming_the_closure(self):
        rec = self.closed()
        path = self.verdict_file(rec["sha"], "clean to advance")
        out = self.refused("close", "--verdict", str(path), says="abandon")
        self.assertIn("osama", out["error"])
        self.assertEqual(out["next_kind"], "blocked")

    def test_changes_requested_is_refused_and_prints_no_open_lineage(self):
        rec = self.closed()
        path = self.verdict_file(rec["sha"], findings=synth.finding(
            1, evidence="f.txt:1"))
        out = self.refused("close", "--verdict", str(path), says="abandon")
        self.assertNotIn("lineage", {k for k, v in out.items() if v == "open"})

    def test_control_the_same_verdicts_record_before_the_closure(self):
        rec = self.first_handoff()
        path = self.verdict_file(rec["sha"], findings=synth.finding(
            1, evidence="f.txt:1"))
        out = self.run_ok("close", "--verdict", str(path))
        self.assertEqual(out["lineage"], "open")
        head = self.commit()
        self.respond(path, head, {"finding_id": "F1", "disposition": "refuted",
                                  "payload": {"evidence": "f.txt:1"}})
        second = self.handoff()
        fp = next(e["fp"] for e in self.ledger() if e["event"] == "finding")
        clean = self.verdict_file(second["sha"], "clean to advance", tail=(
            f"\n## closures\n\n- {fp} withdrawn: fine\n"))
        self.assertIn("closed", self.run_ok("close", "--verdict",
                                            str(clean))["lineage"])

    def test_the_replay_of_a_verdict_that_closed_it_stays_refused(self):
        rec = self.first_handoff()
        path = self.verdict_file(rec["sha"], "clean to advance")
        self.run_ok("close", "--verdict", str(path))
        self.refused("close", "--verdict", str(path), says="no request")

    def by_decision(self):
        path, rec = self.first_round()
        self.run_ok("close", "--lineage", "--reason", "abandon", "--by",
                    "osama")
        return rec, path

    def by_clean_verdict(self):
        rec = self.first_handoff()
        path = self.verdict_file(rec["sha"], "clean to advance")
        self.run_ok("close", "--verdict", str(path))
        return rec, path

    def by_advance(self):
        path, rec = self.first_round()
        self.run_ok("waive", "--finding", "F1", "--reason", "accepted", "--by",
                    "osama")
        self.run_ok("authorize-advance", "--reason", "ship it", "--by",
                    "osama")
        return rec, path

    #: Every way a lineage ends, by what ended it and how the refusal says so.
    ENDINGS = (("decision", by_decision, "decision at round 1"),
               ("clean", by_clean_verdict, "clean at round 1"),
               ("authorization", by_advance, "authorization at round 1"))

    def test_every_door_refuses_the_open_request_of_a_closed_lineage(self):
        """F1 of round 2: `ledger add` recorded four events here."""
        rec = self.closed()
        self.every_door_refuses(rec["sha"], 1, "abandon")

    def test_every_door_refuses_behind_each_ending(self):
        """A ruled round first: the bytes on record and a second ruling, by
        door and by round. Only `--round 1` on another verdict at `ledger add`
        says why differently: a round is ruled once. Nothing behind a marker
        is metadata. Then an open round: `ledger add` of a request reaches
        behind a marker, so a verdict can find one whatever ended the lineage."""
        for name, end, says in self.ENDINGS:
            self.s = Scratch(self, "lifecycle-")
            rec, recorded = end(self)
            term, findings = next(v for v in VERDICTS
                                  if v[0] not in recorded.read_text("utf-8"))
            other = self.verdict_file(rec["sha"], term, findings)
            rows = [((name, "ledger", "tokens"),
                     ("ledger", "add", str(recorded), "--round", "1",
                      "--tokens", "7"), "no request")]
            for door, path, flags in itertools.product(
                    DOORS, (recorded, other),
                    ((), ("--round", "1"), ("--round", "2"))):
                second = (door == ("ledger", "add") and path == other
                          and flags == ("--round", "1"))
                rows.append(((name, door[0], flags, path.name),
                             (*door, str(path), *flags),
                             "different verdict" if second else "no request"))
            self.each_refused(rows)
            self.run_ok("ledger", "add", rec["kept"], "--round", "2")
            self.every_door_refuses(rec["sha"], 2, says)


class TestAVerdictOnAReplacedEmission(Round):
    """D3. The class: which emission of one round a verdict binds, with the
    author's hand-offs in every order this ledger can record."""

    def test_a_verdict_at_the_replaced_sha_is_refused_by_term(self):
        first, second = self.two_emissions()
        self.run_ok("take", first["kept"], "--as", "codex")
        for term, findings in (("clean to advance", "None\n"),
                               ("changes requested", synth.finding(
                                   1, evidence="f.txt:1"))):
            with self.subTest(term=term):
                path = self.verdict_file(first["sha"], term, findings)
                out = self.refused("close", "--verdict", str(path),
                                   says=second["sha"][:12])
                self.assertIn("nothing was recorded", out["error"])

    def test_control_the_survivor_closes(self):
        _, second = self.two_emissions()
        self.run_ok("take", second["kept"], "--as", "codex")
        path = self.verdict_file(second["sha"], "clean to advance")
        self.assertIn("closed", self.run_ok("close", "--verdict",
                                            str(path))["lineage"])

    def test_a_warm_return_to_the_first_commit_makes_it_the_survivor(self):
        first, _ = self.two_emissions()
        git(self.s.repo, "reset", "-q", "--hard", first["sha"])
        again = self.handoff("--base", self.s.base)
        self.assertIs(again["cached"], True)
        path = self.verdict_file(first["sha"], "clean to advance")
        self.assertIn("closed", self.run_ok("close", "--verdict",
                                            str(path))["lineage"])

    def test_a_reviewers_own_ledger_refuses_by_its_newest_take(self):
        first, second = self.two_emissions()
        own = self.s.root / "state-reviewer"
        self.run_ok("take", first["kept"], "--as", "codex", state=own)
        self.run_ok("take", second["kept"], "--as", "codex", state=own)
        path = self.verdict_file(first["sha"], "clean to advance")
        code, rec, out, _ = loupe(self.s, "close", "--verdict", str(path),
                                  state=own)
        self.assertEqual(code, 1, out)
        self.assertIn(second["sha"][:12], rec["error"])

    def test_a_reviewers_own_ledger_answers_ledger_add_by_its_newest_take(self):
        first, second = self.two_emissions()
        own = self.s.root / "state-reviewer"
        self.run_ok("take", first["kept"], "--as", "codex", state=own)
        self.run_ok("take", second["kept"], "--as", "codex", state=own)
        before = (own / "ledger.jsonl").read_bytes()
        path = self.verdict_file(first["sha"], "clean to advance")
        code, rec, out, _ = loupe(self.s, "ledger", "add", str(path), state=own)
        self.assertEqual(code, 1, out)
        self.assertIn(second["sha"][:12], rec["error"])
        self.assertEqual((own / "ledger.jsonl").read_bytes(), before)
        path = self.verdict_file(second["sha"], "clean to advance")
        self.assertGreater(self.run_ok("ledger", "add", str(path),
                                       state=own)["events_added"], 0)

    def test_every_door_refuses_the_replaced_emission(self):
        """F1 of round 2: `ledger add` recorded four events here, and the
        survivor's request left the open view unruled."""
        first, second = self.two_emissions()
        self.every_door_refuses(first["sha"], 1, second["sha"][:12])
        self.assertEqual(self.run_ok("brief")["sha"], second["sha"])

    def test_control_the_survivor_records_by_door_term_and_round(self):
        _, second = self.two_emissions()
        reset = self.reset_to_here()
        for door, (term, findings), flags in itertools.product(
                DOORS, VERDICTS, ((), ("--round", "1"))):
            reset()
            with self.subTest(door=door[0], term=term, flags=flags):
                path = self.verdict_file(second["sha"], term, findings)
                rec = self.run_ok(*door, str(path), *flags)
                self.assertGreater(rec["events_added"], 0)
                self.assertEqual(
                    [(e["round"], e["sha"]) for e in self.ledger()
                     if e["event"] == "verdict"], [(1, second["sha"])])
                # `ledger add` records a ruling and never ends a lineage.
                self.assertEqual(
                    any(e["event"] == Ledger.LINEAGE_CLOSED
                        for e in self.ledger()),
                    door[0] == "close" and term == "clean to advance")

    def test_a_mismatching_round_is_refused_with_that_doors_own_retry(self):
        _, second = self.two_emissions()
        for row, argv in self.spellings(second["sha"], ("--round", "2")):
            with self.subTest(row=row):
                out = self.refused(*argv, says="does not hold")
                self.assertEqual(out["next"],
                                 " ".join(("loupe", *argv[:-2])))

    def test_every_door_refuses_either_emission_behind_a_decision(self):
        first, second = self.two_emissions()
        self.run_ok("close", "--lineage", "--reason", "abandon", "--by",
                    "osama")
        for rec in (first, second):
            self.every_door_refuses(rec["sha"], 1, "abandon")

    def test_control_the_second_emission_is_what_brief_names(self):
        _, second = self.two_emissions()
        self.briefs(second)

    def test_takes_of_either_emission_do_not_move_what_brief_names(self):
        first, second = self.two_emissions()
        for rec in (first, second, first):
            self.run_ok("take", rec["kept"], "--as", "codex")
            self.briefs(second)

    def test_control_a_reviewers_own_ledger_follows_its_newest_take(self):
        first, second = self.two_emissions()
        for order in ((first, second), (second, first)):
            with self.subTest(newest=order[1]["sha"][:12]):
                own = self.s.root / f"state-{order[1]['sha'][:12]}"
                for rec in order:
                    self.run_ok("take", rec["kept"], "--as", "codex",
                                state=own)
                seen = self.run_ok("brief", state=own)
                self.assertEqual(seen["sha"], order[1]["sha"])
                self.assertEqual(Path(seen["source"]).name,
                                 Path(order[1]["kept"]).name)
                self.assertEqual(seen["superseded"], 1)

    def test_control_a_decision_over_the_survivor_still_reports_discarded(self):
        first, second = self.two_emissions()
        self.run_ok("close", "--lineage", "--reason", "abandon", "--by",
                    "osama")
        out = self.refused("brief", says="discarded")
        self.assertIn(second["sha"][:12], out["error"])
        self.assertNotIn(first["sha"][:12], out["error"])

    def test_another_branchs_request_is_discarded_beside_the_survivor(self):
        """A ledger from before the key: one lineage, two branches at one
        round. The other branch's emission replaced nothing and was replaced
        by nothing, whichever row stands for this one."""
        head = self.s.head
        ledger = Ledger(self.s.state)
        for row in ({"sha": "f" * 40, "branch": "other", "source_digest": "1"},
                    {"sha": head, "branch": "main", "source_digest": "2",
                     "claim_digest": "c"}):
            ledger.add({"event": "request", "round": 1, "bytes": 1, **row},
                       lineage="Lpre")
        ledger.add({"event": Ledger.LINEAGE_CLOSED, "at_round": 1,
                    "reason": "x", "authorized_by": "y"}, lineage="Lpre")
        out = self.refused("brief", says="discarded")
        self.assertIn("f" * 12, out["error"])
        self.assertIn(head[:12], out["error"])

    def test_a_take_behind_the_marker_leaves_what_the_closure_discarded(self):
        first, second = self.two_emissions()
        own = self.s.root / "state-reviewer"
        self.run_ok("take", first["kept"], "--as", "codex", state=own)
        self.run_ok("close", "--lineage", "--reason", "abandon", "--by",
                    "osama", state=own)
        self.run_ok("take", second["kept"], "--as", "codex", state=own)
        code, rec, out, err = loupe(self.s, "brief", state=own)
        self.assertNotEqual(code, 0, out)
        self.assertIn("discarded", rec["error"], err)
        self.assertIn(first["sha"][:12], rec["error"])
        self.assertNotIn(second["sha"][:12], rec["error"])

    def test_take_of_the_replaced_emission_says_so(self):
        first, second = self.two_emissions()
        then = self.run_ok("take", first["kept"], "--as", "codex")["then"]
        self.assertIn(f"NOTICE: the author has re-emitted round 1 at "
                      f"{second['sha'][:12]}", then)

    def test_a_reviewers_own_ledger_takes_the_replacement_with_no_notice(self):
        first, second = self.two_emissions()
        own = self.s.root / "state-reviewer"
        self.run_ok("take", first["kept"], "--as", "codex", state=own)
        then = self.run_ok("take", second["kept"], "--as", "codex",
                           state=own)["then"]
        self.assertNotIn("NOTICE", then)
        path = self.verdict_file(second["sha"], "clean to advance")
        self.assertIn("closed", self.run_ok("close", "--verdict", str(path),
                                            state=own)["lineage"])

    def test_control_take_of_the_survivor_notices_nothing(self):
        _, second = self.two_emissions()
        then = self.run_ok("take", second["kept"], "--as", "codex")["then"]
        self.assertNotIn("NOTICE", then)

    def test_take_of_a_closed_lineages_request_says_so(self):
        rec = self.first_handoff()
        self.run_ok("close", "--lineage", "--reason", "abandon", "--by",
                    "osama")
        then = self.run_ok("take", rec["kept"], "--as", "codex")["then"]
        self.assertIn("NOTICE: lineage", then)
        self.assertIn("abandon", then)


SPY = """
import json, pathlib, sys
from review.cli import main
real, spy = pathlib.Path.open, sys.argv[1]
CONCURRENT = b'{"event": "note", "uid": "another-review"}\\n'
def opened(self, mode="r", *a, **kw):
    fh = real(self, mode, *a, **kw)
    if self.name != "ledger.jsonl" or "r" in mode:
        return fh
    class Writer:
        def __enter__(s): return s
        def __exit__(s, *e): fh.close()
        def write(s, blob):
            with open(spy, "a") as out:
                out.write(json.dumps([json.loads(l)["event"]
                                      for l in blob.splitlines()]) + "\\n")
            first = blob.index(b"\\n") + 1
            if sys.argv[2] in ("short", "enospc"):
                written = fh.write(blob[:first])
                with real(self, "ab") as other:
                    other.write(CONCURRENT)
                if sys.argv[2] == "enospc":
                    raise OSError(28, "No space left on device")
                return written
            cut = {"kill": 0, "half": len(blob) // 2,
                   "last": len(blob) - 1}.get(sys.argv[2])
            if cut is not None:
                import os
                fh.write(blob[:cut]); fh.flush(); os._exit(9)
            return fh.write(blob)
    return Writer()
pathlib.Path.open = opened
sys.exit(main(sys.argv[3:]))
"""


class TestOneRecordIsOneWrite(Round):
    """D4, by design rather than repair: a record is one write, so a close has
    no partial state to resume. A retry finds nothing written and redoes it,
    or finds it all written and is refused with its bytes unchanged; a write
    torn inside the record is D6's refusal. The spy wraps `Path.open` around
    the real `review.cli.main` with real argv; everything else in this class
    goes through `bin/loupe`."""

    def spied(self, *argv, mode="run"):
        from review.tests._real_cli import REPO_ROOT, loupe_env
        import subprocess
        import sys
        spy = self.s.root / "writes"
        spy.write_text("", encoding="utf-8")
        env = {**loupe_env(self.s, self.s.state),
               "PYTHONPATH": str(REPO_ROOT)}
        proc = subprocess.run(
            [sys.executable, "-B", "-c", SPY, str(spy),
             mode, *argv],
            cwd=self.s.repo, env=env, capture_output=True, text=True,
            stdin=subprocess.DEVNULL)
        return proc, [json.loads(l) for l in spy.read_text(
            encoding="utf-8").splitlines()]

    def findings_verdict(self, rec):
        return self.verdict_file(rec["sha"], findings=(
            synth.finding(1, severity="High", falsification=TEST,
                          evidence="f.txt:1")
            + synth.finding(2, evidence="f.txt:1")))

    def test_changes_requested_writes_its_findings_in_one_write(self):
        path = self.findings_verdict(self.first_handoff())
        proc, writes = self.spied("close", "--verdict", str(path))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(writes[0][:1], ["verdict"])
        self.assertEqual(writes[0].count("finding"), 2, writes)

    def test_a_clean_verdict_writes_its_closure_in_one_write(self):
        rec = self.first_handoff()
        path = self.verdict_file(rec["sha"], "clean to advance")
        proc, writes = self.spied("close", "--verdict", str(path))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(writes[0], ["verdict", "lineage_closed"])

    def test_a_kill_at_the_write_leaves_no_row_and_the_retry_records(self):
        path = self.findings_verdict(self.first_handoff())
        before = self.ledger()
        proc, _ = self.spied("close", "--verdict", str(path), mode="kill")
        self.assertEqual(proc.returncode, 9)
        self.assertEqual(self.ledger(), before)
        self.run_ok("close", "--verdict", str(path))
        self.assertEqual(sum(e["event"] == "finding" for e in self.ledger()), 2)

    def test_a_retry_after_it_was_all_written_changes_nothing(self):
        verdict, _ = self.first_round()
        self.refused("close", "--verdict", str(verdict), says="no request")

    def test_a_torn_write_is_refused_by_file_and_line_at_every_verb(self):
        for mode in ("half", "last"):
            with self.subTest(cut=mode):
                self.s = Scratch(self, "lifecycle-")
                path = self.findings_verdict(
                    self.first_handoff())
                proc, _ = self.spied("close", "--verdict", str(path), mode=mode)
                self.assertEqual(proc.returncode, 9)
                for argv in (("close", "--verdict", str(path)),
                             ("handoff", "--claim-file", str(self.s.claim)),
                             ("ledger", "report")):
                    out = self.refused(*argv, says="ledger.jsonl line ")
                    self.assertEqual(out["next_kind"], "blocked")

    def test_a_failed_write_is_loud_and_keeps_a_concurrent_record(self):
        for mode, why in (("short", "bytes written"), ("enospc", "No space")):
            with self.subTest(failure=mode):
                self.s = Scratch(self, "lifecycle-")
                path = self.findings_verdict(
                    self.first_handoff())
                ledger = self.s.state / "ledger.jsonl"
                before = ledger.read_bytes()
                proc, _ = self.spied("close", "--verdict", str(path), mode=mode)
                self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)
                out = json.loads(proc.stdout)
                self.assertEqual(out["next_kind"], "blocked")
                for said in (str(ledger), "may hold a partial batch", why):
                    self.assertIn(said, out["error"])
                self.assertIn("reads the end of that file", out["remedy"])
                after = ledger.read_bytes()
                self.assertTrue(after.startswith(before))
                self.assertIn(b'"another-review"', after)

    def test_control_a_whole_write_is_kept(self):
        path = self.findings_verdict(self.first_handoff())
        proc, _ = self.spied("close", "--verdict", str(path))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(sum(e["event"] == "finding"
                             for e in self.ledger()), 2)

    def test_an_advance_writes_its_closure_in_one_write(self):
        self.first_round()
        self.run_ok("waive", "--finding", "F1", "--reason", "later", "--by",
                    "osama")
        proc, writes = self.spied("authorize-advance", "--reason", "enough",
                                  "--by", "osama")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(["lineage_advanced", "lineage_closed"], writes)


class TestAGatePrintingAnyBytes(unittest.TestCase):
    """D5. The class: what a gate writes, by stream and encoding, and its
    exit code; a red one stays red, and the sibling's record survives."""

    def run_gate(self, code):
        gates = (f'[[gates]]\nid = "printer"\ncommand = ["python3.15", "-c", '
                 f'{json.dumps("import sys; " + code)}]\nblocking = true\n'
                 '[[gates]]\nid = "sibling"\ncommand = ["true"]\n'
                 'blocking = true\n')
        self.s = Scratch(self, "gate-bytes-", gates=gates)
        return loupe(self.s, "handoff", "--claim-file", str(self.s.claim),
                     "--base", self.s.base)

    CASES = {"latin-1 on stdout": r"sys.stdout.buffer.write(b'caf\xe9\n')",
             "latin-1 on stderr": r"sys.stderr.buffer.write(b'caf\xe9\n')",
             "a torn multi-byte": r"sys.stdout.buffer.write(b'caf\xc3')",
             "a NUL": r"sys.stdout.buffer.write(b'a\x00b\n')",
             "control: utf-8": r"print('café')"}

    def test_every_output_hands_off_with_both_records(self):
        for name, code in self.CASES.items():
            with self.subTest(case=name):
                rc, rec, out, err = self.run_gate(code)
                self.assertEqual(rc, 0, (out, err))
                self.assertNotIn("codec", out + err)
                text = Path(rec["kept"]).read_text(encoding="utf-8")
                self.assertIn('"id": "printer"', text)
                self.assertIn('"id": "sibling"', text)

    def test_the_retained_output_keeps_what_it_can(self):
        rc, rec, out, err = self.run_gate(self.CASES["latin-1 on stdout"])
        self.assertEqual(rc, 0, (out, err))
        logs = list((self.s.state / "gate-output").rglob("printer.log"))
        self.assertTrue(logs, "no retained output")
        self.assertIn("caf�", logs[0].read_text(encoding="utf-8"))

    def test_a_red_gate_printing_bad_bytes_stays_red(self):
        rc, rec, out, err = self.run_gate(
            r"sys.stdout.buffer.write(b'caf\xe9'); sys.exit(3)")
        self.assertNotIn("codec", out + err)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("printer", out)


class TestATornLedgerLine(Round):
    """D6. The class: one line of `ledger.jsonl` that is no event, by what
    is wrong with it, at every verb that reads the ledger."""

    LINES = {"unterminated": b'{"event": "request", "ro',
             "not json": b"not json\n",
             "a bare number": b"12\n",
             "an array": b"[1]\n",
             "not utf-8": b'{"event": "caf\xe9"}\n',
             "a torn multi-byte": b'{"event": "caf\xc3'}

    def setUp(self):
        super().setUp()
        self.rec = self.first_handoff()
        self.path = self.s.state / "ledger.jsonl"
        self.good = self.path.read_bytes()
        self.verdict = self.verdict_file(self.rec["sha"], "clean to advance")

    def verbs(self):
        return {"brief": ("brief",),
                "report": ("ledger", "report"),
                "handoff": ("handoff", "--claim-file", str(self.s.claim)),
                "take": ("take", self.rec["kept"], "--as", "codex"),
                "close": ("close", "--verdict", str(self.verdict))}

    def test_every_verb_refuses_every_torn_line_by_file_and_line(self):
        line_no = self.good.count(b"\n") + 1
        for name, bad in self.LINES.items():
            self.path.write_bytes(self.good + bad)
            for verb, argv in self.verbs().items():
                with self.subTest(line=name, verb=verb):
                    out = self.refused(*argv, says=f"line {line_no}")
                    self.assertIn(str(self.path), out["error"])
                    self.assertEqual(out["next_kind"], "blocked")
                    self.assertIn("repairs the file", out["remedy"])
                    self.assertEqual(self.path.read_bytes(), self.good + bad)

    def test_a_torn_line_in_the_middle_names_its_own_line(self):
        first, rest = self.good.split(b"\n", 1)
        self.path.write_bytes(first + b"\nnot json\n" + rest)
        out = self.refused("brief", says="line 2 ")
        self.assertNotIn("unterminated", out["error"])

    def test_control_blank_lines_and_a_raw_separator_read(self):
        event = {"event": "note", "text": "one\u2028two"}
        self.path.write_bytes(self.good + b"\n" + json.dumps(
            event, ensure_ascii=False).encode("utf-8") + b"\r\n\n")
        for verb, argv in self.verbs().items():
            if verb in ("brief", "report"):
                with self.subTest(verb=verb):
                    self.run_ok(*argv)
        self.assertEqual(self.run_ok("close", "--verdict",
                                     str(self.verdict))["verdict"],
                         "clean to advance")

    def test_a_refused_read_is_refused_again_and_writes_nothing(self):
        from review.ledger import Ledger, LedgerDefect
        self.path.write_bytes(self.good + b"not json\n")
        ledger = Ledger(self.s.state)
        for attempt in (1, 2):
            with self.assertRaises(LedgerDefect):
                ledger.events()
        with self.assertRaises(LedgerDefect):
            ledger.add({"event": "note"})
        self.assertEqual(self.path.read_bytes(), self.good + b"not json\n")

    def test_control_the_repaired_file_goes_on(self):
        self.path.write_bytes(self.good + b"not json\n")
        self.refused("brief", says="line")
        self.path.write_bytes(self.good)
        self.run_ok("brief")


class TestARespelledRemote(unittest.TestCase):
    """D7. The class: origin's spelling after a ledger was recorded, with no
    state directory named, so the id decides. `bin/loupe` with a scratch HOME.
    A hosted remote is compared; a local path remote is not, and a row says so.
    A real hand-off records the ledger against a local remote; `hosted()` then
    makes it the ledger of a hosted one, since no scratch can push to a host:
    the kept stamp and the directory's id are what the guard reads."""

    HOSTED = "ssh://git@example.invalid/o/remote.git"

    def setUp(self):
        self.s = Scratch(self, "respell-")
        self.home = self.s.root / "home"
        self.states = self.home / ".local" / "state" / "loupe"
        code, rec, out, err = self.cli("handoff", "--claim-file",
                                       str(self.s.claim), "--base",
                                       self.s.base)
        self.assertEqual(code, 0, (out, err))
        (self.ledger_dir,) = list(self.states.iterdir())

    def hosted(self):
        from review import config
        for kept in (self.ledger_dir / "exchange").rglob("*request*"):
            kept.write_text(kept.read_text(encoding="utf-8").replace(
                self.s.url, self.HOSTED), encoding="utf-8")
        self.spell(self.HOSTED)
        moved = self.states / config.repo_identity(self.s.repo)
        self.ledger_dir.rename(moved)
        self.ledger_dir = moved
        self.bytes = (moved / "ledger.jsonl").read_bytes()

    def cli(self, *argv, state_dir=None):
        import subprocess
        from review import env_var
        from review.tests._real_cli import LOUPE, loupe_env
        env = loupe_env(self.s, self.s.state)
        env.pop(env_var("STATE_DIR"))
        if state_dir:
            env[env_var("STATE_DIR")] = str(state_dir)
        proc = subprocess.run([str(LOUPE), *argv], cwd=self.s.repo, env=env,
                              capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload = None
        return proc.returncode, payload, proc.stdout, proc.stderr

    def spell(self, url):
        git(self.s.repo, "remote", "set-url", "origin", url)

    def assert_fresh(self, **kw):
        """Not refused for the spelling: an empty ledger's own answer."""
        code, rec, out, err = self.cli("brief", **kw)
        self.assertNotIn("orphan", out + err)
        self.assertIn("no open review request", rec["error"], (out, err))

    def test_every_hosted_spelling_of_this_repository_refuses(self):
        self.hosted()
        for name, spelling in (
                ("scp-like", "git@example.invalid:o/remote.git"),
                ("https", "https://example.invalid/o/remote.git"),
                ("no .git", "https://example.invalid/o/remote"),
                ("a trailing slash", self.HOSTED + "/"),
                ("another case", "ssh://git@EXAMPLE.invalid/O/remote.git")):
            with self.subTest(spelling=name):
                self.spell(spelling)
                code, rec, out, err = self.cli("brief")
                self.assertEqual(code, 1, (out, err))
                self.assertIn(str(self.ledger_dir), rec["error"])
                self.assertIn(self.HOSTED, rec["remedy"])
                self.assertEqual(rec["next_kind"], "blocked")
                self.assertEqual(len(list(self.states.iterdir())), 1)
                self.assertEqual((self.ledger_dir / "ledger.jsonl")
                                 .read_bytes(), self.bytes)

    def test_control_the_recorded_spelling_reads_its_ledger(self):
        self.hosted()
        _, _, out, err = self.cli("brief")
        self.assertNotIn("orphan", out + err)
        self.assertNotIn("no open review request", out, "read an empty ledger")

    def test_control_another_repository_of_that_name_starts_its_own(self):
        self.hosted()
        self.spell("git@example.invalid:other/remote.git")
        self.assert_fresh()

    def test_the_named_directory_is_the_way_to_a_separate_ledger(self):
        from review import config
        self.hosted()
        self.spell("https://example.invalid/o/remote")
        code, rec, _, _ = self.cli("brief")
        self.assertEqual(code, 1)
        fresh = self.states / config.repo_identity(self.s.repo)
        self.assertIn(str(fresh), rec["remedy"])
        fresh.mkdir()
        self.assert_fresh()

    def test_a_named_state_directory_decides_alone(self):
        self.hosted()
        self.spell("https://example.invalid/o/remote")
        self.assert_fresh(state_dir=self.s.root / "named")

    def test_a_sibling_that_kept_no_request_is_not_recognised(self):
        self.hosted()
        for path in (self.ledger_dir / "exchange").rglob("*request*"):
            path.unlink()
        self.spell("https://example.invalid/o/remote")
        self.assert_fresh()

    def test_a_local_path_remote_is_not_compared(self):
        """Stated limit, not a defect: each of these still starts its own
        ledger, and the first one's bytes stay as they were."""
        before = (self.ledger_dir / "ledger.jsonl").read_bytes()
        for name, spelling in (("relative", "../remote.git"),
                               ("a trailing slash", self.s.url + "/"),
                               ("file://", "file://" + self.s.url)):
            with self.subTest(spelling=name):
                self.spell(spelling)
                self.assertEqual(git(self.s.repo, "ls-remote", "origin",
                                     "refs/heads/main").split()[1],
                                 "refs/heads/main")
                self.assert_fresh()
                self.assertEqual((self.ledger_dir / "ledger.jsonl")
                                 .read_bytes(), before)

    def test_a_hosted_origin_is_not_matched_to_a_local_stamp(self):
        """An scp-like origin whose key equals the local stamp's key."""
        host, rest = self.s.url.lstrip("/").split("/", 1)
        self.spell(f"{host}:{rest}")
        self.assert_fresh()

    def test_a_local_origin_is_not_matched_to_a_hosted_stamp(self):
        """A local path whose key equals the hosted stamp's key."""
        self.hosted()
        self.spell("/example.invalid/o/remote.git")
        self.assert_fresh()


class TestTheUnmappedNotice(unittest.TestCase):
    """The reviewers' second-ranked complaint: a changed file an objective's
    tests or references column lists, called unmapped. The class: how an
    objective names a changed file."""

    NOTICE = "that no objective maps"

    def notice(self, objective):
        s = Scratch(self, "unmapped-")
        (s.repo / "t").mkdir()
        (s.repo / "t" / "test_x.py").write_text("x = 1\n", encoding="utf-8")
        git(s.repo, "add", "t/test_x.py")
        git(s.repo, "commit", "-qm", "a test")
        s.claim.write_text(json.dumps({
            "objective": "o", "objectives": [{"title": "fix", **objective}],
            "references": [{"path": "review.toml", "required": True}]}),
            encoding="utf-8")
        code, rec, out, err = loupe(s, "handoff", "--claim-file", str(s.claim),
                                    "--base", s.base)
        self.assertEqual(code, 0, (out, err))
        lines = Path(rec["kept"]).read_text(encoding="utf-8").splitlines()
        return [l for l in lines if self.NOTICE in l]

    def test_how_an_objective_names_the_file(self):
        for name, objective, mapped in (
                ("paths", {"paths": ["f.txt", "t/test_x.py"]}, True),
                ("tests, the path", {"paths": ["f.txt"],
                                     "tests": ["t/test_x.py"]}, True),
                ("tests, a test in it", {"paths": ["f.txt"],
                                         "tests": ["t/test_x.py::TestA"]}, True),
                ("tests, with a note", {"paths": ["f.txt"],
                                        "tests": ["t/test_x.py (why)"]}, True),
                ("tests, its directory", {"paths": ["f.txt"],
                                          "tests": ["t"]}, True),
                ("references", {"paths": ["f.txt"],
                                "references": ["t/test_x.py"]}, True),
                ("control: another test", {"paths": ["f.txt"],
                                           "tests": ["t/test_y.py"]}, False),
                ("control: nothing names it", {"paths": ["f.txt"]}, False)):
            with self.subTest(row=name):
                found = self.notice(objective)
                if mapped:
                    self.assertEqual(found, [])
                else:
                    self.assertTrue(found)
                    self.assertIn("t/test_x.py", found[0])


class TestATakeOnASharedLedgerKeepsTheCacheWarm(Round):
    """D11. A re-handoff beside the reviewer's `take`, on one ledger."""

    def test_the_rehandoff_after_a_take_is_warm(self):
        first = self.first_handoff()
        self.run_ok("take", first["kept"], "--as", "codex")
        again = self.handoff("--base", self.s.base)
        self.assertIs(again["cached"], True, "new bytes under the reviewer")
        self.assertEqual(again["digest"], first["digest"])

    def test_control_only_rows_with_no_claim_digest_are_cold(self):
        first = self.first_handoff()
        self.run_ok("take", first["kept"], "--as", "codex")
        path = self.s.state / "ledger.jsonl"
        rows = self.ledger()
        for row in rows:
            row.pop("claim_digest", None)
        path.write_text("".join(json.dumps(r, sort_keys=True) + "\n"
                                for r in rows), encoding="utf-8")
        self.assertIs(self.handoff("--base", self.s.base)["cached"], False)


class TestAWaiverOfAHandedOffCommit(Round):
    """D12. The refusal says what the record says, whether or not a verdict
    rules the commit; the rule itself is unchanged."""

    def test_handed_off_and_never_ruled_does_not_say_reviewed(self):
        rec = self.first_handoff()
        out = self.refused("waive", "--sha", rec["sha"], "--reason", "skip",
                           "--by", "osama", says="no verdict rules it")
        self.assertNotIn("reviewed rather than", out["error"])

    def test_ruled_says_reviewed(self):
        _, rec = self.first_round()
        self.refused("waive", "--sha", rec["sha"], "--reason", "skip", "--by",
                     "osama", says="reviewed rather than waived")

    def test_control_a_commit_never_handed_off_waives(self):
        rec = self.run_ok("waive", "--sha", self.s.head, "--reason", "skip",
                          "--by", "osama")
        self.assertIs(rec["recorded"], True)


class TestALedgerEndingWithNoNewline(Round):
    """F1 of an earlier round, at one boundary. The class: how the final
    complete record ends. Without its newline it is a stopped write, refused
    rather than appended after, or the next record would join the line."""

    def ends(self, ending):
        verdict, _ = self.first_round()
        path = self.s.state / "ledger.jsonl"
        path.write_bytes(path.read_bytes().rstrip(b"\n") + ending)
        return verdict, path

    def test_no_newline_is_refused_by_ending_with_bytes_unchanged(self):
        for name, ending in (("nothing", b""), ("spaces", b"  "),
                             ("a carriage return", b"\r"), ("a tab", b"\t")):
            with self.subTest(ending=name):
                self.s = Scratch(self, "lifecycle-")
                verdict, _ = self.ends(ending)
                self.refused("close", "--verdict", str(verdict),
                             says="no newline")
                self.refused("ledger", "report", says="no newline")

    def test_control_its_newline_reads(self):
        self.ends(b"\n")
        self.run_ok("ledger", "report")


class TestRepeatedReturnsToAnEmission(Round):
    """F2 of round 1. The class: which emission stands after A, B, A, B, A, by
    verdict term and with the reviewer's takes interposed. Each row is its own
    scratch run."""

    def visit(self, sha):
        git(self.s.repo, "reset", "-q", "--hard", sha)
        rec = self.handoff("--base", self.s.base)
        self.assertIs(rec["cached"], True)
        return rec

    def history(self, takes=False, visits=3):
        """A, B, then `visits` returns: 1 is A, B, A and 3 is A, B, A, B, A."""
        a, b = self.two_emissions()
        for rec in (a, b, a)[:visits]:
            if takes:
                self.run_ok("take", b["kept"], "--as", "codex")
            self.visit(rec["sha"])
        return a, b

    TERMS = (("clean to advance", "None\n", "closed"),
             ("changes requested", synth.finding(1, evidence="f.txt:1"), "open"))

    def test_the_latest_closes_by_term(self):
        for term, findings, lineage in self.TERMS:
            with self.subTest(term=term):
                self.s = Scratch(self, "lifecycle-")
                a, _ = self.history()
                path = self.verdict_file(a["sha"], term, findings)
                self.assertIn(lineage, self.run_ok("close", "--verdict",
                                                   str(path))["lineage"])

    def test_the_superseded_is_refused_by_term_with_bytes_unchanged(self):
        for term, findings, _ in self.TERMS:
            with self.subTest(term=term):
                self.s = Scratch(self, "lifecycle-")
                a, b = self.history()
                path = self.verdict_file(b["sha"], term, findings)
                self.refused("close", "--verdict", str(path),
                             says=a["sha"][:12])

    def test_takes_interposed_do_not_move_what_stands(self):
        a, b = self.history(takes=True)
        path = self.verdict_file(b["sha"], "clean to advance")
        self.refused("close", "--verdict", str(path), says=a["sha"][:12])
        path = self.verdict_file(a["sha"], "clean to advance")
        self.assertIn("closed", self.run_ok("close", "--verdict",
                                            str(path))["lineage"])

    def test_control_a_cold_emission_after_returns_stands(self):
        a, b = self.history()
        self.visit(b["sha"])
        c = self.commit("cold\n")
        rec = self.handoff("--base", self.s.base)
        self.assertIs(rec["cached"], False)
        self.assertEqual(rec["sha"], c)
        path = self.verdict_file(a["sha"], "clean to advance")
        self.refused("close", "--verdict", str(path), says=c[:12])

    def test_control_a_warm_serve_of_what_stands_adds_no_row(self):
        self.history()
        before = len(self.ledger())
        self.handoff("--base", self.s.base)
        self.assertEqual(len(self.ledger()), before)

    def no_discard_after_closing(self, rec):
        """Clean close of what `brief` named, and no emission reads as lost."""
        path = self.verdict_file(rec["sha"], "clean to advance")
        self.assertIn("closed", self.run_ok("close", "--verdict",
                                            str(path))["lineage"])
        out = self.refused("brief", says="already carries a verdict")
        self.assertNotIn("discarded", out["error"])

    def test_brief_names_what_close_accepts_and_what_it_refuses_says_so(self):
        """F2 of round 2: the bare brief named B, the commit whose verdict
        `close` refuses, and the refusal's remedy sent the reviewer back to
        it. Then a clean close of A reported B as discarded."""
        for visits in (1, 3):
            with self.subTest(visits=visits):
                self.s = Scratch(self, "lifecycle-")
                a, b = self.history(visits=visits)
                seen = self.briefs(a)
                self.assertNotIn(b["kept"], seen["relay"])
                self.refused("close", "--verdict", str(self.verdict_file(
                    b["sha"], "clean to advance")), says=seen["sha"][:12])
                self.no_discard_after_closing(a)

    def test_takes_of_either_request_do_not_move_what_brief_names(self):
        """After the last visit in every order, and interposed at each one."""
        a, b = self.history()
        reset = self.reset_to_here()
        for order in ("a", "b", "ab", "ba"):
            reset()
            with self.subTest(order=order):
                for name in order:
                    self.run_ok("take", {"a": a, "b": b}[name]["kept"], "--as",
                                "codex")
                    self.briefs(a)
                self.no_discard_after_closing(a)
        with self.subTest(order="at each visit"):
            self.s = Scratch(self, "lifecycle-")
            a, _ = self.history(takes=True)
            self.briefs(a)
            self.no_discard_after_closing(a)

    def test_control_a_cold_emission_after_returns_is_what_brief_names(self):
        a, b = self.history()
        self.visit(b["sha"])
        self.commit("cold\n")
        c = self.handoff("--base", self.s.base)
        self.assertIs(c["cached"], False)
        self.briefs(c, superseded=2)
        self.no_discard_after_closing(c)

    def test_a_decision_after_returns_discards_the_return_not_the_newest(self):
        a, b = self.history(visits=1)
        self.run_ok("close", "--lineage", "--reason", "abandon", "--by",
                    "osama")
        out = self.refused("brief", says="discarded")
        self.assertIn(a["sha"][:12], out["error"])
        self.assertNotIn(b["sha"][:12], out["error"])


class TestTheDoorHoldsItsReservation(Round):
    """F1 of round 3: `ledger add`'s admission and append are one operation
    against the writers that replace or end a review. A competitor run inside
    the interval is refused, as `close` refuses it; before and after the
    interval, `TestOneAdmissibilityAtBothDoors` holds."""

    def ingest_with(self, term, findings, compete):
        first = self.first_handoff()
        verdict = self.verdict_file(first["sha"], term, findings)
        original = transport.admit_verdict

        def paused(*args, **kw):
            admitted = original(*args, **kw)
            compete()
            return admitted

        out, cwd = io.StringIO(), os.getcwd()
        os.chdir(self.s.repo)
        try:
            with mock.patch.dict(os.environ, loupe_env(self.s, self.s.state),
                                 clear=True), \
                    mock.patch.object(transport, "admit_verdict", paused), \
                    contextlib.redirect_stdout(out):
                code = cli.main(["ledger", "add", str(verdict)])
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0, out.getvalue())
        events = Ledger(self.s.state).current(first["lineage"])
        self.assertEqual([e["sha"] for e in events if e["event"] == "verdict"],
                         [first["sha"]])
        self.assertEqual(len([e for e in events if e["event"] == "request"]), 1)
        self.assertFalse(any(e["event"] == Ledger.LINEAGE_CLOSED
                             and e.get("reason") == "abandon" for e in events))

    def reemit(self):
        self.commit("a new emission\n")
        self.refused("handoff", "--claim-file", str(self.s.claim), "--base",
                     self.s.base, says="lineage reservation")

    def end(self):
        self.refused("close", "--lineage", "--reason", "abandon", "--by",
                     "osama", says="lineage reservation")

    def test_a_replacement_inside_the_interval_is_refused_changes(self):
        self.ingest_with("changes requested",
                         synth.finding(1, evidence="f.txt:1"), self.reemit)

    def test_a_replacement_inside_the_interval_is_refused_clean(self):
        self.ingest_with("clean to advance", "None\n", self.reemit)

    def test_an_ending_inside_the_interval_is_refused_changes(self):
        self.ingest_with("changes requested",
                         synth.finding(1, evidence="f.txt:1"), self.end)

    def test_an_ending_inside_the_interval_is_refused_clean(self):
        self.ingest_with("clean to advance", "None\n", self.end)


class TestOneAdmissibilityAtBothDoors(Round):
    """F1 of round 2. The class: every state `close` refuses, at `ledger add`
    too, beside what `ledger add` alone may file: a historical verdict into a
    lineage holding no review, the ruling on record filed again, and
    `import-legacy`, which has a door and a proof of its own."""

    def test_every_door_refuses_a_commit_no_request_binds(self):
        self.first_handoff()
        self.each_refused(
            (row, argv, "no request")
            for row, argv in self.spellings(self.s.base, (), ("--round", "1"),
                                            ("--round", "2")))

    def test_every_door_refuses_two_open_rounds_on_one_commit(self):
        rec = self.first_handoff()
        self.run_ok("ledger", "add", rec["kept"], "--round", "2")
        self.each_refused(
            (row, argv, "more than one OPEN")
            for row, argv in self.spellings(rec["sha"], (), ("--round", "1"),
                                            ("--round", "2")))

    def test_every_door_refuses_an_open_round_it_does_not_rule(self):
        first = self.first_handoff()
        self.commit()
        later = self.s.root / "later.md"
        self.run_ok("emit-request", "--claim-file", str(self.s.claim), "--base",
                    self.s.base, "--out", str(later))
        self.run_ok("ledger", "add", str(later), "--round", "2")
        self.every_door_refuses(first["sha"], 1, "does not rule")

    def test_control_the_ruling_on_record_filed_again_is_usage_metadata(self):
        path, rec = self.first_round()
        same = self.run_ok("ledger", "add", str(path), "--round", "1")
        self.assertEqual(same["events_added"], 0)
        counted = self.run_ok("ledger", "add", str(path), "--round", "1",
                              "--tokens", "5")
        self.assertGreater(counted["events_added"], 0)
        self.assertEqual(counted["lineage"], rec["lineage"])
        self.assertEqual({e["round"] for e in self.ledger()
                          if e["event"] == "verdict"}, {1})

    def test_only_the_bytes_at_their_round_are_a_filing_again(self):
        path, rec = self.first_round()
        self.refused("ledger", "add", str(path), says="no request")
        self.refused("ledger", "add", str(path), "--round", "2",
                     says="no request")
        other = self.verdict_file(rec["sha"], "clean to advance")
        self.refused("ledger", "add", str(other), "--round", "1",
                     says="different verdict")

    def test_control_a_lineage_holding_no_review_files_a_historical_verdict(
            self):
        """Its round is a person's to state, and the refusal says so."""
        for term, findings in VERDICTS:
            with self.subTest(term=term):
                self.s = Scratch(self, "lifecycle-")
                path = self.verdict_file(self.s.base, term, findings)
                out = self.refused("ledger", "add", str(path),
                                   says="cannot be derived")
                self.assertIn("--round <that number>", out["remedy"])
                self.refused("close", "--verdict", str(path),
                             says="no review to record into")
                rec = self.run_ok("ledger", "add", str(path), "--round", "3")
                self.assertGreater(rec["events_added"], 0)
                rows = [e for e in self.ledger() if e["event"] == "verdict"]
                self.assertEqual([(e["round"], e["sha"]) for e in rows],
                                 [(3, self.s.base)])

    def test_a_round_is_ruled_once_where_no_review_holds_it(self):
        first, second = VERDICTS
        self.run_ok("ledger", "add", str(self.verdict_file(
            self.s.base, *first)), "--round", "3")
        self.refused("ledger", "add", str(self.verdict_file(
            self.s.base, *second)), "--round", "3", says="different verdict")

    def test_a_marker_alone_is_a_review(self):
        """A closed lineage holding no request, where a read's positional key
        lands on it: nothing is filed behind the marker."""
        ledger = Ledger(self.s.state)
        ledger.add({"event": "verdict", "round": 1, "sha": self.s.base,
                    "source_digest": "1"}, lineage="2")
        ledger.add({"event": Ledger.LINEAGE_CLOSED, "at_round": 1,
                    "reason": "x", "authorized_by": "y"}, lineage="2")
        path = self.verdict_file(self.s.head, "clean to advance")
        self.refused("ledger", "add", str(path), "--round", "2",
                     says="no request")

    def test_control_a_closed_branch_files_it_in_a_lineage_of_its_own(self):
        rec = self.first_handoff()
        self.run_ok("close", "--lineage", "--reason", "abandon", "--by",
                    "osama")
        before = self.ledger()
        term, findings = VERDICTS[1]
        path = self.verdict_file(self.s.base, term, findings)
        out = self.run_ok("ledger", "add", str(path), "--round", "1")
        self.assertNotEqual(out["lineage"], rec["lineage"])
        self.assertEqual(self.ledger()[:len(before)], before)
        self.assertEqual({e["lineage"] for e in self.ledger()[len(before):]},
                         {out["lineage"]})

    def legacy_batch(self):
        """`import-legacy`'s own proof: a verdict row citing bytes it reads at
        a commit the remote holds. Returns (events file, the commit)."""
        s, text = self.s, "VERDICT: changes requested\n"
        (s.repo / "legacy.md").write_text(text, encoding="utf-8")
        git(s.repo, "add", "legacy.md")
        git(s.repo, "commit", "-qm", "legacy source")
        git(s.repo, "push", "-q", "origin", "HEAD:refs/heads/main")
        anchor = git(s.repo, "rev-parse", "HEAD")
        cited = {"sha": anchor, "bytes": len(text), "source_path": "legacy.md",
                 "source_digest": hashlib.sha256(text.encode()).hexdigest()}
        return self.write("events.jsonl", "".join(json.dumps(row) + "\n" for row in (
            {"event": "request", "round": 1, **cited},
            {"event": "verdict", "round": 1, "verdict": "changes requested",
             "finding_ids": 0, **cited}))), anchor

    def test_control_import_legacy_keeps_its_own_door(self):
        """Into no review, an open one and behind a closure: `admit_verdict`
        is the ordinary door's, and this one's check is the anchored source."""
        for state in ("no review", "open", "closed"):
            with self.subTest(state=state):
                self.s = Scratch(self, "lifecycle-")
                if state != "no review":
                    self.first_handoff()
                if state == "closed":
                    self.run_ok("close", "--lineage", "--reason", "abandon",
                                "--by", "osama")
                events, anchor = self.legacy_batch()
                rec = self.run_ok("import-legacy", str(events),
                                  "--source-commit", anchor)
                self.assertEqual(rec["events_added"], 2)
                self.assertEqual(
                    [e["sha"] for e in self.ledger()
                     if e["event"] == "verdict"], [anchor])


class TestTheVerdictSectionMap(Round):
    """D9. The class: a heading of a verdict, by whether the section map can
    keep what is under it. Through `validate` and `close`."""

    def setUp(self):
        super().setUp()
        verdict, _ = self.first_round()
        head = self.commit()
        code, _, out, err = self.respond(verdict, head, ACCEPTED)
        self.assertEqual(code, 0, (out, err))
        self.rec = self.handoff()
        self.fp = next(e["fp"] for e in self.ledger()
                       if e["event"] == "finding")

    def body(self, *blocks):
        return self.verdict_file(self.rec["sha"], "clean to advance",
                                 tail="".join(blocks))

    def check(self, path):
        code, rec, out, _ = loupe(self.s, "validate", str(path),
                                  "--from-target")
        return code, [i.get("code") for i in (rec or {}).get("items", [])], out

    def closure(self, heading="closures"):
        return f"\n## {heading}\n\n- {self.fp} withdrawn: fine\n"

    def test_a_repeated_closures_section_is_refused(self):
        path = self.body(self.closure(), self.closure())
        code, items, out = self.check(path)
        self.assertEqual(code, 1, out)
        self.assertIn("V-SECTION-DUPLICATE", items)
        self.refused("close", "--verdict", str(path), says="V-SECTION-DUPLICATE")

    def test_a_repeated_optional_section_is_refused_each(self):
        for name in ("unavailable references", "tool feedback"):
            with self.subTest(section=name):
                path = self.body(self.closure(), f"\n## {name}\n\nx\n",
                                 f"\n## {name}\n\ny\n")
                self.assertIn("V-SECTION-DUPLICATE", self.check(path)[1])

    def test_a_near_miss_of_a_section_is_refused(self):
        for heading in ("closure", "closures:", "Closure"):
            with self.subTest(heading=heading):
                path = self.body(self.closure(heading))
                code, items, out = self.check(path)
                self.assertEqual(code, 1, out)
                self.assertIn("V-SECTION-SPELLING", items)

    def test_an_unknown_heading_is_a_notice_and_exits_clean(self):
        path = self.body(self.closure(), "\n## residual evidence gaps\n\nx\n")
        code, items, out = self.check(path)
        self.assertEqual(code, 0, out)
        self.assertIn("V-SECTION-UNKNOWN", items)

    def test_control_the_declared_sections_say_nothing(self):
        path = self.body(self.closure(), "\n## tool feedback\n\nfine\n",
                         "\n## leads\n\n- Low high f.txt:1: a thought\n")
        code, items, out = self.check(path)
        self.assertEqual(code, 0, out)
        self.assertFalse([i for i in items if i.startswith("V-SECTION")], items)
        self.assertIn("closed", self.run_ok("close", "--verdict",
                                            str(path))["lineage"])


class TestTakeSaysWhetherTheRefHoldsTheTarget(Round):
    """D10. The class: what became of the pushed ref between hand-off and
    `take`, in a clone sharing the author's objects."""

    def fetch_word(self, rec, *flags):
        return self.run_ok("take", rec["kept"], "--as", "codex",
                           *flags)["target"]["fetch"]

    def handed_off(self):
        return self.first_handoff()

    def test_control_the_ref_as_pushed(self):
        word = self.fetch_word(self.handed_off())
        self.assertIn("fetched", word)
        self.assertNotIn("NOTICE", word)

    def test_control_a_later_commit_on_the_ref(self):
        rec = self.handed_off()
        self.commit()
        git(self.s.repo, "push", "-q", "origin", "main")
        self.assertNotIn("NOTICE", self.fetch_word(rec))

    def test_an_amend_and_force_push_is_reported(self):
        rec = self.handed_off()
        git(self.s.repo, "commit", "-q", "--amend", "-m", "amended")
        git(self.s.repo, "push", "-qf", "origin", "main")
        word = self.fetch_word(rec)
        self.assertIn(f"no longer holds {rec['sha'][:12]}", word)

    def test_no_fetch_says_it_did_not_look(self):
        rec = self.handed_off()
        word = self.fetch_word(rec, "--no-fetch")
        self.assertIn("skipped", word)
        self.assertNotIn("NOTICE", word)


class TestAClosureOfAnUnknownFingerprint(Round):
    """D13. A notice, where a ledger is known; the exit is unchanged."""

    def validated(self, closure):
        verdict, _ = self.first_round()
        head = self.commit()
        code, _, out, err = self.respond(verdict, head, ACCEPTED)
        self.assertEqual(code, 0, (out, err))
        rec = self.handoff()
        known = next(e["fp"] for e in self.ledger() if e["event"] == "finding")
        path = self.verdict_file(rec["sha"], "clean to advance", tail=(
            f"\n## closures\n\n- {closure.format(known=known)}\n"))
        code, out, text, _ = loupe(self.s, "validate", str(path),
                                   "--from-target")
        return code, [i.get("code") for i in (out or {}).get("items", [])], text

    def test_a_typo_is_named(self):
        code, items, text = self.validated(
            "fp2:00000000deadbeef withdrawn: fine")
        self.assertEqual(code, 0, text)
        self.assertIn("C-UNKNOWN-FINGERPRINT", items, text)

    def test_a_bare_reclassification_keeps_its_own_notice_alone(self):
        code, items, text = self.validated(
            "fp2:00000000deadbeef reclassified: High to Low")
        self.assertIn("C-RESIDUE-UNCHECKED", items, text)
        self.assertNotIn("C-UNKNOWN-FINGERPRINT", items)

    def test_control_a_ruled_fingerprint_says_nothing(self):
        code, items, text = self.validated("{known} withdrawn: fine")
        self.assertEqual(code, 0, text)
        self.assertNotIn("C-UNKNOWN-FINGERPRINT", items)


class TestTwoFindingsWithOneFingerprint(Round):
    """D14. The class: the second finding's identity against the first's."""

    def verdict_of(self, *findings):
        rec = self.first_handoff()
        return self.verdict_file(rec["sha"], findings="".join(findings))

    def test_one_fingerprint_twice_is_refused(self):
        path = self.verdict_of(
            synth.finding(1, title="same", evidence="f.txt:1"),
            synth.finding(2, title="same", evidence="f.txt:1"))
        self.refused("close", "--verdict", str(path), says="V-FP-DUPLICATE")

    def test_control_two_titles_record(self):
        path = self.verdict_of(
            synth.finding(1, title="one", evidence="f.txt:1"),
            synth.finding(2, title="two", evidence="f.txt:1"))
        self.assertEqual(self.run_ok("close", "--verdict",
                                     str(path))["findings"], 2)


class TestChangesRequestedThatNamesNothing(Round):
    """D16. The class: what `changes requested` carries."""

    def setUp(self):
        super().setUp()
        self.rec = self.first_handoff()

    def test_none_and_no_closure_is_refused(self):
        path = self.verdict_file(self.rec["sha"])
        self.refused("close", "--verdict", str(path), says="V-CHANGES-NOTHING")

    def test_an_unavailable_reference_alone_is_refused(self):
        path = self.verdict_file(self.rec["sha"], tail=(
            "\n## unavailable references\n\n- review.toml\n"))
        self.refused("close", "--verdict", str(path), says="V-CHANGES-NOTHING")

    def test_control_a_finding_records(self):
        path = self.verdict_file(self.rec["sha"], findings=synth.finding(
            1, evidence="f.txt:1"))
        self.assertEqual(self.run_ok("close", "--verdict",
                                     str(path))["findings"], 1)

    def test_control_a_closure_alone_passes_this_rule(self):
        path = self.verdict_file(self.rec["sha"], tail=(
            "\n## closures\n\n- fp2:00000000deadbeef sustained: stands\n"))
        code, rec, out, _ = loupe(self.s, "validate", str(path))
        self.assertNotIn("V-CHANGES-NOTHING",
                         [i.get("code") for i in rec.get("items", [])], out)


if __name__ == "__main__":
    unittest.main()

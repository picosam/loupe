"""`take`'s compact default, the reviewer's HEAD, and the split gate banner.

Brief `loupe-tool-feedback-pilot-2026-09` (three debug rounds, 2026-09-17/18):

1. `take` printed the whole request every round, attestation objects
   included — 64% of a measured 29,589-byte request;
2. the useful facts of an attestation are few, and a table carries them;
3. `take` reported the target present and never said what the reviewer's
   working tree WAS;
5. one banner line conflated a non-blocking gate's red with a failure of
   the target.

What these tests hold, and what they do not. They hold the RENDERING to the
record: every deviation an attestation can carry is printed, nothing outside
the attestation block changes by a byte, and what cannot be parsed is left
verbatim. They do not re-prove validation: `compact_request` is a view for a
reader, and every check still runs over the original bytes.
"""

import json
import subprocess
import unittest
from pathlib import Path

from review import TOOL_NAME, brief, cli, transport, wire
from review.emit import _git
from review.tests._transport_fixtures import run_cli, scratch_loop_repo, sh

SHA = "a" * 40
OTHER = "b" * 40


def record(gate="tests", **over):
    rec = {
        "id": gate, "command": f"bin/run {gate}", "exit_code": 0,
        "tool_version": "x", "target_sha": SHA, "executed_sha": SHA,
        "tree": "clean", "binding": "bound", "duration_s": 1.0,
        "blocking": True,
        "output": {"sha256": "c" * 64, "bytes": 10, "pointer": "/p/out.log"},
    }
    rec.update(over)
    return rec


def envelope(records, body=None):
    fence = wire.attestation_fence(TOOL_NAME)
    block = body if body is not None else json.dumps(records, indent=2)
    return (f'<{TOOL_NAME}-review-request sha="{SHA}" round="1">\n'
            f"## Claim\n\nclaim prose | with a pipe\n\n## Evidence\n\n"
            f"before the block\n\n```{fence}\n{block}\n```\n\n"
            f"NOT captured — nothing\n\n## Reference\n\nrefs\n"
            f"</{TOOL_NAME}-review-request>\n")


def rows(text):
    return [l for l in text.splitlines()
            if l.startswith("| ") and not l.startswith("| gate ")]


class TestTheCompactRequest(unittest.TestCase):

    def test_only_the_block_changes_and_every_gate_has_one_row(self):
        """FALSIFICATION. Mutation: return `envelope[:block.start()] + table`
        (dropping the suffix) and the suffix assertion fails; drop a record
        from the row generator and the count fails."""
        recs = [record("tests"), record("lint"), record("stage")]
        env = envelope(recs)
        out = brief.compact_request(env, kept="/kept/request.md")
        fence = "```" + wire.attestation_fence(TOOL_NAME)
        start = env.index(fence)
        end = env.index("\n```\n", start) + len("\n```")
        self.assertTrue(out.startswith(env[:start]),
                        "bytes before the block changed")
        self.assertTrue(out.endswith(env[end:]),
                        "bytes after the block changed")
        self.assertNotIn(fence, out)
        self.assertEqual([r.split(" | ")[0][2:] for r in rows(out)],
                         ["tests", "lint", "stage"])
        self.assertIn("/kept/request.md", out,
                      "the full mode is not named beside the table")
        self.assertLess(len(out), len(env))

    def test_the_clean_row_is_the_control(self):
        """The paired control for the deviation test below: a bound, clean,
        passing row at the request's own sha says so and nothing louder."""
        (row,) = rows(brief.compact_request(envelope([record()])))
        self.assertIn("| exit 0 | bound | clean | = target | yes | "
                      "this tool |", row)
        self.assertIn("sha256:" + "c" * 12, row)
        self.assertIn("| /p/out.log |", row, "the log pointer, whole")

    def test_no_deviation_is_ever_summarised_away(self):
        """FALSIFICATION. Each case is one way an attestation differs from
        the clean control, and each must be legible in its row. Mutation:
        hardcode `ran_at = "= target"` and the three sha cases fail; print
        `rec.get("exit_code", 0)` and the missing-exit case fails."""
        cases = {
            "a failing exit": (record(exit_code=3), "exit 3"),
            "an unbound row": (record(binding="unbound: dirty tree"),
                               "unbound: dirty tree"),
            "a dirty tree": (record(tree="dirty"), "| dirty |"),
            "executed elsewhere": (record(executed_sha=OTHER),
                                   f"executed {OTHER[:12]}"),
            "claims another target": (
                record(target_sha=OTHER, executed_sha=OTHER),
                f"claims {OTHER[:12]}"),
            "non-blocking": (record(blocking=False), "| no |"),
            "attested by ci": (
                record(attested_by="ci",
                       ci_run={"id": 77, "conclusion": "failure"}),
                "ci run 77 (failure)"),
            "not run": ({"id": "tests", "error": "timed out at 600s",
                         "blocking": True, "target_sha": SHA},
                        "NOT RUN: timed out at 600s"),
            "no exit code at all": (
                {k: v for k, v in record().items() if k != "exit_code"},
                "| - | bound"),
        }
        for name, (rec, expected) in cases.items():
            with self.subTest(case=name):
                (row,) = rows(brief.compact_request(envelope([rec])))
                self.assertIn(expected, row)
                self.assertNotIn("= target", row) if name in (
                    "executed elsewhere", "claims another target",
                    "not run") else None

    def test_a_target_that_is_not_the_requests_sha_is_not_equal(self):
        """executed = target is not enough: both must be the REQUEST's sha,
        or a block copied whole from another commit would read `= target`
        in every row."""
        env = envelope([record(target_sha=OTHER, executed_sha=OTHER)])
        (row,) = rows(brief.compact_request(env))
        self.assertNotIn("= target", row)

    def test_what_cannot_be_read_is_left_verbatim(self):
        """FALSIFICATION. Mutation: drop the `isinstance(r, dict)` guard and
        the non-object case raises instead of returning the envelope."""
        unreadable = {
            "not json": envelope(None, body="[ {not json"),
            "not an array": envelope(None, body='{"id": "tests"}'),
            "a record that is not an object": envelope([record(), "tests"]),
            "an empty array": envelope([]),
            "no block at all": "## Evidence\n\nnothing here\n",
        }
        for name, env in unreadable.items():
            with self.subTest(case=name):
                self.assertEqual(brief.compact_request(env, kept="/k"), env)

    def test_a_pipe_in_a_command_cannot_forge_a_column(self):
        rec = record(command="run | tee log")
        (row,) = rows(brief.compact_request(envelope([rec])))
        self.assertIn("run \\| tee log", row)
        self.assertEqual(row.replace("\\|", "").count("|"), 11)


class TestTheGateBanner(unittest.TestCase):

    def banners(self, *recs):
        return "\n".join(brief._gate_banners(list(recs)))

    def test_all_green_prints_nothing(self):
        self.assertEqual(self.banners(record(), record("lint")), "")

    def test_a_nonblocking_red_is_advisory_and_says_the_target_passed(self):
        """FALSIFICATION — the round-3 case itself. Mutation: restore the
        single `failed` list and this reads as a target failure again."""
        text = self.banners(record(), record("ci-evidence", exit_code=1,
                                             blocking=False))
        self.assertIn("Advisory gates", text)
        self.assertIn("ci-evidence", text)
        self.assertIn("every blocking gate passed at the target", text)
        self.assertNotIn("AT THE TARGET", text)

    def test_a_blocking_red_is_the_targets_and_is_never_advisory(self):
        text = self.banners(record(exit_code=1), record("lint"))
        self.assertIn("1 of 2 BLOCKING gate(s) did not pass AT THE TARGET: "
                      "tests", text)
        self.assertNotIn("Advisory", text)

    def test_both_at_once_are_two_lines_and_the_advisory_defers(self):
        text = self.banners(record(exit_code=1),
                            record("ci-evidence", exit_code=1,
                                   blocking=False))
        self.assertIn("AT THE TARGET: tests", text)
        self.assertIn("see the blocking failures above", text)
        self.assertNotIn("every blocking gate passed", text)

    def test_a_row_without_a_blocking_flag_is_not_advisory(self):
        """Absent is unknown, and unknown is never the quieter reading."""
        rec = {k: v for k, v in record(exit_code=1).items()
               if k != "blocking"}
        self.assertIn("AT THE TARGET", self.banners(rec))
        self.assertIn("AT THE TARGET", self.banners("not-an-object"))

    def test_the_precis_uses_it(self):
        env = envelope([record(), record("ci-evidence", exit_code=1,
                                         blocking=False)])
        text = brief.request_precis(wire.parse_request(env))
        self.assertIn("Advisory gates", text)
        self.assertNotIn("AT THE TARGET", text)


class TestTheCheckoutLine(unittest.TestCase):

    def test_all_three_states_print_and_name_their_shas(self):
        at = cli.render_checkout({"state": transport.CHECKOUT_AT_TARGET,
                                  "sha": SHA, "tree": "dirty"}, SHA)
        self.assertIn("AT the target", at)
        self.assertIn("dirty", at)
        away = cli.render_checkout({"state": transport.CHECKOUT_ELSEWHERE,
                                    "sha": OTHER, "tree": "clean"}, SHA)
        self.assertIn("NOT the target", away)
        self.assertIn(OTHER[:12], away)
        self.assertIn(SHA[:12], away)
        unknown = cli.render_checkout({"state": transport.CHECKOUT_UNKNOWN,
                                       "sha": None, "tree": None}, SHA)
        self.assertIn("UNKNOWN", unknown)

    def test_the_interactive_take_rendering_carries_it(self):
        """The wiring, not just the renderer (the shape
        `test_tool_identity` uses for the agreement line): a renderer
        nothing calls is the defect this item reported."""
        source = Path(cli.__file__).read_text(encoding="utf-8")
        take = source[source.index("def cmd_take"):]
        take = take[:take.index("\ndef ")]
        self.assertIn("render_checkout(rec['head'], rec['sha'])", take)
        self.assertIn("brief.compact_request(", take)

    def test_the_probe_reads_head_and_never_refuses(self):
        def git(answers):
            def run(*args):
                value = answers[args[0]]
                if isinstance(value, Exception):
                    raise value
                return value
            return run
        cfg = type("Cfg", (), {"repo_root": Path(".")})()
        self.assertEqual(
            transport.reviewer_checkout(
                cfg, SHA, git=git({"rev-parse": SHA, "status": ""})),
            {"state": "at-target", "sha": SHA, "tree": "clean"})
        self.assertEqual(
            transport.reviewer_checkout(
                cfg, SHA, git=git({"rev-parse": OTHER, "status": " M f"})),
            {"state": "elsewhere", "sha": OTHER, "tree": "dirty"})
        self.assertEqual(
            transport.reviewer_checkout(
                cfg, SHA, git=git({"rev-parse": RuntimeError("unborn")})),
            {"state": "unknown", "sha": None, "tree": None})


class TestTakeEndToEnd(unittest.TestCase):
    """Two clones and a bare remote, as the loop integration test builds
    them. The scratch manifest declares no gates, so the table itself is
    held by the unit tests above; this holds the WIRING — which key carries
    what, and that the reviewer's HEAD is read from the reviewer's clone."""

    def setUp(self):
        scratch = scratch_loop_repo(self, "takec-", name="author",
                                    objective="compact take")
        self.tmp, self.author = scratch.tmp, scratch.repo
        self.base, self.claim, self.cwd = (scratch.base, scratch.claim,
                                           scratch.cwd)
        remote = self.tmp / "remote.git"
        self.reviewer = self.tmp / "reviewer"
        sh("git", "init", "-q", "--bare", "-b", "main", str(remote))
        sh("git", "-C", str(self.author), "remote", "add", "origin",
           str(remote))
        sh("git", "-C", str(self.author), "push", "-q", "-u", "origin",
           "main")
        sh("git", "clone", "-q", str(remote), str(self.reviewer))
        code, self.rec = run_cli(self.author, self.tmp / "state-author",
                                 "handoff", "--claim-file", str(self.claim),
                                 "--base", self.base, cwd=self.cwd)
        self.assertEqual(code, 0, self.rec)
        self.head = _git(self.author, "rev-parse", "HEAD")

    def take(self, where, state, *extra):
        return run_cli(where, self.tmp / state, "take", self.rec["kept"],
                       "--as", "codex", *extra, cwd=self.cwd)

    def test_the_default_carries_the_view_and_full_carries_the_bytes(self):
        """FALSIFICATION. Mutation: leave `envelope` in the default payload
        and the first assertion fails; assign the view to `envelope` under
        `--full` and the byte-equality fails wherever a block exists."""
        code, compact = self.take(self.reviewer, "state-a")
        self.assertEqual(code, 0, compact)
        self.assertNotIn("envelope", compact)
        self.assertIn("request_view", compact)
        self.assertTrue(Path(compact["kept"]).is_file())
        code, full = self.take(self.reviewer, "state-b", "--full")
        self.assertEqual(code, 0, full)
        self.assertNotIn("request_view", full)
        self.assertEqual(
            full["envelope"],
            Path(self.rec["kept"]).read_text(encoding="utf-8"))

    def test_head_is_the_reviewers_own_in_all_three_states(self):
        code, at = self.take(self.reviewer, "state-at")
        self.assertEqual(code, 0, at)
        self.assertEqual(at["head"], {"state": "at-target",
                                      "sha": self.head, "tree": "clean"})
        older = _git(self.reviewer, "rev-parse", "HEAD~1")
        sh("git", "-C", str(self.reviewer), "checkout", "-q", older)
        code, away = self.take(self.reviewer, "state-away")
        self.assertEqual(code, 0, away)
        self.assertEqual(away["head"]["state"], "elsewhere")
        self.assertEqual(away["head"]["sha"], older)
        empty = self.tmp / "reviewer-empty"
        sh("git", "init", "-q", "-b", "main", str(empty))
        code, unborn = self.take(empty, "state-unborn")
        self.assertEqual(code, 0, unborn)
        self.assertEqual(unborn["head"]["state"], "unknown")


if __name__ == "__main__":
    unittest.main()

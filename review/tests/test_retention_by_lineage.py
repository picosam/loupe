"""Retained envelopes are immutable and per lineage. Finding 1 (High) of
the 2026-09-05 audit: `exchange/round-<n>-<kind>.md` named the kept copy by
round and kind alone, and every lineage starts at round 1, so opening the
next lineage overwrote the request that opened the last one — the ledger
digest survived and could not recover the document. The retained name now
carries the lineage and the digest, so two documents cannot share a path.
"""

import dataclasses
import unittest
from pathlib import Path

from review import transport
from review.tests._transport_fixtures import (
    CFG, SHA_A, SHA_B, request_text, run_cli, scratch_loop_repo,
    scratch_tmp, sh, verdict_text)


class TestRetentionByLineage(unittest.TestCase):

    def setUp(self):
        self.tmp = scratch_tmp(self, "retention-")
        self.cfg = dataclasses.replace(CFG, ledger_dir=self.tmp)

    def test_round_one_of_a_later_lineage_keeps_the_earlier_round_one(self):
        first, second = request_text(sha=SHA_A), request_text(sha=SHA_B)
        kept1 = Path(transport.keep_bytes(self.cfg, 1, "request", first,
                                          lineage=1))
        kept2 = Path(transport.keep_bytes(self.cfg, 1, "request", second,
                                          lineage=2))
        self.assertNotEqual(kept1, kept2)
        self.assertEqual(kept1.parent.name, "lineage-1")
        self.assertEqual(kept2.parent.name, "lineage-2")
        self.assertEqual(kept1.read_text(encoding="utf-8"), first,
                         "the earlier lineage's request must survive intact")
        self.assertEqual(kept2.read_text(encoding="utf-8"), second)

    def test_two_emissions_of_one_round_and_kind_both_survive(self):
        # A disposition re-emitted with a corrected payload is a second
        # document of the same round and kind; both are of record.
        one = request_text(sha=SHA_A)
        two = request_text(sha=SHA_A, reviewer="gemini")
        k1 = Path(transport.keep_bytes(self.cfg, 2, "disposition", one,
                                       lineage=1))
        k2 = Path(transport.keep_bytes(self.cfg, 2, "disposition", two,
                                       lineage=1))
        self.assertNotEqual(k1, k2)
        self.assertTrue(k1.is_file() and k2.is_file())
        self.assertEqual(k1.read_text(encoding="utf-8"), one)

    def test_a_kept_copy_is_named_by_its_own_digest(self):
        text = request_text()
        kept = Path(transport.keep_bytes(self.cfg, 1, "request", text,
                                         lineage=3))
        digest = transport._digest_text(text)
        self.assertEqual(kept.name, f"round-1-request-{digest[:12]}.md")
        self.assertEqual(
            kept, transport.exchange_path(self.cfg, 1, "request", lineage=3,
                                          digest=digest))

    def test_kept_path_prefers_the_lineage_copy_over_the_flat_legacy_name(self):
        text = request_text()
        digest = transport._digest_text(text)
        find = lambda: transport.kept_path(self.cfg, 1, "request",
                                           lineage=1, digest=digest)
        self.assertIsNone(find(), "nothing kept yet")
        legacy = transport.legacy_exchange_path(self.cfg, 1, "request")
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(text, encoding="utf-8")
        self.assertEqual(find(), legacy,
                         "a copy retained under the flat pre-0.18 name is "
                         "still reachable")
        current = Path(transport.keep_bytes(self.cfg, 1, "request", text,
                                            lineage=1))
        self.assertEqual(find(), current,
                         "the lineage-scoped copy wins once it exists")

    def test_keep_bytes_requires_the_lineage(self):
        with self.assertRaises(TypeError):
            transport.keep_bytes(self.cfg, 1, "request", request_text())


class _ScratchLoop(unittest.TestCase):

    PREFIX = "scratch-"

    def setUp(self):
        scratch = scratch_loop_repo(self, self.PREFIX)
        self.tmp, self.repo = scratch.tmp, scratch.repo
        self.base, self.claim, self.cwd = scratch.base, scratch.claim, scratch.cwd
        self.state = self.tmp / "state"

    def _run(self, *argv):
        return run_cli(self.repo, self.state, *argv, cwd=self.cwd)

    def _handoff(self, base):
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              "--base", base, "--local-only")
        self.assertEqual(code, 0, rec)
        return rec

    def _verdict(self, text, name="v.md"):
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return str(path)


class TestTheReviewersReproducer(_ScratchLoop):
    """Emit round one, close it cleanly, commit another change, open the
    next lineage: both requests used to land on `round-1-request.md`."""

    PREFIX = "two-lineages-"

    def test_the_first_lineages_envelopes_survive_the_second_lineage(self):
        first = self._handoff(self.base)
        kept_request = Path(first["kept"])
        request_bytes = kept_request.read_bytes()
        verdict = self._verdict(verdict_text(sha=first["sha"],
                                             verdict="clean to advance"))
        code, closed = self._run("close", "--verdict", verdict)
        self.assertEqual(code, 0, closed)
        self.assertIsNone(closed["next"], "a clean verdict closes the lineage")
        kept_verdict = Path(closed["kept"])
        verdict_bytes = kept_verdict.read_bytes()

        (self.repo / "f.txt").write_text("three\n", encoding="utf-8")
        sh("git", "-C", str(self.repo), "commit", "-qam", "more")
        second = self._handoff(first["sha"])
        self.assertEqual(second["round"], 1, "a new lineage restarts at 1")

        self.assertNotEqual(Path(second["kept"]), kept_request)
        # By ID since 0.20.0 (brief `keyed-lineage`): the directory is named
        # for the lineage the round belongs to, and a minted id is what that
        # is. The property under test is unchanged — two lineages, two
        # directories, neither writing over the other.
        self.assertNotEqual(first["lineage"], second["lineage"])
        self.assertEqual(kept_request.parent.name,
                         f"lineage-{first['lineage']}")
        self.assertEqual(Path(second["kept"]).parent.name,
                         f"lineage-{second['lineage']}")
        self.assertEqual(kept_request.read_bytes(), request_bytes,
                         "lineage 1's request is retrievable after lineage "
                         "2's round 1 was emitted")
        self.assertEqual(kept_verdict.read_bytes(), verdict_bytes)
        self.assertEqual(kept_verdict.parent.name,
                         f"lineage-{first['lineage']}")


class TestTheOpenRequestBriefReadsChecked(_ScratchLoop):
    """Lineage 25 round 1 F4: `brief` with no argument resolves the open
    request through the kept copy and used to read whatever was there. With
    the current copy gone and a pre-upgrade flat copy of an EARLIER
    lineage's round 1 still present, it served that older request as the
    live one — exit 0, ok, relay and all — while the ledger named another
    SHA. The four states: matching current, matching legacy, missing
    current with a stale legacy, missing both."""

    PREFIX = "brief-checked-"

    def _lineage_two(self):
        """Lineage 1 opened and closed clean, lineage 2's round 1 open;
        returns (first request text, second handoff record)."""
        first = self._handoff(self.base)
        first_text = Path(first["kept"]).read_text(encoding="utf-8")
        verdict = self._verdict(verdict_text(sha=first["sha"],
                                             verdict="clean to advance"))
        code, closed = self._run("close", "--verdict", verdict)
        self.assertEqual(code, 0, closed)
        (self.repo / "f.txt").write_text("three\n", encoding="utf-8")
        sh("git", "-C", str(self.repo), "commit", "-qam", "more")
        second = self._handoff(first["sha"])
        return first_text, second

    def _legacy(self):
        return self.state / "exchange" / "round-1-request.md"

    def test_the_matching_current_copy_briefs_the_open_request(self):
        _, second = self._lineage_two()
        code, rec = self._run("brief")
        self.assertEqual(code, 0, rec)
        self.assertIn(second["sha"][:12], rec["brief"])
        self.assertIn(second["kept"], rec["relay"])

    def test_a_matching_legacy_copy_still_briefs(self):
        # The fallback serves an old-layout copy when it IS the recorded
        # bytes — the paired control for the refusal below.
        _, second = self._lineage_two()
        current = Path(second["kept"])
        self._legacy().write_bytes(current.read_bytes())
        current.unlink()
        code, rec = self._run("brief")
        self.assertEqual(code, 0, rec)
        self.assertIn(second["sha"][:12], rec["brief"])

    def test_a_stale_legacy_copy_is_refused_not_served(self):
        # The reviewer's lifecycle: the earlier lineage's round-1 request
        # sits at the flat name, the current copy is gone.
        first_text, second = self._lineage_two()
        self._legacy().write_text(first_text, encoding="utf-8")
        Path(second["kept"]).unlink()
        code, rec = self._run("brief")
        self.assertNotEqual(code, 0, rec)
        self.assertEqual(rec["next_kind"], "blocked")
        self.assertFalse(rec.get("ok"))
        self.assertIn("does not reproduce the recorded digest", rec["error"])
        self.assertNotIn("relay", rec)
        self.assertNotIn("brief", rec)

    def test_missing_everywhere_is_reported_as_missing(self):
        _, second = self._lineage_two()
        Path(second["kept"]).unlink()
        code, rec = self._run("brief")
        self.assertNotEqual(code, 0, rec)
        self.assertEqual(rec["next_kind"], "blocked")
        self.assertIn("were not kept", rec["error"])

    def test_read_kept_is_the_one_checked_read(self):
        # The contract every consumer shares: (path, text) with text None
        # for a copy that does not reproduce the digest.
        cfg = dataclasses.replace(CFG, ledger_dir=self.tmp / "s")
        text = request_text()
        digest = transport._digest_text(text)
        self.assertEqual(transport.read_kept(cfg, 1, "request", lineage=1,
                                             digest=digest), (None, None))
        kept = Path(transport.keep_bytes(cfg, 1, "request", text, lineage=1))
        self.assertEqual(transport.read_kept(cfg, 1, "request", lineage=1,
                                             digest=digest), (kept, text))
        kept.write_text(text.replace("\n", "\r\n"), encoding="utf-8",
                        newline="")
        self.assertEqual(transport.read_kept(cfg, 1, "request", lineage=1,
                                             digest=digest), (kept, None))


if __name__ == "__main__":
    unittest.main()

"""The warm hand-off cache keys on the review base (public issue #7, loupe
0.26.0).

Re-running `handoff` at an unchanged tip with a warm, digest-verified kept
request serves that request without re-running gates or pushing. The base
was outside the key: a second `handoff --base Y` came back `cached: true`
with `Base:` still X, the author's second base silently dropped. RULED:
key, not refuse — a changed base is an authored input like a corrected
claim, and a refusal at an unchanged tip would leave no repair but a new
commit. The key is the base a cold emission from the same arguments would
use (`--base`, else the last verdict's SHA), RESOLVED as the cold path
resolves it, compared with the kept envelope's `Base:`.

Through the real entry point: `bin/loupe handoff` as a subprocess in a
scratch repository whose history is base -> mid -> tip, one row after
another on the same record, each row's result its own subtest.

| class                 | second invocation's base | expected                     |
|-----------------------|--------------------------|------------------------------|
| same id               | --base BASE              | warm, same digest            |
| another spelling      | --base BASE[:12]         | warm                         |
| symbolic              | --base HEAD~2 (= BASE)   | warm                         |
| omitted, round 1      | no --base                | cold -> refused, state kept  |
| unresolvable          | --base no-such-ref       | cold -> refused, state kept  |
| changed               | --base MID               | cold, re-emitted, Base: MID  |
| changed back          | --base BASE              | cold, re-emitted, Base: BASE |
| round 2, default      | no --base, twice         | cold then warm (verdict SHA) |

"State kept" is byte-identity over everything a refusal guards: the
ledger, the kept envelopes (the cache itself), the remote's refs and the
author's checkout.

MUTATIONS (results in the track report): drop the base comparison; compare
the expression unresolved; let an absent base through; derive the cache's
base from `--base` alone.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from review.tests import synth
from review.tests._real_cli import Scratch, git, loupe


class _BaseRound(unittest.TestCase):

    def setUp(self):
        self.s = s = Scratch(self, "base-cache-")
        s.mid = s.head
        (s.repo / "g.txt").write_text("three\n", encoding="utf-8")
        git(s.repo, "add", "g.txt")
        git(s.repo, "commit", "-qm", "tip")
        s.tip = git(s.repo, "rev-parse", "HEAD")

    def handoff(self, *base_args):
        return loupe(self.s, "handoff", "--claim-file", str(self.s.claim),
                     *base_args)

    def kept_base(self, rec) -> str:
        (line,) = [l for l in Path(rec["kept"]).read_text(
            encoding="utf-8").splitlines() if l.startswith("Base:")]
        return line.split()[1]

    def state(self) -> dict:
        s = self.s
        exchange = s.state / "exchange"
        return {"ledger": (s.state / "ledger.jsonl").read_bytes(),
                "kept": {p.relative_to(exchange).as_posix(): p.read_bytes()
                         for p in sorted(exchange.rglob("*"))
                         if p.is_file()},
                "remote": Scratch.snapshot(s.remote, bare=True),
                "checkout": Scratch.snapshot(s.repo)}

    def warm(self, row, *base_args, digest):
        with self.subTest(row=row):
            code, rec, out, err = self.handoff(*base_args)
            self.assertEqual(code, 0, (out, err))
            self.assertIs(rec["cached"], True, row)
            self.assertEqual(rec["digest"], digest)

    def cold(self, row, *base_args, want_base):
        with self.subTest(row=row):
            code, rec, out, err = self.handoff(*base_args)
            self.assertEqual(code, 0, (out, err))
            self.assertIs(rec["cached"], False, row)
            self.assertEqual(self.kept_base(rec), want_base)
            return rec

    def refused(self, row, *base_args, says):
        with self.subTest(row=row):
            Scratch.settle(self.s.repo)
            before = self.state()
            code, rec, out, err = self.handoff(*base_args)
            self.assertNotIn("Traceback", err)
            self.assertNotEqual(code, 0, rec)
            self.assertIn(says, (rec or {}).get("error", out))
            self.assertEqual(self.state(), before,
                             f"{row}: a refusal moved guarded state")


class TestTheCacheKeysOnTheResolvedBase(_BaseRound):

    def test_round_one(self):
        s = self.s
        first = self.cold("first", "--base", s.base, want_base=s.base)
        digest = first["digest"]
        self.warm("same id", "--base", s.base, digest=digest)
        self.warm("another spelling", "--base", s.base[:12], digest=digest)
        self.warm("symbolic", "--base", "HEAD~2", digest=digest)
        self.refused("omitted, round 1",
                     says="no prior verdict in the ledger; pass --base")
        self.refused("unresolvable", "--base", "no-such-ref",
                     says="does not name one commit")
        changed = self.cold("changed", "--base", s.mid, want_base=s.mid)
        self.assertNotEqual(changed["digest"], digest)
        self.warm("changed, again", "--base", s.mid,
                  digest=changed["digest"])
        self.cold("changed back", "--base", s.base, want_base=s.base)


class TestTheDefaultBaseIsTheLastVerdict(_BaseRound):
    """Round 2 with no `--base`: the cold path uses the SHA the last
    verdict ruled on, so the cache must key on that same derived base —
    not on the absent flag, which would make every default re-run cold."""

    def test_round_two_default(self):
        s = self.s
        first = self.cold("round 1", "--base", s.base, want_base=s.base)
        verdict = s.root / "v1.md"
        verdict.write_text(synth.verdict_text(sha=first["sha"]),
                           encoding="utf-8")
        code, rec, out, err = loupe(s, "close", "--verdict", str(verdict))
        self.assertEqual(code, 0, (out, err))
        answers = s.root / "d1.json"
        answers.write_text(
            '{"head": "%s", "author": "claude", "dispositions": [{'
            '"finding_id": "F1", "disposition": "accepted", "payload": {'
            '"change": "fixed", "verification": "observed", '
            '"falsification": {"status": "pass", '
            '"mutation": "fails_without_fix"}}}]}' % first["sha"],
            encoding="utf-8")
        code, rec, out, err = loupe(s, "respond", "--verdict", str(verdict),
                                    "--from-json", str(answers), "--out",
                                    str(s.root / "d1.md"))
        self.assertEqual(code, 0, (out, err))
        (s.repo / "g.txt").write_text("four\n", encoding="utf-8")
        git(s.repo, "commit", "-qam", "the fix")
        second = self.cold("round 2, default", want_base=first["sha"])
        self.warm("round 2, default, again", digest=second["digest"])
        self.warm("round 2, the same base stated", "--base", first["sha"],
                  digest=second["digest"])
        self.cold("round 2, another base", "--base", s.base,
                  want_base=s.base)


if __name__ == "__main__":
    unittest.main()

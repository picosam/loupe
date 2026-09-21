"""A `reclassified` closure with no `Residue:` beside a new finding on the
closed finding's anchor is a NOTICE (0.25.0, public issue #3).

Reclassifying with no residue is legal, and so is a new finding on the same
anchor; together they are the shape `convergence` cannot read without a
declaration — the new fingerprint counts as a fresh finding on a recurring
anchor (the `hunted` signal) unless the closure's note happens to name it.
So `validate` says so, by finding id, and changes no exit status.

"Same anchor" and "new" are convergence's own, read from the ledger that
computes them (`Ledger.thread_anchor`, `Ledger.ruled_before`): one
authority, never a second reading.

THE PARTITION, each row a real `loupe validate` over a round-2 verdict in a
scratch repository whose round 1 was handed off and closed through the real
CLI (its F1 is anchored at `f.txt`):

  reclassified, no Residue, new finding on the same anchor   notice
  the same, with `Residue: F1` declared                      no notice
  reclassified, no Residue, no finding at all                no notice
  reclassified, no Residue, new finding on another anchor    no notice
  another closure kind (sustained), new finding same anchor  no notice
  reclassified, no Residue, the closed fingerprint unknown   "unchecked"
  the same finding re-raised (not new) on the same anchor    no notice

The exit status is 0 in every row and is compared with the same verdict
validated where the notice cannot fire, so "unchanged" is measured rather
than assumed.
"""
from __future__ import annotations

import unittest

from review.tests.test_precis_taxonomy import (ScratchLoop, finding,
                                               taxonomy_toml, verdict)

NOTICE = "C-RESIDUE-UNDECLARED"
UNCHECKED = "C-RESIDUE-UNCHECKED"


class TestResidueNotice(unittest.TestCase):

    def setUp(self):
        self.loop = ScratchLoop(self, taxonomy_toml(("High", "Medium", "Low"),
                                                    ("High",)))
        code, out = self.loop.handoff()
        self.assertEqual(code, 0, out)
        v1 = self.loop.file("v1.md", verdict(self.loop.head, finding(
            1, "High", title="the whole thing is wrong",
            evidence="f.txt:1")))
        code, out = self.loop.loupe("close", "--verdict", v1)
        self.assertEqual(code, 0, out)
        code, fp = self.loop.loupe("fingerprint", v1)
        self.parent = fp["findings"][0]["fp"]
        # Round 2's target: a new commit the round-1 closure speaks about.
        self.loop.write("f.txt", "three\n")
        self.loop.git("commit", "-q", "-am", "round two")
        self.head2 = self.loop.git("rev-parse", "HEAD")

    def _validate(self, findings: str, closure: str, name: str):
        v = self.loop.file(name, verdict(self.head2, findings, closure))
        code, payload = self.loop.loupe("validate", v)
        codes = [i["code"] for i in payload.get("items", [])]
        return code, payload, codes

    def _closure(self, term="reclassified", residue="", fp=None):
        line = f"- {fp or self.parent} {term}: most of it is fixed"
        return line + (f"\n  Residue: {residue}" if residue else "")

    NARROWED = finding(1, "Medium", title="the narrowed remainder",
                       evidence="f.txt:2")

    def test_no_residue_and_a_new_finding_on_the_anchor_is_a_notice(self):
        code, payload, codes = self._validate(self.NARROWED,
                                              self._closure(), "a.md")
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["ok"])
        self.assertIn(NOTICE, codes)
        item = next(i for i in payload["items"] if i["code"] == NOTICE)
        self.assertEqual(item["level"], "notice")
        self.assertIn(self.parent, item["message"])
        self.assertIn("F1", item["message"])
        self.assertIn("`f.txt`", item["message"])
        self.assertIn("Residue: F1", item["message"])

    def test_a_declared_residue_is_silent(self):
        code, payload, codes = self._validate(
            self.NARROWED, self._closure(residue="F1"), "b.md")
        self.assertEqual(code, 0, payload)
        self.assertNotIn(NOTICE, codes)

    def test_no_new_finding_on_the_anchor_is_silent(self):
        # The only finding is on another anchor…
        code, payload, codes = self._validate(
            finding(1, "Low", title="something elsewhere",
                    evidence="g.txt:1"), self._closure(), "c.md")
        self.assertEqual(code, 0, payload)
        self.assertNotIn(NOTICE, codes)

    def test_no_finding_at_all_is_silent(self):
        code, payload, codes = self._validate("None\n\n", self._closure(),
                                              "d.md")
        self.assertEqual(code, 0, payload)
        self.assertNotIn(NOTICE, codes, payload)

    def test_another_closure_kind_is_silent(self):
        code, payload, codes = self._validate(
            self.NARROWED, self._closure(term="sustained"), "e.md")
        self.assertEqual(code, 0, payload)
        self.assertNotIn(NOTICE, codes)

    def test_a_re_raised_finding_is_not_new(self):
        # The same fingerprint again (same title, anchor, classification):
        # convergence reads it as the SAME thread, so no residue is at issue.
        code, payload, codes = self._validate(
            finding(1, "High", title="the whole thing is wrong",
                    evidence="f.txt:9"), self._closure(), "f.md")
        self.assertEqual(code, 0, payload)
        self.assertNotIn(NOTICE, codes)

    def test_an_unknown_closed_fingerprint_is_stated_unchecked(self):
        stranger = "fp2:" + "0" * 16
        code, payload, codes = self._validate(
            self.NARROWED, self._closure(fp=stranger), "g.md")
        self.assertEqual(code, 0, payload)
        self.assertIn(UNCHECKED, codes)
        self.assertNotIn(NOTICE, codes)

    def test_the_exit_status_is_what_it_is_without_the_notice(self):
        """Measured, not assumed: the notice row and its declared-residue
        twin exit identically, and both as a verdict with no closure."""
        a = self._validate(self.NARROWED, self._closure(), "h1.md")
        b = self._validate(self.NARROWED, self._closure(residue="F1"),
                           "h2.md")
        c = self._validate(self.NARROWED, "", "h3.md")
        self.assertIn(NOTICE, a[2])
        self.assertEqual({a[0], b[0], c[0]}, {0})
        self.assertEqual(a[1]["ok"], c[1]["ok"])


class TestOneAnchorAuthority(unittest.TestCase):
    """The notice and convergence read ONE anchor: `Ledger.thread_anchor`
    is what convergence files the thread under."""

    def test_thread_anchor_is_convergences_thread_anchor(self):
        from review.ledger import Ledger
        ledger = Ledger.in_memory()
        ledger.add({"event": "request", "round": 1, "sha": "a" * 40})
        ledger.add({"event": "verdict", "round": 1, "sha": "a" * 40,
                    "verdict": "changes requested"})
        ledger.add({"event": "finding", "round": 1, "id": "F1",
                    "fp": "fp2:" + "1" * 16, "severity": "High",
                    "anchor_path": "review/x.py"})
        report = ledger.convergence("1")
        ident = ledger.resolve("fp2:" + "1" * 16)
        self.assertEqual(ledger.thread_anchor("fp2:" + "1" * 16, "1"),
                         report["threads"][ident]["anchor"])
        self.assertIsNone(ledger.thread_anchor("fp2:" + "2" * 16, "1"))


if __name__ == "__main__":
    unittest.main()

"""One scripted vertical round through the tool's own surfaces (slice-1
step 2, local leg): emit-request with a deliberately inaccessible artifact →
verdict that must not rule clean over it → respond → ledger → report.

This exercises the machinery of the F4 access asymmetry and the F6 closure
handshake deterministically. What it does NOT exercise — and does not claim
to — is a real second agent on a real cloud surface; that leg needs the
user-relayed reviewer session and is enumerated as unrun in the slice notes.
"""
import dataclasses
import json
import unittest
import unittest.mock
from pathlib import Path

from review import config, validate, wire
from review.emit import emit_request
from review.ledger import Ledger
from review.tests.util import REPO_ROOT

CFG = config.load(REPO_ROOT)
NO_GATES = dataclasses.replace(CFG, gates=[])


class TestShadowRound(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger = Ledger.in_memory()
        cls.ledger.add({"event": "request", "round": 1, "sha": "a" * 40,
                        "bytes": 100})
        cls.ledger.add({"event": "verdict", "round": 1, "sha": "a" * 40,
                        "verdict": "changes requested", "bytes": 100,
                        "finding_ids": 0})

    def test_emitted_request_carries_unavailable_reference(self):
        claim = {
            "objective": "shadow round: exercise the access asymmetry",
            "references": [
                {"path": "review.toml", "required": True},
                {"path": "does/not/exist-anywhere.md", "required": True,
                 "note": "deliberately inaccessible (F4)"},
            ],
        }
        # Emitted against an EMPTY gate manifest, deliberately. This test is
        # about the reference manifest, and running the real manifest here
        # would (a) spawn the whole suite as a subprocess from inside itself
        # and (b) make the assertion depend on whether the developer's tree
        # happens to be clean — the attestations would be legitimately
        # `unbound` mid-edit, which is round-4 F4 working, not failing. Gate
        # execution and binding have their own tests in
        # test_gate_environment.
        # The reachability record is synthetic for the same reason: this test
        # must not push; ensure_pushed has its own tests (test_reachability).
        from review.emit import _git
        head = _git(REPO_ROOT, "rev-parse", "HEAD")
        record = {"state": "pushed", "branch": "main",
                  "ref": "refs/heads/main", "remote": "origin",
                  "url": "ssh://example.invalid/shadow.git", "sha": head,
                  "committed": False}
        envelope = emit_request(NO_GATES, self.ledger, claim, base="HEAD",
                                head="HEAD", reachability=record)
        # Symbolic refs must be resolved to object ids before binding (F16).
        self.assertNotIn('sha="HEAD"', envelope)
        self.assertIn("UNAVAILABLE", envelope)
        self.assertIn("sha256:", envelope)  # digests for reachable references
        parsed = wire.parse_request(envelope)
        self.assertTrue(parsed.wrapped)
        items = validate.validate_request(parsed, NO_GATES)
        self.assertEqual(
            {i.code for i in items if i.level == "error"}, set(),
            [i.message for i in items])
        # An empty manifest still emits the block, holding zero attestations:
        # "these gates ran and there were none" is a statement, where a
        # missing block would be silence (§5.1).
        records, err, tag = validate.parse_attestations(
            parsed.sections["evidence"], validate.accepted_tags(NO_GATES))
        self.assertIsNone(err)
        self.assertEqual(records, [])
        self.assertEqual(tag, NO_GATES.wrapper_tag,
                         "a fresh emission speaks the current dialect")

    def test_closure_handshake_round_trip(self):
        # Author refutes F1 with evidence; the machinery must carry the
        # refutation and the reviewer's answer as typed events.
        verdict_body = (
            'VERDICT: changes requested\n\n## findings\n\n'
            '### F1\nSeverity: Medium\nClassification: unsupported_claim\n'
            'Title: the shadow claim is unsupported\nEvidence: review.toml:1\n'
            'Why: because\nRequired outcome: support it\n'
            'FALSIFICATION: a citation exists\n')
        verdict = wire.parse_verdict(
            f'<loupe-review-verdict sha="{"b" * 40}">\n{verdict_body}\n'
            f'</loupe-review-verdict>')
        fp = verdict.findings[0].fingerprint()

        disp = wire.emit_disposition(
            "loupe", "b" * 40, "c" * 40, "claude", 2,
            [{"finding_id": "F1", "fingerprint": fp,
              "disposition": "refuted",
              "payload": {"evidence": "review.toml:1 declares it"}}])
        parsed = wire.parse_disposition(disp)
        items = validate.validate_disposition(parsed, CFG, against=verdict)
        self.assertEqual([i for i in items if i.level == "error"], [])

        self.ledger.add({"event": "finding", "round": 2, "id": "F1",
                         "fp": fp, "severity": "Medium",
                         "classification": "unsupported_claim",
                         "title": verdict.findings[0].title,
                         "preventable_by": None})
        self.ledger.add({"event": "disposition", "round": 2,
                         "finding_id": "F1", "fp": fp,
                         "disposition": "refuted",
                         "payload": {"evidence": "review.toml:1"}})
        self.ledger.add({"event": "closure", "round": 3, "fp": fp,
                         "closure": "withdrawn",
                         "ref": "F1", "note": "the refutation lands"})
        closures = [e for e in self.ledger.events()
                    if e.get("event") == "closure"]
        self.assertEqual(closures[-1]["closure"], "withdrawn")

    def test_respond_command_shape(self):
        # respond consumes author judgment as JSON and emits the envelope —
        # the disposition is never hand-typed. Runs entirely through stdin
        # and stdout so no writable directory is needed (round-3 F9).
        import contextlib
        import io

        from review.cli import main
        fixture = REPO_ROOT / "review/tests/fixtures/mini-verdict.md"
        payload = json.dumps({
            "head": "c" * 40, "round": 2, "author": "claude",
            "dispositions": [{"finding_id": "F1",
                              "disposition": "accepted",
                              "payload": {"change": "x", "verification": "y",
                                          "falsification": {
                                              "status": "pass",
                                              "mutation": "fails_without_fix"}}}],
        })
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with unittest.mock.patch("sys.stdin", io.StringIO(payload)):
                code = main(["respond", "--verdict", str(fixture),
                             "--from-json", "-"])
        self.assertEqual(code, 0)
        emitted = wire.parse_disposition(buf.getvalue())
        self.assertTrue(emitted.wrapped)
        self.assertEqual(
            emitted.data["dispositions"][0]["fingerprint"],
            wire.parse_verdict(
                fixture.read_text(encoding="utf-8")).findings[0].fingerprint())


if __name__ == "__main__":
    unittest.main()

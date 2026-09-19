"""`close` judges the verdict under the target commit's own authority.
Finding 3 (Medium) of the 2026-09-05 audit: `close` validated the verdict
against the CHECKOUT's config while `validate --from-target` judged it
against the target's, so a checkout that had since renamed a severity
refused a verdict its target had accepted, and the recovery it printed
omitted `--from-target`.
"""

import json
import unittest

from review.tests._transport_fixtures import (
    run_cli, scratch_loop_repo, sh, verdict_text)


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


class TestCloseJudgesUnderTheTargetsAuthority(_ScratchLoop):

    PREFIX = "close-authority-"

    def _rename_low_in_the_checkout(self):
        toml_path = self.repo / "review.toml"
        toml = toml_path.read_text(encoding="utf-8")
        self.assertIn('"Low"', toml)
        toml_path.write_text(toml.replace('"Low"', '"Minor"'),
                             encoding="utf-8")
        sh("git", "-C", str(self.repo), "commit", "-qam",
           "the checkout moves on: Low becomes Minor")

    def test_unrelated_checkout_changes_cannot_alter_acceptance(self):
        opened = self._handoff(self.base)
        verdict = self._verdict(verdict_text(sha=opened["sha"]))  # Low
        self._rename_low_in_the_checkout()
        # The paired control that the checkout's authority now DIFFERS:
        # judged under it, the same verdict is refused for its severity.
        code, checkout = self._run("validate", verdict)
        self.assertNotEqual(code, 0, checkout)
        self.assertIn("V-SEVERITY", json.dumps(checkout))
        # Judged under the target's, it stands — at validate and at close.
        code, target = self._run("validate", verdict, "--from-target")
        self.assertEqual(code, 0, target)
        code, closed = self._run("close", "--verdict", verdict)
        self.assertEqual(code, 0, closed)
        self.assertEqual(closed["verdict"], "changes requested")
        self.assertEqual(closed["findings"], 1)

    def test_a_verdict_the_target_refuses_is_sent_back_under_its_authority(self):
        opened = self._handoff(self.base)
        bogus = verdict_text(sha=opened["sha"]).replace("Severity: Low",
                                                         "Severity: Bogus")
        verdict = self._verdict(bogus)
        code, rec = self._run("close", "--verdict", verdict)
        self.assertNotEqual(code, 0, rec)
        self.assertIn("V-SEVERITY", rec["error"])
        self.assertIn("--from-target", rec["next"],
                      "the recovery names the authority the verdict is "
                      "judged under, not the checkout's")


if __name__ == "__main__":
    unittest.main()

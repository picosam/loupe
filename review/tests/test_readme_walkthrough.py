"""The README's thirty-second walkthrough runs exactly as printed
(lineage 18 round 1 F3).

The author block is EXTRACTED from the shipped README — not restated
here — and executed byte for byte in a disposable repository with a bare
remote, a committed `review.toml`, the reference the claim names, two
commits (so the printed base expression resolves), and an empty lineage
ledger under a scratch HOME. It must emit, record and keep a round-1
request. The paired mutation strips the round-1 `--base` from the same
printed bytes and must reproduce the typed refusal — so deleting the
base from the example, or the example drifting away from a runnable
fresh-lineage sequence, turns this module red rather than shipping.

The later-round control that legitimately omits `--base` is
`test_transport.TestFullLoopIntegration.test_the_loop_end_to_end`
step 4: after a recorded verdict, `handoff` derives the base from the
ledger. This module deliberately does not duplicate that flow; it owns
the fresh-state entry point the README prints first.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from review.tests.util import REPO_ROOT, public_path


def author_script():
    """The walkthrough's author fence, as runnable bytes: `$ `-prefixed
    lines are commands, everything else (the heredoc body) is kept
    verbatim. None where no README or no walkthrough ships."""
    readme = public_path("README.md")
    if readme is None:
        return None
    text = readme.read_text(encoding="utf-8")
    found = re.search(r"## Thirty seconds.*?```console\n(.*?)```", text,
                      re.S)
    if found is None:
        return None
    lines = [line[2:] if line.startswith("$ ") else line
             for line in found.group(1).splitlines()]
    return "\n".join(lines) + "\n"


class TestWalkthroughRunsAsPrinted(unittest.TestCase):

    def setUp(self):
        self.script = author_script()
        if self.script is None:
            self.skipTest("no README walkthrough shipped in this tree")
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="walkthrough-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        remote = self.tmp / "remote.git"
        self.repo = self.tmp / "repo"
        self._sh("git", "init", "-q", "--bare", "-b", "main", str(remote))
        self._sh("git", "init", "-q", "-b", "main", str(self.repo))
        for k, v in (("user.name", "w"),
                     ("user.email", "w@example.invalid"),
                     ("commit.gpgsign", "false")):
            self._sh("git", "-C", str(self.repo), "config", k, v)
        # The generic gate-free config shape the loop test proves: taxonomy
        # and roles from this tree's own root config, manifest dropped so
        # the scaffold attests no foreign commands.
        toml = (REPO_ROOT / "review.toml").read_text(encoding="utf-8")
        toml = toml[:toml.index("[[gates]]")] + toml[toml.index("[roles]"):]
        (self.repo / "review.toml").write_text(toml, encoding="utf-8")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "parser.py").write_text("GAP = True\n",
                                                     encoding="utf-8")
        self._commit("base")
        (self.repo / "src" / "parser.py").write_text("GAP = False\n",
                                                     encoding="utf-8")
        self._commit("close the parser gap")
        self._sh("git", "-C", str(self.repo), "remote", "add", "origin",
                 str(remote))
        self._sh("git", "-C", str(self.repo), "push", "-q", "-u", "origin",
                 "main")
        home = self.tmp / "home"
        home.mkdir()
        self.home = home
        self.env = {**os.environ, "HOME": str(home),
                    "PATH": f"{REPO_ROOT / 'bin'}{os.pathsep}"
                            f"{os.environ.get('PATH', '')}"}

    def _sh(self, *args):
        subprocess.run(args, check=True, capture_output=True, text=True,
                       timeout=60)

    def _commit(self, subject):
        self._sh("git", "-C", str(self.repo), "add", "-A")
        self._sh("git", "-C", str(self.repo), "commit", "-qm", subject)

    def _bash(self, script):
        return subprocess.run(
            ["bash", "-eu", "-o", "pipefail", "-c", script],
            cwd=self.repo, env=self.env, capture_output=True, text=True,
            timeout=180)

    def test_the_printed_author_sequence_emits_and_keeps(self):
        proc = self._bash(self.script)
        self.assertEqual(proc.returncode, 0,
                         f"stdout: {proc.stdout}\nstderr: {proc.stderr}")
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["round"], 1)
        self.assertTrue(payload["recorded"])
        kept = Path(payload["kept"])
        self.assertTrue(kept.is_file(), kept)
        self.assertIn(str(self.home), str(kept),
                      "the kept request landed outside the scaffold's "
                      "state, so the empty-ledger premise was not this "
                      "test's")

    def test_the_round_1_base_is_load_bearing(self):
        self.assertIn(
            "--base", self.script,
            "the README's fresh-lineage example no longer names a base; "
            "round 1 cannot derive one from an empty ledger")
        stripped = re.sub(r"[ \t]+--base[^\n]*", "", self.script)
        self.assertNotIn("--base", stripped)
        proc = self._bash(stripped)
        self.assertNotEqual(proc.returncode, 0,
                            "the fresh-lineage sequence emitted WITHOUT a "
                            "base: the printed flag would be decoration")
        self.assertIn("--base", proc.stdout + proc.stderr,
                      "the refusal does not name the missing flag")


if __name__ == "__main__":
    unittest.main()

"""Path rendering (lineage 5 round 1, F3).

The generated commands are the adapter contract's LITERAL commands — an
agent runs them verbatim — so a repository path the shell would split or
interpret produced a different argv than the one the envelope meant.
`review/paths.py` is the one renderer both surfaces use: the emitter's
`Diff:` line and `take`'s printed diff command cannot disagree on quoting,
ordinary paths render byte-identical to before, and human prose gets the
opposite treatment (an exact home prefix abbreviates to `~`, which never
feeds back into a command).
"""
from __future__ import annotations

import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

from review import paths

METACHAR_DIRS = ("repo with space", "it's", 'q"uote', "semi;colon",
                 "uni-été", "da sh -n", "dollar$var", "back`tick")


class TestShellPath(unittest.TestCase):

    def test_an_ordinary_path_renders_unchanged(self):
        self.assertEqual(paths.shell_path("/a/b/c"), "/a/b/c")

    def test_every_metacharacter_class_stays_one_argument(self):
        for name in METACHAR_DIRS:
            p = f"/tmp/{name}"
            with self.subTest(path=p):
                argv = shlex.split(f"git -C {paths.shell_path(p)} diff a...b")
                self.assertEqual(argv, ["git", "-C", p, "diff", "a...b"])

    def test_execution_control_against_a_metacharacter_repository(self):
        """The command must not merely split right — it must RUN. A scratch
        repository whose path carries a space and a quote is diffed through
        the exact renderer the emitter and `take` print; raw interpolation
        fails this by handing git a directory that does not exist."""
        try:
            tmp = Path(tempfile.mkdtemp(prefix="paths-exec-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        repo = tmp / "repo with 'quote"
        repo.mkdir()

        def git(*args):
            out = subprocess.run(["git", "-C", str(repo), *args],
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr)
            return out.stdout.strip()

        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)],
                       check=True, capture_output=True, timeout=60)
        for k, v in (("user.name", "t"), ("user.email", "t@example.invalid"),
                     ("commit.gpgsign", "false")):
            git("config", k, v)
        (repo / "f.txt").write_text("one\n", encoding="utf-8")
        git("add", ".")
        git("commit", "-q", "-m", "one")
        base = git("rev-parse", "HEAD")
        (repo / "f.txt").write_text("two\n", encoding="utf-8")
        git("commit", "-aq", "-m", "two")
        head = git("rev-parse", "HEAD")

        cmd = paths.diff_command(repo, base, head)
        run = subprocess.run(shlex.split(cmd), capture_output=True,
                             text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("f.txt", run.stdout)


class TestCommandSurfaceInventory(unittest.TestCase):
    """Round-2 F4: every path interpolated into an executable relay or
    typed-recovery command goes through the one shell-safe renderer —
    centralizing only the surfaces a reproducer happened to name left the
    verdict relay with the same argv split one round later.

    The behavioural rows for the take relay, the diff command and the
    placeholder form live in `test_command_surface` /
    `test_command_boundary` (2026-09-02); what stays here is the one
    surface those do not split back with a metacharacter path — the
    verdict relay's `close` line.
    """

    META = "/tmp/verdict with space.md"

    def _verdict(self, ruling="changes requested"):
        from review import wire
        return wire.parse_verdict(
            f'<loupe-review-verdict sha="{"a" * 40}">\n'
            f"VERDICT: {ruling}\n\n## findings\n\nNone\n"
            f"\n## evidence checked\n\n- x\n")

    def test_verdict_relay_close_and_respond_carry_one_path_argument(self):
        import shlex
        from review import brief
        relay = brief.verdict_relay(self._verdict(), source=self.META)
        close = next(l for l in relay.splitlines()
                     if l.startswith("loupe close"))
        self.assertEqual(shlex.split(close),
                         ["loupe", "close", "--verdict", self.META])
        # `respond` is no longer rendered here at all: `close` derives it
        # when it records the round, with the path the tool kept rather than
        # the reviewer's. One fact, one source.
        self.assertNotIn("respond", relay)



class TestDisplayPath(unittest.TestCase):

    def test_a_home_prefixed_path_abbreviates(self):
        home = Path.home()
        self.assertEqual(paths.display_path(home / "x" / "y"), "~/x/y")

    def test_home_itself_is_tilde(self):
        self.assertEqual(paths.display_path(Path.home()), "~")

    def test_outside_home_is_unchanged(self):
        self.assertEqual(paths.display_path("/var/log/x"), "/var/log/x")

    def test_a_string_prefix_that_is_not_a_path_prefix_is_unchanged(self):
        # A sibling whose name merely EXTENDS the home directory's (home
        # plus a trailing "x") shares its string prefix but not its path:
        # the abbreviation is per-component, never a string startswith.
        sibling = str(Path.home()) + "x/file"
        self.assertEqual(paths.display_path(sibling), sibling)


if __name__ == "__main__":
    unittest.main()

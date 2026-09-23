"""What `take` shows a person at a terminal (loupe 0.26.0).

Public issue #9: `take` computes the TARGET commit's undeclared keys
(`decide`) and carried them in the JSON result only. Neither the default
nor the compact TTY rendering showed them, so a person reading the
terminal — the one reader the adapter's ask-once rule does not reach —
never saw what the target leaves open.

Everything here runs `bin/loupe` as a subprocess in a scratch repository
(`_real_cli`): an author hands off, a reviewer clone takes. A rendering is
proved through a pseudo-terminal, because `_out` prints the human text only
when stdout IS a terminal. Each take runs in its own state directory, and
the JSON take of the same request is the reference every rendering is
compared with — values are read from the run, never restated.

Partition (#9):

| class          | target declares           | rendering | expected                                 |
|----------------|---------------------------|-----------|------------------------------------------|
| open, default  | three DECIDE keys absent  | default   | `decide: N key(s)` + every entry's lines |
| open, compact  | the same                  | --compact | the same block                           |
| none, default  | every DECIDE key          | default   | `decide: none — …`                       |
| none, compact  | every DECIDE key          | --compact | `decide: none — …`                       |

MUTATIONS (the track report records each one's own result): drop the
block from the default rendering; drop it from the compact rendering.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import unittest
from pathlib import Path

from review import config
from review.tests._real_cli import Scratch, loupe
from review.tests.test_git_timeout import TAXONOMY

#: Every optional key the scratch configuration leaves out, declared.
DECLARE_ALL_ROLES = 'transport = "path"\ndebug = false\n'
DECLARE_ALL_LIMITS = "token_budget = 600000\n"


class _TakeRound(unittest.TestCase):
    """One handed-off round and a reviewer clone to take it in."""

    def round(self, *, roles_extra: str = "", limits_extra: str = "",
              claim: dict | None = None):
        s = Scratch(self, "take-tty-")
        if roles_extra or limits_extra:
            (s.repo / config.CONFIG_BASENAME).write_text(
                TAXONOMY.format(roles_extra=roles_extra,
                                limits_extra=limits_extra),
                encoding="utf-8")
        if claim is not None:
            s.claim.write_text(json.dumps(claim), encoding="utf-8")
        code, rec, out, err = loupe(s, "handoff", "--claim-file",
                                    str(s.claim), "--base", s.base)
        self.assertEqual(code, 0, (out, err))
        s.rec = rec
        s.clone, _state, _hooks = s.reviewer_clone()
        s.takes = 0
        return s

    def take(self, s, *extra, tty: bool):
        s.takes += 1
        code, payload, out, err = loupe(
            s, "take", s.rec["kept"], "--as", "codex", *extra,
            where=s.clone, state=s.root / f"state-take-{s.takes}", tty=tty)
        self.assertEqual(code, 0, (out, err))
        return payload if not tty else out


class TestTheTerminalShowsTheTargetsDecisions(_TakeRound):

    def block(self, text: str) -> str:
        """The decide block: from its label to the diff line after it."""
        start = text.index("\ndecide: ") + 1
        end = text.index("\ndiff:   ", start)
        self.assertLess(text.index("\nhead:   "), start,
                        "the block sits after the checkout line")
        return text[start:end]

    def assert_open(self, text: str, reference: dict):
        decide = reference["decide"]
        self.assertTrue(decide, "the row needs an open key to show")
        block = self.block(text)
        self.assertTrue(block.startswith(
            f"decide: {len(decide)} key(s) the target's configuration "
            f"({reference['target']['config']}) never declared."), block)
        for entry in decide:
            self.assertIn(f"        {entry['key']} — under [", block)
            self.assertIn(f"          {entry['meaning']}", block)
            self.assertIn(f"applied: {json.dumps(entry['applied'])}", block)
            self.assertIn(f"set:     "
                          f"{entry['set'] or '(no line declares it)'}", block)
            self.assertIn(f"unset:   "
                          f"{entry['unset'] or '(this key has no off state)'}",
                          block)

    def assert_none(self, text: str, reference: dict):
        self.assertEqual(reference["decide"], [])
        self.assertEqual(
            self.block(text),
            f"decide: none — the target's configuration "
            f"({reference['target']['config']}) declares every optional key")

    def test_open_keys_default_and_compact(self):
        s = self.round()
        reference = self.take(s, tty=False)
        with self.subTest(row="open, default"):
            self.assert_open(self.take(s, tty=True), reference)
        with self.subTest(row="open, compact"):
            self.assert_open(self.take(s, "--compact", tty=True), reference)

    def test_a_fully_declared_target_default_and_compact(self):
        s = self.round(roles_extra=DECLARE_ALL_ROLES,
                       limits_extra=DECLARE_ALL_LIMITS)
        reference = self.take(s, tty=False)
        with self.subTest(row="none, default"):
            self.assert_none(self.take(s, tty=True), reference)
        with self.subTest(row="none, compact"):
            self.assert_none(self.take(s, "--compact", tty=True), reference)


class TestTheReviewerLocalScopedDiff(_TakeRound):
    """Public issue #10: the request's `Scoped:` line is a command for the
    AUTHOR's checkout; `take` re-rendered `Diff:` for the reviewer's and
    gave no scoped one. `take` now carries `scoped` — the request's literal
    pathspecs over the request's own range, rooted in the reviewer's
    checkout through the one diff renderer — or null with `scoped_note`
    saying why, and both renderings print a `scoped:` line in every state.

    | class            | the request's `Scoped:` line        | `scoped`      |
    |------------------|-------------------------------------|---------------|
    | absent           | none (no scope_paths)               | null + why    |
    | command          | literal pathspecs over Base...sha   | reviewer-root |
    | none             | the emitter's `none — …`            | null + why    |
    | other range      | edited to another range             | null + why    |
    | pattern pathspec | a spec without `:(literal)`         | null + why    |
    | two lines        | a second `Scoped:` line             | null + why    |
    | unparseable      | an unbalanced quote                 | null + why    |

    The last four are requests edited after emission, taken as files: a
    reviewer command re-rendered from any other form would carry another
    selection under this tool's name. The `command` row is also RUN, in the
    reviewer's clone, and must show the scoped path and not the other.

    MUTATIONS (results in the track report): drop the range check; drop the
    literal-pathspec check; root the command at the author's path; read the
    first of two lines; drop the line from the default rendering.
    """

    def scoped_round(self, scope_paths):
        claim = {"objective": "scoped take",
                 "references": [{"path": "review.toml", "required": True}]}
        if scope_paths is not None:
            claim["scope_paths"] = scope_paths
        s = Scratch(self, "take-scoped-")
        # A second changed path in the span, committed by the author, so a
        # scope of `f.txt` excludes something the whole diff shows.
        (s.repo / "g.txt").write_text("other\n", encoding="utf-8")
        from review.tests.test_git_timeout import git
        git(s.repo, "add", "g.txt")
        git(s.repo, "commit", "-qm", "another path")
        s.claim.write_text(json.dumps(claim), encoding="utf-8")
        code, rec, out, err = loupe(s, "handoff", "--claim-file",
                                    str(s.claim), "--base", s.base)
        self.assertEqual(code, 0, (out, err))
        s.rec = rec
        s.clone, _state, _hooks = s.reviewer_clone()
        s.takes = 0
        s.request = Path(rec["kept"]).read_text(encoding="utf-8")
        return s

    def take_file(self, s, text: str):
        s.takes += 1
        path = s.root / f"edited-{s.takes}.md"
        path.write_bytes(text.encode("utf-8"))
        code, payload, out, err = loupe(
            s, "take", str(path), "--as", "codex", where=s.clone,
            state=s.root / f"state-take-{s.takes}")
        self.assertEqual(code, 0, (out, err))
        return payload

    def scoped_line(self, s) -> str:
        (line,) = [l for l in s.request.splitlines()
                   if l.startswith("Scoped: ")]
        return line

    def assert_none(self, payload, why: str):
        self.assertIsNone(payload["scoped"])
        self.assertIn(why, payload["scoped_note"])

    def test_absent(self):
        s = self.scoped_round(None)
        self.assertNotIn("\nScoped: ", s.request)
        payload = self.take(s, tty=False)
        self.assert_none(payload, "declares no `scope_paths`")
        self.assertIn("scoped: none — the request carries no `Scoped:` line",
                      self.take(s, tty=True))

    def test_command_rooted_here_and_run(self):
        s = self.scoped_round(["f.txt"])
        author = shlex.split(self.scoped_line(s)[len("Scoped: "):])
        payload = self.take(s, tty=False)
        mine = shlex.split(str(payload["scoped"]))
        whole = shlex.split(str(payload["diff"]))
        # Rooted where `diff` is rooted — this checkout — and otherwise the
        # author's command word for word: same range, same pathspecs.
        self.assertEqual(mine[2], whole[2])
        self.assertNotEqual(mine[2], author[2])
        self.assertEqual(mine[:2] + mine[3:], author[:2] + author[3:])
        ran = subprocess.run(["sh", "-c", str(payload["scoped"])],
                             capture_output=True, text=True, timeout=60,
                             stdin=subprocess.DEVNULL)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("a/f.txt", ran.stdout)
        self.assertNotIn("g.txt", ran.stdout)
        # The paired control: the whole diff shows both.
        both = subprocess.run(["sh", "-c", str(payload["diff"])],
                              capture_output=True, text=True, timeout=60,
                              stdin=subprocess.DEVNULL).stdout
        self.assertIn("a/g.txt", both)
        for extra in ((), ("--compact",)):
            with self.subTest(rendering=extra or "default"):
                text = self.take(s, *extra, tty=True)
                self.assertIn(f"\ndiff:   {payload['diff']}\n"
                              f"scoped: {payload['scoped']}\nthen:   ",
                              text)

    def test_the_emitters_none(self):
        s = self.scoped_round(["nope/"])
        self.assertIn("Scoped: none — ", s.request)
        payload = self.take(s, tty=False)
        self.assert_none(payload, "the request's own `Scoped:` line gives no "
                                  "command (none — ")

    def test_an_edited_line_is_not_re_rendered(self):
        s = self.scoped_round(["f.txt"])
        line = self.scoped_line(s)
        other = s.base[:-1] + ("0" if s.base[-1] != "0" else "1")
        rows = {
            "other range": line.replace(f"{s.base}...", f"{other}..."),
            "pattern pathspec": line.replace("':(literal)f.txt'", "f.txt"),
            "two lines": line + "\n" + line,
            "unparseable": line + " '",
        }
        # The paired control: the unedited bytes, taken as a file too.
        with self.subTest(row="control"):
            self.assertIsNotNone(self.take_file(s, s.request)["scoped"])
        for row, edited in rows.items():
            with self.subTest(row=row):
                self.assertNotEqual(edited, line)
                payload = self.take_file(s, s.request.replace(line, edited))
                self.assertIsNone(payload["scoped"], row)
                self.assertTrue(payload["scoped_note"], row)


if __name__ == "__main__":
    unittest.main()

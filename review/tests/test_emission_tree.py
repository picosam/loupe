"""The request's `Tree:` line says what the EMITTING checkout holds, and
calls nothing invalid that the attestations' own `binding` does not (brief
`loupe-tool-feedback-2026-10-06` item 1).

Both reviewers of lineage Lb81962e787 (2026-10-06) read `Tree: DIRTY —
attestations from a dirty tree are not attestations` above 27 records each
`bound` to the target: the hand-off ran its local gates at the commit, and
an analyst deposit landed under `briefs/inbox/` before the emission read
the status. An untracked path is beside the tree, not in it, so ALONE it is
now a notice naming the paths; every state in which the tracked content is
not provably the target's reads exactly as before.

THROUGH THE REAL ENTRY POINT. Each row is a scratch repository with its own
configuration and state directory, and `emit-request --local-only` run as a
subprocess. The repository declares one gate, `act`, which runs a script
kept beside the repository: the gate is how a row changes the checkout
AFTER the gates recorded their binding and BEFORE the emission reads its
status — the interval in which the deposit landed. Every row asserts each
record `bound`, so the header is judged against attestations that are
valid, and asserts the whole `Tree:` line.

THE PARTITION, one row per class of emission-time state:

  E1  nothing changed                              clean at emission
  E2  one untracked deposit                        the notice, naming it
  E3  seven untracked paths                        five named, two counted
  E4  a tracked edit                               DIRTY, as before
  E5  a tracked edit beside a deposit              DIRTY
  E6  a staged new file                            DIRTY
  E6b a staged new file beside a deposit           DIRTY
  E7  a deposit beside an assume-unchanged edit    DIRTY
  E8  a deposit beside a skip-worktree edit        DIRTY
  E9  a deposit beside a stale monitor's edit      DIRTY
  E10 a deposit beside a same-size, same-time edit DIRTY
      under `core.trustctime=false`
  E11 an assume-unchanged edit, nothing untracked  clean at emission: the
  E12 a skip-worktree edit, nothing untracked      stated limit, as before
                                                   0.30.0

E2 against E4 to E10 is the pairing: each strict row differs from the
notice row by the one thing that makes the tracked content unprovable.
E11 and E12 pin the limit review round 2 of lineage Lb33715f372 found
(F4, answered by narrowing): the hidden-edit checks run only when untracked
paths are the status's only lines, so an edit an index flag hides, with
nothing untracked beside it, reads clean at emission, as before 0.30.0.
E7 and E8 are their pairs, the same edits beside a deposit.
The précis (`brief`) is read for E2 and E4: silent on the notice, and the
bold DIRTY line as before.

MUTATIONS (manual, copy of review/emit.py, `python3.15 -B`; recorded in
the commit): `emission_tree`'s hidden-edit check removed sends E7 to E10
red; only its `tree_differs` half removed sends E9 and E10 red; untracked
lines judged alone though a tracked line sits beside them sends E6b red
(E5's unstaged edit is caught by the cold index as well); any status line
DIRTY again (the old rule) sends E2 and E3 red; the hidden-edit checks run
on an empty status too sends E11 and E12 red, so moving that limit moves
these rows and the documents that state it together.
"""
from __future__ import annotations

import os
import re
import unittest

from review import emit
from review.tests.test_precis_taxonomy import ScratchLoop, taxonomy_toml

ACT_GATE = ('\n[[gates]]\nid = "act"\ncommand = ["sh", "-c", '
            '"if [ -f ../act.sh ]; then sh ../act.sh; fi"]\n'
            'blocking = false\n')
DEPOSIT = "briefs/inbox/dep.md"
NOTICE_ONE = (
    "Tree:   clean at emission in its tracked content, which is the target "
    "commit's; 1 untracked path(s) beside it, not in it: "
    "'briefs/inbox/dep.md' — a notice, not an invalidation: 1 of 1 "
    "attestation(s) below bind the target commit's tree, each by its own "
    "`binding`")
DIRTY = "Tree:   " + emit.TREE_DIRTY
CLEAN = "Tree:   clean at emission"
#: A filesystem monitor (hook version 2) that reports nothing changed.
STALE_MONITOR = '#!/bin/sh\nprintf "token\\0"\n'


class TestEmissionTreeLine(unittest.TestCase):

    def emit(self, act: str, prepare=None):
        """A fresh repository whose `act` gate runs `act` (shell, in the
        repository), emitted through the real CLI. Returns (the loop, the
        request text)."""
        loop = ScratchLoop(self, taxonomy_toml(("High",), ("High",),
                                               gates=ACT_GATE))
        loop.write("briefs/inbox/README.md", "the deposit queue\n")
        loop.git("add", "briefs/inbox/README.md")
        loop.git("commit", "-q", "-m", "queue")
        if prepare is not None:
            prepare(loop)
        loop.file("act.sh", act)
        out = loop.tmp / "request.md"
        code, payload = loop.loupe("emit-request", "--claim-file",
                                   loop.claim(), "--base", loop.base,
                                   "--out", out, "--local-only")
        self.assertEqual(code, 0, payload)
        text = out.read_text(encoding="utf-8")
        bindings = re.findall(r'"binding": "([^"]*)"', text)
        self.assertEqual(bindings, ["bound"], text)
        return loop, text

    def hidden_premise(self, loop) -> None:
        """The row's premise, read after the emission from the state it
        left: the status shows the deposit and nothing else, so only the
        hidden-edit check can make the line DIRTY."""
        self.assertEqual(loop.git("status", "--porcelain"), f"?? {DEPOSIT}")

    def tree_line(self, text: str) -> str:
        lines = [ln for ln in text.splitlines() if ln.startswith("Tree:")]
        self.assertEqual(len(lines), 1, text)
        return lines[0]

    # ------------------------------------------------------------ notices

    def test_e1_nothing_changed_is_clean(self):
        _, text = self.emit(":\n")
        self.assertEqual(self.tree_line(text), CLEAN)

    def test_e2_one_deposit_is_a_notice_naming_it(self):
        loop, text = self.emit(f"echo d > {DEPOSIT}\n")
        self.assertEqual(self.tree_line(text), NOTICE_ONE)
        code, payload = loop.loupe("brief", loop.tmp / "request.md")
        self.assertEqual(code, 0, payload)
        self.assertNotIn("Working tree", payload["brief"])

    def test_e3_many_deposits_name_five_and_count_the_rest(self):
        _, text = self.emit("for i in 1 2 3 4 5 6 7; do "
                            "echo d > briefs/inbox/d$i.md; done\n")
        self.assertEqual(self.tree_line(text), (
            "Tree:   clean at emission in its tracked content, which is the "
            "target commit's; 7 untracked path(s) beside it, not in it: "
            "'briefs/inbox/d1.md', 'briefs/inbox/d2.md', "
            "'briefs/inbox/d3.md', 'briefs/inbox/d4.md', "
            "'briefs/inbox/d5.md' and 2 more — a notice, not an "
            "invalidation: 1 of 1 attestation(s) below bind the target "
            "commit's tree, each by its own `binding`"))

    # ------------------------------------------- as strict as it always was

    def test_e4_a_tracked_edit_is_dirty(self):
        loop, text = self.emit("echo edited >> g.txt\n")
        self.assertEqual(self.tree_line(text), DIRTY)
        code, payload = loop.loupe("brief", loop.tmp / "request.md")
        self.assertEqual(code, 0, payload)
        self.assertIn(f"- **Working tree {emit.TREE_DIRTY}** at emission",
                      payload["brief"])

    def test_e5_a_tracked_edit_beside_a_deposit_is_dirty(self):
        _, text = self.emit(f"echo edited >> g.txt\necho d > {DEPOSIT}\n")
        self.assertEqual(self.tree_line(text), DIRTY)

    def test_e6_a_staged_new_file_is_dirty(self):
        _, text = self.emit("echo n > new.txt\ngit add new.txt\n")
        self.assertEqual(self.tree_line(text), DIRTY)

    def test_e6b_a_staged_new_file_beside_a_deposit_is_dirty(self):
        """The status's own tracked line is the only witness here: the
        cold index reads the target's tree and the disk, and a file only
        the index holds is beside both."""
        _, text = self.emit("echo n > new.txt\ngit add new.txt\n"
                            f"echo d > {DEPOSIT}\n")
        self.assertEqual(self.tree_line(text), DIRTY)

    def test_e7_a_deposit_beside_an_assume_unchanged_edit_is_dirty(self):
        loop, text = self.emit("git update-index --assume-unchanged g.txt\n"
                            f"echo edited >> g.txt\necho d > {DEPOSIT}\n")
        self.hidden_premise(loop)
        self.assertEqual(self.tree_line(text), DIRTY)

    def test_e8_a_deposit_beside_a_skip_worktree_edit_is_dirty(self):
        loop, text = self.emit("git update-index --skip-worktree g.txt\n"
                            f"echo edited >> g.txt\necho d > {DEPOSIT}\n")
        self.hidden_premise(loop)
        self.assertEqual(self.tree_line(text), DIRTY)

    # ----------------------------------- the stated limit (Lb33715f372 F4)

    def test_e11_an_assume_unchanged_edit_alone_reads_clean_the_stated_limit(self):
        """E7 without its deposit: the status is empty, the hidden-edit
        checks do not run, and the line reads clean, as before 0.30.0."""
        loop, text = self.emit("git update-index --assume-unchanged g.txt\n"
                               "echo edited >> g.txt\n")
        self.assertEqual(loop.git("status", "--porcelain"), "")
        self.assertEqual(self.tree_line(text), CLEAN)

    def test_e12_a_skip_worktree_edit_alone_reads_clean_the_stated_limit(self):
        """E8 without its deposit, likewise."""
        loop, text = self.emit("git update-index --skip-worktree g.txt\n"
                               "echo edited >> g.txt\n")
        self.assertEqual(loop.git("status", "--porcelain"), "")
        self.assertEqual(self.tree_line(text), CLEAN)

    def test_e9_a_deposit_beside_a_stale_monitor_s_edit_is_dirty(self):
        def monitor(loop):
            hook = loop.tmp / "monitor"
            hook.write_text(STALE_MONITOR, encoding="utf-8")
            hook.chmod(0o755)
            loop.git("config", "core.fsmonitor", str(hook))
            loop.git("config", "core.fsmonitorHookVersion", "2")
            loop.git("status", "--porcelain")
        loop, text = self.emit(
            "git update-index --fsmonitor-valid -- f.txt\n"
            f"echo edited > f.txt\necho d > {DEPOSIT}\n", prepare=monitor)
        self.hidden_premise(loop)
        self.assertEqual(self.tree_line(text), DIRTY)

    def test_e10_a_deposit_beside_a_trusted_stat_edit_is_dirty(self):
        def old_stat(loop):
            loop.git("config", "core.trustctime", "false")
            os.utime(loop.repo / "f.txt", (1577836800, 1577836800))
            loop.git("update-index", "--refresh")
        # "two\n" -> "TWO\n": the same size, its time restored.
        loop, text = self.emit("printf 'TWO\\n' > f.txt\n"
                            "TZ=UTC0 touch -t 202001010000.00 f.txt\n"
                            f"echo d > {DEPOSIT}\n", prepare=old_stat)
        self.hidden_premise(loop)
        self.assertEqual(self.tree_line(text), DIRTY)


if __name__ == "__main__":
    unittest.main()

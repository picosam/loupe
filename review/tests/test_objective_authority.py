"""An objective compared with an authority (brief `take-objective-map`
item 5; loupe 0.26.0).

The recorded case: a round-6 request claimed three disposition ingresses
complete while the tool's own inventory advertised a fourth, and only the
reviewer noticed. 0.25.0 shipped the objectives map this needs. An
objective may now name an AUTHORITY — a committed inventory in the target
tree, one member per line — and the members it claims to COVER; the
request compares the two sets both ways and says, on its face, what the
authority lists that the objective does not cover, and what the objective
covers that the authority does not list. It reports and never refuses: the
authority is the repository's own file, and whether its list is right is
the reviewer's call, which the comparison now puts in front of them.

Through the real entry point: `bin/loupe handoff` as a subprocess in a
scratch repository, the request read back from the kept file.

Partition — the comparison, one request per row:

| class              | covers        | the authority at the target     | expected                          |
|--------------------|---------------|---------------------------------|-----------------------------------|
| complete           | a, b, c       | a, b, c                         | line, no notice                   |
| incomplete (#6)    | a, b, c       | a, b, c, d                      | notice: `d` not covered; précis   |
| over-claim         | a, b, x       | a, b                            | notice: `x` not listed            |
| comments, blanks   | a             | "# head", "", "  a  "           | complete                          |
| many missing       | a             | a + 25 others                   | 10 names, "and 15 more", count 25 |
| untracked here     | a             | ignored, only in the work tree  | cannot be read at the target      |
| symlink            | a             | a tracked symlink               | cannot be read at the target      |
| not UTF-8          | a             | bytes \\xff                     | cannot be read at the target      |
| over the bound     | a             | AUTHORITY_MAX_BYTES + 1 bytes   | cannot be read at the target      |
| no authority       | (no fields)   | —                               | no Authority block (control)      |

Partition — the claim grammar, refused at capture before anything is
committed, pushed or recorded (byte-identity of HEAD, the index, the
worktree, the remote and the ledger):

| class                       | objective                     | expected         |
|-----------------------------|-------------------------------|------------------|
| authority without covers    | authority only                | refused          |
| covers without authority    | covers only                   | refused          |
| a path that cannot travel   | authority "../x"              | refused          |
| empty covers                | covers []                     | refused          |
| control                     | both, well formed             | accepted (above) |

MUTATIONS (results in the track report): drop the not-covered side; drop
the not-listed side; read the authority from the working tree; drop the
pairing rule; drop the byte bound; keep comment lines; drop the name cap.
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from review import emit
from review.tests._real_cli import Scratch, git, loupe

TITLE = "every disposition ingress checks its author"
AUTHORITY = "inventory/doors.txt"


class _Round(unittest.TestCase):

    def scratch(self, files: dict[str, bytes] | None = None,
                symlinks: dict[str, str] | None = None,
                ignored: dict[str, bytes] | None = None) -> Scratch:
        s = Scratch(self, "objective-authority-")
        for rel, data in (files or {}).items():
            path = s.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            git(s.repo, "add", rel)
        for rel, target in (symlinks or {}).items():
            path = s.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, path)
            git(s.repo, "add", rel)
        if ignored:
            (s.repo / ".gitignore").write_text(
                "".join(f"{rel}\n" for rel in ignored), encoding="utf-8")
            git(s.repo, "add", ".gitignore")
            for rel, data in ignored.items():
                path = s.repo / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        git(s.repo, "commit", "-q", "--allow-empty", "-m", "the inventory")
        return s

    def claim(self, s, objective: dict) -> Path:
        path = s.root / "claim.json"
        path.write_text(json.dumps({
            "objective": "an objective compared with its authority",
            "references": [{"path": "review.toml", "required": True}],
            "objectives": [objective]}), encoding="utf-8")
        return path

    def handoff(self, s, objective: dict):
        return loupe(s, "handoff", "--claim-file",
                     str(self.claim(s, objective)), "--base", s.base)

    def request(self, s, objective: dict) -> tuple[str, dict]:
        code, rec, out, err = self.handoff(s, objective)
        self.assertEqual(code, 0, (out, err))
        return Path(rec["kept"]).read_text(encoding="utf-8"), rec


def objective(covers=None, authority=AUTHORITY, **extra):
    obj = {"title": TITLE, "paths": ["f.txt"], **extra}
    if authority is not None:
        obj["authority"] = authority
    if covers is not None:
        obj["covers"] = covers
    return obj


def lines(*members: str) -> bytes:
    return "".join(f"{m}\n" for m in members).encode("utf-8")


class TestTheComparison(_Round):

    def authority_line(self, text: str) -> str:
        (line,) = [l for l in text.splitlines()
                   if l.startswith(f"- {TITLE}: `{AUTHORITY}`")]
        return line

    def notices(self, text: str) -> list[str]:
        return [l for l in text.splitlines()
                if l.startswith("Notice — objective")]

    def test_complete(self):
        s = self.scratch({AUTHORITY: lines("a", "b", "c")})
        text, _ = self.request(s, objective(["a", "b", "c"]))
        line = self.authority_line(text)
        self.assertIn("3 members", line)
        self.assertIn("covers 3; every member it lists is covered, and "
                      "every member covered is listed", line)
        self.assertEqual(self.notices(text), [])

    def test_incomplete_the_recorded_case(self):
        s = self.scratch({AUTHORITY: lines("a", "b", "c", "d")})
        text, rec = self.request(s, objective(["a", "b", "c"]))
        self.assertIn("listed and not covered: `d`",
                      self.authority_line(text))
        self.assertIn(f"Notice — objective authority member(s) the objective "
                      f"does not cover (1): `d` ({TITLE})",
                      self.notices(text))
        # The précis carries the claim's notices to the person relaying.
        self.assertIn("Claim — objective authority member(s) the objective "
                      "does not cover (1)", rec["brief"])

    def test_over_claim(self):
        s = self.scratch({AUTHORITY: lines("a", "b")})
        text, _ = self.request(s, objective(["a", "b", "x"]))
        self.assertIn("covered and not listed: `x`", self.authority_line(text))
        self.assertIn(f"Notice — objective member(s) covered that the named "
                      f"authority does not list (1): `x` ({TITLE})",
                      self.notices(text))

    def test_comments_and_blanks_are_not_members(self):
        s = self.scratch({AUTHORITY: b"# the doors\n\n   a   \n"})
        text, _ = self.request(s, objective(["a"]))
        self.assertIn("1 members", self.authority_line(text))
        self.assertEqual(self.notices(text), [])

    def test_many_missing_are_counted_and_capped(self):
        others = [f"m{i:02d}" for i in range(25)]
        s = self.scratch({AUTHORITY: lines("a", *others)})
        text, _ = self.request(s, objective(["a"]))
        shown = emit.AUTHORITY_NAMES_SHOWN
        line = self.authority_line(text)
        for name in others[:shown]:
            self.assertIn(f"`{name}`", line)
        self.assertNotIn(f"`{others[shown]}`", line)
        self.assertIn(f"and {len(others) - shown} more", line)
        (notice,) = [n for n in self.notices(text) if "does not cover" in n]
        self.assertIn(f"does not cover ({len(others)}):", notice)

    def assert_unreadable(self, text: str, why: str):
        self.assertIn("cannot be read at the target", self.authority_line(text))
        self.assertIn(why, self.authority_line(text))
        self.assertIn(f"Notice — objective authority that cannot be read at "
                      f"the target (1): `{AUTHORITY}` ({TITLE})",
                      self.notices(text))

    def test_a_file_only_in_the_work_tree_is_not_the_targets(self):
        s = self.scratch(ignored={AUTHORITY: lines("a")})
        text, _ = self.request(s, objective(["a"]))
        self.assert_unreadable(text, "not tracked")

    def test_a_symlink(self):
        s = self.scratch({"inventory/real.txt": lines("a")},
                         symlinks={AUTHORITY: "real.txt"})
        text, _ = self.request(s, objective(["a"]))
        self.assert_unreadable(text, "not a regular file")

    def test_not_utf8(self):
        s = self.scratch({AUTHORITY: b"a\n\xff\n"})
        text, _ = self.request(s, objective(["a"]))
        self.assert_unreadable(text, "not UTF-8")

    def test_over_the_bound(self):
        s = self.scratch({AUTHORITY: b"a\n" + b"#" * emit.AUTHORITY_MAX_BYTES})
        text, _ = self.request(s, objective(["a"]))
        self.assert_unreadable(text, f"{emit.AUTHORITY_MAX_BYTES}-byte bound")

    def test_an_objective_without_one_renders_no_block(self):
        s = self.scratch({AUTHORITY: lines("a")})
        text, _ = self.request(s, objective(authority=None))
        self.assertNotIn("Authority —", text)
        self.assertEqual(self.notices(text), [])


class TestTheGrammar(_Round):

    def refused(self, row: str, obj: dict, says: str):
        with self.subTest(row=row):
            s = self.scratch({AUTHORITY: lines("a")})
            Scratch.settle(s.repo)
            before = (Scratch.snapshot(s.repo),
                      Scratch.snapshot(s.remote, bare=True))
            code, rec, out, err = self.handoff(s, obj)
            self.assertNotIn("Traceback", err)
            self.assertNotEqual(code, 0, (out, err))
            self.assertIn(says, rec["error"])
            self.assertEqual((Scratch.snapshot(s.repo),
                              Scratch.snapshot(s.remote, bare=True)), before)
            self.assertFalse((s.state / "ledger.jsonl").exists())

    def test_each_defect(self):
        self.refused("authority without covers", objective(None),
                     "'covers'")
        self.refused("covers without authority",
                     objective(["a"], authority=None), "'authority'")
        self.refused("a path that cannot travel",
                     objective(["a"], authority="../x"), "'..' segment")
        self.refused("empty covers", objective([]), "empty list")


if __name__ == "__main__":
    unittest.main()

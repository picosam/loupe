"""`take`'s compact default, the reviewer's HEAD, and the split gate banner.

Brief `loupe-tool-feedback-pilot-2026-09` (three debug rounds, 2026-09-17/18):

1. `take` printed the whole request every round, attestation objects
   included — 64% of a measured 29,589-byte request;
2. the useful facts of an attestation are few, and a table carries them;
3. `take` reported the target present and never said what the reviewer's
   working tree WAS;
5. one banner line conflated a non-blocking gate's red with a failure of
   the target.

And, from public issue #4 (a reviewer's request, 2026-09-21): `take
--compact`, pointers only — kept path, digest, byte size, lineage, round,
target SHA, the checkout's state, the précis, the target's `decide` list
(round-1 F5 of the 0.25.0 review) and the next command — with every check
and record of the default take unchanged.

What these tests hold, and what they do not. They hold the RENDERING to the
record: every deviation an attestation can carry is printed, nothing outside
the attestation block changes by a byte, and what cannot be parsed is left
verbatim. They do not re-prove validation: `compact_request` is a view for a
reader, and every check still runs over the original bytes.
"""

import json
import os
import re
import shutil
import string
import subprocess
import unittest
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from review import (TOOL_NAME, brief, cli, config, env_var, transport, vocab,
                    wire)
from review.emit import _git
from review.tests._transport_fixtures import (
    identity_of, run_cli, scratch_loop_repo, sh, write_identity)
from review.tests.util import REPO_ROOT

SHA = "a" * 40
OTHER = "b" * 40


def record(gate="tests", **over):
    rec = {
        "id": gate, "command": f"bin/run {gate}", "exit_code": 0,
        "tool_version": "x", "target_sha": SHA, "executed_sha": SHA,
        "tree": "clean", "binding": "bound", "duration_s": 1.0,
        "blocking": True,
        "output": {"sha256": "c" * 64, "bytes": 10, "pointer": "/p/out.log"},
    }
    rec.update(over)
    return rec


def envelope(records, body=None):
    fence = wire.attestation_fence(TOOL_NAME)
    block = body if body is not None else json.dumps(records, indent=2)
    return (f'<{TOOL_NAME}-review-request sha="{SHA}" round="1">\n'
            f"## Claim\n\nclaim prose | with a pipe\n\n## Evidence\n\n"
            f"before the block\n\n```{fence}\n{block}\n```\n\n"
            f"NOT captured — nothing\n\n## Reference\n\nrefs\n"
            f"</{TOOL_NAME}-review-request>\n")


def rows(text):
    return [l for l in text.splitlines()
            if l.startswith("| ") and not l.startswith("| gate ")]


class TestTheCompactRequest(unittest.TestCase):

    def test_only_the_block_changes_and_every_gate_has_one_row(self):
        """FALSIFICATION. Mutation: return `envelope[:block.start()] + table`
        (dropping the suffix) and the suffix assertion fails; drop a record
        from the row generator and the count fails."""
        recs = [record("tests"), record("lint"), record("stage")]
        env = envelope(recs)
        out = brief.compact_request(env, kept="/kept/request.md")
        fence = "```" + wire.attestation_fence(TOOL_NAME)
        start = env.index(fence)
        end = env.index("\n```\n", start) + len("\n```")
        self.assertTrue(out.startswith(env[:start]),
                        "bytes before the block changed")
        self.assertTrue(out.endswith(env[end:]),
                        "bytes after the block changed")
        self.assertNotIn(fence, out)
        self.assertEqual([r.split(" | ")[0][2:] for r in rows(out)],
                         ["tests", "lint", "stage"])
        self.assertIn("/kept/request.md", out,
                      "the full mode is not named beside the table")
        self.assertLess(len(out), len(env))

    def test_the_clean_row_is_the_control(self):
        """The paired control for the deviation test below: a bound, clean,
        passing row at the request's own sha says so and nothing louder."""
        (row,) = rows(brief.compact_request(envelope([record()])))
        self.assertIn("| exit 0 | bound | clean | = target | yes | "
                      "this tool |", row)
        self.assertIn("sha256:" + "c" * 12, row)
        self.assertIn("| /p/out.log |", row, "the log pointer, whole")

    def test_no_deviation_is_ever_summarised_away(self):
        """FALSIFICATION. Each case is one way an attestation differs from
        the clean control, and each must be legible in its row. Mutation:
        hardcode `ran_at = "= target"` and the three sha cases fail; print
        `rec.get("exit_code", 0)` and the missing-exit case fails."""
        cases = {
            "a failing exit": (record(exit_code=3), "exit 3"),
            "an unbound row": (record(binding="unbound: dirty tree"),
                               "unbound: dirty tree"),
            "a dirty tree": (record(tree="dirty"), "| dirty |"),
            "executed elsewhere": (record(executed_sha=OTHER),
                                   f"executed {OTHER[:12]}"),
            "claims another target": (
                record(target_sha=OTHER, executed_sha=OTHER),
                f"claims {OTHER[:12]}"),
            "non-blocking": (record(blocking=False), "| no |"),
            "attested by ci": (
                record(attested_by="ci",
                       ci_run={"id": 77, "conclusion": "failure"}),
                "ci run 77 (failure)"),
            "not run": ({"id": "tests", "error": "timed out at 600s",
                         "blocking": True, "target_sha": SHA},
                        "NOT RUN: timed out at 600s"),
            "no exit code at all": (
                {k: v for k, v in record().items() if k != "exit_code"},
                "| - | bound"),
        }
        for name, (rec, expected) in cases.items():
            with self.subTest(case=name):
                (row,) = rows(brief.compact_request(envelope([rec])))
                self.assertIn(expected, row)
                self.assertNotIn("= target", row) if name in (
                    "executed elsewhere", "claims another target",
                    "not run") else None

    def test_a_target_that_is_not_the_requests_sha_is_not_equal(self):
        """executed = target is not enough: both must be the REQUEST's sha,
        or a block copied whole from another commit would read `= target`
        in every row."""
        env = envelope([record(target_sha=OTHER, executed_sha=OTHER)])
        (row,) = rows(brief.compact_request(env))
        self.assertNotIn("= target", row)

    def test_what_cannot_be_read_is_left_verbatim(self):
        """FALSIFICATION. Mutation: drop the `isinstance(r, dict)` guard and
        the non-object case raises instead of returning the envelope."""
        unreadable = {
            "not json": envelope(None, body="[ {not json"),
            "not an array": envelope(None, body='{"id": "tests"}'),
            "a record that is not an object": envelope([record(), "tests"]),
            "an empty array": envelope([]),
            "no block at all": "## Evidence\n\nnothing here\n",
        }
        for name, env in unreadable.items():
            with self.subTest(case=name):
                self.assertEqual(brief.compact_request(env, kept="/k"), env)

    def _log_cells(self, out):
        return [r.rstrip(" |").rsplit(" | ", 1)[-1] for r in rows(out)]

    def test_a_shared_log_directory_is_named_once(self):
        """RR3 (2026-10-04): the directory most pointers share is named once
        above the table and each row in it prints `…/` and its file name
        (round-2 F5, 2026-10-05: the prefix tells it from a bare pointer,
        which prints as recorded — `f.log` below). Every
        other class keeps its whole pointer: a second directory, a prefix
        that is not a directory boundary, a deeper directory, a pointer
        naming no directory, and a pointer that is not a string."""
        def at(gate, pointer):
            rec = record(gate)
            rec["output"] = dict(rec["output"], pointer=pointer)
            return rec
        recs = [at("a", "/logs/run/a.log"), at("b", "/logs/run/b.log"),
                at("c", "/other/c.log"), at("d", "/logs/runner/d.log"),
                at("e", "/logs/run/sub/e.log"), at("f", "f.log"),
                at("g", 7)]
        out = brief.compact_request(envelope(recs))
        self.assertIn("Logs: `/logs/run/`", out)
        self.assertEqual(out.count("/logs/run/"), 2,
                         "the directory is named once, plus one deeper row")
        self.assertEqual(self._log_cells(out),
                         ["…/a.log", "…/b.log", "/other/c.log",
                          "/logs/runner/d.log", "/logs/run/sub/e.log",
                          "f.log", "7"])

    def test_a_directory_one_row_names_is_not_hoisted(self):
        """The control for the shared directory: one row per directory
        saves nothing by naming it once, so every pointer stays whole."""
        def at(gate, pointer):
            rec = record(gate)
            rec["output"] = dict(rec["output"], pointer=pointer)
            return rec
        out = brief.compact_request(envelope(
            [at("a", "/x/a.log"), at("b", "/y/b.log")]))
        self.assertNotIn("Logs:", out)
        self.assertEqual(self._log_cells(out), ["/x/a.log", "/y/b.log"])

    def test_a_tie_goes_to_the_directory_that_appears_first(self):
        def at(gate, pointer):
            rec = record(gate)
            rec["output"] = dict(rec["output"], pointer=pointer)
            return rec
        out = brief.compact_request(envelope(
            [at("a", "/y/a.log"), at("b", "/x/b.log"), at("c", "/y/c.log"),
             at("d", "/x/d.log")]))
        self.assertIn("Logs: `/y/`", out)
        self.assertEqual(self._log_cells(out),
                         ["…/a.log", "/x/b.log", "…/c.log", "/x/d.log"])

    def test_a_pipe_in_a_command_cannot_forge_a_column(self):
        rec = record(command="run | tee log")
        (row,) = rows(brief.compact_request(envelope([rec])))
        self.assertIn("run \\| tee log", row)
        self.assertEqual(row.replace("\\|", "").count("|"), 11)


#: The `Logs:` rule as `compact_request` prints it since round-2 F5, and as
#: it printed it before (the mutation row reads the old one by its own
#: rule, so the oracle is never what turns the mutation red).
_RULE = re.compile(
    r"^Logs: `(?P<dir>[^`]*)` — a `log` cell beginning `(?P<mark>[^`]+)` is "
    r"in this directory: replace `(?P=mark)` with this path\. Every other "
    r"`log` cell is the pointer as recorded\.$", re.M)
_ENCODED_RULE = re.compile(
    r"^A `log` cell beginning `(?P<mark>[^`]+)` is its whole pointer "
    r"percent-encoded \(RFC 3986, UTF-8\): decode what follows "
    r"`(?P=mark)`\.", re.M)
_OLD_RULE = re.compile(
    r"^Logs: `(?P<dir>[^`]*)/` — a `log` cell holding a file name alone is "
    r"in this directory; any other prints its whole path\.$", re.M)


def reconstruct(view: str) -> list:
    """Every row's log pointer, rebuilt from the printed table and the
    printed rule alone: `None` where the cell says the field is absent."""
    new, old = _RULE.search(view), _OLD_RULE.search(view)
    encoded = _ENCODED_RULE.search(view)
    errors = ("surrogatepass" if "decode with surrogates passed" in view
              else "strict")
    if "\nLogs:" in view:
        assert new or old, "a Logs line that states no known rule"
    out = []
    lines = view.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith(
        "| gate | result |")) + 2
    table = []
    for line in lines[start:]:
        if not line.startswith("| "):
            break
        table.append(line)
    for row in table:
        cell = row[2:-2].split(" | ")[-1]
        if encoded and cell.startswith(encoded["mark"]):
            out.append(urllib.parse.unquote(cell[len(encoded["mark"]):],
                                            errors=errors))
            continue
        if cell == "-":
            out.append(None)
            continue
        # A cell's `\|` is its pipe; the directory is in a code span,
        # where it is not.
        cell = cell.replace("\\|", "|")
        if new and cell.startswith(new["mark"]):
            cell = new["dir"] + cell[len(new["mark"]):]
        elif old and "/" not in cell:
            cell = old["dir"] + "/" + cell
        out.append(cell)
    return out


class TestEveryLogCellReconstructsItsPointer(unittest.TestCase):
    """Round-2 F5 of the final review (2026-10-05): a bare `c.log` printed
    beside two hoisted file names read, by the printed rule, as
    `<dir>/c.log`. A shortened cell now prints `…/<name>`, every other cell
    the pointer verbatim, and the rule line says exactly that.

    THE PARTITION of a request's pointers (class · Logs line):
      R1  absolute, all in one directory           · hoisted, named once
      R2  relative, all in one directory           · hoisted, named once
      R3  `./`-relative                            · hoisted (`.`)
      R4  bare relative names only                 · none
      R5  one directory each                       · none
      R6  a shared directory and a bare name (the reviewer's case)
                                                   · hoisted, named once
      R7  shared, another directory, a deeper one, a non-boundary prefix,
          a bare name                              · hoisted
      R8  shared and an empty pointer              · hoisted
      R9  shared, a record with no pointer, one with no output
                                                   · hoisted
      R10 shared and the directory itself as a pointer · hoisted
      R11 shared and the directory with a trailing slash · hoisted
      R12 two pointers ending in a slash, one directory each · none
      R13 shared and an unshortened pointer beginning `…/` · NONE: it would
          read as shortened, so every pointer prints whole
      R14 a directory named `…`                    · none (not ASCII)
      R15 a `|` in a file name                     · hoisted
      R16 a doubled slash                          · hoisted (`/logs/`)
    Each row: the pointers rebuilt from the table and rule equal the
    record's, and the Logs line is present exactly where expected.
    """

    ROWS = {
        "R1": (["/logs/run/a.log", "/logs/run/b.log", "/logs/run/c.log"],
               "/logs/run"),
        "R2": (["logs/a.log", "logs/b.log"], "logs"),
        "R3": (["./a.log", "./b.log"], "."),
        "R4": (["a.log", "b.log"], None),
        "R5": (["/x/a.log", "/y/b.log"], None),
        "R6": (["/logs/run/a.log", "/logs/run/b.log", "c.log"], "/logs/run"),
        "R7": (["/logs/run/a.log", "/logs/run/b.log", "/other/c.log",
                "/logs/runner/d.log", "/logs/run/sub/e.log", "f.log"],
               "/logs/run"),
        "R8": (["/logs/run/a.log", "/logs/run/b.log", ""], "/logs/run"),
        "R9": (["/logs/run/a.log", "/logs/run/b.log", "NO POINTER",
                "NO OUTPUT"], "/logs/run"),
        "R10": (["/logs/run/a.log", "/logs/run/b.log", "/logs/run"],
                "/logs/run"),
        "R11": (["/logs/run/a.log", "/logs/run/b.log", "/logs/run/"],
                "/logs/run"),
        "R12": (["/d/a/", "/d/b/"], None),
        "R13": (["/logs/run/a.log", "/logs/run/b.log", "…/c.log"], None),
        # Round-1 F2 of the 0.29.0 review: a hoisted directory is ASCII.
        "R14": (["…/a.log", "…/b.log"], None),
        "R15": (["/logs/run/a|b.log", "/logs/run/c.log"], "/logs/run"),
        "R16": (["/logs//a.log", "/logs//b.log"], "/logs/"),
        # Round-3 F2: what a cell cannot print as recorded is encoded.
        "R17": (["/logs/run/a.log", "/logs/run/b.log", "/logs/run/a  b.log"],
                "/logs/run"),
        "R18": (["/logs/run/a.log", "/logs/run/b.log", "/logs/run/c\td.log"],
                "/logs/run"),
        "R19": (["/logs/run/a.log", "/logs/run/b.log", "/logs/run/c\nd.log"],
                "/logs/run"),
        "R20": (["/logs/run/a.log", "/logs/run/b.log", "/logs/run/c\rd.log"],
                "/logs/run"),
        "R21": (["/logs/run/a.log", "/logs/run/b.log", "/logs/run/ e.log"],
                "/logs/run"),
        "R22": (["/logs/run/a.log", "/logs/run/b.log", "/logs/run/a.log "],
                "/logs/run"),
        "R23": (["/logs/my\nrun/x.log", "/logs/my\nrun/y.log"], None),
        "R24": (["/logs/a`b/x.log", "/logs/a`b/y.log"], None),
        "R25": (["a  b.log", " lead.log", "c.log"], None),
        "R26": (["-", "/x/a.log"], None),
        "R27": (["%:x.log", "%41.log"], None),
        "R28": (["/logs/run/a\\|b.log", "/logs/run/c%20d.log",
                 "/logs/run/é.log"], "/logs/run"),
        "R29": (["/logs/my  run/a.log", "/logs/my  run/b.log"], None),
    }
    #: Rows whose table must carry the encoded rule line, and rows that
    #: must not.
    ENCODED = ("R13", "R14", "R15", "R17", "R18", "R19", "R20", "R21", "R22",
               "R23", "R24", "R25", "R26", "R27", "R28", "R29")
    #: Rows whose directory is long enough to count: named exactly once.
    SAVING = ("R1", "R2", "R6")

    @staticmethod
    def records(pointers):
        out = []
        for i, pointer in enumerate(pointers):
            rec = record(f"g{i}")
            if pointer == "NO OUTPUT":
                del rec["output"]
            elif pointer == "NO POINTER":
                del rec["output"]["pointer"]
            else:
                rec["output"] = dict(rec["output"], pointer=pointer)
            out.append(rec)
        return out

    @staticmethod
    def originals(pointers):
        return [None if p in ("NO OUTPUT", "NO POINTER") else p
                for p in pointers]

    def check(self, name, view):
        pointers, directory = self.ROWS[name]
        self.assertEqual(reconstruct(view), self.originals(pointers), view)
        if directory is None:
            self.assertNotIn("\nLogs:", view)
        else:
            self.assertIn(f"\nLogs: `{directory}/` ", view)
        if name in self.SAVING:
            self.assertEqual(view.count(directory + "/"), 1, view)
        self.assertEqual(bool(_ENCODED_RULE.search(view)),
                         name in self.ENCODED, view)

    def test_every_row_reconstructs_its_pointers(self):
        for name, (pointers, _d) in self.ROWS.items():
            with self.subTest(row=name):
                self.check(name, brief.compact_request(
                    envelope(self.records(pointers))))

    def test_the_distinction_mutated_away_mislocates_a_bare_pointer(self):
        """The mutation, kept in the suite: a copy of the package whose
        renderer prints the bare file name under the old rule line, run
        with `python -B` in a child. R6 (the reviewer's case) goes red, and
        R1, whose pointers all lie in the directory, stays green."""
        import shutil
        import sys
        import tempfile
        package = Path(brief.__file__).resolve().parent
        with tempfile.TemporaryDirectory(prefix="f5-mutation-") as tmp:
            copy = Path(tmp) / package.name
            shutil.copytree(package, copy, ignore=shutil.ignore_patterns(
                "__pycache__", "tests"))
            target = copy / "brief.py"
            text = target.read_text(encoding="utf-8")
            for old, new in (
                    ("        return shown if name is None else _HOISTED "
                     "+ name\n",
                     "        return shown\n"),
                    ('''    logs_line = ([f"Logs: `{log_dir}/` — a `log` cell beginning `{_HOISTED}` "
                  f"is in this directory: replace `{_HOISTED}` with this "
                  f"path. Every other `log` cell is the pointer as "
                  f"recorded."] if log_dir is not None else [])
''', '''    logs_line = ([f"Logs: `{log_dir}/` — a `log` cell holding a file name "
                  f"alone is in this directory; any other prints its whole "
                  f"path."] if log_dir is not None else [])
''')):
                self.assertEqual(text.count(old), 1, old)
                text = text.replace(old, new)
            target.write_text(text, encoding="utf-8")
            for name, red in (("R6", True), ("R1", False)):
                with self.subTest(row=name, red=red):
                    env_text = envelope(self.records(self.ROWS[name][0]))
                    proc = subprocess.run(
                        [sys.executable, "-B", "-c",
                         f"import sys; from {package.name} import brief; "
                         "sys.stdout.write(brief.compact_request("
                         "sys.stdin.read()))"],
                        input=env_text, capture_output=True, text=True,
                        cwd=tmp, env={**os.environ, "PYTHONPATH": tmp},
                        timeout=60)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    if red:
                        with self.assertRaises(AssertionError):
                            self.check(name, proc.stdout)
                    else:
                        self.check(name, proc.stdout)


    def mutated(self, tmp, edits):
        """A copy of the package under `tmp` with `edits` applied, each
        anchor found exactly once."""
        import shutil
        package = Path(brief.__file__).resolve().parent
        copy = Path(tmp) / package.name
        shutil.copytree(package, copy, ignore=shutil.ignore_patterns(
            "__pycache__", "tests"))
        target = copy / "brief.py"
        text = target.read_text(encoding="utf-8")
        for old, new in edits:
            self.assertEqual(text.count(old), 1, old)
            text = text.replace(old, new)
        target.write_text(text, encoding="utf-8")
        return package.name

    def run_copy(self, tmp, package, name):
        import sys
        proc = subprocess.run(
            [sys.executable, "-B", "-c",
             f"import sys; from {package} import brief; "
             "sys.stdout.write(brief.compact_request(sys.stdin.read()))"],
            input=envelope(self.records(self.ROWS[name][0])),
            capture_output=True, text=True, cwd=tmp,
            env={**os.environ, "PYTHONPATH": tmp}, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_the_encoding_mutated_away_loses_each_whitespace_row(self):
        """Round-3 F2's mutation, kept in the suite: `_log_cell` printing
        every pointer as it is (the encoding off) sends each row whose
        pointer a cell cannot print red, each on its own; R1 and R2, whose
        pointers print as recorded, stay green. (This oracle reads the
        source; `TestEveryLogCellSurvivesRendering` reads it rendered.)"""
        import tempfile
        with tempfile.TemporaryDirectory(prefix="f2-mutation-") as tmp:
            package = self.mutated(tmp, [("    if _verbatim(shown):\n",
                                          "    if True:\n")])
            for name in (*self.ENCODED, "R1", "R2"):
                with self.subTest(row=name):
                    view = self.run_copy(tmp, package, name)
                    if name in self.ENCODED:
                        with self.assertRaises(AssertionError):
                            self.check(name, view)
                    else:
                        self.check(name, view)

    def test_the_directory_guard_mutated_breaks_the_logs_line(self):
        """A directory a code span cannot carry (a newline, a backtick)
        hoisted anyway: R23 and R24 no longer read back."""
        import tempfile
        with tempfile.TemporaryDirectory(prefix="f2-dir-mutation-") as tmp:
            package = self.mutated(tmp, [
                ("    if not _spans_verbatim(best):\n",
                 "    if False:\n")])
            for name in ("R23", "R24"):
                with self.subTest(row=name):
                    with self.assertRaises(AssertionError):
                        self.check(name, self.run_copy(tmp, package, name))

#: A `log` cell as `compact_request` may print it: the absent field, a
#: hoisted name, a pointer as recorded, or the encoded form. Every
#: character outside the prefixes is in `[A-Za-z0-9./-]`, which no GFM
#: inline construct reads (round-1 F2 of the 0.29.0 review).
_CELL = re.compile(r"\A(?:-|…/[A-Za-z0-9./-]*|[A-Za-z0-9./-]*"
                   r"|%:(?:[A-Za-z0-9./-]|%[0-9A-F]{2})*)\Z")


def _code_span_safe(directory: str) -> bool:
    """The test's own statement of a directory a code span carries as it
    is, written from CommonMark §6.1 (a backtick closes the span, a line
    ending becomes a space, a space at both ends is stripped), §2.3
    (U+0000 is replaced) and pandoc's NFC normalisation, not read from
    `brief`: printable ASCII but the backtick, single interior spaces."""
    return (directory != "" and "`" not in directory
            and all(0x20 <= ord(c) <= 0x7E for c in directory)
            and not directory.startswith(" ") and not directory.endswith(" ")
            and "  " not in directory)


def structural(view: str) -> list:
    """The always-running oracle: every `log` cell matches `_CELL`, a
    hoisted directory is one a code span carries, and the printed rule
    rebuilds every pointer from the source."""
    lines = view.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith(
        "| gate | result |")) + 2
    for line in lines[start:]:
        if not line.startswith("| "):
            break
        cell = line[2:-2].split(" | ")[-1]
        assert _CELL.match(cell), f"a cell outside the inert grammar: {cell!r}"
    logs = _RULE.search(view)
    if logs:
        assert _code_span_safe(logs["dir"][:-1]), (
            f"a hoisted directory a code span changes: {logs['dir']!r}")
    return reconstruct(view)


class _RenderedView(HTMLParser):
    """What a reader of the rendered page sees: each paragraph's text with
    its code spans marked `\\x02…\\x03` (neither is printable, so neither
    is in a hoisted directory), and each table row's cells."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paragraphs, self.rows = [], []
        self._p = self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "p":
            self._p = ""
        elif tag == "code" and self._p is not None:
            self._p += "\x02"
        elif tag == "tr":
            self.rows.append([])
        elif tag == "td":
            self._cell = ""

    def handle_endtag(self, tag):
        if tag == "p" and self._p is not None:
            self.paragraphs.append(self._p)
            self._p = None
        elif tag == "code" and self._p is not None:
            self._p += "\x03"
        elif tag == "td" and self._cell is not None:
            self.rows[-1].append(self._cell)
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell += data
        elif self._p is not None:
            self._p += data


_RENDERED_LOGS = re.compile(
    "Logs: \x02(?P<dir>[^\x03]*)/\x03 — a \x02log\x03 cell beginning "
    "\x02(?P<mark>[^\x03]+)\x03 is in this directory: replace "
    "\x02(?P=mark)\x03 with this path\\. Every other \x02log\x03 cell is "
    "the pointer as recorded\\.")
_RENDERED_ENCODED = re.compile(
    "A \x02log\x03 cell beginning \x02(?P<mark>[^\x03]+)\x03 is its whole "
    "pointer percent-encoded \\(RFC 3986, UTF-8\\): decode what follows "
    "\x02(?P=mark)\x03\\.")


def rendered(view: str) -> tuple:
    """`view` through `pandoc -f gfm`, read back by the rule as it renders:
    `(the hoisted directory or None, the pointers)`."""
    # `--wrap=none`: the HTML writer's own line wrapping is layout, not
    # Markdown, and a browser shows it as the space it replaced.
    html = subprocess.run(["pandoc", "-f", "gfm", "-t", "html",
                           "--wrap=none"], input=view, text=True,
                          capture_output=True, check=True, timeout=60).stdout
    page = _RenderedView()
    page.feed(html)
    text = "\n".join(page.paragraphs)
    logs, encoded = _RENDERED_LOGS.search(text), _RENDERED_ENCODED.search(text)
    errors = ("surrogatepass" if "decode with surrogates passed" in text
              else "strict")
    out = []
    for row in (r for r in page.rows if r):
        cell = row[-1]
        if encoded and cell.startswith(encoded["mark"]):
            out.append(urllib.parse.unquote(cell[len(encoded["mark"]):],
                                            errors=errors))
        elif cell == "-":
            out.append(None)
        elif logs and cell.startswith(logs["mark"]):
            out.append(logs["dir"] + "/" + cell[len(logs["mark"]):])
        else:
            out.append(cell)
    return (logs["dir"] if logs else None), out


_PUNCTUATION = [c for c in string.punctuation if c not in "./-"]
_VERDICT_CONTROLS = ["plain.log", "a  b.log", "-", "%:literal.log"]
_VERDICT_TOKENS = [
    r"a\*b.log", r"a\|b.log", "`x`.log", "*x*.log", "**x**.log",
    "~~x~~.log", "<b>x</b>.log", "a<!--x-->b.log", "a&amp;b.log",
    "a&#65;b.log", "[x](target).log", "![x](target).log",
    "<https://example.test/x>", "~~x~~  y.log", "_x_  y.log",
    "__x__  y.log"]
_PUNCT_DIR = (r"/h/j_d/~x~/*a*/a\|b/&amp;/&#65;/<b>/[x](y)/$m$/:smile:"
              r"/a@b.c/#1/<!--c-->/!x/^y/{z}/'q'/" '"w"')
_REJECTED_DIRS = {
    "backtick": "/a`b", "lf": "/a\nb", "cr": "/a\rb", "tab": "/a\tb",
    "run": "/a  b", "lead": " /a", "trail": "/a ", "nul": "/a\x00b",
    "soh": "/a\x01b", "del": "/a\x7fb", "nbsp": "/a\xa0b",
    "u2028": "/a\u2028b", "surrogate": "/a\udcffb",
    "nfd": "/e\u0301/x", "nfc": "/é/x", "cjk": "/日本", "astral": "/\U0001f600"}


class TestEveryLogCellSurvivesRendering(unittest.TestCase):
    """Round-1 F2 of the 0.29.0 review (2026-10-05): the encoded branch
    kept `_` and `~` (`urllib.parse.quote`'s unreserved set) and the plain
    branch admitted any Markdown, so `~~x~~  y.log` rendered as `x  y.log`
    and sixteen pointers lost characters once the table was READ, which
    the source-level oracle above never did. A cell now holds only
    `[A-Za-z0-9./-]` after its prefix, and the hoisted directory sits in a
    code span that carries it literally, or is not hoisted.

    THE PARTITION of the admitted pointer domain (a JSON string, which
    `A-OUTPUT` admits non-empty; the empty string and an absent pointer are
    rendered too). Each case is one request with an inert control row, and
    states the directory it must hoist (none unless named):
      inert          only [A-Za-z0-9./-], `www.` and dash runs included
      punct-single   every ASCII punctuation mark but `./-`, in a name
      punct-delim    each as a delimiter pair, doubled, and alone
      whitespace     space, runs, tab, LF, CR, CRLF, leading, trailing,
                     NBSP, U+2028, VT, FF, U+3000, U+200B
      control        NUL, SOH, ESC, DEL, NEL
      unicode        composed and decomposed é, CJK, astral, `…`, BOM
      prefix         `-`, `%`, `%:`, `%:x`, `%41`, `%2F`, `…/c.log`, `…`,
                     an interior `%:`
      verdict        the review's sixteen failing pointers, four controls
      combined       Markdown with whitespace, math, emoji, mail, bare
                     URL, entity, escape and link, one pointer each
      surrogate      a lone surrogate (JSON admits one)
      h-inert        a hoisted inert directory, every name class under it,
                     and pointers outside it              · hoisted
      h-punct        a directory full of Markdown, in a code span · hoisted
      h-space        a directory with one interior space  · hoisted
      h-encoded, h-dash, h-dot, h-slashes: the directories `%:`, `-`,
                     `.`, `/logs/`                         · hoisted
      h-ellipsis     the directory `…` (the hoisted prefix; not ASCII)
                                                  · NOT hoisted, encoded
      h-rejected-*   a directory a code span or a renderer might change:
                     a backtick, LF, CR, tab, a space run, a leading or
                     trailing space, NUL, SOH, DEL, NBSP, U+2028, a lone
                     surrogate, decomposed (pandoc composes it) and
                     composed é, CJK, astral   · NOT hoisted, encoded
    The structural test runs everywhere; the rendered test runs where
    `pandoc` is (the author's Mac, not the CI runner) and skips saying so.
    """

    CASES = {
        "inert": (["plain.log", "/logs/x/a.log", "www.example.com",
                   "--a---b--.log", "1.log", "...", "/", "", "a.b-c/d",
                   "NO POINTER"], None),
        "punct-single": (["plain.log",
                          *(f"a{c}b.log" for c in _PUNCTUATION)], None),
        "punct-delim": (["plain.log", *(p for c in _PUNCTUATION for p in (
            f"{c}x{c}.log", f"{c}{c}x{c}{c}.log", c))], None),
        "whitespace": (["plain.log", " ", "a b.log", "a  b.log", "a\tb",
                        "a\nb", "a\rb", "a\r\nb", " lead.log", "trail.log ",
                        "a\xa0b", "a\u2028b", "a\x0bb", "a\x0cb", "a\u3000b",
                        "a\u200bb"], None),
        "control": (["plain.log", "a\x00b", "a\x01b", "a\x1bb", "a\x7fb",
                     "a\x85b"], None),
        "unicode": (["plain.log", "é.log", "e\u0301.log", "日本.log",
                     "\U0001f600.log", "a…b", "\ufeffa"], None),
        "prefix": (["plain.log", "-", "%", "%:", "%:x.log", "%41.log", "%2F",
                    "…/c.log", "…", "x%:y"], None),
        "verdict": ([*_VERDICT_CONTROLS, *_VERDICT_TOKENS], None),
        "combined": (["plain.log", "~~x~~  y.log", "_x_  y.log",
                      "a_b c~d\te.log", "$x$ y.log", ":smile: x.log",
                      "a@b.c d", "https://example.test/x y",
                      "&#x41; \\` [x]", "<b>_x_</b>\n**y**", "a\\\\|b",
                      "www.x.com/_y_"], None),
        "surrogate": (["plain.log", "a\udcffb.log", "\ud800"], None),
        "h-inert": (["/logs/run/a.log", "/logs/run/b.log",
                     "/logs/run/_x_.log", "/logs/run/a  b.log",
                     "/logs/run/-", "/logs/run/%:x", "/logs/run/",
                     "/logs/run/sub/c.log", "c.log", "/other/~~x~~.log",
                     "-", "plain.log", "NO OUTPUT"], "/logs/run"),
        "h-punct": ([f"{_PUNCT_DIR}/a.log", f"{_PUNCT_DIR}/b.log",
                     f"{_PUNCT_DIR}/_c_.log", "plain.log"], _PUNCT_DIR),
        "h-space": (["/my run/a.log", "/my run/b.log", "plain.log"],
                    "/my run"),
        "h-ellipsis": (["…/a.log", "…/b.log", "plain.log"], None),
        "h-encoded": (["%:/a.log", "%:/b.log", "plain.log"], "%:"),
        "h-dash": (["-/a.log", "-/b.log", "plain.log"], "-"),
        "h-dot": (["./a.log", "./b.log", "plain.log"], "."),
        "h-slashes": (["/logs//a.log", "/logs//b.log", "plain.log"],
                      "/logs/"),
        **{f"h-rejected-{k}": ([f"{d}/x.log", f"{d}/y.log", "plain.log"],
                               None)
           for k, d in _REJECTED_DIRS.items()},
    }

    @staticmethod
    def view(pointers):
        return brief.compact_request(envelope(
            TestEveryLogCellReconstructsItsPointer.records(pointers)))

    @staticmethod
    def originals(pointers):
        return TestEveryLogCellReconstructsItsPointer.originals(pointers)

    def red(self, oracle, names=None):
        """The cases `oracle` does not read back exactly (pointers and
        hoisted directory), by name."""
        out = []
        for name, (pointers, directory) in self.CASES.items():
            if names is not None and name not in names:
                continue
            try:
                view = self.view(pointers)
                if oracle is rendered:
                    ok = rendered(view) == (directory,
                                            self.originals(pointers))
                else:
                    logs = _RULE.search(view)
                    ok = (structural(view) == self.originals(pointers)
                          and (logs["dir"][:-1] if logs else None)
                          == directory)
            except (AssertionError, UnicodeDecodeError, ValueError):
                ok = False
            if not ok:
                out.append(name)
        return out

    def require_pandoc(self):
        if shutil.which("pandoc") is None:
            self.skipTest("pandoc is not on PATH (it is on the author's Mac, "
                          "not on the CI runner); the structural test still "
                          "holds every cell to the inert grammar")

    def test_every_case_matches_the_grammar_and_reads_back(self):
        for name, (pointers, directory) in self.CASES.items():
            with self.subTest(case=name):
                view = self.view(pointers)
                self.assertEqual(structural(view), self.originals(pointers),
                                 view)
                logs = _RULE.search(view)
                self.assertEqual(logs["dir"][:-1] if logs else None,
                                 directory, view)

    def test_every_case_reads_back_once_rendered(self):
        self.require_pandoc()
        for name, (pointers, directory) in self.CASES.items():
            with self.subTest(case=name):
                view = self.view(pointers)
                self.assertEqual(rendered(view),
                                 (directory, self.originals(pointers)), view)

    #: One mutation per guard: the attribute patched, its replacement (a
    #: callable taking the attribute's own value is called with it, so
    #: this module imports against a `brief` without the guard), the cases
    #: red once rendered, the cases red in the source, and a case that
    #: stays green under both.
    MUTATIONS = {
        "the cell guard off (the review's own mutation)": (
            "_verbatim", lambda guard: lambda text: True,
            ("whitespace", "verdict", "combined"),
            ("whitespace", "verdict", "combined"), "inert"),
        "the round's encoder (`quote`, `_` and `~` kept)": (
            "_percent_encoded",
            lambda encode: lambda text: urllib.parse.quote(text, safe="/"),
            ("combined", "verdict"), ("combined", "verdict"), "inert"),
        "`_` and `~` admitted as inert": (
            "_INERT", lambda inert: inert | {"_", "~"},
            ("punct-delim", "verdict"), ("punct-delim", "verdict"),
            "inert"),
        "the `-` exclusion dropped": (
            "_verbatim",
            lambda guard: lambda text: all(c in brief._INERT for c in text),
            ("prefix",), ("prefix",), "inert"),
        "the directory guard off": (
            "_spans_verbatim", lambda guard: lambda d: True,
            ("h-rejected-backtick", "h-rejected-lf", "h-rejected-nul",
             "h-rejected-nfd"),
            ("h-rejected-backtick", "h-rejected-lf", "h-rejected-nul",
             "h-rejected-nfd"), "h-punct"),
        "the directory guard without its ASCII bound": (
            "_spans_verbatim", lambda guard: (
                lambda d: "`" not in d and d == d.strip(" ")
                and "  " not in d),
            ("h-rejected-lf", "h-rejected-nul", "h-rejected-nfd"),
            ("h-rejected-lf", "h-rejected-nul", "h-rejected-nfd"),
            "h-punct"),
        "the surrogate rule unprinted": (
            "_SURROGATE_RULE", lambda rule: "",
            ("surrogate",), ("surrogate",), "inert"),
    }

    def check_mutations(self, oracle):
        for label, (attr, value, reds_rendered, reds_source,
                    green) in self.MUTATIONS.items():
            reds = reds_rendered if oracle is rendered else reds_source
            with self.subTest(mutation=label):
                with mock.patch.object(brief, attr,
                                       value(getattr(brief, attr))):
                    seen = self.red(oracle, {*reds, green})
                self.assertEqual(sorted(seen), sorted(reds), label)
        self.assertEqual(self.red(oracle), [], "every guard restored")

    def test_each_guard_mutated_turns_its_cases_red_in_the_source(self):
        self.check_mutations(structural)

    def test_each_guard_mutated_turns_its_cases_red_rendered(self):
        self.require_pandoc()
        self.check_mutations(rendered)


class TestTheGateBanner(unittest.TestCase):

    def banners(self, *recs):
        return "\n".join(brief._gate_banners(list(recs)))

    def test_all_green_prints_nothing(self):
        self.assertEqual(self.banners(record(), record("lint")), "")

    def test_a_nonblocking_red_is_advisory_and_says_the_target_passed(self):
        """FALSIFICATION — the round-3 case itself. Mutation: restore the
        single `failed` list and this reads as a target failure again."""
        text = self.banners(record(), record("ci-evidence", exit_code=1,
                                             blocking=False))
        self.assertIn("Advisory gates", text)
        self.assertIn("ci-evidence", text)
        self.assertIn("every blocking gate passed at the target", text)
        self.assertNotIn("AT THE TARGET", text)

    def test_a_blocking_red_is_the_targets_and_is_never_advisory(self):
        text = self.banners(record(exit_code=1), record("lint"))
        self.assertIn("1 of 2 BLOCKING gate(s) did not pass AT THE TARGET: "
                      "tests", text)
        self.assertNotIn("Advisory", text)

    def test_both_at_once_are_two_lines_and_the_advisory_defers(self):
        text = self.banners(record(exit_code=1),
                            record("ci-evidence", exit_code=1,
                                   blocking=False))
        self.assertIn("AT THE TARGET: tests", text)
        self.assertIn("see the blocking failures above", text)
        self.assertNotIn("every blocking gate passed", text)

    def test_a_row_without_a_blocking_flag_is_not_advisory(self):
        """Absent is unknown, and unknown is never the quieter reading."""
        rec = {k: v for k, v in record(exit_code=1).items()
               if k != "blocking"}
        self.assertIn("AT THE TARGET", self.banners(rec))
        self.assertIn("AT THE TARGET", self.banners("not-an-object"))

    def test_the_precis_uses_it(self):
        env = envelope([record(), record("ci-evidence", exit_code=1,
                                         blocking=False)])
        text = brief.request_precis(wire.parse_request(env))
        self.assertIn("Advisory gates", text)
        self.assertNotIn("AT THE TARGET", text)


class TestTheCheckoutLine(unittest.TestCase):

    def test_all_three_states_print_and_name_their_shas(self):
        at = cli.render_checkout({"state": transport.CHECKOUT_AT_TARGET,
                                  "sha": SHA, "tree": "dirty"}, SHA)
        self.assertIn("AT the target", at)
        self.assertIn("dirty", at)
        away = cli.render_checkout({"state": transport.CHECKOUT_ELSEWHERE,
                                    "sha": OTHER, "tree": "clean"}, SHA)
        self.assertIn("NOT the target", away)
        self.assertIn(OTHER[:12], away)
        self.assertIn(SHA[:12], away)
        unknown = cli.render_checkout({"state": transport.CHECKOUT_UNKNOWN,
                                       "sha": None, "tree": None}, SHA)
        self.assertIn("UNKNOWN", unknown)

    def test_the_interactive_take_rendering_carries_it(self):
        """The wiring, not just the renderer (the shape
        `test_tool_identity` uses for the agreement line): a renderer
        nothing calls is the defect this item reported."""
        source = Path(cli.__file__).read_text(encoding="utf-8")
        take = source[source.index("def cmd_take"):]
        take = take[:take.index("\ndef ")]
        self.assertIn("render_checkout(rec['head'], rec['sha'])", take)
        self.assertIn("brief.compact_request(", take)

    def test_the_probe_reads_head_and_never_refuses(self):
        def git(answers):
            def run(*args):
                value = answers[args[0]]
                if isinstance(value, Exception):
                    raise value
                return value
            return run
        cfg = type("Cfg", (), {"repo_root": Path(".")})()
        self.assertEqual(
            transport.reviewer_checkout(
                cfg, SHA, git=git({"rev-parse": SHA, "status": ""})),
            {"state": "at-target", "sha": SHA, "tree": "clean"})
        self.assertEqual(
            transport.reviewer_checkout(
                cfg, SHA, git=git({"rev-parse": OTHER, "status": " M f"})),
            {"state": "elsewhere", "sha": OTHER, "tree": "dirty"})
        self.assertEqual(
            transport.reviewer_checkout(
                cfg, SHA, git=git({"rev-parse": RuntimeError("unborn")})),
            {"state": "unknown", "sha": None, "tree": None})


class TestTakeEndToEnd(unittest.TestCase):
    """Two clones and a bare remote, as the loop integration test builds
    them. The scratch manifest declares no gates, so the table itself is
    held by the unit tests above; this holds the WIRING — which key carries
    what, and that the reviewer's HEAD is read from the reviewer's clone."""

    def setUp(self):
        scratch = scratch_loop_repo(self, "takec-", name="author",
                                    objective="compact take")
        self.tmp, self.author = scratch.tmp, scratch.repo
        self.base, self.claim, self.cwd = (scratch.base, scratch.claim,
                                           scratch.cwd)
        remote = self.tmp / "remote.git"
        self.reviewer = self.tmp / "reviewer"
        sh("git", "init", "-q", "--bare", "-b", "main", str(remote))
        sh("git", "-C", str(self.author), "remote", "add", "origin",
           str(remote))
        sh("git", "-C", str(self.author), "push", "-q", "-u", "origin",
           "main")
        sh("git", "clone", "-q", str(remote), str(self.reviewer))
        code, self.rec = run_cli(self.author, self.tmp / "state-author",
                                 "handoff", "--claim-file", str(self.claim),
                                 "--base", self.base, cwd=self.cwd)
        self.assertEqual(code, 0, self.rec)
        self.head = _git(self.author, "rev-parse", "HEAD")

    def take(self, where, state, *extra):
        return run_cli(where, self.tmp / state, "take", self.rec["kept"],
                       "--as", "codex", *extra, cwd=self.cwd)

    def test_the_default_carries_the_view_and_full_carries_the_bytes(self):
        """FALSIFICATION. Mutation: leave `envelope` in the default payload
        and the first assertion fails; assign the view to `envelope` under
        `--full` and the byte-equality fails wherever a block exists."""
        code, compact = self.take(self.reviewer, "state-a")
        self.assertEqual(code, 0, compact)
        self.assertNotIn("envelope", compact)
        self.assertIn("request_view", compact)
        self.assertTrue(Path(compact["kept"]).is_file())
        code, full = self.take(self.reviewer, "state-b", "--full")
        self.assertEqual(code, 0, full)
        self.assertNotIn("request_view", full)
        self.assertEqual(
            full["envelope"],
            Path(self.rec["kept"]).read_text(encoding="utf-8"))

    def test_head_is_the_reviewers_own_in_all_three_states(self):
        code, at = self.take(self.reviewer, "state-at")
        self.assertEqual(code, 0, at)
        self.assertEqual(at["head"], {"state": "at-target",
                                      "sha": self.head, "tree": "clean"})
        older = _git(self.reviewer, "rev-parse", "HEAD~1")
        sh("git", "-C", str(self.reviewer), "checkout", "-q", older)
        code, away = self.take(self.reviewer, "state-away")
        self.assertEqual(code, 0, away)
        self.assertEqual(away["head"]["state"], "elsewhere")
        self.assertEqual(away["head"]["sha"], older)
        empty = self.tmp / "reviewer-empty"
        sh("git", "init", "-q", "-b", "main", str(empty))
        code, unborn = self.take(empty, "state-unborn")
        self.assertEqual(code, 0, unborn)
        self.assertEqual(unborn["head"]["state"], "unknown")

    # ------------------------------------------------ `--compact` (issue #4)

    # `scoped` and `scoped_note` (0.26.0, public issue #10): the
    # reviewer-local scoped diff sits beside the reviewer-local `diff`.
    COMPACT_KEYS = {"ok", "kept", "digest", "bytes", "lineage", "round",
                    "sha", "reviewer", "head", "brief", "decide", "diff",
                    "scoped", "scoped_note", "then"}

    def test_compact_carries_pointers_only(self):
        """Public issue #4, the reviewer's request: a pointer-only `take`.

        FALSIFICATION. Mutations: return the default payload under
        `--compact` and the key-set assertion fails (`request_view`,
        `references`, `target`, `tool` appear); return before
        `transport.take` records and keeps anything and the kept file is
        missing from THIS take's state; report the view's size as `bytes`
        and the size assertion fails."""
        code, default = self.take(self.reviewer, "state-d")
        self.assertEqual(code, 0, default)
        code, compact = self.take(self.reviewer, "state-c", "--compact")
        self.assertEqual(code, 0, compact)
        self.assertEqual(set(compact), self.COMPACT_KEYS)
        kept = Path(compact["kept"])
        self.assertTrue(kept.is_file())
        self.assertTrue(kept.is_relative_to(self.tmp / "state-c"),
                        "the compact take must keep and record exactly as "
                        "the default does")
        self.assertEqual(compact["bytes"], kept.stat().st_size)
        self.assertEqual(kept.read_bytes(),
                         Path(self.rec["kept"]).read_bytes())
        for key in ("digest", "lineage", "round", "sha", "reviewer",
                    "head", "brief", "decide", "diff", "then"):
            self.assertEqual(compact[key], default[key], key)
        self.assertEqual(compact["sha"], self.head)
        self.assertNotIn("envelope", compact)
        self.assertNotIn("request_view", compact)

    def test_compact_and_full_are_one_choice(self):
        code, payload = self.take(self.reviewer, "state-x", "--compact",
                                  "--full")
        self.assertEqual(code, 2, payload)
        self.assertFalse((self.tmp / "state-x").exists(),
                         "a usage error must record nothing")

    def test_compact_through_the_real_entry_point_is_smaller(self):
        """`bin/loupe take` as a subprocess, default against `--compact`,
        on the same request: the compact result is a fraction of the
        default's bytes and parses as the same pointers."""
        env = {k: v for k, v in os.environ.items()
               if k not in (env_var("IN_GATE_RUN"), env_var("GATE_HEAD"),
                            env_var("GATE_BASE"), env_var("STATE_DIR"),
                            vocab.TRANSPORT_ENV)
               and k not in {var for var, _v, _t
                             in vocab.TRANSPORT_PROVIDER_SIGNALS}}
        sizes = {}
        for label, extra in (("default", []), ("compact", ["--compact"])):
            proc = subprocess.run(
                [str(REPO_ROOT / "bin" / "loupe"), "--ledger-dir",
                 str(self.tmp / f"state-sub-{label}"), "take",
                 self.rec["kept"], "--as", "codex", *extra],
                cwd=self.reviewer, env=env, capture_output=True,
                stdin=subprocess.DEVNULL, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            sizes[label] = len(proc.stdout)
            payload = json.loads(proc.stdout)
        self.assertEqual(set(payload), self.COMPACT_KEYS)
        self.assertLess(sizes["compact"], sizes["default"])


# The keys the real CLI's environment must not inherit from the suite: a
# gate run's own marks, a state or config redirection, and any transport
# declaration of the host.
_TAKE_DROP = ({env_var("IN_GATE_RUN"), env_var("GATE_HEAD"),
               env_var("GATE_BASE"), env_var("STATE_DIR"),
               env_var("CONFIG"), vocab.TRANSPORT_ENV}
              | {var for var, _v, _t in vocab.TRANSPORT_PROVIDER_SIGNALS})

#: Every key `take` can report as undeclared, in the table's own order.
_DECIDE = tuple(key for key, *_ in vocab.DECIDE_KEYS)
_DECLARED_LINE = re.compile(
    r"^(transport|debug|review_default|enforcement|round_cap|token_budget)"
    r"\s*=.*\n", re.M)


def _declare_none(toml: str) -> str:
    """The target states none of `_DECIDE` (the scratch copy of this
    repository's config already omits `transport`)."""
    return _DECLARED_LINE.sub("", toml)


def _declare_all(toml: str) -> str:
    """The target states every key of `_DECIDE`."""
    return _DECLARED_LINE.sub("", toml).replace(
        "\n[roles]\n",
        '\n[roles]\ntransport = "path"\ndebug = true\n'
        'review_default = "on"\nenforcement = "none"\n', 1).replace(
        "\n[limits]\n",
        "\n[limits]\nround_cap = 3\ntoken_budget = 600000\n", 1)


class TestCompactKeepsTheTargetsDecisions(unittest.TestCase):
    """Round-1 F5 of the 0.25.0 review: `take --compact` dropped `decide`,
    the TARGET commit's undeclared keys, which `take` computes from the
    configuration that governed the request and which exist only in the
    take's result. The kept request stamps the value applied
    (`transport="path"`), never that the key was undeclared, so following
    the compact pointer recovers nothing; and a target that omits a key and
    one that declares it printed identically, so the adapter's "absent
    config asks once" rule could not fire. Reproduced by the reviewer with
    an undeclared `roles.transport`: default take returned the pending
    choice, compact omitted it.

    FALSIFICATION, through the real CLI (`bin/loupe take` as a subprocess,
    HOME redirected, gate and transport variables popped, each take in its
    own state directory), default against `--compact` on the same request:

    - nonempty: the reviewer's case (only `roles.transport` undeclared);
      every kind `take` can produce (a target declaring none of the six
      keys: a set/unset pair of TOML lines, a set line with no off state
      whose applied value is the take's own, a set line rendered from the
      applied cap, and an off state that is the decided-undeclared comment
      line); the take's own applied value under `--transport paste`; and a
      target whose `# decided:` line suppresses its entry;
    - empty: a target declaring all six, where `decide` is PRESENT and
      empty, never absent (the paired control of the reviewer's case);
    - a reviewer checkout whose declarations differ from the target, both
      ways: the take reports the target's list, while `loupe decide` in
      that checkout reports the checkout's own, a different one;
    - on every row, compact still omits the request prose and the
      attestation table the default carries.

    Mutations (the track report records each one's own result): drop
    `decide` from the compact payload; compute it from this checkout's
    configuration; omit it when empty; carry only the keys.
    """

    # `scoped` and `scoped_note` (0.26.0, public issue #10): the
    # reviewer-local scoped diff sits beside the reviewer-local `diff`.
    COMPACT_KEYS = {"ok", "kept", "digest", "bytes", "lineage", "round",
                    "sha", "reviewer", "head", "brief", "decide", "diff",
                    "scoped", "scoped_note", "then"}
    PROSE_AND_ATTESTATIONS = {"request_view", "envelope", "references",
                              "target", "tool", "transport"}

    def scenario(self, edit):
        """A scratch round whose TARGET commit's `review.toml` is `edit` of
        the scratch copy, handed off, with a reviewer clone of the remote."""
        s = scratch_loop_repo(self, "takedec-", name="author",
                              objective="compact decisions")
        cfg = s.repo / "review.toml"
        cfg.write_text(edit(cfg.read_text(encoding="utf-8")),
                       encoding="utf-8")
        sh("git", "-C", str(s.repo), "commit", "-q", "--allow-empty", "-am",
           "declarations")
        s.home = s.tmp / "home"
        s.home.mkdir()
        remote = s.tmp / "remote.git"
        s.reviewer = s.tmp / "reviewer"
        sh("git", "init", "-q", "--bare", "-b", "main", str(remote))
        sh("git", "-C", str(s.repo), "remote", "add", "origin", str(remote))
        sh("git", "-C", str(s.repo), "push", "-q", "-u", "origin", "main")
        sh("git", "clone", "-q", str(remote), str(s.reviewer))
        write_identity(s.reviewer, identity_of("r", "r@example.invalid"))
        code, s.rec = run_cli(s.repo, s.tmp / "state-author", "handoff",
                              "--claim-file", str(s.claim), "--base", s.base,
                              cwd=s.cwd, env={"HOME": str(s.home)})
        self.assertEqual(code, 0, s.rec)
        s.target = _git(s.repo, "rev-parse", "HEAD")
        return s

    def loupe(self, s, state, *argv):
        env = {k: v for k, v in os.environ.items() if k not in _TAKE_DROP}
        env["HOME"] = str(s.home)
        proc = subprocess.run(
            [str(REPO_ROOT / "bin" / "loupe"), "--ledger-dir",
             str(s.tmp / state), *argv],
            cwd=s.reviewer, env=env, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=120)
        try:
            return proc.returncode, json.loads(proc.stdout), proc.stdout
        except json.JSONDecodeError:
            raise AssertionError(f"not JSON (exit {proc.returncode}): "
                                 f"{proc.stdout!r} {proc.stderr!r}") from None

    def both(self, s, tag, *extra):
        """Default and compact take of the same request; returns the one
        `decide` list they must share."""
        code, default, _ = self.loupe(s, f"state-{tag}-d", "take",
                                      s.rec["kept"], "--as", "codex", *extra)
        self.assertEqual(code, 0, default)
        code, compact, raw = self.loupe(s, f"state-{tag}-c", "take",
                                        s.rec["kept"], "--as", "codex",
                                        "--compact", *extra)
        self.assertEqual(code, 0, compact)
        self.assertEqual(compact.get("decide", "<absent>"),
                         default["decide"])
        self.assertEqual(set(compact), self.COMPACT_KEYS)
        self.assertFalse(self.PROSE_AND_ATTESTATIONS & set(compact))
        self.assertIn("## Claim", default["request_view"],
                      "the paired control: the default carries the prose")
        self.assertNotIn("## Claim", raw)
        self.assertNotIn(wire.attestation_fence(TOOL_NAME), raw)
        self.assertEqual(compact["sha"], s.target)
        return default["decide"]

    def test_the_reviewers_case_an_undeclared_target_key_survives(self):
        # Every key declared but `transport`, built from this module's own
        # helper and never from whatever review.toml sits at the root: the
        # extracted candidate's is the public example, which declares a
        # different set (the 0.25.0 round-2 hand-off's CI failure).
        s = self.scenario(lambda toml: _declare_all(toml).replace(
            'transport = "path"\n', "", 1))
        decide = self.both(s, "one")
        self.assertEqual(decide, [{
            "key": vocab.DECIDE_TRANSPORT,
            "meaning": vocab.DECIDE_KEYS[0][1],
            "applied": "path", "set": 'transport = "path"', "unset": None}])
        # Why compact must carry it: the kept request stamps the value
        # applied and nothing that says it was never declared.
        kept = Path(s.rec["kept"]).read_text(encoding="utf-8")
        self.assertNotIn(vocab.DECIDE_TRANSPORT, kept)

    def test_a_fully_declared_target_is_empty_and_present(self):
        """The reviewer's paired case, with the reviewer's own edit: the
        scratch copy declares the other five, and this adds the sixth."""
        s = self.scenario(_declare_all)
        self.assertEqual(self.both(s, "all"), [])

    def test_every_decision_kind_take_can_produce(self):
        s = self.scenario(_declare_none)
        decide = self.both(s, "none")
        self.assertEqual([d["key"] for d in decide], list(_DECIDE))
        self.assertEqual(decide, vocab.decisions(frozenset(), {
            vocab.DECIDE_TRANSPORT: "path",
            vocab.DECIDE_ROUND_CAP: config.DEFAULTS["limits"]["round_cap"]}))
        by = {d["key"]: d for d in decide}
        # A set/unset pair of TOML lines.
        self.assertEqual((by[vocab.DECIDE_DEBUG]["set"],
                          by[vocab.DECIDE_DEBUG]["unset"]),
                         ("debug = true", "debug = false"))
        # A set line with no off state, the applied value the take's own.
        self.assertEqual((by[vocab.DECIDE_TRANSPORT]["applied"],
                          by[vocab.DECIDE_TRANSPORT]["unset"]),
                         ("path", None))
        # A set line rendered from the applied cap.
        cap = config.DEFAULTS["limits"]["round_cap"]
        self.assertEqual(by[vocab.DECIDE_ROUND_CAP]["set"],
                         f"round_cap = {cap}")
        # An off state that is the decided-undeclared comment line.
        self.assertEqual(by[vocab.DECIDE_TOKEN_BUDGET]["unset"],
                         "# decided: limits.token_budget undeclared")
        # The applied value is this take's: the reviewer's correction.
        paste = self.both(s, "paste", "--transport", "paste")
        self.assertEqual({d["key"]: d["applied"] for d in paste}
                         [vocab.DECIDE_TRANSPORT], "paste")
        self.assertEqual([d["key"] for d in paste], list(_DECIDE))

    def test_a_decided_undeclared_line_is_honoured_in_both(self):
        s = self.scenario(lambda toml: _declare_none(toml).replace(
            "\n[limits]\n",
            "\n[limits]\n# decided: limits.token_budget undeclared\n", 1))
        decide = self.both(s, "decided")
        self.assertEqual([d["key"] for d in decide],
                         [k for k in _DECIDE
                          if k != vocab.DECIDE_TOKEN_BUDGET])

    def test_a_reviewer_checkout_declaring_otherwise_does_not_answer(self):
        """The target's list, whatever this checkout says. Paired control
        on each side: `loupe decide`, the verb that DOES read this
        checkout, reports the checkout's own, different list."""
        for target_edit, checkout_edit, name in (
                (_declare_none, _declare_all, "target none, checkout all"),
                (_declare_all, _declare_none, "target all, checkout none")):
            with self.subTest(case=name):
                s = self.scenario(target_edit)
                cfg = s.reviewer / "review.toml"
                cfg.write_text(checkout_edit(cfg.read_text(
                    encoding="utf-8")), encoding="utf-8")
                sh("git", "-C", str(s.reviewer), "commit", "-qam",
                   "the reviewer's checkout declares otherwise")
                self.assertNotEqual(_git(s.reviewer, "rev-parse", "HEAD"),
                                    s.target)
                code, local, _ = self.loupe(s, "state-local", "decide")
                self.assertEqual(code, 0, local)
                decide = self.both(s, "differs")
                target_keys = ([] if target_edit is _declare_all
                               else list(_DECIDE))
                self.assertEqual([d["key"] for d in decide], target_keys)
                self.assertNotEqual([d["key"] for d in local["decide"]],
                                    target_keys,
                                    "the checkout must really differ")


if __name__ == "__main__":
    unittest.main()

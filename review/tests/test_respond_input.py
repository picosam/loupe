"""`respond --from-json`: one closed grammar, every refusal named by its
JSON path and the kind it expected (brief `loupe-tool-feedback-2026-10-06`,
item 5, built 2026-10-22).

Observed at 0.29.0 and still true at 0.30.0: a file without `head` refused
as `{"error": "'head'", "next": "loupe respond --help"}`, and `--help`
named no member. Measured on the base commit (f2b770cb) before the fix,
every row of this module run with `--out` through that tree's `bin/loupe`:
of the 170 rows now refused at the boundary, 11 reached an AttributeError
traceback (a top level that is not an object, a payload that is not one,
nesting past the reader), 76 exited 0 and wrote the envelope (null for an
optional member, `"round": "1"`, a wrong kind for a payload text member
or a `note`; an unknown member, dropped or carried; a record written as
an array of pairs, read as an object), 24 left by a usage exit whose
`next`, `loupe respond --help`, named no member (`'head'`,
`'dispositions'`, a JSONDecodeError's message; and the two lone-surrogate
rows, after creating the `--out` file empty), 31 were validator items that
did not name the member's path, and 28 were refused as a mismatch with the
record (`supplied round True does not match the recorded verdict's 1`) or
as a repeat, naming the member but neither its path nor the kind expected.

THE GRAMMAR is `review.cli.RESPOND_TOP`, `RESPOND_RECORD`,
`RESPOND_PAYLOAD_TEXT` and `RESPOND_FALSIFICATION_TEXT`; the closed
vocabularies and the per-term payload rules stay `validate_disposition`'s,
whose items now name the same paths. The member rows below are DERIVED
from those tables, so a member added there is driven through every wrong
kind with no row written for it; `test_every_declared_member_has_its_rows`
holds the derivation, and the payload's text members to the vocabularies
they come from.

THE PARTITION. Every row runs the in-tree `bin/loupe respond` as a
subprocess, real argv, in a scratch repository whose round 1 is handed off
and closed (`_real_cli.Scratch`: its own identity, HOME, hooks and state
directory). Outcomes:

  refused  exit 1, next_kind blocked, `next` null; `error` is
           `--from-json <file>: <detail>` and names the member's JSON path
           and the kind expected; `remedy` names the path and
           `loupe respond --help`
  usage    exit 2, next_kind command, `next` = `loupe respond --help`
  items    exit 1, next_kind blocked, the validator's item code, its
           message naming the path
  blocked  exit 1, next_kind blocked: a semantic refusal that predates
           this change (a supplied value the record contradicts)
  accepted exit 0, the envelope printed (preview: records nothing)

Every refusal row runs with `--out` naming an ABSENT file and asserts it
was not created and that every byte under the scratch state directory is
unchanged; `TestModes` repeats refusals with a PRE-EXISTING `--out` file
(bytes unchanged), without `--out`, on standard input, against a verdict
that is not recorded, and as `--from-json=` spelling. The valid control
is the canonical document (`_valid`), run through the same entry point in
each mode, and every refusal row is it with one defect; `--out` on the
control writes and records, which is what makes the byte-identity
assertions non-vacuous.

  class                                    outcome
  file: missing path, directory, dangling  usage
        symlink, empty path
  file: symlink to a valid file            accepted (identical envelope)
  bytes: empty, whitespace only, not UTF-8 refused
        (stdin too), a UTF-8 BOM, not JSON,
        nested past the reader
  top level: array, string, number, null,  refused
        boolean, {}, an unknown member,
        a repeated member
  each top-level member (RESPOND_TOP) x    refused (kind or grammar); a
        absent (required only), null,      value the record contradicts:
        wrong kind, empty, malformed       blocked
  dispositions: [] and an unknown id       items (D-COMPLETENESS,
                                           D-UNKNOWN-FINDING)
  a record: string, array of pairs, null,  refused
        number, {}, unknown member,
        repeated member
  each record member (RESPOND_RECORD) x    refused; out of vocabulary or
        absent (required only), null,      blank: items (D-TERM,
        wrong kind, empty, out of vocab    D-SUBTYPE, D-IDENTITY ...)
  payload text members (RESPOND_PAYLOAD_   refused; blank: items
        TEXT) x null, number, array,      (D-PAYLOAD)
        object, blank
  payload: an unknown key of any kind      accepted, carried verbatim
  falsification: null, string, {}, each    refused or items
        member x null / number / absent /
        out of vocab, a repeated member
  falsification: an unknown key            accepted, carried verbatim
  a defect in the SECOND record            named dispositions[1]...
  optional members present and valid       accepted
  a number this reader cannot carry, the   refused, by path and kind
        loader's own limit (`wire.load_
        json`, lineage L369bb23a2f round 1
        F3): an integer past the
        interpreter's digit limit (negative,
        in an open payload or falsification
        member, in a text member), a number
        past a double's range, NaN,
        Infinity, -Infinity; two at once
        (the first in the text named); one
        beside a repeated member (the
        repeat named by its path)
  an integer AT the limit (negative too,   accepted, carried exactly
        an open falsification member), the
        largest double, a 5000-digit
        fraction, an underflow (reads 0.0)

The integer limit is the running interpreter's
(`sys.get_int_max_str_digits()`). Top level, `round` and a nested open
payload member below, at and past it, at all three doors side by side, are
`test_disposition_body.TestTheNumericLimit`, the finding's falsification.

MUTATIONS: one per guard, each run with `python -B` against a copy set
aside and restored (diff-confirmed); the results are recorded in the
track report of 2026-10-22, not here, because a docstring cannot be
re-run.
"""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from review import adapters, env_var, vocab, wire
from review.cli import (RESPOND_FALSIFICATION_TEXT, RESPOND_PAYLOAD_TEXT,
                        RESPOND_RECORD, RESPOND_TOP)
from review.tests import synth
from review.tests._real_cli import LOUPE, Scratch, loupe, loupe_env
from review.validate import ESCALATION_ALTERNATIVES

TEST = "run review/tests/test_x.py and see it fail without the fix"
HELP = "loupe respond --help"


class _ClassCase:
    """What `Scratch` needs of a test case, for a fixture built once per
    class: cleanups at class teardown, and a skip that skips the class."""

    def __init__(self, cls):
        self.cls = cls

    def addCleanup(self, fn, *args, **kwargs):  # noqa: N802 - unittest's name
        self.cls.addClassCleanup(fn, *args, **kwargs)

    def skipTest(self, reason):  # noqa: N802 - unittest's name
        raise unittest.SkipTest(reason)


def _round(cls, prefix: str) -> Scratch:
    """Round 1 handed off and closed: a recorded `changes requested`
    verdict with F1 (High, blocking, naming a falsification test) and F2
    (Low)."""
    s = Scratch(_ClassCase(cls), prefix)
    code, rec, out, err = loupe(s, "handoff", "--claim-file", str(s.claim),
                                "--base", s.base)
    if code != 0:
        raise AssertionError(f"handoff: {out}{err}")
    s.sha = rec["sha"]
    s.verdict = s.root / "v1.md"
    s.verdict.write_text(
        f'<{synth.TAG}-review-verdict sha="{s.sha}">\nVERDICT: changes '
        f"requested\n\n## findings\n\n"
        + synth.finding(1, severity="High", falsification=TEST,
                        evidence="f.txt:1")
        + synth.finding(2, severity="Low", evidence="f.txt:1")
        + f"## evidence checked\n\nf.txt\n</{synth.TAG}-review-verdict>\n",
        encoding="utf-8")
    code, rec, out, err = loupe(s, "close", "--verdict", str(s.verdict))
    if code != 0:
        raise AssertionError(f"close: {out}{err}")
    s.rows = s.root / "rows"
    s.rows.mkdir()
    return s


def _valid(sha: str) -> dict:
    """The canonical valid answer: the control every refusal row differs
    from by exactly one defect."""
    return {"head": sha, "dispositions": [
        {"finding_id": "F1", "disposition": "accepted",
         "payload": {"change": "fixed it", "verification": "ran the test",
                     "falsification": {"status": "pass",
                                       "mutation": "fails_without_fix"}}},
        {"finding_id": "F2", "disposition": "refuted",
         "payload": {"evidence": "f.txt:1 says otherwise"}}]}


def _snapshot(root: Path) -> dict:
    """Every file and directory under `root`, with its bytes."""
    out = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        out[rel] = None if path.is_dir() else path.read_bytes()
    return out


def _set(doc: dict, path: tuple, value) -> dict:
    """A copy of `doc` with the member at `path` replaced (or added)."""
    doc = copy.deepcopy(doc)
    node = doc
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return doc


def _drop(doc: dict, path: tuple) -> dict:
    doc = copy.deepcopy(doc)
    node = doc
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]
    return doc


def _dumps(doc) -> bytes:
    # ensure_ascii: a lone surrogate travels as the `\\ud800` escape, which
    # is valid JSON — the state a real author's file can be in.
    return json.dumps(doc).encode("utf-8")


def _jpath(path: tuple) -> str:
    out = ""
    for key in path:
        out += f"[{key}]" if isinstance(key, int) else (
            f".{key}" if out else key)
    return out


#: The running interpreter's integer limit, the scale of every numeric row
#: (lineage L369bb23a2f, round 1 F3); 0 would mean no limit.
LIMIT = sys.get_int_max_str_digits()
_MARK = "NUMBER-SENTINEL"


def _number_text(doc: dict, path: tuple, token: str) -> str:
    """`doc` as JSON text with the member at `path` written as the raw
    number `token`, so one Python cannot hold (past the limit, NaN) or
    would rewrite (1e-400) reaches the reader as an author wrote it. An
    empty path is the top level."""
    if not path:
        return token
    text = json.dumps(_set(doc, path, _MARK))
    assert text.count(json.dumps(_MARK)) == 1, text
    return text.replace(json.dumps(_MARK), token)


def _numbers(doc: dict) -> dict:
    """The numeric classes every reader of a disposition is driven
    through, by name: (path, token) for the past-the-limit and non-JSON
    numbers, each refused by its path. Empty classes for an interpreter
    with no integer limit."""
    r0 = ("dispositions", 0)
    extra = r0 + ("payload", "extra")
    past = "9" * (LIMIT + 1)
    rows = {
        "a number past a double's range": (extra, "1e400"),
        "a negative number past a double's range": (extra, "-1e400"),
        "NaN": (extra, "NaN"),
        "Infinity": (extra, "Infinity"),
        "-Infinity": (extra, "-Infinity"),
    }
    if LIMIT:
        rows.update({
            "an integer past the limit in an open payload member":
                (extra, past),
            "a negative integer past the limit": (extra, "-" + past),
            "an integer past the limit in an open falsification member":
                (r0 + ("payload", "falsification", "test"), past),
            "an integer past the limit in a payload text member":
                (r0 + ("payload", "change"), past),
        })
    return rows


def _number_fragment(where: str, token: str) -> str:
    """What the loader's refusal says of `token` at `where`."""
    where = where or "the top level"
    if token.lstrip("-").isdigit():
        return f"{where} is an integer of {len(token.lstrip('-'))} digits"
    if token in ("NaN", "Infinity", "-Infinity"):
        return f"{where} is {token}, which is not a JSON number"
    return f"{where} is the number {token}, past the range of a double"


def _two_numbers_text(doc: dict) -> tuple[str, str, str]:
    """Two integers past the limit, in `round` and in an open payload
    member, and the path and token of the first in the text: the one
    named."""
    extra = ("dispositions", 0, "payload", "extra")
    one, two = "9" * (LIMIT + 1), "8" * (LIMIT + 2)
    text = _number_text(_set(doc, ("round",), _MARK + "2"), extra, one)
    text = text.replace(json.dumps(_MARK + "2"), two)
    if text.index(one) < text.index(two):
        return text, _jpath(extra), one
    return text, "round", two


def _duplicate_beside_a_number(doc: dict) -> str:
    """A repeated member and, after it, an integer past the limit: the
    repeat is the defect named, by its path."""
    text = _number_text(doc, ("dispositions", 0, "payload", "extra"),
                        "9" * (LIMIT + 1))
    return text.replace('"disposition": "accepted"',
                        '"disposition": "deferred", "disposition": '
                        '"accepted"', 1)


class Row:
    def __init__(self, name: str, data: bytes, outcome: str,
                 where: str = "", fragment: str = "", code: str = ""):
        self.name, self.data, self.outcome = name, data, outcome
        self.where, self.fragment, self.code = where, fragment, code


# Kinds a member is driven through, and a sample of each.
_WRONG = {"null": None, "number": 7, "boolean": True, "array": ["x"],
          "object": {"x": "y"}, "string": "x"}
_KIND_OF = {"commit": "string", "records": "array", "actor": "string",
            "round": "number", "string": "string",
            "string_or_null": "string", "payload": "object"}


def _member_rows(base: dict, prefix: tuple, table: dict) -> list[Row]:
    """absent (required members), null, and every wrong JSON kind, for
    each member of one closed table — derived from the table itself."""
    rows = []
    for member, (kind, required) in table.items():
        path = prefix + (member,)
        where = _jpath(path)
        if required:
            rows.append(Row(f"{where} absent", _dumps(_drop(base, path)),
                            "refused", where, "is absent; expected"))
        for wrong, sample in _WRONG.items():
            if wrong == _KIND_OF[kind] or (kind == "string_or_null"
                                           and wrong == "null"):
                continue
            rows.append(Row(f"{where} {wrong}",
                            _dumps(_set(base, path, sample)), "refused",
                            where, "expected"))
    return rows


def _rows(sha: str) -> list[Row]:
    v = _valid(sha)
    text = json.dumps(v)
    r0 = ("dispositions", 0)
    f0 = r0 + ("payload", "falsification")
    rows = [
        # ---------------------------------------------- bytes and text
        Row("empty file", b"", "refused", "", "is empty"),
        Row("whitespace only", b" \n\t\n", "refused", "", "is empty"),
        Row("not UTF-8", b"\xff" + _dumps(v), "refused", "",
            "is not UTF-8 text"),
        Row("a UTF-8 byte order mark", b"\xef\xbb\xbf" + _dumps(v),
            "refused", "", "is not valid JSON"),
        Row("not JSON", b'{"head": ', "refused", "", "is not valid JSON"),
        Row("nested past the reader", b"[" * 100000 + b"]" * 100000,
            "refused", "", ""),
        Row("lone surrogate in a value",
            _dumps(_set(v, r0 + ("note",), "\ud800")), "refused",
            "dispositions[0].note", "lone surrogate"),
        Row("lone surrogate in a member name",
            _dumps(_set(v, r0 + ("payload", "\ud800"), "x")), "refused",
            'dispositions[0].payload["\\ud800"]', "lone surrogate"),
        # ---------------------------------------------- the top level
        Row("top level array", b"[1]", "refused", "", "is an array"),
        Row("top level string", b'"x"', "refused", "", 'is the string "x"'),
        Row("top level number", b"3", "refused", "", "is the number 3"),
        Row("top level null", b"null", "refused", "", "is null"),
        Row("top level boolean", b"true", "refused", "",
            "is the boolean true"),
        Row("top level empty object", b"{}", "refused", "head",
            "is absent"),
        Row("top level unknown member", _dumps(_set(v, ("heads",), sha)),
            "refused", "heads", "is not a member of the top level"),
        Row("top level misspelt required member",
            _dumps(_set(_drop(v, ("dispositions",)), ("dispositons",), [])),
            "refused", "dispositons", "is not a member"),
        Row("top level repeated member",
            text.replace('{"head": ', '{"head": "%s", "head": ' % sha,
                         1).encode(), "refused", "head",
            "more than once"),
        # ------------------------------------- top-level member grammar
        Row("head empty", _dumps(_set(v, ("head",), "")), "refused",
            "head", "40-character"),
        Row("head uppercase", _dumps(_set(v, ("head",), sha.upper())),
            "refused", "head", "40-character"),
        Row("head with a trailing newline",
            _dumps(_set(v, ("head",), sha + "\n")), "refused", "head",
            "40-character"),
        Row("verdict_sha short", _dumps(_set(v, ("verdict_sha",), "abc")),
            "refused", "verdict_sha", "40-character"),
        Row("author empty", _dumps(_set(v, ("author",), "")), "refused",
            "author", "actor identifier"),
        Row("author with whitespace",
            _dumps(_set(v, ("author",), "Claude Code")), "refused",
            "author", "actor identifier"),
        Row("round zero", _dumps(_set(v, ("round",), 0)), "refused",
            "round", "positive integer"),
        Row("round fractional", _dumps(_set(v, ("round",), 1.0)),
            "refused", "round", "positive integer"),
        Row("round as a string", _dumps(_set(v, ("round",), "1")),
            "refused", "round", "positive integer"),
        Row("round the record contradicts", _dumps(_set(v, ("round",), 2)),
            "blocked", "", "supplied round 2"),
        Row("verdict_sha the record contradicts",
            _dumps(_set(v, ("verdict_sha",), "a" * 40)), "blocked", "",
            "supplied verdict_sha"),
        Row("author the record contradicts",
            _dumps(_set(v, ("author",), "codex")), "blocked", "",
            "supplied author 'codex'"),
        # ------------------------------------------------ dispositions
        Row("dispositions empty", _dumps(_set(v, ("dispositions",), [])),
            "items", "dispositions", code="D-COMPLETENESS"),
        Row("record a string", _dumps(_set(v, r0, "F1 accepted")),
            "refused", "dispositions[0]", "is the string"),
        Row("record an array of pairs",
            _dumps(_set(v, r0, [list(p) for p in v["dispositions"][0]
                                .items()])),
            "refused", "dispositions[0]", "is an array"),
        Row("record null", _dumps(_set(v, r0, None)), "refused",
            "dispositions[0]", "is null"),
        Row("record a number", _dumps(_set(v, r0, 1)), "refused",
            "dispositions[0]", "is the number 1"),
        Row("record empty object", _dumps(_set(v, r0, {})), "refused",
            "dispositions[0].finding_id", "is absent"),
        Row("record unknown member",
            _dumps(_set(v, r0 + ("subtyp",), "test_amended")), "refused",
            "dispositions[0].subtyp", "is not a member of a disposition"),
        Row("record repeated member",
            text.replace('"disposition": "accepted"',
                         '"disposition": "deferred", "disposition": '
                         '"accepted"', 1).encode(),
            "refused", "dispositions[0].disposition", "more than once"),
        Row("finding_id empty", _dumps(_set(v, r0 + ("finding_id",), "")),
            "items", "dispositions[0].finding_id", code="D-UNKNOWN-FINDING"),
        Row("finding_id unknown",
            _dumps(_set(v, r0 + ("finding_id",), "F9")), "items",
            "dispositions[0].finding_id", code="D-UNKNOWN-FINDING"),
        Row("disposition empty", _dumps(_set(v, r0 + ("disposition",), "")),
            "items", "dispositions[0].disposition", code="D-TERM"),
        Row("disposition out of vocabulary",
            _dumps(_set(v, r0 + ("disposition",), "acepted")), "items",
            "dispositions[0].disposition", code="D-TERM"),
        Row("subtype out of vocabulary",
            _dumps(_set(v, r0 + ("subtype",), "amended")), "items",
            "dispositions[0].subtype", code="D-SUBTYPE"),
        Row("fingerprint the verdict contradicts",
            _dumps(_set(v, r0 + ("fingerprint",), "0" * 16)), "blocked",
            "", "supplied fingerprint"),
        Row("severity the verdict contradicts",
            _dumps(_set(v, r0 + ("severity",), "Low")), "blocked", "",
            "supplied severity"),
        # ------------------------------------------------------ payload
        Row("payload empty", _dumps(_set(v, r0 + ("payload",), {})),
            "items", "dispositions[0].payload.change", code="D-PAYLOAD"),
        Row("payload text blank",
            _dumps(_set(v, r0 + ("payload", "change"), " ")), "items",
            "dispositions[0].payload.change", code="D-PAYLOAD"),
        Row("escalated with neither alternative",
            _dumps(_set(v, ("dispositions", 1),
                        {"finding_id": "F2", "disposition": "escalated",
                         "payload": {"question": "q", "positions": "a"}})),
            "items", "dispositions[1].payload.authority",
            code="D-ESCALATION"),
        Row("test_amended without its members",
            _dumps(_set(v, r0 + ("subtype",), "test_amended")), "items",
            "dispositions[0].payload.original_test",
            code="D-TEST-AMENDED"),
        # ------------------------------------------------ falsification
        Row("falsification absent where the finding names a test",
            _dumps(_drop(v, f0)), "items",
            "dispositions[0].payload.falsification",
            code="D-FALSIFICATION-MISSING"),
        Row("falsification null", _dumps(_set(v, f0, None)), "refused",
            "dispositions[0].payload.falsification", "is null"),
        Row("falsification a string", _dumps(_set(v, f0, "pass")),
            "refused", "dispositions[0].payload.falsification",
            "is the string"),
        Row("falsification an array", _dumps(_set(v, f0, ["pass"])),
            "refused", "dispositions[0].payload.falsification",
            "is an array"),
        Row("falsification empty", _dumps(_set(v, f0, {})), "items",
            "dispositions[0].payload.falsification.status",
            code="D-FALSIFICATION"),
        Row("falsification status absent", _dumps(_drop(v, f0 + ("status",))),
            "items", "dispositions[0].payload.falsification.status",
            code="D-FALSIFICATION"),
        Row("falsification status out of vocabulary",
            _dumps(_set(v, f0 + ("status",), "passed")), "items",
            "dispositions[0].payload.falsification.status",
            code="D-FALSIFICATION"),
        Row("falsification mutation out of vocabulary",
            _dumps(_set(v, f0 + ("mutation",), "fails")), "items",
            "dispositions[0].payload.falsification.mutation",
            code="D-FALSIFICATION"),
        Row("falsification status fail",
            _dumps(_set(v, f0 + ("status",), "fail")), "items",
            "dispositions[0].payload.falsification.status",
            code="D-FALSIFICATION-FAILS"),
        Row("falsification not_run without a note",
            _dumps(_set(v, f0 + ("mutation",), "not_run")), "items",
            "dispositions[0].payload.falsification.note",
            code="D-FALSIFICATION-SILENT"),
        Row("falsification repeated member",
            text.replace('"status": "pass"',
                         '"status": "fail", "status": "pass"', 1).encode(),
            "refused", "dispositions[0].payload.falsification.status",
            "more than once"),
        # ------------------------------------------- the second record
        Row("a defect in the second record",
            _dumps(_set(v, ("dispositions", 1, "payload", "evidence"), 3)),
            "refused", "dispositions[1].payload.evidence",
            "is the number 3"),
        Row("the same id answered twice",
            _dumps(_set(v, ("dispositions", 1, "finding_id"), "F1")),
            "items", "dispositions[1]", code="D-DUPLICATE"),
    ]
    rows += _member_rows(v, (), RESPOND_TOP)
    rows += _member_rows(v, r0, RESPOND_RECORD)
    for member in RESPOND_PAYLOAD_TEXT:
        for wrong in ("null", "number", "array", "object"):
            path = r0 + ("payload", member)
            rows.append(Row(f"payload.{member} {wrong}",
                            _dumps(_set(v, path, _WRONG[wrong])),
                            "refused", _jpath(path), "expected a string"))
    for member in RESPOND_FALSIFICATION_TEXT:
        for wrong in ("null", "number", "array", "object"):
            path = f0 + (member,)
            rows.append(Row(f"falsification.{member} {wrong}",
                            _dumps(_set(v, path, _WRONG[wrong])),
                            "refused", _jpath(path), "expected a string"))
    # -------------- numbers the loader cannot carry (L369bb23a2f round 1 F3)
    for name, (path, token) in _numbers(v).items():
        where = _jpath(path)
        rows.append(Row(name, _number_text(v, path, token).encode(),
                        "refused", where, _number_fragment(where, token)))
    if LIMIT:
        text, where, token = _two_numbers_text(v)
        rows.append(Row("two integers past the limit", text.encode(),
                        "refused", where, _number_fragment(where, token)))
        rows.append(Row("a repeated member beside an integer past the limit",
                        _duplicate_beside_a_number(v).encode(), "refused",
                        "dispositions[0].disposition", "more than once"))
    return rows


def _accepted_rows(sha: str, fingerprint: str) -> list[tuple[str, dict]]:
    """Admitted variants of the control, each run through the entry
    point: every optional member present and valid, and the open keys."""
    v = _valid(sha)
    r0 = ("dispositions", 0)
    amended = _set(_set(v, r0 + ("subtype",), "test_amended"),
                   r0 + ("payload",),
                   {**v["dispositions"][0]["payload"],
                    **{k: "t" for k in vocab.TEST_AMENDED_PAYLOAD}})
    escalated = {"finding_id": "F2", "disposition": "escalated",
                 "payload": {"question": "q", "positions": "a vs b",
                             **{k: "x" for k in ESCALATION_ALTERNATIVES[1]}}}
    extra = r0 + ("payload", "extra")
    numbers = [("the largest double in an open payload member",
                _set(v, extra, 1.7976931348623157e308))]
    if LIMIT:
        at = int("9" * LIMIT)
        numbers += [
            ("an integer at the limit in an open payload member",
             _set(v, extra, at)),
            ("a negative integer at the limit", _set(v, extra, -at)),
            ("an integer at the limit in an open falsification member",
             _set(v, r0 + ("payload", "falsification", "test"), at)),
        ]
    return [
        ("the control", v),
        ("author, round and verdict_sha that agree",
         {**v, "author": "claude", "round": 1, "verdict_sha": sha}),
        ("subtype null", _set(v, r0 + ("subtype",), None)),
        ("subtype empty (no subtype)", _set(v, r0 + ("subtype",), "")),
        ("accepted(test_amended) with its members", amended),
        ("fingerprint and severity that agree",
         _set(_set(v, r0 + ("fingerprint",), fingerprint),
              r0 + ("severity",), "High")),
        ("a record note", _set(v, r0 + ("note",), "for the reviewer")),
        ("an unknown payload key of any kind",
         _set(v, r0 + ("payload", "extra"), [1, {"a": None}])),
        ("an unknown falsification key",
         _set(v, r0 + ("payload", "falsification", "test"), "t.py")),
        ("escalated by authority and criterion",
         _set(v, ("dispositions", 1), escalated)),
    ] + numbers


class _Rows(unittest.TestCase):
    """One closed round shared by every row: refusals write nothing, and
    the preview of an accepted row records nothing, which each row
    asserts."""

    @classmethod
    def setUpClass(cls):
        cls.s = _round(cls, "respond-input-")
        cls.verdict = wire.parse_verdict(
            cls.s.verdict.read_text(encoding="utf-8"))

    def _file(self, name: str, data: bytes) -> Path:
        slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")
        path = self.s.rows / f"{slug}.json"
        path.write_bytes(data)
        return path

    def respond(self, source, *, out: Path | None = None,
                stdin: bytes | None = None, verdict: Path | None = None,
                spelling: str = "space"):
        source = str(source)
        argv = ["respond", "--verdict", str(verdict or self.s.verdict)]
        argv += ([f"--from-json={source}"] if spelling == "equals"
                 else ["--from-json", source])
        if out is not None:
            argv += ["--out", str(out)]
        if stdin is None:
            return loupe(self.s, *argv)
        proc = subprocess.run(
            [str(LOUPE), *argv], cwd=str(self.s.repo),
            env=loupe_env(self.s, self.s.state), input=stdin,
            capture_output=True, timeout=180)
        out_text = proc.stdout.decode("utf-8")
        try:
            payload = json.loads(out_text)
        except json.JSONDecodeError:
            payload = None
        return (proc.returncode, payload, out_text,
                proc.stderr.decode("utf-8", "replace"))

    def check_refusal(self, row: Row, result, source: str):
        code, rec, out, err = result
        self.assertNotIn("Traceback", err)
        self.assertIsNotNone(rec, out)
        if row.outcome == "usage":
            self.assertEqual(code, 2, rec)
            self.assertEqual(rec["next_kind"], "command", rec)
            self.assertEqual(rec["next"], HELP, rec)
            self.assertIn(row.fragment, rec["error"])
            return
        self.assertEqual(code, 1, rec)
        self.assertEqual(rec["next_kind"], "blocked", rec)
        self.assertIsNone(rec["next"], rec)
        if row.outcome == "refused":
            self.assertTrue(rec["error"].startswith(
                f"--from-json {source}: "), rec["error"])
            self.assertIn(row.fragment, rec["error"])
            if row.where:
                # The whole path, not a prefix or a suffix of a longer one.
                self.assertRegex(rec["error"], rf"(?<![\w\[.])"
                                 rf"{re.escape(row.where)}(?![\w\[.])")
                self.assertIn(row.where, rec["remedy"])
            self.assertIn(HELP, rec["remedy"])
        elif row.outcome == "items":
            hits = [i for i in rec["items"] if i["code"] == row.code]
            self.assertTrue(hits, rec["items"])
            self.assertTrue(any(row.where in i["message"] for i in hits),
                            hits)
        elif row.outcome == "blocked":
            self.assertIn(row.fragment, rec["error"])
        else:
            raise AssertionError(row.outcome)

    def run_refusal(self, row: Row, **mode):
        """One refusal row: the result, and nothing written."""
        target = self.s.rows / (re.sub(r"[^A-Za-z0-9]+", "-", row.name)
                                + ".md")
        before_state = _snapshot(self.s.state)
        if mode.get("stdin"):
            source, result = "- (standard input)", self.respond(
                "-", out=target, stdin=row.data)
        else:
            path = self._file(row.name, row.data)
            source, result = str(path), self.respond(
                path, out=target, spelling=mode.get("spelling", "space"))
        self.check_refusal(row, result, source)
        self.assertFalse(target.exists(), "a refusal created --out")
        self.assertEqual(_snapshot(self.s.state), before_state,
                         "a refusal wrote under the state directory")


class TestTheGrammar(_Rows):

    def test_every_refusal_row(self):
        rows = [r for r in _rows(self.s.sha) if r.outcome != "accepted"]
        names = [r.name for r in rows]
        self.assertEqual(len(names), len(set(names)), "row names repeat")
        for row in rows:
            with self.subTest(row=row.name):
                self.run_refusal(row)

    def test_every_accepted_row(self):
        fingerprint = self.verdict.findings[0].fingerprint()
        for name, doc in _accepted_rows(self.s.sha, fingerprint):
            with self.subTest(row=name):
                before = _snapshot(self.s.state)
                code, rec, out, err = self.respond(self._file(name,
                                                              _dumps(doc)))
                self.assertEqual(code, 0, (out, err))
                parsed = wire.parse_disposition(out)
                self.assertTrue(parsed.wrapped, out)
                # Carried as written, never dropped: the record and its
                # payload reach the envelope with every member they had.
                for got, sent in zip(parsed.data["dispositions"],
                                     doc["dispositions"]):
                    self.assertEqual(got["payload"], sent["payload"])
                    for key in set(sent) - {"fingerprint", "severity"}:
                        self.assertEqual(got[key], sent[key], key)
                self.assertEqual(_snapshot(self.s.state), before,
                                 "a preview recorded something")

    def test_every_declared_member_has_its_rows(self):
        """The rows are derived from the grammar's own tables, so a member
        added there is driven through every wrong kind here."""
        names = {r.name for r in _rows(self.s.sha)}
        for member, (_kind, required) in RESPOND_TOP.items():
            self.assertIn(f"{member} null", names)
            if required:
                self.assertIn(f"{member} absent", names)
        for member, (_kind, required) in RESPOND_RECORD.items():
            self.assertIn(f"dispositions[0].{member} number", names)
            if required:
                self.assertIn(f"dispositions[0].{member} absent", names)
        for member in RESPOND_PAYLOAD_TEXT:
            self.assertIn(f"payload.{member} number", names)
        for member in RESPOND_FALSIFICATION_TEXT:
            self.assertIn(f"falsification.{member} number", names)
        # The payload's text members are every term's mandatory members,
        # accepted(test_amended)'s and an escalation's alternatives.
        expected = set(vocab.TEST_AMENDED_PAYLOAD)
        for members in vocab.DISPOSITION_PAYLOADS.values():
            expected |= set(members)
        for option in ESCALATION_ALTERNATIVES:
            expected |= set(option)
        self.assertEqual(set(RESPOND_PAYLOAD_TEXT), expected)

    def test_numbers_the_loader_carries(self):
        """The loader's acceptance, from text as an author wrote it: an
        integer at the limit reaches the envelope digit for digit, and a
        fraction of any length, an underflow and -0 read as Python reads
        them (the deliberate limit), never refused."""
        v = _valid(self.s.sha)
        extra = ("dispositions", 0, "payload", "extra")
        cases = [("1e-400", 0.0), ("1." + "1" * 5000, float("1." + "1" * 5000)),
                 ("-0", 0)]
        if LIMIT:
            cases.append(("9" * LIMIT, int("9" * LIMIT)))
        for token, value in cases:
            with self.subTest(token=token[:12], length=len(token)):
                before = _snapshot(self.s.state)
                path = self._file(f"carried {token[:12]} {len(token)}",
                                  _number_text(v, extra, token).encode())
                code, rec, out, err = self.respond(path)
                self.assertEqual(code, 0, (out, err))
                got = wire.parse_disposition(out).data["dispositions"][0][
                    "payload"]["extra"]
                self.assertEqual((type(got), got), (type(value), value))
                if isinstance(value, int) and token != "-0":
                    self.assertIn(f'"extra": {token}', out)
                self.assertEqual(_snapshot(self.s.state), before)

    def test_the_brief_s_reproducer(self):
        """The observed case, exactly: no top-level `head`."""
        v = _valid(self.s.sha)
        del v["head"]
        path = self._file("brief reproducer", _dumps(v))
        code, rec, out, err = self.respond(path)
        self.assertEqual(code, 1, rec)
        self.assertEqual(rec["error"],
                         f"--from-json {path}: head is absent; expected a "
                         f"40-character lowercase hex commit id — the "
                         f"commit this answer produced")
        self.assertNotEqual(rec["error"], "'head'")


class TestFileArguments(_Rows):
    """The `--from-json` argument itself: the invocation's own defects keep
    the usage exit every unreadable path argument of this verb has."""

    def test_unreadable_paths(self):
        missing = self.s.rows / "missing.json"
        dangling = self.s.rows / "dangling.json"
        dangling.symlink_to(self.s.rows / "nowhere.json")
        for name, arg, fragment in (
                ("missing file", str(missing), "No such file"),
                ("a directory", str(self.s.rows), "directory"),
                ("a dangling symlink", str(dangling), "No such file"),
                ("an empty path", "", "empty path")):
            with self.subTest(row=name):
                target = self.s.rows / "unreadable.md"
                before = _snapshot(self.s.state)
                result = self.respond(arg, out=target)
                self.check_refusal(Row(name, b"", "usage", "", fragment),
                                   result, arg)
                self.assertFalse(target.exists())
                self.assertEqual(_snapshot(self.s.state), before)

    def test_a_symlink_to_a_valid_file_reads_its_target(self):
        real = self._file("symlink target", _dumps(_valid(self.s.sha)))
        link = self.s.rows / "link.json"
        link.symlink_to(real)
        code_r, _rec, out_r, err_r = self.respond(real)
        code_l, _rec, out_l, err_l = self.respond(link)
        self.assertEqual((code_r, code_l), (0, 0), (err_r, err_l))
        self.assertEqual(out_l, out_r)


class TestModes(_Rows):
    """The same boundary in every mode the verb reads its input by."""

    SAMPLE = ("head absent", "top level array", "record an array of pairs",
              "payload.change number", "dispositions[0].payload string",
              "lone surrogate in a value", "falsification repeated member",
              "not UTF-8", "disposition out of vocabulary", "NaN") + (
                  ("an integer past the limit in an open payload member",)
                  if LIMIT else ())

    def sample(self):
        rows = {r.name: r for r in _rows(self.s.sha)}
        return [rows[name] for name in self.SAMPLE]

    def test_on_standard_input(self):
        for row in self.sample():
            with self.subTest(row=row.name):
                self.run_refusal(row, stdin=True)
        code, rec, out, err = self.respond(
            "-", stdin=_dumps(_valid(self.s.sha)))
        self.assertEqual(code, 0, (out, err))

    def test_with_the_equals_spelling(self):
        for row in self.sample():
            with self.subTest(row=row.name):
                self.run_refusal(row, spelling="equals")
        path = self._file("equals control", _dumps(_valid(self.s.sha)))
        code, rec, out, err = self.respond(path, spelling="equals")
        self.assertEqual(code, 0, (out, err))

    def test_without_out(self):
        for row in self.sample():
            with self.subTest(row=row.name):
                path = self._file(row.name, row.data)
                before = _snapshot(self.s.state)
                self.check_refusal(row, self.respond(path), str(path))
                self.assertEqual(_snapshot(self.s.state), before)

    def test_a_pre_existing_out_file_keeps_its_bytes(self):
        for row in self.sample():
            with self.subTest(row=row.name):
                target = self.s.rows / "kept.md"
                target.write_bytes(b"an earlier answer\n")
                path = self._file(row.name, row.data)
                before = _snapshot(self.s.state)
                self.check_refusal(row, self.respond(path, out=target),
                                   str(path))
                self.assertEqual(target.read_bytes(), b"an earlier answer\n")
                self.assertEqual(_snapshot(self.s.state), before)

    def test_against_a_verdict_not_recorded(self):
        # The same verdict with one byte more: the digest no longer names
        # the recorded one, so it is judged under this checkout and only
        # previewed — and the boundary is the same.
        other = self.s.root / "v1-unrecorded.md"
        other.write_text(self.s.verdict.read_text(encoding="utf-8")
                         .replace("because", "because, still", 1),
                         encoding="utf-8")
        for row in self.sample():
            if row.outcome != "refused":
                continue
            with self.subTest(row=row.name):
                path = self._file(row.name, row.data)
                before = _snapshot(self.s.state)
                self.check_refusal(row, self.respond(path, verdict=other),
                                   str(path))
                self.assertEqual(_snapshot(self.s.state), before)
        code, rec, out, err = self.respond(
            self._file("unrecorded control", _dumps(_valid(self.s.sha))),
            verdict=other)
        self.assertEqual(code, 0, (out, err))


class TestTheControlWritesAndRecords(unittest.TestCase):
    """The paired control in `--out` mode, on a round of its own: the valid
    document writes the file and records the answer, so a row's unchanged
    state directory and absent file are evidence, not a mode that writes
    nothing anyway."""

    @classmethod
    def setUpClass(cls):
        cls.s = _round(cls, "respond-input-out-")

    def test_the_valid_document_writes_and_records(self):
        path = self.s.rows / "valid.json"
        path.write_bytes(_dumps(_valid(self.s.sha)))
        target = self.s.rows / "d.md"
        before = _snapshot(self.s.state)
        code, rec, out, err = loupe(
            self.s, "respond", "--verdict", str(self.s.verdict),
            "--from-json", str(path), "--out", str(target))
        self.assertEqual(code, 0, (out, err))
        self.assertTrue(target.exists())
        self.assertEqual(rec["dispositions"], 2)
        self.assertNotEqual(_snapshot(self.s.state), before)


class TestTheHelpShowsTheShape(unittest.TestCase):

    def test_respond_help_names_every_member_and_term(self):
        # A scratch HOME and state directory: `--help` reads neither, and
        # this proves it on nothing of the operator's.
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items()
                   if k not in ("PYTHONPATH", "PYTHONSAFEPATH")}
            env.update(HOME=tmp, COLUMNS="80",
                       **{env_var("STATE_DIR"): str(Path(tmp) / "state")})
            proc = subprocess.run([str(LOUPE), "respond", "--help"],
                                  capture_output=True, text=True, env=env,
                                  cwd=tmp, stdin=subprocess.DEVNULL,
                                  timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = " ".join(proc.stdout.split())
        expected = [*RESPOND_TOP, *RESPOND_RECORD, *vocab.DISPOSITIONS,
                    *RESPOND_PAYLOAD_TEXT, *RESPOND_FALSIFICATION_TEXT,
                    *vocab.FALSIFICATION_STATUSES, *vocab.MUTATION_OUTCOMES,
                    *vocab.ACCEPTED_SUBTYPES, "falsification"]
        for word in expected:
            with self.subTest(word=word):
                self.assertRegex(text, rf"(?<![A-Za-z_]){word}(?![A-Za-z_])")

    def test_the_verb_line_the_adapters_render_is_unchanged(self):
        # The adapters' verb table renders `respond`'s one-line help; the
        # shape lives in the argument's help, never there.
        self.assertIn(("respond", "emit a disposition envelope"),
                      adapters.verb_table())


if __name__ == "__main__":
    unittest.main()

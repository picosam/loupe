"""The disposition envelope's body: one declared grammar, every shape it
can take refused by name at every reader (brief
`loupe-tool-feedback-2026-10-06`, item 5's envelope half, built
2026-10-22).

Measured on the base commit (f209e1a4) before the change, every row of
this module run through that tree's `bin/loupe` in a scratch round, the
state directory restored after any row that wrote: of the 191 rows now
refused at the boundary, `validate` exited 0 on 143 without the verdict
and on 118 with it, and `ledger add` exited 0 and RECORDED 105 of them (an
unknown record member, an uppercase `head`, a number for a payload text
member, an array `positions`); 12 reached an AttributeError traceback at
every reader (a record, `dispositions` or a payload of another kind); 7,
10 and 16 per reader left by the usage exit naming no member (`'NoneType'
object is not iterable`, `unhashable type: 'list'`, `int()` of a list
`round` at `ledger add`, a JSONDecodeError's message, the ledger encoder
meeting a lone surrogate); and `ledger add` refused 20 for a reason that
was not the body's (the verdict "not retrievable" once the body's round
was gone, two authors). The 25 rows of the validator's own items and the
16 accepted rows exit exactly as they did, at every reader.

THE DOMAIN, closed relative to one authority: `review.wire.DISPOSITION_TOP`,
`DISPOSITION_RECORD`, `DISPOSITION_PAYLOAD_TEXT` and
`DISPOSITION_FALSIFICATION_TEXT`, walked by `wire.disposition_body_defects`,
the one walker `respond --from-json` also uses (with its top level
narrowed: `review.cli.RESPOND_TOP`). The member rows below are DERIVED from
those tables, so a member added there is driven through every wrong kind
with no row written for it (`test_every_declared_member_has_its_rows`).
The payload's and the falsification record's key sets stay open; every
member the tool reads as text is a string.

THE READERS, each driven as its caller runs it: `bin/loupe validate
<file>`, `bin/loupe validate <file> --against <verdict>`, and `bin/loupe
ledger add <file>`, real argv, in a scratch repository whose round 1 is
handed off and closed (`test_respond_input._round`: its own identity, HOME,
hooks and state directory). `respond` reads its own emission through the
same reader; `test_respond_input` drives that door.

THE CONTROL is the envelope `respond` prints for the canonical answer with
`"round": 1`. Every row is that envelope with its body replaced by one
defect. The control validates in both modes and `ledger add` records it
(`TestTheControlRecords`), which is what makes each refusal's unchanged
state directory evidence rather than a door that writes nothing anyway.

Outcomes, per reader:

  refused   validate: exit 1, next_kind blocked, `next` null.
            ledger add: exit 1, next_kind command, `next` = `loupe validate
            <file>`, the contract of every refused envelope at that door,
            and every byte under the state directory unchanged.
            Both: the item's code, its message naming the member's JSON
            path and the kind expected.
  items     the validator's own item (D-TERM, D-PAYLOAD, D-IDENTITY ...),
            same exits; a code that needs the verdict (completeness,
            identity, a test the finding names) is not reported by
            `validate` without `--against`, which exits 0 there.
  accepted  validate exits 0 in both modes; `ledger add` records it, or
            refuses it for a pre-existing reason of the record (a round
            the ledger does not hold, a wrapper and body naming two
            authors), never for its shape.

  class                                          outcome
  body: absent, whitespace only, not JSON, a     refused, D-JSON-MALFORMED
        BOM, text after the object, nested past
        the reader
  body: a lone surrogate in a value, a member    refused, D-JSON-MALFORMED,
        name, a top-level member                 by path
  top level: array, string, number, null,        refused, D-JSON-SHAPE
        boolean, {}, an unknown member, a
        misspelt required one
  a repeated member at the top, in a record,     refused, D-JSON-DUPLICATE,
        in the falsification record              by path
  each top-level member (DISPOSITION_TOP) x      refused, D-JSON-SHAPE
        absent (required), null, every wrong
        kind; head and verdict_sha empty,
        uppercase, short, a newline; round
        negative, fractional, a string
  author, round, verdict_sha absent; author ""   accepted
        or not an actor; round 0
  dispositions: []                               items (D-COMPLETENESS)
  a record: string, array of pairs, null,        refused, D-JSON-SHAPE
        number, boolean, {}, an unknown member
  each record member (DISPOSITION_RECORD) x      refused, D-JSON-SHAPE
        absent (required), null, wrong kind
  record members empty or out of vocabulary      items (D-TERM, D-SUBTYPE,
                                                 D-UNKNOWN-FINDING,
                                                 D-IDENTITY, D-DUPLICATE)
  subtype null or "", a note, identity absent    accepted
  each payload text member x null, number,       refused, D-JSON-SHAPE
        boolean, array, object
  payload {}, blank text, an escalation or       items (D-PAYLOAD,
        test_amended short of its members        D-ESCALATION,
                                                 D-TEST-AMENDED)
  falsification: null, string, array, number,    refused, D-JSON-SHAPE
        boolean; each member x the same kinds
  falsification: absent, {}, a member absent,    items (D-FALSIFICATION*)
        empty or out of vocabulary, fail, inert,
        not_run without a note
  an unknown payload or falsification key        accepted, carried
  a defect in the second record; two at once     refused, every path named
  the bytes: not UTF-8, CRLF                     the shared envelope door,
                                                 unchanged: exit 2 usage,
                                                 exit 1 blocked
  a number the loader cannot carry               refused, D-JSON-MALFORMED,
        (`wire.load_json`; lineage               by path; beside a repeat,
        L369bb23a2f round 1 F3): an integer      D-JSON-DUPLICATE by path
        past the interpreter's digit limit
        (negative, in an open payload or
        falsification member, in a text
        member), a number past a double's
        range, NaN, Infinity, -Infinity; two
        at once (the first in the text named)
  an integer at the limit (negative, in an       accepted, carried exactly
        open falsification member), the
        largest double, a 5000-digit
        fraction, an underflow (reads 0.0)

The integer limit is the running interpreter's
(`sys.get_int_max_str_digits()`). `TestTheNumericLimit` drives the top
level, `round` and a nested open payload member below, at and past it
through `respond`, `validate` and `ledger add` side by side: the
finding's falsification. `TestEveryReaderOfTheLoader` gives every other
reader of the loader a row, and `TestTheLoader` the loader itself.

MUTATIONS: one per guard, each run with `python -B` on a copy of the tree
in a scratch directory, the worktree untouched (diff-confirmed); the
results are in the track report of 2026-10-22, because a docstring cannot
be re-run.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

from review import validate as validator
from review import vocab, wire
from review.tests import synth
from review.tests._real_cli import git, loupe
from review.tests.test_respond_input import (
    LIMIT, _drop, _duplicate_beside_a_number, _jpath, _number_fragment,
    _number_text, _numbers, _round, _set, _snapshot, _two_numbers_text,
    _valid)
from review.wire import (DISPOSITION_FALSIFICATION_TEXT,
                         DISPOSITION_PAYLOAD_TEXT, DISPOSITION_RECORD,
                         DISPOSITION_TOP, ESCALATION_ALTERNATIVES)

SHAPE, MALFORMED, DUPLICATE = "D-JSON-SHAPE", "D-JSON-MALFORMED", \
    "D-JSON-DUPLICATE"


class Row:
    """One body. `code` empty means accepted. `standalone` False: the code
    needs the verdict, so `validate` without `--against` accepts it.
    `ledger`, for an accepted row: "recorded", or a fragment of the
    pre-existing refusal `ledger add` answers it with."""

    def __init__(self, name: str, body: str, code: str = "",
                 where: str = "", fragment: str = "", *,
                 standalone: bool = True, ledger: str = "recorded"):
        self.name, self.body, self.code = name, body, code
        self.where, self.fragment = where, fragment
        self.standalone, self.ledger = standalone, ledger


# Kinds a member is driven through, and a sample of each.
_WRONG = {"null": None, "number": 7, "boolean": True, "array": ["x"],
          "object": {"x": "y"}, "string": "x"}
# The JSON kind each member kind is spelled in; that sample is the right
# kind and is not a row (a string that breaks a grammar has its own rows).
_KIND_OF = {"commit": "string", "records": "array", "string": "string",
            "string_or_null": "string", "payload": "object",
            "count": "number", "actor": "string", "round": "number"}
_WANT = wire.DISPOSITION_WANT


def _body(doc) -> str:
    # ensure_ascii: a lone surrogate travels as the `\\ud800` escape, which
    # is valid JSON — the state a hand-edited envelope can be in.
    return json.dumps(doc)


def _member_rows(base: dict, prefix: tuple, table: dict) -> list[Row]:
    """absent (required members), null, and every wrong JSON kind, for
    each member of one closed table — derived from the table itself."""
    rows = []
    for member, (kind, required) in table.items():
        path = prefix + (member,)
        where = _jpath(path)
        if required:
            rows.append(Row(f"{where} absent", _body(_drop(base, path)),
                            SHAPE, where, f"is absent; expected {_WANT[kind]}"))
        for wrong, sample in _WRONG.items():
            if wrong == _KIND_OF[kind] or (kind == "string_or_null"
                                           and wrong == "null"):
                continue
            rows.append(Row(f"{where} {wrong}",
                            _body(_set(base, path, sample)), SHAPE, where,
                            f"; expected {_WANT[kind]}"))
    return rows


def _rows(doc: dict, sha: str) -> list[Row]:
    v = doc
    text = _body(v)
    r0 = ("dispositions", 0)
    r1 = ("dispositions", 1)
    f0 = r0 + ("payload", "falsification")
    rows = [
        # ------------------------------------------------------ the body
        Row("body absent", "", MALFORMED, "", "body is empty"),
        Row("body whitespace only", " \n\t\n", MALFORMED, "",
            "body is empty"),
        Row("body not JSON", '{"head": ', MALFORMED, "",
            "is not readable JSON"),
        Row("body with a byte order mark", "﻿" + text, MALFORMED, "",
            "is not readable JSON"),
        Row("body with text after the object", text + " and more",
            MALFORMED, "", "is not readable JSON"),
        Row("body nested past the reader", "[" * 100000 + "]" * 100000,
            MALFORMED, "", "nests deeper"),
        Row("lone surrogate in a value",
            _body(_set(v, r0 + ("note",), "\ud800")), MALFORMED,
            "dispositions[0].note", "lone surrogate"),
        Row("lone surrogate in a member name",
            _body(_set(v, r0 + ("payload", "\ud800"), "x")), MALFORMED,
            'dispositions[0].payload["\\ud800"]', "lone surrogate"),
        Row("lone surrogate in a payload text member",
            _body(_set(v, r1 + ("payload", "evidence"), "a\udfffb")),
            MALFORMED, "dispositions[1].payload.evidence", "lone surrogate"),
        Row("lone surrogate in a top-level member",
            _body(_set(v, ("author",), "\ud800")), MALFORMED, "author",
            "lone surrogate"),
        # ------------------------------------------------- the top level
        Row("top level array", "[1]", SHAPE, "", "the top level is an array"),
        Row("top level string", '"x"', SHAPE, "",
            'the top level is the string "x"'),
        Row("top level number", "3", SHAPE, "", "the top level is the number"),
        Row("top level null", "null", SHAPE, "", "the top level is null"),
        Row("top level boolean", "true", SHAPE, "",
            "the top level is the boolean true"),
        Row("top level empty object", "{}", SHAPE, "head", "is absent"),
        Row("top level unknown member", _body(_set(v, ("heads",), sha)),
            SHAPE, "heads", "is not a member of the top level"),
        Row("top level misspelt required member",
            _body(_set(_drop(v, ("dispositions",)), ("dispositons",), [])),
            SHAPE, "dispositons", "is not a member"),
        Row("top level repeated member",
            text.replace('"head": ', '"head": "%s", "head": ' % sha, 1),
            DUPLICATE, "head", "more than once"),
        # ------------------------------------- top-level member grammar
        Row("head empty", _body(_set(v, ("head",), "")), SHAPE, "head",
            "40-character"),
        Row("head uppercase", _body(_set(v, ("head",), sha.upper())), SHAPE,
            "head", "40-character"),
        Row("head with a trailing newline",
            _body(_set(v, ("head",), sha + "\n")), SHAPE, "head",
            "40-character"),
        Row("verdict_sha short", _body(_set(v, ("verdict_sha",), "abc")),
            SHAPE, "verdict_sha", "40-character"),
        Row("verdict_sha empty", _body(_set(v, ("verdict_sha",), "")),
            SHAPE, "verdict_sha", "40-character"),
        Row("round negative", _body(_set(v, ("round",), -1)), SHAPE,
            "round", "non-negative integer"),
        Row("round fractional", _body(_set(v, ("round",), 1.0)), SHAPE,
            "round", "non-negative integer"),
        Row("round as a string", _body(_set(v, ("round",), "1")), SHAPE,
            "round", "non-negative integer"),
        # ------------------------------------------------- dispositions
        Row("dispositions empty", _body(_set(v, ("dispositions",), [])),
            "D-COMPLETENESS", "", "", standalone=False),
        Row("record a string", _body(_set(v, r0, "F1 accepted")), SHAPE,
            "dispositions[0]", "is the string"),
        Row("record an array of pairs",
            _body(_set(v, r0, [list(p) for p in
                               v["dispositions"][0].items()])),
            SHAPE, "dispositions[0]", "is an array"),
        Row("record null", _body(_set(v, r0, None)), SHAPE,
            "dispositions[0]", "is null"),
        Row("record a number", _body(_set(v, r0, 1)), SHAPE,
            "dispositions[0]", "is the number 1"),
        Row("record a boolean", _body(_set(v, r0, False)), SHAPE,
            "dispositions[0]", "is the boolean false"),
        Row("record empty object", _body(_set(v, r0, {})), SHAPE,
            "dispositions[0].finding_id", "is absent"),
        Row("record unknown member",
            _body(_set(v, r0 + ("subtyp",), "test_amended")), SHAPE,
            "dispositions[0].subtyp", "is not a member of a disposition"),
        Row("record repeated member",
            text.replace('"disposition": "accepted"',
                         '"disposition": "deferred", "disposition": '
                         '"accepted"', 1),
            DUPLICATE, "dispositions[0].disposition", "more than once"),
        Row("finding_id empty", _body(_set(v, r0 + ("finding_id",), "")),
            "D-UNKNOWN-FINDING", "dispositions[0].finding_id",
            standalone=False),
        Row("finding_id unknown", _body(_set(v, r0 + ("finding_id",), "F9")),
            "D-UNKNOWN-FINDING", "dispositions[0].finding_id",
            standalone=False),
        Row("disposition empty", _body(_set(v, r0 + ("disposition",), "")),
            "D-TERM", "dispositions[0].disposition"),
        Row("disposition out of vocabulary",
            _body(_set(v, r0 + ("disposition",), "acepted")), "D-TERM",
            "dispositions[0].disposition"),
        Row("subtype out of vocabulary",
            _body(_set(v, r0 + ("subtype",), "amended")), "D-SUBTYPE",
            "dispositions[0].subtype"),
        Row("subtype on a refutation",
            _body(_set(v, r1 + ("subtype",), "test_amended")), "D-SUBTYPE",
            "dispositions[1].subtype"),
        Row("fingerprint empty", _body(_set(v, r0 + ("fingerprint",), "")),
            "D-IDENTITY", "dispositions[0].fingerprint", standalone=False),
        Row("fingerprint the verdict contradicts",
            _body(_set(v, r0 + ("fingerprint",), "0" * 16)), "D-IDENTITY",
            "dispositions[0].fingerprint", standalone=False),
        Row("severity the verdict contradicts",
            _body(_set(v, r0 + ("severity",), "Low")), "D-IDENTITY",
            "dispositions[0].severity", standalone=False),
        Row("the same id answered twice",
            _body(_set(v, r1 + ("finding_id",), "F1")), "D-DUPLICATE",
            "dispositions[1]"),
        # ------------------------------------------------------ payload
        Row("payload empty", _body(_set(v, r0 + ("payload",), {})),
            "D-PAYLOAD", "dispositions[0].payload.change"),
        Row("payload text blank",
            _body(_set(v, r0 + ("payload", "change"), " ")), "D-PAYLOAD",
            "dispositions[0].payload.change"),
        Row("payload text empty",
            _body(_set(v, r1 + ("payload", "evidence"), "")), "D-PAYLOAD",
            "dispositions[1].payload.evidence"),
        Row("escalated with neither alternative",
            _body(_set(v, r1, {**v["dispositions"][1],
                               "disposition": "escalated",
                               "payload": {"question": "q",
                                           "positions": "a"}})),
            "D-ESCALATION", "dispositions[1].payload.authority"),
        Row("test_amended without its members",
            _body(_set(v, r0 + ("subtype",), "test_amended")),
            "D-TEST-AMENDED", "dispositions[0].payload.original_test"),
        # ------------------------------------------------ falsification
        Row("falsification absent where the finding names a test",
            _body(_drop(v, f0)), "D-FALSIFICATION-MISSING",
            "dispositions[0].payload.falsification", standalone=False),
        Row("falsification empty", _body(_set(v, f0, {})),
            "D-FALSIFICATION", "dispositions[0].payload.falsification.status"),
        Row("falsification status absent", _body(_drop(v, f0 + ("status",))),
            "D-FALSIFICATION", "dispositions[0].payload.falsification.status"),
        Row("falsification status empty",
            _body(_set(v, f0 + ("status",), "")), "D-FALSIFICATION",
            "dispositions[0].payload.falsification.status"),
        Row("falsification status out of vocabulary",
            _body(_set(v, f0 + ("status",), "passed")), "D-FALSIFICATION",
            "dispositions[0].payload.falsification.status"),
        Row("falsification mutation out of vocabulary",
            _body(_set(v, f0 + ("mutation",), "fails")), "D-FALSIFICATION",
            "dispositions[0].payload.falsification.mutation"),
        Row("falsification status fail",
            _body(_set(v, f0 + ("status",), "fail")),
            "D-FALSIFICATION-FAILS",
            "dispositions[0].payload.falsification.status"),
        Row("falsification passes without the fix",
            _body(_set(v, f0 + ("mutation",), "passes_without_fix")),
            "D-FALSIFICATION-INERT",
            "dispositions[0].payload.falsification.mutation"),
        Row("falsification not_run without a note",
            _body(_set(v, f0 + ("mutation",), "not_run")),
            "D-FALSIFICATION-SILENT",
            "dispositions[0].payload.falsification.note"),
        Row("falsification repeated member",
            text.replace('"status": "pass"',
                         '"status": "fail", "status": "pass"', 1),
            DUPLICATE, "dispositions[0].payload.falsification.status",
            "more than once"),
        # ------------------------------------------- the second record
        Row("a defect in the second record",
            _body(_set(v, r1 + ("payload", "evidence"), 3)), SHAPE,
            "dispositions[1].payload.evidence", "is the number 3"),
    ]
    rows += _member_rows(v, (), DISPOSITION_TOP)
    rows += _member_rows(v, r0, DISPOSITION_RECORD)
    for member in DISPOSITION_PAYLOAD_TEXT:
        for wrong in ("null", "number", "boolean", "array", "object"):
            path = r0 + ("payload", member)
            rows.append(Row(f"payload.{member} {wrong}",
                            _body(_set(v, path, _WRONG[wrong])), SHAPE,
                            _jpath(path), "; expected a string"))
    for wrong in ("null", "string", "array", "number", "boolean"):
        rows.append(Row(f"falsification {wrong}",
                        _body(_set(v, f0, _WRONG[wrong])), SHAPE,
                        "dispositions[0].payload.falsification",
                        "; expected an object {status, mutation[, note]}"))
    for member in DISPOSITION_FALSIFICATION_TEXT:
        for wrong in ("null", "number", "boolean", "array", "object"):
            path = f0 + (member,)
            rows.append(Row(f"falsification.{member} {wrong}",
                            _body(_set(v, path, _WRONG[wrong])), SHAPE,
                            _jpath(path), "; expected a string"))
    # -------------- numbers the loader cannot carry (L369bb23a2f round 1 F3)
    for name, (path, token) in _numbers(v).items():
        where = _jpath(path)
        rows.append(Row(name, _number_text(v, path, token), MALFORMED,
                        where, "in the disposition body, "
                        + _number_fragment(where, token)))
    if LIMIT:
        text, where, token = _two_numbers_text(v)
        rows.append(Row("two integers past the limit", text, MALFORMED,
                        where, "in the disposition body, "
                        + _number_fragment(where, token)))
        rows.append(Row("a repeated member beside an integer past the limit",
                        _duplicate_beside_a_number(v), DUPLICATE,
                        "dispositions[0].disposition", "more than once"))
    return rows


def _accepted_rows(doc: dict) -> list[Row]:
    """Admitted variants of the control, each run through every reader."""
    v = doc
    r0, r1 = ("dispositions", 0), ("dispositions", 1)
    record = v["dispositions"][0]
    amended = _set(_set(v, r0 + ("subtype",), "test_amended"),
                   r0 + ("payload",),
                   {**record["payload"],
                    **{k: "t" for k in vocab.TEST_AMENDED_PAYLOAD}})
    escalated = {**v["dispositions"][1], "disposition": "escalated",
                 "payload": {"question": "q", "positions": "a vs b",
                             **{k: "x" for k in ESCALATION_ALTERNATIVES[1]}}}
    unretrievable = "not retrievable as a RECORDED verdict"
    two_authors = "wrapper tag stamps author"
    extra = r0 + ("payload", "extra")
    numbers = [
        Row("the largest double in an open payload member",
            _body(_set(v, extra, 1.7976931348623157e308))),
        Row("an underflow, read as zero",
            _number_text(v, extra, "1e-400")),
        Row("a fraction of 5000 digits",
            _number_text(v, extra, "1." + "1" * 5000)),
    ]
    if LIMIT:
        at = int("9" * LIMIT)
        numbers += [
            Row("an integer at the limit in an open payload member",
                _body(_set(v, extra, at))),
            Row("a negative integer at the limit", _body(_set(v, extra, -at))),
            Row("an integer at the limit in an open falsification member",
                _body(_set(v, r0 + ("payload", "falsification", "test"),
                           at))),
        ]
    # After the control, whose records `TestTheControlRecords` asserts are
    # new: a row recorded before it would make them a repeat.
    return [
        Row("the control", _body(v)),
        Row("author absent", _body(_drop(v, ("author",)))),
        Row("verdict_sha absent", _body(_drop(v, ("verdict_sha",)))),
        Row("round absent", _body(_drop(v, ("round",))),
            ledger=unretrievable),
        Row("round 0", _body(_set(v, ("round",), 0)), ledger=unretrievable),
        Row("author empty", _body(_set(v, ("author",), "")),
            ledger=two_authors),
        Row("author not an actor identifier",
            _body(_set(v, ("author",), "Claude Code")), ledger=two_authors),
        Row("subtype null", _body(_set(v, r0 + ("subtype",), None))),
        Row("subtype empty", _body(_set(v, r0 + ("subtype",), ""))),
        Row("a record note", _body(_set(v, r0 + ("note",), "for you"))),
        Row("a record note empty", _body(_set(v, r0 + ("note",), ""))),
        Row("fingerprint and severity absent",
            _body(_drop(_drop(v, r0 + ("fingerprint",)),
                        r0 + ("severity",)))),
        Row("an unknown payload key of any kind",
            _body(_set(v, r0 + ("payload", "extra"), [1, {"a": None}]))),
        Row("an unknown falsification key",
            _body(_set(v, r0 + ("payload", "falsification", "test"),
                       "t.py"))),
        Row("escalated by authority and criterion",
            _body(_set(v, r1, escalated))),
        Row("accepted(test_amended) with its members", _body(amended)),
    ] + numbers


class _Round(unittest.TestCase):
    """One closed round and the control envelope `respond` prints for it.
    Refusals write nothing, which each row asserts."""

    PREFIX = "disposition-body-"

    @classmethod
    def setUpClass(cls):
        cls.s = _round(cls, cls.PREFIX)
        source = cls.s.rows / "control.json"
        source.write_text(json.dumps({**_valid(cls.s.sha), "round": 1}),
                          encoding="utf-8")
        code, _rec, out, err = loupe(cls.s, "respond", "--verdict",
                                     str(cls.s.verdict), "--from-json",
                                     str(source))
        if code != 0:
            raise AssertionError(f"respond: {out}{err}")
        cls.control = out
        parsed = wire.parse_disposition(out)
        if parsed.body_defects or not parsed.wrapped:
            raise AssertionError(f"the control is not valid: {out}")
        lines = out.rstrip("\n").split("\n")
        cls.open_tag, cls.close_tag = lines[0], lines[-1]
        cls.doc = parsed.data

    def envelope(self, row: Row) -> Path:
        slug = re.sub(r"[^A-Za-z0-9]+", "-", row.name).strip("-")
        path = self.s.rows / f"{slug}.md"
        separator = "\n" if row.body else ""
        path.write_bytes(f"{self.open_tag}{separator}{row.body}{separator}"
                         f"{self.close_tag}\n".encode("utf-8"))
        return path

    def assert_named(self, row: Row, items: list):
        hits = [i for i in items if i["code"] == row.code]
        self.assertTrue(hits, items)
        if row.where:
            pattern = (rf"(?<![\w\[.]){re.escape(row.where)}(?![\w\[.])")
            self.assertTrue(any(re.search(pattern, i["message"])
                                for i in hits), hits)
        if row.fragment:
            self.assertTrue(any(row.fragment in i["message"] for i in hits),
                            hits)

    def validate(self, row: Row, *, against: bool):
        path = self.envelope(row)
        argv = ["validate", str(path)]
        if against:
            argv += ["--against", str(self.s.verdict)]
        before = _snapshot(self.s.state)
        code, rec, out, err = loupe(self.s, *argv)
        self.assertNotIn("Traceback", err)
        self.assertIsNotNone(rec, out)
        self.assertEqual(_snapshot(self.s.state), before,
                         "validate wrote under the state directory")
        if not row.code or (not against and not row.standalone):
            self.assertEqual(code, 0, rec)
            self.assertTrue(rec["ok"], rec)
            return
        self.assertEqual(code, 1, rec)
        self.assertEqual(rec["next_kind"], "blocked", rec)
        self.assertIsNone(rec["next"], rec)
        self.assert_named(row, rec["items"])

    def ledger_add(self, row: Row):
        path = self.envelope(row)
        before = _snapshot(self.s.state)
        code, rec, out, err = loupe(self.s, "ledger", "add", str(path))
        self.assertNotIn("Traceback", err)
        self.assertIsNotNone(rec, out)
        return code, rec, before


class TestValidate(_Round):

    def test_every_row_without_the_verdict(self):
        rows = _rows(self.doc, self.s.sha) + _accepted_rows(self.doc)
        names = [r.name for r in rows]
        self.assertEqual(len(names), len(set(names)), "row names repeat")
        for row in rows:
            with self.subTest(row=row.name):
                self.validate(row, against=False)

    def test_every_row_against_the_verdict(self):
        for row in _rows(self.doc, self.s.sha) + _accepted_rows(self.doc):
            with self.subTest(row=row.name):
                self.validate(row, against=True)

    def test_a_refused_body_is_not_read(self):
        """The reader's guarantee to every in-process consumer
        (`answered_verdict`, `disposition_events`): a body with a defect
        leaves `data` empty, and one without leaves it whole."""
        for row in _rows(self.doc, self.s.sha) + _accepted_rows(self.doc):
            with self.subTest(row=row.name):
                parsed = wire.parse_disposition(
                    self.envelope(row).read_text(encoding="utf-8"))
                if row.code.startswith("D-JSON"):
                    self.assertIn(row.code,
                                  {c for c, _m in parsed.body_defects})
                    self.assertEqual(parsed.data, {})
                else:
                    self.assertEqual(parsed.body_defects, ())
                    self.assertEqual(parsed.data, json.loads(row.body))

    def test_every_defect_is_reported_at_once(self):
        """The reader collects; it does not stop at the first."""
        v = _set(_set(self.doc, ("dispositions", 0, "payload"), None),
                 ("dispositions", 1, "finding_id"), 2)
        v = _set(v, ("head", ), "")
        row = Row("three defects", _body(v), SHAPE)
        code, rec, out, err = loupe(self.s, "validate",
                                    str(self.envelope(row)))
        self.assertEqual(code, 1, rec)
        messages = " ".join(i["message"] for i in rec["items"])
        for where in ("head", "dispositions[0].payload",
                      "dispositions[1].finding_id"):
            self.assertRegex(messages, rf"(?<![\w\[.]){re.escape(where)}"
                                       rf"(?![\w\[.])")

    def test_every_declared_member_has_its_rows(self):
        """The rows are derived from the grammar's own tables."""
        names = {r.name for r in _rows(self.doc, self.s.sha)}
        for member, (_kind, required) in DISPOSITION_TOP.items():
            self.assertIn(f"{member} null", names)
            if required:
                self.assertIn(f"{member} absent", names)
        for member, (_kind, required) in DISPOSITION_RECORD.items():
            self.assertIn(f"dispositions[0].{member} number", names)
            if required:
                self.assertIn(f"dispositions[0].{member} absent", names)
        for member in DISPOSITION_PAYLOAD_TEXT:
            self.assertIn(f"payload.{member} number", names)
        for member in DISPOSITION_FALSIFICATION_TEXT:
            self.assertIn(f"falsification.{member} number", names)
        self.assertEqual(set(_KIND_OF), set(wire.DISPOSITION_WANT))


class TestLedgerAdd(_Round):
    PREFIX = "disposition-body-ledger-"

    def test_every_refused_row(self):
        for row in _rows(self.doc, self.s.sha):
            with self.subTest(row=row.name):
                code, rec, before = self.ledger_add(row)
                self.assertEqual(code, 1, rec)
                self.assertEqual(rec["next_kind"], "command", rec)
                self.assertTrue(rec["next"].startswith("loupe validate "),
                                rec)
                self.assert_named(row, rec["items"])
                self.assertEqual(_snapshot(self.s.state), before,
                                 "a refusal wrote under the state directory")


class TestTheControlRecords(_Round):
    """The accepted rows on a round of their own: `ledger add` records each
    one the record admits, which is what makes every refusal's unchanged
    state directory evidence."""
    PREFIX = "disposition-body-recorded-"

    def test_every_accepted_row(self):
        for row in _accepted_rows(self.doc):
            with self.subTest(row=row.name):
                code, rec, before = self.ledger_add(row)
                if row.ledger == "recorded":
                    self.assertEqual(code, 0, rec)
                    self.assertTrue(rec["ok"], rec)
                    # The control's records are new; a variant that derives
                    # to the same answer (no author: the record's is
                    # derived) dedups them, and its ingest row is new.
                    if row.name == "the control":
                        self.assertGreater(rec["events_added"], 0, rec)
                    self.assertNotEqual(_snapshot(self.s.state), before)
                    continue
                self.assertEqual(code, 1, rec)
                self.assertNotIn("items", rec)
                self.assertIn(row.ledger, rec.get("error", ""), rec)
                self.assertEqual(_snapshot(self.s.state), before)


class TestTheSharedDoor(_Round):
    """Bytes that never become a body: the envelope door every kind shares
    (`wire.decode_envelope`), unchanged by this boundary and recorded here
    so its contract is not mistaken for the body's."""
    PREFIX = "disposition-body-door-"

    def test_not_utf8_and_crlf(self):
        good = self.control.encode("utf-8")
        cases = (("not UTF-8", good.replace(b'"head"', b'"he\xffad"', 1), 2,
                  "command"),
                 ("CRLF line endings", good.replace(b"\n", b"\r\n"), 1,
                  "blocked"))
        for name, data, exit_code, kind in cases:
            path = self.s.rows / (re.sub(r"\W+", "-", name) + ".md")
            path.write_bytes(data)
            for argv in (("validate", str(path)),
                         ("ledger", "add", str(path))):
                with self.subTest(case=name, verb=argv[0]):
                    before = _snapshot(self.s.state)
                    code, rec, out, err = loupe(self.s, *argv)
                    self.assertNotIn("Traceback", err)
                    self.assertEqual(code, exit_code, rec)
                    self.assertEqual(rec["next_kind"], kind, rec)
                    self.assertEqual(_snapshot(self.s.state), before)


class TestTheNumericLimit(_Round):
    """THE FALSIFICATION of lineage L369bb23a2f, round 1 F3: an integer
    below, at and past the running interpreter's limit
    (`sys.get_int_max_str_digits()`), at the top level, in `round` and in
    a nested open payload member, at all three doors side by side:
    `respond --from-json` (`--out` naming an absent file, except the
    accepted preview), `validate` without and with the verdict, and
    `ledger add`. On the reviewed target every past-the-limit case left
    each door by the usage exit, exit 2, printing the interpreter's
    conversion message and naming no member.

      place             below and at the limit        past the limit
      top level         the existing shape refusal:   refused by path at
                        D-JSON-SHAPE; respond "the    every door: D-JSON-
                        top level is the number"      MALFORMED; respond
      round             the existing semantic         blocked, naming the
                        refusals: respond "supplied   number's path and its
                        round", ledger add "not       digits
                        retrievable"; validate 0
      nested open       accepted and carried digit
        payload member  for digit; ledger add records

    Every refusal exits 1: respond and validate blocked with `next` null,
    ledger add with `next` = `loupe validate <file>`; no `--out` file, and
    every byte under the state directory unchanged."""

    PREFIX = "disposition-body-numbers-"

    def test_every_door_below_at_and_past_the_limit(self):
        if not LIMIT:
            self.skipTest("this interpreter reads an integer of any length")
        extra = ("dispositions", 0, "payload", "extra")
        nested = _set(self.doc, extra, {"a": ["x"]})
        places = (("top", "", self.doc, ()),
                  ("round", "round", self.doc, ("round",)),
                  ("nested", "dispositions[0].payload.extra.a[0]", nested,
                   extra + ("a", 0)))
        for place, where, base, path in places:
            for digits in (LIMIT - 1, LIMIT, LIMIT + 1):
                token = "9" * digits
                with self.subTest(place=place, digits=digits):
                    self.doors(place, where, token,
                               _number_text(base, path, token))

    def refused_by_body(self, place: str, where: str, token: str) -> tuple:
        """(code, message) the envelope readers refuse it with, or None."""
        if len(token) > LIMIT:
            return MALFORMED, ("in the disposition body, "
                               + _number_fragment(where, token))
        if place == "top":
            return SHAPE, "in the disposition body, the top level is the " \
                          "number 9"
        return None

    def doors(self, place: str, where: str, token: str, text: str):
        stem = f"numbers-{place}-{len(token)}"
        source = self.s.rows / f"{stem}.json"
        source.write_text(text, encoding="utf-8")
        target = self.s.rows / f"{stem}-out.md"
        past = len(token) > LIMIT
        preview = place == "nested" and not past
        argv = ["respond", "--verdict", str(self.s.verdict), "--from-json",
                str(source)] + ([] if preview else ["--out", str(target)])
        before = _snapshot(self.s.state)
        code, rec, out, err = loupe(self.s, *argv)
        self.assertNotIn("Traceback", err)
        self.assertFalse(target.exists(), "respond created --out")
        self.assertEqual(_snapshot(self.s.state), before,
                         "respond wrote under the state directory")
        if preview:
            self.assertEqual(code, 0, (out, err))
            self.assertIn(token, out)
            self.assertEqual(wire.parse_disposition(out).data[
                "dispositions"][0]["payload"]["extra"], {"a": [int(token)]})
        else:
            self.assertIsNotNone(rec, out)
            self.assertEqual((code, rec["next_kind"], rec["next"]),
                             (1, "blocked", None), rec)
            if past:
                self.assertIn(f"--from-json {source}: "
                              + _number_fragment(where, token), rec["error"])
                if where:
                    self.assertIn(where, rec["remedy"])
            elif place == "top":
                self.assertIn(f"--from-json {source}: the top level is the "
                              f"number 9", rec["error"])
            else:
                self.assertIn(f"supplied round {token}", rec["error"])
        envelope = self.envelope(Row(stem, text))
        refusal = self.refused_by_body(place, where, token)
        for against in ((), ("--against", str(self.s.verdict))):
            before = _snapshot(self.s.state)
            code, rec, out, err = loupe(self.s, "validate", str(envelope),
                                        *against)
            self.assertNotIn("Traceback", err)
            self.assertIsNotNone(rec, out)
            self.assertEqual(_snapshot(self.s.state), before)
            if refusal is None:
                self.assertEqual(code, 0, rec)
                continue
            self.assertEqual((code, rec["next_kind"], rec["next"]),
                             (1, "blocked", None), rec)
            self.assertTrue(any(i["code"] == refusal[0]
                                and refusal[1] in i["message"]
                                for i in rec["items"]), rec["items"])
        before = _snapshot(self.s.state)
        code, rec, out, err = loupe(self.s, "ledger", "add", str(envelope))
        self.assertNotIn("Traceback", err)
        self.assertIsNotNone(rec, out)
        if refusal is not None:
            self.assertEqual((code, rec["next_kind"]), (1, "command"), rec)
            self.assertTrue(rec["next"].startswith("loupe validate "), rec)
            self.assertTrue(any(i["code"] == refusal[0]
                                and refusal[1] in i["message"]
                                for i in rec["items"]), rec["items"])
            self.assertEqual(_snapshot(self.s.state), before)
        elif place == "round":
            self.assertEqual(code, 1, rec)
            self.assertIn("not retrievable as a RECORDED verdict",
                          rec["error"])
            self.assertEqual(_snapshot(self.s.state), before)
        else:
            self.assertEqual(code, 0, rec)
            self.assertTrue(rec["ok"], rec)
            self.assertNotEqual(_snapshot(self.s.state), before)


class TestEveryReaderOfTheLoader(_Round):
    """The numeric limit sits in the ONE loader, `wire.load_json`, so it
    reaches every reader of it and not only the disposition doors: one row
    per reader, past the limit beside an integer AT the limit that reaches
    the reader's next existing check. On the reviewed target each left on
    the interpreter's ValueError, by the usage exit at a CLI door.

      reader, its door                 past the limit        at the limit
      wire.parse_authorization,        A-JSON-MALFORMED      A-WRAPPER-SPLIT
        validate <authorization>       naming round          (wrapper 1)
      validate.parse_attestations,     A-BLOCK naming        the record
        validate <request> (also       [0].exit_code         checks, no A-BLOCK
        `brief` and transport)
      emit.parse_claim, handoff        blocked, naming       blocked: must be
        --claim-file (emit-request)    objective             a string
      import-legacy's event lines      blocked, line 1       blocked: no
                                                             --source-commit

    Two readers outside `review/` are not rows here, because these tests
    travel with the package and those files do not: `tools/loupe_approve`
    catches `json.JSONDecodeError`, which `NumberOutOfRange` is, and
    `bin/guarded-merge` catches ValueError, which it also is
    (`TestTheLoader`); the track report records the one in-process probe of
    the first."""

    PREFIX = "disposition-body-loader-"

    def setUp(self):
        if not LIMIT:
            self.skipTest("this interpreter reads an integer of any length")
        self.at, self.past = "9" * LIMIT, "9" * (LIMIT + 1)

    def run_refusal(self, *argv) -> dict:
        """A door that refuses: blocked, and nothing written."""
        before = _snapshot(self.s.state)
        head = git(self.s.repo, "rev-parse", "HEAD")
        code, rec, out, err = loupe(self.s, *argv)
        self.assertNotIn("Traceback", err)
        self.assertIsNotNone(rec, out)
        self.assertEqual((code, rec["next_kind"]), (1, "blocked"), rec)
        self.assertEqual(_snapshot(self.s.state), before)
        self.assertEqual(git(self.s.repo, "rev-parse", "HEAD"), head)
        return rec

    def test_validate_an_authorization(self):
        def envelope(name, token):
            text = wire.emit_authorization(
                synth.TAG, sha=self.s.sha, round_no=1, lineage="L1",
                by="sammy", reason="shipping over one open finding",
                waived=[{"finding_id": "F1", "fp": "fp2:" + "1" * 16,
                         "reason": "happy for now", "by": "sammy"}])
            self.assertEqual(text.count('"round": 1,'), 1)
            path = self.s.rows / f"authorization-{name}.md"
            path.write_text(text.replace('"round": 1,', f'"round": {token},'),
                            encoding="utf-8")
            return str(path)
        code, rec, out, err = loupe(self.s, "validate",
                                    envelope("control", "1"))
        self.assertEqual((code, rec["items"]), (0, []), rec)
        for name, token, want in (("at", self.at, "A-WRAPPER-SPLIT"),
                                  ("past", self.past, "A-JSON-MALFORMED")):
            with self.subTest(case=name):
                code, rec, out, err = loupe(self.s, "validate",
                                            envelope(name, token))
                self.assertNotIn("Traceback", err)
                self.assertEqual((code, rec["next_kind"]), (1, "blocked"), rec)
                errors = {i["code"]: i["message"] for i in rec["items"]
                          if i["level"] == "error"}
                self.assertIn(want, errors)
                if name == "past":
                    self.assertIn(_number_fragment("round", token),
                                  errors[want])
                else:
                    self.assertNotIn("A-JSON-MALFORMED", errors)

    def test_the_attestation_block(self):
        fence = wire.attestation_fence(synth.ONE_GATE.wrapper_tag)
        for name, token in (("control", "0"), ("at", self.at),
                            ("past", self.past)):
            with self.subTest(case=name):
                items = validator.validate_attestations(
                    f'```{fence}\n[{{"id": "tests", "exit_code": {token}}}]'
                    f'\n```', synth.ONE_GATE)
                blocks = [i.message for i in items if i.code == "A-BLOCK"]
                if name == "past":
                    self.assertEqual(len(blocks), 1, items)
                    self.assertIn(_number_fragment("[0].exit_code", token),
                                  blocks[0])
                else:
                    self.assertEqual(blocks, [])
                    self.assertTrue(items)

    def test_the_attestation_block_at_validate_without_a_manifest(self):
        """The scratch declares no gate, and `validate` answers an
        unreadable block there with its A-NO-MANIFEST notice: past the
        limit now takes that path exactly as malformed JSON does, where it
        left by the usage exit."""
        request = next(self.s.state.rglob("round-1-request-*.md"))
        text = request.read_text(encoding="utf-8")
        fence = wire.attestation_fence(synth.TAG)
        block = f"```{fence}\n[]\n```"
        self.assertEqual(text.count(block), 1)
        records = {}
        for name, body in (("malformed", "["), ("past", f"[{self.past}]")):
            path = self.s.rows / f"request-{name}.md"
            path.write_text(text.replace(block, f"```{fence}\n{body}\n```"),
                            encoding="utf-8")
            code, rec, out, err = loupe(self.s, "validate", str(path))
            self.assertNotIn("Traceback", err)
            records[name] = (code, rec["items"])
        self.assertEqual(records["past"], records["malformed"])
        self.assertEqual(records["past"][0], 0)

    def test_handoff_reads_the_claim(self):
        claim = json.loads(self.s.claim.read_text(encoding="utf-8"))
        for name, token, fragment in (
                ("at", self.at, "member 'objective' must be a string"),
                ("past", self.past, _number_fragment("objective",
                                                     self.past))):
            with self.subTest(case=name):
                path = self.s.rows / f"claim-{name}.json"
                path.write_text(_number_text(claim, ("objective",), token),
                                encoding="utf-8")
                rec = self.run_refusal("handoff", "--claim-file", str(path),
                                       "--base", self.s.base)
                self.assertIn(fragment, rec["error"])

    def test_import_legacy_reads_each_event_line(self):
        for name, token, fragment in (
                ("at", self.at, "no --source-commit was supplied"),
                ("past", self.past, "undecodable event line(s): line 1: "
                 + _number_fragment("n", self.past))):
            with self.subTest(case=name):
                path = self.s.rows / f"events-{name}.jsonl"
                path.write_text(f'{{"event": "x", "n": {token}}}\n',
                                encoding="utf-8")
                rec = self.run_refusal("import-legacy", str(path))
                self.assertIn(fragment, rec["error"])


class TestTheLoader(unittest.TestCase):
    """`wire.load_json`'s numeric limit itself, in-process: the boundary
    every row above reaches through a door."""

    def test_the_integer_limit(self):
        if not LIMIT:
            self.skipTest("this interpreter reads an integer of any length")
        for sign in ("", "-"):
            for digits in (LIMIT - 1, LIMIT):
                token = sign + "9" * digits
                self.assertEqual(wire.load_json(token), int(token))
            token = sign + "9" * (LIMIT + 1)
            with self.assertRaises(wire.NumberOutOfRange) as caught:
                wire.load_json(token)
            self.assertEqual((caught.exception.path, caught.exception.pos),
                             ("", 0))
            self.assertIn(_number_fragment("", token), caught.exception.msg)

    def test_the_rest_of_the_numeric_domain(self):
        for token, value in (("1e308", 1e308), ("1e-400", 0.0), ("-0", 0),
                             ("-0.0", -0.0),
                             ("1." + "1" * 5000, 1.1111111111111112)):
            self.assertEqual(wire.load_json(token), value)
        for token in ("1e400", "-1e400", "NaN", "Infinity", "-Infinity",
                      "1" * 400 + ".5"):
            with self.subTest(token=token[:12]):
                with self.assertRaises(wire.NumberOutOfRange):
                    wire.load_json(token)

    def test_it_is_the_error_every_reader_already_types(self):
        """What the readers outside `review/` catch: `tools/loupe_approve`
        a json.JSONDecodeError, `bin/guarded-merge` a ValueError."""
        self.assertTrue(issubclass(wire.NumberOutOfRange,
                                   json.JSONDecodeError))
        self.assertTrue(issubclass(wire.NumberOutOfRange, ValueError))

    def test_the_position_and_path_name_the_first_in_the_text(self):
        if not LIMIT:
            self.skipTest("this interpreter reads an integer of any length")
        past = "9" * (LIMIT + 1)
        # A string holding the same digits, a quote and NaN comes first:
        # outside a string, every digit belongs to a number.
        text = ('{"s": "' + past + ' \\" NaN 1e400",\n "n": [1, 2.5,\n  '
                + past + ', NaN]}')
        with self.assertRaises(wire.NumberOutOfRange) as caught:
            wire.load_json(text)
        exc = caught.exception
        self.assertEqual(exc.path, "n[2]")
        self.assertEqual((exc.pos, exc.lineno, exc.colno),
                         (text.rindex(past), 3, 3))
        self.assertIn(_number_fragment("n[2]", past), exc.msg)

    def test_a_depth_past_the_interpreter_s_recursion_limit(self):
        """The decoder accepts a nesting the walk that names the path must
        follow too: it is iterative, so no RecursionError replaces the
        refusal."""
        depth = sys.getrecursionlimit() + 100
        text = "[" * depth + "NaN" + "]" * depth
        with self.assertRaises(wire.NumberOutOfRange) as caught:
            wire.load_json(text)
        self.assertEqual(caught.exception.path, "[0]" * depth)
        self.assertEqual(caught.exception.pos, depth)

    def test_a_repeat_beside_a_number_is_named_by_its_path(self):
        if not LIMIT:
            self.skipTest("this interpreter reads an integer of any length")
        text = '{"a": [' + "9" * (LIMIT + 1) + '], "b": {"c": 1, "c": 2}}'
        with self.assertRaises(wire.DuplicateMember):
            wire.load_json(text)
        self.assertEqual(wire.duplicate_member_path(text), "b.c")


class TestRespondWritesTheBody(unittest.TestCase):
    """Every member `respond` writes is inside the body's grammar: its
    input's top level is the body's, narrowed and never widened."""

    def test_respond_top_narrows_the_body(self):
        from review.cli import RESPOND_TOP, RESPOND_RECORD
        self.assertEqual(list(RESPOND_TOP), list(DISPOSITION_TOP))
        self.assertIs(RESPOND_RECORD, DISPOSITION_RECORD)
        for member, (kind, required) in RESPOND_TOP.items():
            body_kind, body_required = DISPOSITION_TOP[member]
            self.assertEqual(required, body_required, member)
            for sample in ("", "claude", "Claude Code", "a" * 40, 0, 1, -1,
                           True, None, [], {}):
                if wire.admits(kind, sample):
                    self.assertTrue(wire.admits(body_kind, sample),
                                    (member, sample))


if __name__ == "__main__":
    unittest.main()

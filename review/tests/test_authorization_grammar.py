"""The authorization envelope's CLOSED GRAMMAR (2026-09-01).

`vocab.AUTHORIZATION_REQUIRED` and `WAIVED_REQUIRED` named required member
NAMES and nothing else, and the validator checked presence, two truthy
values and the SHA wrapper/body split. Everything else about the shape was
admitted. Exact-target controls showed `loupe validate` returning NO error
when: every wrapper stamp but the tag was absent; `round`, `lineage`, `by`
and `reason` held lists and objects; a waived record's identity, reason and
authorizer held lists and objects; unknown members sat at the top level AND
inside a waived record; and the wrapper stamped `by="wrapper-person"` while
the body stated `by="body-person"`. Malformed JSON escaped `loupe validate`
as a raw `JSONDecodeError` rather than the typed refusal the protocol
promises.

That matters here more than at most boundaries: this artifact is published,
machine-emitted, machine-read, and it can authorize a PR approval. A
validation success that establishes only "something was wrapped" does not
establish the facts its success claims.

THE AUTHORITY is `vocab.AUTHORIZATION_WRAPPER_FIELDS`,
`AUTHORIZATION_FIELDS`, `AUTHORIZATION_NONEMPTY`, `AUTHORIZATION_REQUIRED`,
`WAIVED_FIELDS`, `WAIVED_REQUIRED` and `AUTHORIZATION_DUPLICATED`. Every
case below is DERIVED from it by iteration — there is no hand-maintained
list of member names in this file, deliberately: a second list of members
drifts from the first, and this repository has failed for exactly that. A
member added to the grammar acquires its missing / empty / wrong-type /
unknown / repeated cases here automatically, and
`test_the_matrix_covers_the_grammar_authority` fails by name when a kind
arrives with no recipe to exercise it.

Mutation matrix; each of these must fail this class, and each was RUN:
  1. drop the wrapper-presence check (`_a_wrapper`'s `absent`);
  2. drop the wrapper value-kind checks;
  3. drop the unknown-top-level-member check;
  4. drop the top-level type checks;
  5. ignore `AUTHORIZATION_NONEMPTY`;
  6. drop the unknown-waived-member check;
  7. drop the waived type checks;
  8. restrict `_a_split` to `sha` (the pre-grammar behaviour);
  9. restore ordinary `json.loads` in `parse_authorization`, so malformed
     JSON escapes as a JSONDecodeError instead of A-JSON-MALFORMED;
 10. read a non-object body as `{}` instead of refusing it (A-JSON-SHAPE);
 11. key the waived-record repeat by `finding_id` (round 4 F3);
 12. drop the `name` kind's grammar in `_A_ATTR_VALUE` (round 4 F4).

The limit this suite does NOT close, stated where a reader meets it: `by`
is asserted. A grammar can force the artifact to NAME an authorizer and to
name the same one twice; it cannot make that name true. `test_human_
override.py::test_the_record_is_an_assertion_not_a_proof` owns that fact.
"""
import json
import re
import unittest

from review import validate, vocab, wire
from review.tests.synth import CFG

TAG = CFG.wrapper_tag
SHA = "a" * 40
OTHER_SHA = "b" * 40
FP1 = "fp2:" + "1" * 16

#: The emitter's arguments for a valid authorization. `emit_authorization`'s
#: signature is a fixed contract (another verb calls it), so the control is
#: built by the real emitter and every refused case is this control with ONE
#: mutation applied to the bytes it produced.
EMIT = {"sha": SHA, "round_no": 1, "lineage": 1, "by": "sammy",
        "reason": "shipping over one open finding",
        "waived": [{"finding_id": "F1", "fp": FP1, "reason": "happy for now",
                    "by": "sammy"}]}

_OPEN_TAG_RE = re.compile(r"\A<[\w-]+-review-authorization(?P<attrs>[^>]*)>")


def _emit(**kw) -> str:
    return wire.emit_authorization(TAG, **{**EMIT, **kw})


def _split(text: str):
    """(open tag line, body text, closing tail) of an emitted envelope."""
    head, sep, rest = text.partition(">\n")
    body, sep2, tail = rest.rpartition("\n</")
    return head + sep, body, sep2 + tail


def _with_body(text: str, body) -> str:
    head, _old, tail = _split(text)
    if not isinstance(body, str):
        body = json.dumps(body, indent=2, ensure_ascii=False, sort_keys=True)
    return head + body + tail


def _body(text: str) -> dict:
    return json.loads(_split(text)[1])


def _attrs(text: str) -> dict:
    return wire.parse_attrs(_OPEN_TAG_RE.match(text).group("attrs"))[0]


def _edit_attrs(text: str, fn) -> str:
    m = _OPEN_TAG_RE.match(text)
    return text[:m.start("attrs")] + fn(m.group("attrs")) + text[m.end("attrs"):]


def _set_attr(text: str, name: str, value: str) -> str:
    return _edit_attrs(text, lambda a: re.sub(
        rf'\b{name}="[^"]*"', f'{name}="{value}"', a, count=1))


def _drop_attr(text: str, name: str) -> str:
    return _edit_attrs(text, lambda a: re.sub(
        rf'\s{name}="[^"]*"', "", a, count=1))


def _repeat_attr(text: str, name: str) -> str:
    return _edit_attrs(text, lambda a: re.sub(
        rf'(\b{name}="[^"]*")', r'\1 \1', a, count=1))


def _add_attr(text: str, name: str, value: str) -> str:
    return _edit_attrs(text, lambda a: f'{a} {name}="{value}"')


def _json_repeating(obj: dict, member: str) -> str:
    """`obj` as JSON text stating `member` twice — the one shape
    `json.dumps` cannot produce and `json.loads` silently collapses."""
    parts = [f"{json.dumps(k)}: {json.dumps(v)}" for k, v in obj.items()]
    parts.append(f"{json.dumps(member)}: {json.dumps(obj.get(member))}")
    return "{" + ", ".join(parts) + "}"


def _body_around(record_text: str, body: dict) -> str:
    """The body's JSON text with `waived` holding one record written as the
    given raw text — so a repeated member INSIDE a record survives."""
    others = {k: v for k, v in body.items() if k != "waived"}
    parts = [f"{json.dumps(k)}: {json.dumps(v)}" for k, v in others.items()]
    parts.append(f'"waived": [{record_text}]')
    return "{" + ", ".join(parts) + "}"


class _Base(unittest.TestCase):
    """Every case is (a mutation of) bytes the real emitter produced, and is
    judged by the same two functions `loupe validate` calls."""

    #: A valid value per JSON kind the grammar declares — the VALID side is
    #: schema-derived too, so a new kind joins the controls automatically.
    VALID = {"string": "a stated value", "integer": 2, "boolean": True,
             "waived_records": [dict(EMIT["waived"][0])]}

    #: A wrong value per kind: every JSON kind the member may NOT be. `True`
    #: is in the integer row on purpose — `bool` is an `int` in Python, and
    #: `config._check_value` is the house precedent for saying so.
    WRONG = {"string": [None, True, 3, [], {}],
             "integer": [None, True, "1", 1.5, [], {}],
             "boolean": [None, 3, "yes", [], {}],
             "waived_records": [None, True, 3, "x", {}]}

    #: A malformed value per WRAPPER attribute kind, with the code that
    #: owns the refusal. `sha` and `shape` are owned by the shared envelope
    #: grammar; this grammar checks their presence only, so that one rule
    #: keeps one home.
    MALFORMED = {
        "sha": (["not-a-sha", "", "a" * 39, "A" * 40], "E-SHA-SHAPE"),
        "shape": (["", "abc", "Z" * 16, "0" * 15], "E-SHAPE-GRAMMAR"),
        "digits": (["one", "", " 1", "-1", "1.0"], "A-WRAPPER-VALUE"),
        # A name the wrapper cannot carry (round 4 F4). `"` and `>` are not
        # here because the attribute grammar cannot even STATE them — they
        # are refused one layer up as residue or a split wrapper — so the
        # values here are the ones the parser carries and the grammar
        # refuses: blank, a control character, an opening angle bracket.
        "name": (["", "   ", "\t", "a\tb", "a<b", "a\x7fb"], "A-WRAPPER-VALUE"),
        "identity": (["", "abc", "Z" * 16, "0" * 17], "A-WRAPPER-VALUE"),
        # A lineage id (brief `keyed-lineage`): a minted `L<hex>` or a
        # legacy ordinal, both letter-or-digit-led tokens. Refused: blank,
        # a leading separator, and anything the ref and path names it keys
        # cannot carry — a space, a slash, a control character.
        "lineage_id": (["", " ", "-L1", ".1", "L 1", "L/1", "L\t1"],
                       "A-WRAPPER-VALUE"),
    }

    #: A valid-but-different wrapper value per duplicated fact, for the
    #: disagreement cases, with the code that names the split.
    OTHER = {"sha": (OTHER_SHA, "A-SHA-SPLIT"),
             "round": ("7", "A-WRAPPER-SPLIT"),
             # A lineage id, not a count, since 0.20.0 (brief
             # `keyed-lineage`): the wrapper and the body both carry the
             # STRING, so the paired control below moves a token rather than
             # an integer.
             "lineage": ("L9f8e7d6c5b", "A-WRAPPER-SPLIT"),
             "by": ("someone else", "A-WRAPPER-SPLIT")}

    def codes(self, text: str) -> list[str]:
        parsed = wire.parse_authorization(text)
        return [i.code for i in validate.validate_authorization(parsed, CFG)
                if i.level == "error"]

    def refused(self, text: str, code: str, case: str) -> None:
        got = self.codes(text)
        self.assertIn(code, got, f"{case}: refused with {got or 'nothing'}")

    def clean(self, text: str, case: str) -> None:
        self.assertEqual(self.codes(text), [], f"{case}: a valid control was "
                                               f"refused")


class TestTheAuthorityIsCovered(_Base):
    """The matrix cannot drift from the grammar, because it is generated
    from it — these are the assertions that make that a fact rather than an
    intention."""

    def test_every_declared_kind_has_a_valid_and_a_wrong_recipe(self):
        """MUTATION: add a member to `vocab.AUTHORIZATION_FIELDS` or
        `WAIVED_FIELDS` under a kind with no recipe and this fails by name,
        rather than the member joining the grammar untested."""
        kinds = set(vocab.AUTHORIZATION_FIELDS.values()) | \
            set(vocab.WAIVED_FIELDS.values())
        self.assertTrue(kinds <= set(self.VALID), f"no valid value: {kinds}")
        self.assertTrue(kinds <= set(self.WRONG), f"no wrong value: {kinds}")
        wrapper_kinds = set(vocab.AUTHORIZATION_WRAPPER_FIELDS.values())
        self.assertTrue(wrapper_kinds <= set(self.MALFORMED),
                        f"no malformed value: {wrapper_kinds}")

    def test_every_constraint_names_members_the_grammar_declares(self):
        """A constraint on a member outside the field authority could never
        fire, and a non-empty constraint on a non-string member could never
        be checked — either is a silently dead rule (the claim grammar's
        round-8 F1, one layer over)."""
        self.assertTrue(
            set(vocab.AUTHORIZATION_REQUIRED) <= set(vocab.AUTHORIZATION_FIELDS))
        self.assertTrue(set(vocab.WAIVED_REQUIRED) <= set(vocab.WAIVED_FIELDS))
        for member in vocab.AUTHORIZATION_NONEMPTY:
            self.assertEqual(vocab.AUTHORIZATION_FIELDS.get(member), "string",
                             member)
        for member in vocab.WAIVED_REQUIRED:
            self.assertEqual(vocab.WAIVED_FIELDS.get(member), "string", member)

    def test_a_duplicated_fact_is_stated_by_both_halves(self):
        """`AUTHORIZATION_DUPLICATED` is the set of facts the two halves BOTH
        state. A name in it that only one half carries would compare a value
        against nothing and pass forever."""
        for fact in vocab.AUTHORIZATION_DUPLICATED:
            self.assertIn(fact, vocab.AUTHORIZATION_WRAPPER_FIELDS, fact)
            self.assertIn(fact, vocab.AUTHORIZATION_FIELDS, fact)
            self.assertIn(fact, self.OTHER, f"{fact}: no disagreeing value")

    def test_the_wrapper_grammar_is_exactly_what_the_emitter_stamps(self):
        """The grammar is closed against the EMITTER, not against a reading
        of it: `emit_authorization` is a fixed contract another session
        calls, so an attribute it stamps that the grammar does not name
        would travel unjudged, and one the grammar requires that it never
        stamps would refuse every artifact this tool writes.

        MUTATION: add or remove an attribute in `emit_authorization`'s open
        tag and this fails, which is the only reason the required-presence
        rule below is safe to state."""
        self.assertEqual(set(_attrs(_emit())),
                         set(vocab.AUTHORIZATION_WRAPPER_FIELDS))

    def test_the_body_grammar_is_exactly_what_the_emitter_writes(self):
        self.assertEqual(set(_body(_emit())),
                         set(vocab.AUTHORIZATION_FIELDS))


class TestTheValidControls(_Base):
    """One valid control per refused class, so every refusal below is
    attributable to its own mutation and not to a body this validator would
    have refused anyway."""

    def test_the_emitted_authorization_validates(self):
        self.clean(_emit(), "the emitter's own bytes")

    def test_a_record_carrying_every_optional_member_validates(self):
        """The optional half of `WAIVED_FIELDS` is what `waive_finding`
        records when a finding was escalated, severity-stamped or parked —
        refusing it would refuse the tool's own output."""
        full = {m: (self.VALID[k] if k != "finding_id" else "F1")
                for m, k in vocab.WAIVED_FIELDS.items()}
        self.clean(_emit(waived=[full]), "every optional member present")

    def test_several_overruled_findings_validate(self):
        rec = dict(EMIT["waived"][0])
        second = {**rec, "finding_id": "F2", "fp": "fp2:" + "2" * 16}
        self.clean(_emit(waived=[rec, second]), "two overruled findings")

    def test_an_unknown_wrapper_attribute_is_accepted(self):
        """Design §3.1, asserted rather than assumed: an unlisted attribute
        NAME is deliberately not refused, because an older installation must
        be able to read an envelope carrying an attribute it has never heard
        of or every addition is a flag day. This is the one axis of the
        wrapper the grammar leaves open, and it is open on purpose.

        MUTATION: refuse unknown attributes in `_a_wrapper` and this fails —
        the case that says the opening is a decision."""
        self.clean(_add_attr(_emit(), "novel", "x"), "unknown attribute")


class TestTheTopLevelMembers(_Base):

    def test_every_required_member_is_refused_when_absent(self):
        """Derived from `AUTHORIZATION_REQUIRED`; a member added there gets
        its case here for free.

        MUTATION: drop the `missing` check and every subtest fails."""
        seen = set()
        for member in vocab.AUTHORIZATION_REQUIRED:
            body = {k: v for k, v in _body(_emit()).items() if k != member}
            with self.subTest(missing=member):
                self.refused(_with_body(_emit(), body), "A-MEMBERS",
                             f"missing {member}")
            seen.add(member)
        self.assertEqual(seen, set(vocab.AUTHORIZATION_REQUIRED))

    def test_every_member_refuses_every_wrong_json_type(self):
        """The open axis this closes: `round`, `lineage`, `by` and `reason`
        took lists and objects, and an artifact whose authorizer is `{}`
        names nobody while validating clean.

        MUTATION: drop the `_A_JSON_KIND` dispatch and every subtest fails."""
        seen = set()
        for member, kind in vocab.AUTHORIZATION_FIELDS.items():
            want = "A-WAIVED" if kind == "waived_records" else "A-TYPE"
            for wrong in self.WRONG[kind]:
                body = {**_body(_emit()), member: wrong}
                with self.subTest(member=member, wrong=wrong):
                    self.refused(_with_body(_emit(), body), want,
                                 f"{member} = {wrong!r}")
            seen.add(member)
        self.assertEqual(seen, set(vocab.AUTHORIZATION_FIELDS))

    def test_every_nonempty_member_refuses_a_blank_value(self):
        """Type alone admits `""`: a record whose reason is a space records
        that a decision was taken and not what it was.

        MUTATION: ignore `AUTHORIZATION_NONEMPTY` and every subtest fails,
        while the paired control below keeps passing — which is what
        separates this rule from the type rule above."""
        for member in vocab.AUTHORIZATION_NONEMPTY:
            for blank in ("", " ", "\t\n", "   "):
                body = {**_body(_emit()), member: blank}
                with self.subTest(member=member, blank=blank):
                    self.refused(_with_body(_emit(), body), "A-SILENT",
                                 f"{member} = {blank!r}")
            # Paired control: the same member, stated.
            body = {**_body(_emit()), member: _body(_emit())[member]}
            self.clean(_with_body(_emit(), body), f"{member} stated")

    def test_an_unknown_member_is_refused_by_name(self):
        """An unknown member was dropped in silence, so a misspelled
        `waived` produced an authorization that overruled nothing while
        reading as though it overruled something — the claim grammar's
        `stop_condition_typo` defect on a boundary that can post an
        approval. The refusal names the member, because "invalid" sends a
        reader back to diff two documents.

        MUTATION: drop the `unknown` check and both subtests fail."""
        for name in ("waived_typo", "authority"):
            body = {**_body(_emit()), name: "x"}
            with self.subTest(unknown=name):
                text = _with_body(_emit(), body)
                self.refused(text, "A-UNKNOWN", f"unknown {name}")
                parsed = wire.parse_authorization(text)
                message = " ".join(
                    i.message for i in
                    validate.validate_authorization(parsed, CFG)
                    if i.code == "A-UNKNOWN")
                self.assertIn(name, message, "the refusal does not name it")

    def test_every_member_refuses_being_stated_twice(self):
        """Derived per member: a document stating one member twice has no
        single value to judge, and `json.loads` keeps the LAST — so a body
        could state `by` twice and be judged on whichever the parser kept.

        MUTATION: restore ordinary `json.loads` in `parse_authorization` and
        every subtest fails."""
        for member in vocab.AUTHORIZATION_FIELDS:
            with self.subTest(repeated=member):
                self.refused(
                    _with_body(_emit(), _json_repeating(_body(_emit()),
                                                        member)),
                    "A-JSON-DUPLICATE", f"repeated {member}")


class TestTheWaivedRecords(_Base):

    def _with_record(self, **kw) -> str:
        rec = {**EMIT["waived"][0], **kw}
        return _emit(waived=[rec])

    def test_every_required_member_is_refused_when_absent_or_blank(self):
        """MUTATION: drop the `absent` check in `_a_waived` and every
        subtest fails — the record then names a finding it does not
        identify, or a decision with no reason attached."""
        seen = set()
        for member in vocab.WAIVED_REQUIRED:
            rec = {k: v for k, v in EMIT["waived"][0].items() if k != member}
            with self.subTest(missing=member):
                self.refused(_emit(waived=[rec]), "A-WAIVED-MEMBERS",
                             f"record missing {member}")
            for blank in ("", "   "):
                with self.subTest(blank=member):
                    self.refused(self._with_record(**{member: blank}),
                                 "A-WAIVED-MEMBERS", f"{member} = {blank!r}")
            seen.add(member)
        self.assertEqual(seen, set(vocab.WAIVED_REQUIRED))

    def test_every_member_refuses_every_wrong_json_type(self):
        """A waived record's identity, reason and authorizer took lists and
        objects: `{"fp": {}}` is a fingerprint that matches no finding, and
        `answers_escalation: "yes"` is a fact stated in a type nothing reads.

        MUTATION: drop the type loop in `_a_waived` and every subtest
        fails."""
        seen = set()
        for member, kind in vocab.WAIVED_FIELDS.items():
            for wrong in self.WRONG[kind]:
                with self.subTest(member=member, wrong=wrong):
                    self.refused(self._with_record(**{member: wrong}),
                                 "A-WAIVED-TYPE", f"{member} = {wrong!r}")
            seen.add(member)
        self.assertEqual(seen, set(vocab.WAIVED_FIELDS))

    def test_a_record_that_is_not_an_object_is_refused(self):
        for wrong in (None, True, 3, "F1", []):
            with self.subTest(element=wrong):
                self.refused(_emit(waived=[wrong]), "A-WAIVED",
                             f"waived[0] = {wrong!r}")

    def test_an_unknown_member_is_refused_by_name(self):
        """MUTATION: drop the `unknown` check in `_a_waived` and both
        subtests fail — a misspelled `reason` then records no reason while
        the record validates."""
        for name in ("resaon", "authority"):
            with self.subTest(unknown=name):
                text = self._with_record(**{name: "x"})
                self.refused(text, "A-WAIVED-UNKNOWN", f"unknown {name}")
                message = " ".join(
                    i.message for i in validate.validate_authorization(
                        wire.parse_authorization(text), CFG)
                    if i.code == "A-WAIVED-UNKNOWN")
                self.assertIn(name, message, "the refusal does not name it")

    def test_every_member_refuses_being_stated_twice(self):
        """The nested depth of the repeat rule: `load_json` refuses at every
        depth, and this is the case that proves the authorization body goes
        through it rather than around it."""
        for member in vocab.WAIVED_FIELDS:
            rec = {m: (self.VALID[k] if m != "finding_id" else "F1")
                   for m, k in vocab.WAIVED_FIELDS.items()}
            with self.subTest(repeated=member):
                body = _body_around(_json_repeating(rec, member),
                                    _body(_emit()))
                self.refused(_with_body(_emit(), body), "A-JSON-DUPLICATE",
                             f"repeated {member} in a record")

    def test_one_finding_cannot_be_overruled_twice(self):
        rec = dict(EMIT["waived"][0])
        self.refused(_emit(waived=[rec, dict(rec)]), "A-WAIVED-REPEAT",
                     "one finding, two records")

    def test_uniqueness_is_by_identity_not_by_round_scoped_label(self):
        """Round 4 F3, both polarities. A finding id is round-scoped and a
        fingerprint is the cross-round identity: two standing findings from
        different rounds may both be labelled F1 and are two findings the
        emitter validly writes, while F1 and F2 sharing one fingerprint are
        one finding stated twice.

        MUTATION: key `_a_waived`'s repeat count by `finding_id` again and
        BOTH subtests fail — the valid pair is refused and the duplicate
        identity validates."""
        rec = dict(EMIT["waived"][0])
        same_label = {**rec, "fp": "fp2:" + "2" * 16}
        with self.subTest(polarity="same label, two identities"):
            self.clean(_emit(waived=[rec, same_label]),
                       "two findings both labelled F1")
        other_label = {**rec, "finding_id": "F2"}
        with self.subTest(polarity="two labels, one identity"):
            self.refused(_emit(waived=[rec, other_label]), "A-WAIVED-REPEAT",
                         "one identity under two labels")

    def test_an_authorization_overruling_nothing_is_refused(self):
        self.refused(_emit(waived=[]), "A-WAIVED-EMPTY", "nothing overruled")


class TestTheWrapper(_Base):

    def test_every_stamped_attribute_is_refused_when_absent(self):
        """The widest hole the grammar closes: every wrapper stamp but the
        tag could be absent and validation returned nothing. The face of the
        envelope is what a reader binds to before it opens the body.

        Unlike `shape` on the older kinds, absence here cannot mean an older
        emitter — this envelope kind was born stamped.

        MUTATION: drop the `absent` check in `_a_wrapper` and every subtest
        fails."""
        seen = set()
        for attr in vocab.AUTHORIZATION_WRAPPER_FIELDS:
            with self.subTest(absent=attr):
                self.refused(_drop_attr(_emit(), attr), "A-WRAPPER-MEMBERS",
                             f"wrapper missing {attr}")
            seen.add(attr)
        self.assertEqual(seen, set(vocab.AUTHORIZATION_WRAPPER_FIELDS))

    def test_every_stamped_attribute_refuses_a_malformed_value(self):
        """Presence is not a value: `round="one"` and `tool="abc"` are
        stamps that bind nothing while looking like stamps.

        The expected code is per KIND, and `sha` and `shape` are owned by
        the shared envelope grammar — this case asserts the refusal, not a
        second copy of the rule, which is how one grammar keeps one home.

        MUTATION: drop the value dispatch in `_a_wrapper` and the digits,
        text and identity subtests fail while the sha and shape subtests
        keep passing — which is exactly the ownership split."""
        seen = set()
        for attr, kind in vocab.AUTHORIZATION_WRAPPER_FIELDS.items():
            values, code = self.MALFORMED[kind]
            for value in values:
                with self.subTest(attr=attr, value=value):
                    self.refused(_set_attr(_emit(), attr, value), code,
                                 f"{attr}={value!r}")
            seen.add(attr)
        self.assertEqual(seen, set(vocab.AUTHORIZATION_WRAPPER_FIELDS))

    def test_every_stamped_attribute_refuses_being_stated_twice(self):
        """One attribute parser serves every envelope kind, and this is the
        case that proves the authorization goes through it: a repeat would
        let one declaration hide another, the binding SHA included."""
        for attr in vocab.AUTHORIZATION_WRAPPER_FIELDS:
            with self.subTest(repeated=attr):
                self.refused(_repeat_attr(_emit(), attr), "E-ATTR-DUPLICATE",
                             f"repeated {attr}")

    def test_every_duplicated_fact_must_agree_with_the_body(self):
        """Derived from `AUTHORIZATION_DUPLICATED`. The SHA split was already
        refused; `round`, `lineage` and `by` were not — so an artifact could
        stamp one authorizer on its face and name another in its body, and
        an approval binds to whichever half its reader takes.

        MUTATION: restrict `_a_split` to `sha` and the round, lineage and by
        subtests fail while the sha subtest keeps passing — the pre-grammar
        behaviour, named."""
        seen = set()
        for fact in vocab.AUTHORIZATION_DUPLICATED:
            other, code = self.OTHER[fact]
            with self.subTest(fact=fact):
                self.refused(_set_attr(_emit(), fact, other), code,
                             f"wrapper {fact}={other!r}")
                # Paired control: both halves moved, so they agree again.
                body = {**_body(_emit()),
                        fact: int(other) if fact == "round" else other}
                self.clean(_with_body(_set_attr(_emit(), fact, other), body),
                           f"{fact} agreed at {other!r}")
            seen.add(fact)
        self.assertEqual(seen, set(vocab.AUTHORIZATION_DUPLICATED))


class TestTheBodyIsReadable(_Base):

    def test_malformed_json_is_a_typed_refusal_not_a_crash(self):
        """`loupe validate` promises a typed refusal for every defect it
        meets; a hand-edited authorization made it raise JSONDecodeError out
        of the verb that was asked to judge it.

        MUTATION: restore plain `load_json` without the JSONDecodeError arm
        in `parse_authorization` and every subtest fails with an escaping
        exception rather than an assertion."""
        for body in ('{"sha": "x"', "{bad}", "", "not json at all",
                     "{'sha': 'x'}", '{"sha": "x",}'):
            with self.subTest(body=body):
                self.refused(_with_body(_emit(), body), "A-JSON-MALFORMED",
                             f"malformed {body!r}")

    def test_every_non_object_top_level_kind_is_refused(self):
        """Enumerated rather than asserted: `object` is the one admitted
        JSON kind, and a top-level array carries no members to judge.

        MUTATION: read a non-object body as `{}` and these fail — or, worse,
        pass for the wrong reason, reporting six missing members for a
        document whose defect is that it is not an authorization body."""
        for kind, body in {"null": "null", "boolean": "true", "number": "3",
                           "string": '"x"', "array": "[]"}.items():
            with self.subTest(top_level=kind):
                self.refused(_with_body(_emit(), body), "A-JSON-SHAPE",
                             f"top-level {kind}")

    def test_an_unwrapped_body_is_not_an_authorization(self):
        self.refused('{"sha": "x"}', "A-WRAPPER", "no wrapper")


if __name__ == "__main__":
    unittest.main()

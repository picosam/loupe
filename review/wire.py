"""Wire formats: parse and emit the three envelopes (design §5.1–5.2).

Request and verdict are markdown with a wrapper tag carrying the SHA binding
(the native dialect of this repo, as exercised in rounds 1–2). The disposition
envelope is new in slice 1 and tool-emitted, so its body is JSON — nothing
hand-typed, nothing to mis-parse.

Round 1's verdict predates the wrapper; parse_verdict accepts that legacy
shape and reports wrapped=False so the validator can treat the absence as the
distinct state it is (absent != none).
"""
from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass, field

from . import paths, shape_identity, tool_identity, vocab
from .fingerprint import compute as fp_compute
from .fingerprint import legacy_v1 as fp_legacy_v1

_WRAPPER_RE = re.compile(
    r"<(?P<tag>[\w-]+)-(?P<kind>review-(?:request|verdict|disposition"
    r"|authorization))"
    r"(?P<attrs>[^>]*)>\s*(?P<body>.*?)\s*</(?P=tag)-(?P=kind)>",
    re.DOTALL,
)
_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
#: The stamped install-shape digest's complete value grammar (round 1 F3).
#: Anchored at both ends, so a value with a valid digest inside it is still
#: refused — a substring match would admit exactly the padded and prefixed
#: values the check exists to catch.
_SHAPE_RE = re.compile(r"\A[0-9a-f]{16}\Z")
_FINDING_HEAD_RE = re.compile(r"^### (?P<id>[A-Za-z0-9-]+)\s*$", re.MULTILINE)
# First `path:line` or path-like token in an Evidence field, for the anchor.
_CITATION_RE = re.compile(r"([\w./\-]+\.[\w]+|[\w/-]+):\d+")


def normalize_path(path: str) -> str:
    """The one spelling of a repository path, for joining record to record.

    Round 2 F6 needs to decide whether a reference line and a finding name the
    same file. That decision must be exact-match on a canonical form, never a
    similarity test: a fuzzy join would let unrelated bytes silently excuse a
    returning finding from the repetition breaker, which is the opposite of
    what the breaker is for. So this normalizes only what is unambiguously
    the same path written two ways — a `./` prefix, a trailing slash,
    surrounding whitespace — and leaves everything else to differ.
    """
    path = path.strip().strip("`")
    while path.startswith("./"):
        path = path[2:]
    return path.rstrip("/")


@dataclass
class Finding:
    id: str
    severity: str
    classification: str
    title: str
    evidence: str
    why: str
    required_outcome: str
    falsification: str
    fields_present: list[str] = field(default_factory=list)
    # Optional structured anchor (round-3 F8): a hunk or symbol name that
    # survives line movement. Absent on every pre-slice-2 artifact, so its
    # absence is a distinct, recorded state rather than a parse failure.
    anchor: str = ""
    # Optional declared citation targets (round-4 F7): which `token:N`
    # occurrences in this finding's prose are document citations whose line
    # number moves. Absent on every pre-round-5 artifact; absence falls back
    # to derivation from token shape, and is recorded as the weaker state it
    # is rather than being equated with a declaration.
    citations: str = ""
    # Optional gate id (round 1 F7): the deterministic gate that would have
    # caught this finding before a reviewer had to. The deterministic-
    # preventable metric is defined over exactly this label, and ingestion
    # hardcoded it to None, so the metric read a measured zero on every
    # product path while only hand-seeded events could move it. Absent is a
    # distinct state from "no gate would have caught it" and is reported as
    # such, never as zero.
    preventable_by: str = ""
    # Sweep F1: every field the block stated MORE than once, in order of the
    # repeat. Fields used to be parsed last-write-wins while `fields_present`
    # only recorded that a name had appeared, so a second, empty
    # `FALSIFICATION:` erased the named test from the parsed finding while
    # the field still counted as present — and an acceptance could then
    # record no run of a test the reviewer had visibly named. The parser
    # keeps the FIRST value and records the repeat here; the validator turns
    # any entry into an error, so a duplicated field is a defective finding
    # rather than a quiet choice between two values.
    duplicate_fields: tuple = ()

    @property
    def anchor_path(self) -> str:
        m = _CITATION_RE.search(self.evidence)
        return m.group(1) if m else ""

    @property
    def anchor_source(self) -> str:
        return "declared" if self.anchor else "derived-from-evidence"

    @property
    def citation_source(self) -> str:
        return "declared" if self.citations.strip() else "derived-from-shape"

    @property
    def cited_paths(self) -> list[str]:
        """Every repository path this finding names, canonical and sorted.

        Round 2 F6. Reference bytes were recorded with no finding attached, so
        the repetition breaker — which exempts a returning finding backed by
        NEW content-addressed evidence — could never see them: it asks for
        evidence bearing a fingerprint, and a reference line bears a path.
        This is the join. Both sides already name paths; what was missing was
        recording, on the finding, which ones it cites, so the association is
        derived from what each record states rather than guessed at read time.

        Two sources, both exact. `Citations` is a declared comma-separated
        list. Evidence prose carries `path:line` tokens, read with the same
        regex the anchor uses — so a finding whose Evidence cites
        `review/ledger.py:212-216` binds to a reference line for
        `review/ledger.py` and to nothing else. Nothing here matches on
        basename, substring or proximity: two different files that happen to
        share a name are two different files.
        """
        found = {normalize_path(m) for m in _CITATION_RE.findall(self.evidence)}
        found |= {normalize_path(c) for c in self.citations.split(",")}
        return sorted(p for p in found if p)

    def fingerprint(self, invariant_id: str = "") -> str:
        return fp_compute(self.classification, self.anchor_path, self.anchor,
                          self.title, invariant_id=invariant_id,
                          citations=self.citations)

    def legacy_fingerprint(self, invariant_id: str = "") -> str:
        """The fp1 id this finding used to carry, for alias lineage only."""
        return fp_legacy_v1(invariant_id or self.classification,
                            self.anchor_path, "", self.title)


@dataclass
class Closure:
    """A reviewer closure event carried in the verdict grammar (§5.2).

    Round-3 F3: closures existed as constants and as prose the corpus parser
    special-cased, so round-2 F4/F5/F6 could not complete their own
    falsification tests. This is their wire form:

        - fp2:abc123 sustained: the refutation does not answer the evidence
        - fp2:def456 test_amendment ratified: syntactic identity is right

    A `reclassified` closure may DECLARE where the narrowed claim went, on
    its own continuation line, in the `Field: value` idiom a finding block
    already uses (brief `convergence-blind-to-reclassification`):

        - fp2:abc123 reclassified: High to Medium; most of it is fixed
          Residue: F1

    `residue` holds the round-local finding ids of that declaration, in the
    order the reviewer wrote them. It is the DECLARED half of an edge whose
    other half — the parent — the fingerprint already carries; without it
    the descendant can only be guessed at from the note's prose, which is
    not a grammar.
    """
    fp: str
    closure: str
    outcome: str | None
    note: str
    #: Round-local finding ids declared by `Residue:`; empty when the
    #: closure declares none, which is legal — a reclassification may leave
    #: no residue at all, the rest having simply been fixed.
    residue: tuple = ()
    # Round-4 F2: the raw line, and the shape defects found while parsing it.
    # A line that does not conform is carried as a defective record, never
    # dropped — absent and unparseable are different states, and only the
    # first of them is silence. `defects` holds (code, message) pairs the
    # validator turns into errors; a record with any defect never becomes a
    # ledger event.
    raw: str = ""
    defects: tuple = ()

    @property
    def well_formed(self) -> bool:
        return not self.defects

    def as_event(self, round_no: int, answers_round: int | None = None,
                 residue_fps: list | None = None) -> dict:
        ev = {"event": "closure", "round": round_no, "fp": self.fp,
              "closure": self.closure, "note": self.note}
        if self.outcome:
            ev["outcome"] = self.outcome
        if residue_fps:
            # The declared edge, IN THE RECORD, by fingerprint — the same
            # id every other event binds to. A round-local `F1` means
            # nothing a round later, so recording the declaration verbatim
            # would leave the reader doing the resolution the writer could
            # do exactly once, here, against the verdict that carries both
            # halves (brief `convergence-blind-to-reclassification`).
            ev["residue"] = list(residue_fps)
        if answers_round is not None:
            # Round-6 F1: the round the closure actually answers, when the
            # recording path can derive it from the disposition record
            # being closed — the one thing ledger round numbers alone
            # cannot always say, since a verdict may close a prior ruling
            # AND re-raise the same fingerprint as a new one in the same
            # breath. Absent it, readers fall back to inferring from round
            # comparisons, which is exactly what admits that ambiguity.
            ev["answers_round"] = answers_round
        return ev


@dataclass
class Verdict:
    verdict: str | None
    findings: list[Finding]
    wrapped: bool
    sha: str | None
    tag: str | None
    sections: list[str]
    unavailable_references: list[str]
    body: str
    findings_none: bool = False  # a literal `None` under ## findings
    exact: bool = False  # the wrapper spans the whole document (F16)
    closures: list = field(default_factory=list)  # reviewer closures (F3)
    attr_defects: tuple = ()  # repeated / malformed wrapper attributes


@dataclass
class Request:
    tag: str | None
    attrs: dict
    sections: dict
    body: str
    wrapped: bool
    exact: bool = False  # the wrapper spans the whole document (F16)
    attr_defects: tuple = ()  # repeated / malformed wrapper attributes

    @property
    def sha(self) -> str | None:
        return self.attrs.get("sha")


@dataclass
class Disposition:
    tag: str | None
    attrs: dict
    data: dict
    wrapped: bool
    exact: bool = False  # the wrapper spans the whole document (F16)
    attr_defects: tuple = ()  # repeated / malformed wrapper attributes
    # (code, message) for a body that is unreadable, repeats a member, or
    # is not the declared shape; `data` is {} whenever there is one.
    body_defects: tuple = ()


@dataclass
class Authorization:
    """A human's recorded decision to advance with findings still open.

    Deliberately its OWN kind rather than a derived clean verdict: an
    overridden advance and a clean review are different facts, and the
    artifact that carries one must not be mistakable for the other.
    """
    tag: str | None
    attrs: dict
    data: dict
    wrapped: bool
    exact: bool = False
    attr_defects: tuple = ()
    body_defects: tuple = ()

    @property
    def sha(self) -> str:
        return str(self.data.get("sha") or self.attrs.get("sha") or "")

    @property
    def by(self) -> str:
        return str(self.data.get("by") or "")

    @property
    def waived(self) -> list:
        got = self.data.get("waived")
        return got if isinstance(got, list) else []


class NoncanonicalLineEndings(ValueError):
    """Envelope source bytes whose physical line endings are not plain LF.

    Round-4 F1: every named-file route read its envelope through
    `Path.read_text`, whose universal-newline mode turns physical CRLF and
    CR into LF before anything else sees the document. The grammar tolerates
    the converted text, so a CRLF file validated successfully — and then the
    relay, the recorded digest and the approval helper each described a
    different artefact from the one on disk: the relay carried the
    normalized text, `loupe validate` judged it, and the raw file kept its
    own, different SHA-256.

    The domain is closed by REFUSING the noncanonical physical forms rather
    than carrying them. A heredoc pasted into a shell cannot deliver a CR
    unchanged — the terminal's own line discipline translates it — so
    "accept and carry byte-identically" is a promise this carrier cannot
    keep for CR bytes, and the one honest partition is: LF rides, everything
    else refuses before any parse, validation result or relay exists. Every
    envelope the tool itself emits is LF.
    """

    def __init__(self, message: str, remedy: str = ""):
        super().__init__(message)
        self.remedy = remedy


def line_ending_form(text: str) -> str:
    """The physical line-ending form of `text`, named for a refusal message."""
    crlf = text.count("\r\n")
    cr = text.count("\r") - crlf
    lf = text.count("\n") - crlf
    present = [name for name, n in (("CRLF", crlf), ("CR", cr), ("LF", lf))
               if n]
    if not present:
        return "LF"
    if len(present) == 1:
        return present[0]
    return "mixed " + "/".join(present)


def decode_envelope(data: bytes, source: str) -> str:
    """The envelope text of `data`, or a refusal — the ONE door envelope
    bytes enter this tool by.

    Bytes in, not text: the caller must not have opened the source in a mode
    that rewrites it. `UnicodeDecodeError` is a `ValueError` and travels the
    same typed path it did when this was `read_text`.
    """
    text = data.decode("utf-8")
    if "\r" not in text:
        return text
    form = line_ending_form(text)
    where = paths.display_path(source) if source != "-" else "standard input"
    raise NoncanonicalLineEndings(
        f"{where} has {form} line endings, and an envelope is carried and "
        f"digested as the bytes it is written in — reading it would convert "
        f"them to LF, so what this tool validated, relayed and recorded "
        f"would not be the artefact on disk. Refused before any parse",
        remedy=("a person rewrites the envelope with LF line endings — "
                "their editor's line-ending setting, or a converter — and "
                "re-runs the same command; every envelope this tool emits "
                "already ends its lines with LF, so this is an envelope some "
                "other hand or transfer rewrote"))


def unwrap(text: str):
    """Return (tag, kind, attrs, body, exact) or (None, None, {}, text, False).

    `exact` is True only when the wrapper spans the WHOLE document. Round-3
    F16: a wrapper found somewhere inside surrounding text is not a bound
    envelope — arbitrary prefix or trailer bytes travel unvalidated.
    """
    m = _WRAPPER_RE.search(text)
    if not m:
        return None, None, {}, text.strip(), False
    attrs, _defects = parse_attrs(m.group("attrs"))
    exact = text.strip() == m.group(0).strip()
    return m.group("tag"), m.group("kind"), attrs, m.group("body"), exact


def parse_attrs(attr_text: str) -> tuple[dict, tuple]:
    """Wrapper attributes, FIRST value kept, plus the defects of the
    attribute text: (code, message) pairs the validator turns into errors.

    Lineage-3 round 3 (F1, sustaining round-2 F4): `dict(findall(...))` was
    last-write-wins, so a wrapper carrying two `sha` attributes bound the
    second, erased the first, and the structural preflight — which reads
    the collapsed dictionary — saw a well-formed envelope; `take` then
    fetched, probed and recorded on the account of a SHA the envelope had
    declared twice. Same class as a repeated finding field or a repeated
    section, one layer up. Every attribute is single-valued; a repeat is
    recorded here and refused there; text between the tag name and `>`
    that is not `key="value"` is residue and refused too.
    """
    attrs: dict = {}
    defects: list[tuple[str, str]] = []
    seen: dict[str, int] = {}
    for key, value in _ATTR_RE.findall(attr_text):
        seen[key] = seen.get(key, 0) + 1
        if key in attrs:
            continue
        attrs[key] = value
    for key, n in seen.items():
        if n > 1:
            defects.append(("E-ATTR-DUPLICATE",
                            f"wrapper attribute {key!r} is stated {n} "
                            f"times; every attribute is single-valued, and "
                            f"a repeat would let one declaration hide "
                            f"another (the binding SHA included)"))
    # Round 1 F3 (lineage 12). `shape` was added as a stamped attribute
    # with no value grammar, so an empty, truncated or non-hex value
    # travelled and was rendered to a human as if it were a digest. It is
    # checked HERE because this is the one attribute parser both the
    # request and the disposition readers go through — a grammar enforced
    # at two call sites is a grammar with two chances to drift — and
    # because a structural defect must refuse before any git or ledger
    # call, which everything downstream of this function is.
    #
    # ABSENCE IS ACCEPTED and is not a defect: envelopes emitted before the
    # attribute existed carry none, and §3.1's rule is that an older reader
    # meets an unknown attribute without a flag day. What is refused is a
    # PRESENT value that is not what the emitter can produce. Lowercase
    # only, because `sha256().hexdigest()` is lowercase and a mixed-case
    # value did not come from this tool.
    shape = attrs.get("shape")
    if shape is not None and not _SHAPE_RE.match(shape):
        defects.append(("E-SHAPE-GRAMMAR",
                        f"wrapper attribute 'shape' is {shape!r}, which is "
                        f"not a 16-character lowercase hex digest; an "
                        f"install-shape identity is reported to a human as "
                        f"a digest, and a value that is not one would be "
                        f"read as though it were"))
    residue = _ATTR_RE.sub("", attr_text).strip()
    if residue:
        defects.append(("E-ATTR-RESIDUE",
                        f"wrapper carries text that is not a "
                        f"`key=\"value\"` attribute: {residue!r}"))
    return attrs, tuple(defects)


def wrapper_attr_defects(text: str) -> tuple:
    """The attribute defects of the document's wrapper, or () when there
    is no wrapper (that absence is reported elsewhere)."""
    m = _WRAPPER_RE.search(text)
    if not m:
        return ()
    return parse_attrs(m.group("attrs"))[1]


def detect_kind(text: str) -> str:
    """Best-effort envelope kind: request | verdict | disposition | unknown."""
    _, kind, _, body, _exact = unwrap(text)
    if kind:
        return kind.removeprefix("review-")
    if re.search(r"^VERDICT: ", body, re.MULTILINE):
        return "verdict"
    return "unknown"


def section_key(heading: str) -> str:
    """The identity of a `## heading`: its leading word, lowercased.

    Headings carry commentary — `## Contract — invariants that apply`,
    `## Taxonomy (declared — ...)`, the legacy `## Evidence & gates run` —
    and the leading word is what every emitter and every corpus request has
    agreed on. Exact-word rather than prefix (sweep F5): `## References` is
    not the Reference section, and a heading that only starts like one is a
    malformed alias that fails as absent rather than passing as present.
    """
    m = re.match(r"[a-z]+", heading.strip().lower())
    return m.group(0) if m else ""


def section(sections: dict, name: str) -> str:
    """The content under the FIRST heading whose key is `name`, else ''.

    ONE lookup for the validator, the reference probe and the evidence
    recorder — three prefix matches on the parsed map used to live in three
    files (sweep F5), each able to answer a different section for the same
    name.
    """
    for k, v in sections.items():
        if section_key(k) == name:
            return v
    return ""


def _split_sections(body: str) -> dict[str, str]:
    """Map '## heading' -> content, in order. Preamble under ''. """
    sections: dict[str, str] = {}
    current = ""
    lines: list[str] = []
    for line in body.splitlines():
        m = re.match(r"^## (.+?)\s*$", line)
        if m:
            sections[current] = "\n".join(lines).strip()
            current, lines = m.group(1).strip().lower(), []
        else:
            lines.append(line)
    sections[current] = "\n".join(lines).strip()
    return sections


def _parse_finding_block(fid: str, block: str) -> Finding:
    values = {f: "" for f in (*vocab.FINDING_FIELDS, "Anchor", "Citations",
                              "Preventable-by")}
    present: list[str] = []
    duplicates: list[str] = []
    current_field = None
    # Sweep F1: a field is single-valued. The first statement of it is the
    # value; a repeat is recorded as a defect and its text — header value and
    # continuation lines alike — is not folded into anything, so a repeat can
    # neither replace the first value nor append to it. `absorbing` is False
    # while the parser is inside a repeated field.
    absorbing = False
    for line in block.splitlines():
        m = re.match(r"^(Severity|Classification|Title|Evidence|Why|"
                     r"Anchor|Citations|Preventable-by|Required outcome|"
                     r"FALSIFICATION):\s*(.*)$", line)
        if m:
            current_field = m.group(1)
            if current_field in present:
                duplicates.append(current_field)
                absorbing = False
                continue
            present.append(current_field)
            absorbing = True
            values[current_field] = m.group(2).strip()
        elif current_field and absorbing and line.strip():
            values[current_field] += " " + line.strip()
    return Finding(
        id=fid,
        severity=values["Severity"],
        classification=values["Classification"],
        title=values["Title"],
        evidence=values["Evidence"],
        why=values["Why"],
        required_outcome=values["Required outcome"],
        falsification=values["FALSIFICATION"],
        fields_present=present,
        anchor=values["Anchor"],
        citations=values["Citations"],
        preventable_by=values["Preventable-by"],
        duplicate_fields=tuple(duplicates),
    )


def parse_findings(text: str) -> list[Finding]:
    heads = list(_FINDING_HEAD_RE.finditer(text))
    findings = []
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        block = text[head.end():end]
        # Stop a finding block at the next '## ' section boundary.
        cut = re.search(r"^## ", block, re.MULTILINE)
        if cut:
            block = block[:cut.start()]
        findings.append(_parse_finding_block(head.group("id"), block))
    return findings


# A closures-section line: a list item, or a continuation of the note above it.
_CLOSURE_ITEM_RE = re.compile(r"^\s*[-*]\s+(?P<rest>.*\S)\s*$")
# `<fp> <term>[ <outcome>]: <note>`. Deliberately lenient about the VALUE of
# each field so that a wrong value is reported as a wrong value (round-4 F2);
# only a line whose SHAPE has no fields at all falls through to C-SHAPE.
_CLOSURE_BODY_RE = re.compile(
    r"^`?(?P<fp>\S+?)`?\s+(?P<terms>[A-Za-z_][\w ]*?)\s*:\s*(?P<note>.*)$")
_CLOSURE_FP_RE = re.compile(r"^fp\d+:[0-9a-f]{8,}$")

# The declared residue (brief `convergence-blind-to-reclassification`): a
# continuation line of the closure above it, in the `Field: value` idiom a
# finding block already uses. Case is admitted rather than required, and so
# is whitespace around the colon, because the alternative to recognising
# `residue: F1` is folding it into the note — a declaration the reviewer
# wrote, silently demoted to prose, which is the vanishing act C-UNBULLETED
# exists to prevent one line up.
_CLOSURE_RESIDUE_RE = re.compile(r"^residue\s*:\s*(?P<ids>.*)$", re.IGNORECASE)
#: A round-local finding id, in the grammar the finding heads themselves use.
_RULING_ID_RE = re.compile(r"^[A-Za-z0-9-]+$")

_CLOSURE_GRAMMAR = ("- <fingerprint> <closure>[ <outcome>]: <note>")
_RESIDUE_GRAMMAR = "Residue: <finding id>[, <finding id>…]"


def _residue_defects(ids_text: str, prior: tuple) -> tuple:
    """`Residue:`'s value as a list of round-local ids, or why it is not.

    Shape only: whether the ids NAME findings of this verdict, and whether
    the closure is one that may carry them at all, are the validator's
    (C-RESIDUE-UNKNOWN, C-RESIDUE-TERM) — the same division the fingerprint
    already has, where the parser rules on `fp<n>:<hex>` and the validator
    on whether the record it binds to exists.
    """
    ids = [t for t in re.split(r"[,\s]+", ids_text.strip()) if t]
    if prior:
        return (), (("C-RESIDUE-DUPLICATE",
                     f"a second `Residue:` line on one closure: {ids_text!r} "
                     f"after {list(prior)} — a closure declares its residue "
                     f"once, and two declarations state no order between "
                     f"them; write `{_RESIDUE_GRAMMAR}`"),)
    if not ids:
        return (), (("C-RESIDUE-ID",
                     f"`Residue:` names nothing; omit the line or write "
                     f"`{_RESIDUE_GRAMMAR}` — a declaration with no id "
                     f"claims a residue exists and refuses to say which"),)
    bad = [i for i in ids if not _RULING_ID_RE.match(i)]
    if bad:
        return (), (("C-RESIDUE-ID",
                     f"{bad} is not a round-local finding id: `Residue:` "
                     f"names the ids of findings IN THIS VERDICT "
                     f"(`{_RESIDUE_GRAMMAR}`), never a fingerprint, a round "
                     f"number or prose"),)
    if len(set(ids)) != len(ids):
        return (), (("C-RESIDUE-DUPLICATE",
                     f"{ids} names the same finding twice: an id repeated "
                     f"declares nothing the first statement of it did not"),)
    return tuple(ids), ()


def parse_closures(text: str) -> list[Closure]:
    """Parse the `## closures` section of a verdict (§5.2, round-3 F3).

    Round-4 F2: this used to return only regex matches, so a nonconforming
    line produced no record and therefore no error — invalid closure evidence
    was indistinguishable from no closure evidence, and could be omitted from
    ledger ingestion in silence. Every nonblank line now yields exactly one
    record; a line that does not conform yields a record carrying its defects.
    Absent is not none, and unparseable is neither.
    """
    out: list[Closure] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        item = _CLOSURE_ITEM_RE.match(line)
        if not item:
            # The declared residue comes FIRST, before the continuation
            # rule: a `Residue:` line is a field of the record above it,
            # never note prose, and never a bare line the note swallows.
            residue = _CLOSURE_RESIDUE_RE.match(line.strip())
            if residue:
                if not (out and out[-1].well_formed):
                    out.append(Closure(
                        fp="", closure="", outcome=None, note="", raw=line,
                        defects=(("C-SHAPE",
                                  f"`Residue:` declares where a "
                                  f"reclassified finding's remainder went, "
                                  f"so it belongs under the closure that "
                                  f"reclassified it; there is no well-formed "
                                  f"closure record above "
                                  f"{line.strip()!r}"),)))
                    continue
                ids, defects = _residue_defects(residue.group("ids"),
                                                out[-1].residue)
                out[-1].raw = f"{out[-1].raw}\n{line}"
                if defects:
                    out[-1].defects = out[-1].defects + defects
                else:
                    out[-1].residue = ids
                continue
            # A wrapped note is a legal continuation of the record above it;
            # the same convention finding fields already use. But an
            # unbulleted line that is itself closure-SHAPED — a
            # fingerprint-led `<fp> <term>: <note>` — is a record missing its
            # bullet, and absorbing it into the note above would be the
            # round-4 F2 vanishing act rebuilt by the fix for it (round-5
            # fp2:230b5241e01a1f15): a closure the reviewer wrote would
            # become part of someone else's note, silently.
            shaped = _CLOSURE_BODY_RE.match(line.strip())
            if shaped and _CLOSURE_FP_RE.match(shaped.group("fp")):
                out.append(Closure(
                    fp="", closure="", outcome=None, note="", raw=line,
                    defects=(("C-UNBULLETED",
                              f"closure-shaped line without a list bullet: "
                              f"{line.strip()!r} — a fingerprint-led record "
                              f"is a record, not a continuation of the note "
                              f"above it; write it as `{_CLOSURE_GRAMMAR}`"),)))
                continue
            if out and out[-1].well_formed:
                out[-1].note = f"{out[-1].note} {line.strip()}".strip()
                out[-1].raw = f"{out[-1].raw}\n{line}"
                continue
            out.append(Closure(
                fp="", closure="", outcome=None, note="", raw=line,
                defects=(("C-SHAPE",
                          f"not a closure record and not a continuation of "
                          f"one: {line.strip()!r}; expected "
                          f"`{_CLOSURE_GRAMMAR}`"),)))
            continue

        body = _CLOSURE_BODY_RE.match(item.group("rest"))
        if not body:
            out.append(Closure(
                fp="", closure="", outcome=None, note="", raw=line,
                defects=(("C-SHAPE",
                          f"closure line does not parse: "
                          f"{item.group('rest')!r}; expected "
                          f"`{_CLOSURE_GRAMMAR}` — the `:` before the note is "
                          f"the separator, and the note is what answers the "
                          f"author's evidence"),)))
            continue

        fp = body.group("fp")
        terms = body.group("terms").split()
        defects: list[tuple[str, str]] = []
        if not _CLOSURE_FP_RE.match(fp):
            defects.append(("C-FP",
                            f"{fp!r} is not a fingerprint: closures bind to "
                            f"the id printed in the disposition ledger "
                            f"(`fp<version>:<hex>`), never to a finding "
                            f"number, which is round-local"))
        if len(terms) > 2:
            defects.append(("C-SHAPE",
                            f"{fp}: {' '.join(terms)!r} is not "
                            f"`<closure>[ <outcome>]` — at most one outcome "
                            f"qualifier"))
        out.append(Closure(
            fp=fp, closure=terms[0] if terms else "",
            outcome=terms[1] if len(terms) > 1 else None,
            note=body.group("note").strip(), raw=line,
            defects=tuple(defects)))
    return out


def parse_verdict(text: str) -> Verdict:
    tag, kind, attrs, body, exact = unwrap(text)
    wrapped = kind == "review-verdict"
    verdict_m = re.search(r"^VERDICT: (.+?)\s*$", body, re.MULTILINE)
    verdict = verdict_m.group(1) if verdict_m else None

    sections = _split_sections(body)
    findings_src = sections.get("findings", body if not wrapped else "")
    # Legacy (round 1): no section headers at all — findings follow VERDICT.
    if "findings" not in sections and not wrapped:
        findings_src = body
    findings = parse_findings(findings_src)
    findings_none = bool(re.fullmatch(r"None\.?", sections.get("findings", "").strip()))

    unavailable = []
    unav = sections.get("unavailable references", "")
    for line in unav.splitlines():
        line = line.strip().lstrip("-").strip()
        if line:
            unavailable.append(line)

    return Verdict(
        verdict=verdict,
        findings=findings,
        wrapped=wrapped,
        sha=attrs.get("sha"),
        tag=tag,
        sections=[k for k in sections if k],
        unavailable_references=unavailable,
        body=body,
        findings_none=findings_none,
        exact=exact,
        closures=parse_closures(sections.get("closures", "")),
        attr_defects=wrapper_attr_defects(text),
    )


def parse_request(text: str) -> Request:
    tag, kind, attrs, body, exact = unwrap(text)
    return Request(
        tag=tag,
        attrs=attrs,
        sections=_split_sections(body),
        body=body,
        wrapped=kind == "review-request",
        exact=exact,
        attr_defects=wrapper_attr_defects(text),
    )


# ---------------------------------------------------------------- JSON body

class DuplicateMember(ValueError):
    """A JSON object states one member name twice (lineage-3 round 4, F1).

    `json.loads` keeps the LAST of repeated members and discards every
    earlier one before any validator sees the object — so a disposition
    body could state `"disposition": "deferred"` and then `"accepted"` and
    validate as accepted, and a machine attestation could state
    `"exit_code": 1` and then `0` and validate as green. Same class as a
    repeated finding field, section or wrapper attribute: one declaration
    hiding another. This is the ONE JSON boundary the tool reads machine
    and author JSON through; it refuses at every nesting depth and names
    the member and its path.
    """

    def __init__(self, path: str, name: str):
        super().__init__(f"duplicate JSON member {name!r} at {path or '$'}")
        self.path = path
        self.name = name


class NumberOutOfRange(json.JSONDecodeError):
    """A JSON number this reader cannot carry (lineage L369bb23a2f, round 1
    F3). The interpreter raised a plain ValueError for an integer of more
    than `sys.get_int_max_str_digits()` digits, which no reader typed:
    `respond --from-json`, `validate` and `ledger add` left by the usage
    exit naming no member. Beside it, a number past a double's range read
    as infinity, and `NaN`, `Infinity` and `-Infinity`, which JSON does not
    have, read as themselves; `respond` then wrote `Infinity` or `NaN` into
    an envelope body that is no longer JSON.

    A `json.JSONDecodeError`, so every reader that already types malformed
    JSON types this too with nothing added there; `path` is the number's
    JSON path, empty for the top level, for the readers that refuse by
    path, and `msg` is the sentence naming it."""

    def __init__(self, msg: str, doc: str, pos: int, path: str):
        super().__init__(msg, doc, pos)
        self.path = path


class _Uncarried:
    """A number token `load_json` read and cannot carry, held in its place
    until the whole text is read, so the refusal can say where it is."""

    __slots__ = ("token", "why")

    def __init__(self, token: str, why: str):
        self.token, self.why = token, why


_NON_JSON_NUMBERS = ("NaN", "Infinity", "-Infinity")


def _number(token: str):
    """The value one number token reads as, or `_Uncarried`: THE numeric
    limit, applied by the decoder's three hooks and by the locator that
    finds the token's position. An integer token is read exactly or not at
    all (the interpreter's digit limit, the one ValueError `int` raises on
    a JSON integer token); any other number reads as a finite double."""
    if token in _NON_JSON_NUMBERS:
        return _Uncarried(token, "constant")
    if not any(c in token for c in ".eE"):
        try:
            return int(token)
        except ValueError:
            return _Uncarried(token, "digits")
    value = float(token)
    if math.isinf(value):
        return _Uncarried(token, "range")
    return value


#: A JSON string or number token, for locating a number in text that has
#: already been read: outside a string, every digit belongs to a number.
_JSON_TOKEN_RE = re.compile(
    r'"[^"\\]*(?:\\.[^"\\]*)*"|-?Infinity|NaN'
    r'|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?')


def _uncarried_error(text: str, data) -> NumberOutOfRange:
    """The refusal of the FIRST number `text` cannot carry: its JSON path
    from a pre-order walk of `data` (iterative, so no depth the decoder
    accepted defeats it) and its position from the first such token in
    the text, which is the same number because both orders are the text's."""
    path, number = "", None
    stack = [("", data)]
    while stack:
        here, value = stack.pop()
        if isinstance(value, _Uncarried):
            path, number = here, value
            break
        if isinstance(value, dict):
            stack.extend((json_path(here, k), v)
                         for k, v in reversed(list(value.items())))
        elif isinstance(value, list):
            stack.extend((json_path(here, i), v)
                         for i, v in reversed(list(enumerate(value))))
    pos = next((m.start() for m in _JSON_TOKEN_RE.finditer(text)
                if m.group()[0] != '"'
                and isinstance(_number(m.group()), _Uncarried)), 0)
    where = path or "the top level"
    token = number.token
    shown = token if len(token) <= 60 else token[:57] + "..."
    remedy = "state it as a string, or as the number it should be"
    if number.why == "digits":
        msg = (f"{where} is an integer of {len(token.lstrip('-'))} digits; "
               f"this reader reads an integer of at most "
               f"{sys.get_int_max_str_digits()} digits, exactly, and refuses "
               f"a longer one rather than misread it — {remedy}")
    elif number.why == "range":
        msg = (f"{where} is the number {shown}, past the range of a "
               f"double; it would read as infinity, which JSON cannot state "
               f"— {remedy}")
    else:
        msg = (f"{where} is {token}, which is not a JSON number (JSON has "
               f"no NaN or infinity) — {remedy}")
    return NumberOutOfRange(msg, text, pos, path)


def load_json(text: str):
    """json.loads with repeated object members refused (DuplicateMember)
    at every depth, and every number this reader cannot carry refused
    (NumberOutOfRange, itself a json.JSONDecodeError); other JSON errors
    raise json.JSONDecodeError as usual. Every JSON body an envelope or a
    gate carries, and every claim, is read through this.

    THE NUMERIC LIMIT, deliberate: an integer of at most
    `sys.get_int_max_str_digits()` digits, read exactly, the same limit
    every writer of this tool writes an integer back under; any other
    number as a finite double, rounded as Python's float rounds (an
    underflow reads as zero). Refused: a longer integer, a number past a
    double's range, and `NaN`, `Infinity` and `-Infinity`, which Python's
    reader admits and JSON does not."""
    def hook(pairs):
        seen: dict = {}
        for k, v in pairs:
            if k in seen:
                raise DuplicateMember("", k)
            seen[k] = v
        return seen
    uncarried = []

    def number(token: str):
        value = _number(token)
        if isinstance(value, _Uncarried):
            uncarried.append(value)
        return value
    data = json.loads(text, object_pairs_hook=hook, parse_int=number,
                      parse_float=number, parse_constant=number)
    if uncarried:
        raise _uncarried_error(text, data)
    return data


# ------------------------------------------------------------ dialect fence

def attestation_fence(tag: str) -> str:
    """The named fence carrying the machine attestation block (§5.1).

    Named rather than a bare ```json fence so the validator finds exactly one
    block and never a code sample that happens to be JSON (round-4 F3). It
    derives from the repo's wrapper tag — ONE dialect authority for wrapper
    and fence, where §10.1 found the emitter and the validator each hardcoding
    the same literal and staying consistent only by accident.
    """
    return f"{tag}-attestations"


# ------------------------------------------------------------- reachability

# The Push:/Verify: stamp (§9bis.4, RVW-T7). ONE authority for the line
# grammar: the emitter renders through these functions and the validator
# parses through them, so the two sides can never drift into a half-renamed
# wire format — the §10.1 defect class, where emitter and validator each
# hardcoded the same literal and stayed consistent only by accident.

PUSH_LOCAL_MARKER = "LOCAL-ONLY"

_PUSH_LINE_RE = re.compile(
    r"^Push:\s+(?P<ref>refs/\S+) = (?P<sha>[0-9a-f]{40}) @ "
    r"(?P<remote>\S+) \((?P<url>[^)]*)\) — ls-remote observed after push\s*$",
    re.MULTILINE)
_PUSH_LOCAL_RE = re.compile(
    r"^Push:\s+" + PUSH_LOCAL_MARKER + r" — .*$", re.MULTILINE)



def executable_stamp(label: str, cmd) -> str:
    """An envelope line whose value is a command its reader RUNS.

    Workshop (b): `paths.py` and the command-surface inventory both named
    "the envelope's `Diff:` and `Verify:` lines" among the fields that
    "accept this type and refuse a bare string". They did not. Both were
    built by interpolating a rendered command into an f-string, and a
    `Command` erodes to a plain `str` under f-string, `+` and every other
    ordinary string operation — so the type was gone one character after it
    was created, and no door stood behind either line. The inner values were
    rendered, so nothing was unsafe; the published invariant was simply
    wider than the code, which is the exact defect three rounds of this
    lineage were spent closing elsewhere.

    Now the label and the command are joined HERE, after the check, so the
    erosion happens downstream of the door rather than upstream of it.
    """
    paths.executable(cmd, f"the envelope's `{label}:` line")
    return f"{label + ':':<8}{cmd}"

def render_push_lines(record: dict) -> list[str]:
    """The reachability stamp, from an ensure_pushed record.

    The pushed variant carries the literal verification command (§9bis.3
    rule 7: the reviewer copies, never improvises); the local-only variant
    states on the envelope's face that no other machine can fetch the target.
    """
    if record["state"] == "local-only":
        return [f"Push:   {PUSH_LOCAL_MARKER} — no remote configured; "
                f"declared by --local-only; this SHA is fetchable from no "
                f"other machine"]
    return [
        f"Push:   {record['ref']} = {record['sha']} @ {record['remote']} "
        f"({record['url']}) — ls-remote observed after push",
        # Round 3 F1's class on the reachability surface: this line IS a
        # command (rule 7 — the reviewer copies, never improvises), so every
        # field in it renders as one shell word. `take`'s own fetch recovery
        # already did; the stamp it verifies did not.
        # Round 5 F1: rendered by the one command renderer, like every
        # other field an agent runs verbatim. The `&&` is one of the two
        # operators the tool writes deliberately — an `Op`, the type that
        # says so (lineage 6 round 1, F1).
        executable_stamp("Verify", paths.command(
            paths.Lit("git"), paths.Lit("fetch"), record["url"],
            record["ref"], paths.Op("&&"), paths.Lit("git"),
            paths.Lit("cat-file"), paths.Lit("-e"), record["sha"])),
    ]


def parse_push_line(body: str) -> dict | None:
    """The reachability stamp of a request body, or None where none exists.

    None is a distinct state from local-only: the first means the envelope
    predates or evades §9bis.4, the second means the author declared — on the
    record — that no fetchable surface exists.
    """
    m = _PUSH_LINE_RE.search(body)
    if m:
        return {"state": "pushed", "ref": m.group("ref"),
                "sha": m.group("sha"), "remote": m.group("remote"),
                "url": m.group("url")}
    if _PUSH_LOCAL_RE.search(body):
        return {"state": "local-only"}
    return None


# ---------------------------------------------------------------- disposition

def emit_disposition(tag: str, verdict_sha: str, head: str, author: str,
                     round_no: int, dispositions: list[dict]) -> str:
    """Disposition envelope: binds the verdict SHA it answers AND the head SHA
    it produced (§5.2). Body is canonical JSON — machine-emitted, machine-read.
    """
    data = {
        "verdict_sha": verdict_sha,
        "head": head,
        "author": author,
        "round": round_no,
        "dispositions": dispositions,
    }
    body = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)
    open_tag = (
        f'<{tag}-review-disposition verdict_sha="{verdict_sha}" head="{head}" '
        f'author="{author}" round="{round_no}" tool="{tool_identity()}" '
        f'shape="{shape_identity()}">'
    )
    return f"{open_tag}\n{body}\n</{tag}-review-disposition>\n"


def emit_authorization(tag: str, sha: str, round_no: int, lineage: str,
                       by: str, reason: str, waived: list[dict]) -> str:
    """Authorization envelope: a named human advancing a lineage over
    findings that are still open, listing every one of them.

    Stamped with the emitting installation's identity like the other
    tool-emitted envelopes, and canonical JSON in the body for the same
    reason the disposition is — machine-emitted, machine-read.
    """
    data = {
        "sha": sha,
        "round": round_no,
        "lineage": str(lineage),
        "by": by,
        "reason": reason,
        "waived": waived,
    }
    body = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)
    open_tag = (
        f'<{tag}-review-authorization sha="{sha}" round="{round_no}" '
        f'lineage="{lineage}" by="{by}" tool="{tool_identity()}" '
        f'shape="{shape_identity()}">'
    )
    return f"{open_tag}\n{body}\n</{tag}-review-authorization>\n"


#: JSON's own names for the Python kinds `load_json` can return, so a body
#: that is the wrong top-level kind is reported in the vocabulary the
#: document is written in rather than in Python's.
_JSON_KIND_NAMES = {type(None): "null", bool: "boolean", int: "number",
                    float: "number", str: "string", list: "array",
                    dict: "object"}


def json_kind(value) -> str:
    return _JSON_KIND_NAMES.get(type(value), type(value).__name__)


def parse_authorization(text: str) -> Authorization:
    """The authorization envelope, with EVERY unreadable body converted to a
    typed defect the validator reports.

    A repeated member was already typed; malformed JSON was not, and escaped
    as a raw `json.JSONDecodeError` from the one verb whose whole promise is
    a typed refusal — so a hand-edited authorization crashed `loupe validate`
    instead of being refused by it. A top-level kind that is not an object is
    the same class of defect: `[1, 2]` carries no members to judge, and
    reading it as `{}` would report six missing members for a document whose
    real defect is that it is not an authorization body at all.
    """
    tag, kind, attrs, body, exact = unwrap(text)
    data: dict = {}
    body_defects: tuple = ()
    try:
        loaded = load_json(body)
    except DuplicateMember as exc:
        body_defects = (("A-JSON-DUPLICATE",
                         f"the authorization body states JSON member "
                         f"{exc.name!r} more than once; every member is "
                         f"single-valued and a repeat would let one "
                         f"declaration hide another"),)
    except json.JSONDecodeError as exc:
        body_defects = (("A-JSON-MALFORMED",
                         f"the authorization body is not readable JSON "
                         f"({exc.msg} at line {exc.lineno} column "
                         f"{exc.colno}); an envelope that can authorize an "
                         f"approval is refused by name, never by a crash "
                         f"inside the verb that was asked to judge it"),)
    else:
        if isinstance(loaded, dict):
            data = loaded
        else:
            body_defects = (("A-JSON-SHAPE",
                             f"the authorization body is a top-level "
                             f"{json_kind(loaded)}; an authorization is a "
                             f"JSON object stating the commit it advances, "
                             f"who decided, why, and every finding "
                             f"overruled"),)
    return Authorization(
        tag=tag, attrs=attrs, data=data,
        wrapped=kind == "review-authorization", exact=exact,
        attr_defects=wrapper_attr_defects(text), body_defects=body_defects)


# ------------------------------------------------------ the disposition body
#
# loupe-tool-feedback item 5, the envelope half (2026-10-22). `respond
# --from-json` closed the grammar of its INPUT that day; the body an
# envelope carries was still read as whatever `json.loads` returned, and
# only when it began with `{`. A body that was empty, not JSON or not an
# object read as `{}` and validated as an answer with no records; a record
# that was not an object reached an AttributeError traceback inside
# `validate_disposition`, and a list `finding_id`, a malformed body or a
# string `round` a TypeError or ValueError that left `loupe validate` and
# `loupe ledger add` by the usage exit, naming no member; and a lone
# surrogate in a payload reached the ledger's encoder after the rows before
# it were appended.
#
# One grammar, declared here, and ONE walker over it
# (`disposition_body_defects`), read by the envelope reader below and by
# `respond`'s input reader (`review.cli._respond_input`), which narrows two
# members of the top level (`review.cli.RESPOND_TOP`). Built from pairs,
# the precedent `transport._LEGACY_ENUM_TYPES` set, because `review/cli.py`
# re-exports them and its advisory scan reads a dict literal as a result
# key.

#: What an `escalated` disposition must name beside its mandatory payload
#: (§5.2): settling evidence, OR a decision authority plus the criterion it
#: applies. Each inner tuple is one sufficient set. The grammar below types
#: these members as text; `validate_disposition` judges the alternatives.
ESCALATION_ALTERNATIVES = (("evidence",), ("authority", "criterion"))

#: The body's top level, member -> (kind, required). CLOSED:
#: `emit_disposition` writes exactly these five, and so do all 306
#: envelopes kept on this machine (measured 2026-10-22). `author` is any
#: string and `round` admits 0 because `respond` writes them as it finds
#: them: the configured `[roles] author`, which no grammar closes (its
#: default is ""), and round 0 for an answer it previews with no recorded
#: round (one kept envelope carries it).
DISPOSITION_TOP = dict((
    ("head", ("commit", True)),
    ("dispositions", ("records", True)),
    ("author", ("string", False)),
    ("round", ("count", False)),
    ("verdict_sha", ("commit", False)),
))

#: One disposition record. CLOSED: the members the tool reads or derives,
#: and `note`, the one member authors were measured adding (27 of the 851
#: records kept on this machine, 2026-10-22), carried to the reviewer as
#: written. `subtype` admits null because authors write it (53 of 851).
DISPOSITION_RECORD = dict((
    ("finding_id", ("string", True)),
    ("disposition", ("string", True)),
    ("payload", ("payload", True)),
    ("subtype", ("string_or_null", False)),
    ("fingerprint", ("string", False)),
    ("severity", ("string", False)),
    ("note", ("string", False)),
))

#: The payload members the tool reads as text: every term's mandatory
#: members, accepted(test_amended)'s, and an escalation's alternatives. The
#: payload's KEY SET stays open — it is the author's own record and §5.2
#: fixes only its floor, the ruling `transport._payload_problem` states for
#: the import door — so any other payload member is carried as written.
DISPOSITION_PAYLOAD_TEXT = tuple(dict.fromkeys(
    [m for members in vocab.DISPOSITION_PAYLOADS.values() for m in members]
    + list(vocab.TEST_AMENDED_PAYLOAD)
    + [m for option in ESCALATION_ALTERNATIVES for m in option]))

#: `payload.falsification`'s members the tool reads; its key set stays open
#: for the same reason (authors were measured adding `test`).
DISPOSITION_FALSIFICATION_TEXT = (*vocab.FALSIFICATION_RECORD, "note")

#: Member kind -> what a refusal says was expected. `actor` and `round` are
#: the two narrowings `respond`'s input applies; the body uses the others.
DISPOSITION_WANT = dict((
    ("commit", "a 40-character lowercase hex commit id"),
    ("records", "an array of disposition records"),
    ("actor", vocab.ACTOR_WANT),
    ("round", "a positive integer"),
    ("count", "a non-negative integer"),
    ("string", "a string"),
    ("string_or_null", "a string or null"),
    ("payload", "an object"),
))

#: What an absent required member is FOR, so the refusal says more than
#: its name.
DISPOSITION_ROLE = dict((
    ("head", "the commit this answer produced"),
    ("dispositions", "one record per finding of the verdict"),
    ("finding_id", "the id of the verdict's finding this record answers"),
    ("disposition", "one of " + ", ".join(vocab.DISPOSITIONS)),
    ("payload", "the disposition's mandatory members"),
))

_COMMIT_RE = re.compile(r"\A[0-9a-f]{40}\Z")
_ACTOR_RE = re.compile(vocab.ACTOR_RE)
_JSON_NAME_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")


def admits(kind: str, value) -> bool:
    """Whether `value` is of the declared member `kind`. `bool` is an `int`
    in Python and is never a count here."""
    if kind == "commit":
        return isinstance(value, str) and bool(_COMMIT_RE.match(value))
    if kind == "records":
        return isinstance(value, list)
    if kind == "actor":
        return isinstance(value, str) and bool(_ACTOR_RE.match(value))
    if kind in ("round", "count"):
        return (isinstance(value, int) and not isinstance(value, bool)
                and value >= (1 if kind == "round" else 0))
    if kind == "string":
        return isinstance(value, str)
    if kind == "string_or_null":
        return value is None or isinstance(value, str)
    if kind == "payload":
        return isinstance(value, dict)
    raise AssertionError(f"no check declared for member kind {kind!r}")


def json_path(parent: str, key) -> str:
    """`parent` extended by one member name or array index, in the
    notation every refusal uses: `dispositions[2].payload.falsification`. A
    name that is not an identifier is written as a JSON string in brackets,
    escaped to ASCII, so a name the terminal cannot show is still named."""
    if isinstance(key, int):
        return f"{parent}[{key}]"
    if _JSON_NAME_RE.match(key):
        return f"{parent}.{key}" if parent else key
    return f"{parent}[{json.dumps(key)}]"


def described(value) -> str:
    """A supplied value as a refusal shows it: its JSON kind, and for a
    scalar the value itself, escaped to ASCII and shortened."""
    kind = json_kind(value)
    if isinstance(value, (dict, list)):
        return f"an {kind}"
    if value is None:
        return "null"
    shown = json.dumps(value)
    if len(shown) > 60:
        shown = shown[:57] + "..."
    return f"the {kind} {shown}"


def members_text(table: dict) -> str:
    required = [m for m, (_kind, req) in table.items() if req]
    optional = [m for m, (_kind, req) in table.items() if not req]
    return (f"{', '.join(required)} (required) and {', '.join(optional)} "
            f"(optional)")


@dataclass(frozen=True)
class BodyDefect:
    """One way a loaded disposition body is not the declared shape.

    `path` is the member's JSON path (empty for the body as a whole),
    `detail` the sentence naming it and the kind expected, and `defect` the
    class: unicode, not_object, unknown, missing or type."""
    path: str
    detail: str
    defect: str


def _wrong_kind(path: str, value, want: str) -> BodyDefect:
    return BodyDefect(path, f"{path} is {described(value)}; expected {want}",
                      "type")


def _member_defects(value: dict, where: str, table: dict, what: str):
    """One object against one closed member table: unknown members first
    (a misspelling names itself before the member it meant is reported
    absent), then absent required members, then each member's kind."""
    for member in value:
        if member not in table:
            path = json_path(where, member)
            yield BodyDefect(
                path, f"{path} is not a member of {what}, which carries "
                      f"{members_text(table)} and nothing else — a member "
                      f"the tool does not read would otherwise be dropped "
                      f"or hide the one it meant", "unknown")
    for member, (kind, required) in table.items():
        if required and member not in value:
            path = json_path(where, member)
            yield BodyDefect(
                path, f"{path} is absent; expected {DISPOSITION_WANT[kind]}"
                      f" — {DISPOSITION_ROLE[member]}", "missing")
    for member, item in value.items():
        if member in table and not admits(table[member][0], item):
            yield _wrong_kind(json_path(where, member), item,
                              DISPOSITION_WANT[table[member][0]])


def _record_defects(record, where: str):
    if not isinstance(record, dict):
        yield _wrong_kind(where, record, "an object — one disposition record")
        return
    yield from _member_defects(record, where, DISPOSITION_RECORD,
                               "a disposition record")
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return  # absent or the wrong kind, and reported as such above
    at = json_path(where, "payload")
    for member in DISPOSITION_PAYLOAD_TEXT:
        if member in payload and not isinstance(payload[member], str):
            yield _wrong_kind(json_path(at, member), payload[member],
                              "a string")
    if "falsification" not in payload:
        return
    record_at = json_path(at, "falsification")
    falsification = payload["falsification"]
    if not isinstance(falsification, dict):
        yield _wrong_kind(record_at, falsification,
                          "an object {status, mutation[, note]}")
        return
    for member in DISPOSITION_FALSIFICATION_TEXT:
        if member in falsification and \
                not isinstance(falsification[member], str):
            yield _wrong_kind(json_path(record_at, member),
                              falsification[member], "a string")


def _unencodable(value, path: str):
    """Every member name and string at every depth is UTF-8 encodable. JSON
    admits an escaped lone surrogate (`"\\ud800"`), which decodes to a
    Python string and to no Unicode scalar value; the envelope and the
    ledger are UTF-8, so it fails at the write — after `--out` was created,
    or after the rows before it were appended."""
    if isinstance(value, str):
        yield from _encoding_defect(value, path, path or "the top level")
    elif isinstance(value, dict):
        for key, item in value.items():
            here = json_path(path, key)
            yield from _encoding_defect(key, here, f"the member name {here}")
            yield from _unencodable(item, here)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _unencodable(item, json_path(path, index))


def _encoding_defect(text: str, path: str, what: str):
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        yield BodyDefect(
            path, f"{what} carries a character the envelope cannot encode "
                  f"({exc.reason} at position {exc.start}); a string must be "
                  f"UTF-8 encodable, and a lone surrogate is valid JSON but "
                  f"not a Unicode scalar value", "unicode")


def disposition_body_defects(data, top: dict = DISPOSITION_TOP):
    """Every way a loaded body is not a disposition body, in the order a
    reader that stops at the first reports it: an unencodable string, a top
    level that is not an object, then the top level's members, then each
    record's, its payload's text members and its falsification record.

    The ONE walker over the grammar above. `parse_disposition` collects
    every defect; `respond --from-json` raises the first, passing its own
    narrowed top level. A member whose kind is wrong is reported and not
    descended into, so nothing below it is judged as if it had a shape.
    May raise RecursionError on a body nested past the interpreter's
    limit; both readers catch it."""
    yield from _unencodable(data, "")
    if not isinstance(data, dict):
        yield BodyDefect(
            "", f"the top level is {described(data)}; expected an object "
                f"carrying {members_text(top)}", "not_object")
        return
    yield from _member_defects(data, "", top, "the top level")
    records = data.get("dispositions")
    if isinstance(records, list):
        for index, record in enumerate(records):
            yield from _record_defects(record, json_path("dispositions",
                                                         index))


class _AllPairs(list):
    """An object as every (name, value) pair it states, repeats kept."""


def duplicate_member_path(text: str) -> str | None:
    """The JSON path of the first repeated member, in a pre-order walk, or
    None when the text cannot be walked.

    `load_json` refuses a repeat at any depth but cannot say where: its
    hook sees one object's pairs and not their place. The text already in
    hand is parsed once more, here, only to name it, its numbers read by
    `load_json`'s own hook so a number it refuses cannot hide the path."""
    def walk(value, path):
        if isinstance(value, _AllPairs):
            names = set()
            for name, _item in value:
                if name in names:
                    return json_path(path, name)
                names.add(name)
            for name, item in value:
                found = walk(item, json_path(path, name))
                if found:
                    return found
        elif isinstance(value, list):
            for index, item in enumerate(value):
                found = walk(item, json_path(path, index))
                if found:
                    return found
        return None
    try:
        return walk(json.loads(text, object_pairs_hook=_AllPairs,
                               parse_int=_number, parse_float=_number,
                               parse_constant=_number), "")
    except (RecursionError, ValueError):
        return None


#: The item code each class of shape defect is reported under. A body this
#: tool cannot read or carry as JSON text is MALFORMED, mirroring
#: `A-JSON-MALFORMED`; one that reads but is not the declared shape is
#: SHAPE, mirroring `A-JSON-SHAPE`, at every depth, because every such
#: defect stops the per-term rules alike. A repeat keeps its own code.
_BODY_DEFECT_CODE = dict((
    ("unicode", "D-JSON-MALFORMED"),
    ("not_object", "D-JSON-SHAPE"),
    ("unknown", "D-JSON-SHAPE"),
    ("missing", "D-JSON-SHAPE"),
    ("type", "D-JSON-SHAPE"),
))


def _read_disposition_body(body: str) -> tuple[dict, tuple]:
    """(data, defects) of a disposition body: the parsed object and no
    defect, or `{}` and every defect as (code, message) — never an
    exception. A body with any defect is not read at all, so no consumer
    of `Disposition.data` meets a value whose shape was not checked."""
    if not body.strip():
        return {}, (("D-JSON-MALFORMED",
                     f"the disposition body is empty; a disposition is a "
                     f"JSON object carrying {members_text(DISPOSITION_TOP)}"
                     f", and a body that states nothing answers no "
                     f"finding"),)
    try:
        loaded = load_json(body)
        defects = list(disposition_body_defects(loaded))
    except DuplicateMember as exc:
        # The body is not read at all: a document that states one member
        # twice has no single value for the validator to judge, and reading
        # either would be choosing one declaration over the other.
        where = duplicate_member_path(body) or exc.name
        return {}, (("D-JSON-DUPLICATE",
                     f"the disposition body states JSON member "
                     f"{exc.name!r} more than once, at {where}; every "
                     f"member is single-valued and a repeat would let one "
                     f"declaration hide another"),)
    except NumberOutOfRange as exc:
        return {}, (("D-JSON-MALFORMED",
                     f"in the disposition body, {exc.msg} (line "
                     f"{exc.lineno} column {exc.colno})"),)
    except json.JSONDecodeError as exc:
        return {}, (("D-JSON-MALFORMED",
                     f"the disposition body is not readable JSON "
                     f"({exc.msg} at line {exc.lineno} column {exc.colno}); "
                     f"an envelope is refused by name, never by a crash "
                     f"inside the verb asked to judge it"),)
    except RecursionError:
        return {}, (("D-JSON-MALFORMED",
                     "the disposition body nests deeper than the reader "
                     "can follow; a disposition body needs a handful of "
                     "levels"),)
    if defects:
        return {}, tuple((_BODY_DEFECT_CODE[d.defect],
                          f"in the disposition body, {d.detail}")
                         for d in defects)
    return loaded, ()


def parse_disposition(text: str) -> Disposition:
    """The disposition envelope, with EVERY body that is not the declared
    shape (`DISPOSITION_TOP` and below) converted to typed defects the
    validator reports, and its data `{}` — the one boundary every reader of
    a disposition goes through (`validate`, `ledger add`, `respond`)."""
    tag, kind, attrs, body, exact = unwrap(text)
    data, body_defects = _read_disposition_body(body)
    return Disposition(
        tag=tag, attrs=attrs, data=data,
        wrapped=kind == "review-disposition", exact=exact,
        attr_defects=wrapper_attr_defects(text),
        body_defects=body_defects,
    )

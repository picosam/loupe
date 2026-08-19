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
import re
from dataclasses import dataclass, field

from . import vocab
from .fingerprint import compute as fp_compute
from .fingerprint import legacy_v1 as fp_legacy_v1

_WRAPPER_RE = re.compile(
    r"<(?P<tag>[\w-]+)-(?P<kind>review-(?:request|verdict|disposition))"
    r"(?P<attrs>[^>]*)>\s*(?P<body>.*?)\s*</(?P=tag)-(?P=kind)>",
    re.DOTALL,
)
_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
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
    """
    fp: str
    closure: str
    outcome: str | None
    note: str
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

    def as_event(self, round_no: int) -> dict:
        ev = {"event": "closure", "round": round_no, "fp": self.fp,
              "closure": self.closure, "note": self.note}
        if self.outcome:
            ev["outcome"] = self.outcome
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
    body_defects: tuple = ()  # repeated JSON members in the body


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

_CLOSURE_GRAMMAR = ("- <fingerprint> <closure>[ <outcome>]: <note>")


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


def load_json(text: str):
    """json.loads with repeated object members refused (DuplicateMember)
    at every depth; other JSON errors raise json.JSONDecodeError as usual.
    Every JSON body an envelope or a gate carries is read through this."""
    def hook(pairs):
        seen: dict = {}
        for k, v in pairs:
            if k in seen:
                raise DuplicateMember("", k)
            seen[k] = v
        return seen
    return json.loads(text, object_pairs_hook=hook)


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
        f"Verify: git fetch {record['url']} {record['ref']} && "
        f"git cat-file -e {record['sha']}",
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
        f'author="{author}" round="{round_no}">'
    )
    return f"{open_tag}\n{body}\n</{tag}-review-disposition>\n"


def parse_disposition(text: str) -> Disposition:
    tag, kind, attrs, body, exact = unwrap(text)
    data: dict = {}
    body_defects: tuple = ()
    if body.lstrip().startswith("{"):
        try:
            data = load_json(body)
        except DuplicateMember as exc:
            # The body is not read at all: a document that states one
            # member twice has no single value for the validator to judge,
            # and reading either would be choosing one declaration over the
            # other. The defect travels on the record; validation refuses.
            body_defects = (("D-JSON-DUPLICATE",
                             f"the disposition body states JSON member "
                             f"{exc.name!r} more than once; every member is "
                             f"single-valued and a repeat would let one "
                             f"declaration hide another"),)
    return Disposition(
        tag=tag, attrs=attrs, data=data,
        wrapped=kind == "review-disposition", exact=exact,
        attr_defects=wrapper_attr_defects(text),
        body_defects=body_defects,
    )

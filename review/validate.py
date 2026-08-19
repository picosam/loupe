"""Deterministic envelope validation (design §5.1–5.2, §7).

Every check here is mechanical. Judgments the design explicitly assigns to
reviewers (e.g. whether prose is truly one claim — F10) are NOT checked here
and never will be: a validator that pretends to count claims in prose would
be an LLM in the code path.

Results are typed items, never prose-only: {level, code, message}.
level "error" fails validation (exit 1); "notice" reports a state transition
the design mandates (e.g. blocking finding downgraded to advisory for a
missing falsification test, §5.3a) without failing the artifact.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from . import FORMER_NAMES, TOOL_NAME, vocab
from .config import Config
from .wire import (Disposition, DuplicateMember, Finding, Request, Verdict,
                   attestation_fence, load_json, parse_push_line)
# ONE section splitter for the emitter, the parser and this validator. A
# second copy here is exactly how §10.1 found the emitter and the validator
# each holding the same literal and agreeing only by accident.
from .wire import _split_sections, section as wire_section, section_key


@dataclass
class Item:
    level: str  # error | notice
    code: str
    message: str

    def as_dict(self):
        return {"level": self.level, "code": self.code, "message": self.message}


def _err(code, msg):
    return Item("error", code, msg)


def _notice(code, msg):
    return Item("notice", code, msg)


def errors_in(items: list[Item]) -> list[Item]:
    return [i for i in items if i.level == "error"]


def taxonomy_guard(cfg: Config) -> list[Item]:
    """Refuse to rule when no taxonomy is declared (§5.2, round-3 F5).

    Absent is a distinct state from clean: without a declared severity and
    classification set there is nothing to be judged by, and inventing one
    silently is exactly the round-1 defect (the reviewer imported another
    project's enums).
    """
    if cfg.taxonomy_declared:
        return []
    return [_err("T-UNDECLARED",
                 f"no taxonomy declared for this repo (looked in "
                 f"review.toml, then user config): the tool refuses to rule "
                 f"rather than inventing one (§5.2)")]


_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def accepted_tags(cfg: Config) -> list[str]:
    """Dialect tags this repo reads, current first.

    Former tool names are accepted ONLY where the repo speaks the tool's
    default dialect — a repo that declared its own tag never carried them
    (§10.1). Acceptance of a former name is a recorded state (notice), never
    silence.
    """
    if cfg.wrapper_tag == TOOL_NAME:
        return [cfg.wrapper_tag, *FORMER_NAMES]
    return [cfg.wrapper_tag]


def envelope_identity(tag: str | None, sha: str | None, exact: bool,
                      cfg: Config, kind: str,
                      attr_defects: tuple = ()) -> list[Item]:
    """Wrapper tag, SHA shape and full-document match (round-3 F16).

    A validation success must establish the configured dialect and an exact
    SHA binding. Previously any word-like tag anywhere inside surrounding
    text passed, so `evil-review-verdict` with `sha="x"` and arbitrary
    trailing bytes validated clean.
    """
    items: list[Item] = []
    accepted = accepted_tags(cfg)
    if tag is not None and tag != cfg.wrapper_tag:
        if tag in accepted:
            items.append(_notice(
                "E-TAG-LEGACY",
                f"wrapper tag {tag!r} is a former name of this tool; "
                f"accepted for pre-rename artifacts (§10.1) — envelopes "
                f"emitted now stamp {cfg.wrapper_tag!r}"))
        else:
            items.append(_err("E-TAG",
                              f"wrapper tag {tag!r} is not this repo's "
                              f"configured dialect {cfg.wrapper_tag!r}"))
    if not exact:
        items.append(_err("E-NOT-EXACT",
                          f"the {kind} wrapper does not span the whole "
                          f"document: prefix or trailing bytes would travel "
                          f"unvalidated"))
    if sha is not None and not _SHA_RE.match(sha or ""):
        items.append(_err("E-SHA-SHAPE",
                          f"sha {sha!r} is not a 40-character hex object id, "
                          f"so it binds nothing"))
    # Lineage-3 round 3: the wrapper's attribute text is a closed grammar
    # too — every attribute once, nothing but attributes between the tag
    # and `>`. The parser keeps the first value and reports the rest here.
    for code, message in attr_defects:
        items.append(_err(code, f"the {kind} {message}"))
    return items


def effective_blocking(severity: str, falsification: str,
                       cfg: Config) -> bool:
    """Whether a finding is blocking AFTER the §5.3a downgrade is applied.

    Round-3 F7: the validator used to emit a downgrade notice while every
    later rule still treated the finding as blocking, so it announced a
    transition that never happened. This function is the single authority on
    the question, used by both the verdict and the disposition rules.
    """
    if severity not in cfg.blocking_severities:
        return False
    return bool(falsification.strip())


def validate_closures(v: Verdict, answering: list[dict] | None = None
                      ) -> list[Item]:
    """Reviewer closure records in a verdict (§5.2, round-3 F3, round-4 F2).

    Each closure must name a fingerprint, a term from the closed enum, and —
    for a test amendment — ratified or contested, since silence is neither.

    Two properties round-4 F2 required beyond that. Every nonblank line in the
    section is now a record, so a malformed one fails here instead of
    vanishing; and where the dispositions being answered are known, the
    closures §5.2 makes mandatory are checked for PRESENCE, since a finding
    that dies by omission is precisely what the symmetry rule forbids.
    """
    items: list[Item] = []
    seen: set[str] = set()
    for c in v.closures:
        for code, message in c.defects:
            items.append(_err(code, message))
        if c.defects:
            continue
        if c.closure not in vocab.CLOSURES:
            items.append(_err("C-TERM",
                              f"{c.fp}: closure {c.closure!r} is not in the "
                              f"closed vocabulary {vocab.CLOSURES}"))
            continue
        if c.closure == "test_amendment":
            if c.outcome not in vocab.TEST_AMENDMENT_OUTCOMES:
                items.append(_err("C-AMENDMENT",
                                  f"{c.fp}: test_amendment must record "
                                  f"{' or '.join(vocab.TEST_AMENDMENT_OUTCOMES)}"
                                  f"; silence is neither (§5.2)"))
        elif c.outcome:
            items.append(_err("C-OUTCOME",
                              f"{c.fp}: {c.closure!r} takes no outcome "
                              f"qualifier"))
        if not c.note.strip():
            items.append(_err("C-EVIDENCE",
                              f"{c.fp}: a closure must answer the author's "
                              f"evidence, not merely restate the finding"))
        if c.fp in seen:
            items.append(_err("C-DUPLICATE",
                              f"{c.fp}: closed more than once in one verdict"))
        seen.add(c.fp)
    items.extend(_required_closures(v, answering))
    return items


def _required_closures(v: Verdict, answering: list[dict] | None) -> list[Item]:
    """§5.2: a verdict that leaves a refutation, or an accepted(test_amended),
    without a closure fails validation.

    `answering` is the disposition record set this verdict rules on — either
    parsed from the disposition envelope or read back from the ledger. With
    none supplied the requirement cannot be evaluated, and that is reported as
    its own state rather than passing quietly.
    """
    if answering is None:
        return [_notice("C-UNCHECKED",
                        "mandatory-closure presence not checked: no "
                        "dispositions supplied for this verdict to answer")]
    closed = {c.fp: c for c in v.closures if c.well_formed}
    items: list[Item] = []
    for rec in answering:
        fp = rec.get("fp") or rec.get("fingerprint") or ""
        disp = rec.get("disposition")
        got = closed.get(fp)
        if disp == "refuted" and got is None:
            items.append(_err("C-MISSING",
                              f"{fp}: the author refuted this finding; the "
                              f"verdict must answer the evidence with a "
                              f"closure (§5.2), not leave it open by "
                              f"omission"))
        if rec.get("subtype") == "test_amended":
            if got is None or got.closure != "test_amendment":
                items.append(_err("C-MISSING-AMENDMENT",
                                  f"{fp}: accepted(test_amended) requires a "
                                  f"`test_amendment` closure recording "
                                  f"ratified or contested; silence is "
                                  f"neither (§5.2)"))
    return items


# ------------------------------------------------------------------- verdict

def _reference_grammar_items(content: str) -> list[Item]:
    """Every reference line must be in the closed grammar (round 2 F5).

    A line in no recognised form used to validate clean and then vanish: the
    probe skipped what it could not parse, so a reference the author declared
    required ceased to exist between the envelope and the reviewer while the
    précis upstream still counted it. Dropping it silently was fixed first;
    this is the other half, because material the reviewer must read may not
    depend on a downstream reader noticing it is missing.

    The grammar is imported, not restated. Two copies of one syntax is the
    §10.1 defect this file already carries a comment about.
    """
    from .transport import _REF_LINE_RE
    items: list[Item] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("("):
            continue          # blank, or the emitter's "(none declared)"
        if not _REF_LINE_RE.match(line):
            items.append(_err(
                "R-REFERENCE-FORM",
                f"reference line {stripped!r} matches no form of the "
                f"reference grammar (sha256:, UNAVAILABLE, (directory ...), "
                f"or [required]/[advisory]); nothing about it can be checked "
                f"and it would not reach the reviewer"))
    return items


_VERDICT_HEADING = re.compile(r"^## (.+?)\s*$", re.M)
REQUIRED_VERDICT_SECTIONS = ("findings", "evidence checked")

# The request's §5.1 content model, in the order the emitter renders it and
# every historical request carries it: the taxonomy the reviewer rules by,
# then Claim / Evidence / Contract / Reference — four authors, four trust
# levels. Sweep F5: the verdict grammar above was closed in round 1 of
# lineage 2 (duplicates and order are defects), and the request grammar was
# not — `_split_sections` is a dict, so a second `## Reference` replaced the
# first, and a required reference the author had named could vanish between
# the raw envelope and the machine decision with zero errors. Same rule,
# second layer.
REQUIRED_REQUEST_SECTIONS = ("taxonomy", "claim", "evidence", "contract",
                             "reference")


def _request_section_grammar_items(r: Request) -> list[Item]:
    """Sweep F5: closed request-section grammar, from the raw headings.

    Every required section occurs exactly once and in the declared order;
    a duplicate is a defect (a repeat silently replaces the first when the
    body is parsed into a dict, and a required reference the author named
    can disappear before it is probed); order is checked over the required
    sections that are present, so a missing one is reported once, as
    missing, and not again as misordered.
    """
    headings = [section_key(h) for h in _VERDICT_HEADING.findall(r.body)]
    items: list[Item] = []
    for name in REQUIRED_REQUEST_SECTIONS:
        count = headings.count(name)
        if count > 1:
            items.append(_err("R-SECTION-DUPLICATE",
                              f"'## {name.title()}' appears {count} times; "
                              f"exactly once is required — a repeat replaces "
                              f"the first when parsed, so material declared "
                              f"under the earlier one is not what the "
                              f"reviewer or the reference probe sees"))
    firsts: list[str] = []
    for h in headings:
        if h in REQUIRED_REQUEST_SECTIONS and h not in firsts:
            firsts.append(h)
    expected = [n for n in REQUIRED_REQUEST_SECTIONS if n in firsts]
    if firsts != expected:
        items.append(_err("R-SECTION-ORDER",
                          f"required sections appear as {firsts} but the "
                          f"declared order is "
                          f"{list(REQUIRED_REQUEST_SECTIONS)}"))
    return items


def _section_grammar_items(v: Verdict) -> list[Item]:
    """Round 1 F2: the verdict's published shape was never enforced.

    `## findings` then `## evidence checked` are declared mandatory and
    ordered in every emitted request's Hand-back block, but validation only
    ever read the findings it could parse. A clean verdict carrying no
    evidence record therefore validated — and a clean verdict is
    merge-authorizing text, so "what did you actually check" is the one thing
    it may not omit. Reversed, duplicated and empty sections passed equally.

    Duplicates are counted from the headings themselves rather than from the
    parsed section map, because that map is a dict: a second `## findings`
    silently replaced the first instead of being a detectable defect.
    """
    if not v.wrapped:
        return []                 # V-WRAPPER already says it is not bindable
    headings = [h.strip().lower() for h in _VERDICT_HEADING.findall(v.body)]
    sections = _split_sections(v.body)
    items: list[Item] = []

    for name in REQUIRED_VERDICT_SECTIONS:
        count = headings.count(name)
        if count == 0:
            items.append(_err("V-SECTION-MISSING",
                              f"no '## {name}' section: the hand-back shape "
                              f"declares it mandatory (§5.2)"))
        elif count > 1:
            items.append(_err("V-SECTION-DUPLICATE",
                              f"'## {name}' appears {count} times; exactly "
                              f"once is required, and a repeat silently "
                              f"replaces the first when parsed"))
        elif not sections.get(name, "").strip():
            items.append(_err("V-SECTION-EMPTY",
                              f"'## {name}' is present but empty; a declared "
                              f"section with no content states nothing"))

    ordered = [h for h in headings if h in REQUIRED_VERDICT_SECTIONS]
    firsts: list[str] = []
    for h in ordered:
        if h not in firsts:
            firsts.append(h)
    expected = [n for n in REQUIRED_VERDICT_SECTIONS if n in firsts]
    if len(firsts) == len(expected) and firsts != expected:
        items.append(_err("V-SECTION-ORDER",
                          f"sections appear as {firsts} but the declared "
                          f"order is {list(REQUIRED_VERDICT_SECTIONS)}"))
    return items


def validate_verdict(v: Verdict, cfg: Config,
                     answering: list[dict] | None = None) -> list[Item]:
    items: list[Item] = taxonomy_guard(cfg)
    if items:
        return items

    if not v.wrapped:
        items.append(_err("V-WRAPPER", "no wrapper tag; the verdict is not "
                          "machine-bindable (round-1 defect class)"))
    elif not v.sha:
        items.append(_err("V-SHA", "wrapper carries no sha attribute"))
    else:
        items.extend(envelope_identity(v.tag, v.sha, v.exact, cfg, "verdict",
                                       v.attr_defects))

    items.extend(_section_grammar_items(v))

    if v.verdict is None:
        items.append(_err("V-VERDICT-MISSING",
                          "no 'VERDICT: ...' line found"))
    elif v.verdict not in vocab.VERDICTS:
        items.append(_err("V-VERDICT-STRING",
                          f"verdict {v.verdict!r} is not one of {vocab.VERDICTS}"))
    else:
        first = next((ln for ln in v.body.splitlines() if ln.strip()), "")
        if not first.startswith("VERDICT: "):
            items.append(_err("V-VERDICT-FIRST",
                              "VERDICT must be the first meaningful line"))

    if v.verdict == vocab.VERDICT_CLEAN:
        if v.findings:
            items.append(_err("V-CLEAN-FINDINGS",
                              "clean to advance may not carry findings"))
        elif not v.findings_none:
            items.append(_err("V-CLEAN-NONE",
                              "clean to advance must carry exactly 'None' "
                              "under ## findings"))

    if v.unavailable_references and v.verdict == vocab.VERDICT_CLEAN:
        items.append(_err("V-UNAVAILABLE-CLEAN",
                          "material unavailability maps to 'changes requested' "
                          "(§5.1); a clean verdict may not carry "
                          "unavailable references"))

    items.extend(validate_closures(v, answering))

    sev_rank = {s: i for i, s in enumerate(cfg.severities)}
    expected_ids = [f"F{i}" for i in range(1, len(v.findings) + 1)]
    actual_ids = [f.id for f in v.findings]
    if v.findings and actual_ids != expected_ids:
        items.append(_err("V-IDS",
                          f"finding ids must be sequential {expected_ids[:3]}...; "
                          f"got {actual_ids}"))

    last_rank = -1
    gate_ids = cfg.gate_ids
    for f in v.findings:
        missing = [x for x in vocab.FINDING_FIELDS if x not in f.fields_present]
        if missing:
            items.append(_err("V-FIELDS", f"{f.id}: missing fields {missing}"))
        # Sweep F1: presence is not single-valuedness. A field stated twice
        # used to pass this check on its first statement while the parsed
        # value was its last — an empty second `FALSIFICATION:` deleted the
        # named test from the finding the disposition validator saw, so an
        # acceptance could carry no run record for a test the reviewer had
        # visibly named. Every finding field, required or optional, occurs
        # at most once; a repeat is a defect, never a choice between values.
        for dup in dict.fromkeys(f.duplicate_fields):
            items.append(_err("V-FIELD-DUPLICATE",
                              f"{f.id}: '{dup}:' is stated "
                              f"{f.fields_present.count(dup) + f.duplicate_fields.count(dup)} "
                              f"times; every finding field is single-valued, "
                              f"and a repeat would let one statement hide "
                              f"another"))
        # Round 1 F7: the optional gate label the preventable metric is
        # defined over. Validated against the repo's declared ids so a
        # mistyped gate cannot silently become an uncountable label — the
        # metric would then read the same as never having been captured.
        if f.preventable_by and gate_ids and f.preventable_by not in gate_ids:
            items.append(_err("V-PREVENTABLE-GATE",
                              f"{f.id}: Preventable-by {f.preventable_by!r} "
                              f"is not a declared gate id {gate_ids}"))
        if f.severity not in sev_rank:
            items.append(_err("V-SEVERITY",
                              f"{f.id}: severity {f.severity!r} not in declared "
                              f"taxonomy {cfg.severities}"))
        else:
            if sev_rank[f.severity] < last_rank:
                items.append(_err("V-ORDER",
                                  f"{f.id}: findings must be ordered by severity"))
            last_rank = max(last_rank, sev_rank[f.severity])
        if f.classification not in cfg.classifications:
            items.append(_err("V-CLASSIFICATION",
                              f"{f.id}: classification {f.classification!r} not in "
                              f"declared taxonomy"))
        if f.severity in cfg.blocking_severities and not f.falsification.strip():
            items.append(_notice("V-DOWNGRADE",
                                 f"{f.id}: blocking finding without a "
                                 f"falsification test — effective severity is "
                                 f"advisory by rule (§5.3a), not by "
                                 f"negotiation; disposition rules follow the "
                                 f"advisory severity"))
    return items


# -------------------------------------------------------------- attestations

# Every signal §5.1 requires of a gate attestation, with the check that makes
# removing or corrupting it fail on its own. One table, so a field cannot be
# required in prose and unchecked in code (round-4 F3).
_SHA_OK = _SHA_RE.match
_ATTESTATION_FIELDS = (
    ("id", "A-ID", lambda v: isinstance(v, str) and bool(v.strip()),
     "a non-empty gate id"),
    ("command", "A-COMMAND", lambda v: isinstance(v, str) and bool(v.strip()),
     "the exact command that ran"),
    ("exit_code", "A-EXIT",
     lambda v: isinstance(v, int) and not isinstance(v, bool),
     "an integer exit code — the whole point of the attestation"),
    ("tool_version", "A-TOOL-VERSION",
     lambda v: isinstance(v, str) and bool(v.strip()),
     "the identity of the executable that ran"),
    ("target_sha", "A-TARGET-SHA", lambda v: isinstance(v, str) and _SHA_OK(v),
     "the 40-hex commit this attestation claims to be about"),
    ("executed_sha", "A-EXECUTED-SHA",
     lambda v: isinstance(v, str) and _SHA_OK(v),
     "the 40-hex commit the gate actually ran against"),
    ("tree", "A-TREE", lambda v: v in ("clean", "dirty"),
     "tree cleanliness, exactly 'clean' or 'dirty'"),
    ("binding", "A-BINDING",
     lambda v: isinstance(v, str) and bool(v.strip()),
     "'bound', or 'unbound: <reason>' naming why not"),
    ("duration_s", "A-DURATION",
     lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
     "how long the gate took"),
    ("output", "A-OUTPUT",
     lambda v: isinstance(v, dict)
     and isinstance(v.get("sha256"), str) and bool(v.get("sha256"))
     and isinstance(v.get("bytes"), int)
     and isinstance(v.get("pointer"), str) and bool(v.get("pointer")),
     "a pointer to the full output, with its digest and size"),
)

def parse_attestations(evidence: str, tags: list[str] | None = None):
    """(records, error, tag) for the attestation block in an Evidence section.

    `tags` is the ordered dialect list from accepted_tags(); the returned tag
    names which dialect's fence matched, so a former-name fence is a state the
    caller can record rather than a silent equivalence. The fence name derives
    from the tag (wire.attestation_fence) — never a hardcoded literal, which
    is how the emitter and validator once held two copies of one fact (§10.1).
    """
    tags = tags or [TOOL_NAME]
    for tag in tags:
        fence = attestation_fence(tag)
        m = re.search(r"```" + re.escape(fence) + r"\s*\n(?P<body>.*?)\n```",
                      evidence or "", re.DOTALL)
        if not m:
            continue
        try:
            data = load_json(m.group("body"))
        except DuplicateMember as exc:
            # Lineage-3 round 4 F1: `"exit_code": 1` then `"exit_code": 0`
            # in one record parsed as 0 and validated green. Refused, and
            # the member is named.
            return (None,
                    f"the attestation block states JSON member "
                    f"{exc.name!r} more than once in one record; every "
                    f"member is single-valued, and a repeat would let a "
                    f"failing result hide behind a passing one",
                    tag)
        except json.JSONDecodeError as exc:
            return (None,
                    f"the attestation block is not valid JSON ({exc.msg})",
                    tag)
        if not isinstance(data, list):
            return None, "the attestation block must be a JSON array", tag
        return data, None, tag
    return None, f"no ```{attestation_fence(tags[0])} block", None


def validate_attestations(evidence: str, cfg: Config,
                          request_sha: str | None = None) -> list[Item]:
    """§5.1 attestations, checked one field at a time (round-4 F3, F4).

    Previously this checked that an Evidence *section* existed. Removing the
    exit code and the target SHA from an emitted request therefore produced
    zero errors — a validator that confirms the container and never the
    contents. Every required signal is now independently fatal, and an
    attestation the runner could not bind to the executed tree is rejected
    rather than displayed, because §5.1 already says an attestation from a
    different commit is not an attestation.

    RVW-T2 closed the three ways a record that passed all of that could
    still lie: attestations now bind to the REQUEST's SHA when the caller
    supplies it, not merely to each other, so a wholesale block copied from
    another commit fails; `blocking` is decided by the manifest and never by
    the record, which previously could downgrade its own failure to
    advisory; and a not-run record meets its own evidence requirements — a
    gate id, a reason, and none of the fields only a run could produce.
    """
    records, err, matched = parse_attestations(evidence, accepted_tags(cfg))
    if err is not None:
        if not cfg.gates:
            # Nothing declared, nothing to attest. Distinct from a manifest
            # whose attestations went missing, and reported as its own state.
            return [_notice("A-NO-MANIFEST",
                            "no gate manifest declared, so the request "
                            "carries no attestations (§5.1: absent, not "
                            "clean)")]
        return [_err("A-BLOCK", f"{err}: the §5.1 Evidence block is machine "
                                f"attestations, and {len(cfg.gates)} gates "
                                f"are declared in this repo's manifest")]

    items: list[Item] = []
    if matched is not None and matched != cfg.wrapper_tag:
        items.append(_notice(
            "A-FENCE-LEGACY",
            f"attestation fence carries the former name "
            f"{attestation_fence(matched)!r}; accepted for pre-rename "
            f"artifacts (§10.1) — the emitter now writes "
            f"{attestation_fence(cfg.wrapper_tag)!r}"))
    # RVW-T2(b): blocking is looked up here, in the declared manifest, for
    # every decision below. What the record says about itself decides
    # nothing, and a record contradicting the manifest is its own defect.
    manifest_blocking = {g["id"]: bool(g.get("blocking", False))
                         for g in cfg.gates}
    attested = set()
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            items.append(_err("A-SHAPE", f"attestation {i} is not an object"))
            continue
        gate_id = rec.get("id") if isinstance(rec.get("id"), str) else f"#{i}"
        attested.add(rec.get("id"))
        blocking = manifest_blocking.get(rec.get("id"), False)
        if "blocking" in rec and bool(rec["blocking"]) != blocking:
            items.append(_err(
                "A-BLOCKING-CLAIM",
                f"{gate_id}: the record declares blocking="
                f"{rec['blocking']!r} but the manifest says {blocking}; "
                f"blocking is the manifest's to decide, never the "
                f"record's (§5.1, RVW-T2)"))
        if "error" in rec:
            # A gate that could not run is a recorded state, not a pass —
            # and RVW-T2(c): the one path that means "no evidence" meets its
            # own evidence requirements rather than skipping them all.
            problems = []
            if not (isinstance(rec.get("id"), str) and rec["id"].strip()):
                problems.append("no gate id")
            if not (isinstance(rec.get("error"), str)
                    and rec["error"].strip()):
                problems.append("no reason in 'error'")
            ran_only = [k for k in ("exit_code", "output", "duration_s")
                        if k in rec]
            if ran_only:
                problems.append(f"carries run-only fields {ran_only}, which "
                                f"contradict 'could not run'")
            for problem in problems:
                items.append(_err("A-NOT-RUN-SHAPE",
                                  f"{gate_id}: not-run record: {problem}"))
            level = _err if blocking else _notice
            items.append(level("A-NOT-RUN",
                               f"{gate_id}: {rec.get('error')} — uncaptured "
                               f"is not clean (§5.1)"))
            continue
        for name, code, ok, expected in _ATTESTATION_FIELDS:
            if name not in rec:
                items.append(_err(code, f"{gate_id}: attestation omits "
                                        f"{name!r} ({expected})"))
            elif not ok(rec[name]):
                items.append(_err(code, f"{gate_id}: {name}={rec[name]!r} is "
                                        f"not {expected}"))
        if rec.get("binding") != "bound" and isinstance(rec.get("binding"), str):
            items.append(_err("A-UNBOUND",
                              f"{gate_id}: {rec['binding']}. An attestation "
                              f"from a different commit or a dirty tree is "
                              f"not an attestation (§5.1)"))
        # A gate that RAN and FAILED. Caught the first time this validator met
        # a real emission: the nested test suite exited 1, the attestation
        # honestly recorded `exit_code: 1`, and the request validated clean
        # anyway — the same container-versus-contents defect round-4 F3 named,
        # committed by the fix for it. A blocking gate is blocking.
        if rec.get("exit_code") not in (None, 0):
            level = _err if blocking else _notice
            items.append(level("A-FAILED",
                               f"{gate_id}: exited {rec['exit_code']}. A "
                               f"failing "
                               f"{'blocking ' if blocking else ''}"
                               f"gate does not become evidence by being "
                               f"reported accurately"))
        if rec.get("target_sha") != rec.get("executed_sha"):
            items.append(_err("A-SHA-MISMATCH",
                              f"{gate_id}: attests target "
                              f"{rec.get('target_sha')} but ran against "
                              f"{rec.get('executed_sha')}"))
        # RVW-T2(a): internal consistency is not request binding. A block of
        # attestations that agree with EACH OTHER about some other commit
        # validated here before this check existed.
        if request_sha is not None and rec.get("target_sha") != request_sha:
            items.append(_err(
                "A-REQUEST-SHA",
                f"{gate_id}: attests target {rec.get('target_sha')} but the "
                f"request binds {request_sha}: a wholesale attestation block "
                f"from another commit is not this request's evidence "
                f"(§5.1, RVW-T2)"))

    declared = set(cfg.gate_ids)
    missing = sorted(declared - attested)
    extra = sorted(x for x in attested - declared if isinstance(x, str))
    if missing:
        items.append(_err("A-MISSING",
                          f"declared gates with no attestation: {missing} — a "
                          f"gate absent from the Evidence block is a gate "
                          f"nobody ran"))
    if extra:
        items.append(_err("A-UNDECLARED",
                          f"attestations for gates not in the manifest: "
                          f"{extra}"))
    return items


# ------------------------------------------------------------------- request

_DIFF_SHAPE_RE = re.compile(
    r"(\d+)\s+files?,\s+(\d+)\s+insertions?[^\d]+(\d+)\s+deletions?")


def validate_request(r: Request, cfg: Config,
                     recomputed_shape: tuple[int, int, int] | None = None,
                     round_cap: int | None = None,
                     structural_only: bool = False) -> list[Item]:
    """Validate a request. Two phases share this one function (round-2 F4):

    `structural_only=True` runs the TARGET-INDEPENDENT grammar — wrapper
    present and exact, SHA shape, required attributes, no self-review, the
    closed section grammar and content model, reference grammar, diff
    shape, reachability stamp syntax — every rule that needs no
    configuration to judge. `take` runs it before any git call, so a
    defective envelope cannot make the reviewer's clone fetch from a URL the
    envelope chose. The default runs everything, including the rules the
    target commit's own configuration governs — taxonomy, wrapper dialect,
    permitted and rejected roles, the cap in force, the gate manifest — which
    `take` applies after the fetch, against that configuration.
    """
    items: list[Item] = [] if structural_only else taxonomy_guard(cfg)
    if items:
        return items
    if not r.wrapped:
        items.append(_err("R-WRAPPER", "no wrapper tag"))
        return items
    identity = envelope_identity(r.tag, r.attrs.get("sha"), r.exact, cfg,
                                 "request", r.attr_defects)
    if structural_only:
        # The tag is the repo's declared dialect: config, not grammar.
        identity = [i for i in identity if not i.code.startswith("E-TAG")]
    items.extend(identity)

    for attr in ("sha", "author", "reviewer", "round"):
        if not r.attrs.get(attr):
            items.append(_err("R-ATTR", f"wrapper missing attribute {attr!r}"))
    if r.attrs.get("round") and not re.fullmatch(r"[1-9]\d*",
                                                 r.attrs["round"]):
        items.append(_err("R-ROUND",
                          f"wrapper round {r.attrs['round']!r} is not a "
                          f"positive integer, so it names no round"))

    author = r.attrs.get("author", "").lower()
    reviewer = r.attrs.get("reviewer", "").lower()
    if author and author == reviewer:
        items.append(_err("R-SELF-REVIEW",
                          "author == reviewer: an agent never reviews its own "
                          "diff"))
    if not structural_only and \
            reviewer in [x.lower() for x in cfg.roles["rejected_reviewers"]]:
        items.append(_err("R-REVIEWER-REJECTED",
                          f"reviewer {reviewer!r} is rejected pending a role "
                          f"decision (design §7, §10.7)"))
    permitted = ([] if structural_only
                 else [x.lower() for x in cfg.roles["permitted_reviewers"]])
    if permitted and reviewer and reviewer not in permitted:
        items.append(_err("R-REVIEWER-UNPERMITTED",
                          f"reviewer {reviewer!r} not permitted by repo config"))
    permitted_a = ([] if structural_only
                   else [x.lower() for x in cfg.roles["permitted_authors"]])
    if permitted_a and author and author not in permitted_a:
        items.append(_err("R-AUTHOR-UNPERMITTED",
                          f"author {author!r} not permitted by repo config"))

    cap = cfg.round_cap if round_cap is None else round_cap
    round_no = r.attrs.get("round", "")
    if not structural_only and round_no.isdigit() and int(round_no) > cap:
        items.append(_err("R-BUDGET",
                          f"round {round_no} exceeds the round cap {cap}: the "
                          f"budget breaker escalates to the user instead of "
                          f"emitting. Authorize this lineage explicitly with "
                          f"`{TOOL_NAME} ledger authorize-cap --to {round_no} "
                          f"--reason \"...\"` if the loop should continue"))

    # Sweep F5: the closed section grammar first — duplicates and order are
    # judged from the raw headings, before any content is read through the
    # parsed map that a duplicate would have overwritten.
    items.extend(_request_section_grammar_items(r))

    # Headings may carry commentary ("## Taxonomy (declared — ...)"):
    # match on the leading word (section_key).
    tax = wire_section(r.sections, "taxonomy")
    if not tax:
        # Absent taxonomy: the reviewer must refuse to rule (§5.2); an emitted
        # request without one is invalid outright.
        items.append(_err("R-TAXONOMY", "no declared taxonomy section"))
    elif not structural_only:
        for sev in cfg.severities:
            if sev not in tax:
                items.append(_err("R-TAXONOMY-SEV",
                                  f"declared taxonomy omits severity {sev!r}"))
        for cls in cfg.classifications:
            if cls not in tax:
                items.append(_err("R-TAXONOMY-CLS",
                                  f"declared taxonomy omits classification "
                                  f"{cls!r}"))

    # §5.1's content model: Claim / Evidence / Contract / Reference, each a
    # different author and trust level. Round-3 F4: validating only the
    # wrapper meant a request carrying none of them passed with zero errors,
    # so `validate` returning 0 said nothing about §5.1 conformance.
    for section, code in (("claim", "R-CLAIM"),
                          ("evidence", "R-EVIDENCE"),
                          ("contract", "R-CONTRACT"),
                          ("reference", "R-REFERENCE")):
        content = wire_section(r.sections, section)
        if not content.strip():
            items.append(_err(code, f"no '## {section.title()}' content: the "
                                    f"§5.1 content model requires it"))
        elif section == "reference":
            items.extend(_reference_grammar_items(content))

    claim = wire_section(r.sections, "claim")
    if claim and "objective" not in claim.lower():
        items.append(_err("R-OBJECTIVE",
                          "the Claim must state an objective / decision "
                          "boundary, or the reviewer reviews the wrong "
                          "question (§5.1)"))
    if claim and "risk" not in claim.lower():
        items.append(_err("R-RISK",
                          "the Claim must carry a self-assessed risk with a "
                          "reason: it feeds tiering and is falsifiable in "
                          "hindsight (§5.1)"))

    evidence = wire_section(r.sections, "evidence")
    if evidence and "NOT captured" not in evidence:
        items.append(_err("R-UNCAPTURED",
                          "the Evidence block must enumerate what was NOT "
                          "captured: uncaptured is not clean (§5.1)"))
    if "author-asserted" in evidence:
        items.append(_err("R-ASSERTION-AS-EVIDENCE",
                          "Evidence carries machine attestations only; "
                          "author assertions belong under Claim or NOT "
                          "captured (§5.1, round-3 F10)"))
    if not structural_only:
        # The attestation block is judged against the declared gate
        # manifest — the target commit's, once take has fetched it.
        items.extend(validate_attestations(evidence, cfg,
                                           request_sha=r.attrs.get("sha")))

    reference = wire_section(r.sections, "reference")
    if reference and "sha256:" not in reference and \
            "UNAVAILABLE" not in reference:
        items.append(_err("R-DIGEST",
                          "the reference manifest carries no content digest: "
                          "a pointer without a digest is a rumour (§5.1)"))

    shape_m = _DIFF_SHAPE_RE.search(r.body)
    if not shape_m:
        items.append(_err("R-DIFF-SHAPE",
                          "no machine-readable diff shape "
                          "('N files, I insertions, D deletions')"))
    elif recomputed_shape is not None:
        stated = tuple(int(x) for x in shape_m.groups())
        if stated != recomputed_shape:
            items.append(_err("R-DIFF-SHAPE-DRIFT",
                              f"stated diff shape {stated} != recomputed "
                              f"{recomputed_shape} (hand-counted numbers are "
                              f"the round-1 defect class)"))

    # §9bis.4 (RVW-T7): a SHA the reviewer cannot fetch is not a review
    # target, so a request must stamp the observed reachability of its own
    # target. Local-only is a recorded state, not an error — the flag put it
    # on the envelope's face, which is exactly where it belongs.
    push = parse_push_line(r.body)
    if push is None:
        items.append(_err("R-REACH",
                          "no reachability stamp ('Push: ...'): a SHA the "
                          "reviewer cannot fetch is not a review target "
                          "(§9bis.4) — the emitter pushes the reviewed "
                          "branch, observes the remote ref via ls-remote, "
                          "and stamps what it observed"))
    elif push["state"] == "local-only":
        items.append(_notice("R-LOCAL",
                             "target declared LOCAL-ONLY: no remote exists "
                             "and the SHA is fetchable from no other "
                             "machine, so the reviewer must share this "
                             "clone (§9bis.4)"))
    elif push["sha"] != (r.attrs.get("sha") or ""):
        items.append(_err("R-REACH-SHA",
                          f"the Push stamp observed {push['sha']} but the "
                          f"wrapper binds {r.attrs.get('sha')}: the pushed "
                          f"remote ref does not carry the envelope's target "
                          f"(§9bis.4)"))
    return items


# --------------------------------------------------------------- disposition

def _payload_items(fid: str, disp: str, subtype: str | None,
                   payload: dict) -> list[Item]:
    items: list[Item] = []
    required = vocab.DISPOSITION_PAYLOADS.get(disp, ())
    missing = [k for k in required if not str(payload.get(k, "")).strip()]
    if missing:
        items.append(_err("D-PAYLOAD",
                          f"{fid}: disposition {disp!r} missing mandatory "
                          f"payload {missing}"))
    if disp == "escalated":
        has_evidence = bool(str(payload.get("evidence", "")).strip())
        has_authority = bool(str(payload.get("authority", "")).strip()) and \
            bool(str(payload.get("criterion", "")).strip())
        if not (has_evidence or has_authority):
            items.append(_err("D-ESCALATION",
                              f"{fid}: escalation must name either settling "
                              f"evidence or a decision authority plus "
                              f"criterion (§5.2)"))
    if subtype:
        if disp != "accepted" or subtype not in vocab.ACCEPTED_SUBTYPES:
            items.append(_err("D-SUBTYPE",
                              f"{fid}: subtype {subtype!r} is only legal as "
                              f"accepted({'|'.join(vocab.ACCEPTED_SUBTYPES)})"))
        elif subtype == "test_amended":
            missing = [k for k in vocab.TEST_AMENDED_PAYLOAD
                       if not str(payload.get(k, "")).strip()]
            if missing:
                items.append(_err("D-TEST-AMENDED",
                                  f"{fid}: accepted(test_amended) missing "
                                  f"{missing}"))
    return items


def _falsification_items(fid: str, disp: str, payload: dict,
                         finding: Finding | None) -> list[Item]:
    """The falsification record an `accepted` disposition must carry (§5.3a).

    Before this, `accepted` carried a free-text `verification` and the
    ledger's `falsification_run` event — which two breakers read — had no
    writer outside the breaker tests. The author's statement that the named
    test was run, and what it did, was prose nobody could compare, and the
    proof that the test discriminates at all (reintroduce the defect, watch
    it fail, restore) lived in the author's diligence. This tool's own review
    produced two tests that passed with the defect present; nothing caught
    either until a human re-read the body.

    The record has two closed axes and three contradictions the validator
    refuses rather than records:

      - status `fail`: the test still fails on the head this disposition
        binds, so the finding is not fixed and `accepted` is not available;
      - mutation `passes_without_fix`: the test cannot tell fixed from
        unfixed, so it is not a falsification test, whatever its name;
      - status `cannot_execute` with mutation `fails_without_fix`: a test
        that could not be run was not run against a mutation either.

    `cannot_execute` and `not_run` are legal states — the first is what the
    `unverifiable` breaker exists for — but each must state its reason:
    an unexecuted check with no reason is silence, and silence is the state
    this whole design refuses to treat as a pass.
    """
    if disp != "accepted":
        return []
    record = payload.get("falsification")
    if record is None:
        if finding is not None and finding.falsification.strip():
            return [_err("D-FALSIFICATION-MISSING",
                         f"{fid}: the finding names a falsification test and "
                         f"this acceptance records no run of it — "
                         f"payload.falsification {{status, mutation}} is "
                         f"required (§5.3a: fixed means the named test "
                         f"flipped, not that something nearby was edited)")]
        return []
    if not isinstance(record, dict):
        return [_err("D-FALSIFICATION",
                     f"{fid}: payload.falsification must be an object "
                     f"{{status, mutation[, note]}}, got "
                     f"{type(record).__name__}")]
    items: list[Item] = []
    missing = [k for k in vocab.FALSIFICATION_RECORD
               if not str(record.get(k, "")).strip()]
    if missing:
        items.append(_err("D-FALSIFICATION",
                          f"{fid}: payload.falsification missing {missing}"))
    status = str(record.get("status", "")).strip()
    mutation = str(record.get("mutation", "")).strip()
    note = str(record.get("note", "")).strip()
    if status and status not in vocab.FALSIFICATION_STATUSES:
        items.append(_err("D-FALSIFICATION",
                          f"{fid}: falsification status {status!r} is not in "
                          f"{vocab.FALSIFICATION_STATUSES}"))
    if mutation and mutation not in vocab.MUTATION_OUTCOMES:
        items.append(_err("D-FALSIFICATION",
                          f"{fid}: falsification mutation {mutation!r} is not "
                          f"in {vocab.MUTATION_OUTCOMES}"))
    if status == "fail":
        items.append(_err("D-FALSIFICATION-FAILS",
                          f"{fid}: the named test still fails on the head this "
                          f"disposition binds, so the finding is not fixed — "
                          f"`accepted` is not available; fix it, or if the "
                          f"test itself is unsatisfiable, "
                          f"accepted(test_amended) with one that can pass"))
    if mutation == "passes_without_fix":
        items.append(_err("D-FALSIFICATION-INERT",
                          f"{fid}: the test passes with the defect "
                          f"reintroduced, so it cannot tell fixed from unfixed "
                          f"and falsifies nothing — amend it "
                          f"(accepted(test_amended)) or write one that fails "
                          f"without the fix"))
    if status == "cannot_execute" and mutation == "fails_without_fix":
        items.append(_err("D-FALSIFICATION",
                          f"{fid}: a test that cannot be executed was not "
                          f"executed against a mutation either — "
                          f"status and mutation contradict"))
    if (status == "cannot_execute" or mutation == "not_run") and not note:
        items.append(_err("D-FALSIFICATION-SILENT",
                          f"{fid}: {'status ' + status if status == 'cannot_execute' else 'mutation not_run'} "
                          f"must state its reason in payload.falsification."
                          f"note — an unexecuted check with no reason is "
                          f"silence"))
    return items


def _identity_items(fid: str, rec: dict,
                    against: Verdict | None) -> list[Item]:
    """Round 1 F3: a disposition's identity is DERIVED, never supplied.

    Dispositions and the reviewer closures that answer them bind by
    fingerprint, not by round-local finding id. A record carrying a
    caller-chosen fingerprint therefore closes whichever identity it names
    while the real finding stays open — the symmetry and repetition guarantees
    both read the ledger by fingerprint and would never see it. The emitter
    now derives these fields, and this is the wire-level half: an envelope
    authored by hand is checked against the verdict it claims to answer.
    """
    if against is None:
        return []
    finding = next((f for f in against.findings if f.id == fid), None)
    if finding is None:
        return [_err("D-UNKNOWN-FINDING",
                     f"{fid}: the answered verdict declares no such finding, "
                     f"so this record binds to nothing")]
    items: list[Item] = []
    for key, derived in (("fingerprint", finding.fingerprint()),
                         ("severity", finding.severity)):
        supplied = rec.get(key)
        if supplied is not None and str(supplied) != str(derived):
            items.append(_err(
                "D-IDENTITY",
                f"{fid}: {key} {supplied!r} does not match the verdict's "
                f"{derived!r}. Identity is computed from the finding it "
                f"answers and may not be supplied beside it (round 1 F3)"))
    return items


def validate_disposition(d: Disposition, cfg: Config,
                         against: Verdict | None = None) -> list[Item]:
    items: list[Item] = []
    if not d.wrapped:
        items.append(_err("D-WRAPPER", "no wrapper tag"))
        return items
    items.extend(envelope_identity(d.tag, d.attrs.get("head"), d.exact, cfg,
                                   "disposition", d.attr_defects))
    # Lineage-3 round 4 F1: a body that stated one JSON member twice was
    # not read at all (wire.parse_disposition); the defect names the member
    # and nothing below is judged as if a value had been chosen.
    for code, message in d.body_defects:
        items.append(_err(code, message))
    if d.body_defects:
        return items
    for attr in ("verdict_sha", "head"):
        if not d.attrs.get(attr):
            items.append(_err("D-ATTR", f"wrapper missing attribute {attr!r} — "
                              f"a disposition binds to the verdict it answers "
                              f"AND the head it produced (§5.2)"))
    records = d.data.get("dispositions", [])
    # The finding each record answers, when the verdict is at hand. Without
    # it the falsification check can only judge the record's shape; it
    # cannot know whether the finding named a test at all.
    by_id = {f.id: f for f in against.findings} if against is not None else {}
    seen: dict[str, int] = {}
    for rec in records:
        fid = rec.get("finding_id", "?")
        disp = rec.get("disposition", "")
        seen[fid] = seen.get(fid, 0) + 1
        if disp not in vocab.DISPOSITIONS:
            items.append(_err("D-TERM",
                              f"{fid}: {disp!r} is not in the closed vocabulary "
                              f"{vocab.DISPOSITIONS}"))
            continue
        items.extend(_payload_items(fid, disp, rec.get("subtype"),
                                    rec.get("payload", {})))
        items.extend(_falsification_items(fid, disp, rec.get("payload", {}),
                                          by_id.get(fid)))
        items.extend(_identity_items(fid, rec, against))

    for fid, n in seen.items():
        if n > 1:
            items.append(_err("D-DUPLICATE", f"{fid}: dispositioned {n} times; "
                              f"exactly once is required"))

    if against is not None:
        if d.attrs.get("verdict_sha") and against.sha and \
                d.attrs["verdict_sha"] != against.sha:
            items.append(_err("D-SHA-MISMATCH",
                              f"disposition binds verdict_sha "
                              f"{d.attrs['verdict_sha']} but the verdict is "
                              f"{against.sha}"))
        verdict_ids = {f.id for f in against.findings}
        missing = sorted(verdict_ids - set(seen), key=lambda x: (len(x), x))
        extra = sorted(set(seen) - verdict_ids, key=lambda x: (len(x), x))
        if missing:
            items.append(_err("D-COMPLETENESS",
                              f"findings not dispositioned: {missing} — a "
                              f"finding never dies by omission (§5.2)"))
        if extra:
            items.append(_err("D-UNKNOWN", f"dispositions for unknown findings: "
                              f"{extra}"))
        for rec in records:
            f = by_id.get(rec.get("finding_id"))
            if f is None:
                continue
            # Blocking status is the EFFECTIVE one: a blocking finding that
            # carries no falsification test has already been downgraded to
            # advisory by §5.3a, so deferral and preference become legal for
            # it (round-3 F7 — the downgrade must be applied, not announced).
            if effective_blocking(f.severity, f.falsification, cfg) and \
                    rec.get("disposition") in vocab.BLOCKING_ILLEGAL:
                items.append(_err("D-BLOCKING",
                                  f"{f.id}: {rec['disposition']!r} is illegal "
                                  f"for blocking findings (§5.2)"))
    return items

"""Fingerprint v2 — syntactic finding identity (design §5.3b).

Inputs, per the design: (invariant_id or classification, normalized path,
hunk/symbol anchor, normalized claim text), line numbers excluded.

The algorithm is VERSIONED: any change to normalization or input selection
bumps FP_VERSION so history is never silently re-identified. `legacy_v1`
is retained solely to generate alias lineage events when an older ledger or
artifact carries `fp1:` ids — the design's own answer to a version change.

It provides syntactic identity only. Renames, anchor changes and semantic
repeats are carried by explicit lineage events in the ledger
(resolve_identity below), never guessed here.

Round-3 F1 and F13 changed two rules relative to fp1:
- an invariant-specific key OUTRANKS the text hash (§5.3b), so where one
  exists the claim text is excluded and rewording preserves identity;
- line numbers are stripped only from CITATIONS, not from arbitrary claim
  text, so `timeout:30` and `timeout:60` no longer collide.

Round-4 F7 replaced the guess at the centre of that second rule. Which
`token:N` is a citation is now DECLARED by the finding (`Citations:` in the
wire grammar) and only inferred where nothing is declared — a real
extensionless secondary citation (`design:102`) was indistinguishable from a
literal under inference alone, so it kept its line number and split identity
whenever the line moved. Declared beats derived; derived is recorded as the
weaker state it is, never silently equated with the stronger one.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

from . import vocab

FP_VERSION = "fp2"
FP_LEGACY_VERSION = "fp1"

# A `token:N` or `token:N-M` occurrence. Whether it is a citation (whose line
# number moves) or a meaningful literal (`timeout:30`) is decided by
# _is_citation, never by the bare shape.
_NUM_SUFFIX = re.compile(r"(?P<tok>[\w.\-/]+):(?P<num>\d+)(?:[–—-]\d+)?\b")
_LEGACY_LINE_SUFFIX = re.compile(r"(?<=[\w.\-/]):\d+(?:[–—-]\d+)?\b")
_HAS_EXTENSION = re.compile(r"\.\w+$")
_WS = re.compile(r"\s+")
_PUNCT_MAP = str.maketrans({
    "“": '"', "”": '"', "‘": "'", "’": "'",
    "–": "-", "—": "-",
})


def _base(text: str) -> str:
    """Markup, whitespace and case normalization shared by every field."""
    s = unicodedata.normalize("NFC", text or "").translate(_PUNCT_MAP)
    s = s.replace("`", "").replace("**", "").replace("*", "")
    s = _WS.sub(" ", s).strip().rstrip(".").casefold()
    return s


def _is_citation(token: str, anchor: str,
                 declared: frozenset[str] = frozenset()) -> bool:
    """True when `token:N` is a document citation whose line number moves.

    Two tiers, and the order between them is the whole of round-4 F7:

    1. **Declared** (`Citations:` in the verdict grammar) — the finding names
       its own citation targets, so this is a lookup, not a guess. An
       extensionless secondary citation such as `design:102` is only
       recognizable this way; inference cannot tell it from `timeout:30`,
       and F7 is the case where inference got it wrong.
    2. **Derived**: a path separator, a file extension, or an exact match
       against this finding's own anchor. Deliberately narrow, and weaker by
       construction — it is why tier 1 exists.

    The tiers are additive, not exclusive. Declaring `design` must not force a
    reviewer to re-declare every `review/wire.py:212` it also cites, and the
    derived tier is right about those; what it cannot see is the
    extensionless case, which is exactly what tier 1 supplies.
    """
    tok = token.casefold()
    if tok.strip("/") in declared:
        return True
    if "/" in tok or _HAS_EXTENSION.search(tok):
        return True
    return bool(anchor) and tok == anchor.casefold().strip("/")


def normalize_citations(citations) -> frozenset[str]:
    """The declared citation set, normalized for lookup (F7)."""
    if isinstance(citations, str):
        citations = re.split(r"[,\s]+", citations)
    return frozenset(
        c for c in (normalize_path(x) for x in citations or ()) if c)


def normalize_path(path: str) -> str:
    """Paths and anchors are structured citation fields: always strip."""
    s = _LEGACY_LINE_SUFFIX.sub("", (path or "").strip())
    if s.startswith("./"):
        s = s[2:]
    return _base(s.replace("\\", "/").strip("/"))


def normalize_key(key: str) -> str:
    return _base(key)


def normalize_claim(text: str, anchor: str = "", citations=()) -> str:
    """Claim prose: strip line numbers from citations only (F13, F7)."""
    declared = normalize_citations(citations)

    def repl(m: re.Match) -> str:
        return (m.group("tok")
                if _is_citation(m.group("tok"), anchor, declared)
                else m.group(0))

    return _base(_NUM_SUFFIX.sub(repl, text or ""))


def compute(classification: str, path: str, anchor: str, claim_text: str,
            invariant_id: str = "", citations=()) -> str:
    """Fingerprint a finding.

    Where a stable invariant-specific key exists it outranks the text hash,
    since it is the only identity that survives rewording (§5.3b, round-3 F1):
    the claim text is then excluded from the hash entirely.

    `citations` are the finding's declared citation targets (F7). They change
    only which `token:N` occurrences lose their line number, so a finding that
    declares none fingerprints exactly as it did before the field existed —
    the whole corpus depends on that.
    """
    norm_path = normalize_path(path)
    norm_anchor = normalize_path(anchor)
    if invariant_id:
        parts = (FP_VERSION, "inv", normalize_key(invariant_id), norm_path,
                 norm_anchor)
    else:
        parts = (FP_VERSION, "cls", normalize_key(classification), norm_path,
                 norm_anchor, normalize_claim(claim_text, path, citations))
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{FP_VERSION}:{digest[:16]}"


def legacy_v1(kind_key: str, path: str, anchor: str, claim_text: str) -> str:
    """The fp1 algorithm, retained only to build alias lineage events.

    Frozen: this must never be 'improved'. Its sole job is to reproduce ids
    that already exist in ledgers and in the round-3 verdict text.
    """
    def old_norm(text: str) -> str:
        s = unicodedata.normalize("NFC", text or "").translate(_PUNCT_MAP)
        s = _LEGACY_LINE_SUFFIX.sub("", s)
        s = s.replace("`", "").replace("**", "").replace("*", "")
        return _WS.sub(" ", s).strip().rstrip(".").casefold()

    def old_path(p: str) -> str:
        s = _LEGACY_LINE_SUFFIX.sub("", (p or "").strip())
        if s.startswith("./"):
            s = s[2:]
        return s.replace("\\", "/").strip("/")

    parts = (FP_LEGACY_VERSION, old_norm(kind_key), old_path(path),
             old_norm(anchor), old_norm(claim_text))
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{FP_LEGACY_VERSION}:{digest[:16]}"


def alias_event(old_fp: str, new_fp: str) -> dict:
    """Lineage event carrying identity across a fingerprint version change."""
    return {
        "event": "lineage", "kind": "alias",
        "from_fp": old_fp, "to_fp": new_fp,
        "reason": f"fingerprint algorithm {FP_LEGACY_VERSION}->{FP_VERSION} "
                  f"(round-3 F1 invariant precedence, F13 citation-only line "
                  f"stripping); identity carried, not silently re-identified",
    }


class LineageError(ValueError):
    """A merging lineage that cannot yield one canonical identity.

    Round-4 F6: the previous code broke a cycle and returned whichever node it
    happened to stop at, so `A -> B -> A` resolved to A from A and to B from
    B. That is worse than an error — it silently splits one identity into two,
    which disables exactly the repetition detection lineage exists to keep
    working. A malformed ledger is not a ledger with a plausible answer in it.

    Subclasses ValueError so the CLI's next-command contract already routes it
    (§9bis.3 rule 3) instead of surfacing a traceback.
    """


def merging_edges(lineage_events: list[dict]) -> dict[str, str]:
    """`from_fp -> to_fp` for merging kinds only, or fail closed.

    Only kinds in vocab.LINEAGE_MERGING merge identities (rename, anchor
    change, invariant introduction, alias). `split` deliberately does NOT
    merge: children are distinct claims with recorded parentage, so a parent
    with five split children is well-formed and is not a conflict.
    """
    forward: dict[str, str] = {}
    for ev in lineage_events:
        if ev.get("kind") not in vocab.LINEAGE_MERGING:
            continue
        src, dst = ev.get("from_fp"), ev.get("to_fp")
        if src in forward and forward[src] != dst:
            raise LineageError(
                f"malformed merging lineage: {src} merges into both "
                f"{forward[src]} and {dst}. One fingerprint cannot have two "
                f"canonical identities; record one of them as a `split` or "
                f"remove the conflicting event")
        forward[src] = dst
    return forward


def resolve_identity(fingerprint: str, lineage_events: list[dict]) -> str:
    """Canonical identity of a fingerprint under merging lineage events.

    Resolution follows from_fp -> to_fp edges to a fixed point; the newest
    fingerprint in a merge chain is the canonical one. Every member of a
    well-formed component therefore resolves to the same endpoint. A cycle has
    no such endpoint, so it raises rather than picking one (F6).
    """
    forward = merging_edges(lineage_events)
    path = [fingerprint]
    current = fingerprint
    while current in forward:
        current = forward[current]
        if current in path:
            raise LineageError(
                f"cyclic merging lineage: {' -> '.join(path + [current])}. "
                f"No member of a cycle is canonical; break it in the ledger")
        path.append(current)
    return current

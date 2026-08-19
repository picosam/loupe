"""The transport verbs (design §9bis.3, RVW-T9): `handoff`, `take`, `close`.

One verb per phase, zero required flags on the common path, every decision
derived from config plus a probe. These ride the pushed-SHA reachability
that §9bis.4 landed; they do NOT implement the git-notes carrier (RVW-T1,
parked behind the two-clone prototype). The envelope still crosses machines
by whatever `relay` the repo declares — a file path on one machine, a paste
between two — and each verb records what it did in the ledger, so neither
end of the loop is manual any more.

  handoff  author side: emit-request, then record the request event, keep
           the envelope bytes in the exchange directory (outside the tree,
           §4), and stop — the human sets the round in motion (the agents'
           standing instructions).
           Idempotent on an unchanged tip (§9bis.3 rule 5).
  take     reviewer side: validate the request, PROBE the target (fetch,
           cat-file, base ancestry) and every reference (digest), record
           the request and a `take` event, print the envelope and the exact
           diff command. Refuses everything it cannot verify.
  close    author side: ingest the verdict, record it, close the round; a
           clean verdict closes the lineage; `--lineage` closes one by
           recorded decision, so the next handoff opens round 1 with the
           repo default cap rather than inheriting an authorization granted
           to a different review.

Ledger events introduced here: `take` and `lineage_closed`. Requests and
verdicts are recorded in exactly the shape `ledger add` records them, so a
manual `ledger add` afterwards is a no-op, never a duplicate.
"""
from __future__ import annotations

import dataclasses
import re
import subprocess
from pathlib import Path

from . import TOOL_NAME, validate, vocab, wire
from .config import Config
from .digest import sha256_file, sha256_text
from .fingerprint import alias_event
from .ledger import Ledger

EXCHANGE_DIR = "exchange"

_BASE_LINE_RE = re.compile(r"^Base:\s+([0-9a-f]{40})\b", re.MULTILINE)
# The closed reference grammar. Four forms, and `asserted` is one of them:
# a line naming a path and its requirement marker but carrying no digest, so
# the tool can say the author claimed it and nothing more. It had no syntax
# here while probe_references carried an `asserted` branch and a docstring
# promising it — the branch was unreachable, because the alternation below was
# mandatory. A state the code describes and the grammar cannot express is a
# state the reader is told exists and never sees.
_REF_LINE_RE = re.compile(
    r"^\s*(?P<path>\S+?)(?P<dir>/)?\s+"
    r"(?:sha256:(?P<digest>[0-9a-f]{64})|(?P<unavailable>UNAVAILABLE)"
    r"|\((?P<note>directory[^)]*)\)"
    r"|\[(?P<marker>required|advisory)\])")


# Round 4 F1: the recorded state for "this emission supplied no claim file".
# A distinct value rather than an absent field, so "no claim" is something the
# ledger PROVES rather than something a reader infers from silence — absence
# already means "recorded before the field existed", and one token cannot mean
# both without reopening the hole three rounds have now closed by halves.
#
# The value itself lives in `vocab` with the rest of the claim's grammar
# (round 7 F1), so the capture boundary and the cache cannot drift into two
# spellings of one state; this name is where the cache reads it.
NO_CLAIM = vocab.NO_CLAIM


class Refusal(RuntimeError):
    """A refusal that carries the next command (§9bis.3 rule 3)."""

    def __init__(self, why: str, next_cmd: str):
        super().__init__(why)
        self.next_cmd = next_cmd


def _git(repo_root: Path, *args: str, timeout: int = 120) -> str:
    out = subprocess.run(["git", "-C", str(repo_root), *args],
                         capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout.strip()


_digest_text = sha256_text


# ------------------------------------------------------------------ exchange

def exchange_dir(cfg: Config) -> Path | None:
    return Path(cfg.ledger_dir) / EXCHANGE_DIR if cfg.ledger_dir else None


def exchange_path(cfg: Config, round_no: int, kind: str) -> Path | None:
    d = exchange_dir(cfg)
    return d / f"round-{round_no}-{kind}.md" if d else None


def keep_bytes(cfg: Config, round_no: int, kind: str, text: str) -> str:
    """Write the envelope bytes beside the ledger and return the path, or the
    reason there is none. Retention is best effort: the ledger event carries
    the digest either way, so a copy that could not be kept degrades the
    pointer without losing the identity (the same rule gate output follows)."""
    target = exchange_path(cfg, round_no, kind)
    if target is None:
        return "not kept (no state directory configured)"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.read_text(encoding="utf-8") == text:
            return str(target)
        target.write_text(text, encoding="utf-8")
        return str(target)
    except OSError as exc:
        return f"not kept ({exc.strerror})"


# ------------------------------------------------------------------- events

def request_event(parsed: wire.Request, round_no: int, digest: str,
                  size: int, tokens: int | None = None,
                  claim_digest: str = "") -> dict:
    """The request event exactly as `ledger add` records it (one shape)."""
    event = {"event": "request", "round": round_no, "sha": parsed.sha,
             "author": parsed.attrs.get("author"),
             "reviewer": parsed.attrs.get("reviewer"),
             "source_digest": digest, "bytes": size}
    if tokens is not None:
        event["tokens"] = tokens
    if claim_digest:
        # What the AUTHOR supplied, as opposed to what the tree determined.
        # The handoff cache reads it to tell an unchanged emission from an
        # unchanged tip; absent on every pre-round-3 event, and absence is
        # treated as "not recorded", never as "no claim".
        event["claim_digest"] = claim_digest
    return event


def request_events(parsed: wire.Request, round_no: int, digest: str,
                   size: int, tokens: int | None = None,
                   claim_digest: str = "") -> list[dict]:
    """Everything a recorded request contributes to the ledger, in order:
    the request event, then its content-addressed evidence.

    ONE shape for the three doors a request enters by — `handoff`
    (record_handoff), `take`, and the manual `ledger add`. Sweep F3: the
    manual door recorded the request event alone and skipped the evidence
    events the other two record, so the same envelope produced a different
    ledger depending on which verb filed it — the "advertised parity between
    ingestion paths" was false in shape as well as in validation.
    """
    return [request_event(parsed, round_no, digest, size, tokens,
                          claim_digest=claim_digest),
            *evidence_events(parsed, round_no)]


def evidence_events(parsed: wire.Request, round_no: int) -> list[dict]:
    """Content-addressed evidence carried by a request (round 1 F6).

    The design says the ledger carries evidence digests, and the repetition
    breaker exempts a returning finding when the new round cites new
    content-addressed evidence. Ingestion recorded only the digest of the
    whole envelope, so that exemption was unreachable on the product path: the
    breaker could see a loop but never the escape from it, and a breaker that
    cannot observe its own escape condition is not a reliable one.

    Two content-addressed sources exist in a request, and both key on BYTES:
    a reference's sha256, and a gate attestation's recorded output sha256.
    Keying on the digest rather than the path is the point — moving gate
    output to a new pointer while the bytes are unchanged must not read as
    new evidence, and a genuinely different byte string must.

    Round 2 F6: recording the digests was only half of it. `of` carries the
    path in canonical form because the breaker joins these events to the
    findings that cite that path (see `wire.Finding.cited_paths`); an event
    whose pointer is spelled `./a.md` here and `a.md` in the finding would
    record evidence nothing can reach.

    Round 3 F5: de-duplication keyed on the digest ALONE, which conflated two
    different questions. "Are these bytes new?" is answered by the digest, and
    the breaker still answers it that way. "Which findings can these bytes
    speak for?" is answered by the PATH, and dropping the second path that
    carries a digest threw that edge away — so when two references held
    identical new bytes and only the later one was cited, the join went
    missing and a refutation was falsely escalated as repetition. Which of
    the two got recorded depended on the order the references were listed in.
    The key is therefore the whole association, and newness stays with the
    digest where it belongs.
    """
    events: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    def add(digest: str, source: str, where: str) -> None:
        key = (digest, source, where)
        if not digest or key in seen:
            return
        seen.add(key)
        events.append({"event": "evidence", "round": round_no,
                       "digest": digest, "source": source, "of": where})

    reference = wire.section(parsed.sections, "reference")
    for line in reference.splitlines():
        m = _REF_LINE_RE.match(line)
        if m and m.group("digest"):
            add(m.group("digest"), "reference",
                wire.normalize_path(m.group("path")))

    evidence_section = parsed.sections.get("evidence", "")
    records, error, _ = validate.parse_attestations(evidence_section)
    for record in (records or []) if not error else []:
        output = record.get("output") or {}
        add(output.get("sha256", ""), "gate-output", record.get("id", "?"))
    return events


def verdict_events(parsed: wire.Verdict, round_no: int, digest: str,
                   size: int, tokens: int | None = None) -> list[dict]:
    """Verdict, alias, finding and closure events, exactly as `ledger add`
    records them (moved here from the CLI so `close` and `ledger add` cannot
    drift into two shapes of the same fact)."""
    event = {"event": "verdict", "round": round_no,
             "sha": parsed.sha, "verdict": parsed.verdict,
             "source_digest": digest, "bytes": size,
             "finding_ids": len(parsed.findings)}
    if tokens is not None:
        event["tokens"] = tokens
    events = [event]
    for f in parsed.findings:
        if f.legacy_fingerprint() != f.fingerprint():
            events.append(alias_event(f.legacy_fingerprint(), f.fingerprint()))
        events.append({
            "event": "finding", "round": round_no, "id": f.id,
            "fp": f.fingerprint(), "severity": f.severity,
            "classification": f.classification, "title": f.title,
            "anchor_path": f.anchor_path,
            "falsification": f.falsification,
            # Round 1 F7: carried from the verdict, not hardcoded.
            "preventable_by": f.preventable_by or None,
            # Round 2 F6: which paths this finding cites. The repetition
            # breaker joins reference-digest evidence to finding identity on
            # exactly this list, so it is recorded with the finding rather
            # than re-derived from prose whenever a report is rendered.
            "cites": f.cited_paths})
        # Round 1 F6, the half that makes the repetition exemption REACHABLE.
        # The breaker asks whether a finding returning after a refutation is
        # backed by new content-addressed evidence for that identity. The
        # fingerprint is computed from classification, anchor and title — not
        # from Evidence — so a reviewer can re-assert the same finding with
        # genuinely new evidence and keep the identity, which is exactly the
        # case the exemption exists for. Digesting the Evidence text makes
        # "new" decidable: repeat the same words and the digest repeats.
        if f.evidence.strip():
            events.append({"event": "evidence", "round": round_no,
                           "fp": f.fingerprint(),
                           "digest": sha256_text(f.evidence.strip()),
                           "source": "verdict", "of": f.id})
    for c in parsed.closures:
        events.append(c.as_event(round_no))
    return events


def answered_verdict(cfg: Config, parsed: wire.Disposition,
                     ledger: Ledger | None = None):
    """The verdict a standalone disposition answers, or None (round 2 F2).

    None means "not retrievable", which the caller must treat as a refusal
    rather than as permission to record unchecked identities.

    Round 3 F2: round 2 moved identity authority out of the disposition and
    into this retained file, and stopped there. The exchange copy is kept
    BEST EFFORT — `keep_bytes` degrades to a reason string when it cannot
    write, and nothing guarded the bytes afterwards — so accepting any
    well-formed verdict that carried the claimed SHA handed the forged-
    identity capability straight back: replace the kept copy with a different
    valid verdict at the same SHA, and the attacker chooses which fingerprint
    reaches closures, breakers and the merge decision. A probe did exactly
    that and `ledger add` exited 0.

    The append-only record is the authority; the file beside it is a cache of
    bytes the record already digested. So resolution runs through the ledger:
    the verdict event for this disposition's round supplies the SHA and the
    digest, and the retained bytes must reproduce that digest before they are
    parsed as anything. Missing, ambiguous and mismatched are three distinct
    refusals and none of them is a warning.
    """
    round_no = int(parsed.data.get("round", 0) or 0)
    claimed = parsed.attrs.get("verdict_sha") or ""
    if ledger is None:
        return None            # no record to resolve against: not retrievable
    recorded = recorded_verdict(ledger, round_no=round_no,
                                sha=claimed or None)
    if recorded is None:
        return None
    path = exchange_path(cfg, round_no, "verdict")
    if path is None or not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if _digest_text(text) != recorded["source_digest"]:
        return None            # the kept copy is not the recorded verdict
    verdict = wire.parse_verdict(text)
    if not verdict.wrapped or verdict.sha != recorded.get("sha"):
        return None
    if claimed and verdict.sha != claimed:
        return None
    return verdict


def recorded_verdict(ledger: Ledger, round_no: int | None = None,
                     sha: str | None = None,
                     digest: str | None = None) -> dict | None:
    """The ONE recorded verdict of the current lineage that a response may
    answer, or None.

    Sweep F2: the resolution boundary, shared by the two doors a response
    enters by. `ledger add` resolved a standalone disposition through
    `answered_verdict` above; `respond --out` resolved nothing — it built,
    validated, wrote and recorded a disposition against whatever file it
    was handed, so an unwrapped legacy verdict that fails validation and
    was never recorded produced disposition events, fingerprints and
    falsification runs at a caller-supplied SHA. One rule now, in one place:

      - the event exists in the CURRENT lineage (a closed lineage's verdict
        is answered, not answerable);
      - it is unique — two different verdicts filed at one round, or two
        rounds carrying the same bytes, are ambiguity, and ambiguity is a
        refusal rather than a pick;
      - every key the caller supplies (round, sha, digest) agrees with it;
      - it is the JUST-CLOSED round: the lineage's most recent verdict. A
        response answers the ruling that is awaiting one; a disposition for
        an earlier round would arrive after the round it answers has already
        been superseded, and the record would then carry an answer no
        request ever rendered.
    """
    events = [e for e in ledger.current()
              if e.get("event") == "verdict" and "round" in e]
    if not events:
        return None
    latest = max(e["round"] for e in events)
    # Round-2 F1 (Blocker): uniqueness is judged over the WHOLE answerable
    # round before any caller-supplied key is applied. The first version
    # filtered by the caller's digest first and checked uniqueness inside
    # that subset — so with two different verdicts filed at one round, the
    # author chose which one to answer by choosing the file. The caller's
    # keys are agreement checks against the one recorded verdict, never a
    # selector among several.
    in_round = [e for e in events if e.get("round") == latest]
    digests = {e.get("source_digest") for e in in_round}
    if len(digests) != 1 or None in digests:
        return None            # two rulings for one round: ambiguity, refuse
    recorded = in_round[-1]
    if round_no is not None and recorded.get("round") != round_no:
        return None
    if sha is not None and recorded.get("sha") != sha:
        return None
    if digest is not None and recorded.get("source_digest") != digest:
        return None
    return recorded


def verdict_conflict(ledger: Ledger, round_no: int, digest: str) -> dict | None:
    """The recorded verdict that a NEW verdict for `round_no` would
    contradict, or None (round-2 F1). A round is ruled once: the same bytes
    filed again are the idempotent no-op the ledger already provides; a
    different verdict at an already-ruled round is a second ruling, which no
    door may append — `ledger add --round N` used to, and `respond` then
    answered whichever the author pointed at."""
    for e in ledger.current():
        if e.get("event") == "verdict" and e.get("round") == round_no \
                and e.get("source_digest") not in (None, digest):
            return e
    return None


def _derived_identities(parsed: wire.Disposition,
                        against: wire.Verdict) -> dict:
    """`{finding_id: (fingerprint, severity)}` computed from the verdict.

    Round 2 F2. Round 1 fixed the forged-identity hole in `respond` and left
    `ledger add` — the other door into the same ledger — accepting whatever it
    was handed. The remedy has to live where BOTH paths converge, which is
    here: no caller can record a disposition without the verdict it answers,
    and a supplied value may only agree with what that verdict computes.
    """
    by_id = {f.id: f for f in against.findings}
    out = {}
    for rec in parsed.data.get("dispositions", []):
        fid = rec.get("finding_id")
        finding = by_id.get(fid)
        if finding is None:
            raise Refusal(
                f"disposition names finding {fid!r}, which the answered "
                f"verdict does not declare — identity cannot be derived and "
                f"must not be taken from the disposition (round 2 F2); "
                f"a person corrects the disposition's finding ids to the "
                f"verdict's, or answers the verdict this one is for",
                "")
        computed = (finding.fingerprint(), finding.severity)
        for key, value in (("fingerprint", computed[0]),
                           ("severity", computed[1])):
            supplied = rec.get(key)
            if supplied is not None and str(supplied) != str(value):
                raise Refusal(
                    f"{fid}: supplied {key} {supplied!r} does not match the "
                    f"verdict's {value!r}; identity is computed from the "
                    f"finding being answered (round 2 F2); a person "
                    f"removes {key!r} from the disposition and re-runs the "
                    f"command that produced it",
                    "")
        out[fid] = computed
    return out


def disposition_events(parsed: wire.Disposition,
                       against: wire.Verdict, cfg: Config | None = None) -> list[dict]:
    """Disposition events exactly as `ledger add` records them.

    `against` is REQUIRED: a disposition binds by fingerprint, and a
    fingerprint that was not computed from the finding it claims to answer
    binds to whatever it names. Round 2 F2 made this the single authority for
    both recording paths rather than a check one of them happened to run.

    Round 1 F6: a refutation's evidence is also emitted as a content-addressed
    `evidence` event bound to the finding's fingerprint. The repetition
    breaker asks whether a returning finding is backed by NEW evidence for
    THAT identity, and prose in a payload answers nothing it can compare.
    Digesting the text makes the comparison exact: repeating the same words
    yields the same digest and the breaker fires; saying something genuinely
    new yields a different one and the documented exemption applies.
    """
    round_no = int(parsed.data.get("round", 0))
    derived = _derived_identities(parsed, against)
    events = [{
        "event": "disposition",
        "round": round_no,
        "finding_id": rec.get("finding_id"),
        "fp": derived[rec.get("finding_id")][0],
        "disposition": rec.get("disposition"),
        "subtype": rec.get("subtype"),
        "payload": rec.get("payload", {}),
        "verdict_sha": parsed.attrs.get("verdict_sha"),
        "head": parsed.attrs.get("head")}
        for rec in parsed.data.get("dispositions", [])]
    for rec in parsed.data.get("dispositions", []):
        text = str(rec.get("payload", {}).get("evidence", "")).strip()
        if not text:
            continue
        events.append({"event": "evidence", "round": round_no,
                       "fp": derived[rec.get("finding_id")][0],
                       "digest": sha256_text(text),
                       "source": "disposition",
                       "of": rec.get("finding_id")})
    # The falsification record of an `accepted` disposition (§5.3a) becomes
    # the `falsification_run` event the `stale` and `unverifiable` breakers
    # read. Until this was written, those breakers had no writer outside
    # their own tests: the design carried the mechanism for a test that
    # proves nothing, and no product path ever reached it. The event binds
    # to the TEST that was run — the finding's own, or the amended one under
    # accepted(test_amended) — by digest, so "the named test flipped" is
    # checkable against the verdict rather than taken from prose.
    by_id = {f.id: f for f in against.findings}
    for rec in parsed.data.get("dispositions", []):
        if rec.get("disposition") != "accepted":
            continue
        record = rec.get("payload", {}).get("falsification")
        if not isinstance(record, dict) or not record.get("status"):
            continue
        fid = rec.get("finding_id")
        payload = rec.get("payload", {})
        test = (str(payload.get("amended_test", ""))
                if rec.get("subtype") == "test_amended"
                else by_id[fid].falsification)
        event = {"event": "falsification_run", "round": round_no,
                 "fp": derived[fid][0], "of": fid,
                 "status": record.get("status"),
                 "mutation": record.get("mutation"),
                 "test_digest": sha256_text(test.strip()),
                 "source": "disposition"}
        if cfg is not None:
            # Sweep F12: the run is bound to the finding's EFFECTIVE blocking
            # state when it is recorded — the same rule the validator applies
            # (blocking severity AND a named test) — so the `unverifiable`
            # breaker fires on a blocking finding's unexecutable test and on
            # nothing else, without re-deriving severity at read time.
            event["blocking"] = validate.effective_blocking(
                by_id[fid].severity, test, cfg)
        if str(record.get("note", "")).strip():
            event["note"] = str(record["note"]).strip()
        events.append(event)
    return events


def record_response(cfg: Config, ledger: Ledger, envelope: str,
                    against: wire.Verdict) -> dict:
    """`respond --out`: keep the disposition bytes and record the events, so
    the author's half of the round is in the ledger without a manual add.
    Only when a file was written: an envelope printed to stdout has no bytes
    of record to bind to, and a re-run of the same JSON is idempotent."""
    parsed = wire.parse_disposition(envelope)
    round_no = int(parsed.data.get("round", 0))
    kept = keep_bytes(cfg, round_no, "disposition", envelope)
    added = ledger.add_all(disposition_events(parsed, against, cfg))
    return {"kept": kept, "events_added": added, "round": round_no}


# ------------------------------------------------------------------- handoff

BREAKER_OVERRIDE = "breaker_override"


def firing_id(firing: dict) -> str:
    """The stable identity of one breaker firing: breaker, round, and the
    fingerprint (or limit) it fired on. Round-2 F3: an override that named
    only a breaker and a round ceiling covered any LATER firing that
    happened to share the name and round — a run event arriving after the
    verdict could create an `unverifiable` firing in an already completed
    round that a pre-recorded override already "covered". Identity is what
    the human saw; a firing that did not exist when the decision was
    recorded is not one the decision took."""
    tail = firing.get("fp") or firing.get("limit") or ""
    return f"{firing.get('breaker')}@{firing.get('round', '?')}:{tail}"


def authorize_breaker(cfg: Config, ledger: Ledger, breaker: str,
                      reason: str, authorized_by: str) -> dict:
    """Record the human's decision to continue past a fired breaker.

    Sweep F8. A firing stops the loop (design §5.3d: "any firing stops the
    loop and escalates to me, naming the rule and the decision required"),
    so there has to be a recorded way to take the decision, or "stop" means
    "dead until the lineage closes". This is the shape the design already
    trusts for stepping outside a rule — reason-bearing, actor-bearing,
    appended, lineage-scoped, like `cap_override`.

    Round-2 F2, F3: the decision names its reason and its taker — empty or
    whitespace values are refused before anything is appended, since a
    required flag proves only that a token was supplied — and it binds to
    the firings that are CURRENTLY fired and unauthorized for the named
    breaker, by identity (`covers`). Nothing to cover is a refusal too: an
    authorization for a breaker that is not firing would be a decision
    about a fact the human has not seen.
    """
    if breaker not in vocab.BREAKERS:
        raise Refusal(f"{breaker!r} is not a breaker; the closed set is "
                      f"{list(vocab.BREAKERS)}", "")
    if not (reason or "").strip():
        raise Refusal("a breaker override is a recorded decision and the "
                      "reason IS the record: an empty reason says a "
                      "decision happened and not what it was; nothing is "
                      "appended", "")
    if not (authorized_by or "").strip():
        raise Refusal("no authorizer was declared: continuing past a fired "
                      "breaker is the human's decision, and this tool cannot "
                      "observe who ran it — it can only carry what is "
                      "asserted; silence appends nothing", "")
    firing = [f for f in unauthorized_breakers(cfg, ledger)
              if f.get("breaker") == breaker]
    if not firing:
        raise Refusal(f"breaker {breaker!r} is not firing unauthorized in "
                      f"this lineage: there is no firing for a decision to "
                      f"cover, and an authorization recorded ahead of a "
                      f"firing would cover a fact the human has not seen "
                      f"(round-2 F3)",
                      f"{TOOL_NAME} ledger report")
    covers = sorted(firing_id(f) for f in firing)
    event = {"event": BREAKER_OVERRIDE, "breaker": breaker,
             "covers": covers, "reason": reason.strip(),
             "authorized_by": authorized_by.strip()}
    added = ledger.add(event)
    return {"recorded": bool(added), "breaker": breaker, "covers": covers,
            "authorized_by": authorized_by.strip()}


def unauthorized_breakers(cfg: Config, ledger: Ledger) -> list[dict]:
    """The fired breakers no recorded decision covers (§5.3d, sweep F8):
    a firing is covered only when an override lists its identity."""
    fired = ledger.breakers(ledger.effective_round_cap(cfg.round_cap),
                            token_budget=cfg.token_budget,
                            blocking_severities=cfg.blocking_severities)
    covered = {fid for o in ledger._by(BREAKER_OVERRIDE)
               for fid in o.get("covers", [])}
    return [f for f in fired if firing_id(f) not in covered]


def missing_dispositions(ledger: Ledger) -> dict | None:
    """The just-closed round's findings that have no disposition, if any.

    Sweep F4. `next_round` derived the next round from verdict events
    alone, so `handoff` opened round N+1 the moment round N had a verdict —
    with or without the author's answer — and rendered `(no round-N
    dispositions in the ledger)` into the next request as if that were a
    state rather than a defect. The contract is one disposition per finding
    and a finding never dies by omission. Returns None when nothing is
    owed: no verdict yet in this lineage (round 1), or the last verdict has
    every finding answered exactly once. A clean verdict closes the lineage,
    so a new lineage's round 1 is the clean-verdict control by construction.
    """
    verdicts = ledger._by("verdict")
    if not verdicts:
        return None
    last = max(e["round"] for e in verdicts if "round" in e)
    findings = [e for e in ledger._by("finding") if e.get("round") == last]
    if not findings:
        return None
    answered: dict[str, int] = {}
    for d in ledger._by("disposition"):
        if d.get("round") == last:
            answered[d.get("fp")] = answered.get(d.get("fp"), 0) + 1
    unanswered = [f for f in findings if answered.get(f.get("fp"), 0) == 0]
    repeated = sorted(fp for fp, n in answered.items() if n > 1)
    if not unanswered and not repeated:
        return None
    return {"round": last,
            "findings": len(findings),
            "unanswered": [{"id": f.get("id"), "fp": f.get("fp"),
                            "severity": f.get("severity")}
                           for f in unanswered],
            "repeated": repeated}


def handoff_preflight(cfg: Config, ledger: Ledger) -> None:
    """Refuse a handoff the lifecycle does not permit — BEFORE the cache is
    consulted, a commit made, a push attempted, a gate run or a request
    emitted (sweep F4, F8). Raises Refusal with no runnable `next`: both
    refusals name a decision or an authored answer, which a person supplies
    and no command repairs.

    It reads ledger state, so it runs AFTER the caller has captured and
    judged the author's claim (lineage-3 round 6 F1): a claim whose grammar
    has not been accepted must not cause the ledger to be read at all. The
    ordering is one chain — claim grammar, then lifecycle, then cache, push,
    gates, emission, ledger mutation."""
    owed = missing_dispositions(ledger)
    if owed is not None:
        ids = ", ".join(f"{u['id']} ({u['fp']})" for u in owed["unanswered"])
        parts = []
        if owed["unanswered"]:
            parts.append(f"{len(owed['unanswered'])} of {owed['findings']} "
                         f"finding(s) of the round-{owed['round']} verdict "
                         f"have no disposition: {ids}")
        if owed["repeated"]:
            parts.append(f"answered more than once: "
                         f"{', '.join(owed['repeated'])}")
        raise Refusal(
            "; ".join(parts) + " — one disposition per finding, and a "
            "finding never dies by omission (§5.2); the next round is not "
            "opened until every finding is answered. Answer them with "
            f"`{TOOL_NAME} respond --verdict <the recorded round-"
            f"{owed['round']} verdict> --from-json <dispositions.json> "
            f"--out <disposition.md>`, then hand off",
            "")
    fired = unauthorized_breakers(cfg, ledger)
    if fired:
        named = "; ".join(
            f"{f['breaker']} (round {f.get('round', '?')}"
            + (f", {f['fp']}" if f.get("fp") else "") + f"): {f['rule']}"
            for f in fired)
        raise Refusal(
            f"a circuit breaker fired and no recorded decision covers it — "
            f"{named}. Any firing stops the loop and escalates to the "
            f"human, naming the rule and the decision required (§5.3d). "
            f"The decision is one of: close the lineage "
            f"(`{TOOL_NAME} close --lineage --reason \"...\" --by <who>`), "
            f"or continue past this firing by recorded authorization "
            f"(`{TOOL_NAME} ledger authorize-breaker --breaker "
            f"{fired[0]['breaker']} --reason \"...\" --by <who>`); the "
            f"tool takes neither on its own",
            "")


def cached_handoff(cfg: Config, ledger: Ledger, round_no: int,
                   git=None, claim_digest: str = "") -> dict | None:
    """§9bis.3 rule 5: re-running handoff on an unchanged tip with a warm
    request returns the same envelope without re-running gates or pushing.

    Warm means: the working tree is clean, HEAD equals the SHA the current
    lineage's request for this round already binds, the kept envelope bytes
    still digest to what the ledger recorded, AND the authored claim is the
    same claim. Any of those false — dirty tree, moved tip, missing or
    altered copy, edited claim — and the cache is cold, which is the honest
    state; the cache never serves a stale envelope.

    The claim clause is here because the docstring above promised something
    the code did not do. The tip is only PART of an envelope's input: the
    Claim is authored, arrives from a file, and is not derivable from the
    tree. Emitting round 3 of this tool's own review hit it — a corrected
    claim at an unchanged tip returned the previous envelope, reporting
    `cached: true` for bytes that no longer said what the author meant. An
    idempotence check keyed on a proper subset of its inputs is the same
    defect class this review has now found at three layers, and it is worse
    here than elsewhere: the stale artifact it serves is the one the
    reviewer rules on.

    Round 4 F1 is the third and last visit from this family, so the rule is
    now stated once with no exemptions: **warm requires recorded and current
    claim state to be EQUAL, in both directions.** Round 3 added the claim.
    Round 3 F3 fixed supplied-against-unrecorded. Round 4 F1 found the
    opposite transition still warm — emit with `--claim-file`, then emit
    without it, and the tool served the retained envelope carrying the
    previous author's Claim, though this invocation supplied none and a cold
    emission would have rendered an empty objective and a default risk.

    Each of those three was fixed in the direction it was shown, which is why
    there were three. Every time, the hole was an exemption for "nothing to
    compare" — and absence IS a state: `--claim-file` present and absent are
    two different authored inputs, exactly as two different files are.

    So presence is part of the key. `NO_CLAIM` is recorded when no claim file
    was supplied, so "no claim" can be proved rather than inferred from a
    missing field. An event predating the field records nothing, equals
    neither state, and is therefore cold — unprovable is cold, which is the
    same rule round 3 F3 arrived at, now applied without a special case for
    the direction that happened not to be under review.

    Round 5 F1 removed the last one. `claim_state` defaults to a value that
    matches nothing, so a caller who names no state cannot warm this cache —
    the fourth transition was two UNKNOWNS comparing equal, which is the one
    equality that can never be proved. The default exists rather than being
    a required argument because the reviewer's falsification calls this
    helper without it and requires a cold answer, not a TypeError: an API
    whose default is safe is stronger than one whose contract is documented.
    """
    run = git or (lambda *a: _git(cfg.repo_root, *a))
    try:
        head = run("rev-parse", "HEAD")
        dirty = run("status", "--porcelain")
    except RuntimeError:
        return None
    if dirty:
        return None
    request = next((e for e in reversed(ledger.current())
                    if e.get("event") == "request"
                    and e.get("round") == round_no
                    and e.get("sha") == head), None)
    if request is None:
        return None
    # Round 5 F1: the `or ""` that used to be here folded an unrecorded
    # legacy event and an omitted current argument onto ONE value, so the
    # two unknowns compared equal and a legacy envelope came back warm. The
    # method said absence is never evidence while its own default API turned
    # absence on both sides into a cache hit — the fourth transition, in the
    # round whose claim called an untabulated fourth transition a Blocker.
    #
    # No folding. `None` (never recorded) equals neither NO_CLAIM nor any
    # digest nor the unstated default, so unknown never matches anything,
    # including another unknown. Two states are equal here only when both
    # were recorded and both are the same. The asymmetry is unchanged: cold
    # costs one re-emission, warm costs a reviewer ruling on a claim nobody
    # wrote.
    if request.get("claim_digest") != claim_digest:
        return None
    path = exchange_path(cfg, round_no, "request")
    if path is None or not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    if _digest_text(text) != request.get("source_digest"):
        return None
    return {"envelope": text, "sha": head, "round": round_no,
            "kept": str(path), "digest": request["source_digest"]}


def record_handoff(cfg: Config, ledger: Ledger, envelope: str,
                   round_no: int, claim_digest: str = "") -> dict:
    parsed = wire.parse_request(envelope)
    digest = _digest_text(envelope)
    size = len(envelope.encode("utf-8"))
    kept = keep_bytes(cfg, round_no, "request", envelope)
    # The request event, then (round 1 F6) the evidence the request carries,
    # content-addressed, so the repetition breaker's documented escape
    # condition is observable — one shape with `take` and `ledger add`.
    added = ledger.add_all(request_events(parsed, round_no, digest, size,
                                          claim_digest=claim_digest))
    reviewer = parsed.attrs.get("reviewer", "")
    return {"round": round_no, "sha": parsed.sha, "digest": digest,
            "bytes": size, "kept": kept, "recorded": bool(added),
            "reviewer": reviewer,
            # --as is part of the literal command, not an option to discover:
            # round 1 F4 made the declaration mandatory, and the adapters tell
            # agents to run what the tool printed without improvising flags.
            "reviewer_next": f"{TOOL_NAME} take {kept} --as {reviewer}"
            if not kept.startswith("not kept")
            else f"{TOOL_NAME} take - --as {reviewer} < <the envelope>",
            "author_next": "stop: hand the envelope to the reviewer — the "
                           "human sets the round in motion (standing "
                           "instructions); nothing else runs on this side until "
                           f"the verdict arrives, then `{TOOL_NAME} close "
                           f"--verdict <file>`"}


# ---------------------------------------------------------------------- take

def probe_target(cfg: Config, push: dict | None, sha: str,
                 base: str | None, fetch: bool = True, git=None,
                 retake: tuple[str, str] | None = None) -> dict:
    """Make the target real in THIS clone before a token is spent (§5.1).

    Every state is recorded distinctly: fetched (the stamp's fetch command
    ran and the object is present), present (the object was already here,
    fetch skipped or unnecessary), absent (refused). `git` is injectable
    the same way `ensure_pushed`'s is, so every refusal is testable without
    a network.
    """
    run = git or (lambda *a: _git(cfg.repo_root, *a))
    result = {"sha": sha, "base": base}
    if push is None:
        raise Refusal("the request carries no reachability stamp: a SHA the "
                      "reviewer cannot fetch is not a review target "
                      f"(§9bis.4); return the envelope to the author — "
                      f"`{TOOL_NAME} handoff` stamps it",
                      "")
    if push["state"] == "pushed" and fetch:
        try:
            run("fetch", push["url"], push["ref"])
            result["fetch"] = f"fetched {push['ref']} from {push['url']}"
        except RuntimeError as exc:
            raise Refusal(
                f"cannot fetch the reviewed ref: {exc}. The stamp says the "
                f"author observed it after pushing; this clone cannot see "
                f"it, so the target is not reachable from here (§9bis.4); "
                f"a person restores access to {push['url']} from this "
                f"machine, then re-runs the take",
                "") from exc
    elif push["state"] == "pushed":
        result["fetch"] = "skipped (--no-fetch)"
    else:
        result["fetch"] = ("not attempted: LOCAL-ONLY target, fetchable "
                           "from no other machine")

    def present(obj: str) -> bool:
        try:
            run("cat-file", "-e", f"{obj}^{{commit}}")
            return True
        except RuntimeError:
            return False

    if not present(sha):
        where = ("this is not the clone it was emitted from"
                 if push["state"] == "local-only" else "after the fetch")
        # Sweep F11: a runnable recovery only where one exists — the fetch
        # was skipped and the exact re-take is known; otherwise blocked,
        # because a fetch that just ran and did not bring the object is not
        # repaired by running it again.
        # `retake` is (envelope path, declared identity) — the two values
        # the literal re-take command needs, kept apart so the command's
        # shape is stated here, in the source, where the enumeration reads it.
        can_fetch = (push["state"] == "pushed" and not fetch
                     and retake is not None)
        raise Refusal(f"target {sha} is not present in this clone {where}: "
                      f"a SHA the reviewer cannot resolve is not a review "
                      f"target (§9bis.4)"
                      + ("" if can_fetch else "; a person fetches the "
                         "reviewed ref into this clone, or hands the "
                         "envelope back to the author"),
                      (f"git -C {cfg.repo_root} fetch {push['url']} "
                       f"{push['ref']} && {TOOL_NAME} take {retake[0]} "
                       f"--as {retake[1]}") if can_fetch else "")
    result["target"] = "present"
    if base:
        if not present(base):
            raise Refusal(f"base {base} is not present in this clone: the "
                          f"diff `{base[:12]}...{sha[:12]}` cannot be "
                          f"computed (§9bis.4); a person deepens this clone "
                          f"(`git fetch --unshallow`) or fetches the base "
                          f"ref, then re-runs the take",
                          "")
        try:
            run("merge-base", "--is-ancestor", base, sha)
            result["base"] = "ancestor of target"
        except RuntimeError as exc:
            raise Refusal(f"base {base} is not an ancestor of {sha} in this "
                          f"clone: the envelope's diff is undefined here; "
                          f"return the envelope to the author",
                          "") from exc
    return result


def probe_references(cfg: Config, reference_section: str,
                     sha: str | None = None, git=None) -> list[dict]:
    """Label every reference checked / mismatch / asserted / unavailable /
    unrecognised (§5.1 F4). Absent digests are 'asserted', never checked.

    With `sha` given (sweep F6), every reference is read from the TARGET
    TREE through git — `<sha>:<path>` — never from the working tree. The
    manifest's digests were computed over the author's clean tree at
    exactly that commit, so the object store holds the bytes they describe;
    the reviewer's checkout may be at another commit, or may hold nothing at
    all, and neither state is evidence about the request. Without `sha` the
    working tree is read (the historical behaviour, kept for callers that
    have no target to resolve against).

    Two defects lived here together. `asserted` was documented in this
    docstring and implemented below, but the grammar could not produce it, so
    the branch was dead and the promise empty. And a line the grammar did not
    match was `continue`d — silently dropped, so a reference the author
    declared required simply ceased to exist between the envelope and the
    reviewer, while the précis upstream still counted it. Neither is reported
    as an error here, because labelling is this function's whole job; the
    caller decides what an unreadable required reference means.
    """
    run = git or (lambda *a: _git(cfg.repo_root, *a))

    def kind(path: str) -> str | None:
        """'blob' | 'tree' | None, at the target when `sha` is given, else
        from the working tree."""
        if sha is None:
            full = cfg.repo_root / path
            return "blob" if full.is_file() else "tree" if full.is_dir() \
                else None
        try:
            return run("cat-file", "-t", f"{sha}:{path.rstrip('/')}")
        except RuntimeError:
            return None

    def digest(path: str) -> str:
        if sha is None:
            return sha256_file(cfg.repo_root / path)
        return _digest_bytes(run_bytes(cfg, git, "show", f"{sha}:{path}"))

    where = "in the target tree" if sha is not None else "in this checkout"
    out = []
    for line in reference_section.splitlines():
        if not line.strip() or line.strip().startswith("("):
            continue                      # blank, or the emitter's placeholder
        m = _REF_LINE_RE.match(line)
        if not m:
            out.append({
                "path": line.strip(),
                "status": "unrecognised: matches no form of the reference "
                          "grammar, so nothing about it can be checked"})
            continue
        path = m.group("path")
        entry = {"path": path}
        if m.group("unavailable"):
            entry["status"] = ("declared unavailable by the author; "
                               + ("present " if kind(path) else "absent ")
                               + where)
        elif m.group("dir") or m.group("note"):
            entry["status"] = ("directory " + ("present"
                                               if kind(path) == "tree"
                                               else "absent"))
        elif m.group("digest"):
            if kind(path) != "blob":
                entry["status"] = f"unavailable (not {where})"
            elif digest(path) == m.group("digest"):
                entry["status"] = "checked (digest matches)"
            else:
                entry["status"] = (f"mismatch: the file {where} does not "
                                   f"digest to the manifest's value")
        else:
            entry["status"] = "asserted (no digest to check)"
        out.append(entry)
    return out


def run_bytes(cfg: Config, git, *args: str) -> bytes:
    """Raw stdout of a git command, for content that must be digested as
    bytes. The injectable `git` runner returns text; a test that maps
    `show` returns the file's text and this encodes it back."""
    if git is not None:
        return git(*args).encode("utf-8")
    out = subprocess.run(["git", "-C", str(cfg.repo_root), *args],
                         capture_output=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: "
                           f"{out.stderr.decode('utf-8', 'replace').strip()}")
    return out.stdout


def _digest_bytes(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def target_config(cfg: Config, sha: str, git=None) -> Config:
    """The configuration that governs a request: the target commit's own
    review.toml, built through config.from_text; or, when the target
    carries none, this checkout's — stated as such in `source` (sweep F6).
    """
    from . import config as _config
    run = git or (lambda *a: _git(cfg.repo_root, *a))
    try:
        text = run("show", f"{sha}:{_config.CONFIG_BASENAME}")
    except RuntimeError:
        return dataclasses.replace(
            cfg, source=f"{cfg.source} (this checkout; the target carries "
                        f"no {_config.CONFIG_BASENAME})")
    return _config.from_text(
        text, cfg, source=f"target {_config.CONFIG_BASENAME} at {sha[:12]}")


def take(cfg: Config, ledger: Ledger, envelope: str, source: str,
         reviewer: str | None = None, fetch: bool = True,
         validate_items=None, git=None) -> dict:
    """The reviewer's one command. Returns the record; raises Refusal."""
    parsed = wire.parse_request(envelope)
    if not parsed.wrapped:
        raise Refusal(f"{source} is not a review request",
                      f"{TOOL_NAME} validate {source}")

    # Round 1 F4. The identity must be DECLARED, never defaulted. Falling back
    # to [roles] reviewer meant any process omitting --as permanently appended
    # a take event naming a reviewer who had asserted nothing — which the live
    # ledger already demonstrates. The CLI cannot prove who is running it, so
    # the honest options are an explicit declaration or no event at all; a
    # convenient default is the one option that writes a false record. Note
    # what this does and does not buy: `rejected_reviewers` enforces the
    # DECLARATION and cannot detect an actor who declares someone else.
    me = (reviewer or "").lower()
    addressed = parsed.attrs.get("reviewer", "").lower()
    if not me:
        raise Refusal("no reviewer identity was declared: `take` records who "
                      "ruled, and this tool cannot observe that — it can only "
                      "carry what you assert. [roles] reviewer names the "
                      "repository's default DIRECTION, not who is at this "
                      "keyboard, so it is not an answer here (§7: silence "
                      "must not pick a side). Re-run as "
                      f"`{TOOL_NAME} take {source} --as <your identity>`",
                      "")
    if me != addressed:
        raise Refusal(f"this envelope is addressed to reviewer "
                      f"{addressed!r}; you are {me!r} — the stamp names who "
                      f"rules, and taking it as someone else would review "
                      f"under a role the author did not assign (§7); hand "
                      f"it to the stamped reviewer, or have the author "
                      f"re-emit with the intended stamp",
                      "")

    # Round-2 F4: the TARGET-INDEPENDENT grammar first, before any git call
    # — wrapper and attributes, the closed section grammar, reference and
    # diff-shape grammar, the stamp's syntax. Sweep F6 moved validation
    # after the fetch so the target's own config could govern it, and in
    # doing so let a defective envelope choose the URL this clone fetched
    # from before it was established to be a request at all. Split: what
    # needs no configuration is judged here; what the target commit's
    # configuration governs is judged after the fetch, against it.
    structural = validate.validate_request(parsed, cfg, structural_only=True)
    errors = [i for i in structural if i.level == "error"]
    if errors:
        raise Refusal("the request fails validation ("
                      + ", ".join(i.code for i in errors) + "): a reviewer "
                      "does not rule on a defective envelope, and this "
                      "clone fetches nothing on its account",
                      f"{TOOL_NAME} validate {source}  # then return it to "
                      f"the author")

    round_no = int(parsed.attrs.get("round", "0") or 0)
    sha = parsed.sha or ""
    base_m = _BASE_LINE_RE.search(parsed.body)
    base = base_m.group(1) if base_m else None
    push = wire.parse_push_line(parsed.body)
    # Sweep F6: the target FIRST. Validation used to run before the fetch,
    # against whatever configuration this checkout happened to hold — and
    # an unborn or empty reviewer checkout holds none, so a valid envelope
    # was refused as T-UNDECLARED and sent back to its author for a fault
    # in the reviewer's tree, with zero git calls made. The stamp is what
    # makes the target real here; the target is what carries the config
    # that governs the request; so: fetch and resolve, then read the exact
    # target's review.toml, then validate against THAT — never against
    # unrelated working-tree bytes.
    target = probe_target(cfg, push, sha, base, fetch=fetch, git=git,
                          retake=(source, me))
    governing = target_config(cfg, sha, git=git)
    target["config"] = governing.source
    items = (validate_items(parsed, governing) if validate_items else [])
    errors = [i for i in items if i.level == "error"]
    if errors:
        raise Refusal("the request fails validation ("
                      + ", ".join(i.code for i in errors) + "): a reviewer "
                      "does not rule on a defective envelope",
                      f"{TOOL_NAME} validate {source}  # then return it to "
                      f"the author")
    reference = wire.section(parsed.sections, "reference")
    # References are read from the target tree as well: the manifest's
    # digests describe bytes at that commit, and this checkout — at another
    # commit, or empty — is not evidence about them.
    refs = probe_references(cfg, reference, sha=sha, git=git)

    digest = _digest_text(envelope)
    size = len(envelope.encode("utf-8"))
    kept = keep_bytes(cfg, round_no, "request", envelope)
    ledger.add_all(request_events(parsed, round_no, digest, size))
    ref_summary = {}
    for r in refs:
        key = r["status"].split(" ", 1)[0].rstrip(":")
        ref_summary[key] = ref_summary.get(key, 0) + 1
    ledger.add({"event": "take", "round": round_no, "sha": sha,
                "reviewer": me, "source_digest": digest,
                "fetch": target.get("fetch"), "references": ref_summary})
    diff_cmd = (f"git -C {cfg.repo_root} diff {base}...{sha}" if base
                else f"git -C {cfg.repo_root} show {sha}")
    return {"round": round_no, "sha": sha, "reviewer": me,
            "target": target, "references": refs, "kept": kept,
            "digest": digest, "diff": diff_cmd, "envelope": envelope,
            "then": f"write the verdict as <{parsed.tag}-review-verdict "
                    f'sha="{sha}"> and run `{TOOL_NAME} validate '
                    f"<verdict.md>`; then stop — do not start the next "
                    f"round (standing instructions)"}


# --------------------------------------------------------------------- close

def close_round(cfg: Config, ledger: Ledger, verdict_text: str, source: str,
                round_no: int | None = None, tokens: int | None = None,
                validate_items=None) -> dict:
    parsed = wire.parse_verdict(verdict_text)
    if not parsed.wrapped and parsed.verdict is None:
        raise Refusal(f"{source} is not a verdict", f"{TOOL_NAME} validate {source}")
    items = validate_items(parsed) if validate_items else []
    errors = [i for i in items if i.level == "error"]
    if errors:
        raise Refusal("the verdict fails validation ("
                      + ", ".join(i.code for i in errors) + "): it is "
                      "returned to the reviewer, not recorded",
                      f"{TOOL_NAME} validate {source}")
    # Round 1 F1 (Blocker). The SHA binding is the tool's central invariant and
    # a clean verdict is merge-authorizing evidence, so the round a verdict is
    # filed under may never be taken on the caller's word: `round_no or
    # round_for_sha(...)` let an explicit --round skip the lookup entirely, and
    # a clean verdict for an unrequested commit then wrote a `clean at the exact
    # reviewed SHA` lineage marker for a commit nobody reviewed. The derived
    # round is now authoritative and an explicit one may only AGREE with it.
    ambiguous = ledger.ambiguous_rounds_for_sha(parsed.sha)
    if ambiguous:
        raise Refusal(
            f"sha {parsed.sha} is bound by more than one OPEN request in this "
            f"lineage (rounds {ambiguous}), so which round this verdict "
            f"answers cannot be derived and must not be guessed — a verdict "
            f"closes the request that asked for it (round 2 F1)",
            f"{TOOL_NAME} brief  # identify the live round, then close the "
            f"superseded ones by decision")
    derived = ledger.round_for_sha(parsed.sha)
    if derived is None:
        raise Refusal(f"no request in the current lineage binds sha "
                      f"{parsed.sha}, so this verdict answers no round here — "
                      f"a verdict is closed against the request that asked for "
                      f"it, never against a round number supplied beside it",
                      f"{TOOL_NAME} brief {source}  # then close in the "
                      f"repository whose lineage requested this SHA")
    if round_no is not None and round_no != derived:
        raise Refusal(f"--round {round_no} does not hold the request for sha "
                      f"{parsed.sha}; that request is round {derived}. The "
                      f"round is derived from the SHA and an explicit value "
                      f"may only agree with it (round 1 F1)",
                      f"{TOOL_NAME} close --verdict {source}  # without --round")
    round_no = derived
    digest = _digest_text(verdict_text)
    conflict = verdict_conflict(ledger, round_no, digest)
    if conflict is not None:
        raise Refusal(f"round {round_no} is already ruled by a different "
                      f"verdict (recorded digest "
                      f"{conflict.get('source_digest', '?')[:16]}…); a round "
                      f"is ruled once, and a second ruling would let the "
                      f"author choose which one to answer (round-2 F1)",
                      f"{TOOL_NAME} ledger report")
    size = len(verdict_text.encode("utf-8"))
    kept = keep_bytes(cfg, round_no, "verdict", verdict_text)
    added = ledger.add_all(verdict_events(parsed, round_no, digest, size,
                                          tokens))
    clean = parsed.verdict == "clean to advance"
    record = {"round": round_no, "sha": parsed.sha, "verdict": parsed.verdict,
              "findings": len(parsed.findings), "closures": len(parsed.closures),
              "kept": kept, "digest": digest, "events_added": added}
    if clean:
        ledger.add({"event": Ledger.LINEAGE_CLOSED, "at_round": round_no,
                    "sha": parsed.sha, "outcome": "clean",
                    "reason": f"clean verdict on {parsed.sha} at round "
                              f"{round_no}", "authorized_by": "reviewer"})
        record["lineage"] = ("closed: clean at the exact reviewed SHA. Merge "
                             "authority is the account allowlist and the "
                             "agents' standing rules; this tool does not "
                             "merge (§9bis.2)")
        record["next"] = None
    else:
        record["lineage"] = "open"
        record["next"] = (f"{TOOL_NAME} respond --verdict {source} "
                          f"--from-json <dispositions.json> --out "
                          f"<disposition.md>; then {TOOL_NAME} handoff")
    return record


def waive(cfg: Config, ledger: Ledger, sha: str, reason: str, by: str,
          git=None) -> dict:
    """Record that a commit was deliberately NOT reviewed.

    Nothing in this tool ever gated a commit, so skipping a review has always
    been possible: you simply do not run `handoff`. What was missing is the
    other half — the ledger could not tell "we decided this did not need a
    review" from "nobody thought about it". Those are the two states this
    whole design exists to keep apart, and the one place it had not applied
    the rule to itself was the decision to use it at all.

    So a waiver is an EVENT, not an absence. It names the commit, the reason,
    and who authorized it, and the report counts waived commits beside
    reviewed ones — which makes "how much of this repository went unreviewed,
    and why" a question with an answer instead of a silence.

    Three things it deliberately is not. It does not authorize anything: the
    commit was already permitted, and this records a decision rather than
    granting one. It does not touch the round lineage — a waiver is not a
    round, has no findings and no verdict, and cannot close anything. And it
    is not something an agent may decide: `by` is the authorizing human, the
    reason is required, and an empty one refuses, because a waiver whose
    reason is blank records that a decision happened and not what it was.
    """
    if not reason.strip():
        raise Refusal(
            "a waiver is a recorded decision and the reason IS the record: "
            "without it the ledger says a review was skipped and cannot say "
            "why, which is the absence this event exists to replace",
            "")
    # Sweep F9: the authorizer is DECLARED, never defaulted. `--by` used to
    # default to `user`, so an agent running this verb permanently recorded
    # a human authorization nobody had declared — the false-actor defect
    # `take` had already removed for the reviewer identity, rebuilt in the
    # one verb whose entire purpose is to document a human decision. The
    # tool cannot observe who ran it; silence appends nothing.
    if not (by or "").strip():
        raise Refusal(
            "no authorizer was declared: a waiver records WHO decided a "
            "review was not needed, and this tool cannot observe that — it "
            "can only carry what is asserted. Skipping a review is not an "
            "agent's decision to take, and `user` is not inferred from "
            "silence",
            "")
    run = git or (lambda *a: _git(cfg.repo_root, *a))
    try:
        resolved = run("rev-parse", "--verify", f"{sha}^{{commit}}")
    except RuntimeError as exc:
        raise Refusal(
            f"{sha} does not resolve to a commit in this repository, so the "
            f"waiver would name nothing",
            f"git -C {cfg.repo_root} log --oneline -5") from exc
    # Sweep F10: reviewed and deliberately unreviewed are mutually exclusive
    # facts about a COMMIT, not lineage-local states — so the check reads
    # every request and verdict event in the ledger, closed lineages
    # included, and every imported legacy round. A commit reviewed in a
    # closed lineage was reviewed.
    reviewed = [e for e in ledger.events()
                if e.get("event") in ("request", "verdict")
                and e.get("sha") == resolved]
    if reviewed:
        raise Refusal(
            f"{resolved[:12]} was handed off for review (round "
            f"{reviewed[0].get('round', '?')}, lineage "
            f"{ledger.lineage_of(reviewed[0])}), so it is reviewed rather "
            f"than waived; a waiver may not overwrite a review that "
            f"happened, in this lineage or any closed one",
            f"{TOOL_NAME} ledger report")
    added = ledger.add({"event": "waiver", "sha": resolved, "reason": reason,
                        "authorized_by": by})
    return {"sha": resolved, "reason": reason, "authorized_by": by,
            "recorded": bool(added),
            "next": None}


def close_lineage(ledger: Ledger, reason: str, by: str) -> dict:
    rounds = ledger.rounds()
    if not rounds:
        raise Refusal("the current lineage has no rounds: nothing to close",
                      f"{TOOL_NAME} handoff  # opens round 1")
    if not reason.strip():
        raise Refusal("a lineage is closed by a recorded, reason-bearing "
                      "decision — the reason is the record",
                      "")
    if not (by or "").strip():
        # Sweep F9's class: the same silent `user` default lived here.
        raise Refusal("a lineage is closed by a NAMED human's decision; "
                      "who took it is not inferred from silence",
                      "")
    last = max(rounds)
    open_request = last not in ledger.completed_rounds()
    added = ledger.add({"event": Ledger.LINEAGE_CLOSED, "at_round": last,
                        "outcome": "decision", "reason": reason,
                        "authorized_by": by, "open_request": open_request})
    return {"lineage_closed_at_round": last, "open_request": open_request,
            "recorded": bool(added), "next_lineage": ledger.lineage_number(),
            "next": f"{TOOL_NAME} handoff --base <sha>  # round 1 of "
                    f"lineage {ledger.lineage_number()}, repo default cap"}

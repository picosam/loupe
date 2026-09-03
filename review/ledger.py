"""Append-only JSONL ledger, breakers and metrics (design §5.3c–d, §5.4).

One file per review-target lineage, outside the repo by default (§4, §10.2).
Events are deterministic-content-addressed: `uid` hashes the event minus its
timestamp, so re-adding the same event is a no-op and a bootstrap can be run
twice without duplicating history.

Everything in report() is COMPUTED from events, never asserted: breakers and
metrics are the empirical claims slice 1 exists to falsify, so nothing here
may take a model's (or an author's) word for a count.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import vocab

from . import vocab
from .fingerprint import resolve_identity

LEDGER_BASENAME = "ledger.jsonl"


def _uid(event: dict) -> str:
    src = {k: v for k, v in event.items() if k not in ("uid", "ts")}
    blob = json.dumps(src, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def material_id(*facts) -> str:
    """The content identity of the FACTS one breaker firing was computed
    from — the material a human sees when they audit it.

    Round-4 F1. `firing_id` reduced a firing to (breaker, round,
    fingerprint-or-limit), which is an identity only where the evaluation
    can produce at most one firing per key. Three breakers can produce
    more, or can produce the same key over changed material: `orphan`
    (one firing per unmatched companion BATCH, and a batch that later
    gains a companion), `unverifiable` (one per `cannot_execute` run, and
    a key admits several), `budget`/tokens (one per round, over a spend
    that grows). In each case a decision recorded against the firing the
    human read went on covering material that did not exist when they
    read it — the exact pre-authorization defect round-2 F3 removed from
    the breaker-and-round shape, one level in.

    So every firing declares its own material and the identity folds it
    in. Same facts re-read, same id: a decision keeps covering the firing
    it was taken on. Different or additional facts, different id: a new
    firing nobody has decided, which stops the loop again.
    """
    blob = json.dumps(facts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def firing_id(firing: dict) -> str:
    """The stable identity of one breaker firing: breaker, round, the
    fingerprint (or limit) it fired on, and the content identity of the
    material it fired on.

    Round-2 F3: an override that named only a breaker and a round ceiling
    covered any LATER firing that happened to share the name and round — a
    run event arriving after the verdict could create an `unverifiable`
    firing in an already completed round that a pre-recorded override
    already "covered". Round-4 F1: the same defect survived one level in,
    because a key is not a firing wherever the evaluation admits more than
    one firing per key (see `material_id`). Identity is what the human
    saw; a firing that did not exist when the decision was recorded is not
    one the decision took.

    An older ledger's `covers` string, written before the material half
    existed, no longer matches — which fails CLOSED: the breaker fires
    again and the human decides again, rather than an old decision
    silently covering material nobody has read.
    """
    tail = firing.get("fp") or firing.get("limit") or ""
    ident = f"{firing.get('breaker')}@{firing.get('round', '?')}:{tail}"
    material = firing.get("material")
    return f"{ident}#{material}" if material else ident


#: The `import` row kind that records a pre-ledger atomic claim — a RULING
#: made before this tool existed, carried in through the legacy import door.
ATOMIC_IMPORT_KIND = "atomic"


def is_ruling(event: dict) -> bool:
    """THE definition of a ruling, and the only one (lineage 20 round 9 F3).

    A ruling is what an ANSWER answers: the reviewer's finding, or a legacy
    atomic import standing in for one ruled before this ledger existed.
    `findings_in_round` has said exactly that since the corpus was imported,
    and the round-8 import preflight repeated the same two clauses in its
    own words — but the OPERATIONAL readers (`latest_rulings`,
    `_finding_rounds_by_identity`, `convergence`) each filtered `finding`
    alone. So the real corpus ruled 27 atomic claims in round 1 and
    `standing_findings` — the cohort `authorize_advance` makes a human
    account for by name — silently omitted the two nobody had settled.

    Three definitions of one thing are three chances to disagree, and this
    disagreement fell on the side that DROPS open findings. There is one
    predicate now, every reader goes through it, and a kind added here
    reaches the whole lifecycle at once instead of half of it.
    """
    kind = event.get("event")
    return kind == "finding" or (kind == "import"
                                 and event.get("kind") == ATOMIC_IMPORT_KIND)


#: Display fact of an ordinary finding -> the field a legacy atomic import
#: carries the same fact under. A legacy row has no `id` and no `title`; it
#: has the historical `legacy_id` and the `verbatim` claim text, which are
#: those facts in the vocabulary of the corpus it came from.
RULING_DISPLAY_ALIASES = (("id", "legacy_id"), ("title", "verbatim"))


def ruling_facts(event: dict) -> dict:
    """One ruling as its DISPLAY consumers read it — a waiver record, an
    advance's enumeration by name, the standing cohort's ordering.

    Only facts the row ALREADY carries under another name are filled in
    (`RULING_DISPLAY_ALIASES`). Nothing is invented: a legacy atomic import
    carries no anchor, so this does not give it one, and a reader asking for
    an anchor still gets the absence that is true. An ordinary finding comes
    back unchanged — the same object — so `_uid` over a raw event and every
    identity comparison elsewhere are untouched.
    """
    missing = [(name, alt) for name, alt in RULING_DISPLAY_ALIASES
               if not event.get(name) and event.get(alt)]
    if not missing:
        return event
    return {**event, **{name: event[alt] for name, alt in missing}}


class Ledger:
    """Append-only event log, on disk or in memory.

    Round-3 F9: an in-memory backend exists so the test suite needs no
    writable directory. A reviewer sandboxed read-only — which is the correct
    posture for a reviewer — could not otherwise run the tests that back most
    of this tool's claims, which made the evidence unverifiable by anyone but
    its author.
    """

    def __init__(self, directory: Path | None = None):
        self.path = Path(directory) / LEDGER_BASENAME if directory else None
        self._events: list[dict] | None = [] if directory is None else None

    @classmethod
    def in_memory(cls) -> "Ledger":
        return cls(None)

    def events(self) -> list[dict]:
        if self._events is None:
            self._events = []
            if self.path and self.path.is_file():
                for line in self.path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        self._events.append(json.loads(line))
        return self._events

    def add(self, event: dict) -> bool:
        """Append one event; returns False if an identical event exists."""
        event = dict(event)
        event["uid"] = _uid(event)
        if any(e.get("uid") == event["uid"] for e in self.events()):
            return False
        event.setdefault("ts", datetime.now(timezone.utc).isoformat(
            timespec="seconds"))
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, sort_keys=True,
                                    ensure_ascii=False) + "\n")
        self._events.append(event)
        return True

    def add_all(self, events: list[dict]) -> int:
        return sum(1 for e in events if self.add(e))

    # ------------------------------------------------------------- selectors

    LINEAGE_CLOSED = "lineage_closed"

    def closures_of_lineage(self) -> list[dict]:
        """Every `lineage_closed` marker in the file, oldest first."""
        return [e for e in self.events() if e.get("event") == self.LINEAGE_CLOSED]

    def current(self) -> list[dict]:
        """Events of the CURRENT review lineage: everything after the last
        `lineage_closed` marker (RVW-T9, `close`).

        One ledger file serves one repository across many reviews, so a
        lineage boundary has to be a recorded event, not a new file — the
        record stays append-only and auditable. Everything round-scoped
        (rounds, breakers, metrics, the round cap in force, dispositions
        answered) reads this view, so a cap override authorized for one
        lineage cannot silently become the next review's starting point,
        which is the property the cap-override docstring promised and, until
        this view existed, did not deliver: `effective_round_cap` read the
        last override in the file regardless of which review it was granted
        for. Fingerprint identity aliases stay global (see `lineage`).
        """
        events = self.events()
        start = 0
        for i, e in enumerate(events):
            if e.get("event") == self.LINEAGE_CLOSED:
                start = i + 1
        return events[start:]

    def lineage_number(self) -> int:
        """1 for the first review in this ledger, +1 per recorded closure."""
        return len(self.closures_of_lineage()) + 1

    def lineage_of(self, event: dict) -> int:
        """The lineage number an event belongs to: 1 plus the closures
        recorded before it (sweep F10 — a waiver refusal names which
        lineage reviewed the commit, closed or current)."""
        n = 1
        for e in self.events():
            if e is event or e.get("uid") == event.get("uid"):
                return n
            if e.get("event") == self.LINEAGE_CLOSED:
                n += 1
        return n

    def _by(self, kind: str) -> list[dict]:
        return [e for e in self.current() if e.get("event") == kind]

    def disposition_batches(self, round_no: int | None = None) -> list[dict]:
        """Every disposition EMISSION in the current lineage as ONE unit:
        the disposition row plus the `evidence` and `falsification_run`
        events derived from the same emission (lineage 6 round 2 F1).

        A recorded answer is up to three events (`disposition_events`), and
        supersession is a property of the ANSWER, not of the row alone: a
        superseded emission's `cannot_execute` run firing `unverifiable`
        after a newer emission's `pass` stands is the lifecycle disagreeing
        with itself. Companions bind by the `batch` stamp the emitter
        writes; an event recorded before the stamp existed binds to the
        nearest preceding row for its (round, identity) — the order the one
        emitter has always written.

        A companion that binds no row — a stamp matching no recorded
        emission, or an event with no row for its key at all (a legacy
        import, a hand-added event) — is an ORPHAN: it is kept as its own
        batch with `orphan: True` so no event silently vanishes, but it is
        NEVER standing and never the key's answer (round 3 F1: an orphan
        that stood beside the real answer let a run that belongs to no
        recorded emission certify an acceptance it never verified). A row
        arriving LATER with the orphan's exact stamp is the same emission
        completed out of order — a repair — and adopts the orphan's
        companions as one batch; a row with a different stamp supersedes
        nothing about the orphan, which stays on the audit surface
        (`orphan_companion_batches`) and fires the `orphan` breaker.

        Each dict: `key` (round, resolved fp), `disposition` (the row, or
        None for an orphan), `evidence`, `runs`, `standing` (whether this
        is the key's STANDING ANSWER — the newest disposition emission;
        orphans never stand), `orphan`. Evidence NOT sourced from a
        disposition — a verdict's, a request reference's — is no companion
        and is deliberately absent here.
        """
        batches: list[dict] = []
        newest_by_key: dict[tuple, dict] = {}
        by_stamp: dict[tuple, dict] = {}
        orphan_unstamped: dict[tuple, dict] = {}
        for e in self.current():
            kind = e.get("event")
            if kind not in ("disposition", "evidence", "falsification_run"):
                continue
            if kind == "evidence" and e.get("source") != "disposition":
                continue
            if round_no is not None and e.get("round") != round_no:
                continue
            key = (e.get("round"), self.resolve(e.get("fp")))
            if kind == "disposition":
                stamped = by_stamp.get((key, e["batch"])) \
                    if e.get("batch") else None
                if stamped is not None and stamped["orphan"]:
                    # The repair: a row completing the emission whose stamp
                    # its companions already carry. One batch, now an answer.
                    batch = stamped
                    batch["disposition"] = e
                    batch["orphan"] = False
                else:
                    batch = {"key": key, "disposition": e, "evidence": [],
                             "runs": [], "standing": True, "orphan": False}
                    batches.append(batch)
                    if e.get("batch"):
                        by_stamp[(key, e["batch"])] = batch
                prev = newest_by_key.get(key)
                if prev is not None and prev is not batch:
                    prev["standing"] = False
                batch["standing"] = True
                newest_by_key[key] = batch
                continue
            if e.get("batch"):
                target = by_stamp.get((key, e["batch"]))
            else:
                target = (newest_by_key.get(key)
                          or orphan_unstamped.get(key))
            if target is None:
                # An orphan: kept, visible, never standing (round 3 F1) —
                # a batch that is no answer must not render as one, feed a
                # metric, or lend its run to a different emission.
                target = {"key": key, "disposition": None, "evidence": [],
                          "runs": [], "standing": False, "orphan": True}
                batches.append(target)
                if e.get("batch"):
                    by_stamp[(key, e["batch"])] = target
                else:
                    orphan_unstamped[key] = target
            target["runs" if kind == "falsification_run"
                   else "evidence"].append(e)
        return batches

    def standing_disposition_batches(self, round_no: int | None = None
                                     ) -> list[dict]:
        """The standing ANSWER per (round, resolved fingerprint) — the
        one projection every operational consumer reads: the preflight,
        verdict-closure validation, the breakers, the reviewer-facing
        rendering and the per-round metrics. At most ONE batch per key,
        and every batch here carries its disposition row: an orphan
        companion is no answer and never appears (round 3 F1 — two
        standing batches for one key let a foreign run certify an
        acceptance). Raw event multiplicity and orphans are audit history:
        the file, and `orphan_companion_batches`."""
        return [b for b in self.disposition_batches(round_no)
                if b["standing"]]

    def orphan_companion_batches(self, round_no: int | None = None
                                 ) -> list[dict]:
        """The audit/anomaly surface (round 3 F1): every companion batch
        that binds no recorded disposition emission — a stamp matching no
        row, or a truly rowless companion. Kept and named, never standing:
        these certify nothing and attribute to nothing, and each one fires
        the `orphan` breaker so a human decides what it is."""
        return [b for b in self.disposition_batches(round_no)
                if b["orphan"]]

    def standing_dispositions(self, round_no: int | None = None) -> list[dict]:
        """The STANDING answer per finding: the newest disposition event for
        each resolved fingerprint, optionally limited to one round.

        An append-only ledger supersedes by recency; it does not forbid
        history. A disposition legitimately re-binds when the head moves
        under it — a regenerated artifact committed after the record — and
        until this selector existed the lifecycle read raw event
        multiplicity as "answered more than once" and had no legal way out
        of its own append-only rule (lineage 6 round 2; the same
        newest-record-decides rule `recorded_transport` follows). Every
        event stays in the file; what changes is which one is the answer.
        Keyed by (round, identity): a finding that returns in a later round
        is answered per round, and only re-emissions WITHIN a round
        supersede. Derived from `disposition_batches` so the row this
        returns and the companions the other consumers read can never name
        two different emissions (round 2 F1).
        """
        return [b["disposition"]
                for b in self.standing_disposition_batches(round_no)
                if b["disposition"] is not None]

    def lineage(self) -> list[dict]:
        # Identity aliases (fp1 -> fp2 etc.) describe finding identity across
        # the whole repository history and are deliberately NOT scoped.
        return [e for e in self.events() if e.get("event") == "lineage"]

    def effective_round_cap(self, default: int) -> int:
        """The cap in force for THIS review lineage.

        The configured cap is the default for every review in the repo; a
        single lineage is raised past it only by a recorded, reason-bearing
        override event, reusing the shape the design already trusts for
        stepping outside a rule (§7's `--override-role-assignment`). Keeping
        the authorization in the append-only ledger rather than in config
        means it is auditable, scoped to the review it was granted for, and
        cannot silently become the next review's starting point.
        """
        overrides = self._by("cap_override")
        return int(overrides[-1]["round_cap"]) if overrides else default

    def resolve(self, fp: str) -> str:
        return resolve_identity(fp, self.lineage())

    def rounds(self) -> list[int]:
        """Every round that has started (a request or a verdict exists) in
        the current lineage."""
        rs = {e["round"] for e in self.current()
              if e.get("event") in ("request", "verdict") and "round" in e}
        return sorted(rs)

    def rounds_for_sha(self, sha: str | None) -> list[int]:
        """Every round in the current lineage whose request binds `sha`."""
        seen: list[int] = []
        for e in self._by("request"):
            if e.get("sha") == sha and e["round"] not in seen:
                seen.append(e["round"])
        return seen

    def recorded_lineage_for_sha(self, sha: str | None) -> int | None:
        """The lineage a `take` (or a local `request`) recorded for `sha`,
        or None when nothing recorded one (F1: the git carrier's lineage).

        A `take` fetched over `git:<lineage>/<round>` stamps the CARRIED
        lineage on its event, because a fresh reviewer ledger's own
        `lineage_number()` has no relationship to the lineage the author's
        machine is on. A locally emitted request stamps none — its round
        was written under this same ledger's own progression, so
        `lineage_number()` already answers correctly for it. Callers use
        this as the first authority and fall back to `lineage_number()`
        only when it returns None.
        """
        for e in reversed(self.current()):
            if (e.get("sha") == sha and e.get("event") in ("request", "take")
                    and e.get("lineage") is not None):
                return int(e["lineage"])
        return None

    def round_for_sha(self, sha: str | None):
        """The OPEN round whose request binds `sha`, or None — never a guess.

        Round 2 F1: this returned the first historical match, so a SHA that
        appears in two rounds — a re-emission on an unchanged tip, or a round
        that re-reviews the same commit — resolved to the earlier one. A
        verdict then closed a round that was already answered and skipped the
        later round's dispositions entirely.

        Ruled rounds are excluded because a verdict answers an OPEN request;
        one already carrying a verdict is not awaiting an answer. If more than
        one open round still binds the SHA the answer is genuinely ambiguous,
        and `ambiguous_rounds_for_sha` reports it so the caller can refuse
        rather than pick.
        """
        open_rounds = self._open_rounds_for_sha(sha)
        return open_rounds[-1] if len(open_rounds) == 1 else None

    def _open_rounds_for_sha(self, sha: str | None) -> list[int]:
        ruled = set(self.completed_rounds())
        return [r for r in self.rounds_for_sha(sha) if r not in ruled]

    def ambiguous_rounds_for_sha(self, sha: str | None) -> list[int]:
        """The open rounds binding `sha` when there is more than one.

        Empty when the SHA resolves cleanly or not at all — those are the two
        states the caller already handles; this names only the third.
        """
        open_rounds = self._open_rounds_for_sha(sha)
        return open_rounds if len(open_rounds) > 1 else []

    def completed_rounds(self) -> list[int]:
        """Rounds whose verdict exists.

        Round-3 F2: progress breakers may only be evaluated over these. A
        round with a request and no verdict yet is in flight, not spinning —
        judging it as a loop failure made the breaker report untrustworthy
        during exactly the window an agent consults it.
        """
        return sorted({e["round"] for e in self._by("verdict")
                       if "round" in e})

    def _all_rulings(self) -> list[dict]:
        """Every ruling of the current lineage, in recorded order — THE
        ruling authority (`is_ruling`, round-9 F3).

        Every lifecycle reader goes through this: `findings_in_round`,
        `latest_rulings`, `_finding_rounds_by_identity` (and so
        `current_answers`, `standing_findings` and `_ruling_withdrawn`),
        `convergence` and the breakers. Raw events, never display copies —
        the breakers identify a firing's material by `_uid` of the event
        itself, so what they hash has to be what the file holds.
        """
        return [e for e in self.current() if is_ruling(e)]

    def findings_in_round(self, r: int) -> list[dict]:
        """Findings ruled in round r: verdict findings plus atomic imports."""
        return [e for e in self._all_rulings() if e.get("round") == r]

    # -------------------------------------------------------------- breakers

    def breakers(self, round_cap: int, token_budget: int | None = None,
                 blocking_severities: list[str] | None = None
                 ) -> list[dict]:
        """Evaluate every breaker over every completed round (§5.3d).

        Any firing is an escalation to the user, naming the rule and the
        decision required — the report carries them, it does not suppress.

        `blocking_severities` is the repo's declared blocking set, for the
        `unverifiable` rule (sweep F12): that breaker is defined over a
        BLOCKING finding's test and used to fire for every `cannot_execute`
        run regardless — a Medium finding's unexecutable test escalated with
        a rule text that named a severity it did not have. A run written by
        the product path carries `blocking` (bound when it was recorded,
        through validate.effective_blocking); a run without it is joined to
        its finding event's severity here, against the supplied set. A run
        that cannot be judged either way does not fire — a breaker that
        overstates is what makes the report untrustworthy where it is meant
        to drive a decision.
        """
        fired: list[dict] = []
        # Progress breakers read completed rounds only (F2); the budget
        # breaker reads every started round, because a fourth round that has
        # begun has already spent its budget.
        rounds = self.completed_rounds()
        # Standing emissions only (lineage 6 round 2 F1): a superseded
        # emission's run or refutation evidence is history, not a live
        # claim — an obsolete `cannot_execute` must not stop a lineage
        # whose standing answer is `pass`. Evidence from verdicts and
        # request references is no emission companion and stays raw.
        batches = self.standing_disposition_batches()
        dispositions = [b["disposition"] for b in batches
                        if b["disposition"] is not None]
        evidence = ([e for e in self._by("evidence")
                     if e.get("source") != "disposition"]
                    + [e for b in batches for e in b["evidence"]])
        runs = [x for b in batches for x in b["runs"]]
        # Round-9 F3: the ruling authority, not `finding` alone — a run
        # recorded against a legacy atomic ruling has a severity to be
        # judged by, and reading only `finding` events made it unjudgeable
        # and so silently non-blocking.
        all_findings = self._all_rulings()

        first_seen: dict[str, int] = {}
        for r in rounds:
            for f in self.findings_in_round(r):
                ident = self.resolve(f["fp"])
                first_seen.setdefault(ident, r)

        digests_before: set[str] = set()
        for r in rounds:
            round_findings = self.findings_in_round(r)
            round_evidence = [e for e in evidence if e.get("round") == r]
            new_digests = {e["digest"] for e in round_evidence
                           if e["digest"] not in digests_before}

            new_fps = [f for f in round_findings
                       if first_seen[self.resolve(f["fp"])] == r]
            returning = [f for f in round_findings
                         if first_seen[self.resolve(f["fp"])] < r]

            for f in returning:
                ident = self.resolve(f["fp"])
                prior = [d for d in dispositions
                         if d.get("round", 0) < r
                         and self.resolve(d["fp"]) == ident]
                last = prior[-1]["disposition"] if prior else None
                # Round 2 F6. Two kinds of evidence can exempt a returning
                # finding, and until now only one of them was reachable.
                #
                #   stamped — a verdict evidence event carrying this exact
                #             fingerprint (the reviewer's own prose).
                #   cited   — a reference-digest event from the round's
                #             REQUEST whose path this finding cites. The
                #             author answers by putting new bytes under a
                #             path the finding points at; that is new
                #             material for this identity even though the
                #             request could not know the fingerprint when it
                #             was emitted.
                #
                # Both are content-addressed, and `digests_before` is keyed on
                # bytes, so the same bytes reappearing at a new pointer are
                # not new evidence for either kind.
                cites = set(f.get("cites") or [])
                fp_new_evidence = {
                    e["digest"] for e in round_evidence
                    if e["digest"] not in digests_before
                    and (self.resolve(e.get("fp", "")) == ident
                         or (e.get("source") == "reference"
                             and e.get("of") in cites))}
                if last == "refuted" and not fp_new_evidence:
                    fired.append({
                        "breaker": "repetition", "round": r, "fp": ident,
                        "material": material_id("finding", _uid(f)),
                        "rule": "fingerprint returned after `refuted` with no "
                                "new (content-addressed) evidence",
                        "decision_required": "the reviewer must answer the "
                                             "refutation evidence or withdraw",
                    })
                passed = any(x for x in runs
                             if self.resolve(x["fp"]) == ident
                             and x.get("round", 0) < r
                             and x.get("status") == "pass")
                if last == "accepted" and passed:
                    fired.append({
                        "breaker": "stale", "round": r, "fp": ident,
                        "material": material_id("finding", _uid(f)),
                        "rule": "fingerprint returned after `accepted` and a "
                                "passing falsification test",
                        "decision_required": "check the reviewer's SHA binding",
                    })

            round_dispositions = [d for d in dispositions
                                  if d.get("round") == r]
            round_runs = [x for x in runs if x.get("round") == r]
            # Round-5 F1 / round-6 F2: a closure or disposition that
            # SETTLES the current ruling of some identity is progress,
            # even for an identity that does not return this round at
            # all. Scored `as_of_round=r` — the ruling current AT ROUND
            # r, never the lineage's eventual final ruling — so a LATER
            # re-raise of the same fingerprint cannot reach back and turn
            # an already-settled historical round into a spinning one; a
            # STALE withdrawal (one that does not target r's ruling) is
            # excluded either way and so still cannot suppress this
            # breaker.
            round_settles = [event for pairs in
                             self.current_answers(as_of_round=r).values()
                             for effect, event in pairs
                             if effect == vocab.ANSWER_SETTLES
                             and event.get("round") == r]
            if (r > min(rounds, default=r)
                    and not new_fps and not round_dispositions
                    and not new_digests and not round_runs
                    and not round_settles):
                fired.append({
                    "breaker": "no-progress", "round": r,
                    "material": material_id("no-progress", r),
                    "rule": "zero new fingerprints, zero disposition changes, "
                            "zero new material evidence, no changed "
                            "falsification outcome",
                    "decision_required": "the loop is spinning; escalate",
                })

            for x in runs:
                if x.get("round") == r and x.get("status") == "cannot_execute" \
                        and self._run_is_blocking(x, all_findings,
                                                  blocking_severities):
                    fired.append({
                        "breaker": "unverifiable", "round": r, "fp": x["fp"],
                        "material": material_id("run", _uid(x)),
                        "rule": "a blocking finding's falsification test "
                                "cannot be executed",
                        "decision_required": "a gate nobody can open is not a "
                                             "gate; decide its standing",
                    })

            digests_before |= {e["digest"] for e in round_evidence}

        # Round 3 F1: an unmatched companion is an anomaly, and the
        # conservative reading of an anomaly is an ESCALATION, not
        # evidence. An orphan feeds no other breaker and certifies no
        # acceptance — its `pass` exempts nothing, its `cannot_execute`
        # blocks nothing on another emission's account — it fires its own
        # breaker, whatever round it sits in, until a human decides what
        # it is. Completed-rounds scoping deliberately does not apply: the
        # product emitter writes the row before its companions, so an
        # orphan is never a normal in-flight state.
        for b in self.orphan_companion_batches():
            r_o, ident = b["key"]
            events = b["runs"] + b["evidence"]
            kinds = ", ".join(sorted({e["event"] for e in events}))
            stamp = next((e.get("batch") for e in events
                          if e.get("batch")), None)
            fired.append({
                # Every companion in the batch, so a batch that GAINS one
                # after a decision is a new firing rather than material
                # the earlier decision silently swallows — the unstamped
                # shape, where later companions join the one rowless
                # batch for their key, has no other discriminator.
                "breaker": "orphan", "round": r_o, "fp": ident,
                "material": material_id(
                    "companions", sorted(_uid(e) for e in events)),
                "rule": f"companion event(s) ({kinds}) bind no recorded "
                        f"disposition emission"
                        + (f" — batch stamp {stamp!r} matches no row"
                           if stamp else
                           " — no row exists for this key"),
                "decision_required": "an event that answers no recorded "
                                     "emission certifies nothing; audit "
                                     "the emitter or the imported events "
                                     "and decide its standing",
            })

        started = self.rounds()
        # Cumulative-token half of the budget rule (§5.3d, round-3 F6,
        # round-4 F5). The budget comes from `[limits] token_budget` and is
        # passed through by every product report path; the counts come from
        # `tokens` on request/verdict events, recorded by `ledger record-tokens`
        # because nothing in a no-LLM code path can measure them. Three
        # distinct states, and the report names which one it is in: no budget
        # declared, budget with no counts, budget with counts.
        tokens = self.token_state(token_budget)
        if tokens["state"] in ("live", "partial") and \
                tokens["spent"] > token_budget:
            # A partial sum is a lower bound, and a lower bound over the
            # budget is a true positive — the qualifier travels with the
            # rule so the firing never overstates what was measured.
            bound = (" — a LOWER BOUND; uncounted events remain"
                     if tokens["state"] == "partial" else "")
            fired.append({
                "breaker": "budget", "limit": "tokens",
                "round": max(started or [0]),
                # The measured spend, not just the fact of a breach: a
                # decision taken at 400 tokens over does not cover 10000.
                "material": material_id("tokens", tokens["state"],
                                        tokens["spent"], token_budget,
                                        list(tokens["per_event"])),
                "rule": f"cumulative tokens {tokens['spent']} > budget "
                        f"{token_budget} "
                        f"({' + '.join(str(n) for n in tokens['per_event'])})"
                        f"{bound}",
                "decision_required": "raise the token budget or settle by "
                                     "escalation",
            })

        if started and max(started) > round_cap:
            fired.append({
                "breaker": "budget", "limit": "rounds",
                "round": max(started),
                "material": material_id("rounds", max(started), round_cap),
                "rule": f"round count {max(started)} > cap {round_cap}",
                "decision_required": "continue past the cap or settle by "
                                     "escalation",
            })
        # The identity every consumer reads — the report, the
        # authorization's `covers`, the handoff refusal — computed once,
        # here, where the firings are made (round-4 F1).
        for fire in fired:
            fire["firing"] = firing_id(fire)
        return fired

    # ----------------------------------------------------------- convergence

    #: A fingerprint the reviewer has refused to withdraw this many times is
    #: a thread the loop is not closing.
    SUSTAINED_THREAD = 2
    #: An anchor that produces NEW fingerprints in this many rounds is a
    #: domain being hunted rather than a defect being fixed.
    HUNTED_ANCHOR = 2

    def convergence(self) -> dict:
        """Is this lineage closing its findings, or hunting the same
        domains? A REPORT, not a verdict — read what it counts, not the
        word it ends with.

        Added 2026-08-25, when the round cap stopped refusing. A count of
        rounds is a threshold: it fires on a lineage doing exactly what it
        should and stays silent on one that is genuinely stuck, because it
        measures duration and the question is direction. This measures
        direction, from what the record already holds.

        Two signals, and the second is the one a round count cannot see.

        `threads` — per finding identity, the reviewer's closure each round.
        A fingerprint SUSTAINED repeatedly is a claim the author keeps
        answering and the reviewer keeps rejecting; that is the loop failing
        to close one thing.

        `hunted` — an anchor whose findings are NEW fingerprints round after
        round. Every one of them may be real and every fix may land, and the
        domain still never closes, because what is being asked for is
        completeness over a set nobody can enumerate from inside. That is
        not a stuck author or a stubborn reviewer; it is a boundary demand
        with no stopping condition, and it looks like healthy progress from
        inside any single round — every finding true, every fix accepted,
        the same anchor back next time.

        Neither signal proves anything. A sustained thread can be a
        reviewer who is simply right, and a hunted anchor can be a genuinely
        rich domain being worked through in order. What the report gives a
        human is the shape of the loop over its whole length, which no
        single round shows.

        Round-9 F3: the rulings it counts come from the ONE ruling authority
        (`_all_rulings`), so a lineage whose first round was imported is
        measured over the claims it actually ruled. An anchor-less ruling —
        which every legacy atomic import is — joins `threads` and the
        per-round counts and joins NO anchor group: `hunted` asks which
        anchor keeps producing new fingerprints, and a ruling that names no
        anchor is evidence about none. Grouping them under a shared absent
        key would invent exactly the anchor data the row does not carry.
        """
        rounds = sorted(self.rounds())
        findings = self._all_rulings()
        closures = [e for e in self.current() if e.get("event") == "closure"]

        first_seen, last_seen, per_round, anchors = {}, {}, {}, {}
        finding_rounds: dict[str, list[int]] = {}
        for f in findings:
            ident = self.resolve(f.get("fp", ""))
            r = f.get("round", 0)
            first_seen.setdefault(ident, r)
            last_seen[ident] = max(last_seen.get(ident, r), r)
            finding_rounds.setdefault(ident, []).append(r)
            per_round.setdefault(r, []).append(ident)
            if f.get("anchor_path"):
                anchors.setdefault(f["anchor_path"], {}).setdefault(r, set()
                                                                    ).add(ident)

        threads = {}
        for ident, opened in sorted(first_seen.items()):
            ident_closures = sorted(
                (c for c in closures if self.resolve(c.get("fp", "")) == ident),
                key=lambda c: c.get("round", 0))
            fr = finding_rounds.get(ident, [])
            # Round-5 F2 / round-6 F1: withdrawn is relative to the
            # identity's NEWEST ruling, judged by what each closure
            # actually TARGETS (`_answer_target_round`) rather than by
            # round arithmetic alone — a withdrawal answering an earlier
            # ruling, or one sharing a round with a same-fingerprint
            # re-raise, has no say over the current one. The full
            # `closures` history below is untouched, so a stale
            # withdrawal still displays; it just stops immunizing the
            # thread from `stalled_threads`.
            withdrawn = any(
                c.get("closure") == "withdrawn"
                and self._answer_target_round("closure", c, fr)
                == last_seen[ident]
                for c in ident_closures)
            # Round-6 F3: a valid withdrawal closes a SEGMENT of this
            # thread's life, and a freshly re-raised identity starts a
            # new one. Sustained closures from a segment the reviewer
            # already ended must not carry into the new segment and make
            # a just-reopened thread instantly stalled — only sustains
            # recorded AFTER the most recent validly-targeted withdrawal
            # count toward the current segment's threshold.
            segment_start = max(
                (int(c.get("round") or 0) for c in ident_closures
                 if c.get("closure") == "withdrawn"
                 and self._answer_target_round("closure", c, fr) is not None),
                default=0)
            sustained = sum(1 for c in ident_closures
                            if c.get("closure") == "sustained"
                            and int(c.get("round") or 0) > segment_start)
            threads[ident] = {
                "opened_round": opened,
                "closures": [c.get("closure") for c in ident_closures],
                "sustained": sustained,
                "withdrawn": withdrawn,
                "anchor": next((f.get("anchor_path") for f in findings
                                if self.resolve(f.get("fp", "")) == ident), None),
            }

        stuck = sorted(i for i, t in threads.items()
                       if t["sustained"] >= self.SUSTAINED_THREAD
                       and not t["withdrawn"])
        # Round-4 F6: FRESH means first seen in that round. The first cut
        # rendered every identity present in a round, so a finding returning
        # under the same fingerprint was displayed as new — the report
        # overstating exactly the evidence it exists to weigh.
        hunted = {}
        for anchor, by_round in anchors.items():
            fresh = {r: sorted(i for i in ids if first_seen[i] == r)
                     for r, ids in sorted(by_round.items())}
            fresh = {r: ids for r, ids in fresh.items() if ids}
            distinct = set().union(*fresh.values()) if fresh else set()
            if len(fresh) >= self.HUNTED_ANCHOR and len(distinct) > 1:
                hunted[anchor] = fresh

        # Named states, so the report says which shape it is in rather than
        # leaving a reader to derive it — and never more than the counts
        # support.
        # Round-4 F3: `closing` used to mean only "not rising", so a loop
        # taking one fresh finding every round for ever reported as closing
        # — a reassuring word for the shape this report exists to expose,
        # handed to a human as the answer to whether the loop converges.
        # Closing now requires what the word claims: inflow actually falling
        # AND the reviewer actually withdrawing things. A flat loop is
        # `steady`, which is neither an alarm nor a reassurance.
        counts = [len(per_round.get(r, [])) for r in rounds]
        withdrew = any(t["withdrawn"] for t in threads.values())
        falling = len(counts) > 1 and counts[-1] < counts[0] and all(
            b <= a for a, b in zip(counts, counts[1:]))
        if len(rounds) < 2:
            state = "open"
        elif stuck:
            state = "stalled"
        elif hunted:
            state = "hunting"
        elif falling and withdrew:
            state = "closing"
        else:
            state = "steady"

        return {
            "state": state,
            "rounds": rounds,
            "findings_per_round": {r: len(per_round.get(r, []))
                                   for r in rounds},
            "new_per_round": {r: sum(1 for i in per_round.get(r, [])
                                     if first_seen[i] == r) for r in rounds},
            "threads": threads,
            "stalled_threads": stuck,
            "hunted_anchors": hunted,
            "withdrawn_any": withdrew,
            "reading": self._convergence_reading(state, stuck, hunted),
        }

    @staticmethod
    def _convergence_reading(state, stuck, hunted) -> str:
        if state == "stalled":
            return (f"{len(stuck)} finding(s) the reviewer has refused to "
                    f"withdraw twice or more: the loop is not closing them, "
                    f"and another round of the same answer will not either")
        if state == "hunting":
            return (f"{len(hunted)} anchor(s) produced NEW findings in "
                    f"several rounds. Every one may be real and every fix "
                    f"may have landed — that is what makes this shape hard "
                    f"to see from inside a round. Ask whether the domain "
                    f"can be closed at all from inside the artifact, or "
                    f"whether the claim should be narrowed to what a stated "
                    f"authority covers")
        if state == "closing":
            return ("findings per round are falling and the reviewer is "
                    "withdrawing them: the loop is closing what it opens")
        if state == "steady":
            return ("findings keep arriving and inflow is not falling. That "
                    "is not an alarm and not a reassurance — it says the "
                    "loop is neither stuck on one claim nor hunting one "
                    "domain, and is still opening as much as it closes")
        return "too little recorded to say anything about direction"

    # ---------------------------------------------------------------- tokens

    def token_state(self, token_budget: int | None) -> dict:
        """Which of the FOUR token states this ledger is in.

        A metric that reports a number is claiming the number was measured.
        `no_budget` and `no_counts` are both distinct from a spend of zero,
        and the breaker is inert in both — stated, not implied by silence
        (round-4 F5). Round-5 fp2:0a2cf43451b3d38f found the fourth state
        one level in: SOME envelope events counted. That is `partial`, never
        `live` — the sum is a lower bound whose breach is a true positive
        but whose silence means nothing, and the report says which events
        are uncounted rather than letting the sum impersonate a total.
        """
        counted = [e for e in self.current() if e.get("tokens") is not None]
        per_event = [int(e["tokens"]) for e in counted]
        uncounted = sorted(
            (e.get("round", 0), e["event"])
            for e in self._by("request") + self._by("verdict")
            if e.get("tokens") is None)
        if token_budget is None:
            return {"state": "no_budget", "spent": sum(per_event),
                    "per_event": per_event, "budget": None,
                    "why": "no token_budget declared in [limits]; the "
                           "cumulative-token breaker cannot fire"}
        if not counted:
            return {"state": "no_counts", "spent": 0, "per_event": [],
                    "budget": token_budget,
                    "why": f"budget {token_budget} declared, but no round "
                           f"carries a token count; absent is not zero, so "
                           f"the breaker cannot fire"}
        if uncounted:
            named = ", ".join(f"round {r} {kind}" for r, kind in uncounted)
            return {"state": "partial", "spent": sum(per_event),
                    "per_event": per_event, "budget": token_budget,
                    "uncounted": [f"round {r} {kind}" for r, kind in uncounted],
                    "why": f"{sum(per_event)} of {token_budget} tokens over "
                           f"{len(per_event)} counted events is a LOWER "
                           f"BOUND: {len(uncounted)} envelope event(s) carry "
                           f"no count ({named}). A breach of the budget by "
                           f"the bound is real; the bound staying under it "
                           f"means nothing"}
        return {"state": "live", "spent": sum(per_event),
                "per_event": per_event, "budget": token_budget,
                "why": f"{sum(per_event)} of {token_budget} tokens over "
                       f"{len(per_event)} counted events, every envelope "
                       f"event counted"}

    # --------------------------------------------------------------- metrics

    def latest_rulings(self) -> dict[str, dict]:
        """The NEWEST ruling of every finding identity in this lineage:
        resolved fingerprint -> the finding event of its latest round.

        The identity is what makes a finding one finding across rounds and
        aliases; the newest ruling is what an answer must answer, and whose
        display facts (id, severity, title) an artifact naming it carries.
        A finding id is round-scoped, so it is never the key here.

        Every RULING (`_all_rulings`), not `finding` events alone (round-9
        F3), and each one carried with its display facts (`ruling_facts`) —
        a legacy atomic ruling names itself `legacy_id`/`verbatim`, and a
        consumer that renders `id` and `title` was reading None off the
        exact rows this method had been dropping.
        """
        latest: dict[str, dict] = {}
        for event in self._all_rulings():
            fp = self.resolve(event.get("fp", ""))
            if not fp:
                continue
            if (fp not in latest
                    or int(event.get("round") or 0)
                    >= int(latest[fp].get("round") or 0)):
                latest[fp] = ruling_facts(event)
        return latest

    def _finding_rounds_by_identity(self, as_of_round: int | None = None
                                    ) -> dict[str, list[int]]:
        """Resolved identity -> every round it was RULED in (`_all_rulings`,
        round-9 F3: a legacy atomic import is a ruling, and an answer bound
        by round arithmetic over a ruling set missing half its members
        binds to the wrong ruling or to none)."""
        result: dict[str, list[int]] = {}
        for f in self._all_rulings():
            r = int(f.get("round") or 0)
            if as_of_round is not None and r > as_of_round:
                continue
            fp = self.resolve(f.get("fp", ""))
            if fp:
                result.setdefault(fp, []).append(r)
        return result

    @staticmethod
    def _answer_target_round(kind: str, event: dict,
                             finding_rounds) -> int | None:
        """The finding round `event` (a disposition, closure or waiver)
        actually answers for its identity (round-6 F1).

        A disposition answers its own round's ruling exactly — `respond`
        derives its round from the finding it responds to, so the two are
        always the same round by construction. A closure or waiver instead
        answers whatever ruling was standing at or before its own round:
        `answers_round`, stamped by the recording path from the specific
        disposition record the closure closes, is authoritative when
        present — the one case ledger round numbers alone cannot resolve,
        because a verdict may close a prior ruling AND re-raise the same
        fingerprint as a new one in the same breath, landing both events
        at the identical round. Absent it (hand-built or legacy events),
        the latest finding round at or before the event's own round is the
        best available answer, matching every case that is not that
        collision.
        """
        r = int(event.get("round") or 0)
        if kind == "disposition":
            return r
        explicit = event.get("answers_round")
        if explicit is not None:
            return int(explicit)
        at_or_before = [fr for fr in finding_rounds if fr <= r]
        return max(at_or_before) if at_or_before else None

    def current_answers(self, as_of_round: int | None = None
                        ) -> dict[str, list[tuple[str, dict]]]:
        """Every recorded answer that answers the NEWEST ruling of its
        identity, as (effect, event) pairs per resolved fingerprint, with
        the effect read from `vocab.FINDING_ANSWERS`.

        THE LIFECYCLE DERIVATION (lineage 20 rounds 3-4). An answer is
        round-bound: recorded at round r, it answers the newest ruling only
        if it targets that ruling's round (`_answer_target_round`). Before
        this existed the three answer kinds were bound three different
        ways — an acceptance to its round, a withdrawal and a waiver to the
        identity for all time — so a finding withdrawn or waived in round 1
        and re-raised in round 2 was silently answered, and an advance
        closed over a ruling nobody had answered. One binding rule for
        every kind is the repair, and the table is what makes the kind set
        closed.

        `as_of_round`, when given, computes this AS IF the lineage ended
        there (round-6 F2): only findings at or before it exist, so a
        LATER re-raise cannot retroactively make an earlier round's
        genuine settlement disappear when a historical round is scored
        against this same method (`breakers`'s no-progress check).

        Dispositions come through `standing_dispositions`, so a re-emission
        within a round supersedes as it does everywhere else; closures and
        waivers are the raw events, which the tool never re-emits.
        """
        finding_rounds = self._finding_rounds_by_identity(as_of_round)
        latest = {fp: max(rounds) for fp, rounds in finding_rounds.items()}
        answers: dict[str, list[tuple[str, dict]]] = {}

        def consider(kind: str, term, event: dict) -> None:
            if as_of_round is not None \
                    and int(event.get("round") or 0) > as_of_round:
                return
            fp = self.resolve(event.get("fp", ""))
            if fp not in latest:
                return
            target = self._answer_target_round(kind, event,
                                               finding_rounds.get(fp, ()))
            if target is None or target != latest[fp]:
                return
            effect = vocab.FINDING_ANSWERS.get((kind, term))
            if effect is None:
                return
            answers.setdefault(fp, []).append((effect, event))

        for event in self._by("closure"):
            consider("closure", event.get("closure"), event)
        for event in self.standing_dispositions():
            consider("disposition", event.get("disposition"), event)
        for event in self._by(vocab.FINDING_WAIVER_EVENT):
            consider(vocab.FINDING_WAIVER_EVENT, None, event)
        return answers

    def standing_findings(self) -> list[dict]:
        """Findings of this lineage that are still OPEN, newest ruling first.

        THE LIFECYCLE AUTHORITY for advancing over open findings (lineage 20
        round 3 F2), derived from `current_answers` and nothing else: the
        newest ruling of each identity stands unless a CURRENT answer to it
        SETTLES it. Before this existed, `authorize_advance` asked only
        whether ANY human waiver had been recorded, so waiving one of two
        open findings emitted an authorization naming one and silently
        killed the other — the omission the symmetry rule exists to
        prohibit, reintroduced by the mechanism meant to make overrides
        visible.

        A human waiver deliberately does NOT remove a finding from this set.
        It records that the finding STANDS UNFIXED, which is the opposite of
        resolving it — and the set is what an authorization must enumerate,
        so a waiver that shrank it would hide the very findings the artifact
        exists to name. `authorize_advance` requires every member of this
        set to carry exactly one current waiver (`current_waivers`).

        Every OPEN answer leaves the finding here, and that is deliberate
        rather than an omission: `refuted` awaits the reviewer; `deferred`,
        `preference` and `escalated` record where the work went or who must
        decide; a sustained or reclassified identity has not been withdrawn.
        None of them is a finding resolved, so an advance over one is
        exactly what a human must be asked to authorize by name.
        """
        answers = self.current_answers()
        standing = [ruling for fp, ruling in self.latest_rulings().items()
                    if not any(effect == vocab.ANSWER_SETTLES
                               for effect, _ in answers.get(fp, []))]
        return sorted(standing,
                      key=lambda e: (-int(e.get("round") or 0),
                                     str(e.get("id") or "")))

    def current_waivers(self) -> dict[str, dict]:
        """Resolved fingerprint -> the human waiver answering its NEWEST
        ruling. A waiver recorded against an earlier ruling of the same
        identity is stale and is not here (lineage 20 round 4 F1): an answer
        to an older ruling is not an answer to a later one."""
        return {fp: event
                for fp, pairs in self.current_answers().items()
                for effect, event in pairs
                if effect == vocab.ANSWER_OVERRULES}

    def open_round(self) -> int | None:
        """A round whose request is recorded and whose verdict is not.

        An advance is about a ruling, so it may not be taken while a newer
        request is awaiting one: closing at the older ruled SHA would bind
        an authorization to a commit that is no longer what the loop is
        working on (lineage 20 round 3 F2).
        """
        requested = {e.get("round") for e in self.current()
                     if e.get("event") == "request"}
        ruled = {e.get("round") for e in self.current()
                 if e.get("event") == "verdict"}
        pending = [r for r in requested - ruled if r is not None]
        return max(pending) if pending else None

    def _withdrawn_identities(self) -> set[str]:
        return {self.resolve(e["fp"]) for e in self._by("closure")
                if e.get("closure") == "withdrawn"}

    def _ruling_withdrawn(self, ident: str, ruling_round: int) -> bool:
        """Whether a withdrawal answers the ruling `ident` carried at
        `ruling_round` SPECIFICALLY — the one it TARGETS
        (`_answer_target_round`), never merely one recorded somewhere in
        its history (round-5 F3, round-6 F1).

        `_withdrawn_identities` answers "was this identity ever withdrawn",
        which binds a withdrawal to every ruling the identity has ever
        carried rather than the one it actually answers: a round-1
        withdrawal that predates a round-2 re-ruling is not an answer to
        that later ruling, and a round-2 withdrawal with no intervening
        re-ruling is still the answer to a round-1 refutation — and a
        withdrawal that shares its own round with a same-fingerprint
        re-raise answers whichever ruling its stamped `answers_round`
        names, never whichever one round arithmetic would guess.
        """
        finding_rounds = self._finding_rounds_by_identity().get(ident, [])
        return any(
            self._answer_target_round("closure", e, finding_rounds)
            == ruling_round
            for e in self._by("closure")
            if e.get("closure") == "withdrawn"
            and self.resolve(e.get("fp", "")) == ident)

    def _round_tokens(self, r: int, requests: dict, verdicts: dict):
        """Tokens for one round, or the reason there are none (round-4 F5)."""
        parts = {k: ev.get("tokens") for k, ev in
                 (("request", requests.get(r, {})),
                  ("verdict", verdicts.get(r, {})))}
        counted = {k: v for k, v in parts.items() if v is not None}
        if not counted:
            return "not captured (no token count recorded for this round)"
        total = sum(int(v) for v in counted.values())
        detail = ", ".join(f"{k} {v}" for k, v in sorted(counted.items()))
        missing = sorted(k for k, v in parts.items() if v is None)
        if missing:
            return f"{total} partial ({detail}; {'/'.join(missing)} uncounted)"
        return f"{total} ({detail})"

    def metrics(self, gate_manifest: list[str] | None = None) -> dict:
        """Every §5.4 metric derivable from this ledger; the underivable ones
        are reported as 'not captured', never silently zero (absent != none).
        """
        per_round: dict[int, dict] = {}
        # Standing answers, not raw events: a superseded disposition is
        # history, and a metric that counted it would report one finding
        # answered twice (lineage 6 round 2). And ROUND-LOCAL standing
        # answers (round 2 F1): a fingerprint returning in a later round is
        # answered per round, and attributing every round's answer to every
        # round that saw the identity reported both under each.
        verdicts = {e["round"]: e for e in self._by("verdict")}
        requests = {e["round"]: e for e in self._by("request")}

        for r in self.rounds():
            findings = self.findings_in_round(r)
            idents = {self.resolve(f["fp"]) for f in findings}
            r_batches = [b for b in self.standing_disposition_batches(
                             round_no=r)
                         if b["disposition"] is not None
                         and b["key"][1] in idents]
            r_disp = [b["disposition"] for b in r_batches]
            disp_counts = Counter(d["disposition"] for d in r_disp)
            subtype_counts = Counter(d["subtype"] for d in r_disp
                                     if d.get("subtype"))
            n = len(findings)
            # §5.4 defines this as findings whose falsification test is a
            # command ALREADY IN THE GATE MANIFEST. Round-3 F15: trusting the
            # label instead counted gates that do not exist. With no manifest
            # declared the metric is not computable, and says so; the labels
            # survive as candidates, which is what they always were.
            # Round-4 F8: the manifest is a list of stable gate IDS, supplied
            # by every product report path rather than only by a unit test —
            # ids are what the repo declared and what a finding can name, and
            # they survive the command changing underneath them.
            labelled = [f for f in findings if f.get("preventable_by")]
            if gate_manifest and labelled and len(labelled) < len(findings):
                # Round 2 F7: a PARTIAL set of labels was divided by the full
                # finding count and reported as a share, so unlabelled
                # findings silently counted as "no gate would have caught
                # this". Round 1 handled none-labelled and not some-labelled —
                # the case, not the class. Every denominator member must be
                # measured or the share is not one.
                counted = [f for f in labelled
                           if f["preventable_by"] in gate_manifest]
                preventable_metric = {
                    "count": None, "of": n,
                    "share": (f"partially captured ({len(labelled)} of "
                              f"{len(findings)} findings carry a gate label); "
                              f"a share over an unmeasured denominator is not "
                              f"a share"),
                    "labelled": len(labelled),
                    "gates": sorted({f["preventable_by"] for f in counted}),
                }
            elif gate_manifest and not labelled and findings:
                # Round 1 F7: with a manifest declared and no finding carrying
                # a label, this reported count 0 / share 0% — a measured
                # zero, indistinguishable from "we checked and no gate would
                # have caught any of them". Nothing was measured: the label
                # was never captured. Absent is not zero.
                preventable_metric = {
                    "count": None, "of": n,
                    "share": "not captured (no finding declared a gate id; "
                             "absent is not zero)",
                    "gates": [],
                }
            elif gate_manifest:
                counted = [f for f in labelled
                           if f["preventable_by"] in gate_manifest]
                preventable_metric = {
                    "count": len(counted), "of": n,
                    "share": self._rate(len(counted), n),
                    "gates": sorted({f["preventable_by"] for f in counted}),
                    "labelled": len(labelled),
                }
            else:
                preventable_metric = {
                    "count": None, "of": n,
                    "share": "not computable (no gate manifest declared)",
                    "candidate_gates": sorted(
                        {f["preventable_by"] for f in labelled}),
                    "candidates": len(labelled),
                }
            # §5.4 defines the numerator as refuted findings with evidence
            # that the reviewer then withdrew. Round-3 F14: counting raw
            # `refuted` reported unresolved disagreements as false
            # positives. Round-5 F3: the withdrawal must answer THIS
            # round's ruling — `_ruling_withdrawn` binds it there rather
            # than to any withdrawal the identity has ever carried.
            refuted_withdrawn = [
                d for d in r_disp
                if d["disposition"] == "refuted"
                and self._ruling_withdrawn(self.resolve(d["fp"]), r)
                and str(d.get("payload", {}).get("evidence", "")).strip()]
            accepted = [b for b in r_batches
                        if b["disposition"]["disposition"] == "accepted"]
            # §5.4 unverified-acceptance: an acceptance whose verification is
            # not a run of the named test. This read an optional payload key
            # (`verification_kind`) that no product path wrote, so every
            # lineage-2 round reported "not classifiable" for every
            # acceptance. It now reads the `falsification_run` events that
            # `respond` records from the disposition's falsification record
            # — the same events the breakers read — and keeps the states
            # apart: proven, unproven, unexecutable, and the two kinds of
            # absence (the finding named no test; a test was named and no run
            # was recorded, which is the legacy shape and the rigor leak).
            named = {self.resolve(f["fp"]): bool(
                str(f.get("falsification", "")).strip()) for f in findings}
            # The standing emission's OWN run, never a superseded one and
            # never a foreign one: a stale `cannot_execute` beside a
            # standing `pass` is history (round 2 F1), and an orphan's run
            # attributed by a shared (fingerprint, round) key certified an
            # acceptance it never verified (round 3 F1). The run is read
            # from the exact batch the accepted row belongs to — batch-
            # exact by construction, not by a collapsing lookup.
            ver_kinds = Counter(self._acceptance_state(
                b["runs"][-1] if b["runs"] else None,
                named.get(b["key"][1], False)) for b in accepted)
            per_round[r] = {
                "finding_ids": verdicts.get(r, {}).get(
                    "finding_ids", len(self._by_round_parent(r)) or n),
                "atomic_claims": n,
                "severity_spread": dict(Counter(
                    f.get("severity", "?") for f in findings)),
                "dispositions": dict(disp_counts),
                "disposition_subtypes": dict(subtype_counts),
                "refutation_rate": self._rate(len(refuted_withdrawn), n),
                "refutations_open": disp_counts["refuted"] -
                len(refuted_withdrawn),
                "deferral_rate": self._rate(disp_counts["deferred"], n),
                "deterministic_preventable": preventable_metric,
                "unverified_acceptance": dict(ver_kinds),
                "envelope_bytes": {
                    "request": requests.get(r, {}).get("bytes"),
                    "verdict": verdicts.get(r, {}).get("bytes"),
                },
                "tokens": self._round_tokens(r, requests, verdicts),
            }

        clean_rounds = [r for r, v in verdicts.items()
                        if v.get("verdict") == vocab.VERDICT_CLEAN]
        return {
            "rounds": per_round,
            "rounds_to_clean": (min(clean_rounds) if clean_rounds
                                else f"open (no clean verdict in "
                                     f"{len(verdicts)} rounds)"),
        }

    def _by_round_parent(self, r: int) -> list[dict]:
        return [e for e in self._by("import")
                if e.get("round") == r and e.get("kind") == "parent"]

    @staticmethod
    def _rate(num: int, den: int) -> str:
        return f"{num}/{den}" + (f" ({round(100 * num / den)}%)" if den else "")

    @staticmethod
    def _acceptance_state(run: dict | None, test_named: bool) -> str:
        """Plain-language bucket for one accepted disposition (§5.4).

        Five states, none folded into another: the two absences differ —
        a finding that named no test cannot have one run, while a named
        test with no recorded run is the leak the metric exists to count.
        """
        if run is None:
            return ("named test, no run recorded" if test_named
                    else "no test named")
        status, mutation = run.get("status"), run.get("mutation")
        if status == "cannot_execute":
            return "test could not be executed"
        if status == "pass" and mutation == "fails_without_fix":
            return "test passed, mutation proven"
        if status == "pass" and mutation in ("not_run", None):
            return "test passed, mutation not run"
        # `fail` and `passes_without_fix` never validate into an acceptance;
        # if one is in the ledger anyway (a hand-added event), say what it is
        # rather than bucket it as something it is not — a passing test whose
        # mutation ALSO passed proves nothing, which is not "not run".
        return f"recorded {status}/{mutation}"

    def waivers(self) -> list[dict]:
        """Commits recorded as deliberately unreviewed, oldest first.

        Read across the WHOLE ledger rather than the current lineage: a
        waiver is a decision about a commit, not a move within a review, and
        closing a lineage does not un-skip what was skipped.
        """
        # `events()` and not `_by()`, which is lineage-scoped: a commit that
        # was skipped stays skipped after a lineage closes, exactly as
        # identity aliases are unscoped for the same reason.
        return [{k: e.get(k) for k in ("sha", "reason", "authorized_by")}
                for e in self.events() if e.get("event") == "waiver"]

    def _run_is_blocking(self, run: dict, findings: list[dict],
                         blocking_severities: list[str] | None) -> bool:
        """Whether a falsification run belongs to a BLOCKING finding
        (sweep F12). The run's own `blocking` stamp first; else the latest
        finding event carrying the run's identity, at or before the run's
        round, judged against the supplied blocking set by severity (the
        run's existence already evidences a named test). Unjudgeable
        is False: the breaker states a severity, so it may not fire on one
        it cannot establish."""
        if run.get("blocking") is not None:
            return bool(run["blocking"])
        if blocking_severities is None:
            return False
        ident = self.resolve(run.get("fp", ""))
        candidates = [f for f in findings
                      if self.resolve(f.get("fp", "")) == ident
                      and f.get("round", 0) <= run.get("round", 0)]
        if not candidates:
            return False
        f = max(candidates, key=lambda e: e.get("round", 0))
        # Severity alone here: a run event exists only because a test was
        # named and run (or could not be), so the "named test" half of the
        # validator's rule is already evidenced by the run itself.
        return f.get("severity") in blocking_severities

    def report(self, round_cap: int, gate_manifest: list[str] | None = None,
               token_budget: int | None = None,
               blocking_severities: list[str] | None = None) -> dict:
        waived = self.waivers()
        return {
            "breakers_fired": self.breakers(round_cap, token_budget,
                                            blocking_severities),
            "round_cap": round_cap,
            "gate_manifest": list(gate_manifest) if gate_manifest else [],
            "tokens": self.token_state(token_budget),
            # The direction of the loop, beside its spend. A budget says how
            # much has been used; this says whether it is being used to
            # close anything (2026-08-25).
            "convergence": self.convergence(),
            "metrics": self.metrics(gate_manifest),
            # The skipped half of the record. Reported even when empty, and
            # as a list rather than a count, because "which commits and why"
            # is the question a waiver exists to answer — a bare number would
            # be the same silence in a different shape.
            "waived": {"count": len(waived), "commits": waived},
            "events": len(self.events()),
            "lineage": {"number": self.lineage_number(),
                        "events": len(self.current()),
                        "closed_before": [
                            {k: c.get(k) for k in ("at_round", "outcome",
                                                   "reason", "authorized_by")}
                            for c in self.closures_of_lineage()]},
            "ledger": str(self.path),
        }


def render_convergence_md(c: dict) -> str:
    """Markdown rendering of convergence(); every number comes from the
    dict, and the reading beneath them says only what they support."""
    lines = [f"## Convergence — **{c['state']}**", "", c["reading"], ""]
    lines.append("| round | findings | new identities |")
    lines.append("|---|---|---|")
    for r in c["rounds"]:
        lines.append(f"| {r} | {c['findings_per_round'].get(r, 0)} | "
                     f"{c['new_per_round'].get(r, 0)} |")
    lines.append("")
    if c["stalled_threads"]:
        lines.append("**Threads the reviewer will not withdraw**")
        for ident in c["stalled_threads"]:
            t = c["threads"][ident]
            lines.append(f"- `{ident}` opened round {t['opened_round']}, "
                         f"sustained {t['sustained']}× "
                         f"({t['anchor'] or 'no anchor'})")
        lines.append("")
    if c["hunted_anchors"]:
        lines.append("**Anchors producing NEW findings round after round**")
        for anchor_path, by_round in c["hunted_anchors"].items():
            rounds = ", ".join(f"round {r}: {len(ids)}"
                               for r, ids in by_round.items())
            lines.append(f"- `{anchor_path}` — {rounds}")
        lines.append("")
    return "\n".join(lines)


def render_report_md(report: dict, pending_round: int | None = None) -> str:
    """Markdown rendering of report(); every number comes from the dict.

    `pending_round` names the round whose request is being emitted FROM this
    snapshot and is therefore not in it (lineage 20 round 4 tool feedback:
    a request read `Round 4 of 3` beside `Breakers fired: 0`, because
    handoff renders the report before it records the request). The line
    says so rather than leaving a reader to reconcile the two numbers.
    """
    lines = ["## Ledger report — breakers and metrics (§5.3–5.4)", ""]
    fired = report["breakers_fired"]
    lines.append(f"Breakers fired: **{len(fired)}** "
                 f"(round cap {report['round_cap']})")
    if pending_round is not None:
        lines.append(f"Snapshot taken before round {pending_round} is "
                     f"recorded: a breaker this round's request trips "
                     f"fires when the request is recorded, and reaches the "
                     f"next envelope, not this one.")
    for b in fired:
        fp = f" `{b['fp']}`" if b.get("fp") else ""
        # The firing identity is what an authorization binds to, so the
        # report that asks for the decision prints it (round-4 F1).
        lines.append(f"- **{b['breaker']}** round {b['round']}{fp} "
                     f"[`{b.get('firing') or firing_id(b)}`]: "
                     f"{b['rule']} → {b['decision_required']}")
    lines.append("")
    if report.get("convergence"):
        lines.append(render_convergence_md(report["convergence"]))
    waived = report.get("waived") or {"count": 0, "commits": []}
    lines.append(f"Waived (deliberately unreviewed): **{waived['count']}**")
    for w in waived["commits"]:
        lines.append(f"- `{(w.get('sha') or '?')[:12]}` — {w.get('reason')} "
                     f"(authorized by {w.get('authorized_by')})")
    lines.append("")
    m = report["metrics"]
    lines.append(f"Rounds to clean: {m['rounds_to_clean']}")
    # Both inputs the round-4 findings said were declared but never reached a
    # report path. Printed even when empty or inert, because "the manifest was
    # supplied and matched nothing" and "no manifest was supplied" are the
    # distinction F8 and F5 both turned on.
    manifest = report.get("gate_manifest") or []
    lines.append(f"Gate manifest in force: "
                 f"{', '.join(manifest) if manifest else 'none declared'}")
    tokens = report.get("tokens") or {}
    if tokens:
        lines.append(f"Token budget: {tokens['state']} — {tokens['why']}")
    lines.append("")
    header = ["metric"] + [f"round {r}" for r in sorted(m["rounds"])]
    rows = []
    keys = [("finding IDs", "finding_ids"),
            ("atomic claims", "atomic_claims"),
            ("severity spread", "severity_spread"),
            ("dispositions", "dispositions"),
            ("refutation rate", "refutation_rate"),
            ("deferral rate", "deferral_rate"),
            ("deterministic-preventable", "deterministic_preventable"),
            ("accepted verification", "unverified_acceptance"),
            ("envelope bytes", "envelope_bytes"),
            ("tokens/round", "tokens")]
    for label, key in keys:
        row = [label]
        for r in sorted(m["rounds"]):
            val = m["rounds"][r][key]
            if key == "deterministic_preventable":
                gates = val.get("gates")
                if gates is None:
                    val = (f"{val['share']}; {val['candidates']} candidate "
                           f"label(s): {'; '.join(val['candidate_gates']) or 'none'}")
                else:
                    val = f"{val['share']} — {'; '.join(gates) or 'none'}"
            elif isinstance(val, dict):
                val = ", ".join(f"{k}: {v}" for k, v in val.items()) or "—"
            row.append(str(val))
        rows.append(row)
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    lin = report.get("lineage")
    if lin:
        lines.append(f"Lineage: {lin['number']} ({lin['events']} events in "
                     f"this lineage; {len(lin['closed_before'])} closed "
                     f"before it)")
    lines.append(f"Events: {report['events']} · Ledger: `{report['ledger']}`")
    return "\n".join(lines)

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
from .fingerprint import resolve_identity

LEDGER_BASENAME = "ledger.jsonl"


def _uid(event: dict) -> str:
    src = {k: v for k, v in event.items() if k not in ("uid", "ts")}
    blob = json.dumps(src, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


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

    def findings_in_round(self, r: int) -> list[dict]:
        """Findings ruled in round r: verdict findings plus atomic imports."""
        out = [e for e in self._by("finding") if e.get("round") == r]
        out += [e for e in self._by("import")
                if e.get("round") == r and e.get("kind") == "atomic"]
        return out

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
        dispositions = self._by("disposition")
        evidence = self._by("evidence")
        runs = self._by("falsification_run")
        all_findings = self._by("finding")

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
                        "rule": "fingerprint returned after `accepted` and a "
                                "passing falsification test",
                        "decision_required": "check the reviewer's SHA binding",
                    })

            round_dispositions = [d for d in dispositions
                                  if d.get("round") == r]
            round_runs = [x for x in runs if x.get("round") == r]
            if (r > min(rounds, default=r)
                    and not new_fps and not round_dispositions
                    and not new_digests and not round_runs):
                fired.append({
                    "breaker": "no-progress", "round": r,
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
                        "rule": "a blocking finding's falsification test "
                                "cannot be executed",
                        "decision_required": "a gate nobody can open is not a "
                                             "gate; decide its standing",
                    })

            digests_before |= {e["digest"] for e in round_evidence}

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
                "rule": f"round count {max(started)} > cap {round_cap}",
                "decision_required": "continue past the cap or settle by "
                                     "escalation",
            })
        return fired

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

    def _withdrawn_identities(self) -> set[str]:
        return {self.resolve(e["fp"]) for e in self._by("closure")
                if e.get("closure") == "withdrawn"}

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
        dispositions = self._by("disposition")
        verdicts = {e["round"]: e for e in self._by("verdict")}
        requests = {e["round"]: e for e in self._by("request")}

        for r in self.rounds():
            findings = self.findings_in_round(r)
            fps = {f["fp"] for f in findings}
            r_disp = [d for d in dispositions if self.resolve(d["fp"]) in
                      {self.resolve(fp) for fp in fps}]
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
            withdrawn = self._withdrawn_identities()
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
            # `refuted` reported unresolved disagreements as false positives.
            refuted_withdrawn = [
                d for d in r_disp
                if d["disposition"] == "refuted"
                and self.resolve(d["fp"]) in withdrawn
                and str(d.get("payload", {}).get("evidence", "")).strip()]
            accepted = [d for d in r_disp if d["disposition"] == "accepted"]
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
            runs_by = {(self.resolve(x["fp"]), x.get("round")): x
                       for x in self._by("falsification_run")}
            ver_kinds = Counter(self._acceptance_state(
                runs_by.get((self.resolve(d["fp"]), d.get("round"))),
                named.get(self.resolve(d["fp"]), False)) for d in accepted)
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
        if status == "pass":
            return "test passed, mutation not run"
        # `fail` and `passes_without_fix` never validate into an acceptance;
        # if one is in the ledger anyway (a hand-added event), say what it is
        # rather than bucket it as something it is not.
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


def render_report_md(report: dict) -> str:
    """Markdown rendering of report(); every number comes from the dict."""
    lines = ["## Ledger report — breakers and metrics (§5.3–5.4)", ""]
    fired = report["breakers_fired"]
    lines.append(f"Breakers fired: **{len(fired)}** "
                 f"(round cap {report['round_cap']})")
    for b in fired:
        fp = f" `{b['fp']}`" if b.get("fp") else ""
        lines.append(f"- **{b['breaker']}** round {b['round']}{fp}: "
                     f"{b['rule']} → {b['decision_required']}")
    lines.append("")
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

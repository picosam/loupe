"""CLI (design §9bis.3): exit codes mean exactly one thing — 0 ok, 1 findings,
2 usage. Every non-zero exit declares its recovery, never a diagnosis:
`next_kind: command` carries the next command in `next`; `next_kind: blocked`
carries `next: null` and a `remedy` a person must act on (`_blocked` below).
Structured output when stdout is not a TTY; the agent never parses prose.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import (FORMER_NAMES, TOOL_NAME, TOOL_VERSION, adapters, brief, config,
               emit, paths, transport, vocab, wire)
from .digest import sha256_file, sha256_text
from .ledger import (AmbiguousLineage, Ledger, known_branch,
                     render_convergence_md, render_cross_lineage_md,
                     render_report_md)
from .validate import (Item, accepted_tags, errors_in, scope_items,
                       validate_authorization,
                       validate_disposition, validate_request,
                       validate_verdict)

EXIT_OK, EXIT_FINDINGS, EXIT_USAGE = 0, 1, 2


def _tty() -> bool:
    return sys.stdout.isatty()


# The payload keys an agent RUNS rather than reads — the closed set, checked
# at the one door every structured result leaves by (workshop (b)).
#
# The policy used to be enforced by whichever caller remembered it:
# `_blocked` called `paths.executable`, `_finish` did not and wrote any
# truthy `next_cmd` as `next_kind: command`, the success side of `respond`
# and `close --lineage` reached `_out` through neither, and
# `transport._runnable` held a hand-maintained field list per call site. Six
# doors for one policy is five chances to add a seventh. Every current caller
# passed a rendered command, so nothing was unsafe — the defect was that
# nothing MADE them, and the next caller inherits no such habit.
#
# `test_cli_exits.py` asserts that `print` appears in exactly `_out` and
# three exit-0 artifact writers, so this door is the structured channel's
# only exit. What it does not reach is stated where those surfaces are
# enforced instead: envelope stamp lines (`wire.executable_stamp`), fenced
# relay lines (`brief._fence`), and the adapters' command slots.
RUNNABLE_KEYS = ("next", "reviewer_next", "diff")

# Every top-level key a structured result may carry that an agent READS
# rather than runs. Together with RUNNABLE_KEYS this is THE result schema,
# and it lives at the egress that enforces it (R1-F3): a key is classified
# runnable or prose at the moment it is introduced, or `_out` refuses the
# payload — however the dict was built. The old closure was an AST scan
# over three modules' dict literals; it missed subscript assignments,
# updates, merges, helper returns and any fourth module, so eight keys the
# egress already emitted were invisible to it. A lexical inventory cannot
# close a set that arbitrary Python constructs; the egress can, because
# every payload passes through it at runtime. The scan survives in
# `test_command_boundary` as advisory drift evidence only.
PROSE_KEYS = (
    "absent", "agreement", "anchor_path", "answers_escalation", "at_round",
    "attrs", "author",
    "author_next",
    # The authority's answer to a finding (2026-09-01): who decided, where
    # the work went if it went anywhere, what brings it back, and the set of
    # findings one authorization advances over. All read, never run — the
    # commands that produce them are a human's.
    "authorized_by", "base", "by", "destination", "trigger", "waived",
    # Round 2 F1: the emission stamp binding a disposition row to its
    # companion events; an identity the agent reads, never a command.
    "batch",
    "blocking", "breaker", "brief",
    # The emitting worktree's branch, recorded on the request event
    # (2026-09-05, brief `concurrent-round-refusal`): the axis the
    # concurrency refusals compare on. Read, never run — a person checks it
    # with git, and the decision it asks for is theirs.
    "branch",
    "bytes",
    "bytes_freed", "cached",
    # The git ref carrier (2026-09-03): where a `git` round's envelope was
    # put — the ref, the remote it was pushed to, and the leg it carries.
    # Read, never run: a person checks it with git if they want to, and the
    # command the round hands over goes through the runnable door like every
    # other one.
    "carrier", "ref", "remote",
    "checked", "cites", "claim_digest",
    "classification", "classification_notes", "classifications", "closures",
    "config", "convergence", "covers", "data", "declared_transport",
    "default_cap", "digest",
    # Round 3 F2's correction event (`ledger correct-actor`): the event
    # uids it names, the identity it says actually acted, and who is
    # asserting the correction. Read, never run — the correction itself is
    # the recorded decision; nothing downstream executes it.
    "corrects", "corrected_by", "true_actor",
    # What this repository never declared, and what the tool applied
    # instead (2026-09-03, brief `config-absent-asks-once`). Prose in the
    # strictest sense: each entry carries the exact TOML LINE a person
    # writes into review.toml, which is a thing to read and copy, never a
    # command to run.
    "decide",
    "disposition", "dispositions", "dry_run",
    "entry", "envelope", "error", "event", "events_added", "events_total",
    "exit", "falsification", "fetch", "files", "finding_id", "finding_ids",
    "findings", "findings_per_round", "fp", "from", "gate_output", "head",
    "hunted_anchors", "id",
    "ignored_control_fields", "install", "installed", "items", "kept",
    "kept_unrecognised", "kind", "ledger", "ledgers", "limits", "lineage",
    "lineage_closed_at_round", "moved", "mutation", "next_kind",
    "new_per_round",
    # The counterpart `new_per_round` needs to be read at all (brief
    # `convergence-blind-to-reclassification`): per round, how many
    # identities were residues of a finding the reviewer reclassified rather
    # than new claims. A count a person weighs; it runs nothing.
    "reclassified_per_round",
    "next_lineage", "note", "of", "ok", "open_request",
    "out", "outcome",
    # Keyed lineage (2026-09-06). `open_lineages` names every OTHER review
    # live in this repository — id, branch, round, state — and
    # `cross_lineage` names each standing finding whose identity is also
    # ruled in one of them. Both are FACTS a person weighs; neither answers
    # anything, changes any record, or carries a command.
    "open_lineages", "cross_lineage",
    # The lineage reservation's holder record (2026-09-06, lineage 27 round
    # 1 F1): the process that holds admission — its branch, its pid, the
    # verb it is running and when it took the reservation. Written into a
    # file beside the ledger and rendered into the refusal a second
    # worktree is given, all four read and never run: nothing here is a
    # command, and the recovery is to wait for that process to finish.
    "pid", "verb", "ts",
    "path", "payload", "permitted_authors", "permitted_reviewers",
    "preventable_by", "pruned", "reason", "recorded", "referenced_shas",
    "reader", "reading", "references", "rejected_reviewers", "relay",
    # `take`'s default rendering of the request (2026-09-18, brief
    # `loupe-tool-feedback-pilot-2026-09`): the envelope with its
    # attestation objects as a table. Read, never run, and never named
    # `envelope` — that key means the exact bytes.
    "request_view",
    # A member of `take`'s `head` record (the reviewer's checkout: state,
    # sha, tree). Nested, so the egress door never sees it; declared for
    # the advisory scan, which reads dict literals and cannot tell depth.
    "tree",
    "remedy", "repeated",
    "reviewer", "roles", "round", "round_cap", "severities", "severity",
    "rounds", "sha", "source",
    # Round-9 F2 / round-10 F2: how many legacy source PATHS the import read
    # for itself, the anchor commit it read them at, and the remote-tracking
    # refs that contain that anchor. Read, never run — together they are the
    # provenance claim a person is being asked to trust, and the refs are the
    # half that says the bytes were already shared rather than just present.
    "source_artifacts", "source_commit", "source_refs", "source_witness",
    "source_digest", "stalled_threads", "state",
    # Round-9 F4 (2026-09-03): the claim's authored scope against the
    # machine-computed changed-path span, as notice items. Prose an agent
    # relays to the author — never a command, and never an exit: the
    # comparison reports and does not refuse.
    "scope",
    "status", "subtype", "superseded",
    "tag", "target", "taxonomy", "test_digest", "then", "threads", "title",
    "to",
    # The debug round's recorded critique (2026-08-31): prose about the
    # tool the agent reads, never a command.
    "text",
    "token_budget", "tokens",
    # brief ci-attested-gates (2026-09-07): the gate row's attester, the run
    # it names, and the limit the poll runs under.
    "attested_by", "ci_run", "ci_timeout",
    # Lineage 7 round 1: which INSTALLATION wrote the envelope and which is
    # reading it, with the verdict on whether they agree. Read, never run —
    # and deliberately not a command, because the tool takes no position on
    # which of two differing installations should win.
    "tool", "tool_agreement", "tool_writer",
    # RVW-T18: the install SHAPE either end carries — the launchers, which
    # a wheel install does not resolve at all. Read, never run, and never
    # compared: two installs of identical code differ here by construction,
    # so these are reported beside the behavioural verdict rather than
    # folded into it.
    "reader_shape", "writer_shape",
    # RVW-T11: an enum the agent READS. It decides which command the tool
    # renders, and is never itself a command — the relay it selects goes
    # through the same runnable door as every other executable field.
    "transport", "unanswered", "verdict", "verdict_sha", "withdrawn_any",
    "wrapper", "writer", "written",
)

_CLASSIFIED_KEYS = frozenset(RUNNABLE_KEYS) | frozenset(PROSE_KEYS)


def _out(payload: dict, tty_text: str | None = None):
    # The schema door: refuse an unclassified top-level key BEFORE anything
    # is printed, whatever construction route built the dict. This is what
    # makes the inventory closed — not a scan's opinion of the source.
    unclassified = sorted(k for k in payload if k not in _CLASSIFIED_KEYS)
    if unclassified:
        raise TypeError(
            f"unclassified structured-result key(s) {unclassified}: every "
            f"top-level key is declared RUNNABLE_KEYS (an agent runs it) or "
            f"PROSE_KEYS (an agent reads it) at the egress, when the key is "
            f"introduced — a result field is a decision about execution, "
            f"never a side effect of building a dict")
    for key in RUNNABLE_KEYS:
        if payload.get(key) is not None:
            paths.executable(payload[key], f"the `{key}` field")
    if _tty() and tty_text is not None:
        print(tty_text)
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))


def _finish(items, next_cmd: str, brief_text: str | None = None,
            remedy: str = "", relay_text: str | None = None,
            agreement: dict | None = None) -> int:
    """The items-list exit, typed like every other one.

    Round 2 F8 typed this branch `command` "by construction, not by
    assumption", and the construction was wrong: four of the six callers
    passed sentences — `fix the listed items in <file>, then re-run …` —
    straight into the field the adapters tell an agent to execute. Round 3 F6
    caught it, which makes it the second time this defect shipped inside its
    own fix.

    The rule is now the rule everywhere: `next_cmd` empty means blocked, and
    `remedy` says what a person must do. A validation failure usually IS
    blocked — nothing the tool can run repairs an envelope someone has to
    edit — and saying so beats naming a command that re-reports the failure.
    """
    payload = {"items": [i.as_dict() for i in items],
               "ok": not errors_in(items)}
    lines = [f"{i.level}: [{i.code}] {i.message}" for i in items]
    # Round-2 F2: a verb that reads a STAMPED envelope reports whose
    # installation wrote it, in both output modes and whether or not the
    # envelope validates — a defective envelope from another installation
    # is exactly when knowing that matters.
    if agreement is not None:
        payload["tool"] = agreement
        lines.append(render_tool_agreement(agreement).rstrip("\n"))
    if brief_text:
        payload["brief"] = brief_text
    # The account and the commands stay separate fields all the way out, so
    # no caller has to divide one blob by guesswork (round 3 relay split).
    if relay_text:
        payload["relay"] = relay_text
    if errors_in(items):
        kind = "command" if next_cmd else "blocked"
        payload["next"] = next_cmd or None
        payload["next_kind"] = kind
        if kind == "blocked":
            payload["remedy"] = remedy or "a person must correct the items above"
            lines.append(f"blocked: {payload['remedy']}")
        else:
            lines.append(f"next: {next_cmd}")
        _out(payload, "\n".join(lines))
        return EXIT_FINDINGS
    if brief_text:
        lines.append("")
        lines.append(brief_text)
    if relay_text:
        lines.append("")
        lines.append(relay_text)
    _out(payload, "\n".join(lines) if lines else "ok")
    return EXIT_OK


# The fields a non-zero exit owns (§9bis.3 rules 3 and 4). One authority:
# `_blocked` derives every one of them, and auxiliary payload data may not
# supply, replace or contradict any (round-10 F2).
CONTROL_FIELDS = ("ok", "exit", "error", "next", "next_kind", "remedy")


def _blocked(next_cmd: str, why: str, code: int = EXIT_FINDINGS,
             remedy: str = "", extra: dict | None = None,
             detail: str = "", lead: str = "") -> int:
    """Every non-zero exit that is not an items list, in one place.

    Round-3 F17 required the next command on every non-zero exit; round-4 F9
    found the branches that printed it as prose instead, so `json.loads` on a
    real exit-1 path raised. `why` is carried because a machine reader may
    want it, but `next` is the contract: the agent acts on the command, never
    on the diagnosis (§9bis.3 rules 3 and 4).

    Round 2 F8: making the output structured left the field an agent executes
    holding sentences — `fix <path>, then re-run`. A truthy `next` was never
    the property worth asserting; a RUNNABLE one was. `next_kind` states which
    of the two recoveries this exit has, so the adapter rule can branch on a
    field instead of on the shape of a string:

      command — `next` is a literal argv an agent runs verbatim.
      blocked — `next` is null and `remedy` says what a PERSON must do.

    Blocked is a real state, not a gap to be papered over with an invented
    command: malformed TOML, a merge only a human can arbitrate, an envelope
    that must be authored. Naming it is what lets an agent stop and relay
    rather than improvise. Passing no `next_cmd` selects it.

    Round 5 F1: `next` is the field the adapters tell agents to run
    verbatim, so it takes a rendered `paths.Command` or nothing at all.
    Three rounds tried to prove commands safe by reading the source that
    built them; this door checks the type instead, and a string built any
    other way cannot pass it by being unanalysable.
    """
    next_cmd = paths.executable(next_cmd, "the `next` field")
    kind = "command" if next_cmd else "blocked"
    # Round-9 F2: a non-zero exit does not mean the command was
    # side-effect-free, and an operator recovering from a partial run needs
    # what it DID do, not only what stopped it. `extra` carries that state
    # into the structured result; `detail` renders it for the human.
    #
    # Round-10 F2: it may ADD to a refusal and never redefine one. The first
    # version was an unrestricted `payload.update(extra or {})`, which handed
    # every caller authority over the fields this function exists to own — a
    # refusal could report `ok: true`, exit 0, or a `next` command nothing
    # selected, which is precisely what an agent acts on. The control fields
    # are stripped from the auxiliary data and then written last, so neither
    # a mistake nor a later caller can forge them, and a collision is named
    # in the result rather than dropped in silence.
    payload = dict(extra or {})
    forged = sorted(k for k in payload if k in CONTROL_FIELDS)
    for key in forged:
        payload.pop(key)
    if forged:
        payload["ignored_control_fields"] = forged
    payload.update({"ok": False, "exit": code, "error": why,
                    "next": next_cmd or None, "next_kind": kind})
    tail = f"\n{detail}" if detail else ""
    # Round 1 F3: `detail` renders AFTER the recovery line, which is right
    # for "what the run did before it stopped" and wrong for anything the
    # recovery line refers to. A remedy reading "relay the items above" over
    # a TTY that printed no items sends the reader back to the recomputation
    # this lineage forbids. `lead` is the region the recovery may point at.
    head = f"\n{lead}" if lead else ""
    if kind == "blocked":
        payload["remedy"] = remedy or why
        _out(payload, f"{why}{head}\nblocked: {payload['remedy']}{tail}")
    else:
        _out(payload, f"{why}{head}\nnext: {next_cmd}{tail}")
    return code


def _detect_and_parse(text: str):
    kind = wire.detect_kind(text)
    if kind == "request":
        return kind, wire.parse_request(text)
    if kind == "verdict":
        return kind, wire.parse_verdict(text)
    if kind == "disposition":
        return kind, wire.parse_disposition(text)
    if kind == vocab.AUTHORIZATION_KIND:
        return kind, wire.parse_authorization(text)
    return "unknown", None


def cmd_validate(args, cfg) -> int:
    # `-` reads stdin like `take` and `close` do (round 3 relay fix): a
    # refused `close --verdict -` names `validate -` as its next command,
    # and a named next command must run as printed.
    text = _read_envelope(args.envelope)
    kind, parsed = _detect_and_parse(text)
    if kind == "unknown":
        return _blocked(
            "",
            f"{args.envelope} carries no recognizable envelope wrapper",
            remedy=f"whoever authored {args.envelope} must wrap it in a "
                   f"<{cfg.wrapper_tag}-review-"
                   f"{{request|verdict|disposition|authorization}}> tag; "
                   f"envelopes come from `handoff`, `respond`, "
                   f"`authorize-advance` and the reviewer, never from "
                   f"a command that rewrites this file")
    # Round-2 F2: `validate` accepts every envelope kind from anywhere —
    # it is the verb a refusal names as its next command — so it is a
    # cross-installation reader of both stamped kinds and says so.
    agreement = (transport.tool_agreement(parsed)
                 if kind in vocab.STAMPED_KINDS else None)
    # Sweep F6's authority, made reachable from the verb `take` names. The
    # declaration is the flag; the SHA is DERIVED from the envelope's own
    # stamp, so there is no second value to get wrong and no way to judge an
    # envelope about one commit under another commit's rules.
    governing, authority = cfg, []
    if args.from_target:
        sha = getattr(parsed, "sha", "") or ""
        if not sha:
            return _blocked(
                "", f"--from-target needs the envelope to stamp the commit "
                    f"it is about, and this {kind} stamps none",
                remedy="a person drops the declaration, or supplies an "
                       "envelope that names its target; the tool will not "
                       "guess which commit's rules apply")
        try:
            governing = transport.governing_for(cfg, sha)
        except transport.Refusal as exc:
            return _blocked(exc.next_cmd, str(exc), remedy=exc.remedy)
        authority = [Item("notice", "T-AUTHORITY",
                          f"judged against {governing.source}")]
    _lineage_read = _ledger(cfg, args)
    try:
        # Round-3 F2: validate is itself a cross-installation reader of both
        # stamped kinds (it says so, four lines up), and it was resolving by
        # this checkout's branch while holding an envelope that names its
        # own review. The same precedence helper every other envelope-bearing
        # verb uses decides it here too. Its SOURCE leg is always empty in
        # practice — `_read_envelope(args.envelope)` is called without `cfg`,
        # so a `git:<lineage>/<round>` reference is refused before this line
        # is reached, because a reference names a ROUND and the leg belongs
        # to the verb, which this one does not have: it admits every kind.
        # That is the admitted source domain, and the claim's contract is
        # narrowed to it rather than left standing as a universal.
        judged_lineage = _read_lineage(_lineage_read, cfg, "validate",
                                       getattr(parsed, "sha", None),
                                       carried=_carried_by(args.envelope,
                                                           text))
    except LineageUnchoosable as exc:
        return _blocked("", str(exc), remedy=exc.remedy)
    if kind == "request":
        items = validate_request(
            parsed, governing,
            round_cap=_lineage_read.effective_round_cap(
                governing.round_cap, judged_lineage))
    elif kind == "verdict":
        items = validate_verdict(
            parsed, governing,
            answering=_dispositions_answered(_lineage_read, parsed,
                                             judged_lineage))
    elif kind == vocab.AUTHORIZATION_KIND:
        items = validate_authorization(parsed, governing)
    else:
        against = None
        if args.against:
            against = wire.parse_verdict(_read_envelope(args.against))
        items = validate_disposition(parsed, governing, against)
        if against is None:
            items.append(Item("notice", "D-STANDALONE",
                              "completeness not checked: pass --against "
                              "<verdict> to enforce it"))
    items = authority + items
    # A validated verdict is the moment the reviewer hands it back, so it is
    # where the human first meets the ruling. Emitting the précis here means
    # they get the account without anyone remembering to ask for it — and the
    # relay beside it, separately, so the reviewer has no reason to invent a
    # split of its own (round 3 relay split).
    #
    # Round 4 F2: the relay is withheld until validation SUCCEEDS. It is
    # derived from the unvalidated verdict line, so a wrapped envelope
    # claiming `clean to advance` while carrying a blocking finding produced
    # one result that said `blocked, no runnable recovery` to the agent and
    # "Nothing is blocking — run close" to the human, about a verdict that is
    # neither clean nor valid. `close` would have rejected it, so nothing was
    # bypassed; but a contradiction printed at the malformed-envelope
    # boundary is exactly where the typed-recovery contract exists to stop an
    # agent improvising. The précis still goes out either way: describing a
    # broken envelope is how its author finds out what is broken.
    is_verdict = kind == "verdict" and parsed.wrapped
    precis = (brief.verdict_precis(parsed, source=args.envelope)
              if is_verdict else None)
    # RVW-T11: the topology comes from this machine's own record of the
    # round, never from the verdict document — see the note on the relay
    # below, which this now also decides.
    ledger = _lineage_read
    carrier = (transport.recorded_transport(ledger, parsed.sha,
                                            judged_lineage)
               if is_verdict else vocab.TRANSPORT_DEFAULT)
    round_no = (ledger.round_for_sha(parsed.sha, judged_lineage)
                if is_verdict else None)
    # F1: the round's RECORDED provenance decides the lineage this verdict
    # rides on. `carrier_lineage_for_sha` reads back the id the round was
    # carried under — the same id on both machines since the lineage was
    # keyed, and the pre-keying `take` stamp for a round taken over a
    # reference before it. None means this ledger never saw the round, and
    # the lineage this verb resolved is the honest answer.
    verdict_lineage = (ledger.carrier_lineage_for_sha(parsed.sha,
                                                      prefer=judged_lineage)
                       if is_verdict else None)
    if verdict_lineage is None:
        verdict_lineage = judged_lineage
    reference = _round_reference(carrier, round_no, verdict_lineage)
    # The verdict leg of a `git` round is pushed HERE, by the reviewer, and
    # only here: `validate --from-target` is the reviewer's last step and
    # the moment the ruling is established, so it is the one place that can
    # put bytes on a ref without either end trusting an unvalidated
    # document. A verdict that fails validation is returned to its author
    # and nothing is published; the digest and SHA binding downstream are
    # unchanged, because the ref is a carrier and never an authority.
    if (is_verdict and args.from_target and reference
            and not errors_in(items)):
        try:
            transport.push_envelope(cfg, verdict_lineage, round_no,
                                    "verdict", text)
        except transport.Refusal as exc:
            return _blocked(exc.next_cmd, str(exc), remedy=exc.remedy)
    # RVW-T11. The verdict document cannot be trusted to carry the topology —
    # its author is an agent writing prose, and a field it mis-transcribes
    # would decide what the OTHER side is told to run. The value comes from
    # this machine's own record of the round instead: the reviewer's `take`
    # wrote it, and `recorded_transport` reads it back for this SHA.
    # Round-3 F2: a validated envelope that no heredoc can carry
    # byte-identically (no terminal newline) refuses HERE, before any
    # relay is printed — a relay that silently changed the bytes would
    # record a different digest from the artifact just validated.
    try:
        verdict_next = (
            brief.verdict_relay(
                parsed, source=args.envelope, transport=carrier,
                reference=reference,
                # On a paste round the reviewer hands over ONE block: the
                # close command consuming the verdict bytes as its own stdin
                # (a quoted heredoc) — bytes outside a fence are bytes a chat
                # surface may rewrite (round 3, live).
                envelope=text)
            if is_verdict and not errors_in(items) else None)
    except brief.UnrelayableEnvelope as exc:
        return _blocked("", f"{args.envelope}: {exc}",
                        remedy=f"a person appends the terminal newline to "
                               f"{paths.display_path(args.envelope)} and "
                               f"re-runs this command")
    return _finish(items, "", brief_text=precis, relay_text=verdict_next,
                   agreement=agreement,
                   remedy=f"whoever authored {args.envelope} must correct the "
                          f"items above and re-run "
                          f"`{paths.command(*paths.lits(TOOL_NAME, 'validate'), args.envelope)}`; envelopes "
                          f"are edited by their author, not by this tool")


def cmd_fingerprint(args, cfg) -> int:
    text = _read_envelope(args.verdict)
    v = wire.parse_verdict(text)
    rows = [{"id": f.id, "fp": f.fingerprint(), "severity": f.severity,
             "classification": f.classification, "anchor_path": f.anchor_path,
             "title": f.title} for f in v.findings]
    _out({"sha": v.sha, "findings": rows},
         "\n".join(f"{r['fp']}  {r['id']:<4} {r['title']}" for r in rows))
    return EXIT_OK


def cmd_respond(args, cfg) -> int:
    # Round 4 F2, the half this verb owns: `close` has always accepted `-`
    # and read the verdict from stdin, and this verb did not — so the relay
    # printed a two-command sequence whose first half worked off a paste and
    # whose second half looked for a file literally named `-`. An author on
    # another machine could carry the verdict in and then not answer it.
    # Both halves read the same way now.
    if args.verdict == "-" and args.from_json == "-":
        return _blocked(
            "",
            "both --verdict and --from-json were given as `-`, and one "
            "stdin cannot carry two documents",
            remedy="a person passes one of them as a path: the verdict the "
                   "reviewer delivered, or the dispositions they wrote")
    verdict_text = _read_envelope(args.verdict, cfg, "verdict")
    verdict = wire.parse_verdict(verdict_text)
    ledger = _ledger(cfg, args)
    # Which review this response belongs to: the lineage the round the
    # verdict rules was recorded in, resolved through the request that
    # carries its SHA (brief `keyed-lineage`, item 3).
    try:
        lineage = _read_lineage(ledger, cfg, "respond", verdict.sha,
                                carried=_carried_by(args.verdict, None))
    except LineageUnchoosable as exc:
        return _blocked("", str(exc), remedy=exc.remedy)
    # Sweep F2: a disposition is an answer to a VALID, RECORDED verdict, not
    # a free-standing assertion. This verb used to parse whatever file it
    # was handed and go straight to building the response — an unwrapped
    # legacy verdict that fails validation, never recorded, produced
    # disposition events at a caller-supplied SHA with exit 0. The verdict
    # is validated first, in every mode.
    items = validate_verdict(
        verdict, cfg,
        answering=_dispositions_answered(ledger, verdict, lineage))
    if errors_in(items):
        return _finish(items,
                       paths.command(*paths.lits(TOOL_NAME, "validate"),
                                     args.verdict),
                       remedy=f"{paths.display_path(args.verdict)} is not "
                              f"a valid verdict, so nothing can answer it; "
                              f"the reviewer must correct and re-issue it")
    # The recorded verdict this response answers, resolved through the same
    # boundary `ledger add` uses for a standalone disposition. Round 4 F1:
    # this used to be resolved only `if args.out`, so the shared author
    # check below never ran for the door that renders and prints instead of
    # writing — resolved unconditionally now. With --out — the mode that
    # writes bytes of record and appends to the ledger — round and SHA are
    # then DERIVED from the record, and the verdict must resolve to exactly
    # one; a value supplied in the JSON may only agree. Without --out the
    # envelope is rendered and printed, not recorded, so a verdict that does
    # not (yet) resolve is not itself refused here — only a resolving one
    # whose author disagrees is, below.
    recorded = transport.recorded_verdict(
        ledger, lineage, digest=sha256_text(verdict_text))
    if args.out:
        if recorded is None:
            close_cmd = paths.command(
                *paths.lits(TOOL_NAME, "close", "--verdict"), args.verdict)
            report_cmd = paths.command(
                *paths.lits(TOOL_NAME, "ledger", "report"))
            return _blocked(
                "",
                f"{args.verdict} does not resolve to exactly one recorded "
                f"verdict of the current lineage's just-closed round: it is "
                f"either not recorded (`{paths.command(*paths.lits(TOOL_NAME, 'close', '--verdict'))}` first), "
                f"recorded twice, from a closed lineage, or superseded by a "
                f"later round — a response answers the ruling that is "
                f"awaiting one, and nothing else is recorded",
                remedy=f"record the verdict with `{close_cmd}` if it "
                       f"has not been, then re-run this command; if it has, "
                       f"`{report_cmd}` shows which round is open")
    # `-` reads the dispositions from stdin, so an agent can pipe them and a
    # read-only surface can exercise the command without writing a file.
    raw = (sys.stdin.read() if args.from_json == "-"
           else Path(args.from_json).read_text(encoding="utf-8"))
    try:
        # The author's judgment file goes through the same duplicate-
        # refusing boundary as every envelope body (lineage-3 round 4 F1):
        # two `disposition` members for one finding is not a choice the
        # tool makes on the author's behalf.
        data = wire.load_json(raw)
    except wire.DuplicateMember as exc:
        return _blocked(
            "",
            f"{args.from_json} states JSON member {exc.name!r} more than "
            f"once; every member is single-valued, and the tool will not "
            f"choose between two declarations",
            remedy=f"a person removes the repeated member from "
                   f"{args.from_json} and re-runs this command")
    if args.out and recorded is not None:
        for key, derived in (("round", recorded["round"]),
                             ("verdict_sha", recorded["sha"])):
            supplied = data.get(key)
            if supplied is not None and str(supplied) != str(derived):
                return _blocked(
                    "",
                    f"supplied {key} {supplied!r} does not match the recorded "
                    f"verdict's {derived!r} — the round and SHA a response "
                    f"answers are derived from the record, never supplied "
                    f"beside it",
                    remedy=f"remove {key!r} from {args.from_json} and re-run "
                           f"this command; the tool will not edit the "
                           f"author's own input file")
        data["round"] = recorded["round"]
        data["verdict_sha"] = recorded["sha"]
    if recorded is not None:
        # Round 3 F2: the disposition author is the identity that accepted
        # or rejected the verdict's findings, which is the identity the
        # RECORDED REQUEST already named as this round's author — never a
        # value the disposition JSON supplies beside it, which a reviewer
        # answering its own verdict could stamp as itself. Round 4 F1: this
        # used to run only inside `if args.out:` (`recorded` was `None`
        # otherwise), so the door that renders and prints instead of
        # writing carried the wire author unverified — the same check now
        # runs whenever the verdict resolves at all, through the ONE
        # function every disposition-ingress door shares
        # (`transport.check_disposition_author`; `ledger add` calls it too).
        # Absent a matching request event (an older ledger, or a round that
        # predates the field), there is nothing recorded to compare against
        # and the tool falls back to the configured author, exactly as
        # before this fix.
        try:
            transport.check_disposition_author(
                ledger, recorded["round"], data.get("author"), lineage)
        except transport.Refusal as exc:
            return _blocked(exc.next_cmd, str(exc), remedy=exc.remedy)
        expected_author = transport.request_author(ledger, recorded["round"],
                                                   lineage)
        if expected_author is not None:
            data["author"] = expected_author
    by_id = {f.id: f for f in verdict.findings}
    records = []
    for rec in data["dispositions"]:
        rec = dict(rec)
        f = by_id.get(rec.get("finding_id"))
        if f is not None:
            # Round 1 F3: this was `setdefault`, so an author-supplied value
            # won and the ledger recorded an identity the verdict never
            # computed. Derivation is unconditional now; a supplied value may
            # only agree, and disagreement refuses rather than being quietly
            # overwritten — the author should learn their file is wrong.
            for key, derived in (("fingerprint", f.fingerprint()),
                                 ("severity", f.severity)):
                supplied = rec.get(key)
                if supplied is not None and str(supplied) != str(derived):
                    return _blocked(
                        "",
                        f"{rec.get('finding_id')}: supplied {key} "
                        f"{supplied!r} does not match the verdict's "
                        f"{derived!r} — identity is computed from the finding "
                        f"being answered, never supplied beside it "
                        f"(round 1 F3)",
                        remedy=f"remove {key!r} for {rec.get('finding_id')} "
                               f"from {args.from_json} and re-run this "
                               f"command; the tool will not edit the author's "
                               f"own input file")
                rec[key] = derived
        records.append(rec)
    envelope = wire.emit_disposition(
        tag=cfg.wrapper_tag,
        verdict_sha=data.get("verdict_sha") or verdict.sha or "",
        head=data["head"], author=data.get("author", cfg.roles["author"]),
        round_no=int(data.get("round", 0)), dispositions=records)
    items = validate_disposition(wire.parse_disposition(envelope), cfg,
                                 against=verdict)
    if errors_in(items):
        respond_cmd = paths.command(
            *paths.lits(TOOL_NAME, "respond", "--verdict"), args.verdict,
            paths.Lit("--from-json"), args.from_json)
        return _finish(items, "",
                       remedy=f"a person must correct the dispositions in "
                              f"{paths.display_path(args.from_json)} and "
                              f"re-run `{respond_cmd}`; the tool will not "
                              f"edit the author's own judgment file")
    if args.out:
        Path(args.out).write_text(envelope, encoding="utf-8")
        # RVW-T9: a written disposition is the author's half of the round;
        # record and keep it here so the loop needs no manual `ledger add`.
        rec = transport.record_response(cfg, ledger, envelope,
                                        against=verdict, lineage=lineage)
        _out({"ok": True, "out": args.out, "dispositions": len(records),
              "kept": rec["kept"], "events_added": rec["events_added"],
              "next": paths.command(*paths.lits(TOOL_NAME, "handoff"))},
             f"wrote {args.out}; {rec['events_added']} disposition event(s) "
             f"recorded, kept at {paths.display_path(rec['kept'])}\n"
             f"next: {paths.command(*paths.lits(TOOL_NAME, 'handoff'))}")
    else:
        print(envelope, end="")
    return EXIT_OK


def _ledger(cfg, args) -> Ledger:
    return Ledger(cfg.ledger_dir)


def _envelope_sha(text: str | None) -> str | None:
    """The commit CAPTURED envelope bytes bind, or None.

    Round-2 F3 took the read out of here. It used to open the source
    itself, so a verb that resolved a lineage and then processed the
    document read the source TWICE — which consumed `-` outright (the paste
    relay's close command is a heredoc: the peek drained stdin and the real
    read got an empty document) and, on a file or a fetched ref, let
    selection and processing see different bytes. Its caller captures once
    and hands the bytes here; any failure to parse still answers None and
    the verb's own reader raises the refusal a person can act on.
    """
    if not text:
        return None
    for parse in (wire.parse_request, wire.parse_verdict):
        try:
            parsed = parse(text)
        except Exception:
            continue
        if parsed.sha:
            return parsed.sha
    return None


def _envelope_lineage(text: str | None) -> str | None:
    """The lineage id CAPTURED request bytes STAMP, or None (round-3 F1).

    Round-2 F5 put the author's lineage id on the request wrapper so the
    reviewer's `take` could tell a continuation from a new review. Round-3
    F1 found the other verbs still ignoring it: handed review B's request
    from checkout A, `brief` reported A and relayed A's round, and
    `ledger add` recorded B's bytes into A. A stamp is explicit provenance
    and outranks the branch the verb happens to run from; a `git:<id>/<n>`
    reference outranks both (the caller checks that first). A verdict
    carries no stamp and answers None here; so does anything that does not
    parse, whose own reader raises the refusal.
    """
    if not text:
        return None
    try:
        parsed = wire.parse_request(text)
    except Exception:
        return None
    try:
        return transport.stamped_lineage(parsed)
    except Exception:
        return None


def _carried_by(source: str | None, text: str | None) -> str | None:
    """The review identity an invocation CARRIES: the id of a
    `git:<lineage>/<round>` source, else the stamp on the captured bytes,
    else None — in which case the caller falls back to its branch."""
    reference = transport.parse_round_reference(source or "")
    if reference:
        return reference[0]
    return _envelope_lineage(text)


class LineageUnchoosable(Exception):
    """No lineage can be chosen for this verb, and none may be guessed.

    The one state brief `keyed-lineage` item 3 requires a refusal for: a
    detached HEAD or a branch git will not name, with several lineages open
    and no explicit reference to resolve one. Every other state is
    deterministic — the open lineage recorded on this branch, the single
    unbranched one, the id an envelope's SHA resolves to, or a new lineage.
    """

    def __init__(self, message: str, remedy: str):
        super().__init__(message)
        self.remedy = remedy


def _unchoosable(ledger: Ledger, verb: str, branch: str
                 ) -> LineageUnchoosable:
    named = ", ".join(
        f"`{l}` on {ledger.lineage_branch(l) or 'an unrecorded branch'} "
        f"(round(s) {', '.join(str(r) for r in ledger.rounds(l)) or 'none'})"
        for l in ledger.open_lineages())
    where = (f"branch {branch}" if branch
             else "a detached HEAD, which names no branch")
    return LineageUnchoosable(
        f"this worktree is on {where}, and "
        f"{len(ledger.open_lineages())} lineages are open in this "
        f"repository: {named}. `{verb}` acts on ONE lineage and there is "
        f"nothing here to choose it by — a branch is what binds a worktree "
        f"to its review",
        remedy="a person checks out the branch whose review this is, or "
               "names the round explicitly (`git:<lineage>/<round>`) where "
               "the verb reads an envelope. The tool picks between open "
               "reviews for nobody")


def _branch_lineage(ledger: Ledger, cfg, verb: str,
                    branch: str | None = None) -> str | None:
    """The open lineage this worktree's branch is on, or None.

    None is "this branch holds no open review", not a fault: `handoff` mints
    one, a READ falls back to the legacy positional successor, and a verb
    that RECORDS refuses. Each caller says what the absence means for it —
    this answers only what the ledger holds.
    """
    mine = transport.current_branch(cfg) if branch is None else branch
    lineage, state = ledger.choose_open_lineage(mine)
    if state == Ledger.LINEAGE_AMBIGUOUS:
        raise _unchoosable(ledger, verb, mine)
    return lineage


def _carried_lineage(ledger: Ledger, cfg, verb: str, branch=None):
    """(the review identity this INVOCATION carries, the refusal to raise
    if nothing else resolves one).

    Round-2 F1 made this its own step. A SHA names a commit, and two
    branches may review one commit under two reviews; what says which of
    them a verb is acting on is the branch it was invoked from — so the
    branch is resolved FIRST and handed to the SHA lookup as the binding to
    preserve, instead of being consulted only after the SHA has already
    picked by event order. An unchoosable branch is not fatal here: an
    envelope may still resolve unambiguously, and the refusal is carried
    forward to be raised only if it does not.
    """
    try:
        return _branch_lineage(ledger, cfg, verb, branch), None
    except LineageUnchoosable as exc:
        return None, exc


def _ambiguous(exc: AmbiguousLineage, verb: str) -> LineageUnchoosable:
    """`AmbiguousLineage` as the refusal a person acts on (round-2 F1)."""
    return LineageUnchoosable(
        f"{exc} — so `{verb}` cannot say which review this envelope "
        f"belongs to, and it records nothing rather than choosing by the "
        f"order the two reviews happened to be recorded in",
        remedy="a person runs this from the worktree whose review it is, "
               "so the branch names it, or hands the round over as "
               "`git:<lineage>/<round>` where the verb reads an envelope; "
               "the tool picks between reviews of one commit for nobody")


def _read_lineage(ledger: Ledger, cfg, verb: str, sha: str | None = None,
                  carried: str | None = None) -> str:
    """The lineage a READ acts on: the one an envelope's SHA resolves to
    WITHIN the review this invocation carries, else this branch's open one,
    else the LEGACY POSITIONAL SUCCESSOR — `str(closures + 1)`, the key the
    window after the last closure marker has always had.

    A read never mints. The fallback is what makes an unmigrated ledger
    report the numbers it always reported: on an empty ledger it is `"1"`,
    and on a ledger whose every lineage is closed it names the next one,
    which holds no events and reports as the empty lineage the old
    positional cursor did.

    It DOES refuse in one state, added by round-2 F1: a SHA recorded by
    more than one review, reached from a checkout that names none of them.
    A read that guessed there returned another review's retained bytes and
    another review's ref.
    """
    mine, unchoosable = ((carried, None) if carried
                         else _carried_lineage(ledger, cfg, verb))
    if sha:
        try:
            resolved = ledger.recorded_lineage_for_sha(sha, prefer=mine)
        except AmbiguousLineage as exc:
            raise _ambiguous(exc, verb) from exc
        if resolved is not None:
            return resolved
    if mine is not None:
        return mine
    if unchoosable is not None:
        raise unchoosable
    return str(len(ledger.all_closures()) + 1)


def _write_lineage(ledger: Ledger, cfg, verb: str, sha: str | None = None,
                   carried: str | None = None) -> str:
    """The lineage a verb that RECORDS acts on, resolved from the envelope
    it was given (its SHA, through the request that carries it) WITHIN the
    review this invocation carries, and otherwise from this worktree's
    branch.

    Refuses rather than guessing when neither answers, and — since round-2
    F1 — when the SHA answers with more than one review and nothing carried
    says which: recording into a lineage nobody chose is how a round dies
    in the wrong review, and a close that chose by event order appended its
    closure to another branch's lineage.
    """
    mine, unchoosable = ((carried, None) if carried
                         else _carried_lineage(ledger, cfg, verb))
    if sha:
        try:
            resolved = ledger.recorded_lineage_for_sha(sha, prefer=mine)
        except AmbiguousLineage as exc:
            raise _ambiguous(exc, verb) from exc
        if resolved is not None:
            return resolved
    if mine is not None:
        return mine
    if unchoosable is not None:
        raise unchoosable
    raise LineageUnchoosable(
        f"no open lineage in this repository holds "
        + (f"{sha[:12]}, and none is recorded on this worktree's branch"
           if sha else "this worktree's branch")
        + f", so `{verb}` has no review to record into",
        remedy=f"a person opens the round first — "
               f"`{paths.command(*paths.lits(TOOL_NAME, 'handoff', '--claim-file'), paths.Ph('<claim>'))}` "
               f"— or runs this from the worktree whose review it belongs to")


def _dispositions_answered(ledger: Ledger, verdict,
                           lineage: str) -> list[dict] | None:
    """The dispositions a verdict is answering, read back from the ledger.

    §5.2 requires a closure for every refutation and every
    accepted(test_amended); round-4 F2 required that requirement to be
    checked. The set is the dispositions of the round before this verdict's.
    Where the round cannot be identified the answer is None — "not checkable"
    — never an empty list, which would silently mean "nothing was required".
    """
    # Round 2 F1 separated two questions that shared one lookup. `close` asks
    # "which OPEN round does this verdict answer", and a ruled round is not
    # awaiting an answer. This asks "which round is this verdict FOR", which
    # stays true after the round closes — otherwise re-validating a recorded
    # verdict would report its closure requirements as unknowable.
    bound = ledger.rounds_for_sha(verdict.sha, lineage)
    if not bound:
        return None
    round_no = bound[-1]
    # The STANDING answer per finding — the newest event per fingerprint —
    # not raw event multiplicity: a disposition legitimately re-binds when
    # the head moves under it (lineage 6 round 2, with the ledger's
    # standing_dispositions as the one authority for what "answered" means).
    return ledger.standing_dispositions(lineage, round_no=round_no - 1)


def cmd_ledger_add(args, cfg) -> int:
    text = _read_envelope(args.envelope)
    kind, parsed = _detect_and_parse(text)
    ledger = _ledger(cfg, args)
    # The lineage this envelope belongs to: the one its SHA resolves to
    # through the request that carries it, else this branch's open one,
    # else the legacy positional successor. A READ resolution deliberately,
    # so a hand-built ledger with no branch recorded anywhere — which is
    # every fixture and every pre-0.20.0 ledger — lands where it always
    # did instead of minting a review nobody asked for.
    try:
        lineage = _read_lineage(
            ledger, cfg, "ledger add",
            getattr(parsed, "sha", None)
            or getattr(parsed, "attrs", {}).get("verdict_sha"),
            carried=_carried_by(args.envelope, text))
    except LineageUnchoosable as exc:
        return _blocked("", str(exc), remedy=exc.remedy)
    digest = sha256_file(Path(args.envelope))
    size = Path(args.envelope).stat().st_size
    added = 0
    agreement = None
    if kind == "verdict":
        items = validate_verdict(
            parsed, cfg,
            answering=_dispositions_answered(ledger, parsed, lineage))
        if errors_in(items):
            return _finish(items,
                           paths.command(*paths.lits(TOOL_NAME, "validate"),
                                         args.envelope))
        round_no = args.round or ledger.round_for_sha(parsed.sha, lineage)
        if round_no is None:
            return _blocked(
                "",
                f"no request in the ledger matches sha {parsed.sha}, so the "
                f"round cannot be derived",
                remedy=f"a person establishes which round {args.envelope} "
                       f"belongs to and re-runs this command with `--round "
                       f"<that number>`; the tool will not pick a round for "
                       f"an envelope no recorded request accounts for")
        # Round-2 F1: this door could file a SECOND, different verdict for
        # an already-ruled round (`--round N` skips the open-round lookup),
        # and `respond` then answered whichever file the author chose. The
        # same guard `close` applies: a round is ruled once.
        conflict = transport.verdict_conflict(ledger, round_no, digest,
                                              lineage)
        if conflict is not None:
            return _blocked(
                "",
                f"round {round_no} is already ruled by a different verdict "
                f"(recorded digest {conflict.get('source_digest', '?')[:16]}…); "
                f"a round is ruled once, and a second ruling would let the "
                f"author choose which one to answer (round-2 F1)",
                remedy=f"nothing to add: the round's verdict is recorded; if "
                       f"this file is the ruling that should stand, that is a "
                       f"lineage decision for a person, not a second event")
        # Verdict, alias, finding and closure events come from ONE shape
        # authority shared with `close` (transport.verdict_events): reviewer
        # closures become ledger state through the CLI, not by hand-editing
        # the ledger (round-3 F3), and identity is carried across the
        # fingerprint version change rather than silently re-identified
        # (§5.3b).
        try:
            new_events = transport.verdict_events(
                parsed, round_no, digest, size, args.tokens,
                answering=ledger.standing_dispositions(lineage,
                                                       round_no=round_no - 1))
        except transport.Refusal as exc:
            return _blocked(exc.next_cmd, str(exc), remedy=exc.remedy)
        added += ledger.add_all(new_events, lineage=lineage)
    elif kind == "request":
        # Sweep F3: this door validated nothing. A wrapped envelope carrying
        # the single word `malformed` was recorded as a round's request with
        # exit 0 — no taxonomy, no Claim, no attestations, no reachability
        # stamp — and the ledger is the identity authority every later
        # lifecycle decision reads. The same request validation `take` and
        # `handoff` apply runs here, against the cap in force for this
        # lineage, before anything is appended; a refusal appends nothing.
        agreement = transport.tool_agreement(parsed)
        items = validate_request(
            parsed, cfg,
            round_cap=ledger.effective_round_cap(cfg.round_cap, lineage))
        if errors_in(items):
            return _finish(items,
                           paths.command(*paths.lits(TOOL_NAME, "validate"),
                                         args.envelope))
        round_no = args.round or int(parsed.attrs.get("round", 0)) or None
        if round_no is None:
            return _blocked(
                "", "the request carries no round attribute and none was "
                    "given",
                remedy=f"a person supplies the round with `--round <number>`, "
                       f"or re-emits {args.envelope} from `handoff`, which "
                       f"stamps one; the tool will not infer a round from an "
                       f"envelope that states none")
        # The request event AND its evidence events — the shape the other
        # two doors record, through the one function they use.
        added += ledger.add_all(transport.request_events(
            parsed, round_no, digest, size, args.tokens), lineage=lineage)
        ledger.add({"event": "ingest", "kind": "request",
                    "round": round_no, "digest": digest,
                    "tool": agreement["reader"],
                    "tool_agreement": agreement["agreement"],
                    **({"tool_writer": agreement["writer"]}
                       if agreement["writer"] else {})}, lineage=lineage)
    elif kind == "disposition":
        # Round 2 F2: this path recorded whatever identity it was handed.
        # A disposition binds by fingerprint, so it cannot be recorded
        # without the verdict that computes one — and if that verdict
        # cannot be retrieved, refusing is the only honest outcome.
        # Round 3 F2: resolved through the LEDGER, which is the record, not
        # through the kept file, which is a best-effort copy of it.
        against = transport.answered_verdict(cfg, parsed, ledger, lineage)
        if against is None:
            return _blocked(
                "",
                f"the verdict this disposition answers "
                f"({parsed.attrs.get('verdict_sha', '?')[:12]}) is not "
                f"retrievable as a RECORDED verdict: the ledger must hold "
                f"exactly one verdict for this round and the kept bytes must "
                f"reproduce its digest (round 2 F2, round 3 F2)",
                remedy=f"a person records that verdict first — "
                       f"`{paths.command(*paths.lits(TOOL_NAME, 'close', '--verdict'), paths.Ph("<the reviewer's file>"))}` — and then "
                       f"re-emits the response with `{paths.command(*paths.lits(TOOL_NAME, 'respond'))}`, "
                       f"which binds it to the record; a disposition is an "
                       f"answer to a recorded ruling, never to a file")
        # Round 4 F1: the third disposition-ingress door. `respond --out`
        # derives and refuses a mismatched author; this standalone door
        # used to record whatever the wire stamped, unverified — the exact
        # gap `disposition_events`' own docstring named. Same shared check,
        # before anything is appended. Round 5 F1: this door used to pass
        # only the wrapper attribute and discard the check's answer, so an
        # OMITTED author reached `disposition_events` unchanged and
        # appended `author: null` even with a recorded request author.
        # Reconcile wrapper against body first (the two can be edited
        # apart once the envelope is a file on disk), then record what the
        # check derives on both stamps — `disposition_events` reads the
        # wrapper attribute, but keeping the body member in step is the
        # same parity `respond` gets for free from `emit_disposition`.
        try:
            supplied = transport.disposition_supplied_author(parsed)
            resolved = transport.check_disposition_author(
                ledger, int(parsed.data.get("round", 0)), supplied, lineage)
        except transport.Refusal as exc:
            return _blocked(exc.next_cmd, str(exc), remedy=exc.remedy)
        parsed.attrs["author"] = resolved
        parsed.data["author"] = resolved
        items = validate_disposition(parsed, cfg, against=against)
        if errors_in(items):
            return _finish(items,
                           paths.command(*paths.lits(TOOL_NAME, "validate"),
                                         args.envelope))
        added += ledger.add_all(
            transport.disposition_events(parsed, against, cfg),
            lineage=lineage)
        # Round-1 F3. This is the ONE door a disposition can arrive at from
        # another installation — `respond --out` writes and records in one
        # process, so its stamp is this end's by construction and comparing
        # there could only ever say `match`. Here it cannot, so here is
        # where the comparison means something.
        agreement = transport.tool_agreement(parsed)
        ledger.add({"event": "ingest", "kind": "disposition",
                    "round": int(parsed.data.get("round", 0)),
                    "digest": digest, "tool": agreement["reader"],
                    "tool_agreement": agreement["agreement"],
                    **({"tool_writer": agreement["writer"]}
                       if agreement["writer"] else {})}, lineage=lineage)
    else:
        return _blocked(
            paths.command(*paths.lits(TOOL_NAME, "validate"), args.envelope),
            f"{args.envelope} is not a recognizable request, verdict or "
            f"disposition envelope")
    # The review the rows joined, by id: this door records into a lineage it
    # RESOLVED rather than one the caller named, so the record has to say
    # which (brief `keyed-lineage`, item 2).
    payload = {"ok": True, "events_added": added, "lineage": lineage,
               "ledger": str(ledger.path)}
    report = ""
    if agreement is not None:
        payload["tool"] = agreement
        report = render_tool_agreement(agreement)
    _out(payload, f"added {added} events to {ledger.path}\n{report}".rstrip()
                  + "\n")
    return EXIT_OK


def cmd_authorize_cap(args, cfg) -> int:
    """Record a reason-bearing override of the round cap for this lineage."""
    ledger = _ledger(cfg, args)
    try:
        lineage = _write_lineage(ledger, cfg, "ledger authorize-cap")
    except LineageUnchoosable as exc:
        return _blocked("", str(exc), remedy=exc.remedy)
    added = ledger.add({"event": "cap_override", "round_cap": args.to,
                        "reason": args.reason, "authorized_by": args.by,
                        "default_cap": cfg.round_cap}, lineage=lineage)
    _out({"ok": True, "recorded": bool(added), "round_cap": args.to,
          "lineage": lineage,
          "default_cap": cfg.round_cap, "ledger": str(ledger.path)},
         f"round cap for lineage {lineage}: {args.to} "
         f"(repo default stays {cfg.round_cap})")
    return EXIT_OK


def cmd_authorize_breaker(args, cfg) -> int:
    """Record the human's decision to continue past a fired breaker (§5.3d,
    sweep F8) — the counterpart of the handoff preflight's stop."""
    ledger = _ledger(cfg, args)
    try:
        lineage = _write_lineage(ledger, cfg, "ledger authorize-breaker")
    except LineageUnchoosable as exc:
        return _blocked("", str(exc), remedy=exc.remedy)
    try:
        rec = transport.authorize_breaker(cfg, ledger, args.breaker,
                                          args.reason or "", args.by or "",
                                          lineage)
    except transport.Refusal as exc:
        return _blocked(exc.next_cmd, str(exc),
                        remedy="a person supplies the decision — a real "
                               "reason, a named human, a breaker that is "
                               "firing — or takes the other one, "
                               f"`{paths.command(*paths.lits(TOOL_NAME, 'close', '--lineage'))}`")
    rec["ok"] = True
    rec["ledger"] = str(ledger.path)
    _out(rec, f"breaker {rec['breaker']}: continuing past "
              f"{len(rec['covers'])} firing(s) — {', '.join(rec['covers'])} — "
              f"authorized by {rec['authorized_by']}")
    return EXIT_OK


def cmd_ledger_correct_actor(args, cfg) -> int:
    """Record that one or more retained ledger events misattribute their
    actor (round 3 F2's second half) — an append-only correction, never an
    edit: the ledger keeps the wrong stamp AND the record of it being
    wrong, exactly as `authorize_breaker` keeps a firing AND the decision
    to continue past it."""
    ledger = _ledger(cfg, args)
    try:
        rec = transport.correct_actor(cfg, ledger, args.event or [],
                                      args.actor or "", args.by or "",
                                      args.reason or "",
                                      _read_lineage(ledger, cfg,
                                                    "ledger correct-actor"))
    except transport.Refusal as exc:
        report_cmd = paths.command(
            *paths.lits(TOOL_NAME, "ledger", "report"))
        return _blocked(exc.next_cmd, str(exc),
                        remedy=f"a person supplies the decision — a real "
                               f"reason, a named corrector, the true actor, "
                               f"and at least one recorded event uid to "
                               f"correct (`{report_cmd}` lists recent "
                               f"events)")
    rec["ok"] = True
    rec["ledger"] = str(ledger.path)
    _out(rec, f"corrected {len(rec['corrects'])} event(s) — "
              f"{', '.join(rec['corrects'])} — true actor: "
              f"{rec['true_actor']}, asserted by {rec['corrected_by']}")
    return EXIT_OK


def cmd_ledger_report(args, cfg) -> int:
    ledger = _ledger(cfg, args)
    # Round-4 F8 and F5: the declared manifest and the declared budget travel
    # with every product report, so the CLI and the emitter compute what the
    # ledger API computes. A metric that is only correct when a unit test
    # supplies its inputs is not wired.
    try:
        lineage = _read_lineage(ledger, cfg, "ledger report")
    except LineageUnchoosable as exc:
        return _blocked("", str(exc), remedy=exc.remedy)
    report = ledger.report(lineage,
                           ledger.effective_round_cap(cfg.round_cap, lineage),
                           gate_manifest=cfg.gate_ids,
                           token_budget=cfg.token_budget,
                           blocking_severities=cfg.blocking_severities)
    if args.format == "md" or (args.format is None and _tty()):
        print(render_report_md(report))
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return EXIT_OK


def cmd_ledger_convergence(args, cfg) -> int:
    """Is this lineage closing its findings, or hunting the same domains?

    The verb the advisory round cap points at. A count of rounds says how
    long the loop has run; this says which direction it is going, from the
    closures and anchors already in the record.
    """
    ledger = _ledger(cfg, args)
    try:
        lineage = _read_lineage(ledger, cfg, "ledger convergence")
    except LineageUnchoosable as exc:
        return _blocked("", str(exc), remedy=exc.remedy)
    result = ledger.convergence(lineage)
    result["lineage"] = lineage
    result["ok"] = True
    result["ledger"] = str(ledger.path)
    _out(result, render_convergence_md(result))
    return EXIT_OK


def cmd_import_legacy(args, cfg) -> int:
    """§5.2 legacy import: ingest pre-derived events (JSONL, one event per
    line — `import`, `lineage`, `disposition`, `closure` …) into the ledger.

    Until 2026-08-16 this verb derived those events itself from one
    repository's own review corpus. That derivation is vocabulary — the
    labels, file names and round records of one history — and stays behind
    with that repository (§11); what travels is the mechanism: a ledger that
    binds pre-ledger findings by explicit import events. Whoever holds a
    legacy corpus writes the derivation and pipes its output here.

    `--source-commit` is that derivation's other half (round-10 F2). Every
    row cites a tracked path (`source_path`) and the digest it claims for
    those bytes; this tool reads the content ITSELF from
    `<source-commit>:<source_path>`, hashes it itself, and requires the
    row's own claim text to occur inside it. The anchor must be contained in
    a remote-tracking ref of this repository's remote, so a row can cite only
    history that was already shared — never a file written in the same
    session as the batch, which is what round 9's caller-written source
    manifest could not tell apart.

    The anchor is a REQUIRED flag rather than a ref this verb resolves for
    itself, and that is a decision, not an omission. A moving default would
    make one batch import today and refuse tomorrow, and would silently
    re-point the bytes a row's digest was computed against; the anchor is a
    fact of the derivation, so the derivation states it. It is verified
    here, never trusted.
    """
    raw = (sys.stdin.read() if args.events == "-"
           else Path(args.events).read_text(encoding="utf-8"))
    events, bad = [], []
    for n, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            # Round-10 F4: `json.loads` keeps the LAST of repeated members
            # and drops every earlier one BEFORE the closed schema sees the
            # object, so `{"round": 999, "round": 1}` was already 1 and a
            # conflict the grammar exists to catch never reached it.
            # `wire.load_json` is the tool's one JSON door and refuses a
            # repeat at any depth — the same mechanism `respond`'s
            # disposition body has read through since lineage-3 round 4.
            ev = wire.load_json(line)
        except wire.DuplicateMember as exc:
            bad.append(f"line {n}: {exc}")
            continue
        except json.JSONDecodeError as exc:
            bad.append(f"line {n}: {exc.msg}")
            continue
        if not isinstance(ev, dict) or not ev.get("event"):
            bad.append(f"line {n}: not an event object with an 'event' key")
            continue
        events.append(ev)
    if bad:
        return _blocked("",
                        "undecodable event line(s): " + "; ".join(bad[:5]),
                        remedy=f"a person must repair "
                               f"{paths.display_path(args.events)} so every "
                               f"line is one JSON event object stating each "
                               f"member once, then re-run "
                               f"`{paths.command(*paths.lits(TOOL_NAME, 'import-legacy'), args.events)}`")
    if not args.source_commit:
        return _blocked(
            "",
            "no --source-commit was supplied: every imported row cites bytes "
            "at a commit of this repository's own shared history, and a batch "
            "that substantiates its rows from a file its own author wrote "
            "establishes nothing (round-10 F2)",
            remedy=f"a person passes the commit the derivation read its "
                   f"sources from — one this clone can see in a "
                   f"remote-tracking ref — to "
                   f"`{paths.command(*paths.lits(TOOL_NAME, 'import-legacy'), args.events, paths.Lit('--source-commit'), paths.Ph('<commit>'))}`")
    # Shape first, and pure: a malformed batch is refused by name before
    # the tool reads the ledger or touches Git (design §3.2; 2026-09-03).
    shape_problems = transport.legacy_shape_problems(events)
    if shape_problems:
        return _blocked(
            "", "legacy import: " + "; ".join(shape_problems[:5]),
            remedy=f"a person corrects the derivation that produced "
                   f"{paths.display_path(args.events)} against the closed "
                   f"per-kind schema, then re-runs "
                   f"`{paths.command(*paths.lits(TOOL_NAME, 'import-legacy'), args.events)}`")
    authority, source_problems = transport.read_source_authority(
        cfg, args.source_commit, fetch=not args.no_fetch)
    if source_problems:
        return _blocked(
            "", "legacy source anchor: " + "; ".join(source_problems[:5]),
            remedy=f"a person names an anchor commit this clone can see in a "
                   f"remote-tracking ref; the source authority is shared "
                   f"repository history, so nothing is imported against a "
                   f"commit only this machine has")
    ledger = _ledger(cfg, args)
    # Rounds 7 F2 / 8 F1-F2 / 9 F1 / 10 F1-F2-F4: this door's arbitrary
    # pre-derived JSON cannot be trusted the way the product path's own
    # derivation can be — a duplicate-refusing parse, a closed per-kind
    # schema, a git-anchored source whose bytes and text this tool reads
    # itself, batch-local identity resolution, and a lifecycle recomputation
    # over PERSISTED answers as well as candidate ones. Checked as a whole
    # batch, before anything is appended — a partially bad import must
    # append zero events, not the rows that happened to come first.
    # The lineage the imported rows join: this branch's open one, else a
    # fresh id — an import is a review's history arriving, and history that
    # belongs to no open review is its own lineage. `migrate-state` and
    # `import-legacy` are the migration agent's verbs; this is only the key
    # they must write.
    try:
        import_lineage = _read_lineage(ledger, cfg, "import-legacy")
    except LineageUnchoosable as exc:
        return _blocked("", str(exc), remedy=exc.remedy)
    problems = transport.legacy_import_problems(ledger, events, authority,
                                                import_lineage)
    if problems:
        return _blocked(
            "",
            "legacy import: " + "; ".join(problems[:5]),
            remedy=f"a person corrects the derivation that produced "
                   f"{paths.display_path(args.events)} — the closed per-kind "
                   f"schema, a `source_path` and `source_digest` on every row "
                   f"naming bytes at the anchor that CONTAIN the row's own "
                   f"claim text, a conflict-free and acyclic alias graph, and "
                   f"an explicit `answers_round` naming a real prior ruling "
                   f"behind every settling answer — then re-runs "
                   f"`{paths.command(*paths.lits(TOOL_NAME, 'import-legacy'), args.events)}`; "
                   f"the tool will not append part of a batch it cannot "
                   f"verify")
    added = ledger.add_all(events, lineage=import_lineage)
    _out({"ok": True, "events_added": added, "events_total": len(events),
          "lineage": import_lineage,
          "source_commit": authority.commit,
          "source_refs": authority.refs,
          "source_witness": authority.witness,
          "source_artifacts": len(authority),
          "ledger": str(ledger.path)},
         f"imported {added} of {len(events)} events into {ledger.path} "
         f"(from {len(authority)} source path(s) at "
         f"{authority.commit[:12]}, carried by "
         f"{', '.join(authority.refs)} — {authority.witness})")
    return EXIT_OK


def _claim_unreadable_exit(args, exc: "emit.ClaimUnreadable") -> int:
    """A claim file was supplied and could not be read (round 3 F3): an
    error, never mistaken for "no claim given"."""
    return _blocked(
        "", f"the claim file could not be read ({exc})",
        remedy=f"a person must make {args.claim_file} readable, or pass a "
               f"different --claim-file; the tool will not emit a request "
               f"whose authored Claim it could not load")


def _claim_defect_exit(exc: "emit.ClaimDefective") -> int:
    """The ONE typed blocked recovery for a claim file that is not a claim
    (lineage-3 rounds 5 and 7).

    Round 5 gave the repeated member this exit. Round 7 found four defects
    that did not have it — a top-level `null`, `true`, `3`, `"x"` or `[]`; a
    declared member of the wrong type; an unknown member; and malformed JSON,
    which escaped as a raw JSONDecodeError, the one input defect the tool
    answered with a traceback rather than a structured refusal. They all
    arrive here now: the defect and its location are named, and a person
    edits the file — the tool will not edit the author's own input.
    """
    where = f" (at {exc.member})" if exc.member else ""
    if exc.path is None:
        # The defect is in the invocation, so there is no file to correct.
        remedy = ("a person passes a real path to --claim-file, or omits the "
                  "option entirely — omission is the recorded no-claim "
                  "state, and an empty path is not the same invocation")
    else:
        remedy = (f"a person corrects {exc.path}{where} and re-runs this "
                  f"command; the tool will not edit the author's own input "
                  f"file, and will not emit a request whose authored Claim "
                  f"it could not read as one")
    return _blocked("", f"the claim file {exc.detail}", remedy=remedy)


def _round_reference(carrier: str, round_no, lineage: str) -> str | None:
    """`git:<lineage>/<round>` for a round carried on a ref, else None.

    The lineage is a fact of the ledger and the round is a fact of the
    envelope, so neither side of a relay can derive this on its own — which
    is why the reference is a word a person carries rather than something
    the far end recomputes. `lineage` is now required rather than derived
    here: a ledger holds several, and there is no "the" lineage to fall
    back to (brief `keyed-lineage`).
    """
    if carrier != vocab.TRANSPORT_GIT or not round_no or not lineage:
        return None
    return transport.round_reference(lineage, int(round_no))


def _decide(cfg, applied=None) -> list:
    """The `decide` list for a result: one entry per key this repository
    never declared (2026-09-03, brief `config-absent-asks-once`).

    Absence used to be silent — `transport` fell to `path`, `debug` to off,
    a budget to uncounted — and a silent default is a decision the
    repository never made and nobody was ever told about. The tool has no
    model and prints JSON, so it cannot ask; it can REPORT, and one adapter
    rule turns the report into a question asked once per key, whose answer
    is the printed line committed to `review.toml`. A declared key produces
    no entry, which is what ends the asking permanently.

    Not every undeclared key is here: the ones that REFUSE when absent —
    the taxonomy, the roles, `review.toml` itself — are ruled refusals, not
    defaults, and a decision has already been forced about them.
    """
    return cfg.decisions(applied)


def _scope_report(items) -> dict:
    """The claim-versus-span notice as a result FIELD, or nothing.

    Its own key rather than the shared `items` list: `items` is the
    validation channel whose errors decide the exit, and this report never
    decides one (round-9 F4 is non-blocking). A field an agent can read and
    relay, beside a line a human reads.
    """
    return {"scope": [i.as_dict() for i in items]} if items else {}


def _scope_text(items) -> str:
    return "".join(f"\n\nnotice: [{i.code}] {i.message}" for i in items)


def _debug_flag(args) -> bool | None:
    """The invocation's explicit word on the debug stamp, or None when it
    said nothing — the state `emit.resolve_debug` hands to the
    repository's standing `[roles] debug` declaration, then to off."""
    if getattr(args, "debug", False):
        return True
    if getattr(args, "no_debug", False):
        return False
    return None


def _emit(args, cfg, ledger, captured: "emit.CapturedClaim",
          selected_transport: str, lineage: str):
    """emit-request's body, shared with handoff: push, emit, validate.
    Returns (envelope, parsed, scope) or an int exit code, where `scope` is
    the non-blocking claim-versus-span report (`validate.scope_items`).

    Round 2 F1 (lineage 12): the effective roles are NOT passed in. They are
    resolved inside `ensure_pushed`, against the committed authority, from
    the flags — because the only authority entitled to authorise a role is
    the one the reviewer will judge it by, and before the commit exists
    there is no such authority to ask.

    `captured` is the claim the verb already read and closed at the capture
    boundary, passed explicitly (lineage-3 round 7 F1). There is no fallback
    that loads it here. The parameter used to be optional, with `None`
    meaning "not captured yet" — and `parse_claim("null", ...)` returned
    None, so a claim file whose entire content was the JSON value `null`
    collided with the sentinel and this function reopened the path. The
    emitted Claim could then come from the second read while the cache
    digest and the ledger attested the first.
    """
    claim = captured.claim
    # §9bis.4: commit outstanding work, push the reviewed branch, observe the
    # remote ref — BEFORE emission, refusing every state that cannot yield a
    # fetchable target. The record is what the envelope stamps.
    try:
        record = emit.ensure_pushed(cfg, head=args.head,
                                    local_only=args.local_only,
                                    commit_subject=claim.get("commit_subject"),
                                    round_no=emit.next_round(ledger,
                                                             lineage),
                                    transport=selected_transport,
                                    author_flag=getattr(args, "author", None),
                                    reviewer_flag=getattr(args, "reviewer",
                                                          None),
                                    scope_paths=claim.get("scope_paths"),
                                    allow_outside_scope=getattr(
                                        args, "allow_outside_scope", False))
    except (emit.AuthorityAbsent, emit.RoleSelectionError,
            emit.SweepRefused) as exc:
        # RVW-T17: ONE catch, reached by both author doors, because both
        # reach `ensure_pushed` through this function. Round 7 F2's defect —
        # two author doors accepting different states — has no second place
        # to live any more.
        return _blocked("", str(exc), remedy=exc.remedy)
    # U-1. What governs the emission is the authority resolved FROM THE
    # TARGET, not this checkout's config. The two are routinely different
    # and nothing compared them: `LOUPE_CONFIG` names an out-of-tree file
    # the reviewer never reads; an `assume-unchanged` index entry makes the
    # worktree bytes and the committed bytes disagree; and the wrapper tag
    # decides the envelope's own element name, so a checkout saying `x`
    # emitted `<x-review-request>` while `take` refused E-TAG under the
    # target's `loupe`. Moving the ORIGIN question to the artifact without
    # moving the VALUES would have left round 2 F1 alive one level out —
    # the author's taxonomy, gate ids, budget, blocking severities, round
    # cap, roles and tag all reaching the envelope from a source the
    # reviewer does not read. `take` judges every one of them against the
    # target's config, so that is what renders them.
    governing = record["governing"]
    # Round 1 F2: the stamps are the roles re-resolved against the COMMITTED
    # authority, not the tuple resolved from this checkout before the commit
    # existed. The early tuple is still computed and still refuses — it is
    # the cache key and it catches an unassigned or rejected identity before
    # any git call — but it is not what reaches the envelope.
    roles = record["roles"]
    envelope = emit.emit_request(governing, ledger, claim, base=args.base,
                                 head=record["sha"], reachability=record,
                                 author=roles[0], reviewer=roles[1],
                                 transport=selected_transport,
                                 debug=emit.resolve_debug(cfg,
                                                          _debug_flag(args)),
                                 lineage=lineage)
    parsed = wire.parse_request(envelope)
    base = args.base or max((e for e in ledger.current(lineage)
                             if e.get("event") == "verdict"),
                            key=lambda e: e["round"])["sha"]
    shape = emit.diff_shape(cfg.repo_root, base, parsed.sha)
    items = validate_request(parsed, governing,
                             recomputed_shape=(shape["files"],
                                               shape["insertions"],
                                               shape["deletions"]),
                             round_cap=ledger.effective_round_cap(
                                 governing.round_cap, lineage))
    if errors_in(items):
        return _finish(items, "",
                       remedy=f"a person must correct review.toml or "
                              f"{args.claim_file} to satisfy the items above, "
                              f"then re-run "
                              f"`{paths.command(paths.Lit(TOOL_NAME), paths.token(args.command))}`")
    # Round-9 F4, non-blocking: the authored scope against the span that was
    # just measured. Both halves are in hand exactly here — the claim the
    # capture boundary closed, and `file_list` from the same `diff_shape`
    # the envelope's own "What changed" block renders — so the comparison
    # needs no second measurement and can disagree with neither.
    return envelope, parsed, scope_items(captured.claim,
                                        shape["file_list"])


def cmd_emit_request(args, cfg) -> int:
    # Round 7 F1: the same boundary as handoff, in the same place — before
    # the ledger is constructed or consulted, before Git, gates or emission.
    # This verb judged the claim inside `_emit`, which was already ahead of
    # every git and ledger call, but the rule is the boundary's, not one
    # call site's.
    try:
        captured = _capture_claim(args)
    except emit.ClaimUnreadable as exc:
        return _claim_unreadable_exit(args, exc)
    except emit.ClaimDefective as exc:
        return _claim_defect_exit(exc)
    # The other authored inputs, judged at the same boundary: before the
    # ledger is consulted, before the push, gates or emission (§4;
    # round-2 F3 for the references).
    # Round 2 F1 (lineage 12): NO config-dependent role check runs here.
    # Round 1 F2 moved authorization to the committed authority and left
    # this call in place as a cache key and an early refusal — but every
    # check it makes reads the CHECKOUT's permitted and rejected lists, so
    # a stale checkout could veto an identity the target permits. That is
    # the inverse of the defect F2 closed, in the same place. The flags
    # travel instead, provenance intact, and the target authorises: at
    # `ensure_pushed` on the cold path, inside `cached_handoff` on the warm
    # one, where HEAD is provably the target.
    try:
        # RVW-T11: the declared topology is an authored input like the role
        # stamp, and it is resolved at the same boundary for the same two
        # reasons — an unrecognised value is refused before anything is
        # committed, pushed, run or recorded, and the EFFECTIVE value is what
        # the cache must key on. Reading it later, at the emitter, would let a
        # warm envelope stamped `path` answer an invocation that asked for
        # `paste`.
        selected_transport = emit.resolve_transport(
            cfg, getattr(args, "transport", None),
            local_only=bool(getattr(args, "local_only", False)))
        emit.check_references(cfg, captured.claim.get("references"))
    except (emit.RoleSelectionError, emit.TransportSelectionError,
            emit.ReferenceUnbound) as exc:
        return _blocked("", str(exc), remedy=exc.remedy)
    # Round 7 F2: the same authority boundary as `handoff`, at the same point
    # RVW-T17. This door used to carry its own copy of the authority check,
    # because round 7 F2 found `emit-request` emitting the exact state
    # `handoff` refused — two author doors accepting different states, which
    # is round 2 F1 with the author's own ends as the two ends. That fix
    # made them call one function; this one makes them share one CALL SITE.
    # Both doors reach `emit.ensure_pushed` through `_emit`, and the check
    # lives there, so the defect class is now unrepresentable rather than
    # tested: there is no second place to put a different question.
    ledger = _ledger(cfg, args)
    # `emit-request` writes an envelope to a file and opens nothing, so it
    # READS the lineage rather than minting one: the round it renders
    # belongs to whatever review this branch is on.
    try:
        lineage = _read_lineage(ledger, cfg, "emit-request")
    except LineageUnchoosable as exc:
        return _blocked("", str(exc), remedy=exc.remedy)
    result = _emit(args, cfg, ledger, captured, selected_transport, lineage)
    if isinstance(result, int):
        return result
    envelope, parsed, scope = result
    if args.out:
        Path(args.out).write_text(envelope, encoding="utf-8")
        _out({"ok": True, "out": args.out, "sha": parsed.sha,
              **_scope_report(scope),
              "decide": _decide(cfg, {vocab.DECIDE_TRANSPORT:
                                      selected_transport})},
             f"wrote {args.out}{_scope_text(scope)}")
    else:
        print(envelope, end="")
    return EXIT_OK


# Round 3 F3's exception moved to `emit` with the capture it belongs to;
# the name stays here for callers that import it from the CLI.
ClaimUnreadable = emit.ClaimUnreadable


def _capture_claim(args) -> "emit.CapturedClaim":
    """THE claim boundary for both emitting verbs: read the supplied claim
    exactly once and close its grammar, before anything else runs.

    Round 3 F3: an unreadable supplied claim is an error, not an absence —
    "no claim was given" and "a claim was given and could not be read" once
    shared the empty sentinel, and the cache skips comparison on empty, so
    the tool served a warm envelope built from a DIFFERENT claim and reported
    `cached: true`. Round 4 F1: supplying no claim is its own recorded state,
    compared like any other. Round 5 F1: a member stated twice is refused by
    name. Round 6 F1: all of it happens before the ledger is consulted.
    Round 7 F1: and it closes the whole domain, not one defect of it — the
    top-level kind, the required members, the unknown ones, the type of every
    declared field and of every nested reference. The digest and the value
    come from the same single read; `emit.capture_claim` is where that read
    happens, and nothing downstream may reopen the path.
    """
    # The raw CLI value, NOT a Path: `Path(path) if path else None` folded an
    # explicitly empty --claim-file into the no-claim state (round 8 F3). The
    # boundary owns that distinction, like every other state of this input.
    return emit.capture_claim(getattr(args, "claim_file", None))


def cmd_handoff(args, cfg) -> int:
    """Author side, one verb (§9bis.3, RVW-T9): emit-request, record the
    request, keep the bytes outside the tree, print the reviewer's literal
    command, and stop."""
    # The authored half of the envelope's input, captured and judged FIRST.
    # The tip alone does not determine what this request says, so the cache
    # may not key on the tip alone (found while emitting round 3 of this
    # tool's own review).
    #
    # Round 3 F3: a supplied-but-unreadable claim refuses HERE, so it can
    # never be mistaken for "no claim given". Round 5 F1: so does a claim
    # that states a member twice — a warm envelope keyed on the bytes of a
    # defective claim would otherwise be served as if the claim were sound.
    #
    # Lineage-3 round 6 F1: and they refuse before the ledger is consulted,
    # not merely before the cache. Malformed author bytes are not a
    # lifecycle question, and reading lifecycle state to decide a round the
    # tool is about to refuse to open makes ledger I/O answer to input whose
    # grammar has not been accepted. The claim is settled, then the ledger.
    #
    # Round 7 F1: "settled" means the whole domain — kind, required members,
    # unknown members, every field and nested-reference type — not just the
    # one defect a round happened to name.
    try:
        captured = _capture_claim(args)
    except emit.ClaimUnreadable as exc:
        return _claim_unreadable_exit(args, exc)
    except emit.ClaimDefective as exc:
        return _claim_defect_exit(exc)
    claim_digest = captured.digest
    # Per-invocation role selection and the claim's required references are
    # authored input like the claim itself, judged at the same boundary —
    # before the ledger, the cache, Git, gates or emission (§4; round-2 F3
    # for the references). Roles resolve before the cache is consulted
    # because the effective stamp is part of what makes a kept envelope
    # warm; references bind before it so a warm re-serve cannot re-record a
    # request whose required evidence no commit carries.
    try:
        # RVW-T11: same boundary, same reason as the role stamp — the
        # effective value is part of what makes a kept envelope warm, so it
        # is resolved before the cache is consulted, not at the emitter the
        # cache is meant to skip.
        selected_transport = emit.resolve_transport(
            cfg, getattr(args, "transport", None),
            local_only=bool(getattr(args, "local_only", False)))
        emit.check_references(cfg, captured.claim.get("references"))
    except (emit.RoleSelectionError, emit.TransportSelectionError,
            emit.ReferenceUnbound) as exc:
        return _blocked("", str(exc), remedy=exc.remedy)
    ledger = _ledger(cfg, args)
    # Sweep F4 / F8: the lifecycle preflight, before the cache is consulted,
    # anything committed, pushed, run or emitted. Every finding of the
    # just-closed verdict has its disposition; no fired breaker is unanswered
    # by a recorded human decision. Both are blocked states — an authored
    # answer or a decision, never a command.
    # The declared approval pathway, before anything is committed, pushed,
    # run or emitted (brief `approval-pathway-lock-in`, item 2). Only this
    # verb: `emit-request` writes an envelope to a file, while `handoff` is
    # the step that opens the round the approval would have to close.
    try:
        emit.check_enforcement(cfg)
    except emit.EnforcementUnsatisfiable as exc:
        return _blocked("", str(exc), remedy=exc.remedy)
    # WHICH LINEAGE THIS HANDOFF IS (brief `keyed-lineage`, item 3): the
    # OPEN lineage whose requests were recorded on this worktree's branch,
    # and a NEW one when this branch holds none. A second worktree on a
    # second branch therefore opens its own review instead of being
    # refused, which is what retires the stopgap that stood here.
    branch = transport.current_branch(cfg)
    try:
        lineage = _branch_lineage(ledger, cfg, "handoff", branch=branch)
    except LineageUnchoosable as exc:
        return _blocked("", str(exc), remedy=exc.remedy)
    opening = lineage is None
    #
    # Lineage 27 round 1 F1: the RESERVATION, taken before the lifecycle is
    # read and released only after the round is recorded.
    #
    # A ledger read is one moment, and everything after it destroys: the
    # commit and push in `_emit`, the gate run that holds the interval open
    # for minutes, and `record_handoff`, which under the `git` carrier
    # force-pushes the envelope to a `(lineage, round)` ref. Two handoffs of
    # ONE lineage that merely overlap therefore both pass any check —
    # neither is refused, both record the same round, and the second
    # overwrites the first. A check cannot protect a write it races; only
    # something HELD across the interval can, which is what this is.
    #
    # Two locks, because there are two resources — and neither is the
    # repository. CONTINUING a lineage holds `lineage-<id>.lock`, which
    # excludes only the commands acting on the SAME review. OPENING one
    # holds the branch's `lineage-new-<branch>.lock` instead, because an id
    # that does not exist cannot be locked by id and two handoffs on ONE
    # branch would otherwise mint two ids for the review the operator meant
    # to open once. A lock over the whole repository would be held across
    # the gates and would refuse the second worktree for the whole interval,
    # which is the refusal keying exists to retire.
    #
    # The branch is resolved once above and handed to the preflight, so the
    # reservation's record and the refusals below name the same worktree.
    opening_lock = (transport.NewLineageReservation(ledger, branch=branch,
                                                    verb="handoff")
                    if opening else None)
    reservation = None
    if opening_lock is None:
        reservation = transport.LineageReservation(ledger, branch=branch,
                                                   verb="handoff",
                                                   lineage=lineage)
    else:
        try:
            opening_lock.acquire()
        except transport.Refusal as exc:
            return _blocked("", str(exc), remedy=exc.remedy)
        try:
            # Re-read under the lock: an opener that finished while this one
            # waited may have recorded the very lineage this branch should
            # continue, and continuing it is what the branch rule says. A
            # minted id needs no lock of its own — nothing else can name it.
            #
            # Round-2 F2: the re-read has to reach the FILE. `Ledger.events`
            # caches, so this "re-read" was reading the same snapshot the
            # selection above took, and the opener it exists to observe was
            # invisible to it.
            ledger.reload()
            lineage = _branch_lineage(ledger, cfg, "handoff", branch=branch)
        except LineageUnchoosable as exc:
            opening_lock.release()
            return _blocked("", str(exc), remedy=exc.remedy)
        if lineage is None:
            lineage = transport.new_lineage_id()
        else:
            reservation = transport.LineageReservation(
                ledger, branch=branch, verb="handoff", lineage=lineage)
    try:
        if reservation is not None:
            reservation.acquire()
    except transport.Refusal as exc:
        # Blocked, like every other refusal here: the recovery is to wait
        # for the holder, which is a person's move and no command of ours.
        if opening_lock is not None:
            opening_lock.release()
        return _blocked("", str(exc), remedy=exc.remedy)
    # Held through `record_handoff` on every path — a refused emission, a
    # red gate, an exception — which is what `finally` is for.
    try:
        try:
            # Round-2 F2: THE AUTHORITATIVE LIFECYCLE SNAPSHOT, taken here
            # and not before. The lineage was selected, and `Ledger.events`
            # cached, before the reservation existed; a close completing in
            # that interval was therefore invisible, the acquisition
            # succeeded normally, and this handoff appended a round-1
            # request AFTER that lineage's clean closure — into a review
            # that had ended, where a fresh `brief` reports no open request.
            # A lock excludes an operation while it is held; only a read
            # taken under it is current.
            ledger.reload()
            if reservation is not None and ledger.is_closed(lineage):
                return _blocked(
                    "",
                    f"lineage {lineage} was closed while this handoff was "
                    f"waiting for its reservation, so the round it was "
                    f"opening has no review to open into; nothing was "
                    f"committed, emitted or recorded",
                    remedy=f"a person re-runs "
                           f"`{paths.command(*paths.lits(TOOL_NAME, 'handoff', '--claim-file'), paths.Ph('<claim>'))}` "
                           f"— this branch now holds no open review, so that "
                           f"run opens a new one rather than appending "
                           f"behind a terminal marker")
            transport.handoff_preflight(cfg, ledger, lineage, branch=branch)
        except transport.Refusal as exc:
            # Round 7 F3: this replaced every preflight refusal's remedy with one
            # sentence about dispositions and decisions, so the configuration
            # refusal reached agents with a recovery that could not repair it.
            # A blocked exit has no runnable `next`; its remedy is the only
            # recovery field there is, and it belongs to the refusal that raised.
            return _blocked("", str(exc),
                            remedy=exc.remedy or
                            "a person supplies what is missing — the "
                            "dispositions, or the recorded decision — then "
                            "re-runs this command")
        round_no = emit.next_round(ledger, lineage)
        # User decision 2026-08-25: past the cap the tool emits and INVESTIGATES
        # rather than refusing. The count alone taught nothing — it fires on a
        # lineage doing exactly what it should — so the answer travels with the
        # warning: which findings the loop is failing to close, and which
        # domains keep producing new ones however many are fixed.
        past_cap = round_no > ledger.effective_round_cap(cfg.round_cap,
                                                         lineage)
        cached = transport.cached_handoff(
            cfg, ledger, round_no, lineage, claim_digest=claim_digest,
            transport=selected_transport,
            author_flag=getattr(args, "author", None),
            reviewer_flag=getattr(args, "reviewer", None),
            debug=emit.resolve_debug(cfg, _debug_flag(args)))
        if cached is not None:
            # RVW-T17, the call site the redesign had to rule on rather than
            # inherit: this branch returns BEFORE `_emit`, so it never reaches
            # `ensure_pushed` and never re-reads the committed authority. That
            # is correct, and it is worth saying why rather than leaving it to
            # look like an oversight. `cached_handoff` serves only when the
            # tree is clean AND HEAD equals the SHA of the request it kept. A
            # SHA names a tree; the same SHA is the same `review.toml` bytes,
            # necessarily. The authority was verified against that SHA when the
            # kept request was first emitted, so re-reading it here could not
            # return a different answer — and an amend, which is the one way
            # the content under a served request could move, changes HEAD and
            # busts the cache before this branch is reached.
            #
            # Round-3 F1: this branch reads a request RETAINED by an earlier
            # run — across processes, so possibly across installations — and
            # then re-records it and renders its relay. It is a
            # cross-installation reader like any other, and it compares BEFORE
            # either of those, because both are what a stale installation gets
            # wrong.
            agreement = transport.tool_agreement(
                wire.parse_request(cached["envelope"]))
            rec = transport.record_handoff(cfg, ledger, cached["envelope"],
                                           round_no, lineage,
                                           claim_digest=claim_digest)
            rec["cached"] = True
            rec["ok"] = True
            rec["decide"] = _decide(cfg, {vocab.DECIDE_TRANSPORT:
                                          selected_transport})
            rec["tool"] = agreement
            ledger.add({"event": "ingest", "kind": "request",
                        "round": round_no, "digest": rec.get("digest"),
                        "source": "handoff cache",
                        "tool": agreement["reader"],
                        "tool_agreement": agreement["agreement"],
                        **({"tool_writer": agreement["writer"]}
                           if agreement["writer"] else {})}, lineage=lineage)
            if args.out:
                Path(args.out).write_text(cached["envelope"], encoding="utf-8")
                rec["out"] = args.out
            _brief_into(rec, cached["envelope"], ledger, lineage)
            _out(rec, f"round {round_no} request for {rec['sha']} is already "
                      f"recorded and kept at {paths.display_path(rec['kept'])} — gates not re-run "
                      f"(§9bis.3 rule 5)\n\n{rec['brief']}\n\n"
                      f"{render_tool_agreement(agreement)}\n{rec['relay']}\n\n"
                      f"author: {rec['author_next']}")
            return EXIT_OK
        result = _emit(args, cfg, ledger, captured, selected_transport,
                       lineage)
        if isinstance(result, int):
            return result
        envelope, parsed, scope = result
        rec = transport.record_handoff(cfg, ledger, envelope, round_no,
                                       lineage, claim_digest=claim_digest)
        rec.update(_scope_report(scope))
        rec["cached"] = False
        rec["ok"] = True
        rec["decide"] = _decide(cfg, {vocab.DECIDE_TRANSPORT: selected_transport})
        if args.out:
            Path(args.out).write_text(envelope, encoding="utf-8")
            rec["out"] = args.out
        _brief_into(rec, envelope, ledger, lineage)
        if past_cap:
            rec["convergence"] = ledger.convergence(lineage)
        _out(rec, f"round {round_no} request emitted for {rec['sha']}, "
                  f"recorded ({rec['bytes']} bytes, sha256 {rec['digest'][:16]}…), "
                  f"kept at {paths.display_path(rec['kept'])}"
                  f"{_scope_text(scope)}\n\n"
                  f"{rec['brief']}\n\n"
                  f"{render_convergence_md(rec['convergence']) if past_cap else ''}"
                  f"{rec['relay']}\n\n"
                  f"author: {rec['author_next']}")
        return EXIT_OK
    finally:
        if reservation is not None:
            reservation.release()
        if opening_lock is not None:
            opening_lock.release()


def _brief_into(rec: dict, envelope: str, ledger, lineage: str) -> None:
    """Attach the plain-language précis and the relay to a result record.

    On both channels deliberately. A TTY reader sees the text; an agent reads
    JSON, where `brief` and `relay` are fields it can hand to the user rather
    than a summary it has to compose — which is the difference between the
    human always getting one and usually getting one.
    """
    parsed = wire.parse_request(envelope)
    rec["brief"] = brief.request_precis(parsed, ledger)
    rec["relay"] = brief.relay(
        rec.get("kept"), parsed, envelope,
        reference=_round_reference(transport.declared_transport(parsed),
                                   rec.get("round"), lineage))


def _read_envelope(arg: str, cfg=None, kind: str | None = None) -> str:
    """Envelope text, read as the BYTES it is written in (round-4 F1).

    `Path.read_text` and text-mode `sys.stdin` both read in universal-newline
    mode, which converts physical CRLF and CR to LF before the grammar, the
    validator, the digest or the relay ever sees the document — so a CRLF
    envelope validated successfully and then rode, and was recorded, as text
    that differed from the file on disk. Both legs read raw here and
    `wire.decode_envelope` rules on the physical form: LF passes, every other
    form refuses before anything is parsed.

    Every envelope reader that takes a path or `-` from a person goes through
    this one function, so the domain has one door rather than one per verb.
    """
    reference = transport.parse_round_reference(arg)
    if reference is not None:
        # `git:<lineage>/<round>` — a `git` round's envelope, fetched from
        # the ref the other side pushed it to. The LEG is the verb's, not
        # the argument's: `take` reads a request, `close --verdict` and
        # `respond --verdict` read a verdict. That is why the reference
        # names a round rather than a ref — a person carries one word, and
        # the verb they are running says which envelope of that round it
        # means.
        lineage, round_no = reference
        if cfg is None or kind is None:
            raise transport.Refusal(
                f"{arg} names a round, and this verb reads an envelope from "
                f"a file or standard input",
                "", remedy="a person passes the envelope's path, or `-` to "
                           "read it from standard input")
        return transport.fetch_envelope(cfg, lineage, round_no, kind)
    if arg == "-":
        return wire.decode_envelope(sys.stdin.buffer.read(), arg)
    return wire.decode_envelope(Path(arg).read_bytes(), arg)


def cmd_take(args, cfg) -> int:
    """Reviewer side, one verb: validate, probe, record, print the envelope
    and the exact diff command — then the reviewer rules and stops."""
    ledger = _ledger(cfg, args)
    # `git:<lineage>/<round>` fetches the request from its ref; a path and
    # `-` read as they always did. The reference is parsed a second time
    # here (past `_read_envelope`'s own parse) because F1's fix needs the
    # CARRIED lineage itself, not just the bytes it names — a fresh
    # reviewer ledger has no other way to learn it.
    carried_reference = transport.parse_round_reference(args.envelope)
    carried_lineage = carried_reference[0] if carried_reference else None
    envelope = _read_envelope(args.envelope, cfg, "request")
    # The cap in force is a fact of the lineage this take records into, and
    # `take_lineage` is the one rule that decides which that is. Round-2 F5:
    # the same three inputs `transport.take` gives it, so the cap and the
    # record cannot be computed against two different reviews — the carried
    # reference, the id the envelope stamps, and the author branch it names.
    parsed_request = wire.parse_request(envelope)
    try:
        take_lineage = transport.take_lineage(
            ledger, parsed_request.sha or "", carried_lineage,
            stamped=transport.stamped_lineage(parsed_request),
            branch=known_branch(parsed_request.attrs.get("branch")))
    except transport.Refusal as exc:
        return _blocked(exc.next_cmd, str(exc), remedy=exc.remedy)
    try:
        rec = transport.take(
            cfg, ledger, envelope, args.envelope, reviewer=args.as_,
            fetch=not args.no_fetch, transport=args.transport,
            lineage=carried_lineage,
            # Sweep F6: `governing` is the target commit's own config,
            # resolved by `take` after the fetch — not this checkout's.
            validate_items=lambda parsed, governing: validate_request(
                parsed, governing,
                round_cap=ledger.effective_round_cap(governing.round_cap,
                                                     take_lineage)))
    except transport.Refusal as exc:
        # The items come from the refusal because `take` already computed
        # them under the authority that governs them — the envelope's own
        # grammar before the fetch, the TARGET commit's config after it.
        # Handing back a command that recomputes them under this checkout's
        # config is the defect this carries the diagnosis to avoid.
        return _blocked(exc.next_cmd, str(exc), remedy=exc.remedy,
                        extra=({"items": [i.as_dict() for i in exc.items]}
                               if exc.items else None),
                        lead="\n".join(f"{i.level}: [{i.code}] {i.message}"
                                        for i in exc.items))
    rec["ok"] = True
    # F3: `decide` is already `take`'s own — built from the TARGET commit's
    # config, the same authority `validate_items` judged the request
    # against, not this checkout's. Overwriting it with `_decide(cfg, …)`
    # here is exactly the split the finding names: an empty or older
    # reviewer checkout would report the target's own declared keys as
    # undeclared.
    refs = "\n".join(f"  {r['status']:<40} {r['path']}"
                      for r in rec["references"]) or "  (none declared)"
    rec["brief"] = brief.request_precis(wire.parse_request(envelope), ledger)
    # Round-1 F4: the agreement was returned in JSON and rendered nowhere a
    # human looks. A report-not-refuse decision rests on the human SEEING
    # the difference; a field only the non-TTY path carries cannot support
    # a decision the visible command never names.
    # Tool feedback, pilot rounds 1 to 3 (brief
    # `loupe-tool-feedback-pilot-2026-09`): the default is the request with
    # its attestation objects as a table, and the verbatim bytes are one
    # flag or one path away — `--full`, or `kept`, which `take` always
    # wrote. The payload never carries both: `envelope` means the exact
    # bytes and nothing else, so a consumer that pipes it onward is never
    # handed a rendering under that name.
    verbatim = rec.pop("envelope")
    if args.full:
        rec["envelope"] = shown = verbatim
    else:
        rec["request_view"] = shown = brief.compact_request(
            verbatim, kept=rec.get("kept"), tags=accepted_tags(cfg))
    _out(rec, f"{shown}\n"
              f"--- taken: round {rec['round']} target {rec['sha']} as "
              f"reviewer {rec['reviewer']}\n\n{rec['brief']}\n\n"
              f"target: {rec['target']}\n"
              f"{render_checkout(rec['head'], rec['sha'])}"
              f"references:\n{refs}\n"
              f"{render_tool_agreement(rec['tool'])}"
              f"diff:   {rec['diff']}\nthen:   {rec['then']}")
    return EXIT_OK


def render_checkout(checkout: dict, sha: str) -> str:
    """The reviewer's HEAD beside the target, all three states printed.

    `at-target` prints too, for the reason `render_tool_agreement` gives: a
    line that appears only when something is off teaches nothing about what
    its absence means.
    """
    state = checkout.get("state")
    tree = checkout.get("tree") or "tree state unknown"
    if state == transport.CHECKOUT_AT_TARGET:
        return (f"head:   AT the target — this checkout is {sha[:12]} "
                f"({tree})\n")
    if state == transport.CHECKOUT_ELSEWHERE:
        return (f"head:   NOT the target — this checkout is "
                f"{str(checkout.get('sha'))[:12]} ({tree}), the target is "
                f"{sha[:12]}\n        files read from this working tree are "
                f"not the reviewed bytes; the diff command below is\n")
    return ("head:   UNKNOWN — this checkout has no HEAD to compare (empty "
            "or unborn clone); read the target through the diff command\n")


def render_tool_agreement(agreement: dict) -> str:
    """The tool-identity report, for the eyes the decision belongs to.

    All three states are printed, `match` included: a line that appears
    only on disagreement teaches the reader nothing about what its absence
    means, and this round's own record is that an absence gets read as
    agreement. One line when the installations agree, and the reason beside
    it when they do not (round-1 F4).
    """
    state = agreement.get("agreement")
    if state == "match":
        head = (f"tool:   match — both ends carry the declared behavioural "
                f"set {agreement['reader']}\n")
    elif state == "unstamped":
        head = (f"tool:   UNSTAMPED — this end carries "
                f"{agreement['reader']}; the envelope declares no set\n"
                f"        {agreement['note']}\n")
    else:
        head = (f"tool:   DIFFERS — this end carries {agreement['reader']}, "
                f"the envelope was written under {agreement['writer']}\n"
                f"        {agreement['note']}\n")
    return head + _render_shape(agreement)


def _render_shape(agreement: dict) -> str:
    """The install-shape line: REPORTED, never a verdict (RVW-T18).

    It renders on its own line, below the behavioural one, and it never
    says `match`, `differs` or anything else that reads as a judgement —
    because it is not one. A wheel install and a clone of the same code
    carry different shapes by construction, so equality here is not a
    property worth asserting and inequality is not a fault. What the line
    is for is the one thing the behavioural identity genuinely cannot see:
    `bin/loupe` decides what runs and exports the environment stash every
    gate subprocess inherits, so a shim that moved is a fact a human may
    want, even though no reader should refuse on it.

    Omitted entirely when this end has no shape to report AND the envelope
    declared none — an envelope predating the split says nothing here, and
    a line saying nothing is worse than no line.
    """
    mine = agreement.get("reader_shape")
    theirs = agreement.get("writer_shape")
    if not mine and not theirs:
        return ""
    if not theirs:
        return (f"shape:  this end {mine}; the envelope declares none "
                f"(reported, never compared)\n")
    return (f"shape:  this end {mine}, the envelope {theirs} "
            f"(reported, never compared)\n")


def cmd_waive(args, cfg) -> int:
    """Record that a commit was deliberately not reviewed.

    The verb exists because skipping was already possible and unrecordable.
    See `transport.waive` for why an absence was the wrong way to store a
    decision.
    """
    ledger = _ledger(cfg, args)
    if getattr(args, "finding", None):
        # A finding waiver is an answer WITHIN a review, so it takes the
        # lineage this branch is on. The commit waiver below stays global.
        try:
            waiver_lineage = _write_lineage(ledger, cfg, "waive --finding")
        except LineageUnchoosable as exc:
            return _blocked("", str(exc), remedy=exc.remedy)
        try:
            rec = transport.waive_finding(
                cfg, ledger, args.finding, args.reason or "", args.by,
                waiver_lineage,
                destination=getattr(args, "destination", None) or "",
                trigger=getattr(args, "trigger", None) or "")
        except transport.Refusal as exc:
            return _blocked(exc.next_cmd, str(exc), remedy=exc.remedy)
        rec["ok"] = True
        where = (f"; parked at {rec['destination']}"
                 + (f", back when {rec['trigger']}" if rec.get("trigger")
                    else "")
                 if rec.get("destination") else "")
        asked = ("answering the author's escalation"
                 if rec["answers_escalation"]
                 else "overruled without being escalated first")
        _out(rec, f"{rec['finding_id']} ({rec['fp']}) stands unfixed by "
                  f"decision of {rec['authorized_by']}, {asked}: "
                  f"{rec['reason']}{where}\n"
                  f"recorded as the authority's answer — not an acceptance, "
                  f"and not a reviewer withdrawal\n"
                  f"next: {rec['next']}")
        return EXIT_OK
    try:
        rec = transport.waive(cfg, ledger, args.sha, args.reason or "",
                              args.by)
    except transport.Refusal as exc:
        return _blocked(exc.next_cmd, str(exc), remedy=exc.remedy)
    rec["ok"] = True
    _out(rec, f"waived {rec['sha'][:12]}: {rec['reason']}\n"
              f"authorized by {rec['authorized_by']}; recorded in the ledger "
              f"as a decision, which is what makes it different from silence")
    return EXIT_OK


def cmd_authorize_advance(args, cfg) -> int:
    """A named human advances the lineage over findings they overruled."""
    ledger = _ledger(cfg, args)
    # The third writer of the terminal marker (lineage 27 round 2 F1). The
    # round-1 fix reserved `handoff` and `close` and called the lifecycle
    # covered; it is not. `authorize_advance` reads `open_round` and then
    # appends `lineage_closed` — the same shared lifecycle and the same
    # terminal marker `close` takes the reservation for — so an advance
    # that merely OVERLAPS an author's next handoff ends the lineage over
    # a round being opened beside it: the request lands after the read, is
    # swallowed by the closure, and the closure records `open_request:
    # False` about it. `Ledger.events` caches its first read, so the
    # handoff whose gates span the advance then finishes from its cached
    # previous-lineage state. No forged state and no weaker-access
    # adversary — two legitimate operator actions and a scheduling error,
    # which is exactly what a reservation is for.
    #
    # Taken before the FIRST lifecycle read (`transport.authorize_advance`
    # opens with `ledger.open_round()`), released after the record, on
    # every path — `finally`, like the other two.
    #
    # What it does NOT do: grant anything. Holding the reservation excludes
    # a concurrent lifecycle writer and decides nothing else; every refusal
    # below it stands untouched, and the advance remains a NAMED human's
    # recorded decision — `--reason` and `--by` still required, the pending
    # round still refused, the standing findings still each answered by a
    # waiver. Exclusion is not authority.
    try:
        lineage = _write_lineage(ledger, cfg, "authorize-advance")
    except LineageUnchoosable as exc:
        return _blocked("", str(exc), remedy=exc.remedy)
    reservation = transport.LineageReservation(
        ledger, branch=transport.current_branch(cfg),
        verb="authorize-advance", lineage=lineage)
    try:
        reservation.acquire()
    except transport.Refusal as exc:
        return _blocked("", str(exc), remedy=exc.remedy)
    try:
        try:
            # Round-2 F2, the third terminal-marker writer: the lifecycle
            # this advance ends is read UNDER the reservation. Selected
            # before it and cached, the lineage could already have been
            # closed by the time the lock was free, and the marker would be
            # appended behind the one already there.
            ledger.reload()
            if ledger.is_closed(lineage):
                return _blocked(
                    "",
                    f"lineage {lineage} was closed while this advance was "
                    f"waiting for its reservation, so there is no open "
                    f"review to advance; nothing was recorded",
                    remedy=f"a person re-reads the record — "
                           f"`{paths.command(*paths.lits(TOOL_NAME, 'brief'))}` "
                           f"— and decides whether a new review is what this "
                           f"decision belongs to; this tool appends nothing "
                           f"behind a terminal marker")
            rec = transport.authorize_advance(cfg, ledger, args.reason or "",
                                              args.by, lineage)
        except transport.Refusal as exc:
            return _blocked(exc.next_cmd, str(exc), remedy=exc.remedy)
        envelope = rec.pop("envelope")
        if args.out:
            Path(args.out).write_text(envelope, encoding="utf-8")
            rec["out"] = args.out
        rec["ok"] = True
        _out(rec, f"lineage {rec['lineage']} advanced at {rec['sha'][:12]} by "
                  f"{rec['authorized_by']}: {rec['reason']}\n"
                  f"over {len(rec['waived'])} overruled finding(s): "
                  f"{', '.join(rec['waived'])}\n"
                  f"kept at {paths.display_path(rec['kept'])} — an "
                  f"authorization, NOT a clean verdict, and it says so "
                  f"wherever it is carried")
        return EXIT_OK
    finally:
        reservation.release()


def cmd_close(args, cfg) -> int:
    """Author side: ingest the verdict and close the round; a clean verdict
    closes the lineage; --lineage closes one by recorded decision."""
    ledger = _ledger(cfg, args)
    # WHICH LINEAGE THIS CLOSE ACTS ON (brief `keyed-lineage`, item 3).
    # `--verdict` resolves it from the envelope's SHA, through the request
    # that carries it — the envelope decides, not the checkout. `--lineage`
    # is given no envelope and closes the review this branch is on. Neither
    # may guess: a close appends the terminal marker, and appending it to
    # the wrong review discards a round nobody ruled.
    #
    # Resolved BEFORE the reservation, because the reservation is keyed on
    # it: `lineage-<id>.lock` excludes the commands acting on this same
    # review and no longer excludes a second worktree running a different
    # one.
    # Round-2 F3: the verdict is read EXACTLY ONCE, here, and the same bytes
    # resolve the lineage and are recorded. Two reads consumed the supported
    # `--verdict -` outright — the paste relay's own generated close command
    # is a heredoc, so the preliminary read drained stdin and the real read
    # found an empty document — and on a file or a fetched ref they let
    # selection and processing see different bytes. A failed capture is kept
    # as the exception it was and re-raised at the point the second read
    # used to raise, so a malformed envelope refuses exactly as it did.
    verdict_text, verdict_error, verdict_sha = None, None, None
    carried_lineage = None
    if not args.lineage and args.verdict:
        reference = transport.parse_round_reference(args.verdict)
        carried_lineage = reference[0] if reference else None
        try:
            verdict_text = _read_envelope(args.verdict, cfg, "verdict")
        except (transport.Refusal, OSError, ValueError) as exc:
            verdict_error = exc     # re-raised for real below
        if verdict_text is not None:
            try:
                verdict_sha = wire.parse_verdict(verdict_text).sha
            except (ValueError, KeyError, TypeError):
                verdict_sha = None
    branch = transport.current_branch(cfg)
    try:
        lineage = _branch_lineage(ledger, cfg, "close", branch=branch) \
            if not verdict_sha else _write_lineage(ledger, cfg, "close",
                                                   verdict_sha,
                                                   carried=carried_lineage)
    except LineageUnchoosable as exc:
        return _blocked("", str(exc), remedy=exc.remedy)
    # The same reservation the handoff takes, around close's own
    # read-check-write (lineage 27 round 1 F1). Both closes are inside it:
    # `--verdict` refuses a verdict that would swallow an unruled request,
    # and `--lineage` appends the marker that ends the lineage — each reads
    # the ledger and then writes to it, so a close running beside an
    # admission INTO THE SAME LINEAGE could slip between that admission and
    # its record and rule on, or close over, a round the ledger does not
    # yet show. Refused, not queued: the human retries once the holder
    # finishes.
    #
    # When this branch names no lineage the reservation is the BRANCH's
    # opening lock, not nothing: a close arriving while this branch's first
    # handoff is mid-admission must be told the reservation is held, not
    # told the branch has no review — the review is one `record_handoff`
    # away from existing, and the marker this verb appends would land in
    # front of it.
    # Round-2 F2: what this close SELECTED, so the re-read under the
    # reservation can tell the transition apart from the state. Closing a
    # round into a lineage that was already closed when this command started
    # is whatever it always was — the refusals inside `close_round` rule on
    # it. What is new is a lineage that was OPEN here and is closed by the
    # time the reservation is granted.
    selected_open = lineage is not None and not ledger.is_closed(lineage)
    reservation = (
        transport.LineageReservation(ledger, branch=branch, verb="close",
                                     lineage=lineage) if lineage
        else transport.NewLineageReservation(ledger, branch=branch,
                                             verb="close"))
    try:
        reservation.acquire()
    except transport.Refusal as exc:
        return _blocked("", str(exc), remedy=exc.remedy)
    try:
        try:
            # Round-2 F2: the AUTHORITATIVE lifecycle snapshot is the one
            # taken under the reservation, on every path — not only the
            # `lineage is None` one. `Ledger.events` caches its first disk
            # read, so a close whose selection predates the lock was ruling
            # on state that another process had already moved.
            ledger.reload()
            if lineage is None:
                # Re-read under the reservation, then refuse for real: the
                # admission that was in flight has finished by now, so this
                # says what the record says rather than what it said before
                # the lock was free.
                lineage = _write_lineage(ledger, cfg, "close", verdict_sha,
                                         carried=carried_lineage)
            elif selected_open and ledger.is_closed(lineage):
                # The terminal marker is already there. Whatever this close
                # was going to append — a verdict, or a second closure —
                # would land behind it, in a review that has ended.
                return _blocked(
                    "",
                    f"lineage {lineage} was closed while this close was "
                    f"waiting for its reservation, so there is no open "
                    f"review here to close; nothing was recorded",
                    remedy=f"a person re-reads the record — "
                           f"`{paths.command(*paths.lits(TOOL_NAME, 'brief'))}` "
                           f"— and decides whether this ruling belongs to a "
                           f"review that is still open; this tool appends "
                           f"nothing behind a terminal marker")
            if args.lineage:
                rec = transport.close_lineage(ledger, args.reason or "",
                                              args.by or "", lineage)
                rec["ok"] = True
                _out(rec, f"lineage {rec['lineage']} closed at round "
                          f"{rec['lineage_closed_at_round']}"
                          + (" (with a request still open)"
                             if rec["open_request"] else "")
                          + f"\nnext: {rec['next']}")
                return EXIT_OK
            if not args.verdict:
                return _usage_exit(
                    paths.command(*paths.lits(TOOL_NAME, "close", "--help")),
                    "close needs --verdict or --lineage",
                    remedy="a person chooses which close this is — a verdict to "
                           "record, or a lineage to end by decision — and "
                           "supplies its flag")
            # Round-2 F3: the ONE capture, not a second read. `raise` here
            # is where the second read used to raise, so the refusal a
            # malformed or unreachable envelope gets is byte-for-byte the
            # one it got before — while a stdin envelope, which a second
            # read could only find empty, is still in hand.
            if verdict_error is not None:
                raise verdict_error
            text = verdict_text
            parsed_verdict = wire.parse_verdict(text)
            # Round 2 F2: the recorded transport is resolved and validated
            # BEFORE close_round writes anything. Resolved after, a defective
            # record refused the round only once the verdict — and for a clean
            # ruling the lineage closure — was already appended, and the
            # refusal escaped as generic usage help that cannot repair an
            # append-only ledger; and a CLEAN close emptied `current()` first,
            # so a recorded `paste` silently became the default carrier. The
            # value is read once, here, and carried across the closure.
            carrier = transport.recorded_transport(ledger,
                                                   parsed_verdict.sha,
                                                   lineage)
            # Read BEFORE the close: the round's envelopes ride the lineage
            # the round was CARRIED on, which is this lineage for everything
            # written since the key existed and the pre-keying `take` stamp
            # for a round taken over a reference before it.
            carrier_lineage = (ledger.carrier_lineage_for_sha(
                parsed_verdict.sha, prefer=lineage) or lineage)
            # Audit of 2026-09-05, finding 3. The verdict is judged under the
            # TARGET commit's own review.toml — the authority `take` read and
            # `validate --from-target` judged it by — never under whatever this
            # checkout holds now. A checkout that had since renamed a severity
            # rejected a verdict its target had already accepted, with
            # V-SEVERITY, and the recovery it printed omitted `--from-target`.
            # An accepted artifact's acceptance cannot depend on unrelated
            # checkout changes; the author's clone always holds the target,
            # since it pushed it.
            governing = transport.governing_for(cfg, parsed_verdict.sha)
            rec = transport.close_round(
                cfg, ledger, text, args.verdict, lineage,
                round_no=args.round, tokens=args.tokens,
                validate_items=lambda parsed: validate_verdict(
                    parsed, governing,
                    answering=_dispositions_answered(ledger, parsed,
                                                     lineage)))
        except LineageUnchoosable as exc:
            return _blocked("", str(exc), remedy=exc.remedy)
        except transport.Refusal as exc:
            return _blocked(exc.next_cmd, str(exc), remedy=exc.remedy)
        rec["ok"] = True
        rec["brief"] = brief.verdict_precis(parsed_verdict, source=args.verdict)
        rec["relay"] = brief.verdict_relay(
            parsed_verdict, source=args.verdict, transport=carrier,
            reference=_round_reference(carrier, rec.get("round"),
                                       carrier_lineage))
        _out(rec, f"round {rec['round']} closed: {rec['verdict']} on {rec['sha']} "
                  f"({rec['findings']} findings, {rec['closures']} closures), "
                  f"kept at {paths.display_path(rec['kept'])}\n"
                  f"lineage: {rec['lineage']}\n\n"
                  f"{rec['brief']}\n\n{rec['relay']}")
        return EXIT_OK
    finally:
        reservation.release()


def cmd_prune(args, cfg) -> int:
    """State maintenance, not lifecycle: executes the retention policy whose
    hard lines are in `transport.prune_gate_output`'s docstring — the ledger
    and `exchange/` are never touched, only gate output no ledger event
    references is removed."""
    ledger = _ledger(cfg, args)
    try:
        result = transport.prune_gate_output(cfg, ledger,
                                             dry_run=args.dry_run)
    except transport.Refusal as exc:
        return _blocked(exc.next_cmd, str(exc),
                        remedy="nothing is retained where no state "
                               "directory exists; there is nothing a "
                               "person needs to do")
    result["ok"] = True
    verb = "would remove" if result["dry_run"] else "removed"
    _out(result,
         f"{verb} {len(result['pruned'])} unreferenced gate-output "
         f"director{'y' if len(result['pruned']) == 1 else 'ies'} "
         f"({result['bytes_freed']} bytes); kept {result['kept']} — the "
         f"ledger and exchange/ are never pruned")
    return EXIT_OK


def cmd_brief(args, cfg) -> int:
    """Anyone's verb: what does this envelope ask or rule, in prose, and what
    exactly do I carry? With no argument it finds the open request itself.

    Deliberately available to either side and at any time. The précis is
    derived, so running it changes nothing and records nothing — it is the one
    verb that is safe to run when you are not sure what state the loop is in.
    """
    ledger = _ledger(cfg, args)
    source, superseded, text = args.envelope, 0, None
    # Round-2 F3: ONE capture of the source, and the same bytes resolve the
    # lineage and are summarised. `brief -` is how a request or a verdict
    # arrives from a paste relay, and the preliminary read consumed it —
    # `- is neither a review request nor a verdict`, about a document that
    # was both. A failed capture is kept and re-raised where the second read
    # used to raise it.
    captured, capture_error, carried_lineage = None, None, None
    if args.envelope:
        reference = transport.parse_round_reference(args.envelope)
        carried_lineage = reference[0] if reference else None
        try:
            captured = _read_envelope(args.envelope, cfg, "request")
        except (transport.Refusal, OSError, ValueError) as exc:
            capture_error = exc
    # The lineage this brief is about. With an envelope in hand it is the
    # one that envelope's SHA resolves to WITHIN the review this invocation
    # carries; with none, it is the open lineage this worktree's branch is
    # on (brief `keyed-lineage`, item 3).
    try:
        lineage = _read_lineage(ledger, cfg, "brief", _envelope_sha(captured),
                                carried=carried_lineage
                                or _envelope_lineage(captured))
    except LineageUnchoosable as exc:
        return _blocked("", str(exc), remedy=exc.remedy)
    # Every OTHER open lineage, named on the face of this brief: with
    # several reviews live in one repository, "the open request" is a claim
    # about one of them and a reader is owed the rest.
    elsewhere = [
        {"lineage": l, "branch": ledger.lineage_branch(l) or None,
         "round": ledger.open_round(l),
         "state": ("awaiting a verdict" if ledger.open_round(l) is not None
                   else "ruled, awaiting the author")}
        for l in ledger.open_lineages() if l != lineage]
    elsewhere_text = ("\n\nAlso open in this repository: "
                      + "; ".join(
                          f"lineage {o['lineage']} on "
                          f"{o['branch'] or 'an unrecorded branch'}, round "
                          f"{o['round'] if o['round'] is not None else '—'} "
                          f"({o['state']})" for o in elsewhere)
                      if elsewhere else "")
    if source is None:
        event, superseded = brief.find_open_request(ledger, lineage)
        if event is None:
            # The third state, named because it is real and the sentence
            # that omitted it was false (brief `concurrent-round-refusal`,
            # change 4). A round emitted from another worktree and thrown
            # away when the lineage was closed at another SHA is neither
            # "nothing emitted" nor "already ruled", and the worktree that
            # emitted it was told both. A keyed lineage makes the state
            # unreachable going forward — a handoff on another branch opens
            # its own review — and ledgers already carrying it must still
            # read truthfully.
            closed = ledger.last_closed_lineage(
                transport.current_branch(cfg))
            discarded = (ledger.discarded_requests(closed) if closed
                         else [])
            emit_cmd = paths.command(
                *paths.lits(TOOL_NAME, 'handoff', '--claim-file'),
                paths.Ph('<their claim file>'))
            if discarded:
                named = "; ".join(
                    f"round {e.get('round', '?')} for "
                    f"{str(e.get('sha') or '?')[:12]}"
                    + (f" on {b}"
                       if (b := known_branch(e.get("branch")))
                       else "")
                    for e in discarded)
                return _blocked(
                    "",
                    f"no open review request in this lineage — and the "
                    f"previous one was closed with {len(discarded)} request "
                    f"still unruled: {named}. That round was emitted and "
                    f"then discarded by the close, so it carries no verdict "
                    f"and never will{elsewhere_text}",
                    remedy=f"a person decides whether that work is re-opened: "
                           f"the discarded round is emitted again into this "
                           f"lineage with "
                           f"`{emit_cmd}`, or it is left as the record shows "
                           f"it — abandoned. This tool re-opens nothing on "
                           f"its own")
            return _blocked(
                "",
                "no open review request in this lineage: either nothing has "
                "been emitted yet, or every emitted round already carries a "
                "verdict" + elsewhere_text,
                remedy=f"a person writes the claim and emits a round with "
                       f"`{emit_cmd}`; "
                       f"there is nothing to summarise until one is "
                       f"open, and the claim is the author's judgment, which "
                       f"this tool carries and never invents")
        # Lineage 25 round 1 F4: the checked read. A copy that does not
        # reproduce the open event's recorded digest — a pre-upgrade flat
        # copy of an EARLIER lineage's round 1, say — is not the open
        # request, and a brief derived from it would relay the wrong
        # target with a live command under it.
        digest = event.get("source_digest") or ""
        path, text = transport.read_kept(
            cfg, event["round"], "request",
            lineage=ledger.carrier_lineage_for_sha(event.get("sha"),
                                                  prefer=lineage) or lineage,
            digest=digest)
        if path is None:
            return _blocked(
                "",
                f"round {event['round']} is open but its bytes were not kept, "
                f"so there is nothing to summarise",
                remedy=f"a person passes the envelope itself — "
                       f"`{paths.command(*paths.lits(TOOL_NAME, 'brief'), paths.Ph('<the path they hold>'))}` — since the copy this "
                       f"tool kept is gone and only the person who has the "
                       f"bytes knows where they are")
        if text is None:
            return _blocked(
                "",
                f"round {event['round']} is open, but the retained copy at "
                f"{paths.display_path(str(path))} does not reproduce the "
                f"recorded digest {digest[:12]}… — it is another envelope "
                f"(a rewritten copy, or a flat pre-0.18 copy of an earlier "
                f"lineage's round {event['round']}), so it is not summarised",
                remedy=f"a person passes the envelope itself — "
                       f"`{paths.command(*paths.lits(TOOL_NAME, 'brief'), paths.Ph('<the path they hold>'))}` — or restores "
                       f"the copy the ledger digested; the tool rewrites "
                       f"and migrates no retained bytes")
        source = str(path)

    if text is None:
        # Round-2 F3: the captured bytes, never a second read of a source
        # that may be standard input, a mutable file or a moving ref.
        if capture_error is not None:
            raise capture_error
        text = captured
    as_request = wire.parse_request(text)
    if as_request.wrapped:
        # R1-F2: `brief` is an official relay reader, so a wrapped-looking
        # request is not yet a summarisable one — the structural grammar
        # rules first, BEFORE any brief or relay is rendered. Without this,
        # a defective wrapper (a transport stamp the grammar refuses, a
        # missing role) still produced a live `take` command pointing at a
        # kept path nobody declared reachable.
        items = validate_request(as_request, cfg, structural_only=True)
        if errors_in(items):
            return _finish(
                items, "",
                remedy="the author re-emits the request: a brief cannot "
                       "summarise, and a relay cannot carry, an envelope "
                       "whose own grammar refuses it")
        # Round-2 F2: `brief` RENDERS the command a human carries, which is
        # precisely the output a stale installation gets wrong — it is the
        # incident this mechanism exists to expose. It compares before it
        # renders, like every other cross-installation reader.
        agreement = transport.tool_agreement(as_request)
        # Round-3 F2: an envelope no heredoc can carry byte-identically
        # refuses before a relay is rendered, on both branches below.
        try:
            relay_text = brief.relay(
                source, as_request, text, paste=args.paste,
                reference=_round_reference(
                    transport.declared_transport(as_request),
                    as_request.attrs.get("round"),
                    ledger.carrier_lineage_for_sha(as_request.sha,
                                                   prefer=lineage)
                    or lineage))
        except brief.UnrelayableEnvelope as exc:
            return _blocked("", f"{source}: {exc}",
                            remedy=f"a person appends the terminal newline "
                                   f"to {paths.display_path(source)} and "
                                   f"re-runs this command")
        # Ruling 1 of 2026-09-06: the cross-lineage notice, in `brief` as
        # well as on the envelope's face — and the other open lineages, so
        # "the open request" is not read as "the only one".
        notices = ledger.cross_lineage_notices(lineage)
        rec = {"kind": "request", "source": source, "ok": True,
               "decide": _decide(cfg),
               "round": as_request.attrs.get("round"),
               "sha": as_request.sha, "superseded": superseded,
               "lineage": lineage, "open_lineages": elsewhere,
               "cross_lineage": notices,
               "tool": agreement,
               "brief": brief.request_precis(as_request, ledger,
                                             full=args.full),
               "relay": relay_text}
        note = (f"\nNOTE: {superseded} earlier emission(s) of this round are "
                f"superseded; this is the live one.\n" if superseded else "")
        _out(rec, f"{rec['brief']}\n{note}"
                  f"{render_cross_lineage_md(notices)}"
                  f"{elsewhere_text}\n\n"
                  f"{render_tool_agreement(agreement)}\n{rec['relay']}")
        return EXIT_OK

    as_verdict = wire.parse_verdict(text)
    if as_verdict.wrapped:
        # Two fields, exactly as the request side has always had: the account
        # a human reads, and the commands a human copies (round 3 relay
        # split). Never one blob for a caller to divide by guesswork.
        try:
            carrier = transport.recorded_transport(ledger, as_verdict.sha,
                                                   lineage)
            # F1: same authority as the push in `cmd_validate` — the
            # round's carried lineage, so a `brief` rendered on either
            # machine names the same reference.
            brief_lineage = (ledger.carrier_lineage_for_sha(
                as_verdict.sha, prefer=lineage) or lineage)
            relay_text = brief.verdict_relay(
                as_verdict, source=source, transport=carrier,
                reference=_round_reference(
                    carrier, ledger.round_for_sha(as_verdict.sha, lineage),
                    brief_lineage),
                envelope=text)
        except brief.UnrelayableEnvelope as exc:
            return _blocked("", f"{source}: {exc}",
                            remedy=f"a person appends the terminal newline "
                                   f"to {paths.display_path(source)} and "
                                   f"re-runs this command")
        rec = {"kind": "verdict", "source": source, "ok": True,
               "decide": _decide(cfg), "sha": as_verdict.sha,
               "lineage": lineage, "open_lineages": elsewhere,
               "brief": brief.verdict_precis(as_verdict, source=source,
                                             full=args.full),
               "relay": relay_text}
        _out(rec, f"{rec['brief']}\n\n{rec['relay']}")
        return EXIT_OK

    return _blocked(paths.command(*paths.lits(TOOL_NAME, "validate"),
                                  source),
                    f"{source} is neither a review request nor a verdict")


def _decide_text(entries: list, cfg) -> str:
    """The `decide` list for a person: one block per key, with the section
    the printed line belongs under. The JSON is the contract; this is the
    same entries read aloud, and it derives every value from them."""
    if not entries:
        return (f"this repository declares every optional key "
                f"({cfg.source}); there is nothing left to decide")
    lines = [f"{len(entries)} key(s) this repository never declared "
             f"({cfg.source}). Each `set`/`unset` line below is written "
             f"verbatim into {config.CONFIG_BASENAME}, under the section "
             f"named beside it.", ""]
    for entry in entries:
        section = entry["key"].rpartition(".")[0]
        lines.append(f"{entry['key']} — under [{section}]")
        lines.append(f"  {entry['meaning']}")
        # The values as the JSON prints them: this text is the same result
        # read aloud, and `False`/`None` are a second spelling of a payload
        # a reader may also be parsing.
        lines.append(f"  applied: {json.dumps(entry['applied'])}")
        lines.append(f"  set:     {entry['set'] or '(no line declares it)'}")
        lines.append(f"  unset:   {entry['unset'] or '(this key has no off '
                                                     'state)'}")
        lines.append("")
    lines.append(f"`{vocab.DECIDE_TRANSPORT}` reports what an emission from "
                 f"this environment would resolve now — the same resolution "
                 f"`handoff` makes — not a value anyone declared.")
    return "\n".join(lines)


def cmd_decide(args, cfg) -> int:
    """READ what this repository never declared, without emitting anything
    (2026-09-19, the upgrade procedure's step 5).

    The `decide` list has always ridden on results that emit or record
    something — `handoff`, `emit-request`, `take`, `brief`. An operator who
    has just moved a pin wants the same list for a different reason: what
    does the reader I now run ask of my config, and what does it honour? The
    only way to get it was to import the package and call
    `config.load().decisions()` by hand, which is not a procedure step.

    So this verb is a READING, and the shape follows from that word:

      * ONE code path. It calls `_decide` — the same function the emitting
        verbs call — so the entries are the emitting verbs' entries and
        cannot drift from them. Re-deriving the list here would make the
        answer to "what will `handoff` ask me?" a second implementation of
        `handoff`'s question, which is the defect this verb exists to
        retire.
      * Exit 0 whether the list is empty or not. An empty list is the good
        state and a full one is a question, and neither is a finding: a
        reading reports, it does not rule.
      * It writes NOTHING — no ledger event, no kept bytes, no lock, no
        state directory. It never touches `_ledger`, which is what every
        other verb here reaches for; there is no lineage to read, because
        the config's silence is a fact about the repository rather than
        about any round. That is also what lets it run outside a lineage,
        with no ledger at all, on a detached HEAD and from a subdirectory:
        `config.load` resolves the root, and nothing below it asks git
        anything.

    `roles.transport` is the one entry with a choice in it. The emitting
    verbs report the transport they RESOLVED for the round they are about to
    open; a reading opens no round, so there is no such value. Reporting the
    table's built-in `path` would be a guess printed as a declaration — an
    environment declaring `paste` would be told `path` is what applies.
    So the verb resolves it exactly as `handoff` does, through
    `emit.resolve_transport`, with no invocation flags because a reading has
    none to give: the answer is what an emission from THIS environment would
    carry now, with the config, environment and provider precedence that
    resolution owns. A declaration outside the closed vocabulary refuses
    there, through the same typed boundary every other reader passes.
    """
    applied = emit.resolve_transport(cfg)
    entries = _decide(cfg, {vocab.DECIDE_TRANSPORT: applied})
    _out({"ok": True, "config": cfg.source, "decide": entries},
         _decide_text(entries, cfg))
    return EXIT_OK


def cmd_migrate_state(args, cfg) -> int:
    """Move state and config written under a FORMER tool name (§10.1, RVW-T6).

    A rename is a migration, not a substitution: without this step the
    renamed tool points at directories that do not exist and silently starts
    fresh ledgers. The move is a directory rename with every ledger digest
    verified across it; both-exist is ambiguous and refuses — the tool never
    guesses which record is the real one.
    """
    home = Path(args.home) if getattr(args, "home", None) else Path.home()
    moved, absent = [], []
    for kind, parent in (("state", home / ".local" / "state"),
                         ("config", home / ".config")):
        for former in FORMER_NAMES:
            src, dst = parent / former, parent / TOOL_NAME
            if not src.is_dir():
                absent.append(f"{kind}: nothing at {src}")
                continue
            if dst.exists():
                return _blocked(
                    "",
                    f"both {src} and {dst} exist: the tool cannot decide "
                    f"which is the record (§10.1)",
                    remedy=f"a person must inspect {src} and {dst}, merge "
                           f"them by hand, and remove the one that is not "
                           f"the record")
            before = {str(p.relative_to(src)): sha256_file(p)
                      for p in sorted(src.rglob("ledger.jsonl"))}
            src.rename(dst)
            after = {rel: sha256_file(dst / rel) for rel in before}
            if before != after:
                return _blocked(
                    "",
                    f"ledger digests changed across the move of {src}",
                    remedy=f"a person must restore from {dst} by hand: the "
                           f"rename moved bytes that no longer digest "
                           f"identically, and no command can adjudicate that")
            moved.append({"from": str(src), "to": str(dst),
                          "ledgers": after})
    _out({"ok": True, "moved": moved, "absent": absent},
         "\n".join([f"moved {m['from']} -> {m['to']} "
                    f"({len(m['ledgers'])} ledger(s), digests verified)"
                    for m in moved] +
                   [f"nothing to do — {a}" for a in absent]
                   if (moved or absent) else ["nothing to migrate"]))
    return EXIT_OK


def cmd_render_adapters(args, cfg) -> int:
    """§6.3 / RVW-T10: render the per-agent adapters from one source, check
    that the rendered files are current (a drift gate), install them where
    the agents read them, or check THAT for drift.

    The gate has always guarded the tracked copies only. What an agent
    actually loads is `~/.claude/skills/loupe/SKILL.md` and its Codex twin,
    and nothing wrote them but a person with `cp` — so on 2026-08-19 both
    were found rounds behind the source, advertising a `respond` step with no
    falsification record and a verb table missing `waive`. Rendering a
    procedure nobody reads is not a procedure. `--install` is the deploy the
    gate could never be: it refuses to install adapters that are themselves
    stale, keeps whatever it replaces, and reports every target it touched.
    """
    if args.dir:
        directory = Path(args.dir)
    elif args.check_install or args.install:
        # RVW-T21 D1: these two modes are machine-global (they ask about
        # ~/.claude/skills/, ~/.codex/skills/, not the repository underfoot),
        # so their default source is the package's own adapters/ sibling,
        # never the cwd repo's — that default exists only in the tool's own
        # checkout and failed this check everywhere else. `render` and
        # `--check` are repo-local and keep the cwd-relative default below.
        directory = adapters.machine_global_dir()
        if not directory.is_dir():
            return _blocked(
                "",
                f"no adapters/ directory beside the installed package "
                f"({directory}) — the machine-global install modes do not "
                f"fall back to the current repository's adapters/",
                remedy=f"pass --dir <path to the rendered adapters> "
                       f"explicitly")
    else:
        directory = cfg.repo_root / adapters.ADAPTERS_DIR
    if args.check_install:
        rows = adapters.check_install(directory)
        drift = [r for r in rows if r["status"] != "in_sync"]
        if drift:
            return _blocked(
                paths.command(*paths.lits(TOOL_NAME, "render-adapters",
                                          "--install")),
                "installed adapter(s) differ from the rendered copies: "
                + "; ".join(_drift_line(r) for r in drift))
        _out({"ok": True, "install": rows},
             "\n".join(f"in sync  {r['target']}" for r in rows))
        return EXIT_OK
    if args.install:
        # Never install what the gate would reject: a stale tracked copy
        # deployed everywhere is the drift this verb exists to end, spread
        # rather than fixed.
        stale = adapters.check_all(directory)
        if stale:
            return _blocked(
                paths.command(*paths.lits(TOOL_NAME, "render-adapters")),
                "refusing to install adapters that are themselves stale: "
                + ", ".join(stale))
        keep = cfg.ledger_dir / "replaced-adapters" if cfg.ledger_dir else None
        rows = adapters.install_all(directory, keep_dir=keep)
        rendered = "\n".join(_install_line(r) for r in rows)
        failed = [r for r in rows if r["status"] == "failed"]
        if failed:
            # Every attempted target travels with the refusal, installed and
            # replaced rows included: this verb writes to several places and
            # stops at the first it cannot, so a bare failure message would
            # make a partial deployment indistinguishable from none.
            touched = [r for r in rows
                       if r["status"] in ("installed", "replaced")]
            return _blocked(
                "",
                "could not install: "
                + "; ".join(f"{r['kind']} → {r['target']}: {r['error']}"
                            for r in failed)
                + (f" — {len(touched)} target(s) were already written before "
                   f"this and are listed in `installed`" if touched else ""),
                remedy="a person makes the install target writable (or "
                       "removes what occupies it) and re-runs this command; "
                       "the tool will not choose a different destination for "
                       "an agent's procedure, and any target already written "
                       "is named in the result",
                extra={"installed": rows},
                detail=rendered)
        _out({"ok": True, "installed": rows}, rendered)
        return EXIT_OK
    if args.check:
        stale = adapters.check_all(directory)
        if stale:
            return _blocked(
                paths.command(*paths.lits(TOOL_NAME, "render-adapters")),
                "stale adapter(s): " + ", ".join(stale))
        _out({"ok": True, "checked": [str(directory / rel)
                                      for rel in adapters.OUTPUTS.values()]},
             f"adapters under {directory} are in sync with the source")
        return EXIT_OK
    written = adapters.render_all(directory)
    _out({"ok": True, "written": written,
          "install": {k: v[0] for k, v in adapters.INSTALL.items()}},
         "\n".join(f"rendered {p}" for p in written.values()) + "\ninstall: "
         + "; ".join(f"{k} → {v[0]} ({v[1]})"
                     for k, v in adapters.INSTALL.items()))
    return EXIT_OK


def _drift_line(row: dict) -> str:
    """One `check_install` drift row, rendered for a person: the failing
    SIDE's own path, never the healthy other side (RVW-T21 D1) — a
    `source_*` status points at `source`, everything else (including the
    target-side `unreadable`) points at `target`, exactly as it always did."""
    path = row["source"] if row["status"].startswith("source_") else row["target"]
    return f"{row['kind']} {row['status']} at {path}"


def _install_line(row: dict) -> str:
    """One install row, rendered for a person: what happened to which path,
    and where the bytes it replaced went."""
    line = f"{row['status']:<10} {row['target']}"
    if row.get("kept"):
        line += f"  (replaced {row['replaced_digest'][:12]}, kept at "
        line += f"{row['kept']})"
    if row.get("error"):
        line += f"  — {row['error']}"
    return line


class UsageError(Exception):
    """An argparse usage failure, routed through the next-command contract.

    Round-3 F17: argparse's own error() exits 2 with a diagnosis and no next
    command, bypassing §9bis.3 rule 3. Every non-zero exit must name the next
    command instead of explaining the problem.
    """

    def __init__(self, message: str, prog: str):
        super().__init__(message)
        self.prog = prog


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # noqa: D102 - argparse hook
        raise UsageError(message, self.prog)


class _OnceAction(argparse.Action):
    """Refuse a repeated option instead of applying last-write-wins.

    Lineage-5 round-1 F1: `--author claude --author codex` was admitted as
    the unambiguous stamp `codex` — argparse's ordinary store collapses
    repetition, so neither the repetition nor the discarded actor ever
    reached resolution. A role selector names an identity for an
    append-only record; a repeated one — same value or conflicting, either
    order — is a defect of the invocation, refused with both occurrences
    named, never silently resolved to the last writer.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        current = getattr(namespace, self.dest, None)
        if current is not None:
            raise argparse.ArgumentError(
                self, f"given more than once ({current!r}, then {values!r}): "
                      f"a repeated role selector is refused, never collapsed "
                      f"to the last value")
        setattr(namespace, self.dest, values)


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(
        prog=TOOL_NAME,
        description="Deterministic, agent-neutral review tool (slice 1).")
    p.add_argument("--version", action="version", version=TOOL_VERSION)
    p.add_argument("--ledger-dir", help="override the ledger location "
                   "(default: ~/.local/state/<tool>/<repo-id>/)")
    sub = p.add_subparsers(dest="command", required=True,
                           parser_class=_Parser)

    v = sub.add_parser("validate", help="validate an envelope")
    v.add_argument("--from-target", action="store_true",
                   help="judge the envelope against the TARGET commit's own "
                        "review.toml, read from this clone's object store at "
                        "the SHA the envelope stamps — the same authority "
                        "`take` uses, and the one a reviewer needs when "
                        "their own checkout carries no config. Refuses when "
                        "the target is not present here; without it the "
                        "envelope is judged against this checkout, which is "
                        "a different authority")
    v.add_argument("envelope", help="envelope file, or - for stdin — the "
                                    "same forms `take` and `close` read, so "
                                    "the `next` a refused stdin close names "
                                    "is runnable as printed")
    v.add_argument("--against", help="verdict to check a disposition against")
    v.set_defaults(func=cmd_validate)

    f = sub.add_parser("fingerprint", help="stable finding ids for a verdict")
    f.add_argument("verdict")
    f.set_defaults(func=cmd_fingerprint)

    r = sub.add_parser("respond", help="emit a disposition envelope")
    r.add_argument("--verdict", required=True)
    r.add_argument("--from-json", required=True,
                   help="JSON file of author dispositions, or - for stdin")
    r.add_argument("--out", help="write the envelope here AND record it in "
                                 "the ledger (stdout emission records nothing)")
    r.set_defaults(func=cmd_respond)

    led = sub.add_parser("ledger", help="ledger operations")
    lsub = led.add_subparsers(dest="ledger_command", required=True)
    la = lsub.add_parser("add", help="ingest an envelope into the ledger")
    la.add_argument("envelope")
    la.add_argument("--round", type=int)
    la.add_argument("--tokens", type=int,
                    help="token count for this envelope, from whoever ran the "
                         "model — this tool has no model in any code path and "
                         "cannot measure it (§6.1). Omit and the round is "
                         "recorded as uncounted, which is not zero")
    la.set_defaults(func=cmd_ledger_add)
    lac = lsub.add_parser("authorize-cap",
                          help="raise the round cap for THIS lineage only")
    lac.add_argument("--to", type=int, required=True)
    lac.add_argument("--reason", required=True)
    lac.add_argument("--by", required=True,
                     help="who authorized it; not inferred from silence")
    lac.set_defaults(func=cmd_authorize_cap)
    lab = lsub.add_parser("authorize-breaker",
                          help="record the decision to continue past a fired "
                               "circuit breaker, for THIS lineage only")
    lab.add_argument("--breaker", required=True)
    lab.add_argument("--reason", required=True)
    lab.add_argument("--by", required=True,
                     help="who took the decision; not inferred from silence")
    lab.set_defaults(func=cmd_authorize_breaker)
    lca = lsub.add_parser("correct-actor",
                          help="record that a recorded event's true actor "
                               "differs from what it stamps (or omits), "
                               "without rewriting the append-only record")
    lca.add_argument("--event", action="append",
                     help="uid of a recorded event to correct; repeatable "
                          "for more than one")
    lca.add_argument("--actor", required=True,
                     help="who actually acted, correcting the named event(s)")
    lca.add_argument("--reason", required=True)
    lca.add_argument("--by", required=True,
                     help="who is asserting the correction; not inferred "
                          "from silence")
    lca.set_defaults(func=cmd_ledger_correct_actor)
    lc = lsub.add_parser("convergence",
                         help="is this lineage closing its findings, or "
                              "hunting the same domains?")
    lc.set_defaults(func=cmd_ledger_convergence)
    lr = lsub.add_parser("report", help="breakers + metrics")
    lr.add_argument("--format", choices=("json", "md"))
    lr.set_defaults(func=cmd_ledger_report)

    il = sub.add_parser("import-legacy",
                        help="ingest pre-derived legacy events (JSONL, or - "
                             "for stdin) into the ledger — the §5.2 legacy "
                             "import mechanism; the derivation from a given "
                             "corpus is that corpus owner's script")
    il.add_argument("events", help="JSONL file of ledger events, or -")
    il.add_argument("--source-commit",
                    help="the commit whose tracked bytes every row cites "
                         "(source_path + source_digest); read and hashed "
                         "here, and refused unless a remote-tracking ref "
                         "contains it")
    il.add_argument("--no-fetch", action="store_true",
                    help="do not reach the remote; judge the anchor against "
                         "this clone's remote-tracking refs, which are local "
                         "state — a WEAKER claim, recorded as such")
    il.set_defaults(func=cmd_import_legacy)

    ms = sub.add_parser("migrate-state",
                        help="move state/config written under a former tool "
                             "name, digests verified (§10.1)")
    ms.add_argument("--home", help="override the home directory (tests)")
    ms.set_defaults(func=cmd_migrate_state)

    def emission_flags(sp):
        sp.add_argument("--claim-file",
                        help="JSON with the authored Claim content")
        sp.add_argument("--base", help="override the derived base SHA")
        sp.add_argument("--head", help="override HEAD")
        sp.add_argument("--local-only", action="store_true",
                        help="declare that no remote exists and the target "
                             "is fetchable from no other machine; stamped on "
                             "the envelope, and an error when a remote is "
                             "configured (§9bis.4)")
        sp.add_argument("--allow-outside-scope", action="store_true",
                        help="sweep outstanding paths the claim's "
                             "`scope_paths` does not cover, instead of "
                             "refusing before the commit; the request names "
                             "them on its face. Never overrides the "
                             "fixture-marker refusal")
        sp.add_argument("--transport", choices=list(vocab.TRANSPORTS),
                        action=_OnceAction,
                        help="whether the reviewer shares this filesystem: "
                             "`path` means a kept path is a thing they can "
                             "open, `paste` means bytes are the only carrier. "
                             "Resolution order: this flag, then [roles] "
                             "transport in the config, then the environment's "
                             f"own declaration ({vocab.TRANSPORT_ENV}, set by "
                             "a cloud environment's configuration) or a "
                             "documented cloud provider signal, then the "
                             "undeclared steady case, `path` — the local "
                             "same-machine loop. It is stamped on the "
                             "envelope, recorded, and decides what the "
                             "printed relay says (RVW-T11)")
        debug_group = sp.add_mutually_exclusive_group()
        debug_group.add_argument("--debug", action="store_true",
                        help="stamp this round as a debug round "
                             "(debug=\"tool-feedback\" on the envelope): the "
                             "reviewer is asked to also critique the tool's "
                             "own performance this round — efficiency, cost, "
                             "accuracy — in a `## tool feedback` verdict "
                             "section, recorded at close. Advisory; never a "
                             "gate. Precedence: this flag (either "
                             "direction), then `[roles] debug` in the "
                             "repository's config, then off")
        debug_group.add_argument("--no-debug", action="store_true",
                        dest="no_debug",
                        help="do not stamp this round as a debug round, "
                             "overriding a standing `[roles] debug = true` "
                             "declaration for this invocation only")
        sp.add_argument("--author", action=_OnceAction,
                        help="per-invocation author stamp, selecting WITHIN "
                             "the config's permitted_authors — refused "
                             "outside them, when no list is declared, and "
                             "when the flag is repeated (§4)")
        sp.add_argument("--reviewer", action=_OnceAction,
                        help="per-invocation reviewer stamp, selecting "
                             "WITHIN the config's permitted_reviewers — "
                             "refused outside them, when no list is "
                             "declared, when the flag is repeated, and for "
                             "rejected reviewers (§4)")
        sp.add_argument("--out")

    e = sub.add_parser("emit-request",
                       help="emit the next review request (the lower-level "
                            "half of handoff: no ledger record, no exchange "
                            "copy)")
    emission_flags(e)
    e.set_defaults(func=cmd_emit_request)

    h = sub.add_parser("handoff",
                       help="author: emit, record, keep, stop (§9bis.3)")
    emission_flags(h)
    h.set_defaults(func=cmd_handoff)

    tk = sub.add_parser("take", help="reviewer: fetch and probe the target, "
                                     "validate against the target's own "
                                     "config, check every reference from the "
                                     "target tree, record, print the envelope "
                                     "and diff command; --as <identity> is "
                                     "required")
    tk.add_argument("envelope", help="request envelope file, or - for stdin")
    tk.add_argument("--as", dest="as_",
                    help="REQUIRED, no default. The identity taking this "
                         "review. The tool cannot observe who runs it, so it "
                         "records what you declare and refuses on silence; "
                         "[roles] reviewer names the repository's default "
                         "direction, not who is at the keyboard. Must equal "
                         "the envelope's stamp")
    tk.add_argument("--full", action="store_true",
                    help="print the request verbatim, every attestation "
                         "object included. The default prints the same "
                         "request with those objects as one table; the kept "
                         "file `take` names always holds the exact bytes")
    tk.add_argument("--no-fetch", action="store_true",
                    help="do not run the stamped fetch; still requires the "
                         "target to be present in this clone")
    tk.add_argument("--transport", choices=list(vocab.TRANSPORTS),
                    # R1-F2: the reviewer's correction names a topology for
                    # an append-only record, exactly like the author's
                    # selector — repeated same or conflicting, either
                    # order, is a defect of the invocation, refused with
                    # both occurrences named, never last-write-wins.
                    action=_OnceAction,
                    help="correct what the envelope declares about this "
                         "channel, when the author stamped it wrong: you are "
                         "the side that knows whether you share their "
                         "filesystem. Recorded beside the author's value, and "
                         "it decides what your verdict's relay tells them to "
                         "run (RVW-T11)")
    tk.set_defaults(func=cmd_take)

    br = sub.add_parser("brief",
                        help="say in plain language what an envelope asks or "
                             "rules, and print the exact command or bytes to "
                             "carry it; finds the open request with no "
                             "argument")
    br.add_argument("envelope", nargs="?",
                    help="request or verdict envelope, or - for stdin; "
                         "omitted, the open request in this lineage is found "
                         "from the ledger")
    br.add_argument("--full", action="store_true",
                    help="for a verdict, also print what each finding "
                         "requires — the reviewer's specification, "
                         "written for whoever implements the fix")
    br.add_argument("--paste", action="store_true",
                    help="also print the full envelope bytes, for a reviewer "
                         "on another machine to paste into `take -`")
    br.set_defaults(func=cmd_brief)

    dc = sub.add_parser("decide",
                        help="read what this repository never declared: one "
                             "entry per undeclared optional key, with the "
                             "value applied and the exact line that sets or "
                             "unsets it; records nothing and emits nothing")
    dc.set_defaults(func=cmd_decide)

    w = sub.add_parser("waive", help="record a human decision that something "
                                     "goes unaddressed: a commit that is not "
                                     "reviewed, or a finding that stands")
    what = w.add_mutually_exclusive_group(required=True)
    what.add_argument("--sha",
                      help="the commit that goes unreviewed; resolved here, "
                           "so a waiver cannot name something that does not "
                           "exist")
    what.add_argument("--finding",
                      help="a finding of the current lineage that a human "
                           "decides may stand unfixed, by id (round-scoped) "
                           "or fingerprint. The authority's ANSWER to an "
                           "escalation: the author cannot write it, and the "
                           "reviewer has not changed their mind")
    w.add_argument("--destination",
                   help="with --finding: where the work goes instead, when "
                        "it goes somewhere — a brief, an issue")
    w.add_argument("--trigger",
                   help="with --finding and a destination: what brings it "
                        "back, so parking it is not forgetting it")
    w.add_argument("--reason", required=True,
                   help="REQUIRED and the point of the record: a waiver "
                        "without one says a decision happened and not what "
                        "it was")
    w.add_argument("--by", required=True,
                   help="who authorized it — REQUIRED, and not inferred from "
                        "silence: skipping a review is a human's decision, "
                        "and this verb is run only after that named human "
                        "has taken it")
    w.set_defaults(func=cmd_waive)

    aa = sub.add_parser("authorize-advance",
                        help="a named human advances the lineage over "
                             "findings they have overruled; emits its own "
                             "envelope, never a derived clean verdict")
    aa.add_argument("--reason", required=True,
                    help="REQUIRED: why the change advances with findings "
                         "still open")
    aa.add_argument("--by", required=True,
                    help="who decided — REQUIRED and asserted, never "
                         "observed: this records attribution, not proof that "
                         "a human rather than an agent took the decision")
    aa.add_argument("--out",
                    help="write the authorization envelope here as well as "
                         "keeping it under the state directory")
    aa.set_defaults(func=cmd_authorize_advance)

    c = sub.add_parser("close", help="author: ingest the verdict and close "
                                     "the round; --lineage closes a lineage "
                                     "by recorded decision")
    c.add_argument("--verdict", help="verdict envelope file, or - for stdin")
    c.add_argument("--round", type=int)
    c.add_argument("--tokens", type=int,
                   help="token count for the verdict, from whoever ran the "
                        "model (never measured here)")
    c.add_argument("--lineage", action="store_true")
    c.add_argument("--reason")
    c.add_argument("--by", default=None,
                   help="with --lineage: who took the decision — required "
                        "there, and not inferred from silence")
    c.set_defaults(func=cmd_close)

    pr = sub.add_parser("prune",
                        help="remove retained gate output for commits no "
                             "ledger event references; the ledger and "
                             "exchange/ are never pruned")
    pr.add_argument("--dry-run", action="store_true",
                    help="report exactly what would be removed, remove "
                         "nothing")
    pr.set_defaults(func=cmd_prune)

    ra = sub.add_parser("render-adapters",
                        help="render the per-agent adapters (Claude skill, "
                             "Codex skill, instruction block) from one "
                             "source; --check is a drift gate, --install "
                             "deploys them where the agents read them, "
                             "--check-install reports drift there (§6.3)")
    ra_mode = ra.add_mutually_exclusive_group()
    ra_mode.add_argument("--check", action="store_true",
                         help="the drift gate: are the tracked copies current")
    ra_mode.add_argument("--install", action="store_true",
                         help="deploy the rendered adapters to the paths the "
                              "agents load them from; keeps whatever it "
                              "replaces and names it")
    ra_mode.add_argument("--check-install", action="store_true",
                         help="report drift between the rendered copies and "
                              "the installed ones (not a gate: a machine "
                              "with no install is not a broken build)")
    ra.add_argument("--dir", help="output directory (default: <repo>/adapters "
                                  "for render and --check; the installed "
                                  "package's own adapters/ sibling for "
                                  "--install and --check-install)")
    ra.set_defaults(func=cmd_render_adapters)
    return p


def _usage_exit(next_cmd: str, message: str, remedy: str = "") -> int:
    """Exit 2 with a typed recovery, structured when stdout is not a TTY."""
    return _blocked(next_cmd, message, code=EXIT_USAGE, remedy=remedy)


def main(argv=None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except UsageError as exc:
        # `prog` is a command PREFIX ("loupe validate"), not one word:
        # each of its words is proved shell-inert on its own.
        return _usage_exit(
            paths.command(*[paths.token(w) for w in exc.prog.split()],
                          paths.Lit("--help")),
            str(exc))
    except SystemExit as exc:
        # --help and --version exit 0 having already printed; keep that path.
        code = int(exc.code or 0)
        if code == 0:
            return 0
        return _usage_exit(
            paths.command(*paths.lits(TOOL_NAME, "--help")), "usage error")
    # migrate-state must be reachable while only legacy state exists — it is
    # the command the legacy refusal points at.
    #
    # Round 1 F8: this call used to sit ABOVE the boundary below, so every
    # configuration failure — malformed TOML, an unreadable file, the
    # legacy-state refusal — escaped as a traceback with no `next`. That is
    # the one thing the adapters tell agents they can always rely on, and
    # config failures are what a fresh install hits first.
    try:
        cfg = config.load(ledger_dir=args.ledger_dir,
                          check_legacy=args.command != "migrate-state")
    except config.ConfigError as exc:
        # The error carries its own recovery kind; `_blocked` types the
        # payload from whether a command exists (round 2 F8).
        return _blocked(exc.next_cmd, str(exc), code=exc.code,
                        remedy=exc.remedy)
    except (RuntimeError, ValueError, OSError, KeyError,
            TypeError) as exc:
        # Anything config.load did not anticipate still owes a next command.
        return _usage_exit(
            paths.command(*paths.lits(TOOL_NAME, "--help")), str(exc))
    try:
        return args.func(args, cfg)
    except wire.NoncanonicalLineEndings as exc:
        # Round-4 F1: a physical form this tool cannot carry unchanged is a
        # BLOCKED state, not a usage error — no flag repairs a file's line
        # endings, and the generic `<verb> --help` recovery would have sent
        # an agent looking for one. `remedy` says what a person does.
        return _blocked("", str(exc), remedy=exc.remedy)
    except transport.Refusal as exc:
        # A refusal raised outside a verb's own catch — the envelope
        # readers, which any verb may reach through `_read_envelope`. It
        # carries its own recovery, and the generic `<verb> --help` below
        # would discard it (round 2 F8's defect, one level out).
        return _blocked(exc.next_cmd, str(exc), remedy=exc.remedy)
    except vocab.TransportDeclarationError as exc:
        # Round 2 F2: a defective transport DECLARATION is a typed, blocked
        # state, never generic usage — `<verb> --help` cannot repair a
        # record or a config, and discarding the remedy left the agent an
        # unrunnable dead end. The remedy names what a person declares; for
        # a defective ledger record, a corrected `take`/`request` record
        # supersedes it by recency.
        return _blocked("", str(exc), remedy=exc.remedy)
    except (RuntimeError, ValueError, OSError, KeyError,
            TypeError) as exc:
        # OSError, not FileNotFoundError: round-5 fp2:9f8d9f3677f33f51 found
        # `validate <directory>` reaching an uncaught IsADirectoryError
        # traceback — a reachable filesystem failure bypassing the
        # structured-output contract. Permission and encoding failures are
        # the adjacent states (UnicodeDecodeError is a ValueError).
        return _usage_exit(
            paths.command(paths.Lit(TOOL_NAME),
                          paths.token(args.command), paths.Lit("--help")),
            str(exc))


if __name__ == "__main__":
    sys.exit(main())

"""CLI (design §9bis.3): exit codes mean exactly one thing — 0 ok, 1 findings,
2 usage. Every non-zero exit prints the next command, not a diagnosis.
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
from .ledger import Ledger, render_convergence_md, render_report_md
from .validate import (Item, errors_in, validate_disposition,
                       validate_request, validate_verdict)

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
# `test_round4_fixes.py` asserts that `print` appears in exactly `_out` and
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
    "absent", "agreement", "anchor_path", "at_round", "attrs", "author",
    "author_next",
    "authorized_by", "base",
    # Round 2 F1: the emission stamp binding a disposition row to its
    # companion events; an identity the agent reads, never a command.
    "batch",
    "blocking", "breaker", "brief", "bytes",
    "bytes_freed", "cached", "checked", "cites", "claim_digest",
    "classification", "classification_notes", "classifications", "closures",
    "config", "convergence", "covers", "data", "declared_transport",
    "default_cap", "digest",
    "disposition", "dispositions", "dry_run",
    "entry", "envelope", "error", "event", "events_added", "events_total",
    "exit", "falsification", "fetch", "files", "finding_id", "finding_ids",
    "findings", "findings_per_round", "fp", "from", "gate_output", "head",
    "hunted_anchors", "id",
    "ignored_control_fields", "install", "installed", "items", "kept",
    "kept_unrecognised", "kind", "ledger", "ledgers", "limits", "lineage",
    "lineage_closed_at_round", "moved", "mutation", "next_kind",
    "new_per_round", "next_lineage", "note", "of", "ok", "open_request",
    "out", "outcome",
    "path", "payload", "permitted_authors", "permitted_reviewers",
    "preventable_by", "pruned", "reason", "recorded", "referenced_shas",
    "reader", "reading", "references", "rejected_reviewers", "relay",
    "remedy", "repeated",
    "reviewer", "roles", "round", "round_cap", "severities", "severity",
    "rounds", "sha", "source", "source_digest", "stalled_threads", "state",
    "status", "subtype", "superseded",
    "tag", "target", "taxonomy", "test_digest", "then", "threads", "title",
    "to",
    "token_budget", "tokens",
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
                   f"{{request|verdict|disposition}}> tag; envelopes come "
                   f"from `handoff`, `respond` and the reviewer, never from "
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
    if kind == "request":
        items = validate_request(
            parsed, governing,
            round_cap=_ledger(cfg, args).effective_round_cap(
                governing.round_cap))
    elif kind == "verdict":
        items = validate_verdict(
            parsed, governing,
            answering=_dispositions_answered(_ledger(cfg, args), parsed))
    else:
        against = None
        if args.against:
            against = wire.parse_verdict(
                Path(args.against).read_text(encoding="utf-8"))
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
    # RVW-T11. The verdict document cannot be trusted to carry the topology —
    # its author is an agent writing prose, and a field it mis-transcribes
    # would decide what the OTHER side is told to run. The value comes from
    # this machine's own record of the round instead: the reviewer's `take`
    # wrote it, and `recorded_transport` reads it back for this SHA.
    verdict_next = (
        brief.verdict_relay(
            parsed, source=args.envelope,
            transport=transport.recorded_transport(_ledger(cfg, args),
                                                   parsed.sha),
            # On a paste round the reviewer hands over ONE block: the
            # command and, fenced beneath it, the verdict bytes it
            # consumes — bytes outside a fence are bytes a chat surface
            # may rewrite (round 3, live).
            envelope=text)
        if is_verdict and not errors_in(items) else None)
    return _finish(items, "", brief_text=precis, relay_text=verdict_next,
                   agreement=agreement,
                   remedy=f"whoever authored {args.envelope} must correct the "
                          f"items above and re-run "
                          f"`{paths.command(*paths.lits(TOOL_NAME, 'validate'), args.envelope)}`; envelopes "
                          f"are edited by their author, not by this tool")


def cmd_fingerprint(args, cfg) -> int:
    text = Path(args.verdict).read_text(encoding="utf-8")
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
    verdict_text = _read_envelope(args.verdict)
    verdict = wire.parse_verdict(verdict_text)
    ledger = _ledger(cfg, args)
    # Sweep F2: a disposition is an answer to a VALID, RECORDED verdict, not
    # a free-standing assertion. This verb used to parse whatever file it
    # was handed and go straight to building the response — an unwrapped
    # legacy verdict that fails validation, never recorded, produced
    # disposition events at a caller-supplied SHA with exit 0. The verdict
    # is validated first, in every mode.
    items = validate_verdict(
        verdict, cfg, answering=_dispositions_answered(ledger, verdict))
    if errors_in(items):
        return _finish(items,
                       paths.command(*paths.lits(TOOL_NAME, "validate"),
                                     args.verdict),
                       remedy=f"{paths.display_path(args.verdict)} is not "
                              f"a valid verdict, so nothing can answer it; "
                              f"the reviewer must correct and re-issue it")
    # And with --out — the mode that writes bytes of record and appends to
    # the ledger — the verdict must resolve to exactly one recorded verdict
    # of the current lineage, its just-closed round, through the same
    # boundary `ledger add` uses for a standalone disposition. Round and SHA
    # are then DERIVED from that record; a value supplied in the JSON may
    # only agree. Without --out the envelope is rendered and printed, not
    # recorded; the door it may later enter by (`ledger add`) resolves it
    # through the same rule.
    recorded = None
    if args.out:
        recorded = transport.recorded_verdict(
            ledger, digest=sha256_text(verdict_text))
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
    if recorded is not None:
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
                                        against=verdict)
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


def _dispositions_answered(ledger: Ledger, verdict) -> list[dict] | None:
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
    bound = ledger.rounds_for_sha(verdict.sha)
    if not bound:
        return None
    round_no = bound[-1]
    # The STANDING answer per finding — the newest event per fingerprint —
    # not raw event multiplicity: a disposition legitimately re-binds when
    # the head moves under it (lineage 6 round 2, with the ledger's
    # standing_dispositions as the one authority for what "answered" means).
    return ledger.standing_dispositions(round_no=round_no - 1)


def cmd_ledger_add(args, cfg) -> int:
    text = Path(args.envelope).read_text(encoding="utf-8")
    kind, parsed = _detect_and_parse(text)
    ledger = _ledger(cfg, args)
    digest = sha256_file(Path(args.envelope))
    size = Path(args.envelope).stat().st_size
    added = 0
    agreement = None
    if kind == "verdict":
        items = validate_verdict(
            parsed, cfg, answering=_dispositions_answered(ledger, parsed))
        if errors_in(items):
            return _finish(items,
                           paths.command(*paths.lits(TOOL_NAME, "validate"),
                                         args.envelope))
        round_no = args.round or ledger.round_for_sha(parsed.sha)
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
        conflict = transport.verdict_conflict(ledger, round_no, digest)
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
        added += ledger.add_all(transport.verdict_events(
            parsed, round_no, digest, size, args.tokens))
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
            round_cap=ledger.effective_round_cap(cfg.round_cap))
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
            parsed, round_no, digest, size, args.tokens))
        ledger.add({"event": "ingest", "kind": "request",
                    "round": round_no, "digest": digest,
                    "tool": agreement["reader"],
                    "tool_agreement": agreement["agreement"],
                    **({"tool_writer": agreement["writer"]}
                       if agreement["writer"] else {})})
    elif kind == "disposition":
        # Round 2 F2: this path recorded whatever identity it was handed.
        # A disposition binds by fingerprint, so it cannot be recorded
        # without the verdict that computes one — and if that verdict
        # cannot be retrieved, refusing is the only honest outcome.
        # Round 3 F2: resolved through the LEDGER, which is the record, not
        # through the kept file, which is a best-effort copy of it.
        against = transport.answered_verdict(cfg, parsed, ledger)
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
        items = validate_disposition(parsed, cfg, against=against)
        if errors_in(items):
            return _finish(items,
                           paths.command(*paths.lits(TOOL_NAME, "validate"),
                                         args.envelope))
        added += ledger.add_all(
            transport.disposition_events(parsed, against, cfg))
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
                       if agreement["writer"] else {})})
    else:
        return _blocked(
            paths.command(*paths.lits(TOOL_NAME, "validate"), args.envelope),
            f"{args.envelope} is not a recognizable request, verdict or "
            f"disposition envelope")
    payload = {"ok": True, "events_added": added, "ledger": str(ledger.path)}
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
    added = ledger.add({"event": "cap_override", "round_cap": args.to,
                        "reason": args.reason, "authorized_by": args.by,
                        "default_cap": cfg.round_cap})
    _out({"ok": True, "recorded": bool(added), "round_cap": args.to,
          "default_cap": cfg.round_cap, "ledger": str(ledger.path)},
         f"round cap for this lineage: {args.to} "
         f"(repo default stays {cfg.round_cap})")
    return EXIT_OK


def cmd_authorize_breaker(args, cfg) -> int:
    """Record the human's decision to continue past a fired breaker (§5.3d,
    sweep F8) — the counterpart of the handoff preflight's stop."""
    ledger = _ledger(cfg, args)
    try:
        rec = transport.authorize_breaker(cfg, ledger, args.breaker,
                                          args.reason or "", args.by or "")
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


def cmd_ledger_report(args, cfg) -> int:
    ledger = _ledger(cfg, args)
    # Round-4 F8 and F5: the declared manifest and the declared budget travel
    # with every product report, so the CLI and the emitter compute what the
    # ledger API computes. A metric that is only correct when a unit test
    # supplies its inputs is not wired.
    report = ledger.report(ledger.effective_round_cap(cfg.round_cap),
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
    result = ledger.convergence()
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
    """
    raw = (sys.stdin.read() if args.events == "-"
           else Path(args.events).read_text(encoding="utf-8"))
    events, bad = [], []
    for n, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
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
                               f"line is one JSON event object, then re-run "
                               f"`{paths.command(*paths.lits(TOOL_NAME, 'import-legacy'), args.events)}`")
    ledger = _ledger(cfg, args)
    added = ledger.add_all(events)
    _out({"ok": True, "events_added": added, "events_total": len(events),
          "ledger": str(ledger.path)},
         f"imported {added} of {len(events)} events into {ledger.path}")
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


def _emit(args, cfg, ledger, captured: "emit.CapturedClaim",
          selected_transport: str):
    """emit-request's body, shared with handoff: push, emit, validate.
    Returns (envelope, parsed) or an int exit code.

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
                                    round_no=emit.next_round(ledger),
                                    transport=selected_transport,
                                    author_flag=getattr(args, "author", None),
                                    reviewer_flag=getattr(args, "reviewer",
                                                          None))
    except (emit.AuthorityAbsent, emit.RoleSelectionError) as exc:
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
                                 transport=selected_transport)
    parsed = wire.parse_request(envelope)
    base = args.base or max((e for e in ledger.current()
                             if e.get("event") == "verdict"),
                            key=lambda e: e["round"])["sha"]
    shape = emit.diff_shape(cfg.repo_root, base, parsed.sha)
    items = validate_request(parsed, governing,
                             recomputed_shape=(shape["files"],
                                               shape["insertions"],
                                               shape["deletions"]),
                             round_cap=ledger.effective_round_cap(
                                 governing.round_cap))
    if errors_in(items):
        return _finish(items, "",
                       remedy=f"a person must correct review.toml or "
                              f"{args.claim_file} to satisfy the items above, "
                              f"then re-run "
                              f"`{paths.command(paths.Lit(TOOL_NAME), paths.token(args.command))}`")
    return envelope, parsed


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
    result = _emit(args, cfg, ledger, captured, selected_transport)
    if isinstance(result, int):
        return result
    envelope, parsed = result
    if args.out:
        Path(args.out).write_text(envelope, encoding="utf-8")
        _out({"ok": True, "out": args.out, "sha": parsed.sha},
             f"wrote {args.out}")
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
    try:
        transport.handoff_preflight(cfg, ledger)
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
    round_no = emit.next_round(ledger)
    # User decision 2026-08-25: past the cap the tool emits and INVESTIGATES
    # rather than refusing. The count alone taught nothing — it fires on a
    # lineage doing exactly what it should — so the answer travels with the
    # warning: which findings the loop is failing to close, and which
    # domains keep producing new ones however many are fixed.
    past_cap = round_no > ledger.effective_round_cap(cfg.round_cap)
    cached = transport.cached_handoff(
        cfg, ledger, round_no, claim_digest=claim_digest,
        transport=selected_transport,
        author_flag=getattr(args, "author", None),
        reviewer_flag=getattr(args, "reviewer", None))
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
                                       round_no, claim_digest=claim_digest)
        rec["cached"] = True
        rec["ok"] = True
        rec["tool"] = agreement
        ledger.add({"event": "ingest", "kind": "request",
                    "round": round_no, "digest": rec.get("digest"),
                    "source": "handoff cache",
                    "tool": agreement["reader"],
                    "tool_agreement": agreement["agreement"],
                    **({"tool_writer": agreement["writer"]}
                       if agreement["writer"] else {})})
        if args.out:
            Path(args.out).write_text(cached["envelope"], encoding="utf-8")
            rec["out"] = args.out
        _brief_into(rec, cached["envelope"], ledger)
        _out(rec, f"round {round_no} request for {rec['sha']} is already "
                  f"recorded and kept at {paths.display_path(rec['kept'])} — gates not re-run "
                  f"(§9bis.3 rule 5)\n\n{rec['brief']}\n\n"
                  f"{render_tool_agreement(agreement)}\n{rec['relay']}\n\n"
                  f"author: {rec['author_next']}")
        return EXIT_OK
    result = _emit(args, cfg, ledger, captured, selected_transport)
    if isinstance(result, int):
        return result
    envelope, parsed = result
    rec = transport.record_handoff(cfg, ledger, envelope, round_no,
                                   claim_digest=claim_digest)
    rec["cached"] = False
    rec["ok"] = True
    if args.out:
        Path(args.out).write_text(envelope, encoding="utf-8")
        rec["out"] = args.out
    _brief_into(rec, envelope, ledger)
    if past_cap:
        rec["convergence"] = ledger.convergence()
    _out(rec, f"round {round_no} request emitted for {rec['sha']}, "
              f"recorded ({rec['bytes']} bytes, sha256 {rec['digest'][:16]}…), "
              f"kept at {paths.display_path(rec['kept'])}\n\n"
              f"{rec['brief']}\n\n"
              f"{render_convergence_md(rec['convergence']) if past_cap else ''}"
              f"{rec['relay']}\n\n"
              f"author: {rec['author_next']}")
    return EXIT_OK


def _brief_into(rec: dict, envelope: str, ledger) -> None:
    """Attach the plain-language précis and the relay to a result record.

    On both channels deliberately. A TTY reader sees the text; an agent reads
    JSON, where `brief` and `relay` are fields it can hand to the user rather
    than a summary it has to compose — which is the difference between the
    human always getting one and usually getting one.
    """
    parsed = wire.parse_request(envelope)
    rec["brief"] = brief.request_precis(parsed, ledger)
    rec["relay"] = brief.relay(rec.get("kept"), parsed, envelope)


def _read_envelope(arg: str) -> str:
    return (sys.stdin.read() if arg == "-"
            else Path(arg).read_text(encoding="utf-8"))


def cmd_take(args, cfg) -> int:
    """Reviewer side, one verb: validate, probe, record, print the envelope
    and the exact diff command — then the reviewer rules and stops."""
    ledger = _ledger(cfg, args)
    envelope = _read_envelope(args.envelope)
    try:
        rec = transport.take(
            cfg, ledger, envelope, args.envelope, reviewer=args.as_,
            fetch=not args.no_fetch, transport=args.transport,
            # Sweep F6: `governing` is the target commit's own config,
            # resolved by `take` after the fetch — not this checkout's.
            validate_items=lambda parsed, governing: validate_request(
                parsed, governing,
                round_cap=ledger.effective_round_cap(governing.round_cap)))
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
    refs = "\n".join(f"  {r['status']:<40} {r['path']}"
                      for r in rec["references"]) or "  (none declared)"
    rec["brief"] = brief.request_precis(wire.parse_request(envelope), ledger)
    # Round-1 F4: the agreement was returned in JSON and rendered nowhere a
    # human looks. A report-not-refuse decision rests on the human SEEING
    # the difference; a field only the non-TTY path carries cannot support
    # a decision the visible command never names.
    _out(rec, f"{rec['envelope']}\n"
              f"--- taken: round {rec['round']} target {rec['sha']} as "
              f"reviewer {rec['reviewer']}\n\n{rec['brief']}\n\n"
              f"target: {rec['target']}\nreferences:\n{refs}\n"
              f"{render_tool_agreement(rec['tool'])}"
              f"diff:   {rec['diff']}\nthen:   {rec['then']}")
    return EXIT_OK


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


def cmd_close(args, cfg) -> int:
    """Author side: ingest the verdict and close the round; a clean verdict
    closes the lineage; --lineage closes one by recorded decision."""
    ledger = _ledger(cfg, args)
    try:
        if args.lineage:
            rec = transport.close_lineage(ledger, args.reason or "",
                                          args.by or "")
            rec["ok"] = True
            _out(rec, f"lineage closed at round {rec['lineage_closed_at_round']}"
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
        text = _read_envelope(args.verdict)
        parsed_verdict = wire.parse_verdict(text)
        # Round 2 F2: the recorded transport is resolved and validated
        # BEFORE close_round writes anything. Resolved after, a defective
        # record refused the round only once the verdict — and for a clean
        # ruling the lineage closure — was already appended, and the
        # refusal escaped as generic usage help that cannot repair an
        # append-only ledger; and a CLEAN close emptied `current()` first,
        # so a recorded `paste` silently became the default carrier. The
        # value is read once, here, and carried across the closure.
        carrier = transport.recorded_transport(ledger, parsed_verdict.sha)
        rec = transport.close_round(
            cfg, ledger, text, args.verdict, round_no=args.round,
            tokens=args.tokens,
            validate_items=lambda parsed: validate_verdict(
                parsed, cfg,
                answering=_dispositions_answered(ledger, parsed)))
    except transport.Refusal as exc:
        return _blocked(exc.next_cmd, str(exc), remedy=exc.remedy)
    rec["ok"] = True
    rec["brief"] = brief.verdict_precis(parsed_verdict, source=args.verdict)
    rec["relay"] = brief.verdict_relay(
        parsed_verdict, source=args.verdict, transport=carrier)
    _out(rec, f"round {rec['round']} closed: {rec['verdict']} on {rec['sha']} "
              f"({rec['findings']} findings, {rec['closures']} closures), "
              f"kept at {paths.display_path(rec['kept'])}\n"
              f"lineage: {rec['lineage']}\n\n"
              f"{rec['brief']}\n\n{rec['relay']}")
    return EXIT_OK


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
    source, superseded = args.envelope, 0
    if source is None:
        event, superseded = brief.find_open_request(ledger)
        if event is None:
            return _blocked(
                "",
                "no open review request in this lineage: either nothing has "
                "been emitted yet, or every emitted round already carries a "
                "verdict",
                remedy=f"a person writes the claim and emits a round with "
                       f"`{paths.command(*paths.lits(TOOL_NAME, 'handoff', '--claim-file'), paths.Ph('<their claim file>'))}`; "
                       f"there is nothing to summarise until one is "
                       f"open, and the claim is the author's judgment, which "
                       f"this tool carries and never invents")
        path = transport.exchange_path(cfg, event["round"], "request")
        if path is None or not path.is_file():
            return _blocked(
                "",
                f"round {event['round']} is open but its bytes were not kept, "
                f"so there is nothing to summarise",
                remedy=f"a person passes the envelope itself — "
                       f"`{paths.command(*paths.lits(TOOL_NAME, 'brief'), paths.Ph('<the path they hold>'))}` — since the copy this "
                       f"tool kept is gone and only the person who has the "
                       f"bytes knows where they are")
        source = str(path)

    text = _read_envelope(source)
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
        rec = {"kind": "request", "source": source, "ok": True,
               "round": as_request.attrs.get("round"),
               "sha": as_request.sha, "superseded": superseded,
               "tool": agreement,
               "brief": brief.request_precis(as_request, ledger),
               "relay": brief.relay(source, as_request, text,
                                    paste=args.paste)}
        note = (f"\nNOTE: {superseded} earlier emission(s) of this round are "
                f"superseded; this is the live one.\n" if superseded else "")
        _out(rec, f"{rec['brief']}\n{note}\n"
                  f"{render_tool_agreement(agreement)}\n{rec['relay']}")
        return EXIT_OK

    as_verdict = wire.parse_verdict(text)
    if as_verdict.wrapped:
        # Two fields, exactly as the request side has always had: the account
        # a human reads, and the commands a human copies (round 3 relay
        # split). Never one blob for a caller to divide by guesswork.
        rec = {"kind": "verdict", "source": source, "ok": True,
               "sha": as_verdict.sha,
               "brief": brief.verdict_precis(as_verdict, source=source,
                                             full=args.full),
               "relay": brief.verdict_relay(
                   as_verdict, source=source,
                   transport=transport.recorded_transport(ledger,
                                                          as_verdict.sha),
                   envelope=text)}
        _out(rec, f"{rec['brief']}\n\n{rec['relay']}")
        return EXIT_OK

    return _blocked(paths.command(*paths.lits(TOOL_NAME, "validate"),
                                  source),
                    f"{source} is neither a review request nor a verdict")


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

    w = sub.add_parser("waive", help="record that a commit was deliberately "
                                     "NOT reviewed, with the reason")
    w.add_argument("--sha", required=True,
                   help="the commit that goes unreviewed; resolved here, so "
                        "a waiver cannot name something that does not exist")
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

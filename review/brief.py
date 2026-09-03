"""Plain-language précis of an envelope, and the relay the human carries.

The loop has exactly one human step — moving an envelope between two agents —
and until now it was the only step with no account of what was being moved.
The bytes are written for the reviewer; `relay = "user"` names a person who
was given a path and no reason. This module closes that: it restates, in
prose, what an envelope already says, and prints the exact command or the
exact bytes needed to carry it.

Everything here is DERIVED. No field is authored, nothing is inferred, and no
model is involved — so the précis cannot claim something the envelope does not
contain, and two runs over the same bytes produce the same text. Where a value
is absent it is reported as absent, never as zero or as a default.
"""
from __future__ import annotations

import re
import textwrap

from . import TOOL_NAME, paths, vocab
from .validate import parse_attestations
# ONE authority for the reference-line grammar, and for reading the declared
# transport off an envelope. Round 1 F10 came from reading those lines loosely
# here while transport parsed them strictly, so the précis described a state
# the envelope did not record; a second reading of the transport attribute
# would be the same defect with a different field.
from .transport import _REF_LINE_RE, declared_transport

_HEADER = {
    "target": re.compile(r"^Target:\s+(\S+)", re.M),
    "base": re.compile(r"^Base:\s+(\S+)", re.M),
    "tree": re.compile(r"^Tree:\s+(.+?)\s*$", re.M),
    "push": re.compile(r"^Push:\s+(.+?)\s*$", re.M),
    "round": re.compile(r"^Round:\s+(.+?)\s*$", re.M),
}
_OBJECTIVE = re.compile(r"^Objective / decision boundary:\s*(.+?)(?=\n\n|\Z)",
                        re.M | re.S)
_RISK = re.compile(r"^Self-assessed risk:\s*(.+?)(?=\n\n|\Z)", re.M | re.S)
_CHANGED = re.compile(r"^What changed \((.+?)\s*—", re.M)
# A list item at the emitter's indent OR at column 0: `_split_sections`
# strips each section's content, so the FIRST bullet of a section that opens
# with a bullet (Stop conditions does) loses its two spaces, and an
# indent-only pattern counted every such section one short — every envelope
# that ever carried the section, round 5's "6 of 7" and the sweep's "7 of 8"
# included. Author-found while checking the sweep's own render (2026-08-17);
# the recurring trap category: a stated count whose enumeration disagrees
# with it.
_BULLETS = re.compile(r"^(?:  )?- (.+?)$", re.M)
_LINEAGE = re.compile(r"^Lineage:\s+(\d+)", re.M)


_LABEL = 15  # kept for callers that still pass an explicit indent


def _wrap(text: str, indent: str = "") -> str:
    """Flatten to one line.

    This used to hard-wrap at 100 columns and indent the continuation under a
    padded label, which made the précis a fixed-width table. Round 5: that is
    why five rounds of this account arrived at the human unreadable. The
    précis is written for a person, an agent relays it into a chat, and a
    markdown renderer collapses runs of spaces and strips leading indent — so
    the column alignment that made it legible in a terminal was exactly what
    destroyed it everywhere else, and the only way to preserve it was a code
    fence, which is the "copy this" signal belonging to the relay.

    So no manual wrapping and no alignment. One field per line, markdown
    emphasis for the label, and every destination wraps it itself: a terminal
    soft-wraps, a renderer re-flows. One rendering that survives both beats
    two renderings that can drift.
    """
    return " ".join(text.split())


# A placeholder a person fills in — and, to a shell, a redirection.
_PLACEHOLDER = re.compile(r"<[^<>]*>")


def _fence(*commands: str, lang: str = "bash") -> list[str]:
    """A command block that survives being relayed into a chat.

    Round 5 F1: with `lang="bash"` every line here is a command an agent
    runs verbatim, so every line must be a rendered `paths.Command` — a
    string built any other way is refused rather than trusted. The one
    non-command use is the envelope paste (`lang=""`), which is bytes.

    Indentation does not: markdown strips it, and five rounds of this
    account reached the human with its commands run together as prose. A
    fence is explicit, and it marks the one thing that IS meant to be copied
    verbatim rather than read.

    Sweep F7: the delimiter is longer than any backtick run in the content.
    A fixed three-backtick fence was closed by the first three-backtick line
    inside it — and a real request carries its own ```<tag>-attestations
    block, so the pasted envelope's outer block ended inside Evidence, 119
    lines before the wrapper did, and everything after travelled as prose
    whose whitespace and markup no renderer preserves. CommonMark: a code
    fence is closed only by a run at least as long as the one that opened
    it, so one longer than the longest run inside cannot be closed by the
    content (§9bis.4: paste is the cross-machine transport, and it carries
    bytes or it carries nothing).
    """
    if lang == "bash":
        checked = []
        for line in commands:
            paths.executable(line, "a fenced relay line")
            # Workshop (c): the promise is that every live line runs as
            # PRINTED, so a line carrying a placeholder nobody has filled in
            # is not a live line. `<...>` is a redirection to a shell, so
            # `--as <your id>` and `--from-json <dispositions.json>` are not
            # merely incomplete — they are syntax errors that stop the whole
            # pasted block, which is how a fence came to promise something
            # its own bytes broke.
            #
            # The rule lives HERE rather than at each call site for the same
            # reason the executable check does: a promise every caller has to
            # remember is one the next caller breaks. Commented, the reader
            # still gets the exact command, and the block still parses.
            if not str(line).startswith("#") and _PLACEHOLDER.search(line):
                line = paths.comment(line)
            checked.append(line)
        commands = tuple(checked)
    longest = 0
    for line in commands:
        for run in re.findall(r"`+", line):
            longest = max(longest, len(run))
    tick = "`" * max(3, longest + 1)
    return [f"{tick}{lang}", *commands, tick]


class UnrelayableEnvelope(ValueError):
    """An envelope whose exact bytes cannot ride a heredoc (round-3 F2).

    A heredoc delivers one newline after its last body line, always — so
    an envelope with no terminal newline cannot arrive byte-identical
    through this carrier, and printing a relay that silently appends one
    would hand the consuming verb different bytes from the ones the
    validator judged. Raised BEFORE any relay is rendered; the remedy is
    the file's, not the carrier's.
    """


def _heredoc_body(envelope: str) -> str:
    """The heredoc body whose delivery is byte-identical to `envelope`.

    Round-3 F2 closed the terminal-newline domain: the old code stripped
    EVERY trailing newline and the heredoc restored exactly one, so zero,
    one and two terminal newlines all delivered one — the author recorded
    a different byte digest from the artifact the reviewer validated.
    The partition now: an envelope ending in one or more newlines has
    exactly ONE stripped — the one the heredoc's own mechanics restore —
    so every extra terminal newline rides as an empty body line and the
    delivered bytes equal the source bytes; an envelope with no terminal
    newline is refused, because no heredoc can deliver it unchanged.
    """
    if not envelope.endswith("\n"):
        raise UnrelayableEnvelope(
            "this envelope does not end with a newline, and a heredoc "
            "delivers exactly one after its last line — relaying it would "
            "change the bytes the validator judged. A person appends the "
            "terminal newline to the file and re-runs; every envelope the "
            "tool itself emits already ends with one")
    return envelope[:-1]


def _heredoc(data: str, base: str) -> "paths.Heredoc":
    """A quoted heredoc opener whose delimiter no line of `data` can close
    early. The exact-line collision is the danger that matters: a data
    line equal to the delimiter ends the heredoc THERE, and everything
    after it would run as shell — so the delimiter is chosen against the
    bytes, bumped with a numeric suffix until it collides with nothing."""
    lines = set(data.split("\n"))
    delim, n = base, 1
    while delim in lines:
        n += 1
        delim = f"{base}_{n}"
    return paths.Heredoc(delim)


def _paste_block(command_line, data: str, delimiter: str) -> list[str]:
    """ONE fence carrying a command and the bytes it consumes — the paste
    legs' whole relay (relay ergonomics, 2026-08-30).

    The old shape was two fences with prose between them: the command in
    one, the bytes labelled and fenced below, and the human had to carry
    both and the receiving agent had to marry them back up — measured
    failing in live cross-machine use, where the reviewer errored unless
    the human retyped the command beside the paste. Folded into one block,
    the paste IS the command: the bytes ride as the command's own stdin
    under a QUOTED delimiter, so nothing in them expands or executes.

    The fence keeps its promise in the only form a heredoc admits: paste
    the block and exactly one command runs, with the data as its stdin.
    The command line is checked through the same executable door as every
    fenced relay line; the data lines are not commands and are not checked
    as commands — they are inert heredoc content, made inert by the quoted
    delimiter `_heredoc` chose against them. The fence itself outruns any
    backtick run inside, exactly as `_fence` does.
    """
    paths.executable(command_line, "a paste-block command line")
    lines = [str(command_line), *data.split("\n"), delimiter]
    longest = 0
    for line in lines:
        for run in re.findall(r"`+", line):
            longest = max(longest, len(run))
    tick = "`" * max(3, longest + 1)
    return [f"{tick}bash", *lines, tick]


# The heredoc delimiter bases, derived from the tool's own name so a rename
# travels; sanitised into the Heredoc class rather than trusted to fit it.
_DELIM_BASE = re.sub(r"[^A-Z0-9_]", "_", TOOL_NAME.upper()) or "TOOL"


def _reference_states(section: str) -> dict[str, int]:
    """Reference lines counted by the state the grammar actually records.

    Round 1 F10. `digested` means a sha256 the reviewer's `take` can check;
    `asserted` means a line with no digest, which nothing can verify;
    `declared unavailable` is the author saying so; a directory carries
    per-file digests via git rather than one of its own. Collapsing these into
    "digested" claimed verification that three of the four never had.
    """
    counts = {"with a digest to check": 0, "directories": 0,
              "asserted, no digest": 0, "declared unavailable": 0,
              "in no recognised form": 0}
    for line in section.splitlines():
        if not line.strip() or line.strip().startswith("("):
            continue
        m = _REF_LINE_RE.match(line)
        if m is None:
            counts["in no recognised form"] += 1
        elif m.group("unavailable"):
            counts["declared unavailable"] += 1
        elif m.group("dir") or m.group("note"):
            counts["directories"] += 1
        elif m.group("digest"):
            counts["with a digest to check"] += 1
        else:
            # Reachable since the reference grammar gained an explicit
            # asserted form. Round 2 recorded a deviation here — the
            # disposition promised this bucket and the grammar could not
            # produce it; the grammar was the thing that was wrong.
            counts["asserted, no digest"] += 1
    return counts


def _count_bullets(section: str, heading: str) -> int:
    m = re.search(rf"^{re.escape(heading)}\n((?:  - .+\n?)+)", section, re.M)
    if not m:
        return 0
    items = _BULLETS.findall(m.group(1))
    if len(items) == 1 and items[0].strip().startswith("("):
        return 0          # the emitter's "(nothing declared)" placeholder
    return len(items)


# --------------------------------------------------------------- discovery

def open_requests(ledger) -> list[dict]:
    """Request events in the CURRENT lineage whose round carries no verdict."""
    current = ledger.current()
    ruled = {e.get("round") for e in current if e.get("event") == "verdict"}
    return [e for e in current
            if e.get("event") == "request" and e.get("round") not in ruled]


def find_open_request(ledger):
    """`(event, superseded)` for the live request, or `(None, 0)`.

    The ledger is append-only, so re-emitting a round does not remove its
    predecessor: both sit there as open requests at the same round. The last
    one written is the live one and the earlier ones are superseded — reported
    as a count rather than silently dropped, because a human who emitted twice
    should be told which of the two they are about to carry.
    """
    candidates = open_requests(ledger)
    if not candidates:
        return None, 0
    top = max(e.get("round", 0) for e in candidates)
    at_round = [e for e in candidates if e.get("round", 0) == top]
    return at_round[-1], len(at_round) - 1


# ------------------------------------------------------------------ précis

def request_precis(parsed, ledger=None) -> str:
    """What this request asks, in prose. Derived from the envelope only."""
    body, sections = parsed.body, parsed.sections
    attrs = parsed.attrs
    head = {k: (m.group(1) if (m := rx.search(body)) else None)
            for k, rx in _HEADER.items()}
    claim = sections.get("claim", "")

    out = ["## What this asks", ""]
    rnd = head["round"] or attrs.get("round", "?")
    lineage = m.group(1) if (m := _LINEAGE.search(body)) else "?"
    out.append(f"- **Round** — {rnd}, lineage {lineage}")
    out.append(f"- **Direction** — {attrs.get('author', '?')} wrote it, "
               f"{attrs.get('reviewer', '?')} rules on it")

    target = head["target"] or attrs.get("sha") or "?"
    branch = attrs.get("branch")
    out.append(f"- **Commit** — {target[:12]}"
               + (f" on {branch}" if branch else "")
               + (f", working tree {head['tree']}" if head["tree"] else ""))

    push = head["push"] or ""
    if push.startswith("LOCAL-ONLY"):
        out.append("- **Reachable** — NO — this commit exists on this machine "
                   "only. A reviewer elsewhere cannot fetch it.")
    elif push:
        # Round 1 F9: this said "any machine can fetch it". The Push line
        # attests an ls-remote observation — that the ref is present on the
        # remote — and says nothing about who may read that remote. A private
        # repository is exactly the case where the stronger claim is false.
        out.append("- **Reachable** — present on the remote and confirmed there "
                   "after the push, so a client with access to that remote "
                   "can fetch it")
    else:
        out.append("- **Reachable** — not stated")

    if head["base"] and head["base"] == target:
        out.append("- **Diff** — EMPTY — base and target are the same "
                   "commit, so the diff shows nothing")
    elif changed := (m.group(1) if (m := _CHANGED.search(claim)) else None):
        out.append(f"- **Diff** — {changed}")

    if m := _OBJECTIVE.search(claim):
        out.append("- **Asking for** — " + _wrap(m.group(1)))
    if m := _RISK.search(claim):
        out.append("- **Author risk** — " + _wrap(m.group(1)))
    if scope := sections.get("review scope", "").strip():
        out.append("- **Scope** — " + _wrap(scope))

    not_done = _count_bullets(claim, "Deliberately not done:")
    if not_done:
        out.append(f"- **Declared** — {not_done} thing(s) the author says are "
                   f"deliberately not done — read them before ruling")
    not_captured = _count_bullets(
        sections.get("evidence", ""),
        "NOT captured — this handoff cannot vouch for these:")
    if not_captured:
        out.append(f"- **Unevidenced** — {not_captured} thing(s) the author says "
                   f"this handoff cannot vouch for")

    records, err, _ = parse_attestations(sections.get("evidence", ""))
    if err or records is None:
        out.append("- **Gates** — none readable in this envelope")
    else:
        passed = [r for r in records if r.get("exit_code") == 0]
        bound = [r for r in records if r.get("binding") == "bound"]
        line = (f"- **Gates** — {len(passed)} of {len(records)} passed")
        if len(bound) == len(records) and records:
            line += ", all bound to this exact commit"
        else:
            line += (f", only {len(bound)} of {len(records)} bound to this "
                     f"commit — the rest prove nothing about it")
        out.append(line)

    states = _reference_states(sections.get("reference", ""))
    if total := sum(states.values()):
        # Round 1 F10: this said the tool "digested each one" for every
        # reference, including those the envelope marks unavailable or carries
        # with no digest at all. The states are distinct in the grammar and
        # are reported distinctly here.
        parts = [f"{n} {label}" for label, n in states.items() if n]
        out.append(f"- **References** — {total} the reviewer must read — "
                   + ", ".join(parts))
    if stop := sections.get("stop conditions", "").strip():
        n = len(_BULLETS.findall(stop))
        if n:
            out.append(f"- **Refuse if** — {n} stated stop condition(s)")
    return "\n".join(out)


def verdict_precis(parsed, source: str | None = None,
                   blocking: tuple[str, ...] = ("Blocker", "High"),
                   full: bool = False) -> str:
    """What this verdict rules, in prose. Derived from the envelope only.

    This listed severity COUNTS in its first form, which is not an account of
    anything: a reader cannot act on "1 Blocker, 4 High", and the point of
    putting a human in the relay is that they can intervene before the loop
    continues. They cannot intervene on a tally. Every finding is now named,
    with what the reviewer requires, blocking ones first.
    """
    ruling = parsed.verdict or ""
    # The heading IS the verdict (user-directed 2026-08-22): the first
    # thing the carrier reads is the ruling itself, not a label for it.
    # First letter capitalized like every heading (round-1 F4); the
    # machine vocabulary is untouched — validators consume the wire's
    # VERDICT line, never this rendering. No verdict line → the generic
    # heading and the absence stated, never a heading inventing a ruling.
    if ruling:
        out = [f"## {ruling[0].upper()}{ruling[1:]}", ""]
    else:
        out = ["## What this rules", "",
               "- **Verdict** — no verdict line found"]
    if parsed.sha:
        out.append(f"- **On commit** — {parsed.sha[:12]}")

    findings = parsed.findings or []
    if parsed.findings_none and not findings:
        out.append("- **Findings** — none — the reviewer recorded a literal "
                   "'None'")
    else:
        blockers = [f for f in findings if f.severity in blocking]
        rest = [f for f in findings if f.severity not in blocking]
        out.append(f"- **Findings** — {len(findings)}, of which "
                   f"{len(blockers)} block")
        for group, heading in (
                (blockers, "**Blocking — these stop the change advancing**"),
                (rest, "**Non-blocking**")):
            if not group:
                continue
            out.append("")
            out.append(heading)
            out.append("")
            for f in group:
                out.append(f"- **{f.id}** · {f.severity} — "
                           + _wrap(f.title))
                if full and f.required_outcome:
                    # Nested one level, which markdown reads as a sub-item
                    # and a terminal reads as an indented continuation.
                    out.append("    - needs: " + _wrap(f.required_outcome))
        if not full and any(f.required_outcome for f in findings):
            # The reviewer's Required outcome is a specification written for
            # whoever implements the fix. It is the longest and densest part
            # of a verdict and it is not what a person deciding whether to
            # carry the envelope needs, so it is opt-in. Nothing here can
            # make it plainer: rewriting the reviewer's words would need a
            # model, and a model in this code path is the one thing the tool
            # refuses everywhere else.
            out.append("")
            out.append(f"- **Detail** — what each finding requires: "
                       f"`{TOOL_NAME} brief <verdict> --full`")

    if parsed.closures:
        kinds: dict[str, int] = {}
        for c in parsed.closures:
            kinds[c.closure] = kinds.get(c.closure, 0) + 1
        out.append("- **Closures** — " + ", ".join(f"{n} {k}"
                                                   for k, n in kinds.items())
                   + " — answers to earlier rounds' findings")
    if parsed.unavailable_references:
        out.append(f"- **Unread** — {len(parsed.unavailable_references)} "
                   f"reference(s) the reviewer could not retrieve; the "
                   f"verdict says so rather than ruling around them")

    # Workshop (c): the audience line and the carrier choice used to be two
    # bullets HERE, describing a fence printed somewhere below them. Round 1
    # had put them inside the relay as prose and a relaying agent mangled
    # them; the brief was the reply to that. But a person handed the fence
    # alone — which is the whole point of a block you paste as-is — then held
    # commands naming the REVIEWER's own file with nothing saying so.
    #
    # Both now travel inside the fence as comments, where they cannot be
    # separated from the commands they qualify and cannot be reworded in
    # transit. What stays here is what the brief is for: an account of what
    # was ruled, for a person deciding whether to intervene.
    return "\n".join(out)


def verdict_relay(parsed, source: str | None = None,
                  transport: str = vocab.TRANSPORT_DEFAULT,
                  envelope: str | None = None,
                  reference: str | None = None) -> str:
    """One section, `## Verdict`, holding exactly what the human carries.

    On a declared paste round, `envelope` (the verdict's own bytes) travels
    INSIDE the one block, as the stdin of the close command that consumes
    them — a quoted heredoc, delimiter chosen against the bytes — exactly
    as the request leg carries its envelope.
    Round 3, live: the verdict crossed a chat surface as loose prose, the
    renderer stripped its markdown headings, and the author's `close`
    refused a mangled document the reviewer had validated byte-for-byte. A
    fence is the one region a chat surface preserves verbatim; bytes
    outside one are bytes the transit may rewrite.

    The lineage of this shape, each turn answering a real failure: the
    commands began inside `verdict_precis` (one blob; every relayer
    improvised its own split); round 3 separated brief from relay; round 5
    fenced the commands; lineage-5 round 1 named the audience in prose
    beside them, and a relaying agent wrapped the whole section in a fence
    of its own; 2026-08-22 the user cut the prose entirely, leaving a bare
    fence whose first line named a path on the REVIEWER's machine.

    Workshop (c) is the turn that stops the oscillation, because prose
    beside a fence and prose in the brief were the only two options being
    traded. A comment is inside the block and inert, so the block keeps one
    promise — every line runs exactly as printed — while carrying the two
    facts a person needed: who runs it, and that the path is the reviewer's.

    `respond` is a comment for the same reason, and it is the sharper case:
    it carries `<dispositions.json>` and `<disposition.md>`, files the
    author has to write first. Printed live beside `close`, it made the one
    block a person is told to paste as-is contain a line that could not be
    — so a paste-and-run either failed on a file named `<dispositions.json>`
    or taught the reader not to trust the fence. Commented, the author reads
    the exact command and runs it when it is true.

    Round-2 F4: a real `source` path renders shell-safe — these lines are
    run verbatim, and a path with a space split into the wrong argv. The
    placeholder forms stay literal: a placeholder is filled in by a
    person, never executed, and quoting it would suggest otherwise.
    """
    # The name states the property the inventory scan enforces: anything
    # interpolated after a command token is either paths.shell_path(...)
    # at the site or a local whose name declares it pre-quoted — and this
    # one's single assignment is the proof.
    # A `git` round's verdict is already on a ref both sides reach — the
    # reviewer's own `validate --from-target` pushed it — so the word is the
    # round reference and the block is one line with no bytes beside it.
    # The reference is the caller's to supply, because the lineage is a fact
    # of the ledger and not of the verdict document.
    if transport == vocab.TRANSPORT_GIT and reference:
        return "\n".join(["## Verdict", "", *_fence(paths.command(
            *paths.lits(TOOL_NAME, "close", "--verdict"), reference))])
    verdict_word = (paths.Lit("-") if transport == vocab.TRANSPORT_PASTE
                    else (source if source else paths.Ph("<verdict.md>")))
    # `respond` used to ride here as a comment. It is gone, and the reason
    # is not brevity: `close` DERIVES the next command when it records the
    # round — `changes requested` returns `respond --verdict <the recorded
    # verdict> …`, `clean to advance` returns nothing — so printing a second
    # copy here made two sources for one fact, and this was the copy that
    # could be wrong. It named the REVIEWER's path, while the one `close`
    # emits names the path the tool actually kept. A relay that guesses the
    # next step ahead of the verb that computes it is a guess the reader
    # cannot distinguish from an answer.
    #
    # What stays is what nothing else carries. The audience: the verdict
    # brief no longer states it, and a person reading `loupe close` in their
    # own terminal reasonably ran it — that is a real 2026-08-20 report, not
    # a hypothetical. The carrier: round 4 F2, ruled a High design gap,
    # because this line names a file on the REVIEWER's machine and an author
    # anywhere else has no way to see that from the line itself.
    # RVW-T11. Until the topology was declared, this line had to hedge: it
    # named the reviewer's own path and then explained, on every round, what
    # to do if the author was somewhere else — a caveat that was noise in the
    # only configuration the loop had ever run, and the sole protection in
    # the one it had never run. Now the round says which it is, so the line
    # states one fact instead of two possibilities.
    #
    # Relay ergonomics (user, 2026-08-30): this leg is one section titled
    # `## Verdict` containing exactly what needs to be carried — no
    # commentary, no second block, on either transport and for either
    # verdict. The paste round's old note ("the author's to run — paste
    # the verdict into it") carried an instruction only because the bytes
    # travelled in a SEPARATE fence a person had to marry up with the
    # command; folded into one heredoc block, the instruction is embodied
    # and the note would restate what the block already does.
    if transport == vocab.TRANSPORT_PASTE and envelope is not None:
        data = _heredoc_body(envelope)
        hd = _heredoc(data, f"{_DELIM_BASE}_VERDICT")
        close_cmd = paths.command(
            *paths.lits(TOOL_NAME, "close", "--verdict"), paths.Lit("-"), hd)
        return "\n".join(["## Verdict", "",
                          *_paste_block(close_cmd, data, hd.delimiter)])
    close_cmd = paths.command(paths.Lit(TOOL_NAME), paths.Lit("close"),
                              paths.Lit("--verdict"), verdict_word)
    # F1 (lineage 6 round 1): a placeholder-bearing line is a Template, and
    # a Template may not be a live fence line — the fence promises every
    # line runs exactly as printed. With no real source path the command is
    # a person's to finish, so it travels as a comment.
    if isinstance(close_cmd, paths.Template):
        close_cmd = paths.comment(close_cmd)
    return "\n".join(["## Verdict", "", *_fence(close_cmd)])


# ------------------------------------------------------------------- relay

def relay(kept: str | None, parsed, envelope: str,
          paste: bool = False, transport: str | None = None,
          reference: str | None = None) -> str:
    """The exact thing the human carries — one block, same shape as the
    verdict side.

    Workshop (c): this leg used to print two fences with prose between them
    and a third line of prose after, and the person had to read all of it to
    decide which fence applied. The verdict leg, meanwhile, printed one bare
    fence and kept its prose in the brief. One job, two conventions, and the
    only thing a relaying agent could do reliably with either was move the
    whole section and hope.

    Now both legs are: a heading, one fence, and every fact that qualifies a
    command inside it as a comment.

    RVW-T11 finished the sentence Workshop (c) left open. The shape was
    right and the CONTENT was still hedging: one live line for the carrier
    the tool could see working, plus a commented alternative for the case it
    could not see — printed every round, in a loop where that case had never
    once occurred. The tool could not see it because nobody had told it, and
    nothing in the envelope said which topology the round ran over.

    Now the round declares it. `path` — the two ends read the same disk —
    renders exactly one line and nothing else: the carrier that applies, no
    alternative, no caveat, nothing for a relaying agent to reword. `paste`
    renders ONE block in which the take command opens a quoted heredoc and
    the envelope bytes ride as its stdin (relay ergonomics, 2026-08-30 —
    the earlier command-fence-plus-bytes-fence shape made the human retype
    the command beside the paste), because a reviewer who cannot open this
    filesystem needs the envelope itself and not a pointer into it. Neither
    shape asks the reader to choose; the round already chose, and the fence
    says what it chose.
    """
    transport = (transport if transport is not None
                 else declared_transport(parsed))
    # `--as` is part of the command, not an option to discover: round 1 F4
    # made the identity declaration mandatory, and a relay that prints a line
    # the tool will refuse is worse than printing none — the human hands over
    # something that fails and has to debug a tool they are only carrying for.
    reviewer = (getattr(parsed, "attrs", {}) or {}).get("reviewer", "")
    # `git` (ruled 2026-09-03): the envelope is on a ref both sides reach, so
    # this leg is ONE line naming the round — no bytes, no path, nothing for
    # the carrier to rewrite in transit. It is the shape `paste` would have
    # had if a person had not been the only carrier available, and it keeps
    # the same promise as the other two: the round already chose, and the
    # fence says what it chose.
    if transport == vocab.TRANSPORT_GIT and reference:
        take_line = paths.command(
            paths.Lit(TOOL_NAME), paths.Lit("take"), reference,
            paths.Lit("--as"),
            reviewer if reviewer else paths.Ph("<your id>"))
        if isinstance(take_line, paths.Template):
            take_line = paths.comment(take_line)
        return "\n".join(["## How to carry it", "", *_fence(take_line)])
    # Relay ergonomics (user, 2026-08-30): a declared paste round with a
    # stamped reviewer is ONE block — the take command opening a quoted
    # heredoc, the envelope bytes as its stdin, the delimiter closing them.
    # The old shape (command fence, label, bytes fence) failed in live
    # cross-machine use: the reviewer errored unless the human retyped the
    # command beside the paste. Without a stamped reviewer the command is a
    # Template no fence may run, so that case keeps the two-part shape
    # below, where the commented command and the bytes travel separately.
    if transport == vocab.TRANSPORT_PASTE and reviewer:
        data = _heredoc_body(envelope)
        hd = _heredoc(data, f"{_DELIM_BASE}_REQUEST")
        take_cmd = paths.command(paths.Lit(TOOL_NAME), paths.Lit("take"),
                                 paths.Lit("-"), paths.Lit("--as"),
                                 reviewer, hd)
        return "\n".join(["## How to carry it", "",
                          *_paste_block(take_cmd, data, hd.delimiter)])
    # Round 4 F1: the identity is a dynamic shell WORD, not a path and not
    # therefore safe — a permitted id carrying `;` altered the command an
    # agent is instructed to run verbatim. The placeholder stays literal;
    # round 5 F1 made both of them words of a rendered command rather than
    # a fragment spliced into one.
    as_words = (paths.Lit("--as"),
                reviewer if reviewer else paths.Ph("<your id>"))
    pasted = paths.command(paths.Lit(TOOL_NAME), paths.Lit("take"),
                           paths.Lit("-"), *as_words)
    # F1 (lineage 6 round 1): with no reviewer stamped, the take line
    # carries a placeholder — a Template, which may not be a live fence
    # line. It travels as a comment a person finishes.
    if isinstance(pasted, paths.Template):
        pasted = paths.comment(pasted)
    # No audience line on this leg: `request_precis` already opens with
    # `- **Direction** — <author> wrote it, <reviewer> rules on it`, and a
    # relay that restates the brief beside it is paying twice for one fact.
    # The verdict leg keeps its audience line precisely because its brief
    # does NOT carry the equivalent.
    have_kept = bool(kept) and not kept.startswith("not kept")
    lines = []
    if have_kept and transport != vocab.TRANSPORT_PASTE:
        # One line, and it is the whole block. Quoted exactly when the shell
        # needs it (F3): this line is copied into a shell, and a state path
        # with a space would otherwise split into the wrong argv. An ordinary
        # path renders unchanged.
        take_line = paths.command(paths.Lit(TOOL_NAME), paths.Lit("take"),
                                  kept, *as_words)
        if isinstance(take_line, paths.Template):
            take_line = paths.comment(take_line)
        lines.append(take_line)
    elif have_kept:
        lines.append(paths.comment(
            "the reviewer's to run — this round declares no shared "
            "filesystem, so paste the envelope below into it"))
        lines.append(pasted)
    else:
        # Neither declaration's doing: the bytes were never kept, so there is
        # no path to offer under either topology.
        lines.append(paths.comment(
            "the bytes were not kept, so paste is the only carrier:"))
        lines.append(pasted)
    # A declared paste round needs the bytes, not a flag that would print
    # them: the human carrying this has no second command to run, and asking
    # them to re-invoke the tool to obtain the thing they were just told to
    # carry is the extra step the declaration exists to remove.
    paste = paste or (transport == vocab.TRANSPORT_PASTE and have_kept)
    out = ["## How to carry it", "", *_fence(*lines)]
    if paste:
        out.append("")
        out.append("The envelope, to paste:")
        out.append("")
        # Not a command: no language tag, and a fence the envelope's own
        # fences cannot close.
        out.extend(_fence(envelope.rstrip("\n"), lang=""))
    return "\n".join(out)

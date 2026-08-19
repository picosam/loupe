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

from . import TOOL_NAME
from .validate import parse_attestations
# ONE authority for the reference-line grammar. Round 1 F10 came from reading
# those lines loosely here while transport parsed them strictly, so the précis
# described a state the envelope did not record.
from .transport import _REF_LINE_RE

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


def _fence(*commands: str, lang: str = "bash") -> list[str]:
    """A command block that survives being relayed into a chat.

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
    longest = 0
    for line in commands:
        for run in re.findall(r"`+", line):
            longest = max(longest, len(run))
    tick = "`" * max(3, longest + 1)
    return [f"{tick}{lang}", *commands, tick]


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

    out = ["## WHAT THIS ASKS", ""]
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
    out = ["## WHAT THIS RULES", ""]
    ruling = parsed.verdict or "no verdict line found"
    out.append(f"- **Verdict** — {ruling}")
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

    return "\n".join(out)


def verdict_relay(parsed, source: str | None = None) -> str:
    """The commands that follow a verdict, as their own artifact.

    These lines used to sit inside `verdict_precis`, under a `Next` heading,
    and that merge is a real defect rather than a formatting preference. The
    request side has carried two separate fields since it was written — a
    `brief` a human reads and a `relay` a human copies — and the verdict side
    carried one blob containing both kinds of text. An agent handing that to
    a person has no structural signal about which half is which, so it
    improvises a split, differently each round: fencing the prose, or burying
    the command inside the summary. Three sessions of the same complaint were
    the symptom; one field doing two jobs was the cause.

    So the account and the instructions are separated at the source, and the
    adapters can state one rule for both directions: give the human the
    brief, then give them the relay.

    Round 5: the commands are FENCED. A four-space indent is how a terminal
    sets a command apart and how markdown builds a code block — but only if
    nothing upstream has already stripped the indent, which is exactly what
    happens when an agent relays this into a chat. A fence survives the trip,
    and it is the one place a fence belongs: this half IS the copy-this half.
    """
    where = source or "<verdict.md>"
    ruling = parsed.verdict or ""
    out = ["## WHAT TO RUN NEXT", ""]
    if ruling.startswith("clean to advance"):
        out.append("Nothing is blocking. Recording it closes the lineage "
                   "at this commit:")
        out.append("")
        out.extend(_fence(f"{TOOL_NAME} close --verdict {where}"))
    else:
        out.append("Recording it concedes nothing — it files the ruling "
                   "and leaves the lineage open:")
        out.append("")
        out.extend(_fence(f"{TOOL_NAME} close --verdict {where}"))
        out.append("")
        out.append("Then each finding is answered, which is where "
                   "accept / refute / defer is decided:")
        out.append("")
        out.extend(_fence(f"{TOOL_NAME} respond --verdict {where} "
                          f"--from-json <dispositions.json> "
                          f"--out <disposition.md>"))
    return "\n".join(out)


# ------------------------------------------------------------------- relay

def relay(kept: str | None, parsed, envelope: str,
          paste: bool = False) -> str:
    """The exact thing the human carries — a command, or the bytes.

    Both are printed unconditionally rather than the tool guessing which the
    situation needs: a path is useless to an agent on another machine, and
    pasted bytes are needless friction on this one. The human can see which
    applies from the reachability line in the précis above.
    """
    # `--as` is part of the command, not an option to discover: round 1 F4
    # made the identity declaration mandatory, and a relay that prints a line
    # the tool will refuse is worse than printing none — the human hands over
    # something that fails and has to debug a tool they are only carrying for.
    reviewer = (getattr(parsed, "attrs", {}) or {}).get("reviewer", "")
    as_flag = f" --as {reviewer}" if reviewer else " --as <your id>"
    out = ["## HOW TO CARRY IT", ""]
    if kept and not kept.startswith("not kept"):
        out.append("Same machine — give the reviewer this line verbatim:")
        out.append("")
        out.extend(_fence(f"{TOOL_NAME} take {kept}{as_flag}"))
        out.append("")
    else:
        out.append("Same machine — the bytes were not kept, so the paste "
                   "below is the only transport.")
        out.append("")
    out.append(f"Another machine or a cloud session — have the reviewer run "
               f"`{TOOL_NAME} take -{as_flag}` and paste the envelope into it.")
    if not paste:
        out.append("")
        out.append(f"Re-run with `--paste` to print the {len(envelope)} bytes "
                   f"to paste.")
    else:
        out.append("")
        out.append("The envelope, to paste:")
        out.append("")
        # Not a command: no language tag, and a fence the envelope's own
        # fences cannot close.
        out.extend(_fence(envelope.rstrip("\n"), lang=""))
    return "\n".join(out)

"""Per-agent adapters, generated from one source (design §6.3, RVW-T10).

The agents' standing instruction files may not name this tool — that is a
verified property, and it is what lets them keep working with nothing
installed. Adapters are the
exception: they carry the literal command lines (§9bis.3 rule 7) so an agent
copies rather than improvises, and they install at USER level so no shared
repository needs an instruction-file edit.

One source renders every adapter: PROCEDURE below (what each side does, in
order, and what it never does) plus the verb table read from the CLI's own
parser, so an adapter cannot describe a verb the tool does not have or omit
one it does. `render-adapters --check` byte-guards the rendered files and is
a gate.

Rendered kinds:
  claude-skill        adapters/claude/SKILL.md   → ~/.claude/skills/loupe/
  codex-skill         adapters/codex/SKILL.md    → ~/.codex/skills/loupe/
  instructions-block  adapters/instructions-block.md — an agent-neutral
                      block for a BEGIN/END GENERATED region in a project
                      instruction file, where a skill directory is not read
No Gemini adapter: §10.7 decided Gemini/Antigravity does not hold the
reviewer role, and the author role there is a separate decision.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import TOOL_NAME, TOOL_VERSION, paths, vocab
from .digest import sha256_text

ADAPTERS_DIR = "adapters"

# Install targets. Codex's was observed on the authoring machine on
# 2026-08-15 (~/.codex/skills/<name>/SKILL.md with the same frontmatter
# shape). Claude's is the documented convention: no ~/.claude/skills existed
# on 2026-08-15, and the first install created it on 2026-08-16. The path is
# therefore occupied but still convention-sourced — a Claude release that
# moved the directory would not be caught by anything here.
INSTALL = {
    "claude-skill": ("~/.claude/skills/%s/SKILL.md" % TOOL_NAME,
                     "conventional; first installed here 2026-08-16"),
    "codex-skill": ("~/.codex/skills/%s/SKILL.md" % TOOL_NAME,
                    "observed on the authoring machine"),
}

def _claim_members() -> str:
    """The claim's members, from the grammar authority itself (round 7 F1) —
    a field added to `vocab.CLAIM_FIELDS` reaches every agent's copy of the
    procedure at the next render, and no adapter can list a member the
    boundary would refuse."""
    return ", ".join(f"`{m}`" for m in sorted(vocab.CLAIM_FIELDS))


# ONE source. Role-agnostic on purpose: an agent learns which side it holds
# from the envelope's stamp (author=… reviewer=…), never from this text.
#
# A FUNCTION, not a module-level dict (lineage 8): every vocabulary
# enumeration below is rendered from `review/vocab.py` at call time, so the
# derivation is live — patch the authority and re-render, and the text
# follows. That property is what makes the shipped-restatement exclusion
# for `adapters/` TRUE rather than asserted: these documents cannot restate
# a vocabulary, they instantiate it, and
# `test_adapter_enumerations_are_derived` proves it by mutation.
def procedure() -> dict:
    status_key, mutation_key = vocab.FALSIFICATION_RECORD
    return {
    "both": [
        "Exit codes mean exactly one thing: 0 ok, 1 findings or refusal, "
        "2 usage. Every non-zero exit carries `next_kind`: when it is "
        "`command`, run the `next` command verbatim; when it is `blocked`, "
        "`next` is null — stop, give the human the `remedy` field, and wait. "
        "Either way, do not diagnose and do not improvise a flag or a fix.",
        "Output is JSON when stdout is not a TTY. Read fields; never parse "
        "prose.",
        "Never hand-type an envelope, never edit the ledger, never paste gate "
        "output into Evidence — the tool runs gates and attests them.",
        "The human sets a review round in motion. Whichever side you hold, "
        "you stop where the procedure says stop; you do not invoke the "
        "other side by any mechanism (CLI, subagent, hook, API).",
        "Boundary closure. When a finding is about a parser, a grammar, or a "
        "lifecycle seam, the answer partitions the COMPLETE admitted domain: "
        "every input kind the boundary accepts, every declared field and "
        "nested shape, every ordering the seam can be reached by — with "
        "paired VALID controls proving the check is live, and a mutation per "
        "bypass proving it can fail. Making the named reproducer green is not "
        "an answer, it is one point of a domain, and the next unpartitioned "
        "point is the next round. This binds both sides: an acceptance and a "
        "closure are judged by whether the domain is closed, never by whether "
        "the example passes. One terminator, and only one: a domain whose "
        "completeness cannot be established from inside the artifact — a set "
        "that can be declared but never proven complete from within — may be "
        "declared CLOSED RELATIVE TO A STATED AUTHORITY. An authority is a "
        "machine-generated, separately gated artifact that derives the set; "
        "a hand-maintained list is never one. The author declares it in the "
        "claim, per anchor. From then on the reviewer attacks the authority "
        "— its derivation and its coverage — and may still bring an "
        "instance, but an instance against a declared-closed domain must "
        "name the authority it escapes; one that names none is not a "
        "completeness finding. Rejecting the authority is an ordinary "
        "finding, anchored on the authority itself, and it is bounded, "
        "because unbounded it restates one level down forever. The "
        "author states the authority's DOMAIN beside it — what it is "
        "claimed to cover, in the terms it enforces. THE REVIEWER RULES "
        "ON THE DOMAIN BEFORE RULING UNDER IT: does it match the anchor's "
        "declared purpose and every surface still relied on downstream? A "
        "concrete escape outside the declaration is admissible evidence "
        "that the domain is too narrow, and a domain narrower than its "
        "anchor's purpose is a finding about the claim. Only once a "
        "stated domain is ACCEPTED is coverage ruled against it, and only "
        "then is a demand outside it scope for a backlog rather than a "
        "finding for the round, however well it reproduces. Narrowing is "
        "a complete answer only when the anchor's purpose and every "
        "consumer claim narrow with it, leaving no wider reliance "
        "implicit; a narrowing that leaves a wider promise standing is "
        "not an answer, it is the defect. Where it is complete it is "
        "preferred to widening the mechanism. The declaration is "
        "not an "
        "exemption: it moves the attackable surface from an unbounded hunt "
        "onto a finite artifact, and an authority narrower than its own "
        "STATED DOMAIN is itself the defect.",
        "Every request and every verdict result carries TWO fields, and they "
        "are two different kinds of text. `brief` is the plain-language "
        "account of what is being asked or ruled — give it to the human as "
        "readable prose, never inside a code block. `relay` is the literal "
        "commands to run — reproduce it EXACTLY as given, and nothing else. "
        "The relay already carries its own code fences around each command; "
        "wrapping the whole block in another fence breaks at the first "
        "inner fence and everything after travels as prose, so never add "
        "fencing of your own in either direction. Emit them in that order, "
        "both, every time, and do "
        "not merge, reorder, summarise or paraphrase either: both are derived "
        "from the envelope and yours would not be. Never put your own prose "
        "inside the relay block and never bury a command inside the brief. "
        f"`{paths.command(*paths.lits(TOOL_NAME, 'brief'))}` reproduces "
        f"both at any time, and with no argument it "
        "finds the open request itself.",
        "The round declares whether the two sides share a filesystem, and "
        "that is what decides which carrier the relay prints. You do not "
        "decide it and you do not detect it: the tool resolves it from "
        "declarations, strongest first — an explicit `--transport "
        f"{'|'.join(vocab.TRANSPORTS)}`, "
        "then `[roles] transport` in the repository's config, "
        f"then the environment's own declaration (`{vocab.TRANSPORT_ENV}`, "
        "set by a cloud environment's configuration) or a documented cloud "
        "provider signal, and with none of those a new emission carries "
        f"`{vocab.TRANSPORT_EMISSION_DEFAULT}`, "
        "the workflow's steady case: author and reviewer on the "
        "operator's machine. A cloud-authored round therefore reaches "
        "`paste` through its environment's declaration, never through your "
        "guess (a locally readable request file proves nothing about what "
        "the other side can open). A human corrects a wrong stamp by an "
        "explicit statement about THIS round's topology or an exact "
        "command carrying `--transport` — to `handoff` if you are the "
        "author, to `take` if you are the reviewer; a conversational "
        "question is not a declaration, so ask rather than convert one "
        "into an override. Never improvise the flag, and never substitute "
        "a carrier the relay did not print: a round declared `path` whose "
        "commands you rewrite for a paste is a round whose record no "
        "longer describes it.",
        "A paste-carried envelope travels INSIDE the fence the tool "
        "printed, on both legs: the request bytes ride under `take -`, the "
        "verdict bytes under `close --verdict -`. Hand the fenced block "
        "over whole; never re-send envelope bytes as loose prose — a chat "
        "surface preserves a fenced region verbatim and rewrites "
        "everything else (markdown headings included), and a mangled "
        "envelope refuses validation on the other side.",
    ],
    "author": [
        ("write the claim",
         None,
         f"A JSON claim file, and a closed grammar: its members are "
         f"{_claim_members()} — `{vocab.CLAIM_REFERENCES_FIELD}` is a list "
         f"of objects carrying a non-empty "
         f"{', '.join(f'`{m}`' for m in vocab.CLAIM_REFERENCE_REQUIRED)} "
         f"(the reviewer must read it; the tool digests it) and optionally "
         f"{', '.join(f'`{m}`' for m in sorted(set(vocab.CLAIM_REFERENCE_FIELDS) - set(vocab.CLAIM_REFERENCE_REQUIRED)))}; "
         f"{', '.join(f'`{m}`' for m in vocab.CLAIM_LIST_FIELDS)} are lists "
         f"of strings; the rest are strings; "
         f"{', '.join(f'`{m}`' for m in vocab.CLAIM_REQUIRED)} is required. "
         f"Malformed JSON, a top-level value that is not an object, a "
         f"repeated member at any depth, a wrong type, and an unknown member "
         f"are each refused by name before the tool reads the ledger, "
         f"touches Git, runs a gate or records anything — an unknown member "
         f"is never ignored, so a misspelling cannot silently erase what it "
         f"meant to declare. So are the states that are not in the JSON: the "
         f"file must be UTF-8, every string must be encodable (a lone "
         f"surrogate is valid JSON and not a value an envelope can carry), "
         f"and `--claim-file \"\"` is refused rather than treated as an "
         f"omitted option. Supplying no claim at all stays legal and is "
         f"recorded as its own state. It is your judgment; the tool carries "
         f"it and never invents it."),
        ("hand off",
         paths.command(*paths.lits(TOOL_NAME, "handoff", "--claim-file"),
                       paths.Ph("<claim.json>"),
                       paths.opt(paths.Lit("--base"), paths.Ph("<sha>"))),
         "Commits outstanding tracked work, pushes the reviewed branch, runs "
         "the gate manifest, emits and validates the request, records it, "
         "keeps the bytes under the state directory, and prints the "
         "reviewer's command. `--base` is needed only for round 1 of a "
         "lineage. Re-running on an unchanged tip is a no-op that returns "
         "the same envelope. THEN STOP: hand the human the fenced block "
         "under `## How to carry it`, whole and unedited. It is "
         "self-contained — every live line in it runs exactly as printed, "
         "and it carries the ONE carrier this round declared, never a "
         "choice for the reader to make. Add no prose of your own around "
         "it: a relay that gets reworded in transit is the failure this "
         "shape exists to end. Refusals (untracked files, detached HEAD, "
         "unreachable remote, unassigned roles, no taxonomy) name the fix."),
        ("close the round",
         paths.command(*paths.lits(TOOL_NAME, "close", "--verdict"),
                       paths.Ph("<verdict.md>")),
         f"Validates and records the verdict. `{vocab.VERDICT_CHANGES}` → "
         f"the lineage stays open and the next command is `respond`; "
         f"`{vocab.VERDICT_CLEAN}` → the lineage is closed at the exact "
         f"reviewed SHA. Merge "
         "authority is the account allowlist and the standing rules you "
         "operate under, not this tool."),
        ("respond",
         paths.command(*paths.lits(TOOL_NAME, "respond", "--verdict"),
                       paths.Ph("<verdict.md>"), paths.Lit("--from-json"),
                       paths.Ph("<d.json>"), paths.Lit("--out"),
                       paths.Ph("<disposition.md>")),
         f"Exactly one disposition per finding — "
         f"{' / '.join(vocab.DISPOSITIONS)} — each with its mandatory "
         f"payload; a blocking finding cannot take "
         f"{' or '.join(f'`{d}`' for d in vocab.BLOCKING_ILLEGAL)}. "
         "An `accepted` disposition for a finding that names a "
         "falsification test also records the run of THAT test, in "
         f"`payload.falsification`: `{status_key}` "
         f"({' | '.join(vocab.FALSIFICATION_STATUSES)}) "
         f"on the head you hand back, and `{mutation_key}` — the same test "
         f"with the defect reintroduced "
         f"({' | '.join(vocab.MUTATION_OUTCOMES)}), "
         "which is the proof the test can fail at all. A test "
         "that still fails, or one that passes without the fix, is refused "
         "as an acceptance; `cannot_execute` and `not_run` need a `note` "
         "saying why. Do the mutation before you write the record, not "
         "after. `--out` records and keeps it. Then make the changes and "
         "hand off again: the next request carries the disposition ledger."),
        ("close a lineage by decision",
         paths.command(*paths.lits(TOOL_NAME, "close", "--lineage",
                                   "--reason"), paths.qph("<why>")),
         "Only when the human decides a review is over without a clean "
         "verdict. Recorded with the reason; the next handoff opens round 1 "
         "with the repository's default cap."),
    ],
    "reviewer": [
        ("take",
         paths.command(*paths.lits(TOOL_NAME, "take"),
                       paths.Ph("<request.md>"), paths.Lit("--as"),
                       paths.Ph("<your id>")),
         "`--as` is MANDATORY and names you: the tool cannot observe who is "
         "running it, so it records the identity you declare and refuses "
         "rather than assume one. Declaring an identity that is not yours "
         "puts a false actor in an append-only record. Validates the request, "
         "checks that you are the stamped reviewer, fetches the target and "
         "resolves base and head in this clone, labels every reference "
         "checked / mismatch / unavailable, records the take, and prints the "
         "envelope with the exact diff command. If it refuses, return its "
         "output to the human — never review a defective or unreachable "
         "envelope."),
        ("rule",
         None,
         "Read the diff with the printed command and rule by the declared "
         "taxonomy only. Findings ordered by severity, atomic (one claim per "
         "ID), each with every required field and a FALSIFICATION. Prior "
         "dispositions are answered under `## closures` by fingerprint. "
         "Material you could not retrieve goes under `## unavailable "
         "references` and forbids a clean verdict."),
        ("validate, then stop",
         paths.command(*paths.lits(TOOL_NAME, "validate"),
                       paths.Ph("<verdict.md>"),
                       paths.Lit("--from-target")),
         # This paragraph described `governing_for` as it stood between
         # rounds 2 and 5 — resolving from a RECORD and falling back to
         # "whatever governed the take". Round 6 F1 deleted that machinery
         # entirely, and both this text and the function's own docstring
         # kept describing it, which shipped in 0.9.0. There is no
         # "otherwise": the target carries its rules or the verb refuses.
         "`--from-target` judges your verdict against the target commit's "
         "OWN review.toml, read straight out of that commit — the same "
         "bytes `take` read, resolved the same way, with no record "
         "consulted and nothing to fall back to. A target that carries no "
         "review.toml refuses here exactly as it refuses at `take` and at "
         "`handoff`: rules that live on one machine cannot be shown to a "
         "second, and a review is the act of showing them. Without the "
         "flag the "
         "envelope is judged against whatever YOUR checkout holds, which on "
         "a detached worktree or a second machine is routinely no config at "
         "all, and the step the procedure requires of you cannot complete. "
         "Repeat until it exits 0. Then STOP: hand the human the block "
         "under `## What to run next`, whole and unedited — it is the "
         "author's to run. On a "
         "paste round that block already carries the verdict bytes in "
         "their own fence beneath the command; hand it over as one piece "
         "and never restate the bytes as loose prose (a chat surface "
         "rewrites unfenced text). On a path round hand the kept file's "
         "path with it. Add no prose around it. You do not start the next "
         "round, do not send it back for fix-and-resubmit, and do not "
         "decide whether it enters the record."),
    ],
    }


def verb_table() -> list[tuple[str, str]]:
    """(verb, help) for every subcommand the CLI actually has, from the
    parser itself — the adapters cannot drift from the command surface."""
    from .cli import build_parser
    parser = build_parser()
    rows = []
    for action in parser._actions:  # noqa: SLF001 - argparse has no public API for this
        if getattr(action, "choices", None) and isinstance(action.choices, dict):
            for name, sub in action.choices.items():
                rows.append((name, (sub.description or "").strip()
                             or _help_of(action, name)))
    return rows


def _help_of(action, name: str) -> str:
    for choice in getattr(action, "_choices_actions", []):
        if choice.dest == name:
            return (choice.help or "").strip()
    return ""


def _body() -> list[str]:
    L = []
    L.append(f"## What this is")
    L.append("")
    L.append(f"`{TOOL_NAME}` is a deterministic review tool: request → "
             f"verdict → disposition envelopes bound to SHAs, a gate manifest "
             f"the tool runs itself, fingerprinted findings, an append-only "
             f"ledger with breakers. No model runs inside it. You are either "
             f"the **author** or the **reviewer** of a given envelope — the "
             f"envelope's stamp (`author=… reviewer=…`) says which, per "
             f"invocation, and this text does not.")
    L.append("")
    steps = procedure()
    L.append("## Rules that hold on both sides")
    L.append("")
    for rule in steps["both"]:
        L.append(f"- {rule}")
    L.append("")
    for role in ("author", "reviewer"):
        L.append(f"## If you hold the {role} stamp")
        L.append("")
        for i, (title, cmd, what) in enumerate(steps[role], 1):
            L.append(f"{i}. **{title}**" + (f" — `{cmd}`" if cmd else ""))
            L.append(f"   {what}")
        L.append("")
    L.append("## Verbs (from the CLI itself)")
    L.append("")
    L.append("| verb | what |")
    L.append("|---|---|")
    for verb, help_ in verb_table():
        L.append(f"| `{paths.command(paths.Lit(TOOL_NAME), paths.token(verb))}` | {help_} |")
    L.append("")
    L.append("## Where things are")
    L.append("")
    L.append("- Config: `review.toml` at the repo root (taxonomy, roles, "
             "limits, gates). Absent taxonomy → the tool refuses to emit and "
             "the reviewer refuses to rule.")
    L.append(f"- State: `~/.local/state/{TOOL_NAME}/<repo-id>/` — the ledger, "
             f"gate output, and `exchange/` with every kept envelope. "
             f"Override with `--ledger-dir` or `{TOOL_NAME.upper()}_STATE_DIR`.")
    L.append("- The reviewed SHA is pushed before emission and stamped with "
             "the observed remote ref; `take` verifies it. A SHA that cannot "
             "be fetched is not a review target.")
    L.append("")
    return L


def _frontmatter(surface: str) -> list[str]:
    return [
        "---",
        f"name: {TOOL_NAME}",
        # This sentence was false in 0.9.0 and shipped that way. Round 6 F1
        # removed the external-configuration ORIGIN for reviewed commits,
        # and the body of this document was updated while the description
        # was not — so the field an agent reads FIRST, and the field that
        # decides whether the skill loads at all, told every session not to
        # require the very thing `handoff` and `take` refuse without. The
        # drift gate compares generated bytes to their generator; it cannot
        # notice that the generator describes a rule that changed.
        f"description: Use when asked to hand off work for review, take a "
        f"review request, respond to a verdict, or close a round with "
        f"`{TOOL_NAME}`. A REVIEWED commit must carry `review.toml` in the "
        f"repo root: `handoff` and `take` both refuse a target that tracks "
        f"none, because rules living on one machine cannot be shown to a "
        f"second. A user-level `~/.config/{TOOL_NAME}/<repo-id>.toml` still "
        f"governs every LOCAL verb, so do not require an in-tree file "
        f"before acting locally. Carries the literal commands for the "
        f"author and reviewer sides; the envelope stamp says which side "
        f"you hold.",
        "---",
        "",
        f"# {TOOL_NAME} — review procedure ({surface} adapter, GENERATED)",
        "",
        f"GENERATED by `{paths.command(*paths.lits(TOOL_NAME, 'render-adapters'))}` from one source "
        f"(review/adapters.py) at tool version {TOOL_VERSION} — do not edit; "
        f"regenerate. `{TOOL_NAME} render-adapters --check` guards it.",
        "",
    ]


def render(kind: str) -> str:
    if kind == "claude-skill":
        head = _frontmatter("Claude Code")
        head.append(f"Install: `{INSTALL[kind][0]}` ({INSTALL[kind][1]}).")
        head.append("")
        return "\n".join(head + _body())
    if kind == "codex-skill":
        head = _frontmatter("Codex")
        head.append(f"Install: `{INSTALL[kind][0]}` ({INSTALL[kind][1]}).")
        head.append("")
        return "\n".join(head + _body())
    if kind == "instructions-block":
        head = [
            f"<!-- BEGIN GENERATED: {TOOL_NAME} adapter — do not edit; "
            f"`{TOOL_NAME} render-adapters` regenerates, `--check` guards -->",
            f"# {TOOL_NAME} — review procedure (instruction block, GENERATED)",
            "",
            f"For a project instruction file (AGENTS.md / CLAUDE.md region) "
            f"on a surface that reads no skill directory. Tool version "
            f"{TOOL_VERSION}.",
            "",
        ]
        return "\n".join(head + _body() + [
            f"<!-- END GENERATED: {TOOL_NAME} adapter -->", ""])
    raise ValueError(f"unknown adapter kind {kind!r}")


OUTPUTS = {
    "claude-skill": Path("claude") / "SKILL.md",
    "codex-skill": Path("codex") / "SKILL.md",
    "instructions-block": Path("instructions-block.md"),
}


def render_all(directory: Path) -> dict[str, str]:
    """Write every adapter under `directory`; returns {kind: path}."""
    written = {}
    for kind, rel in OUTPUTS.items():
        path = directory / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(kind), encoding="utf-8")
        written[kind] = str(path)
    return written


def install_targets() -> dict:
    """{kind: expanded path} — where each agent actually reads its copy.

    `INSTALL` stays the one authority for these paths (§6.3): the renderer
    prints them, the installer writes them, the drift check reads them, and
    nothing outside this module restates them.
    """
    return {kind: Path(target).expanduser()
            for kind, (target, _why) in INSTALL.items()}


class RetentionFailed(RuntimeError):
    """The bytes an install would replace could not be preserved and proven
    preserved. Round-9 F1: this is a refusal, never a warning — the target is
    left exactly as it was."""


def _select(targets: dict | None) -> dict:
    """The scope of an install or a drift check.

    `None` means "the production defaults". An explicit mapping means
    "exactly these", INCLUDING an empty one, which is zero targets and must
    read and write nothing. Round-9 F3: this was `targets or
    install_targets()`, so `{}` — a bounded or test-only scope — expanded
    into the real user-level agent directories. That is the same falsey-state
    collapse this round removed from `--claim-file`, reintroduced one file
    away from it: an explicit empty value is a state, not an absence.
    """
    return install_targets() if targets is None else dict(targets)


def check_install(directory: Path, targets: dict | None = None) -> list[dict]:
    """Per install target: `in_sync`, `stale`, `absent` or `unreadable`,
    against the rendered file under `directory`.

    `render-adapters --check` guards the TRACKED copies; it says nothing
    about the ones an agent loads, and on 2026-08-19 the installed Claude
    skill was found several rounds behind — missing the falsification record
    the `respond` step now requires, the `waive` verb, and the current `take`
    text. An agent reading a stale procedure is the one drift no gate here
    could see. This is deliberately NOT in the gate manifest: a machine that
    has never installed the skill is not a broken build.
    """
    rows = []
    for kind, target in _select(targets).items():
        source = directory / OUTPUTS[kind]
        try:
            wanted = source.read_text(encoding="utf-8")
        except OSError as exc:
            rows.append({"kind": kind, "target": str(target),
                         "status": "unreadable", "error": str(exc)})
            continue
        row = {"kind": kind, "source": str(source), "target": str(target),
               "source_digest": sha256_text(wanted)}
        if not target.is_file():
            row["status"] = "absent"
        else:
            try:
                current = target.read_text(encoding="utf-8")
            except OSError as exc:
                row["status"] = "unreadable"
                row["error"] = str(exc)
                rows.append(row)
                continue
            row["target_digest"] = sha256_text(current)
            row["status"] = "in_sync" if current == wanted else "stale"
        rows.append(row)
    return rows


def install_all(directory: Path, targets: dict | None = None,
                keep_dir: Path | None = None) -> list[dict]:
    """Copy each rendered adapter to where its agent reads it.

    Idempotent: a target already carrying those bytes is `unchanged` and is
    not rewritten. A target carrying different bytes is `replaced` — but only
    after what it carried has been written, flushed, fsynced and read back
    at the kept path. Preservation is a PRECONDITION of replacement, not a
    best effort beside it (round-9 F1): where the bytes cannot be preserved
    and proven preserved, the row is `failed` and the target is left exactly
    as it was.

    The first version degraded a retention failure to a `"not kept (…)"`
    string and overwrote anyway, which made the guarantee that justifies
    overwriting — a hand edit is never lost — untrue in precisely the states
    where it mattered: no state directory, an unwritable one, a keep path
    occupied by something else. Reporting the digest of bytes nobody kept is
    not preservation. Retention elsewhere in this tool is best effort because
    the bytes exist in the record anyway; a user's edited procedure exists
    nowhere else.
    """
    rows = []
    for kind, target in sorted(_select(targets).items()):
        source = directory / OUTPUTS[kind]
        row = {"kind": kind, "source": str(source), "target": str(target)}
        try:
            wanted = source.read_text(encoding="utf-8")
        except OSError as exc:
            rows.append({**row, "status": "failed", "error": str(exc)})
            continue
        row["digest"] = sha256_text(wanted)
        current = None
        if target.is_file():
            try:
                current = target.read_text(encoding="utf-8")
            except OSError as exc:
                rows.append({**row, "status": "failed", "error": str(exc)})
                continue
        if current == wanted:
            rows.append({**row, "status": "unchanged"})
            continue
        if current is not None:
            row["replaced_digest"] = sha256_text(current)
            try:
                row["kept"] = _preserve(kind, current, keep_dir)
            except RetentionFailed as exc:
                rows.append({**row, "status": "failed", "error": str(exc),
                             "preserved": False})
                continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(wanted, encoding="utf-8")
        except OSError as exc:
            rows.append({**row, "status": "failed", "error": str(exc)})
            continue
        rows.append({**row,
                     "status": "replaced" if current is not None
                     else "installed"})
    return rows


def _preserve(kind: str, text: str, keep_dir: Path | None) -> str:
    """Write `text` where it can be recovered from, prove it is there, and
    return the path — or raise RetentionFailed, which stops the replacement.

    Every state of the destination is answered, and only one of them is a
    success that did not just write the file: a kept copy that already holds
    exactly these bytes. A copy that holds DIFFERENT bytes under the name of
    this digest is not evidence of anything, so it refuses rather than
    assuming its own convention held.
    """
    if keep_dir is None:
        raise RetentionFailed(
            "no retention directory is configured, so the bytes this install "
            "would replace could not be kept anywhere")
    target = Path(keep_dir) / f"{kind}-{sha256_text(text)[:12]}.md"
    try:
        if target.is_file():
            if target.read_text(encoding="utf-8") == text:
                return str(target)
            raise RetentionFailed(
                f"{target} already exists and holds different bytes, so it is "
                f"not a copy of what this install would replace")
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
    except RetentionFailed:
        raise
    except (OSError, UnicodeError) as exc:
        # Round-10 F1: a kept path holding bytes that are not UTF-8 is an
        # admitted state of this destination, and UnicodeDecodeError is a
        # ValueError, not an OSError — so it escaped the refusal and took the
        # whole installer result with it, losing the failed row and every
        # other attempted target exactly when preservation could not be
        # proven. It is the same "not an OSError" gap the claim boundary was
        # ruled on two rounds ago, reintroduced here by me: every way of
        # failing to read what is at the destination is one refusal.
        raise RetentionFailed(f"could not keep the replaced bytes at "
                              f"{target}: {exc}") from exc
    try:
        written = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RetentionFailed(f"kept {target} could not be read back to "
                              f"prove it: {exc}") from exc
    if written != text:
        raise RetentionFailed(f"kept {target} does not read back as the "
                              f"bytes it was given")
    return str(target)


def check_all(directory: Path) -> list[str]:
    """Names of adapters whose rendered text differs from the file on disk
    (missing counts as stale)."""
    stale = []
    for kind, rel in OUTPUTS.items():
        path = directory / rel
        try:
            current = path.read_text(encoding="utf-8")
        except OSError:
            current = None
        if current != render(kind):
            stale.append(str(path))
    return stale

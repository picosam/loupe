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
  codex-skill         adapters/codex/SKILL.md    → ~/.agents/skills/loupe/
  instructions-block  adapters/instructions-block.md — an agent-neutral
                      block for a BEGIN/END GENERATED region in a project
                      instruction file, where a skill directory is not read
                      (`render-adapters --check-embedded` compares such a
                      region with this rendering, `--write-embedded`
                      replaces it)
No Gemini adapter: §10.7 decided Gemini/Antigravity does not hold the
reviewer role, and the author role there is a separate decision.

WHERE THE MACHINE-GLOBAL MODES READ FROM (0.25.0, install independence).
`--check-install` and `--install` with no `--dir` take their source from
the RUNNING package's own rendering, `render(kind)`, and consult no
directory at all: the answer cannot depend on whether a checkout happens to
sit beside the package. Until 0.24.x they read an `adapters/` directory
beside the installed package, which a wheel install never has, so a machine
holding only the adopting repository and the published pin could not
install or verify its adapters at all. `--dir <path>` keeps the
directory-sourced behaviour exactly, including `--install`'s refusal of a
stale tracked copy.
"""
from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path

from . import TOOL_NAME, TOOL_VERSION, paths, vocab
from .digest import sha256_text


def _sha256_bytes(data: bytes) -> str:
    """The digest of bytes AS THEY ARE on disk. For UTF-8 text with no
    newline translation it equals `sha256_text` of the decoded text, so a
    kept name computed either way agrees wherever both apply."""
    return hashlib.sha256(data).hexdigest()

ADAPTERS_DIR = "adapters"

# Install targets, each with WHERE ITS AUTHORITY COMES FROM.
#
# Claude's is the documented convention: no ~/.claude/skills existed on
# 2026-08-15, and the first install created it on 2026-08-16. The path is
# therefore occupied but still convention-sourced — a Claude release that
# moved the directory would not be caught by anything here.
#
# Codex's was `~/.codex/skills/<name>/` until 0.24.x: OBSERVED on the
# authoring machine on 2026-08-15, never documented (public issue #1). The
# ruling (2026-09-21) is that OpenAI's documentation decides, and it names
# `$HOME/.agents/skills` as the USER scope. That is HOME-relative: CODEX_HOME
# does not move it. What was observed the same day is recorded separately in
# INSTALL_OBSERVED, because an observation is not an authority and the two
# must never read as one claim.
INSTALL = {
    "claude-skill": ("~/.claude/skills/%s/SKILL.md" % TOOL_NAME,
                     "conventional; first installed here 2026-08-16"),
    "codex-skill": ("~/.agents/skills/%s/SKILL.md" % TOOL_NAME,
                    "documented user-scope path: OpenAI, "
                    "https://learn.chatgpt.com/docs/build-skills, "
                    "retrieved 2026-09-21"),
}

# Observed behaviour, dated, beside the documented path — never instead of it.
INSTALL_OBSERVED = {
    "codex-skill": (
        "observed 2026-09-21 with codex-cli 0.155.0-alpha.9.2 "
        "(`codex debug prompt-input`, no model call): Codex lists skills "
        "from BOTH `$CODEX_HOME/skills` and `$HOME/.agents/skills`, and "
        "the same name in both is listed twice, so `render-adapters "
        "--install` moves a legacy `~/.codex/skills/%s/` copy out of "
        "discovery (kept, never deleted unkept)" % TOOL_NAME),
}

# Where earlier versions installed a kind, relative to a root that the
# environment decides (see `legacy_targets`). A copy found there is a
# second, stale discovery of the same skill name.
LEGACY_SKILL_RELATIVE = Path("skills") / TOOL_NAME / "SKILL.md"

def _claim_members() -> str:
    """The claim's members, from the grammar authority itself (round 7 F1) —
    a field added to `vocab.CLAIM_FIELDS` reaches every agent's copy of the
    procedure at the next render, and no adapter can list a member the
    boundary would refuse.

    0.25.0: a list-of-object member carries its own closed field table, so
    it is rendered WITH that table — required fields first, then optional —
    from `vocab.CLAIM_OBJECT_LIST_FIELDS`/`CLAIM_OBJECT_REQUIRED`, never
    restated here."""
    def member(m: str) -> str:
        if vocab.CLAIM_FIELDS[m] != "list_of_object":
            return f"`{m}`"
        required = vocab.CLAIM_OBJECT_REQUIRED[m]
        optional = [f for f in vocab.CLAIM_OBJECT_LIST_FIELDS[m]
                    if f not in required]
        fields = ", ".join(f"`{f}`" for f in required)
        if optional:
            fields += "; optional " + ", ".join(f"`{f}`" for f in optional)
        extra = "".join(
            f"; `{f}` is one of "
            f"{', '.join(f'`{o}`' for o in vocab.CARRIED_OUTCOMES)}"
            for f, kind in vocab.CLAIM_OBJECT_LIST_FIELDS[m].items()
            if kind == "carried_outcome")
        # 0.26.0: optional fields an entry declares together or not at all.
        extra += "".join(
            "; " + " and ".join(f"`{f}`" for f in group)
            + " are declared together"
            for group in vocab.CLAIM_OBJECT_TOGETHER.get(m, ()))
        return f"`{m}` (a list of objects: {fields}{extra})"
    return ", ".join(member(m) for m in sorted(vocab.CLAIM_FIELDS))


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
        "Review is the default end of an implementation session, and the "
        "relay to the reviewer is the human's. With `[roles] review_default "
        "= \"on\"` (absent counts as on) the author runs `handoff` unasked "
        "once the work is done — unless the user declined a review earlier "
        "in this session or said it needs none, in which case say so in one "
        "line and stop; a decline is per session and changes no config. "
        "The commands of your own side are yours to run, `handoff` included "
        "— it IS the author's emission step, never a step to hand back to "
        "the human, and nothing is emitted or validated until it exits 0. "
        "Whichever "
        "side you hold, you stop where the procedure says stop; you do not "
        "invoke the other side by any mechanism (CLI, subagent, hook, API). "
        "One exception, and it is never this tool's to grant: where the "
        "standing instructions you operate under allow an agent-relayed "
        "window AND the operator granted one for this lineage, a harness "
        "outside this tool carries the relay block to the reviewer, whole "
        "and unedited, and the request's Roles line names that harness as "
        "`relay=` instead of a person. Nothing else changes on either side: "
        "the author never writes or edits what the reviewer is handed, the "
        "reviewer takes a relay block that arrives that way exactly as one "
        "a person pasted, and stops at the validated verdict. "
        f"Two verbs are never yours: "
        f"`{paths.command(*paths.lits(TOOL_NAME, 'waive'))}` and "
        f"`{paths.command(*paths.lits(TOOL_NAME, 'authorize-advance'))}` "
        "record a HUMAN's decision that a finding stands unfixed, or that "
        "the lineage advances over findings still open. The tool cannot "
        "tell your hand from theirs — it carries the name you type — so "
        "running either yourself mints exactly the authorization this "
        "checkpoint exists to require. Report what you would say and let "
        "the human answer.",
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
        "Trust model. The tool has ONE principal: the operator, and every "
        "agent running a verb runs it for them. The ledger, the state "
        "directory, the clone and the remote it pushes to are the "
        "operator's own writable state, so a finding whose premise is that "
        "a party holding that write access forges a row, a ref, a source "
        "file or an envelope is OUTSIDE THE TAXONOMY: it describes the "
        "owner attacking their own record, which no mechanism in this tool "
        "can prevent and none is asked to. What the record is guarded "
        "against is OMISSION and FATIGUE — a finding that dies unanswered, "
        "an answer bound to the wrong ruling, a stale attestation, a stamp "
        "that misdescribes who did what. Rule on those. A forgery finding "
        "must name, in the finding, the party with LESS than operator "
        "access who could mount it; one that names none is not a finding "
        "for the round, and the author answers it by citing this rule, "
        "never by building a mechanism. Boundary closure above partitions "
        "the domain of MISTAKES a boundary admits, not the domain of "
        "adversaries.",
        "Absent config asks once. A result carrying a `decide` list has "
        "applied a built-in default for a key this repository never "
        "declared; each entry names the key, its meaning, the value "
        "applied, and the exact TOML line for `set` and for `unset`. Ask "
        "the user once per session, per key, in plain language, whether "
        "this project sets or unsets it, write their answer into "
        "`review.toml` as the printed line and commit it. Never ask about "
        "a declared key, never ask twice in a session, and never decide "
        "it yourself. A key whose only off state is to stay undeclared "
        "prints its `unset` as a comment line — `# decided: "
        "limits.token_budget undeclared` — and that line IS the answer: "
        "write it into `review.toml` under `[limits]` and the entry stops, "
        "while the key stays undeclared and uncounted. "
        f"`{paths.command(*paths.lits(TOOL_NAME, 'decide'))}` prints the "
        "same list on demand, and is the ONLY way to read it without "
        "emitting or recording anything: use it when nobody asked for a "
        "round — after a version pin moves, or when the user asks what "
        "this repository has left undeclared — and never run an emitting "
        "verb to find out.",
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
        "A paste-carried envelope travels INSIDE the one fenced block the "
        "tool printed, as the stdin of the command that consumes it: the "
        "request bytes ride under `take -` and the verdict bytes under "
        "`close --verdict -`, each opened as a quoted heredoc whose "
        "delimiter closes the block. The block IS the whole relay — "
        "nothing to retype beside it, and pasting it into a shell runs "
        "exactly the one command with the bytes as its input. Hand it "
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
         f"of strings; "
         f"{', '.join(f'`{m}`' for m in vocab.CLAIM_OBJECT_LIST_FIELDS)} are "
         f"non-empty lists of objects with the fields shown beside each; "
         f"the rest are strings; required: "
         f"{', '.join(f'`{m}`' for m in vocab.CLAIM_REQUIRED)}, and an "
         f"empty `{vocab.CLAIM_REFERENCES_FIELD}` list is refused like a "
         f"missing one. "
         f"Malformed JSON, a top-level value that is not an object, a "
         f"repeated member at any depth, a wrong type, and an unknown member "
         f"are each refused by name before the tool reads the ledger, "
         f"touches Git, runs a gate or records anything; a carried "
         f"fingerprint or attestation-map row that this machine's ledger "
         f"contradicts is refused after the ledger is read and still before "
         f"Git is touched, and a gate the target's manifest lacks at "
         f"emission, before any gate runs — an unknown member "
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
         "Under `[roles] enforcement = \"pr-approval\"` the work is on a "
         "branch, never the default one, and an open pull request exists "
         "for it before this step — open one with the forge's CLI if none "
         "does; the tool touches no forge and refuses the default branch. "
         "Commits outstanding tracked work, runs the local gates at that "
         "commit (a red blocking gate stops it there: nothing is pushed, "
         "and the local commit is named for you to amend), pushes the "
         "reviewed branch, awaits any CI-attested gates on the pushed "
         "commit, emits and validates the request, records it, "
         "keeps the bytes under the state directory, and prints the "
         "reviewer's command. `--base` is needed only for round 1 of a "
         "lineage. Re-running on an unchanged tip is a no-op that returns "
         "the same envelope. A debug round asks the reviewer to also "
         "critique the tool's own performance this round (efficiency, "
         "cost, accuracy) in a `## "
         f"{vocab.TOOL_FEEDBACK_SECTION}` verdict section, recorded at "
         "close — advisory, never a gate. It is declared, strongest "
         "first: `--debug`/`--no-debug` for this invocation, else a "
         "standing `[roles] debug = true` in the repository's config, "
         "else off. THEN STOP: hand the human the fenced block "
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
         "An `accepted` disposition follows this order, and only this "
         "order: (1) make the fix; (2) run the finding's named "
         "falsification test on the fixed head, which records "
         f"`payload.falsification.{status_key}` "
         f"({' | '.join(vocab.FALSIFICATION_STATUSES)}); (3) run the "
         "mutation — the same test with the defect reintroduced — and "
         f"restore, which records `.{mutation_key}` "
         f"({' | '.join(vocab.MUTATION_OUTCOMES)}), the proof the test "
         "can fail at all; (4) THEN write the disposition record with "
         "`--out`; (5) hand off again. A test that still fails, or one "
         "that passes without the fix, is refused as an acceptance; "
         "`cannot_execute` and `not_run` need a `note` saying why. A fix "
         "proposed but not made is `deferred` or `escalated`, never "
         "`accepted` — and a blocking finding cannot take `deferred`. "
         "`--out` records and keeps it; the next request carries the "
         "disposition ledger."),
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
         "request with the exact diff command. `--compact` returns "
         "pointers only instead — the kept path, its digest and byte size, "
         "lineage, round, target SHA, whether your checkout is that target, "
         "the précis, the target's `decide` list and the next command, with "
         "no request prose and no attestation table — for a reviewer that "
         "reads the kept file itself; its `decide` list is the default "
         "take's, and the ask-once rule applies to it the same way. The "
         "request's `Swept:` line "
         "names every path the author's hand-off committed and whether it "
         "sat inside the claim's `scope_paths`: a path swept OUTSIDE the "
         "declared scope, or an intent-to-add entry, is work the author may "
         "not have inspected — read it first. The request is printed with "
         "its attestation objects as ONE TABLE (gate, result, binding, tree, "
         "where it ran, who executed it, command, output digest); every "
         "other section is verbatim. `--full` prints the exact bytes instead, "
         "and the `kept` path always holds them — read that file when a "
         "finding turns on a field the table does not carry. The `head:` "
         "line says whether YOUR working tree is the target: when it reads "
         "NOT the target, files you open from the working tree are not the "
         "reviewed bytes — read the target through the diff command it "
         "prints. If it refuses, return its output to the human — "
         "never review a defective or unreachable envelope."),
        ("rule",
         None,
         "Read the diff with the printed command and rule by the declared "
         "taxonomy only. Findings ordered by severity, atomic (one claim per "
         "ID), each with every required field and a FALSIFICATION. Anchor "
         "the falsification test in the reviewed tree wherever the defect "
         "admits it; where the defect genuinely lives in a mutable artifact "
         "outside the tree — a PR body, an issue, a dashboard — say so in "
         "the finding itself, so a later `cannot_execute` reads as the "
         "anticipated outcome of a stated dependency rather than an author "
         "evasion, and the human deciding the `unverifiable` breaker has "
         "your own words that the dependency was known. Prior "
         "dispositions are answered under `## closures` by fingerprint. "
         "When a closure is `reclassified` and a finding of THIS verdict "
         "carries what remains of it, add a `Residue: <finding id>` line "
         "under that closure, so convergence counts the thread once instead "
         "of reading your narrowed residue as a new finding on the same "
         "anchor. "
         "Material you could not retrieve goes under `## unavailable "
         "references` and forbids a clean verdict. If the request stamps "
         f"`{vocab.DEBUG_ATTR}=\"{vocab.DEBUG_TOOL_FEEDBACK}\"`, this is a "
         "debug round: ALSO add a `## "
         f"{vocab.TOOL_FEEDBACK_SECTION}` section after `## evidence "
         "checked` — a self-critical account of how the TOOL could have "
         "served this round better, under three headings: efficiency "
         "(steps, reads or re-derivations the procedure wasted), cost "
         "(bytes, tokens and wall time you observed, against what the "
         "round needed), accuracy (anywhere an envelope, a gate result, a "
         "brief or a refusal misled you or made you check something twice). "
         "Critique the tool, never the diff — findings stay findings, and "
         "nothing in this section may soften or substitute for one. The "
         "section is advisory prose about the tool, recorded in the "
         "author's ledger at close; you may volunteer it on any round, and "
         "the stamp is what asks for it."),
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
         "under `## Verdict`, whole and unedited — it is the author's to "
         "run, and it is the whole relay. On a paste round it is ONE "
         "fence in which the close command consumes the verdict bytes as "
         "its own stdin; hand it over as one piece and never restate the "
         "bytes as loose prose (a chat surface rewrites unfenced text). "
         "On a path round hand the kept file's path with it. Add no "
         "prose around it. You do not start the next round, do not send "
         "it back for fix-and-resubmit, and do not decide whether it "
         "enters the record. One conditional coda, where the standing "
         "rules you operate under grant a clean-verdict approval: after "
         "your verdict validates as `clean to advance` and the reviewed "
         "branch has an open pull request, if the operator's approval "
         "credentials for the project's approval tooling are present on "
         "this machine, that grant covers recording the approving review "
         "through that tooling; if they are absent, say so once and point "
         "at the project's approval setup instead. The relay comes first "
         "either way and is never blocked on it."),
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
        head.append(f"Observed, separately: {INSTALL_OBSERVED[kind]}.")
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


def package_dir() -> Path:
    """The RUNNING package's own directory: what the machine-global modes
    name as their source when no `--dir` is given.

    It is named, never read. `--check-install` and `--install` without
    `--dir` compare and write `render(kind)` — the text this very package
    renders — so no directory's presence or contents can change their
    answer (0.25.0, the user's ruling 5 of 2026-09-21: no adopter step may
    depend on a same-machine scenario). Until 0.24.x the default source was
    `installation_root() / "adapters"`, a sibling a wheel install never
    has: on a machine holding only the adopting repository and the
    published pin the verb refused outright.
    """
    return Path(__file__).resolve().parent


def rendered_source() -> str:
    """How a row names a source that is the running package's rendering."""
    return f"rendered by {TOOL_NAME} {TOOL_VERSION} at {package_dir()}"


def install_targets() -> dict:
    """{kind: expanded path} — where each agent actually reads its copy.

    `INSTALL` stays the one authority for these paths (§6.3): the renderer
    prints them, the installer writes them, the drift check reads them, and
    nothing outside this module restates them.
    """
    return {kind: Path(target).expanduser()
            for kind, (target, _why) in INSTALL.items()}


def legacy_targets(targets: dict | None = None) -> dict:
    """{kind: [path, …]} — where an EARLIER version installed a kind that now
    installs elsewhere, so a copy found there is a second discovery of the
    same skill name.

    Codex only (public issue #1): `~/.codex/skills/loupe/SKILL.md`, and
    `$CODEX_HOME/skills/loupe/SKILL.md` when CODEX_HOME is set to an
    ABSOLUTE path that is not `~/.codex` (a relative one names no single
    place — Codex would resolve it against its own working directory — so
    it is ignored rather than guessed at). Candidates are de-duplicated by
    real path, and a candidate that IS an install target by real path (a
    symlinked `skills/` directory, say) is never a legacy copy: moving it
    would remove the skill just installed.

    THE INTERLOCK. Legacy candidates exist only when the codex-skill target
    in `targets` is the documented path itself. A test that substitutes its
    own targets — by argument or by patching `install_targets` — therefore
    reaches no legacy path under the real HOME, whatever else it forgets to
    patch; the only way to exercise migration is to redirect HOME, which
    moves the documented target and its legacy siblings together.
    """
    targets = install_targets() if targets is None else targets
    codex = targets.get("codex-skill")
    if codex is None or Path(codex) != Path(
            INSTALL["codex-skill"][0]).expanduser():
        return {}
    roots = [Path("~/.codex").expanduser()]
    env = os.environ.get("CODEX_HOME", "")
    if env and Path(env).expanduser().is_absolute():
        roots.append(Path(env).expanduser())
    protected = {os.path.realpath(t) for t in targets.values()}
    seen: set = set()
    found = []
    for root in roots:
        candidate = root / LEGACY_SKILL_RELATIVE
        key = os.path.realpath(candidate)
        if key in seen or key in protected:
            continue
        seen.add(key)
        found.append(candidate)
    return {"codex-skill": found} if found else {}


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


def _select_legacy(targets: dict, legacy: dict | None) -> dict:
    """`None` derives the legacy candidates from `targets` (see the
    interlock in `legacy_targets`); an explicit mapping means exactly
    those, and `{}` means none."""
    return legacy_targets(targets) if legacy is None else dict(legacy)


def _source(directory: Path | None, kind: str) -> tuple[str, str | None]:
    """(label, text) for one kind. With no directory the text is this
    package's rendering and cannot fail; with one, it is the file there,
    read by the caller (text None)."""
    if directory is None:
        return rendered_source(), render(kind)
    return str(directory / OUTPUTS[kind]), None


def _legacy_state(path: Path) -> dict | None:
    """None when no legacy copy is there; otherwise `legacy_present` (with
    its BYTES, `data`, and their digest) or `legacy_blocked` (with the
    reason).

    Only loupe's own directory is ever touched, and only when it holds
    exactly one regular file, `SKILL.md`. Anything else there — another
    file, a subdirectory, a symbolic link at either level, bytes that do not
    read as UTF-8 — is a state a person sorts out: the installer will not
    guess which of two things in a skill directory the operator meant to
    keep.

    The copy is read as bytes and carried as bytes (round-1 F2 of the 0.25.0
    review). It was read with `read_text`, whose universal-newline decoding
    turns CRLF and a lone CR into LF, so a legacy file carried over with
    other line endings was kept, digested and reported as a transformed
    copy — and then the original was deleted. UTF-8 is still REQUIRED (a
    strict decode, newline-neutral, whose result is discarded): what is
    moved out of discovery must be a skill file this tool can name, and an
    undecodable one stays where it is.
    """
    path = Path(path)
    directory = path.parent
    if not os.path.lexists(path):
        return None
    if os.path.islink(directory) or os.path.islink(path):
        return {"status": "legacy_blocked",
                "error": f"{path if os.path.islink(path) else directory} is "
                         f"a symbolic link: removing through it could "
                         f"remove what it points at"}
    if not path.is_file():
        return {"status": "legacy_blocked",
                "error": f"{path} is not a regular file"}
    try:
        entries = sorted(os.listdir(directory))
    except OSError as exc:
        return {"status": "legacy_blocked",
                "error": f"{directory} cannot be listed: {exc}"}
    extra = [e for e in entries if e != "SKILL.md"]
    if extra:
        return {"status": "legacy_blocked",
                "error": f"{directory} holds {', '.join(extra)} besides "
                         f"SKILL.md; only a directory holding loupe's own "
                         f"SKILL.md alone is ever moved"}
    try:
        data = path.read_bytes()
        data.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        return {"status": "legacy_blocked",
                "error": f"{path} cannot be read as UTF-8: {exc}"}
    return {"status": "legacy_present", "data": data,
            "target_digest": _sha256_bytes(data)}


def _legacy_rows(legacy: dict) -> list:
    """(kind, path, state) for every legacy copy actually present."""
    found = []
    for kind, candidates in sorted(legacy.items()):
        for path in candidates:
            state = _legacy_state(Path(path))
            if state is not None:
                found.append((kind, Path(path), state))
    return found


def check_install(directory: Path | None, targets: dict | None = None,
                  legacy: dict | None = None) -> list[dict]:
    """Per install target, one of six statuses, against the source — the
    running package's rendering when `directory` is None (the no-`--dir`
    default since 0.25.0), else the rendered file under `directory`.

    `kind`, `source`, `target` and `status` are common to every row (RVW-T21
    lineage 15 round 2 F2: an earlier revision of this text claimed
    source-side and target-side fields were mutually exclusive, which these
    four being shared by both already contradicts — `status` is what tells
    the two apart, not field presence). Beyond those four, per status,
    exactly:

    - `source_absent` — nothing more; no file at `directory / OUTPUTS[kind]`
      (reachable only with a directory)
    - `source_unreadable` — `error` (the file exists but could not be read;
      reachable only with a directory)
    - `absent` — `source_digest` (no file at `target`)
    - `unreadable` — `source_digest`, `error` (the installed copy exists but
      could not be read)
    - `in_sync` / `stale` — `source_digest`, `target_digest`

    Then one row per LEGACY copy present (`legacy_targets`), after the
    target rows: `legacy_present` (with `target_digest` and
    `superseded_by`, the documented path that replaces it) — a second
    discovery of the same skill, which is drift that `--install` repairs —
    or `legacy_blocked` (with `error`), a copy `--install` refuses to move.
    An absent legacy copy produces no row: it is the state wanted.

    `render-adapters --check` guards the TRACKED copies; it says nothing
    about the ones an agent loads, and on 2026-08-19 the installed Claude
    skill was found several rounds behind — missing the falsification record
    the `respond` step now requires, the `waive` verb, and the current `take`
    text. An agent reading a stale procedure is the one drift no gate here
    could see. This is deliberately NOT in the gate manifest: a machine that
    has never installed the skill is not a broken build.
    """
    targets = _select(targets)
    legacy = _select_legacy(targets, legacy)
    rows = []
    for kind, target in targets.items():
        label, wanted = _source(directory, kind)
        if wanted is None:
            source = directory / OUTPUTS[kind]
            # RVW-T21 D1: a source-side failure must name the SOURCE, with
            # a status that says which side it is — never a row a reader
            # confuses with a failure of the healthy installed file.
            if not source.is_file():
                rows.append({"kind": kind, "source": label,
                             "target": str(target), "status": "source_absent"})
                continue
            try:
                wanted = source.read_text(encoding="utf-8")
            except OSError as exc:
                rows.append({"kind": kind, "source": label,
                             "target": str(target),
                             "status": "source_unreadable", "error": str(exc)})
                continue
        row = {"kind": kind, "source": label, "target": str(target),
               "source_digest": sha256_text(wanted)}
        if not target.is_file():
            row["status"] = "absent"
        else:
            # Bytes, as `install_all` judges them: `in_sync` exactly when
            # `--install` would leave the file alone, and a digest of what
            # is on disk rather than of a newline-translated reading of it.
            try:
                current = target.read_bytes()
            except OSError as exc:
                row["status"] = "unreadable"
                row["error"] = str(exc)
                rows.append(row)
                continue
            row["target_digest"] = _sha256_bytes(current)
            row["status"] = ("in_sync" if current == wanted.encode("utf-8")
                             else "stale")
        rows.append(row)
    for kind, path, state in _legacy_rows(legacy):
        row = {"kind": kind, "source": _source(directory, kind)[0],
               "target": str(path), "status": state["status"],
               "superseded_by": str(targets[kind])}
        if "error" in state:
            row["error"] = state["error"]
        else:
            row["target_digest"] = state["target_digest"]
        rows.append(row)
    return rows


def install_all(directory: Path | None, targets: dict | None = None,
                keep_dir: Path | None = None,
                legacy: dict | None = None) -> list[dict]:
    """Copy each adapter to where its agent reads it — this package's own
    rendering when `directory` is None, else the rendered file under it —
    then move every legacy copy of it out of discovery.

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

    LEGACY COPIES (0.25.0, public issue #1). Every legacy copy is
    classified BEFORE anything is written: one that cannot be moved
    (`_legacy_state`: an extra entry beside SKILL.md, a symbolic link, bytes
    that do not read) makes every row `blocked` and writes nothing at all.
    Otherwise, after its kind's documented target is in place (installed,
    replaced or unchanged — never while that write failed, which would leave
    no discoverable copy), each legacy copy is preserved through the same
    `_preserve` a replacement uses, then its SKILL.md and the now-empty
    `loupe` directory are removed: status `migrated`, with `kept`. A
    retention failure leaves the legacy copy where it was (`failed`,
    `preserved: False`), exactly as it leaves a replaced target.

    BYTES, BOTH CALLERS (round-1 F2 of the 0.25.0 review). What is kept,
    digested and compared is what is on disk, never a newline-translated
    reading of it: a replaced target and a legacy copy are read with
    `read_bytes`, `_preserve` writes and proves those bytes, and the target
    is written with `write_bytes`. So "unchanged" means the target carries
    the rendered bytes exactly — a copy differing only in line endings is
    `replaced` (its bytes kept), as `check_install` calls it `stale` — and
    a legacy copy is removed only after the bytes kept are proven equal to
    the bytes it holds at that moment, re-read just before the removal.
    """
    targets = _select(targets)
    legacy = _select_legacy(targets, legacy)
    found = _legacy_rows(legacy)
    blocked = [(kind, path, state) for kind, path, state in found
               if state["status"] == "legacy_blocked"]
    if blocked:
        return [{"kind": kind, "source": _source(directory, kind)[0],
                 "target": str(path), "status": "blocked",
                 "superseded_by": str(targets[kind]), "error": state["error"]}
                for kind, path, state in blocked]
    rows = []
    for kind, target in sorted(targets.items()):
        label, wanted = _source(directory, kind)
        row = {"kind": kind, "source": label, "target": str(target)}
        if wanted is None:
            try:
                wanted = (directory / OUTPUTS[kind]).read_text(
                    encoding="utf-8")
            except OSError as exc:
                rows.append({**row, "status": "failed", "error": str(exc)})
                continue
        row["digest"] = sha256_text(wanted)
        wanted_bytes = wanted.encode("utf-8")
        current = None
        if target.is_file():
            try:
                current = target.read_bytes()
            except OSError as exc:
                rows.append({**row, "status": "failed", "error": str(exc)})
                continue
        if current == wanted_bytes:
            rows.append({**row, "status": "unchanged"})
            continue
        if current is not None:
            row["replaced_digest"] = _sha256_bytes(current)
            try:
                row["kept"] = _preserve(kind, current, keep_dir)
            except RetentionFailed as exc:
                rows.append({**row, "status": "failed", "error": str(exc),
                             "preserved": False})
                continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(wanted_bytes)
        except OSError as exc:
            rows.append({**row, "status": "failed", "error": str(exc)})
            continue
        rows.append({**row,
                     "status": "replaced" if current is not None
                     else "installed"})
    placed = {r["kind"] for r in rows
              if r["status"] in ("installed", "replaced", "unchanged")}
    for kind, path, state in found:
        row = {"kind": kind, "source": _source(directory, kind)[0],
               "target": str(path), "superseded_by": str(targets[kind]),
               "replaced_digest": state["target_digest"]}
        if kind not in placed:
            rows.append({**row, "status": "failed",
                         "error": f"not moved: {targets[kind]} was not "
                                  f"written, and removing this copy would "
                                  f"leave no discoverable one"})
            continue
        try:
            row["kept"] = _preserve(kind, state["data"], keep_dir)
        except RetentionFailed as exc:
            rows.append({**row, "status": "failed", "error": str(exc),
                         "preserved": False})
            continue
        # The copy was read before any target was written; what is removed
        # must be what was kept, so it is read again at the last moment and
        # compared byte for byte. A copy that changed in between is left
        # where it is, and its earlier bytes stay kept.
        try:
            unchanged = path.read_bytes() == state["data"]
        except OSError:
            unchanged = False
        if not unchanged:
            rows.append({**row, "status": "failed",
                         "error": f"not moved: {path} no longer holds the "
                                  f"bytes read before the install, so "
                                  f"{row['kept']} is not a copy of what "
                                  f"removing it would delete"})
            continue
        try:
            path.unlink()
            path.parent.rmdir()
        except OSError as exc:
            rows.append({**row, "status": "failed",
                         "error": f"kept at {row['kept']}, but could not be "
                                  f"removed from discovery: {exc}"})
            continue
        rows.append({**row, "status": "migrated"})
    return rows


def _write_synced(target: Path, data: bytes) -> None:
    """Write `data` to `target` as bytes — no encoding, no newline
    translation — flushed and fsynced. One seam, so a test can stand in for
    a filesystem that stores something other than what it was given."""
    with open(target, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def _preserve(kind: str, data: bytes, keep_dir: Path | None) -> str:
    """Write `data` where it can be recovered from, prove it is there, and
    return the path — or raise RetentionFailed, which stops the replacement.

    Every state of the destination is answered, and only one of them is a
    success that did not just write the file: a kept copy that already holds
    exactly these bytes. A copy that holds DIFFERENT bytes under the name of
    this digest is not evidence of anything, so it refuses rather than
    assuming its own convention held.

    BYTES, never text (round-1 F2 of the 0.25.0 review). The name is the
    digest of the bytes given, and both proofs — an existing copy, and the
    read-back of a fresh one — compare `read_bytes()` with them. Comparing
    decoded text proved nothing about line endings: `read_text` translates
    CRLF and a lone CR to LF, so a kept copy with other endings than the
    original read back "equal" and licensed deleting the original.
    """
    if keep_dir is None:
        raise RetentionFailed(
            "no retention directory is configured, so the bytes this install "
            "would replace could not be kept anywhere")
    target = Path(keep_dir) / f"{kind}-{_sha256_bytes(data)[:12]}.md"
    try:
        if target.is_file():
            if target.read_bytes() == data:
                return str(target)
            raise RetentionFailed(
                f"{target} already exists and holds different bytes, so it is "
                f"not a copy of what this install would replace")
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_synced(target, data)
    except RetentionFailed:
        raise
    except (OSError, UnicodeError) as exc:
        # Round-10 F1: every way of failing to read or write what is at the
        # destination is one refusal, never an escape that takes the whole
        # installer result with it. Nothing here decodes any more (a kept
        # path holding non-UTF-8 bytes is simply different bytes), but the
        # handler stays as wide as that ruling made it.
        raise RetentionFailed(f"could not keep the replaced bytes at "
                              f"{target}: {exc}") from exc
    try:
        written = target.read_bytes()
    except OSError as exc:
        raise RetentionFailed(f"kept {target} could not be read back to "
                              f"prove it: {exc}") from exc
    if written != data:
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


# ------------------------------------------------ an embedded instruction block
#
# Public issue #5. A repository that carries `instructions-block` INSIDE a
# tracked file (the BEGIN/END GENERATED region of an AGENTS.md or CLAUDE.md)
# had no verb that said whether that region equals the current rendering, and
# `docs/upgrading.md` §4 gave a sed-and-diff recipe by hand. The region is
# located LEXICALLY, by the rendering's own marker lines, and nothing else:
#
#   - a marker is a line that BEGINS, at column 0, with the BEGIN or the END
#     prefix below — no Markdown is parsed, so a marker line quoted at column
#     0 inside a fenced example counts as a marker and makes the structure
#     ambiguous, which is refused by name rather than guessed through (indent
#     the example to keep it out);
#   - the region runs from the first byte of the BEGIN line through the end
#     of the END line's own terminator, so a region that ends the file with
#     no final newline differs from the rendering, whose END line has one;
#   - a region carrying a carriage return is refused (`crlf`): byte equality
#     with an LF rendering cannot hold, and writing LF lines into a CRLF
#     region would mix the file's line endings. Bytes OUTSIDE the region are
#     never judged, never rewritten.
EMBEDDED_KIND = "instructions-block"
EMBEDDED_BEGIN = f"<!-- BEGIN GENERATED: {TOOL_NAME} adapter"
EMBEDDED_END = f"<!-- END GENERATED: {TOOL_NAME} adapter"


class EmbeddedRefusal(Exception):
    """A file whose embedded region cannot be judged, named by `status`.

    `usage` marks the two states where the argument names no file at all
    (absent, or not a regular file): exit 2. Every other refusal is a state
    of an existing file: exit 1. None of them has a command that repairs it,
    so each is blocked with a remedy."""

    def __init__(self, status: str, message: str, remedy: str,
                 usage: bool = False, data: dict | None = None):
        super().__init__(message)
        self.status, self.remedy, self.usage = status, remedy, usage
        self.data = data or {}


def _embedded_file(given: Path) -> tuple[Path, bool]:
    """(the file actually read and written, whether a link was followed).

    Only the LAST component decides "followed": a symlinked file such as
    `CLAUDE.md -> AGENTS.md` is followed to its target, and the result says
    so. A parent directory that happens to be a link (macOS `/tmp`) is not a
    followed file and is not reported as one."""
    if not os.path.lexists(given):
        raise EmbeddedRefusal(
            "absent", f"{given} does not exist",
            "name an existing file that carries the embedded block",
            usage=True)
    followed = os.path.islink(given)
    target = Path(os.path.realpath(given)) if followed else given
    if followed and not os.path.lexists(target):
        raise EmbeddedRefusal(
            "absent", f"{given} is a symbolic link to {target}, which does "
                      f"not exist",
            "name an existing file that carries the embedded block",
            usage=True)
    try:
        mode = os.stat(target).st_mode
    except OSError as exc:
        raise EmbeddedRefusal(
            "unreadable", f"{target} cannot be examined: {exc}",
            "a person makes the file readable, then re-runs this command"
        ) from exc
    if stat.S_ISDIR(mode):
        raise EmbeddedRefusal(
            "directory", f"{target} is a directory, not a file",
            "name the instruction FILE that carries the block, e.g. "
            "AGENTS.md", usage=True)
    if not stat.S_ISREG(mode):
        # Checked BEFORE any open: a FIFO would block the read forever.
        raise EmbeddedRefusal(
            "not_a_file", f"{target} is not a regular file",
            "name the instruction FILE that carries the block", usage=True)
    return target, followed


def embedded_region(path) -> dict:
    """Read `path` (following a symlinked file) and locate its one region.

    Returns {"path", "target", "followed", "text", "start", "end",
    "begin_line", "end_line"} — `start`/`end` are character offsets into
    `text`, lines are 1-based — or raises EmbeddedRefusal naming the defect:
    `absent`, `directory`, `not_a_file`, `unreadable`, `not_utf8`,
    `missing_markers`, `unbalanced_begin` (BEGIN without END),
    `unbalanced_end` (END without BEGIN), `end_before_begin`, `nested` (a
    BEGIN inside an open region), `duplicated` (two regions), `crlf`.
    """
    given = Path(path)
    target, followed = _embedded_file(given)
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise EmbeddedRefusal(
            "unreadable", f"{target} cannot be read: {exc}",
            "a person makes the file readable, then re-runs this command"
        ) from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EmbeddedRefusal(
            "not_utf8", f"{target} is not UTF-8 ({exc})",
            "a person re-encodes the file as UTF-8; the tool will not guess "
            "an encoding for bytes it would then rewrite") from exc
    begins, ends, marks = [], [], []
    offset = 0
    for number, line in enumerate(text.split("\n"), 1):
        if line.startswith(EMBEDDED_BEGIN):
            begins.append(number)
            marks.append(("BEGIN", number, offset))
        elif line.startswith(EMBEDDED_END):
            ends.append(number)
            marks.append(("END", number, offset))
        offset += len(line) + 1
    data = {"begin_lines": begins, "end_lines": ends}
    where = (f"BEGIN at line(s) {begins or 'none'}, END at line(s) "
             f"{ends or 'none'}")
    remedy = ("a person repairs the markers so the file holds exactly one "
              "BEGIN line followed by one END line, each at column 0 "
              "(indent any quoted example of a marker), then re-runs this "
              "command")
    if not marks:
        raise EmbeddedRefusal(
            "missing_markers", f"{target} carries no embedded {TOOL_NAME} "
                               f"adapter block: no line begins with "
                               f"{EMBEDDED_BEGIN!r} or {EMBEDDED_END!r}",
            "paste the rendered instructions-block (both marker lines "
            "included) where the block belongs, or name the file that "
            "carries it", data=data)
    regions, open_at = [], None
    for kind, number, at in marks:
        if kind == "BEGIN":
            if open_at is not None:
                raise EmbeddedRefusal(
                    "nested", f"{target}: a BEGIN at line {number} opens "
                              f"inside the region BEGUN at line "
                              f"{open_at[0]} ({where})", remedy, data=data)
            open_at = (number, at)
        elif open_at is None:
            # An END before any region, with a BEGIN still to come, is the
            # markers in the wrong order; any other stray END is unbalanced.
            status = ("end_before_begin"
                      if not regions and any(n > number for n in begins)
                      else "unbalanced_end")
            raise EmbeddedRefusal(
                status, f"{target}: an END at line {number} closes no open "
                        f"region ({where})", remedy, data=data)
        else:
            end = text.find("\n", at)
            end = len(text) if end < 0 else end + 1
            regions.append((open_at[0], number, open_at[1], end))
            open_at = None
    if open_at is not None:
        raise EmbeddedRefusal(
            "unbalanced_begin", f"{target}: the BEGIN at line {open_at[0]} "
                                f"is never closed ({where})", remedy,
            data=data)
    if len(regions) > 1:
        raise EmbeddedRefusal(
            "duplicated", f"{target} carries {len(regions)} embedded "
                          f"regions ({where}); which one governs is not "
                          f"this tool's to guess", remedy, data=data)
    begin_line, end_line, start, end = regions[0]
    if "\r" in text[start:end]:
        raise EmbeddedRefusal(
            "crlf", f"{target}: the region (lines {begin_line}-{end_line}) "
                    f"carries carriage returns; the rendering is LF-only, "
                    f"so byte equality cannot hold and a rewrite would mix "
                    f"the file's line endings",
            "a person converts the file (or at least the region) to LF "
            "line endings, then re-runs this command",
            data={**data, "begin_line": begin_line, "end_line": end_line})
    return {"path": str(given), "target": str(target), "followed": followed,
            "text": text, "start": start, "end": end,
            "begin_line": begin_line, "end_line": end_line,
            "data": {"begin_line": begin_line, "end_line": end_line}}


def check_embedded(path) -> dict:
    """The region of `path` against the running package's rendering:
    the `embedded_region` record plus `status` (`current` or `stale`),
    `source`, `source_digest` and `digest` (the region's)."""
    info = embedded_region(path)
    wanted = render(EMBEDDED_KIND)
    region = info["text"][info["start"]:info["end"]]
    return {**info, "source": rendered_source(),
            "source_digest": sha256_text(wanted),
            "digest": sha256_text(region),
            "status": "current" if region == wanted else "stale"}


def write_embedded(path) -> dict:
    """Replace exactly the region's bytes with the rendering; every byte
    outside it is written back as it was read. A current region is not
    rewritten at all (`current`); a stale one becomes `written`.

    The new bytes go to a temporary file beside the target, are flushed and
    fsynced, take the target's permission bits, and replace it atomically;
    then the file is read back and must equal what was intended. Every
    structural refusal `check_embedded` raises is raised here first, before
    anything is written. The replaced region is not retained: the file is a
    tracked instruction file, and git is where its prior bytes live."""
    info = check_embedded(path)
    if info["status"] == "current":
        return info
    target = Path(info["target"])
    text = info["text"]
    new = text[:info["start"]] + render(EMBEDDED_KIND) + text[info["end"]:]
    tmp = None
    try:
        mode = stat.S_IMODE(os.stat(target).st_mode)
        fd, tmp = tempfile.mkstemp(dir=target.parent,
                                   prefix=f".{target.name}.")
        with os.fdopen(fd, "wb") as fh:
            fh.write(new.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, target)
        tmp = None
    except OSError as exc:
        raise EmbeddedRefusal(
            "unwritable", f"{target} could not be rewritten: {exc}",
            "a person makes the file and its directory writable, then "
            "re-runs this command; the file was left as it was") from exc
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    if target.read_bytes() != new.encode("utf-8"):
        raise EmbeddedRefusal(
            "unwritable", f"{target} does not read back as the bytes "
                          f"written",
            "a person inspects the file; the replaced region is in its "
            "version history")
    return {**info, "status": "written",
            "digest": info["source_digest"]}

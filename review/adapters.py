"""Per-agent adapters, generated from one source (design §6.3, RVW-T10).

The agents' standing instruction files may not name this tool — that is a
verified property, and it is what lets them keep working with nothing
installed. Adapters are the
exception: they carry the literal command lines (§9bis.3 rule 7) so an agent
copies rather than improvises, and they install at USER level so no shared
repository needs an instruction-file edit.

One source renders every adapter: `procedure()` below (what each side does,
in order, and what it never does) plus the verb names read from the CLI's own
parser, so an adapter cannot name a verb the tool does not have or omit one
it does. `render-adapters --check` byte-guards the rendered files and is
a gate.

Rendered kinds:
  claude-skill        adapters/claude/SKILL.md   → ~/.claude/skills/loupe/
  codex-skill         adapters/codex/SKILL.md    → ~/.agents/skills/loupe/
  <skill>-author      adapters/<agent>/author.md   → beside that SKILL.md
  <skill>-reviewer    adapters/<agent>/reviewer.md → beside that SKILL.md
                      (RR8: a skill's SKILL.md carries the rules that hold
                      on both sides and routes to its two role files, one
                      per stamp, read on demand; `SKILL_ROLE_KINDS`)
  <skill>-<rare>      adapters/<agent>/<rare>.md → beside that SKILL.md, read
                      only on the trigger the routing names; `RARE_FILES`
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

#: The two sides of the procedure, in the order the instruction block
#: carries them.
ROLES = ("author", "reviewer")

#: RR8 (the determinism and efficiency pass, 2026-10-03): a skill surface
#: splits by role. `SKILL.md` keeps what holds on both sides and a routing
#: line; each side's procedure is a sibling file in the same skill
#: directory, read on demand, so a reviewer round no longer loads the
#: author's steps (4,893 bytes on the Codex skill, measured 2026-10-04).
#: The agent-relayed-window clause is a both-sides rule, so it stays in
#: `SKILL.md`, the one file a reader outside this tool greps for it
#: (`docs/upgrading.md` §6). Each role file is its own kind with its
#: own install target, so the install, the drift check and the retention
#: of a replaced copy treat it exactly as they treat `SKILL.md`. The
#: instruction block has no directory to hold siblings and stays whole.
SKILL_ROLE_KINDS = {
    surface: {role: f"{surface}-{role}" for role in ROLES}
    for surface in ("claude-skill", "codex-skill")}
for _surface, _kinds in SKILL_ROLE_KINDS.items():
    _target, _why = INSTALL[_surface]
    for _role, _kind in _kinds.items():
        INSTALL[_kind] = (_target.rsplit("/", 1)[0] + f"/{_role}.md", _why)
del _surface, _kinds, _target, _why, _role, _kind

#: 2026-11-04: what a reader needs only on a trigger leaves `SKILL.md` for a
#: file of its own beside it, named in the routing section with its trigger
#: (`tool-feedback` in the reviewer's `rule` step), so most sessions never
#: load it. Each is its own kind with its own install target, as a role file
#: is. The instruction block carries them as sections instead. These names
#: are the keys of `procedure()["rare"]`.
RARE_FILES = ("decide", "carrier", "closure-authority", "tool-feedback")
SKILL_RARE_KINDS = {
    surface: {name: f"{surface}-{name}" for name in RARE_FILES}
    for surface in SKILL_ROLE_KINDS}
for _surface, _kinds in SKILL_RARE_KINDS.items():
    _target, _why = INSTALL[_surface]
    for _name, _kind in _kinds.items():
        INSTALL[_kind] = (_target.rsplit("/", 1)[0] + f"/{_name}.md", _why)
del _surface, _kinds, _target, _why, _name, _kind

#: Every surface an agent reads the procedure from, by its main kind.
SURFACES = ("claude-skill", "codex-skill", "instructions-block")

# Observed behaviour, dated, beside the documented path — never instead of it.
# Held here for whoever changes the install path; since 2026-11-04 no adapter
# prints it, because it is for people and `docs/upgrading.md` carries it.
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


def _claim_values() -> str:
    """The claim's value grammars, one clause per kind in
    `vocab.CLAIM_VALUE_GRAMMAR`, each naming the members of that kind as
    `vocab.CLAIM_LIST_VALUE_KINDS` and `vocab.CLAIM_OBJECT_LIST_FIELDS`
    declare them (2026-11-01): the member list says which fields exist,
    and this says what their values look like, so an author learns the
    grammar before a refusal teaches it one defect at a time.

    A field is named by its own name, which the member list just placed
    inside its member; one whose name is also a top-level member
    (`objectives[].references` beside the top-level `references`) is
    qualified, so the two are never read as one."""
    names: dict[str, list[str]] = {kind: [] for kind in
                                   vocab.CLAIM_VALUE_GRAMMAR}
    for member, kind in vocab.CLAIM_LIST_VALUE_KINDS.items():
        if kind in names and kind != "string":
            names[kind].append(f"`{member}`")
    for member, table in vocab.CLAIM_OBJECT_LIST_FIELDS.items():
        for field, kind in table.items():
            if kind in names and kind != "string":
                names[kind].append(f"`{member}[].{field}`"
                                   if field in vocab.CLAIM_FIELDS
                                   else f"`{field}`")
    clauses = []
    for kind, phrase in vocab.CLAIM_VALUE_GRAMMAR.items():
        if kind == "string":
            clauses.append(f"strings in an object: {phrase}")
        elif names[kind]:
            clauses.append(f"{', '.join(names[kind])}: {phrase}")
    return "Values — " + "; ".join(clauses) + "."


def _judge_reading() -> list[str]:
    """THE SLOT for how a reviewer first reads a round, which the code around
    `take` decides: lines returned here open the `rule` step on every
    surface, ahead of the reading order written there. It returns none, so
    nothing renders; the change that alters that reading (track `om-judge`,
    2026-11-04) fills it."""
    return []


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
    waive = paths.command(*paths.lits(TOOL_NAME, "waive"))
    authorize = paths.command(*paths.lits(TOOL_NAME, "authorize-advance"))
    required = ", ".join(
        f"`{m}`" + (" (non-empty)" if m == vocab.CLAIM_REFERENCES_FIELD else "")
        for m in vocab.CLAIM_REQUIRED)
    optional_refs = sorted(set(vocab.CLAIM_REFERENCE_FIELDS)
                           - set(vocab.CLAIM_REFERENCE_REQUIRED))
    return {
    "both": [
        "**Results.** Off a TTY, output is JSON: read its fields. Exit 0 is "
        "ok, 1 is findings or a refusal, 2 is usage. Every non-zero exit "
        "carries `next_kind`. `command`: run `next` verbatim. `blocked`: "
        "read `remedy`. If it is yours to do, because it names your side or "
        "a file or commit you wrote, do it and re-run. Otherwise give the "
        "human the remedy and wait. Add no flag or fix those fields don't "
        "name.",
        "**The record.** The tool writes envelopes, the ledger and gate "
        "attestations, so don't hand-type an envelope, edit the ledger or "
        "paste gate output into Evidence.",
        "**Ending a session.** With `[roles] review_default = \"on\"` "
        "(absent counts as on), the author runs `handoff` unasked once the "
        "work is done, unless the user declined a review this session or "
        "said it needs none; then say so in one line and stop. A decline "
        "changes no config. `handoff` is the author's to run, never the "
        "human's, and nothing is emitted until it exits 0.",
        "**The relay is the human's.** Stop where your side's procedure "
        "says, and don't invoke the other side by any mechanism (CLI, "
        "subagent, hook, API). One exception, never this tool's to grant: "
        "the standing instructions you operate under allow an agent-relayed "
        "window AND the operator granted one for this lineage. Then a "
        "harness outside this tool carries the relay block to the reviewer, "
        "whole and unedited, and the request's Roles line names that "
        "harness as `relay=` instead of a person, which the author declares "
        "by setting the claim's `relay` member to the harness's name. "
        "Nothing else changes: the author never writes or edits what the "
        "reviewer is handed, and the reviewer takes that block as one a "
        "person pasted and stops at the validated verdict.",
        "**Two verbs are never yours unless the standing instructions you "
        f"operate under say otherwise.** `{waive}` and `{authorize}` record "
        "a human's decision that a commit goes unreviewed, that a finding "
        "stands unfixed, or that the lineage advances over open findings. "
        "The tool carries whatever name you type, so running either on your "
        "own mints the authorization this checkpoint exists to require. "
        "Where those instructions allow it, run either only to record "
        "exactly what the human selected in an interactive prompt this "
        "session, under their name. Otherwise report what you would say and "
        "let the human answer.",
        "**Brief, then relay, exactly.** Every request and verdict result "
        "carries `brief` and `relay`. Give the human both, in that order, "
        "every time. `brief` is readable prose, never in a code block. "
        "`relay` is the literal commands with the fences it already has: "
        "add no fence around it and put nothing of yours inside it. Don't "
        "merge, reorder, summarise or paraphrase either; both derive from "
        "the envelope and yours would not. A relay may carry envelope bytes "
        "as a heredoc in one fence; hand that over whole, since a chat "
        "surface rewrites anything outside a fence. "
        f"`{paths.command(*paths.lits(TOOL_NAME, 'brief'))}` reprints both, "
        "and with no argument finds the open request.",
        "**The carrier.** The relay prints the one carrier this round "
        "declared. The tool resolves it from declarations, so don't detect "
        "it, and don't rewrite a command for another carrier.",
        "**Boundary closure.** When a finding is about a parser, a grammar "
        "or a lifecycle seam, the answer partitions the complete admitted "
        "domain: every input kind the boundary accepts, every declared "
        "field and nested shape, every ordering the seam can be reached by, "
        "with paired valid controls proving the check is live. The proof a "
        "test can fail is the one run with the defect back; neither side "
        "asks for a mutation per guard or per bypass. A green reproducer is "
        "one point of that domain; both sides judge an acceptance or a closure "
        "by whether the domain is closed. A gap no plausible input reaches "
        "demands no partition: the reviewer reports it at Low and says so. "
        "A domain that can't be shown "
        "complete from inside the artifact has one terminator: "
        "`closure-authority.md`.",
        "**Trust model.** The tool has one principal, the operator, and "
        "every agent runs its verbs for them. The ledger, state directory, "
        "clone and remote are the operator's own writable state, so forging "
        "a row, ref, source file or envelope with that access is outside "
        "the taxonomy: it describes the owner attacking their own record. "
        "The record guards against omission and fatigue: a finding that "
        "dies unanswered, an answer bound to the wrong ruling, a stale "
        "attestation, a stamp that misdescribes who did what. Rule on "
        "those. A forgery finding names the party with less than operator "
        "access who could mount it; one that names none is not a finding "
        "for the round, and the author answers it by citing this rule, not "
        "by building a mechanism. Boundary closure partitions the mistakes "
        "a boundary admits, not adversaries.",
    ],
    "finish": {
        "author":
        "A round is done on your side when `handoff` exits 0 and the human "
        "has its `brief` and `relay`. A refusal is not that stop: follow "
        "`next_kind`, as the both-sides rules say.",
        "reviewer":
        "You are done when `validate` exits 0 and the human has the `brief` "
        "and the `relay`. Until then, keep going.",
    },
    "author": [
        ("write the claim",
         None,
         "A JSON file holding your judgment; the tool carries it and never "
         "invents it. Its grammar is closed: every defect is refused by "
         "member path before Git or a gate runs. Supplying no claim is legal "
         "and recorded as its own state.\n"
         f"Members: {_claim_members()}.\n"
         f"Required: {required}. `{vocab.CLAIM_REFERENCES_FIELD}` is a list "
         f"of objects carrying a non-empty "
         f"{', '.join(f'`{m}`' for m in vocab.CLAIM_REFERENCE_REQUIRED)} "
         f"(the reviewer must read it; the tool digests it) and optionally "
         f"{', '.join(f'`{m}`' for m in optional_refs)}; "
         f"{', '.join(f'`{m}`' for m in vocab.CLAIM_LIST_FIELDS)} are lists "
         f"of strings; "
         f"{', '.join(f'`{m}`' for m in vocab.CLAIM_OBJECT_LIST_FIELDS)} are "
         f"non-empty lists of objects; the rest are strings.\n"
         f"{_claim_values()}"),
        ("hand off",
         paths.command(*paths.lits(TOOL_NAME, "handoff", "--claim-file"),
                       paths.Ph("<claim.json>"),
                       paths.opt(paths.Lit("--base"), paths.Ph("<sha>"))),
         "Commits outstanding tracked work, runs the local gates at that "
         "commit, pushes the reviewed branch to its own name, awaits any "
         "CI-attested gates, emits, validates and records the request, and "
         "prints the reviewer's command. `--base` is needed only for round 1 "
         "of a lineage. Re-running on an unchanged tip returns the same "
         "envelope.\n"
         "Under `[roles] enforcement = \"pr-approval\"`, the work must be on "
         "a non-default branch with an open pull request before this step; "
         "open it with the forge's CLI, since the tool touches no forge.\n"
         "`--debug`/`--no-debug`, else `[roles] debug = true`, else off, "
         "decides whether the reviewer also critiques the tool in a `## "
         f"{vocab.TOOL_FEEDBACK_SECTION}` section: advisory, never a gate.\n"
         "Then stop, with the `brief` and `relay`."),
        ("close the round",
         paths.command(*paths.lits(TOOL_NAME, "close", "--verdict"),
                       paths.Ph("<verdict.md>")),
         f"Validates and records the verdict. `{vocab.VERDICT_CHANGES}`: "
         f"the lineage stays open and the next command is `respond`. "
         f"`{vocab.VERDICT_CLEAN}`: the lineage is closed at the exact "
         f"reviewed SHA. Merge authority is the account allowlist and the "
         f"standing rules you operate under, not this tool.\n"
         f"When round 3 of a lineage comes back `{vocab.VERDICT_CHANGES}`, "
         f"stop before `respond` and answer \"should we redesign?\" for the "
         f"user: read the lineage's findings, say why the reviewer keeps "
         f"coming back, whether any are lexical or perfection with no real "
         f"consequence, and whether the design is the problem, with a "
         f"recommendation. A fourth round waits for their answer."),
        ("respond",
         paths.command(*paths.lits(TOOL_NAME, "respond", "--verdict"),
                       paths.Ph("<verdict.md>"), paths.Lit("--from-json"),
                       paths.Ph("<d.json>"), paths.Lit("--out"),
                       paths.Ph("<disposition.md>")),
         f"Exactly one disposition per finding — "
         f"{' / '.join(vocab.DISPOSITIONS)} — each with its mandatory "
         f"payload; a blocking finding cannot take "
         f"{' or '.join(f'`{d}`' for d in vocab.BLOCKING_ILLEGAL)}. "
         f"Write `<d.json>` in the shape "
         f"`{paths.command(*paths.lits(TOOL_NAME, 'respond', '--help'))}` "
         "prints; its payload text members are strings, an escalation's "
         "positions included.\n"
         "An `accepted` disposition follows this order, and only this "
         "order: (1) make the fix; (2) run the finding's named "
         "falsification test on the fixed head, which records "
         f"`payload.falsification.{status_key}` "
         f"({' | '.join(vocab.FALSIFICATION_STATUSES)}); (3) run the "
         "mutation, the same test with the defect reintroduced, and "
         f"restore, which records `.{mutation_key}` "
         f"({' | '.join(vocab.MUTATION_OUTCOMES)}), the proof the test "
         "can fail at all; (4) write the disposition record with `--out`; "
         "(5) hand off again.\n"
         "A fix proposed but not made is `deferred` or `escalated`, never "
         "`accepted`, and a blocking finding cannot take `deferred`."),
        ("close a lineage by decision",
         paths.command(*paths.lits(TOOL_NAME, "close", "--lineage",
                                   "--reason"), paths.qph("<why>"),
                       paths.Lit("--by"), paths.Ph("<who decided>")),
         "Only when the human decides a review is over without a clean "
         "verdict. Recorded with the reason; the next handoff opens round 1 "
         "with the repository's default cap."),
    ],
    "reviewer": [
        ("take",
         paths.command(*paths.lits(TOOL_NAME, "take"),
                       paths.Ph("<request.md>"), paths.Lit("--as"),
                       paths.Ph("<your id>")),
         "`--as` names you. The tool can't observe who is running it, so it "
         "records what you declare in an append-only record: declare only "
         "your own identity. `take` validates the request, checks you are "
         "the stamped reviewer, fetches the target, labels every reference "
         "checked / mismatch / unavailable, records the take, and prints the "
         "request with the exact diff command. A defective or unreachable "
         "envelope isn't reviewed: return a refusal that isn't yours to fix "
         "to the human.\n"
         "Three lines to read in what it prints. `head:` reading NOT the "
         "target means your working tree isn't the reviewed bytes, so read "
         "through the diff command it prints. `Swept:` naming a path outside "
         "the claim's `scope_paths`, or an intent-to-add entry, is work the "
         "author may not have inspected, so read it first. The attestation "
         "table condenses each object; the `kept` path (or `--full`) holds "
         "the exact bytes, for a finding that turns on a field the table "
         "doesn't carry.\n"
         "`--compact` returns pointers instead of the request, for a "
         "reviewer that reads the kept file itself."),
        ("rule",
         None,
         "\n".join([
             *_judge_reading(),
             "Read the full diff first, with the printed command, before you "
             "weigh the author's account. `scope_paths` and a `Scoped:` diff "
             "say where the author worked and never limit your search.",
             "The author's objective, risk, not-done list and observations "
             "are claims to test.",
             "Report everything you find rather than filtering, and rank by "
             "consequence: each finding's Why names the real input or "
             "situation a user or agent would meet and what goes wrong for "
             "them. Severity orders the findings and is no bar to reporting "
             "one. Mark a low-confidence finding `unconfirmed` in its Why. "
             "Claim a finding as blocking only when you reproduced it or can "
             "show it in the reviewed bytes; report the rest as unconfirmed, "
             "at a severity that doesn't block.",
             "A boundary's domain gap with no plausible input goes at Low, "
             "with that said. A Low doesn't by itself ask for changes; "
             "`validate` decides the verdict string a round of only Lows "
             "must carry.",
             "What the trust model or an accepted authority puts outside the "
             "round (a forgery naming no lesser party, a demand outside an "
             "accepted domain) stays out of `## findings`: name it in a line "
             "under `## evidence checked`.",
             "Write the verdict in the request's own `Verdict shape` and "
             "classify each finding by its `## Taxonomy`; the validator "
             "holds both.",
             "A FALSIFICATION names one test and asks for no mutation table. "
             "Anchor each FALSIFICATION test in the reviewed tree wherever "
             "the defect admits it. Where the defect lives in a mutable "
             "artifact outside the tree (a PR body, an issue, a dashboard), "
             "say so in the finding, so a later `cannot_execute` reads as "
             "the outcome of a stated dependency and the human deciding the "
             "`unverifiable` breaker has your words that it was known.",
             f"A request stamped `{vocab.DEBUG_ATTR}="
             f"\"{vocab.DEBUG_TOOL_FEEDBACK}\"` also asks for "
             "`tool-feedback.md`, beside this procedure; you may volunteer "
             "that section on any round.",
         ])),
        ("validate, then stop",
         paths.command(*paths.lits(TOOL_NAME, "validate"),
                       paths.Ph("<verdict.md>"),
                       paths.Lit("--from-target")),
         "`--from-target` judges the verdict against the target commit's own "
         "`review.toml`, the bytes `take` read. Without it the verdict is "
         "judged against your checkout, which on a detached worktree or a "
         "second machine is often no config at all. A refusal of the "
         "verdict's content is yours: correct it and re-run until it exits "
         "0.\n"
         "Then stop, with the `brief` and `relay`. The relay, under "
         "`## Verdict`, is the author's to run: on a paste round one fence "
         "with the verdict bytes as the command's stdin, on a path round "
         "with the kept file's path. You don't start the next round, send it "
         "back for fix-and-resubmit, or decide whether it enters the "
         "record.\n"
         "Where the standing rules you operate under grant a clean-verdict "
         f"approval: after a validated `{vocab.VERDICT_CLEAN}` on a branch "
         "with an open pull request, that grant covers recording the "
         "approving review through the project's approval tooling if the "
         "operator's credentials for it are on this machine. If they are "
         "absent, say so once and point at the project's approval setup. "
         "The relay comes first and is never blocked on it."),
    ],
    # What a reader needs only on a trigger, each its own file on a skill
    # surface and its own section in the instruction block: name -> (title,
    # the trigger the routing states or None where a role step names it,
    # the paragraphs). Keys are `RARE_FILES`.
    "rare": {
        "decide": (
            "absent config asks once",
            "a result carries a non-empty `decide` list, or someone asks "
            "what this repository leaves undeclared.",
            ["Absent config asks once. A result carrying a `decide` list has "
             "applied a built-in default for a key this repository never "
             "declared. Each entry names the key, its meaning, the value "
             "applied, and the exact TOML line for `set` and for `unset`.",
             "Ask the user once per session, per key, in plain language, "
             "whether this project sets or unsets it; write their answer "
             "into `review.toml` as the printed line and commit it. Ask only "
             "about undeclared keys, and leave the decision to them.",
             "A key whose only off state is to stay undeclared prints its "
             "`unset` as a comment line, `# decided: limits.token_budget "
             "undeclared`. That line is the answer: write it into "
             "`review.toml` under `[limits]` and the entry stops, while the "
             "key stays undeclared and uncounted.",
             f"`{paths.command(*paths.lits(TOOL_NAME, 'decide'))}` prints "
             "the same list and is the only way to read it without emitting "
             "or recording anything. Use it when nobody asked for a round, "
             "such as after a version pin moves, instead of running an "
             "emitting verb to find out."]),
        "carrier": (
            "correcting a round's carrier",
            "someone disputes the round's transport, or a relay's carrier "
            "looks wrong for where the other side runs.",
            ["A round declares whether the two sides share a filesystem, and "
             "that decides which carrier the relay prints. The tool resolves "
             f"it from declarations (`"
             f"{paths.command(*paths.lits(TOOL_NAME, 'handoff', '--help'))}` "
             "lists their order); with none, a new emission carries "
             f"`{vocab.TRANSPORT_EMISSION_DEFAULT}`. A locally readable "
             "request file proves nothing about what the other side can "
             "open.",
             "A human corrects a wrong stamp by an explicit statement about "
             "this round's topology, or an exact command carrying "
             f"`--transport {'|'.join(vocab.TRANSPORTS)}`: to `handoff` if "
             "you are the author, to `take` if you are the reviewer. A "
             "conversational question isn't a declaration, so ask rather "
             f"than turn one into an override. A "
             f"`{vocab.TRANSPORT_EMISSION_DEFAULT}` round whose commands you "
             "rewrite for a paste is a round whose record no longer "
             "describes it."]),
        "closure-authority": (
            "closing a domain relative to an authority",
            "a claim's objective declares `authority` and `covers`, or you "
            "are about to declare or narrow one.",
            ["A domain whose completeness can't be established from inside "
             "the artifact, a set that can be declared but never proven "
             "complete from within, may be declared closed relative to a "
             "stated authority. It is the only terminator. An authority is a "
             "machine-generated, separately gated artifact that derives the "
             "set; a hand-maintained list never is. The declaration is no "
             "exemption: it moves the attackable surface from an unbounded "
             "hunt onto a finite artifact, and an authority narrower than "
             "its own stated domain is itself the defect.",
             "**Author.** Declare it in the claim, per anchor "
             "(`objectives[].authority`, `covers`), and state its domain: "
             "what it is claimed to cover, in the terms it enforces.",
             "**Reviewer.** Rule on the domain before ruling under it: does "
             "it match the anchor's declared purpose and every surface still "
             "relied on downstream? A concrete escape outside the declaration "
             "is admissible evidence that the domain is too narrow, and a "
             "domain narrower than its anchor's purpose is a finding about "
             "the claim. Once a stated domain is accepted, rule coverage "
             "against it; a demand outside it then goes to a backlog, however "
             "well it reproduces.",
             "**Attacking it.** Attack the authority's derivation and "
             "coverage. You may still bring an instance, naming the authority "
             "it escapes; one that names none is not a completeness finding. "
             "Rejecting the authority is an ordinary finding anchored on the "
             "authority itself, and it is bounded, because unbounded it "
             "restates one level down forever.",
             "**Narrowing.** A narrowing is a complete answer only when the "
             "anchor's purpose and every consumer claim narrow with it, "
             "leaving no wider reliance implicit; one that leaves a wider "
             "promise standing is the defect. Where it is complete it is "
             "preferred to widening the mechanism."]),
        "tool-feedback": (
            "a debug round's tool feedback",
            None,
            [f"Add a `## {vocab.TOOL_FEEDBACK_SECTION}` section after "
             "`## evidence checked`: a self-critical account of how the tool "
             "could have served this round better, under three headings. "
             "Efficiency: steps, reads or re-derivations the procedure "
             "wasted. Cost: bytes, tokens and wall time you observed, against "
             "what the round needed. Accuracy: anywhere an envelope, a gate "
             "result, a brief or a refusal misled you or made you check "
             "something twice.",
             "Critique the tool, never the diff. Findings stay findings, and "
             "nothing here softens or substitutes for one. The section is "
             "advisory prose, recorded in the author's ledger at close."]),
    },
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


def _generated_line() -> str:
    render_verb = paths.command(*paths.lits(TOOL_NAME, "render-adapters"))
    return (f"GENERATED by `{render_verb}` at tool version {TOOL_VERSION}; "
            f"do not edit, regenerate.")


def _head_sections() -> list[str]:
    """What this is, and the rules that hold on both sides: the part of the
    procedure every surface carries first."""
    L = []
    L.append("## What this is")
    L.append("")
    L.append(f"`{TOOL_NAME}` runs the mechanical half of a review: request, "
             f"verdict and disposition envelopes bound to SHAs, gates it runs "
             f"and attests itself, an append-only ledger with breakers. No "
             f"model runs inside it. The envelope's stamp (`author=… "
             f"reviewer=…`) says which side you hold, per envelope.")
    L.append("")
    L.append(f"A reviewed commit must carry `review.toml`; `handoff` and "
             f"`take` refuse one that doesn't. A user-level "
             f"`~/.config/{TOOL_NAME}/<repo-id>.toml` still governs every "
             f"local verb, so acting locally needs no in-tree file.")
    L.append("")
    steps = procedure()
    L.append("## Rules that hold on both sides")
    L.append("")
    for rule in steps["both"]:
        L.append(f"- {rule}")
    L.append("")
    return L


def _role_section(role: str) -> list[str]:
    """One side's procedure, steps in order: the same lines whether the
    instruction block carries it inline or a role file carries it alone. A
    step's text may run to several lines, each indented under it."""
    steps = procedure()
    L = [f"## If you hold the {role} stamp", "", steps["finish"][role], ""]
    for i, (title, cmd, what) in enumerate(steps[role], 1):
        L.append(f"{i}. **{title}**" + (f" — `{cmd}`" if cmd else ""))
        L.extend(f"   {line}" for line in what.split("\n"))
    L.append("")
    return L


#: How an agent on each skill surface knows the directory it loaded this
#: skill from, so a role file is named relative to the skill itself.
#: Claude Code prints "Base directory for this skill: <dir>" on every load,
#: a Team profile's included (`~/.claude-<team>/skills/loupe`, seen in its
#: transcripts 2026-11-01); Codex lists each skill by its `SKILL.md` path.
_SKILL_DIR_HINT = {
    "claude-skill": "in the base directory Claude Code printed when it "
                    "loaded this skill",
    "codex-skill": "in the directory you opened it from",
}


def _rare_index() -> list[str]:
    """The rare files a reader finds by a trigger the routing states, one
    line each. `tool-feedback` has no line: the reviewer's `rule` step names
    it where its stamp applies."""
    return [f"- `{name}.md`: {trigger}"
            for name, (_title, trigger, _body) in procedure()["rare"].items()
            if trigger is not None]


def _routing_section(surface: str) -> list[str]:
    """RR8: where a skill surface's side files are. Names each by its file
    name alone, from `INSTALL`, and places it beside this `SKILL.md` wherever
    the copy is, never by an absolute path: a copy deployed outside
    `INSTALL`'s target (a Team profile's own `skills/loupe/`) that named the
    personal path sent its reader to a directory that profile denies (Osama,
    2026-11-01)."""
    L = ["## Your side's procedure", "",
         "Before you act on an envelope, read your side's file:", ""]
    for role, kind in SKILL_ROLE_KINDS[surface].items():
        L.append(f"- the **{role}** stamp: "
                 f"`{INSTALL[kind][0].rsplit('/', 1)[1]}`")
    L += ["",
          f"Both sit beside this `SKILL.md`, {_SKILL_DIR_HINT[surface]}; "
          f"read them there, never from another install of this skill. Act "
          f"from that file's text, not from memory of an earlier read.",
          "",
          "Beside them, read only when it applies:", "",
          *_rare_index(), ""]
    return L


def _rare_block_sections() -> list[str]:
    """The rare files as the instruction block carries them: the block has no
    directory, so each is a section under its file's name, and the names the
    rules use lead to it."""
    L = ["## Read only when it applies", "", *_rare_index(), ""]
    for name, (title, _trigger, body) in procedure()["rare"].items():
        L += [f"## `{name}.md`: {title}", ""]
        for paragraph in body:
            L += [paragraph, ""]
    return L


def _tail_sections() -> list[str]:
    verbs = ", ".join(
        f"`{paths.command(paths.Lit(TOOL_NAME), paths.token(verb))}`"
        for verb, _help in verb_table())
    return ["## Verbs (from the CLI itself)", "",
            f"{verbs}. `{TOOL_NAME} <verb> --help` has each.", ""]


def _body() -> list[str]:
    """The whole procedure, both sides and the rare sections inline: the
    instruction block's."""
    return (_head_sections() + [line for role in ROLES
                                for line in _role_section(role)]
            + _rare_block_sections() + _tail_sections())


def _skill_body(surface: str) -> list[str]:
    """A skill's `SKILL.md`: both-sides rules and the routing section in
    place of the two procedures and the rare files (RR8)."""
    return _head_sections() + _routing_section(surface) + _tail_sections()


def _frontmatter(surface: str) -> list[str]:
    return [
        "---",
        f"name: {TOOL_NAME}",
        # Loaded into every session on both skill surfaces, whether or not
        # the skill fires, so it holds only the trigger (SL-B10, 2026-10-03;
        # narrowed 2026-11-04). No colon: it would break the YAML.
        f"description: Use when handing off work for review through "
        f"`{TOOL_NAME}`, or when a `{TOOL_NAME}` command, request or verdict "
        f"reaches you.",
        "---",
        "",
        f"# {TOOL_NAME} — review procedure ({surface} adapter)",
        "",
        _generated_line(),
        "",
    ]


_SURFACE_NAMES = {"claude-skill": "Claude Code", "codex-skill": "Codex"}


def _role_file(surface: str, role: str) -> str:
    """A skill's role file (RR8): no frontmatter, so no agent discovers it
    as a skill of its own; it is read from the routing line in `SKILL.md`."""
    return "\n".join([
        f"# {TOOL_NAME} — {role} procedure ({_SURFACE_NAMES[surface]} "
        f"adapter)",
        "",
        _generated_line(),
        "",
        *_role_section(role)])


def _rare_file(name: str) -> str:
    """A skill's rare file: no frontmatter either, and read only on its
    trigger. The same bytes on each skill surface."""
    title, _trigger, body = procedure()["rare"][name]
    lines = [f"# {TOOL_NAME} — {title}", "", _generated_line(), ""]
    for paragraph in body:
        lines += [paragraph, ""]
    return "\n".join(lines)


def surface_text(surface: str) -> str:
    """Everything an agent on `surface` can read of the procedure: the
    instruction block, or a skill's `SKILL.md` followed by its role files
    and rare files. What a check of the procedure's CONTENT reads, so a rule
    moved between a skill's files is still found."""
    if surface not in SKILL_ROLE_KINDS:
        return render(surface)
    return "\n".join([render(surface),
                      *(render(kind) for kind in
                        SKILL_ROLE_KINDS[surface].values()),
                      *(render(kind) for kind in
                        SKILL_RARE_KINDS[surface].values())])


def render(kind: str) -> str:
    for surface, kinds in SKILL_ROLE_KINDS.items():
        for role, role_kind in kinds.items():
            if kind == role_kind:
                return _role_file(surface, role)
    for kinds in SKILL_RARE_KINDS.values():
        for name, rare_kind in kinds.items():
            if kind == rare_kind:
                return _rare_file(name)
    if kind in _SURFACE_NAMES:
        return "\n".join(_frontmatter(_SURFACE_NAMES[kind])
                         + _skill_body(kind))
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


_SKILL_OUTPUT_DIRS = {"claude-skill": Path("claude"),
                      "codex-skill": Path("codex")}

OUTPUTS = {
    "claude-skill": Path("claude") / "SKILL.md",
    "codex-skill": Path("codex") / "SKILL.md",
    "instructions-block": Path("instructions-block.md"),
    # RR8: each skill's role files, beside its SKILL.md.
    **{kind: _SKILL_OUTPUT_DIRS[surface] / f"{role}.md"
       for surface, kinds in SKILL_ROLE_KINDS.items()
       for role, kind in kinds.items()},
    # 2026-11-04: and its rare files, read on a trigger.
    **{kind: _SKILL_OUTPUT_DIRS[surface] / f"{name}.md"
       for surface, kinds in SKILL_RARE_KINDS.items()
       for name, kind in kinds.items()},
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

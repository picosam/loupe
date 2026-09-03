# loupe: overview

This document is the argument for `loupe` — the case for handing your
review loop to it, and an honest account of what that costs. It is not the
manual: the exact grammar of every command is in
[`docs/design.md`](design.md), and the thirty-second walkthrough is in the
project [`README`](../README.md).

`loupe` is a deterministic, agent-neutral review tool for AI-coding-agent
work: one agent authors a change, a different agent reviews it, and the
author agent runs `handoff` unasked once its own work is done — a human
carries every leg between them. "Deterministic" means the tool
itself runs no model anywhere in its own code — it is a CLI (a program you
run from a shell) written in Python's standard library only, with nothing
in its own code that talks to a network beyond the Git subprocesses that
already push and fetch your commits. What it replaces is the review
loop's plumbing — who says what, in what shape, and when a round is
actually over — not the judgment either agent brings to the diff.

## 1. What problem this solves

Review processes for agent-authored changes tend to become *repository
furniture*: a pile of scripts, a contract, a skill, a merge shim — the
kind of thing every new repository starts by copy-pasting from the last
one. Copies drift. Two repositories that started from one such toolkit
were measured to differ in contract version, rejection codes and CLI
fixes — and the *original* repository, the one the toolkit was extracted
from, was running a strictly weaker validator than the copy it spawned.

The natural fix — write a better prompt, tell the reviewing agent to be
rigorous — doesn't hold, for a reason worth stating plainly: two agents
reviewing each other's work over prose alone have no fixed ground. The
review binds to whatever the reviewer's checkout happens to hold at the
moment it looks. The severity scale is whatever the reviewing model
decides to supply that day. A finding drifts when it's restated by hand
between rounds. Nothing stops either side from talking the other into one
more round, or stops a round from simply never ending. `loupe`'s answer is
not a better prompt; it is removing the degrees of freedom that let review
drift in the first place — replacing prose exchanged between two agents
with **envelopes**: small, structured documents a validator accepts or
refuses outright, each bound to an exact commit.

Three things follow, and they set the scope of everything below:

- **Merge gates belong in CI, not in this tool.** A check that only runs
  on one laptop gates nothing for anyone else. `loupe` treats a clean
  verdict as the *authorization* and CI (or a forge's own branch
  protection) as the *enforcement* — the two are deliberately never
  conflated.
- **The process is role-agnostic.** Author and reviewer are parameters
  resolved for each round, not hardcoded directions — the same agent can
  hold either role on different days, on different repositories.
- **Deterministic checks run first.** Anything a linter, type-checker,
  test or schema check can decide runs before any model looks at the
  change, and the tool runs those checks itself rather than trusting a
  claim that they passed.

## 2. What it does, in one round

Every round moves through the same five steps, and a human is on both
ends of every leg — the tool never invokes the other side by any
mechanism (no CLI call, no subagent, no hook, no API):

```
author agent → relay (a human) → reviewer agent → verdict → relay → close
```

**Author runs `loupe handoff`.** This commits your outstanding tracked
work, pushes the branch, runs the gate manifest — the repository's own
list of commands (tests, linters, whatever it declares) that the tool
executes itself rather than taking your word for — and then emits and
validates a **request**: an envelope naming the exact commit, what you're
claiming to have done, and every file reference the reviewer needs. It
records the round, keeps a copy, prints the one command the reviewer runs,
and **stops**. Nothing about running `handoff` is itself "the end of a
session" that the tool can see — but the design treats review as the
default end of one anyway: with the onboarding default (`[roles]
review_default = "on"`), an author agent runs `handoff` unasked once the
work is done, and only skips it when a person declined a review earlier
in that same session. Carrying the printed command to the reviewer — the
**relay** — is the human's job either way; the tool has no verb that
reaches across.

**Reviewer runs `loupe take --as <identity>`.** This fetches the exact
commit, checks every reference the request claimed against what the
target commit's tree actually holds, and validates the request against
*that repository's own* `review.toml` — never against whatever the
reviewer's local checkout happens to hold. The reviewer then rules,
writing a **verdict**: exactly one of two literal outcomes — `clean to
advance`, or its opposite, asking for changes — with every finding in the
latter carrying a severity, a required outcome, and a falsification test —
a concrete, runnable way to prove the finding has actually been fixed, not
just addressed in prose.

**Author closes the round with `loupe close`.** A clean verdict closes the
**lineage** — the whole chain of rounds for one change — at that exact
commit. Anything else keeps it open: `loupe respond` records exactly
one **disposition** per finding, from a closed set of five — accepted
(fixed and verified), refuted (with live evidence the reviewer must then
answer), deferred (to a named destination), a stated preference, or
escalated to a named authority. Two of the five are not legal answers to a
*blocking* finding at all: you cannot defer a blocker to later, and a
blocker is not a matter of preference. The author makes the changes and
hands off again.

**The human override, when a finding shouldn't block forever.** Two verbs
exist for exactly the case where a named person decides, deliberately, to
live with something the tool would otherwise keep blocking on: `loupe
waive --finding <id> --reason … --by <who>` records that one specific
finding stands unfixed, and `loupe authorize-advance --reason … --by <who>`
then lets the lineage advance once every open finding carries such a
waiver. This produces its own kind of envelope — an authorization — never
a manufactured clean verdict, because an overridden review and a genuinely
clean one are different facts, and the record has to keep them
distinguishable. Both `--by` fields are asserted, not proven: the tool
cannot observe who typed a command, and says so rather than implying a
guarantee it doesn't have.

**Carrying the bytes across.** Author and reviewer are routinely on
different machines, and the tool declares — never guesses — how the
envelope travels: `path` when both sides share a filesystem (the everyday
case, and the default when nothing else is declared), a human paste when a
person carries the bytes by hand (a chat window, a ticket — anything that
preserves a fenced block verbatim), or `git` when the two sides share no
disk but both already reach the same remote, in which case the envelope
rides a small, disposable Git reference alongside the commit that's
already being pushed and fetched. Whichever one a round uses, the tool
prints the exact command for that carrier; no relaying human has to
choose or reword anything.

## 3. What it guarantees, and what it deliberately does not

**Trust model — one principal.** The operator running loupe is the only
principal the tool recognizes, and every agent that runs a verb — author
or reviewer — runs it *for* that person. The ledger (the tool's own
append-only history of what happened), the state directory, the clone,
and the remote it pushes to are all that operator's own writable state.
So the tool defends the record against **omission and fatigue** — a
finding that quietly dies unanswered, an answer bound to the wrong
ruling, a stamp that misdescribes who did what — and explicitly **not**
against its own owner: someone with write access to that state could
forge a record, and nothing here prevents it or claims to. If your threat
model includes a party with *less* than the operator's own access forging
review records, that is inside the tool's taxonomy and worth raising as a
finding; the operator's own good faith is the one thing outside it, by
design, stated rather than hidden.

**No forge access.** `loupe` never talks to GitHub, GitLab, or any other
forge's API. It touches no pull request, posts no comment, grants no
approval. What it does touch is plain Git, as a subprocess: `handoff`
pushes your branch and observes the remote ref with `git ls-remote`,
`take` fetches the target commit. Opening a pull request, posting an
approval, merging — these stay the agents' own steps, taken with their
own credentials, under whatever standing instructions govern them, and
entirely outside this tool. One consequence worth stating plainly: an
approval that fires *automatically* off a posted verdict — no person in
the loop — is explicitly rejected by the design, on grounds that hold
regardless of how convenient it would be. Nothing on a forge's side can
tell an authentic verdict from a fabricated file with valid-looking
digests, and the party best positioned to fabricate one is the author of
the very change it would approve.

**No model inside.** The CLI core is Python, standard library only — no
model runs anywhere in the tool's own code, and it holds no model
credential. This is also why it can run inside CI and be invoked by any
agent that has a shell.

**What the grammar actually guarantees.** A request has four sections,
each with a different author and a different trust level — a claim (the
author's own assertion, unverified), evidence (machine-attested — a gate
that actually ran, with its exact command, exit code and output digest
recorded, never just claimed), a contract (the repository's declared
invariants), and references (addressable, resolved against the target
commit's own tree). Findings get a stable identity — a **fingerprint**,
a hash computed by the tool over a finding's classification, its anchor
and its claim text, deliberately excluding line numbers so a finding
survives an unrelated edit above it — stated honestly as a *syntactic*
guarantee only: a verbatim repeat is caught, a paraphrase of the same
finding is not. Six **circuit breakers** — deterministic checks, all
computed from the ledger — catch a loop going wrong in a specific way: a
finding that returns after being refuted with nothing genuinely new, one
that returns after its own falsification test had already passed, a round
that changes nothing measurable, a blocking finding whose test cannot be
run, spend past a declared cap, and a disposition whose supporting
evidence matches no recorded emission. **Every firing except one stops the
loop**: it **refuses** the next `handoff` until a human has recorded a
decision covering it — this isn't advisory prose, it's an actual refusal
in the tool's own preflight. The one exception is a round count past the
repository's cap — it advises rather than refuses, because a round count
alone is a poor proxy for whether a lineage is actually stuck; the
convergence report below is what answers the question the cap was
standing in for. Spend past a *token* budget is a different member of the
same breaker and is not the exception. **A measured token breach refuses
like every other firing.** And what the repository never declared is always
reported, never silently assumed: any verb that applied a built-in
default — transport fell to `path`, a debug flag defaulted off — says so
in a `decide` list on its result, naming the exact key, what it means,
what was applied, and the exact line that would set it, until the
repository declares it and the reporting stops for good.

**A tool-identity stamp catches a mismatch a version number hides.** Both
sides of a round run their own installation, and two installations can
report an identical version string while running different code — a stale
grafted copy, an install a few fixes behind. The **request**, the
**disposition** and the **authorization** — every envelope this tool's own
installation emits — carry a content digest (`tool`) over the exact code
that produced them, so a stale reader is caught by what it actually emits,
not by what it claims to be. The verdict carries none: the reviewing agent
hand-authors it from a shape block rather than the tool emitting it, and a
digest on hand-authored prose would prove nothing about the code that
actually ran.

**What it does not guarantee.** The `--by` on a waiver or an
authorization is asserted, never verified — the tool cannot distinguish a
human from an agent holding the same credentials, and its own design
record says so rather than implying a control that isn't there. Fingerprint
identity is syntactic, not semantic. Token counts on a debug round are
declared by whoever ran the model, never independently measured, because
no model runs inside the tool to measure against. And the one-principal
trust model above is not a limitation discovered after the fact — it is
the boundary the whole mechanism is built up to, not past.

## 4. What it costs

**One committed file, at minimum.** A reviewed commit has to carry its own
`review.toml` — the repository's severities, classifications, gate
commands and roles — because a review is the act of showing a target's own
rules to a second machine, and rules that live on only one machine cannot
be shown to anyone. `handoff` and `take` both refuse a target that tracks
none, with nothing to fall back to. Declaring that vocabulary — deciding
what counts as a blocking finding here, what gates must pass — is real,
upfront work, not a checkbox.

**A human relays every leg, by design.** Nothing here can hand a request
to a reviewer or a verdict back to an author on its own — that omission is
deliberate, not a missing feature, and it is what keeps a round from
being carried across machines by anything other than a person. The
author agent still emits the request unasked, at the end of its own
session; what the tool cannot do, and a human always does, is hand that
envelope to the reviewer or a verdict back to the author. On the
everyday case (one machine, one filesystem) this costs nothing beyond
running the printed command. Across machines with no shared filesystem
and no shared remote, it costs an actual human paste per leg of every
round.

**The tool refuses more than it guesses.** No declared taxonomy, no
emission. No fetchable target, no request. The default branch under a
declared approval-enforcement pathway, no `handoff` — the runtime check
is the branch state alone; establishing the open pull request itself is
the author's own procedural step with the forge's CLI, outside this
tool's reach, never a runtime check `loupe` makes for you (§3, "No
forge access"). Untracked files at handoff time, a refusal rather than
a guess about whether they were meant to ship. This is the same
rigidity the argument above is built on, and it shows up as friction on
the way in: a first `handoff` in a freshly
onboarded repository is more likely to refuse and tell you what's missing
than to succeed on the first try.

**Some of the design is not built yet, and the document says exactly
where.** Risk tiering (a deterministic score meant to replace diff size as
a depth proxy), path-scoped contract invariants, automatic execution of a
finding's own falsification test, and an MCP facade are all designed but
not implemented — `docs/design.md` §9 marks the exact line between what
runs today and what is still on paper, on purpose, rather than blurring
it.

## 5. How to adopt

The [README](../README.md) has the install paths and a thirty-second,
two-terminal walkthrough of one full round. Before running a first round
against a repository you actually care about, run the onboarding pass in
[`docs/onboarding.md`](onboarding.md) — it is the difference between a
`review.toml` that says what your repository actually enforces and one
that quietly doesn't, and it closes the one question most review setups
skip until a PR is sitting there green and unmergeable: who approves the
reviewed work, and can they actually reach it.

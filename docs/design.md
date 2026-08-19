# loupe — design of a deterministic, agent-neutral review process

This is the specification of the mechanism `loupe` implements. It is derived
from a longer, private design record that grew through five reviewed rounds
against real repositories; what travels here is the **grammar, the
mechanisms and the metrics**. The vocabulary of any one repository (its
severities, classifications, gate names, thresholds) is configuration, and
the private history that produced the design stays where it happened. Where
this document says "designed" without "implemented", the tool does not do it
yet — see §Status at the end.

## 1. The problem

Review processes for agent-authored changes tend to become *repository
furniture*: a pile of scripts, a contract, a skill and a merge shim that
every collaborator inherits, and that every new repository starts by
copy-pasting. Copies drift. Two repositories that started from one review
toolkit were measured to differ in contract version, rejection codes and CLI
fixes — the original was running a strictly weaker validator than the copy.

Two conclusions shape everything else:

1. **The portable thing is the grammar, not the vocabulary.** Envelope
   structure, SHA binding, failure policy and disposition rules are the same
   everywhere. Severity and classification enums legitimately differ between
   a repository that reviews code and one that reviews prose, so they are
   supplied by the repository, never by the tool.
2. **The process must belong to the person running it, not to the repository
   being reviewed.** Zero footprint in a shared tree by default; identical
   behaviour whether the configuration is personal or committed.

Three points taken as settled:

- Merge gates belong in CI. A gate on one laptop gates nothing for anyone
  else. The verdict is the *authorization*; CI is the *enforcement*; they
  were never the same thing.
- The process is role-agnostic: author and reviewer are parameters,
  resolved per invocation.
- Deterministic first: anything a linter, type-checker, test or schema check
  can decide runs before any model sees the change.

## 2. Ownership model — zero shared-repository footprint

| Layer | Lives where | Footprint in a shared repo |
|---|---|---|
| The tool | user machine, `uvx`, or vendored in repos that opt in | zero |
| Grammar and failure policy | inside the tool | zero |
| Taxonomy, gate manifest, roles, limits | `review.toml` in the repo **if the repo wants it**; otherwise `~/.config/loupe/<repo-id>.toml` | zero by default |
| Round ledger | `~/.local/state/loupe/<repo-id>/` by default | zero by default |
| Agent adapters | user level (`~/.claude/skills/`, `~/.codex/skills/`) | zero |

Auto-detection has exactly one boundary. With no configuration the tool
still discovers what is *observable* (gates are commands that exist or do
not). It must **not** invent what is *decided*: absent a declared severity and
classification set the tool refuses to emit and the reviewer refuses to rule,
because a taxonomy is decision vocabulary and a tool that supplies its own has
authored the user's judgment scale. That refusal costs one file, and it is
not a repository file: user-level config preserves the zero-footprint
property exactly.

## 3. The four design questions

### 3.1 What goes into the review envelope

**Content model: Claim / Evidence / Contract / Reference.** Four parts, each
with a different author, trust level and failure mode if omitted.

| Part | Author | Trust | If omitted |
|---|---|---|---|
| **Claim** | the author agent | asserted, unverified | the reviewer re-derives intent from the diff and reviews the wrong question |
| **Evidence** | machines only | attested | the reviewer spends tokens on what a linter settled, or trusts an unrun check |
| **Contract** | the repository | declared | the reviewer invents invariants, or misses the one the change breaks |
| **Reference** | git | addressable | nothing to review |

The four sections, and the taxonomy the reviewer rules by, form a **closed
grammar**: each occurs exactly once, in this order (Taxonomy, Claim,
Evidence, Contract, Reference), identified by the leading word of its `##`
heading. A repeated section is a defect, not a choice between two — the
parser would keep the last and lose the first, and a required reference
declared under the earlier one would vanish between the raw envelope and
both the validator and the reviewer's probe. A heading that merely starts
like a section name (`## References`) is not that section and fails as
absent.
The wrapper's own attribute text is closed the same way: every attribute
(`sha`, `branch`, `author`, `reviewer`, `round`, and the disposition's
`verdict_sha`/`head`) occurs once — the first declaration is the value, a
repeat is a validation error, and text between the tag name and `>` that is
not a `key="value"` pair is refused — so a defective wrapper cannot choose
the SHA that validation, the fetch and the ledger bind. All of this is
judged before any git call.
The two JSON bodies — the disposition's, and the machine attestation block
inside Evidence — are read through one boundary that refuses a repeated
object member at any depth and names it; `json.loads` alone keeps the last
and discards the rest, which would let `"disposition": "deferred"` hide
behind `"accepted"` or `"exit_code": 1` behind `0`. The author's judgment
file that `respond` reads, and the claim file that `handoff` and
`emit-request` turn into the Claim and Reference sections, go through the
same boundary — a claim stating `"references"` twice is refused by name
before anything is pushed, cached, run or emitted, not loaded with the
earlier declaration erased. The handoff settles that grammar before it
consults ledger state at all: the author's bytes are captured once, digested
and parsed together, and only then does the lifecycle preflight read the
ledger. Malformed input is not a lifecycle question, so it never causes a
lifecycle to be read — and the digest that keys the emission cache attests
the same bytes the emitted Claim was parsed from, which a second read of the
path could not guarantee.

The claim's grammar is closed, not merely deduplicated, and one authority
declares it: the members, which are required, which are strings, which are
lists of strings, and the shape of a reference (a non-empty `path`, an
optional boolean `required`, an optional `note`). Malformed JSON, a
top-level value that is not an object, a member stated twice at any depth, a
declared member of the wrong type, a nested reference of the wrong shape and
an unknown member are one typed refusal naming the defect and its location —
an unknown member above all, because ignoring it in silence is how a
misspelled `stop_condition_typo` erases a stop condition the reviewer is
then never told to enforce. The domain includes the states that are not in
the JSON: bytes that are not UTF-8 (a readable file the tool cannot decode
is not a claim, and it used to escape as a traceback rather than a typed
refusal), a declared string carrying a value the envelope cannot encode — a
lone surrogate is valid JSON but not a Unicode scalar value, and every
surface downstream is UTF-8 — and an empty `--claim-file`, which is a
different invocation from an omitted one and is never folded into it.
Supplying no claim remains its own recorded state. Both emitting verbs complete that boundary before the ledger is
constructed or read, before Git, before the gates, before emission and
before anything is recorded; a supplied claim is read exactly once, and the
capture it produces is a frozen value carrying digest, claim and presence
together, so no downstream branch can find a value it reads as "not captured
yet" and reopen the file.

**Boundary closure** — a rule the review loop imposes on itself, learned
across four rounds that each fixed one symptom of one boundary. When a
finding concerns a parser, a grammar or a lifecycle seam, the acceptance
that answers it and the closure that rules on it must partition the complete
admitted domain: every input kind the boundary accepts, every declared field
and nested shape, every ordering by which the seam can be reached — with
paired valid controls proving the check is live, and one mutation per bypass
proving it can fail. Making the named reproducer green is not an answer; it
is one point of a domain, and the next unpartitioned point is the next
round. The rule is carried in the generated adapters, so both sides read it
from the same source.

**Claim** — objective / decision boundary, what changed and why, what was
*deliberately not* done, stop conditions, hand-back questions, self-assessed
risk with a reason. Cheap, high-signal, and explicitly not evidence: the
validator never lets a claim satisfy a gate. It comes from a JSON claim file
the author writes; the tool carries it and never invents it.

**Evidence** — a machine attestation block, never raw output inline. The
repository declares a **gate manifest** (`id`, `command`, `blocking`); the
emitter runs every gate itself and records, per gate: exact command, exit
code, the identity of the executable that ran (a content digest, stronger
than a version string), the target SHA and the SHA the gate actually
executed against, tree cleanliness, a `binding` verdict, duration, and a
digest plus pointer to the retained full output. Three rules, lifted from a
SHA-bound verification cache that got them right repeatedly:

- **uncaptured is not clean** — a gate not run is a distinct state from a
  gate that passed, and only one of them may satisfy anything;
- **SHA-bound and dirty-invalidated** — an attestation from a dirty tree or a
  different commit is not an attestation (`binding: unbound`, and the
  validator rejects it);
- **recompute, don't trust** — the validator checks every field, never the
  presence of a section. A blocking gate that ran and failed does not become
  evidence by being reported accurately.

**Contract** — invariants the repository declares, carried into the envelope
only where they apply to the change. Designed; the current implementation
carries a free-form `contract` list from the claim file, and the reviewer is
told explicitly when nothing is declared (absent is not the same as none).

**Reference** — `base...head` SHAs, never an inline diff, plus an **immutable
reference manifest**: every artifact the reviewer must read carried with a
content digest, required-vs-advisory status, and — where the emitter cannot
read it — the label `UNAVAILABLE`. A pointer without a digest is a rumour.
The reviewer's `take` re-probes every reference and labels it
checked / mismatch / unavailable / asserted before a token is spent.
**Material unavailability blocks a clean verdict**, expressed inside the
two-string verdict vocabulary: `changes requested` with an
`## unavailable references` block, one finding per unreachable material
reference.

**Tiering** (designed, not implemented): a deterministic risk score over
declared inputs — diff size, path-class weights, gate signal, novelty —
mapping to independent *reach* and *depth* axes, printing its arithmetic,
failing upward on malformed input. Size alone is a poor risk proxy.

Rejected: full file contents inline; bare diff only; free-form prose briefs;
LLM pre-summarization of the diff (spends tokens to save tokens and inserts a
lossy, non-deterministic authority upstream of the reviewer).

### 3.2 How both sides keep the freedom to think

The reviewer rules with a **two-string verdict**: `VERDICT: clean to advance`
or `VERDICT: changes requested`, the first meaningful line of the verdict,
nothing else counts. Findings are ordered by severity, **atomic** (exactly one
claim per finding id), and each carries Severity, Classification, Title,
Evidence, Why, Required outcome and FALSIFICATION. Optional structured
`Anchor:` and `Citations:` fields make identity robust (below). Every field
is **single-valued**: the first statement is the value, a repeat is a
validation error, and a repeat's text is folded into nothing — so a second,
empty `FALSIFICATION:` can neither erase the named test nor let an
acceptance be recorded without a run of it.

The author answers with a **third envelope**, the disposition, machine-emitted
from a JSON file of the author's judgment: **exactly one disposition per
finding**, from a closed vocabulary of five, each with a mandatory payload.

| Disposition | Meaning | Mandatory payload | Legal for blocking findings |
|---|---|---|---|
| `accepted` | fixed | the change + the verification that flipped the finding's falsification test, recorded as `falsification: {status, mutation[, note]}` wherever the finding names a test (§3.3a) | yes |
| `refuted` | the finding's premise is factually false | live evidence (command output, file citation) | yes — and it obliges the reviewer to answer the evidence, not restate the finding |
| `deferred` | real, but outside this change's decision boundary | a destination **and** a trigger condition | no |
| `preference` | a valid alternative; author's call | the axis of preference and why the reviewer's option is also acceptable | no |
| `escalated` | genuine disagreement the evidence does not settle | the question, both positions, and either the evidence that would settle it or a named decision authority plus criterion | yes — ends the round |

`accepted` carries one structured subtype, `accepted(test_amended)`, for a
falsification test that is itself unsatisfiable (payload: original test,
amended test, why). The reviewer's next closure ratifies or contests it;
silence is neither.

**The symmetry rule.** A finding dies in exactly two ways: the author accepts
and verifies it, or the reviewer withdraws it in response to a refutation. It
never dies by omission, fatigue or round count. The validator enforces
completeness (`F1..Fn` each dispositioned exactly once); the ledger enforces
persistence across rounds. **Reviewer closure events** — `withdrawn`,
`sustained`, `reclassified`, `test_amendment: ratified | contested` — are
grammar (`## closures`, one line per prior fingerprint) and are validated:
a verdict that leaves a refutation or an `accepted(test_amended)` unanswered
fails; a malformed closure line is a defective record, never a silently
dropped one.

Legacy findings that predate the ledger are bound by explicit **import
events** (historical id, verbatim text, source digest, generated
fingerprint); closures bind to the generated fingerprint. The tool ingests
pre-derived import events (`import-legacy`); how they are derived from a
given corpus is that corpus owner's script.

Rejected: a two-word `accepted/challenged` vocabulary (forces deferrals and
preferences to masquerade as challenges); free-text dispositions
(unmeasurable); reviewer-approves-the-disposition (a round trip per finding);
author closes findings unilaterally after N rounds (expiry is not resolution).

### 3.3 How the loop detects that it is running in circles

Four mechanisms, all deterministic, all computed by the tool.

**(a) Mandatory falsification for blocking findings.** A blocking finding
that names no falsification is **downgraded to advisory by the validator**,
not by negotiation. Where the falsification is a runnable command, the design
runs it before the next round and auto-closes on pass (designed; no tool
code executes tests yet). The author's `accepted` must reference the same
test, and does so structurally: `payload.falsification` records `status`
(`pass` | `fail` | `cannot_execute`) for the named test on the head handed
back, and `mutation` (`fails_without_fix` | `passes_without_fix` |
`not_run`) for the same test with the defect reintroduced — the proof that
the test can fail at all. The validator refuses an acceptance whose test
still fails or passes without the fix, and requires a `note` for
`cannot_execute` and `not_run`. `respond` records the run as a
`falsification_run` event bound to the test by digest; the `stale` and
`unverifiable` breakers and the unverified-acceptance metric read it.

**(b) Fingerprints.** Stable finding identity across rounds:
`hash(classification, normalized anchor path, declared anchor, normalized
claim text, declared citations)`, line numbers excluded — computed by the
tool from the verdict, never supplied by a model. The algorithm is versioned
(`fp2:` today), and **alias events** carry identity across a version change
rather than silently re-identifying history. What it provides is *syntactic*
identity: verbatim and near-verbatim repeats collide, paraphrases do not, and
the design says so instead of claiming paraphrase resistance. Two
narrowings the mechanism needed: a finding **declares** which `token:N`
occurrences are citations whose line number moves (inference from token shape
is the recorded weaker fallback), and **lineage fails closed** — a cycle or a
fingerprint merging into two targets raises rather than returning whichever
node the walk stopped at.

**(c) The ledger.** Append-only JSONL per repository, outside the tree by
default: rounds, findings by fingerprint, dispositions, closures, evidence
digests, gate attestations, token counts, cap authorizations, lineage
boundaries. Events are content-addressed (`uid` over the event minus its
timestamp), so re-adding is a no-op. Everything reported is computed from
events, never asserted.

**(d) Circuit breakers.** Any firing **stops the loop** and escalates to the
human, naming the rule and the decision required. Stopping is enforced, not
described: `handoff` refuses (typed `blocked`) while a fired breaker has no
recorded decision covering it. The decision is one of two recorded events —
close the lineage (`close --lineage --reason … --by <who>`), or continue past
that firing by a reason-bearing, actor-bearing authorization (`ledger
authorize-breaker --breaker <name> --reason … --by <who>`), which binds by
identity to the firings of that breaker that are fired and unauthorized when
it is recorded — a firing that did not exist then (a run event arriving
after the verdict, say) is not covered, and a breaker that is not firing
cannot be authorized ahead of time. An empty or whitespace reason or actor
is refused before anything is appended: a required flag proves a token was
supplied, not that a decision was taken. A breaker that only appears inside
an exit-0 request does not break the circuit; this one does.

| Breaker | Fires when |
|---|---|
| **repetition** | a fingerprint returns after `refuted` and the round cites no new *content-addressed* evidence (a new pointer to the same bytes is not novelty) |
| **stale** | a fingerprint returns after `accepted` and a passing falsification test |
| **no-progress** | a completed round adds zero new fingerprints, zero disposition changes, zero new material evidence and no changed falsification outcome |
| **unverifiable** | a blocking finding's falsification test cannot be executed — *blocking* judged per finding (the run event carries its finding's effective blocking state), so a Medium finding's unexecutable test is reported in the record but fires nothing |
| **budget** | round count > cap (default 3), or cumulative counted tokens > declared budget — a partial count is a lower bound; a breach by the bound is real, silence means nothing |

Progress breakers read *completed* rounds only: a round with a request and
no verdict yet is in flight, not spinning. Rejected: "do not repeat
yourself" as an instruction (unenforceable); embedding similarity
(non-deterministic, needs a model to judge a model); author-side veto after
N repeats; escalating severity on repetition (rewards persistence).

### 3.4 Cost as a design constraint

Every metric is derivable from the ledger with no extra instrumentation, and
a metric whose input was not captured says so rather than printing zero.

| Metric | Definition |
|---|---|
| tokens/round | envelope in + verdict out — **declared** by whoever ran the model, never measured by the tool (no model in any code path); four states: no budget / no counts / partial / live |
| rounds-to-clean | verdicts per lineage until `clean to advance` |
| deterministic-preventable share | findings whose falsification names a gate id *already in the manifest* — the feedback loop that makes the process cheaper: each one is a gate that should have run earlier |
| refutation rate | findings refuted with evidence **and withdrawn** ÷ total (the false-positive rate; open refutations are reported separately) |
| unverified-acceptance rate | `accepted` whose verification is not a runnable command |
| envelope bytes | request and verdict sizes — the one number the loop always has, deliberately not converted to tokens |

Dial settings, stated: diff by reference, never inline unless the transport
is blind; gate output by pointer and attestation, always; no LLM
pre-summarization, ever; round cap 3 then escalate (round 4 is where loops
live); auto-close on a passing falsification test because the reviewer named
it.

## 4. Roles are envelope data

Roles are resolved per invocation and stamped on the envelope
(`author=… reviewer=…`), in this precedence:

```
repository review config           # what assignments are PERMITTED at all
  └─ per-invocation stamp          # selects WITHIN that permission
     └─ otherwise: unassigned → the tool refuses to emit
```

Refusing to default is the point: silently inheriting one direction is the
hardcoded convention the design must not have. The validator enforces
`author != reviewer` — the machine-checkable half of "an agent never reviews
its own diff" — and rejects reviewer identities the repository lists as
rejected. Two limits stated rather than papered over: `author != reviewer`
proves two strings differ, not that two authorized actors exist; and a flag
set by a person carries their authority while the identical flag set by an
agent does not, and no tool can tell them apart — which is why flags only
select within what the repository permits.

**The human sets a review round in motion.** Whichever side an agent holds,
it stops where the procedure says stop: the author stops when the request is
emitted and kept; the reviewer stops at the verdict. Neither invokes the
other by any mechanism (CLI, subagent, hook, API), and neither decides
whether a completed review enters the record. This binds agent behaviour; the
tool supports it by having no verb that reaches the other side.

## 5. Transport, reachability, and the verbs

### 5.1 The reviewed SHA must be fetchable

A review binds to a SHA; author and reviewer routinely sit on different
machines; **a SHA the reviewer cannot fetch is not a review target**, and an
envelope naming one is false on its face however faithfully it is carried.
So emission **refuses** on an unreachable target rather than warning (a
warning reaches the author, the envelope reaches the reviewer). Before
emitting, the tool commits outstanding tracked work, pushes the reviewed
branch with an explicit refspec, runs `git ls-remote`, and stamps the
**observed** remote ref — not the push's exit code — as `Push:` and the
reviewer's literal verification command as `Verify:`. Each state is refused
distinctly: untracked files (add or ignore is the author's call), detached
HEAD (the push authorization is bounded to a branch), several remotes and no
upstream (not derivable), rejected push (with git's stderr), post-push
mismatch (concurrent push), base not an ancestor of head. One auditable
exception: a repository with **no remote at all** may emit `--local-only`,
stamped `LOCAL-ONLY` on the envelope's face — an error when any remote
exists, so it cannot become a bypass. Credentials embedded in a remote URL
are scrubbed before stamping.

### 5.2 Envelope transport

Designed options: paste (always works, one human round trip per leg), git
notes keyed by the reviewed SHA (co-location, **not** binding — the validator
still checks the inner SHA against the object; notes are an untrusted carrier
with rewrite, refspec and multi-writer hazards; not implemented, parked
behind a two-clone prototype), committing the envelope on the branch
(pollutes the change under review — rejected), PR comments (turns the
forge into a review bus — rejected). Today the envelope crosses by whatever
`relay` the repository declares — a kept file path on one machine, a paste
between two — and each verb records what it did, so neither end is manual.

### 5.3 One verb per phase, no flag the agent must decide

Every decision the agent makes is a token cost and a drift risk; the tool
decides everything derivable and the agent runs one command.

One flag is required, and it is required precisely because it is *not*
derivable: `take --as <identity>`. The tool cannot observe who is at the
keyboard, and the repository's `[roles] reviewer` names the permitted
direction of review, not the actor — inferring one from the other is how a
review gets recorded against someone who never ran it. So the reviewer
identity is declared, never defaulted, and silence is refused rather than
filled in. The agent still decides nothing: the relay line printed by
`handoff` carries the literal `--as` the reviewer is to run.

- **`handoff`** (author): a lifecycle preflight first — every finding of
  the just-closed verdict has exactly one recorded disposition, and no fired
  breaker lacks a recorded decision — refusing as `blocked` before anything
  is committed, pushed, run or emitted (a finding never dies by omission,
  and the next round is not opened until every one is answered); then
  commit + push + observe, run the gate manifest, emit and validate the
  request, record the request event, keep the bytes under the state
  directory's `exchange/`, print the reviewer's literal command, stop.
  Idempotent: on an unchanged tip with a warm, digest-verified copy it
  returns the same envelope without re-running gates or pushing.
- **`take --as <identity>`** (reviewer): check that the declared identity
  equals the stamp — no identity is inferred, and the relay supplies the
  flag — judge the *target-independent* grammar first (wrapper and
  attributes, the closed section grammar, reference and diff-shape grammar,
  the stamp's syntax: every rule that needs no configuration), so a
  defective envelope causes no git call and chooses no fetch URL; then run
  the stamped fetch, resolve target and base in *this* clone and check
  ancestry, **then** validate the request against the *target
  commit's own* `review.toml` (read from the object store, never from
  whatever this checkout happens to hold — an empty or unrelated checkout is
  not evidence about the envelope; a target that carries no config is
  governed by this checkout's, and the record says which), label every
  reference by reading it from the target tree, record the take, print the
  envelope and the exact diff command — then rule and stop. A defective
  envelope is returned, not reviewed.
- **`respond`** (author): one disposition per finding from JSON, answering
  a verdict that is *valid and recorded* — `--out` resolves it to exactly
  one recorded verdict of the current lineage's just-closed round, derives
  round and SHA from that record (a supplied value may only agree), then
  records and keeps the disposition; an invalid, unrecorded, ambiguous,
  superseded or closed-lineage verdict is refused and nothing is appended.
  Uniqueness is judged over the whole answerable round before any
  caller-supplied key is applied — the file the author points at is an
  agreement check, never a selector among rulings — and a round is ruled
  once: neither `close` nor `ledger add --round` files a second, different
  verdict for a round that already has one. `ledger add` resolves a
  standalone disposition through the same rule.
- **`close`** (author): ingest and record the verdict; `clean to advance`
  closes the **lineage** at that SHA; `--lineage --reason` closes one by
  recorded decision. A new lineage restarts at round 1 with the repository's
  default cap — a cap authorization granted to one review never becomes the
  next one's starting point.
- **`waive --sha … --reason … --by <who>`**: record that a commit was
  deliberately *not* reviewed. The authorizer is declared, never defaulted
  (silence appends nothing), and a commit any lineage — closed or current —
  handed off for review is reviewed, not waivable: the two are exclusive
  facts about a commit, not lineage-local states.
- **`validate`**, **`fingerprint`**, **`ledger add | report |
  authorize-cap | authorize-breaker`** (`ledger add` validates a request
  against the cap in force before recording it, and records the same
  request-plus-evidence shape `handoff` and `take` record), **`emit-request`**
  (the lower-level half of `handoff`), **`import-legacy`**,
  **`migrate-state`**, **`render-adapters`**. Every `--by` is required where
  it appears: who authorized is carried, never inferred.

Exit codes mean exactly one thing — 0 ok, 1 findings or refusal, 2 usage —
and every non-zero exit prints **a typed recovery, not a diagnosis**. The
type is `next_kind`. `command` means `next` holds a literal argv the agent
runs verbatim. `blocked` means `next` is null and `remedy` says what a
*person* must do — malformed TOML, a merge only a human can arbitrate, an
envelope that has to be authored, an identity only the actor can declare.
Some failures genuinely need a person, and a tool that dressed those up as a
command would be inviting an agent to execute a sentence; naming the state
is what lets the agent stop and relay. This holds on the transport surface
too: every refusal a verb raises carries either a literal runnable line
(`loupe <verb> …`, `git …`, joined by `&&`, no placeholder) or nothing, and
the enumeration is checked from the source.
Output is structured JSON when stdout is not a TTY; the agent never parses
prose.
No network in the tool's own code: git performs the one publish step, with a
timeout, so an unreachable remote fails rather than hangs.

## 6. Packaging

**A CLI core**, Python 3.11+, standard library only, no network in its own
code, no model in any code path — the only shape every agent can shell out
to, CI can run, and a human with no agent can use. Three install paths:
`uvx --from git+<repo> loupe`, a vendored directory committed into a
repository that opts in, or a `~/.local/bin` deploy. No package index.

**Per-agent adapters, generated, never hand-written.** One source renders a
Claude skill, a Codex skill and an agent-neutral instruction block; the verb
table is read from the CLI parser itself, so an adapter cannot describe a
verb the tool lacks; `render-adapters --check` is a drift gate. Adapters may
name the tool and carry literal command lines; the user's standing agent
instructions need not name it at all — the process keeps working with
nothing installed.

That gate guards the *tracked* copies. What an agent loads is a file in its
own directory, and rendering a procedure nobody reads is not a procedure —
so `render-adapters --install` deploys the rendered copies to those paths and
`--check-install` reports drift there. The install is idempotent, refuses to
run from a tracked copy the gate would reject rather than spreading it, and
never clobbers: a target whose bytes differ is `replaced` only after what it
held has been written, fsynced and read back at the kept path, and the row
names the digest and where the bytes went — a copy someone edited by hand and
one left by an older version are indistinguishable from the tool's side, so
both are preserved rather than guessed at. Preservation is a precondition,
not a best effort beside the overwrite: where it cannot be proven — no
retention directory, an unwritable one, a kept path holding other bytes, a
copy that does not read back — the row is `failed` and the target is left
exactly as it was. Retention elsewhere in this tool degrades to a pointer
because the bytes survive in the record anyway; a user's edited procedure
survives nowhere else. And because the verb writes to several places, a
refusal carries every target it already touched, so a partial deployment is
never reported as none. `--check-install` is deliberately not in the gate manifest: a
machine that has never installed the skill is not a broken build.

**MCP wrapper: deferred, not rejected.** It buys nothing the CLI lacks while
agents can run a shell command, and it cannot run in CI, so it could only
ever be a facade over the core, generated from the same command table.

## 7. Migration path for an existing review harness

Extract, prove, change nothing: run the validator against a repository's
existing envelope corpus under a **dialect** (wrapper tag + enums supplied as
config) and require byte-identical accept/reject decisions. Then delegate:
the repository's own review wrapper becomes a thin call into the tool with
`--config`, keeping the wire. Then opt in, per repository and on green
tests, to the v2 grammar (disposition envelope, fingerprints, ledger,
breakers). Enforcement — required checks, merge gates — moves to CI only
where a repository wants it, and is explicitly not driven by the extraction.
Dialects drift permanently and on purpose: the tool never unifies enums; it
unifies grammar and rejects unknown values.

## 8. What the tool must never carry

Grammar, mechanisms and metrics travel; vocabulary, identity and thresholds
stay behind as configuration. Concretely, this repository's own development
history — the review corpus the fingerprinter was first tested against, the
identities and repositories it was extracted from, the thresholds one
project chose — is not here. The public test suite runs on synthetic
fixtures produced by the real emitter and small generators; the tool's
claims about its own history are made where that history lives.

## 9. Status — implemented, designed, not run

Implemented and tested (standard-library unit tests, plus a real two-clone
loop over a bare path remote): the three envelopes and their validators; the
gate manifest with field-by-field attestation validation; roles and stamps;
fingerprints v2 with declared anchors and citations, alias lineage and
fail-closed resolution; the ledger with lineage scoping; the five breakers;
the metrics with honest token states; reachability (push, observe, stamp,
verify); `handoff` / `take` / `respond` / `close`; the former-name dialect
acceptance and state migration; adapters rendered from one source.

Designed, not implemented: risk tiering (reach × depth); path-scoped
contract invariants; automatic execution of runnable falsification tests and
auto-close; the git-notes carrier; an MCP facade.

Known limits: syntactic fingerprint identity only; token counts are declared,
never measured; the tool cannot distinguish a person from an agent using the
same credential, and says so rather than pretending server-side controls do.

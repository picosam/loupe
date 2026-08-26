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
The wrapper's own attribute text is closed the same way — the attributes
(`sha`, `branch`, `author`, `reviewer`, `round`, `transport`, `tool`, and
the disposition's `verdict_sha`/`head`) — occurring once each: the first
declaration is the value, a repeat is a validation error, and text between
the tag name and `>` that is not a `key="value"` pair is refused — so a
defective wrapper cannot choose the SHA that validation, the fetch and the
ledger bind. All of this is judged before any git call. "Closed" here means
each attribute occurs once and nothing but attributes appear; it does not
mean an unlisted NAME is refused, and that is deliberate — an older
installation must be able to read an envelope carrying an attribute it has
never heard of, or every addition would be a flag day.

`tool` is the emitting installation's content identity (§3.3e).
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

The rule has one terminator. Some domains terminate by exhaustion, because
their completeness can be established from inside the artifact — a parser's
admitted input kinds, a closed grammar's members. Others cannot: "every
travelling artefact whose bytes affect behaviour" is a set that can be
declared but never proven complete from within, so on such a domain the
rule alone can always demand one more point, and a competent reviewer will
always find one. Such a domain may be declared closed relative to a stated
authority: a machine-generated, separately gated artifact that derives the
set — never a hand-maintained list, because every hand list in the record
reproduced the defect one level down. The author declares the authority in
the claim, per anchor, and the reviewer rules on it in the same envelope:
the authority's derivation and coverage are attackable, and an instance
finding against a declared-closed domain must name the authority it
escapes — that citation is what makes it a completeness finding rather
than the next round of an unbounded hunt. Rejecting the authority is an
ordinary finding, anchored on the authority itself. The declaration is not
an exemption: it moves the attackable surface from an unbounded hunt onto
a finite artifact, and a narrow authority is itself the defect.

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
falsification test that is itself unsatisfiable (payload: `original_test`,
`amended_test`, `why_unsatisfiable`). The reviewer's next closure ratifies or contests it;
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

Five mechanisms, all deterministic, all computed by the tool.

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
cannot be authorized ahead of time. A firing's identity is its breaker, its
round, the fingerprint or limit it fired on, **and the content identity of
the material it fired on** — the companion events of one orphan batch, the
one unexecutable run, the measured token spend. The key alone is not an
identity wherever the evaluation admits more than one firing per key, or the
same key over changed material: a decision on the first unmatched companion
would take every later one, a decision on one unexecutable run would take
the next, a decision at 400 tokens over budget would take 10 000. Identity
is what the human read, so the same facts re-read stay decided and any
further material is a new firing that stops the loop again. An older
ledger's coverage string, written before the material half existed, no
longer matches — which fails closed: the breaker fires again and the human
decides again. An empty or whitespace reason or actor
is refused before anything is appended: a required flag proves a token was
supplied, not that a decision was taken. A breaker that only appears inside
an exit-0 request does not break the circuit; this one does.

**The round count advises; everything else refuses, token budgets
included.** `budget` covers two limits and they are not the same kind of
fact, so they do not get the same treatment.

`limit = rounds` is a PROXY. It says only how long the loop has run: it
fires on lineages doing exactly what they should, and stays silent on ones
that are genuinely stuck, because it measures duration and the question is
direction. It is still evaluated and still reported, and it no longer
refuses; `ledger convergence` answers the question it was standing in for,
and a handoff past the cap carries that answer with it rather than a
number.

`limit = tokens` is MEASURED, against a ceiling the repository declared,
and a partial count over that ceiling is a lower bound whose breach is
real. It refuses exactly as it always did, and continuing past it still
needs a reason-bearing recorded authorization. Every other breaker points
at something the record shows went wrong — a finding that returned after a
refutation, a run that cannot execute, a companion that answers no
emission — and stopping the loop on those is the point.

**Convergence (§3.3f).** Two signals, read from the ledger. `stalled` — a
finding identity the reviewer has refused to withdraw across rounds: the
loop failing to close one claim. `hunting` — an anchor whose findings are
NEW identities round after round; every one may be real and every fix may
have landed, and the domain still never closes, because what is being
asked for is completeness over a set nobody can enumerate from inside.
That second shape is invisible from inside any single round — each finding
true, each fix accepted, the same anchor back next time — and invisible to
a finding count, which can fall while it happens. Neither signal is a
proof: a sustained thread can be a reviewer who is simply right, and a
hunted anchor can be a rich domain worked through in order. What the
report gives a human is the shape of the loop over its whole length.
`closing` means what the word says — inflow falling AND findings
being withdrawn; a loop opening one finding every round and closing
none is `steady`, which is neither an alarm nor a reassurance.
Calling that `closing` was the first version's error, and it
mattered because this report replaces a hard stop.

| Breaker | Fires when |
|---|---|
| **repetition** | a fingerprint returns after `refuted` and the round cites no new *content-addressed* evidence (a new pointer to the same bytes is not novelty) |
| **stale** | a fingerprint returns after `accepted` and a passing falsification test |
| **no-progress** | a completed round adds zero new fingerprints, zero disposition changes, zero new material evidence and no changed falsification outcome |
| **unverifiable** | a blocking finding's falsification test cannot be executed — *blocking* judged per finding (the run event carries its finding's effective blocking state), so a Medium finding's unexecutable test is reported in the record but fires nothing |
| **budget** | `rounds`: round count > cap (default 3) — **advisory**, reported and does not refuse. `tokens`: cumulative counted tokens > declared budget — a partial count is a lower bound; a breach by the bound is real, silence means nothing, and it **refuses** pending a recorded authorization |
| **orphan** | a disposition companion event (a falsification run, refutation evidence) binds no recorded emission — its batch stamp matches no row, or no row exists for its key. An orphan is kept on the audit surface, is never a standing answer, feeds no other breaker and certifies no acceptance; it fires until a human decides what it is |

Progress breakers read *completed* rounds only: a round with a request and
no verdict yet is in flight, not spinning. Rejected: "do not repeat
yourself" as an instruction (unenforceable); embedding similarity
(non-deterministic, needs a model to judge a model); author-side veto after
N repeats; escalating severity on repetition (rewards persistence).

**(e) Tool identity.** The two sides of a round run two *installations* of
this tool, and they can differ while agreeing on `TOOL_VERSION`. Every
envelope the tool emits carries `tool`, a 16-hex digest over the travelling
artefacts that determine its behaviour, by **logical path** and content —
an identity of the *declared travelling behavioural set, per that declared
authority*, never of "what the installation is": the guarantee is relative
to the enumeration, and the enumeration, not the claim, is what a reviewer
attacks (the boundary-closure terminator above, applied to the tool's own
identity). The set: the
package modules, and the launcher of every advertised install path —
`bin/loupe` for the two copy-based paths, and `pyproject.toml`, whose
`[project.scripts]` entry is what the `uvx` path builds its console script
from. An identity over modules alone would call two installations equal
while one of them refused to start; an identity covering one launcher and
not the other would do the same for one install shape out of three.

*Logical* path because extraction maps `public/X` to `X`: one artefact is
`public/pyproject.toml` in the workbench and `pyproject.toml` in the tree
extracted from it, and the identity must call those the same file or the
two would never agree. What the digest does **not** cover is a built
wheel's generated console script — it is not readable from inside the
installed package — so what is covered is the input that produces it. That
is the limit, stated rather than claimed away.

The set is enumerated per artefact, never by prefix: every travelling file
is carried or excluded with a reason, and a gate asserts that enumeration
equals what actually travels in both directions. A prefix rule would let
the next behavioural file inherit an exclusion nobody decided, which is
how the `uvx` launcher went uncovered for a round.

Each stamp has a named reader, and a stamp without one is decoration. The
readers are a closed set (`vocab.STAMPED_READERS`), each classed by whether
the envelope can have crossed an installation boundary before reaching it.
That set is not trusted on its own: `vocab.STAMPED_PARSE_SITES` attributes
every place the production code parses a stamped envelope to the seams it
serves, and a gate walks the source for those parses and fails on any it
does not name. A reader authority checked only against another hand-written
list cannot discover the reader nobody wrote down, which is how the warm
cache stayed silent for a round.

| stamped | written by | compared by, and why |
|---|---|---|
| request | `handoff` | `take`, `brief`, `validate`, `ledger add`, and the warm `handoff` cache — every verb that reads one. `brief` matters most: it renders the command a human carries, which is exactly what a stale installation gets wrong. The cache matters for the same reason and is easier to miss: re-running `handoff` on an unchanged tip returns a request RETAINED by an earlier run, so the reader and the writer are different processes and may be different installations |
| disposition | `respond --out` | `validate` and `ledger add`. Not `respond --out` itself: it writes and records in one process, so a comparison there could only ever say `match`, and a check that cannot fail is worse than a stated absence |
| verdict | *nobody* | — |

The verdict is **not** stamped. It has no emitter: the reviewer agent
hand-authors it from a shape block, so a stamp there would be a model
transcribing a digest, which proves nothing about the code that ran. An
unverifiable field is worse than an absent one.

A reader reports one of three states: `match`, `differs`, or `unstamped` (an
envelope written before this existed — historical silence, which is not
agreement). Both readers render all three where a human can see them and
record them in the ledger, so the record says which installation ruled
instead of leaving it to be inferred from an absence.

It reports; it does not refuse. The envelope is sound either way — a stale
installation validates a good envelope perfectly well, which is exactly
what made the drift invisible. What differs is everything the reading end
*renders*: the relay, the commands handed back to the human. And a digest
carries no ordering, so `differs` cannot tell a reader that is behind from
one that is ahead; refusing would block the case where the reviewer's tool
is the better of the two. The rule is: refuse where the tool can be certain
something is broken, report where it can only be certain something is
different.

Why a digest and not the version: on 2026-08-23 a reviewer ruled a round
with an installation grafted from three fixes earlier and relayed a command
carrying a shell syntax error that the round under review existed to fix.
Both installations honestly reported `0.4.0`. Bumping the version inside the
reviewed round (§9) makes publications distinguishable; it structurally
cannot make two builds of one version distinguishable, and that is the case
that fired. The same argument the gate attestations have always made about
the executables they run — a version string is what a tool says about
itself, the digest is what it is — now applies to this tool.

One stated limit: the wrapper grammar has no attribute allow-list, so an
installation predating `tool` reads a stamped envelope without complaint and
without comparing. Detection is one-directional until both ends carry it.

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
Both legs read a paste: the request leg through `take -`, the verdict leg
through `close --verdict -` and `respond --verdict -`.

**The topology is declared, not detected (RVW-T11).** Which of the two cases
a round runs in is a fact about the pair of machines, and no process can
observe it: each side can see where IT runs, while the commands each side
prints are about where the OTHER side runs. So `transport` joins the role
stamp as a declared, closed-vocabulary input — `path` when both ends read the
same filesystem, `paste` when they do not — defaulting to `[roles] transport`
and selected per round by `--transport`. It is stamped on the envelope's
wrapper, recorded in the ledger by every verb that files the round, and it is
what decides the carrier each relay prints: `path` renders the kept-path
command and nothing beside it, `paste` renders `take -` and `close --verdict -`
with the bytes. The reviewer may correct an author's stamp with `take
--transport`, and the correction is recorded beside the value it corrects
rather than replacing it.

The declaration is ONE lifecycle boundary over every source and reader
(R1-F2): config value, author flag, environment declaration, reviewer
correction, wrapper stamp and ledger record all resolve through a single
schema-derived reader. On the READING side absence is the historical
default, `path`: a config without the key, an unstamped envelope, a record
that predates the attribute — every silent artifact written before the
attribute existed came from the same-machine loop, and reading that
silence as anything else would make the historical record unreadable. An
explicitly empty value, a value outside the vocabulary, a repeated or
conflicting selector, and a defective recorded value are refused BEFORE
anything downstream acts — before a ledger write, a git call, a gate run,
a cache serve or a runnable relay — because each of those failure shapes
used to fall open to `path`, the exact direction §5.2 names as harmful: a
pointer printed for a far end that cannot open it. Refusal lives in the
reader itself, not in one validating verb a caller has to remember:
`brief` is as much an official relay reader as `take`, and a closed
lifecycle holds at every entry or at none.

On the EMISSION side a new round resolves through one stated order,
strongest declaration first: the explicit `--transport` flag; `[roles]
transport` in the governing config; the environment's own declaration,
`LOUPE_TRANSPORT` — a cloud environment's configuration sets it to
`paste`, because bytes are the only carrier that reaches the operator's
machine from a sandbox; a documented provider signal (below); the `path`
entailed by `--local-only`; and finally `path`, the undeclared steady
case — this workflow's declared topology is the operator's machine
running both sides, and the lineage-6 incident (a silent `path` emitted
to a cross-machine reviewer) is answered by the cloud side CARRYING a
declaration, not by pricing every ordinary local round at the
cross-machine carrier, which is what the first repair did and what left
the executable default, the CLI help and this document contradicting one
another (round 3 F2).

The provider signal is deliberately narrow. Open-ended inference stays
rejected — `take -` on stdin, a path that does not resolve, an env var
read for what it might mean: each reports the local side only and fails
open. What is admitted is a CLOSED matrix of documented (variable, exact
value) pairs in `vocab.TRANSPORT_PROVIDER_SIGNALS`, under one stated
workflow assumption: the other endpoint — the relay the operator drives —
is the operator's local machine, so an author endpoint known to be a
cloud sandbox does not share its filesystem. Today the matrix holds
exactly `CLAUDE_CODE_REMOTE == "true"` → `paste` (Claude Code cloud;
repository evidence, recorded 2026-08-11). Codex cloud is deliberately
absent: current official OpenAI documentation guarantees user-configured
environment variables persist through a cloud chat and documents no
intrinsic cloud/topology marker, so a Codex cloud environment declares
`LOUPE_TRANSPORT=paste` in its own configuration instead of being
sniffed. A signal ranks below every human declaration and above only the
silence it disambiguates.

Three consequences follow from having the fact rather than guessing it. The
same-machine round — the steady case — pays nothing: one line, no
alternative, no prose for a relaying agent to reword. The cross-machine
round gets exact commands instead of a hedge, and its envelope bytes ride
INSIDE the printed fence on both legs — the request under `take -`, the
verdict under `close --verdict -` — because a chat surface preserves a
fenced region verbatim and rewrites everything else (round 3, live: a
validated verdict lost its markdown headings crossing a chat as loose
prose). And the reachability leg stays refusable: `--local-only` and
`transport = "paste"` are a contradiction — a SHA fetchable from nowhere
handed to a reviewer with no access to this filesystem — so emission
refuses it before the commit, whichever source resolved the `paste`,
rather than stamping it for the reviewer to discover at `take`.

**Decided: paste is the transport, and there is no other.** A carrier that
reaches a party with no access to the other's filesystem — a session
fetching the bytes itself, a forge-mediated transport — is deliberately
NOT provided. Not "not yet": providing one means either teaching the tool
to fetch from somewhere, which makes it a client of a service the review
does not control, or routing envelopes through a forge, which is the
review bus this section already rejects. A person moving bytes is a
transport that works everywhere, needs no credential, and cannot fail
open. The cost is stated rather than hidden: an author and a reviewer on
different machines each need a human paste per leg, and the two-machine
flow has never been exercised end to end. If that cost ever becomes the
wrong one, the decision — not the code — is what changes first.

### 5.3 One verb per phase, no flag the agent must decide

Every decision the agent makes is a token cost and a drift risk; the tool
decides everything derivable and the agent runs one command.

Two flags are required, and both are required precisely because they are
*not* derivable. The first is `take --as <identity>`. The tool cannot observe who is at the
keyboard, and the repository's `[roles] reviewer` names the permitted
direction of review, not the actor — inferring one from the other is how a
review gets recorded against someone who never ran it. So the reviewer
identity is declared, never defaulted, and silence is refused rather than
filled in. The agent still decides nothing: the relay line printed by
`handoff` carries the literal `--as` the reviewer is to run.

The second is `--transport` (§5.2). Nothing a process can read tells it
whether the other side shares this filesystem, and unlike the identity
there IS a safe silence: the repository declares its standing case in
`[roles] transport`, a cloud environment declares its own in
`LOUPE_TRANSPORT`, and with neither the tool resolves the workflow's
steady case, `path` — so the flag is the exception a human names when they
know this round is different. The agent decides nothing here either — it
passes what it was told or nothing at all, and the relay it hands over
carries the one carrier the round declared.

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
  **`migrate-state`**, **`render-adapters`**, **`prune`**. Every `--by` is
  required where it appears: who authorized is carried, never inferred.

The emitting verbs take an optional `--author` / `--reviewer` — the §4
per-invocation stamp, selecting WITHIN the config's permitted lists.
Outside them it is refused, and so is a flag against a repository that
declares no list: with nothing declared there is no permission to select
within, and an unconstrained flag would put arbitrary identities into an
append-only record. A repeated selector — same value or conflicting,
either order — is refused naming both occurrences, never collapsed to the
last writer. Every invariant binds the EFFECTIVE identity whatever its
source: an UNASSIGNED side refuses at resolution (empty is a domain
member, and a malformed permitted list carrying the empty string cannot
admit it as a selection), a config-defaulted rejected or unpermitted
identity refuses exactly as a flagged one does — `rejected_reviewers`
outranks the permitted list, self-review is refused for every pair — and
resolution precedes the ledger, the cache, the commit, the push and the
gate run. The effective stamp is part of what makes a kept envelope warm:
a cached handoff emitted under one direction never answers an invocation
that selected another.

A REQUIRED reference must be target-bound, judged at the same boundary,
in three layers — an unavailable or unrecognised required reference
forbids the clean verdict the round seeks, so every one of them refuses
before the ledger, the push, the gates and emission.

*The path*, as the wire can carry it — checked for EVERY reference,
required or advisory, at the preflight and again at the render boundary.
A manifest line is whitespace-delimited, so the admitted grammar is
deliberately restricted rather than quoted: ordinary relative paths,
including non-ASCII names, are carried; whitespace, control and invisible
characters, a leading `-`, a trailing `/`, `.` and `..` segments, doubled
separators and absolute paths are refused, each by its own name. One
character class defines what the emitter may render and what the
reviewer's parser recognises, so the two cannot drift apart.
Requiredness decides a policy — whether unavailable evidence blocks a
clean verdict — and never the syntax the line needs to carry a row.

*The tracking state.* `take` reads references from the target tree, so a
required reference that is ignored, untracked, missing, deleted-though-
tracked, or escaping the repository root is refused: a digest over bytes
no commit carries binds nothing.

*The object mode*, read from the index and an lstat BEFORE anything
follows the final component — target-following checks decide only the
states whose policy depends on the referent, or a dangling link reports
as a deleted file and a link pointing outside the root as an escaping
path. A regular file, executable or not, and a directory prefix are
readable the same way by both sides. A tracked SYMLINK and a
GITLINK are not — the emitter would digest the bytes a link points at
while `take` digests the link, and a submodule's bytes live in another
repository — so both refuse, and a regular file replaced by a link since
the last commit refuses with them, because the handoff commits what the
disk carries.

Kind and digest are derived from the TARGET TREE by one shared pair of
functions, so the manifest the author renders and the states the reviewer
computes cannot describe an object differently. Material the target tree
does not carry is declared UNAVAILABLE at emission rather than digested
from local bytes. Advisory references keep the three-state rendering
(file-with-digest, directory, declared UNAVAILABLE): declared-unavailable
is the author's honesty mechanism for material that genuinely cannot
travel, and that state is the reviewer's to weigh.

Commands are BUILT ONE WAY, and the fields that carry them check it.
Every command the tool prints — relay lines, typed `next` recoveries, the
reachability stamp's `Verify:` line, the diff commands both sides print —
comes from one renderer that takes the command's words, each word a
validated type that cannot cross into another: a shell-inert literal (a
verb, a flag — nothing a shell acts on can be spelled in its grammar), one
of the two operators the tool writes deliberately (`&&`, `<`), or a
`<placeholder>` a person fills in, whose quotes and brackets exist only in
balanced pairs the grammar itself writes. Every other word is a dynamic
value quoted as it is built, so no later moment exists at which it could
still be unrendered. A third form covers the surfaces where a quote would
corrupt the artifact rather than protect it — a generated verb table, a
usage hint: the value is PROVED shell-inert and refused if it is not.
Nothing is exempt for not being a path: a reviewer identity and a Git ref
are dynamic words like any other, and Git accepts a ref name carrying a
semicolon.

The boundary is STRUCTURAL, not the shape of the source. A rendered
command is its own type carrying its construction record, and the fields
an agent executes accept that type and refuse both a bare string and a
type-forged instance without the record. A command containing a
placeholder is a TEMPLATE — a distinct type those fields refuse by name —
so a line a person must finish travels only as prose or a `#` comment,
never as a field an agent runs verbatim. What the executable fields carry
is therefore, by construction: inert words, intentional operators, and
quoted single arguments. Reading the source remains a drift detector
beside the boundary, over every module of the package recursively, and it
fails closed — but it is best-effort evidence, not the invariant: Python
admits indefinitely many spellings of a call, and the guarantee does not
rest on enumerating them. A deliberately malicious in-process author who
forges private objects is outside this threat model; the boundary closes
every ordinary construction route, and the claim stops there.

**Retention.** The state directory's layers are not equal. The ledger is
append-only and is never pruned; `exchange/` — the kept envelopes — is the
review record itself and is never pruned; retained gate output is the one
designed-pruneable layer, because every attestation carries the sha256 and
byte count of its output, so a missing retained copy degrades the pointer
without losing the identity. `prune` executes exactly that policy: it
removes plain gate-output directories for commits no ledger event
references — the common orphan is a refused emission attempt, which
retains gate output and records nothing — and keeps everything else,
NAMED by class in the result: referenced directories, symlinks (live or
broken, never dereferenced — on a deletion boundary the filesystem object
type is authority, so classification never follows a link and nothing
outside the layer is read or written even when a crafted or stale link
points elsewhere), non-directories, non-SHA names, and entries whose type
cannot be read. That rule starts at the CONTAINER: `gate-output/` itself
is opened no-follow, directory-only, and enumeration, accounting and
deletion are all anchored to that one descriptor — a linked,
non-directory or unreadable container refuses at the open. For a container
RENAMED, REPLACED OR REMOVED after that, the enforceable invariant is
stated as what it is: prune refuses a replacement OBSERVABLE AT ITS
COMPARISON POINTS, and descriptor anchoring keeps a replacement made at
any other moment from redirecting traversal, accounting or deletion. The
opened identity is compared with the one the path names at four points —
at the open, before each entry's decision, after that entry's accounting
and before its removal or dry-run record, and once more before the run
reports success — so dry-run and act refuse on identical states, and a run
with one entry or none refuses like a run with many.

Two intervals remain irreducible, and both are part of the contract rather
than excluded from it. Between the last comparison and the removal
syscall, a replacement is not detected and the deletion still runs against
the verified inode inside the state root. Between the final comparison and
the successful return, a replacement is not detected either, so a run can
report success while the state directory already names something else —
nothing outside the layer has been read or written, and nothing further
has been deleted. No check can close either interval; only an atomic
ownership mechanism could, and the tool does not claim one.
Removal deletes nested links rather than their targets,
the freed-bytes accounting walks without following either, and
`--dry-run` reports the identical decision without acting on it.

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

**A CLI core**, Python 3.14.x, standard library only, no network in its own
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
gate manifest with field-by-field attestation validation; roles and stamps,
including per-invocation selection within the permitted lists (§4);
fingerprints v2 with declared anchors and citations, alias lineage and
fail-closed resolution; the ledger with lineage scoping; the breakers
(`repetition`, `stale`, `no-progress`, `unverifiable`, `budget`, `orphan`);
the metrics with honest token states; reachability (push, observe, stamp,
verify); `handoff` / `take` / `respond` / `close`; state retention with
`prune` (§5.3); the former-name dialect acceptance and state migration;
adapters rendered from one source.

Designed, not implemented: risk tiering (reach × depth); path-scoped
contract invariants; automatic execution of runnable falsification tests and
auto-close; the git-notes carrier; an MCP facade.

Known limits: syntactic fingerprint identity only; token counts are declared,
never measured; the tool cannot distinguish a person from an agent using the
same credential, and says so rather than pretending server-side controls do.

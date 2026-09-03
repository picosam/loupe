# Changelog

The version is bumped inside the reviewed round of any change that will be
published, so `--version` discriminates publishes. Newest first. Sections
addressed to upgraders say so; read them before upgrading across the version
they name.

## 0.16.0

**An unknown section or key now says that a version skew is possible.**
`[tool] requires` (0.15.0) lets a repository state the floor it was written
for, and closes the class for every pair of installations that both know the
key. What it cannot reach is a config written for a newer tool that declares
no floor: from an older reader that looks exactly like a misspelling, and the
remedy sent a person to repair a file that may be correct. The reader cannot
tell the two apart, so the refusal now names both — the defect and the skew —
along with its own version and the `[tool] requires` declaration that would
make the diagnosis exact. Only for an UNDECLARED name; a declared key with
the wrong kind is the config's own defect in every version and says nothing
about versions. Forward-only, like every fix on this seam: it reaches the
skews that come after it, never a reader already deployed.

**The claim's scope is compared with the span it was measured against.**
A claim can assert "no other file touched" while the machine-computed
base-to-head span carries another commit's paths — that happened, and only a
reader noticing caught it. Both author verbs now report every changed path
the claim names nowhere: not a reference, not in the review scope, not a
stated exclusion. It rides out as a `scope` field beside the record and a
notice line beside the text, and it NEVER changes the exit — an unaccounted
path is a claim to fix or a rationale to add, and the author is the one who
knows which. Paths are matched whole, so `data.txt` does not account for
`a.txt`; a claim that states nothing reports nothing, since it asserts no
scope to contradict.

**Retained gate output is per run, so a failure survives the next run.**
It was keyed by the executed SHA alone — `gate-output/<sha>/<id>.log` — so
a second run at one commit overwrote the first in place, and the copy that
matters is always the failing run's. Measured twice, most recently
2026-09-02: a handoff refused on a failing gate, the re-run passed, and the
log a person then opened was the passing run's. Each run now writes under
`gate-output/<sha>/<run>/`, named by UTC second plus six hex characters, and
`<sha>/newest` is a symlink to the last one. Every attestation's pointer
names its own run's bytes. `prune` is unchanged: the pruneable unit is
still the per-SHA directory.

**A ruling is owed an answer wherever it was made.** The handoff preflight
took the latest verdict round and looked for unanswered rulings only there,
so a ruling made in any other round could not be owed — and a semantic-only
legacy corpus imports rulings at rounds with no verdict artifact at all.
The owed set is now derived from the ruling authority: the newest ruling of
every identity in the lineage, minus every identity the record shows an
answer for, with the lifecycle's own round-binding on both sides. Answered
means any recorded answer — the author's disposition, the reviewer's
closure, or a named human's waiver — because omission is what this check
guards and a ruling one of the three answered was not omitted.

**A cited legacy source path is a canonical path.** `source_path` is
caller-supplied and interpolated into `<commit>:<path>`, and git resolves
`.` and `..` — so `a/../b` read b's bytes while the record named a/../b as
the source. Dot segments (a dot INSIDE a name, `.gitignore`, is untouched),
leading and repeated separators, and NUL or newline framing now refuse
before git is asked anything, and the tree entry git returns must be the
entry that was requested.

**`take` no longer fetches a foreign target into the checkout you are
standing in.** Measured live 2026-08-31: run from the wrong directory, `take`
resolved the round against the CALLER's repository, fetched the reviewed SHA
into it — which made the foreign commit resolve locally and removed the only
signal that anything was wrong — and appended another repository's round to
this one's append-only ledger, where it cannot be removed. The reviewer-side
probe now compares repositories BEFORE the fetch: the stamp's push URL
against this clone's remotes, normalised so one repository is one key across
the spellings git accepts (`ssh`, `https`, `scp`-style, with or without
`.git` or userinfo). Three states pass — a remote of this clone names the
stamped repository, this clone already holds the target, or this clone
declares no remote at all (a scratch checkout, which is what an empty CI
reviewer is, and which contradicts no stamp). Anything else refuses before
the fetch and before any event is written, naming both URLs.

**The envelope can ride a ref now, on the remote both sides already use.**
`transport = "git"` joins `path` and `paste` as a third topology: the two
ends share no filesystem AND both reach the remote the reviewed branch is
pushed to. `handoff` pushes the request to
`refs/<tool>/<lineage>/<round>/request`, `validate --from-target` pushes the
verdict beside it, `respond --out` pushes the disposition; `take`, `close
--verdict` and `respond --verdict` accept `git:<lineage>/<round>` wherever
they accept a file or `-`, and fetch the leg the verb needs. Each leg's relay
becomes ONE line naming the round instead of a block of bytes. The object is
a bare blob rather than a commit wrapping a file — no invented author, tree
or timestamp — and the refspec is forced inside that namespace alone, because
a blob ref has no ancestry to fast-forward. The ref is an untrusted carrier
and confers nothing: bytes replaced on it are refused by the same digest and
SHA bindings that would refuse them pasted, and storage is still not a
trigger — a pushed ref starts no work, and a person still tells the reviewer
to take. Paste is unchanged and stays the fallback for a remote that cannot
be reached; `--local-only` refuses `git` exactly as it refuses `paste`. This
reverses one sentence of design §5.2 by the user's decision, and only that
one: PR comments, a commit on the branch, and git notes stay rejected, with
their reasons.

**The configuration says what it never said.** Two keys were ruled the same
day. `[roles] review_default` (`on` | `off`, undeclared `on`) declares
whether an implementation session ends with a round unasked; no verb gates on
it — no invocation of this tool is the end of a session — so it is a
declaration the agents read. `[roles] enforcement` (`pr-approval` | `none`,
undeclared `none`) declares how the approval reaches the default branch, and
the tool does exactly one thing with it: `handoff` refuses when
`pr-approval` is declared and the branch under review IS the remote's default
branch, since a commit already there has no pull request for an approval to
bind to. Opening the pull request, posting the approval and merging stay
outside a tool that touches no forge. **Upgraders:** both keys are a
cross-installation contract — a repository that declares one becomes
unreadable to any installation older than 0.16.0, which refuses with
`[roles] states unknown key` — so declare them only once the readers that
parse them are in place, and use `[tool] requires` to make the refusal say
so.

**A built-in default is now reported instead of applied in silence.** Absence
means one of two things in this tool: a refusal that is ruled and stays — the
taxonomy, the roles, the config file itself — or a default nobody chose and
nobody was told about. Every `handoff`, `take`, `emit-request` and `brief`
result now carries a `decide` list: one entry per key THIS repository never
declared, with its meaning in a sentence, the value applied, and the exact
TOML line that sets it and (where there is one) unsets it. Six keys are
covered: `[roles] transport`, `debug`, `review_default`, `enforcement`, and
`[limits] round_cap`, `token_budget`. A declared key produces no entry, which
is what ends the reporting permanently. The tool stops there by construction:
it has no model and prints JSON, so turning one report into one question
asked once is the adapters' side of the boundary.

**The trust model is stated, and it has one principal.** Lineage 20 spent
seven rounds closing ways the operator's own agents could forge the
operator's own ledger — a fabricated import row, a hand-written remote ref, a
substring that substantiates an acceptance — each answered with a mechanism
that the next round found another way around, because there is no bottom to
defending a record against its owner. The contract now says what the tool
guards: the record against omission and fatigue, never against a party with
operator access. Both adapters carry the rule beside boundary closure, which
partitions the domain of mistakes a boundary admits, not the domain of
adversaries; a forgery finding must name a party with less than operator
access who could mount it, or it is not a finding for the round. No
behaviour changed; the two residual non-security items from that lineage's
last verdict are parked in the workbench backlog.

**Imported disposition rows may carry `author`, and only a reader at or
after this change accepts them.** The closed import schema refuses an
unknown member by name, so a reader older than this change — 0.15.0, or a
0.16.0 build before the field existed — REJECTS a row that carries `author`
rather than ignoring it. That is safe failure, not additive interoperability:
a derivation that states the field needs the new reader, and the release
note says so instead of promising an older one will preserve the rest. A
row without `author` is accepted by every reader, unchanged.

## 0.15.0

**A named human can now answer a finding, and advance over it.** A finding
died exactly two ways — the author accepts and verifies it, or the reviewer
withdraws it against a refutation — and neither is what happens when a person
reads a finding and decides to live with it. `escalated` already routed a
blocking finding to a named authority; nothing recorded what that authority
answered, so an escalation either looped or was laundered into a reviewer
withdrawal the reviewer did not mean. After that laundering the record cannot
separate "the reviewer found nothing" from "the reviewer found something a
human waved off", which is the distinction the whole loop exists to keep.

`waive --finding <id|fingerprint> --reason … --by …` records that one finding
stands unfixed, with an optional `--destination` and `--trigger` for work that
moved rather than vanished. It refuses a silent reason or authorizer, a
finding this lineage never ruled, a second answer to a finding already
answered, and an id that names different findings in different rounds — ids
are round-scoped, so the fingerprint disambiguates. `authorize-advance
--reason … --by …` then emits an **authorization** envelope naming the commit,
who advanced it, why, and every overruled finding with its own reason — a set
DERIVED from the ledger's lifecycle authority rather than collected from
whatever waivers happen to exist, so an advance refuses while any standing
finding is unanswered, and refuses while a newer request awaits a verdict.

**It is a fourth envelope kind, never a derived clean verdict.** A derived
clean state would make an overridden review indistinguishable from one where
the reviewer found nothing, at the exact moment the difference matters, and
would turn an unverifiable claim about a human's decision into a merge. The
authorization is stamped with the emitting installation's `tool` identity by
the same rule as the request and the disposition — it is tool-emitted, and the
verdict's exemption is about having no emitter at all, so the stamped-kind set
is now three. The approval companion binds to one and posts an approval whose
first line says it is not a clean review, listing every finding still open.

**Every answer to a finding is bound to the ruling it answers.** The
standing set is derived from one table, `vocab.FINDING_ANSWERS`: each closure
term, each disposition and the waiver, with what it does to the finding it
answers (settles it, overrules it, or leaves it open), and one binding rule
for all of them — an answer recorded at round r answers the newest ruling of
its identity only if r is not earlier than that ruling. Before this, an
acceptance was bound to its round while a withdrawal and a waiver were bound
to the identity for all time, so a finding withdrawn or waived in round 1 and
re-raised in round 2 was treated as answered, and an advance could close over
a ruling nobody had answered — emitting the older finding's id, severity and
title from the stale waiver. The authorization now carries the CURRENT
ruling's facts, and a stale waiver leaves the re-raised finding unanswered
until a human answers it again.

**The authorization's closed grammar counts findings by identity.** Two
overruled records are one finding when they share a fingerprint, whatever
their round-scoped ids say, and two findings when they share a label across
rounds; `A-WAIVED-REPEAT` was keyed by the label and had both polarities
wrong, refusing an envelope the tool itself emits.

**An authorizer name is one grammar, refused before anything is recorded.**
`--by` is stamped into a quoted wrapper attribute with no escape, so a name
carrying a double quote, an angle bracket or a control character was
accepted, recorded, and then emitted as bytes the validator refused — an
advance reported as success with no usable artifact. Both `waive --finding`
and `authorize-advance` now refuse such a name by the same rule the validator
judges the stamp by, and `authorize-advance` validates the artifact it
emitted BEFORE keeping it or writing either ledger event, so no emitter drift
can commit an unusable terminal state. The emitted request's ledger report
now says it is a snapshot taken before the request it travels in is recorded.

**The limit is published rather than implied**: `by` is asserted. The tool
cannot observe who ran a command, so this buys attribution and visibility, not
proof that a human rather than an agent decided. The generated adapters now
tell every agent that `waive` and `authorize-advance` are never theirs to run.

**A forward-compatible version floor for config evolution — which does not
help any reader already deployed.** The narrow claim is deliberate: an earlier
draft of this entry said config evolution "no longer accuses a valid config",
and that was false for the case that prompted it. A repository may declare
`[tool] requires = "<version>"`, checked BEFORE schema validation — the
ordering is the feature, not an implementation detail — so a reader that knows
the key refuses with the required and installed versions instead of `unknown
key` and a remedy telling a person to repair a correct file.

What this does NOT do: a reader older than the `[tool]` section itself still
refuses with `unknown section [tool]` and still blames a valid config, because
the diagnosis it would need is in a release it does not have. The originating
defect — a published installation refusing a valid target it was handed by the
advertised relay — is therefore still open on that path, and this release
narrows the deliverable rather than claiming it. The floor pays only for skews
between this release and later ones, after a repository has adopted it, which
is an argument for adopting it early and not a reason to read it as a fix for
deployed readers. Unknown keys and sections still refuse exactly as before —
the closed grammar is why a misspelling cannot silently erase what it meant to
declare.

## 0.14.0

**A repository can declare that every round is a debug round.** The
debug stamp — the reviewer's standing invitation to critique the tool's
own performance in a `## tool feedback` verdict section — was flag-only,
so it reached a round only when whoever ran `handoff` remembered to type
it, and a default that depends on memory is not a default. It now
resolves like the transport, strongest declaration first: `--debug` or
the new `--no-debug` for one invocation (together they are a usage
refusal), then `[roles] debug = true` in the repository's config, then
off. The built-in default stays off on purpose: the stamp spends
reviewer attention, so it is asked for by declaration, never assumed.
This is the first boolean config key, and it found the shape checker's
bool branch accepting any value without a check — a wanted bool now
takes only a real bool.

**The overview's claims were reviewed against the tree, and eight were
repaired.** The tool-identity stamp is carried by the two tool-emitted
envelopes (requests and dispositions) — the verdict deliberately carries
none, and the overview no longer says "every envelope". The waiver
record for skipped development rounds lives in the author's per-machine
ledger and does not travel; the overview now says a public reader is
trusting that paragraph, not inspecting the events. The breaker
enforcement claim carries its one exception where it is made: the
round-count threshold advises while every other firing, the measured
token breach included, blocks the next handoff. "No vocabulary of its
own" became "no repository taxonomy of its own" — the protocol's enums
are the tool's; the judgment scale is yours. The network boundary is
stated as measured: no native network client, with Git subprocesses
performing the networked steps — `handoff` pushes and observes the
remote ref with `ls-remote`, `take` fetches — consistently across the
overview, the README and the specification. And the overview no longer
enumerates the breaker or verdict vocabularies at all — 0.13.0 claimed
it restated none while a bold table listed every breaker and a wrapped
code span held both verdict strings, unseen by a guard that read only
single-line backtick spans; the guard now normalizes whitespace, reads
bold table cells, and matches members as word-bounded substrings.

0.13.0's own rationale for its minor bump is corrected in place: it
claimed a shipped document joins the compared tool identity, which
teaches the identity boundary backwards.

## 0.13.0

Documentation only; no behaviour changes.

**The argument now has its own document.** `docs/design.md` was a single
1,145-line specification carrying two jobs: making the case for the tool to
someone deciding whether to adopt it, and specifying the mechanism for someone
implementing against it. Those readers want opposite things, and the first was
being served badly.

**`docs/overview.md`** (new) is the case: the problem, why writing a better
reviewer prompt does not fix it, the core idea, then each mechanism presented
as the EVIDENCE for trusting the tool rather than as an implementation detail
— fetchable SHAs, attested gate output, fingerprint identity, the append-only
ledger, the breakers, tool identity, convergence reporting. It ends with what
the design deliberately refuses to do and what those refusals cost, and with
what is designed but not built. Start there if you have not decided.

**`docs/design.md` is unchanged in this release.** A restructuring of the
specification was drafted alongside the overview and NOT shipped: the
specification is bound to the adapters by locators and derived counts that a
prose reorganisation breaks, and those bindings exist to stop the shipped
documents and the instruction blocks drifting apart. Reworking it is worth
doing on its own, against those guards, rather than riding along with a
document split.

Nothing in the overview restates a closed vocabulary. Where it would have
listed one — the five dispositions — it describes the shape and sends the
reader to the specification, so there is still exactly one place each
vocabulary is written down. That exclusion is measured rather than asserted:
the guard that keeps `docs/onboarding.md` honest about enumerating nothing now
walks the overview too, and it caught this document's disposition table on its
first run.

Every designed-but-not-implemented marker travels with the claim it qualifies:
risk tiering, path-scoped contract invariants, automatic execution of
falsification tests, the git-notes carrier and the MCP facade are all still
marked as not built.

The minor bump rather than a patch: a new shipped document is a new surface,
and every shipped document must be explicitly accounted for in the identity
boundary — this one as documentation, EXCLUDED from the compared behavioural
identity, whose set is the package modules and only the package modules. The
`tool` digest on the two tool-emitted envelopes (requests and dispositions;
verdicts are deliberately unstamped) moves with this release because the
version bump edits `TOOL_VERSION` in `review/__init__.py`, which is compared
— not because documentation bytes are hashed.

## 0.12.1

Documentation only; no behaviour changes and nothing addressed to upgraders.

**Onboarding covers provisioning.** The pass ended at "`loupe --version`
succeeds ... fix that first", which does not stick in an environment rebuilt
for every session — a cloud development container, a CI runner, an ephemeral
workspace. A repository could therefore carry `review.toml` with no way to run
the tool that reads it. The new section covers where provisioning belongs (the
repository's own tracked script), pinning the tool to an exact commit and
reporting drift rather than acting on it, isolation from the repository's own
environment, and staying non-fatal.

It also covers the interpreter, which is where this surprises people. A current
`uv` reads `requires-python` out of the pinned ref and provisions a matching
interpreter itself, so naming a version in a provisioning script is a constant
that goes wrong when the floor moves. Two things defeat that and both surface
as the same "no interpreter found" error — an older `uv`, and downloads
disabled by policy — so the section says read the error, which names the
download it would have made, before hardcoding anything.

**The gate manifest section gains identity.** Its environment test — rerun the
gate in a fresh worktree carrying no ignored files and no ambient credentials —
cannot catch a gate that passes as an unprivileged user and fails as root,
because a worktree does not change who you are. A gate asserting file
permissions is the common shape. This matters more than it sounds: there is no
bypass for a blocking gate, so an author whose identity reddens one cannot emit
at all.

**The medium a person uses is not a transport** (design §5.2). `paste` names a
topology — the bytes crossed, the two sides share no filesystem — not a window.
A person moving them through a chat surface, a pull-request comment or a ticket
has used the transport already provided, and the recorded stamp stays truthful;
what remains refused is the TOOL fetching or posting them. Two conditions are
stated for a forge-hosted medium: byte fidelity is the carrier's problem, and
storage is not a trigger.

## 0.12.0

One behaviour change, addressed to upgraders; the rest is test structure.

**The gate manifest runs concurrently.** `run_gates` executes the declared
gates in a four-worker thread pool instead of one at a time. Measured on
this project's own 13-gate manifest: 137.2s sequential to 83.5s, all gates
green in both, nothing skipped, cached or path-filtered. The loop is now
bounded by its slowest gate rather than by their sum.

*What this requires of your manifest, and it is a real requirement:* every
gate must be a CHECKER — read-only against the working tree, building only
into its own temporary directory. A gate that WRITES into the tree was
always questionable and is now unsound, because two of them can run at
once. If you have one, either make it read-only or set
`LOUPE_GATE_WORKERS=1`, which forces the previous sequential path with no
code change. An unparsable value falls back to the default rather than
raising.

*What does not change:* attestation order is the manifest's, never
completion order — the results are re-sequenced by index, so the emitted
Evidence block is byte-identical whatever order the gates finish in. That
is falsified by a test whose manifest completes in exactly reverse order.

*One field changes meaning:* `duration_s` is now wall time under
contention, not isolated cost. On this project's manifest the two slowest
gates read about 4% and 6% higher than when run alone. Do not read the
attested durations as per-gate cost.

**Test modules named by behaviour.** `test_transport.py` (5,308 lines, 27
classes) is now four modules — lifecycle, events, authority, integration —
plus a shared `_transport_fixtures.py`. No test was lost, gained or
renamed: the `Class.method` id set is identical. The collected count drops
19 because `TestTake` had been collected twice, in its own module and in
`test_transport_topology.py`, which imported the class to borrow two
helpers; unittest collects a TestCase in every module namespace that holds
it. The helpers are functions now, and nothing imports a TestCase across
modules.

## 0.11.3

Three behaviour changes — the claim boundary, retained-copy identity, and
the diff shape — addressed to upgraders; the rest is documentation.

**Retained copies compare as raw bytes; the claim digest is a named
semantic identity.** The physical-identity contract is now two-tier and
stated in the design doc: a carried envelope has one physical form (LF —
any CR refuses at every person-supplied reader, as since 0.11.0), and a
retained exchange copy is compared as raw bytes against the ledger's
recorded digest before any reuse — retention restores a rewritten copy
from the canonical emitted text; verdict resolution and the emission
cache treat a byte-distinct or undecodable copy as unavailable or cold.
Previously a kept file rewritten to CRLF still warm-cached and retrieved,
because the digest read it through newline translation. The claim digest,
by contrast, is deliberately a semantic-text identity — byte-distinct
physical forms of one claim digest equal — because a claim is re-rendered
into the emission, never carried as bytes.

**The diff shape reports the commit span it sweeps.** The envelope's
What-changed line (and both sides' précis) now ends "spanning N commits":
a round's diff runs from the previous round's target, and a first handoff
with a carelessly chosen base once swept thirteen commits of unmeant work
while the claim said "one commit". The count is machine-computed and
reported, never refused on — the base remains the author's judgment.

**A debug round asks the reviewer to critique the tool.** `handoff
--debug` stamps the request `debug="tool-feedback"`; the reviewer's
procedure then asks for a `## tool feedback` verdict section — a
self-critical account of how the tool could have served that round better,
under efficiency, cost and accuracy — which `close` records as a ledger
event so the critiques accumulate. The section is legal on any verdict and
gates nothing: advisory prose about the tool, by construction. The stamp
joins the claim, the roles and the transport in the emission cache's key,
and older readers tolerate it as they tolerate any unknown attribute.

**Ledgers are per machine, by design — now said on the contract's face.**
Metrics, breakers and the round cap are computed from what this machine
recorded; the envelopes that crossed are the only reconciliation. The
reviewer-side adapter also gains one conditional coda: where the standing
rules grant a clean-verdict approval and the operator's credentials for
the project's approval tooling are present locally, that grant covers
recording the approving review — the relay always comes first, and the
tool itself gains no approval machinery.

**A supplied claim must carry references.** The claim grammar's required
members are now `objective` **and** `references`, and an empty
`references` list is refused like a missing one. The onboarding page had
told every first round "objective and at least one references entry"
since it was written, while the validator required `objective` alone —
ruled intended policy rather than advisory prose: a supplied claim that
hands the reviewer nothing to read is a bare assertion, and the reference
manifest is the envelope's whole point. Supplying no claim at all remains
legal and remains its own recorded state. If your automation writes
minimal claims, add the references your reviewer was already owed.

**The human-facing documents lead with the story.** A thirty-second
two-terminal walkthrough — executable exactly as printed, from a fresh
repository, and proven so by a shipped test — and a round-lifecycle
diagram open the README; the design specification gains a contents line;
and onboarding §8 now starts from what the session actually loaded,
partitioned three ways: a repository instruction file is repaired in
place; a session that loaded only user-level or machine-global rules is
told to propose a minimal repository contract rather than route project
facts into a private global file; and a session that loaded nothing gets
the same proposal, carrying only the operating floor (generated files
are regenerated, a human starts every round, who approves by name,
commit discipline).

**The specification agrees with the doors.** Since 0.9.0 every reviewed
door — `handoff`, `take`, `validate --from-target` — refuses a target
that tracks no `review.toml`, yet three passages of `docs/design.md`
still described the pre-0.9.0 model: reviewed configuration as possibly
personal, user-level config as preserving a zero-footprint repository,
and a `take` that falls back to this checkout's rules. All three now
state the target-carried-authority rule; user-level config governs local
verbs only, and a shipped guard holds the documents to it.

## 0.11.2

One test-only change; no runtime behaviour moves.

**The identity's travelling universe is destination-filtered.** The private
workbench this tree is extracted from now publishes a second, unrelated
tool from the same source repository, and its extraction inventory gained a
destination axis: a file may travel to a repository that is not this one.
The workbench-only completeness suite (`test_identity_boundary`, skipped in
this tree) now counts only artefacts travelling HERE, so the other tool's
bytes are not demanded of this tool's identity. As with 0.11.1, registering
nothing and moving no behaviour, the bump exists so `--version`
discriminates the published trees.

## 0.11.1

Documentation and packaging of the public surface; no behaviour changes.

**Release notes move out of the README into this file.** Six sections,
0.11.0 back to 0.6.0 — more than half the README was changelog. Each
section's body is preserved byte-for-byte after its opening sentence; the
openings themselves ("Changed in 0.x…") became the version headings, with
the 0.9.0 and 0.8.0 openings reframed as explicit upgrade-note labels so
those two stay findable as what they are. The README is rewritten around
what remains: the problem, the loop, install, adapters, status,
verification.

**The tool identity changes for a version reason.** Registering the new
shipped document required one entry in the identity registry inside
`review/__init__.py`, which is itself an identity artifact — so a 0.11.0
envelope and an 0.11.1 envelope stamp different `tool` values even though
no behaviour moved, and the first cross-version round after upgrading will
read `differs` for a version reason, exactly as it did across
0.9.0 → 0.10.0. An older reader still accepts and validates the envelope —
the mismatch is reported, never refused — but it is not untouched: it
records that provenance, and it renders its side of the round with its own
older code.

## 0.11.0

Three fixes from the first onboarding of a repository
with environment-sensitive git hooks, and each moves behaviour.

**Every git subprocess now runs in the caller's environment.** 0.10.0's
gate-environment fix restored the caller's `PYTHONSAFEPATH`/`PYTHONPATH`
for gate subprocesses only; a repository's git hooks — pre-push, the
commit hooks, reference-transaction, a configured fsmonitor — are the
repository's own code in exactly the same trust position, and they still
inherited the shim's hardened interpreter environment. Measured: a
pre-push hook importing a sibling module failed with `ModuleNotFoundError`
under `handoff` and passed in a clean shell, refusing the emission. The
restoration is now applied at every git door, so hooks see what the
caller's shell would have given them. If a hook of yours somehow relied on
the leaked hardening, that reliance ends here.

**`render-adapters --check-install` and `--install` no longer read the
current repository.** Both modes ask about machine-global state (the
installed skills under your home directory), so their default source is
now the `adapters/` directory beside the installed package, not
`<cwd>/adapters` — the old default existed only in the tool's own checkout
and failed the check everywhere else, misreporting the healthy installed
copy as `unreadable`. Where no package-sibling directory exists (a wheel
install), the verb refuses with a remedy naming `--dir` instead of
proceeding into a misattributed failure; and a source-side failure now
reports the source path under its own statuses, `source_absent` /
`source_unreadable`. `render` and `--check` keep the repo-local default;
they generate and gate the tracked copies.

**The reviewer procedure gains a falsification-anchoring rule.** Anchor
the falsification test in the reviewed tree wherever the defect admits it;
where the defect genuinely lives in a mutable artifact outside the tree —
a PR body, an issue, a dashboard — the finding says so, so a later
`cannot_execute` reads as the anticipated outcome of a stated dependency
rather than an author evasion. Learned from a round where an external
artifact moved, the named test became unexecutable by construction, and
the honest disposition fired the `unverifiable` breaker.

## 0.10.0

Two things move that a reader should know about.

**The author's authority check now reads the commit instead of predicting
it.** It runs after the commit and before the push, and asks `git show
<sha>:review.toml` — the same call the reviewer's verbs make. Previously it
ran before the commit existed and had to anticipate what `git commit -a`
would record, which is unbounded: git offers arbitrarily many ways to change
what a commit records between a check and the commit, and five separate
review rounds each closed one of them. Consequences you may notice: a
declared `filter` attribute on `review.toml` no longer refuses, because
whatever the filter does the commit records something and that something is
read; a `skip-worktree` entry whose worktree copy is absent no longer
refuses, which was a false refusal — `commit -a` does not delete such an
entry; and if you have `assume-unchanged` set on `review.toml`, the emission
is now governed by the bytes in the commit rather than the bytes in your
worktree. The last of those is a behaviour change with no error message: it
is the case that used to emit under one set of rules and be judged under
another.

Related, and the reason the above matters beyond the check itself: what the
envelope DECLARES — taxonomy, gate ids, token budget, blocking severities,
round cap, roles, wrapper tag — now renders from the target commit's
configuration rather than from the emitting checkout's. These are identical
wherever the two agree, which is the ordinary case. They differ exactly
where the two ends would previously have disagreed silently.

**Every envelope now carries `shape` beside `tool`.** `tool` is the content
identity of the package modules; `shape` is the identity of the launchers,
`bin/loupe` and `pyproject.toml`. `shape` is reported and never compared.
The reason is measured: those two artefacts are not resolvable in a wheel
install, so including them in the compared identity made three advertised
install paths compute three identities for one codebase, and every
cross-machine round reported a disagreement that said nothing about what
either end would do. `tool` is now equal across all three install paths for
the same code. One transitional cost: an 0.9.0 envelope and an 0.10.0
envelope built from identical code stamp different `tool` values, so the
first cross-version round after upgrading will read `differs` for a version
reason. An older reader ignores the unknown `shape` attribute, by design.

**Rejecting a declared authority is now bounded, and narrowing a claim is a
legitimate answer.** The 0.7.0 terminator (below) bounded instance findings
against a declared-closed domain but left rejection of the authority itself
unbounded, and *"a narrow authority is itself the defect"* never said narrow
relative to what. In this tool's own review record that combination closed
two lineages by human decision rather than by a clean verdict: *"your
authority rests on a set one level down"* is always available and always
true, so each round derived one more authority and the next found the next
universe beneath it. One bound now applies, and it is about the CLAIM. The
author states the authority's DOMAIN beside it, in the terms the authority
enforces — and the reviewer rules on that domain BEFORE ruling under it:
does it match the anchor's purpose and everything still relied on
downstream? A concrete escape outside the declaration is evidence that the
domain is too narrow, not something the declaration excludes from being
heard. Only once the domain is accepted is coverage ruled against it, and
only then is a demand outside it scope rather than a finding, however well
it reproduces. So narrowing the claim until it matches what is enforced is
an available and complete answer to a coverage finding — but only when the
anchor's purpose and every consumer claim narrow with it; a narrowing that
leaves a wider promise standing is the defect. Nothing here demotes a finding
for failing to reproduce — a second bound of that shape was drafted and
removed before shipping, because it could not be stated once and excluded
nothing in the record; design §3.1 records why, since the same idea will
occur to anyone reading the rule. The cost is stated there too: an author
may declare a domain narrow enough to be trivially covered, and what holds
that is the narrowing being visible in the claim and rulable.

## 0.9.0

**Upgrade note: this release can refuse work that used to run.**
A reviewed commit now carries the rules it is judged by. `handoff`, `take`
and `validate --from-target` all refuse a target that tracks no `review.toml`
of its own — an authority living on one machine cannot be shown to a second,
and a review is the act of showing it. If your repository is governed only by
a user-level `~/.config/loupe/<repo-id>.toml`, copy it to `review.toml` at the
repo root and commit it; every local verb is unaffected either way.

Also changed: a gate id is one filename component, so gate output can no
longer be written outside the state directory; a `review.toml` entry is
judged by its git MODE rather than its object type, which is how a committed
symlink could make the two ends read different bytes for one SHA; the config
schema is derived from the defaults rather than restated beside them; and
every subprocess failure is typed, so a clone that cannot read its own
objects is no longer taken as evidence that a target carries no
configuration.

## 0.8.0

**Upgrade note, if you match relay bytes.** The verdict relay of a `path`
round now prints its command alone: the `# the author's to run` comment
above it restated the block's own `## What to run next` heading and is
gone. A `paste` round keeps its note, because there it carries an
instruction — the verdict bytes have to reach the command. Nothing about
what you run changes; if you match relay bytes exactly, match one line
fewer.

## 0.7.0

The review contract both agents
read gained a terminator for the boundary-closure rule: a domain whose
completeness cannot be established from inside the artifact may be
declared closed relative to a stated authority — a machine-generated,
separately gated artifact that derives the set, declared by the author in
the claim, per anchor. The reviewer then attacks the authority's
derivation and coverage, and an instance finding against a declared-closed
domain must name the authority it escapes; rejecting the authority is an
ordinary finding anchored on it. The tool identity's claim narrows to
match: `tool` identifies the declared travelling behavioural set per its
authority, never "what the installation is", and the readers report in
those terms. The three items 0.6.0 named as known and unfixed are closed
under exactly that contract: the workbench half of the reader scan derives
its module universe from the generated extraction inventory through an
explicit module predicate; the AST scanner is one factored function that
owns its call domain, probed by synthetic modules; and the generated
`adapters/` documents render every vocabulary enumeration from
`review/vocab.py` itself, which is what makes their exclusion from the
shipped-restatement search true rather than asserted.

## 0.6.0

Every envelope the tool emits
carries a `tool` attribute — the content identity of the declared
behavioural set of the installation that
wrote it — and `take`, `brief`, `validate` and `ledger add` report whether
the reading installation matches, differs, or read an envelope written
before the stamp existed. They report; they do not refuse. **The round cap
no longer refuses either**: it is advisory, and `loupe ledger convergence`
reports whether a lineage is closing its findings or returning to the same
domains. A token-budget breach is unchanged and still refuses, because it
is measured against a declared ceiling rather than standing in for one.

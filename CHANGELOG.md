# Changelog

The version is bumped inside the reviewed round of any change that will be
published, so `--version` discriminates publishes. Newest first. Sections
addressed to upgraders say so; read them before upgrading across the version
they name.

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

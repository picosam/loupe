# loupe

A deterministic, agent-neutral review tool: request → verdict → disposition
envelopes bound to commit SHAs, a gate manifest the tool runs itself,
fingerprinted findings, an append-only ledger with circuit breakers. No model
runs inside it; it decides everything derivable so the agents on either side
run one command each and stop.

Python 3.14.x, standard library only, no network in the tool's own code (git
performs the one publish step). Design: [docs/design.md](docs/design.md).

## What it does, in one loop

```
author    loupe handoff --claim-file claim.json [--base <sha>]
              → commits, pushes, observes the remote ref, runs the gate
                manifest, emits + validates the request, records it, keeps
                the bytes, prints the reviewer's command — then STOPS
reviewer  loupe take <request.md> --as <identity>
              → fetches the target, validates against the target's own
                config, checks the stamped identity against the one you
                declared, labels every reference from the target tree,
                records, prints the envelope and the exact diff command —
                rules, `loupe validate` the verdict, then STOPS
author    loupe close --verdict verdict.md
              → records; `changes requested` → respond; `clean to advance`
                → the lineage is closed at that SHA
author    loupe respond --verdict verdict.md --from-json d.json --out d.md
              → one disposition per finding, from a closed vocabulary; then
                make the changes and hand off again
```

Exit codes mean exactly one thing (0 ok, 1 findings or refusal, 2 usage), and
every non-zero exit prints the next command. Output is JSON when stdout is
not a TTY. The human sets each round in motion; the tool has no verb that
reaches the other side.

`--as` is the one flag the common path requires, and it is required on
purpose: `take` records who ruled, and the tool cannot observe who is at
the keyboard — it can only carry what is declared. The repository's
configured reviewer names the loop's default *direction*, not the actor, so
it is never used as an identity; silence appends nothing.

## Install

Three paths, all git-or-copy, no package index:

- `uvx --from git+<this repository> loupe …` — builds from `pyproject.toml`
  in an isolated environment; the console script `loupe` is `review.cli:main`
- vendor the `review/` directory and `bin/loupe` into a repository that
  wants it committed (`<repo>/bin/loupe` beside `<repo>/review/`)
- keep this tree together somewhere on disk — say `~/.local/lib/loupe/`,
  holding `bin/loupe` and `review/` — and symlink its shim onto your PATH:
  `ln -s ~/.local/lib/loupe/bin/loupe ~/.local/bin/loupe`

All three load the same `review` package; `pyproject.toml` serves the first
only, and the other two never read it. The shim resolves the package as
`../review` relative to its own real location, walking symlinks first, so
the last two paths need `bin/loupe` and `review/` to stay siblings under one
directory. Copying the shim *alone* into `~/.local/bin` does not work — it
would look for `~/.local/review` — and that layout is deliberately not
supported: the shim loads exactly one `review` package and never searches
for another.

Then, in a repository you want reviewed, copy `review.toml` from this
repository's root — it is the generic example — to that repository's root,
commit it, and edit every value: severities and classifications are *your*
vocabulary, gates are *your* commands, roles are *your* agents. Without a
declared taxonomy the tool refuses to emit and the reviewer refuses to
rule — a tool that supplies its own vocabulary has authored your judgment
scale. In-tree is required for review: a reviewed commit carries the rules
it is judged by, so `handoff` and `take` refuse a target that tracks no
`review.toml`. A user-level `~/.config/loupe/<repo-id>.toml` governs local
verbs only. The full pass — gates, taxonomy, roles, and the
approval question most onboardings skip — is
[docs/onboarding.md](docs/onboarding.md).

State lives outside the reviewed tree at `~/.local/state/loupe/<repo-id>/`
(ledger, gate output, kept envelopes); override with `--ledger-dir` or
`LOUPE_STATE_DIR`. `loupe prune` removes retained gate output for commits
no ledger event references — the ledger and the kept envelopes are the
record and are never pruned.

## Agent adapters

`adapters/` holds a Claude skill, a Codex skill and an agent-neutral
instruction block, all rendered from one source by `loupe render-adapters`
(`--check` is a drift gate). Install at user level:

- Claude Code: `~/.claude/skills/loupe/SKILL.md` (conventional path — verify
  on first install)
- Codex: `~/.codex/skills/loupe/SKILL.md`

An adapter names the tool and carries the literal command lines. Your
standing agent instructions need not name it at all: the process keeps
working with nothing installed.

## Layout

| Path | What |
|---|---|
| `bin/loupe` | entry point (`python3 -m review`) |
| `review/` | the tool: `wire` (envelopes), `validate`, `emit`, `transport` (handoff/take/close), `ledger`, `fingerprint`, `config`, `adapters`, `cli` |
| `review/tests/` | the suite — `python3 -m unittest discover -s review/tests -t .`; runs read-only (the tests that need a scratch directory self-skip, with the reason stated, where writes are denied) |
| `review.toml` | the generic example configuration, also this repository's own |
| `pyproject.toml` | packaging metadata for the `uvx` install path; version read from `review.TOOL_VERSION` |
| `adapters/` | generated per-agent adapters |
| `docs/design.md` | the specification |

## Status

Version 0.11.0 — the version is bumped inside the reviewed round of any
change that will be published, so `--version` discriminates publishes.
Implemented and tested: the envelopes and validators, the
gate manifest and attestation checks, roles and stamps (including
per-invocation selection within the permitted lists), fingerprints with
alias lineage, the ledger with lineage scoping, the breakers (`repetition`,
`stale`, `no-progress`, `unverifiable`, `budget`, `orphan`), honest
metrics, reachability, the transport verbs, retention with `prune`, dialect
migration, generated
adapters. Designed but not implemented: risk tiering, path-scoped contract
invariants, auto-execution of falsification tests, a git-notes carrier, an
MCP facade. See design §9.

Changed in 0.11.0. Three fixes from the first onboarding of a repository
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

Changed in 0.10.0. Two things move that a reader should know about.

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

Changed in 0.9.0, and this one can refuse work that used to run.
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

Changed in 0.8.0, if you are upgrading. The verdict relay of a `path`
round now prints its command alone: the `# the author's to run` comment
above it restated the block's own `## What to run next` heading and is
gone. A `paste` round keeps its note, because there it carries an
instruction — the verdict bytes have to reach the command. Nothing about
what you run changes; if you match relay bytes exactly, match one line
fewer.

Changed in 0.7.0. The review contract both agents
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

Changed in 0.6.0. Every envelope the tool emits
carries a `tool` attribute — the content identity of the declared
behavioural set of the installation that
wrote it — and `take`, `brief`, `validate` and `ledger add` report whether
the reading installation matches, differs, or read an envelope written
before the stamp existed. They report; they do not refuse. **The round cap
no longer refuses either**: it is advisory, and `loupe ledger convergence`
reports whether a lineage is closing its findings or returning to the same
domains. A token-budget breach is unchanged and still refuses, because it
is measured against a declared ceiling rather than standing in for one.

The tool was developed and falsified against a private review corpus that
stays with its author; the public suite runs on synthetic fixtures produced
by the real emitter.

## Verifying a checkout

```
python3 -m unittest discover -s review/tests -t .
bin/loupe render-adapters --check
bin/loupe --version
```

## Where this tree comes from

This repository is **published, not developed**: it is generated output,
extracted from the author's private workbench by a deterministic script that
maps a declared boundary onto this tree. Every byte here has a source there,
and the next extraction rewrites this tree from that source — so an edit made
here does not survive, and the repository takes no pull requests. Report
issues instead; the fix lands in the source and arrives with the next
extraction.

For the same reason there is no CI here. The workbench runs its gate manifest
on every push — tests, whitespace, adapter drift, the extraction audit, and a
gate that extracts this very tree into disposable storage and runs its suite
standing alone — so the checks that guard this tree run where the changes are
made. The verification commands above are what that last gate runs; run them
on any checkout to see the same result.

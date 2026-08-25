# loupe

A deterministic, agent-neutral review tool: request → verdict → disposition
envelopes bound to commit SHAs, a gate manifest the tool runs itself,
fingerprinted findings, an append-only ledger with circuit breakers. No model
runs inside it; it decides everything derivable so the agents on either side
run one command each and stop.

Python 3.11+, standard library only, no network in the tool's own code (git
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
repository's root — it is the generic example — to that repository's root
(or to `~/.config/loupe/<repo-id>.toml` to leave the tree untouched), and
edit every value: severities and classifications are *your* vocabulary, gates
are *your* commands, roles are *your* agents. Without a declared taxonomy the
tool refuses to emit and the reviewer refuses to rule — a tool that supplies
its own vocabulary has authored your judgment scale.

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

Version 0.6.0 — the version is bumped inside the reviewed round of any
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

Changed in 0.6.0, if you are upgrading. Every envelope the tool emits now
carries a `tool` attribute — the content identity of the installation that
wrote it — and `take`, `brief`, `validate` and `ledger add` report whether
the reading installation matches, differs, or read an envelope written
before the stamp existed. They report; they do not refuse. **The round cap
no longer refuses either**: it is advisory, and `loupe ledger convergence`
reports whether a lineage is closing its findings or returning to the same
domains. A token-budget breach is unchanged and still refuses, because it
is measured against a declared ceiling rather than standing in for one.

Known and unfixed at 0.6.0, from the review that produced it: the
workbench-only half of the reader scan derives its module universe from a
literal list rather than from the generated inventory; the AST scanner's
import-alias handling is asserted by a parallel implementation in its test
rather than by the scanner itself; and the generated `adapters/` documents
are excluded from the shipped-restatement check although they do enumerate
runtime vocabularies. None affects the envelopes, the ledger or the gates.
All three are instances of one open design question — how a completeness
domain is declared closed relative to a stated authority — and are the
subject of the next review lineage rather than pending patches.

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

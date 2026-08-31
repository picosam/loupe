# loupe

One agent writes the change, another reviews it, and a human starts every
round. loupe is the fixed ground between them — a deterministic,
agent-neutral review tool: request → verdict → disposition
envelopes bound to commit SHAs, a gate manifest the tool runs itself,
fingerprinted findings, an append-only ledger with circuit breakers. No model runs inside
it; it decides everything derivable so the agents on either side run one
command per hand-off and stop where the procedure says stop.

Built for AI coding agents reviewing each other's work — Claude Code, Codex,
or any agent with a shell — with a human setting each round in motion.
Python 3.14.x, standard library only, no network in the tool's own code (git
performs the one publish step). The specification is
[docs/design.md](docs/design.md).

## Thirty seconds, two terminals

The author's terminal — your agent, or you:

```console
$ cat > ../claim.json <<'EOF'
{"objective": "close the parser gap",
 "references": [{"path": "src/parser.py", "required": true}]}
EOF
$ loupe handoff --claim-file ../claim.json --base $(git rev-parse HEAD~1)
```

`handoff` commits outstanding tracked work, pushes the branch, runs *your*
gates from `review.toml`, emits and validates the request, records it, and
prints the one command the reviewer runs. Then it stops — carrying the
envelope across is the human's move. Two details are load-bearing:
`--base` names where round 1 of a lineage starts (here, the commit before
the change) — a later round derives its base from the recorded verdict,
and only then may the flag be omitted; and the claim lives outside the
tree (or in your ignore file), because it is your working note and
`handoff` refuses untracked files rather than guessing whether they were
meant to ship.

The reviewer's terminal — a different agent, possibly a different machine:

```console
$ loupe take round-1-request.md --as codex
$ loupe validate verdict.md --from-target
```

`take` fetches the target, resolves base and head in this clone, checks
every claimed reference against the reviewed tree, and prints the envelope
with the exact diff command. The reviewer rules by the taxonomy in the
reviewed repository's own `review.toml`, writes findings each carrying a
falsification test, and `validate --from-target` judges the verdict
against the reviewed commit's own config. Exit 0: hand it back, stop.

The author's terminal again:

```console
$ loupe close --verdict verdict.md
```

`clean to advance` closes the lineage at that exact SHA. `changes
requested` keeps it open: `loupe respond` records exactly one disposition
per finding, you fix, you hand off again. Nobody restated a finding, chose
a severity scale, or decided when the round ends — the envelopes carry all
of it, and a human started every step.

## Why it exists

Two agents reviewing each other's work over prose alone have no fixed
ground: the review binds to whatever the reviewer's checkout happens to
hold, the severity scale is whatever the reviewing model supplies, findings
drift when restated by hand between rounds, and each side can talk the other
into one more round. loupe replaces that with envelopes a validator accepts
or refuses: the request names a commit the reviewer fetches; the judgment
vocabulary comes from the reviewed repository's own committed `review.toml`;
findings carry fingerprints with alias lineage so they cannot quietly
mutate; circuit breakers report rounds that stop converging; and the tool
has no verb that reaches the other side — the human starts every round.

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

The same loop as a picture — the tool's verbs act, the human carries:

```mermaid
sequenceDiagram
    actor H as human
    participant A as author agent
    participant R as reviewer agent
    H->>A: start the round
    A->>A: handoff — commit, push, gates, emit + record
    A-->>H: request + relay, then stop
    H->>R: carry the request
    R->>R: take — fetch, verify references, print the diff command
    R->>R: rule, then validate --from-target
    R-->>H: verdict, then stop
    H->>A: carry the verdict
    A->>A: close — record the verdict
    alt changes requested
        A->>A: respond — one disposition per finding, fix, hand off again
    else clean to advance
        Note over A: lineage closed at the reviewed SHA
    end
    Note over A,R: every event lands in the append-only ledger; a fired breaker blocks the next handoff until a human records a decision
```

Exit codes mean exactly one thing (0 ok, 1 findings or refusal, 2 usage),
and every non-zero exit declares its recovery in `next_kind`: `command`
carries, in `next`, a literal command to run verbatim; `blocked` carries
`next: null` and a `remedy` — a state no command repairs, where a person
acts. Output is JSON when stdout is not a TTY. `--as` is the one flag the common path requires, on purpose:
`take` records who ruled, and the tool cannot observe who is at the
keyboard — it can only carry what is declared.

## Install

Three paths, all git-or-copy, no package index:

- `uvx --from git+<this repository> loupe …` — builds from `pyproject.toml`
  in an isolated environment; the console script `loupe` is `review.cli:main`
- vendor `review/` and `bin/loupe` into a repository that wants the tool
  committed (`<repo>/bin/loupe` beside `<repo>/review/`)
- keep this tree together somewhere on disk — say `~/.local/lib/loupe/`,
  holding `bin/loupe` and `review/` — and symlink its shim onto your PATH:
  `ln -s ~/.local/lib/loupe/bin/loupe ~/.local/bin/loupe`

All three load the same `review` package. The shim resolves it as `../review`
relative to its own real location, walking symlinks first — so `bin/loupe`
and `review/` must stay siblings under one directory, and copying the shim
*alone* into `~/.local/bin` deliberately does not work.

Then, in a repository you want reviewed, copy `review.toml` from this
repository's root — it is the generic example, and also this repository's
own configuration — to that repository's root, commit it, and edit every
value: severities and classifications are *your* vocabulary, gates are
*your* commands, roles are *your* agents. Without a declared taxonomy the
tool refuses to emit and the reviewer refuses to rule — a tool that supplies
its own vocabulary has authored your judgment scale. A reviewed commit
carries the rules it is judged by, so `handoff` and `take` refuse a target
that tracks no `review.toml`; a user-level `~/.config/loupe/<repo-id>.toml`
governs local verbs only. The full pass — gates, taxonomy, roles, and the
approval question most onboardings skip — is
[docs/onboarding.md](docs/onboarding.md).

State lives outside the reviewed tree at `~/.local/state/loupe/<repo-id>/`
(ledger, gate output, kept envelopes); override with `--ledger-dir` or
`LOUPE_STATE_DIR`. `loupe prune` removes retained gate output for commits no
ledger event references — the ledger and the kept envelopes are the record
and are never pruned.

## Agent adapters

`adapters/` holds a Claude skill, a Codex skill and an agent-neutral
instruction block, all rendered from one source by `loupe render-adapters`
(`--check` is a drift gate). Install at user level:
`~/.claude/skills/loupe/SKILL.md` (Claude Code — conventional path, verify
on first install) and `~/.codex/skills/loupe/SKILL.md` (Codex). An adapter
names the tool and carries the literal command lines; your standing agent
instructions need not name it at all, and the process keeps working with
nothing installed.

## Status

Version 0.12.1 — the version is bumped inside the reviewed round of any
change that will be published, so `--version` discriminates publishes.
Implemented and tested: the envelopes and validators, the gate manifest and
attestation checks, roles and stamps, fingerprints with alias lineage, the
ledger with lineage scoping, the breakers (`repetition`, `stale`,
`no-progress`, `unverifiable`, `budget`, `orphan`), honest metrics,
reachability, the transport verbs, retention with `prune`, dialect
migration, generated adapters. Designed but not implemented: risk tiering,
path-scoped contract invariants, auto-execution of falsification tests, a
git-notes carrier, an MCP facade. See design §9.

Recent changes, including the upgrade notes for 0.9.0 and 0.8.0, are in
[CHANGELOG.md](CHANGELOG.md). The tool was developed and falsified against
a private review corpus that stays with its author; the public suite runs
on synthetic fixtures produced by the real emitter.

## Verifying a checkout

```bash
python3 -m unittest discover -s review/tests -t .
bin/loupe render-adapters --check
bin/loupe --version
```

The suite runs read-only: the tests that need a scratch directory self-skip,
with the reason stated, where writes are denied.

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

## License

MIT — see [LICENSE](LICENSE).

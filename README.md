# loupe

One agent writes the change, another reviews it, and a human relays every
leg between them. loupe is the fixed ground between them — a deterministic,
agent-neutral review tool: request → verdict → disposition → authorization
envelopes bound to commit SHAs, a gate manifest the tool runs itself,
fingerprinted findings, an append-only ledger with circuit breakers. No model runs inside
it; it decides everything derivable so the agents on either side run one
command per hand-off and stop where the procedure says stop.

Built for AI coding agents reviewing each other's work — Claude Code, Codex,
or any agent with a shell — the author agent runs the review step unasked
once its work is done, and a human carries each leg between them.
Python 3.15.x, standard library only, no native network client in the tool's
own code (Git subprocesses perform the networked steps: `handoff` pushes and
observes the remote ref, `take` fetches). The argument for the design — what it solves, and what the rigidity costs —
is [docs/overview.md](docs/overview.md); the specification is
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
of it, and a human carried every leg.

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
has no verb that reaches the other side — an author agent emits
unasked and a human carries every leg.

## What it does, in one loop

```
author    loupe handoff --claim-file claim.json [--base <sha>]
              → commits, runs the local gates (a red blocking one stops
                it before the push), pushes, observes the remote ref,
                awaits any CI-attested gates, emits + validates the
                request, records it, keeps the bytes, prints the
                reviewer's command — then STOPS
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
    A->>A: work is done — handoff runs unasked, commit, local gates, push, CI gates, emit + record
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

`loupe decide` is the read-only companion to that loop: it prints every
optional key your `review.toml` never declared, with the value the tool
applied and the exact line that sets or unsets it, and it writes nothing —
run it after moving a version pin, or whenever you want to see what this
reader is deciding on your behalf.

## Install

**Requirements**, as they are, not as a wishlist: Python 3.15.x, pinned
(`pyproject.toml` declares `requires-python = ">=3.15,<3.16"`); the
standard library only, nothing else to resolve; and no network call in
the tool's own code at any point (Git subprocesses do the two networked
steps — `handoff` pushes and observes, `take` fetches). And one
non-negotiable across every path below: **`loupe` has to resolve on
PATH.** Every command in this README, and every agent skill in the next
section, shells out to the bare name `loupe` — a skill that names the
tool but can't find it on PATH doesn't fail at install time, it fails
silently later, on its first real command.

Two install paths, both from wherever you cloned or point at this
repository (`<repo>` below — a local path or the repository's own git
URL, either works identically), both run start to finish rather than
just described before being written down here:

**Python packaging**, no clone needed — either run it through `uvx`:

```console
$ uvx --from git+<repo> loupe --version
```

or put it on PATH directly with `pip`:

```console
$ pip install git+<repo>
$ loupe --version
```

Both build the same wheel from `pyproject.toml` — the console script
`loupe` is `review.cli:main` — in whatever environment is active; `uvx`
re-resolves an isolated one on every invocation, `pip install` puts it
wherever you ran `pip`. Neither packages an `adapters/` directory, and
neither needs one: the agent adapters are rendered by the installed
package itself (next section).

**A clone**, when you want the tree itself: keep it together on disk and
symlink its shim onto your PATH for a standalone copy with everything,
including `adapters/`, or commit a vendored copy into a repository that
wants `loupe` on the tree it reviews:

```console
$ git clone <repo> loupe
$ ln -s "$PWD/loupe/bin/loupe" ~/.local/bin/loupe
$ loupe --version
```

The shim resolves the `review` package as `../review` relative to its own
real location, walking symlinks first — so `bin/loupe` and `review/` must
stay siblings under one directory, and copying the shim *alone* onto PATH
deliberately does not work. To vendor instead of symlinking, commit
`review/` and `bin/loupe` into the target repository as `<repo>/bin/loupe`
beside `<repo>/review/`.

Then, in the repository you actually want reviewed, copy `review.toml`
from this repository's root — it is the generic example, and also this
repository's own configuration. From a clone:

```console
$ cp loupe/review.toml .
```

or, with no clone of this repository on the machine at all, from a
throwaway shallow copy that nothing afterwards depends on:

```console
$ tmp="$(mktemp -d)" && git clone --quiet --depth 1 <repo> "$tmp" && cp "$tmp/review.toml" . && rm -rf "$tmp"
```

and commit it:

```console
$ git add review.toml && git commit -m 'review: onboard loupe'
```

Edit every value before your first real round: severities and
classifications are *your* vocabulary, gates are *your* commands, roles
are *your* agents. Without a declared taxonomy the tool refuses to emit
and the reviewer refuses to rule — a tool that supplied its own
vocabulary would have authored your judgment scale. A reviewed commit
carries the rules it is judged by, so `handoff` and `take` both refuse a
target that tracks no `review.toml`; a user-level
`~/.config/loupe/<repo-id>.toml` governs local verbs only, never a
reviewed door. The full pass — gates, taxonomy, roles, and the approval
question most onboardings skip — is
[docs/onboarding.md](docs/onboarding.md); once that's done, your first
round is the [thirty-second walkthrough](#thirty-seconds-two-terminals)
above, starting with `loupe handoff`.

State lives outside the reviewed tree at `~/.local/state/loupe/<repo-id>/`
(ledger, gate output, kept envelopes); override with `--ledger-dir` or
`LOUPE_STATE_DIR`. `loupe prune` removes retained gate output for commits no
ledger event references — the ledger and the kept envelopes are the record
and are never pruned.

### Upgrade and uninstall

Upgrading is a **procedure, not a verb**: nothing here fetches its own
successor, because the tool opens no network connection in its own code.
Moving a repository from one pinned version to another is
[docs/upgrading.md](docs/upgrading.md) — seven steps, each with the
command that performs it and the check that proves it, covering both
install forms, the version floor in `review.toml`, and the adapters,
which regenerate from whatever is *installed* rather than from the pin.
`uvx` re-resolves its source on every invocation, so it tracks whatever
the pointed-at ref holds; `pip install`, a clone, or a vendored copy all
stay exactly where you put them until you reinstall or `git pull` by
hand. After any update, whatever the install form, re-run `loupe
render-adapters --install` (below) yourself — nothing does it for you,
and an installed skill does not know its source moved.

Uninstalling is plain file removal, because nothing here registers itself
anywhere: delete `~/.claude/skills/loupe/` and `~/.agents/skills/loupe/`
for the agent skills (and `~/.codex/skills/loupe/`, where versions before
0.25.0 put the Codex skill), take `loupe` off PATH (drop the symlink, `pip
uninstall loupe`, or let an unused `uvx` cache expire on its own), and
remove a vendored `review/` + `bin/loupe` from a repository like any other
tracked files.

## Agent adapters

`adapters/` holds a Claude skill, a Codex skill and an agent-neutral
instruction block, all rendered from one source by `loupe render-adapters`
(`--check` is the drift gate that keeps the three from disagreeing).
Deploy the two agent-specific skills to where each agent actually reads
them from:

```console
$ loupe render-adapters --install
```

This writes `~/.claude/skills/loupe/SKILL.md` (Claude Code) and
`~/.agents/skills/loupe/SKILL.md` (Codex), and never clobbers silently: if a
target already holds different bytes — your own hand edit, or an older
render — those bytes are written out and proven recoverable *before* the
target is overwritten, and the install reports where they went; if they
can't be kept, it refuses instead of overwriting blind. Check either
target later for drift with:

```console
$ loupe render-adapters --check-install
```

**Both render from the installed package itself** (from 0.25.0): no
`adapters/` directory, clone or checkout has to exist beside the install,
so a `uvx`/`pip`/`uv tool` install and a clone behave identically, and
every row names its source as `rendered by loupe <version> at <package
path>`. `--dir <path>` reads rendered files from a directory instead,
when that is what you want. (Through 0.24.x both commands read an
`adapters/` sibling of the package and refused on a wheel install.)

**Where the Codex skill goes — two claims, kept apart.** *Documented:*
OpenAI's skills documentation
(<https://learn.chatgpt.com/docs/build-skills>, retrieved 2026-09-21)
names `$HOME/.agents/skills` as the user-scope skill directory; that is
the path `--install` writes, and `CODEX_HOME` does not move it.
*Observed*, separately, on 2026-09-21 with codex-cli 0.155.0-alpha.9.2
(`codex debug prompt-input`, which makes no model call): Codex lists
skills from both `$CODEX_HOME/skills` and `$HOME/.agents/skills`, and
lists a name held in both twice. Versions before 0.25.0 installed the
skill to `~/.codex/skills/loupe/`, so `--install` moves such a legacy
copy — and one under `$CODEX_HOME/skills/loupe/` — out of discovery,
keeping its bytes first exactly as it keeps a replaced skill's. It
touches only loupe's own directory, and refuses, writing nothing, when
that directory holds anything besides `SKILL.md`; `--check-install`
reports a legacy copy still present as drift.

**An instruction block embedded in a tracked file** — the `<!-- BEGIN
GENERATED: loupe adapter` region of an `AGENTS.md` or `CLAUDE.md` — is
checked against the current rendering with `loupe render-adapters
--check-embedded <file>` and repaired, that region's bytes and no others,
with `--write-embedded <file>`; [docs/upgrading.md](docs/upgrading.md)
step 4 says what each refusal means.

**Gemini and Antigravity get no generated skill here, by decision, not
by any limit on what they can run.** `render-adapters` renders exactly
three artifacts — the Claude skill, the Codex skill, and the
agent-neutral `instructions-block.md` — and no fourth; any other agent
with a shell can still run every command in this README, and
`instructions-block.md` exists specifically to be pasted into that
agent's own standing instructions. An adapter names the tool and carries
the literal command lines; your standing agent instructions need not
name it at all, and the process keeps working with nothing installed.

## Status

Version 0.26.0 — the version is bumped inside the reviewed round of any
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

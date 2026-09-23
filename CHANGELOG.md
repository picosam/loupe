# Changelog

The version is bumped inside the reviewed round of any change that will be
published, so `--version` discriminates publishes. Newest first. Sections
addressed to upgraders say so; read them before upgrading across the version
they name.

## 0.26.0

**For upgraders — read before crossing this version.** Two things move.

**Python 3.15 is the one supported interpreter.** `requires-python` is
now `>=3.15,<3.16`, replacing `>=3.14,<3.15`. The package still
supports exactly one minor, because its scope scanner is closed against
the `ast` grammar of the interpreter that gates it. Under 3.14 the
package refuses at its first statement and names both versions.

**This release is published on the 3.15.0 release candidate, as if
final.** Until 3.15.0 final ships, run `uv python install 3.15` BEFORE
the pinned install. A `uvx`/`uv tool` install provisions 3.15 from the
declared range only once a 3.15.0 final exists that your `uv` knows
about: PEP 440 orders `3.15.0rc2` below `3.15`, so `>=3.15` excludes the
rc from a download, and uv finds no release candidate for it. Measured
with uv 0.12.18 and an empty interpreter store, it answered "No
interpreter found for Python >=3.15, <3.16"; after `uv python install
3.15` (which installs 3.15.0rc2 from a plain `3.15` request) the same
`uvx --from` install ran and reported this version. An rc that is
already installed IS used. The copy-based launchers (`bin/loupe`,
`loupe-approve`) now try `python3.15` before `python3`, so a machine
whose bare `python3` is another minor still runs the tool when a 3.15
exists.

Keep `review.toml` in TOML 1.0 while any machine that authors or reviews
the repository runs a release below 0.26.0. Python 3.15's `tomllib`
reads TOML 1.1, and 3.14's reads 1.0, so such a reader refuses 1.1-only
syntax as invalid TOML and names the line and column. Rewriting that
line in its 1.0 spelling is the whole repair.

**An objective gains two optional fields, `authority` and `covers`**
(below). An author on 0.25.x refuses a claim whose `objectives` entry
carries either, as an unknown field: measured with the published 0.25.0,
"objectives[0] states unknown field 'authority'". A reviewer on 0.25.0
still takes the request.

What does NOT move: no key, no claim member (the two fields sit inside the
existing `objectives` member), no wrapper attribute, no request section,
no verdict grammar. A 0.25.0 reader takes a 0.26.0 request. Measured with
the published 0.25.0 on a request carrying an `Authority —` block and its
notice, a `Scoped:` line and the new `Env:` wording: `take`, `validate`
and `brief` exit 0, `validate` reports no items, and the tool agreement
reads `differs`, never a refusal. As at every version bump, the `tool`
digest differs.

**Measured before the move, on 3.15.0rc2** (2026-09-22, a scratch clone,
the same clone under 3.14.7 as the control):

| suite | tests | 3.15-only non-passes | cause |
|---|---|---|---|
| `review/tests` | 2,079 | 4 | the declared 3.14 interval |
| `tools/tests` | 979 | 0 | — |
| the candidate's suite | 1,733 | 4 | the same four |
| park and approve candidates | | 0 | — |

None of those failures came from the tool's behaviour. The grammar gate
passes unchanged. PEP 810's `lazy import` adds one field, `is_lazy`, to
`Import` and `ImportFrom`. Both are classified transparent, which is
correct: a lazy import defers the imported module's code, not an
evaluation in the scope being scanned. PEP 798 admits a starred element
in a comprehension and a `DictComp` without a value, and both nodes are
already transparent.

**The request's `Env:` line no longer guesses the reviewer's reader
(issue #8).** When the `loupe` on the hand-off's PATH was not the running
installation, the line went on to say that a same-machine reviewer runs
that one. That is a claim about another process's PATH, and it is false
wherever a harness puts its own reader first on the reviewer's PATH. The
line now states what the hand-off observed and names where the other half
is observed: `` `loupe` on PATH is 0.25.0, NOT this 0.26.0 (this process's
PATH; the reviewer's `take` reports the reader it ran) ``. No reader parses
the line, so an older reader shows the new text as it is.

**`take` shows the target's open decisions at a terminal (issue #9).**
`take` reports the keys the TARGET commit's configuration never declared
(`decide`), but it did so only in its JSON result, so a person reading the
terminal never saw them. The default and the `--compact`
renderings now print a `decide:` block after the `head:` line: the count,
the configuration it was read from, and for each key the lines `loupe
decide` prints (meaning, applied value, `set` and `unset`). A target that
declares every optional key prints `decide: none` rather than nothing. The
JSON result is unchanged.

**`take` gives the reviewer a scoped diff for their own checkout (issue
#10).** A request whose claim declares `scope_paths` carries a `Scoped:`
line: the span's diff limited to the matched paths, as a command for the
AUTHOR's checkout (`git -C <author root> …`). `take` re-rendered `Diff:`
for the reviewer's checkout and gave no scoped counterpart. Its result now
carries `scoped`, the request's own pathspecs over the request's own range,
rooted where `diff` is rooted and run like it, and both renderings print a
`scoped:` line under `diff:`.

- The pathspecs are read from the request, not recomputed, because which
  paths the claim's `scope_paths` match is the emitter's decision and the
  reviewer's command must select the same set.
- They are re-rendered only from a line in the form the emitter writes:
  the range of the request's own `Base:` and target, then literal
  pathspecs only. Anything else gives `scoped: null` and a `scoped_note`
  saying why: no line, the emitter's own `none` or `withheld`, a second
  `Scoped:` line, another range, a pattern pathspec, or quoting that does
  not parse.
- Result shape: `take` and `take --compact` gain the keys `scoped` and
  `scoped_note`. A request from a 0.25.0 author carries the same `Scoped:`
  form and gets a reviewer-local command too (measured: the published 0.25.0
  emitting, this version taking).

**A changed `--base` at an unchanged tip is no longer dropped (issue #7).**
Re-running `handoff` on an unchanged tip serves the kept request without
re-running gates or pushing. The review base was not part of that
decision, so a second `handoff --base Y` returned `cached: true` with
`Base:` still naming the first base. The base is now part of the key,
chosen over refusing a changed base: a refusal at an unchanged tip would
leave no repair but a new commit, while a corrected base is an authored
input like a corrected claim, which already re-emits the round.

- The base compared is the one a fresh emission from the same arguments
  would use: `--base`, else the commit the last verdict ruled on. It is
  resolved as the fresh emission resolves it and must equal the kept
  request's `Base:`. So `--base <id>`, an abbreviation of it and `HEAD~2`
  naming the same commit are all served from the cache, and on round 2 a
  re-run without `--base` still is.
- Anything else re-emits the round, and the emission's own checks then
  apply. On round 1, re-running without `--base` after a hand-off that gave
  one now refuses with "no prior verdict in the ledger; pass --base",
  where it used to return the kept request: repeat the `--base`. A symbolic
  base is read when the re-run starts, as a fresh emission reads it, so a
  ref that moved since the first hand-off names the commit it names now.

**One authority for whether a finding blocks (issue #6).** 0.25.0 judged
the verdict précis by the TARGET commit's `review.toml`, as `close` and
`validate --from-target` already did. Three other readers of the same
question still used the CHECKOUT's configuration:

- the `blocking` stamp `respond --out` and `ledger add` write on a
  falsification run;
- the `unverifiable` breaker's judgment of a run that carries no stamp,
  in `ledger report`, the hand-off's preflight and the request's own
  ledger report;
- the rule that a blocking finding cannot be `deferred` or answered with
  `preference`.

So in a round where the checkout's blocking list differed from the
reviewed commit's, for example with a taxonomy edit in flight, a finding
the précis called blocking could be deferred, or have its unexecutable test
stamped non-blocking, and the reverse. Measured before the change: every
row where the two lists differ answered by the checkout. All three now
read the configuration of the commit the finding's verdict ruled on.

- A response to a RECORDED verdict is validated, rendered and stamped
  under that verdict's target, resolved once. As at `close`, a target this
  clone cannot resolve refuses and names the fetch. A verdict not yet
  recorded can only be previewed (`--out` refuses it), and is judged under
  the checkout as before.
- An unstamped run is judged against the blocking list of the commit its
  finding's round ruled on, and one whose target cannot be resolved is
  unjudgeable and does not fire, as before.
- Nothing crosses installations: the stamp is the field it always was.

**An objective can name the inventory that decides whether it is complete
(brief `take-objective-map` item 5).** The recorded case: a request
claimed three disposition ingresses complete while the tool's own
inventory listed a fourth, and only the reviewer noticed. An `objectives`
entry may now carry `authority`, the path of a committed inventory, and
`covers`, the members the objective claims. The two are declared together
or not at all, and `authority` takes the reference-path grammar.

- The request reads the inventory AT THE TARGET commit, through the
  readers the reference manifest already uses (a symlink or a submodule is
  refused), never the working tree. The format is UTF-8 text, one member
  per line, blank lines and `#` lines skipped, at most 1 MiB.
- The two sets are compared both ways, as exact strings. Under the
  objectives table an `Authority —` block names each inventory with its
  digest and member count, then what it lists that the objective does not
  cover and what the objective covers that it does not list.
- Each discrepancy is also a `Notice —` line, which the précis carries, and
  so is an inventory that cannot be read at the target: not tracked, not a
  regular file, not UTF-8, or over the bound. It reports and never refuses:
  whether the inventory is right is the reviewer's call.
- The smallest version, named as such: the inventory is a plain file the
  repository's own tooling writes. Selecting members out of a structured
  inventory, such as a JSON document, is not built. `take` does not
  recompute the comparison, so a hand-edited request could misstate it,
  as it could the objectives table.

## 0.25.0

**For upgraders — read before crossing this version.** Three things move.

1. **Re-run `loupe render-adapters --install` on every machine** whose agents
   load the skills. The Codex skill moves to the path OpenAI documents,
   `~/.agents/skills/loupe/SKILL.md`. The install keeps any legacy
   `~/.codex/skills/loupe/` copy, then moves it out of Codex's discovery.
   Until it runs, a machine holding both lists the skill to Codex twice.
2. **`[limits] git_timeout` is a new key.** Declare it only after every
   machine that authors or reviews the repository runs 0.25.0, and raise
   `[tool] requires` to `0.25.0` in the same commit: an older installation
   refuses a configuration that declares it.
3. **The claim gains five members.** An author on 0.24.x refuses a claim
   file carrying any of them as an unknown member. A reviewer on 0.24.x
   still takes the request (measured below).

What does NOT move: no wrapper attribute, no required request section, no
attestation field, no verdict grammar. A 0.24.1 reader validates, briefs and
takes a 0.25.0 request carrying every new claim member, measured with the
published 0.24.1: `validate` reports no items, `brief` and `take` succeed.
Two cases break that. If the target commit's `review.toml` declares
`git_timeout`, the 0.24.x reader refuses the unknown key. If it raises
`[tool] requires` to 0.25.0, that floor refuses the older reader by name.
As at every version bump, the `tool` digest on a request's wrapper
differs from a 0.24.x reader's own, and the reader reports that as
`differs`, never as a refusal.

**A configurable git ceiling, and a typed refusal on any git timeout.**
Every git door hardcoded 120 seconds. `commit -a` and `push` run the
repository's own hooks, and git runs `pre-push` even when there is nothing
to send. One adopting repository's `pre-push` gate took 133 s, so every
push the tool made there died of a raw `subprocess.TimeoutExpired`: a
traceback at `respond --out`'s envelope push, a generic failure elsewhere.

- `[limits] git_timeout` takes whole seconds, at least 1, default 120.
  That is the old number, so a configuration that declares nothing
  behaves exactly as before.
- One reader, `config.git_timeout`, feeds every git door that holds a
  configuration: the hand-off's commit, push and observation, the envelope
  carrier's push and fetch, the reviewer's fetch, and the authority,
  reference, enforcement and waiver reads. A door with no configuration
  keeps its built-in and says so.
- A timeout at ANY git door is now a blocked refusal (exit 1, `next:
  null`). It names the command as it ran, the ceiling and where it came
  from, and what the verb had already done: the local commit it made, a
  push it could not confirm, an envelope it kept.
- The remedy is a person's decision: raise the key, or make the hook or
  remote faster. The tool never skips a repository's hooks. A commit
  stopped inside its hooks leaves git's `index.lock` behind; the refusal
  names it and does not remove it.
- The cost, stated: a ceiling raised for a slow hook is also raised for an
  unreachable remote.

**Gate before push (issue #2).** `handoff` and `emit-request` used to
commit, push, and then run the gates, so a red blocking gate refused an
emission whose commit was already on the remote. The order is now:

1. commit, with the same preflight;
2. run the LOCAL gates at that commit;
3. refuse if the request validator would refuse them;
4. push and observe the remote ref;
5. await the CI-attested gates on the pushed commit (CI can judge only a
   pushed commit);
6. emit.

The gates still run once per hand-off, and the refusal in step 3 uses the
validator's own rule, so it and the post-emission validation cannot
disagree. A red blocking gate stops the hand-off with nothing pushed,
emitted or recorded. The refusal names the local commit (or says no
commit was made) and every failed gate, with the validator's items and
each gate's retained output. The tool does not undo its commit; the
author amends or resets it.

- An advisory gate's red stops nothing. `--local-only` gates before it
  emits. A warm re-run is unchanged.
- An attestation always binds to a commit, as it did.
- Cost: the CI-attested wait no longer overlaps the local gates.
- Result shape: a red gate is a blocked refusal before the push
  (`error`, `remedy`, `items`, `sha`, `gate_output`) instead of the
  post-push validation's item list.
- One review base, for the gates the hand-off runs locally. A symbolic or
  relative `--base` (`HEAD`, `HEAD~1`, the branch, its remote-tracking
  ref, a tag) is resolved once, before the hand-off commits outstanding
  work. That one commit id is what every local gate is told, what `Base:`
  stamps, and what the shape and the post-emission validation measure.
  Anything that is not exactly one commit (a range, a tree, an absent
  name) refuses before anything is committed. A CI-attested gate is
  outside this guarantee: CI runs it with no review base, and the hand-off
  takes CI's receipt, so that evidence binds the target commit but does
  not prove the review base. A range-sensitive gate (one that reads
  `LOUPE_GATE_BASE`) must run locally, not `attested_by = "ci"`, to get
  it. That limit is not new in 0.25.0; only the promise is.

**The verdict précis counts blocking findings by the target's taxonomy.**
`validate`, `close` and `brief` took the blocking set from the built-in
literals `Blocker` and `High`, compared case-sensitively. A repository
declaring lowercase severities read "7 findings, of which 0 block" over a
blocking `high` finding. They now read `review.toml` at the verdict's
target commit and judge each finding by the validator's own rule, naming
the §5.3a downgrade beside the finding. No built-in severity names remain.

- A severity the taxonomy does not declare is listed under its own
  heading.
- A taxonomy with no blocking list says so.
- An unresolvable target says blocking cannot be judged.
- `validate` without `--from-target` now reads the target's `review.toml`
  for the précis only.
- Wire: none; the précis is a rendering.

**Adapters need nothing beside the installed package (the user's ruling:
no adopter step may depend on a same-machine scenario).** With no `--dir`,
`render-adapters --install` and `--check-install` render from the running
package. Through 0.24.x they read an `adapters/` directory beside the
package, which a `uvx`/`uv tool` install never has, so they refused there
and `docs/upgrading.md` told adopters to point `--dir` at a clone. Now any
install form installs and verifies its own adapters. `--dir <path>` keeps
the directory-sourced behaviour. `docs/upgrading.md` §4 and the README's
install section need no clone.

**`render-adapters --check-embedded <file>` and `--write-embedded <file>`
(issue #5).** They check, and replace, exactly the generated instruction
block embedded in a tracked file (the `BEGIN`/`END GENERATED: loupe
adapter` region) against the current rendering. Every byte outside the
region is untouched.

- Refused by name: missing, unbalanced, out-of-order, duplicated and
  nested markers; carriage returns in the region; bytes that are not
  UTF-8; absent paths, directories and non-regular files.
- A symlinked file is followed, and the result says so.
- A stale region's recovery is the runnable `--write-embedded`.

**The Codex skill's install path (issue #1).** `--install` writes the Codex
skill to `~/.agents/skills/loupe/SKILL.md`, the user-scope path OpenAI
documents (Build skills, retrieved 2026-09-21), instead of
`~/.codex/skills/loupe/SKILL.md`. The old path was only ever observed to
work, never documented. On 2026-09-21, codex-cli 0.155.0-alpha.9.2 listed
skills from both roots, and a name present in both twice.

- A legacy copy under `~/.codex/skills/loupe/`, or under
  `$CODEX_HOME/skills/loupe/` when that is distinct, is kept byte for byte
  under the state directory's `replaced-adapters/`. Its bytes are proven
  equal to the copy's and re-read just before removal, and only then is it
  removed from discovery. The copy stays where it is when it is not UTF-8,
  when a kept file already holds other bytes, or when it changed after it
  was read.
- `--install` and `--check-install` judge an installed adapter by its
  bytes. A copy that differs from the rendering only in line endings is now
  `stale` and `replaced`, with its bytes kept, rather than `in_sync`. An
  installed copy that is not UTF-8 is kept and replaced, where it used to
  fail as a usage error.
- A legacy directory holding anything besides `SKILL.md` refuses the
  whole install, which writes nothing.
- `--check-install` reports a legacy copy still present as drift.
- A 0.24.x `--install` run from a clone writes the old path back, and the
  next 0.25.0 `--install` moves it out again.

**A validator notice for a reclassification without its residue (issue
#3).** `validate` reports `C-RESIDUE-UNDECLARED` when a `reclassified`
closure declares no `Residue:` while the same verdict raises a new finding
on the closed finding's anchor. "Same anchor" is convergence's own rule,
now one shared function. `C-RESIDUE-UNCHECKED` says when this machine's
ledger cannot judge. Both are notices: the exit status never changes.

**Reviewer requests (issue #4).**

- **`take --compact`**: pointers only — the kept path, digest, byte size,
  lineage, round, target, checkout state, précis, the target's `decide`
  list (the default take's, the empty list included) and the next command.
  On a 38,804-byte request it printed 1,654 bytes against the default's
  27,999; that measurement predates `decide` joining it. The default and
  `--full` are unchanged.
- **Five new claim members.** Four are a new member kind, a non-empty list
  of objects with a closed field table:
  - `carried_findings` — `fingerprint`, `origin` (`<lineage>/<round>`),
    `outcome` (`fixed | deferred | superseded`; withdrawal is the
    reviewer's act), optional `required` and `fix`. A fingerprint its
    in-ledger origin never ruled is refused before anything is committed.
    An origin this ledger does not hold is stated "not verifiable here",
    never refused. `required` is read from the kept origin verdict. Fix
    commits outside the span are marked.
  - `objectives` — `title`, `paths` (the `scope_paths` grammar), optional
    `tests` and `references`. The request renders objective → changed
    files, naming unmapped in-scope files and paths that match nothing.
  - `observations` — `command`, `result`, `context`. Hand-typed gate
    observations, rendered in the Claim under a heading that says they are
    NOT this hand-off's attestations.
  - `attestation_map` — `fingerprint`, `gate`, optional `test`. Each row
    must answer a finding: the previous round's standing disposition or a
    carried entry. Its gate must be in the target's manifest; that is
    checked after the commit, before any gate runs or anything is pushed.
    Rows render the attested command, exit and binding from the request's
    own attestation block.

  The fifth, `excluded_paths`, is a list in the `scope_paths` grammar:
  span paths declared out of this review, one prefix or glob instead of
  one line per file.
- **A `Scoped:` diff command** beside the full one, when the claim declares
  `scope_paths`. It names exactly the changed paths those entries match, as
  literal pathspecs, chosen by the same matcher as the objective table and
  the `In scope:` count; a matched `dir/` prefix stays one word. No pattern
  is handed to git, whose matching reads `[^a]`, backslashes and POSIX
  classes differently and selects everything below a bare directory name.
  When nothing matches, the line reads `none`, because a diff naming no
  path is the whole span. A command over 65,536 bytes is withheld with its
  size, never shortened. The matcher judges a rename by its new path, so
  the command never selects a rename's old path: a matched `dir/` excludes
  by name each rename source it would also reach, and a rename into scope
  shows as a whole-file addition of its target (the `Diff:` line still
  shows the rename).
- **An `In scope:` count** — the span less generated-by-declaration and
  excluded paths — shown in the request précis beside the machine total.
  It sits after the total, so a 0.24.x reader's recomputation still
  matches the total.
- **The scope report accounts for patterns.** A changed path matched by
  `scope_paths`, `excluded_paths` or an objective's `paths` counts as
  named, so `R-SCOPE-UNNAMED` fires less often.

The version moves because every change above travels.

## 0.24.1

Documentation only; no behaviour changes, and nothing addressed to
upgraders. This release adds no key, no marker and no envelope grammar, so
nothing in it asks a reader floor to move. That is a statement about what
the release REQUIRES, not a promise that the two versions are
interchangeable everywhere, and two things tell them apart by design. The
version string is one of the files the tool's content-derived identity is
computed over, so a request emitted at 0.24.1 carries a different `tool`
digest on its wrapper from one emitted at 0.24.0, as well as a different
version on its attestations, and a reader at the other version reports the
pair as `differs` — a report, never a refusal, and here the expected mark
of a version bump rather than a sign of a drifted installation. And a
repository that raises `[tool] requires` to 0.24.1 refuses a 0.24.0 reader
with the floor named, which is the floor doing its job; under a floor at
or below 0.24.0, or none, both readers accept the same valid envelope and
refuse the same defective one.

**`docs/upgrading.md` §6, Answer B, states two more places the window
sentence declares nothing.** 0.24.0 shipped before its review finished;
the review then closed clean, and along the way it tightened the reader of
the window opt-in sentence in the one harness that reads it. The page that
tells an adopter where to write that sentence now says what the reader
does: an HTML comment is judged before a preformatted opener, so a tag
named inside a comment opens nothing; a line carrying both a comment
boundary and a raw tag outside it, in either order, is an unresolved
context and refuses the whole file, inside a generic HTML block as at the
top level; and a comment or a preformatted container opened inside a
generic HTML block keeps its exclusion past the blank line that ends the
block. Seven lines of prose, inside the list of contexts that page already
gave. A sentence written as the page always said to write it — ordinary
prose opening a paragraph, a list item or a heading — is read exactly as
before.

The version moves because a tree that differs from the published 0.24.0
must not call itself 0.24.0.

## 0.24.0

Documentation, and one read-only verb. No key was added, nothing on the
wire moved, and no reader floor has to move for this release: an
installation at 0.23.0 and one at this version emit and validate
identically. What is new in the tool itself is `loupe decide`, which reads
and records nothing (last paragraph below).

**A written upgrade pathway: `docs/upgrading.md`.** Re-running
`docs/onboarding.md` on an already-onboarded repository upgrades nothing —
it is a one-time, local pass, by design — and no page said what moving a
pinned installation from one published version to another actually
consists of. The new page is seven steps, each with the command that
performs it, the check that proves it, and what skipping it costs: read
the CHANGELOG across the versions you cross; move the pin to a published,
PEELED commit, for both install forms — a provisioning script's tag and
commit pair, and the `@<commit>` of `uvx --from git+…` or `uv tool
install`, with `git ls-remote --tags <repo> 'refs/tags/vX.Y.Z^{}'` as the
way to read the peeled id off the remote rather than guess it; raise
`[tool] requires` to the floor the CONFIGURATION needs rather than to the
newest version you happen to have installed, because it is the oldest
reader that may take a round that the key is about; reinstall at the pin
on every machine that authors or reviews and re-run `render-adapters
--install` there; re-read the `decide` list against the new reader; re-run
onboarding §8 against the new adapter text; and run one round, so the new
reader's attestation is on the record. Two measured facts the page exists
to name: the adapters regenerate from whatever is INSTALLED, never from
the pin, so one repository can be governed by two different adapter texts
at the same time; and a cloud environment is rebuilt by its provisioner on
its next build while a person's machine is not, silently. It ends with an
upgrader's table across 0.16.0, 0.17.0, 0.20.0, 0.22.0 and 0.23.0, drawn
from those releases' own notes. The README's upgrade section, which said there
was no update mechanism, now points at the page and states the boundary
instead: upgrading is a procedure, not a verb, because the tool opens no
network connection in its own code and cannot fetch its own successor.

**What three real upgrades taught the page, folded in before it shipped.**
A floor is raised by MARKERS as well as by keys: a line an older reader
ignores in silence — the `# decided: <key> undeclared` comment honoured
from 0.17.0, the `loupe-fixture: corrupt-by-design` marker honoured by the
hand-off preflight from 0.22.0 — raises it exactly as an unreadable key
does, because `[tool] requires` is the only thing that turns that silence
into a refusal; the page works two floors out in full and shows why
neither reaches 0.23.0, whose two additions degrade honestly. One
measured floor came out at 0.17.0 while the tool stood at 0.23.0. 0.22.0
is the one crossing in the tabled span that asks the ADOPTING repository
to change its own tree, so it has its own sub-step: mark the
corrupt-by-design fixtures your suite writes into the tracked tree, or
declare `scope_paths` — with the three things that run taught, including
that a test module spelling the marker out verbatim refuses every later
hand-off that touches it. A grep for the old version separates live sites,
which move, from records, which do not. A repository that tracks no
rendered artefact has nothing in its tree to regenerate, which is the
common case and now says so — told by the GENERATED markers the rendered
text carries, never by filename, because the instruction block's ordinary
home is a region inside `AGENTS.md` or `CLAUDE.md` and a filename search
reports nothing for exactly the repository holding a stale copy; step 4
carries the marker searches, the regenerate-and-diff recipe for an
embedded region, and the version line that dates it at a glance. And a
long span is read twice: the CHANGELOG
for keys, floors and markers, then your own tracked files for sentences
about the tool that a release has falsified — 0.16.0's re-keyed gate
output path and 0.18.0's `LOUPE_GATE_HEAD` / `LOUPE_GATE_BASE` each did
that to an adopter's own prose.

**`loupe decide`** — a read-only verb that prints the `decide` list on
demand: every optional key this repository never declared, each with its
meaning, the value the tool applied, and the exact `review.toml` line for
`set` and for `unset`. The list previously rode only on verbs that emit or
record something, so an operator who had just moved a version pin had no
way to ask what the new reader wants of their configuration without
opening a round — three real upgrades on the day 0.23.0 was published each
had to import the package by hand to read it. `decide` reuses the
derivation the emitting verbs use — its entries are their entries — loads
the configuration through the same layers and refuses on the same `[tool]
requires` floor. It exits 0 whether the list is empty or not, records
nothing, creates no state directory, and runs with no ledger, outside any
lineage, on a detached HEAD and from a subdirectory. `roles.transport`
reports what an emission from the current environment would resolve,
resolved exactly as `handoff` resolves it. The "absent config asks once"
rule in the adapters names the verb.


**Onboarding's efficiency check asks the operator instead of deleting.**
§8's third row told a session to delete every sentence in a repository
instruction file that restates the user-level or machine-global file the
session loaded. That is right only where every session reading the
repository file also loads that global file. Where the repository has
collaborators on the forge, organisation or cloud accounts that load no
personal file, or agents on surfaces whose global file is a different one,
the deletion removes rules those sessions need — and the row's own
sentence, that the global file travels with nobody, is the reason it is
wrong there. The row is now an evaluated question with the same three
parts every time: MEASURE who reads this repository's file without the
operator's global one (the collaborator case has a read-only forge command
in the cell), LIST each sentence that restates it, and ASK the operator
keep-or-delete per group of readers — keep recommended wherever such a
reader exists, delete recommended only where a single operator's sessions
are the only ones that read the file. A session no longer applies this
silently.

**The relay choice is put in front of the operator, in both forms.**
Onboarding §8 and `docs/upgrading.md` §6 now state the choice a repository
makes about how a round reaches the reviewer — a person carries every
relay, or the repository declares an agent-relayed review window by name —
and the upgrading page writes out the text of both answers so neither is
the one an agent picks by default. The tool is untouched by this: it never
invokes a reviewer, there is no configuration key for a window and none is
planned, and the harness that would carry one is outside this tool and is
not published with it, so a reader whose adapter does not carry the
exception clause keeps the human relay and should.

*For upgraders:* nothing to install, nothing to migrate, and no
coordination between the two sides of a loop. Read `docs/upgrading.md`
once before your next re-pin; and if a session ever deleted restating
sentences from your instruction file under the old efficiency rule, §8's
new form is the pass that puts that question back to you.

## 0.23.0

Seven changes, all to what the tool SAYS or READS; none to what a round
binds. Most came from reviewers' own tool feedback.

**The `Round:` line reports the round, not a standing policy.** Its
parenthetical switched on one axis — whether a ledger override had moved
the cap — so `(budget breaker fires past the cap)` was printed
byte-identically on round 1 of every lineage, pairing a state report with
boilerplate nothing distinguished from it. Two readers, two rounds apart,
on opposite sides of the exchange, read the standing half as the event:
one spent a paragraph of a verdict ruling that no override was recorded,
the other told the operator that a fourth round needed their
authorization. It never did. Within the cap the line now says so; past it,
that nothing is gated, no authorization is needed, the tool emits rather
than refusing, and the convergence report the envelope already carries is
what the count stood in for. "Fires" is gone: past the cap nothing trips.
`loupe brief`'s past-cap banner carried the same sentence and is corrected
with it.

**`Base:` no longer invents a round 0, and neither does the disposition
section.** Round 1 of a lineage printed "the SHA ruled on in round 0" and
headed an empty section "Disposition ledger — round 0". There is no round
0. Round 1 now says the base is the one the author declared, ruled on by
no round of this lineage, and reports from the ledger whether any verdict
in the record ever ruled on that SHA — the common shape when a lineage
opens on the tip a closed one ended on. Later rounds are unchanged.

**The disposition ledger names the ruling it answers.** The section opens
with the previous round's verdict: its recorded digest and its kept copy's
path, composed from the verdict event itself, never from a directory
listing, so another lineage's round-N verdict cannot be served in its
place. Where no copy was kept it says so in words, and on a `paste` or
`git` round it says the digest is the half the reviewer can check.

**A `reclassified` closure declares where the narrowed claim went, and
convergence follows it.** A reviewer who confirms most of a finding fixed
and re-issues the residue under an accurate new title mints a second
identity on the same anchor; convergence read that as a new finding — the
signature of hunting — at exactly the moment the loop was working. Add one
continuation line under the closure, `Residue: F1` (several ids
comma-separated), naming findings of the same verdict. Optional: a
reclassification that leaves nothing behind omits it. Convergence counts a
declared residue as a continuation (`reclassified_per_round`, and
`descends_from` / `reclassified_to` / `descends_via` on each thread); a
genuinely new finding beside a residue on the same anchor still reads
`hunting`. For records written before this field, the residue is read from
the round-local finding id in the closure's own note, fail-closed: the
whole note must be readable before any of it is used. Every id-shaped
token in it — `F1`, and near-misses of one such as `f1` or `F01` — must
place exactly to an id of that round, all of them to one identity, before
any question of whether that identity could be a residue. An ineligible
name makes the note ambiguous rather than making another name unique, and
a token this reading cannot place makes it unreadable rather than leaving
the rest looking unique. A legacy `R1-F5`, a fingerprint and a path are
not id-shaped, so they neither name nor refuse. Every followed edge is
labelled `declared` or `inferred from the closure's note`. Declared on any other closure term it is refused
(`C-RESIDUE-TERM`), as are an id no finding of the verdict carries
(`C-RESIDUE-UNKNOWN`), a malformed or empty value (`C-RESIDUE-ID`) and a
repeat (`C-RESIDUE-DUPLICATE`). *Reviewers:* the `rule` step and the
request's verdict-shape block both ask for it. *Upgraders:* nothing to do.
A verdict carrying the field validates on 0.22.0 and on 0.16.0, which read
the line as part of the note, and a ledger carrying the `residue` member
reads there as before; only `import-legacy`'s closed event grammar on an
older installation refuses such a row.

**Generated paths get their own heading in the request's span.** A changed
path whose `linguist-generated` attribute is set IN THE TARGET TREE is
listed under its own heading with its own counts, and the header states
both totals — every changed path, and the part that is not generated — so
a reviewer skips rendered inventories by rule instead of by inspection.
*To benefit, mark your generated paths `linguist-generated` in
`.gitattributes` and commit it* — the attribute forges already use to
collapse those files in a diff. It is read from the commit under review,
never from the emitting checkout, and from that commit's tracked
declaration alone: the lookup is isolated from `info/attributes`, from a
configured or default user attributes file and from the system one, so no
machine-local override can promote a handwritten path or suppress a
declared one, and where that isolation cannot be established the span says
nothing about generated paths rather than something partly local. There is
no configuration key for it and
no inference from a file's name. A repository that marks nothing gets the
span it always got, byte for byte; so does a git older than 2.40, which
lacks `check-attr --source`.

**The `respond` step is ordered.** It asked for a verified acceptance and
THEN said to make the changes. It now reads: make the fix; run the
finding's named test on the fixed head; run the mutation and restore; THEN
write the record with `--out`; hand off. A fix proposed but not made is
`deferred` or `escalated`, never `accepted`.

**Tests: one grammar for the declared interpreter interval.** Two
travelling tests parsed `project.requires-python` with looser regexes than
each other — one by substring. `review/tests/util.declared_interval` is
the single bounded, whole-value grammar, and a test fails if a second
reader of the interval appears in the package.

## 0.22.0

**`handoff` names what its commit sweeps, before the commit exists, and
refuses two states nobody could mean.** `handoff` commits outstanding
tracked work and pushes it BEFORE any gate runs, so a gate protects the
envelope and never the commit. That ordering is sound only while the dirty
paths are the author's. It stopped being sound the first time a test suite
was killed at a timeout: its corrupt-by-design fixture stayed tracked by
intent-to-add beside a repointed record, and the next hand-off in that
workspace would have published both under the author's envelope. The order
stays — what an attestation binds to does not change — and a preflight now
runs ahead of the commit.

*`scope_paths`, a new optional claim member.* A list of strings: an exact
path, a directory prefix ending in `/`, or an fnmatch glob. When the claim
declares it, outstanding tracked paths outside it are REFUSED before the
commit, with nothing committed, pushed or emitted. `[]` is a declaration
("this round sweeps nothing"), not silence. `--allow-outside-scope` sweeps
them anyway, and the request says so on its face. `review_scope` stays the
prose a reviewer reads; this is the half a machine can hold a commit to.
A claim that does not declare it behaves as before, and the request says
the sweep was held to no path list.

*The fixture marker.* A swept file carrying the marker line — the phrase
`loupe-fixture:` followed by `corrupt-by-design`, alone on a line, comment
leaders allowed — is refused, and no flag overrides it: the remedy is to
restore the tree. A repository opts in by writing the marker into fixtures
its suite places in the real tree. It is a whole line and never a
substring, so a document that explains the marker does not trip it.

*On the request's face.* Two header lines, printed in every state:
`Swept:` lists the paths this hand-off committed (or says it committed
nothing), whether they sat inside the declared scope, and any
intent-to-add entries among them; `Env:` gives the interpreter and whether
it is inside the declared range, whether the process ran as root, and
whether the `loupe` on PATH is this installation. Older readers ignore
both lines.

*An interpreter below the floor gets a sentence.* The package's first
statement now checks the interpreter, in syntax any Python 3 parses. Under
3.9 the tool used to die with `TypeError: unsupported operand type(s) for
|` from inside its own package; it now says which version it needs, which
it found, and that nothing was read or written.

*How the sweep is read.* Not from `status --porcelain` and the working
tree: the first draft did that, and its own review found three faces of the
mistake — git quotes a non-ASCII name in that output, `commit -a` records
the index for a `skip-worktree` or `assume-unchanged` entry and the link
text for a symlink, and a rename is two paths. The preflight now COPIES the
index to a temporary file, runs `git add -u` against the copy (the exact
staging `commit -a` performs), lists the candidate with `diff --cached --raw
-z --no-renames` (lossless paths, a rename as its D and its A, the blob id
each path will record) and reads each recorded blob's bytes. A submodule
pointer has no bytes to judge and is refused by name, and both readers
pass `--ignore-submodules=none` so no diff setting can hide one. After the
real commit, the commit's own raw diff is compared with the candidate by
status, path, mode and blob id: a hook, filter or attribute that changed
what was recorded — the executable bit alone included — is refused with
the commit named, nothing pushed. The real index is never written.

## 0.21.0

**`take` prints the request compactly, and says what the reviewer's
checkout is.** Three debug rounds in a row (2026-09-17/18) the reviewer's
tool feedback named the same cost first: `take` returned the whole request,
and 64% of a measured 29,589-byte request was the attestation array — five
of its twenty objects repeating one CI run and one receipt each.

*The default rendering.* `take` now prints the request with its
attestation objects as one table: gate, result (`exit N`, or `NOT RUN:` and
the reason), binding, tree, where it ran, whether it blocks, who executed
it, the command, the output's digest and the log's pointer, whole. `= target` in the ran-at column
means `executed_sha` = `target_sha` = the request's own sha; any other row
prints both shas. Every other section is byte-for-byte the envelope's. A
block that does not parse, or holds a record that is not an object, is left
exactly as it was: the rendering never summarises what it could not read.
The measured request went from 29,589 to 13,727 bytes.

*The full mode.* `take --full` prints the exact bytes, and the `kept` path
— which `take` always wrote — holds them either way. Nothing else reads
the rendering: validation, the digest, the kept copy and the ledger all
work from the original bytes, so this is a view, not a second format.

*For consumers of the JSON result.* The default payload carries
`request_view` and NO `envelope` key; `--full` carries `envelope` and no
`request_view`. `envelope` means the exact bytes and nothing else, so a
caller that pipes it onward is never handed a rendering under that name. A
script that read `envelope` from `take` passes `--full` or reads `kept`.

*`head`.* The result gains `head` — `{state, sha, tree}` with `state` one
of `at-target`, `elsewhere`, `unknown` — and the text prints it in all
three states. It is reported, never refused: a reviewer rules from the
diff command as legitimately as from a checkout, and an empty clone is a
documented reviewer state. It exists because a reviewer whose working tree
sat at an older commit read the right diff and ran its probes against the
wrong files.

*The gate banner.* A request précis said "1 of 20 did not pass" for a
NON-blocking gate reporting the branch's earlier CI history, and a reviewer
read a failure of the target. Blocking rows that did not pass are now
named as failures AT THE TARGET; non-blocking ones get their own line,
which says whether every blocking gate passed. A row with no `blocking`
member is read as blocking.

## 0.20.0

**The lineage is keyed.** Every event now carries a lineage `id`, and every
round-scoped read takes that id as an argument instead of deriving it from
a position in the file. Until this version `current()` meant "everything
after the last `lineage_closed` marker", so one repository held exactly one
review in flight, structurally: two worktrees emitting into the shared
ledger both computed lineage 1 round 1, and closing either discarded the
other's request unruled. N reviews now coexist in one ledger.

*The id.* A new lineage's id is `L` followed by ten hex characters
(`secrets.token_hex(5)`) — opaque, and letter-led so it can never be read
as a legacy ordinal, which is decimal digits. It is displayed everywhere
the number used to be: `lineage L3f9a1c2b7e` rather than `lineage 27`. The
exchange path is `exchange/lineage-<id>/…` and the envelope ref is
`refs/loupe/<id>/<round>/<leg>`, carried as `git:<id>/<round>`. A legacy
lineage keeps its ordinal as its id, so every retained path, every pushed
ref and every historical citation of "lineage 25" stays true.

*Which lineage a verb acts on.* `handoff` continues the OPEN lineage whose
requests were recorded on this worktree's branch, and mints a new one when
this branch holds none — so a second worktree opens its own review instead
of being refused. `close`, `respond`, `brief <envelope>`, `ledger add` and
`validate --from-target` resolve the lineage from the SHA their envelope
names, through the request that recorded it. `close --lineage`,
`authorize-advance`, `waive --finding` and the `ledger` verbs act on this
branch's open lineage. A detached HEAD with several lineages open is the
one state where a lineage cannot be chosen, and the verb refuses saying so
rather than picking. `take` uses the id the `git:<id>/<round>` reference
carried, else the lineage this ledger already recorded for the commit, else
the single open lineage, else a new id.

*Reading older ledgers.* The reader derives a key for every event: an
explicit `lineage` id wins; an event without one belongs to the legacy
positional lineage — the ordinal counted by `lineage_closed` markers in the
prefix before the first keyed event, as a decimal string. Absence is a
third state, never a collision and never a default of "the current
lineage". A mixed ledger keeps its prefix's ordinals under the keyed events
that follow.

*The cross-lineage notice.* Fingerprint identity aliases are global by
design, so one identity can be ruled in two lineages at once and disposed
differently. Answers stay derived over ONE lineage's rulings, bound to the
newest ruling of each identity, exactly as before. What is new is a notice:
at emission and in `brief`, a standing finding whose identity is also ruled
in another OPEN lineage is named with that lineage's id, round and
disposition. It answers nothing and changes no record.

*The report aggregate.* `ledger report` reports the lineage this branch is
on and gains the repository aggregate: every open lineage with its id,
branch, rounds and summed counted tokens. A fact, with no breaker and no
new config key — the round cap counts the rounds of one review and is never
aggregated, and the token budget stays per lineage.

*Reservations.* `LineageReservation` now locks `lineage-<id>.lock`, so what
it excludes is two commands acting on the SAME review; two worktrees
running two different lineages share nothing to overwrite and are no longer
refused. Opening a new lineage additionally takes a short repository-wide
`lineages.lock` across the id assignment and the first record, so two
openers cannot mint one review's worth of state twice.

*Retired.* The 0.19.0 concurrency refusal is gone — `handoff` no longer
refuses while another branch holds an open request, and `close` no longer
refuses over another branch's round, because neither is in this lineage any
more. `brief` still names a round that was emitted and then discarded,
truthfully, for ledgers that already carry that state.

*Which review a commit's envelope belongs to.* A SHA names a commit, and
two branches may examine one commit under two independently scoped
reviews. The SHA resolvers therefore keep the review identity the
invocation carries — the branch it was run from, or the id a
`git:<id>/<round>` reference names — through lookup, validation, retention
and verdict publication. Where a commit is under review twice and the
invocation names neither review, the verb refuses and records nothing; it
never chooses by the order the two reviews happened to be recorded in. One
review of a commit, and every legacy ledger, resolve exactly as they did.

*The request envelope names its lineage.* The request wrapper now carries
`lineage="<id>"`, the id the author's side records, keeps and pushes the
round under. It matters on the reviewer's side, where `path` and `paste`
supply no reference: `take` used to fold any previously unseen commit into
whatever single review was open on its ledger, so a reviewer taking two
independent branch reviews recorded both as one. `take` now binds to the
carried id; failing that to the review this ledger already recorded for the
commit; failing that to the open review whose recorded author branch is the
envelope's, which keeps sequential continuation working for envelopes that
carry no id; and it refuses rather than adopting when more than one
association remains.

*Lifecycle reads happen under the reservation.* `handoff`, `close` and
`authorize-advance` re-read the ledger from disk immediately after
acquiring their reservation and revalidate the lineage they selected before
it. A lock excludes an operation while it is held but cannot make an
earlier read current, so a close completing while a handoff waited for the
lock was invisible and that handoff appended a round-1 request behind the
review's own closure. A lineage open at selection and closed by the time
the reservation is granted now refuses, having recorded nothing.

*Envelopes are read once.* `close --verdict -` and `brief -` read their
source a second time to resolve the lineage, which left the real read an
empty document — the paste relay's own generated close command is a
heredoc. Each verb now captures its source exactly once and uses those
bytes for both resolution and processing, on every carrier.

*`import-legacy` validates against the lineage it appends to.* Round
sequence and answer-to-ruling binding are read from the destination lineage
instead of the whole ledger, so a concurrent review can no longer supply the
ruling an imported withdrawal claims to settle or the round a later verdict
skips; identity aliases stay global, and their effect is checked against
each affected lineage's own answers.

**A gate can be attested by CI instead of executed locally.** A `[[gates]]`
row may declare `attested_by = "ci"`. `handoff` then does not run that
command: the branch is already pushed when gates run, so the runner polls
the Actions API for a completed workflow run at the exact reviewed SHA and
records that run's identity, URL and conclusion as the attestation. The row
carries `attested_by: "ci"` and a `ci_run` object beside every field the
attestation already required, and its retained output is the run JSON rather
than a command transcript — verifiable by opening the URL, not believable by
trusting the author's machine. Inside CI (`GITHUB_ACTIONS`) the gate runs
normally, since there this process is the executor; `loupe-gates
--execute-ci-gates` forces local execution. A conclusion other than
`success` fails the gate with the run URL; no completed run at that SHA
inside `[limits] ci_timeout` (new; default 900 seconds, polled every 20),
`gh` missing or unauthenticated, or an unresolvable repository or branch is
recorded in the not-run shape, so a blocking gate still refuses. A run on
the same branch at a different commit is never admitted. A run's
conclusion is not a gate's result: CI publishes a receipt — one row per
declared gate with its command, exit code and not-run reason, uploaded as
the artifact `loupe-gates-<sha>` — and the runner decides each gate by its
own row; a green run with no receipt, no row for the gate, a row declaring
not-run, a changed command or a receipt for another commit is a not-run
record. The validator checks the receipt beside the run: required fields
and types, the SHAs, the conclusion against the exit. The cost: wall
time per handoff grows and CI minutes become a review dependency; the poll
is run-level, so a red workflow at the reviewed commit refuses every
CI-attested gate. *For upgraders:* `attested_by` and `[limits] ci_timeout`
are new keys, unreadable to earlier installations, which refuse naming the
version skew — unknown keys inside a gate row now get the same remedy
unknown sections have had; adopt them once both sides of your loop parse
them.

*The review an envelope names travels with it.* `brief`, `ledger add`,
`respond` and `close` carry the identity a supplied envelope names — the
`lineage` the request wrapper stamps, or a `git:<id>/<round>` reference —
ahead of the checkout's own branch; a stamp naming a review this ledger
does not hold refuses without writes rather than recording under the
branch's review. And a fresh reviewer can take two independently emitted
reviews of one commit over the git carrier: the retake check compares
request bytes, not commits, so the same bytes carried under a second
lineage still refuse while a second review of the same commit enters.

*For upgraders:* the `lineage` attribute on the request wrapper is the one
wire change in this version and needs no coordination: an older reader
tolerates it as it tolerates every unlisted wrapper attribute, and an
envelope emitted before this version carries none, which `take` reads as
the legacy path. And **nothing migrates, and nothing needs to.** An event
written before this version carries no id, and the reader derives one for
it: the ordinal counted by `lineage_closed` markers in the prefix before
the first keyed event, as a decimal string — every number, path and ref an
older ledger already had. New events carry ids from this version on, and a
ledger that mixes the two reads correctly. There is no command that
rewrites older rows with explicit ids: `migrate-state` still does only what
it did before, move state written under a former tool name with its
digests verified, and materialising legacy ordinals is deferred until
something needs them on the rows themselves.

## 0.19.0

**A second worktree can no longer open or close a round over another's.**
The state directory is keyed by repository, so two worktrees of one
repository share one ledger — by design; a per-worktree ledger was the
earlier defect. Both computed the same lineage and the same round, neither
was told about the other, and closing either one discarded the other's
open request: never ruled, never refused, gone from the record. Under the
`git` carrier it was worse still — both force-pushed their envelope to the
one `(lineage, round)` ref, and the second silently overwrote the first.
The request event now records the emitting worktree's `branch`, read off
the envelope's own stamp. `handoff` refuses while another branch holds an
open request in the lineage, before anything is committed, pushed or run —
including before a `git` round pushes its envelope ref. `close` refuses a
verdict that leaves an open request unruled; the human's way through is
the recorded decision that already existed, `close --lineage --reason …`,
and there is no new flag. Branch, not SHA, is the axis, so
amend-and-re-emit is untouched: it still supersedes its own emission at a
new SHA and still reports the superseded count. `brief` also names a third
state it used to deny — a round emitted and then discarded when the
lineage was closed at another SHA — which ledgers written before this
version can already carry. *For upgraders:* nothing migrates. Events
recorded before the field carry no branch, absence is read as unknown, and
a collision is never concluded from it. This refusal is a stopgap: a keyed
lineage, which lets two rounds coexist, retires it.

**And two worktrees can no longer be admitted to one lineage at once.**
The branch refusal above is a single read of the ledger, and everything a
handoff does after it destroys: it commits outstanding work, pushes the
branch, runs the gate manifest — minutes, usually — emits, and only then
records the round. Two worktrees whose handoffs merely overlapped
therefore both passed that check, because neither had recorded anything
for the other to see: both opened round 1, and under the `git` carrier
the second force-pushed its envelope over the first at the shared
`(lineage, round)` ref. `handoff` and `close` now take an exclusive
reservation on the repository's state directory — the one directory every
worktree of a repository shares — before the lifecycle is read, and hold
it until the round is recorded or the command has failed. It is an OS
lock, so a process that dies releases it: there is no stale lock to clear
and no flag to override it with. A second worktree is refused, not
queued, and told which branch holds the reservation, as what, and since
when; it retries when that command finishes. A close is covered the same
way, so it cannot slip between another worktree's admission and its
record. Sequential flows are untouched — amend-and-re-emit takes the
reservation, finds it free, and proceeds exactly as before. *For
upgraders:* on a platform without `fcntl` the reservation cannot be taken
and both verbs refuse, naming why, rather than running unguarded.

The reservation covers every command that can end a lineage, not the two
that were caught racing: `authorize-advance` appends the same
`lineage_closed` marker as `close`, and now holds the same exclusive claim
from before its pending-round read until after its record. An ordinary
human advance can no longer close over a handoff being admitted beside
it, and a handoff can no longer open a round inside an advance; the
refusal names the holding branch, the verb it is running and its pid, as
the other two do. Taking the reservation grants nothing — the advance is
still a named human's recorded decision, refused as before while a round
awaits a verdict or a standing finding is unanswered.

## 0.18.0

Three findings of an external audit of the tool (2026-09-05), each
reproduced in a disposable repository before it was fixed.

**Retained envelopes are kept by lineage, round, kind AND digest.** The
kept copy was `exchange/round-<n>-<kind>.md`, named by round and kind
alone — and every lineage starts at round 1, so the request that opened
one lineage overwrote the request that opened the last, and the rule that
restores a differing copy from the canonical text made the loss permanent.
The ledger digest survived and could not recover the document. A kept copy
now lives at `exchange/lineage-<l>/round-<n>-<kind>-<digest12>.md`: no two
documents can share a path, and a copy whose bytes do not reproduce its
own name is a rewrite every reader already refuses. *For upgraders:*
nothing is moved. Copies retained under the flat name stay where they are
and stay readable — every reader falls back to the flat name, still
digest-checked against the ledger — and new emissions land only under the
lineage directory.

**`close` judges the verdict under the target commit's own review.toml.**
It validated against whatever the checkout held, while `take` and
`validate --from-target` judged under the target's; a checkout that had
since renamed a severity refused, with `V-SEVERITY`, a verdict its target
had already accepted, and the recovery it printed omitted `--from-target`.
One authority for the three doors now, and the recovery names it.
Unrelated checkout changes cannot alter the acceptance of an already
reviewed artifact.

**Every gate is told the review range.** `handoff` commits outstanding
work before the manifest runs, so a gate that inspected the working tree —
`git diff --check` was one — inspected nothing and was attested `bound`
over commits that carried the defect. The runner now exports
`LOUPE_GATE_HEAD` on every run and `LOUPE_GATE_BASE` whenever the emission
knows the base (a handoff always does); a gate that checks a range reads
these, and a run without a base exports none, so the gate can say what it
fell back to rather than guess.

## 0.17.0

**A key with no off value can be left undeclared by decision, durably.**
`[limits] token_budget` is uncounted when absent, and every integer a
reader accepts is a budget the breaker fires on, so the `decide` entry for
it printed no `unset` line and the adapter's ask-once could never end: an
intentionally unbudgeted repository heard the question in every session.
The entry now prints its `unset` as a comment line, `# decided:
limits.token_budget undeclared`; written under `[limits]`, the current
reader treats the key as decided — no entry — while the key stays absent
and uncounted. A comment is the one form every reader parses without
refusing: an older reader ignores it and keeps asking, which is this seam's
usual forward-only reach. The marker counts only for keys whose off state
is that line, so it cannot silence a key that has a real off value.

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

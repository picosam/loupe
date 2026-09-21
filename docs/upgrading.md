# Upgrading a pinned loupe

Onboarding is a one-time, local act: re-running
[onboarding.md](onboarding.md) on a repository that already carries a
`review.toml` upgrades nothing. Moving a repository from one published
version of the tool to another is this page — seven steps, each with the
command that performs it, the check that proves it, and what it costs to
skip it.

**This is a procedure, not a verb.** There is no `loupe upgrade`, and
there will not be one: the tool opens no network connection in its own
code — the two networked steps are git subprocesses, `handoff` pushing
and `take` fetching — so it cannot fetch its own successor, and a tool
that rewrote the pin it is judged under would be attesting rounds against
a version nobody chose. A provisioning script that compares its pin
against upstream and prints one line when they differ is doing the whole
of its job; applying the difference is this page, run by a person and
their agent together.

Who runs it: an agent can execute every command below and read every
check. Two steps end in a decision that is the operator's alone — the
floor in step 3 and the relay choice in step 6 — and the page says so
where they fall.

## What you need before step 1

- **The version each machine is on.** `loupe --version`, run on every
  machine that authors or reviews in this repository, plus the pin your
  provisioning script holds. These can already disagree; that is one of
  the things this procedure exists to end.
- **The version you are moving to**, as a published tag on the
  repository you install from.
- **The list of machines.** A person's laptop, a cloud environment, a CI
  runner that reviews — each is a separate execution of step 4.

### The pin, and the two forms it takes

Everything below is written for both install forms the README
describes. Find yours first, because the pin lives in a different place
in each.

**Form A — a clone at a pinned commit, from the repository's own
provisioning script.** The pin is two tracked values: a tag for people to
read and a commit id for the machine to check out.

```sh
LOUPE_TAG=v0.23.0
LOUPE_COMMIT=<the peeled commit id of that tag>
```

```console
$ git clone --quiet <repo> "$loupe_dir" 2>/dev/null \
    || git -C "$loupe_dir" fetch --quiet origin
$ git -C "$loupe_dir" checkout --quiet "$LOUPE_COMMIT"
$ ln -sf "$loupe_dir/bin/loupe" ~/.local/bin/loupe
```

The variable names are yours; the two values are the point. Keep them
together: a tag alone is a movable label, and a commit alone tells the
next reader nothing about which release it is.

**Form B — Python packaging, no clone.** The pin is the `@<sha>` on the
source:

```console
$ uvx --from 'git+<repo>@<commit>' loupe --version
$ uv tool install 'git+<repo>@<commit>'
```

`uvx` re-resolves on every invocation, so it tracks exactly what the
`@<commit>` names and nothing else; `uv tool install` puts `loupe` on
PATH until you install over it. Neither packages `adapters/` — step 4
says what that costs and how to answer it.

**Pin the peeled commit, never an unpublished ref.** A tag that exists
only on the machine that made it, a branch tip, a local ref: none of
them is fetchable by the next environment that builds itself, and an
attestation that names a build nobody else can obtain is a claim, not a
record. Read the commit a published tag names straight off the remote:

```console
$ git ls-remote --tags <repo> 'refs/tags/v0.23.0^{}'
```

`^{}` peels the tag to the commit it names. Without it, an annotated tag
prints the tag object's own id instead, and pinning that makes the pin
and `git rev-parse HEAD` disagree forever — the drift line then compares
two different kinds of thing and reports a difference nobody can fix. If
`ls-remote` prints nothing for the peeled form, the tag is either
lightweight (the unpeeled line is already the commit) or not published;
check which before pinning anything.

## 1. Read the CHANGELOG between your pin and the target

```console
$ git show <target-commit>:CHANGELOG.md
```

or, in a clone you already have:

```console
$ git -C <clone> log --oneline <current-commit>..<target-commit> -- CHANGELOG.md
```

Three things to name per version, not one: a configuration **key** it
adds; a **marker** the reader newly honours somewhere in your tree; and a
change that asks **your own repository to move** rather than your pin.
Across the span this page tables, 0.16.0 to 0.23.0, there is exactly one
of the third kind — 0.22.0 — and it has its own sub-step below. Sections
addressed to upgraders say so in the text; the table at the end of this
page collects the span.

**Then grep your own tree for statements about the tool**, which is the
half a long span makes expensive and a short one still catches:

```console
$ git grep -n -i loupe -- ':!CHANGELOG*'
```

Read every hit that asserts a behaviour — configuration comments,
instruction files, developer documentation — because a release can
falsify a sentence you wrote and nothing will tell you. Two measured
instances from a repository crossing eleven versions: 0.16.0 re-keyed
retained gate output to `gate-output/<sha>/<run>/`, which falsified a
path written into that repository's own configuration comments; and
0.18.0 began exporting `LOUPE_GATE_HEAD` and `LOUPE_GATE_BASE` to every
gate, which falsified the stated reason a gate had been removed — the
reason was that a gate could not know the review range, and by then it
could.

**Check:** you can name, per version crossed, which of the three kinds it
is (or that it is none), and every sentence your own tree asserts about
the tool is still true at the target version.

**Skipped:** you find out mid-round. A key adopted on one machine and
unreadable on another does not fail at the moment you write it — it
fails when the other side is handed the target, which is the reviewer's
first command of a round you have already emitted. And a falsified
sentence in an instruction file is worse than a stale comment: an agent
acts on it without checking.

## 2. Move the pin to a published, peeled commit

Form A — edit the two tracked values and commit them:

```console
$ git ls-remote --tags <repo> 'refs/tags/v0.23.0^{}'
$ ${EDITOR:-vi} <your provisioning script>
$ git add <your provisioning script> && git commit
```

Form B — change the `@<commit>` wherever the install is written down (a
provisioning script, a developer README, a CI step), and reinstall in
step 4.

Work on a branch named by your repository's own convention, and write
the commit subject in your repository's own commit vocabulary — some
admit only a closed set of prefixes, and this page is in no position to
know yours. (An example, not an instruction: `chore: pin loupe v0.23.0`.)

**Find every place the old version is written down, and move only the
live ones:**

```console
$ git grep -n '0\.16\.0\|v0\.16\.0\|<the old commit>'
```

A grep for the old pin returns two kinds of hit, and they are not
treated alike. **Live sites move**: the pin in the provisioning script,
`[tool] requires` in `review.toml` (step 3), and any prose in an
instruction file that states which version this repository currently
runs. **Records do not move**: dated evidence, a changelog of your own,
a review or decision record, a post-mortem, a commit message. Those
state what was true when they were written, and editing them to match
today's pin destroys the one property that makes them worth keeping. If
a record's age is not obvious from where it sits, that is an argument
for dating it, never for rewriting it.

**Check:** after a fresh provision, `git -C "$loupe_dir" rev-parse HEAD`
equals `LOUPE_COMMIT` (form A), and the script's own drift comparison
reports no difference. Under form B, `uvx --from 'git+<repo>@<commit>'
loupe --version` prints the target version. The grep above returns
nothing but records.

**Skipped:** the pin still names the old build, and every step after
this one upgrades a machine while the environment that rebuilds itself
quietly goes on provisioning what it always did.

## 3. Raise `[tool] requires` to the floor the configuration needs

```toml
[tool]
requires = "0.20.0"
```

**Raise it to the floor your configuration actually needs, not to the
newest version you happen to have installed.** The key is a statement
about the oldest reader that may take a round in this repository, and
every machine that authors or reviews has to clear it. Set above what
the configuration needs, it refuses a machine that could have read the
file perfectly well. Set below what the configuration needs, an older
reader meets a key it does not know and refuses the target — from 0.16.0
it at least says a version skew is possible and names its own version,
which is a better error, not a smaller problem. The floor is checked
before schema validation, which is the feature: a reader that knows the
key tells you the versions instead of telling a person to repair a
correct file.

So derive it from what the file declares, key by key. `attested_by` on a
gate row and `[limits] ci_timeout` need 0.20.0. `[roles]
review_default` and `[roles] enforcement` need 0.16.0. A configuration
that declares none of the newer keys needs no new floor at all, and
raising it anyway is how a reviewing machine that was fine yesterday
stops being able to take a round.

**Markers count, not only keys — and they are the half that is easy to
miss.** An unreadable key announces itself: the older reader refuses and
names it. A marker does the opposite. It is a line somewhere in your tree
that a newer reader honours and an older one **ignores in silence**,
producing no error at all and simply not doing the thing you wrote the
line for. Anything in that class raises the floor exactly as an
unreadable key does, because `[tool] requires` is the only mechanism that
turns that silence into a refusal a person can act on.

Two such markers exist today:

- **`# decided: <key> undeclared`**, honoured from 0.17.0. Written under
  its section, it records that the key is undeclared on purpose and stops
  the question. An older reader parses the comment as a comment, which is
  to say as nothing, and asks again every session.
- **`loupe-fixture: corrupt-by-design`**, honoured from 0.22.0 by the
  hand-off preflight, which refuses to sweep a file carrying it. An older
  reader has no preflight: it sweeps the file, commits it and pushes it,
  which is the whole failure the marker exists to prevent.

Worked examples, with the arithmetic shown:

- A repository whose configuration is otherwise entirely readable by its
  old reader, and whose only newer line is the decided-undeclared
  comment, comes out at **0.17.0**. Not at the newest published version —
  measured on a real upgrade, where the floor that came out of this step
  was 0.17.0 while the tool was at 0.23.0, because that single comment
  line was the only thing in the tree the old reader could not honour.
- A repository that also carries the fixture marker in test fixtures its
  suite writes into the tracked tree comes out at **0.22.0**.
- Neither is pushed to **0.23.0** by that release's own two additions. A
  `Residue:` line and a `linguist-generated` attribute degrade honestly
  on an older reader: the verdict still validates, and a repository that
  marks nothing gets the span it always got, byte for byte. That release
  note says in those words that there is nothing for an upgrader to do.
  The discriminator is not "does an older reader notice", it is **what
  its not noticing costs you**: a decision defeated, or a refusal that
  does not happen, raises the floor; a convenience the newer reader adds
  on top of unchanged output does not.

**Check:** point an installation *older than the floor you just wrote* at
the repository and confirm it refuses on the floor rather than reading
anything:

```console
$ loupe ledger report          # run from the repository root, older installation
```

It must answer with the floor refusal — the shape is `… requires loupe
<declared> or newer; this installation is <installed>`, followed by the
statement that the configuration is not broken and the reader is older
than the repository it was asked to read — and it must not get as far as
reporting gates, a round cap or anything else. Then run the same command
on the machine with the **oldest** installed loupe of everyone who
actually reviews here: that one must print your gate ids and round cap.
Those two runs together are the floor, one proving it bites and one
proving it does not bite the people who have to work under it.

**Skipped:** the version skew arrives as an unexplained refusal on
whichever machine happens to be behind, and the remedy it prints without
the declaration sends someone to repair a file that is correct. Or, for a
marker, nothing arrives at all — the line you wrote is silently inert on
the machine that most needed to honour it.

## 3a. If your suite writes corrupt-by-design fixtures into the real tree

This is the one change in the 0.16.0–0.23.0 span that asks the **adopting
repository** to change its own tree rather than its pin, and it lands with
0.22.0's hand-off preflight. If your test suite writes deliberately
corrupt fixtures into the real, tracked tree — the shape that bit the
tool itself, a suite killed at a timeout leaving its corrupt fixture
staged — then give those files the marker line, or declare `scope_paths`
in your claims so the sweep is held to paths you named. Doing neither
leaves you exactly where the preflight was built to stop you: the next
hand-off in that workspace commits and publishes the corruption under
your own envelope.

The marker is the phrase `loupe-fixture:` followed by
`corrupt-by-design`, alone on a line, comment leaders allowed. Three
things learned running this for real:

- **A fixture format that admits a comment takes the marker as a comment
  and parses identically** — YAML, TOML, shell, Python and anything else
  with a line comment. Do not take that on faith: assert it in the suite,
  so the fixture is proven to still parse to the same value with the
  marker line in it. A format with no comment syntax at all cannot carry
  the marker, and that fixture needs the `scope_paths` answer instead.
- **Mark only the fixtures that are genuinely the corrupt case.** The
  marker asserts corruptness; putting it on a valid paired control makes
  the control a lie and refuses a hand-off that should have proceeded.
  And it cannot ride on a symlink or on an untracked file, so a suite
  whose corrupt artifacts are either of those still owes `scope_paths`.
- **Never write the marker line verbatim in test source the hand-off may
  sweep.** It is matched as a whole line wherever it appears in a swept
  file, so a test module that spells it out literally refuses every later
  hand-off that touches that module — the suite that writes the marker
  becomes the thing the marker refuses. Assemble it from two string
  halves in the source and join them where it is written.

**Check:** with the marker in place, the suite passes (including the
parse-identity assertion above), and a hand-off attempted while a marked
fixture is outstanding refuses by name instead of committing it. A
repository that chose `scope_paths` instead proves it the other way: a
claim declaring the paths this round may touch refuses outstanding work
outside them, with nothing committed, pushed or emitted.

**Skipped:** nothing happens on the old reader, and the first hand-off
after the upgrade either refuses in a way nobody expected, or — if you
also skipped the marker — publishes a corrupt fixture under your name.

## 4. Reinstall at the pin on every machine that authors or reviews, then regenerate the adapters

```console
$ git -C "$loupe_dir" fetch --quiet origin && git -C "$loupe_dir" checkout --quiet "$LOUPE_COMMIT"   # form A
$ uv tool install --force 'git+<repo>@<commit>'                                                      # form B
$ loupe --version
$ loupe render-adapters --install
$ loupe render-adapters --check-install
```

**In most repositories there is nothing here to regenerate in the tree,
and that is the normal case.** The adapters are installed
machine-globally — `~/.claude/skills/loupe/`, `~/.codex/skills/loupe/` —
and the repository holds no rendered copy of them. For such a repository
this step is entirely the three commands above, run on each machine, and
the working tree does not change at all: do not go hunting for files that
do not exist. Tell which kind you are before you look:

```console
$ git grep -n -e 'BEGIN GENERATED: loupe adapter' -e 'END GENERATED: loupe adapter'
$ git grep -n 'GENERATED by `loupe render-adapters`'
$ git ls-files | grep -i -e 'skills/loupe' -e 'instructions-block' -e adapters
```

**Search by what the rendered text says about itself, not by where it
might live.** Every adapter this tool renders is self-identifying: the
instruction block opens with `<!-- BEGIN GENERATED: loupe adapter …` and
closes with `<!-- END GENERATED: loupe adapter -->`, and each skill file
carries a line beginning `GENERATED by` that names `loupe
render-adapters` as what wrote it — the second command is that line,
quoted with the backticks the file itself carries around the command.
The first two find those markers wherever they sit. The third is a
**hint only**: it finds a vendored `adapters/` directory or a tracked
`skills/loupe/`, and it misses the ordinary case outright, because the
block's ordinary destination is a region inside `AGENTS.md` or
`CLAUDE.md` — filenames that match nothing in it. A repository that
asked only the third question would read its own empty output as
"nothing to regenerate" while carrying a stale copy of the procedure its
agents load into every session.

**Empty output from all three searches is what "nothing tracked to
regenerate" means**, and then the machine-level install is the whole of
step 4. Any hit means your repository embeds a rendered copy — an
instruction block pasted into a tracked file, a vendored skill — and
that copy is now the stale one: regenerate it from the new install and
commit it, in the same commit discipline as step 2.

**The one-glance staleness check, before any of that.** The rendered
block names the version it came from — `Tool version X` in the
instruction block, `at tool version X` in a skill — so the embedded copy
says out loud whether it predates the pin:

```console
$ git grep -n -e 'Tool version' -e 'at tool version'
$ loupe --version
```

Two different numbers is the answer. The same number is not proof: a
re-render at the same version can still differ, which is what the diff
below is for.

### Regenerating an embedded block

The tool renders into a directory; it has no verb that rewrites a region
inside one of your files, and none that checks one. So render beside your
tree and replace the region between the markers yourself — everything
from the `BEGIN GENERATED` line through the `END GENERATED` line,
inclusive, and nothing outside it:

```console
$ tmp="$(mktemp -d)"
$ loupe render-adapters --dir "$tmp"
$ sed -n '/BEGIN GENERATED: loupe adapter/,/END GENERATED: loupe adapter/p' AGENTS.md > "$tmp/embedded.md"
$ diff -u "$tmp/embedded.md" "$tmp/instructions-block.md"
```

`render-adapters --dir` writes all three kinds there —
`instructions-block.md`, `claude/SKILL.md`, `codex/SKILL.md` — from the
installation it is run from, so run it on a machine already reinstalled
at the pin. The `diff` is the check: **no output is the proof the
embedded region equals the current rendering**, and any output is the
patch you have not applied yet. Run the same pair against `CLAUDE.md`,
and against each vendored skill with its own rendered file, one per
embedded copy. `render-adapters --check` does not cover any of this: it
compares whole rendered files under a directory, and an embedded region
is not one of them.

**Check:** `loupe --version` prints the target on every machine, and
`--check-install` exits 0 on every machine. Under form B the packaged
wheel carries no `adapters/` sibling, so `--check-install` refuses with a
remedy naming `--dir`: point it at rendered adapters from a clone of the
same pinned commit, or keep a clone for this purpose. A refusal here is
the install shape telling you the truth, not a failure to work around.
Where you do track a rendered copy, the check is that regenerating it
leaves no diff.

**Skipped — and this is the step the page exists to name.** Two measured
consequences, neither of which announces itself:

- **The adapters regenerate from whatever is installed, never from the
  pin.** `render-adapters --install` reads the package it is run from. So
  a machine still carrying the old install writes the old adapter text,
  and one repository ends up governed by two different agent
  instructions depending on whose machine the session runs on. Nothing
  compares them across machines; `--check-install` only compares one
  machine against its own install.
- **A cloud environment is rebuilt by the provisioner; a person's
  machine is not.** The environment that is created fresh for every
  session picks up step 2's new pin on its next build, with no further
  action. A laptop keeps whatever was installed on it the day it was
  installed, and keeps it silently, and `loupe --version` is the only
  thing that will say so.

## 5. Re-read the `decide` list against the new reader

```console
$ loupe decide
```

The `decide` list is one entry per key this repository never declared,
each naming the key, its meaning, the value applied in its absence, and
the exact TOML line that sets it and — where there is one — unsets it.
Read it with the read-only verb above, before and after the upgrade if
you can, and compare the two lists. Do **not** reach for a verb that
emits or records just to see it: `handoff`, `take`, `emit-request` and an
open round's `brief` all carry the list, and every one of them changes
the record. `loupe ledger report` changes nothing, but carries no
`decide` list at all — it reports breakers and metrics, which is a
different question.

Two things the comparison shows, and they are the point of running it:

- **What the new reader now honours**, which leaves the list. The
  measured case is a key answered in writing: a `# decided:` line
  honoured from 0.17.0 stops its entry, so the entry that was there
  before the upgrade is simply gone after it.
- **What it newly asks.** A release that adds a key adds an entry for it
  to every repository that has not declared it. An installation pinned
  below 0.16.0 has never seen a `decide` list at all, so on that upgrade
  *every* entry is new.

Answer each entry by writing the printed `set` or `unset` line into
`review.toml` and committing it. **The agent asks the operator and never
decides**: one question per key, once, in plain language, and a key the
operator does not answer stays undeclared and keeps its entry.

**Check:** every key you answered in writing produces no entry, and any
entry you do see names a key that is genuinely undeclared here and has
been put to the operator.

**Skipped:** a reader that cannot read a newer marker keeps asking about
a key the repository already answered in writing. The measured case is
`[limits] token_budget`, whose only off state is to stay undeclared: from
0.17.0 the comment line `# decided: limits.token_budget undeclared`,
written under `[limits]`, is honoured — the entry stops, the key stays
undeclared and uncounted. An older reader ignores the comment and asks
again, every session, about a decision that is written down in the file
it just read. Moving the pin forward is what ends that question; leaving
one machine behind reintroduces it on that machine alone.

## 6. Re-run onboarding §8 with the new adapter text, and put the relay choice to the operator

```console
$ grep -n 'agent-relayed window' ~/.claude/skills/loupe/SKILL.md ~/.codex/skills/loupe/SKILL.md
```

Onboarding §8 makes the repository's agent instruction files true. Run
it again, because step 4 changed the adapter those files must not
contradict — a rule that agreed with the old adapter can disagree with
the new one, and the compatibility row is where that surfaces.

One thing in the newer adapters needs a decision rather than a repair.

**What is true today, stated plainly:**

- **The tool never invokes a reviewer.** No model runs inside it, and
  there is no verb that starts the other side of a round. Whichever side
  an agent holds, it stops where the procedure says stop.
- **A window is not a loupe setting.** There is no key for it and none
  is planned. It exists only where the operator's own standing
  instructions declare it by name in the repository's instruction file,
  and the operator grants one per lineage.
- **The harness that would carry the relay block is outside this tool
  and is not published with it.** An adopter who has no such harness
  keeps the human relay; that is not a degraded state, it is the
  workflow this tool was built for.
- **Read your own installed adapter before deciding anything.** The
  command above finds the exception clause if your adapter carries it.
  If it prints nothing, your reviewer's instructions say you do not
  invoke the other side by any mechanism, full stop — and a window
  declared against that adapter hands your reviewer a text forbidding
  the take it is being asked to perform.

**The choice, with both answers written out.** This page does not make
it. Ask the operator which one this repository takes, and write the text
they choose into the repository's instruction file.

*Answer A — the relay stays a person's.*

> Review rounds in this repository are relayed by a person. No agent
> invokes the other side of a round by any mechanism — CLI, subagent,
> hook or API — and this repository declares no agent-relayed review
> window.

*Answer B — this repository declares an agent-relayed review window.*

> Agent-relayed review windows are declared here by name. A window is the
> operator's decision, granted per lineage, bounded in rounds, and
> recorded by the harness that carries it. Inside a granted window that
> harness hands the open request's relay block to the reviewer whole and
> unedited, and the request's Roles line names the harness as `relay=`
> instead of a person. No agent grants or extends a window, and no agent
> edits what the reviewer is handed. The author's side never writes,
> edits or substitutes the reviewer's verdict; the stamped reviewer
> writes and validates its own verdict, exactly as the adapter's
> procedure requires, and stops there. Outside a granted window the relay
> is a person's.

Answer B is only true if all three of its preconditions hold: the
installed adapter carries the exception clause, a harness exists on the
machine that will run it, and the operator grants each window. Where one
is missing, B is a sentence the repository cannot honour, which is worse
than A.

**Where you write it counts as much as which answer you pick.** The reader
that looks for B is deliberately fail-closed, and it refuses far more
contexts than it accepts. Write the sentence as ordinary prose, opening a
paragraph, a list item or a heading, indented fewer than four columns.
It declares NOTHING inside a fenced block, an indented block, a block
quote, an HTML comment (judged before a preformatted opener, so a tag named
inside a comment opens nothing; a line that carries a comment boundary and a
raw tag outside it, in either order, is an unresolved context and refuses
the whole file, inside a generic HTML block as at the top level; a comment
or a preformatted container opened inside such a block keeps its exclusion
past the blank line that ends the block), a pre, code, script, style,
textarea or xmp
container, or a generic HTML block such as a div — and those last two
extend to the end of their container, which for a fence means the end of
the file and for an HTML block means the next blank line, so an unclosed
example ABOVE the sentence takes the sentence with it. A fence closes only
in the container it was opened in, and its closer is judged against THAT
line's own container content origin rather than against the opener's
indentation: zero to three columns in at the top level, however far the
opener was indented; under the same quote markers inside a block quote and
at most three columns past them; at the item's own indentation inside a
list item. A run of fence characters standing anywhere else is that
fence's content and closes nothing. A pre, code, script, style, textarea
or xmp container ends only on a COMPLETE end tag naming it: another name,
a longer name and an unfinished tag all close nothing, and the container
runs on to the end of the file. Paste ONE answer rather
than this page: the phrase occurring a second time in a form the reader
cannot classify refuses the file outright, with the conflicting line
numbers. Where the reader cannot tell, it refuses and prints the whole
enforced domain with the refusal — read that text rather than guessing at
Markdown.

**Every prohibition in B names the side it binds, and that is not
tidiness.** A repository's own instruction file outranks the generic
adapter for the agents reading both, so a prohibition written for every
agent lands on the reviewer as well — and "no agent writes a verdict"
forbids the one output the reviewer procedure exists to produce, in the
very text an operator is told to install. Keep the two apart: the author
never manufactures the other side's output, and the stamped reviewer
writes its verdict and validates it with `validate --from-target`, which
is its own step and nobody else's.

**Check:** the instruction file states one of the two answers, in those
words, and `grep` finds it. Absent an answer, nothing is written and
nothing changes: a repository that has declared no window has none.

**Skipped:** either the operator never learns the choice exists, or — the
expensive shape — a window is declared against an adapter that forbids
it, and the first round inside that window is handed to a reviewer whose
own instructions tell it to refuse.

## 7. Run one round

```console
$ loupe handoff --claim-file <your claim file>
```

then carry the relay to the reviewer as this repository does.

**Check:** the request's own face reports the installation that produced
it. From 0.22.0 the `Env:` line says which interpreter ran, whether it is
inside the declared range, and whether the `loupe` on PATH is this
installation; the `Swept:` line names what the hand-off commit swept.
Read both on the emitted request, and read the gate table the reviewer
sees. The round ends with the new reader's attestation on the record.

**Skipped:** the upgrade stays a claim nobody attested. Every step above
is reversible until a round binds to it, and the first round after a
re-pin is where a wrong floor, a stale adapter or a missing install
actually surfaces — to the reviewer, mid-round, rather than to you.

## Upgrading across these versions

Drawn from the release notes' own upgrader sections. Read the CHANGELOG
entry itself for anything you are crossing; this is the index, not a
substitute.

| Crossing | What the release note tells an upgrader | What ignoring it costs |
|---|---|---|
| 0.16.0 | Where the `decide` list begins: a built-in default applied for a key the repository never declared is reported instead of applied in silence, one entry per key with its meaning, the value applied and the exact TOML line that sets and unsets it. Six keys are covered at that version. `[roles] review_default` and `[roles] enforcement` arrive here too, and both are a cross-installation contract: a repository that declares one becomes unreadable to any installation older than 0.16.0. An unknown section or key now also says a version skew is possible rather than blaming the file. | An installation pinned below 0.16.0 has never seen a `decide` list, so on this crossing every entry is new to it and step 5 is a long sitting, not a glance. Declare the two new keys only once the readers that parse them are in place, and say so with `[tool] requires`. |
| 0.17.0 | A key whose only off state is absence can be decided in writing: `# decided: limits.token_budget undeclared`, written under `[limits]`, stops the entry while the key stays undeclared and uncounted. Nothing migrates. | An older reader ignores the comment and keeps asking about a decision the repository already made in writing. The fix is the pin, on every machine. |
| 0.20.0 | Two kinds of note. The `lineage` attribute on the request wrapper needs no coordination — an older reader tolerates it, an older envelope carries none, nothing migrates and no command rewrites older rows. But `attested_by = "ci"` on a gate row and `[limits] ci_timeout` are new keys, unreadable to earlier installations, which refuse naming the version skew; adopt them once both sides of your loop parse them. | Declaring a CI-attested gate before the other side can read it makes the target refuse on the reviewer's machine, which is the wrong end of the round to discover it. Raise `[tool] requires` with the keys, not after them. |
| 0.22.0 | The hand-off preflight runs ahead of the commit. `scope_paths` is a new optional claim member — an exact path, a directory prefix ending in `/`, or a glob; `[]` is a declaration that this round sweeps nothing, and a claim that omits it sweeps as before. A swept file carrying the marker line `loupe-fixture:` followed by `corrupt-by-design` is refused outright, with no flag that overrides it. Two header lines, `Swept:` and `Env:`, which older readers ignore; `Env:` also reports whether the process ran as root, which is worth having where tests behave differently under root in a container. An interpreter below the floor now gets a sentence instead of a stack trace. **The one crossing in this span that asks your own tree to change — see step 3a.** | A repository whose suite writes fixtures into its real tree owes either that marker in the fixtures or a declared `scope_paths` on the claim. Owing neither, a hand-off run after an interrupted suite publishes a corrupt-by-design fixture under the author's envelope. |
| 0.23.0 | Reporting and reading only; nothing a round binds changes. A `reclassified` closure may carry a `Residue:` continuation line naming the findings its narrowed claim went into, so convergence follows the thread instead of reading it as new. Generated paths get their own heading in the request's span — opt in by marking them `linguist-generated` in `.gitattributes` and committing it; a repository that marks nothing gets the span it always got, byte for byte. Upgraders: nothing to do, and no reader floor has to move. | Little, directly — a verdict carrying `Residue:` validates on 0.22.0 and on 0.16.0. The one exception: `import-legacy`'s closed event grammar on an older installation refuses a ledger row carrying the `residue` member. |

## What this page does not do

- It does not move anything for you, and no part of the tool does.
- It does not cover downgrades. The steps are not symmetric: a
  configuration written for a newer reader is refused by an older one by
  design, so moving a pin backwards starts by removing the keys that
  need the newer floor.
- It does not decide the relay question in step 6, and an agent running
  this procedure must not decide it either.

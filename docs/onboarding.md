# Onboarding a repository

Run this once per repository, **before the first review round**. It ends
with a committed `review.toml`, a gate manifest the tool has actually run,
and an answer to the question most onboardings skip: who approves the
reviewed work.

The ordering is load-bearing, not tidy. Where a repository received a
`review.toml` and a review round with this pass never run, the first
round's findings were exactly what the skipped steps exist to prevent: a
blocking gate that reported green regardless of what it guarded, meta-file
changes on a branch the repository forbids, and documentation asserting a
merge gate the live settings did not enforce. A round spent rediscovering
your own configuration is the expensive way to run this page.

This page does not restate the review procedure — the rendered adapters
carry that (`loupe render-adapters`, and the installed skills) — nor the
install, which is in the README.

## Before starting

- `loupe --version` succeeds.
- If you have installed the adapters at user level, check them for drift.
  `--check-install` is machine-global — it does not read the repository
  underfoot — so run it from anywhere:

  ```
  loupe render-adapters --check-install
  ```

  Its default source is the `adapters/` directory beside the *installed*
  package, which cwd does not affect. Whether that directory exists depends
  on which install path you used, not on where you run the command: the
  `uvx` wheel packages the `review` module alone, and the README's
  documented minimal vendored layout (`review/` + `bin/loupe`, no
  `adapters/`) is the same shape by design. Either one — or any other
  install whose package root lacks an `adapters/` sibling — refuses with a
  `--dir` remedy naming the rendered adapters to point at explicitly, rather
  than proceeding into a misattributed failure.

If either fails, fix that first; every command below assumes a working
install.

## When the environment is rebuilt each session

The README's install is something you do once on a machine. An environment
created fresh for every session — a cloud development container, a CI
runner, an ephemeral workspace — does not keep it, and "fix that first"
above does not stick there. Provision instead: make acquiring the tool part
of what the repository does when its environment is built.

Skipping this is how a repository ends up carrying the rules for a review it
has no way to run.

**It belongs in the repository's own tracked provisioning script**, not in
per-environment configuration pasted into a web form. Tracked means
versioned, reviewable, and unable to drift from what the environment
actually runs. Pasted means invisible to everyone who did not paste it.

**Pin the tool to an exact commit.** A round binds to a determinate tool —
the identity it records is the tool that ran your gates. Against a floating
ref nobody can say afterwards which build ruled a round, and the attestation
decays into a claim about whatever happened to be current. Report drift
rather than acting on it: compare the pin against the upstream head, print
one line when they differ, and let a person decide. Never auto-upgrade.

**Install it isolated, never into the repository's own environment.** The
distribution and the package it installs are deliberately different names,
and the package name is generic enough to collide with anything else
claiming it in a shared environment. That isolation is why the README's
`uvx` path is the one to reach for here.

**Never fatal.** A repository's own harness must not depend on the review
tool being present. Warn and continue: a missing reviewer is not a broken
checkout.

**The interpreter — the part that surprises people.** loupe declares a
narrow `requires-python`. A current `uv` reads that constraint out of the
pinned ref and provisions a matching interpreter itself, so naming a version
in your script is not merely unnecessary — it is a constant that goes wrong
the day the floor moves. Two things defeat it, and both surface as the same
"no interpreter found" error:

- **An older `uv`.** The behaviour is version-dependent, which is why it
  should not be assumed in either direction. Measured: a recent `uv`
  resolves the constraint from a git source and offers the managed
  download; an older one, in a container, did not.
- **Downloads disabled by policy.** `UV_PYTHON_DOWNLOADS=never` is a
  reasonable setting for a locked-down environment, and it produces an
  identical symptom from an unrelated cause.

Read the error before hardcoding anything: it names the download it would
have made, which separates the two. Prefer a current `uv` and permitted
downloads. If you control neither, naming the floor explicitly is a last
resort — record it beside the pin as a value that must move when the pin
moves, because nothing else will tell you.

## 1. The configuration lives in the tree

Copy `review.toml` from the loupe repository root into **your repository's
root**, commit it, and edit every value — it is the generic example, and
every one of its choices below is yours to remake.

In-tree is not a style choice. A reviewed commit carries the rules it is
judged by: `handoff`, `take` and `validate --from-target` all read
`git show <sha>:review.toml` from the target commit itself, and each
refuses a target that tracks none — rules living on one machine cannot be
shown to a second. For a shared repository this is also simply better: the
governing vocabulary is visible to every operator instead of resident on
one laptop.

A user-level `~/.config/loupe/<repo-id>.toml` still governs every **local**
verb (ledger reads, validation of files you hold) and can serve a
repository you never intend to emit from. It cannot make a repository
reviewable.

## 2. The gate manifest — what the repo can actually attest

`handoff` runs every `[[gates]]` command itself, in the repo root, and
records the exit code bound to the emitted SHA. Nothing is taken on the
author's word. So the manifest must be what the repository can honestly
attest:

- **Inventory first.** If the repo already has one runner for all its
  checks (`make check`, a `bin/` script, the CI entry point), the manifest
  is that one command, blocking — re-listing its members recreates the
  drift the runner exists to end. The price is coarse attribution (every
  finding names the one gate id); accept it knowingly. Otherwise list the
  real checks, each with a stable `id`: ids are what findings name,
  commands are how they run.
- **`blocking = true` honestly.** A blocking gate that exits non-zero makes
  the handoff refuse; a non-blocking one becomes a notice the reviewer
  reads. Do not invent gates the repository does not have.
- **A guard over prose is advisory drift evidence by construction.** No
  lexical check holds a semantic guarantee, so a check over prose reports
  drift and the claims around it narrow to what the check actually owns —
  never the other way. A prose gate whose description promises meaning is
  the first finding a good reviewer files.
- **Know the limits before they surprise you.** A gate is capped at 600
  seconds. And the attestation carries an exit code, so "could not run"
  and "ran and failed" both arrive as non-zero — the distinction lives in
  the retained output under
  `~/.local/state/loupe/<repo-id>/gate-output/<sha>/<id>.log`. There is no
  bypass for a blocking gate that cannot run: it refuses the handoff
  exactly as a failure does. Design the manifest so every blocking gate
  can run wherever you emit from.

**Prove the manifest through the tool, not only by hand.** A command that
passes in your shell can still fail as a subprocess — a different
environment is the classic cause. Two measures, cheap together:

- Run every manifest command by hand once, from a clean tree, as the
  control. Note the durations against the 600 s cap.
- For round 1, add a temporary non-blocking probe gate that asserts the
  subprocess environment is the one your gates need — at minimum:

  ```toml
  [[gates]]
  id = "env-probe"
  command = ["python3", "-c", "import os,sys; sys.exit(1 if 'PYTHONSAFEPATH' in os.environ else 0)"]
  blocking = false
  ```

  Read its attestation in the first emitted request, then delete it. The
  interesting signal is any gate that passes one way and fails the other.

**Environment-dependent gates: prove you need a special form before
choosing one.** If a gate needs a live system (a network service, a device,
credentials), first run the full gate in a fresh `git worktree` carrying
none of your ignored local files and no ambient credentials. If it exits 0
there, it is not environment-dependent in the way that matters, and you are
done. Only if it genuinely cannot run away from its environment do you
choose, deliberately, between:

- *Full form, blocking*: away from the environment the handoff refuses.
  Correct when a handoff is a completion claim and "a live check that
  cannot run is a failure, not a skip" is the repo's own rule.
- *Offline form, blocking*: attests the weaker thing on every handoff,
  including those emitted where the environment was available.
- *Two gates — offline blocking, full non-blocking*: emits when away, but a
  real live failure is also only a notice. Permanently downgrades the live
  checks.

**A fresh worktree does not change who you are, and identity is part of
the environment.** A gate asserting anything about file permissions can pass
as an unprivileged user and fail as root — the classic shape is a test that
makes a path unreadable and expects the read to fail, which root simply
bypasses. The worktree check above will not catch it: it varies the
filesystem and the ambient credentials, not the identity. So run the
manifest as the identity that will actually run it. This bites hardest where
the author is an ephemeral environment that happens to run as root — there
is no bypass for a blocking gate, so an author whose identity reddens one
cannot emit at all, and the defect is in the gate's assumptions rather than
in the work being reviewed.

Whichever you choose, write the reasoning into the config's comments — the
next reader is someone with no memory of this decision.

## 3. Taxonomy

Absent a declared `[taxonomy]` the tool refuses to emit and the reviewer
refuses to rule — a tool that supplies its own vocabulary has authored your
judgment scale. Declare:

- `severities`, and `blocking` — the subset whose findings must carry a
  falsification test and cannot be deferred.
- `classifications`, each with a `[taxonomy.classification_notes]` line,
  each traceable to a failure mode this repository has actually documented
  (its post-mortems, its traps, its verifier rules). A code repo leans
  toward design gaps and internal contradictions; a prose or evidence repo
  toward unverified claims and stale evidence. Keep the count where a
  reviewer can hold it — five to seven.

## 4. Roles

Put the repository's standing direction in `[roles] author` / `reviewer`,
and list everyone the repository permits in `permitted_authors` /
`permitted_reviewers` — the per-invocation `--author`/`--reviewer` flags
select only within those lists. `relay = "user"` states that a person
carries the envelope; the tool cannot observe transport, so keep the value
truthful. A round in the reverse direction is one `--author`/`--reviewer`
pair away; the standing lines are the default, not a cage.

## 5. Limits

`round_cap = 3` unless the repository has a reason. Leave `token_budget`
undeclared unless you have measured something: the tool reports "no budget
declared, the breaker cannot fire", which is honest — a number with no
measurement behind it is not.

## 6. Verify it resolves

From the repo root, `loupe ledger report` must print your gate ids and
round cap, lineage 1 with 0 events, and `tokens.state: no_budget` if you
left the budget undeclared. (`loupe brief` proves nothing here: with no
open request it reports the blocked state whether or not you are
configured.)

## 7. Who approves? Answer before the first round

Onboarding a review tool grants no merge authority. If the target branch is
protected — required approving reviews, required status contexts — then a
repository can end this page correctly onboarded, green on every check it
controls, and unmergeable, because nobody available can approve. That state
was measured, on a real repository, with both human code owners away; no
verdict, however clean, moves it.

So close the question now, not on a blocked PR:

- **Read the live protection**, not the documentation of it — the two
  drift. What does the branch actually require to merge?
- **Verify every requirement is satisfiable** by identities you control or
  can reach, plus the repo's own CI. If approvals are required and the
  answer is a machine account or app you operate, install it now, as a
  deliberate admin action.
- **Write the approval policy into the repository** — who approves what,
  and what a loupe verdict authorizes them to do. A rule that exists only
  in an operator's head is exactly the class of defect the review round
  exists to catch, and it will be found there.

A useful discipline where a machine identity approves on the strength of a
clean verdict: the approval is granted by a person, per approval, on the
exact reviewed SHA — and branch protection's "require approval of the most
recent push" makes GitHub enforce the same SHA discipline the verdict has.
An approval granted automatically on a verdict is a merge gate in all but
name, built on records the platform cannot authenticate; do not build it
casually.

## 8. Make the repository's agent instructions true

Do this before the first round, not after it. An agent instruction file —
`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, whatever the agents working here
load — is read into **every** session, which makes a wrong one worse than
none: an agent acts on it without checking, and the error repeats silently
until a human happens to notice.

Onboarding has just introduced roles, gates and a merge path. Make these
true in every such file:

- **which agent holds which role here** — or better, point at the
  configuration that declares it, so the two cannot drift apart;
- **the branch and merge discipline the repository actually enforces**;
- **every claim that something is enforced.** This is the expensive one.
  Remove or correct any statement that a gate, check, review or approval
  is enforced when the live configuration does not enforce it. The
  opening of this page describes the case: three separate documents each
  asserted a merge gate the live ruleset did not enforce, and the first
  round found all three. Check each claim against the live settings.
  Where a claim is aspirational, say so in those words — an instruction
  file that reads as a safety net and is not one is the worst outcome
  available here.

Before repairing, notice what there is to repair. **State which
instruction files this session actually loaded** — the repository's own
(`AGENTS.md`, `CLAUDE.md`, a rules file) and any user-level or
machine-global file your agent surface reads (for Claude Code
`~/.claude/CLAUDE.md`, for Codex `~/.codex/AGENTS.md`; name your
surface's equivalent). Declare it from the session — do not probe another
machine's filesystem, and absence on this machine is not absence
everywhere: a collaborator's agents may load rules yours do not.

Three topologies, three different answers:

- **The repository carries an instruction file** — with or without
  machine-global rules loaded beside it. The truth pass above is the
  whole step: make what the repository states true.
- **No repository instruction file, but the session loaded user-level or
  machine-global rules.** Do not send repository facts there: a global
  file does not travel with the clone, a collaborator or a different
  agent surface never sees it, and project policy would pollute one
  person's private defaults. Propose — as a diff the owner agrees to,
  like every change on this page — a minimal repository instruction file
  carrying the operating floor below. Global rules may keep covering
  only what is genuinely generic (tone, language, personal style);
  everything onboarding just created — roles, gates, who approves, the
  merge path — belongs to the repository file.
- **Nothing is loaded anywhere.** The agents working here run on no
  contract at all, and onboarding has just created obligations nothing
  states. Propose the same minimal repository instruction file; until
  the owner grows it, the floor is the whole contract.

The **operating floor**, for both repository-absent branches, and no
more:

1. generated files are regenerated, never hand-edited — name each
   generated artifact and the command that regenerates it;
2. a human sets a review round in motion; agents follow the installed
   loupe adapter and stop where it says stop;
3. who approves reviewed work, by name — §7's answer, written where
   the agents will actually read it;
4. work happens on the agreed branch in logical-unit commits; nothing
   is pushed except through the tool's own verbs (`handoff` commits
   and pushes the reviewed branch) or on the owner's explicit request.

The floor is deliberately not a style guide: no language rules, no
role assignments between named agents, no delegation policy — those
are the owner's to add or not.

While you are in these files, it is worth proposing — not applying
unilaterally — a narrower pass against the criteria that make an
instruction file cheap to load and hard to get wrong: routing rather than
restating; one fact, one home, because a fact stated twice drifts and
nothing detects the stale copy; pointing at the artefact that owns a
procedure instead of duplicating it; naming what is generated and must
never be hand-edited; and cutting what git history already carries.

This is not licence to rewrite the repository's agent contract wholesale
or to import conventions from elsewhere. Every change should trace to
something onboarding made false, or to one of those criteria. Propose a
diff, say what makes each change true, and get agreement — an
instruction file encodes decisions you were not present for.

## 9. The first round

The pass is a precondition of the round, not a parallel activity — and it
ends here. **A human sets the round in motion**; the adapters carry both
sides' procedure. Preconditions worth stating to whoever starts it:

- `handoff` commits outstanding tracked work and pushes the reviewed
  branch. Never run it in a checkout another agent or person is using, or
  on someone else's branch: it would publish their half-done work under
  your envelope.
- A supplied claim must hand the reviewer at least one `references`
  entry — the validator refuses a claim without one, an empty list
  included, and its refusals name every required member. What could not
  be attested goes in `evidence_not_captured`, in words.
- One note for the first reviewer, learned the expensive way: anchor every
  falsification test in the reviewed tree wherever the defect admits it. A
  test anchored in a mutable artifact outside the tree — a PR body, an
  issue tracker, a dashboard — becomes unexecutable the moment that
  artifact moves, and an honest `cannot_execute` on a blocking finding
  fires a breaker that only a human decision clears. Where the defect
  genuinely lives outside the tree, say so in the finding, so the
  dependency is on the record before it bites.

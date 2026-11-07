<!-- BEGIN GENERATED: loupe adapter — do not edit; `loupe render-adapters` regenerates, `--check` guards -->
# loupe — review procedure (instruction block, GENERATED)

For a project instruction file (AGENTS.md / CLAUDE.md region) on a surface that reads no skill directory. Tool version 0.34.0.

## What this is

`loupe` runs the mechanical half of a review: request, verdict and disposition envelopes bound to SHAs, gates it runs and attests itself, an append-only ledger with breakers. No model runs inside it. The envelope's stamp (`author=… reviewer=…`) says which side you hold, per envelope.

A reviewed commit must carry `review.toml`; `handoff` and `take` refuse one that doesn't. A user-level `~/.config/loupe/<repo-id>.toml` still governs every local verb, so acting locally needs no in-tree file.

## Rules that hold on both sides

- **Results.** Off a TTY, output is JSON: read its fields. Exit 0 is ok, 1 is findings or a refusal, 2 is usage. Every non-zero exit carries `next_kind`. `command`: run `next` verbatim. `blocked`: read `remedy`. If it is yours to do, because it names your side or a file or commit you wrote, do it and re-run. Otherwise give the human the remedy and wait. Add no flag or fix those fields don't name.
- **The record.** The tool writes envelopes, the ledger and gate attestations, so don't hand-type an envelope, edit the ledger or paste gate output into Evidence.
- **Ending a session.** With `[roles] review_default = "on"` (absent counts as on), the author runs `handoff` unasked once the work is done, unless the user declined a review this session or said it needs none; then say so in one line and stop. A decline changes no config. `handoff` is the author's to run, never the human's, and nothing is emitted until it exits 0.
- **The relay is the human's.** Stop where your side's procedure says, and don't invoke the other side by any mechanism (CLI, subagent, hook, API). One exception, never this tool's to grant: the standing instructions you operate under allow an agent-relayed window AND the operator granted one for this lineage. Then a harness outside this tool carries the relay block to the reviewer, whole and unedited, and the request's Roles line names that harness as `relay=` instead of a person, which the author declares by setting the claim's `relay` member to the harness's name. Nothing else changes: the author never writes or edits what the reviewer is handed, and the reviewer takes that block as one a person pasted and stops at the validated verdict.
- **Two verbs are never yours unless the standing instructions you operate under say otherwise.** `loupe waive` and `loupe authorize-advance` record a human's decision that a commit goes unreviewed, that a finding stands unfixed, or that the lineage advances over open findings. The tool carries whatever name you type, so running either on your own mints the authorization this checkpoint exists to require. Where those instructions allow it, run either only to record exactly what the human selected in an interactive prompt this session, under their name. Otherwise report what you would say and let the human answer.
- **Brief, then relay, exactly.** Every request and verdict result carries `brief` and `relay`. Give the human both, in that order, every time. `brief` is readable prose, never in a code block. `relay` is the literal commands with the fences it already has: add no fence around it and put nothing of yours inside it. Don't merge, reorder, summarise or paraphrase either; both derive from the envelope and yours would not. A relay may carry envelope bytes as a heredoc in one fence; hand that over whole, since a chat surface rewrites anything outside a fence. `loupe brief` reprints both, and with no argument finds the open request.
- **The carrier.** The relay prints the one carrier this round declared. The tool resolves it from declarations, so don't detect it, and don't rewrite a command for another carrier.
- **Boundary closure.** When a finding is about a parser, a grammar or a lifecycle seam, the answer partitions the complete admitted domain: every input kind the boundary accepts, every declared field and nested shape, every ordering the seam can be reached by, with paired valid controls proving the check is live. The proof a test can fail is the one run with the defect back; neither side asks for a mutation per guard or per bypass. A green reproducer is one point of that domain; both sides judge an acceptance or a closure by whether the domain is closed. A gap no plausible input reaches demands no partition: the reviewer reports it at Low and says so. A domain that can't be shown complete from inside the artifact has one terminator: `closure-authority.md`.
- **Trust model.** The tool has one principal, the operator, and every agent runs its verbs for them. The ledger, state directory, clone and remote are the operator's own writable state, so forging a row, ref, source file or envelope with that access is outside the taxonomy: it describes the owner attacking their own record. The record guards against omission and fatigue: a finding that dies unanswered, an answer bound to the wrong ruling, a stale attestation, a stamp that misdescribes who did what. Rule on those. A forgery finding names the party with less than operator access who could mount it; one that names none is not a finding for the round, and the author answers it by citing this rule, not by building a mechanism. Boundary closure partitions the mistakes a boundary admits, not adversaries.

## If you hold the author stamp

A round is done on your side when `handoff` exits 0 and the human has its `brief` and `relay`. A refusal is not that stop: follow `next_kind`, as the both-sides rules say.

1. **write the claim**
   A JSON file holding your judgment; the tool carries it and never invents it. Its grammar is closed: every defect is refused by member path before Git or a gate runs. Supplying no claim is legal and recorded as its own state.
   Members: `access_note`, `attestation_map` (a list of objects: `fingerprint`, `gate`; optional `test`), `carried_findings` (a list of objects: `fingerprint`, `origin`, `outcome`; optional `required`, `fix`; `outcome` is one of `fixed`, `deferred`, `superseded`), `commit_subject`, `contract`, `deliberately_not`, `evidence_not_captured`, `excluded_paths`, `hand_back`, `objective`, `objectives` (a list of objects: `title`, `paths`; optional `tests`, `references`, `authority`, `covers`; `authority` and `covers` are declared together), `observations` (a list of objects: `command`, `result`, `context`), `redesign_answer`, `references`, `relay`, `review_scope`, `risk`, `scope_paths`, `stop_conditions`.
   Required: `objective`, `references` (non-empty). `references` is a list of objects carrying a non-empty `path` (the reviewer must read it; the tool digests it) and optionally `note`, `required`; `scope_paths`, `excluded_paths`, `deliberately_not`, `evidence_not_captured`, `stop_conditions`, `hand_back`, `contract` are lists of strings; `carried_findings`, `objectives`, `observations`, `attestation_map` are non-empty lists of objects; the rest are strings.
   Values — `scope_paths`, `excluded_paths`, `paths`: a file, a directory written `dir/` (a bare `dir` reads the same) or a glob; `origin`: `<lineage>/<round>`; `fix`: a list of 40-hex commit ids; `authority`: one repository path; `tests`, `objectives[].references`, `covers`: a list of strings; strings in an object: non-empty.
2. **hand off** — `loupe handoff --claim-file <claim.json> [--base <sha>]`
   Commits outstanding tracked work, runs the local gates at that commit, pushes the reviewed branch to its own name, awaits any CI-attested gates, emits, validates and records the request, and prints the reviewer's command. `--base` is needed only for round 1 of a lineage. Re-running on an unchanged tip returns the same envelope.
   Under `[roles] enforcement = "pr-approval"`, the work must be on a non-default branch with an open pull request before this step; open it with the forge's CLI, since the tool touches no forge.
   `--debug`/`--no-debug`, else `[roles] debug = true`, else off, decides whether the reviewer also critiques the tool in a `## tool feedback` section: advisory, never a gate.
   Then stop, with the `brief` and `relay`.
3. **close the round** — `loupe close --verdict <verdict.md>`
   Validates and records the verdict. `changes requested`: the lineage stays open and the next command is `respond`. `clean to advance`: the lineage is closed at the exact reviewed SHA. Merge authority is the account allowlist and the standing rules you operate under, not this tool.
   When round 3 of a lineage comes back `changes requested`, stop before `respond` and answer "should we redesign?" for the user: read the lineage's findings, say why the reviewer keeps coming back, whether any are lexical or perfection with no real consequence, and whether the design is the problem, with a recommendation. A fourth round waits for their answer.
4. **respond** — `loupe respond --verdict <verdict.md> --from-json <d.json> --out <disposition.md>`
   Exactly one disposition per finding — accepted / refuted / deferred / preference / escalated — each with its mandatory payload; a blocking finding cannot take `deferred` or `preference`. Write `<d.json>` in the shape `loupe respond --help` prints; its payload text members are strings, an escalation's positions included.
   An `accepted` disposition follows this order, and only this order: (1) make the fix; (2) run the finding's named falsification test on the fixed head, which records `payload.falsification.status` (pass | fail | cannot_execute); (3) run the mutation, the same test with the defect reintroduced, and restore, which records `.mutation` (fails_without_fix | passes_without_fix | not_run), the proof the test can fail at all; (4) write the disposition record with `--out`; (5) hand off again.
   A fix proposed but not made is `deferred` or `escalated`, never `accepted`, and a blocking finding cannot take `deferred`.
5. **close a lineage by decision** — `loupe close --lineage --reason "<why>" --by <who decided>`
   Only when the human decides a review is over without a clean verdict. Recorded with the reason; the next handoff opens round 1 with the repository's default cap.

## If you hold the reviewer stamp

You are done when `validate` exits 0 and the human has the `brief` and the `relay`. Until then, keep going.

1. **take** — `loupe take <request.md> --as <your id>`
   `--as` names you. The tool can't observe who is running it, so it records what you declare in an append-only record: declare only your own identity. `take` validates the request, checks you are the stamped reviewer, fetches the target, labels every reference checked / mismatch / unavailable, records the take, and prints the request with the exact diff command. A defective or unreachable envelope isn't reviewed: return a refusal that isn't yours to fix to the human.
   Three lines to read in what it prints. `head:` reading NOT the target means your working tree isn't the reviewed bytes, so read through the diff command it prints. `Swept:` naming a path outside the claim's `scope_paths`, or an intent-to-add entry, is work the author may not have inspected, so read it first. The attestation table condenses each object; the `kept` path (or `--full`) holds the exact bytes, for a finding that turns on a field the table doesn't carry.
   `--compact` returns pointers instead of the request, for a reviewer that reads the kept file itself.
2. **rule**
   Read the full diff first, with the printed command, before you weigh the author's account. `scope_paths` and a `Scoped:` diff say where the author worked and never limit your search.
   The author's objective, risk, not-done list and observations are claims to test.
   Report everything you find rather than filtering, and rank by consequence: each finding's Why names the real input or situation a user or agent would meet and what goes wrong for them. Severity orders the findings and is no bar to reporting one. Mark a low-confidence finding `unconfirmed` in its Why. Claim a finding as blocking only when you reproduced it or can show it in the reviewed bytes; report the rest as unconfirmed, at a severity that doesn't block.
   A boundary's domain gap with no plausible input goes at Low, with that said. A Low doesn't by itself ask for changes; `validate` decides the verdict string a round of only Lows must carry.
   What the trust model or an accepted authority puts outside the round (a forgery naming no lesser party, a demand outside an accepted domain) stays out of `## findings`: name it in a line under `## evidence checked`.
   Write the verdict in the request's own `Verdict shape` and classify each finding by its `## Taxonomy`; the validator holds both.
   A FALSIFICATION names one test and asks for no mutation table. Anchor each FALSIFICATION test in the reviewed tree wherever the defect admits it. Where the defect lives in a mutable artifact outside the tree (a PR body, an issue, a dashboard), say so in the finding, so a later `cannot_execute` reads as the outcome of a stated dependency and the human deciding the `unverifiable` breaker has your words that it was known.
   A request stamped `debug="tool-feedback"` also asks for `tool-feedback.md`, beside this procedure; you may volunteer that section on any round.
3. **validate, then stop** — `loupe validate <verdict.md> --from-target`
   `--from-target` judges the verdict against the target commit's own `review.toml`, the bytes `take` read. Without it the verdict is judged against your checkout, which on a detached worktree or a second machine is often no config at all. A refusal of the verdict's content is yours: correct it and re-run until it exits 0.
   Then stop, with the `brief` and `relay`. The relay, under `## Verdict`, is the author's to run: on a paste round one fence with the verdict bytes as the command's stdin, on a path round with the kept file's path. You don't start the next round, send it back for fix-and-resubmit, or decide whether it enters the record.
   Where the standing rules you operate under grant a clean-verdict approval: after a validated `clean to advance` on a branch with an open pull request, that grant covers recording the approving review through the project's approval tooling if the operator's credentials for it are on this machine. If they are absent, say so once and point at the project's approval setup. The relay comes first and is never blocked on it.

## Read only when it applies

- `decide.md`: a result carries a non-empty `decide` list, or someone asks what this repository leaves undeclared.
- `carrier.md`: someone disputes the round's transport, or a relay's carrier looks wrong for where the other side runs.
- `closure-authority.md`: a claim's objective declares `authority` and `covers`, or you are about to declare or narrow one.

## `decide.md`: absent config asks once

Absent config asks once. A result carrying a `decide` list has applied a built-in default for a key this repository never declared. Each entry names the key, its meaning, the value applied, and the exact TOML line for `set` and for `unset`.

Ask the user once per session, per key, in plain language, whether this project sets or unsets it; write their answer into `review.toml` as the printed line and commit it. Ask only about undeclared keys, and leave the decision to them.

A key whose only off state is to stay undeclared prints its `unset` as a comment line, `# decided: limits.token_budget undeclared`. That line is the answer: write it into `review.toml` under `[limits]` and the entry stops, while the key stays undeclared and uncounted.

`loupe decide` prints the same list and is the only way to read it without emitting or recording anything. Use it when nobody asked for a round, such as after a version pin moves, instead of running an emitting verb to find out.

## `carrier.md`: correcting a round's carrier

A round declares whether the two sides share a filesystem, and that decides which carrier the relay prints. The tool resolves it from declarations (`loupe handoff --help` lists their order); with none, a new emission carries `path`. A locally readable request file proves nothing about what the other side can open.

A human corrects a wrong stamp by an explicit statement about this round's topology, or an exact command carrying `--transport path|paste|git`: to `handoff` if you are the author, to `take` if you are the reviewer. A conversational question isn't a declaration, so ask rather than turn one into an override. A `path` round whose commands you rewrite for a paste is a round whose record no longer describes it.

## `closure-authority.md`: closing a domain relative to an authority

A domain whose completeness can't be established from inside the artifact, a set that can be declared but never proven complete from within, may be declared closed relative to a stated authority. It is the only terminator. An authority is a machine-generated, separately gated artifact that derives the set; a hand-maintained list never is. The declaration is no exemption: it moves the attackable surface from an unbounded hunt onto a finite artifact, and an authority narrower than its own stated domain is itself the defect.

**Author.** Declare it in the claim, per anchor (`objectives[].authority`, `covers`), and state its domain: what it is claimed to cover, in the terms it enforces.

**Reviewer.** Rule on the domain before ruling under it: does it match the anchor's declared purpose and every surface still relied on downstream? A concrete escape outside the declaration is admissible evidence that the domain is too narrow, and a domain narrower than its anchor's purpose is a finding about the claim. Once a stated domain is accepted, rule coverage against it; a demand outside it then goes to a backlog, however well it reproduces.

**Attacking it.** Attack the authority's derivation and coverage. You may still bring an instance, naming the authority it escapes; one that names none is not a completeness finding. Rejecting the authority is an ordinary finding anchored on the authority itself, and it is bounded, because unbounded it restates one level down forever.

**Narrowing.** A narrowing is a complete answer only when the anchor's purpose and every consumer claim narrow with it, leaving no wider reliance implicit; one that leaves a wider promise standing is the defect. Where it is complete it is preferred to widening the mechanism.

## `tool-feedback.md`: a debug round's tool feedback

Add a `## tool feedback` section after `## evidence checked`: a self-critical account of how the tool could have served this round better, under three headings. Efficiency: steps, reads or re-derivations the procedure wasted. Cost: bytes, tokens and wall time you observed, against what the round needed. Accuracy: anywhere an envelope, a gate result, a brief or a refusal misled you or made you check something twice.

Critique the tool, never the diff. Findings stay findings, and nothing here softens or substitutes for one. The section is advisory prose, recorded in the author's ledger at close.

## Verbs (from the CLI itself)

`loupe validate`, `loupe fingerprint`, `loupe respond`, `loupe ledger`, `loupe import-legacy`, `loupe migrate-state`, `loupe emit-request`, `loupe handoff`, `loupe take`, `loupe brief`, `loupe decide`, `loupe waive`, `loupe authorize-advance`, `loupe close`, `loupe prune`, `loupe render-adapters`. `loupe <verb> --help` has each.

<!-- END GENERATED: loupe adapter -->

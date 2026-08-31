"""Deterministic, agent-neutral review tool — `loupe`.

Design: docs/design.md in the published repository (derived from the
workbench design record). Stdlib only, no network
I/O in the tool's own code, no LLM in any code path (§6.1); git performs the
one publish step (§9bis.4).

TOOL_NAME is the single name authority. Renamed `rvw` → `loupe` 2026-08-14
(§10.1, RVW-T6) as a migration, not a substitution: the wire dialect (wrapper
tag and attestation fence) derives from config's wrapper tag, whose default is
this constant; state and config paths derive from it; and the literals that
§10.1 found hardcoded in emit.py and validate.py were removed rather than
renamed, so the emitter and validator can no longer drift apart.

FORMER_NAMES is what keeps already-emitted artifacts readable: where a repo
speaks the default dialect, wrapper tags and attestation fences carrying a
former name are accepted with a notice — a recorded state, never a silent
one. State written under a former name is never silently orphaned: config
refuses loudly when it finds a legacy ledger and no current one, and
`migrate-state` moves it with digest verification (the 325-event slice-1
record is exactly what this protects).
"""

from pathlib import Path

TOOL_NAME = "loupe"
# Version policy (decided 2026-08-22): a lineage whose clean close will be
# published bumps this INSIDE the reviewed round, so the version is part of
# the reviewed diff and `--version` discriminates publishes. Two publications
# shipped as 0.3.0 (`13d27b9`, `0a6281d`) because bumping after the clean
# verdict would have been an unreviewed edit — that ambiguity is what this
# policy removes. A same-version pair of publishes is now a defect, not a gap.
#
# 0.6.0 is a DOCUMENTED EXCEPTION to the sentence above, and says so rather
# than pretending to satisfy it. The policy's case is "a lineage whose CLEAN
# close will be published". Lineage 7 did not close clean: it closed by
# recorded decision on 2026-08-25 under a stopping rule agreed before its
# last round, with three findings standing — the workbench reader universe,
# a scanner that does not own the test of its own call domain, and a false
# `adapters/` document exclusion. Those are the next lineage's subject, not
# defects awaiting a patch.
#
# So there was no reviewed round left to carry this bump, and the choice was
# between publishing under a version that no longer discriminates and
# bumping outside a round. Bumping wins, because the ambiguity the policy
# exists to remove is two publishes reporting the same string, and that cost
# was already paid once at 0.3.0. What the exception costs is that this one
# line is unreviewed; what it buys is that `--version` still tells two
# publications apart. The user directed the publication knowing what stands.
#
# 0.7.0 returns to the policy: bumped inside lineage 8's reviewed round,
# which lands the boundary-authority terminator in the shipped contract,
# narrows the identity claim to the declared behavioural set, and closes
# the three findings 0.6.0's README named as standing.
#
# 0.8.0, inside lineage 9's round 4: the verdict relay of a PATH round no
# longer prints an audience comment above its command. Minor rather than
# patch because a relay is bytes an agent reproduces verbatim and a human
# reads, so its shape is interface — and the reviewer adapters, which said
# the block "says so on its own first line", are regenerated with that
# clause gone. The lineage's other work is workbench-only.
#
# 0.9.0 is the SECOND documented exception, the same shape as 0.6.0's and
# recorded for the same reason. Lineage 11 closed by recorded decision on
# 2026-08-28 at round 11, not clean: round 11's finding was parked whole as
# RVW-T17 — the author's authority check PREDICTS the commit it would create
# instead of VERIFYING the one it made — because the answer is a redesign
# and not a patch. So no reviewed round remained to carry this bump, and the
# choice was again between publishing under a version that no longer
# discriminates and bumping outside a round. Six of the eighteen declared
# behavioural artefacts differ from what 0.8.0 published; two publications
# reporting the same string is the exact ambiguity this policy exists to
# remove, and it was already paid for once at 0.3.0.
#
# Minor rather than patch, on behaviour that changed at the doors:
# the external-configuration ORIGIN no longer governs a REVIEWED commit —
# `handoff`, `take` and `validate --from-target` all refuse a target that
# carries no configuration of its own, because an authority living on one
# machine cannot be shown to a second and a review is the act of showing it
# (round 6 F1). A repository governed only by `~/.config/loupe/<id>.toml`
# could hand off under 0.8.0 and cannot under 0.9.0; its local verbs are
# untouched. Alongside it: a gate id is one filename component, closing a
# write outside the ledger directory (round 6 F2); the config schema is
# derived from `DEFAULTS` rather than restated (round 4 F4); a non-regular
# `review.toml` entry is refused by mode rather than admitted by object type,
# which is where a committed symlink split the two ends (round 4 F1); and
# every subprocess failure is typed, so an unusable object store is no longer
# read as evidence that a target carries no configuration (round 3 F2, F3).
#
# 0.10.0 RETURNS TO THE POLICY: bumped inside the reviewed round, because
# this lineage's clean close is what publishes it. Minor, and the reasons
# are interface:
#   - the wire format gains an attribute. Every emitted envelope now carries
#     `shape` beside `tool` (RVW-T18). Older readers ignore an unknown
#     attribute by design — §3.1 says so and means it — so this is additive,
#     but it is the envelope's own face and it is not a patch.
#   - `tool` covers a different set. It is now the 16 package modules alone;
#     `bin/loupe` and `pyproject.toml` moved to the reported-only `shape`.
#     An 0.9.0 envelope and an 0.10.0 envelope from the SAME code stamp
#     different `tool` values, so the first cross-version round after this
#     will read `differs` for a version reason and not a code one. That is
#     the last time the value lies; from here the three advertised install
#     paths agree.
#   - the author-side authority boundary MOVED (RVW-T17). It runs after the
#     commit and before the push, reading `git show <sha>:review.toml`
#     instead of predicting what `commit -a` would record. States that
#     used to refuse now proceed (a declared filter attribute; a
#     skip-worktree entry whose worktree copy is gone — that one was a
#     false refusal), and one state that used to pass now governs
#     differently: with `assume-unchanged` set, the emission is ruled by
#     the committed bytes rather than the worktree's.
#   - what an envelope DECLARES can change for one repository. Taxonomy,
#     gate ids, budget, blocking severities, round cap, roles and wrapper
#     tag now render from the target commit's configuration rather than
#     the emitting checkout's. Identical where those agree, which is the
#     ordinary case; different exactly where the two ends would previously
#     have disagreed without saying so.
#
# 0.11.0 — the RVW-T21 defect fixes from the first late onboarding pass.
# Minor, because child-process and CLI behaviour move:
#   - every git subprocess now runs in the CALLER's environment (D2). The
#     0.10.0 gate-environment fix covered gates only; a repository's git
#     hooks — pre-push, the commit hooks, reference-transaction, a
#     configured fsmonitor — are the repository's own code in the same
#     trust position, and they inherited the shim's PYTHONSAFEPATH /
#     PYTHONPATH hardening. A real pre-push hook refused a real handoff.
#     `config.caller_env` is the one authority; every `_git`-family door
#     passes it; the gate re-entrancy marker still reaches gates only.
#   - `render-adapters --check-install` / `--install` default their source
#     to the installed package's own adapters/ sibling instead of the cwd
#     repository's (D1) — the modes are machine-global and the old default
#     existed only in the tool's own checkout — refusing with a `--dir`
#     remedy when no such directory exists (a wheel install); and a
#     source-side failure now reports the SOURCE path under its own
#     statuses (`source_absent`, `source_unreadable`) instead of
#     misattributing the healthy installed target.
#   - the reviewer procedure gains the falsification-anchoring rule (D3):
#     anchor in the reviewed tree wherever the defect admits it, and name
#     an external mutable dependency in the finding when it does not.
TOOL_VERSION = "0.12.0"

# Names this tool has carried before, oldest first. Read acceptance for
# artifacts and state produced under them is deliberate and noticed, never
# assumed (§10.1: a half-renamed wire format is worse than a break because it
# is silent).
FORMER_NAMES = ("rvw",)

# The declared travelling behavioural set — every travelling artefact whose
# bytes can change what the tool does, by LOGICAL path: the name the
# artefact has in an installed tree. This enumeration is the AUTHORITY the
# tool's identity is computed against, and the identity claims exactly this
# set — never "what this installation is", which is a claim no enumeration
# can warrant (lineage 8, the boundary-authority decision: the attackable
# surface is the finite, gated enumeration, not an unbounded claim).
#
# Lineage 7. A version string is what a tool chooses to say about itself; the
# digest of the files that ran is what it is. `emit.py` has made that argument
# about GATE executables since round 3. It was never made about loupe itself,
# and the cost came due twice: on 2026-08-23 a reviewer ruled a round with an
# installation grafted from three fixes earlier and relayed a command carrying
# a shell syntax error that the round under review existed to fix — both
# binaries honestly reported `0.4.0`.
#
# Round 1 defined this over `review/*.py`; the reviewer broke it with
# `bin/loupe`. Round 2 added the shim but excluded the rest of the travelling
# set by PREFIX, and the reviewer broke that too, one directory higher:
# `public/pyproject.toml` declares `[project.scripts] loupe =
# "review.cli:main"` and says on its own face that it backs the `uvx` install
# path. The blanket `public/` rule called it non-behavioural, which was
# false — and worse, a prefix rule admits every FUTURE file beneath it
# without a decision, which is round-1 F1 recreated one level up.
#
# So there are no prefix rules. Every travelling artefact is named here
# exactly once, carried or excluded with a reason, and
# `test_identity_boundary.py` asserts this enumeration EQUALS the extraction
# inventory's travelling set in both directions. A new travelling file makes
# that test fail until someone decides which it is; nothing inherits.
#
# LOGICAL paths, because extraction maps `public/X` -> `X`. `pyproject.toml`
# is `public/pyproject.toml` here and `pyproject.toml` in an installed tree,
# and the identity must call those the same artefact or a workbench and the
# tree extracted from it would never agree. `_resolve` performs exactly that
# transform, and a test requires each carried artefact to resolve through
# exactly one of the two forms, so an ambiguous tree cannot silently pick.

#: Reasons, named once and shared, so an exclusion cannot be a bare entry.
TESTS = ("tests check the tool's behaviour and never decide it; no CLI path "
         "imports them")
ADAPTERS = ("generated from review/adapters.py, which IS carried, and guarded "
            "by `render-adapters --check`; they instruct the agents, not the "
            "tool")
DOCS = "prose; the tool reads none of it to decide anything"
EXAMPLE_CONFIG = ("the example config shipped for a reader to copy; the tool "
                  "reads the config of the repository under review, never "
                  "this file")
VERSION_CONTROL = "consulted by version control, never by this tool"

#: Carried and COMPARED: the package, and nothing else.
#:
#: RVW-T18. Until 0.9.0 this tuple also held `bin/loupe` and
#: `pyproject.toml`, on the argument that an identity should cover the
#: launcher of every advertised install path. The argument was sound and
#: the set was not: this enumeration is read by code INSIDE the package,
#: and a wheel install carries only the package, so those two artefacts
#: are unreachable in the one install path that builds from
#: `pyproject.toml`. Measured across four shapes of byte-identical 0.9.0
#: code — workbench and full clone 18/18; vendored `review/` + `bin/loupe`
#: 17/18; `uvx` wheel install 16/18, with `pyproject.toml` absent from the
#: wheel RECORD entirely and `bin/loupe` present only as the generated
#: console script, which `installation_root()` does not resolve. Three
#: identities, and of the three ADVERTISED install paths no two could ever
#: agree. That is arithmetic, not judgement, and it is why every
#: cross-machine round reported `tool: DIFFERS` for reasons unrelated to
#: what either end would do.
#:
#: So the set that is COMPARED is the largest one that can be equal across
#: every install shape: the package modules. What was lost by narrowing it
#: is not discarded — it moves to `SHAPE_ARTEFACTS` below and is REPORTED.
IDENTITY_ARTEFACTS = (
    "review/__init__.py",
    "review/__main__.py",
    "review/adapters.py",
    "review/brief.py",
    "review/cli.py",
    "review/config.py",
    "review/digest.py",
    "review/emit.py",
    "review/fingerprint.py",
    "review/ledger.py",
    "review/paths.py",
    "review/refs.py",
    "review/transport.py",
    "review/validate.py",
    "review/vocab.py",
    "review/wire.py",
)

#: Carried and REPORTED, never compared: the launchers.
#:
#: The other half of RVW-T18. These decide what runs and, in the shim's
#: case, what environment a gate subprocess sees — `bin/loupe` exports the
#: caller's `PYTHONSAFEPATH`/`PYTHONPATH` stash that `emit.py` restores
#: into every gate — so dropping them from the record entirely would let a
#: tampered or merely STALE shim change gate results while the identity
#: said `match`. That was the strongest objection to narrowing the set, and
#: it is answered here rather than accepted: the bytes stay in the
#: envelope, under their own attribute.
#:
#: They are reported and NEVER compared, because comparing them is exactly
#: what could not work: absent in a wheel, present in a clone, and the two
#: installations are running the same code. A reader that compared this
#: would reproduce the defect the split exists to remove. Absence is a
#: normal, correct state for this set, which is why it is not evidence of
#: anything on its own — the human reads it beside a `tool` that already
#: says whether the behaviour agrees.
SHAPE_ARTEFACTS = (
    "bin/loupe",
    "pyproject.toml",
)

IDENTITY_EXCLUDED = {
    ".gitignore": VERSION_CONTROL,
    "CHANGELOG.md": DOCS,
    "LICENSE": DOCS,
    "README.md": DOCS,
    "adapters/claude/SKILL.md": ADAPTERS,
    "adapters/codex/SKILL.md": ADAPTERS,
    "adapters/instructions-block.md": ADAPTERS,
    "docs/design.md": DOCS,
    "docs/onboarding.md": DOCS,
    "review.toml": EXAMPLE_CONFIG,
    "review/tests/__init__.py": TESTS,
    "review/tests/fixtures/legacy-verdict.md": TESTS,
    "review/tests/fixtures/mini-verdict.md": TESTS,
    "review/tests/fixtures/verdict-with-closures.md": TESTS,
    "review/tests/synth.py": TESTS,
    "review/tests/test_adapters.py": TESTS,
    "review/tests/test_attestation_integrity.py": TESTS,
    "review/tests/test_breakers.py": TESTS,
    "review/tests/test_command_boundary.py": TESTS,
    "review/tests/test_convergence.py": TESTS,
    "review/tests/test_debug_round.py": TESTS,
    "review/tests/test_command_surface.py": TESTS,
    "review/tests/test_falsification_record.py": TESTS,
    "review/tests/test_fingerprint.py": TESTS,
    "review/tests/test_gate_environment.py": TESTS,
    "review/tests/test_git_reads.py": TESTS,
    "review/tests/test_paths.py": TESTS,
    "review/tests/test_prune.py": TESTS,
    "review/tests/test_reachability.py": TESTS,
    "review/tests/test_readme_walkthrough.py": TESTS,
    "review/tests/test_reference_binding.py": TESTS,
    "review/tests/test_reference_domain.py": TESTS,
    "review/tests/test_rename_migration.py": TESTS,
    "review/tests/test_role_stamp.py": TESTS,
    "review/tests/test_round3_fixes.py": TESTS,
    "review/tests/test_round4_fixes.py": TESTS,
    "review/tests/test_round5_fixes.py": TESTS,
    "review/tests/test_shadow_round.py": TESTS,
    "review/tests/test_tool_identity.py": TESTS,
    "review/tests/_transport_fixtures.py": TESTS,
    "review/tests/test_transport_authority.py": TESTS,
    "review/tests/test_transport_events.py": TESTS,
    "review/tests/test_transport_integration.py": TESTS,
    "review/tests/test_transport_lifecycle.py": TESTS,
    "review/tests/test_transport_topology.py": TESTS,
    "review/tests/test_validate.py": TESTS,
    "review/tests/test_worktree_and_brief.py": TESTS,
    "review/tests/util.py": TESTS,
}


def is_package_module(logical: str) -> bool:
    """The explicit, finite module predicate: a logical path is a package
    module iff it lies under `review/`, ends `.py`, and is not a test.

    Lineage 8: every module universe is an AUTHORITY filtered through this
    one predicate — the installed tree's universe filters the travelling
    enumeration (`IDENTITY_ARTEFACTS`, below), and the workbench's filters
    the extraction inventory's `stays` entries, in the boundary suite where
    that inventory lives. The predicate decides what a path IS, never which
    paths exist: existence always comes from the authority, so a module
    cannot exist without being scanned, and neither universe rests on a
    list someone typed.
    """
    return (logical.startswith("review/") and logical.endswith(".py")
            and not logical.startswith("review/tests/"))


#: The modules a production read path can live in — in an installed tree.
#: Round-4 F1: the parse-site gate walked a tuple of fourteen names written
#: in the test, so `review/corpus.py` — which parses a request — was outside
#: it, and a NEW module could be added and never scanned. The universe is
#: now derived from the same enumeration the boundary gate already proves
#: equals what travels, so a module cannot exist without being scanned.
def production_modules() -> tuple:
    """Every non-test module of the installed tree, by logical path: the
    travelling authority filtered through `is_package_module`.

    A workbench also holds modules that stay behind; their authority is the
    extraction inventory's `stays` entries, which stay in the workbench —
    so the workbench universe is derived THERE, in the boundary suite,
    through the same predicate. Until lineage 8 the workbench extra was a
    literal tuple here (`IDENTITY_NOT_SHIPPED`), which is exactly the hand
    list the reviewer broke twice, one level down each time.
    """
    return tuple(sorted(p for p in IDENTITY_ARTEFACTS
                        if is_package_module(p)))


def installation_root() -> Path:
    """The directory holding the package — what `bin/loupe` computes as
    ROOT, and what every logical path is resolved against."""
    return Path(__file__).resolve().parent.parent


def identity_paths(root: Path | None = None,
                   artefacts: tuple = None) -> dict:
    """Each carried artefact's LOGICAL name mapped to where it actually is
    in this installation, applying the `public/X` -> `X` extraction
    transform explicitly rather than hoping the two trees agree by luck.

    The installed form is tried first and the workbench form second. A
    logical path that resolves to neither maps to the installed form and is
    digested as absent — which is how a site-packages install, with no
    `bin/loupe` beside it, correctly gets a SHAPE of its own while its
    behavioural identity still equals every other install of the same code.

    `artefacts` names WHICH declared set to resolve, and defaults to the
    compared one. It is a parameter rather than two near-identical
    functions because the resolution rule is one rule: the two sets differ
    in what they enumerate, never in how a name becomes a path.
    """
    root = Path(root) if root is not None else installation_root()
    if artefacts is None:
        artefacts = IDENTITY_ARTEFACTS
    resolved = {}
    for logical in artefacts:
        installed = root / logical
        workbench = root / "public" / logical
        resolved[logical] = (installed if installed.is_file()
                             else workbench if workbench.is_file()
                             else installed)
    return resolved


def tool_identity(root: Path | None = None) -> str:
    """The content identity of the declared travelling behavioural set —
    `IDENTITY_ARTEFACTS`, which is the authority — as this installation
    carries it: 16 hex characters over logical path and bytes.

    The claim is bounded by the authority, deliberately: this identifies
    the declared set, not "what this installation is". Whether the set is
    complete is the authority's own property, held by its gate — the
    attackable surface is the finite enumeration, never the unbounded
    claim (lineage 8).

    Equal across a workbench and an installed tree carrying the same code,
    because the set is exactly what travels and decides and the extraction
    transform is applied; different the moment any of it differs, whatever
    the two versions call themselves. Computed on demand from files on disk
    — never cached, never written into the source — so it cannot go stale
    the way the thing it detects goes stale.
    """
    from .digest import sha256_file_set
    return sha256_file_set(identity_paths(root))[:16]


def shape_identity(root: Path | None = None) -> str:
    """The content identity of the declared INSTALL-SHAPE set —
    `SHAPE_ARTEFACTS` — as this installation carries it: 16 hex characters
    over logical path and bytes, computed exactly as `tool_identity` is.

    Reported beside the behavioural identity and NEVER compared against
    another end's. Two installations of the same code legitimately differ
    here — a wheel install carries neither launcher on a path this can
    resolve, a clone carries both — so equality is not a property this
    value has, and a reader that demanded it would rebuild the defect
    RVW-T18 removed.

    What it IS good for is the question the shim can otherwise dodge: the
    launcher decides what runs, and `bin/loupe` in particular exports the
    environment stash `emit.py` restores into every gate subprocess, so a
    shim that changed under a reader's feet is a fact worth carrying even
    though it is not a fact worth refusing on. Absent artefacts digest as
    absent, so this value is stable and meaningful within one install
    shape, which is the comparison a human actually makes.
    """
    from .digest import sha256_file_set
    return sha256_file_set(identity_paths(root, SHAPE_ARTEFACTS))[:16]


def env_var(suffix: str) -> str:
    """Environment variable name for this tool, e.g. LOUPE_STATE_DIR."""
    return f"{TOOL_NAME.upper()}_{suffix}"

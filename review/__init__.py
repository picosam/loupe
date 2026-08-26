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
TOOL_VERSION = "0.8.0"

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

#: Carried: the launcher of every advertised install path, and the package.
#: `bin/loupe` backs the two copy-based paths; `pyproject.toml` backs the
#: `uvx` path, and its `[project.scripts]` entry is the launcher's SOURCE.
#: A built wheel's generated console script is not readable from inside the
#: package, so what is covered is the input that produces it — stated as a
#: limit in design.md rather than claimed as more than it is.
IDENTITY_ARTEFACTS = (
    "bin/loupe",
    "pyproject.toml",
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

IDENTITY_EXCLUDED = {
    ".gitignore": VERSION_CONTROL,
    "LICENSE": DOCS,
    "README.md": DOCS,
    "adapters/claude/SKILL.md": ADAPTERS,
    "adapters/codex/SKILL.md": ADAPTERS,
    "adapters/instructions-block.md": ADAPTERS,
    "docs/design.md": DOCS,
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
    "review/tests/test_command_surface.py": TESTS,
    "review/tests/test_falsification_record.py": TESTS,
    "review/tests/test_fingerprint.py": TESTS,
    "review/tests/test_gate_environment.py": TESTS,
    "review/tests/test_paths.py": TESTS,
    "review/tests/test_prune.py": TESTS,
    "review/tests/test_reachability.py": TESTS,
    "review/tests/test_reference_binding.py": TESTS,
    "review/tests/test_reference_domain.py": TESTS,
    "review/tests/test_rename_migration.py": TESTS,
    "review/tests/test_role_stamp.py": TESTS,
    "review/tests/test_round3_fixes.py": TESTS,
    "review/tests/test_round4_fixes.py": TESTS,
    "review/tests/test_round5_fixes.py": TESTS,
    "review/tests/test_shadow_round.py": TESTS,
    "review/tests/test_tool_identity.py": TESTS,
    "review/tests/test_transport.py": TESTS,
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


def identity_paths(root: Path | None = None) -> dict:
    """Each carried artefact's LOGICAL name mapped to where it actually is
    in this installation, applying the `public/X` -> `X` extraction
    transform explicitly rather than hoping the two trees agree by luck.

    The installed form is tried first and the workbench form second. A
    logical path that resolves to neither maps to the installed form and is
    digested as absent — which is how a site-packages install, with no
    `bin/loupe` beside it, correctly gets an identity of its own.
    """
    root = Path(root) if root is not None else installation_root()
    resolved = {}
    for logical in IDENTITY_ARTEFACTS:
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


def env_var(suffix: str) -> str:
    """Environment variable name for this tool, e.g. LOUPE_STATE_DIR."""
    return f"{TOOL_NAME.upper()}_{suffix}"

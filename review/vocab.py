"""Closed vocabularies of the review grammar (design §5.2).

This module is the single authority for every enum the grammar defines.
Prose (emitted envelopes, reports) renders FROM these constants; nothing may
restate them. Taxonomy (severities, classifications) is deliberately NOT here:
it is per-repo config, declared in each envelope (§8, dialect drift).
"""

# Author dispositions — five terms and closed (§5.2).
DISPOSITIONS = ("accepted", "refuted", "deferred", "preference", "escalated")

# `accepted` carries one structured subtype rather than a sixth term.
ACCEPTED_SUBTYPES = ("test_amended",)

# Dispositions illegal for findings at blocking severity.
BLOCKING_ILLEGAL = ("deferred", "preference")

# Reviewer closure events (§5.2).
CLOSURES = ("withdrawn", "sustained", "reclassified", "test_amendment")
TEST_AMENDMENT_OUTCOMES = ("ratified", "contested")

# Verdict strings — the masters' two-string vocabulary, untouched (§5.1 F4/R2-F2:
# material unavailability maps to `changes requested`, never a third string).
VERDICT_CLEAN = "clean to advance"
VERDICT_CHANGES = "changes requested"
VERDICTS = (VERDICT_CLEAN, VERDICT_CHANGES)

# Fields every finding must carry, in order (round-2 request, verdict shape).
FINDING_FIELDS = (
    "Severity",
    "Classification",
    "Title",
    "Evidence",
    "Why",
    "Required outcome",
    "FALSIFICATION",
)

# Mandatory payload keys per disposition (§5.2 table). A disposition without
# its payload does not validate.
DISPOSITION_PAYLOADS = {
    "accepted": ("change", "verification"),
    "refuted": ("evidence",),
    "deferred": ("destination", "trigger"),
    "preference": ("axis", "why_reviewer_option_acceptable"),
    "escalated": ("question", "positions"),  # plus evidence OR authority+criterion
}

# Payload keys for accepted(test_amended) (§5.2).
TEST_AMENDED_PAYLOAD = ("original_test", "amended_test", "why_unsatisfiable")

# Falsification kinds (§5.3a).
# Round 6 F2. A gate id is not only a logical identity: `_retain_output`
# interpolates it into `<ledger>/gate-output/<sha>/<id>.log`, so an absolute
# form discards the directory entirely and a separator or dot segment escapes
# or aliases it. Raw-string uniqueness cannot establish unique DESTINATIONS.
# The grammar is closed instead of the escapes being enumerated: an id is one
# filename component built from a set that contains no separator, no leading
# dot, and nothing a filesystem folds — so containment and one-id-one-file
# are properties of the alphabet rather than of a blacklist someone maintains.
GATE_ID_RE = r"[A-Za-z0-9][A-Za-z0-9._-]*"
GATE_ID_MAX = 64


# Git tree-entry modes (round 4 F1). The set is git's, not this tool's, and
# it is closed: git writes exactly these. Only the two REGULAR-FILE modes
# carry bytes a checkout would read — a symlink's blob holds a path, a tree
# holds entries, a gitlink holds another repository's commit — so only they
# may supply a configuration.
GIT_FILE_MODES = ("100644", "100755")
GIT_MODE_NAMES = {
    "100644": "a regular file",
    "100755": "an executable file",
    "120000": "a symbolic link",
    "040000": "a directory",
    "40000": "a directory",
    "160000": "a submodule",
}


def git_mode_name(mode: str) -> str:
    """What a tree entry IS, named rather than left as a number."""
    return GIT_MODE_NAMES.get(mode, f"an unrecognised entry (mode {mode})")


FALSIFICATION_KINDS = ("command", "observation")

# What an `accepted` disposition records about the finding's falsification
# test (§5.3a: "fixed" means the named test flipped, not "I edited something
# nearby"). Carried as payload["falsification"] = {status, mutation[, note]}
# and recorded as a `falsification_run` event, which is what the `stale` and
# `unverifiable` breakers read. Two closed axes:
#
#   status   — the named test, run on the head this disposition binds
#   mutation — the same test, run with the defect reintroduced: the proof
#              that the test can tell fixed from unfixed. A test that passes
#              both ways falsifies nothing, and this tool's own review found
#              two of those written by the author of the fix.
FALSIFICATION_STATUSES = ("pass", "fail", "cannot_execute")
MUTATION_OUTCOMES = ("fails_without_fix", "passes_without_fix", "not_run")
FALSIFICATION_RECORD = ("status", "mutation")

# Lineage event kinds (§5.3b): how identity is carried when syntax moves.
LINEAGE_KINDS = ("rename", "anchor_change", "invariant_introduced",
                 "split", "semantic_repeat", "alias")
# Kinds that merge two fingerprints into one identity. `split` forks a parent
# into distinct children and `semantic_repeat` records a judgment; neither merges.
LINEAGE_MERGING = ("rename", "anchor_change", "invariant_introduced", "alias")

# Breakers (§5.3d). Names only — the logic lives in ledger.py. `orphan`
# (round 3 F1): a disposition companion event that binds no recorded
# emission — it certifies nothing, feeds no other breaker, and escalates.
BREAKERS = ("repetition", "stale", "no-progress", "unverifiable", "budget",
            "orphan")

# --------------------------------------------------------------- the claim

# The claim file's complete grammar (lineage-3 round 7 F1). ONE authority:
# the capture boundary admits from it, the closed-world test derives its
# cases from it, and the adapters render the author's field list from it.
#
# Round 5 closed the claim's SYNTAX (a member stated twice) and round 6 put
# that check ahead of every ledger read. Neither closed the DOMAIN: `null`,
# `true`, `3`, `"x"` and `[]` were all admitted as claims, every field could
# carry any type, an unknown member was silently ignored — so a mistyped
# `stop_condition_typo` erased a stop condition without a word — and
# malformed JSON escaped as a raw JSONDecodeError rather than the typed
# refusal every other defect gets. A boundary that admits four shapes and
# refuses one is not a boundary; this is the closed set.
NO_CLAIM = "none"

# Rendered into the envelope as prose (emit.emit_request).
CLAIM_STRING_FIELDS = (
    "objective",        # the decision boundary; required
    "risk",             # self-assessed, with a reason
    "review_scope",     # the tier and what is in it
    "access_note",      # how the reviewer reaches the target
    "relay",            # overrides the config's relay line
    "commit_subject",   # the subject handoff commits outstanding work under
)

# Rendered as bulleted lists; every element is a string.
CLAIM_LIST_FIELDS = (
    "deliberately_not",
    "evidence_not_captured",
    "stop_conditions",
    "hand_back",
    "contract",
)

# The reference manifest: digested, labelled, never dropped.
CLAIM_REFERENCES_FIELD = "references"

# member -> kind. The three kinds are the whole grammar; a member added here
# without a kind the validator knows is a refusal, not a silent admission.
CLAIM_FIELDS = {
    **{f: "string" for f in CLAIM_STRING_FIELDS},
    **{f: "list_of_string" for f in CLAIM_LIST_FIELDS},
    CLAIM_REFERENCES_FIELD: "references",
}

# One reference object. `path` is required and non-empty: a reference with no
# path is a digest of nothing, and the manifest's whole point is that a
# pointer without a digest is a rumour (§5.1 F4).
CLAIM_REFERENCE_FIELDS = {"path": "string", "required": "boolean",
                          "note": "string"}
CLAIM_REFERENCE_REQUIRED = ("path",)

# Required when a claim file is supplied at all. Supplying no claim is its
# own recorded state (round-4 F1) and stays legal; supplying one that states
# no objective is not — it renders exactly like the no-claim default, which
# is the collapse rounds 3 and 4 spent themselves separating. `references`
# joined 2026-08-30 (0.11.3, ruled intended policy rather than advisory
# prose): the onboarding page had always told the first round "objective AND
# at least one references entry" while the validator required objective
# alone. A supplied claim that hands the reviewer nothing to read is a bare
# assertion; the manifest's whole point is digested references. An empty
# list is refused for the same reason a missing member is (emit checks it).
CLAIM_REQUIRED = ("objective", "references")

# Members whose string value may not be empty or blank.
CLAIM_NONEMPTY = ("objective",)


# ------------------------------------------------------------- transport

# How the two ends of one round exchange bytes (§5.2, RVW-T11). Closed, and
# it is a TOPOLOGY rather than a carrier menu: `path` says the two sides read
# the same filesystem, so a kept path is a thing the other end can open;
# `paste` says they do not, so bytes are the only thing that crosses. The
# §5.2 decision is unchanged by this enum — paste is still the only carrier
# provided for the second case, and no third value adds one.
#
# It is declared, never observed. A process can see where IT runs; it cannot
# see where the other side runs, and the relay text on each leg is about the
# OTHER side. Inferring it from what is visible locally — an environment
# variable, `take -` on stdin, a missing path — fails open in the direction
# that hurts: it reads "same machine" when nobody said so, which is exactly
# the state that prints a path the far end cannot open. So silence means the
# default, and the default is stated on the envelope's face rather than
# assumed by each reader.
TRANSPORT_PATH = "path"
TRANSPORT_PASTE = "paste"
TRANSPORTS = (TRANSPORT_PATH, TRANSPORT_PASTE)

# ---------------------------------------------------------------- debug

# The debug-round stamp (2026-08-31). A round emitted with `--debug`
# carries `debug="tool-feedback"` on the request wrapper: the reviewer is
# asked to ALSO critique the tool's own performance that round —
# efficiency, cost, accuracy — in an optional `## tool feedback` verdict
# section, which `close` records as a ledger event so the critiques
# accumulate. The section is legal on any verdict (a reviewer may always
# volunteer it); the stamp is what asks for it. Advisory by construction:
# it is prose about the tool, never a gate, and no check judges its
# quality (ruled 2026-08-30, prose guards).
DEBUG_ATTR = "debug"
DEBUG_TOOL_FEEDBACK = "tool-feedback"
TOOL_FEEDBACK_SECTION = "tool feedback"

# --------------------------------------------------------- stamped envelopes
# Which envelope kinds carry a tool-identity stamp, and every production seam
# that READS one — the finite authority round-2 F2 required, because fixing
# one silent reader would only have left the next unpartitioned door.
#
# `cross` means the envelope can have been written by another installation, so
# the seam must compare before it renders or records. `same_process` means the
# verb wrote the envelope itself in the same run: a comparison there could
# only ever say `match`, and a check that cannot fail is worse than a stated
# absence. The verdict is in neither list — it has NO emitter, being
# hand-authored by the reviewer agent from a shape block, so there is nothing
# to compare and stamping it would be a model transcribing a digest.
STAMPED_KINDS = ("request", "disposition")

STAMPED_READERS = {
    ("take", "request"): "cross",
    ("brief", "request"): "cross",
    ("validate", "request"): "cross",
    ("validate", "disposition"): "cross",
    ("ledger add", "request"): "cross",
    ("ledger add", "disposition"): "cross",
    # Round-3 F1. Re-running `handoff` on an unchanged tip returns the
    # RETAINED request from a previous run and renders its relay — across
    # processes, and therefore across installations. It was absent from
    # this table, and the matrix that was supposed to catch that compared
    # one hand-written list against another and so could not.
    ("handoff cache", "request"): "cross",
    # The fresh emission renders what it has just built in this process.
    ("handoff", "request"): "same_process",
    ("respond", "disposition"): "same_process",
    # Archival: envelopes that predate the stamp, so there is nothing to
    # compare — distinct from `same_process`, where a comparison would be
    # possible but could only ever say `match`. Used by workbench-only
    # import paths; no seam declared here carries it.
}

SEAM_CLASSES = ("cross", "same_process", "archival")

# Where the production code actually PARSES a stamped envelope, attributed
# to the seams those parses serve. Round-3 F1: an authority listing readers
# is only as complete as whoever remembered to add one, so the test that
# guards it walks the source for every `parse_request` / `parse_disposition`
# / `_detect_and_parse` call and fails on any site this map does not name.
# A new read path is then unclassified until someone classifies it.
#
# An empty tuple means the site is not a seam of its own: `_detect_and_parse`
# parses only to decide which kind it has, and its callers are the seams.
# Several helpers serve two seams — `_brief_into` and `record_handoff` are
# reached from both the fresh and the cached handoff branch — so the
# comparison belongs to the branch, not to the helper.
STAMPED_PARSE_SITES = {
    ("cli", "_detect_and_parse"): (),
    ("cli", "cmd_validate"): (("validate", "request"),
                              ("validate", "disposition")),
    ("cli", "cmd_respond"): (("respond", "disposition"),),
    ("cli", "cmd_ledger_add"): (("ledger add", "request"),
                                ("ledger add", "disposition")),
    ("cli", "cmd_handoff"): (("handoff cache", "request"),),
    ("cli", "_emit"): (("handoff", "request"),),
    ("cli", "_brief_into"): (("handoff", "request"),
                             ("handoff cache", "request")),
    ("cli", "cmd_take"): (("take", "request"),),
    ("cli", "cmd_brief"): (("brief", "request"),),
    ("transport", "record_response"): (("respond", "disposition"),),
    ("transport", "cached_handoff"): (("handoff cache", "request"),),
    ("transport", "record_handoff"): (("handoff", "request"),
                                      ("handoff cache", "request")),
    ("transport", "take"): (("take", "request"),),
}

#: The parse calls that reach a stamped envelope, for the source walk.
STAMPED_PARSE_CALLS = ("parse_request", "parse_disposition",
                       "_detect_and_parse")
# What an UNSTAMPED envelope or a legacy ledger record READS as: every
# envelope and record written before the attribute existed came from a
# same-filesystem loop, and reading that silence as anything else would
# make the historical record unreadable (RVW-T11).
TRANSPORT_DEFAULT = TRANSPORT_PATH
# What a NEW emission resolves to when NO declaration reaches it (round 3
# F2, settling the lineage-6-round-2 incident policy): `path`, the
# workflow's declared steady case — the user operates author and reviewer
# on one machine, and pricing every ordinary local round at the paste
# carrier answered the incident by taxing the case that never caused it.
# The cross-machine case is covered not by the silent default but by an
# ENVIRONMENT-BOUND declaration the cloud side carries: a cloud-authored
# session sets `LOUPE_TRANSPORT=paste` (TRANSPORT_ENV, configured in the
# cloud environment's own settings), or is recognized by a documented
# provider signal (TRANSPORT_PROVIDER_SIGNALS). Both rank above this
# default and below the repository's `[roles] transport` and the explicit
# `--transport` flag — a human declaration always outranks an environment
# one, which outranks an inference, which outranks silence.
TRANSPORT_EMISSION_DEFAULT = TRANSPORT_PATH

# The Loupe-specific environment declaration (round 3 F2). Set by the
# ENVIRONMENT a session runs in — a cloud sandbox's configuration writes
# `LOUPE_TRANSPORT=paste` because bytes are the only carrier that reaches
# the operator's machine from there. It is a declaration like the config
# key: empty or outside the vocabulary is refused, never folded to a
# default.
TRANSPORT_ENV = "LOUPE_TRANSPORT"

# Documented provider signals: (variable, exact value) -> the transport it
# entails, under ONE stated workflow assumption: the OTHER endpoint — the
# reviewer relay the operator drives — is on the operator's local machine,
# so an author endpoint known to be a cloud sandbox does not share its
# filesystem. The matrix is closed and exact-match: any other value of the
# variable is NOT a signal (a process that half-matches an env var is
# inferring, which §5.2 rejects).
#
#   CLAUDE_CODE_REMOTE == "true" — Claude Code cloud sessions set it
#   (recorded evidence, 2026-08-11: cloud provisioning scripts gate on
#   exactly this variable and value). It identifies the AUTHOR endpoint
#   only.
#
# Codex cloud is deliberately absent: current official OpenAI
# documentation guarantees user-configured environment variables persist
# through a cloud chat and documents no intrinsic cloud/topology marker —
# so a Codex cloud environment declares TRANSPORT_ENV instead of being
# sniffed.
TRANSPORT_PROVIDER_SIGNALS = (
    ("CLAUDE_CODE_REMOTE", "true", TRANSPORT_PASTE),
)


class TransportDeclarationError(RuntimeError):
    """A transport declaration outside the closed grammar, refused by the
    one reader every source and consumer goes through (R1-F2)."""

    def __init__(self, message: str, remedy: str):
        super().__init__(message)
        self.remedy = remedy


def transport_or_default(value, where: str) -> str:
    """THE transport lifecycle boundary: every admitted source — config,
    author flag, reviewer correction, wrapper stamp, ledger record — and
    every reader resolve a declared value through this one function
    (R1-F2).

    Exactly four input states, schema-derived, nothing else:

      * absent (None)   — the default. A real declaration, not an unknown:
                          every envelope, config and ledger event written
                          before the attribute existed came from a loop
                          with one topology, and reading that silence as
                          unknown would make the historical record
                          unreadable (RVW-T11).
      * empty ("")      — refused. Stating a value emptily is a different
                          act from stating none — the same rule the claim
                          boundary applies to `--claim-file ""` — and an
                          `or` that folds it to the default is the exact
                          fail-open direction §5.2 names: `path` selected
                          when nobody declared the shared filesystem.
      * outside the     — refused, by value, before anything downstream
        vocabulary        acts: an unrecognised carrier stamped on an
                          envelope, recorded in an append-only ledger, or
                          used to select a relay would mean both ends
                          acting on a topology neither can name.
      * in the          — returned as declared.
        vocabulary

    Refusal happens HERE, in the reader, because a closed lifecycle cannot
    rely on every caller remembering to route through one validating verb:
    the round-1 evidence was three admitted routes (an explicitly empty
    config, a repeated reviewer correction, a defective wrapper read by
    `brief`) that each selected `path` without anyone declaring it.
    """
    if value is None:
        return TRANSPORT_DEFAULT
    if value not in TRANSPORTS:
        stated = "explicitly empty" if value == "" else repr(value)
        raise TransportDeclarationError(
            f"{where} declares a transport that is {stated}, not one of "
            f"{list(TRANSPORTS)}: the transport is a closed vocabulary, "
            f"and a value outside it names no topology either side can "
            f"act on (§5.2)",
            remedy=f"a person declares one of {list(TRANSPORTS)} — "
                   f"`path` when both sides read the same filesystem, "
                   f"`paste` when they do not")
    return value

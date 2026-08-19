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

# Breakers (§5.3d). Names only — the logic lives in ledger.py.
BREAKERS = ("repetition", "stale", "no-progress", "unverifiable", "budget")

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
    "objective",        # the decision boundary; the one required member
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
# is the collapse rounds 3 and 4 spent themselves separating.
CLAIM_REQUIRED = ("objective",)

# Members whose string value may not be empty or blank.
CLAIM_NONEMPTY = ("objective",)

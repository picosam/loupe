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
# interpolates it into `<ledger>/gate-output/<sha>/<run>/<id>.log`, so an absolute
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

# Round 3 F3: `relay` names WHO carried the request — the same kind of fact
# `[roles] author`/`reviewer` name — never what to do with the answer. A
# free-text sentence in it (a hand-back instruction, say) would render on
# the Roles line as if it were the carrier's identity. Closed to the
# identifier grammar an actor name is written in: one token, no whitespace,
# nothing that could read as prose. `hand_back` is the claim's dedicated
# field for a stopping instruction; this grammar exists so `relay` cannot
# be mistaken for it.
ACTOR_RE = r'\A[A-Za-z0-9][A-Za-z0-9_.-]*\Z'
ACTOR_WANT = ("a single actor identifier — letters, digits, '_', '-' or "
             "'.', no whitespace")


# ------------------------------------------------------------- transport

# How the two ends of one round exchange bytes (§5.2, RVW-T11). Closed, and
# it is a TOPOLOGY rather than a carrier menu: `path` says the two sides read
# the same filesystem, so a kept path is a thing the other end can open;
# `paste` says they do not, so bytes are the only thing that crosses; `git`
# says they do not share a disk but DO reach one remote, so the envelope
# rides a ref under `refs/<tool>/` on the remote the reviewed branch was
# already pushed to (ruled 2026-09-03, brief `git-ref-carrier`).
#
# `git` reverses one sentence of §5.2 — "paste is the transport, and there
# is no other" — and reverses nothing else. The rejections stand: PR
# comments (a forge as the review bus, a credential the tool must hold) and
# a commit on the branch (it pollutes the change under review) and git notes
# (one ref for every note, rewritten on each add, so two rounds or two
# writers collide). What answers §5.2's own objection is that the remote is
# not a new service: `handoff` already pushes to it and `take` already
# fetches from it, so nothing new is reached and no credential is added.
# Storage is still not a trigger — a pushed ref starts nothing, and the human
# still tells the reviewer to take.
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
TRANSPORT_GIT = "git"
TRANSPORTS = (TRANSPORT_PATH, TRANSPORT_PASTE, TRANSPORT_GIT)

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

# ------------------------------------------------- the authority's answer

# A finding dies exactly two ways in the protocol proper: the author accepts
# and verifies it, or the reviewer withdraws it against a refutation. The
# symmetry rule is deliberate and stays — it is what stops findings dying of
# omission or fatigue. But neither death is available when a HUMAN reads a
# finding and decides to live with it as it stands.
#
# The vocabulary already went most of the way (2026-09-01): `escalated` is
# legal on a blocking finding and its payload carries `authority` and
# `criterion`, so a finding can already be ROUTED to a named human. What was
# missing is the other half — nothing recorded what that authority ANSWERED.
# The escalation therefore either looped, or got laundered into a reviewer
# withdrawal the reviewer did not mean, which makes "the reviewer found
# nothing" indistinguishable from "the reviewer found something a human waved
# off". Those are different facts and keeping them apart is the whole job.
#
# So the answer is an EVENT, on the pattern the commit waiver already set: it
# names the finding, the reason and the human who decided. It is deliberately
# NOT a disposition — the author does not get to write the authority's answer
# — and deliberately not a withdrawal, because the reviewer did not change
# their mind.
FINDING_WAIVER_EVENT = "finding_waiver"

# THE LIFECYCLE AUTHORITY for whether a ruled finding still STANDS (lineage
# 20 round 4 F1/F2). Every recorded answer to a finding is one of the closure
# terms above, one of the five dispositions, or the human waiver — and this
# table states what each one does to the finding it answers. It is closed
# against those vocabularies by a test that fails by name when a term
# arrives here unclassified, so a new answer kind cannot join the grammar
# and leave the standing set to guess.
#
# Three effects, and only three:
#   SETTLES   — the finding leaves the standing set (the reviewer withdrew
#               it, or the author accepted it and the validator checked the
#               falsification record);
#   OVERRULES — it STANDS UNFIXED, and a named human has answered it, which
#               is what an advance enumerates;
#   OPEN      — it stands and nobody has answered it: the reviewer sustained
#               or reclassified it, an amended test is on record, or the
#               author refuted, deferred, preferred or escalated — each of
#               those says where the work went, none says it is resolved.
#
# EVERY answer is bound to the ruling it answers: an answer recorded at
# round r answers the newest ruling of that identity only if r is not
# earlier than the round that ruling was made in. An identity re-raised in a
# later round is open again whatever was recorded against its earlier
# ruling — that is the `stale` breaker's whole subject, and it holds for a
# withdrawal and a waiver exactly as it holds for an acceptance.
ANSWER_SETTLES = "settles"
ANSWER_OVERRULES = "overrules"
ANSWER_OPEN = "open"
FINDING_ANSWERS = {
    ("closure", "withdrawn"): ANSWER_SETTLES,
    ("closure", "sustained"): ANSWER_OPEN,
    ("closure", "reclassified"): ANSWER_OPEN,
    ("closure", "test_amendment"): ANSWER_OPEN,
    ("disposition", "accepted"): ANSWER_SETTLES,
    ("disposition", "refuted"): ANSWER_OPEN,
    ("disposition", "deferred"): ANSWER_OPEN,
    ("disposition", "preference"): ANSWER_OPEN,
    ("disposition", "escalated"): ANSWER_OPEN,
    (FINDING_WAIVER_EVENT, None): ANSWER_OVERRULES,
}

#: The (kind, term) pairs that carry AUTHORIZATION WEIGHT — every answer
#: that settles a ruling or overrules it, derived from the table above
#: rather than restated. Anything not here leaves the finding OPEN and can
#: forge no lifecycle state, so the checks that guard ingestion enumerate
#: their own domain from this instead of from a list someone maintains
#: (round-9 F1/F2: the round-8 checks hand-picked two of these three).
SETTLING_ANSWERS = frozenset(
    pair for pair, effect in FINDING_ANSWERS.items() if effect != ANSWER_OPEN)


# ------------------------------------------------ the legacy import door
#
# THE CLOSED GRAMMAR OF AN IMPORTED EVENT (lineage 20 round 9 F2), on the
# pattern `CLAIM_FIELDS` and `AUTHORIZATION_FIELDS` set, and for a sharper
# version of the same reason. `import-legacy` ingests arbitrary pre-derived
# JSONL — a door with no cryptographic and no process-level authentication —
# and until this existed the only thing it required of a row was a truthy
# `event` key. So `{"event": "disposition", "disposition": "accepted",
# "payload": "not-a-dict"}` reached an uncaught AttributeError, a finding
# with `round: []` imported, and a bare `verdict` row at round 99 made
# `metrics()["rounds_to_clean"]` report 99.
#
# Two halves, and the second is the one self-consistency cannot supply:
#
#   SHAPE      — every admitted kind's fields and their types, closed. An
#                unknown member is refused BY NAME rather than ignored,
#                because a misspelt `answers_round` silently dropped turns a
#                bound closure back into an inferred one.
#   PROVENANCE — every row names a TRACKED PATH in this repository's own git
#                history (`source_path`) and the digest it claims for those
#                bytes (`source_digest`); the tool reads the content itself
#                at an anchor commit it has verified is contained in a
#                fetched remote-tracking ref, hashes it ITSELF, and then
#                requires the row's own claim TEXT to occur inside that
#                content (`LEGACY_CONTAINMENT`).
#
# Round 9 built provenance out of a manifest FILE the caller wrote: `{path,
# sha256}` entries the tool hashed. Round 10 walked through it in one step —
# the party who writes the batch also writes the manifest, so a throwaway
# file, hashed by its own author, "substantiated" a finding titled
# `fabricated`. Two properties replace it, and NEITHER alone is enough:
#
#   ANCHORED   — the bytes are read from `<commit>:<source_path>` where the
#                commit is ancestry of a ref THE REMOTE ITSELF answers for
#                (`ls-remote`, the observation `emit.ensure_pushed` makes).
#                Not `refs/remotes/*`: that is local, writable state — `git
#                update-ref refs/remotes/origin/main <any local commit>`
#                exits 0 offline — and resting the anchor on evidence its
#                own author can write would be this section's own mistake
#                one level down. Bytes that exist only in the terminal
#                session that ran the import cannot be cited at all, and a
#                digest the caller computed is never believed: it is
#                recomputed and compared.
#   CONTAINED  — the row's claim text (a finding's `title`, an import's
#                `verbatim`, a disposition's mandatory payload
#                substantiation, a closure's `ref`, a verdict's term, a
#                request's declared roles) must occur IN the decoded source
#                content. This is what makes the citation about the row: an
#                anchored digest alone proves only that some real file was
#                named beside an arbitrary claim.
#
# What this still CANNOT establish, stated so no reader over-reads it: it is
# not non-repudiation. Someone able to push to the repository's remote can
# commit a file that says whatever the row needs and then cite it; a
# commit's message and authorship are not checked; containment is a
# SUBSTRING test over the whole file, so a source that happens to contain
# the text substantiates it whatever the surrounding context meant. What it
# does establish is that the cited bytes are already durable, shared,
# attributable history, and that the row's own words are traceable INTO
# them — not merely consistent with a sibling field the same author wrote.

#: `import` row kinds. `atomic` is a RULING (`ledger.is_ruling`); `parent`
#: is the pre-split claim one was derived from, carried for provenance and
#: display and never ruled on its own.
LEGACY_IMPORT_ROW_KINDS = ("parent", "atomic")

#: The two members EVERY admitted row carries, whatever its kind. Restated
#: in each kind's `required` below rather than merged in silently, so the
#: schema-derived tests cover them like any other member.
LEGACY_SOURCE_FIELDS = {"source_path": "text", "source_digest": "digest"}

#: kind -> the members whose text must OCCUR IN the cited source content.
#: A member absent or null on a row is not checked (it claims nothing); a
#: member present and not contained refuses the whole batch.
#:
#: `disposition` is empty here and driven by `DISPOSITION_PAYLOADS` instead:
#: what a disposition claims about its source is its mandatory
#: substantiation, and that table already names it per term — one authority,
#: not two lists that can disagree.
#:
#: `lineage` is DELIBERATELY EMPTY, and this is a stated limit rather than an
#: oversight. An alias or split row carries no claim text of its own; its
#: content is two fingerprints, which are hashes and occur in no prose. What
#: guards it instead is `transport._lineage_containment_problems`: any
#: ENDPOINT that is a known ruling must have ITS claim text in the row's
#: source, so a merge of two real findings cannot be sourced to a file that
#: mentions neither. A lineage row whose endpoints are not (yet) rulings —
#: the legacy-fingerprint aliases a derivation emits — is anchored and
#: nothing more.
LEGACY_CONTAINMENT = {
    "finding": ("title",),
    "import": ("verbatim",),
    "disposition": (),
    "closure": ("ref",),
    "lineage": (),
    "request": ("author", "reviewer"),
    "verdict": ("verdict",),
}

#: Members every admitted row may carry whatever its kind: the ledger's own
#: bookkeeping. `uid` is recomputed on append and `ts` defaults to now, so
#: both are optional — a corpus that kept real historical timestamps should
#: not have to throw them away to come in.
LEGACY_COMMON_OPTIONAL = {"uid": "stamp", "ts": "text"}

#: kind -> (required members, optional members), each member -> its type.
#: `source_path` and `source_digest` are required on EVERY kind: a row with
#: no source is a row whose only authority is the batch it arrived in.
LEGACY_EVENT_SCHEMA = {
    "finding": {
        "required": {"event": "event_name", "round": "round", "fp": "fingerprint",
                     "id": "text", "severity": "text",
                     "classification": "text", "title": "text",
                     "source_path": "text",
                     "source_digest": "digest"},
        "optional": {"anchor_path": "text_or_blank", "anchor": "text_or_blank",
                     "citations": "text_or_blank",
                     "invariant_id": "text_or_blank",
                     "falsification": "text_or_blank",
                     "preventable_by": "text_or_null",
                     "cites": "text_list"},
    },
    "import": {
        "required": {"event": "event_name", "kind": "import_kind", "round": "round",
                     "fp": "fingerprint", "legacy_id": "text",
                     "severity": "text", "classification": "text",
                     "verbatim": "text", "source_path": "text",
                     "source_digest": "digest"},
        "optional": {"anchor_path": "text_or_blank", "anchor": "text_or_blank",
                     "citations": "text_or_blank",
                     "invariant_id": "text_or_blank",
                     "preventable_by": "text_or_null"},
    },
    "disposition": {
        "required": {"event": "event_name", "round": "round", "fp": "fingerprint",
                     "finding_id": "text", "disposition": "disposition_term",
                     "payload": "payload", "source_path": "text",
                     "source_digest": "digest"},
        # `author` (round 6 F1): OPTIONAL, not required — an imported row
        # may state it, and `transport._legacy_disposition_author_problems`
        # runs it through the same `check_disposition_author` every other
        # disposition ingress shares: a matching value passes, a mismatch
        # refuses the batch, and an omitted value is derived from the
        # round's recorded request when that request names one. Only a
        # round whose request genuinely carries no author leaves this
        # member absent — the legacy state this schema always admitted.
        "optional": {"subtype": "subtype_or_null", "verdict_sha": "commit",
                     "head": "commit", "batch": "text",
                     "author": "text_or_null"},
    },
    "closure": {
        "required": {"event": "event_name", "round": "round", "fp": "fingerprint",
                     "closure": "closure_term", "source_path": "text",
                     "source_digest": "digest"},
        "optional": {"note": "text_or_blank", "ref": "text",
                     "outcome": "amendment_outcome",
                     "answers_round": "round"},
    },
    "lineage": {
        "required": {"event": "event_name", "kind": "lineage_kind",
                     "from_fp": "fingerprint", "to_fp": "fingerprint",
                     "source_path": "text",
                     "source_digest": "digest"},
        "optional": {"reason": "text_or_blank"},
    },
    "request": {
        "required": {"event": "event_name", "round": "round", "sha": "commit",
                     "bytes": "count", "source_path": "text",
                     "source_digest": "digest"},
        "optional": {"author": "text_or_null", "reviewer": "text_or_null",
                     "tokens": "count"},
        "envelope": True,
    },
    "verdict": {
        "required": {"event": "event_name", "round": "round", "sha": "commit",
                     "bytes": "count", "verdict": "verdict_term",
                     "finding_ids": "count", "source_path": "text",
                     "source_digest": "digest"},
        "optional": {"tokens": "count"},
        "envelope": True,
    },
}

#: Members a row must carry only because of a value it states elsewhere.
#: (kind, member, value) -> the member that becomes mandatory.
#:
#: The first entry is round-9 F1's whole repair. An UNSTAMPED `withdrawn`
#: closure means whatever the identity graph and the ruling set say it means
#: AT READ TIME, and a later, separate import can change both — so a closure
#: that was correctly bound to round 1 when it was appended silently became
#: an answer to a round-2 ruling that did not exist when it was recorded.
#: Validating harder at import cannot fix that: the validation is a
#: statement about the state of the moment, and the meaning it validated is
#: re-derived later against a state nobody re-validated. An explicit stamp
#: is the only binding that does not depend on the graph looking the same
#: afterwards, so this door admits no unstamped settling closure at all.
LEGACY_CONDITIONAL_REQUIRED = {
    ("closure", "closure", "withdrawn"): "answers_round",
    ("closure", "closure", "test_amendment"): "outcome",
}


def legacy_envelope_kinds() -> tuple:
    """The admitted kinds whose facts come from the SOURCE, not the batch.

    Derived from the schema's own `envelope` marks rather than restated as
    a second list beside it: an entry declaring itself an envelope is
    declaring that a manifest entry must state its round, kind and target
    commit, and one place says so.
    """
    return tuple(kind for kind, entry in LEGACY_EVENT_SCHEMA.items()
                 if entry.get("envelope"))

# An authorizer NAME as the authorization wrapper can carry it (lineage 20
# round 4 F4). `by` is stamped into a double-quoted wrapper attribute, and
# the attribute grammar (`wire._ATTR_RE`) has no escape: a quote ends the
# value, a `>` ends the tag, and a control character is nothing a name
# needs. Rather than an encoding that two readers must agree on, the
# grammar is a REFUSAL, shared by the verbs that record a name and the
# validator that judges the stamped one — so every name the tool accepts
# is a name the wrapper can carry, and the partition is this one character
# class rather than a list of characters someone remembered. Blankness is
# refused separately (A-SILENT / A-WRAPPER-VALUE): this rule is about
# representability, not silence.
AUTHORIZER_NAME_RE = r'\A[^\x00-\x1f\x7f"<>]+\Z'
AUTHORIZER_NAME_WANT = ('a name with no double quote, no angle bracket and '
                        'no control character')

# Advancing a lineage over waived findings produces its OWN artifact rather
# than a derived clean verdict, and the difference is the point. A mechanism
# that let an override mint a clean-equivalent verdict would make an
# overridden review indistinguishable from a clean one at exactly the moment
# the distinction matters most — and would convert an unverifiable claim into
# a merge, which is the self-approval route the whole posture exists to close.
#
# What the tool can and cannot say about that record, stated where the
# constants live so no caller has to infer it: `by` is an ASSERTED
# authorizer. The tool cannot observe who ran a command, so this buys
# attribution and visibility, never proof that a human rather than an agent
# took the decision. That protection lives in the instructions each agent
# operates under, exactly as it does for the commit waiver and the breaker
# authorization; this stamps the claim loudly instead of pretending to verify
# it.
ADVANCE_EVENT = "lineage_advanced"
AUTHORIZATION_KIND = "authorization"
AUTHORIZATION_REQUIRED = ("sha", "round", "lineage", "by", "reason", "waived")
WAIVED_REQUIRED = ("finding_id", "fp", "reason", "by")

# THE AUTHORIZATION'S CLOSED GRAMMAR (2026-09-01), on the pattern
# `CLAIM_FIELDS` set for the claim and for the same reason.
#
# The two constants above named REQUIRED MEMBERS and nothing else, so the
# domain around them was open on every other axis: exact-target controls
# showed `loupe validate` returning no error at all when every wrapper stamp
# but the tag was absent, when `round`, `lineage`, `by` and `reason` held
# lists and objects, when unknown members sat at the top level and inside a
# waived record, and when the wrapper said `by="wrapper-person"` while the
# body said `by="body-person"`. This is a published, machine-emitted and
# machine-read boundary that can authorize a PR approval: a validation
# success there must establish the facts it claims to, and a boundary that
# admits arbitrary shapes establishes nothing.
#
# What the four constants below close, in the order a reader meets them:
# the wrapper's attributes, the body's top-level members, the members of one
# waived record, and the facts the two halves state twice and must agree on.

#: Wrapper attribute -> value kind. `emit_authorization` stamps exactly
#: these, so all of them are REQUIRED: unlike `shape` on the older kinds,
#: none of these can be missing because an earlier installation predated
#: them — this envelope kind was born stamped, so an absent attribute is a
#: defect rather than an older emitter.
#:
#: An UNKNOWN attribute name is deliberately still accepted (design §3.1):
#: an older installation must be able to read an envelope carrying an
#: attribute it has never heard of, or every addition is a flag day. What is
#: closed here is that the attributes this grammar names are present, occur
#: once (`parse_attrs`), and carry a value of their kind.
#:
#: Kinds `sha` and `shape` are judged by the SHARED envelope grammar
#: (`E-SHA-SHAPE` in `envelope_identity`, `E-SHAPE-GRAMMAR` in
#: `parse_attrs`) and are presence-only here, deliberately: a grammar
#: enforced at two call sites is a grammar with two chances to drift.
AUTHORIZATION_WRAPPER_FIELDS = {
    "sha": "sha",           # the commit advanced
    "round": "digits",      # decimal, stamped as text
    "lineage": "digits",
    "by": "name",           # asserted, never verified — AUTHORIZER_NAME_RE
    "tool": "identity",     # 16 lowercase hex
    "shape": "shape",
}

#: Top-level body member -> JSON kind. Closed: an unknown member is refused
#: BY NAME rather than ignored, because a misspelled `waived` that is
#: dropped in silence turns an authorization overruling two findings into
#: one overruling none.
AUTHORIZATION_FIELDS = {
    "sha": "string",
    "round": "integer",
    "lineage": "integer",
    "by": "string",
    "reason": "string",
    "waived": "waived_records",
}

#: Body members whose string value may not be blank. `by` and `reason` are
#: the two silences the whole mechanism exists to replace; `sha` binds
#: nothing when it is empty.
AUTHORIZATION_NONEMPTY = ("sha", "by", "reason")

#: One waived record -> JSON kind. The first four are `WAIVED_REQUIRED` and
#: must be non-empty; the rest are optional context the waiver carries when
#: `transport.waive_finding` recorded it (`severity` and `title` from the
#: finding, `destination`/`trigger` from a parked follow-up,
#: `answers_escalation` saying whether the author had routed it first).
WAIVED_FIELDS = {
    "finding_id": "string",
    "fp": "string",
    "reason": "string",
    "by": "string",
    "severity": "string",
    "title": "string",
    "destination": "string",
    "trigger": "string",
    "answers_escalation": "boolean",
}

#: Facts the wrapper and the body BOTH state, and must therefore agree on.
#: They are written by one emitter in one call, so disagreement means a hand
#: edit after emission — and an approval binds to whichever half its reader
#: happens to take. The SHA split was already refused; `round`, `lineage`
#: and `by` were not, so an artifact could stamp one authorizer on its face
#: and name another in its body.
AUTHORIZATION_DUPLICATED = ("sha", "round", "lineage", "by")

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
# The authorization joined them 2026-09-01, by the same rule rather than by
# exception: it is TOOL-emitted, so an installation stamps it and a reader
# can compare. The verdict's exemption is about having no emitter at all, not
# about being the odd kind out — so a new tool-emitted kind that skipped the
# stamp would be the unprincipled case, not this.
STAMPED_KINDS = ("request", "disposition", "authorization")

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
                   f"`paste` when a person carries the bytes, `git` when "
                   f"both sides reach the remote the branch is pushed to")
    return value


# ------------------------------------------------- review as the default end

# Whether an implementation session ends with a review round by default
# (ruled 2026-09-03, brief `review-by-default`). The tool cannot see a
# session, so nothing here gates a verb: the value is a DECLARATION the
# adapters read, exactly as `debug` is a declaration the emitter reads. It
# is a closed pair rather than a bool because the two states are named in
# the procedure text the adapters render, and `on`/`off` is what a person
# writes in `review.toml`.
#
# WHY THE PAIR IS NOT EXPORTED AS A COLLECTION, stated here rather than left
# to look like an oversight: this repository proves that no shipped document
# restates a closed vocabulary without the restatement being registered and
# compared, and it finds restatements with a proximity detector over the
# members. `on` and `off` are ordinary English words — and substrings of
# ordinary English words — so every document ever written "enumerates" them,
# and registering that as a restatement would assert something false about
# every one of those documents. The vocabulary is closed all the same: it is
# stated once, in the boundary below that admits it.
REVIEW_DEFAULT_ON = "on"
REVIEW_DEFAULT_OFF = "off"
#: Undeclared resolves to `on`: review is the default end of an
#: implementation session, which is the ruling this key exists to carry.
REVIEW_DEFAULT_DEFAULT = REVIEW_DEFAULT_ON

# ------------------------------------------------------------ enforcement

# How a reviewed branch's approval is enforced (ruled 2026-09-03, brief
# `approval-pathway-lock-in`). `pr-approval`: the branch is merged through a
# pull request that carries the approval, so the reviewed work is never the
# default branch itself. `none`: the project commits to its default branch
# and nothing enforces the round — the case this workbench is in, which was
# silent until the key existed.
#
# The tool touches no forge, so this key does exactly one thing inside it:
# `handoff` refuses `pr-approval` on the remote's default branch, because
# that state can produce no PR for the approval to bind to. Opening the PR
# is the author agent's step with `gh`, outside loupe.
ENFORCEMENT_PR_APPROVAL = "pr-approval"
ENFORCEMENT_NONE = "none"
#: Undeclared resolves to `none`: a repository that declared no pathway has
#: none, and inventing one would refuse rounds nobody asked to be enforced.
ENFORCEMENT_DEFAULT = ENFORCEMENT_NONE


def _closed_or_default(value, where: str, allowed: tuple, default: str,
                       what: str) -> str:
    """The `transport_or_default` shape for the other closed `[roles]`
    enums: absent is the default, empty or outside the vocabulary refuses.

    One function rather than one per key, because the four input states and
    the reason each is answered the way it is are identical — stating a
    value emptily is a different act from stating none, and an `or` that
    folds it to the default is the fail-open route R1-F2 closed.
    """
    if value is None:
        return default
    if value not in allowed:
        stated = "explicitly empty" if value == "" else repr(value)
        raise TransportDeclarationError(
            f"{where} declares {what} that is {stated}, not one of "
            f"{list(allowed)}: it is a closed vocabulary, and a value "
            f"outside it names nothing either side can act on",
            remedy=f"a person declares one of {list(allowed)} in the "
                   f"repository's configuration, or removes the key")
    return value


def review_default_or_default(value, where: str) -> str:
    """Whether review is the default end of a session, through one reader.

    The vocabulary is stated HERE, in the one boundary that admits it, for
    the reason recorded above the two members.
    """
    return _closed_or_default(value, where,
                              (REVIEW_DEFAULT_ON, REVIEW_DEFAULT_OFF),
                              REVIEW_DEFAULT_DEFAULT, "a review default")


def enforcement_or_default(value, where: str) -> str:
    """Which approval pathway the repository declares, through one reader.

    Its vocabulary is stated here too, beside its sibling's: two keys ruled
    on the same day, read the same way, so a reader looking for either
    finds both in one place.
    """
    return _closed_or_default(value, where,
                              (ENFORCEMENT_PR_APPROVAL, ENFORCEMENT_NONE),
                              ENFORCEMENT_DEFAULT, "an enforcement")


# ------------------------------------------- what the repository never said

# A local parameter is set, unset, or ABSENT, and absence used to be silent
# (ruled 2026-09-03, brief `config-absent-asks-once`). Absence means one of
# two things in this tool: a REFUSAL — taxonomy, the roles, `review.toml`
# itself, which are ruled and stay refusals — or a built-in DEFAULT the
# repository never chose. The second is what stops being silent here.
#
# The tool has no model and prints JSON, so it cannot ask. What it can do is
# REPORT that it decided something the repository never declared: every
# result of a verb that applied one of these defaults carries a `decide`
# list, one entry per undeclared key, and one adapter rule turns that into
# the single question a person answers once — set it, or unset it — after
# which the key is declared and the asking ends permanently.
#
# The trigger is the REPOSITORY's own declaration and nothing else: an entry
# says review.toml declares nothing for this key, while `applied` reports
# the value this invocation actually used, whatever supplied it. Refusal
# keys are deliberately absent from this table; they are not defaults.
#
# The entries live here rather than beside the config grammar because they
# are vocabulary — the meaning of a key and the exact line that declares it
# — and because `config` derives its schema from `DEFAULTS`, which by
# design does not hold the values below.
DECIDE_TRANSPORT = "roles.transport"
DECIDE_DEBUG = "roles.debug"
DECIDE_REVIEW_DEFAULT = "roles.review_default"
DECIDE_ENFORCEMENT = "roles.enforcement"
DECIDE_ROUND_CAP = "limits.round_cap"
DECIDE_TOKEN_BUDGET = "limits.token_budget"

#: (key, meaning, applied default, the value that SETS it, the value that
#: UNSETS it or None where the key has no off state). `applied` is None for
#: `limits.round_cap` alone: its built-in value is the config layer's, and
#: restating it here would be the second source this table exists to avoid —
#: `config.Config.decisions` fills it from `DEFAULTS`.
DECIDE_KEYS = (
    (DECIDE_TRANSPORT,
     "how the two ends of a round exchange the envelope: `path` when they "
     "read the same filesystem, `paste` when a person carries the bytes, "
     "`git` when both reach the remote the branch is pushed to",
     TRANSPORT_EMISSION_DEFAULT, TRANSPORT_EMISSION_DEFAULT, None),
    (DECIDE_DEBUG,
     "whether every round also asks the reviewer to critique the tool "
     "itself, which spends reviewer attention on the tool rather than on "
     "the change",
     False, True, False),
    (DECIDE_REVIEW_DEFAULT,
     "whether an implementation session ends with a review round without "
     "being asked, or only when someone asks for one",
     REVIEW_DEFAULT_DEFAULT, REVIEW_DEFAULT_ON, REVIEW_DEFAULT_OFF),
    (DECIDE_ENFORCEMENT,
     "whether the reviewed branch is merged through a pull request that "
     "carries the approval, or nothing enforces the round",
     ENFORCEMENT_DEFAULT, ENFORCEMENT_PR_APPROVAL, ENFORCEMENT_NONE),
    (DECIDE_ROUND_CAP,
     "how many rounds one lineage runs before the tool reports what the "
     "loop is failing to close instead of opening another",
     None, None, None),
    (DECIDE_TOKEN_BUDGET,
     "the cumulative token budget one lineage may spend before the budget "
     "breaker fires; undeclared is uncounted, which is neither zero nor "
     "infinite",
     None, 200000, None),
)


def toml_line(key: str, value) -> str:
    """The exact `review.toml` line that declares `key` as `value`, under
    the section its dotted name gives. Scalars only — every key in
    `DECIDE_KEYS` is a string, a bool or a whole number."""
    name = key.split(".")[-1]
    if isinstance(value, bool):
        return f"{name} = {'true' if value else 'false'}"
    if isinstance(value, str):
        return f'{name} = "{value}"'
    return f"{name} = {value}"


def decisions(declared, applied=None) -> list:
    """One `decide` entry per key of `DECIDE_KEYS` the repository did not
    declare, in table order.

    `declared` is the set of dotted keys `review.toml` states — a declared
    key produces NO entry, which is what ends the asking permanently.
    `applied` maps a dotted key to the value this invocation actually used,
    where the caller knows it; every other key reports its built-in default.
    """
    applied = applied or {}
    out = []
    for key, meaning, default, set_value, unset_value in DECIDE_KEYS:
        if key in declared:
            continue
        entry = dict(key=key, meaning=meaning,
                     applied=applied.get(key, default))
        # `limits.round_cap` states no value of its own above: its built-in
        # cap is the config layer's, so the line that declares it is
        # rendered from what was applied rather than from a second copy.
        proposed = set_value if set_value is not None else entry["applied"]
        entry["set"] = (toml_line(key, proposed)
                        if proposed is not None else None)
        entry["unset"] = (toml_line(key, unset_value)
                          if unset_value is not None else None)
        out.append(entry)
    return out

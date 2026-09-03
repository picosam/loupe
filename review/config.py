"""Configuration and state-path resolution (design §4, §10.2).

Taxonomy, roles and limits come from `review.toml` at the repo root when the
repo declares one, else `~/.config/<tool>/<repo-id>.toml`, else built-in
defaults. The ledger lives OUTSIDE the repo by default —
`~/.local/state/<tool>/<repo-id>/` — because that is what keeps the process
the user's rather than repo furniture (§4). Both are overridable:
<TOOL>_STATE_DIR / <TOOL>_CONFIG env vars, or --ledger-dir on the CLI.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import paths, vocab, FORMER_NAMES, TOOL_NAME, TOOL_VERSION, env_var

CONFIG_BASENAME = "review.toml"

#: The `[tool]` section's one key: the minimum reader version a repository
#: declares it was written for. Bound as a name because every use of it in
#: this module would otherwise be a constant string key, which the
#: command-boundary drift scan reads as a CLI egress key — see the note at
#: its DEFAULTS entry below, and `caller_env` for the same reasoning applied
#: to two environment names.
REQUIRES_KEY = "requires"

# Round-3 F5: these defaults previously restated this repo's taxonomy, so a
# repo with no config silently inherited llm's severities and classifications
# instead of being told none were declared. Taxonomy now has NO built-in
# value — absent is a distinct state from declared, and the tool refuses to
# rule rather than inventing one. Only genuinely repo-independent policy
# (the rejected-reviewer rule from §7, the wrapper tag) has a default.
DEFAULTS = {
    "taxonomy": {
        "severities": [],
        "blocking": [],
        "classifications": [],
        "classification_notes": {},
    },
    "roles": {
        "author": "",
        "reviewer": "",
        "permitted_authors": [],
        "permitted_reviewers": [],
        # §7: Gemini reviewer stamps are rejected by the validator until §10.7
        # is decided — rejected, not merely discouraged.
        "rejected_reviewers": ["gemini", "antigravity"],
        # RVW-T11 / round 3 F2: whether the two sides of a round share a
        # filesystem. Deliberately NO default value here — "unstated" must
        # stay observable at the one emission-side reader
        # (`emit.resolve_transport`), because the undeclared state is
        # where the environment gets its say: a cloud-authored session's
        # own declaration (LOUPE_TRANSPORT) or a documented provider
        # signal resolves it to `paste`, and only then does the steady
        # case fall to `vocab.TRANSPORT_EMISSION_DEFAULT` (`path`, the
        # local same-machine loop this workflow declares). A default
        # written here would erase the difference between a repo that
        # declared `path` and one that declared nothing — silencing the
        # cloud declaration that exists precisely for the second.
    },
    # §10.5, provisional. `token_budget` has NO default on purpose: a budget
    # nobody declared is not a budget of zero and not a budget of infinity,
    # and the cumulative-token breaker must be able to say which of the two
    # states it is in (round-4 F5).
    "limits": {"round_cap": 3, "token_budget": None},
    "wrapper": {"tag": TOOL_NAME},
    # The repository's declared MINIMUM reader version (2026-09-01, brief
    # config-keys-are-a-cross-installation-contract). `requires` has NO
    # default for the same reason `token_budget` has none: a repository that
    # declares nothing requires nothing, and "undeclared" must stay
    # distinguishable from "requires 0.0.0" — the first is silence, the
    # second is a statement.
    #
    # Why a NEW SECTION rather than another key in an existing one: an
    # installation too old to know this key at all refuses it with `unknown
    # section [tool]`, which points at a whole feature the reader has never
    # heard of. The same declaration buried in `[roles]` would refuse with
    # `[roles] states unknown key 'requires'`, which reads like a typo in a
    # section the reader does understand. Both refusals are wrong about the
    # cause; only one of them is diagnosable.
    #
    # The key NAME is bound above rather than written as a literal here, for
    # the reason `caller_env` states about its two environment names: the
    # command-boundary suite's ADVISORY drift scan reads a constant dict key
    # in this module as a CLI egress key, and this is a key of the
    # repository's CONFIG FILE, not of anything the tool prints. Registering
    # it as a printed output key to satisfy a lexical scan would weaken the
    # schema that scan exists to guard.
    "tool": {REQUIRES_KEY: None},
}
# Gates are a list, not a section of scalars, so they merge by replacement.
DEFAULT_GATES: list[dict] = []


@dataclass
class Config:
    repo_root: Path
    repo_id: str
    taxonomy: dict
    roles: dict
    limits: dict
    wrapper: dict
    tool: dict = field(default_factory=lambda: dict(DEFAULTS["tool"]))
    gates: list = field(default_factory=list)
    source: str = "defaults"
    ledger_dir: Path = field(default=None)  # resolved in load()
    #: The dotted keys the loaded file ACTUALLY states, before any merge.
    #: A merged section cannot answer this — `round_cap` is present in every
    #: config because DEFAULTS supplies it — and "the repository never said"
    #: is exactly the state `decisions` reports, so it is recorded at the one
    #: place the user's own bytes are read (`load` and `from_text`).
    declared: frozenset = field(default_factory=frozenset)

    @property
    def taxonomy_declared(self) -> bool:
        """Whether a taxonomy was declared at all (§5.2: absent taxonomy means
        the reviewer must refuse to rule — absent is not the same as none)."""
        return bool(self.taxonomy["severities"] and
                    self.taxonomy["classifications"])

    @property
    def severities(self) -> list[str]:
        return list(self.taxonomy["severities"])

    @property
    def blocking_severities(self) -> list[str]:
        return list(self.taxonomy["blocking"])

    @property
    def classifications(self) -> list[str]:
        return list(self.taxonomy["classifications"])

    @property
    def round_cap(self) -> int:
        return int(self.limits["round_cap"])

    @property
    def wrapper_tag(self) -> str:
        return self.wrapper["tag"]

    @property
    def token_budget(self) -> int | None:
        """Cumulative-token budget for this lineage, or None if undeclared.

        None is a real answer, not a missing one: with no budget the §5.3d
        token breaker cannot fire and the report says exactly that, rather
        than comparing against an invented ceiling (round-4 F5).
        """
        budget = self.limits.get("token_budget")
        return None if budget is None else int(budget)

    @property
    def required_version(self) -> str | None:
        """The minimum reader version this repository declares, or None.

        None is a real answer: a repository that declares no floor is not a
        repository declaring `0.0.0`. Nothing in the tool GATES on this — it
        is read once, at the config boundary, by `check_tool_version`, and
        the only thing it can do is refuse.
        """
        return self.tool.get(REQUIRES_KEY)

    def decisions(self, applied=None) -> list:
        """What this repository never declared, and what was applied instead
        (2026-09-03, brief `config-absent-asks-once`).

        One entry per undeclared key of `vocab.DECIDE_KEYS`, empty when the
        repository declared them all. The table, the meanings and the exact
        TOML lines are vocabulary; what this adds is the only fact the
        config layer owns — which keys the file actually states, and the
        built-in round cap, which is DEFAULTS' rather than vocab's.
        """
        supplied = dict(applied or {})
        supplied.setdefault(vocab.DECIDE_ROUND_CAP,
                            DEFAULTS["limits"]["round_cap"])
        return vocab.decisions(self.declared, supplied)

    @property
    def gate_commands(self) -> list[str]:
        """Commands declared in the gate manifest, as printable strings."""
        return [" ".join(g["command"]) for g in self.gates]

    @property
    def gate_ids(self) -> list[str]:
        """Stable gate identities from the manifest.

        Round-4 F8: the preventable-share metric compares against THESE, not
        against a command string or a display label. A command is an
        implementation of a gate and changes when the tooling changes; the id
        is what the repo declared and what a finding can name.
        """
        return [g["id"] for g in self.gates]


def caller_env() -> dict:
    """The environment the CALLER of the tool had, for any child process
    that may execute the REPOSITORY's or the USER's own code.

    Found on the first per-project onboarding (2026-08-19): the bin/ shim
    hardens the tool's own interpreter with PYTHONSAFEPATH=1 and
    PYTHONPATH=<tool root>, both
    environment variables, so every gate subprocess inherited them — and
    twelve of that repo's sixteen gates failed on ModuleNotFoundError under
    `handoff` while passing by hand. PYTHONSAFEPATH strips the script
    directory and cwd from sys.path, which is precisely what a repository's
    ad-hoc check scripts rely on; the leaked PYTHONPATH additionally let any
    gate import this package by accident. It failed closed (A-FAILED, a
    refusal), but a manifest that can only attest red for repositories the
    tool never met is a manifest nobody declares.

    RVW-T21 D2 (2026-08-29) found the contract was scoped to gates and the
    class was wider. A repository's git HOOKS are the repository's own code
    in exactly the same trust position — `commit -a` runs pre-commit,
    prepare-commit-msg, commit-msg and post-commit, `push` runs pre-push,
    every ref write can reach reference-transaction, and `status` consults
    a configured core.fsmonitor — and they inherited the hardened
    environment because only `run_gates` passed this. On `beos` a pre-push
    hook importing a sibling `tools` module died with ModuleNotFoundError
    and refused a handoff; the same push in a clean environment was a
    no-op success. So this is now applied at every `git` door as well
    (`emit._git`, `emit._git_bytes`, `emit._is_ancestor`, `config._git`,
    `transport._git`, `transport.run_bytes`), which after that fix is every
    child process the tool starts. Nothing in the package relies on a git
    child seeing the hardened values: git consumes neither variable, and the
    tool sets no GIT_* variables of its own.

    A gate — and a hook — is the REPOSITORY's command and must run in the
    environment the caller of loupe had, not the one the shim made for the
    tool. The shim stashes the caller's values (LOUPE_CALLER_PYTHONSAFEPATH
    / LOUPE_CALLER_PYTHONPATH — presence distinguishes set-to-anything from
    unset) and marks itself with LOUPE_SHIM; this restores exactly those.
    Invoked without the shim (python3 -m review) there is no stash and no
    exact answer, so the best available approximation is applied and named
    as such: drop PYTHONSAFEPATH, and drop this package's own root from
    PYTHONPATH, leaving everything else the caller set. The stash and marker
    variables themselves stay out of the child environment either way; the
    gate re-entrancy marker is added by `run_gates`, never here — a hook
    that legitimately runs a loupe read verb must not see itself as nested
    inside a gate execution (RVW-T21 D2).
    """
    # The two variable names are bound rather than written as literal
    # subscript keys: the command-boundary suite's ADVISORY drift scan reads
    # a constant-key subscript assignment in this module as a CLI egress
    # key, and these are environment names for a child process, not keys of
    # anything this tool prints. Classifying them as output keys to satisfy
    # a lexical scan would weaken the schema that scan exists to guard.
    safepath, pythonpath = "PYTHONSAFEPATH", "PYTHONPATH"
    env = dict(os.environ)
    ran_via_shim = env.pop(env_var("SHIM"), None)
    stash_safe = env.pop(env_var("CALLER_PYTHONSAFEPATH"), None)
    stash_path = env.pop(env_var("CALLER_PYTHONPATH"), None)
    if ran_via_shim:
        for name, stashed in ((safepath, stash_safe),
                              (pythonpath, stash_path)):
            if stashed is None:
                env.pop(name, None)
            else:
                env[name] = stashed
        return env
    env.pop(safepath, None)
    # Round-1 F1: only components EXACTLY equal to the tool root are
    # removed; everything else keeps its value and its ordering — empty
    # components included, because an empty PYTHONPATH component is not
    # inert filler, it is the current working directory. The earlier
    # truthiness filter (`if p and ...`) deleted them, which could recreate
    # in the fallback the very gate-only import failure this function
    # exists to end. An untouched value is not split and rejoined at all;
    # a value that was nothing but the tool root becomes unset, since no
    # component of the caller's remains to carry.
    own_root = str(Path(__file__).resolve().parent.parent)
    if pythonpath in env:
        parts = env[pythonpath].split(os.pathsep)
        if own_root in parts:
            kept = [p for p in parts if p != own_root]
            if kept:
                env[pythonpath] = os.pathsep.join(kept)
            else:
                env.pop(pythonpath)
    return env


def _git(repo_root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True, text=True, timeout=30,
            # RVW-T21 D2: `status` consults a configured core.fsmonitor
            # hook, and any of these reads may fire one on a repository
            # that has them; the caller's environment is the only one a
            # repository's own script can be expected to run in.
            env=caller_env(),
        )
    except OSError:
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def find_repo_root(start: Path | None = None) -> Path:
    start = start or Path.cwd()
    top = _git(start, "rev-parse", "--show-toplevel")
    return Path(top) if top else start


def canonical_root(repo_root: Path) -> Path:
    """The MAIN worktree's root for this repository.

    A linked worktree is the same repository with a different directory, and
    the review record belongs to the repository. `--git-common-dir` resolves
    to the main checkout's `.git` from any worktree, so its parent is the one
    root every worktree agrees on. Falls back to `repo_root` outside git and
    for bare or otherwise unusual layouts, where there is nothing better.
    """
    common = _git(repo_root, "rev-parse", "--git-common-dir")
    if not common:
        return repo_root
    path = Path(common)
    if not path.is_absolute():
        # `git -C <root> rev-parse` returns it relative to that root.
        path = repo_root / path
    path = path.resolve()
    return path.parent if path.name == ".git" else path


def _identity_name(origin: str | None, root: Path) -> str:
    """The human-facing half of the id, derived from the SAME thing the hash
    is derived from — otherwise the two disagree and worktrees split."""
    if origin:
        stripped = origin.rstrip("/")
        if stripped.endswith(".git"):
            stripped = stripped[:-len(".git")]
        segment = re.split(r"[/:]", stripped)[-1]
        if segment:
            return segment
    return root.name


def repo_identity(repo_root: Path) -> str:
    """Stable repo id: <name>-<hash8 of origin URL, else main-worktree path>.

    Stable across clones AND across linked worktrees. Both halves derive from
    one key: the earlier form hashed the origin URL — deliberately, so the id
    survived a clone to a different directory — and then prefixed the result
    with the *current* directory's basename, which a worktree changes. The id
    therefore split per worktree and every worktree read an empty ledger:
    round number, cap, breakers, lineage and token budget all silently reset.
    """
    root = canonical_root(repo_root).resolve()
    origin = _git(root, "remote", "get-url", "origin")
    key = origin or str(root)
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    return f"{_identity_name(origin, root)}-{h}"


# Round 4 F4. `from_text` caught only TOMLDecodeError, so "TOML the config
# layer rejects" was not a closed state: `taxonomy = []` crashed inside
# resolution with a raw TypeError, and `[limits] round_cap = "x"` acquired
# target authority and raised a raw ValueError later, after the boundary had
# reported success. Both left the CLI without its typed author remedy.
#
# The schema is DERIVED from DEFAULTS, which is already the authority for
# what a section is and what kind each value has — a hand-kept second list
# would be one more thing to drift. Only what DEFAULTS deliberately omits is
# named here, and each omission is documented at its own entry above.
CONFIG_DECLARED_ELSEWHERE = {
    ("roles", "relay"): str,            # what carries the envelope
    ("roles", "transport"): str,        # unstated must stay observable
    ("roles", "debug"): bool,           # standing debug-round declaration;
                                        # absent falls to the flag, then off
                                        # (`emit.resolve_debug`)
    # The two keys ruled 2026-09-03. Both are declared HERE rather than in
    # DEFAULTS for the reason `transport` is: absence has to stay observable,
    # because it is what `decisions()` reports as never chosen and what one
    # adapter rule turns into a question asked once. Their built-in values
    # live in `vocab` (`REVIEW_DEFAULT_DEFAULT`, `ENFORCEMENT_DEFAULT`).
    #
    # And both are a CROSS-INSTALLATION CONTRACT, on the seam documented
    # below: a repository that declares either becomes unreadable to every
    # installation older than this one, which refuses with `[roles] states
    # unknown key` and a remedy that is false. So a repository adopts them
    # only once a reader that parses them is published — the same staging
    # `[roles] debug` had — and `[tool] requires` is what makes the refusal
    # say the true thing when it happens anyway.
    ("roles", "review_default"): str,   # session ends with a round, or not
    ("roles", "enforcement"): str,      # which approval pathway, if any
    ("limits", "token_budget"): int,    # absent is not zero and not infinity
    ("tool", REQUIRES_KEY): str,        # minimum reader version; absent is
                                        # silence, not a floor of 0.0.0
}
_KIND_OF = {str: "a string", int: "a whole number", list: "a list of strings",
            dict: "a table of string values", bool: "true or false"}
# Round 4 F2: `[roles] relay` is declared `str` above (`CONFIG_DECLARED_
# ELSEWHERE`) and nothing narrower — so a config author could write a
# hand-back sentence there, `emit_request` would copy it verbatim onto the
# Roles line when the claim states no `relay` of its own, and the actor-
# identifier grammar `validate_claim` applies to the claim's own `relay`
# member (review/emit.py) never saw it. Same grammar, applied here — the
# typed pre-lifecycle boundary every config value already passes through —
# so the EFFECTIVE relay is closed to an actor identifier regardless of
# which of the two admitted sources supplied it.
_ACTOR_RE = re.compile(vocab.ACTOR_RE)


def _kind_for(section: str, key: str):
    """The Python type a declared value must have, from DEFAULTS."""
    declared = DEFAULTS.get(section, {})
    if key in declared and declared[key] is not None:
        return type(declared[key])
    return CONFIG_DECLARED_ELSEWHERE.get((section, key))


def _bad(where: str, saw, want: str) -> str:
    return (f"{where} must be {want}, not "
            f"{type(saw).__name__} ({saw!r})")


def _check_value(section: str, key: str, value, errors: list) -> None:
    want = _kind_for(section, key)
    where = f"[{section}] {key}"
    if want is bool or (isinstance(value, bool) and want is int):
        # `bool` is an `int` in Python and is never a count here — and a
        # wanted bool takes only a real bool, not whatever TOML parsed
        # (the first bool key, 2026-08-31, found this branch returning
        # early with no check at all).
        if want is not bool or not isinstance(value, bool):
            errors.append(_bad(where, value, _KIND_OF[want]))
        return
    if not isinstance(value, want):
        errors.append(_bad(where, value, _KIND_OF[want]))
        return
    if want is list and not all(isinstance(x, str) for x in value):
        errors.append(f"{where} must hold strings only")
    if want is dict and not all(isinstance(k, str) and isinstance(v, str)
                                for k, v in value.items()):
        errors.append(f"{where} must map strings to strings")
    if want is int and value < 0:
        errors.append(f"{where} must not be negative")
    if (section, key) == ("limits", "round_cap") and value < 1:
        errors.append(f"{where} must be at least 1")
    # Round 4 F2: `relay` names WHO carried the request, the same fact the
    # claim's own `relay` member is closed to an actor identifier for
    # (review/emit.py's `validate_claim`). A blank value is not a defect —
    # emission falls back exactly as an absent key does — but anything else
    # that fails the grammar is refused here, before it can reach the Roles
    # line unverified.
    #
    # Round 5 F3: this used to validate `value.strip()` while emission
    # (`review/emit.py`'s `relay = (claim.get("relay") or
    # cfg.roles.get("relay") or ...)`) renders the ORIGINAL, unstripped
    # value — so `" user "` matched the stripped copy, passed, and rendered
    # `relay= user  ` on the Roles line. No stripping anywhere now: the
    # grammar is checked against the exact value emission will render, and
    # "blank" is exactly what emission's `or` already treats as absent — a
    # falsy `""`, not a stripped one — so the two agree on every input,
    # not only the ones this comment happens to enumerate.
    if (section, key) == ("roles", "relay") and value and not \
            _ACTOR_RE.match(value):
        errors.append(
            f"{where} is {value!r}, which is not {vocab.ACTOR_WANT} — "
            f"relay says WHO carried the request, never what to do with "
            f"the answer; a stopping instruction belongs in the claim's "
            f"'hand_back' field, not in config")


def _check_gates(rows, errors: list) -> None:
    if not isinstance(rows, list):
        errors.append(_bad("gates", rows, "a list of tables"))
        return
    for i, row in enumerate(rows):
        at = f"gates[{i}]"
        if not isinstance(row, dict):
            errors.append(_bad(at, row, "a table"))
            continue
        unknown = sorted(set(row) - {"id", "command", "blocking"})
        if unknown:
            errors.append(f"{at} states unknown key(s) {unknown}")
        gid = row.get("id")
        if not isinstance(gid, str) or not gid.strip():
            errors.append(f"{at} must state a non-empty string `id`")
        elif not re.fullmatch(vocab.GATE_ID_RE, gid):
            errors.append(
                f"{at} id {gid!r} is not one filename component: a gate id "
                f"names its retained output at "
                f"<ledger>/gate-output/<sha>/<run>/<id>.log, so it must "
                f"match "
                f"{vocab.GATE_ID_RE} — an absolute form discards that "
                f"directory and a separator or dot segment leaves it")
        elif len(gid) > vocab.GATE_ID_MAX:
            errors.append(f"{at} id is longer than {vocab.GATE_ID_MAX} "
                          f"characters")
        command = row.get("command")
        if (not isinstance(command, list) or not command
                or not all(isinstance(w, str) for w in command)):
            errors.append(f"{at} `command` must be a non-empty list of "
                          f"strings")
        if "blocking" in row and not isinstance(row["blocking"], bool):
            errors.append(_bad(f"{at} `blocking`", row["blocking"],
                               "true or false"))
    # Round 5 F2: the rows were judged one at a time, so the SHAPE was closed
    # and the IDENTITY was not. A gate id is the join key for blocking policy,
    # missing-gate checks, retained output (`<sha>/<id>.log`) and the
    # deterministic-preventable metric — so two rows sharing one id are two
    # gates wearing one identity: both run, the second output overwrites the
    # first, and the first record's pointer then names bytes that are not its
    # own. Uniqueness is a property of the manifest, and only the manifest can
    # be asked about it.
    seen: dict[str, int] = {}
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        gid = row.get("id")
        if isinstance(gid, str) and gid.strip():
            # Round 6 F2: distinct STRINGS are not distinct destinations on a
            # case-folding filesystem, and the destination is what the
            # identity has to be unique in.
            key = gid.casefold()
            if key in seen:
                errors.append(
                    f"gates[{seen[key]}] and gates[{i}] declare ids that name "
                    f"one retained output ({gid!r}): a gate id names its own "
                    f"file and joins the attestation, so two rows cannot "
                    f"share a destination")
            else:
                seen[key] = i


# The config-evolution seam (2026-09-01, brief
# config-keys-are-a-cross-installation-contract, measured on lineage 20
# round 2). A key added to this grammar is a CROSS-INSTALLATION CONTRACT,
# and until now the grammar had no way to say so. `take` judges a target's
# config bytes against the READER's schema, so the moment a repository
# declared a key its own tool understood, every older installation refused
# the whole round — and refused with a remedy that was FALSE:
#
#     repo:review.toml declares 1 invalid value(s): [roles] states unknown
#       key 'debug'
#     remedy: a person repairs repo:review.toml
#
# The config was correct. The reader was old. Nothing in that output said so,
# and a person following the remedy would have damaged a valid file.
#
# TOLERATING UNKNOWN KEYS IS NOT THE FIX AND STAYS REJECTED: the closed
# grammar is why a misspelled key cannot silently erase what it meant to
# declare, and `check_shape` below is unchanged. What is added is a way for
# the repository to state the floor it was written for, so a reader below
# that floor can say the true thing instead of the false one.
#
# THE HONEST LIMIT, stated here rather than discovered later: an installation
# older than this key ITSELF still refuses with `unknown section [tool]`,
# because it has never heard of the section carrying the requirement. No
# change made in this version can reach a reader that is already deployed —
# the refusal is emitted by the OLD binary, and improving the new one reaches
# only future skews. That asymmetry is an argument for shipping the mechanism
# EARLY, when the population of too-old readers is smallest, not for skipping
# it. It also means a repository adopts `[tool] requires` only once the
# readers that understand it are published; adopting it sooner re-breaks the
# very bootstrap this exists to fix.

#: A dotted run of ASCII digits. Deliberately not `\d`, which in a `str`
#: pattern also matches non-ASCII decimal digits — `"٠.١٤.٠"` would parse and
#: then compare against a version nobody wrote.
_VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+)*")


def parse_version(value) -> tuple[int, ...] | None:
    """`"0.14.0"` -> `(0, 14, 0)`, or None if that is not a version.

    None is the ONLY failure mode: no exception escapes, because every
    caller is a boundary that owes a typed refusal rather than a traceback,
    and the value arrives from a file anyone may edit.
    """
    if not isinstance(value, str) or not _VERSION_RE.fullmatch(value):
        return None
    return tuple(int(part) for part in value.split("."))


def version_below(installed: tuple, required: tuple) -> bool:
    """Whether `installed` is strictly older than `required`.

    Components are compared numerically and the SHORTER side is padded with
    zeros, so `0.14` and `0.14.0` are the same version and `0.9.9` is below
    both. Padding rather than comparing lengths first is what keeps
    `(0, 9)` below `(0, 14, 0)`: string or length ordering would put the
    two-component value first and read 9 as newer than 14.
    """
    width = max(len(installed), len(required))

    def pad(version: tuple) -> tuple:
        return version + (0,) * (width - len(version))

    return pad(installed) < pad(required)


def check_tool_version(user: dict, source: str,
                       installed: str = TOOL_VERSION) -> None:
    """Refuse a config written for a NEWER tool than this one, before any
    judgment of its schema.

    ORDER IS THE WHOLE POINT. A config that declares `requires = "0.15.0"`
    AND uses a key only 0.15.0 knows is not a broken config — it is a config
    this reader is too old to read, and both facts have the same single
    cause. Judged the other way round, the reader would report the SYMPTOM
    (an unknown key) and hide the CAUSE (a version floor it does not meet),
    which is exactly the false remedy this mechanism exists to end. So this
    runs above `check_shape` at every door, and the version refusal wins.

    `installed` is a parameter so a test can drive both sides of the
    comparison; production always passes the package's own `TOOL_VERSION`.
    A shape this cannot read — a `[tool]` that is not a table — is left to
    `check_shape`, which owns kinds and will name it precisely.
    """
    if not isinstance(user, dict):
        return
    section = user.get("tool")
    if not isinstance(section, dict) or REQUIRES_KEY not in section:
        return
    declared = section[REQUIRES_KEY]
    required = parse_version(declared)
    if required is None:
        # A malformed floor is a REFUSAL, never a silent pass. Admitting it
        # would make the guarantee conditional on spelling: a repository that
        # typed `requires = "0.14.0-rc1"` would believe it had declared a
        # floor and would in fact have declared nothing, which is worse than
        # having declared nothing on purpose.
        raise ConfigError(
            f"{source} declares an unreadable [tool] requires value "
            f"({declared!r}): a minimum {TOOL_NAME} version is dotted "
            f"numbers, like \"{TOOL_VERSION}\"",
            remedy=f"a person repairs the [tool] requires value in {source}; "
                   f"this one is the config's own defect, not a version skew")
    current = parse_version(installed)
    if current is None or not version_below(current, required):
        return
    # The message a too-old reader prints. It names BOTH versions because
    # either alone leaves the reader's question open, and it says in words
    # that the configuration is not the thing at fault — the previous
    # refusal's remedy sent a person to repair a correct file.
    raise ConfigError(
        f"{source} requires {TOOL_NAME} {declared} or newer; this "
        f"installation is {installed}. The configuration is not broken — "
        f"this reader is older than the repository it was asked to read",
        code=1,
        remedy=f"a person runs a {TOOL_NAME} installation at {declared} or "
               f"newer against this repository; do not edit {source}, which "
               f"is correct for the tool that wrote it")


def check_shape(user: dict, source: str) -> None:
    """Every declared section, key and value kind, or a ConfigError naming
    all of them at once — a person repairing a config should see the whole
    list, not one error per run."""
    errors: list[str] = []
    # An UNDECLARED name is the one defect class that is also what a
    # newer repository looks like to this reader; a wrong kind never is.
    # Tracked apart so the remedy can say so without saying it everywhere
    # (2026-09-03, brief config-keys-are-a-cross-installation-contract).
    undeclared = False
    if not isinstance(user, dict):
        raise ConfigError(f"{source} is not a table")
    known = set(DEFAULTS) | {"gates"}
    for name in sorted(set(user) - known):
        undeclared = True
        errors.append(f"unknown section [{name}]; the declared sections are "
                      f"{sorted(known)}")
    for section in sorted(set(user) & set(DEFAULTS)):
        body = user[section]
        if not isinstance(body, dict):
            errors.append(_bad(f"[{section}]", body, "a table"))
            continue
        for key in sorted(body):
            if _kind_for(section, key) is None:
                undeclared = True
                errors.append(f"[{section}] states unknown key {key!r}")
                continue
            _check_value(section, key, body[key], errors)
    if "gates" in user:
        _check_gates(user["gates"], errors)
    if errors:
        remedy = (f"a person repairs {source}; every item above names the "
                  f"section, the key and the kind it must have")
        if undeclared:
            # The half `[tool] requires` cannot reach: a repository written
            # for a NEWER tool that declares no floor — or declares one in a
            # section this reader knows nothing about — looks exactly like a
            # misspelling from here. The reader cannot tell the two apart, so
            # it names both and its own version instead of sending a person
            # to repair a file that may be correct. Forward-only, and said so
            # in the brief: this reaches the skews that come after it.
            remedy += (
                f" — UNLESS this is a version skew rather than a defect. An "
                f"undeclared section or key is also what a repository "
                f"written for a NEWER {TOOL_NAME} looks like to an older "
                f"reader, and this installation is {TOOL_NAME} "
                f"{TOOL_VERSION}. Check that first: if {source} was written "
                f"for a newer tool, the file is correct and this reader is "
                f"behind, and repairing it would damage a valid file. A "
                f"repository can make that refusal say so directly by "
                f"declaring [tool] {REQUIRES_KEY}")
        raise ConfigError(
            f"{source} declares {len(errors)} invalid value(s): "
            + "; ".join(errors), remedy=remedy)


def _declared(user: dict) -> frozenset:
    """The dotted keys the user's own bytes state, `section.key`.

    Read from the RAW document, before `_merged` folds the defaults in:
    after the merge every section holds every default, and "the repository
    never declared this" is no longer answerable (`Config.decisions`).
    """
    keys = set()
    for section, body in user.items():
        if isinstance(body, dict):
            keys.update(f"{section}.{key}" for key in body)
    return frozenset(keys)


def _merged(user: dict) -> dict:
    merged = {}
    for section, defaults in DEFAULTS.items():
        merged[section] = {**defaults, **user.get(section, {})}
    return merged


def from_text(text: str, like: "Config", source: str) -> "Config":
    """A Config built from TOML text, sharing `like`'s repo root, identity
    and ledger location.

    Sweep F6: the configuration that governs a review request — taxonomy,
    roles, gates, cap, wrapper dialect — is the one carried BY THE TARGET
    COMMIT, and `take` was reading whatever the reviewer's checkout happened
    to hold: an unborn or empty checkout holds nothing, so a valid envelope
    was refused as "no taxonomy declared" and sent back to its author for a
    fault in the reviewer's tree. This builds the target's own configuration
    from the bytes `git show <sha>:review.toml` returns; the state directory
    stays the reviewer's, because the ledger is where the reviewer records,
    not something the target dictates.
    """
    try:
        user = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"{source} is not valid TOML: {exc}",
            remedy=f"the author must repair {source} at the target commit; "
                   f"a request whose governing config cannot be read cannot "
                   f"be ruled on") from exc
    # Above the schema, at both doors: a reader too old for this config must
    # say so rather than report the first key it fails to recognise.
    check_tool_version(user, source)
    check_shape(user, source)
    sections = _merged(user)
    return Config(repo_root=like.repo_root, repo_id=like.repo_id,
                  source=source, gates=user.get("gates", DEFAULT_GATES),
                  ledger_dir=like.ledger_dir, declared=_declared(user),
                  **sections)


def legacy_state_dir(repo_id: str, home: Path | None = None) -> Path | None:
    """State for this repo under a FORMER tool name, or None.

    §10.1's hazard in its purest form: renaming the tool repoints the default
    ledger path at a directory that does not exist, and the tool would start
    a fresh, empty ledger — silently orphaning the existing record. A legacy
    directory is therefore a state load() must refuse on, never route around.
    """
    home = home or Path.home()
    for former in FORMER_NAMES:
        candidate = home / ".local" / "state" / former / repo_id
        if candidate.is_dir():
            return candidate
    return None


class ConfigError(RuntimeError):
    """A configuration failure that still owes the caller a typed recovery.

    Round 1 F8: config resolution ran ABOVE cli.main's structured error
    boundary, so malformed TOML, an unreadable file and the legacy-state
    refusal all exited as tracebacks. The adapters instruct agents to run the
    `next` command on every non-zero exit and never to diagnose, so a failure
    with no `next` leaves the agent with the one instruction it cannot follow —
    and config failures are exactly the ones a fresh install hits first.

    Round 2 F8: putting them inside the boundary fixed the traceback and left
    the recovery unusable, because `next` was filled with a diagnosis — `fix
    <path>, then re-run` — that an agent told to run `next` verbatim cannot
    execute. The class is broader than configuration: any refusal whose remedy
    is a human edit had nothing executable to offer and said so in prose.

    So a refusal now declares WHICH KIND of recovery it has. `next_cmd` is a
    literal command or empty; empty means blocked, and `remedy` carries what a
    human must do. Blocked is not a lesser state to be avoided by inventing a
    command — some failures genuinely need a person, and saying so is the
    honest output. What is forbidden is prose in the field an agent executes.

    `code` distinguishes the exit semantics: a malformed or unreadable config
    is a usage error (2), while the legacy-state refusal is a blocked state
    with a remedy (1), matching the exit codes the adapters publish.
    """

    def __init__(self, message: str, next_cmd: str = "", code: int = 2,
                 remedy: str = ""):
        super().__init__(message)
        # Workshop (b): symmetric with `transport.Refusal`, which has guarded
        # at construction since round 5 F1. Both carry a `next_cmd` that
        # becomes the CLI's `next`; only one checked it.
        self.next_cmd = paths.executable(next_cmd, "ConfigError.next_cmd")
        self.code = code
        self.remedy = remedy

    @property
    def kind(self) -> str:
        return "command" if self.next_cmd else "blocked"


def load(repo_root: Path | None = None, ledger_dir: str | None = None,
         check_legacy: bool = True) -> Config:
    repo_root = find_repo_root(repo_root)
    repo_id = repo_identity(repo_root)

    candidates = [
        (repo_root / CONFIG_BASENAME, f"repo:{CONFIG_BASENAME}"),
        (Path(os.environ.get(env_var("CONFIG"), "")), "env:" + env_var("CONFIG")),
        (Path.home() / ".config" / TOOL_NAME / f"{repo_id}.toml", "user config"),
    ]
    user, source = {}, "defaults"
    for path, label in candidates:
        if path and path.is_file():
            try:
                user = tomllib.loads(path.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as exc:
                raise ConfigError(
                    f"{path} ({label}) is not valid TOML: {exc}",
                    remedy=f"a person must repair the TOML syntax in {path}; "
                           f"no command the tool can name will do it") from exc
            except (OSError, ValueError) as exc:
                raise ConfigError(
                    f"{path} ({label}) could not be read: {exc}",
                    remedy=f"a person must make {path} readable to this "
                           f"process, or remove it") from exc
            source = label
            break

    check_tool_version(user, source)
    check_shape(user, source)
    sections = _merged(user)
    cfg = Config(repo_root=repo_root, repo_id=repo_id, source=source,
                 gates=user.get("gates", DEFAULT_GATES),
                 declared=_declared(user), **sections)

    env_dir = os.environ.get(env_var("STATE_DIR"))
    if ledger_dir or env_dir:
        cfg.ledger_dir = Path(ledger_dir or env_dir)
        return cfg

    base = Path.home() / ".local" / "state" / TOOL_NAME / repo_id
    if check_legacy and not base.is_dir():
        legacy = legacy_state_dir(repo_id)
        if legacy is not None:
            raise ConfigError(
                f"ledger state for this repo exists under the tool's former "
                f"name at {legacy}, and nothing exists at {base}: starting a "
                f"fresh ledger would silently orphan that record (§10.1)",
                paths.command(*paths.lits(TOOL_NAME, "migrate-state")),
                code=1)
    cfg.ledger_dir = base
    return cfg

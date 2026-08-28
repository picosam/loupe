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

from . import paths, vocab, FORMER_NAMES, TOOL_NAME, env_var

CONFIG_BASENAME = "review.toml"

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
    gates: list = field(default_factory=list)
    source: str = "defaults"
    ledger_dir: Path = field(default=None)  # resolved in load()

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


def _git(repo_root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True, text=True, timeout=30,
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
    ("limits", "token_budget"): int,    # absent is not zero and not infinity
}
_KIND_OF = {str: "a string", int: "a whole number", list: "a list of strings",
            dict: "a table of string values", bool: "true or false"}


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
    if want is bool or isinstance(value, bool) and want is int:
        # `bool` is an `int` in Python and is never a count here.
        if want is not bool:
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
                f"<ledger>/gate-output/<sha>/<id>.log, so it must match "
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


def check_shape(user: dict, source: str) -> None:
    """Every declared section, key and value kind, or a ConfigError naming
    all of them at once — a person repairing a config should see the whole
    list, not one error per run."""
    errors: list[str] = []
    if not isinstance(user, dict):
        raise ConfigError(f"{source} is not a table")
    known = set(DEFAULTS) | {"gates"}
    for name in sorted(set(user) - known):
        errors.append(f"unknown section [{name}]; the declared sections are "
                      f"{sorted(known)}")
    for section in sorted(set(user) & set(DEFAULTS)):
        body = user[section]
        if not isinstance(body, dict):
            errors.append(_bad(f"[{section}]", body, "a table"))
            continue
        for key in sorted(body):
            if _kind_for(section, key) is None:
                errors.append(f"[{section}] states unknown key {key!r}")
                continue
            _check_value(section, key, body[key], errors)
    if "gates" in user:
        _check_gates(user["gates"], errors)
    if errors:
        raise ConfigError(
            f"{source} declares {len(errors)} invalid value(s): "
            + "; ".join(errors),
            remedy=f"a person repairs {source}; every item above names the "
                   f"section, the key and the kind it must have")


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
    check_shape(user, source)
    sections = _merged(user)
    return Config(repo_root=like.repo_root, repo_id=like.repo_id,
                  source=source, gates=user.get("gates", DEFAULT_GATES),
                  ledger_dir=like.ledger_dir, **sections)


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

    check_shape(user, source)
    sections = _merged(user)
    cfg = Config(repo_root=repo_root, repo_id=repo_id, source=source,
                 gates=user.get("gates", DEFAULT_GATES), **sections)

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

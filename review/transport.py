"""The transport verbs (design §9bis.3, RVW-T9): `handoff`, `take`, `close`.

One verb per phase, zero required flags on the common path, every decision
derived from config plus a probe. These ride the pushed-SHA reachability
that §9bis.4 landed; they do NOT implement the git-notes carrier (RVW-T1,
parked behind the two-clone prototype). The envelope still crosses machines
by whatever `relay` the repo declares — a file path on one machine, a paste
between two — and each verb records what it did in the ledger, so neither
end of the loop is manual any more.

  handoff  author side: emit-request — which an author agent runs unasked
           once implementation work is done, per the agents' standing
           instructions — then record the request event, keep the
           envelope bytes in the exchange directory (outside the tree,
           §4), and stop; carrying the envelope to the reviewer is the
           human's relay, never the round's start.
           Idempotent on an unchanged tip (§9bis.3 rule 5).
  take     reviewer side: validate the request, PROBE the target (fetch,
           cat-file, base ancestry) and every reference (digest), record
           the request and a `take` event, print the envelope and the exact
           diff command. Refuses everything it cannot verify.
  close    author side: ingest the verdict, record it, close the round; a
           clean verdict closes the lineage; `--lineage` closes one by
           recorded decision, so the next handoff opens round 1 with the
           repo default cap rather than inheriting an authorization granted
           to a different review.

Ledger events introduced here: `take` and `lineage_closed`. Requests and
verdicts are recorded in exactly the shape `ledger add` records them, so a
manual `ledger add` afterwards is a no-op, never a duplicate.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# POSIX advisory locking, and the ONLY reason this module has a platform
# branch. `fcntl.flock` is what makes the lineage reservation below a
# reservation rather than a second check: the kernel holds it, so a holder
# that dies — crash, kill, a gate that never returns and is interrupted —
# releases it with no stale file to reason about and no override flag to
# invent. Where it is absent the reservation cannot be taken, and admission
# is REFUSED rather than granted unguarded (`LineageReservation.acquire`).
try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only where it is absent
    fcntl = None

from . import (TOOL_NAME, paths, refs, shape_identity, tool_identity,
               validate, vocab, wire)
from .config import (Config, GitCeiling, built_in_git_ceiling, caller_env,
                     git_ceiling, git_timeout_refusal)
from .digest import sha256_file, sha256_text
from .fingerprint import LineageError, alias_event, resolve_identity
from .fingerprint import compute as fp_compute
# `firing_id` is defined beside the code that MAKES the firings
# (round-4 F1: identity travels with the thing it identifies);
# it stays reachable here because this is the module that owns
# the authorization boundary reading it.
from .ledger import (Ledger, firing_id, is_ruling, known_branch,
                     new_lineage_id, ruling_facts)
from .ledger import _uid as _event_uid

EXCHANGE_DIR = "exchange"

# Sweep F6 moved `take`'s validation onto the TARGET commit's configuration,
# because "a valid envelope was refused as T-UNDECLARED ... for a fault in
# the reviewer's tree". The refusal's own `next` then reopened the door it
# closed: it sent the reviewer to `validate`, which resolves configuration
# from THIS checkout, so the reviewer re-derived the diagnosis under the
# wrong authority and reported a defect belonging to their own tree. Live
# 2026-08-27, a reviewer on a detached worktree did exactly that and named
# T-UNDECLARED as the blocker of an envelope whose real defect was its
# bytes.
#
# A defective envelope is the AUTHOR's to fix in every case, so the reviewer
# has no command to run and the exit is `blocked` — the state `_blocked`
# already documents for "an envelope that must be authored". The items
# travel with it, computed once, under the authority that governs them.
_AUTHORS_TO_FIX = (
    "the AUTHOR corrects the envelope and re-emits it; a reviewer does not "
    "edit an envelope and has nothing to run here. Relay the items above. "
    "Do NOT re-derive them with `validate`: these were judged against "
    "{authority}, and `validate` judges against whatever configuration this "
    "checkout holds — a different authority, which can report defects that "
    "belong to your tree rather than to the request")

_BASE_LINE_RE = re.compile(r"^Base:\s+([0-9a-f]{40})\b", re.MULTILINE)
# The request's `Scoped:` line (0.25.0), read back by `take` to re-render it
# for the reviewer's checkout (public issue #10, 0.26.0). Searched in the
# header only — the lines before the first section heading — because the
# Claim below it is the author's prose, where a line may begin with anything.
_SCOPED_LINE_RE = re.compile(r"^Scoped: (.*)$", re.MULTILINE)
# The only pathspec forms the emitter's `Scoped:` command carries
# (`emit._pathspecs`): every one literal, so no pattern reaches git.
_SCOPED_SPEC_FORMS = (":(literal)", ":(exclude,literal)")


def scoped_pathspecs(body: str, base: str | None, sha: str,
                     render=None) -> tuple[list[str] | None, str | None]:
    """(the pathspecs of the request's `Scoped:` command, None) — or
    (None, why this reader gives no reviewer-local command).

    Public issue #10 (0.26.0). The request's `Scoped:` line is a command for
    the AUTHOR's checkout: `git -C <author root> … diff <base>...<target> --
    <pathspecs>`. `take` already re-renders `Diff:` for the reviewer's; this
    is the scoped half. The pathspecs are taken from the author's line, not
    recomputed, because the set is the emitter's decision about which
    changed paths the claim's `scope_paths` match (`emit._pathspecs`) and
    the reviewer's command must select exactly that set — and they are
    re-rendered only from a line in the exact form the emitter writes:
    nothing but literal pathspecs, and `render(root, *pathspecs)` — the
    caller's one diff renderer over THIS request's `Base:` and target —
    reproducing the line byte for byte from the root and pathspecs parsed
    out of it. So the range, the options and the quoting are all judged by
    the renderer that wrote them, and no command word is restated here.
    Anything else — no line, the emitter's own `none` or `withheld`, a
    second `Scoped:` line, quoting that does not parse, a different range, a
    pattern pathspec — gives no command and says why, because a line
    re-rendered from bytes of another form would carry another selection
    under this tool's name.
    """
    import shlex
    header = body.split("\n## ", 1)[0]
    lines = _SCOPED_LINE_RE.findall(header)
    if not lines:
        return None, ("the request carries no `Scoped:` line: its claim "
                      "declares no `scope_paths`")
    if len(lines) > 1:
        return None, (f"the request carries {len(lines)} `Scoped:` lines, "
                      f"and a reviewer command cannot choose between them")
    text = lines[0].strip()
    if text.startswith(("none — ", "withheld — ")):
        return None, (f"the request's own `Scoped:` line gives no command "
                      f"({text})")
    if base is None:
        return None, ("the request names no `Base:`, so there is no range "
                      "to limit")
    try:
        argv = shlex.split(text)
    except ValueError:
        return None, ("the request's `Scoped:` line does not parse as one "
                      "command line")
    specs = argv[7:]
    if (len(argv) < 8 or render is None
            or not all(s.startswith(_SCOPED_SPEC_FORMS) for s in specs)
            or str(render(argv[2], *specs)) != text):
        return None, (f"the request's `Scoped:` line is not the form this "
                      f"reader re-renders: the one diff command over this "
                      f"request's range, {base[:12]}...{sha[:12]}, limited by "
                      f"literal pathspecs only")
    return specs, None
# The closed reference grammar. Four forms, and `asserted` is one of them:
# a line naming a path and its requirement marker but carrying no digest, so
# the tool can say the author claimed it and nothing more. It had no syntax
# here while probe_references carried an `asserted` branch and a docstring
# promising it — the branch was unreachable, because the alternation below was
# mandatory. A state the code describes and the grammar cannot express is a
# state the reader is told exists and never sees.
# Round 3 F1: the path half is BUILT from the one grammar the preflight
# enforces (`refs.PATH_CHARS`), so what the emitter may render and what this
# parser may recognise cannot drift into two rules again.
_REF_LINE_RE = re.compile(
    rf"^\s*(?P<path>{refs.PATH_CHARS}+?)(?P<dir>/)?\s+"
    r"(?:sha256:(?P<digest>[0-9a-f]{64})|(?P<unavailable>UNAVAILABLE)"
    r"|\((?P<note>directory[^)]*)\)"
    r"|\[(?P<marker>required|advisory)\])")


# Round 4 F1: the recorded state for "this emission supplied no claim file".
# A distinct value rather than an absent field, so "no claim" is something the
# ledger PROVES rather than something a reader infers from silence — absence
# already means "recorded before the field existed", and one token cannot mean
# both without reopening the hole three rounds have now closed by halves.
#
# The value itself lives in `vocab` with the rest of the claim's grammar
# (round 7 F1), so the capture boundary and the cache cannot drift into two
# spellings of one state; this name is where the cache reads it.
NO_CLAIM = vocab.NO_CLAIM


class Refusal(RuntimeError):
    """A refusal that carries the next command (§9bis.3 rule 3).

    Round 5 F1: `next_cmd` is a field agents run verbatim, so it accepts a
    rendered `paths.Command` or the empty string ("no command applies")
    and refuses anything else. A command built by string construction
    cannot reach an agent through this door by being unanalysable.

    `remedy` and `items` are what the empty `next_cmd` needs to be honest.
    A refusal with no command is `blocked`, and `_blocked`'s contract is
    that a blocked exit names what a PERSON must do — so a raise site that
    selects the blocked state has to supply the sentence, or it has typed
    the state and withheld the only field it carries. `items` travels for
    the same reason in the one case where the refusing verb has already
    computed the diagnosis: a reviewer told to relay a defect needs the
    defect, and re-deriving it with a second command is exactly what the
    misdirection below made unsafe.
    """

    def __init__(self, why: str, next_cmd: str, remedy: str = "",
                 items=None):
        super().__init__(why)
        self.next_cmd = paths.executable(next_cmd, "Refusal.next_cmd")
        self.remedy = remedy
        self.items = list(items or [])


class GitTimeout(Refusal):
    """A git subprocess ran past its ceiling (0.25.0).

    Every git door converts `subprocess.TimeoutExpired` into this, so no
    verb exits on a traceback when a hook, a filter or a remote is slow.
    It is a `Refusal` — and so a `RuntimeError`, the class every caller of
    a git door already names (round 4 F3) — and `main` renders it as a
    blocked exit: `next` is null, because no command repairs a slow hook,
    and `remedy` is the decision a person takes (`config.
    git_timeout_refusal`, the one wording).

    A caller that wraps a git failure in a refusal of its own passes this
    one through untouched: its remedy is the true one, and a wrapper's
    ("make the remote writable", "restore access") would send a person to
    fix something that is not broken.

    `argv` is the command exactly as it ran (`TimeoutExpired.cmd`; the
    message renders it with URL userinfo removed); `ceiling` is the
    `config.GitCeiling` that was in force.
    """

    def __init__(self, argv, ceiling: GitCeiling, state: str = ""):
        why, remedy = git_timeout_refusal(argv, ceiling, state)
        super().__init__(why, "", remedy=remedy)
        self.argv = tuple(str(a) for a in argv)
        self.ceiling = ceiling
        self.state = state

    def within(self, state: str) -> "GitTimeout":
        """The same refusal, saying what the verb had already done when
        the ceiling fell — a local commit, an unconfirmed push. A caller
        that knows adds it; the timeout itself is unchanged."""
        joined = f"{self.state} {state}".strip() if self.state else state
        return GitTimeout(self.argv, self.ceiling, joined)


#: Round 1 F1 (lineage 12). `git replace` installs a ref under
#: `refs/replace/` — or under whatever `GIT_REPLACE_REF_BASE` names — and
#: every ordinary object lookup then transparently returns the REPLACEMENT.
#: That is local, uncommitted, per-machine state, so it is the same class of
#: input as a filter driver or a textconv attribute, and it defeats the
#: property this boundary exists to create: it reaches `ls-tree` and
#: `cat-file` exactly as it reaches `show`, so switching porcelain for
#: plumbing would not have helped. `--no-replace-objects` is a git-wide
#: option, evaluated before the subcommand and before any ref base is
#: consulted, and it turns the whole mechanism off for that one invocation.
NO_REPLACE = "--no-replace-objects"


def _git(repo_root: Path, *args: str, no_replace: bool = False,
         ceiling: GitCeiling | None = None) -> str:
    # RVW-T21 D2: the caller's environment, not the shim's hardened one.
    # `take`'s `fetch` writes refs and fires reference-transaction where a
    # repository has one, and `status` consults a configured
    # core.fsmonitor; both are the repository's own scripts, in the same
    # trust position as a gate. Applied at the door rather than per
    # subcommand.
    #
    # 0.25.0: the ceiling is the CALLER's `config.git_ceiling(cfg)` — every
    # runner below that holds a configuration passes it, so `[limits]
    # git_timeout` reaches every subcommand this door carries (the door's
    # rule, not the argv's, as for the environment above). A caller with no
    # configuration in hand gets the built-in. The cost, stated where it is
    # paid: a ceiling raised for a slow hook is also raised for an
    # unreachable remote, so a hung network call takes that much longer to
    # refuse. And a timeout is a typed refusal here — until 0.25.0 this door
    # let `TimeoutExpired` escape, which is where `respond --out`'s envelope
    # push died with a traceback — while every other subprocess failure
    # becomes the `RuntimeError` its callers already name, as `emit._git`'s
    # does (round 4 F3).
    ceiling = ceiling or built_in_git_ceiling()
    try:
        out = subprocess.run(["git", *([NO_REPLACE] if no_replace else []),
                              "-C", str(repo_root), *args],
                             capture_output=True, text=True,
                             timeout=ceiling.seconds, env=caller_env())
    except subprocess.TimeoutExpired as exc:
        raise GitTimeout(exc.cmd, ceiling) from exc
    except subprocess.SubprocessError as exc:
        raise RuntimeError(
            f"a `git` subprocess did not complete: "
            f"`{paths.command(paths.Lit('git'), *args)}` — "
            f"{type(exc).__name__}: {exc}") from exc
    if out.returncode != 0:
        raise RuntimeError(
            f"a `git` subprocess failed: "
            f"`{paths.command(paths.Lit('git'), *args)}` — "
            f"{out.stderr.strip()}")
    return out.stdout.strip()


_digest_text = sha256_text


# ------------------------------------------------------------------ exchange

def exchange_dir(cfg: Config) -> Path | None:
    return Path(cfg.ledger_dir) / EXCHANGE_DIR if cfg.ledger_dir else None


def exchange_path(cfg: Config, round_no: int, kind: str, *,
                  lineage: str, digest: str) -> Path | None:
    """Where the bytes of one envelope are retained: by lineage, round,
    kind AND digest, so no later emission can land on an earlier one.

    `lineage` is the lineage ID (brief `keyed-lineage`). A legacy lineage's
    id is the ordinal it always had, as a string, so every path a previous
    version wrote is the path this one reads.

    Audit of 2026-09-05, finding 1. The name was `round-<n>-<kind>.md`,
    without the lineage — and every lineage starts at round 1, so the
    request that opened lineage 25 overwrote the request that opened
    lineage 24, and `keep_bytes`, which restores differing bytes from the
    canonical text, made the loss permanent. The ledger digest survived
    and could not recover the document. A retained envelope is now
    immutable by construction: the digest is in the name, two documents
    cannot share a path, and a copy whose bytes do not reproduce its own
    name is a rewrite every reader already refuses.
    """
    d = exchange_dir(cfg)
    if d is None:
        return None
    return (d / f"lineage-{lineage}"
            / f"round-{round_no}-{kind}-{digest[:12]}.md")


def legacy_exchange_path(cfg: Config, round_no: int, kind: str) -> Path | None:
    """The flat pre-0.18 name, read but never written: what survives of a
    lineage retained before the name carried it."""
    d = exchange_dir(cfg)
    return d / f"round-{round_no}-{kind}.md" if d else None


def read_kept(cfg: Config, round_no: int, kind: str, *, lineage: str,
              digest: str) -> tuple[Path | None, str | None]:
    """The ONE checked read of a retained copy: `(path, text)`.

    `path` is the copy `kept_path` selected (None: nothing retained at
    either name); `text` is its bytes decoded, and None whenever they do
    not reproduce `digest` — the ledger's own record of the envelope —
    or cannot be read or decoded. Lineage 25 round 1 F4: the open-request
    brief read whatever `kept_path` returned and, with the current copy
    gone and a pre-upgrade flat copy still present, served an EARLIER
    lineage's request as the live one, relay and all. Every consumer of a
    kept copy goes through here now, so the legacy fallback can serve an
    old lineage's copy and never a wrong one. Raw bytes, decoded without
    newline translation (lineage 17 round 5 F1): a CRLF rewrite must not
    reproduce the digest."""
    path = kept_path(cfg, round_no, kind, lineage=lineage, digest=digest)
    if path is None:
        return None, None
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return path, None
    if not digest or _digest_text(text) != digest:
        return path, None
    return path, text


def kept_path(cfg: Config, round_no: int, kind: str, *, lineage: str,
              digest: str) -> Path | None:
    """The retained copy of a recorded envelope, if one exists: the
    lineage-scoped name first, else the flat legacy name. Every reader
    still checks the bytes against the recorded digest, so the fallback
    can serve an old lineage's copy and never a wrong one."""
    current = exchange_path(cfg, round_no, kind, lineage=lineage,
                            digest=digest)
    if current is None:
        return None
    if current.is_file():
        return current
    legacy = legacy_exchange_path(cfg, round_no, kind)
    return legacy if legacy is not None and legacy.is_file() else None


def _runnable(record: dict, *fields: str) -> dict:
    """Check every JSON field agents execute through the one door.

    Round 5 F1 typed `Refusal.next_cmd`, the CLI's `next` and the fenced
    relay lines. These are the same kind of field on the RESULT side —
    `reviewer_next` is the line the human hands over verbatim, `next` is
    what the author runs, `diff` is what the reviewer runs — so they are
    checked at the same door rather than trusted for being nearby.
    """
    for field in fields:
        if record.get(field) is not None:
            paths.executable(record[field], f"the `{field}` field")
    return record


def keep_bytes(cfg: Config, round_no: int, kind: str, text: str, *,
               lineage: str) -> str:
    """Write the envelope bytes beside the ledger and return the path, or the
    reason there is none. Retention is best effort: the ledger event carries
    the digest either way, so a copy that could not be kept degrades the
    pointer without losing the identity (the same rule gate output follows).

    `lineage` is required, never defaulted: it is half of the name that
    keeps one lineage's round 1 from overwriting another's (finding 1 of
    the 2026-09-05 audit), and a caller that does not know which lineage it
    is keeping for has no business keeping.

    The already-kept check compares RAW BYTES, not decoded text (lineage 17
    round 5 F1, ruled 2026-08-30): a text read translates CRLF to LF, so a
    kept copy rewritten under the tool compared equal to the canonical text
    and stayed as it was. This operation owns the copy, so a copy whose
    bytes differ — whatever rewrote them — is restored from the canonical
    emitted text rather than trusted."""
    target = exchange_path(cfg, round_no, kind, lineage=lineage,
                           digest=_digest_text(text))
    if target is None:
        return "not kept (no state directory configured)"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.read_bytes() == text.encode("utf-8"):
            return str(target)
        target.write_text(text, encoding="utf-8")
        return str(target)
    except OSError as exc:
        return f"not kept ({exc.strerror})"


# ------------------------------------------------------- the git ref carrier

# `transport = "git"` (ruled 2026-09-03, brief `git-ref-carrier`). The
# envelope rides a ref under `refs/<tool>/` on the SAME remote the reviewed
# branch was already pushed to, one ref per leg:
#
#     refs/loupe/<lineage>/<round>/request      pushed by `handoff`
#     refs/loupe/<lineage>/<round>/verdict      pushed by `validate --from-target`
#     refs/loupe/<lineage>/<round>/disposition  pushed by `respond --out`
#
# THE OBJECT IS A BARE BLOB, not a commit wrapping a file. Measured before
# choosing: `git push <remote> +<blob>:refs/loupe/...` creates and
# force-updates the ref, `git fetch <remote> +<ref>:<ref>` brings it into the
# reviewer's clone, and `git cat-file blob <ref>` returns the bytes — every
# step ordinary porcelain, on a stock git, with no commit object, tree,
# author identity or timestamp invented to carry a document that is none of
# those things. A commit would have added three fabricated fields and a
# second object per leg for nothing.
#
# THE REF IS AN UNTRUSTED CARRIER and stays one. Nothing here validates,
# and nothing downstream trusts the ref: the fetched bytes go through the
# same readers a pasted envelope does, and the digest and SHA binding are
# unchanged. A ref is where bytes rest — storage is not a trigger, so a
# pushed ref starts nothing and the human still tells the reviewer to take.
#
# The reference a person carries is `git:<lineage>/<round>` — one word,
# because the lineage is not derivable on the far side and the round alone
# would name a different envelope in the next lineage.
CARRIER_PREFIX = "git:"
_ROUND_REFERENCE_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)/(\d+)$")


def carrier_ref(lineage: str, round_no: int, kind: str) -> str:
    """The ref one leg of one round rides on.

    `lineage` is the lineage ID, which is what makes the key safe: the ref
    is `(lineage, round, leg)` and `push_envelope` force-pushes to it
    deliberately, so amend-and-re-emit can replace its own bytes. That is
    correct exactly when the lineage id is unique, which is what a keyed
    lineage provides (brief `keyed-lineage`). A legacy lineage keeps its
    ordinal, so every ref a previous version pushed is the ref this one
    fetches.
    """
    return f"refs/{TOOL_NAME}/{lineage}/{int(round_no)}/{kind}"


def round_reference(lineage: str, round_no: int) -> str:
    """What a person carries for a `git` round: `git:<lineage>/<round>`."""
    return f"{CARRIER_PREFIX}{lineage}/{int(round_no)}"


def parse_round_reference(value: str):
    """`(lineage, round)` for a round reference, or None for anything else.

    The lineage half comes back as the STRING it is written as — a minted
    id (`L3f9a1c2b7e`) or a legacy ordinal (`27`) — because it names a
    lineage and is never arithmetic.

    None rather than a raise: every envelope reader is handed a path, `-`,
    or this, and "not a round reference" is the ordinary case, not a fault.
    """
    if not isinstance(value, str) or not value.startswith(CARRIER_PREFIX):
        return None
    m = _ROUND_REFERENCE_RE.match(value[len(CARRIER_PREFIX):])
    return (m.group(1), int(m.group(2))) if m else None


def _carrier_refusal(why: str, remedy: str) -> "Refusal":
    return Refusal(why, "", remedy=remedy)


def carrier_remote(cfg: Config, git=None) -> str:
    """The remote the envelope refs ride on.

    The reviewed branch's upstream remote, else the one configured remote.
    Two remotes and no upstream is not derivable and the tool never invents
    a decision (§9bis.3) — the same rule, and the same refusal, that
    `ensure_pushed` applies to the branch push this rides beside.
    """
    run = git or (lambda *a: _git(cfg.repo_root, *a,
                                  ceiling=git_ceiling(cfg)))
    try:
        remotes = [r for r in run("remote").splitlines() if r.strip()]
    except GitTimeout:
        raise
    except RuntimeError as exc:
        raise _carrier_refusal(
            f"the configured remotes could not be read: {exc}",
            remedy="a person runs this inside a repository whose remotes "
                   "can be read; the round rides one of them") from exc
    if not remotes:
        raise _carrier_refusal(
            f"transport={vocab.TRANSPORT_GIT!r} carries the envelope on a "
            f"ref of the remote both sides reach, and this clone has no "
            f"remote",
            remedy=f"a person adds the remote, or declares "
                   f"`{vocab.toml_line('roles.transport', vocab.TRANSPORT_PASTE)}`"
                   f" under [roles] — the carrier that needs no remote")
    if len(remotes) == 1:
        return remotes[0]
    branch = ""
    try:
        branch = run("rev-parse", "--abbrev-ref", "HEAD")
    except GitTimeout:
        raise
    except RuntimeError:
        branch = ""
    if branch and branch != "HEAD":
        upstream = run("for-each-ref", "--format=%(upstream:remotename)",
                       f"refs/heads/{branch}").strip()
        if upstream:
            return upstream
    if "origin" in remotes:
        return "origin"
    upstream_cmd = paths.command(*paths.lits("git", "push", "-u"),
                                 paths.Ph("<remote>"), paths.Ph("<branch>"))
    raise _carrier_refusal(
        f"{len(remotes)} remotes exist ({', '.join(remotes)}) and none is "
        f"derivable as the one carrying this round's envelope refs",
        remedy=f"a person sets the branch's upstream with `{upstream_cmd}`, "
               f"then re-runs")


def _blob_of(cfg: Config, text: str, git=None) -> str:
    """Write the envelope bytes into this clone's object store as a blob.

    Bytes, not text: the envelope's physical form is part of what the
    digest binds, and a text-mode write would translate line endings on
    the way in — the same defect `_read_envelope` closed on the way out.
    """
    if git is not None:
        return git("hash-object", "-w", "--stdin")
    # A door of its own (it writes stdin, which `_git` does not carry), so
    # it takes the configured ceiling and types its timeout itself (0.25.0).
    ceiling = git_ceiling(cfg)
    try:
        out = subprocess.run(
            ["git", "-C", str(cfg.repo_root), "hash-object", "-w",
             "--stdin"],
            input=text.encode("utf-8"), capture_output=True,
            timeout=ceiling.seconds, env=caller_env())
    except subprocess.TimeoutExpired as exc:
        raise GitTimeout(exc.cmd, ceiling) from exc
    if out.returncode != 0:
        raise _carrier_refusal(
            f"the envelope could not be written to the object store: "
            f"{out.stderr.decode('utf-8', 'replace').strip()}",
            remedy="a person makes this repository writable to this "
                   "process, then re-runs")
    return out.stdout.decode("utf-8").strip()


def push_envelope(cfg: Config, lineage: str, round_no: int, kind: str,
                  text: str, git=None) -> dict:
    """Push one leg's envelope to its ref, and return what was pushed.

    Force by refspec, deliberately: a blob ref has no ancestry to fast
    forward, and a re-emitted round legitimately replaces the bytes at its
    own ref. The force is bounded to `refs/<tool>/…`, a namespace nothing
    else writes — the branch push `ensure_pushed` performs is untouched by
    this and stays non-forced.

    The push runs the REPOSITORY's `pre-push` hook, as the branch push
    does, under the configured `[limits] git_timeout`; a timeout is the
    typed `GitTimeout`, passed through rather than re-described as an
    unwritable remote (0.25.0).
    """
    run = git or (lambda *a: _git(cfg.repo_root, *a,
                                  ceiling=git_ceiling(cfg)))
    remote = carrier_remote(cfg, git=git)
    ref = carrier_ref(lineage, round_no, kind)
    blob = _blob_of(cfg, text, git=git)
    try:
        run("push", remote, f"+{blob}:{ref}")
    except GitTimeout as exc:
        raise exc.within(
            f"The {kind} envelope is kept locally; whether its ref {ref} "
            f"reached {remote} is unconfirmed") from exc
    except RuntimeError as exc:
        raise _carrier_refusal(
            f"the {kind} envelope could not be pushed to {ref} at "
            f"{remote}: {exc}",
            remedy=f"a person makes {remote} writable to this process — the "
                   f"reviewed branch was pushed there, so the envelope ref "
                   f"can be too — or declares "
                   f"`{vocab.toml_line('roles.transport', vocab.TRANSPORT_PASTE)}`"
                   f" under [roles] and carries the bytes by hand") from exc
    return {"ref": ref, "remote": remote, "kind": kind, "round": round_no}


def fetch_envelope(cfg: Config, lineage: str, round_no: int, kind: str,
                   git=None) -> str:
    """The bytes of one leg, fetched from its ref into this clone.

    The ref is mirrored locally under the same name before it is read, so
    the read names an exact ref rather than `FETCH_HEAD`, which any other
    fetch in the same clone would overwrite.
    """
    run = git or (lambda *a: _git(cfg.repo_root, *a,
                                  ceiling=git_ceiling(cfg)))
    remote = carrier_remote(cfg, git=git)
    ref = carrier_ref(lineage, round_no, kind)
    reference = round_reference(lineage, round_no)
    try:
        run("fetch", remote, f"+{ref}:{ref}")
    except GitTimeout:
        raise
    except RuntimeError as exc:
        raise _carrier_refusal(
            f"{remote} carries no {kind} envelope for {reference} ({ref}): "
            f"{exc}",
            remedy=f"the other side runs its own step first — the round's "
                   f"{kind} is pushed by the verb that produces it — or a "
                   f"person passes the envelope file itself") from exc
    try:
        # Replacement off: `git replace` is local state that rewrites what
        # any object read returns, and this one decides the bytes a round is
        # judged on. The bytes stay untrusted either way — every binding
        # downstream is computed from them — but they must at least be the
        # bytes the fetch brought in.
        data = run_bytes(cfg, git, "cat-file", "blob", ref, no_replace=True)
    except GitTimeout:
        raise
    except _UNUSABLE as exc:
        raise _carrier_refusal(
            f"{ref} was fetched from {remote} but its bytes could not be "
            f"read: {exc}",
            remedy="a person re-runs; if it repeats, the ref does not name "
                   "an envelope blob and the other side re-pushes it") from exc
    return wire.decode_envelope(data, reference)


# ------------------------------------------------------------------- prune

GATE_OUTPUT_DIR = "gate-output"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")


def _referenced_shas(events: list[dict]) -> set[str]:
    """Every 40-hex string anywhere in any ledger event, at any depth.

    Deliberately broader than the keys known to carry SHAs today (`sha`,
    `base`): a key added later must widen retention by default, never narrow
    it. Over-collection keeps a directory that could have gone; the inverse
    deletes evidence a record still points at.
    """
    found: set[str] = set()

    def walk(value):
        if isinstance(value, str):
            if _SHA40.fullmatch(value):
                found.add(value)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    for event in events:
        walk(event)
    return found


def _entry_kind(entry) -> str:
    """Filesystem object type of one gate-output child, WITHOUT following
    links — on a deletion boundary the object type is authority, and every
    classification here reads the entry itself, never its target (round-1
    F2: `is_dir()` followed a SHA-named symlink out of the retention root,
    then `rmtree` refused it and aborted the whole operation).

    `entry` is an `os.DirEntry` from the anchored scandir, so the type
    comes from the directory read itself (`follow_symlinks=False`
    throughout). `symlink` covers live and broken links alike — a broken
    link is still a link, and neither is ever dereferenced. `unreadable`
    is an entry whose type cannot be read at all; unknown is kept, not
    guessed at.
    """
    try:
        if entry.is_symlink():
            return "symlink"
        if entry.is_dir(follow_symlinks=False):
            return "dir"
        return "not-a-directory"
    except OSError:
        return "unreadable"


def _no_follow_size(anchor_fd: int, name: str) -> tuple[int, int]:
    """(files, bytes) under child `name` of the anchored container, never
    crossing a symlink: `fwalk` is anchored to the same descriptor the
    deletion uses, does not descend into linked directories, and sizes
    come from `follow_symlinks=False` stats — so the accounting of what a
    removal frees cannot read anything outside the directory being
    removed, whatever any path component is replaced with meanwhile."""
    files = total = 0
    for _dirpath, _dirs, names, dirfd in os.fwalk(name, dir_fd=anchor_fd,
                                                  follow_symlinks=False):
        for n in names:
            try:
                stat = os.stat(n, dir_fd=dirfd, follow_symlinks=False)
            except OSError:
                continue
            files += 1
            total += stat.st_size
    return files, total


def _open_gate_output(base: Path) -> int | None:
    """A descriptor for the gate-output CONTAINER itself, or None when it
    does not exist — and a Refusal for every other state.

    Round-2 F1: the child-level no-follow checks established nothing while
    the directory every child was resolved FROM could itself be a symlink
    — `base.is_dir()` and `iterdir()` both followed it, and a container
    link redirected enumeration, accounting and deletion outside the state
    root entirely. So the container is opened `O_NOFOLLOW | O_DIRECTORY`
    and every subsequent operation — scandir, fwalk, rmtree — is anchored
    to that one descriptor: a symlink refuses at open (ELOOP), a
    non-directory refuses (ENOTDIR), an unreadable one refuses (EACCES),
    absent is the clean zero, and a replacement race after the open cannot
    redirect anything because no later step resolves the container's path
    again.
    """
    try:
        return os.open(base, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise Refusal(
            f"gate-output at {base} could not be opened as a plain "
            f"directory without following a link "
            f"({exc.strerror or exc}): a container that is a symlink, a "
            f"non-directory or unreadable is kept, never traversed — "
            f"nothing was enumerated, accounted or deleted", "")


def _require_container_named(base: Path, anchor: int,
                             removed: int) -> None:
    """Refuse when `gate-output/` no longer names the directory this run
    opened (round 3 F2).

    The contract says a container replaced after the open refuses, and the
    implementation did not check: every operation was anchored to the
    descriptor, so a rename-and-relink went unnoticed and prune carried on
    deleting inside the directory that used to be the layer. Anchoring made
    that SAFE — nothing outside the state root can be reached — but safe is
    not what was promised, and a caller told that an identity change stops
    the operation is entitled to have it stop.

    The opened identity is compared with the path's at four points, which
    is the partition of when a replacement becomes observable: at the open,
    before each entry's decision, after that entry's accounting and before
    its removal or dry-run record, and once more before the run reports
    success. Round 4 F3 named the third: accounting can be long, and a
    single-entry run had no next iteration to notice the change, so it
    deleted and reported success after the identity had changed.

    The enforceable invariant, stated as what it is (round 5 F2): prune
    REFUSES a replacement observable at one of those comparison points, and
    descriptor anchoring keeps a replacement made at any other moment from
    redirecting traversal, accounting or deletion. It is not an
    unconditional refusal, and no comparison can make it one — a check
    describes the instant it ran.

    Two intervals are therefore irreducible, and both are stated rather
    than excluded. Between the last comparison and the `rmtree` syscall: a
    replacement there is not detected, and the deletion still runs against
    the verified inode inside the state root. Between the final comparison
    and the successful return: a replacement there is not detected either,
    so a run can report success while `gate-output/` already names
    something else — nothing has been deleted outside the layer, and
    nothing more has been deleted at all. What is NOT irreducible is the
    accounting-to-removal interval, which round 5 F2 named and which the
    third comparison point now covers.
    """
    try:
        st = os.stat(base, follow_symlinks=False)
        named = (st.st_dev, st.st_ino)
    except OSError:
        named = None
    opened = os.fstat(anchor)
    if named != (opened.st_dev, opened.st_ino):
        raise Refusal(
            f"gate-output at {base} is no longer the directory this run "
            f"opened (it was renamed, replaced or removed while prune was "
            f"running): the operation stops rather than continuing against "
            f"a container the state directory no longer names"
            + (f" — {removed} directory(ies) had already been removed "
               f"through the opened descriptor, inside the state root"
               if removed else " — nothing was removed"), "")


def prune_gate_output(cfg: Config, ledger: Ledger,
                      dry_run: bool = False) -> dict:
    """Remove retained gate output for commits no ledger event references.

    The retention policy this verb executes (decided 2026-08-22, measured
    first: 1.8 MB after 13 rounds, growth linear in emission ATTEMPTS
    because a refused handoff retains its gate output and records nothing):

    - the ledger is append-only and is NEVER pruned;
    - `exchange/` is the review record itself and is NEVER pruned;
    - `gate-output/` is the one designed-pruneable layer — every attestation
      carries the sha256 and byte count of its output, so a missing retained
      copy degrades the pointer without losing the identity (§5.1).

    Within that layer the rule is referenced-by-the-record, across ALL
    lineages: a PLAIN directory named by a SHA that appears in any ledger
    event stays; one that appears nowhere — a refused emission attempt is
    the common producer — is removed. Everything else is kept and NAMED in
    the result (`kept_unrecognised`): symlinks — live, broken, whatever
    they point at — non-directories, non-SHA names, and entries whose type
    cannot be read. Classification never follows a link (round-1 F2), and
    that rule now starts at the CONTAINER (round-2 F1): `gate-output/`
    itself is opened `O_NOFOLLOW | O_DIRECTORY` and enumeration,
    accounting and deletion all run against that one descriptor, so a
    linked or replaced container refuses instead of redirecting the
    operation outside the state root. Nothing outside `gate-output/` is
    read or written even when a crafted or stale link points elsewhere;
    removal deletes nested links rather than their targets (`rmtree` does
    not follow), and the freed-bytes accounting walks without following
    either. `--dry-run` reports the identical decision — refusals
    included — without acting on it.
    """
    if cfg.ledger_dir is None:
        raise Refusal("no state directory is configured, so there is no "
                      "retained gate output to prune", "")
    base = Path(cfg.ledger_dir) / GATE_OUTPUT_DIR
    referenced = _referenced_shas(ledger.events())
    pruned, kept, unrecognised = [], 0, []
    # Round-2 F1: the container FIRST, and by descriptor from then on. The
    # refusal (symlink, non-directory, unreadable) is decided before the
    # dry_run branch can matter, so dry-run and act refuse identically.
    anchor = _open_gate_output(base)
    if anchor is not None:
        try:
            # Round 3 F2: replaced between the open and the enumeration.
            _require_container_named(base, anchor, len(pruned))
            with os.scandir(anchor) as scan:
                entries = sorted(scan, key=lambda e: e.name)
            for entry in entries:
                # …and replaced during enumeration, accounting or deletion.
                # Before the DECISION, not before the removal, so `--dry-run`
                # refuses on exactly the state the acting run refuses on.
                _require_container_named(base, anchor, len(pruned))
                kind = _entry_kind(entry)
                if kind != "dir":
                    kept += 1
                    unrecognised.append({"entry": entry.name, "kind": kind})
                    continue
                if not _SHA40.fullmatch(entry.name):
                    kept += 1
                    unrecognised.append({"entry": entry.name,
                                         "kind": "non-sha-name"})
                    continue
                if entry.name in referenced:
                    kept += 1
                    continue
                files, size = _no_follow_size(anchor, entry.name)
                # Round 4 F3: accounting can be long, and the replacement
                # is observable immediately before the destructive act.
                # Checking again HERE — after accounting, before the
                # removal or the dry-run record — is what makes the
                # one-entry and final-entry cases refuse like every other
                # instead of relying on a next iteration that may not
                # exist. Before the record, not just before the rmtree, so
                # dry-run and act still decide identically.
                _require_container_named(base, anchor, len(pruned))
                record = {"sha": entry.name, "files": files, "bytes": size}
                if not dry_run:
                    shutil.rmtree(entry.name, dir_fd=anchor)
                pruned.append(record)
            # …and once more before reporting success, so a run whose last
            # entry was the one replaced — or a run with no entries at all
            # — cannot return a clean result computed against a container
            # the state directory no longer names.
            _require_container_named(base, anchor, len(pruned))
        finally:
            os.close(anchor)
    return {"pruned": pruned, "kept": kept, "dry_run": dry_run,
            "kept_unrecognised": unrecognised,
            "bytes_freed": sum(e["bytes"] for e in pruned),
            "referenced_shas": len(referenced),
            "gate_output": str(base)}


# ------------------------------------------------------------------- events

def declared_transport(parsed) -> str:
    """The transport an envelope declares, or the default when it declares
    none (RVW-T11).

    Absence is the default here, and deliberately not an unknown — the
    opposite of the handoff cache's rule about claims. The two cases differ
    in what a wrong answer costs: an unprovable claim state served warm hands
    the reviewer an envelope nobody wrote, while an unstated transport is a
    round emitted before the attribute existed, whose two ends did share a
    filesystem because that was the only configuration the tool had. Reading
    it as unknown would make every historical envelope unreadable to the
    relay; reading it as the default reproduces exactly what those rounds did.

    A value outside the vocabulary — or an explicitly empty one — is
    refused HERE, not returned for someone else to judge (R1-F2). The old
    contract returned it as-is on the theory that `validate`'s R-TRANSPORT
    refuses it first, but `brief` is also an official relay reader and did
    not validate, so a stamp the grammar refuses selected a live carrier
    anyway; and the `or` that folded `transport=""` to the default was the
    fail-open route §5.2 names. A closed lifecycle cannot rely on every
    caller remembering the one validating verb, so the reader itself is
    the boundary.
    """
    return vocab.transport_or_default(
        (getattr(parsed, "attrs", {}) or {}).get("transport"),
        "the envelope's transport stamp")


def recorded_transport(ledger, sha: str, lineage: str) -> str:
    """The transport recorded for the round that binds `sha`, on THIS
    machine's ledger (RVW-T11).

    The verdict leg needs the value at a point where no request envelope is
    in hand: the reviewer runs `validate <verdict.md>`, which reads a document
    the reviewer wrote. So the value is taken from the record the reviewer's
    own `take` already wrote for this SHA — a recorded fact on this machine,
    not an inference from the environment and not a field the verdict's author
    could mis-transcribe.

    It answers on both sides without a special case: the reviewer's ledger
    carries `take`, the author's carries `request`, and each records what the
    envelope declared. Neither present — a verdict for a SHA this ledger never
    saw — is the default, which is what the leg did before it was declared.

    R1-F2: the newest matching record DECIDES — a recorded value outside
    the vocabulary is refused, not skipped. The old loop scanned past an
    invalid record to whatever older one looked valid, and fell to the
    default when none did: an invalid-record fallback, selecting a carrier
    no record declared. A record with no transport key at all predates the
    attribute and reads as the default, exactly like an unstamped envelope.
    """
    for e in reversed(ledger.current(lineage)):
        if e.get("sha") != sha:
            continue
        if e.get("event") in ("take", "request"):
            return vocab.transport_or_default(
                e.get("transport"),
                f"the recorded {e['event']} event for {sha[:12]}")
    return vocab.TRANSPORT_DEFAULT


def request_event(parsed: wire.Request, round_no: int, digest: str,
                  size: int, tokens: int | None = None,
                  claim_digest: str = "") -> dict:
    """The request event exactly as `ledger add` records it (one shape)."""
    event = {"event": "request", "round": round_no, "sha": parsed.sha,
             "author": parsed.attrs.get("author"),
             "reviewer": parsed.attrs.get("reviewer"),
             # RVW-T11: read off the envelope, never off this machine's
             # config — the recorded value must be what the ENVELOPE
             # declared, so both ledgers agree about the round even when the
             # two machines' configs do not. Absent on every envelope emitted
             # before the attribute existed, and `declared_transport` reads
             # that absence as the default rather than as an unknown.
             "transport": declared_transport(parsed),
             "source_digest": digest, "bytes": size}
    # The emitting worktree's branch, read off the envelope for the same
    # reason as `author` and `transport` above: the recorded value must be
    # what the ENVELOPE declared, so the author's ledger and the reviewer's
    # `take` record the same fact about the round. It is the axis the
    # concurrency refusals compare on (brief `concurrent-round-refusal`) —
    # sha cannot serve, because amend-and-re-emit changes it deliberately.
    # Absent on every envelope emitted before the stamp existed and on a
    # detached checkout, and absence is UNKNOWN: `Ledger.known_branch`
    # concludes no collision from it, so the field is simply not written
    # rather than written empty.
    branch = known_branch(parsed.attrs.get("branch"))
    if branch:
        event["branch"] = branch
    if tokens is not None:
        event["tokens"] = tokens
    if claim_digest:
        # What the AUTHOR supplied, as opposed to what the tree determined.
        # The handoff cache reads it to tell an unchanged emission from an
        # unchanged tip; absent on every pre-round-3 event, and absence is
        # treated as "not recorded", never as "no claim".
        event["claim_digest"] = claim_digest
    return event


def request_events(parsed: wire.Request, round_no: int, digest: str,
                   size: int, tokens: int | None = None,
                   claim_digest: str = "") -> list[dict]:
    """Everything a recorded request contributes to the ledger, in order:
    the request event, then its content-addressed evidence.

    ONE shape for the three doors a request enters by — `handoff`
    (record_handoff), `take`, and the manual `ledger add`. Sweep F3: the
    manual door recorded the request event alone and skipped the evidence
    events the other two record, so the same envelope produced a different
    ledger depending on which verb filed it — the "advertised parity between
    ingestion paths" was false in shape as well as in validation.
    """
    return [request_event(parsed, round_no, digest, size, tokens,
                          claim_digest=claim_digest),
            *evidence_events(parsed, round_no)]


def evidence_events(parsed: wire.Request, round_no: int) -> list[dict]:
    """Content-addressed evidence carried by a request (round 1 F6).

    The design says the ledger carries evidence digests, and the repetition
    breaker exempts a returning finding when the new round cites new
    content-addressed evidence. Ingestion recorded only the digest of the
    whole envelope, so that exemption was unreachable on the product path: the
    breaker could see a loop but never the escape from it, and a breaker that
    cannot observe its own escape condition is not a reliable one.

    Two content-addressed sources exist in a request, and both key on BYTES:
    a reference's sha256, and a gate attestation's recorded output sha256.
    Keying on the digest rather than the path is the point — moving gate
    output to a new pointer while the bytes are unchanged must not read as
    new evidence, and a genuinely different byte string must.

    Round 2 F6: recording the digests was only half of it. `of` carries the
    path in canonical form because the breaker joins these events to the
    findings that cite that path (see `wire.Finding.cited_paths`); an event
    whose pointer is spelled `./a.md` here and `a.md` in the finding would
    record evidence nothing can reach.

    Round 3 F5: de-duplication keyed on the digest ALONE, which conflated two
    different questions. "Are these bytes new?" is answered by the digest, and
    the breaker still answers it that way. "Which findings can these bytes
    speak for?" is answered by the PATH, and dropping the second path that
    carries a digest threw that edge away — so when two references held
    identical new bytes and only the later one was cited, the join went
    missing and a refutation was falsely escalated as repetition. Which of
    the two got recorded depended on the order the references were listed in.
    The key is therefore the whole association, and newness stays with the
    digest where it belongs.
    """
    events: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    def add(digest: str, source: str, where: str) -> None:
        key = (digest, source, where)
        if not digest or key in seen:
            return
        seen.add(key)
        events.append({"event": "evidence", "round": round_no,
                       "digest": digest, "source": source, "of": where})

    reference = wire.section(parsed.sections, "reference")
    for line in reference.splitlines():
        m = _REF_LINE_RE.match(line)
        if m and m.group("digest"):
            add(m.group("digest"), "reference",
                wire.normalize_path(m.group("path")))

    evidence_section = parsed.sections.get("evidence", "")
    records, error, _ = validate.parse_attestations(evidence_section)
    for record in (records or []) if not error else []:
        output = record.get("output") or {}
        add(output.get("sha256", ""), "gate-output", record.get("id", "?"))
    return events


def verdict_events(parsed: wire.Verdict, round_no: int, digest: str,
                   size: int, tokens: int | None = None,
                   answering: list[dict] | None = None) -> list[dict]:
    """Verdict, alias, finding and closure events, exactly as `ledger add`
    records them (moved here from the CLI so `close` and `ledger add` cannot
    drift into two shapes of the same fact).

    `answering` is the disposition record set this verdict's closures
    answer (round-6 F1) — the same set `_required_closures` validates
    against, one round before this one. Each closure is stamped with the
    round of the matching disposition, so a later reader never has to
    infer what a closure answers from round arithmetic alone: a verdict
    that both closes fp X and re-raises fp X as a new finding in the same
    breath is otherwise indistinguishable, by round number, from one
    closure answering its own round's finding.

    Round-7 F1: that inference is not just unnecessary when `answering`
    is supplied, it is UNSAFE when it is not, for exactly one admitted
    shape — a closure whose fingerprint is ALSO among this verdict's own
    findings. There the round-comparison fallback every OTHER closure may
    still fall back to (`Ledger._answer_target_round`, for legacy or
    hand-built events whose domain is provably a single ruling) would
    resolve to this verdict's own new finding, silently making a closure
    of the PRIOR ruling settle the one it cannot possibly answer. This
    function refuses before returning or appending anything rather than
    emit that closure unstamped.
    """
    event = {"event": "verdict", "round": round_no,
             "sha": parsed.sha, "verdict": parsed.verdict,
             "source_digest": digest, "bytes": size,
             "finding_ids": len(parsed.findings)}
    if tokens is not None:
        event["tokens"] = tokens
    events = [event]
    # A `## tool feedback` section — the debug round's ask, though a
    # reviewer may volunteer it on any round — is recorded so the
    # critiques of the tool's own performance accumulate somewhere a
    # report can reach (2026-08-31). Advisory prose about loupe itself:
    # it feeds no breaker, certifies nothing, and its absence on a
    # debug-stamped round is between the humans, not a refusal.
    feedback = wire._split_sections(parsed.body).get(
        vocab.TOOL_FEEDBACK_SECTION, "").strip()
    if feedback:
        events.append({"event": "tool_feedback", "round": round_no,
                       "sha": parsed.sha, "text": feedback})
    for f in parsed.findings:
        if f.legacy_fingerprint() != f.fingerprint():
            events.append(alias_event(f.legacy_fingerprint(), f.fingerprint()))
        events.append({
            "event": "finding", "round": round_no, "id": f.id,
            "fp": f.fingerprint(), "severity": f.severity,
            "classification": f.classification, "title": f.title,
            "anchor_path": f.anchor_path,
            "falsification": f.falsification,
            # Round 1 F7: carried from the verdict, not hardcoded.
            "preventable_by": f.preventable_by or None,
            # Round 2 F6: which paths this finding cites. The repetition
            # breaker joins reference-digest evidence to finding identity on
            # exactly this list, so it is recorded with the finding rather
            # than re-derived from prose whenever a report is rendered.
            "cites": f.cited_paths})
        # Round 1 F6, the half that makes the repetition exemption REACHABLE.
        # The breaker asks whether a finding returning after a refutation is
        # backed by new content-addressed evidence for that identity. The
        # fingerprint is computed from classification, anchor and title — not
        # from Evidence — so a reviewer can re-assert the same finding with
        # genuinely new evidence and keep the identity, which is exactly the
        # case the exemption exists for. Digesting the Evidence text makes
        # "new" decidable: repeat the same words and the digest repeats.
        if f.evidence.strip():
            events.append({"event": "evidence", "round": round_no,
                           "fp": f.fingerprint(),
                           "digest": sha256_text(f.evidence.strip()),
                           "source": "verdict", "of": f.id})
    answering_by_fp = {}
    for rec in (answering or []):
        fp = rec.get("fp") or rec.get("fingerprint") or ""
        if fp:
            answering_by_fp[fp] = rec
    reraised_fps = {f.fingerprint() for f in parsed.findings}
    # The declared residue is recorded by FINGERPRINT (brief
    # `convergence-blind-to-reclassification`). Resolution happens once,
    # here, where the verdict that declares `F1` and the finding that IS F1
    # are the same document; a round-local id on the event would be a
    # pointer into a namespace no later reader holds.
    fp_by_finding_id = {f.id: f.fingerprint() for f in parsed.findings}
    for c in parsed.closures:
        rec = answering_by_fp.get(c.fp)
        unknown = [r for r in c.residue if r not in fp_by_finding_id]
        if unknown:
            # Unreachable through `close`, which validates first
            # (C-RESIDUE-UNKNOWN) — and stated as a refusal anyway, because
            # the alternative when it IS reached is a closure event whose
            # declared edge silently names fewer residues than the reviewer
            # wrote, which is the shrinking count this brief is about.
            raise Refusal(
                f"{c.fp}: the closure declares `Residue: "
                f"{', '.join(unknown)}`, and this verdict carries no such "
                f"finding — the declared edge cannot be recorded by "
                f"fingerprint because there is no fingerprint to record",
                next_cmd="",
                remedy="the reviewer names the id of a finding in this same "
                       "verdict, or drops the `Residue:` line")
        # Scoped to `withdrawn` (round-7 F1): it is the one closure term
        # that SETTLES a ruling (`vocab.FINDING_ANSWERS`) — the only kind
        # whose mistargeting has any observable effect (`sustained` and
        # the other OPEN-effect terms are read for their own round, never
        # for the ruling they target, so a collision there settles
        # nothing and is not the authorization gap this finding names).
        if rec is None and c.closure == "withdrawn" and c.fp in reraised_fps:
            raise Refusal(
                f"{c.fp}: this verdict both closes it and re-raises it as "
                f"a new finding in the same round, and no disposition "
                f"record names which ruling the closure answers — the "
                f"prior one it closes, or this round's own new one. Round "
                f"arithmetic cannot tell those apart (round-7 F1), so "
                f"binding it by inference would silently settle whichever "
                f"one the fallback happens to prefer",
                next_cmd="",
                remedy="a person supplies the disposition record the "
                       "closure answers (the prior round's standing "
                       "dispositions), or the reviewer re-issues the "
                       "verdict without raising the identical fingerprint "
                       "as a new finding in the same verdict that closes it")
        answers_round = (int(rec["round"])
                         if rec is not None and rec.get("round") is not None
                         else None)
        events.append(c.as_event(
            round_no, answers_round=answers_round,
            residue_fps=[fp_by_finding_id[r] for r in c.residue]))
    return events


#: Event kinds `import-legacy` admits — DERIVED from the closed schema
#: rather than restated beside it (round-9 F2: the allowlist was a
#: hand-written set with no external authority, so a kind could be admitted
#: here and described nowhere). `finding_waiver` is absent for a reason no
#: schema entry could carry: a human waiver is a decision `loupe waive`
#: records against a name a person supplies to that command, never a JSON
#: row a generic importer cannot authenticate at all (round-8 F2).
LEGACY_IMPORT_KINDS = frozenset(vocab.LEGACY_EVENT_SCHEMA)

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")
_HEX40 = re.compile(r"\A[0-9a-f]{40}\Z")
_HEX16 = re.compile(r"\A[0-9a-f]{16}\Z")


def _is_int(value) -> bool:
    # `True` is an int in Python and is not a round number. Every numeric
    # member here is a canonical integer or it is a defect.
    return isinstance(value, int) and not isinstance(value, bool)


def _payload_problem(value) -> str | None:
    """A disposition payload's TYPES, closed; its key set deliberately not.

    The payload is the author's own record, and §5.2 fixes only its floor
    (`DISPOSITION_PAYLOADS`) — a legacy corpus's extra key is vocabulary,
    not a forgery surface, since nothing outside the mandatory keys carries
    authorization weight. What IS closed is the shape a reader can assume:
    an object of strings, or of one-level objects of strings. That is the
    half `payload: "fabricated"` walked through into an AttributeError.
    """
    if not isinstance(value, dict):
        return f"is {type(value).__name__}, not an object"
    for key, item in value.items():
        if not isinstance(key, str):
            return f"has a non-string key {key!r}"
        if isinstance(item, str):
            continue
        if isinstance(item, dict) and all(
                isinstance(k, str) and isinstance(v, str)
                for k, v in item.items()):
            continue
        return (f"member {key!r} is {type(item).__name__}, not a string or "
                f"an object of strings")
    return None


#: Member type -> the closed vocabulary a value of it must belong to. Pairs
#: rather than a literal mapping so the type names stay what they are —
#: names of member TYPES, never keys of a result payload.
_LEGACY_ENUM_TYPES = (
    ("closure_term", vocab.CLOSURES),
    ("disposition_term", vocab.DISPOSITIONS),
    ("amendment_outcome", vocab.TEST_AMENDMENT_OUTCOMES),
    ("lineage_kind", vocab.LINEAGE_KINDS),
    ("import_kind", vocab.LEGACY_IMPORT_ROW_KINDS),
    ("verdict_term", vocab.VERDICTS),
    ("envelope_kind", vocab.legacy_envelope_kinds()),
)


def _type_problem(value, kind: str) -> str | None:
    """What is wrong with `value` as a member of type `kind`, or None.

    One table for every admitted member of every admitted kind, so a
    container where a scalar belongs is refused by the same rule that
    refuses a string where a round number belongs.
    """
    enums = dict(_LEGACY_ENUM_TYPES)
    if kind in enums:
        return (None if value in enums[kind]
                else f"is {value!r}, not one of {list(enums[kind])}")
    if kind == "subtype_or_null":
        return (None if value is None or value in vocab.ACCEPTED_SUBTYPES
                else f"is {value!r}, not null or one of "
                     f"{list(vocab.ACCEPTED_SUBTYPES)}")
    if kind == "round":
        return (None if _is_int(value) and value > 0
                else f"is {value!r}, not a positive integer")
    if kind == "count":
        return (None if _is_int(value) and value >= 0
                else f"is {value!r}, not a non-negative integer")
    if kind in ("text", "fingerprint", "event_name"):
        return (None if isinstance(value, str) and value.strip()
                else f"is {value!r}, not a non-empty string")
    if kind == "text_or_blank":
        return (None if isinstance(value, str)
                else f"is {value!r}, not a string")
    if kind == "text_or_null":
        return (None if value is None or isinstance(value, str)
                else f"is {value!r}, not a string or null")
    if kind == "text_list":
        return (None if isinstance(value, list)
                and all(isinstance(x, str) for x in value)
                else f"is {value!r}, not a list of strings")
    if kind == "digest":
        return (None if isinstance(value, str) and _HEX64.match(value)
                else f"is {value!r}, not a 64-character lowercase hex digest")
    if kind == "commit":
        return (None if isinstance(value, str) and _HEX40.match(value)
                else f"is {value!r}, not a 40-character lowercase hex commit")
    if kind == "stamp":
        return (None if isinstance(value, str) and _HEX16.match(value)
                else f"is {value!r}, not a 16-character lowercase hex uid")
    if kind == "payload":
        return _payload_problem(value)
    raise AssertionError(f"no check declared for member type {kind!r}")


def _closed_shape_problems(row: dict, where: str, required: dict,
                           optional: dict) -> list[str]:
    """One object against one closed field grammar: every required member
    present and of its type, every optional member of its type, and every
    OTHER member refused by name."""
    problems = []
    for member, kind in sorted(required.items()):
        if member not in row:
            problems.append(f"{where}: required member {member!r} is absent")
            continue
        bad = _type_problem(row[member], kind)
        if bad:
            problems.append(f"{where}: {member} {bad}")
    for member, kind in sorted(optional.items()):
        if member in row:
            bad = _type_problem(row[member], kind)
            if bad:
                problems.append(f"{where}: {member} {bad}")
    unknown = sorted(set(row) - set(required) - set(optional))
    if unknown:
        problems.append(
            f"{where}: unknown member(s) {unknown} — the legacy grammar is "
            f"closed, and a member nobody reads is a member whose meaning "
            f"the record cannot state")
    return problems


#: A `source_path` this boundary will even ASK git about. The value is
#: caller-controlled and is interpolated into `<commit>:<path>`, so the
#: shape is closed rather than the escapes enumerated: one or more
#: non-empty, non-dot segments, no leading slash, no `..`, no NUL and no
#: newline (a newline would also make `ls-tree`'s one-entry check a lie).
#:
#: Round-11 F6, closed 2026-09-03: the sentence above was the whole claim
#: and the pattern delivered half of it. `[^/\x00\n]+` excludes exactly
#: three characters, so a `.` or `..` segment was a legal segment and
#: `a/../x`, `a/./x` and `.` itself resolved through git to some OTHER
#: tree path than the one the row named — a path the record then reported
#: as the cited source. The per-segment lookaheads reject a segment that
#: IS `.` or `..`; a name that merely starts with a dot (`.gitignore`,
#: `...odd`) is an ordinary tracked path and stays legal. Leading,
#: trailing and repeated separators were already excluded and still are.
_SOURCE_PATH_RE = re.compile(
    r"\A(?!/)(?:(?!\.\.?/)[^/\x00\n]+/)*(?!\.\.?\Z)[^/\x00\n]+\Z")


class SourceAuthority:
    """The source bytes an imported batch may derive from: THIS repository's
    own shared git history, at one anchor commit (lineage 20 round 10 F2).

    Round 9's authority was a manifest file the CALLER wrote — `{path,
    sha256}` entries this tool hashed. It read like external verification
    and was not: the party who writes the batch also writes the manifest, so
    a throwaway file, hashed by its own author, "substantiated" a finding
    titled `fabricated`. The manifest is gone. Two properties replace it,
    and neither alone would have closed that reproduction:

    ANCHORED. A row names `source_path`, a tracked path, and the content is
    read from `<commit>:<path>` where `commit` was verified to be ancestry
    of a ref the REMOTE ITSELF answers for — `ls-remote`, the observation
    `emit.ensure_pushed` makes after a push, for the same reason: history
    one machine can show a second. The row's `source_digest` is NEVER
    believed; it is recomputed here and compared, so it is a commitment the
    caller can be held to rather than an input.

    Measured while designing this, and it is why the check is `ls-remote`
    rather than `refs/remotes/*`: a remote-tracking ref is LOCAL, WRITABLE
    state. `git update-ref refs/remotes/origin/main <any local commit>`
    exits 0 with no network and no remote, and `for-each-ref --contains`
    then names it. Resting the anchor on that would have been round 9's
    mistake one level down — evidence the same party can author.

    CONTAINED. The row's own claim text must occur IN that content
    (`vocab.LEGACY_CONTAINMENT`, `contains` below). This is the half that
    makes the citation about the row: an anchored digest alone proves only
    that some real file was named beside an arbitrary claim, which is
    exactly what round 10 walked through.

    THE STATED LIMIT, precisely. This is not non-repudiation. Someone able
    to push to this remote — or to rewrite this clone's remote URL — can
    commit a file saying whatever a row needs and then cite it; the anchor
    commit's message and authorship are not verified; containment is a
    SUBSTRING test over the whole decoded file, so a source that happens to
    contain the text substantiates it whatever the surrounding context
    meant; and a legitimate derivation is still not RE-DERIVED here — a
    generic importer does not know any repository's derivation (§11).
    `--no-fetch` weakens the anchor deliberately and says so on the result
    (`source_witness`). What it does establish, and the manifest did not:
    the cited bytes are already durable, shared, attributable history rather
    than a scratch file written in the same terminal session as the import,
    and the row's own words are traceable into those bytes rather than
    merely consistent with a sibling field the same author wrote.
    """

    def __init__(self, commit: str, refs: list[str], witness: str,
                 refs_containing, run, read):
        self.commit = commit
        #: The refs that carry the anchor. Reported, not just counted: which
        #: shared branch carries it is the fact a person auditing the import
        #: needs.
        self.refs = list(refs)
        #: HOW those refs were established — observed at the remote, or read
        #: from local remote-tracking refs under `--no-fetch`. The record
        #: says which, because they are not the same claim.
        self.witness = witness
        self._refs_containing = refs_containing
        self._run = run
        self._read = read
        self._blobs: dict[str, tuple[bytes | None, str | None]] = {}
        self._commits: dict[str, bool] = {}

    def __len__(self) -> int:
        return len([p for p, (data, _) in self._blobs.items()
                    if data is not None])

    def paths(self) -> list[str]:
        return sorted(p for p, (data, _) in self._blobs.items()
                      if data is not None)

    def blob(self, path) -> tuple[bytes | None, str | None]:
        """The bytes at `<anchor>:<path>`, or why there are none. Cached, so
        a batch of 162 rows over 20 artifacts asks git 20 times."""
        if not isinstance(path, str) or not _SOURCE_PATH_RE.match(path):
            return None, (f"source_path {path!r} is not a repository path "
                          f"this boundary will resolve")
        if path in self._blobs:
            return self._blobs[path]
        self._blobs[path] = _anchored_blob(self._run, self._read,
                                           self.commit, path)
        return self._blobs[path]

    def digest(self, path: str) -> str:
        """The sha256 THIS TOOL computes over the anchored bytes."""
        return hashlib.sha256(self.blob(path)[0]).hexdigest()

    def size(self, path: str) -> int:
        return len(self.blob(path)[0])

    def contains(self, path: str, needle: str) -> bool | None:
        """Whether `needle` occurs in the decoded source content. None means
        the question could not be asked — the bytes are not UTF-8 — which
        the caller must treat as a refusal, never as a pass."""
        try:
            text = self.blob(path)[0].decode("utf-8")
        except UnicodeDecodeError:
            return None
        return needle in text

    def shared_commit(self, sha: str) -> bool:
        """Whether `sha` is carried by the same witness the anchor answered
        to. A legacy corpus of THIS repository reviewed THIS repository's
        commits, so a request/verdict row's target is checkable rather than
        assertable — and it is checked against the SAME evidence, never a
        weaker one, or the door would have two bars."""
        if sha not in self._commits:
            self._commits[sha] = bool(self._refs_containing(sha))
        return self._commits[sha]


def _anchored_blob(run, read, commit: str,
                   path: str) -> tuple[bytes | None, str | None]:
    """The bytes at `<commit>:<path>`, or why there are none.

    A module-level function taking its runners as parameters, not a method
    reading them off `self`, because that is the shape `bin/loupe-git-read-audit`
    resolves: an object-graph read reached through an attribute is a read the
    boundary file cannot classify, and an unclassified read is an unmade
    decision (lineage 12 round 3 F1).
    """
    at = commit[:12]
    try:
        # `-z` (round-11 F6): without it git QUOTES a name it considers
        # unusual, so the entry's name field is a rendering rather than the
        # path, and the check below would compare a path against a
        # C-escaped picture of one. NUL-terminated records also make the
        # more-than-one-entry test exact, where `\n` was a proxy for it.
        listing = run("ls-tree", "--full-tree", "-z", commit, "--", path)
    except _UNUSABLE as exc:
        return None, f"{path} cannot be listed at {at} ({exc})"
    entries = [e for e in listing.split("\0") if e.strip()]
    if not entries:
        return None, (f"{path} is not tracked at {at} — an imported row "
                      f"cites this repository's shared history, and this "
                      f"path is not in it")
    # Round 3 F2's lesson, one boundary over: a tree may carry one path
    # twice, and then WHICH entry was read is not established.
    if len(entries) > 1:
        return None, f"{path} has more than one tree entry at {at}"
    meta, tab, name = entries[0].partition("\t")
    fields = meta.split()
    if not tab or len(fields) < 3:
        return None, f"the tree entry for {path} at {at} cannot be read"
    # Round-11 F6: git ANSWERS about a path of its own choosing. `a/./x`
    # and `a/../x` are legal arguments that resolve to other tree paths,
    # and the grammar above now refuses those — but the grammar is a claim
    # about the string, and this is the claim about what git returned. The
    # entry read must be the entry asked for, or the digest, the size and
    # the containment test below all describe a file the row does not name.
    if name != path:
        return None, (f"the tree entry at {at} is {name!r}, not the "
                      f"requested {path!r}: the bytes read must be the "
                      f"bytes the row cites")
    if fields[0] not in vocab.GIT_FILE_MODES:
        # Round 4 F1: type `blob` covers a symlink, whose content is a path,
        # not the bytes a reader would see.
        return None, (f"{path} at {at} is {vocab.git_mode_name(fields[0])}, "
                      f"not a regular file")
    try:
        return read("show", f"{commit}:{path}"), None
    except _UNUSABLE as exc:
        return None, f"{path} at {at} cannot be read ({exc})"


def _present(run, sha: str) -> bool:
    try:
        run("cat-file", "-e", f"{sha}^{{commit}}")
        return True
    except _UNUSABLE:
        return False


def _observed_refs_containing(run, remote: str, sha: str) -> list[str]:
    """Every ref THE REMOTE ITSELF answers for whose history contains `sha`.

    `ls-remote` is the remote's own statement about its refs — the exact
    observation `emit.ensure_pushed` makes after a push (§9bis.4), and the
    reason it is that call and not `for-each-ref refs/remotes/`: a
    remote-tracking ref is local, writable state. `git update-ref
    refs/remotes/origin/main <any local commit>` exits 0 offline, and
    `--contains` then names it, so the anchor would rest on evidence its own
    author can write. Measured, 2026-09-02.

    Ancestry is still computed locally, against the object store, which is
    why the caller runs with replacement objects off: the TIP comes from the
    remote, and what that tip's history contains is read from the original
    graph. A tip whose object this clone does not have vouches for nothing —
    fail-closed, and the fetch is what supplies it.
    """
    if not _present(run, sha):
        return []
    out = []
    for line in run("ls-remote", remote).splitlines():
        parts = line.split("\t")
        if len(parts) != 2 or not _HEX40.match(parts[0].strip()):
            continue
        tip, ref = parts[0].strip(), parts[1].strip()
        if not _present(run, tip):
            continue
        try:
            run("merge-base", "--is-ancestor", sha, tip)
        except _UNUSABLE:
            continue
        out.append(ref)
    return out


def _remote_refs_containing(run, sha: str) -> list[str]:
    """Every `refs/remotes/*` ref whose history contains `sha`, or [].

    The `--no-fetch` witness, and the WEAKER one: these refs are local state
    a person with a shell can write, so this says the bytes were fetched
    from somewhere at some point, not that a remote attests them now. The
    flag is the only way to reach it and the result records that it was
    used (`source_witness`).
    """
    if not _present(run, sha):
        return []
    try:
        out = run("for-each-ref", "--contains", sha, "--format=%(refname)",
                  "refs/remotes/")
    except _UNUSABLE:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _anchor_remote(remotes: list[str]) -> str | None:
    """`origin` when it exists, else the single remote. More than one and
    neither named `origin`, and the tool does not invent a decision
    (§9bis.3)."""
    if "origin" in remotes:
        return "origin"
    return remotes[0] if len(remotes) == 1 else None


def read_source_authority(cfg: Config, commit_ref: str, fetch: bool = True,
                          git=None) -> tuple[SourceAuthority | None,
                                             list[str]]:
    """Resolve and VERIFY the anchor commit: (authority, problems).

    Every defect is reported as prose, never raised — the caller refuses the
    whole import atomically and a person names a different anchor.

    `git` is injectable exactly as `take`'s and `probe_target`'s are, so
    every refusal below is testable without a network or a remote.
    """
    run = git or (lambda *a: _git(cfg.repo_root, *a, no_replace=True,
                                  ceiling=git_ceiling(cfg)))
    read = (lambda *a: run_bytes(cfg, git, *a, no_replace=True))
    try:
        remotes = [r for r in run("remote").splitlines() if r.strip()]
    except _UNUSABLE as exc:
        return None, [f"this clone cannot be asked about its remotes ({exc}), "
                      f"so no shared history can anchor an import"]
    if not remotes:
        return None, [
            "no remote is configured: an imported row cites bytes that are "
            "already shared history, and a clone with no remote has none. "
            "There is no --local-only here on purpose — that flag would be "
            "the manifest's hole again, one level down (round-10 F2)"]
    remote = _anchor_remote(remotes)
    if remote is None:
        return None, [
            f"{len(remotes)} remotes exist ({', '.join(remotes)}) and none is "
            f"named `origin`, so which one this repository's shared history "
            f"lives at is not derivable, and the tool does not invent a "
            f"decision"]
    if fetch:
        try:
            # --prune matters even here: the fetch is what brings the
            # observed tips into the object store, and a pruned clone is the
            # one whose local refs cannot outlive what the remote carries.
            run("fetch", "--prune", remote)
        except GitTimeout:
            raise
        except _UNUSABLE as exc:
            return None, [
                f"cannot fetch {remote} ({exc}): the anchor is judged against "
                f"refs the remote answers for, and a clone that could not "
                f"reach it has nothing to judge against. Restore access, or "
                f"re-run with --no-fetch to fall back to the remote-tracking "
                f"refs as they stand — a weaker claim the record will say was "
                f"made"]
    try:
        commit = run("rev-parse", "--verify", "--end-of-options",
                     f"{commit_ref}^{{commit}}").strip()
    except _UNUSABLE as exc:
        return None, [f"--source-commit {commit_ref!r} resolves to no commit "
                      f"in this clone ({exc})"]
    if not _HEX40.match(commit):
        return None, [f"--source-commit {commit_ref!r} resolved to {commit!r}, "
                      f"which is not a commit id"]
    if fetch:
        def refs_containing(sha):
            return _observed_refs_containing(run, remote, sha)
        witness = f"observed at {remote} (ls-remote), ancestry from this clone"
    else:
        def refs_containing(sha):
            return _remote_refs_containing(run, sha)
        witness = ("remote-tracking refs as they stand (--no-fetch): LOCAL "
                   "state, which a person with a shell can write")
    try:
        refs_with = refs_containing(commit)
    except GitTimeout:
        raise
    except _UNUSABLE as exc:
        return None, [f"cannot ask {remote} which refs carry "
                      f"{commit[:12]} ({exc})"]
    if not refs_with:
        return None, [
            f"--source-commit {commit_ref} ({commit[:12]}) is ancestry of no "
            f"ref this clone accepts as shared — witness: {witness}. It is "
            f"local history, and an import may cite only bytes that were "
            f"already shared. This is the whole gain over the source manifest "
            f"round 9 shipped: a file invented in the same session as the "
            f"batch cannot be anchored (round-10 F2)"]
    return SourceAuthority(commit, refs_with, witness, refs_containing,
                           run, read), []


def _identity_problems(row: dict, where: str) -> list[str]:
    """A ruling row's fingerprint, RECOMPUTED from the identity inputs the
    row itself declares (round-9 F2).

    `fp` is otherwise a free label: nothing stopped two fabricated findings
    from claiming any two identities an alias could then collapse, or one
    row from claiming an identity that belongs to a real standing finding.
    The fingerprint function is the tool's own (§5.3b) and any corpus whose
    identities are meaningful here computed them with it, so requiring the
    row to carry its inputs and hash to what it claims costs a real
    derivation nothing and makes the identity machine-derived rather than
    asserted.
    """
    text = row.get("title") if row.get("event") == "finding" \
        else row.get("verbatim")
    computed = fp_compute(
        row.get("classification", ""), row.get("anchor_path", ""),
        row.get("anchor", ""), text or "",
        invariant_id=row.get("invariant_id", ""),
        citations=row.get("citations", ""))
    if computed != row.get("fp"):
        return [f"{where}: fp {row.get('fp')!r} is not the fingerprint of "
                f"the identity facts this row declares — those hash to "
                f"{computed}. An imported ruling's identity is recomputed, "
                f"never taken on the batch's word (round-9 F2)"]
    return []


def _claimed_texts(e: dict) -> list[tuple[str, str]]:
    """(member, text) pairs whose words this row claims come from its source.

    Derived from `vocab.LEGACY_CONTAINMENT` plus, for a disposition, the
    mandatory substantiation `vocab.DISPOSITION_PAYLOADS` already names per
    term — so what a disposition must prove and what it must contain are one
    table, never two that drift. Absent, null and blank members claim
    nothing and are not checked; that they are PRESENT where the grammar
    demands them is a separate rule, above.
    """
    out = []
    for member in vocab.LEGACY_CONTAINMENT.get(e.get("event"), ()):
        value = e.get(member)
        if isinstance(value, str) and value.strip():
            out.append((member, value))
    if e.get("event") == "disposition":
        payload = e.get("payload") or {}
        for key in vocab.DISPOSITION_PAYLOADS.get(e.get("disposition"), ()):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                out.append((f"payload.{key}", value))
    return out


def _containment_problems(where: str, path: str, texts,
                          authority: SourceAuthority) -> list[str]:
    """Every claimed text that does NOT occur in the anchored source bytes.

    Round-10 F2's whole reproduction, in one function: the source content
    was `| R1-F1a | one atomic claim |`, the row's title was `fabricated`,
    the digest was real and correctly computed, and the import went through.
    A digest binds a row to bytes; only this binds the row's WORDS to them.
    """
    problems = []
    for member, text in texts:
        held = authority.contains(path, text)
        if held is None:
            return [f"{where}: {path} at {authority.commit[:12]} is not UTF-8, "
                    f"so whether it contains this row's {member} cannot be "
                    f"asked — and an unaskable question is not a pass"]
        if not held:
            problems.append(
                f"{where}: {member} {_excerpt(text)} does not occur in "
                f"{path} at {authority.commit[:12]}, the source this row "
                f"cites — an anchored digest says some real file was named, "
                f"never that this claim came out of it (round-10 F2)")
    return problems


def _excerpt(text: str, width: int = 60) -> str:
    return repr(text if len(text) <= width else text[:width] + "…")


def _row_shape_problems(e: dict, n: int) -> list[str]:
    """One row against the closed schema alone — pure, no ledger, no git.

    Split out 2026-09-03: the door used to read the git source authority
    (a fetch, an ls-remote, tree reads) BEFORE this check, so every
    malformed row paid a round of subprocesses to be refused by name —
    41 s of a 156 s suite in one wrong-type matrix, and a door that did
    not do what the design said (shape refused before Git is touched)."""
    kind = e.get("event")
    where = f"line {n} ({kind})"
    schema = vocab.LEGACY_EVENT_SCHEMA[kind]
    optional = {**schema["optional"], **vocab.LEGACY_COMMON_OPTIONAL}
    required = dict(schema["required"])
    for (k, member, value), demanded in \
            vocab.LEGACY_CONDITIONAL_REQUIRED.items():
        if k == kind and e.get(member) == value:
            required[demanded] = optional.pop(
                demanded, required.get(demanded, "text"))
    problems = _closed_shape_problems(e, where, required, optional)
    if e.get("event") != kind:                       # unreachable by routing
        problems.append(f"{where}: event {e.get('event')!r} is not {kind!r}")
    return problems


def legacy_shape_problems(events: list[dict]) -> list[str]:
    """Pass 1 of `legacy_import_problems` on its own: the kind domain and
    every row's closed shape, for the caller to run before it reads any
    source authority. Empty means the batch may proceed to provenance."""
    bad_kinds = sorted({str(e.get("event")) for e in events
                        if e.get("event") not in LEGACY_IMPORT_KINDS})
    if bad_kinds:
        return [f"event kind(s) {bad_kinds} are not in the legacy import "
                f"domain {sorted(LEGACY_IMPORT_KINDS)}; a human waiver in "
                f"particular is recorded only by the dedicated waive "
                f"command, never imported (round-8 F2)"]
    problems: list[str] = []
    for n, e in enumerate(events, 1):
        problems.extend(_row_shape_problems(e, n))
    return problems


def _row_problems(e: dict, n: int, authority: SourceAuthority) -> list[str]:
    """One row against the closed schema and the git-anchored source."""
    problems = _row_shape_problems(e, n)
    if problems:
        return problems
    kind = e.get("event")
    where = f"line {n} ({kind})"
    path = e["source_path"]
    data, unreadable = authority.blob(path)
    if unreadable:
        return [f"{where}: {unreadable} (anchor {authority.commit[:12]}, "
                f"contained in {', '.join(authority.refs)})"]
    actual = authority.digest(path)
    if actual != e["source_digest"]:
        return [f"{where}: {path} at {authority.commit[:12]} hashes to "
                f"{actual}, this row claims {e['source_digest']} — the digest "
                f"is recomputed from the anchored bytes, never taken from the "
                f"row that cites them (round-10 F2)"]
    problems.extend(_containment_problems(where, path, _claimed_texts(e),
                                          authority))
    if kind in vocab.legacy_envelope_kinds():
        # An envelope row states two facts about bytes rather than about a
        # claim, and both are machine-checkable here: how many bytes the
        # source is, and whether the commit it says was reviewed is a commit
        # this repository actually shares. The round the envelope belongs to
        # is checked over the whole batch (`_round_shape_problems`).
        if e["bytes"] != authority.size(path):
            problems.append(
                f"{where}: bytes {e['bytes']} but {path} at "
                f"{authority.commit[:12]} is {authority.size(path)} bytes")
        if not authority.shared_commit(e["sha"]):
            problems.append(
                f"{where}: target {e['sha'][:12]} is contained in no "
                f"remote-tracking ref of this clone — a legacy corpus of this "
                f"repository reviewed this repository's commits, so a "
                f"{kind} row's target is checkable rather than assertable")
    if kind in ("finding", "import"):
        problems.extend(_identity_problems(e, where))
    if kind == "disposition":
        payload = e["payload"]
        missing = [k for k in vocab.DISPOSITION_PAYLOADS.get(
            e["disposition"], ()) if not str(payload.get(k, "")).strip()]
        if missing:
            problems.append(
                f"{where}: {e['disposition']} disposition is missing "
                f"mandatory payload {missing} — the same substantiation the "
                f"product path's validator demands")
    return problems


#: What one settling answer resolves to. `bound` is the only admissible
#: state; the rest name why an answer settles nothing a reader can trust.
_BOUND = "bound"


def _answer_binding(kind: str, event: dict, rulings) -> tuple:
    """Which ruling MATERIAL `event` answers under a given ruling set —
    (state, detail), where `state` is `_BOUND` or the defect's name.

    `rulings` is round -> the uids of the rulings of this identity AT that
    round, so a bound answer's detail is `(round, uids)` and not a round
    number alone. Round-10 F1: reducing the binding to `(state, round)` made
    the comparison in `_lifecycle_problems` blind to WHICH ruling occupies
    the round. A second import could alias a settled identity onto a
    distinct new finding at the SAME round, and a persisted acceptance —
    unchanged, already on disk, its round unchanged — silently began
    covering a ruling it never answered. The round number is the slot; the
    uid set is what is in it, and an answer is bound to what is in it.

    The target itself comes from `Ledger._answer_target_round`, the same
    function the ledger reads with, so the importer's idea of what an
    answer targets cannot drift from the reader's. What is layered on top
    are the states a reader has no way to represent: a stamp that is not a
    round number, a stamp naming a round the closure predates, an unstamped
    answer whose own round ALSO carries a ruling of this identity (round-7
    F1's collision), and a target that is no ruling at all.
    """
    own = int(event.get("round") or 0)
    raw = event.get("answers_round")
    rounds = set(rulings)
    if kind != "disposition":
        if raw is not None and (not _is_int(raw) or raw <= 0):
            return ("malformed", raw)
        if raw is not None and raw >= own:
            return ("future", raw)
        if raw is None and own in rounds:
            return ("ambiguous", own)
    target = Ledger._answer_target_round(kind, event, rounds)
    if target is None or target not in rulings:
        return ("unbound", target)
    return (_BOUND, (target, rulings[target]))


def _answer_term(e: dict):
    """The (kind, term) pair `vocab.FINDING_ANSWERS` classifies this event
    by, whatever kind of answer it is."""
    kind = e.get("event")
    if kind == "closure":
        return (kind, e.get("closure"))
    if kind == "disposition":
        return (kind, e.get("disposition"))
    if kind == vocab.FINDING_WAIVER_EVENT:
        return (kind, None)
    return None


def _rulings_by_identity(events, resolve) -> dict[str, dict[int, tuple]]:
    """Resolved identity -> round -> the ruling MATERIAL at that round: the
    content uid of every ruling event, in recorded order (round-10 F1).

    `ledger._uid` is the same content identity the breakers use to say what
    a human decided about (`material_id`), and for the same reason: a slot's
    occupant changing is not the slot changing. Order-preserving rather than
    a set, so two rulings that happen to be byte-identical still count as
    two — multiplicity is part of what an answer would newly cover.
    """
    rounds: dict[str, dict[int, list]] = {}
    for e in events:
        if is_ruling(e):
            fp = resolve(e.get("fp", ""))
            if fp:
                by_round = rounds.setdefault(fp, {})
                by_round.setdefault(int(e.get("round") or 0),
                                    []).append(_event_uid(e))
    return {fp: {r: tuple(uids) for r, uids in by_round.items()}
            for fp, by_round in rounds.items()}


def _lineage_scopes(ledger: Ledger, events: list[dict], lineage: str
                    ) -> list[tuple[str, list[dict], list[dict]]]:
    """`(key, the events of that lineage today, the events it would hold
    after this batch)` for every lineage this batch can reach (round-2 F4).

    Two things are true at once and this is where they meet. Rulings and
    answers are PER LINEAGE — that is the ruled semantics of
    `keyed-lineage`, and the reason the destination's own set is the only
    one a candidate row may bind against. Identity aliases are GLOBAL — one
    graph over the whole file — so an alias row in this batch can change
    what a persisted answer in a DIFFERENT lineage answers, even though not
    one of its rows is appended there.

    So every lineage is a scope, and only the destination's scope gains the
    batch. That is what keeps the global alias check global while no
    lineage's rounds or rulings are ever read as another's.
    """
    keys = ledger.lineages()
    if str(lineage) not in keys:
        keys = keys + [str(lineage)]
    scopes = []
    for key in keys:
        persisted = ledger.current(key)
        scopes.append((key, persisted,
                       persisted + events if key == str(lineage)
                       else persisted))
    return scopes


def _lifecycle_problems(ledger: Ledger, events: list[dict],
                        lineage: str) -> list[str]:
    """Every settling or overruling answer — PERSISTED and candidate —
    recomputed under the identity graph and ruling set this batch would
    leave behind (round-9 F1).

    Round 8 validated the rows the CURRENT call supplied and stopped there,
    which is a statement about one moment. The meaning of an unstamped
    answer is re-derived on every read from the graph and the ruling set,
    and a LATER, separate import can change both: call 1 imported a round-1
    ruling and a round-2 withdrawal that correctly settled round 1; call 2
    added an alias and a round-2 re-ruling, and the closure appended by
    call 1 — already validated, already on disk — became an answer to a
    ruling that did not exist when it was recorded.

    So an import may not retroactively change what a persisted answer
    answers. Each answer is bound twice — once under the ledger as it
    stands, once under the ledger this batch would create — and a
    persisted answer that WAS bound must come out bound to the same ruling.
    An answer already broken before this call is not made this call's
    fault: refusing it would deadlock a door on damage a later import
    cannot repair, and the state it is in is unchanged either way.

    Candidate rows have no `before`, so they must simply come out bound.
    Their unstamped case is already gone: `LEGACY_CONDITIONAL_REQUIRED`
    admits no unstamped `withdrawn` closure at all, which is what makes the
    whole cross-batch vector unreachable for anything this door appends —
    an explicit stamp means the same thing under every future graph.

    Both bindings are computed PER LINEAGE since 0.20.1 (round-2 F4). The
    whole-ledger ruling scan let another review's ruling substantiate this
    batch's answer: a withdrawal targeting a round-1 ruling that existed
    only in a concurrent lineage validated here and was then appended to
    the destination, where the reader — `_all_rulings(lineage)` and
    everything derived from it — finds no such ruling at all. A candidate
    answer therefore binds against `ledger.current(lineage)` plus the batch,
    the exact state the append leaves behind. Aliases stay global, and the
    persisted half runs over EVERY lineage's own answer/ruling set
    (`_lineage_scopes`), because a global alias row moves identities in
    lineages this batch appends nothing to.
    """
    batch_alias = [e for e in events if e.get("event") == "lineage"]
    before_alias = [e for e in ledger.events() if e.get("event") == "lineage"]
    after_alias = before_alias + batch_alias

    def resolver(alias_events):
        def resolve(fp):
            return resolve_identity(fp, alias_events)
        return resolve

    resolve_before = resolver(before_alias)
    resolve_after = resolver(after_alias)

    problems: list[str] = []
    for key, persisted, after_events in _lineage_scopes(ledger, events,
                                                        lineage):
        before_rounds = _rulings_by_identity(persisted, resolve_before)
        after_rounds = _rulings_by_identity(after_events, resolve_after)
        if key == str(lineage):
            for e in events:
                pair = _answer_term(e)
                if pair is None or pair not in vocab.SETTLING_ANSWERS:
                    continue
                fp_after = resolve_after(e.get("fp", ""))
                after = _answer_binding(pair[0], e,
                                        after_rounds.get(fp_after, {}))
                if after[0] != _BOUND:
                    problems.append(_unbound_prose(e, pair, fp_after, after))
        for e in persisted:
            pair = _answer_term(e)
            if pair is None or pair not in vocab.SETTLING_ANSWERS:
                continue
            fp_before = resolve_before(e.get("fp", ""))
            before = _answer_binding(pair[0], e,
                                     before_rounds.get(fp_before, {}))
            if before[0] != _BOUND:
                continue
            fp_after = resolve_after(e.get("fp", ""))
            after = _answer_binding(pair[0], e, after_rounds.get(fp_after, {}))
            if after != before:
                problems.append(_rebind_prose(e, pair, fp_before, before,
                                              fp_after, after))
    return problems


def _rebind_prose(e: dict, pair, fp_before: str, before: tuple,
                  fp_after: str, after: tuple) -> str:
    """Why a persisted answer may not be moved — naming WHICH of the two
    things changed, the slot or its occupant (round-10 F1).

    The second is the case the round-number comparison could not see, and
    it is the one that reads as nothing having happened: same identity, same
    round, a different ruling underneath.
    """
    was_round, was_material = before[1]
    head = (f"{fp_before}: a persisted {pair[0]} ({pair[1] or 'waiver'}) at "
            f"round {e.get('round')!r} answers the round-{was_round} ruling "
            f"of this identity today")
    if after[0] != _BOUND:
        return (f"{head}; after this batch it would answer nothing "
                f"({after[0]}, {after[1]!r} of {fp_after}). An import may not "
                f"reinterpret an answer already on disk (round-9 F1)")
    now_round, now_material = after[1]
    if now_round != was_round:
        return (f"{head}; after this batch it would answer round {now_round} "
                f"of {fp_after} instead. An import may not reinterpret an "
                f"answer already on disk (round-9 F1)")
    added = [u for u in now_material if u not in was_material]
    gone = [u for u in was_material if u not in now_material]
    return (f"{head} — {len(was_material)} ruling event(s) "
            f"[{', '.join(was_material)}]. After this batch that same round "
            f"of {fp_after} would carry {len(now_material)} "
            f"[{', '.join(now_material)}]"
            + (f", adding {', '.join(added)}" if added else "")
            + (f", dropping {', '.join(gone)}" if gone else "")
            + ". The round did not move; WHICH ruling occupies it did, and a "
              "persisted answer may not silently start covering a ruling it "
              "never answered (round-10 F1)")


def _lineage_containment_problems(ledger: Ledger, events: list[dict],
                                  authority: SourceAuthority) -> list[str]:
    """A lineage row whose ENDPOINT is a known ruling must cite a source
    that carries that ruling's claim text (round-10 F2).

    A lineage row has no claim text of its own — it is two fingerprints, and
    a fingerprint occurs in no prose — so `vocab.LEGACY_CONTAINMENT` leaves
    it empty and this is the check that stands in its place. Without it, an
    alias merging two REAL standing findings into one could be sourced to
    any real, anchored file in the repository, which is the same shape as
    the hole round 10 found: a true citation beside an unrelated claim. F1's
    own escape came in through an alias, so this is deliberately not left to
    anchoring alone.

    THE LIMIT, stated: an endpoint that is not a ruling in ledger+batch — a
    derivation's legacy-scheme fingerprint, a pre-split parent — is anchored
    and nothing more, because there is no claim text to look for.
    """
    ruling_texts: dict[str, list[str]] = {}
    for e in ledger.events() + events:
        if not is_ruling(e):
            continue
        text = ruling_facts(e).get("title")
        fp = e.get("fp")
        if isinstance(fp, str) and isinstance(text, str) and text.strip():
            ruling_texts.setdefault(fp, []).append(text)
    problems: list[str] = []
    for n, e in enumerate(events, 1):
        if e.get("event") != "lineage":
            continue
        claims = [(f"{member} ruling text", text)
                  for member in ("from_fp", "to_fp")
                  for text in dict.fromkeys(
                      ruling_texts.get(e.get(member), []))]
        problems.extend(_containment_problems(
            f"line {n} (lineage {e.get('kind')})", e["source_path"], claims,
            authority))
    return problems


def _envelope_round_problems(ledger: Ledger, events: list[dict],
                             lineage: str) -> list[str]:
    """Envelope rounds must be the contiguous sequence 1..N over the
    DESTINATION lineage and the batch together.

    Lineage-scoped since 0.20.1 (round-2 F4). A round is an ordinal of ONE
    review loop, and once N reviews coexist in one ledger the whole file is
    not that loop: scanning `ledger.events()` let a round-2 request recorded
    by an unrelated concurrent review satisfy the contiguity a round-3
    verdict owed its OWN lineage, and the batch imported. The rows are
    appended with `import_lineage` and every downstream reader
    (`Ledger.rounds`, `completed_rounds`, `next_round`) reads that key, so
    this door must judge the same view they will.

    This is what replaces the round-9 manifest's per-artifact `round`
    declaration, and it is a property of the RECORD rather than of a second
    file the same author wrote. The harm it guards is the one F2 measured: a
    bare `verdict` row at round 99 imported and made `rounds_to_clean`
    report 99. A round is an ordinal of one loop, so a record stating round
    99 with no rounds 3..98 describes a loop that never ran.

    THE LIMIT: a corpus whose earliest rounds left no artifact cannot state
    its later envelope rounds either. That is fail-closed and deliberate —
    such a corpus imports its semantic rows, which carry their own rounds,
    and states no envelopes it cannot place.
    """
    kinds = vocab.legacy_envelope_kinds()
    rounds = {int(e["round"]) for e in ledger.current(lineage) + events
              if e.get("event") in kinds and _is_int(e.get("round"))}
    if not rounds:
        return []
    missing = sorted(set(range(1, max(rounds) + 1)) - rounds)
    if not missing:
        return []
    return [f"the request/verdict rounds this batch would leave in lineage "
            f"{lineage} are "
            f"{sorted(rounds)}, which skips {missing}: a round is an ordinal "
            f"of one review loop, and a record that names round {max(rounds)} "
            f"without {missing} describes a loop that never ran (round-9 F2's "
            f"round-99 verdict, now judged against the record itself rather "
            f"than a manifest the same author wrote)"]


def _legacy_disposition_author_problems(ledger: Ledger, events: list[dict],
                                        lineage: str) -> list[str]:
    """Every imported `disposition` row through the ONE shared author
    check every other disposition ingress already uses (round 6 F1).

    `import-legacy` is a fourth door a disposition can reach the ledger by,
    beside `respond --out`, `respond` without `--out`, and standalone
    `ledger add` — and it read `check_disposition_author` never: the
    legacy schema neither required nor permitted an `author` member, and
    the door appended whatever the batch supplied, unchanged. A round
    whose recorded request named an author could still receive an
    imported disposition with none.

    `check_disposition_author` already derives or refuses correctly for a
    round's PERSISTED request; `events` is passed through as its
    `extra_events` so a request row imported in this very batch — and, by
    the time a later batch runs this same pass, a request row an earlier
    sequential batch already committed and `ledger.current()` now carries
    — both bind exactly the same way a long-persisted one does.

    Mutates each disposition row's `author` in place to whatever the check
    returns, the same working-copy mutation `respond` and `ledger add`
    already make to their own JSON before it is emitted or appended. Safe
    here too: the whole batch is appended only when `legacy_import_problems`
    returns no problems at all, from any pass, so a mutation made ahead of
    that gate is discarded along with the rest of `events` on any refusal.
    """
    problems: list[str] = []
    for n, e in enumerate(events, 1):
        if e.get("event") != "disposition":
            continue
        try:
            resolved = check_disposition_author(
                ledger, e.get("round"), e.get("author"), lineage,
                extra_events=events)
        except Refusal as exc:
            problems.append(f"line {n} (disposition): {exc}")
            continue
        if resolved is not None:
            e["author"] = resolved
    return problems


def _unbound_prose(e: dict, pair, fp: str, state: tuple) -> str:
    """Why one answer binds to no ruling a reader can trust, in the words
    the person repairing the derivation needs."""
    name, detail = state
    if name == "malformed":
        why = (f"answers_round {detail!r} is not a canonical positive "
               f"integer")
    elif name == "future":
        why = (f"answers_round {detail} does not precede this event's own "
               f"round {e.get('round')!r} — nothing can answer a ruling "
               f"that did not exist yet, or its own round's")
    elif name == "ambiguous":
        why = (f"carries no answers_round and this identity is ALSO ruled "
               f"at its own round {detail} — which ruling it answers cannot "
               f"be inferred (round-7 F1's collision)")
    else:
        why = (f"targets round {detail!r}, which is no ruling of this "
               f"identity — an imported answer must substantiate the "
               f"finding relation it claims, never assert one")
    return f"{fp}: {pair[0]} ({pair[1] or 'waiver'}) {why}"


def legacy_import_problems(ledger: Ledger, events: list[dict],
                           authority: SourceAuthority,
                           lineage: str) -> list[str]:
    """Every violation `events` carries against the closed schema, the
    source authority and the lifecycle rules `import-legacy` must enforce
    before appending anything (rounds 7 F2, 8 F1-F2, 9 F1-F2).

    Six passes, each a precondition of the next, over the WHOLE batch:

    1. CLOSED SHAPE (`vocab.LEGACY_EVENT_SCHEMA`) — an admitted kind, its
       required members, every member's type including nested payload
       values, and refusal by name of anything else. Round-9 F2: the door
       admitted any object with a truthy `event`, so a string payload
       reached an uncaught AttributeError and `round: []` imported.
    2. PROVENANCE (`SourceAuthority`) — every row cites a tracked path at a
       git anchor this tool verified is contained in a remote-tracking ref;
       the tool reads those bytes and recomputes the digest the row claims;
       and the row's own claim TEXT must occur inside them. Round 9 asked a
       manifest the caller wrote whether the caller's own file hashed as the
       caller said, which round 10 walked through in one step. A ruling's
       fingerprint is recomputed from its declared identity facts, an
       envelope row's byte count and target commit are checked against the
       source and against shared history, and envelope rounds must form one
       contiguous loop.
    3. DISPOSITION AUTHOR (`_legacy_disposition_author_problems`) — every
       imported disposition through the same `check_disposition_author`
       every other disposition ingress shares: a round whose recorded
       request — persisted, batch-local, or an earlier sequential batch
       already committed — names an author derives or refuses a matching
       row before anything is appended; only a round whose request truly
       has no author leaves the imported disposition authorless (round
       6 F1).
    4. IDENTITY (`fingerprint.resolve_identity`) — one canonical graph over
       the ledger's persisted lineage AND this batch's alias rows, failing
       closed on a cycle or a conflicting merge exactly as the reader does
       (round-8 F1).
    5. LINEAGE CONTAINMENT (`_lineage_containment_problems`) — an alias or
       split whose endpoint is a known ruling must cite a source carrying
       that ruling's text, since a fingerprint occurs in no prose and
       anchoring alone would let any real file substantiate any merge.
    6. LIFECYCLE (`_lifecycle_problems`) — every settling or overruling
       answer, persisted and candidate, bound under the graph and the exact
       ruling MATERIAL this batch would leave behind (rounds 9 F1, 10 F1).

    The domain of pass 5 is `vocab.SETTLING_ANSWERS`, derived from the
    effect table rather than hand-picked: every OTHER admitted term maps to
    `ANSWER_OPEN` and can forge no lifecycle state, which is why an
    `ANSWER_OPEN` closure is checked for shape and provenance and for
    nothing further.

    `lineage` is the DESTINATION — the id `cmd_import_legacy` appends these
    rows with — and since 0.20.1 (round-2 F4) passes 3, 5 and 6 are judged
    against that lineage's own state rather than the whole file. Rounds and
    rulings are per lineage; reading them across the file let a concurrent
    review supply the ruling an imported withdrawal claimed to settle and
    the round-2 request an imported round-3 verdict needed, and the rows
    landed in a lineage whose readers find neither. Identity aliases stay
    global, and their effect is checked against EACH lineage's own answers.

    Returns every violation found; empty means the whole batch is clean.
    The caller must append NONE of `events` when this is non-empty — this
    is an atomic, before-the-fact property of the whole batch, never a
    per-row best effort.
    """
    problems = legacy_shape_problems(events)
    if problems:
        return problems
    for n, e in enumerate(events, 1):
        problems.extend(_row_problems(e, n, authority))
    problems.extend(_envelope_round_problems(ledger, events, lineage))
    if problems:
        return problems
    problems = _legacy_disposition_author_problems(ledger, events, lineage)
    if problems:
        return problems
    try:
        problems = _lineage_containment_problems(ledger, events, authority)
        return problems or _lifecycle_problems(ledger, events, lineage)
    except LineageError as exc:
        return [f"batch-local lineage: {exc}"]


def answered_verdict(cfg: Config, parsed: wire.Disposition,
                     ledger: Ledger | None = None, lineage: str = ""):
    """The verdict a standalone disposition answers, or None (round 2 F2).

    None means "not retrievable", which the caller must treat as a refusal
    rather than as permission to record unchecked identities.

    Round 3 F2: round 2 moved identity authority out of the disposition and
    into this retained file, and stopped there. The exchange copy is kept
    BEST EFFORT — `keep_bytes` degrades to a reason string when it cannot
    write, and nothing guarded the bytes afterwards — so accepting any
    well-formed verdict that carried the claimed SHA handed the forged-
    identity capability straight back: replace the kept copy with a different
    valid verdict at the same SHA, and the attacker chooses which fingerprint
    reaches closures, breakers and the merge decision. A probe did exactly
    that and `ledger add` exited 0.

    The append-only record is the authority; the file beside it is a cache of
    bytes the record already digested. So resolution runs through the ledger:
    the verdict event for this disposition's round supplies the SHA and the
    digest, and the retained bytes must reproduce that digest before they are
    parsed as anything. Missing, ambiguous and mismatched are three distinct
    refusals and none of them is a warning.
    """
    round_no = int(parsed.data.get("round", 0) or 0)
    claimed = parsed.attrs.get("verdict_sha") or ""
    if ledger is None:
        return None            # no record to resolve against: not retrievable
    recorded = recorded_verdict(ledger, lineage, round_no=round_no,
                                sha=claimed or None)
    if recorded is None:
        return None
    _, text = read_kept(cfg, round_no, "verdict",
                        lineage=ledger.carrier_lineage_for_sha(
                            recorded.get("sha"), prefer=lineage) or lineage,
                        digest=recorded["source_digest"])
    if text is None:
        return None            # missing, or not the recorded verdict
    verdict = wire.parse_verdict(text)
    if not verdict.wrapped or verdict.sha != recorded.get("sha"):
        return None
    if claimed and verdict.sha != claimed:
        return None
    return verdict


def recorded_verdict(ledger: Ledger, lineage: str,
                     round_no: int | None = None,
                     sha: str | None = None,
                     digest: str | None = None) -> dict | None:
    """The ONE recorded verdict of `lineage` that a response may answer, or
    None.

    Sweep F2: the resolution boundary, shared by the two doors a response
    enters by. `ledger add` resolved a standalone disposition through
    `answered_verdict` above; `respond --out` resolved nothing — it built,
    validated, wrote and recorded a disposition against whatever file it
    was handed, so an unwrapped legacy verdict that fails validation and
    was never recorded produced disposition events, fingerprints and
    falsification runs at a caller-supplied SHA. One rule now, in one place:

      - the event exists in the CURRENT lineage (a closed lineage's verdict
        is answered, not answerable);
      - it is unique — two different verdicts filed at one round, or two
        rounds carrying the same bytes, are ambiguity, and ambiguity is a
        refusal rather than a pick;
      - every key the caller supplies (round, sha, digest) agrees with it;
      - it is the JUST-CLOSED round: the lineage's most recent verdict. A
        response answers the ruling that is awaiting one; a disposition for
        an earlier round would arrive after the round it answers has already
        been superseded, and the record would then carry an answer no
        request ever rendered.
    """
    if ledger.is_closed(lineage):
        # A CLOSED lineage has no answerable ruling. Before the lineage was
        # keyed this fell out of `current()` being "everything after the
        # last closure marker", which was empty the moment a lineage
        # closed; keyed, the window still holds the closed review's
        # verdicts, so the rule has to be stated rather than inherited. A
        # response is the author's half of a round that is still running.
        return None
    events = [e for e in ledger.current(lineage)
              if e.get("event") == "verdict" and "round" in e]
    if not events:
        return None
    latest = max(e["round"] for e in events)
    # Round-2 F1 (Blocker): uniqueness is judged over the WHOLE answerable
    # round before any caller-supplied key is applied. The first version
    # filtered by the caller's digest first and checked uniqueness inside
    # that subset — so with two different verdicts filed at one round, the
    # author chose which one to answer by choosing the file. The caller's
    # keys are agreement checks against the one recorded verdict, never a
    # selector among several.
    in_round = [e for e in events if e.get("round") == latest]
    digests = {e.get("source_digest") for e in in_round}
    if len(digests) != 1 or None in digests:
        return None            # two rulings for one round: ambiguity, refuse
    recorded = in_round[-1]
    if round_no is not None and recorded.get("round") != round_no:
        return None
    if sha is not None and recorded.get("sha") != sha:
        return None
    if digest is not None and recorded.get("source_digest") != digest:
        return None
    return recorded


def request_author(ledger: Ledger, round_no: int, lineage: str,
                   extra_events=()) -> str | None:
    """The author `lineage`'s request event for `round_no`
    stamped, or None when no such event is recorded — an older ledger, a
    round opened before the field existed, or (defensively) no request
    event at all for that round.

    Round 3 F2: a disposition answers the round a REQUEST opened, and that
    request already named its author (`request_event`, above) before any
    reviewer verdict or author response existed to contest it. That is the
    one fact a disposition may not re-assert on its own behalf — `respond`
    derives the expected author from here rather than from the JSON it was
    handed, the same way it already derives round and verdict_sha from the
    recorded verdict rather than trusting a caller-supplied copy.

    Several request events can share one round (a re-emission after a
    correction); the most recently recorded one is authoritative, mirroring
    `recorded_verdict`'s own "newest decides" rule.

    `extra_events` folds in rows not yet part of the ledger (round 6 F1):
    `import-legacy` validates a whole batch before anything is appended, so
    a request and the disposition answering it can both be candidate rows
    of the SAME unappended batch. Appended last, same as any other match.
    """
    matches = [e for e in list(ledger.current(lineage)) + list(extra_events)
              if e.get("event") == "request" and e.get("round") == round_no]
    if not matches:
        return None
    author = matches[-1].get("author")
    return str(author) if author else None


def check_disposition_author(ledger: Ledger, round_no: int, author,
                             lineage: str, extra_events=()) -> str | None:
    """The ONE disposition-ingress author check, shared by every door a
    disposition can enter the ledger by (round 4 F1, round 5 F1).

    Round 3 F2 bound a disposition's author to the request that opened its
    round — but the check lived inline in `respond --out` alone. Round 4 F1
    found the other two doors the same disposition can reach: `respond`
    without `--out`, which still emitted an envelope carrying a mismatched
    author, and standalone `ledger add`, which recorded one — both read
    `request_author` never, and so both persisted whatever actor the wire
    supplied. One function now, called before anything is emitted or
    appended, at all three: `respond --out`, `respond` without `--out`, and
    `ledger add`.

    Round 5 F1: an OMITTED author used to pass through unchanged, the same
    as the genuine no-recorded-request state — so a disposition naming no
    author at all still appended `author: null` even when the round's
    request named one, at the one door (`ledger add`) that never derives it
    itself. This function now derives that case too, so every door can
    just use what it returns rather than re-deriving.

    `author` is the value the caller already has in hand — the disposition
    JSON's `author` member, an already-parsed envelope's `attrs["author"]`,
    or (`ledger add`'s ingested envelope carries both independently)
    `disposition_supplied_author`'s reconciliation of the two.

    `extra_events` (round 6 F1) is forwarded to `request_author` unchanged
    — `import-legacy`'s own batch, so a request row imported alongside the
    disposition it opens still binds it, before either is appended.

    Returns the author the caller should record. No recorded request for
    `round_no` (an older ledger, or a round that predates the field) is
    silence with nothing to derive from — the declared legacy state — and
    `author` passes through unchanged, `None` included. A recorded request
    with an omitted `author` derives to the request's author. A recorded
    request with a matching `author` returns it unchanged. A mismatch
    raises `Refusal`; nothing is derived past that point.
    """
    expected = request_author(ledger, round_no, lineage, extra_events)
    if expected is None:
        return author
    if author is None:
        return expected
    if str(author) != str(expected):
        raise Refusal(
            f"supplied author {author!r} does not match round {round_no}'s "
            f"recorded request author {expected!r} — a response is "
            f"attributed to whoever the request that opened the round "
            f"named as its author, never to a value the disposition "
            f"supplies beside it (round 3 F2, round 4 F1)",
            "",
            remedy="remove the author from the disposition and re-run the "
                   "command that produced it; the tool derives the author "
                   "from the recorded request and will not edit the "
                   "author's own input file")
    return author


def disposition_supplied_author(parsed) -> str | None:
    """The author a disposition envelope supplies, reconciled across its two
    independent stamps (round 5 F1).

    `emit_disposition` writes one `author` value to both the wrapper tag's
    `author` attribute and the JSON body's `author` member — but an
    envelope arriving at standalone `ledger add` is a file on disk, and
    nothing stops the two from being edited apart. Absent from both is
    silence, exactly like a disposition `respond` composes fresh, whose
    body member is the only stamp that exists before `emit_disposition`
    writes the wrapper. Present in only one names that one. Present in
    both, disagreeing, refuses outright, before `check_disposition_author`
    ever runs — an envelope names one author, not two, and there is no
    single value yet to compare against the recorded request.
    """
    wrapper = parsed.attrs.get("author")
    body = parsed.data.get("author")
    if wrapper is not None and body is not None and str(wrapper) != str(body):
        raise Refusal(
            f"the disposition's wrapper tag stamps author {wrapper!r} but "
            f"its body states {body!r} — an envelope names one author, not "
            f"two",
            "",
            remedy="a person corrects the envelope so the wrapper tag and "
                   "the JSON body name the same author, or removes one of "
                   "the two; the tool will not choose between them")
    return wrapper if wrapper is not None else body


def verdict_conflict(ledger: Ledger, round_no: int, digest: str,
                     lineage: str) -> dict | None:
    """The recorded verdict that a NEW verdict for `round_no` would
    contradict, or None (round-2 F1). A round is ruled once: the same bytes
    filed again are the idempotent no-op the ledger already provides; a
    different verdict at an already-ruled round is a second ruling, which no
    door may append — `ledger add --round N` used to, and `respond` then
    answered whichever the author pointed at."""
    for e in ledger.current(lineage):
        if e.get("event") == "verdict" and e.get("round") == round_no \
                and e.get("source_digest") not in (None, digest):
            return e
    return None


def _derived_identities(parsed: wire.Disposition,
                        against: wire.Verdict) -> dict:
    """`{finding_id: (fingerprint, severity)}` computed from the verdict.

    Round 2 F2. Round 1 fixed the forged-identity hole in `respond` and left
    `ledger add` — the other door into the same ledger — accepting whatever it
    was handed. The remedy has to live where BOTH paths converge, which is
    here: no caller can record a disposition without the verdict it answers,
    and a supplied value may only agree with what that verdict computes.
    """
    by_id = {f.id: f for f in against.findings}
    out = {}
    for rec in parsed.data.get("dispositions", []):
        fid = rec.get("finding_id")
        finding = by_id.get(fid)
        if finding is None:
            raise Refusal(
                f"disposition names finding {fid!r}, which the answered "
                f"verdict does not declare — identity cannot be derived and "
                f"must not be taken from the disposition (round 2 F2); "
                f"a person corrects the disposition's finding ids to the "
                f"verdict's, or answers the verdict this one is for",
                "")
        computed = (finding.fingerprint(), finding.severity)
        for key, value in (("fingerprint", computed[0]),
                           ("severity", computed[1])):
            supplied = rec.get(key)
            if supplied is not None and str(supplied) != str(value):
                raise Refusal(
                    f"{fid}: supplied {key} {supplied!r} does not match the "
                    f"verdict's {value!r}; identity is computed from the "
                    f"finding being answered (round 2 F2); a person "
                    f"removes {key!r} from the disposition and re-runs the "
                    f"command that produced it",
                    "")
        out[fid] = computed
    return out


def disposition_events(parsed: wire.Disposition,
                       against: wire.Verdict, cfg: Config | None = None) -> list[dict]:
    """Disposition events exactly as `ledger add` records them.

    `against` is REQUIRED: a disposition binds by fingerprint, and a
    fingerprint that was not computed from the finding it claims to answer
    binds to whatever it names. Round 2 F2 made this the single authority for
    both recording paths rather than a check one of them happened to run.

    Round 1 F6: a refutation's evidence is also emitted as a content-addressed
    `evidence` event bound to the finding's fingerprint. The repetition
    breaker asks whether a returning finding is backed by NEW evidence for
    THAT identity, and prose in a payload answers nothing it can compare.
    Digesting the text makes the comparison exact: repeating the same words
    yields the same digest and the breaker fires; saying something genuinely
    new yields a different one and the documented exemption applies.
    """
    round_no = int(parsed.data.get("round", 0))
    # Round-1 F3: which installation WROTE this answer, from the envelope's
    # own stamp — not from `tool_identity()`, which would record whoever
    # happened to ingest it. Absent for an envelope written before the
    # stamp existed, and absent is left absent rather than defaulted.
    wrote = (parsed.attrs or {}).get("tool")
    derived = _derived_identities(parsed, against)
    # Round 2 F1: one recorded answer is up to THREE events, and the answer
    # supersedes as a unit — so every event of one emission carries the same
    # `batch` stamp, the content address of the emission itself. The ledger's
    # batch projection binds companions to their disposition row by it;
    # deterministic, so replaying the same envelope re-derives the same stamp
    # and the uid dedup makes the replay a no-op.
    batch = sha256_text(json.dumps(
        {"attrs": dict(parsed.attrs), "data": parsed.data},
        sort_keys=True, ensure_ascii=False))[:16]
    events = [{
        "event": "disposition",
        "round": round_no,
        "finding_id": rec.get("finding_id"),
        "fp": derived[rec.get("finding_id")][0],
        "disposition": rec.get("disposition"),
        "subtype": rec.get("subtype"),
        "payload": rec.get("payload", {}),
        "verdict_sha": parsed.attrs.get("verdict_sha"),
        "head": parsed.attrs.get("head"),
        # Round 3 F2: the actor this answer is attributed to, carried
        # explicitly rather than left implicit in the kept exchange file —
        # the round-2 record predates this field and carries none, which is
        # exactly the gap the finding named. Round 4 F1: every caller of
        # this function now calls `check_disposition_author` first — both
        # `respond` (with and without `--out`) and the standalone `ledger
        # add` door verify the wire author against the recorded request
        # before this event is ever built, so what this function reads off
        # `parsed.attrs` here has already been checked, not merely carried.
        "author": parsed.attrs.get("author"),
        "batch": batch,
        **({"tool": wrote} if wrote else {})}
        for rec in parsed.data.get("dispositions", [])]
    for rec in parsed.data.get("dispositions", []):
        text = str(rec.get("payload", {}).get("evidence", "")).strip()
        if not text:
            continue
        events.append({"event": "evidence", "round": round_no,
                       "fp": derived[rec.get("finding_id")][0],
                       "digest": sha256_text(text),
                       "source": "disposition",
                       "of": rec.get("finding_id"),
                       "batch": batch})
    # The falsification record of an `accepted` disposition (§5.3a) becomes
    # the `falsification_run` event the `stale` and `unverifiable` breakers
    # read. Until this was written, those breakers had no writer outside
    # their own tests: the design carried the mechanism for a test that
    # proves nothing, and no product path ever reached it. The event binds
    # to the TEST that was run — the finding's own, or the amended one under
    # accepted(test_amended) — by digest, so "the named test flipped" is
    # checkable against the verdict rather than taken from prose.
    by_id = {f.id: f for f in against.findings}
    for rec in parsed.data.get("dispositions", []):
        if rec.get("disposition") != "accepted":
            continue
        record = rec.get("payload", {}).get("falsification")
        if not isinstance(record, dict) or not record.get("status"):
            continue
        fid = rec.get("finding_id")
        payload = rec.get("payload", {})
        test = (str(payload.get("amended_test", ""))
                if rec.get("subtype") == "test_amended"
                else by_id[fid].falsification)
        event = {"event": "falsification_run", "round": round_no,
                 "fp": derived[fid][0], "of": fid,
                 "status": record.get("status"),
                 "mutation": record.get("mutation"),
                 "test_digest": sha256_text(test.strip()),
                 "source": "disposition",
                 "batch": batch}
        if cfg is not None:
            # Sweep F12: the run is bound to the finding's EFFECTIVE blocking
            # state when it is recorded — the same rule the validator applies
            # (blocking severity AND a named test) — so the `unverifiable`
            # breaker fires on a blocking finding's unexecutable test and on
            # nothing else, without re-deriving severity at read time.
            # 0.26.0: `cfg` is the TARGET's configuration (the commit the
            # verdict rules on) at both recording doors — `respond --out`
            # through `record_response`, and `ledger add` — so the stamp
            # agrees with the précis and `close` by construction.
            event["blocking"] = validate.effective_blocking(
                by_id[fid].severity, test, cfg)
        if str(record.get("note", "")).strip():
            event["note"] = str(record["note"]).strip()
        events.append(event)
    return events


def record_response(cfg: Config, ledger: Ledger, envelope: str,
                    against: wire.Verdict, lineage: str, git=None,
                    governing: Config | None = None) -> dict:
    """`respond --out`: keep the disposition bytes and record the events, so
    the author's half of the round is in the ledger without a manual add.
    Only when a file was written: an envelope printed to stdout has no bytes
    of record to bind to, and a re-run of the same JSON is idempotent.

    `governing` is the configuration the run events' `blocking` stamp is
    judged by: the TARGET's, the commit `against` rules on (0.26.0, brief
    `unverifiable-breaker-target-authority`). It used to be `cfg`, the
    checkout's, while the précis and `close` judged the same finding by the
    target's. A caller that already resolved it hands it over, so one verb
    asks once; one that did not gets it resolved here, and an unresolvable
    target refuses rather than stamping by the checkout."""
    if governing is None:
        governing = governing_for(cfg, against.sha or "", git=git)
    parsed = wire.parse_disposition(envelope)
    round_no = int(parsed.data.get("round", 0))
    # The lineage the round the disposition ANSWERS belongs to, and the
    # lineage its envelope is stored and pushed under. The two are the same
    # id for everything this tool writes; they differ only for a round
    # taken over a `git:` reference before the lineage was keyed
    # (`Ledger.carrier_lineage_for_sha`).
    carrier_lineage = ledger.carrier_lineage_for_sha(
        against.sha, prefer=lineage) or lineage
    kept = keep_bytes(cfg, round_no, "disposition", envelope,
                      lineage=carrier_lineage)
    added = ledger.add_all(disposition_events(parsed, against, governing),
                           lineage=lineage)
    # The disposition leg of a `git` round. The topology is the round's own,
    # read from THIS end's record of it (`recorded_transport`) rather than
    # from the disposition document, for the reason the verdict leg states:
    # an envelope's prose is written by an agent, and a field it
    # mis-transcribes would decide where the other side is told to look.
    carrier = None
    if recorded_transport(ledger, against.sha, lineage) == vocab.TRANSPORT_GIT:
        carrier = push_envelope(cfg, carrier_lineage, round_no,
                                "disposition", envelope, git=git)
    return {"kept": kept, "events_added": added, "round": round_no,
            "carrier": carrier}


# ------------------------------------------------------------------- handoff

BREAKER_OVERRIDE = "breaker_override"


def authorize_breaker(cfg: Config, ledger: Ledger, breaker: str,
                      reason: str, authorized_by: str, lineage: str) -> dict:
    """Record the human's decision to continue past a fired breaker.

    Sweep F8. A firing stops the loop (design §5.3d: "any firing stops the
    loop and escalates to me, naming the rule and the decision required"),
    so there has to be a recorded way to take the decision, or "stop" means
    "dead until the lineage closes". This is the shape the design already
    trusts for stepping outside a rule — reason-bearing, actor-bearing,
    appended, lineage-scoped, like `cap_override`.

    Round-2 F2, F3: the decision names its reason and its taker — empty or
    whitespace values are refused before anything is appended, since a
    required flag proves only that a token was supplied — and it binds to
    the firings that are CURRENTLY fired and unauthorized for the named
    breaker, by identity (`covers`). Nothing to cover is a refusal too: an
    authorization for a breaker that is not firing would be a decision
    about a fact the human has not seen.
    """
    if breaker not in vocab.BREAKERS:
        raise Refusal(f"{breaker!r} is not a breaker; the closed set is "
                      f"{list(vocab.BREAKERS)}", "")
    if not (reason or "").strip():
        raise Refusal("a breaker override is a recorded decision and the "
                      "reason IS the record: an empty reason says a "
                      "decision happened and not what it was; nothing is "
                      "appended", "")
    if not (authorized_by or "").strip():
        raise Refusal("no authorizer was declared: continuing past a fired "
                      "breaker is the human's decision, and this tool cannot "
                      "observe who ran it — it can only carry what is "
                      "asserted; silence appends nothing", "")
    firing = [f for f in unauthorized_breakers(cfg, ledger, lineage)
              if f.get("breaker") == breaker]
    if not firing:
        raise Refusal(f"breaker {breaker!r} is not firing unauthorized in "
                      f"this lineage: there is no firing for a decision to "
                      f"cover, and an authorization recorded ahead of a "
                      f"firing would cover a fact the human has not seen "
                      f"(round-2 F3)",
                      paths.command(paths.Lit(TOOL_NAME), paths.Lit("ledger"),
                                    paths.Lit("report")))
    covers = sorted(firing_id(f) for f in firing)
    event = {"event": BREAKER_OVERRIDE, "breaker": breaker,
             "covers": covers, "reason": reason.strip(),
             "authorized_by": authorized_by.strip()}
    added = ledger.add(event, lineage=lineage)
    return {"recorded": bool(added), "breaker": breaker, "covers": covers,
            "authorized_by": authorized_by.strip()}


CORRECTION = "correction"


def correct_actor(cfg: Config, ledger: Ledger, event_uids: list,
                  true_actor: str, corrected_by: str, reason: str,
                  lineage: str) -> dict:
    """Record that one or more retained events misattribute their actor —
    without rewriting the append-only record they name.

    Round 3 F2's second half. `respond` now refuses a mismatched author
    going forward, but the ledger is append-only, and this repository's own
    round-2 disposition already recorded five events under the reviewer's
    identity when the request they answer named the author. There is no
    way to un-stamp them truthfully — only to say, in the same append-only
    record, what the truth actually was. This is the shape the design
    already trusts for exactly that kind of stepping-outside-the-record
    decision — reason-bearing, actor-bearing, appended — `authorize_breaker`
    and `waive` immediately above use the same three refusals for the same
    reason: an empty value proves only that a flag was supplied, and the
    tool cannot observe who is asserting a correction; it can only carry
    what a named human asserts.

    `event_uids` must each resolve to an event this ledger's CURRENT
    lineage actually holds — a correction binds to a recorded fact, never
    to an identifier the tool cannot verify — and the correction is
    additionally refused when the ledger already carries EVERY named uid
    stamped with the asserted true actor, since a correction that changes
    nothing records a decision that was never taken.
    """
    uids = sorted({str(u).strip() for u in (event_uids or []) if str(u).strip()})
    if not uids:
        raise Refusal("no event named: a correction with nothing to "
                      "correct records nothing", "")
    by_uid = {e.get("uid"): e for e in ledger.current(lineage)}
    missing = [u for u in uids if u not in by_uid]
    if missing:
        raise Refusal(
            f"event(s) not found in the current lineage's ledger: "
            f"{', '.join(missing)} — a correction binds to a recorded "
            f"event, never to an identifier the tool cannot verify", "")
    if not (true_actor or "").strip():
        raise Refusal("no true actor was named: a correction that does not "
                      "say who actually acted corrects nothing", "")
    if not (reason or "").strip():
        raise Refusal("a correction is a recorded decision and the reason "
                      "IS the record: an empty reason says a correction "
                      "happened and not what it was; nothing is appended",
                      "")
    if not (corrected_by or "").strip():
        raise Refusal("no corrector was declared: this tool cannot observe "
                      "who is asserting a correction — it can only carry "
                      "what is asserted; silence appends nothing", "")
    true_actor = true_actor.strip()
    if all(by_uid[u].get("author") == true_actor for u in uids):
        raise Refusal(
            f"every named event already carries author {true_actor!r}: a "
            f"correction that asserts what is already recorded changes "
            f"nothing and would itself misdescribe what happened", "")
    event = {"event": CORRECTION, "corrects": uids, "true_actor": true_actor,
             "reason": reason.strip(), "corrected_by": corrected_by.strip()}
    added = ledger.add(event, lineage=lineage)
    return {"recorded": bool(added), "corrects": uids,
            "true_actor": true_actor, "corrected_by": corrected_by.strip()}


def tool_agreement(parsed) -> dict:
    """Whether the envelope in hand was written under the same declared
    behavioural set this end carries.

    The comparison is between two identities of the DECLARED travelling
    set (`tool_identity`), never between "the installations" — a claim the
    digest cannot warrant, since anything outside the declared authority is
    outside what it measures (lineage 8).

    Three states, and the middle one is why this exists:

      `match`      the writer stamped an identity equal to mine.
      `differs`    it stamped a different one. The envelope is still sound —
                   the 2026-08-23 incident proved a stale reader validates a
                   good envelope perfectly well — but every line this end
                   RENDERS from it comes from different code, and that is
                   what reached the human wrong, twice.
      `unstamped`  no identity at all: an envelope from an installation
                   older than this mechanism. Reported as its own state
                   rather than folded into `match`, because historical
                   silence is not agreement (§5.2's rule for reading an
                   undeclared field).

    It reports; it does not refuse. A digest carries no ordering, so
    `differs` cannot distinguish a reader that is behind from one that is
    ahead — and refusing a sound envelope because the reviewer's tool is
    NEWER would block the better of the two. The tool refuses where it can
    be certain something is broken and reports where it can only be certain
    something is different; which of those a skew is, is the human's call,
    and this is what puts it in front of them.
    """
    mine = tool_identity()
    theirs = (parsed.attrs or {}).get("tool")
    # RVW-T18. The shape travels beside the behavioural identity and is
    # carried through to the reader UNCOMPARED. It is put in the returned
    # dict rather than left in `parsed.attrs` so that every renderer of this
    # report has it without reaching back into the envelope — and it is
    # never folded into `agreement`, because the states of `agreement` are
    # the states a human might act on and a differing shape is not one of
    # them. Two installs of identical code differ here as a matter of
    # course; that is the whole reason the set was split.
    shapes = {"reader_shape": shape_identity(),
              "writer_shape": (parsed.attrs or {}).get("shape")}
    if not theirs:
        return {"agreement": "unstamped", "reader": mine, "writer": None,
                **shapes,
                "note": "the envelope was written by an installation that "
                        "predates tool identity; nothing can be compared, "
                        "which is not the same as agreement"}
    if theirs == mine:
        return {"agreement": "match", "reader": mine, "writer": theirs,
                **shapes}
    return {"agreement": "differs", "reader": mine, "writer": theirs,
            **shapes,
            "note": "the envelope was written under a DIFFERENT declared "
                    "behavioural set of this tool. What it says is sound; "
                    "what this end renders from it — the relay, the "
                    "commands you are about to hand back — comes from "
                    "different code. Reconcile the two installations, or "
                    "proceed knowing which one produced what"}


def target_blocking(cfg: Config, git=None):
    """`sha -> the blocking list of the configuration AT that commit`, or
    None where it cannot be resolved here or declares no taxonomy.

    The one authority for "is this finding blocking?" (0.26.0, brief
    `unverifiable-breaker-target-authority`): the configuration of the
    commit the finding's verdict ruled on — `governing_for`, the resolution
    `close`, `validate --from-target` and the précis already use — never the
    checkout's. Resolved lazily and once per commit, because the breakers
    ask it only for a run that carries no `blocking` stamp."""
    cache: dict[str, list[str] | None] = {}

    def at(sha: str) -> list[str] | None:
        if sha not in cache:
            try:
                governing = governing_for(cfg, sha, git=git)
            except Refusal:
                cache[sha] = None
            else:
                cache[sha] = (list(governing.blocking_severities)
                              if governing.taxonomy_declared else None)
        return cache[sha]
    return at


def unauthorized_breakers(cfg: Config, ledger: Ledger,
                          lineage: str) -> list[dict]:
    """The fired breakers no recorded decision covers (§5.3d, sweep F8):
    a firing is covered only when an override lists its identity."""
    fired = ledger.breakers(lineage,
                            ledger.effective_round_cap(cfg.round_cap,
                                                       lineage),
                            token_budget=cfg.token_budget,
                            blocking_at=target_blocking(cfg))
    covered = {fid for o in ledger._by(BREAKER_OVERRIDE, lineage)
               for fid in o.get("covers", [])}
    return [f for f in fired if firing_id(f) not in covered]


def missing_dispositions(ledger: Ledger, lineage: str) -> dict | None:
    """Every ruling of `lineage` that no recorded answer answers.

    Sweep F4. `next_round` derived the next round from verdict events
    alone, so `handoff` opened round N+1 the moment round N had a verdict —
    with or without the author's answer — and rendered `(no round-N
    dispositions in the ledger)` into the next request as if that were a
    state rather than a defect. The contract is one disposition per finding
    and a finding never dies by omission. Returns None when nothing is
    owed: no verdict yet in this lineage (round 1), or the last verdict has
    every finding answered. A clean verdict closes the lineage, so a new
    lineage's round 1 is the clean-verdict control by construction.

    Lineage 6 round 2: "answered" means the finding HAS a standing answer —
    the newest disposition event for its fingerprint — not that exactly one
    event exists. The old count-based rule read the append-only ledger as
    write-once, so a disposition that legitimately re-bound (the head moved
    under it when a regenerated artifact was committed after the record)
    dead-ended the lineage with no legal recovery: the ledger may not be
    edited, and no decision verb covered the state. Recency supersedes,
    exactly as `recorded_transport` reads its records; every event stays in
    the file as audit history, and a duplicate WITHIN one envelope remains
    the validation defect it always was (D-DUPLICATE).

    Round-10 F3: this was the last lifecycle reader still filtering
    `_by("finding")` after round 9 unified the others onto `is_ruling`. So a
    round whose only unanswered ruling was a legacy ATOMIC IMPORT was
    reported as owing nothing, and `handoff_preflight` opened the next round
    over a finding that had died by omission — the exact state this function
    exists to make impossible. It reads `findings_in_round`, the one ruling
    authority, and translates display fields through `ruling_facts`, which
    is where a legacy row's `legacy_id`/`verbatim` become the `id`/`title` a
    refusal payload names.

    Lineage 20 round 11 F5, landed 2026-09-03. The round SELECTION was the
    last thing here still anchored to the verdict: the maximum verdict round
    was taken and rulings were looked for in that round alone, so a ruling
    made in any other round could not be owed. That is not a corner: an
    atomic ruling imported at a round with no verdict artifact of its own —
    exactly what `import-legacy` writes — was invisible to the one check
    whose job is that a finding never dies by omission, and so was any
    ruling of an earlier round the loop had walked past. The owed set is
    now derived from the ruling AUTHORITY: `latest_rulings` (the newest
    ruling of every identity in this lineage) minus every identity that
    `current_answers` shows an answer for. Both are the lifecycle's own
    round-binding, so an answer recorded against an earlier ruling of a
    re-raised identity still does not answer the later one.

    ANSWERED, here, means any recorded answer, not a disposition alone:
    the author's disposition, the reviewer's closure, or a named human's
    waiver. What this function guards is omission — a ruling nobody
    responded to — and a ruling the reviewer withdrew or a human overruled
    by name has been responded to. Reading only dispositions would refuse
    a handoff over findings the record shows were answered by the two
    parties entitled to answer them.
    """
    rulings = ledger.latest_rulings(lineage)
    if not rulings:
        return None
    answers = ledger.current_answers(lineage)
    unanswered = sorted(
        ((fp, r) for fp, r in rulings.items() if not answers.get(fp)),
        key=lambda item: (int(item[1].get("round") or 0),
                          str(item[1].get("id") or "")))
    if not unanswered:
        return None
    return {"round": max(int(r.get("round") or 0) for _, r in unanswered),
            "findings": len(rulings),
            "unanswered": [{"id": r.get("id"), "fp": fp,
                            "round": int(r.get("round") or 0),
                            "severity": r.get("severity")}
                           for fp, r in unanswered]}


def current_branch(cfg: Config, git=None) -> str:
    """The branch THIS worktree has checked out, or "" when it has none.

    `cfg.repo_root` is `--show-toplevel`, so it is the worktree's own root
    rather than the main checkout `repo_identity` canonicalises to — which
    is the whole point here: the ledger is shared across worktrees and the
    branch is what tells them apart. A detached HEAD and an unreadable
    repository both answer "", which every caller reads as unknown.
    """
    run = git or (lambda *a: _git(cfg.repo_root, *a,
                                  ceiling=git_ceiling(cfg)))
    try:
        return known_branch(run("rev-parse", "--abbrev-ref", "HEAD"))
    except GitTimeout:
        # A git that did not answer in time is not a detached HEAD: the
        # typed refusal travels, rather than an "unknown" that would let
        # the verb go on to choose a lineage by it (0.25.0).
        raise
    except RuntimeError:
        return ""


def _open_request_evidence(events: list[dict]) -> str:
    """The rounds an open-request refusal is naming, as a person reads them."""
    return "; ".join(
        f"{known_branch(e.get('branch')) or 'an unrecorded branch'} holds "
        f"round {e.get('round', '?')} for {str(e.get('sha') or '?')[:12]}"
        + (f", emitted {e['ts']}" if e.get("ts") else "")
        for e in events)


def _close_lineage_command() -> str:
    return paths.command(
        *paths.lits(TOOL_NAME, "close", "--lineage", "--reason"),
        paths.qph("..."), paths.Lit("--by"), paths.Ph("<who>"))


#: The reservation's file, beside the ledger it protects. One state
#: directory serves every worktree of one repository (`repo_identity`
#: canonicalises a linked worktree back to the main checkout), so a name
#: under it IS a shared resource every worktree contends for.
#:
#: Keyed since 2026-09-06 (brief `keyed-lineage`): the protected resource
#: is ONE lineage, and two worktrees running two different lineages have
#: nothing to exclude each other from. The one lineage that has no id yet
#: is reserved by BRANCH instead — see `opening_lock_basename`.
def lineage_lock_basename(lineage: str) -> str:
    return f"lineage-{lineage}.lock"


def opening_lock_basename(branch: str) -> str:
    """The lock that stands for the lineage this BRANCH has not opened yet.

    An id that does not exist cannot be locked by id, and the resource two
    openers contend for is not the repository — it is one branch's review.
    Two worktrees on two branches opening two lineages destroy nothing and
    must both be admitted, which is the whole of `keyed-lineage`; two
    handoffs on ONE branch, neither of which can see the other's request
    until it is recorded, would mint two ids for the review the operator
    meant to open once.

    So the name is derived from the branch: a readable slug for the human
    who reads a holder record, and a digest of the exact branch name so two
    different branches can never collapse onto one lock. A branch git will
    not name — a detached HEAD — gets the single unnamed lock, which
    over-excludes rather than under-excludes; the verbs refuse on that state
    anyway once more than one lineage is open.
    """
    mine = known_branch(branch)
    if not mine:
        return "lineage-new.lock"
    slug = re.sub(r"[^A-Za-z0-9._-]", "-", mine)[:40].strip("-") or "branch"
    digest = hashlib.sha256(mine.encode("utf-8")).hexdigest()[:8]
    return f"lineage-new-{slug}-{digest}.lock"


def _reservation_evidence(holder: dict) -> str:
    """The holder, as a person reads it — or the honest absence of one."""
    if not holder:
        return ("another process holds it and has not yet written its "
                "record")
    who = known_branch(holder.get("branch")) or "an unrecorded branch"
    pid = holder.get("pid")
    verb = holder.get("verb") or "a lifecycle command"
    since = f", since {holder['ts']}" if holder.get("ts") else ""
    return (f"{who} is running `{TOOL_NAME} {verb}`"
            + (f" as pid {pid}" if pid else "") + since)


class LineageReservation:
    """An exclusive claim on ONE lineage, held across the WHOLE admission —
    the lifecycle check, the commit, the push, the gate run, the emission
    and the record.

    Lineage 27 round 1 F1. A ledger READ is a single moment, and everything
    the handoff does after it destroys: `_emit` commits outstanding work and
    pushes the branch, the gates run for minutes, and `record_handoff`
    force-pushes the envelope to a `(lineage, round)` ref. Two handoffs of
    ONE lineage that merely OVERLAP therefore both pass any check —
    neither is refused, both record the same round, and under the `git`
    carrier the second overwrites the first's envelope. A later check cannot
    protect a write it races; only something held across the interval can.

    KEYED SINCE 2026-09-06 (brief `keyed-lineage`). The lock is
    `lineage-<id>.lock`, so what it excludes is two commands acting on the
    SAME lineage — which is the destruction: one ref, one round number, one
    terminal marker. Two worktrees running two DIFFERENT lineages share
    nothing to overwrite and are no longer refused, which is the whole point
    of keying and what retires the branch refusal that stood here before
    (brief `concurrent-round-refusal`, superseded). Opening a NEW lineage
    additionally takes `NewLineageReservation`, keyed on the BRANCH,
    because the id does not exist to lock until it has been minted.

    What is held is an OS-level exclusive lock (`fcntl.flock`, non-blocking)
    on a file in the shared state directory. The kernel owns it: a holder
    that dies releases it, so there is no stale-lock state to detect, no
    timeout to tune and no `--force` to invent — which is why this is a lock
    rather than a lock FILE. The holder's branch, pid, verb and timestamp
    are written into it after acquisition so a refused process can name who
    it is waiting on; the record is truncated on release, so a reader never
    sees a holder that has finished.

    Refuse, never wait. Waiting would make one worktree's gate run into the
    other's timeout, and the reservation's whole point is that a person
    decides — the tool says who holds it and that it clears when their
    handoff finishes or fails.

    WHO TAKES IT is derived from what it protects, not from a list of the
    commands that happened to be found racing (lineage 27 round 2 F1). The
    protected resource is one repository's lineage, and the writes that end
    it are the `lineage_closed` appends: `close_round`'s clean closure,
    `authorize_advance`'s authorization closure, and `close_lineage`'s
    decision — reached by `close --verdict`, `authorize-advance` and
    `close --lineage`, which is the whole of the CLI surface that appends
    one. (`ledger add` records a verdict's events without the closure,
    `import-legacy`'s domain excludes the marker, and `waive` records an
    answer to a finding; none reaches a terminal write.) Round 1 reserved
    the first and third and left the middle one outside — an ordinary
    advance of a ruled and waived round, overlapping the author's next
    handoff, closing over a request the ledger did not yet show. All three
    hold it now, each from before its own first lifecycle read through its
    final record.

    A SEQUENTIAL re-emission into the same lineage — amend-and-re-emit —
    takes the reservation, finds it free, and is unchanged.
    """

    #: The name a reservation over a not-yet-minted lineage uses. It cannot
    #: collide with a real lineage's lock, because a minted id is letter-led
    #: and a legacy one is digits, and neither is empty.
    UNASSIGNED = "unassigned"

    def __init__(self, ledger: Ledger, branch: str = "",
                 verb: str = "handoff", lineage: str | None = None):
        self.branch = branch
        self.verb = verb
        self.lineage = str(lineage) if lineage else self.UNASSIGNED
        # `Ledger.path` is None only for the in-memory backend, which exists
        # so the suite needs no writable directory (`Ledger`). There is then
        # no shared state directory, so there is nothing to reserve and
        # nothing that could be racing: a reservation over a per-process
        # list is a lock over nothing. No production path reaches it —
        # `config.load` always resolves `ledger_dir` (the flag, the
        # environment, or ~/.local/state/<tool>/<repo id>), and both call
        # sites build their ledger from it.
        self.path = (ledger.path.parent
                     / lineage_lock_basename(self.lineage)
                     if ledger.path is not None else None)
        self._fh = None

    def holder(self) -> dict:
        """Whoever's record the reservation file carries, `{}` for none.

        Read WITHOUT the lock, by a process that has just been refused, so
        every degenerate state is ordinary rather than exceptional: the file
        may be empty (the holder acquired and has not written yet), missing,
        or unreadable. None of those is evidence of anything but absence.
        """
        try:
            text = self.path.read_text(encoding="utf-8") if self.path else ""
        except OSError:
            return {}
        try:
            record = json.loads(text) if text.strip() else {}
        except ValueError:
            return {}
        return record if isinstance(record, dict) else {}

    def acquire(self) -> "LineageReservation":
        if self.path is None:
            return self
        if fcntl is None:
            raise Refusal(
                f"this platform has no `fcntl`, so the lineage reservation "
                f"that keeps two worktrees of one repository from opening "
                f"and recording a round at the same time cannot be taken — "
                f"and one state directory serves every worktree, so without "
                f"it an overlapping {self.verb} would overwrite what another "
                f"one recorded",
                "",
                remedy=f"a person runs this on a POSIX host, or runs one "
                       f"worktree at a time by hand and accepts that nothing "
                       f"enforces it. The tool does not grant admission it "
                       f"cannot make exclusive")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            holder = self.holder()
            fh.close()
            mine = known_branch(self.branch) or "an unrecorded branch"
            what = ("a new lineage on this branch"
                    if self.lineage == self.UNASSIGNED
                    else f"lineage {self.lineage}")
            raise Refusal(
                f"another worktree of this repository holds the lineage "
                f"reservation for {what} — {_reservation_evidence(holder)} "
                f"— and this worktree is on {mine}. One state directory "
                f"serves them all, so an overlapping {self.verb} would "
                f"compute the same round of that same lineage and then "
                f"commit, push and record over it: a ledger read cannot see "
                f"a round that is being opened right now, only one already "
                f"recorded",
                "",
                remedy=f"nobody has to decide anything here — the "
                       f"reservation is released the moment the holder's "
                       f"command finishes, whether it succeeds or fails, and "
                       f"the OS releases it if that process dies. Wait for "
                       f"it and run this again")
        self._fh = fh
        # Written AFTER the lock is held, so only the holder ever writes it
        # and a reader is never told about a process that lost the race.
        record = {"branch": known_branch(self.branch), "pid": os.getpid(),
                  "verb": self.verb, "lineage": self.lineage,
                  "ts": datetime.now(timezone.utc).isoformat(
                      timespec="seconds")}
        try:
            fh.truncate(0)
            fh.write(json.dumps(record, sort_keys=True) + "\n")
            fh.flush()
        except OSError:
            # The reservation is the LOCK; the record beside it is only
            # evidence for whoever is refused. A directory that will not
            # take the bytes must not cost the exclusion they describe.
            pass
        return self

    def release(self) -> None:
        """Release on EVERY path — a refused emission, a red gate, an
        exception — which is why all three call sites hold it in
        `finally`."""
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            fh.truncate(0)
            fh.flush()
        except OSError:
            pass
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()

    def __enter__(self) -> "LineageReservation":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()


class NewLineageReservation(LineageReservation):
    """The claim on the lineage this BRANCH has not opened yet (brief
    `keyed-lineage`, item 4).

    A per-lineage lock cannot protect an id that does not exist. Two
    handoffs opening a lineage at the same moment on ONE branch would each
    read "no open lineage on my branch", each mint an id, and each record a
    round 1 — two reviews where the operator meant one, and neither able to
    see the other, because the branch resolution reads REQUEST events and
    neither request is written yet. So the assignment AND the admission that
    follows it are one critical section, keyed by branch.

    Deliberately NOT repository-wide. A lock over the whole repository would
    be held across the gates — minutes — and would refuse the second
    worktree's handoff for the entire interval, which is the refusal this
    brief exists to retire. What two openers on two branches share is
    nothing: two ids, two refs, two round numbers, two markers.

    The same lock is what `close` takes when it can name no lineage of its
    own (`cmd_close`): a close arriving while this branch's review is being
    opened must be refused by the reservation rather than told the branch
    has no review, because the review is one `record_handoff` away from
    existing.

    Everything else is `LineageReservation`: the same OS lock, the same
    refusal shape, the same released-on-every-path discipline. It is a
    subclass rather than a flag so the two names say which resource is held
    where a reader meets them.
    """

    def __init__(self, ledger: Ledger, branch: str = "",
                 verb: str = "handoff"):
        super().__init__(ledger, branch=branch, verb=verb)
        self.lineage = self.UNASSIGNED
        self.path = (ledger.path.parent / opening_lock_basename(branch)
                     if ledger.path is not None else None)


def handoff_preflight(cfg: Config, ledger: Ledger, lineage: str,
                      branch: str | None = None, git=None) -> None:
    """Refuse a handoff the lifecycle does not permit — BEFORE the cache is
    consulted, a commit made, a push attempted, a gate run or a request
    emitted (sweep F4, F8). Raises Refusal with no runnable `next`: both
    refusals name a decision or an authored answer, which a person supplies
    and no command repairs.

    It reads ledger state, so it runs AFTER the caller has captured and
    judged the author's claim (lineage-3 round 6 F1): a claim whose grammar
    has not been accepted must not cause the ledger to be read at all. The
    ordering is one chain — claim grammar, then lifecycle, then cache, push,
    gates, emission, ledger mutation.

    `lineage` is the lineage this handoff continues (or the id it has just
    minted for a new one): every read below is scoped to it, so a second
    worktree's open review is not this one's business and cannot refuse it.
    Until 2026-09-06 the first thing here was a refusal of exactly that
    (brief `concurrent-round-refusal`) — the stopgap for one repository
    holding one review, retired by keying rather than relaxed.

    `branch` is this worktree's, derived here when the caller names none —
    and passed in by `cmd_handoff`, so the reservation it took and the
    refusals below name one worktree.

    It is a READ, and that is its limit (lineage 27 round 1 F1): it can see
    a round already recorded and not one being opened beside it, so two
    handoffs of one lineage that overlap both pass it. `LineageReservation`
    is what covers the interval, and `cmd_handoff` holds it around this
    call and everything after it."""
    # RVW-T17. The authority question used to be asked HERE, before the
    # commit existed, which made it a prediction about what `commit -a`
    # would record. Rounds 7 to 11 of lineage 11 are five different ways a
    # prediction can be wrong — the index against the worktree, a
    # pathname's shape against its bytes, a filter's declaration against
    # its behaviour, git's rendered attribute value against the attribute
    # name, and a commit hook restaging after the check — and the list has
    # no principled end, because git offers arbitrarily many ways to change
    # what a commit records between a check and the commit.
    #
    # It now lives in `emit.ensure_pushed`, immediately after the commit
    # and before the push and the emission, and it asks the artifact:
    # `git show <sha>:review.toml`, the same call `take` makes. Everything
    # predictive stopped mattering — whatever a filter, a hook, an
    # attribute or the index did, the commit records something and that
    # something is read. What remains here is the lifecycle, which is what
    # this function is for.
    owed = missing_dispositions(ledger, lineage)
    if owed is not None:
        ids = ", ".join(f"{u['id']} (round {u['round']}, {u['fp']})"
                        for u in owed["unanswered"])
        parts = [f"{len(owed['unanswered'])} of {owed['findings']} "
                 f"ruling(s) in this lineage have no disposition: {ids}"]
        # The placeholder is the tool's own word; the round it refers to
        # is stated in the prose around the command rather than rendered
        # into a slot no shell ever sees (round 5 F1).
        answer_cmd = paths.command(
            *paths.lits(TOOL_NAME, "respond", "--verdict"),
            paths.Ph("<the recorded verdict>"), paths.Lit("--from-json"),
            paths.Ph("<dispositions.json>"), paths.Lit("--out"),
            paths.Ph("<disposition.md>"))
        raise Refusal(
            "; ".join(parts) + " — one disposition per finding, and a "
            "finding never dies by omission (§5.2); the next round is not "
            f"opened until every finding is answered. Answer the verdict "
            f"each unanswered ruling was made in — the newest is round "
            f"{owed['round']} — with `{answer_cmd}`, then hand off",
            "")
    # The ROUND COUNT is a threshold, not an observed anomaly (user decision
    # 2026-08-25). Round-4 F2: the first cut of this exempted every `budget`
    # firing without looking at `limit`, which silently removed the
    # TOKEN-budget stop as well — a stop nobody asked to remove. The two are
    # different kinds of fact. A round count is a proxy: it says how long the
    # loop ran and nothing about whether it closed anything. A token breach
    # is MEASURED, against a ceiling the repository declared, and a lower
    # bound over that ceiling is a true positive. So only `limit == "rounds"`
    # advises; a token breach still refuses and still needs a recorded
    # decision. Every other breaker names something the record shows went
    # wrong — a finding that returned after a refutation, a run that cannot
    # execute, a companion that answers no emission — and stopping on those
    # is the point. `budget` names only how much the loop has spent, which
    # is a proxy: it fires on lineages doing exactly what they should and
    # says nothing about whether they are converging. It is still evaluated,
    # still reported, and still needs no authorization to be seen; it just
    # no longer refuses. `ledger convergence` answers the question the
    # threshold was standing in for.
    fired = [f for f in unauthorized_breakers(cfg, ledger, lineage)
             if not (f.get("breaker") == "budget"
                     and f.get("limit") == "rounds")]
    if fired:
        named = "; ".join(
            f"{f['breaker']} (round {f.get('round', '?')}"
            + (f", {f['fp']}" if f.get("fp") else "")
            + f") [{firing_id(f)}]: {f['rule']}"
            for f in fired)
        raise Refusal(
            f"a circuit breaker fired and no recorded decision covers it — "
            f"{named}. Any firing stops the loop and escalates to the "
            f"human, naming the rule and the decision required (§5.3d). "
            f"The decision is one of: close the lineage "
            f"(`{paths.command(*paths.lits(TOOL_NAME, 'close', '--lineage', '--reason'), paths.qph('...'), paths.Lit('--by'), paths.Ph('<who>'))}`), "
            f"or continue past this firing by recorded authorization "
            f"(`{paths.command(*paths.lits(TOOL_NAME, 'ledger', 'authorize-breaker', '--breaker'), fired[0]['breaker'], paths.Lit('--reason'), paths.qph('...'), paths.Lit('--by'), paths.Ph('<who>'))}`); the "
            f"tool takes neither on its own",
            "")


#: `cached_handoff`'s `base` when the caller states none — a value that
#: equals no base, so an unstated base is cold (the round 5 F1 rule).
_BASE_UNSTATED = object()


def cached_handoff(cfg: Config, ledger: Ledger, round_no: int, lineage: str,
                   git=None, claim_digest: str = "",
                   roles: tuple[str, str] | None = None,
                   transport: str | None = None,
                   author_flag: str | None = None,
                   reviewer_flag: str | None = None,
                   debug: bool = False,
                   base=_BASE_UNSTATED) -> dict | None:
    """§9bis.3 rule 5: re-running handoff on an unchanged tip with a warm
    request returns the same envelope without re-running gates or pushing.

    Warm means: the working tree is clean, HEAD equals the SHA the current
    lineage's request for this round already binds, the kept envelope bytes
    still digest to what the ledger recorded, AND the authored claim is the
    same claim. Any of those false — dirty tree, moved tip, missing or
    altered copy, edited claim — and the cache is cold, which is the honest
    state; the cache never serves a stale envelope.

    The claim clause is here because the docstring above promised something
    the code did not do. The tip is only PART of an envelope's input: the
    Claim is authored, arrives from a file, and is not derivable from the
    tree. Emitting round 3 of this tool's own review hit it — a corrected
    claim at an unchanged tip returned the previous envelope, reporting
    `cached: true` for bytes that no longer said what the author meant. An
    idempotence check keyed on a proper subset of its inputs is the same
    defect class this review has now found at three layers, and it is worse
    here than elsewhere: the stale artifact it serves is the one the
    reviewer rules on.

    Round 4 F1 is the third and last visit from this family, so the rule is
    now stated once with no exemptions: **warm requires recorded and current
    claim state to be EQUAL, in both directions.** Round 3 added the claim.
    Round 3 F3 fixed supplied-against-unrecorded. Round 4 F1 found the
    opposite transition still warm — emit with `--claim-file`, then emit
    without it, and the tool served the retained envelope carrying the
    previous author's Claim, though this invocation supplied none and a cold
    emission would have rendered an empty objective and a default risk.

    Each of those three was fixed in the direction it was shown, which is why
    there were three. Every time, the hole was an exemption for "nothing to
    compare" — and absence IS a state: `--claim-file` present and absent are
    two different authored inputs, exactly as two different files are.

    So presence is part of the key. `NO_CLAIM` is recorded when no claim file
    was supplied, so "no claim" can be proved rather than inferred from a
    missing field. An event predating the field records nothing, equals
    neither state, and is therefore cold — unprovable is cold, which is the
    same rule round 3 F3 arrived at, now applied without a special case for
    the direction that happened not to be under review.

    Round 5 F1 removed the last one. `claim_state` defaults to a value that
    matches nothing, so a caller who names no state cannot warm this cache —
    the fourth transition was two UNKNOWNS comparing equal, which is the one
    equality that can never be proved. The default exists rather than being
    a required argument because the reviewer's falsification calls this
    helper without it and requires a cold answer, not a TypeError: an API
    whose default is safe is stronger than one whose contract is documented.

    Public issue #7 (0.26.0) found the review BASE outside the key: a second
    `handoff --base Y` at an unchanged tip came back `cached: true` with
    `Base:` still X, the author's second base silently dropped. KEYED, not
    refused. A refusal would have no repair — at an unchanged tip the only
    way to a request with the corrected base would be a new commit — while a
    changed base is an authored input like a corrected claim, and a
    corrected claim at an unchanged tip already goes cold and re-emits the
    round. So `base` is the expression a cold emission from the same
    arguments would use (`cli._review_base`), resolved here exactly as
    `ensure_pushed` resolves it — `rev-parse --verify <base>^{commit}` —
    which reads the same state the cold path would, because the tree is
    clean and HEAD is the kept request's target, so the cold path would
    commit nothing before resolving. Warm needs that commit to equal the
    kept envelope's `Base:`. Unstated, None (no derivable base: the cold
    path refuses "pass --base"), unresolvable, or a kept copy with no
    `Base:` line: cold, and the cold path says what is wrong. A base that
    IS the kept id is compared without running git, since it names that
    commit by construction.
    """
    run = git or (lambda *a: _git(cfg.repo_root, *a,
                                  ceiling=git_ceiling(cfg)))
    try:
        head = run("rev-parse", "HEAD")
        dirty = run("status", "--porcelain")
    except RuntimeError:
        return None
    if dirty:
        return None
    request = next((e for e in reversed(ledger.current(lineage))
                    if e.get("event") == "request"
                    and e.get("round") == round_no
                    and e.get("sha") == head), None)
    if request is None:
        return None
    # Round 5 F1: the `or ""` that used to be here folded an unrecorded
    # legacy event and an omitted current argument onto ONE value, so the
    # two unknowns compared equal and a legacy envelope came back warm. The
    # method said absence is never evidence while its own default API turned
    # absence on both sides into a cache hit — the fourth transition, in the
    # round whose claim called an untabulated fourth transition a Blocker.
    #
    # No folding. `None` (never recorded) equals neither NO_CLAIM nor any
    # digest nor the unstated default, so unknown never matches anything,
    # including another unknown. Two states are equal here only when both
    # were recorded and both are the same. The asymmetry is unchanged: cold
    # costs one re-emission, warm costs a reviewer ruling on a claim nobody
    # wrote.
    if request.get("claim_digest") != claim_digest:
        return None
    # An altered copy — rewritten, CRLF, undecodable, or a legacy flat
    # copy of some other round — is cold, which is the honest state.
    path, text = read_kept(cfg, round_no, "request",
                           lineage=lineage,
                           digest=request.get("source_digest") or "")
    if text is None:
        return None
    # The effective role stamp is part of the envelope's input (§4): a warm
    # copy emitted under one (author, reviewer) must not answer an
    # invocation that selected another — the stale artifact would carry the
    # wrong stamp to the reviewer. Unlike the claim, roles are DERIVABLE
    # when unstated: `None` means no per-invocation selection, which
    # resolves to the config's default direction — a real state, not an
    # unknown — so legacy warm behaviour is unchanged. The recorded side is
    # read from the kept envelope itself, which has carried the stamp since
    # the wire format existed.
    # Round 2 F1 (lineage 12). This used to fall back to the CHECKOUT's
    # defaults, and its caller passed roles the checkout had authorised —
    # so a checkout whose permitted list no longer contained an identity
    # the TARGET permits could veto a warm serve, or key it on the wrong
    # pair. By this point the tree is clean and HEAD equals the SHA the
    # recorded request binds, so HEAD *is* the target: its authority is
    # resolvable here, and it is the only authority entitled to answer.
    #
    # A caller may still pass `roles` outright — the reviewer-side
    # falsifications do — and that is unchanged. What changed is where the
    # value comes from when it is not passed.
    if roles is not None:
        want_author, want_reviewer = roles
    else:
        from . import emit as _emit
        try:
            governing, origin = resolve_authority(cfg, head, git=git)
        except Refusal:
            return None
        if origin != AUTHORITY_TARGET:
            return None
        try:
            want_author, want_reviewer = _emit.resolve_roles(
                governing, author=author_flag, reviewer=reviewer_flag)
        except Exception:
            # A target that will not authorise these roles cannot serve a
            # warm envelope stamped with them. Cold is the honest answer;
            # the cold path raises the refusal with its remedy.
            return None
    kept_request = wire.parse_request(text)
    attrs = kept_request.attrs
    if (attrs.get("author", "").lower() != want_author.lower()
            or attrs.get("reviewer", "").lower() != want_reviewer.lower()):
        return None
    # RVW-T11, and the same rule as the role stamp one line above: the
    # declared transport is part of the envelope's input, so a copy emitted
    # under one topology must not answer an invocation that declared the
    # other. Warm here would be the sharp version of the defect — the human
    # asks for the cross-machine round, the tool hands back the envelope that
    # says `path`, and the reviewer's own relay then prints a path on a
    # machine they cannot see. Derivable when unstated, exactly like roles.
    # R1-F2: both sides of the comparison go through the one lifecycle
    # boundary — the requested side refuses an explicitly empty or unknown
    # config value instead of folding it to the default, and the declared
    # side refuses a defective kept stamp BEFORE the cache can serve it.
    want_transport = vocab.transport_or_default(
        transport if transport is not None else cfg.roles.get("transport"),
        "the transport this invocation resolved" if transport is not None
        else "[roles] transport in the governing config")
    if declared_transport(kept_request) != want_transport:
        return None
    # The debug stamp is part of the envelope's input too (2026-08-31),
    # under the same rule as the claim, the roles and the transport: a
    # warm copy stamped as a debug round must not answer an invocation
    # that did not ask for one, and the other way around — the stale
    # artifact would tell the reviewer the wrong thing about what this
    # round asks of them. Absent on both sides is a real, matching state.
    want_debug = vocab.DEBUG_TOOL_FEEDBACK if debug else None
    if attrs.get(vocab.DEBUG_ATTR) != want_debug:
        return None
    # Round 1 F2 (High). The docstring above calls a proper-subset cache key
    # the defect class it exists to prevent, and then left one input out of
    # the key: the tool identity. It is stamped ON the envelope and it covers
    # the code that runs the gates, validates and renders — so a copy emitted
    # under a different behavioural set is not a current result, and serving
    # it lets an upgrade leave the author on the old runner's attestations
    # while `cached: true` says the opposite. Reporting `differs` afterwards
    # is evidence, not currency. Only an exact match is warm: an absent or
    # unequal stamp cannot be SHOWN to be current, and the cheap answer to
    # "cannot be shown" is one re-emission.
    if attrs.get("tool", "") != tool_identity():
        return None
    # Public issue #7: the review base, resolved as the cold path resolves
    # it, must be the base the kept envelope names (docstring above). Last,
    # so every boundary before it keeps its order: a defective declaration
    # still refuses whatever base was asked for.
    kept_base = (m.group(1) if (m := _BASE_LINE_RE.search(kept_request.body))
                 else None)
    if base is _BASE_UNSTATED or base is None or kept_base is None:
        return None
    if base != kept_base:
        try:
            resolved = run("rev-parse", "--verify", f"{base}^{{commit}}")
        except RuntimeError:
            return None
        if resolved != kept_base:
            return None
    return {"envelope": text, "sha": head, "round": round_no,
            "kept": str(path), "digest": request["source_digest"]}


def _reviewer_next(parsed, kept: str, reviewer: str,
                   reference: str | None = None):
    """The one command the reviewer runs, in the topology this round declared.

    Three states, and the transport decides between the first two rather than
    the tool guessing from what it can see locally. `paste`: the reviewer is
    not on this filesystem, so the kept path is not a thing they can open and
    the only carrier is the bytes — `take -`, live, with the paste supplying
    stdin. `path`: they read the same disk, so the kept path IS the carrier
    and the command is one line with nothing to choose. The third state is
    neither declaration's doing — the bytes were not kept at all — and it
    falls to the paste form because that is the only carrier left.

    RVW-T16's named residue was here: the not-kept fallback rendered
    `take - --as <id> < <the envelope>`, a success-side placeholder in a
    field agents run verbatim. F1 (lineage 6 round 1) closed that door for
    the whole class: a placeholder-bearing line is a `Template`, and a
    Template cannot satisfy `executable()` — so the not-kept state now
    returns None here, and the record carries the template as prose in
    `then`, where a person reads it. The paste branch does NOT reproduce
    it — a declared paste round has real bytes at a real kept path, so the
    redirect a person would have to fill in does not arise.
    """
    take = (paths.Lit(TOOL_NAME), paths.Lit("take"))
    as_words = (paths.Lit("--as"), reviewer)
    declared = declared_transport(parsed)
    # The `git` round is the fourth state, and the only one whose command
    # names neither a local path nor a paste: the envelope is on a ref both
    # sides reach, so the reviewer fetches it by round reference. It is
    # the one state that answers ahead of the not-kept check: the push
    # carries the EMITTED text, not the kept copy, so a round whose local
    # copy could not be kept still has a carrier and a runnable line.
    if declared == vocab.TRANSPORT_GIT and reference:
        return paths.command(*take, reference, *as_words)
    if kept.startswith("not kept"):
        return None
    if declared == vocab.TRANSPORT_PASTE:
        return paths.command(*take, paths.Lit("-"), *as_words)
    return paths.command(*take, kept, *as_words)


def _reviewer_note(parsed, kept: str, reviewer: str) -> str | None:
    """Prose for the one state `_reviewer_next` cannot answer with a
    runnable line: the bytes were not kept, so the command needs a stdin a
    person must supply, and the template travels as prose."""
    if not kept.startswith("not kept"):
        return None
    template = paths.command(
        paths.Lit(TOOL_NAME), paths.Lit("take"), paths.Lit("-"),
        paths.Lit("--as"), reviewer, paths.Op("<"),
        paths.Ph("<the envelope>"))
    return (f"the bytes were not kept, so only the person holding the "
            f"envelope can carry it: `{template}`, with the envelope "
            f"supplied on stdin")


def record_handoff(cfg: Config, ledger: Ledger, envelope: str,
                   round_no: int, lineage: str, claim_digest: str = "",
                   git=None) -> dict:
    parsed = wire.parse_request(envelope)
    digest = _digest_text(envelope)
    size = len(envelope.encode("utf-8"))
    kept = keep_bytes(cfg, round_no, "request", envelope,
                      lineage=lineage)
    # The request leg of a `git` round: the envelope goes to its ref on the
    # remote the reviewed branch was just pushed to, and the relay becomes
    # one line naming the round instead of a block of bytes. Both handoff
    # paths reach this function — the cold one and the warm cache — so a
    # re-run re-pushes identical bytes to the same ref, which is a no-op
    # the same way the warm serve is.
    reference = None
    carrier = None
    if declared_transport(parsed) == vocab.TRANSPORT_GIT:
        carrier = push_envelope(cfg, lineage, round_no, "request", envelope,
                                git=git)
        reference = round_reference(lineage, round_no)
    # The request event, then (round 1 F6) the evidence the request carries,
    # content-addressed, so the repetition breaker's documented escape
    # condition is observable — one shape with `take` and `ledger add`.
    added = ledger.add_all(request_events(parsed, round_no, digest, size,
                                          claim_digest=claim_digest),
                           lineage=lineage)
    reviewer = parsed.attrs.get("reviewer", "")
    return _runnable({"round": round_no, "sha": parsed.sha, "digest": digest,
            "bytes": size, "kept": kept, "recorded": bool(added),
            # The review this round belongs to, by id. Displayed everywhere
            # the ordinal used to be (brief `keyed-lineage`, item 2).
            "lineage": str(lineage),
            "reviewer": reviewer,
            # Where the envelope was put, when it was put anywhere: the ref
            # and the remote a person can check for themselves. Null on
            # every other topology, which is the honest answer — the bytes
            # went nowhere but the kept path.
            "carrier": carrier,
            # --as is part of the literal command, not an option to discover:
            # round 1 F4 made the declaration mandatory, and the adapters tell
            # agents to run what the tool printed without improvising flags.
            "reviewer_next": _reviewer_next(parsed, kept, reviewer,
                                            reference),
            # Prose for the not-kept state, where no runnable line exists
            # (F1: a placeholder template may not ride a runnable field).
            "then": _reviewer_note(parsed, kept, reviewer),
            # Prose, not a command: it says STOP, and the command it
            # mentions is the one that comes after the verdict arrives.
            "author_next": "stop: hand the envelope to the reviewer — "
                           "carrying it is the human's relay, not a "
                           "round they start; nothing else runs on "
                           "this side until "
                           f"the verdict arrives, then "
                           f"`{paths.command(*paths.lits(TOOL_NAME, 'close', '--verdict'), paths.Ph('<file>'))}`"},
                     "reviewer_next")


# ---------------------------------------------------------------------- take

#: A remote URL reduced to the repository it names, so the two ends can be
#: compared across the spellings git accepts for one repository:
#: `git@host:owner/repo.git`, `https://host/owner/repo`, `ssh://git@host/
#: owner/repo.git` and `file:///path/repo.git` all reduce to the same key.
#: Userinfo goes (the emitter already scrubs it out of the stamp), the `.git`
#: suffix goes, and the comparison is case-insensitive — a key that differs
#: only in case is not a different repository on any host this tool meets.
_URL_SCHEME_RE = re.compile(r"\A[A-Za-z][A-Za-z0-9+.-]*://(?:[^@/]*@)?")
_URL_SCP_RE = re.compile(r"\A(?:[^@/]*@)?(?P<host>[^/:]+):(?P<path>.+)\Z")


def remote_key(url: str) -> str:
    """The repository a remote URL names, normalised for comparison."""
    text = (url or "").strip().rstrip("/")
    stripped = _URL_SCHEME_RE.sub("", text)
    if stripped == text:
        m = _URL_SCP_RE.match(text)
        if m:
            stripped = f"{m.group('host')}/{m.group('path')}"
    if stripped.endswith(".git"):
        stripped = stripped[:-len(".git")]
    return stripped.strip("/").lower()


def clone_remotes(repo_root) -> list[str]:
    """Every remote URL this clone is configured with, in `git remote -v`
    order. A clone with none returns the empty list, which is a state and
    not an error: a local-only round is taken in the clone that emitted it.
    """
    try:
        out = _git(repo_root, "remote", "-v")
    except RuntimeError:
        return []
    urls = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] not in urls:
            urls.append(parts[1])
    return urls


def probe_target(cfg: Config, push: dict | None, sha: str,
                 base: str | None, fetch: bool = True, git=None,
                 retake: tuple[str, str] | None = None,
                 remotes: list[str] | None = None) -> dict:
    """Make the target real in THIS clone before a token is spent (§5.1).

    Every state is recorded distinctly: fetched (the stamp's fetch command
    ran and the object is present), present (the object was already here,
    fetch skipped or unnecessary), absent (refused). `git` is injectable
    the same way `ensure_pushed`'s is, so every refusal is testable without
    a network.

    Measured 2026-08-31 and fixed here: the clone is checked BEFORE the
    fetch. `take` used to fetch the stamped ref into whatever checkout the
    caller happened to be standing in, and the fetch then removed the only
    signal that anything was wrong — the foreign commit resolved locally,
    the target looked legitimate, and the reviewer leg of another
    repository's round was appended to THIS repository's append-only
    ledger, where it cannot be removed. So a stamped target whose remote
    this clone does not know, and whose object this clone does not already
    hold, refuses here: before the fetch, and before any event is written.
    `remotes` is injectable for the same reason `git` is.
    """
    # Round 3 F1 (lineage 12): every read below dereferences the stamped
    # target — presence, ancestry, the configuration — so the runner
    # itself disables replacement. The flag is git-wide and cannot ride as
    # a subcommand argument, so this is the only place it can go.
    run = git or (lambda *a: _git(cfg.repo_root, *a, no_replace=True,
                                  ceiling=git_ceiling(cfg)))
    result = {"sha": sha, "base": base}
    if push is None:
        raise Refusal("the request carries no reachability stamp: a SHA the "
                      "reviewer cannot fetch is not a review target "
                      f"(§9bis.4); return the envelope to the author — "
                      f"`{TOOL_NAME} handoff` stamps it",
                      "")

    def present(obj: str) -> bool:
        try:
            run("cat-file", "-e", f"{obj}^{{commit}}")
            return True
        except GitTimeout:
            raise
        except RuntimeError:
            return False

    if push["state"] == "pushed":
        # Which repository is this? The stamp names one by its URL, this
        # clone names its own by its remotes, and the two are compared as
        # repositories rather than as strings (`remote_key`), because one
        # repository is reached by several spellings and a reviewer who
        # cloned over https must not be refused an ssh stamp. Three states
        # pass: a remote of this clone names the stamped repository; this
        # clone already holds the target (an object that is here was not
        # fetched by this take, so no foreign history is pulled in on the
        # envelope's say-so); or this clone declares NO remote at all —
        # a scratch checkout dedicated to the review, which the empty-CI
        # reviewer is, and which contradicts no stamp because it claims
        # to be no repository in particular. The refusal fires only where
        # the contradiction is positive: this clone says it is some other
        # repository, and the target is not here.
        urls = clone_remotes(cfg.repo_root) if remotes is None else remotes
        known = {remote_key(u) for u in urls}
        if urls and remote_key(push["url"]) not in known and not present(sha):
            raise Refusal(
                f"this checkout is not the repository the envelope names: "
                f"the stamp points at {push['url']}, this clone's remotes "
                f"are {', '.join(urls)}, and {sha[:12]} is not here. "
                f"Fetching it would make a foreign commit resolve locally "
                f"and append this round to THIS repository's append-only "
                f"ledger, where it cannot be removed (2026-08-31, measured)",
                "", remedy="a person runs the take from a checkout of the "
                           "repository the stamp names — the ledger a round "
                           "is recorded in is the repository's, not the "
                           "caller's")
    if push["state"] == "pushed" and fetch:
        try:
            run("fetch", push["url"], push["ref"])
            result["fetch"] = f"fetched {push['ref']} from {push['url']}"
        except GitTimeout:
            # Not "the target is unreachable": the remote or a hook was
            # slower than the ceiling, and the remedy is that decision
            # (0.25.0).
            raise
        except RuntimeError as exc:
            raise Refusal(
                f"cannot fetch the reviewed ref: {exc}. The stamp says the "
                f"author observed it after pushing; this clone cannot see "
                f"it, so the target is not reachable from here (§9bis.4); "
                f"a person restores access to {push['url']} from this "
                f"machine, then re-runs the take",
                "") from exc
    elif push["state"] == "pushed":
        result["fetch"] = "skipped (--no-fetch)"
    else:
        result["fetch"] = ("not attempted: LOCAL-ONLY target, fetchable "
                           "from no other machine")

    if not present(sha):
        where = ("this is not the clone it was emitted from"
                 if push["state"] == "local-only" else "after the fetch")
        # Sweep F11: a runnable recovery only where one exists — the fetch
        # was skipped and the exact re-take is known; otherwise blocked,
        # because a fetch that just ran and did not bring the object is not
        # repaired by running it again.
        # `retake` is (envelope path, declared identity) — the two values
        # the literal re-take command needs, kept apart so the command's
        # shape is stated here, in the source, where the enumeration reads it.
        can_fetch = (push["state"] == "pushed" and not fetch
                     and retake is not None)
        raise Refusal(f"target {sha} is not present in this clone {where}: "
                      f"a SHA the reviewer cannot resolve is not a review "
                      f"target (§9bis.4)"
                      + ("" if can_fetch else "; a person fetches the "
                         "reviewed ref into this clone, or hands the "
                         "envelope back to the author"),
                      paths.command(
                          paths.Lit("git"), paths.Lit("-C"), cfg.repo_root,
                          paths.Lit("fetch"), push["url"], push["ref"],
                          paths.Op("&&"), paths.Lit(TOOL_NAME),
                          paths.Lit("take"), retake[0], paths.Lit("--as"),
                          retake[1])
                      if can_fetch else "")
    result["target"] = "present"
    if base:
        if not present(base):
            raise Refusal(f"base {base} is not present in this clone: the "
                          f"diff `{base[:12]}...{sha[:12]}` cannot be "
                          f"computed (§9bis.4); a person deepens this clone "
                          f"(`{paths.command(*paths.lits('git', 'fetch', '--unshallow'))}`) "
                          f"or fetches the base "
                          f"ref, then re-runs the take",
                          "")
        try:
            run("merge-base", "--is-ancestor", base, sha)
            result["base"] = "ancestor of target"
        except RuntimeError as exc:
            raise Refusal(f"base {base} is not an ancestor of {sha} in this "
                          f"clone: the envelope's diff is undefined here; "
                          f"return the envelope to the author",
                          "") from exc
    return result


def probe_references(cfg: Config, reference_section: str,
                     sha: str | None = None, git=None) -> list[dict]:
    """Label every reference checked / mismatch / asserted / unavailable /
    unrecognised (§5.1 F4). Absent digests are 'asserted', never checked.

    With `sha` given (sweep F6), every reference is read from the TARGET
    TREE through git — `<sha>:<path>` — never from the working tree. The
    manifest's digests were computed over the author's clean tree at
    exactly that commit, so the object store holds the bytes they describe;
    the reviewer's checkout may be at another commit, or may hold nothing at
    all, and neither state is evidence about the request. Without `sha` the
    working tree is read (the historical behaviour, kept for callers that
    have no target to resolve against).

    Two defects lived here together. `asserted` was documented in this
    docstring and implemented below, but the grammar could not produce it, so
    the branch was dead and the promise empty. And a line the grammar did not
    match was `continue`d — silently dropped, so a reference the author
    declared required simply ceased to exist between the envelope and the
    reviewer, while the précis upstream still counted it. Neither is reported
    as an error here, because labelling is this function's whole job; the
    caller decides what an unreadable required reference means.
    """
    # Round 2 F2 (lineage 12). With a target, both reads below derive a
    # file reference from the target OBJECT GRAPH, so both disable
    # replacement — the emitter's half is hardened identically, or the two
    # ends would describe one SHA from two different graphs. The
    # working-tree branch (`sha is None`) is a different state and is left
    # alone: there is no object graph in it to replace.
    run = git or (lambda *a: _git(cfg.repo_root, *a, no_replace=sha
                                  is not None, ceiling=git_ceiling(cfg)))

    def kind(path: str) -> str | None:
        """'blob' | 'tree' | None, at the target when `sha` is given, else
        from the working tree. With a target, this is `refs.target_kind` —
        the same call the emitter makes (round 3 F1)."""
        if sha is None:
            full = cfg.repo_root / path
            return "blob" if full.is_file() else "tree" if full.is_dir() \
                else None
        return refs.target_kind(run, sha, path)

    def digest(path: str) -> str:
        if sha is None:
            return sha256_file(cfg.repo_root / path)
        return refs.target_digest(
            lambda *a: run_bytes(cfg, git, *a, no_replace=True), sha, path)

    where = "in the target tree" if sha is not None else "in this checkout"
    out = []
    for line in reference_section.splitlines():
        if not line.strip() or line.strip().startswith("("):
            continue                      # blank, or the emitter's placeholder
        m = _REF_LINE_RE.match(line)
        if not m:
            out.append({
                "path": line.strip(),
                "status": "unrecognised: matches no form of the reference "
                          "grammar, so nothing about it can be checked"})
            continue
        path = m.group("path")
        entry = {"path": path}
        if m.group("unavailable"):
            entry["status"] = ("declared unavailable by the author; "
                               + ("present " if kind(path) else "absent ")
                               + where)
        elif m.group("dir") or m.group("note"):
            entry["status"] = ("directory " + ("present"
                                               if kind(path) == "tree"
                                               else "absent"))
        elif m.group("digest"):
            if kind(path) != "blob":
                entry["status"] = f"unavailable (not {where})"
            elif digest(path) == m.group("digest"):
                entry["status"] = "checked (digest matches)"
            else:
                entry["status"] = (f"mismatch: the file {where} does not "
                                   f"digest to the manifest's value")
        else:
            entry["status"] = "asserted (no digest to check)"
        out.append(entry)
    return out


def run_bytes(cfg: Config, git, *args: str,
              no_replace: bool = False) -> bytes:
    """Raw stdout of a git command, for content that must be digested as
    bytes. The injectable `git` runner returns text; a test that maps
    `show` returns the file's text and this encodes it back.

    `no_replace` carries the same guarantee as `_git`'s: replacement
    objects off, for the reads that decide an authority."""
    if git is not None:
        return git(*args).encode("utf-8")
    # Round 4 F3, the byte reader's half of the same normalisation.
    # RVW-T21 D2: and the caller's environment, like every other git door.
    # 0.25.0: and the configured ceiling, typed on timeout like `_git`.
    ceiling = git_ceiling(cfg)
    try:
        out = subprocess.run(["git", *([NO_REPLACE] if no_replace else []),
                              "-C", str(cfg.repo_root), *args],
                             capture_output=True, timeout=ceiling.seconds,
                             env=caller_env())
    except subprocess.TimeoutExpired as exc:
        raise GitTimeout(exc.cmd, ceiling) from exc
    except subprocess.SubprocessError as exc:
        raise RuntimeError(
            f"a `git` subprocess did not complete: "
            f"`{paths.command(paths.Lit('git'), *args)}` — "
            f"{type(exc).__name__}: {exc}") from exc
    except OSError as exc:
        raise RuntimeError(
            f"a `git` subprocess could not be started: "
            f"`{paths.command(paths.Lit('git'), *args)}` — {exc}") from exc
    if out.returncode != 0:
        raise RuntimeError(
            f"a `git` subprocess failed: "
            f"`{paths.command(paths.Lit('git'), *args)}` — "
            f"{out.stderr.decode('utf-8', 'replace').strip()}")
    return out.stdout


def _digest_bytes(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


# Round 4 F3: what "the object store could not answer" is, as a closed set.
# `_git` and `run_bytes` normalise their own failures, but this boundary
# promises a typed refusal and so establishes it here rather than resting on
# a collaborator a caller may replace.
_UNUSABLE = (RuntimeError, subprocess.SubprocessError, OSError)

def _config_basename() -> str:
    from . import config as _config
    return _config.CONFIG_BASENAME


AUTHORITY_TARGET = "target"
AUTHORITY_EXTERNAL = "external"


def resolve_authority(cfg: Config, sha: str,
                      git=None) -> tuple[Config, str]:
    """The configuration governing `sha`, and WHERE it came from.

    Round 3 F2 and F3. This was `target_config`, which caught every
    `RuntimeError` from `git show <sha>:review.toml` and answered "the
    target carries none" — and `_git` turns every nonzero git exit into
    that one class. So a present entry whose blob object is missing, whose
    mode is not a blob, or whose read failed for any operational reason was
    answered with the CHECKOUT's rules, and round 3's digest could not see
    it because both ends asked the same misclassifying question. Invalid
    UTF-8 escaped further still: the text-mode reader raised
    `UnicodeDecodeError` past every typed refusal.

    External fallback is advertised for exactly ONE state — the file is not
    there — so that state is established by asking the question that
    answers it (`ls-tree`, which prints nothing and exits 0 for a path a
    tree lacks) rather than inferred from a failure to read. Every other
    outcome is a refusal: git unusable, a non-blob entry, an unreadable
    blob, bytes that are not UTF-8, TOML the config layer rejects.
    """
    from . import config as _config
    basename = _config.CONFIG_BASENAME
    # Round 1 F1 (lineage 12): BOTH reads run with replacement objects off.
    # This is the ONE resolver all three doors call, so hardening it here is
    # what makes the author, `take` and `validate --from-target` inherit the
    # guarantee — there is no second read to forget.
    run = git or (lambda *a: _git(cfg.repo_root, *a, no_replace=True,
                                  ceiling=git_ceiling(cfg)))
    fix = (f"the AUTHOR repairs the configuration in the target commit and "
           f"re-emits; a reviewer does not edit the rules it is judged by")
    try:
        entry = run("ls-tree", "--full-tree", sha, "--", basename).strip()
    except _UNUSABLE as exc:
        raise Refusal(
            f"this clone cannot say whether {sha[:12]} carries {basename}, "
            f"so "
            f"neither its rules nor their absence is established: {exc}",
            "", remedy="a person repairs this clone; an unusable object "
                       "store is not evidence that a target carries no "
                       "configuration")
    if not entry:
        return dataclasses.replace(
            cfg, source=f"{cfg.source} (external; {sha[:12]} carries no "
                        f"{basename})"), AUTHORITY_EXTERNAL
    # Round 4 F1: the OBJECT TYPE was the whole check, and a symlink is
    # mode 120000 with type `blob`. So a committed symlink entered the
    # target branch and its LINK TEXT was parsed as TOML, while the author's
    # `config.load` — which uses Path.is_file() and read_text() — followed
    # the link and read the file it points at. Two ends, one SHA, different
    # bytes: the cross-end authority split this lineage exists to remove,
    # arriving through the one git mode whose type says `blob` and whose
    # content is a path. The mode is what says whether an entry is a file.
    # A tree may carry the SAME path twice. `git mktree` accepts it and
    # `git fsck` calls it `duplicateEntries`, but the object reads, and
    # `ls-tree` then prints TWO lines. `split(maxsplit=3)` cannot tell that
    # from one line: it yields four fields either way, so the length check
    # below passes, only the FIRST entry's mode is examined, and the whole
    # second entry rides along unread inside `fields[3]`. Measured on this
    # machine. The read that follows resolves the first entry too, so no
    # divergence between the two ends is demonstrated — but "the mode I
    # checked is the mode of the blob I read" would be resting on an
    # undocumented tie-break rather than on anything asked. One line asks.
    if "\n" in entry:
        raise Refusal(
            f"the tree listing for {basename} in {sha[:12]} carries more "
            f"than one entry, so which entry the rules would be read from "
            f"is not established: a duplicated path is not a tree this "
            f"boundary will guess about",
            "", remedy=fix)
    fields = entry.split(maxsplit=3)
    if len(fields) < 3:
        raise Refusal(
            f"the tree listing for {basename} in {sha[:12]} cannot be read "
            f"({entry!r}), so what the entry IS is not established",
            "", remedy=fix)
    mode, kind = fields[0], fields[1]
    if mode not in vocab.GIT_FILE_MODES:
        raise Refusal(
            f"{basename} in {sha[:12]} is {vocab.git_mode_name(mode)} "
            f"(mode {mode}, type {kind}), not a regular file, so the bytes "
            f"it declares are not the bytes a checkout would read — and its "
            f"presence is not absence",
            "", remedy=fix)
    try:
        raw = run_bytes(cfg, git, "show", f"{sha}:{basename}",
                        no_replace=True)
    except _UNUSABLE as exc:
        raise Refusal(
            f"{basename} is PRESENT in {sha[:12]} and cannot be read "
            f"({exc}); a file that exists and cannot be read is not a file "
            f"that is absent, and this checkout's rules are not a "
            f"substitute for it",
            "", remedy=fix)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Refusal(
            f"{basename} in {sha[:12]} is not valid UTF-8 ({exc}), so the "
            f"rules it declares cannot be read",
            "", remedy=fix)
    try:
        return _config.from_text(
            text, cfg,
            source=f"target {basename} at {sha[:12]}"), AUTHORITY_TARGET
    except _config.ConfigError as exc:
        raise Refusal(
            f"{basename} in {sha[:12]} cannot be read: {exc}", "",
            remedy=fix)


def governing_for(cfg: Config, sha: str, git=None) -> Config:
    """The authority that governs an envelope about `sha`, for the verbs
    that are not `take`: the target commit's own configuration, read from
    the commit.

    HISTORY, because this docstring described machinery that no longer
    exists and shipped that way in 0.9.0. Round 1 F1 made this refuse
    where `target_config` fell back. Round 2 F1 showed that made it
    stricter than `take`, so rounds 2 to 5 built a proof that the rules a
    verdict was judged by were the rules its request was judged by — a
    recorded authority identity, matched rather than re-derived. Round 6
    F1 deleted all of it: an authority living on one machine can never be
    shown to a second, and a review is the act of showing it. The origin
    went, and with it the record, the matching and the fallback.

    So there is nothing left to prove and nothing to fall back to. The
    question is the one `take` asks, of the input `take` asks it of, and
    since RVW-T17 it is also the one the AUTHOR asks: `git show
    <sha>:review.toml`. A target that carries no configuration refuses at
    all three doors, identically.
    """
    # Round 3 F1 (lineage 12): every read below dereferences the stamped
    # target — presence, ancestry, the configuration — so the runner
    # itself disables replacement. The flag is git-wide and cannot ride as
    # a subcommand argument, so this is the only place it can go.
    run = git or (lambda *a: _git(cfg.repo_root, *a, no_replace=True,
                                  ceiling=git_ceiling(cfg)))
    drop = ("a person validates without the declaration, accepting that "
            "whatever configuration this checkout resolves NOW is a "
            "different authority from the one the request was judged under")
    try:
        run("cat-file", "-e", f"{sha}^{{commit}}")
    except RuntimeError:
        raise Refusal(
            f"target {sha[:12]} is not in this clone, so the authority it "
            f"was judged under cannot be resolved here",
            "", remedy=f"a person fetches the target into this clone — "
                       f"`{paths.command(paths.Lit('git'), paths.Lit('fetch'), paths.Ph('<remote>'), paths.Ph('<ref>'))}` "
                       f"— or {drop}")
    governing, origin = resolve_authority(cfg, sha, git=git)
    if origin != AUTHORITY_TARGET:
        raise Refusal(
            f"{sha[:12]} carries no {_config_basename()} of its own, so the "
            f"rules it would be judged by live on one machine and cannot be "
            f"shown to another. A reviewed commit carries its own authority",
            "", remedy=f"the AUTHOR commits {_config_basename()} and "
                       f"re-emits; a review whose rules are not in the "
                       f"artifact is a review two ends cannot agree on")
    return governing


#: The grammar a CARRIED lineage id must satisfy before this end binds a
#: round to it: a minted `L<hex>`, a legacy ordinal, or anything else a
#: person materialised — but never a path segment, a ref fragment or an
#: empty string, because the id names a directory under `exchange/` and a
#: ref under `refs/<tool>/`.
_LINEAGE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


def stamped_lineage(parsed) -> str | None:
    """The lineage id the REQUEST ENVELOPE carries, or None.

    Round-2 F5. The author's emitter knows the id — it is the key the round
    is recorded, kept and pushed under on that machine — and before this
    it kept it to itself: a `path` or `paste` envelope named no review at
    all, so a reviewer taking two independent branch reviews had nothing to
    tell a continuation from a new review and the declared fallback merged
    them into one lineage. The id now rides on the wrapper, where an older
    reader ignores it (§3.1: an unknown attribute meets no flag day) and a
    pre-0.20.0 envelope carries none, which is the legacy path below.

    A value that is not an id is read as ABSENT rather than refused: this
    attribute chooses a key on the reviewer's side and decides no grammar,
    and an envelope is not defective for carrying a stamp this reader
    cannot use.
    """
    value = (getattr(parsed, "attrs", {}) or {}).get("lineage")
    value = value.strip() if isinstance(value, str) else ""
    return value if value and _LINEAGE_ID_RE.match(value) else None


def take_lineage(ledger: Ledger, sha: str,
                 carried: str | None = None,
                 stamped: str | None = None,
                 branch: str = "") -> str:
    """The lineage a `take` records into — the rule in `take`'s own
    docstring, in one place so the record and the kept bytes cannot
    disagree about it. Raises `Refusal` rather than guessing.

    Round-2 F5 replaced the old fourth step. "The sole open lineage" cannot
    distinguish a continuation from a new independent review, and taking two
    path-carried reviews of two branches recorded both as one lineage on the
    reviewer's side — the combined-round state keying exists to remove. The
    provenance the ingress was missing is now on the envelope (`stamped`),
    and where an envelope predates it the AUTHOR'S BRANCH — already stamped
    since 2026-09-05 and already recorded on the reviewer's request events —
    says whether this is the review already open here. Where neither
    answers and more than one association is possible, this refuses; it
    never adopts.
    """
    if carried:
        return str(carried)
    if stamped:
        return str(stamped)
    stored = ledger.recorded_lineage_for_sha(sha)
    if stored is not None:
        return stored                      # an idempotent retake
    open_lineages = ledger.open_lineages()
    if not open_lineages:
        return new_lineage_id()
    named = {l: known_branch(ledger.lineage_branch(l)) for l in open_lineages}
    mine = branch and [l for l, b in named.items() if b == branch]
    if mine:
        if len(mine) == 1:
            return mine[0]
        raise Refusal(
            f"{len(mine)} open reviews on this ledger record branch "
            f"{branch} ({', '.join(mine)}), and this request carries no "
            f"lineage id, so which review it continues cannot be derived — "
            f"a take that guessed would merge two reviews into one",
            "", remedy="the AUTHOR re-emits with a current version, whose "
                       "request carries its own lineage id, or hands over "
                       "the round as `git:<lineage>/<round>`; this end "
                       "picks between open reviews for nobody")
    unbranched = [l for l, b in named.items() if not b]
    if branch and not unbranched:
        # Every open review here names a branch and none of them is this
        # envelope's: an independent review, which opens its own lineage
        # rather than being folded into somebody else's.
        return new_lineage_id()
    if len(open_lineages) == 1 and (not branch or unbranched):
        # The legacy continuation this step exists for: one open review,
        # recorded before either the id or the branch was stamped, and
        # rounds 2 and 3 of it land where round 1 did.
        return open_lineages[0]
    raise Refusal(
        f"{len(open_lineages)} reviews are open on this ledger "
        f"({', '.join(open_lineages)}) and this request carries neither a "
        f"lineage id nor a branch that matches one of them, so whether it "
        f"continues one of them or opens a new review cannot be derived",
        "", remedy="the AUTHOR re-emits with a current version, whose "
                   "request carries its own lineage id, or hands over the "
                   "round as `git:<lineage>/<round>`; adopting an open "
                   "review on a guess is what merges two of them")


#: The three answers `reviewer_checkout` can give. A closed set, so the
#: rendering branches on a state and never on a sentence.
CHECKOUT_AT_TARGET = "at-target"
CHECKOUT_ELSEWHERE = "elsewhere"
CHECKOUT_UNKNOWN = "unknown"


def reviewer_checkout(cfg: Config, sha: str, git=None) -> dict:
    """What the reviewer's working tree IS, beside what was fetched.

    Tool feedback, pilot round 1 (brief `loupe-tool-feedback-pilot-2026-09`
    item 3): `take` reported the target object present and said nothing of
    HEAD, so a reviewer whose checkout sat at an older commit read the
    target's diff and ran its own probes against other bytes — that round's
    F1 was exactly that defect, found in the harness that carried it.

    REPORTED, never refused: a reviewer rules from `git show` and the diff
    command as legitimately as from a checkout, and an empty or unborn clone
    is a documented reviewer state. So this states one of three things and
    `take` prints it; what to do about `elsewhere` is the reviewer's.
    A dirty tree at the target is still `at-target`, with `tree` saying
    dirty: the two facts are separate and both are printed.
    """
    run = git or (lambda *a: _git(cfg.repo_root, *a,
                                  ceiling=git_ceiling(cfg)))
    try:
        head = run("rev-parse", "HEAD")
    except RuntimeError:
        return {"state": CHECKOUT_UNKNOWN, "sha": None, "tree": None}
    try:
        tree = "dirty" if run("status", "--porcelain") else "clean"
    except RuntimeError:
        tree = None
    state = CHECKOUT_AT_TARGET if head == sha else CHECKOUT_ELSEWHERE
    return {"state": state, "sha": head, "tree": tree}


def take(cfg: Config, ledger: Ledger, envelope: str, source: str,
         reviewer: str | None = None, fetch: bool = True,
         validate_items=None, git=None, transport: str | None = None,
         remotes: list[str] | None = None,
         lineage: str | None = None) -> dict:
    """The reviewer's one command. Returns the record; raises Refusal.

    `lineage` is the lineage CARRIED by a `git:<lineage>/<round>` reference
    (F1), and since 2026-09-06 it is the lineage ID and therefore the key
    the reviewer's own ledger records this round under: one id names one
    review on both machines, which is what makes the envelope refs and the
    two ledgers agree (brief `keyed-lineage`). None for every other carrier
    — `path` and `paste` name no lineage.

    WHICH LINEAGE A TAKE RECORDS INTO, deterministically:

      1. the carried id, when the reference named one;
      2. failing that, THE ID THE ENVELOPE ITSELF STAMPS — the author's,
         and since 0.20.0 the key both machines use for one review
         (round-2 F5). `path` and `paste` name no reference, but the
         request they carry names its review;
      3. failing that, the lineage this ledger already recorded for the
         SHA — a retake of a round this reviewer has seen;
      4. failing that — a pre-0.20.0 envelope, which stamps no id — the
         open lineage whose recorded AUTHOR BRANCH is this envelope's, so
         rounds 2 and 3 of a pasted review land where round 1 did; a new
         id when every open review here is another branch's; and a REFUSAL
         when several associations remain possible.

    The reviewer's own BRANCH is deliberately not consulted: a request
    event's `branch` is the AUTHOR's, read off the envelope, so it says
    nothing about the checkout ruling on it. Step 4 compares that AUTHOR
    branch with the author branch the reviewer's own request events
    recorded — both ends of that comparison are the author's.

    `transport` is the reviewer's correction of what the envelope declares,
    and it exists because the author's declaration can be wrong in exactly
    one direction that matters: an author who did not know the reviewer was
    elsewhere stamps `path`, and the verdict leg would then print a command
    naming a file on the REVIEWER's machine to an author who cannot open it —
    round 4's F2, arriving through a mis-declaration instead of through
    silence. The reviewer is the one party who knows, so the reviewer may
    say so; the correction is recorded beside the envelope's own value rather
    than replacing it, because a disagreement between the two ends about the
    channel is a fact worth keeping, not one to overwrite.

    It is a declaration, not a detection. `take -` is NOT read as evidence of
    a paste — an agent may pipe a local file, and a carrier inferred from the
    shape of an argument is the same fail-open guess `vocab` refuses.
    """
    parsed = wire.parse_request(envelope)
    if not parsed.wrapped:
        raise Refusal(f"{source} is not a review request",
                      paths.command(paths.Lit(TOOL_NAME),
                                    paths.Lit("validate"), source))

    # Round 1 F4. The identity must be DECLARED, never defaulted. Falling back
    # to [roles] reviewer meant any process omitting --as permanently appended
    # a take event naming a reviewer who had asserted nothing — which the live
    # ledger already demonstrates. The CLI cannot prove who is running it, so
    # the honest options are an explicit declaration or no event at all; a
    # convenient default is the one option that writes a false record. Note
    # what this does and does not buy: `rejected_reviewers` enforces the
    # DECLARATION and cannot detect an actor who declares someone else.
    me = (reviewer or "").lower()
    addressed = parsed.attrs.get("reviewer", "").lower()
    if not me:
        raise Refusal("no reviewer identity was declared: `take` records who "
                      "ruled, and this tool cannot observe that — it can only "
                      "carry what you assert. [roles] reviewer names the "
                      "repository's default DIRECTION, not who is at this "
                      "keyboard, so it is not an answer here (§7: silence "
                      "must not pick a side). Re-run as "
                      f"`{TOOL_NAME} take {paths.shell_path(source)} "
                      f"--as <your identity>`",
                      "")
    if me != addressed:
        raise Refusal(f"this envelope is addressed to reviewer "
                      f"{addressed!r}; you are {me!r} — the stamp names who "
                      f"rules, and taking it as someone else would review "
                      f"under a role the author did not assign (§7); hand "
                      f"it to the stamped reviewer, or have the author "
                      f"re-emit with the intended stamp",
                      "")

    # Round-2 F4: the TARGET-INDEPENDENT grammar first, before any git call
    # — wrapper and attributes, the closed section grammar, reference and
    # diff-shape grammar, the stamp's syntax. Sweep F6 moved validation
    # after the fetch so the target's own config could govern it, and in
    # doing so let a defective envelope choose the URL this clone fetched
    # from before it was established to be a request at all. Split: what
    # needs no configuration is judged here; what the target commit's
    # configuration governs is judged after the fetch, against it.
    structural = validate.validate_request(parsed, cfg, structural_only=True)
    errors = [i for i in structural if i.level == "error"]
    if errors:
        raise Refusal("the request fails validation ("
                      + ", ".join(i.code for i in errors) + "): a reviewer "
                      "does not rule on a defective envelope, and this "
                      "clone fetches nothing on its account",
                      "", remedy=_AUTHORS_TO_FIX.format(
                          authority="the envelope's own target-independent "
                                    "grammar, which no configuration "
                                    "governs"),
                      items=errors)

    round_no = int(parsed.attrs.get("round", "0") or 0)
    sha = parsed.sha or ""
    # F1: a stored lineage for this SHA (a prior take of the same round,
    # itself carried) disagreeing with what THIS take carries is not a
    # choice the tool makes silently — the later value winning would move
    # the round's verdict ref out from under whoever already has the
    # earlier reference.
    if lineage is not None:
        # Round-3 F3: the comparison is by REQUEST, not by commit. Two
        # independently emitted reviews of one commit are two requests with
        # two digests and both enter; the same bytes carried under a second
        # lineage is a retake redirecting a verdict's destination, refused.
        taken_elsewhere = [l for l in ledger.lineages_for_source_digest(
            _digest_text(envelope)) if l != lineage]
        if taken_elsewhere:
            stored = taken_elsewhere[0]
            raise Refusal(
                f"these exact request bytes for {sha[:12]} were already taken "
                f"under lineage {stored}, and this take carries lineage "
                f"{lineage} for the same request — a round's lineage is fixed "
                f"by the take that first records it, and the tool will not "
                f"move a verdict's destination between two lineages for one "
                f"round",
                "", remedy="carry the same round reference every time this "
                           "round is taken; a genuinely different review of "
                           "this commit is a new request with its own bytes, "
                           "and that one enters on its own reference")
    base_m = _BASE_LINE_RE.search(parsed.body)
    base = base_m.group(1) if base_m else None
    push = wire.parse_push_line(parsed.body)
    # Sweep F6: the target FIRST. Validation used to run before the fetch,
    # against whatever configuration this checkout happened to hold — and
    # an unborn or empty reviewer checkout holds none, so a valid envelope
    # was refused as T-UNDECLARED and sent back to its author for a fault
    # in the reviewer's tree, with zero git calls made. The stamp is what
    # makes the target real here; the target is what carries the config
    # that governs the request; so: fetch and resolve, then read the exact
    # target's review.toml, then validate against THAT — never against
    # unrelated working-tree bytes.
    target = probe_target(cfg, push, sha, base, fetch=fetch, git=git,
                          retake=(source, me), remotes=remotes)
    governing, origin = resolve_authority(cfg, sha, git=git)
    if origin != AUTHORITY_TARGET:
        raise Refusal(
            f"{sha[:12]} carries no {_config_basename()} of its own, so "
            f"there is no authority in the artifact to rule under — and "
            f"this checkout's is a different one (round 2 F1: the two ends "
            f"refuse and accept the same states)",
            "", remedy=f"the AUTHOR commits {_config_basename()} and "
                       f"re-emits")
    target["config"] = governing.source
    items = (validate_items(parsed, governing) if validate_items else [])
    errors = [i for i in items if i.level == "error"]
    if errors:
        raise Refusal("the request fails validation ("
                      + ", ".join(i.code for i in errors) + "): a reviewer "
                      "does not rule on a defective envelope",
                      "", remedy=_AUTHORS_TO_FIX.format(
                          authority=f"the TARGET commit's own configuration "
                                    f"({governing.source})"),
                      items=errors)
    checkout = reviewer_checkout(cfg, sha, git=git)
    reference = wire.section(parsed.sections, "reference")
    # References are read from the target tree as well: the manifest's
    # digests describe bytes at that commit, and this checkout — at another
    # commit, or empty — is not evidence about them.
    refs = probe_references(cfg, reference, sha=sha, git=git)

    digest = _digest_text(envelope)
    size = len(envelope.encode("utf-8"))
    recorded_lineage = take_lineage(
        ledger, sha, lineage, stamped=stamped_lineage(parsed),
        branch=known_branch(parsed.attrs.get("branch")))
    kept = keep_bytes(cfg, round_no, "request", envelope,
                      lineage=recorded_lineage)
    ledger.add_all(request_events(parsed, round_no, digest, size),
                   lineage=recorded_lineage)
    ref_summary = {}
    for r in refs:
        key = r["status"].split(" ", 1)[0].rstrip(":")
        ref_summary[key] = ref_summary.get(key, 0) + 1
    # RVW-T11. The envelope's declaration, corrected by the reviewer if the
    # reviewer said so — and BOTH values recorded when they differ, so the
    # ledger shows a disagreement about the channel rather than only its
    # winner. `declared` is what the author stamped; `transport` is what this
    # end will render its verdict leg for.
    declared = declared_transport(parsed)
    effective = transport if transport is not None else declared
    # Which installation ruled, in the record. The 2026-08-23 skew was
    # reconstructed afterwards by noticing that the take event lacked a
    # field the request had — inference from an absence. It is stated now.
    agreement = tool_agreement(parsed)
    take_event = {"event": "take", "round": round_no, "sha": sha,
                  "reviewer": me, "source_digest": digest,
                  "transport": effective,
                  "tool": agreement["reader"],
                  "tool_agreement": agreement["agreement"],
                  "fetch": target.get("fetch"), "references": ref_summary}
    if agreement["writer"]:
        take_event["tool_writer"] = agreement["writer"]
    if effective != declared:
        take_event["declared_transport"] = declared
    ledger.add(take_event, lineage=recorded_lineage)
    diff_cmd = (paths.diff_command(cfg.repo_root, base, sha) if base
                else paths.command(
                    paths.Lit("git"), paths.Lit("-C"), cfg.repo_root,
                    paths.Lit("--no-replace-objects"),
                    paths.Lit("show"), sha))
    # Public issue #10 (0.26.0): the scoped diff for THIS checkout, beside
    # the whole one — the request's `Scoped:` pathspecs re-rendered through
    # the one diff renderer, rooted here instead of at the author's clone.
    specs, scoped_note = scoped_pathspecs(
        parsed.body, base, sha,
        render=lambda root, *given: paths.diff_command(root, base, sha,
                                                       *given))
    scoped_cmd = None
    if specs:
        from .emit import SCOPED_COMMAND_MAX
        scoped_cmd = paths.diff_command(cfg.repo_root, base, sha, *specs)
        size = len(str(scoped_cmd).encode("utf-8"))
        if size > SCOPED_COMMAND_MAX:
            # The emitter's bound, for the same reason: a longer line is
            # withheld whole, never shortened into another selection.
            scoped_cmd, scoped_note = None, (
                f"rooted in this checkout the command is {size} bytes, over "
                f"the {SCOPED_COMMAND_MAX}-byte bound for one command line, "
                f"so it is withheld; the diff line is the whole span")
    return _runnable({"round": round_no, "sha": sha, "reviewer": me,
            "lineage": recorded_lineage,
            "target": target, "head": checkout,
            "references": refs, "kept": kept,
            "digest": digest, "diff": diff_cmd,
            "scoped": scoped_cmd, "scoped_note": scoped_note,
            "envelope": envelope,
            "transport": effective, "tool": agreement,
            # F3: `decide` names what the TARGET's own committed
            # `review.toml` never declared — `governing` is that config,
            # already resolved above to judge this request. The caller's
            # own checkout config is not this take's authority for
            # anything, and reporting ITS silence here told an empty or
            # older reviewer checkout that keys the target repository has
            # long since declared were still open questions.
            "decide": governing.decisions(
                {vocab.DECIDE_TRANSPORT: effective}),
            # The flag is not decoration: this `take` resolved the
            # governing configuration from the target commit, and the
            # command it hands over must resolve the SAME authority or the
            # reviewer validates their verdict against whatever their own
            # checkout happens to hold — which, on the cross-machine round
            # this tool exists for, is routinely nothing at all.
            "then": f"write the verdict as <{parsed.tag}-review-verdict "
                    f'sha="{sha}"> and run '
                    f"`{paths.command(*paths.lits(TOOL_NAME, 'validate'), paths.Ph('<verdict.md>'), paths.Lit('--from-target'))}`; "
                    f"then stop — do not start the next round (standing "
                    f"instructions)"},
                     "diff", "scoped")


# --------------------------------------------------------------------- close

def _unruled_open_requests(ledger: Ledger, round_no: int, sha: str,
                           lineage: str) -> list[dict]:
    """The open requests a verdict for (`round_no`, `sha`) does NOT answer.

    A verdict answers the request that asked for it, and the superseded
    emissions of that same round on that same branch — amend-and-re-emit
    replaces its own emission, and the survivor is what was ruled. Anything
    else open in this lineage is a round nobody ruled: another round, or the
    same round emitted twice within it. A round of ANOTHER lineage is not
    this verdict's business and never was reachable here.

    Branch absence is unknown on either side, and unknown never makes a
    round foreign — a pre-field ledger reads exactly as it did before.
    """
    ruling = next((e for e in reversed(ledger.open_requests(lineage))
                   if e.get("round") == round_no and e.get("sha") == sha),
                  None)
    ruled_branch = known_branch((ruling or {}).get("branch"))
    out = []
    for e in ledger.open_requests(lineage):
        if e.get("round") != round_no:
            out.append(e)
            continue
        mine = known_branch(e.get("branch"))
        if mine and ruled_branch and mine != ruled_branch:
            out.append(e)
    return out


def close_round(cfg: Config, ledger: Ledger, verdict_text: str, source: str,
                lineage: str, round_no: int | None = None,
                tokens: int | None = None, validate_items=None) -> dict:
    parsed = wire.parse_verdict(verdict_text)
    if not parsed.wrapped and parsed.verdict is None:
        raise Refusal(f"{source} is not a verdict",
                      paths.command(paths.Lit(TOOL_NAME),
                                    paths.Lit("validate"), source))
    items = validate_items(parsed) if validate_items else []
    errors = [i for i in items if i.level == "error"]
    if errors:
        # The recovery names the authority the verdict is judged under —
        # the target's (finding 3, 2026-09-05); a bare `validate` would
        # re-judge it under this checkout, the wrong authority twice.
        raise Refusal("the verdict fails validation ("
                      + ", ".join(i.code for i in errors) + "): it is "
                      "returned to the reviewer, not recorded",
                      paths.command(paths.Lit(TOOL_NAME),
                                    paths.Lit("validate"), source,
                                    paths.Lit("--from-target")))
    # Round 1 F1 (Blocker). The SHA binding is the tool's central invariant and
    # a clean verdict is merge-authorizing evidence, so the round a verdict is
    # filed under may never be taken on the caller's word: `round_no or
    # round_for_sha(...)` let an explicit --round skip the lookup entirely, and
    # a clean verdict for an unrequested commit then wrote a `clean at the exact
    # reviewed SHA` lineage marker for a commit nobody reviewed. The derived
    # round is now authoritative and an explicit one may only AGREE with it.
    ambiguous = ledger.ambiguous_rounds_for_sha(parsed.sha, lineage)
    if ambiguous:
        raise Refusal(
            f"sha {parsed.sha} is bound by more than one OPEN request in this "
            f"lineage (rounds {ambiguous}), so which round this verdict "
            f"answers cannot be derived and must not be guessed — a verdict "
            f"closes the request that asked for it (round 2 F1)",
            paths.command(*paths.lits(TOOL_NAME, "brief")))
    derived = ledger.round_for_sha(parsed.sha, lineage)
    if derived is None:
        raise Refusal(f"no request in the current lineage binds sha "
                      f"{parsed.sha}, so this verdict answers no round here — "
                      f"a verdict is closed against the request that asked for "
                      f"it, never against a round number supplied beside it",
                      paths.command(*paths.lits(TOOL_NAME, "brief"),
                                    source))
    if round_no is not None and round_no != derived:
        raise Refusal(f"--round {round_no} does not hold the request for sha "
                      f"{parsed.sha}; that request is round {derived}. The "
                      f"round is derived from the SHA and an explicit value "
                      f"may only agree with it (round 1 F1)",
                      paths.command(
                          *paths.lits(TOOL_NAME, "close", "--verdict"),
                          source))
    round_no = derived
    # The other half of the concurrency stopgap, and the half the audit
    # missed: the destruction happens at CLOSE, not at emission. A clean
    # verdict appends `lineage_closed`, and every request still open when it
    # lands is swallowed — never ruled, never refused, gone from the record.
    # So a verdict may only close a lineage it actually rules on.
    unruled = _unruled_open_requests(ledger, round_no, parsed.sha, lineage)
    if unruled:
        raise Refusal(
            f"this verdict rules round {round_no} for {parsed.sha[:12]}, and "
            f"this lineage holds an open request it does not rule — "
            f"{_open_request_evidence(unruled)}. Recording it would close "
            f"that request by omission: it would be neither answered nor "
            f"refused, which is the one outcome the record exists to "
            f"prevent",
            "",
            remedy=f"a person decides what becomes of the unruled round: it "
                   f"is ruled by its own verdict first, or the lineage is "
                   f"ended by recorded decision with "
                   f"`{_close_lineage_command()}` — which states that the "
                   f"open request was abandoned, and by whom. A verdict is "
                   f"not that decision")
    digest = _digest_text(verdict_text)
    conflict = verdict_conflict(ledger, round_no, digest, lineage)
    if conflict is not None:
        raise Refusal(f"round {round_no} is already ruled by a different "
                      f"verdict (recorded digest "
                      f"{conflict.get('source_digest', '?')[:16]}…); a round "
                      f"is ruled once, and a second ruling would let the "
                      f"author choose which one to answer (round-2 F1)",
                      paths.command(paths.Lit(TOOL_NAME), paths.Lit("ledger"),
                                    paths.Lit("report")))
    size = len(verdict_text.encode("utf-8"))
    # Kept under the lineage the verdict rules on — BEFORE a clean close
    # appends the marker that opens the next one.
    kept = keep_bytes(cfg, round_no, "verdict", verdict_text,
                      lineage=ledger.carrier_lineage_for_sha(
                          parsed.sha, prefer=lineage) or lineage)
    added = ledger.add_all(verdict_events(
        parsed, round_no, digest, size, tokens,
        answering=ledger.standing_dispositions(lineage,
                                               round_no=round_no - 1)),
        lineage=lineage)
    clean = parsed.verdict == "clean to advance"
    record = {"round": round_no, "sha": parsed.sha, "verdict": parsed.verdict,
              "findings": len(parsed.findings), "closures": len(parsed.closures),
              "kept": kept, "digest": digest, "events_added": added}
    if clean:
        ledger.add({"event": Ledger.LINEAGE_CLOSED, "at_round": round_no,
                    "sha": parsed.sha, "outcome": "clean",
                    "reason": f"clean verdict on {parsed.sha} at round "
                              f"{round_no}", "authorized_by": "reviewer"},
                   lineage=lineage)
        record["lineage"] = ("closed: clean at the exact reviewed SHA. Merge "
                             "authority is the account allowlist and the "
                             "agents' standing rules; this tool does not "
                             "merge (§9bis.2)")
        record["next"] = None
    else:
        record["lineage"] = "open"
        # F1 (lineage 6 round 1): the old value here was the finding's live
        # exhibit — `respond … <dispositions.json> --out <disposition.md>;
        # then loupe handoff` in the top-level runnable field, with `then`
        # posing as a shell word and two files the author has not written
        # yet. A `next` is a command an agent runs verbatim; a command with
        # placeholders is a person's to finish, so it travels as prose and
        # `next` is honestly empty until the dispositions exist.
        record["next"] = None
        respond_template = paths.command(
            *paths.lits(TOOL_NAME, "respond", "--verdict"), source,
            paths.Lit("--from-json"), paths.Ph("<dispositions.json>"),
            paths.Lit("--out"), paths.Ph("<disposition.md>"))
        record["then"] = (
            f"write one disposition per finding, then "
            f"`{respond_template}`, then "
            f"`{paths.command(*paths.lits(TOOL_NAME, 'handoff'))}`")
    return _runnable(record, "next")


def waive(cfg: Config, ledger: Ledger, sha: str, reason: str, by: str,
          git=None) -> dict:
    """Record that a commit was deliberately NOT reviewed.

    Nothing in this tool ever gated a commit, so skipping a review has always
    been possible: you simply do not run `handoff`. What was missing is the
    other half — the ledger could not tell "we decided this did not need a
    review" from "nobody thought about it". Those are the two states this
    whole design exists to keep apart, and the one place it had not applied
    the rule to itself was the decision to use it at all.

    So a waiver is an EVENT, not an absence. It names the commit, the reason,
    and who authorized it, and the report counts waived commits beside
    reviewed ones — which makes "how much of this repository went unreviewed,
    and why" a question with an answer instead of a silence.

    Three things it deliberately is not. It does not authorize anything: the
    commit was already permitted, and this records a decision rather than
    granting one. It does not touch the round lineage — a waiver is not a
    round, has no findings and no verdict, and cannot close anything. And it
    is not something an agent may decide: `by` is the authorizing human, the
    reason is required, and an empty one refuses, because a waiver whose
    reason is blank records that a decision happened and not what it was.
    """
    if not reason.strip():
        raise Refusal(
            "a waiver is a recorded decision and the reason IS the record: "
            "without it the ledger says a review was skipped and cannot say "
            "why, which is the absence this event exists to replace",
            "")
    # Sweep F9: the authorizer is DECLARED, never defaulted. `--by` used to
    # default to `user`, so an agent running this verb permanently recorded
    # a human authorization nobody had declared — the false-actor defect
    # `take` had already removed for the reviewer identity, rebuilt in the
    # one verb whose entire purpose is to document a human decision. The
    # tool cannot observe who ran it; silence appends nothing.
    if not (by or "").strip():
        raise Refusal(
            "no authorizer was declared: a waiver records WHO decided a "
            "review was not needed, and this tool cannot observe that — it "
            "can only carry what is asserted. Skipping a review is not an "
            "agent's decision to take, and `user` is not inferred from "
            "silence",
            "")
    # Round 3 F1 (lineage 12), found by the read audit rather than by
    # review: `rev-parse --verify <sha>^{commit}` PEELS, so a replacement
    # could let a waiver name one commit while recording another — and a
    # waiver is the record that says a commit was deliberately not
    # reviewed, which makes it exactly the wrong place to be wrong.
    run = git or (lambda *a: _git(cfg.repo_root, *a, no_replace=True,
                                  ceiling=git_ceiling(cfg)))
    try:
        resolved = run("rev-parse", "--verify", f"{sha}^{{commit}}")
    except RuntimeError as exc:
        raise Refusal(
            f"{sha} does not resolve to a commit in this repository, so the "
            f"waiver would name nothing",
            paths.command(paths.Lit("git"), paths.Lit("-C"),
                          cfg.repo_root, paths.Lit("log"),
                          paths.Lit("--oneline"), paths.Lit("-5"))) from exc
    # Sweep F10: reviewed and deliberately unreviewed are mutually exclusive
    # facts about a COMMIT, not lineage-local states — so the check reads
    # every request and verdict event in the ledger, closed lineages
    # included, and every imported legacy round. A commit reviewed in a
    # closed lineage was reviewed.
    reviewed = [e for e in ledger.events()
                if e.get("event") in ("request", "verdict")
                and e.get("sha") == resolved]
    if reviewed:
        raise Refusal(
            f"{resolved[:12]} was handed off for review (round "
            f"{reviewed[0].get('round', '?')}, lineage "
            f"{ledger.lineage_of(reviewed[0])}), so it is reviewed rather "
            f"than waived; a waiver may not overwrite a review that "
            f"happened, in this lineage or any closed one",
            paths.command(paths.Lit(TOOL_NAME), paths.Lit("ledger"),
                                    paths.Lit("report")))
    # Deliberately UNKEYED: a waiver is a decision about a COMMIT, not a
    # move within a review (`Ledger.waivers` reads the whole file), so it
    # survives every lineage boundary exactly as identity aliases do.
    added = ledger.add({"event": "waiver", "sha": resolved, "reason": reason,
                        "authorized_by": by})
    return {"sha": resolved, "reason": reason, "authorized_by": by,
            "recorded": bool(added),
            "next": None}


def _finding_records(ledger: Ledger, lineage: str) -> list[dict]:
    """Every finding ruled in `lineage`, newest round first."""
    out = []
    for r in sorted(ledger.rounds(lineage), reverse=True):
        out.extend(ledger.findings_in_round(r, lineage))
    return out


def waive_finding(cfg: Config, ledger: Ledger, ref: str, reason: str, by: str,
                  lineage: str, destination: str = "",
                  trigger: str = "") -> dict:
    """Record a named human's answer to a finding: it stands, unfixed.

    This is the third way a finding can die, and it exists because the other
    two could not express what was happening. A finding dies when the author
    accepts and verifies it, or when the reviewer withdraws it against a
    refutation — neither of which is true when a human reads the finding and
    decides to live with it. `escalated` already ROUTES a finding to a named
    authority; nothing recorded what that authority answered, so the
    escalation either looped or got laundered into a reviewer withdrawal the
    reviewer did not mean. After that laundering the record cannot separate
    "the reviewer found nothing" from "the reviewer found something a human
    waved off", and keeping those apart is the point of the whole loop.

    It is deliberately not a disposition: the author does not get to write
    the authority's answer. It is deliberately not a closure: the reviewer
    did not change their mind. And `by` is asserted rather than observed —
    this buys attribution, never proof that a human took the decision.
    """
    if not reason.strip():
        raise Refusal(
            "overruling a finding is a recorded decision and the reason IS "
            "the record: without it the ledger says a finding was set aside "
            "and cannot say why, which is the silence this event replaces",
            "")
    if not (by or "").strip():
        raise Refusal(
            "no authorizer was declared: this records WHO decided a finding "
            "may stand unfixed, and the tool cannot observe that — it can "
            "only carry what is asserted. Overruling a reviewer is not an "
            "agent's decision to take, and `user` is not inferred from "
            "silence",
            "")
    _refuse_unrepresentable_name(by)
    known = _finding_records(ledger, lineage)
    if not known:
        raise Refusal(
            "the current lineage has no ruled findings, so there is nothing "
            "to overrule",
            paths.command(*paths.lits(TOOL_NAME, "ledger", "report")))
    matched = [e for e in known
               if ref == e.get("fp") or ref == e.get("id")]
    if not matched:
        raise Refusal(
            f"no finding in this lineage is {ref!r}; a waiver naming a "
            f"finding that was never ruled records a decision about nothing",
            paths.command(*paths.lits(TOOL_NAME, "ledger", "report")))
    # A finding id is round-scoped and a fingerprint is not: `F1` in round 2
    # and `F1` in round 3 are different findings that share a label. Refusing
    # rather than picking the newest is the same rule the rest of the tool
    # follows — the identity is computed, never guessed at by a reader.
    fps = {e.get("fp") for e in matched}
    if len(fps) > 1:
        raise Refusal(
            f"{ref!r} names {len(fps)} different findings across this "
            f"lineage's rounds ({', '.join(sorted(f for f in fps if f))}); "
            f"a finding id is round-scoped, so name the fingerprint",
            paths.command(*paths.lits(TOOL_NAME, "ledger", "report")))
    fp = ledger.resolve(matched[0].get("fp", ""))
    # The ruling a waiver answers is the NEWEST one of this identity, and
    # the record binds to its round (lineage 20 round 4 F1): an id names a
    # round-scoped ruling, a fingerprint names an identity, and either way
    # the human is answering the finding as it currently stands.
    finding = ledger.latest_rulings(lineage)[fp]
    # Every answer that bears on this ruling NOW — round-bound, from the
    # one lifecycle derivation. A withdrawal or an acceptance of an EARLIER
    # ruling of the same identity is not an answer to this one (round 4
    # F2), so it neither blocks the waiver nor settles the finding.
    answers = ledger.current_answers(lineage).get(fp, [])
    withdrawn = [e for effect, e in answers
                 if e.get("event") == "closure"]
    if withdrawn:
        raise Refusal(
            f"{finding.get('id')} ({fp}) was already withdrawn by the "
            f"reviewer, so it is answered and needs no authorization",
            "")
    # Round 3 F2: an ACCEPTED finding could still be waived, so the record
    # could say a human let something stand unfixed that the author had
    # already fixed and verified. Both are answers; a second answer over the
    # top of one describes a lineage that did not happen.
    accepted = [e for effect, e in answers
                if effect == vocab.ANSWER_SETTLES
                and e.get("event") == "disposition"]
    if accepted:
        raise Refusal(
            f"{finding.get('id')} ({fp}) was accepted by the author, whose "
            f"falsification record the validator already checked, so it is "
            f"answered and does not stand unfixed",
            paths.command(*paths.lits(TOOL_NAME, "ledger", "report")))
    already = ledger.current_waivers(lineage).get(fp)
    if already is not None:
        raise Refusal(
            f"{finding.get('id')} ({fp}) is already overruled by "
            f"{already.get('authorized_by')!r}: {already.get('reason')}. "
            f"One finding takes one answer — a second would let one reason "
            f"hide another",
            "")
    # Whether the author put this finding to the authority, or the authority
    # reached for it unprompted. Both are legitimate and they are different
    # facts, so the record carries which one happened rather than leaving a
    # reader to assume the tidier of the two.
    escalated = any(
        d.get("disposition") == "escalated"
        and ledger.resolve(d.get("fp", "")) == fp
        for d in ledger.standing_dispositions(lineage))
    event = {"event": vocab.FINDING_WAIVER_EVENT, "fp": fp,
             "finding_id": finding.get("id"), "round": finding.get("round"),
             "severity": finding.get("severity"),
             "title": finding.get("title"),
             "reason": reason, "authorized_by": by,
             "answers_escalation": escalated}
    if destination.strip():
        event["destination"] = destination.strip()
    if trigger.strip():
        event["trigger"] = trigger.strip()
    added = ledger.add(event, lineage=lineage)
    return {"fp": fp, "finding_id": finding.get("id"),
            "severity": finding.get("severity"), "title": finding.get("title"),
            "reason": reason, "authorized_by": by,
            "answers_escalation": escalated,
            "destination": event.get("destination"),
            "trigger": event.get("trigger"),
            "recorded": bool(added),
            "next": paths.command(*paths.lits(TOOL_NAME, "authorize-advance"))}


_AUTHORIZER_NAME = re.compile(vocab.AUTHORIZER_NAME_RE)


def _refuse_unrepresentable_name(by: str) -> None:
    """An authorizer name the wrapper cannot carry is refused BEFORE any
    record is written (lineage 20 round 4 F4). `by` is stamped into a
    double-quoted attribute with no escape, so `Alice "The Decider"` and
    `Alice > Bob` were accepted, recorded, and then emitted as bytes the
    validator refused — an advance reported as success with no usable
    artifact. The grammar is `vocab.AUTHORIZER_NAME_RE`, the same one the
    validator judges the stamp by, so the two cannot disagree."""
    if not _AUTHORIZER_NAME.match(by or ""):
        bad = sorted({c for c in (by or "") if not _AUTHORIZER_NAME.match(c)})
        raise Refusal(
            f"the authorizer {by!r} carries {bad!r}, and an authorizer is "
            f"{vocab.AUTHORIZER_NAME_WANT}: the authorization stamps this "
            f"name into a quoted wrapper attribute, which has no escape, so "
            f"a name the wrapper cannot carry is refused here rather than "
            f"recorded and then emitted as an artifact the validator "
            f"rejects",
            "")


def authorize_advance(cfg: Config, ledger: Ledger, reason: str, by: str,
                      lineage: str) -> dict:
    """Advance a lineage over findings a human has overruled.

    It emits its OWN envelope kind rather than deriving a clean verdict, and
    that is the whole design. A derived clean state would make an overridden
    review indistinguishable from one where the reviewer found nothing — at
    exactly the moment the difference matters — and would let an
    unverifiable claim about a human's decision turn into a merge. This
    stamps the claim instead: the artifact says who advanced it, why, and
    every finding still open, so the approval it can carry says the same.

    It is a lifecycle READ-then-WRITE — `open_round` below, the
    `lineage_closed` append at the end — so `cmd_authorize_advance` holds
    the `LineageReservation` around the whole of it, from before this
    function is entered until after its last append (lineage 27 round 2
    F1). The exclusion is the caller's because the reservation is the
    COMMAND's: the same reason `handoff` and `close` take it there.
    """
    if not reason.strip():
        raise Refusal(
            "advancing over open findings is a recorded decision and the "
            "reason IS the record", "")
    if not (by or "").strip():
        raise Refusal(
            "advancing over open findings is a NAMED human's decision; who "
            "took it is not inferred from silence", "")
    _refuse_unrepresentable_name(by)
    # Round 3 F2: an advance is about a RULING, so it may not be taken while
    # a newer request is awaiting one — closing at the older ruled SHA would
    # bind the authorization to a commit the loop has already moved past.
    pending = ledger.open_round(lineage)
    if pending is not None:
        raise Refusal(
            f"round {pending} is emitted and awaiting a verdict: an advance "
            f"binds to a ruling, and authorizing the previous one now would "
            f"record a decision about a commit this lineage has moved past",
            paths.command(*paths.lits(TOOL_NAME, "close", "--verdict")))
    verdicts = [e for e in ledger.current(lineage)
                if e.get("event") == "verdict"]
    if not verdicts:
        raise Refusal(
            "this lineage has no recorded verdict, so there is no ruling to "
            "advance past",
            paths.command(*paths.lits(TOOL_NAME, "close", "--verdict")))
    last = max(verdicts, key=lambda e: e.get("round", 0))
    if last.get("verdict") == vocab.VERDICT_CLEAN:
        raise Refusal(
            f"round {last.get('round')} is already {vocab.VERDICT_CLEAN!r}: "
            f"the clean verdict is the artifact that advances it, and an "
            f"authorization beside one would claim a human overruled "
            f"findings that no longer stand",
            "")
    # Round 3 F2: the authorization is DERIVED from the lifecycle authority,
    # never from the mere existence of waiver events. Asking only "has any
    # finding been overruled?" let a single waiver advance a lineage with
    # other findings still standing, and emit an envelope naming one of them
    # while the rest died of omission — the death the symmetry rule forbids,
    # reintroduced by the mechanism built to make overrides visible.
    standing = ledger.standing_findings(lineage)
    if not standing:
        raise Refusal(
            "no finding in this lineage is still open, so there is nothing "
            "for a human to authorize: a clean verdict is the artifact that "
            "advances a lineage nobody had to overrule",
            paths.command(*paths.lits(TOOL_NAME, "handoff")))
    # Only a waiver answering the NEWEST ruling of an identity answers it
    # (round 4 F1): one recorded against an earlier ruling is stale, and an
    # authorization built on it would carry a decision taken before the
    # current finding existed, under that older finding's id.
    waived = ledger.current_waivers(lineage)
    unanswered = [f for f in standing
                  if ledger.resolve(f.get("fp", "")) not in waived]
    if unanswered:
        named = ", ".join(f"{f.get('id')} ({f.get('severity')})"
                          for f in unanswered)
        raise Refusal(
            f"{len(unanswered)} finding(s) are still open and unanswered: "
            f"{named}. An advance names EVERY finding it advances over — a "
            f"partial authorization would let the ones it does not name die "
            f"of omission, which is the death this record exists to prevent",
            paths.command(*paths.lits(TOOL_NAME, "waive", "--finding")))
    # What the finding IS comes from the standing ruling; what was decided
    # about it comes from the waiver. The waiver also recorded the ruling's
    # facts as it saw them, but the artifact enumerates the standing set,
    # so the set is where its display facts are read from.
    records = []
    for ruling in sorted(standing,
                         key=lambda f: ledger.resolve(f.get("fp", ""))):
        fp = ledger.resolve(ruling.get("fp", ""))
        waiver = waived[fp]
        record = {"finding_id": ruling.get("id"), "fp": fp,
                  "severity": ruling.get("severity"),
                  "title": ruling.get("title"),
                  "reason": waiver.get("reason"),
                  "by": waiver.get("authorized_by")}
        for k in ("destination", "trigger", "answers_escalation"):
            if waiver.get(k) is not None:
                record[k] = waiver.get(k)
        records.append({k: v for k, v in record.items() if v is not None})
    sha = last.get("sha", "")
    round_no = int(last.get("round", 0))
    envelope = wire.emit_authorization(
        cfg.wrapper_tag, sha=sha, round_no=round_no, lineage=lineage,
        by=by, reason=reason, waived=records)
    # The artifact is judged by the tool's own validator BEFORE anything is
    # kept or recorded (round 4 F4). An advance that reports success and
    # closes the lineage on bytes `loupe validate` then refuses leaves no
    # usable authorization to carry the decision — and no emitter drift,
    # now or later, may commit that terminal state.
    defects = [i for i in validate.validate_authorization(
        wire.parse_authorization(envelope), cfg) if i.level == "error"]
    if defects:
        raise Refusal(
            "the emitted authorization does not validate ("
            + "; ".join(f"{i.code}: {i.message}" for i in defects)
            + "); nothing was kept or recorded, because an advance whose "
            "artifact the tool's own validator refuses is not an advance",
            "")
    kept = keep_bytes(cfg, round_no, "authorization", envelope,
                      lineage=lineage)
    ledger.add({"event": vocab.ADVANCE_EVENT, "at_round": round_no,
                "sha": sha, "reason": reason, "authorized_by": by,
                "waived": len(records)}, lineage=lineage)
    ledger.add({"event": Ledger.LINEAGE_CLOSED, "at_round": round_no,
                "outcome": "authorization", "reason": reason,
                "authorized_by": by, "open_request": False},
               lineage=lineage)
    return {"sha": sha, "round": round_no, "lineage": lineage,
            "authorized_by": by, "reason": reason,
            "waived": [r.get("finding_id") for r in records],
            "envelope": envelope, "kept": str(kept), "next": None}


def close_lineage(ledger: Ledger, reason: str, by: str,
                  lineage: str) -> dict:
    rounds = ledger.rounds(lineage)
    if not rounds:
        raise Refusal("the current lineage has no rounds: nothing to close",
                      paths.command(*paths.lits(TOOL_NAME, "handoff")))
    if not reason.strip():
        raise Refusal("a lineage is closed by a recorded, reason-bearing "
                      "decision — the reason is the record",
                      "")
    if not (by or "").strip():
        # Sweep F9's class: the same silent `user` default lived here.
        raise Refusal("a lineage is closed by a NAMED human's decision; "
                      "who took it is not inferred from silence",
                      "")
    last = max(rounds)
    open_request = last not in ledger.completed_rounds(lineage)
    added = ledger.add({"event": Ledger.LINEAGE_CLOSED, "at_round": last,
                        "outcome": "decision", "reason": reason,
                        "authorized_by": by, "open_request": open_request},
                       lineage=lineage)
    # Workshop (b): the only record-returning transport verb that was not
    # checked here, while `record_handoff`, `take` and `close_round` all
    # were. Its `next` was rendered correctly; nothing made it stay that way.
    # F1 (lineage 6 round 1): `handoff --base <sha>` carries a placeholder
    # only a person can fill — the base of a lineage that does not exist
    # yet — so it is prose, and `next` is honestly empty.
    return _runnable(
        {"lineage_closed_at_round": last, "open_request": open_request,
         "lineage": str(lineage),
         "recorded": bool(added),
         # The next handoff MINTS an id; there is no number to predict, and
         # naming one would be an invented fact (brief `keyed-lineage`).
         "next_lineage": None,
         "next": None,
         "then": f"the next handoff opens round 1 of a new lineage: "
                 f"`{paths.command(*paths.lits(TOOL_NAME, 'handoff', '--base'), paths.Ph('<sha>'))}`"},
        "next")

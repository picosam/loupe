"""Request emission (design §9bis.3): derive, never ask.

Base, head, branch, roles, round and diff shape are all machine-derived —
the round-1 defects (hand-counted diff shape, missing wrapper, missing
taxonomy) were all products of hand-typing an envelope, and this module is
the mechanism that ends that. Authored content (the Claim, stop conditions,
hand-back questions) comes from a claim file: it is the author's judgment,
which the tool carries but never invents.
"""
from __future__ import annotations

import concurrent.futures
import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

from types import MappingProxyType
from typing import Mapping

from . import (TOOL_NAME, TOOL_VERSION, env_var, paths, refs,
               shape_identity, tool_identity, vocab, wire)
from .config import (Config, GitCeiling, built_in_git_ceiling, caller_env,
                     git_ceiling)
from .digest import sha256_text
from .ledger import Ledger, render_cross_lineage_md, render_report_md



#: Round 2 F1 of lineage 12 hardened the AUTHORITY read against `git
#: replace`; round 2 F2 found the reference reads were the other half. A
#: manifest digest is evidence about the target's bytes exactly as the
#: config is, and a replacement ref rewrites both — so every read that
#: derives a file reference from the target object graph disables
#: replacement too. Shared spelling with `transport.NO_REPLACE`, and the
#: same reasoning: a git-wide option, evaluated before any ref base.
NO_REPLACE = "--no-replace-objects"


def _git_timeout(argv, ceiling: GitCeiling):
    """The typed refusal for a git subprocess that ran past `ceiling`
    (0.25.0): `transport.GitTimeout`, built here so every door in this
    module raises the one class `main` renders as a blocked exit. `argv`
    is the command as it ran (`TimeoutExpired.cmd`). Function-local import:
    `transport` imports this module's siblings, and reaches for this module
    the same way."""
    from .transport import GitTimeout
    return GitTimeout(argv, ceiling)


def _git(repo_root: Path, *args: str, no_replace: bool = False,
         git_options: tuple[str, ...] = (), env: dict | None = None,
         ceiling: GitCeiling | None = None) -> str:
    # The timeout is §9bis.4's fail-don't-hang rule as much as hygiene: push
    # and ls-remote reach the network, and an unreachable remote must refuse.
    #
    # 0.25.0: the ceiling is the caller's `config.git_ceiling(cfg)` where the
    # caller holds a configuration — `ensure_pushed`'s runner, which carries
    # `commit -a` and `push` and so the repository's own hooks, is the
    # measured case (an adopter's pre-push gate took 133 s against the old
    # hardcoded 120) — and the door's built-in where it holds only a root.
    # The rule is the door's, not the argv's, as for the environment below.
    # The cost, stated where it is paid: a ceiling raised for a slow hook is
    # also raised for an unreachable remote. A timeout is `GitTimeout`, a
    # typed refusal naming the command, the ceiling and where it came from;
    # every other subprocess failure stays the RuntimeError below.
    #
    # Round 4 F3: a timeout is an ADMITTED outcome — this call declares one —
    # and `TimeoutExpired` is not a `RuntimeError`, so it escaped every
    # caller that catches the failure of a git call and left an agent with
    # no `next_kind` and no remedy. So did a missing executable. Both are
    # normalised HERE, at the one place that runs git, rather than at each
    # caller: a reader that can fail in a way its callers cannot name is the
    # defect, not the individual catch that missed it.
    #
    # RVW-T21 D2: the environment is the CALLER's, not the shim's. This
    # runner carries `commit -a` (pre-commit, prepare-commit-msg,
    # commit-msg, post-commit) and `push` (pre-push) on the handoff path,
    # and every ref update can fire reference-transaction. A hook is the
    # repository's own code, the same trust position as a gate, and a real
    # pre-push hook importing a sibling module died of the leaked
    # PYTHONSAFEPATH on 2026-08-29. Applied at the door, so it covers every
    # subcommand this runner will ever carry rather than the ones it does.
    #
    # `git_options` and `env` are the two doors Ld9f75a1de8 round 1 F1
    # needed and neither existed: git-WIDE options (evaluated before the
    # subcommand, which is why they cannot ride in `args`) and an
    # environment DERIVED from the caller's rather than inherited whole.
    # One reader uses them — `generated_at_target`, to shut off the
    # machine-local attribute sources — and both default to today's
    # behaviour, so no other door moves.
    ceiling = ceiling or built_in_git_ceiling()
    try:
        out = subprocess.run(["git", *([NO_REPLACE] if no_replace else []),
                              "-C", str(repo_root), *git_options, *args],
                             capture_output=True, text=True,
                             timeout=ceiling.seconds,
                             env=caller_env() if env is None else env)
    except subprocess.TimeoutExpired as exc:
        raise _git_timeout(exc.cmd, ceiling) from exc
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
            f"{out.stderr.strip()}")
    return out.stdout.strip()


def _git_bytes(repo_root: Path, *args: str,
               no_replace: bool = False) -> bytes:
    """Raw stdout, for content that must be digested as BYTES rather than
    decoded first — `git show <sha>:<path>` over a file this process has no
    business assuming is UTF-8 (round 3 F1).

    `no_replace` carries round 2 F2's guarantee: a digest that describes
    the target's bytes must be taken from the ORIGINAL object graph."""
    # RVW-T21 D2: the caller's environment at every git door, uniformly.
    # This one reads objects and fires no hook today; the rule is the
    # door's, not the subcommand's, so a future caller cannot reintroduce
    # the leak by passing a different argv here.
    # 0.25.0: it holds no configuration, so its ceiling is its built-in —
    # and a timeout is the typed refusal, never a traceback.
    ceiling = built_in_git_ceiling()
    try:
        out = subprocess.run(["git", *([NO_REPLACE] if no_replace else []),
                              "-C", str(repo_root), *args],
                             capture_output=True, timeout=ceiling.seconds,
                             env=caller_env())
    except subprocess.TimeoutExpired as exc:
        raise _git_timeout(exc.cmd, ceiling) from exc
    if out.returncode != 0:
        raise RuntimeError(
            f"a `git` subprocess failed: "
            f"`{paths.command(paths.Lit('git'), *args)}` — "
            f"{out.stderr.decode('utf-8', 'replace').strip()}")
    return out.stdout


#: `_is_ancestor`'s own ceiling. It holds no configuration, so nothing
#: declared reaches it; named so the refusal and the call cannot disagree.
_ANCESTRY_TIMEOUT_S = 60


def _is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    # Round 3 F1: ancestry is a statement about the object graph.
    # RVW-T21 D2: and it is read in the caller's environment, like every
    # other git door here.
    # 0.25.0: a timeout is the typed refusal, not a False — "did not answer
    # in time" is not "is not an ancestor", and it used to be a traceback.
    ceiling = built_in_git_ceiling(_ANCESTRY_TIMEOUT_S)
    try:
        out = subprocess.run(["git", NO_REPLACE, "-C", str(repo_root),
                              "merge-base", "--is-ancestor", ancestor,
                              descendant],
                             capture_output=True, text=True,
                             timeout=ceiling.seconds, env=caller_env())
    except subprocess.TimeoutExpired as exc:
        raise _git_timeout(exc.cmd, ceiling) from exc
    return out.returncode == 0


def _scrub_url(url: str) -> str:
    """Strip userinfo from a remote URL before it is stamped anywhere.

    https remotes can embed credentials (https://user:token@host/...), and
    nothing in an envelope may hold secrets. scp-style git@host: stays: that
    username is transport convention, not a credential.
    """
    return re.sub(r"://[^/@]*@", "://", url)


def next_round(ledger: Ledger, lineage: str) -> int:
    """The round `lineage`'s next emission opens — one authority, used by
    both the emitter and the CLI (which needs it before emission, for the
    commit subject).

    Keyed since 2026-09-06: the round is a fact of ONE lineage, and a
    ledger holding two open ones has two next rounds (brief
    `keyed-lineage`)."""
    verdicts = [e for e in ledger.current(lineage)
                if e.get("event") == "verdict"]
    return max((e["round"] for e in verdicts), default=0) + 1


def _resolve_push_destination(repo: Path, branch: str, remotes: list[str],
                              run) -> tuple[str, str] | None:
    """The exact remote and remote ref a push of `branch` resolves to —
    the ONE derivation both `ensure_pushed` and `check_enforcement` read
    (F2).

    Before the fix, `check_enforcement` picked a remote named `origin`
    on its own, never consulting the branch's upstream; `ensure_pushed`
    already did. With several remotes and no upstream, the two could
    name different destinations — enforcement tested one remote's
    default branch while the push landed on another's, so a branch that
    was the default branch of its actual push target could pass
    preflight whenever a differently-defaulted remote happened to be
    named `origin`.

    `branch`'s own upstream wins when declared. Absent that, the sole
    remote is the only derivable destination; more than one remote with
    no upstream is not derivable and raises, exactly as `ensure_pushed`
    has always refused it (§9bis.3: the tool never invents a decision).
    `remotes` empty returns None — the caller decides what "no remote"
    means for its own refusal.
    """
    if not remotes:
        return None
    upstream = run("for-each-ref",
                   "--format=%(upstream:remotename)\t%(upstream:remoteref)",
                   f"refs/heads/{branch}")
    remote, _, merge_ref = upstream.partition("\t")
    if remote:
        _destination_branch(remote, merge_ref)
        return remote, merge_ref
    if len(remotes) > 1:
        raise RuntimeError(
            f"{branch} has no upstream and {len(remotes)} remotes exist "
            f"({', '.join(remotes)}): the destination is not derivable, "
            f"and the tool never invents a decision (§9bis.3). Set one "
            f"with `{paths.command(*paths.lits('git', 'push', '-u'), paths.Ph('<remote>'), branch)}`, "
            f"then re-run")
    return remotes[0], f"refs/heads/{branch}"


def _destination_branch(remote: str, remote_ref: str) -> str:
    """Return the branch identity carried by an exact push destination.

    Enforcement is about the REMOTE ref that ``ensure_pushed`` will update,
    not the name of the local branch on the left side of that refspec.  Only
    ``refs/heads/<branch>`` has a branch identity that can be compared with a
    remote's default branch.  Anything else is refused instead of being
    silently classified as a safe working-branch destination.
    """
    prefix = "refs/heads/"
    if not remote_ref.startswith(prefix) or not remote_ref[len(prefix):]:
        raise RuntimeError(
            f"push destination {remote}:{remote_ref or '(empty)'} does not "
            f"name refs/heads/<branch>, so its branch identity cannot be "
            f"derived and the approval pathway cannot be checked")
    return remote_ref[len(prefix):]


def _repo_slug(url: str) -> str:
    """`[HOST/]OWNER/REPO` from a remote URL — the explicit `--repo` value.

    PORTED, deliberately, from `bin/loupe-ci-evidence` (2026-09-07, brief
    `ci-attested-gates`): that script is workbench repo config and this is
    the travelling tool, so the tool may not import it. The alternative — a
    gate runner that resolves its CI coordinates differently from the gate
    that reads CI evidence — is two answers to one question, and the whole
    point of asking `gh` with an explicit `--repo` is that ambient state
    (`GH_REPO`, the cwd's inferred remote) must not decide which repository
    an attestation is about. A fork shares commit SHAs with its source, so a
    green run read from the wrong fork looks exactly like this one's.

    Accepts the scp-like (`git@host:owner/repo.git`) and URL
    (`https://host/owner/repo`, `ssh://git@host/owner/repo`) forms, with or
    without userinfo and `.git`. github.com yields the bare `OWNER/REPO`
    `gh` expects; any other host keeps its `HOST/` prefix, which `gh` also
    accepts. A URL with no repository path refuses rather than guessing.
    """
    text = url.strip()
    host_path = None
    m = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://(?:[^@/]*@)?([^/]+)/(.+)$", text)
    if m:
        host_path = (m.group(1), m.group(2))
    else:
        m = re.match(r"^(?:[^@/]*@)?([^/:]+):(.+)$", text)
        if m:
            host_path = (m.group(1), m.group(2))
    if host_path is None:
        raise RuntimeError(
            f"remote URL {_scrub_url(text)!r} has no host/path shape, so no "
            f"repository can be named explicitly")
    host, path = host_path
    host = host.split(":")[0].lower()
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-len(".git")]
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        raise RuntimeError(
            f"remote URL {_scrub_url(text)!r} names no owner/repository pair")
    slug = "/".join(parts[-2:])
    return slug if host == "github.com" else f"{host}/{slug}"


def _ci_target(repo: Path, git=None) -> tuple[str, str]:
    """`(repository slug, remote branch)` — where a push of this branch
    lands, which is the only place a CI run about it can exist.

    The same derivation `ensure_pushed` and `bin/loupe-ci-evidence` use, for
    the same reason: the LOCAL branch name is not necessarily the name the
    branch has on the remote, and `git remote get-url <name>` is the FETCH
    url while `remote.<name>.pushurl` can send the push somewhere else
    entirely. CI runs where the commit LANDED. Git expands `insteadOf` and
    `pushInsteadOf` before answering, so what this reads is already the true
    destination; a remote pushing to several DIFFERENT repositories refuses,
    because no single CI target can be attested for it.

    Every refusal is a `RuntimeError` naming the state, and the caller turns
    it into a not-run attestation: an unresolvable destination is uncaptured,
    which is not clean.
    """
    run = git or (lambda *a: _git(repo, *a))
    branch = run("rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        raise RuntimeError(
            "detached HEAD — a branch is what CI indexes its runs by, and "
            "there is none to ask about")
    remotes = [r.strip() for r in run("remote").splitlines() if r.strip()]
    destination = _resolve_push_destination(repo, branch, remotes, run)
    if destination is None:
        raise RuntimeError(
            "no remote is configured, so nothing was pushed and no CI run "
            "can be about this work")
    remote, remote_ref = destination
    push_urls = [u.strip() for u in
                 run("remote", "get-url", "--push", "--all", remote).splitlines()
                 if u.strip()]
    if not push_urls:
        raise RuntimeError(
            f"{remote} resolves to no push URL, so where the reviewed commit "
            f"landed cannot be established")
    slugs = sorted({_repo_slug(u) for u in push_urls})
    if len(slugs) > 1:
        raise RuntimeError(
            f"{remote} pushes to {len(slugs)} repositories "
            f"({', '.join(slugs)}): the commit lands in more than one place "
            f"and no single CI target can be attested for it")
    return slugs[0], _destination_branch(remote, remote_ref)


# ------------------------------------------------ the hand-off preflight
#
# Brief `handoff-guards-generalized` (2026-09-18). `handoff` commits the
# outstanding tracked work BEFORE any gate runs — and until 0.25.0 pushed it
# before any gate ran too — so a gate can protect the push and the envelope
# and never the commit. Every assumption that ordering rests on has failed
# once: that the dirty paths are the author's (an
# adopter's killed suite left a corrupt-by-design fixture staged), that the
# environment can run the tool at all, that fixtures are gone when their
# process is. The preflight NAMES what it found, before the commit, and
# refuses the two states nobody could mean.

#: A fixture a suite writes into the REAL tree carries this marker ON A LINE
#: OF ITS OWN (comment leaders allowed around it), so the tree can say of
#: itself that a file was never meant to be committed. The tool defines the
#: marker; a repository opts in by writing it. A whole line, never a
#: substring: this file, the changelog and every document that EXPLAINS the
#: marker name it inside a sentence, and a substring rule would have refused
#: the hand-off that introduced it. Assembled from two halves so that not
#: even this assignment is a marker line.
FIXTURE_MARKER = "loupe-fixture: " + "corrupt-by-design"
_FIXTURE_LINE = re.compile(
    rb"^[ \t]*(?:#+|//+|/\*+|<!--+|;+|--+|\*+)?[ \t]*"
    + re.escape(FIXTURE_MARKER.encode("utf-8"))
    + rb"[ \t]*(?:\*+/|--+>)?[ \t]*\r?$", re.M)


def carries_fixture_marker(data: bytes) -> bool:
    return _FIXTURE_LINE.search(data) is not None


class SweepRefused(RuntimeError):
    """The outstanding work holds something this round did not declare.

    Raised from `ensure_pushed` BEFORE the commit — the one refusal on the
    author's side that leaves no commit behind, because its subject is what
    the commit would have swept in. Carries a remedy for the reason
    `AuthorityAbsent` does: a blocked exit has no runnable `next`."""

    def __init__(self, message: str, remedy: str):
        super().__init__(message)
        self.remedy = remedy


#: The empty tree, for a candidate built on an unborn branch.
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _git_raw(repo: Path, *args: str, env=None,
             no_replace: bool = False) -> bytes:
    """`git` in `repo`, stdout as BYTES: paths and blobs are bytes, and a
    text decode here is where a filename would be mangled. `no_replace`
    is passed at every site that dereferences the object graph, so the
    read boundary's audit can see the guard where it applies."""
    out = subprocess.run(["git", *([NO_REPLACE] if no_replace else []),
                          "-C", str(repo), *args],
                         capture_output=True, timeout=120,
                         env={**caller_env(), **(env or {})})
    if out.returncode != 0:
        raise RuntimeError(
            f"a `git` subprocess failed: "
            f"`{paths.command(paths.Lit('git'), *args)}` — "
            f"{out.stderr.decode('utf-8', 'replace').strip()}")
    return out.stdout


def _raw_entries(raw: bytes) -> list[tuple[str, str, str, str]]:
    """(status, path, mode, blob) from `git diff --raw -z --no-renames`:
    `:srcmode dstmode srcsha dstsha status\0path\0`. Lossless: `-z` never
    quotes a path, whatever its bytes; the path is decoded with
    surrogateescape so an undecodable name still round-trips."""
    out = []
    fields = raw.split(b"\0")
    i = 0
    while i + 1 < len(fields) and fields[i]:
        head = fields[i].decode("utf-8", "surrogateescape").split()
        # :100644 100644 <src> <dst> M
        _src_mode, dst_mode, _src, dst, status = head[0][1:], head[1], head[2], head[3], head[4]
        path = fields[i + 1].decode("utf-8", "surrogateescape")
        out.append((status[0], path, dst_mode, dst))
        i += 2
    return out


class CandidateCommit:
    """What `git commit -a` WOULD record, read from git, never predicted.

    W3 F1, F2 and F4 (2026-09-18) were three faces of one mistake: the
    first preflight inferred the commit from `status --porcelain` display
    text and the working tree's bytes. Git quotes a non-ASCII name there
    (F1); `commit -a` records the INDEX for a `skip-worktree` or
    `assume-unchanged` entry and the link TEXT for a symlink, not what the
    working tree shows (F2); and a rename is two paths, not one (F4). The
    same file argues, at RVW-T17, that predictions about a commit have no
    principled end. So this stops predicting:

      1. the repository's index is COPIED to a temporary file, and `git
         add -u` runs against the copy — the exact staging `commit -a`
         performs, honouring every index flag as it will;
      2. `git diff --cached --raw -z --no-renames HEAD` on that copy lists
         every path the commit would change, losslessly, with the blob id
         it would record, a rename as its D and its A;
      3. for each recorded blob, `git cat-file blob <id>` is the bytes the
         commit will hold: the index content, a symlink's link text;
      4. a gitlink (a submodule pointer, mode 160000) has no bytes to
         judge and is reported as such, for the caller to refuse.

    After the real commit, `recorded(head)` reads the commit's own raw
    diff the same way, so the caller can prove the commit holds exactly
    the candidate — status, path, mode and blob id — a hook, a filter or
    an attribute that changed what was recorded is then a refusal with the
    commit named, not a silent gap. Both reads pass
    `--ignore-submodules=none`, so no diff setting can hide a gitlink.
    The real repository's index is never written.
    """

    def __init__(self, repo: Path):
        self.repo = repo

    def _parent(self) -> str | None:
        try:
            return _git_raw(self.repo, "rev-parse", "--verify", "-q",
                              "HEAD^{commit}").decode().strip()
        except RuntimeError:
            return None

    def changes(self) -> list[dict]:
        return _candidate_changes(self.repo, self._parent() or _EMPTY_TREE)

    def recorded(self, head: str) -> list[tuple[str, str, str, str]]:
        return _recorded_changes(self.repo, self._parent_of(head)
                                 or _EMPTY_TREE, head)

    def _parent_of(self, head: str) -> str | None:
        try:
            return _git_raw(self.repo, "rev-parse", "--verify", "-q",
                              f"{head}^{{commit}}^",
                              no_replace=True).decode().strip() or None
        except RuntimeError:
            return None


# The git reads of CandidateCommit live in module functions with names of
# their own: the read boundary's audit keys function source by NAME, and
# `NoCandidate` below shares the method names.

def _candidate_changes(repo: Path, base: str) -> list[dict]:
    index = _git_raw(repo, "rev-parse", "--git-path", "index").decode().strip()
    index_path = Path(index)
    if not index_path.is_absolute():
        index_path = repo / index_path
    with tempfile.TemporaryDirectory(prefix="loupe-candidate-") as tmp:
        scratch = Path(tmp) / "index"
        if index_path.is_file():
            shutil.copy2(index_path, scratch)
        env = {"GIT_INDEX_FILE": str(scratch)}
        _git_raw(repo, "add", "-u", env=env)
        # `--ignore-submodules=none` on BOTH readers (W4 F2): a
        # `diff.ignoreSubmodules` or per-submodule `ignore` setting hides a
        # changed gitlink from `diff`, and two equally filtered reads that
        # agree prove nothing about what the commit holds.
        raw = _git_raw(repo, "diff", "--cached", "--raw", "-z",
                       "--no-renames", "--ignore-submodules=none", base,
                       env=env, no_replace=True)
        out = []
        for status, path, mode, blob in _raw_entries(raw):
            entry = {"status": status, "path": path, "mode": mode,
                     "blob": blob, "bytes": None}
            if status != "D" and mode != "160000":
                entry["bytes"] = _git_raw(repo, "cat-file", "blob", blob,
                                          env=env, no_replace=True)
            out.append(entry)
        return out


def _recorded_changes(repo: Path, parent: str, head: str):
    raw = _git_raw(repo, "diff", "--raw", "-z", "--no-renames",
                   "--ignore-submodules=none", parent, head, no_replace=True)
    return _raw_entries(raw)


class NoCandidate:
    """The stand-in for tests that inject a fake `git` runner: they judge
    reachability, and no scratch tree exists for the preflight to read. It
    reports no change and agrees with any commit, and it is used ONLY when
    a runner was injected; the preflight's own domain is held by the
    real-git tests in `test_handoff_preflight`."""

    def changes(self):
        return []

    def recorded(self, head):
        return None


def in_scope(path: str, scope_paths) -> bool:
    import fnmatch
    for entry in scope_paths:
        if path == entry or (entry.endswith("/") and path.startswith(entry)):
            return True
        if any(ch in entry for ch in "*?[") and fnmatch.fnmatchcase(path,
                                                                    entry):
            return True
    return False


def sweep_preflight(changes: list[dict], scope_paths=None,
                    allow_outside_scope: bool = False) -> dict:
    """What the hand-off's commit would sweep in, judged before it exists,
    from `CandidateCommit.changes()` — the bytes git will record, never the
    working tree's. Returns the record the envelope prints. Refuses,
    committing nothing:

      - a gitlink among the changes: a submodule pointer has no bytes this
        preflight can judge, so it is an indeterminate state and refused by
        name (the author commits it by hand first);
      - a change whose recorded bytes carry `FIXTURE_MARKER` — by its own
        declaration not the author's work. No flag overrides this: the
        remedy is to restore the tree, and a fixture that must be committed
        does not carry the marker;
      - a changed path outside the claim's `scope_paths` — BOTH ends of a
        rename, since each is a change — unless the author passed
        `--allow-outside-scope`, which the request then states.

    A claim with no `scope_paths` sweeps as it always did, and the record
    says `declared: False` so the request can say that too.
    """
    swept = sorted((c["path"], c["status"]) for c in changes)
    gitlinks = [c["path"] for c in changes if c["mode"] == "160000"
                and c["status"] != "D"]
    if gitlinks:
        raise SweepRefused(
            "the outstanding work changes a submodule pointer (a gitlink, "
            "mode 160000): " + ", ".join(sorted(gitlinks))
            + " — a pointer has no bytes this preflight can judge, so the "
              "state is indeterminate and is not swept",
            remedy="commit the submodule change yourself, then re-run. "
                   "Nothing has been committed, pushed or emitted")
    marked = sorted(c["path"] for c in changes
                    if c["bytes"] is not None
                    and carries_fixture_marker(c["bytes"]))
    if marked:
        raise SweepRefused(
            "the outstanding work holds "
            + ("a file" if len(marked) == 1 else f"{len(marked)} files")
            + f" marked `{FIXTURE_MARKER}`: " + ", ".join(marked)
            + " — a fixture a test suite wrote into the real tree and did "
              "not remove (a killed run never reaches its cleanup). A "
              "hand-off commits and PUSHES outstanding tracked work; this "
              "would have been published under the author's envelope",
            remedy="restore the tree: unstage and discard each named path, "
                   "or drop its intent-to-add entry from the index, then "
                   "re-run. Nothing has been committed, pushed or emitted")
    declared = scope_paths is not None
    outside = sorted({p for p, _s in swept if not in_scope(p, scope_paths)}
                     if declared else [])
    if outside and not allow_outside_scope:
        raise SweepRefused(
            f"the outstanding work holds {len(outside)} path(s) outside the "
            f"claim's `scope_paths`: " + ", ".join(outside)
            + " — by the claim's own account they are not this round's "
              "work, and a hand-off would commit and push them with it",
            remedy="commit or restore those paths yourself, or add them to "
                   "`scope_paths` if they ARE this round's work, and re-run; "
                   "`--allow-outside-scope` sweeps them anyway and says so "
                   "on the request's face. Nothing has been committed, "
                   "pushed or emitted")
    return {"swept": [f"{p} ({s})" for p, s in swept],
            "declared": declared, "outside": outside,
            "allowed_outside": bool(outside)}


def environment_report() -> dict:
    """The environment this hand-off ran in, for the request's face.

    REPORTED, never refused here: the one state that cannot author at all —
    an interpreter below the floor — is refused by the package's own first
    statement, before this module can load. What remains are facts a
    reviewer may want and the tool cannot rule on: an interpreter ABOVE the
    declared range, a root identity (a gate has gone red for that alone),
    and whether the `loupe` on PATH is this installation.
    """
    from . import REQUIRES_PYTHON, REQUIRES_PYTHON_BELOW, TOOL_VERSION
    version = sys.version_info[:3]
    in_range = REQUIRES_PYTHON <= version[:2] < REQUIRES_PYTHON_BELOW
    geteuid = getattr(os, "geteuid", None)
    bare = shutil.which(TOOL_NAME)
    bare_version = None
    if bare:
        try:
            done = subprocess.run([bare, "--version"], capture_output=True,
                                  text=True, timeout=20, env=caller_env())
            bare_version = (done.stdout.strip().split() or [None])[-1] \
                if done.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            bare_version = None
    return {"python": ".".join(map(str, version)), "python_in_range": in_range,
            "python_range": (f">={REQUIRES_PYTHON[0]}.{REQUIRES_PYTHON[1]},"
                             f"<{REQUIRES_PYTHON_BELOW[0]}."
                             f"{REQUIRES_PYTHON_BELOW[1]}"),
            "root": (geteuid() == 0) if geteuid else None,
            "bare_tool": bare, "bare_version": bare_version,
            "tool_version": TOOL_VERSION}


def render_preflight_lines(reachability: dict) -> list[str]:
    """The `Swept:` and `Env:` header lines. Both always print once a
    hand-off computed them: a line that appears only when something is off
    teaches nothing about what its absence means."""
    lines = []
    sweep = reachability.get("sweep")
    if sweep is not None:
        if not sweep["swept"]:
            text = "nothing — the hand-off committed no outstanding work"
        else:
            text = (f"{len(sweep['swept'])} path(s) committed by this "
                    f"hand-off (A added, M modified, D deleted, T type "
                    f"changed): " + ", ".join(sweep["swept"]))
            if not sweep["declared"]:
                text += (" — the claim declares no `scope_paths`, so nothing "
                         "held these to a scope")
            elif sweep["outside"]:
                text += (" — OUTSIDE the declared `scope_paths`, swept under "
                         "--allow-outside-scope: "
                         + ", ".join(sweep["outside"]))
            else:
                text += " — all inside the declared `scope_paths`"
        lines.append(f"Swept:  {text}")
    env = reachability.get("environment")
    if env is not None:
        python = (f"python {env['python']}"
                  + ("" if env["python_in_range"]
                     else f" (OUTSIDE the declared {env['python_range']})"))
        who = ("ROOT" if env["root"] else
               "identity unknown" if env["root"] is None else "not root")
        if not env["bare_tool"]:
            bare = f"no `{TOOL_NAME}` on PATH"
        elif env["bare_version"] == env["tool_version"]:
            bare = f"`{TOOL_NAME}` on PATH is {env['bare_version']}, this one"
        else:
            # Public issue #8 (0.26.0): this used to go on to say that "a
            # same-machine reviewer runs that one". That is an inference
            # about ANOTHER process's PATH, and it is false exactly where a
            # harness scopes its own reader into the reviewer's environment
            # (a relay that prepends a staged build) — which is where the
            # line was printed most. The hand-off observes this process;
            # which reader the reviewer ran is a fact `take` observes and
            # records (its tool agreement), so the line states the one and
            # points at the other instead of guessing.
            bare = (f"`{TOOL_NAME}` on PATH is "
                    f"{env['bare_version'] or 'unreadable'}, NOT this "
                    f"{env['tool_version']} (this process's PATH; the "
                    f"reviewer's `take` reports the reader it ran)")
        lines.append(f"Env:    {python}; {who}; {bare}")
    return lines


def _git_timeout_class():
    """`transport.GitTimeout`, for an `except` clause in this module —
    through a call because `transport` is imported function-locally here
    (it reaches for this module the same way)."""
    from .transport import GitTimeout
    return GitTimeout


class _typed_raw_timeout:
    """Type a timeout raised by `_git_raw` — the candidate reader's door,
    which holds only a repository root and lets `TimeoutExpired` escape —
    as the one refusal every other door raises (0.25.0). Its ceiling is
    that door's built-in, so the refusal says `[limits] git_timeout` does
    not reach it. `state` says what the hand-off had done by then."""

    def __init__(self, state: str):
        self.state = state

    def __enter__(self):
        return self

    def __exit__(self, kind, exc, tb):
        if kind is None or not issubclass(kind, subprocess.TimeoutExpired):
            return False
        raise _git_timeout(exc.cmd, built_in_git_ceiling(int(exc.timeout))
                           ).within(self.state) from exc


def ensure_pushed(cfg: Config, head: str | None = None,
                  local_only: bool = False,
                  commit_subject: str | None = None,
                  round_no: int | None = None, git=None,
                  transport: str | None = None,
                  author_flag: str | None = None,
                  reviewer_flag: str | None = None,
                  scope_paths=None,
                  allow_outside_scope: bool = False,
                  candidate=None, before_push=None,
                  base: str | None = None) -> dict:
    """Make the review target fetchable BEFORE emission (§9bis.4, RVW-T7).

    Commits outstanding tracked work, pushes the reviewed branch, and returns
    the reachability record the envelope stamps — with the remote ref value
    OBSERVED via ls-remote after the push, never assumed from the push's exit
    code. Every state that cannot yield a fetchable target refuses loudly,
    because a SHA the reviewer cannot fetch is not a review target and an
    envelope naming one is a false artifact.

    `before_push` (0.25.0, public issue #2: gate before push) is called
    with the record built so far — the commit made, its authority read, its
    roles resolved, its destination derived — after every local refusal and
    before anything leaves the machine: before the push, and before the
    `--local-only` return, which pushes nothing. It may raise to refuse, and
    a refusal there leaves the local commit, if one was made, for the
    author to amend, exactly as `SweepRefused` and `AuthorityAbsent` do.
    The hand-off passes the LOCAL gates here (`cli._emit`); None keeps the
    old order for every other caller.

    `base`, when given, is the review range's other end, and THIS is the
    one place it is resolved (0.25.0 review round 1 F1): ONCE, before this
    function's own commit and push, to the single commit it names at that
    moment (`rev-parse --verify <base>^{commit}` — a tag peels to its
    commit; a range, a tree, an option-shaped or an absent name refuses,
    with nothing committed). So a symbolic or relative base means what it
    meant when the author typed it, before the tool moved anything: `HEAD`
    is the tip before the outstanding work was committed, `HEAD~1` its
    parent, and the branch and its remote-tracking ref are read before the
    commit and the push advance them. The id is handed to `before_push` as
    `record["base"]` — the value every LOCAL gate is told — and returned
    in the record under the same key, and the caller emits, measures and
    validates THAT id, never the expression again: a second resolution
    after the commit named another range than the gates had checked. The
    guarantee stops at the gates the hand-off runs locally (0.25.0 review
    round 2 F2): a CI-attested gate is not told this base — CI runs the
    manifest with none, and the hand-off takes CI's receipt — so its
    evidence binds the target commit but does not prove the review base,
    and a range-sensitive gate must run locally, not `attested_by = "ci"`,
    to receive it.

    `git` is the command runner — `(*args) -> stdout, raising RuntimeError on
    a nonzero exit` — injectable so every refusal state is testable without a
    network or a scratch repository.
    """
    repo = cfg.repo_root
    # 0.25.0: the declared ceiling, at the door. This runner carries `commit
    # -a` (pre-commit, prepare-commit-msg, commit-msg, post-commit) and
    # `push` (pre-push) below — the measured case — but the rule is the
    # door's, as `caller_env`'s is, so no future argv here can fall back to
    # the built-in. Cost: a ceiling raised for a slow hook is also raised
    # for an unreachable remote.
    run = git or (lambda *a: _git(repo, *a, ceiling=git_ceiling(cfg)))

    # RVW-T11 leg 2, and the one place the two declarations can contradict
    # each other. `--local-only` says review is genuinely same-clone;
    # `transport = paste` says the reviewer has no access to this filesystem.
    # Both cannot hold: the reviewer would receive bytes naming a SHA no
    # clone of theirs can fetch, and would discover it at `take`, after the
    # human has already carried the envelope. Refused here, before the
    # commit, because it needs no git state to be false — this is the leg
    # RVW-T11 called unsolved-but-visible, now enforced rather than stamped.
    # Both cross-machine topologies, and one reason: `paste` puts the bytes
    # in a person's hands and `git` puts them on a remote, and neither
    # reviewer can open this filesystem.
    if local_only and transport in (vocab.TRANSPORT_PASTE,
                                    vocab.TRANSPORT_GIT):
        raise RuntimeError(
            f"--local-only declares the target fetchable from no remote, "
            f"while transport={transport!r} declares a reviewer with no "
            f"access to this filesystem: the two cannot both be true, "
            f"and the envelope would bind a SHA that reviewer can never reach "
            f"(§9bis.4, RVW-T11). Push the branch to a remote both sides can "
            f"fetch, or declare transport={vocab.TRANSPORT_PATH!r}")

    branch = run("rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        raise RuntimeError(
            "detached HEAD: the push authorization is bounded to the "
            "reviewed branch, and a detached HEAD has none (§9bis.4). Check "
            "out a branch, then re-run emit-request")

    # A --head override must already be the branch tip: the push publishes
    # the branch, and an envelope may not bind a target the stamped push did
    # not carry. Checked before the commit step so refusal has no side
    # effects.
    if head is not None:
        named = run("rev-parse", head)
        if named != run("rev-parse", "HEAD"):
            raise RuntimeError(
                f"--head {head} resolves to {named}, which is not the tip of "
                f"{branch}: the push publishes the branch tip, so an older "
                f"target would stamp a remote ref the wrapper does not bind "
                f"(§9bis.4)")
    # The range's base, resolved ONCE and here, before the commit, for the
    # same reason (0.25.0; review round 1 F1): the LOCAL gates run before
    # the push and are told this id (a CI-attested gate is told none —
    # round 2 F2), the emission binds this id, and a base that does not
    # name exactly one commit refuses with nothing committed.
    # `--verify` refuses what resolves to several lines (a range, an
    # option-shaped value) and the `^{commit}` peel refuses a tree and
    # turns an annotated tag into the commit a range can end at.
    resolved_base = None
    if base is not None:
        try:
            resolved_base = run("rev-parse", "--verify", f"{base}^{{commit}}")
        except _git_timeout_class():
            raise
        except RuntimeError as exc:
            raise RuntimeError(
                f"the review base {base!r} does not name one commit here, "
                f"so no range can be gated or emitted from it: nothing has "
                f"been committed, pushed or emitted. Pass --base a single "
                f"commit (an id, a branch, a tag, or an expression such as "
                f"HEAD~1, read before the hand-off commits anything) ({exc})"
            ) from exc
    # Returned beside the reachability, for the caller to emit, measure and
    # validate — present only when a base was given, so a caller that asked
    # for none gets the record it always got.
    resolved = {"base": resolved_base} if base is not None else {}

    porcelain = run("status", "--porcelain")
    untracked = [ln[3:] for ln in porcelain.splitlines()
                 if ln.startswith("??")]
    tracked = [ln for ln in porcelain.splitlines()
               if ln.strip() and not ln.startswith("??")]
    if untracked:
        raise RuntimeError(
            "untracked files present: " + ", ".join(sorted(untracked)) +
            " — add them or ignore them; the tool cannot decide which "
            "(§9bis.4: an untracked file is neither committed work nor "
            "ignorable noise until the author says so)")

    # The preflight, BEFORE the commit: what `commit -a` would record, read
    # from a temporary index (CandidateCommit), against what the claim
    # declared. It refuses with nothing committed. With an injected fake
    # runner there is no tree to read, and `NoCandidate` says so.
    if candidate is None:
        candidate = CandidateCommit(repo) if git is None else NoCandidate()
    with _typed_raw_timeout("Nothing has been committed, pushed or "
                            "emitted."):
        changes = candidate.changes()
    sweep = sweep_preflight(changes, scope_paths, allow_outside_scope)
    environment = environment_report()

    committed = False
    if tracked:
        subject = commit_subject or (
            f"emit-request: outstanding work for round {round_no}"
            if round_no is not None else "emit-request: outstanding work")
        try:
            run("commit", "-a", "-m", subject)
        except _git_timeout_class() as exc:
            # A commit stopped inside its hooks is stopped HOLDING the index
            # lock (measured: `commit -a` writes the index under
            # `index.lock` before pre-commit runs, and a killed git cannot
            # remove it). Said here, because the re-run the remedy asks for
            # would otherwise meet git's own lock refusal unexplained. The
            # tool does not remove it: another git may hold it legitimately.
            # Asked of git (`--git-path` knows a linked worktree's own
            # index), never inferred; a git that cannot answer leaves the
            # sentence out.
            try:
                lock = Path(run("rev-parse", "--git-path", "index.lock"))
                lock = lock if lock.is_absolute() else repo / lock
                lock = str(lock) if lock.exists() else ""
            except RuntimeError:
                lock = ""
            raise exc.within(
                "The hand-off was committing its outstanding work: nothing "
                "was pushed or emitted, and the branch's newest commit says "
                "whether this one was recorded before `git` was stopped"
                + (f". `git` was stopped holding the index lock and {lock} "
                   f"remains; `git` refuses to write the index until a "
                   f"person removes it, once no `git` process is running"
                   if lock else "")) from exc
        committed = True

    target = run("rev-parse", "HEAD")

    # The commit exists; prove it holds EXACTLY the candidate the preflight
    # judged — same paths, same statuses, same blob ids. A pre-commit hook,
    # a clean filter or an attribute can change what `commit -a` records
    # between the read above and the commit; git offers no principled end
    # to that list (RVW-T17), so the artifact is read back instead. A
    # difference is refused with the commit named: nothing is pushed or
    # emitted, and the commit is the author's own to amend.
    if committed:
        with _typed_raw_timeout(
                f"The hand-off committed {target[:12]} locally; nothing "
                f"was pushed or emitted."):
            recorded = candidate.recorded(target)
        if recorded is not None:
            # (status, path, MODE, blob): the mode is a tree-entry fact git
            # records and a hook can change alone (W4 F4).
            expected = {(c["status"], c["path"], c["mode"], c["blob"])
                        for c in changes}
            got = {(s, p, m, b) for s, p, m, b in recorded}
            if expected != got:
                strange = sorted(f"{p} ({s}, mode {m})"
                                 for s, p, m, b in got ^ expected)
                raise SweepRefused(
                    f"the commit {target[:12]} does not record what the "
                    f"preflight judged: " + ", ".join(strange)
                    + " differ — something between the read and the commit "
                      "(a hook, a filter, an attribute) changed what was "
                      "recorded. The commit exists and is not pushed",
                    remedy="inspect the commit, amend or reset it, and "
                           "re-run; nothing has been pushed or emitted")

    # RVW-T17: THE authority boundary, and the only one on the author's
    # side. It is here — after the commit above, before the local-only
    # return and before the push — because everywhere earlier is a
    # PREDICTION. Rounds 7 to 11 of lineage 11 each found a different way
    # to make a prediction wrong (the index against the worktree, a
    # pathname's shape against its bytes, a filter's declaration against
    # its behaviour, git's rendered attribute against the attribute name,
    # a commit hook restaging after the check), and that list has no
    # principled end: git offers arbitrarily many ways to change what a
    # commit records between a check and the commit. A sixth was found
    # while building the domain partition and needs no hook, filter or
    # attribute at all — `git update-index --assume-unchanged review.toml`
    # leaves `status --porcelain` clean, lets every prospective check pass,
    # and then `commit -a` records the INDEX while the author's config was
    # loaded from the WORKTREE.
    #
    # So nothing is predicted. `resolve_authority` reads the artifact —
    # `git show <sha>:review.toml` — which is the identical call `take` and
    # `validate --from-target` make, against the identical input. Whatever
    # a filter, a hook, an attribute or the index did, the commit records
    # something and that something is what is read.
    #
    # The cost is that a local commit may exist before a refusal. That is
    # not a new class: this function already commits AND pushes before the
    # taxonomy refusal below and before the post-emission validation
    # refusal in the CLI, so the boundary here is strictly LESS
    # side-effecting than two refusals the tool already ships. Nothing is
    # pushed and nothing is emitted, and the commit is the author's own to
    # amend.
    #
    # Function-local import: `transport` imports this module's siblings but
    # not this module, and `transport` already reaches for `config` this way
    # twice for the same reason.
    from .transport import AUTHORITY_TARGET, resolve_authority
    governing, origin = resolve_authority(cfg, target, git=git)
    if origin != AUTHORITY_TARGET:
        raise AuthorityAbsent(
            f"the commit under review, {target[:12]}, carries no "
            f"{_config_basename(cfg)} of its own, so the rules a reviewer "
            f"would judge it by live on this machine and cannot be shown to "
            f"another. A reviewed commit carries its own authority "
            f"(configuration in force here: {cfg.source})",
            remedy=f"the author commits {_config_basename(cfg)} to this "
                   f"repository — the same values, in the artifact under "
                   f"review — and re-runs. A user-level copy still governs "
                   f"every local verb; only an emission needs the rules to "
                   f"travel. Nothing has been pushed or emitted")

    # Round 1 F2 (lineage 12). U-1 routed what the envelope RENDERS through
    # the committed authority and left the role STAMPS behind: `resolve_roles`
    # runs before this function, against the checkout, so a no-flag emission
    # stamped the checkout's default author and reviewer while `take` judged
    # them against the target's `permitted_authors`/`permitted_reviewers`.
    # The provenance is what makes the fix expressible — a DEFAULTED role is
    # re-resolved from the authority, an EXPLICIT one is re-validated against
    # it — so the flags are passed in rather than the already-resolved tuple,
    # which cannot say which of the two a value was.
    #
    # Placement is the same argument as the authority read above: after the
    # commit, before the push and before any gate runs, so an identity the
    # target rejects stops with no external side effect.
    effective_roles = resolve_roles(governing, author=author_flag,
                                    reviewer=reviewer_flag)

    remotes = [r for r in run("remote").splitlines() if r.strip()]
    if local_only:
        if remotes:
            raise RuntimeError(
                f"--local-only while remote(s) exist ({', '.join(remotes)}): "
                f"the flag exists for repos with no fetchable surface at "
                f"all, never as a bypass of the push rule (§9bis.4)")
        record = {"state": "local-only", "branch": branch, "sha": target,
                  "committed": committed, "governing": governing,
                  "roles": effective_roles, "sweep": sweep,
                  "environment": environment, **resolved}
        if before_push is not None:
            before_push(dict(record, base=resolved_base))
        return record
    if not remotes:
        raise RuntimeError(
            "no remote configured: a SHA the reviewer cannot fetch is not a "
            "review target (§9bis.4). Add a remote — or, only if review is "
            "genuinely same-clone, re-run with --local-only, which stamps "
            "that state on the envelope's face")

    remote, merge_ref = _resolve_push_destination(repo, branch, remotes, run)

    url = _scrub_url(run("remote", "get-url", remote))

    # Gate before push (0.25.0). Everything above is local and refuses with
    # nothing sent; the caller's check runs here, the last point before the
    # push, and sees the record the push would carry.
    if before_push is not None:
        before_push({"state": "unpushed", "branch": branch, "ref": merge_ref,
                     "remote": remote, "url": url, "sha": target,
                     "committed": committed, "governing": governing,
                     "roles": effective_roles, "sweep": sweep,
                     "environment": environment, "base": resolved_base})

    local = (f"committed {target[:12]} locally" if committed
             else f"made no commit (HEAD is {target[:12]})")
    try:
        # Always an explicit refspec: a push that silently does nothing
        # (push.default surprises) is worse than one that fails (§9bis.4).
        run("push", remote, f"refs/heads/{branch}:{merge_ref}")
    except _git_timeout_class() as exc:
        # Before the generic catch below, which would re-describe a slow
        # pre-push hook as a failed push and drop the one remedy that fits.
        raise exc.within(
            f"The hand-off {local} and emitted nothing; whether {branch} "
            f"reached {remote} is unconfirmed until the remote is asked for "
            f"{merge_ref} (`ls-remote`)") from exc
    except RuntimeError as exc:
        raise RuntimeError(
            f"push of {branch} to {remote} failed — refusing to emit: an "
            f"envelope naming an unfetchable SHA is a false artifact "
            f"(§9bis.4). {exc}") from exc

    observed = ""
    try:
        listing = run("ls-remote", remote, merge_ref)
    except _git_timeout_class() as exc:
        raise exc.within(
            f"The hand-off {local} and the push to {remote} returned, but "
            f"the remote ref could not be observed; nothing was "
            f"emitted") from exc
    for line in listing.splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[1] == merge_ref:
            observed = parts[0]
    if observed != target:
        raise RuntimeError(
            f"after the push, {merge_ref} at {remote} is "
            f"{observed or 'absent'}, not {target}: the stamp is the "
            f"observed remote ref, and it does not carry the target "
            f"(§9bis.4)")

    return {"state": "pushed", "branch": branch, "ref": merge_ref,
            "remote": remote, "url": url, "sha": target,
            "committed": committed, "governing": governing,
            "roles": effective_roles, "sweep": sweep,
            "environment": environment, **resolved}


def diff_shape(repo_root: Path, base: str, head: str) -> dict:
    """Machine-computed diff shape — never hand-counted (round-1 trap #3)."""
    # Round 3 F1: the shape is a measurement of the target's own history.
    numstat = _git(repo_root, "diff", "--numstat", f"{base}...{head}",
                   no_replace=True)
    files, ins, dels, areas = [], 0, 0, set()
    for line in numstat.splitlines():
        a, d, path = line.split("\t", 2)
        files.append(path)
        ins += 0 if a == "-" else int(a)
        dels += 0 if d == "-" else int(d)
        areas.add(path.split("/", 1)[0] if "/" in path else "<root>")
    # The commit count rides with the shape (ruled 2026-08-30, brief
    # review-scope-envelope): a lineage-14 envelope swept thirteen commits
    # of prior-session work while its claim said "one commit", and nothing
    # showed the author the span before the human carried it. Report, not
    # refuse — the ci-evidence precedent: the honest failure was prose the
    # author never checked, and a refusal would only relocate it.
    commits = int(_git(repo_root, "rev-list", "--count", f"{base}..{head}",
                       no_replace=True))
    return {"files": len(files), "insertions": ins, "deletions": dels,
            "changed_lines": ins + dels, "areas": sorted(areas),
            "commits": commits, "file_list": files,
            "entries": _numstat_entries(repo_root, base, head, files)}


def _numstat_entries(repo_root: Path, base: str, head: str,
                     display: list[str]) -> list[dict]:
    """The same rows as `file_list`, carrying each path's RAW spelling and
    its own two counts.

    A SECOND read of the same diff, `-z`, rather than a reshaping of the
    first — because the first read's spellings are what the envelope has
    always printed and are not usable as paths. `--numstat` C-quotes a
    path that is not plain ASCII (`"caf\\303\\251.txt"`) and renders a
    rename as one composite field (`d/{old.json => new.json}`), so neither
    form can be handed to a git command that takes pathspecs. `-z` gives
    the raw bytes and splits a rename into its two paths; the display
    spelling stays exactly as it was, so what the reviewer reads does not
    move.

    `target_path` is the POST-image: the path that exists at the target,
    which is the tree whose attributes classify it. A generated file that
    was renamed into place is generated at the target under its new name,
    and its old name is not a path there at all. `source_path` is that old
    name — a rename's or copy's pre-image, None for every other row. No
    matcher judges it; it is kept because git's tree walk still visits it,
    so a pathspec over its directory selects it (round-3 F1, the `Scoped:`
    line).

    Defensive on the pairing rather than trusting it: the two reads are the
    same diff under the same options, so the rows correspond by position —
    but a disagreement in length means the correspondence is unknown, and
    the honest answer to that is no per-path rows rather than rows zipped
    against the wrong paths.
    """
    raw = _git(repo_root, "diff", "--numstat", "-z", f"{base}...{head}",
               no_replace=True)
    tokens = [t for t in raw.split("\0") if t != ""]
    entries: list[dict] = []
    i = 0
    while i < len(tokens):
        a, d, path = tokens[i].split("\t", 2)
        i += 1
        source = None
        if path == "":
            # A rename or copy: the two paths follow as their own records,
            # old then new.
            if i + 1 >= len(tokens):
                return []
            source, path = tokens[i], tokens[i + 1]
            i += 2
        entries.append({
            "target_path": path,
            "source_path": source,
            "insertions": None if a == "-" else int(a),
            "deletions": None if d == "-" else int(d)})
    if len(entries) != len(display):
        return []
    for entry, shown in zip(entries, display):
        entry["path"] = shown
    return entries


def shape_line(s: dict) -> str:
    return (f"{s['files']} files, {s['insertions']} insertions, "
            f"{s['deletions']} deletions = {s['changed_lines']} changed "
            f"lines, {len(s['areas'])} areas, spanning {s['commits']} "
            f"commit{'' if s['commits'] == 1 else 's'}")


#: The attribute that says a generator wrote this path, not a person. It is
#: the FORGE convention — GitHub collapses a `linguist-generated` file in a
#: diff view — which is why the span reads it rather than taking a key in
#: `review.toml`. A config key is a cross-installation contract that every
#: adopter would have to learn and keep in step with a list they already
#: maintain; the attribute is already in the repository, already travels
#: with the commit, and already means this. Deriving it from a routing map
#: (the brief's proposal) was the other candidate and was rejected for the
#: same reason inverted: `README.md`'s generated column is one workbench's
#: table and reaches no other installation.
GENERATED_ATTR = "linguist-generated"

#: Values `git check-attr` reports that mean the attribute is ON. `set` is
#: the bare form (`path linguist-generated`); `true` is the spelling the
#: forge documents (`path linguist-generated=true`). `unset` (`-path …`),
#: `unspecified` (no rule) and `false` mean OFF, and so does any other
#: value — an unrecognised one is not read as consent.
_GENERATED_ON = frozenset({"set", "true"})

#: Pathspecs per `check-attr` call. A 12,000-line round touched 173 paths;
#: a chunk keeps the argv bounded whatever a future round does, and the
#: audit sees one call site either way.
_ATTR_CHUNK = 256

#: The one environment name this package sets for a git child, and the
#: reason the exception exists: it is the ONLY way to shut off the system
#: attributes file, which no config key relocates. Bound rather than
#: written as a literal subscript key, for the reason `config.caller_env`
#: states about its own two names.
_ATTR_NOSYSTEM = "GIT_ATTR_NOSYSTEM"


def _config_value(text: str) -> str:
    """A path as a git config VALUE: quoted, so `#`, `;` and trailing
    whitespace in it are content rather than syntax."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _isolated_attr_dir(repo_root: Path, workspace: Path) -> Path:
    """A throwaway git directory that reaches the target's objects and
    carries no attribute source of its own.

    Ld9f75a1de8 round 1 F1: `--source=<sha>` selects which TREE
    supplies the TRACKED `.gitattributes`, and does not touch the rest of
    the lookup. Three machine-local sources still reach the answer, and
    MEASURED on git 2.54.0 each one flips this repository's own example —
    `$GIT_DIR/info/attributes` (highest precedence of all, above the tree),
    `core.attributesFile` wherever it is configured, and its default file
    at `$XDG_CONFIG_HOME/git/attributes` or `~/.config/git/attributes`.
    Any of them promotes a handwritten path to "generated, skip by rule" or
    suppresses a declared one, under a heading that says the target tree
    declared it.

    So the lookup runs somewhere that HAS no such sources rather than being
    asked to ignore them:

      * `info/attributes` — the file simply does not exist here, and it is
        the one source no config key disables. The directory is built by
        hand rather than by `git init`, so an init template cannot deposit
        one either.
      * `core.attributesFile` — pinned to an empty file IN this directory,
        by this directory's own config. Repository config outranks global
        and system config, so one line covers a value configured anywhere
        and the default file when it is configured nowhere.
      * the system file — `GIT_ATTR_NOSYSTEM=1` in the child environment,
        set by `generated_at_target`, which also strips every OTHER `GIT_*`
        name the caller had: `GIT_CONFIG_COUNT`/`KEY`/`VALUE` outranks
        repository config and re-opens the very door this closes (measured),
        and `GIT_DIR`, `GIT_COMMON_DIR` and `GIT_WORK_TREE` would move the
        directory out from under all of it.

    What must still WORK is the other half of the claim, and is measured
    the same way: the target's root rules, its nested `.gitattributes` and
    its `[attr]` macros all resolve here exactly as they do unrestricted,
    because they come from the tree `--source` names and nothing else
    changed. `objects/info/alternates` is what makes that tree reachable —
    the real object store is borrowed, never copied, and git follows that
    store's OWN alternates from there, so a `--shared` clone resolves too.
    A linked worktree is the reason the store is asked for by
    `rev-parse --git-path objects` rather than assumed to be
    `<repo>/.git/objects`.

    Raises `RuntimeError` or `OSError` when any of that cannot be
    established; the caller turns either into the conservative answer.
    """
    store = Path(_git(repo_root, "rev-parse", "--git-path", "objects"))
    if not store.is_absolute():
        store = repo_root / store
    store = store.resolve(strict=True)
    if not store.is_dir():
        raise RuntimeError(f"no object store at {store}")
    # A repository whose objects are not SHA-1 needs the scratch directory
    # to say so, or `--source=<sha>` cannot parse the id at all.
    fmt = _git(repo_root, "rev-parse", "--show-object-format")
    if not fmt.isalnum():
        raise RuntimeError(f"unreadable object format {fmt!r}")
    git_dir = workspace / "attributes.git"
    (git_dir / "objects" / "info").mkdir(parents=True)
    (git_dir / "refs").mkdir()
    (git_dir / "objects" / "info" / "alternates").write_text(
        f"{store}\n", encoding="utf-8")
    (git_dir / "HEAD").write_text("ref: refs/heads/none\n", encoding="utf-8")
    empty = git_dir / "no-attributes"
    empty.write_text("", encoding="utf-8")
    lines = ["[core]",
             f"\trepositoryformatversion = {0 if fmt == 'sha1' else 1}",
             "\tbare = true",
             f"\tattributesFile = {_config_value(str(empty))}"]
    if fmt != "sha1":
        lines += ["[extensions]", f"\tobjectformat = {fmt}"]
    (git_dir / "config").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return git_dir


def generated_at_target(repo_root: Path, sha: str,
                        paths: list[str]) -> set[str]:
    """Which of `paths` the TARGET TREE's TRACKED git attributes mark
    generated.

    THE TARGET'S ATTRIBUTES, not this checkout's — `--source=<sha>`, and
    replacement disabled like every other read of a stamped object here.
    Lineage 12 round 3 F1 is why: three separate boundaries of this tool
    were found reading the local object graph where they meant to read the
    target's, one per round, and the working tree is exactly the wrong
    authority for a claim the envelope makes about a commit. A path marked
    generated in the author's working tree but not in the commit is not
    generated in what the reviewer will read; one marked in the commit and
    unmarked since still is.

    And TRACKED is the second half, which `--source` alone does not give:
    the lookup runs against `_isolated_attr_dir`, so `info/attributes`, a
    configured `core.attributesFile`, its default user file and the system
    file cannot add, remove or alter what the target declared. That is what
    lets the heading keep saying the declaration is in the target tree's
    attributes — it now is. The whole environment for the call is derived
    from the caller's rather than inherited: every `GIT_*` name is dropped,
    because several of them reopen exactly what the scratch directory
    closes, and `GIT_ATTR_NOSYSTEM=1` is the only one set.

    `paths` are RAW post-image spellings (`entries[*]["target_path"]`), and
    the returned set is in the same spelling. `-z` on the way back too, so
    a path with spaces or non-ASCII bytes survives the round trip
    unquoted.

    Degrades to "none" rather than refusing, and NEVER to a partial answer:
    if the isolation cannot be established — no scratch directory, an
    object store that cannot be resolved, a git too old for `--source`
    (it reached git in 2.40) — every path stays in the ordinary list and
    the span says nothing about generated paths, which is exactly how it
    rendered before any of this existed. There is no third rendering in
    which some sources were shut off and the heading hedges about the rest.
    The classification is advisory — it tells a reviewer what they may skip
    by rule, and it authorises nothing — so a git that cannot answer costs
    a convenience, not a check.
    """
    if not paths:
        return set()
    marked: set[str] = set()
    try:
        workspace = Path(tempfile.mkdtemp(prefix="loupe-attrs-"))
    except OSError:
        return set()
    try:
        git_dir = _isolated_attr_dir(repo_root, workspace)
        env = {name: value for name, value in _caller_env().items()
               if not name.startswith("GIT_")}
        env[_ATTR_NOSYSTEM] = "1"
        for start in range(0, len(paths), _ATTR_CHUNK):
            chunk = paths[start:start + _ATTR_CHUNK]
            out = _git(repo_root, "check-attr", "-z", f"--source={sha}",
                       GENERATED_ATTR, "--", *chunk, no_replace=True,
                       git_options=(f"--git-dir={git_dir}",), env=env)
            fields = out.split("\0")
            # Triples: path, attribute, value. A trailing empty field is
            # the last NUL; anything else short of a whole triple means the
            # output is not what this reader was written against.
            for i in range(0, len(fields) - 2, 3):
                if fields[i + 2].strip().lower() in _GENERATED_ON:
                    marked.add(fields[i])
    except (RuntimeError, OSError):
        return set()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    return marked


def _generated_span(shape: dict, generated: set[str]) -> tuple[str, list[str]]:
    """The `What changed` span's two halves, given the classification.

    Returns the header's suffix and the lines that follow the ordinary
    listing. Both are EMPTY when nothing is marked, and that is the whole
    compatibility claim: a repository that marks no path renders the span
    it has always rendered, byte for byte, with no heading, no suffix and
    the same file list in the same order.

    The totals do not move. `shape_line` keeps counting every changed path,
    because the span is what the round actually touched and a reviewer
    deciding whether the claim matches the diff needs that number whole.
    What the suffix adds is the OTHER total — the part that is not
    generated — so the two are read together rather than one standing in
    for the other.
    """
    entries = shape.get("entries") or []
    marked = [e for e in entries if e["target_path"] in generated]
    if not marked:
        return "", []
    plain = [e for e in entries if e["target_path"] not in generated]
    lines = sum((e["insertions"] or 0) + (e["deletions"] or 0)
                for e in plain)
    of_those = (f"{len(marked)} of those paths carries"
                if len(marked) == 1 else
                f"{len(marked)} of those paths carry")
    suffix = (f"; {of_those} `{GENERATED_ATTR}` in the target tree's "
              f"attributes, leaving {len(plain)} file"
              f"{'' if len(plain) == 1 else 's'} and {lines} changed lines "
              f"that do not")
    rows = []
    for e in marked:
        if e["insertions"] is None or e["deletions"] is None:
            count = "binary"
        else:
            count = f"+{e['insertions']} -{e['deletions']}"
        rows.append(f"  {e['path']}   ({count})")
    return suffix, [
        "",
        f"Generated by declaration — these carry `{GENERATED_ATTR}` in the "
        f"target tree's attributes, so a reviewer may skip them by rule "
        f"rather than by inspection:",
        "",
        *rows]


def _tool_identity(repo_root: Path, argv0: str) -> str:
    """Content identity of the executable a gate actually invoked (§5.1).

    §5.1 asks each attestation for a "tool version". A version string is what
    a tool chooses to say about itself; the digest of the file that ran is
    what it is. Where the executable resolves, this is therefore strictly
    stronger than the field the design asked for, and where it does not, it
    says `unresolved` rather than guessing a version.
    """
    resolved = shutil.which(argv0, path=os.environ.get("PATH")) or ""
    if not resolved or not Path(resolved).is_absolute():
        # `which` echoes a path-like argv0 back unchanged, so a repo-relative
        # gate command resolves against the repo rather than the caller's cwd.
        candidate = repo_root / (resolved or argv0)
        resolved = str(candidate) if candidate.is_file() else ""
    if not resolved:
        return f"{argv0} -> unresolved"
    try:
        digest = hashlib.sha256(Path(resolved).read_bytes()).hexdigest()[:16]
    except OSError as exc:
        return f"{argv0} -> {resolved} (unreadable: {exc.strerror})"
    return f"{argv0} -> {resolved} sha256:{digest}"


#: The name of the symlink each per-SHA directory keeps pointing at its
#: most recent run. Retained output is now per RUN, so "the log for this
#: gate at this commit" is no longer a single path; this is where a person
#: following that instinct lands.
NEWEST_RUN = "newest"


def run_token(when: float | None = None) -> str:
    """A directory name for ONE gate run: UTC to the second, then six hex
    characters so two runs in the same second cannot collide. Time-ordered,
    so `ls` in a per-SHA directory reads oldest to newest."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ",
                          time.gmtime(time.time() if when is None else when))
    return f"{stamp}-{os.urandom(3).hex()}"


def _point_at_newest(sha_dir: Path, run: str) -> None:
    """Repoint `<sha>/newest` at this run, atomically and best-effort.

    Best-effort because the pointer is a convenience and the attestation
    already carries the exact path: a filesystem that cannot make a symlink
    loses the shortcut, never the evidence.
    """
    link = sha_dir / NEWEST_RUN
    tmp = sha_dir / f".{NEWEST_RUN}.{os.getpid()}"
    try:
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        os.symlink(run, tmp, target_is_directory=True)
        os.replace(tmp, link)
    except (OSError, NotImplementedError, AttributeError):
        try:
            tmp.unlink()
        except OSError:
            pass


def _retain_output(cfg: Config, executed_sha: str, gate_id: str,
                   blob: str, run: str) -> dict:
    """Digest the gate's full output and point at the retained copy (§5.1).

    Output is retained OUTSIDE the repo, under the ledger directory, because
    that is where this process's state lives (§4) and a review must not add
    bytes to a tree it is reviewing. The digest is computed either way, so a
    failure to retain degrades the pointer without losing the identity.

    RETAINED PER RUN, not per commit (`concurrent-gates-single-flake`,
    2026-09-03). The path was `<sha>/<id>.log`, so a second run at the same
    commit overwrote the first in place, last writer winning. That destroyed
    the only copy of the thing worth keeping twice — measured 2026-09-02: a
    handoff refused on a failing gate, the natural next act was to re-run
    it, the re-run passed, and the retained log a person then opened was the
    PASSING run's. Each run now writes under its own `<sha>/<run>/`
    directory, so an attestation's pointer names bytes nothing later
    rewrites, and `<sha>/newest` is repointed for whoever wants the last
    one. Prune semantics are untouched: the pruneable unit is still the
    per-SHA directory, and a run directory goes when its SHA does.
    """
    record = {"sha256": hashlib.sha256(blob.encode("utf-8")).hexdigest(),
              "bytes": len(blob.encode("utf-8"))}
    if cfg.ledger_dir is None:
        record["pointer"] = "not retained (no state directory configured)"
        return record
    target = Path(cfg.ledger_dir) / "gate-output" / executed_sha / run
    try:
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{gate_id}.log"
        path.write_text(blob, encoding="utf-8")
        record["pointer"] = str(path)
        _point_at_newest(target.parent, run)
    except OSError as exc:
        record["pointer"] = f"not retained ({exc.strerror})"
    return record


#: The caller-environment helper used to live here, next to its first
#: consumer. RVW-T21 D2 gave it three consumers — this module, `transport`
#: and `config` itself, since EVERY git subprocess must run in the caller's
#: environment and not the shim's — so it moved to `config`, the one module
#: the other two already import and which imports neither. ONE authority,
#: never a copy; this name stays because the gate-environment record and
#: this module's own callers spell it this way.
_caller_env = caller_env


# ------------------------------------------------------- CI-attested gates
#
# 2026-09-07, brief `ci-attested-gates`, asked by the user. The heavy half of
# this manifest — the suites and the three candidate builds — runs TWICE per
# handoff: once on the author's machine, then again in CI on the push the
# handoff just made, byte for byte the same manifest at the same commit. The
# second run is the one a reviewer can verify; the first is the one that
# spends three minutes of somebody's laptop spawning hundreds of git
# subprocesses. A gate may therefore declare `attested_by = "ci"`, and this
# runner then WAITS for CI's answer at the exact target SHA instead of
# re-executing the command locally.
#
# What is traded, stated rather than discovered: wall time per handoff grows
# (the author waits for a runner instead of for their own CPU) and Actions
# minutes are now on the critical path of every round. Local CPU drops,
# which is what was asked for.

#: The one admitted value of a gate's `attested_by`. A gate is executed by
#: this process or attested by CI; there is no third executor, and the
#: config layer refuses anything else BY NAME rather than defaulting it.
CI_ATTESTER = "ci"

#: GitHub Actions sets this in every step. INSIDE CI the runner is the
#: executor — somebody has to actually run the command, and it is this
#: process — so an attested gate there executes exactly as an unattested one
#: does. A poll from inside the very run that would answer it is a deadlock
#: wearing an attestation's clothes.
CI_ENV = "GITHUB_ACTIONS"

#: How often the Actions API is asked, and how long the wait may last before
#: the attestation is recorded as uncaptured. The interval is a parameter of
#: `run_gates` so a test can drive two polls without sleeping for forty
#: seconds; the timeout is `[limits] ci_timeout`, whose built-in this is.
CI_POLL_INTERVAL_S = 20.0
CI_TIMEOUT_DEFAULT_S = 900

#: What one poll asks for. `gh run list --commit <sha>` is NOT used and must
#: not be: measured on the authoring workstation (gh 2.x, 2026-09) it returns
#: an EMPTY LIST silently for a commit that plainly has runs, which a poller
#: cannot tell apart from "CI has not started yet" and would ride to a
#: timeout every time. The runs come back per branch; the SHA match is made
#: here, where a mismatch is a decision rather than an absence.
CI_RUN_FIELDS = ("databaseId,headSha,status,conclusion,url,workflowName,"
                 "updatedAt")
CI_RUN_LIMIT = "20"

#: THE RECEIPT (round-3 F2, 2026-09-07). The Actions API answers about RUNS,
#: and the first cut of this mechanism read a run's CONCLUSION and assigned
#: it to every declared gate. That was reproduced as a defect rather than
#: argued as one: a stub `gh` returning a single green workflow named
#: `docs-only` at the target produced `exit_code 0`, `binding: bound` for a
#: gate whose command exits 1. A successful workflow is not evidence that
#: THIS gate executed — it may be absent, filtered, still queued, or have
#: declared the gate `--not-run` — and no comment asking two files to stay in
#: step can narrow what the row claims.
#:
#: So CI now emits the evidence itself. `bin/loupe-gates --receipt <path>`
#: writes one row per DECLARED gate — its id, the exact command, the exit
#: code, and the reason it did not run if it did not — and the workflow
#: uploads that file as an artifact named for the commit it is about. The
#: runner downloads it and reads the gate's own row. The workflow/manifest
#: relationship stops being a rule a person must remember and becomes
#: mechanical: the receipt says what ran, per gate, in CI's own words.
#: Round-3 F3: the schema string and the artifact name are the
#: authority's, re-exported here for the callers that already name
#: them — one definition, two spellings of the same fact.
CI_RECEIPT_SCHEMA = vocab.CI_RECEIPT_SCHEMA
CI_RECEIPT_FILENAME = "loupe-receipt.json"


def ci_receipt_artifact(sha: str) -> str:
    """The artifact name the workflow uploads and the runner asks for.

    Named for the COMMIT, not for the run: two runs at one commit answer the
    same question, and an artifact whose name carries the SHA cannot be
    mistaken for one from the previous push even if a listing is stale.
    """
    return vocab.ci_receipt_artifact(sha)


def gate_receipt(target_sha: str, gates: list[dict],
                 records: Mapping[str, dict],
                 declared: Mapping[str, str] | None = None) -> dict:
    """What CI publishes about its own run of the manifest.

    One row per gate in the DECLARED manifest — including the ones declared
    `--not-run`, with their reason. A gate absent from this list is a gate
    the receipt cannot attest, and the reader turns that into a not-run
    record rather than into silence: omission is the shape the old defect
    took, so omission may not read as a pass.

    `command` is carried because a changed command is a different gate. A
    receipt row saying `pytest -x` cannot attest a manifest entry that now
    says `pytest`, and the reader compares them.
    """
    declared = declared or {}
    rows = []
    for gate in gates:
        gate_id = gate["id"]
        command = " ".join(gate["command"])
        if gate_id in declared:
            rows.append({"id": gate_id, "command": command, "exit_code": None,
                         "duration_s": None, "not_run": declared[gate_id]})
            continue
        rec = records.get(gate_id)
        if rec is None or "error" in rec or "exit_code" not in rec:
            reason = (rec.get("error") if isinstance(rec, dict) and rec.get("error")
                      else "no attestation was produced for this gate")
            rows.append({"id": gate_id, "command": command, "exit_code": None,
                         "duration_s": None, "not_run": reason})
            continue
        rows.append({"id": gate_id, "command": rec.get("command", command),
                     "exit_code": rec["exit_code"],
                     "duration_s": rec.get("duration_s"), "not_run": None})
    return {"schema": CI_RECEIPT_SCHEMA, "sha": target_sha,
            "tool_version": f"{TOOL_NAME}/{TOOL_VERSION}", "gates": rows}


def _gh_runs(slug: str, branch: str) -> list[dict]:
    """One poll: the most recent workflow runs on the PUSHED ref.

    `--repo` is explicit because `gh` otherwise resolves the repository from
    ambient state (`GH_REPO` overrides the checkout), and an attestation
    about the wrong fork is indistinguishable from one about this commit —
    forks share SHAs.

    Every failure raises `RuntimeError`: `gh` absent, unauthenticated,
    offline, or answering something that is not a JSON list. Uncaptured is
    not clean, so none of those states may return an empty list.
    """
    argv = ["gh", "run", "list", "--repo", slug, "--branch", branch,
            "--json", CI_RUN_FIELDS, "--limit", CI_RUN_LIMIT]
    printable = paths.command(paths.Lit("gh"), *argv[1:])
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=120, env=caller_env())
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            f"`{printable}` did not run ({type(exc).__name__}: {exc}) — "
            f"a CI-attested gate needs `gh` on PATH") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"`{printable}` failed: "
            f"{proc.stderr.strip() or f'exit {proc.returncode}, no stderr'}")
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"`{printable}` did not answer JSON: {exc}") from exc
    if not isinstance(rows, list):
        raise RuntimeError(
            f"`{printable}` answered {type(rows).__name__}, not a list of runs")
    return [r for r in rows if isinstance(r, dict)]


def _gh(argv: list[str], timeout: int = 180) -> str:
    """One `gh` invocation, every failure a `RuntimeError` naming the state.

    Same contract as `_gh_runs`: absent, unauthenticated, offline and
    non-zero all raise, because a CI-attested gate that cannot ask is
    uncaptured, and uncaptured is not clean. Never returns an empty answer
    for a failed call.
    """
    printable = paths.command(paths.Lit("gh"), *argv[1:])
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, env=caller_env())
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            f"`{printable}` did not run ({type(exc).__name__}: {exc}) — "
            f"a CI-attested gate needs `gh` on PATH") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"`{printable}` failed: "
            f"{proc.stderr.strip() or f'exit {proc.returncode}, no stderr'}")
    return proc.stdout


def _gh_artifact_names(slug: str, run_id) -> set[str]:
    """What one run uploaded. `gh api` rather than `gh run view`, because the
    artifact list is the REST resource and the name is all that is needed."""
    out = _gh(["gh", "api", f"repos/{slug}/actions/runs/{run_id}/artifacts"])
    try:
        doc = json.loads(out or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"the artifact listing for run {run_id} is not JSON: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("artifacts"), list):
        raise RuntimeError(
            f"the artifact listing for run {run_id} has no 'artifacts' array")
    return {a.get("name") for a in doc["artifacts"] if isinstance(a, dict)}


def _read_receipt(directory: Path) -> dict | None:
    """The receipt inside a downloaded artifact, or None.

    `gh run download -n <name>` extracts into the destination directory, but
    whether it nests under the artifact's name has changed across `gh`
    versions, so the file is looked for by name at any depth before any
    other JSON is considered. Sorted, so two files never race to be the
    answer.
    """
    candidates = [directory / CI_RECEIPT_FILENAME]
    candidates += sorted(p for p in directory.rglob(CI_RECEIPT_FILENAME))
    candidates += sorted(p for p in directory.rglob("*.json"))
    for path in candidates:
        if not path.is_file():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(doc, dict) and isinstance(doc.get("gates"), list):
            return doc
    return None


def _ci_fetch_receipt(slug: str, at_sha: list[dict],
                      target_sha: str) -> tuple[dict | None, dict | None, str]:
    """`(receipt, the run that produced it, why not)`.

    Completed runs at the target SHA are asked NEWEST FIRST for the artifact
    named after this commit; the first one carrying a readable receipt ABOUT
    this commit answers. Every rejection is collected into the reason
    string, because "no receipt" is the state that turns five blocking gates
    into not-run records and the author is owed the list of what was asked.
    """
    artifact = ci_receipt_artifact(target_sha)
    completed = [r for r in at_sha if r.get("status") == "completed"]
    completed.sort(key=lambda r: str(r.get("updatedAt") or ""), reverse=True)
    tried: list[str] = []
    for run in completed:
        run_id = run.get("databaseId")
        label = f"run {run_id} ({run.get('workflowName')})"
        try:
            names = _gh_artifact_names(slug, run_id)
        except RuntimeError as exc:
            tried.append(f"{label}: {exc}")
            continue
        if artifact not in names:
            tried.append(f"{label} uploaded no {artifact} artifact "
                         f"(uploaded: {sorted(n for n in names if n) or 'nothing'})")
            continue
        with tempfile.TemporaryDirectory(prefix="loupe-receipt-") as tmp:
            try:
                _gh(["gh", "run", "download", str(run_id), "--repo", slug,
                     "-n", artifact, "-D", tmp])
            except RuntimeError as exc:
                tried.append(f"{label}: {exc}")
                continue
            doc = _read_receipt(Path(tmp))
        if doc is None:
            tried.append(f"{label}: the {artifact} artifact carries no "
                         f"readable gate receipt")
            continue
        if doc.get("sha") != target_sha:
            tried.append(f"{label}: its receipt is about {doc.get('sha')!r}, "
                         f"not {target_sha}")
            continue
        return doc, run, ""
    if not completed:
        return None, None, "no completed run at that SHA"
    return None, None, "; ".join(tried)


def _ci_summary(row: dict) -> dict:
    """One run's identity, in this tool's spelling — what makes the
    attestation verifiable by hand rather than believable."""
    return {"id": row.get("databaseId"), "url": row.get("url"),
            "status": row.get("status"), "conclusion": row.get("conclusion"),
            "head_sha": row.get("headSha"), "workflow": row.get("workflowName"),
            "completed": row.get("updatedAt")}


def _ci_decision(rows: list[dict], target_sha: str) -> tuple[str, list[dict]]:
    """`(state, runs at the target SHA)` where state is `success`, `failed`
    or `pending`.

    THE SHA MATCH IS THE WHOLE ATTESTATION. A run on the same branch at a
    different commit says nothing about this one — it is the staleness
    `ci-evidence` reports, not evidence that this commit passed — so a row
    whose `headSha` is not exactly the target is not admitted at any point.

    `failed` as soon as ANY completed run at the SHA concluded other than
    success: a doomed handoff should not wait out the timeout. `success`
    only when every run at the SHA has completed, because a workflow still
    in flight may yet be the one that refuses. That makes an unrelated red
    workflow at this commit refuse a CI-attested gate — the honest limit of
    asking a run-level API about a gate-level fact, and it fails closed.
    """
    at_sha = [r for r in rows if r.get("headSha") == target_sha]
    completed = [r for r in at_sha if r.get("status") == "completed"]
    failed = [r for r in completed if r.get("conclusion") != "success"]
    if failed:
        return "failed", at_sha
    if at_sha and len(completed) == len(at_sha):
        return "success", at_sha
    return "pending", at_sha


def _ci_not_run(gate: dict, reason: str) -> dict:
    """The not-run shape, for every state in which no CI answer was obtained.

    Deliberately NOT a record with a synthesised exit code: a gate nobody ran
    and CI did not attest has produced no evidence, and §5.1's not-run shape
    is the one that says so without carrying run-only fields it cannot back
    (RVW-T2(c)). A blocking gate in this state is still blocking.
    """
    return {"id": gate["id"], "blocking": gate.get("blocking", False),
            "attested_by": CI_ATTESTER, "error": reason}


def _ci_attestation(cfg: Config, gate: dict, target_sha: str, row: dict,
                    summary: dict, receipt: dict, context: dict,
                    run: str) -> dict:
    """A gate row whose executor was CI, carrying the run AND the receipt row
    in which that run recorded THIS gate's execution.

    Every field §5.1 requires is present and means what it says; three of
    them mean it about a runner rather than about this machine, so
    `attested_by` sits beside them and the retained output is the run
    identity and the receipt:

      exit_code     the exit code the RECEIPT recorded for this gate — CI's
                    own answer about this command, not a run conclusion
                    mapped onto it. Round-3 F2: the conclusion belongs to a
                    run, and a run is not a gate.
      command       the receipt's, checked equal to the manifest's before
                    this is built. A changed command is a different gate.
      tool_version  the identity of what ran. This process cannot digest a
                    runner's executable, and claiming a local sha256 for a
                    command that ran elsewhere would be the lie this whole
                    mechanism exists to avoid, so it names the run.
      duration_s    what obtaining the attestation cost HERE — the wait, not
                    the run. The gate's own timing is in `receipt`.

    `binding` is `bound` because the run's head IS the target: CI checked out
    that commit and nothing else, which is a stronger binding than a local
    gate can offer from a tree that may be dirty.
    """
    # Round-3 F3: BUILT from the authority, key by key, so a field cannot
    # enter a record without a declared kind and code — and so the
    # validator is never handed something it was never told about. Three
    # come from the receipt DOCUMENT rather than the gate's row in it, and
    # the artifact is this tool's own name for where the document
    # travelled.
    _document = {"sha", "tool_version", "schema"}
    receipt_row = {
        name: (ci_receipt_artifact(target_sha) if name == "artifact"
               else receipt.get(name) if name in _document
               else row.get(name))
        for name in vocab.CI_RECEIPT_ROW}
    blob = json.dumps({**context, "target_sha": target_sha,
                       "attested_by": CI_ATTESTER, "deciding_run": summary,
                       "receipt": receipt_row,
                       "receipt_gate_ids": [r.get("id") for r in receipt["gates"]
                                            if isinstance(r, dict)]},
                      indent=2, ensure_ascii=False, sort_keys=True)
    return {
        "id": gate["id"],
        "command": row["command"],
        "attested_by": CI_ATTESTER,
        "ci_run": summary,
        "receipt": receipt_row,
        "exit_code": row["exit_code"],
        "tool_version": (f"attested by ci: {context['repository']} "
                         f"{summary['workflow']} run {summary['id']} "
                         f"({summary['conclusion']}), gate {gate['id']} "
                         f"exit {row['exit_code']} per "
                         f"{ci_receipt_artifact(target_sha)}"),
        "runner": f"{TOOL_NAME}/{TOOL_VERSION}",
        "target_sha": target_sha,
        "executed_sha": summary.get("head_sha"),
        "tree": "clean",
        "binding": "bound",
        "duration_s": context["waited_s"],
        "output": _retain_output(cfg, target_sha, gate["id"], blob, run),
        "blocking": gate.get("blocking", False),
    }


def _ci_records(cfg: Config, gates: list[dict], target_sha: str, slug: str,
                at_sha: list[dict], context: dict, run: str,
                state: str) -> dict:
    """Every CI-attested gate's record, decided by CI's RECEIPT rather than
    by a run's conclusion (round-3 F2).

    The table, once, in one place:

      no receipt for this commit in any completed run   not-run, naming what
                                                        was asked
      the receipt has no row for this gate              not-run: "CI ran the
                                                        workflow but not
                                                        this gate"
      the row declares `not_run`                        not-run, quoting CI's
                                                        own reason
      the row's command is not the manifest's           not-run: a changed
                                                        command is a
                                                        different gate
      the row exited non-zero, run not successful       FAILED at that exit,
                                                        with the run URL
      the row exited 0 and the run concluded success    PASS
      any other pairing of row and run                  not-run: the receipt
                                                        and the run disagree

    The last two rows are the biconditional the validator enforces: a CI row
    exists only where `conclusion == "success"` and `exit_code == 0` agree.
    A green gate inside a run that concluded otherwise is therefore NOT a
    pass here — the fail-closed run granularity the brief already declared,
    kept, now with the receipt saying exactly which gate is being refused.
    """
    # Round-3 F1. Receipt selection PRESERVES the run-level state
    # `_ci_decision` established, and used not to. That function is
    # fail-closed by design — any completed run at the SHA concluding other
    # than success makes the whole commit `failed` — but it handed on only
    # the run list, and selection then walked ALL completed runs newest
    # first for the first readable receipt. A newer failed run that uploaded
    # nothing therefore fell through to an older green run's receipt and
    # every gate came back a bound pass, at a commit whose CI is red.
    #
    # A green receipt older than the failure is not evidence about this
    # commit's gates; it is evidence that they passed BEFORE the run that
    # says otherwise. So when the state is `failed`, only the failed runs
    # may answer: their own per-gate receipt where they left one — where
    # the biconditional below turns a green row inside a red run into a
    # not-run and a non-zero row into the failure it records — and
    # otherwise a not-run naming what was asked. The success state is
    # untouched: several green runs at one commit are all admissible, which
    # is what lets the newest-first walk find a receipt among them.
    admitted = at_sha
    if state == "failed":
        admitted = [r for r in at_sha if r.get("status") == "completed"
                    and r.get("conclusion") != "success"]
    receipt, deciding, why = _ci_fetch_receipt(slug, admitted, target_sha)
    if receipt is None:
        scope = ("" if state != "failed" else
                 f"; the completed run(s) at {target_sha[:12]} concluded "
                 f"other than success, and only their own receipt can "
                 f"answer for this commit's gates")
        return {g["id"]: _ci_not_run(
            g, f"not run: no gate receipt for {target_sha} ({why}{scope}) — "
               f"a run's conclusion says nothing about which gates it "
               f"executed, so an unreceipted gate is not evidence")
            for g in gates}
    summary = _ci_summary(deciding)
    rows = {r.get("id"): r for r in receipt["gates"] if isinstance(r, dict)}
    artifact = ci_receipt_artifact(target_sha)
    out: dict[str, dict] = {}
    for gate in gates:
        row = rows.get(gate["id"])
        if row is None:
            out[gate["id"]] = _ci_not_run(
                gate, f"not run: CI ran the workflow but not this gate — "
                      f"{artifact} from run {summary['id']} names "
                      f"{sorted(k for k in rows if k)} and not {gate['id']!r}")
            continue
        if row.get("not_run"):
            out[gate["id"]] = _ci_not_run(
                gate, f"not run: CI declared this gate not run "
                      f"({row['not_run']}) — run {summary['id']} is green "
                      f"about gates it executed, and this is not one")
            continue
        expected = " ".join(gate["command"])
        if row.get("command") != expected:
            out[gate["id"]] = _ci_not_run(
                gate, f"not run: {artifact} attests the command "
                      f"{row.get('command')!r}, and this manifest declares "
                      f"{expected!r} — a changed command is a different gate")
            continue
        exit_code = row.get("exit_code")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            out[gate["id"]] = _ci_not_run(
                gate, f"not run: {artifact} records exit_code "
                      f"{exit_code!r} for this gate, which is not an exit "
                      f"code")
            continue
        success = summary.get("conclusion") == "success"
        if (exit_code == 0) != success:
            out[gate["id"]] = _ci_not_run(
                gate, f"not run: {artifact} records exit {exit_code} for this "
                      f"gate while run {summary['id']} concluded "
                      f"{summary.get('conclusion')!r} ({summary.get('url')}) "
                      f"— the receipt and the run disagree, and a "
                      f"contradiction is not evidence")
            continue
        out[gate["id"]] = _ci_attestation(cfg, gate, target_sha, row, summary,
                                          receipt, context, run)
    return out


def _await_ci(cfg: Config, gates: list[dict], target_sha: str, run: str,
              interval: float, timeout: float) -> dict:
    """Poll ONCE for the whole set of CI-attested gates and return their
    attestations by id.

    One poll per interval, not one per gate: the gates share a question —
    "what did CI execute at this commit?" — and asking it five times would be
    five API calls for one answer, five times the rate limit, and five
    chances to read a different one. The receipt is fetched once for the same
    reason: it carries every gate's row.
    """
    started = time.monotonic()
    try:
        # 0.25.0: `_ci_target` holds only a root; the runner handed to it
        # carries this configuration's ceiling, and a timeout is the typed
        # refusal rather than "no coordinates" — did-not-answer-in-time is
        # not unresolvable, and a person can act on the difference.
        slug, branch = _ci_target(cfg.repo_root, git=lambda *a: _git(
            cfg.repo_root, *a, ceiling=git_ceiling(cfg)))
    except _git_timeout_class():
        raise
    except RuntimeError as exc:
        return {g["id"]: _ci_not_run(
            g, f"not run: the CI coordinates are not resolvable, so no run "
               f"can be attested for this commit ({exc})") for g in gates}
    polls = 0
    while True:
        polls += 1
        try:
            rows = _gh_runs(slug, branch)
        except RuntimeError as exc:
            return {g["id"]: _ci_not_run(g, f"not run: {exc}") for g in gates}
        state, at_sha = _ci_decision(rows, target_sha)
        waited = time.monotonic() - started
        if state != "pending":
            context = {"repository": slug, "branch": branch, "polls": polls,
                       "waited_s": round(waited, 2), "run_state": state,
                       "runs_at_target_sha": [_ci_summary(r) for r in at_sha],
                       "poll_interval_s": interval, "timeout_s": timeout}
            return _ci_records(cfg, gates, target_sha, slug, at_sha,
                               context, run, state)
        if waited + interval > timeout:
            pending = sum(1 for r in at_sha
                          if r.get("status") != "completed")
            return {g["id"]: _ci_not_run(
                g, f"not run: no completed run for {target_sha} within "
                   f"{int(timeout)}s ({polls} poll(s) of "
                   f"{slug}@{branch}; {pending} run(s) at that SHA still in "
                   f"flight) — an unattested gate is not evidence")
                for g in gates}
        time.sleep(interval)


@dataclasses.dataclass(frozen=True)
class LocalGates:
    """The LOCAL half of one hand-off's gate run: every manifest gate this
    process executes, run at the commit BEFORE the push (0.25.0, public
    issue #2) and carried to the emission, so that no gate runs twice.

    `records` are the attestations in manifest order, exactly as the
    envelope will carry them; `run` is the retention token the whole run
    shares, so the CI-attested half that is awaited after the push retains
    beside them, as one run's evidence always has. `base` is the review
    base these gates were told (`<TOOL>_GATE_BASE`, None when none was):
    the range a range-sensitive gate attested, which only a request naming
    that same base may carry (0.25.0 review round 1 F1).
    """
    sha: str
    run: str
    records: tuple
    base: str | None = None


def run_local_gates(cfg: Config, target_sha: str, base: str | None = None,
                    execute_ci_gates: bool = False) -> LocalGates:
    """The half of `run_gates` that needs nothing from a remote: every gate
    not attested by CI (all of them inside CI or under `execute_ci_gates`,
    the split `run_gates` makes). Executed at the commit, before the push;
    `run_gates(..., prior=...)` later adds the CI-attested half without
    executing these again."""
    token = run_token()
    records = run_gates(cfg, target_sha, base=base,
                        execute_ci_gates=execute_ci_gates, local_only=True,
                        token=token)
    return LocalGates(target_sha, token, tuple(records), base)


def local_gate_failures(cfg: Config, local: LocalGates
                        ) -> tuple[list[str], list]:
    """`(failed gate ids, the validator's error items)` for the local half.

    THE PREDICATE IS THE VALIDATOR'S, never a second one: each record is
    judged by `validate.validate_attestations` — the very function the
    emitted request is validated by — against the manifest's own row for
    that gate and the commit the request will bind. Judged one record at a
    time so the refusal can name every gate that failed, and because a
    record's errors are its own: the manifest-level checks (a gate missing,
    a gate undeclared) cannot fire on a half the runner built from the
    manifest itself. So the refusal before the push and the validation
    after the emission cannot disagree — on a failed exit, an unbound
    tree, a not-run record, `not run: nested` included.
    """
    from .validate import errors_in, validate_attestations
    rows = {g["id"]: g for g in cfg.gates}
    failed, items = [], []
    for rec in local.records:
        one = dataclasses.replace(cfg, gates=[rows[rec["id"]]])
        errors = errors_in(validate_attestations(
            _attestation_block(cfg.wrapper_tag, [rec]), one,
            request_sha=local.sha))
        if errors:
            failed.append(rec["id"])
            items.extend(errors)
    return failed, items


class GatesRefused(RuntimeError):
    """A blocking local gate did not pass, and the hand-off stopped BEFORE
    the push (0.25.0, public issue #2: gate before push, no undo).

    Nothing was pushed, emitted or recorded. The commit the hand-off made,
    if it made one, is left for the author to amend or reset — the same
    precedent `SweepRefused`'s read-back refusal and `AuthorityAbsent` set:
    the tool does not undo its own commit. Carries the validator's items
    and the commit, so the refusal names what failed without a second run.
    """

    def __init__(self, message: str, remedy: str, items, sha: str,
                 outputs: dict):
        super().__init__(message)
        self.remedy = remedy
        self.items = list(items)
        self.sha = sha
        self.outputs = dict(outputs)


def gate_before_push(cfg: Config, record: dict, base: str | None = None,
                     verb: str = "handoff") -> LocalGates:
    """Run the local gates at `record`'s commit and refuse, before the push,
    when the validator would refuse them after the emission.

    `record` is what `ensure_pushed` hands its `before_push`: the commit,
    whether the hand-off made it, and the governing configuration whose
    manifest runs. Returns the `LocalGates` the emission carries; raises
    `GatesRefused` otherwise.
    """
    sha = record["sha"]
    local = run_local_gates(cfg, sha, base=base)
    failed, items = local_gate_failures(cfg, local)
    if not items:
        return local
    by_id = {r["id"]: r for r in local.records}
    outputs = {gid: (by_id[gid].get("output") or {}).get("pointer", "")
               for gid in failed if isinstance(by_id[gid].get("output"),
                                               dict)}
    where = (f"the hand-off committed its outstanding work locally at "
             f"{sha}" if record.get("committed") else
             f"no commit was made (HEAD is {sha})")
    names = ", ".join(failed) if failed else "the local attestation block"
    undo = (f"then amends or resets the local commit {sha[:12]} — the tool "
            f"does not undo its own commit — and re-runs"
            if record.get("committed") else "then commits the fix and re-runs")
    raise GatesRefused(
        f"{len(failed) or 1} local gate(s) did not pass at {sha[:12]}: "
        f"{names} — judged by the request validator's own attestation rule "
        f"before anything left this machine. Nothing was pushed, emitted or "
        f"recorded; {where}",
        remedy=(f"the author fixes what the failed gate(s) report (the "
                f"items below; each gate's output is retained at the "
                f"pointer named), {undo} `{TOOL_NAME} {verb}`"),
        items=items, sha=sha, outputs=outputs)


def run_gates(cfg: Config, target_sha: str,
              base: str | None = None, execute_ci_gates: bool = False,
              ci_poll_interval: float = CI_POLL_INTERVAL_S, *,
              local_only: bool = False, prior: LocalGates | None = None,
              token: str | None = None) -> list[dict]:
    """Execute the declared gate manifest and return attestations (§5.1).

    `base` is the review range's other end (audit of 2026-09-05, finding
    4): handoff commits outstanding work BEFORE the gates run, so a gate
    that inspects the working tree — `git diff --check` was one — has
    nothing left to inspect and attests a clean tree that says nothing
    about the commits under review. The range is exported to every gate
    THIS RUN EXECUTES as `<TOOL>_GATE_BASE` and `<TOOL>_GATE_HEAD` (a
    CI-attested gate's receipt comes from CI's own run, which is told no
    review base — round 2 F2 of the 0.25.0 review); a gate that reads a range
    reads these, and a run with no base (a bare `loupe-gates` outside a
    handoff) exports only the head, so the gate can say what it fell back
    to rather than guess.

    Round-3 F10: the Evidence block previously carried author assertions
    labelled as such. Honest disclosure is not conformance — Evidence is
    machine-attested by definition, so the emitter runs the gates itself and
    records exit codes.

    Round-4 F4: it also stamped whatever SHA it was handed. Gates run in the
    current checkout, so the tree that executed is the only thing an
    attestation can honestly bind to. This reads HEAD and cleanliness from
    that checkout, records `executed_sha` from it, and marks the attestation
    `unbound` — with the reason — whenever the executed tree is not exactly
    the target. A probe that ran `true` in this tree and claimed the null SHA
    is the defect; it now claims nothing.

    A gate declaring `attested_by = "ci"` is NOT executed here (2026-09-07,
    brief `ci-attested-gates`) unless this process is itself the CI runner
    (`GITHUB_ACTIONS`) or the caller passes `execute_ci_gates`. CI's own
    per-gate RECEIPT for the target SHA, not a second local execution and
    not a run's conclusion, becomes the attestation (round-3 F2) — which
    needs the SHA on the remote, so that half is awaited AFTER the push.

    HAND-OFF ORDER, since 0.25.0 (public issue #2): commit → LOCAL gates →
    push → CI-attested gates → emission. The two halves are one run: the
    hand-off calls `run_local_gates` (this function with `local_only`) at
    the commit, refuses before the push when the validator would refuse
    the result (`gate_before_push`), and after the push calls this with
    `prior`, which awaits the CI-attested half and merges both in manifest
    order WITHOUT executing the local half again. `token` is the shared
    retention token. Called with neither, this is the whole manifest in one
    call, as `bin/loupe-gates` and every direct caller always had it.
    """
    # Which gates this process executes, and which it waits for. INSIDE CI
    # everything executes: the attestation has to be produced by somebody,
    # and there the somebody is this run. `--execute-ci-gates` is the same
    # escape hatch LOUPE_GATE_WORKERS=1 is — a way back to local execution
    # that needs no code change.
    ci_gates = ([] if os.environ.get(CI_ENV) or execute_ci_gates
                else [g for g in cfg.gates
                      if g.get("attested_by") == CI_ATTESTER])
    ci_ids = {g["id"] for g in ci_gates}
    local_gates = [g for g in cfg.gates if g["id"] not in ci_ids]
    if prior is not None:
        # The pre-run half must be THIS commit's and THIS manifest's local
        # half, or the envelope would carry evidence about something else.
        if prior.sha != target_sha:
            raise RuntimeError(
                f"the local gates were run at {prior.sha}, and the request "
                f"binds {target_sha}: evidence about one commit cannot be "
                f"carried by another's request")
        if [r["id"] for r in prior.records] != [g["id"] for g in local_gates]:
            raise RuntimeError(
                f"the local gates run before the push "
                f"({[r['id'] for r in prior.records]}) are not this "
                f"manifest's local half ({[g['id'] for g in local_gates]})")
        # And THIS range's (0.25.0 review round 1 F1): a range-sensitive
        # gate attested the base it was told, so a request naming another
        # base cannot carry it — the blocking gate would read as passed for
        # a range it never checked. Compared as given: the hand-off hands
        # both ends the one id `ensure_pushed` resolved before its commit.
        if prior.base != base:
            raise RuntimeError(
                f"the local gates run before the push were told the review "
                f"base {prior.base or '(none)'}, and the request names "
                f"{base or '(none)'}: a gate's evidence about one range "
                f"cannot be carried by a request about another")
    order = local_gates if local_only else cfg.gates
    wait = [] if local_only else ci_gates

    # Re-entrancy guard. A gate manifest that runs the test suite, whose
    # tests emit envelopes, would otherwise recurse forever — and it did on
    # first run. The guard is an inherited env var rather than a parameter
    # because the recursion crosses a process boundary, where a flag cannot
    # reach. A nested emission records the gates as not run rather than
    # pretending they passed. With `prior`, the half already recorded is
    # kept exactly as recorded: it is the evidence the pre-push check judged.
    if os.environ.get(env_var("IN_GATE_RUN")):
        nested = {g["id"]: {"id": g["id"],
                            "blocking": g.get("blocking", False),
                            "error": "not run: nested inside a gate execution"}
                  for g in order}
        if prior is not None:
            nested.update({r["id"]: r for r in prior.records})
        return [nested[g["id"]] for g in order]

    ci_timeout = float(cfg.limits.get("ci_timeout", CI_TIMEOUT_DEFAULT_S)
                       if isinstance(cfg.limits, dict) else
                       CI_TIMEOUT_DEFAULT_S)
    records: dict[str, dict] = {}
    if prior is not None:
        # The local half ran before the push and is not run again; only the
        # CI-attested half is left, and it needs the pushed SHA.
        records.update({r["id"]: r for r in prior.records})
        if wait:
            records.update(_await_ci(cfg, wait, target_sha, prior.run,
                                     interval=ci_poll_interval,
                                     timeout=ci_timeout))
        return [records[g["id"]] for g in order]

    try:
        executed_sha = _git(cfg.repo_root, "rev-parse", "HEAD",
                            ceiling=git_ceiling(cfg))
        porcelain = _git(cfg.repo_root, "status", "--porcelain",
                         ceiling=git_ceiling(cfg))
    except _git_timeout_class():
        # A tree git did not describe in time is not "cannot identify": the
        # typed refusal travels (0.25.0).
        raise
    except RuntimeError as exc:
        return [{"id": g["id"], "blocking": g.get("blocking", False),
                 "error": f"not run: cannot identify the executed tree ({exc})"}
                for g in order]

    tree = "dirty" if porcelain else "clean"
    if executed_sha != target_sha:
        binding = (f"unbound: gates executed against {executed_sha}, not the "
                   f"attested target {target_sha}")
    elif porcelain:
        binding = ("unbound: the executed tree is dirty, so it is not the "
                   "target commit's content")
    else:
        binding = "bound"

    env = {**_caller_env(), env_var("IN_GATE_RUN"): "1",
           env_var("GATE_HEAD"): target_sha}
    # The base is THIS run's or nobody's (lineage 25 round 1 F3): an
    # inherited LOUPE_GATE_BASE from a caller's stale environment made the
    # whitespace gate attest HEAD..HEAD as "the review range" and clean,
    # under a run that described itself as base-less. The runner owns both
    # names; it clears them before exporting what it knows.
    env.pop(env_var("GATE_BASE"), None)
    if base:
        env[env_var("GATE_BASE")] = base
    # One token for the whole manifest: the run is the unit of evidence, so
    # every gate of one run retains beside its siblings and a later run at
    # the same commit lands somewhere else entirely. A hand-off's two halves
    # are one run, and share the token its caller passes.
    run = token or run_token()

    def run_one(gate: dict) -> dict:
        started = time.monotonic()
        try:
            proc = subprocess.run(gate["command"], cwd=cfg.repo_root,
                                  capture_output=True, text=True, timeout=600,
                                  env=env)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"id": gate["id"],
                    "blocking": gate.get("blocking", False),
                    "error": f"could not execute: {exc}"}
        duration = time.monotonic() - started
        return {
            "id": gate["id"],
            "command": " ".join(gate["command"]),
            "exit_code": proc.returncode,
            "tool_version": _tool_identity(cfg.repo_root, gate["command"][0]),
            "runner": f"{TOOL_NAME}/{TOOL_VERSION}",
            "target_sha": target_sha,
            "executed_sha": executed_sha,
            "tree": tree,
            "binding": binding,
            "duration_s": round(duration, 2),
            "output": _retain_output(cfg, executed_sha, gate["id"],
                                     proc.stdout + proc.stderr, run),
            "blocking": gate.get("blocking", False),
        }

    # Gates run CONCURRENTLY (2026-08-31). Measured: 145.4s sequential ->
    # 85.8s at four workers, all gates green in both, no coverage change —
    # the loop becomes bounded by its slowest gate rather than their sum.
    #
    # Sound because every declared gate is a CHECKER: each is read-only
    # against the working tree, and the two that build anything
    # (candidate-standalone, park-candidate) build into their own mkdtemp.
    # A gate that WROTE to the tree would make this unsound, so that is the
    # rule a new gate must meet to join the manifest.
    #
    # Order is the manifest's, never completion order: the executor maps over
    # cfg.gates and the results are re-sequenced by index. An attestation
    # block whose order depended on a race would be a different document on
    # every run, and these documents are compared byte-for-byte.
    #
    # duration_s is therefore WALL time under contention, not isolated cost.
    # Four workers, because the measurement said so — eight was slower on
    # this machine (4 performance cores, spawn-heavy work).
    #
    # LOUPE_GATE_WORKERS=1 forces the old sequential path. It exists because
    # a concurrency bug in the thing that produces review evidence must have
    # a way back that does not need a code change.
    try:
        workers = int(os.environ.get(env_var("GATE_WORKERS"), "4"))
    except ValueError:
        workers = 4

    def wait_for_ci() -> dict:
        return _await_ci(cfg, wait, target_sha, run,
                         interval=ci_poll_interval, timeout=ci_timeout)

    workers = max(1, min(workers, len(local_gates) or 1))
    if workers == 1 or not local_gates:
        # The sequential path waits for CI FIRST: a poll that spends its
        # timeout is the long pole either way, and doing it up front keeps
        # the two halves in one obvious order for whoever is debugging.
        if wait:
            records.update(wait_for_ci())
        for gate in local_gates:
            records[gate["id"]] = run_one(gate)
    else:
        # The CI wait rides in the pool beside the local gates rather than
        # before or after them: it is almost entirely sleep, and serialising
        # it would add the whole poll to a loop this change exists to
        # shorten. One extra worker, because it occupies a thread it never
        # computes in. (A hand-off's CI half no longer rides here: it waits
        # for the push, so it is awaited with `prior`, after it.)
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=workers + (1 if wait else 0)) as pool:
            waiting = pool.submit(wait_for_ci) if wait else None
            for rec in pool.map(run_one, local_gates):
                records[rec["id"]] = rec
            if waiting is not None:
                records.update(waiting.result())
    return [records[g["id"]] for g in order]


def _attestation_block(tag: str, attestations: list[dict]) -> str:
    """The Evidence block: one machine-readable authority, not two.

    Round-4 F3: the attestations used to be rendered as prose lines and
    checked for the presence of a section, so removing the exit code and the
    target SHA from an emitted request produced zero validation errors. They
    are now carried structurally and validated field by field. Rendering them
    twice — once for a human, once for the machine — would restate one fact in
    two places, which is the trap this project keeps rediscovering.
    """
    body = json.dumps(attestations, indent=2, ensure_ascii=False,
                      sort_keys=True)
    return f"```{wire.attestation_fence(tag)}\n{body}\n```"


def _taxonomy_block(cfg: Config) -> str:
    notes = cfg.taxonomy.get("classification_notes", {})
    lines = [
        f"Severity, ordered:      {' > '.join(cfg.severities)}",
        f"Blocking severities:    {', '.join(cfg.blocking_severities)}",
        "Classification, one of:",
    ]
    for cls in cfg.classifications:
        note = notes.get(cls, "")
        lines.append(f"  {cls:<24}{note}".rstrip())
    lines.append("")
    lines.append("Findings must be ORDERED by severity and ATOMIC — exactly "
                 "one claim per finding ID.")
    return "\n".join(lines)


def _verdict_shape_block(cfg: Config, sha: str) -> str:
    """Generated from vocab — the enums render from one authority, always."""
    closures = ", ".join(f"`{c}`" for c in vocab.CLOSURES[:-1])
    outcomes = "|".join(vocab.TEST_AMENDMENT_OUTCOMES)
    return "\n".join([
        f'Verdict shape: `<{cfg.wrapper_tag}-review-verdict sha="{sha}">` '
        f"wrapper; first meaningful line exactly",
        f"`VERDICT: {vocab.VERDICT_CLEAN}` or `VERDICT: {vocab.VERDICT_CHANGES}`; "
        f"then `## findings` then `## evidence checked`,",
        "in that order. Findings ordered by severity, atomic, IDs `F1..Fn` "
        "(fresh numbering; reference",
        "earlier rounds' findings by fingerprint when continuing one). Each "
        "finding carries " + ", ".join(vocab.FINDING_FIELDS) + ".",
        "Optionally add `Anchor: <symbol or hunk name>` — a structured anchor "
        "that survives line movement and",
        "distinguishes two findings in one file. Omit it and the anchor is "
        "derived from the first Evidence",
        "citation instead, which is weaker and is recorded as such.",
        "Optionally add `Citations: <comma-separated targets>` naming the "
        "documents this finding cites, e.g.",
        "`Citations: design, review/wire.py`. Line numbers are stripped from "
        "declared citations so a moved line",
        "keeps the finding's identity, while `timeout:30` and `port:8080` stay "
        "distinct. Omit it and citations are",
        "guessed from token shape, which cannot see an extensionless "
        "secondary citation such as `design:102`",
        "and therefore splits its identity when the line moves.",
        f"`{vocab.VERDICT_CLEAN}` carries exactly `None` under findings.",
        "",
        f"Reviewer closure events, where a prior finding is answered: put "
        f"them under `## closures`,",
        "one per line, as `- <fingerprint> <closure>[ <outcome>]: <what the "
        "author's evidence did or did not establish>`.",
        f"Closures: {closures}, or `test_amendment` with outcome "
        f"`{outcomes}` answering an accepted(test_amended)",
        "disposition — silence is neither. Fingerprints are the ones printed "
        "in the disposition ledger above.",
        "Where a closure is `reclassified` and a finding of THIS verdict "
        "carries what remains, add a continuation line",
        "`  Residue: F1` (several ids comma-separated) under it: convergence "
        "then counts the thread once instead of",
        "reading the narrowed residue as a new finding on the same anchor.",
        "Material you cannot retrieve: list it under `## unavailable "
        "references`, return",
        f"`VERDICT: {vocab.VERDICT_CHANGES}`, one finding per unreachable "
        f"material reference (§5.1).",
    ])


def _round_line(round_no: int, effective_cap: int, default_cap: int) -> str:
    """The `Round:` line: a state report, and a parenthetical that reports
    THE SAME STATE rather than a standing policy (brief
    `round-cap-stamp-misreports`).

    The parenthetical used to switch on one axis only — whether a ledger
    override had moved the cap — so `budget breaker fires past the cap` was
    printed byte-identically on round 1 of every lineage that had no
    override. One line then carried two halves with opposite statuses, and
    two independent readers, two rounds apart, on opposite sides of the
    exchange, read the standing half as reporting the state half: lineage 25
    round 4 cost the reviewer a paragraph of verdict prose ruling that the
    clean finding recorded no override, and lineage 26 round 3 made the
    AUTHOR tell the user a fourth round needed their authorization. It does
    not.

    So the policy half is state-dependent too, and it says what the tool
    actually does. "Fires" is gone with it: past the cap no breaker trips
    anything. The user decided 2026-08-25 that past the cap the tool emits
    and INVESTIGATES rather than refusing — `handoff_preflight` exempts
    `budget`/`rounds` from the firing that would stop the loop, `validate`
    records R-BUDGET as a notice and not an error, and `review.toml`'s
    `[limits]` comment says it in as many words: "nothing is gated, and no
    authorization is needed to continue". The convergence report this line
    points at is in the envelope by construction — `Ledger.report` carries
    it and `render_report_md` renders it — so the pointer is to bytes the
    reviewer already holds, not to a command they must run.
    """
    # The override half is unchanged in content: which cap is in force and
    # on whose authority is a fact about this lineage either way.
    override = (f"repo default is {default_cap}; this lineage is authorized "
                f"to {effective_cap} by a recorded ledger override — "
                if effective_cap != default_cap else "")
    state = ("past the advisory cap: nothing is gated and no authorization "
             "is needed to continue — the tool emits rather than refusing, "
             "and the convergence report under `## Ledger report` below is "
             "what the count was a poor proxy for"
             if round_no > effective_cap
             else "the cap is advisory and this round is within it")
    return f"Round:  {round_no} of {effective_cap} ({override}{state})"


def _base_line(base: str, round_no: int, ledger: Ledger) -> str:
    """The `Base:` line, state-dependent on the round.

    It read `(the SHA ruled on in round {round_no - 1})` unconditionally, so
    round 1 of a fresh lineage said "ruled on in round 0". Nothing was ruled
    on in round 0; there is no round 0. The base of a round-1 request is
    whatever the author passed as `--base` — `_emit` refuses without one
    when the lineage holds no verdict — and calling that a ruling invented a
    round, an authority and a reviewer that never existed.

    On round 1 the record is also asked whether ANY verdict ever ruled on
    that commit, which is the common case when a lineage opens on the tip a
    previous one closed cleanly. That is one pass over the ledger's own
    events, matching the recorded `sha` of recorded verdicts — the record,
    not an inference from round arithmetic — so the absence is reported as
    an absence rather than left to look like one.
    """
    if round_no > 1:
        return f"Base:   {base}   (the SHA ruled on in round {round_no - 1})"
    ruled = [e for e in ledger.events()
             if e.get("event") == "verdict" and e.get("sha") == base]
    if ruled:
        last = ruled[-1]
        where = (f"lineage {ledger.lineage_of(last)} round "
                 f"{last.get('round', '?')}")
        # The last one recorded, and how many there are: a tip that carried
        # several rounds of a closed lineage is ruled on by each of them,
        # and naming every one would bury the fact in a list.
        elsewhere = (f"; a verdict elsewhere in this ledger did rule on it "
                     + (f"({where})" if len(ruled) == 1 else
                        f"({len(ruled)} of them, the last in {where})"))
    else:
        elsewhere = "; no verdict in this ledger rules on it"
    return (f"Base:   {base}   (round 1 opens this lineage: the base is the "
            f"one the author declared, ruled on by no round of this "
            f"lineage{elsewhere})")


def _ruling_pointer(cfg: Config, ledger: Ledger, round_no: int,
                    lineage: str, transport: str) -> str:
    """Where the verdict these dispositions answer is, beside the ledger.

    The reviewer's standing ask: the disposition ledger below lists answers
    to a ruling the envelope never named, so reading the two together meant
    hunting for the verdict by hand.

    DERIVED FROM THE RECORD, never from the directory. The round's verdict
    event carries `source_digest`, the digest is half the retained copy's
    name, and `transport.kept_path` composes the other half — so a pointer
    exists exactly when the record says which bytes were ruled, and a
    directory holding some other lineage's round-N verdict cannot be
    mistaken for this one. Retention is best effort by design (`keep_bytes`
    degrades to a reason string), so "no copy was kept" is a state this line
    reports in words: a verdict closed from stdin on another machine, or an
    imported legacy round, leaves the digest and nothing else.

    `transport` decides which half is useful. On `path` both ends read one
    filesystem and the path is actionable; on `paste` or `git` it names a
    file on the author's machine, so the digest is what the reviewer can
    check the bytes they were handed against.
    """
    prior = round_no - 1
    if prior < 1:
        return ("Round 1 opens this lineage: there is no previous verdict "
                "for these to answer.")
    verdicts = [e for e in ledger.current(lineage)
                if e.get("event") == "verdict" and e.get("round") == prior]
    if not verdicts:
        return (f"No round-{prior} verdict is recorded in this lineage, so "
                f"these dispositions answer no ruling this ledger holds.")
    digest = str(verdicts[-1].get("source_digest") or "")
    if not digest:
        return (f"The ruling these answer is the round {prior} verdict; the "
                f"record carries no digest for it (an imported or "
                f"hand-added event), so it names no retained copy.")
    head = f"The ruling these answer: the round {prior} verdict, sha256 `{digest}`"
    # Local import for the same reason `transport` imports this module
    # locally: one naming authority for the retained copy, and no new
    # module-level edge between the two.
    from . import transport as _transport
    kept = _transport.kept_path(cfg, prior, "verdict", lineage=lineage,
                                digest=digest)
    if kept is None:
        return (f"{head} — no copy of it was kept beside this ledger (a "
                f"verdict closed from stdin on another machine, or an "
                f"imported round), so the digest is the whole pointer.")
    if transport == vocab.TRANSPORT_PATH:
        return (f"{head}, kept at `{kept}` — this round declares transport "
                f"`{transport}`, so that path is one both ends read.")
    return (f"{head}, kept at `{kept}` — that path is on the author's "
            f"machine, which this round's `{transport}` transport does not "
            f"share, so the digest is the half you can check.")


def _disposition_heading(round_no: int) -> str:
    """The disposition ledger's heading, which on round 1 named a round
    that does not exist.

    `## Disposition ledger — round 0, emitted by the tool` is what every
    round-1 request printed, because the heading was `round_no - 1` with
    nothing asked about whether that round could exist. It cannot: round 1
    opens the lineage, so there is no earlier round, no ruling and nothing
    to dispose of. This is the leftover the commit that fixed the `Base:`
    and `Round:` lines named and did not take.

    NOT part of the validated request grammar, and checked rather than
    assumed: `REQUIRED_REQUEST_SECTIONS` is taxonomy / claim / evidence /
    contract / reference, and `wire.section_key` reduces a heading to its
    leading word, so every reader that could match this one matches
    `disposition` — which no production reader asks for. The leading word
    is preserved anyway, because a heading's identity is the cheapest thing
    to keep stable and `test_transport_integration` locates the ruling
    pointer by the `## Disposition ledger` prefix.
    """
    if round_no > 1:
        return (f"## Disposition ledger — round {round_no - 1}, emitted by "
                f"the tool")
    return ("## Disposition ledger — round 1 opens this lineage, emitted by "
            "the tool")


def _dispositions_block(ledger: Ledger, round_no: int, lineage: str) -> str:
    # Round 2 F1: the reviewer is shown the STANDING answer per finding,
    # once — the live round-2 request printed each answer three times,
    # because this block read raw events while the preflight read the
    # standing selector. Superseded emissions are marked by count, never
    # presented as co-standing answers; the raw rows stay in the ledger
    # file, which is the audit surface.
    every = ledger.disposition_batches(lineage, round_no=round_no)
    batches = [b for b in every if b["disposition"] is not None]
    standing = [b for b in batches if b["standing"]]
    # Round 3 F1: orphan companions are named to the reviewer, never
    # rendered as answers and never silently dropped — the anomaly is the
    # reviewer's business exactly because it certifies nothing above.
    orphans = [b for b in every if b["orphan"]]
    orphan_note = (f"- ANOMALY: {len(orphans)} orphan companion batch(es) "
                   f"bind no recorded emission and certify nothing (the "
                   f"`orphan` breaker fires; see the ledger report)"
                   if orphans else "")
    if not standing:
        # The leftover named by the commit that fixed `Base:` and `Round:`:
        # a round-1 request asked this block for round 0 and got "(no
        # round-0 dispositions in the ledger)". There is no round 0, so
        # that sentence reported an emptiness of something that never
        # existed — the same invention the `Base:` line was carrying, one
        # section further down. Below round 1 the block says what is true:
        # the lineage opens here and there is nothing to carry.
        base = ("(round 1 opens this lineage: no earlier round ruled, so "
                "there are no dispositions to carry)" if round_no < 1 else
                f"(no round-{round_no} dispositions in the ledger)")
        return f"{base}\n{orphan_note}" if orphan_note else base
    superseded = Counter(b["key"] for b in batches if not b["standing"])
    lines = [orphan_note] if orphan_note else []
    for b in standing:
        e = b["disposition"]
        sub = f"({e['subtype']})" if e.get("subtype") else ""
        payload = e.get("payload", {})
        detail = payload.get("verification") or payload.get("destination") or ""
        # The falsification record travels with the acceptance it backs
        # (§5.3a): the reviewer rules on whether the named test was run and
        # whether it can fail, so both axes are shown, not summarised.
        run = payload.get("falsification")
        if isinstance(run, dict) and run.get("status"):
            detail = (f"{detail} [falsification: {run.get('status')}; "
                      f"mutation: {run.get('mutation')}]").strip()
        line = (f"- {e['finding_id']} `{e['fp']}` — "
                f"**{e['disposition']}{sub}** {detail}".rstrip())
        earlier = superseded.get(b["key"], 0)
        if earlier:
            line += (f" — standing answer; supersedes {earlier} earlier "
                     f"emission(s) kept as audit history")
        lines.append(line)
    return "\n".join(lines)


def _reference_block(repo_root: Path, references: list[dict],
                     sha: str, git=None, git_bytes=None) -> str:
    """Immutable reference manifest (§5.1 F4): digest, access, status.
    A pointer without a digest is a rumour; an unreadable reference is
    labelled unavailable, not dropped (absent != none).

    Round 3 F1: kind and digest come from the TARGET TREE at `sha`, through
    the same `refs` derivation `take` uses — never from the working tree.
    The manifest is a promise about bytes the reviewer will fetch, and the
    reviewer fetches the commit; a digest over worktree bytes was the same
    promise about a state only this machine can see. For a regular file the
    two agreed by construction (the handoff commits before it emits); for a
    symlink they never did, and the difference arrived as a `mismatch` the
    author could not reproduce. Emission now cannot describe an object
    differently from the side that checks it.

    A reference the target tree does not carry renders UNAVAILABLE here —
    which is what `take` would report anyway. Advisory material that cannot
    travel keeps its declared-unavailable state; it is now declared at
    emission rather than discovered by the reviewer.
    """
    # Round 2 F2: both target reads, replacement-disabled. The manifest
    # this renders is a promise about bytes the reviewer will fetch, so it
    # must describe the ORIGINAL objects — and the reviewer's own
    # verification (`probe_references`) is hardened the same way, or the two
    # ends would derive a reference from two different object graphs for
    # one SHA.
    run = git or (lambda *a: _git(repo_root, *a, no_replace=True))
    run_bytes = git_bytes or (lambda *a: _git_bytes(repo_root, *a,
                                                    no_replace=True))
    lines = []
    for ref in references:
        path = str(ref["path"])
        # Round 4 F4: the render boundary refuses too, not only the
        # preflight upstream of it. A caller reaching this function
        # directly — a library user, a test, a future verb — cannot
        # produce a manifest line the reviewer's parser will not
        # recognise, whatever the row's `required` value says.
        grammar = refs.path_error(path)
        if grammar is not None:
            raise ReferenceUnbound(
                f"reference {path!r} is {grammar}",
                remedy="a person points the reference at a path the "
                       "manifest line can carry")
        req = "required" if ref.get("required", True) else "advisory"
        note = ref.get("note", "")
        kind = refs.target_kind(run, sha, path)
        if kind == "blob":
            digest = refs.target_digest(run_bytes, sha, path)
            lines.append(f"  {path}  sha256:{digest}  "
                         f"[{req}] {note}".rstrip())
        elif kind == "tree":
            lines.append(f"  {path}/  (directory; per-file digests via "
                         f"git) [{req}] {note}".rstrip())
        else:
            lines.append(f"  {path}  UNAVAILABLE from this surface — "
                         f"mark findings that depend on it `unavailable` "
                         f"[{req}] {note}".rstrip())
    return "\n".join(lines)


class RoleSelectionError(RuntimeError):
    """A per-invocation role selection the repository's config does not
    permit. The recovery is always a person's — the config gains the
    identity, or the flag is dropped — so the CLI maps this to `blocked`
    and `remedy` carries what that person must do."""

    def __init__(self, message: str, remedy: str):
        super().__init__(message)
        self.remedy = remedy


# One class, two names: the boundary and its error live in vocab (R1-F2) —
# a transport declared outside the closed grammar, or one that contradicts
# the target's reachability. Like a role selection, the recovery is a
# person's, so the CLI maps it to `blocked`. This module's historical name
# stays importable; the callers that catch it name the selection act, not
# the module that refuses.
TransportSelectionError = vocab.TransportDeclarationError


def resolve_transport(cfg: Config, transport: str | None = None,
                      local_only: bool = False,
                      env=None) -> str:
    """The effective transport for one emission (RVW-T11, round 3 F2).

    The same precedence shape as `resolve_roles`, and for the same reason:
    the tool cannot observe the value, so it is declared, and a per-invocation
    flag selects within what the repository declares rather than widening it.
    The difference is that the vocabulary here is the tool's own closed enum
    rather than a per-repo list — `path` and `paste` are the only two states
    the grammar has — so the permission being selected within is `TRANSPORTS`
    itself, and an unknown value is refused rather than carried into an
    append-only record.

    ONE precedence order, strongest declaration first (round 3 F2 restored
    a single policy after the incident repair left the executable default,
    the CLI help and the canonical design contradicting each other):

      1. `--transport`                — this invocation's explicit human word
      2. `[roles] transport`          — the repository's standing declaration
      3. `LOUPE_TRANSPORT` in `env`   — the ENVIRONMENT's declaration: a
                                        cloud sandbox's configuration sets
                                        `paste` because bytes are the only
                                        carrier that reaches the operator's
                                        machine from there
      4. a documented provider signal — `vocab.TRANSPORT_PROVIDER_SIGNALS`,
                                        exact variable and exact value; an
                                        inference ranks below every human
                                        declaration and is admitted only
                                        with its provider/value matrix and
                                        the other-endpoint-is-local
                                        assumption stated in vocab
      5. `--local-only`               — entailed `path`: a target fetchable
                                        from no remote is reviewable only on
                                        this filesystem
      6. `vocab.TRANSPORT_EMISSION_DEFAULT` (`path`) — the workflow's
                                        declared steady case: author and
                                        reviewer on the operator's machine

    A signal-resolved or environment-resolved `paste` beside `--local-only`
    still reaches `ensure_pushed`'s contradiction refusal — the check stays
    closed because resolution here never silently reconciles the two.

    Historical READING is unchanged (`vocab.TRANSPORT_DEFAULT`): every
    envelope and record written before the attribute existed came from a
    same-filesystem loop. An EXPLICITLY empty value — config key or
    environment variable — is neither silence nor a declaration (R1-F2:
    the `or` that used to fold it to the default was the fail-open route)
    and the one lifecycle boundary refuses it by state.
    """
    if transport is not None:
        return vocab.transport_or_default(transport, "--transport")
    declared = cfg.roles.get("transport")
    if declared is not None:
        return vocab.transport_or_default(
            declared,
            f"[roles] transport in the governing config ({cfg.source})")
    env = os.environ if env is None else env
    if vocab.TRANSPORT_ENV in env:
        return vocab.transport_or_default(
            env[vocab.TRANSPORT_ENV],
            f"the {vocab.TRANSPORT_ENV} environment declaration")
    for variable, value, entailed in vocab.TRANSPORT_PROVIDER_SIGNALS:
        if env.get(variable) == value:
            return entailed
    return (vocab.TRANSPORT_PATH if local_only
            else vocab.TRANSPORT_EMISSION_DEFAULT)


def resolve_debug(cfg: Config, debug: bool | None = None) -> bool:
    """Whether this emission stamps a debug round (decided 2026-08-31).

    The same precedence shape as `resolve_transport`, cut to the three
    states this value has, and for the same reason: a default that depends
    on whoever runs `handoff` remembering a flag is not a default.
    Strongest declaration first:

      1. `--debug` / `--no-debug` — this invocation's explicit human word,
         in either direction; the CLI passes None when neither was given
      2. `[roles] debug`          — the repository's standing declaration,
                                    for a repository whose every round
                                    should ask the reviewer to critique
                                    the tool (the tool's own workbench is
                                    the motivating case)
      3. off — the built-in default. The stamp spends reviewer attention
         on tool critique, so it is asked for by declaration, never
         assumed; a repository that declares nothing gets none.

    No environment or provider step: unlike transport, nothing about an
    execution environment entails a debug round.
    """
    if debug is not None:
        return bool(debug)
    declared = cfg.roles.get("debug")
    if declared is not None:
        return bool(declared)
    return False


def resolve_review_default(cfg: Config) -> str:
    """Whether review is the default end of an implementation session
    (ruled 2026-09-03, brief `review-by-default`).

    The precedence shape of `resolve_debug`, cut to two states, because
    there is no flag to add: the value describes what an AGENT does at the
    end of a session, and no invocation of this tool is that moment. So:
    `[roles] review_default` when the repository declares it, else
    `vocab.REVIEW_DEFAULT_DEFAULT` (`on`) — and an undeclared repository is
    reported in `decide`, which is how the silence gets answered once.

    Nothing in the tool gates on this. It is resolved here so that one
    reader owns the value, refuses a declaration outside the vocabulary,
    and the adapters read a resolved answer rather than parsing config.
    """
    return vocab.review_default_or_default(
        cfg.roles.get("review_default"),
        f"[roles] review_default in the governing config ({cfg.source})")


def resolve_enforcement(cfg: Config) -> str:
    """Which approval pathway the repository declares (ruled 2026-09-03,
    brief `approval-pathway-lock-in`).

    Same shape and same reason as `resolve_review_default`. The one thing
    the tool does with it is `check_enforcement` below; posting the
    approval and opening the pull request are the agents' steps with `gh`,
    outside a tool that touches no forge.
    """
    return vocab.enforcement_or_default(
        cfg.roles.get("enforcement"),
        f"[roles] enforcement in the governing config ({cfg.source})")


class EnforcementUnsatisfiable(RuntimeError):
    """`pr-approval` declared, and the branch under review is the remote's
    DEFAULT branch (brief `approval-pathway-lock-in`, item 2).

    The matrix that brief locks in has one row that could not be assigned
    while the state was reachable: a project committing straight to its
    default branch has no pull request, so there is nothing for the
    approval to bind to and the declared pathway silently does not apply.
    Declaring `pr-approval` is the statement that it does apply, so the
    state is refused at the author's door rather than discovered at merge
    time, when the round is already spent.

    Raised BEFORE anything is committed, pushed, run or emitted. Its
    remedy is a person's — branch the work, or declare the pathway this
    project actually uses — so the CLI maps it to `blocked`.
    """

    def __init__(self, message: str, remedy: str):
        super().__init__(message)
        self.remedy = remedy


def check_enforcement(cfg: Config, git=None) -> None:
    """Refuse `handoff` on the remote's default branch under `pr-approval`.

    `git` is the injectable runner every other refusal in this module uses,
    so the state is testable without a network. Two reads answer "is this
    the default branch", in the order that costs nothing first: the
    remote-tracking symbolic ref this clone already holds, then
    `ls-remote --symref`, which asks the remote itself. A remote that
    answers neither is not evidence that the branch is safe, so silence
    passes: the refusal states a fact it established, never one it guessed.

    F2: the remote tested is `_resolve_push_destination`'s answer — the
    same one `ensure_pushed` will push to — never a remote picked because
    it happens to be named `origin`. Several remotes with no derivable
    destination is not evidence either way, so it is left for
    `ensure_pushed`'s own refusal of that state, later in the same verb
    and still before any push, gate or ledger write.
    """
    if resolve_enforcement(cfg) != vocab.ENFORCEMENT_PR_APPROVAL:
        return
    repo = cfg.repo_root
    run = git or (lambda *a: _git(repo, *a, ceiling=git_ceiling(cfg)))
    try:
        branch = run("rev-parse", "--abbrev-ref", "HEAD")
    except RuntimeError:
        return
    if branch == "HEAD":
        return                       # detached: `ensure_pushed` refuses it
    remotes = [r for r in run("remote").splitlines() if r.strip()]
    if not remotes:
        return                       # no remote has a default branch
    destination = _resolve_push_destination(repo, branch, remotes, run)
    remote, merge_ref = destination
    destination_branch = _destination_branch(remote, merge_ref)
    default = ""
    remote_prefix = f"refs/remotes/{remote}/"
    try:
        ref = run("symbolic-ref", f"refs/remotes/{remote}/HEAD")
        default = ref[len(remote_prefix):] if ref.startswith(remote_prefix) else ""
    except RuntimeError:
        default = ""
    if not default:
        try:
            for line in run("ls-remote", "--symref", remote, "HEAD").splitlines():
                if line.startswith("ref:"):
                    symref = line.split()[1]
                    heads_prefix = "refs/heads/"
                    default = (symref[len(heads_prefix):]
                               if symref.startswith(heads_prefix) else "")
                    break
        except RuntimeError:
            return
    if not default or default != destination_branch:
        return
    raise EnforcementUnsatisfiable(
        f"[roles] enforcement = {vocab.ENFORCEMENT_PR_APPROVAL!r} declares "
        f"that the approval rides a pull request, and destination "
        f"{remote}:{merge_ref} is {remote}'s default branch "
        f"({destination_branch}): there is no pull request for a commit "
        f"that is already on it, so nothing would carry the approval and "
        f"the declared pathway would silently not apply",
        remedy=f"the author moves the work onto a branch and opens a pull "
               f"request for it (outside this tool, which touches no "
               f"forge), then re-runs; or the repository declares "
               f"`{vocab.toml_line('roles.enforcement', vocab.ENFORCEMENT_NONE)}`"
               f" under [roles], which is the honest declaration for a "
               f"project that commits to its default branch")


class AuthorityAbsent(RuntimeError):
    """The reviewed commit carries no configuration of its own (RVW-T17,
    round 6 F1's rule at its verified site).

    Raised from `ensure_pushed` AFTER the commit and BEFORE the push, so
    it names an artifact that exists rather than one that was predicted.
    Its remedy is the author's — the only party who can commit the rules —
    which is why it carries one at all: round 7 F3 found a preflight
    refusal reaching agents under a neighbour's recovery sentence, and a
    blocked exit has no runnable `next` to fall back on. The CLI maps this
    to `blocked`."""

    def __init__(self, message: str, remedy: str):
        super().__init__(message)
        self.remedy = remedy


def _config_basename(cfg) -> str:
    """The configuration filename, from the config layer rather than typed
    here — the same source `transport`'s refusals name it from, so the two
    ends cannot drift on what a reviewed commit is being asked to carry."""
    from . import config as _config
    return _config.CONFIG_BASENAME


class ReferenceUnbound(RuntimeError):
    """A REQUIRED reference the reviewer could never retrieve from the
    target tree (round-2 F3). Required means the reviewer must read it,
    and `take` reads references from the target commit — so a digest over
    ignored or untracked worktree bytes binds nothing any fetchable commit
    carries: it produces a manifest entry only the emitting machine can
    satisfy, and an unavailable required reference forbids the clean
    verdict the round exists to seek. The CLI maps this to `blocked`."""

    def __init__(self, message: str, remedy: str):
        super().__init__(message)
        self.remedy = remedy


def _reference_object_state(repo: Path, run, raw: str,
                            candidate: Path) -> str | None:
    """Why the object at a tracked, present reference cannot be read the
    same way by both sides — or None when it can (round 3 F1).

    Two authorities, because the seam can be reached in two orders. The
    INDEX says what the target tree will carry, and is the authority for a
    path whose worktree state is ordinary. The WORKTREE overrides it for
    the one ordering the index cannot describe yet: `handoff` commits what
    the disk carries, so a regular file replaced by a link since the last
    commit reaches the target as a link while `ls-files --stage` still
    reports 100644.

    A directory prefix — tracked entries below `raw`, no entry AT it — is
    valid and needs no mode: a directory reference is never digested, and
    `take` labels it present or absent from the target tree.
    """
    if (repo / candidate).is_symlink():
        return refs.object_error(refs.MODE_SYMLINK)
    # `-z`: NUL-separated and never quoted, so a non-ASCII path compares
    # equal to the path the claim declared instead of arriving as git's
    # `"caf\303\251.md"` escaping.
    raw_entries = run("ls-files", "--stage", "-z", "--", raw)
    for entry in raw_entries.split("\0"):
        if not entry or "\t" not in entry:
            continue
        meta, listed = entry.split("\t", 1)
        if listed != raw:
            continue                   # an entry BELOW a directory prefix
        return refs.object_error(meta.split()[0])
    return None


def check_references(cfg: Config, references, git=None) -> None:
    """Refuse, before the ledger, the push, the gates and emission, every
    reference the wire cannot carry — and every REQUIRED one the target
    tree cannot supply.

    Two layers with different reach, because requiredness decides a policy
    and not a syntax (round 4 F4):

    THE PATH, checked for EVERY reference (`refs.path_error`). Ordinary
    relative paths and non-ASCII names are valid; whitespace, control and
    invisible characters, a leading `-`, a trailing `/`, `.` and `..`
    segments, doubled separators and absolute paths are refused. The
    manifest line is whitespace-delimited and the reviewer's parser reads
    what the emitter renders: an advisory row with a space in its path came
    back `unrecognised`, which is no advisory state at all. Requiredness
    cannot change what the line can carry.

    THE BINDING, checked for REQUIRED references only, in the order a
    filesystem allows the question to be asked (round 4 F5):

    * the OBJECT MODE first, from the index and an lstat — never following
      the final component. A tracked SYMLINK and a GITLINK are refused,
      because emission and `take` cannot derive the same bytes from
      either. Following first meant a dangling link was reported as a
      deleted file and a link pointing outside the root as an escaping
      path: fail-closed, but under a name that was false and a remedy that
      did not describe the fix.
    * then the states that genuinely depend on the referent: escaping the
      repository root through a resolved path, tracked (the handoff's own
      commit carries the worktree bytes to the target tree), present,
      IGNORED (round-2 F3's state: a digest over bytes `.gitignore`
      guarantees no commit will ever carry), untracked, or missing — a
      deleted tracked file included, since the auto commit would carry the
      deletion.

    ADVISORY references keep the §5.1 three-state rendering — file-with-
    digest, directory, declared UNAVAILABLE — because declared-unavailable
    is the author's honesty mechanism for material that genuinely cannot
    travel, and that state is the reviewer's to weigh, not this boundary's
    to forbid. Only their PATH is constrained.
    """
    repo = cfg.repo_root
    run = git or (lambda *a: _git(repo, *a, ceiling=git_ceiling(cfg)))
    for i, ref in enumerate(references or []):
        raw = str(ref.get("path", ""))
        required = ref.get("required", True)
        where = f"references[{i}] ({raw!r}, " \
                f"{'required' if required else 'advisory'})"

        grammar = refs.path_error(raw)
        if grammar is not None:
            raise ReferenceUnbound(
                f"{where} is {grammar}",
                remedy=f"a person renames the referenced file, points the "
                       f"reference at a path the manifest line can carry, "
                       f"or drops the reference where the envelope's own "
                       f"content already carries the evidence")
        if not required:
            continue

        def refuse(state: str) -> "ReferenceUnbound":
            return ReferenceUnbound(
                f"{where} is {state}: the reviewer's `take` reads "
                f"references from the target tree, so this required "
                f"reference could never be retrieved or digest-checked "
                f"there (round-2 F3)",
                remedy=f"a person tracks {raw or 'the referenced path'} in "
                       f"the repository, points the reference at a tracked "
                       f"path, or drops it where the envelope's own content "
                       f"already carries the evidence")

        candidate = Path(raw)
        if candidate.is_absolute():
            raise refuse("an absolute path, which no repository tree "
                         "carries")
        # The object's own mode, before anything follows the link: what the
        # target tree carries is decided by the index and an lstat, and
        # both are answers about the reference itself rather than about
        # whatever it points at.
        object_state = _reference_object_state(repo, run, raw, candidate)
        if object_state is not None:
            raise refuse(object_state)
        try:
            inside = (repo / candidate).resolve().is_relative_to(
                repo.resolve())
        except OSError:
            inside = False
        if not inside:
            raise refuse("a path escaping the repository root")
        tracked = bool(run("ls-files", "--", raw))
        exists = (repo / candidate).exists()
        if tracked and exists:
            continue
        if not exists:
            raise refuse(
                "tracked but missing from the working tree — the commit "
                "this handoff makes would carry its deletion" if tracked
                else "missing entirely")
        try:
            run("check-ignore", "-q", "--", raw)
            ignored = True
        except RuntimeError:
            ignored = False
        raise refuse(
            "ignored — a digest over bytes only this machine holds"
            if ignored else "untracked, so no commit carries it yet")


# The former name, kept so a caller that learned it does not silently get
# the old two-layer behaviour from a stale import.
check_required_references = check_references


def resolve_roles(cfg: Config, author: str | None = None,
                  reviewer: str | None = None) -> tuple[str, str]:
    """The effective (author, reviewer) for one emission — design §4's
    precedence, which the published specification has stated since the first
    extraction and the CLI never shipped (the first per-project onboarding
    hit the absence): the repo config says what assignments are PERMITTED at
    all, a per-invocation stamp selects WITHIN that permission, and
    unassigned refuses.

    A flag selects; it never widens. An identity outside the declared
    permitted list is refused, and so is a flag against a repo that declares
    no list — with nothing declared there is no permission to select within,
    and an unconstrained flag would let any string into an append-only
    record. That a flag names the same identity the config defaults to does
    not exempt it: the license for per-invocation selection is the declared
    list, not the harmlessness of one value. `rejected_reviewers` outranks
    the permitted list. Every invariant — NON-EMPTY on both sides (round-2
    F2: unassigned refuses here, not in the emitter downstream of the
    ledger and the push, and a malformed permitted list carrying "" cannot
    admit emptiness as a selection), rejection, permission where a list is
    declared, self-review — binds the EFFECTIVE identity, flagged or
    config-defaulted alike (round-1 F1): each is invalid in every
    envelope, and refusing before the commit, the push and the gate run is
    the boundary's promise, not an optimisation for one input source.
    """
    roles = cfg.roles

    def _selected(side: str, value: str | None, permitted_key: str) -> str:
        if value is None:
            return roles.get(side) or ""
        permitted = [x.lower() for x in roles.get(permitted_key) or []]
        if not permitted:
            raise RoleSelectionError(
                f"--{side} {value!r} selects a per-invocation {side}, but "
                f"the repo config declares no {permitted_key}: there is no "
                f"permitted list to select within (§4)",
                remedy=f"a person declares {permitted_key} in the governing "
                       f"config ({cfg.source}), or the flag is dropped")
        if value.lower() not in permitted:
            raise RoleSelectionError(
                f"--{side} {value!r} is not in {permitted_key} "
                f"{permitted}: a flag selects within the declared "
                f"permission, it does not widen it (§4)",
                remedy=f"a person adds {value!r} to {permitted_key} in the "
                       f"governing config ({cfg.source}), or the flag "
                       f"names a permitted identity")
        return value

    effective_author = _selected("author", author, "permitted_authors")
    effective_reviewer = _selected("reviewer", reviewer, "permitted_reviewers")

    # Round-1 F1: the invariants bind the EFFECTIVE identity, whatever its
    # source. The first version checked `rejected_reviewers` only when the
    # flag was present, so a config whose DEFAULT reviewer was rejected
    # resolved cleanly and the refusal arrived from the request validator —
    # after the commit, the push and the gate run this boundary exists to
    # precede. Flag-selected and config-defaulted identities pass the same
    # checks; only the remedy differs, because only the recovery does.
    def _source(side: str, flagged: str | None) -> str:
        return (f"--{side} {flagged!r}" if flagged is not None
                else f"the config's default {side} ({cfg.source})")

    # Round-2 F2: empty is a member of the domain, and it refuses HERE.
    # The first version returned ('', '') and left "unassigned refuses" to
    # the emitter — downstream of the ledger and of the commit/push this
    # boundary promises to precede — and a malformed permitted list
    # containing "" could even admit the empty string as a selection. No
    # effective identity, from any source, past this point.
    for side, value, flagged in (("author", effective_author, author),
                                 ("reviewer", effective_reviewer, reviewer)):
        if not value:
            raise RoleSelectionError(
                f"{_source(side, flagged)} resolves to no {side} at all: "
                f"unassigned refuses at resolution, before the ledger, the "
                f"commit, the push and the gate run (§7 — silence must not "
                f"pick a direction)",
                remedy=f"a person assigns {side} in the governing config "
                       f"({cfg.source}), or selects one with --{side} from "
                       f"a declared permitted list")

    rejected = [x.lower() for x in roles.get("rejected_reviewers") or []]
    if effective_reviewer.lower() in rejected:
        raise RoleSelectionError(
            f"{_source('reviewer', reviewer)} resolves to "
            f"{effective_reviewer!r}, which is rejected pending a role "
            f"decision (§7, §10.7), whatever list also carries it",
            remedy="a person reopens the rejected-reviewer decision in the "
                   "governing config, or names another identity"
                   + ("" if reviewer is not None
                      else " as the config's default reviewer"))
    for side, value, key, flagged in (
            ("author", effective_author, "permitted_authors", author),
            ("reviewer", effective_reviewer, "permitted_reviewers", reviewer)):
        permitted = [x.lower() for x in roles.get(key) or []]
        if permitted and value and value.lower() not in permitted:
            # A flagged non-member was already refused in _selected; this
            # reaches only the defaulted source, mirroring the validator's
            # R-AUTHOR-UNPERMITTED / R-REVIEWER-UNPERMITTED before any
            # side effect instead of after emission.
            raise RoleSelectionError(
                f"{_source(side, flagged)} resolves to {value!r}, which is "
                f"not in {key} {permitted}",
                remedy=f"a person repairs the governing config "
                       f"({cfg.source}): its default {side} is outside its "
                       f"own {key}")
    if (effective_author and effective_reviewer
            and effective_author.lower() == effective_reviewer.lower()):
        raise RoleSelectionError(
            f"author and reviewer both resolve to "
            f"{effective_author!r}: an agent never reviews its own diff",
            remedy="a person assigns two different identities — by flag or "
                   "in the governing config — before anything is emitted")
    return effective_author, effective_reviewer


def emit_request(cfg: Config, ledger: Ledger, claim: dict,
                 base: str | None = None, head: str | None = None,
                 reachability: dict | None = None,
                 author: str | None = None,
                 reviewer: str | None = None,
                 transport: str | None = None,
                 debug: bool = False, *, lineage: str,
                 local_gates: "LocalGates | None" = None) -> str:
    if not cfg.taxonomy_declared:
        raise RuntimeError(
            "no taxonomy declared for this repo: refusing to emit an envelope "
            "the reviewer would have to invent a taxonomy to answer (§5.2). "
            "Declare [taxonomy] in review.toml")
    if reachability is None:
        # §9bis.4: emission refuses on an unreachable target, it does not
        # warn. The record comes from ensure_pushed (the CLI calls it); a
        # caller without one has not made the target fetchable.
        raise RuntimeError(
            "no reachability record: the target must be pushed — or "
            "explicitly declared local-only — before emission (§9bis.4). "
            "Call ensure_pushed first, or emit through the CLI, which does")

    repo = cfg.repo_root
    # Always resolve to a real object id: an envelope that binds a symbolic
    # ref binds nothing, since the ref moves (round-3 F16's SHA-shape rule
    # caught this emitter embedding "HEAD" verbatim).
    head = _git(repo, "rev-parse", head or "HEAD")
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if reachability.get("sha") != head:
        raise RuntimeError(
            f"stale reachability record: it observed "
            f"{reachability.get('sha')}, but the envelope binds {head} "
            f"(§9bis.4) — re-run ensure_pushed against the current tip")
    verdicts = [e for e in ledger.current(lineage)
                if e.get("event") == "verdict"]
    if base is None:
        if not verdicts:
            raise RuntimeError("no prior verdict in the ledger; pass --base")
        base = max(verdicts, key=lambda e: e["round"])["sha"]
    base = _git(repo, "rev-parse", base)
    if not _is_ancestor(repo, base, head):
        raise RuntimeError(
            f"base {base} is not an ancestor of head {head}: pushing the "
            f"branch does not make the base fetchable, so the reviewer "
            f"could not compute "
            f"`{paths.command(*paths.lits('git', 'diff'), f'{base[:12]}...{head[:12]}')}` "
            f"(§9bis.4)")
    round_no = max((e["round"] for e in verdicts), default=0) + 1

    # Explicit values are the CLI's resolved per-invocation selection (§4);
    # None means no selection was made and the config's default direction
    # holds — the pre-flag behaviour, byte for byte.
    author = author if author is not None else (cfg.roles.get("author") or "")
    reviewer = (reviewer if reviewer is not None
                else (cfg.roles.get("reviewer") or ""))
    # What actually carried the envelope. Until 2026-08-13 this was the
    # literal string "user", which became false the moment the author process
    # began invoking the reviewer directly: the stamp asserted a human relay
    # that was not there. The tool cannot observe its own transport, so it
    # takes the value and refuses to invent one.
    relay = (claim.get("relay") or cfg.roles.get("relay")
             or "unrecorded (transport not declared)")
    # RVW-T11. `relay` says WHO carries the envelope; this says whether the
    # two ends share a filesystem, which is the other half of the same fact
    # and the half the printed commands depend on. Resolved rather than read
    # straight from config so an unknown value is refused here — at the
    # boundary that stamps it — and not at whichever reader trips on it
    # first. The CLI resolves it earlier for the cache key and passes the
    # result; a direct caller that names none gets the same answer.
    transport = resolve_transport(cfg, transport)
    if not author or not reviewer:
        raise RuntimeError("roles unassigned and no config assigns them: "
                           "refusing to emit (§7 — silence must not pick a "
                           "direction)")

    shape = diff_shape(repo, base, head)
    # 0.25.0 (public issue #4): the claim's record-bound members. The CLI
    # already refused what the ledger could refuse before anything was
    # committed; this is the same function again, for the states the
    # request renders. The gate ids are judged against the governing
    # manifest `cfg` now is — the target's, which did not exist before the
    # commit. The hand-off already judged them in its gate-before-push
    # callback, before any gate ran or anything was pushed; this is the same
    # function for a caller that reached `emit_request` directly.
    record = (check_claim_record(claim, ledger, lineage, cfg)
              if claim.get("carried_findings") or claim.get("attestation_map")
              else None)
    check_map_gates(claim, cfg, head)
    dirty = _git(repo, "status", "--porcelain")
    # Round-4 F8: the declared gate manifest reaches the metric here, not only
    # through the ledger API a test calls directly. Round-4 F5: so does the
    # configured token budget, which is None when none is declared.
    effective_cap = ledger.effective_round_cap(cfg.round_cap, lineage)
    from .transport import target_blocking
    report_md = render_report_md(ledger.report(
        lineage, effective_cap, gate_manifest=cfg.gate_ids,
        token_budget=cfg.token_budget,
        # 0.26.0: a run's blocking state, judged by the commit its finding
        # was ruled on — not by this emission's target, a later commit.
        blocking_at=target_blocking(cfg)),
        pending_round=round_no)
    # Ruling 1 of 2026-09-06: the cross-lineage notice, ON THE ENVELOPE'S
    # FACE. Empty when no standing identity of this lineage is also ruled in
    # another open one, which is every ordinary round.
    cross_md = render_cross_lineage_md(ledger.cross_lineage_notices(lineage))

    tree_state = ("DIRTY — attestations from a dirty tree are not attestations"
                  if dirty else "clean at emission")
    # Brief `take-objective-map` item 2: a 12,520-line diff paid full
    # attention on rendered inventories the author never wrote. The span
    # separates them so the reviewer can skip them BY RULE — the rule being
    # the repository's own git attributes at the target, which is the only
    # declaration that travels with the commit to the machine reading it.
    generated = generated_at_target(repo, head, [e["target_path"]
                                                 for e in shape["entries"]])
    generated_suffix, generated_lines = _generated_span(shape, generated)
    shown_paths = ([e["path"] for e in shape["entries"]
                    if e["target_path"] not in generated]
                   if generated_lines else shape["file_list"])
    changed = "\n".join(f"  {p}" for p in shown_paths)
    if generated_lines and not shown_paths:
        changed = "  (none — every changed path is generated by declaration)"
    # `local_gates`: the half the hand-off ran before its push (0.25.0) —
    # carried, never re-run; only the CI-attested half is awaited here.
    attestations = run_gates(cfg, head, base=base, prior=local_gates)
    ev_cap = _attestation_block(cfg.wrapper_tag, attestations)
    ev_not = "\n".join(f"  - {x}" for x in claim.get("evidence_not_captured", []))
    stops = "\n".join(f"  - {x}" for x in claim.get("stop_conditions", []))
    hand = "\n".join(f"  {i}. {x}"
                     for i, x in enumerate(claim.get("hand_back", []), 1))
    not_done = "\n".join(f"  - {x}" for x in claim.get("deliberately_not", []))
    contract = "\n".join(claim.get("contract", []))

    parts = [
        # `tool` is what the emitting installation IS, not what it calls
        # itself: the reader compares it with its own and says so, because
        # two installations that disagree produce two different relays from
        # one sound envelope (lineage 7 round 1).
        f'<{cfg.wrapper_tag}-review-request sha="{head}" branch="{branch}" '
        f'author="{author}" reviewer="{reviewer}" round="{round_no}" '
        # Round-2 F5: THE REVIEW THIS ENVELOPE BELONGS TO, on its face. The
        # emitter has always known the id; keeping it here left `path` and
        # `paste` ingress with nothing to tell a continuation from a new
        # review, and a reviewer taking two branch reviews merged both into
        # one lineage. Stamped, one id names one review on both machines,
        # which is what the `git` reference already carried. An older
        # reader tolerates the unlisted attribute (§3.1) and an envelope
        # emitted before this carries none, which `take_lineage` reads as
        # the legacy path rather than as a defect.
        f'lineage="{lineage}" '
        f'transport="{transport}" tool="{tool_identity()}" '
        f'shape="{shape_identity()}"'
        # The debug stamp rides only when asked for (2026-08-31): an
        # unlisted attribute is tolerated by older readers, deliberately.
        + (f' {vocab.DEBUG_ATTR}="{vocab.DEBUG_TOOL_FEEDBACK}"'
           if debug else "")
        + ">",
        f"Roles: author={author} · reviewer={reviewer} · relay={relay} · "
        f"transport={transport}. "
        f"Per-invocation stamp, overriding the default direction for this "
        f"artifact only (the agents' standing instructions).",
        "",
        f"Target: {head}",
        _base_line(base, round_no, ledger),
        wire.executable_stamp("Diff", paths.diff_command(repo, base, head)),
        # 0.25.0: the same diff limited to the paths the claim's
        # scope_paths match, when it declares any — an executable stamp
        # like the line above, or a line saying why none is given.
        *([_scoped_stamp(lambda *specs: paths.diff_command(
            repo, base, head, *specs), claim["scope_paths"], shape)]
          if claim.get("scope_paths") else []),
        f"Tree:   {tree_state}",
        *render_preflight_lines(reachability or {}),
        *wire.render_push_lines(reachability),
        f"Access: {claim.get('access_note', 'see reference manifest below')}",
        _round_line(round_no, effective_cap, cfg.round_cap),
        "",
        "## Taxonomy",
        "",
        _taxonomy_block(cfg),
        "",
        "## Claim",
        "",
        f"Objective / decision boundary: {claim.get('objective', '').strip()}",
        "",
        f"Self-assessed risk: {claim.get('risk', 'not stated').strip()}",
        "",
        f"What changed ({shape_line(shape)} — machine-computed"
        f"{generated_suffix}):",
        "",
        changed,
        *generated_lines,
        # 0.25.0 (public issue #4). Every block below is ADDITIVE: absent
        # from the claim, it renders nothing, and the request reads exactly
        # as a 0.24.x emitter wrote it. All of them sit in the Claim — the
        # author's section — because each is the author's declaration; the
        # machine values some of them quote are read from this envelope.
        *(["", in_scope_line] if (in_scope_line := _in_scope_line(
            shape, generated, claim.get("excluded_paths") or ())) else []),
        *(_objectives_block(claim["objectives"], shape, generated,
                            claim.get("excluded_paths") or (),
                            read_authority=_authority_reader(repo, head))
          if claim.get("objectives") else []),
        *(_carried_block(record, repo, base, head)
          if claim.get("carried_findings") else []),
        *(_attestation_map_block(record, attestations)
          if claim.get("attestation_map") else []),
        *(_observations_block(claim["observations"])
          if claim.get("observations") else []),
        "",
        "Deliberately not done:",
        not_done or "  - (nothing declared)",
        "",
        _disposition_heading(round_no),
        "",
        _ruling_pointer(cfg, ledger, round_no, lineage, transport),
        "",
        _dispositions_block(ledger, round_no - 1, lineage),
        "",
        "## Evidence",
        "",
        "Machine attestations, recorded by the runner and validated field by "
        "field (§5.1). `binding` is the",
        "one that decides whether the rest means anything: gates run in a "
        "checkout, so `executed_sha` is what",
        "actually ran and `target_sha` is only a claim until the two agree "
        "over a clean tree.",
        "",
        ev_cap,
        "",
        "NOT captured — this handoff cannot vouch for these:",
        ev_not or "  - (none)",
        "",
        report_md,  # carries its own '## Ledger report — ...' heading
        "",
        # Carries its own heading too, and renders to nothing at all when
        # this repository has one lineage open — which is the common case.
        cross_md,
        "## Contract — invariants that apply",
        "",
        contract or "(none declared)",
        "",
        "## Reference",
        "",
        _reference_block(repo, claim.get("references", []), head),
        "",
        "## Review scope",
        "",
        claim.get("review_scope", "(none declared)"),
        "",
        # The machine-held half (2026-09-18): what the hand-off's commit was
        # held to. Absent from the claim, the section says so rather than
        # printing an empty list that reads as "nothing allowed".
        *(["Scope paths:",
           *([f"  - {p}" for p in claim["scope_paths"]]
             or ["  - (declared empty: this round sweeps nothing)"]), ""]
          if claim.get("scope_paths") is not None
          else ["Scope paths: (none declared — the hand-off's sweep was not "
                "held to a path list)", ""]),
        *(_excluded_block(claim["excluded_paths"], shape)
          if claim.get("excluded_paths") is not None else []),
        "## Stop conditions",
        "",
        stops or "  - (none)",
        "",
        "## Hand-back",
        "",
        hand or "  (none)",
        "",
        _verdict_shape_block(cfg, head),
        f"</{cfg.wrapper_tag}-review-request>",
        "",
    ]
    return "\n".join(parts)


class ClaimUnreadable(RuntimeError):
    """A claim file was supplied and could not be read (round 3 F3).

    An unreadable supplied claim is an error, not an absence. "No claim was
    given" and "a claim was given and could not be read" once returned the
    same empty sentinel, and the cache skips comparison on empty — so an
    unreadable path served a warm envelope built from a DIFFERENT claim and
    reported `cached: true`.
    """


class ClaimDefective(ValueError):
    """The claim file is not a claim: one typed refusal for every way the
    author's bytes can fail to be one (lineage-3 rounds 5 and 7).

    The claim is the author's input that BECOMES the Claim and Reference
    sections. Round 5 closed one defect: read with ordinary `json.loads`,
    `"references": [must-read]` followed by `"references": []` loaded as
    `[]` and the earlier required reference was erased before emission,
    structural validation, digesting or the reviewer's probe could see it had
    ever been declared. Round 7 found that closing one defect is not closing
    a boundary — `null`, `true`, `3`, `"x"` and `[]` were admitted as claims;
    every declared field accepted any type; an unknown member was dropped in
    silence, so a misspelled `stop_condition_typo` erased a stop condition;
    and malformed JSON escaped as a raw JSONDecodeError, the one defect with
    no typed recovery at all.

    So the exception carries what is wrong, not merely which member: `defect`
    is the kind, `member` the location (`references[0].required` for a nested
    one), `detail` the sentence a person acts on. `name` is retained as the
    round-5 duplicate's member.
    """

    def __init__(self, path: Path | None, detail: str, *, defect: str,
                 member: str | None = None, name: str | None = None):
        # `path` is None where the defect is in the invocation rather than in
        # a file: an empty --claim-file names nothing to correct.
        super().__init__(detail if path is None else f"{path}: {detail}")
        self.path = path
        self.detail = detail
        self.defect = defect
        self.member = member
        self.name = name if name is not None else member


@dataclasses.dataclass(frozen=True)
class CapturedClaim:
    """One capture of the author's claim: the digest of the bytes read, the
    value parsed from THOSE bytes, and whether a claim was supplied at all.

    Frozen, and `claim` is deep-immutable (mappings proxied, sequences
    tupled). Round 6 made the read happen once; round 7 removed the last way
    to make it happen twice. `_emit` used to take `claim=None` meaning "not
    captured — load it yourself", and `parse_claim("null")` returned None, so
    a claim file whose whole content was `null` collided with the sentinel
    and the path was reopened: the emitted Claim could then come from the
    second read while the cache digest and the ledger attested the first.
    A supplied claim is now read exactly once, here, and `supplied` — not the
    value's truthiness — is what says whether there was one.
    """
    digest: str
    claim: Mapping
    supplied: bool


_EMPTY_CLAIM: Mapping = MappingProxyType({})


def _frozen(value):
    """`value` with every mapping proxied and every sequence tupled, so a
    captured claim cannot be edited between the digest and the emission."""
    if isinstance(value, dict):
        return MappingProxyType({k: _frozen(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_frozen(v) for v in value)
    return value


def _kind_of(value) -> str:
    """The JSON kind name, for a refusal that says what was supplied."""
    for kind, types in (("null", type(None)), ("boolean", bool),
                        ("number", (int, float)), ("string", str),
                        ("array", list), ("object", dict)):
        if isinstance(value, types):
            return kind
    return type(value).__name__


# Why a given member may not be blank, where the reason is worth stating.
_WHY_NONEMPTY = {
    "objective": "a claim that states no objective renders exactly like no "
                 "claim at all, and those are different states",
}

_ACTOR_RE = re.compile(vocab.ACTOR_RE)


def _check_string(path: Path, member: str, value, *, nonempty: bool) -> None:
    if not isinstance(value, str):
        raise ClaimDefective(
            path, f"member {member!r} must be a string, not "
                  f"{_kind_of(value)}", defect="type", member=member)
    if nonempty and not value.strip():
        why = _WHY_NONEMPTY.get(member.split(".")[-1].split("[")[0],
                                "an empty value declares nothing")
        raise ClaimDefective(
            path, f"member {member!r} is empty; {why}", defect="empty",
            member=member)
    # Round-8 F4: a `str` is not yet a value this tool can carry. JSON admits
    # a lone escaped surrogate — `"\ud800"` decodes to a Python string — and
    # every surface downstream is UTF-8: the envelope bytes, the digest, the
    # kept file, stdout. Admitting it on type alone let a claim reach the
    # ledger, a commit and a push before the encode that could never succeed.
    # The domain of a claim string is the Unicode scalar values, not `str`.
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ClaimDefective(
            path, f"member {member!r} carries a character the envelope "
                  f"cannot encode ({exc.reason} at position {exc.start}); a "
                  f"claim string must be UTF-8 encodable, and a lone "
                  f"surrogate is valid JSON but not a Unicode scalar value",
            defect="unicode", member=member) from exc


def _check_reference(path: Path, index: int, ref) -> None:
    where = f"references[{index}]"
    if not isinstance(ref, dict):
        raise ClaimDefective(
            path, f"{where} must be an object with a path, not "
                  f"{_kind_of(ref)}", defect="type", member=where)
    for member in ref:
        if member not in vocab.CLAIM_REFERENCE_FIELDS:
            raise ClaimDefective(
                path, f"{where} states unknown member {member!r}; a "
                      f"reference carries "
                      f"{', '.join(sorted(vocab.CLAIM_REFERENCE_FIELDS))} and "
                      f"nothing else", defect="unknown",
                member=f"{where}.{member}")
    for member in vocab.CLAIM_REFERENCE_REQUIRED:
        if member not in ref:
            raise ClaimDefective(
                path, f"{where} states no {member!r}; a pointer without one "
                      f"is a reference to nothing", defect="missing",
                member=f"{where}.{member}")
    for member, value in ref.items():
        kind = vocab.CLAIM_REFERENCE_FIELDS[member]
        if kind == "string":
            _check_string(path, f"{where}.{member}", value,
                          nonempty=member in vocab.CLAIM_REFERENCE_REQUIRED)
        elif kind == "boolean" and not isinstance(value, bool):
            raise ClaimDefective(
                path, f"{where}.{member} must be true or false, not "
                      f"{_kind_of(value)}", defect="type",
                member=f"{where}.{member}")


def validate_claim(value, path: Path) -> dict:
    """The parsed claim, checked against the ONE field authority in
    `vocab.CLAIM_FIELDS`, or ClaimDefective naming the defect.

    Closed on every axis the domain has: the top-level kind, the presence of
    what is required, the absence of what is unknown, the type of every
    declared member, and the shape of every nested reference. Nothing here
    decides what a field MEANS — that is the author's judgment, which this
    tool carries and never invents.
    """
    if not isinstance(value, dict):
        raise ClaimDefective(
            path, f"a claim is a JSON object; this file is a top-level "
                  f"{_kind_of(value)}", defect="not_object")
    for member in value:
        if member not in vocab.CLAIM_FIELDS:
            raise ClaimDefective(
                path, f"states unknown member {member!r}; the claim's members "
                      f"are {', '.join(sorted(vocab.CLAIM_FIELDS))}. An "
                      f"unknown member was once ignored in silence, so a "
                      f"misspelling erased whatever it meant to declare",
                defect="unknown", member=member)
    for member in vocab.CLAIM_REQUIRED:
        if member not in value:
            raise ClaimDefective(
                path, f"states no {member!r}, which every supplied claim must "
                      f"carry", defect="missing", member=member)
    for member, item in value.items():
        kind = vocab.CLAIM_FIELDS[member]
        if kind == "string":
            _check_string(path, member, item,
                          nonempty=member in vocab.CLAIM_NONEMPTY)
            # Round 3 F3: `relay` is provenance — who carried the request —
            # closed to the same one-token identifier grammar an author or
            # reviewer name is written in. A blank value is not a defect
            # here (emission falls back to the configured relay exactly as
            # an absent member does); anything ELSE that fails the grammar
            # is refused before it can be rendered as if it named an actor.
            #
            # Round 5 F3: this used to validate `item.strip()` while
            # emission below (`relay = claim.get("relay") or
            # cfg.roles.get("relay") or ...`) renders the ORIGINAL,
            # unstripped member — so `" user "` matched the stripped copy,
            # passed, and rendered `relay= user  ` on the Roles line. No
            # stripping here either: the grammar is checked against the
            # exact value emission will render, and "blank" is exactly what
            # emission's `or` already treats as absent — a falsy `""`, not
            # a stripped one.
            if member == "relay" and item and not \
                    _ACTOR_RE.match(item):
                raise ClaimDefective(
                    path,
                    f"member 'relay' is {item!r}, which is not "
                    f"{vocab.ACTOR_WANT} — relay says WHO carried the "
                    f"request, never what to do with the answer; a "
                    f"stopping instruction belongs under 'hand_back', the "
                    f"claim's dedicated field for exactly that",
                    defect="shape", member=member)
        elif kind == "list_of_string":
            if not isinstance(item, list):
                raise ClaimDefective(
                    path, f"member {member!r} must be a list of strings, not "
                          f"{_kind_of(item)}", defect="type", member=member)
            for i, element in enumerate(item):
                _check_string(path, f"{member}[{i}]", element, nonempty=False)
        elif kind == "references":
            if not isinstance(item, list):
                raise ClaimDefective(
                    path, f"member {member!r} must be a list of reference "
                          f"objects, not {_kind_of(item)}", defect="type",
                    member=member)
            if not item:
                raise ClaimDefective(
                    path, f"member {member!r} is an empty list; a reference "
                          f"manifest with no entries hands the reviewer "
                          f"nothing to read, which is the missing-member "
                          f"state spelled differently", defect="empty",
                    member=member)
            for i, ref in enumerate(item):
                _check_reference(path, i, ref)
        elif kind == "list_of_object":
            _check_object_list(path, member, item)
    return value


# ------------------------------------------ the claim's object-list members
#
# 0.25.0 (public issue #4, brief `take-objective-map`). A fourth member kind:
# a list of objects, each member with its own closed field table in
# `vocab.CLAIM_OBJECT_LIST_FIELDS`. Every defect is the same typed refusal as
# every other claim defect, located by member path.

#: The fingerprint grammar, derived from the minting function rather than
#: restated: the version prefix and the digest length `fingerprint.compute`
#: actually emits.
def _fingerprint_re() -> "re.Pattern":
    from .fingerprint import FP_VERSION, compute
    width = len(compute("", "", "", "").split(":", 1)[1])
    return re.compile(rf"{re.escape(FP_VERSION)}:[0-9a-f]{{{width}}}")


_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_GATE_ID_RE = re.compile(vocab.GATE_ID_RE)


def _origin_parts(value: str):
    """`(lineage, round)` for an origin in the `git:` round-reference body
    grammar (`<lineage id>/<round>`, round >= 1), else None — the grammar is
    `transport`'s, read rather than restated."""
    from .transport import _ROUND_REFERENCE_RE
    m = _ROUND_REFERENCE_RE.match(value)
    if not m or int(m.group(2)) < 1:
        return None
    return m.group(1), int(m.group(2))


_OBJECT_FIELD_WANT = {
    "fingerprint": "a finding fingerprint as a verdict prints it "
                   "(`fp2:` and 16 hex digits)",
    "origin": "`<lineage id>/<round>`, the verdict the finding was ruled in "
              "(round 1 or later)",
    "carried_outcome": f"one of {', '.join(vocab.CARRIED_OUTCOMES)}",
    "commits": "a 40-character lowercase commit id",
    "gate_id": f"a gate id ({vocab.GATE_ID_RE}, at most "
               f"{vocab.GATE_ID_MAX} characters)",
    "path": "a repository path a reference manifest line can carry",
}


def _check_field_list(path: Path, where: str, value) -> None:
    """A non-empty list of non-empty strings."""
    if not isinstance(value, list):
        raise ClaimDefective(
            path, f"{where} must be a list of strings, not {_kind_of(value)}",
            defect="type", member=where)
    if not value:
        raise ClaimDefective(
            path, f"{where} is an empty list; it declares nothing — omit the "
                  f"field instead", defect="empty", member=where)
    for i, element in enumerate(value):
        _check_string(path, f"{where}[{i}]", element, nonempty=True)


def _check_object_field(path: Path, where: str, kind: str, value) -> None:
    if kind in ("list_of_string", "scope_paths", "commits"):
        _check_field_list(path, where, value)
        if kind == "commits":
            for i, sha in enumerate(value):
                if not _COMMIT_RE.fullmatch(sha):
                    raise ClaimDefective(
                        path, f"{where}[{i}] is {sha!r}, which is not "
                              f"{_OBJECT_FIELD_WANT[kind]}: an abbreviated "
                              f"or symbolic name binds nothing a reviewer "
                              f"can check", defect="shape",
                        member=f"{where}[{i}]")
        return
    _check_string(path, where, value, nonempty=True)
    if kind == "path":
        # The reference grammar, not a second one: an objective's authority
        # is read at the target exactly as a reference is, so it must be a
        # path a manifest line can carry and `git show <sha>:<path>` reads.
        why = refs.path_error(value)
        if why is not None:
            raise ClaimDefective(
                path, f"{where} is {value!r}, which is not "
                      f"{_OBJECT_FIELD_WANT[kind]}: {why}", defect="shape",
                member=where)
        return
    ok = {"string": lambda v: True,
          "fingerprint": lambda v: bool(_fingerprint_re().fullmatch(v)),
          "origin": lambda v: _origin_parts(v) is not None,
          "carried_outcome": lambda v: v in vocab.CARRIED_OUTCOMES,
          "gate_id": lambda v: (bool(_GATE_ID_RE.fullmatch(v))
                                and len(v) <= vocab.GATE_ID_MAX)}[kind]
    if not ok(value):
        raise ClaimDefective(
            path, f"{where} is {value!r}, which is not "
                  f"{_OBJECT_FIELD_WANT[kind]}", defect="shape", member=where)


def _check_object_list(path: Path, member: str, item) -> None:
    """One list-of-object member, closed on every axis: the container, its
    emptiness, each element's kind, unknown fields, missing required ones,
    and every field's type and grammar."""
    table = vocab.CLAIM_OBJECT_LIST_FIELDS[member]
    required = vocab.CLAIM_OBJECT_REQUIRED[member]
    if not isinstance(item, list):
        raise ClaimDefective(
            path, f"member {member!r} must be a list of objects, not "
                  f"{_kind_of(item)}", defect="type", member=member)
    if not item:
        raise ClaimDefective(
            path, f"member {member!r} is an empty list; it declares nothing, "
                  f"which is the absent state spelled differently — omit the "
                  f"member instead", defect="empty", member=member)
    seen_fps: dict[str, int] = {}
    for i, element in enumerate(item):
        where = f"{member}[{i}]"
        if not isinstance(element, dict):
            raise ClaimDefective(
                path, f"{where} must be an object, not {_kind_of(element)}",
                defect="type", member=where)
        for field in element:
            if field not in table:
                raise ClaimDefective(
                    path, f"{where} states unknown field {field!r}; an entry "
                          f"of {member!r} carries "
                          f"{', '.join(sorted(table))} and nothing else",
                    defect="unknown", member=f"{where}.{field}")
        for field in required:
            if field not in element:
                raise ClaimDefective(
                    path, f"{where} states no {field!r}, which every entry of "
                          f"{member!r} must carry", defect="missing",
                    member=f"{where}.{field}")
        for field, value in element.items():
            _check_object_field(path, f"{where}.{field}", table[field], value)
        for group in vocab.CLAIM_OBJECT_TOGETHER.get(member, ()):
            stated = [f for f in group if f in element]
            if stated and len(stated) != len(group):
                absent = [f for f in group if f not in element]
                raise ClaimDefective(
                    path, f"{where} states {', '.join(repr(f) for f in stated)} "
                          f"without {', '.join(repr(f) for f in absent)}; an "
                          f"entry of {member!r} declares "
                          f"{' and '.join(repr(f) for f in group)} together or "
                          f"not at all, since either alone is compared with "
                          f"nothing", defect="missing",
                    member=f"{where}.{absent[0]}")
        # A carried fingerprint is the join key the attestation map resolves
        # against; carried twice, it names two outcomes for one finding.
        if member == "carried_findings":
            fp = element["fingerprint"]
            if fp in seen_fps:
                raise ClaimDefective(
                    path, f"{where}.fingerprint {fp!r} is already carried at "
                          f"{member}[{seen_fps[fp]}]; one finding is carried "
                          f"once, with one outcome", defect="duplicate",
                    member=f"{where}.fingerprint")
            seen_fps[fp] = i


def _squash(text) -> str:
    return " ".join(str(text or "").split())


def _origin_required(cfg: Config, ledger: Ledger, origin_lineage: str,
                     origin_round: int, verdict_event: dict, ident: str):
    """What the origin verdict required of the finding with identity
    `ident`, read from the bytes THIS machine kept — or None when they are
    not kept here or do not reproduce the recorded digest."""
    from . import transport
    carrier = (ledger.carrier_lineage_for_sha(verdict_event.get("sha"),
                                              prefer=origin_lineage)
               or origin_lineage)
    _, text = transport.read_kept(cfg, origin_round, "verdict",
                                  lineage=carrier,
                                  digest=verdict_event.get("source_digest")
                                  or "")
    if text is None:
        return None
    for f in wire.parse_verdict(text).findings:
        if ledger.resolve(f.fingerprint()) == ident:
            return f.required_outcome
    return None


def check_map_gates(claim, cfg: Config, head: str) -> None:
    """Refuse an `attestation_map` row naming a gate the GOVERNING manifest
    does not declare — a map row can only point at an attestation the
    request will carry.

    One function, two callers (0.25.0): the hand-off's gate-before-push
    callback, where it runs after the commit (the target's manifest exists
    only then) and BEFORE any gate runs or anything is pushed; and
    `emit_request`, for a direct caller that reached it another way."""
    unknown_gates = sorted({row["gate"]
                            for row in claim.get("attestation_map") or ()
                            if row["gate"] not in cfg.gate_ids})
    if unknown_gates:
        from . import transport as _transport
        raise _transport.Refusal(
            f"the claim's attestation_map names gate(s) "
            f"{', '.join(unknown_gates)} that the governing manifest at "
            f"{head[:12]} does not declare "
            f"({', '.join(cfg.gate_ids) or 'it declares none'}); a map row "
            f"can only point at an attestation this request will carry. "
            f"No gate was run and nothing was pushed or recorded",
            "", remedy="a person corrects the claim's attestation_map to "
                       "name declared gates, or declares the gate in "
                       "review.toml, and re-runs the hand-off")


def check_claim_record(claim, ledger: Ledger, lineage: str, cfg: Config,
                       path: Path | None = None) -> dict:
    """The claim's record-bound members judged against THIS machine's ledger
    (0.25.0, public issue #4): refusals raise ClaimDefective, everything
    else is returned as per-entry STATES the request renders.

    Called twice, from one function so the two cannot disagree: by the CLI
    BEFORE `ensure_pushed` commits anything — the earliest point both the
    ledger and the lineage are known, which is where a refusal costs the
    author nothing — and by `emit_request`, which renders the states.

    Refused, because the ledger that could answer is in hand:
      * a carried fingerprint absent from its origin verdict when that
        verdict IS recorded in this ledger — the claim names a finding the
        record says was never ruled there;
      * an attestation-map fingerprint that resolves neither to a standing
        disposition of this lineage's previous round nor to a carried
        entry — a map row about no finding this request answers.
    Stated, never refused, because this machine cannot know:
      * an origin verdict this ledger does not hold — "not verifiable
        here": the author may be on another machine than the round was;
      * a `required` the author typed that differs from the kept origin
        verdict's `Required outcome` — the verdict's own text is what the
        request shows, and the difference is named beside it.
    """
    def refuse(where: str, detail: str):
        raise ClaimDefective(path, f"{where} {detail}", defect="record",
                             member=where)

    if not claim.get("carried_findings") and not claim.get("attestation_map"):
        # Nothing record-bound declared: no ledger read at all, so a claim
        # without these members costs and risks exactly what it did before.
        return {"carried": [], "answered": {}, "previous": None, "mapped": []}
    carried = []
    answered: dict[str, str] = {}
    for i, entry in enumerate(claim.get("carried_findings") or ()):
        where = f"carried_findings[{i}]"
        fp, origin = entry["fingerprint"], entry["origin"]
        origin_lineage, origin_round = _origin_parts(origin)
        ident = ledger.resolve(fp)
        verdicts = [e for e in ledger.current(origin_lineage)
                    if e.get("event") == "verdict"
                    and e.get("round") == origin_round]
        state = {"index": i, "fingerprint": fp, "origin": origin,
                 "outcome": entry["outcome"], "fix": list(entry.get("fix")
                                                          or ()),
                 "in_ledger": bool(verdicts), "typed": entry.get("required"),
                 "required": entry.get("required"), "required_from": (
                     "author" if entry.get("required") else None),
                 "required_note": None}
        if verdicts:
            ruled = {ledger.resolve(e.get("fp", ""))
                     for e in ledger.findings_in_round(origin_round,
                                                       origin_lineage)}
            if ident not in ruled:
                refuse(f"{where}.fingerprint",
                       f"is {fp!r}, and the origin verdict {origin} is "
                       f"recorded in this ledger without that finding — "
                       f"it rules {len(ruled)} finding(s), none with this "
                       f"identity. Name a finding that verdict ruled, or the "
                       f"verdict that ruled this one")
            found = _origin_required(cfg, ledger, origin_lineage,
                                     origin_round, verdicts[-1], ident)
            if found is None:
                state["required_note"] = (
                    "the origin verdict is recorded here but its bytes are "
                    "not kept on this machine, so `required` was not "
                    "compared")
            else:
                if state["typed"] and _squash(state["typed"]) != _squash(
                        found):
                    state["required_note"] = (
                        "the claim's `required` differs from the origin "
                        "verdict's Required outcome, which is shown")
                state["required"], state["required_from"] = found, "origin"
        carried.append(state)
        answered.setdefault(ident, f"carried from {origin}")

    previous = next_round(ledger, lineage) - 1
    if previous >= 1:
        for d in ledger.standing_dispositions(lineage, round_no=previous):
            answered.setdefault(
                ledger.resolve(d.get("fp") or d.get("fingerprint") or ""),
                f"round {previous} {d.get('finding_id', '?')} "
                f"({d.get('disposition', '?')})")
    mapped = []
    for i, row in enumerate(claim.get("attestation_map") or ()):
        ident = ledger.resolve(row["fingerprint"])
        mapped.append({"fingerprint": row["fingerprint"], "ident": ident,
                       "gate": row["gate"], "test": row.get("test"),
                       "answers": answered.get(ident)})
        if ident not in answered:
            refuse(f"attestation_map[{i}].fingerprint",
                   f"is {row['fingerprint']!r}, which resolves to no "
                   f"standing disposition of round {previous} of this "
                   f"lineage and to no carried_findings entry — a map row "
                   f"must name a finding this request answers"
                   if previous >= 1 else
                   f"is {row['fingerprint']!r}, and this request opens its "
                   f"lineage, so only a carried_findings entry can be "
                   f"mapped — this one is not carried")
    return {"carried": carried, "answered": answered, "previous": previous,
            "mapped": mapped}


#: The longest `Scoped:` command the envelope renders, in UTF-8 bytes.
#: Linux caps ONE exec argument at 131,072 bytes (MAX_ARG_STRLEN) — what
#: `sh -c '<line>'` hands the shell — and macOS caps a whole exec at 1 MiB.
#: Half the smaller figure leaves room for the rest of an invocation. A
#: longer command is WITHHELD with its reason, never truncated: a shortened
#: path list is exactly the disagreement round-2 F3 closed.
SCOPED_COMMAND_MAX = 65_536


def _pathspecs(scope_paths, changed: list[str],
               sources=()) -> tuple[list[str], list[str]]:
    """(the changed paths `in_scope` matches, the git pathspecs that select
    exactly those out of every path the span's tree walk visits) —
    `changed` being the span's RAW post-image paths, the spellings every
    other scope surface matches, and `sources` its renames' (and copies')
    pre-image paths.

    Round-2 F3 (0.25.0): this used to hand the claim's own patterns to git,
    and git is a second matcher. Its wildmatch reads `[^a]` as a negation
    where `fnmatchcase` reads a literal caret, reads `\\` as an escape and
    `[[:digit:]]` as a class, and a pathspec without a wildcard also selects
    everything BELOW it, so an exact entry naming a directory showed that
    directory's contents where `in_scope` selects nothing. Now the one
    matcher decides the set and git is only told its members, so the two
    agree by construction.

    Round-3 F1: agreeing on the set `in_scope` judges is not enough,
    because git's tree walk does not judge that set. It runs BEFORE rename
    detection and visits both ends of every rename, and a directory
    pathspec selects the old end of a rename that left the directory — a
    deletion `in_scope`, which judges post-image paths only, never chose.
    So the pathspecs are computed against the WALKED set, `changed` plus
    `sources`, and the rename treatment is stated once: in_scope judges a
    rename by its target and never by its source, so the command selects
    no source. A rename whose target is in scope therefore shows as a
    whole-file addition of that target, wherever its source lies, and one
    whose target is out of scope shows nothing, even when its source lies
    under a matched prefix. The command's paths are exactly the matched
    set; git's rename detection then runs among those paths only, so it
    can pair two of them but never name a path outside them.

      - every pathspec is `:(literal)`: no pattern reaches git at all;
      - a `dir/` entry that matches a changed path is emitted as itself
        (a file named `dir` is not selected by it; measured), and every
        walked path below `dir/` that is not matched — a rename source,
        since every changed path there IS matched — is excluded by name
        with `:(exclude,literal)`, so a prefix stays one word plus one per
        source instead of one word per file;
      - an exclusion `:(exclude,literal)q` also drops everything below
        `q/`, and that can hold a matched path: a source file `dir/x`
        renamed away while `dir/x/child` is added. A prefix whose
        exclusions would drop a matched path is NOT compressed, and its
        matched paths are emitted by name instead — a literal
        `dir/x/child` does not select the file `dir/x` (measured);
      - every other matched path is emitted as itself;
      - git also selects, for a literal `p`, every walked path below `p/`,
        which exists only when `p` is a file at one end of the span and a
        directory at the other. Each such path `in_scope` does not match —
        a changed path, or a source a rename took out of that directory —
        is excluded by name. That cannot drop a matched path: a walked
        path below a file `p` exists only at the end where `p` is a
        directory, and is a file there, so nothing lies below it.

    An empty match returns no pathspec, and the caller must then render no
    command: `git diff A...B --` naming nothing is the WHOLE span."""
    changed = list(dict.fromkeys(changed))
    matched = [p for p in changed if in_scope(p, scope_paths)]
    if not matched:
        return [], []
    chosen = set(matched)
    walked = list(dict.fromkeys([*changed, *sources]))

    def keeps_chosen(q: str) -> bool:
        return not any(c == q or c.startswith(q + "/") for c in chosen)

    prefixes, covered = [], set()
    for entry in dict.fromkeys(scope_paths):
        if entry.endswith("/"):
            below = [p for p in matched if p.startswith(entry)]
            if below and all(keeps_chosen(q) for q in walked
                             if q.startswith(entry) and q not in chosen):
                prefixes.append(entry)
                covered.update(below)
    files = [p for p in matched if p not in covered]
    specs = ([f":(literal){e}" for e in prefixes]
             + [f":(literal){p}" for p in files])
    specs += [f":(exclude,literal){q}" for q in walked if q not in chosen
              and (any(q.startswith(e) for e in prefixes)
                   or any(q.startswith(p + "/") for p in files))]
    return matched, specs


def _scoped_stamp(render, scope_paths, shape: dict) -> str:
    """The envelope's `Scoped:` line: the span's diff limited to what the
    claim's `scope_paths` match, as a command — or, when no command can say
    exactly that, a line that says why and runs nothing. `render(*specs)` is
    the caller's `paths.diff_command` over the span, so the one declared
    diff renderer stays at its one declared call site."""
    label = f"{'Scoped:':<8}"
    entries = shape.get("entries") or []
    if shape.get("file_list") and not entries:
        # `_numstat_entries` could not pair the raw spellings with the
        # rows: the paths git would need are unknown, and a display
        # spelling (C-quoted, or a composite rename) names no file.
        return (f"{label}withheld — the span's raw paths could not be "
                f"read, so no command can name the in-scope paths exactly")
    matched, specs = _pathspecs(
        scope_paths, [e["target_path"] for e in entries],
        [e["source_path"] for e in entries if e.get("source_path")])
    if not specs:
        return (f"{label}none — no changed path in the span matches the "
                f"claim's scope_paths (a diff naming no path would show "
                f"the whole span, so no command is given)")
    cmd = render(*specs)
    size = len(str(cmd).encode("utf-8"))
    if size > SCOPED_COMMAND_MAX:
        return (f"{label}withheld — the claim's scope_paths match "
                f"{len(matched)} changed paths, and the command selecting "
                f"exactly them is {size} bytes, over the "
                f"{SCOPED_COMMAND_MAX}-byte bound for one command line; the "
                f"Diff line above is the whole span")
    return wire.executable_stamp("Scoped", cmd)


def _span_rows(shape: dict) -> list[dict]:
    """Per changed path: the display spelling, the raw post-image spelling
    the matchers read, and its two counts (None for binary)."""
    entries = shape.get("entries") or []
    if entries:
        return [{"path": e["path"], "raw": e["target_path"],
                 "insertions": e["insertions"], "deletions": e["deletions"]}
                for e in entries]
    return [{"path": p, "raw": p, "insertions": None, "deletions": None}
            for p in shape.get("file_list", [])]


def _in_scope_line(shape: dict, generated: set, excluded) -> str | None:
    """`In scope: N files, I insertions, D deletions (...)` — the span less
    the paths generated by declaration and those `excluded_paths` matches —
    or None when nothing is subtracted, so a round that declares and marks
    nothing renders exactly as before."""
    rows = _span_rows(shape)
    gen = [r for r in rows if r["raw"] in generated]
    excl = [r for r in rows if r["raw"] not in generated
            and excluded and in_scope(r["raw"], excluded)]
    if not gen and not excl:
        return None
    kept = [r for r in rows if r["raw"] not in generated
            and not (excluded and in_scope(r["raw"], excluded))]
    ins = sum(r["insertions"] or 0 for r in kept)
    dels = sum(r["deletions"] or 0 for r in kept)
    return (f"In scope: {len(kept)} files, {ins} insertions, {dels} "
            f"deletions (machine-computed: the span less {len(gen)} path(s) "
            f"generated by declaration and {len(excl)} matched by "
            f"excluded_paths)")


def _cell(value) -> str:
    return " ".join(str(value).split()).replace("|", "\\|")


def _notice_line(label: str, items: list[str]) -> str:
    return f"Notice — {label} ({len(items)}): " + ", ".join(items)


#: The largest inventory an objective may name as its authority, in bytes.
#: It is read whole, at emission, into the request's face; a larger file is
#: reported as unreadable rather than read in part, because a partial list
#: would compare as though its missing members did not exist.
AUTHORITY_MAX_BYTES = 1_048_576

#: How many member names one authority line or notice spells out before it
#: counts the rest. The count is always the whole count.
AUTHORITY_NAMES_SHOWN = 10


def _authority_reader(repo: Path, head: str):
    """`path -> (members, sha256, None)` or `(None, None, why)`: an
    objective's authority as the TARGET carries it (brief
    `take-objective-map` item 5, 0.26.0).

    Read through the readers the reference manifest and the source anchor
    already use — `refs.target_kind` for presence, then
    `transport._anchored_blob`, which refuses a symlink, a submodule and an
    ambiguous or substituted tree entry — with replacement objects off,
    exactly as `_reference_block` hardens its runners. Never the working
    tree: the request describes the commit the reviewer will fetch.

    The format is the smallest one a repository's own tooling can write
    from any inventory it keeps: UTF-8 text, one member per line, surrounding
    whitespace stripped, blank lines and lines starting with `#` skipped,
    repeats counted once."""
    from .transport import _anchored_blob
    run = lambda *a: _git(repo, *a, no_replace=True)          # noqa: E731
    read = lambda *a: _git_bytes(repo, *a, no_replace=True)   # noqa: E731

    def at_target(path: str):
        kind = refs.target_kind(run, head, path)
        if kind is None:
            return None, None, f"`{path}` is not tracked at {head[:12]}"
        if kind != "blob":
            return None, None, (f"`{path}` at {head[:12]} is a {kind}, not "
                                f"a file")
        data, why = _anchored_blob(run, read, head, path)
        if data is None:
            return None, None, why
        if len(data) > AUTHORITY_MAX_BYTES:
            return None, None, (f"it is {len(data)} bytes, over the "
                                f"{AUTHORITY_MAX_BYTES}-byte bound for an "
                                f"authority")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return None, None, "its bytes are not UTF-8"
        members = list(dict.fromkeys(
            line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")))
        return members, hashlib.sha256(data).hexdigest(), None
    return at_target


def _names(items: list[str]) -> str:
    """Up to AUTHORITY_NAMES_SHOWN names, then the count of the rest."""
    shown = ", ".join(f"`{i}`" for i in items[:AUTHORITY_NAMES_SHOWN])
    rest = len(items) - AUTHORITY_NAMES_SHOWN
    return shown + (f", and {rest} more" if rest > 0 else "")


def _counted_notice(label: str, items: list[str]) -> str:
    """`_notice_line`'s shape with the list capped and the count whole."""
    rest = len(items) - AUTHORITY_NAMES_SHOWN
    return (f"Notice — {label} ({len(items)}): "
            + ", ".join(items[:AUTHORITY_NAMES_SHOWN])
            + (f", and {rest} more" if rest > 0 else ""))


def _authority_block(objectives, read_authority) -> tuple[list[str],
                                                          list[str]]:
    """(the Authority lines, their notices) for the objectives that name an
    authority — nothing at all when none does, so a claim without the two
    fields renders exactly as before (brief `take-objective-map` item 5).

    The comparison is exact strings, both ways: members the authority lists
    and the objective does not cover contradict its claim to be complete;
    members it covers that the authority does not list are claims the
    authority does not back. It reports and never refuses — the inventory
    is the repository's own file, and whether it is right is the reviewer's
    call, which this puts in front of them before the relay."""
    named = [o for o in objectives if o.get("authority")]
    if not named:
        return [], []
    out = ["", "Authority — for each objective that names one, the members "
                "it covers compared with that inventory, read at the target "
                "(one member per line; blank and `#` lines skipped):", ""]
    uncovered, unlisted, unreadable = [], [], []
    for obj in named:
        title, where = _squash(obj["title"]), obj["authority"]
        members, digest, why = (read_authority(where) if read_authority
                                else (None, None, "no target to read it from"))
        if members is None:
            out.append(f"- {title}: `{where}` — cannot be read at the "
                       f"target: {why}")
            unreadable.append(f"`{where}` ({title})")
            continue
        covers = list(dict.fromkeys(obj["covers"]))
        listed, covered = set(members), set(covers)
        missing = [m for m in members if m not in covered]
        extra = [c for c in covers if c not in listed]
        uncovered += [f"`{m}` ({title})" for m in missing]
        unlisted += [f"`{c}` ({title})" for c in extra]
        head = (f"- {title}: `{where}` (sha256:{digest[:16]}…, "
                f"{len(members)} members) — covers {len(covers)}; ")
        if not missing and not extra:
            out.append(head + "every member it lists is covered, and every "
                              "member covered is listed")
        else:
            out.append(head + "listed and not covered: "
                       + (_names(missing) or "none")
                       + "; covered and not listed: "
                       + (_names(extra) or "none"))
    notices = []
    if uncovered:
        notices.append(_counted_notice(
            "objective authority member(s) the objective does not cover",
            uncovered))
    if unlisted:
        notices.append(_counted_notice(
            "objective member(s) covered that the named authority does not "
            "list", unlisted))
    if unreadable:
        notices.append(_counted_notice(
            "objective authority that cannot be read at the target",
            unreadable))
    return out, notices


def _objectives_block(objectives, shape: dict, generated: set,
                      excluded, read_authority=None) -> list[str]:
    rows = _span_rows(shape)
    scoped = [r for r in rows if r["raw"] not in generated
              and not (excluded and in_scope(r["raw"], excluded))]
    out = ["", "Objectives — the author's map; the changed files are "
               "computed by the tool from the span, matching each "
               "objective's paths (exact path, `dir/` prefix or glob):", "",
           "| objective | declared paths | changed files it maps | tests | "
           "references |", "|---|---|---|---|---|"]
    unmatched = []
    for obj in objectives:
        hits = [r["path"] for r in rows if in_scope(r["raw"], obj["paths"])]
        for p in obj["paths"]:
            if not any(in_scope(r["raw"], [p]) for r in rows):
                unmatched.append(f"`{p}` ({obj['title']})")
        out.append("| " + " | ".join(_cell(c) for c in (
            obj["title"], ", ".join(f"`{p}`" for p in obj["paths"]),
            ", ".join(hits) or "(none)",
            ", ".join(obj.get("tests") or ()) or "-",
            ", ".join(obj.get("references") or ()) or "-")) + " |")
    unmapped = [r["path"] for r in scoped
                if not any(in_scope(r["raw"], o["paths"]) for o in objectives)]
    notices = []
    if unmapped:
        notices.append(_notice_line(
            "changed file(s) in scope that no objective maps", unmapped))
    if unmatched:
        notices.append(_notice_line(
            "objective path(s) that match no changed file", unmatched))
    authority_lines, authority_notices = _authority_block(objectives,
                                                          read_authority)
    notices += authority_notices
    return out + authority_lines + ([""] + notices if notices else [])


def _fix_in_span(repo: Path, sha: str, base: str, head: str) -> bool:
    """Whether commit `sha` is in `base..head`: an ancestor of the head and
    not of the base — the emitter's own ancestry door, replacement off. A
    SHA this clone does not hold is an ancestor of nothing, so it is in no
    span here."""
    return (_is_ancestor(repo, sha, head)
            and not _is_ancestor(repo, sha, base))


def _carried_block(record: dict, repo: Path, base: str,
                   head: str) -> list[str]:
    out = ["", "Carried findings — findings of earlier verdicts this request "
               "answers, as the author declares them; each is checked against "
               "this machine's ledger:", ""]
    unverifiable, differs, outside = [], [], []
    for c in record["carried"]:
        fixes = []
        for sha in c["fix"]:
            inside = _fix_in_span(repo, sha, base, head)
            fixes.append(f"`{sha[:12]}`"
                         + ("" if inside else " (NOT in this review's span)"))
            if not inside:
                outside.append(f"`{sha[:12]}` ({c['fingerprint']})")
        line = (f"- `{c['fingerprint']}` · origin {c['origin']} · "
                f"**{c['outcome']}**"
                + (f" — fix: {', '.join(fixes)}" if fixes else ""))
        out.append(line)
        if not c["in_ledger"]:
            unverifiable.append(f"`{c['fingerprint']}` ({c['origin']})")
            out.append("  - origin verdict not in this ledger — not "
                       "verifiable here")
        if c["required"]:
            source = ("the origin verdict, kept on this machine"
                      if c["required_from"] == "origin"
                      else "typed by the author")
            out.append(f"  - required ({source}): {_squash(c['required'])}")
        if c["required_note"]:
            out.append(f"  - {c['required_note']}")
            if "differs" in c["required_note"]:
                differs.append(f"`{c['fingerprint']}`")
    notices = []
    if unverifiable:
        notices.append(_notice_line(
            "carried finding(s) whose origin verdict is not in this ledger, "
            "so not verifiable here", unverifiable))
    if differs:
        notices.append(_notice_line(
            "carried finding(s) whose `required` differs from the origin "
            "verdict's", differs))
    if outside:
        notices.append(_notice_line(
            "fix commit(s) outside this review's span", outside))
    return out + ([""] + notices if notices else [])


def _attestation_map_block(record: dict,
                           attestations: list[dict]) -> list[str]:
    by_gate = {a.get("id"): a for a in attestations if isinstance(a, dict)}
    out = ["", "Finding-to-attestation map — the author maps each finding to "
               "a gate; the command, result and binding are read from this "
               "request's own attestation block, never typed:", "",
           "| finding | answers | gate | test | command | result | binding |",
           "|---|---|---|---|---|---|---|"]
    weak = []
    for row in record["mapped"]:
        rec = by_gate.get(row["gate"], {})
        if "error" in rec:
            result = f"NOT RUN: {rec.get('error')}"
        else:
            result = (f"exit {rec.get('exit_code')}" if "exit_code" in rec
                      else "-")
        if rec.get("exit_code") != 0 or rec.get("binding") != "bound":
            weak.append(f"`{row['fingerprint']}` → {row['gate']}")
        out.append("| " + " | ".join(_cell(c) for c in (
            f"`{row['fingerprint']}`", row["answers"] or "-",
            row["gate"], row["test"] or "-",
            rec.get("command", "-"), result,
            rec.get("binding", "-"))) + " |")
    mapped = {row["ident"] for row in record["mapped"]}
    uncovered = [f"`{ident}` ({what})"
                 for ident, what in record["answered"].items()
                 if ident not in mapped]
    notices = []
    if weak:
        notices.append(_notice_line(
            "mapped gate(s) whose attestation did not pass or is not bound",
            weak))
    if uncovered:
        notices.append(_notice_line(
            "finding(s) this request answers that no map row covers — "
            "rerun these", uncovered))
    return out + ([""] + notices if notices else [])


def _observations_block(observations) -> list[str]:
    out = ["", "Author-typed observations — typed by the author, NOT this "
               "hand-off's attestations; this tool ran and checked none of "
               "them:", ""]
    for o in observations:
        out.append(f"- `{_squash(o['command'])}` → {_squash(o['result'])} "
                   f"— context: {_squash(o['context'])}")
    return out


def _excluded_block(excluded, shape: dict) -> list[str]:
    rows = _span_rows(shape)
    if not excluded:
        return ["Excluded paths: (declared empty — nothing excluded)", ""]
    out = ["Excluded paths — declared out of this review's scope (exact "
           "path, `dir/` prefix or glob), subtracted from the in-scope "
           "counts:"]
    for entry in excluded:
        n = sum(1 for r in rows if in_scope(r["raw"], [entry]))
        out.append(f"  - `{entry}` ("
                   + (f"matches {n} span path(s)" if n
                      else "matches no span path") + ")")
    return out + [""]


def parse_claim(text: str, path: Path) -> dict:
    """The authored claim, parsed and closed from bytes the caller already
    captured: duplicate members refused at any depth (wire.load_json),
    malformed JSON refused as the same typed defect rather than escaping as
    a raw JSONDecodeError, and the whole grammar checked by validate_claim.
    """
    try:
        parsed = wire.load_json(text)
    except wire.DuplicateMember as exc:
        raise ClaimDefective(
            path, f"states JSON member {exc.name!r} more than once; every "
                  f"member is single-valued, and the tool will not choose "
                  f"which declaration becomes the Claim",
            defect="duplicate", member=exc.name, name=exc.name) from exc
    except json.JSONDecodeError as exc:
        raise ClaimDefective(
            path, f"is not valid JSON: {exc.msg} (line {exc.lineno}, column "
                  f"{exc.colno})", defect="syntax") from exc
    return validate_claim(parsed, path)


def capture_claim(supplied: "str | Path | None") -> CapturedClaim:
    """THE capture boundary: read the supplied claim exactly once, close its
    grammar, and return the digest and the value together (round 7 F1).

    Every caller goes through here before it touches ledger state, Git, the
    gates, the cache or the record; nothing downstream may reopen the path.
    `None` — the option omitted — is the no-claim state, which stays legal
    and carries its own recorded digest rather than an empty string the cache
    would skip.

    It takes the CLI's own value, not a `Path`, because two of the domain's
    states live in that value and are lost by converting it early (round 8):

      - an EMPTY path (`--claim-file ""`) is not an omitted option. The CLI
        turned every falsey value into `None`, so an author who supplied an
        empty path got the no-claim lifecycle — the supplied-versus-absent
        collapse this whole object exists to prevent, arriving through the
        argument instead of through the file (F3);
      - bytes that are not UTF-8 are a supplied claim that is not a claim,
        and `read_text` answered them with a raw UnicodeDecodeError — the one
        malformed-input state with no typed recovery, which is also the one
        exit that broke the CLI's structured-exit contract (F2).
    """
    if supplied is None:
        return CapturedClaim(vocab.NO_CLAIM, _EMPTY_CLAIM, False)
    if not str(supplied).strip():
        raise ClaimDefective(
            None,
            "was supplied as an empty path; omitting --claim-file is the "
            "recorded no-claim state and an empty path names nothing, so the "
            "two are different invocations and the tool will not fold one "
            "into the other", defect="empty_path")
    path = Path(supplied)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ClaimUnreadable(f"{path}: {exc.strerror or exc}") from exc
    except UnicodeDecodeError as exc:
        raise ClaimDefective(
            path, f"is not UTF-8 text: {exc.reason} at byte {exc.start}; a "
                  f"claim the tool cannot decode is not a claim it can carry",
            defect="encoding") from exc
    return CapturedClaim(sha256_text(text),
                         _frozen(parse_claim(text, path)), True)


def load_claim(path: Path) -> dict:
    """The authored claim read from `path` through `parse_claim`. Retained
    for direct callers and tests; the CLI captures through `capture_claim`,
    which is the only path that reads a claim during a handoff."""
    return parse_claim(path.read_text(encoding="utf-8"), path)

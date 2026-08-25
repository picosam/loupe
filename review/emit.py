"""Request emission (design §9bis.3): derive, never ask.

Base, head, branch, roles, round and diff shape are all machine-derived —
the round-1 defects (hand-counted diff shape, missing wrapper, missing
taxonomy) were all products of hand-typing an envelope, and this module is
the mechanism that ends that. Authored content (the Claim, stop conditions,
hand-back questions) comes from a claim file: it is the author's judgment,
which the tool carries but never invents.
"""
from __future__ import annotations

import dataclasses
import hashlib
from collections import Counter
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from types import MappingProxyType
from typing import Mapping

from . import (TOOL_NAME, TOOL_VERSION, env_var, paths, refs,
               tool_identity, vocab, wire)
from .config import Config
from .digest import sha256_text
from .ledger import Ledger, render_report_md



def _git(repo_root: Path, *args: str) -> str:
    # The timeout is §9bis.4's fail-don't-hang rule as much as hygiene: push
    # and ls-remote reach the network, and an unreachable remote must refuse.
    out = subprocess.run(["git", "-C", str(repo_root), *args],
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(
            f"a `git` subprocess failed: "
            f"`{paths.command(paths.Lit('git'), *args)}` — "
            f"{out.stderr.strip()}")
    return out.stdout.strip()


def _git_bytes(repo_root: Path, *args: str) -> bytes:
    """Raw stdout, for content that must be digested as BYTES rather than
    decoded first — `git show <sha>:<path>` over a file this process has no
    business assuming is UTF-8 (round 3 F1)."""
    out = subprocess.run(["git", "-C", str(repo_root), *args],
                         capture_output=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(
            f"a `git` subprocess failed: "
            f"`{paths.command(paths.Lit('git'), *args)}` — "
            f"{out.stderr.decode('utf-8', 'replace').strip()}")
    return out.stdout


def _is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    out = subprocess.run(["git", "-C", str(repo_root), "merge-base",
                          "--is-ancestor", ancestor, descendant],
                         capture_output=True, text=True, timeout=60)
    return out.returncode == 0


def _scrub_url(url: str) -> str:
    """Strip userinfo from a remote URL before it is stamped anywhere.

    https remotes can embed credentials (https://user:token@host/...), and
    nothing in an envelope may hold secrets. scp-style git@host: stays: that
    username is transport convention, not a credential.
    """
    return re.sub(r"://[^/@]*@", "://", url)


def next_round(ledger: Ledger) -> int:
    """The round the next emission opens — one authority, used by both the
    emitter and the CLI (which needs it before emission, for the commit
    subject)."""
    verdicts = [e for e in ledger.current() if e.get("event") == "verdict"]
    return max((e["round"] for e in verdicts), default=0) + 1


def ensure_pushed(cfg: Config, head: str | None = None,
                  local_only: bool = False,
                  commit_subject: str | None = None,
                  round_no: int | None = None, git=None,
                  transport: str | None = None) -> dict:
    """Make the review target fetchable BEFORE emission (§9bis.4, RVW-T7).

    Commits outstanding tracked work, pushes the reviewed branch, and returns
    the reachability record the envelope stamps — with the remote ref value
    OBSERVED via ls-remote after the push, never assumed from the push's exit
    code. Every state that cannot yield a fetchable target refuses loudly,
    because a SHA the reviewer cannot fetch is not a review target and an
    envelope naming one is a false artifact.

    `git` is the command runner — `(*args) -> stdout, raising RuntimeError on
    a nonzero exit` — injectable so every refusal state is testable without a
    network or a scratch repository.
    """
    repo = cfg.repo_root
    run = git or (lambda *a: _git(repo, *a))

    # RVW-T11 leg 2, and the one place the two declarations can contradict
    # each other. `--local-only` says review is genuinely same-clone;
    # `transport = paste` says the reviewer has no access to this filesystem.
    # Both cannot hold: the reviewer would receive bytes naming a SHA no
    # clone of theirs can fetch, and would discover it at `take`, after the
    # human has already carried the envelope. Refused here, before the
    # commit, because it needs no git state to be false — this is the leg
    # RVW-T11 called unsolved-but-visible, now enforced rather than stamped.
    if local_only and transport == vocab.TRANSPORT_PASTE:
        raise RuntimeError(
            f"--local-only declares the target fetchable from no remote, "
            f"while transport={vocab.TRANSPORT_PASTE!r} declares a reviewer "
            f"with no access to this filesystem: the two cannot both be true, "
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

    committed = False
    if tracked:
        subject = commit_subject or (
            f"emit-request: outstanding work for round {round_no}"
            if round_no is not None else "emit-request: outstanding work")
        run("commit", "-a", "-m", subject)
        committed = True

    target = run("rev-parse", "HEAD")

    remotes = [r for r in run("remote").splitlines() if r.strip()]
    if local_only:
        if remotes:
            raise RuntimeError(
                f"--local-only while remote(s) exist ({', '.join(remotes)}): "
                f"the flag exists for repos with no fetchable surface at "
                f"all, never as a bypass of the push rule (§9bis.4)")
        return {"state": "local-only", "branch": branch, "sha": target,
                "committed": committed}
    if not remotes:
        raise RuntimeError(
            "no remote configured: a SHA the reviewer cannot fetch is not a "
            "review target (§9bis.4). Add a remote — or, only if review is "
            "genuinely same-clone, re-run with --local-only, which stamps "
            "that state on the envelope's face")

    upstream = run("for-each-ref",
                   "--format=%(upstream:remotename)\t%(upstream:remoteref)",
                   f"refs/heads/{branch}")
    remote, _, merge_ref = upstream.partition("\t")
    if not remote:
        if len(remotes) > 1:
            raise RuntimeError(
                f"{branch} has no upstream and {len(remotes)} remotes exist "
                f"({', '.join(remotes)}): the destination is not derivable, "
                f"and the tool never invents a decision (§9bis.3). Set one "
                f"with `{paths.command(*paths.lits('git', 'push', '-u'), paths.Ph('<remote>'), branch)}`, "
                f"then re-run")
        remote, merge_ref = remotes[0], f"refs/heads/{branch}"

    url = _scrub_url(run("remote", "get-url", remote))
    try:
        # Always an explicit refspec: a push that silently does nothing
        # (push.default surprises) is worse than one that fails (§9bis.4).
        run("push", remote, f"refs/heads/{branch}:{merge_ref}")
    except RuntimeError as exc:
        raise RuntimeError(
            f"push of {branch} to {remote} failed — refusing to emit: an "
            f"envelope naming an unfetchable SHA is a false artifact "
            f"(§9bis.4). {exc}") from exc

    observed = ""
    for line in run("ls-remote", remote, merge_ref).splitlines():
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
            "committed": committed}


def diff_shape(repo_root: Path, base: str, head: str) -> dict:
    """Machine-computed diff shape — never hand-counted (round-1 trap #3)."""
    numstat = _git(repo_root, "diff", "--numstat", f"{base}...{head}")
    files, ins, dels, areas = [], 0, 0, set()
    for line in numstat.splitlines():
        a, d, path = line.split("\t", 2)
        files.append(path)
        ins += 0 if a == "-" else int(a)
        dels += 0 if d == "-" else int(d)
        areas.add(path.split("/", 1)[0] if "/" in path else "<root>")
    return {"files": len(files), "insertions": ins, "deletions": dels,
            "changed_lines": ins + dels, "areas": sorted(areas),
            "file_list": files}


def shape_line(s: dict) -> str:
    return (f"{s['files']} files, {s['insertions']} insertions, "
            f"{s['deletions']} deletions = {s['changed_lines']} changed "
            f"lines, {len(s['areas'])} areas")


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


def _retain_output(cfg: Config, executed_sha: str, gate_id: str,
                   blob: str) -> dict:
    """Digest the gate's full output and point at the retained copy (§5.1).

    Output is retained OUTSIDE the repo, under the ledger directory, because
    that is where this process's state lives (§4) and a review must not add
    bytes to a tree it is reviewing. The digest is computed either way, so a
    failure to retain degrades the pointer without losing the identity.
    """
    record = {"sha256": hashlib.sha256(blob.encode("utf-8")).hexdigest(),
              "bytes": len(blob.encode("utf-8"))}
    if cfg.ledger_dir is None:
        record["pointer"] = "not retained (no state directory configured)"
        return record
    target = Path(cfg.ledger_dir) / "gate-output" / executed_sha
    try:
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{gate_id}.log"
        path.write_text(blob, encoding="utf-8")
        record["pointer"] = str(path)
    except OSError as exc:
        record["pointer"] = f"not retained ({exc.strerror})"
    return record


def _caller_env() -> dict:
    """The environment the CALLER of the tool had, for gate subprocesses.

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

    A gate is the REPOSITORY's command and must run in the environment the
    caller of loupe had, not the one the shim made for the tool. The shim
    stashes the caller's values (LOUPE_CALLER_PYTHONSAFEPATH /
    LOUPE_CALLER_PYTHONPATH — presence distinguishes set-to-anything from
    unset) and marks itself with LOUPE_SHIM; this restores exactly those.
    Invoked without the shim (python3 -m review) there is no stash and no
    exact answer, so the best available approximation is applied and named
    as such: drop PYTHONSAFEPATH, and drop this package's own root from
    PYTHONPATH, leaving everything else the caller set. The stash and marker
    variables themselves stay out of the gate environment either way; the
    re-entrancy marker is added by the caller of this helper.
    """
    env = dict(os.environ)
    ran_via_shim = env.pop(env_var("SHIM"), None)
    stash_safe = env.pop(env_var("CALLER_PYTHONSAFEPATH"), None)
    stash_path = env.pop(env_var("CALLER_PYTHONPATH"), None)
    if ran_via_shim:
        if stash_safe is None:
            env.pop("PYTHONSAFEPATH", None)
        else:
            env["PYTHONSAFEPATH"] = stash_safe
        if stash_path is None:
            env.pop("PYTHONPATH", None)
        else:
            env["PYTHONPATH"] = stash_path
        return env
    env.pop("PYTHONSAFEPATH", None)
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
    if "PYTHONPATH" in env:
        parts = env["PYTHONPATH"].split(os.pathsep)
        if own_root in parts:
            kept = [p for p in parts if p != own_root]
            if kept:
                env["PYTHONPATH"] = os.pathsep.join(kept)
            else:
                env.pop("PYTHONPATH")
    return env


def run_gates(cfg: Config, target_sha: str) -> list[dict]:
    """Execute the declared gate manifest and return attestations (§5.1).

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
    """
    # Re-entrancy guard. A gate manifest that runs the test suite, whose
    # tests emit envelopes, would otherwise recurse forever — and it did on
    # first run. The guard is an inherited env var rather than a parameter
    # because the recursion crosses a process boundary, where a flag cannot
    # reach. A nested emission records the gates as not run rather than
    # pretending they passed.
    if os.environ.get(env_var("IN_GATE_RUN")):
        return [{"id": g["id"], "blocking": g.get("blocking", False),
                 "error": "not run: nested inside a gate execution"}
                for g in cfg.gates]

    try:
        executed_sha = _git(cfg.repo_root, "rev-parse", "HEAD")
        porcelain = _git(cfg.repo_root, "status", "--porcelain")
    except RuntimeError as exc:
        return [{"id": g["id"], "blocking": g.get("blocking", False),
                 "error": f"not run: cannot identify the executed tree ({exc})"}
                for g in cfg.gates]

    tree = "dirty" if porcelain else "clean"
    if executed_sha != target_sha:
        binding = (f"unbound: gates executed against {executed_sha}, not the "
                   f"attested target {target_sha}")
    elif porcelain:
        binding = ("unbound: the executed tree is dirty, so it is not the "
                   "target commit's content")
    else:
        binding = "bound"

    env = {**_caller_env(), env_var("IN_GATE_RUN"): "1"}
    attestations = []
    for gate in cfg.gates:
        started = time.monotonic()
        try:
            proc = subprocess.run(gate["command"], cwd=cfg.repo_root,
                                  capture_output=True, text=True, timeout=600,
                                  env=env)
        except (OSError, subprocess.SubprocessError) as exc:
            attestations.append({"id": gate["id"],
                                 "blocking": gate.get("blocking", False),
                                 "error": f"could not execute: {exc}"})
            continue
        duration = time.monotonic() - started
        attestations.append({
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
                                     proc.stdout + proc.stderr),
            "blocking": gate.get("blocking", False),
        })
    return attestations


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
        "Material you cannot retrieve: list it under `## unavailable "
        "references`, return",
        f"`VERDICT: {vocab.VERDICT_CHANGES}`, one finding per unreachable "
        f"material reference (§5.1).",
    ])


def _dispositions_block(ledger: Ledger, round_no: int) -> str:
    # Round 2 F1: the reviewer is shown the STANDING answer per finding,
    # once — the live round-2 request printed each answer three times,
    # because this block read raw events while the preflight read the
    # standing selector. Superseded emissions are marked by count, never
    # presented as co-standing answers; the raw rows stay in the ledger
    # file, which is the audit surface.
    every = ledger.disposition_batches(round_no=round_no)
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
        base = f"(no round-{round_no} dispositions in the ledger)"
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
    run = git or (lambda *a: _git(repo_root, *a))
    run_bytes = git_bytes or (lambda *a: _git_bytes(repo_root, *a))
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
    run = git or (lambda *a: _git(repo, *a))
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
                 transport: str | None = None) -> str:
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
    verdicts = [e for e in ledger.current() if e.get("event") == "verdict"]
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
    dirty = _git(repo, "status", "--porcelain")
    # Round-4 F8: the declared gate manifest reaches the metric here, not only
    # through the ledger API a test calls directly. Round-4 F5: so does the
    # configured token budget, which is None when none is declared.
    effective_cap = ledger.effective_round_cap(cfg.round_cap)
    report_md = render_report_md(ledger.report(
        effective_cap, gate_manifest=cfg.gate_ids,
        token_budget=cfg.token_budget,
        blocking_severities=cfg.blocking_severities))

    tree_state = ("DIRTY — attestations from a dirty tree are not attestations"
                  if dirty else "clean at emission")
    changed = "\n".join(f"  {p}" for p in shape["file_list"])
    attestations = run_gates(cfg, head)
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
        f'transport="{transport}" tool="{tool_identity()}">',
        f"Roles: author={author} · reviewer={reviewer} · relay={relay} · "
        f"transport={transport}. "
        f"Per-invocation stamp, overriding the default direction for this "
        f"artifact only (the agents' standing instructions).",
        "",
        f"Target: {head}",
        f"Base:   {base}   (the SHA ruled on in round {round_no - 1})",
        wire.executable_stamp("Diff", paths.diff_command(repo, base, head)),
        f"Tree:   {tree_state}",
        *wire.render_push_lines(reachability),
        f"Access: {claim.get('access_note', 'see reference manifest below')}",
        f"Round:  {round_no} of {effective_cap} "
        + (f"(repo default is {cfg.round_cap}; this lineage is authorized to "
           f"{effective_cap} by a recorded ledger override)"
           if effective_cap != cfg.round_cap
           else "(budget breaker fires past the cap)"),
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
        f"What changed ({shape_line(shape)} — machine-computed):",
        "",
        changed,
        "",
        "Deliberately not done:",
        not_done or "  - (nothing declared)",
        "",
        f"## Disposition ledger — round {round_no - 1}, emitted by the tool",
        "",
        _dispositions_block(ledger, round_no - 1),
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
            for i, ref in enumerate(item):
                _check_reference(path, i, ref)
    return value


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

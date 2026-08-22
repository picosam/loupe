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
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from types import MappingProxyType
from typing import Mapping

from . import TOOL_NAME, TOOL_VERSION, env_var, vocab, wire
from .config import Config
from .digest import sha256_file, sha256_text
from .ledger import Ledger, render_report_md



def _git(repo_root: Path, *args: str) -> str:
    # The timeout is §9bis.4's fail-don't-hang rule as much as hygiene: push
    # and ls-remote reach the network, and an unreachable remote must refuse.
    out = subprocess.run(["git", "-C", str(repo_root), *args],
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout.strip()


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
                  round_no: int | None = None, git=None) -> dict:
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
                f"with `git push -u <remote> {branch}`, then re-run")
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
    events = [e for e in ledger.current()
              if e.get("event") == "disposition" and e.get("round") == round_no]
    if not events:
        return f"(no round-{round_no} dispositions in the ledger)"
    lines = []
    for e in events:
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
        lines.append(f"- {e['finding_id']} `{e['fp']}` — "
                     f"**{e['disposition']}{sub}** {detail}".rstrip())
    return "\n".join(lines)


def _reference_block(repo_root: Path, references: list[dict]) -> str:
    """Immutable reference manifest (§5.1 F4): digest, access, status.
    A pointer without a digest is a rumour; an unreadable reference is
    labelled unavailable, not dropped (absent != none).
    """
    lines = []
    for ref in references:
        path = repo_root / ref["path"]
        req = "required" if ref.get("required", True) else "advisory"
        if path.is_file():
            lines.append(f"  {ref['path']}  sha256:{sha256_file(path)}  "
                         f"[{req}] {ref.get('note', '')}".rstrip())
        elif path.is_dir():
            lines.append(f"  {ref['path']}/  (directory; per-file digests via "
                         f"git) [{req}] {ref.get('note', '')}".rstrip())
        else:
            lines.append(f"  {ref['path']}  UNAVAILABLE from this surface — "
                         f"mark findings that depend on it `unavailable` "
                         f"[{req}] {ref.get('note', '')}".rstrip())
    return "\n".join(lines)


def emit_request(cfg: Config, ledger: Ledger, claim: dict,
                 base: str | None = None, head: str | None = None,
                 reachability: dict | None = None) -> str:
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
            f"could not compute `git diff {base[:12]}...{head[:12]}` "
            f"(§9bis.4)")
    round_no = max((e["round"] for e in verdicts), default=0) + 1

    author = cfg.roles.get("author") or ""
    reviewer = cfg.roles.get("reviewer") or ""
    # What actually carried the envelope. Until 2026-08-13 this was the
    # literal string "user", which became false the moment the author process
    # began invoking the reviewer directly: the stamp asserted a human relay
    # that was not there. The tool cannot observe its own transport, so it
    # takes the value and refuses to invent one.
    relay = (claim.get("relay") or cfg.roles.get("relay")
             or "unrecorded (transport not declared)")
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
        f'<{cfg.wrapper_tag}-review-request sha="{head}" branch="{branch}" '
        f'author="{author}" reviewer="{reviewer}" round="{round_no}">',
        f"Roles: author={author} · reviewer={reviewer} · relay={relay}. "
        f"Per-invocation stamp, overriding the default direction for this "
        f"artifact only (the agents' standing instructions).",
        "",
        f"Target: {head}",
        f"Base:   {base}   (the SHA ruled on in round {round_no - 1})",
        f"Diff:   git -C {repo} diff {base}...{head}",
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
        _reference_block(repo, claim.get("references", [])),
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

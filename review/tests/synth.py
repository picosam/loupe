"""Synthetic envelopes for the travelling tests (§11, decided 2026-08-16:
the development corpus stays behind; the public suite must pass without it).

Nothing here is hand-typed wire format where the tool can produce it: a
request comes from the real emitter (`emit_request`) against this checkout
with a synthetic reachability record and no gates, exactly as
test_reachability already did — read-only, no network, no scratch clone.
Verdicts are built by small generators, and the two legacy shapes the
validator must still recognise (the pre-wrapper round-1 style, and the
former-name dialect) are derived from those by the minimal transformation
that defines them.

Tests whose point is a property of the REAL historical artifacts live in
the workbench's evidence suite, which stays behind with the corpus.
"""
from __future__ import annotations

import dataclasses
import json

from review import config, wire
from review.emit import _attestation_block, _git, emit_request
from review.ledger import Ledger
from review.tests.util import LINEAGE, REPO_ROOT

CFG = config.load(REPO_ROOT)
NO_GATES = dataclasses.replace(CFG, gates=[])
# The tag every synthetic envelope speaks: this checkout's, so the fixtures
# validate under CFG whatever the repository calls itself.
TAG = CFG.wrapper_tag

SHA = "9" * 40

CLAIM = {"objective": "synthetic request for the test suite",
         "risk": "low — fixture",
         "references": [{"path": "review.toml", "required": True}]}


def head_sha() -> str:
    return _git(REPO_ROOT, "rev-parse", "HEAD")


def reachability(sha: str) -> dict:
    return {"state": "pushed", "branch": "main", "ref": "refs/heads/main",
            "remote": "origin", "url": "ssh://example.invalid/x.git",
            "sha": sha, "committed": False}


def shadow_ledger(round_no: int = 1, sha: str | None = None) -> Ledger:
    """A ledger whose last verdict is round `round_no`, so the next emission
    is round `round_no + 1`."""
    sha = sha or head_sha()
    ledger = Ledger.in_memory()
    for r in range(1, round_no + 1):
        ledger.add({"event": "request", "round": r, "sha": sha, "bytes": 1})
        ledger.add({"event": "verdict", "round": r, "sha": sha,
                    "verdict": "changes requested", "bytes": 1,
                    "finding_ids": 0})
    return ledger


def emitted_request(cfg=NO_GATES, claim: dict | None = None,
                    ledger: Ledger | None = None) -> str:
    """A request the real emitter produced against HEAD (diff HEAD...HEAD:
    zero files), stamped with a synthetic push. Validates clean under NO_GATES
    apart from what the caller breaks on purpose."""
    head = head_sha()
    return emit_request(cfg, ledger or shadow_ledger(), claim or CLAIM,
                        base="HEAD", head="HEAD",
                        reachability=reachability(head), lineage=LINEAGE)


def attestation_records(target_sha: str, gate_id: str = "probe",
                        exit_code: int = 0, blocking: bool = False) -> list[dict]:
    """One conforming ran-record per the validator's field table."""
    return [{"id": gate_id, "command": "true", "exit_code": exit_code,
             "tool_version": "true -> /usr/bin/true sha256:0000000000000000",
             "runner": "synthetic", "target_sha": target_sha,
             "executed_sha": target_sha, "tree": "clean", "binding": "bound",
             "duration_s": 0.0,
             "output": {"sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4"
                                  "649b934ca495991b7852b855", "bytes": 0,
                        "pointer": "not retained (synthetic)"},
             "blocking": blocking}]


def attestation_block(tag: str, target_sha: str, **kw) -> str:
    return _attestation_block(tag, attestation_records(target_sha, **kw))


def complete_attestation(**over) -> dict:
    """One complete ran-record in the shape the emitter writes (round-4 F3),
    every §5.1 field present; `over` mutates or corrupts single fields."""
    rec = {
        "id": "tests",
        "command": "python3 -m unittest discover -s review/tests -t .",
        "exit_code": 0,
        "tool_version": "python3 -> /usr/bin/python3 sha256:0123456789abcdef",
        "runner": "loupe/0.2.0",
        "target_sha": SHA,
        "executed_sha": SHA,
        "tree": "clean",
        "binding": "bound",
        "duration_s": 12.3,
        "output": {"sha256": "a" * 64, "bytes": 42,
                   "pointer": "/state/gate-output/tests.log"},
        "blocking": True,
    }
    rec.update(over)
    return rec


def evidence_with(records: list[dict]) -> str:
    """A request Evidence section carrying `records` as its machine
    attestation block, under this checkout's fence."""
    return ("Machine attestations:\n\n"
            f"```{wire.attestation_fence(CFG.wrapper_tag)}\n"
            f"{json.dumps(records, indent=2)}\n```\n\n"
            "NOT captured — this handoff cannot vouch for these:\n"
            "  - nothing else\n")


#: A one-gate blocking manifest with no state directory: what the
#: attestation validator is judged against.
ONE_GATE = dataclasses.replace(
    CFG, gates=[{"id": "tests", "command": ["true"], "blocking": True}],
    ledger_dir=None)


def finding(n: int = 1, severity: str = "Low",
            classification: str = "design_gap",
            title: str | None = None,
            falsification: str = "observation: it is fixed",
            evidence: str = "review/wire.py:1") -> str:
    return (f"### F{n}\nSeverity: {severity}\nClassification: "
            f"{classification}\nTitle: {title or f'finding number {n}'}\n"
            f"Evidence: {evidence}\nWhy: because\nRequired outcome: fix it\n"
            f"FALSIFICATION: {falsification}\n\n")


def verdict_text(sha: str = SHA, verdict: str = "changes requested",
                 findings: int = 1, closures: str = "", tag: str = TAG,
                 body_extra: str = "") -> str:
    body = f'<{tag}-review-verdict sha="{sha}">\nVERDICT: {verdict}\n\n## findings\n\n'
    if verdict == "clean to advance":
        body += "None\n"
    else:
        for i in range(1, findings + 1):
            body += finding(i)
    if closures:
        body += "\n## closures\n\n" + closures + "\n"
    body += body_extra
    body += "\n## evidence checked\n\nreview/wire.py\n</" + tag + "-review-verdict>\n"
    return body


def legacy_verdict_text() -> str:
    """The pre-wrapper shape: no wrapper, no SHA binding, no section
    headers — findings follow the VERDICT line directly."""
    return ("VERDICT: changes requested\n\n" + finding(1) + finding(2))


def former_dialect_verdict(former: str = "rvw", **kw) -> str:
    """A verdict in the tool's former dialect: the minimal transformation
    that defines the legacy state (§10.1) applied to a current one."""
    return verdict_text(**kw).replace(f"{TAG}-review-verdict",
                                      f"{former}-review-verdict")


def former_dialect_request(former: str = "rvw") -> str:
    """A request whose attestation fence carries the former name and whose
    records are otherwise conforming — the state A-FENCE-LEGACY names."""
    head = head_sha()
    text = emitted_request()
    # Replace the empty attestation block the gate-less emitter wrote with a
    # conforming record set under the former fence.
    empty = _attestation_block(TAG, [])
    assert empty in text, "the gate-less emitter writes an empty block"
    return text.replace(empty, attestation_block(former, head))

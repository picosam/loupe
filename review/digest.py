"""Content digests — one function, one authority.

Lived in the legacy-corpus bootstrap until 2026-08-16; moved here so the code
that travels (emitter, transport, CLI) no longer imports the module that
derives this workbench's own history (§11: the bootstrap is vocabulary, the
digest is mechanism).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file_set(members: dict) -> str:
    """One digest over a NAMED set of files: {logical name -> path}.

    The name is the key, not the location: an artefact that lives at
    `public/pyproject.toml` in a workbench and `pyproject.toml` in the tree
    extracted from it is ONE artefact, and the caller resolves that
    transform before handing the mapping here (round-2 F1).

    Serialized canonically before hashing — sorted by path, explicit
    separators, no indentation — so the value depends on the files and on
    nothing about the machine that computed it: not the order the caller
    listed them in, not duplicates in that list, not where the tree sits on
    disk.

    Two honest notes about what the shape does and does not buy, because
    the first draft of this docstring claimed more than the code delivers
    and two mutations proved it. Carrying the relative path beside each
    hash does NOT, on its own, catch two modules whose contents are
    swapped: the members are already in manifest order, so the swap moves
    the hashes and changes the digest either way. The path is here because
    it makes the serialization self-describing and keeps the value stable
    if the manifest is ever reordered — not because it detects a rename.
    Likewise, recording a missing file as `absent` rather than skipping it
    is determinism, not detection: it makes the digest a total function of
    the declared set, so a caller cannot get a shorter list and a
    plausible-looking value out of an incomplete tree.
    """
    rows = []
    for name in sorted(members):
        target = Path(members[name])
        rows.append([name, sha256_file(target) if target.is_file()
                     else "absent"])
    return sha256_text(json.dumps(rows, sort_keys=True, ensure_ascii=False,
                                  separators=(",", ":")))

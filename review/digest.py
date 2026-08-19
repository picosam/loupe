"""Content digests — one function, one authority.

Lived in the legacy-corpus bootstrap until 2026-08-16; moved here so the code
that travels (emitter, transport, CLI) no longer imports the module that
derives this workbench's own history (§11: the bootstrap is vocabulary, the
digest is mechanism).
"""
from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

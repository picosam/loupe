from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def spec_path() -> Path | None:
    """The shipped specification, in whichever tree this suite is running in.

    The same file has two paths. In the workbench it is `public/docs/design.md`,
    because `public/` is the boundary marking what travels; in an extracted
    candidate the prefix is stripped and it is `docs/design.md`. A travelling
    test that hardcodes either one passes in one tree and errors in the other,
    which is how the candidate's standalone suite caught this test the first
    time it ran.

    Returns None where neither exists, so a caller can skip rather than fail:
    a suite running somewhere the spec was not shipped has nothing to say
    about the spec, and that is different from the spec being wrong.
    """
    for rel in ("public/docs/design.md", "docs/design.md"):
        candidate = REPO_ROOT / rel
        if candidate.is_file():
            return candidate
    return None


def public_path(*names: str) -> Path | None:
    """A shipped file by its candidate-root name, in whichever tree this
    suite runs in: `public/<name>` in the workbench, `<name>` in an
    extracted candidate. None where neither exists (sweep F13: the README
    is now a parity fixture and has the same two paths the spec has)."""
    for name in names:
        for rel in (f"public/{name}", name):
            candidate = REPO_ROOT / rel
            if candidate.is_file():
                return candidate
    return None

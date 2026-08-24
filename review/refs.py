"""The reference wire: one path grammar, one derivation, both sides.

Lineage 5 round 3 F1. A REQUIRED reference is a promise that the reviewer
can read exactly those bytes from the target commit, and the promise is
checkable only if two things hold. Both were decided in two places, and
the two places disagreed:

  * the PATH has to survive the wire. A manifest line is whitespace-
    delimited — `<path>  sha256:<64hex>  [required]` — so a path carrying a
    space, a tab or an invisible separator renders a line the reviewer's
    parser cannot recognise. `check_references` admitted it, and
    the reference arrived `unrecognised` after the handoff had already
    committed, pushed and run the gates. An unrecognised or unavailable
    required reference forbids a clean verdict, so the round is spent.
  * the OBJECT has to be one both sides read the same way. Emission
    digested WORKTREE bytes and `take` digests the TARGET TREE's object, so
    a tracked symlink digested the bytes it pointed at on one side and the
    link text on the other: `mismatch`, out of a preflight that passed.

So: a deliberately restricted path grammar (the alternative — quoting the
wire — buys nothing a reference path needs and adds an escaping boundary
to get wrong), declared once and enforced before any side effect; and one
pair of derivation functions that both the emitter and `take` call, so
"the same kind and digest from target-carried bytes" is structural rather
than a property two call sites must be trusted to preserve.

The admitted set is deliberately SMALLER than the parseable set: the
parser must recognise everything the grammar can render, and the preflight
refuses some rows the parser would still read (an invisible format
character parses fine and is unreadable to a person). Stricter inside is
safe; the reverse is the defect this closes.

Standard library only, no project imports: both `emit` and `transport`
depend on this module, and it must not be able to depend back.
"""
from __future__ import annotations

import hashlib
import unicodedata

from . import paths

# The command this module's refusal names, rendered like every other
# command the tool prints (round 5 F1).
_SHOW = paths.command(*paths.lits("git", "show"), paths.Ph("<sha>:<path>"))

# The one character class a reference path may be built from, and the one
# the line parser is built from (`transport._REF_LINE_RE`). Excluding `\s`
# is what makes the whitespace-delimited line unambiguous; excluding the C0
# range and DEL keeps a control character out of a line an agent prints.
PATH_CHARS = r"[^\s\x00-\x1f\x7f]"

# Git index modes, as `ls-files --stage` reports them.
MODE_REGULAR = ("100644", "100755")
MODE_SYMLINK = "120000"
MODE_GITLINK = "160000"

# Unicode categories a reference path may not contain. Zs/Zl/Zp are
# whitespace the delimiter cannot survive; Cc/Cf are invisible in every
# surface a reference is read on; Cs/Cn cannot be a filename's meaning.
_FORBIDDEN_CATEGORIES = ("Zs", "Zl", "Zp", "Cc", "Cf", "Cs", "Cn")


def _char_name(ch: str) -> str:
    try:
        return unicodedata.name(ch)
    except ValueError:
        return f"U+{ord(ch):04X}"


def path_error(raw: str) -> str | None:
    """Why `raw` cannot travel as a reference path, or None if it can.

    The state is phrased as a noun the caller can drop into its own
    refusal — every one of them names what the wire, not this function,
    could not carry.
    """
    if not raw:
        return "an empty path, which names nothing the reviewer could read"
    for i, ch in enumerate(raw):
        if ch.isspace() or unicodedata.category(ch) in ("Zs", "Zl", "Zp"):
            return (f"a path carrying whitespace at position {i} "
                    f"({_char_name(ch)}): a manifest line is whitespace-"
                    f"delimited, so the reviewer's parser reads the rest of "
                    f"the path as another field and labels the reference "
                    f"unrecognised")
        if unicodedata.category(ch) in _FORBIDDEN_CATEGORIES:
            return (f"a path carrying a control or invisible character at "
                    f"position {i} ({_char_name(ch)}): it cannot be read, "
                    f"retyped or verified on any surface this reference "
                    f"travels through")
    if raw.startswith("-"):
        return ("a path beginning with '-', which every command it reaches "
                "reads as an option rather than a file")
    if raw.endswith("/"):
        return ("a path with a trailing '/': the manifest renders the "
                "directory marker itself, and a path that carries one too "
                "makes the rendered line ambiguous")
    segments = raw.split("/")
    if ".." in segments:
        return ("a path escaping the repository root through a '..' "
                "segment, which no repository tree carries")
    if "." in segments:
        return ("a non-canonical path (a '.' segment): "
                f"`{_SHOW}` resolves it against the caller's directory, "
                "so the two sides would not read the same object")
    if "" in segments[1:] or raw.startswith("/"):
        # A leading '/' is absolute (the caller names that state itself);
        # an empty inner segment is a doubled separator.
        if raw.startswith("/"):
            return "an absolute path, which no repository tree carries"
        return ("a path with an empty segment ('//'), which names no entry "
                "in any tree")
    return None


def object_error(mode: str) -> str | None:
    """Why the target tree's object at this mode cannot be a reference, or
    None if it can. The rule is that both sides must derive the same bytes:
    a regular blob does, a link and a submodule pointer do not."""
    if mode in MODE_REGULAR:
        return None
    if mode == MODE_SYMLINK:
        return ("a tracked symlink: emission would digest the bytes it "
                "points at while `take` digests the link itself, so the "
                "reference arrives as a mismatch — and a link to an "
                "untracked or ignored file points the reviewer at bytes no "
                "commit carries")
    if mode == MODE_GITLINK:
        return ("a submodule pointer (gitlink): the bytes live in another "
                "repository, and the target tree carries only the commit id")
    return (f"tracked with object mode {mode}, which is not a regular file: "
            f"the reviewer's `take` has no defined way to read it from the "
            f"target tree")


def target_kind(run_text, sha: str, path: str) -> str | None:
    """'blob' | 'tree' | None, read from the TARGET TREE.

    `run_text(*args) -> str` is the caller's git runner. Both the emitter
    and `take` decide a reference's kind here, so neither can decide it
    from the working tree — which is the state the reviewer cannot see.
    """
    try:
        kind = run_text("cat-file", "-t", f"{sha}:{path.rstrip('/')}")
    except RuntimeError:
        return None
    kind = (kind or "").strip()
    return kind or None


def target_digest(run_bytes, sha: str, path: str) -> str:
    """sha256 of the bytes the TARGET TREE carries at `path`.

    `run_bytes(*args) -> bytes`. The one digest both sides compute: the
    emitter writes it into the manifest, `take` recomputes it from the same
    object, so a match means the reviewer read what the author sent.
    """
    return hashlib.sha256(run_bytes("show", f"{sha}:{path}")).hexdigest()

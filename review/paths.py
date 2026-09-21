"""Path rendering, centralized (lineage 5 round 1, F3).

Two renderers because the two destinations have opposite needs, and one
value cannot serve both. A SHELL COMMAND needs an argv that survives the
shell: `git -C /tmp/repo with space diff …` splits into the wrong argv, so
command rendering quotes — and it never abbreviates, because `~` inside
quotes does not expand, so a home-relative form and quoting cannot compose;
in a command, identity beats brevity. HUMAN PROSE needs the shortest
truthful name: an exact home prefix reads as `~`, which is shorter, stable
across machines, and does not disclose the account name in text meant to
travel between sessions. Structured machine fields (JSON values, ledger
events, envelope stamps) use neither — they keep the explicit absolute
path, because identity is their whole job.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

_SAFE_TOKEN = re.compile(r"[A-Za-z0-9._/@:+-]+")


def shell_path(path) -> str:
    """`path` as one shell word, safe to interpolate into a command an
    agent runs verbatim: quoted exactly when the shell would otherwise
    split or interpret it, byte-identical to the input when it would not —
    so ordinary paths render unchanged and a space, quote, semicolon or
    other metacharacter arrives as part of the one argument it belongs
    to."""
    return shlex.quote(str(path))


def shell_word(value) -> str:
    """Any dynamic value as one shell word — the same renderer as
    `shell_path`, under the name that says what the rule actually is
    (round 4 F1).

    "Not a path" was read as "safe unquoted", and it is not: a reviewer
    identity and a Git ref are both dynamic values that reach a command an
    agent runs verbatim, and Git accepts `refs/heads/topic;printf` as a ref
    name. Every dynamic word in a command goes through here; for ordinary
    tokens — a SHA, a plain identity, a branch without metacharacters —
    the output is byte-identical to the input, so quoting everything costs
    nothing and closes the class.
    """
    return shlex.quote(str(value))


# The word grammar, one type per meaning (lineage 6 round 1, F1). Three
# kinds of word render UNQUOTED, and the previous grammar let them share one
# type: a `Lit` could carry a trailing semicolon, an unmatched quote or a
# substitution inside `<...>`, because the single regex that admitted
# placeholders also admitted their residue. The types below cannot cross:
#
#   * `Lit`   — a shell-inert word the tool wrote. No bracket, no quote, no
#               operator, no whitespace: exactly `[A-Za-z0-9._/@:+-]+`, the
#               same closed class `safe_token` proves. Nothing a shell acts
#               on can be spelled inside it.
#   * `Op`    — the exact operators the tool writes deliberately: `&&` and
#               `<`. A closed two-member vocabulary; membership is the whole
#               grammar, so no arbitrary value can become one.
#   * `Ph`    — a placeholder a PERSON fills in: `<sha>`, `<sha>:<path>`,
#               `"<why>"`, `[--base <sha>]`. Quotes and brackets exist only
#               in balanced pairs the grammar itself writes, and the inner
#               text is a closed inert class — no substitution, redirect,
#               separator or control character can be spelled. A command
#               that contains one is a `Template`, and a Template is NOT
#               executable: a placeholder can only ever demote a command to
#               something a person must finish, never smuggle bytes into a
#               field an agent runs.
_OPERATORS = frozenset(("&&", "<"))

_PH_ANGLE = r"<[A-Za-z0-9 .,'_/-]+>"
_PH_UNIT = rf"{_PH_ANGLE}(?::{_PH_ANGLE})*"
_PH_INERT = r"[A-Za-z0-9._/@:+-]+"
_PH_QUOTED = rf'"(?:{_PH_UNIT}|{_PH_INERT})"'
_PH_ITEM = rf"(?:{_PH_UNIT}|{_PH_QUOTED}|{_PH_INERT})"
_PH_OPT = rf"\[{_PH_ITEM}(?: {_PH_ITEM})*\]"
_PH_WORD = re.compile(rf"{_PH_UNIT}|{_PH_QUOTED}|{_PH_OPT}")

# Characters no dynamic word may carry into a rendered line: quoting keeps a
# tab or a space inside its one argument, but a line terminator or another
# C0 control would end or corrupt the LINE — and a relay line is the unit a
# person copies and runs.
_CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")

_MINT = object()


def _reject_subclassing(cls):
    """`isinstance` is the door, so a subclass is a key to it.

    `command()` admits any `isinstance(w, Lit)` and `executable()` any
    `isinstance(v, Command)`, so `class MyLit(Lit)` rendered a hostile value
    unquoted and `class MyCmd(Command)` walked through every executable
    field — neither visible to a scan that matches call spellings.
    """
    def __init_subclass__(cls, **kw):
        raise TypeError(
            f"{cls.__name__} may not be subclassed: the executable doors "
            f"admit it by isinstance, so a subclass is a bypass of the "
            f"check, not an extension of it")
    return __init_subclass__


class Command(str):
    """A command line this module rendered from validated segments — the
    ONLY kind of string the tool's executable fields accept (round 5 F1;
    segment validation lineage 6 round 1, F1).

    Three rounds tried to prove commands safe by reading the source that
    built them, and each round the next construction form walked through.
    Source-shape inference fails open by nature: what it cannot analyse it
    has to call prose. So the boundary moved off the shape and onto the
    TYPE, and then off the type alone and onto the SEGMENTS: `command()`
    builds a Command from words that are each validated at construction —
    shell-inert `Lit`s, the two `Op`s, or dynamic values quoted as exactly
    one argument — and a word that could need a person's hand (`Ph`) makes
    the result a `Template`, which the executable doors refuse. There is no
    later moment at which a value could be unrendered, and no word class
    whose grammar can spell a shell action the tool did not write.

    The mint is a construction attribute, not just a type: `str.__new__(
    Command, x)` still constructs an instance, because a `str` subclass
    cannot close its base's constructor — but it constructs one without the
    segment record `command()` writes, and `executable()` checks for that
    record. A deliberately malicious in-process author who forges private
    attributes is outside the threat model (they can already run anything);
    what this closes is every ordinary construction route, and the claim
    stops there.
    """

    __init_subclass__ = classmethod(_reject_subclassing(None))

    def __new__(cls, value="", mint=None):
        if mint is not _MINT:
            raise TypeError(
                "a Command is built by paths.command(...), which validates "
                "every word as it builds it; constructing one directly "
                "asserts that rendering happened without doing it, and the "
                "executable doors would believe the assertion")
        return super().__new__(cls, value)


class Template(str):
    """A command SHAPE with at least one `Ph` in it — something a person
    must finish before it can run (lineage 6 round 1, F1).

    It renders for prose, verb tables and `# comment` lines exactly like a
    Command, and that is all it may do: `executable()` refuses it, so a
    placeholder-bearing template cannot satisfy a field agents execute
    verbatim. The split is what makes the placeholder grammar harmless —
    `<sha>` IS shell syntax, and the only door it can reach is one that
    never runs.
    """

    __init_subclass__ = classmethod(_reject_subclassing(None))

    def __new__(cls, value="", mint=None):
        if mint is not _MINT:
            raise TypeError(
                "a Template is built by paths.command(...) from validated "
                "words; constructing one directly bypasses the word grammar")
        return super().__new__(cls, value)


class Lit(str):
    """A shell-inert word the TOOL wrote — a verb, a flag, a program name.

    Renders UNQUOTED, so the grammar is the entire safety argument: exactly
    `[A-Za-z0-9._/@:+-]+`, the closed class `safe_token` proves, in which
    no operator, separator, quote, bracket, substitution or control
    character can be spelled. An operator is an `Op`; a placeholder is a
    `Ph`; a dynamic value is passed to `command()` bare and quoted as one
    argument. The old grammar admitted all three through one regex, and its
    residue — a trailing semicolon, an unmatched quote, arbitrary bytes
    inside `<...>` — rendered unquoted as "the tool's own word".
    """

    __init_subclass__ = classmethod(_reject_subclassing(None))

    def __new__(cls, value=""):
        text = str(value)
        if not text or not _SAFE_TOKEN.fullmatch(text):
            raise ValueError(
                f"{text!r} may not be a Lit: a Lit renders UNQUOTED, so "
                f"only the shell-inert class [A-Za-z0-9._/@:+-]+ may be "
                f"one. An operator is paths.Op ({sorted(_OPERATORS)} and "
                f"nothing else); a <placeholder> is paths.Ph; a dynamic "
                f"value is passed to command() bare and quoted as one "
                f"argument")
        return super().__new__(cls, text)


class Op(str):
    """One of the exact shell operators this tool writes deliberately:
    `&&` between two commands that must both run, `<` for a stdin
    redirect. Membership in that two-member set is the whole grammar, so
    arbitrary values cannot acquire operator semantics — a dynamic `"&&"`
    handed to `command()` bare is quoted into an inert argument instead.
    """

    __init_subclass__ = classmethod(_reject_subclassing(None))

    def __new__(cls, value=""):
        text = str(value)
        if text not in _OPERATORS:
            raise ValueError(
                f"{text!r} is not an operator this tool writes: the closed "
                f"set is {sorted(_OPERATORS)}, and anything else is either "
                f"a Lit (shell-inert), a Ph (a placeholder), or a dynamic "
                f"value command() quotes")
        return super().__new__(cls, text)


_HEREDOC_DELIM = re.compile(r"[A-Z][A-Z0-9_]*")


class Heredoc(str):
    """The one redirection that carries BYTES after the line: a QUOTED
    heredoc opener, `<<'DELIM'` (relay ergonomics, 2026-08-30 — the paste
    legs fold the command and the envelope into ONE fenced block, and the
    heredoc is what makes that block a single runnable unit rather than a
    command beside bytes a person must marry up).

    The quotes are the safety property and they are written HERE, in
    balanced pair: a quoted delimiter makes the body inert — no expansion,
    no substitution, no command can be spelled by the bytes it carries —
    and the delimiter class [A-Z][A-Z0-9_]* can spell no quote, operator,
    separator or control character, so the rendered form is always exactly
    one operator word. The bytes themselves never pass through this type:
    they are fence content, and the caller chooses the delimiter AGAINST
    them, bumped until no data line equals it — the exact-line collision
    is the one way a heredoc ends early and hands the rest to the shell.
    """

    __init_subclass__ = classmethod(_reject_subclassing(None))

    def __new__(cls, delim=""):
        text = str(delim)
        if not _HEREDOC_DELIM.fullmatch(text):
            raise ValueError(
                f"{text!r} may not open a heredoc: the delimiter class is "
                f"[A-Z][A-Z0-9_]* and nothing else — it renders unquoted "
                f"inside the quoted opener, so only a class that can spell "
                f"no quote or operator keeps the opener one word")
        rendered = super().__new__(cls, f"<<'{text}'")
        rendered.delimiter = text
        return rendered


class Ph(str):
    """A placeholder a PERSON fills in — `<sha>`, `<sha>:<path>`,
    `"<why>"`, `"..."`, `[--base <sha>]`.

    The grammar writes quotes and brackets only in balanced pairs — a
    quoted form via `qph()`, an optional-argument group via `opt()` — and
    the text inside any pair is a closed inert class: letters, digits,
    space, and `.,'_/-` inside angle brackets, the `Lit` class outside
    them. No substitution, redirect, separator, unbalanced quote or
    control character can be spelled. What keeps even that harmless is the
    type split: a command containing a Ph is a `Template`, and a Template
    never satisfies an executable door.
    """

    __init_subclass__ = classmethod(_reject_subclassing(None))

    def __new__(cls, value=""):
        text = str(value)
        if not _PH_WORD.fullmatch(text):
            raise ValueError(
                f"{text!r} may not be a Ph: a placeholder is `<name>` "
                f"(inner text from [A-Za-z0-9 .,'_/-]), a `:`-joined chain "
                f"of those, a quoted form built by qph(), or a bracketed "
                f"optional group built by opt(). A value needing any other "
                f"character is a dynamic word — pass it to command() bare "
                f"and it is quoted as one argument")
        return super().__new__(cls, text)


def qph(body) -> Ph:
    """A placeholder rendered inside VISIBLE double quotes — `"<why>"`,
    `"..."` — for a slot whose filled value will need quoting too. Both
    quotes are written here, so an unbalanced quote cannot be spelled."""
    return Ph(f'"{body}"')


def opt(*words) -> Ph:
    """An optional-argument group — `[--base <sha>]` — from words that are
    already validated types. Both brackets are written here, so an
    unbalanced bracket cannot be spelled; the words must be `Lit` or `Ph`,
    because an optional group is documentation of a slot, never a carrier
    for a dynamic value."""
    for w in words:
        if type(w) not in (Lit, Ph):
            raise TypeError(
                f"opt() takes Lit or Ph words, not {type(w).__name__}: an "
                f"optional group documents a slot a person fills, so every "
                f"word in it is the tool's own vocabulary")
    return Ph("[" + " ".join(str(w) for w in words) + "]")


def token(value) -> Lit:
    """A dynamic value that must render UNQUOTED, proved shell-inert
    first — for the surfaces where a quote would corrupt the document
    being generated rather than protect it (a verb table, a usage hint).
    Raises if the value carries anything but `[A-Za-z0-9._/@:+-]`."""
    return Lit(safe_token(value))


def lits(*words) -> tuple:
    """Several literal words at once: `command(*lits(TOOL_NAME, "close",
    "--verdict"), source)` reads as the command it renders. Literal words
    only — an operator or a placeholder is written as its own type at the
    call site, so the grammar refuses it here rather than blessing it."""
    return tuple(Lit(w) for w in words)


def command(*words):
    """The one command renderer: typed words as written, everything else
    quoted as exactly one shell word.

    `command(Lit("git"), Lit("-C"), repo, Lit("diff"), rev_range)` — the
    tool's own vocabulary is literal, and every value from outside is a
    single argument whatever it carries. An ordinary value renders
    byte-identical to itself, so this changes no existing output.

    Returns a `Command` when every word is a `Lit`, an `Op` or a quoted
    dynamic value — a line that runs exactly as printed. Returns a
    `Template` the moment any word is a `Ph`: a placeholder means a person
    must finish the line, so the result must never satisfy an executable
    door. The choice is made here, structurally, not by any caller's
    promise.
    """
    parts = []
    template = False
    for w in words:
        kind = type(w)
        if kind in (Command, Template):
            raise TypeError(
                f"a {kind.__name__} may not be a word of another command: "
                f"compose words, not rendered lines — a rendered line "
                f"re-quoted would corrupt it, and unquoted would splice it")
        if kind in (Lit, Op, Heredoc):
            parts.append(str(w))
        elif kind is Ph:
            parts.append(str(w))
            template = True
        else:
            text = str(w)
            if _CONTROL.search(text):
                raise ValueError(
                    f"{text!r} carries a control character: quoting keeps "
                    f"any byte inside one argument, but a line terminator "
                    f"would end the LINE, and a relay line is the unit a "
                    f"person copies and runs")
            parts.append(shlex.quote(text))
    cls = Template if template else Command
    rendered = cls(" ".join(parts), _MINT)
    rendered._segments = tuple(str(w) for w in words)
    return rendered


def comment(text) -> Command:
    """A `#` line — guidance that travels INSIDE the fence and runs as a no-op.

    Workshop (c). The relay's prose kept moving because both places it could
    live were wrong. Beside the fence, a relaying agent mangled it — that is
    why round 1's audience line was pulled out and the fence left bare. In
    the brief, it was one hop from the commands it qualified, so the person
    holding a fence that names the REVIEWER's own path had no way to see that
    it would not exist on another machine.

    A comment is the third place, and it is the only one with the properties
    both halves needed: it is inside the block, so it cannot be separated
    from the commands or reworded in transit, and it is a valid shell line
    that does nothing, so it does not cost the block its one real promise —
    that what is in a bash fence can be run exactly as printed.

    That promise is what makes a not-yet-runnable command a comment too. The
    verdict relay used to print `respond` beside `close` with two unfilled
    placeholders in it, so the block a person was told to paste as-is had a
    line in it that could not be. Commented, the whole block is safe to run
    and the author still reads the exact command they will need next.
    """
    line = str(text)
    if "\n" in line or "\r" in line:
        raise ValueError(
            "a comment is one line: a newline inside a fenced block would "
            "end the comment and run whatever followed it")
    rendered = Command("# " + line, _MINT)
    rendered._segments = (line,)
    return rendered


def executable(value, where: str):
    """Refuse anything but a rendered Command in a field agents run.

    The empty string is the tool's own "no command applies" (a blocked
    recovery has none), and it is the only exception.

    Workshop (a): that exception was written `value == ""`, an EQUALITY
    test standing in for a type test, so any object whose `__eq__` answered
    True to `""` was returned to the caller unchanged — a door checking
    types with one branch that asks the value's own opinion of itself.

    Lineage 6 round 1, F1: the door checks the construction record, not the
    type alone — `str.__new__(Command, x)` builds a Command-typed string
    without the segment record `command()` writes, and it is refused here
    for lacking it. And a `Template` is refused BY NAME: a placeholder-
    bearing line is a person's to finish, never an agent's to run.
    """
    if type(value) is str and value == "":
        return value
    if isinstance(value, Template):
        raise TypeError(
            f"{where} must be runnable as printed, and this value carries a "
            f"placeholder a person has to fill in: a Template belongs in "
            f"prose or a # comment, never in a field agents run verbatim")
    if isinstance(value, Command) and getattr(value, "_segments", None):
        return value
    raise TypeError(
        f"{where} must be a rendered command (paths.command(...)), not a "
        f"{type(value).__name__} built by string construction: agents run "
        f"this field verbatim, so every dynamic word in it has to have "
        f"been rendered as it was built (round 5 F1)")


def safe_token(value) -> str:
    """A value that must reach a surface UNQUOTED, proved safe at that use
    (round 4 F1's second renderer).

    Quoting is the right answer almost everywhere, but not where the
    output is a generated document rather than a shell line: a markdown
    verb table rendering `loupe 'close'` is corrupt, and a usage hint
    reading `loupe 'validate' --help` is worse than useless. Those values
    are closed sets — a CLI verb, an adapter surface name, a round number
    — so the safety is provable instead of imposed: anything outside the
    shell-inert set raises rather than rendering, because a value that
    could carry syntax into a document an agent copies from is a defect
    wherever it came from.
    """
    text = str(value)
    if not text or not _SAFE_TOKEN.fullmatch(text):
        raise ValueError(
            f"{text!r} is not a shell-inert token: this surface renders it "
            f"unquoted, so only [A-Za-z0-9._/@:+-] may reach it")
    return text


def diff_command(repo_root, base: str, head: str, *pathspecs: str) -> str:
    """THE diff command both sides print — the emitter into the envelope,
    `take` into the reviewer's result. One renderer, so the two surfaces
    cannot disagree on quoting.

    `pathspecs` (0.25.0) limit it to the claim's `scope_paths` — the
    request's second, scoped diff line — after a `--`, each quoted as one
    shell word; none given, the command is byte-identical to before."""
    # Round 3 F1 (lineage 12). The command a human RUNS must resolve the
    # same object graph the tool read. Without this, a replacement ref on
    # the reviewer's machine shows them a diff that is not the diff the
    # emitter measured, the gates attested, or the verdict will bind — and
    # nothing in the round would say so.
    return command(Lit("git"), Lit("-C"), repo_root,
                   Lit("--no-replace-objects"), Lit("diff"),
                   f"{base}...{head}",
                   *((Lit("--"), *pathspecs) if pathspecs else ()))


def display_path(path) -> str:
    """`path` for human prose: an exact `Path.home()` prefix abbreviates to
    `~` (any POSIX home, not a platform special case); everything else is
    returned as it is. Never fed back into a command — the shell only
    expands `~` unquoted, which is exactly where `shell_path` cannot leave
    it."""
    p = Path(path)
    home = Path.home()
    if p == home:
        return "~"
    try:
        return "~/" + str(p.relative_to(home))
    except ValueError:
        return str(p)

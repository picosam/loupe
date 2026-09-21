"""The verdict précis takes its blocking set from the TARGET's taxonomy
(0.25.0; an adopter's open item, 2026-09-20).

THE DEFECT. `brief.verdict_precis` compared severities against a built-in
`("Blocker", "High")`, case-sensitively, and its three callers — `validate`,
`close` and `brief` — passed no taxonomy. A repository declaring
`severities = ["high", "medium", "low"]` and `blocking = ["high"]` therefore
read "7 findings, of which 0 block", with its `high` finding filed under
Non-blocking, about a verdict its own validator judged by the declared set.
`TestLowercaseTaxonomy` reproduces that case first, through the real CLI.

THE RULE. The blocking set is the one declared by the configuration at the
verdict's target SHA — the authority `validate --from-target`, `take` and
`close` already resolve (`transport.governing_for`) — and membership is the
validator's own (`validate.effective_blocking`: exact match, with the §5.3a
downgrade stated beside the finding). No built-in severity literal remains.
A severity the taxonomy does not declare gets its own heading; a taxonomy
with no blocking list says so; no resolvable taxonomy says blocking cannot
be judged, and invents nothing.

THE PARTITION, one class per row, each through `loupe validate`, `loupe
brief` and `loupe close` as real subprocesses in a scratch repository with
its own configuration (never this workbench's `review.toml`) and its own
state directory:

  capitalised taxonomy                      TestCapitalisedTaxonomy
  lowercase taxonomy (the adopter's case)   TestLowercaseTaxonomy
  mixed case against a lowercase taxonomy   TestMixedCaseFinding
  custom severity names                     TestCustomSeverityNames
  a taxonomy declaring no blocking list     TestNoBlockingList
  a blocking finding with no falsification  TestDowngradeIsShown
  no taxonomy resolvable                    TestNoResolvableTaxonomy

THE MUTATIONS each row falsifies are in the track report's table.

`ScratchLoop` is shared with `test_residue_notice` and
`test_claim_members`: one real repository per test, the CLI run the way
`bin/loupe` runs it, the gate-run re-entrancy markers and the host's
transport declarations scrubbed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from review import TOOL_NAME, env_var, vocab
from review.tests.util import REPO_ROOT

SHIM = REPO_ROOT / "bin" / TOOL_NAME

#: Environment names the scratch CLI must not inherit: the gate-run
#: re-entrancy markers (this suite may itself run inside a gate), the
#: machine's state directory and any out-of-tree configuration.
_SCRUBBED = tuple(env_var(n) for n in ("IN_GATE_RUN", "GATE_HEAD",
                                       "GATE_BASE", "STATE_DIR", "CONFIG"))


def cli_env(state: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in _SCRUBBED and k != vocab.TRANSPORT_ENV
           and k not in {var for var, _v, _t
                         in vocab.TRANSPORT_PROVIDER_SIGNALS}}
    env[env_var("STATE_DIR")] = str(state)
    # What `bin/loupe` sets, for the fallback that runs the package
    # directly where no shim travels.
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONSAFEPATH"] = "1"
    return env


def taxonomy_toml(severities, blocking=None, classifications=("defect",),
                  gates: str = "") -> str:
    """A scratch repository's own review.toml. `blocking=None` omits the
    key entirely — the "declares no blocking list" state."""
    lines = ["[taxonomy]",
             f"severities = {json.dumps(list(severities))}"]
    if blocking is not None:
        lines.append(f"blocking = {json.dumps(list(blocking))}")
    lines += [f"classifications = {json.dumps(list(classifications))}",
              "", "[roles]", 'author = "claude"', 'reviewer = "codex"',
              'relay = "user"', 'transport = "path"', ""]
    return "\n".join(lines) + gates


def finding(n, severity, title=None, evidence="f.txt:1",
            falsification="observation: it is fixed",
            classification="defect", required="fix it") -> str:
    return (f"### F{n}\nSeverity: {severity}\nClassification: "
            f"{classification}\nTitle: {title or f'finding number {n}'}\n"
            f"Evidence: {evidence}\nWhy: because\nRequired outcome: "
            f"{required}\nFALSIFICATION: {falsification}\n\n")


def verdict(sha: str, findings: str, closures: str = "") -> str:
    body = (f'<loupe-review-verdict sha="{sha}">\nVERDICT: changes '
            f"requested\n\n## findings\n\n{findings}")
    if closures:
        body += f"## closures\n\n{closures}\n\n"
    return body + "## evidence checked\n\nf.txt\n</loupe-review-verdict>\n"


class ScratchLoop:
    """One real repository the real CLI runs a round in.

    `toml` is the repository's whole configuration, committed with the
    base. `remote=True` adds a bare `origin` and pushes `main`, for the
    verbs that push; otherwise hand-offs run `--local-only`.
    """

    def __init__(self, case: unittest.TestCase, toml: str, *,
                 remote: bool = False, prefix: str = "t3-"):
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix=prefix))
        except OSError as exc:
            case.skipTest(f"filesystem writes denied ({exc})")
        case.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        self.state = self.tmp / "state"
        self.remote = remote
        self._git_top("init", "-q", "-b", "main", str(self.repo))
        for k, v in (("user.name", "t3"), ("user.email", "t3@example.invalid"),
                     ("commit.gpgsign", "false")):
            self.git("config", k, v)
        if remote:
            self._git_top("init", "-q", "--bare", str(self.tmp / "origin.git"))
            self.git("remote", "add", "origin", str(self.tmp / "origin.git"))
        self.write("review.toml", toml)
        self.write("f.txt", "one\n")
        self.write("g.txt", "a\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "init")
        self.base = self.git("rev-parse", "HEAD")
        if remote:
            self.git("push", "-q", "origin", "main")
        self.write("f.txt", "two\n")
        self.git("commit", "-q", "-am", "change")
        self.head = self.git("rev-parse", "HEAD")

    @staticmethod
    def _git_top(*args) -> str:
        return subprocess.run(["git", *args], check=True, capture_output=True,
                              text=True, timeout=60,
                              stdin=subprocess.DEVNULL).stdout.strip()

    def git(self, *args) -> str:
        return self._git_top("-C", str(self.repo), *args)

    def write(self, rel: str, text: str) -> Path:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def file(self, name: str, text: str) -> Path:
        """A file OUTSIDE the repository (claims, verdicts): nothing the
        hand-off could sweep into a commit."""
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path

    def loupe(self, *argv, cwd: Path | None = None):
        cmd = ([str(SHIM)] if SHIM.is_file()
               else [sys.executable, "-m", "review"])
        proc = subprocess.run(cmd + [str(a) for a in argv],
                              cwd=str(cwd or self.repo),
                              env=cli_env(self.state), capture_output=True,
                              text=True, timeout=300,
                              stdin=subprocess.DEVNULL)
        try:
            return proc.returncode, json.loads(proc.stdout)
        except json.JSONDecodeError:
            return proc.returncode, proc.stdout + proc.stderr

    def claim(self, body: dict | None = None, name="claim.json") -> Path:
        body = body or {"objective": "t3 fixture",
                        "references": [{"path": "review.toml",
                                        "required": True}]}
        return self.file(name, json.dumps(body))

    def handoff(self, claim: Path | None = None, *extra):
        argv = ["handoff", "--claim-file", claim or self.claim(),
                "--base", self.base, *extra]
        if not self.remote:
            argv.append("--local-only")
        return self.loupe(*argv)

    def snapshot(self) -> dict:
        """Refs, index and worktree, byte for byte — what a refusal must
        leave exactly as it found."""
        files = {}
        for path in sorted(self.repo.rglob("*")):
            if ".git" in path.relative_to(self.repo).parts or \
                    not path.is_file():
                continue
            files[str(path.relative_to(self.repo))] = path.read_bytes()
        index = self.repo / ".git" / "index"
        return {"refs": self.git("for-each-ref", "--format=%(refname) "
                                 "%(objectname)"),
                "head": self.git("rev-parse", "HEAD"),
                "symbolic": self.git("symbolic-ref", "-q", "HEAD"),
                "index": index.read_bytes() if index.exists() else b"",
                "files": files}


class _PrecisRow(unittest.TestCase):
    """One taxonomy, one verdict, three verbs. Subclasses declare the
    taxonomy, the findings and what each verb's précis must say."""

    SEVERITIES: tuple = ()
    BLOCKING: tuple | None = ()
    FINDINGS: tuple = ()        # (severity, falsification) per finding

    def setUp(self):
        self.loop = ScratchLoop(self, taxonomy_toml(self.SEVERITIES,
                                                    self.BLOCKING))
        code, out = self.loop.handoff()
        self.assertEqual(code, 0, out)
        body = "".join(finding(i, sev, falsification=fals)
                       for i, (sev, fals) in enumerate(self.FINDINGS, 1))
        self.verdict = self.loop.file("verdict.md",
                                      verdict(self.loop.head, body))

    def precis(self):
        """(validate, validate --from-target, brief, close) as
        (exit, payload) pairs, in that order — close last, since it
        records the round."""
        return {
            "validate": self.loop.loupe("validate", self.verdict),
            "validate --from-target": self.loop.loupe(
                "validate", self.verdict, "--from-target"),
            "brief": self.loop.loupe("brief", self.verdict),
            "close": self.loop.loupe("close", "--verdict", self.verdict),
        }

    def assert_every_verb(self, expect_in=(), expect_not_in=(),
                          close_refuses=False):
        for verb, (code, payload) in self.precis().items():
            if verb == "close" and close_refuses:
                self.assertNotEqual(code, 0, f"{verb}: {payload}")
                self.assertNotIn("brief", payload,
                                 "a refused close prints no précis")
                continue
            self.assertIsInstance(payload, dict, f"{verb}: {payload}")
            text = payload.get("brief") or ""
            for needle in expect_in:
                self.assertIn(needle, text, f"{verb}:\n{text}")
            for needle in expect_not_in:
                self.assertNotIn(needle, text, f"{verb}:\n{text}")


def _section(text: str, heading: str) -> str:
    """The bullet block under one bold précis heading."""
    after = text.split(heading, 1)[1] if heading in text else ""
    return after.split("\n\n**", 1)[0]


class TestCapitalisedTaxonomy(_PrecisRow):
    SEVERITIES = ("High", "Medium", "Low")
    BLOCKING = ("High",)
    FINDINGS = (("High", "observation: it is fixed"),
                ("Medium", "observation: x"), ("Low", "observation: y"))

    def test_the_declared_capitalised_set_blocks(self):
        self.assert_every_verb(
            expect_in=("- **Findings** — 3, of which 1 block",
                       "**Blocking — these stop the change advancing**",
                       "- **F1** · High — finding number 1"),
            expect_not_in=("cannot be judged", "not in the target's"))


class TestLowercaseTaxonomy(_PrecisRow):
    """The adopter's case, reproduced first: "7 findings, of which 0
    block" before this change, 1 after, on every verb."""
    SEVERITIES = ("high", "medium", "low")
    BLOCKING = ("high",)
    FINDINGS = (("high", "observation: it is fixed"),
                ("medium", "o"), ("medium", "o"), ("medium", "o"),
                ("low", "o"), ("low", "o"), ("low", "o"))

    def test_seven_findings_of_which_one_blocks(self):
        for verb, (code, payload) in self.precis().items():
            self.assertEqual(code, 0, f"{verb}: {payload}")
            text = payload["brief"]
            self.assertIn("- **Findings** — 7, of which 1 block", text, verb)
            self.assertNotIn("of which 0 block", text, verb)
            blocking = _section(text, "**Blocking — these stop the change "
                                      "advancing**")
            self.assertIn("**F1** · high", blocking, verb)
            self.assertNotIn("**F1**", _section(text, "**Non-blocking**"),
                             verb)


class TestMixedCaseFinding(_PrecisRow):
    """`High` against a lowercase taxonomy: not blocking, not non-blocking
    — its own heading, because the validator refuses the severity and a
    précis filing it as advisory would describe an unrecordable verdict."""
    SEVERITIES = ("high", "low")
    BLOCKING = ("high",)
    FINDINGS = (("High", "observation: it is fixed"),
                ("high", "observation: it is fixed"))

    def test_an_undeclared_severity_is_named_as_such(self):
        runs = self.precis()
        for verb in ("validate", "validate --from-target", "brief"):
            code, payload = runs[verb]
            text = payload["brief"]
            self.assertIn("- **Findings** — 2, of which 1 block", text, verb)
            self.assertIn("- **Severity not in the target's taxonomy** — 1 "
                          "finding(s)", text, verb)
            outside = _section(text, "**Severity not in the target's "
                                     "taxonomy — neither")
            self.assertIn("**F1** · High", outside, verb)
            self.assertNotIn("**F1**", _section(text, "**Non-blocking**"),
                             verb)
        # validate refuses the verdict (V-SEVERITY) and still describes it;
        # close refuses to record it and prints no précis at all.
        self.assertEqual(runs["validate"][0], 1)
        self.assertIn("V-SEVERITY",
                      [i["code"] for i in runs["validate"][1]["items"]])
        self.assertNotEqual(runs["close"][0], 0)
        self.assertNotIn("brief", runs["close"][1])


class TestCustomSeverityNames(_PrecisRow):
    SEVERITIES = ("sev1", "sev2", "sev3")
    BLOCKING = ("sev1",)
    FINDINGS = (("sev1", "observation: it is fixed"), ("sev2", "o"),
                ("sev3", "o"))

    def test_names_no_built_in_set_knows(self):
        self.assert_every_verb(
            expect_in=("- **Findings** — 3, of which 1 block",
                       "- **F1** · sev1 — finding number 1"),
            expect_not_in=("cannot be judged",))


class TestNoBlockingList(_PrecisRow):
    SEVERITIES = ("High", "Low")
    BLOCKING = None         # the key is absent from review.toml
    FINDINGS = (("High", "observation: it is fixed"), ("Low", "o"))

    def test_the_precis_says_the_target_declares_none(self):
        self.assert_every_verb(
            expect_in=("- **Findings** — 2; the target declares no "
                       "blocking severities, so none of them blocks by "
                       "declaration", "**Non-blocking**"),
            expect_not_in=("of which", "**Blocking —"))


class TestDowngradeIsShown(_PrecisRow):
    """§5.3a: a blocking-severity finding with no falsification test is
    advisory by rule; the précis counts it as not blocking and says why,
    beside the finding."""
    SEVERITIES = ("High", "Low")
    BLOCKING = ("High",)
    FINDINGS = (("High", ""), ("High", "observation: it is fixed"),
                ("Low", "o"))

    def test_the_downgrade_is_counted_and_named(self):
        self.assert_every_verb(expect_in=(
            "- **Findings** — 3, of which 1 block; 1 of blocking severity "
            "downgraded to advisory (no falsification test, §5.3a)",
            "- **F1** · High (advisory by §5.3a: blocking severity, no "
            "falsification test) — finding number 1",
            "- **F2** · High — finding number 2"))


class TestTheTargetNotTheCheckout(_PrecisRow):
    """The checkout's configuration is NOT the authority: here it has
    since dropped the blocking list, while the target commit still declares
    `blocking = ["high"]`. Every verb — `validate` with and without
    `--from-target`, `brief`, `close` — counts by the target."""
    SEVERITIES = ("high", "low")
    BLOCKING = ("high",)
    FINDINGS = (("high", "observation: it is fixed"), ("low", "o"))

    def setUp(self):
        super().setUp()
        self.loop.write("review.toml", taxonomy_toml(("high", "low"), ()))

    def test_every_verb_counts_by_the_target(self):
        self.assert_every_verb(
            expect_in=("- **Findings** — 2, of which 1 block",),
            expect_not_in=("declares no blocking severities",))


class TestNoResolvableTaxonomy(unittest.TestCase):
    """No resolvable taxonomy: the précis says blocking cannot be judged,
    and says why; nothing is invented, and `close` — which may only record
    under the target's authority — refuses rather than print a count."""

    FINDINGS = finding(1, "High") + finding(2, "Low")

    def test_a_target_this_clone_does_not_hold(self):
        loop = ScratchLoop(self, taxonomy_toml(("High", "Low"), ("High",)))
        code, out = loop.handoff()
        self.assertEqual(code, 0, out)
        stranger = "0123456789abcdef" * 2 + "01234567"
        v = loop.file("v.md", verdict(stranger, self.FINDINGS))
        for argv in (("validate", v), ("brief", v)):
            code, payload = loop.loupe(*argv)
            text = payload["brief"]
            self.assertIn("- **Findings** — 2; blocking cannot be judged "
                          "here — target 0123456789ab is not in this clone",
                          text, argv)
            self.assertNotIn("of which", text, argv)
            self.assertIn("**Findings — not partitioned: no target taxonomy "
                          "to judge blocking by**", text, argv)
        code, payload = loop.loupe("close", "--verdict", v)
        self.assertNotEqual(code, 0, payload)
        self.assertNotIn("brief", payload)

    def test_a_target_whose_configuration_declares_no_taxonomy(self):
        loop = ScratchLoop(self, '[roles]\nauthor = "claude"\n'
                                 'reviewer = "codex"\n')
        v = loop.file("v.md", verdict(loop.head, self.FINDINGS))
        for argv in (("validate", v), ("brief", v)):
            code, payload = loop.loupe(*argv)
            text = payload["brief"]
            self.assertIn("- **Findings** — 2; blocking cannot be judged "
                          "here — the target's configuration declares no "
                          "taxonomy", text, argv)
            self.assertNotIn("of which", text, argv)
        code, payload = loop.loupe("validate", v)
        self.assertEqual(code, 1, "T-UNDECLARED still refuses to rule")
        code, payload = loop.loupe("close", "--verdict", v)
        self.assertNotEqual(code, 0, payload)
        self.assertNotIn("brief", payload)


class TestNoLiteralRemains(unittest.TestCase):
    """The précis has no default set to fall back to: called without a
    governing taxonomy it says blocking cannot be judged, whatever the
    severities happen to be spelt."""

    def test_no_governing_taxonomy_means_no_partition(self):
        from review import brief, wire
        v = wire.parse_verdict(verdict("a" * 40, finding(1, "Blocker")
                                       + finding(2, "High")))
        text = brief.verdict_precis(v)
        self.assertIn("blocking cannot be judged here", text)
        self.assertNotIn("of which", text)
        self.assertNotIn("**Blocking —", text)


if __name__ == "__main__":
    unittest.main()

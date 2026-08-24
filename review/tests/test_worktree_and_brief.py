"""Worktree identity, and the précis/relay the human carries.

Two fixes with one thing in common: both were invisible until someone used the
tool the way a person actually would. `repo_identity` hashed the origin URL so
the id survived a clone, then prefixed the *current* directory's basename, so
a linked worktree resolved to its own empty ledger — round number, cap,
breakers, lineage and budget all silently reset. And the one human step in the
loop, carrying an envelope between two agents, produced a path and no account
of what was in it.

The identity tests build real repositories and real worktrees: the defect was
in what git reports from a linked worktree, which no injected runner would
have reproduced. They self-skip where filesystem writes are denied, as the
loop integration does.
"""
import json
import re
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from review import TOOL_NAME, brief, cli, config, wire


def _sh(*args):
    subprocess.run(args, check=True, capture_output=True, text=True,
                   timeout=60)


def _git(where, *args):
    out = subprocess.run(["git", "-C", str(where), *args], check=True,
                         capture_output=True, text=True, timeout=60)
    return out.stdout.strip()


class TestWorktreeIdentity(unittest.TestCase):
    """A linked worktree is the same repository, so it is one review record."""

    def setUp(self):
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="wt-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.main = self.tmp / "main-checkout"
        _sh("git", "init", "-q", "-b", "main", str(self.main))
        for k, v in (("user.name", "a"), ("user.email", "a@example.invalid"),
                     ("commit.gpgsign", "false")):
            _sh("git", "-C", str(self.main), "config", k, v)
        (self.main / "f.txt").write_text("one\n", encoding="utf-8")
        _sh("git", "-C", str(self.main), "add", ".")
        _sh("git", "-C", str(self.main), "commit", "-q", "-m", "init")

    def _worktree(self, name="linked"):
        path = self.tmp / name
        _sh("git", "-C", str(self.main), "worktree", "add", "-q", "-b", name,
            str(path))
        return path

    def test_worktree_resolves_to_the_same_identity_with_an_origin(self):
        _sh("git", "-C", str(self.main), "remote", "add", "origin",
            "git@example.invalid:owner/proj.git")
        linked = self._worktree()
        self.assertEqual(config.repo_identity(self.main),
                         config.repo_identity(linked))

    def test_worktree_resolves_to_the_same_identity_without_an_origin(self):
        # The path-keyed branch is the one a worktree breaks hardest: every
        # worktree has a different path, so before the fix nothing matched.
        linked = self._worktree()
        self.assertEqual(config.repo_identity(self.main),
                         config.repo_identity(linked))

    def test_the_name_half_comes_from_the_origin_not_the_directory(self):
        # Both halves must derive from one key. A directory renamed or a
        # worktree checked out elsewhere must not move the ledger.
        _sh("git", "-C", str(self.main), "remote", "add", "origin",
            "git@example.invalid:owner/proj.git")
        self.assertTrue(config.repo_identity(self.main).startswith("proj-"))

    def test_origin_url_forms_yield_the_same_name(self):
        for url, expected in (
                ("git@example.invalid:owner/proj.git", "proj"),
                ("https://example.invalid/owner/proj.git", "proj"),
                ("https://example.invalid/owner/proj", "proj"),
                ("/srv/git/proj.git", "proj")):
            with self.subTest(url=url):
                self.assertEqual(config._identity_name(url, Path("/x/other")),
                                 expected)

    def test_no_origin_falls_back_to_the_main_worktree_name(self):
        # rsplit: the id is <name>-<hash8> and the name may itself contain a
        # hyphen, as this fixture's "main-checkout" does.
        self.assertEqual(
            config.repo_identity(self._worktree()).rsplit("-", 1)[0],
            self.main.name)

    def test_canonical_root_of_a_worktree_is_the_main_checkout(self):
        self.assertEqual(config.canonical_root(self._worktree()).resolve(),
                         self.main.resolve())

    def test_outside_git_the_start_directory_is_its_own_root(self):
        plain = self.tmp / "not-a-repo"
        plain.mkdir()
        self.assertEqual(config.canonical_root(plain), plain)


class TestOpenRequestDiscovery(unittest.TestCase):
    """`brief` with no argument resolves the one live request, or refuses."""

    def _ledger(self, events):
        from review.ledger import Ledger
        led = Ledger.in_memory()
        for e in events:
            led.add(e)
        return led

    def test_nothing_emitted_means_nothing_open(self):
        event, superseded = brief.find_open_request(self._ledger([]))
        self.assertIsNone(event)
        self.assertEqual(superseded, 0)

    def test_a_ruled_round_is_not_open(self):
        led = self._ledger([{"event": "request", "round": 1, "sha": "a"},
                            {"event": "verdict", "round": 1, "sha": "a"}])
        self.assertIsNone(brief.find_open_request(led)[0])

    def test_the_newest_emission_wins_and_the_rest_are_reported(self):
        led = self._ledger([{"event": "request", "round": 1, "sha": "old"},
                            {"event": "request", "round": 1, "sha": "new"}])
        event, superseded = brief.find_open_request(led)
        self.assertEqual(event["sha"], "new")
        self.assertEqual(superseded, 1)

    def test_the_highest_open_round_wins(self):
        led = self._ledger([{"event": "request", "round": 1, "sha": "a"},
                            {"event": "verdict", "round": 1, "sha": "a"},
                            {"event": "request", "round": 2, "sha": "b"}])
        event, superseded = brief.find_open_request(led)
        self.assertEqual(event["sha"], "b")
        self.assertEqual(superseded, 0)

    def test_a_closed_lineage_does_not_leak_into_the_next(self):
        led = self._ledger([{"event": "request", "round": 1, "sha": "a"},
                            {"event": "lineage_closed", "round": 1}])
        self.assertIsNone(brief.find_open_request(led)[0])


class TestPrecisIsDerived(unittest.TestCase):
    """The précis restates the envelope; it never adds to it."""

    def _request(self, **kw):
        from review.tests.test_transport import request_text
        return wire.parse_request(request_text(**kw))

    def test_it_names_both_sides_and_the_round(self):
        text = brief.request_precis(self._request(round_no=2))
        self.assertIn("codex", text)
        self.assertIn("claude", text)
        self.assertIn("Round", text)

    def test_an_unreachable_target_is_stated_as_unreachable(self):
        text = brief.request_precis(self._request(push=False))
        self.assertIn("Reachable", text)

    def test_an_empty_diff_is_called_out(self):
        # base == target is the shape that silently reviews nothing.
        same = self._request(sha="f" * 40, base="f" * 40)
        self.assertIn("EMPTY", brief.request_precis(same))

    def test_pushed_target_does_not_claim_universal_access(self):
        """Round 1 F9 (Low), falsification.

        The Push line attests an ls-remote observation — the ref is present on
        the remote — and says nothing about who may read that remote. The
        précis said "any machine can fetch it", which is false for exactly the
        case this repository is in: a private remote.
        """
        text = brief.request_precis(self._request(push=True))
        self.assertNotIn("any machine", text)
        self.assertIn("access", text)

    def test_unavailable_reference_is_not_called_digested(self):
        """Round 1 F10 (Low), falsification.

        Every reference was reported as digested regardless of the state the
        envelope recorded, including ones the author declared unavailable and
        ones carried with no digest at all.
        """
        refs = (f"  a.md  sha256:{'0' * 64}  [required] has a digest\n"
                f"  b.md  UNAVAILABLE from this surface — mark [required]\n"
                f"  c.md  [required] asserted with no digest\n"
                f"  d/  (directory; per-file digests via git) [required]\n")
        text = brief.request_precis(self._request(refs=refs))
        self.assertNotIn("digested each one", text)
        self.assertIn("declared unavailable", text)
        self.assertIn("asserted, no digest", text)

    def test_reference_states_are_counted_separately(self):
        refs = (f"  a.md  sha256:{'0' * 64}  [required] x\n"
                f"  b.md  UNAVAILABLE from this surface — mark [required]\n"
                f"  d/  (directory; per-file digests via git) [required]\n"
                f"  c.md  [required] carries nothing the grammar accepts\n")
        states = brief._reference_states(refs)
        self.assertEqual(states["with a digest to check"], 1)
        self.assertEqual(states["declared unavailable"], 1)
        self.assertEqual(states["directories"], 1)
        self.assertEqual(states["asserted, no digest"], 1)

    def test_only_states_that_occur_are_reported(self):
        # A précis that lists every bucket including the empty ones would say
        # more than the envelope again, in a quieter way.
        refs = f"  a.md  sha256:{'0' * 64}  [required] x\n"
        text = brief.request_precis(self._request(refs=refs))
        self.assertIn("1 with a digest to check", text)
        self.assertNotIn("declared unavailable", text)

    def test_the_heading_is_the_verdict_itself(self):
        # User-directed 2026-08-22: the first thing the carrier reads is the
        # ruling, not a label for it. A clean verdict's relay carries only
        # the close command — respond answers findings a clean verdict does
        # not have.
        v = wire.parse_verdict(
            '<loupe-review-verdict sha="abc">\n'
            "VERDICT: clean to advance\n\n## findings\n\nNone\n"
            "\n## evidence checked\n\n- the tree\n")
        self.assertIn("## Clean to advance", brief.verdict_precis(v))
        relay = brief.verdict_relay(v)
        self.assertIn(f"{TOOL_NAME} close --verdict", relay)
        self.assertNotIn("respond", relay)

    def test_no_verdict_line_keeps_the_generic_heading(self):
        # The heading never invents a ruling: absent a VERDICT line, the
        # generic heading returns and the absence is stated as itself.
        v = wire.parse_verdict(
            '<loupe-review-verdict sha="abc">\n'
            "\n## findings\n\nNone\n"
            "\n## evidence checked\n\n- the tree\n")
        precis = brief.verdict_precis(v)
        self.assertIn("## What this rules", precis)
        self.assertIn("no verdict line found", precis)

    def test_both_legs_render_one_fence_whose_every_line_runs_as_printed(self):
        """The relay convention, workshop (c) — one shape, both legs.

        The rulings this replaces, in order. 2026-08-20: the verdict relay
        carries the AUTHOR's commands and must say so, as a prose line in the
        relay. 2026-08-22: a relaying agent mangled that prose, so the relay
        became a bare fence and the statement moved to the brief. Neither
        held, because both were arguing about where prose goes NEXT TO a
        fence, and a person handed the fence alone still held commands naming
        a path on someone else's machine.

        The convention now: heading, one fence, and every line inside it
        either a live command or a `#` comment. Prose is inside the block, so
        it cannot be separated from what it qualifies or reworded in transit,
        and the block keeps one promise a reader can rely on — paste it and
        exactly the live lines run.
        """
        from review.tests.test_transport import request_text
        legs = {}
        # RVW-T11: the shape is the shape under EITHER declaration. A
        # topology that changes which command is printed must not be able to
        # change the promise the block makes about itself.
        for declared in ("path", "paste"):
            for verdict in ("clean to advance", "changes requested"):
                v = wire.parse_verdict(
                    f'<loupe-review-verdict sha="abc">\n'
                    f"VERDICT: {verdict}\n\n## findings\n\nNone\n"
                    f"\n## evidence checked\n\n- the tree\n")
                legs[f"verdict/{declared}/{verdict}"] = (
                    brief.verdict_relay(v, source="/tmp/v.md",
                                        transport=declared),
                    "## What to run next")
            req = wire.parse_request(request_text(transport_attr=declared))
            # `paste=False` on both: the declared-paste leg appends the bytes
            # after the fence, and this gate is about the fence.
            relay = brief.relay("/tmp/r.md", req, "bytes")
            legs[f"request/{declared}"] = (
                relay.split("\n\nThe envelope, to paste:")[0],
                "## How to carry it")

        for name, (relay, heading) in legs.items():
            with self.subTest(leg=name):
                lines = relay.splitlines()
                self.assertEqual(lines[0], heading)
                self.assertEqual(lines[1], "")
                self.assertTrue(lines[2].startswith("```"))
                self.assertTrue(lines[-1].startswith("```"))
                self.assertEqual(relay.count("```"), 2,
                                 "one fence, opened once and closed once")
                body = lines[3:-1]
                self.assertTrue(body, "the fence is empty")
                for line in body:
                    self.assertTrue(
                        line.startswith(TOOL_NAME) or line.startswith("#"),
                        f"neither a command nor a comment: {line!r}")
                # Exactly one live line: a block is one action, and the
                # alternatives beside it are alternatives, not steps.
                live = [l for l in body if not l.startswith("#")]
                self.assertEqual(len(live), 1, f"expected one live line: {body}")
                # And no comment restates what the brief already carries: the
                # request leg's audience is its precis's Direction line, so
                # only the verdict leg — whose brief has no equivalent —
                # names its audience here.
                named = any("author's to run" in l for l in body)
                self.assertEqual(named, name.startswith("verdict"),
                                 f"{name}: audience line in the wrong leg")

    def test_a_relay_block_is_valid_shell_and_runs_only_its_live_lines(self):
        """The promise, checked by a shell rather than asserted.

        This is what the old verdict fence could not do. Its second line was
        `loupe respond ... --from-json <dispositions.json> --out
        <disposition.md>`, and `<...>` is a REDIRECTION to a shell: the line
        is a syntax error with a dangling `>`, so the block a person was told
        to paste as-is did not parse. Not a matter of taste — the fence made
        a promise the bytes broke.
        """
        import subprocess
        v = wire.parse_verdict(
            '<loupe-review-verdict sha="abc">\n'
            "VERDICT: changes requested\n\n## findings\n\n- F1\n"
            "\n## evidence checked\n\n- the tree\n")
        from review.tests.test_transport import request_text
        legs = []
        for declared in ("path", "paste"):
            req = wire.parse_request(request_text(transport_attr=declared))
            legs.append((f"verdict/{declared}",
                         brief.verdict_relay(v, source="/tmp/v.md",
                                             transport=declared)))
            legs.append((f"request/{declared}",
                         brief.relay("/tmp/r.md", req, "bytes")))
        for name, relay in legs:
            with self.subTest(leg=name):
                # The bash fence, and only it. A declared-paste request leg
                # prints the envelope bytes below the block in a fence of
                # their own, and those bytes are not shell — slicing to the
                # last line of the section would hand this gate a document
                # to parse instead of the commands it exists to check.
                lines = relay.splitlines()
                opened = lines.index("```bash")
                closed = lines.index("```", opened + 1)
                body = "\n".join(lines[opened + 1:closed])
                syntax = subprocess.run(["bash", "-n"], input=body, text=True,
                                        capture_output=True)
                self.assertEqual(syntax.returncode, 0,
                                 f"the block a person pastes does not parse: "
                                 f"{syntax.stderr}")
                # Only the live lines execute; the commented alternatives and
                # the not-yet-runnable next step stay inert.
                traced = subprocess.run(
                    ["bash", "-c", body.replace(TOOL_NAME, "echo RAN:")],
                    text=True, capture_output=True)
                ran = [l for l in traced.stdout.splitlines()
                       if l.startswith("RAN:")]
                self.assertEqual(len(ran), 1,
                                 f"expected exactly one live line, ran {ran}")

    def test_headings_are_sentence_case_on_every_surface(self):
        """Round-1 F4: the headings were hard-coded all-caps and the tests
        froze the accident as protocol. They are presentation, consumed by
        no validator or wire grammar, so they render in sentence case on
        all four surfaces — request brief, request relay, verdict brief,
        verdict relay — while the actual machine vocabulary (the VERDICT
        line the wire grammar consumes) keeps its mandated casing."""
        from review.tests.test_transport import request_text
        req = wire.parse_request(request_text())
        v = wire.parse_verdict(
            '<loupe-review-verdict sha="abc">\n'
            "VERDICT: clean to advance\n\n## findings\n\nNone\n"
            "\n## evidence checked\n\n- the tree\n")
        surfaces = {
            "request brief": (brief.request_precis(req), "## What this asks"),
            "request relay": (brief.relay("/tmp/r.md", req, "bytes"),
                              "## How to carry it"),
            "verdict brief": (brief.verdict_precis(v), "## Clean to advance"),
            "verdict relay": (brief.verdict_relay(v), "## What to run next"),
        }
        for name, (text, heading) in surfaces.items():
            self.assertIn(heading, text, name)
            self.assertNotIn(heading.upper(), text, name)
        # The machine identifier is not presentation and does not soften.
        self.assertEqual(v.verdict, "clean to advance")

    def test_a_verdict_names_its_findings_rather_than_counting_them(self):
        """The user-reported gap: ten findings summarised as "1 Blocker, 4
        High" is not an account of anything, and the human holding the relay
        cannot intervene on a tally.
        """
        v = wire.parse_verdict(
            '<loupe-review-verdict sha="abc">\n'
            "VERDICT: changes requested\n\n## findings\n\n"
            "### F1\nSeverity: Blocker\nClassification: design_gap\n"
            "Title: a clean verdict can be filed for an unreviewed commit\n"
            "Evidence: transport.py:406\nWhy: it defeats the binding\n"
            "Required outcome: derive the round from the SHA\n"
            "FALSIFICATION: command: `run the test`\n\n"
            "### F2\nSeverity: Low\nClassification: factual_error\n"
            "Title: the precis overstates reachability\n"
            "Evidence: brief.py:1\nWhy: it says more than the envelope\n"
            "Required outcome: say what the push line attests\n"
            "FALSIFICATION: command: `run the other test`\n"
            "\n## evidence checked\n\n- the diff\n")
        text = brief.verdict_precis(v, source="/tmp/v.md")
        self.assertIn("unreviewed commit", text)      # the title, not a count
        self.assertIn("F1", text)
        self.assertIn("F2", text)
        self.assertIn("1 block", text)                # blocking count, still
        # The literal next step lives in the relay now, and the précis must
        # not carry a command at all — that merge is what made every reviewer
        # invent its own split of the two.
        self.assertNotIn(f"{TOOL_NAME} close", text)
        self.assertNotIn(f"{TOOL_NAME} respond", text)
        relay = brief.verdict_relay(v, source="/tmp/v.md")
        self.assertIn("/tmp/v.md", relay)
        self.assertNotIn("<verdict.md>", relay)       # not a placeholder
        self.assertIn(f"{TOOL_NAME} close --verdict /tmp/v.md", relay)
        # Blocking findings are separated from the rest, so the reader sees
        # what actually stops the change without decoding severity names.
        self.assertIn("Blocking", text)
        self.assertIn("Non-blocking", text)

        # The reviewer's Required outcome is a specification for whoever
        # implements the fix, not something a person carrying the envelope
        # needs. It is the densest part of a verdict, so it is opt-in.
        self.assertNotIn("derive the round from the SHA", text)
        self.assertIn("--full", text)
        detailed = brief.verdict_precis(v, source="/tmp/v.md", full=True)
        self.assertIn("derive the round from the SHA", detailed)
        self.assertNotIn("--full", detailed)

    def test_the_default_verdict_precis_stays_short(self):
        # The complaint that produced this shape: nine findings rendered long
        # enough that the reader could not see the whole ruling at once.
        findings = "".join(
            f"### F{i}\nSeverity: High\nClassification: design_gap\n"
            f"Title: finding number {i}\nEvidence: f.py:1\nWhy: because\n"
            f"Required outcome: {'a long specification paragraph. ' * 12}\n"
            f"FALSIFICATION: command: `run it`\n\n" for i in range(1, 10))
        v = wire.parse_verdict(
            f'<loupe-review-verdict sha="abc">\nVERDICT: changes requested\n'
            f"\n## findings\n\n{findings}\n## evidence checked\n\n- x\n")
        lines = brief.verdict_precis(v).splitlines()
        self.assertLessEqual(len(lines), 22,
                             "nine findings must still fit on a screen")
        self.assertEqual(sum(1 for ln in lines if "finding number" in ln), 9,
                         "every finding is still named")

    def test_a_verdict_precis_without_a_source_still_reads(self):
        v = wire.parse_verdict(
            '<loupe-review-verdict sha="abc">\n'
            "VERDICT: clean to advance\n\n## findings\n\nNone\n"
            "\n## evidence checked\n\n- the diff\n")
        self.assertIn("<verdict.md>", brief.verdict_relay(v))

    def test_the_precis_survives_a_markdown_renderer(self):
        """Round 5, the user's fourth report of one problem.

        Separating the brief from the relay fixed WHICH content went where
        and not HOW it was formatted for where it lands. The précis was a
        fixed-width table — two-space indents, labels padded to a column —
        which reads perfectly in a terminal and is destroyed by every rich
        text renderer between here and the person: markdown collapses runs of
        spaces and strips leading indent, so `  Verdict      changes
        requested` arrives as `Verdict changes requested` and the whole
        account becomes an unstructured wall.

        The only way to preserve alignment was a code fence, which is the
        copy-this signal that belongs to the relay — so the readable half was
        trapped between collapsing and looking like something to run.

        This asserts the property directly: put the précis through the two
        transformations a markdown renderer performs, and it must still carry
        its structure.
        """
        v = wire.parse_verdict(
            '<loupe-review-verdict sha="abc">\n'
            "VERDICT: changes requested\n\n## findings\n\n"
            "### F1\nSeverity: Blocker\nClassification: design_gap\n"
            "Title: something is badly wrong\nEvidence: x.py:1\n"
            "Why: because\nRequired outcome: fix it\n"
            "FALSIFICATION: command: `run it`\n"
            "\n## evidence checked\n\n- the diff\n")
        precis = brief.verdict_precis(v, source="/tmp/v.md")

        # No fixed-width layout: nothing may depend on a run of spaces or on
        # a leading indent, because neither survives.
        for line in precis.splitlines():
            self.assertFalse(
                re.search(r"\S {2,}\S", line),
                f"column alignment will collapse in a renderer: {line!r}")
            self.assertFalse(
                line.startswith("  "),
                f"leading indent will be stripped or become a code block: "
                f"{line!r}")

        # And the structure is markup a renderer understands, so it survives.
        collapsed = "\n".join(re.sub(r" {2,}", " ", ln.strip())
                              for ln in precis.splitlines())
        self.assertEqual(collapsed, precis,
                         "the précis must be unchanged by the collapse")
        self.assertIn("## Changes requested", precis)
        self.assertIn("- **Findings** —", precis)
        self.assertIn("**F1**", precis)

    def test_the_relay_fences_its_commands(self):
        # The other half: a command must arrive copyable. Indentation was
        # doing that job and indentation does not survive the trip.
        v = wire.parse_verdict(
            '<loupe-review-verdict sha="abc">\n'
            "VERDICT: clean to advance\n\n## findings\n\nNone\n"
            "\n## evidence checked\n\n- the diff\n")
        relay = brief.verdict_relay(v, source="/tmp/v.md")
        self.assertIn("```bash", relay)
        fenced = relay.split("```bash")[1].split("```")[0].strip().splitlines()
        live = [l for l in fenced if not l.startswith("#")]
        self.assertEqual(live, [f"{TOOL_NAME} close --verdict /tmp/v.md"],
                         "the fence holds one live command and nothing else")

    def test_brief_and_relay_never_contain_each_other(self):
        """The user-reported gap, three sessions running.

        The request side has always emitted two fields — an account to read
        and commands to copy. The verdict side emitted one blob holding both,
        so a reviewer relaying it had no structural signal about which half
        was which and invented a split of its own each round: fencing the
        prose as if it were pasteable, burying the command inside the
        summary. The complaint looked like a reviewer being careless; the
        cause was one field doing two jobs.

        Asserted in both directions, because either one alone permits the
        merge to come back from the other side.
        """
        v = wire.parse_verdict(
            '<loupe-review-verdict sha="abc">\n'
            "VERDICT: changes requested\n\n## findings\n\n"
            "### F1\nSeverity: High\nClassification: design_gap\n"
            "Title: something is wrong\nEvidence: x.py:1\nWhy: because\n"
            "Required outcome: fix it\nFALSIFICATION: command: `run it`\n"
            "\n## evidence checked\n\n- the diff\n")
        precis = brief.verdict_precis(v, source="/tmp/v.md")
        relay = brief.verdict_relay(v, source="/tmp/v.md")

        # No command in the account.
        for verb in ("close --verdict", "respond --verdict"):
            self.assertNotIn(verb, precis, "a command leaked into the brief")
        # No account in the commands: the relay names findings nowhere.
        self.assertNotIn("something is wrong", relay)
        self.assertNotIn("F1", relay)
        # And each does its own job.
        self.assertIn("something is wrong", precis)
        self.assertIn(f"{TOOL_NAME} close --verdict /tmp/v.md", relay)

    def test_the_relay_carries_the_whole_envelope_when_asked(self):
        envelope = "<loupe-review-request sha=\"a\">\nbody\n</loupe-review-request>"
        out = brief.relay("/kept/path.md", None, envelope, paste=True)
        self.assertIn(envelope, out)

    def test_the_default_topology_relays_one_line_and_nothing_else(self):
        """RVW-T11. A same-machine round prints the carrier that applies and
        no account of the one that does not.

        This is what the declaration bought. The relay used to carry, on
        every round, a commented alternative for a reviewer elsewhere and a
        hint about the flag that prints the bytes — true, and noise in the
        only topology the loop had ever run. The round now says which it is,
        so this shape is the whole block: heading, fence, one command.
        """
        envelope = "x" * 500
        out = brief.relay("/kept/path.md", self._request(), envelope,
                          paste=False)
        self.assertIn(f"{TOOL_NAME} take /kept/path.md --as codex", out)
        self.assertNotIn(envelope, out)
        self.assertNotIn("500 bytes", out)
        # No second carrier, and no comment about one: inside the fence
        # there is one line, and it is the command.
        self.assertNotIn("take -", out)
        body = [ln for ln in out.splitlines() if ln.strip()]
        self.assertEqual(len(body), 4, out)   # heading, ```bash, command, ```
        self.assertEqual([ln for ln in body[2:-1] if ln.startswith("#")], [])

    def test_paste_still_appends_the_bytes_when_asked_for_them(self):
        """`brief --paste` is unchanged by the topology: a person may always
        ask for the bytes, whatever the round declared."""
        envelope = "x" * 500
        out = brief.relay("/kept/path.md", self._request(), envelope,
                          paste=True)
        self.assertIn(envelope, out)

    def test_the_relay_command_carries_the_mandatory_identity_flag(self):
        # Round 1 F4 made --as mandatory. A relay that prints a line the tool
        # refuses hands the human a failure to debug on someone else's behalf.
        parsed = self._request()
        out = brief.relay("/kept/path.md", parsed, "envelope", paste=False)
        self.assertIn("--as codex", out)
        self.assertIn("take /kept/path.md --as codex", out)

    def test_a_declared_paste_round_relays_the_bytes_carrier(self):
        """RVW-T11, the other half. When the round declares that the two ends
        share no filesystem, the kept path is not a carrier at all — so it is
        not printed, live or commented, and the bytes travel with the command
        that consumes them rather than behind a flag."""
        envelope = "x" * 500
        out = brief.relay("/kept/path.md", self._request(transport_attr="paste"),
                          envelope, paste=False)
        self.assertIn(f"{TOOL_NAME} take - --as codex", out)
        self.assertNotIn("/kept/path.md", out)
        # The bytes come along uninvited: there is no second command for the
        # human to run to obtain what they were just told to carry.
        self.assertIn(envelope, out)

    def test_the_paste_carrier_is_declared_never_inferred(self):
        """The same kept path, the same call, the same everything except the
        declaration — and that alone decides the carrier.

        The rejected design was to guess: read `take -`, an environment
        variable, or a path that does not resolve, and conclude the sides are
        apart. Every such signal fails open toward `path`, which is the one
        answer that prints a pointer the far end cannot follow.
        """
        args = ("/kept/path.md", None, "e")
        local = brief.relay(*args, transport="path")
        remote = brief.relay(*args, transport="paste")
        self.assertIn("take /kept/path.md", local)
        self.assertNotIn("take -", local)
        self.assertIn("take -", remote)
        self.assertNotIn("take /kept/path.md", remote)

    def test_unkept_bytes_fall_to_paste_under_either_declaration(self):
        """Not the topology's doing: with nothing kept there is no path to
        offer, so both declarations reach the same carrier."""
        for declared in ("path", "paste"):
            out = brief.relay("not kept (…)", self._request(), "e",
                              transport=declared)
            self.assertIn(f"{TOOL_NAME} take - --as codex", out, declared)

    def test_an_unstamped_envelope_asks_for_the_identity(self):
        out = brief.relay("/kept/path.md", None, "e", paste=False)
        self.assertIn("--as <your id>", out)


class TestBrief(unittest.TestCase):
    """Sweep F7 (High): the paste relay is a byte carrier, or it is nothing.

    `brief --paste` wrapped the whole envelope in a fixed three-backtick
    fence. A real request carries its own ```<tag>-attestations block, whose
    closing line is three backticks — so the outer block closed inside
    Evidence, and every line after it (the ledger report, the contract, the
    references, the wrapper's own closing tag) travelled as prose whose
    whitespace and markup no renderer preserves.
    """

    @staticmethod
    def _fenced_block(rendered: str, after: str):
        """(fence, body) of the first fenced block following the marker
        line `after`, closed the way CommonMark closes it: by the first
        subsequent line that is a backtick run at least as long as the
        opener (and nothing else)."""
        lines = rendered.splitlines()
        start = lines.index(after)
        i = start + 1
        while not lines[i].startswith("`"):
            i += 1
        opener = lines[i]
        n = len(opener) - len(opener.lstrip("`"))
        body = []
        for line in lines[i + 1:]:
            stripped = line.strip()
            if stripped and set(stripped) == {"`"} and len(stripped) >= n:
                return opener, "\n".join(body)
            body.append(line)
        raise AssertionError("the fenced block never closes")

    def test_paste_fence_contains_an_attested_request(self):
        """FALSIFICATION for F7. Mutation: make `_fence` return a fixed
        three-backtick delimiter again — the attestation fence inside the
        envelope closes the outer block and the body below stops 100+ lines
        short of the wrapper; the equality fails."""
        from review.tests import synth
        envelope = synth.emitted_request()
        fence_line = f"```{synth.NO_GATES.wrapper_tag}-attestations"
        self.assertIn(fence_line, envelope,
                      "the fixture must carry a real internal fence")
        self.assertIn("\n```\n", envelope)
        rendered = brief.relay("/kept/r.md", wire.parse_request(envelope),
                               envelope, paste=True)
        opener, body = self._fenced_block(rendered, "The envelope, to paste:")
        self.assertGreater(len(opener.rstrip("`")) or 4, 3)  # ≥ 4 backticks
        self.assertEqual(body, envelope.rstrip("\n"),
                         "the pasted block must carry the whole envelope, "
                         "byte for byte, wrapper to wrapper")
        self.assertTrue(body.rstrip().endswith("</loupe-review-request>"))
        # And it round-trips: what a reviewer pastes into `take -` parses
        # as the same request.
        self.assertEqual(wire.parse_request(body).sha,
                         wire.parse_request(envelope).sha)

    def test_the_fence_outruns_any_run_inside(self):
        # A run of four inside gets five outside; a run of seven, eight.
        for run in (3, 4, 7):
            block = brief._fence("x", "`" * run + " inside", "y", lang="")
            self.assertEqual(block[0], "`" * (run + 1))
            self.assertEqual(block[-1], "`" * (run + 1))
        # No backticks inside: the ordinary three, tagged bash. A bash
        # fence takes rendered Commands only (round 5 F1) — a bare string
        # is refused rather than trusted.
        from review import paths
        rendered = paths.command(paths.Lit("loupe"), paths.Lit("take"), "x",
                                 paths.Lit("--as"), "codex")
        self.assertEqual(brief._fence(rendered),
                         ["```bash", "loupe take x --as codex", "```"])
        with self.assertRaises(TypeError):
            brief._fence("loupe take x --as codex")

    def test_the_first_bullet_of_a_section_is_counted(self):
        """Author-found during the sweep (not a reviewer finding, declared
        as such): `_split_sections` strips each section, so a section that
        opens with a bullet loses that bullet's indent, and the brief's
        bullet pattern required the indent — the stop-condition count read
        one short on every envelope that ever carried the section (round 5:
        6 of 7; the sweep request: 7 of 8). Mutation: restore the
        indent-only `_BULLETS` and this fails on the count."""
        from review.tests import synth
        stops = ["one", "two", "three"]
        envelope = synth.emitted_request(claim={**synth.CLAIM,
                                                "stop_conditions": stops})
        parsed = wire.parse_request(envelope)
        section = parsed.sections["stop conditions"]
        # The parsed section really has lost the first indent — this is the
        # state the counter has to read correctly.
        self.assertTrue(section.startswith("- one"), repr(section[:20]))
        self.assertEqual(len(brief._BULLETS.findall(section)), 3)
        precis = brief.request_precis(parsed, None)
        self.assertIn("3 stated stop condition(s)", precis)
        # And a bulleted list that sits mid-section, keeping its indent,
        # still counts exactly: two declared, two counted.
        envelope = synth.emitted_request(claim={
            **synth.CLAIM, "stop_conditions": stops,
            "deliberately_not": ["a", "b"]})
        precis = brief.request_precis(wire.parse_request(envelope), None)
        self.assertIn("2 thing(s) the author says are deliberately not done",
                      precis)
        self.assertIn("3 stated stop condition(s)", precis)

    def test_simple_command_fence_control(self):
        # The command relay is unchanged: one runnable line in a bash fence
        # that a chat renders as copyable.
        from review.tests.test_transport import request_text
        req = wire.parse_request(request_text())
        out = brief.relay("/kept/r.md", req, "irrelevant", paste=False)
        self.assertIn(f"```bash\n{TOOL_NAME} take /kept/r.md", out)

    def test_an_unstamped_identity_leaves_no_live_line_to_break_on(self):
        """Workshop (c), found by the shell check rather than by reading.

        With no reviewer stamped, the `take` line renders `--as <your id>` —
        and `<your id>` is a REDIRECTION, so the one live line in a block
        headed "every live line below runs as printed" was a syntax error
        that killed the paste. It predates the workshop; nothing tested the
        promise, so nothing noticed. The fence's placeholder rule makes the
        line a comment: the reader still gets the command to fill in, and
        what remains live still parses.
        """
        out = brief.relay("/kept/r.md", None, "irrelevant", paste=False)
        body = out.splitlines()[3:-1]
        live = [l for l in body if not l.startswith("#")]
        self.assertEqual(live, [], f"a line that cannot run is live: {live}")
        self.assertIn("<your id>", out)


class TestHandoffAlwaysCarriesTheBrief(unittest.TestCase):
    """The guarantee: the human account is on BOTH channels, every time.

    An agent reads JSON, a person reads the TTY text. If the précis existed
    only on one of them it would be a courtesy on the other.
    """

    def setUp(self):
        try:
            self.tmp = Path(tempfile.mkdtemp(prefix="brief-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.repo = self.tmp / "repo"
        _sh("git", "init", "-q", "-b", "main", str(self.repo))
        for k, v in (("user.name", "a"), ("user.email", "a@example.invalid"),
                     ("commit.gpgsign", "false")):
            _sh("git", "-C", str(self.repo), "config", k, v)
        from review.tests.util import REPO_ROOT
        toml = (REPO_ROOT / "review.toml").read_text(encoding="utf-8")
        toml = toml[:toml.index("[[gates]]")] + toml[toml.index("[roles]"):]
        (self.repo / "review.toml").write_text(toml, encoding="utf-8")
        (self.repo / "f.txt").write_text("one\n", encoding="utf-8")
        _sh("git", "-C", str(self.repo), "add", ".")
        _sh("git", "-C", str(self.repo), "commit", "-q", "-m", "init")
        self.base = _git(self.repo, "rev-parse", "HEAD")
        (self.repo / "f.txt").write_text("two\n", encoding="utf-8")
        _sh("git", "-C", str(self.repo), "commit", "-qam", "change")
        self.state = self.tmp / "state"
        self.claim = self.tmp / "claim.json"
        self.claim.write_text(json.dumps({
            "objective": "brief test",
            "references": [{"path": "review.toml", "required": True}]}),
            encoding="utf-8")
        self.cwd = os.getcwd()
        self.addCleanup(os.chdir, self.cwd)

    def _run(self, *argv):
        import contextlib
        from io import StringIO
        os.chdir(self.repo)
        buf = StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["--ledger-dir", str(self.state), *argv])
        os.chdir(self.cwd)
        out = buf.getvalue()
        try:
            return code, json.loads(out)
        except json.JSONDecodeError:
            return code, out

    def test_handoff_output_carries_brief_and_relay(self):
        code, rec = self._run("handoff", "--claim-file", str(self.claim),
                              "--base", self.base, "--local-only")
        self.assertEqual(code, 0, rec)
        self.assertIn("What this asks", rec["brief"])
        self.assertIn("How to carry it", rec["relay"])
        # The relay must name the literal command, not describe it.
        self.assertIn(f"take {rec['kept']}", rec["relay"])

    def test_invalid_verdict_validation_has_no_actionable_relay(self):
        """Round 4 F2 (Medium), falsification.

        The relay is derived from the verdict LINE, before anything checks
        whether the verdict is coherent. So a wrapped envelope claiming
        `clean to advance` while carrying a blocking finding produced one
        result that told the agent `blocked, next=null, no runnable recovery`
        and told the human "Nothing is blocking — run `loupe close`", about a
        verdict that is neither clean nor valid. `close` rejects it, so
        nothing was bypassed; but printing a contradiction at the
        malformed-envelope boundary is precisely where the typed-recovery
        contract exists to stop an agent improvising.
        """
        invalid = self.tmp / "invalid-verdict.md"
        invalid.write_text(
            '<loupe-review-verdict sha="%s">\n'
            "VERDICT: clean to advance\n\n## findings\n\n"
            "### F1\nSeverity: High\nClassification: design_gap\n"
            "Title: a clean verdict carrying a blocking finding\n"
            "Evidence: f.txt:1\nWhy: the two cannot both be true\n"
            "Required outcome: pick one\n"
            "FALSIFICATION: command: `run it`\n"
            "\n## evidence checked\n\n- the diff\n"
            "</loupe-review-verdict>\n" % _git(self.repo, "rev-parse", "HEAD"),
            encoding="utf-8")
        code, rec = self._run("validate", str(invalid))

        self.assertEqual(code, 1, rec)
        self.assertEqual(rec["next_kind"], "blocked", rec)
        self.assertIsNone(rec["next"], rec)
        self.assertNotIn("relay", rec,
                         "a result with no runnable recovery must not also "
                         "hand the human a command to run")
        # The account still goes out: describing a broken envelope is how its
        # author learns what is broken. Only the instructions are withheld.
        self.assertIn("Clean to advance", rec["brief"])

    def test_a_valid_verdict_still_gets_its_relay(self):
        # The control for the test above. Withholding the relay from every
        # verdict would satisfy F2 by deleting the feature.
        valid = self.tmp / "valid-verdict.md"
        valid.write_text(
            '<loupe-review-verdict sha="%s">\n'
            "VERDICT: clean to advance\n\n## findings\n\nNone\n"
            "\n## evidence checked\n\n- the diff\n"
            "</loupe-review-verdict>\n" % _git(self.repo, "rev-parse", "HEAD"),
            encoding="utf-8")
        code, rec = self._run("validate", str(valid))
        self.assertEqual(code, 0, rec)
        self.assertIn("What to run next", rec["relay"])
        self.assertIn(f"{TOOL_NAME} close --verdict", rec["relay"])

    def test_a_local_only_target_is_reported_as_unreachable(self):
        _, rec = self._run("handoff", "--claim-file", str(self.claim),
                           "--base", self.base, "--local-only")
        self.assertIn("this machine only", rec["brief"])

    def test_brief_with_no_argument_finds_what_handoff_emitted(self):
        _, emitted = self._run("handoff", "--claim-file", str(self.claim),
                               "--base", self.base, "--local-only")
        code, found = self._run("brief")
        self.assertEqual(code, 0, found)
        self.assertEqual(found["sha"], emitted["sha"])
        self.assertEqual(found["superseded"], 0)

    def test_brief_refuses_as_blocked_when_nothing_is_open(self):
        """RVW-T16 (2026-08-19): this used to assert a `next` of `handoff
        --claim-file <claim.json> --base <sha>` — an argv no agent can run,
        in the one field the adapters tell every agent to run verbatim. The
        claim is the author's judgment, which this tool carries and never
        invents, so there IS no command here: the recovery is a person
        writing one. That is what `blocked` is for."""
        code, rec = self._run("brief")
        self.assertEqual(code, 1)
        self.assertEqual(rec["next_kind"], "blocked")
        self.assertIsNone(rec["next"])
        self.assertIn("handoff", rec["remedy"])
        self.assertIn("claim", rec["remedy"])

    def test_brief_paste_prints_the_bytes_to_paste(self):
        self._run("handoff", "--claim-file", str(self.claim),
                  "--base", self.base, "--local-only")
        _, rec = self._run("brief", "--paste")
        self.assertIn("review-request", rec["relay"])
        # Round 5: `---8<---` scissors were an indentation-era marker. A
        # fence is what survives being relayed into a chat, and it is the
        # only wrapper that means "copy this verbatim" in both destinations.
        self.assertIn("```", rec["relay"])

    def test_brief_refuses_something_that_is_neither_envelope(self):
        junk = self.tmp / "junk.md"
        junk.write_text("not an envelope\n", encoding="utf-8")
        code, rec = self._run("brief", str(junk))
        self.assertEqual(code, 1)
        self.assertIn("next", rec)


class TestVerdictCarrier(unittest.TestCase):
    """Round 4 F2, the half the tool owns: the verdict leg's transport.

    The request leg has always been a choice — a kept path on this machine,
    or the bytes for `take -`. The verdict leg printed the REVIEWER's path
    in both author commands and offered nothing else, and `respond` could
    not read a paste even when the author had one: `close -` worked,
    `respond -` looked for a file named `-`. An author on another machine
    could carry the verdict in and then not answer it.

    What remains of F2 — a carrier declared at the start of the round, a
    forge-comment transport, a cloud author fetching bytes with no
    reviewer-local path — is escalated, not closed: it is the user's
    decision (see the disposition for round 4).
    """

    def _verdict_text(self):
        from review.tests.synth import TAG
        return (f'<{TAG}-review-verdict sha="{"a" * 40}">\n'
                f"VERDICT: changes requested\n\n## findings\n\n### F1\n"
                f"Severity: High\nClassification: correctness\n"
                f"Title: t\nEvidence: e\nWhy: w\nRequired outcome: r\n"
                f"FALSIFICATION: f\nPreventable-by: tests\n"
                f"</{TAG}-review-verdict>\n")

    def test_the_carrier_travels_with_the_commands_it_qualifies(self):
        """Round 4 F2's substance, relocated by workshop (c).

        The finding was that the verdict leg named the REVIEWER's own path
        and offered the author nothing else. The answer was a `Carrier`
        bullet in the brief — correct in content, one artifact away from the
        commands it was about, and therefore absent from the fence a person
        is told to hand over on its own. It is now a comment inside that
        fence, which is where it is read.
        """
        parsed = wire.parse_verdict(self._verdict_text())
        relay = brief.verdict_relay(parsed, source="/tmp/verdict.md",
                                    transport="paste")
        # The reviewer's own path is not named at all: under this declaration
        # it is not a thing the author can open, so offering it — live or as
        # a fallback — is offering a failure.
        self.assertIn(f"{TOOL_NAME} close --verdict -", relay)
        self.assertNotIn("/tmp/verdict.md", relay)
        # Every line of it is still either a command or an inert comment.
        for line in relay.splitlines()[3:-1]:
            self.assertTrue(line.startswith(TOOL_NAME) or line.startswith("#"))

    def test_the_same_machine_verdict_leg_states_one_fact(self):
        """RVW-T11. F2's caveat was protection in a topology the loop had
        never run and noise in the one it always ran. Declared same-machine,
        the line names the path — which is openable — and says nothing about
        a carrier, because there is no choice to describe.

        The AUDIENCE line survives in both shapes, and deliberately: it
        answers a 2026-08-20 report of a person running `loupe close` in the
        reviewer's own terminal, and sharing one machine is exactly the
        condition that makes that mistake easy.
        """
        parsed = wire.parse_verdict(self._verdict_text())
        relay = brief.verdict_relay(parsed, source="/tmp/verdict.md")
        self.assertIn(f"{TOOL_NAME} close --verdict /tmp/verdict.md", relay)
        self.assertNotIn("--verdict -", relay)
        self.assertIn("the author's to run", relay)
        self.assertNotIn("shared filesystem", relay)

    def test_the_relay_does_not_guess_the_step_close_computes(self):
        """`respond` is `close`'s to emit, not the relay's to predict.

        It rode here as a comment for one commit. `close_round` DERIVES the
        next command as it records the round — `respond --verdict <the
        recorded verdict>` on changes requested, nothing on clean — so the
        relay's copy was a second source for one fact, and the one that
        could be wrong: it named the reviewer's path where `close` names the
        path the tool actually kept.
        """
        parsed = wire.parse_verdict(self._verdict_text())
        relay = brief.verdict_relay(parsed, source="/tmp/verdict.md")
        self.assertNotIn("respond", relay)
        self.assertNotIn("dispositions.json", relay)

    def test_respond_reads_the_verdict_from_stdin_like_close(self):
        import argparse
        import contextlib
        import io
        import sys
        from review.tests.synth import CFG
        try:
            tmp = Path(tempfile.mkdtemp(prefix="carrier-"))
        except OSError as exc:
            self.skipTest(f"filesystem writes denied ({exc})")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        dispositions = tmp / "d.json"
        dispositions.write_text(json.dumps({"head": "b" * 40,
                                            "dispositions": []}),
                                encoding="utf-8")
        args = argparse.Namespace(verdict="-", from_json=str(dispositions),
                                  out=None, ledger_dir=str(tmp),
                                  command="respond")
        buf = io.StringIO()
        stdin = sys.stdin
        sys.stdin = io.StringIO(self._verdict_text())
        try:
            with contextlib.redirect_stdout(buf):
                code = cli.cmd_respond(args, CFG)
        finally:
            sys.stdin = stdin
        # The verdict was READ — the run reaches validation and reports on
        # the dispositions, rather than dying on a file named `-`.
        self.assertNotIn("No such file", buf.getvalue())
        self.assertIn("F1", buf.getvalue())
        self.assertNotEqual(code, 2)

    def test_one_stdin_cannot_carry_both_documents(self):
        import argparse
        import contextlib
        import io
        from review.tests.synth import CFG
        args = argparse.Namespace(verdict="-", from_json="-", out=None,
                                  ledger_dir=None, command="respond")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.cmd_respond(args, CFG)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["next_kind"], "blocked")
        self.assertIn("one stdin cannot carry two documents",
                      payload["error"])
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()

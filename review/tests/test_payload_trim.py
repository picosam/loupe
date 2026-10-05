"""Brief `loupe-payload-and-launch-trim` (2026-10-04): the rows of the loupe
release batch the determinism and efficiency pass kept out of its landing,
each held through `bin/loupe` as its caller runs it.

Every scratch repository is `_real_cli.Scratch`: a temporary directory, a
bare remote, its own `user.name`/`user.email`, its own state directory and a
HOME redirected into the scratch, so nothing of the operator's machine is
read or written.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from review.ledger import Ledger
from review.tests._real_cli import Scratch, loupe


def _write_rows(state: Path, rows: list[dict]) -> bytes:
    """A hand-built ledger, as an import or a hand edit leaves one; returns
    its bytes, for the byte-identity assertions."""
    state.mkdir(parents=True, exist_ok=True)
    data = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
    (state / "ledger.jsonl").write_text(data, encoding="utf-8")
    return data.encode("utf-8")


class TestLineageOfFindsTheEventItWasGiven(unittest.TestCase):
    """`Ledger.lineage_of` matched `e.get("uid") == event.get("uid")`, so for
    events with no uid `None == None` matched the FIRST uid-less event and
    returned its key. The entry point that prints it is `waive --sha`, whose
    refusal names the lineage that reviewed the commit.

    One row per class of the event being located:
      * uid-less, not the first event        -> its own key (the defect)
      * uid-less, the first event            -> its own key (unchanged)
      * carrying a uid, as loupe writes      -> its own key (unchanged)
      * a commit no event names              -> the waiver records (control)

    The expected key is read from `lineage_keys()`, the one authority for a
    legacy key, never restated.
    """

    def setUp(self):
        self.s = Scratch(self, "lineage-of-")

    def _refusal(self):
        before = (self.s.state / "ledger.jsonl").read_bytes()
        code, payload, out, err = loupe(
            self.s, "waive", "--sha", self.s.head, "--reason", "r",
            "--by", "a person")
        self.assertNotEqual(code, 0, out + err)
        self.assertEqual((self.s.state / "ledger.jsonl").read_bytes(), before,
                         "a refused waiver changed the ledger")
        return payload["error"]

    def _expected(self, index: int) -> str:
        return Ledger(self.s.state).lineage_keys()[index]

    def test_a_uidless_request_after_a_closure_names_its_own_lineage(self):
        _write_rows(self.s.state, [
            {"event": "lineage_closed", "at_round": 1},
            {"event": "request", "sha": self.s.head, "round": 1}])
        key = self._expected(1)
        self.assertNotEqual(key, self._expected(0))
        self.assertIn(f"lineage {key})", self._refusal())

    def test_a_uidless_request_first_names_its_own_lineage(self):
        _write_rows(self.s.state, [
            {"event": "request", "sha": self.s.head, "round": 1},
            {"event": "lineage_closed", "at_round": 1}])
        self.assertIn(f"lineage {self._expected(0)})", self._refusal())

    def test_events_written_by_loupe_name_their_own_lineage(self):
        ledger = Ledger(self.s.state)
        ledger.add({"event": "lineage_closed", "at_round": 1})
        ledger.add({"event": "request", "sha": self.s.head, "round": 1})
        self.assertIn(f"lineage {self._expected(1)})", self._refusal())

    def test_control_an_unreviewed_commit_is_waived(self):
        _write_rows(self.s.state, [
            {"event": "lineage_closed", "at_round": 1},
            {"event": "request", "sha": self.s.head, "round": 1}])
        code, payload, out, err = loupe(
            self.s, "waive", "--sha", self.s.base, "--reason", "r",
            "--by", "a person")
        self.assertEqual(code, 0, out + err)
        self.assertTrue(payload["recorded"])

    def test_a_uidless_copy_resolves_by_its_content(self):
        """A caller holding a COPY of a uid-less row (not the ledger's own
        object) is answered by content, not by the fallback to the newest
        key."""
        _write_rows(self.s.state, [
            {"event": "request", "sha": self.s.head, "round": 1},
            {"event": "lineage_closed", "at_round": 1},
            {"event": "request", "sha": self.s.base, "round": 1}])
        ledger = Ledger(self.s.state)
        keys = ledger.lineage_keys()
        self.assertNotEqual(keys[0], keys[-1])
        self.assertEqual(ledger.lineage_of(dict(ledger.events()[0])),
                         keys[0])

    def test_lineages_for_sha_agrees_with_lineage_keys(self):
        """The resolver every envelope-bearing verb uses reads the same
        key: `lineages_for_sha` built on the old match said `["1"]`."""
        _write_rows(self.s.state, [
            {"event": "lineage_closed", "at_round": 1},
            {"event": "request", "sha": self.s.head, "round": 1}])
        ledger = Ledger(self.s.state)
        self.assertEqual(ledger.lineages_for_sha(self.s.head),
                         [ledger.lineage_keys()[1]])


_A = "a" * 40
_B = "b" * 40


def _request(sha=_A, round_no=1, **extra) -> dict:
    return {"event": "request", "sha": sha, "round": round_no, **extra}


_CLOSED = {"event": "lineage_closed", "at_round": 1}


def _old_lineages_for_sha(self, sha):
    """`lineages_for_sha` as it read before round-2 F4: each event
    re-resolved through `lineage_of`. Kept for the mutation rows only."""
    if not sha:
        return []
    seen = []
    for e in reversed(self.events()):
        if e.get("sha") == sha and e.get("event") in ("request", "take"):
            key = self.lineage_of(e)
            if key not in seen:
                seen.append(key)
    return seen


class TestEqualRowsKeepTheirOwnLineage(unittest.TestCase):
    """Round-2 F4 of the final review (2026-10-05): `lineage_of` tested
    identity and equal content together from the OLDEST event, so a
    uid-less row equal to an earlier one in another lineage was named that
    lineage, `lineages_for_sha` lost a key, and `require_unshared_target`
    let the shared head through.

    Every ledger is a JSONL file read by `Ledger`'s own reader, and every
    expected key is read from `lineage_keys()`, the one authority.

    THE PARTITION (class: ledger · expected):
      E1 equal uid-less rows across one closure marker
         [req A, closed, req A]              · each its own key; SHA -> 2, 1
      E2 the same across two markers
         [req A, closed, req A, closed, req A] · own keys; SHA -> 3, 2, 1
      E3 duplicates inside one lineage, one more after the marker
         [req A, req A, closed, req A]       · own keys; SHA -> 2, 1
      E4 duplicates inside one lineage only  · own keys; SHA -> 1
      E5 differing content (round 1, round 2) across a marker · SHA -> 2, 1
      E6 distinct uids                       · own keys; SHA -> 2, 1
      E7 ONE uid repeated in two lineages (a hand copy) · own keys; SHA -> 2, 1
      E8 declared lineages, otherwise equal  · own keys; SHA -> Lb, La
      E9 a legacy prefix, a keyed event, then a hand row equal to the first
         · own keys; SHA(A) -> 2, 1
    Copies (an object the ledger does not hold):
      K1 equal rows in one lineage (E4)      -> that key
      K2 equal rows in two lineages (E1)     -> AmbiguousEvent naming both
      K3 a unique uid (E6)                   -> its key
      K4 a uid in two lineages (E7)          -> AmbiguousEvent naming both
      K5 a declared lineage                  -> the declared id
      K6 matching no row                     -> the newest key; "1" on an
                                                empty ledger (unchanged)
    The guard: `require_unshared_target` on the same files refuses every
    cross-lineage ledger from each of its lineages, and passes E4 (one
    lineage) and a head no row names. `recorded_lineage_for_sha` with
    `prefer` names E1's second lineage (it answered None).
    """

    LEDGERS = {
        "E1": ([_request(), _CLOSED, _request()], [_A]),
        "E2": ([_request(), _CLOSED, _request(), _CLOSED, _request()], [_A]),
        "E3": ([_request(), _request(), _CLOSED, _request()], [_A]),
        "E4": ([_request(), _request()], [_A]),
        "E5": ([_request(), _CLOSED, _request(round_no=2)], [_A]),
        "E6": ([_request(uid="x"), {**_CLOSED, "uid": "z"},
                _request(uid="y")], [_A]),
        "E7": ([_request(uid="u"), _CLOSED, _request(uid="u")], [_A]),
        "E8": ([_request(lineage="La00000000a"),
                _request(lineage="Lb00000000b")], [_A]),
        "E9": ([_request(), _CLOSED, _request(_B, lineage="Lk00000000k"),
                _request()], [_A, _B]),
    }
    CROSS = ("E1", "E2", "E3", "E5", "E6", "E7", "E8", "E9")

    def setUp(self):
        import shutil
        import tempfile
        self.dir = Path(tempfile.mkdtemp(prefix="lineage-equal-"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _ledger(self, name: str) -> Ledger:
        rows, _shas = self.LEDGERS[name]
        _write_rows(self.dir, rows)
        return Ledger(self.dir)

    def _sha_keys(self, ledger: Ledger, sha: str) -> list[str]:
        """Every key the rows binding `sha` sit in, newest first, read
        from `lineage_keys()` — the oracle for `lineages_for_sha`."""
        out: list[str] = []
        pairs = list(zip(ledger.events(), ledger.lineage_keys()))
        for e, key in reversed(pairs):
            if e.get("sha") == sha and e.get("event") in ("request", "take") \
                    and key not in out:
                out.append(key)
        return out

    # One check per property, raising; each test runs it once per row.

    EXPECTED_SHA = {"E1": ["2", "1"], "E2": ["3", "2", "1"],
                    "E3": ["2", "1"], "E4": ["1"], "E5": ["2", "1"],
                    "E6": ["2", "1"], "E7": ["2", "1"],
                    "E8": ["Lb00000000b", "La00000000a"], "E9": ["2", "1"]}

    def _check_own(self, name):
        ledger = self._ledger(name)
        self.assertEqual([ledger.lineage_of(e) for e in ledger.events()],
                         ledger.lineage_keys())

    def _check_sha(self, name):
        ledger = self._ledger(name)
        self.assertEqual(ledger.lineages_for_sha(_A), self.EXPECTED_SHA[name])
        for sha in self.LEDGERS[name][1]:
            self.assertEqual(ledger.lineages_for_sha(sha),
                             self._sha_keys(ledger, sha))

    def _check_guard(self, name):
        from review.cli import SharedTarget, require_unshared_target
        self._ledger(name)
        lineages = self._sha_keys(Ledger(self.dir), _A)
        self.assertGreater(len(lineages), 1)
        before = (self.dir / "ledger.jsonl").read_bytes()
        for lineage in lineages:
            with self.assertRaises(SharedTarget, msg=lineage):
                require_unshared_target(Ledger(self.dir), lineage,
                                        {"sha": _A}, False)
        self.assertEqual((self.dir / "ledger.jsonl").read_bytes(), before)

    def test_every_returned_object_names_its_own_lineage(self):
        for name in self.LEDGERS:
            with self.subTest(ledger=name):
                self._check_own(name)

    def test_lineages_for_sha_keeps_every_applicable_key(self):
        for name in self.LEDGERS:
            with self.subTest(ledger=name):
                self._check_sha(name)

    def test_the_shared_target_guard_refuses_each_cross_lineage_ledger(self):
        """From EVERY lineage that binds the head, never only the newest."""
        for name in self.CROSS:
            with self.subTest(ledger=name):
                self._check_guard(name)

    def test_control_one_lineage_and_an_unnamed_head_pass(self):
        from review.cli import require_unshared_target
        for name, sha in (("E4", _A), ("E1", _B)):
            with self.subTest(ledger=name, sha=sha[:1]):
                self.assertIsNone(require_unshared_target(
                    self._ledger(name), "1", {"sha": sha}, False))

    def test_the_resolver_finds_the_preferred_lineages_round(self):
        ledger = self._ledger("E1")
        self.assertEqual(ledger.recorded_lineage_for_sha(_A, prefer="2"), "2")
        self.assertEqual(ledger.recorded_lineage_for_sha(_A, prefer="1"), "1")

    def test_a_copy_resolves_only_when_its_matches_share_a_lineage(self):
        from review.ledger import AmbiguousEvent
        cases = {
            "K1 equal rows in one lineage": ("E4", 0, "1"),
            "K2 equal rows in two lineages": ("E1", 0, ["1", "2"]),
            "K3 a unique uid": ("E6", 2, "2"),
            "K4 a uid in two lineages": ("E7", 2, ["1", "2"]),
            "K5 a declared lineage": ("E8", 1, "Lb00000000b"),
        }
        for case, (name, index, expected) in cases.items():
            with self.subTest(case=case):
                ledger = self._ledger(name)
                copy = dict(ledger.events()[index])
                self.assertIsNot(copy, ledger.events()[index])
                if isinstance(expected, list):
                    with self.assertRaises(AmbiguousEvent) as raised:
                        ledger.lineage_of(copy)
                    self.assertEqual(raised.exception.candidates, expected)
                else:
                    self.assertEqual(ledger.lineage_of(copy), expected)

    def test_a_copy_matching_no_row_answers_the_newest_key(self):
        with self.subTest(case="K6 a populated ledger"):
            self.assertEqual(self._ledger("E2").lineage_of(_request(_B)), "3")
        with self.subTest(case="K6 an empty ledger"):
            self.assertEqual(Ledger.in_memory().lineage_of(_request()), "1")

    # ------------------------------------------------------------ mutations
    #
    # Each row states whether the mutation must turn it red, so a mutation
    # that reaches a row it should not is caught as surely as one that
    # misses a row it should reach.

    def _under(self, check, red: set[str], names):
        for name in names:
            with self.subTest(ledger=name, red=name in red):
                if name in red:
                    with self.assertRaises((AssertionError, LookupError)):
                        check(name)
                else:
                    check(name)

    def test_the_identity_guard_mutated_lets_each_cross_lineage_row_through(
            self):
        """Identity-first resolution disabled: every ledger whose equal rows
        (by content or by uid) sit in two lineages misnames or raises."""
        from unittest import mock
        with mock.patch.object(Ledger, "_position_of",
                               lambda self, event: None):
            self._under(self._check_own, {"E1", "E2", "E3", "E7", "E9"},
                        self.LEDGERS)

    def test_the_positional_sha_guard_mutated_with_identity_loses_a_key(self):
        """`lineages_for_sha` reverted to re-resolving through `lineage_of`,
        with `lineage_of` the oldest-equal-row match (the pre-fix pair): the
        SHA query loses a key on every equal-row ledger, and the guard lets
        the older lineage through. Reverted ALONE it is an equivalent
        mutant: with identity first, `lineage_of` on the ledger's own object
        IS its positional key, so no row can tell them apart."""
        from unittest import mock

        def oldest_equal(self, event):
            keys = self.lineage_keys()
            for i, e in enumerate(self.events()):
                if e == event:
                    return keys[i]
            return keys[-1] if keys else "1"

        red = {"E1", "E2", "E3", "E7", "E9"}
        with mock.patch.object(Ledger, "lineages_for_sha",
                               _old_lineages_for_sha), \
                mock.patch.object(Ledger, "lineage_of", oldest_equal):
            self._under(self._check_sha, red, self.LEDGERS)
            self._under(self._check_guard, red, self.CROSS)


def _derived(events: list[dict]) -> list[str]:
    """The legacy-key rule restated ONCE, here, as the oracle the cached
    index is compared with: an independent derivation, so a cache serving a
    stale state cannot agree with it by construction."""
    from review.ledger import declared_lineage
    keys, ordinal, prefix = [], 1, True
    for e in events:
        d = declared_lineage(e)
        if d:
            prefix = False
            keys.append(d)
            continue
        keys.append(str(ordinal))
        if prefix and e.get("event") == Ledger.LINEAGE_CLOSED:
            ordinal += 1
    return keys


class TestTheLineageIndexFollowsTheLedger(unittest.TestCase):
    """TL-2: the lineage index is derived once per state of `events()` —
    the list's identity and its length — and a state change derives it
    afresh. One row per way the state changes, and one for a caller that
    edits what it was handed."""

    def setUp(self):
        import tempfile, shutil
        self.dir = Path(tempfile.mkdtemp(prefix="lineage-index-"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _assert_index(self, ledger: Ledger):
        events = ledger.events()
        keys = _derived(events)
        self.assertEqual(ledger.lineage_keys(), keys)
        for key in set(keys):
            self.assertEqual(ledger.current(key),
                             [e for e, k in zip(events, keys) if k == key])
        self.assertEqual(ledger.lineages(), list(dict.fromkeys(keys)))

    def test_an_add_is_seen_by_the_next_read(self):
        ledger = Ledger(self.dir)
        ledger.add({"event": "request", "sha": "a" * 40, "round": 1})
        self._assert_index(ledger)
        ledger.add({"event": "lineage_closed", "at_round": 1})
        ledger.add({"event": "request", "sha": "b" * 40, "round": 1})
        self._assert_index(ledger)
        ledger.add({"event": "request", "sha": "c" * 40, "round": 1},
                   lineage="Labcdef0123")
        self._assert_index(ledger)
        self.assertIn("Labcdef0123", ledger.lineages())

    def test_an_in_memory_add_is_seen_by_the_next_read(self):
        ledger = Ledger.in_memory()
        self.assertEqual(ledger.lineages(), [])
        ledger.add({"event": "request", "sha": "a" * 40, "round": 1},
                   lineage="L0000000001")
        self._assert_index(ledger)
        self.assertEqual(ledger.lineages(), ["L0000000001"])

    def test_a_rewrite_of_the_same_length_is_seen_after_reload(self):
        """`migrate-state` rewrites the file in place: as many rows, keyed.
        Length alone would serve the old keys."""
        _write_rows(self.dir, [
            {"event": "request", "sha": "a" * 40, "round": 1},
            {"event": "lineage_closed", "at_round": 1}])
        ledger = Ledger(self.dir)
        self._assert_index(ledger)
        _write_rows(self.dir, [
            {"event": "request", "sha": "a" * 40, "round": 1,
             "lineage": "L1111111111"},
            {"event": "lineage_closed", "at_round": 1,
             "lineage": "L1111111111"}])
        ledger.reload()
        self._assert_index(ledger)
        self.assertEqual(ledger.lineages(), ["L1111111111"])

    def test_a_caller_editing_what_it_was_handed_changes_nothing(self):
        ledger = Ledger(self.dir)
        ledger.add({"event": "request", "sha": "a" * 40, "round": 1})
        ledger.lineage_keys().append("X")
        ledger.current("1").clear()
        ledger.lineages().append("X")
        self._assert_index(ledger)


def _gates(*ids: str) -> str:
    return "".join(f'\n[[gates]]\nid = "{g}"\ncommand = ["true"]\n'
                   f"blocking = true\n" for g in ids)


def _attestations(kept: str) -> list[dict]:
    """The attestation records of a kept request, read from its own block."""
    from review import TOOL_NAME, wire
    text = Path(kept).read_text(encoding="utf-8")
    fence = "```" + wire.attestation_fence(TOOL_NAME)
    start = text.index(fence) + len(fence)
    return json.loads(text[start:text.index("\n```", start)])


class TestTheTakeNamesTheLogDirectoryOnce(unittest.TestCase):
    """RR3 through `bin/loupe handoff` and `bin/loupe take`: the request's
    log pointers, as the kept request records them, against the take's
    compact view of it. Rows: several gates (one directory, named once) and
    one gate (nothing hoisted, the pointer whole). The classes a hand-off
    cannot produce — a second directory, a pointer that is not a string —
    are held on the renderer in `test_take_compact`."""

    def _round(self, *gates):
        s = Scratch(self, "take-logs-", gates=_gates(*gates))
        code, rec, out, err = loupe(s, "handoff", "--claim-file",
                                    str(s.claim), "--base", s.base)
        self.assertEqual(code, 0, (out, err))
        clone, _state, _hooks = s.reviewer_clone()
        code, taken, out, err = loupe(s, "take", rec["kept"], "--as",
                                      "codex", where=clone,
                                      state=s.root / "state-take")
        self.assertEqual(code, 0, (out, err))
        return _attestations(rec["kept"]), taken["request_view"]

    def test_several_gates_name_their_directory_once(self):
        records, view = self._round("one", "two", "three")
        pointers = [r["output"]["pointer"] for r in records]
        directories = {p.rsplit("/", 1)[0] for p in pointers}
        self.assertEqual(len(directories), 1)
        (directory,) = directories
        self.assertEqual(view.count(directory), 1, view)
        self.assertIn(f"Logs: `{directory}/`", view)
        for p in pointers:
            self.assertIn(f"| …/{p.rsplit('/', 1)[1]} |", view)
            self.assertNotIn(p, view)
        # Round-2 F5: the printed rule rebuilds every pointer exactly.
        from review.tests.test_take_compact import reconstruct
        self.assertEqual(reconstruct(view), pointers)

    def test_one_gate_keeps_its_whole_pointer(self):
        """Not hoisted: the one row prints its whole pointer, as recorded
        when every character is inert and `%:`-encoded otherwise (0.29.0
        round 1 F2). The expected cell is derived here from the published
        rule, not from `brief`: a scratch directory under `mkdtemp` may hold
        `_` (macOS's `/var/folders/b_/…` always does), which the table now
        encodes."""
        records, view = self._round("one")
        (pointer,) = [r["output"]["pointer"] for r in records]
        self.assertNotIn("Logs:", view)
        self.assertNotIn("| …/", view)
        inert = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                    "0123456789./-")
        cell = (pointer if pointer != "-" and set(pointer) <= inert else
                "%:" + "".join(c if c in inert else "".join(
                    f"%{b:02X}" for b in c.encode("utf-8")) for c in pointer))
        self.assertIn(f"| {cell} |", view)
        from review.tests.test_take_compact import reconstruct
        self.assertEqual(reconstruct(view), [pointer])


class TestARequestListsTheWaiversOfItsSpan(unittest.TestCase):
    """RR4 through `bin/loupe waive` and `bin/loupe handoff`: the request's
    report lists the waivers inside base..head and counts the rest, and
    drops the manifest line its Evidence block already gives; `ledger
    report` still lists every waiver and the manifest.

    The scratch history is base -> mid -> head; the request reviews
    base..head, so `mid` is inside the span and `base` is outside it.
    Rows: one inside and one outside; none at all; every one inside; and
    `ledger report` beside the first (unchanged)."""

    def setUp(self):
        from review.tests._real_cli import git
        self.s = Scratch(self, "waiver-span-", gates=_gates("one"))
        (self.s.repo / "g.txt").write_text("mid\n", encoding="utf-8")
        git(self.s.repo, "add", "g.txt")
        git(self.s.repo, "commit", "-qm", "mid")
        self.mid = git(self.s.repo, "rev-parse", "HEAD")
        (self.s.repo / "f.txt").write_text("three\n", encoding="utf-8")
        git(self.s.repo, "commit", "-qam", "head")
        self.head = git(self.s.repo, "rev-parse", "HEAD")

    def _waive(self, sha, reason):
        code, payload, out, err = loupe(self.s, "waive", "--sha", sha,
                                        "--reason", reason, "--by", "a person")
        self.assertEqual(code, 0, (out, err))

    def _request(self) -> str:
        code, rec, out, err = loupe(self.s, "handoff", "--claim-file",
                                    str(self.s.claim), "--base", self.s.base)
        self.assertEqual(code, 0, (out, err))
        self.assertEqual(rec["sha"], self.head)
        return Path(rec["kept"]).read_text(encoding="utf-8")

    @staticmethod
    def _waived_lines(text: str) -> list[str]:
        start = text.index("Waived (deliberately unreviewed)")
        block = text[start:text.index("\n\n", start)]
        return block.splitlines()

    def test_one_inside_one_outside(self):
        self._waive(self.mid, "inside-reason")
        self._waive(self.s.base, "outside-reason")
        text = self._request()
        head, *listed = self._waived_lines(text)
        self.assertTrue(head.startswith(
            "Waived (deliberately unreviewed): **2** — 1 inside "
            f"{self.s.base[:12]}..{self.head[:12]}"), head)
        self.assertIn("the other 1 are outside it", head)
        self.assertEqual(len(listed), 1)
        self.assertIn(f"`{self.mid[:12]}` — inside-reason", listed[0])
        self.assertNotIn("outside-reason", text)
        self.assertNotIn("Gate manifest in force", text)
        # `ledger report` is unchanged: every waiver, and the manifest.
        code, _p, report, err = loupe(self.s, "ledger", "report",
                                      "--format", "md")
        self.assertEqual(code, 0, err)
        self.assertIn("Waived (deliberately unreviewed): **2**\n", report)
        self.assertIn("inside-reason", report)
        self.assertIn("outside-reason", report)
        self.assertIn("Gate manifest in force: one", report)

    def test_no_waiver_prints_the_bare_count(self):
        text = self._request()
        self.assertEqual(self._waived_lines(text),
                         ["Waived (deliberately unreviewed): **0**"])

    def test_every_waiver_inside_names_no_other(self):
        self._waive(self.mid, "inside-reason")
        head, *listed = self._waived_lines(self._request())
        self.assertNotIn("the other", head)
        self.assertEqual(len(listed), 1)


class TestTheSpanMatchOfAWaiver(unittest.TestCase):
    """RR4's match on the renderer: a hand-built waiver may abbreviate its
    SHA; seven characters or more match, fewer never do."""

    def test_the_match(self):
        from review.ledger import render_report_md
        full = "abcdef0" + "1" * 33
        report = {"breakers_fired": [], "round_cap": 3, "gate_manifest": ["g"],
                  "metrics": {"rounds_to_clean": "open", "rounds": {}},
                  "events": 0, "ledger": "x",
                  "waived": {"count": 4, "commits": [
                      {"sha": full, "reason": "full"},
                      {"sha": full[:7], "reason": "seven"},
                      {"sha": full[:6], "reason": "six"},
                      {"sha": None, "reason": "none"}]}}
        text = render_report_md(report, request_span=("a..b",
                                                      frozenset({full})))
        self.assertIn("**4** — 2 inside a..b", text)
        for reason, listed in (("full", True), ("seven", True),
                               ("six", False), ("none", False)):
            with self.subTest(reason=reason):
                self.assertEqual(f"— {reason} (" in text, listed)
        self.assertNotIn("Gate manifest in force", text)
        self.assertIn("Gate manifest in force: g",
                      render_report_md(report))


class TestAHandoffRefusesAHeadAnotherLineageTargets(unittest.TestCase):
    """RR1, the hand-off half, through `bin/loupe handoff`: a head another
    lineage's request or take already names refuses once the head is known
    and before any gate runs, unless `--shared-target` declares a second
    review of that commit.

    The gate appends to a marker OUTSIDE the repository, so "no gate ran"
    is a file that does not grow; "nothing left the machine" is the remote
    ref, read from the bare remote; "nothing recorded" is the ledger and
    the exchange directory, compared as bytes.

    Rows (one class each):
      refuse   another OPEN lineage's request names the head (a second branch
               at the same commit)
      refuse   a CLOSED lineage's request names it
      refuse   another lineage's TAKE names it (a reviewer on this ledger)
      pass     the first row's state with --shared-target (paired control)
      pass     the second row's lineage closed, then a NEW commit (control:
               the check reads the head the hand-off would target)
      pass     the hand-off sweeps outstanding work into a new commit (the
               head is the swept commit, which no lineage names)
      refuse   two CLOSED legacy lineages hold EQUAL uid-less requests at the
               head (round-2 F4): both are named, not only the oldest
      refuse   round-2 F6, the recovery advice: refused before AND after the
               other lineage is closed, and the advice never offers closing
               it, and says closing does not clear; each remedy it names
               (a new commit, --shared-target) clears it after the close

    MUTATIONS (round-2 F6, 2026-10-05, python3.15 -B, `review/cli.py`
    copied aside, restored from the copy and compared byte for byte): the
    closure remedy restored -> `test_the_advice_offers_only_what_clears`
    red, every other row green.
    """

    def setUp(self):
        self.s = Scratch(self, "shared-target-")
        self.marker = self.s.root / "gate-ran"
        self.s.write_config(gates=(
            '\n[[gates]]\nid = "mark"\n'
            f'command = ["sh", "-c", "echo ran >> {self.marker}"]\n'
            "blocking = true\n"))
        from review.tests._real_cli import git
        self.git = git
        git(self.s.repo, "commit", "-qam", "gate")
        self.head = git(self.s.repo, "rev-parse", "HEAD")

    def _handoff(self, *extra):
        return loupe(self.s, "handoff", "--claim-file", str(self.s.claim),
                     "--base", self.s.base, *extra)

    def _opened(self):
        code, rec, out, err = self._handoff()
        self.assertEqual(code, 0, (out, err))
        self.assertEqual(rec["sha"], self.head)
        return rec

    def _snapshot(self):
        state = self.s.state
        files = {p.relative_to(state): p.read_bytes()
                 for p in sorted(state.rglob("*"))
                 if p.is_file() and "gate-output" not in p.parts
                 # A reservation lock is the empty file a hand-off creates
                 # before it reads anything; it guards nothing and records
                 # nothing.
                 and not (p.suffix == ".lock" and not p.read_bytes())}
        marker = self.marker.read_bytes() if self.marker.exists() else b""
        refs = self.git(self.s.remote, "for-each-ref",
                        "--format=%(refname) %(objectname)")
        return files, marker, refs

    def _assert_refused(self, *extra, names: str):
        before = self._snapshot()
        code, rec, out, err = self._handoff(*extra)
        self.assertNotEqual(code, 0, (out, err))
        self.assertIsNone(rec["next"])
        self.assertEqual(rec["next_kind"], "blocked")
        self.assertIn(f"is already the target of lineage(s) {names}",
                      rec["error"])
        self.assertIn("--shared-target", rec["remedy"])
        self.assertEqual(self._snapshot(), before,
                         "a refused hand-off ran a gate, pushed or recorded")
        return rec

    def _branch(self, name):
        self.git(self.s.repo, "checkout", "-q", "-b", name)

    def test_another_open_lineage_refuses(self):
        first = self._opened()
        self._branch("other")
        self._assert_refused(names=first["lineage"])
        self.assertNotIn("refs/heads/other",
                         self.git(self.s.remote, "for-each-ref"))

    def test_control_shared_target_declares_the_second_review(self):
        first = self._opened()
        self._branch("other")
        code, rec, out, err = self._handoff("--shared-target")
        self.assertEqual(code, 0, (out, err))
        self.assertEqual(rec["sha"], first["sha"])
        self.assertNotEqual(rec["lineage"], first["lineage"])

    def _close(self):
        code, closed, out, err = loupe(self.s, "close", "--lineage",
                                       "--reason", "done", "--by", "a person")
        self.assertEqual(code, 0, (out, err))

    def test_a_closed_lineage_refuses(self):
        first = self._opened()
        self._close()
        self._assert_refused(names=first["lineage"])

    def test_control_a_new_commit_after_a_closed_lineage(self):
        self._opened()
        self._close()
        (self.s.repo / "f.txt").write_text("four\n", encoding="utf-8")
        self.git(self.s.repo, "commit", "-qam", "next")
        code, rec, out, err = self._handoff()
        self.assertEqual(code, 0, (out, err))
        self.assertNotEqual(rec["sha"], self.head)

    def test_control_the_swept_commit_is_the_head_judged(self):
        self._opened()
        self._close()
        (self.s.repo / "f.txt").write_text("swept\n", encoding="utf-8")
        code, rec, out, err = self._handoff()
        self.assertEqual(code, 0, (out, err))
        self.assertNotEqual(rec["sha"], self.head)

    def test_another_lineages_take_refuses(self):
        """A reviewer's take on this ledger, in a lineage recorded on
        another branch: the hand-off from `main` opens its own lineage,
        and the take names its head."""
        ledger = Ledger(self.s.state)
        ledger.add({"event": "request", "sha": self.s.base, "round": 1,
                    "branch": "elsewhere"}, lineage="Lfeedface01")
        ledger.add({"event": "take", "sha": self.head, "round": 1},
                   lineage="Lfeedface01")
        self._assert_refused(names="Lfeedface01")

    def test_equal_uidless_requests_in_two_lineages_are_both_named(self):
        """Round-2 F4 through the hand-off: `[request, closed, request,
        closed]` with the two requests equal and uid-less. The hand-off
        opens a lineage of its own, and the refusal names BOTH earlier
        lineages; the old resolver named the second request's lineage
        `1` and lost `2`."""
        request = {"event": "request", "sha": self.head, "round": 1}
        closed = {"event": "lineage_closed", "at_round": 1}
        _write_rows(self.s.state, [request, closed, request, closed])
        keys = Ledger(self.s.state).lineage_keys()
        self.assertEqual(len(set(keys)), 2)
        self._assert_refused(names=", ".join(sorted(set(keys))))

    #: The refusal's own statement that closure does not clear it, and the
    #: two spellings of the closure remedy it used to offer.
    DOES_NOT_CLEAR = "Closing the other lineage does not clear this"
    CLOSURE_OFFERS = ("close --lineage", "closes the other lineage")

    def _assert_advice(self, rec):
        for offer in self.CLOSURE_OFFERS:
            self.assertNotIn(offer, rec["remedy"])
        self.assertIn(self.DOES_NOT_CLEAR, rec["remedy"])
        self.assertIn("commits the work", rec["remedy"])
        self.assertIn("--shared-target", rec["remedy"])

    def test_the_advice_offers_only_what_clears(self):
        first = self._opened()
        self._branch("other")
        with self.subTest(state="the other lineage open"):
            self._assert_advice(self._assert_refused(names=first["lineage"]))
        self.git(self.s.repo, "checkout", "-q", "main")
        self._close()
        self._branch("after-close")
        with self.subTest(state="the other lineage closed"):
            self._assert_advice(self._assert_refused(names=first["lineage"]))

    def test_each_remedy_the_advice_names_clears_it_after_a_close(self):
        for remedy in ("a new commit", "--shared-target"):
            with self.subTest(remedy=remedy):
                self.setUp()
                first = self._opened()
                self._close()
                self._assert_refused(names=first["lineage"])
                if remedy == "a new commit":
                    (self.s.repo / "f.txt").write_text("four\n",
                                                       encoding="utf-8")
                    self.git(self.s.repo, "commit", "-qam", "next")
                    code, rec, out, err = self._handoff()
                    self.assertNotEqual(rec.get("sha"), self.head)
                else:
                    code, rec, out, err = self._handoff("--shared-target")
                    self.assertEqual(rec.get("sha"), self.head)
                self.assertEqual(code, 0, (out, err))


class TestAHandoffChecksItsOwnCommitSubject(unittest.TestCase):
    """OF-6 through `bin/loupe handoff`: `[tool] commit_subject_check`
    runs on the subject of the commit the hand-off is about to make, the
    path of a file holding it appended, and a failure refuses with nothing
    committed.

    Rows (one class each):
      pass     no key declared, outstanding work: committed (control)
      pass     key declared, the check accepts: committed under the subject,
               and the check saw exactly that subject
      refuse   key declared, the check rejects: HEAD, the working tree, the
               state directory, the remote and the gate marker unchanged
      refuse   the check cannot start
      refuse   the check outlives the git ceiling
      pass     key declared and rejecting, but nothing to commit: the check
               judges only a commit the hand-off makes
      refuse   an empty list, and a string, at the configuration boundary
    """

    GOOD = "good: the work"
    BAD = "bad work"

    def setUp(self):
        from review.tests._real_cli import git
        self.git = git
        self.s = Scratch(self, "subject-check-")
        self.marker = self.s.root / "gate-ran"
        self.seen = self.s.root / "seen"

    def _configure(self, check: str | None, git_timeout=None):
        gates = ('\n[[gates]]\nid = "mark"\n'
                 f'command = ["sh", "-c", "echo ran >> {self.marker}"]\n'
                 "blocking = true\n")
        tool = f"\n[tool]\ncommit_subject_check = {check}\n" if check else ""
        self.s.write_config(gates=gates + tool, git_timeout=git_timeout)
        self.git(self.s.repo, "commit", "-qam", "config")

    def _accepting(self) -> str:
        script = f'cp "$1" {self.seen}; grep -q "^good" "$1"'
        return json.dumps(["sh", "-c", script, "check"])

    def _dirty(self):
        (self.s.repo / "f.txt").write_text("outstanding\n", encoding="utf-8")

    def _handoff(self, subject: str):
        claim = json.loads(self.s.claim.read_text(encoding="utf-8"))
        claim["commit_subject"] = subject
        self.s.claim.write_text(json.dumps(claim), encoding="utf-8")
        return loupe(self.s, "handoff", "--claim-file", str(self.s.claim),
                     "--base", self.s.base)

    def _snapshot(self):
        state = self.s.state
        files = {p.relative_to(state): p.read_bytes()
                 for p in sorted(state.rglob("*"))
                 if p.is_file() and "gate-output" not in p.parts
                 and not (p.suffix == ".lock" and not p.read_bytes())}
        return (files,
                self.git(self.s.repo, "rev-parse", "HEAD"),
                (self.s.repo / "f.txt").read_bytes(),
                self.git(self.s.remote, "for-each-ref",
                         "--format=%(refname) %(objectname)"),
                self.marker.exists())

    def _assert_refused(self, subject: str, needle: str):
        before = self._snapshot()
        code, rec, out, err = self._handoff(subject)
        self.assertNotEqual(code, 0, (out, err))
        self.assertEqual(rec["next_kind"], "blocked")
        self.assertIn(needle, rec["error"])
        self.assertIn("nothing was committed", rec["remedy"])
        self.assertEqual(self._snapshot(), before)
        return rec

    def _subject_of_head(self) -> str:
        return self.git(self.s.repo, "log", "-1", "--format=%s")

    def test_control_no_key_commits(self):
        self._configure(None)
        self._dirty()
        code, rec, out, err = self._handoff(self.BAD)
        self.assertEqual(code, 0, (out, err))
        self.assertEqual(self._subject_of_head(), self.BAD)

    def test_an_accepted_subject_commits(self):
        self._configure(self._accepting())
        self._dirty()
        code, rec, out, err = self._handoff(self.GOOD)
        self.assertEqual(code, 0, (out, err))
        self.assertEqual(self._subject_of_head(), self.GOOD)
        self.assertEqual(self.seen.read_bytes(),
                         (self.GOOD + "\n").encode("utf-8"))

    def test_a_rejected_subject_refuses_before_the_commit(self):
        self._configure(self._accepting())
        self._dirty()
        rec = self._assert_refused(self.BAD, "refused the subject")
        self.assertIn(repr(self.BAD), rec["error"])

    def test_a_check_that_cannot_start_refuses(self):
        self._configure(json.dumps([str(self.s.root / "no-such-check")]))
        self._dirty()
        self._assert_refused(self.GOOD, "could not start")

    def test_a_check_past_the_ceiling_refuses(self):
        self._configure(json.dumps(["sh", "-c", "sleep 30", "check"]),
                        git_timeout=2)
        self._dirty()
        self._assert_refused(self.GOOD, "did not finish in 2s")

    def test_control_nothing_to_commit_runs_no_check(self):
        self._configure(json.dumps(["false"]))
        code, rec, out, err = self._handoff(self.BAD)
        self.assertEqual(code, 0, (out, err))

    def test_the_value_is_judged_at_the_configuration_boundary(self):
        for value, needle in (("[]", "must name a command"),
                              ('"bin/check"', "must be a list of strings")):
            with self.subTest(value=value):
                self.s.write_config(
                    gates=f"\n[tool]\ncommit_subject_check = {value}\n")
                self.git(self.s.repo, "commit", "-qam", f"config {value}")
                self._dirty()
                before = self._snapshot()
                code, rec, out, err = self._handoff(self.GOOD)
                self.assertNotEqual(code, 0, (out, err))
                self.assertIn(needle, out + err)
                self.assertEqual(self._snapshot(), before)
                self.git(self.s.repo, "checkout", "-q", "--", "f.txt")


class TestTheRenderedSkillDescription(unittest.TestCase):
    """SL-B10 through `bin/loupe render-adapters --dir`: the description an
    agent loads in every session holds the trigger and the stamp clause;
    the configuration rule is in the body, on every surface."""

    def test_the_rendered_files(self):
        s = Scratch(self, "render-description-")
        out_dir = s.root / "rendered"
        code, payload, out, err = loupe(s, "render-adapters", "--dir",
                                        str(out_dir))
        self.assertEqual(code, 0, (out, err))
        from review import adapters
        for kind in ("claude-skill", "codex-skill"):
            with self.subTest(kind=kind):
                text = (out_dir / adapters.OUTPUTS[kind]).read_text(
                    encoding="utf-8")
                description = text.split("---")[1].split("description: ",
                                                         1)[1]
                self.assertNotIn("review.toml", description)
                self.assertIn("the envelope stamp says which side you hold",
                              description)
        for path in sorted(out_dir.rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            if "## What this is" in text:
                with self.subTest(path=path.name):
                    self.assertIn("A REVIEWED commit must carry `review.toml`",
                                  text)


#: What a skill directory holds after an install (RR8), stated here rather
#: than read from `INSTALL`, so an install that stopped writing a role file
#: cannot agree with the test by construction.
SKILL_DIRECTORY = {"SKILL.md", "author.md", "reviewer.md"}


class TestTheSkillSplitsByRole(unittest.TestCase):
    """RR8 through `bin/loupe render-adapters` as its caller runs it, HOME
    redirected into the scratch and CODEX_HOME emptied, so no legacy
    location of the operator's is ever a candidate: each skill installs
    `SKILL.md` with the shared rules and a routing line, and one file per
    side beside it, and `--check-install` and `--check` judge every file.

    Rows (one class each):
      pass     a fresh install writes exactly SKILL.md, author.md and
               reviewer.md into each skill directory, each equal to the
               rendering; `--check-install` reads every one in sync
      pass     the routing line names files that exist beside SKILL.md
      pass     a reviewer reads SKILL.md and reviewer.md and no author step,
               an author no reviewer step; no role file carries frontmatter,
               so no agent discovers one as a skill of its own
      pass     the agent-relayed-window clause stays in SKILL.md, the one
               file `docs/upgrading.md` §6 greps for it
      refuse   a missing role file is drift (`absent`), and the check writes
               nothing; `--install` writes it and leaves every other file
               `unchanged` (the control)
      refuse   a hand-edited role file is drift (`stale`); `--install`
               replaces it only after keeping its bytes
      refuse   an install from before the split (SKILL.md alone) is drift
               in all three files; `--install` replaces SKILL.md, keeping
               it, and writes both role files
      refuse   `--install --dir` from a rendering missing a role file
               refuses it as stale and writes nothing under HOME; the
               complete rendering installs (the control)
      refuse   `--check --dir`, the `adapters` gate, is red for a missing
               role file, names it and writes nothing; green, judging every
               role file, on the complete rendering (the control)

    MUTATIONS (2026-10-04, python3.15 -B, each restored from a copy):
    `install_targets` keeping only the three main kinds (every row but
    4 and 9 red); `check_install` skipping the role kinds (rows 1, 5, 6
    and 7 red); `check_all` skipping them (rows 8 and 9 red);
    `_skill_body` returning the whole procedure (rows 2 and 3 red, and
    `test_adapters`' one-source row).
    """

    def setUp(self):
        from review import adapters
        self.adapters = adapters
        self.s = Scratch(self, "skill-split-")
        self.home = self.s.root / "home"

    def _loupe(self, *argv):
        # An absolute CODEX_HOME inherited from the host would make its
        # `skills/loupe/` a legacy candidate, outside the scratch; an empty
        # one names nothing.
        return loupe(self.s, *argv, env={"CODEX_HOME": ""})

    def _target(self, kind) -> Path:
        rel = self.adapters.INSTALL[kind][0]
        self.assertTrue(rel.startswith("~/"))
        return self.home / rel[2:]

    def _install(self) -> dict:
        code, payload, out, err = self._loupe("render-adapters", "--install")
        self.assertEqual(code, 0, (out, err))
        return {r["kind"]: r for r in payload["installed"]}

    def _check_install(self) -> tuple[int, dict]:
        code, payload, out, err = self._loupe("render-adapters",
                                              "--check-install")
        self.assertIsNotNone(payload, (out, err))
        return code, {r["kind"]: r["status"] for r in payload["install"]}

    @staticmethod
    def _snapshot(root: Path) -> dict:
        return {p.relative_to(root).as_posix(): p.read_bytes()
                for p in sorted(root.rglob("*")) if p.is_file()}

    def _rendered(self) -> Path:
        rendered = self.s.root / "rendered"
        code, _payload, out, err = self._loupe("render-adapters", "--dir",
                                               str(rendered))
        self.assertEqual(code, 0, (out, err))
        return rendered

    def test_a_fresh_install_writes_every_file_in_sync(self):
        installed = self._install()
        self.assertEqual({r["status"] for r in installed.values()},
                         {"installed"})
        for surface in self.adapters.SKILL_ROLE_KINDS:
            with self.subTest(surface=surface):
                directory = self._target(surface).parent
                self.assertEqual({p.name for p in directory.iterdir()},
                                 SKILL_DIRECTORY)
        for kind in self.adapters.INSTALL:
            with self.subTest(kind=kind):
                self.assertEqual(self._target(kind).read_bytes(),
                                 self.adapters.render(kind).encode("utf-8"))
        code, rows = self._check_install()
        self.assertEqual(code, 0, rows)
        self.assertEqual(sorted(rows), sorted(self.adapters.INSTALL))
        self.assertEqual(len(rows), 2 * len(SKILL_DIRECTORY))
        self.assertEqual(set(rows.values()), {"in_sync"})

    def test_the_routing_line_names_files_beside_it(self):
        import re
        self._install()
        for surface, kinds in self.adapters.SKILL_ROLE_KINDS.items():
            skill = self._target(surface)
            text = skill.read_text(encoding="utf-8")
            routing = text[text.index("## Your side's procedure"):
                           text.index("## Verbs")]
            named = re.findall(
                r"`([a-z]+\.md)` beside this file \(`~/([^`]+)`\)", routing)
            self.assertEqual(len(named), len(kinds), routing)
            for name, rel in named:
                with self.subTest(surface=surface, name=name):
                    self.assertTrue((skill.parent / name).is_file())
                    self.assertEqual(self.home / rel, skill.parent / name)

    def test_each_side_reads_only_its_own_steps(self):
        self._install()
        for surface, kinds in self.adapters.SKILL_ROLE_KINDS.items():
            skill = self._target(surface).read_text(encoding="utf-8")
            for role, kind in kinds.items():
                other = next(r for r in kinds if r != role)
                mine = self._target(kind).read_text(encoding="utf-8")
                with self.subTest(surface=surface, role=role):
                    self.assertIn(f"## If you hold the {role} stamp", mine)
                    self.assertNotIn(f"## If you hold the {other} stamp",
                                     skill + mine)
                    self.assertFalse(mine.startswith("---"))
                    self.assertLess(len((skill + mine).encode()),
                                    len(self.adapters.render(
                                        "instructions-block").encode()))

    def test_the_window_clause_stays_in_skill_md(self):
        import re
        self._install()
        for surface in self.adapters.SKILL_ROLE_KINDS:
            skill = re.sub(r"\s+", " ", self._target(surface).read_text(
                encoding="utf-8")).lower()
            with self.subTest(surface=surface):
                self.assertIn("agent-relayed window", skill)
                self.assertIn(
                    "a harness outside this tool carries the relay block",
                    skill)

    def test_a_missing_role_file_is_drift_and_install_repairs_it(self):
        self._install()
        kind = self.adapters.SKILL_ROLE_KINDS["codex-skill"]["reviewer"]
        self._target(kind).unlink()
        before = self._snapshot(self.home)
        code, rows = self._check_install()
        self.assertEqual(code, 1, rows)
        self.assertEqual(self._snapshot(self.home), before,
                         "a drift check wrote under HOME")
        self.assertEqual(rows.pop(kind), "absent")
        self.assertEqual(set(rows.values()), {"in_sync"})
        installed = self._install()
        self.assertEqual(installed.pop(kind)["status"], "installed")
        self.assertEqual({r["status"] for r in installed.values()},
                         {"unchanged"})
        code, rows = self._check_install()
        self.assertEqual((code, set(rows.values())), (0, {"in_sync"}))

    def test_a_hand_edited_role_file_is_kept_then_replaced(self):
        self._install()
        kind = self.adapters.SKILL_ROLE_KINDS["claude-skill"]["author"]
        edited = b"my own author steps\r\n"
        self._target(kind).write_bytes(edited)
        code, rows = self._check_install()
        self.assertEqual((code, rows[kind]), (1, "stale"))
        row = self._install()[kind]
        self.assertEqual(row["status"], "replaced")
        kept = Path(row["kept"])
        self.assertTrue(kept.resolve().is_relative_to(self.s.root), kept)
        self.assertEqual(kept.read_bytes(), edited)
        self.assertEqual(self._target(kind).read_bytes(),
                         self.adapters.render(kind).encode("utf-8"))

    def test_an_install_from_before_the_split_upgrades_in_place(self):
        old = "an earlier loupe skill: the whole procedure in one file\n"
        for surface in self.adapters.SKILL_ROLE_KINDS:
            path = self._target(surface)
            path.parent.mkdir(parents=True)
            path.write_text(old, encoding="utf-8")
        code, rows = self._check_install()
        self.assertEqual(code, 1, rows)
        for surface, kinds in self.adapters.SKILL_ROLE_KINDS.items():
            with self.subTest(surface=surface):
                self.assertEqual(rows[surface], "stale")
                self.assertEqual({rows[k] for k in kinds.values()},
                                 {"absent"})
        installed = self._install()
        for surface, kinds in self.adapters.SKILL_ROLE_KINDS.items():
            with self.subTest(surface=surface):
                self.assertEqual(installed[surface]["status"], "replaced")
                self.assertEqual(Path(installed[surface]["kept"]).read_text(
                    encoding="utf-8"), old)
                self.assertEqual({installed[k]["status"]
                                  for k in kinds.values()}, {"installed"})
                self.assertEqual(
                    {p.name for p in self._target(surface).parent.iterdir()},
                    SKILL_DIRECTORY)
        code, rows = self._check_install()
        self.assertEqual((code, set(rows.values())), (0, {"in_sync"}))

    def test_an_install_from_a_directory_missing_a_role_file_writes_nothing(
            self):
        rendered = self._rendered()
        missing = rendered / "codex" / "author.md"
        kept = missing.read_bytes()
        missing.unlink()
        code, payload, out, err = self._loupe("render-adapters", "--install",
                                              "--dir", str(rendered))
        self.assertEqual(code, 1, (out, err))
        self.assertIn(str(missing), payload["error"])
        self.assertEqual(self._snapshot(self.home), {},
                         "a refused install wrote under HOME")
        missing.write_bytes(kept)
        code, payload, out, err = self._loupe("render-adapters", "--install",
                                              "--dir", str(rendered))
        self.assertEqual(code, 0, (out, err))
        self.assertEqual(
            {p.name for p in self._target("codex-skill").parent.iterdir()},
            SKILL_DIRECTORY)

    def test_the_drift_gate_names_a_missing_role_file(self):
        rendered = self._rendered()
        code, payload, out, err = self._loupe("render-adapters", "--check",
                                              "--dir", str(rendered))
        self.assertEqual(code, 0, (out, err))
        checked = {Path(p).relative_to(rendered).as_posix()
                   for p in payload["checked"]}
        self.assertLessEqual({f"{agent}/{name}" for agent in ("claude", "codex")
                              for name in SKILL_DIRECTORY}, checked)
        missing = rendered / "codex" / "reviewer.md"
        missing.unlink()
        before = self._snapshot(rendered)
        code, payload, out, err = self._loupe("render-adapters", "--check",
                                              "--dir", str(rendered))
        self.assertEqual(code, 1, (out, err))
        self.assertIn(str(missing), payload["error"])
        self.assertEqual(self._snapshot(rendered), before,
                         "the drift gate wrote")


if __name__ == "__main__":
    unittest.main()

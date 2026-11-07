"""`bin/loupe` in a scratch repository — the real entry point, the real argv,
and nothing of the operator's machine.

Built on `test_git_timeout.Scratch` (an author repository with a bare
remote, its own identity, its own hooks directory and state directory, and
a COMMITTED configuration). What this adds is the launcher itself, with:

  - HOME redirected into the scratch, so no user-level configuration, git
    configuration or install of the operator's is read;
  - the package's own PYTHONPATH/PYTHONSAFEPATH NOT passed: `bin/loupe`
    sets them itself and stashes the caller's for the gates, so passing the
    suite's would hand every gate a value its caller never set;
  - a TTY mode: `_out` prints its human rendering only when stdout is a
    terminal, so a rendering is proved through a pseudo-terminal and never
    through a patched `_tty`.

LAUNCHED, THEN FORKED (Osama, "cut in tests only", 2026-11-05). The first row
of each verb in a test process is `bin/loupe` launched whole, and so is a TTY
row, a row from a thread other than the main one, and a row whose `env` names
what the interpreter reads as it starts. Every other row forks from one process
that loaded the package (`_Loaded`), the verb's own code and every git and gate
under it still real. `test_fixture_copy` holds a forked row to a launched one.
`TEST_LAUNCH_EVERY_ROW=1` launches every row, to compare a whole module.

A STATE BUILT ONCE (`restored`): what several tests all start from, a hand-off
at 45 git launches above all, made once per test process and put back for each.

Every scratch `git init` sets its own local user.name and user.email
(`Scratch.IDENTITY`, and `reviewer_clone`).
"""
from __future__ import annotations

import atexit
import copy
import io
import json
import os
import select
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from review.tests._transport_fixtures import (
    _BuildCase, _template_parent, copy_tree)
from review.tests.test_git_timeout import Scratch, cli_env, git
from review.tests.util import REPO_ROOT

LOUPE = REPO_ROOT / "bin" / "loupe"

__all__ = ["LOUPE", "Scratch", "git", "loupe", "loupe_env", "restored", "row"]


def loupe_env(scratch: Scratch, state: Path, extra: dict | None = None
              ) -> dict:
    """The environment of one `bin/loupe` process in `scratch`."""
    env = cli_env(state, None)
    for name in ("PYTHONPATH", "PYTHONSAFEPATH", "XDG_CONFIG_HOME",
                 "XDG_STATE_HOME", "GIT_CONFIG_GLOBAL"):
        env.pop(name, None)
    home = scratch.root / "home"
    home.mkdir(exist_ok=True)
    env["HOME"] = str(home)
    env.update(extra or {})
    return env


def _tty_run(cmd, cwd, env, timeout) -> tuple[int, str, str]:
    """(exit, stdout as the terminal received it, stderr) with stdout on a
    pseudo-terminal. Read while the child runs: a take's rendering is
    larger than a pty's buffer, and a child blocked on a full buffer never
    exits."""
    master, slave = os.openpty()
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env,
                                stdin=subprocess.DEVNULL, stdout=slave,
                                stderr=err)
        os.close(slave)
        chunks, deadline = [], time.monotonic() + timeout
        try:
            while True:
                if time.monotonic() > deadline:
                    proc.kill()
                    raise TimeoutError(f"{cmd} did not finish in {timeout}s")
                ready, _, _ = select.select([master], [], [], 0.2)
                if ready:
                    try:
                        data = os.read(master, 65536)
                    except OSError:         # EIO: every slave end closed
                        break
                    if not data:
                        break
                    chunks.append(data)
                elif proc.poll() is not None:
                    # Exited with nothing pending: one last drain.
                    try:
                        while (data := os.read(master, 65536)):
                            chunks.append(data)
                    except OSError:
                        pass
                    break
        finally:
            os.close(master)
        code = proc.wait(timeout=timeout)
        err.seek(0)
        stderr = err.read().decode("utf-8", "replace")
    # The terminal's line discipline turns every "\n" into "\r\n".
    text = b"".join(chunks).decode("utf-8").replace("\r\n", "\n")
    return code, text, stderr


#: The program of `_Loaded`: the package imported once, then a forked child a
#: row, which takes the row's directory, environment and argv, with stdin as it
#: was (`/dev/null`) and stdout and stderr on files, and ends as `sys.exit(main())`
#: would. Says the child's pid, then how it ended; a closed pipe ends it.
FORKED_CLI = r"""
import json, os, sys

from review.cli import main

asks, says = os.fdopen(int(sys.argv[1]), encoding="ascii"), int(sys.argv[2])
for line in asks:
    row = json.loads(line)
    pid = os.fork()
    if pid:
        os.write(says, b"%d\n" % pid)
        ended = os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1])
        os.write(says, b"%d\n" % ended)
        continue
    status = 1
    try:
        asks.close()
        os.close(says)
        os.chdir(row["cwd"])
        os.environ.clear()
        os.environ.update(row["env"])
        for fd, path in ((1, row["out"]), (2, row["err"])):
            opened = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.dup2(opened, fd)
            os.close(opened)
        try:
            status = main(row["argv"])
        except SystemExit as stop:
            status = stop.code
        if status is None:
            status = 0
        elif not isinstance(status, int):
            print(status, file=sys.stderr)
            status = 1
        sys.stdout.flush()
    except BaseException:
        sys.excepthook(*sys.exc_info())
        status = 1
    finally:
        try:
            sys.stderr.flush()
        finally:
            os._exit(status & 0xFF)
"""


class _Loaded:
    """One process that imported the package, and a row forked from it per
    call. Skipped is the launch: `sh` running `bin/loupe`, the interpreter's
    start and the import, 114 ms at load 9 against 1.6 ms for a fork (measured
    2026-11-05). One per test process, started at its first forked row."""

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="loupe-forked-cli-")).resolve()
        env = dict(os.environ, HOME=str(self.dir), LOUPE_SHIM="1",
                   PYTHONSAFEPATH="1", PYTHONPATH=str(REPO_ROOT))
        python = "python3.15" if shutil.which("python3.15") else "python3"
        ask_r, ask_w = os.pipe()
        say_r, say_w = os.pipe()
        self.proc = subprocess.Popen(
            [python, "-c", FORKED_CLI, str(ask_r), str(say_w)],
            cwd=self.dir, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, pass_fds=(ask_r, say_w))
        os.close(ask_r)
        os.close(say_w)
        self.asks = os.fdopen(ask_w, "w", encoding="ascii")
        self.says = say_r
        self.heard = b""
        atexit.register(self.close)

    def close(self) -> None:
        if self.asks.closed:
            return
        try:
            self.asks.close()
        except BrokenPipeError:     # it died with a row still unread
            pass
        os.close(self.says)
        self.proc.wait(timeout=30)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _said(self, deadline: float) -> int | None:
        """The next number it says; None if `deadline` passes first."""
        while b"\n" not in self.heard:
            ready, _, _ = select.select(
                [self.says], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                return None
            data = os.read(self.says, 64)
            if not data:
                raise AssertionError("the loaded process died: exit "
                                     f"{self.proc.wait(timeout=30)}")
            self.heard += data
        line, self.heard = self.heard.split(b"\n", 1)
        return int(line)

    def run(self, argv: list[str], cwd: str, env: dict, timeout: float
            ) -> tuple[int, str, str]:
        out, err = self.dir / "stdout", self.dir / "stderr"
        try:
            self.asks.write(json.dumps({"argv": argv, "cwd": cwd, "env": env,
                                        "out": str(out), "err": str(err)})
                            + "\n")
            self.asks.flush()
        except BrokenPipeError:
            raise AssertionError("the loaded process died: exit "
                                 f"{self.proc.wait(timeout=30)}") from None
        # Said as it forks, once its load is done: not the row's time yet.
        pid = self._said(time.monotonic() + 60)
        if pid is None:
            raise AssertionError(f"the loaded process did not fork: {argv}")
        code = self._said(time.monotonic() + timeout)
        if code is None:
            os.kill(pid, signal.SIGKILL)
            self._said(time.monotonic() + 30)
            raise subprocess.TimeoutExpired([str(LOUPE), *argv], timeout)
        # As `subprocess.run(text=True)` reads a pipe: the locale's encoding,
        # strict, universal newlines.
        return (code, *(io.TextIOWrapper(io.BytesIO(p.read_bytes())).read()
                        for p in (out, err)))


_LOADED: list[_Loaded] = []
#: The verbs this process has launched whole.
_LAUNCHED: set[str] = set()
#: What the interpreter reads as it starts, and `sh` to find it: a row whose
#: `env` names one of these is launched, since a fork cannot start over.
_READ_AT_START = ("PYTHON", "LC_", "LANG", "PATH")


def _forks(argv: list[str], extra: dict | None, cwd: str) -> bool:
    if (os.environ.get("TEST_LAUNCH_EVERY_ROW")
            or threading.current_thread() is not threading.main_thread()
            or not os.path.isdir(cwd)
            or any(k.startswith(_READ_AT_START) for k in extra or ())):
        return False
    verb = next((a for a in argv if not a.startswith("-")), "")
    if verb not in _LAUNCHED:
        _LAUNCHED.add(verb)
        return False
    return True


def as_launched(env: dict, cwd: str) -> dict:
    """`env` as `bin/loupe` hands it to the interpreter: the caller's
    PYTHONSAFEPATH and PYTHONPATH stashed for the gates, or any older stash
    cleared, and its own set."""
    out = dict(env)
    for name in ("PYTHONSAFEPATH", "PYTHONPATH"):
        if name in out:
            out[f"LOUPE_CALLER_{name}"] = out[name]
        else:
            out.pop(f"LOUPE_CALLER_{name}", None)
    caller = out.get("PYTHONPATH")
    out.update(LOUPE_SHIM="1", PYTHONSAFEPATH="1", PWD=cwd,
               PYTHONPATH=f"{REPO_ROOT}:{caller}" if caller else str(REPO_ROOT))
    return out


def forked(argv: list[str], cwd: str, env: dict, timeout: float = 180
           ) -> tuple[int, str, str]:
    """One row forked from this process's `_Loaded`, started if need be."""
    if not _LOADED:
        _LOADED.append(_Loaded())
    return _LOADED[0].run(argv, cwd, as_launched(env, cwd), timeout)


def row(words: list[str], cwd: str, env: dict, extra: dict | None = None,
        timeout: float = 180) -> tuple[int, str, str]:
    """(exit, stdout, stderr) of `bin/loupe words...` with stdin on /dev/null,
    launched or forked (module docstring). `env` is the whole environment,
    `extra` what of it this row alone sets."""
    if _forks(words, extra, cwd):
        return forked(words, cwd, env, timeout)
    proc = subprocess.run([str(LOUPE), *words], cwd=cwd, env=env,
                          capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


#: key -> (the scratch as its build left it, where its bytes wait, the facts).
_RESTORABLE: dict = {}


def restored(case, key, build) -> tuple[Scratch, object]:
    """The scratch `build(case) -> (scratch, facts)` leaves, built once per test
    process and put back for each caller at the path it was built at. Unlike
    `copied_instance` this holds a review's state, whose request names its path
    and whose ledger and file names carry that request's digest, so it cannot
    move: one caller at a time per key, which a process running its tests one
    after another gives. The path goes at the caller's cleanup. Returns (a
    scratch of the caller's, a copy of the build's facts)."""
    if key not in _RESTORABLE:
        scratch, facts = build(_BuildCase())
        saved = Path(tempfile.mkdtemp(prefix="r-", dir=_template_parent()))
        copy_tree(scratch.root, saved)
        _RESTORABLE[key] = scratch, saved, facts
    scratch, saved, facts = _RESTORABLE[key]
    shutil.rmtree(scratch.root, ignore_errors=True)
    copy_tree(saved, scratch.root)
    case.addCleanup(shutil.rmtree, scratch.root, True)
    own = copy.copy(scratch)
    own.case = case
    return own, copy.deepcopy(facts)


def loupe(scratch: Scratch, *argv, where: Path | None = None,
          state: Path | None = None, env: dict | None = None,
          tty: bool = False, timeout: float = 180):
    """`bin/loupe argv...` in `where` (default: the author repository), or a
    row forked as if it were (module docstring).

    Returns (exit, payload, stdout, stderr): `payload` is the parsed JSON
    result, or None when the output is not JSON (every TTY run)."""
    words = [os.fsdecode(a) for a in argv]
    cwd = str(where or scratch.repo)
    environment = loupe_env(scratch, state or scratch.state, env)
    if tty:
        code, out, err = _tty_run([str(LOUPE), *words], cwd, environment,
                                  timeout)
    else:
        code, out, err = row(words, cwd, environment, env, timeout)
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        payload = None
    return code, payload, out, err

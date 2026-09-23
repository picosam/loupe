"""`bin/loupe` as a subprocess, in a scratch repository — the real entry
point, the real argv, and nothing of the operator's machine.

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

Every scratch `git init` sets its own local user.name and user.email
(`Scratch.IDENTITY`, and `reviewer_clone`).
"""
from __future__ import annotations

import json
import os
import select
import subprocess
import tempfile
import time
from pathlib import Path

from review.tests.test_git_timeout import Scratch, cli_env, git
from review.tests.util import REPO_ROOT

LOUPE = REPO_ROOT / "bin" / "loupe"

__all__ = ["LOUPE", "Scratch", "git", "loupe", "loupe_env"]


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


def loupe(scratch: Scratch, *argv, where: Path | None = None,
          state: Path | None = None, env: dict | None = None,
          tty: bool = False, timeout: float = 180):
    """`bin/loupe argv...` run in `where` (default: the author repository).

    Returns (exit, payload, stdout, stderr): `payload` is the parsed JSON
    result, or None when the output is not JSON (every TTY run)."""
    cmd = [str(LOUPE), *argv]
    cwd = str(where or scratch.repo)
    environment = loupe_env(scratch, state or scratch.state, env)
    if tty:
        code, out, err = _tty_run(cmd, cwd, environment, timeout)
    else:
        proc = subprocess.run(cmd, cwd=cwd, env=environment,
                              capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=timeout)
        code, out, err = proc.returncode, proc.stdout, proc.stderr
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        payload = None
    return code, payload, out, err

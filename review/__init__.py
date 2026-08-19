"""Deterministic, agent-neutral review tool — `loupe`.

Design: docs/design.md in the published repository (derived from the
workbench design record). Stdlib only, no network
I/O in the tool's own code, no LLM in any code path (§6.1); git performs the
one publish step (§9bis.4).

TOOL_NAME is the single name authority. Renamed `rvw` → `loupe` 2026-08-14
(§10.1, RVW-T6) as a migration, not a substitution: the wire dialect (wrapper
tag and attestation fence) derives from config's wrapper tag, whose default is
this constant; state and config paths derive from it; and the literals that
§10.1 found hardcoded in emit.py and validate.py were removed rather than
renamed, so the emitter and validator can no longer drift apart.

FORMER_NAMES is what keeps already-emitted artifacts readable: where a repo
speaks the default dialect, wrapper tags and attestation fences carrying a
former name are accepted with a notice — a recorded state, never a silent
one. State written under a former name is never silently orphaned: config
refuses loudly when it finds a legacy ledger and no current one, and
`migrate-state` moves it with digest verification (the 325-event slice-1
record is exactly what this protects).
"""

TOOL_NAME = "loupe"
TOOL_VERSION = "0.3.0"

# Names this tool has carried before, oldest first. Read acceptance for
# artifacts and state produced under them is deliberate and noticed, never
# assumed (§10.1: a half-renamed wire format is worse than a break because it
# is silent).
FORMER_NAMES = ("rvw",)


def env_var(suffix: str) -> str:
    """Environment variable name for this tool, e.g. LOUPE_STATE_DIR."""
    return f"{TOOL_NAME.upper()}_{suffix}"

"""The launch prerequisites ``cartopian dispatch`` enforces, as one shared check.

``dispatch`` refuses a launch when the role has no agent, a set-but-empty
model or effort, an invalid output contract, a host that cannot wait out the
role timeout, a work-root mapping that does not exist on this machine, or an
agent that does not resolve on PATH. The dispatch rehearsal must apply the
same checks before it calls a task dispatchable — a rehearsal that passes
while the real launch would refuse is a false readiness claim. Both consumers
therefore call this module; the wording is the wording dispatch prints.

Each finding is ``{code, prefix, message}``: ``prefix`` is the stderr channel
dispatch uses (``guard`` or ``error``), so dispatch's reporting is unchanged.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from cli import host_capability, output_safety

DEFAULT_TIMEOUT = "60m"
DEFAULT_TIMEOUT_SECONDS = 60 * 60


def _finding(code: str, prefix: str, message: str) -> Dict[str, str]:
    return {"code": code, "prefix": prefix, "message": message}


def role_checks(
    role: str,
    role_record: Mapping[str, Any],
    *,
    environ: Optional[Mapping[str, str]] = None,
    context: str = "dispatch",
) -> List[Dict[str, str]]:
    """Role-record prerequisites, in dispatch's order; empty when all hold."""
    launch = role_record.get("launch") or {}
    agent = launch.get("agent")
    if not agent:
        return [
            _finding(
                "agent-not-configured",
                "guard",
                f"roles.{role}.agent is not configured — dispatch this role manually",
            )
        ]
    model = launch.get("model")
    if model is not None and not model:
        return [
            _finding(
                "model-empty",
                "guard",
                f"roles.{role}.model is set but empty — set a model identifier or "
                "remove the key",
            )
        ]
    effort = launch.get("effort")
    if effort is not None and not effort:
        return [
            _finding(
                "effort-empty",
                "guard",
                f"roles.{role}.effort is set but empty — set an effort level or "
                "remove the key",
            )
        ]
    try:
        output_safety.limits_from_environment(
            dict(os.environ if environ is None else environ)
        )
    except output_safety.OutputSafetyError as exc:
        return [
            _finding(
                "output-contract-invalid",
                "guard",
                f"invalid automated-handoff output contract: {exc}",
            )
        ]
    timeout = launch.get("timeout") or DEFAULT_TIMEOUT
    host_ok, _budget, host_refusal = host_capability.check_wait_budget(
        role,
        host_capability.parse_duration(str(timeout)) or DEFAULT_TIMEOUT_SECONDS,
        context=context,
    )
    if not host_ok:
        return [_finding("host-wait-budget", "guard", str(host_refusal))]
    return []


def environment_checks(
    role: str,
    role_record: Mapping[str, Any],
    resolved_work_roots: Mapping[str, str],
) -> List[Dict[str, str]]:
    """Machine prerequisites: mapped work roots exist and the agent resolves."""
    findings: List[Dict[str, str]] = []
    missing_roots = [
        path for path in resolved_work_roots.values() if not Path(path).is_dir()
    ]
    if missing_roots:
        findings.append(
            _finding(
                "work-root-missing",
                "guard",
                "work root path(s) do not exist on this machine: "
                + ", ".join(missing_roots)
                + " — fix the [work_roots] mapping in cartopian.local.toml",
            )
        )
    agent = (role_record.get("launch") or {}).get("agent")
    if agent and shutil.which(str(agent)) is None:
        findings.append(
            _finding(
                "agent-not-found",
                "error",
                f"handoff agent not found on PATH: {agent} — install the wrapper "
                f"(on native Windows the `.cmd` shim in wrappers/ps1 must be on PATH), "
                f"or set roles.{role}.agent to an absolute path",
            )
        )
    return findings

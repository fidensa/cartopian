"""Small, explicit presentation views of complete lifecycle evaluations.

These functions never decide readiness, authority, or audit success. Detail
commands remain available, and blocking evidence is never summarized away.
"""
from collections import Counter
from typing import Any, Dict, List


def _counts(findings: List[Dict[str, Any]]) -> Dict[str, int]:
    return dict(sorted(Counter(item.get("kind", "unspecified") for item in findings).items()))


def compact_audit(record: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(record)
    result["projection"] = "compact-audit-v1"
    result["warning_summary"] = _counts(result.pop("warnings"))
    result["attribution_count"] = len(result.pop("attributions"))
    provenance = dict(result["provenance"])
    provenance["advisory_summary"] = _counts(provenance.pop("advisory", []))
    result["provenance"] = provenance
    result["details"] = {
        "command": "plan-audit",
        "arguments": {"project_path": record["project_path"]},
    }
    return result


def compact_orientation(record: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(record)
    result["projection"] = "compact-orientation-v1"
    roles = result.pop("roles")
    result["role_names"] = sorted(roles)
    # The PM's own permissions stay visible. Other roles' launch/configuration
    # facts are evaluated internally for readiness and loaded at assignment.
    result["pm_effective_grants"] = roles.get("pm", {}).get("effective_grants", [])
    result["details"] = {
        "command": "next-action",
        "arguments": {"project_path": record["project_path"]},
    }
    return result

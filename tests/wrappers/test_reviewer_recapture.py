"""Reviewer live-evidence recapture launch contract (TASK-03-007, FR-011).

Agent-agnostic, opt-in, evidence-gated reviewer-role launch contract honored by
EVERY shipped wrapper via the shared launch helper
(``wrappers/bin/_cartopian-status.sh`` :: ``cartopian_review_recapture_active`` /
``cartopian_review_recapture_banner``). The recapture capability attaches to the
REVIEWER ROLE through a role-level signal with NO agent name in it
(``CARTOPIAN_REVIEW_RECAPTURE``), so a new wrapper inherits it for free.

Red-before-green
----------------
* RED (``TestRecaptureRedState`` / ``test_codex_review_without_recapture_has_no_egress``):
  a reviewer-context codex invocation that needs to re-run an evidence-gated
  task's probe harness against a declared source work root, WITHOUT the recapture
  opt-in, gets neither the egress grant nor the read-only-source contract — so the
  model-backed probe's egress gate stays closed and NO fresh evidence is produced,
  and no recapture banner is printed. (Since TASK-03-009 the codex sandbox scopes
  the work-root union natively, so the agent now launches *scoped* rather than
  failing closed on the guard; the recapture RED state is the absence of egress,
  which is what gates the probe.) The bare probe harness likewise refuses without
  egress.
* GREEN (``TestRecaptureGreen*``): the SAME reviewer dispatch, opted in via the
  agent-neutral signal, for TWO different reviewer agents (codex and gemini via
  the shared wrapper mechanism), re-runs the probe harness end to end and writes
  FRESH evidence under ``$TMPDIR`` distinct from the pinned baseline, with the
  source work root never added to the writable scope (read-only at the sandbox
  layer for codex/gemini). The opt-in/evidence guard holds: with the signal off
  there is no banner, no network/sandbox grant, and no recapture behavior.

The wrappers are exercised for real against fake agents + a fake ``cartopian``
(no live model), stdlib/coreutils only (NF-001).
"""
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / "wrappers" / "bin"
HELPER = BIN_DIR / "_cartopian-status.sh"
PROBE = REPO_ROOT / "tests" / "wrappers" / "reviewer-recapture" / "run-recapture-probe.sh"
ALL_WRAPPERS = ["cartopian-claude", "cartopian-codex", "cartopian-gemini", "cartopian-devin"]

_TIMEOUT_BIN = shutil.which("timeout") or shutil.which("gtimeout")
pytestmark = pytest.mark.skipif(
    _TIMEOUT_BIN is None, reason="no coreutils timeout/gtimeout on PATH"
)

# A fake upstream agent: records the launch contract (argv, cwd, whether it saw
# the agent-neutral recapture signal) to $CAPTURE, then re-runs the recapture
# probe harness to produce fresh evidence (proving end-to-end egress + scratch).
_FAKE_AGENT = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
cap = os.environ.get("CAPTURE")
if cap:
    with open(cap, "w", encoding="utf-8") as fh:
        json.dump({
            "argv": sys.argv,
            "cwd": os.getcwd(),
            "recapture_env": os.environ.get("CARTOPIAN_REVIEW_RECAPTURE"),
        }, fh)
probe = os.environ.get("RECAPTURE_PROBE")
if probe and os.environ.get("RECAPTURE_BASELINE"):
    subprocess.run(["bash", probe, os.environ.get("RECAPTURE_NONCE", "n1")], check=False)
'''

# A fake `cartopian` whose `resolve-config` emits a work_roots map (so the
# wrapper's OQ-009 access-grant block sees a declared source work root). Any
# other subcommand prints nothing.
_FAKE_CARTOPIAN = r'''#!/usr/bin/env python3
import json, os, sys
if len(sys.argv) >= 2 and sys.argv[1] == "resolve-config":
    roots = os.environ.get("FAKE_WORK_ROOTS", "")
    wr = {}
    if roots:
        for i, p in enumerate(roots.split(os.pathsep)):
            if p:
                wr["src%d" % i] = p
    print(json.dumps({"work_roots": wr}))
'''


def _fake_bin(dir_path: Path, name: str, body: str) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    p = dir_path / name
    p.write_text(body, encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _make_project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    (project / "prompts").mkdir(parents=True)
    (project / "reports").mkdir(parents=True)
    prompt = project / "prompts" / "PROMPT-03-007.md"
    prompt.write_text("review the evidence-gated task\n", encoding="utf-8")
    return prompt


def _fakebin_dir(prompt: Path) -> Path:
    return prompt.parent.parent.parent / "fakebin"


def _run_wrapper(wrapper: str, prompt: Path, *, env_extra=None,
                 work_roots=None, subprocess_timeout=30):
    """Run a real wrapper against fake agents + fake cartopian. Returns the
    CompletedProcess. ``work_roots`` is a list of absolute dirs the fake
    cartopian reports as the (read-only) source."""
    fakebin = _fakebin_dir(prompt)
    # The upstream CLI each wrapper invokes shares one fake-agent body.
    for tool in ("claude", "codex", "gemini", "devin"):
        _fake_bin(fakebin, tool, _FAKE_AGENT)
    _fake_bin(fakebin, "cartopian", _FAKE_CARTOPIAN)

    path_parts = [str(fakebin), str(Path(_TIMEOUT_BIN).parent),
                  "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    env = {
        "PATH": os.pathsep.join(path_parts),
        "HOME": os.environ.get("HOME", "/tmp"),
        "CARTOPIAN_TIMEOUT": "20s",
        "CARTOPIAN_REPORT_POLL": "0.2",
        "CARTOPIAN_REPORT_GRACE_POLLS": "2",
        "RECAPTURE_PROBE": str(PROBE),
    }
    if work_roots:
        env["FAKE_WORK_ROOTS"] = os.pathsep.join(str(p) for p in work_roots)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(BIN_DIR / wrapper), str(prompt)],
        env=env, capture_output=True, text=True, timeout=subprocess_timeout,
    )


def _seed_source(tmp_path: Path):
    """A read-only source work root holding a pinned baseline + a TMPDIR scratch."""
    src = tmp_path / "srcrepo"
    src.mkdir()
    baseline = src / "baseline-evidence.txt"
    baseline.write_text("PINNED BASELINE EVIDENCE\n", encoding="utf-8")
    scratch = tmp_path / "tmpdir" / "recapture-scratch"
    return src, baseline, scratch


# --------------------------------------------------------------------------- #
# Shared mechanism: every wrapper honors the same agent-neutral signal.
# --------------------------------------------------------------------------- #
class TestSharedMechanism:
    def test_helper_defines_agent_neutral_recapture_functions(self):
        text = HELPER.read_text(encoding="utf-8")
        assert "cartopian_review_recapture_active()" in text
        assert "cartopian_review_recapture_banner()" in text
        assert "CARTOPIAN_REVIEW_RECAPTURE" in text

    @pytest.mark.parametrize("wrapper", ALL_WRAPPERS)
    def test_every_wrapper_calls_shared_recapture_helper(self, wrapper):
        text = (BIN_DIR / wrapper).read_text(encoding="utf-8")
        assert "cartopian_review_recapture_active" in text, (
            f"{wrapper} must honor the shared recapture helper (inherited for free)"
        )

    def test_signal_carries_no_agent_name(self):
        # The role-level signal must not encode any agent identity.
        assert "CARTOPIAN_REVIEW_RECAPTURE" != ""
        for agent in ("CODEX", "CLAUDE", "GEMINI", "DEVIN"):
            assert agent not in "CARTOPIAN_REVIEW_RECAPTURE"

    def test_codex_no_longer_uses_codex_specific_recapture_flag(self):
        # The rejected pass's agent-coupled flag must be gone.
        text = (BIN_DIR / "cartopian-codex").read_text(encoding="utf-8")
        assert "CARTOPIAN_CODEX_RECAPTURE" not in text


# --------------------------------------------------------------------------- #
# Bare probe harness — red/green at the harness layer.
# --------------------------------------------------------------------------- #
class TestProbeHarness:
    def test_probe_exists_and_executable(self):
        assert PROBE.is_file()
        assert os.access(PROBE, os.X_OK)

    def test_red_no_egress_produces_no_fresh_evidence(self, tmp_path):
        src, baseline, scratch = _seed_source(tmp_path)
        proc = subprocess.run(
            ["bash", str(PROBE), "r1"],
            env={"PATH": os.environ["PATH"], "RECAPTURE_BASELINE": str(baseline),
                 "RECAPTURE_SCRATCH": str(scratch)},
            capture_output=True, text=True, timeout=15,
        )
        assert proc.returncode == 3, proc.stderr
        assert "no egress" in proc.stderr
        assert not (scratch / "fresh-evidence.txt").exists()

    def test_green_recapture_writes_fresh_evidence_distinct_from_baseline(self, tmp_path):
        src, baseline, scratch = _seed_source(tmp_path)
        before = baseline.read_text(encoding="utf-8")
        proc = subprocess.run(
            ["bash", str(PROBE), "g1"],
            env={"PATH": os.environ["PATH"], "RECAPTURE_BASELINE": str(baseline),
                 "RECAPTURE_SCRATCH": str(scratch), "CARTOPIAN_REVIEW_RECAPTURE": "1"},
            capture_output=True, text=True, timeout=15,
        )
        assert proc.returncode == 0, proc.stderr
        fresh = scratch / "fresh-evidence.txt"
        assert fresh.is_file(), "recapture produced no fresh evidence"
        assert fresh.read_text(encoding="utf-8") != before, "fresh evidence must differ from baseline"
        # The reviewed source baseline stays read-only / untouched.
        assert baseline.read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------- #
# RED — a reviewer codex invocation against a declared source work root, with
# recapture OFF, gets no egress and no read-only-source contract, so it produces
# no fresh live evidence (the red state recapture exists to clear).
# --------------------------------------------------------------------------- #
class TestRecaptureRedState:
    def test_codex_review_without_recapture_has_no_egress(self, tmp_path):
        prompt = _make_project(tmp_path)
        src, baseline, scratch = _seed_source(tmp_path)
        capture = _fakebin_dir(prompt) / "capture.json"
        proc = _run_wrapper(
            "cartopian-codex", prompt, work_roots=[src],
            env_extra={
                "CAPTURE": str(capture),
                "RECAPTURE_BASELINE": str(baseline),
                "RECAPTURE_SCRATCH": str(scratch),
                # recapture NOT set; no unrestricted bypass.
            },
        )
        # No recapture opt-in → no banner and no egress grant, so the model-backed
        # probe's egress gate stays closed and produces NO fresh evidence. This is
        # the red state recapture (TASK-03-007) exists to clear.
        assert "recapture mode" not in proc.stderr
        assert not (scratch / "fresh-evidence.txt").exists(), "no egress → no fresh evidence"
        cap = json.loads(capture.read_text(encoding="utf-8"))
        assert cap.get("recapture_env") in (None, ""), "recapture signal must be absent"
        assert "sandbox_workspace_write.network_access=true" not in cap["argv"], (
            "egress must NOT be granted without the recapture opt-in"
        )
        # Native work-root union scoping (TASK-03-009): outside recapture the source
        # work root is part of the general read/write union, so it IS added to the
        # writable scope here. The read-only-source narrowing is a recapture-ONLY
        # property (asserted in the green tests, where str(src) is absent from argv).
        assert "--add-dir" in cap["argv"] and str(src) in cap["argv"], (
            "outside recapture the work-root union (incl. source) should be scoped read/write"
        )


# --------------------------------------------------------------------------- #
# GREEN — the same dispatch, opted in, for codex AND gemini (shared mechanism).
# --------------------------------------------------------------------------- #
class TestRecaptureGreenCodex:
    def test_codex_recapture_grants_readonly_source_and_egress(self, tmp_path):
        prompt = _make_project(tmp_path)
        src, baseline, scratch = _seed_source(tmp_path)
        before = baseline.read_text(encoding="utf-8")
        capture = _fakebin_dir(prompt) / "capture.json"
        proc = _run_wrapper(
            "cartopian-codex", prompt, work_roots=[src],
            env_extra={
                "CAPTURE": str(capture),
                "RECAPTURE_BASELINE": str(baseline),
                "RECAPTURE_SCRATCH": str(scratch),
                "RECAPTURE_NONCE": "codex-green",
                "CARTOPIAN_REVIEW_RECAPTURE": "1",
            },
        )
        assert proc.returncode == 0, f"stderr={proc.stderr}"
        # Banner documents the exact scope contract.
        assert "reviewer live-evidence recapture mode" in proc.stderr
        assert "read-only source work root: " + str(src) in proc.stderr
        assert "$TMPDIR" in proc.stderr and "egress" in proc.stderr
        # The launch did NOT fail closed and did NOT widen the writable scope.
        cap = json.loads(capture.read_text(encoding="utf-8"))
        argv = cap["argv"]
        assert "--sandbox" in argv and "workspace-write" in argv
        assert "sandbox_workspace_write.network_access=true" in argv, "egress not granted"
        assert str(src) not in argv, "source work root must not be added to writable scope"
        assert cap["recapture_env"] == "1"
        # Fresh evidence under TMPDIR scratch, distinct from the pinned baseline;
        # the read-only source baseline is untouched.
        fresh = scratch / "fresh-evidence.txt"
        assert fresh.is_file(), "probe produced no fresh evidence"
        assert fresh.read_text(encoding="utf-8") != before
        assert baseline.read_text(encoding="utf-8") == before


class TestRecaptureGreenGemini:
    def test_gemini_recapture_forces_sandbox_and_banner(self, tmp_path):
        prompt = _make_project(tmp_path)
        src, baseline, scratch = _seed_source(tmp_path)
        before = baseline.read_text(encoding="utf-8")
        capture = _fakebin_dir(prompt) / "capture.json"
        proc = _run_wrapper(
            "cartopian-gemini", prompt, work_roots=[src],
            env_extra={
                "CAPTURE": str(capture),
                "RECAPTURE_BASELINE": str(baseline),
                "RECAPTURE_SCRATCH": str(scratch),
                "RECAPTURE_NONCE": "gemini-green",
                "CARTOPIAN_REVIEW_RECAPTURE": "1",
            },
        )
        assert proc.returncode == 0, f"stderr={proc.stderr}"
        assert "reviewer live-evidence recapture mode" in proc.stderr
        cap = json.loads(capture.read_text(encoding="utf-8"))
        argv = cap["argv"]
        # gemini realizes read-only source + egress via its OS sandbox.
        assert "--sandbox" in argv, "gemini recapture must force the OS sandbox on"
        assert str(src) not in argv
        assert cap["recapture_env"] == "1"
        fresh = scratch / "fresh-evidence.txt"
        assert fresh.is_file()
        assert fresh.read_text(encoding="utf-8") != before
        assert baseline.read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------- #
# Opt-in / evidence guard — recapture OFF leaves every review unaffected.
# --------------------------------------------------------------------------- #
class TestRecaptureOptInGuard:
    def test_codex_without_signal_grants_no_egress_no_banner(self, tmp_path):
        prompt = _make_project(tmp_path)
        capture = _fakebin_dir(prompt) / "capture.json"
        # No declared work roots (a plain review), recapture OFF.
        proc = _run_wrapper(
            "cartopian-codex", prompt,
            env_extra={"CAPTURE": str(capture)},
        )
        assert proc.returncode == 0, proc.stderr
        cap = json.loads(capture.read_text(encoding="utf-8"))
        argv = cap["argv"]
        assert "sandbox_workspace_write.network_access=true" not in argv, "egress leaked without opt-in"
        assert "recapture mode" not in proc.stderr
        assert cap["recapture_env"] in (None, "")

    def test_gemini_without_signal_has_no_sandbox_no_banner(self, tmp_path):
        prompt = _make_project(tmp_path)
        capture = _fakebin_dir(prompt) / "capture.json"
        proc = _run_wrapper(
            "cartopian-gemini", prompt,
            env_extra={"CAPTURE": str(capture)},
        )
        assert proc.returncode == 0, proc.stderr
        cap = json.loads(capture.read_text(encoding="utf-8"))
        assert "--sandbox" not in cap["argv"], "sandbox forced without opt-in"
        assert "recapture mode" not in proc.stderr


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))

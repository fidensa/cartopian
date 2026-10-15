"""Staged report publication for activated Claude handoffs.

``cli/claude_publication.py`` ports the non-Claude wrappers' publication route
to ``cartopian-claude``: a private staging directory that sandboxed shell
commands may write, published through the mediated writer after exit. These
tests pin the helper, the sandbox/Stop-hook bindings the settings helper
derives from it, and the Stop hook's acceptance of a staged report.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from cli import claude_launch_settings, claude_publication, claude_stop_hook
from cli.work_access import WorkAccessError

REPO_ROOT = Path(__file__).resolve().parents[2]

_ROLES = (
    "[roles.coder]\n"
    'description = "Implements tasks."\n'
    'grants = ["coder-like"]\n\n'
    "[roles.reviewer]\n"
    'description = "Reviews tasks."\n'
    'grants = ["reviewer-like"]\n\n'
    "[roles.reader]\n"
    'description = "Reads only."\n'
    'grants = ["read:prompts"]\n\n'
)

COMPLETE_REPORT = """# Completion report

Status: complete

## Identity

- Report path: reports/REPORT-01-001.md

## Completion evidence

Ran the suite in the foreground; all green.

## Remaining risks

None.

## Ready to close

yes
"""


def _project(tmp_path: Path, *, gated: bool = True) -> tuple[Path, Path]:
    project = tmp_path / "project"
    work_root = tmp_path / "product"
    home = tmp_path / "home"
    for directory in (project / "prompts", project / "reports", project / "reviews", work_root):
        directory.mkdir(parents=True)
    registry = home / ".cartopian" / "projects.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps([{"id": "demo", "path": str(project)}]), encoding="utf-8")
    roles = _ROLES if gated else '[roles.coder]\ndescription = "Implements tasks."\n'
    project.joinpath("cartopian.toml").write_text(
        "[project]\n"
        'id = "demo"\n'
        'name = "Demo"\n'
        'project_schema_version = "v0.14.0"\n'
        'work_roots = ["product"]\n\n' + roles,
        encoding="utf-8",
    )
    project.joinpath("cartopian.local.toml").write_text(
        f'[work_roots]\nproduct = "{work_root}"\n', encoding="utf-8"
    )
    return project, home


def _env(project: Path, home: Path, role: str = "coder", *, report: str = "REPORT-01-001.md",
         variant: str | None = "task") -> dict[str, str]:
    env = {
        "HOME": str(home),
        "CARTOPIAN_ROLE": role,
        "CARTOPIAN_EXPECTED_REPORT_PATH": str(project / "reports" / report),
    }
    if variant:
        env["CARTOPIAN_EXPECTED_REPORT_VARIANT"] = variant
    return env


@pytest.fixture
def staged(tmp_path):
    """A prepared staging directory for the demo coder, removed afterwards."""
    project, home = _project(tmp_path)
    env = _env(project, home)
    prepared = claude_publication.prepare(project, env)
    assert prepared is not None
    directory, text = prepared
    try:
        yield project, home, env, directory, text
    finally:
        shutil.rmtree(directory, ignore_errors=True)


# --- prepare -----------------------------------------------------------------


def test_prepare_creates_a_private_directory_below_the_system_temp_root(staged):
    _project_dir, _home, _env_map, directory, text = staged
    path = Path(directory)
    assert path.parent == Path(claude_publication.publication_root())
    assert path.name.startswith(claude_publication.DIRECTORY_PREFIX)
    assert os.path.realpath(directory) == directory
    assert oct(path.stat().st_mode & 0o777) == "0o700"
    assert "\n" not in text
    assert str(path / "report.md") in text
    assert "REPORT-01-001.md" in text
    assert "review.md" not in text
    assert "not the Write or Edit tool" in text


def test_prepare_names_the_review_candidate_for_a_review_slot(tmp_path):
    project, home = _project(tmp_path)
    env = _env(project, home, "reviewer", report="REPORT-01-001-review.md", variant="review")
    prepared = claude_publication.prepare(project, env)
    assert prepared is not None
    directory, text = prepared
    try:
        assert str(Path(directory) / "report.md") in text
        assert str(Path(directory) / "review.md") in text
        assert "REVIEW-01-001.md" in text
    finally:
        shutil.rmtree(directory)


def test_prepare_infers_the_variant_a_manual_launch_omits(tmp_path):
    project, home = _project(tmp_path)
    prepared = claude_publication.prepare(project, _env(project, home, variant=None))
    assert prepared is not None
    shutil.rmtree(prepared[0])


@pytest.mark.parametrize(
    "case",
    ["no-report-slot", "no-write-reports", "ungated", "variant-mismatch"],
)
def test_prepare_stages_nothing_or_refuses_outside_the_contract(tmp_path, case):
    project, home = _project(tmp_path, gated=case != "ungated")
    if case == "no-report-slot":
        env = {"HOME": str(home), "CARTOPIAN_ROLE": "coder"}
    elif case == "no-write-reports":
        env = _env(project, home, "reader")
    elif case == "ungated":
        env = _env(project, home)
    else:
        env = _env(project, home, variant="review")
        with pytest.raises(WorkAccessError, match="variant disagrees"):
            claude_publication.prepare(project, env)
        return
    assert claude_publication.prepare(project, env) is None


def test_prepare_refuses_a_report_slot_outside_the_project_reports_directory(tmp_path):
    project, home = _project(tmp_path)
    env = _env(project, home)
    env["CARTOPIAN_EXPECTED_REPORT_PATH"] = str(project / "STATE.md")
    with pytest.raises(WorkAccessError, match="canonical dispatch-bound report slot"):
        claude_publication.prepare(project, env)


# --- publish / discard -------------------------------------------------------


def test_publish_lands_the_staged_bytes_at_the_slot_and_removes_the_directory(staged):
    project, _home, env, directory, _text = staged
    Path(directory, "report.md").write_text(COMPLETE_REPORT, encoding="utf-8")
    claude_publication.publish(project, directory, env)
    assert (project / "reports" / "REPORT-01-001.md").read_text(encoding="utf-8") == COMPLETE_REPORT
    assert not Path(directory).exists()


def test_publish_without_a_candidate_leaves_the_slot_untouched(staged):
    project, _home, env, directory, _text = staged
    claude_publication.publish(project, directory, env)
    assert not (project / "reports" / "REPORT-01-001.md").exists()
    assert not Path(directory).exists()


def test_publish_replaces_a_report_the_assignee_also_wrote_directly(staged):
    project, _home, env, directory, _text = staged
    slot = project / "reports" / "REPORT-01-001.md"
    slot.write_text("earlier direct write\n", encoding="utf-8")
    Path(directory, "report.md").write_text(COMPLETE_REPORT, encoding="utf-8")
    claude_publication.publish(project, directory, env)
    assert slot.read_text(encoding="utf-8") == COMPLETE_REPORT


@pytest.mark.parametrize("alias", ["symlink", "directory", "oversized", "binary"])
def test_publish_refuses_aliased_or_unbounded_candidates_and_still_cleans_up(staged, alias):
    from cli.native_work_sandbox import MAX_PUBLICATION

    project, _home, env, directory, _text = staged
    candidate = Path(directory) / "report.md"
    protected = project / "STATE.md"
    protected.write_text("protected", encoding="utf-8")
    if alias == "symlink":
        candidate.symlink_to(protected)
    elif alias == "directory":
        candidate.mkdir()
    elif alias == "oversized":
        with candidate.open("wb") as stream:
            stream.truncate(MAX_PUBLICATION + 1)
    else:
        candidate.write_bytes(b"\xff\xfe not utf-8")
    with pytest.raises((OSError, WorkAccessError, UnicodeDecodeError)):
        claude_publication.publish(project, directory, env)
    assert not (project / "reports" / "REPORT-01-001.md").exists()
    assert not Path(directory).exists()


def test_publish_publishes_the_review_before_the_completion_signal(tmp_path):
    project, home = _project(tmp_path)
    env = _env(project, home, "reviewer", report="REPORT-01-001-review.md", variant="review")
    directory, _text = claude_publication.prepare(project, env)
    try:
        Path(directory, "review.md").write_text("# Review\n\nbody\n", encoding="utf-8")
        Path(directory, "report.md").write_text("# Review report\n", encoding="utf-8")
        claude_publication.publish(project, directory, env)
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    assert (project / "reviews" / "REVIEW-01-001.md").read_text(encoding="utf-8") == "# Review\n\nbody\n"
    assert (project / "reports" / "REPORT-01-001-review.md").read_text(encoding="utf-8") == "# Review report\n"


@pytest.mark.parametrize("shape", ["foreign-parent", "foreign-name", "symlink", "mode"])
def test_discard_and_publish_refuse_directories_the_helper_did_not_stage(tmp_path, shape, staged):
    project, _home, env, directory, _text = staged
    root = Path(claude_publication.publication_root())
    if shape == "foreign-parent":
        target = tmp_path / (claude_publication.DIRECTORY_PREFIX + "x")
        target.mkdir(mode=0o700)
    elif shape == "foreign-name":
        target = Path(directory).with_name("other-" + Path(directory).name)
        target.mkdir(mode=0o700)
    elif shape == "symlink":
        target = root / (claude_publication.DIRECTORY_PREFIX + "link-" + Path(directory).name[-8:])
        target.symlink_to(directory, target_is_directory=True)
    else:
        target = Path(directory)
        target.chmod(0o755)
    try:
        with pytest.raises(WorkAccessError):
            claude_publication.discard(str(target))
        with pytest.raises(WorkAccessError):
            claude_publication.publish(project, str(target), env)
        assert os.path.lexists(target)
        assert Path(directory).exists()
    finally:
        if target.is_symlink():
            target.unlink()
        elif target != Path(directory):
            shutil.rmtree(target, ignore_errors=True)


def test_discard_removes_a_staged_directory(staged):
    _project_dir, _home, _env_map, directory, _text = staged
    Path(directory, "report.md").write_text("draft", encoding="utf-8")
    claude_publication.discard(directory)
    assert not Path(directory).exists()


def test_cli_prepare_prints_directory_then_instructions(tmp_path, capsys):
    project, home = _project(tmp_path)
    env = _env(project, home)
    os.environ.update(env)
    try:
        assert claude_publication.main(["prepare", "--project-dir", str(project)]) == 0
    finally:
        for key in env:
            os.environ.pop(key, None)
    out = capsys.readouterr().out
    directory, text = out.rstrip("\n").split("\n", 1)
    try:
        assert Path(directory).is_dir()
        assert text.startswith("Cartopian staged publication instructions:")
    finally:
        shutil.rmtree(directory, ignore_errors=True)


# --- settings bindings -------------------------------------------------------


def _settings(project: Path, env: dict[str, str]) -> dict:
    return claude_launch_settings.build_settings(
        REPO_ROOT,
        windows=False,
        project_dir=project,
        include_capability=True,
        include_completion=True,
        environ=env,
    )


def test_settings_admit_the_staged_directory_and_bind_the_stop_hook(staged):
    project, _home, env, directory, _text = staged
    settings = _settings(project, {**env, "CARTOPIAN_PUBLICATION_DIR": directory})
    filesystem = settings["sandbox"]["filesystem"]
    assert directory in filesystem["allowWrite"]
    assert str(project.resolve()) in filesystem["denyWrite"]
    assert directory not in filesystem["denyWrite"]
    stop_args = settings["hooks"]["Stop"][0]["hooks"][0]["args"]
    assert stop_args[stop_args.index("--staged-report") + 1] == os.path.join(directory, "report.md")
    assert stop_args[stop_args.index("--expected-report") + 1] == env["CARTOPIAN_EXPECTED_REPORT_PATH"]


def test_settings_without_a_staged_directory_are_unchanged(tmp_path):
    project, home = _project(tmp_path)
    settings = _settings(project, _env(project, home))
    assert "allowWrite" not in settings["sandbox"]["filesystem"] or all(
        claude_publication.DIRECTORY_PREFIX not in path
        for path in settings["sandbox"]["filesystem"]["allowWrite"]
    )
    assert "--staged-report" not in settings["hooks"]["Stop"][0]["hooks"][0]["args"]


@pytest.mark.parametrize("shape", ["foreign-parent", "mode", "missing", "symlink"])
def test_settings_refuse_a_publication_directory_the_wrapper_did_not_stage(tmp_path, shape, staged):
    project, _home, env, directory, _text = staged
    cleanup = None
    if shape == "foreign-parent":
        bogus = tmp_path / (claude_publication.DIRECTORY_PREFIX + "x")
        bogus.mkdir(mode=0o700)
        value = str(bogus)
    elif shape == "mode":
        Path(directory).chmod(0o755)
        value = directory
    elif shape == "missing":
        value = str(Path(directory).with_name(Path(directory).name + "-missing"))
    else:
        link = Path(claude_publication.publication_root()) / (
            claude_publication.DIRECTORY_PREFIX + "link-" + Path(directory).name[-8:]
        )
        link.symlink_to(directory, target_is_directory=True)
        cleanup = link
        value = str(link)
    try:
        with pytest.raises(claude_launch_settings.SettingsError):
            _settings(project, {**env, "CARTOPIAN_PUBLICATION_DIR": value})
    finally:
        if cleanup is not None:
            cleanup.unlink()


# --- Stop hook ---------------------------------------------------------------


def _payload() -> dict:
    return {"session_id": "sess-staged", "hook_event_name": "Stop", "stop_hook_active": False}


def test_stop_hook_allows_a_complete_staged_report(tmp_path):
    report = tmp_path / "project" / "reports" / "REPORT-01-001.md"
    report.parent.mkdir(parents=True)
    staged_report = tmp_path / "staging" / "report.md"
    staged_report.parent.mkdir()
    staged_report.write_text(COMPLETE_REPORT, encoding="utf-8")
    env = {
        claude_stop_hook.REPORT_ENV: str(report),
        claude_stop_hook.VARIANT_ENV: "task",
        claude_stop_hook.STAGED_REPORT_ENV: str(staged_report),
    }
    decision = claude_stop_hook.evaluate(_payload(), environ=env, tmpdir=str(tmp_path / "state"))
    assert decision.action == "allow"


def test_stop_hook_block_names_the_staging_path_and_its_missing_elements(tmp_path):
    report = tmp_path / "project" / "reports" / "REPORT-01-001.md"
    report.parent.mkdir(parents=True)
    staged_report = tmp_path / "staging" / "report.md"
    staged_report.parent.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    env = {
        claude_stop_hook.REPORT_ENV: str(report),
        claude_stop_hook.VARIANT_ENV: "task",
        claude_stop_hook.STAGED_REPORT_ENV: str(staged_report),
    }
    absent = claude_stop_hook.evaluate(_payload(), environ=env, tmpdir=str(state))
    assert absent.action == "block"
    assert f"Report path: {report}" in absent.reason
    assert f"Staging path: {staged_report}" in absent.reason
    assert "single" in absent.reason and "shell command" in absent.reason
    assert "has not been written" in absent.reason

    staged_report.write_text("# Completion report\n\n## Identity\n\n- Report path: x\n", encoding="utf-8")
    partial = claude_stop_hook.evaluate(_payload(), environ=env, tmpdir=str(state))
    assert partial.action == "block"
    assert "does not parse as a complete report" in partial.reason
    assert "Missing from the current file" in partial.reason
    assert "Remaining risks" in partial.reason


def test_stop_hook_staged_binding_is_argv_only(tmp_path, monkeypatch, capsys):
    import io
    import sys

    report = tmp_path / "project" / "reports" / "REPORT-01-001.md"
    report.parent.mkdir(parents=True)
    staged_report = tmp_path / "staging" / "report.md"
    staged_report.parent.mkdir()
    staged_report.write_text(COMPLETE_REPORT, encoding="utf-8")
    monkeypatch.setenv(claude_stop_hook.STAGED_REPORT_ENV, str(staged_report))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(_payload()).encode())))
    assert claude_stop_hook.main([
        "--expected-report", str(report), "--expected-variant", "task",
        "--max-blocks", "3", "--state-dir", str(tmp_path / "state"),
    ]) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "block"

    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(_payload()).encode())))
    assert claude_stop_hook.main([
        "--expected-report", str(report), "--expected-variant", "task",
        "--max-blocks", "3", "--state-dir", str(tmp_path / "state"),
        "--staged-report", str(staged_report),
    ]) == 0
    assert capsys.readouterr().out == ""

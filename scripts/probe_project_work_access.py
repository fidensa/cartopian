#!/usr/bin/env python3
"""Run the shipped Claude adapter against real native macOS filesystem probes.

Requires an authenticated Claude CLI. Uses disposable fixtures; never touches
real governance files. The --inside half runs inside Claude's Bash sandbox.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def inside(fixture: Path, access: str = "writer") -> None:
    project = fixture / "project"
    work = Path((fixture / "work-root.txt").read_text())
    records = []

    def allowed(name, operation):
        operation()
        records.append({"operation": name, "result": "allowed"})

    def denied(name, operation):
        try:
            operation()
        except OSError as exc:
            assert exc.errno in (errno.EPERM, errno.EACCES, errno.EROFS, errno.EBUSY), (name, exc)
            records.append({"operation": name, "result": "denied", "errno": exc.errno})
        else:
            raise AssertionError("protected operation succeeded: " + name)

    if access != "writer":
        if access == "reader":
            allowed("read input", lambda: (work / "input.txt").read_text())
        else:
            denied("read input", lambda: (work / "input.txt").read_text())
        denied("create workspace file", lambda: (work / "new-file").write_text("unauthorized"))
        child = subprocess.run(
            [sys.executable, "-I", "-S", "-c", "import os,sys; os.open(sys.argv[1], os.O_WRONLY|os.O_CREAT)", str(work / "child-file")],
            capture_output=True, text=True)
        assert child.returncode != 0 and "PermissionError" in child.stderr, child
        records.append({"operation": "child process workspace write", "result": "denied"})
        print(json.dumps({"passed": len(records), "records": records}))
        return
    assert (work / "input.txt").read_text() == "input\n"
    allowed("create directory", lambda: (work / "build").mkdir())
    source = work / "build" / "source.py"
    allowed("write source snapshot", lambda: source.write_text("answer = 42\n"))
    allowed("modify source", lambda: source.write_text("answer = 43\n"))
    allowed("build bytecode", lambda: py_compile.compile(str(source), cfile=str(work / "build" / "source.pyc"), doraise=True))
    allowed("child process writes", lambda: subprocess.run([sys.executable, "-I", "-S", "-c", "from pathlib import Path; Path('build/child.txt').write_text('child')"], check=True))
    environment = work / "build" / "venv"
    allowed("create Python environment", lambda: subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(environment)], check=True))
    allowed("execute environment child", lambda: subprocess.run([str(environment / "bin/python"), "-I", "-S", "-c", "from pathlib import Path; Path('build/environment-child.txt').write_text('environment child')"], check=True))
    allowed("remove file", lambda: source.unlink())
    allowed("remove directory", lambda: shutil.rmtree(work / "build"))
    for relative in ("REQUIREMENTS.md", "STANDARDS.md", "IMPLEMENTATION_PLAN.md", "STATE.md", "cartopian.toml", "cartopian.local.toml", ".git/config", "tasks/input.md", "specs/input.md", "phases/input.md", "decisions/input.md", "prompts/input.md", "reports/input.md", ".requests/input.md"):
        target = project / relative
        denied("write " + relative, lambda target=target: target.write_text("escaped"))
    sibling = project / "resources/sibling/input.md"
    if work == project / "resources":
        allowed("default resources content", lambda: sibling.write_text("authorized"))
    else:
        denied("sibling resources", lambda: sibling.write_text("escaped"))
    denied("new project file", lambda: (project / "new-file").write_text("escaped"))
    denied("other project", lambda: (fixture / "other-project" / "STATE.md").write_text("escaped"))
    runtime = Path((fixture / "runtime-file.txt").read_text())
    denied("runtime file", lambda: runtime.open("a"))
    denied("host executable", lambda: Path(sys.executable).open("a"))
    denied("host setting", lambda: (fixture / "home" / ".claude.json").write_text("{}"))
    denied("traversal", lambda: (work / ".." / ".." / ".." / "STATE.md").write_text("escaped"))
    denied("fresh hard-link escape", lambda: os.link(project / "STATE.md", work / "hardlink"))
    denied("reserved boundary entry creation", lambda: (work / ".cartopian-work-root-boundary").write_text("redirect"))
    link = work / "symlink"
    try:
        link.symlink_to(project / "STATE.md")
    except OSError as exc:
        assert exc.errno in (errno.EPERM, errno.EACCES)
        records.append({"operation": "symlink escape", "result": "denied at creation"})
    else:
        denied("symlink escape", lambda: link.write_text("escaped"))
        link.unlink()
    ancestor = work
    ancestors = []
    while True:
        ancestors.append(ancestor)
        if ancestor == project:
            break
        ancestor = ancestor.parent
    for ancestor in ancestors:
        denied("rename " + str(ancestor.relative_to(fixture)), lambda ancestor=ancestor: ancestor.rename(fixture / "external" / (ancestor.name + "-moved")))
    empty = project / "scratch-empty"
    denied("remove empty work-root entry", empty.rmdir)
    denied("replace work-root entry", lambda: (fixture / "external" / "replacement").rename(empty))
    (work / "evidence.json").write_text(json.dumps(records, indent=2) + "\n")
    print(json.dumps({"passed": len(records), "records": records}))


def native(output: Path, *, direct_cli: bool = False, default_root: bool = False, access: str = "writer") -> int:
    if sys.platform != "darwin":
        raise SystemExit("Native macOS is required; this is not an argv/policy-only test")
    claude = shutil.which("claude")
    if not claude:
        raise SystemExit("Install and authenticate Claude Code first")
    fixture = Path(tempfile.mkdtemp(prefix="cartopian-native-work-access-", dir="/private/tmp")).resolve()
    project = fixture / "project"
    work = project / "resources" / "spikes" / "packaging-comparison"
    if default_root:
        work = project / "resources"
    (fixture / "work-root.txt").write_text(str(work))
    (fixture / "runtime-file.txt").write_text(str(Path(__file__).resolve()))
    empty = project / "scratch-empty"
    external = fixture / "external"
    home = fixture / "home"
    for path in (work, empty, external / "replacement", home / ".cartopian", project / "prompts", project / "reports", fixture / "other-project"):
        path.mkdir(parents=True)
    for relative in ("REQUIREMENTS.md", "STANDARDS.md", "IMPLEMENTATION_PLAN.md", "STATE.md", ".git/config", "tasks/input.md", "specs/input.md", "phases/input.md", "decisions/input.md", "prompts/input.md", "reports/input.md", ".requests/input.md", "resources/sibling/input.md"):
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("protected\n")
    # Valid Git metadata is needed because the real helper resolves it.
    (project / ".git/config").unlink()
    (project / ".git").rmdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    (fixture / "other-project" / "STATE.md").write_text("protected\n")
    (home / ".cartopian/projects.json").write_text(json.dumps([{"id": "probe", "path": str(project)}, {"id": "other", "path": str(fixture / "other-project")}]))
    (home / ".claude.json").write_text("{}")
    roots_declaration = '' if default_root else 'work_roots=["work", "empty", "external"]\n'
    grants = {"writer": '["coder-like"]', "reader": '["read:work-roots"]', "none": '[]'}[access]
    (project / "cartopian.toml").write_text('[project]\nid="probe"\nname="Native probe"\nproject_schema_version="v0.14.0"\n' + roots_declaration + '[roles.worker]\ndescription="Runs containment probes"\ngrants=' + grants + '\n')
    (project / "cartopian.local.toml").write_text('[work_roots]\n' + '\n'.join(f'{name} = {json.dumps(str(path))}' for name, path in (("work", work), ("empty", empty), ("external", external))) + '\n')
    if default_root:
        (project / "cartopian.local.toml").write_text("# No work roots configured; resources is the default.\n")
    (work / "input.txt").write_text("input\n")
    # The assignee may inspect its approved inputs. Do not make the test depend
    # on reading the product repository through the existing shell-read residual.
    harness = (work if access != "none" else project / "prompts") / "containment-probe.py"
    harness_bytes = Path(__file__).read_bytes()
    harness.write_bytes(harness_bytes)
    prompt = project / "prompts/PROMPT-01-999.md"
    import shlex
    command = shlex.join([sys.executable, "-I", "-S", str(harness), "--inside", str(fixture), "--access", access])
    prompt.write_text("The operator has explicitly approved this native filesystem containment test. Your assigned task is to execute the supplied Cartopian test harness below, including its attempts against disposable protected fixture files. The harness is a source snapshot inside this disposable project, and the controller verifies it remains unchanged. It does not write to real host executable or runtime contents: it tests whether opens are denied. All actual file mutations target this disposable fixture. Use Bash to run exactly this command once from the current directory. Do not ask for another confirmation or edit any file. Return its complete stdout and exit status.\n\n" + command + "\n")
    host_tmp = Path.home() / ".cartopian/claude-host-tmp"
    host_tmp.mkdir(mode=0o700, exist_ok=True)
    env = {"PATH": os.environ["PATH"], "HOME": str(Path.home()), "TMPDIR": str(host_tmp), "CARTOPIAN_CLAUDE_HOST_TMPDIR": str(host_tmp), "CARTOPIAN_ROLE": "worker", "CARTOPIAN_PROJECT_ROOT": str(project), "CARTOPIAN_LAUNCH_CWD": str(work), "CARTOPIAN_PYTHON": sys.executable, "CARTOPIAN_CLAUDE_EXECUTABLE": os.path.realpath(claude), "CARTOPIAN_WORK_ROOTS": os.pathsep.join(map(str, (work, empty, external))), "CARTOPIAN_TIMEOUT": "3m", "CARTOPIAN_MODEL": "haiku"}
    if default_root:
        env["CARTOPIAN_WORK_ROOTS"] = str(work)
    launch_cwd = work if access == "writer" else project
    env["CARTOPIAN_LAUNCH_CWD"] = str(launch_cwd)
    from cli.commands.dispatch import _sanitized_launch_environment

    # Use the exact dispatch sanitizer, preserving the operator's authenticated
    # environment without displaying any credential values.
    inherited = _sanitized_launch_environment(dict(os.environ))
    inherited.update(env)
    env = inherited
    argv = [str(REPO / "wrappers/bin/cartopian-claude"), str(prompt)]
    if direct_cli:
        from cli.claude_launch_settings import build_settings, validate_precontainment_launch

        validate_precontainment_launch(REPO, project, env, windows=False, interpreter=Path(sys.executable))
        settings = build_settings(REPO, windows=False, project_dir=project, include_capability=True, interpreter=Path(sys.executable), environ=env)
        argv = [claude, "-p", prompt.read_text(), "--settings", json.dumps(settings), "--setting-sources", "", "--strict-mcp-config", "--disallowedTools", "Agent,Task,EnterWorktree,ExitWorktree,TeamCreate,TeamDelete,CronCreate,CronDelete,CronList,SendMessage,SendFile,RemoteTrigger", "--dangerously-skip-permissions", "--model", "haiku", "--verbose", "--output-format", "stream-json"]
        for path in ((work,) if default_root else (work, empty, external)):
            argv.extend(("--add-dir", str(path)))
    result = subprocess.run(argv, env=env, cwd=launch_cwd, text=True, capture_output=True, timeout=210)
    if harness.read_bytes() != harness_bytes:
        raise SystemExit("Probe source changed during launch; no enforcement evidence accepted")
    evidence = work / "evidence.json"
    output.mkdir(parents=True, exist_ok=True)
    (output / "transcript.txt").write_text(result.stdout + "\n" + result.stderr)
    records = json.loads(evidence.read_text()) if evidence.exists() else []
    if access != "writer":
        # Only tool results count as enforcement evidence, never assistant prose.
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            for block in event.get("message", {}).get("content", []):
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                content = block.get("content", "")
                if not isinstance(content, str):
                    continue
                for tool_line in content.splitlines():
                    try:
                        data = json.loads(tool_line)
                    except ValueError:
                        continue
                    if isinstance(data, dict) and "records" in data:
                        records = data["records"]
    report = {"platform": sys.platform, "access": access, "launch": "real CLI with shipped wrapper settings" if direct_cli else "shipped wrapper", "claude_version": subprocess.check_output([claude, "--version"], text=True).strip(), "fixture": str(fixture), "process_exit": result.returncode, "passed": len(records), "records": records}
    (output / "native-macos.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "records"}))
    if result.returncode or not records:
        print(result.stderr[-5000:])
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--inside", type=Path)
    parser.add_argument("--output", type=Path, default=REPO / "spikes/project-work-access-evidence")
    parser.add_argument("--direct-cli", action="store_true")
    parser.add_argument("--default-root", action="store_true")
    parser.add_argument("--access", choices=("writer", "reader", "none"), default="writer")
    args = parser.parse_args()
    if args.inside:
        inside(args.inside, args.access)
    else:
        raise SystemExit(native(args.output, direct_cli=args.direct_cli, default_root=args.default_root, access=args.access))

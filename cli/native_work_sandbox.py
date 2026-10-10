"""Native macOS process containment for the non-Claude shipped wrappers.

The controller is outside Seatbelt; the entire agent (not just its shell tool)
and every child inherit a deny-default profile. Only work grants and a fresh
private state directory permit writes. Publication consumes bytes at fixed
dispatch-bound slots through the existing mediated writer, never agent paths.
No caller-selected profile, extra writable path or unavailable-sandbox retry.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Mapping

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cli.work_access import SHIPPED_ADAPTERS, WorkAccess, WorkAccessError, within, wrapper_preflight

SANDBOX = "/usr/bin/sandbox-exec"
MAX_PUBLICATION = 2 * 1024 * 1024
METADATA_NAMES = ("git", "codex", "claude", "cartopian", "gemini", "opencode", "hermes")


def git_environment(environ: Mapping[str, str]) -> dict[str, str]:
    # No mapped PATH executable, global Git hook/configuration or inherited
    # loader runs in the controller's read-only metadata discovery.
    return {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": environ.get("HOME", str(Path.home())),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_OPTIONAL_LOCKS": "0"}


def metadata_paths(access: WorkAccess) -> tuple[str, ...]:
    from cli.claude_launch_settings import project_git_protected_roots

    paths = []
    def walk_error(exc):
        raise WorkAccessError("cannot inspect protected work metadata: " + str(exc))
    for root in access.mapped:
        for directory, subdirs, filenames in os.walk(root, followlinks=False, onerror=walk_error):
            protected_directory = any(within(directory, path) for path in paths)
            for name in (*subdirs, *filenames):
                candidate = Path(directory) / name
                reserved = name.casefold() in {"." + item for item in METADATA_NAMES}
                if protected_directory or reserved:
                    if candidate.is_symlink():
                        raise WorkAccessError("protected work metadata must not contain symbolic links: " + str(candidate))
                if reserved:
                    paths.append(str(candidate))
                    if name.casefold() == ".git" and candidate.is_file():
                        paths.extend(project_git_protected_roots(candidate.parent,
                                                                environ=git_environment(os.environ)))
    for path in dict.fromkeys(paths):
        if Path(path).is_dir():
            for directory, subdirs, filenames in os.walk(path, followlinks=False, onerror=walk_error):
                for name in (*subdirs, *filenames):
                    candidate = Path(directory) / name
                    if candidate.is_symlink():
                        raise WorkAccessError("protected work metadata must not contain symbolic links: " + str(candidate))
    return tuple(dict.fromkeys(paths))


def literal(value: str) -> str:
    if any(ord(c) < 32 or ord(c) == 127 for c in value) or len(value.encode()) > 900:
        raise WorkAccessError("path cannot be represented in a native sandbox literal")
    return json.dumps(value, ensure_ascii=False)


def backend_executable(adapter: str, access: WorkAccess, environ: Mapping[str, str]) -> str:
    executable = shutil.which(adapter, path=environ.get("PATH"))
    if not executable:
        raise WorkAccessError("agent backend not found on PATH: " + adapter)
    if any(within(os.path.realpath(executable), root) for root in access.mapped):
        raise WorkAccessError("backend executable must be installed outside governed work roots")
    return executable


def validate_boundary(access: WorkAccess, environ: Mapping[str, str]) -> None:
    from cli.claude_launch_settings import (
        enforcement_protected_roots, filesystem_path_is_within, project_git_protected_roots,
        registered_foreign_work_roots, registered_project_roots, validate_foreign_work_root_aliases,
        validate_writable_work_root_hardlinks,
    )

    if sys.platform != "darwin" or not Path(SANDBOX).is_file():
        raise WorkAccessError("native macOS Seatbelt is unavailable; no unsandboxed fallback")
    runtime = str(Path(__file__).resolve().parents[1])
    host_home = Path(environ.get("HOME", str(Path.home())))
    protected = (*enforcement_protected_roots(Path(runtime), environ, windows=False),
                 *project_git_protected_roots(Path(access.project), environ=git_environment(environ)),
                 *(str(host_home / name) for name in (".codex", ".claude", ".gemini", ".hermes", ".config", "Library")))
    for root in access.mapped:
        literal(root)
        if not Path(root).is_dir() or os.path.abspath(root) != os.path.realpath(root):
            raise WorkAccessError("work roots must be existing canonical directories: " + root)
        if any(filesystem_path_is_within(root, path) or filesystem_path_is_within(path, root) for path in protected):
            raise WorkAccessError("work roots overlap the containment runtime, configuration, interpreter or host state")
        if within(access.project, root):
            raise WorkAccessError("a work root cannot contain its governing project")
        for project in registered_project_roots(environ):
            if project != access.project and (filesystem_path_is_within(root, project) or filesystem_path_is_within(project, root)):
                raise WorkAccessError("work roots overlap another governed project: " + project)
    foreign = registered_foreign_work_roots(environ, active_project=access.project, active_work_roots=access.mapped)
    validate_foreign_work_root_aliases(access.mapped, foreign)
    # Shared complete-inode scan: a pre-existing writable hard link cannot be
    # repaired by a pathname sandbox. For unreadable roots the same scan must
    # include only those roots, so an outside readable alias is refused too.
    denied_reads = tuple(root for root in access.mapped if root not in access.backend_readable)
    metadata = metadata_paths(access)
    validate_writable_work_root_hardlinks(access.backend_writable, protected_roots=metadata)
    validate_writable_work_root_hardlinks(denied_reads)
    # macOS supports nullfs mounts as well as ordinary volumes. Reject any
    # mounted view within a permission-bearing root, including the root itself.
    mounted = subprocess.run(["/sbin/mount"], capture_output=True, text=True, check=True,
                             env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}).stdout
    for line in mounted.splitlines():
        match = re.fullmatch(r"(.+) on (.+) \([^\n]+\)", line)
        if not match:
            raise WorkAccessError("cannot decode native mount table; containment refused")
        mount = os.path.realpath(match[2])
        if any(within(mount, root) for root in access.mapped):
            raise WorkAccessError("mounted work-root aliases are not supported: " + mount)
        source = os.path.realpath(match[1]) if match[1].startswith("/") else None
        if source and not source.startswith("/dev/") and any(
            within(source, root) or within(root, source) for root in access.mapped
        ):
            raise WorkAccessError("mounted source exposes a work-root alias: " + source)
    from cli.claude_launch_settings import _capability_context, _session_roles

    resolution, _roots = _capability_context(Path(access.project), environ=environ)
    exceptions = resolution.sandbox_for(_session_roles(environ))
    if any(exceptions[key] for key in ("allow_unix_sockets", "writable_paths", "allow_local_binding", "allowed_domains")):
        raise WorkAccessError("contained process launches refuse authored network, socket, local-binding and extra writable-path exceptions")


def profile(access: WorkAccess, scratch: Path | None = None, proxy_port: int | None = None,
            *, local_listener: bool = False) -> str:
    rules = [
        "(version 1)", "(deny default)", "(allow process-exec)", "(allow process-fork)",
        "(allow process-info* (target same-sandbox))", "(allow signal (target same-sandbox))",
        "(allow sysctl-read)", "(allow file-read*)", "(allow user-preference-read)",
        # DNS and certificate verification only; no daemon, Apple Events,
        # Launch Services, Keychain mutation or remote-control IPC grant.
        '(allow mach-lookup (global-name "com.apple.system.opendirectoryd.libinfo") '
        '(global-name "com.apple.system.opendirectoryd.membership") '
        '(global-name "com.apple.mDNSResponder") '
        '(global-name "com.apple.SystemConfiguration.DNSConfiguration") '
        '(global-name "com.apple.SystemConfiguration.configd") '
        '(global-name "com.apple.TrustEvaluationAgent") '
        '(global-name "com.apple.ocspd") '
        '(global-name "com.apple.trustd.agent"))',
        '(allow system-socket (socket-domain AF_UNIX))',
        '(allow system-socket (require-all (socket-domain AF_SYSTEM) (socket-protocol 2)))',
        '(allow network-outbound (control-name "com.apple.netsrc"))',
        '(allow network-outbound (remote unix-socket (literal "/private/var/run/mDNSResponder")))',
        '(allow file-write-data (literal "/dev/null"))',
    ]
    if proxy_port is not None:
        if not 1 <= proxy_port <= 65535:
            raise WorkAccessError("invalid controller proxy port")
        rules.append(f'(allow network-outbound (remote tcp "localhost:{proxy_port}"))')
    if local_listener:
        # A runtime listener does not grant outbound access to host services.
        rules.append('(allow network-bind network-inbound (local tcp "localhost:*"))')
    writable = (*access.backend_writable, *((str(scratch),) if scratch else ()))
    # APFS commonly ignores case. Match variant spellings of enforcement
    # directories as well as their ordinary lowercase names.
    alternatives = "|".join("".join(f"[{char}{char.upper()}]" for char in name) for name in METADATA_NAMES)
    metadata = '(regex #".*/[.](' + alternatives + ')(/.*)?$")'
    for root in writable:
        exclusion = f" (require-not {metadata})" if root in access.mapped else ""
        rules.append(f"(allow file-write* (require-all (subpath {literal(root)}){exclusion}))")
        # A root is writable *content*, not a replaceable directory entry.
        # Pin every ancestor, including roots with no boundary marker present.
        for ancestor in (Path(root), *Path(root).parents):
            rules.append(f"(deny file-write-unlink file-write-create (literal {literal(str(ancestor))}))")
    for root in access.mapped:
        # Git/agent settings beneath work roots are enforcement inputs too.
        rules.append(f"(deny file-write* (require-all (subpath {literal(root)}) {metadata}))")
        rules.append(f"(deny file-write* (literal {literal(str(Path(root) / '.cartopian-work-root-boundary'))}))")
        if root not in access.backend_readable:
            rules.append(f"(deny file-read* (subpath {literal(root)}))")
    for path in metadata_paths(access):
        rules.append(f"(deny file-write* (subpath {literal(path)}))")
        # Linked-worktree Git directories may have an ordinary basename.
        # Pin their supporting entries so they cannot move out of the deny.
        for ancestor in (Path(path), *Path(path).parents):
            rules.append(f"(deny file-write-unlink file-write-create (literal {literal(str(ancestor))}))")
    if scratch:
        for name in ("publication", "prompt.md"):
            rules.append(f"(deny file-write-unlink file-write-create (literal {literal(str(scratch / name))}))")
        rules.append(f"(deny file-write* (literal {literal(str(scratch / 'prompt.md'))}))")
    return "\n".join(rules) + "\n"


def check_substrate(access: WorkAccess) -> None:
    try:
        result = subprocess.run([SANDBOX, "-p", profile(access), "/usr/bin/true"],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkAccessError("cannot start native containment backend: " + str(exc)) from exc
    if result.returncode:
        raise WorkAccessError("native containment backend refused startup: " + result.stderr.strip())


def read_candidate(directory: Path, name: str) -> bytes | None:
    """Never read a sandbox-authored pathname through an unpinned parent."""
    parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_PUBLICATION:
                raise WorkAccessError("publication candidate must be a bounded independent regular file")
            data = stream.read(MAX_PUBLICATION + 1)
            if len(data) > MAX_PUBLICATION:
                raise WorkAccessError("publication candidate exceeds size limit")
            data.decode("utf-8")
            return data
    finally:
        os.close(parent)


def publication_slots(access: WorkAccess, environ: Mapping[str, str]) -> dict[str, tuple[str, str]]:
    from cli import report_identity

    expected = environ.get("CARTOPIAN_EXPECTED_REPORT_PATH")
    if not expected:
        return {}
    path = Path(expected)
    variant = environ.get("CARTOPIAN_EXPECTED_REPORT_VARIANT")
    if path.parent != Path(access.project) / "reports" or not report_identity.REPORT_FILENAME_RE.fullmatch(path.name):
        raise WorkAccessError("publication requires a canonical dispatch-bound report slot")
    if report_identity.filename_contract_variant(path.name) != variant:
        raise WorkAccessError("publication variant disagrees with the dispatch report slot")
    if "write:reports" not in access.grants:
        return {}
    slots = {"report.md": ("report", path.name)}
    if variant in ("review", "planning-review"):
        suffix = path.name.removeprefix("REPORT-").removesuffix("-review.md") if variant == "review" else path.stem.removeprefix("REPORT-")
        slots["review.md"] = ("review", "REVIEW-" + suffix + ".md")
    return slots


def publish(access: WorkAccess, directory: Path, slots: dict[str, tuple[str, str]]) -> None:
    from cli.mediated_write import mediated_write

    # Review first, completion signal last. The existing downstream observer
    # validates schema, identities, request alignment and verdict agreement.
    for candidate in ("review.md", "report.md"):
        if candidate not in slots:
            continue
        content = read_candidate(directory, candidate)
        if content is not None:
            kind, name = slots[candidate]
            mediated_write(access.project, kind, name, content)


def backend_environment(adapter: str, scratch: Path, environ: Mapping[str, str]) -> dict[str, str]:
    """Redirect runtime state; no real host state directory becomes writable."""
    from cli.commands.dispatch import _sanitized_launch_environment

    env = _sanitized_launch_environment(dict(environ))
    home = scratch / "home"
    home.mkdir()
    for name in ("tmp", "config", "cache", "data", "state"):
        (scratch / name).mkdir()
    host_home = Path(environ.get("HOME", str(Path.home())))
    # Credentials are copied only at their known data slots; user plugins,
    # MCP servers, startup hooks and backend configuration are never cloned.
    credential_paths = {
        "codex": (".codex/auth.json",),
        "hermes": (".hermes/auth.json", ".hermes/.env", ".codex/auth.json"),
        "devin": (".local/share/devin/credentials.toml",),
        "agy": (), "opencode": (".local/share/opencode/auth.json", ".codex/auth.json"),
    }
    for relative in credential_paths[adapter]:
        source = host_home / relative
        if source.is_file():
            # .env is credential data; use the same bounded no-follow UTF-8 read.
            data = read_candidate(source.parent, source.name)
            target = (scratch / "data" / relative.removeprefix(".local/share/")) if relative.startswith(".local/share/") else home / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data or b"")
            target.chmod(0o600)
    env.update(HOME=str(home), TMPDIR=str(scratch / "tmp"), TMP=str(scratch / "tmp"),
               TEMP=str(scratch / "tmp"), XDG_CONFIG_HOME=str(scratch / "config"),
               XDG_CACHE_HOME=str(scratch / "cache"), XDG_DATA_HOME=str(scratch / "data"),
               XDG_STATE_HOME=str(scratch / "state"), CODEX_HOME=str(home / ".codex"),
               HERMES_HOME=str(home / ".hermes"), PYTHONDONTWRITEBYTECODE="1")
    # Rust's platform certificate loader otherwise needs broad Keychain IPC.
    # Use the system CA bundle; preserve an operator-selected certificate file.
    if adapter == "codex" and not env.get("CODEX_CA_CERTIFICATE") and not env.get("SSL_CERT_FILE"):
        if not Path("/etc/ssl/cert.pem").is_file():
            raise WorkAccessError("native Codex requires a readable system CA bundle or an explicit CODEX_CA_CERTIFICATE")
        env["CODEX_CA_CERTIFICATE"] = "/etc/ssl/cert.pem"
    # Profile selection cannot point Hermes back at a persistent home.
    env.pop("CARTOPIAN_HERMES_PROFILE", None)
    for directory in (home / ".codex", home / ".hermes"):
        directory.mkdir(exist_ok=True)
    if adapter == "hermes":
        # Preserve ordinary provider/model selection without importing hooks,
        # plugin configuration, external state paths or terminal overrides.
        source = host_home / ".hermes/config.yaml"
        if source.is_file():
            config = (read_candidate(source.parent, source.name) or b"").decode()
            section = re.search(r"(?m)^model:\s*\n((?:[ \t]+[^\n]*\n|\n)*)", config)
            selected = {}
            if section:
                for key in ("default", "provider", "base_url"):
                    match = re.search(r"(?m)^  " + key + r":\s*([^\n]+)$", section[1])
                    if match:
                        value = match[1].strip().strip("'\"")
                        if not re.fullmatch(r"[A-Za-z0-9_./:@+\-]+", value):
                            raise WorkAccessError("Hermes model selection needs plain scalar values for native containment")
                        selected[key] = value
            if selected:
                (home / ".hermes/config.yaml").write_text("model:\n" + "".join(
                    "  " + key + ": " + json.dumps(value) + "\n" for key, value in selected.items()))
                if selected.get("provider"):
                    env["CARTOPIAN_NATIVE_HERMES_PROVIDER"] = selected["provider"]
        if environ.get("CARTOPIAN_HERMES_PROFILE"):
            raise WorkAccessError("contained Hermes profile selection is not yet supported; no host-state fallback")
    if adapter == "opencode":
        auth_path = scratch / "data/opencode/auth.json"
        codex_path = home / ".codex/auth.json"
        if auth_path.is_file() and codex_path.is_file():
            auth = json.loads(auth_path.read_text())
            tokens = json.loads(codex_path.read_text()).get("tokens", {})
            existing = auth.get("openai", {})
            access_token = tokens.get("access_token", "")
            if existing.get("type") == "oauth" and access_token and tokens.get("refresh_token"):
                # Refresh tokens rotate. Borrow a fresher canonical Codex token
                # for an already-configured OpenAI OAuth provider, solely in
                # private state; never rotate or rewrite either host auth store.
                try:
                    claims = json.loads(base64.urlsafe_b64decode(access_token.split(".")[1] + "=="))
                    expires = int(claims["exp"]) * 1000
                except (ValueError, KeyError, IndexError):
                    expires = 0
                if expires > time.time() * 1000 and expires > existing.get("expires", 0):
                    existing.update(access=access_token, refresh=tokens["refresh_token"], expires=expires)
                    if tokens.get("account_id"):
                        existing["accountId"] = tokens["account_id"]
                    auth_path.write_text(json.dumps(auth))
    return env


def run(adapter: str, project: Path, prompt: Path, argv: list[str], *, probe: bool = False) -> int:
    from cli.native_network_proxy import network_proxy
    access = wrapper_preflight(adapter, project)
    if not access.contained or adapter == "claude":
        raise WorkAccessError("native process backend requires a contained non-Claude launch")
    if not argv:
        raise WorkAccessError("missing backend command")
    executable = shutil.which(argv[0])
    if not executable or any(within(os.path.realpath(executable), root) for root in access.mapped):
        raise WorkAccessError("backend executable must be installed outside governed work roots")
    check_substrate(access)
    with tempfile.TemporaryDirectory(prefix="cartopian-contained-", dir="/private/tmp") as raw, network_proxy() as port:
        scratch = Path(raw).resolve()
        env = backend_environment(adapter, scratch, os.environ)
        if adapter == "hermes" and "--provider" not in argv and env.get("CARTOPIAN_NATIVE_HERMES_PROVIDER"):
            argv += ["--provider", env["CARTOPIAN_NATIVE_HERMES_PROVIDER"]]
        proxy = f"http://127.0.0.1:{port}"
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env[key] = proxy
        env.update(NO_PROXY="", no_proxy="")
        slots = {} if probe else publication_slots(access, os.environ)
        candidates = scratch / "publication"
        candidates.mkdir()
        env["CARTOPIAN_PUBLICATION_DIR"] = str(candidates)
        if not probe:
            # The immutable original remains available for input/request checks.
            # Only its launch argument is replaced, never the project artifact.
            launch_prompt = scratch / "prompt.md"
            instructions = ["Cartopian native containment publication instructions:",
                            "The original assignment follows unchanged. Work-root grants are enforced by the OS.",
                            "Governance/report/review files are read-only to this process."]
            for candidate, (kind, name) in slots.items():
                instructions.append(f"Write the final {kind} bytes to {candidates / candidate}, instead of the project slot {name}. "
                                    "The outside controller will publish those bytes through Cartopian's mediated writer when you exit. "
                                    "Keep all identity fields and paths inside the artifact unchanged.")
            launch_prompt.write_text("\n".join(instructions) + "\n\n" + prompt.read_text())
            argv = [str(launch_prompt) if arg == str(prompt) else arg for arg in argv]
        argv[0] = executable
        child = subprocess.Popen([SANDBOX, "-p", profile(access, scratch, port, local_listener=adapter in ("agy", "opencode")), *argv], env=env,
                                 start_new_session=True)
        def signal_group(number, _frame=None):
            try:
                os.killpg(child.pid, number)
            except ProcessLookupError:
                pass
        old_handlers = {}
        def interrupted(number, _frame):
            signal_group(number)
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                signal_group(signal.SIGKILL)
        for sig in (signal.SIGTERM, signal.SIGINT):
            old_handlers[sig] = signal.signal(sig, interrupted)
        try:
            result = child.wait()
            # Stop remaining descendants before consuming their candidate bytes.
            signal_group(signal.SIGKILL)
            publish(access, candidates, slots)
            if os.environ.get("CARTOPIAN_NATIVE_PROBE") == "1":
                captured = read_candidate(scratch / "tmp", "probe.json")
                if captured is not None:
                    records = json.loads(captured)
                    if not isinstance(records, list) or any(
                        not isinstance(item, dict) or set(item) - {"operation", "result", "errno"}
                        or not isinstance(item.get("operation"), str)
                        or item.get("result") not in ("allowed", "denied", "denied at creation")
                        for item in records
                    ):
                        raise WorkAccessError("invalid native probe records")
                    print("CARTOPIAN_NATIVE_PROBE=" + json.dumps(records), flush=True)
            return result if result >= 0 else 128 - result
        finally:
            signal_group(signal.SIGKILL)
            child.wait()
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", required=True, choices=SHIPPED_ADAPTERS[1:])
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--prompt", required=True, type=Path)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        return run(args.adapter, args.project, args.prompt, args.command[1:] if args.command[:1] == ["--"] else args.command, probe=args.probe)
    except Exception as exc:
        print("[guard] native process containment: " + str(exc), file=sys.stderr)
        if os.environ.get("CARTOPIAN_NATIVE_PROBE") == "1":
            import traceback

            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Unit coverage for the `hermes-cli` registration format.

Hermes is the first client whose registration adapter executes a host binary:
writes go through `hermes config set` (per-key, `enabled: true` last; repairs
of an existing owned entry write `enabled: false` first), reads through one
whole-entry `hermes config get --json`, uninstall through a promptless
`hermes config unset` — never through file merges, because Hermes owns YAML
fidelity. These tests pin the convergent write sequence (D4), the five-way
reader verdict (including the unmanaged-keys refusal: Hermes prefers `url`
over `command`, so an entry carrying launch-affecting extras is never
current), the plan-time destination/executable/version freeze (D10), the
per-operation resolution scope that carries one verified profile home from
verification through mutation, fail-closed `config path` discovery, the
closed uninstaller dispatch, and the subprocess-hygiene posture (fixed
timeout, stdin closed, capture bounded while the child runs, shell-free
argv), all against a scriptable stub `hermes` installed as the only hermes
on a restricted PATH.
"""
from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

import pytest

from cli import install_workflow
from cli.install_workflow import (
    _CLIENTS,
    _REGISTRATION_ADAPTERS,
    HERMES_REGISTRATION_TIMEOUT_SECONDS,
    SUPPORTED_CLIENTS,
    WorkflowRefusal,
    _client_config_path,
    _client_destinations,
    _hermes_bridge_rows,
    _hermes_config_path,
    _hermes_runtime_facts,
    _read_hermes_registration,
    _uninstall_hermes_registration,
    _verify_frozen_destinations,
    _write_hermes_registration,
    unregister_client,
)

EXPECTED = "/install/bin/cartopian-mcp"

# The fresh-write per-key sequence; `enabled` MUST be last so an interrupted
# sequence can never leave a partially configured entry active.
SET_KEYS = [
    "mcp_servers.cartopian.command",
    "mcp_servers.cartopian.timeout",
    "mcp_servers.cartopian.env.CARTOPIAN_MCP_HOST",
    "mcp_servers.cartopian.env.CARTOPIAN_HERMES_HOME",
    "mcp_servers.cartopian.enabled",
]

# A repair of an existing owned entry additionally writes `enabled: false`
# FIRST: without it, an already-enabled drifted entry would stay active
# through an interrupted repair with partially updated fields.
REPAIR_SET_KEYS = ["mcp_servers.cartopian.enabled"] + SET_KEYS


@pytest.fixture(autouse=True)
def clean_hermes_env(monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return home


class HermesStub:
    """A scriptable `hermes` on a restricted PATH, recording every argv.

    Behavior is driven by control files so one stub covers every scenario:
    ``config_path`` (printed by `config path`), ``version`` (printed by
    `--version`), ``get_stdout``/``get_stderr``/``get_exit`` (the `config get
    --json` outcome), ``fail_set_at`` (1-based index of the `config set` call
    that exits 1), and ``sets.log`` / ``argv.log`` as the recorded evidence.
    """

    def __init__(self, tmp_path: Path, monkeypatch) -> None:
        self.ctrl = tmp_path / "hermes-ctrl"
        self.ctrl.mkdir(exist_ok=True)
        self.bin_dir = tmp_path / "hermesbin"
        self.bin_dir.mkdir(exist_ok=True)
        self.profile_home = tmp_path / "hermes-profile"
        self.profile_home.mkdir(exist_ok=True)
        (self.ctrl / "config_path").write_text(
            str(self.profile_home / "config.yaml"), encoding="utf-8"
        )
        (self.ctrl / "version").write_text(
            "hermes 0.20.0 (2026.8.3)", encoding="utf-8"
        )
        stub = self.bin_dir / "hermes"
        stub.write_text(
            "#!/bin/sh\n"
            f'CTRL="{self.ctrl}"\n'
            'echo "$@" >> "$CTRL/argv.log"\n'
            'PROFILE=""\n'
            'if [ "$1" = "-p" ]; then PROFILE="$2"; shift 2; fi\n'
            'echo "$1 $2 profile=$PROFILE home=$HERMES_HOME" >> "$CTRL/pins.log"\n'
            'if [ "$1" = "--version" ]; then cat "$CTRL/version"; exit 0; fi\n'
            'case "$1 $2" in\n'
            '  "config path") cat "$CTRL/config_path"; exit 0;;\n'
            '  "config get")\n'
            '    [ -f "$CTRL/get_stdout" ] && cat "$CTRL/get_stdout"\n'
            '    [ -f "$CTRL/get_stderr" ] && cat "$CTRL/get_stderr" >&2\n'
            '    [ -f "$CTRL/get_exit" ] && exit "$(cat "$CTRL/get_exit")"\n'
            "    exit 0;;\n"
            '  "config set")\n'
            '    n=0; [ -f "$CTRL/set_count" ] && n="$(cat "$CTRL/set_count")"\n'
            '    n=$((n+1)); echo "$n" > "$CTRL/set_count"\n'
            '    if [ -f "$CTRL/fail_set_at" ] && [ "$n" -eq "$(cat "$CTRL/fail_set_at")" ]; then\n'
            '      echo "simulated set failure" >&2; exit 1\n'
            "    fi\n"
            '    echo "set $3 $4" >> "$CTRL/sets.log"; exit 0;;\n'
            '  "config unset")\n'
            '    echo "unset $3" >> "$CTRL/sets.log"; exit 0;;\n'
            "esac\n"
            "exit 0\n",
            encoding="utf-8",
        )
        stub.chmod(
            stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH
        )
        monkeypatch.setenv(
            "PATH", os.pathsep.join([str(self.bin_dir), "/usr/bin", "/bin"])
        )

    # -- scripting -----------------------------------------------------------

    def script_get(self, payload, *, exit_code: int = 0, stderr: str = "") -> None:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        (self.ctrl / "get_stdout").write_text(text, encoding="utf-8")
        (self.ctrl / "get_exit").write_text(str(exit_code), encoding="utf-8")
        if stderr:
            (self.ctrl / "get_stderr").write_text(stderr, encoding="utf-8")

    def script_absent_entry(self) -> None:
        (self.ctrl / "get_stdout").write_text("", encoding="utf-8")
        (self.ctrl / "get_stderr").write_text(
            "Config key not set", encoding="utf-8"
        )
        (self.ctrl / "get_exit").write_text("1", encoding="utf-8")

    def fail_set_at(self, index: int) -> None:
        (self.ctrl / "fail_set_at").write_text(str(index), encoding="utf-8")

    def clear_set_failure(self) -> None:
        (self.ctrl / "fail_set_at").unlink(missing_ok=True)
        (self.ctrl / "set_count").unlink(missing_ok=True)

    # -- recorded evidence ---------------------------------------------------

    def sets(self) -> list[str]:
        log = self.ctrl / "sets.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    def argv(self) -> list[str]:
        log = self.ctrl / "argv.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    def pins(self, command: str) -> list[str]:
        """`profile=<p> home=<HERMES_HOME>` for each invocation of ``command``
        (e.g. ``"config set"``), with any leading `-p <name>` already parsed."""
        log = self.ctrl / "pins.log"
        lines = (
            log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        )
        prefix = command + " "
        return [line[len(prefix):] for line in lines if line.startswith(prefix)]

    def desired_entry(self) -> dict:
        return {
            "command": EXPECTED,
            "timeout": HERMES_REGISTRATION_TIMEOUT_SECONDS,
            "env": {
                "CARTOPIAN_MCP_HOST": "hermes",
                "CARTOPIAN_HERMES_HOME": str(self.profile_home),
            },
            "enabled": True,
        }


@pytest.fixture
def stub(tmp_path, monkeypatch):
    return HermesStub(tmp_path, monkeypatch)


def _no_hermes_on_path(tmp_path, monkeypatch):
    empty = tmp_path / "emptybin"
    empty.mkdir(exist_ok=True)
    monkeypatch.setenv(
        "PATH", os.pathsep.join([str(empty), "/usr/bin", "/bin"])
    )


# --- adapter-map closure ----------------------------------------------------


def test_every_client_format_maps_to_a_live_adapter():
    """The closed dispatch cannot silently mis-route a client: every format
    declared in _CLIENTS resolves to a (reader, writer) adapter pair."""
    for client, descriptor in _CLIENTS.items():
        fmt = descriptor["format"]
        assert fmt in _REGISTRATION_ADAPTERS, (client, fmt)
        reader, writer = _REGISTRATION_ADAPTERS[fmt]
        assert callable(reader) and callable(writer)


def test_hermes_is_a_supported_client_with_the_hermes_cli_format():
    assert "hermes" in SUPPORTED_CLIENTS
    assert _CLIENTS["hermes"]["format"] == "hermes-cli"


def test_other_clients_resolve_to_the_same_paths_as_before(tmp_path, monkeypatch):
    """The hermes resolver must not relocate any existing client's config file
    (blast-radius guard for the shared `_client_config_path`)."""
    if os.name == "nt":
        pytest.skip("static expectations below are the POSIX branch")
    _no_hermes_on_path(tmp_path, monkeypatch)
    expected = {
        "claude-code": ".claude.json",
        "codex": ".codex/config.toml",
        "gemini": ".gemini/settings.json",
        "devin": ".config/devin/config.json",
        "windsurf": ".codeium/windsurf/mcp_config.json",
        "claude-desktop": (
            "Library/Application Support/Claude/claude_desktop_config.json"
        ),
        "cursor": ".cursor/mcp.json",
    }
    for client in SUPPORTED_CLIENTS:
        if client in ("opencode", "hermes"):
            continue
        assert _client_config_path(client, tmp_path) == tmp_path / expected[client]


# --- destination resolution (D10): the CLI's answer, not env guesswork ------


def test_config_path_comes_from_the_hermes_cli(stub, home):
    """`hermes config path` folds in profile flag, sticky default, and ambient
    HERMES_HOME, so its printed path is the frozen destination."""
    assert _hermes_config_path(home) == stub.profile_home / "config.yaml"


def test_resolver_output_beats_ambient_hermes_home(stub, home, monkeypatch, tmp_path):
    """Wrong-profile regression: an ambient HERMES_HOME pointing elsewhere must
    not override what the CLI actually resolves — resolver output decides."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "some-other-home"))
    assert _hermes_config_path(home) == stub.profile_home / "config.yaml"


def test_named_profile_home_is_followed(stub, home, tmp_path):
    """A profile-scoped `config path` answer (e.g. under `-p reviewer` or a
    sticky active_profile) moves both frozen surfaces together."""
    profile = tmp_path / "profiles" / "reviewer"
    profile.mkdir(parents=True)
    (stub.ctrl / "config_path").write_text(
        str(profile / "config.yaml"), encoding="utf-8"
    )
    assert _hermes_config_path(home) == profile / "config.yaml"
    destinations = [dest for _src, dest in _hermes_bridge_rows(home)]
    assert destinations == [
        profile / "skills" / "cartopian" / "DESCRIPTION.md",
        profile / "skills" / "cartopian" / "use-cartopian" / "SKILL.md",
    ]


def test_config_path_failure_refuses_instead_of_guessing(stub, home):
    """A runnable hermes that cannot report its config location must refuse:
    a static fallback could name a different profile than the one Hermes
    actually uses, silently splitting the registration and the bridge."""
    broken = stub.bin_dir / "hermes"
    broken.write_text(
        "#!/bin/sh\n"
        'if [ "$1 $2" = "config path" ]; then echo boom >&2; exit 3; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    with pytest.raises(WorkflowRefusal, match="cannot report"):
        _hermes_config_path(home)


def test_config_path_empty_output_refuses(stub, home):
    (stub.ctrl / "config_path").write_text("", encoding="utf-8")
    with pytest.raises(WorkflowRefusal, match="printed nothing"):
        _hermes_config_path(home)


def test_config_path_relative_output_refuses(stub, home):
    (stub.ctrl / "config_path").write_text(
        "relative/config.yaml", encoding="utf-8"
    )
    with pytest.raises(WorkflowRefusal, match="non-absolute"):
        _hermes_config_path(home)


def test_cli_absent_detection_falls_back_to_the_static_default(
    home, tmp_path, monkeypatch
):
    """Without a runnable CLI, detection uses the static default location
    (HERMES_HOME or ~/.hermes) instead of failing the whole plan for other
    clients; planning with hermes *selected* still refuses (test below)."""
    _no_hermes_on_path(tmp_path, monkeypatch)
    assert _hermes_config_path(home) == home / ".hermes" / "config.yaml"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hh"))
    assert _hermes_config_path(home) == tmp_path / "hh" / "config.yaml"


def test_skills_bundle_rows_derive_from_the_config_dir(stub, home):
    rows = _hermes_bridge_rows(home)
    assert [src for src, _dest in rows] == [
        "templates/clients/hermes/skills/DESCRIPTION.md",
        "templates/clients/hermes/skills/use-cartopian/SKILL.md",
    ]
    bundle = stub.profile_home / "skills" / "cartopian"
    assert [dest for _src, dest in rows] == [
        bundle / "DESCRIPTION.md",
        bundle / "use-cartopian" / "SKILL.md",
    ]


def test_client_destinations_freeze_executable_and_version(stub, home):
    destinations = _client_destinations(("hermes",), home)["hermes"]
    assert destinations["registration"] == [
        str(stub.profile_home / "config.yaml")
    ]
    assert destinations["executable"] == [
        str((stub.bin_dir / "hermes").resolve())
    ]
    assert destinations["version"] == ["hermes 0.20.0 (2026.8.3)"]


def test_cli_absent_refuses_at_plan_time(home, tmp_path, monkeypatch):
    _no_hermes_on_path(tmp_path, monkeypatch)
    with pytest.raises(WorkflowRefusal, match="not on PATH"):
        _client_destinations(("hermes",), home)


def test_unsupported_version_refuses_at_plan_time(stub, home):
    (stub.ctrl / "version").write_text("hermes 0.19.4", encoding="utf-8")
    with pytest.raises(WorkflowRefusal, match="below the minimum"):
        _hermes_runtime_facts(home)


def test_unparseable_version_refuses_at_plan_time(stub, home):
    (stub.ctrl / "version").write_text("mystery build", encoding="utf-8")
    with pytest.raises(WorkflowRefusal, match="could not be parsed"):
        _hermes_runtime_facts(home)


def test_executable_swap_between_plan_and_apply_refuses(stub, home):
    recorded = _client_destinations(("hermes",), home)
    recorded["hermes"]["executable"] = ["/somewhere/else/hermes"]
    with pytest.raises(WorkflowRefusal, match="executable changed"):
        _verify_frozen_destinations(("hermes",), home, recorded, "registration")


def test_version_change_between_plan_and_apply_refuses(stub, home):
    recorded = _client_destinations(("hermes",), home)
    (stub.ctrl / "version").write_text("hermes 0.21.0", encoding="utf-8")
    with pytest.raises(WorkflowRefusal, match="version changed"):
        _verify_frozen_destinations(("hermes",), home, recorded, "registration")


def test_version_change_refuses_before_bridge_mutation_too(stub, home):
    """The frozen executable/version revalidation is kind-agnostic: bridges
    apply before registrations, so the bridge pass must refuse a changed
    toolchain before any skill file is written."""
    recorded = _client_destinations(("hermes",), home)
    (stub.ctrl / "version").write_text("hermes 0.21.0", encoding="utf-8")
    with pytest.raises(WorkflowRefusal, match="version changed"):
        _verify_frozen_destinations(("hermes",), home, recorded, "bridges")


def test_unchanged_environment_verifies_frozen_destinations(stub, home):
    recorded = _client_destinations(("hermes",), home)
    _verify_frozen_destinations(("hermes",), home, recorded, "registration")
    _verify_frozen_destinations(("hermes",), home, recorded, "bridges")


# --- reader: one subprocess, four-way verdict --------------------------------


def test_reader_reports_missing_when_the_key_is_not_set(stub, home):
    stub.script_absent_entry()
    assert _read_hermes_registration("hermes", home, EXPECTED) == (
        "missing",
        "absent",
    )


def test_reader_reports_current_only_on_the_complete_desired_entry(stub, home):
    stub.script_get(stub.desired_entry())
    assert _read_hermes_registration("hermes", home, EXPECTED) == (
        "current",
        "expected-command",
    )


@pytest.mark.parametrize(
    "mutation",
    [
        {"enabled": False},
        {"enabled": None},
        {"timeout": 600},
        {"env": {"CARTOPIAN_MCP_HOST": "hermes"}},  # missing home marker
        {"env": {}},
    ],
)
def test_owned_but_drifted_entries_read_dirty(stub, home, mutation):
    """Our command with any other field missing or different — including the
    interrupted-write case where `enabled` never landed — is drift, not
    current: revision 1's command-only read could report an interrupted
    registration as complete."""
    entry = stub.desired_entry()
    entry.update(mutation)
    stub.script_get(entry)
    state, evidence = _read_hermes_registration("hermes", home, EXPECTED)
    assert state == "dirty"
    assert evidence.startswith("configuration-fingerprint:")


@pytest.mark.parametrize(
    "extra",
    [
        {"url": "https://elsewhere.example/mcp"},
        {"args": ["--evil"]},
        {"transport": "sse"},
    ],
)
def test_launch_affecting_extras_are_never_current(stub, home, extra):
    """Hermes prefers `url` over `command`: an entry carrying the expected
    command plus a foreign URL (or any other unmanaged field) would launch
    or connect elsewhere while reading as registered — it must read dirty
    with the unmanaged marker, never current."""
    entry = stub.desired_entry()
    entry.update(extra)
    stub.script_get(entry)
    state, evidence = _read_hermes_registration("hermes", home, EXPECTED)
    assert state == "dirty"
    assert evidence.startswith("unmanaged-keys;")


def test_unmanaged_env_key_is_never_current(stub, home):
    """Extra env vars are launch inputs too (PYTHONPATH can change what code
    the registered command runs), and the per-key sets cannot remove them."""
    entry = stub.desired_entry()
    entry["env"]["PYTHONPATH"] = "/somewhere/injected"
    stub.script_get(entry)
    state, evidence = _read_hermes_registration("hermes", home, EXPECTED)
    assert state == "dirty"
    assert evidence.startswith("unmanaged-keys;")


def test_writer_refuses_and_preserves_an_entry_with_unmanaged_fields(stub, home):
    """Repair is refused, not attempted: re-applying the managed keys cannot
    remove `url`, so converging would leave an enabled entry that connects
    somewhere else entirely."""
    entry = stub.desired_entry()
    entry["url"] = "https://elsewhere.example/mcp"
    stub.script_get(entry)
    with pytest.raises(WorkflowRefusal, match="url"):
        _write_hermes_registration("hermes", home, EXPECTED)
    assert stub.sets() == []


def test_foreign_command_reads_dirty_with_the_foreign_marker(stub, home):
    entry = stub.desired_entry()
    entry["command"] = "/somebody/elses/server"
    stub.script_get(entry)
    state, evidence = _read_hermes_registration("hermes", home, EXPECTED)
    assert state == "dirty"
    assert evidence.startswith("foreign-command;")


def test_reader_fails_closed_on_malformed_json(stub, home):
    stub.script_get("this is not json")
    assert _read_hermes_registration("hermes", home, EXPECTED) == (
        "malformed",
        "unreadable-hermes-configuration",
    )


def test_reader_fails_closed_on_a_non_object_entry(stub, home):
    stub.script_get('["an", "array"]')
    state, _evidence = _read_hermes_registration("hermes", home, EXPECTED)
    assert state == "malformed"


def test_reader_fails_closed_on_an_unexpected_exit(stub, home):
    stub.script_get("", exit_code=3, stderr="something exploded")
    state, _evidence = _read_hermes_registration("hermes", home, EXPECTED)
    assert state == "malformed"


def test_reader_does_not_treat_absent_text_on_exit_two_as_missing(stub, home):
    """Hermes documents only exit 1 as the missing-key outcome."""
    stub.script_get("", exit_code=2, stderr="Config key not set")
    state, _evidence = _read_hermes_registration("hermes", home, EXPECTED)
    assert state == "malformed"


def test_reader_fails_closed_when_the_cli_is_absent(home, tmp_path, monkeypatch):
    _no_hermes_on_path(tmp_path, monkeypatch)
    assert _read_hermes_registration("hermes", home, EXPECTED) == (
        "malformed",
        "hermes-cli-absent",
    )


# --- writer: five keys, enabled last, convergent -----------------------------


def test_fresh_write_runs_the_five_key_sequence_enabled_last(stub, home):
    stub.script_absent_entry()
    _write_hermes_registration("hermes", home, EXPECTED)
    sets = stub.sets()
    assert [line.split()[1] for line in sets] == SET_KEYS
    assert sets[0] == f"set mcp_servers.cartopian.command {EXPECTED}"
    assert (
        sets[1]
        == "set mcp_servers.cartopian.timeout "
        + str(HERMES_REGISTRATION_TIMEOUT_SECONDS)
    )
    assert sets[2] == "set mcp_servers.cartopian.env.CARTOPIAN_MCP_HOST hermes"
    assert sets[3] == (
        "set mcp_servers.cartopian.env.CARTOPIAN_HERMES_HOME "
        + str(stub.profile_home)
    )
    assert sets[4] == "set mcp_servers.cartopian.enabled true"


def test_current_entry_writes_nothing(stub, home):
    stub.script_get(stub.desired_entry())
    _write_hermes_registration("hermes", home, EXPECTED)
    assert stub.sets() == []


def test_drifted_entry_is_rewritten_disabled_first(stub, home):
    """A repair leads with `enabled: false` and ends with `enabled: true`, so
    no interruption point leaves the previously enabled entry active with
    partially updated fields."""
    entry = stub.desired_entry()
    entry["timeout"] = 600
    stub.script_get(entry)
    _write_hermes_registration("hermes", home, EXPECTED)
    sets = stub.sets()
    assert [line.split()[1] for line in sets] == REPAIR_SET_KEYS
    assert sets[0] == "set mcp_servers.cartopian.enabled false"
    assert sets[-1] == "set mcp_servers.cartopian.enabled true"


def test_foreign_entry_refuses_and_never_overwrites(stub, home):
    entry = stub.desired_entry()
    entry["command"] = "/somebody/elses/server"
    stub.script_get(entry)
    with pytest.raises(WorkflowRefusal, match="different command"):
        _write_hermes_registration("hermes", home, EXPECTED)
    assert stub.sets() == []


def test_malformed_entry_refuses_with_the_manual_snippet(stub, home):
    stub.script_get("not json")
    with pytest.raises(WorkflowRefusal, match="mcp_servers:"):
        _write_hermes_registration("hermes", home, EXPECTED)
    assert stub.sets() == []


def test_cli_absent_write_refuses(home, tmp_path, monkeypatch):
    _no_hermes_on_path(tmp_path, monkeypatch)
    with pytest.raises(WorkflowRefusal, match="not on PATH"):
        _write_hermes_registration("hermes", home, EXPECTED)


@pytest.mark.parametrize("fail_at", [1, 2, 3, 4, 5])
def test_interruption_at_each_write_leaves_the_entry_inert_then_converges(
    stub, home, fail_at
):
    """Interruption safety by construction: whichever write fails, `enabled`
    has not landed (it is last), the reader classifies the partial entry as
    owned-but-drifted, and re-apply converges by re-running the sequence."""
    stub.script_absent_entry()
    stub.fail_set_at(fail_at)
    with pytest.raises(WorkflowRefusal, match="config set"):
        _write_hermes_registration("hermes", home, EXPECTED)
    landed = stub.sets()
    assert len(landed) == fail_at - 1
    assert not any(".enabled" in line for line in landed), (
        "an interrupted sequence must never have written `enabled`"
    )

    if fail_at > 1:
        # The reader sees the partial entry (our command, `enabled` absent):
        # owned-but-drifted, never current.
        partial = {"command": EXPECTED}
        stub.script_get(partial)
        state, evidence = _read_hermes_registration("hermes", home, EXPECTED)
        assert state == "dirty"
        assert evidence.startswith("configuration-fingerprint:")
        stub.script_absent_entry()

    # Re-apply converges: the full sequence runs again, enabled last.
    stub.clear_set_failure()
    (stub.ctrl / "sets.log").unlink(missing_ok=True)
    _write_hermes_registration("hermes", home, EXPECTED)
    assert [line.split()[1] for line in stub.sets()] == SET_KEYS


@pytest.mark.parametrize("fail_at", [1, 2, 3, 4, 5, 6])
def test_interrupted_repair_of_an_enabled_entry_is_inert(stub, home, fail_at):
    """Repair interruption safety: the first write disables the entry, so an
    already-enabled drifted registration can never remain active alongside
    partially updated fields. A failure on the very first write changes
    nothing at all — the old entry stays whole, never half-repaired."""
    entry = stub.desired_entry()
    entry["timeout"] = 600  # drifted but enabled
    stub.script_get(entry)
    stub.fail_set_at(fail_at)
    with pytest.raises(WorkflowRefusal, match="config set"):
        _write_hermes_registration("hermes", home, EXPECTED)
    landed = stub.sets()
    assert len(landed) == fail_at - 1
    if landed:
        assert landed[0] == "set mcp_servers.cartopian.enabled false", (
            "any repair write must be preceded by disabling the entry"
        )
    assert "set mcp_servers.cartopian.enabled true" not in landed

    # Re-apply converges: the entry still carries our command, the reader
    # classifies it owned-but-drifted, and the repair sequence re-runs.
    stub.clear_set_failure()
    (stub.ctrl / "sets.log").unlink(missing_ok=True)
    _write_hermes_registration("hermes", home, EXPECTED)
    assert [line.split()[1] for line in stub.sets()] == REPAIR_SET_KEYS


# --- profile pinning: one resolved identity for the read and every write ----


def test_registration_sequence_is_pinned_to_the_resolved_profile(stub, home):
    """The home is resolved once (`config path`, unpinned by construction) and
    the whole-entry read plus all five writes carry the explicit identity: for
    a root home that is `-p default` with HERMES_HOME, because Hermes lets a
    sticky active_profile override a bare root HERMES_HOME — without the pin a
    mid-sequence profile switch could redirect or split the writes."""
    stub.script_absent_entry()
    _write_hermes_registration("hermes", home, EXPECTED)
    expected_pin = f"profile=default home={stub.profile_home}"
    assert stub.pins("config path") == ["profile= home="]
    assert stub.pins("config get") == [expected_pin]
    assert stub.pins("config set") == [expected_pin] * 5


def test_reader_is_pinned_to_the_resolved_profile(stub, home):
    stub.script_get(stub.desired_entry())
    _read_hermes_registration("hermes", home, EXPECTED)
    assert stub.pins("config get") == [
        f"profile=default home={stub.profile_home}"
    ]


def test_named_profile_home_is_pinned_via_trusted_env(stub, home, tmp_path):
    """A profile-parented home needs no `-p` flag: Hermes trusts a HERMES_HOME
    whose parent directory is named `profiles` verbatim, before consulting the
    sticky active_profile — the env alone is the pin."""
    profile = tmp_path / "profiles" / "reviewer"
    profile.mkdir(parents=True)
    (stub.ctrl / "config_path").write_text(
        str(profile / "config.yaml"), encoding="utf-8"
    )
    stub.script_absent_entry()
    _write_hermes_registration("hermes", home, EXPECTED)
    expected_pin = f"profile= home={profile}"
    assert stub.pins("config get") == [expected_pin]
    assert stub.pins("config set") == [expected_pin] * 5
    assert not any(line.startswith("-p ") for line in stub.argv())


# --- resolution scope: one profile home per operation ------------------------


def test_operation_scope_resolves_the_profile_home_once(stub, home, tmp_path):
    """Inside one operation the first `hermes config path` answer is carried
    from planning through verification into the mutation: a concurrent
    sticky-profile switch after verification must not redirect the actual
    write to a home the operation never displayed."""
    stub.script_absent_entry()
    with install_workflow._hermes_resolution_scope():
        recorded = _client_destinations(("hermes",), home)
        _verify_frozen_destinations(("hermes",), home, recorded, "registration")
        # A concurrent profile switch changes what `config path` would now say.
        hijacked = tmp_path / "profiles" / "hijacked"
        hijacked.mkdir(parents=True)
        (stub.ctrl / "config_path").write_text(
            str(hijacked / "config.yaml"), encoding="utf-8"
        )
        _write_hermes_registration("hermes", home, EXPECTED)
    # Exactly one resolution, and every write pinned to the verified home.
    assert stub.pins("config path") == ["profile= home="]
    assert stub.pins("config set") == [
        f"profile=default home={stub.profile_home}"
    ] * 5


def test_apply_registrations_pairs_verification_and_mutation_in_one_scope(
    stub, home
):
    """`_apply_registrations` runs the frozen-destination check and the write
    under a single resolution, so both consult the same profile home."""
    stub.script_absent_entry()
    recorded = _client_destinations(("hermes",), home)
    (stub.ctrl / "pins.log").unlink(missing_ok=True)
    install_workflow._apply_registrations(
        ("hermes",), home, Path("/install"), recorded
    )
    assert stub.pins("config path") == ["profile= home="]
    assert [line.split()[1] for line in stub.sets()] == SET_KEYS


def test_workflow_entry_points_enter_the_resolution_scope():
    """Every operation that touches Hermes destinations resolves the profile
    home once at its boundary; a new entry point that forgets the scope
    reintroduces the split-write hazard."""
    for func in (
        install_workflow.plan_workflow,
        install_workflow.apply_workflow,
        install_workflow.verify_workflow,
        install_workflow._apply_registrations,
        install_workflow._apply_bridges,
        install_workflow.unregister_client,
    ):
        assert getattr(func, "_hermes_scoped", False), func.__name__


# --- uninstall: promptless config unset, guarded by the ours-check ----------


def test_uninstall_unsets_our_entry(stub, home):
    stub.script_get(stub.desired_entry())
    _uninstall_hermes_registration(home, EXPECTED)
    assert stub.sets() == ["unset mcp_servers.cartopian"]
    # The ours-check read and the unset carry the same resolved identity, so
    # a profile switch between them cannot unset a different config's entry.
    expected_pin = f"profile=default home={stub.profile_home}"
    assert stub.pins("config get") == [expected_pin]
    assert stub.pins("config unset") == [expected_pin]


def test_uninstall_of_an_absent_entry_is_a_no_op(stub, home):
    stub.script_absent_entry()
    _uninstall_hermes_registration(home, EXPECTED)
    assert stub.sets() == []


def test_uninstall_preserves_a_foreign_entry(stub, home):
    entry = stub.desired_entry()
    entry["command"] = "/somebody/elses/server"
    stub.script_get(entry)
    with pytest.raises(WorkflowRefusal, match="different command"):
        _uninstall_hermes_registration(home, EXPECTED)
    assert stub.sets() == []


def test_uninstall_fails_closed_on_an_unreadable_entry(stub, home):
    stub.script_get("not json")
    with pytest.raises(WorkflowRefusal, match="preserved"):
        _uninstall_hermes_registration(home, EXPECTED)
    assert stub.sets() == []


def test_uninstall_never_uses_mcp_remove(stub, home):
    """`hermes mcp remove` prompts on stdin — unacceptable when stdin is the
    MCP protocol pipe — so the uninstall path must never invoke it."""
    stub.script_get(stub.desired_entry())
    _uninstall_hermes_registration(home, EXPECTED)
    assert not any(line.startswith("mcp ") for line in stub.argv())


# --- unregister_client: the user-facing dispatch to the guarded uninstall ---


def test_unregister_client_routes_hermes_to_the_guarded_uninstall(stub, home):
    entry = stub.desired_entry()
    entry["command"] = "/install/bin/cartopian-mcp"
    stub.script_get(entry)
    unregister_client("hermes", Path("/install"), client_home=home)
    assert stub.sets() == ["unset mcp_servers.cartopian"]


def test_unregister_client_refuses_an_unsupported_client(home):
    with pytest.raises(WorkflowRefusal, match="unsupported client"):
        unregister_client("mystery-agent", Path("/install"), client_home=home)


def test_unregister_client_without_an_uninstaller_gives_a_manual_instruction(
    stub, home
):
    """Formats outside the closed uninstaller map refuse with the config path
    to edit rather than guessing at a file mutation."""
    with pytest.raises(WorkflowRefusal, match="manually"):
        unregister_client("codex", Path("/install"), client_home=home)


# --- subprocess hygiene ------------------------------------------------------


def test_hanging_cli_is_killed_at_the_fixed_timeout(stub, home, monkeypatch):
    """The refusal must land at the timeout, not when the child's descendants
    feel like exiting: killing only the immediate shell once left its `sleep`
    holding the pipes, and this 1s timeout took the full 20s to report."""
    monkeypatch.setattr(install_workflow, "_HERMES_SUBPROCESS_TIMEOUT_SECONDS", 1)
    hang = stub.bin_dir / "hermes"
    hang.write_text("#!/bin/sh\nsleep 20\n", encoding="utf-8")
    started = time.monotonic()
    with pytest.raises(WorkflowRefusal, match="was killed"):
        install_workflow._run_hermes(str(hang), ("config", "path"))
    assert time.monotonic() - started < 10


def test_oversized_output_is_bounded_and_refused(stub, home, monkeypatch):
    monkeypatch.setattr(install_workflow, "_HERMES_MAX_CAPTURE_BYTES", 64)
    flood = stub.bin_dir / "hermes"
    flood.write_text(
        "#!/bin/sh\nprintf 'a%.0s' $(seq 1 200)\n", encoding="utf-8"
    )
    with pytest.raises(WorkflowRefusal, match="refusing"):
        install_workflow._run_hermes(str(flood), ("config", "path"))


def test_endless_flood_is_killed_at_the_bound_not_buffered(
    stub, home, monkeypatch
):
    """The capture bound is enforced while the child runs: a process that
    floods forever is killed the moment a stream passes the cap. A
    buffer-then-check implementation would sit on the flood until the fixed
    timeout (and grow without bound) — this test would then fail on the
    timeout message instead of the capture-bound refusal."""
    monkeypatch.setattr(install_workflow, "_HERMES_MAX_CAPTURE_BYTES", 4096)
    flood = stub.bin_dir / "hermes"
    flood.write_text(
        "#!/bin/sh\nwhile :; do printf "
        "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'; done\n",
        encoding="utf-8",
    )
    with pytest.raises(WorkflowRefusal, match="produced more than"):
        install_workflow._run_hermes(str(flood), ("config", "path"))


def test_subprocesses_never_read_this_process_stdin(stub, home):
    """Under MCP-hosted execution stdin is the protocol pipe; a stub that
    blocks on stdin must still complete because the child gets /dev/null."""
    reader = stub.bin_dir / "hermes"
    reader.write_text(
        "#!/bin/sh\ncat > /dev/null\necho done\n", encoding="utf-8"
    )
    code, stdout, _stderr = install_workflow._run_hermes(
        str(reader), ("config", "path")
    )
    assert code == 0
    assert stdout.strip() == "done"

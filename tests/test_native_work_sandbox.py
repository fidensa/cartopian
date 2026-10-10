"""Kernel conformance through launched wrappers, separate from argv tests.

Run natively outside another Seatbelt sandbox. Skips are explicitly not evidence.
The deterministic backend executes real syscalls (including child processes);
authenticated vendor-CLI evidence is collected separately by the native probe.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli.native_work_sandbox import check_substrate, profile, publication_slots, publish, read_candidate
from cli.work_access import WorkAccessError, effective_access
from tests.test_project_work_access import ADAPTERS, REPO, project_fixture


@pytest.fixture
def native(tmp_path):
    if sys.platform != "darwin":
        pytest.skip("requires native macOS Seatbelt")
    project, work, home = project_fixture(tmp_path, configured=True)
    access = effective_access(project, {"work": str(work)}, (), activated=True)
    try:
        check_substrate(access)
    except WorkAccessError as exc:
        if os.environ.get("CARTOPIAN_REQUIRE_NATIVE_CONFORMANCE") == "1":
            pytest.fail(str(exc))
        pytest.skip("native backend cannot start in enclosing sandbox: " + str(exc))
    return project, work, home


BACKEND = r'''
import errno, json, os, pathlib, socket, subprocess, sys
if '--help' in sys.argv:
    sys.exit(0)
p = pathlib.Path(os.environ['CARTOPIAN_PROJECT_ROOT'])
w = pathlib.Path(os.environ['CARTOPIAN_WORK_ROOTS'])
role = os.environ['CARTOPIAN_ROLE']
records = []
def operation(name, expected, action):
    try:
        action()
    except OSError as exc:
        assert expected == 'denied' and exc.errno in (errno.EPERM, errno.EACCES, errno.EROFS), (name, expected, exc)
        records.append({'operation':name,'result':'denied','errno':exc.errno})
    else:
        assert expected == 'allowed', name + ' escaped containment'
        records.append({'operation':name,'result':'allowed'})
operation('read work', 'denied' if role=='none' else 'allowed', lambda:(w/'input').read_text())
if os.environ.get('TEST_DATA_ALIAS'):
    operation('read APFS firmlink alias','denied' if role=='none' else 'allowed',
              lambda:pathlib.Path(os.environ['TEST_DATA_ALIAS']).read_text())
for relative in ('STATE.md','cartopian.toml','cartopian.local.toml','REQUIREMENTS.md','STANDARDS.md','IMPLEMENTATION_PLAN.md','.git/config','.requests/input','requests/input','tasks/input','phases/input','specs/input','decisions/input','prompts/input','reports/input','reviews/input','resources/sibling/input'):
    operation('write '+relative,'denied',lambda relative=relative:(p/relative).write_text('escape'))
operation('outside project','denied',lambda:(p.parent/'other-project/STATE.md').write_text('escape'))
operation('runtime','denied',lambda:pathlib.Path(os.environ['TEST_RUNTIME']).open('a'))
operation('host state','denied',lambda:pathlib.Path(os.environ['TEST_HOST_STATE']).write_text('escape'))
operation('create work','allowed' if role=='worker' else 'denied',lambda:(w/'created').write_text('work'))
code = 'from pathlib import Path; Path('+repr(str(w/'child'))+').write_text("child")'
child = subprocess.run([sys.executable,'-I','-S','-c',code],capture_output=True,text=True)
assert (child.returncode==0)==(role=='worker'), child.stderr
records.append({'operation':'child write','result':'allowed' if role=='worker' else 'denied'})
operation('fresh hardlink','denied',lambda:os.link(p/'STATE.md',w/'linked'))
for address in ('127.0.0.1', '::1', '::ffff:127.0.0.1'):
    def connection(address=address):
        family = socket.AF_INET if address=='127.0.0.1' else socket.AF_INET6
        with socket.socket(family) as client:
            client.settimeout(2)
            client.connect((address, int(os.environ['TEST_LOCAL_PORT'])))
    operation('local daemon '+address,'denied',connection)
from urllib.parse import urlparse
proxy = urlparse(os.environ['HTTPS_PROXY'])
with socket.create_connection((proxy.hostname,proxy.port),timeout=5) as client:
    client.sendall(('CONNECT 127.0.0.1:'+os.environ['TEST_LOCAL_PORT']+' HTTP/1.1\r\n\r\n').encode())
    assert client.recv(1024).startswith(b'HTTP/1.1 403'), 'proxy escaped local restriction'
records.append({'operation':'proxy local destination','result':'denied'})
if os.environ.get('CARTOPIAN_EXPECTED_REPORT_PATH'):
    publication=pathlib.Path(os.environ['CARTOPIAN_PUBLICATION_DIR'])
    (publication/'report.md').write_text('mediated report')
    (publication/'review.md').write_text('mediated review')
    operation('direct publication','denied',lambda:pathlib.Path(os.environ['CARTOPIAN_EXPECTED_REPORT_PATH']).write_text('escape'))
if role=='worker':
    operation('symlink creation','allowed',lambda:(w/'link').symlink_to(p/'STATE.md'))
    operation('symlink write','denied',lambda:(w/'link').write_text('escape'))
    operation('remove work file','allowed',lambda:(w/'created').unlink())
    operation('work Git metadata','denied',lambda:(w/'.git/config').write_text('escape'))
    operation('case variant settings','denied',lambda:(w/'.CoDeX/config.toml').write_text('escape'))
    operation('agent settings directory','denied',lambda:(w/'.GEMINI').mkdir())
    operation('traversal','denied',lambda:(w/'../../../STATE.md').write_text('escape'))
    for a in (w,*w.parents):
        if a == p.parent: break
        operation('rename '+str(a),'denied',lambda a=a:a.rename(p.parent/(a.name+'-moved')))
    empty = p/'scratch-empty'
    operation('remove empty root','denied',lambda:empty.rmdir())
    operation('replace empty root','denied',lambda:(p.parent/'replacement').rename(empty))
print('CONFORMANCE='+json.dumps(records))
'''


@pytest.mark.parametrize("adapter", ADAPTERS[1:])
@pytest.mark.parametrize("role", ("worker", "reader", "none"))
def test_launched_wrapper_kernel_conformance(native, tmp_path, adapter, role):
    project, work, home = native
    config = project / 'cartopian.toml'
    config.write_text(config.read_text().replace('grants=["read:work-roots"]',
                                                'grants=["read:work-roots", "write:reports"]'))
    for relative in ('STATE.md','REQUIREMENTS.md','STANDARDS.md','IMPLEMENTATION_PLAN.md','.git/config','.requests/input','requests/input','tasks/input','phases/input','specs/input','decisions/input','prompts/input','reports/input','reviews/input','resources/sibling/input'):
        file = project / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('protected')
    (project/'.git/config').write_text('')
    subprocess.run(['/usr/bin/git','init','-q',str(project)], check=True)
    # No live host file is ever changed by an allowed test action.
    state = home / 'host-state'
    state.write_text('protected')
    other = tmp_path / 'other-project/STATE.md'
    other.parent.mkdir()
    other.write_text('protected')
    (work / 'input').write_text('input')
    (work / '.git').mkdir()
    (work / '.git/config').write_text('protected')
    (work / '.CoDeX').mkdir()
    (work / '.CoDeX/config.toml').write_text('protected')
    empty = project / 'scratch-empty'
    empty.mkdir()
    (tmp_path / 'replacement').mkdir()
    import socket
    listener = socket.socket()
    listener.bind(('127.0.0.1',0))
    listener.listen()
    binary = tmp_path / 'bin' / adapter
    binary.parent.mkdir()
    binary.write_text('#!' + sys.executable + '\n' + BACKEND)
    binary.chmod(0o755)
    env = dict(os.environ, HOME=str(home), PATH=str(binary.parent)+':/usr/bin:/bin:/usr/sbin:/sbin',
               CARTOPIAN_ROLE=role, CARTOPIAN_PROJECT_ROOT=str(project), CARTOPIAN_WORK_ROOTS=str(work),
               CARTOPIAN_LAUNCH_CWD=str(work if role=='worker' else project), CARTOPIAN_PYTHON=sys.executable,
               CARTOPIAN_TIMEOUT='30s', TEST_RUNTIME=str(REPO/'cli/work_access.py'), TEST_HOST_STATE=str(state),
               TEST_LOCAL_PORT=str(listener.getsockname()[1]),
               CARTOPIAN_EXPECTED_REPORT_PATH=str(project/'reports/REPORT-01-999-review.md'),
               CARTOPIAN_EXPECTED_REPORT_VARIANT='review')
    data_alias = Path('/System/Volumes/Data' + str(work/'input'))
    if data_alias.exists() and os.path.samefile(data_alias,work/'input'):
        env['TEST_DATA_ALIAS'] = str(data_alias)
    (project/'reviews').mkdir(exist_ok=True)
    try:
        result = subprocess.run([str(REPO/'wrappers/bin'/('cartopian-'+adapter)),str(project/'prompts/PROMPT-01-999.md')],
                                env=env, text=True, capture_output=True, timeout=45)
    finally:
        listener.close()
    assert result.returncode == 0, result.stdout + result.stderr
    records = [json.loads(line.removeprefix('CONFORMANCE=')) for line in result.stdout.splitlines() if line.startswith('CONFORMANCE=')]
    assert len(records)==1 and len(records[0]) >= 24, result.stdout + result.stderr
    assert (project/'STATE.md').read_text() == state.read_text() == other.read_text() == 'protected'
    expected = project/'reports/REPORT-01-999-review.md'
    if role in ('worker','reader'):
        assert expected.read_text() == 'mediated report'
        assert (project/'reviews/REVIEW-01-999.md').read_text() == 'mediated review'
    else:
        assert not expected.exists()


def test_mediated_publication_is_bound_to_grants_and_slots(tmp_path):
    project, work, _home = project_fixture(tmp_path)
    (project/'reviews').mkdir()
    access = effective_access(project, {'resources':str(work)}, ('write:reports',), activated=True)
    expected = project/'reports/REPORT-01-999-review.md'
    env = {'CARTOPIAN_EXPECTED_REPORT_PATH':str(expected),'CARTOPIAN_EXPECTED_REPORT_VARIANT':'review'}
    slots = publication_slots(access, env)
    assert slots == {'report.md':('report',expected.name),'review.md':('review','REVIEW-01-999.md')}
    staged = tmp_path/'publication'
    staged.mkdir()
    (staged/'report.md').write_text('report bytes')
    (staged/'review.md').write_text('review bytes')
    (staged/'STATE.md').write_text('ignored')
    publish(access, staged, slots)
    assert expected.read_text() == 'report bytes'
    assert (project/'reviews/REVIEW-01-999.md').read_text() == 'review bytes'
    assert not (project/'STATE.md').exists()
    no_grant = effective_access(project, {'resources':str(work)}, (), activated=True)
    assert publication_slots(no_grant, env) == {}
    with pytest.raises(WorkAccessError):
        publication_slots(access, dict(env,CARTOPIAN_EXPECTED_REPORT_PATH=str(project/'STATE.md')))


@pytest.mark.parametrize('alias',('symlink','hardlink','directory','fifo','oversized'))
def test_controller_rejects_candidate_aliases_and_unbounded_data(tmp_path, alias):
    from cli.native_work_sandbox import MAX_PUBLICATION
    candidate = tmp_path/'report.md'
    protected = tmp_path/'protected'
    protected.write_text('protected')
    if alias=='symlink': candidate.symlink_to(protected)
    elif alias=='hardlink': os.link(protected,candidate)
    elif alias=='directory': candidate.mkdir()
    elif alias=='fifo': os.mkfifo(candidate)
    else:
        with candidate.open('wb') as stream: stream.truncate(MAX_PUBLICATION+1)
    with pytest.raises((OSError,WorkAccessError)):
        read_candidate(tmp_path,'report.md')


def test_runtime_subdirectory_cannot_be_a_work_root(native):
    from cli.native_work_sandbox import validate_boundary
    project, _work, home = native
    access = effective_access(project, {'runtime':str(REPO/'cli')}, ('read:work-roots','write:worktree'), activated=True)
    with pytest.raises(WorkAccessError, match='overlap the containment runtime'):
        validate_boundary(access, {'HOME':str(home),'CARTOPIAN_ROLE':'worker'})


@pytest.mark.parametrize('grants', ((), ('read:work-roots','write:worktree')))
def test_preexisting_cross_boundary_hardlink_refuses_launch(native, grants):
    from cli.native_work_sandbox import validate_boundary
    project, work, home = native
    protected = home/'protected'
    protected.write_text('protected')
    os.link(protected,work/'alias')
    access = effective_access(project, {'work':str(work)}, grants, activated=True)
    with pytest.raises(Exception, match='hard.link'):
        validate_boundary(access, {'HOME':str(home),'CARTOPIAN_ROLE':'worker'})


def test_active_config_cannot_become_a_mixed_work_root(native):
    from cli.native_work_sandbox import validate_boundary
    project, work, home = native
    access = effective_access(project, {'work':str(work),'config':str(home/'.cartopian')},
                              ('read:work-roots','write:worktree'), activated=True)
    with pytest.raises(WorkAccessError, match='configuration'):
        validate_boundary(access, {'HOME':str(home),'CARTOPIAN_ROLE':'worker'})


@pytest.mark.parametrize('alias', ('symlink', 'hardlink'))
def test_git_metadata_alias_inside_writable_root_refuses_launch(native, alias):
    from cli.native_work_sandbox import validate_boundary
    project, work, home = native
    source = work/'source'
    source.write_text('protected')
    metadata = work/'.git/config'
    metadata.parent.mkdir()
    if alias == 'symlink':
        metadata.symlink_to(source)
    else:
        os.link(source,metadata)
    access = effective_access(project, {'work':str(work)}, ('read:work-roots','write:worktree'), activated=True)
    with pytest.raises(Exception, match='symbolic link|hard.link'):
        validate_boundary(access, {'HOME':str(home),'CARTOPIAN_ROLE':'worker'})


def test_private_hermes_state_preserves_only_provider_scalars(tmp_path):
    from cli.native_work_sandbox import backend_environment
    host = tmp_path/'host/.hermes'
    host.mkdir(parents=True)
    (host/'config.yaml').write_text('model:\n  default: chosen-model\n  provider: openai-codex\nplugins:\n  enabled: [unsafe]\n')
    (host/'hooks').mkdir()
    scratch = tmp_path/'scratch'
    scratch.mkdir()
    env = backend_environment('hermes',scratch,{'HOME':str(host.parent)})
    assert env['CARTOPIAN_NATIVE_HERMES_PROVIDER'] == 'openai-codex'
    config = Path(env['HERMES_HOME'])/'config.yaml'
    assert 'chosen-model' in config.read_text()
    assert 'plugins' not in config.read_text()
    assert not (Path(env['HERMES_HOME'])/'hooks').exists()


def test_private_agy_session_uses_redirected_home_without_real_keychain(tmp_path, monkeypatch):
    from cli.native_work_sandbox import backend_environment

    host = tmp_path / 'host'
    source = host / '.gemini/antigravity-cli/antigravity-oauth-token'
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({'token': {'access_token': 'fixture-access', 'refresh_token': 'fixture-refresh'},
                                  'auth_method': 'fixture-method', 'id_token': 'fixture-id'}))
    (source.parent / 'settings.json').write_text('{"hooks": "must not be copied"}')
    scratch = tmp_path / 'private'
    scratch.mkdir()
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('redirected HOME accessed real Keychain'))
    env = backend_environment('agy', scratch, {'HOME': str(host), 'JETSKI_APP_DATA_DIR': '/host/state'})
    target = Path(env['JETSKI_APP_DATA_DIR']) / source.name
    assert target.read_bytes() == source.read_bytes()
    assert target.stat().st_mode & 0o777 == 0o600
    assert env['AGY_CLI_DISABLE_AUTO_UPDATE'] == 'true'
    assert not (target.parent / 'settings.json').exists()
    target.write_text('private refresh')
    assert json.loads(source.read_text())['token']['refresh_token'] == 'fixture-refresh'


@pytest.mark.skipif(os.name == 'nt', reason='macOS Keychain bridge uses Unix account lookup')
@pytest.mark.parametrize('encoded', (False, True))
def test_agy_keychain_bridge_preserves_session_without_logging(tmp_path, monkeypatch, capsys, encoded):
    import pwd
    from cli.native_work_sandbox import seed_agy_session

    host = tmp_path / 'host'
    private = tmp_path / 'private'
    host.mkdir()
    private.mkdir()
    data = json.dumps({'token': {'access_token': 'fixture-secret', 'refresh_token': 'fixture-refresh'},
                       'auth_method': 'fixture-method', 'id_token': 'fixture-id'}).encode()
    output = b'go-keyring-base64:' + base64.b64encode(data) if encoded else data
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(pwd, 'getpwuid', lambda _: SimpleNamespace(pw_dir=str(host)))
    calls = []
    def read_session(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=output + b'\n')
    monkeypatch.setattr(subprocess, 'run', read_session)
    seed_agy_session(private, host)
    target = private / '.gemini/antigravity-cli/antigravity-oauth-token'
    assert target.read_bytes() == data
    assert calls[0][0] == ['/usr/bin/security', 'find-generic-password', '-s', 'gemini', '-a', 'antigravity', '-w']
    assert calls[0][1]['capture_output'] and calls[0][1]['timeout'] == 10
    assert set(calls[0][1]['env']) == {'HOME', 'PATH'}
    assert target.stat().st_mode & 0o777 == 0o600
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('data', (b'fixture-secret-invalid-json', b'[]', b'{"token": {}}'))
def test_agy_session_rejects_invalid_data_without_exposing_secrets(tmp_path, data):
    from cli.native_work_sandbox import seed_agy_session

    host = tmp_path / 'host'
    source = host / '.gemini/antigravity-cli/antigravity-oauth-token'
    source.parent.mkdir(parents=True)
    source.write_bytes(data)
    private = tmp_path / 'private'
    private.mkdir()
    with pytest.raises(WorkAccessError, match='invalid Antigravity saved-session format') as exc:
        seed_agy_session(private, host)
    assert 'fixture-secret' not in str(exc.value)
    assert not (private / '.gemini').exists()


def test_agy_private_pty_cannot_write_or_control_host_terminal(native, tmp_path):
    import pty
    from cli.native_work_sandbox import SANDBOX

    project, work, _home = native
    scratch = tmp_path / 'pty-state'
    scratch.mkdir()
    access = effective_access(project, {'work': str(work)}, (), activated=True)
    host_master, host_slave = pty.openpty()
    code = '''
import errno, fcntl, os, pty, sys, termios
master, slave = pty.openpty()
os.write(slave, b'private terminal works\\n')
assert b'private terminal works' in os.read(master, 100)
try:
    fd = os.open(sys.argv[1], os.O_WRONLY | os.O_NOCTTY)
except OSError as exc:
    assert exc.errno in (errno.EPERM, errno.EACCES), exc
else:
    os.close(fd)
    raise AssertionError('sandbox can write a host terminal')
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_NOCTTY)
try:
    fcntl.ioctl(fd, termios.TIOCSTI, b'x')
except OSError as exc:
    assert exc.errno in (errno.EPERM, errno.EACCES), exc
else:
    raise AssertionError('sandbox can inject into a host terminal')
finally:
    os.close(fd)
os.close(slave)
os.close(master)
print('private PTY works; host terminal writes and injection denied')
'''
    try:
        result = subprocess.run([SANDBOX, '-p', profile(access, scratch, pseudo_terminal=True),
                                 sys.executable, '-I', '-S', '-c', code, os.ttyname(host_slave)],
                                capture_output=True, text=True, timeout=15)
    finally:
        os.close(host_master)
        os.close(host_slave)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'host terminal writes and injection denied' in result.stdout

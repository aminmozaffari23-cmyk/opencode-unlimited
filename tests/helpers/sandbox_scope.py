#!/usr/bin/env python3
"""Fail closed before offline fixtures can touch a real controller channel.

This is a TEST guard, not a production permission sandbox. Tests still launch
owned local processes. No provider or WARP runtime is needed for these checks.
"""
import importlib.util
import os
from pathlib import Path
import sys


def checked_path(value, root=None):
    root = root or os.environ.get('OUC_TEST_SANDBOX')
    if not root or not value:
        raise RuntimeError('offline fixture requires explicit OUC_TEST_SANDBOX and path')
    base = Path(root).resolve()
    path = Path(value).resolve()
    if base == Path('/') or path == base or base not in path.parents:
        raise RuntimeError(f'offline fixture path escapes its sandbox: {path}')
    return str(path)


def fixture_path(name, *, optional=False):
    value = os.environ.get(name)
    # Version discovery must never create state (or require provider setup).
    if sys.argv[1:] == ['--version']:
        return value or os.devnull
    if optional and not value:
        return ''
    return checked_path(value)


def assert_config_scope(cfg, root):
    names = ('state_dir', 'config_file', 'state_file', 'state_lock',
             'rotation_lock', 'managed_lock', 'pid_file', 'daemon_lock',
             'instance_lock', 'supervisor_log', 'watchdog_log', 'heartbeat',
             'target_home', 'opencode_log', 'hosts_file', 'user_state_dir',
             'journal_file', 'journal_md', 'run_status_file', 'agent_lock_file',
             'hop_request', 'hop_result', 'hop_fallback_dir',
             'hop_fallback_request', 'hop_fallback_result')
    for name in names:
        checked_path(getattr(cfg, name), root)
    return names


def main():
    if len(sys.argv) != 3:
        raise SystemExit('Usage: sandbox_scope.py OUC_SOURCE TEST_ROOT')
    module_path, root = sys.argv[1:]
    spec = importlib.util.spec_from_file_location('scope_check_ouc', module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    cfg = module.Config()
    names = assert_config_scope(cfg, root)
    for name in ('UNIT_PATH', 'BIN_PATH', 'LIB_DIR'):
        checked_path(getattr(module, name), root)
    helper_dir = Path(__file__).resolve().parent
    for name in ('warp_bin', 'iptables', 'ip6tables', 'oc_bin', 'probe_cmd'):
        path = Path(getattr(cfg, name)).resolve()
        if helper_dir not in path.parents or not path.name.startswith('fake_'):
            raise RuntimeError(f'offline fixture executable is not a shipped fake: {name}')
    print(f'OFFLINE_SCOPE_OK {len(names)} controller paths; all external commands are explicit fakes')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Fault-injection native fixture. Never opens a socket or calls WARP.

An explicit test-owned wrapper supplies BOTH paths; there is no fallback path.
Task, verification and diagnostic streams are separate to expose contamination.
"""
import json
import os
from pathlib import Path
import sys
import time
from sandbox_scope import checked_path


def main(state_file, sandbox):
    argv = sys.argv[1:]
    if argv == ['--version']:
        print('opencode v2.2.7')
        return 0
    path = Path(checked_path(state_file, sandbox))
    state = json.loads(path.read_text())
    diagnostic = '--agent' in argv and argv[argv.index('--agent')+1] == 'ouc-diagnostic'
    verify = any('Reply with exactly: OUC-VERIFY-OK' in a for a in argv)
    kind = 'diagnostic' if diagnostic else ('verify' if verify else 'task')
    counter = kind + '_calls'
    i = state.get(counter, 0)
    script = state.get(kind + '_script') or ['ok']
    outcome = script[min(i, len(script)-1)]
    state[counter] = i+1
    keys = ('HOME', 'PWD', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME',
            'XDG_CACHE_HOME', 'HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy',
            'NO_PROXY', 'no_proxy', 'OPENCODE_CONFIG_CONTENT', 'OPENCODE_CONFIG',
            'RESILIENCE_TASK_SECRET', 'OPENAI_API_KEY')
    profile = Path(os.environ.get('XDG_CONFIG_HOME', '/missing'))/'opencode/opencode.json'
    config = json.loads(profile.read_text()) if profile.is_file() else None
    state.setdefault('invocations', []).append({
        'kind': kind, 'outcome': outcome, 'pid': os.getpid(), 'argv': argv, 'cwd': os.getcwd(),
        'environment': {k: os.environ.get(k) for k in keys}, 'config': config})
    path.write_text(json.dumps(state))
    session = argv[argv.index('-s')+1] if '-s' in argv else ('ses_diagnostic_fixture' if diagnostic else 'ses_primary_fixture')
    def emit(typ, **kw):
        print(json.dumps(dict(type=typ, timestamp=1, sessionID=session, **kw)), flush=True)
    emit('step_start', part={'type': 'step-start'})
    if outcome == 'stall':
        time.sleep(10)
        return 0
    if outcome == 'truncated':
        return 0
    if outcome == 'exit':
        return 5
    errors = {'network': ('provider.transport', 'fetch failed: ECONNRESET'),
              'bare-transport': ('provider.transport', ''),
              'quota': ('api.error', 'Free usage limit reached'),
              'rate': ('api.error', '429 Too Many Requests'),
              'fatal': ('api.error', 'invalid api key'),
              'unknown': ('api.error', 'unexpected provider failure')}
    if outcome in errors:
        code, message = errors[outcome]
        emit('error', error={'type': code, 'message': message})
        return 0
    if outcome == 'tool':
        emit('tool_use', part={'type': 'tool', 'tool': 'shell', 'state': {'status': 'completed', 'output': 'SHOULD NOT RUN'}})
    reply = 'OUC-DIAGNOSTIC-OK' if diagnostic else ('OUC-VERIFY-OK' if verify else 'OUC-RESILIENCE-OK')
    if outcome == 'wrong-marker':
        reply = 'not the required marker'
    emit('text', part={'type': 'text', 'text': reply})
    emit('step_finish', part={'type': 'step-finish', 'reason': 'stop'})
    return 0

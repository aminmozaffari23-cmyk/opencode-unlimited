#!/usr/bin/env python3
"""SIMULATED OpenCode v2 native events. Never contacts WARP or a provider.

OUC_V2_FIXTURE is a test-owned JSON state file with a script of outcomes.
--version is side-effect free, like the real CLI. This is not live quota proof.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from sandbox_scope import fixture_path


def emit(typ, session, **kw):
    print(json.dumps(dict(type=typ, timestamp=1, sessionID=session, **kw)), flush=True)


def main():
    argv = sys.argv[1:]
    if argv == ['--version']:
        print('opencode v2.2.7')
        return 0
    path = Path(fixture_path('OUC_V2_FIXTURE'))
    state = json.loads(path.read_text())
    i = state.get('calls', 0)
    script = state.get('script') or ['ok']
    outcome = script[min(i, len(script)-1)]
    state['calls'] = i+1
    state.setdefault('invocations', []).append({
        'argv': argv, 'cwd': os.getcwd(), 'PWD': os.environ.get('PWD'),
        'proxy': {k: os.environ.get(k) for k in ('HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy','NO_PROXY','no_proxy')}})
    path.write_text(json.dumps(state))
    session = argv[argv.index('-s')+1] if '-s' in argv else 'ses_primary_fixture'
    emit('step_start', session, part={'type':'step-start'})
    if outcome == 'truncated':
        return 0
    if outcome == 'stall':
        time.sleep(5)
        return 0
    if outcome == 'exit':
        return 5
    if outcome in ('quota','rate','network','fatal','unknown','marker-error'):
        messages = {'quota':'Free usage limit reached', 'rate':'429 Too Many Requests',
                    'network':'fetch failed: ECONNRESET', 'fatal':'invalid api key',
                    'unknown':'unexpected provider failure', 'marker-error':'unexpected provider failure'}
        if outcome == 'marker-error':
            emit('text', session, part={'type':'text','text':'OUC-VERIFY-OK'})
        emit('error', session, error={'type':'api.error','message':messages[outcome]})
        return int(state.get('error_exit', 0))  # error events can arrive with exit 0
    reply = 'OUC-VERIFY-OK' if 'Reply with exactly: OUC-VERIFY-OK' in argv else '429K saved; documentation example: Free usage limit reached'
    emit('text', session, part={'type':'text','text':reply})
    emit('step_finish', session, part={'type':'step-finish','reason':'stop'})
    return 0


if __name__ == '__main__':
    sys.exit(main())

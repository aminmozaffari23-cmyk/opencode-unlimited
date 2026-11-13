#!/usr/bin/env python3
"""Reproducible OFFLINE monkey/concurrency tests; no real usage is exhausted.

Actual controller subprocesses use fail-closed test-owned native/WARP/probe
fixtures. Loopback listeners satisfy bridge-health checks only; they carry no
provider traffic. JSON evidence records the seed, oracle, argv and native logs.
"""
import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('OUC_TEST_MODULE', str(ROOT/'ouc.py'))).resolve()
HELPERS = ROOT/'tests/helpers'
PRIMARY = 'opencode/minimax-m3.1-flash-free'
SECONDARY = 'opencode/beam-free'
sys.path.insert(0, str(HELPERS))


def digest(path):
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def protected_channels():
    return {p: digest(p) for p in ('/tmp/opencode-unlimited/hop.request',
        '/var/lib/opencode-unlimited/config.json', '/var/lib/opencode-unlimited/state.json',
        '/home/user/.local/state/opencode-unlimited/hop.request', '/etc/hosts')}


def reference(script, diagnostics, verification, limit=8):
    """Independent small policy oracle, not calls to implementation methods."""
    n = 0; failures = 0; network = 0; checked = False
    diagnoses = 0; verify_calls = 0; rotations = 0
    while n < 80:
        outcome = script[min(n, len(script)-1)]; n += 1
        if outcome == 'ok':
            return dict(rc=0, task_calls=n, diagnostic_calls=diagnoses, verify_calls=verify_calls, rotations=rotations)
        failures += 1
        if outcome == 'fatal':
            break
        if outcome in ('unknown','truncated','exit'):
            network = 0; checked = False
            if n >= 2:
                break
            continue
        if failures >= limit:
            break
        if outcome in ('network','bare-transport'):
            network += 1
            if network <= 3:
                continue
            if not checked:
                diag = diagnostics[min(diagnoses, len(diagnostics)-1)]; diagnoses += 1
                if diag == 'ok':
                    checked = True
                    continue
        else:
            network = 0; checked = False
            if outcome == 'rate':
                continue
        verdict = verification[min(verify_calls, len(verification)-1)]; verify_calls += 1
        if verdict != 'ok':
            break
        rotations += 1
        network = 0; checked = False
    return dict(rc=1, task_calls=n, diagnostic_calls=diagnoses, verify_calls=verify_calls, rotations=rotations)


def make_environment(root, port, task_script, diagnostic_script, verify_script):
    env = {k:v for k,v in os.environ.items() if not k.startswith(('OUC_','FAKE_','OPENCODE_'))}
    root = Path(root)
    (root/'home').mkdir(); (root/'state').mkdir(); (root/'project').mkdir()
    (root/'hosts').write_text('127.0.0.1 localhost\n'); (root/'native.log').touch()
    (root/'task.txt').write_text('- perform the harmless simulated task\n')
    fixture = root/'native.json'
    fixture.write_text(json.dumps(dict(task_script=task_script, diagnostic_script=diagnostic_script, verify_script=verify_script)))
    (root/'warp.json').write_text(json.dumps(dict(connected=True, registered=True, reg_count=1)))
    wrapper = root/'fake-native.py'
    wrapper.write_text('#!/usr/bin/env python3\nimport sys\n'+f'sys.path.insert(0,{str(HELPERS)!r})\n'+
        'from fake_resilience import main\n'+f'sys.exit(main({str(fixture)!r},{str(root)!r}))\n')
    wrapper.chmod(0o755)
    env.update(OUC_STATE_DIR=str(root/'state'), OUC_USER_STATE_DIR=str(root/'user-state'),
        OUC_TARGET_HOME=str(root/'home'), OUC_TEST_SANDBOX=str(root), OUC_LOG_PATH=str(root/'native.log'),
        OUC_PREFIX=str(root/'prefix'), OUC_UNIT_DIR=str(root/'units'), OUC_BIN_PATH=str(root/'prefix/bin/ouc'),
        OUC_LIB_DIR=str(root/'prefix/lib/opencode-unlimited'), OUC_INSTANCE_LOCK=str(root/'instance.lock'),
        OUC_HOSTS_FILE=str(root/'hosts'), OUC_OC_BIN=str(wrapper), OUC_WARP_MODE='proxy',
        OUC_PROXY_SOCKS_PORT=str(port), OUC_PROXY_HTTP_PORT=str(port),
        OUC_WARP_BIN=str(HELPERS/'fake_warp.py'), OUC_PROBE_CMD=str(HELPERS/'fake_probe.sh'),
        OUC_IPTABLES=str(HELPERS/'fake_iptables.py'), OUC_IP6TABLES=str(HELPERS/'fake_iptables.py'),
        FAKE_WARP_STATE=str(root/'warp.json'), FAKE_WARP_LOG=str(root/'warp.log'),
        FAKE_IPT_STATE=str(root/'firewall.json'), FAKE_IPT_LOG=str(root/'firewall.log'),
        OUC_MIN_ROTATE_GAP='0', OUC_ROTATE_GAP_JITTER='0', OUC_KEYS_ATTEMPTS='1',
        OUC_ROTATION_MODE='keys', OUC_VERIFY='1', OUC_VERIFY_TIMEOUT='3',
        OUC_PIN='0', OUC_WATCHDOG='0', OUC_MAX_CONSEC_FAIL='8', OUC_MAX_STEP_ATTEMPTS='2',
        OUC_RATE_LIMIT_BACKOFF='0', OUC_TRANSPORT_RETRY_DELAY='0', OUC_TRANSPORT_RETRIES='3',
        OUC_TRANSPORT_DIAGNOSTIC_MODEL=SECONDARY, OUC_TRANSPORT_DIAGNOSTIC_TIMEOUT='2',
        OUC_STALL_SECONDS='10', OUC_STALL_KILL_GRACE='1', OUC_TEST_MODEL=PRIMARY,
        OUC_RESUME_MODEL=PRIMARY, PWD='/deliberately/stale')
    return env, fixture


def command(root):
    # The first process only validates scope and execs the unmodified CLI. It
    # neither substitutes a classifier nor changes a product method.
    validate = ('import importlib.util,sys,os;sys.path.insert(0,sys.argv[1]);'
        'from sandbox_scope import assert_config_scope;'
        's=importlib.util.spec_from_file_location("scope",sys.argv[2]);'
        'm=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m);'
        'c=m.Config();assert_config_scope(c,sys.argv[3]);'
        'assert all("tests/helpers/fake_" in str(getattr(c,k)) for k in ("warp_bin","iptables","ip6tables","probe_cmd"));'
        'os.execv(sys.executable,[sys.executable,sys.argv[2],*sys.argv[4:]])')
    return [sys.executable,'-c',validate,str(HELPERS),str(SOURCE),str(root),'agent',
            '--task',str(root/'task.txt'),'--dir',str(root/'project'),'-m',PRIMARY]


def validate_invocations(data, root):
    for i in data.get('invocations', []):
        argv = i['argv']; env = i['environment']
        model = argv[argv.index('-m')+1]
        if i['kind'] == 'diagnostic':
            assert model == SECONDARY
            assert '--auto' not in argv and '-s' not in argv and '-c' not in argv
            assert not str(i['cwd']).startswith(str(root))
            assert {'action':'tool.use','resource':'*','effect':'deny'} in i['config']['policies']
        else:
            assert model == PRIMARY
            assert i['cwd'] == str(root/'project')
            if i['kind'] == 'task':
                assert '--auto' in argv
                if data['invocations'].index(i) > 0:
                    assert '-s' in argv and 'ses_primary_fixture' in argv
            else:
                assert '--auto' not in argv and '-s' not in argv
        assert i['cwd'] == env['PWD']
        assert all(env[k] == env['HTTP_PROXY'] for k in ('HTTPS_PROXY','http_proxy','https_proxy'))


def run_case(index, script, diagnoses, verification, evidence):
    t = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='ouc-stress-transport-') as td, socket.socket() as listener:
        root = Path(td); listener.bind(('127.0.0.1',0)); listener.listen(256)
        env, fixture = make_environment(root, listener.getsockname()[1], script, diagnoses, verification)
        cmd = command(root)
        p = subprocess.Popen(cmd,env=env,cwd=root/'project',stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True)
        tree = CONTROLLER.OwnedProcessTree(p)
        try:
            output, _ = p.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            tree.collect()  # identity-scoped capture BEFORE signalling parent
            p.send_signal(signal.SIGTERM)
            try:
                output, _ = p.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                tree.stop(grace=1)
                output, _ = p.communicate(timeout=3)
            (evidence/f'{index:03d}-timeout.log').write_text(output)
            raise RuntimeError(f'scoped simulated case {index} exceeded the 120s external deadline')
        finally:
            tree.stop(grace=1)
            if p.stdout:
                p.stdout.close()
        data = json.loads(fixture.read_text())
        journal = json.loads((root/'user-state/journal.json').read_text())
        state = json.loads((root/'state/state.json').read_text())
        expected = reference(script, diagnoses, verification)
        actual = dict(rc=p.returncode,task_calls=data.get('task_calls',0),diagnostic_calls=data.get('diagnostic_calls',0),
                      verify_calls=data.get('verify_calls',0),rotations=state.get('hops',0))
        validate_invocations(data, root)
        assert actual == expected, (index,script,diagnoses,verification,actual,expected,output)
        assert (journal['reason']=='done') == (p.returncode==0)
        assert journal['budgets_used']['consec_fail'] <= 8
        assert not (root/'firewall.log').exists(), 'proxy controller ran a firewall command'
        assert (root/'hosts').read_text() == '127.0.0.1 localhost\n'
        if all(x in ('network','bare-transport','rate','ok') for x in script):
            assert journal['budgets_used']['walls'] == 0
            assert not state.get('blacklist'), 'transport-only recovery marked spent quota'
        result = dict(case=index,simulated=True,script=script,diagnoses=diagnoses,verification=verification,
                      actual=actual,expected=expected,seconds=round(time.monotonic()-t,3),
                      context_valid=True,firewall_commands=0)
        (evidence/f'{index:03d}.json').write_text(json.dumps(dict(result=result,journal=journal,invocations=data['invocations']),indent=2)+'\n')
        (evidence/f'{index:03d}.log').write_text(output)
        return result


def parser_monkey(seed, count=4000):
    spec=importlib.util.spec_from_file_location('monkey_ouc',SOURCE);m=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=m;spec.loader.exec_module(m)
    global CONTROLLER
    CONTROLLER = m
    rng=random.Random(seed); total_bytes=0
    noise=['429K','Free usage limit reached','ECONNRESET','level=ERROR','401 Unauthorized','provider.transport']
    errors=[('provider.transport','', 'network'),('api.error','Free usage limit reached','quota'),
            ('api.error','429 Too Many Requests','rate'),('api.error','invalid api key','fatal'),
            ('api.error','unexpected provider failure','unknown')]
    for index in range(count):
        selected=errors[rng.randrange(len(errors))] if rng.random()<.5 else None
        records=[dict(type='text',sessionID='ses_noise',part=dict(text=rng.choice(noise))),
                 dict(type='tool_use',sessionID='ses_noise',part=dict(tool='shell',state=dict(status='error',error=rng.choice(noise))))]
        if selected:
            records.append(dict(type='error',sessionID='ses_primary',error=dict(type=selected[0],message=selected[1])))
        records.append(dict(type='text',sessionID='ses_later_noise',part=dict(text=rng.choice(noise))))
        blob=('\n'.join(json.dumps(x) for x in records)+'\n').encode();total_bytes+=len(blob)
        event=m.NativeEvents(); pos=0
        while pos<len(blob):
            size=rng.randint(1,97);event.feed(blob[pos:pos+size]);pos+=size
        event.feed(b'',final=True)
        assert (event.failure or {}).get('class') == (selected[2] if selected else None)
        assert event.tool_events == 1
        if selected:
            assert event.failure['session']=='ses_primary'
    return dict(cases=count,seed=seed,bytes=total_bytes,random_chunk_boundaries=True,failures=0)


def active(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(') ',1)[1].split()[0] not in ('Z','X')
    except OSError:
        return False


def contention_and_cancel(evidence, contenders=16):
    with tempfile.TemporaryDirectory(prefix='ouc-stress-lease-') as td,socket.socket() as listener:
        root=Path(td);listener.bind(('127.0.0.1',0));listener.listen(256)
        env,fixture=make_environment(root,listener.getsockname()[1],['stall','ok'],['ok'],['ok'])
        cmd=command(root); unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
        with (evidence/'lease-owner.log').open('w') as sink:
            owner=subprocess.Popen(cmd,env=env,cwd=root/'project',stdin=subprocess.DEVNULL,stdout=sink,stderr=sink)
            try:
                end=time.monotonic()+10
                while time.monotonic()<end:
                    if json.loads(fixture.read_text()).get('task_calls'):break
                    assert owner.poll() is None
                    time.sleep(.01)
                assert json.loads(fixture.read_text()).get('task_calls')==1
                with concurrent.futures.ThreadPoolExecutor(max_workers=contenders) as pool:
                    refused=list(pool.map(lambda _:subprocess.run(cmd,env=env,cwd=root/'project',capture_output=True,text=True,timeout=15),range(contenders)))
                assert all(p.returncode==1 and 'another managed run owns recovery' in p.stderr for p in refused)
                assert json.loads(fixture.read_text())['task_calls']==1
                owner.send_signal(signal.SIGTERM);assert owner.wait(timeout=8)==143
                assert unrelated.poll() is None
                journal=json.loads((root/'user-state/journal.json').read_text())
                assert journal['reason']=='cancelled' and journal['steps'][0]['state']=='interrupted'
                pid=json.loads(fixture.read_text())['invocations'][0]['pid']
                assert not active(pid), 'owned native child survived cancellation'
                resumed=subprocess.run(cmd,env=env,cwd=root/'project',capture_output=True,text=True,timeout=10)
                assert resumed.returncode==0
                data=json.loads(fixture.read_text());assert data['task_calls']==2
                (evidence/'lease-resume.log').write_text(resumed.stdout+resumed.stderr)
                return dict(contenders=contenders,duplicate_native_calls=0,cancel_exit=143,
                            owned_native_cleaned=True,unrelated_process_preserved=True,lease_reacquired=True)
            finally:
                if owner.poll() is None:owner.terminate();owner.wait(timeout=8)
                unrelated.terminate();unrelated.wait()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--evidence',required=True);ap.add_argument('--cases',type=int,default=96)
    ap.add_argument('--workers',type=int,default=12);ap.add_argument('--seed',type=int,default=20261108)
    args=ap.parse_args();evidence=Path(args.evidence);evidence.mkdir(parents=True,exist_ok=False)
    for p in HELPERS.glob('fake_*'):
        if p.suffix in ('.py','.sh'):p.chmod(p.stat().st_mode|0o111)
    before=protected_channels();t=time.monotonic();parser=parser_monkey(args.seed)
    rng=random.Random(args.seed)
    scripts=[(['network']*n+['ok'],['ok'],['ok']) for n in range(1,7)]
    scripts += [(['network']*4+['ok'],[d],['ok']) for d in ('network','fatal','rate','tool','wrong-marker','truncated')]
    scripts += [(['network'],['network'],['ok']),(['quota'],['ok'],['ok']),(['rate'],['ok'],['ok']),
                (['network']*5+['ok'],['ok'],['rate']),(['bare-transport']*4+['ok'],['network'],['ok'])]
    choices=['network','network','network','bare-transport','rate','quota','unknown','truncated','exit']
    while len(scripts)<args.cases:
        prefix=[rng.choice(choices) for _ in range(rng.randint(1,9))]
        scripts.append((prefix+[rng.choice(['ok','fatal'])],[rng.choice(['ok','network','fatal','tool'])],
                        [rng.choice(['ok','ok','ok','rate','network'])]))
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending=[pool.submit(run_case,i,*spec,evidence) for i,spec in enumerate(scripts)]
        for done in concurrent.futures.as_completed(pending):
            results.append(done.result())
    contention=contention_and_cancel(evidence)
    after=protected_channels();assert after==before,'shared channel changed'
    summary=dict(simulated=True,source_sha256=digest(SOURCE),seed=args.seed,parser_monkey=parser,
        subprocess_cases=len(results),concurrent_workers=args.workers,process_results=sorted(results,key=lambda x:x['case']),
        contention=contention,shared_channels_unchanged=True,failures=0,seconds=round(time.monotonic()-t,3),
        real_provider_requests=0,real_warp_mutations=0,ten_hour_soak=False)
    (evidence/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='process_results'},indent=2))


if __name__=='__main__':
    main()

#!/usr/bin/env python3
"""Offline resilience regressions. All provider failures/rotations are simulated.

Uses explicit test-owned paths and fail-closed fake executables. No real quota
is consumed. Run against OUC_TEST_MODULE=older/ouc.py to reproduce failures.
"""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import test_runner_v2 as harness

o, event, ROOT = harness.o, harness.event, harness.ROOT

PRIMARY = 'opencode/minimax-m3.1-flash-free'
SECONDARY = 'opencode/beam-free'


class TransportRegression(unittest.TestCase):
    setUp = harness.RunnerRegression.setUp
    tearDown = harness.RunnerRegression.tearDown
    script = harness.RunnerRegression.script
    follower = harness.RunnerRegression.follower

    def native(self, task, diagnostic=('ok',), verify=('ok',)):
        path = self.root/'resilience-native.json'
        path.write_text(json.dumps({'task_script': list(task), 'diagnostic_script': list(diagnostic),
                                    'verify_script': list(verify)}))
        self.cfg.oc_bin = self.script(
            f'import sys\nsys.path.insert(0,{str(ROOT/"tests/helpers")!r})\n'
            'from fake_resilience import main\n'
            f'sys.exit(main({str(path)!r},{str(self.root)!r}))\n', 'fake-native.py')
        self.cfg.agent_model = PRIMARY
        self.cfg.resume_model = PRIMARY
        self.cfg.transport_retries = 3
        self.cfg.transport_retry_delay = 0
        self.cfg.transport_diagnostic_model = SECONDARY
        self.cfg.transport_diagnostic_timeout = 1
        self.cfg.rate_backoff = [0]
        self.cfg.stall_seconds = 3
        self.cfg.stall_kill_grace = 1
        self.cfg.max_step_attempts = 2
        self.cfg.agent_root = str(self.root/'project')
        Path(self.cfg.agent_root).mkdir(exist_ok=True)
        taskfile = self.root/'task.txt'; taskfile.write_text('- make harmless output\n')
        args = argparse.Namespace(task=str(taskfile), max_hours=0, model=PRIMARY,
                                  dir=self.cfg.agent_root, fresh=True, goal='fault injection', args=[])
        return args, path

    def execute(self, script, diagnostic=('ok',), *, mode='agent', verify=('ok',), health_ok=True, max_fail=60):
        args, path = self.native(script, diagnostic, verify)
        self.cfg.max_consec_fail = max_fail
        rotations = []
        def health(cfg, log, state, **kw):
            rotations.append(kw)
            self.assertEqual(kw['model'], PRIMARY)
            self.assertEqual(kw['cwd'], self.cfg.agent_root if mode == 'agent' else os.getcwd())
            self.assertFalse(kw.get('quota_evidence', True))
            self.assertTrue(kw.get('require_verified'))
            if health_ok:
                state.update(hops=int(state.load().get('hops') or 0)+1)
            return {'ok': health_ok, 'verified': health_ok, 'attempts': 1,
                    'block': '2001:db8:2:2', 'detail': 'simulated verification'}
        with patch.object(o, 'rotate_until_healthy', side_effect=health), \
             patch.object(o, '_sleep_quietly'), contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            if mode == 'agent':
                rc = o.cmd_agent(args, self.cfg, self.log)
            elif mode == 'run':
                rc = o.cmd_run(argparse.Namespace(args=['--','-m',PRIMARY,'harmless task']), self.cfg, self.log)
            else:
                self.cfg.verify = True
                rc = o.cmd_resume(argparse.Namespace(session='ses_primary_fixture',model=PRIMARY,prompt='continue harmless task'), self.cfg, self.log)
        journal = json.loads(Path(self.cfg.journal_file).read_text()) if Path(self.cfg.journal_file).exists() else None
        return rc, json.loads(path.read_text()), journal, rotations

    def assert_context(self, data, *, mode='agent'):
        task = [i for i in data['invocations'] if i['kind'] == 'task']
        for index, call in enumerate(task):
            self.assertIn(PRIMARY, call['argv'])
            self.assertEqual(call['cwd'], self.cfg.agent_root if mode == 'agent' else os.getcwd())
            self.assertEqual(call['environment']['PWD'], call['cwd'])
            if index or mode == 'resume':
                self.assertIn('-s', call['argv'])
                self.assertIn('ses_primary_fixture', call['argv'])
                self.assertNotIn('ses_diagnostic_fixture', call['argv'])
            self.assertEqual('--auto' in call['argv'], mode == 'agent')
        return task

    def test_bare_transport_code_is_network_not_unknown(self):
        self.assertEqual(o.classify_failure(event({'type':'provider.transport','message':''}))['class'], 'network')

    def test_defaults_are_three_additional_attempts(self):
        cfg = o.Config()
        self.assertEqual(cfg.transport_retries, 3)
        self.assertEqual(cfg.transport_diagnostic_model, cfg.test_model)
        self.assertEqual(cfg.as_dict()['transport_retries'], 3)
        self.assertEqual((cfg.max_hours,cfg.max_consec_fail,cfg.min_rotate_gap,cfg.rotate_gap_jitter), (10,60,15,5))
        self.assertEqual((cfg.max_rotations,cfg.max_rotate_per_hour,cfg.resume_max), (0,0,0))

    def test_each_of_three_retries_can_succeed_without_diagnosis(self):
        for n in (1,2,3):
            with self.subTest(retries=n):
                rc, data, journal, rotations = self.execute(['network']*n+['ok'])
                self.assertEqual((rc,data['task_calls'],data.get('diagnostic_calls',0),len(rotations)), (0,n+1,0,0))
                self.assertEqual(journal['budgets_used']['transport_retries'], n)
                self.assert_context(data)

    def test_four_failures_then_healthy_diagnosis_retries_primary(self):
        rc, data, journal, rotations = self.execute(['network']*4+['ok'])
        self.assertEqual((rc,data['task_calls'],data['diagnostic_calls'],len(rotations)), (0,5,1,0))
        self.assertEqual([i['kind'] for i in data['invocations']], ['task']*4+['diagnostic','task'])
        self.assert_context(data)
        b = journal['budgets_used']
        self.assertEqual((b['walls'],b['resumes'],b['rotations'],b['diagnostics'],b['transport_retries'],b['transport_primary_checks']), (0,0,0,1,3,1))
        self.assertEqual(b['attempts'], 5)
        self.assertTrue(journal['transport_events'])

    def test_failed_diagnosis_rotates_then_retries_only_primary(self):
        rc, data, journal, rotations = self.execute(['network']*4+['ok'], ['network'])
        self.assertEqual((rc,data['task_calls'],data['diagnostic_calls'],len(rotations)), (0,5,1,1))
        self.assert_context(data)
        b = journal['budgets_used']
        self.assertEqual((b['walls'],b['rotations'],b['transport_rotations'],b['diagnostics'],b['transport_retries']), (0,1,1,1,3))

    def test_primary_still_failing_after_healthy_diagnosis_rotates(self):
        rc, data, journal, rotations = self.execute(['network']*5+['ok'])
        self.assertEqual((rc,data['task_calls'],data['diagnostic_calls'],len(rotations)), (0,6,1,1))
        self.assert_context(data)
        self.assertEqual(journal['reason'], 'done')

    def test_secondary_success_cannot_mark_primary_task_done(self):
        rc, data, journal, rotations = self.execute(['network']*4+['fatal'])
        self.assertEqual((rc,data['task_calls'],data['diagnostic_calls'],len(rotations)), (1,5,1,0))
        self.assertEqual(journal['reason'], 'fatal')
        self.assertNotEqual(journal['steps'][0]['state'], 'done')

    def test_transport_failure_limit_is_not_reset_by_diagnosis_or_rotation(self):
        rc, data, journal, rotations = self.execute(['network'], ['network'], max_fail=8)
        self.assertEqual(rc, 1)
        self.assertEqual(data['task_calls'], 8)
        self.assertEqual(journal['budgets_used']['consec_fail'], 8)
        self.assertIn('consecutive-failures', journal['reason'])
        self.assertEqual(len(rotations), 1)

    def test_failure_budget_stops_before_diagnosis(self):
        rc, data, journal, rotations = self.execute(['network'], max_fail=3)
        self.assertEqual((rc,data['task_calls'],data.get('diagnostic_calls',0),len(rotations)), (1,3,0,0))
        self.assertEqual(journal['budgets_used']['consec_fail'], 3)

    def test_failed_primary_verification_is_not_a_successful_recovery(self):
        rc, data, journal, rotations = self.execute(['network']*4+['ok'], ['network'], health_ok=False)
        self.assertEqual((rc,data['task_calls'],len(rotations)), (1,4,1))
        self.assertNotEqual(journal['reason'], 'done')
        self.assertNotEqual(journal['steps'][0]['state'], 'done')

    def test_rate_and_fatal_are_not_transport_diagnoses(self):
        for outcome, expected in (('rate',0),('fatal',1)):
            rc, data, journal, rotations = self.execute([outcome,'ok'])
            self.assertEqual(rc, expected)
            self.assertEqual(data.get('diagnostic_calls',0), 0)
            self.assertEqual(rotations, [])

    def test_native_run_uses_same_transport_policy_without_auto(self):
        rc, data, journal, rotations = self.execute(['network']*5+['ok'],mode='run')
        self.assertEqual((rc,data['task_calls'],data['diagnostic_calls'],len(rotations)), (0,6,1,1))
        self.assertIsNone(journal)
        self.assert_context(data, mode='run')

    def test_explicit_resume_task_uses_same_policy_without_auto(self):
        rc, data, journal, rotations = self.execute(['network']*4+['ok'],['network'],mode='resume')
        self.assertEqual((rc,data['task_calls'],data['diagnostic_calls'],len(rotations)), (0,5,1,1))
        self.assert_context(data, mode='resume')

    def test_resume_preflight_has_three_retries_then_diagnosis(self):
        rc, data, journal, rotations = self.execute(['ok'],['network'],mode='resume',verify=['network']*4+['ok'])
        self.assertEqual(rc, 0)
        self.assertEqual(data['diagnostic_calls'], 1)
        self.assertEqual(len(rotations), 1)
        self.assertEqual(data['task_calls'], 1)
        self.assert_context(data, mode='resume')

    def diagnostic(self, outcomes=('ok',), *, model=PRIMARY, seconds=3):
        args, path = self.native(['ok'],outcomes)
        result = o.transport_diagnostic(self.cfg,self.log,model=model,deadline=time.monotonic()+seconds)
        return result,json.loads(path.read_text())

    def test_diagnostic_is_separate_no_tools_no_session_no_task_secrets(self):
        with patch.dict(os.environ, {'RESILIENCE_TASK_SECRET':'TASK-DO-NOT-COPY','OPENAI_API_KEY':'TEST-ONLY-NOT-A-REAL-KEY','OPENCODE_CONFIG_CONTENT':'TASK-DO-NOT-COPY'}):
            result,data = self.diagnostic()
        self.assertTrue(result['ok'])
        call = data['invocations'][0]
        self.assertEqual(call['kind'], 'diagnostic')
        self.assertIn(SECONDARY, call['argv'])
        for option in ('--auto','-s','-c','--continue'):
            self.assertNotIn(option, call['argv'])
        self.assertNotEqual(call['cwd'], self.cfg.agent_root)
        self.assertEqual(call['cwd'], call['environment']['PWD'])
        self.assertIsNone(call['environment']['RESILIENCE_TASK_SECRET'])
        self.assertIsNone(call['environment']['OPENAI_API_KEY'])
        self.assertIsNone(call['environment']['OPENCODE_CONFIG_CONTENT'])
        self.assertNotIn('make harmless output', ' '.join(call['argv']))
        config=call['config']
        self.assertIn({'action':'tool.use','resource':'*','effect':'deny'},config['policies'])
        self.assertIn({'action':'provider.use','resource':'*','effect':'allow'},config['policies'])
        self.assertFalse(any(x['action'].startswith('integration.') and x['effect']=='deny' for x in config['policies']))
        self.assertIn({'action':'*','resource':'*','effect':'deny'},config['permissions'])
        self.assertNotEqual(call['environment']['XDG_DATA_HOME'], os.environ.get('XDG_DATA_HOME'))
        self.assertFalse(Path(call['cwd']).exists())  # diagnostic profile cleaned
        self.assertEqual(self.cfg.agent_model, PRIMARY)

    def test_diagnostic_rejects_any_tool_event_even_with_marker(self):
        result,data = self.diagnostic(['tool'])
        self.assertFalse(result['ok'])
        self.assertEqual(result['failure_class'], 'diagnostic-tools')

    def test_diagnostic_marker_or_completion_alone_is_not_enough(self):
        for outcome in ('wrong-marker','truncated','fatal','rate','quota','network'):
            with self.subTest(outcome=outcome):
                result,data = self.diagnostic([outcome])
                self.assertFalse(result['ok'])

    def test_diagnostic_cannot_use_primary_as_its_secondary(self):
        result,data = self.diagnostic(model=SECONDARY)
        self.assertTrue(result['ok'])
        self.assertNotIn(SECONDARY, data['invocations'][0]['argv'])
        self.assertEqual(self.cfg.transport_diagnostic_model, SECONDARY)

    def test_expired_diagnostic_does_not_launch_any_process(self):
        result,data = self.diagnostic(seconds=-1)
        self.assertFalse(result['ok'])
        self.assertEqual(data.get('diagnostic_calls',0), 0)

    def test_diagnostic_deadline_cleans_owned_process_only(self):
        unrelated = subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'])
        try:
            start=time.monotonic();result,data = self.diagnostic(['stall'],seconds=.25)
            self.assertFalse(result['ok'])
            self.assertLess(time.monotonic()-start, 3)
            self.assertIsNone(unrelated.poll())
        finally:
            unrelated.terminate();unrelated.wait()

    def test_transport_rotation_requires_verified_primary_even_when_verify_disabled(self):
        self.cfg._opencode_v2 = True
        previous = self.cfg.test_model
        def rotate(cfg, log, state, why):
            self.assertTrue(cfg.verify)
            self.assertEqual(cfg.test_model, PRIMARY)
            self.assertFalse(cfg.rotation_quota_evidence)
            return {'ok': True,'verified': False,'new':'2001:db8:2:2::1'}
        with patch.object(o,'rotate',side_effect=rotate) as rotation:
            result = o.rotate_until_healthy(self.cfg,self.log,o.State(self.cfg),deadline=time.monotonic()+2,
                model=PRIMARY,cwd=str(self.root),quota_evidence=False,require_verified=True,max_attempts=1)
        self.assertFalse(result['ok'])
        self.assertEqual(rotation.call_count, 1)
        self.assertEqual(self.cfg.test_model, previous)
        self.assertFalse(self.cfg.verify)

    def test_transport_rotation_does_not_call_old_block_spent_quota(self):
        from unittest.mock import Mock
        self.cfg.rotation_mode='keys';self.cfg.keys_attempts=1
        self.cfg.rotation_quota_evidence=False
        fake=Mock(rotate_keys=lambda:(True,''),connected=lambda:True)
        with patch.object(o,'Warp',return_value=fake),patch.object(o,'Bridge'),patch.object(o,'_wait_rotation_spacing'), \
             patch.object(o,'probe_egress',return_value='2001:db8:1:1::1'), \
             patch.object(o,'_wait_for_egress',return_value='2001:db8:2:2::1'), \
             patch.object(o,'verify_request',return_value={'ran':True,'ok':True}):
            result=o.rotate(self.cfg,self.log,o.State(self.cfg),'transport-recovery')
        self.assertTrue(result['ok'])
        self.assertNotIn('2001:db8:1:1', o.State(self.cfg).load()['blacklist'])

    def test_proxy_firewall_all_entry_points_are_noop(self):
        # The binaries may be installed in a Codespace even in proxy mode.
        # A missing binary would let the older unsafe implementation pass.
        self.cfg.iptables = self.cfg.ip6tables = self.script('raise SystemExit(91)\n', 'unused-iptables.py')
        fw=o.Firewall(self.cfg,self.log)
        with patch.object(o.subprocess,'run') as invocation:
            fw._ipt(False,'-F','OUC_LEAKGUARD')
            fw._ipt(True,'-F','OUC_LEAKGUARD')
            self.assertFalse(fw.install())
            self.assertFalse(fw.installed())
            fw.lift();fw.teardown()
            invocation.assert_not_called()

    def test_proxy_pinner_remove_never_modifies_hosts(self):
        path=Path(self.cfg.hosts_file)
        path.write_text('127.0.0.1 localhost\n'+o.PIN_BEGIN+'\n2001:db8::1 example.invalid\n'+o.PIN_END+'\n')
        before=path.read_bytes()
        self.assertFalse(o.Pinner(self.cfg,self.log).remove())
        self.assertEqual(path.read_bytes(), before)

    def test_transport_exact_session_ignores_latest_preference_only_for_recovery(self):
        with patch.dict(os.environ,{'OUC_RESUME_USE_SESSION_ID':'0'}),patch.object(o,'opencode_is_v2',return_value=True):
            args=o.resume_argv(self.cfg,session='ses_exact',model=PRIMARY,exact=True)
        self.assertIn('ses_exact',args);self.assertIn('-s',args);self.assertNotIn('-c',args)


    def test_quota_failure_budget_stops_before_an_extra_rotation(self):
        args, path = self.native(['quota'])
        self.cfg.max_consec_fail = 3
        def health(cfg, log, state, **kw):
            state.update(hops=int(state.load().get('hops') or 0)+1)
            return {'ok':True,'verified':True,'block':'2001:db8:2:2'}
        with patch.object(o,'rotate_until_healthy',side_effect=health) as rotate, \
             contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            rc=o.cmd_agent(args,self.cfg,self.log)
        self.assertEqual(rc,1)
        self.assertEqual(json.loads(path.read_text())['task_calls'],3)
        self.assertEqual(rotate.call_count,2)
        self.assertEqual(json.loads(Path(self.cfg.journal_file).read_text())['budgets_used']['consec_fail'],3)

    def test_expired_rotation_spacing_releases_lock_without_warp_mutation(self):
        from unittest.mock import Mock
        self.cfg.recovery_deadline = time.monotonic()-.1
        warp=Mock(connected=lambda:True)
        with patch.object(o,'Warp',return_value=warp):
            result=o.rotate(self.cfg,self.log,o.State(self.cfg),'transport-recovery')
        self.assertFalse(result['ok'])
        warp.mode_warp.assert_not_called()
        warp.rotate_keys.assert_not_called()
        warp.registration_delete.assert_not_called()
        import fcntl
        with open(self.cfg.rotation_lock,'a+') as lock:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)

    def test_rotation_spacing_exception_always_releases_lock(self):
        from unittest.mock import Mock
        with patch.object(o,'_wait_rotation_spacing',side_effect=RuntimeError('simulated spacing fault')), \
             patch.object(o,'Warp',return_value=Mock(connected=lambda:True)):
            result=o.rotate(self.cfg,self.log,o.State(self.cfg),'simulated-spacing-fault')
        self.assertFalse(result['ok'])
        import fcntl
        with open(self.cfg.rotation_lock,'a+') as lock:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)


    def test_terminal_error_hanging_cli_is_cleaned_for_retry_not_left_for_stall(self):
        self.cfg._opencode_v2 = True
        child = self.script('import json,time\nprint(json.dumps({"type":"error","sessionID":"ses_primary_fixture","error":{"type":"provider.transport","message":"ECONNRESET"}}),flush=True)\ntime.sleep(10)\n')
        self.cfg.stall_seconds = 8
        result = o.run_child(self.cfg,[child,'--format','json'],dict(os.environ),self.follower(),self.log,
                             deadline=time.monotonic()+4,emit=False)
        self.assertEqual(result['failure']['class'],'network')
        self.assertTrue(result['native_error_terminated'])
        self.assertFalse(result['stalled'])
        self.assertLess(result['duration'],3)


    def test_socks_connect_timeout_is_not_a_stream_inactivity_timeout(self):
        import socket
        import threading
        client, server = socket.socketpair()  # AF_UNIX: no network interface
        errors = []
        def upstream():
            try:
                assert server.recv(3) == b'\x05\x01\x00'
                server.sendall(b'\x05\x00')
                request = server.recv(300)
                assert request[:3] == b'\x05\x01\x00'
                server.sendall(b'\x05\x00\x00\x01' + b'\0'*6)
                time.sleep(.12)  # longer than the 50 ms connect timeout
                server.sendall(b'late-model-response')
            except BaseException as exc:
                errors.append(exc)
            finally:
                server.close()
        thread=threading.Thread(target=upstream);thread.start()
        def connect(*args, **kwargs):
            client.settimeout(kwargs['timeout'])
            return client
        try:
            with patch.object(socket,'create_connection',side_effect=connect):
                stream=o._socks5_connect('127.0.0.1',40000,'example.invalid',443,timeout=.05)
            self.assertIsNone(stream.gettimeout(),'handshake timeout leaked into the provider stream')
            self.assertEqual(stream.recv(1024), b'late-model-response')
        finally:
            thread.join(timeout=2);client.close();server.close()
        self.assertFalse(errors,errors)


if __name__ == '__main__':
    unittest.main()

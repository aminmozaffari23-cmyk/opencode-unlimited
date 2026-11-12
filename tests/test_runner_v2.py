#!/usr/bin/env python3
"""Offline regressions for the v2 runner. Uses only test-owned temp paths/processes.

Run: python3 tests/test_runner_v2.py -v
OUC_TEST_MODULE may point at an older ouc.py to reproduce the pre-fix failures.
Provider quota/rate fixtures below are SIMULATED, not live quota observations.
"""
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE = Path(os.environ.get('OUC_TEST_MODULE', str(ROOT / 'ouc.py')))
spec = importlib.util.spec_from_file_location('ouc_under_test', MODULE)
o = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = o
spec.loader.exec_module(o)
sys.path.insert(0, str(ROOT / 'tests/helpers'))
from sandbox_scope import assert_config_scope


def event(error=None, kind='error', session='ses_fixture', text=None):
    d = {'type': kind, 'sessionID': session, 'timestamp': 1}
    if error is not None:
        d['error'] = error
    if text is not None:
        d['part'] = {'type': 'text', 'text': text}
    return (json.dumps(d) + '\n').encode()


def alive(pid):
    try:
        state = Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()[0]
        return state not in ('Z', 'X')
    except (OSError, IndexError):
        return False


class RunnerRegression(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ouc-v2-unit-')
        self.root = Path(self.tmp.name)
        # No default may resolve to a real CLI, global fallback, or host file.
        stub = self.script('import sys\nif sys.argv[1:]==["--version"]: print("opencode v1.0.0")\nelse: sys.exit(91)\n', 'no-provider.py')
        hosts = self.root / 'hosts'; hosts.write_text('127.0.0.1 localhost\n')
        env = {k: v for k, v in os.environ.items() if not k.startswith(('OUC_', 'FAKE_'))}
        env.update(OUC_STATE_DIR=str(self.root / 'state'),
                   OUC_TARGET_HOME=str(self.root / 'home'),
                   OUC_USER_STATE_DIR=str(self.root / 'user-state'),
                   OUC_TEST_SANDBOX=str(self.root),
                   OUC_LOG_PATH=str(self.root / 'native.log'),
                   OUC_HOSTS_FILE=str(hosts),
                   OUC_OC_BIN=stub, OUC_WARP_BIN=stub, OUC_IPTABLES=stub,
                   OUC_IP6TABLES=stub, OUC_PROBE_CMD=stub,
                   OUC_PROXY_SOCKS_PORT='0', OUC_PROXY_HTTP_PORT='0',
                   OUC_WARP_MODE='proxy', OUC_VERIFY='0', OUC_PIN='0',
                   OUC_WATCHDOG='0', OUC_INSTANCE_LOCK=str(self.root / 'instance.lock'))
        self.env = patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)
        self.cfg = o.Config()
        assert_config_scope(self.cfg, self.root)  # before any controller write
        Path(self.cfg.state_dir).mkdir(parents=True)
        Path(self.cfg.target_home).mkdir(parents=True)
        Path(self.cfg.opencode_log).parent.mkdir(parents=True, exist_ok=True)
        Path(self.cfg.opencode_log).touch()
        self.log = o.Log(str(self.root / 'controller.log'), echo=False)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def script(self, body, name='child.py'):
        p = self.root / name
        p.write_text('#!/usr/bin/env python3\n' + body)
        p.chmod(0o755)
        return str(p)

    def follower(self):
        f = o.LogFollower(self.cfg, o.State(self.cfg))
        f.skip_history()
        return f

    def test_plain_size_is_not_rate_limit(self):
        self.assertIsNone(o.classify_failure(b'screenshot.png 429K\n'))

    def test_svg_coordinates_are_not_rate_limit(self):
        self.assertIsNone(o.classify_failure(b'<path d="M 429 20 L 500 1"/>\n'))

    def test_quoted_quota_in_model_text_is_not_control_event(self):
        self.assertIsNone(o.classify_failure(event(kind='text', text='Example: Free usage limit reached; 429 Too Many Requests')))

    def test_tool_output_error_is_not_provider_failure(self):
        blob = {'type': 'tool_use', 'sessionID': 'ses_fixture', 'part':
                {'type': 'tool', 'state': {'status': 'error', 'error': '429 Free usage limit reached'}}}
        self.assertIsNone(o.classify_failure((json.dumps(blob) + '\n').encode()))

    def test_info_log_quoting_quota_is_not_error(self):
        self.assertIsNone(o.classify_failure(b'level=INFO message="documentation says Free usage limit reached"\n'))

    def test_v2_actual_model_unavailable_shape(self):
        got = o.classify_failure(event({'type': 'provider.no-route', 'message': 'Model unavailable: opencode/not-a-model'}))
        self.assertIsNotNone(got)
        self.assertEqual(got['class'], 'fatal')
        self.assertEqual(got['session'], 'ses_fixture')

    def test_v2_quota_preserves_session(self):
        got = o.classify_failure(event({'name': 'APIError', 'data': {'message': 'Free usage limit reached', 'statusCode': 429}}))
        self.assertEqual(got['class'], 'quota')
        self.assertEqual(got['session'], 'ses_fixture')

    def test_v2_rate_not_quota(self):
        got = o.classify_failure(event({'type': 'api', 'message': 'Too many requests per minute', 'statusCode': 429}))
        self.assertEqual(got['class'], 'rate')
        self.assertEqual(got['session'], 'ses_fixture')

    def test_v2_unknown_error_never_becomes_success(self):
        got = o.classify_failure(event({'type': 'internal', 'message': 'unexpected provider failure'}))
        self.assertIsNotNone(got)
        self.assertEqual(got['class'], 'unknown')

    def test_v2_http_401_is_fatal(self):
        got = o.classify_failure(event({'name': 'APIError', 'data': {'message': 'no access', 'statusCode': 401}}))
        self.assertEqual(got['class'], 'fatal')

    def test_v2_network_error(self):
        got = o.classify_failure(event({'name': 'UnknownError', 'data': {'message': 'fetch failed: ECONNRESET'}}))
        self.assertEqual(got['class'], 'network')

    def test_legacy_real_error_line_still_works(self):
        blob = b'level=ERROR message="stream error" providerID=opencode modelID=beam-free session.id=ses_native error.error="AI_APICallError: Free usage limit reached"\n'
        got = o.classify_failure(blob)
        self.assertEqual(got['class'], 'quota')
        self.assertEqual(got['session'], 'ses_native')
        self.assertEqual(got['model'], 'opencode/beam-free')

    def test_event_metadata_does_not_leak_between_errors(self):
        blob = event({'message': 'Free usage limit reached'}, session='ses_first')
        blob += event(kind='text', session='ses_unrelated', text='later ordinary message')
        got = o.classify_failure(blob)
        self.assertEqual(got['session'], 'ses_first')

    def test_env_reconciles_pwd_with_actual_directory(self):
        with patch.dict(os.environ, {'PWD': '/not/the/current/directory'}):
            env = o.opencode_env(self.cfg)
        self.assertEqual(env['PWD'], os.getcwd())

    def test_all_process_proxy_variables_agree(self):
        before = os.environ.get('NO_PROXY')
        with patch.dict(os.environ, {'NO_PROXY': '*', 'no_proxy': '.opencode.ai', 'HTTPS_PROXY': 'http://wrong.invalid:9'}):
            env = o.opencode_env(self.cfg)
        for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy'):
            self.assertEqual(env[key], self.cfg.http_proxy)
        self.assertEqual(env['NO_PROXY'], 'localhost,127.0.0.1,::1')
        self.assertEqual(env['no_proxy'], env['NO_PROXY'])
        self.assertEqual(os.environ.get('NO_PROXY'), before)  # no global exports

    def test_run_child_reconciles_explicit_cwd_and_pwd(self):
        d = self.root / 'project'; d.mkdir()
        p = self.root / 'observed.json'
        child = self.script(f'import os,json,pathlib\npathlib.Path({str(p)!r}).write_text(json.dumps([os.getcwd(),os.environ.get("PWD")]))\n')
        result = o.run_child(self.cfg, [child], dict(os.environ, PWD='/stale'), self.follower(), self.log, cwd=str(d))
        self.assertEqual(result['rc'], 0)
        self.assertEqual(json.loads(p.read_text()), [str(d), str(d)])

    def test_resume_retains_selected_agent_model(self):
        self.cfg.agent_model = 'opencode/minimax-m3.1-flash-free'
        args = o.resume_argv(self.cfg)
        self.assertIn(self.cfg.agent_model, args)

    def test_restart_does_not_replay_acknowledged_hop(self):
        o.State(self.cfg).update(last_hop_request='already_handled')
        supervisor = o.Supervisor(self.cfg, self.log)
        self.assertEqual(supervisor.last_request, 'already_handled')

    def test_user_state_override_is_complete(self):
        state = self.root / 'isolated-user-state'
        with patch.dict(os.environ, {'OUC_USER_STATE_DIR': str(state)}):
            c = o.Config()
        for name in ('journal_file', 'journal_md', 'run_status_file', 'agent_lock_file', 'hop_request', 'hop_result'):
            self.assertEqual(Path(getattr(c, name)).parent, state, name)
        self.assertEqual(Path(c.user_state_dir), state)

    def test_nonzero_exit_with_verify_marker_is_not_success(self):
        self.cfg.verify = True
        self.cfg.verify_timeout = 3
        self.cfg.oc_bin = self.script('import sys\nprint("OUC-VERIFY-OK")\nsys.exit(1)\n')
        self.assertFalse(o.verify_request(self.cfg, self.log)['ok'])

    def test_budget_elapsed_is_monotonic(self):
        j = o.Journal(self.cfg); j.open('- make a harmless file\n', resume=False)
        b = o.Budget(self.cfg, j)
        before = b.elapsed()
        with patch.object(o.time, 'time', return_value=time.time()+86400):
            self.assertLess(abs(b.elapsed() - before), 1.0)

    def test_inference_curl_quota_hint_removed(self):
        self.assertNotIn('def quota_reset_hint(', MODULE.read_text())
        self.assertNotIn('OUC_QUOTA_HINT_URL', MODULE.read_text())

    def test_background_cpu_is_not_agent_progress(self):
        self.cfg.stall_seconds = 1
        self.cfg.stall_kill_grace = 1
        child = self.script('import time\nend=time.monotonic()+3\nwhile time.monotonic()<end: pass\n')
        result = o.run_child(self.cfg, [child], dict(os.environ), self.follower(), self.log, cwd=str(self.root))
        self.assertTrue(result['stalled'])
        self.assertLess(result['duration'], 3.0)

    def test_stall_cleans_only_owned_descendants(self):
        self.cfg.stall_seconds = 1
        self.cfg.stall_kill_grace = 1
        pidfile = self.root / 'grandchild.pid'
        child = self.script('import subprocess,sys,time,pathlib\n'
                            'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(12)"])\n'
                            f'pathlib.Path({str(pidfile)!r}).write_text(str(p.pid))\n'
                            'time.sleep(12)\n')
        pid = None
        try:
            result = o.run_child(self.cfg, [child], dict(os.environ), self.follower(), self.log, cwd=str(self.root))
            pid = int(pidfile.read_text())
            self.assertTrue(result['stalled'])
            self.assertFalse(alive(pid))
        finally:
            if pid is None and pidfile.exists(): pid = int(pidfile.read_text())
            if pid and alive(pid): os.kill(pid, signal.SIGTERM)

    def test_fake_quota_in_log_does_not_trigger_wall_scanner(self):
        state = o.State(self.cfg)
        o.scan_wall(self.cfg, state, self.log, baseline=True)
        with open(self.cfg.opencode_log, 'ab') as fh:
            fh.write(event(kind='text', text='Free usage limit reached example'))
        self.assertFalse(o.scan_wall(self.cfg, state, self.log)['wall'])

    def test_successful_json_text_not_error_even_with_429(self):
        blob = event(kind='step_start') + event(kind='text', text='429K output saved') + event(kind='step_finish')
        self.assertIsNone(o.classify_failure(blob))


    def fixture(self, script, *, error_exit=0):
        import argparse
        self.cfg.oc_bin = str(ROOT/'tests/helpers/fake_v2.py')
        Path(self.cfg.oc_bin).chmod(0o755)
        self.cfg.agent_model = 'opencode/minimax-m3.1-flash-free'
        self.cfg.resume_model = 'opencode/wrong-default'
        self.cfg.rate_backoff = [0]
        self.cfg.stall_seconds = .3
        self.cfg.stall_kill_grace = 1
        self.cfg.max_step_attempts = 2
        self.cfg.agent_root = str(self.root/'site')
        task = self.root/'task.txt'; task.write_text('- make the fixture output\n')
        state = self.root/'native.json'
        state.write_text(json.dumps({'script':script,'error_exit':error_exit}))
        args = argparse.Namespace(task=str(task), max_hours=0, model=self.cfg.agent_model,
                                  dir=self.cfg.agent_root, fresh=True, goal='offline fixture', args=[])
        return args, state

    def run_fixture(self, script):
        import io
        args, state = self.fixture(script)
        def health(cfg, log, st, **kw):
            self.assertEqual(kw.get('model'), self.cfg.agent_model)
            self.assertEqual(kw.get('cwd'), self.cfg.agent_root)
            st.update(hops=int(st.load().get('hops') or 0)+1)
            return {'ok':True,'verified':True,'block':'2001:db8:1:1'}
        with patch.dict(os.environ, {'OUC_V2_FIXTURE':str(state)}), \
             patch.object(o, 'rotate_until_healthy', side_effect=health) as rotate, \
             patch.object(o, 'wait_for_network', return_value=True), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = o.cmd_agent(args, self.cfg, self.log)
        return rc, json.loads(state.read_text()), json.loads(Path(self.cfg.journal_file).read_text()), rotate.call_count

    def test_native_stream_split_at_every_byte(self):
        e = o.NativeEvents()
        data = event({'message':'Free usage limit reached'})
        for byte in data:
            e.feed(bytes([byte]))
        self.assertEqual(e.failure['class'], 'quota')
        self.assertEqual(e.session, 'ses_fixture')

    def test_unknown_error_shape_fails_closed(self):
        got = o.classify_failure(b'{"type":"error","error":{"newSchema":{"status":429}}}\n')
        self.assertEqual(got['class'], 'unknown')

    def test_partial_legacy_line_is_completed_once(self):
        state = o.State(self.cfg)
        o.scan_wall(self.cfg, state, self.log, baseline=True)
        with open(self.cfg.opencode_log, 'a') as f: f.write('level=ERROR error="Free usage ')
        self.assertFalse(o.scan_wall(self.cfg, state, self.log)['wall'])
        with open(self.cfg.opencode_log, 'a') as f: f.write('limit reached"\n')
        self.assertTrue(o.scan_wall(self.cfg, state, self.log)['wall'])
        self.assertFalse(o.scan_wall(self.cfg, state, self.log)['wall'])

    def test_managed_lease_prevents_background_rotation(self):
        state = o.State(self.cfg); state.update(pending_wall=True)
        lease = o.ManagedRunLease(self.cfg)
        self.assertTrue(lease.acquire())
        try:
            with patch.object(o, 'rotate', side_effect=AssertionError('duplicate rotation')):
                result = o.maybe_rotate_for_wall(self.cfg, state, self.log)
            self.assertEqual(result['skipped'], 'foreground owns recovery')
        finally:
            lease.close()
        self.assertFalse(o.managed_run_active(self.cfg))

    def test_managed_lease_refuses_second_owner(self):
        first = o.ManagedRunLease(self.cfg); second = o.ManagedRunLease(self.cfg)
        self.assertTrue(first.acquire())
        try: self.assertFalse(second.acquire())
        finally: second.close(); first.close()

    def test_v2_private_server_without_blanket_auto(self):
        with patch.object(o, 'opencode_is_v2', return_value=True):
            args = o.opencode_run_argv(self.cfg, ['hello'])
        self.assertIn('--standalone', args)
        self.assertIn('json', args)
        self.assertNotIn('--auto', args)

    def test_v2_known_session_is_exact_not_latest(self):
        with patch.object(o, 'opencode_is_v2', return_value=True):
            args = o.resume_argv(self.cfg, session='ses_exact')
        self.assertIn('-s', args)
        self.assertIn('ses_exact', args)
        self.assertNotIn('-c', args)

    def test_v2_explicit_continue_preference_is_respected(self):
        with patch.dict(os.environ, {'OUC_RESUME_USE_SESSION_ID':'0'}), \
             patch.object(o, 'opencode_is_v2', return_value=True):
            args = o.resume_argv(self.cfg, session='ses_exact')
        self.assertIn('-c', args)
        self.assertNotIn('-s', args)

    def test_simulated_quota_recovery_preserves_context(self):
        rc, native, journal, rotations = self.run_fixture(['quota','ok'])
        self.assertEqual(rc, 0)
        self.assertEqual(rotations, 1)
        self.assertEqual(journal['budgets_used']['walls'], 1)
        self.assertEqual(journal['budgets_used']['resumes'], 1)
        second = native['invocations'][1]
        self.assertIn('ses_primary_fixture', second['argv'])
        self.assertIn('-s', second['argv'])
        self.assertIn('opencode/minimax-m3.1-flash-free', second['argv'])
        self.assertEqual(second['cwd'], self.cfg.agent_root)
        self.assertEqual(second['PWD'], self.cfg.agent_root)

    def test_simulated_rate_does_not_rotate_or_switch_model(self):
        rc, native, journal, rotations = self.run_fixture(['rate','ok'])
        self.assertEqual((rc,rotations), (0,0))
        self.assertIn('opencode/minimax-m3.1-flash-free', native['invocations'][1]['argv'])
        self.assertEqual(journal['budgets_used']['walls'], 0)

    def test_simulated_network_does_not_rotate_or_switch_model(self):
        rc, native, journal, rotations = self.run_fixture(['network','ok'])
        self.assertEqual((rc,rotations), (0,0))
        self.assertIn('opencode/minimax-m3.1-flash-free', native['invocations'][1]['argv'])

    def test_simulated_stall_retains_selected_model(self):
        rc, native, journal, rotations = self.run_fixture(['stall','ok'])
        self.assertEqual((rc,rotations), (0,0))
        self.assertIn('opencode/minimax-m3.1-flash-free', native['invocations'][1]['argv'])
        self.assertEqual(journal['budgets_used']['stalls'], 1)

    def test_simulated_unknown_exit_retains_selected_model(self):
        rc, native, journal, rotations = self.run_fixture(['exit','ok'])
        self.assertEqual((rc,rotations), (0,0))
        self.assertIn('opencode/minimax-m3.1-flash-free', native['invocations'][1]['argv'])

    def test_typed_error_with_exit_zero_cannot_mark_done(self):
        rc, native, journal, rotations = self.run_fixture(['unknown'])
        self.assertEqual((rc,rotations), (1,0))
        self.assertEqual(journal['steps'][0]['state'], 'failed')
        self.assertEqual(native['calls'], 2)

    def test_fatal_with_exit_zero_stops_without_rotation(self):
        rc, native, journal, rotations = self.run_fixture(['fatal'])
        self.assertEqual((rc,rotations), (1,0))
        self.assertEqual(journal['reason'], 'fatal')
        self.assertEqual(native['calls'], 1)

    def test_verify_marker_plus_typed_error_is_not_success(self):
        args, state = self.fixture(['marker-error'])
        self.cfg.verify = True
        with patch.dict(os.environ, {'OUC_V2_FIXTURE':str(state)}):
            result = o.verify_request(self.cfg, self.log)
        self.assertFalse(result['ok'])
        self.assertFalse(result['walled'])
        self.assertEqual(result['failure_class'], 'unknown')

    def test_deadline_stops_owned_call_without_quota_claim(self):
        self.cfg.stall_seconds = 10
        child = self.script('import time\ntime.sleep(5)\n')
        result = o.run_child(self.cfg, [child], dict(os.environ), self.follower(), self.log,
                             deadline=time.monotonic()+.3)
        self.assertTrue(result['deadline_expired'])
        self.assertFalse(result['stalled'])
        self.assertIsNone(result['failure'])
        self.assertLess(result['duration'], 2)

    def test_completed_cli_does_not_wait_for_background_pipe_eof(self):
        pidfile = self.root/'preview.pid'
        child = self.script('import subprocess,sys,pathlib\n'
                            'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(12)"])\n'
                            f'pathlib.Path({str(pidfile)!r}).write_text(str(p.pid))\n')
        unrelated = subprocess.Popen([sys.executable,'-c','import time; time.sleep(12)'])
        try:
            result = o.run_child(self.cfg, [child], dict(os.environ), self.follower(), self.log)
            self.assertEqual(result['rc'], 0)
            self.assertLess(result['duration'], 3)
            self.assertFalse(alive(int(pidfile.read_text())))
            self.assertIsNone(unrelated.poll())
        finally:
            unrelated.terminate(); unrelated.wait()
            if pidfile.exists() and alive(int(pidfile.read_text())):
                os.kill(int(pidfile.read_text()), signal.SIGTERM)

    def test_verification_rate_failure_never_rotates_again(self):
        with patch.object(o, 'rotate', return_value={'ok':False,'failure_class':'rate','error':'rate limited'}) as rotate:
            result = o.rotate_until_healthy(self.cfg,self.log,o.State(self.cfg),deadline=time.monotonic()+20)
        self.assertFalse(result['ok'])
        self.assertEqual(rotate.call_count, 1)

    def test_sigterm_writes_cancelled_report_and_releases_lease(self):
        args, state = self.fixture(['stall'])
        env = dict(os.environ, OUC_V2_FIXTURE=str(state), OUC_OC_BIN=self.cfg.oc_bin)
        with open(self.root/'cancel.log','w') as sink:
            p = subprocess.Popen([sys.executable,str(MODULE),'agent','--task',args.task,
                                  '--dir',args.dir,'-m',args.model],env=env,stdout=sink,stderr=sink)
            try:
                end = time.monotonic()+5
                while time.monotonic()<end:
                    if json.loads(state.read_text()).get('calls'): break
                    if p.poll() is not None: break
                    time.sleep(.03)
                self.assertIsNone(p.poll())
                p.send_signal(signal.SIGTERM)
                self.assertEqual(p.wait(timeout=5), 143)
            finally:
                if p.poll() is None: p.terminate(); p.wait(timeout=5)
        journal = json.loads(Path(self.cfg.journal_file).read_text())
        self.assertEqual(journal['reason'], 'cancelled')
        self.assertEqual(journal['steps'][0]['state'], 'interrupted')
        self.assertFalse(o.managed_run_active(self.cfg))



    def test_symlink_workdir_has_physical_matching_pwd(self):
        real = self.root/'real'; real.mkdir()
        link = self.root/'link'; link.symlink_to(real, target_is_directory=True)
        env = o.opencode_env(self.cfg, str(link))
        self.assertEqual(env['PWD'], str(real))

    def test_managed_v2_refuses_unstructured_format_override(self):
        with patch.object(o, 'opencode_is_v2', return_value=True):
            with self.assertRaises(ValueError):
                o.opencode_run_argv(self.cfg, ['--format','default','hello'])

    def test_truncated_native_success_is_not_a_completed_step(self):
        rc, native, journal, rotations = self.run_fixture(['truncated'])
        self.assertEqual((rc,rotations), (1,0))
        self.assertEqual(journal['steps'][0]['state'], 'failed')


    def test_legacy_info_cannot_promote_quoted_error_severity(self):
        for line in (
            b'level=INFO message="Documentation: level=ERROR Free usage limit reached"\n',
            b'Documentation example: level=ERROR message="Free usage limit reached"\n',
        ):
            with self.subTest(line=line):
                self.assertIsNone(o.classify_failure(line))

    def test_two_hop_channels_do_not_replay_each_other_after_restart(self):
        from unittest.mock import Mock
        state = o.State(self.cfg)
        o.atomic_write_json(self.cfg.hop_request, {'id': 'primary_fixture', 'ts': 1})
        o.atomic_write_json(self.cfg.hop_fallback_request, {'id': 'fallback_fixture', 'ts': 2})
        rotation = Mock(return_value={'ok': True, 'new': '2001:db8:2:2::1'})
        with patch.object(o, 'rotate', rotation):
            supervisor = o.Supervisor(self.cfg, self.log)
            supervisor.handle_request()
            supervisor.handle_request()
            supervisor.handle_request()
            restarted = o.Supervisor(self.cfg, self.log)
            restarted.handle_request()
            restarted.handle_request()
        self.assertEqual(rotation.call_count, 2)
        self.assertEqual(state.load()['last_hop_request'], 'fallback_fixture')

    def test_egress_wait_uses_monotonic_not_wall_clock(self):
        from unittest.mock import Mock
        with patch.object(o.time, 'time', side_effect=AssertionError('wall clock used for elapsed wait')), \
             patch.object(o.time, 'monotonic', return_value=11.0), \
             patch.object(o.time, 'sleep'), \
             patch.object(o, 'probe_egress', return_value='2001:db8:2:2::1'):
            result = o._wait_for_egress(self.cfg, Mock(connected=lambda: True), '', 10.0)
        self.assertEqual(result, '2001:db8:2:2::1')

    def test_rotation_duration_survives_backward_wall_clock_jump(self):
        from unittest.mock import Mock
        self.cfg.rotation_mode = 'keys'
        self.cfg.keys_attempts = 1
        fake_warp = Mock(rotate_keys=lambda: (True, ''), connected=lambda: True)
        with patch.object(o, 'Warp', return_value=fake_warp), \
             patch.object(o, 'Firewall'), patch.object(o, 'Pinner'), patch.object(o, 'Bridge'), \
             patch.object(o, '_wait_rotation_spacing'), \
             patch.object(o, 'probe_egress', return_value='2001:db8:1:1::1'), \
             patch.object(o, '_wait_for_egress', return_value='2001:db8:2:2::1'), \
             patch.object(o, 'verify_request', return_value={'ran': True, 'ok': True}), \
             patch.object(o, 'now', return_value=1000), \
             patch.object(o.time, 'time', side_effect=[1000.0, -1000.0, -1000.0, -1000.0]), \
             patch.object(o.time, 'monotonic', side_effect=[10.0, 10.0, 12.0, 12.0]):
            result = o.rotate(self.cfg, self.log, o.State(self.cfg), 'simulated-clock-jump')
        self.assertTrue(result['ok'])
        self.assertGreaterEqual(result['duration'], 0)
        self.assertLess(result['duration'], 5)

    def test_hop_response_wait_uses_monotonic_not_wall_clock(self):
        import argparse, io
        with patch.object(o.os, 'geteuid', return_value=1000), \
             patch.object(o, 'supervisor_alive', return_value=(True, 1, 'fixture')), \
             patch.object(o, 'now', return_value=1000), \
             patch.object(o, 'read_json', return_value=None), \
             patch.object(o.time, 'time', side_effect=AssertionError('wall clock used for hop wait')), \
             patch.object(o.time, 'monotonic', side_effect=[0.0, 0.0, 2.0]), \
             patch.object(o.time, 'sleep'), contextlib.redirect_stderr(io.StringIO()):
            result = o.cmd_hop(argparse.Namespace(wait=1), self.cfg, self.log)
        self.assertEqual(result, 1)

    def test_address_poll_interval_uses_monotonic_clock(self):
        from unittest.mock import Mock
        supervisor = o.Supervisor(self.cfg, self.log)
        supervisor.warp = Mock(connected=lambda: True)
        with patch.object(o.time, 'time', side_effect=AssertionError('wall clock used for address interval')), \
             patch.object(o.time, 'monotonic', return_value=100.0), \
             patch.object(o, 'sync_current') as sync:
            supervisor.sync_address()
        sync.assert_called_once()

    def test_root_background_resume_receives_callable_demoter(self):
        from unittest.mock import Mock
        self.cfg.auto_resume = True
        self.cfg.oc_bin = self.script('print("unused")\n')
        o.State(self.cfg).update(last_wall=1000)
        demoter = lambda: None
        with patch.object(o, 'now', return_value=1000), \
             patch.object(o, '_demote', return_value=(demoter, 1000, 'fixture-user')), \
             patch.object(o.subprocess, 'Popen', return_value=Mock(pid=12345)) as launch:
            self.assertTrue(o.supervisor_auto_resume(self.cfg, o.State(self.cfg), self.log))
        self.assertIs(launch.call_args.kwargs['preexec_fn'], demoter)

    def test_skipping_large_log_starts_at_actual_end_not_first_megabyte(self):
        path = Path(self.cfg.opencode_log)
        path.write_bytes(b'x' * (o.MAX_LOG_READ + 64) + b'\n')
        follower = self.follower()
        self.assertEqual(follower.poll(), b'')
        with path.open('ab') as sink:
            sink.write(b'new-invocation-record\n')
        self.assertEqual(follower.poll(), b'new-invocation-record\n')

    def test_restart_baseline_discards_previous_inode_partial(self):
        state = o.State(self.cfg)
        state.update(log_id='previous-inode', wall_partial='level=ERROR message="')
        Path(self.cfg.opencode_log).write_text('history from replacement log\n')
        o.scan_wall(self.cfg, state, self.log, baseline=True)
        self.assertEqual(state.load().get('wall_partial'), '')

    def test_early_rotation_probe_error_still_releases_lock(self):
        from unittest.mock import Mock
        self.cfg.proxy_mode = False
        self.cfg.kill_switch = True
        with patch.object(o, 'Warp', return_value=Mock(connected=lambda: True)), \
             patch.object(o, 'Firewall'), patch.object(o, 'Pinner'), \
             patch.object(o, '_wait_rotation_spacing'), \
             patch.object(o, 'probe_egress', side_effect=RuntimeError('simulated early failure')):
            result = o.rotate(self.cfg, self.log, o.State(self.cfg), 'simulated-early-error')
        self.assertFalse(result['ok'])
        self.assertIn('simulated early failure', result['error'])
        import fcntl
        with open(self.cfg.rotation_lock, 'a+') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_fixture_scope_rejects_global_fallback_without_writing(self):
        import copy
        escaped = copy.copy(self.cfg)
        escaped.hop_fallback_request = '/tmp/opencode-unlimited/hop.request'
        with self.assertRaisesRegex(RuntimeError, 'escapes'):
            assert_config_scope(escaped, self.root)
        assert_config_scope(self.cfg, self.root)

    def test_fake_provider_refuses_missing_explicit_sandbox(self):
        path = self.root / 'refused.json'
        env = dict(os.environ, OUC_V2_FIXTURE=str(path))
        env.pop('OUC_TEST_SANDBOX', None)
        result = subprocess.run([sys.executable, str(ROOT/'tests/helpers/fake_v2.py'), 'run'], env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(path.exists())
        self.assertIn('explicit', result.stderr)

    def test_foreground_lease_blocks_legacy_rotation_flock(self):
        lease = o.ManagedRunLease(self.cfg)
        self.assertTrue(lease.acquire())
        code = 'import fcntl,sys; f=open(sys.argv[1],"a+"); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)'
        try:
            child = subprocess.run([sys.executable, '-c', code, self.cfg.rotation_lock], capture_output=True)
            self.assertNotEqual(child.returncode, 0)
        finally:
            lease.close()
        child = subprocess.run([sys.executable, '-c', code, self.cfg.rotation_lock], capture_output=True)
        self.assertEqual(child.returncode, 0)

    def test_foreground_lease_blocks_alias_state_owner(self):
        import copy
        first = o.ManagedRunLease(self.cfg)
        self.assertTrue(first.acquire())
        second_cfg = copy.copy(self.cfg)
        second_cfg.managed_lock = str(self.root/'other-managed.lock')
        link = self.root/'shared-rotation.lock'; link.symlink_to(self.cfg.rotation_lock)
        second_cfg.rotation_lock = str(link)
        second = o.ManagedRunLease(second_cfg)
        try:
            self.assertFalse(second.acquire())
        finally:
            second.close(); first.close()

    def test_owned_rotation_lock_is_reentrant_and_keeps_existing_mode(self):
        Path(self.cfg.rotation_lock).touch(mode=0o600)
        lease = o.ManagedRunLease(self.cfg)
        self.assertTrue(lease.acquire())
        try:
            with o.StateLock(self.cfg.rotation_lock, blocking=False, fix_mode=False):
                self.assertTrue(Path(self.cfg.rotation_lock).is_file())
            self.assertEqual(Path(self.cfg.rotation_lock).stat().st_mode & 0o777, 0o600)
        finally:
            lease.close()

    def test_health_context_verifies_selected_model_and_directory(self):
        model = 'opencode/minimax-m3.1-flash-free'
        previous = self.cfg.test_model
        def rotation(cfg, log, state, why):
            self.assertEqual(cfg.test_model, model)
            self.assertEqual(cfg.verify_cwd, str(self.root))
            return {'ok': True, 'verified': True, 'new': '2001:db8:2:2::1'}
        with patch.object(o, 'rotate', side_effect=rotation):
            result = o.rotate_until_healthy(self.cfg, self.log, o.State(self.cfg), deadline=time.monotonic()+3, model=model, cwd=str(self.root))
        self.assertTrue(result['ok'])
        self.assertEqual(self.cfg.test_model, previous)

    def test_verify_override_keeps_configured_default_model(self):
        args, state = self.fixture(['ok'])
        self.cfg.verify = True
        previous = self.cfg.test_model
        with patch.dict(os.environ, {'OUC_V2_FIXTURE': str(state)}):
            result = o.verify_request(self.cfg, self.log, model=args.model, cwd=str(self.root))
        self.assertTrue(result['ok'])
        invocation = json.loads(state.read_text())['invocations'][0]
        self.assertIn(args.model, invocation['argv'])
        self.assertEqual(invocation['cwd'], str(self.root))
        self.assertEqual(self.cfg.test_model, previous)

    def test_resume_verifies_selected_model(self):
        import argparse
        args, _state = self.fixture(['ok'])
        args = argparse.Namespace(session='ses_selected', model=args.model, prompt='continue')
        with patch.object(o, 'verify_request', return_value={'ok': True}) as verify, \
             patch.object(o, 'run_child', return_value={'rc': 0}):
            self.assertEqual(o.cmd_resume(args, self.cfg, self.log), 0)
        self.assertEqual(verify.call_args.kwargs.get('model'), args.model)

    def test_v2_unknown_recovery_model_does_not_rotate(self):
        self.cfg._opencode_v2 = True
        with patch.object(o, 'rotate', return_value={'ok': True, 'verified': True, 'new': '2001:db8:2:2::1'}) as rotate:
            result = o.rotate_until_healthy(self.cfg, self.log, o.State(self.cfg), deadline=time.monotonic()+3)
        self.assertFalse(result['ok'])
        self.assertIn('model', result['detail'])
        rotate.assert_not_called()

    def test_legacy_doctor_helper_has_defined_logger(self):
        from unittest.mock import Mock
        out = []
        with patch.object(o, 'Warp', return_value=Mock(status=lambda: 'Connected')), \
             patch.object(o, 'port_open', return_value=False), \
             patch.object(o, 'probe_egress', return_value='2001:db8:2:2::1'):
            o.doctor_proxy(self.cfg, out)
        self.assertTrue(any('proxy mode' in line for line in out))

    def test_cli_verify_accepts_explicit_model(self):
        args = o.build_parser().parse_args(['verify', '-m', 'opencode/beam-free'])
        self.assertEqual(args.model, 'opencode/beam-free')


if __name__ == '__main__':
    unittest.main()

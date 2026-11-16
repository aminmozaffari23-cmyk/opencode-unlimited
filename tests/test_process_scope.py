"""The orphan reaper must select run-owned sandboxes, not similar processes."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'tests/helpers/orphan_scope.py'


class ProcessScopeTest(unittest.TestCase):
    def test_cleanup_is_run_and_sandbox_scoped(self):
        with tempfile.TemporaryDirectory(prefix='ouc-test.') as tmp:
            fake = Path(tmp) / 'fake_daemon.py'
            fake.write_text('import time\ntime.sleep(60)\n')
            run_id = uuid.uuid4().hex
            env = dict(os.environ, OUC_TEST_SANDBOX=tmp, OUC_TEST_RUN_ID=run_id)
            mine = subprocess.Popen([sys.executable, str(fake)], env=env)
            other = subprocess.Popen([sys.executable, str(fake)], env=dict(env, OUC_TEST_RUN_ID=uuid.uuid4().hex))
            try:
                for _ in range(50):
                    result = subprocess.run([sys.executable, str(HELPER), '--run-id', run_id], capture_output=True, text=True)
                    if str(mine.pid) in result.stdout: break
                    time.sleep(.02)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn('pid='+str(mine.pid), result.stdout)
                self.assertNotIn('pid='+str(other.pid), result.stdout)
                result = subprocess.run([sys.executable, str(HELPER), '--run-id', run_id, '--terminate'], capture_output=True, text=True)
                mine.wait(timeout=5)
                self.assertIsNone(other.poll())
            finally:
                for proc in (mine, other):
                    if proc.poll() is None: proc.kill()
                    proc.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()

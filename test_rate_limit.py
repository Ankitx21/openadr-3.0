"""Run isolated burst protection tests. Never send traffic to the deployed server."""
import os
import subprocess
import sys
from pathlib import Path

if __name__ == '__main__':
    os.environ.setdefault('DEBUG', 'True')
    raise SystemExit(subprocess.call([sys.executable, str(Path(__file__).with_name('manage.py')), 'test',
        'vtn_hub.tests.HubTests.test_bursts_rejected_in_all_phases',
        'vtn_hub.tests.HubTests.test_shared_fixed_window_budget']))

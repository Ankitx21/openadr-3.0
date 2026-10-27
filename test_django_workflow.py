"""Safe replacement for the legacy live-database workflow script."""
import os
import subprocess
import sys
from pathlib import Path

if __name__ == '__main__':
    os.environ.setdefault('DEBUG', 'True')
    raise SystemExit(subprocess.call([sys.executable, str(Path(__file__).with_name('manage.py')), 'test', 'vtn_hub']))

"""Keep the existing Render start command compatible with in-memory limits.

Gunicorn automatically reads this file from the repository root. Older Render
services may still have --workers 2 in their saved start command. Without Redis,
use one worker so concurrent request threads share a single limiter cache.
"""
import os

workers = 1
threads = 4
timeout = 60


def on_starting(server):
    if not os.environ.get('REDIS_URL'):
        server.cfg.set('workers', 1)
        server.num_workers = 1
        server.log.info('SQLite test deployment: one worker with shared in-memory request limits.')

"""Apply the two preview-sandbox patches to the /tmp mirror of the
backend. Run after EVERY rsync of backend/ -> /tmp/boord_lite_backend_copy:
  1. db.py: point the sqlite file at /tmp so nothing is written in the repo
  2. main.py: FRONTEND_DIR -> /tmp/boord_lite_frontend (the frontend mirror)

The DB used to be sqlite:///:memory: with a StaticPool, which forces every
request in the process onto ONE shared connection. That is not how the app
runs in production (a file DB on the default pool, a connection per request),
and it made the preview lie: two overlapping requests would trip
"sqlite3.InterfaceError: Cursor needed to be reset because of
commit/rollback", so any endpoint that commits mid-request - /api/sync/harvest
creating a placeholder lot, most of all - returned a spurious 500 whenever
anything else hit the API at the same time. That sank the auto-seed and would
have been easy to mistake for a bug in the sync code itself (measured
2026-08-09: 6/6 concurrent syncs failed on StaticPool, 6/6 passed on a file
DB). A file DB deleted at launch keeps the "fresh, self-seeding database on
every restart" property without misrepresenting how the app behaves.
"""
import re

DB = "/tmp/boord_lite_backend_copy/db.py"
MAIN = "/tmp/boord_lite_backend_copy/main.py"

src = open(DB).read()
src = re.sub(
    r'^DATABASE_URL = .*$',
    'DATABASE_URL = "sqlite:////tmp/boord_lite_preview.db"',
    src,
    flags=re.MULTILINE,
)
open(DB, "w").write(src)

src = open(MAIN).read()
# Match to the END of the line - the join() call contains nested parens
# (os.path.dirname(__file__)), so a non-greedy "[^)]*)" stops too early.
src = re.sub(
    r'^FRONTEND_DIR = .*$',
    'FRONTEND_DIR = "/tmp/boord_lite_frontend"',
    src,
    flags=re.MULTILINE,
)
open(MAIN, "w").write(src)
print("patches applied")

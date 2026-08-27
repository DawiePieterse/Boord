"""Preview-sandbox launcher for the LITE app: serves the /tmp mirror off a
throwaway sqlite file and auto-seeds demo data once the API is up.
Master copy lives at .claude/preview_launcher.py in the Lite repo -
copy to /tmp/boord_lite_launcher.py after a /tmp purge (see .claude/rebuild_preview.sh)."""
import sys, os
sys.path = [p for p in sys.path if p]
port = int(os.environ.get('PORT', '8823'))

# Start from a blank database on every launch. The preview used to get this
# for free from an in-memory DB, but that needed a StaticPool (one shared
# connection for the whole process), which made concurrent requests fail with
# spurious 500s - see the note in .claude/preview_patches.py. Deleting the file
# up front keeps the fresh-every-restart behaviour while letting the app use
# the same file DB and connection pool it uses in production.
PREVIEW_DB = "/tmp/boord_lite_preview.db"
for _suffix in ("", "-wal", "-shm"):
    try:
        os.remove(PREVIEW_DB + _suffix)
    except FileNotFoundError:
        pass

# Auto-seed demo data once the API is up - the blank DB above means every
# server start would otherwise leave the screens empty.
import threading, subprocess, urllib.request, time

def seed_when_ready():
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        time.sleep(1)
        try:
            urllib.request.urlopen(f"{base}/api/blocks", timeout=2)
            break
        except Exception:
            continue
    else:
        print("[auto-seed] server never became ready, skipping", flush=True)
        return
    print("[auto-seed] seeding demo data...", flush=True)
    r = subprocess.run(
        [sys.executable, "/tmp/boord_lite_backend_copy/seed_demo.py", base],
        capture_output=True, text=True,
    )
    print(r.stdout, flush=True)
    if r.returncode != 0:
        print(f"[auto-seed] failed:\n{r.stderr}", flush=True)

threading.Thread(target=seed_when_ready, daemon=True).start()

import uvicorn
uvicorn.run('main:app', host='127.0.0.1', port=port, loop='asyncio', http='h11',
            app_dir='/tmp/boord_lite_backend_copy')

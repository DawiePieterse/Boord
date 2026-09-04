import sys, os

# Local development only. There is no `tailscale serve` in front of this, so
# without it security.is_admin_client refuses the Admin app to the developer's
# own browser exactly as it refuses the farm's console. start_server.bat, which
# is what actually runs a farm server, never sets this - see
# security.DEV_LOOPBACK_ENV.
os.environ.setdefault("BOORD_ALLOW_LOOPBACK_ADMIN", "1")

_here = os.path.dirname(os.path.abspath(__file__))
_site = os.path.join(_here, ".venv", "lib", "python3.9", "site-packages")
if _site not in sys.path:
    sys.path.insert(0, _site)

import uvicorn
uvicorn.run("main:app", host="127.0.0.1", port=8811, loop="asyncio", http="h11", app_dir=_here)

import sys, os

_here = os.path.dirname(os.path.abspath(__file__))
_site = os.path.join(_here, ".venv", "lib", "python3.9", "site-packages")
if _site not in sys.path:
    sys.path.insert(0, _site)

import uvicorn
uvicorn.run("main:app", host="127.0.0.1", port=8811, loop="asyncio", http="h11", app_dir=_here)

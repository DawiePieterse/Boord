import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backup import start_backup_scheduler
from db import PHOTOS_DIR, seed_defaults
from market_mail import start_market_mail_scheduler
from migrate import run_migrations
from version import prime as prime_version
from routers import (auth, backups, dashboard, devices, harvest_records, master_data, lots,
                      payments, planner, processing, receiving, reports, setup, suppliers, sync,
                      version, weather)
import device_auth
from security import (ADMIN_ONLY_MESSAGE, NOT_PAIRED_MESSAGE, came_through_cloudflare,
                      may_open_admin, paired_device)

app = FastAPI(title="Boord Harvest & Receiving")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(master_data.router)
app.include_router(lots.router)
app.include_router(harvest_records.router)
app.include_router(suppliers.router)
app.include_router(receiving.router)
app.include_router(payments.router)
app.include_router(reports.router)
app.include_router(sync.router)
app.include_router(processing.router)
app.include_router(dashboard.router)
app.include_router(planner.router)
app.include_router(weather.router)
app.include_router(backups.router)
app.include_router(setup.router)
app.include_router(version.router)


@app.middleware("http")
async def admin_app_is_not_on_the_farm_wifi(request: Request, call_next):
    """The Admin screens themselves, guarded the same way their API is.

    Every /api endpoint the admin uses depends on require_admin_client, so the
    data was already safe without this - but a phone on the farm wifi could
    still load the whole Admin app and sit there failing every request, which
    looks like a broken app rather than a closed door. Blocking the static
    files says what is actually true, once, at the address bar.

    Matched on the path prefix rather than on the mounted app, because
    /admin/ is served by the catch-all StaticFiles mount at "/" - there is no
    separate mount to hang a dependency on. "/admin" itself is included: html=True
    redirects it to "/admin/", and a redirect is a perfectly good way in.
    """
    path = request.url.path
    device = paired_device(request)
    through_tunnel = came_through_cloudflare(request)
    wants_a_page = request.method == "GET" and not path.startswith("/api/")

    # From the internet, nothing answers an unpaired device except the two
    # pages that pair one. On the farm wifi the Field and Pack House screens
    # have always been open, because getting onto that wifi takes standing on
    # the farm; the tunnel is reachable by anybody with the address, so there
    # the pairing cookie is what the wifi password was.
    if through_tunnel and device is None and not _open_to_the_internet(path):
        if wants_a_page:
            return RedirectResponse("/login/", status_code=303)
        return PlainTextResponse(NOT_PAIRED_MESSAGE, status_code=403)

    if (path == "/admin" or path.startswith("/admin/")) and not may_open_admin(request):
        if through_tunnel:
            return RedirectResponse("/login/", status_code=303)
        return PlainTextResponse(ADMIN_ONLY_MESSAGE, status_code=403)

    response = await call_next(request)
    # A device still in use is remembered for another full term, so no tablet
    # is logged out in the middle of a season for having been paired a year ago.
    if device is not None and device_auth.needs_renewal(device):
        device_auth.renew(response, device)
    return response


# What an unpaired device may fetch through the tunnel: the sign-in and
# pairing pages, the shared styles and scripts they are drawn with (nothing
# in shared/ is a secret - it is the same code every tablet already has), and
# the three calls those pages make. Exact names and prefixes with their
# slash, so "/login-anything" is not a way round.
_OPEN_PREFIXES = ("/login/", "/pair/", "/shared/")
_OPEN_PATHS = {"/login", "/pair", "/api/auth/state", "/api/auth/login", "/api/auth/pair"}


def _open_to_the_internet(path: str) -> bool:
    return path in _OPEN_PATHS or path.startswith(_OPEN_PREFIXES)


@app.on_event("startup")
def on_startup():
    # Migrations first, always. seed_defaults() writes rows through the
    # models, so it has to be looking at the schema those models describe.
    run_migrations()
    seed_defaults()
    start_backup_scheduler()
    start_market_mail_scheduler()
    # After the migrations, so the revision this reports is the one the
    # server is about to serve against rather than the one it booted with.
    prime_version()


class NoCacheStaticFiles(StaticFiles):
    """Devices keep the packhouse/field/admin pages open for days at a time,
    so without this a browser can silently keep serving JS/HTML from before
    the last deploy - screens break with no visible error until someone
    manually clears the cache. Forcing revalidation costs one cheap 304 per
    load and guarantees every device picks up new code immediately.

    Scoped to StaticFiles only (not a global app middleware) so it can't
    affect API request handling/concurrency."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/photos", StaticFiles(directory=PHOTOS_DIR), name="photos")

# The blank import templates, so the setup wizard can hand a new customer the
# file to fill in rather than describing its columns and hoping. Must be
# mounted BEFORE "/" - that mount is a catch-all, and templates/ sits beside
# frontend/ rather than inside it. Headings only, no rows: see
# templates/README.md for why anything in these files would be imported as
# somebody's real data.
# Mounted only if it is actually there. StaticFiles checks the directory at
# startup and raises if it is missing, which would turn "somebody deleted a
# blank csv" into a farm server that refuses to boot. A missing template
# should cost a 404 on a download link, nothing more.
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")
if os.path.isdir(TEMPLATES_DIR):
    app.mount("/templates", StaticFiles(directory=TEMPLATES_DIR), name="templates")
else:
    print(f"[boord] no templates directory at {TEMPLATES_DIR} - the import templates "
          f"the setup wizard links to will 404", flush=True)

# The manual and training guides, for the Reports tab, so the office can open
# one on the PC (or hand a phone to a new picker's supervisor) without hunting
# for the repo folder. They live at the repo root beside the .md they are
# rendered from, not in frontend/, so they are named here one by one: a fixed
# list rather than a mount, so nothing else at the root is ever served. Under
# /admin/ so the middleware above keeps them off the farm wifi like the rest.
REPO_DIR = os.path.join(os.path.dirname(__file__), "..")
MANUALS = {"MANUAL", "TRAINING_ADMIN", "TRAINING_FIELD", "TRAINING_PACKHOUSE"}


@app.get("/admin/manuals/{name}.pdf", include_in_schema=False)
def manual_pdf(name: str):
    path = os.path.join(REPO_DIR, name + ".pdf")
    if name not in MANUALS or not os.path.isfile(path):
        return PlainTextResponse("No such manual", status_code=404)
    # No filename, so the browser opens it in its viewer rather than saving it.
    return FileResponse(path, media_type="application/pdf")


FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/", NoCacheStaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

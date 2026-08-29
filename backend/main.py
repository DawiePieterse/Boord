import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backup import start_backup_scheduler
from db import PHOTOS_DIR, seed_defaults
from migrate import run_migrations
from routers import (auth, backups, dashboard, devices, harvest_records, master_data, lots,
                      payments, processing, receiving, reports, setup, suppliers, sync, weather)

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
app.include_router(weather.router)
app.include_router(backups.router)
app.include_router(setup.router)


@app.on_event("startup")
def on_startup():
    # Migrations first, always. seed_defaults() writes rows through the
    # models, so it has to be looking at the schema those models describe.
    run_migrations()
    seed_defaults()
    start_backup_scheduler()


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

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/", NoCacheStaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

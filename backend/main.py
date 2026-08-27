import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backup import start_backup_scheduler
from db import PHOTOS_DIR, create_db_and_tables, seed_defaults
from routers import (analysis, auth, backups, dashboard, devices, harvest_records, master_data, lots, owner_view,
                      payments, processing, receiving, reports, risk, suppliers, sync, weather)

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
app.include_router(analysis.router)
app.include_router(weather.router)
app.include_router(risk.router)
app.include_router(backups.router)
app.include_router(owner_view.router)


@app.on_event("startup")
def on_startup():
    create_db_and_tables()
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

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/", NoCacheStaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

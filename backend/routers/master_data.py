import os
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlmodel import Session, SQLModel, select

from db import PHOTOS_DIR, get_session
from excel_io import parse_uploaded_table, rows_to_csv_bytes, rows_to_xlsx_bytes
from models import Block, RateSetting, RateType, Supplier, SystemSetting, Team, Worker
from security import get_current_admin, get_optional_admin

router = APIRouter(prefix="/api", tags=["master-data"])


class RateSettingIn(SQLModel):
    effective_date: date
    rate_type: RateType = RateType.per_kg
    default_rate_per_kg: float = 0.0
    tier_rates_json: str = "{}"


def _export(headers, rows, fmt: str, filename: str):
    if fmt == "xlsx":
        data = rows_to_xlsx_bytes(headers, rows, filename)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ext = "xlsx"
    else:
        data = rows_to_csv_bytes(headers, rows)
        media = "text/csv"
        ext = "csv"
    return Response(content=data, media_type=media,
                     headers={"Content-Disposition": f'attachment; filename="{filename}.{ext}"'})


# --- Teams -------------------------------------------------------------

@router.get("/teams")
def list_teams(session: Session = Depends(get_session)):
    return session.exec(select(Team)).all()


@router.post("/teams")
def upsert_team(team: Team, session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    session.merge(team)
    session.commit()
    return {"ok": True}


@router.delete("/teams/{team_id}")
def deactivate_team(team_id: str, session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    obj = session.get(Team, team_id)
    if obj:
        obj.active = False
        session.add(obj)
        session.commit()
    return {"ok": True}


# --- Blocks --------------------------------------------------------------

@router.get("/blocks")
def list_blocks(session: Session = Depends(get_session)):
    return session.exec(select(Block)).all()


@router.post("/blocks")
def upsert_block(block: Block, session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    session.merge(block)
    session.commit()
    return {"ok": True}


@router.delete("/blocks/{block_id}")
def deactivate_block(block_id: str, session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    obj = session.get(Block, block_id)
    if obj:
        obj.active = False
        session.add(obj)
        session.commit()
    return {"ok": True}


@router.get("/blocks/export")
def export_blocks(fmt: str = Query("xlsx", pattern="^(csv|xlsx)$"), session: Session = Depends(get_session),
                   admin=Depends(get_current_admin)):
    blocks = session.exec(select(Block)).all()
    headers = ["id", "name", "variety", "trees", "hectares", "active"]
    rows = [[b.id, b.name, b.variety, b.trees, b.hectares, b.active] for b in blocks]
    return _export(headers, rows, fmt, "Blocks")


@router.post("/blocks/import")
async def import_blocks(file: UploadFile, replace: bool = Query(False),
                         session: Session = Depends(get_session),
                         admin=Depends(get_current_admin)):
    records = await parse_uploaded_table(file)
    # Refuse a file with nothing in it, before anything is written.
    # "Replace all" reads this file as the farm's new complete block list, so
    # an empty one means "the new list is empty" and retires every block the
    # farm has - which is what happens if somebody downloads the blank
    # templates/blocks.csv, means to fill it in, and uploads it as it is.
    # Confirmed by hand before this guard: {"imported": 0, "deactivated": 21}.
    # Recoverable, since blocks are deactivated rather than deleted, but only
    # by someone who works out what happened - the response reads as success.
    # Same guard, and the same reasoning, as routers/historical.py's
    # _check_usable(), which is there because the identical mistake used to
    # wipe a farm's whole harvest history.
    if not records:
        raise HTTPException(400, "That file has no data rows - only column headings. "
                                  "Fill it in first. Nothing was changed.")

    count = 0
    imported_ids = set()
    for r in records:
        if not r.get("id"):
            continue
        block_id = str(r["id"]).strip()
        imported_ids.add(block_id)
        active_raw = r.get("active", True)
        active = str(active_raw).strip().lower() not in ("false", "0", "no")
        session.merge(Block(
            id=block_id,
            name=r.get("name") or "",
            variety=r.get("variety") or "",
            trees=int(r.get("trees") or 0),
            hectares=float(r.get("hectares") or 0),
            active=active,
        ))
        count += 1

    if not imported_ids:
        raise HTTPException(400, "No usable rows in that file - every row is missing an id. "
                                  "Check the column headings against templates/README.md. "
                                  "Nothing was changed.")

    deactivated = 0
    if replace:
        # "Replace all": this file is the new complete block list, so any
        # existing active block that isn't in it is retired rather than
        # deleted outright - historical harvest records still reference it
        # by id, so it stays in the table, just no longer selectable.
        for block in session.exec(select(Block).where(Block.active == True)).all():  # noqa: E712
            if block.id not in imported_ids:
                block.active = False
                session.add(block)
                deactivated += 1

    session.commit()
    return {"imported": count, "deactivated": deactivated}


# --- Workers ---------------------------------------------------------------

# What an unauthenticated caller gets back for each worker. The Field app
# (renderWorkerOptions) and the badge printer both read this endpoint without
# a token and need only these; everything else on Worker - id_number (SA ID),
# bank, account, whatsapp_number - is personal data that used to go out to
# anyone who could reach the server, no credentials needed.
_PUBLIC_WORKER_FIELDS = ("id", "first_name", "last_name", "name", "supplier_id", "photo_filename", "active")


@router.get("/workers")
def list_workers(session: Session = Depends(get_session), admin=Depends(get_optional_admin)):
    """Full records for a signed-in admin (the Master Data edit modal needs
    the bank/ID fields); a reduced projection for everyone else."""
    workers = session.exec(select(Worker)).all()
    if admin:
        return workers
    return [{f: getattr(w, f) for f in _PUBLIC_WORKER_FIELDS} for w in workers]


@router.post("/workers")
def upsert_worker(worker: Worker, session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    if worker.first_name or worker.last_name:
        worker.name = f"{worker.first_name} {worker.last_name}".strip()
    elif not worker.name:
        worker.name = worker.id
    existing = session.get(Worker, worker.id)
    if existing:
        worker.photo_filename = existing.photo_filename
    session.merge(worker)
    session.commit()
    return {"ok": True}


@router.post("/workers/{worker_id}/photo")
async def upload_worker_photo(worker_id: str, file: UploadFile, session: Session = Depends(get_session),
                               admin=Depends(get_current_admin)):
    worker = session.get(Worker, worker_id)
    if not worker:
        raise HTTPException(404, "Worker not found")
    ext = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(400, "Unsupported image type")
    if worker.photo_filename:
        old_path = os.path.join(PHOTOS_DIR, worker.photo_filename)
        if os.path.exists(old_path):
            os.remove(old_path)  # avoid orphan if extension changes between uploads
    filename = f"{worker_id}{ext}"
    with open(os.path.join(PHOTOS_DIR, filename), "wb") as f:
        f.write(await file.read())
    worker.photo_filename = filename
    session.add(worker)
    session.commit()
    return {"ok": True, "photo_filename": filename}


@router.delete("/workers/{worker_id}")
def deactivate_worker(worker_id: str, session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    obj = session.get(Worker, worker_id)
    if obj:
        obj.active = False
        session.add(obj)
        session.commit()
    return {"ok": True}


@router.get("/workers/export")
def export_workers(fmt: str = Query("xlsx", pattern="^(csv|xlsx)$"), session: Session = Depends(get_session),
                    admin=Depends(get_current_admin)):
    workers = session.exec(select(Worker)).all()
    supplier_names = {s.id: s.name for s in session.exec(select(Supplier)).all()}
    headers = ["emp_nr", "first_name", "last_name", "id_number", "bank", "account", "whatsapp_number",
               "supplier_id", "supplier_name", "active"]
    rows = [[w.id, w.first_name, w.last_name, w.id_number, w.bank, w.account, w.whatsapp_number,
             w.supplier_id, supplier_names.get(w.supplier_id, ""), w.active]
            for w in workers]
    return _export(headers, rows, fmt, "Workers")


@router.post("/workers/import")
async def import_workers(file: UploadFile, session: Session = Depends(get_session),
                          admin=Depends(get_current_admin)):
    records = await parse_uploaded_table(file)
    count = 0
    for r in records:
        emp_nr = r.get("emp_nr") or r.get("id") or r.get("Emp Nr") or r.get("emp nr")
        if not emp_nr:
            continue
        active_raw = r.get("active", True)
        active = str(active_raw).strip().lower() not in ("false", "0", "no")
        first_name = r.get("first_name") or r.get("First Name") or ""
        last_name = r.get("last_name") or r.get("Last Name") or ""
        legacy_name = r.get("name") or r.get("Naam & Van") or ""
        if first_name or last_name:
            display_name = f"{first_name} {last_name}".strip()
        else:
            display_name = legacy_name
            parts = legacy_name.split(" ", 1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else ""
        emp_id = str(emp_nr).strip()
        existing = session.get(Worker, emp_id)
        session.merge(Worker(
            id=emp_id,
            first_name=str(first_name).strip(),
            last_name=str(last_name).strip(),
            name=display_name,
            id_number=str(r.get("id_number") or "").strip(),
            bank=r.get("bank") or "",
            account=str(r.get("account") or ""),
            whatsapp_number=str(r.get("whatsapp_number") or ""),
            supplier_id=int(r["supplier_id"]) if r.get("supplier_id") else None,
            photo_filename=existing.photo_filename if existing else "",
            active=active,
        ))
        count += 1
    session.commit()
    return {"imported": count}


# --- Rate settings -----------------------------------------------------

@router.get("/rate-settings/current")
def current_rate_setting(session: Session = Depends(get_session)):
    # Rates are an append-only history, so "current" is the newest row.
    # Ordering by effective_date alone is not enough: setting a rate twice in
    # one day (or correcting one the same day) leaves two rows sharing a date,
    # and the tie resolved to whichever the database happened to return first -
    # in practice the OLD one. The admin saw "Rate saved" while wages carried
    # on being worked out at the previous rate. Break the tie on id so the most
    # recently saved row wins. Kept in step with the same lookup in
    # routers/payments.py.
    setting = session.exec(
        select(RateSetting).order_by(RateSetting.effective_date.desc(), RateSetting.id.desc())
    ).first()
    return setting


@router.post("/rate-settings")
def create_rate_setting(setting_in: RateSettingIn, session: Session = Depends(get_session),
                         admin=Depends(get_current_admin)):
    setting = RateSetting(**setting_in.model_dump())
    session.add(setting)
    session.commit()
    session.refresh(setting)
    return setting


# --- System settings (farm name, urgency thresholds, GPS, season) ------

@router.get("/system-settings")
def get_system_settings(session: Session = Depends(get_session)):
    return session.exec(select(SystemSetting)).first()


@router.put("/system-settings")
def update_system_settings(setting: SystemSetting, session: Session = Depends(get_session),
                            admin=Depends(get_current_admin)):
    existing = session.exec(select(SystemSetting)).first()
    setting.id = existing.id if existing else None
    session.merge(setting)
    session.commit()
    return {"ok": True}

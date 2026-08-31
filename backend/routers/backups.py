import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from backup import BACKUPS_DIR, create_backup, list_backups, offsite_status
from security import get_current_admin

router = APIRouter(prefix="/api/backups", tags=["backups"])


@router.get("")
def get_backups(admin=Depends(get_current_admin)):
    return list_backups()


@router.get("/offsite")
def get_offsite_status(admin=Depends(get_current_admin)):
    """Whether the copy off this machine is actually happening.

    Its own endpoint rather than fields on GET /api/backups, which returns a
    bare list the admin screen iterates - changing that shape would break the
    table for the sake of two lines of status.
    """
    return offsite_status()


@router.post("")
def trigger_backup(admin=Depends(get_current_admin)):
    filename = create_backup()
    return {"filename": filename}


@router.get("/{filename}/download")
def download_backup(filename: str, admin=Depends(get_current_admin)):
    safe_name = os.path.basename(filename)
    path = os.path.join(BACKUPS_DIR, safe_name)
    if not os.path.exists(path):
        raise HTTPException(404, "Backup not found")
    return FileResponse(path, media_type="application/zip", filename=safe_name)

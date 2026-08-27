from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session, SQLModel, select

from db import get_session
from models import AdminUser
from security import create_access_token, get_current_admin, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

MIN_PASSWORD_LENGTH = 8


class ChangePasswordIn(SQLModel):
    """Request BODY, deliberately not a query parameter. As a bare `str`
    argument FastAPI bound this to the query string, so the new password
    travelled in the URL and the web server wrote it to its access log in
    cleartext (verified: uvicorn logged
    'POST /api/auth/change-password?new_password=... 200'), as well as
    landing in browser history and any proxy along the way."""
    new_password: str


@router.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends(), session: Session = Depends(get_session)):
    user = session.exec(select(AdminUser).where(AdminUser.username == form.username)).first()
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(401, "Invalid username or password")
    return {"access_token": create_access_token(user.username), "token_type": "bearer"}


@router.post("/change-password")
def change_password(body: ChangePasswordIn, session: Session = Depends(get_session),
                     current: AdminUser = Depends(get_current_admin)):
    from db import pwd_context
    new_password = body.new_password
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    current.password_hash = pwd_context.hash(new_password)
    session.add(current)
    session.commit()
    return {"ok": True}

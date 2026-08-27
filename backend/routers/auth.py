from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session, SQLModel, select

from db import get_session
from models import AdminUser
from security import create_access_token, get_admin_pending_password_change, verify_password

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
    return {
        "access_token": create_access_token(user.username),
        "token_type": "bearer",
        # A fresh install signs in with a password it generated for itself.
        # The token is issued so the admin can call change-password with it,
        # but get_current_admin refuses it everywhere else, so the app sends
        # them straight to the "set your password" screen.
        "must_change_password": user.must_change_password,
    }


@router.post("/change-password")
def change_password(body: ChangePasswordIn, session: Session = Depends(get_session),
                     current: AdminUser = Depends(get_admin_pending_password_change)):
    """The only endpoint an admin who still owes a password change can reach,
    hence the pending-friendly dependency - anything stricter would lock a new
    install out of the very step that unlocks it."""
    from db import clear_initial_password_file, pwd_context
    new_password = body.new_password
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if verify_password(new_password, current.password_hash):
        # Otherwise "change your password" is satisfiable by retyping the one
        # the installer printed, which is exactly the password being retired.
        raise HTTPException(400, "That is already this account's password - choose a different one")
    current.password_hash = pwd_context.hash(new_password)
    current.must_change_password = False
    session.add(current)
    session.commit()
    # The generated password no longer opens anything, so the copy left in
    # data/ for the installer to print is now pure liability.
    clear_initial_password_file()
    return {"ok": True}

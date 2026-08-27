import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlmodel import Session, select

from db import DATA_DIR, engine, pwd_context
from models import AdminUser

def _load_or_create_secret() -> str:
    """The JWT signing key, stable across restarts.

    BOORD_SECRET_KEY wins if it's set. Otherwise the key is generated once and
    kept in data/.secret_key rather than regenerated per process: a fresh
    random key on every start invalidated every token ever issued, so the
    scheduled task that launches this at boot - and every update_server.bat
    restart - silently signed all admins out, despite TOKEN_EXPIRE_DAYS
    promising a 30-day session. Never shipped in git (data/ is ignored) and
    excluded from backups, so it is still not a hardcoded shared secret.
    """
    env = os.environ.get("BOORD_SECRET_KEY")
    if env:
        return env
    key_path = os.path.join(DATA_DIR, ".secret_key")
    try:
        with open(key_path) as f:
            existing = f.read().strip()
        if existing:
            return existing
    except FileNotFoundError:
        pass
    key = os.urandom(32).hex()
    try:
        # 0600 where the OS honours it; on Windows the data dir is already
        # restricted to the service account.
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key)
    except OSError as e:
        # An unwritable data dir must not stop the server booting - fall back
        # to the old per-process behaviour, just noisily.
        print(f"[security] could not persist {key_path} ({e!r}) - admin sessions "
              f"will not survive a restart", flush=True)
    return key


SECRET_KEY = _load_or_create_secret()
ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 30

bearer_scheme = HTTPBearer(auto_error=False)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRE_DAYS)
    return jwt.encode({"sub": username, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def _admin_for_credentials(credentials: Optional[HTTPAuthorizationCredentials]) -> Optional[AdminUser]:
    """The AdminUser a bearer token identifies, or None if there is no token
    or it doesn't check out. Never raises - callers decide what a miss means."""
    if credentials is None:
        return None
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
    except JWTError:
        return None
    with Session(engine) as session:
        return session.exec(select(AdminUser).where(AdminUser.username == username)).first()


def get_current_admin(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)) -> AdminUser:
    user = _admin_for_credentials(credentials)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    return user


def get_optional_admin(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Optional[AdminUser]:
    """For endpoints that must stay reachable without a login but should hand
    back more once an admin IS signed in - see master_data.list_workers, which
    the unauthenticated Field app and badge printer both need, but which must
    not serve ID/bank numbers to them."""
    return _admin_for_credentials(credentials)

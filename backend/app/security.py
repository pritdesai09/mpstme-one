"""Password hashing (scrypt, built into Python) and login tokens (JWT)."""
import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .db import get_conn
from .settings import JWT_HOURS, JWT_SECRET

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(key).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, key_b64 = stored.split("$")
        salt, key = base64.b64decode(salt_b64), base64.b64decode(key_b64)
    except ValueError:
        return False
    test = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return hmac.compare_digest(test, key)


def create_token(user_id: int) -> str:
    payload = {"sub": str(user_id), "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_HOURS)}
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


_bearer = HTTPBearer(auto_error=False)


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    if creds is None:
        raise HTTPException(401, "Please sign in.")
    try:
        user_id = int(jwt.decode(creds.credentials, JWT_SECRET, algorithms=["HS256"])["sub"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Your session has expired. Please sign in again.")
    with get_conn() as conn:
        user = conn.execute("SELECT id, email, name, role FROM users WHERE id = %s", (user_id,)).fetchone()
    if not user:
        raise HTTPException(401, "Account not found. Please sign in again.")
    return user
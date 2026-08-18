"""JWT-Erzeugung und -Pruefung."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jose import jwt

ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 8


def create_token(secret: str, user_id: str, username: str, role: str,
                 must_change_password: bool = False) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode(
        {
            "sub": user_id,
            "username": username,
            "role": role,
            "pwchange": must_change_password,
            "exp": expire,
        },
        secret,
        algorithm=ALGORITHM,
    )


def decode_token(secret: str, token: str) -> dict:
    """Wirft JWTError bei ungueltigem oder abgelaufenem Token."""
    return jwt.decode(token, secret, algorithms=[ALGORITHM])

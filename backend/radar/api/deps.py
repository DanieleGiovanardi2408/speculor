"""FastAPI dependencies: database session and (optional) Supabase JWT auth."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import time
from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated, Any

import jwt
from dotenv import dotenv_values
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import Engine
from sqlmodel import Session

from radar.config import REPO_ROOT
from radar.db import get_engine


def get_db_engine() -> Engine:
    """The engine used by every route. Tests override this dependency."""
    return get_engine()


def get_session(engine: Annotated[Engine, Depends(get_db_engine)]) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


# --- auth -------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _dotenv() -> dict[str, str | None]:
    path = REPO_ROOT / ".env"
    return dict(dotenv_values(path)) if path.exists() else {}


def _env(name: str) -> str | None:
    """Process environment first, then the repo .env file."""
    value = os.environ.get(name)
    return value if value is not None else _dotenv().get(name)


def auth_required() -> bool:
    return (_env("RADAR_AUTH_REQUIRED") or "").strip().lower() in {"1", "true", "yes", "on"}


def _b64url_decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


class TokenError(ValueError):
    pass


def verify_hs256(token: str, secret: str, audience: str = "authenticated") -> dict[str, Any]:
    """Verify an HS256 JWT (signature, exp, nbf, aud). Returns the claims or raises TokenError."""
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenError("malformed token")
    head_b64, body_b64, sig_b64 = parts
    try:
        header = json.loads(_b64url_decode(head_b64))
        claims = json.loads(_b64url_decode(body_b64))
        signature = _b64url_decode(sig_b64)
    except (binascii.Error, ValueError) as exc:
        raise TokenError("malformed token") from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise TokenError("malformed token")
    if header.get("alg") != "HS256":
        raise TokenError("unsupported token algorithm")
    expected = hmac.new(
        secret.encode("utf-8"), f"{head_b64}.{body_b64}".encode("ascii"), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(expected, signature):
        raise TokenError("bad signature")
    now = time.time()
    exp = claims.get("exp")
    if not isinstance(exp, int | float) or exp <= now:
        raise TokenError("token expired")
    nbf = claims.get("nbf")
    if isinstance(nbf, int | float) and nbf > now + 30:
        raise TokenError("token not yet valid")
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    if audience not in auds:
        raise TokenError("bad audience")
    return claims


@lru_cache(maxsize=4)
def _jwks_client(supabase_url: str) -> jwt.PyJWKClient:
    url = f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)


def verify_supabase_token(token: str) -> dict[str, Any]:
    """Verify a Supabase access token.

    Asymmetric signing keys (ES256/RS256, the default for new projects) are checked against
    the project's JWKS (SUPABASE_URL); legacy HS256 tokens against SUPABASE_JWT_SECRET.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise TokenError("malformed token") from exc
    alg = header.get("alg")
    if alg == "HS256":
        secret = _env("SUPABASE_JWT_SECRET")
        if not secret:
            raise TokenError("HS256 token but SUPABASE_JWT_SECRET is not set")
        return verify_hs256(token, secret)
    if alg not in {"ES256", "RS256", "EdDSA"}:
        raise TokenError(f"unsupported alg {alg}")
    supabase_url = _env("SUPABASE_URL")
    if not supabase_url:
        raise TokenError("asymmetric token but SUPABASE_URL is not set")
    try:
        key = _jwks_client(supabase_url).get_signing_key_from_jwt(token)
        claims: dict[str, Any] = jwt.decode(
            token, key.key, algorithms=[alg], audience="authenticated"
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("expired") from exc
    except jwt.PyJWTError as exc:
        raise TokenError("bad signature or claims") from exc
    return claims


def require_auth(authorization: Annotated[str | None, Header()] = None) -> None:
    """No-op unless RADAR_AUTH_REQUIRED=true; then require a Supabase JWT for the allowed email."""
    if not auth_required():
        return
    allowed = (_env("RADAR_ALLOWED_EMAIL") or "").strip().lower()
    if not allowed or not (_env("SUPABASE_URL") or _env("SUPABASE_JWT_SECRET")):
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Auth is required but SUPABASE_URL (or SUPABASE_JWT_SECRET) / "
            "RADAR_ALLOWED_EMAIL are not set",
        )
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = verify_supabase_token(token)
    except TokenError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    email = claims.get("email")
    if not isinstance(email, str) or email.strip().lower() != allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is not allowed")

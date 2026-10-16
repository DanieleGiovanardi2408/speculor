from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

SECRET = "test-secret-for-hs256-only"
ALLOWED = "Owner@Example.com"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_token(claims: dict[str, Any], secret: str = SECRET, alg: str = "HS256") -> str:
    head = _b64(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    body = _b64(json.dumps(claims).encode())
    sig = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64(sig)}"


def claims(**over: Any) -> dict[str, Any]:
    base = {
        "sub": "u1",
        "email": "owner@example.com",
        "aud": "authenticated",
        "exp": int(time.time()) + 600,
        "role": "authenticated",
    }
    base.update(over)
    return base


@pytest.fixture()
def auth_client(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("RADAR_AUTH_REQUIRED", "true")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setenv("RADAR_ALLOWED_EMAIL", ALLOWED)
    return client


def _get(c: TestClient, token: str | None, path: str = "/api/today") -> int:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    code: int = c.get(path, headers=headers).status_code
    return code


def test_no_auth_when_not_required(client: TestClient) -> None:
    assert _get(client, None) == 200


def test_health_is_public(auth_client: TestClient) -> None:
    assert _get(auth_client, None, "/api/health") == 200


def test_valid_token_allowed_email(auth_client: TestClient) -> None:
    assert _get(auth_client, make_token(claims())) == 200
    assert _get(auth_client, make_token(claims()), "/api/postings") == 200


def test_other_email_forbidden(auth_client: TestClient) -> None:
    assert _get(auth_client, make_token(claims(email="someone@example.com"))) == 403


def test_missing_token(auth_client: TestClient) -> None:
    assert _get(auth_client, None) == 401


def test_expired_token(auth_client: TestClient) -> None:
    assert _get(auth_client, make_token(claims(exp=int(time.time()) - 5))) == 401


def test_bad_signature_audience_and_alg(auth_client: TestClient) -> None:
    assert _get(auth_client, make_token(claims(), secret="wrong")) == 401
    assert _get(auth_client, make_token(claims(aud="anon"))) == 401
    assert _get(auth_client, make_token(claims(), alg="none")) == 401
    assert _get(auth_client, "garbage") == 401


def test_es256_token_verified_against_jwks(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import jwt
    from cryptography.hazmat.primitives.asymmetric import ec

    from radar.api import deps

    private = ec.generate_private_key(ec.SECP256R1())
    other = ec.generate_private_key(ec.SECP256R1())

    class FakeJWKS:
        def get_signing_key_from_jwt(self, token: str) -> Any:
            return type("K", (), {"key": private.public_key()})()

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(deps, "_jwks_client", lambda url: FakeJWKS())
    good = jwt.encode(claims(), private, algorithm="ES256", headers={"kid": "k1"})
    assert _get(auth_client, good) == 200
    forged = jwt.encode(claims(), other, algorithm="ES256", headers={"kid": "k1"})
    assert _get(auth_client, forged) == 401
    wrong_user = jwt.encode(claims(email="x@example.com"), private, algorithm="ES256")
    assert _get(auth_client, wrong_user) == 403
    expired = jwt.encode(claims(exp=int(time.time()) - 5), private, algorithm="ES256")
    assert _get(auth_client, expired) == 401

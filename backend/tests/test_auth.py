"""Tests for JWT authentication dependency and protected route 401 enforcement.

Strategy:
- Integration tests use app.dependency_overrides to bypass or exercise auth.
- Unit tests of get_current_user mock _get_jwks with a real RSA test key pair.
- No real Supabase network calls are made.
"""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from app.core.auth import get_current_user
from app.main import app


# ---------------------------------------------------------------------------
# Test RSA key pair (generated once for the module)
# ---------------------------------------------------------------------------

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()
_KID = "test-key-id"

# Public key as JWK dict (for mocking JWKS responses)
_JWK = json.loads(RSAAlgorithm.to_jwk(_PUBLIC_KEY))
_JWK["kid"] = _KID
_JWK["use"] = "sig"
_JWK["alg"] = "RS256"

_TEST_JWKS = {"keys": [_JWK]}


def _make_token(sub: str = "test-user-id", expired: bool = False) -> str:
    """Create a signed JWT for testing."""
    now = int(time.time())
    exp = now - 10 if expired else now + 3600
    payload = {
        "sub": sub,
        "aud": "authenticated",
        "iat": now,
        "exp": exp,
    }
    return jwt.encode(
        payload,
        _PRIVATE_KEY,
        algorithm="RS256",
        headers={"kid": _KID},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """TestClient with dependency overrides cleared — exercises real auth."""
    app.dependency_overrides.clear()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Integration tests: 401 on ALL protected routes without Authorization header
# ---------------------------------------------------------------------------

def test_ingestion_without_token_returns_401(client):
    """POST /ingestion/ingest without Authorization → 401 UNAUTHORIZED envelope."""
    response = client.post("/api/v1/ingestion/ingest", json={"ticket_id_or_url": "PROJ-1"})
    assert response.status_code == 401
    payload = response.json()
    assert payload["error"] == "UNAUTHORIZED"
    assert payload["message"] == "Authentication required."
    assert payload["code"] == 401


def test_bdd_without_token_returns_401(client):
    """POST /bdd/generate without Authorization → 401 UNAUTHORIZED envelope."""
    response = client.post(
        "/api/v1/bdd/generate",
        json={"session_id": "abc", "acceptance_criteria": "Given something"},
    )
    assert response.status_code == 401
    payload = response.json()
    assert payload["error"] == "UNAUTHORIZED"
    assert payload["message"] == "Authentication required."
    assert payload["code"] == 401


def test_verification_run_agentic_without_token_returns_401(client):
    """POST /verification/run-agentic without Authorization -> 401 UNAUTHORIZED envelope (AC5)."""
    response = client.post(
        "/api/v1/verification/run-agentic",
        json={
            "session_id": "abc",
            "bdd_content": "Scenario: x\n  Given y",
            "mode": "full_repo",
            "github_input": "https://github.com/org/repo",
        },
    )
    assert response.status_code == 401
    payload = response.json()
    assert payload["error"] == "UNAUTHORIZED"
    assert payload["message"] == "Authentication required."
    assert payload["code"] == 401




# ---------------------------------------------------------------------------
# Unit tests: get_current_user function
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_current_user_valid_token_returns_user_id():
    """get_current_user with a valid JWT returns the sub claim as user_id."""
    token = _make_token(sub="user-abc-123")

    with patch("app.core.auth._get_jwks", new_callable=AsyncMock) as mock_jwks:
        mock_jwks.return_value = _TEST_JWKS

        mock_request = MagicMock(spec=Request)
        mock_request.headers = {"Authorization": f"Bearer {token}"}

        user_id = await get_current_user(mock_request)
        assert user_id == "user-abc-123"


@pytest.mark.asyncio
async def test_get_current_user_expired_token_raises_401():
    """get_current_user with an expired JWT raises HTTPException(401)."""
    token = _make_token(expired=True)

    with patch("app.core.auth._get_jwks", new_callable=AsyncMock) as mock_jwks:
        mock_jwks.return_value = _TEST_JWKS

        mock_request = MagicMock(spec=Request)
        mock_request.headers = {"Authorization": f"Bearer {token}"}

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(mock_request)
        assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_missing_header_raises_401():
    """get_current_user with no Authorization header raises HTTPException(401)."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(mock_request)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_bad_bearer_format_raises_401():
    """get_current_user with malformed Authorization header raises HTTPException(401)."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {"Authorization": "Token abc123"}

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(mock_request)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_invalid_token_raises_401():
    """get_current_user with a tampered/invalid JWT raises HTTPException(401)."""
    with patch("app.core.auth._get_jwks", new_callable=AsyncMock) as mock_jwks:
        mock_jwks.return_value = _TEST_JWKS

        mock_request = MagicMock(spec=Request)
        mock_request.headers = {"Authorization": "Bearer not.a.valid.jwt"}

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(mock_request)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Test: JWKS cache behaviour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_jwks_cache_populated_and_reused():
    """_get_jwks caches the result after the first fetch and reuses on subsequent calls."""
    import app.core.auth as auth_module

    original_cache = auth_module._jwks_cache
    original_expiry = auth_module._jwks_cache_expiry
    auth_module._jwks_cache = None
    auth_module._jwks_cache_expiry = 0.0  # force cache miss

    try:
        with patch("app.core.auth.httpx.AsyncClient") as mock_client_class:
            mock_response = MagicMock()
            mock_response.json.return_value = _TEST_JWKS
            mock_response.raise_for_status = MagicMock()

            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            result = await auth_module._get_jwks()
            assert result == _TEST_JWKS
            assert auth_module._jwks_cache == _TEST_JWKS
            assert auth_module._jwks_cache_expiry > 0.0

            # Second call must NOT trigger another HTTP request (cache still fresh)
            result2 = await auth_module._get_jwks()
            assert result2 == _TEST_JWKS
            mock_client.get.assert_awaited_once()
    finally:
        auth_module._jwks_cache = original_cache
        auth_module._jwks_cache_expiry = original_expiry


@pytest.mark.asyncio
async def test_jwks_cache_refreshes_after_ttl_expiry():
    """_get_jwks fetches fresh JWKS when the TTL has elapsed."""
    import app.core.auth as auth_module

    original_cache = auth_module._jwks_cache
    original_expiry = auth_module._jwks_cache_expiry

    auth_module._jwks_cache = {"keys": [{"kid": "old-key"}]}
    auth_module._jwks_cache_expiry = 0.0  # expired TTL

    try:
        with patch("app.core.auth.httpx.AsyncClient") as mock_client_class:
            mock_response = MagicMock()
            mock_response.json.return_value = _TEST_JWKS
            mock_response.raise_for_status = MagicMock()

            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            result = await auth_module._get_jwks()
            assert result == _TEST_JWKS  # stale cache replaced
            mock_client.get.assert_awaited_once()
    finally:
        auth_module._jwks_cache = original_cache
        auth_module._jwks_cache_expiry = original_expiry

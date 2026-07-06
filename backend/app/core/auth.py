"""JWT authentication dependency using Supabase JWKS.

Validates RS256 JWTs issued by Supabase via the public JWKS endpoint.
The JWKS response is cached in memory with a 1-hour TTL to handle key rotation.
"""

import json
import time

import httpx
import jwt
from fastapi import HTTPException, Request
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from app.core.config import settings

# Module-level JWKS cache with TTL.
_jwks_cache: dict | None = None
_jwks_cache_expiry: float = 0.0  # monotonic timestamp after which cache is stale
_JWKS_TTL_SECONDS: float = 3600.0  # refresh every hour to handle key rotation


async def _get_jwks() -> dict:
    """Fetch JWKS from Supabase, returning cached value if still fresh."""
    global _jwks_cache, _jwks_cache_expiry
    now = time.monotonic()
    if _jwks_cache is None or now >= _jwks_cache_expiry:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{settings.supabase_url}/auth/v1/.well-known/jwks.json",
                headers={"apikey": settings.supabase_anon_key},
            )
            resp.raise_for_status()
            _jwks_cache = resp.json()
            _jwks_cache_expiry = now + _JWKS_TTL_SECONDS
    return _jwks_cache


async def get_current_user(request: Request) -> str:
    """FastAPI dependency — validate Bearer JWT and return the user_id (sub claim).

    Raises HTTPException(401) for missing, malformed, expired, or invalid tokens.
    """
    authorization = request.headers.get("Authorization")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Authentication required.",
        )
    token = authorization.removeprefix("Bearer ")

    # JWKS fetch is separated from JWT decode so httpx errors don't mask as auth failures.
    try:
        jwks = await _get_jwks()
    except httpx.HTTPError:
        raise HTTPException(status_code=401, detail="Authentication required.")

    # Guard outside decode try-block so it cannot be swallowed by the JWT except clause.
    keys = jwks.get("keys", [])
    if not keys:
        raise HTTPException(status_code=401, detail="Authentication required.")

    try:
        # Select the correct key by kid (key ID) from the JWT header.
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        alg = header.get("alg", "RS256")

        public_key = None
        for k in keys:
            if k.get("kid") == kid:
                public_key = (
                    ECAlgorithm.from_jwk(json.dumps(k))
                    if alg.startswith("ES")
                    else RSAAlgorithm.from_jwk(json.dumps(k))
                )
                break
        # Fall back to first key if kid not found.
        if public_key is None:
            k = keys[0]
            kty = k.get("kty", "RSA")
            public_key = (
                ECAlgorithm.from_jwk(json.dumps(k))
                if kty == "EC"
                else RSAAlgorithm.from_jwk(json.dumps(k))
            )

        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256", "ES256"],
            audience="authenticated",
        )
        user_id: str = payload["sub"]
        return user_id

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Authentication required.")
    except (jwt.InvalidTokenError, KeyError):
        raise HTTPException(status_code=401, detail="Authentication required.")

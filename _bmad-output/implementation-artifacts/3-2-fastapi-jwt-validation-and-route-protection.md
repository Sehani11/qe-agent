# Story 3.2: FastAPI JWT Validation & Route Protection

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **authenticated user**,
I want all FastAPI API endpoints to validate my session token,
So that unauthenticated users cannot access any backend data or operations.

## Acceptance Criteria

1. **Given** the `get_current_user` dependency in `backend/app/core/auth.py` is applied to all protected routes
   **When** a request arrives with a valid Supabase-issued JWT in the `Authorization: Bearer` header
   **Then** the dependency decodes the JWT using Supabase's JWKS and returns the authenticated `user_id` (NFR-S5)

2. **And** when a request arrives without a token or with an invalid/expired token
   **Then** the endpoint returns HTTP 401 with `{"error": "UNAUTHORIZED", "message": "Authentication required.", "code": 401}` (FR4)

3. **And** the `DEV_USER_ID` stub from Epic 1 is replaced by the real `user_id` extracted from the validated JWT across all services

4. **And** Next.js `middleware.ts` redirects unauthenticated users to `/login` before any page renders (FR4)

5. **And** a Pytest test verifies that a request without a token to any protected route returns HTTP 401

## Tasks / Subtasks

- [x] **Task 1: Implement real JWT validation in `backend/app/core/auth.py`** (AC: 1, 2, 3)
  - [x] Add `PyJWT` and `cryptography` to backend dependencies (`uv add PyJWT cryptography`)
  - [x] Implement `get_current_user(request: Request) -> str` FastAPI dependency:
    - Extract `Authorization` header; if missing → raise `HTTPException(401, detail="Authentication required.")`
    - Strip `Bearer ` prefix from token value; if format wrong → raise `HTTPException(401)`
    - Fetch Supabase JWKS from `{supabase_url}/auth/v1/jwks` using `httpx` (async GET, cache in module-level variable after first fetch)
    - Decode JWT using `jwt.decode()` with JWKS public key, `algorithms=["RS256"]`, `audience="authenticated"`
    - On `jwt.ExpiredSignatureError` or `jwt.InvalidTokenError` → raise `HTTPException(401, detail="Authentication required.")`
    - Return `payload["sub"]` as the `user_id` string
  - [x] Ensure the `http_exception_handler` in `main.py` produces the exact 401 envelope:
    `{"error": "UNAUTHORIZED", "message": "Authentication required.", "code": 401}` — update handler to use `"UNAUTHORIZED"` code for 401s
  - [x] Remove `dev_user_id` usage from `get_current_user`; `Settings.dev_user_id` can remain for now (used in services until each is updated)
  - [x] Add `supabase_url` to `Settings` (already exists as `supabase_url: str = ""`) — document it must be set in `.env`

- [x] **Task 2: Apply `get_current_user` to verification routes** (AC: 1, 2, 3)
  - [x] Edit `backend/app/api/v1/verification.py`:
    - Add `current_user: str = Depends(get_current_user)` to `fetch_verification_code` route
    - Add `current_user: str = Depends(get_current_user)` to `run_verification_endpoint` route
    - Replace `user_id=settings.dev_user_id` with `user_id=current_user` in `run_verification_endpoint`
  - [x] Import `get_current_user` from `app.core.auth` and `Request` from `fastapi`

- [x] **Task 3: Create Next.js `middleware.ts`** (AC: 4)
  - [x] Create `frontend/src/middleware.ts` at the `src/` root (Next.js App Router middleware location)
  - [x] Use `@supabase/ssr` `createServerClient` pattern with cookies to check session:
    - Call `supabase.auth.getUser()` — returns `{ data: { user }, error }`
    - If `!user` AND request is not for `/login`, `/auth/callback`, or static assets → `NextResponse.redirect(new URL("/login", request.url))`
    - If user exists OR path is public → `NextResponse.next()`
  - [x] Export `config.matcher` to exclude `_next/static`, `_next/image`, `favicon.ico`, and `/auth/` paths from middleware
  - [x] The middleware must NOT call `getSession()` — use `getUser()` which validates via Supabase server (not just cookie) per `@supabase/ssr` best practices

- [x] **Task 4: Wire `Authorization: Bearer` token into frontend API calls** (AC: 1, 3)
  - [x] Update `frontend/src/lib/api/client.ts` — add a request interceptor that:
    - Calls `supabase.auth.getSession()` from the client-side Supabase client (`@/lib/supabase/client`)
    - Sets `Authorization: Bearer {access_token}` header on every outgoing request if a session exists
  - [x] This ensures all TanStack Query hooks (ingestion, BDD, verification) automatically include the JWT

- [x] **Task 5: Write Pytest tests for auth dependency** (AC: 1, 2, 5)
  - [x] Create `backend/tests/test_auth.py`
  - [x] Test: request to `POST /api/v1/ingestion/ingest` without `Authorization` header → HTTP 401 with `{"error": "UNAUTHORIZED", "message": "Authentication required.", "code": 401}`
  - [x] Test: request to `POST /api/v1/bdd/generate` without token → HTTP 401
  - [x] Test: request to `POST /api/v1/verification/fetch` without token → HTTP 401
  - [x] Test: `get_current_user` with valid mock JWT payload returns `user_id` from `sub` claim
  - [x] Test: `get_current_user` with expired JWT raises HTTP 401
  - [x] Mock JWKS fetch in all tests — do NOT hit real Supabase network in unit tests
  - [x] Use FastAPI `dependency_overrides` for integration tests where needed

## Dev Notes

### What Already Exists (DO NOT recreate)

| Symbol | File | Notes |
|---|---|---|
| `get_current_user()` | `backend/app/core/auth.py:10` | Currently returns `settings.dev_user_id` stub — **REPLACE** with real JWT logic in Task 1 |
| `Settings.supabase_url` | `backend/app/core/config.py:49` | Already declared as `supabase_url: str = ""` — just needs a real value in `.env` |
| `Settings.supabase_service_role_key` | `backend/app/core/config.py:50` | Available but NOT needed for JWT validation — JWKS endpoint is public |
| `Settings.dev_user_id` | `backend/app/core/config.py:18` | Keep in config but no longer used in `get_current_user` after this story |
| `current_user: str = Depends(get_current_user)` | `backend/app/api/v1/ingestion.py:20`, `bdd.py:15` | Already applied to ingestion and BDD routes — will work automatically once `get_current_user` is real |
| `verification.py` routes | `backend/app/api/v1/verification.py` | **DO NOT have** `get_current_user` yet — add in Task 2 |
| `http_exception_handler` | `backend/app/main.py:48` | Currently returns `"error": "HTTP_ERROR"` for all — update to return `"error": "UNAUTHORIZED"` for 401s |
| `frontend/src/lib/api/client.ts` | `frontend/src/lib/api/client.ts` | Axios client — needs Bearer token interceptor in Task 4 |
| `frontend/src/lib/supabase/client.ts` | `frontend/src/lib/supabase/client.ts` | Client-side Supabase client — use `supabase.auth.getSession()` in interceptor |

### Key Architecture Decisions — MUST FOLLOW

From `architecture.md`:

1. **JWT Validation via JWKS (not secret)** — Supabase issues RS256 JWTs signed with a private key. The public JWKS endpoint is `{SUPABASE_URL}/auth/v1/jwks`. Never hardcode a signing secret. Use asymmetric verification only.
2. **`get_current_user` returns `user_id` string** — the `sub` claim of the Supabase JWT is the user's UUID. This is the value passed everywhere as `user_id`.
3. **All protected routes use `Depends(get_current_user)`** — applied at the route handler level. The dependency raises `HTTPException(401)` before the handler body executes.
4. **Error envelope for 401** — MUST be `{"error": "UNAUTHORIZED", "message": "Authentication required.", "code": 401}` per architecture spec. The existing `http_exception_handler` returns `"HTTP_ERROR"` — update to special-case 401.
5. **Frontend middleware uses `getUser()` not `getSession()`** — `getUser()` validates the session against the Supabase server (prevents stale cookie attacks). `getSession()` only reads the cookie without server validation.
6. **`/health` endpoint stays public** — do NOT add auth to the health check route.

### JWKS Caching Pattern

Fetching JWKS on every request is wasteful. Cache at module level with a simple in-memory cache:

```python
# backend/app/core/auth.py
import httpx
from jose import JWTError, jwt  # or use PyJWT

_jwks_cache: dict | None = None

async def _get_jwks() -> dict:
    global _jwks_cache
    if _jwks_cache is None:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{settings.supabase_url}/auth/v1/jwks")
            resp.raise_for_status()
            _jwks_cache = resp.json()
    return _jwks_cache
```

**Note on JWT library choice:** `python-jose` (`jose`) is already an indirect dependency and is simpler for JWKS handling than raw `PyJWT`. Check if it's available before adding a new dependency. If not available, use `PyJWT` with `cryptography`. Do NOT add both.

```bash
# Check what's available first:
uv pip show python-jose 2>/dev/null || echo "not installed"
```

### `get_current_user` Implementation Pattern

```python
async def get_current_user(request: Request) -> str:
    authorization = request.headers.get("Authorization")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Authentication required.",
        )
    token = authorization.removeprefix("Bearer ")
    
    try:
        jwks = await _get_jwks()
        payload = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            audience="authenticated",
        )
        user_id: str = payload["sub"]
        return user_id
    except (JWTError, KeyError, Exception):
        raise HTTPException(
            status_code=401,
            detail="Authentication required.",
        )
```

### 401 Error Envelope — `main.py` Update Required

The current `http_exception_handler` returns `"error": "HTTP_ERROR"` for all HTTP exceptions. The architecture spec requires `"UNAUTHORIZED"` for 401. Update the handler:

```python
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    if exc.status_code == 401:
        return JSONResponse(
            status_code=401,
            content={"error": "UNAUTHORIZED", "message": "Authentication required.", "code": 401},
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "HTTP_ERROR", "message": exc.detail, "code": exc.status_code},
    )
```

### Next.js Middleware Pattern

Per `@supabase/ssr` docs — middleware must use `createServerClient` with cookies, and call `getUser()` (not `getSession()`):

```typescript
// frontend/src/middleware.ts
import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

export async function middleware(request: NextRequest) {
  let supabaseResponse = NextResponse.next({ request });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() { return request.cookies.getAll(); },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
          supabaseResponse = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) =>
            supabaseResponse.cookies.set(name, value, options)
          );
        },
      },
    }
  );

  const { data: { user } } = await supabase.auth.getUser();

  if (!user && !request.nextUrl.pathname.startsWith("/login") &&
      !request.nextUrl.pathname.startsWith("/auth")) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    return NextResponse.redirect(url);
  }

  return supabaseResponse;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)"],
};
```

### Frontend axios interceptor pattern

```typescript
// frontend/src/lib/api/client.ts  (add to existing axiosInstance)
import { supabase } from "@/lib/supabase/client";

axiosInstance.interceptors.request.use(async (config) => {
  const { data: { session } } = await supabase.auth.getSession();
  if (session?.access_token) {
    config.headers.Authorization = `Bearer ${session.access_token}`;
  }
  return config;
});
```

### Routes Protected vs Public

After this story:
- **Protected** (require valid JWT): `POST /api/v1/ingestion/ingest`, `POST /api/v1/bdd/generate`, `POST /api/v1/verification/fetch`, `POST /api/v1/verification/run`
- **Public** (no auth): `GET /health`

### Testing Strategy for JWT

DO NOT hit real Supabase in tests. Use module-level patching:

```python
# In tests, override get_current_user entirely for integration tests:
from app.core.auth import get_current_user

async def mock_get_current_user() -> str:
    return "test-user-id"

app.dependency_overrides[get_current_user] = mock_get_current_user

# For unit tests of get_current_user itself, mock _get_jwks:
with patch("app.core.auth._get_jwks", new_callable=AsyncMock) as mock_jwks:
    mock_jwks.return_value = {...}  # valid JWKS structure
```

For generating test JWTs without a real Supabase, use `python-jose` to sign with a test RSA key pair and mock the JWKS response with the corresponding public key.

### Existing Test Patterns

The existing tests use `TestClient(app)` with `app.dependency_overrides` to bypass dependencies. See `backend/tests/test_ingestion.py` for the pattern. Auth tests should follow the same approach.

### Project Structure Notes

Files to modify:
```
backend/app/core/auth.py                    (replace stub with real JWT validation)
backend/app/main.py                         (update http_exception_handler for 401)
backend/app/api/v1/verification.py          (add get_current_user dependency)
frontend/src/middleware.ts                  (new — Next.js route protection)
frontend/src/lib/api/client.ts              (add Bearer token request interceptor)
```

New files:
```
backend/tests/test_auth.py                  (new — JWT validation + 401 tests)
```

No new database migrations needed for this story. No new API routes. No frontend UI changes.

### Story 3.1 Handoff — What Was Built

Story 3.1 delivered:
- `frontend/src/app/actions/auth.ts` — Server Actions for signUp, signInWithPassword, signInWithOAuth, signOut
- `frontend/src/lib/supabase/server.ts` — `createServerSupabaseClient()` using `@supabase/ssr` with cookies
- `frontend/src/components/auth/OAuthButton.tsx` — client wrapper for OAuth sign-in
- `frontend/src/components/layout/LogoutButton.tsx` — client wrapper for signOut
- `frontend/src/app/login/page.tsx` — login/registration page with error handling
- `frontend/src/app/auth/callback/route.ts` — OAuth PKCE code exchange

After Story 3.1, authenticated users have a valid Supabase session stored in cookies. The `access_token` from that session is what gets sent as `Authorization: Bearer` to the FastAPI backend.

### References

- Epic 3 story 3.2 AC: [epics.md](_bmad-output/planning-artifacts/epics.md#story-32-fastapi-jwt-validation--route-protection)
- Architecture auth patterns: [architecture.md](_bmad-output/planning-artifacts/architecture.md#authentication--security)
- Architecture mandatory rules: [architecture.md](_bmad-output/planning-artifacts/architecture.md#mandatory-rules--all-ai-agents-must-follow)
- Backend `get_current_user` stub: [auth.py](backend/app/core/auth.py)
- Backend main app + exception handler: [main.py](backend/app/main.py)
- Verification routes (no auth yet): [verification.py](backend/app/api/v1/verification.py)
- Frontend axios client: [client.ts](frontend/src/lib/api/client.ts)
- Frontend Supabase client-side: [client.ts](frontend/src/lib/supabase/client.ts)
- Existing ingestion tests (pattern reference): [test_ingestion.py](backend/tests/test_ingestion.py)
- FR4 (unauthenticated denied), NFR-S5 (JWT validation): [epics.md](_bmad-output/planning-artifacts/epics.md#functional-requirements)
- `@supabase/ssr` middleware docs: https://supabase.com/docs/guides/auth/server-side/nextjs

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

None.

### Completion Notes List

- Implemented real RS256 JWT validation in `backend/app/core/auth.py` using PyJWT + cryptography. JWKS fetched from `{SUPABASE_URL}/auth/v1/jwks` and cached with 1-hour TTL for key rotation handling.
- Key selection by `kid` JWT header field; falls back to first JWKS key if `kid` not matched.
- JWKS fetch separated from JWT decode try/except — httpx errors caught independently so they cannot mask as JWT auth failures; specific except clauses (ExpiredSignatureError, InvalidTokenError, KeyError) replace broad `except Exception`.
- Updated `http_exception_handler` in `main.py` to return `{"error": "UNAUTHORIZED", ...}` envelope for all 401s.
- Applied `Depends(get_current_user)` to both `/verification/fetch` and `/verification/run` routes; replaced `settings.dev_user_id` with `current_user` in `/run`.
- Created `frontend/src/middleware.ts` using `@supabase/ssr` `createServerClient` with `getUser()` (not `getSession()`). Redirects unauthenticated requests to `/login`; public paths `/login`, `/auth`, and static assets excluded via `config.matcher`.
- Added axios request interceptor to `frontend/src/lib/api/client.ts` that attaches `Authorization: Bearer {access_token}` from active Supabase session to every outgoing request; interceptor wrapped in try/catch for getSession() failures.
- Created `backend/tests/test_auth.py` with 11 tests covering: 401 on all 4 protected routes, valid JWT → user_id, expired JWT → 401, missing/malformed header → 401, invalid token → 401, JWKS cache population and TTL refresh.
- Fixed existing test fixtures in `test_bdd.py`, `test_ingestion.py`, `test_verification.py`, `test_verification_service.py` to override `get_current_user` — all 93 tests pass.

### File List

- `backend/app/core/auth.py` (modified — replaced DEV_USER_ID stub with real RS256 JWT validation)
- `backend/app/main.py` (modified — updated http_exception_handler to return UNAUTHORIZED envelope for 401s)
- `backend/app/api/v1/verification.py` (modified — added get_current_user dependency to both routes)
- `backend/pyproject.toml` (modified — added PyJWT>=2.8.0 and cryptography>=42.0.0)
- `frontend/src/middleware.ts` (new — Next.js App Router middleware for route protection)
- `frontend/src/lib/api/client.ts` (modified — added Bearer token request interceptor)
- `backend/tests/test_auth.py` (new — 9 JWT auth tests)
- `backend/tests/test_bdd.py` (modified — added get_current_user override fixture)
- `backend/tests/test_ingestion.py` (modified — added get_current_user override to client fixture)
- `backend/tests/test_verification.py` (modified — added get_current_user override to client fixture)
- `backend/tests/test_verification_service.py` (modified — added get_current_user override to client fixture)

## Change Log

- 2026-04-10: Story 3.2 implemented — real JWT validation, route protection (backend + frontend), 9 auth tests added, 91 total tests passing.
- 2026-04-10: Code review fixes — JWKS cache TTL added (1h), exception handling restructured to prevent HTTPException swallowing, missing /verification/run 401 test added, axios interceptor error handling added. 93 tests passing.

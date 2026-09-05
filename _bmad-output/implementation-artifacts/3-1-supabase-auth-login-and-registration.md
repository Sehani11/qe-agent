# Story 3.1: Supabase Auth — Login & Registration

Status: done

> **Amended 2026-08-22** ([maintenance record](maintenance-2026-08-22-ui-redesign-feature-file.md)): The login page was rebuilt on the new token system — the gradient background and `rounded-3xl border-sky-100 bg-white/90` card quoted here no longer exist. Fields come from the shared `Field`/`Input` primitives, submit buttons are `SubmitButton` (pending state via `useFormStatus`), and the page carries the `Wordmark` + `ThemeToggle` header. Auth errors stay **inline**, not toasts: they belong to the form and must survive a reload. Server-action behaviour is unchanged.

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **visitor**,
I want to register with email/password or sign in via OAuth,
So that I can access the platform and have all my data associated with my account.

## Acceptance Criteria

1. **Given** the user visits `/login`
   **When** they complete the email/password registration form and submit
   **Then** a new account is created via the `signUp` Next.js Server Action in `src/app/actions/auth.ts` using the Supabase Auth SDK server-side (FR1)

2. **And given** the user already has an account
   **When** they complete the email/password login form and submit
   **Then** they are authenticated via the `signInWithPassword` Server Action and redirected to `/` (FR1)

3. **And given** the user clicks the OAuth provider button (e.g. Google)
   **When** the OAuth flow completes
   **Then** the user is authenticated via the `signInWithOAuth` Server Action and redirected to the dashboard (FR2)

4. **And given** an authenticated user clicks "Log out"
   **When** the `signOut` Server Action is called
   **Then** the session is terminated and the user is redirected to `/login` (FR3)

5. **And** the Supabase Auth SDK is **only** called inside Server Actions — never in client components

6. **And** no auth tokens or session secrets are ever exposed to client-side JavaScript

## Tasks / Subtasks

- [x] **Task 1: Install `@supabase/ssr` and create server-side Supabase client** (AC: 1, 2, 3, 4, 5, 6)
  - [x] Run `npm install @supabase/ssr` in `frontend/`
  - [x] Create `frontend/src/lib/supabase/server.ts` — exports `createServerSupabaseClient()` that creates a Supabase client using `cookies()` from `next/headers` (server-side only, never imported in client components)
  - [x] Update `frontend/src/lib/supabase/client.ts` — ensure it only uses `NEXT_PUBLIC_*` env vars (already correct, no change needed unless values need updating)
  - [x] Add `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` to `.env.local.example` if not already present

- [x] **Task 2: Create Server Actions for auth in `src/app/actions/auth.ts`** (AC: 1, 2, 3, 4, 5, 6)
  - [x] Create `frontend/src/app/actions/auth.ts` with `"use server"` directive
  - [x] Implement `signUp(formData: FormData)` Server Action:
    - Extract `email` and `password` from `formData`
    - Call `supabase.auth.signUp({ email, password })` using `createServerSupabaseClient()`
    - On success: call `redirect("/")` (from `next/navigation`)
    - On error: redirect to `/login?error=` with generic message (never expose raw Supabase errors)
  - [x] Implement `signInWithPassword(formData: FormData)` Server Action:
    - Extract `email` and `password` from `formData`
    - Call `supabase.auth.signInWithPassword({ email, password })`
    - On success: call `redirect("/")`
    - On error: redirect to `/login?error=Invalid+email+or+password.`
  - [x] Implement `signInWithOAuth(provider: "google" | "github")` Server Action:
    - Call `supabase.auth.signInWithOAuth({ provider, options: { redirectTo: process.env.NEXT_PUBLIC_SITE_URL + "/auth/callback" } })`
    - Return the OAuth URL for client-side redirect (OAuth requires client-side navigation to the provider URL)
  - [x] Implement `signOut()` Server Action:
    - Call `supabase.auth.signOut()`
    - Call `redirect("/login")`

- [x] **Task 3: Create OAuth callback route** (AC: 3)
  - [x] Create `frontend/src/app/auth/callback/route.ts` (Route Handler, not a page)
  - [x] Handle the OAuth code exchange: extract `code` from query params, call `supabase.auth.exchangeCodeForSession(code)`
  - [x] On success: redirect to `/`
  - [x] On error: redirect to `/login?error=oauth_failed`

- [x] **Task 4: Create `/login` page** (AC: 1, 2, 3)
  - [x] Create `frontend/src/app/login/page.tsx`
  - [x] Page is a **Server Component** (no `"use client"` directive) — forms use Server Actions directly
  - [x] Email/password form:
    - `<form action={signUp}>` and `<form action={signInWithPassword}>` (separate sections)
    - Inputs: `email` (type="email"), `password` (type="password")
    - Two submit buttons: "Sign In" and "Create Account" (separate sections)
  - [x] OAuth button: thin `"use client"` `OAuthButton` component calls `signInWithOAuth("google")` then `router.push(url)`
  - [x] Error display: reads `searchParams.error` to display auth error messages
  - [x] Design matches existing app style: same gradient background, card with `rounded-3xl border border-sky-100 bg-white/90 shadow-[0_24px_80px_-24px_rgba(14,116,144,0.22)] backdrop-blur`
  - [x] Page is accessible at `/login` without authentication

- [x] **Task 5: Add a logout button to the dashboard** (AC: 4)
  - [x] Update `frontend/src/app/page.tsx` to add a logout button
  - [x] Create `frontend/src/components/layout/LogoutButton.tsx`:
    - Calls `signOut` Server Action via a `<form action={signOut}>` form with submit button
    - Styled as a subtle button in the top-right corner: `text-sm font-medium text-slate-500 hover:text-slate-800`
  - [x] Mount `LogoutButton` in dashboard via `<header className="absolute right-6 top-6">`

- [x] **Task 6: Write unit/integration tests** (AC: 1–6)
  - [x] Create `frontend/src/app/actions/__tests__/auth.test.ts`
  - [x] Test `signUp`: calls `supabase.auth.signUp` with correct params; redirects on error with generic message (no raw Supabase errors exposed)
  - [x] Test `signInWithPassword`: calls `supabase.auth.signInWithPassword`; redirects on invalid credentials
  - [x] Test `signOut`: calls `supabase.auth.signOut` and triggers redirect to `/login`
  - [x] Test `signInWithOAuth`: calls `supabase.auth.signInWithOAuth` with correct provider; returns error on failure
  - [x] Mock `createServerSupabaseClient` — return a mock Supabase client
  - [x] Mock `redirect` from `next/navigation` — verify it's called with correct path
  - [x] 9 tests passing; OAuth callback E2E not unit-testable without running Supabase (documented)

## Dev Notes

### What Already Exists (DO NOT recreate)

| Symbol | File | Notes |
|---|---|---|
| `supabase` (client) | `frontend/src/lib/supabase/client.ts:8` | Client-side Supabase client using `NEXT_PUBLIC_*` env vars — for reading session state in RSC/hooks |
| `Settings.supabase_url` | `backend/app/core/config.py:49` | Already in backend config — no changes needed for Story 3.1 |
| `Settings.supabase_service_role_key` | `backend/app/core/config.py:50` | Already in backend config — used in Epic 3.2 for JWT JWKS fetch |
| `Settings.supabase_anon_key` | `backend/app/core/config.py:51` | Already in backend config |
| `get_current_user()` | `backend/app/core/auth.py:10` | **DO NOT MODIFY** — still returns `DEV_USER_ID` stub. Will be replaced in Story 3.2 |

### Architecture Constraints — CRITICAL

These rules come directly from `architecture.md` and MUST be followed:

1. **Auth in Next.js = Server Actions ONLY** — The Supabase Auth SDK (`supabase.auth.signUp`, `signInWithPassword`, `signOut`) MUST only be called inside `"use server"` functions. Never call these in Client Components or directly in page render.
2. **No token exposure to client** — JWT tokens, session secrets, and refresh tokens must NEVER appear in:
   - Client component state
   - Props passed to client components
   - `localStorage` or `sessionStorage`
   - API responses from Next.js routes
3. **Server-side client pattern** — Use `@supabase/ssr` with `cookies()` from `next/headers` for the server client. The `cookies()` call makes the client server-side — this is what handles the session cookie exchange automatically.
4. **DEV_USER_ID stub stays for this story** — The FastAPI backend still uses `DEV_USER_ID` in Story 3.1. Do NOT wire the real user_id through to the backend in this story. That replacement happens in Story 3.2.

### `@supabase/ssr` Pattern (MUST USE — prevents common mistakes)

The `@supabase/supabase-js` client alone does NOT handle Next.js App Router cookie-based sessions. **`@supabase/ssr` is required** for Server Actions to persist auth state via cookies.

```typescript
// frontend/src/lib/supabase/server.ts
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

export async function createServerSupabaseClient() {
  const cookieStore = await cookies();
  return createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return cookieStore.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value, options }) =>
            cookieStore.set(name, value, options)
          );
        },
      },
    }
  );
}
```

### OAuth Flow Architecture Note

OAuth with Supabase requires a two-step process:
1. Server Action calls `supabase.auth.signInWithOAuth(...)` → returns a `data.url` (the provider's auth URL)
2. Client must navigate to `data.url` → this CANNOT be done inside a Server Action (no `window.location` server-side)

**Solution**: Create a thin `"use client"` OAuth button component that:
1. Calls the Server Action to get the OAuth URL
2. Uses `router.push(url)` or `window.location.href = url` to navigate

The actual Supabase auth SDK call stays server-side; only the navigation happens client-side.

### Environment Variables Required

Add to `.env.local` (and `.env.local.example`):
```
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
NEXT_PUBLIC_SITE_URL=http://localhost:3000   # Used for OAuth redirect_to
```

The `NEXT_PUBLIC_SITE_URL` is needed for the OAuth callback URL construction in the `signInWithOAuth` Server Action.

### Login Page Design

Follow the existing card pattern from `frontend/src/app/page.tsx:39` (the demo card):
```tsx
<div className="rounded-3xl border border-sky-100 bg-white/90 p-8 shadow-[0_24px_80px_-24px_rgba(14,116,144,0.22)] backdrop-blur">
```

Same gradient background: `bg-[radial-gradient(circle_at_top,_#dbeafe,_#eff6ff_32%,_#f8fafc_70%)]`

### Project Structure Notes

New files to create:
```
frontend/src/lib/supabase/server.ts                          (new — server-side Supabase client)
frontend/src/app/actions/auth.ts                             (new — Server Actions: signUp, signInWithPassword, signInWithOAuth, signOut)
frontend/src/app/login/page.tsx                              (new — login/registration page)
frontend/src/app/auth/callback/route.ts                      (new — OAuth callback Route Handler)
frontend/src/components/layout/LogoutButton.tsx              (new — thin client wrapper for signOut)
frontend/src/components/auth/OAuthButton.tsx                 (new — thin client wrapper for OAuth sign-in)
frontend/src/app/actions/__tests__/auth.test.ts              (new — Server Actions unit tests)
```

Files to modify:
```
frontend/src/app/page.tsx                                    (add LogoutButton to header)
frontend/.env.local.example                                  (add NEXT_PUBLIC_SITE_URL)
frontend/.env.local                                          (add NEXT_PUBLIC_SITE_URL)
frontend/package.json                                        (add @supabase/ssr, upgrade @supabase/supabase-js)
```

No backend files change in this story. Backend auth stub (`get_current_user` returning `DEV_USER_ID`) stays intact — replaced in Story 3.2.

### Testing Approach

Tests use the same Vitest + React Testing Library setup from Epic 1/2.

For Server Actions testing:
- Mock `createServerSupabaseClient` at module level: `vi.mock("@/lib/supabase/server", () => ({ createServerSupabaseClient: vi.fn() }))`
- Mock `next/navigation`: `vi.mock("next/navigation", () => ({ redirect: vi.fn() }))`
- Mock `next/headers`: `vi.mock("next/headers", () => ({ cookies: vi.fn(() => ({ getAll: vi.fn(() => []), set: vi.fn() })) }))`

### Learnings from Epic 2 — Apply to This Story

From Story 2.4 code review notes and established patterns:
- **`"use client"` at top of ALL client components** — only `LogoutButton.tsx` and the OAuth button sub-component need this; `login/page.tsx` should be a Server Component
- **Lucide icons from `lucide-react`** — for any icons on login page (e.g. `LogIn`, `UserPlus`)
- **No `useEffect` for derived state** — if displaying auth errors from URL params, read `searchParams` directly in Server Component render
- **Tailwind utility classes only** — no CSS modules
- **TypeScript strict types** — no `any`; type all Server Action return values

### Git Context (Recent Commits)

The most recent work was Epic 2 (GitHub verification + display), merged via PR #2. All Epic 1 and Epic 2 features use `DEV_USER_ID` stub. The codebase has no auth layer yet — `/`, `/session/[sessionId]` are publicly accessible. This story introduces the first real auth boundary.

### References

- Epic 3 story 3.1 AC: [epics.md](_bmad-output/planning-artifacts/epics.md#story-31-supabase-auth--login--registration)
- Architecture auth patterns: [architecture.md](_bmad-output/planning-artifacts/architecture.md#authentication--security)
- Architecture mandatory rules: [architecture.md](_bmad-output/planning-artifacts/architecture.md#mandatory-rules--all-ai-agents-must-follow)
- Architecture frontend patterns: [architecture.md](_bmad-output/planning-artifacts/architecture.md#frontend-architecture)
- Existing Supabase client (client-side): [client.ts](frontend/src/lib/supabase/client.ts)
- Backend auth stub (DO NOT MODIFY): [auth.py](backend/app/core/auth.py)
- Existing dashboard page: [page.tsx](frontend/src/app/page.tsx)
- FR1 (registration), FR2 (OAuth), FR3 (logout), FR4 (unauthenticated denied): [epics.md](_bmad-output/planning-artifacts/epics.md#functional-requirements)
- `@supabase/ssr` docs: https://supabase.com/docs/guides/auth/server-side/nextjs

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

- `@supabase/ssr@0.10.2` requires `@supabase/supabase-js@^2.102.1`; upgraded from `2.99.0` to `^2.102.1` (latest `2.103.0`) as a direct peer dependency requirement. Minor version bump, no breaking changes.
- Initial `auth.ts` used `Promise<{ error: string } | never>` return type which caused TypeScript error on form `action` prop (expected `(formData: FormData) => void | Promise<void>`). Refactored to redirect-on-error pattern — all error paths call `redirect("/login?error=...")`, return type becomes `Promise<void>`. Tests updated accordingly.

### Completion Notes List

- Installed `@supabase/ssr@^0.10.2` and upgraded `@supabase/supabase-js` from `2.99.0` → `^2.102.1` to satisfy peer dependency.
- Created `frontend/src/lib/supabase/server.ts` — `createServerSupabaseClient()` uses `@supabase/ssr`'s `createServerClient` with `cookies()` from `next/headers` for App Router cookie-based session management.
- Created `frontend/src/app/actions/auth.ts` — four Server Actions (`signUp`, `signInWithPassword`, `signInWithOAuth`, `signOut`). All Supabase Auth SDK calls are server-side only. Errors use `redirect("/login?error=...")` pattern (generic messages, no raw Supabase errors exposed). `signInWithOAuth` returns OAuth URL for client-side navigation.
- Created `frontend/src/app/auth/callback/route.ts` — Route Handler exchanges OAuth code for session via `supabase.auth.exchangeCodeForSession(code)`, redirects to `/` on success or `/login?error=oauth_failed` on failure.
- Created `frontend/src/app/login/page.tsx` — Server Component. Two separate forms: sign-in (`signInWithPassword`) and register (`signUp`). OAuth buttons rendered via `OAuthButton` client component. Error display reads `searchParams.error`. Matches existing app card/gradient design system.
- Created `frontend/src/components/auth/OAuthButton.tsx` — thin `"use client"` component; calls `signInWithOAuth` Server Action, navigates to returned OAuth URL via `router.push`. Google and GitHub supported.
- Created `frontend/src/components/layout/LogoutButton.tsx` — thin `"use client"` component; `<form action={signOut}>` submit button styled as subtle link.
- Updated `frontend/src/app/page.tsx` — added `LogoutButton` in absolute top-right header.
- Updated `.env.local` and `.env.local.example` with `NEXT_PUBLIC_SITE_URL=http://localhost:3000`.
- 9 unit tests written and passing (Vitest). Pre-existing failures in `GitHubSourceSelector.test.tsx` (22) and `BDDEditorPanel.test.tsx` (1) confirmed pre-existing — no regressions.
- TypeScript strict mode: clean (`npx tsc --noEmit` — no errors).
- ESLint: clean on all new files.
- Backend `get_current_user()` stub unchanged — DEV_USER_ID replacement is Story 3.2 scope.

### File List

- `frontend/package.json` (modified — add `@supabase/ssr@^0.10.2`, upgrade `@supabase/supabase-js` to `^2.102.1`)
- `frontend/package-lock.json` (modified — lockfile updated for new packages)
- `frontend/src/lib/supabase/server.ts` (new)
- `frontend/src/app/actions/auth.ts` (new)
- `frontend/src/app/auth/callback/route.ts` (new)
- `frontend/src/app/login/page.tsx` (new)
- `frontend/src/components/auth/OAuthButton.tsx` (new)
- `frontend/src/components/layout/LogoutButton.tsx` (new)
- `frontend/src/app/page.tsx` (modified — import + mount LogoutButton)
- `frontend/.env.local.example` (modified — add NEXT_PUBLIC_SITE_URL)
- `frontend/.env.local` (modified — add NEXT_PUBLIC_SITE_URL)
- `frontend/src/app/actions/__tests__/auth.test.ts` (new)

## Senior Developer Review (AI)

**Review Date:** 2026-04-10
**Outcome:** Changes Requested → Fixed

### Action Items

- [x] [High] `signUp` redirects to `/` when `data.session` is null (email confirmation pending) — added `check-your-email` redirect path and info banner on login page [`auth.ts:11-21`]
- [x] [High] `OAuthButton` silently swallows failure — no user feedback when `signInWithOAuth` returns error — added `error` state and `isPending` state with visible message [`OAuthButton.tsx:14-18`]
- [x] [Med] Login page renders arbitrary `?error=` param verbatim — reflected content injection risk — restricted to `ERROR_MESSAGES` allowlist [`login/page.tsx:29`]
- [x] [Med] `formData.get()` returns `null` silently cast to string — added `!email || !password` guard in both `signUp` and `signInWithPassword` [`auth.ts:7-8`]
- [x] [Med] `frontend/package-lock.json` modified but missing from File List — added to File List
- [x] [Low] `router.push` used for external OAuth URL — changed to `window.location.href` for reliable external navigation [`OAuthButton.tsx:17`]

## Change Log

- 2026-04-10: Story 3.1 implemented — server-side Supabase client, auth Server Actions, login page, OAuth callback, LogoutButton; 9 unit tests added (claude-sonnet-4-6)
- 2026-04-10: Code review fixes applied — 5 issues resolved (H1, H2, M1, M2, M3 + L3); tests now 12/12 passing

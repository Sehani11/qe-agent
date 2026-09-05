/**
 * Auth error plumbing shared by the server actions and the login page.
 *
 * Server actions never put a provider's raw message in the redirect URL: they
 * classify it into one of the stable codes below and redirect with that. The
 * login page turns the code back into copy. Keeping the wire format a closed
 * set is what stops an unrecognised message from silently resolving to
 * "no error" — the failure mode that made bad-credential sign-ins look like
 * nothing had happened at all.
 */

export const AUTH_ERROR_CODES = [
    "invalid_credentials",
    "email_not_confirmed",
    "email_exists",
    "weak_password",
    "invalid_email",
    "rate_limited",
    "missing_fields",
    "signup_failed",
    "signin_failed",
    "oauth_failed",
] as const;

export type AuthErrorCode = (typeof AUTH_ERROR_CODES)[number];

const MESSAGES: Record<AuthErrorCode, string> = {
    invalid_credentials: "Invalid email or password. Check both and try again.",
    email_not_confirmed:
        "This email has not been confirmed yet. Use the link we sent you, then sign in.",
    email_exists: "That email is already registered. Sign in instead.",
    weak_password: "That password is too weak. Use at least 6 characters.",
    invalid_email: "Enter a valid email address.",
    rate_limited: "Too many attempts. Wait a moment before trying again.",
    missing_fields: "Email and password are required.",
    signup_failed: "Registration failed. Please try again.",
    signin_failed: "Sign-in failed. Please try again.",
    oauth_failed: "OAuth sign-in failed. Please try again.",
};

/** Shown for a code we do not recognise. Never null — an unknown failure still
 *  has to be visible, otherwise the form silently swallows it. */
export const FALLBACK_AUTH_ERROR = "Something went wrong. Please try again.";

/** Resolve an `?error=` param into display copy. Returns null only when there
 *  is no error param at all. */
export function resolveAuthError(code: string | null | undefined): string | null {
    if (!code) return null;
    const trimmed = code.trim();
    if (!trimmed) return null;
    return MESSAGES[trimmed as AuthErrorCode] ?? FALLBACK_AUTH_ERROR;
}

/** The shape we care about from a Supabase AuthError, without importing it. */
interface ProviderAuthError {
    code?: string | null;
    status?: number | null;
    message?: string | null;
}

/** Supabase's own `error.code` values, mapped onto ours. */
const PROVIDER_CODE_MAP: Record<string, AuthErrorCode> = {
    invalid_credentials: "invalid_credentials",
    email_not_confirmed: "email_not_confirmed",
    user_already_exists: "email_exists",
    email_exists: "email_exists",
    weak_password: "weak_password",
    email_address_invalid: "invalid_email",
    validation_failed: "invalid_email",
    over_request_rate_limit: "rate_limited",
    over_email_send_rate_limit: "rate_limited",
};

/** Message patterns, for provider responses that predate `error.code`. */
const MESSAGE_PATTERNS: Array<[RegExp, AuthErrorCode]> = [
    [/invalid login credentials/i, "invalid_credentials"],
    [/email not confirmed|confirm your email/i, "email_not_confirmed"],
    [/already registered|already exists|already been registered/i, "email_exists"],
    [/password should be at least|weak password|password is too short/i, "weak_password"],
    [/rate limit|too many requests/i, "rate_limited"],
    [/invalid email|unable to validate email/i, "invalid_email"],
];

/**
 * Reduce a provider error to one of our codes. `fallback` is what an
 * unrecognised error becomes, so the caller decides whether an unclassifiable
 * failure reads as a sign-in or a sign-up problem.
 */
export function classifyAuthError(
    error: ProviderAuthError | null | undefined,
    fallback: AuthErrorCode
): AuthErrorCode {
    if (!error) return fallback;

    const code = error.code?.trim();
    if (code && PROVIDER_CODE_MAP[code]) return PROVIDER_CODE_MAP[code];

    const message = error.message ?? "";
    for (const [pattern, mapped] of MESSAGE_PATTERNS) {
        if (pattern.test(message)) return mapped;
    }

    if (error.status === 429) return "rate_limited";

    return fallback;
}

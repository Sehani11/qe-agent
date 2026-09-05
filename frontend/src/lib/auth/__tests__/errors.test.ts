import { describe, it, expect } from "vitest";

import {
    classifyAuthError,
    resolveAuthError,
    FALLBACK_AUTH_ERROR,
} from "../errors";

describe("resolveAuthError", () => {
    it("returns null only when there is no error param", () => {
        expect(resolveAuthError(undefined)).toBeNull();
        expect(resolveAuthError(null)).toBeNull();
        expect(resolveAuthError("")).toBeNull();
        expect(resolveAuthError("   ")).toBeNull();
    });

    it("renders copy for a bad-credential sign-in", () => {
        expect(resolveAuthError("invalid_credentials")).toMatch(/invalid email or password/i);
    });

    it("still shows something for a code it does not know", () => {
        // The regression this guards: an unmapped code used to resolve to null,
        // so the login form reloaded with no visible failure at all.
        expect(resolveAuthError("some_code_added_later")).toBe(FALLBACK_AUTH_ERROR);
    });

    it("resolves the oauth_failed code the callback route redirects with", () => {
        expect(resolveAuthError("oauth_failed")).toMatch(/oauth/i);
    });
});

describe("classifyAuthError", () => {
    it("prefers the provider error code", () => {
        expect(
            classifyAuthError({ code: "invalid_credentials", message: "Bad Request" }, "signin_failed")
        ).toBe("invalid_credentials");
    });

    it("falls back to matching the message when no code is present", () => {
        expect(
            classifyAuthError({ message: "Invalid login credentials" }, "signin_failed")
        ).toBe("invalid_credentials");
        expect(
            classifyAuthError({ message: "Email not confirmed" }, "signin_failed")
        ).toBe("email_not_confirmed");
        expect(
            classifyAuthError({ message: "User already registered" }, "signup_failed")
        ).toBe("email_exists");
    });

    it("maps a 429 to a rate-limit code", () => {
        expect(classifyAuthError({ status: 429, message: "nope" }, "signin_failed")).toBe(
            "rate_limited"
        );
    });

    it("uses the caller's fallback for anything unrecognised", () => {
        expect(classifyAuthError({ message: "kaboom" }, "signup_failed")).toBe("signup_failed");
        expect(classifyAuthError(null, "signin_failed")).toBe("signin_failed");
    });
});

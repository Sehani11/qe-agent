import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock next/navigation redirect — throws NEXT_REDIRECT to simulate Next.js behaviour
const mockRedirect = vi.fn();
vi.mock("next/navigation", () => ({
  redirect: (url: string) => {
    mockRedirect(url);
    throw new Error(`NEXT_REDIRECT:${url}`);
  },
}));

// Mock next/headers cookies
const mockCookieStore = {
  getAll: vi.fn(() => []),
  set: vi.fn(),
};
vi.mock("next/headers", () => ({
  cookies: vi.fn(() => Promise.resolve(mockCookieStore)),
}));

// Build reusable mock Supabase client
function buildMockSupabaseClient(overrides: Record<string, unknown> = {}) {
  return {
    auth: {
      signUp: vi
        .fn()
        .mockResolvedValue({ data: { user: { id: "user-1" }, session: { access_token: "tok" } }, error: null }),
      signInWithPassword: vi
        .fn()
        .mockResolvedValue({ data: { user: { id: "user-1" } }, error: null }),
      signInWithOAuth: vi.fn().mockResolvedValue({
        data: { url: "https://provider.example.com/oauth" },
        error: null,
      }),
      signOut: vi.fn().mockResolvedValue({ error: null }),
      ...overrides,
    },
  };
}

// Mock @/lib/supabase/server
const mockCreateServerSupabaseClient = vi.fn();
vi.mock("@/lib/supabase/server", () => ({
  createServerSupabaseClient: () => mockCreateServerSupabaseClient(),
}));

// Import after mocks are set up
import {
  signUp,
  signInWithPassword,
  signInWithOAuth,
  signOut,
} from "../auth";

describe("Server Actions — auth", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockRedirect.mockReset();
  });

  // ─── signUp ───────────────────────────────────────────────────────────────

  describe("signUp", () => {
    it("calls supabase.auth.signUp with email and password from FormData", async () => {
      const mockClient = buildMockSupabaseClient();
      mockCreateServerSupabaseClient.mockResolvedValue(mockClient);

      const formData = new FormData();
      formData.set("email", "test@example.com");
      formData.set("password", "password123");

      await expect(signUp(formData)).rejects.toThrow("NEXT_REDIRECT:/");

      expect(mockClient.auth.signUp).toHaveBeenCalledWith(
        expect.objectContaining({
          email: "test@example.com",
          password: "password123",
        })
      );
      expect(mockRedirect).toHaveBeenCalledWith("/");
    });

    it("redirects to /login?message=check-your-email when session is null (email confirmation pending)", async () => {
      const mockClient = buildMockSupabaseClient({
        signUp: vi.fn().mockResolvedValue({
          data: { user: { id: "user-1" }, session: null },
          error: null,
        }),
      });
      mockCreateServerSupabaseClient.mockResolvedValue(mockClient);

      const formData = new FormData();
      formData.set("email", "new@example.com");
      formData.set("password", "password123");

      await expect(signUp(formData)).rejects.toThrow("NEXT_REDIRECT:/login");

      expect(mockRedirect).toHaveBeenCalledWith(
        expect.stringContaining("message=")
      );
      expect(mockRedirect).toHaveBeenCalledWith(
        expect.stringContaining("check-your-email")
      );
    });

    it("redirects to /login with error param on Supabase signUp failure", async () => {
      const mockClient = buildMockSupabaseClient({
        signUp: vi.fn().mockResolvedValue({
          data: { user: null, session: null },
          error: { message: "Email already registered" },
        }),
      });
      mockCreateServerSupabaseClient.mockResolvedValue(mockClient);

      const formData = new FormData();
      formData.set("email", "exists@example.com");
      formData.set("password", "password123");

      await expect(signUp(formData)).rejects.toThrow("NEXT_REDIRECT:/login");

      expect(mockRedirect).toHaveBeenCalledWith(
        expect.stringContaining("/login?error=")
      );
    });

    it("does not expose raw Supabase error messages in redirect URL", async () => {
      const mockClient = buildMockSupabaseClient({
        signUp: vi.fn().mockResolvedValue({
          data: { user: null, session: null },
          error: { message: "Internal Supabase error details" },
        }),
      });
      mockCreateServerSupabaseClient.mockResolvedValue(mockClient);

      const formData = new FormData();
      formData.set("email", "test@example.com");
      formData.set("password", "password123");

      await expect(signUp(formData)).rejects.toThrow();

      const redirectUrl = mockRedirect.mock.calls[0]?.[0] as string;
      // Checked decoded, since the previous assertion passed only because
      // encodeURIComponent had replaced the spaces.
      expect(decodeURIComponent(redirectUrl)).not.toContain(
        "Internal Supabase error details"
      );
      expect(redirectUrl).toBe("/login?error=signup_failed");
    });

    it("redirects to /login with error when email or password is missing", async () => {
      const mockClient = buildMockSupabaseClient();
      mockCreateServerSupabaseClient.mockResolvedValue(mockClient);

      const formData = new FormData();
      // no email or password set

      await expect(signUp(formData)).rejects.toThrow("NEXT_REDIRECT:/login");

      expect(mockClient.auth.signUp).not.toHaveBeenCalled();
      expect(mockRedirect).toHaveBeenCalledWith(
        expect.stringContaining("/login?error=")
      );
    });
  });

  // ─── signInWithPassword ───────────────────────────────────────────────────

  describe("signInWithPassword", () => {
    it("calls supabase.auth.signInWithPassword with email and password from FormData", async () => {
      const mockClient = buildMockSupabaseClient();
      mockCreateServerSupabaseClient.mockResolvedValue(mockClient);

      const formData = new FormData();
      formData.set("email", "user@example.com");
      formData.set("password", "secret");

      await expect(signInWithPassword(formData)).rejects.toThrow(
        "NEXT_REDIRECT:/"
      );

      expect(mockClient.auth.signInWithPassword).toHaveBeenCalledWith({
        email: "user@example.com",
        password: "secret",
      });
      expect(mockRedirect).toHaveBeenCalledWith("/");
    });

    it("redirects to /login with error param on invalid credentials", async () => {
      const mockClient = buildMockSupabaseClient({
        signInWithPassword: vi.fn().mockResolvedValue({
          data: { user: null },
          error: { message: "Invalid login credentials" },
        }),
      });
      mockCreateServerSupabaseClient.mockResolvedValue(mockClient);

      const formData = new FormData();
      formData.set("email", "user@example.com");
      formData.set("password", "wrongpassword");

      await expect(signInWithPassword(formData)).rejects.toThrow(
        "NEXT_REDIRECT:/login"
      );

      expect(mockRedirect).toHaveBeenCalledWith("/login?error=invalid_credentials");
    });

    it("classifies a bad-credential error by error.code when the message is unhelpful", async () => {
      const mockClient = buildMockSupabaseClient({
        signInWithPassword: vi.fn().mockResolvedValue({
          data: { user: null },
          error: { code: "invalid_credentials", status: 400, message: "Bad Request" },
        }),
      });
      mockCreateServerSupabaseClient.mockResolvedValue(mockClient);

      const formData = new FormData();
      formData.set("email", "user@example.com");
      formData.set("password", "wrongpassword");

      await expect(signInWithPassword(formData)).rejects.toThrow();

      expect(mockRedirect).toHaveBeenCalledWith("/login?error=invalid_credentials");
    });

    it("distinguishes an unconfirmed email from a wrong password", async () => {
      const mockClient = buildMockSupabaseClient({
        signInWithPassword: vi.fn().mockResolvedValue({
          data: { user: null },
          error: { code: "email_not_confirmed", message: "Email not confirmed" },
        }),
      });
      mockCreateServerSupabaseClient.mockResolvedValue(mockClient);

      const formData = new FormData();
      formData.set("email", "user@example.com");
      formData.set("password", "secret");

      await expect(signInWithPassword(formData)).rejects.toThrow();

      expect(mockRedirect).toHaveBeenCalledWith("/login?error=email_not_confirmed");
    });

    it("falls back to invalid_credentials for an unrecognised sign-in failure", async () => {
      const mockClient = buildMockSupabaseClient({
        signInWithPassword: vi.fn().mockResolvedValue({
          data: { user: null },
          error: { message: "some brand new upstream failure" },
        }),
      });
      mockCreateServerSupabaseClient.mockResolvedValue(mockClient);

      const formData = new FormData();
      formData.set("email", "user@example.com");
      formData.set("password", "secret");

      await expect(signInWithPassword(formData)).rejects.toThrow();

      expect(mockRedirect).toHaveBeenCalledWith("/login?error=invalid_credentials");
    });

    it("redirects to /login with error when email or password is missing", async () => {
      const mockClient = buildMockSupabaseClient();
      mockCreateServerSupabaseClient.mockResolvedValue(mockClient);

      const formData = new FormData();
      // no email or password set

      await expect(signInWithPassword(formData)).rejects.toThrow(
        "NEXT_REDIRECT:/login"
      );

      expect(mockClient.auth.signInWithPassword).not.toHaveBeenCalled();
      expect(mockRedirect).toHaveBeenCalledWith("/login?error=missing_fields");
    });
  });

  // ─── signInWithOAuth ──────────────────────────────────────────────────────

  describe("signInWithOAuth", () => {
    it("calls supabase.auth.signInWithOAuth with the given provider and returns the OAuth URL", async () => {
      const mockClient = buildMockSupabaseClient();
      mockCreateServerSupabaseClient.mockResolvedValue(mockClient);

      const result = await signInWithOAuth("google");

      expect(mockClient.auth.signInWithOAuth).toHaveBeenCalledWith(
        expect.objectContaining({ provider: "google" })
      );
      expect(result).toEqual({ url: "https://provider.example.com/oauth" });
    });

    it("returns error object on OAuth failure", async () => {
      const mockClient = buildMockSupabaseClient({
        signInWithOAuth: vi.fn().mockResolvedValue({
          data: { url: null },
          error: { message: "OAuth provider not configured" },
        }),
      });
      mockCreateServerSupabaseClient.mockResolvedValue(mockClient);

      const result = await signInWithOAuth("github");

      expect(result).toEqual({ error: "OAuth sign-in failed. Please try again." });
    });
  });

  // ─── signOut ──────────────────────────────────────────────────────────────

  describe("signOut", () => {
    it("calls supabase.auth.signOut and redirects to /login", async () => {
      const mockClient = buildMockSupabaseClient();
      mockCreateServerSupabaseClient.mockResolvedValue(mockClient);

      await expect(signOut()).rejects.toThrow("NEXT_REDIRECT:/login");

      expect(mockClient.auth.signOut).toHaveBeenCalled();
      expect(mockRedirect).toHaveBeenCalledWith("/login");
    });

    it("redirects to /login even if signOut returns an error", async () => {
      const mockClient = buildMockSupabaseClient({
        signOut: vi.fn().mockResolvedValue({
          error: { message: "Session already expired" },
        }),
      });
      mockCreateServerSupabaseClient.mockResolvedValue(mockClient);

      await expect(signOut()).rejects.toThrow("NEXT_REDIRECT:/login");

      expect(mockRedirect).toHaveBeenCalledWith("/login");
    });
  });
});

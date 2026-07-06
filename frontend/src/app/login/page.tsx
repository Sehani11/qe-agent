import { signUp, signInWithPassword } from "@/app/actions/auth";
import OAuthButton from "@/components/auth/OAuthButton";

const ERROR_MESSAGES: Record<string, string> = {
  oauth_failed: "OAuth sign-in failed. Please try again.",
  "Registration failed. Please try again.": "Registration failed. Please try again.",
  "Invalid email or password.": "Invalid email or password.",
  "Email and password are required.": "Email and password are required.",
};

interface LoginPageProps {
  searchParams: Promise<{ error?: string; message?: string }>;
}

export default async function LoginPage({ searchParams }: LoginPageProps) {
  const { error, message } = await searchParams;

  const errorMessage = error ? (ERROR_MESSAGES[decodeURIComponent(error)] ?? null) : null;
  const infoMessage =
    message === "check-your-email"
      ? "Account created! Check your email to confirm your address before signing in."
      : null;

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top,_#dbeafe,_#eff6ff_32%,_#f8fafc_70%)] flex items-center justify-center px-4">
      <div className="w-full max-w-md space-y-6">
        {/* Header */}
        <div className="text-center space-y-2">
          <span className="inline-flex rounded-full bg-sky-100 px-4 py-1 text-sm font-semibold tracking-wide text-sky-700">
            QE Verification Agent
          </span>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">
            Sign in or create an account
          </h1>
        </div>

        {/* Info message (e.g. email confirmation prompt) */}
        {infoMessage && (
          <div className="rounded-xl bg-sky-50 border border-sky-200 px-4 py-3 text-sm text-sky-700">
            {infoMessage}
          </div>
        )}

        {/* Error message — only known codes rendered */}
        {errorMessage && (
          <div className="rounded-xl bg-rose-50 border border-rose-200 px-4 py-3 text-sm text-rose-700">
            {errorMessage}
          </div>
        )}

        {/* Card */}
        <div className="rounded-3xl border border-sky-100 bg-white/90 p-8 shadow-[0_24px_80px_-24px_rgba(14,116,144,0.22)] backdrop-blur space-y-6">
          {/* OAuth */}
          {/* <div className="space-y-3">
            <OAuthButton provider="google" label="Continue with Google" />
            <OAuthButton provider="github" label="Continue with GitHub" />
          </div> */}

          {/* <div className="relative flex items-center gap-3">
            <div className="flex-1 border-t border-slate-200" />
            <span className="text-xs font-medium text-slate-400 uppercase tracking-wide">
              or
            </span>
            <div className="flex-1 border-t border-slate-200" />
          </div> */}

          {/* Sign in form */}
          <form action={signInWithPassword} className="space-y-4">
            <div className="space-y-1">
              <label
                htmlFor="signin-email"
                className="text-sm font-medium text-slate-700"
              >
                Email
              </label>
              <input
                id="signin-email"
                name="email"
                type="email"
                required
                autoComplete="email"
                className="w-full rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-900 placeholder:text-slate-400 focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-100"
                placeholder="you@example.com"
              />
            </div>
            <div className="space-y-1">
              <label
                htmlFor="signin-password"
                className="text-sm font-medium text-slate-700"
              >
                Password
              </label>
              <input
                id="signin-password"
                name="password"
                type="password"
                required
                autoComplete="current-password"
                className="w-full rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-900 placeholder:text-slate-400 focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-100"
                placeholder="••••••••"
              />
            </div>
            <button
              type="submit"
              className="w-full rounded-xl bg-gradient-to-r from-blue-600 to-sky-500 px-6 py-3 text-sm font-semibold text-white shadow-lg shadow-blue-200 transition hover:from-blue-500 hover:to-sky-400"
            >
              Sign In
            </button>
          </form>

          <div className="relative flex items-center gap-3">
            <div className="flex-1 border-t border-slate-200" />
            <span className="text-xs font-medium text-slate-400 uppercase tracking-wide">
              new here?
            </span>
            <div className="flex-1 border-t border-slate-200" />
          </div>

          {/* Registration form */}
          <form action={signUp} className="space-y-4">
            <div className="space-y-1">
              <label
                htmlFor="signup-email"
                className="text-sm font-medium text-slate-700"
              >
                Email
              </label>
              <input
                id="signup-email"
                name="email"
                type="email"
                required
                autoComplete="email"
                className="w-full rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-900 placeholder:text-slate-400 focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-100"
                placeholder="you@example.com"
              />
            </div>
            <div className="space-y-1">
              <label
                htmlFor="signup-password"
                className="text-sm font-medium text-slate-700"
              >
                Password
              </label>
              <input
                id="signup-password"
                name="password"
                type="password"
                required
                autoComplete="new-password"
                minLength={6}
                className="w-full rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-900 placeholder:text-slate-400 focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-100"
                placeholder="••••••••"
              />
            </div>
            <button
              type="submit"
              className="w-full rounded-xl border border-sky-200 bg-sky-50 px-6 py-3 text-sm font-semibold text-sky-700 transition hover:bg-sky-100"
            >
              Create Account
            </button>
          </form>
        </div>
      </div>
    </main>
  );
}

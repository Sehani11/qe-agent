import { signUp, signInWithPassword } from "@/app/actions/auth";
import SubmitButton from "@/components/auth/SubmitButton";
import ThemeToggle from "@/components/layout/ThemeToggle";
import { Wordmark } from "@/components/layout/AppNav";
import { Field, Input } from "@/components/ui/field";
import { resolveAuthError } from "@/lib/auth/errors";
// OAuthButton is imported where the commented-out OAuth block below is
// re-enabled — restore the import with it.

interface LoginPageProps {
  searchParams: Promise<{ error?: string; message?: string }>;
}

export default async function LoginPage({ searchParams }: LoginPageProps) {
  const { error, message } = await searchParams;

  // searchParams arrive already decoded, so this must not decode again: a
  // second pass throws URIError on any stray "%" and would take the whole page
  // down instead of showing the failure it was meant to describe.
  const errorMessage = resolveAuthError(error);
  const infoMessage =
    message === "check-your-email"
      ? "Account created. Check your email to confirm the address, then sign in."
      : null;

  return (
    <div className="flex min-h-screen flex-col">
      {/* Border spans the viewport; the shell inside keeps the wordmark on
          the same left edge it has everywhere else in the app. */}
      <header className="border-b border-rule">
        <div className="app-shell flex h-14 items-center justify-between">
          <Wordmark />
          <ThemeToggle />
        </div>
      </header>

      <main
        id="main"
        className="flex flex-1 items-center justify-center px-5 py-10 sm:px-6 sm:py-16 lg:px-8"
      >
        <div className="w-full max-w-sm">
          <p className="eyebrow text-pass">Sign in</p>
          <h1 className="mt-3 text-[1.75rem] leading-tight">
            Pick up a session
          </h1>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            Your sessions, generated scenarios and verification runs stay tied to
            this account.
          </p>

          {/* Auth outcomes stay inline rather than becoming toasts: they belong
              to the form, and a dismissible toast can be missed on reload. */}
          {infoMessage && (
            <div
              className="gutter-rule mt-6 rounded-md border border-pass/30 bg-pass-soft/60 px-4 py-3"
              data-signal="pass"
              role="status"
            >
              <p className="eyebrow text-pass-ink">Check your email</p>
              <p className="mt-1 text-[0.8125rem] leading-snug text-foreground">
                {infoMessage}
              </p>
            </div>
          )}

          {errorMessage && (
            <div
              className="gutter-rule mt-6 rounded-md border border-fail/30 bg-fail-soft/60 px-4 py-3"
              data-signal="fail"
              role="alert"
            >
              <p className="eyebrow text-fail-ink">Failed</p>
              <p className="mt-1 text-[0.8125rem] leading-snug text-foreground">
                {errorMessage}
              </p>
            </div>
          )}

          <div className="mt-7 rounded-lg border border-rule bg-card p-6">
            {/* OAuth */}
            {/* <div className="space-y-3">
              <OAuthButton provider="google" label="Continue with Google" />
              <OAuthButton provider="github" label="Continue with GitHub" />
            </div>

            <div className="relative my-6 flex items-center gap-3">
              <div className="h-px flex-1 bg-rule" />
              <span className="eyebrow text-muted-foreground">or</span>
              <div className="h-px flex-1 bg-rule" />
            </div> */}

            <form action={signInWithPassword} className="space-y-4">
              <Field label="Email" htmlFor="signin-email">
                <Input
                  id="signin-email"
                  name="email"
                  type="email"
                  required
                  autoComplete="email"
                  placeholder="you@example.com"
                />
              </Field>
              <Field label="Password" htmlFor="signin-password">
                <Input
                  id="signin-password"
                  name="password"
                  type="password"
                  required
                  autoComplete="current-password"
                  placeholder="••••••••"
                />
              </Field>
              <SubmitButton pendingLabel="Signing in" className="w-full">
                Sign in
              </SubmitButton>
            </form>

            <div className="relative my-6 flex items-center gap-3">
              <div className="h-px flex-1 bg-rule" />
              <span className="eyebrow text-muted-foreground">New here</span>
              <div className="h-px flex-1 bg-rule" />
            </div>

            <form action={signUp} className="space-y-4">
              <Field label="Email" htmlFor="signup-email">
                <Input
                  id="signup-email"
                  name="email"
                  type="email"
                  required
                  autoComplete="email"
                  placeholder="you@example.com"
                />
              </Field>
              <Field
                label="Password"
                htmlFor="signup-password"
                description="At least 6 characters."
              >
                <Input
                  id="signup-password"
                  name="password"
                  type="password"
                  required
                  autoComplete="new-password"
                  minLength={6}
                  placeholder="••••••••"
                />
              </Field>
              <SubmitButton
                pendingLabel="Creating account"
                variant="outline"
                className="w-full"
              >
                Create account
              </SubmitButton>
            </form>
          </div>
        </div>
      </main>
    </div>
  );
}

"use client";

import { useState } from "react";
import { signInWithOAuth } from "@/app/actions/auth";

interface OAuthButtonProps {
  provider: "google" | "github";
  label: string;
}

export default function OAuthButton({ provider, label }: OAuthButtonProps) {
  const [error, setError] = useState<string | null>(null);
  const [isPending, setIsPending] = useState(false);

  async function handleClick() {
    setError(null);
    setIsPending(true);
    try {
      const result = await signInWithOAuth(provider);
      if ("url" in result) {
        window.location.href = result.url;
      } else {
        setError(result.error);
        setIsPending(false);
      }
    } catch {
      setError("OAuth sign-in failed. Please try again.");
      setIsPending(false);
    }
  }

  return (
    <div className="space-y-1">
      <button
        onClick={handleClick}
        disabled={isPending}
        className="w-full rounded-xl border border-slate-200 bg-white px-6 py-2.5 text-sm font-semibold text-slate-700 shadow-sm transition hover:bg-slate-50 hover:border-slate-300 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {isPending ? "Redirecting…" : label}
      </button>
      {error && (
        <p className="text-xs text-rose-600">{error}</p>
      )}
    </div>
  );
}

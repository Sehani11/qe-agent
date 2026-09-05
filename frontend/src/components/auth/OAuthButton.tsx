"use client";

import { useState } from "react";
import { signInWithOAuth } from "@/app/actions/auth";
import { Button } from "@/components/ui/button";

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
    <div className="space-y-1.5">
      <Button
        variant="outline"
        size="lg"
        onClick={handleClick}
        disabled={isPending}
        loading={isPending}
        className="w-full"
      >
        {isPending ? "Redirecting…" : label}
      </Button>
      {error && (
        <p className="text-xs text-fail-ink" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

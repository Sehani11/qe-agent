"use server";

import { redirect } from "next/navigation";
import { createServerSupabaseClient } from "@/lib/supabase/server";

export async function signUp(formData: FormData): Promise<void> {
  const email = formData.get("email") as string;
  const password = formData.get("password") as string;

  if (!email || !password) {
    redirect("/login?error=" + encodeURIComponent("Email and password are required."));
  }

  const supabase = await createServerSupabaseClient();
  const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000";
  const { data, error } = await supabase.auth.signUp({
    email,
    password,
    options: {
      emailRedirectTo: `${siteUrl}/`,
    },
  });

  if (error) {
    redirect(
      "/login?error=" +
        encodeURIComponent(error.message || "Registration failed. Please try again.")
    );
  }

  // Supabase requires email confirmation by default — session is null until confirmed
  if (!data.session) {
    redirect("/login?message=" + encodeURIComponent("check-your-email"));
  }

  redirect("/");
}

export async function signInWithPassword(formData: FormData): Promise<void> {
  const email = formData.get("email") as string;
  const password = formData.get("password") as string;

  if (!email || !password) {
    redirect("/login?error=" + encodeURIComponent("Email and password are required."));
  }

  const supabase = await createServerSupabaseClient();
  const { error } = await supabase.auth.signInWithPassword({ email, password });

  if (error) {
    redirect(
      "/login?error=" + encodeURIComponent(error.message || "Invalid email or password.")
    );
  }

  redirect("/");
}

export async function signInWithOAuth(
  provider: "google" | "github"
): Promise<{ url: string } | { error: string }> {
  const supabase = await createServerSupabaseClient();
  const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000";

  const { data, error } = await supabase.auth.signInWithOAuth({
    provider,
    options: {
      redirectTo: `${siteUrl}/auth/callback`,
    },
  });

  if (error || !data.url) {
    return { error: "OAuth sign-in failed. Please try again." };
  }

  return { url: data.url };
}

export async function signOut(): Promise<void> {
  const supabase = await createServerSupabaseClient();
  await supabase.auth.signOut();
  redirect("/login");
}

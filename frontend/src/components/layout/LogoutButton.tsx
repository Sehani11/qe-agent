"use client";

import { signOut } from "@/app/actions/auth";

export default function LogoutButton() {
  return (
    <form action={signOut}>
      <button
        type="submit"
        className="text-sm font-medium text-slate-500 transition hover:text-slate-800"
      >
        Log out
      </button>
    </form>
  );
}

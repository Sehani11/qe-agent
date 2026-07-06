"use client";

import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import LogoutButton from "@/components/layout/LogoutButton";
import { useSessionContext } from "@/context/SessionContext";

export default function Home() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { resetSession } = useSessionContext();

  const handleStartNewSession = () => {
    resetSession();
    // Drop any cached session/BDD/verification data from past sessions so it
    // can't be served back to the new session page on mount.
    queryClient.removeQueries({ queryKey: ["sessions"] });
    router.push(`/session/${crypto.randomUUID()}`);
  };

  return (
    <main className="relative flex min-h-screen flex-col items-center justify-center overflow-hidden bg-[radial-gradient(circle_at_top,_#dbeafe,_#eff6ff_32%,_#f8fafc_70%)] px-6 text-slate-900">
      {/* Top-right actions */}
      <div className="absolute right-6 top-6 flex items-center gap-4">
        <Link
          href="/sessions"
          className="text-sm font-medium text-slate-500 transition hover:text-sky-600"
        >
          Past Sessions
        </Link>
        <LogoutButton />
      </div>

      {/* Hero */}
      <div className="mx-auto max-w-2xl space-y-8 text-center">
        <span className="inline-flex rounded-full bg-sky-100 px-4 py-1 text-sm font-semibold tracking-wide text-sky-700">
          QE Verification Agent
        </span>

        <h1 className="text-4xl font-bold tracking-tight text-slate-900 sm:text-6xl">
          Turn Jira tickets into{" "}
          <span className="bg-gradient-to-r from-blue-600 to-sky-500 bg-clip-text text-transparent">
            BDD test scenarios
          </span>
        </h1>

        <p className="mx-auto max-w-xl text-base leading-7 text-slate-600 sm:text-lg">
          Bring in a Jira ticket, generate structured Gherkin scenarios, verify
          them against your codebase, and export as a feature file or CSV.
        </p>

        <div className="flex flex-col items-center gap-3 sm:flex-row sm:justify-center">
          <button
            onClick={handleStartNewSession}
            className="w-full rounded-xl bg-gradient-to-r from-blue-600 to-sky-500 px-8 py-3.5 text-sm font-semibold text-white shadow-lg shadow-blue-200 transition hover:from-blue-500 hover:to-sky-400 sm:w-auto"
          >
            Start New Session
          </button>
          <Link
            href="/sessions"
            className="w-full rounded-xl border border-slate-200 bg-white px-8 py-3.5 text-center text-sm font-semibold text-slate-700 shadow-sm transition hover:border-sky-300 hover:text-sky-700 sm:w-auto"
          >
            View Past Sessions
          </Link>
        </div>
      </div>

      {/* Feature cards */}
      <div className="mx-auto mt-20 grid max-w-3xl gap-4 sm:grid-cols-3">
        {[
          {
            step: "1",
            color: "bg-sky-50 text-sky-800",
            title: "Fetch Jira Ticket",
            desc: "Enter a ticket ID or URL to pull acceptance criteria automatically.",
          },
          {
            step: "2",
            color: "bg-blue-50 text-blue-800",
            title: "Generate BDD/Test Cases",
            desc: "AI converts acceptance criteria into structured Gherkin scenarios.",
          },
          {
            step: "3",
            color: "bg-indigo-50 text-indigo-800",
            title: "Verify & Export",
            desc: "Verify against your GitHub repo and export as .feature or CSV.",
          },
        ].map(({ step, color, title, desc }) => (
          <div key={step} className={`rounded-2xl p-5 ${color.split(" ")[0]}`}>
            <span className={`text-xs font-bold uppercase tracking-widest ${color.split(" ")[1]}`}>
              Step {step}
            </span>
            <p className="mt-2 text-sm font-semibold text-slate-900">{title}</p>
            <p className="mt-1 text-sm text-slate-600">{desc}</p>
          </div>
        ))}
      </div>
    </main>
  );
}

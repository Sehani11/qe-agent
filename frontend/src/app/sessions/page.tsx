"use client";

import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { ArrowLeft, Plus } from "lucide-react";
import LogoutButton from "@/components/layout/LogoutButton";
import { useSessionContext } from "@/context/SessionContext";
import { useSessionList } from "@/lib/hooks/useSession";
import type { SessionListItem } from "@/lib/types/session";

function BddStatusBadge({ status }: { status: SessionListItem["bdd_status"] }) {
  if (status === "generated") {
    return (
      <span className="inline-flex rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-medium text-emerald-700">
        Generated
      </span>
    );
  }
  if (status === "uploaded") {
    return (
      <span className="inline-flex rounded-full bg-sky-100 px-2.5 py-0.5 text-xs font-medium text-sky-700">
        Uploaded
      </span>
    );
  }
  return (
    <span className="inline-flex rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-500">
      None
    </span>
  );
}

export default function SessionsPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { resetSession } = useSessionContext();
  const { data: sessions, isLoading, isError } = useSessionList();

  const handleStartNewSession = () => {
    resetSession();
    queryClient.removeQueries({ queryKey: ["sessions"] });
    router.push(`/session/${crypto.randomUUID()}`);
  };

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top,_#dbeafe,_#eff6ff_32%,_#f8fafc_70%)] text-slate-900">
      {/* Header */}
      <header className="border-b border-sky-100 bg-white/80 px-6 py-4 backdrop-blur">
        <div className="mx-auto flex max-w-4xl items-center justify-between">
          <Link
            href="/"
            className="inline-flex items-center gap-1.5 text-sm font-medium text-slate-500 transition hover:text-sky-600"
          >
            <ArrowLeft className="h-4 w-4" />
            Home
          </Link>
          <div className="flex items-center gap-4">
            <button
              onClick={handleStartNewSession}
              className="inline-flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-blue-600 to-sky-500 px-4 py-2 text-sm font-semibold text-white shadow transition hover:from-blue-500 hover:to-sky-400"
            >
              <Plus className="h-4 w-4" />
              New Session
            </button>
            <LogoutButton />
          </div>
        </div>
      </header>

      {/* Content */}
      <div className="mx-auto max-w-4xl px-6 py-10">
        <div className="mb-6">
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">Past Sessions</h1>
          <p className="mt-1 text-sm text-slate-500">
            Pick up where you left off or review previously generated scenarios.
          </p>
        </div>

        {isLoading && (
          <div className="flex items-center gap-2 py-12 text-sm text-slate-500">
            <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-sky-500" />
            Loading sessions…
          </div>
        )}

        {isError && (
          <p className="py-12 text-sm text-red-600">
            Failed to load sessions. Please refresh the page.
          </p>
        )}

        {!isLoading && !isError && sessions?.length === 0 && (
          <div className="flex flex-col items-center gap-4 py-20 text-center">
            <p className="text-slate-500">No past sessions yet.</p>
            <button
              onClick={handleStartNewSession}
              className="rounded-xl bg-gradient-to-r from-blue-600 to-sky-500 px-6 py-2.5 text-sm font-semibold text-white shadow transition hover:from-blue-500 hover:to-sky-400"
            >
              Start Your First Session
            </button>
          </div>
        )}

        {!isLoading && !isError && sessions && sessions.length > 0 && (
          <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50 text-left">
                  <th className="px-5 py-3 font-semibold text-slate-700">Jira Ticket</th>
                  <th className="px-5 py-3 font-semibold text-slate-700">Created</th>
                  <th className="px-5 py-3 font-semibold text-slate-700">BDD Status</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((session) => (
                  <tr
                    key={session.id}
                    onClick={() => router.push(`/session/${session.id}`)}
                    className="cursor-pointer border-b border-slate-100 transition-colors last:border-0 hover:bg-sky-50"
                  >
                    <td className="px-5 py-3.5 font-medium text-slate-900">
                      {session.jira_ticket_id}
                    </td>
                    <td className="px-5 py-3.5 text-slate-500">
                      {new Date(session.created_at).toLocaleDateString(undefined, {
                        year: "numeric",
                        month: "short",
                        day: "numeric",
                      })}
                    </td>
                    <td className="px-5 py-3.5">
                      <BddStatusBadge status={session.bdd_status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </main>
  );
}

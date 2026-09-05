"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, FileText, Plus, Trash2, X } from "lucide-react";

import AppNav from "@/components/layout/AppNav";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { EmptyState, ErrorState } from "@/components/ui/empty-state";
import { SkeletonRows } from "@/components/ui/loaders";
import { useToast } from "@/components/ui/toast";
import { useSessionContext } from "@/context/SessionContext";
import {
  SESSIONS_PAGE_SIZE,
  useDeleteAllSessions,
  useDeleteSession,
  useDeleteSessions,
  useSessionList,
} from "@/lib/hooks/useSession";
import type { SessionListItem } from "@/lib/types/session";

/**
 * How far this session has got, as one badge.
 *
 * The stages are cumulative, so the FURTHEST one reached is what gets shown:
 * a verified session still has a BDD, and a session with a BDD was still
 * fetched from Jira first. Showing anything but the last stage would understate
 * the work already done.
 *
 * Verification is a separate field rather than another bdd_status value,
 * because the two axes are independent server-side — this component is where
 * they collapse into a single thing to read at a glance.
 */
function SessionStatusBadge({ session }: { session: SessionListItem }) {
  if (session.verification_status === "completed") {
    return <Badge signal="pass">Verified</Badge>;
  }
  // The BDD stages, newest artefact wins. An edit is generated-or-uploaded
  // content the user has since revised, so it reads as its own state.
  if (session.bdd_status === "edited") return <Badge signal="pending">BDD edited</Badge>;
  if (session.bdd_status === "uploaded") return <Badge signal="keyword">BDD uploaded</Badge>;
  if (session.bdd_status === "generated") return <Badge signal="keyword">BDD generated</Badge>;
  // No BDD yet. A session only exists because a ticket was fetched, so this is
  // a real stage rather than an absence — "No BDD" described what had not
  // happened instead of what had.
  return <Badge signal="neutral">Jira fetched</Badge>;
}

export default function SessionsPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { resetSession } = useSessionContext();
  const toast = useToast();
  // Page index, not offset — the pager reads better in pages and the offset is
  // derived in one place. Deliberately local: opening a session and coming back
  // is a fresh visit to the list, and the first page is the right place to land.
  const [page, setPage] = useState(0);
  const offset = page * SESSIONS_PAGE_SIZE;

  const { data, isLoading, isError, isPlaceholderData, refetch } = useSessionList({
    offset,
  });

  const deleteSession = useDeleteSession();
  const [pendingDelete, setPendingDelete] = useState<SessionListItem | null>(null);

  const confirmDelete = () => {
    if (!pendingDelete) return;
    const ticket = pendingDelete.jira_ticket_id;
    deleteSession.mutate(pendingDelete.id, {
      onSuccess: () => {
        toast.success("Session deleted", {
          description: `${ticket} and its scenarios and verdicts are gone.`,
        });
      },
      onError: () => {
        toast.error("Could not delete that session", {
          description: "It is still in your history. Try again.",
        });
      },
      onSettled: () => setPendingDelete(null),
    });
  };

  // Multi-select, scoped to the page on screen: ids the API has not returned
  // cannot be selected, so this never accumulates a set larger than what the
  // person can actually see checked.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // Tracks which page `selected` belongs to. Adjusted during render — same
  // technique as the page-clamping below — so switching pages clears the
  // previous page's checkmarks before they ever paint on the new one.
  const [selectedPage, setSelectedPage] = useState(page);
  if (selectedPage !== page) {
    setSelectedPage(page);
    if (selected.size > 0) setSelected(new Set());
  }

  const deleteSessions = useDeleteSessions();
  const [pendingBulkDelete, setPendingBulkDelete] = useState(false);

  const confirmBulkDelete = () => {
    const ids = Array.from(selected);
    deleteSessions.mutate(ids, {
      onSuccess: ({ succeeded, failed }) => {
        if (failed === 0) {
          toast.success(`Deleted ${succeeded} session${succeeded === 1 ? "" : "s"}`, {
            description: "Their scenarios, verdicts, and chat history are gone.",
          });
        } else {
          toast.error(`Deleted ${succeeded} of ${succeeded + failed}`, {
            description: "Some sessions could not be deleted. Try again for the rest.",
          });
        }
        setSelected(new Set());
      },
      onError: () => {
        toast.error("Could not delete the selected sessions", {
          description: "Try again.",
        });
      },
      onSettled: () => setPendingBulkDelete(false),
    });
  };

  const deleteAllSessions = useDeleteAllSessions();
  const [pendingDeleteAll, setPendingDeleteAll] = useState(false);

  const confirmDeleteAll = () => {
    deleteAllSessions.mutate(undefined, {
      onSuccess: (deleted) => {
        toast.success(`Deleted ${deleted} session${deleted === 1 ? "" : "s"}`, {
          description: "Your session history for this project is empty.",
        });
        setSelected(new Set());
        setPage(0);
      },
      onError: () => {
        toast.error("Could not clear your sessions", {
          description: "Your sessions are still there. Try again.",
        });
      },
      onSettled: () => setPendingDeleteAll(false),
    });
  };

  const sessions = data?.items;
  const total = data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / SESSIONS_PAGE_SIZE));
  const hasPreviousPage = page > 0;
  const hasNextPage = offset + (sessions?.length ?? 0) < total;
  const allOnPageSelected =
    !!sessions && sessions.length > 0 && sessions.every((s) => selected.has(s.id));

  const toggleSelected = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAllOnPage = () => {
    if (!sessions) return;
    setSelected((prev) => {
      const next = new Set(prev);
      if (allOnPageSelected) {
        sessions.forEach((s) => next.delete(s.id));
      } else {
        sessions.forEach((s) => next.add(s.id));
      }
      return next;
    });
  };

  // Sessions can disappear between visits (another tab, another device), which
  // can leave the requested page past the end. The response still carries the
  // real total, so step back to the last page that exists rather than render an
  // empty list that reads as "no sessions". Adjusted during render, not in an
  // effect, so the corrected page is fetched without committing the empty one.
  if (
    data &&
    !isPlaceholderData &&
    data.items.length === 0 &&
    data.total > 0 &&
    page > pageCount - 1
  ) {
    setPage(pageCount - 1);
  }

  const handleStartNewSession = () => {
    resetSession();
    queryClient.removeQueries({ queryKey: ["sessions"] });
    router.push(`/session/${crypto.randomUUID()}`);
  };

  return (
    <div className="flex min-h-screen flex-col">
      <AppNav
        actions={
          <div className="flex items-center gap-2">
            {total > 0 && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => setPendingDeleteAll(true)}
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                Delete all
              </Button>
            )}
            <Button size="sm" onClick={handleStartNewSession}>
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              New session
            </Button>
          </div>
        }
      />

      <main id="main" className="app-shell flex-1 py-10">
        <div className="mb-7">
          <p className="eyebrow text-muted-foreground">Sessions</p>
          <h1 className="mt-2 text-[1.75rem] leading-tight">Past runs</h1>
          <p className="mt-2 max-w-lg text-sm leading-relaxed text-muted-foreground">
            Every ticket you have ingested, with the scenarios and verdicts that
            came out of it. Open one to keep working.
          </p>
        </div>

        {isLoading && <SkeletonRows rows={4} />}

        {isError && (
          <ErrorState
            title="Could not load your sessions"
            description="The server did not respond. Check your connection and try again."
            onRetry={() => refetch()}
          />
        )}

        {!isLoading && !isError && sessions?.length === 0 && total === 0 && (
          <EmptyState
            icon={FileText}
            title="No sessions yet"
            description="Ingest a Jira ticket and the agent will write your first set of scenarios."
            action={
              <Button onClick={handleStartNewSession}>
                <Plus className="h-4 w-4" aria-hidden="true" />
                Start a session
              </Button>
            }
          />
        )}

        {!isLoading && !isError && sessions && sessions.length > 0 && (
          <>
            {/* Selection toolbar: a "select all on this page" checkbox that
                doubles as the count, and the bulk action it unlocks. Sits
                above the list rather than floating over it, so it never
                covers the last row on a short viewport. */}
            <div className="mb-2 flex h-9 items-center gap-3 px-1">
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                <input
                  type="checkbox"
                  checked={allOnPageSelected}
                  onChange={toggleSelectAllOnPage}
                  aria-label="Select all sessions on this page"
                  className="h-3.5 w-3.5 rounded border-rule accent-keyword"
                />
                {selected.size > 0
                  ? `${selected.size} selected`
                  : "Select all on this page"}
              </label>

              {selected.size > 0 && (
                <>
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={() => setPendingBulkDelete(true)}
                  >
                    <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                    Delete selected
                  </Button>
                  <button
                    type="button"
                    onClick={() => setSelected(new Set())}
                    className="inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
                  >
                    <X className="h-3.5 w-3.5" aria-hidden="true" />
                    Clear
                  </button>
                </>
              )}
            </div>

            <ul className="overflow-hidden rounded-lg border border-rule bg-card">
              {sessions.map((session, index) => (
                <li
                  key={session.id}
                  className="group/row flex items-center border-b border-rule last:border-0"
                  style={{ animation: `row-in 240ms ease-out ${index * 22}ms both` }}
                >
                  <label className="flex shrink-0 items-center self-stretch px-3">
                    <input
                      type="checkbox"
                      checked={selected.has(session.id)}
                      onChange={() => toggleSelected(session.id)}
                      aria-label={`Select session ${session.jira_ticket_id}`}
                      className="h-3.5 w-3.5 rounded border-rule accent-keyword"
                    />
                  </label>

                  {/* The nav target is one button so the click target matches
                      the affordance, instead of a click handler on a <tr>.
                      The checkbox and delete button are siblings, not nested
                      inside it — a <button> cannot contain other interactive
                      elements. */}
                  <button
                    type="button"
                    onClick={() => router.push(`/session/${session.id}`)}
                    className="group flex flex-1 items-center gap-4 py-3.5 pr-4 text-left transition-colors hover:bg-muted/50 focus-visible:bg-muted/50"
                  >
                    <span className="font-mono text-xs text-muted-foreground/70 tabular-nums">
                      {String(offset + index + 1).padStart(2, "0")}
                    </span>

                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-mono text-sm font-semibold text-foreground">
                        {session.jira_ticket_id}
                      </span>
                      <span className="mt-0.5 block text-xs text-muted-foreground">
                        {new Date(session.created_at).toLocaleDateString(undefined, {
                          year: "numeric",
                          month: "short",
                          day: "numeric",
                        })}
                      </span>
                    </span>

                    <SessionStatusBadge session={session} />

                    <ChevronRight
                      className="h-4 w-4 shrink-0 text-muted-foreground/50 transition-transform group-hover:translate-x-0.5 group-hover:text-foreground"
                      aria-hidden="true"
                    />
                  </button>

                  <button
                    type="button"
                    onClick={() => setPendingDelete(session)}
                    aria-label={`Delete session ${session.jira_ticket_id}`}
                    className="mr-3 shrink-0 rounded p-2 text-muted-foreground/50 opacity-0 transition-colors hover:bg-fail-soft hover:text-fail-ink focus-visible:opacity-100 group-hover/row:opacity-100"
                  >
                    <Trash2 className="h-4 w-4" aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}

        {/* No pager chrome until there is a second page to reach. */}
        {!isLoading && !isError && total > SESSIONS_PAGE_SIZE && (
          <nav
            aria-label="Sessions pagination"
            className="mt-4 flex flex-col-reverse items-center justify-between gap-3 sm:flex-row"
          >
            <p
              className="font-mono text-xs text-muted-foreground tabular-nums"
              // The count changes under the reader when a page loads, so it is
              // announced rather than silently swapped.
              aria-live="polite"
            >
              {offset + 1}&ndash;{offset + (sessions?.length ?? 0)} of {total}
            </p>

            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => setPage((current) => Math.max(0, current - 1))}
                disabled={!hasPreviousPage || isPlaceholderData}
              >
                <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
                Previous
              </Button>

              <span className="font-mono text-xs text-muted-foreground tabular-nums">
                {page + 1} / {pageCount}
              </span>

              <Button
                size="sm"
                variant="outline"
                onClick={() => setPage((current) => current + 1)}
                disabled={!hasNextPage || isPlaceholderData}
              >
                Next
                <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
              </Button>
            </div>
          </nav>
        )}
      </main>

      <ConfirmModal
        open={pendingDelete !== null}
        destructive
        title="Delete session"
        description={
          <>
            Delete{" "}
            <span className="font-semibold text-foreground">
              {pendingDelete?.jira_ticket_id ?? "this session"}
            </span>
            ? Its scenarios, verdicts, and chat history will be gone. This
            can&apos;t be undone.
          </>
        }
        confirmLabel="Delete"
        isConfirming={deleteSession.isPending}
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />

      <ConfirmModal
        open={pendingBulkDelete}
        destructive
        title="Delete selected sessions"
        description={
          <>
            Delete{" "}
            <span className="font-semibold text-foreground">
              {selected.size} session{selected.size === 1 ? "" : "s"}
            </span>
            ? Their scenarios, verdicts, and chat history will be gone. This
            can&apos;t be undone.
          </>
        }
        confirmLabel="Delete"
        isConfirming={deleteSessions.isPending}
        onConfirm={confirmBulkDelete}
        onCancel={() => setPendingBulkDelete(false)}
      />

      <ConfirmModal
        open={pendingDeleteAll}
        destructive
        title="Delete all sessions"
        description={
          <>
            Delete{" "}
            <span className="font-semibold text-foreground">
              all {total} session{total === 1 ? "" : "s"}
            </span>{" "}
            in this project? Every scenario, verdict, and chat history goes
            with them. This can&apos;t be undone.
          </>
        }
        confirmLabel="Delete all"
        isConfirming={deleteAllSessions.isPending}
        onConfirm={confirmDeleteAll}
        onCancel={() => setPendingDeleteAll(false)}
      />
    </div>
  );
}

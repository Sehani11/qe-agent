/**
 * sessions/page.test.tsx
 *
 * Covers pagination of the past-sessions list: the window the page requests,
 * the row numbering across pages, the end-stop states, and the distinction
 * between "this page is empty" and "you have no sessions".
 */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, within } from "@/test/test-utils";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { SessionProvider } from "@/context/SessionContext";
import type { SessionListItem, SessionListPage } from "@/lib/types/session";
import SessionsPage from "@/app/sessions/page";

const PAGE_SIZE = 20;

const push = vi.fn();
vi.mock("next/navigation", () => ({
    useRouter: () => ({ push, replace: vi.fn() }),
    // AppNav highlights the active link, so the nav hook must be mocked too.
    usePathname: () => "/sessions",
}));

// The page under test drives the hook; the hook itself is exercised by its own
// request to the API, so here it is a spy that records the requested window.
const useSessionList = vi.fn();
// Delete hooks are exercised by their own hook tests; here they only need to
// exist, and to record what the page asks them to do.
const useDeleteSession = vi.fn();
const deleteSessionsMutate = vi.fn();
const deleteAllSessionsMutate = vi.fn();
vi.mock("@/lib/hooks/useSession", () => ({
    SESSIONS_PAGE_SIZE: 20,
    useSessionList: (args?: { limit?: number; offset?: number }) => useSessionList(args),
    useDeleteSession: () => useDeleteSession(),
    useDeleteSessions: () => ({ mutate: deleteSessionsMutate, isPending: false }),
    useDeleteAllSessions: () => ({ mutate: deleteAllSessionsMutate, isPending: false }),
}));

function makeSession(
    index: number,
    over: Partial<SessionListItem> = {}
): SessionListItem {
    return {
        id: `session-${index}`,
        user_id: "user-a",
        jira_ticket_id: `PROJ-${index}`,
        jira_ticket_url: `https://acme.atlassian.net/browse/PROJ-${index}`,
        created_at: "2026-04-11T00:00:00Z",
        bdd_status: "generated",
        verification_status: "none",
        ...over,
    };
}

/** Serve pages out of a fixed corpus, the way the API would. */
function servePages(total: number) {
    const all = Array.from({ length: total }, (_, i) => makeSession(i + 1));
    useSessionList.mockImplementation((args?: { offset?: number }) => {
        const offset = args?.offset ?? 0;
        const page: SessionListPage = {
            items: all.slice(offset, offset + PAGE_SIZE),
            total,
            limit: PAGE_SIZE,
            offset,
        };
        return {
            data: page,
            isLoading: false,
            isError: false,
            isPlaceholderData: false,
            refetch: vi.fn(),
        };
    });
}

function renderPage() {
    const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false } },
    });
    return render(
        <QueryClientProvider client={queryClient}>
            <SessionProvider>
                <SessionsPage />
            </SessionProvider>
        </QueryClientProvider>
    );
}

beforeEach(() => {
    useSessionList.mockReset();
    push.mockReset();
    useDeleteSession.mockReset();
    useDeleteSession.mockReturnValue({ mutate: vi.fn(), isPending: false });
    deleteSessionsMutate.mockReset();
    deleteAllSessionsMutate.mockReset();
});

describe("SessionsPage pagination", () => {
    it("requests the first page on mount", () => {
        servePages(42);
        renderPage();

        expect(useSessionList).toHaveBeenCalledWith({ offset: 0 });
        expect(screen.getByText("1–20 of 42")).toBeInTheDocument();
        expect(screen.getByText("1 / 3")).toBeInTheDocument();
    });

    it("requests the next window and continues the row numbering when paging forward", () => {
        servePages(42);
        renderPage();

        fireEvent.click(screen.getByRole("button", { name: /next/i }));

        expect(useSessionList).toHaveBeenLastCalledWith({ offset: PAGE_SIZE });
        expect(screen.getByText("21–40 of 42")).toBeInTheDocument();
        // Row numbers continue across pages rather than restarting at 01.
        expect(screen.getByText("21")).toBeInTheDocument();
        expect(screen.getByText("PROJ-21")).toBeInTheDocument();
    });

    it("disables Previous on the first page and Next on the last", () => {
        servePages(42);
        renderPage();

        expect(screen.getByRole("button", { name: /previous/i })).toBeDisabled();

        fireEvent.click(screen.getByRole("button", { name: /next/i }));
        fireEvent.click(screen.getByRole("button", { name: /next/i }));

        expect(screen.getByText("41–42 of 42")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();
        expect(screen.getByRole("button", { name: /previous/i })).toBeEnabled();
    });

    it("pages back to the previous window", () => {
        servePages(42);
        renderPage();

        fireEvent.click(screen.getByRole("button", { name: /next/i }));
        fireEvent.click(screen.getByRole("button", { name: /previous/i }));

        expect(useSessionList).toHaveBeenLastCalledWith({ offset: 0 });
        expect(screen.getByText("1–20 of 42")).toBeInTheDocument();
    });

    it("renders no pager when everything fits on one page", () => {
        servePages(5);
        renderPage();

        expect(screen.queryByRole("navigation", { name: /pagination/i })).toBeNull();
        expect(screen.getByText("PROJ-1")).toBeInTheDocument();
    });

    it("shows the empty state only when the user has no sessions at all", () => {
        servePages(0);
        renderPage();

        expect(screen.getByText(/no sessions yet/i)).toBeInTheDocument();
    });

    it("steps back to the last real page when the requested one is past the end", () => {
        // First render serves page 2 of 42; the corpus then shrinks to 5, so the
        // requested offset no longer exists and the page must correct itself.
        servePages(42);
        renderPage();
        fireEvent.click(screen.getByRole("button", { name: /next/i }));

        servePages(5);
        fireEvent.click(screen.getByRole("button", { name: /next/i }));

        expect(useSessionList).toHaveBeenLastCalledWith({ offset: 0 });
        expect(screen.queryByText(/no sessions yet/i)).toBeNull();
        expect(screen.getByText("PROJ-1")).toBeInTheDocument();
    });
});

// ---------------------------------------------------------------------------
// Multi-select and bulk delete
// ---------------------------------------------------------------------------

describe("SessionsPage multi-select", () => {
    it("checking a row reveals the bulk delete action with a live count", () => {
        servePages(5);
        renderPage();

        expect(screen.queryByRole("button", { name: /delete selected/i })).toBeNull();

        fireEvent.click(screen.getByLabelText("Select session PROJ-1"));

        expect(screen.getByText("1 selected")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /delete selected/i })).toBeInTheDocument();
    });

    it("the header checkbox selects and clears every row on the page", () => {
        servePages(5);
        renderPage();

        fireEvent.click(
            screen.getByLabelText("Select all sessions on this page")
        );

        expect(screen.getByText("5 selected")).toBeInTheDocument();
        for (let i = 1; i <= 5; i++) {
            expect(screen.getByLabelText(`Select session PROJ-${i}`)).toBeChecked();
        }

        fireEvent.click(
            screen.getByLabelText("Select all sessions on this page")
        );
        expect(screen.getByText("Select all on this page")).toBeInTheDocument();
    });

    it("Clear drops the selection without deleting anything", () => {
        servePages(5);
        renderPage();

        fireEvent.click(screen.getByLabelText("Select session PROJ-1"));
        fireEvent.click(screen.getByRole("button", { name: /^clear$/i }));

        expect(screen.queryByRole("button", { name: /delete selected/i })).toBeNull();
        expect(deleteSessionsMutate).not.toHaveBeenCalled();
    });

    it("confirming the bulk delete sends the selected ids", () => {
        servePages(5);
        renderPage();

        fireEvent.click(screen.getByLabelText("Select session PROJ-1"));
        fireEvent.click(screen.getByLabelText("Select session PROJ-3"));
        fireEvent.click(screen.getByRole("button", { name: /delete selected/i }));

        const dialog = screen.getByRole("dialog");
        expect(within(dialog).getByText(/delete selected sessions/i)).toBeInTheDocument();
        fireEvent.click(within(dialog).getByRole("button", { name: /^delete$/i }));

        expect(deleteSessionsMutate).toHaveBeenCalledWith(
            expect.arrayContaining(["session-1", "session-3"]),
            expect.anything()
        );
    });

    it("switching pages clears the selection made on the previous one", () => {
        servePages(42);
        renderPage();

        fireEvent.click(screen.getByLabelText("Select session PROJ-1"));
        expect(screen.getByText("1 selected")).toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", { name: /next/i }));

        expect(screen.queryByText(/selected$/i)).toBeNull();
        expect(screen.getByText("Select all on this page")).toBeInTheDocument();
    });

    it("offers Delete all only once there is something to delete", () => {
        servePages(0);
        renderPage();
        expect(screen.queryByRole("button", { name: /^delete all$/i })).toBeNull();
    });

    it("confirming Delete all calls the bulk-delete-all mutation", () => {
        servePages(5);
        renderPage();

        fireEvent.click(screen.getByRole("button", { name: /^delete all$/i }));

        const dialog = screen.getByRole("dialog");
        expect(within(dialog).getByText(/delete all sessions/i)).toBeInTheDocument();
        fireEvent.click(within(dialog).getByRole("button", { name: /^delete all$/i }));

        expect(deleteAllSessionsMutate).toHaveBeenCalled();
    });
});

// ---------------------------------------------------------------------------
// Status badge — how far a session has got, as one label
// ---------------------------------------------------------------------------

describe("SessionsPage status badge", () => {
    /** Render a list containing exactly one session in the given state. */
    function renderOne(over: Partial<SessionListItem>) {
        useSessionList.mockReturnValue({
            data: { items: [makeSession(1, over)], total: 1, limit: PAGE_SIZE, offset: 0 },
            isLoading: false,
            isError: false,
            isPlaceholderData: false,
            refetch: vi.fn(),
        });
        renderPage();
    }

    it("names the Jira fetch as a stage of its own", () => {
        // A session only exists because a ticket was fetched, so "No BDD"
        // described what had NOT happened rather than what had.
        renderOne({ bdd_status: "none", verification_status: "none" });
        expect(screen.getByText("Jira fetched")).toBeInTheDocument();
    });

    it("says BDD generated, not just Generated", () => {
        renderOne({ bdd_status: "generated", verification_status: "none" });
        expect(screen.getByText("BDD generated")).toBeInTheDocument();
    });

    it("distinguishes uploaded and edited BDD", () => {
        renderOne({ bdd_status: "uploaded", verification_status: "none" });
        expect(screen.getByText("BDD uploaded")).toBeInTheDocument();
    });

    it("shows verified once a run has produced verdicts", () => {
        renderOne({ bdd_status: "generated", verification_status: "completed" });
        expect(screen.getByText("Verified")).toBeInTheDocument();
    });

    it("shows the FURTHEST stage, not the BDD one", () => {
        // The stages are cumulative: a verified session still has a BDD, and
        // reporting that instead would understate the work already done.
        renderOne({ bdd_status: "edited", verification_status: "completed" });

        expect(screen.getByText("Verified")).toBeInTheDocument();
        expect(screen.queryByText("BDD edited")).not.toBeInTheDocument();
    });

    it("does not claim verification on a session that has none", () => {
        renderOne({ bdd_status: "edited", verification_status: "none" });

        expect(screen.getByText("BDD edited")).toBeInTheDocument();
        expect(screen.queryByText("Verified")).not.toBeInTheDocument();
    });
});

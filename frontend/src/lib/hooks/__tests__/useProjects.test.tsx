/**
 * useSyncActiveProject — keeps the stored project id pointing at a real project.
 *
 * Without it the app can sit on an id that no longer resolves: first visit,
 * a project deleted elsewhere, or a stored id from another account.
 */
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi, beforeEach } from "vitest";

const get = vi.fn();
vi.mock("@/lib/api/client", () => ({
    default: { get: (...a: unknown[]) => get(...a), post: vi.fn(), patch: vi.fn() },
}));

import { useSyncActiveProject } from "@/lib/hooks/useProjects";
import { useProjectStore } from "@/lib/stores/projectStore";

function Probe() {
    useSyncActiveProject();
    const activeProjectId = useProjectStore((s) => s.activeProjectId);
    return <span data-testid="active">{activeProjectId ?? "none"}</span>;
}

/** The client the current probe is rendered with, for tests that invalidate. */
let queryClientRef: QueryClient | null = null;

function renderProbe() {
    const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    queryClientRef = queryClient;
    return render(
        <QueryClientProvider client={queryClient}>
            <Probe />
        </QueryClientProvider>
    );
}

const PROJECTS = [
    { id: "p1", name: "Project-1", created_at: "2026-01-01T00:00:00Z" },
    { id: "p2", name: "Project-2", created_at: "2026-02-01T00:00:00Z" },
];

beforeEach(() => {
    localStorage.clear();
    get.mockReset();
    get.mockResolvedValue({ data: PROJECTS });
    useProjectStore.setState({
        activeProjectId: null,
        explicitlyCleared: false,
        hydrated: true,
    });
});

describe("useSyncActiveProject", () => {
    it("selects the oldest project on a first visit", async () => {
        // Oldest first means a migrated user lands on Project-1, where all of
        // their existing sessions were moved.
        renderProbe();
        await waitFor(() =>
            expect(screen.getByTestId("active")).toHaveTextContent("p1")
        );
    });

    it("leaves a valid stored project alone", async () => {
        useProjectStore.setState({ activeProjectId: "p2", hydrated: true });
        renderProbe();

        await waitFor(() => expect(get).toHaveBeenCalled());
        expect(screen.getByTestId("active")).toHaveTextContent("p2");
    });

    it("recovers when the stored project no longer exists", async () => {
        // Deleted in another tab, or a stored id from a different account —
        // otherwise the app stays wedged on a project it cannot load.
        useProjectStore.setState({ activeProjectId: "gone", hydrated: true });
        renderProbe();

        await waitFor(() =>
            expect(screen.getByTestId("active")).toHaveTextContent("p1")
        );
    });

    it("clears the selection when there are no projects at all", async () => {
        get.mockResolvedValue({ data: [] });
        useProjectStore.setState({ activeProjectId: "gone", hydrated: true });
        renderProbe();

        await waitFor(() =>
            expect(screen.getByTestId("active")).toHaveTextContent("none")
        );
    });

    it("does not overrule a new project while the list is refetching", async () => {
        // THE CREATE-AND-BOUNCE BUG. Creating a project selects it AND
        // invalidates this list. Until the refetch lands, `projects` is the old
        // list without the new project — and a reconciler that trusts it reads
        // a live choice as a stale one and drops the user back on p1, one
        // render after they made the thing they wanted.
        useProjectStore.setState({ activeProjectId: "p1", hydrated: true });
        const { rerender } = renderProbe();
        await waitFor(() =>
            expect(screen.getByTestId("active")).toHaveTextContent("p1")
        );

        // The create: selected immediately, list not back yet, and the refetch
        // resolves with the new project present.
        let resolveRefetch: (value: unknown) => void = () => {};
        get.mockImplementation(
            () => new Promise((resolve) => (resolveRefetch = resolve))
        );
        useProjectStore.setState({ activeProjectId: "p3" });
        // NOT awaited: invalidateQueries resolves only once the refetch does,
        // and this test is holding that refetch open on purpose.
        void queryClientRef!.invalidateQueries({ queryKey: ["projects"] });
        await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
        rerender(
            <QueryClientProvider client={queryClientRef!}>
                <Probe />
            </QueryClientProvider>
        );

        // Mid-flight: the stale list has no p3, and it must be left alone.
        expect(screen.getByTestId("active")).toHaveTextContent("p3");

        resolveRefetch({
            data: [
                ...PROJECTS,
                { id: "p3", name: "Project-3", created_at: "2026-03-01T00:00:00Z" },
            ],
        });
        await waitFor(() =>
            expect(screen.getByTestId("active")).toHaveTextContent("p3")
        );
    });

    it("leaves a deliberately cleared selection empty", async () => {
        // Deleting the project you were in clears the selection ON PURPOSE.
        // Refilling it with a survivor drops the user into a project they never
        // chose — silently, since the switcher only shows a different name —
        // and everything they do next lands in the wrong project.
        useProjectStore.setState({
            activeProjectId: null,
            explicitlyCleared: true,
            hydrated: true,
        });
        renderProbe();

        await waitFor(() => expect(get).toHaveBeenCalled());
        expect(screen.getByTestId("active")).toHaveTextContent("none");
    });

    it("still repairs a stale id, which nobody chose", async () => {
        // The opposite case, and the reason the flag exists rather than just
        // checking for null: an id from a deleted-elsewhere project or another
        // account resolves to nothing, so leaving it would wedge the app.
        useProjectStore.setState({
            activeProjectId: "gone",
            explicitlyCleared: false,
            hydrated: true,
        });
        renderProbe();

        await waitFor(() =>
            expect(screen.getByTestId("active")).toHaveTextContent("p1")
        );
    });

    it("waits for rehydration before correcting anything", async () => {
        // Correcting too early would flick a reload onto the first project and
        // back once the stored value arrives.
        useProjectStore.setState({ activeProjectId: "p2", hydrated: false });
        renderProbe();

        await waitFor(() => expect(get).toHaveBeenCalled());
        expect(screen.getByTestId("active")).toHaveTextContent("p2");
    });
});

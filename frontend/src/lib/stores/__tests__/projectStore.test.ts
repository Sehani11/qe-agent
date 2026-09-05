/**
 * projectStore — which project the app is working in.
 *
 * It holds only an id on purpose: the project's data is server state owned by
 * React Query, and a second copy here would be the stale one the UI reads.
 */
import { describe, it, expect, beforeEach } from "vitest";

import { useProjectStore } from "@/lib/stores/projectStore";

beforeEach(() => {
    localStorage.clear();
    useProjectStore.setState({
        activeProjectId: null,
        explicitlyCleared: false,
        hydrated: true,
    });
});

describe("projectStore", () => {
    it("starts with no project until one is chosen", () => {
        expect(useProjectStore.getState().activeProjectId).toBeNull();
    });

    it("switches project", () => {
        useProjectStore.getState().setActiveProject("project-a");
        expect(useProjectStore.getState().activeProjectId).toBe("project-a");
    });

    it("persists the choice so a reload stays in the same project", () => {
        useProjectStore.getState().setActiveProject("project-a");

        const stored = localStorage.getItem("qe-agent-active-project");
        expect(stored).toBeTruthy();
        expect(JSON.parse(stored!).state.activeProjectId).toBe("project-a");
    });

    it("does not persist the hydration flag", () => {
        // It describes this session, not the user's choice. Persisting it would
        // read back as already-hydrated before rehydration had actually run.
        useProjectStore.getState().setActiveProject("project-a");

        const stored = JSON.parse(localStorage.getItem("qe-agent-active-project")!);
        expect(stored.state).not.toHaveProperty("hydrated");
    });

    it("can be cleared when the active project is gone", () => {
        useProjectStore.getState().setActiveProject("project-a");
        useProjectStore.getState().setActiveProject(null);
        expect(useProjectStore.getState().activeProjectId).toBeNull();
    });
});

describe("telling a deliberate clear from an absent one", () => {
    // Both hold `activeProjectId: null` and want opposite treatment, so the
    // pair has to move together — see useSyncActiveProject.
    it("writes the flag to storage beside the id it qualifies", () => {
        // A reload after deleting the project you were in must not quietly
        // resolve to a different one, so the flag has to survive the reload.
        useProjectStore.getState().setActiveProject(null);

        const stored = JSON.parse(
            localStorage.getItem("qe-agent-active-project") ?? "{}"
        );
        expect(stored.state.explicitlyCleared).toBe(true);
    });

    it("marks a clear as deliberate", () => {
        useProjectStore.getState().setActiveProject(null);

        expect(useProjectStore.getState().activeProjectId).toBeNull();
        expect(useProjectStore.getState().explicitlyCleared).toBe(true);
    });

    it("ends the deliberate clear as soon as a project is chosen", () => {
        useProjectStore.getState().setActiveProject(null);
        useProjectStore.getState().setActiveProject("p1");

        expect(useProjectStore.getState().explicitlyCleared).toBe(false);
    });
});

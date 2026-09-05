"use client";

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

/**
 * Which project the app is currently working in.
 *
 * Zustand rather than context because this is read by nearly every page and
 * changed from one control: a context provider would re-render the whole tree
 * on every switch, and threading it through props would touch every route.
 *
 * It holds ONLY the id. The project's data is server state and belongs to
 * React Query — duplicating it here would create two copies to keep in step,
 * and the stale one would be the one the UI reads.
 */
interface ProjectState {
    /** Null before the first load, or after the active project is deleted. */
    activeProjectId: string | null;
    setActiveProject: (id: string | null) => void;
    /**
     * Whether the empty selection was ASKED FOR rather than merely absent.
     *
     * Both states hold `activeProjectId: null`, and they need opposite
     * treatment: "never chosen" wants a project picked automatically, while
     * "I just deleted the one I was in" must not silently drop the user into a
     * sibling project's context — the next thing they do would land somewhere
     * they never chose. `useSyncActiveProject` is the reader.
     */
    explicitlyCleared: boolean;
    /** True once the persisted value has been read back. */
    hydrated: boolean;
    setHydrated: () => void;
}

export const useProjectStore = create<ProjectState>()(
    persist(
        (set) => ({
            activeProjectId: null,
            // Clearing IS the deliberate act; choosing a project ends it. The
            // two always move together, so nothing can set one without the
            // other and leave the pair describing a state that never happened.
            setActiveProject: (id) =>
                set({ activeProjectId: id, explicitlyCleared: id === null }),
            explicitlyCleared: false,
            hydrated: false,
            setHydrated: () => set({ hydrated: true }),
        }),
        {
            name: "qe-agent-active-project",
            storage: createJSONStorage(() => localStorage),
            // `hydrated` describes this session, not the user's choice, so it
            // must not be written to storage and read back as already-true.
            partialize: (state) => ({
                activeProjectId: state.activeProjectId,
                // Persisted with the id it qualifies: a reload after deleting
                // the project you were in should not quietly resolve to a
                // different one, which is the behaviour this exists to stop.
                explicitlyCleared: state.explicitlyCleared,
            }),
            onRehydrateStorage: () => (state) => state?.setHydrated(),
        }
    )
);

/**
 * The active project id, or null until one is chosen.
 *
 * Selector-shaped so a component re-renders on a project switch and on nothing
 * else — subscribing to the whole store would re-render on `hydrated` too.
 */
export const useActiveProjectId = () =>
    useProjectStore((state) => state.activeProjectId);

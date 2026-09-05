"use client";

import React, {
    createContext,
    useCallback,
    useContext,
    useMemo,
    useSyncExternalStore,
} from "react";

import { useFineTunedStatus } from "@/lib/hooks/useFineTunedStatus";
import { useProject } from "@/lib/hooks/useProjects";
import { useProjectStore } from "@/lib/stores/projectStore";
import {
    DEFAULT_BDD_PROVIDER,
    DEFAULT_MODEL_ID,
    findModelOption,
    modelIdOf,
    type BddModelProviderId,
    type LLMModelOption,
    type LLMSelection,
} from "@/lib/types/llm";

const BDD_STORAGE_KEY = "qe-agent-bdd-model";

interface ModelContextValue {
    /** The option the picker is showing. Never undefined — falls back to the default. */
    model: LLMModelOption;
    /** Spread straight into an LLM-backed request body. */
    selection: LLMSelection;
    /**
     * Which model generates BDD scenarios — a separate axis from `model`.
     *
     * The EFFECTIVE provider, not the stored preference: `fine_tuned` only
     * survives here while a fine-tuned model is actually being served. See
     * `fineTuned` below.
     */
    bddProvider: BddModelProviderId;
    setBddProvider: (provider: BddModelProviderId) => void;
    /** Spread into the /bdd/generate body: carries BOTH axes. */
    bddSelection: LLMSelection & { bdd_model_provider: BddModelProviderId };
    /** Whether a fine-tuned model is being served, and why not when it is not. */
    fineTuned: { available: boolean; detail: string; isLoading: boolean };
}

const ModelContext = createContext<ModelContextValue | undefined>(undefined);

/* ---------------------------------------------------------------------------
   Same external-store approach as ThemeProvider: the chosen model lives in
   localStorage, so it is read through useSyncExternalStore rather than copied
   into state by a mount effect. Server and hydration snapshots both start at
   the default, and a change in another tab propagates for free.
--------------------------------------------------------------------------- */

const listeners = new Set<() => void>();

function emitModelChange() {
    for (const listener of listeners) listener();
}

function subscribe(onStoreChange: () => void) {
    listeners.add(onStoreChange);
    window.addEventListener("storage", onStoreChange);
    return () => {
        listeners.delete(onStoreChange);
        window.removeEventListener("storage", onStoreChange);
    };
}

/* The model itself is no longer stored in this browser — it comes from the
   active project. What follows is the BDD provider, which is a different axis:
   it selects a SERVING PATH (fine-tuned vs general), has its own toggle on the
   session page, and is not part of project configuration. */

/* The BDD provider is stored and read the same way, under its own key. It is a
   second axis rather than another entry in the model catalog: it selects a
   different SERVING PATH, and the general model still applies underneath it
   whenever the fine-tuned model is not the one answering. */

let inMemoryBddProvider: BddModelProviderId = DEFAULT_BDD_PROVIDER;

function isBddProvider(value: unknown): value is BddModelProviderId {
    return value === "general_llm" || value === "fine_tuned";
}

function readStoredBddProvider(): BddModelProviderId {
    try {
        const stored = localStorage.getItem(BDD_STORAGE_KEY);
        return isBddProvider(stored) ? stored : DEFAULT_BDD_PROVIDER;
    } catch {
        return inMemoryBddProvider;
    }
}

function bddServerSnapshot(): BddModelProviderId {
    return DEFAULT_BDD_PROVIDER;
}

export function ModelProvider({ children }: { children: React.ReactNode }) {
    // The project is the ONLY source. There used to be a navbar picker whose
    // choice lived in localStorage and outranked this, which made sense while
    // both existed. It does not now: with the picker gone there would be no way
    // to clear a stored value, so every browser that ever used it would keep
    // silently overriding the project setting, and the settings page would look
    // broken to exactly the people who had used the app the longest.
    //
    // The stale `qe-agent-model` key is simply never read again. Nothing needs
    // to clear it.
    const activeProjectId = useProjectStore((s) => s.activeProjectId);
    const { data: project } = useProject(activeProjectId);
    const projectModelId = modelIdOf(project?.llm_provider, project?.llm_model);

    const modelId =
        projectModelId && findModelOption(projectModelId)
            ? projectModelId
            : DEFAULT_MODEL_ID;
    const storedBddProvider = useSyncExternalStore(
        subscribe,
        readStoredBddProvider,
        bddServerSnapshot
    );

    // Resolving the preference against reality happens HERE, once, because both
    // consumers have to agree: the toggle that draws the state, and the request
    // body that carries it. Gating only the control left them disagreeing —
    // a switch rendered off while `/bdd/generate` still asked for `fine_tuned`,
    // which the server then tried, failed, and reported as `configured=
    // fine_tuned effective=none`.
    //
    // The STORED value is left alone. A model that comes back should bring the
    // preference back with it rather than making someone re-tick a switch that
    // was never deliberately turned off.
    const { data: fineTunedStatus, isLoading: fineTunedLoading } =
        useFineTunedStatus();
    const fineTunedAvailable = fineTunedStatus?.available ?? false;
    const bddProvider: BddModelProviderId =
        storedBddProvider === "fine_tuned" && !fineTunedAvailable
            ? "general_llm"
            : storedBddProvider;

    // `!` is safe because readStoredModelId only ever returns a catalog id.
    const model = findModelOption(modelId) ?? findModelOption(DEFAULT_MODEL_ID)!;

    const setBddProvider = useCallback((next: BddModelProviderId) => {
        if (!isBddProvider(next)) return;
        inMemoryBddProvider = next;
        try {
            localStorage.setItem(BDD_STORAGE_KEY, next);
        } catch {
            // Not persisting is survivable — the choice still holds this session.
        }
        emitModelChange();
    }, []);

    const value = useMemo<ModelContextValue>(() => {
        const selection: LLMSelection = {
            llm_provider: model.llm_provider,
            llm_model: model.llm_model,
        };
        return {
            model,
            selection,
            bddProvider,
            setBddProvider,
            // Both axes travel together: on the general_llm path the backend
            // needs to know WHICH general model, and on the fine-tuned path it
            // ignores them — so sending both is always correct and the client
            // never has to reason about which one applies.
            bddSelection: { ...selection, bdd_model_provider: bddProvider },
            fineTuned: {
                available: fineTunedAvailable,
                detail: fineTunedStatus?.detail ?? "",
                isLoading: fineTunedLoading,
            },
        };
    }, [
        model,
        bddProvider,
        setBddProvider,
        fineTunedAvailable,
        fineTunedStatus?.detail,
        fineTunedLoading,
    ]);

    return <ModelContext.Provider value={value}>{children}</ModelContext.Provider>;
}

export function useModel() {
    const context = useContext(ModelContext);
    if (!context) {
        throw new Error("useModel must be used within a ModelProvider");
    }
    return context;
}

/**
 * The selection to merge into a request body.
 *
 * Separate from `useModel` so hooks that only need to send the choice do not
 * re-render on every unrelated context change — and so the two fields are
 * always sent as a pair.
 */
export function useModelSelection(): LLMSelection {
    return useModel().selection;
}

/** The request body fields for BDD generation — both axes, always paired. */
export function useBddSelection() {
    return useModel().bddSelection;
}

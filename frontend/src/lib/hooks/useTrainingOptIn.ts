"use client";

import { useCallback, useSyncExternalStore } from "react";
import { useQuery } from "@tanstack/react-query";

import apiClient from "@/lib/api/client";

const STORAGE_KEY = "qe-agent-training-opt-in";

/* ---------------------------------------------------------------------------
   Whether captured scenarios may later be used to fine-tune a model.

   Stored per browser and read through useSyncExternalStore, the same way the
   model choice is, so the server and hydration snapshots agree and a change in
   another tab propagates.

   The value here is a REQUEST, not a decision. The backend ANDs it with the
   deployment's TRAINING_DATA_OPT_IN policy, so this can withdraw consent but
   never grant it — see `_resolve_training_opt_in` in app/api/v1/bdd.py.
--------------------------------------------------------------------------- */

const listeners = new Set<() => void>();

function subscribe(onStoreChange: () => void) {
    listeners.add(onStoreChange);
    window.addEventListener("storage", onStoreChange);
    return () => {
        listeners.delete(onStoreChange);
        window.removeEventListener("storage", onStoreChange);
    };
}

/** Consent is the default, matching the backend's own default policy. */
const DEFAULT = true;

let inMemory = DEFAULT;

function read(): boolean {
    try {
        const stored = localStorage.getItem(STORAGE_KEY);
        // Only an explicit "false" withdraws consent. Anything unreadable or
        // unrecognised falls back to the default rather than guessing.
        if (stored === "false") return false;
        if (stored === "true") return true;
        return DEFAULT;
    } catch {
        return inMemory;
    }
}

function serverSnapshot(): boolean {
    return DEFAULT;
}

/** The deployment's policy — what the UI is allowed to offer. */
export function useClientConfig() {
    return useQuery({
        queryKey: ["client-config"],
        queryFn: async () => {
            const { data } = await apiClient.get<{
                training_data_opt_in_allowed: boolean;
            }>("/config");
            return data;
        },
        // Operator policy, not session data — it does not change under the user.
        staleTime: Infinity,
        retry: false,
    });
}

export function useTrainingOptIn() {
    const requested = useSyncExternalStore(subscribe, read, serverSnapshot);
    const { data: config } = useClientConfig();

    const setRequested = useCallback((next: boolean) => {
        inMemory = next;
        try {
            localStorage.setItem(STORAGE_KEY, String(next));
        } catch {
            // Not persisting is survivable — the choice holds this session.
        }
        for (const listener of listeners) listener();
    }, []);

    // Until the policy loads, assume it is permitted: the control renders in
    // its normal state and the request is authoritative either way. Showing
    // "unavailable" during a fetch would flicker a governance message that may
    // not be true.
    const allowed = config?.training_data_opt_in_allowed ?? true;

    return {
        /** What this browser asks for. */
        requested,
        setRequested,
        /** False when the deployment forbids training outright. */
        allowed,
        /** What will actually be stamped on the row. */
        effective: allowed && requested,
    };
}

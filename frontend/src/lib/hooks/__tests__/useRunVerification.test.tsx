/**
 * useRunVerification — starting a run, and abandoning one.
 *
 * The behaviour worth pinning is what a stopped run leaves behind: nothing.
 * Partial verdicts describe an arbitrary prefix of the scenarios, decided by
 * when the button was pressed, and showing them beside scenarios that were
 * never checked invites reading an unchecked scenario as a passing one.
 */
import React from "react";
import { renderHook, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

const disconnect = vi.fn();
const connect = vi.fn();

vi.mock("@/lib/hooks/useSSEStream", () => ({
    useSSEStream: () => ({ connect, disconnect, isStreaming: true }),
}));

const setVerificationResults = vi.fn();
const setVerificationSummary = vi.fn();
const setVerificationPlan = vi.fn();
const setGlobalError = vi.fn();
const setIsVerifying = vi.fn();

vi.mock("@/context/SessionContext", () => ({
    useSessionContext: () => ({
        setVerificationResults,
        setVerificationSummary,
        setVerificationPlan,
        setGlobalError,
        setIsVerifying,
    }),
}));

const toastInfo = vi.fn();
vi.mock("@/components/ui/toast", () => ({
    useToast: () => ({
        info: toastInfo,
        success: vi.fn(),
        warning: vi.fn(),
        error: vi.fn(),
    }),
}));

vi.mock("@/providers/ModelProvider", () => ({
    useModelSelection: () => ({ llm_provider: "openai", llm_model: "gpt-4o" }),
}));

vi.mock("@/lib/supabase/client", () => ({
    supabase: { auth: { getSession: async () => ({ data: { session: null } }) } },
}));

import { useRunVerification } from "@/lib/hooks/useRunVerification";

beforeEach(() => {
    vi.clearAllMocks();
});

describe("stopping a run", () => {
    it("aborts the stream", () => {
        const { result } = renderHook(() => useRunVerification());

        act(() => result.current.stopVerification());

        expect(disconnect).toHaveBeenCalledTimes(1);
    });

    it("discards every verdict the run produced", () => {
        const { result } = renderHook(() => useRunVerification());

        act(() => result.current.stopVerification());

        expect(setVerificationResults).toHaveBeenCalledWith([]);
        expect(setVerificationSummary).toHaveBeenCalledWith(null);
        // The plan goes too: a stale scenario count would size the next run's
        // progress bar against a run that no longer exists.
        expect(setVerificationPlan).toHaveBeenCalledWith(null);
    });

    it("is not reported as a failure", () => {
        // Stopping is what the user asked for; a global error would put a
        // failure banner on a deliberate act.
        const { result } = renderHook(() => useRunVerification());

        act(() => result.current.stopVerification());

        expect(setGlobalError).not.toHaveBeenCalled();
        expect(toastInfo).toHaveBeenCalled();
    });

    it("leaves nothing for the next run to inherit", async () => {
        // stop-then-verify has to mean a fresh check, not a resumed one.
        const { result } = renderHook(() => useRunVerification());

        act(() => result.current.stopVerification());
        await act(async () => {
            await result.current.runVerification({
                session_id: "s1",
                bdd_content: "Feature: x",
                mode: "full_repo",
                github_input: "https://github.com/org/repo",
                project_id: "p1",
                use_knowledge_base: false,
                code_index_enabled: false,
            });
        });

        expect(setVerificationResults).toHaveBeenNthCalledWith(1, []);
        expect(setVerificationResults).toHaveBeenNthCalledWith(2, []);
        expect(connect).toHaveBeenCalledTimes(1);
    });
});

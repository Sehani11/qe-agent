"use client";

import { useCallback } from "react";
import { useSessionContext } from "@/context/SessionContext";
import { supabase } from "@/lib/supabase/client";
import { useToast } from "@/components/ui/toast";
import { useModelSelection } from "@/providers/ModelProvider";
import { useSSEStream } from "./useSSEStream";
import type {
    AgenticVerificationRequest,
    VerificationPlan,
    VerificationVerdict,
} from "../types/verification";

/**
 * Hook for running agentic LLM verification via POST /api/v1/verification/run-agentic SSE endpoint.
 *
 * The agentic flow requires no pre-fetch step — the backend LLM calls GitHub tools
 * on demand. The payload is AgenticVerificationRequest (session_id, bdd_content, mode, github_input).
 *
 * isVerifying is derived from useSSEStream.isStreaming — the single source of truth
 * for loading state per the Story 2.2 code review mandate.
 */
export function useRunVerification() {
    const {
        setVerificationResults,
        setVerificationSummary,
        setVerificationPlan,
        setGlobalError,
        setIsVerifying,
    } = useSessionContext();

    const toast = useToast();
    const selection = useModelSelection();

    const { connect, disconnect, isStreaming } = useSSEStream({
        onStatusChange: useCallback((streaming: boolean) => {
            setIsVerifying(streaming);
        }, [setIsVerifying]),
        onVerdict: useCallback(
            (verdict: VerificationVerdict) => {
                setVerificationResults((prev) => [...prev, verdict]);
            },
            [setVerificationResults],
        ),
        onVerificationPlan: useCallback(
            (plan: VerificationPlan) => {
                setVerificationPlan(plan);
                // Said once, quietly: a scenario silently missing from the
                // report would otherwise look like the run lost it.
                if (plan.duplicates_skipped > 0) {
                    toast.info(
                        `Skipped ${plan.duplicates_skipped} duplicate scenario${
                            plan.duplicates_skipped === 1 ? "" : "s"
                        }`,
                        { description: `Verifying ${plan.total} unique scenarios.` },
                    );
                }
            },
            [setVerificationPlan, toast],
        ),
        onVerificationComplete: useCallback(
            (
                total: number,
                passed: number,
                failed: number,
                inconclusive: number,
                partial: number,
            ) => {
                setVerificationSummary({ total, passed, failed, inconclusive, partial });
                // A run that finished with gaps is not an error — it is the
                // answer the user asked for — so it reads as a warning, not a
                // failure, and names the number they need to act on.
                //
                // Undecided scenarios are counted separately and never folded
                // into "not implemented": the two ask opposite things of the
                // reader — write code, or verify again — and announcing "all
                // passed" while some were never established would be false.
                // Partial scenarios are reported separately for the same
                // reason: they ask the reader to wire up code that already
                // exists, not to write it. Folding them into "not implemented"
                // is what sends someone to rebuild a working feature.
                const detail = [
                    `${passed} passed`,
                    partial ? `${partial} partial` : "",
                    inconclusive ? `${inconclusive} inconclusive` : "",
                ]
                    .filter(Boolean)
                    .join(", ");

                if (failed > 0) {
                    toast.warning(`${failed} of ${total} not implemented`, {
                        description: `${detail}. Open a scenario for the detail.`,
                    });
                } else if (partial > 0) {
                    toast.warning(`${partial} of ${total} partially implemented`, {
                        description: `${detail}, none absent. Open a partial scenario to see what is missing.`,
                    });
                } else if (inconclusive > 0) {
                    toast.warning(`${inconclusive} of ${total} inconclusive`, {
                        description: `${passed} passed, none failed. Open an inconclusive scenario to see what evidence was missing.`,
                    });
                } else {
                    toast.success(`All ${total} scenarios passed`, {
                        description: "Every scenario was matched to code in the repository.",
                    });
                }
            },
            [setVerificationSummary, toast],
        ),
        onError: useCallback(
            (message: string) => {
                // Do NOT clear bddContent, verificationMode, githubInput, sessionId on error
                setGlobalError(message);
                toast.error("Verification stopped", { description: message });
            },
            [setGlobalError, toast],
        ),
        onWarning: useCallback(
            (message: string) => {
                // A toast rather than the global error banner: the run is
                // still producing verdicts, and the banner reads as "this
                // failed". Worth saying out loud all the same — silently
                // ignoring the code index is how someone concludes the
                // feature does nothing.
                toast.warning("Verification is running degraded", {
                    description: message,
                });
            },
            [toast],
        ),
    });

    const runVerification = useCallback(
        async (payload: AgenticVerificationRequest) => {
            // Clear stale results from any previous run before starting a new stream
            setVerificationResults([]);
            setVerificationSummary(null);
            setVerificationPlan(null);

            // Raw fetch needs the full backend URL (not Next.js origin) and the
            // Bearer token — apiClient handles both automatically, but useSSEStream
            // uses fetch() directly.
            const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
            const { data: { session } } = await supabase.auth.getSession();
            const authHeaders: Record<string, string> = session?.access_token
                ? { Authorization: `Bearer ${session.access_token}` }
                : {};

            connect(
                `${apiBaseUrl}/api/v1/verification/run-agentic`,
                "POST",
                { ...payload, ...selection } as unknown as Record<string, unknown>,
                authHeaders,
            );
        },
        [
            connect,
            selection,
            setVerificationResults,
            setVerificationSummary,
            setVerificationPlan,
        ],
    );

    /**
     * Abandon a run in progress and discard what it produced.
     *
     * A stopped run leaves nothing behind. Partial verdicts are not a partial
     * answer — they describe an arbitrary prefix of the scenarios, decided by
     * when the user happened to press the button, and reading them beside the
     * scenarios that were never checked invites treating an unchecked scenario
     * as a passing one. Verification is the whole set or none of it.
     *
     * This also makes stop-then-verify mean what it looks like. `runVerification`
     * clears results anyway, so keeping them would only leave a stale set on
     * screen in the gap between the two — long enough to read, and wrong.
     *
     * Not an error, so it does not go through `onError`: stopping is what the
     * user asked for, and `setGlobalError` would put a failure banner on a
     * deliberate act.
     */
    const stopVerification = useCallback(() => {
        disconnect();
        setVerificationResults([]);
        setVerificationSummary(null);
        setVerificationPlan(null);
        toast.info("Verification stopped", {
            description: "Nothing was kept. Running it again starts a fresh check.",
        });
    }, [
        disconnect,
        setVerificationResults,
        setVerificationSummary,
        setVerificationPlan,
        toast,
    ]);

    return { runVerification, stopVerification, isVerifying: isStreaming };
}

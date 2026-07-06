"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { useSessionContext } from "@/context/SessionContext";
import { useBDDGenerate, scenariosToGherkin } from "@/lib/hooks/useBDDGenerate";
import { useSession, useSessionBDD, useSessionVerificationResults } from "@/lib/hooks/useSession";
import { useRunVerification } from "@/lib/hooks/useRunVerification";
import apiClient from "@/lib/api/client";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import TerminalProgressLog from "@/components/pipeline/TerminalProgressLog";
import BDDEditorPanel from "@/components/pipeline/BDDEditorPanel";
import GitHubSourceSelector from "@/components/pipeline/GitHubSourceSelector";
import VerificationResultsPanel from "@/components/pipeline/VerificationResultsPanel";
import type { CodeReference, VerificationVerdict } from "@/lib/types/verification";

interface IngestResponse {
    session_id: string;
    jira_ticket_id: string;
    acceptance_criteria: string;
    status: string;
}

export default function SessionPipelinePage() {
    const router = useRouter();
    const params = useParams();
    const routeSessionId = params.sessionId as string;
    const {
        sessionId,
        setSessionId,
        jiraTicketId,
        setJiraTicketId,
        acceptanceCriteria,
        setAcceptanceCriteria,
        bddContent,
        setBddContent,
        isIngesting,
        setIsIngesting,
        isGeneratingBDD,
        setIsGeneratingBDD,
        setIsVerifying,
        globalError,
        setGlobalError,
        // FIX M2: needed to reset stale verification state on new ticket ingestion
        setVerificationMode,
        setGithubInput,
        verificationMode,
        githubInput,
        verificationResults,
        verificationSummary,
        setVerificationResults,
        setVerificationSummary,
        setBddScenarios,
        setLogs,
        setFetchedFiles,
    } = useSessionContext();

    const { runVerification, isVerifying } = useRunVerification();
    const queryClient = useQueryClient();

    // Load existing session BDD and verification results (Story 3.5)
    const { data: existingSession } = useSession(routeSessionId);
    // Skip the BDD fetch when the session metadata says no BDD exists (avoids
    // a noisy 404 on freshly-ingested sessions and on sessions that never
    // generated BDD).
    const { data: existingBDD, isLoading: isBDDLoading } = useSessionBDD(
        existingSession?.bdd_status && existingSession.bdd_status !== "none"
            ? routeSessionId
            : null
    );
    const { data: existingVerificationResults } = useSessionVerificationResults(routeSessionId);

    // SessionProvider lives in the root layout, so state from a previously
    // viewed session survives navigating Home → New Session. Detect that
    // mismatch and (a) render a placeholder during the stale render so the
    // old session's data never paints, and (b) reset the context in an
    // effect for the next render. Skipped when sessionId is null (nothing
    // stale) or already matches the route — the latter covers ingestion,
    // where setSessionId + router.replace land in the same React-18 batch
    // so context is already in sync by the time the route changes.
    // Set during ingestion to the freshly-created (real) session id. New
    // sessions enter on a throwaway client-generated UUID route, so after
    // ingestion `setSessionId(realId)` lands one render before `router.replace`
    // updates routeSessionId. During that gap sessionId !== routeSessionId even
    // though context is correct (ahead of the route, not stale). Without this
    // guard the reset effect below would fire and wipe acceptanceCriteria,
    // leaving "Generate BDD" disabled. Cleared once the route catches up.
    const ingestedSessionIdRef = useRef<string | null>(null);

    const isContextStale =
        sessionId !== null &&
        sessionId !== routeSessionId &&
        sessionId !== ingestedSessionIdRef.current;

    const bddPopulatedForRef = useRef<string | null>(null);
    const verificationPopulatedForRef = useRef<string | null>(null);
    const previousSessionIdRef = useRef<string | null>(sessionId);

    // Clear the ingestion marker once the route has caught up to the
    // freshly-ingested session, so a later genuine cross-session navigation is
    // still detected as stale and resets correctly.
    useEffect(() => {
        if (routeSessionId === ingestedSessionIdRef.current) {
            ingestedSessionIdRef.current = null;
        }
    }, [routeSessionId]);
    useEffect(() => {
        if (!isContextStale) {
            previousSessionIdRef.current = sessionId;
            return;
        }
        // Drop React Query cache for the session we're leaving so no stale
        // BDD / verification data can resurface for the next route.
        const previousSessionId = previousSessionIdRef.current;
        if (previousSessionId) {
            queryClient.removeQueries({ queryKey: ["sessions", previousSessionId] });
            queryClient.removeQueries({ queryKey: ["sessions", previousSessionId, "bdd"] });
            queryClient.removeQueries({
                queryKey: ["sessions", previousSessionId, "verification-results"],
            });
        }
        previousSessionIdRef.current = null;
        bddPopulatedForRef.current = null;
        verificationPopulatedForRef.current = null;
        setSessionId(null);
        setJiraTicketId(null);
        setAcceptanceCriteria(null);
        setBddContent("");
        setBddScenarios([]);
        setVerificationResults([]);
        setVerificationSummary(null);
        setVerificationMode(null);
        setGithubInput("");
        setGlobalError(null);
        setLogs([]);
        setFetchedFiles([]);
        setIsIngesting(false);
        setIsGeneratingBDD(false);
        setIsVerifying(false);
    }, [
        isContextStale,
        sessionId,
        queryClient,
        setSessionId,
        setJiraTicketId,
        setAcceptanceCriteria,
        setBddContent,
        setBddScenarios,
        setVerificationResults,
        setVerificationSummary,
        setVerificationMode,
        setGithubInput,
        setGlobalError,
        setLogs,
        setFetchedFiles,
        setIsIngesting,
        setIsGeneratingBDD,
        setIsVerifying,
    ]);

    // Populate BDD editor from saved session data (only once per session visit).
    // Uses a ref so re-visiting a previously-viewed session correctly repopulates
    // (sessionId in context can lag behind routeSessionId across nav cycles).
    useEffect(() => {
        if (!existingBDD || bddPopulatedForRef.current === routeSessionId) return;
        bddPopulatedForRef.current = routeSessionId;
        setSessionId(routeSessionId);
        if (existingBDD.source === "generated") {
            try {
                const parsed = JSON.parse(existingBDD.content) as {
                    scenarios: Array<{
                        source_ac_clause: string;
                        feature: string;
                        scenario: string;
                        given: string;
                        when: string;
                        then: string;
                    }>;
                };
                setBddScenarios(parsed.scenarios);
                setBddContent(scenariosToGherkin(parsed.scenarios));
            } catch {
                // Malformed JSON — leave BDD empty; user can re-generate
            }
        } else {
            setBddContent(existingBDD.content);
        }
    }, [existingBDD, routeSessionId, setSessionId, setBddContent, setBddScenarios]);

    // Restore jiraTicketId from saved session metadata (only if not already set from ingestion)
    useEffect(() => {
        if (!existingSession || jiraTicketId) return;
        setJiraTicketId(existingSession.jira_ticket_id);
    }, [existingSession, jiraTicketId, setJiraTicketId]);

    // Populate verification results from saved session data (only once per session visit).
    useEffect(() => {
        if (!existingVerificationResults?.length || verificationPopulatedForRef.current === routeSessionId) return;
        verificationPopulatedForRef.current = routeSessionId;
        const mapped: VerificationVerdict[] = existingVerificationResults.map((r) => ({
            scenario_id: r.scenario_id,
            scenario_title: r.scenario_title,
            status: r.status,
            justification: r.justification,
            code_reference: r.code_reference as unknown as CodeReference,
            github_links: r.github_links as string[],
            implementation_suggestion: r.implementation_suggestion,
        }));
        setVerificationResults(mapped);
        const passed = existingVerificationResults.filter((r) => r.status === "pass").length;
        const failed = existingVerificationResults.filter((r) => r.status === "fail").length;
        setVerificationSummary({ total: existingVerificationResults.length, passed, failed });
    }, [existingVerificationResults, routeSessionId, setVerificationResults, setVerificationSummary]);

    const [ticketInput, setTicketInput] = useState("");
    const [ticketInputError, setTicketInputError] = useState<string | null>(null);
    const generateBDD = useBDDGenerate();

    const handleIngestTrigger = async () => {
        const rawVal = ticketInput.trim();
        if (!rawVal) {
            setTicketInputError("Ticket ID cannot be empty.");
            return;
        }
        setTicketInputError(null);

        setGlobalError(null);
        setIsIngesting(true);
        setJiraTicketId(rawVal);
        setAcceptanceCriteria(null);
        setBddContent("");
        // FIX M2: Reset GitHub verification state so stale mode/input don't resurface
        // when BDD is generated for the new ticket.
        setVerificationMode(null);
        setGithubInput("");
        setVerificationResults([]);
        setVerificationSummary(null);

        try {
            const response = await apiClient.post<IngestResponse>("/ingestion/ingest", {
                ticket_id_or_url: rawVal,
            });

            // Mark the new session id as "ours" before updating context so the
            // stale-reset effect doesn't misfire during the render gap between
            // setSessionId and router.replace taking effect (see ref above).
            ingestedSessionIdRef.current = response.data.session_id;
            setSessionId(response.data.session_id);
            setJiraTicketId(response.data.jira_ticket_id);
            setAcceptanceCriteria(response.data.acceptance_criteria ?? null);
            setBddContent("");
            router.replace(`/session/${response.data.session_id}`);
        } catch (error: unknown) {
            let message = "Ticket ingestion failed.";

            if (typeof error === "object" && error !== null && "response" in error) {
                const axiosError = error as {
                    response?: {
                        data?: {
                            message?: string;
                        };
                    };
                };
                message = axiosError.response?.data?.message ?? message;
            } else if (error instanceof Error) {
                message = error.message;
            }

            setGlobalError(message);
        } finally {
            setIsIngesting(false);
        }
    };

    const handleGenerateBDD = () => {
        const activeSessionId = sessionId || routeSessionId;

        if (!activeSessionId || !acceptanceCriteria) {
            setGlobalError("Ingest a Jira ticket first to retrieve acceptance criteria.");
            return;
        }

        setGlobalError(null);
        setIsGeneratingBDD(true);

        generateBDD.mutate(
            {
                session_id: activeSessionId,
                acceptance_criteria: acceptanceCriteria,
            },
            {
                onSuccess: (data) => {
                    setBddScenarios(data.scenarios);
                    setBddContent(scenariosToGherkin(data.scenarios));
                    setIsGeneratingBDD(false);
                },
                onError: (error) => {
                    const message = error instanceof Error ? error.message : "BDD generation failed.";
                    setGlobalError(message);
                    setIsGeneratingBDD(false);
                },
            }
        );
    };

    const handleRunVerification = () => {
        const activeSessionId = sessionId || routeSessionId;

        if (!activeSessionId || !bddContent || !verificationMode || !githubInput.trim()) {
            return;
        }

        setGlobalError(null);

        runVerification({
            session_id: activeSessionId,
            bdd_content: bddContent,
            mode: verificationMode,
            github_input: githubInput,
        });
    };

    const displayedSessionId = sessionId || routeSessionId;
    const canGenerateBDD = Boolean(acceptanceCriteria) && !isIngesting && !isGeneratingBDD;

    // Don't paint the previous session's data while the reset effect catches
    // up — render a minimal placeholder until context state matches the route.
    if (isContextStale) {
        return (
            <div className="flex min-h-screen items-center justify-center bg-[linear-gradient(180deg,#eff6ff_0%,#f8fbff_26%,#f8fafc_100%)] text-slate-900">
                <div className="flex items-center gap-3 text-sm text-slate-500">
                    <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-sky-500" />
                    Loading session…
                </div>
            </div>
        );
    }

    return (
        <div className="flex min-h-screen flex-col bg-[linear-gradient(180deg,#eff6ff_0%,#f8fbff_26%,#f8fafc_100%)] text-slate-900">
            {/* Top bar */}
            <header className="sticky top-0 z-20 border-b border-sky-100 bg-white/90 px-4 py-3 backdrop-blur lg:px-6">
                <div className="mx-auto flex max-w-7xl items-center justify-between gap-4">
                    <div>
                        <p className="text-xs font-semibold uppercase tracking-[0.25em] text-sky-700">BDD Workspace</p>
                        <h1 className="text-base font-bold tracking-tight text-slate-900 lg:text-lg">Jira to BDD demo</h1>
                    </div>
                    <div className="hidden rounded-xl bg-sky-50 px-3 py-1.5 text-xs text-slate-600 sm:block">
                        Session: <span className="font-mono font-semibold text-slate-900">{displayedSessionId || "NEW"}</span>
                    </div>
                </div>
            </header>

            {/* Breadcrumb nav */}
            <div className="border-b border-sky-50 bg-white/60 px-4 py-2 lg:px-6">
                <div className="mx-auto max-w-7xl">
                    <Link
                        href="/"
                        className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-500 transition hover:text-sky-600"
                    >
                        <ArrowLeft className="h-3.5 w-3.5" />
                        Back to Home
                    </Link>
                </div>
            </div>

            {/* Ticket input bar */}
            <div className="border-b border-sky-100 bg-white/80 px-4 py-3 backdrop-blur lg:px-6">
                <div className="mx-auto max-w-7xl">
                    <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center">
                        <input
                            type="text"
                            placeholder="Enter Jira Ticket ID or URL..."
                            value={ticketInput}
                            onChange={(e) => setTicketInput(e.target.value)}
                            className="min-w-0 flex-1 rounded-xl border border-sky-200 bg-sky-50 px-4 py-2.5 text-sm text-slate-900 shadow-sm outline-none transition focus:border-sky-500 focus:bg-white"
                        />
                        <div className="flex gap-2">
                            <button
                                onClick={handleIngestTrigger}
                                className="flex-1 whitespace-nowrap rounded-xl bg-gradient-to-r from-blue-600 to-sky-500 px-4 py-2.5 text-sm font-semibold text-white shadow transition hover:from-blue-500 hover:to-sky-400 sm:flex-none sm:px-5"
                            >
                                {isIngesting ? "Fetching..." : "Fetch Ticket"}
                            </button>
                            <button
                                onClick={handleGenerateBDD}
                                disabled={!canGenerateBDD}
                                className="flex-1 whitespace-nowrap rounded-xl bg-gradient-to-r from-blue-700 to-indigo-600 px-4 py-2.5 text-sm font-semibold text-white shadow transition hover:from-blue-600 hover:to-indigo-500 disabled:cursor-not-allowed disabled:opacity-40 sm:flex-none sm:px-5"
                            >
                                {isGeneratingBDD ? "Generating..." : "Generate BDD"}
                            </button>
                        </div>
                        {jiraTicketId && (
                            <span className="rounded-xl bg-blue-50 px-3 py-2 text-xs text-blue-900">
                                Ticket: <span className="font-semibold">{jiraTicketId}</span>
                            </span>
                        )}
                    </div>
                    {(ticketInputError || globalError) && (
                        <p className="mt-2 rounded-xl bg-rose-50 px-4 py-2 text-sm text-rose-700">
                            {ticketInputError || globalError}
                        </p>
                    )}
                    {isBDDLoading && sessionId !== routeSessionId && (
                        <div className="mt-2 flex items-center gap-2 text-sm text-slate-500">
                            <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-slate-300 border-t-sky-500" />
                            Loading saved BDD content…
                        </div>
                    )}
                </div>
            </div>

            {/* Main content — keyed on routeSessionId so all panels (Monaco
                editor especially) fully remount when the session changes and
                no internal component state can survive across sessions. */}
            <div key={routeSessionId} className="flex-1 px-4 py-4 lg:px-6">
                <div className="mx-auto flex max-w-7xl flex-col gap-4">
                    {/* Editor + Status panel */}
                    <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
                        {/* Status sidebar — auto height on mobile, full height on desktop */}
                        <div className="shrink-0 lg:w-80 xl:w-96">
                            <TerminalProgressLog />
                        </div>
                        {/* BDD Editor */}
                        <div className="lg:flex-1">
                            <BDDEditorPanel />
                        </div>
                    </div>

                    {/* GitHub Source Verification */}
                    {bddContent && (
                        <GitHubSourceSelector
                            onVerify={handleRunVerification}
                            isVerifying={isVerifying}
                        />
                    )}

                    {/* Verification Results */}
                    {(verificationResults.length > 0 || verificationSummary !== null || isVerifying) && (
                        <VerificationResultsPanel />
                    )}
                </div>
            </div>
        </div>
    );
}

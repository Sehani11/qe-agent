"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Plus } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useSessionContext } from "@/context/SessionContext";
import { useBDDGenerate, scenariosToGherkin } from "@/lib/hooks/useBDDGenerate";
import { useSession, useSessionBDD, useSessionVerificationResults } from "@/lib/hooks/useSession";
import { useRunVerification } from "@/lib/hooks/useRunVerification";
import apiClient from "@/lib/api/client";
import AppNav from "@/components/layout/AppNav";
import FineTunedToggle from "@/components/model/FineTunedToggle";
import { useActiveProjectId } from "@/lib/stores/projectStore";
// TODO(training-opt-in): consent toggle temporarily hidden — see TODO.md.
// import TrainingOptInToggle from "@/components/model/TrainingOptInToggle";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Input } from "@/components/ui/field";
import { InlineLoader } from "@/components/ui/loaders";
import { useToast } from "@/components/ui/toast";
import TerminalProgressLog from "@/components/pipeline/TerminalProgressLog";
import BDDEditorPanel from "@/components/pipeline/BDDEditorPanel";
import GitHubSourceSelector from "@/components/pipeline/GitHubSourceSelector";
import VerificationResultsPanel from "@/components/pipeline/VerificationResultsPanel";
import ChatPanel from "@/components/pipeline/ChatPanel";
import {
    DEFAULT_VERIFICATION_MODE,
    type CodeReference,
    type RagContextItem,
    type VerificationVerdict,
} from "@/lib/types/verification";

interface IngestResponse {
    session_id: string;
    jira_ticket_id: string;
    jira_ticket_url: string;
    acceptance_criteria: string;
    status: string;
}

export default function SessionPipelinePage() {
    const router = useRouter();
    const params = useParams();
    const routeSessionId = params.sessionId as string;
    const toast = useToast();
    const {
        sessionId,
        setSessionId,
        jiraTicketId,
        setJiraTicketId,
        jiraTicketUrl,
        setJiraTicketUrl,
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
        useKnowledgeBase,
        codeIndexEnabled,
        verificationResults,
        verificationSummary,
        setVerificationResults,
        setVerificationSummary,
        setBddScenarios,
        setLogs,
        resetSession,
    } = useSessionContext();

    const { runVerification, stopVerification, isVerifying } = useRunVerification();
    // Scopes every action on this page: which Jira the ticket comes from,
    // and which GitHub token reads the repository.
    const activeProjectId = useActiveProjectId();
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

    // Seeded from context so the ticket the user typed survives the route
    // change from the throwaway session id to the real one after ingestion.
    const [ticketInput, setTicketInput] = useState(jiraTicketUrl ?? "");
    const [ticketInputError, setTicketInputError] = useState<string | null>(null);

    // Both the ingest and the generate paths overwrite the editor, so they share
    // one slot — only one confirmation can ever be open at a time.
    const [pendingAction, setPendingAction] = useState<"ingest" | "generate" | null>(null);

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
        setJiraTicketUrl(null);
        setTicketInput("");
        setTicketInputError(null);
        setAcceptanceCriteria(null);
        setBddContent("");
        setBddScenarios([]);
        setVerificationResults([]);
        setVerificationSummary(null);
        setVerificationMode(DEFAULT_VERIFICATION_MODE);
        setGithubInput("");
        setGlobalError(null);
        setLogs([]);
        setIsIngesting(false);
        setIsGeneratingBDD(false);
        setIsVerifying(false);
    }, [
        isContextStale,
        sessionId,
        queryClient,
        setSessionId,
        setJiraTicketId,
        setJiraTicketUrl,
        setTicketInput,
        setTicketInputError,
        setAcceptanceCriteria,
        setBddContent,
        setBddScenarios,
        setVerificationResults,
        setVerificationSummary,
        setVerificationMode,
        setGithubInput,
        setGlobalError,
        setLogs,
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

    // Put the submitted ticket reference back in the field when a saved session
    // is opened. Falls back to the extracted key for sessions ingested before
    // the URL was persisted, and for those created by uploading a .feature file.
    // Only fills an untouched field, so it can never overwrite what someone is
    // mid-way through typing.
    useEffect(() => {
        if (!existingSession) return;
        const submitted = existingSession.jira_ticket_url ?? existingSession.jira_ticket_id;
        setTicketInput((current) => (current.trim() === "" ? submitted : current));
        setJiraTicketUrl(submitted);
    }, [existingSession, setJiraTicketUrl]);

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
            rag_context: (r.rag_context ?? null) as RagContextItem[] | null,
        }));
        setVerificationResults(mapped);
        const passed = existingVerificationResults.filter((r) => r.status === "pass").length;
        const failed = existingVerificationResults.filter((r) => r.status === "fail").length;
        // Counted explicitly rather than as "everything else", so a status this
        // build does not know about cannot quietly inflate the undecided count.
        const inconclusive = existingVerificationResults.filter(
            (r) => r.status === "inconclusive",
        ).length;
        const partial = existingVerificationResults.filter(
            (r) => r.status === "partial",
        ).length;
        setVerificationSummary({
            total: existingVerificationResults.length,
            passed,
            failed,
            inconclusive,
            partial,
        });

        // Restore the source the LAST run used, so re-running a past session
        // does not mean retyping the URL from memory. Results come back oldest
        // first, so the newest row that actually recorded a source wins — older
        // rows predate the columns and carry null.
        const latestWithSource = [...existingVerificationResults]
            .reverse()
            .find((r) => r.github_input);
        if (latestWithSource?.github_input) {
            if (latestWithSource.verification_mode) {
                setVerificationMode(latestWithSource.verification_mode);
            }
            setGithubInput(latestWithSource.github_input);
        }
    }, [
        existingVerificationResults,
        routeSessionId,
        setVerificationResults,
        setVerificationSummary,
        setVerificationMode,
        setGithubInput,
    ]);

    const generateBDD = useBDDGenerate();

    const runIngest = async () => {
        const rawVal = ticketInput.trim();
        if (!rawVal) return;

        setPendingAction(null);
        setGlobalError(null);
        setIsIngesting(true);
        setJiraTicketId(rawVal);
        setJiraTicketUrl(rawVal);
        setAcceptanceCriteria(null);
        setBddContent("");
        // FIX M2: Reset GitHub verification state so stale mode/input don't resurface
        // when BDD is generated for the new ticket.
        setVerificationMode(DEFAULT_VERIFICATION_MODE);
        setGithubInput("");
        setVerificationResults([]);
        setVerificationSummary(null);

        try {
            const response = await apiClient.post<IngestResponse>("/ingestion/ingest", {
                project_id: activeProjectId,
                ticket_id_or_url: rawVal,
            });

            // Mark the new session id as "ours" before updating context so the
            // stale-reset effect doesn't misfire during the render gap between
            // setSessionId and router.replace taking effect (see ref above).
            ingestedSessionIdRef.current = response.data.session_id;
            setSessionId(response.data.session_id);
            setJiraTicketId(response.data.jira_ticket_id);
            // The server echoes back what it stored, so the field keeps showing
            // the URL the user submitted rather than being cleared on success.
            setJiraTicketUrl(response.data.jira_ticket_url);
            setTicketInput(response.data.jira_ticket_url);
            setAcceptanceCriteria(response.data.acceptance_criteria ?? null);
            setBddContent("");
            toast.success("Ticket fetched", {
                description: `${response.data.jira_ticket_id} is ready. Generate BDD to turn its criteria into scenarios.`,
            });
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
            toast.error("Could not fetch that ticket", { description: message });
        } finally {
            setIsIngesting(false);
        }
    };

    const runGenerateBDD = () => {
        const activeSessionId = sessionId || routeSessionId;

        setPendingAction(null);

        if (!activeSessionId || !acceptanceCriteria) {
            const message = "Ingest a Jira ticket first to retrieve acceptance criteria.";
            setGlobalError(message);
            toast.warning("No acceptance criteria yet", { description: message });
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
                    toast.success("BDD generated", {
                        description: `${data.scenarios.length} ${
                            data.scenarios.length === 1 ? "scenario" : "scenarios"
                        } written. Edit them, then verify against a repo.`,
                    });
                },
                onError: (error) => {
                    const message = error instanceof Error ? error.message : "BDD generation failed.";
                    setGlobalError(message);
                    setIsGeneratingBDD(false);
                    toast.error("BDD generation failed", { description: message });
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
            project_id: activeProjectId,
            bdd_content: bddContent,
            mode: verificationMode,
            github_input: githubInput,
            use_knowledge_base: useKnowledgeBase,
            // TODO(code-index): "Use code index" toggle is hidden in the UI for
            // now (see TODO.md), so this always sends false until it's back.
            code_index_enabled: codeIndexEnabled,
        });
    };

    // Work that a re-run would destroy. Editor content and verdicts both live
    // only in context until they are saved, so overwriting them is not undoable.
    const hasWorkInProgress =
        bddContent.trim().length > 0 || verificationResults.length > 0;

    // Fetching a ticket resets BDD, verification results and the GitHub target
    // (see runIngest), so it is only unprompted while there is nothing to lose.
    const handleIngestTrigger = () => {
        if (!ticketInput.trim()) {
            setTicketInputError("Enter a ticket ID or URL.");
            return;
        }
        setTicketInputError(null);

        if (hasWorkInProgress) {
            setPendingAction("ingest");
            return;
        }
        void runIngest();
    };

    // Generating replaces the whole document, including hand-written edits.
    const handleGenerateBDD = () => {
        if (bddContent.trim().length > 0) {
            setPendingAction("generate");
            return;
        }
        runGenerateBDD();
    };

    // Same entry point as Home and Sessions: clear context and cached session
    // data, then land on a throwaway session id that ingestion replaces with
    // the real one.
    // Local state is cleared alongside the context because this navigation
    // stays on the same route, so the component is not guaranteed to remount
    // and the stale-reset effect above does not fire (resetSession already
    // nulls sessionId).
    const handleStartNewSession = () => {
        resetSession();
        setTicketInput("");
        setTicketInputError(null);
        setPendingAction(null);
        bddPopulatedForRef.current = null;
        verificationPopulatedForRef.current = null;
        queryClient.removeQueries({ queryKey: ["sessions"] });
        router.push(`/session/${crypto.randomUUID()}`);
    };

    const displayedSessionId = sessionId || routeSessionId;
    const canGenerateBDD = Boolean(acceptanceCriteria) && !isIngesting && !isGeneratingBDD;

    // Don't paint the previous session's data while the reset effect catches
    // up — render a minimal placeholder until context state matches the route.
    if (isContextStale) {
        return (
            <div className="flex min-h-screen items-center justify-center">
                <InlineLoader>Loading session</InlineLoader>
            </div>
        );
    }

    return (
        <div className="flex min-h-screen flex-col">
            <AppNav
                actions={
                    <div className="flex items-center gap-2">
                        {displayedSessionId && (
                            <span className="hidden items-center gap-2 rounded-md border border-rule bg-surface px-2.5 py-1 font-mono text-xs text-muted-foreground lg:inline-flex">
                                <span className="text-muted-foreground/70">session</span>
                                <span className="text-foreground">
                                    {displayedSessionId.slice(0, 8)}
                                </span>
                            </span>
                        )}
                        <Button size="sm" onClick={handleStartNewSession}>
                            <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                            New session
                        </Button>
                    </div>
                }
            />

            {/* Ingest bar — the entry point to everything below it, so it sits
                directly under the nav and stays put while you scroll. */}
            <div className="sticky top-14 z-20 border-b border-rule bg-background/85 backdrop-blur-md">
                <div className="app-shell py-3">
                    <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                        <div className="min-w-0 flex-1">
                            <label htmlFor="ticket-input" className="sr-only">
                                Jira ticket ID or URL
                            </label>
                            <Input
                                id="ticket-input"
                                mono
                                placeholder="PROJ-1234 or a Jira URL"
                                value={ticketInput}
                                onChange={(e) => setTicketInput(e.target.value)}
                                onKeyDown={(e) => {
                                    if (e.key === "Enter" && !isIngesting) handleIngestTrigger();
                                }}
                                aria-invalid={ticketInputError ? true : undefined}
                                aria-describedby={ticketInputError ? "ticket-input-error" : undefined}
                                disabled={isIngesting}
                            />
                        </div>

                        <div className="flex gap-2">
                            <Button
                                onClick={handleIngestTrigger}
                                loading={isIngesting}
                                variant="outline"
                                className="flex-1 sm:flex-none"
                            >
                                {isIngesting ? "Fetching" : "Fetch ticket"}
                            </Button>
                            <Button
                                onClick={handleGenerateBDD}
                                disabled={!canGenerateBDD}
                                loading={isGeneratingBDD}
                                className="flex-1 sm:flex-none"
                            >
                                {isGeneratingBDD ? "Generating" : "Generate BDD"}
                            </Button>
                        </div>

                        {jiraTicketId && (
                            <span className="inline-flex items-center gap-2 rounded-md border border-rule bg-surface px-2.5 py-1.5 font-mono text-xs">
                                <span className="text-muted-foreground">ticket</span>
                                <span className="font-semibold text-foreground">{jiraTicketId}</span>
                            </span>
                        )}
                    </div>

                    {/* Which model generates. The consent toggle that sat beside
                        it — whether what this produces may train a future model —
                        is hidden for now; see TODO(training-opt-in) in TODO.md.
                        Restore <TrainingOptInToggle /> inside this row. */}
                    <div className="mt-2.5 flex flex-wrap items-start gap-x-6 gap-y-2">
                        <FineTunedToggle />
                    </div>

                    {ticketInputError && (
                        <p id="ticket-input-error" className="mt-2 text-xs text-fail-ink" role="alert">
                            {ticketInputError}
                        </p>
                    )}

                    {isBDDLoading && sessionId !== routeSessionId && (
                        <InlineLoader className="mt-2">Loading saved scenarios</InlineLoader>
                    )}
                </div>
            </div>

            {/* Main content — keyed on routeSessionId so all panels (Monaco
                editor especially) fully remount when the session changes and
                no internal component state can survive across sessions. */}
            <main id="main" key={routeSessionId} className="app-shell flex-1 py-5">
                <div className="flex flex-col gap-5">
                    <div className="flex flex-col gap-5 lg:flex-row lg:items-start">
                        <div className="shrink-0 lg:w-72 xl:w-80">
                            <TerminalProgressLog />
                        </div>
                        <div className="min-w-0 lg:flex-1">
                            <BDDEditorPanel />
                        </div>
                    </div>

                    {/* RAG chat — available whenever a ticket exists for this session
                        (set on ingestion and restored on revisit), so chat history
                        shows when returning to a past session (AC3). */}
                    {displayedSessionId && jiraTicketId && (
                        <ChatPanel sessionId={displayedSessionId} />
                    )}

                    {bddContent && (
                        <GitHubSourceSelector
                            onVerify={handleRunVerification}
                            isVerifying={isVerifying}
                            onStop={stopVerification}
                        />
                    )}

                    {(verificationResults.length > 0 || verificationSummary !== null || isVerifying) && (
                        <VerificationResultsPanel />
                    )}

                    {/* globalError also surfaces as a toast; this keeps it on
                        screen for anyone who dismissed or missed that. */}
                    {globalError && (
                        <div
                            className="gutter-rule rounded-lg border border-fail/30 bg-fail-soft/60 px-4 py-3"
                            data-signal="fail"
                            role="alert"
                        >
                            <p className="eyebrow text-fail-ink">Failed</p>
                            <p className="mt-1 text-sm text-foreground">{globalError}</p>
                        </div>
                    )}
                </div>
            </main>

            <ConfirmModal
                open={pendingAction === "ingest"}
                title="Fetch a ticket and clear this session?"
                description="Fetching replaces the acceptance criteria and clears the scenarios, verification results and repository target currently on screen. Anything you have not saved is lost."
                confirmLabel="Fetch ticket"
                destructive
                onConfirm={() => void runIngest()}
                onCancel={() => setPendingAction(null)}
            />

            <ConfirmModal
                open={pendingAction === "generate"}
                title="Overwrite the current scenarios?"
                description="Generating writes a fresh set of scenarios from the acceptance criteria, replacing everything in the editor — including your own edits."
                confirmLabel="Generate"
                destructive
                onConfirm={runGenerateBDD}
                onCancel={() => setPendingAction(null)}
            />
        </div>
    );
}

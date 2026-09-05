"use client";

import React, { createContext, useContext, useState, ReactNode } from "react";
import { SSELogEvent } from "../lib/types/session";
import {
    DEFAULT_VERIFICATION_MODE,
    type VerificationMode,
    VerificationVerdict,
    VerificationSummary,
    VerificationPlan,
} from "../lib/types/verification";

export interface BDDScenario {
    source_ac_clause: string;
    feature: string;
    scenario: string;
    given: string;
    when: string;
    then: string;
}

interface SessionContextType {
    sessionId: string | null;
    setSessionId: (id: string | null) => void;
    jiraTicketId: string | null;
    setJiraTicketId: (id: string | null) => void;
    /** The URL (or bare key) the user submitted to ingest this session's ticket.
     *  Lives here rather than in the pipeline page's local state so it survives
     *  the route change from the throwaway session id to the real one. */
    jiraTicketUrl: string | null;
    setJiraTicketUrl: (value: string | null) => void;
    acceptanceCriteria: string | null;
    setAcceptanceCriteria: (criteria: string | null) => void;
    bddContent: string;
    setBddContent: (content: string) => void;
    logs: SSELogEvent[];
    setLogs: (logs: SSELogEvent[] | ((prev: SSELogEvent[]) => SSELogEvent[])) => void;
    isIngesting: boolean;
    setIsIngesting: (status: boolean) => void;
    isGeneratingBDD: boolean;
    setIsGeneratingBDD: (status: boolean) => void;
    isVerifying: boolean;
    setIsVerifying: (status: boolean) => void;
    globalError: string | null;
    setGlobalError: (error: string | null) => void;
    // Epic 2: GitHub verification state
    verificationMode: VerificationMode | null;
    setVerificationMode: (mode: VerificationMode | null) => void;
    githubInput: string;
    setGithubInput: (value: string) => void;
    /** Story 4.9: opt-in to knowledge-base enrichment for the verification run. */
    useKnowledgeBase: boolean;
    setUseKnowledgeBase: (value: boolean) => void;
    /** Opt-in: let the project's code index suggest which files to read first. */
    codeIndexEnabled: boolean;
    setCodeIndexEnabled: (value: boolean) => void;
    bddScenarios: BDDScenario[];
    setBddScenarios: (scenarios: BDDScenario[]) => void;
    // Story 2.3: LLM verification results (consumed by Story 2.4 display)
    verificationResults: VerificationVerdict[];
    setVerificationResults: (
        results: VerificationVerdict[] | ((prev: VerificationVerdict[]) => VerificationVerdict[])
    ) => void;
    verificationSummary: VerificationSummary | null;
    setVerificationSummary: (summary: VerificationSummary | null) => void;
    /** How many scenarios this run will actually verify, announced before the
     *  first verdict. Null until a run starts, and lower than the count in the
     *  BDD file whenever duplicates were skipped — progress measured against
     *  the file would then stall short of 100%. */
    verificationPlan: VerificationPlan | null;
    setVerificationPlan: (plan: VerificationPlan | null) => void;
    /** Wipe every field back to its initial value. Call before navigating to
     *  a brand-new session so the destination page boots empty. */
    resetSession: () => void;
}

const SessionContext = createContext<SessionContextType | undefined>(undefined);

export function SessionProvider({ children }: { children: ReactNode }) {
    const [sessionId, setSessionId] = useState<string | null>(null);
    const [jiraTicketId, setJiraTicketId] = useState<string | null>(null);
    const [jiraTicketUrl, setJiraTicketUrl] = useState<string | null>(null);
    const [acceptanceCriteria, setAcceptanceCriteria] = useState<string | null>(null);
    const [bddContent, setBddContent] = useState<string>("");
    const [logs, setLogs] = useState<SSELogEvent[]>([]);

    const [isIngesting, setIsIngesting] = useState<boolean>(false);
    const [isGeneratingBDD, setIsGeneratingBDD] = useState<boolean>(false);
    const [isVerifying, setIsVerifying] = useState<boolean>(false);
    const [globalError, setGlobalError] = useState<string | null>(null);

    // Epic 2: GitHub Code Verification state
    const [verificationMode, setVerificationMode] =
        useState<VerificationMode | null>(DEFAULT_VERIFICATION_MODE);
    const [githubInput, setGithubInput] = useState<string>("");
    const [useKnowledgeBase, setUseKnowledgeBase] = useState<boolean>(false);
    const [codeIndexEnabled, setCodeIndexEnabled] = useState<boolean>(false);
    // Story 2.3: LLM verification results
    const [verificationResults, setVerificationResults] = useState<VerificationVerdict[]>([]);
    const [verificationSummary, setVerificationSummary] = useState<VerificationSummary | null>(null);
    const [verificationPlan, setVerificationPlan] = useState<VerificationPlan | null>(null);
    const [bddScenarios, setBddScenarios] = useState<BDDScenario[]>([]);

    const resetSession = () => {
        setSessionId(null);
        setJiraTicketId(null);
        setJiraTicketUrl(null);
        setAcceptanceCriteria(null);
        setBddContent("");
        setLogs([]);
        setIsIngesting(false);
        setIsGeneratingBDD(false);
        setIsVerifying(false);
        setGlobalError(null);
        setVerificationMode(DEFAULT_VERIFICATION_MODE);
        setGithubInput("");
        setUseKnowledgeBase(false);
        setCodeIndexEnabled(false);
        setBddScenarios([]);
        setVerificationResults([]);
        setVerificationSummary(null);
        setVerificationPlan(null);
    };

    return (
        <SessionContext.Provider
            value={{
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
                logs,
                setLogs,
                isIngesting,
                setIsIngesting,
                isGeneratingBDD,
                setIsGeneratingBDD,
                isVerifying,
                setIsVerifying,
                globalError,
                setGlobalError,
                verificationMode,
                setVerificationMode,
                githubInput,
                setGithubInput,
                useKnowledgeBase,
                setUseKnowledgeBase,
                codeIndexEnabled,
                setCodeIndexEnabled,
                bddScenarios,
                setBddScenarios,
                verificationResults,
                setVerificationResults,
                verificationSummary,
                setVerificationSummary,
                verificationPlan,
                setVerificationPlan,
                resetSession,
            }}
        >
            {children}
        </SessionContext.Provider>
    );
}

export function useSessionContext() {
    const context = useContext(SessionContext);
    if (context === undefined) {
        throw new Error("useSessionContext must be used within a SessionProvider");
    }
    return context;
}

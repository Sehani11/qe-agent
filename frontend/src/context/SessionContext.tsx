"use client";

import React, { createContext, useContext, useState, ReactNode } from "react";
import { SSELogEvent } from "../lib/types/session";
import type {
    VerificationMode,
    FetchedFile,
    VerificationVerdict,
    VerificationSummary,
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
    fetchedFiles: FetchedFile[];
    setFetchedFiles: (files: FetchedFile[]) => void;
    bddScenarios: BDDScenario[];
    setBddScenarios: (scenarios: BDDScenario[]) => void;
    // Story 2.3: LLM verification results (consumed by Story 2.4 display)
    verificationResults: VerificationVerdict[];
    setVerificationResults: (
        results: VerificationVerdict[] | ((prev: VerificationVerdict[]) => VerificationVerdict[])
    ) => void;
    verificationSummary: VerificationSummary | null;
    setVerificationSummary: (summary: VerificationSummary | null) => void;
    /** Wipe every field back to its initial value. Call before navigating to
     *  a brand-new session so the destination page boots empty. */
    resetSession: () => void;
}

const SessionContext = createContext<SessionContextType | undefined>(undefined);

export function SessionProvider({ children }: { children: ReactNode }) {
    const [sessionId, setSessionId] = useState<string | null>(null);
    const [jiraTicketId, setJiraTicketId] = useState<string | null>(null);
    const [acceptanceCriteria, setAcceptanceCriteria] = useState<string | null>(null);
    const [bddContent, setBddContent] = useState<string>("");
    const [logs, setLogs] = useState<SSELogEvent[]>([]);

    const [isIngesting, setIsIngesting] = useState<boolean>(false);
    const [isGeneratingBDD, setIsGeneratingBDD] = useState<boolean>(false);
    const [isVerifying, setIsVerifying] = useState<boolean>(false);
    const [globalError, setGlobalError] = useState<string | null>(null);

    // Epic 2: GitHub Code Verification state
    const [verificationMode, setVerificationMode] = useState<VerificationMode | null>(null);
    const [githubInput, setGithubInput] = useState<string>("");
    const [fetchedFiles, setFetchedFiles] = useState<FetchedFile[]>([]);
    // Story 2.3: LLM verification results
    const [verificationResults, setVerificationResults] = useState<VerificationVerdict[]>([]);
    const [verificationSummary, setVerificationSummary] = useState<VerificationSummary | null>(null);
    const [bddScenarios, setBddScenarios] = useState<BDDScenario[]>([]);

    const resetSession = () => {
        setSessionId(null);
        setJiraTicketId(null);
        setAcceptanceCriteria(null);
        setBddContent("");
        setLogs([]);
        setIsIngesting(false);
        setIsGeneratingBDD(false);
        setIsVerifying(false);
        setGlobalError(null);
        setVerificationMode(null);
        setGithubInput("");
        setFetchedFiles([]);
        setBddScenarios([]);
        setVerificationResults([]);
        setVerificationSummary(null);
    };

    return (
        <SessionContext.Provider
            value={{
                sessionId,
                setSessionId,
                jiraTicketId,
                setJiraTicketId,
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
                fetchedFiles,
                setFetchedFiles,
                bddScenarios,
                setBddScenarios,
                verificationResults,
                setVerificationResults,
                verificationSummary,
                setVerificationSummary,
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

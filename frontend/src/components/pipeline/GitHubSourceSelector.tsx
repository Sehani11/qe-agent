"use client";

import React from "react";
import { useSessionContext } from "@/context/SessionContext";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import type { VerificationMode } from "@/lib/types/verification";
import {
    CircleStop,
    Files,
    GitBranch,
    GitPullRequest,
    ShieldCheck,
    type LucideProps,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/field";
import { Switch } from "@/components/ui/switch";
import { useCodeIndexStatus } from "@/lib/hooks/useKnowledge";
import { useProject } from "@/lib/hooks/useProjects";
import { useActiveProjectId } from "@/lib/stores/projectStore";
import { cn } from "@/lib/utils";

/**
 * Turn a project's stored repository into the URL this field expects.
 *
 * Project settings documents the value as `owner/repo` OR a full URL, and both
 * shapes really do get typed in, so the one that reaches the input has to be
 * normalised rather than pasted through — `acme/app` in a field whose
 * placeholder is a URL looks like a mistake, and the backend resolves the two
 * differently.
 *
 * Exported for its own tests: the shapes it has to survive are the interesting
 * part, and they are hard to reach through the component.
 */
export function repoUrlFrom(repo?: string | null): string {
    const trimmed = (repo ?? "").trim().replace(/\/+$/, "");
    if (!trimmed) return "";
    if (/^https?:\/\//i.test(trimmed)) return trimmed;
    // Host-qualified but scheme-less, e.g. "github.com/acme/app". Prefixing the
    // shorthand form onto this would produce github.com/github.com/acme/app.
    if (/^[\w.-]+\.[a-z]{2,}\//i.test(trimmed)) return `https://${trimmed}`;
    return `https://github.com/${trimmed.replace(/^\/+/, "")}`;
}

interface GitHubSourceSelectorProps {
    onVerify: () => void;
    /** Abandon a run in progress. Verdicts already returned are kept. */
    onStop: () => void;
    isVerifying: boolean;
}

// FIX H1/M4: Store Icon component references, not instantiated JSX elements at module scope.
// JSX at module scope creates element instances once at load time, breaking React's render-cycle
// element identity model. Using ComponentType lets React instantiate icons per render.
interface ModeConfig {
    value: VerificationMode;
    label: string;
    Icon: React.ComponentType<LucideProps>;
    placeholder: string;
    multiline: boolean;
    /** What this mode actually reads, so the choice is obvious before you commit. */
    blurb: string;
}

// Order is the recommendation: full repo first because it is the default and
// the one that needs least prior knowledge; the narrower modes follow as
// opt-in optimisations.
const MODES: ModeConfig[] = [
    {
        value: "full_repo",
        label: "Full Repository",
        Icon: GitBranch,
        placeholder: "https://github.com/org/repo",
        multiline: false,
        blurb: "Searches the whole repository. Takes longer, and finds code you did not think to point at.",
    },
    {
        value: "exact_files",
        label: "Exact File Paths",
        Icon: Files,
        placeholder: "One GitHub file URL per line, e.g. https://github.com/org/repo/blob/main/src/auth/routes.py",
        multiline: true,
        blurb: "Reads only the files you list. Fastest, and best when you know where the behaviour lives.",
    },
    {
        value: "pull_request",
        label: "Pull Request",
        Icon: GitPullRequest,
        placeholder: "https://github.com/org/repo/pull/42",
        multiline: false,
        blurb: "Reads only what the pull request changes. Use it to check a branch before merge.",
    },
];

export default function GitHubSourceSelector({
    onVerify,
    onStop,
    isVerifying,
}: GitHubSourceSelectorProps) {
    const {
        verificationMode,
        setVerificationMode,
        githubInput,
        setGithubInput,
        useKnowledgeBase,
        setUseKnowledgeBase,
        codeIndexEnabled,
        setCodeIndexEnabled,
    } = useSessionContext();

    // The repository this project already names, ready to drop into the field.
    const activeProjectId = useActiveProjectId();
    const { data: project } = useProject(activeProjectId);
    const defaultRepoUrl = repoUrlFrom(project?.github_repo);

    // Whether the code-index toggle can do anything. Retrieval needs an index
    // to read; without one the switch would send a flag the server ignores.
    const { data: codeIndex } = useCodeIndexStatus();
    const hasCodeIndex = Boolean(codeIndex?.indexed);
    const codeIndexHint = hasCodeIndex
        ? `Point the agent at the files most likely to hold each scenario, instead of it hunting for them. Indexed from ${codeIndex?.repo ?? "this project's repository"} at ${(codeIndex?.indexed_ref ?? "").slice(0, 7)}.`
        : "Index this project's repository from the Knowledge page, under Code index, to enable this.";

    // Seeding on the mode CLICK is not enough: `DEFAULT_VERIFICATION_MODE` is
    // `full_repo`, so a session opens in that mode with nobody having clicked
    // anything, and the field sat empty until you switched away and back.
    //
    // Fires once per stretch of time the mode is full_repo, tracked by the ref,
    // so clearing the box to type a different repository is not immediately
    // undone — an effect keyed on "input is empty" would fight the user.
    const seededRef = React.useRef(false);
    React.useEffect(() => {
        if (verificationMode !== "full_repo") {
            // Leaving the mode arms it again, so coming back re-seeds.
            seededRef.current = false;
            return;
        }
        if (seededRef.current) return;
        // The project may still be loading. Staying un-armed means this runs
        // again when it arrives, instead of deciding "no default" too early.
        if (!defaultRepoUrl) return;

        seededRef.current = true;
        // A restored past run, or anything already typed, outranks the default.
        if (githubInput.trim() === "") setGithubInput(defaultRepoUrl);
    }, [verificationMode, defaultRepoUrl, githubInput, setGithubInput]);

    const isVerifyDisabled = verificationMode === null || githubInput.trim() === "" || isVerifying;

    // Stop discards every verdict the run has produced, and a run costs an LLM
    // call per scenario — so it is confirmed, like the other controls in this
    // app that destroy work rather than produce it.
    const [confirmingStop, setConfirmingStop] = React.useState(false);

    const handleModeChange = (mode: VerificationMode) => {
        setVerificationMode(mode);
        // Still cleared on switch (AC 5) — a value typed for one mode is wrong
        // for another. Full-repo verification is the exception: what it needs is
        // exactly what the project already stores, so it opens prefilled and the
        // common case becomes "check it and hit verify" rather than "paste the
        // same URL again". Still an ordinary input, so it can be overtyped.
        //
        // Only this mode: the other two need a file or pull-request URL, which
        // no project-level default can supply.
        setGithubInput(mode === "full_repo" ? defaultRepoUrl : "");
    };

    const activeMode = MODES.find((m) => m.value === verificationMode);

    return (
        <section className="overflow-hidden rounded-lg border border-rule bg-card">
            <header className="flex items-center gap-3 border-b border-rule px-4 py-3.5">
                <ShieldCheck className="h-4 w-4 shrink-0 text-pass" aria-hidden="true" />
                <div className="min-w-0">
                    <p className="eyebrow text-muted-foreground">Verify</p>
                    <h2 className="mt-1 text-base">Where should the agent look?</h2>
                </div>
            </header>

            <div className="flex flex-col gap-5 p-4">
                <div>
                    <p className="eyebrow mb-2 text-muted-foreground">Source</p>
                    <div
                        role="tablist"
                        aria-label="Verification mode selector"
                        className="grid gap-2 sm:grid-cols-3"
                    >
                        {MODES.map((mode) => {
                            const isActive = verificationMode === mode.value;
                            // FIX H1: Instantiate Icon component during render, not at module scope
                            const { Icon } = mode;
                            return (
                                <button
                                    key={mode.value}
                                    id={`github-mode-${mode.value}`}
                                    role="tab"
                                    aria-selected={isActive}
                                    onClick={() => handleModeChange(mode.value)}
                                    className={cn(
                                        "flex items-center justify-center gap-2 rounded-md border px-3 py-2.5 text-[0.8125rem] font-medium transition-colors",
                                        isActive
                                            ? "border-pass/40 bg-pass-soft text-pass-ink"
                                            : "border-rule bg-surface text-muted-foreground hover:bg-muted hover:text-foreground"
                                    )}
                                >
                                    <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                                    <span className="truncate">{mode.label}</span>
                                </button>
                            );
                        })}
                    </div>
                </div>

                {/* Input area — shown only when a mode is selected */}
                {activeMode && (
                    <div className="flex flex-col gap-2">
                        {/* FIX M3: id is mode-specific so it is always unique in the DOM */}
                        <label
                            htmlFor={`github-source-input-${activeMode.value}`}
                            className="eyebrow text-muted-foreground"
                        >
                            {activeMode.label}
                        </label>
                        {activeMode.multiline ? (
                            <Textarea
                                id={`github-source-input-${activeMode.value}`}
                                mono
                                rows={4}
                                placeholder={activeMode.placeholder}
                                value={githubInput}
                                onChange={(e) => setGithubInput(e.target.value)}
                                // FIX L3: Suppress spellcheck/autocomplete on code-path inputs
                                spellCheck={false}
                                autoComplete="off"
                                className="text-[0.8125rem]"
                            />
                        ) : (
                            <Input
                                id={`github-source-input-${activeMode.value}`}
                                type="text"
                                mono
                                placeholder={activeMode.placeholder}
                                value={githubInput}
                                onChange={(e) => setGithubInput(e.target.value)}
                                // FIX L3: Suppress spellcheck/autocomplete on URL/path inputs
                                spellCheck={false}
                                autoComplete="off"
                                className="text-[0.8125rem]"
                            />
                        )}
                        <p className="text-xs leading-relaxed text-muted-foreground">
                            {activeMode.blurb}
                        </p>
                    </div>
                )}

                {/* Story 4.9: opt-in to project knowledge-base enrichment (default off) */}
                <Switch
                    checked={useKnowledgeBase}
                    onCheckedChange={setUseKnowledgeBase}
                    label="Use project knowledge base"
                    description="Enrich each scenario with relevant Confluence / Jira / document context during verification."
                    className="w-full items-start rounded-md border border-rule bg-surface-raised px-3 py-2.5 transition-colors hover:bg-muted/50"
                />

                {/* Opt-in to code-index discovery (default off). Disabled with
                    an explanation rather than hidden when the project has no
                    index: a control that vanishes leaves nobody knowing the
                    capability exists, while one that is present and explained
                    says what to do to get it. */}
                <div title={codeIndexHint}>
                    <Switch
                        checked={codeIndexEnabled && hasCodeIndex}
                        onCheckedChange={setCodeIndexEnabled}
                        disabled={!hasCodeIndex}
                        label="Use code index"
                        description={codeIndexHint}
                        className="w-full items-start rounded-md border border-rule bg-surface-raised px-3 py-2.5 transition-colors hover:bg-muted/50"
                    />
                </div>

                {/* Run Verification button — single agentic flow (Story 2.5)
                    FIX H2: Removed aria-disabled — native <button disabled> already communicates
                    disabled state to assistive tech. Mixing both sends conflicting tab-order signals.

                    Stop sits BESIDE the progress button rather than replacing
                    it. A run is one LLM call per scenario and can last minutes,
                    so the only previous way out of a wrong repo or a wrong mode
                    was to reload the page and lose the verdicts already in.
                    Keeping "Verifying…" visible means the row still says the
                    run is alive; the stop is an addition to that, not a
                    substitute for it. */}
                <div
                    data-testid="verify-row"
                    className="flex w-full items-center gap-2"
                >
                    <Button
                        id="verify-button"
                        size="lg"
                        onClick={onVerify}
                        disabled={isVerifyDisabled}
                        loading={isVerifying}
                        className="flex-1"
                    >
                        {!isVerifying && (
                            <ShieldCheck className="h-4 w-4" aria-hidden="true" />
                        )}
                        {isVerifying ? "Verifying…" : "Run Verification"}
                    </Button>
                    {isVerifying && (
                        <Button
                            size="lg"
                            variant="destructive"
                            onClick={() => setConfirmingStop(true)}
                            className="shrink-0"
                        >
                            <CircleStop className="h-4 w-4" aria-hidden="true" />
                            Stop
                        </Button>
                    )}
                </div>

                {/* Helper text when no mode selected */}
                {verificationMode === null && (
                    <p className="text-center text-xs text-muted-foreground">
                        Select a verification mode above to get started.
                    </p>
                )}
            </div>

            <ConfirmModal
                open={confirmingStop}
                destructive
                title="Stop verification"
                description={
                    <>
                        Every verdict from this run is discarded, including the
                        scenarios already checked — a partial result would leave
                        unchecked scenarios looking like passing ones. Running
                        verification again starts a fresh check of all of them.
                    </>
                }
                confirmLabel="Stop"
                cancelLabel="Keep going"
                onConfirm={() => {
                    setConfirmingStop(false);
                    onStop();
                }}
                onCancel={() => setConfirmingStop(false)}
            />
        </section>
    );
}

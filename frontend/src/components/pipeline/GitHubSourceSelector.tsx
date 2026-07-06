"use client";

import React from "react";
import { useSessionContext } from "@/context/SessionContext";
import type { VerificationMode } from "@/lib/types/verification";
import { GitBranch, Files, GitPullRequest, ShieldCheck, type LucideProps } from "lucide-react";

interface GitHubSourceSelectorProps {
    onVerify: () => void;
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
}

const MODES: ModeConfig[] = [
    {
        value: "exact_files",
        label: "Exact File Paths",
        Icon: Files,
        placeholder: "One GitHub file URL per line, e.g. https://github.com/org/repo/blob/main/src/auth/routes.py",
        multiline: true,
    },
    {
        value: "full_repo",
        label: "Full Repository",
        Icon: GitBranch,
        placeholder: "https://github.com/org/repo",
        multiline: false,
    },
    {
        value: "pull_request",
        label: "Pull Request",
        Icon: GitPullRequest,
        placeholder: "https://github.com/org/repo/pull/42",
        multiline: false,
    },
];

export default function GitHubSourceSelector({
    onVerify,
    isVerifying,
}: GitHubSourceSelectorProps) {
    const { verificationMode, setVerificationMode, githubInput, setGithubInput } =
        useSessionContext();

    const isVerifyDisabled = verificationMode === null || githubInput.trim() === "" || isVerifying;

    const handleModeChange = (mode: VerificationMode) => {
        setVerificationMode(mode);
        setGithubInput(""); // AC: clear input on mode switch
    };

    const activeMode = MODES.find((m) => m.value === verificationMode);

    return (
        <div className="relative">
            <div className="flex h-full flex-col overflow-hidden rounded-3xl border border-sky-100 bg-white shadow-[0_20px_70px_-30px_rgba(37,99,235,0.16)]">
                {/* Header */}
                <div className="flex items-center justify-between border-b border-slate-200 bg-white px-5 py-4">
                    <div className="flex items-center space-x-3">
                        <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 text-white shadow-sm">
                            <ShieldCheck className="h-4 w-4" />
                        </div>
                        <div>
                            <h2 className="text-base font-semibold tracking-wide text-slate-900">GitHub Source Verification</h2>
                            <p className="text-sm text-slate-500">Select the code source to verify against your BDD scenarios.</p>
                        </div>
                    </div>
                </div>

                <div className="flex flex-col gap-5 p-5">
                    {/* Mode selector — segmented control */}
                    <div>
                        <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">Verification Mode</p>
                        <div
                            role="tablist"
                            aria-label="Verification mode selector"
                            className="flex gap-2"
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
                                        className={[
                                            "flex flex-1 items-center justify-center gap-2 rounded-xl border px-3 py-2.5 text-sm font-medium transition-all",
                                            isActive
                                                ? "border-blue-500 bg-blue-50 text-blue-700 shadow-sm ring-1 ring-blue-200"
                                                : "border-slate-200 bg-slate-50 text-slate-600 hover:border-sky-300 hover:bg-sky-50 hover:text-sky-700",
                                        ].join(" ")}
                                    >
                                        <Icon className="w-4 h-4" aria-hidden="true" />
                                        <span className="hidden sm:inline">{mode.label}</span>
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
                                className="text-xs font-semibold uppercase tracking-wider text-slate-500"
                            >
                                {activeMode.label}
                            </label>
                            {activeMode.multiline ? (
                                <textarea
                                    id={`github-source-input-${activeMode.value}`}
                                    rows={4}
                                    placeholder={activeMode.placeholder}
                                    value={githubInput}
                                    onChange={(e) => setGithubInput(e.target.value)}
                                    // FIX L3: Suppress spellcheck/autocomplete on code-path inputs
                                    spellCheck={false}
                                    autoComplete="off"
                                    className="w-full resize-none rounded-2xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-slate-900 shadow-sm outline-none transition focus:border-sky-500 focus:bg-white focus:ring-2 focus:ring-sky-200"
                                />
                            ) : (
                                <input
                                    id={`github-source-input-${activeMode.value}`}
                                    type="text"
                                    placeholder={activeMode.placeholder}
                                    value={githubInput}
                                    onChange={(e) => setGithubInput(e.target.value)}
                                    // FIX L3: Suppress spellcheck/autocomplete on URL/path inputs
                                    spellCheck={false}
                                    autoComplete="off"
                                    className="w-full rounded-2xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-slate-900 shadow-sm outline-none transition focus:border-sky-500 focus:bg-white focus:ring-2 focus:ring-sky-200"
                                />
                            )}
                        </div>
                    )}

                    {/* Run Verification button — single agentic flow (Story 2.5)
                        FIX H2: Removed aria-disabled — native <button disabled> already communicates
                        disabled state to assistive tech. Mixing both sends conflicting tab-order signals. */}
                    <button
                        id="verify-button"
                        onClick={onVerify}
                        disabled={isVerifyDisabled}
                        className="flex w-full items-center justify-center gap-2 rounded-2xl bg-gradient-to-r from-blue-600 to-indigo-600 px-6 py-3 text-sm font-semibold text-white shadow-lg shadow-blue-100 transition hover:from-blue-500 hover:to-indigo-500 disabled:cursor-not-allowed disabled:from-slate-300 disabled:to-slate-300 disabled:text-slate-400 disabled:shadow-none"
                    >
                        <ShieldCheck className="h-4 w-4" aria-hidden="true" />
                        {isVerifying ? "Verifying…" : "Run Verification"}
                    </button>

                    {/* Helper text when no mode selected */}
                    {verificationMode === null && (
                        <p className="text-center text-xs text-slate-400">
                            Select a verification mode above to get started.
                        </p>
                    )}
                </div>
            </div>
        </div>
    );
}

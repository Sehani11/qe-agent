"use client";

import React from "react";
import { useSessionContext } from "@/context/SessionContext";
import VerificationResultRow from "./VerificationResultRow";

export default function VerificationResultsPanel() {
    const { verificationResults, verificationSummary, isVerifying, bddContent } = useSessionContext();

    // AC 7: Do not render when there are no results, no summary, and not verifying
    if (verificationResults.length === 0 && verificationSummary === null && !isVerifying) {
        return null;
    }

    // Count total scenarios from BDD content for progress percentage
    const totalScenarios = (bddContent.match(/^\s*Scenario[: ]/gm) ?? []).length;
    const verifiedCount = verificationResults.length;
    const progressPercent =
        isVerifying && totalScenarios > 0
            ? Math.round((verifiedCount / totalScenarios) * 100)
            : null;

    // Compute pass percentage inline — no useEffect for derived state
    // When total === 0, passPercent is 0 (not null) so the summary bar still renders
    const passPercent =
        verificationSummary !== null
            ? verificationSummary.total > 0
                ? Math.round((verificationSummary.passed / verificationSummary.total) * 100)
                : 0
            : null;

    const summaryAccentClass =
        passPercent === null
            ? ""
            : passPercent === 100
              ? "border-emerald-200 bg-emerald-50 text-emerald-900"
              : passPercent === 0
                ? "border-rose-200 bg-rose-50 text-rose-900"
                : "border-amber-200 bg-amber-50 text-amber-900";

    return (
        <div>
            <div className="rounded-3xl border border-sky-100 bg-white/90 p-6 shadow-[0_20px_70px_-30px_rgba(14,116,144,0.22)] backdrop-blur">
                {/* Section label */}
                <p className="mb-4 text-sm font-semibold uppercase tracking-[0.25em] text-sky-700">
                    Verification Results
                </p>

                {/* Loader — shown while verification is in progress */}
                {isVerifying && (
                    <div className="mb-5 rounded-2xl border border-sky-100 bg-sky-50 px-5 py-4">
                        <div className="mb-2 flex items-center gap-3">
                            <svg
                                className="h-5 w-5 shrink-0 animate-spin text-sky-500"
                                xmlns="http://www.w3.org/2000/svg"
                                fill="none"
                                viewBox="0 0 24 24"
                            >
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                <path
                                    className="opacity-75"
                                    fill="currentColor"
                                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                                />
                            </svg>
                            <p className="text-sm font-semibold text-sky-800">Verifying scenarios…</p>
                            {progressPercent !== null && (
                                <span className="ml-auto text-sm font-bold text-sky-700">{progressPercent}%</span>
                            )}
                        </div>
                        {progressPercent !== null && (
                            <div className="h-2 w-full overflow-hidden rounded-full bg-sky-200">
                                <div
                                    className="h-full rounded-full bg-sky-500 transition-all duration-500"
                                    style={{ width: `${progressPercent}%` }}
                                />
                            </div>
                        )}
                        <p className="mt-2 text-xs text-sky-600">
                            {totalScenarios > 0
                                ? `${verifiedCount} of ${totalScenarios} scenario${totalScenarios > 1 ? "s" : ""} verified`
                                : "Analysing your codebase against BDD scenarios"}
                        </p>
                    </div>
                )}

                {/* Summary bar — shown as soon as verificationSummary is available (AC 4) */}
                {verificationSummary !== null && passPercent !== null && (
                    <div
                        className={`mb-5 flex flex-wrap items-center gap-4 rounded-2xl border px-5 py-4 ${summaryAccentClass}`}
                    >
                        <span className="text-3xl font-bold">{passPercent}%</span>
                        <div className="flex flex-col text-sm">
                            <span className="font-semibold">Overall Pass Rate</span>
                            <span className="text-xs opacity-80">
                                {verificationSummary.passed} passed &bull; {verificationSummary.failed} failed &bull;{" "}
                                {verificationSummary.total} total
                            </span>
                        </div>
                    </div>
                )}

                {/* Results list — grows progressively as SSE verdicts arrive (AC 5) */}
                {verificationResults.length > 0 && (
                    <div className="flex flex-col gap-3">
                        {verificationResults.map((verdict) => (
                            <VerificationResultRow key={verdict.scenario_id} verdict={verdict} />
                        ))}
                    </div>
                )}
        </div>
        </div>
    );
}

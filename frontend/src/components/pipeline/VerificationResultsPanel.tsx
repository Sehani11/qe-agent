"use client";

import React from "react";
import { Download, FileText } from "lucide-react";
import { useSessionContext } from "@/context/SessionContext";
import { useExportReportCsv, useExportReportPdf } from "@/lib/hooks/useVerification";
import { Button } from "@/components/ui/button";
import { ProgressBar, RunSpinner } from "@/components/ui/loaders";
import { cn } from "@/lib/utils";
import VerificationResultRow from "./VerificationResultRow";

/** Suffix for the "Verified against" label, so the eyebrow names the mode. */
const MODE_LABELS: Record<string, string> = {
    full_repo: " · full repository",
    exact_files: " · exact files",
    pull_request: " · pull request",
};

export default function VerificationResultsPanel() {
    const {
        sessionId,
        verificationResults,
        verificationSummary,
        verificationPlan,
        isVerifying,
        bddContent,
        verificationMode,
        githubInput,
    } = useSessionContext();

    // Story 5.4: report export (enabled only when verification results exist)
    const exportPdf = useExportReportPdf();
    const exportCsv = useExportReportCsv();
    const canExport = verificationResults.length > 0 && !!sessionId;
    const exportError = exportPdf.error || exportCsv.error;

    // AC 7: Do not render when there are no results, no summary, and not verifying
    if (verificationResults.length === 0 && verificationSummary === null && !isVerifying) {
        return null;
    }

    // Progress counts against what the run will actually verify. The BDD file's
    // own scenario count is the fallback for a run that reported no plan, but it
    // over-counts whenever duplicates were skipped — and a bar that stops at 60%
    // on a finished run reads as a failure.
    const totalScenarios =
        verificationPlan?.total ?? (bddContent.match(/^\s*Scenario[: ]/gm) ?? []).length;
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

    // An undecided scenario is not a pass, so a run holding any of them can
    // never read as an unqualified success — 100% only claims the whole run
    // was actually established.
    const inconclusiveCount = verificationSummary?.inconclusive ?? 0;
    const partialCount = verificationSummary?.partial ?? 0;
    // A partial scenario is outstanding work, so it disqualifies a clean sweep
    // exactly as an undecided one does.
    const isCleanSweep =
        passPercent === 100 && inconclusiveCount === 0 && partialCount === 0;

    // Class names double as the signal: full pass, total fail, or partial.
    const summaryAccentClass =
        passPercent === null
            ? ""
            : isCleanSweep
              ? "border-pass/30 bg-pass-soft"
              : passPercent === 0 && inconclusiveCount === 0
                ? "border-fail/30 bg-fail-soft"
                : "border-pending/30 bg-pending-soft";

    const summarySignal =
        passPercent === null
            ? ""
            : isCleanSweep
              ? "pass"
              : passPercent === 0 && inconclusiveCount === 0
                ? "fail"
                : "pending";

    return (
        <section className="overflow-hidden rounded-lg border border-rule bg-card">
            <header className="flex flex-wrap items-start justify-between gap-3 border-b border-rule px-4 py-3.5">
                <div className="min-w-0">
                    <p className="eyebrow text-muted-foreground">Then</p>
                    <h2 className="mt-1 text-base">Verification Results</h2>
                </div>

                <div className="flex items-center gap-1.5">
                    <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => sessionId && exportPdf.mutate(sessionId)}
                        disabled={!canExport || exportPdf.isPending}
                        loading={exportPdf.isPending}
                    >
                        {!exportPdf.isPending && (
                            <FileText className="h-3.5 w-3.5" aria-hidden="true" />
                        )}
                        <span>{exportPdf.isPending ? "Generating…" : "Download PDF"}</span>
                    </Button>
                    <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => sessionId && exportCsv.mutate(sessionId)}
                        disabled={!canExport || exportCsv.isPending}
                        loading={exportCsv.isPending}
                    >
                        {!exportCsv.isPending && (
                            <Download className="h-3.5 w-3.5" aria-hidden="true" />
                        )}
                        <span>{exportCsv.isPending ? "Generating…" : "Download CSV"}</span>
                    </Button>
                </div>
            </header>

            <div className="p-4">
                {exportError && (
                    <div
                        className="gutter-rule mb-4 rounded-md border border-fail/30 bg-fail-soft/60 px-3 py-2.5"
                        data-signal="fail"
                        role="alert"
                    >
                        <p className="text-xs text-foreground">
                            The report could not be generated. Try the export again.
                        </p>
                    </div>
                )}

                {/* In-flight state — shown while verdicts stream in */}
                {isVerifying && (
                    <div className="mb-4 rounded-md border border-rule bg-surface-raised px-4 py-3.5">
                        <div className="mb-2.5 flex items-center gap-2.5">
                            <RunSpinner className="text-pending" />
                            <p className="font-mono text-[0.8125rem] font-semibold text-foreground">
                                Checking scenarios against code
                            </p>
                            {progressPercent !== null && (
                                <span className="ml-auto font-mono text-[0.8125rem] font-semibold text-muted-foreground tabular-nums">
                                    {progressPercent}%
                                </span>
                            )}
                        </div>
                        <ProgressBar
                            value={progressPercent}
                            signal="pending"
                            label="Verification progress"
                        />
                        <p className="mt-2 font-mono text-xs text-muted-foreground">
                            {totalScenarios > 0
                                ? `${verifiedCount} of ${totalScenarios} scenario${totalScenarios > 1 ? "s" : ""} checked`
                                : "Reading the repository"}
                        </p>
                    </div>
                )}

                {/* Summary bar — shown as soon as verificationSummary is available (AC 4) */}
                {verificationSummary !== null && passPercent !== null && (
                    <div
                        className={cn(
                            "gutter-rule mb-4 flex flex-wrap items-center gap-x-5 gap-y-2 rounded-md border px-4 py-3.5",
                            summaryAccentClass
                        )}
                        data-signal={summarySignal}
                    >
                        <span className="font-mono text-3xl font-semibold tabular-nums text-foreground">
                            {passPercent}%
                        </span>
                        <div className="flex flex-col">
                            <span className="text-[0.8125rem] font-semibold text-foreground">
                                Overall Pass Rate
                            </span>
                            <span className="mt-0.5 font-mono text-xs text-muted-foreground">
                                {verificationSummary.passed} passed &bull;{" "}
                                {/* Only shown when there are any, so an all-decided
                                    run reads exactly as it did before. */}
                                {partialCount > 0 && <>{partialCount} partial &bull; </>}
                                {verificationSummary.failed} failed &bull;{" "}
                                {inconclusiveCount > 0 && (
                                    <>{inconclusiveCount} inconclusive &bull; </>
                                )}
                                {verificationSummary.total} total
                            </span>
                        </div>
                    </div>
                )}

                {/* What these verdicts were checked against. Restored from the
                    stored run on revisit, so a past session says what it read
                    rather than leaving the reader to guess. */}
                {githubInput.trim() && verificationResults.length > 0 && (
                    <div className="mb-4 rounded-md border border-rule bg-surface-raised px-3 py-2.5">
                        <p className="eyebrow text-muted-foreground">
                            Verified against{MODE_LABELS[verificationMode ?? ""] ?? ""}
                        </p>
                        <ul className="mt-1.5 space-y-0.5">
                            {githubInput
                                .split(/\r?\n/)
                                .map((line) => line.trim())
                                .filter(Boolean)
                                .map((line) => (
                                    <li key={line} className="min-w-0">
                                        <a
                                            href={line}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            className="block truncate font-mono text-xs text-foreground underline decoration-rule-strong underline-offset-2 transition-colors hover:decoration-foreground"
                                        >
                                            {line}
                                        </a>
                                    </li>
                                ))}
                        </ul>
                    </div>
                )}

                {/* Results list — grows progressively as SSE verdicts arrive (AC 5) */}
                {verificationResults.length > 0 && (
                    <div className="flex flex-col gap-2">
                        {verificationResults.map((verdict, index) => (
                            <div
                                key={verdict.scenario_id}
                                style={{ animation: `row-in 220ms ease-out ${index * 18}ms both` }}
                            >
                                <VerificationResultRow verdict={verdict} />
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </section>
    );
}

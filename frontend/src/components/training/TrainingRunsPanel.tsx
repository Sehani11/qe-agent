"use client";

import React, { useState } from "react";
import {
    AlertCircle,
    ChevronDown,
    Download,
    ExternalLink,
    Loader2,
    Play,
    Terminal,
    Trash2,
} from "lucide-react";

import { Badge, type Signal } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { SkeletonRows } from "@/components/ui/loaders";
import { useToast } from "@/components/ui/toast";
import { apiErrorMessage } from "@/lib/api/errors";
import {
    useDeleteFinishedTrainingRuns,
    useDeleteTrainingRun,
    useDownloadTrainedModel,
    useStartTrainingRun,
    useTrainingReadiness,
    useTrainingRuns,
} from "@/lib/hooks/useTrainingData";
import type { TrainingRun, TrainingRunStatus } from "@/lib/types/training";
import { isRunActive } from "@/lib/types/training";

/**
 * How a run's status reads, as a badge.
 *
 * The three working states share one tone rather than each getting their own:
 * they are stages of a single wait, and colour-coding them would imply a
 * difference in kind that only the label carries.
 */
const STATUS_TONE: Record<TrainingRunStatus, Signal> = {
    queued: "neutral",
    building: "pending",
    training: "pending",
    fetching: "pending",
    completed: "pass",
    failed: "fail",
};

const STATUS_LABEL: Record<TrainingRunStatus, string> = {
    queued: "Queued",
    building: "Building set",
    training: "Training",
    fetching: "Collecting",
    completed: "Trained",
    failed: "Failed",
};

/** The run's own output, collapsed until asked for. */
function RunLog({ log }: { log: string }) {
    const [open, setOpen] = useState(false);
    if (!log.trim()) return null;

    return (
        <div className="mt-3">
            <button
                type="button"
                onClick={() => setOpen((current) => !current)}
                aria-expanded={open}
                className="inline-flex items-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
            >
                <Terminal className="h-3.5 w-3.5" aria-hidden="true" />
                {open ? "Hide log" : "Show log"}
                <ChevronDown
                    className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-180" : ""}`}
                    aria-hidden="true"
                />
            </button>
            {open && (
                // Pinned to the BOTTOM of its scroll box via flex-col-reverse:
                // a running job's newest line is the one worth reading, and a
                // top-anchored box would show the same first screen forever.
                <pre className="scrollbar-thin mt-2 flex max-h-64 flex-col-reverse overflow-auto rounded-md border border-rule bg-surface-raised p-3 font-mono text-[0.6875rem] leading-relaxed text-muted-foreground">
                    <code>{log}</code>
                </pre>
            )}
        </div>
    );
}

function RunRow({
    run,
    onDelete,
}: {
    run: TrainingRun;
    /** Opens the confirmation. Owned by the panel so only one can be open. */
    onDelete: (run: TrainingRun) => void;
}) {
    const download = useDownloadTrainedModel();
    const toast = useToast();
    const active = isRunActive(run.status);

    const handleDownload = () => {
        download.mutate(run.id, {
            onError: (error) => {
                toast.error("Could not download the model", {
                    description: apiErrorMessage(
                        error,
                        "The adapter may no longer be on the server. Train again to produce a new one."
                    ),
                });
            },
        });
    };

    return (
        <li className="rounded-md border border-rule bg-surface-raised px-3 py-2.5">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex min-w-0 items-center gap-2">
                    <Badge signal={STATUS_TONE[run.status]}>
                        {active && (
                            <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
                        )}
                        {STATUS_LABEL[run.status]}
                    </Badge>
                    <span className="truncate text-[0.8125rem] text-foreground">
                        {run.detail ?? "—"}
                    </span>
                </div>

                <div className="flex shrink-0 items-center gap-3 font-mono text-xs text-muted-foreground">
                    {/* Only once the build has run: "0 pairs" on a queued run
                        reads as a result rather than as "not yet known". */}
                    {run.train_pairs > 0 && (
                        <span>
                            {run.train_pairs} train / {run.holdout_pairs} holdout
                        </span>
                    )}
                    <span>{new Date(run.created_at).toLocaleDateString()}</span>

                    {run.kernel_ref && (
                        <a
                            href={`https://www.kaggle.com/code/${run.kernel_ref}`}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-1 transition-colors hover:text-foreground"
                        >
                            Kernel
                            <ExternalLink className="h-3 w-3" aria-hidden="true" />
                        </a>
                    )}

                    {run.has_model && (
                        <Button
                            size="sm"
                            variant="outline"
                            onClick={handleDownload}
                            disabled={download.isPending}
                            loading={download.isPending}
                        >
                            {!download.isPending && (
                                <Download className="h-3.5 w-3.5" aria-hidden="true" />
                            )}
                            Download model
                        </Button>
                    )}

                    {/* Absent, not disabled, while the run is active: deleting
                        the row would not stop the work, so there is nothing
                        here for the button to mean. */}
                    {!active && (
                        <button
                            type="button"
                            onClick={() => onDelete(run)}
                            aria-label={`Delete run from ${new Date(
                                run.created_at
                            ).toLocaleDateString()}`}
                            title="Delete this run"
                            className="inline-flex h-7 w-7 items-center justify-center rounded border border-rule text-muted-foreground transition-colors hover:border-fail/40 hover:bg-fail-soft hover:text-fail-ink"
                        >
                            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                        </button>
                    )}
                </div>
            </div>

            <RunLog log={run.log} />
        </li>
    );
}

/**
 * Start a fine-tune, then watch it and collect the result.
 *
 * The run is a long out-of-process job — it builds a dataset, pushes it to
 * Kaggle's free GPU and waits for the kernel — so there is nothing to await
 * here. Starting one returns immediately and the list below becomes the
 * progress indicator, polling while anything is still moving.
 */
export default function TrainingRunsPanel({
    datasetCount = 0,
}: {
    /** Uploaded datasets available to train on. Zero means there is nothing to do. */
    datasetCount?: number;
}) {
    const { data: readiness } = useTrainingReadiness();
    const { data: runs, isLoading, isError } = useTrainingRuns();
    const startRun = useStartTrainingRun();
    const deleteRun = useDeleteTrainingRun();
    const deleteFinished = useDeleteFinishedTrainingRuns();
    const toast = useToast();

    const [confirming, setConfirming] = useState(false);
    const [pendingDelete, setPendingDelete] = useState<TrainingRun | null>(null);
    // Separate from `pendingDelete` so the two confirmations can never be open
    // at once and cannot be mistaken for one another.
    const [confirmingClearAll, setConfirmingClearAll] = useState(false);

    const handleDeleteRun = () => {
        if (!pendingDelete) return;
        const hadModel = pendingDelete.has_model;
        deleteRun.mutate(pendingDelete.id, {
            onSuccess: () => {
                toast.success("Run deleted", {
                    description: hadModel
                        ? "Its log and the model it produced are gone."
                        : "Its log is gone.",
                });
            },
            onError: (error) => {
                toast.error("Could not delete that run", {
                    description: apiErrorMessage(
                        error,
                        "It is still listed. Try again."
                    ),
                });
            },
            onSettled: () => setPendingDelete(null),
        });
    };

    const handleClearFinished = () => {
        deleteFinished.mutate(undefined, {
            onSuccess: (deleted) => {
                toast.success(`Deleted ${deleted} run${deleted === 1 ? "" : "s"}`, {
                    description:
                        "Their logs and any models they produced are gone. Anything still training was left alone.",
                });
            },
            onError: (error) => {
                toast.error("Could not clear the runs", {
                    description: apiErrorMessage(error, "They are still listed. Try again."),
                });
            },
            onSettled: () => setConfirmingClearAll(false),
        });
    };

    const blockedReason = readiness?.reason ?? null;
    const hasActiveRun = !!runs?.some((run) => isRunActive(run.status));
    // What "Clear finished" would actually remove. Counted from the same list
    // on screen, so the confirmation names the number the user can see.
    const finished = runs?.filter((run) => !isRunActive(run.status)) ?? [];
    const finishedCount = finished.length;
    // Adapters are only on disk here, so the confirmation has to say how many
    // downloadable models the click would destroy.
    const modelsAtRisk = finished.filter((run) => run.has_model).length;
    // Falls back to 2 only until readiness has loaded — the server owns this
    // number, because it follows from how the builder splits train/holdout.
    const minDatasets = readiness?.min_datasets ?? 2;
    const enoughDatasets = datasetCount >= minDatasets;
    // Three separate reasons the button cannot be pressed, kept apart because
    // each has a different fix: configure the server, wait, or upload more.
    const canStart =
        !blockedReason && !hasActiveRun && enoughDatasets && !startRun.isPending;

    const handleStart = () => {
        startRun.mutate(undefined, {
            onSuccess: () => {
                toast.success("Training started", {
                    description:
                        "Building the training set, then handing it to Kaggle's GPU. This takes a while — the run below updates as it goes.",
                });
            },
            // The server's own message, not a guess at it: the refusals it
            // sends are specific and actionable, and listing likely causes
            // instead is wrong whenever the real one is a third thing.
            onError: (error) => {
                toast.error("Could not start training", {
                    description: apiErrorMessage(
                        error,
                        "The server did not respond. Check that the backend is running, then try again."
                    ),
                });
            },
            onSettled: () => setConfirming(false),
        });
    };

    return (
        <div className="flex flex-col gap-5">
            <div className="flex flex-col gap-3 rounded-lg border border-rule bg-card p-4">
                <div className="flex items-center gap-2">
                    <Play className="h-4 w-4 text-keyword" aria-hidden="true" />
                    <h2 className="text-sm">Train a model</h2>
                </div>
                <p className="text-xs leading-relaxed text-muted-foreground">
                    Builds a training set from everything you have uploaded, then runs
                    the fine-tune on Kaggle&apos;s free GPU and brings the adapter back.
                    Expect this to take a while — the GPU run alone is tens of minutes.
                    Only one run happens at a time.
                </p>
                <p className="text-xs leading-relaxed text-muted-foreground">
                    Needs at least{" "}
                    <span className="font-medium text-foreground">
                        {minDatasets} uploaded files
                    </span>
                    . Scenarios from one file are near-duplicates, so the whole file goes
                    to either training or the holdout — never both. With a single file
                    there is nothing left to train on.
                </p>

                {blockedReason && (
                    <div
                        className="gutter-rule flex items-start gap-2 rounded-md border border-pending/30 bg-pending-soft/60 px-3 py-2"
                        data-signal="pending"
                    >
                        <AlertCircle
                            className="mt-0.5 h-3.5 w-3.5 shrink-0 text-pending-ink"
                            aria-hidden="true"
                        />
                        <span className="text-xs text-foreground">{blockedReason}</span>
                    </div>
                )}

                <div className="flex flex-wrap items-center gap-3">
                    <Button
                        onClick={() => setConfirming(true)}
                        disabled={!canStart}
                        loading={startRun.isPending}
                    >
                        {!startRun.isPending && (
                            <Play className="h-3.5 w-3.5" aria-hidden="true" />
                        )}
                        {startRun.isPending ? "Starting…" : "Train now"}
                    </Button>

                    {/* Says which of the reasons applies, rather than leaving a
                        disabled button with no explanation. */}
                    {!blockedReason && hasActiveRun && (
                        <span className="text-xs text-muted-foreground">
                            A run is already in progress.
                        </span>
                    )}
                    {/* Names the real constraint. "Upload at least one" was
                        wrong: one file always becomes the holdout, so the run
                        would build for a minute and then fail with nothing to
                        train on. */}
                    {!blockedReason && !hasActiveRun && !enoughDatasets && (
                        <span className="text-xs text-muted-foreground">
                            {datasetCount === 0
                                ? `Upload at least ${minDatasets} files above first.`
                                : `Upload ${minDatasets - datasetCount} more file${
                                      minDatasets - datasetCount === 1 ? "" : "s"
                                  } — one file on its own becomes the holdout, leaving nothing to train on.`}
                        </span>
                    )}
                </div>
            </div>

            <div className="rounded-lg border border-rule bg-card p-4">
                <div className="mb-3 flex items-center justify-between gap-3">
                    <h2 className="text-sm">Runs</h2>
                    {/* Only when there is something it would remove — an
                        always-present button that deletes nothing is noise. */}
                    {finishedCount > 0 && (
                        <Button
                            size="sm"
                            variant="outline"
                            onClick={() => setConfirmingClearAll(true)}
                            disabled={deleteFinished.isPending}
                            loading={deleteFinished.isPending}
                        >
                            {!deleteFinished.isPending && (
                                <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                            )}
                            Clear finished
                        </Button>
                    )}
                </div>
                {isLoading ? (
                    <SkeletonRows rows={2} />
                ) : isError ? (
                    <div
                        className="gutter-rule flex items-start gap-2 rounded-md border border-fail/30 bg-fail-soft/60 px-3 py-2"
                        data-signal="fail"
                        role="alert"
                    >
                        <AlertCircle
                            className="mt-0.5 h-3.5 w-3.5 shrink-0 text-fail-ink"
                            aria-hidden="true"
                        />
                        <span className="text-xs text-foreground">
                            Could not load your training runs.
                        </span>
                    </div>
                ) : !runs || runs.length === 0 ? (
                    <p className="text-[0.8125rem] text-muted-foreground">
                        Nothing trained yet.
                    </p>
                ) : (
                    <ul className="flex flex-col gap-2">
                        {runs.map((run) => (
                            <RunRow key={run.id} run={run} onDelete={setPendingDelete} />
                        ))}
                    </ul>
                )}
            </div>

            <ConfirmModal
                open={confirming}
                title="Start a training run"
                description={
                    <>
                        This builds a training set from your uploads — one LLM call per
                        document, which costs money — and then occupies your Kaggle GPU
                        quota for the run. It cannot be cancelled from here.
                    </>
                }
                confirmLabel="Train now"
                isConfirming={startRun.isPending}
                onConfirm={handleStart}
                onCancel={() => setConfirming(false)}
            />

            <ConfirmModal
                open={pendingDelete !== null}
                destructive
                title="Delete training run"
                description={
                    <>
                        Delete this run&apos;s log
                        {pendingDelete?.has_model
                            ? " and the model it produced"
                            : ""}
                        ? {pendingDelete?.has_model
                            ? "Download the model first if you still want it — it is not kept anywhere else. "
                            : ""}
                        This can&apos;t be undone.
                    </>
                }
                confirmLabel="Delete"
                isConfirming={deleteRun.isPending}
                onConfirm={handleDeleteRun}
                onCancel={() => setPendingDelete(null)}
            />

            <ConfirmModal
                open={confirmingClearAll}
                destructive
                title="Clear finished runs"
                description={
                    <>
                        Delete{" "}
                        <span className="font-semibold text-foreground">
                            all {finishedCount} finished run
                            {finishedCount === 1 ? "" : "s"}
                        </span>
                        , their logs and any models they produced?
                        {modelsAtRisk > 0 && (
                            <>
                                {" "}
                                <span className="font-semibold text-foreground">
                                    {modelsAtRisk} trained model
                                    {modelsAtRisk === 1 ? "" : "s"}
                                </span>{" "}
                                will be deleted with them — download anything you want to
                                keep first.
                            </>
                        )}{" "}
                        Anything still training is left alone. This can&apos;t be undone.
                    </>
                }
                confirmLabel="Delete all"
                isConfirming={deleteFinished.isPending}
                onConfirm={handleClearFinished}
                onCancel={() => setConfirmingClearAll(false)}
            />
        </div>
    );
}

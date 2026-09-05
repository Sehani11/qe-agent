"use client";

import { useState } from "react";
import { AlertTriangle, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { useToast } from "@/components/ui/toast";
import { apiErrorMessage } from "@/lib/api/errors";
import { useFineTunedStatus } from "@/lib/hooks/useFineTunedStatus";
import { useEvaluationRuns } from "@/lib/hooks/useModelComparison";
import {
    useTrainingDatasets,
    useTrainingRuns,
    useWipeAllTrainingData,
} from "@/lib/hooks/useTrainingData";
import { isRunActive } from "@/lib/types/training";

/**
 * Start over: clear everything fine-tuning has produced, here and on Kaggle.
 *
 * Each of the three stores can already be cleared on its own, so this is not
 * a missing capability — it is the one intention ("start again") that the
 * three separate buttons make tedious and easy to half-finish. Clearing them
 * piecemeal leaves states that are not a fresh start: runs citing datasets
 * that are gone, evaluation rows scoring a model that no longer exists.
 *
 * It also goes where the per-store buttons cannot: the trained models on the
 * server, the dataset and kernels pushed to the account's Kaggle, and the
 * model loaded into the serving runtime. Those are what the runs actually
 * produced, so the confirmation names them — someone clearing rows should not
 * discover later that a copy of their corpus is still sitting in a Kaggle
 * dataset, or that the fine-tuned toggle still answers.
 *
 * Last on the page, in its own bordered zone, because it is the only control
 * here that destroys work rather than producing it.
 */
export default function WipeTrainingDataPanel() {
    const { data: datasets } = useTrainingDatasets();
    const { data: runs } = useTrainingRuns();
    const { data: evaluationRuns } = useEvaluationRuns();
    const { data: fineTuned } = useFineTunedStatus();
    const wipe = useWipeAllTrainingData();
    const toast = useToast();

    const [confirming, setConfirming] = useState(false);

    const datasetCount = datasets?.length ?? 0;
    const runCount = runs?.length ?? 0;
    const evaluationCount = evaluationRuns?.length ?? 0;
    const rowCount = datasetCount + runCount + evaluationCount;
    // A served model counts as something to wipe even with no rows left. It
    // outlives them — it lives in the serving runtime's own store, not in
    // anything this page lists — so gating on the rows alone would disable the
    // one control that can remove it, in exactly the state that matters: right
    // after a wipe that predates this, still answering the fine-tuned toggle.
    const hasSomethingToWipe = rowCount > 0 || !!fineTuned?.available;

    // The server refuses (409) while a run is going, for the same reason a
    // single run cannot be deleted: the row is not what the run is made of.
    const hasActiveRun = !!runs?.some((run) => isRunActive(run.status));

    const handleWipe = () => {
        wipe.mutate(undefined, {
            onSuccess: (counts) => {
                toast.success("Fine-tuning data wiped", {
                    description: `${counts.datasets} dataset${
                        counts.datasets === 1 ? "" : "s"
                    }, ${counts.runs} run${
                        counts.runs === 1 ? "" : "s"
                    }, ${counts.evaluation_rows} evaluation row${
                        counts.evaluation_rows === 1 ? "" : "s"
                    } and ${counts.kernels} Kaggle kernel${
                        counts.kernels === 1 ? "" : "s"
                    } are gone.${
                        counts.served_model
                            ? ` ${counts.served_model} was unloaded from the model runtime.`
                            : ""
                    }`,
                });
            },
            onError: (error) => {
                toast.error("Could not wipe the data", {
                    description: apiErrorMessage(
                        error,
                        "Nothing was deleted. Try again."
                    ),
                });
            },
            onSettled: () => setConfirming(false),
        });
    };

    return (
        <>
            <section className="flex flex-col gap-3 rounded-lg border border-fail/30 bg-fail-soft/40 p-4">
                <div className="flex items-center gap-2">
                    <AlertTriangle className="h-4 w-4 text-fail-ink" aria-hidden="true" />
                    <h2 className="text-sm text-fail-ink">Danger zone</h2>
                </div>
                <p className="text-xs leading-relaxed text-muted-foreground">
                    Deletes everything on this page — uploaded datasets, every training
                    run and its log, the models they produced, and all saved evaluation
                    results — plus the dataset and kernels pushed to Kaggle.
                </p>
                <div>
                    <Button
                        variant="destructive"
                        size="sm"
                        onClick={() => setConfirming(true)}
                        disabled={!hasSomethingToWipe || hasActiveRun || wipe.isPending}
                        loading={wipe.isPending}
                    >
                        {!wipe.isPending && (
                            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                        )}
                        Wipe all fine-tuning data
                    </Button>
                    {/* Two different reasons the button is unavailable, each
                        with a different resolution — waiting, or nothing to do. */}
                    {hasActiveRun && (
                        <p className="mt-2 text-xs text-muted-foreground">
                            A training run is in progress. Wait for it to finish first.
                        </p>
                    )}
                    {!hasActiveRun && !hasSomethingToWipe && (
                        <p className="mt-2 text-xs text-muted-foreground">
                            There is nothing to wipe.
                        </p>
                    )}
                </div>
            </section>

            <ConfirmModal
                open={confirming}
                destructive
                title="Wipe all fine-tuning data"
                description={
                    <>
                        This deletes, for your account:
                        {/* Itemised with counts rather than described in prose:
                            three different stores go at once, and the models in
                            particular exist nowhere else. */}
                        <ul className="mt-2 list-disc space-y-1 pl-4">
                            <li>
                                <span className="font-semibold text-foreground">
                                    {datasetCount}
                                </span>{" "}
                                uploaded dataset{datasetCount === 1 ? "" : "s"}
                            </li>
                            <li>
                                <span className="font-semibold text-foreground">
                                    {runCount}
                                </span>{" "}
                                training run{runCount === 1 ? "" : "s"}, their logs and
                                any trained models they produced
                            </li>
                            <li>
                                <span className="font-semibold text-foreground">
                                    {evaluationCount}
                                </span>{" "}
                                saved evaluation run
                                {evaluationCount === 1 ? "" : "s"}
                            </li>
                        </ul>
                        {fineTuned?.available && (
                            <p className="mt-2">
                                Currently served:{" "}
                                <span className="font-semibold text-foreground">
                                    {fineTuned.model}
                                </span>
                                . It is unloaded too — this is the copy that keeps
                                answering after the files are gone.
                            </p>
                        )}
                        <p className="mt-2">
                            Every trained model on this server goes with them, along with
                            the training dataset and kernels in your Kaggle account and
                            the model currently loaded for serving — the fine-tuned
                            toggle will have nothing to answer with afterwards. Download
                            any model you want to keep first: it is not stored anywhere
                            else. This can&apos;t be undone.
                        </p>
                    </>
                }
                confirmLabel="Wipe everything"
                isConfirming={wipe.isPending}
                onConfirm={handleWipe}
                onCancel={() => setConfirming(false)}
            />
        </>
    );
}

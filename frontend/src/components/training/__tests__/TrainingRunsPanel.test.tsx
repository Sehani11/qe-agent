/**
 * TrainingRunsPanel.test.tsx
 *
 * The "Train now" button and the run list it feeds.
 *
 * The assertions concentrate on the three separate reasons the button can be
 * unavailable — the server is not configured, a run is already going, nothing
 * has been uploaded — because each has a different fix and a bare disabled
 * button communicates none of them.
 */
import React from "react";
import { render, screen, fireEvent, within } from "@/test/test-utils";
import { describe, it, expect, vi, beforeEach } from "vitest";

import type { TrainingReadiness, TrainingRun } from "@/lib/types/training";
import TrainingRunsPanel from "@/components/training/TrainingRunsPanel";

const startMutate = vi.fn();
const downloadMutate = vi.fn();

let readinessState: { data: TrainingReadiness | undefined };
let runsState: {
    data: TrainingRun[] | undefined;
    isLoading: boolean;
    isError: boolean;
};
let startState: { mutate: typeof startMutate; isPending: boolean };

const deleteRunMutate = vi.fn();
const clearFinishedMutate = vi.fn();
const publishMutate = vi.fn();
const recheckMutate = vi.fn();
// Publishing is possible by default so the button is present in most tests;
// the blocked case sets this and asserts it disappears.
let servingState: { data: { can_serve: boolean; reason: string | null } } = {
    data: { can_serve: true, reason: null },
};

vi.mock("@/lib/hooks/useTrainingData", () => ({
    useTrainingReadiness: () => readinessState,
    useTrainingRuns: () => runsState,
    useStartTrainingRun: () => startState,
    useDownloadTrainedModel: () => ({ mutate: downloadMutate, isPending: false }),
    useDeleteTrainingRun: () => ({ mutate: deleteRunMutate, isPending: false }),
    useDeleteFinishedTrainingRuns: () => ({
        mutate: clearFinishedMutate,
        isPending: false,
    }),
    useServingReadiness: () => servingState,
    usePublishTrainingRun: () => ({ mutate: publishMutate, isPending: false }),
    useRecheckTrainingRun: () => ({ mutate: recheckMutate, isPending: false }),
}));

function makeRun(overrides: Partial<TrainingRun> = {}): TrainingRun {
    return {
        id: "run-1",
        status: "completed",
        detail: "Trained. The adapter is ready to download.",
        train_pairs: 182,
        holdout_pairs: 20,
        kernel_ref: "someone/bdd-fine-tune",
        has_model: true,
        serve_status: null,
        serve_detail: null,
        served_model: null,
        log: "$ build_dataset.py\nWrote 182 train / 20 holdout pairs\n",
        created_at: "2026-08-24T00:00:00Z",
        completed_at: "2026-08-24T01:00:00Z",
        ...overrides,
    };
}

const trainButton = () => screen.getByRole("button", { name: /train now/i });

beforeEach(() => {
    vi.clearAllMocks();
    readinessState = {
        data: {
            can_train: true,
            reason: null,
            active_run_id: null,
            min_datasets: 2,
        },
    };
    runsState = { data: [], isLoading: false, isError: false };
    startState = { mutate: startMutate, isPending: false };
    servingState = { data: { can_serve: true, reason: null } };
});

// ---------------------------------------------------------------------------
// Starting a run
// ---------------------------------------------------------------------------

describe("starting a run", () => {
    it("confirms before spending money and GPU quota", () => {
        render(<TrainingRunsPanel datasetCount={2} />);

        fireEvent.click(trainButton());

        expect(startMutate).not.toHaveBeenCalled();
        expect(screen.getByRole("dialog")).toBeInTheDocument();
        expect(screen.getByText(/start a training run/i)).toBeInTheDocument();
    });

    it("starts the run once confirmed", () => {
        render(<TrainingRunsPanel datasetCount={2} />);

        fireEvent.click(trainButton());
        const dialog = screen.getByRole("dialog");
        fireEvent.click(within(dialog).getByRole("button", { name: /train now/i }));

        expect(startMutate).toHaveBeenCalledTimes(1);
    });

    it("does not start when the confirmation is cancelled", () => {
        render(<TrainingRunsPanel datasetCount={2} />);

        fireEvent.click(trainButton());
        fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

        expect(startMutate).not.toHaveBeenCalled();
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("shows the server's own refusal instead of guessing at it", async () => {
        // Regression: this listed the two likely causes ("already training, or
        // Kaggle may not be configured") and was wrong whenever the real cause
        // was a third thing — which is exactly what happened on the first run.
        startMutate.mockImplementation(
            (_v: undefined, opts?: { onError?: (e: unknown) => void }) =>
                opts?.onError?.({
                    response: {
                        data: { message: "training_runs table does not exist." },
                    },
                })
        );
        render(<TrainingRunsPanel datasetCount={2} />);

        fireEvent.click(trainButton());
        const dialog = screen.getByRole("dialog");
        fireEvent.click(within(dialog).getByRole("button", { name: /train now/i }));

        expect(
            await screen.findByText(/training_runs table does not exist/i)
        ).toBeInTheDocument();
    });

    it("falls back to plain language when there is no server message to read", () => {
        startMutate.mockImplementation(
            (_v: undefined, opts?: { onError?: (e: unknown) => void }) =>
                opts?.onError?.(new Error("Network Error"))
        );
        render(<TrainingRunsPanel datasetCount={2} />);

        fireEvent.click(trainButton());
        const dialog = screen.getByRole("dialog");
        fireEvent.click(within(dialog).getByRole("button", { name: /train now/i }));

        expect(screen.getByText(/did not respond/i)).toBeInTheDocument();
    });

    it("explains a server that cannot train, rather than just disabling", () => {
        readinessState.data = {
            can_train: false,
            reason: "Kaggle credentials are not configured on the server.",
            active_run_id: null,
            min_datasets: 2,
        };
        render(<TrainingRunsPanel datasetCount={2} />);

        expect(trainButton()).toBeDisabled();
        expect(screen.getByText(/Kaggle credentials/i)).toBeInTheDocument();
    });

    it("says a run is already going, which is not a misconfiguration", () => {
        runsState.data = [makeRun({ status: "training", has_model: false })];
        render(<TrainingRunsPanel datasetCount={2} />);

        expect(trainButton()).toBeDisabled();
        expect(screen.getByText(/already in progress/i)).toBeInTheDocument();
    });

    it("says to upload something when there is nothing to train on", () => {
        render(<TrainingRunsPanel datasetCount={0} />);

        expect(trainButton()).toBeDisabled();
        expect(screen.getByText(/upload at least 2 files/i)).toBeInTheDocument();
    });

    it("refuses a single file, which can only ever become the holdout", () => {
        // Regression: this used to allow a run at one file. The builder splits
        // by origin FILE and always reserves one origin for the holdout, so a
        // lone file built for a minute and then failed with 0 train pairs.
        render(<TrainingRunsPanel datasetCount={1} />);

        expect(trainButton()).toBeDisabled();
        expect(screen.getByText(/upload 1 more file\b/i)).toBeInTheDocument();
        expect(screen.getByText(/nothing to train on/i)).toBeInTheDocument();
    });

    it("allows the run once the second file arrives", () => {
        render(<TrainingRunsPanel datasetCount={2} />);
        expect(trainButton()).toBeEnabled();
    });

    it("takes the threshold from the server, not from a hard-coded 2", () => {
        readinessState.data = {
            can_train: true,
            reason: null,
            active_run_id: null,
            min_datasets: 4,
        };
        render(<TrainingRunsPanel datasetCount={2} />);

        expect(trainButton()).toBeDisabled();
        expect(screen.getByText(/upload 2 more files/i)).toBeInTheDocument();
    });
});

// ---------------------------------------------------------------------------
// The run list
// ---------------------------------------------------------------------------

describe("the run list", () => {
    it("shows an empty state before anything has been trained", () => {
        render(<TrainingRunsPanel datasetCount={1} />);
        expect(screen.getByText(/nothing trained yet/i)).toBeInTheDocument();
    });

    it("reports the status, the detail and what it trained on", () => {
        runsState.data = [makeRun()];
        render(<TrainingRunsPanel datasetCount={1} />);

        expect(screen.getByText("Trained")).toBeInTheDocument();
        expect(screen.getByText(/adapter is ready to download/i)).toBeInTheDocument();
        expect(screen.getByText("182 train / 20 holdout")).toBeInTheDocument();
    });

    it("hides the pair count until the build has produced one", () => {
        // "0 pairs" on a queued run reads as a result rather than as "not yet".
        runsState.data = [
            makeRun({ status: "queued", train_pairs: 0, holdout_pairs: 0 }),
        ];
        render(<TrainingRunsPanel datasetCount={1} />);

        expect(screen.queryByText(/train \/ .* holdout/)).not.toBeInTheDocument();
    });

    it("links to the Kaggle kernel so a live run can be watched", () => {
        runsState.data = [makeRun({ status: "training", has_model: false })];
        render(<TrainingRunsPanel datasetCount={1} />);

        expect(screen.getByRole("link", { name: /kernel/i })).toHaveAttribute(
            "href",
            "https://www.kaggle.com/code/someone/bdd-fine-tune"
        );
    });

    it("shows an error note when the list cannot be loaded", () => {
        runsState = { data: undefined, isLoading: false, isError: true };
        render(<TrainingRunsPanel datasetCount={1} />);

        expect(screen.getByRole("alert")).toHaveTextContent(
            /could not load your training runs/i
        );
    });
});

// ---------------------------------------------------------------------------
// Downloading the model
// ---------------------------------------------------------------------------

describe("downloading the trained model", () => {
    it("offers the download only once a run has produced one", () => {
        runsState.data = [makeRun({ status: "training", has_model: false })];
        render(<TrainingRunsPanel datasetCount={1} />);

        expect(
            screen.queryByRole("button", { name: /download model/i })
        ).not.toBeInTheDocument();
    });

    it("downloads the adapter for the run it belongs to", () => {
        runsState.data = [makeRun({ id: "run-42" })];
        render(<TrainingRunsPanel datasetCount={1} />);

        fireEvent.click(screen.getByRole("button", { name: /download model/i }));

        expect(downloadMutate).toHaveBeenCalledTimes(1);
        expect(downloadMutate.mock.calls[0][0]).toBe("run-42");
    });
});

// ---------------------------------------------------------------------------
// The log
// ---------------------------------------------------------------------------

describe("the run log", () => {
    it("stays collapsed until asked for", () => {
        runsState.data = [makeRun()];
        render(<TrainingRunsPanel datasetCount={1} />);

        expect(screen.queryByText(/Wrote 182 train/)).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", { name: /show log/i }));

        expect(screen.getByText(/Wrote 182 train/)).toBeInTheDocument();
    });

    it("offers no toggle for a run that has printed nothing", () => {
        runsState.data = [makeRun({ status: "queued", log: "" })];
        render(<TrainingRunsPanel datasetCount={1} />);

        expect(
            screen.queryByRole("button", { name: /show log/i })
        ).not.toBeInTheDocument();
    });
});

// ---------------------------------------------------------------------------
// Deleting runs and their logs
// ---------------------------------------------------------------------------

describe("deleting a run", () => {
    const deleteButton = () => screen.getByRole("button", { name: /delete run from/i });

    it("confirms before deleting, naming the model that goes with it", () => {
        runsState.data = [makeRun({ has_model: true })];
        render(<TrainingRunsPanel datasetCount={2} />);

        fireEvent.click(deleteButton());

        expect(deleteRunMutate).not.toHaveBeenCalled();
        const dialog = screen.getByRole("dialog");
        // The adapter lives nowhere else, so the warning has to be explicit.
        expect(within(dialog).getByText(/the model it produced/i)).toBeInTheDocument();
        expect(within(dialog).getByText(/download the model first/i)).toBeInTheDocument();
    });

    it("does not mention a model when the run never produced one", () => {
        runsState.data = [makeRun({ status: "failed", has_model: false })];
        render(<TrainingRunsPanel datasetCount={2} />);

        fireEvent.click(deleteButton());

        const dialog = screen.getByRole("dialog");
        expect(within(dialog).queryByText(/download the model first/i)).toBeNull();
    });

    it("deletes the run it was opened for, once confirmed", () => {
        runsState.data = [makeRun({ id: "run-77" })];
        render(<TrainingRunsPanel datasetCount={2} />);

        fireEvent.click(deleteButton());
        const dialog = screen.getByRole("dialog");
        fireEvent.click(within(dialog).getByRole("button", { name: /^delete$/i }));

        expect(deleteRunMutate).toHaveBeenCalledTimes(1);
        expect(deleteRunMutate.mock.calls[0][0]).toBe("run-77");
    });

    it("does not delete when the confirmation is cancelled", () => {
        runsState.data = [makeRun()];
        render(<TrainingRunsPanel datasetCount={2} />);

        fireEvent.click(deleteButton());
        fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

        expect(deleteRunMutate).not.toHaveBeenCalled();
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("offers no delete while a run is still going", () => {
        // Absent rather than disabled: deleting the row would not stop the
        // subprocess or the Kaggle kernel, so the button would mean nothing.
        runsState.data = [makeRun({ status: "training", has_model: false })];
        render(<TrainingRunsPanel datasetCount={2} />);

        expect(screen.queryByRole("button", { name: /delete run from/i })).toBeNull();
    });
});

describe("clearing finished runs", () => {
    const clearButton = () => screen.getByRole("button", { name: /clear finished/i });

    it("is offered only when something would actually be removed", () => {
        runsState.data = [makeRun({ status: "training", has_model: false })];
        render(<TrainingRunsPanel datasetCount={2} />);

        expect(screen.queryByRole("button", { name: /clear finished/i })).toBeNull();
    });

    it("counts only the finished runs, and the models they hold", () => {
        runsState.data = [
            makeRun({ id: "a", has_model: true }),
            makeRun({ id: "b", status: "failed", has_model: false }),
            makeRun({ id: "c", status: "training", has_model: false }),
        ];
        render(<TrainingRunsPanel datasetCount={2} />);

        fireEvent.click(clearButton());

        const dialog = screen.getByRole("dialog");
        expect(within(dialog).getByText(/all 2 finished runs/i)).toBeInTheDocument();
        expect(within(dialog).getByText(/1 trained model/i)).toBeInTheDocument();
        expect(within(dialog).getByText(/still training is left alone/i)).toBeInTheDocument();
    });

    it("clears them once confirmed", () => {
        runsState.data = [makeRun(), makeRun({ id: "b" })];
        render(<TrainingRunsPanel datasetCount={2} />);

        fireEvent.click(clearButton());
        const dialog = screen.getByRole("dialog");
        fireEvent.click(within(dialog).getByRole("button", { name: /delete all/i }));

        expect(clearFinishedMutate).toHaveBeenCalledTimes(1);
    });
});


// ---------------------------------------------------------------------------
// Publishing a run to the local model runtime
// ---------------------------------------------------------------------------

describe("publishing a run", () => {
    const serveButton = () =>
        screen.getByRole("button", { name: /serve this run/i });

    it("offers the button on a run that produced an adapter", () => {
        runsState.data = [makeRun()];
        render(<TrainingRunsPanel datasetCount={2} />);

        expect(serveButton()).toBeInTheDocument();
    });

    it("publishes the run that was clicked", () => {
        runsState.data = [makeRun({ id: "run-7" })];
        render(<TrainingRunsPanel datasetCount={2} />);

        fireEvent.click(serveButton());

        expect(publishMutate).toHaveBeenCalledTimes(1);
        expect(publishMutate.mock.calls[0][0]).toBe("run-7");
    });

    it("says nothing about publishing on a run with no adapter", () => {
        // A failed run has no model to serve, so the button would be a lie.
        runsState.data = [makeRun({ status: "failed", has_model: false })];
        render(<TrainingRunsPanel datasetCount={2} />);

        expect(
            screen.queryByRole("button", { name: /serve this run/i })
        ).not.toBeInTheDocument();
    });

    it("hides the button when the server cannot publish at all", () => {
        // Hidden rather than disabled: the blocker is server setup nobody can
        // fix from this row, so a per-row control has nothing to offer.
        servingState = {
            data: { can_serve: false, reason: "The GGUF converter is not available." },
        };
        runsState.data = [makeRun()];
        render(<TrainingRunsPanel datasetCount={2} />);

        expect(
            screen.queryByRole("button", { name: /serve this run/i })
        ).not.toBeInTheDocument();
        // Downloading the adapter still works — it needs none of that toolchain.
        expect(
            screen.getByRole("button", { name: /download model/i })
        ).toBeInTheDocument();
    });

    it("shows progress in the row, which outlives the click", () => {
        runsState.data = [
            makeRun({
                serve_status: "publishing",
                serve_detail: "Converting the adapter for qwen2.5:1.5b-instruct…",
            }),
        ];
        render(<TrainingRunsPanel datasetCount={2} />);

        expect(
            screen.getByText(/converting the adapter for qwen2\.5/i)
        ).toBeInTheDocument();
    });

    it("keeps the button out of reach while a publish is running", () => {
        runsState.data = [makeRun({ serve_status: "publishing", serve_detail: "Queued" })];
        render(<TrainingRunsPanel datasetCount={2} />);

        expect(serveButton()).toBeDisabled();
    });

    it("says which run the app is serving once one succeeds", () => {
        runsState.data = [
            makeRun({
                serve_status: "served",
                served_model: "bdd-lora-1.5b",
                serve_detail: "Serving bdd-lora-1.5b from this run.",
            }),
        ];
        render(<TrainingRunsPanel datasetCount={2} />);

        expect(screen.getByText(/serving bdd-lora-1\.5b from this run/i)).toBeInTheDocument();
        // Offered again, because re-publishing after a retrain is the normal case.
        expect(screen.getByRole("button", { name: /re-serve/i })).toBeInTheDocument();
    });

    it("surfaces a publish failure without touching the run's own status", () => {
        // A run that trained fine and failed to publish is a real state, and
        // the training badge must keep saying the training went well.
        runsState.data = [
            makeRun({
                serve_status: "failed",
                serve_detail: "The base model qwen2.5:1.5b-instruct is not in the runtime.",
            }),
        ];
        render(<TrainingRunsPanel datasetCount={2} />);

        expect(
            screen.getByText(/base model qwen2\.5:1\.5b-instruct is not in the runtime/i)
        ).toBeInTheDocument();
        expect(screen.getByText("Trained")).toBeInTheDocument();
    });
});


// ---------------------------------------------------------------------------
// What the confirmation warns about
// ---------------------------------------------------------------------------
//
// The cost sentence is the reason this confirmation exists, and it is only true
// for .feature uploads: those carry Gherkin with no acceptance criteria, so the
// builder pays an LLM to write the criteria backwards. Pairs (.jsonl, and the
// .csv that converts to it) arrive complete and skip that entirely. Warning
// about money on a run that spends none is how people learn to click past the
// warning on the run that does.

describe("the cost warning", () => {
    it("names the LLM cost when feature files will be back-generated", () => {
        render(<TrainingRunsPanel datasetCount={3} featureCount={2} />);

        fireEvent.click(trainButton());

        const dialog = screen.getByRole("dialog");
        expect(within(dialog).getByText(/2 feature files/i)).toBeInTheDocument();
        expect(within(dialog).getByText(/costs money/i)).toBeInTheDocument();
    });

    it("says no LLM call is needed when every upload is already a pair", () => {
        render(<TrainingRunsPanel datasetCount={2} featureCount={0} />);

        fireEvent.click(trainButton());

        const dialog = screen.getByRole("dialog");
        expect(within(dialog).getByText(/no llm calls are needed/i)).toBeInTheDocument();
        expect(within(dialog).queryByText(/costs money/i)).not.toBeInTheDocument();
    });

    it("still warns about the quota and the one-way door either way", () => {
        render(<TrainingRunsPanel datasetCount={2} featureCount={0} />);

        fireEvent.click(trainButton());

        const dialog = screen.getByRole("dialog");
        expect(within(dialog).getByText(/kaggle gpu quota/i)).toBeInTheDocument();
        expect(within(dialog).getByText(/cannot be cancelled/i)).toBeInTheDocument();
    });

    it("counts a single feature file in the singular", () => {
        render(<TrainingRunsPanel datasetCount={2} featureCount={1} />);

        fireEvent.click(trainButton());

        const dialog = screen.getByRole("dialog");
        expect(within(dialog).getByText(/1 feature file[^s]/i)).toBeInTheDocument();
    });
});


// ---------------------------------------------------------------------------
// Recovering a run whose watcher died
// ---------------------------------------------------------------------------
//
// The watcher is the fragile part of a long run: it polls for hours, so a DNS
// blip or the wall-clock timeout ends it while the GPU kernel carries on. The
// row reads failed for work that is still running, and the only button on
// offer must not be "Train now" — that pushes over the very kernel being
// recovered.

describe("rechecking a dropped run", () => {
    const recheckButton = () => screen.getByRole("button", { name: /check kaggle/i });

    it("offers the recovery on a failed run that reached Kaggle", () => {
        runsState.data = [
            makeRun({ status: "failed", has_model: false, detail: "gaierror" }),
        ];
        render(<TrainingRunsPanel datasetCount={2} />);

        expect(recheckButton()).toBeInTheDocument();
    });

    it("rechecks the run that was clicked", () => {
        runsState.data = [
            makeRun({ id: "run-9", status: "failed", has_model: false }),
        ];
        render(<TrainingRunsPanel datasetCount={2} />);

        fireEvent.click(recheckButton());

        expect(recheckMutate).toHaveBeenCalledTimes(1);
        expect(recheckMutate.mock.calls[0][0]).toBe("run-9");
    });

    it("does not offer it on a run that never reached Kaggle", () => {
        // No kernel means nothing to re-attach to; the fix is a new run.
        runsState.data = [
            makeRun({ status: "failed", has_model: false, kernel_ref: null }),
        ];
        render(<TrainingRunsPanel datasetCount={2} />);

        expect(
            screen.queryByRole("button", { name: /check kaggle/i })
        ).not.toBeInTheDocument();
    });

    it("does not offer it on a run that already collected its adapter", () => {
        runsState.data = [makeRun({ status: "completed", has_model: true })];
        render(<TrainingRunsPanel datasetCount={2} />);

        expect(
            screen.queryByRole("button", { name: /check kaggle/i })
        ).not.toBeInTheDocument();
    });

    it("does not offer it while the run is still being watched", () => {
        runsState.data = [makeRun({ status: "training", has_model: false })];
        render(<TrainingRunsPanel datasetCount={2} />);

        expect(
            screen.queryByRole("button", { name: /check kaggle/i })
        ).not.toBeInTheDocument();
    });
});

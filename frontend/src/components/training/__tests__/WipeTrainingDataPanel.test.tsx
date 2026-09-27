/**
 * WipeTrainingDataPanel.test.tsx
 *
 * The start-over button. It destroys three stores at once and the trained
 * models exist nowhere else, so the assertions concentrate on what the
 * confirmation actually tells the user before they commit to it.
 */
import React from "react";
import { render, screen, fireEvent, within } from "@/test/test-utils";
import { describe, it, expect, vi, beforeEach } from "vitest";

import type { TrainingDataset, TrainingRun } from "@/lib/types/training";
import WipeTrainingDataPanel from "@/components/training/WipeTrainingDataPanel";

const wipeMutate = vi.fn();

let datasetsState: { data: TrainingDataset[] | undefined };
let runsState: { data: TrainingRun[] | undefined };
let evaluationState: { data: { run_id: string }[] | undefined };

vi.mock("@/lib/hooks/useTrainingData", () => ({
    useTrainingDatasets: () => datasetsState,
    useTrainingRuns: () => runsState,
    useWipeAllTrainingData: () => ({ mutate: wipeMutate, isPending: false }),
}));

vi.mock("@/lib/hooks/useModelComparison", () => ({
    useEvaluationRuns: () => evaluationState,
}));

let fineTunedState: {
    data: { available: boolean; model: string | null; detail: string } | undefined;
};

vi.mock("@/lib/hooks/useFineTunedStatus", () => ({
    useFineTunedStatus: () => fineTunedState,
}));

function makeRun(overrides: Partial<TrainingRun> = {}): TrainingRun {
    return {
        id: "run-1",
        status: "completed",
        detail: null,
        train_pairs: 10,
        holdout_pairs: 2,
        kernel_ref: null,
        has_model: true,
        serve_status: null,
        serve_detail: null,
        served_model: null,
        log: "",
        created_at: "2026-08-24T00:00:00Z",
        completed_at: "2026-08-24T01:00:00Z",
        ...overrides,
    };
}

function makeDataset(id = "ds-1"): TrainingDataset {
    return {
        id,
        filename: "pairs.jsonl",
        kind: "jsonl",
        item_count: 12,
        storage_path: `u/training-data/${id}/pairs.jsonl`,
        created_at: "2026-08-24T00:00:00Z",
    };
}

const wipeButton = () =>
    screen.getByRole("button", { name: /wipe all fine-tuning data/i });

beforeEach(() => {
    vi.clearAllMocks();
    datasetsState = { data: [makeDataset()] };
    runsState = { data: [makeRun()] };
    evaluationState = { data: [{ run_id: "run-a" }] };
    fineTunedState = {
        data: {
            available: true,
            model: "bdd-lora-1.5b",
            detail: "Serving bdd-lora-1.5b.",
        },
    };
});

describe("the start-over button", () => {
    it("itemises every store it would empty, with counts", () => {
        datasetsState = { data: [makeDataset("a"), makeDataset("b")] };
        render(<WipeTrainingDataPanel />);

        fireEvent.click(wipeButton());

        const dialog = screen.getByRole("dialog");
        expect(within(dialog).getByText("2")).toBeInTheDocument();
        expect(within(dialog).getByText(/uploaded datasets/i)).toBeInTheDocument();
        expect(within(dialog).getByText(/training run/i)).toBeInTheDocument();
        expect(within(dialog).getByText(/saved evaluation run/i)).toBeInTheDocument();
        // The models exist nowhere else, so this warning has to be present.
        expect(within(dialog).getByText(/download any model/i)).toBeInTheDocument();
        expect(wipeMutate).not.toHaveBeenCalled();
    });

    it("warns that the Kaggle dataset and kernels go too", () => {
        // The one consequence a user cannot see from this page: a copy of the
        // corpus lives in their own Kaggle account, and the wipe deletes it.
        render(<WipeTrainingDataPanel />);

        fireEvent.click(wipeButton());

        const dialog = screen.getByRole("dialog");
        expect(within(dialog).getByText(/kaggle account/i)).toBeInTheDocument();
    });

    it("stays available with no rows left while a model is still served", () => {
        // The state an earlier wipe leaves behind: nothing listed on the page,
        // but the served copy still answering the fine-tuned toggle. Gating on
        // the rows alone would disable the one control that can remove it.
        datasetsState = { data: [] };
        runsState = { data: [] };
        evaluationState = { data: [] };
        render(<WipeTrainingDataPanel />);

        expect(wipeButton()).toBeEnabled();

        fireEvent.click(wipeButton());
        const dialog = screen.getByRole("dialog");
        expect(within(dialog).getByText("bdd-lora-1.5b")).toBeInTheDocument();
    });

    it("wipes only once confirmed", () => {
        render(<WipeTrainingDataPanel />);

        fireEvent.click(wipeButton());
        const dialog = screen.getByRole("dialog");
        fireEvent.click(within(dialog).getByRole("button", { name: /wipe everything/i }));

        expect(wipeMutate).toHaveBeenCalledTimes(1);
    });

    it("does not wipe when the confirmation is cancelled", () => {
        render(<WipeTrainingDataPanel />);

        fireEvent.click(wipeButton());
        fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

        expect(wipeMutate).not.toHaveBeenCalled();
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("is unavailable while a run is in progress, and says why", () => {
        // The server refuses this too — the row is not what a run is made of.
        runsState = { data: [makeRun({ status: "training", has_model: false })] };
        render(<WipeTrainingDataPanel />);

        expect(wipeButton()).toBeDisabled();
        expect(screen.getByText(/wait for it to finish/i)).toBeInTheDocument();
    });

    it("is unavailable when there is nothing to wipe", () => {
        datasetsState = { data: [] };
        runsState = { data: [] };
        evaluationState = { data: [] };
        // The served model has to be gone too, not just the rows — it is one
        // of the things this button removes.
        fineTunedState = {
            data: { available: false, model: null, detail: "No model." },
        };
        render(<WipeTrainingDataPanel />);

        expect(wipeButton()).toBeDisabled();
        expect(screen.getByText(/nothing to wipe/i)).toBeInTheDocument();
    });

    it("stays available when only one of the three stores has anything in it", () => {
        datasetsState = { data: [] };
        runsState = { data: [] };
        evaluationState = { data: [{ run_id: "run-a" }] };
        render(<WipeTrainingDataPanel />);

        expect(wipeButton()).toBeEnabled();
    });

    it("names the stores it clears, so the blast radius is knowable up front", () => {
        render(<WipeTrainingDataPanel />);

        expect(
            screen.getByText(/uploaded datasets, every training run/i)
        ).toBeInTheDocument();
    });
});

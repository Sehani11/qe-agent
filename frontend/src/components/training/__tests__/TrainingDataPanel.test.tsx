/**
 * TrainingDataPanel.test.tsx
 *
 * Unit tests for Story 6.7 — Manual Training Dataset Upload.
 * Covers AC1 (upload + per-file outcomes), AC3 (line-numbered rejection shown
 * verbatim), AC4 (list render, empty state, delete behind confirmation).
 */
import React from "react";
import { render, screen, fireEvent } from '@/test/test-utils';
import { describe, it, expect, vi, beforeEach } from "vitest";
import type {
    TrainingDataset,
    TrainingUploadResponse,
} from "@/lib/types/training";
import TrainingDataPanel from "@/components/training/TrainingDataPanel";

// ---------------------------------------------------------------------------
// Mutable mock state for the useTrainingData hooks
// ---------------------------------------------------------------------------

const uploadMutate = vi.fn();
const uploadReset = vi.fn();
const deleteMutate = vi.fn();

let uploadState: {
    mutate: typeof uploadMutate;
    reset: typeof uploadReset;
    isPending: boolean;
    isError: boolean;
    data: TrainingUploadResponse | undefined;
};

let listState: {
    data: TrainingDataset[] | undefined;
    isLoading: boolean;
    isError: boolean;
};

let deleteState: {
    mutate: typeof deleteMutate;
    isPending: boolean;
    variables: string | undefined;
};

const downloadSampleMutate = vi.fn();

vi.mock("@/lib/hooks/useTrainingData", () => ({
    useTrainingDatasets: () => listState,
    useUploadTrainingDatasets: () => uploadState,
    useDeleteTrainingDataset: () => deleteState,
    useDownloadSampleDataset: () => ({
        mutate: downloadSampleMutate,
        isPending: false,
    }),
}));

function makeDataset(overrides: Partial<TrainingDataset> = {}): TrainingDataset {
    return {
        id: "ds-1",
        filename: "reset.feature",
        kind: "feature",
        item_count: 3,
        storage_path: "user-a/training-data/ds-1/reset.feature",
        created_at: "2026-08-08T00:00:00Z",
        ...overrides,
    };
}

function makeFile(name: string, size = 100): File {
    const file = new File(["Feature: x"], name, { type: "text/plain" });
    Object.defineProperty(file, "size", { value: size });
    return file;
}

function selectFiles(files: File[]) {
    const input = screen.getByLabelText("Training data files");
    fireEvent.change(input, { target: { files } });
}

beforeEach(() => {
    vi.clearAllMocks();
    uploadState = {
        mutate: uploadMutate,
        reset: uploadReset,
        isPending: false,
        isError: false,
        data: undefined,
    };
    listState = { data: [], isLoading: false, isError: false };
    deleteState = { mutate: deleteMutate, isPending: false, variables: undefined };
});

// ---------------------------------------------------------------------------
// Upload (AC1)
// ---------------------------------------------------------------------------

describe("upload", () => {
    it("sends the selected files to the upload mutation", () => {
        render(<TrainingDataPanel />);
        const files = [makeFile("a.feature"), makeFile("b.jsonl")];
        selectFiles(files);

        fireEvent.click(screen.getByRole("button", { name: "Upload" }));

        expect(uploadMutate).toHaveBeenCalledTimes(1);
        expect(uploadMutate.mock.calls[0][0].map((f: File) => f.name)).toEqual([
            "a.feature",
            "b.jsonl",
        ]);
    });

    it("blocks submission when no file is chosen", () => {
        render(<TrainingDataPanel />);

        fireEvent.click(screen.getByRole("button", { name: "Upload" }));

        expect(uploadMutate).not.toHaveBeenCalled();
        expect(
            screen.getByText(/choose at least one .feature or .jsonl file/i)
        ).toBeInTheDocument();
    });

    it("rejects an unsupported extension before hitting the network", () => {
        render(<TrainingDataPanel />);
        selectFiles([makeFile("notes.txt")]);

        fireEvent.click(screen.getByRole("button", { name: "Upload" }));

        expect(uploadMutate).not.toHaveBeenCalled();
        expect(screen.getByText(/only .feature and .jsonl/i)).toBeInTheDocument();
    });

    it("rejects a file over the size limit before hitting the network", () => {
        render(<TrainingDataPanel />);
        selectFiles([makeFile("huge.jsonl", 11 * 1024 * 1024)]);

        fireEvent.click(screen.getByRole("button", { name: "Upload" }));

        expect(uploadMutate).not.toHaveBeenCalled();
        expect(screen.getByText(/maximum size is 10 MB/i)).toBeInTheDocument();
    });

    it("shows a loader while the upload is in flight", () => {
        uploadState.isPending = true;
        render(<TrainingDataPanel />);

        expect(screen.getByText("Uploading…")).toBeInTheDocument();
    });

    it("clears the selection after a successful upload so it cannot be re-sent", () => {
        // Duplicate uploads create duplicate rows, objects AND training pairs.
        uploadMutate.mockImplementation(
            (_files: File[], opts?: { onSuccess?: (r: TrainingUploadResponse) => void }) =>
                opts?.onSuccess?.({ accepted: [makeDataset()], rejected: [] })
        );
        render(<TrainingDataPanel />);
        selectFiles([makeFile("a.feature")]);

        fireEvent.click(screen.getByRole("button", { name: "Upload" }));
        expect(uploadMutate).toHaveBeenCalledTimes(1);

        // A second click has nothing selected to send.
        fireEvent.click(screen.getByRole("button", { name: "Upload" }));
        expect(uploadMutate).toHaveBeenCalledTimes(1);
        expect(
            screen.getByText(/choose at least one .feature or .jsonl file/i)
        ).toBeInTheDocument();
    });

    it("keeps the selection when every file was rejected", () => {
        uploadMutate.mockImplementation(
            (_files: File[], opts?: { onSuccess?: (r: TrainingUploadResponse) => void }) =>
                opts?.onSuccess?.({
                    accepted: [],
                    rejected: [{ filename: "a.feature", reason: "No scenarios found." }],
                })
        );
        render(<TrainingDataPanel />);
        selectFiles([makeFile("a.feature")]);

        fireEvent.click(screen.getByRole("button", { name: "Upload" }));
        fireEvent.click(screen.getByRole("button", { name: "Upload" }));

        expect(uploadMutate).toHaveBeenCalledTimes(2);
    });

    it("reports accepted and rejected files from one batch", () => {
        uploadState.data = {
            accepted: [makeDataset({ filename: "good.feature", item_count: 2 })],
            rejected: [
                { filename: "pairs.jsonl", reason: "Line 3: not valid JSON." },
            ],
        };
        render(<TrainingDataPanel />);

        expect(screen.getByText(/good.feature \(2\)/)).toBeInTheDocument();
        // AC3: the backend's reason, including the line number, shown verbatim.
        expect(
            screen.getByText("pairs.jsonl: Line 3: not valid JSON.")
        ).toBeInTheDocument();
    });
});

// ---------------------------------------------------------------------------
// List (AC4)
// ---------------------------------------------------------------------------

describe("uploaded datasets list", () => {
    it("renders filename, kind, count and date", () => {
        listState.data = [
            makeDataset(),
            makeDataset({
                id: "ds-2",
                filename: "pairs.jsonl",
                kind: "jsonl",
                item_count: 1,
            }),
        ];
        render(<TrainingDataPanel />);

        expect(screen.getByText("reset.feature")).toBeInTheDocument();
        expect(screen.getByText("3 scenarios")).toBeInTheDocument();
        expect(screen.getByText("pairs.jsonl")).toBeInTheDocument();
        expect(screen.getByText("1 pair")).toBeInTheDocument();
    });

    it("shows an empty state when nothing has been uploaded", () => {
        listState.data = [];
        render(<TrainingDataPanel />);

        expect(screen.getByText("Nothing uploaded yet.")).toBeInTheDocument();
    });

    it("shows a loader while the list is loading", () => {
        listState = { data: undefined, isLoading: true, isError: false };
        render(<TrainingDataPanel />);

        // The panel renders SkeletonRows while loading; it announces itself
        // through role="status"/aria-label rather than visible copy.
        expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
    });

    it("shows an error note when the list fails to load", () => {
        listState = { data: undefined, isLoading: false, isError: true };
        render(<TrainingDataPanel />);

        expect(
            screen.getByText("Could not load uploaded datasets.")
        ).toBeInTheDocument();
    });
});

// ---------------------------------------------------------------------------
// Delete (AC4)
// ---------------------------------------------------------------------------

describe("delete", () => {
    it("asks for confirmation before deleting", () => {
        listState.data = [makeDataset()];
        render(<TrainingDataPanel />);

        fireEvent.click(screen.getByRole("button", { name: "Delete reset.feature" }));

        expect(deleteMutate).not.toHaveBeenCalled();
        expect(screen.getByText("Delete training dataset")).toBeInTheDocument();
    });

    it("deletes the dataset once confirmed", () => {
        listState.data = [makeDataset()];
        render(<TrainingDataPanel />);

        fireEvent.click(screen.getByRole("button", { name: "Delete reset.feature" }));
        fireEvent.click(screen.getByRole("button", { name: "Delete" }));

        expect(deleteMutate).toHaveBeenCalledTimes(1);
        expect(deleteMutate.mock.calls[0][0]).toBe("ds-1");
    });

    it("does not delete when the confirmation is cancelled", () => {
        listState.data = [makeDataset()];
        render(<TrainingDataPanel />);

        fireEvent.click(screen.getByRole("button", { name: "Delete reset.feature" }));
        fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

        expect(deleteMutate).not.toHaveBeenCalled();
        expect(screen.queryByText("Delete training dataset")).not.toBeInTheDocument();
    });
});

// ---------------------------------------------------------------------------
// Sample downloads
// ---------------------------------------------------------------------------

describe("sample datasets", () => {
    it("offers a sample in each accepted format", () => {
        render(<TrainingDataPanel />);

        expect(
            screen.getByRole("button", { name: /sample\.jsonl/i })
        ).toBeInTheDocument();
        expect(
            screen.getByRole("button", { name: /sample\.feature/i })
        ).toBeInTheDocument();
    });

    it("asks the server for the format that was clicked", () => {
        render(<TrainingDataPanel />);

        fireEvent.click(screen.getByRole("button", { name: /sample\.feature/i }));

        expect(downloadSampleMutate).toHaveBeenCalledTimes(1);
        expect(downloadSampleMutate.mock.calls[0][0]).toBe("feature");
    });

    it("defaults to the pairs format, which is the one worth showing", () => {
        // A .jsonl pair is the shape nobody guesses right: the assistant side
        // has to be a JSON object encoded as a STRING.
        render(<TrainingDataPanel />);

        fireEvent.click(screen.getByRole("button", { name: /sample\.jsonl/i }));

        expect(downloadSampleMutate.mock.calls[0][0]).toBe("jsonl");
    });
});

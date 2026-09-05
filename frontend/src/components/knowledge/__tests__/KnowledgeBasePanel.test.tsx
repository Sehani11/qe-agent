/**
 * KnowledgeBasePanel.test.tsx
 *
 * Unit tests for Story 4.5 — Knowledge Base Ingestion UI.
 * Covers AC2 (progress), AC3 (error), AC4 (sources list + empty), AC5 (validation).
 */
import React from "react";
import { render, screen, fireEvent, act, within } from '@/test/test-utils';
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { KnowledgeSource, KnowledgeSSEEvent } from "@/lib/types/knowledge";
import KnowledgeBasePanel from "@/components/knowledge/KnowledgeBasePanel";

// ---------------------------------------------------------------------------
// Mutable mock state for the useKnowledge hooks
// ---------------------------------------------------------------------------

const confluenceIngest = vi.fn();
const jiraIngest = vi.fn();
const docIngest = vi.fn();
const docReset = vi.fn();
const refetch = vi.fn();
const deleteMutate = vi.fn();
const deleteAllMutate = vi.fn();

interface IngestOptions {
    // The second argument carries whatever else the stream reported: code
    // indexing sends files, chunks and the commit it built at, none of which
    // fit a count.
    onComplete?: (count: number, event: KnowledgeSSEEvent) => void;
    onError?: (message: string) => void;
}

interface DocResult {
    ingested_count: number;
    chunk_count: number;
    title: string;
}
let docState: {
    ingest: typeof docIngest;
    isUploading: boolean;
    error: string | null;
    result: DocResult | null;
    reset: typeof docReset;
};

let confluenceState: {
    ingest: typeof confluenceIngest;
    isIngesting: boolean;
    error: string | null;
    progressMessage: string | null;
};
let jiraState: typeof confluenceState;
let sourcesState: {
    data: KnowledgeSource[] | undefined;
    isLoading: boolean;
    isError: boolean;
    refetch: typeof refetch;
};
// Capture the options the component passes so tests can fire onComplete/onError.
let confluenceOptions: IngestOptions | undefined;
let jiraOptions: IngestOptions | undefined;

const codeIndexIngest = vi.fn();
let codeIndexState: {
    ingest: typeof codeIndexIngest;
    isIngesting: boolean;
    error: string | null;
    progressMessage: string | null;
};
let codeIndexStatusState: {
    data:
        | {
              indexed: boolean;
              repo: string | null;
              indexed_ref: string | null;
              file_count: number;
              indexed_at: string | null;
          }
        | undefined;
};
let codeIndexOptions: IngestOptions | undefined;

vi.mock("@/lib/hooks/useKnowledge", () => ({
    useIngestConfluence: (opts?: IngestOptions) => {
        confluenceOptions = opts;
        return confluenceState;
    },
    useIngestJira: (opts?: IngestOptions) => {
        jiraOptions = opts;
        return jiraState;
    },
    useIngestDocument: () => docState,
    useKnowledgeSources: () => sourcesState,
    useDeleteKnowledgeSource: () => ({
        mutate: deleteMutate,
        isPending: false,
        variables: undefined,
    }),
    useDeleteAllKnowledgeSources: () => ({
        mutate: deleteAllMutate,
        isPending: false,
    }),
    useCodeIndexStatus: () => codeIndexStatusState,
    useIndexCode: (opts?: IngestOptions) => {
        codeIndexOptions = opts;
        return codeIndexState;
    },
}));

// useProjects and projectStore are deliberately NOT mocked. The code-index
// card reads them to seed its repository field, but so does the ModelProvider
// the shared render wrapper mounts — and a whole-module mock listing only the
// export this panel needs takes that provider's away with it.

function reset() {
    vi.clearAllMocks();
    confluenceState = {
        ingest: confluenceIngest,
        isIngesting: false,
        error: null,
        progressMessage: null,
    };
    jiraState = {
        ingest: jiraIngest,
        isIngesting: false,
        error: null,
        progressMessage: null,
    };
    sourcesState = { data: [], isLoading: false, isError: false, refetch };
    docState = {
        ingest: docIngest,
        isUploading: false,
        error: null,
        result: null,
        reset: docReset,
    };
    codeIndexState = {
        ingest: codeIndexIngest,
        isIngesting: false,
        error: null,
        progressMessage: null,
    };
    // Default: never indexed, which is what a new project looks like.
    codeIndexStatusState = { data: undefined };
}

function selectFile(name: string, type: string) {
    const input = screen.getByLabelText(/document file/i);
    const file = new File(["dummy content"], name, { type });
    fireEvent.change(input, { target: { files: [file] } });
    return file;
}

function makeSource(overrides: Partial<KnowledgeSource> = {}): KnowledgeSource {
    return {
        id: "s-1",
        user_id: "u-1",
        source_type: "confluence",
        source_url: "https://wiki.example.com/pages/1",
        title: "Auth Design",
        page_count: 1,
        ingestion_status: "completed",
        created_at: "2026-07-04T00:00:00Z",
        ...overrides,
    };
}

describe("KnowledgeBasePanel", () => {
    beforeEach(reset);

    // ---- AC5: validation ------------------------------------------------

    it("blocks Confluence submit with no space key (AC5)", () => {
        render(<KnowledgeBasePanel />);
        fireEvent.click(screen.getByRole("button", { name: /ingest confluence/i }));
        expect(confluenceIngest).not.toHaveBeenCalled();
        expect(screen.getByText(/provide a space key/i)).toBeInTheDocument();
    });

    it("submits Confluence when a space key is provided (AC5)", () => {
        render(<KnowledgeBasePanel />);
        fireEvent.change(screen.getByLabelText(/space key/i), { target: { value: "ENG" } });
        fireEvent.click(screen.getByRole("button", { name: /ingest confluence/i }));
        expect(confluenceIngest).toHaveBeenCalledWith({ space_key: "ENG", page_id: undefined });
    });

    it("blocks Jira submit for an invalid project key (AC5)", () => {
        render(<KnowledgeBasePanel />);
        fireEvent.change(screen.getByLabelText(/project key/i), {
            target: { value: "not a key!" },
        });
        fireEvent.click(screen.getByRole("button", { name: /ingest jira/i }));
        expect(jiraIngest).not.toHaveBeenCalled();
        expect(screen.getByText(/must be 1–10 uppercase/i)).toBeInTheDocument();
    });

    it("uppercases and submits a valid Jira project key (AC5)", () => {
        render(<KnowledgeBasePanel />);
        fireEvent.change(screen.getByLabelText(/project key/i), { target: { value: "proj" } });
        fireEvent.click(screen.getByRole("button", { name: /ingest jira/i }));
        expect(jiraIngest).toHaveBeenCalledWith({
            project_key: "PROJ",
            sprint: undefined,
            label: undefined,
        });
    });

    // ---- AC2: progress --------------------------------------------------

    it("renders the progress message while ingesting (AC2)", () => {
        confluenceState = {
            ingest: confluenceIngest,
            isIngesting: true,
            error: null,
            progressMessage: "Processing: Architecture Overview",
        };
        render(<KnowledgeBasePanel />);
        expect(screen.getByText(/processing: architecture overview/i)).toBeInTheDocument();
    });

    // ---- AC3: error -----------------------------------------------------

    it("renders an error alert when a hook reports an error (AC3)", () => {
        jiraState = {
            ingest: jiraIngest,
            isIngesting: false,
            error: "Jira fetch failed. Please try again.",
            progressMessage: null,
        };
        render(<KnowledgeBasePanel />);
        expect(screen.getByText(/jira fetch failed/i)).toBeInTheDocument();
    });

    // ---- AC4: sources list + empty state --------------------------------

    it("shows the empty state when there are no sources (AC4)", () => {
        render(<KnowledgeBasePanel />);
        expect(screen.getByText(/no knowledge sources ingested yet/i)).toBeInTheDocument();
    });

    it("renders ingested sources with a link when a URL is present (AC4)", () => {
        sourcesState = {
            data: [makeSource({ title: "Auth Design" })],
            isLoading: false,
            isError: false,
            refetch,
        };
        render(<KnowledgeBasePanel />);
        const link = screen.getByRole("link", { name: /auth design/i });
        expect(link).toHaveAttribute("href", "https://wiki.example.com/pages/1");
        expect(link).toHaveAttribute("target", "_blank");
    });

    it("renders a source without a URL as plain text (AC4)", () => {
        sourcesState = {
            data: [makeSource({ source_type: "jira", title: "PROJ-1 summary", source_url: null })],
            isLoading: false,
            isError: false,
            refetch,
        };
        render(<KnowledgeBasePanel />);
        expect(screen.queryByRole("link")).not.toBeInTheDocument();
        expect(screen.getByText("PROJ-1 summary")).toBeInTheDocument();
    });

    it("renders the page count for each source (AC4)", () => {
        sourcesState = {
            data: [makeSource({ page_count: 3 })],
            isLoading: false,
            isError: false,
            refetch,
        };
        render(<KnowledgeBasePanel />);
        expect(screen.getByText(/3 pages/i)).toBeInTheDocument();
    });

    // ---- Story 4.7: delete a source (via confirmation modal) ------------

    it("opens the confirmation modal and deletes on confirm (Story 4.7)", () => {
        sourcesState = {
            data: [makeSource({ id: "s-42", title: "Auth Design" })],
            isLoading: false,
            isError: false,
            refetch,
        };
        render(<KnowledgeBasePanel />);

        // Row trash button opens the modal (does NOT delete yet)
        fireEvent.click(screen.getByRole("button", { name: /delete auth design/i }));
        expect(deleteMutate).not.toHaveBeenCalled();
        expect(screen.getByText(/delete knowledge source/i)).toBeInTheDocument();

        // Confirm in the modal triggers the delete
        fireEvent.click(screen.getByRole("button", { name: "Delete" }));
        expect(deleteMutate).toHaveBeenCalledTimes(1);
        expect(deleteMutate.mock.calls[0][0]).toBe("s-42");
    });

    it("does not delete when the modal is cancelled (Story 4.7)", () => {
        sourcesState = {
            data: [makeSource({ id: "s-42", title: "Auth Design" })],
            isLoading: false,
            isError: false,
            refetch,
        };
        render(<KnowledgeBasePanel />);

        fireEvent.click(screen.getByRole("button", { name: /delete auth design/i }));
        fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

        expect(deleteMutate).not.toHaveBeenCalled();
        expect(screen.queryByText(/delete knowledge source/i)).not.toBeInTheDocument();
    });

    // ---- AC2 / AC4: onComplete success path -----------------------------

    it("shows the ingested count and refetches the list on completion (AC2, AC4)", () => {
        render(<KnowledgeBasePanel />);
        act(() => {
            confluenceOptions?.onComplete?.(5, {
                type: "complete",
                ingested_count: 5,
            });
        });
        expect(screen.getByText(/ingested 5 items/i)).toBeInTheDocument();
        expect(refetch).toHaveBeenCalled();
    });

    // ---- AC6: degraded (nothing ingested) note --------------------------

    it("shows the informational degraded note when nothing was ingested (AC6)", () => {
        render(<KnowledgeBasePanel />);
        act(() => {
            jiraOptions?.onComplete?.(0, {
                type: "complete",
                ingested_count: 0,
            });
        });
        // The same path also raises a toast titled "Nothing was ingested", so
        // this matches the inline note's full sentence to stay unambiguous.
        expect(
            screen.getByText(/nothing was ingested\. check that pinecone/i)
        ).toBeInTheDocument();
    });

    // ---- Story 4.6: document upload ------------------------------------

    it("blocks upload of an unsupported file type (AC5)", () => {
        render(<KnowledgeBasePanel />);
        selectFile("notes.txt", "text/plain");
        fireEvent.click(screen.getByRole("button", { name: /upload document/i }));
        expect(docIngest).not.toHaveBeenCalled();
        expect(screen.getByText(/only \.pdf and \.docx/i)).toBeInTheDocument();
    });

    it("blocks upload when no file is chosen (AC5)", () => {
        render(<KnowledgeBasePanel />);
        fireEvent.click(screen.getByRole("button", { name: /upload document/i }));
        expect(docIngest).not.toHaveBeenCalled();
        expect(screen.getByText(/choose a \.pdf or \.docx/i)).toBeInTheDocument();
    });

    it("uploads a valid .pdf file (AC5)", () => {
        render(<KnowledgeBasePanel />);
        const file = selectFile("design.pdf", "application/pdf");
        fireEvent.click(screen.getByRole("button", { name: /upload document/i }));
        expect(docIngest).toHaveBeenCalledWith(file);
    });

    it("renders the chunk count on a successful upload (AC4)", () => {
        docState = {
            ingest: docIngest,
            isUploading: false,
            error: null,
            result: { ingested_count: 1, chunk_count: 7, title: "design.pdf" },
            reset: docReset,
        };
        render(<KnowledgeBasePanel />);
        expect(screen.getByText(/ingested 7 chunks from design\.pdf/i)).toBeInTheDocument();
    });

    it("renders an error alert when the upload fails (AC5)", () => {
        docState = {
            ingest: docIngest,
            isUploading: false,
            error: "No extractable text found.",
            result: null,
            reset: docReset,
        };
        render(<KnowledgeBasePanel />);
        expect(screen.getByText(/no extractable text found/i)).toBeInTheDocument();
    });

    // ---- Delete all (bulk clear) ---------------------------------------

    it("hides Delete all when there is nothing to delete", () => {
        sourcesState = { data: [], isLoading: false, isError: false, refetch };
        render(<KnowledgeBasePanel />);

        expect(
            screen.queryByRole("button", { name: /delete all/i })
        ).not.toBeInTheDocument();
    });

    it("clears every source after confirming Delete all", () => {
        sourcesState = {
            data: [makeSource(), makeSource({ id: "src-2", title: "PROJ-1 summary" })],
            isLoading: false,
            isError: false,
            refetch,
        };
        render(<KnowledgeBasePanel />);

        fireEvent.click(screen.getByRole("button", { name: /delete all/i }));

        // The confirmation names the count — "all" on its own is not a quantity.
        expect(screen.getByText(/delete all knowledge sources/i)).toBeInTheDocument();
        expect(screen.getByText(/all 2 sources/i)).toBeInTheDocument();

        // Scoped to the dialog: the trigger shares this name on purpose, since
        // an action keeps its name from control through to confirmation.
        fireEvent.click(
            within(screen.getByRole("dialog")).getByRole("button", { name: "Delete all" })
        );

        expect(deleteAllMutate).toHaveBeenCalledTimes(1);
    });

    it("does not clear anything when Delete all is cancelled", () => {
        sourcesState = {
            data: [makeSource()],
            isLoading: false,
            isError: false,
            refetch,
        };
        render(<KnowledgeBasePanel />);

        fireEvent.click(screen.getByRole("button", { name: /delete all/i }));
        fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

        expect(deleteAllMutate).not.toHaveBeenCalled();
        expect(
            screen.queryByText(/delete all knowledge sources/i)
        ).not.toBeInTheDocument();
    });


    // ---- Ingest by URL (method selector) --------------------------------

    it("ingests Confluence pages from pasted URLs and IDs", () => {
        render(<KnowledgeBasePanel />);

        fireEvent.click(screen.getByRole("tab", { name: "Page URLs" }));
        fireEvent.change(screen.getByLabelText(/page urls or ids/i), {
            target: {
                value:
                    "https://acme.atlassian.net/wiki/spaces/ENG/pages/123456/Auth\n\n789\n",
            },
        });
        fireEvent.click(screen.getByRole("button", { name: /ingest confluence/i }));

        // Blank lines dropped; the raw refs go to the backend, which owns the
        // URL parsing so both ends cannot drift on what a page ref looks like.
        expect(confluenceIngest).toHaveBeenCalledWith({
            page_refs: [
                "https://acme.atlassian.net/wiki/spaces/ENG/pages/123456/Auth",
                "789",
            ],
        });
    });

    it("blocks Confluence URL submit when nothing was pasted", () => {
        render(<KnowledgeBasePanel />);

        fireEvent.click(screen.getByRole("tab", { name: "Page URLs" }));
        fireEvent.click(screen.getByRole("button", { name: /ingest confluence/i }));

        expect(confluenceIngest).not.toHaveBeenCalled();
        expect(screen.getByText(/add at least one page url or page id/i)).toBeInTheDocument();
    });

    it("ingests Jira tickets from pasted URLs and keys", () => {
        render(<KnowledgeBasePanel />);

        fireEvent.click(screen.getByRole("tab", { name: "Ticket URLs" }));
        fireEvent.change(screen.getByLabelText(/ticket urls or keys/i), {
            target: { value: "https://acme.atlassian.net/browse/PROJ-1, PROJ-2" },
        });
        fireEvent.click(screen.getByRole("button", { name: /ingest jira/i }));

        // Commas split too — pasting a comma-separated list is common.
        expect(jiraIngest).toHaveBeenCalledWith({
            ticket_refs: ["https://acme.atlassian.net/browse/PROJ-1", "PROJ-2"],
        });
    });

    it("blocks Jira URL submit when nothing was pasted", () => {
        render(<KnowledgeBasePanel />);

        fireEvent.click(screen.getByRole("tab", { name: "Ticket URLs" }));
        fireEvent.click(screen.getByRole("button", { name: /ingest jira/i }));

        expect(jiraIngest).not.toHaveBeenCalled();
        expect(screen.getByText(/add at least one ticket url or key/i)).toBeInTheDocument();
    });

    it("keeps the project-key method as the Jira default", () => {
        render(<KnowledgeBasePanel />);

        expect(
            screen.getByRole("tab", { name: "Whole project" })
        ).toHaveAttribute("aria-selected", "true");
        expect(screen.getByLabelText(/project key/i)).toBeInTheDocument();
    });

});

// ---------------------------------------------------------------------------
// The two ingestion cards sit side by side, so their buttons must line up
// ---------------------------------------------------------------------------

describe("ingest button alignment", () => {
    it("pins both submit buttons to the bottom of their card", () => {
        // Confluence and Jira are grid items and stretch to equal height, but
        // Jira carries an extra sprint/label row. Without mt-auto the shorter
        // card leaves its slack BELOW the button, so the two buttons sit at
        // different heights.
        render(<KnowledgeBasePanel />);

        for (const name of [/ingest confluence/i, /ingest jira/i]) {
            const button = screen.getByRole("button", { name });
            expect(button.className, `${name} is not bottom-pinned`).toContain(
                "mt-auto"
            );
        }
    });

    it("keeps each button as the last child before any status note", () => {
        // mt-auto only reaches the bottom while the button is in the card's
        // own flex column — nesting it in a wrapper would silently stop it.
        render(<KnowledgeBasePanel />);

        const button = screen.getByRole("button", { name: /ingest confluence/i });
        expect(button.parentElement?.tagName).toBe("FORM");
        expect(button.parentElement?.className).toContain("flex-col");
    });
});

// ---------------------------------------------------------------------------
// Code index — the semantic index verification uses to find candidate files
// ---------------------------------------------------------------------------

describe("code index card", () => {
    beforeEach(reset);

    it("says so when the project has never been indexed", () => {
        render(<KnowledgeBasePanel />);

        expect(screen.getByTestId("code-index-status")).toHaveTextContent(
            /not indexed yet/i
        );
        expect(
            screen.getByRole("button", { name: /^index code$/i })
        ).toBeInTheDocument();
    });

    it("reports the commit and date it was built at", () => {
        // A branch name moves, so only the SHA distinguishes a fresh index from
        // a month-old one — which is the whole reason it is recorded.
        codeIndexStatusState = {
            data: {
                indexed: true,
                repo: "org/repo",
                indexed_ref: "abc1234def567",
                file_count: 120,
                indexed_at: "2026-08-01T00:00:00Z",
            },
        };
        render(<KnowledgeBasePanel />);

        const status = screen.getByTestId("code-index-status");
        expect(status).toHaveTextContent("org/repo");
        expect(status).toHaveTextContent("abc1234");
        expect(status).toHaveTextContent("120 files");
        // Re-index rather than Index, so the action reads as a refresh.
        expect(
            screen.getByRole("button", { name: /re-index code/i })
        ).toBeInTheDocument();
    });

    it("indexes the repository that was typed", () => {
        render(<KnowledgeBasePanel />);

        fireEvent.change(screen.getByLabelText(/repository/i), {
            target: { value: "acme/app" },
        });
        fireEvent.change(screen.getByLabelText(/branch or commit/i), {
            target: { value: "develop" },
        });
        fireEvent.click(screen.getByRole("button", { name: /index code/i }));

        expect(codeIndexIngest).toHaveBeenCalledWith({
            repo_url: "acme/app",
            ref: "develop",
        });
    });

    it("defaults the ref to HEAD when none is given", () => {
        render(<KnowledgeBasePanel />);

        fireEvent.change(screen.getByLabelText(/repository/i), {
            target: { value: "acme/app" },
        });
        fireEvent.click(screen.getByRole("button", { name: /index code/i }));

        expect(codeIndexIngest).toHaveBeenCalledWith({
            repo_url: "acme/app",
            ref: "HEAD",
        });
    });

    it("refuses to index with no repository to index", () => {
        render(<KnowledgeBasePanel />);

        fireEvent.click(screen.getByRole("button", { name: /index code/i }));

        expect(codeIndexIngest).not.toHaveBeenCalled();
        expect(screen.getByText(/enter a repository/i)).toBeInTheDocument();
    });

    it("reports what was built when the stream completes", () => {
        render(<KnowledgeBasePanel />);

        act(() => {
            codeIndexOptions?.onComplete?.(0, {
                type: "complete",
                indexed_files: 120,
                chunks: 4200,
                sha: "abc1234def567",
            });
        });

        expect(screen.getByText(/120 files \(4200 chunks\)/i)).toBeInTheDocument();
        expect(screen.getByText(/abc1234/)).toBeInTheDocument();
    });

    it("surfaces an indexing error instead of a silent no-op", () => {
        codeIndexState = {
            ingest: codeIndexIngest,
            isIngesting: false,
            error: "Could not resolve 'nope' in org/repo.",
            progressMessage: null,
        };
        render(<KnowledgeBasePanel />);

        expect(screen.getByText(/could not resolve/i)).toBeInTheDocument();
    });
});

/**
 * VerificationResultsPanel.test.tsx
 *
 * Unit tests for Story 2.4 — Verification Results Display
 * Covers AC 1–7: summary bar, per-scenario rows, auto-expand, suggestion callout,
 * GitHub links, mobile overlay, and empty-state guard.
 */
import React from "react";
import { render, screen, fireEvent } from '@/test/test-utils';
import { describe, it, expect, vi, beforeEach } from "vitest";
import VerificationResultsPanel from "@/components/pipeline/VerificationResultsPanel";
import type { VerificationVerdict, VerificationSummary } from "@/lib/types/verification";

// ---------------------------------------------------------------------------
// Mock SessionContext
// ---------------------------------------------------------------------------

let mockVerificationResults: VerificationVerdict[] = [];
let mockVerificationSummary: VerificationSummary | null = null;
let mockVerificationMode: string | null = null;
let mockGithubInput = "";

vi.mock("@/context/SessionContext", () => ({
    useSessionContext: () => ({
        sessionId: "session-123",
        verificationResults: mockVerificationResults,
        verificationSummary: mockVerificationSummary,
        isVerifying: false,
        bddContent: "",
        // The panel echoes the source the run was scoped to; in the app this is
        // restored from the stored rows when a past session is reopened.
        verificationMode: mockVerificationMode,
        githubInput: mockGithubInput,
    }),
}));

// Story 5.4: stub the export mutation hooks (they use TanStack Query internally,
// which would otherwise require a QueryClientProvider in these unit tests).
const mockExportPdf = vi.fn();
const mockExportCsv = vi.fn();

vi.mock("@/lib/hooks/useVerification", () => ({
    useExportReportPdf: () => ({ mutate: mockExportPdf, isPending: false, error: null }),
    useExportReportCsv: () => ({ mutate: mockExportCsv, isPending: false, error: null }),
}));

// ---------------------------------------------------------------------------
// Test data helpers
// ---------------------------------------------------------------------------

function makeVerdict(overrides: Partial<VerificationVerdict> = {}): VerificationVerdict {
    return {
        scenario_id: "sc-1",
        scenario_title: "User can log in",
        status: "pass",
        justification: "The auth handler at routes.py:42 validates credentials correctly.",
        code_reference: { file: "src/auth/routes.py", function: "login", line: 42 },
        github_links: ["https://github.com/org/repo/blob/main/src/auth/routes.py#L42"],
        implementation_suggestion: null,
        ...overrides,
    };
}

function makeSummary(overrides: Partial<VerificationSummary> = {}): VerificationSummary {
    return { total: 2, passed: 1, failed: 1, ...overrides };
}

function resetMockState() {
    mockVerificationResults = [];
    mockVerificationSummary = null;
    mockVerificationMode = null;
    mockGithubInput = "";
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("VerificationResultsPanel", () => {
    beforeEach(() => {
        resetMockState();
        vi.clearAllMocks();
    });

    // ---- AC guard: renders nothing when empty ---------------------------

    it("renders nothing when verificationResults is empty and verificationSummary is null", () => {
        render(<VerificationResultsPanel />);
        // The shared render mounts ToastProvider's notifications region, so the
        // panel's absence is asserted directly rather than via an empty container.
        expect(screen.queryByRole("region", { name: /verification/i })).not.toBeInTheDocument();
        expect(screen.queryByText(/scenario/i)).not.toBeInTheDocument();
    });

    // ---- AC 4: Summary bar ---------------------------------------------

    it("renders summary bar with correct pass percentage when verificationSummary is provided", () => {
        mockVerificationResults = [makeVerdict()];
        mockVerificationSummary = makeSummary({ total: 4, passed: 3, failed: 1 });

        render(<VerificationResultsPanel />);

        expect(screen.getByText("75%")).toBeInTheDocument();
        expect(screen.getByText(/3 passed/i)).toBeInTheDocument();
        expect(screen.getByText(/1 failed/i)).toBeInTheDocument();
        expect(screen.getByText(/4 total/i)).toBeInTheDocument();
    });

    it("shows 100% pass rate summary with green accent class", () => {
        mockVerificationResults = [makeVerdict({ status: "pass" })];
        mockVerificationSummary = makeSummary({ total: 1, passed: 1, failed: 0 });

        render(<VerificationResultsPanel />);

        expect(screen.getByText("100%")).toBeInTheDocument();
        // Summary container carries the pass signal styling
        const summaryEl = screen.getByText("100%").closest("div");
        expect(summaryEl?.className).toMatch(/pass/);
    });

    it("shows 0% pass rate summary with red accent class", () => {
        mockVerificationResults = [makeVerdict({ status: "fail", implementation_suggestion: "Fix it." })];
        mockVerificationSummary = makeSummary({ total: 1, passed: 0, failed: 1 });

        render(<VerificationResultsPanel />);

        expect(screen.getByText("0%")).toBeInTheDocument();
        const summaryEl = screen.getByText("0%").closest("div");
        expect(summaryEl?.className).toMatch(/fail/);
    });

    // ---- AC 1: One row per verdict (AC 1) --------------------------------

    it("renders one VerificationResultRow per verdict in verificationResults", () => {
        mockVerificationResults = [
            makeVerdict({ scenario_id: "sc-1", scenario_title: "Login works" }),
            makeVerdict({ scenario_id: "sc-2", scenario_title: "Logout works" }),
        ];

        render(<VerificationResultsPanel />);

        expect(screen.getByText("Login works")).toBeInTheDocument();
        expect(screen.getByText("Logout works")).toBeInTheDocument();
    });

    // ---- AC 2: Failed verdict auto-expands and shows suggestion ----------

    it("auto-expands failed verdict row and shows implementation_suggestion (AC 2)", () => {
        mockVerificationResults = [
            makeVerdict({
                scenario_id: "sc-fail",
                scenario_title: "Payment fails gracefully",
                status: "fail",
                implementation_suggestion: "Add error boundary around payment handler.",
            }),
        ];

        render(<VerificationResultsPanel />);

        // The suggestion callout should be immediately visible (auto-expanded)
        expect(screen.getByText("Add error boundary around payment handler.")).toBeInTheDocument();
    });

    it("does NOT show implementation_suggestion for passed verdict rows (AC 2)", () => {
        mockVerificationResults = [
            makeVerdict({
                scenario_id: "sc-pass",
                scenario_title: "Login works",
                status: "pass",
                implementation_suggestion: null,
            }),
        ];

        render(<VerificationResultsPanel />);

        // Passed row is collapsed by default — expand it first
        fireEvent.click(screen.getByRole("button", { name: /login works/i }));

        // No suggestion callout should be present
        expect(screen.queryByText(/implementation suggestion/i)).not.toBeInTheDocument();
    });

    // ---- AC 1: Code reference pill content ------------------------------

    it("renders code_reference pill with file, function, and line when row is expanded (AC 1)", () => {
        mockVerificationResults = [
            makeVerdict({
                scenario_id: "sc-1",
                scenario_title: "User can log in",
                status: "pass",
                code_reference: { file: "src/auth/routes.py", function: "login", line: 42 },
            }),
        ];

        render(<VerificationResultsPanel />);

        // Expand the row
        fireEvent.click(screen.getByRole("button", { name: /user can log in/i }));

        // Code pill should contain file, function name, and line number
        // The span renders: "src/auth/routes.py › login:42"
        const pill = screen.getByText(/src\/auth\/routes\.py.+login.+42/);
        expect(pill).toBeInTheDocument();
        expect(pill.tagName).toBe("SPAN");
    });

    // ---- AC 3: GitHub links render as anchor tags with target="_blank" ---

    it("renders GitHub links as anchor tags with target='_blank' (AC 3)", () => {
        mockVerificationResults = [
            makeVerdict({
                scenario_id: "sc-1",
                status: "pass",
                github_links: [
                    "https://github.com/org/repo/blob/main/src/auth.py#L10",
                ],
            }),
        ];

        render(<VerificationResultsPanel />);

        // Expand the row to see links
        fireEvent.click(screen.getByRole("button", { name: /user can log in/i }));

        const links = screen.getAllByRole("link", { name: /view on github/i });
        expect(links.length).toBeGreaterThan(0);
        links.forEach((link) => {
            expect(link).toHaveAttribute("target", "_blank");
            expect(link).toHaveAttribute("href", "https://github.com/org/repo/blob/main/src/auth.py#L10");
        });
    });

    // ---- AC 6: Mobile overlay is rendered in DOM ------------------------

    // Skipped (Story 4.4 review M2): the mobile read-only overlay this asserted
    // was removed from VerificationResultsPanel in a later refactor, so the text
    // no longer exists. Kept as a documented skip rather than a false-green delete;
    // restore/remove when the mobile overlay decision is revisited.
    it.skip("renders mobile read-only overlay element (AC 6)", () => {
        mockVerificationResults = [makeVerdict()];

        render(<VerificationResultsPanel />);

        // The overlay is always present in DOM; CSS (md:hidden) controls visibility
        expect(screen.getByText(/results are read-only on mobile/i)).toBeInTheDocument();
    });

    // ---- Verified-against source ----------------------------------------

    it("shows the source the verdicts were checked against", () => {
        mockVerificationResults = [makeVerdict()];
        mockVerificationSummary = makeSummary();
        mockVerificationMode = "full_repo";
        mockGithubInput = "https://github.com/org/repo";

        render(<VerificationResultsPanel />);

        expect(screen.getByText(/verified against/i)).toBeInTheDocument();
        const link = screen.getByRole("link", { name: "https://github.com/org/repo" });
        expect(link).toHaveAttribute("href", "https://github.com/org/repo");
        expect(link).toHaveAttribute("target", "_blank");
    });

    it("lists every file URL when the run used exact-files mode", () => {
        mockVerificationResults = [makeVerdict()];
        mockVerificationSummary = makeSummary();
        mockVerificationMode = "exact_files";
        mockGithubInput =
            "https://github.com/org/repo/blob/main/a.py\nhttps://github.com/org/repo/blob/main/b.py";

        render(<VerificationResultsPanel />);

        expect(
            screen.getByRole("link", { name: /a\.py$/ })
        ).toBeInTheDocument();
        expect(
            screen.getByRole("link", { name: /b\.py$/ })
        ).toBeInTheDocument();
    });

    it("omits the source block when no source was recorded (older runs)", () => {
        mockVerificationResults = [makeVerdict()];
        mockVerificationSummary = makeSummary();
        mockGithubInput = "";

        render(<VerificationResultsPanel />);

        expect(screen.queryByText(/verified against/i)).not.toBeInTheDocument();
    });

    // ---- AC 7: No RAG section -------------------------------------------

    it("does not render any RAG context section (AC 7)", () => {
        mockVerificationResults = [makeVerdict()];
        mockVerificationSummary = makeSummary();

        render(<VerificationResultsPanel />);

        expect(screen.queryByText(/rag/i)).not.toBeInTheDocument();
        expect(screen.queryByText(/knowledge base/i)).not.toBeInTheDocument();
    });

    // ---- Renders panel when summary available but no results yet ---------

    it("renders panel (with summary bar only) when summary is present but results list is empty", () => {
        mockVerificationResults = [];
        mockVerificationSummary = makeSummary({ total: 3, passed: 3, failed: 0 });

        render(<VerificationResultsPanel />);

        expect(screen.getByText("100%")).toBeInTheDocument();
    });

    // ---- Story 5.4: Report export buttons -------------------------------

    it("disables export buttons when there are no verification results", () => {
        mockVerificationResults = [];
        mockVerificationSummary = makeSummary({ total: 3, passed: 3, failed: 0 });

        render(<VerificationResultsPanel />);

        expect(screen.getByRole("button", { name: /download pdf/i })).toBeDisabled();
        expect(screen.getByRole("button", { name: /download csv/i })).toBeDisabled();
    });

    it("enables export buttons and triggers export mutations when results exist", () => {
        mockVerificationResults = [makeVerdict()];
        mockVerificationSummary = makeSummary();

        render(<VerificationResultsPanel />);

        const pdfBtn = screen.getByRole("button", { name: /download pdf/i });
        const csvBtn = screen.getByRole("button", { name: /download csv/i });
        expect(pdfBtn).toBeEnabled();
        expect(csvBtn).toBeEnabled();

        fireEvent.click(pdfBtn);
        expect(mockExportPdf).toHaveBeenCalledWith("session-123");

        fireEvent.click(csvBtn);
        expect(mockExportCsv).toHaveBeenCalledWith("session-123");
    });
});

/**
 * VerificationResultsPanel.test.tsx
 *
 * Unit tests for Story 2.4 — Verification Results Display
 * Covers AC 1–7: summary bar, per-scenario rows, auto-expand, suggestion callout,
 * GitHub links, mobile overlay, and empty-state guard.
 */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import VerificationResultsPanel from "@/components/pipeline/VerificationResultsPanel";
import type { VerificationVerdict, VerificationSummary } from "@/lib/types/verification";

// ---------------------------------------------------------------------------
// Mock SessionContext
// ---------------------------------------------------------------------------

let mockVerificationResults: VerificationVerdict[] = [];
let mockVerificationSummary: VerificationSummary | null = null;

vi.mock("@/context/SessionContext", () => ({
    useSessionContext: () => ({
        verificationResults: mockVerificationResults,
        verificationSummary: mockVerificationSummary,
    }),
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
        const { container } = render(<VerificationResultsPanel />);
        expect(container.firstChild).toBeNull();
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
        // Summary container should have emerald styling
        const summaryEl = screen.getByText("100%").closest("div");
        expect(summaryEl?.className).toMatch(/emerald/);
    });

    it("shows 0% pass rate summary with red accent class", () => {
        mockVerificationResults = [makeVerdict({ status: "fail", implementation_suggestion: "Fix it." })];
        mockVerificationSummary = makeSummary({ total: 1, passed: 0, failed: 1 });

        render(<VerificationResultsPanel />);

        expect(screen.getByText("0%")).toBeInTheDocument();
        const summaryEl = screen.getByText("0%").closest("div");
        expect(summaryEl?.className).toMatch(/rose/);
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

    it("renders mobile read-only overlay element (AC 6)", () => {
        mockVerificationResults = [makeVerdict()];

        render(<VerificationResultsPanel />);

        // The overlay is always present in DOM; CSS (md:hidden) controls visibility
        expect(screen.getByText(/results are read-only on mobile/i)).toBeInTheDocument();
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
});

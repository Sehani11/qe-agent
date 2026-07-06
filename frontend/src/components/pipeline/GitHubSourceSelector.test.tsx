/**
 * GitHubSourceSelector.test.tsx
 *
 * Unit tests for Story 2.1 — GitHub Source Selection UI
 * Covers AC 1-7: mode switching, input rendering, Verify button disabled logic,
 * SessionContext state updates, and mobile read-only guard.
 */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import GitHubSourceSelector from "@/components/pipeline/GitHubSourceSelector";
import type { VerificationMode } from "@/lib/types/verification";

// ---------------------------------------------------------------------------
// Mock SessionContext so we can control and observe the context state
// ---------------------------------------------------------------------------

let mockVerificationMode: VerificationMode | null = null;
let mockGithubInput = "";
const mockSetVerificationMode = vi.fn((mode: VerificationMode | null) => {
    mockVerificationMode = mode;
});
const mockSetGithubInput = vi.fn((value: string) => {
    mockGithubInput = value;
});

vi.mock("@/context/SessionContext", () => ({
    useSessionContext: () => ({
        verificationMode: mockVerificationMode,
        setVerificationMode: mockSetVerificationMode,
        githubInput: mockGithubInput,
        setGithubInput: mockSetGithubInput,
    }),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderSelector(overrides: Partial<{ onVerify: () => void; isVerifying: boolean }> = {}) {
    const onVerify = overrides.onVerify ?? vi.fn();
    const isVerifying = overrides.isVerifying ?? false;
    return render(<GitHubSourceSelector onVerify={onVerify} isVerifying={isVerifying} />);
}

function resetMockState() {
    mockVerificationMode = null;
    mockGithubInput = "";
    mockSetVerificationMode.mockClear();
    mockSetGithubInput.mockClear();
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("GitHubSourceSelector", () => {
    beforeEach(() => {
        resetMockState();
    });

    // ---- AC 1: Mode selector renders with three options ------------------

    it("renders all three mode buttons (AC 1)", () => {
        renderSelector();
        expect(screen.getByRole("tab", { name: /exact file paths/i })).toBeInTheDocument();
        expect(screen.getByRole("tab", { name: /full repository/i })).toBeInTheDocument();
        expect(screen.getByRole("tab", { name: /pull request/i })).toBeInTheDocument();
    });

    it("shows helper text when no mode is selected (AC 1)", () => {
        renderSelector();
        expect(screen.getByText(/select a verification mode above/i)).toBeInTheDocument();
    });

    // ---- AC 2: Exact File Paths → textarea ------------------------------

    it("shows a textarea when 'Exact File Paths' mode is active (AC 2)", () => {
        mockVerificationMode = "exact_files";
        renderSelector();
        const textarea = screen.getByRole("textbox");
        expect(textarea.tagName).toBe("TEXTAREA");
        expect(textarea).toHaveAttribute("placeholder", "One file path per line, e.g. src/auth/routes.py");
    });

    // ---- AC 3: Full Repository → single input ---------------------------

    it("shows a text input when 'Full Repository' mode is active (AC 3)", () => {
        mockVerificationMode = "full_repo";
        renderSelector();
        const input = screen.getByRole("textbox");
        expect(input.tagName).toBe("INPUT");
        expect(input).toHaveAttribute("placeholder", "https://github.com/org/repo");
    });

    // ---- AC 4: Pull Request → single input ------------------------------

    it("shows a text input when 'Pull Request' mode is active (AC 4)", () => {
        mockVerificationMode = "pull_request";
        renderSelector();
        const input = screen.getByRole("textbox");
        expect(input.tagName).toBe("INPUT");
        expect(input).toHaveAttribute("placeholder", "https://github.com/org/repo/pull/42");
    });

    // ---- AC 5: Mode and input stored in SessionContext ------------------

    it("calls setVerificationMode with the clicked mode value (AC 5)", () => {
        renderSelector();
        fireEvent.click(screen.getByRole("tab", { name: /full repository/i }));
        expect(mockSetVerificationMode).toHaveBeenCalledWith("full_repo");
    });

    it("calls setGithubInput when the user types in the input field (AC 5)", () => {
        mockVerificationMode = "full_repo";
        renderSelector();
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "https://github.com/org/repo" } });
        expect(mockSetGithubInput).toHaveBeenCalledWith("https://github.com/org/repo");
    });

    it("clears githubInput (setGithubInput('')) when switching modes (AC 5)", () => {
        mockVerificationMode = "full_repo";
        mockGithubInput = "https://github.com/some/repo";
        renderSelector();
        fireEvent.click(screen.getByRole("tab", { name: /pull request/i }));
        // mode change fires setVerificationMode AND setGithubInput("")
        expect(mockSetVerificationMode).toHaveBeenCalledWith("pull_request");
        expect(mockSetGithubInput).toHaveBeenCalledWith("");
    });

    // ---- AC 6: Verify button disabled logic -----------------------------

    it("disables Verify button when no mode is selected (AC 6)", () => {
        // mockVerificationMode is null by default
        renderSelector();
        expect(screen.getByRole("button", { name: /verify scenarios/i })).toBeDisabled();
    });

    it("disables Verify button when mode is selected but input is empty (AC 6)", () => {
        mockVerificationMode = "full_repo";
        mockGithubInput = ""; // empty
        renderSelector();
        expect(screen.getByRole("button", { name: /verify scenarios/i })).toBeDisabled();
    });

    it("disables Verify button when input is only whitespace (AC 6)", () => {
        mockVerificationMode = "full_repo";
        mockGithubInput = "   ";
        renderSelector();
        expect(screen.getByRole("button", { name: /verify scenarios/i })).toBeDisabled();
    });

    it("enables Verify button when mode is selected AND input is non-empty (AC 6)", () => {
        mockVerificationMode = "full_repo";
        mockGithubInput = "https://github.com/org/repo";
        renderSelector();
        expect(screen.getByRole("button", { name: /verify scenarios/i })).not.toBeDisabled();
    });

    it("disables Verify button when isVerifying=true even if mode and input are set (AC 6)", () => {
        mockVerificationMode = "full_repo";
        mockGithubInput = "https://github.com/org/repo";
        renderSelector({ isVerifying: true });
        expect(screen.getByRole("button", { name: /verifying/i })).toBeDisabled();
    });

    it("shows 'Verifying…' label when isVerifying=true (AC 6)", () => {
        mockVerificationMode = "full_repo";
        mockGithubInput = "https://github.com/org/repo";
        renderSelector({ isVerifying: true });
        expect(screen.getByRole("button", { name: /verifying/i })).toBeInTheDocument();
    });

    it("calls onVerify when Verify button is clicked while enabled (AC 6)", () => {
        mockVerificationMode = "full_repo";
        mockGithubInput = "https://github.com/org/repo";
        const onVerify = vi.fn();
        renderSelector({ onVerify });
        fireEvent.click(screen.getByRole("button", { name: /verify scenarios/i }));
        expect(onVerify).toHaveBeenCalledOnce();
    });

    // ---- AC 7: Mobile read-only overlay is rendered ---------------------

    it("renders mobile read-only overlay element (AC 7)", () => {
        renderSelector();
        // The overlay is always in the DOM; CSS (md:hidden) controls visibility
        expect(screen.getByText(/verification selector is read-only/i)).toBeInTheDocument();
    });

    // ---- AC 8: No API calls (sanity: no fetch/axios in module) ----------

    it("does not trigger onVerify when button is disabled (AC 8 / AC 6)", () => {
        // No mode selected — button is disabled
        const onVerify = vi.fn();
        renderSelector({ onVerify });
        const btn = screen.getByRole("button", { name: /verify scenarios/i });
        fireEvent.click(btn); // clicking a disabled button should not fire
        expect(onVerify).not.toHaveBeenCalled();
    });

    // ---- aria-selected state --------------------------------------------

    it("marks active mode tab with aria-selected=true", () => {
        mockVerificationMode = "exact_files";
        renderSelector();
        expect(screen.getByRole("tab", { name: /exact file paths/i })).toHaveAttribute("aria-selected", "true");
        expect(screen.getByRole("tab", { name: /full repository/i })).toHaveAttribute("aria-selected", "false");
        expect(screen.getByRole("tab", { name: /pull request/i })).toHaveAttribute("aria-selected", "false");
    });

    // ---- Mode-specific input IDs (FIX M3) --------------------------------

    it("assigns a mode-specific id to the input element (fix M3)", () => {
        mockVerificationMode = "full_repo";
        renderSelector();
        expect(screen.getByRole("textbox")).toHaveAttribute("id", "github-source-input-full_repo");
    });

    it("assigns a mode-specific id to the textarea element (fix M3)", () => {
        mockVerificationMode = "exact_files";
        renderSelector();
        expect(screen.getByRole("textbox")).toHaveAttribute("id", "github-source-input-exact_files");
    });

    // ---- Code-path inputs have spellCheck=false (FIX L3) -----------------

    it("disables spellcheck on the textarea (fix L3)", () => {
        mockVerificationMode = "exact_files";
        renderSelector();
        expect(screen.getByRole("textbox")).toHaveAttribute("spellcheck", "false");
    });

    it("disables spellcheck on the text input (fix L3)", () => {
        mockVerificationMode = "full_repo";
        renderSelector();
        expect(screen.getByRole("textbox")).toHaveAttribute("spellcheck", "false");
    });
});

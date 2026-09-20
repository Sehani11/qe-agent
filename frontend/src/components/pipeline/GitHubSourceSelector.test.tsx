/**
 * GitHubSourceSelector.test.tsx
 *
 * Unit tests for Story 2.1 — GitHub Source Selection UI
 * Covers AC 1-7: mode switching, input rendering, Verify button disabled logic,
 * SessionContext state updates, and mobile read-only guard.
 */
import React from "react";
import { render, screen, fireEvent, within } from '@/test/test-utils';
import { describe, it, expect, vi, beforeEach } from "vitest";
import GitHubSourceSelector, {
    repoUrlFrom,
} from "@/components/pipeline/GitHubSourceSelector";
import type { VerificationMode } from "@/lib/types/verification";

// ---------------------------------------------------------------------------
// Mock SessionContext so we can control and observe the context state
// ---------------------------------------------------------------------------

let mockVerificationMode: VerificationMode | null = null;
let mockGithubInput = "";
let mockUseKnowledgeBase = false;
const mockSetVerificationMode = vi.fn((mode: VerificationMode | null) => {
    mockVerificationMode = mode;
});
const mockSetGithubInput = vi.fn((value: string) => {
    mockGithubInput = value;
});
const mockSetUseKnowledgeBase = vi.fn((value: boolean) => {
    mockUseKnowledgeBase = value;
});
let mockCodeIndexEnabled = false;
const mockSetCodeIndexEnabled = vi.fn((value: boolean) => {
    mockCodeIndexEnabled = value;
});

vi.mock("@/context/SessionContext", () => ({
    useSessionContext: () => ({
        verificationMode: mockVerificationMode,
        setVerificationMode: mockSetVerificationMode,
        githubInput: mockGithubInput,
        setGithubInput: mockSetGithubInput,
        useKnowledgeBase: mockUseKnowledgeBase,
        setUseKnowledgeBase: mockSetUseKnowledgeBase,
        codeIndexEnabled: mockCodeIndexEnabled,
        setCodeIndexEnabled: mockSetCodeIndexEnabled,
    }),
}));

// TODO(code-index): the code-index switch is commented out in the component
// (see TODO.md), so this mock and the tests below it are unused for now.
// The code-index switch is disabled unless the project has an index, so its
// status decides whether the control can be clicked at all.
let mockCodeIndex: {
    indexed: boolean;
    repo: string | null;
    indexed_ref: string | null;
    file_count: number;
    indexed_at: string | null;
} | undefined;
vi.mock("@/lib/hooks/useKnowledge", () => ({
    useCodeIndexStatus: () => ({ data: mockCodeIndex }),
}));

// The project supplies the full-repo default. Both mocks also have to satisfy
// ModelProvider, which the shared test shell mounts and which reads the same
// two modules — a partial mock would leave it importing undefined.
let mockProjectRepo = "";
vi.mock("@/lib/hooks/useProjects", () => ({
    useProject: () => ({
        data: { id: "p1", github_repo: mockProjectRepo, llm_provider: "", llm_model: "" },
        isLoading: false,
    }),
}));
vi.mock("@/lib/stores/projectStore", () => ({
    useActiveProjectId: () => "p1",
    useProjectStore: (selector: (s: Record<string, unknown>) => unknown) =>
        selector({ activeProjectId: "p1", setActiveProject: vi.fn() }),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderSelector(
    overrides: Partial<{
        onVerify: () => void;
        onStop: () => void;
        isVerifying: boolean;
    }> = {}
) {
    const onVerify = overrides.onVerify ?? vi.fn();
    const onStop = overrides.onStop ?? vi.fn();
    const isVerifying = overrides.isVerifying ?? false;
    return render(
        <GitHubSourceSelector
            onVerify={onVerify}
            onStop={onStop}
            isVerifying={isVerifying}
        />
    );
}

/** The Stop in the button row, never the one inside the confirmation dialog. */
const stopButton = () =>
    within(screen.getByTestId("verify-row")).getByRole("button", {
        name: /^stop$/i,
    });

function resetMockState() {
    mockVerificationMode = null;
    mockGithubInput = "";
    mockUseKnowledgeBase = false;
    // No project repository by default, so the pre-existing cases still describe
    // a deployment that has not configured one.
    mockProjectRepo = "";
    mockCodeIndexEnabled = false;
    // No code index by default, matching a project nobody has indexed yet.
    mockCodeIndex = undefined;
    mockSetVerificationMode.mockClear();
    mockSetGithubInput.mockClear();
    mockSetUseKnowledgeBase.mockClear();
    mockSetCodeIndexEnabled.mockClear();
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("GitHubSourceSelector", () => {
    beforeEach(() => {
        resetMockState();
    });

    // ---- Story 4.9: knowledge-base opt-in switch -------------------------

    it("renders the knowledge-base switch off by default (Story 4.9)", () => {
        // role="switch", not "checkbox": assistive tech should announce this as
        // on/off, which is what the control actually means.
        renderSelector();
        const toggle = screen.getByRole("switch", {
            name: /use project knowledge base/i,
        });
        expect(toggle).toHaveAttribute("aria-checked", "false");
    });

    // Queried by name rather than by role alone: the form carries more than one
    // switch, so a bare getByRole("switch") is ambiguous and would throw.
    const knowledgeBaseSwitch = () =>
        screen.getByRole("switch", { name: /use project knowledge base/i });

    it("turns knowledge-base enrichment on when the switch is clicked (Story 4.9)", () => {
        renderSelector();
        fireEvent.click(knowledgeBaseSwitch());
        expect(mockSetUseKnowledgeBase).toHaveBeenCalledWith(true);
    });

    it("turns it back off", () => {
        // The setter receives the state being moved TO, so an off-switch has to
        // send false rather than re-sending true.
        mockUseKnowledgeBase = true;
        renderSelector();

        expect(knowledgeBaseSwitch()).toHaveAttribute("aria-checked", "true");
        fireEvent.click(knowledgeBaseSwitch());
        expect(mockSetUseKnowledgeBase).toHaveBeenCalledWith(false);
    });

    // ---- Code-index opt-in switch ---------------------------------------
    // TODO(code-index): re-enable these once the "Use code index" switch is
    // back in GitHubSourceSelector. See TODO.md.

    // const codeIndexSwitch = () =>
    //     screen.getByRole("switch", { name: /use code index/i });

    // it("disables the code-index switch when the project has no index", () => {
    //     // A switch that silently does nothing is worse than one that says why
    //     // it cannot: the backend ignores the flag without an index.
    //     renderSelector();
    //     expect(codeIndexSwitch()).toBeDisabled();
    //     expect(
    //         screen.getByText(/index this project's repository/i)
    //     ).toBeInTheDocument();
    // });

    // it("enables it and reports what was indexed once an index exists", () => {
    //     mockCodeIndex = {
    //         indexed: true,
    //         repo: "org/repo",
    //         indexed_ref: "abc1234def",
    //         file_count: 120,
    //         indexed_at: "2026-08-01T00:00:00Z",
    //     };
    //     renderSelector();
    //
    //     expect(codeIndexSwitch()).not.toBeDisabled();
    //     fireEvent.click(codeIndexSwitch());
    //     expect(mockSetCodeIndexEnabled).toHaveBeenCalledWith(true);
    //     expect(screen.getByText(/abc1234/)).toBeInTheDocument();
    // });

    // it("stays off when the index disappears even if the flag was left on", () => {
    //     // Session state outlives the index: re-indexing elsewhere, or switching
    //     // projects, can leave the flag set with nothing behind it.
    //     mockCodeIndexEnabled = true;
    //     renderSelector();
    //
    //     expect(codeIndexSwitch()).toHaveAttribute("aria-checked", "false");
    // });

    it("still explains what enabling it does", () => {
        renderSelector();
        expect(
            screen.getByText(/enrich each scenario with relevant confluence/i)
        ).toBeInTheDocument();
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
        expect(textarea).toHaveAttribute(
            "placeholder",
            expect.stringContaining("One GitHub file URL per line"),
        );
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
        expect(screen.getByRole("button", { name: /run verification/i })).toBeDisabled();
    });

    it("disables Verify button when mode is selected but input is empty (AC 6)", () => {
        mockVerificationMode = "full_repo";
        mockGithubInput = ""; // empty
        renderSelector();
        expect(screen.getByRole("button", { name: /run verification/i })).toBeDisabled();
    });

    it("disables Verify button when input is only whitespace (AC 6)", () => {
        mockVerificationMode = "full_repo";
        mockGithubInput = "   ";
        renderSelector();
        expect(screen.getByRole("button", { name: /run verification/i })).toBeDisabled();
    });

    it("enables Verify button when mode is selected AND input is non-empty (AC 6)", () => {
        mockVerificationMode = "full_repo";
        mockGithubInput = "https://github.com/org/repo";
        renderSelector();
        expect(screen.getByRole("button", { name: /run verification/i })).not.toBeDisabled();
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
        fireEvent.click(screen.getByRole("button", { name: /run verification/i }));
        expect(onVerify).toHaveBeenCalledOnce();
    });

    // ---- AC 7: Mobile read-only overlay ---------------------------------
    // Skipped: the mobile read-only overlay this asserted was removed from
    // GitHubSourceSelector in a later refactor (the component no longer renders
    // that text). Kept as a documented skip rather than a false-green delete;
    // restore/remove when the mobile read-only decision is revisited.
    it.skip("renders mobile read-only overlay element (AC 7)", () => {
        renderSelector();
        expect(screen.getByText(/verification selector is read-only/i)).toBeInTheDocument();
    });

    // ---- AC 8: No API calls (sanity: no fetch/axios in module) ----------

    it("does not trigger onVerify when button is disabled (AC 8 / AC 6)", () => {
        // No mode selected — button is disabled
        const onVerify = vi.fn();
        renderSelector({ onVerify });
        const btn = screen.getByRole("button", { name: /run verification/i });
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

// ---------------------------------------------------------------------------
// Prefilling the full-repo field from the project's configured repository
// ---------------------------------------------------------------------------

describe("full-repo default from project config", () => {
    beforeEach(resetMockState);

    const pickFullRepo = () =>
        fireEvent.click(screen.getByRole("tab", { name: /full repository/i }));

    it("prefills on load, with no mode switch at all", () => {
        // DEFAULT_VERIFICATION_MODE is "full_repo", so a session opens in this
        // mode without anyone clicking. Seeding only from the click handler left
        // the field empty until you switched away and back.
        mockProjectRepo = "acme/app";
        mockVerificationMode = "full_repo";
        renderSelector();

        expect(mockSetGithubInput).toHaveBeenCalledWith("https://github.com/acme/app");
    });

    it("seeds once the project finishes loading", () => {
        // The project is fetched, so the first render can legitimately have no
        // repository yet. Deciding "no default" then would never be revisited.
        mockProjectRepo = "";
        mockVerificationMode = "full_repo";
        const view = renderSelector();
        expect(mockSetGithubInput).not.toHaveBeenCalled();

        mockProjectRepo = "acme/app";
        view.rerender(<GitHubSourceSelector onVerify={vi.fn()} onStop={vi.fn()} isVerifying={false} />);

        expect(mockSetGithubInput).toHaveBeenCalledWith("https://github.com/acme/app");
    });

    it("does not refill the box after the user empties it", () => {
        // Clearing it is how you type a different repository. An effect keyed on
        // "the field is empty" would put the default straight back.
        mockProjectRepo = "acme/app";
        mockVerificationMode = "full_repo";
        const view = renderSelector();
        expect(mockSetGithubInput).toHaveBeenCalledWith("https://github.com/acme/app");
        mockSetGithubInput.mockClear();

        mockGithubInput = "";
        view.rerender(<GitHubSourceSelector onVerify={vi.fn()} onStop={vi.fn()} isVerifying={false} />);

        expect(mockSetGithubInput).not.toHaveBeenCalled();
    });

    it("does not overwrite a value restored from a past run", () => {
        mockProjectRepo = "acme/app";
        mockVerificationMode = "full_repo";
        mockGithubInput = "https://github.com/acme/from-last-run";
        renderSelector();

        expect(mockSetGithubInput).not.toHaveBeenCalled();
    });

    it("prefills the field with the project's repository", () => {
        // Otherwise every session starts by pasting a URL the project already
        // knows — the whole point of storing it in project settings.
        mockProjectRepo = "https://github.com/acme/app";
        renderSelector();

        pickFullRepo();

        expect(mockSetGithubInput).toHaveBeenCalledWith("https://github.com/acme/app");
    });

    it("expands the owner/repo shorthand into a URL", () => {
        // Project settings accepts both shapes, and the field's placeholder is a
        // URL — dropping "acme/app" into it would read as a mistake.
        mockProjectRepo = "acme/app";
        renderSelector();

        pickFullRepo();

        expect(mockSetGithubInput).toHaveBeenCalledWith("https://github.com/acme/app");
    });

    it("leaves the value editable rather than fixing it", () => {
        // "use it or change it" — a prefill that could not be overtyped would be
        // a downgrade from an empty box.
        mockProjectRepo = "acme/app";
        mockVerificationMode = "full_repo";
        mockGithubInput = "https://github.com/acme/app";
        renderSelector();

        const field = screen.getByRole("textbox");
        expect(field).not.toBeDisabled();
        expect(field).not.toHaveAttribute("readonly");

        fireEvent.change(field, { target: { value: "https://github.com/acme/other" } });
        expect(mockSetGithubInput).toHaveBeenCalledWith("https://github.com/acme/other");
    });

    it("does not prefill the modes a repository cannot answer", () => {
        // These need a file URL or a pull-request URL; a bare repo would be a
        // value the user has to clear before they can type the real one.
        mockProjectRepo = "acme/app";
        renderSelector();

        fireEvent.click(screen.getByRole("tab", { name: /pull request/i }));
        expect(mockSetGithubInput).toHaveBeenLastCalledWith("");

        fireEvent.click(screen.getByRole("tab", { name: /exact file paths/i }));
        expect(mockSetGithubInput).toHaveBeenLastCalledWith("");
    });

    it("still clears when the project names no repository", () => {
        // The pre-projects behaviour, unchanged for anyone who has not set one.
        mockProjectRepo = "";
        renderSelector();

        pickFullRepo();

        expect(mockSetGithubInput).toHaveBeenCalledWith("");
    });
});

describe("repoUrlFrom", () => {
    it("passes a full URL through, minus any trailing slash", () => {
        expect(repoUrlFrom("https://github.com/acme/app")).toBe(
            "https://github.com/acme/app"
        );
        expect(repoUrlFrom("https://github.com/acme/app/")).toBe(
            "https://github.com/acme/app"
        );
    });

    it("expands owner/repo", () => {
        expect(repoUrlFrom("acme/app")).toBe("https://github.com/acme/app");
    });

    it("adds only the scheme to a host-qualified path", () => {
        // Treating this as shorthand would yield github.com/github.com/acme/app.
        expect(repoUrlFrom("github.com/acme/app")).toBe("https://github.com/acme/app");
    });

    it("handles a self-hosted host", () => {
        expect(repoUrlFrom("git.acme.io/team/app")).toBe("https://git.acme.io/team/app");
    });

    it("is empty for nothing configured", () => {
        expect(repoUrlFrom("")).toBe("");
        expect(repoUrlFrom("   ")).toBe("");
        expect(repoUrlFrom(undefined)).toBe("");
        expect(repoUrlFrom(null)).toBe("");
    });
});

describe("stopping a run in progress", () => {
    // A run is one LLM call per scenario and can last minutes. Without this the
    // only way out of a wrong repo or a wrong mode was to reload the page,
    // which loses every verdict already returned.
    it("offers no stop before a run starts", () => {
        renderSelector({ isVerifying: false });

        expect(
            within(screen.getByTestId("verify-row")).queryByRole("button", {
                name: /^stop$/i,
            })
        ).not.toBeInTheDocument();
    });

    it("offers stop while verifying", () => {
        renderSelector({ isVerifying: true });

        expect(stopButton()).toBeEnabled();
    });

    it("asks before stopping — the run's verdicts are discarded", () => {
        const onStop = vi.fn();
        renderSelector({ isVerifying: true, onStop });

        fireEvent.click(stopButton());

        expect(screen.getByRole("dialog")).toBeInTheDocument();
        expect(onStop).not.toHaveBeenCalled();
    });

    it("stops once confirmed", () => {
        const onStop = vi.fn();
        renderSelector({ isVerifying: true, onStop });

        fireEvent.click(stopButton());
        const dialog = screen.getByRole("dialog");
        fireEvent.click(within(dialog).getByRole("button", { name: /^stop$/i }));

        expect(onStop).toHaveBeenCalledTimes(1);
    });

    it("carries on when the confirmation is dismissed", () => {
        const onStop = vi.fn();
        renderSelector({ isVerifying: true, onStop });

        fireEvent.click(stopButton());
        fireEvent.click(screen.getByRole("button", { name: /keep going/i }));

        expect(onStop).not.toHaveBeenCalled();
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("keeps the progress button visible beside it", () => {
        // Stop is an addition to the running state, not a replacement for it —
        // the row still has to say the run is alive.
        renderSelector({ isVerifying: true });

        const verify = screen.getByRole("button", { name: /verifying/i });
        expect(verify).toBeInTheDocument();
        expect(verify).toBeDisabled();
    });
});

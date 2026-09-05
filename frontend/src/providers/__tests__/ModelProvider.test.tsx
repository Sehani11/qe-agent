/**
 * ModelProvider — the model comes from the ACTIVE PROJECT, and from nowhere
 * else.
 *
 * It used to come from a navbar picker whose choice lived in localStorage and
 * outranked the project. With that picker removed there would be no way to
 * clear such a value, so every browser that had ever used it would keep
 * silently overriding project settings. These cover the replacement rule and,
 * specifically, that a leftover stored value is now inert.
 */
import React from "react";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, beforeEach, vi } from "vitest";

// ModelProvider reads the active project over the API. `projectResponse` is
// what that call resolves to, so each test can state the project it is about.
let projectResponse: unknown = null;

vi.mock("@/lib/api/client", () => ({
    default: { get: vi.fn().mockImplementation(async () => ({ data: projectResponse })) },
}));

// An active project id, so the provider actually issues that request.
vi.mock("@/lib/stores/projectStore", () => ({
    useProjectStore: (selector: (s: { activeProjectId: string }) => unknown) =>
        selector({ activeProjectId: "project-1" }),
}));

// The fine-tuned axis is resolved against whether a model is actually being
// served, so these state it explicitly. Available by default: most of the file
// is about the stored choice, not about the model going missing.
let fineTunedState: {
    data: { available: boolean; model: string | null; detail: string } | undefined;
    isLoading: boolean;
};

vi.mock("@/lib/hooks/useFineTunedStatus", () => ({
    FINE_TUNED_STATUS_KEY: ["bdd", "fine-tuned-status"],
    useFineTunedStatus: () => fineTunedState,
}));

import { ModelProvider, useModel, useModelSelection } from "@/providers/ModelProvider";

const STORAGE_KEY = "qe-agent-model";
/** Mirrors ModelProvider: the default a stored choice was made against. */
const DEFAULT_STAMP_KEY = "qe-agent-model-default";

function Probe() {
    const { model } = useModel();
    const selection = useModelSelection();

    return (
        <div>
            <span data-testid="id">{model.id}</span>
            <span data-testid="payload">{JSON.stringify(selection)}</span>
        </div>
    );
}

/** A project row carrying the given model preference. */
function projectWith(llm_provider: string, llm_model: string) {
    return { id: "project-1", name: "Project 1", llm_provider, llm_model };
}

/** Render, then let the project query resolve. */
async function renderWithProject(project: unknown) {
    projectResponse = project;
    const result = renderProbe();
    await screen.findByTestId("id");
    return result;
}

function withProviders(children: React.ReactNode) {
    const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    return (
        <QueryClientProvider client={queryClient}>
            <ModelProvider>{children}</ModelProvider>
        </QueryClientProvider>
    );
}

function renderProbe() {
    return render(withProviders(<Probe />));
}

beforeEach(() => {
    fineTunedState = {
        data: {
            available: true,
            model: "bdd-lora-1.5b",
            detail: "Serving bdd-lora-1.5b.",
        },
        isLoading: false,
    };
    localStorage.clear();
    projectResponse = null;
});

describe("ModelProvider", () => {
    it("falls back to the deployment default when the project states no preference", async () => {
        await renderWithProject(projectWith("", ""));
        expect(screen.getByTestId("id")).toHaveTextContent("openai:gpt-4o");
    });

    it("exposes the selection in the shape a request body wants", async () => {
        await renderWithProject(projectWith("", ""));
        expect(JSON.parse(screen.getByTestId("payload").textContent!)).toEqual({
            llm_provider: "openai",
            llm_model: "gpt-4o",
        });
    });

    it("uses the active project's model", async () => {
        await renderWithProject(projectWith("claude", "claude-sonnet-5"));

        await vi.waitFor(() => {
            expect(JSON.parse(screen.getByTestId("payload").textContent!)).toEqual({
                llm_provider: "claude",
                llm_model: "claude-sonnet-5",
            });
        });
    });

    it("falls back to the default when the project names a retired model", async () => {
        // A model dropped from the catalog would otherwise keep being sent on
        // behalf of every project that still names it.
        await renderWithProject(projectWith("openai", "gpt-3.5-turbo"));
        expect(screen.getByTestId("id")).toHaveTextContent("openai:gpt-4o");
    });

    it("falls back to the default when there is no project at all", async () => {
        await renderWithProject(null);
        expect(screen.getByTestId("id")).toHaveTextContent("openai:gpt-4o");
    });

    it("ignores a model left in localStorage by the removed navbar picker", async () => {
        // The reason the stored choice had to go. It used to OUTRANK the
        // project; with no picker left to change it, honouring it would pin
        // every long-standing browser to an old model forever and make the
        // project setting look broken to exactly those users.
        localStorage.setItem("qe-agent-model", "claude:claude-opus-5");
        localStorage.setItem("qe-agent-model-default", "openai:gpt-4o");

        await renderWithProject(projectWith("claude", "claude-sonnet-5"));

        await vi.waitFor(() => {
            expect(screen.getByTestId("id")).toHaveTextContent("claude:claude-sonnet-5");
        });
    });

    it("ignores a stored model even when the project states no preference", async () => {
        localStorage.setItem("qe-agent-model", "claude:claude-opus-5");
        localStorage.setItem("qe-agent-model-default", "openai:gpt-4o");

        await renderWithProject(projectWith("", ""));

        expect(screen.getByTestId("id")).toHaveTextContent("openai:gpt-4o");
    });
});

describe("ModelProvider — the fine-tuned toggle", () => {
    function BddProbe() {
        const { bddProvider, setBddProvider, bddSelection } = useModel();
        return (
            <div>
                <span data-testid="bdd">{bddProvider}</span>
                <span data-testid="bdd-payload">{JSON.stringify(bddSelection)}</span>
                <button onClick={() => setBddProvider("fine_tuned")}>ft</button>
                <button onClick={() => setBddProvider("general_llm")}>gen</button>
            </div>
        );
    }

    const renderBdd = () => render(withProviders(<BddProbe />));

    it("defaults to the general LLM, matching BDD_MODEL_PROVIDER", () => {
        renderBdd();
        expect(screen.getByTestId("bdd")).toHaveTextContent("general_llm");
    });

    it("sends both axes so the backend never has to guess", () => {
        renderBdd();
        fireEvent.click(screen.getByText("ft"));

        expect(JSON.parse(screen.getByTestId("bdd-payload").textContent!)).toEqual({
            llm_provider: "openai",
            llm_model: "gpt-4o",
            bdd_model_provider: "fine_tuned",
        });
    });

    it("keeps the project's model while the fine-tuned toggle is on", async () => {
        // The project's model still backs chat and verification, and is what
        // the backend falls back to if the fine-tuned endpoint is unavailable.
        projectResponse = projectWith("claude", "claude-sonnet-5");
        renderBdd();
        await screen.findByTestId("bdd");
        fireEvent.click(screen.getByText("ft"));

        await vi.waitFor(() => {
            const payload = JSON.parse(
                screen.getByTestId("bdd-payload").textContent!
            );
            expect(payload.bdd_model_provider).toBe("fine_tuned");
            expect(payload.llm_model).toBe("claude-sonnet-5");
        });
    });

    it("persists separately from the project's model", () => {
        const first = renderBdd();
        fireEvent.click(screen.getByText("ft"));
        first.unmount();

        renderBdd();
        expect(screen.getByTestId("bdd")).toHaveTextContent("fine_tuned");
        expect(localStorage.getItem("qe-agent-model")).toBeNull();
    });

    it("downgrades to the general LLM when no fine-tuned model is served", () => {
        // THE BUG THIS EXISTS FOR. Gating only the toggle left the request body
        // still asking for a model that was gone, which the server tried,
        // failed, and reported as `configured=fine_tuned effective=none` — a
        // 500 where the whole point was a silent, correct downgrade.
        fineTunedState = {
            data: { available: false, model: null, detail: "No model." },
            isLoading: false,
        };
        renderBdd();
        fireEvent.click(screen.getByText("ft"));

        expect(screen.getByTestId("bdd")).toHaveTextContent("general_llm");
        expect(
            JSON.parse(screen.getByTestId("bdd-payload").textContent!)
                .bdd_model_provider
        ).toBe("general_llm");
    });

    it("keeps the stored preference so a returning model restores it", () => {
        // Downgrading is about this request, not about forgetting what the
        // person asked for. Re-registering the model must not cost them a
        // re-tick of a switch they never deliberately turned off.
        fineTunedState = {
            data: { available: false, model: null, detail: "No model." },
            isLoading: false,
        };
        const off = renderBdd();
        fireEvent.click(screen.getByText("ft"));
        expect(screen.getByTestId("bdd")).toHaveTextContent("general_llm");
        off.unmount();

        fineTunedState = {
            data: { available: true, model: "bdd-lora", detail: "Serving." },
            isLoading: false,
        };
        renderBdd();

        expect(screen.getByTestId("bdd")).toHaveTextContent("fine_tuned");
    });

    it("ignores a stored value that is not a known provider", () => {
        localStorage.setItem("qe-agent-bdd-model", "some_old_provider");
        renderBdd();
        expect(screen.getByTestId("bdd")).toHaveTextContent("general_llm");
    });

    it("turns back off, returning generation to the project's model", () => {
        renderBdd();
        fireEvent.click(screen.getByText("ft"));
        fireEvent.click(screen.getByText("gen"));

        expect(screen.getByTestId("bdd")).toHaveTextContent("general_llm");
        expect(
            JSON.parse(screen.getByTestId("bdd-payload").textContent!)
                .bdd_model_provider
        ).toBe("general_llm");
    });
});

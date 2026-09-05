/**
 * FineTunedToggle — the switch has to describe what generation will actually do.
 *
 * The preference outlives the model: it sits in localStorage, while the model
 * is removed by a wipe and the shim is a process someone stops by hand. The
 * generate path then falls back to the general LLM without saying so, which is
 * how a switch reading "on" ends up claiming a provenance the scenarios do not
 * have.
 *
 * `useModel` resolves that disagreement before either consumer sees it, so
 * these drive the toggle entirely through the context — the same value the
 * request body carries. A toggle that read availability for itself is what
 * previously let the two diverge.
 */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

import FineTunedToggle from "@/components/model/FineTunedToggle";
import type { BddModelProviderId } from "@/lib/types/llm";

const setBddProvider = vi.fn();
let modelState: {
    bddProvider: BddModelProviderId;
    fineTuned: { available: boolean; detail: string; isLoading: boolean };
};

vi.mock("@/providers/ModelProvider", () => ({
    useModel: () => ({ ...modelState, setBddProvider }),
}));

const toggle = () => screen.getByRole("switch", { name: /use fine-tuned model/i });

beforeEach(() => {
    vi.clearAllMocks();
    modelState = {
        bddProvider: "general_llm",
        fineTuned: {
            available: true,
            detail: "Serving bdd-lora-1.5b.",
            isLoading: false,
        },
    };
});

describe("the fine-tuned toggle", () => {
    it("switches the provider when a model is being served", () => {
        render(<FineTunedToggle />);

        expect(toggle()).toBeEnabled();
        fireEvent.click(toggle());

        expect(setBddProvider).toHaveBeenCalledWith("fine_tuned");
    });

    it("is unavailable with a reason when no model is served", () => {
        modelState.fineTuned = {
            available: false,
            detail: "The fine-tuned model service is not responding.",
            isLoading: false,
        };
        render(<FineTunedToggle />);

        expect(toggle()).toBeDisabled();
        expect(screen.getByText(/not responding/i)).toBeInTheDocument();
    });

    it("reads off when the context has resolved away from fine-tuned", () => {
        // The state a wipe leaves behind. `useModel` has already downgraded the
        // stored preference, so both the switch and the request body say
        // general_llm — this asserts the switch follows that resolution.
        modelState.fineTuned = {
            available: false,
            detail: "No model.",
            isLoading: false,
        };
        render(<FineTunedToggle />);

        expect(toggle()).toHaveAttribute("aria-checked", "false");
    });

    it("stays on while a model is still served", () => {
        modelState.bddProvider = "fine_tuned";
        render(<FineTunedToggle />);

        expect(toggle()).toHaveAttribute("aria-checked", "true");
    });

    it("says nothing while the status is still loading", () => {
        // Flashing "no model" at someone who has one is as wrong as the state
        // this guards against, so loading commits to neither.
        modelState.fineTuned = { available: false, detail: "", isLoading: true };
        render(<FineTunedToggle />);

        expect(toggle()).toBeDisabled();
        expect(screen.queryByText(/not responding/i)).not.toBeInTheDocument();
    });
});

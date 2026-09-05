/**
 * useTrainingOptIn — a REQUEST for consent, not a decision.
 *
 * The backend ANDs it with the deployment policy, so these cover what the UI
 * is allowed to offer: withdrawing consent always works, granting it does not
 * when the operator has forbidden training outright.
 */
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi, beforeEach } from "vitest";

const get = vi.fn();
vi.mock("@/lib/api/client", () => ({ default: { get: (...a: unknown[]) => get(...a) } }));

import { useTrainingOptIn } from "@/lib/hooks/useTrainingOptIn";

function Probe() {
    const { requested, setRequested, allowed, effective } = useTrainingOptIn();
    return (
        <div>
            <span data-testid="requested">{String(requested)}</span>
            <span data-testid="allowed">{String(allowed)}</span>
            <span data-testid="effective">{String(effective)}</span>
            <button onClick={() => setRequested(false)}>withdraw</button>
            <button onClick={() => setRequested(true)}>grant</button>
        </div>
    );
}

function renderProbe() {
    const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false } },
    });
    return render(
        <QueryClientProvider client={queryClient}>
            <Probe />
        </QueryClientProvider>
    );
}

beforeEach(() => {
    localStorage.clear();
    get.mockReset();
    get.mockResolvedValue({ data: { training_data_opt_in_allowed: true } });
});

describe("useTrainingOptIn", () => {
    it("defaults to consenting, matching the backend policy default", () => {
        renderProbe();
        expect(screen.getByTestId("requested")).toHaveTextContent("true");
    });

    it("withdraws consent and remembers it across a remount", () => {
        const first = renderProbe();
        fireEvent.click(screen.getByText("withdraw"));
        expect(screen.getByTestId("requested")).toHaveTextContent("false");
        first.unmount();

        renderProbe();
        expect(screen.getByTestId("requested")).toHaveTextContent("false");
    });

    it("cannot grant consent the deployment forbids", async () => {
        // The backend ANDs the two, so the UI must not present this as usable.
        get.mockResolvedValue({ data: { training_data_opt_in_allowed: false } });
        renderProbe();

        await waitFor(() =>
            expect(screen.getByTestId("allowed")).toHaveTextContent("false")
        );

        fireEvent.click(screen.getByText("grant"));
        expect(screen.getByTestId("requested")).toHaveTextContent("true");
        expect(screen.getByTestId("effective")).toHaveTextContent("false");
    });

    it("assumes permitted while the policy is still loading", () => {
        // Flashing a governance message that may be false is worse than a
        // control that settles a moment later.
        renderProbe();
        expect(screen.getByTestId("allowed")).toHaveTextContent("true");
    });

    it("treats an unrecognised stored value as the default", () => {
        localStorage.setItem("qe-agent-training-opt-in", "maybe");
        renderProbe();
        expect(screen.getByTestId("requested")).toHaveTextContent("true");
    });
});

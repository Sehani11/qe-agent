/**
 * confirm-modal.test.tsx — reusable ConfirmModal.
 * Covers: hidden when closed, confirm/cancel/backdrop/Escape callbacks,
 * and the isConfirming disabled state.
 */
import React from "react";
import { render, screen, fireEvent } from '@/test/test-utils';
import { describe, it, expect, vi, beforeEach } from "vitest";
import { ConfirmModal } from "@/components/ui/confirm-modal";

const onConfirm = vi.fn();
const onCancel = vi.fn();

function renderModal(props: Partial<React.ComponentProps<typeof ConfirmModal>> = {}) {
    return render(
        <ConfirmModal
            open
            title="Delete item"
            description="Are you sure?"
            confirmLabel="Delete"
            onConfirm={onConfirm}
            onCancel={onCancel}
            {...props}
        />
    );
}

describe("ConfirmModal", () => {
    beforeEach(() => vi.clearAllMocks());

    it("renders nothing when open is false", () => {
        renderModal({ open: false });
        // Asserted against the dialog rather than container.firstChild: the
        // shared render wraps everything in ToastProvider, which always mounts
        // its notifications region, so the container is never empty.
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
        expect(screen.queryByText("Delete item")).not.toBeInTheDocument();
    });

    it("renders title and description when open", () => {
        renderModal();
        expect(screen.getByRole("dialog")).toBeInTheDocument();
        expect(screen.getByText("Delete item")).toBeInTheDocument();
        expect(screen.getByText("Are you sure?")).toBeInTheDocument();
    });

    it("calls onConfirm when the confirm button is clicked", () => {
        renderModal();
        fireEvent.click(screen.getByRole("button", { name: "Delete" }));
        expect(onConfirm).toHaveBeenCalledTimes(1);
        expect(onCancel).not.toHaveBeenCalled();
    });

    it("calls onCancel from the Cancel button, backdrop, and Escape", () => {
        renderModal();

        fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
        // Queried from the dialog rather than the render container: the modal
        // portals into <body>, so it is not inside the container at all.
        const backdrop = screen
            .getByRole("dialog")
            .querySelector('[aria-hidden="true"]')!;
        fireEvent.click(backdrop);
        fireEvent.keyDown(window, { key: "Escape" });

        expect(onCancel).toHaveBeenCalledTimes(3);
        expect(onConfirm).not.toHaveBeenCalled();
    });

    it("portals into document.body rather than mounting in place", () => {
        // Regression: the modal is position:fixed, and the app chrome it is
        // rendered from (nav, ingest bar) uses backdrop-blur — which makes
        // those elements a containing block for fixed descendants. Mounted in
        // place, the modal centred itself inside the nav strip and was clipped.
        const { container } = renderModal();

        const dialog = screen.getByRole("dialog");
        expect(dialog).toBeInTheDocument();
        expect(container).not.toContainElement(dialog);
        expect(document.body).toContainElement(dialog);
    });

    it("disables actions while confirming and ignores Escape", () => {
        renderModal({ isConfirming: true });
        expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
        expect(screen.getByRole("button", { name: /cancel/i })).toBeDisabled();

        fireEvent.keyDown(window, { key: "Escape" });
        expect(onCancel).not.toHaveBeenCalled();
    });
});

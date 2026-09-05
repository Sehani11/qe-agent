/**
 * usePopoverMenu — the dismissal and focus contract both nav dropdowns rely on.
 *
 * These live at the hook rather than in ModelMenu and ProjectSwitcher
 * separately: the behaviour is now shared, so a regression would otherwise be
 * caught in one component's tests and silently shipped in the other's.
 */
import React from "react";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

import { usePopoverMenu } from "@/lib/hooks/usePopoverMenu";

function Menu({ onClose }: { onClose?: () => void } = {}) {
    const { open, toggle, close, menuId, containerRef, triggerRef } =
        usePopoverMenu({ onClose });

    return (
        <div>
            <div ref={containerRef}>
                <button
                    ref={triggerRef}
                    onClick={toggle}
                    aria-expanded={open}
                    aria-controls={open ? menuId : undefined}
                >
                    trigger
                </button>
                {open && (
                    <div id={menuId} role="menu">
                        <button role="menuitem" onClick={() => close()}>
                            item
                        </button>
                    </div>
                )}
            </div>
            <button>outside</button>
        </div>
    );
}

const trigger = () => screen.getByRole("button", { name: "trigger" });

describe("usePopoverMenu", () => {
    it("opens and closes from the trigger", () => {
        render(<Menu />);
        expect(screen.queryByRole("menu")).not.toBeInTheDocument();

        fireEvent.click(trigger());
        expect(screen.getByRole("menu")).toBeInTheDocument();
        expect(trigger()).toHaveAttribute("aria-expanded", "true");

        fireEvent.click(trigger());
        expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    });

    it("closes on Escape and returns focus to the trigger", () => {
        // Focus would otherwise land on the document body, leaving a keyboard
        // user with no way back to the control they just used.
        render(<Menu />);
        fireEvent.click(trigger());

        act(() => {
            window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
        });

        expect(screen.queryByRole("menu")).not.toBeInTheDocument();
        expect(trigger()).toHaveFocus();
    });

    it("dismisses on an outside press WITHOUT stealing focus back", () => {
        // The user has already aimed at something else; pulling focus to the
        // trigger would fight the click they are in the middle of making.
        render(<Menu />);
        fireEvent.click(trigger());

        fireEvent.pointerDown(screen.getByRole("button", { name: "outside" }));

        expect(screen.queryByRole("menu")).not.toBeInTheDocument();
        expect(trigger()).not.toHaveFocus();
    });

    it("stays open on a press inside the menu", () => {
        render(<Menu />);
        fireEvent.click(trigger());

        fireEvent.pointerDown(screen.getByRole("menuitem"));
        expect(screen.getByRole("menu")).toBeInTheDocument();
    });

    it("runs the caller's onClose so per-menu state is reset", () => {
        // ProjectSwitcher discards its half-typed new-project name this way.
        const onClose = vi.fn();
        render(<Menu onClose={onClose} />);

        fireEvent.click(trigger());
        fireEvent.click(screen.getByRole("menuitem"));

        expect(onClose).toHaveBeenCalledTimes(1);
        expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    });

    it("unbinds its listeners once closed", () => {
        // Left attached, every menu on the page would answer every keystroke.
        const remove = vi.spyOn(window, "removeEventListener");
        render(<Menu />);

        fireEvent.click(trigger());
        fireEvent.click(trigger());

        const events = remove.mock.calls.map(([name]) => name);
        expect(events).toContain("keydown");
        expect(events).toContain("pointerdown");
        remove.mockRestore();
    });
});

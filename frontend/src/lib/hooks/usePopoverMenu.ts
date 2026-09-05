"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";

/**
 * The dismissal and focus mechanics shared by the nav's dropdown menus.
 *
 * `ModelMenu` and `ProjectSwitcher` are different controls — one picks from a
 * static catalog, the other lists server data and can create a row — but their
 * popover behaviour is identical: Escape closes and returns focus to the
 * trigger, a pointer press outside dismisses without stealing focus, and the
 * listeners exist only while open. Keeping one copy means a change to how these
 * dismiss (focus trapping, touch handling) lands on both instead of on whichever
 * one the next person remembers.
 *
 * Returns the state plus the refs each component wires to its own markup, so
 * this owns behaviour and nothing about appearance.
 */
export function usePopoverMenu({ onClose }: { onClose?: () => void } = {}) {
    const [open, setOpen] = useState(false);
    const menuId = useId();
    const containerRef = useRef<HTMLDivElement>(null);
    const triggerRef = useRef<HTMLButtonElement>(null);

    // Held in a ref so `close` stays referentially stable and the effect below
    // does not re-subscribe whenever the caller passes a fresh closure. Synced
    // in an effect rather than during render: a ref write while rendering is
    // not safe under concurrent rendering, which may discard the attempt.
    const onCloseRef = useRef(onClose);
    useEffect(() => {
        onCloseRef.current = onClose;
    }, [onClose]);

    /**
     * Close the menu.
     *
     * `refocus` is false for an outside press: the user has already aimed at
     * something else, and pulling focus back to the trigger would fight them.
     * Escape (and choosing an item) keeps it true, because focus would otherwise
     * fall to the document body with no way back to the control by keyboard.
     */
    const close = useCallback((refocus = true) => {
        setOpen(false);
        onCloseRef.current?.();
        if (refocus) triggerRef.current?.focus();
    }, []);

    const toggle = useCallback(() => setOpen((v) => !v), []);

    // Bound only while open — a closed menu has nothing to dismiss, and leaving
    // them attached would make every menu on the page answer every keystroke.
    useEffect(() => {
        if (!open) return;

        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") {
                e.preventDefault();
                close();
            }
        };
        const onPointerDown = (e: PointerEvent) => {
            if (!containerRef.current?.contains(e.target as Node)) close(false);
        };

        window.addEventListener("keydown", onKey);
        window.addEventListener("pointerdown", onPointerDown);
        return () => {
            window.removeEventListener("keydown", onKey);
            window.removeEventListener("pointerdown", onPointerDown);
        };
    }, [open, close]);

    return { open, setOpen, toggle, close, menuId, containerRef, triggerRef };
}

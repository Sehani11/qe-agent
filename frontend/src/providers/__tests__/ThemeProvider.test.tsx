/**
 * ThemeProvider — reads the stored choice and the OS preference through
 * useSyncExternalStore, so these cover the snapshot paths rather than any
 * mount-effect state copying.
 */
import React from "react";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

import { ThemeProvider, useTheme } from "@/providers/ThemeProvider";

const STORAGE_KEY = "qe-agent-theme";

/** Drives matchMedia so the "system" branch can be steered from a test. */
let systemDark = false;
const mediaListeners = new Set<() => void>();

function installMatchMedia() {
    vi.stubGlobal(
        "matchMedia",
        vi.fn(() => ({
            get matches() {
                return systemDark;
            },
            addEventListener: (_: string, listener: () => void) => {
                mediaListeners.add(listener);
            },
            removeEventListener: (_: string, listener: () => void) => {
                mediaListeners.delete(listener);
            },
        }))
    );
}

function setSystemDark(value: boolean) {
    systemDark = value;
    act(() => {
        for (const listener of mediaListeners) listener();
    });
}

function Probe() {
    const { theme, resolved, setTheme } = useTheme();
    return (
        <div>
            <span data-testid="theme">{theme}</span>
            <span data-testid="resolved">{resolved}</span>
            <button onClick={() => setTheme("dark")}>dark</button>
            <button onClick={() => setTheme("light")}>light</button>
            <button onClick={() => setTheme("system")}>system</button>
        </div>
    );
}

function renderProvider() {
    return render(
        <ThemeProvider>
            <Probe />
        </ThemeProvider>
    );
}

beforeEach(() => {
    systemDark = false;
    mediaListeners.clear();
    localStorage.clear();
    installMatchMedia();
    document.documentElement.className = "";
    document.documentElement.style.colorScheme = "";
    localStorage.setItem(STORAGE_KEY, "system");
});

afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

describe("ThemeProvider", () => {
    it("adopts the theme already in storage on first render", () => {
        localStorage.setItem(STORAGE_KEY, "dark");
        renderProvider();

        expect(screen.getByTestId("theme")).toHaveTextContent("dark");
        expect(screen.getByTestId("resolved")).toHaveTextContent("dark");
    });

    it("resolves 'system' against the OS preference", () => {
        localStorage.setItem(STORAGE_KEY, "system");
        systemDark = true;
        renderProvider();

        expect(screen.getByTestId("theme")).toHaveTextContent("system");
        expect(screen.getByTestId("resolved")).toHaveTextContent("dark");
    });

    it("follows the OS while the choice is 'system'", () => {
        localStorage.setItem(STORAGE_KEY, "system");
        renderProvider();
        expect(screen.getByTestId("resolved")).toHaveTextContent("light");

        setSystemDark(true);
        expect(screen.getByTestId("resolved")).toHaveTextContent("dark");
    });

    it("ignores the OS once a theme is pinned", () => {
        renderProvider();
        fireEvent.click(screen.getByRole("button", { name: "light" }));

        setSystemDark(true);

        expect(screen.getByTestId("theme")).toHaveTextContent("light");
        expect(screen.getByTestId("resolved")).toHaveTextContent("light");
    });

    it("persists the choice and paints the document", () => {
        renderProvider();
        fireEvent.click(screen.getByRole("button", { name: "dark" }));

        expect(localStorage.getItem(STORAGE_KEY)).toBe("dark");
        expect(screen.getByTestId("resolved")).toHaveTextContent("dark");
        expect(document.documentElement.classList.contains("dark")).toBe(true);
        expect(document.documentElement.style.colorScheme).toBe("dark");
    });

    it("still switches theme when storage is unavailable", () => {
        // Private mode: both reads and writes throw. The choice has to survive
        // in memory, otherwise the toggle would snap straight back.
        vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
            throw new Error("blocked");
        });
        vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
            throw new Error("blocked");
        });

        renderProvider();
        fireEvent.click(screen.getByRole("button", { name: "dark" }));

        expect(screen.getByTestId("theme")).toHaveTextContent("dark");
        expect(screen.getByTestId("resolved")).toHaveTextContent("dark");
    });

    it("falls back to 'system' for a corrupt stored value", () => {
        localStorage.setItem(STORAGE_KEY, "chartreuse");
        renderProvider();

        expect(screen.getByTestId("theme")).toHaveTextContent("system");
    });

    it("throws when useTheme is used outside the provider", () => {
        const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
        expect(() => render(<Probe />)).toThrow(/must be used within a ThemeProvider/);
        consoleError.mockRestore();
    });
});

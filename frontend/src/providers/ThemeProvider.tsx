"use client";

import React, {
    createContext,
    useCallback,
    useContext,
    useEffect,
    useMemo,
    useSyncExternalStore,
} from "react";

export type Theme = "light" | "dark" | "system";

const STORAGE_KEY = "qe-agent-theme";

interface ThemeContextValue {
    theme: Theme;
    /** The theme actually painted right now, with `system` resolved. */
    resolved: "light" | "dark";
    setTheme: (theme: Theme) => void;
}

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);

/**
 * Inlined in <head> so the correct palette is on <html> before first paint.
 * Without this the app flashes light before hydration on a dark-mode machine.
 */
export const themeInitScript = `(function(){try{var s=localStorage.getItem("${STORAGE_KEY}")||"system";var d=s==="dark"||(s==="system"&&window.matchMedia("(prefers-color-scheme: dark)").matches);document.documentElement.classList.toggle("dark",d);document.documentElement.style.colorScheme=d?"dark":"light";}catch(e){}})();`;

const MEDIA_QUERY = "(prefers-color-scheme: dark)";

/* ---------------------------------------------------------------------------
   The stored choice and the OS preference are both state that lives outside
   React, so they are read through useSyncExternalStore rather than copied into
   useState by a mount effect. That keeps the server render and the hydration
   pass agreeing (both take the server snapshot), and it means a theme change
   in another tab updates this one for free.
--------------------------------------------------------------------------- */

/** Same-tab subscribers. The `storage` event only fires in *other* tabs, so a
 *  local emitter is what notifies this one after it writes. */
const listeners = new Set<() => void>();

function emitThemeChange() {
    for (const listener of listeners) listener();
}

function subscribeToStoredTheme(onStoreChange: () => void) {
    listeners.add(onStoreChange);
    window.addEventListener("storage", onStoreChange);
    return () => {
        listeners.delete(onStoreChange);
        window.removeEventListener("storage", onStoreChange);
    };
}

/** Last choice made this session. Storage is the source of truth when it is
 *  readable; this is what keeps the toggle working when it is not (private
 *  mode, blocked cookies) instead of snapping back to "system" on every click. */
let inMemoryTheme: Theme = "system";

function readStoredTheme(): Theme {
    try {
        const stored = localStorage.getItem(STORAGE_KEY);
        if (stored === "light" || stored === "dark" || stored === "system") {
            return stored;
        }
        // Storage is readable and simply holds nothing usable, so the neutral
        // default is the honest answer.
        return "system";
    } catch {
        // Storage itself is unusable (private mode, blocked cookies) — only
        // here does the session's own choice stand in for it.
        return inMemoryTheme;
    }
}

/** On the server there is no storage to read, and the init script has not run
 *  yet either, so both snapshots below start from the neutral default. */
function storedThemeServerSnapshot(): Theme {
    return "system";
}

function subscribeToSystemTheme(onStoreChange: () => void) {
    const query = window.matchMedia(MEDIA_QUERY);
    query.addEventListener("change", onStoreChange);
    return () => query.removeEventListener("change", onStoreChange);
}

function readSystemPrefersDark(): boolean {
    return window.matchMedia(MEDIA_QUERY).matches;
}

function systemPrefersDarkServerSnapshot(): boolean {
    return false;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
    const theme = useSyncExternalStore(
        subscribeToStoredTheme,
        readStoredTheme,
        storedThemeServerSnapshot
    );
    const systemPrefersDark = useSyncExternalStore(
        subscribeToSystemTheme,
        readSystemPrefersDark,
        systemPrefersDarkServerSnapshot
    );

    // Fully derived — no second piece of state to keep in step with the first.
    const resolved: "light" | "dark" =
        theme === "dark" || (theme === "system" && systemPrefersDark) ? "dark" : "light";

    // Painting the document is a real side effect, so it belongs in an effect.
    // It is idempotent with the inlined init script, which has already set the
    // same classes before first paint.
    useEffect(() => {
        document.documentElement.classList.toggle("dark", resolved === "dark");
        document.documentElement.style.colorScheme = resolved;
    }, [resolved]);

    const setTheme = useCallback((next: Theme) => {
        inMemoryTheme = next;
        try {
            localStorage.setItem(STORAGE_KEY, next);
        } catch {
            // Not persisting is survivable; the session still looks right.
        }
        // Re-read happens through the store, so this is the only notification
        // needed — the snapshot is the single source of truth either way.
        emitThemeChange();
    }, []);

    const value = useMemo(
        () => ({ theme, resolved, setTheme }),
        [theme, resolved, setTheme]
    );

    return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
    const context = useContext(ThemeContext);
    if (!context) {
        throw new Error("useTheme must be used within a ThemeProvider");
    }
    return context;
}

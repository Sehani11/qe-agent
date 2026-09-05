import React from "react";
import { render as rtlRender, type RenderOptions } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ToastProvider } from "@/components/ui/toast";
import { ModelProvider } from "@/providers/ModelProvider";
import { ThemeProvider } from "@/providers/ThemeProvider";

/**
 * Components under test call `useToast` / `useTheme` / `useModel`, each of
 * which requires a provider. Rather than wrap every call site, tests import
 * `render` from here and get the same provider shell the app mounts in its
 * root layout.
 *
 * The NESTING mirrors `app/layout.tsx` deliberately: ModelProvider reads the
 * active project through React Query, so it has to sit inside a QueryClient
 * exactly as it does in the app. A different order here would pass tests that
 * the real tree cannot mount.
 */
function Providers({ children }: { children: React.ReactNode }) {
    // A fresh client per render: shared cache across tests leaks one test's
    // fixtures into the next, and retries turn a failed fetch into a timeout.
    const [queryClient] = React.useState(
        () =>
            new QueryClient({
                defaultOptions: { queries: { retry: false, gcTime: 0 } },
            })
    );

    return (
        <ThemeProvider>
            <QueryClientProvider client={queryClient}>
                <ToastProvider>
                    <ModelProvider>{children}</ModelProvider>
                </ToastProvider>
            </QueryClientProvider>
        </ThemeProvider>
    );
}

function render(ui: React.ReactElement, options?: Omit<RenderOptions, "wrapper">) {
    return rtlRender(ui, { wrapper: Providers, ...options });
}

export * from "@testing-library/react";
export { render, Providers };

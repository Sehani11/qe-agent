import type { Metadata } from "next";
import { Instrument_Sans, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { QueryProvider } from "@/providers/QueryProvider";
import { SessionProvider } from "@/context/SessionContext";
import { ModelProvider } from "@/providers/ModelProvider";
import { ThemeProvider, themeInitScript } from "@/providers/ThemeProvider";
import { ToastProvider } from "@/components/ui/toast";

// JetBrains Mono carries the headings, keywords and any machine-generated
// text; Instrument Sans handles prose. The pairing is deliberate — the product
// is a .feature file editor, so the type that names things matches the type
// that shows them.
const jetbrainsMono = JetBrains_Mono({
    variable: "--font-jetbrains-mono",
    subsets: ["latin"],
    weight: ["400", "500", "600", "700"],
    display: "swap",
});

const instrumentSans = Instrument_Sans({
    variable: "--font-instrument-sans",
    subsets: ["latin"],
    weight: ["400", "500", "600"],
    display: "swap",
});

export const metadata: Metadata = {
    title: "qe-agent — acceptance criteria to verified coverage",
    description:
        "Generate Gherkin scenarios from a Jira ticket, then verify each one against your codebase.",
};

export default function RootLayout({
    children,
}: Readonly<{
    children: React.ReactNode;
}>) {
    return (
        <html
            lang="en"
            className={`${jetbrainsMono.variable} ${instrumentSans.variable}`}
            suppressHydrationWarning
        >
            <head>
                {/* Paints the stored theme before first paint, so a dark-mode
                    machine never flashes the light palette. */}
                <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
            </head>
            <body className="font-sans antialiased" suppressHydrationWarning>
                <a
                    href="#main"
                    className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[200] focus:rounded-md focus:border focus:border-rule focus:bg-popover focus:px-3 focus:py-2 focus:text-sm focus:font-medium"
                >
                    Skip to content
                </a>
                <ThemeProvider>
                    <QueryProvider>
                        <ToastProvider>
                            <ModelProvider>
                                <SessionProvider>{children}</SessionProvider>
                            </ModelProvider>
                        </ToastProvider>
                    </QueryProvider>
                </ThemeProvider>
            </body>
        </html>
    );
}

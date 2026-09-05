"use client";

import { Monitor, Moon, Sun } from "lucide-react";

import { useTheme, type Theme } from "@/providers/ThemeProvider";
import { cn } from "@/lib/utils";

const OPTIONS: { value: Theme; label: string; icon: React.ElementType }[] = [
    { value: "light", label: "Light", icon: Sun },
    { value: "dark", label: "Dark", icon: Moon },
    { value: "system", label: "Match system", icon: Monitor },
];

/** Three-state segmented control: an explicit choice, or follow the OS. */
export default function ThemeToggle() {
    const { theme, setTheme } = useTheme();

    return (
        <div
            className="inline-flex items-center gap-0.5 rounded-md border border-rule bg-surface p-0.5"
            role="radiogroup"
            aria-label="Colour theme"
        >
            {OPTIONS.map(({ value, label, icon: Icon }) => {
                const active = theme === value;
                return (
                    <button
                        key={value}
                        type="button"
                        role="radio"
                        aria-checked={active}
                        aria-label={label}
                        title={label}
                        onClick={() => setTheme(value)}
                        className={cn(
                            "rounded p-1.5 transition-colors",
                            active
                                ? "bg-muted text-foreground"
                                : "text-muted-foreground hover:text-foreground"
                        )}
                    >
                        <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                );
            })}
        </div>
    );
}

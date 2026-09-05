"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Check, ChevronDown, FolderGit2, Plus, Settings2 } from "lucide-react";

import {
    useCreateProject,
    useProjectList,
    useSyncActiveProject,
} from "@/lib/hooks/useProjects";
import { usePopoverMenu } from "@/lib/hooks/usePopoverMenu";
import { useProjectStore } from "@/lib/stores/projectStore";
import { cn } from "@/lib/utils";

/**
 * Picks the project everything else is scoped to.
 *
 * First in the bar, before the page links: it is the widest piece of context on
 * screen, and every link to its right means something different depending on
 * which project is selected.
 */
export default function ProjectSwitcher({ className }: { className?: string }) {
    const router = useRouter();
    // Resolved here because the switcher is in the nav, so it runs on every
    // page. Anything scoped to a project (the sessions list, knowledge) waits
    // on this rather than briefly querying with no project at all.
    useSyncActiveProject();

    const { data: projects, isLoading } = useProjectList();
    const activeProjectId = useProjectStore((s) => s.activeProjectId);
    const setActiveProject = useProjectStore((s) => s.setActiveProject);
    const createProject = useCreateProject();

    const [creating, setCreating] = useState(false);
    const [newName, setNewName] = useState("");
    const nameInputRef = useRef<HTMLInputElement>(null);

    const active = projects?.find((p) => p.id === activeProjectId);

    // Dismissal and focus return are shared with ModelMenu; the create row is
    // this menu's own state, so it is discarded through the hook's onClose —
    // reopening should offer a fresh row, not a half-typed name from last time.
    const resetCreateRow = useCallback(() => {
        setCreating(false);
        setNewName("");
    }, []);

    const { open, toggle, close, menuId, containerRef, triggerRef } =
        usePopoverMenu({ onClose: resetCreateRow });

    // Focus follows the intent: opening the create row means typing a name.
    useEffect(() => {
        if (creating) nameInputRef.current?.focus();
    }, [creating]);

    const submitNew = async () => {
        const name = newName.trim();
        if (!name || createProject.isPending) return;
        await createProject.mutateAsync(name);
        close();
        // A new project has none of the things every other page needs — no
        // Jira URL, no repository, no credentials — so the only useful next
        // screen is the one that supplies them. Creating it and dropping the
        // user back where they were leaves an empty project selected and
        // nothing on screen explaining why the page went blank.
        router.push("/settings");
    };

    return (
        // A FIXED width, not max-width. The trigger sits between the wordmark
        // and the nav links, so sizing it to the project name moves every link
        // to its right — once when the name arrives (it renders "Loading…"
        // first, which is narrower than most names) and again on every switch.
        // The label truncates inside a stable box instead.
        //
        // On the wrapper rather than the button so a caller can override it:
        // twMerge lets the mobile drawer pass `w-full` and win.
        <div ref={containerRef} className={cn("relative w-48", className)}>
            <button
                ref={triggerRef}
                type="button"
                onClick={toggle}
                aria-haspopup="menu"
                aria-expanded={open}
                aria-controls={open ? menuId : undefined}
                className={cn(
                    "flex h-8 w-full items-center gap-1.5 rounded-md border border-rule bg-surface pl-2 pr-1.5",
                    "text-[0.8125rem] font-medium text-foreground transition-colors hover:border-rule-strong",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/25"
                )}
            >
                <FolderGit2
                    className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                    aria-hidden="true"
                />
                <span className="sr-only">Project: </span>
                {/* min-w-0 is what actually lets `truncate` work: a flex item
                    defaults to min-width:auto and would otherwise refuse to
                    shrink below its text, overflowing the fixed box. */}
                <span className="min-w-0 flex-1 truncate text-left">
                    {active?.name ?? (isLoading ? "Loading…" : "No project")}
                </span>
                <ChevronDown
                    className={cn(
                        "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-150",
                        open && "rotate-180"
                    )}
                    aria-hidden="true"
                />
            </button>

            {open && (
                <div
                    id={menuId}
                    role="menu"
                    aria-label="Project"
                    className={cn(
                        // Same rule as ModelMenu: at least as wide as its
                        // trigger, 17rem as the floor for the narrow nav case,
                        // and capped so it cannot overflow a small screen.
                        "absolute left-0 z-50 mt-1.5 w-[17rem] min-w-full max-w-[calc(100vw-1.5rem)]",
                        "overflow-hidden rounded-lg border border-rule bg-popover shadow-xl shadow-black/10",
                        "animate-[overlay-in_120ms_ease-out] dark:shadow-black/40"
                    )}
                >
                    <p className="eyebrow border-b border-rule px-3 py-2 text-muted-foreground">
                        Projects
                    </p>

                    <div className="scrollbar-thin max-h-64 overflow-y-auto py-1">
                        {projects?.map((project) => {
                            const isActive = project.id === activeProjectId;
                            return (
                                <button
                                    key={project.id}
                                    type="button"
                                    role="menuitemradio"
                                    aria-checked={isActive}
                                    onClick={() => {
                                        setActiveProject(project.id);
                                        close();
                                    }}
                                    className={cn(
                                        "flex w-full items-center gap-2 px-3 py-2 text-left text-[0.8125rem] transition-colors",
                                        isActive ? "bg-pass-soft" : "hover:bg-muted"
                                    )}
                                >
                                    <Check
                                        className={cn(
                                            "h-3.5 w-3.5 shrink-0",
                                            isActive ? "text-pass" : "text-transparent"
                                        )}
                                        aria-hidden="true"
                                    />
                                    <span
                                        className={cn(
                                            "truncate",
                                            isActive
                                                ? "font-medium text-pass-ink"
                                                : "text-foreground"
                                        )}
                                    >
                                        {project.name}
                                    </span>
                                </button>
                            );
                        })}

                        {projects?.length === 0 && (
                            <p className="px-3 py-2 text-xs text-muted-foreground">
                                No projects yet — create one to get started.
                            </p>
                        )}
                    </div>

                    <div className="border-t border-rule p-1.5">
                        {creating ? (
                            <div className="flex items-center gap-1.5">
                                <input
                                    ref={nameInputRef}
                                    value={newName}
                                    onChange={(e) => setNewName(e.target.value)}
                                    onKeyDown={(e) => {
                                        if (e.key === "Enter") {
                                            e.preventDefault();
                                            void submitNew();
                                        }
                                    }}
                                    placeholder="Project name"
                                    aria-label="New project name"
                                    className="min-w-0 flex-1 rounded border border-input bg-surface px-2 py-1.5 text-[0.8125rem] focus-visible:border-ring focus-visible:outline-none"
                                />
                                <button
                                    type="button"
                                    onClick={() => void submitNew()}
                                    disabled={!newName.trim() || createProject.isPending}
                                    className="rounded bg-pass px-2 py-1.5 text-xs font-medium text-white disabled:opacity-50 dark:text-[color:var(--paper)]"
                                >
                                    {createProject.isPending ? "…" : "Create"}
                                </button>
                            </div>
                        ) : (
                            <div className="flex items-center gap-1">
                                <button
                                    type="button"
                                    onClick={() => setCreating(true)}
                                    className="flex flex-1 items-center gap-2 rounded px-2 py-1.5 text-[0.8125rem] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                                >
                                    <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                                    New project
                                </button>
                                {activeProjectId && (
                                    <Link
                                        href="/settings"
                                        onClick={() => close(false)}
                                        className="flex items-center gap-2 rounded px-2 py-1.5 text-[0.8125rem] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                                    >
                                        <Settings2
                                            className="h-3.5 w-3.5"
                                            aria-hidden="true"
                                        />
                                        Configure
                                    </Link>
                                )}
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}

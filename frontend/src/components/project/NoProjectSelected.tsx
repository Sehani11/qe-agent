"use client";

import React from "react";
import { useRouter } from "next/navigation";
import { FolderGit2, FolderPlus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { apiErrorMessage } from "@/lib/api/errors";
import { useCreateProject, useProjectList } from "@/lib/hooks/useProjects";
import { useProjectStore } from "@/lib/stores/projectStore";

/**
 * What a project-scoped page shows when there is no project to scope it to.
 *
 * Reachable by deleting the project you were working in. Everything on such a
 * page describes a project, so there is nothing to render and nothing to fix
 * from the page itself — the only way forward used to be knowing that the nav
 * switcher hides a create row inside a dropdown. A page that needs a project
 * should offer to get one where the person already is.
 *
 * BOTH ways of getting one, because deleting is not only the last project's
 * story: someone with three projects who deletes one still lands here, and
 * telling them to create a fourth would be answering a question they did not
 * ask. Survivors are listed when there are any; creating is always offered.
 *
 * Either route selects the project it ends on, so this resolves the state it
 * appears in rather than handing the user back to the switcher.
 */
export default function NoProjectSelected({
    /** What the page cannot show. Completes "…needs a project to …". */
    purpose = "configure",
}: {
    purpose?: string;
}) {
    const router = useRouter();
    const createProject = useCreateProject();
    const { data: projects } = useProjectList();
    const setActiveProject = useProjectStore((s) => s.setActiveProject);
    const existing = projects ?? [];
    const toast = useToast();
    const [name, setName] = React.useState("");

    const submit = () => {
        const trimmed = name.trim();
        if (!trimmed || createProject.isPending) return;
        createProject.mutate(trimmed, {
            onSuccess: (project) => {
                setName("");
                toast.success("Project created", {
                    description: `${project.name} is now the active project.`,
                });
                // Same destination as the switcher's create row: a new project
                // has nothing configured, and settings is where that is fixed.
                router.push("/settings");
            },
            onError: (error) => {
                toast.error("Could not create the project", {
                    description: apiErrorMessage(error, "Try again."),
                });
            },
        });
    };

    return (
        <section className="flex flex-col gap-4 rounded-lg border border-rule bg-card p-6">
            <div className="flex items-center gap-2">
                <FolderPlus
                    className="h-4 w-4 text-muted-foreground"
                    aria-hidden="true"
                />
                {/* "Create a project", not "No project selected": a caller
                    that shows this under a page header has already said the
                    latter, and a card repeating its own page title reads as a
                    duplicate rather than as the thing to do next. */}
                <h2 className="text-sm">Create a project</h2>
            </div>

            <p className="max-w-prose text-xs leading-relaxed text-muted-foreground">
                There is no project to {purpose}.{" "}
                {existing.length > 0
                    ? "Pick one of yours, or create another — either becomes the active project straight away."
                    : "A new one becomes the active project straight away, so you can carry on from here."}
            </p>

            {existing.length > 0 && (
                <div className="flex flex-col gap-2">
                    <p className="eyebrow text-muted-foreground">Your projects</p>
                    <div className="flex flex-wrap gap-2">
                        {existing.map((project) => (
                            <Button
                                key={project.id}
                                variant="outline"
                                size="sm"
                                onClick={() => setActiveProject(project.id)}
                            >
                                <FolderGit2
                                    className="h-3.5 w-3.5"
                                    aria-hidden="true"
                                />
                                {project.name}
                            </Button>
                        ))}
                    </div>
                </div>
            )}

            <Field label="Project name" htmlFor="new-project-name">
                <div className="flex flex-wrap items-center gap-2">
                    <Input
                        id="new-project-name"
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        onKeyDown={(e) => {
                            if (e.key === "Enter") {
                                e.preventDefault();
                                submit();
                            }
                        }}
                        placeholder="e.g. Checkout revamp"
                        className="w-full sm:w-72"
                    />
                    <Button
                        onClick={submit}
                        disabled={!name.trim()}
                        loading={createProject.isPending}
                    >
                        {createProject.isPending ? "Creating" : "Create project"}
                    </Button>
                </div>
            </Field>
        </section>
    );
}

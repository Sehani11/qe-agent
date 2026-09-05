"use client";

import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/lib/api/client";
import type {
    Project,
    ProjectConfigUpdate,
    ProjectSummary,
} from "@/lib/types/project";
import { useProjectStore } from "@/lib/stores/projectStore";

const PROJECTS_KEY = ["projects"] as const;

/** Every project the user owns, oldest first. */
export function useProjectList() {
    return useQuery({
        queryKey: PROJECTS_KEY,
        queryFn: async () => {
            const { data } = await apiClient.get<ProjectSummary[]>("/projects");
            return data;
        },
        retry: false,
    });
}

/** One project's full config. Credentials come back as `has_*` flags only. */
export function useProject(projectId: string | null) {
    return useQuery({
        queryKey: ["project", projectId],
        queryFn: async () => {
            const { data } = await apiClient.get<Project>(`/projects/${projectId}`);
            return data;
        },
        enabled: !!projectId,
        retry: false,
    });
}

export function useCreateProject() {
    const queryClient = useQueryClient();
    const setActiveProject = useProjectStore((s) => s.setActiveProject);

    return useMutation({
        mutationFn: async (name: string) => {
            const { data } = await apiClient.post<Project>("/projects", { name });
            return data;
        },
        onSuccess: (project) => {
            queryClient.invalidateQueries({ queryKey: PROJECTS_KEY });
            // Creating a project is an implicit request to work in it; leaving
            // the user on the old one makes them switch twice.
            setActiveProject(project.id);
        },
    });
}

export function useUpdateProject(projectId: string | null) {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async (update: ProjectConfigUpdate) => {
            const { data } = await apiClient.patch<Project>(
                `/projects/${projectId}`,
                update
            );
            return data;
        },
        onSuccess: (project) => {
            queryClient.setQueryData(["project", projectId], project);
            // The name shows in the switcher, so that list is stale now too.
            queryClient.invalidateQueries({ queryKey: PROJECTS_KEY });
        },
    });
}

/**
 * Delete a project via DELETE /projects/{id}. Any project can go, including
 * the caller's last one — the switcher's empty state offers a new one.
 *
 * Clears the active project if it was the one just deleted, so
 * useSyncActiveProject picks a survivor on the next render instead of the
 * switcher briefly pointing at a project that no longer exists.
 */
export function useDeleteProject() {
    const queryClient = useQueryClient();
    const activeProjectId = useProjectStore((s) => s.activeProjectId);
    const setActiveProject = useProjectStore((s) => s.setActiveProject);

    return useMutation({
        mutationFn: async (projectId: string) => {
            await apiClient.delete(`/projects/${projectId}`);
            return projectId;
        },
        onSuccess: (projectId) => {
            queryClient.invalidateQueries({ queryKey: PROJECTS_KEY });
            queryClient.removeQueries({ queryKey: ["project", projectId] });
            if (activeProjectId === projectId) setActiveProject(null);
        },
    });
}

/**
 * Keeps the stored project id pointing at one that still exists.
 *
 * Three cases this resolves, all of which otherwise leave the app wedged on a
 * project it cannot load: first visit with nothing chosen, a project deleted in
 * another tab, and a stored id from a different account.
 *
 * Runs once the list has loaded; before that `activeProjectId` is trusted as-is
 * so a reload does not flicker onto the first project and back.
 */
export function useSyncActiveProject() {
    const { data: projects, isSuccess, isFetching } = useProjectList();
    const activeProjectId = useProjectStore((s) => s.activeProjectId);
    const setActiveProject = useProjectStore((s) => s.setActiveProject);
    const explicitlyCleared = useProjectStore((s) => s.explicitlyCleared);
    const hydrated = useProjectStore((s) => s.hydrated);

    useEffect(() => {
        // `isFetching` is the guard that stops this fighting a change it has
        // not been told about yet. Creating a project sets it active AND
        // invalidates this list; until the refetch lands, `projects` is the old
        // list, which does not contain the new project — so without this the
        // reconciler reads a live choice as a stale one and yanks the user back
        // to projects[0], one render after they created the thing they wanted.
        //
        // `isSuccess` alone does not cover it: it stays true through a
        // background refetch, which is exactly when the data is stale.
        if (!isSuccess || isFetching || !hydrated || !projects) return;

        // Having no project can be a decision. Deleting the project you were in
        // clears the selection on purpose, and refilling it with a survivor
        // drops the user into a project they never chose — silently, since the
        // switcher just shows a different name. Every later action then lands
        // in the wrong place. A stale or foreign id is the opposite case: it
        // resolves to nothing, so it IS repaired below.
        if (explicitlyCleared) return;

        const stillExists = projects.some((p) => p.id === activeProjectId);
        if (stillExists) return;

        // Oldest first, so this lands on Project-1 for anyone migrated.
        setActiveProject(projects[0]?.id ?? null);
    }, [
        isSuccess,
        isFetching,
        hydrated,
        projects,
        activeProjectId,
        explicitlyCleared,
        setActiveProject,
    ]);

    return { projects, isSuccess };
}

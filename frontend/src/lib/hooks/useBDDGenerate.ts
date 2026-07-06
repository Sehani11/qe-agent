"use client";

import { useMutation } from "@tanstack/react-query";

import apiClient from "@/lib/api/client";

interface BDDScenario {
    source_ac_clause: string;
    feature: string;
    scenario: string;
    given: string;
    when: string;
    then: string;
}

interface BDDGenerateResponse {
    scenarios: BDDScenario[];
}

interface BDDGenerateRequest {
    session_id: string;
    acceptance_criteria: string;
}

export function scenariosToGherkin(scenarios: BDDScenario[]): string {
    const blocks: string[] = [];

    scenarios.forEach((scenario, index) => {
        if (index === 0 || scenarios[index - 1].feature !== scenario.feature) {
            if (blocks.length > 0) {
                blocks.push("");
            }
            blocks.push(`Feature: ${scenario.feature}`);
            blocks.push("");
        }

        blocks.push(`  # Source AC: ${scenario.source_ac_clause}`);
        blocks.push(`  Scenario: ${scenario.scenario}`);
        blocks.push(`    ${scenario.given}`);
        blocks.push(`    ${scenario.when}`);
        blocks.push(`    ${scenario.then}`);
        blocks.push("");
    });

    return blocks.join("\n").trim();
}

async function generateBDD(request: BDDGenerateRequest): Promise<BDDGenerateResponse> {
    const response = await apiClient.post<BDDGenerateResponse>("/bdd/generate", request);
    return response.data;
}

export function useBDDGenerate() {
    return useMutation({
        mutationFn: generateBDD,
    });
}
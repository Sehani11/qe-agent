"use client";

import { useMutation } from "@tanstack/react-query";

import apiClient from "@/lib/api/client";
import { useBddSelection } from "@/providers/ModelProvider";
import { useTrainingOptIn } from "@/lib/hooks/useTrainingOptIn";
import type { BddModelProviderId, LLMSelection } from "@/lib/types/llm";

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

/** Matches a line that already opens with a Gherkin step keyword. */
const STEP_KEYWORD = /^(given|when|then|and|but)\b/i;

/**
 * Render one step block: `Given ...` plus any And/But continuation lines.
 *
 * The model is asked for Given/When/Then syntax but routinely returns the step
 * text alone ("a user is on the registration page"). Writing that out verbatim
 * produced something that only looked like Gherkin: Cucumber cannot run it, and
 * the training pipeline's parser dropped every scenario as having incomplete
 * steps — which is why nothing captured in the app was usable for fine-tuning.
 *
 * The keyword is only added when the line does not already carry one, so a
 * model that does the right thing is left alone rather than given "Given Given".
 */
function renderStep(keyword: "Given" | "When" | "Then", text: string): string[] {
    const lines = (text ?? "")
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);

    return lines.map((line, index) => {
        if (STEP_KEYWORD.test(line)) return `    ${line}`;
        // The system prompt asks for And/But to be folded into the step text,
        // so a second line is a continuation of the same clause, not a new one.
        return index === 0 ? `    ${keyword} ${line}` : `    And ${line}`;
    });
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
        blocks.push(...renderStep("Given", scenario.given));
        blocks.push(...renderStep("When", scenario.when));
        blocks.push(...renderStep("Then", scenario.then));
        blocks.push("");
    });

    return blocks.join("\n").trim();
}

async function generateBDD(
    request: BDDGenerateRequest &
        LLMSelection & {
            bdd_model_provider: BddModelProviderId;
            training_opt_in: boolean;
        }
): Promise<BDDGenerateResponse> {
    const response = await apiClient.post<BDDGenerateResponse>("/bdd/generate", request);
    return response.data;
}

export function useBDDGenerate() {
    // Carries both axes — which serving path (fine-tuned vs general) and,
    // for the general path, which model. The backend ignores the model half
    // when the fine-tuned endpoint answers, so sending both is always right.
    const selection = useBddSelection();
    // Consent travels with the write that captures the content — the row is
    // stamped as it is created and never reclassified afterwards.
    const { requested: trainingOptIn } = useTrainingOptIn();

    return useMutation({
        mutationFn: (request: BDDGenerateRequest) =>
            generateBDD({
                ...request,
                ...selection,
                training_opt_in: trainingOptIn,
            }),
    });
}
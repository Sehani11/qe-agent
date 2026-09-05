/**
 * The model catalog, and how NEXT_PUBLIC_AVAILABLE_MODELS / _DEFAULT_MODEL
 * narrow it.
 *
 * Both are read once at module scope, so each case sets the environment and
 * re-imports the module through `vi.resetModules()` — assigning to
 * `process.env` after the fact would change nothing.
 *
 * The property that matters throughout: whatever the env says, the app ends up
 * with a non-empty list and a default that is IN it. A misconfigured deployment
 * should get too much choice, never a picker it cannot use.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

type LlmModule = typeof import("@/lib/types/llm");

const AVAILABLE = "NEXT_PUBLIC_AVAILABLE_MODELS";
const DEFAULT = "NEXT_PUBLIC_DEFAULT_MODEL";

/** Load the module fresh with the given env applied. */
async function load(env: Record<string, string | undefined>): Promise<LlmModule> {
    for (const [key, value] of Object.entries(env)) {
        if (value === undefined) delete process.env[key];
        else process.env[key] = value;
    }
    vi.resetModules();
    return import("@/lib/types/llm");
}

beforeEach(() => {
    delete process.env[AVAILABLE];
    delete process.env[DEFAULT];
});

afterEach(() => {
    delete process.env[AVAILABLE];
    delete process.env[DEFAULT];
    vi.resetModules();
});

describe("with neither variable set", () => {
    it("offers the whole built-in catalog", async () => {
        const llm = await load({});

        const ids = llm.LLM_MODEL_OPTIONS.map((m) => m.id);
        expect(ids).toContain("openai:gpt-4o");
        expect(ids).toContain("claude:claude-sonnet-5");
        expect(ids).toContain("local:llama3.2:3b");
    });

    it("keeps the previous default, so nothing moves for existing deployments", async () => {
        const llm = await load({});
        expect(llm.DEFAULT_MODEL_ID).toBe("openai:gpt-4o");
    });
});

describe("NEXT_PUBLIC_AVAILABLE_MODELS", () => {
    it("offers only what it names", async () => {
        // The reason this exists: a deployment with only OPENAI_API_KEY should
        // not list Claude and hand the user a 400 they could not have foreseen.
        const llm = await load({
            [AVAILABLE]: "openai:gpt-4o,openai:gpt-4o-mini",
        });

        expect(llm.LLM_MODEL_OPTIONS.map((m) => m.id)).toEqual([
            "openai:gpt-4o",
            "openai:gpt-4o-mini",
        ]);
        expect(llm.LLM_PROVIDER_GROUPS.map((g) => g.id)).toEqual(["openai"]);
    });

    it("keeps the catalog's label and note for a model it knows", async () => {
        const llm = await load({ [AVAILABLE]: "claude:claude-sonnet-5" });

        expect(llm.LLM_MODEL_OPTIONS[0]).toMatchObject({
            label: "Claude Sonnet 5",
            supportsTools: true,
        });
        // The note is the catalog's own copy, not the generic line an UNKNOWN
        // id gets. Asserted by contrast rather than by exact text: what this
        // test is about is which branch of parseModelEntry ran, and pinning the
        // wording makes every copy edit — a price, a rewording — a test change.
        expect(llm.LLM_MODEL_OPTIONS[0].note).not.toBe(
            "Configured for this deployment."
        );
        expect(llm.LLM_MODEL_OPTIONS[0].note).toMatch(/\S/);
    });

    it("accepts a model the catalog has never heard of", async () => {
        // A deployment should not need a frontend release to offer a model the
        // vendor shipped last week.
        const llm = await load({ [AVAILABLE]: "openai:gpt-5-turbo" });

        expect(llm.LLM_MODEL_OPTIONS).toHaveLength(1);
        expect(llm.LLM_MODEL_OPTIONS[0]).toMatchObject({
            id: "openai:gpt-5-turbo",
            llm_provider: "openai",
            llm_model: "gpt-5-turbo",
            label: "gpt-5-turbo",
            // Read from the PROVIDER, which is where the backend keeps it —
            // not guessed from the model name.
            supportsTools: true,
        });
    });

    it("derives tool support from the provider for an unknown local model", async () => {
        // Ollama cannot call tools, so the picker must still warn that this
        // model cannot run verification.
        const llm = await load({ [AVAILABLE]: "local:mistral:7b" });

        expect(llm.LLM_MODEL_OPTIONS[0]).toMatchObject({
            llm_provider: "local",
            // Split on the FIRST colon only — the tag carries its own.
            llm_model: "mistral:7b",
            supportsTools: false,
        });
    });

    it("tolerates whitespace and blank entries", async () => {
        const llm = await load({
            [AVAILABLE]: " openai:gpt-4o , , claude:claude-opus-5 ,",
        });

        expect(llm.LLM_MODEL_OPTIONS.map((m) => m.id)).toEqual([
            "openai:gpt-4o",
            "claude:claude-opus-5",
        ]);
    });

    it("drops an entry naming a provider the backend cannot build", async () => {
        const llm = await load({
            [AVAILABLE]: "gemini:gemini-2.0,openai:gpt-4o",
        });

        expect(llm.LLM_MODEL_OPTIONS.map((m) => m.id)).toEqual(["openai:gpt-4o"]);
    });

    it("drops a bare model with no provider", async () => {
        const llm = await load({ [AVAILABLE]: "gpt-4o,openai:gpt-4o-mini" });

        expect(llm.LLM_MODEL_OPTIONS.map((m) => m.id)).toEqual([
            "openai:gpt-4o-mini",
        ]);
    });

    it("de-duplicates repeats", async () => {
        // Two identical rows would also collide on their React key.
        const llm = await load({
            [AVAILABLE]: "openai:gpt-4o,openai:gpt-4o",
        });

        expect(llm.LLM_MODEL_OPTIONS).toHaveLength(1);
    });

    it("falls back to the full catalog when nothing survives parsing", async () => {
        // A typo must degrade to too much choice, never to an unusable picker.
        const llm = await load({ [AVAILABLE]: "nonsense,also-nonsense" });

        expect(llm.LLM_MODEL_OPTIONS.length).toBeGreaterThan(1);
        expect(llm.DEFAULT_MODEL_ID).toBe("openai:gpt-4o");
    });

    it("falls back when set to only whitespace", async () => {
        const llm = await load({ [AVAILABLE]: "   " });
        expect(llm.LLM_MODEL_OPTIONS.length).toBeGreaterThan(1);
    });

    it("groups across providers in menu order", async () => {
        const llm = await load({
            [AVAILABLE]: "local:llama3.2:3b,claude:claude-opus-5,openai:gpt-4o",
        });

        // Grouping order follows the catalog, not the order they were listed.
        expect(llm.LLM_PROVIDER_GROUPS.map((g) => g.id)).toEqual([
            "openai",
            "claude",
            "local",
        ]);
        expect(llm.LLM_PROVIDER_GROUPS.every((g) => g.models.length > 0)).toBe(true);
    });
});

describe("NEXT_PUBLIC_DEFAULT_MODEL", () => {
    it("starts a fresh browser on the configured model", async () => {
        const llm = await load({
            [AVAILABLE]: "openai:gpt-4o,claude:claude-sonnet-5",
            [DEFAULT]: "claude:claude-sonnet-5",
        });

        expect(llm.DEFAULT_MODEL_ID).toBe("claude:claude-sonnet-5");
    });

    it("ignores a default that is not offered", async () => {
        // Honouring it would leave the app defaulting to something the picker
        // rejects as unknown, which is no valid selection at all.
        const llm = await load({
            [AVAILABLE]: "openai:gpt-4o",
            [DEFAULT]: "claude:claude-opus-5",
        });

        expect(llm.DEFAULT_MODEL_ID).toBe("openai:gpt-4o");
    });

    it("falls back to the first offered model when unset", async () => {
        const llm = await load({ [AVAILABLE]: "claude:claude-opus-5,openai:gpt-4o" });
        expect(llm.DEFAULT_MODEL_ID).toBe("claude:claude-opus-5");
    });

    it("always names something findModelOption can resolve", async () => {
        // The invariant the picker depends on: it validates a stored choice
        // against the offered list and falls back to DEFAULT_MODEL_ID, so a
        // default outside that list would leave it with nothing to select.
        for (const env of [
            {},
            { [AVAILABLE]: "openai:gpt-4o-mini" },
            { [AVAILABLE]: "nonsense" },
            { [AVAILABLE]: "openai:gpt-4o", [DEFAULT]: "claude:claude-opus-5" },
            { [DEFAULT]: "local:llama3.2:3b" },
        ]) {
            const llm = await load({
                [AVAILABLE]: undefined,
                [DEFAULT]: undefined,
                ...env,
            });
            expect(
                llm.findModelOption(llm.DEFAULT_MODEL_ID),
                `default unresolvable for ${JSON.stringify(env)}`
            ).toBeDefined();
        }
    });
});

describe("modelIdOf", () => {
    it("pairs a provider and model", async () => {
        const llm = await load({});
        expect(llm.modelIdOf("openai", "gpt-4o")).toBe("openai:gpt-4o");
    });

    it("returns empty for a half-set pair, which means no preference", async () => {
        const llm = await load({});
        expect(llm.modelIdOf("openai", "")).toBe("");
        expect(llm.modelIdOf("", "gpt-4o")).toBe("");
        expect(llm.modelIdOf(null, null)).toBe("");
        expect(llm.modelIdOf(undefined, undefined)).toBe("");
    });
});

describe(".env.example ships values that actually work", () => {
    // A sample file is documentation people paste. If its example narrows the
    // catalog to models this build cannot describe, or names a default outside
    // its own list, whoever copies it gets a broken picker and no clue why.
    async function sampleEnv(): Promise<Record<string, string>> {
        const { readFileSync } = await import("node:fs");
        const text = readFileSync(".env.example", "utf8");
        const out: Record<string, string> = {};
        // Split on \r?\n, not \n: these files are CRLF here, and `.` in a JS
        // regex does not match \r — it counts as a line terminator. Leaving the
        // \r on the line makes every match fail, which is how this guard first
        // shipped green while checking nothing at all.
        for (const line of text.split(/\r?\n/)) {
            const m = /^\s*(NEXT_PUBLIC_[A-Z0-9_]+)=(.*)$/.exec(line);
            if (m) out[m[1]] = m[2].trim();
        }
        return out;
    }

    it("parses its AVAILABLE_MODELS into a real, non-empty list", async () => {
        const env = await sampleEnv();
        const available = env[AVAILABLE];
        if (!available) return; // commented out is a valid choice

        const llm = await load({ [AVAILABLE]: available, [DEFAULT]: undefined });

        // Every entry survived — none silently dropped as an unknown provider,
        // which is how a sample quietly offers less than it appears to.
        expect(llm.LLM_MODEL_OPTIONS).toHaveLength(available.split(",").length);
    });

    it("names a DEFAULT_MODEL that its own AVAILABLE_MODELS contains", async () => {
        const env = await sampleEnv();
        if (!env[DEFAULT]) return;

        const llm = await load({
            [AVAILABLE]: env[AVAILABLE],
            [DEFAULT]: env[DEFAULT],
        });

        // Not just "resolvable" — the configured value must be the one used.
        // Falling back silently would mean the sample's two lines disagree.
        expect(llm.DEFAULT_MODEL_ID).toBe(env[DEFAULT]);
    });
});

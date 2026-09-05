/**
 * The models this app offers, and the shape the backend expects them in.
 *
 * The built-in catalog below is the product decision about which models are
 * worth offering. A deployment narrows or extends it without a code change:
 *
 *   NEXT_PUBLIC_AVAILABLE_MODELS=openai:gpt-4o,claude:claude-sonnet-5
 *   NEXT_PUBLIC_DEFAULT_MODEL=openai:gpt-4o
 *
 * That exists because the picker is not purely cosmetic: a deployment holding
 * only OPENAI_API_KEY still listed Claude, and choosing it produced a 400 the
 * user could not have predicted. Offering only what the server can serve is the
 * difference between a menu and a guess.
 *
 * Both are read at BUILD time — Next inlines `NEXT_PUBLIC_*` into the client
 * bundle — so changing them takes a rebuild, not just a restart.
 *
 * A pair is only ever valid together — `gpt-4o` sent with `claude` reaches
 * Anthropic as an unknown model name — so every option carries its own provider
 * and the picker never composes the two independently.
 */

/** Provider ids the backend's factory accepts. */
export type LLMProviderId = "openai" | "claude" | "local";

/** Exactly what an LLM-backed request body carries. */
export interface LLMSelection {
    llm_provider: LLMProviderId;
    llm_model: string;
}

export interface LLMModelOption extends LLMSelection {
    /** Stable key and form value: provider and model are chosen as one unit. */
    id: string;
    label: string;
    /** One-line character sketch, shown under the option. */
    note: string;
    /**
     * Whether the backend provider implements tool calling.
     *
     * Verification is built entirely on it, so a model without it can generate
     * scenarios and answer chat but cannot verify. Mirrors `supports_tools` on
     * the Python provider, which is the authority — this copy only exists so
     * the picker can say so before the request is refused.
     */
    supportsTools: boolean;
}

export interface LLMProviderGroup {
    id: LLMProviderId;
    label: string;
    models: LLMModelOption[];
}

function option(
    llm_provider: LLMProviderId,
    llm_model: string,
    label: string,
    note: string,
    supportsTools = true
): LLMModelOption {
    return {
        id: `${llm_provider}:${llm_model}`,
        llm_provider,
        llm_model,
        label,
        note,
        supportsTools,
    };
}

/** Display name per provider, and the order groups appear in the menu. */
const PROVIDER_LABELS: Record<LLMProviderId, string> = {
    openai: "OpenAI",
    claude: "Anthropic",
    local: "Local (Ollama)",
};

const PROVIDER_ORDER = Object.keys(PROVIDER_LABELS) as LLMProviderId[];

function isProviderId(value: string): value is LLMProviderId {
    return value in PROVIDER_LABELS;
}

/**
 * Whether a provider's backend class implements tool calling.
 *
 * A PROVIDER-level fact, not a per-model one: `supports_tools` is a class
 * attribute on each Python provider (True on OpenAI and Claude, False on
 * Ollama), so every model of a provider shares its answer. That is what makes
 * it safe to derive for a model named in env that this catalog has never heard
 * of — it is read from the provider, not guessed from the model name.
 */
const PROVIDER_SUPPORTS_TOOLS: Record<LLMProviderId, boolean> = {
    openai: true,
    claude: true,
    local: false,
};

/** Everything this build knows how to describe. Env selects FROM this. */
const CATALOG: readonly LLMProviderGroup[] = [
    {
        id: "openai",
        label: "OpenAI",
        models: [
            // NOT reordered by price, unlike the Anthropic group below.
            // DEFAULT_MODEL_ID falls back to the first option in the catalog,
            // so moving a cheaper model to the top here silently changes the
            // default model for every deployment that has not set
            // NEXT_PUBLIC_DEFAULT_MODEL.
            option("openai", "gpt-4o", "GPT-4o", "Default. Balanced quality and speed."),
            option("openai", "gpt-4o-mini", "GPT-4o mini", "Cheapest; fine for chat."),
            option("openai", "gpt-4.1", "GPT-4.1", "Stronger on long code context."),
            option("openai", "gpt-4.1-mini", "GPT-4.1 mini", "Faster, cheaper 4.1."),
        ],
    },
    {
        id: "claude",
        label: "Anthropic",
        models: [
            // Cheapest first. Safe to order by price here and not in the
            // OpenAI group above: the catalog default only ever reads the very
            // first entry of the very first group. Notes carry $ per million tokens
            // (input / output) because "cheap" is otherwise a word the reader
            // has to take on trust — and output, the half that dominates
            // generation cost, is 5x input on every one of these.
            option(
                "claude",
                "claude-haiku-4-5-20251001",
                "Claude Haiku 4.5",
                "Cheapest Claude."
            ),
            option(
                "claude",
                "claude-sonnet-4-6",
                "Claude Sonnet 4.6",
                "Previous Sonnet."
            ),
            option(
                "claude",
                "claude-sonnet-5",
                "Claude Sonnet 5",
                "Strong all-round default."
            ),
            option(
                "claude",
                "claude-opus-5",
                "Claude Opus 5",
                "Most capable; slowest."
            ),
        ],
    },
    {
        id: "local",
        label: "Local (Ollama)",
        models: [
            option(
                "local",
                "llama3.2:3b",
                "Llama 3.2 3B",
                "Runs on your machine; needs Ollama.",
                false
            ),
        ],
    },
] as const;

const CATALOG_OPTIONS: readonly LLMModelOption[] = CATALOG.flatMap((g) => g.models);

/**
 * Turn one `provider:model` entry from env into an option.
 *
 * A known id keeps the catalog's label and note. An unknown one is still
 * accepted — a deployment should not need a frontend release to offer a model
 * the vendor shipped last week — and is described from what can be known for
 * certain: the model string as its own label, and tool support read from the
 * provider. Returns null only for an entry naming a provider the backend has
 * no factory for, which could never succeed.
 */
function parseModelEntry(entry: string): LLMModelOption | null {
    const trimmed = entry.trim();
    if (!trimmed) return null;

    const known = CATALOG_OPTIONS.find((m) => m.id === trimmed);
    if (known) return known;

    // Split once: an Ollama tag carries its own colon ("local:llama3.2:3b").
    const separator = trimmed.indexOf(":");
    if (separator <= 0) return null;

    const provider = trimmed.slice(0, separator);
    const model = trimmed.slice(separator + 1).trim();
    if (!model || !isProviderId(provider)) return null;

    return {
        id: `${provider}:${model}`,
        llm_provider: provider,
        llm_model: model,
        label: model,
        note: `Configured for this deployment.`,
        supportsTools: PROVIDER_SUPPORTS_TOOLS[provider],
    };
}

/** Group a flat option list, in provider order, dropping empty groups. */
function groupOptions(options: readonly LLMModelOption[]): LLMProviderGroup[] {
    return PROVIDER_ORDER.map((id) => ({
        id,
        label: PROVIDER_LABELS[id],
        models: options.filter((m) => m.llm_provider === id),
    })).filter((group) => group.models.length > 0);
}

/**
 * The models this deployment offers.
 *
 * Written as a literal `process.env.NEXT_PUBLIC_…` because that is the form
 * Next replaces at build time; reading it through a variable yields undefined
 * in the browser.
 *
 * An unset value means the whole catalog, so a deployment that has not thought
 * about this behaves exactly as before. A value that parses to NOTHING also
 * falls back rather than rendering an empty picker: a typo should degrade to
 * too much choice, never to an app with no usable model at all.
 */
function resolveOfferedOptions(): readonly LLMModelOption[] {
    const configured = process.env.NEXT_PUBLIC_AVAILABLE_MODELS?.trim();
    if (!configured) return CATALOG_OPTIONS;

    const parsed = configured
        .split(",")
        .map(parseModelEntry)
        .filter((m): m is LLMModelOption => m !== null);

    // De-duplicated so a repeated entry cannot produce two identical rows with
    // the same React key.
    const unique = [...new Map(parsed.map((m) => [m.id, m])).values()];
    return unique.length > 0 ? unique : CATALOG_OPTIONS;
}

/** Every option this deployment offers, flattened, in menu order. */
export const LLM_MODEL_OPTIONS: readonly LLMModelOption[] = resolveOfferedOptions();

/** The offered options, grouped for the picker. */
export const LLM_PROVIDER_GROUPS: readonly LLMProviderGroup[] =
    groupOptions(LLM_MODEL_OPTIONS);

/**
 * The model a browser starts on, before anyone has chosen.
 *
 * `NEXT_PUBLIC_DEFAULT_MODEL` when it names something this deployment actually
 * offers; otherwise the first offered model. It MUST be an offered id — the
 * picker validates a stored choice against the offered list and falls back to
 * this one, so a default outside that list would fail the same check and leave
 * the app with no valid selection at all.
 *
 * Point it at the same model as the backend's LLM_PROVIDER / LLM_MODEL: this is
 * what the client sends, and a mismatch means the picker names one model while
 * a request with no selection is served by another.
 */
function resolveDefaultModelId(): string {
    const configured = process.env.NEXT_PUBLIC_DEFAULT_MODEL?.trim();
    if (configured && LLM_MODEL_OPTIONS.some((m) => m.id === configured)) {
        return configured;
    }
    return LLM_MODEL_OPTIONS[0].id;
}

export const DEFAULT_MODEL_ID = resolveDefaultModelId();

/**
 * The catalog id for a provider/model pair, or "" when either half is missing.
 *
 * The two are only meaningful together, and a project stores them as separate
 * columns that are BOTH empty for "no preference" — so every place that turns
 * those columns back into an id has to make the same judgement about a half-set
 * pair. One function, so they cannot disagree about what a half-set pair means.
 */
export function modelIdOf(
    provider?: string | null,
    model?: string | null
): string {
    return provider && model ? `${provider}:${model}` : "";
}

/* ---------------------------------------------------------------------------
   BDD generation runs on a second, independent axis: the fine-tuned model
   trained on this project's own scenarios, or whichever general model is
   selected above. It is separate because it applies to ONE flow — the
   fine-tuned endpoint serves a single fixed model and cannot chat or verify.
--------------------------------------------------------------------------- */

export type BddModelProviderId = "general_llm" | "fine_tuned";

/** Matches the backend's BDD_MODEL_PROVIDER default. */
export const DEFAULT_BDD_PROVIDER: BddModelProviderId = "general_llm";

export function findModelOption(id: string): LLMModelOption | undefined {
    return LLM_MODEL_OPTIONS.find((model) => model.id === id);
}

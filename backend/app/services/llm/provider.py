"""LLM Provider abstract base class.

All LLM interactions in the application MUST go through this interface.
Never call openai. or anthropic. SDK methods directly in business logic.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field


@dataclass
class ToolLoopStats:
    """What one ``generate_with_tools`` run actually cost.

    The caller creates it and passes it in; providers fill it as the loop runs.
    A provider that ignores it leaves it at zero rather than failing, so this is
    never load-bearing for a verdict — it exists so cost claims can be measured
    instead of assumed.

    Token counts are NORMALISED across vendors, because the two report the same
    run differently and the raw numbers are not comparable. ``input_tokens`` is
    always the whole prompt including anything served from cache, and
    ``cached_input_tokens`` is the part of that which was a cache read. OpenAI
    already reports the total that way; Anthropic reports the three parts
    separately and *excludes* cache reads from its own ``input_tokens``, so its
    provider adds them back before recording.

    Reading the cached figure matters as much as the total: on OpenAI, cached
    prompt tokens still count against the per-minute token limit, so a run whose
    input is mostly cache hits is cheap in dollars and no cheaper against the
    rate limit that is actually blocking.

    ``tool_calls`` holds the tool names requested in each round, in order. It
    lets the caller ask questions like "how many rounds went by before the agent
    read a file" without any provider needing to know what a given tool means.
    """

    #: API calls made, including the final forced-answer call.
    rounds: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    #: One entry per round that requested tools; the names requested in it.
    tool_calls: list[list[str]] = field(default_factory=list)

    def merge(self, other: "ToolLoopStats") -> None:
        """Fold a second loop's cost into this one.

        For a scenario that took more than one pass — an escalation after an
        inconclusive verdict — where the reported cost has to be everything
        spent, not just the pass that produced the answer.

        ``tool_calls`` is extended rather than interleaved, so ``rounds_before``
        keeps describing the FIRST pass. That is the pass worth measuring: it
        is the one a code index would have shortcut, and averaging it with a
        follow-up that starts from a filled cache would flatter the number.
        """
        self.rounds += other.rounds
        self.input_tokens += other.input_tokens
        self.cached_input_tokens += other.cached_input_tokens
        self.output_tokens += other.output_tokens
        self.tool_calls.extend(other.tool_calls)

    def rounds_before(self, tool_name: str) -> int | None:
        """How many rounds completed before ``tool_name`` was first requested.

        Returns None when it was never requested — which is a different fact
        from zero, and collapsing the two would read as "went straight to it"
        for a run that never went there at all.
        """
        for index, names in enumerate(self.tool_calls):
            if tool_name in names:
                return index
        return None


def record_usage(
    stats: "ToolLoopStats | None", prompt: int, cached: int, output: int
) -> None:
    """Add one response's normalised usage to ``stats``, if there is one."""
    if stats is None:
        return
    stats.rounds += 1
    stats.input_tokens += prompt
    stats.cached_input_tokens += cached
    stats.output_tokens += output


#: Sampling temperature for the agentic tool loop.
#:
#: Zero, where the rest of this module uses 0.7 for chat and 0.2 for structured
#: output. Verification is a JUDGEMENT, not a generation: the same scenario
#: against the same commit should reach the same verdict every time, and a
#: reviewer who re-runs a report and sees a different answer cannot trust
#: either one.
#:
#: This loop was previously running at the provider default of 1.0 — the most
#: random setting available — which showed up as identical runs reading wildly
#: different amounts of code (one scenario went 23 864 tokens then 8 200 on a
#: rerun) and as verdicts moving between "pass" and "inconclusive" with no
#: change to the code under test. Non-determinism there is not a stylistic
#: matter; it is the report contradicting itself.
#:
#: Zero can make a tool loop repeat a call rather than move on. That is bounded
#: here: `max_tool_rounds` caps the loop and its last round forces a text
#: answer, so a degenerate repeat costs rounds but cannot run away.
TOOL_LOOP_TEMPERATURE = 0.0

#: Fixed seed for providers that accept one, so repeated runs of the same
#: scenario sample identically where the backend can arrange it.
#:
#: Temperature alone does not buy reproducibility. Even at 0 the same request
#: can come back different — expert routing, batching and floating-point order
#: all vary between calls — and a tool loop amplifies it: one different file
#: opened in the first round cascades into a different verdict. Measured on a
#: 16-scenario suite, two consecutive runs at temperature 0 still disagreed on
#: three of them.
#:
#: A seed is best-effort, not a guarantee; OpenAI returns `system_fingerprint`
#: precisely so a caller can tell when the backend changed underneath it. It
#: narrows the gap at no cost, and the honest conclusion is the one below it:
#: verification is not reproducible enough to trust a single run's changed
#: verdict as a signal about a change you made.
TOOL_LOOP_SEED = 20260829


def call_label(tool_name: str, tool_args: dict) -> str:
    """Name a tool call the way the model would recognise it.

    Used in the elision placeholder, so the model can tell which of several
    reads was removed and re-issue exactly that one. ``path`` and ``query`` are
    the arguments that identify a call; anything else is noise in a placeholder.
    """
    for key in ("path", "query"):
        value = tool_args.get(key)
        if value:
            return f'{tool_name}({key}="{value}")'
    return f"{tool_name}(...)"


def elision_placeholder(label: str) -> str:
    """Stand in for a tool result dropped from the replayed conversation.

    The wording is deliberate and should not be shortened. The model has to
    understand three things: the call succeeded, the text is retrievable, and
    its absence here is not a fact about the code. Without the last point a
    model that can no longer see a file it read is one step from reporting the
    behaviour it was looking for as missing — the exact false ``fail`` the
    verification prompt's evidence rules exist to prevent.
    """
    return (
        f"[The result of {label} was removed from this transcript to save "
        "space. The call SUCCEEDED and the content is still available: call "
        "the tool again with the same arguments if you need it. Its removal "
        "here is NOT evidence about what that file or search contains, and "
        "must never be cited as an absence.]"
    )


def usage_int(value: object) -> int:
    """Coerce a usage field to an int, treating anything else as zero.

    Usage fields go missing between SDK versions and are absent entirely on
    mocked responses. A measurement is not worth raising over, and a
    non-numeric value silently poisoning the arithmetic is worse than a zero.
    """
    return value if isinstance(value, int) else 0


class LLMProvider(ABC):
    """Abstract base class for LLM provider integrations.

    Concrete implementations (ClaudeProvider, OpenAIProvider) must
    implement all abstract methods. Business logic services receive
    an LLMProvider instance from the factory — never a concrete class.
    """

    #: Whether `generate_with_tools` is really implemented. False means the
    #: method exists to satisfy the interface but raises — the agentic
    #: verification flow is built entirely on it, so a caller must check this
    #: and refuse UP FRONT. Without the check the run still "succeeds": the
    #: service catches the per-scenario error, emits an error event, continues,
    #: and then reports a completed run in which nothing was verified.
    supports_tools: bool = False

    @abstractmethod
    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        """Generate a text completion from the LLM.

        Args:
            prompt: The user prompt to send to the LLM.
            system_prompt: Optional system-level instructions.

        Returns:
            The LLM's text response.

        Raises:
            LLMProviderError: If the LLM call fails.
        """

    async def generate_stream(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """Stream a text completion token-by-token (Story 5.1).

        Default implementation yields the full ``generate()`` result as a single
        chunk, so providers without native streaming still produce a valid stream.
        Providers with streaming APIs (e.g. OpenAI) override this to yield deltas
        and honor ``temperature`` (the default wrapper ignores it).

        Yields:
            Text chunks (tokens) of the LLM response, in order.

        Raises:
            LLMProviderError: If the LLM call fails.
        """
        yield await self.generate(prompt, system_prompt)

    @abstractmethod
    async def generate_structured(
        self,
        prompt: str,
        system_prompt: str = "",
        response_format: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Generate a structured (JSON) response from the LLM.

        Args:
            prompt: The user prompt.
            system_prompt: Optional system-level instructions.
            response_format: Optional JSON schema for response structure.

        Returns:
            Parsed JSON response as a dictionary.

        Raises:
            LLMProviderError: If the LLM call fails or response is not valid JSON.
        """


    @abstractmethod
    async def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_executor: Callable[[str, dict], Awaitable[str]],
        max_tool_rounds: int = 10,
        stats: ToolLoopStats | None = None,
        tool_result_window: int = 0,
    ) -> str:
        """Run a tool-calling agent loop until the model returns a final text response.

        The provider handles the provider-native message format and tool call protocol.
        Tool execution is delegated to tool_executor(tool_name, tool_args) -> str.

        Args:
            messages:       Initial conversation (system + user messages in provider format).
                A message may carry ``"cache_control": True`` to mark it as the end
                of a reusable prompt prefix — see the note below. Providers MUST
                strip that key before sending, since it is ours and not any
                vendor's.
            tools:          Tool definitions in provider-native schema.
            tool_executor:  Async callable that executes a tool and returns its string result.
            max_tool_rounds: Maximum tool call iterations before forcing a final answer.
            stats:          Optional ``ToolLoopStats`` to fill with token and round
                counts as the loop runs. Purely observational.
            tool_result_window: How many of the most recent tool results to keep
                verbatim in the replayed conversation; 0 (the default) keeps all
                of them, which is the historical behaviour. See the note below.

        Returns:
            The model's final text response after all tool calls are resolved.

        Raises:
            LLMProviderError: If any LLM call in the loop fails unrecoverably.

        Prompt caching:
            Verifying many scenarios against one repository sends the same
            system prompt and the same code evidence every time, and paying full
            price for it each time is most of the bill. ``cache_control`` lets a
            caller say "everything up to and including this message repeats" so a
            provider that supports prefix caching can charge accordingly.

            It is a hint, never a requirement: a provider that has no such
            feature drops the flag and behaves exactly as before. What the caller
            must guarantee is that the marked prefix really is byte-identical
            across calls — a prefix that varies is simply never a cache hit.

        Tool-result elision (``tool_result_window``):
            Every round replays the whole conversation, and tool results are the
            bulk of it — one file read can be 10 000 tokens, re-sent on every
            later round. A window of N replaces all but the N newest results
            with a short placeholder, so a long loop stops paying for evidence
            the model has already used.

            This trades away prompt caching for the rounds after the first
            elision, since rewriting an earlier message changes the prefix. That
            is the right trade only when the binding constraint is a per-minute
            token limit rather than the bill, because cached tokens still count
            against those limits. It is off by default for that reason.

            Providers MUST make the placeholder say that the content was removed
            from the transcript and can be re-read, and that its removal says
            nothing about what the file contains. Silently dropping evidence
            invites the model to conclude something is absent because it can no
            longer see it.
        """


class LLMProviderError(Exception):
    """Raised when an LLM provider call fails.

    ``retryable`` separates "this call was unlucky" from "this call cannot
    succeed". A rate limit is the first: the request was well-formed and the
    same request will work once load drops. A missing API key is the second, and
    no amount of waiting fixes it.

    Callers need the distinction because the two want opposite handling — back
    off and try again, versus stop immediately and tell the user what to change.
    Inferring it from the message text is what a caller does when the exception
    will not say, and it gets it wrong: a provider renders every rate limit as
    one fixed sentence, which is indistinguishable from a constant
    configuration error.
    """

    def __init__(self, *args: object, retryable: bool = False) -> None:
        super().__init__(*args)
        self.retryable = retryable

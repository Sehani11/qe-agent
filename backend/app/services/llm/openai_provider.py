"""OpenAI LLM provider implementation."""

import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass

from openai import AsyncOpenAI, OpenAIError, RateLimitError

from app.core.config import settings
from app.services.llm.provider import (
    TOOL_LOOP_SEED,
    TOOL_LOOP_TEMPERATURE,
    LLMProvider,
    LLMProviderError,
    ToolLoopStats,
    call_label,
    elision_placeholder,
    record_usage,
    usage_int,
)


class OpenAIProvider(LLMProvider):
    """OpenAI provider implementation."""

    supports_tools = True

    def __init__(self, api_key: str, model: str | None = None) -> None:
        # Stripped, so a key that is only whitespace is treated as absent.
        # Unstripped it is truthy, passes this guard, and reaches the SDK as an
        # unsendable header — `Illegal header value b'Bearer '` — instead of the
        # message below, which says what to set.
        self._api_key = (api_key or "").strip()
        if not self._api_key:
            raise LLMProviderError(
                "No OpenAI API key is configured. Set OPENAI_API_KEY "
                "to use GPT models."
            )
        # Resolved once at construction rather than read from settings at each
        # call site: a per-request override has to hold for every call this
        # instance makes, including the multi-call tool loop below.
        self._model = model or settings.llm_model
        # Apply 30 second timeout per NFR-P3.
        # max_retries lets the SDK auto-retry 429s with exponential backoff,
        # honoring the Retry-After header — absorbs transient TPM bursts.
        self._client = AsyncOpenAI(api_key=self._api_key, timeout=30.0, max_retries=3)

    @property
    def model(self) -> str:
        return self._model

    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        """Generate text using OpenAI API."""
        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=0.7,
            )
            return response.choices[0].message.content or ""
        except RateLimitError as e:
            raise LLMProviderError(_rate_limit_message(e), retryable=True) from e
        except OpenAIError as e:
            raise LLMProviderError(f"OpenAI API error: {e!s}") from e
        except Exception as e:
            raise LLMProviderError(f"Unexpected error calling OpenAI: {e!s}") from e

    async def generate_stream(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """Stream text token-by-token using the OpenAI streaming API."""
        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            stream = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=temperature,
                stream=True,
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except RateLimitError as e:
            raise LLMProviderError(_rate_limit_message(e), retryable=True) from e
        except OpenAIError as e:
            raise LLMProviderError(f"OpenAI API error: {e!s}") from e
        except Exception as e:
            raise LLMProviderError(f"Unexpected error calling OpenAI: {e!s}") from e

    async def generate_structured(
        self,
        prompt: str,
        system_prompt: str = "",
        response_format: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Generate structured JSON using OpenAI API via JSON mode."""
        try:
            messages = []
            sys_msg = system_prompt or "You are a helpful assistant."
            if response_format:
                schema_str = json.dumps(response_format, indent=2)
                sys_msg += (
                    f"\n\nYou MUST reply with ONLY a valid JSON object matching this schema:\n"
                    f"{schema_str}"
                )

            messages.append({"role": "system", "content": sys_msg})
            messages.append({"role": "user", "content": prompt})

            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or "{}"
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMProviderError("LLM returned invalid JSON structure.") from e
        except RateLimitError as e:
            raise LLMProviderError(_rate_limit_message(e), retryable=True) from e
        except OpenAIError as e:
            raise LLMProviderError(f"OpenAI API error: {e!s}") from e
        except Exception as e:
            raise LLMProviderError(f"Unexpected error calling OpenAI: {e!s}") from e

    async def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_executor: Callable[[str, dict], Awaitable[str]],
        max_tool_rounds: int = 10,
        stats: ToolLoopStats | None = None,
        tool_result_window: int = 0,
    ) -> str:
        """Run a tool-calling agent loop using OpenAI function calling.

        OpenAI caches long prompt prefixes automatically and exposes no
        per-message control, so the caller's ``cache_control`` hint is dropped
        here rather than forwarded — an unknown key would be rejected. What
        earns the discount is the caller placing the shared, byte-identical part
        of the prompt first, which it does.
        """
        try:
            current_messages = [_strip_cache_flag(m) for m in messages]
            # Where each tool result landed, so older ones can be elided once
            # the window fills. Ordered oldest-first by construction.
            result_slots: list[_ToolResultSlot] = []
            # Rounds 0..(max_tool_rounds-2) allow tool calls.
            # The final round is reserved for the forced text answer below —
            # keeping total API calls ≤ max_tool_rounds as required by AC 11.
            for _round in range(max_tool_rounds - 1):
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=current_messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=TOOL_LOOP_TEMPERATURE,
                    seed=TOOL_LOOP_SEED,
                )
                _record(stats, response)
                choice = response.choices[0]

                if choice.finish_reason == "stop" or choice.message.tool_calls is None:
                    return choice.message.content or ""

                if stats is not None:
                    stats.tool_calls.append(
                        [call.function.name for call in choice.message.tool_calls]
                    )

                # Append assistant message with tool calls
                current_messages.append(choice.message.model_dump(exclude_none=True))

                # Execute each tool call and append results
                for tool_call in choice.message.tool_calls:
                    tool_args = json.loads(tool_call.function.arguments)
                    result = await tool_executor(tool_call.function.name, tool_args)
                    current_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    })
                    result_slots.append(
                        _ToolResultSlot(
                            index=len(current_messages) - 1,
                            label=call_label(tool_call.function.name, tool_args),
                        )
                    )

                _elide_old_results(current_messages, result_slots, tool_result_window)

            # Final round (max_tool_rounds-th call): force a text answer.
            # Passing tools= with tool_choice="none" is required by the OpenAI API.
            current_messages.append({
                "role": "user",
                "content": (
                    "You have used the maximum number of tool calls. "
                    "Based on the evidence gathered so far, provide your final verdict. "
                    "Reply with ONLY a valid JSON object — no explanation, no markdown."
                ),
            })
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=current_messages,
                tools=tools,
                tool_choice="none",
                response_format={"type": "json_object"},
                temperature=TOOL_LOOP_TEMPERATURE,
                seed=TOOL_LOOP_SEED,
            )
            _record(stats, response)
            return response.choices[0].message.content or ""
        except RateLimitError as e:
            raise LLMProviderError(_rate_limit_message(e), retryable=True) from e
        except OpenAIError as e:
            raise LLMProviderError(f"OpenAI API error: {e!s}") from e
        except LLMProviderError:
            raise
        except Exception as e:
            raise LLMProviderError(f"Unexpected error calling OpenAI: {e!s}") from e


@dataclass
class _ToolResultSlot:
    """Where one tool result sits in the conversation, and what produced it."""

    index: int
    label: str
    elided: bool = False


def _record(stats: ToolLoopStats | None, response: object) -> None:
    """Add one OpenAI response's token usage to ``stats``.

    ``prompt_tokens`` already counts cache hits, so it is the normalised total
    the caller wants; the cached portion is broken out separately because on
    OpenAI it still counts against the per-minute token limit.
    """
    if stats is None:
        return
    usage = getattr(response, "usage", None)
    details = getattr(usage, "prompt_tokens_details", None)
    record_usage(
        stats,
        prompt=usage_int(getattr(usage, "prompt_tokens", None)),
        cached=usage_int(getattr(details, "cached_tokens", None)),
        output=usage_int(getattr(usage, "completion_tokens", None)),
    )


def _elide_old_results(
    messages: list[dict], slots: list[_ToolResultSlot], window: int
) -> None:
    """Replace all but the ``window`` newest tool results with a placeholder.

    Mutates ``messages`` in place. A window of 0 disables this entirely, which
    is the default and the historical behaviour.

    Only the message body is rewritten — the ``tool_call_id`` stays, because the
    assistant turn that requested it is still in the conversation and the API
    rejects a tool call with no matching result.
    """
    if window <= 0 or len(slots) <= window:
        return
    for slot in slots[:-window]:
        if slot.elided:
            continue
        messages[slot.index]["content"] = elision_placeholder(slot.label)
        slot.elided = True


def _strip_cache_flag(message: dict) -> dict:
    """Drop the caller's ``cache_control`` prompt-caching hint from a message.

    The flag is this codebase's own convention for marking the end of a reusable
    prompt prefix. Anthropic has a matching API feature; OpenAI does not, so the
    key is removed before the message is sent.
    """
    return {key: value for key, value in message.items() if key != "cache_control"}


def _rate_limit_message(exc: RateLimitError) -> str:
    """Say which kind of 429 this was.

    OpenAI answers 429 for two unrelated conditions and the SDK raises
    RateLimitError for both. Collapsing them into one "try again in a few
    seconds" is wrong half the time: a quota-exhausted key never recovers by
    waiting, and the advice sends someone to watch a request that cannot
    succeed. The code is on the error body, so there is no need to guess.
    """
    code = getattr(exc, "code", None)
    if code is None:
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            code = body.get("code")

    if code == "insufficient_quota":
        return (
            "This OpenAI key has no remaining quota. Waiting will not help — "
            "add credit or billing to the account, or switch the project to a "
            "different model."
        )
    return (
        "The AI service is busy right now (rate limit reached). "
        "Please try again in a few seconds."
    )
